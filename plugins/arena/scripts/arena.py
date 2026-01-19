#!/usr/bin/env python3
"""
Agent Arena Orchestrator

File-driven multi-agent orchestration for Claude Code, Codex, and Gemini CLIs.

Fixes from code review:
- Path traversal protection in artifact validation
- Fixed done tracking (per-cycle, not per-turn reset)
- Fixed stagnation detection for single-agent runs
- Added logging framework
- Fixed silent YAML parse failures
- Added input validation for mode/persona names
"""
from __future__ import annotations

import argparse
import asyncio
import dataclasses
import datetime as dt
import fcntl
import hashlib
import json
import logging
import os
import re
import stat
import sys
import tempfile
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, IO

import yaml  # Required for constraint loading

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("arena")

# Import utilities from utils module
from utils import (
    write_live, set_live_log, get_live_log,
    utc_now_iso, read_text, ensure_secure_dir,
    write_text_atomic, append_jsonl_durable,
    load_json, save_json_atomic,
    normalize_for_hash, sha256, text_similarity,
    validate_name, is_subpath, resolve_path_template,
    VALID_NAME_PATTERN,
)

# Import source resolution
from sources import (
    SourceBlock, ResolvedSources,
    resolve_source_block, resolve_legacy_sources,
)

# Import data models
from models import (
    OrchestratorLock, Agent, Envelope,
    ConstraintRule, Constraint,
    CritiqueIssue, Critique,
    AdjudicationDecision, Adjudication,
    DEFAULT_TIMEOUT_SECONDS,
)

# Import parsers
from parsers import (
    parse_envelope, parse_critique, parse_adjudication,
    validate_artifacts,
)

# Import config loading
from config import (
    load_constraints, compress_constraints, save_compressed_constraints,
    load_frontmatter_doc, load_mode, load_persona, load_profile, merge_profile,
)

# Import HITL functions
from hitl import (
    ingest_hitl_answers, write_hitl_questions,
    write_agent_result, write_resolution,
)

EXIT_OK = 0
EXIT_HITL = 10
EXIT_MAX_TURNS = 11  # Changed from 1 to be distinct from generic failure
EXIT_ERROR = 1

# Context window settings for thread history
DEFAULT_THREAD_HISTORY_COUNT = 10  # Number of recent messages to include
DEFAULT_MESSAGE_TRUNCATE_LENGTH = 2000  # Characters per message (was 500)

# Script location for plugin-relative paths
# When installed as plugin: points to plugin's scripts/ dir
# When run standalone: points to ~/.arena/
SCRIPT_DIR = Path(__file__).parent.resolve()
# Default config dir is sibling to scripts/ in plugin structure, or same dir for standalone
DEFAULT_CONFIG_DIR = (SCRIPT_DIR.parent / "config") if (SCRIPT_DIR.parent / "config").exists() else SCRIPT_DIR

# Add script directory to path for relative imports (enables running from any directory)
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

# Import router for dynamic expert selection
ROUTER_AVAILABLE = False
ROUTER_IMPORT_ERROR: Optional[str] = None
try:
    from router import select_experts, load_experts, save_routing_result
    ROUTER_AVAILABLE = True
except ImportError as e:
    ROUTER_IMPORT_ERROR = str(e)


# =============================================================================
# Prompt Templates for Reliable Generation
# =============================================================================

def build_generator_prompt(
    goal: str,
    source: str,
    compressed_constraints: str,
    previous_artifact: Optional[str],
    previous_adjudication: Optional[Adjudication],
    iteration: int,
) -> str:
    """Build prompt for the generator phase."""
    refinement_section = ""
    if previous_artifact and previous_adjudication:
        refinement_section = f"""
PREVIOUS ARTIFACT (ITERATION {iteration - 1})
{previous_artifact}

ADJUDICATION FEEDBACK
{previous_adjudication.bill_of_work}

INSTRUCTIONS
You are REFINING the previous artifact. Apply ONLY the fixes specified in the bill of work.
Do NOT introduce new content or restructure unless specifically required by the feedback.
Maintain the original structure and intent while addressing the issues.
"""
    else:
        refinement_section = """
INSTRUCTIONS
Generate initial content that satisfies the goal while adhering to all constraints.
Be thorough and complete - this is your first draft.
"""

    return f"""\
SYSTEM CONTEXT
You are a generator agent in a reliable generation pipeline.
Iteration: {iteration}

GOAL
{goal.strip()}

{f"SOURCE MATERIAL{chr(10)}{source.strip()}" if source else ""}

CONSTRAINTS
{compressed_constraints}

{refinement_section}

OUTPUT
Produce ONLY the artifact content (no JSON envelope, no explanations).
The output should be the complete, final text ready for critique.
""".strip()


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
    """Build prompt for a critic phase.

    Args:
        constraint: The constraint to evaluate against
        artifact: The artifact content to review
        goal: The goal context
        iteration: Current iteration number
        run_dir: Run directory for script path resolution
        project_root: Project root for script path resolution
        artifact_path: Path to the artifact file (for script stdin)
        allow_scripts: Whether to allow script execution in source blocks
        arena_home: Global arena home directory (~/.arena/)
    """
    rules_section = []
    for rule in constraint.rules:
        rule_text = f"### Rule: {rule.id}\n{rule.text}\nDefault Severity: {rule.default_severity}"
        if rule.examples:
            if "violation" in rule.examples:
                rule_text += f"\nExample Violation: {rule.examples['violation']}"
            if "compliant" in rule.examples:
                rule_text += f"\nExample Compliant: {rule.examples['compliant']}"
        rules_section.append(rule_text)

    # Build context for path resolution
    ctx = {}
    if run_dir:
        ctx["run_dir"] = run_dir
    if project_root:
        ctx["project_root"] = project_root
    if artifact_path:
        ctx["artifact"] = artifact_path
    if run_dir:
        ctx["source"] = run_dir / "source.md"
    if constraint.source_path:
        ctx["constraint_dir"] = constraint.source_path.parent
    elif run_dir:
        ctx["constraint_dir"] = run_dir / "constraints"
    if arena_home:
        ctx["arena_home"] = arena_home

    base_dir = constraint.source_path.parent if constraint.source_path else (run_dir / "constraints" if run_dir else Path.cwd())

    # Build script execution section if constraint has a script
    script_section = ""
    if constraint.script and run_dir and project_root and artifact_path:
        try:
            resolved_script = resolve_path_template(constraint.script, ctx, base_dir)
            script_section = f"""
PRE-ANALYSIS SCRIPT
Before your analysis, run this script to get additional validation information:
```
{resolved_script} < {artifact_path}
```

Execute the script and interpret the results as part of your critique.
Include any script output, errors, or issues in your findings.
If the script reports validation errors, treat them as findings in your response.

"""
        except ValueError as e:
            logger.warning(f"Invalid script path in constraint {constraint.id}: {e}")

    # Build sources section - NEW format (source_block) vs OLD format (sources)
    sources_section = ""

    if constraint.source_block and run_dir and project_root:
        # NEW format: resolve source block and inject content
        resolved = resolve_source_block(
            source_block=constraint.source_block,
            ctx=ctx,
            base_dir=base_dir,
            allow_scripts=allow_scripts,
        )

        if resolved.errors:
            for error in resolved.errors:
                logger.warning(f"Source resolution error in {constraint.id}: {error}")

        if resolved.warnings:
            for warning in resolved.warnings:
                logger.info(f"Source resolution warning in {constraint.id}: {warning}")

        if resolved.content.strip():
            sources_section = f"""
SOURCE MATERIAL
The following source material is provided for fact-checking and context:

{resolved.content}

"""

    elif constraint.sources and run_dir and project_root:
        # OLD format: just list paths (backward compatibility)
        resolved_sources, errors = resolve_legacy_sources(constraint.sources, ctx, base_dir)

        for error in errors:
            logger.warning(f"Legacy source error in {constraint.id}: {error}")

        if resolved_sources:
            sources_list = "\n".join(f"- {p}" for p in resolved_sources)
            sources_section = f"""
REFERENCE SOURCES
Read these files for context before your analysis:
{sources_list}

"""

    return f"""\
SYSTEM CONTEXT
You are a critic agent reviewing content for constraint: {constraint.id}
Iteration: {iteration}

CONSTRAINT: {constraint.id.upper()}
Priority: {constraint.priority}

{constraint.summary}
{sources_section}{script_section}RULES TO EVALUATE
{chr(10).join(rules_section)}

GOAL CONTEXT
{goal[:500]}

ARTIFACT TO REVIEW
{artifact}

OUTPUT REQUIREMENTS
Respond with a SINGLE JSON object (no markdown, no extra text):
{{
  "constraint_id": "{constraint.id}",
  "overall": "PASS" | "FAIL",
  "issues": [
    {{
      "id": "{constraint.id}-001",
      "rule_id": "rule-id-that-was-violated",
      "severity": "CRITICAL" | "HIGH" | "MEDIUM" | "LOW",
      "location": "paragraph X, sentence Y" or "section name",
      "finding": "What is wrong",
      "evidence": "Quote or reference from rules",
      "suggested_fix": "How to fix it",
      "confidence": 0.0-1.0
    }}
  ],
  "approved_sections": [
    {{"location": "paragraphs 1-5", "note": "Meets all criteria"}}
  ],
  "summary": "Brief summary of findings"
}}

EVALUATION GUIDELINES
- Be thorough but fair - only flag genuine violations
- Provide specific locations for each issue
- Suggest concrete fixes, not vague improvements
- Rate confidence based on clarity of violation
- If no issues found, return overall: "PASS" with empty issues array
""".strip()


