from __future__ import annotations

import secrets
from datetime import datetime, timezone

import pytest

from src.models import CompletionReport, JobInterpreter, JobRecord, JobStatus, TaskRecord, TaskStatus


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_active_task(org, agent: str = "dev_agent") -> tuple[str, str]:
    """Insert an IN_PROGRESS task + register a session. Returns (task_id, session_id)."""
    task = TaskRecord(
        id=org.db.next_task_id(),
        assigned_agent=agent,
        team="engineering",
        brief="test-task",
        status=TaskStatus.IN_PROGRESS,
    )
    org.db.insert_task(task)
    session_id = "sid-" + secrets.token_hex(4)
    org.sessions.set_active(task.id, agent, session_id)
    return task.id, session_id


def _make_job(org, task_id: str, agent: str = "dev_agent") -> str:
    """Insert a pending JOB row owned by *task_id*. Returns job_id."""
    job_id = org.db.next_job_id()
    org.db.insert_job(JobRecord(
        id=job_id,
        task_id=task_id,
        agent_name=agent,
        title="test job",
        rationale="need to run something",
        script_text="echo hi",
        interpreter=JobInterpreter.BASH,
        status=JobStatus.PENDING,
        created_at=datetime.now(timezone.utc).isoformat(),
    ))
    return job_id


def _post_completion(client, task_id: str, session_id: str, agent: str, **extra):
    payload = {
        "session_id": session_id,
        "agent": agent,
        "status": "blocked",
        "confidence": 0,
        "output_summary": "waiting on job",
        **extra,
    }
    return client.post(f"/api/v1/orgs/alpha/tasks/{task_id}/completion", json=payload)


# ---------------------------------------------------------------------------
# Original placeholder tests (T7 shape-only) — kept here for coverage.
# ---------------------------------------------------------------------------

def test_completion_report_default_waiting_on_job_ids_is_empty():
    """waiting_on_job_ids defaults to empty list when omitted."""
    report = CompletionReport(
        task_id="TASK-1", agent="a", status="completed",
        confidence=80, output_summary="done",
    )
    assert report.waiting_on_job_ids == []


def test_completion_report_accepts_waiting_on_job_ids():
    """waiting_on_job_ids deserializes from a list of strings."""
    report = CompletionReport(
        task_id="TASK-1", agent="a", status="blocked",
        confidence=0, output_summary="waiting",
        waiting_on_job_ids=["JOB-12", "JOB-13"],
    )
    assert report.waiting_on_job_ids == ["JOB-12", "JOB-13"]


# ---------------------------------------------------------------------------
# Route validation tests — require real FastAPI test client + org fixture.
# ---------------------------------------------------------------------------

def test_completion_blocked_with_unknown_job_returns_404(client_with_runtime):
    """A job id that doesn't exist in the DB → 404 job_not_found."""
    client, org = client_with_runtime
    task_id, session_id = _make_active_task(org)
    resp = _post_completion(
        client, task_id, session_id, "dev_agent",
        waiting_on_job_ids=["JOB-NOPE"],
    )
    assert resp.status_code == 404
    detail = resp.json()["detail"]
    assert detail["code"] == "job_not_found"
    assert detail["job_id"] == "JOB-NOPE"


def test_completion_blocked_with_unowned_job_returns_400(client_with_runtime):
    """A job that exists but belongs to a different task → 400 job_not_owned_by_task."""
    client, org = client_with_runtime
    # task_a owns the job; task_b tries to block on it.
    task_a_id, _ = _make_active_task(org, agent="dev_agent")
    task_b_id, session_b = _make_active_task(org, agent="qa_engineer")
    job_id = _make_job(org, task_a_id, agent="dev_agent")

    resp = _post_completion(
        client, task_b_id, session_b, "qa_engineer",
        waiting_on_job_ids=[job_id],
    )
    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert detail["code"] == "job_not_owned_by_task"
    assert detail["job_id"] == job_id
    assert detail["owner_task_id"] == task_a_id


def test_completion_non_blocked_with_waiting_on_job_ids_returns_400(client_with_runtime):
    """status=completed with waiting_on_job_ids → 400 waiting_on_job_ids_requires_blocked."""
    client, org = client_with_runtime
    task_id, session_id = _make_active_task(org)
    job_id = _make_job(org, task_id)

    resp = client.post(
        f"/api/v1/orgs/alpha/tasks/{task_id}/completion",
        json={
            "session_id": session_id,
            "agent": "dev_agent",
            "status": "completed",
            "confidence": 80,
            "output_summary": "done",
            "waiting_on_job_ids": [job_id],
        },
    )
    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert detail["code"] == "waiting_on_job_ids_requires_blocked"
    assert detail["got_status"] == "completed"


