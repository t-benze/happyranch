"""Unit tests for FeishuEventListener._handle_event_async.

The handler is the only piece of the listener that has logic; the WS thread
itself is treated as I/O the SDK owns. Tests construct event payload objects
that mimic lark_oapi's P2ImMessageReceiveV1 shape and invoke the handler
directly (no real WebSocket).
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.daemon.feishu_listener import FeishuEventListener
from src.infrastructure.audit_logger import AuditLogger
from src.infrastructure.database import Database


def _event(
    *,
    event_id: str = "evt_1",
    chat_id: str = "oc_target",
    root_id: str | None = "om_target",
    sender_type: str = "user",
    msg_type: str = "text",
    content: str = '{"text": "APPROVE\\nfine"}',
    msg_id: str = "om_reply",
):
    return SimpleNamespace(
        header=SimpleNamespace(event_id=event_id),
        event=SimpleNamespace(
            sender=SimpleNamespace(sender_type=sender_type),
            message=SimpleNamespace(
                message_id=msg_id,
                chat_id=chat_id,
                root_id=root_id,
                message_type=msg_type,
                content=content,
            ),
        ),
    )


def _seed_notification(
    db: Database,
    *,
    feishu_message_id: str = "om_target",
    task_id: str = "TASK-1",
    expires_at: datetime | None = None,
) -> None:
    from src.models import TaskRecord
    db.insert_task(TaskRecord(id=task_id, team="engineering", brief="b"))
    expires = expires_at or datetime.now(timezone.utc) + timedelta(hours=72)
    db.mint_escalation_notification(
        feishu_message_id=feishu_message_id,
        org_slug="o", task_id=task_id, chat_id="oc_target",
        expires_at=expires,
    )


@pytest.fixture
def listener(tmp_path):
    db = Database(tmp_path / "opc.db")
    resolve_mock = AsyncMock()
    listener = FeishuEventListener(
        slug="o", db=db, audit=AuditLogger(db),
        chat_id="oc_target",
        resolve_escalation=resolve_mock,
        loop=asyncio.get_event_loop(),
        app_id="cli_x", app_secret="s_x", domain="https://x",
    )
    return listener, db, resolve_mock


@pytest.mark.asyncio
async def test_handler_calls_resolve_on_approve(listener):
    listener_obj, db, resolve_mock = listener
    _seed_notification(db)
    await listener_obj._handle_event_async(_event())
    resolve_mock.assert_awaited_once()
    args, kwargs = resolve_mock.await_args
    assert kwargs["task_id"] == "TASK-1"
    assert kwargs["decision"] == "approve"
    assert kwargs["rationale"] == "fine"
    row = db.get_escalation_notification("om_target")
    assert row["consumed_at"] is not None
    actions = [r["action"] for r in db.get_audit_logs("TASK-1")]
    assert "escalation_reply_processed" in actions


@pytest.mark.asyncio
async def test_handler_dedups_redelivered_event(listener):
    listener_obj, db, resolve_mock = listener
    _seed_notification(db)
    await listener_obj._handle_event_async(_event(event_id="evt_dup"))
    await listener_obj._handle_event_async(_event(event_id="evt_dup"))
    assert resolve_mock.await_count == 1


@pytest.mark.asyncio
async def test_handler_drops_wrong_chat(listener):
    listener_obj, db, resolve_mock = listener
    _seed_notification(db)
    await listener_obj._handle_event_async(_event(chat_id="oc_other"))
    resolve_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_handler_drops_no_root_id(listener):
    listener_obj, db, resolve_mock = listener
    _seed_notification(db)
    await listener_obj._handle_event_async(_event(root_id=None))
    resolve_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_handler_drops_app_sender(listener):
    listener_obj, db, resolve_mock = listener
    _seed_notification(db)
    await listener_obj._handle_event_async(_event(sender_type="app"))
    resolve_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_handler_drops_unknown_root(listener):
    listener_obj, db, resolve_mock = listener
    # No notification seeded; root_id won't match anything.
    await listener_obj._handle_event_async(_event())
    resolve_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_handler_drops_consumed_notification(listener):
    listener_obj, db, resolve_mock = listener
    _seed_notification(db)
    db.consume_escalation_notification("om_target", consumed_by="cli-fallback")
    await listener_obj._handle_event_async(_event())
    resolve_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_handler_drops_expired_notification(listener):
    listener_obj, db, resolve_mock = listener
    past = datetime.now(timezone.utc) - timedelta(hours=1)
    _seed_notification(db, expires_at=past)
    await listener_obj._handle_event_async(_event())
    resolve_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_handler_drops_bad_decision(listener):
    listener_obj, db, resolve_mock = listener
    _seed_notification(db)
    await listener_obj._handle_event_async(_event(
        content='{"text": "MAYBE\\nnot sure"}',
    ))
    resolve_mock.assert_not_awaited()
    actions = [r["action"] for r in db.get_audit_logs("TASK-1")]
    assert "escalation_reply_rejected" in actions


@pytest.mark.asyncio
async def test_handler_handles_post_message(listener):
    listener_obj, db, resolve_mock = listener
    _seed_notification(db)
    post_content = json.dumps({
        "zh_cn": {
            "title": "",
            "content": [
                [{"tag": "text", "text": "APPROVE"}],
                [{"tag": "text", "text": "shipping it"}],
            ],
        }
    })
    await listener_obj._handle_event_async(_event(
        msg_type="post", content=post_content,
    ))
    resolve_mock.assert_awaited_once()
    kwargs = resolve_mock.await_args.kwargs
    assert kwargs["decision"] == "approve"
    assert kwargs["rationale"] == "shipping it"


@pytest.mark.asyncio
async def test_handler_records_consumed_outcome(listener):
    listener_obj, db, resolve_mock = listener
    _seed_notification(db)
    await listener_obj._handle_event_async(_event())
    cur = db._conn.execute(
        "SELECT outcome, reason FROM processed_event_ids "
        "WHERE feishu_event_id = ?", ("evt_1",),
    )
    row = cur.fetchone()
    assert row["outcome"] == "consumed"
    assert row["reason"] is None


@pytest.mark.asyncio
async def test_handler_records_wrong_chat_ignored(listener):
    listener_obj, db, resolve_mock = listener
    _seed_notification(db)
    await listener_obj._handle_event_async(_event(chat_id="oc_other"))
    cur = db._conn.execute(
        "SELECT outcome, reason FROM processed_event_ids "
        "WHERE feishu_event_id = ?", ("evt_1",),
    )
    row = cur.fetchone()
    assert row["outcome"] == "ignored"
    assert row["reason"] == "wrong_chat"


@pytest.mark.asyncio
async def test_handler_records_bad_decision_rejected(listener):
    listener_obj, db, resolve_mock = listener
    _seed_notification(db)
    await listener_obj._handle_event_async(_event(
        content='{"text": "MAYBE\\nnot sure"}',
    ))
    cur = db._conn.execute(
        "SELECT outcome, reason FROM processed_event_ids "
        "WHERE feishu_event_id = ?", ("evt_1",),
    )
    row = cur.fetchone()
    assert row["outcome"] == "rejected"
    assert row["reason"] == "bad_decision"