def build_adjudicator_prompt(
    constraints: List[Constraint],
    artifact: str,
    critiques: List[Critique],
    goal: str,
    iteration: int,
    max_iterations: int,
) -> str:
    """Build prompt for the adjudicator phase."""
    constraints_section = "\n".join(
        f"- {c.id} (priority {c.priority}): {c.summary[:100]}..."
        for c in constraints
    )

    critiques_section = []
    for critique in critiques:
        critique_text = f"### {critique.reviewer} on {critique.constraint_id}: {critique.overall}"
        if critique.issues:
            for issue in critique.issues:
                critique_text += f"\n  - [{issue.severity}] {issue.id}: {issue.finding}"
        else:
            critique_text += "\n  No issues found"
        critiques_section.append(critique_text)

    return f"""\
SYSTEM CONTEXT
You are the adjudicator in a reliable generation pipeline.
Your role is to find the optimal boundary between competing constraints.
Iteration: {iteration}/{max_iterations}

GOAL
{goal.strip()}

CONSTRAINTS (ordered by priority)
{constraints_section}

ARTIFACT UNDER REVIEW
{artifact}

CRITIQUES FROM ALL REVIEWERS
{chr(10).join(critiques_section)}

YOUR ROLE
1. Analyze tensions between competing constraints
2. Decide which issues to pursue vs dismiss
3. Create a prioritized bill of work for the generator

DECISION CRITERIA
- CRITICAL issues: Must be fixed (safety, security, legal)
- HIGH issues: Should be fixed unless they conflict with higher-priority constraints
- MEDIUM/LOW issues: Fix if easy, dismiss if they conflict or are stylistic
- When constraints conflict: Higher priority wins, but find the boundary that satisfies both maximally

OUTPUT REQUIREMENTS
Respond with a SINGLE JSON object:
{{
  "iteration": {iteration},
  "status": "REWRITE" | "APPROVED",
  "tension_analysis": [
    {{
      "axis": "constraint-A vs constraint-B",
      "current_position": "where the artifact currently sits",
      "target": "where it should be",
      "guidance": "how to get there"
    }}
  ],
  "decisions": [
    {{
      "issue_id": "constraint-001",
      "constraint": "constraint-name",
      "severity": "CRITICAL|HIGH|MEDIUM|LOW",
      "status": "pursuing" | "dismissed",
      "flagged_by": ["agent1", "agent2"],
      "competing_constraint": "other-constraint or null",
      "adjudication": "reasoning if in tension",
      "rationale": "why dismissed (if dismissed)",
      "guidance": "specific fix instructions"
    }}
  ],
  "termination": {{
    "critical_pursuing": 0,
    "high_pursuing": 0
  }},
  "bill_of_work": "## MUST FIX (CRITICAL)\\n1. issue-id: guidance\\n\\n## SHOULD FIX (HIGH)\\n..."
}}

APPROVAL CRITERIA
- Status should be "APPROVED" only if:
  - No CRITICAL issues pursuing
  - No HIGH issues pursuing (or profile allows some HIGH issues)
- Otherwise status should be "REWRITE"
""".strip()


async def run_process(
    cmd: List[str],
    stdin_text: str,
    timeout: Optional[int],
    stream_prefix: Optional[str] = None,
    suppress_stderr: bool = False,
) -> Tuple[int, str, str]:
    """Run subprocess with optional timeout and streaming output.

    Args:
        cmd: Command to run
        stdin_text: Text to send to stdin
        timeout: Timeout in seconds (None = no timeout)
        stream_prefix: If set, stream output to console with this prefix
        suppress_stderr: If True, don't stream stderr (still capture it)

    Returns:
        (returncode, stdout, stderr)
    """
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    # Write stdin and close
    if proc.stdin:
        proc.stdin.write(stdin_text.encode("utf-8"))
        await proc.stdin.drain()
        proc.stdin.close()
        await proc.stdin.wait_closed()

    stdout_lines: List[str] = []
    stderr_lines: List[str] = []

    async def read_stream(
        stream: asyncio.StreamReader, lines: List[str], is_stderr: bool = False
    ) -> None:
        """Read stream line by line, optionally printing with prefix."""
        while True:
            line_bytes = await stream.readline()
            if not line_bytes:
                break
            line = line_bytes.decode("utf-8", errors="replace").rstrip("\n\r")
            lines.append(line)
            # Skip streaming stderr if suppressed (still captured in lines)
            if is_stderr and suppress_stderr:
                continue
            if stream_prefix:
                prefix = f"{stream_prefix}"
                if is_stderr:
                    prefix = f"{stream_prefix} [stderr]"
                # Write to live log
                write_live(line, prefix=f"{prefix}: ")
                # Also print to stdout
                print(f"  {prefix}: {line}", flush=True)

    async def run_with_streaming() -> int:
        """Run the process with streaming output."""
        await asyncio.gather(
            read_stream(proc.stdout, stdout_lines, is_stderr=False),
            read_stream(proc.stderr, stderr_lines, is_stderr=True),
        )
        await proc.wait()
        return proc.returncode or 0

    try:
        if timeout:
            rc = await asyncio.wait_for(run_with_streaming(), timeout=timeout)
        else:
            rc = await run_with_streaming()
        return rc, "\n".join(stdout_lines), "\n".join(stderr_lines)
    except asyncio.TimeoutError:
        # Graceful shutdown: try terminate first, then kill
        try:
            proc.terminate()
            await asyncio.wait_for(proc.wait(), timeout=5)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
        return -1, "\n".join(stdout_lines), f"Process timed out after {timeout}s"


async def run_agent(
    agent: Agent, prompt: str, stream: bool = True
) -> Tuple[Envelope, str, str]:
    """Run agent CLI and parse response.

    Args:
        agent: Agent to run
        prompt: Prompt to send
        stream: If True, stream output to console with agent name prefix
    """
    stream_prefix = agent.name if stream else None
    rc, stdout, stderr = await run_process(
        agent.cmd, prompt, agent.timeout, stream_prefix, agent.suppress_stderr
    )

    if rc == -1:  # Timeout
        return Envelope.error(f"Timeout after {agent.timeout}s"), stdout, stderr

    if rc != 0 and not stdout.strip():
        return Envelope.error(f"Exit code {rc}: {stderr[:500]}"), stdout, stderr

    # Warn if non-zero exit but has output
    if rc != 0:
        logger.warning(f"Agent {agent.name} exited with code {rc} but produced output")

    env, err = parse_envelope(stdout, agent.kind)
    return env, stdout, stderr


async def run_research(
    topics: List[str],
    research_agent_cmd: List[str],
    goal: str,
    stream: bool = True,
) -> str:
    """Run web research on given topics using the specified agent."""
    research_prompt = f"""\
You are a focused web researcher. Research the following topics thoroughly using web search.

GOAL CONTEXT:
{goal[:500]}

RESEARCH TOPICS:
{chr(10).join(f"- {t}" for t in topics)}

INSTRUCTIONS:
1. Search for each topic using targeted queries
2. Extract specific facts, data, and insights
3. Include source URLs for all findings
4. Focus on actionable information relevant to the goal

OUTPUT FORMAT:
Provide findings as a structured list:
- [Finding] (Source: URL)

Be thorough but concise. No fluff.
"""
    write_live("=" * 40)
    write_live(f"RESEARCH: {', '.join(topics[:3])}")
    write_live("=" * 40)

    rc, stdout, stderr = await run_process(
        research_agent_cmd,
        research_prompt,
        timeout=None,
        stream_prefix="researcher" if stream else None,
    )

    if rc != 0 and not stdout.strip():
        return f"Research failed: {stderr[:500]}"

    return stdout.strip()


