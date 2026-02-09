---
description: Configure arena settings for this project
argument-hint: [plan-review on|off|quick|multi-expert]
allowed-tools: Bash, Read, Write, AskUserQuestion
---

# Arena Configure

Configure arena plugin settings for the current project. Settings are stored in `.claude/arena.local.md`.

## Usage

```
/arena:configure plan-review on              # Enable with ask mode (prompts each time)
/arena:configure plan-review off             # Disable plan review
/arena:configure plan-review quick           # Enable with auto Codex+Gemini review
/arena:configure plan-review multi-expert    # Enable with auto arena multi-expert
/arena:configure                             # Interactive configuration
```

## How Plan Review Works

When enabled, ExitPlanMode is **gated** by a PreToolUse hook:

1. Claude drafts a plan and tries to call ExitPlanMode
2. The hook **blocks** ExitPlanMode and injects review instructions
3. Claude runs the configured review (Codex+Gemini, arena, or asks user)
4. Claude iterates on the plan based on feedback
5. Claude creates a marker file and calls ExitPlanMode again
6. The hook sees the marker and **allows** ExitPlanMode through
7. User sees the final, reviewed plan with "Clear context and begin"

## Workflow

### 1. Parse Arguments

Extract from `$ARGUMENTS`:
- `plan-review on` → enable with `ask` mode
- `plan-review off` → disable
- `plan-review quick` → enable with `quick` mode
- `plan-review multi-expert` → enable with `multi-expert` mode
- Empty → interactive mode

### 2. Direct Mode

1. Create `.claude/` directory if needed:
   ```bash
   mkdir -p .claude
   ```

2. If `.claude/arena.local.md` exists, read it and update the frontmatter values.

3. If it doesn't exist, create it with the appropriate settings:
   ```markdown
   ---
   plan-review: true
   plan-review-type: ask
   ---

   # Arena Local Configuration

   Project-specific arena settings. Edit the frontmatter above to change settings.

   ## Available Settings

   | Setting | Values | Description |
   |---------|--------|-------------|
   | plan-review | true/false | Enable plan review gate on ExitPlanMode |
   | plan-review-type | ask/quick/multi-expert | `ask` = prompt each time, `quick` = auto Codex+Gemini, `multi-expert` = auto arena |
   ```

4. Report the change:
   - `on`: "Plan review enabled (ask mode). You'll choose skip/quick/multi-expert each time."
   - `off`: "Plan review disabled. Plans proceed to implementation without review."
   - `quick`: "Plan review enabled (quick mode). Codex + Gemini will auto-review every plan."
   - `multi-expert`: "Plan review enabled (multi-expert mode). Arena will auto-review every plan."

### 3. Interactive Mode (no arguments)

Use AskUserQuestion to present available settings:

```
AskUserQuestion([
  {
    "question": "Which arena feature would you like to configure?",
    "header": "Feature",
    "options": [
      {"label": "Plan Review", "description": "Gate ExitPlanMode with Codex/Gemini/arena review"},
      {"label": "Show current config", "description": "Display current arena settings for this project"}
    ],
    "multiSelect": false
  }
])
```

**If "Plan Review":**

```
AskUserQuestion([
  {
    "question": "How should plan review work?",
    "header": "Review Mode",
    "options": [
      {"label": "Ask each time (Recommended)", "description": "Prompt for skip/quick/multi-expert on each plan"},
      {"label": "Auto quick review", "description": "Always run Codex + Gemini review (~1-2 min)"},
      {"label": "Auto multi-expert", "description": "Always run full arena review (~3-5 min)"},
      {"label": "Disable", "description": "No review gate on plans"}
    ],
    "multiSelect": false
  }
])
```

Then create/update `.claude/arena.local.md` with the appropriate `plan-review` and `plan-review-type` values.

**If "Show current config":**

Read `.claude/arena.local.md` and display current settings in a formatted table. If the file doesn't exist, report that no project-specific settings are configured.
