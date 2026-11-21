"""Focused founder revisit API checks in the existing no-launch synthetic venue."""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from scripts.test_agent_retirement import PREFIX, fixture
from scripts.test_task_pause_resume import queued
from runtime.daemon.org_state import OrgState
from runtime.models import BlockKind, TaskRecord, TaskStatus

OrgClient = tuple[OrgState, httpx.AsyncClient]
TERMINAL = (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED, TaskStatus.SUPERSEDED)


@pytest.fixture
async def org_client(tmp_path: Path) -> AsyncIterator[OrgClient]:
    async with fixture(tmp_path / "org") as value:
        yield value


def seed(org: OrgState, task_id: str = "TASK-001", *, status: TaskStatus = TaskStatus.COMPLETED,
         predecessor: str | None = None, block: BlockKind | None = None) -> None:
    org.db.insert_task(TaskRecord(id=task_id, brief="Synthetic revisit work", team="retiring",
                                 assigned_agent="worker", status=status, block_kind=block,
                                 revisit_of_task_id=predecessor))
    if status == TaskStatus.CANCELLED:
        org.db.execute("UPDATE tasks SET cancelled_at=? WHERE id=?",
                       (datetime.now(timezone.utc).isoformat(), task_id))
        org.db._conn.commit()


async def revisit(client: httpx.AsyncClient, task_id: str = "TASK-001") -> httpx.Response:
    return await client.post(f"{PREFIX}/tasks/{task_id}/revisit", json={})


async def refused(org: OrgState, client: httpx.AsyncClient, successor: str, status: TaskStatus) -> None:
    # Observe all persisted state, including Pause and audits, rather than just
    # checking the expected tasks table. No refusal may consume or mutate it.
    before = tuple(org.db._conn.iterdump())
    response = await revisit(client)
    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert detail == {
        "code": "revisit_successor_live",
        "reason": f"Successor {successor} of predecessor TASK-001 is still {status.value}.",
        "predecessor_root_task_id": "TASK-001",
        "existing_successor_task_id": successor,
        "existing_successor_status": status.value,
    }
    assert tuple(org.db._conn.iterdump()) == before
    assert queued(org) == []


@pytest.mark.parametrize("status", [TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED])
async def test_sequential_double_revisit(org_client: OrgClient, status: TaskStatus) -> None:
    org, client = org_client
    seed(org, status=status)
    first = await revisit(client)
    assert first.status_code == 200, first.text
    successor = first.json()["new_root_task_id"]
    assert queued(org) == [(org.slug, successor, None)]
    await refused(org, client, successor, TaskStatus.PENDING)
    assert org.db.get_direct_revisits("TASK-001") == [successor]
    assert org.db.get_task("TASK-001").status == status


async def test_concurrent_double_revisit(org_client: OrgClient) -> None:
    org, client = org_client
    seed(org)
    # Both requests reach admission with the same terminal predecessor snapshot.
    async with org.db_lock:
        calls = [asyncio.create_task(revisit(client)) for _ in range(2)]
        await asyncio.sleep(0)
        assert not any(call.done() for call in calls)
    responses = await asyncio.gather(*calls)
    assert sorted(response.status_code for response in responses) == [200, 409]
    success = next(response for response in responses if response.status_code == 200)
    successor = success.json()["new_root_task_id"]
    failure = next(response for response in responses if response.status_code == 409)
    assert failure.json()["detail"]["existing_successor_task_id"] == successor
    assert failure.json()["detail"]["code"] == "revisit_successor_live"
    assert org.db.get_direct_revisits("TASK-001") == [successor]
    assert len(org.db.execute("SELECT id FROM tasks").fetchall()) == 2
    assert len(org.db.execute("SELECT id FROM audit_log WHERE action='revisit_of'").fetchall()) == 1
    assert queued(org) == [(org.slug, successor, None)]


@pytest.mark.parametrize("status", TERMINAL)
async def test_revisit_after_successor_terminal(org_client: OrgClient, status: TaskStatus) -> None:
    org, client = org_client
    seed(org)
    first = await revisit(client)
    assert first.status_code == 200, first.text
    successor = first.json()["new_root_task_id"]
    org.db.update_task(successor, status=status)
    second = await revisit(client)
    assert second.status_code == 200, second.text
    assert second.json()["new_root_task_id"] != successor
    assert len(org.db.get_direct_revisits("TASK-001")) == 2


@pytest.mark.parametrize(("status", "block"), [
    (TaskStatus.PENDING, None),
    (TaskStatus.IN_PROGRESS, None),
    (TaskStatus.IN_PROGRESS, BlockKind.DELEGATED),
    (TaskStatus.IN_PROGRESS, BlockKind.BLOCKED_ON_JOB),
    (TaskStatus.ESCALATED, None),
])
async def test_transitive_live_successor(org_client: OrgClient, status: TaskStatus,
                                         block: BlockKind | None) -> None:
    org, client = org_client
    seed(org)
    seed(org, "TASK-002", status=TaskStatus.FAILED, predecessor="TASK-001")
    seed(org, "TASK-003", status=status, predecessor="TASK-002", block=block)
    await refused(org, client, "TASK-003", status)


async def test_escalated_successor_can_itself_be_revisited(org_client: OrgClient) -> None:
    org, client = org_client
    seed(org)
    seed(org, "TASK-002", status=TaskStatus.ESCALATED, predecessor="TASK-001", block=None)
    response = await revisit(client, "TASK-002")
    assert response.status_code == 200, response.text
    successor = response.json()["new_root_task_id"]
    assert org.db.get_task("TASK-002").status == TaskStatus.SUPERSEDED
    assert queued(org) == [(org.slug, successor, None)]
    await refused(org, client, successor, TaskStatus.PENDING)


async def test_flagged_child_uses_root_successors(org_client: OrgClient) -> None:
    org, client = org_client
    seed(org)
    seed(org, "TASK-002", status=TaskStatus.PENDING, predecessor="TASK-001")
    org.db.insert_task(TaskRecord(id="TASK-003", brief="Synthetic flagged leaf", parent_task_id="TASK-001",
                                 status=TaskStatus.FAILED))
    response = await revisit(client, "TASK-003")
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["predecessor_root_task_id"] == "TASK-001"
    assert response.json()["detail"]["existing_successor_task_id"] == "TASK-002"
