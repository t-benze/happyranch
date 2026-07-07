"""Test that CLI resolve-escalation consumes open Feishu notification rows."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from runtime.infrastructure.audit_logger import AuditLogger
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
        json={"decision": "continue", "rationale": "ok"},
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
        json={"decision": "cancel", "rationale": "nope"},
    )
    assert r.status_code == 200
    assert r.json()["new_status"] == "cancelled"

    row = org.db.get_escalation_notification("om_reject")
    assert row["consumed_at"] is not None
    assert row["consumed_by"] == "cli-fallback"


# ── THR-075: Continue/Cancel vocabulary ──────────────────────────────


@pytest.mark.asyncio
async def test_resolve_escalation_continue_returns_pending_and_reenqueues(
    client_with_runtime,
):
    """decision=continue -> task PENDING, re-enqueued, header fires. (RED)"""
    client, org = client_with_runtime

    org.db.insert_task(TaskRecord(id="T-CONT", brief="b"))
    org.db.update_task(
        "T-CONT",
        status=TaskStatus.ESCALATED, block_kind=None,
    )

    r = client.post(
        "/api/v1/orgs/alpha/tasks/T-CONT/resolve-escalation",
        json={"decision": "continue", "rationale": "go ahead"},
    )
    assert r.status_code == 200, f"got {r.status_code} {r.text}"
    assert r.json()["new_status"] == "pending"

    task = org.db.get_task("T-CONT")
    assert task.status == TaskStatus.PENDING
    assert task.block_kind is None
    assert "continued" in (task.note or "")


@pytest.mark.asyncio
async def test_resolve_escalation_continue_audit_stores_continue(
    client_with_runtime,
):
    """AuditLogger.log_escalation_resolved records decision=continue verbatim. (RED)"""
    client, org = client_with_runtime

    org.db.insert_task(TaskRecord(id="T-CONT2", brief="b"))
    org.db.update_task(
        "T-CONT2",
        status=TaskStatus.ESCALATED, block_kind=None,
    )

    r = client.post(
        "/api/v1/orgs/alpha/tasks/T-CONT2/resolve-escalation",
        json={"decision": "continue", "rationale": "proceed"},
    )
    assert r.status_code == 200

    logs = org.db.get_audit_logs("T-CONT2")
    resolved = [e for e in logs if e["action"] == "escalation_resolved"]
    assert len(resolved) == 1
    payload = resolved[0]["payload"] or {}
    assert payload.get("decision") == "continue"


@pytest.mark.asyncio
async def test_resolve_escalation_cancel_sets_cancelled_and_notifies_parent(
    client_with_runtime,
):
    """decision=cancel -> CANCELLED + cancelled_at + parent notified. (RED)"""
    client, org = client_with_runtime

    # Create a parent that is waiting on a delegated child
    org.db.insert_task(TaskRecord(id="T-PARENT", brief="parent"))
    org.db.update_task(
        "T-PARENT",
        status=TaskStatus.IN_PROGRESS, block_kind=BlockKind.DELEGATED,
    )
    # Create the escalated child that belongs to this parent
    org.db.insert_task(
        TaskRecord(id="T-CANCEL", brief="child", parent_task_id="T-PARENT")
    )
    org.db.update_task(
        "T-CANCEL",
        status=TaskStatus.ESCALATED, block_kind=None,
    )

    r = client.post(
        "/api/v1/orgs/alpha/tasks/T-CANCEL/resolve-escalation",
        json={"decision": "cancel", "rationale": "not needed"},
    )
    assert r.status_code == 200, f"got {r.status_code} {r.text}"
    assert r.json()["new_status"] == "cancelled"

    child = org.db.get_task("T-CANCEL")
    assert child.status == TaskStatus.CANCELLED
    assert child.cancelled_at is not None
    assert "cancelled" in (child.note or "")


@pytest.mark.asyncio
async def test_resolve_escalation_cancel_audit_stores_cancel(
    client_with_runtime,
):
    """AuditLogger.log_escalation_resolved records decision=cancel verbatim. (RED)"""
    client, org = client_with_runtime

    org.db.insert_task(TaskRecord(id="T-CANCEL2", brief="b"))
    org.db.update_task(
        "T-CANCEL2",
        status=TaskStatus.ESCALATED, block_kind=None,
    )

    r = client.post(
        "/api/v1/orgs/alpha/tasks/T-CANCEL2/resolve-escalation",
        json={"decision": "cancel", "rationale": "stop"},
    )
    assert r.status_code == 200

    logs = org.db.get_audit_logs("T-CANCEL2")
    resolved = [e for e in logs if e["action"] == "escalation_resolved"]
    assert len(resolved) == 1
    payload = resolved[0]["payload"] or {}
    assert payload.get("decision") == "cancel"


@pytest.mark.asyncio
async def test_resolve_escalation_invalid_decision_returns_400(
    client_with_runtime,
):
    """Invalid decision value -> 400. (RED)"""
    client, org = client_with_runtime

    org.db.insert_task(TaskRecord(id="T-INV", brief="b"))
    org.db.update_task(
        "T-INV",
        status=TaskStatus.ESCALATED, block_kind=None,
    )

    r = client.post(
        "/api/v1/orgs/alpha/tasks/T-INV/resolve-escalation",
        json={"decision": "approve", "rationale": "old vocab"},
    )
    assert r.status_code == 400, f"expected 400, got {r.status_code}"

    r2 = client.post(
        "/api/v1/orgs/alpha/tasks/T-INV/resolve-escalation",
        json={"decision": "reject", "rationale": "old vocab"},
    )
    assert r2.status_code == 400, f"expected 400, got {r2.status_code}"
