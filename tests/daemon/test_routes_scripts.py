"""Unit tests for src/daemon/routes/scripts.py (spec §5.1)."""
from __future__ import annotations

import secrets

import pytest

from src.models import TaskRecord, TaskStatus


def _make_active_session(org, agent: str = "engineering_head"):
    task = TaskRecord(
        id=org.db.next_task_id(),
        assigned_agent=agent,
        team="engineering",
        brief="test",
        status=TaskStatus.IN_PROGRESS,
    )
    org.db.insert_task(task)
    session_id = "sid-" + secrets.token_hex(4)
    org.sessions.set_active(task.id, agent, session_id)
    return task.id, session_id


def _make_completed_task(org, agent: str = "engineering_head"):
    task = TaskRecord(
        id=org.db.next_task_id(),
        assigned_agent=agent,
        team="engineering",
        brief="done",
        status=TaskStatus.COMPLETED,
    )
    org.db.insert_task(task)
    return task.id


def test_submit_unknown_task(client_with_runtime):
    client, org = client_with_runtime
    r = client.post(
        "/api/v1/orgs/alpha/scripts/submit",
        json={
            "task_id": "TASK-999",
            "session_id": "sid",
            "title": "x",
            "rationale": "y",
            "script": "echo hi",
            "interpreter": "bash",
        },
    )
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "unknown_task"


def test_submit_task_not_active(client_with_runtime):
    client, org = client_with_runtime
    task_id = _make_completed_task(org)
    r = client.post(
        "/api/v1/orgs/alpha/scripts/submit",
        json={
            "task_id": task_id,
            "session_id": "sid",
            "title": "x", "rationale": "y", "script": "echo hi", "interpreter": "bash",
        },
    )
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "task_not_active"


def test_submit_session_mismatch(client_with_runtime):
    client, org = client_with_runtime
    task_id, _real_sid = _make_active_session(org)
    r = client.post(
        "/api/v1/orgs/alpha/scripts/submit",
        json={
            "task_id": task_id,
            "session_id": "WRONG",
            "title": "x", "rationale": "y", "script": "echo hi", "interpreter": "bash",
        },
    )
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "session_mismatch"


def test_submit_happy_path(client_with_runtime):
    client, org = client_with_runtime
    task_id, sid = _make_active_session(org)
    r = client.post(
        "/api/v1/orgs/alpha/scripts/submit",
        json={
            "task_id": task_id,
            "session_id": sid,
            "title": "Close PR #247",
            "rationale": "needs founder gh scope",
            "script": "gh pr close 247",
            "interpreter": "bash",
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["id"].startswith("SR-")
    assert body["status"] == "pending"


def test_submit_empty_title(client_with_runtime):
    client, org = client_with_runtime
    task_id, sid = _make_active_session(org)
    r = client.post(
        "/api/v1/orgs/alpha/scripts/submit",
        json={
            "task_id": task_id, "session_id": sid,
            "title": "  ", "rationale": "y", "script": "x", "interpreter": "bash",
        },
    )
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "empty_title"


def test_submit_unknown_interpreter(client_with_runtime):
    client, org = client_with_runtime
    task_id, sid = _make_active_session(org)
    r = client.post(
        "/api/v1/orgs/alpha/scripts/submit",
        json={
            "task_id": task_id, "session_id": sid,
            "title": "x", "rationale": "y", "script": "x", "interpreter": "ruby",
        },
    )
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "unknown_interpreter"


def test_submit_invalid_cwd_hint_dotdot(client_with_runtime):
    client, org = client_with_runtime
    task_id, sid = _make_active_session(org)
    r = client.post(
        "/api/v1/orgs/alpha/scripts/submit",
        json={
            "task_id": task_id, "session_id": sid,
            "title": "x", "rationale": "y", "script": "x", "interpreter": "bash",
            "cwd_hint": "../../etc",
        },
    )
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "invalid_cwd_hint"


def test_submit_script_too_large(client_with_runtime):
    client, org = client_with_runtime
    task_id, sid = _make_active_session(org)
    big = "x" * 65537
    r = client.post(
        "/api/v1/orgs/alpha/scripts/submit",
        json={
            "task_id": task_id, "session_id": sid,
            "title": "x", "rationale": "y", "script": big, "interpreter": "bash",
        },
    )
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "script_too_large"
