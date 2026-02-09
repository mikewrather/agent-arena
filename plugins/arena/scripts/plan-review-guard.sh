#!/bin/bash
# Plan Review Guard - PostToolUse hook for ExitPlanMode
#
# Checks if plan review is enabled via .claude/arena.local.md.
# If enabled, injects review options into the conversation via stderr + exit 2.
# If not enabled, exits 0 silently (zero overhead).

set -euo pipefail

CONFIG=".claude/arena.local.md"

# Quick exit if no config file
if [[ ! -f "$CONFIG" ]]; then
    exit 0
fi

# Parse YAML frontmatter for plan-review setting
FRONTMATTER=$(sed -n '/^---$/,/^---$/{ /^---$/d; p; }' "$CONFIG")
PLAN_REVIEW=$(echo "$FRONTMATTER" | grep '^plan-review:' | sed 's/plan-review: *//' | tr -d '[:space:]')

if [[ "$PLAN_REVIEW" != "true" ]]; then
    exit 0
fi

# Plan review is enabled - inject review prompt via stderr
cat >&2 << 'REVIEW_PROMPT'
=== PLAN REVIEW CHECKPOINT ===

A plan has been finalized. Plan review is enabled for this project.

Before proceeding to implementation, use AskUserQuestion to present the user with review options:

Question: "How would you like to review this plan before implementation?"
Header: "Plan Review"
Options:
1. "Skip review" - "Proceed directly to implementation without external review"
2. "Quick review (Codex + Gemini)" - "Two independent AI perspectives review your plan in parallel (~1-2 min)"
3. "Multi-expert arena review" - "Full arena orchestration with specialized expert panel (~3-5 min)"

Based on their choice:

- **Skip**: Continue to implementation normally.

- **Quick review**: Read the most recent plan file from .claude/plans/. Then use the Task tool to launch TWO subagents in parallel:
  (1) codex-default: "Review this implementation plan for issues, risks, gaps, missing edge cases, and suggested improvements. Be specific and actionable. Here is the plan: [plan content]"
  (2) gemini-default: same prompt.
  When both complete, synthesize their feedback into a concise summary and present it to the user before proceeding.

- **Multi-expert**: Read the most recent plan file from .claude/plans/. Create an arena run using /arena:run with profile 'multi-expert' and the plan content as the goal. Tell the user the review is running in background and they'll be notified when complete.

===============================
REVIEW_PROMPT

exit 2
