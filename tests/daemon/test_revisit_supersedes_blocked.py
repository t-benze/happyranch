"""§3(a) auto-resolve forcing function + Gap-B delegated close (THR-018 tier #3).

A founder `revisit` (or Feishu-reply revisit) of an escalated or in_progress(delegated)
predecessor auto-transitions that predecessor to the terminal
RESOLVED_SUPERSEDED status — block_kind cleared, audit citing the new
continuation root (the maker-checker evidence) — without re-enqueuing it.
The delegated close is gated on all children being terminal.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from runtime.models import BlockKind, TaskRecord, TaskStatus


def _build_org(tmp_path: Path):
    from runtime.infrastructure.database import Database

    db = Database(tmp_path / "happyranch.db")
    org = MagicMock()
    org.db = db
    org.slug = "acme"
    org.db_lock = asyncio.Lock()
    # _enqueue_parent_if_waiting reads orch._db; point it at the real db so the
    # parent-wake call operates on real rows (no-ops cleanly for a root).
    orch = MagicMock()
    orch._db = db
    org.orchestrator = orch
    state = MagicMock()
    state.is_idle = False
    state.queue = MagicMock()
    return org, state, db


def _audit_actions(db, task_id: str) -> list[str]:
    return [e["action"] for e in db.get_audit_logs(task_id)]


def _audit_payload(db, task_id: str, action: str) -> dict:
    for e in db.get_audit_logs(task_id):
        if e["action"] == action:
            return e["payload"] or {}
    return {}


@pytest.mark.asyncio
async def test_revisit_supersedes_blocked_escalated_predecessor(tmp_path: Path):
    from runtime.daemon.routes.tasks import revisit_from_notification

    org, state, db = _build_org(tmp_path)
    db.insert_task(TaskRecord(
        id="TASK-1", brief="b", team="engineering", assigned_agent="m",
        status=TaskStatus.ESCALATED, block_kind=None,
    ))

    result = await revisit_from_notification(
        org, state, task_id="TASK-1", founder_note="ruled in THR-X", actor="cli",
    )

    pred = db.get_task("TASK-1")
    assert pred.status == TaskStatus.RESOLVED_SUPERSEDED
    assert pred.block_kind is None
    assert pred.completed_at is not None
    # Maker-checker evidence: the audit cites the concrete successor task_id.
    payload = _audit_payload(db, "TASK-1", "escalation_superseded")
    assert payload["successor_root"] == result.new_root_id
    assert payload["prior_block_kind"] == "escalated"
    # Gap-A: the superseded task is NEVER re-enqueued; only the new root is.
    state.queue.put_nowait.assert_not_called()
    state.queue.enqueue.assert_called_once_with("acme", result.new_root_id)
    assert result.new_root_id != "TASK-1"


@pytest.mark.asyncio
async def test_revisit_supersedes_blocked_delegated_when_all_children_terminal(
    tmp_path: Path,
):
    from runtime.daemon.routes.tasks import revisit_from_notification

    org, state, db = _build_org(tmp_path)
    db.insert_task(TaskRecord(
        id="TASK-1", brief="parent", team="engineering", assigned_agent="m",
        status=TaskStatus.IN_PROGRESS, block_kind=BlockKind.DELEGATED,
    ))
    db.insert_task(TaskRecord(
        id="TASK-2", brief="c1", parent_task_id="TASK-1", status=TaskStatus.COMPLETED,
    ))
    db.insert_task(TaskRecord(
        id="TASK-3", brief="c2", parent_task_id="TASK-1", status=TaskStatus.FAILED,
    ))

    result = await revisit_from_notification(
        org, state, task_id="TASK-1", founder_note=None, actor="cli",
    )

    pred = db.get_task("TASK-1")
    assert pred.status == TaskStatus.RESOLVED_SUPERSEDED
    assert pred.block_kind is None
    payload = _audit_payload(db, "TASK-1", "escalation_superseded")
    assert payload["successor_root"] == result.new_root_id
    assert payload["prior_block_kind"] == "delegated"
    state.queue.put_nowait.assert_not_called()


@pytest.mark.asyncio
async def test_revisit_refuses_blocked_delegated_with_live_child(tmp_path: Path):
    """Gap-B gate: a delegated parent with any non-terminal child is NOT
    revisit-eligible — it must not be superseded (would abandon the live
    child) and the live child must not be touched (no cascade-SIGTERM)."""
    from fastapi import HTTPException
    from runtime.daemon.routes.tasks import revisit_from_notification

    org, state, db = _build_org(tmp_path)
    db.insert_task(TaskRecord(
        id="TASK-1", brief="parent", team="engineering", assigned_agent="m",
        status=TaskStatus.IN_PROGRESS, block_kind=BlockKind.DELEGATED,
    ))
    db.insert_task(TaskRecord(
        id="TASK-2", brief="done", parent_task_id="TASK-1", status=TaskStatus.COMPLETED,
    ))
    db.insert_task(TaskRecord(
        id="TASK-3", brief="live", parent_task_id="TASK-1",
        status=TaskStatus.IN_PROGRESS,
    ))

    with pytest.raises(HTTPException) as exc:
        await revisit_from_notification(
            org, state, task_id="TASK-1", founder_note=None, actor="cli",
        )
    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "cannot_revisit"
    # Predecessor untouched (still blocked-delegated), live child untouched.
    assert db.get_task("TASK-1").status == TaskStatus.IN_PROGRESS
    assert db.get_task("TASK-1").block_kind == BlockKind.DELEGATED
    assert db.get_task("TASK-3").status == TaskStatus.IN_PROGRESS
    assert "escalation_superseded" not in _audit_actions(db, "TASK-1")


@pytest.mark.asyncio
async def test_revisit_completed_predecessor_is_not_superseded(tmp_path: Path):
    """A normal revisit of a COMPLETED predecessor must NOT mint a superseded
    terminal — the predecessor stays COMPLETED (no overreach of the new state)."""
    from runtime.daemon.routes.tasks import revisit_from_notification

    org, state, db = _build_org(tmp_path)
    db.insert_task(TaskRecord(
        id="TASK-1", brief="b", team="engineering", assigned_agent="m",
        status=TaskStatus.COMPLETED,
    ))

    await revisit_from_notification(
        org, state, task_id="TASK-1", founder_note=None, actor="cli",
    )

    assert db.get_task("TASK-1").status == TaskStatus.COMPLETED
    assert "escalation_superseded" not in _audit_actions(db, "TASK-1")


@pytest.mark.asyncio
async def test_manual_resolve_escalation_approve_does_not_supersede(tmp_path: Path):
    """Maker-checker negative: the founder's manual `resolve-escalation approve`
    is a DISTINCT path that re-runs the work (→ PENDING). It must never produce
    RESOLVED_SUPERSEDED — only a human-authorized continuation does that."""
    from runtime.daemon.routes.tasks import resolve_escalation_in_process

    org, state, db = _build_org(tmp_path)
    db.insert_task(TaskRecord(
        id="TASK-1", brief="b", team="engineering", assigned_agent="m",
        status=TaskStatus.ESCALATED, block_kind=None,
    ))
    db.list_open_notifications_for_task = MagicMock(return_value=[])

    new_status = await resolve_escalation_in_process(
        org, state, task_id="TASK-1", decision="approve", rationale="ship it",
    )
    assert new_status == "pending"
    assert db.get_task("TASK-1").status == TaskStatus.PENDING
    assert "escalation_superseded" not in _audit_actions(db, "TASK-1")


@pytest.mark.asyncio
async def test_revisit_supersedes_escalated_sibling_in_family(tmp_path: Path):
    """Founder revisit of an escalated root also closes eligible escalated
    sibling revisits in the same family, while leaving failed/completed/cancelled
    siblings untouched. THR-046 msg127 broader family closure."""
    from runtime.daemon.routes.tasks import revisit_from_notification

    org, state, db = _build_org(tmp_path)

    # Original escalated root (the explicit predecessor).
    db.insert_task(TaskRecord(
        id="TASK-1", brief="original escalation", team="engineering",
        assigned_agent="m",
        status=TaskStatus.ESCALATED, block_kind=None,
    ))
    # Escalated sibling revisit — should be closed.
    db.insert_task(TaskRecord(
        id="TASK-2", brief="escalated sibling", team="engineering",
        assigned_agent="m",
        status=TaskStatus.ESCALATED, block_kind=None,
        revisit_of_task_id="TASK-1",
    ))
    # Failed sibling revisit — should NOT be touched.
    db.insert_task(TaskRecord(
        id="TASK-3", brief="failed sibling", team="engineering",
        assigned_agent="m",
        status=TaskStatus.FAILED, block_kind=None,
        revisit_of_task_id="TASK-1",
    ))
    # Completed sibling revisit — should NOT be touched.
    db.insert_task(TaskRecord(
        id="TASK-4", brief="completed sibling", team="engineering",
        assigned_agent="m",
        status=TaskStatus.COMPLETED, block_kind=None,
        revisit_of_task_id="TASK-1",
    ))

    result = await revisit_from_notification(
        org, state, task_id="TASK-1", founder_note="ruled in THR-X", actor="cli",
    )

    # Explicit predecessor superseded.
    pred = db.get_task("TASK-1")
    assert pred.status == TaskStatus.RESOLVED_SUPERSEDED
    assert pred.block_kind is None
    pred_payload = _audit_payload(db, "TASK-1", "escalation_superseded")
    assert pred_payload["successor_root"] == result.new_root_id
    assert pred_payload["prior_block_kind"] == "escalated"

    # Escalated sibling superseded.
    sib = db.get_task("TASK-2")
    assert sib.status == TaskStatus.RESOLVED_SUPERSEDED
    assert sib.block_kind is None
    sib_payload = _audit_payload(db, "TASK-2", "escalation_superseded")
    assert sib_payload["successor_root"] == result.new_root_id
    assert sib_payload["prior_block_kind"] == "escalated"

    # Failed sibling untouched.
    assert db.get_task("TASK-3").status == TaskStatus.FAILED
    assert "escalation_superseded" not in _audit_actions(db, "TASK-3")

    # Completed sibling untouched.
    assert db.get_task("TASK-4").status == TaskStatus.COMPLETED
    assert "escalation_superseded" not in _audit_actions(db, "TASK-4")

    # Gap-A: only the new root is enqueued.
    state.queue.enqueue.assert_called_with("acme", result.new_root_id)


@pytest.mark.asyncio
async def test_unruled_escalation_with_no_continuation_stays_escalated(tmp_path: Path):
    """The escalation backlog only auto-closes via the revisit/dispatch
    forcing function. With no continuation created, an `escalated` task
    is never transitioned by any aggregation/read path (Path B: escalations
    are the stored top-level status='escalated')."""
    from runtime.orchestrator.dashboard_summary import compute_stale_escalations

    org, state, db = _build_org(tmp_path)
    old = datetime(2026, 1, 1, tzinfo=timezone.utc)
    db._conn.execute(
        "INSERT INTO tasks (id, brief, assigned_agent, team, status, created_at, updated_at) "
        "VALUES ('TASK-1', 'b', 'm', 'engineering', 'escalated', ?, ?)",
        (old.isoformat(), old.isoformat()),
    )
    db._conn.commit()
    now = datetime(2026, 1, 3, tzinfo=timezone.utc)
    rows = compute_stale_escalations(db, now=now)
    assert [r.task_id for r in rows] == ["TASK-1"]
    # Read paths never transition it.
    assert db.get_task("TASK-1").status == TaskStatus.ESCALATED
    assert db.get_task("TASK-1").block_kind is None


@pytest.mark.asyncio
async def test_revisit_supersedes_ancestor_in_revisit_chain(tmp_path: Path):
    """Finding 1 (ancestor-chain, founder path): A is escalated, B is an
    escalated revisit of A, and a founder revisit of B closes both B and A.
    When the explicit predecessor is itself a revisit, the ancestor root
    must also be evaluated through the eligibility gate and closed."""
    from runtime.daemon.routes.tasks import revisit_from_notification

    org, state, db = _build_org(tmp_path)

    # A: TASK-1 — original escalated root.
    db.insert_task(TaskRecord(
        id="TASK-1", brief="original root escalated", team="engineering",
        assigned_agent="m",
        status=TaskStatus.ESCALATED, block_kind=None,
    ))
    # B: TASK-2 — escalated revisit of A (explicit predecessor).
    db.insert_task(TaskRecord(
        id="TASK-2", brief="escalated revisit of root", team="engineering",
        assigned_agent="m",
        status=TaskStatus.ESCALATED, block_kind=None,
        revisit_of_task_id="TASK-1",
    ))

    result = await revisit_from_notification(
        org, state, task_id="TASK-2", founder_note="ruled in THR-X", actor="cli",
    )

    # B (explicit predecessor) superseded.
    pred_b = db.get_task("TASK-2")
    assert pred_b.status == TaskStatus.RESOLVED_SUPERSEDED
    assert pred_b.block_kind is None
    pb_payload = _audit_payload(db, "TASK-2", "escalation_superseded")
    assert pb_payload["successor_root"] == result.new_root_id
    assert pb_payload["prior_block_kind"] == "escalated"

    # A (ancestor root) must also be superseded.
    pred_a = db.get_task("TASK-1")
    assert pred_a.status == TaskStatus.RESOLVED_SUPERSEDED
    assert pred_a.block_kind is None
    pa_payload = _audit_payload(db, "TASK-1", "escalation_superseded")
    assert pa_payload["successor_root"] == result.new_root_id
    assert pa_payload["prior_block_kind"] == "escalated"

    # Gap-A: only the new root is enqueued.
    state.queue.enqueue.assert_called_with("acme", result.new_root_id)


@pytest.mark.asyncio
async def test_revisit_family_sibling_gets_parent_wake(tmp_path: Path):
    """Finding 2 (founder revisit tail): a closed family sibling must get
    parent-wake tail behavior. The family sibling has a delegated parent
    with all-terminal children — after the founder revisit, the parent
    must be enqueued."""
    from runtime.daemon.routes.tasks import revisit_from_notification

    org, state, db = _build_org(tmp_path)

    # Original escalated root.
    db.insert_task(TaskRecord(
        id="TASK-1", brief="original root", team="engineering",
        assigned_agent="m",
        status=TaskStatus.ESCALATED, block_kind=None,
    ))
    # Escalated sibling revisit with a delegated parent (TASK-P).
    db.insert_task(TaskRecord(
        id="TASK-2", brief="escalated sibling", team="engineering",
        assigned_agent="m",
        status=TaskStatus.ESCALATED, block_kind=None,
        revisit_of_task_id="TASK-1",
        parent_task_id="TASK-P",
    ))
    # Delegated parent: in_progress with all-terminal children (just TASK-2,
    # which will become terminal RESOLVED_SUPERSEDED).
    db.insert_task(TaskRecord(
        id="TASK-P", brief="delegated parent", team="engineering",
        assigned_agent="engineering_head",
        status=TaskStatus.IN_PROGRESS, block_kind=BlockKind.DELEGATED,
    ))

    await revisit_from_notification(
        org, state, task_id="TASK-1", founder_note="ruled in THR-X", actor="cli",
    )

    # Family sibling TASK-2 is closed.
    sib = db.get_task("TASK-2")
    assert sib.status == TaskStatus.RESOLVED_SUPERSEDED
    assert sib.block_kind is None

    # Parent-wake: the delegated parent TASK-P must be enqueued because
    # TASK-2 (its child) reached a terminal.
    # _enqueue_parent_if_waiting enqueues via orch._queue.put_nowait(slug, task_id).
    parent_enqueue_calls = [
        c for c in org.orchestrator._queue.put_nowait.call_args_list
        if c[0][1] == "TASK-P"
    ]
    assert len(parent_enqueue_calls) >= 1, (
        f"Expected parent-wake enqueue for TASK-P, got {org.orchestrator._queue.put_nowait.call_args_list}"
    )


@pytest.mark.asyncio
async def test_revisit_family_sibling_thread_originated_gets_thread_followup(
    tmp_path: Path,
):
    """Finding 2 (founder revisit tail): a thread-originated family sibling
    must get task-followup handling. The family sibling's revisit-chain ancestor
    is thread-dispatched — after the founder revisit, _maybe_post_thread_followup
    must fire for the sibling, posting a system message to the thread."""
    from runtime.daemon.routes.tasks import revisit_from_notification
    from runtime.infrastructure.audit_logger import AuditLogger

    org, state, db = _build_org(tmp_path)

    # Seed an OPEN thread.
    from runtime.models import ThreadRecord, ThreadStatus
    db.insert_thread(ThreadRecord(
        id="THR-DUMMY", status=ThreadStatus.OPEN, subject="test",
        created_by="founder",
    ))

    # TASK-A: escalated, thread-originated root (explicit predecessor).
    db.insert_task(TaskRecord(
        id="TASK-1", brief="original thread-dispatched root", team="engineering",
        assigned_agent="m",
        status=TaskStatus.ESCALATED, block_kind=None,
        dispatched_from_thread_id="THR-DUMMY",
    ))
    # Write a thread_dispatch audit row so _maybe_post_thread_followup can
    # resolve the dispatcher identity.
    audit = AuditLogger(db)
    audit.log_thread_dispatch(
        "THR-DUMMY", task_id="TASK-1", dispatcher="engineering_head",
        target_agent="m", team="engineering",
    )
    # TASK-B: escalated, revisits TASK-A (family sibling).
    db.insert_task(TaskRecord(
        id="TASK-2", brief="escalated sibling", team="engineering",
        assigned_agent="m",
        status=TaskStatus.ESCALATED, block_kind=None,
        revisit_of_task_id="TASK-1",
    ))

    await revisit_from_notification(
        org, state, task_id="TASK-1", founder_note="ruled in THR-X", actor="cli",
    )

    # Both TASK-A (explicit) and TASK-B (sibling) are superseded.
    assert db.get_task("TASK-1").status == TaskStatus.RESOLVED_SUPERSEDED
    sib = db.get_task("TASK-2")
    assert sib.status == TaskStatus.RESOLVED_SUPERSEDED
    assert sib.block_kind is None

    # Task-followup: a system message must have been posted to the thread
    # as evidence _maybe_post_thread_followup fired for the sibling.
    # The followup for TASK-2 walks the revisit chain [TASK-2, TASK-1],
    # finds original=TASK-1 with dispatched_from_thread_id="THR-DUMMY".
    system_messages = [
        m for m in db.list_thread_messages("THR-DUMMY")
        if m.kind == "system"
    ]
    assert len(system_messages) >= 1, (
        f"Expected thread-followup system message for TASK-2 in THR-DUMMY, "
        f"got messages: {system_messages}"
    )
