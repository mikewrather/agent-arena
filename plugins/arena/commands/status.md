---
description: Check status of arena runs
argument-hint: [run-name]
allowed-tools: Bash, Read
---

# Arena Status

Check the status of arena orchestration runs.

## Usage

```
/arena:status              # Show latest run status
/arena:status auth-review  # Check specific run
/arena:status --all        # List all runs
```

## Workflow

### Show Latest Run (default)

Read `.arena/runs/latest/` symlink target and show detailed status for that run.

### If `--all` provided:

List all runs with their status:

```bash
for dir in .arena/runs/*/; do
  [ -L "$dir" ] && continue  # Skip symlinks
  name=$(basename "$dir")
  if [ -f "$dir/agent-result.json" ]; then
    status=$(cat "$dir/agent-result.json" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status','unknown'))")
    echo "$name: $status"
  elif [ -f "$dir/resolution.json" ]; then
    reason=$(cat "$dir/resolution.json" | python3 -c "import sys,json; print(json.load(sys.stdin).get('reason','unknown'))")
    echo "$name: completed ($reason)"
  elif [ -f "$dir/state.json" ]; then
    awaiting=$(cat "$dir/state.json" | python3 -c "import sys,json; print(json.load(sys.stdin).get('awaiting_human',False))")
    if [ "$awaiting" = "True" ]; then
      echo "$name: awaiting human input"
    else
      echo "$name: in progress"
    fi
  else
    echo "$name: initialized"
  fi
done
```

### If specific run name provided:

Show detailed status:

1. Read `.arena/runs/<run-name>/agent-result.json` if exists (most recent agent outcome)
2. Read `.arena/runs/<run-name>/state.json` for current state
3. Read `.arena/runs/<run-name>/resolution.json` if exists
4. Count turns from `.arena/runs/<run-name>/thread.jsonl`

Report:
- Run name
- Current turn
- Status (in_progress, awaiting_human, done, needs_human, error)
- Questions pending (if needs_human)
- Summary (if done)
- Resolution reason (if completed)
- Last agent responses (abbreviated)
