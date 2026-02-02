# GenFlow: Custom Workflow Engine

A flexible, configurable workflow engine for multi-phase content generation with per-constraint behavior control, scoped adjudication, and loop-based iteration. Independent of GenLoop but shares core infrastructure (prompt builders, models, parsers, HITL protocol).

## Overview

GenFlow extends the fixed Generate → Critique → Adjudicate → Refine loop of GenLoop into an arbitrary user-defined workflow. Where GenLoop hard-codes the phase sequence and uses approval policies to control termination, GenFlow lets you define custom step sequences, control what happens at each severity level per-constraint, scope which critiques an adjudicator sees, and loop back to named steps for targeted re-evaluation.

### When to Use GenFlow vs GenLoop

| Scenario | Use |
|----------|-----|
| Standard constraint-driven generation | GenLoop |
| Custom multi-phase workflows | GenFlow |
| Multiple critique passes with different constraint subsets | GenFlow |
| Per-constraint severity behaviors (HALT/ESCALATE/IGNORE) | GenFlow |
| Scoped adjudication (only adjudicate recent critiques) | GenFlow |
| Loop back to a specific step after refinement | GenFlow |
| Per-step model or agent overrides | GenFlow |
| Simple generate-critique-approve loop | GenLoop |

## Architecture

### Workflow Model

A GenFlow workflow is an ordered list of steps. Each step is one of four types:

```
generate → critique → adjudicate → refine
```

Steps execute sequentially. A `refine` step can use `loop_to` to jump back to a named earlier step, creating iteration within the workflow. The outer iteration loop (controlled by `max_iterations`) wraps the entire workflow — if all steps complete without approval, the workflow restarts from step 0.

```
┌──────────────────────────────────────────────────────────────┐
│                     OUTER ITERATION LOOP                      │
│  (iteration 1..max_iterations)                                │
│                                                               │
│  ┌──────────────────────────────────────────────────────────┐ │
│  │               WORKFLOW STEP SEQUENCE                      │ │
│  │                                                           │ │
│  │  step[0] → step[1] → step[2] → ... → step[N]            │ │
│  │                                                           │ │
│  │  Any step can:                                            │ │
│  │    → Return EXIT_OK (approved, workflow done)             │ │
│  │    → Return EXIT_HITL (needs human, pause)                │ │
│  │    → Return EXIT_ERROR (failure, abort)                   │ │
│  │    → Continue to next step                                │ │
│  │    → Loop back (refine with loop_to)                      │ │
│  └──────────────────────────────────────────────────────────┘ │
│                                                               │
│  If all steps complete → increment iteration, restart         │
│  If max_iterations reached → EXIT_MAX_TURNS                   │
└──────────────────────────────────────────────────────────────┘
```

### Shared Infrastructure

GenFlow reuses these modules from the Arena core:

| Module | Purpose |
|--------|---------|
| `models.py` | `Constraint`, `Critique`, `CritiqueIssue`, `Adjudication`, `Agent` |
| `parsers.py` | `parse_critique()`, `parse_adjudication()` |
| `utils.py` | Atomic file I/O, hashing, timestamps, live output |
| `hitl.py` | HITL question/answer protocol |
| `config.py` | Constraint loading and compression |
| `genloop_config.py` | `ConstraintConfig`, `AdjudicationConfig`, `OutputConfig` (reused directly) |
| `arena.py` | Prompt builders (`build_generator_prompt`, `build_critic_prompt`, `build_adjudicator_prompt`, `build_refinement_prompt`), `run_process`, `load_goal` |

### Data Flow

```
                    goal.yaml
                       │
                       ▼
              ┌─────────────────┐
              │   load_goal()   │
              │   load_constraints()
              │   compress_constraints()
              └────────┬────────┘
                       │
                       ▼
              ┌─────────────────┐
              │ WorkflowContext  │ ◄── Mutable state shared across all steps
              └────────┬────────┘
                       │
           ┌───────────┼───────────┐
           ▼           ▼           ▼
       generate    critique    adjudicate/refine
           │           │           │
           ▼           ▼           ▼
       artifact    critiques    adjudication
       (.md)       (.json)      (.yaml)
           │           │           │
           └───────────┼───────────┘
                       ▼
              ┌─────────────────┐
              │   thread.jsonl  │  ◄── Append-only conversation log
              └─────────────────┘
```

## Configuration

GenFlow uses a YAML configuration file with a `workflow` key that defines the step sequence, plus optional behavior and constraint configuration.

### Minimal Configuration

```yaml
max_iterations: 3

workflow:
  - step: generate
  - step: critique
  - step: adjudicate
  - step: refine
```

### Full Configuration

