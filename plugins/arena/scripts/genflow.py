#!/usr/bin/env python3
"""
Genflow Workflow Orchestrator

A flexible, configurable workflow engine with per-constraint behaviors,
multi-phase support, and scoped adjudication. Independent of genloop but
shares infrastructure (prompt builders, run_process, models).
"""
from __future__ import annotations

import asyncio
import dataclasses
import logging
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from models import Agent, Constraint, Critique, CritiqueIssue, Adjudication
from parsers import parse_critique, parse_adjudication
from utils import (
    write_live, utc_now_iso, sha256,
    append_jsonl_durable, save_json_atomic, load_json,
    write_text_atomic, ensure_secure_dir,
)
from hitl import write_hitl_questions, write_agent_result, write_resolution
from config import load_constraints, compress_constraints, save_compressed_constraints
from genflow_config import (
    GenflowConfig, WorkflowStep, IssueBehavior,
    get_behavior_for_severity, resolve_constraints_for_step,
    get_step_index_by_name, validate_workflow,
)

logger = logging.getLogger("arena")

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_HITL = 10
EXIT_MAX_TURNS = 11

# Internal step control flow signals (not process exit codes)
STEP_CONTINUE = 100   # Proceed to next workflow step
STEP_LOOP_BACK = -1   # Loop back to earlier step (refine with loop_to)
STEP_ESCALATE = -2    # Escalate to HITL immediately


@dataclasses.dataclass
class WorkflowContext:
    """Shared context passed through workflow execution."""
    run_dir: Path
    state_dir: Path
    global_dir: Optional[Path]
    project_root: Path
    agents: Dict[str, Agent]
    genflow_cfg: GenflowConfig
    all_constraints: List[Constraint]
    goal: str
    source: str
    compressed_constraints: str
    artifact: Optional[str]
    artifact_path: Optional[Path]
    iteration: int
    max_iterations: int
    allow_scripts: bool
    no_stream: bool
    thread_path: Path
    state_path: Path
    # Accumulated critiques per step name
    critiques_by_step: Dict[str, List[Critique]]
    # All critiques not yet adjudicated
    unadjudicated_critiques: List[Critique]
    # Last adjudication result
    last_adjudication: Optional[Adjudication]
    # Step index tracking for loop_to
    current_step_index: int


@dataclasses.dataclass
class CritiqueStepResult:
    """Result from executing a critique step."""
    critiques: List[Critique]
    halted: bool
    halt_reason: Optional[str]
    escalated: bool
    escalate_issues: List[CritiqueIssue]
    filtered_critiques: List[Critique]  # IGNORE issues removed


async def run_process(
    cmd: List[str],
    stdin_text: str,
    timeout: Optional[int],
    stream_prefix: Optional[str] = None,
    suppress_stderr: bool = False,
) -> Tuple[int, str, str]:
    """Run subprocess with optional timeout and streaming output."""
    # Import here to avoid circular imports
    from arena import run_process as arena_run_process
    return await arena_run_process(cmd, stdin_text, timeout, stream_prefix, suppress_stderr)


def build_generator_prompt(
    goal: str,
    source: str,
    compressed_constraints: str,
    previous_artifact: Optional[str],
    previous_adjudication: Optional[Adjudication],
    iteration: int,
) -> str:
    """Build prompt for the generator phase."""
    from arena import build_generator_prompt as arena_build_generator_prompt
    return arena_build_generator_prompt(
        goal, source, compressed_constraints,
        previous_artifact, previous_adjudication, iteration
    )


def build_critic_prompt(
    constraint: Constraint,
    artifact: str,
    goal: str,
    iteration: int,
    run_dir: Optional[Path] = None,
    project_root: Optional[Path] = None,
    artifact_path: Optional[Path] = None,
    allow_scripts: bool = False,
    arena_home: Optional[Path] = None,
) -> str:
    """Build prompt for a critic phase."""
    from arena import build_critic_prompt as arena_build_critic_prompt
    return arena_build_critic_prompt(
        constraint, artifact, goal, iteration,
        run_dir, project_root, artifact_path, allow_scripts, arena_home
    )


def build_adjudicator_prompt(
    constraints: List[Constraint],
    artifact: str,
    critiques: List[Critique],
    goal: str,
    iteration: int,
    max_iterations: int,
) -> str:
    """Build prompt for the adjudicator phase."""
    from arena import build_adjudicator_prompt as arena_build_adjudicator_prompt
    return arena_build_adjudicator_prompt(
        constraints, artifact, critiques, goal, iteration, max_iterations
    )


