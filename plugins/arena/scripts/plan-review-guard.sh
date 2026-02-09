#!/bin/bash
# Plan Review Gate - PreToolUse hook for ExitPlanMode
#
# BLOCKS ExitPlanMode until the plan has been reviewed.
# Uses a marker file (.claude/.plan-reviewed) to track review completion.
#
# Flow:
#   1. Claude drafts plan, calls ExitPlanMode
#   2. This hook BLOCKS it (exit 2) with review instructions
#   3. Claude runs review (Codex+Gemini or arena), iterates on feedback
#   4. Claude creates marker: touch .claude/.plan-reviewed
#   5. Claude calls ExitPlanMode again
#   6. This hook sees marker, cleans it up, allows through (exit 0)
#
# Config in .claude/arena.local.md frontmatter:
#   plan-review: true          # enable/disable
#   plan-review-type: quick    # quick | multi-expert | ask (default: ask)

set -euo pipefail

CONFIG=".claude/arena.local.md"
MARKER=".claude/.plan-reviewed"

# Quick exit if no config file
if [[ ! -f "$CONFIG" ]]; then
    exit 0
fi

# Parse YAML frontmatter
FRONTMATTER=$(sed -n '/^---$/,/^---$/{ /^---$/d; p; }' "$CONFIG")
PLAN_REVIEW=$(echo "$FRONTMATTER" | grep '^plan-review:' | sed 's/plan-review: *//' | tr -d '[:space:]')

if [[ "$PLAN_REVIEW" != "true" ]]; then
    exit 0
fi

# Check if already reviewed this cycle
if [[ -f "$MARKER" ]]; then
    rm -f "$MARKER"
    exit 0
fi

# Read review type preference
REVIEW_TYPE=$(echo "$FRONTMATTER" | grep '^plan-review-type:' | sed 's/plan-review-type: *//' | tr -d '[:space:]')
REVIEW_TYPE=${REVIEW_TYPE:-ask}

# Block ExitPlanMode and inject review instructions
case "$REVIEW_TYPE" in
    quick)
        cat >&2 << 'REVIEW_PROMPT'
=== PLAN REVIEW REQUIRED ===

Plan review is enabled for this project (type: quick).
ExitPlanMode is BLOCKED until the plan is reviewed.

Do the following:

1. Read the plan file from .claude/plans/ (find the most recent .md file).

2. Use the Task tool to launch TWO subagents in parallel:
   - codex-default: "Review this implementation plan critically. Identify issues, risks, gaps, missing edge cases, and suggest specific improvements. Here is the plan: [full plan content]"
   - gemini-default: same prompt.

3. When both complete, synthesize their feedback. If there are actionable issues:
   - Update the plan file to address the feedback
   - Summarize what changed

4. Create the review marker:
   touch .claude/.plan-reviewed

5. Call ExitPlanMode again. It will succeed this time.

===============================
REVIEW_PROMPT
        ;;

    multi-expert)
        cat >&2 << 'REVIEW_PROMPT'
=== PLAN REVIEW REQUIRED ===

Plan review is enabled for this project (type: multi-expert).
ExitPlanMode is BLOCKED until the plan is reviewed.

Do the following:

1. Read the plan file from .claude/plans/ (find the most recent .md file).

2. Launch an arena run with the multi-expert profile:
   - Create a run directory and write the plan as the goal
   - Execute: uv run --project ${CLAUDE_PLUGIN_ROOT} python3 ${CLAUDE_PLUGIN_ROOT}/scripts/arena.py --config ${CLAUDE_PLUGIN_ROOT}/config/arena.config.json --name plan-review -p multi-expert
   - Wait for it to complete (monitor .arena/runs/plan-review/agent-result.json)

3. Read the results and incorporate actionable feedback into the plan.

4. Create the review marker:
   touch .claude/.plan-reviewed

5. Call ExitPlanMode again. It will succeed this time.

===============================
REVIEW_PROMPT
        ;;

    ask|*)
        cat >&2 << 'REVIEW_PROMPT'
=== PLAN REVIEW REQUIRED ===

Plan review is enabled for this project.
ExitPlanMode is BLOCKED until the plan is reviewed.

First, use AskUserQuestion to ask:
  Question: "How would you like to review this plan?"
  Header: "Plan Review"
  Options:
  1. "Skip review" - "Proceed without external review"
  2. "Quick review (Codex + Gemini)" - "Two AI perspectives in parallel (~1-2 min)"
  3. "Multi-expert arena review" - "Full arena orchestration with expert panel (~3-5 min)"

Based on their choice:

- **Skip**: Create the marker (touch .claude/.plan-reviewed) and call ExitPlanMode again.

- **Quick review**:
  1. Read the most recent plan file from .claude/plans/.
  2. Launch TWO Task subagents in parallel:
     - codex-default: "Review this implementation plan critically. Identify issues, risks, gaps, missing edge cases, and suggest specific improvements. Here is the plan: [full plan content]"
     - gemini-default: same prompt.
  3. Synthesize feedback. If actionable, update the plan.
  4. Create marker: touch .claude/.plan-reviewed
  5. Call ExitPlanMode again.

- **Multi-expert**:
  1. Read the most recent plan file from .claude/plans/.
  2. Launch arena run with multi-expert profile.
  3. Wait for completion, incorporate feedback.
  4. Create marker: touch .claude/.plan-reviewed
  5. Call ExitPlanMode again.

===============================
REVIEW_PROMPT
        ;;
esac

exit 2
