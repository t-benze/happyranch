#!/usr/bin/env bash
# Fake Codex binary — reads scripted behavior from $FAKE_CODEX_PLAN
# and optionally calls happyranch to simulate an agent session.
set -e

PROMPT=""
JSON_OUTPUT=0
# Detect `--json` anywhere in the argv (Codex passes it among other flags).
for arg in "$@"; do
    if [[ "$arg" == "--json" ]]; then
        JSON_OUTPUT=1
        break
    fi
done

if [[ "${*: -1}" == "-" ]]; then
    PROMPT="$(cat)"
elif [[ $# -gt 0 ]]; then
    PROMPT="${*: -1}"
fi

TASK_ID=$(echo "$PROMPT" | awk -F': ' '/^[[:space:]]*task_id: /{gsub(/^[[:space:]]*/, "", $0); print $2; exit}')
SESSION_ID=$(echo "$PROMPT" | awk -F': ' '/^[[:space:]]*session_id: /{gsub(/^[[:space:]]*/, "", $0); print $2; exit}')

# Multi-org: the executor cwd is <runtime>/orgs/<slug>/workspaces/<agent>.
ORG_PARENT="${PWD%/workspaces/*}"
ORG_SLUG="${ORG_PARENT##*/}"

# Plan stdout redirected to stderr so the NDJSON event stream we emit below
# is the ONLY thing on stdout — _parse_codex_usage scans stdout line-by-line
# for `{"type":"turn.completed",...}`, and any extra non-NDJSON text from
# plans would either break the scan or pollute usage_raw_json.
if [[ -n "${FAKE_CODEX_PLAN:-}" && -f "$FAKE_CODEX_PLAN" ]]; then
    bash "$FAKE_CODEX_PLAN" "$TASK_ID" "$SESSION_ID" "$ORG_SLUG" 1>&2
fi

# When the orchestrator runs Codex with `--json`, emit a real-shaped
# NDJSON event stream so the parsers work: `thread.started` first (carrying
# the stable thread_id, as on real codex-cli 0.148.0 — the TASK-5977 session
# parser reads it), then the terminal `turn.completed` usage event (the
# TASK-173 usage parser reads it). Mirrors codex-cli >= 0.137: the terminal
# usage event is `turn.completed` and no model field is emitted.
if [[ "$JSON_OUTPUT" == 1 ]]; then
    cat <<'EOF'
{"type":"thread.started","thread_id":"01a0-fake-codex-thread","timestamp":"2026-01-01T00:00:00Z"}
{"type":"turn.completed","usage":{"input_tokens":2000,"cached_input_tokens":150,"output_tokens":800,"reasoning_output_tokens":100}}
EOF
fi

exit 0