def build_refinement_prompt(
    artifact_path: Path,
    adjudication: Adjudication,
    goal: str,
    iteration: int,
) -> str:
    """Build prompt for refinement phase."""
    from arena import build_refinement_prompt as arena_build_refinement_prompt
    return arena_build_refinement_prompt(artifact_path, adjudication, goal, iteration)


def get_agent_cmd_with_model(agent: Agent, model: Optional[str]) -> List[str]:
    """Get agent command with optional model override."""
    cmd = list(agent.cmd)
    if not model:
        return cmd

    # Add model flag based on agent kind
    if agent.kind == "claude":
        if "--model" not in cmd:
            cmd.extend(["--model", model])
    elif agent.kind == "codex":
        if "--model" not in cmd:
            cmd.extend(["--model", model])
    elif agent.kind == "gemini":
        # Gemini uses different model flag patterns
        if "--model" not in cmd and "-m" not in cmd:
            cmd.extend(["--model", model])

    return cmd


async def run_genflow_orchestrator(
    args: Any,
    cfg: Dict[str, Any],
    state_dir: Path,
    run_dir: Path,
    agents: Dict[str, Agent],
    genflow_cfg: GenflowConfig,
    global_dir: Optional[Path] = None,
) -> int:
    """Main entry point for genflow orchestration."""
    thread_path = run_dir / "thread.jsonl"
    state_path = run_dir / "state.json"

    # Load state for resuming
    state = load_json(
        state_path,
        {
            "awaiting_human": False,
            "iteration": 1,
            "step_index": 0,
            "artifact": None,
            "critiques_by_step": {},
            "unadjudicated_critiques": [],
            "last_adjudication": None,
        },
    )

    # HITL directory
    hitl_dir = run_dir / "hitl"
    ensure_secure_dir(hitl_dir)

    # Check for HITL resume
    if state.get("awaiting_human"):
        if getattr(args, "reset_hitl", False):
            # Manual override: clear stale HITL state
            state["awaiting_human"] = False
            save_json_atomic(state_path, state)
            write_live("HITL state cleared via --reset-hitl")
            logger.info("HITL state cleared via --reset-hitl flag")
        else:
            from hitl import ingest_hitl_answers
            hitl_answers = ingest_hitl_answers(run_dir)
            if hitl_answers:
                state["awaiting_human"] = False
                append_jsonl_durable(
                    thread_path,
                    {
                        "id": sha256(f"human:{utc_now_iso()}"),
                        "ts": utc_now_iso(),
                        "iteration": state.get("iteration", 1),
                        "phase": "hitl_response",
                        "agent": "human",
                        "role": "user",
                        "content": str(hitl_answers),
                    },
                )
                save_json_atomic(state_path, state)
                write_live("=" * 60)
                write_live("RESUMING: Human answers received")
                write_live("=" * 60)
            elif not (hitl_dir / "questions.json").exists():
                # Phantom HITL: awaiting_human is set but questions.json is gone
                state["awaiting_human"] = False
                save_json_atomic(state_path, state)
                write_live("Cleared stale HITL state (no questions.json found)")
                logger.warning("Cleared phantom HITL state: awaiting_human was true but questions.json missing")
            else:
                logger.info(f"Awaiting human answers at {hitl_dir / 'answers.json'}")
                logger.info(f"See questions at {hitl_dir / 'questions.json'}")
                return EXIT_HITL

    # Load inputs
    from arena import load_goal
    loaded_goal = load_goal(
        run_dir,
        project_root=Path.cwd(),
        arena_home=global_dir,
        allow_scripts=getattr(args, 'allow_scripts', False),
    )
    if not loaded_goal or not loaded_goal.goal_text.strip():
        logger.error(f"No goal defined in {run_dir / 'goal.yaml'}")
        return EXIT_ERROR

    goal = loaded_goal.goal_text
    source = loaded_goal.source_content

    # Load constraints
    constraints = load_constraints(run_dir / "constraints")
    if not constraints:
        logger.warning("No constraints found")
        write_live("WARNING: No constraints found")

    # Compress constraints for generator
    compressed = compress_constraints(constraints)
    if compressed:
        save_compressed_constraints(run_dir, compressed)

    # Validate workflow
    validation_errors = validate_workflow(genflow_cfg)
    if validation_errors:
        for err in validation_errors:
            logger.error(f"Workflow validation: {err}")
        return EXIT_ERROR

    # Configuration
    max_iterations = genflow_cfg.max_iterations
    if hasattr(args, 'max_iterations') and args.max_iterations:
        max_iterations = args.max_iterations

    project_root = state_dir.parent

    # Log configuration
    write_live("=" * 60)
    write_live(f"GENFLOW: {run_dir.name}")
    write_live(f"Config: {genflow_cfg.source_path}")
    write_live(f"Goal: {goal[:50]}...")
    write_live(f"Workflow: {len(genflow_cfg.workflow)} steps")
    write_live(f"Max iterations: {max_iterations}")
    write_live("=" * 60)

    # Dry run mode
    if getattr(args, 'dry_run', False):
        write_live("")
        write_live("DRY RUN - Workflow Preview")
        write_live("-" * 40)
        for i, step in enumerate(genflow_cfg.workflow):
            step_name = step.name or step.step
            agent_str = step.agent or "(default)"
            model_str = f" [{step.model}]" if step.model else ""
            write_live(f"  {i+1}. {step.step}: {step_name} → {agent_str}{model_str}")
            if step.step == "critique":
                matched = resolve_constraints_for_step(step, constraints)
                write_live(f"       constraints: {[c.id for c in matched]}")
                write_live(f"       execution: {step.execution}")
            elif step.step == "adjudicate":
                write_live(f"       scope: {step.scope}")
            elif step.step == "refine":
                write_live(f"       mode: {step.mode}")
                if step.loop_to:
                    write_live(f"       loop_to: {step.loop_to}")
        write_live("-" * 40)
        return EXIT_OK

    # Build context
    context = WorkflowContext(
        run_dir=run_dir,
        state_dir=state_dir,
        global_dir=global_dir,
        project_root=project_root,
        agents=agents,
        genflow_cfg=genflow_cfg,
        all_constraints=constraints,
        goal=goal,
        source=source,
        compressed_constraints=compressed,
        artifact=state.get("artifact"),
        artifact_path=None,
        iteration=state.get("iteration", 1),
        max_iterations=max_iterations,
        allow_scripts=getattr(args, 'allow_scripts', False),
        no_stream=getattr(args, 'no_stream', False),
        thread_path=thread_path,
        state_path=state_path,
        critiques_by_step={
            k: [Critique.from_dict(c) for c in v]
            for k, v in state.get("critiques_by_step", {}).items()
        },
        unadjudicated_critiques=[
            Critique.from_dict(c) for c in state.get("unadjudicated_critiques", [])
        ],
        last_adjudication=(
            Adjudication.from_dict(state["last_adjudication"])
            if state.get("last_adjudication") else None
        ),
        current_step_index=state.get("step_index", 0),
    )

    # Main iteration loop
    while context.iteration <= max_iterations:
        result = await execute_workflow(context)

        if result == EXIT_OK:
            # Workflow completed successfully
            return EXIT_OK
        elif result == EXIT_HITL:
            # HITL needed - save state and exit
            save_workflow_state(context)
            return EXIT_HITL
        elif result == EXIT_ERROR:
            return EXIT_ERROR
        elif result == EXIT_MAX_TURNS:
            # All steps completed - move to next iteration
            context.iteration += 1
            context.current_step_index = 0
            save_workflow_state(context)
        else:
            logger.error(f"Unexpected workflow result: {result}")
            return EXIT_ERROR

    # Max iterations reached
    write_live("")
    write_live(f"⚠ Max iterations ({max_iterations}) reached")

    # Save final artifact
    if context.artifact:
        final_dir = run_dir / "final"
        final_dir.mkdir(parents=True, exist_ok=True)
        write_text_atomic(final_dir / "artifact.md", context.artifact)

    write_resolution(run_dir, "max_iterations", max_iterations, "Reached max iterations")
    write_agent_result(run_dir, "done", EXIT_MAX_TURNS, summary="Max iterations reached")
    return EXIT_MAX_TURNS


