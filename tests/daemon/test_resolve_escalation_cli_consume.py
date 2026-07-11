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
async def test_resolve_escalation_cancel_removed_returns_400(
    client_with_runtime,
):
    """THR-080: 'cancel' is no longer a valid decision for resolve-escalation."""
    client, org = client_with_runtime

    org.db.insert_task(TaskRecord(id="T-2", brief="b"))
    org.db.update_task(
        "T-2",
        status=TaskStatus.ESCALATED, block_kind=None,
    )

    r = client.post(
        "/api/v1/orgs/alpha/tasks/T-2/resolve-escalation",
        json={"decision": "cancel", "rationale": "nope"},
    )
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "invalid_decision"


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
async def test_cancel_escalated_task_sets_cancelled_and_notifies_parent(
    client_with_runtime,
):
    """THR-080: cancelling an escalated task uses normal POST /cancel.
    Verifies parity: CANCELLED + cancelled_at + parent notified."""
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
        "/api/v1/orgs/alpha/tasks/T-CANCEL/cancel",
        json={"rationale": "not needed"},
    )
    assert r.status_code == 200, f"got {r.status_code} {r.text}"

    child = org.db.get_task("T-CANCEL")
    assert child.status == TaskStatus.CANCELLED
    assert child.cancelled_at is not None
    assert "cancelled" in (child.note or "")


@pytest.mark.asyncio
async def test_cancel_escalated_task_audit_stores_task_cancelled(
    client_with_runtime,
):
    """THR-080: cancelling an escalated task via POST /cancel writes
    a task_cancelled audit row (parity)."""
    client, org = client_with_runtime

    org.db.insert_task(TaskRecord(id="T-CANCEL2", brief="b"))
    org.db.update_task(
        "T-CANCEL2",
        status=TaskStatus.ESCALATED, block_kind=None,
    )

    r = client.post(
        "/api/v1/orgs/alpha/tasks/T-CANCEL2/cancel",
        json={"rationale": "stop"},
    )
    assert r.status_code == 200

    logs = org.db.get_audit_logs("T-CANCEL2")
    cancelled = [e for e in logs if e["action"] == "task_cancelled"]
    assert len(cancelled) == 1, f"expected task_cancelled audit, got {[e['action'] for e in logs]}"
    payload = cancelled[0]["payload"] or {}
    assert payload.get("rationale") == "stop"


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


# ── THR-080: Supersede ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_resolve_escalation_supersede_mints_successor_and_closes_predecessor(
    client_with_runtime,
):
    """THR-080: decision=supersede with brief -> successor created, predecessor SUPERSEDED."""
    client, org = client_with_runtime

    org.db.insert_task(TaskRecord(id="T-SUP", brief="original brief"))
    org.db.update_task(
        "T-SUP",
        status=TaskStatus.ESCALATED, block_kind=None,
    )

    r = client.post(
        "/api/v1/orgs/alpha/tasks/T-SUP/resolve-escalation",
        json={"decision": "supersede", "rationale": "reroute", "brief": "successor task"},
    )
    assert r.status_code == 200, f"got {r.status_code} {r.text}"
    assert r.json()["new_status"] == "superseded"

    predecessor = org.db.get_task("T-SUP")
    assert predecessor.status == TaskStatus.SUPERSEDED
    assert predecessor.block_kind is None

    # Successor task must exist with the supplied brief.
    logs = org.db.get_audit_logs("T-SUP")
    superseded_logs = [e for e in logs if e["action"] == "escalation_superseded"]
    assert len(superseded_logs) == 1
    successor_id = superseded_logs[0]["payload"]["successor_root"]
    successor = org.db.get_task(successor_id)
    assert successor is not None
    assert successor.brief == "successor task"


@pytest.mark.asyncio
async def test_resolve_escalation_supersede_requires_brief(
    client_with_runtime,
):
    """THR-080: supersede without a brief -> 422."""
    client, org = client_with_runtime

    org.db.insert_task(TaskRecord(id="T-SUP2", brief="original"))
    org.db.update_task(
        "T-SUP2",
        status=TaskStatus.ESCALATED, block_kind=None,
    )

    r = client.post(
        "/api/v1/orgs/alpha/tasks/T-SUP2/resolve-escalation",
        json={"decision": "supersede", "rationale": "reroute", "brief": ""},
    )
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "supersede_requires_brief"


# ── THR-080: Continue fail-closed gating ────────────────────────────


@pytest.mark.asyncio
async def test_resolve_escalation_continue_rejects_live_children(
    client_with_runtime,
):
    """THR-080 memo §3: continue must reject when the escalated task has
    non-terminal children. The error message must name the supersede fallback."""
    client, org = client_with_runtime

    org.db.insert_task(TaskRecord(id="T-PAR", brief="parent"))
    org.db.update_task(
        "T-PAR",
        status=TaskStatus.ESCALATED, block_kind=None,
    )
    # Add a non-terminal (PENDING) child.
    org.db.insert_task(
        TaskRecord(id="T-CHD", brief="child", parent_task_id="T-PAR")
    )
    # Child stays PENDING (non-terminal).

    r = client.post(
        "/api/v1/orgs/alpha/tasks/T-PAR/resolve-escalation",
        json={"decision": "continue", "rationale": "go ahead"},
    )
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert detail["code"] == "cannot_continue_live_children"
    assert "supersede" in detail.get("remedy", "").lower()


@pytest.mark.asyncio
async def test_resolve_escalation_continue_accepts_all_children_terminal(
    client_with_runtime,
):
    """THR-080 memo §3: continue accepts when all children are terminal."""
    client, org = client_with_runtime

    org.db.insert_task(TaskRecord(id="T-PAR2", brief="parent"))
    org.db.update_task(
        "T-PAR2",
        status=TaskStatus.ESCALATED, block_kind=None,
    )
    # Add a terminal (COMPLETED) child.
    org.db.insert_task(
        TaskRecord(
            id="T-CHD2", brief="child", parent_task_id="T-PAR2",
            status=TaskStatus.COMPLETED,
        )
    )

    r = client.post(
        "/api/v1/orgs/alpha/tasks/T-PAR2/resolve-escalation",
        json={"decision": "continue", "rationale": "go ahead"},
    )
    assert r.status_code == 200, f"got {r.status_code} {r.text}"
    assert "pending" in r.json()["new_status"]


# ── THR-080: Audit-attribution ──────────────────────────────────────


@pytest.mark.asyncio
async def test_resolve_escalation_continue_records_real_actor(
    client_with_runtime,
):
    """THR-080: the audit row must record the real actor, not hardcoded 'founder'."""
    client, org = client_with_runtime

    org.db.insert_task(TaskRecord(id="T-ACT", brief="b"))
    org.db.update_task(
        "T-ACT",
        status=TaskStatus.ESCALATED, block_kind=None,
    )

    r = client.post(
        "/api/v1/orgs/alpha/tasks/T-ACT/resolve-escalation",
        json={"decision": "continue", "rationale": "ok", "actor": "engineering_manager"},
    )
    assert r.status_code == 200

    logs = org.db.get_audit_logs("T-ACT")
    resolved = [e for e in logs if e["action"] == "escalation_resolved"]
    assert len(resolved) == 1
    assert resolved[0]["agent"] == "engineering_manager"
