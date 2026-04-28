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

# Extract task_id, session_id, and agent name from the start-task SKILL's
# Parameters block. The agent name appears in the prompt's first line:
# "You are <agent>. Use the start-task skill to handle this task."
TASK_ID=$(echo "$PROMPT" | awk -F': ' '/^[[:space:]]*task_id: /{gsub(/^[[:space:]]*/, "", $0); print $2; exit}')
SESSION_ID=$(echo "$PROMPT" | awk -F': ' '/^[[:space:]]*session_id: /{gsub(/^[[:space:]]*/, "", $0); print $2; exit}')
AGENT=$(echo "$PROMPT" | awk '/^You are /{sub(/^You are /, "", $0); sub(/\..*$/, "", $0); print; exit}')

# Multi-org: the executor cwd is <runtime>/orgs/<slug>/workspaces/<agent>.
# Strip /workspaces/<agent> off the tail and take the basename — that's the slug.
ORG_PARENT="${PWD%/workspaces/*}"
ORG_SLUG="${ORG_PARENT##*/}"

# If a plan file exists, source it (it can call opc). Pass the agent name as
# $3 and the org slug as $4 so plans can call agent-callback commands with
# the required --org flag.
if [[ -n "${FAKE_CLAUDE_PLAN:-}" && -f "$FAKE_CLAUDE_PLAN" ]]; then
    bash "$FAKE_CLAUDE_PLAN" "$TASK_ID" "$SESSION_ID" "$AGENT" "$ORG_SLUG"
fi

exit 0
