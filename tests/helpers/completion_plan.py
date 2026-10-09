"""Explicit test plans report through the supported source-bound completion CLI."""
from __future__ import annotations


def completion_prelude() -> str:
    """Define report_completion(agent, summary) for task plans with bound IDs."""
    return '''report_completion() {
    callback_file="$PWD/completion-$session_id.json"
    python - "$task_id" "$session_id" "$1" "$2" "$callback_file" <<'COMPLETION_JSON'
import json
import pathlib
import sys
task, session, agent, summary, destination = sys.argv[1:]
payload = {"task_id": task, "session_id": session, "agent": agent,
           "status": "completed", "confidence": 90, "summary": summary}
try:
    decision = json.loads(summary)
except json.JSONDecodeError:
    decision = None
if isinstance(decision, dict) and "action" in decision:
    payload["decision"] = decision
pathlib.Path(destination).write_text(json.dumps(payload))
COMPLETION_JSON
    happyranch report-completion --org "$org_slug" --from-file "$callback_file"
}
'''