def test_completion_blocked_with_duplicate_jobs_dedupes_server_side(client_with_runtime):
    """Duplicate job ids in waiting_on_job_ids are deduplicated; only one result row stored."""
    client, org = client_with_runtime
    task_id, session_id = _make_active_task(org)
    job_id = _make_job(org, task_id)

    resp = _post_completion(
        client, task_id, session_id, "dev_agent",
        waiting_on_job_ids=[job_id, job_id, job_id],
    )
    assert resp.status_code == 200

    rows = org.db.get_task_results(task_id)
    assert len(rows) == 1
    stored = rows[0].get("waiting_on_job_ids") or []
    assert stored == [job_id]


def test_route_does_not_mutate_task_status(client_with_runtime):
    """Critical: the route persists task_result but does NOT change tasks.status.
    The orchestrator branch (T12) handles the state mutation later."""
    client, org = client_with_runtime
    task_id, session_id = _make_active_task(org)
    job_id = _make_job(org, task_id)

    resp = _post_completion(
        client, task_id, session_id, "dev_agent",
        waiting_on_job_ids=[job_id],
    )
    assert resp.status_code == 200

    # task row must still be IN_PROGRESS — run_step mutates it, not the route.
    task = org.db.get_task(task_id)
    assert task is not None
    assert task.status == TaskStatus.IN_PROGRESS


def test_waiting_on_job_ids_round_trips_via_db(client_with_runtime):
    """The persisted waiting_on_job_ids can be read back correctly from the DB."""
    client, org = client_with_runtime
    task_id, session_id = _make_active_task(org)
    job_id = _make_job(org, task_id)

    resp = _post_completion(
        client, task_id, session_id, "dev_agent",
        waiting_on_job_ids=[job_id],
    )
    assert resp.status_code == 200

    rows = org.db.get_task_results(task_id)
    assert len(rows) == 1
    stored = rows[0].get("waiting_on_job_ids") or []
    assert stored == [job_id]


def test_completion_blocked_without_waiting_on_job_ids_still_works(client_with_runtime):
    """status=blocked with the field ABSENT (not explicit []) is the legacy
    self-escalate path and stays unaffected by the new validation."""
    client, org = client_with_runtime
    task_id, session_id = _make_active_task(org)

    resp = client.post(
        f"/api/v1/orgs/alpha/tasks/{task_id}/completion",
        json={
            "session_id": session_id,
            "agent": "dev_agent",
            "status": "blocked",
            "confidence": 0,
            "output_summary": "waiting for human",
        },
    )
    assert resp.status_code == 200


def test_completion_blocked_with_explicit_empty_list_returns_400(client_with_runtime):
    """status=blocked + EXPLICIT waiting_on_job_ids=[] is malformed — the agent
    set the field but didn't supply ids. Return 400 empty_waiting_on_job_ids
    instead of silently falling through to the legacy escalate path (which
    would mask the bug in the agent's JSON construction)."""
    client, org = client_with_runtime
    task_id, session_id = _make_active_task(org)

    resp = client.post(
        f"/api/v1/orgs/alpha/tasks/{task_id}/completion",
        json={
            "session_id": session_id,
            "agent": "dev_agent",
            "status": "blocked",
            "confidence": 0,
            "output_summary": "waiting",
            "waiting_on_job_ids": [],
        },
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "empty_waiting_on_job_ids"


def test_completion_completed_with_explicit_empty_list_returns_400(client_with_runtime):
    """status=completed + EXPLICIT waiting_on_job_ids=[] is also malformed —
    the agent shouldn't be setting the field on a completed report. Reject
    with the same 400 empty_waiting_on_job_ids; the empty-list check fires
    before the status check (a non-blocked status with an empty list is the
    most surprising case to debug — the explicit-empty signal beats
    waiting_on_job_ids_requires_blocked since the list is empty)."""
    client, org = client_with_runtime
    task_id, session_id = _make_active_task(org)

    resp = client.post(
        f"/api/v1/orgs/alpha/tasks/{task_id}/completion",
        json={
            "session_id": session_id,
            "agent": "dev_agent",
            "status": "completed",
            "confidence": 80,
            "output_summary": "done",
            "waiting_on_job_ids": [],
        },
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "empty_waiting_on_job_ids"


def test_completion_multiple_valid_jobs_accepted(client_with_runtime):
    """Multiple distinct owned jobs are accepted and stored in sorted order."""
    client, org = client_with_runtime
    task_id, session_id = _make_active_task(org)
    job_id_1 = _make_job(org, task_id)
    job_id_2 = _make_job(org, task_id)

    resp = _post_completion(
        client, task_id, session_id, "dev_agent",
        waiting_on_job_ids=[job_id_2, job_id_1],  # unsorted on purpose
    )
    assert resp.status_code == 200

    rows = org.db.get_task_results(task_id)
    assert len(rows) == 1
    stored = rows[0].get("waiting_on_job_ids") or []
    # Server dedupes + sorts
    assert stored == sorted([job_id_1, job_id_2])
