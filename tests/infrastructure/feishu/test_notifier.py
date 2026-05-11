"""Unit tests for EscalationNotifier."""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from src.infrastructure.audit_logger import AuditLogger
from src.infrastructure.database import Database
from src.infrastructure.feishu.client import FeishuSendError
from src.infrastructure.feishu.notifier import EscalationNotifier
from src.orchestrator.org_config import FeishuNotificationsConfig


@dataclass
class _FakeFeishuClient:
    sent: list[dict]
    next_message_id: str = "om_fake"

    def send_post_message(self, *, chat_id, title, body_lines):
        self.sent.append({"chat_id": chat_id, "title": title, "body_lines": body_lines})
        return self.next_message_id


def _cfg(chat_id: str = "oc_x") -> FeishuNotificationsConfig:
    return FeishuNotificationsConfig(
        provider="feishu", region="feishu",
        chat_id=chat_id, app_id="cli_test", app_secret="secret_test",
        reply_ttl_hours=72,
    )


def _seed_task(db: Database, task_id: str = "TASK-1") -> None:
    from src.models import TaskRecord
    db.insert_task(TaskRecord(
        id=task_id,
        team="engineering",
        brief="Add Alipay support",
    ))


@pytest.mark.asyncio
async def test_notify_escalated_sends_and_audits(tmp_path):
    db = Database(tmp_path / "opc.db")
    _seed_task(db)
    fake = _FakeFeishuClient(sent=[], next_message_id="om_42")
    notifier = EscalationNotifier(
        slug="hk-macau-tourism",
        db=db,
        audit=AuditLogger(db),
        client=fake,
        config=_cfg(chat_id="oc_abc"),
    )

    await notifier.notify_escalated(
        task_id="TASK-1",
        agent="engineering_head",
        reason="Manager requested founder authority.",
        last_summary="Two delegation rounds failed.",
    )

    assert len(fake.sent) == 1
    sent = fake.sent[0]
    assert sent["chat_id"] == "oc_abc"
    assert "TASK-1" in sent["title"]
    assert "hk-macau-tourism" in sent["title"]
    body_text = "\n".join(sent["body_lines"])
    assert "engineering_head" in body_text
    assert "Add Alipay support" in body_text
    assert "Two delegation rounds failed" in body_text
    assert "Manager requested founder authority" in body_text
    assert "APPROVE" in body_text
    assert "REJECT" in body_text
    assert "opc resolve-escalation" in body_text

    row = db.get_escalation_notification("om_42")
    assert row is not None
    assert row["task_id"] == "TASK-1"
    assert row["consumed_at"] is None

    actions = [r["action"] for r in db.get_audit_logs("TASK-1")]
    assert "escalation_notify_sent" in actions


@dataclass
class _ExplodingFeishuClient:
    def send_post_message(self, *, chat_id, title, body_lines):
        raise FeishuSendError(code=99991663, msg="permission denied")


@pytest.mark.asyncio
async def test_notify_escalated_swallows_send_failure(tmp_path):
    db = Database(tmp_path / "opc.db")
    _seed_task(db)
    notifier = EscalationNotifier(
        slug="o", db=db, audit=AuditLogger(db),
        client=_ExplodingFeishuClient(), config=_cfg(),
    )
    await notifier.notify_escalated(
        task_id="TASK-1", agent="x", reason="r", last_summary="s",
    )
    actions = [r["action"] for r in db.get_audit_logs("TASK-1")]
    assert "escalation_notify_sent" not in actions
    assert "escalation_notify_failed" in actions
    # Notification row never minted (we send first, then mint).
    assert db.get_escalation_notification("om_fake") is None


@pytest.mark.asyncio
async def test_notify_escalated_missing_task_is_no_op(tmp_path):
    db = Database(tmp_path / "opc.db")
    fake = _FakeFeishuClient(sent=[])
    notifier = EscalationNotifier(
        slug="o", db=db, audit=AuditLogger(db),
        client=fake, config=_cfg(),
    )
    await notifier.notify_escalated(
        task_id="TASK-DOES-NOT-EXIST", agent="x", reason="r",
    )
    assert fake.sent == []
