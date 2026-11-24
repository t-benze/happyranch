"""GH-1852 focused ASGI/SQLite checks; no lifespan, provider or live org."""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from scripts.test_agent_retirement import PREFIX, fixture
from runtime.daemon.org_state import OrgState
from runtime.models import (TaskRecord, TaskStatus, ThreadInvocationPurpose,
                            ThreadMessageKind, ThreadRecord)
from runtime.orchestrator.run_step import (_maybe_post_thread_escalation,
                                           _maybe_post_thread_followup)

THREAD = "THR-dispatch"
PREDECESSOR = "TASK-001"
OrgClient = tuple[OrgState, httpx.AsyncClient]


@pytest.fixture
async def org_client(tmp_path: Path) -> AsyncIterator[OrgClient]:
    async with fixture(tmp_path / "org") as (org, client):
        org.db.insert_thread(ThreadRecord(id=THREAD, subject="Synthetic continuation"))
        for agent in ("manager", "keeper"):
            org.db.add_thread_participant(THREAD, agent, added_by="founder")
        org.orchestrator.attach_thread_queue(org.thread_queue, asyncio.get_running_loop(), rediscover=False)
        yield org, client


def seed(org: OrgState, *, dispatcher: str | None = "keeper", linked: bool = True,
         revisit: bool = False) -> None:
    if revisit:
        org.db.insert_task(TaskRecord(id="TASK-original", brief="Synthetic original",
            status=TaskStatus.FAILED, dispatched_from_thread_id=THREAD))
    org.db.insert_task(TaskRecord(id=PREDECESSOR, brief="Synthetic escalation",
        team="retiring", assigned_agent="manager", status=TaskStatus.ESCALATED,
        dispatched_from_thread_id=THREAD if linked else None,
        revisit_of_task_id="TASK-original" if revisit else None))
    if dispatcher is not None:
        org.orchestrator._audit.log_thread_dispatch(THREAD,
            task_id="TASK-original" if revisit else PREDECESSOR,
            dispatcher=dispatcher, target_agent="manager", team="retiring")


def invocation(org: OrgState) -> str:
    seq = org.db.append_thread_message(thread_id=THREAD, speaker="founder",
        kind=ThreadMessageKind.MESSAGE, body_markdown="Synthetic resolution instruction")
    return org.db.mint_thread_invocation(thread_id=THREAD, agent_name="manager",
        triggering_seq=seq, purpose=ThreadInvocationPurpose.REPLY).invocation_token


async def supersede(org: OrgState, client: httpx.AsyncClient, *, task_id: str = PREDECESSOR,
                    token: str | None = None, actor: str = "founder") -> str:
    if token is not None:
        response = await client.post(f"{PREFIX}/threads/{THREAD}/resolve-escalation", json={
            "task_id": task_id, "decision": "supersede", "brief": "Synthetic successor",
            "invocation_token": token, "dispatcher": "manager", "rationale": "Synthetic fix"})
    else:
        response = await client.post(f"{PREFIX}/tasks/{task_id}/resolve-escalation", json={
            "decision": "supersede", "brief": "Synthetic successor", "actor": actor})
    assert response.status_code == 200, response.text
    assert response.json()["new_status"] == "superseded"
    return next(row["payload"]["successor_root"] for row in org.db.get_audit_logs(task_id)
                if row["action"] == "escalation_superseded")


def dispatch_rows(org: OrgState, task_id: str) -> list[dict]:
    return [row for row in org.db.get_audit_logs_by_action("thread_dispatch")
            if row["payload"]["task_id"] == task_id]


def assert_dispatch(org: OrgState, successor: str, dispatcher: str, predecessor: str = PREDECESSOR) -> None:
    rows = dispatch_rows(org, successor)
    assert len(rows) == 1
    assert rows[0]["task_id"] == THREAD and rows[0]["agent"] == dispatcher
    assert rows[0]["payload"] == {"task_id": successor, "dispatcher": dispatcher,
        "target_agent": "manager", "team": "retiring", "superseded_task_id": predecessor}
    assert org.db.get_task(successor).revisit_of_task_id is None


async def assert_event(org: OrgState, task_id: str, kind: str, status: str, dispatcher: str) -> None:
    messages = [message for message in org.db.list_thread_messages(THREAD)
                if message.system_payload and message.system_payload.get("task_id") == task_id
                and message.system_payload.get("kind_tag") == kind]
    assert len(messages) == 1
    message = messages[0]
    assert message.kind == ThreadMessageKind.SYSTEM and message.speaker == dispatcher
    assert message.system_payload["status"] == status
    assert message.system_payload["original_task_id"] == task_id
    invs = org.db.execute("SELECT * FROM thread_invocations WHERE thread_id=? AND triggering_seq=?",
                          (THREAD, message.seq)).fetchall()
    assert len(invs) == 1
    assert invs[0]["agent_name"] == dispatcher and invs[0]["purpose"] == "task_followup"
    assert invs[0]["status"] == "pending"
    # Observe delivery at the real queue boundary, without starting a provider.
    tokens = []
    while org.thread_queue.size:
        job = await org.thread_queue.get()
        tokens.append(job.invocation_token)
    assert invs[0]["invocation_token"] in tokens


