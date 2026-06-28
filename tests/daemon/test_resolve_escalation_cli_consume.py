"""Test that CLI resolve-escalation consumes open Feishu notification rows."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from runtime.models import BlockKind, TaskRecord, TaskStatus


@pytest.mark.asyncio
async def test_resolve_escalation_cli_consumes_open_feishu_notification(
    client_with_runtime,
):
    client, org = client_with_runtime

    org.db.insert_task(TaskRecord(id="T-1", brief="b"))
    org.db.update_task(
        "T-1",
        status=TaskStatus.ESCALATED, block_kind=None,
    )
    expires = datetime.now(timezone.utc) + timedelta(hours=72)
    org.db.mint_escalation_notification(
        feishu_message_id="om_open", org_slug="alpha", task_id="T-1",
        chat_id="oc", expires_at=expires,
    )

    r = client.post(
        "/api/v1/orgs/alpha/tasks/T-1/resolve-escalation",
        json={"decision": "approve", "rationale": "ok"},
    )
    assert r.status_code == 200
    assert r.json()["new_status"] == "pending"

    row = org.db.get_escalation_notification("om_open")
    assert row["consumed_at"] is not None
    assert row["consumed_by"] == "cli-fallback"


@pytest.mark.asyncio
async def test_resolve_escalation_reject_also_consumes_notification(
    client_with_runtime,
):
    client, org = client_with_runtime

    org.db.insert_task(TaskRecord(id="T-2", brief="b"))
    org.db.update_task(
        "T-2",
        status=TaskStatus.ESCALATED, block_kind=None,
    )
    expires = datetime.now(timezone.utc) + timedelta(hours=72)
    org.db.mint_escalation_notification(
        feishu_message_id="om_reject", org_slug="alpha", task_id="T-2",
        chat_id="oc", expires_at=expires,
    )

    r = client.post(
        "/api/v1/orgs/alpha/tasks/T-2/resolve-escalation",
        json={"decision": "reject", "rationale": "nope"},
    )
    assert r.status_code == 200
    assert r.json()["new_status"] == "failed"

    row = org.db.get_escalation_notification("om_reject")
    assert row["consumed_at"] is not None
    assert row["consumed_by"] == "cli-fallback"
