"""THR-080: thread-reachable resolve-escalation route tests."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from runtime.models import BlockKind, TaskRecord, TaskStatus, ThreadRecord, ThreadStatus


@pytest.mark.asyncio
async def test_thread_resolve_escalation_continue_succeeds(
    client_with_runtime,
):
    """THR-080 Option A: continue from thread surface re-enqueues the task."""
    client, org = client_with_runtime

    # Create a thread
    org.db.insert_thread(ThreadRecord(
        id="THR-1", subject="Test", composed_by="engineering_manager",
        status=ThreadStatus.OPEN,
    ))
    # Create an escalated task dispatched from this thread
    org.db.insert_task(TaskRecord(
        id="T-1", brief="test", dispatched_from_thread_id="THR-1",
    ))
    org.db.update_task("T-1", status=TaskStatus.ESCALATED, block_kind=None)

    r = client.post(
        "/api/v1/orgs/alpha/threads/THR-1/resolve-escalation",
        json={
            "task_id": "T-1",
            "decision": "continue",
            "rationale": "proceed",
            "actor": "engineering_manager",
        },
    )
    assert r.status_code == 200, f"got {r.status_code} {r.text}"
    assert r.json()["new_status"] == "pending"

    task = org.db.get_task("T-1")
    assert task.status == TaskStatus.PENDING


@pytest.mark.asyncio
async def test_thread_resolve_escalation_rejects_task_not_in_lineage(
    client_with_runtime,
):
    """THR-080: a task NOT in this thread's lineage -> 409."""
    client, org = client_with_runtime

    org.db.insert_thread(ThreadRecord(
        id="THR-2", subject="Test", composed_by="engineering_manager",
        status=ThreadStatus.OPEN,
    ))
    # Task from a DIFFERENT thread
    org.db.insert_task(TaskRecord(
        id="T-2", brief="test", dispatched_from_thread_id="OTHER-THREAD",
    ))
    org.db.update_task("T-2", status=TaskStatus.ESCALATED, block_kind=None)

    r = client.post(
        "/api/v1/orgs/alpha/threads/THR-2/resolve-escalation",
        json={
            "task_id": "T-2",
            "decision": "continue",
            "rationale": "nope",
        },
    )
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "task_not_in_thread_lineage"


@pytest.mark.asyncio
async def test_thread_resolve_escalation_rejects_invalid_decision(
    client_with_runtime,
):
    """THR-080: 'cancel' is rejected on the thread route too."""
    client, org = client_with_runtime

    org.db.insert_thread(ThreadRecord(
        id="THR-3", subject="Test", composed_by="engineering_manager",
        status=ThreadStatus.OPEN,
    ))
    org.db.insert_task(TaskRecord(
        id="T-3", brief="test", dispatched_from_thread_id="THR-3",
    ))
    org.db.update_task("T-3", status=TaskStatus.ESCALATED, block_kind=None)

    r = client.post(
        "/api/v1/orgs/alpha/threads/THR-3/resolve-escalation",
        json={
            "task_id": "T-3",
            "decision": "cancel",
            "rationale": "nope",
        },
    )
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "invalid_decision"


@pytest.mark.asyncio
async def test_thread_resolve_escalation_supersede_mints_successor(
    client_with_runtime,
):
    """THR-080: supersede from thread surface works."""
    client, org = client_with_runtime

    org.db.insert_thread(ThreadRecord(
        id="THR-4", subject="Test", composed_by="engineering_manager",
        status=ThreadStatus.OPEN,
    ))
    org.db.insert_task(TaskRecord(
        id="T-4", brief="original", dispatched_from_thread_id="THR-4",
    ))
    org.db.update_task("T-4", status=TaskStatus.ESCALATED, block_kind=None)

    r = client.post(
        "/api/v1/orgs/alpha/threads/THR-4/resolve-escalation",
        json={
            "task_id": "T-4",
            "decision": "supersede",
            "rationale": "reroute",
            "brief": "successor task",
        },
    )
    assert r.status_code == 200, f"got {r.status_code} {r.text}"
    assert r.json()["new_status"] == "superseded"

    predecessor = org.db.get_task("T-4")
    assert predecessor.status == TaskStatus.SUPERSEDED


@pytest.mark.asyncio
async def test_thread_resolve_escalation_continue_rejects_live_children(
    client_with_runtime,
):
    """THR-080 memo §3: continue from thread surface also rejects live children."""
    client, org = client_with_runtime

    org.db.insert_thread(ThreadRecord(
        id="THR-5", subject="Test", composed_by="engineering_manager",
        status=ThreadStatus.OPEN,
    ))
    org.db.insert_task(TaskRecord(
        id="T-5", brief="parent", dispatched_from_thread_id="THR-5",
    ))
    org.db.update_task("T-5", status=TaskStatus.ESCALATED, block_kind=None)
    # Add a non-terminal child.
    org.db.insert_task(
        TaskRecord(id="T-5-CHD", brief="child", parent_task_id="T-5")
    )

    r = client.post(
        "/api/v1/orgs/alpha/threads/THR-5/resolve-escalation",
        json={
            "task_id": "T-5",
            "decision": "continue",
            "rationale": "go",
        },
    )
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert detail["code"] == "cannot_continue_live_children"
    assert "supersede" in detail.get("remedy", "").lower()


@pytest.mark.asyncio
async def test_thread_resolve_escalation_checks_parent_chain_lineage(
    client_with_runtime,
):
    """THR-080: lineage check walks parent chain, not just dispatched_from_thread_id."""
    client, org = client_with_runtime

    org.db.insert_thread(ThreadRecord(
        id="THR-6", subject="Test", composed_by="engineering_manager",
        status=ThreadStatus.OPEN,
    ))
    # Root task dispatched from thread.
    org.db.insert_task(TaskRecord(
        id="T-ROOT", brief="root", dispatched_from_thread_id="THR-6",
    ))
    # Child of root (NOT directly dispatched from thread)
    org.db.insert_task(TaskRecord(
        id="T-CHD", brief="child", parent_task_id="T-ROOT",
    ))
    org.db.update_task("T-CHD", status=TaskStatus.ESCALATED, block_kind=None)

    r = client.post(
        "/api/v1/orgs/alpha/threads/THR-6/resolve-escalation",
        json={
            "task_id": "T-CHD",
            "decision": "continue",
            "rationale": "proceed",
        },
    )
    assert r.status_code == 200, f"got {r.status_code} {r.text}"
    assert r.json()["new_status"] == "pending"
