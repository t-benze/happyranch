#!/usr/bin/env bash
# Fake OpenCode binary — reads scripted behavior from $FAKE_OPENCODE_PLAN
# (task path) / $FAKE_OPENCODE_THREAD_PLAN (thread path) and optionally
# calls happyranch to simulate an agent's session.
#
# Mirrors the real opencode 1.18.25 run contract (TASK-6080 audit):
#   - `opencode run [-s <id>] --dir <ws> --format json` with the prompt
#     body on STDIN (no positional message element).
#   - stdout is a JSONL event stream; every event carries the top-level
#     `sessionID`. Fresh runs mint a stable fake id; resume (`-s <id>`)
#     re-emits the SAME id (the observed 1.18.25 behavior).
#   - `--format json` must be present for the NDJSON emission (the runtime
#     always passes it).
set -e

PROMPT="$(cat)"
SID=""
JSON_OUTPUT=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        run) shift ;;
        -s) SID="$2"; shift 2 ;;
        --dir) shift 2 ;;
        --format)
            if [[ "$2" == "json" ]]; then
                JSON_OUTPUT=1
            fi
            shift 2 ;;
        -m) shift 2 ;;
        *) shift ;;
    esac
done

# Stable fake session id: reuse the resumed id when present, else a fixed
# fresh id (mirrors the real CLI's stable-across-continuation behavior).
if [[ -n "$SID" ]]; then
    EMITTED_SID="$SID"
else
    EMITTED_SID="ses_fake-opencode-0000000000000000"
fi

TASK_ID=$(echo "$PROMPT" | awk -F': ' '/^[[:space:]]*task_id: /{gsub(/^[[:space:]]*/, "", $0); print $2; exit}')
SESSION_ID=$(echo "$PROMPT" | awk -F': ' '/^[[:space:]]*session_id: /{gsub(/^[[:space:]]*/, "", $0); print $2; exit}')

# Multi-org: the executor cwd is <runtime>/orgs/<slug>/workspaces/<agent>.
ORG_PARENT="${PWD%/workspaces/*}"
ORG_SLUG="${ORG_PARENT##*/}"

# Thread invocation detection — same markers fake_claude.sh uses.
THREAD_INVOCATION_TOKEN=$(echo "$PROMPT" | awk -F': ' '/^Your invocation_token for this turn is: /{print $2; exit}')
if [[ -n "$THREAD_INVOCATION_TOKEN" ]]; then
    THREAD_ID=$(echo "$PROMPT" | awk '/^You are participating in thread /{
        match($0, /THR-[0-9]+/); print substr($0, RSTART, RLENGTH); exit
    }')
    PURPOSE_LINE=$(echo "$PROMPT" | awk '/^  Message [0-9]+ addressed/{print "reply"; exit} /^  The founder has added you/{print "bootstrap"; exit} /^  This thread is being archived/{print "close_out"; exit} /^  Task TASK-[0-9]+ that you dispatched/{print "task_followup"; exit}')
    THREAD_PURPOSE="${PURPOSE_LINE:-reply}"
    THREAD_AGENT="${PWD##*/}"
    if [[ -n "${FAKE_OPENCODE_THREAD_PLAN:-}" && -f "$FAKE_OPENCODE_THREAD_PLAN" ]]; then
        bash "$FAKE_OPENCODE_THREAD_PLAN" \
            "$THREAD_ID" "$THREAD_INVOCATION_TOKEN" "$THREAD_AGENT" "$ORG_SLUG" "$THREAD_PURPOSE" "$SID" 1>&2
    fi
    if [[ "$JSON_OUTPUT" == 1 ]]; then
        cat <<EOF
{"type":"step_start","timestamp":1,"sessionID":"$EMITTED_SID","part":{"id":"prt_01","sessionID":"$EMITTED_SID","type":"step-start"}}
{"type":"step_finish","timestamp":2,"sessionID":"$EMITTED_SID","part":{"id":"prt_02","reason":"stop","type":"step-finish","tokens":{"total":1,"input":1,"output":1,"reasoning":0,"cache":{"write":0,"read":0}},"cost":0}}
EOF
    fi
    exit 0
fi

# Task path plan (stdout redirected to stderr so the NDJSON below is the
# ONLY thing on stdout — the parsers scan stdout line-by-line).
if [[ -n "${FAKE_OPENCODE_PLAN:-}" && -f "$FAKE_OPENCODE_PLAN" ]]; then
    bash "$FAKE_OPENCODE_PLAN" "$TASK_ID" "$SESSION_ID" "$ORG_SLUG" 1>&2
fi

if [[ "$JSON_OUTPUT" == 1 ]]; then
    cat <<EOF
{"type":"step_start","timestamp":1,"sessionID":"$EMITTED_SID","part":{"id":"prt_01","sessionID":"$EMITTED_SID","type":"step-start"}}
{"type":"step_finish","timestamp":2,"sessionID":"$EMITTED_SID","part":{"id":"prt_02","reason":"stop","type":"step-finish","tokens":{"total":1,"input":1,"output":1,"reasoning":0,"cache":{"write":0,"read":0}},"cost":0}}
EOF
fi

exit 0
