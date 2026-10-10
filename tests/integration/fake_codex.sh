#!/usr/bin/env bash
# Fake Codex binary — reads scripted behavior from $FAKE_CODEX_PLAN
# and optionally calls happyranch to simulate an agent session.
set -e
STUB_ARGV=("$@")
if [[ -z "${HAPPYRANCH_TEST_PARENT_MANIFEST:-}" || -z "${HAPPYRANCH_TEST_STUB_GUARD:-}" ]]; then
    echo 'deterministic stub requires isolated test parent' >&2
    exit 86
fi

# uv and daemon startup may prepend other tool directories. This registered
# stub lives beside the bound Python/callback wrappers; restore that exact
# test-only route before the unchanged executable/registry/plan identity gate.
export PATH="${0%/*}:/usr/bin:/bin"
HAPPYRANCH_TEST_CONTEXT_ARGV_JSON=$(python -c 'import json,sys; print(json.dumps(sys.argv[1:]))' "${STUB_ARGV[@]}")
export HAPPYRANCH_TEST_CONTEXT_ARGV_JSON

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
    PROMPT="$(cat; printf '\001')"
    PROMPT="${PROMPT%$'\001'}"
elif [[ $# -gt 0 ]]; then
    PROMPT="${*: -1}"
fi

if [[ -z "${FAKE_CODEX_PLAN:-}" || ! -f "$FAKE_CODEX_PLAN" || ! -x "$FAKE_CODEX_PLAN" ]]; then
        echo 'deterministic stub plan missing or unavailable' >&2
        exit 86
    fi
    if [[ -n "${HAPPYRANCH_TEST_PARENT_MANIFEST:-}" ]]; then
        C7_PLAN=$(python "$HAPPYRANCH_TEST_STUB_GUARD" "$0" codex "$FAKE_CODEX_PLAN" "${STUB_ARGV[@]}")
        if [[ "$C7_PLAN" == C7 ]]; then
            export HAPPYRANCH_TEST_CONTEXT_STUB="$0"
            export HAPPYRANCH_TEST_CONTEXT_PLAN="$FAKE_CODEX_PLAN"
            export HAPPYRANCH_TEST_CONTEXT_PROVIDER_PID="$$"
            C7_PROVIDER_SESSION="c7-codex-$$"
            trap 'C7_STATUS=$?; python "$HAPPYRANCH_TEST_STUB_GUARD" --context-exit "$0" codex "$FAKE_CODEX_PLAN" "$C7_STATUS" "$$" "$C7_PROVIDER_SESSION" || exit 86; exit "$C7_STATUS"' EXIT
        fi
    fi

# A recovery invocation carries its new runtime tuple in an explicit binding.
# The outer Parameters block may also be present; every present tuple must
# agree. Non-task contexts retain empty positional args for their own parser.
if [[ "${C7_PLAN:-}" == C7 ]]; then
    BINDING=$(printf '%s' "$PROMPT" | python -c '
import sys
from tests.helpers.human_team_context_plan import _outer_identity
from pathlib import Path
kind, identity = _outer_identity(sys.stdin.read(), Path.cwd().parent.parent.name)
print(identity["task_id"] + " " + identity["session_id"] if kind == "task" else "")
')
else
BINDING=$(printf '%s' "$PROMPT" | python -c '
import re,sys
prompt=sys.stdin.read()
tasks=re.findall(r"^[ \t]*task_id: (TASK-[0-9]+)[ \t]*$",prompt,re.MULTILINE)
sessions=re.findall(r"^[ \t]*session_id: (sess-[a-f0-9]+)[ \t]*$",prompt,re.MULTILINE)
explicit=re.findall(r"binding task=(TASK-[0-9]+) session=(sess-[a-f0-9]+)",prompt)
if len(tasks)>1 or len(sessions)>1 or len(explicit)>1 or bool(tasks)!=bool(sessions):
    raise SystemExit("duplicate_or_incomplete_runtime_binding")
ordinary=list(zip(tasks,sessions))
if ordinary and explicit and ordinary!=explicit:
    raise SystemExit("conflicting_runtime_binding")
selected=ordinary or explicit
print(" ".join(selected[0]) if selected else "")
')
fi
TASK_ID="${BINDING%% *}"
SESSION_ID="${BINDING#* }"

# Multi-org: the executor cwd is <runtime>/orgs/<slug>/workspaces/<agent>.
ORG_PARENT="${PWD%/workspaces/*}"
ORG_SLUG="${ORG_PARENT##*/}"

# Plan stdout redirected to stderr so the NDJSON event stream we emit below
# is the ONLY thing on stdout — _parse_codex_usage scans stdout line-by-line
# for `{"type":"turn.completed",...}`, and any extra non-NDJSON text from
# plans would either break the scan or pollute usage_raw_json.
    if [[ -n "${FAKE_CODEX_PLAN:-}" && -f "$FAKE_CODEX_PLAN" ]]; then
    printf '%s' "$PROMPT" | bash "$FAKE_CODEX_PLAN" "$TASK_ID" "$SESSION_ID" "$ORG_SLUG" 1>&2
fi

# When the orchestrator runs Codex with `--json`, emit a real-shaped
# NDJSON event stream so the parsers work: `thread.started` first (carrying
# the stable thread_id, as on real codex-cli 0.148.0 — the TASK-5977 session
# parser reads it), then the terminal `turn.completed` usage event (the
# TASK-173 usage parser reads it). Mirrors codex-cli >= 0.137: the terminal
# usage event is `turn.completed` and no model field is emitted.
if [[ "$JSON_OUTPUT" == 1 ]]; then
    if [[ "${C7_PLAN:-}" == C7 ]]; then
        printf '{"type":"thread.started","thread_id":"%s"}\n' "$C7_PROVIDER_SESSION"
        printf '%s\n' '{"type":"turn.completed","usage":{"input_tokens":2000,"cached_input_tokens":150,"output_tokens":800,"reasoning_output_tokens":100}}'
    else
    cat <<'EOF'
{"type":"thread.started","thread_id":"01a0-fake-codex-thread","timestamp":"2026-01-01T00:00:00Z"}
{"type":"turn.completed","usage":{"input_tokens":2000,"cached_input_tokens":150,"output_tokens":800,"reasoning_output_tokens":100}}
EOF
    fi
fi

exit 0
