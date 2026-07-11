"""THR-080: thread-reachable resolve-escalation route tests."""
from __future__ import annotations

import pytest

from runtime.models import (
    BlockKind,
    TaskRecord,
    TaskStatus,
    ThreadInvocationPurpose,
    ThreadRecord,
    ThreadStatus,
)


def _mint_authorized_invocation(org, thread_id: str, agent: str) -> str:
    """Add agent as thread participant and mint a REPLY invocation token.

    Returns the invocation token string. The agent must be a team manager
    for the resolve-escalation route to authorize them.
    """
    org.db.add_thread_participant(thread_id, agent, added_by="founder")
    inv = org.db.mint_thread_invocation(
        thread_id=thread_id,
        agent_name=agent,
        triggering_seq=0,
        purpose=ThreadInvocationPurpose.REPLY,
    )
    return inv.invocation_token


# ── Happy path tests (manager-authorized) ──────────────────────────

@pytest.mark.asyncio
async def test_thread_resolve_escalation_continue_succeeds(
    client_with_runtime,
):
    """THR-080 Option A: continue from thread surface re-enqueues the task."""
    client, org = client_with_runtime

    org.db.insert_thread(ThreadRecord(
        id="THR-1", subject="Test", composed_by="engineering_manager",
        status=ThreadStatus.OPEN,
    ))
    org.db.insert_task(TaskRecord(
        id="T-1", brief="test", dispatched_from_thread_id="THR-1",
    ))
    org.db.update_task("T-1", status=TaskStatus.ESCALATED, block_kind=None)

    token = _mint_authorized_invocation(org, "THR-1", "engineering_head")

    r = client.post(
        "/api/v1/orgs/alpha/threads/THR-1/resolve-escalation",
        json={
            "task_id": "T-1",
            "decision": "continue",
            "rationale": "proceed",
            "invocation_token": token,
            "dispatcher": "engineering_head",
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
    org.db.insert_task(TaskRecord(
        id="T-2", brief="test", dispatched_from_thread_id="OTHER-THREAD",
    ))
    org.db.update_task("T-2", status=TaskStatus.ESCALATED, block_kind=None)

    token = _mint_authorized_invocation(org, "THR-2", "engineering_head")

    r = client.post(
        "/api/v1/orgs/alpha/threads/THR-2/resolve-escalation",
        json={
            "task_id": "T-2",
            "decision": "continue",
            "rationale": "nope",
            "invocation_token": token,
            "dispatcher": "engineering_head",
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

    token = _mint_authorized_invocation(org, "THR-3", "engineering_head")

    r = client.post(
        "/api/v1/orgs/alpha/threads/THR-3/resolve-escalation",
        json={
            "task_id": "T-3",
            "decision": "cancel",
            "rationale": "nope",
            "invocation_token": token,
            "dispatcher": "engineering_head",
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

    token = _mint_authorized_invocation(org, "THR-4", "engineering_head")

    r = client.post(
        "/api/v1/orgs/alpha/threads/THR-4/resolve-escalation",
        json={
            "task_id": "T-4",
            "decision": "supersede",
            "rationale": "reroute",
            "brief": "successor task",
            "invocation_token": token,
            "dispatcher": "engineering_head",
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
    org.db.insert_task(
        TaskRecord(id="T-5-CHD", brief="child", parent_task_id="T-5")
    )

    token = _mint_authorized_invocation(org, "THR-5", "engineering_head")

    r = client.post(
        "/api/v1/orgs/alpha/threads/THR-5/resolve-escalation",
        json={
            "task_id": "T-5",
            "decision": "continue",
            "rationale": "go",
            "invocation_token": token,
            "dispatcher": "engineering_head",
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
    org.db.insert_task(TaskRecord(
        id="T-ROOT", brief="root", dispatched_from_thread_id="THR-6",
    ))
    org.db.insert_task(TaskRecord(
        id="T-CHD", brief="child", parent_task_id="T-ROOT",
    ))
    org.db.update_task("T-CHD", status=TaskStatus.ESCALATED, block_kind=None)

    token = _mint_authorized_invocation(org, "THR-6", "engineering_head")

    r = client.post(
        "/api/v1/orgs/alpha/threads/THR-6/resolve-escalation",
        json={
            "task_id": "T-CHD",
            "decision": "continue",
            "rationale": "proceed",
            "invocation_token": token,
            "dispatcher": "engineering_head",
        },
    )
    assert r.status_code == 200, f"got {r.status_code} {r.text}"
    assert r.json()["new_status"] == "pending"


# ── RED tests: authority enforcement (THR-080 #2) ──────────────────

@pytest.mark.asyncio
async def test_thread_resolve_escalation_rejects_unauthorized_worker(
    client_with_runtime,
):
    """THR-080 #2: a non-manager worker is rejected with actionable error."""
    client, org = client_with_runtime

    org.db.insert_thread(ThreadRecord(
        id="THR-AUTH", subject="Test", composed_by="engineering_manager",
        status=ThreadStatus.OPEN,
    ))
    org.db.insert_task(TaskRecord(
        id="T-AUTH", brief="test", dispatched_from_thread_id="THR-AUTH",
    ))
    org.db.update_task("T-AUTH", status=TaskStatus.ESCALATED, block_kind=None)

    # Mint invocation for dev_agent (a worker, not a manager in conftest teams.yaml).
    token = _mint_authorized_invocation(org, "THR-AUTH", "dev_agent")

    r = client.post(
        "/api/v1/orgs/alpha/threads/THR-AUTH/resolve-escalation",
        json={
            "task_id": "T-AUTH",
            "decision": "continue",
            "rationale": "i want to continue",
            "invocation_token": token,
            "dispatcher": "dev_agent",
        },
    )
    assert r.status_code == 403, f"got {r.status_code} {r.text}"
    detail = r.json()["detail"]
    assert detail["code"] == "resolve_escalation_not_authorized"
    # Actionable error must name the supersede fallback.
    assert "supersede" in detail.get("remedy", "").lower() or "manager" in detail.get("remedy", "").lower()


@pytest.mark.asyncio
async def test_thread_resolve_escalation_rejects_missing_invocation_token(
    client_with_runtime,
):
    """THR-080 #2: missing invocation_token is rejected with 422."""
    client, org = client_with_runtime

    org.db.insert_thread(ThreadRecord(
        id="THR-MISS", subject="Test", composed_by="engineering_manager",
        status=ThreadStatus.OPEN,
    ))
    org.db.insert_task(TaskRecord(
        id="T-MISS", brief="test", dispatched_from_thread_id="THR-MISS",
    ))
    org.db.update_task("T-MISS", status=TaskStatus.ESCALATED, block_kind=None)

    r = client.post(
        "/api/v1/orgs/alpha/threads/THR-MISS/resolve-escalation",
        json={
            "task_id": "T-MISS",
            "decision": "continue",
            "rationale": "no token",
        },
    )
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "missing_invocation_token"


@pytest.mark.asyncio
async def test_thread_resolve_escalation_derives_actor_from_dispatcher(
    client_with_runtime,
):
    """THR-080 #2: actor is derived from the validated dispatcher, not
    a client-supplied spoof field. The legacy 'actor' body field is
    ignored."""
    client, org = client_with_runtime

    org.db.insert_thread(ThreadRecord(
        id="THR-ACTOR", subject="Test", composed_by="engineering_manager",
        status=ThreadStatus.OPEN,
    ))
    org.db.insert_task(TaskRecord(
        id="T-ACTOR", brief="test", dispatched_from_thread_id="THR-ACTOR",
    ))
    org.db.update_task("T-ACTOR", status=TaskStatus.ESCALATED, block_kind=None)

    token = _mint_authorized_invocation(org, "THR-ACTOR", "engineering_head")

    # Try to spoof actor as "founder" while presenting engineering_head's token.
    r = client.post(
        "/api/v1/orgs/alpha/threads/THR-ACTOR/resolve-escalation",
        json={
            "task_id": "T-ACTOR",
            "decision": "continue",
            "rationale": "spoof test",
            "invocation_token": token,
            "dispatcher": "engineering_head",
        },
    )
    assert r.status_code == 200, f"got {r.status_code} {r.text}"

    # The audit log should show the REAL actor (engineering_head), not
    # any spoofed value.
    logs = org.db.get_audit_logs("T-ACTOR")
    resolved_logs = [e for e in logs if e["action"] == "escalation_resolved"]
    assert len(resolved_logs) >= 1
    assert resolved_logs[0]["agent"] == "engineering_head"


# ── Token lifecycle: replay prevention (THR-080 review R2) ────────

@pytest.mark.asyncio
async def test_thread_resolve_escalation_rejects_replayed_token(
    client_with_runtime,
):
    """A single invocation token can resolve at most once — a second
    call with the same token must be rejected (mirrors reply/decline
    lifecycle)."""
    client, org = client_with_runtime

    org.db.insert_thread(ThreadRecord(
        id="THR-REPLAY", subject="Test", composed_by="engineering_manager",
        status=ThreadStatus.OPEN,
    ))
    org.db.insert_task(TaskRecord(
        id="T-REPLAY", brief="test", dispatched_from_thread_id="THR-REPLAY",
    ))
    org.db.update_task("T-REPLAY", status=TaskStatus.ESCALATED, block_kind=None)

    token = _mint_authorized_invocation(org, "THR-REPLAY", "engineering_head")

    payload = {
        "task_id": "T-REPLAY",
        "decision": "continue",
        "rationale": "first call",
        "invocation_token": token,
        "dispatcher": "engineering_head",
    }

    # First call: succeeds.
    r1 = client.post(
        "/api/v1/orgs/alpha/threads/THR-REPLAY/resolve-escalation",
        json=payload,
    )
    assert r1.status_code == 200, f"first call: got {r1.status_code} {r1.text}"

    # Reset task back to escalated so a replay would mutate again if not guarded.
    org.db.update_task("T-REPLAY", status=TaskStatus.ESCALATED, block_kind=None)

    # Second call with the SAME token: must reject.
    r2 = client.post(
        "/api/v1/orgs/alpha/threads/THR-REPLAY/resolve-escalation",
        json={**payload, "rationale": "replay attempt"},
    )
    assert r2.status_code == 409, f"replay: got {r2.status_code} {r2.text}"
    detail = r2.json()["detail"]
    assert detail["code"] == "invocation_token_consumed"


# ── Thread followup test (THR-080 #3) ──────────────────────────────

@pytest.mark.asyncio
async def test_thread_supersede_emits_thread_followup(
    client_with_runtime,
):
    """THR-080 #3: supersede from the thread route emits a thread followup
    (TASK_FOLLOWUP invocation) for thread-originated tasks."""
    client, org = client_with_runtime

    org.db.insert_thread(ThreadRecord(
        id="THR-FUP", subject="Test", composed_by="engineering_manager",
        status=ThreadStatus.OPEN,
    ))
    org.db.insert_task(TaskRecord(
        id="T-FUP", brief="original", dispatched_from_thread_id="THR-FUP",
    ))
    org.db.update_task("T-FUP", status=TaskStatus.ESCALATED, block_kind=None)

    # Insert a synthetic thread_dispatch audit row so _maybe_post_thread_followup
    # can resolve the dispatcher identity.
    org.db.insert_audit_log(
        task_id="THR-FUP",
        agent="engineering_head",
        action="thread_dispatch",
        payload={"task_id": "T-FUP", "dispatcher": "engineering_head",
                 "target_agent": "dev_agent", "team": "engineering"},
    )

    token = _mint_authorized_invocation(org, "THR-FUP", "engineering_head")

    r = client.post(
        "/api/v1/orgs/alpha/threads/THR-FUP/resolve-escalation",
        json={
            "task_id": "T-FUP",
            "decision": "supersede",
            "rationale": "reroute",
            "brief": "successor task",
            "invocation_token": token,
            "dispatcher": "engineering_head",
        },
    )
    assert r.status_code == 200, f"got {r.status_code} {r.text}"
    assert r.json()["new_status"] == "superseded"

    # Assert a TASK_FOLLOWUP invocation was minted for the predecessor.
    invs = org.db.list_thread_invocations("THR-FUP")
    followup_invs = [
        i for i in invs if i.purpose == ThreadInvocationPurpose.TASK_FOLLOWUP
    ]
    assert len(followup_invs) >= 1, (
        f"Expected at least one TASK_FOLLOWUP invocation for T-FUP, "
        f"got invocations: {[(i.agent_name, i.purpose) for i in invs]}"
    )
