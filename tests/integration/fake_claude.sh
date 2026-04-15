#!/usr/bin/env bash
# Fake Claude binary — reads scripted behavior from $FAKE_CLAUDE_PLAN
# and optionally calls opc to simulate an agent's session.
set -e

PROMPT=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        -p) PROMPT="$2"; shift 2 ;;
        --permission-mode) shift 2 ;;
        *) shift ;;
    esac
done

# Extract task_id and session_id from the prompt.
TASK_ID=$(echo "$PROMPT" | awk -F': ' '/^Task ID: /{print $2; exit}')
SESSION_ID=$(echo "$PROMPT" | awk -F': ' '/^Session ID: /{print $2; exit}')

# If a plan file exists, source it (it can call opc).
if [[ -n "${FAKE_CLAUDE_PLAN:-}" && -f "$FAKE_CLAUDE_PLAN" ]]; then
    bash "$FAKE_CLAUDE_PLAN" "$TASK_ID" "$SESSION_ID"
fi

exit 0