```yaml
max_iterations: 5

# Custom workflow: generate, critique structure, critique style separately,
# adjudicate all critiques, then refine with loop back to structure critique
workflow:
  - step: generate
    name: draft
    agent: claude
    model: opus

  - step: critique
    name: structure_review
    execution: parallel
    constraints: ["structure*", "completeness*"]
    order: priority

  - step: critique
    name: style_review
    execution: serial
    agent: gemini
    constraints: ["style*", "tone*"]

  - step: adjudicate
    name: judge
    agent: claude
    model: opus
    scope: accumulated

  - step: refine
    name: fix
    agent: claude
    mode: edit
    loop_to: structure_review

# Per-severity default behaviors
default_behavior:
  critical: halt
  high: halt
  medium: continue
  low: ignore

# Reused from genloop
constraints:
  dir: .arena/constraints
  routing:
    default_agents: ["claude", "codex", "gemini"]
    rules:
      - match: "security*"
        agents: ["claude", "codex"]

adjudication:
  approval:
    block_on: ["CRITICAL", "HIGH"]
  escalation:
    triggers: ["max_iterations", "thrashing"]

output:
  dir: final
  filename: artifact.md
```

### Per-Constraint Behavior Overrides

Constraints can define their own severity behaviors in their YAML files:

```yaml
# constraints/security.yaml
id: security
priority: 1
summary: No security vulnerabilities
behavior:
  critical: escalate    # Go directly to HITL, skip adjudication
  high: halt            # Stop critique phase, proceed to adjudicate
  medium: continue      # Accumulate, keep reviewing
  low: ignore           # Log but exclude from adjudication
rules:
  - id: no-injection
    text: No SQL injection vulnerabilities
    default_severity: CRITICAL
```

## Step Types

### generate

Produces the artifact. Uses the shared `build_generator_prompt()` which includes the goal, source material, compressed constraints, and (on iteration > 1) the previous artifact and adjudication feedback.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `name` | string | `null` | Step name for `loop_to` references |
| `agent` | string | `"claude"` | Which agent generates |
| `model` | string | `null` | Model override (e.g., `"opus"`, `"o3"`) |

After generation, all critique state is reset (`critiques_by_step` and `unadjudicated_critiques` cleared).

### critique

Reviews the artifact against constraints. Supports two execution modes.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `name` | string | `null` | Step name for `loop_to` references |
| `agent` | string | `"claude"` | Agent for all critiques in this step |
| `model` | string | `null` | Model override |
| `execution` | `"parallel"` \| `"serial"` | `"parallel"` | How to run critiques |
| `order` | `"priority"` \| `"definition"` | `"priority"` | Constraint ordering |
| `constraints` | list[string] | `null` (all) | Glob patterns filtering which constraints to evaluate |

**Parallel execution**: All constraints run concurrently via `asyncio.gather()`. Behaviors (HALT/ESCALATE/CONTINUE/IGNORE) are applied post-hoc after all critiques complete.

**Serial execution**: Constraints run one-by-one. A HALT behavior on any issue stops the critique phase immediately (early exit). Useful when certain constraints are prerequisites for others.

**Constraint filtering**: The `constraints` field accepts glob patterns matched against constraint IDs using `fnmatch`. This allows multiple critique steps to target different constraint subsets:

```yaml
- step: critique
  name: safety_check
  constraints: ["safety*", "legal*"]

- step: critique
  name: quality_check
  constraints: ["style*", "tone*", "completeness*"]
```

### adjudicate

Analyzes critiques and decides whether to approve, continue refining, or escalate.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `name` | string | `null` | Step name |
| `agent` | string | `"claude"` | Adjudicator agent |
| `model` | string | `null` | Model override |
| `scope` | `"accumulated"` \| `"previous"` \| `"all"` | `"accumulated"` | Which critiques to consider |

**Scope behavior**:

| Scope | Critiques considered | Cleared after adjudication |
|-------|---------------------|---------------------------|
| `accumulated` | All critiques not yet adjudicated | Yes (all cleared) |
| `previous` | Only critiques from the immediately preceding critique step | Only the previous step's critiques |
| `all` | Every critique from every step in the current iteration | None |

If the adjudicator returns `status: "APPROVED"`, the workflow exits successfully. The final artifact is saved to `final/artifact.md`.

### refine

Applies fixes from the adjudication's bill of work. Supports two modes.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `name` | string | `null` | Step name |
| `agent` | string | `"claude"` | Refining agent |
| `model` | string | `null` | Model override |
| `mode` | `"edit"` \| `"rewrite"` | `"edit"` | Refinement strategy |
| `loop_to` | string | `null` | Step name to jump back to after refining |

**Edit mode**: Copies the artifact to a working file and gives the agent file-editing permissions (Claude gets `--dangerously-skip-permissions` and `--add-dir` pointing to the run directory). The agent applies surgical edits to the file.

