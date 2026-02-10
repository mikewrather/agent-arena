#!/bin/bash
# Plan Review Gate - PreToolUse hook for ExitPlanMode
#
# BLOCKS ExitPlanMode until the plan has been reviewed.
# Uses a marker file (.claude/.plan-reviewed) to track review completion.
#
# Flow:
#   1. Claude drafts plan, calls ExitPlanMode
#   2. This hook reads the plan from stdin JSON (tool_input.plan)
#   3. This hook BLOCKS it (exit 2) with review instructions + plan content
#   4. Claude runs review (Codex+Gemini or arena), iterates on feedback
#   5. Claude creates marker: touch .claude/.plan-reviewed
#   6. Claude calls ExitPlanMode again
#   7. This hook sees marker, cleans it up, allows through (exit 0)
#
# Config in .claude/arena.local.md frontmatter:
#   plan-review: true          # enable/disable
#   plan-review-type: quick    # quick | multi-expert | ask (default: ask)

set -euo pipefail

CONFIG=".claude/arena.local.md"
MARKER=".claude/.plan-reviewed"

# Read stdin (hook JSON payload)
INPUT=$(cat)

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

# Extract plan content from stdin JSON
PLAN_CONTENT=$(echo "$INPUT" | jq -r '.tool_input.plan // empty' 2>/dev/null)
if [[ -z "$PLAN_CONTENT" ]]; then
    PLAN_CONTENT="(Plan content not available in hook input. Read the most recent .md file from .claude/plans/)"
fi

# Read review type preference
REVIEW_TYPE=$(echo "$FRONTMATTER" | grep '^plan-review-type:' | sed 's/plan-review-type: *//' | tr -d '[:space:]')
REVIEW_TYPE=${REVIEW_TYPE:-ask}

# Build the review prompt with embedded plan content
REVIEW_PROMPT="The plan content is:

<plan>
${PLAN_CONTENT}
</plan>"

# Block ExitPlanMode and inject review instructions
case "$REVIEW_TYPE" in
    quick)
        cat >&2 << REVIEW_MSG
=== PLAN REVIEW REQUIRED ===

Plan review is enabled for this project (type: quick).
ExitPlanMode is BLOCKED until the plan is reviewed.

${REVIEW_PROMPT}

Do the following:

1. Use the Task tool to launch TWO subagents in parallel:
   - codex-default: "Review this implementation plan critically. Identify issues, risks, gaps, missing edge cases, and suggest specific improvements. Here is the plan: [include the full plan content above]"
   - gemini-default: same prompt.

2. When both complete, synthesize their feedback. If there are actionable issues:
   - Update the plan file to address the feedback
   - Summarize what changed

3. Create the review marker:
   touch .claude/.plan-reviewed

4. Call ExitPlanMode again. It will succeed this time.

===============================
REVIEW_MSG
        ;;

    multi-expert)
        cat >&2 << REVIEW_MSG
=== PLAN REVIEW REQUIRED ===

Plan review is enabled for this project (type: multi-expert).
ExitPlanMode is BLOCKED until the plan is reviewed.

${REVIEW_PROMPT}

Do the following:

1. Launch an arena run with the multi-expert profile using the plan content above as the goal.

2. Wait for completion, read the results, and incorporate actionable feedback into the plan.

3. Create the review marker:
   touch .claude/.plan-reviewed

4. Call ExitPlanMode again. It will succeed this time.

===============================
REVIEW_MSG
        ;;

    ask|*)
        cat >&2 << REVIEW_MSG
=== PLAN REVIEW REQUIRED ===

Plan review is enabled for this project.
ExitPlanMode is BLOCKED until the plan is reviewed.

${REVIEW_PROMPT}

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
  1. Launch TWO Task subagents in parallel with the plan content above:
     - codex-default: "Review this implementation plan critically. Identify issues, risks, gaps, missing edge cases, and suggest specific improvements. Here is the plan: [include the full plan content]"
     - gemini-default: same prompt.
  2. Synthesize feedback. If actionable, update the plan.
  3. Create marker: touch .claude/.plan-reviewed
  4. Call ExitPlanMode again.

- **Multi-expert**:
  1. Launch arena run with multi-expert profile using the plan content as the goal.
  2. Wait for completion, incorporate feedback.
  3. Create marker: touch .claude/.plan-reviewed
  4. Call ExitPlanMode again.

===============================
REVIEW_MSG
        ;;
esac

exit 2
