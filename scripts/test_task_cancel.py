"""Focused no-launch founder cancel route checks; see docs/local-ci.md."""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock, patch

import httpx
import pytest

from scripts.test_agent_retirement import PREFIX, fixture
from scripts.test_task_pause_resume import queued
from runtime.daemon.org_state import OrgState
from runtime.models import BlockKind, TaskRecord, TaskStatus
from runtime.orchestrator.run_step import _enqueue_parent_if_waiting
from runtime.workflows.recovery import WorkflowTaskOwnership, classify_task

OrgClient = tuple[OrgState, httpx.AsyncClient]


@pytest.fixture
async def org_client(tmp_path: Path) -> AsyncIterator[OrgClient]:
    async with fixture(tmp_path / "org") as value:
        yield value


def seed(org: OrgState, *, child_status: TaskStatus = TaskStatus.PENDING,
         child_block: BlockKind | None = None) -> None:
    org.db.insert_task(TaskRecord(id="TASK-parent", brief="Synthetic waiting manager",
        assigned_agent="manager", team="retiring", status=TaskStatus.IN_PROGRESS,
        block_kind=BlockKind.DELEGATED))
    org.db.insert_task(TaskRecord(id="TASK-child", parent_task_id="TASK-parent",
        brief="Synthetic cancelled child", assigned_agent="worker", team="retiring",
        status=child_status, block_kind=child_block))


@pytest.mark.parametrize("child_status,child_block", [
    (TaskStatus.PENDING, None),
    (TaskStatus.ESCALATED, None),
    (TaskStatus.IN_PROGRESS, None),
    (TaskStatus.IN_PROGRESS, BlockKind.DELEGATED),
    (TaskStatus.IN_PROGRESS, BlockKind.BLOCKED_ON_JOB),
])
async def test_cancel_child_wakes_parent_after_commit(
    org_client: OrgClient, child_status: TaskStatus, child_block: BlockKind | None,
) -> None:
    org, client = org_client
    seed(org, child_status=child_status, child_block=child_block)
    parent = org.db.get_task("TASK-parent")
    enqueue = org.orchestrator._queue.enqueue
    publications = []

    def observe_enqueue(*args: Any, **kwargs: Any) -> None:
        assert not org.db_lock.locked()
        assert not org.db._lock._is_owned()
        assert not org.db._conn.in_transaction
        child = org.db.get_task("TASK-child")
        assert child.status == TaskStatus.CANCELLED
        assert child.cancelled_at is not None
        publications.append(args)
        enqueue(*args, **kwargs)

    with patch.object(org.orchestrator._queue, "enqueue", side_effect=observe_enqueue):
        response = await client.post(f"{PREFIX}/tasks/TASK-child/cancel", json={"rationale": "stop"})
    assert response.status_code == 200, response.text
    assert response.json()["cancelled"] == ["TASK-child"]
    assert queued(org) == [(org.slug, "TASK-parent", None)]
    assert publications == [(org.slug, "TASK-parent")]
    assert org.db.get_task("TASK-parent") == parent
    assert org.db.get_task("TASK-child").note == "cancelled by founder: stop"
    assert any(row["action"] == "task_cancelled" for row in org.db.get_audit_logs("TASK-child"))


@pytest.mark.parametrize("cascade", [True, False])
async def test_cancel_subtree_notifies_only_topmost_node(org_client: OrgClient, cascade: bool) -> None:
    org, client = org_client
    seed(org, child_status=TaskStatus.IN_PROGRESS, child_block=BlockKind.DELEGATED)
    org.db.insert_task(TaskRecord(id="TASK-grandchild", parent_task_id="TASK-child", brief="Synthetic leaf"))
    with patch("runtime.orchestrator.run_step._enqueue_parent_if_waiting",
               wraps=_enqueue_parent_if_waiting) as wake:
        response = await client.post(f"{PREFIX}/tasks/TASK-child/cancel", json={"cascade": cascade})
    assert response.status_code == 200, response.text
    assert set(response.json()["cancelled"]) == ({"TASK-child", "TASK-grandchild"} if cascade else {"TASK-child"})
    assert org.db.get_task("TASK-grandchild").status == (TaskStatus.CANCELLED if cascade else TaskStatus.PENDING)
    assert queued(org) == [(org.slug, "TASK-parent", None)]
    wake.assert_called_once_with(org.orchestrator, "TASK-child")


async def test_cancel_root_never_notifies_descendant_parents(org_client: OrgClient) -> None:
    org, client = org_client
    seed(org)
    with patch("runtime.orchestrator.run_step._enqueue_parent_if_waiting",
               wraps=_enqueue_parent_if_waiting) as wake:
        response = await client.post(f"{PREFIX}/tasks/TASK-parent/cancel", json={})
    assert response.status_code == 200, response.text
    assert set(response.json()["cancelled"]) == {"TASK-parent", "TASK-child"}
    assert org.db.get_task("TASK-parent").status == TaskStatus.CANCELLED
    assert org.db.get_task("TASK-child").status == TaskStatus.CANCELLED
    assert queued(org) == []
    wake.assert_not_called()