@pytest.mark.parametrize("resolver", ["thread", "founder", "agent"])
async def test_successor_escalation_dispatcher(org_client: OrgClient, resolver: str) -> None:
    org, client = org_client
    seed(org)
    successor = await supersede(org, client, token=invocation(org) if resolver == "thread" else None,
                                actor="manager" if resolver == "agent" else "founder")
    dispatcher = "keeper" if resolver == "founder" else "manager"
    org.db.update_task(successor, status=TaskStatus.ESCALATED)
    await asyncio.to_thread(_maybe_post_thread_escalation, org.orchestrator, successor,
                            reason="Synthetic successor blocker")
    await assert_event(org, successor, "task_escalated", "escalated", dispatcher)
    assert_dispatch(org, successor, dispatcher)


@pytest.mark.parametrize("status", [TaskStatus.COMPLETED, TaskStatus.FAILED,
                                    TaskStatus.SUPERSEDED, TaskStatus.CANCELLED])
async def test_successor_terminal_followup(org_client: OrgClient, status: TaskStatus) -> None:
    org, client = org_client
    seed(org)
    successor = await supersede(org, client, token=invocation(org))
    org.db.update_task(successor, status=status)
    await asyncio.to_thread(_maybe_post_thread_followup, org.orchestrator, successor,
                            status=status, auto_revisit_spawned=False)
    kind = "task_failed" if status in (TaskStatus.FAILED, TaskStatus.CANCELLED) else "task_completed"
    await assert_event(org, successor, kind, status.value, "manager")


async def test_chained_supersede_and_replay(org_client: OrgClient) -> None:
    org, client = org_client
    seed(org)
    token = invocation(org)
    successor = await supersede(org, client, token=token)
    org.db.update_task(successor, status=TaskStatus.ESCALATED)
    latest = await supersede(org, client, task_id=successor, token=token)
    await asyncio.sleep(0)  # Deliver run_coroutine_threadsafe publications.
    await asyncio.sleep(0)
    await assert_event(org, successor, "task_completed", "superseded", "manager")
    assert_dispatch(org, latest, "manager", successor)
    before = tuple(org.db._conn.iterdump())
    assert await supersede(org, client, task_id=successor, token=token) == latest
    assert tuple(org.db._conn.iterdump()) == before
    inv = org.db.get_pending_invocation(token)
    assert inv is not None and inv.dispatched_task_id is None
    assert org.db.execute("SELECT * FROM thread_invocation_dispatches").fetchall() == []
    org.db.update_task(latest, status=TaskStatus.COMPLETED)
    await asyncio.to_thread(_maybe_post_thread_followup, org.orchestrator, latest,
                            status=TaskStatus.COMPLETED, auto_revisit_spawned=False)
    await assert_event(org, latest, "task_completed", "completed", "manager")


async def test_founder_inherits_revisit_origin(org_client: OrgClient) -> None:
    org, client = org_client
    seed(org, revisit=True)
    successor = await supersede(org, client)
    assert_dispatch(org, successor, "keeper")


@pytest.mark.parametrize("case", ["unresolved", "founder-row", "unlinked"])
async def test_no_invented_dispatcher(org_client: OrgClient, case: str) -> None:
    org, client = org_client
    seed(org, dispatcher=None if case == "unresolved" else "founder" if case == "founder-row" else "keeper",
         linked=case != "unlinked")
    successor = await supersede(org, client, actor="manager" if case == "unlinked" else "founder")
    assert dispatch_rows(org, successor) == []


async def test_dispatch_audit_failure_rolls_back_supersession(org_client: OrgClient,
                                                            monkeypatch: pytest.MonkeyPatch) -> None:
    org, client = org_client
    seed(org)
    token = invocation(org)
    before = tuple(org.db._conn.iterdump())
    insert = org.db.insert_audit_log_uncommitted

    def fail_dispatch(*args: Any, **kwargs: Any) -> int:
        if kwargs.get("action") == "thread_dispatch":
            assert org.db._conn.in_transaction
            raise RuntimeError("Synthetic dispatch audit failure")
        return insert(*args, **kwargs)

    monkeypatch.setattr(org.db, "insert_audit_log_uncommitted", fail_dispatch)
    with pytest.raises(RuntimeError, match="Synthetic dispatch audit failure"):
        await supersede(org, client, token=token)
    assert tuple(org.db._conn.iterdump()) == before


@pytest.mark.parametrize("old_status", [TaskStatus.ESCALATED, TaskStatus.COMPLETED])
async def test_no_retroactive_surfacing(org_client: OrgClient, old_status: TaskStatus) -> None:
    org, client = org_client
    seed(org)
    # An already-created continuation root from the old writer has no dispatch row.
    org.db.insert_task(TaskRecord(id="TASK-old", brief="Synthetic historical continuation",
        status=old_status, team="retiring", assigned_agent="manager",
        dispatched_from_thread_id=THREAD))
    org.db.insert_task(TaskRecord(id="TASK-old-predecessor", brief="Synthetic historical predecessor",
        status=TaskStatus.SUPERSEDED, dispatched_from_thread_id=THREAD))
    org.orchestrator._audit.log_escalation_superseded("TASK-old-predecessor", successor_root="TASK-old",
        prior_block_kind="escalated", actor="founder")
    org.orchestrator.attach_thread_queue(org.thread_queue, asyncio.get_running_loop())
    successor = await supersede(org, client, token=invocation(org))
    org.db.update_task(successor, status=TaskStatus.ESCALATED)
    await asyncio.to_thread(_maybe_post_thread_escalation, org.orchestrator, successor, reason="New event")
    await assert_event(org, successor, "task_escalated", "escalated", "manager")
    assert dispatch_rows(org, "TASK-old") == []
    assert not any(message.system_payload and message.system_payload.get("task_id") == "TASK-old"
                   for message in org.db.list_thread_messages(THREAD))