def save_workflow_state(context: WorkflowContext) -> None:
    """Save workflow state for resumption."""
    state = {
        "awaiting_human": False,
        "iteration": context.iteration,
        "step_index": context.current_step_index,
        "artifact": context.artifact,
        "critiques_by_step": {
            k: [c.to_dict() for c in v]
            for k, v in context.critiques_by_step.items()
        },
        "unadjudicated_critiques": [c.to_dict() for c in context.unadjudicated_critiques],
        "last_adjudication": (
            context.last_adjudication.to_dict() if context.last_adjudication else None
        ),
    }
    save_json_atomic(context.state_path, state)


async def execute_workflow(context: WorkflowContext) -> int:
    """Execute workflow steps in sequence.

    Returns:
        EXIT_OK: Workflow approved
        EXIT_HITL: HITL needed
        EXIT_ERROR: Error occurred
        EXIT_MAX_TURNS: All steps completed, continue to next iteration
    """
    workflow = context.genflow_cfg.workflow

    while context.current_step_index < len(workflow):
        step = workflow[context.current_step_index]

        if step.step == "generate":
            result = await execute_generate_step(step, context)
        elif step.step == "critique":
            result = await execute_critique_step(step, context)
        elif step.step == "adjudicate":
            result = await execute_adjudicate_step(step, context)
        elif step.step == "refine":
            result = await execute_refine_step(step, context)
        else:
            logger.error(f"Unknown step type: {step.step}")
            return EXIT_ERROR

        if result == STEP_CONTINUE:
            # Proceed to next workflow step
            context.current_step_index += 1
            save_workflow_state(context)
        elif result == STEP_LOOP_BACK:
            # Loop back to earlier step (set by refine with loop_to)
            continue
        elif result == STEP_ESCALATE:
            # Escalate to HITL immediately
            return EXIT_HITL
        elif result == EXIT_OK:
            return EXIT_OK
        elif result == EXIT_HITL:
            return EXIT_HITL
        else:
            # Any other value (including EXIT_ERROR) is an error
            return EXIT_ERROR

    # All steps completed - continue to next iteration
    return EXIT_MAX_TURNS