async def test_live_sibling_holds_parent_until_last_cancel(org_client: OrgClient) -> None:
    org, client = org_client
    seed(org)
    org.db.insert_task(TaskRecord(id="TASK-sibling", parent_task_id="TASK-parent", brief="Synthetic sibling"))
    first = await client.post(f"{PREFIX}/tasks/TASK-child/cancel", json={})
    assert first.status_code == 200, first.text
    assert org.db.get_task("TASK-child").status == TaskStatus.CANCELLED
    assert org.db.get_task("TASK-sibling").status == TaskStatus.PENDING
    assert queued(org) == []
    last = await client.post(f"{PREFIX}/tasks/TASK-sibling/cancel", json={})
    assert last.status_code == 200, last.text
    assert queued(org) == [(org.slug, "TASK-parent", None)]
    duplicate = await client.post(f"{PREFIX}/tasks/TASK-sibling/cancel", json={})
    assert duplicate.status_code == 409, duplicate.text
    assert duplicate.json()["detail"]["code"] == "task_already_terminal"
    assert queued(org) == []


async def test_cancel_losing_terminal_race_never_wakes(org_client: OrgClient) -> None:
    org, client = org_client
    seed(org)
    read = asyncio.Event()
    get_task = org.db.get_task

    def observe_read(task_id: str) -> TaskRecord | None:
        result = get_task(task_id)
        if task_id == "TASK-child":
            read.set()
        return result

    async with org.db_lock:
        with patch.object(org.db, "get_task", side_effect=observe_read):
            request = asyncio.create_task(client.post(f"{PREFIX}/tasks/TASK-child/cancel", json={}))
            await asyncio.wait_for(read.wait(), timeout=5)
        org.db.update_task("TASK-child", status=TaskStatus.COMPLETED)
    response = await request
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "task_already_terminal"
    assert org.db.get_task("TASK-child").status == TaskStatus.COMPLETED
    assert queued(org) == []


@pytest.mark.parametrize("selected_member", [False, True])
async def test_mixed_delivery_selects_topmost_committed_node(
    org_client: OrgClient, selected_member: bool,
) -> None:
    org, client = org_client
    seed(org, child_status=TaskStatus.IN_PROGRESS, child_block=BlockKind.DELEGATED)
    org.db.insert_task(TaskRecord(id="TASK-grandchild", parent_task_id="TASK-child", brief="Synthetic leaf"))
    prior = {tid: org.db.get_task(tid) for tid in ("TASK-child", "TASK-grandchild")}
    seal = Mock()

    def ownership(db: Any, task_id: str, **kwargs: Any) -> WorkflowTaskOwnership:
        if selected_member and task_id == "TASK-grandchild":
            return WorkflowTaskOwnership("seq_visit")
        return classify_task(db, task_id, **kwargs)

    async def committed_delivery(*args: Any, **kwargs: Any) -> dict:
        # Isolate the owner's existing post-COMMIT delivery port. The real route,
        # topmost selection, parent helper and queue remain under observation.
        async with org.db_lock:
            with org.db._lock:
                org.db._conn.execute("BEGIN IMMEDIATE")
                try:
                    for tid in prior:
                        org.db._conn.execute("UPDATE tasks SET status='cancelled',block_kind=NULL,"
                            "cancelled_at='2026-11-14T00:00:00+00:00' WHERE id=?", (tid,))
                    org.db._conn.commit()
                except BaseException:
                    org.db._conn.rollback()
                    raise
        return dict(tasks=["TASK-grandchild", "TASK-child"],
                    prior={tid: task.status for tid, task in prior.items()},
                    block_kinds={tid: task.block_kind for tid, task in prior.items()},
                    controls=[], owners=SimpleNamespace(seal=seal))

    target = "TASK-grandchild" if selected_member else "TASK-child"
    with patch("runtime.workflows.recovery.classify_task", side_effect=ownership), \
         patch("runtime.workflows.sequential_mixed_cancel.has_sequential_descendant", return_value=True), \
         patch("runtime.workflows.sequential_mixed_cancel.cancel_mixed", side_effect=committed_delivery), \
         patch("runtime.orchestrator.run_step._enqueue_parent_if_waiting", wraps=_enqueue_parent_if_waiting) as wake:
        response = await client.post(f"{PREFIX}/tasks/{target}/cancel", json={})
    assert response.status_code == 200, response.text
    assert response.json()["cancelled"] == ["TASK-grandchild", "TASK-child"]
    assert all(org.db.get_task(tid).status == TaskStatus.CANCELLED for tid in prior)
    assert queued(org) == [(org.slug, "TASK-parent", None)]
    wake.assert_called_once_with(org.orchestrator, "TASK-child")
    seal.assert_called_once_with(failed=False)


async def test_refused_mixed_cancel_rolls_back_without_wake(org_client: OrgClient) -> None:
    org, client = org_client
    seed(org)
    child = org.db.get_task("TASK-child")

    async def refuse(*args: Any, **kwargs: Any) -> None:
        async with org.db_lock:
            with org.db._lock:
                org.db._conn.execute("BEGIN IMMEDIATE")
                try:
                    org.db._conn.execute("UPDATE tasks SET status='cancelled' WHERE id='TASK-child'")
                    raise ValueError("synthetic owner validation refusal")
                finally:
                    org.db._conn.rollback()

    with patch("runtime.workflows.sequential_mixed_cancel.has_sequential_descendant", return_value=True), \
         patch("runtime.workflows.sequential_mixed_cancel.cancel_mixed", side_effect=refuse):
        response = await client.post(f"{PREFIX}/tasks/TASK-child/cancel", json={})
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "sequential_cancellation_reconciliation_required"
    assert org.db.get_task("TASK-child") == child
    assert queued(org) == []
