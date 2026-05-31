from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.infrastructure.database import Database
from src.infrastructure.audit_logger import AuditLogger


def _make_event(*, root_id: str | None, text: str, sender_type: str = "user", event_id: str = "evt_1"):
    """Build a mock event mimicking the lark-oapi P2ImMessageReceiveV1 shape."""
    content = json.dumps({"text": text})
    msg = SimpleNamespace(
        chat_id="oc_xyz",
        message_id="om_inbound_1",
        root_id=root_id,
        message_type="text",
        content=content,
    )
    sender = SimpleNamespace(
        sender_type=sender_type,
        sender_id=SimpleNamespace(open_id="ou_user_1"),
    )
    return SimpleNamespace(
        header=SimpleNamespace(event_id=event_id),
        event=SimpleNamespace(
            message=msg,
            sender=sender,
        ),
    )


@pytest.fixture()
def listener(tmp_path: Path):
    from src.daemon.feishu_listener import FeishuEventListener
    db = Database(tmp_path / "happyranch.db")
    audit = AuditLogger(db)
    loop = asyncio.new_event_loop()
    l = FeishuEventListener(
        slug="acme", db=db, audit=audit, chat_id="oc_xyz",
        resolve_escalation=AsyncMock(),
        revisit_from_notification=AsyncMock(return_value=object()),
        dispatch_via_feishu=AsyncMock(return_value=("TASK-DISP", "engineering")),
        send_dispatch_confirmation=AsyncMock(),
        send_dispatch_error=AsyncMock(),
        allow_dispatch=True,
        loop=loop, app_id="x", app_secret="x", domain="https://feishu.cn",
    )
    return l, db, loop


def test_top_level_dispatch_branch_taken(listener):
    """root_id is None + allow_dispatch=True → dispatch branch is taken
    (we verify by checking that resolve_escalation wasn't called, since
    the dispatch branch stub does not call it)."""
    l, db, loop = listener
    event = _make_event(root_id=None, text="DISPATCH engineering\nbrief here")
    loop.run_until_complete(l._handle_event_async(event))
    # Verify the listener took the dispatch branch, not the reply branch:
    # resolve_escalation should NOT have been called.
    assert not l._resolve_escalation.called


def test_top_level_dispatch_dropped_when_disabled(tmp_path: Path):
    from src.daemon.feishu_listener import FeishuEventListener
    db = Database(tmp_path / "happyranch.db")
    audit = AuditLogger(db)
    loop = asyncio.new_event_loop()
    l = FeishuEventListener(
        slug="acme", db=db, audit=audit, chat_id="oc_xyz",
        resolve_escalation=AsyncMock(),
        revisit_from_notification=AsyncMock(),
        dispatch_via_feishu=AsyncMock(),
        send_dispatch_confirmation=AsyncMock(),
        send_dispatch_error=AsyncMock(),
        allow_dispatch=False,  # OFF
        loop=loop, app_id="x", app_secret="x", domain="https://feishu.cn",
    )
    event = _make_event(root_id=None, text="DISPATCH engineering\nbrief")
    loop.run_until_complete(l._handle_event_async(event))
    assert not l._dispatch_via_feishu.called
    assert not l._resolve_escalation.called


def test_threaded_reply_takes_reply_branch(listener):
    """root_id is set → reply branch taken; dispatch helper NOT called."""
    from datetime import timedelta
    from src.models import TaskRecord, TaskStatus, BlockKind
    l, db, loop = listener
    expires = datetime.now(timezone.utc) + timedelta(hours=72)
    db.mint_escalation_notification(
        feishu_message_id="om_root", org_slug="acme", task_id="TASK-1",
        chat_id="oc_xyz", expires_at=expires,
    )
    db.insert_task(TaskRecord(
        id="TASK-1", brief="x", team="engineering",
        assigned_agent="m", status=TaskStatus.BLOCKED,
        block_kind=BlockKind.ESCALATED,
    ))
    event = _make_event(root_id="om_root", text="APPROVE\nok")
    loop.run_until_complete(l._handle_event_async(event))
    assert not l._dispatch_via_feishu.called
    assert l._resolve_escalation.called  # threaded reply routes to resolve_escalation