async def execute_generate_step(step: WorkflowStep, context: WorkflowContext) -> int:
    """Execute a generate step."""
    iter_dir = context.run_dir / "iterations" / str(context.iteration)
    iter_dir.mkdir(parents=True, exist_ok=True)

    is_refinement = context.iteration > 1 and context.last_adjudication is not None
    phase_label = "Refinement" if is_refinement else "Generation"

    write_live("")
    write_live(f"▶ STEP: {step.name or 'generate'} ({phase_label}, iteration {context.iteration})")

    # Get agent
    agent_name = step.agent or "claude"
    if agent_name not in context.agents:
        logger.error(f"Agent '{agent_name}' not found")
        return EXIT_ERROR
    agent = context.agents[agent_name]
    agent_cmd = get_agent_cmd_with_model(agent, step.model)

    write_live(f"  {agent_name} → generating draft...")

    # Build prompt
    prompt = build_generator_prompt(
        goal=context.goal,
        source=context.source,
        compressed_constraints=context.compressed_constraints,
        previous_artifact=context.artifact,
        previous_adjudication=context.last_adjudication,
        iteration=context.iteration,
    )

    # Save prompt
    write_text_atomic(iter_dir / f"prompt_generate_{agent_name}.txt", prompt)

    # Run generator
    stream_prefix = agent_name if not context.no_stream else None
    rc, stdout, stderr = await run_process(
        agent_cmd, prompt, agent.timeout, stream_prefix, agent.suppress_stderr
    )

    if rc != 0 and not stdout.strip():
        logger.error(f"Generator failed: {stderr[:500]}")
        return EXIT_ERROR

    context.artifact = stdout.strip()
    context.artifact_path = iter_dir / "artifact.md"
    write_text_atomic(context.artifact_path, context.artifact)

    write_live(f"  ✓ Generated artifact (~{len(context.artifact.split())} words)")

    # Append to thread
    append_jsonl_durable(
        context.thread_path,
        {
            "id": sha256(f"generate:{utc_now_iso()}:{context.iteration}"),
            "ts": utc_now_iso(),
            "iteration": context.iteration,
            "phase": "generate",
            "step_name": step.name,
            "agent": agent_name,
            "role": "assistant",
            "artifact_path": str(context.artifact_path),
        },
    )

    # Reset critiques for new artifact
    context.critiques_by_step = {}
    context.unadjudicated_critiques = []

    return STEP_CONTINUE


async def execute_critique_step(step: WorkflowStep, context: WorkflowContext) -> int:
    """Execute a critique step - serial or parallel."""
    if step.execution == "serial":
        result = await execute_critique_serial(step, context)
    else:
        result = await execute_critique_parallel(step, context)

    # Check for escalation
    if result.escalated:
        write_live(f"  ⚠ ESCALATE: {len(result.escalate_issues)} issues require human intervention")
        hitl_questions = [{
            "agent": "orchestrator",
            "questions": [{
                "id": "escalate",
                "question": f"Issues escalated to HITL:\n" + "\n".join(
                    f"- [{i.severity}] {i.id}: {i.finding}"
                    for i in result.escalate_issues
                ),
                "priority": "critical",
                "required": True,
            }],
        }]
        write_hitl_questions(context.run_dir, hitl_questions, context.iteration)
        return EXIT_HITL

    # Check for halt (proceed to adjudicate with issues found)
    if result.halted:
        write_live(f"  ⚠ HALT: {result.halt_reason}")

    # Store critiques
    step_name = step.name or f"critique_{context.current_step_index}"
    context.critiques_by_step[step_name] = result.filtered_critiques
    context.unadjudicated_critiques.extend(result.filtered_critiques)

    return STEP_CONTINUE


