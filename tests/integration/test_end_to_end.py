from __future__ import annotations

import json
import os
from pathlib import Path

import httpx
import pytest


pytestmark = pytest.mark.integration


def _auth_headers() -> dict:
    from src.daemon import paths
    return {"Authorization": f"Bearer {paths.read_token()}"}


def test_register_and_run_completes_via_callback(live_daemon, runtime, tmp_path):
    port = live_daemon
    base = f"http://127.0.0.1:{port}/api/v1"

    # Plan: fake claude calls report-completion.
    plan = tmp_path / "plan.sh"
    plan.write_text(
        '#!/usr/bin/env bash\n'
        'task_id=$1; session_id=$2\n'
        'opc report-completion \\\n'
        '  --task-id "$task_id" --session-id "$session_id" \\\n'
        '  --agent engineering_head --status completed --confidence 90 \\\n'
        '  --summary \'{"action":"done","summary":"ok"}\'\n'
    )
    plan.chmod(0o755)
    os.environ["FAKE_CLAUDE_PLAN"] = str(plan)

    # Register the runtime
    r = httpx.post(f"{base}/runtimes/register", json={"path": str(runtime)},
                   headers=_auth_headers(), timeout=5.0)
    assert r.status_code == 200

    # Submit a task
    r = httpx.post(f"{base}/tasks", json={"type": "general", "brief": "smoke"},
                   headers=_auth_headers(), timeout=5.0)
    assert r.status_code == 200
    task_id = r.json()["task_id"]

    # Stream events until terminal
    with httpx.stream("GET", f"{base}/tasks/{task_id}/events",
                      headers=_auth_headers(), timeout=30.0) as stream:
        outcome = None
        for line in stream.iter_lines():
            if not line.startswith("data: "):
                continue
            event = json.loads(line.removeprefix("data: "))
            if event.get("type") in ("task_complete", "task_escalated", "task_rejected"):
                outcome = event.get("type")
                break
    assert outcome == "task_complete"

    # Confirm DB has a task_result tagged with a session_id
    r = httpx.get(f"{base}/tasks/{task_id}", headers=_auth_headers(), timeout=5.0)
    body = r.json()
    assert body["task"]["status"] == "approved"
    assert any(res["session_id"] for res in body["results"])
