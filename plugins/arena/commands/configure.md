---
description: Configure arena settings for this project
argument-hint: [plan-review on|off]
allowed-tools: Bash, Read, Write, AskUserQuestion
---

# Arena Configure

Configure arena plugin settings for the current project. Settings are stored in `.claude/arena.local.md`.

## Usage

```
/arena:configure plan-review on    # Enable plan review on ExitPlanMode
/arena:configure plan-review off   # Disable plan review
/arena:configure                   # Interactive configuration
```

## Workflow

### 1. Parse Arguments

Extract from `$ARGUMENTS`:
- `plan-review on` → enable plan review
- `plan-review off` → disable plan review
- Empty → interactive mode

### 2. Direct Mode (plan-review on|off)

1. Create `.claude/` directory if needed:
   ```bash
   mkdir -p .claude
   ```

2. If `.claude/arena.local.md` exists, read it and update the `plan-review` value in the frontmatter.

3. If it doesn't exist, create it:
   ```markdown
   ---
   plan-review: true
   ---

   # Arena Local Configuration

   Project-specific arena settings. Edit the frontmatter above to change settings.

   ## Available Settings

   | Setting | Values | Description |
   |---------|--------|-------------|
   | plan-review | true/false | Present review options (skip/quick/multi-expert) when plans are finalized via ExitPlanMode |
   ```

4. Report the change:
   - If enabled: "Plan review enabled. When you finalize a plan, you'll be asked to choose a review approach (skip, quick Codex+Gemini, or multi-expert arena)."
   - If disabled: "Plan review disabled. Plans will proceed to implementation without review prompts."

### 3. Interactive Mode (no arguments)

Use AskUserQuestion to present available settings:

```
AskUserQuestion([
  {
    "question": "Which arena feature would you like to configure?",
    "header": "Feature",
    "options": [
      {"label": "Plan Review", "description": "Get Codex/Gemini/multi-expert review when plans are finalized"},
      {"label": "Show current config", "description": "Display current arena settings for this project"}
    ],
    "multiSelect": false
  }
])
```

**If "Plan Review":**

Check current state from `.claude/arena.local.md` (if exists), then:

```
AskUserQuestion([
  {
    "question": "Enable automatic plan review when exiting plan mode?",
    "header": "Plan Review",
    "options": [
      {"label": "Enable (Recommended)", "description": "Present review options (skip/quick/multi-expert) when plans are finalized"},
      {"label": "Disable", "description": "No review prompt on plan finalization"}
    ],
    "multiSelect": false
  }
])
```

Then create/update `.claude/arena.local.md` as described in step 2 above.

**If "Show current config":**

Read `.claude/arena.local.md` and display current settings in a formatted table. If the file doesn't exist, report that no project-specific settings are configured.