async def execute_critique_serial(step: WorkflowStep, context: WorkflowContext) -> CritiqueStepResult:
    """Run constraints one-by-one with early exit on HALT/ESCALATE."""
    step_name = step.name or "critique"
    write_live("")
    write_live(f"▶ STEP: {step_name} (serial critique)")

    iter_dir = context.run_dir / "iterations" / str(context.iteration)
    critiques_dir = iter_dir / "critiques"
    critiques_dir.mkdir(parents=True, exist_ok=True)

    # Get constraints for this step
    constraints = resolve_constraints_for_step(step, context.all_constraints)

    critiques: List[Critique] = []
    filtered_critiques: List[Critique] = []
    escalate_issues: List[CritiqueIssue] = []
    halted = False
    halt_reason = None

    for constraint in constraints:
        if halted:
            break

        # Get agent for this constraint
        agent_name = step.agent or "claude"
        if agent_name not in context.agents:
            logger.warning(f"Agent '{agent_name}' not found, skipping {constraint.id}")
            continue
        agent = context.agents[agent_name]
        agent_cmd = get_agent_cmd_with_model(agent, step.model)

        write_live(f"  {agent_name} ({constraint.id}) → reviewing...")

        # Build prompt
        prompt = build_critic_prompt(
            constraint=constraint,
            artifact=context.artifact,
            goal=context.goal,
            iteration=context.iteration,
            run_dir=context.run_dir,
            project_root=context.project_root,
            artifact_path=context.artifact_path,
            allow_scripts=context.allow_scripts,
            arena_home=context.global_dir,
        )

        write_text_atomic(critiques_dir / f"prompt_{constraint.id}_{agent_name}.txt", prompt)

        # Run critic
        stream_prefix = f"{agent_name}[{constraint.id}]" if not context.no_stream else None
        rc, stdout, stderr = await run_process(
            agent_cmd, prompt, agent.timeout, stream_prefix, agent.suppress_stderr
        )

        critique = parse_critique(stdout, agent_name, constraint.id, context.iteration)
        critiques.append(critique)

        # Save critique
        save_json_atomic(critiques_dir / f"{constraint.id}-{agent_name}.json", critique.to_dict())

        # Process issues with behavior handling
        filtered_issues = []
        for issue in critique.issues:
            behavior = get_behavior_for_severity(constraint, issue.severity, context.genflow_cfg)

            if behavior == IssueBehavior.HALT:
                write_live(f"    [{issue.severity}] {issue.id}: HALT")
                halted = True
                halt_reason = f"{issue.severity} issue in {constraint.id}: {issue.finding[:50]}"
                filtered_issues.append(issue)
                break
            elif behavior == IssueBehavior.ESCALATE:
                write_live(f"    [{issue.severity}] {issue.id}: ESCALATE")
                escalate_issues.append(issue)
            elif behavior == IssueBehavior.CONTINUE:
                write_live(f"    [{issue.severity}] {issue.id}: continue")
                filtered_issues.append(issue)
            else:  # IGNORE
                write_live(f"    [{issue.severity}] {issue.id}: ignore")

        # Create filtered critique
        if filtered_issues or not critique.issues:
            filtered_critique = Critique(
                constraint_id=critique.constraint_id,
                reviewer=critique.reviewer,
                iteration=critique.iteration,
                overall=critique.overall if filtered_issues else "PASS",
                issues=filtered_issues,
                approved_sections=critique.approved_sections,
                summary=critique.summary,
            )
            filtered_critiques.append(filtered_critique)

        # Log to thread
        append_jsonl_durable(
            context.thread_path,
            {
                "id": sha256(f"critique:{agent_name}:{constraint.id}:{utc_now_iso()}"),
                "ts": utc_now_iso(),
                "iteration": context.iteration,
                "phase": "critique",
                "step_name": step_name,
                "agent": agent_name,
                "constraint": constraint.id,
                "issues_count": len(critique.issues),
                "overall": critique.overall,
            },
        )

    return CritiqueStepResult(
        critiques=critiques,
        halted=halted,
        halt_reason=halt_reason,
        escalated=len(escalate_issues) > 0,
        escalate_issues=escalate_issues,
        filtered_critiques=filtered_critiques,
    )