def build_prompt(
    agent_name: str,
    mode: str,
    mode_body: str,
    persona_body: str,
    pattern: str,
    turn_idx: int,
    max_turns: int,
    goal: str,
    context: str,
    summary: str,
    thread_tail: List[Dict[str, Any]],
    hitl_answers: Optional[Dict[str, Any]] = None,
    enable_research: bool = False,
) -> str:
    """Build the prompt for an agent."""
    thread_text = "\n".join(
        f"[{m.get('agent', '?')}|{m.get('status', '?')}] {m.get('content', '')[:DEFAULT_MESSAGE_TRUNCATE_LENGTH]}"
        for m in thread_tail[-DEFAULT_THREAD_HISTORY_COUNT:]
    )

    answers_section = ""
    if hitl_answers:
        answers_section = f"""
HUMAN ANSWERS TO PREVIOUS QUESTIONS
{json.dumps(hitl_answers, indent=2)}
"""

    return f"""\
SYSTEM CONTEXT
You are agent "{agent_name}" in a multi-agent orchestration system.
Mode: {mode} | Pattern: {pattern} | Turn: {turn_idx}/{max_turns}

{mode_body}

{persona_body}

GOAL
{goal.strip()}

SHARED CONTEXT
{context.strip()}

ROLLING SUMMARY
{summary.strip() if summary else "(none)"}

CONVERSATION THREAD (recent)
{thread_text if thread_text else "(start of conversation)"}
{answers_section}

OUTPUT REQUIREMENTS
Respond with a SINGLE JSON object (no markdown, no extra text):
{{
  "status": "ok" | "needs_human" | "needs_research" | "done" | "error",
  "message": "your response",
  "questions": [  // empty if none
    {{"id": "q1", "question": "...", "priority": "critical|high|normal", "required": true}}
  ],
  "research_topics": [  // empty if none - only when status="needs_research"
    "specific topic to research"
  ],
  "artifacts": [  // empty if none
    {{"path": "relative/path", "description": "what it is"}}
  ],
  "confidence": 0.0-1.0,  // optional
  "agrees_with": ["agent_name"],  // optional, for consensus
  "objections": [  // optional
    {{"target": "agent or idea", "severity": "critical|major|minor", "reason": "why"}}
  ]
}}

- If you need human clarification, set status="needs_human" with questions
- If the goal is fully satisfied, set status="done"
- Include confidence (0.0-1.0) when making assessments
- Use agrees_with to indicate consensus with other agents
{f'- If you need web research to inform your response, set status="needs_research" with research_topics' if enable_research else ''}
""".strip()


def tail_thread(thread_path: Path, n: int = 20) -> List[Dict[str, Any]]:
    """Read last N entries from thread JSONL."""
    if not thread_path.exists():
        return []
    lines = thread_path.read_text(encoding="utf-8").splitlines()
    out = []
    for line in lines[-n:]:
        try:
            obj = json.loads(line)
            if isinstance(obj, dict):
                out.append(obj)
        except json.JSONDecodeError:
            continue
    return out


def detect_stagnation(
    thread_tail: List[Dict[str, Any]], agents: List[str], threshold: float = 0.90
) -> bool:
    """Detect if last two rounds are too similar (stagnation)."""
    # Need at least 2 agents to detect meaningful stagnation
    if len(agents) < 2:
        return False

    # Get last messages per agent for last 2 rounds
    agent_msgs: Dict[str, List[str]] = {a: [] for a in agents}
    for entry in reversed(thread_tail):
        agent = entry.get("agent", "")
        if agent in agent_msgs and len(agent_msgs[agent]) < 2:
            agent_msgs[agent].append(entry.get("content", ""))

    # Need at least 2 messages per agent to compare
    agents_with_history = [a for a, msgs in agent_msgs.items() if len(msgs) >= 2]
    if len(agents_with_history) < 2:
        return False

    # Check similarity for each agent
    for agent in agents_with_history:
        msgs = agent_msgs[agent]
        sim = text_similarity(msgs[0], msgs[1])
        if sim < threshold:
            return False  # Significant change detected

    return True  # All agents stagnated


def check_consensus(envelopes: Dict[str, Envelope], min_agree: int = 2) -> bool:
    """Check for consensus using agrees_with field or message similarity."""
    agents = list(envelopes.keys())
    if len(agents) < 2:
        return False

    # Check explicit agrees_with
    for agent, env in envelopes.items():
        if env.status in ("error", "needs_human"):
            return False
        if env.agrees_with:
            agreers = set(env.agrees_with) | {agent}
            if len(agreers & set(agents)) >= min_agree:
                return True

    # Fallback: check message similarity
    messages = [(a, envelopes[a].message) for a in agents]
    for i, (a1, m1) in enumerate(messages):
        similar_count = 1
        for j, (a2, m2) in enumerate(messages):
            if i != j and text_similarity(m1, m2) > 0.85:
                similar_count += 1
        if similar_count >= min_agree:
            return True

    return False


# =============================================================================
# Multi-Phase Orchestrator (Reliable Generation Pattern)
# =============================================================================

