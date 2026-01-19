#!/bin/bash
# Arena HITL Handler - Processes SubagentStop events
#
# Called by hooks when arena subagent completes.
# Reads agent-result.json and outputs instructions for Claude.
#
# Exit codes:
#   0 - Continue normally
#   2 - Block and show message to Claude (via stderr)

set -euo pipefail

# Find the latest run directory
ARENA_DIR=".arena"
LATEST_LINK="${ARENA_DIR}/runs/latest"

if [[ ! -L "$LATEST_LINK" ]] && [[ ! -d "$LATEST_LINK" ]]; then
    # No arena run active
    exit 0
fi

RESULT_FILE="${LATEST_LINK}/agent-result.json"
if [[ ! -f "$RESULT_FILE" ]]; then
    exit 0
fi

# Parse the result
STATUS=$(jq -r '.status // "unknown"' "$RESULT_FILE" 2>/dev/null || echo "unknown")
EXIT_CODE=$(jq -r '.exit_code // 0' "$RESULT_FILE" 2>/dev/null || echo "0")
RUN_NAME=$(jq -r '.run_name // "unknown"' "$RESULT_FILE" 2>/dev/null || echo "unknown")

case "$STATUS" in
    "needs_human"|"hitl")
        # HITL needed - read questions and format for Claude
        QUESTIONS_FILE="${LATEST_LINK}/hitl/questions.json"
        if [[ -f "$QUESTIONS_FILE" ]]; then
            echo "=== ARENA HITL REQUIRED ===" >&2
            echo "Run: $RUN_NAME" >&2
            echo "" >&2
            echo "Questions from agents:" >&2
            jq -r '.questions[] | "[\(.agent)] \(.questions | if type == "array" then .[0].question // .[0] else . end)"' "$QUESTIONS_FILE" 2>/dev/null >&2 || cat "$QUESTIONS_FILE" >&2
            echo "" >&2
            echo "To respond, use AskUserQuestion tool to collect answers," >&2
            echo "then resume with: /arena:resume $RUN_NAME" >&2
            echo "=========================" >&2
            exit 2
        fi
        ;;

    "completed"|"done"|"success")
        # Success - summarize results
        RESOLUTION_FILE="${LATEST_LINK}/resolution.json"
        if [[ -f "$RESOLUTION_FILE" ]]; then
            REASON=$(jq -r '.reason // "completed"' "$RESOLUTION_FILE" 2>/dev/null || echo "completed")
            TURN=$(jq -r '.final_turn // "?"' "$RESOLUTION_FILE" 2>/dev/null || echo "?")
            echo "=== ARENA COMPLETED ===" >&2
            echo "Run: $RUN_NAME" >&2
            echo "Resolution: $REASON" >&2
            echo "Final turn: $TURN" >&2
            echo "" >&2
            # Check for final artifact (reliable-generation)
            if [[ -f "${LATEST_LINK}/final/artifact.md" ]]; then
                echo "Output: ${LATEST_LINK}/final/artifact.md" >&2
            fi
            echo "Thread: ${LATEST_LINK}/thread.jsonl" >&2
            echo "========================" >&2
        fi
        ;;

    "error"|"failed")
        ERROR=$(jq -r '.error // "Unknown error"' "$RESULT_FILE" 2>/dev/null || echo "Unknown error")
        echo "=== ARENA ERROR ===" >&2
        echo "Run: $RUN_NAME" >&2
        echo "Error: $ERROR" >&2
        echo "===================" >&2
        ;;

    "max_turns")
        echo "=== ARENA MAX TURNS ===" >&2
        echo "Run: $RUN_NAME" >&2
        echo "Reached maximum turn limit." >&2
        echo "Review: ${LATEST_LINK}/thread.jsonl" >&2
        echo "To continue: /arena:resume $RUN_NAME --extend" >&2
        echo "=======================" >&2
        ;;
esac

exit 0
