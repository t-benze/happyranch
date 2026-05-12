from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest


def _build_org_state(tmp_path: Path):
    from src.infrastructure.database import Database
    from src.models import TaskRecord, TaskStatus

    db = Database(tmp_path / "opc.db")
    db.insert_task(TaskRecord(
        id="TASK-9", brief="x", team="engineering",
        assigned_agent="m", status=TaskStatus.FAILED,
    ))
    db.mint_escalation_notification(
        feishu_message_id="om_root", org_slug="acme", task_id="TASK-9",
        chat_id="oc_xyz",
        expires_at=datetime(2099, 1, 1, tzinfo=timezone.utc),
        kind="failure",
    )
    org = MagicMock()
    org.db = db
    org.slug = "acme"
    org.db_lock = asyncio.Lock()
    org.orchestrator = MagicMock()
    state = MagicMock()
    state.is_idle = False
    state.queue = MagicMock()
    return org, state, db


@pytest.mark.asyncio
async def test_revisit_with_cli_actor_consumes_failure_notification(tmp_path: Path):
    from src.daemon.routes.tasks import revisit_from_notification
    org, state, db = _build_org_state(tmp_path)

    await revisit_from_notification(
        org, state, task_id="TASK-9", founder_note="retry it", actor="cli",
    )

    row = db.get_escalation_notification("om_root")
    assert row["consumed_at"] is not None
    assert row["consumed_by"] == "cli-fallback"


@pytest.mark.asyncio
async def test_revisit_with_feishu_actor_does_not_consume(tmp_path: Path):
    """The listener consumes the row separately at step 8r. The helper
    must NOT also try to consume — would race with the listener consume."""
    from src.daemon.routes.tasks import revisit_from_notification
    org, state, db = _build_org_state(tmp_path)

    await revisit_from_notification(
        org, state, task_id="TASK-9", founder_note="retry it",
        actor="feishu-reply",
    )

    row = db.get_escalation_notification("om_root")
    assert row["consumed_at"] is None  # listener will consume separately
    assert row["consumed_by"] is None


@pytest.mark.asyncio
async def test_revisit_with_cli_actor_does_not_consume_escalation_kind(tmp_path: Path):
    """Only kind='failure' notifications should be consumed via the
    cli-fallback path. kind='escalation' rows are owned by
    resolve_escalation_in_process's own consume hook — leave them alone."""
    from src.daemon.routes.tasks import revisit_from_notification
    org, state, db = _build_org_state(tmp_path)
    # Add an escalation notification for the same task
    db.mint_escalation_notification(
        feishu_message_id="om_esc_root", org_slug="acme", task_id="TASK-9",
        chat_id="oc_xyz",
        expires_at=datetime(2099, 1, 1, tzinfo=timezone.utc),
        kind="escalation",  # NOT failure
    )

    await revisit_from_notification(
        org, state, task_id="TASK-9", founder_note="x", actor="cli",
    )

    failure_row = db.get_escalation_notification("om_root")
    escalation_row = db.get_escalation_notification("om_esc_root")
    assert failure_row["consumed_at"] is not None
    assert failure_row["consumed_by"] == "cli-fallback"
    assert escalation_row["consumed_at"] is None  # untouched