async def run_multi_phase_orchestrator(
    args: argparse.Namespace,
    cfg: Dict[str, Any],
    state_dir: Path,
    global_dir: Optional[Path],
    run_dir: Path,
    agents: Dict[str, Agent],
    phases_config: Dict[str, Any],
) -> int:
    """Run multi-phase orchestration (Generate → Critique → Adjudicate → Refine loop)."""
    thread_path = run_dir / "thread.jsonl"
    state_path = run_dir / "state.json"

    # Load state for resuming
    state = load_json(
        state_path,
        {
            "awaiting_human": False,
            "iteration": 1,
            "phase": "generate",
            "artifact": None,
            "critiques": [],
            "adjudication": None,
        },
    )

    # HITL directory for this run
    hitl_dir = run_dir / "hitl"
    ensure_secure_dir(hitl_dir)

    # Check for HITL resume
    hitl_answers = None
    if state.get("awaiting_human"):
        hitl_answers = ingest_hitl_answers(run_dir)
        if hitl_answers:
            state["awaiting_human"] = False
            # Add answers to thread
            append_jsonl_durable(
                thread_path,
                {
                    "id": sha256(f"human:{utc_now_iso()}"),
                    "ts": utc_now_iso(),
                    "iteration": state.get("iteration", 1),
                    "phase": "hitl_response",
                    "agent": "human",
                    "role": "user",
                    "content": json.dumps(hitl_answers),
                },
            )
            save_json_atomic(state_path, state)
            write_live("=" * 60)
            write_live("RESUMING: Human answers received")
            write_live("=" * 60)
        else:
            logger.info(f"Awaiting human answers at {hitl_dir / 'answers.json'}")
            logger.info(f"See questions at {hitl_dir / 'questions.json'}")
            return EXIT_HITL

    # Load inputs
    goal = read_text(run_dir / "goal.md")
    source = read_text(run_dir / "source.md")
    constraints = load_constraints(run_dir / "constraints")

    if not goal.strip():
        logger.error(f"No goal defined in {run_dir / 'goal.md'}")
        return EXIT_ERROR

    if not constraints:
        logger.warning("No constraints found - running without constraint enforcement")
        write_live("WARNING: No constraints/ directory or no .yaml files found")

    # Compress constraints for generator
    compressed = compress_constraints(constraints)
    if compressed:
        save_compressed_constraints(run_dir, compressed)

    # Configuration
    max_iterations = phases_config.get("refine", {}).get("max_iterations", 3)
    if args.max_iterations:
        max_iterations = args.max_iterations

    termination_config = phases_config.get("termination", {})
    approve_when = termination_config.get("approve_when", "no_critical_and_no_high")

    # Agent configuration
    generate_agent_name = phases_config.get("generate", {}).get("agent", "claude")
    adjudicate_agent_name = phases_config.get("adjudicate", {}).get("agent", "claude")
    critique_agents = phases_config.get("critique", {}).get("agents", ["claude", "codex", "gemini"])

    # Validate agents exist
    for agent_name in [generate_agent_name, adjudicate_agent_name] + critique_agents:
        if agent_name not in agents:
            logger.error(f"Agent '{agent_name}' not found in configuration")
            return EXIT_ERROR

    # Log configuration
    write_live("=" * 60)
    write_live(f"RELIABLE GENERATION: {run_dir.name}")
    write_live(f"Goal: {goal[:50]}...")
    constraint_summary = ", ".join(f"{c.id} ({len(c.rules)} rules)" for c in constraints)
    write_live(f"Constraints: {constraint_summary}" if constraints else "Constraints: (none)")
    write_live(f"Max iterations: {max_iterations}")
    write_live("")

    # Dry run mode - just show configuration and structure guidance
    if args.dry_run:
        write_live("DRY RUN - Configuration Preview")
        write_live("-" * 40)
        write_live("")
        write_live("AGENTS:")
        write_live(f"  Generator:   {generate_agent_name}")
        write_live(f"  Critics:     {', '.join(critique_agents)}")
        write_live(f"  Adjudicator: {adjudicate_agent_name}")
        write_live("")
        write_live("INPUTS:")
        write_live(f"  Goal:        {run_dir / 'goal.md'} {'✓' if goal.strip() else '✗ MISSING'}")
        write_live(f"  Source:      {run_dir / 'source.md'} {'✓' if source.strip() else '(optional)'}")
        write_live(f"  Constraints: {run_dir / 'constraints/'}")
        write_live("")

        if constraints:
            write_live("CONSTRAINT ROUTING (all-to-all):")
            total_critiques = len(constraints) * len(critique_agents)
            write_live(f"  {len(constraints)} constraints × {len(critique_agents)} agents = {total_critiques} critique tasks")
            write_live("")
            for constraint in constraints:
                write_live(f"  {constraint.id} (priority {constraint.priority}, {len(constraint.rules)} rules):")
                for agent in critique_agents:
                    write_live(f"    → {agent}")
        else:
            write_live("⚠ NO CONSTRAINTS FOUND")
            write_live("")
            write_live("Expected structure:")
            write_live(f"  {run_dir}/")
            write_live("  ├── goal.md              # What to generate (REQUIRED)")
            write_live("  ├── source.md            # Source material (optional)")
            write_live("  └── constraints/         # Constraint files (REQUIRED)")
            write_live("      ├── safety.yaml      # Example: safety rules")
            write_live("      ├── quality.yaml     # Example: quality standards")
            write_live("      └── tone.yaml        # Example: tone/style rules")
            write_live("")
            write_live("Constraint YAML format:")
            write_live("  id: safety")
            write_live("  priority: 1              # Lower = higher priority")
            write_live("  summary: |")
            write_live("    Brief description for generator...")
            write_live("  rules:")
            write_live("    - id: rule-name")
            write_live("      text: \"Detailed rule for critics\"")
            write_live("      default_severity: CRITICAL  # CRITICAL/HIGH/MEDIUM/LOW")
            write_live("")
            write_live(f"See template: {SCRIPT_DIR.parent / 'templates' / 'reliable-generation' / 'README.md'}")

        write_live("")
        write_live("-" * 40)
        return EXIT_OK

    # Resume from saved state
    iteration = state.get("iteration", 1)
    current_phase = state.get("phase", "generate")
    artifact = state.get("artifact")
    critiques = [Critique.from_dict(c) for c in state.get("critiques", [])]
    adjudication = Adjudication.from_dict(state["adjudication"]) if state.get("adjudication") else None

    # Main iteration loop
    while iteration <= max_iterations:
        iter_dir = run_dir / "iterations" / str(iteration)
        iter_dir.mkdir(parents=True, exist_ok=True)

        # Phase: Generate
        if current_phase == "generate":
            write_live("")
            write_live(f"▶ PHASE: Generation (iteration {iteration})")
            write_live(f"  {generate_agent_name} → generating draft...")

            prompt = build_generator_prompt(
                goal=goal,
                source=source,
                compressed_constraints=compressed,
                previous_artifact=artifact,
                previous_adjudication=adjudication,
                iteration=iteration,
            )

            # Save prompt
            write_text_atomic(iter_dir / f"prompt_generate_{generate_agent_name}.txt", prompt)

            # Run generator
            agent = agents[generate_agent_name]
            rc, stdout, stderr = await run_process(
                agent.cmd, prompt, agent.timeout,
                stream_prefix=generate_agent_name if not args.no_stream else None,
                suppress_stderr=agent.suppress_stderr,
            )

            if rc != 0 and not stdout.strip():
                logger.error(f"Generator failed: {stderr[:500]}")
                return EXIT_ERROR

            artifact = stdout.strip()
            write_text_atomic(iter_dir / "artifact.md", artifact)

            token_count = len(artifact.split())
            write_live(f"  ✓ Draft complete (~{token_count} words)")

            # Append to thread
            append_jsonl_durable(
                thread_path,
                {
                    "id": sha256(f"generator:{utc_now_iso()}:{iteration}"),
                    "ts": utc_now_iso(),
                    "iteration": iteration,
                    "phase": "generate",
                    "agent": generate_agent_name,
                    "role": "assistant",
                    "content": f"Generated artifact ({token_count} words)",
                    "artifact_path": str(iter_dir / "artifact.md"),
                },
            )

            # Update state
            state["artifact"] = artifact
            state["phase"] = "critique"
            state["critiques"] = []
            save_json_atomic(state_path, state)

            current_phase = "critique"

        # Phase: Critique (parallel, all-to-all)
        if current_phase == "critique":
            write_live("")
            write_live("▶ PHASE: Critique (parallel)")

            critiques_dir = iter_dir / "critiques"
            critiques_dir.mkdir(parents=True, exist_ok=True)

            # Build all critique tasks (all agents × all constraints)
            critique_tasks = []
            task_info = []

            # Calculate project_root (parent of state_dir, which is .arena)
            project_root = state_dir.parent
            artifact_path = iter_dir / "artifact.md"

            # Get arena_home for source resolution
            arena_home = global_dir if global_dir else Path.home() / ".arena"

            for constraint in constraints:
                for agent_name in critique_agents:
                    agent = agents[agent_name]
                    prompt = build_critic_prompt(
                        constraint=constraint,
                        artifact=artifact,
                        goal=goal,
                        iteration=iteration,
                        run_dir=run_dir,
                        project_root=project_root,
                        artifact_path=artifact_path,
                        allow_scripts=args.allow_scripts if hasattr(args, 'allow_scripts') else False,
                        arena_home=arena_home,
                    )

                    write_text_atomic(
                        critiques_dir / f"prompt_{constraint.id}_{agent_name}.txt",
                        prompt,
                    )

                    write_live(f"  {agent_name} ({constraint.id}) → reviewing...")

                    critique_tasks.append(
                        run_process(
                            agent.cmd, prompt, agent.timeout,
                            stream_prefix=f"{agent_name}[{constraint.id}]" if not args.no_stream else None,
                            suppress_stderr=agent.suppress_stderr,
                        )
                    )
                    task_info.append((agent_name, constraint))

            # Run all critiques in parallel
            results = await asyncio.gather(*critique_tasks)

            critiques = []
            for (rc, stdout, stderr), (agent_name, constraint) in zip(results, task_info):
                critique = parse_critique(stdout, agent_name, constraint.id, iteration)
                critiques.append(critique)

                # Save critique output
                save_json_atomic(
                    critiques_dir / f"{constraint.id}-{agent_name}.json",
                    critique.to_dict(),
                )

                # Log summary
                issue_count = len(critique.issues)
                critical_count = sum(1 for i in critique.issues if i.severity == "CRITICAL")
                high_count = sum(1 for i in critique.issues if i.severity == "HIGH")

                if issue_count > 0:
                    write_live(f"  {agent_name} ({constraint.id}): {critical_count} CRITICAL, {high_count} HIGH, {issue_count - critical_count - high_count} other")
                else:
                    write_live(f"  {agent_name} ({constraint.id}): PASS")

                # Append to thread
                append_jsonl_durable(
                    thread_path,
                    {
                        "id": sha256(f"critique:{agent_name}:{constraint.id}:{utc_now_iso()}"),
                        "ts": utc_now_iso(),
                        "iteration": iteration,
                        "phase": "critique",
                        "agent": agent_name,
                        "constraint": constraint.id,
                        "role": "assistant",
                        "overall": critique.overall,
                        "issues_count": issue_count,
                        "content": critique.summary,
                    },
                )

            # Update state
            state["critiques"] = [c.to_dict() for c in critiques]
            state["phase"] = "adjudicate"
            save_json_atomic(state_path, state)

            current_phase = "adjudicate"

        # Phase: Adjudicate
        if current_phase == "adjudicate":
            write_live("")
            write_live("▶ PHASE: Adjudication")
            write_live(f"  {adjudicate_agent_name} → analyzing critiques...")

            prompt = build_adjudicator_prompt(
                constraints=constraints,
                artifact=artifact,
                critiques=critiques,
                goal=goal,
                iteration=iteration,
                max_iterations=max_iterations,
            )

            # Log context size for monitoring
            context_tokens = len(prompt.split())
            if context_tokens > 100000:  # Warn at ~100K words (rough proxy for tokens)
                write_live(f"  ⚠ Large context: ~{context_tokens} words")

            write_text_atomic(iter_dir / f"prompt_adjudicate_{adjudicate_agent_name}.txt", prompt)

            agent = agents[adjudicate_agent_name]
            rc, stdout, stderr = await run_process(
                agent.cmd, prompt, agent.timeout,
                stream_prefix=adjudicate_agent_name if not args.no_stream else None,
                suppress_stderr=agent.suppress_stderr,
            )

            if rc != 0 and not stdout.strip():
                logger.error(f"Adjudicator failed: {stderr[:500]}")
                return EXIT_ERROR

            adjudication = parse_adjudication(stdout, iteration)
            save_json_atomic(iter_dir / "adjudication.yaml", adjudication.to_dict())

            write_live(f"  Verdict: {adjudication.status}")
            write_live(f"    CRITICAL pursuing: {adjudication.critical_pursuing}")
            write_live(f"    HIGH pursuing: {adjudication.high_pursuing}")

            # Append to thread
            append_jsonl_durable(
                thread_path,
                {
                    "id": sha256(f"adjudication:{utc_now_iso()}:{iteration}"),
                    "ts": utc_now_iso(),
                    "iteration": iteration,
                    "phase": "adjudicate",
                    "agent": adjudicate_agent_name,
                    "role": "assistant",
                    "status": adjudication.status,
                    "critical_pursuing": adjudication.critical_pursuing,
                    "high_pursuing": adjudication.high_pursuing,
                    "content": adjudication.bill_of_work[:500],
                },
            )

            # Check for approval
            if adjudication.status == "APPROVED":
                write_live("")
                write_live("✓ APPROVED: All constraints satisfied")

                # Save final artifact
                final_dir = run_dir / "final"
                final_dir.mkdir(parents=True, exist_ok=True)
                write_text_atomic(final_dir / "artifact.md", artifact)
                write_live(f"  Output: {final_dir / 'artifact.md'}")

                write_resolution(run_dir, "approved", iteration, "All constraints satisfied")
                write_agent_result(run_dir, "done", EXIT_OK, summary="Artifact approved")
                return EXIT_OK

            # Check for thrashing (same issues returning)
            if iteration >= 2:
                prev_adjudication_path = run_dir / "iterations" / str(iteration - 1) / "adjudication.yaml"
                if prev_adjudication_path.exists():
                    prev_adj = Adjudication.from_dict(load_json(prev_adjudication_path, {}))
                    prev_issues = {d.issue_id for d in prev_adj.decisions if d.status == "pursuing"}
                    curr_issues = {d.issue_id for d in adjudication.decisions if d.status == "pursuing"}
                    if prev_issues & curr_issues:
                        overlapping = prev_issues & curr_issues
                        write_live(f"  ⚠ Thrashing detected: {overlapping}")

                        # Check for HITL escalation
                        if "thrashing" in termination_config.get("escalate_on", []):
                            hitl_questions = [{
                                "agent": "orchestrator",
                                "questions": [{
                                    "id": "thrashing",
                                    "question": f"Thrashing detected on issues: {overlapping}. How should we proceed?",
                                    "priority": "critical",
                                    "required": True,
                                }],
                            }]
                            write_hitl_questions(run_dir, hitl_questions, iteration)
                            state["awaiting_human"] = True
                            state["adjudication"] = adjudication.to_dict()
                            save_json_atomic(state_path, state)
                            logger.info("HITL requested due to thrashing")
                            write_agent_result(run_dir, "needs_human", EXIT_HITL, questions=hitl_questions)
                            return EXIT_HITL

            # Check for conflicting criticals requiring HITL
            if "conflicting_criticals" in termination_config.get("escalate_on", []):
                critical_issues = [d for d in adjudication.decisions if d.severity == "CRITICAL" and d.status == "pursuing"]
                if len(critical_issues) > 1:
                    # Check if any compete
                    for i, issue in enumerate(critical_issues):
                        if issue.competing_constraint:
                            write_live(f"  ⚠ Conflicting CRITICAL issues detected")
                            hitl_questions = [{
                                "agent": "orchestrator",
                                "questions": [{
                                    "id": "conflict",
                                    "question": f"CRITICAL issues conflict: {issue.issue_id} vs {issue.competing_constraint}. Which takes priority?",
                                    "priority": "critical",
                                    "required": True,
                                }],
                            }]
                            write_hitl_questions(run_dir, hitl_questions, iteration)
                            state["awaiting_human"] = True
                            state["adjudication"] = adjudication.to_dict()
                            save_json_atomic(state_path, state)
                            logger.info("HITL requested due to conflicting criticals")
                            write_agent_result(run_dir, "needs_human", EXIT_HITL, questions=hitl_questions)
                            return EXIT_HITL

            # Update state for next iteration
            state["adjudication"] = adjudication.to_dict()
            state["phase"] = "generate"
            state["iteration"] = iteration + 1
            save_json_atomic(state_path, state)

            iteration += 1
            current_phase = "generate"

            if iteration > max_iterations:
                break

            write_live("")
            write_live(f"▶ PHASE: Refinement ({iteration - 1}/{max_iterations})")
            write_live(f"  {generate_agent_name} → applying fixes...")

    # Max iterations reached without approval
    write_live("")
    write_live(f"⚠ Max iterations ({max_iterations}) reached without approval")

    # Save best artifact to final regardless of escalation
    final_dir = run_dir / "final"
    final_dir.mkdir(parents=True, exist_ok=True)
    write_text_atomic(final_dir / "artifact.md", artifact)
    write_text_atomic(final_dir / "status.md", f"# Status: MAX_ITERATIONS\n\nReached {max_iterations} iterations without full approval.\n\nRemaining issues:\n{adjudication.bill_of_work if adjudication else 'Unknown'}")

    if "max_iterations" in termination_config.get("escalate_on", []):
        # Escalate to human for decision
        hitl_questions = [{
            "agent": "orchestrator",
            "questions": [{
                "id": "max_iterations",
                "question": f"Max iterations ({max_iterations}) reached. Accept current artifact or continue?",
                "priority": "high",
                "required": True,
            }],
        }]
        write_hitl_questions(run_dir, hitl_questions, iteration)
        state["awaiting_human"] = True
        save_json_atomic(state_path, state)
        write_agent_result(run_dir, "needs_human", EXIT_HITL, questions=hitl_questions)
        return EXIT_HITL
    else:
        # Just exit with max_turns code (no HITL configured)
        write_resolution(run_dir, "max_iterations", max_iterations, f"Reached max iterations ({max_iterations})")
        write_agent_result(run_dir, "done", EXIT_MAX_TURNS, summary="Max iterations reached")
        return EXIT_MAX_TURNS


