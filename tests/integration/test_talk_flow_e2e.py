"""End-to-end: daemon + real HTTP for the full talk lifecycle.

Exercises the talk routes against a live daemon (spawned via
`scripts/daemon.sh`) and asserts state on disk (transcript file,
learnings.md) as well as on the audit log.

Style note: this file intentionally mirrors ``test_end_to_end.py`` — same
``live_daemon`` fixture, same ``_auth_headers`` helper, same ``httpx``
direct calls. The plan (docs/superpowers/plans/2026-04-21-talk-flow.md
§Task 14) sketched the flow using a ``cli_runner`` fixture that does not
exist; we adapt to the project's established HTTP integration pattern
while preserving the plan's spirit — exercise daemon routes end-to-end.
"""
from __future__ import annotations

from pathlib import Path

import httpx
import pytest


pytestmark = pytest.mark.integration


def _auth_headers() -> dict:
    from src.daemon import paths

    return {"Authorization": f"Bearer {paths.read_token()}"}


def _base(port: str) -> str:
    return f"http://127.0.0.1:{port}/api/v1"


def test_full_talk_lifecycle(live_daemon, runtime: Path) -> None:
    port = live_daemon
    base = _base(port)
    headers = _auth_headers()

    # 1. Minimal workspace (just the directory — end route writes learnings.md
    # directly without needing the start-task skill marker).
    (runtime / "workspaces" / "dev_agent").mkdir(parents=True, exist_ok=True)

    # 2. Start a talk.
    r1 = httpx.post(
        f"{base}/talks",
        json={"agent_name": "dev_agent"},
        headers=headers,
        timeout=5.0,
    )
    assert r1.status_code == 200, r1.text
    tid = r1.json()["talk_id"]
    assert tid.startswith("TALK-")

    # 3. Second start for the same agent → 409 with prior talk id echoed back.
    r2 = httpx.post(
        f"{base}/talks",
        json={"agent_name": "dev_agent"},
        headers=headers,
        timeout=5.0,
    )
    assert r2.status_code == 409, r2.text
    detail = r2.json()["detail"]
    assert detail["code"] == "talk_already_open"
    assert detail["prior_open_talk_id"] == tid

    # 4. End the talk with a summary, transcript, and one learning.
    end_body = {
        "summary": "We agreed Codex is better for infra tasks.",
        "topic_list": ["executor choice"],
        "transcript_markdown": "## turn 1\nfounder: let's talk.\nagent: ok.\n",
        "learnings": [{"text": "Codex > Claude for infra work."}],
        "kb_slugs": [],
    }
    r3 = httpx.post(
        f"{base}/talks/{tid}/end",
        json=end_body,
        headers=headers,
        timeout=5.0,
    )
    assert r3.status_code == 200, r3.text
    end_resp = r3.json()
    assert end_resp["talk_id"] == tid
    assert end_resp["status"] == "closed"
    assert end_resp["new_learnings_count"] == 1
    assert end_resp["transcript_path"]

    # 5. GET the talk — summary round-trips, status is closed.
    r4 = httpx.get(f"{base}/talks/{tid}", headers=headers, timeout=5.0)
    assert r4.status_code == 200, r4.text
    got = r4.json()
    assert got["status"] == "closed"
    assert got["summary"] == end_body["summary"]

    # 6. Transcript file exists on disk under <runtime>/talks/.
    transcript_file = runtime / "talks" / f"{tid}.md"
    assert transcript_file.exists(), transcript_file
    transcript_text = transcript_file.read_text()
    assert "## turn 1" in transcript_text

    # 7. learnings.md was written to the agent's workspace.
    learnings_file = runtime / "workspaces" / "dev_agent" / "learnings.md"
    assert learnings_file.exists(), learnings_file
    assert "Codex > Claude for infra work." in learnings_file.read_text()

    # 8. Audit log has both talk_started and talk_ended entries. The
    # audit_log table stores talk_id in the task_id column (see
    # AuditLogger note above log_talk_started), so filtering by task_id
    # is the right query shape here.
    r5 = httpx.get(
        f"{base}/audit",
        params={"task_id": tid},
        headers=headers,
        timeout=5.0,
    )
    assert r5.status_code == 200, r5.text
    actions = {entry["action"] for entry in r5.json()["entries"]}
    assert "talk_started" in actions
    assert "talk_ended" in actions

    # 9. A new talk for the same agent now succeeds (prior is closed).
    r6 = httpx.post(
        f"{base}/talks",
        json={"agent_name": "dev_agent"},
        headers=headers,
        timeout=5.0,
    )
    assert r6.status_code == 200, r6.text
    tid2 = r6.json()["talk_id"]
    assert tid2 != tid


def test_orphan_resolution_flow(live_daemon, runtime: Path) -> None:
    port = live_daemon
    base = _base(port)
    headers = _auth_headers()

    (runtime / "workspaces" / "dev_agent").mkdir(parents=True, exist_ok=True)

    # Start talk #1.
    r1 = httpx.post(
        f"{base}/talks",
        json={"agent_name": "dev_agent"},
        headers=headers,
        timeout=5.0,
    )
    assert r1.status_code == 200, r1.text
    tid1 = r1.json()["talk_id"]

    # Second start blocked.
    r2 = httpx.post(
        f"{base}/talks",
        json={"agent_name": "dev_agent"},
        headers=headers,
        timeout=5.0,
    )
    assert r2.status_code == 409, r2.text

    # Abandon the orphan.
    r3 = httpx.post(
        f"{base}/talks/{tid1}/abandon",
        json={"reason": "orphan_at_new_start"},
        headers=headers,
        timeout=5.0,
    )
    assert r3.status_code == 200, r3.text
    assert r3.json()["status"] == "abandoned"

    # New start now succeeds and yields a different talk id.
    r4 = httpx.post(
        f"{base}/talks",
        json={"agent_name": "dev_agent"},
        headers=headers,
        timeout=5.0,
    )
    assert r4.status_code == 200, r4.text
    tid2 = r4.json()["talk_id"]
    assert tid2 != tid1