**Rewrite mode**: Sends the full artifact + adjudication feedback back through the generator prompt. The agent produces a complete new version.

**Loop control**: When `loop_to` is set, after refinement the workflow jumps back to the named step. All critique state is cleared for fresh re-evaluation. If the named step doesn't exist, a warning is logged and execution continues to the next step.

## Issue Behavior System

The core differentiator from GenLoop. Each severity level maps to a behavior that controls what happens when an issue of that severity is found.

### Behaviors

| Behavior | Effect |
|----------|--------|
| `halt` | Stop the critique phase. Proceed to the next step (typically adjudicate). The halting issue is included in the critique results. In serial mode, remaining constraints are skipped. In parallel mode, applied post-hoc (all critiques complete, but halt is flagged). |
| `continue` | Accumulate the issue and keep running. Standard behavior — the issue is included in critiques sent to the adjudicator. |
| `escalate` | Skip adjudication entirely. Go directly to HITL with the escalated issues. The orchestrator writes HITL questions and exits with code 10. |
| `ignore` | Log the issue but exclude it from adjudication. The adjudicator never sees ignored issues. Useful for informational findings that shouldn't block approval. |

### Resolution Hierarchy

When determining behavior for an issue, GenFlow checks four levels (highest priority first):

1. **Per-constraint `behavior` field** in the constraint YAML file
2. **Per-constraint entry** in `config.constraint_behaviors`
3. **Config `default_behavior`** block
4. **Built-in defaults**: CRITICAL=halt, HIGH=halt, MEDIUM=continue, LOW=ignore

### Default Behaviors

```yaml
default_behavior:
  critical: halt       # Stop critique, proceed to adjudicate
  high: halt           # Stop critique, proceed to adjudicate
  medium: continue     # Keep going, accumulate issues
  low: ignore          # Log but don't send to adjudicator
```

## State Management

### WorkflowContext

A mutable dataclass passed through all steps. Key fields:

| Field | Type | Description |
|-------|------|-------------|
| `artifact` | `str` | Current artifact text |
| `artifact_path` | `Path` | Path to current artifact file |
| `iteration` | `int` | Current outer iteration (1-based) |
| `current_step_index` | `int` | Current position in workflow steps |
| `critiques_by_step` | `dict[str, list[Critique]]` | Critiques keyed by step name |
| `unadjudicated_critiques` | `list[Critique]` | Critiques not yet adjudicated |
| `last_adjudication` | `Adjudication` | Most recent adjudication result |

### Persistence & Resumption

State is saved to `state.json` after each step and on HITL interrupts:

```json
{
  "awaiting_human": false,
  "iteration": 2,
  "step_index": 3,
  "artifact": "...",
  "critiques_by_step": { "structure_review": [...] },
  "unadjudicated_critiques": [...],
  "last_adjudication": { ... }
}
```

On resume, if `awaiting_human` is true, the orchestrator checks for HITL answers in `hitl/answers.json`. If found, it ingests them and continues from the saved step index. If not found, it exits with code 10 again.

### Exit Codes

| Code | Constant | Meaning |
|------|----------|---------|
| 0 | `EXIT_OK` | Artifact approved |
| 1 | `EXIT_ERROR` | Fatal error |
| 10 | `EXIT_HITL` | HITL needed — human input required |
| 11 | `EXIT_MAX_TURNS` | Max iterations reached without approval |

Internal return values during workflow execution:

| Value | Meaning |
|-------|---------|
| `1` | Continue to next step |
| `-1` | Loop back (refine with `loop_to`) |
| `-2` | Escalate directly to HITL |

## Run Directory Structure

```
.arena/runs/<name>/
├── goal.yaml                     # Generation goal + source definitions
├── state.json                    # Workflow state (iteration, step, artifact)
├── thread.jsonl                  # Append-only conversation log
├── resolution.json               # How the run ended
├── live.log                      # Streaming output
├── hitl/                         # HITL questions/answers
│   ├── questions.json
│   └── answers.json
├── constraints/                  # Constraint YAML files
│   ├── accuracy.yaml
│   └── style.yaml
├── iterations/
│   └── 1/
│       ├── artifact.md           # Generated artifact
│       ├── artifact_refined.md   # Refined artifact (if refine step ran)
│       ├── prompt_generate_claude.txt
│       ├── prompt_refine_claude.txt
│       ├── adjudication_judge.yaml
│       └── critiques/
│           ├── prompt_accuracy_claude.txt
│           ├── accuracy-claude.json
│           ├── style-gemini.json
│           └── ...
└── final/
    └── artifact.md               # Approved output
```

## Example Workflows