async def execute_critique_parallel(step: WorkflowStep, context: WorkflowContext) -> CritiqueStepResult:
    """Run all constraints simultaneously, apply behaviors post-hoc."""
    step_name = step.name or "critique"
    write_live("")
    write_live(f"▶ STEP: {step_name} (parallel critique)")

    iter_dir = context.run_dir / "iterations" / str(context.iteration)
    critiques_dir = iter_dir / "critiques"
    critiques_dir.mkdir(parents=True, exist_ok=True)

    # Get constraints for this step
    constraints = resolve_constraints_for_step(step, context.all_constraints)

    # Build all critique tasks
    critique_tasks = []
    task_info = []

    for constraint in constraints:
        agent_name = step.agent or "claude"
        if agent_name not in context.agents:
            logger.warning(f"Agent '{agent_name}' not found, skipping {constraint.id}")
            continue
        agent = context.agents[agent_name]
        agent_cmd = get_agent_cmd_with_model(agent, step.model)

        prompt = build_critic_prompt(
            constraint=constraint,
            artifact=context.artifact,
            goal=context.goal,
            iteration=context.iteration,
            run_dir=context.run_dir,
            project_root=context.project_root,
            artifact_path=context.artifact_path,
            allow_scripts=context.allow_scripts,
            arena_home=context.global_dir,
        )

        write_text_atomic(critiques_dir / f"prompt_{constraint.id}_{agent_name}.txt", prompt)
        write_live(f"  {agent_name} ({constraint.id}) → reviewing...")

        stream_prefix = f"{agent_name}[{constraint.id}]" if not context.no_stream else None
        critique_tasks.append(
            run_process(agent_cmd, prompt, agent.timeout, stream_prefix, agent.suppress_stderr)
        )
        task_info.append((agent_name, constraint))

    # Run all critiques in parallel
    results = await asyncio.gather(*critique_tasks)

    critiques: List[Critique] = []
    filtered_critiques: List[Critique] = []
    escalate_issues: List[CritiqueIssue] = []
    halted = False
    halt_reason = None

    for (rc, stdout, stderr), (agent_name, constraint) in zip(results, task_info):
        critique = parse_critique(stdout, agent_name, constraint.id, context.iteration)
        critiques.append(critique)

        # Save critique
        save_json_atomic(critiques_dir / f"{constraint.id}-{agent_name}.json", critique.to_dict())

        # Apply behaviors post-hoc
        filtered_issues = []
        for issue in critique.issues:
            behavior = get_behavior_for_severity(constraint, issue.severity, context.genflow_cfg)

            if behavior == IssueBehavior.HALT:
                if not halted:
                    halted = True
                    halt_reason = f"{issue.severity} issue in {constraint.id}"
                filtered_issues.append(issue)
            elif behavior == IssueBehavior.ESCALATE:
                escalate_issues.append(issue)
            elif behavior == IssueBehavior.CONTINUE:
                filtered_issues.append(issue)
            # IGNORE: don't add to filtered

        # Create filtered critique
        if filtered_issues or not critique.issues:
            filtered_critique = Critique(
                constraint_id=critique.constraint_id,
                reviewer=critique.reviewer,
                iteration=critique.iteration,
                overall=critique.overall if filtered_issues else "PASS",
                issues=filtered_issues,
                approved_sections=critique.approved_sections,
                summary=critique.summary,
            )
            filtered_critiques.append(filtered_critique)

        # Log summary
        issue_count = len(critique.issues)
        if issue_count > 0:
            critical = sum(1 for i in critique.issues if i.severity == "CRITICAL")
            high = sum(1 for i in critique.issues if i.severity == "HIGH")
            write_live(f"  {agent_name} ({constraint.id}): {critical} CRITICAL, {high} HIGH, {issue_count - critical - high} other")
        else:
            write_live(f"  {agent_name} ({constraint.id}): PASS")

        # Log to thread
        append_jsonl_durable(
            context.thread_path,
            {
                "id": sha256(f"critique:{agent_name}:{constraint.id}:{utc_now_iso()}"),
                "ts": utc_now_iso(),
                "iteration": context.iteration,
                "phase": "critique",
                "step_name": step_name,
                "agent": agent_name,
                "constraint": constraint.id,
                "issues_count": issue_count,
                "overall": critique.overall,
            },
        )

    return CritiqueStepResult(
        critiques=critiques,
        halted=halted,
        halt_reason=halt_reason,
        escalated=len(escalate_issues) > 0,
        escalate_issues=escalate_issues,
        filtered_critiques=filtered_critiques,
    )


