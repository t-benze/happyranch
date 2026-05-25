"""In-process helpers extracted from scripts route handlers (Task 5)."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from src.daemon.event_bus import EventBus
from src.daemon.routes.scripts import (
    reject_script_from_notification,
    run_script_from_notification,
)
from src.daemon.sessions import SessionTracker
from src.infrastructure.database import Database
from src.models import (
    ScriptInterpreter,
    ScriptRequestRecord,
    ScriptRequestStatus,
)


def _insert_pending_sr(org, sr_id: str = "SR-001") -> None:
    org.db.insert_script_request(ScriptRequestRecord(
        id=sr_id, task_id="TASK-1", agent_name="dev",
        title="t", rationale="r", script_text="echo hi",
        interpreter=ScriptInterpreter.BASH,
        cwd_hint=None,
        status=ScriptRequestStatus.PENDING,
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
    db = Database(root / "grassland.db")
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
    result = await reject_script_from_notification(
        org, sr_id="SR-001", reason="not a fit",
    )
    assert result.status == ScriptRequestStatus.REJECTED
    assert result.reject_reason == "not a fit"


@pytest.mark.asyncio
async def test_reject_helper_404_when_missing(scripts_test_org):
    org = scripts_test_org
    with pytest.raises(HTTPException) as exc:
        await reject_script_from_notification(
            org, sr_id="SR-999", reason="x",
        )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_reject_helper_409_when_not_pending(scripts_test_org):
    org = scripts_test_org
    _insert_pending_sr(org)
    org.db.transition_script_to_rejected(
        "SR-001", reviewer="founder", reason="prior",
        reviewed_at="2026-05-25T00:00:00Z",
    )
    with pytest.raises(HTTPException) as exc:
        await reject_script_from_notification(
            org, sr_id="SR-001", reason="late",
        )
    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "not_pending"


@pytest.mark.asyncio
async def test_run_helper_transitions_to_running(scripts_test_org, monkeypatch):
    org = scripts_test_org
    _insert_pending_sr(org)

    async def _fake_spawn(**kw):
        from src.daemon.scripts_runner import RunResult
        return RunResult(
            status="completed", exit_code=0, duration_ms=10,
            stdout_head="ok", stderr_head=None,
            stdout_bytes=2, stderr_bytes=0,
            truncated_stdout=False, truncated_stderr=False,
            reason=None,
        )
    monkeypatch.setattr("src.daemon.routes.scripts._spawn_script", _fake_spawn)

    result = await run_script_from_notification(
        org, sr_id="SR-001", actor="feishu-reply", founder_note="ok",
    )
    assert result["status"] == "running"
    assert result["id"] == "SR-001"

    for _ in range(40):
        rec = org.db.get_script_request("SR-001")
        if rec.status != ScriptRequestStatus.RUNNING:
            break
        await asyncio.sleep(0.05)
    rec = org.db.get_script_request("SR-001")
    assert rec.status in (
        ScriptRequestStatus.COMPLETED, ScriptRequestStatus.FAILED,
    )


@pytest.mark.asyncio
async def test_run_helper_404_when_missing(scripts_test_org):
    org = scripts_test_org
    with pytest.raises(HTTPException) as exc:
        await run_script_from_notification(
            org, sr_id="SR-999", actor="feishu-reply", founder_note="ok",
        )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_run_helper_409_when_not_pending(scripts_test_org):
    org = scripts_test_org
    _insert_pending_sr(org)
    org.db.transition_script_to_rejected(
        "SR-001", reviewer="founder", reason="x",
        reviewed_at="2026-05-25T00:00:00Z",
    )
    with pytest.raises(HTTPException) as exc:
        await run_script_from_notification(
            org, sr_id="SR-001", actor="feishu-reply", founder_note="ok",
        )
    assert exc.value.status_code == 409
