from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from runtime.infrastructure.database import Database
from runtime.infrastructure.audit_logger import AuditLogger
from runtime.daemon.feishu_listener import FeishuEventListener
from runtime.models import TaskRecord, TaskStatus, BlockKind


def _mk_listener(tmp_path: Path):
    db = Database(tmp_path / "happyranch.db")
    audit = AuditLogger(db)
    loop = asyncio.new_event_loop()

    # Mock returns a RevisitResult-shape object (Task 10's contract).
    revisit_result = SimpleNamespace(
        new_root_id="TASK-REVISIT",
        predecessor_root_id="TASK-1",
        flagged_task_id="TASK-1",
        cascade=["TASK-1"],
        prior_status="failed",
    )
    return FeishuEventListener(
        slug="acme", db=db, audit=audit, chat_id="oc_xyz",
        resolve_escalation=AsyncMock(),
        revisit_from_notification=AsyncMock(return_value=revisit_result),
        dispatch_via_feishu=AsyncMock(),
        send_dispatch_confirmation=AsyncMock(),
        send_dispatch_error=AsyncMock(),
        allow_dispatch=False,
        loop=loop, app_id="x", app_secret="x", domain="feishu",
    ), db, loop


def _mk_notification(db: Database, *, kind: str, task_id: str = "TASK-1"):
    expires = datetime(2099, 1, 1, tzinfo=timezone.utc)
    db.mint_escalation_notification(
        feishu_message_id="om_root", org_slug="acme", task_id=task_id,
        chat_id="oc_xyz", expires_at=expires,
        kind=kind,
    )


def _mk_event(text: str, root_id: str = "om_root"):
    import json
    msg = SimpleNamespace(
        chat_id="oc_xyz", message_id="om_in", root_id=root_id,
        message_type="text",
        content=json.dumps({"text": text}),
    )
    sender = SimpleNamespace(
        sender_type="user",
        sender_id=SimpleNamespace(open_id="ou_user_1"),
    )
    return SimpleNamespace(
        header=SimpleNamespace(event_id="evt_1"),
        event=SimpleNamespace(message=msg, sender=sender),
    )


def _insert_escalated_task(db: Database, task_id: str = "TASK-1"):
    db.insert_task(TaskRecord(
        id=task_id, brief="x", team="engineering",
        assigned_agent="m", status=TaskStatus.BLOCKED,
        block_kind=BlockKind.ESCALATED,
    ))


def _insert_failed_task(db: Database, task_id: str = "TASK-1"):
    db.insert_task(TaskRecord(
        id=task_id, brief="x", team="engineering",
        assigned_agent="m", status=TaskStatus.FAILED,
    ))


def test_escalation_approve_routes_to_resolve(tmp_path: Path):
    l, db, loop = _mk_listener(tmp_path)
    _insert_escalated_task(db)
    _mk_notification(db, kind="escalation")
    loop.run_until_complete(l._handle_event_async(_mk_event("APPROVE\nok")))
    assert l._resolve_escalation.called
    assert not l._revisit_from_notification.called


def test_escalation_revisit_is_verb_mismatch(tmp_path: Path):
    l, db, loop = _mk_listener(tmp_path)
    _insert_escalated_task(db)
    _mk_notification(db, kind="escalation")
    loop.run_until_complete(l._handle_event_async(_mk_event("REVISIT\nplease")))
    assert not l._resolve_escalation.called
    assert not l._revisit_from_notification.called
    # Notification still unconsumed
    row = db.get_escalation_notification("om_root")
    assert row["consumed_at"] is None


def test_failure_revisit_routes_to_revisit_helper(tmp_path: Path):
    l, db, loop = _mk_listener(tmp_path)
    _insert_failed_task(db)
    _mk_notification(db, kind="failure")
    loop.run_until_complete(l._handle_event_async(_mk_event("REVISIT\nadd field")))
    assert l._revisit_from_notification.called
    kwargs = l._revisit_from_notification.call_args.kwargs
    assert kwargs["task_id"] == "TASK-1"
    assert kwargs["founder_note"] == "add field"
    assert kwargs["actor"] == "feishu-reply"
    row = db.get_escalation_notification("om_root")
    assert row["consumed_at"] is not None
    assert row["consumed_by"] == "feishu-reply"


def test_failure_approve_is_verb_mismatch(tmp_path: Path):
    l, db, loop = _mk_listener(tmp_path)
    _insert_failed_task(db)
    _mk_notification(db, kind="failure")
    loop.run_until_complete(l._handle_event_async(_mk_event("APPROVE\nyes")))
    assert not l._revisit_from_notification.called
    assert not l._resolve_escalation.called
    row = db.get_escalation_notification("om_root")
    assert row["consumed_at"] is None  # unconsumed


def test_failure_revisit_cannot_revisit_unconsumes(tmp_path: Path):
    """If revisit_from_notification raises with cannot_revisit, the
    notification row stays UNCONSUMED so the founder can fall back to CLI."""
    from fastapi import HTTPException
    l, db, loop = _mk_listener(tmp_path)
    _insert_failed_task(db)
    _mk_notification(db, kind="failure")
    # Make the mock raise cannot_revisit
    l._revisit_from_notification = AsyncMock(
        side_effect=HTTPException(
            status_code=409,
            detail={"code": "cannot_revisit", "current_status": "in_progress"},
        ),
    )
    loop.run_until_complete(l._handle_event_async(_mk_event("REVISIT\nplease")))
    row = db.get_escalation_notification("om_root")
    assert row["consumed_at"] is None  # unconsumed — preserves founder's intent