async def execute_adjudicate_step(step: WorkflowStep, context: WorkflowContext) -> int:
    """Execute an adjudication step with scoped critiques."""
    step_name = step.name or "adjudicate"
    write_live("")
    write_live(f"▶ STEP: {step_name} (adjudication, scope={step.scope})")

    iter_dir = context.run_dir / "iterations" / str(context.iteration)

    # Get critiques based on scope
    critiques = get_critiques_for_scope(step, context)

    if not critiques:
        write_live("  No critiques to adjudicate - continuing")
        return STEP_CONTINUE

    # Get agent
    agent_name = step.agent or "claude"
    if agent_name not in context.agents:
        logger.error(f"Agent '{agent_name}' not found")
        return EXIT_ERROR
    agent = context.agents[agent_name]
    agent_cmd = get_agent_cmd_with_model(agent, step.model)

    write_live(f"  {agent_name} → analyzing {len(critiques)} critiques...")

    # Get constraints that were critiqued
    critiqued_constraint_ids = {c.constraint_id for c in critiques}
    critiqued_constraints = [c for c in context.all_constraints if c.id in critiqued_constraint_ids]

    # Build prompt
    prompt = build_adjudicator_prompt(
        constraints=critiqued_constraints,
        artifact=context.artifact,
        critiques=critiques,
        goal=context.goal,
        iteration=context.iteration,
        max_iterations=context.max_iterations,
    )

    write_text_atomic(iter_dir / f"prompt_adjudicate_{agent_name}.txt", prompt)

    # Run adjudicator
    stream_prefix = agent_name if not context.no_stream else None
    rc, stdout, stderr = await run_process(
        agent_cmd, prompt, agent.timeout, stream_prefix, agent.suppress_stderr
    )

    if rc != 0 and not stdout.strip():
        logger.error(f"Adjudicator failed: {stderr[:500]}")
        return EXIT_ERROR

    adjudication = parse_adjudication(stdout, context.iteration)
    save_json_atomic(iter_dir / f"adjudication_{step_name}.yaml", adjudication.to_dict())

    context.last_adjudication = adjudication

    # Clear adjudicated critiques based on scope
    if step.scope == "accumulated":
        context.unadjudicated_critiques = []
    elif step.scope == "previous":
        # Only clear the most recent step's critiques
        workflow = context.genflow_cfg.workflow
        if context.current_step_index > 0:
            prev_step = workflow[context.current_step_index - 1]
            if prev_step.step == "critique":
                prev_name = prev_step.name or f"critique_{context.current_step_index - 1}"
                if prev_name in context.critiques_by_step:
                    prev_critiques = context.critiques_by_step[prev_name]
                    context.unadjudicated_critiques = [
                        c for c in context.unadjudicated_critiques
                        if c not in prev_critiques
                    ]

    write_live(f"  Verdict: {adjudication.status}")
    write_live(f"    CRITICAL pursuing: {adjudication.critical_pursuing}")
    write_live(f"    HIGH pursuing: {adjudication.high_pursuing}")

    # Log to thread
    append_jsonl_durable(
        context.thread_path,
        {
            "id": sha256(f"adjudicate:{utc_now_iso()}:{context.iteration}"),
            "ts": utc_now_iso(),
            "iteration": context.iteration,
            "phase": "adjudicate",
            "step_name": step_name,
            "agent": agent_name,
            "status": adjudication.status,
            "critical_pursuing": adjudication.critical_pursuing,
            "high_pursuing": adjudication.high_pursuing,
        },
    )

    # Check for approval
    if adjudication.status == "APPROVED":
        write_live("")
        write_live("✓ APPROVED: All constraints satisfied")

        # Save final artifact
        final_dir = context.run_dir / "final"
        final_dir.mkdir(parents=True, exist_ok=True)
        write_text_atomic(final_dir / "artifact.md", context.artifact)
        write_live(f"  Output: {final_dir / 'artifact.md'}")

        write_resolution(context.run_dir, "approved", context.iteration, "All constraints satisfied")
        write_agent_result(context.run_dir, "done", EXIT_OK, summary="Artifact approved")
        return EXIT_OK

    return STEP_CONTINUE


