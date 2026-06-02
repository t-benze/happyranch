from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock

import pytest


@pytest.fixture()
def org_with_failed_task(tmp_path: Path):
    from runtime.infrastructure.database import Database
    from runtime.models import TaskRecord, TaskStatus

    db = Database(tmp_path / "happyranch.db")
    db.insert_task(TaskRecord(
        id="TASK-1", brief="ferry scraper", team="engineering",
        assigned_agent="manager", status=TaskStatus.FAILED,
    ))
    org = MagicMock()
    org.db = db
    org.slug = "acme"
    org.db_lock = asyncio.Lock()
    org.orchestrator = MagicMock()
    state = MagicMock()
    state.queue = MagicMock()
    state.is_idle = False
    return org, state, db


@pytest.mark.asyncio
async def test_revisit_from_notification_spawns_new_root(org_with_failed_task):
    from runtime.daemon.routes.tasks import revisit_from_notification
    org, state, db = org_with_failed_task
    result = await revisit_from_notification(
        org, state,
        task_id="TASK-1",
        founder_note="add Service Class field",
        actor="feishu-reply",
    )
    assert result.new_root_id != "TASK-1"
    assert result.predecessor_root_id == "TASK-1"
    assert result.flagged_task_id == "TASK-1"
    assert result.cascade == ["TASK-1"]
    assert result.prior_status == "failed"
    new_task = db.get_task(result.new_root_id)
    assert new_task is not None
    assert new_task.revisit_of_task_id == "TASK-1"
    assert new_task.brief == "ferry scraper"  # inherited
    assert new_task.team == "engineering"
    audit_rows = db.get_audit_logs(result.new_root_id)
    revisit_of = [r for r in audit_rows if r["action"] == "revisit_of"]
    assert len(revisit_of) == 1
    payload = revisit_of[0]["payload"]
    assert payload.get("actor") == "feishu-reply"
    assert payload.get("founder_note") == "add Service Class field"


@pytest.mark.asyncio
async def test_revisit_from_notification_raises_when_ineligible(org_with_failed_task):
    from fastapi import HTTPException
    from runtime.daemon.routes.tasks import revisit_from_notification
    from runtime.models import TaskStatus
    org, state, db = org_with_failed_task
    db.update_task("TASK-1", status=TaskStatus.IN_PROGRESS)
    with pytest.raises(HTTPException) as exc:
        await revisit_from_notification(
            org, state, task_id="TASK-1", founder_note="x", actor="feishu-reply",
        )
    assert exc.value.status_code == 409
    assert "cannot_revisit" in str(exc.value.detail)
