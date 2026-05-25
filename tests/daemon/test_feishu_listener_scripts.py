"""Listener dispatch for kind=script_request × {APPROVE, REJECT, REVISIT}."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.daemon.feishu_listener import FeishuEventListener
from src.infrastructure.audit_logger import AuditLogger
from src.infrastructure.database import Database


def _mk_listener(tmp_path: Path):
    db = Database(tmp_path / "grassland.db")
    audit = AuditLogger(db)
    loop = asyncio.new_event_loop()
    listener = FeishuEventListener(
        slug="acme", db=db, audit=audit, chat_id="oc_xyz",
        resolve_escalation=AsyncMock(),
        revisit_from_notification=AsyncMock(),
        dispatch_via_feishu=AsyncMock(),
        send_dispatch_confirmation=AsyncMock(),
        send_dispatch_error=AsyncMock(),
        allow_dispatch=False,
        loop=loop, app_id="x", app_secret="x", domain="feishu",
        run_script_from_notification=AsyncMock(return_value={"id": "SR-1", "status": "running"}),
        reject_script_from_notification=AsyncMock(),
    )
    return listener, db, loop


def _mint(db: Database, sr_id: str = "SR-1"):
    db.mint_escalation_notification(
        feishu_message_id="om_root", org_slug="acme", task_id=sr_id,
        chat_id="oc_xyz",
        expires_at=datetime(2099, 1, 1, tzinfo=timezone.utc),
        kind="script_request",
    )


def _mk_event(text: str):
    msg = SimpleNamespace(
        chat_id="oc_xyz", message_id="om_in", root_id="om_root",
        message_type="text", content=json.dumps({"text": text}),
    )
    sender = SimpleNamespace(
        sender_type="user",
        sender_id=SimpleNamespace(open_id="ou_user_1"),
    )
    return SimpleNamespace(
        header=SimpleNamespace(event_id="evt_1"),
        event=SimpleNamespace(message=msg, sender=sender),
    )


def test_script_request_approve_routes_to_run_helper(tmp_path):
    l, db, loop = _mk_listener(tmp_path)
    _mint(db)
    loop.run_until_complete(l._handle_event_async(_mk_event("APPROVE\nlgtm")))

    l._run_script_from_notification.assert_called_once()
    kwargs = l._run_script_from_notification.call_args.kwargs
    assert kwargs["sr_id"] == "SR-1"

    row = db.get_escalation_notification("om_root")
    assert row["consumed_at"] is not None
    assert row["consumed_by"] == "feishu-reply"


def test_script_request_reject_routes_to_reject_helper(tmp_path):
    l, db, loop = _mk_listener(tmp_path)
    _mint(db)
    loop.run_until_complete(l._handle_event_async(_mk_event("REJECT\nnot a fit")))

    l._reject_script_from_notification.assert_called_once()
    kwargs = l._reject_script_from_notification.call_args.kwargs
    assert kwargs["sr_id"] == "SR-1"
    assert kwargs["reason"] == "not a fit"

    row = db.get_escalation_notification("om_root")
    assert row["consumed_at"] is not None


def test_script_request_reject_with_empty_body_uses_fallback_reason(tmp_path):
    l, db, loop = _mk_listener(tmp_path)
    _mint(db)
    loop.run_until_complete(l._handle_event_async(_mk_event("REJECT")))

    kwargs = l._reject_script_from_notification.call_args.kwargs
    assert kwargs["reason"] == "(no rationale provided via Feishu)"


def test_script_request_revisit_is_verb_mismatch(tmp_path):
    l, db, loop = _mk_listener(tmp_path)
    _mint(db)
    loop.run_until_complete(l._handle_event_async(_mk_event("REVISIT\nplease")))

    l._run_script_from_notification.assert_not_called()
    l._reject_script_from_notification.assert_not_called()
    row = db.get_escalation_notification("om_root")
    assert row["consumed_at"] is None


def test_script_request_handler_exception_unconsumes(tmp_path):
    """If the helper raises (e.g. not_pending because CLI won the race),
    the notification stays unconsumed for cli-fallback consume."""
    from fastapi import HTTPException
    l, db, loop = _mk_listener(tmp_path)
    _mint(db)
    l._run_script_from_notification = AsyncMock(
        side_effect=HTTPException(
            status_code=409, detail={"code": "not_pending", "status": "rejected"},
        ),
    )
    loop.run_until_complete(l._handle_event_async(_mk_event("APPROVE")))
    row = db.get_escalation_notification("om_root")
    assert row["consumed_at"] is None
