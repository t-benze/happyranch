"""In-process helpers extracted from scripts route handlers (Task 5)."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from runtime.daemon.event_bus import EventBus
from runtime.daemon.routes.jobs import (
    reject_job_from_notification,
)
from runtime.daemon.sessions import SessionTracker
from runtime.infrastructure.database import Database
from runtime.models import (
    JobInterpreter,
    JobRecord,
    JobStatus,
)


def _insert_pending_sr(org, job_id: str = "SR-001") -> None:
    org.db.insert_job(JobRecord(
        id=job_id, task_id="TASK-1", agent_name="dev",
        title="t", rationale="r", script_text="echo hi",
        interpreter=JobInterpreter.BASH,
        cwd_hint=None,
        status=JobStatus.PENDING,
        created_at="2026-05-25T00:00:00Z",
    ))


@pytest.fixture()
def scripts_test_org(tmp_path):
    """Minimal in-memory org-like object exposing the attributes the helpers
    touch: slug, root, db, db_lock, sessions, event_bus.
    """
    root = tmp_path / "orgs" / "acme"
    (root / "scripts").mkdir(parents=True)
    (root / "workspaces" / "dev").mkdir(parents=True)
    db = Database(root / "happyranch.db")
    org = SimpleNamespace(
        slug="acme",
        root=root,
        db=db,
        db_lock=asyncio.Lock(),
        sessions=SessionTracker(),
        event_bus=EventBus(history_loader=lambda _tid: []),
        orchestrator=None,
    )
    return org


@pytest.mark.asyncio
async def test_reject_helper_transitions_to_rejected(scripts_test_org):
    org = scripts_test_org
    _insert_pending_sr(org)
    result = await reject_job_from_notification(
        org, job_id="SR-001", reason="not a fit",
    )
    assert result.status == JobStatus.REJECTED
    assert result.reject_reason == "not a fit"


@pytest.mark.asyncio
async def test_reject_helper_404_when_missing(scripts_test_org):
    org = scripts_test_org
    with pytest.raises(HTTPException) as exc:
        await reject_job_from_notification(
            org, job_id="SR-999", reason="x",
        )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_reject_helper_409_when_not_pending(scripts_test_org):
    org = scripts_test_org
    _insert_pending_sr(org)
    org.db.transition_job_to_rejected(
        "SR-001", reviewer="founder", reason="prior",
        reviewed_at="2026-05-25T00:00:00Z",
    )
    with pytest.raises(HTTPException) as exc:
        await reject_job_from_notification(
            org, job_id="SR-001", reason="late",
        )
    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "not_pending"



