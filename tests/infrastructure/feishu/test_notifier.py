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


@dataclass
class _ThreadReplyClient:
    """Fake supporting send_thread_reply for parse-hint tests."""
    replies: list[dict]
    next_message_id: str = "om_hint"

    def send_post_message(self, *, chat_id, title, body_lines):
        # Unused by parse-hint path; keep signature for shared Protocol.
        raise AssertionError("send_post_message should not be called")

    def send_thread_reply(self, *, parent_message_id, title, body_lines):
        self.replies.append({
            "parent_message_id": parent_message_id,
            "title": title,
            "body_lines": body_lines,
        })
        return self.next_message_id


@pytest.mark.asyncio
async def test_send_parse_hint_replies_in_thread_and_audits(tmp_path):
    db = Database(tmp_path / "opc.db")
    _seed_task(db)
    fake = _ThreadReplyClient(replies=[], next_message_id="om_hint_7")
    notifier = EscalationNotifier(
        slug="o", db=db, audit=AuditLogger(db),
        client=fake, config=_cfg(),
    )

    await notifier.send_parse_hint(
        parent_message_id="om_founder_reply",
        task_id="TASK-1",
        text_preview="approve: skip device check",
        feishu_event_id="evt_42",
    )

    assert len(fake.replies) == 1
    sent = fake.replies[0]
    assert sent["parent_message_id"] == "om_founder_reply"
    body_text = "\n".join(sent["body_lines"])
    assert "approve: skip device check" in body_text
    assert "APPROVE" in body_text
    assert "REJECT" in body_text
    assert "REVISIT" in body_text
    # Founder is told they can retry without going to CLI.
    assert "thread" in body_text.lower()

    rows = db.get_audit_logs("TASK-1")
    actions = {r["action"]: r["payload"] for r in rows}
    assert "escalation_parse_hint_sent" in actions
    payload = actions["escalation_parse_hint_sent"]
    assert payload["hint_message_id"] == "om_hint_7"
    assert payload["feishu_event_id"] == "evt_42"


@dataclass
class _ExplodingThreadReplyClient:
    def send_post_message(self, *, chat_id, title, body_lines):
        raise AssertionError("not used")

    def send_thread_reply(self, *, parent_message_id, title, body_lines):
        raise FeishuSendError(code=230020, msg="message_not_found")


@pytest.mark.asyncio
async def test_send_parse_hint_swallows_send_failure(tmp_path):
    db = Database(tmp_path / "opc.db")
    _seed_task(db)
    notifier = EscalationNotifier(
        slug="o", db=db, audit=AuditLogger(db),
        client=_ExplodingThreadReplyClient(), config=_cfg(),
    )
    # Must not raise.
    await notifier.send_parse_hint(
        parent_message_id="om_p",
        task_id="TASK-1",
        text_preview="anything",
        feishu_event_id="evt_x",
    )
    actions = [r["action"] for r in db.get_audit_logs("TASK-1")]
    assert "escalation_parse_hint_sent" not in actions
    assert "escalation_parse_hint_send_failed" in actions


@pytest.mark.asyncio
async def test_send_parse_hint_truncates_long_preview_in_body(tmp_path):
    """The hint body should display a bounded preview so an attacker (or a
    pasted novel) can't bloat the Feishu post payload arbitrarily."""
    db = Database(tmp_path / "opc.db")
    _seed_task(db)
    fake = _ThreadReplyClient(replies=[])
    notifier = EscalationNotifier(
        slug="o", db=db, audit=AuditLogger(db),
        client=fake, config=_cfg(),
    )
    await notifier.send_parse_hint(
        parent_message_id="om_p",
        task_id="TASK-1",
        text_preview="x" * 500,
        feishu_event_id="evt_x",
    )
    body_text = "\n".join(fake.replies[0]["body_lines"])
    # 200-char cap + ellipsis marker
    assert "x" * 200 in body_text
    assert "x" * 201 not in body_text