def get_critiques_for_scope(step: WorkflowStep, context: WorkflowContext) -> List[Critique]:
    """Get critiques based on adjudication scope."""
    if step.scope == "all":
        # All critiques from entire workflow
        all_critiques = []
        for critiques in context.critiques_by_step.values():
            all_critiques.extend(critiques)
        return all_critiques

    elif step.scope == "previous":
        # Only the immediately preceding critique step
        workflow = context.genflow_cfg.workflow
        for i in range(context.current_step_index - 1, -1, -1):
            prev_step = workflow[i]
            if prev_step.step == "critique":
                prev_name = prev_step.name or f"critique_{i}"
                return context.critiques_by_step.get(prev_name, [])
        return []

    else:  # "accumulated" (default)
        # All critiques not yet adjudicated
        return context.unadjudicated_critiques


async def execute_refine_step(step: WorkflowStep, context: WorkflowContext) -> int:
    """Execute a refine step with optional loop_to support."""
    step_name = step.name or "refine"
    write_live("")
    write_live(f"▶ STEP: {step_name} (refine, mode={step.mode})")

    if not context.last_adjudication:
        write_live("  No adjudication to refine from - skipping")
        return STEP_CONTINUE

    iter_dir = context.run_dir / "iterations" / str(context.iteration)
    curr_artifact_path = iter_dir / "artifact_refined.md"

    # Get agent
    agent_name = step.agent or "claude"
    if agent_name not in context.agents:
        logger.error(f"Agent '{agent_name}' not found")
        return EXIT_ERROR
    agent = context.agents[agent_name]
    agent_cmd = get_agent_cmd_with_model(agent, step.model)

    if step.mode == "edit":
        # File-based surgical editing
        write_live(f"  {agent_name} → applying surgical edits...")

        # Copy current artifact to working file
        shutil.copy(context.artifact_path, curr_artifact_path)

        prompt = build_refinement_prompt(
            artifact_path=curr_artifact_path,
            adjudication=context.last_adjudication,
            goal=context.goal,
            iteration=context.iteration,
        )

        # For Claude, add edit permissions
        if agent.kind == "claude":
            if "--dangerously-skip-permissions" not in agent_cmd:
                agent_cmd.append("--dangerously-skip-permissions")
            if "--add-dir" not in agent_cmd:
                agent_cmd.extend(["--add-dir", str(context.run_dir)])

    else:
        # Full rewrite mode
        write_live(f"  {agent_name} → rewriting artifact...")

        prompt = build_generator_prompt(
            goal=context.goal,
            source=context.source,
            compressed_constraints=context.compressed_constraints,
            previous_artifact=context.artifact,
            previous_adjudication=context.last_adjudication,
            iteration=context.iteration,
        )

    write_text_atomic(iter_dir / f"prompt_refine_{agent_name}.txt", prompt)

    # Run refine
    stream_prefix = agent_name if not context.no_stream else None
    rc, stdout, stderr = await run_process(
        agent_cmd, prompt, agent.timeout, stream_prefix, agent.suppress_stderr
    )

    if rc != 0 and not stdout.strip():
        logger.error(f"Refine failed: {stderr[:500]}")
        return EXIT_ERROR

    # Update artifact
    if step.mode == "edit":
        context.artifact = curr_artifact_path.read_text(encoding="utf-8").strip()
    else:
        context.artifact = stdout.strip()
        write_text_atomic(curr_artifact_path, context.artifact)

    context.artifact_path = curr_artifact_path

    write_live(f"  ✓ Refined artifact (~{len(context.artifact.split())} words)")

    # Log to thread
    append_jsonl_durable(
        context.thread_path,
        {
            "id": sha256(f"refine:{utc_now_iso()}:{context.iteration}"),
            "ts": utc_now_iso(),
            "iteration": context.iteration,
            "phase": "refine",
            "step_name": step_name,
            "agent": agent_name,
            "mode": step.mode,
            "artifact_path": str(curr_artifact_path),
        },
    )

    # Handle loop_to
    if step.loop_to:
        loop_index = get_step_index_by_name(context.genflow_cfg.workflow, step.loop_to)
        if loop_index >= 0:
            write_live(f"  → Looping back to step '{step.loop_to}'")
            context.current_step_index = loop_index
            # Clear critiques for re-evaluation
            context.critiques_by_step = {}
            context.unadjudicated_critiques = []
            return STEP_LOOP_BACK
        else:
            logger.warning(f"loop_to step '{step.loop_to}' not found")

    return STEP_CONTINUE