async def run_orchestrator(args: argparse.Namespace) -> int:
    """Main orchestrator loop."""
    cfg = load_json(Path(args.config), {})
    state_dir = Path(cfg.get("state_dir", ".arena"))

    # Global dir for shared modes/personas/profiles
    # Priority: config value > DEFAULT_CONFIG_DIR (auto-detected from script location)
    global_dir_str = cfg.get("global_dir")
    if global_dir_str:
        global_dir = Path(global_dir_str).expanduser()
    else:
        global_dir = DEFAULT_CONFIG_DIR

    # Load and merge profile if specified
    if args.profile:
        profile = load_profile(state_dir, args.profile, global_dir)
        if profile:
            logger.info(f"Loaded profile: {args.profile}")
            if profile.get("description"):
                logger.info(f"  {profile['description']}")
            cfg = merge_profile(cfg, profile)

            # Apply profile settings to args (if not overridden on CLI)
            if args.turns is None and "turns" in profile:
                args.turns = profile["turns"]
            if not args.stop_on_consensus and profile.get("stop_on_consensus"):
                args.stop_on_consensus = True
            if not args.stop_on_stagnation and profile.get("stop_on_stagnation"):
                args.stop_on_stagnation = True
            if not args.pattern and "pattern" in profile:
                args.pattern = profile["pattern"]
            if not args.mode and "mode" in profile:
                args.mode = profile["mode"]

    # Default turns if still not set
    if args.turns is None:
        args.turns = 6

    # Acquire lock
    lock = OrchestratorLock(state_dir)
    if not lock.acquire():
        logger.error("Another orchestrator is running. Exiting.")
        return EXIT_ERROR

    try:
        return await _run_orchestrator_locked(args, cfg, state_dir, global_dir)
    finally:
        lock.release()


async def _run_orchestrator_locked(
    args: argparse.Namespace,
    cfg: Dict[str, Any],
    state_dir: Path,
    global_dir: Optional[Path],
) -> int:
    """Orchestrator logic (with lock held)."""
    ensure_secure_dir(state_dir)

    # Create or use named run directory
    if args.name:
        run_name = args.name
    else:
        run_name = dt.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")

    run_dir = state_dir / "runs" / run_name
    is_new_run = not run_dir.exists()
    ensure_secure_dir(run_dir)

    # Create/update runs/latest symlink for run discovery
    latest_link = state_dir / "runs" / "latest"
    if latest_link.is_symlink() or latest_link.exists():
        latest_link.unlink()
    latest_link.symlink_to(run_name)  # relative symlink within runs/

    # Check for goal.md in run directory
    goal_path = run_dir / "goal.md"
    if is_new_run and not goal_path.exists():
        # Create template goal.md for user to edit
        goal_path.write_text("# Goal\n\nDescribe your objective here.\n", encoding="utf-8")
        logger.info(f"Created {goal_path} - edit it and re-run")
        return EXIT_ERROR

    # Open live log in run directory (append if resuming)
    live_log_path = run_dir / "live.log"
    live_log_file = open(live_log_path, "a", encoding="utf-8")
    set_live_log(live_log_file)

    # Create/update symlink in state_dir root for easy access
    live_link = state_dir / "live.log"
    if live_link.is_symlink() or live_link.exists():
        live_link.unlink()
    live_link.symlink_to(live_log_path.relative_to(state_dir))

    write_live("=" * 60)
    write_live(f"ARENA ORCHESTRATOR - {run_name}")
    write_live(f"Watch: tail -f {state_dir}/live.log")
    write_live("=" * 60)

    try:
        return await _run_orchestrator_inner(args, cfg, state_dir, global_dir, run_dir)
    finally:
        write_live("=" * 60)
        write_live("ORCHESTRATOR FINISHED")
        write_live("=" * 60)
        live_log_file.close()
        set_live_log(None)


