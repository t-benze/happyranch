from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.infrastructure.database import Database
from src.infrastructure.audit_logger import AuditLogger
from src.daemon.feishu_listener import FeishuEventListener
from src.daemon.routes.tasks import DispatchError


def _mk_listener(tmp_path: Path, *, allow_dispatch: bool = True):
    db = Database(tmp_path / "grassland.db")
    audit = AuditLogger(db)
    loop = asyncio.new_event_loop()
    return FeishuEventListener(
        slug="acme", db=db, audit=audit, chat_id="oc_xyz",
        resolve_escalation=AsyncMock(),
        revisit_from_notification=AsyncMock(),
        dispatch_via_feishu=AsyncMock(return_value=("TASK-DISP", "engineering")),
        send_dispatch_confirmation=AsyncMock(),
        send_dispatch_error=AsyncMock(),
        allow_dispatch=allow_dispatch,
        loop=loop, app_id="x", app_secret="x", domain="feishu",
    ), db, loop


def _mk_event(text: str, *, sender_type: str = "user", event_id: str = "evt_1"):
    """Build a mock event with the lark-oapi nested envelope shape."""
    msg = SimpleNamespace(
        chat_id="oc_xyz", message_id="om_in", root_id=None,
        message_type="text", content=json.dumps({"text": text}),
    )
    sender = SimpleNamespace(
        sender_type=sender_type,
        sender_id=SimpleNamespace(open_id="ou_user_1"),
    )
    return SimpleNamespace(
        header=SimpleNamespace(event_id=event_id),
        event=SimpleNamespace(message=msg, sender=sender),
    )


def test_dispatch_success_sends_confirmation(tmp_path: Path):
    l, db, loop = _mk_listener(tmp_path)
    loop.run_until_complete(l._handle_event_async(
        _mk_event("DISPATCH engineering\nfix the thing")
    ))
    assert l._dispatch_via_feishu.called
    assert l._send_dispatch_confirmation.called
    confirm_kwargs = l._send_dispatch_confirmation.call_args.kwargs
    assert confirm_kwargs["task_id"] == "TASK-DISP"
    assert confirm_kwargs["team"] == "engineering"


def test_dispatch_empty_brief_sends_error(tmp_path: Path):
    l, db, loop = _mk_listener(tmp_path)
    l._dispatch_via_feishu.side_effect = DispatchError("empty_brief")
    loop.run_until_complete(l._handle_event_async(
        _mk_event("DISPATCH engineering\nactual brief here")
    ))
    assert l._send_dispatch_error.called
    err_kwargs = l._send_dispatch_error.call_args.kwargs
    assert "empty_brief" in err_kwargs["reason"]


def test_dispatch_unknown_team_lists_valid(tmp_path: Path):
    l, db, loop = _mk_listener(tmp_path)
    l._dispatch_via_feishu.side_effect = DispatchError(
        "unknown_team", valid_teams=["engineering", "customer-care"],
    )
    loop.run_until_complete(l._handle_event_async(
        _mk_event("DISPATCH wrongteam\nbrief")
    ))
    assert l._send_dispatch_error.called
    err_kwargs = l._send_dispatch_error.call_args.kwargs
    assert "unknown team" in err_kwargs["reason"]
    assert "wrongteam" in err_kwargs["reason"]
    assert "engineering" in err_kwargs["valid_teams"]


def test_dispatch_confirmation_send_failure_swallowed(tmp_path: Path):
    """Confirmation send failure must NOT propagate (task was already created)."""
    l, db, loop = _mk_listener(tmp_path)
    l._send_dispatch_confirmation.side_effect = RuntimeError("feishu down")
    # Must not raise
    loop.run_until_complete(l._handle_event_async(
        _mk_event("DISPATCH engineering\nbrief")
    ))
    assert l._dispatch_via_feishu.called  # task was created


def test_dispatch_system_sender_is_dropped(tmp_path: Path):
    """sender_type='system' must be dropped on the dispatch branch — only
    'user' senders are accepted (matches the reply-branch filter)."""
    l, db, loop = _mk_listener(tmp_path)
    loop.run_until_complete(l._handle_event_async(
        _mk_event("DISPATCH engineering\nfix the thing",
                  sender_type="system", event_id="evt_sys")
    ))
    # No task should be created and no confirmation sent
    assert not l._dispatch_via_feishu.called
    assert not l._send_dispatch_confirmation.called
    # Event must be recorded as ignored (not_user_sender)
    cur = db._conn.execute(
        "SELECT outcome, reason FROM processed_event_ids "
        "WHERE feishu_event_id = ?", ("evt_sys",),
    )
    row = cur.fetchone()
    assert row["outcome"] == "ignored"
    assert row["reason"] == "not_user_sender"
