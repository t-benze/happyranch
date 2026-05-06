#!/usr/bin/env bash
# Fake Claude binary — reads scripted behavior from $FAKE_CLAUDE_PLAN
# and optionally calls opc to simulate an agent's session.
set -e

PROMPT=""
JSON_OUTPUT=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        -p) PROMPT="$2"; shift 2 ;;
        --permission-mode) shift 2 ;;
        --output-format)
            if [[ "$2" == "json" ]]; then
                JSON_OUTPUT=1
            fi
            shift 2 ;;
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
#
# The plan's stdout is redirected to stderr so the fixture-shaped JSON we emit
# below is the ONLY thing on our stdout. ClaudeExecutor passes
# `--output-format json` and the parser does `json.loads(stdout.strip())`, so
# any opc-error messages or plan diagnostic prints would otherwise corrupt
# the JSON parse. Plans only need stdout-clean execution; their side effects
# (calling opc, touching files) are unaffected.
if [[ -n "${FAKE_CLAUDE_PLAN:-}" && -f "$FAKE_CLAUDE_PLAN" ]]; then
    bash "$FAKE_CLAUDE_PLAN" "$TASK_ID" "$SESSION_ID" "$AGENT" "$ORG_SLUG" 1>&2
fi

# When the orchestrator runs Claude with `--output-format json` (always, since
# T5), emit a fixture-shaped result blob so `_parse_claude_usage` writes a
# session_token_usage row. Without this, integration runs would never exercise
# the parser and every fake-Claude session would leave the row table empty.
if [[ "$JSON_OUTPUT" == 1 ]]; then
    cat <<'EOF'
{"type":"result","result":"ok","model":"claude-sonnet-4-6","usage":{"input_tokens":1000,"output_tokens":500,"cache_creation_input_tokens":300,"cache_read_input_tokens":200}}
EOF
fi

exit 0
