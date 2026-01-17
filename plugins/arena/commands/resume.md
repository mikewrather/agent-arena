---
description: Manually resume an arena run (usually handled automatically by hooks)
argument-hint: <run-name>
allowed-tools: Task, Read, Write, AskUserQuestion
---

# Arena Resume

Manually resume an orchestration run. Note: This is usually handled automatically by the SubagentStop hook after you answer HITL questions.

## Usage

```
/arena:resume auth-security-review
```

## When to Use

- If the automatic hook failed for some reason
- If you want to resume an old run manually
- If you edited answers.json directly

## Workflow

### 1. Validate Run Exists

Read `.arena/runs/<run-name>/state.json` and verify the run exists.

### 2. Check for Pending Questions

If `awaiting_human: true`, read questions from `.arena/runs/<run-name>/hitl/questions.json`.

Use AskUserQuestion to collect answers (max 4 questions per call).

### 3. Launch Resume Agent

Use the Task tool to launch arena in background with the run_name and answers:

```
Task(
  subagent_type="arena",
  prompt="run_name: <run-name>\nanswers: <collected answers as JSON>",
  run_in_background=true,
  description="Resuming arena orchestration"
)
```

### 4. Notify User

Tell the user:

> Resumed arena orchestration for `<run-name>`. The hook will notify you when it completes or needs more input.
