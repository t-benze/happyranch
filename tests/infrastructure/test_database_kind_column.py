from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.infrastructure.database import Database


@pytest.fixture()
def db(tmp_path: Path) -> Database:
    return Database(tmp_path / "happyranch.db")


def test_mint_escalation_notification_defaults_to_escalation_kind(db: Database):
    db.mint_escalation_notification(
        feishu_message_id="om_abc",
        org_slug="acme",
        task_id="TASK-1",
        chat_id="oc_xyz",
        expires_at=datetime(2099, 1, 1, tzinfo=timezone.utc),
    )
    row = db.get_escalation_notification("om_abc")
    assert row is not None
    assert row["kind"] == "escalation"


def test_mint_escalation_notification_accepts_failure_kind(db: Database):
    db.mint_escalation_notification(
        feishu_message_id="om_def",
        org_slug="acme",
        task_id="TASK-2",
        chat_id="oc_xyz",
        expires_at=datetime(2099, 1, 1, tzinfo=timezone.utc),
        kind="failure",
    )
    row = db.get_escalation_notification("om_def")
    assert row is not None
    assert row["kind"] == "failure"


def test_mint_rejects_unknown_kind(db: Database):
    with pytest.raises(ValueError, match=r"kind must be .* got 'bogus'"):
        db.mint_escalation_notification(
            feishu_message_id="om_x",
            org_slug="acme",
            task_id="TASK-3",
            chat_id="oc_xyz",
            expires_at=datetime(2099, 1, 1, tzinfo=timezone.utc),
            kind="bogus",
        )