async def _run_orchestrator_inner(
    args: argparse.Namespace,
    cfg: Dict[str, Any],
    state_dir: Path,
    global_dir: Optional[Path],
    run_dir: Path,
) -> int:
    """Inner orchestrator logic."""
    thread_path = run_dir / "thread.jsonl"
    state_path = run_dir / "state.json"

    # Load state (for resuming interrupted runs)
    state = load_json(
        state_path,
        {"awaiting_human": False, "turn": 0, "done_agents": [], "done_cycle": -1},
    )

    # HITL directory for this run
    hitl_dir = run_dir / "hitl"
    ensure_secure_dir(hitl_dir)

    # Check for HITL resume
    hitl_answers = None
    if state.get("awaiting_human"):
        hitl_answers = ingest_hitl_answers(run_dir)
        if hitl_answers:
            state["awaiting_human"] = False
            # Add answers to thread
            append_jsonl_durable(
                thread_path,
                {
                    "id": sha256(f"human:{utc_now_iso()}"),
                    "ts": utc_now_iso(),
                    "turn": state["turn"],
                    "agent": "human",
                    "role": "user",
                    "status": "ok",
                    "content": json.dumps(hitl_answers),
                },
            )
            save_json_atomic(state_path, state)
        else:
            logger.info(f"Awaiting human answers at {hitl_dir / 'answers.json'}")
            logger.info(f"See questions at {hitl_dir / 'questions.json'}")
            return EXIT_HITL

    # Load inputs from run directory
    goal = read_text(run_dir / "goal.md")
    context = read_text(run_dir / "context.md")
    summary = read_text(run_dir / "summary.md")

    if not goal.strip():
        logger.error(f"No goal defined in {run_dir / 'goal.md'}")
        return EXIT_ERROR

    # Load mode (checks local .arena/modes/ first, then global ~/.arena/modes/)
    mode_name = args.mode or cfg.get("mode", "collaborative")
    try:
        mode_meta, mode_body = load_mode(state_dir, mode_name, global_dir)
    except ValueError as e:
        logger.error(str(e))
        return EXIT_ERROR

    # Build agents
    agents_cfg = cfg.get("agents", {})
    agents: Dict[str, Agent] = {}
    for name, acfg in agents_cfg.items():
        agents[name] = Agent(
            name=name,
            kind=acfg["kind"],
            cmd=acfg["cmd"],
            timeout=acfg.get("timeout", DEFAULT_TIMEOUT_SECONDS),
            suppress_stderr=acfg.get("suppress_stderr", False),
        )

    order = cfg.get("order", list(agents.keys()))
    if not order:
        logger.error("No agents configured")
        return EXIT_ERROR

    # Validate order against agents
    for agent_name in order:
        if agent_name not in agents:
            logger.error(f"Agent '{agent_name}' in order but not defined in agents")
            return EXIT_ERROR

    # Check for multi-phase pattern (reliable generation)
    phases_config = cfg.get("phases")
    pattern = (
        args.pattern
        or cfg.get("pattern")
        or cfg.get("default_pattern")
    )

    if pattern == "multi-phase" or phases_config:
        logger.info("Multi-phase pattern detected - using reliable generation orchestrator")
        return await run_multi_phase_orchestrator(
            args=args,
            cfg=cfg,
            state_dir=state_dir,
            global_dir=global_dir,
            run_dir=run_dir,
            agents=agents,
            phases_config=phases_config or {},
        )

    # Dynamic routing: select experts based on goal if enabled
    personas_cfg = cfg.get("personas", {})
    routing_enabled = cfg.get("routing", False)

    if routing_enabled and not ROUTER_AVAILABLE:
        # FAIL LOUDLY: routing was requested but router module couldn't be imported
        error_msg = f"Routing enabled but router module failed to import: {ROUTER_IMPORT_ERROR}"
        logger.error(error_msg)
        write_live(f"ERROR: {error_msg}")
        write_live(f"Router should be at: {SCRIPT_DIR / 'router.py'}")
        return EXIT_ERROR

    # Expert assignment configuration
    expert_assignment = cfg.get("expert_assignment", None)  # None, "single_agent", or "matrix"
    expert_agent_name = cfg.get("expert_agent", "codex")  # Used for single_agent mode
    max_experts_cfg = cfg.get("max_experts", None)  # None = no limit

    # Multi-expert task list: [(agent_name, persona_name, persona_body), ...]
    # Only populated when expert_assignment is configured
    multi_expert_tasks: List[Tuple[str, str, str]] = []

    if routing_enabled and ROUTER_AVAILABLE:
        logger.info("Dynamic routing enabled - selecting experts for goal...")
        write_live("=" * 40)
        write_live("ROUTING: Selecting experts for goal...")
        write_live("=" * 40)

        # Load expert definitions
        experts_dir = global_dir / "experts" if global_dir else DEFAULT_CONFIG_DIR / "experts"
        expert_pool = load_experts(experts_dir)

        if not expert_pool:
            # FAIL LOUDLY: routing requires experts but none were found
            error_msg = f"Routing enabled but no experts found in: {experts_dir}"
            logger.error(error_msg)
            write_live(f"ERROR: {error_msg}")
            write_live("Expected .yaml files defining expert personas")
            return EXIT_ERROR

        # Run router to select ALL relevant experts (with optional cap)
        routing_result = select_experts(
            goal=goal,
            context=context,
            expert_pool=expert_pool,
            mode=mode_name,
            max_experts=max_experts_cfg,
        )

        # Check if routing succeeded - fail loudly if not
        if not routing_result.success:
            error_msg = f"Router failed: {routing_result.error}"
            logger.error(error_msg)
            write_live(f"ERROR: {error_msg}")
            return EXIT_ERROR

        # Save routing decision for auditability
        save_routing_result(routing_result, run_dir)

        selected_experts = routing_result.selected
        logger.info(f"Router selected: {selected_experts} (confidence: {routing_result.confidence})")
        write_live(f"Selected experts ({len(selected_experts)}): {', '.join(selected_experts)}")
        write_live(f"Confidence: {routing_result.confidence}")
        write_live(f"Reasoning: {routing_result.reasoning[:200]}...")

        # Handle expert assignment strategy
        if expert_assignment == "single_agent":
            # All experts go to one agent (e.g., codex)
            write_live(f"Assignment: single_agent (all to {expert_agent_name})")
            for persona_name in selected_experts:
                try:
                    _, persona_body = load_persona(state_dir, persona_name, global_dir)
                    multi_expert_tasks.append((expert_agent_name, persona_name, persona_body))
                except ValueError as e:
                    error_msg = f"Persona '{persona_name}' not found"
                    logger.error(error_msg)
                    write_live(f"ERROR: {error_msg}")
                    return EXIT_ERROR

        elif expert_assignment == "matrix":
            # Full matrix: each expert × each agent
            write_live(f"Assignment: matrix ({len(selected_experts)} experts × {len(order)} agents)")
            for persona_name in selected_experts:
                try:
                    _, persona_body = load_persona(state_dir, persona_name, global_dir)
                    for agent_name in order:
                        multi_expert_tasks.append((agent_name, persona_name, persona_body))
                except ValueError as e:
                    error_msg = f"Persona '{persona_name}' not found"
                    logger.error(error_msg)
                    write_live(f"ERROR: {error_msg}")
                    return EXIT_ERROR

        else:
            # Legacy mode: map 1:1 to agents (for backwards compatibility)
            if len(selected_experts) < len(order):
                error_msg = (
                    f"Router selected {len(selected_experts)} experts "
                    f"but {len(order)} agents need personas (use expert_assignment config for N experts)"
                )
                logger.error(error_msg)
                write_live(f"ERROR: {error_msg}")
                return EXIT_ERROR

            for i, agent_name in enumerate(order):
                if i < len(selected_experts):
                    personas_cfg[agent_name] = selected_experts[i]

        write_live("=" * 40)

    # Skip persona validation when using multi-expert tasks (personas already loaded)
    agent_personas: Dict[str, str] = {}
    if not multi_expert_tasks:
        # Validate persona configuration: either routing populated personas or they were explicitly set
        # This prevents silent fallback to a non-existent "default" persona
        missing_personas = [agent for agent in order if agent not in personas_cfg]
        if missing_personas and not routing_enabled:
            error_msg = (
                f"No personas configured for agents: {missing_personas}. "
                f"Either enable routing (routing: true) or specify personas explicitly in profile."
            )
            logger.error(error_msg)
            write_live(f"ERROR: {error_msg}")
            return EXIT_ERROR

        # Load personas per agent (checks local first, then global)
        # Note: personas_cfg was populated by routing above if enabled
        for agent_name in order:
            persona_name = personas_cfg.get(agent_name)
            if not persona_name:
                # This shouldn't happen if validation above passed, but be defensive
                error_msg = f"No persona configured for agent '{agent_name}'"
                logger.error(error_msg)
                write_live(f"ERROR: {error_msg}")
                return EXIT_ERROR
            try:
                _, persona_body = load_persona(state_dir, persona_name, global_dir)
                agent_personas[agent_name] = persona_body
            except ValueError as e:
                # Enhanced error: include where we looked for the persona
                error_msg = f"Persona '{persona_name}' not found for agent '{agent_name}'"
                logger.error(error_msg)
                write_live(f"ERROR: {error_msg}")
                write_live(f"Searched: {state_dir / 'personas'}, {global_dir / 'personas' if global_dir else 'N/A'}")
                return EXIT_ERROR

    # Pattern priority: CLI > mode > config > "sequential"
    pattern = (
        args.pattern
        or mode_meta.get("default_pattern")
        or cfg.get("default_pattern")
        or "sequential"
    )

    # Research configuration
    enable_research = cfg.get("enable_research", False)
    research_agent_name = cfg.get("research_agent", "gemini")
    research_agent_cmd = agents.get(research_agent_name, agents.get("gemini", Agent("gemini", "gemini", ["gemini"]))).cmd

    start_turn = state.get("turn", 0)
    max_turns = start_turn + args.turns
    cycle_length = len(order)

    # Multi-expert execution path: runs all expert tasks in parallel (single round)
    if multi_expert_tasks:
        write_live("=" * 40)
        write_live(f"MULTI-EXPERT REVIEW ({len(multi_expert_tasks)} tasks)")
        write_live("=" * 40)

        turn_dir = run_dir / "turns" / "turn_0001"
        turn_dir.mkdir(parents=True, exist_ok=True)

        thread_tail = tail_thread(thread_path)
        tasks = []
        task_info: List[Tuple[str, str]] = []  # [(agent_name, persona_name), ...]

        for agent_name, persona_name, persona_body in multi_expert_tasks:
            agent = agents[agent_name]
            prompt = build_prompt(
                agent_name=agent_name,
                mode=mode_name,
                mode_body=mode_body,
                persona_body=persona_body,
                pattern="parallel",
                turn_idx=1,
                max_turns=1,
                goal=goal,
                context=context,
                summary=summary,
                thread_tail=thread_tail,
                hitl_answers=None,
                enable_research=enable_research,
            )
            write_text_atomic(turn_dir / f"prompt_{agent_name}_{persona_name}.txt", prompt)
            tasks.append(run_agent(agent, prompt, stream=not args.no_stream))
            task_info.append((agent_name, persona_name))

        write_live(f"Running {len(tasks)} parallel expert reviews...")
        for agent_name, persona_name in task_info:
            write_live(f"  • {persona_name} ({agent_name})")

        results = await asyncio.gather(*tasks)

        # Collect results
        all_messages = []
        hitl_questions: List[Dict[str, Any]] = []

        for (env, raw_out, raw_err), (agent_name, persona_name) in zip(results, task_info):
            task_id = f"{agent_name}_{persona_name}"
            write_live(f">>> {persona_name} ({agent_name}): status={env.status}")
            all_messages.append(f"**{persona_name}** ({agent_name}): {env.message}")

            write_text_atomic(
                turn_dir / f"out_{task_id}.json",
                json.dumps(env.to_dict(), indent=2),
            )
            if raw_err:
                write_text_atomic(turn_dir / f"stderr_{task_id}.log", raw_err)

            append_jsonl_durable(
                thread_path,
                {
                    "id": sha256(f"{task_id}:{utc_now_iso()}:1"),
                    "ts": utc_now_iso(),
                    "turn": 1,
                    "agent": agent_name,
                    "persona": persona_name,
                    "role": "assistant",
                    "status": env.status,
                    "content": env.message,
                    "questions": env.questions,
                    "artifacts": [a for a in env.artifacts],
                    "confidence": env.confidence,
                    "agrees_with": env.agrees_with,
                },
            )

            if env.status == "needs_human" and env.questions:
                hitl_questions.append(
                    {"agent": agent_name, "persona": persona_name, "questions": env.questions}
                )

        # Check for HITL
        if hitl_questions:
            write_hitl_questions(run_dir, hitl_questions, 1)
            state["awaiting_human"] = True
            save_json_atomic(state_path, state)
            write_live(f"\nHITL: {len(hitl_questions)} experts need human input")
            write_agent_result(run_dir, "needs_human", EXIT_HITL, questions=hitl_questions)
            return EXIT_HITL

        # Write combined summary
        combined_summary = "\n\n---\n\n".join(all_messages)
        final_dir = run_dir / "final"
        final_dir.mkdir(parents=True, exist_ok=True)
        write_text_atomic(final_dir / "expert_reviews.md", combined_summary)

        write_live("=" * 40)
        write_live(f"Multi-expert review complete: {len(results)} reviews collected")
        write_live(f"Output: {final_dir / 'expert_reviews.md'}")
        write_live("=" * 40)

        write_resolution(run_dir, "multi_expert_complete", 1, f"Completed {len(results)} expert reviews")
        write_agent_result(run_dir, "done", EXIT_OK, summary=f"Multi-expert review complete ({len(results)} reviews)")
        return EXIT_OK

    for turn in range(start_turn, max_turns):
        state["turn"] = turn
        save_json_atomic(state_path, state)

        turn_dir = run_dir / "turns" / f"turn_{turn + 1:04d}"
        turn_dir.mkdir(parents=True, exist_ok=True)

        thread_tail = tail_thread(thread_path)

        # Calculate current cycle for done tracking
        current_cycle = turn // cycle_length

        if pattern == "sequential":
            # Sequential: one agent per turn
            agent_name = order[turn % cycle_length]
            agent = agents[agent_name]

            prompt = build_prompt(
                agent_name=agent_name,
                mode=mode_name,
                mode_body=mode_body,
                persona_body=agent_personas.get(agent_name, ""),
                pattern=pattern,
                turn_idx=turn + 1,
                max_turns=max_turns,
                goal=goal,
                context=context,
                summary=summary,
                thread_tail=thread_tail,
                hitl_answers=hitl_answers,
                enable_research=enable_research,
            )
            hitl_answers = None  # Clear after first use

            write_text_atomic(turn_dir / f"prompt_{agent_name}.txt", prompt)

            logger.info(f"Turn {turn + 1}: Running {agent_name}...")
            write_live("-" * 40)
            write_live(f"TURN {turn + 1}: {agent_name}")
            write_live("-" * 40)

            env, raw_out, raw_err = await run_agent(
                agent, prompt, stream=not args.no_stream
            )

            write_live(f">>> {agent_name} finished: status={env.status}")

            # Display any questions from the agent (informational, not HITL-blocking)
            if env.questions and env.status != "needs_human":
                write_live(f"\n[{agent_name}] included questions:")
                for i, q in enumerate(env.questions, 1):
                    if isinstance(q, dict):
                        q_text = q.get("question", q.get("text", str(q)))
                    else:
                        q_text = str(q)
                    write_live(f"  - {q_text}")

            write_text_atomic(
                turn_dir / f"out_{agent_name}.json", json.dumps(env.to_dict(), indent=2)
            )
            if raw_err:
                write_text_atomic(turn_dir / f"stderr_{agent_name}.log", raw_err)

            # Validate artifacts (relative to project, not run_dir)
            warnings = validate_artifacts(env, Path.cwd())
            if warnings:
                env.message += f"\n[Warnings: {'; '.join(warnings)}]"

            # Append to thread
            append_jsonl_durable(
                thread_path,
                {
                    "id": sha256(f"{agent_name}:{utc_now_iso()}:{turn}"),
                    "ts": utc_now_iso(),
                    "turn": turn + 1,
                    "agent": agent_name,
                    "role": "assistant",
                    "status": env.status,
                    "content": env.message,
                    "questions": env.questions,
                    "artifacts": [a for a in env.artifacts],
                    "confidence": env.confidence,
                },
            )

            # Handle HITL
            if env.status == "needs_human" and env.questions:
                write_hitl_questions(
                    run_dir,
                    [{"agent": agent_name, "questions": env.questions}],
                    turn + 1,
                )
                state["awaiting_human"] = True
                save_json_atomic(state_path, state)
                logger.info(f"HITL requested by {agent_name}")
                write_agent_result(
                    run_dir, "needs_human", EXIT_HITL,
                    questions=[{"agent": agent_name, "questions": env.questions}]
                )
                return EXIT_HITL

            # Handle research requests
            if env.status == "needs_research" and env.research_topics and enable_research:
                logger.info(f"Research requested by {agent_name}: {env.research_topics}")
                research_results = await run_research(
                    topics=env.research_topics,
                    research_agent_cmd=research_agent_cmd,
                    goal=goal,
                    stream=not args.no_stream,
                )
                # Append research results to thread
                append_jsonl_durable(
                    thread_path,
                    {
                        "id": sha256(f"researcher:{utc_now_iso()}:{turn}"),
                        "ts": utc_now_iso(),
                        "turn": turn + 1,
                        "agent": "researcher",
                        "role": "system",
                        "status": "ok",
                        "content": f"RESEARCH RESULTS for: {', '.join(env.research_topics)}\n\n{research_results}",
                        "research_topics": env.research_topics,
                    },
                )
                write_live(f">>> Research complete, {len(research_results)} chars")
                # Don't count this as a turn completion - the agent will continue next turn

            # Track done status per-cycle (not per-turn)
            if state.get("done_cycle") != current_cycle:
                # New cycle: reset done tracking
                state["done_agents"] = []
                state["done_cycle"] = current_cycle

            if env.status == "done":
                done_agents = set(state.get("done_agents", []))
                done_agents.add(agent_name)
                state["done_agents"] = list(done_agents)

                # Check if all agents have said done in this cycle
                if done_agents >= set(order):
                    write_resolution(run_dir, "all_done", turn + 1, env.message)
                    logger.info("All agents reported done. Stopping.")
                    write_agent_result(
                        run_dir, "done", EXIT_OK,
                        summary="All agents reported done"
                    )
                    return EXIT_OK

        else:  # parallel
            # Parallel: all agents run concurrently
            tasks = []
            prompts: Dict[str, str] = {}

            for agent_name in order:
                agent = agents[agent_name]
                prompt = build_prompt(
                    agent_name=agent_name,
                    mode=mode_name,
                    mode_body=mode_body,
                    persona_body=agent_personas.get(agent_name, ""),
                    pattern=pattern,
                    turn_idx=turn + 1,
                    max_turns=max_turns,
                    goal=goal,
                    context=context,
                    summary=summary,
                    thread_tail=thread_tail,
                    hitl_answers=hitl_answers,
                    enable_research=enable_research,
                )
                prompts[agent_name] = prompt
                write_text_atomic(turn_dir / f"prompt_{agent_name}.txt", prompt)
                tasks.append(run_agent(agent, prompt, stream=not args.no_stream))

            hitl_answers = None

            logger.info(f"Turn {turn + 1}: Running all agents in parallel...")
            write_live("-" * 40)
            write_live(f"TURN {turn + 1}: PARALLEL ({', '.join(order)})")
            write_live("-" * 40)

            results = await asyncio.gather(*tasks)

            envelopes: Dict[str, Envelope] = {}
            hitl_questions: List[Dict[str, Any]] = []

            for agent_name, (env, raw_out, raw_err) in zip(order, results):
                write_live(f">>> {agent_name} finished: status={env.status}")
                envelopes[agent_name] = env

                # Display any questions from the agent (informational, not HITL-blocking)
                if env.questions and env.status != "needs_human":
                    write_live(f"\n[{agent_name}] included questions:")
                    for i, q in enumerate(env.questions, 1):
                        if isinstance(q, dict):
                            q_text = q.get("question", q.get("text", str(q)))
                        else:
                            q_text = str(q)
                        write_live(f"  - {q_text}")

                write_text_atomic(
                    turn_dir / f"out_{agent_name}.json",
                    json.dumps(env.to_dict(), indent=2),
                )
                if raw_err:
                    write_text_atomic(turn_dir / f"stderr_{agent_name}.log", raw_err)

                # Validate artifacts (relative to project)
                warnings = validate_artifacts(env, Path.cwd())
                if warnings:
                    env.message += f"\n[Warnings: {'; '.join(warnings)}]"

                append_jsonl_durable(
                    thread_path,
                    {
                        "id": sha256(f"{agent_name}:{utc_now_iso()}:{turn}"),
                        "ts": utc_now_iso(),
                        "turn": turn + 1,
                        "agent": agent_name,
                        "role": "assistant",
                        "status": env.status,
                        "content": env.message,
                        "questions": env.questions,
                        "artifacts": [a for a in env.artifacts],
                        "confidence": env.confidence,
                        "agrees_with": env.agrees_with,
                    },
                )

                if env.status == "needs_human" and env.questions:
                    hitl_questions.append(
                        {"agent": agent_name, "questions": env.questions}
                    )

            # Handle HITL (collected from all agents)
            if hitl_questions:
                write_hitl_questions(run_dir, hitl_questions, turn + 1)
                state["awaiting_human"] = True
                save_json_atomic(state_path, state)
                logger.info(f"HITL requested by {len(hitl_questions)} agent(s)")
                write_agent_result(
                    run_dir, "needs_human", EXIT_HITL,
                    questions=hitl_questions
                )
                return EXIT_HITL

            # Check consensus
            if args.stop_on_consensus and check_consensus(envelopes):
                write_resolution(
                    run_dir, "consensus", turn + 1, "Agents reached consensus"
                )
                logger.info("Consensus detected. Stopping.")
                write_agent_result(
                    run_dir, "done", EXIT_OK,
                    summary="Agents reached consensus"
                )
                return EXIT_OK

            # Check if all done
            if all(e.status == "done" for e in envelopes.values()):
                write_resolution(
                    run_dir, "all_done", turn + 1, "All agents reported done"
                )
                logger.info("All agents reported done. Stopping.")
                write_agent_result(
                    run_dir, "done", EXIT_OK,
                    summary="All agents reported done"
                )
                return EXIT_OK

            # Add round summary to thread
            summary_lines = [
                f"- {a}: {e.status} (conf={e.confidence})" for a, e in envelopes.items()
            ]
            append_jsonl_durable(
                thread_path,
                {
                    "id": sha256(f"moderator:{utc_now_iso()}:{turn}"),
                    "ts": utc_now_iso(),
                    "turn": turn + 1,
                    "agent": "moderator",
                    "role": "system",
                    "status": "ok",
                    "content": "ROUND SUMMARY\n" + "\n".join(summary_lines),
                },
            )

        # Check stagnation (after turn 2+)
        if turn >= 2 and args.stop_on_stagnation:
            thread_tail = tail_thread(thread_path, n=cycle_length * 3)
            if detect_stagnation(thread_tail, order):
                write_resolution(
                    run_dir, "stagnation", turn + 1, "No significant progress detected"
                )
                logger.info("Stagnation detected. Stopping.")
                write_agent_result(
                    run_dir, "done", EXIT_OK,
                    summary="Stagnation detected - no significant progress"
                )
                return EXIT_OK

    # Max turns reached
    write_resolution(run_dir, "max_turns", max_turns, "Reached maximum turn limit")
    logger.info(f"Reached max turns ({max_turns}).")
    write_agent_result(
        run_dir, "done", EXIT_MAX_TURNS,
        summary="Reached maximum turn limit"
    )
    return EXIT_MAX_TURNS


