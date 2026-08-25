"""Route-level tests: GET /tasks/{task_id} exposes the derived work_status.

Time is controlled by inserting fixture timestamps (raw SQL for historic
rows, current time for fresh rows) — no sleeps. The derivation helper itself
is unit-tested with an explicit clock in test_work_status.py; these tests
prove the envelope wiring end to end.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from runtime.infrastructure.audit_logger import AuditLogger
from runtime.models import BlockKind, TaskRecord, TaskStatus


def _insert_task(org_state, *, status=TaskStatus.IN_PROGRESS, block_kind=None,
                 agent="dev_agent", last_heartbeat: datetime | None = None) -> str:
    now = datetime.now(timezone.utc)
    org_state.db.insert_task(TaskRecord(
        id="TASK-WS", brief="work-status fixture", team="engineering",
        assigned_agent=agent, status=status, block_kind=block_kind,
        created_at=now, updated_at=now,
    ))
    # insert_task does not persist last_heartbeat — set it via update_task
    # (same path the queue heartbeat uses) so the fixture controls liveness.
    if last_heartbeat is not None:
        org_state.db.update_task("TASK-WS", last_heartbeat=last_heartbeat.isoformat())
    return "TASK-WS"


def _insert_audit_at(org_state, task_id: str, action: str, agent: str,
                     ts: datetime, payload: dict | None = None) -> None:
    """Raw-SQL insert so the timestamp is fully controlled (deterministic)."""
    import json
    org_state.db._conn.execute(
        "INSERT INTO audit_log (task_id, agent, action, payload, timestamp) "
        "VALUES (?, ?, ?, ?, ?)",
        (task_id, agent, action,
         json.dumps(payload) if payload else None, ts.isoformat()),
    )
    org_state.db._conn.commit()


def _get_work_status(app, auth_headers) -> dict:
    r = TestClient(app).get(
        "/api/v1/orgs/alpha/tasks/TASK-WS", headers=auth_headers,
    )
    assert r.status_code == 200
    body = r.json()
    assert "work_status" in body, "envelope must carry work_status"
    return body["work_status"]


def test_detail_envelope_includes_work_status(
    tmp_home, app, org_state, auth_headers,
) -> None:
    now = datetime.now(timezone.utc)
    _insert_task(org_state, last_heartbeat=now)
    AuditLogger(org_state.db).log_session_start(
        "TASK-WS", "dev_agent", workspace="/w",
    )
    ws = _get_work_status(app, auth_headers)
    assert ws["applicable"] is True
    assert ws["state"] == "newly_started"
    assert ws["heartbeat"]["freshness"] == "fresh"


def test_newly_started_no_progress_route(tmp_home, app, org_state, auth_headers) -> None:
    now = datetime.now(timezone.utc)
    _insert_task(org_state, last_heartbeat=now)
    AuditLogger(org_state.db).log_session_start(
        "TASK-WS", "dev_agent", workspace="/w",
    )
    ws = _get_work_status(app, auth_headers)
    assert ws["state"] == "newly_started"
    assert ws["latest_progress"] is None
    assert ws["session_start_ts"] is not None


def test_recent_progress_route(tmp_home, app, org_state, auth_headers) -> None:
    now = datetime.now(timezone.utc)
    _insert_task(org_state, last_heartbeat=now)
    AuditLogger(org_state.db).log_session_start(
        "TASK-WS", "dev_agent", workspace="/w",
    )
    AuditLogger(org_state.db).log_progress(
        "TASK-WS", "dev_agent", "Phase 3 of 6: tests passing",
    )
    ws = _get_work_status(app, auth_headers)
    assert ws["state"] == "recent_progress"
    assert ws["latest_progress"]["message"] == "Phase 3 of 6: tests passing"
    assert ws["latest_progress"]["timestamp"] is not None


def test_stale_no_receipt_route(tmp_home, app, org_state, auth_headers) -> None:
    """TASK-5521-shaped: fresh heartbeat, session started > 5m ago, no progress."""
    now = datetime.now(timezone.utc)
    _insert_task(org_state, last_heartbeat=now - timedelta(seconds=30))
    _insert_audit_at(org_state, "TASK-WS", "session_start", "dev_agent",
                     now - timedelta(minutes=28))
    ws = _get_work_status(app, auth_headers)
    assert ws["state"] == "stale_no_receipt"
    assert ws["label"] == "Stale-but-alive — no substantive update recorded"
    assert ws["heartbeat"]["freshness"] == "fresh"
    assert ws["latest_progress"] is None


def test_stale_old_receipt_route(tmp_home, app, org_state, auth_headers) -> None:
    now = datetime.now(timezone.utc)
    _insert_task(org_state, last_heartbeat=now - timedelta(seconds=30))
    _insert_audit_at(org_state, "TASK-WS", "session_start", "dev_agent",
                     now - timedelta(minutes=60))
    _insert_audit_at(org_state, "TASK-WS", "progress", "dev_agent",
                     now - timedelta(minutes=12), payload={"message": "old"})
    ws = _get_work_status(app, auth_headers)
    assert ws["state"] == "stale_old_receipt"
    assert ws["latest_progress"]["message"] == "old"


def test_heartbeat_stale_route(tmp_home, app, org_state, auth_headers) -> None:
    now = datetime.now(timezone.utc)
    _insert_task(org_state, last_heartbeat=now - timedelta(minutes=2))
    AuditLogger(org_state.db).log_session_start(
        "TASK-WS", "dev_agent", workspace="/w",
    )
    ws = _get_work_status(app, auth_headers)
    assert ws["state"] == "heartbeat_stale"
    assert ws["heartbeat"]["freshness"] == "stale"


def test_heartbeat_absent_route(tmp_home, app, org_state, auth_headers) -> None:
    _insert_task(org_state, last_heartbeat=None)
    AuditLogger(org_state.db).log_session_start(
        "TASK-WS", "dev_agent", workspace="/w",
    )
    ws = _get_work_status(app, auth_headers)
    assert ws["state"] == "heartbeat_unavailable"
    assert ws["heartbeat"]["freshness"] == "unavailable"


def test_terminal_not_applicable_route(
    tmp_home, app, org_state, auth_headers,
) -> None:
    now = datetime.now(timezone.utc)
    _insert_task(org_state, status=TaskStatus.COMPLETED, last_heartbeat=None)
    AuditLogger(org_state.db).log_session_start(
        "TASK-WS", "dev_agent", workspace="/w",
    )
    ws = _get_work_status(app, auth_headers)
    assert ws["applicable"] is False
    assert ws["state"] == "not_applicable"
    assert ws["reason"] == "terminal"
    assert ws["heartbeat"]["timestamp"] is None


def test_parked_on_block_not_applicable_route(
    tmp_home, app, org_state, auth_headers,
) -> None:
    now = datetime.now(timezone.utc)
    _insert_task(org_state, status=TaskStatus.IN_PROGRESS,
                 block_kind=BlockKind.DELEGATED, last_heartbeat=None)
    AuditLogger(org_state.db).log_session_start(
        "TASK-WS", "dev_agent", workspace="/w",
    )
    ws = _get_work_status(app, auth_headers)
    assert ws["state"] == "not_applicable"
    assert ws["reason"] == "blocked"


def test_prior_session_progress_not_satisfying_route(
    tmp_home, app, org_state, auth_headers,
) -> None:
    """Receipt BEFORE the current session_start must not satisfy the session."""
    now = datetime.now(timezone.utc)
    _insert_task(org_state, last_heartbeat=now - timedelta(seconds=30))
    _insert_audit_at(org_state, "TASK-WS", "session_start", "dev_agent",
                     now - timedelta(minutes=10))
    _insert_audit_at(org_state, "TASK-WS", "progress", "dev_agent",
                     now - timedelta(minutes=12), payload={"message": "prior"})
    ws = _get_work_status(app, auth_headers)
    assert ws["state"] == "stale_no_receipt"
    assert ws["latest_progress"] is None


def test_work_status_never_leaks_arbitrary_payload(
    tmp_home, app, org_state, auth_headers,
) -> None:
    """Privacy fence: raw audit payloads (paths, stdout, secrets) stay out."""
    now = datetime.now(timezone.utc)
    _insert_task(org_state, last_heartbeat=now - timedelta(seconds=30))
    _insert_audit_at(org_state, "TASK-WS", "session_start", "dev_agent",
                     now - timedelta(minutes=10), payload={"workspace": "/secret/ws"})
    _insert_audit_at(
        org_state, "TASK-WS", "progress", "dev_agent",
        now - timedelta(seconds=30),
        payload={"message": "milestone", "stdout": "TOP-SECRET", "path": "/etc/passwd"},
    )
    ws = _get_work_status(app, auth_headers)
    assert ws["state"] == "recent_progress"
    # Only the concise message is surfaced; arbitrary keys never leak.
    assert ws["latest_progress"] == {
        "timestamp": ws["latest_progress"]["timestamp"],
        "message": "milestone",
        "agent": "dev_agent",
    }
    assert "stdout" not in ws["latest_progress"]
    assert "path" not in ws["latest_progress"]
    assert "/secret/ws" not in str(ws)