### Minimal (equivalent to GenLoop)

```yaml
workflow:
  - step: generate
  - step: critique
  - step: adjudicate
  - step: refine
```

### Safety-First with Escalation

Run safety constraints serially with escalation on critical findings, then quality constraints in parallel:

```yaml
max_iterations: 5

workflow:
  - step: generate
    agent: claude
    model: opus

  - step: critique
    name: safety_gate
    execution: serial
    constraints: ["safety*", "legal*"]

  - step: critique
    name: quality_review
    execution: parallel
    constraints: ["accuracy*", "completeness*", "style*"]

  - step: adjudicate
    scope: accumulated

  - step: refine
    mode: edit
    loop_to: safety_gate

default_behavior:
  critical: escalate   # Safety criticals go straight to human
  high: halt
  medium: continue
  low: ignore
```

### Multi-Model Pipeline

Use different models for different phases:

```yaml
workflow:
  - step: generate
    agent: claude
    model: opus          # High-quality generation

  - step: critique
    agent: gemini        # Fast, cost-effective critique
    execution: parallel

  - step: adjudicate
    agent: claude
    model: opus          # Careful adjudication

  - step: refine
    agent: claude
    mode: edit           # Surgical edits
    model: sonnet        # Sonnet is good at targeted edits
```

### Two-Pass Review

Review structure first, then style, with separate adjudication:

```yaml
workflow:
  - step: generate
    name: draft

  - step: critique
    name: structure_review
    constraints: ["structure*", "accuracy*"]

  - step: adjudicate
    name: structure_judge
    scope: previous      # Only look at structure critiques

  - step: refine
    name: structure_fix
    mode: edit

  - step: critique
    name: style_review
    constraints: ["style*", "tone*"]

  - step: adjudicate
    name: final_judge
    scope: accumulated   # Look at any remaining issues

  - step: refine
    name: style_fix
    mode: edit
    loop_to: style_review
```

## Thread Log Format

Every step appends a JSON line to `thread.jsonl`:

```jsonl
{"id":"sha256(...)","ts":"2025-01-27T...","iteration":1,"phase":"generate","step_name":"draft","agent":"claude","role":"assistant","artifact_path":".../artifact.md"}
{"id":"sha256(...)","ts":"2025-01-27T...","iteration":1,"phase":"critique","step_name":"structure_review","agent":"claude","constraint":"accuracy","issues_count":2,"overall":"FAIL"}
{"id":"sha256(...)","ts":"2025-01-27T...","iteration":1,"phase":"adjudicate","step_name":"judge","agent":"claude","status":"REWRITE","critical_pursuing":0,"high_pursuing":2}
{"id":"sha256(...)","ts":"2025-01-27T...","iteration":1,"phase":"refine","step_name":"fix","agent":"claude","mode":"edit","artifact_path":".../artifact_refined.md"}
```

## Implementation Files

| File | Purpose |
|------|---------|
| `scripts/genflow.py` | Main orchestrator: `run_genflow_orchestrator()`, step executors, state management |
| `scripts/genflow_config.py` | Configuration: `GenflowConfig`, `WorkflowStep`, `IssueBehavior`, `ConstraintBehavior`, validation |

### Key Functions

**genflow.py**:
- `run_genflow_orchestrator()` — Entry point. Loads state, goal, constraints; validates workflow; runs iteration loop.
- `execute_workflow()` — Walks through steps sequentially. Handles exit codes and loop-backs.
- `execute_generate_step()` — Runs generator agent, saves artifact, resets critique state.
- `execute_critique_step()` — Dispatches to parallel or serial execution.
- `execute_critique_parallel()` — Runs all constraints concurrently, applies behaviors post-hoc.
- `execute_critique_serial()` — Runs constraints one-by-one with early exit on HALT.
- `execute_adjudicate_step()` — Scoped critique resolution, checks for APPROVED status.
- `execute_refine_step()` — Edit or rewrite mode, handles `loop_to`.
- `get_critiques_for_scope()` — Resolves which critiques the adjudicator sees based on scope.
- `save_workflow_state()` — Persists context to `state.json`.

**genflow_config.py**:
- `GenflowConfig.from_dict()` — Parses full config from YAML dict.
- `WorkflowStep.from_dict()` — Parses individual step.
- `IssueBehavior` — Enum: HALT, CONTINUE, ESCALATE, IGNORE.
- `ConstraintBehavior` — Per-severity behavior mapping.
- `get_behavior_for_severity()` — Resolves behavior through 4-level hierarchy.
- `resolve_constraints_for_step()` — Filters constraints by glob patterns and sorts by order preference.
- `validate_workflow()` — Checks for duplicate names, valid `loop_to` references, valid enum values, and requires at least one generate step.
