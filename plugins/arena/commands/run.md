---
description: Run multi-agent orchestration in background
argument-hint: <goal> [-p profile]
allowed-tools: Bash, Read, Write, AskUserQuestion
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

### 2. Generate Run Name and Setup

1. Generate run name from goal (kebab-case, max 30 chars)
2. Create run directory:
   ```bash
   mkdir -p .arena/runs/<run-name>
   ```
3. Write goal.yaml:
   ```yaml
   goal: |
     <goal from prompt>
   ```

### 3. Launch Orchestrator

Run arena.py directly via Bash (in background):

```bash
uv run --project ${CLAUDE_PLUGIN_ROOT} python3 ${CLAUDE_PLUGIN_ROOT}/scripts/arena.py \
  --config ${CLAUDE_PLUGIN_ROOT}/config/arena.config.json \
  --name <run-name> \
  -p <profile> &
```

**Note:** Use `&` to run in background, or use Bash tool's `run_in_background=true` parameter.

### 4. Notify User

Tell the user:

> Started arena orchestration in background. You can keep working - I'll notify you when input is needed or when it completes.
>
> To check progress: `tail -f .arena/live.log`

## What Happens Next

The plugin's **SubagentStop hook** (`hooks/hooks.json`) automatically:
- **Detects completion** by reading `.arena/runs/latest/agent-result.json`
- **Shows HITL questions** if agents need human input (exit code 10)
- **Summarizes results** when orchestration completes
- **Reports errors** with actionable recovery steps

The hook runs `scripts/hitl-handler.sh` which parses the result and outputs status to Claude.

## Manual Check (Optional)

If needed, you can manually check status:
- Read `.arena/runs/latest/agent-result.json` for current state
- Read `.arena/runs/latest/state.json` for turn count
- Use `/arena:status` for formatted view
