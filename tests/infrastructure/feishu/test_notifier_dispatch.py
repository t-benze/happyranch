from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from runtime.infrastructure.audit_logger import AuditLogger
from runtime.infrastructure.database import Database
from runtime.infrastructure.feishu.notifier import EscalationNotifier
from runtime.orchestrator.org_config import FeishuNotificationsConfig


class _FakeClient:
    def __init__(self):
        self.sent = []

    def send_post_message(self, *, chat_id, title, body_lines):
        self.sent.append({"chat_id": chat_id, "title": title, "body": body_lines})
        return "om_confirm_1"


@pytest.fixture()
def notifier(tmp_path: Path):
    db = Database(tmp_path / "happyranch.db")
    audit = AuditLogger(db)
    client = _FakeClient()
    cfg = FeishuNotificationsConfig(
        provider="feishu", region="feishu", chat_id="oc_xyz",
        app_id="cli", app_secret="x", reply_ttl_hours=72,
    )
    return EscalationNotifier(
        slug="acme", db=db, audit=audit, client=client, config=cfg,
    ), client


def test_send_dispatch_confirmation_renders_card(notifier):
    n, client = notifier
    asyncio.run(n.send_dispatch_confirmation(
        task_id="TASK-21", team="engineering",
        brief="investigate the 503 thing",
    ))
    assert len(client.sent) == 1
    body = "\n".join(client.sent[0]["body"])
    assert "TASK-21" in client.sent[0]["title"]
    assert "engineering" in body
    assert "investigate the 503" in body
    assert "happyranch tail" in body


def test_send_dispatch_error_lists_reason(notifier):
    n, client = notifier
    asyncio.run(n.send_dispatch_error(
        reason="unknown team \"engineerin\"",
        valid_teams=["engineering", "customer-care"],
    ))
    assert len(client.sent) == 1
    body = "\n".join(client.sent[0]["body"])
    assert "unknown team" in body
    assert "engineering" in body
    assert "customer-care" in body


def test_send_dispatch_confirmation_swallows_exception(notifier):
    n, client = notifier
    client.send_post_message = lambda **kw: (_ for _ in ()).throw(RuntimeError("boom"))
    # Must not raise
    asyncio.run(n.send_dispatch_confirmation(
        task_id="TASK-21", team="engineering", brief="x",
    ))
