#!/usr/bin/env bash
# Fake Codex binary — reads scripted behavior from $FAKE_CODEX_PLAN
# and optionally calls opc to simulate an agent session.
set -e

PROMPT=""
if [[ "${*: -1}" == "-" ]]; then
    PROMPT="$(cat)"
elif [[ $# -gt 0 ]]; then
    PROMPT="${*: -1}"
fi

TASK_ID=$(echo "$PROMPT" | awk -F': ' '/^[[:space:]]*task_id: /{gsub(/^[[:space:]]*/, "", $0); print $2; exit}')
SESSION_ID=$(echo "$PROMPT" | awk -F': ' '/^[[:space:]]*session_id: /{gsub(/^[[:space:]]*/, "", $0); print $2; exit}')

if [[ -n "${FAKE_CODEX_PLAN:-}" && -f "$FAKE_CODEX_PLAN" ]]; then
    bash "$FAKE_CODEX_PLAN" "$TASK_ID" "$SESSION_ID"
fi

exit 0
