#!/bin/bash
# Arena Notification Formatter
#
# Formats notifications from arena orchestration for better visibility.
# Reads notification JSON from stdin, outputs formatted message.
#
# Input (JSON via stdin):
# {
#   "title": "...",
#   "message": "...",
#   "type": "info|warning|error|success",
#   "metadata": { ... }
# }

set -euo pipefail

# Read JSON from stdin
INPUT=$(cat)

# Extract fields
TITLE=$(echo "$INPUT" | jq -r '.title // "Arena Notification"')
MESSAGE=$(echo "$INPUT" | jq -r '.message // ""')
TYPE=$(echo "$INPUT" | jq -r '.type // "info"')

# Check if this is an arena-related notification
if [[ "$TITLE" != *"arena"* ]] && [[ "$TITLE" != *"Arena"* ]] && [[ "$MESSAGE" != *"arena"* ]] && [[ "$MESSAGE" != *".arena/"* ]]; then
    # Not arena-related, pass through
    exit 0
fi

# Format based on type
case "$TYPE" in
    "error")
        ICON="[ERROR]"
        ;;
    "warning")
        ICON="[WARN]"
        ;;
    "success")
        ICON="[OK]"
        ;;
    *)
        ICON="[ARENA]"
        ;;
esac

# Check for specific arena events
if [[ "$MESSAGE" == *"HITL"* ]] || [[ "$MESSAGE" == *"needs_human"* ]]; then
    echo "════════════════════════════════════════" >&2
    echo "$ICON HUMAN INPUT REQUIRED" >&2
    echo "────────────────────────────────────────" >&2
    echo "$MESSAGE" >&2
    echo "" >&2
    echo "Next steps:" >&2
    echo "  1. Review questions in .arena/runs/latest/hitl/questions.json" >&2
    echo "  2. Use /arena:resume to continue" >&2
    echo "════════════════════════════════════════" >&2
elif [[ "$MESSAGE" == *"completed"* ]] || [[ "$MESSAGE" == *"APPROVED"* ]]; then
    echo "════════════════════════════════════════" >&2
    echo "$ICON ORCHESTRATION COMPLETE" >&2
    echo "────────────────────────────────────────" >&2
    echo "$MESSAGE" >&2
    echo "" >&2
    # Check for final artifact
    if [[ -f ".arena/runs/latest/final/artifact.md" ]]; then
        echo "Output: .arena/runs/latest/final/artifact.md" >&2
    fi
    echo "════════════════════════════════════════" >&2
elif [[ "$MESSAGE" == *"iteration"* ]] || [[ "$MESSAGE" == *"critique"* ]]; then
    echo "──── $ICON $TITLE ────" >&2
    echo "$MESSAGE" >&2
else
    # Generic arena notification
    echo "──── $ICON ────" >&2
    echo "$TITLE: $MESSAGE" >&2
fi

exit 0
