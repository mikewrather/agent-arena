---
description: Run multi-agent orchestration in background
argument-hint: <goal> [-p profile]
allowed-tools: Task, Read, Write, AskUserQuestion
---

# Arena Run

Launch a background multi-agent orchestration session.

## Usage Examples

```
/arena:run Review auth module -p security-audit
/arena:run Improve API design -p brainstorm
/arena:run Analyze performance -p multi-expert
```

## Profiles

| Profile | Description |
|---------|-------------|
| `security-audit` | Adversarial parallel with security personas |
| `code-review` | Standard sequential code review |
| `brainstorm` | Collaborative parallel with diverse experts |
| `opus-deep` | Deep analysis with 3 Opus 4.5 instances |
| `multi-expert` | Architect + security + performance panel |
| `research-brainstorm` | Sequential brainstorm with web research |
| `reliable-generation` | Constraint-driven generation with critique loop |

**Note:** For `reliable-generation`, use `/arena:genloop` instead - it handles the constraint setup workflow.

## Workflow

### 1. Parse Arguments

Extract from `$ARGUMENTS`:
- Goal text (required)
- `-p` or `--profile`: profile name (default: `code-review`)

### 2. Launch Background Agent

Use the Task tool to launch arena in background:

```
Task(
  subagent_type="arena",
  prompt="goal: <user's goal>\nprofile: <profile>",
  run_in_background=true,
  description="Running arena orchestration"
)
```

### 3. Notify User

Tell the user:

> Started arena orchestration in background. You can keep working - I'll notify you when input is needed or when it completes.
>
> To check progress: `tail -f .arena/live.log`

## What Happens Next

The SubagentStop hook automatically handles:
- **Waking you** when the agent completes
- **Prompting for HITL answers** if agents need human input
- **Re-launching the agent** to continue after you answer
- **Presenting final results** when orchestration completes

You don't need to poll or check manually - the hook system takes care of the HITL loop.

## Manual Check (Optional)

If needed, you can manually check status:
- Read `.arena/runs/latest/agent-result.json` for current state
- Read `.arena/runs/latest/state.json` for turn count
- Use `/arena:status` for formatted view
