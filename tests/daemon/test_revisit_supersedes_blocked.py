"""§3(a) auto-resolve forcing function + Gap-B delegated close (THR-018 tier #3).

A founder `revisit` (or Feishu-reply revisit) of a blocked(escalated|delegated)
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