def main() -> int:
    ap = argparse.ArgumentParser(description="Agent Arena Orchestrator")
    ap.add_argument("--config", default="arena.config.json", help="Config file path")
    ap.add_argument(
        "--name", "-n",
        help="Run name (creates .arena/runs/<name>/). If not set, uses timestamp."
    )
    ap.add_argument(
        "--profile", "-p",
        help="Load profile (e.g., security-audit, code-review, brainstorm)"
    )
    ap.add_argument("--mode", help="Override mode (e.g., adversarial, collaborative)")
    ap.add_argument(
        "--pattern", choices=["sequential", "parallel", "multi-phase"], help="Override pattern"
    )
    ap.add_argument("--turns", type=int, default=None, help="Number of turns to run")
    ap.add_argument(
        "--max-iterations", type=int, default=None,
        help="Max iterations for multi-phase pattern (default: 3)"
    )
    ap.add_argument(
        "--dry-run", action="store_true",
        help="Preview constraint routing without executing (multi-phase only)"
    )
    ap.add_argument(
        "--allow-scripts", action="store_true",
        help="Allow script execution in source blocks (security: disabled by default)"
    )
    ap.add_argument(
        "--template-info", action="store_true",
        help="Show reliable-generation template documentation and exit"
    )
    ap.add_argument(
        "--stop-on-consensus", action="store_true", help="Stop on 2-of-3 consensus"
    )
    ap.add_argument(
        "--stop-on-stagnation", action="store_true", help="Stop if no progress"
    )
    ap.add_argument("--verbose", "-v", action="store_true", help="Enable debug logging")
    ap.add_argument(
        "--no-stream", action="store_true", help="Disable streaming output"
    )
    args = ap.parse_args()

    if args.verbose:
        logger.setLevel(logging.DEBUG)

    # Handle --template-info flag
    if args.template_info:
        template_readme = DEFAULT_CONFIG_DIR.parent / "templates" / "reliable-generation" / "README.md"
        if template_readme.exists():
            print(template_readme.read_text(encoding="utf-8"))
        else:
            print(f"Template README not found at: {template_readme}")
            print("\nExpected location: arena-plugin/templates/reliable-generation/README.md")
        return EXIT_OK

    return asyncio.run(run_orchestrator(args))


if __name__ == "__main__":
    raise SystemExit(main())
