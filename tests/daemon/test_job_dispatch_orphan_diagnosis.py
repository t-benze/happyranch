"""Diagnosis regression tests: the historical submission-to-dispatch gap.

Durable evidence (audit trail) shows the historical orphans (happyranch
JOB-155/191/193/201; tourism JOB-002/003/004) all share one shape: the job row
was inserted ``pending`` with ``started_at IS NULL`` and never transitioned to
``running``. This file proves, against the CURRENT code, the exact mechanism
behind that shape: on the auto-run submission path, when the synchronous
dispatch handoff (``_run_job_core``) fails validation — e.g. a ``cwd_hint``
that resolves to a missing directory → ``409 cwd_missing`` — the submit route
re-raises to the caller and leaves the row permanently ``pending`` with no
terminal transition and no failure audit. The caller re-submits with a
corrected hint and the orphan row silently accumulates.

This is the regression fixture for the durable historical stages; the
all-org observer must detect such a row once it crosses the age threshold.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

from runtime.daemon.stale_pending_jobs import (
    STALE_PENDING_JOB_MAX_AGE,
    scan_org_stale_pending,
)
from runtime.models import JobStatus, TaskRecord, TaskStatus


def _z(iso: datetime) -> str:
    return iso.isoformat().replace("+00:00", "Z")


def _make_active_session(org, agent: str = "engineering_head"):
    task = TaskRecord(
        id=org.db.next_task_id(),
        assigned_agent=agent,
        team="engineering",
        brief="diagnosis fixture",
        status=TaskStatus.IN_PROGRESS,
    )
    org.db.insert_task(task)
    session_id = "sid-" + secrets.token_hex(4)
    org.sessions.set_active(task.id, agent, session_id)
    return task.id, session_id


def test_auto_run_dispatch_validation_failure_strands_pending_row(client_with_runtime):
    """The historical orphan mechanism, reproduced on current code: a bad
    cwd_hint makes the dispatch handoff fail synchronously, the submit route
    surfaces 409 to the caller, and the row is left pending-never-started
    with NO terminal transition and NO failure audit."""
    client, org = client_with_runtime
    task_id, session_id = _make_active_session(org)
    r = client.post(
        "/api/v1/orgs/alpha/jobs/submit",
        json={
            "task_id": task_id,
            "session_id": session_id,
            "title": "orphan reproduction",
            "rationale": "",
            "script": "echo hi",
            "interpreter": "bash",
            "cwd_hint": "no/such/dir",
        },
    )
    # The caller sees a dispatch failure (this is the historical 409 the
    # agents hit; they re-submitted with corrected hints as JOB-156/192/194/202).
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "cwd_missing"

    # The row exists and is stranded: pending, never started.
    rows = org.db.list_jobs_db(task_id=task_id)
    assert len(rows) == 1
    stranded = rows[0]
    assert stranded.status == JobStatus.PENDING
    assert stranded.started_at is None
    assert stranded.finished_at is None
    assert stranded.reason is None

    # No dispatch audit and no failure audit — the handoff gap is silent.
    actions = [a["action"] for a in org.db.get_audit_logs(task_id)]
    assert "job_submitted" in actions
    assert "job_auto_started" not in actions
    assert "job_run_started" not in actions
    assert "job_run_failed" not in actions

    # The observer detects it once it crosses the age threshold (backdate the
    # created_at the way history aged these rows; observation is read-only).
    org.db.execute(
        "UPDATE jobs SET created_at = ? WHERE id = ?",
        (_z(datetime.now(timezone.utc) - STALE_PENDING_JOB_MAX_AGE - timedelta(days=1)),
         stranded.id),
    )
    findings = scan_org_stale_pending(org.db, now=datetime.now(timezone.utc))
    assert [f["id"] for f in findings] == [stranded.id]
    assert findings[0]["task_id"] == task_id


def test_review_required_submission_stays_pending_awaiting_founder(client_with_runtime):
    """The OTHER historical shape: review-gated submissions stay pending by
    design until the founder acts. They are NOT dispatch failures, but once
    they outlive the threshold the observer still surfaces them so the
    consumer can distinguish abandoned (terminal task) from merely slow."""
    client, org = client_with_runtime
    task_id, session_id = _make_active_session(org)
    r = client.post(
        "/api/v1/orgs/alpha/jobs/submit",
        json={
            "task_id": task_id,
            "session_id": session_id,
            "title": "review gate reproduction",
            "rationale": "needs founder",
            "script": "echo hi",
            "interpreter": "bash",
            "review_required": True,
        },
    )
    assert r.status_code == 201
    assert r.json()["status"] == "pending"
    rows = org.db.list_jobs_db(task_id=task_id)
    assert len(rows) == 1
    assert rows[0].status == JobStatus.PENDING
    assert rows[0].started_at is None

    # Fresh review-gated job: observer excludes it (below threshold).
    assert scan_org_stale_pending(org.db, now=datetime.now(timezone.utc)) == []
