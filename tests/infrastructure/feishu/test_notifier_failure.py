from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from src.infrastructure.audit_logger import AuditLogger
from src.infrastructure.database import Database
from src.infrastructure.feishu.notifier import EscalationNotifier
from src.orchestrator.org_config import FeishuNotificationsConfig


class _FakeClient:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    def send_post_message(self, *, chat_id, title, body_lines):
        self.sent.append({"chat_id": chat_id, "title": title, "body": body_lines})
        return "om_failure_msg_1"


@pytest.fixture()
def setup(tmp_path: Path):
    db = Database(tmp_path / "opc.db")
    audit = AuditLogger(db)
    client = _FakeClient()
    cfg = FeishuNotificationsConfig(
        provider="feishu", region="feishu", chat_id="oc_xyz",
        app_id="cli", app_secret="x", reply_ttl_hours=72,
    )
    notifier = EscalationNotifier(
        slug="acme", db=db, audit=audit, client=client, config=cfg,
    )
    # Insert a task so the notifier can render its brief
    from src.models import TaskRecord, TaskStatus
    db.insert_task(TaskRecord(
        id="TASK-9", brief="ferry scraper update", team="engineering",
        assigned_agent="dev_agent", status=TaskStatus.FAILED,
    ))
    return notifier, db, client


def test_send_failure_renders_card_and_mints_failure_kind(setup):
    notifier, db, client = setup
    asyncio.run(notifier.send_failure(
        task_id="TASK-9",
        agent="dev_agent",
        failure_kind="self_blocked",
        failure_note="cannot determine fare-tier mapping",
        last_summary="delegated; agent returned blocked status",
    ))
    assert len(client.sent) == 1
    sent = client.sent[0]
    assert "FAILED" in sent["title"]
    body_text = "\n".join(sent["body"]) if isinstance(sent["body"], list) else str(sent["body"])
    assert "self_blocked" in body_text
    assert "cannot determine fare-tier mapping" in body_text
    assert "REVISIT" in body_text

    row = db.get_escalation_notification("om_failure_msg_1")
    assert row is not None
    assert row["kind"] == "failure"
    assert row["task_id"] == "TASK-9"


def test_send_failure_swallows_send_exception(setup):
    notifier, db, client = setup

    def boom(**kwargs):
        raise RuntimeError("feishu down")

    client.send_post_message = boom
    # Must not raise
    asyncio.run(notifier.send_failure(
        task_id="TASK-9", agent="dev_agent",
        failure_kind="self_blocked", failure_note="x", last_summary="",
    ))
    # No notification row minted on send failure
    assert db.get_escalation_notification("om_failure_msg_1") is None
