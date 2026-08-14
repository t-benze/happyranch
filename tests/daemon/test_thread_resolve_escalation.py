"""THR-080: thread-reachable resolve-escalation route tests."""
from __future__ import annotations

import pytest

from runtime.models import (
    BlockKind,
    TaskRecord,
    TaskStatus,
    ThreadInvocationPurpose,
    ThreadInvocationStatus,
    ThreadMessageKind,
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


def _autonomous_continue_payload(org, *, thread_id: str, task_id: str, agent: str) -> dict:
    """Seed the causal THR-166 repair/review/reverify evidence seam."""
    org.db.add_thread_participant(thread_id, agent, added_by="founder")
    org.db.update_task(task_id, assigned_agent=agent)
    org.db.insert_audit_log(task_id, "orchestrator", "escalation", {"reason": "revise"})
    seq = org.db.append_thread_message(
        thread_id=thread_id, speaker=agent, kind=ThreadMessageKind.SYSTEM,
        system_payload={"kind_tag": "task_escalated", "task_id": task_id,
                        "root_task_id": task_id},
    )
    child_id = f"{task_id}-REVERIFY"
    org.db.insert_task(TaskRecord(
        id=child_id, brief="bounded reverify", parent_task_id=task_id,
        status=TaskStatus.COMPLETED,
    ))
    inv = org.db.mint_thread_invocation(
        thread_id=thread_id, agent_name=agent, triggering_seq=seq,
        purpose=ThreadInvocationPurpose.TASK_FOLLOWUP,
    )
    return {
        "task_id": task_id, "decision": "continue", "dispatcher": agent,
        "invocation_token": inv.invocation_token,
        "policy_id": "THR-166-genuine-human-blocker", "policy_version": "1",
        "policy_provenance": "founder:THR-166:seq-29",
        "continuation_class": "repair_review_reverify_reevaluate_original_gate",
        "attestation_checks": [
            "no_schema_or_overloaded_column_change", "no_permission_sandbox_or_allow_rule_change",
            "no_auth_credentials_security_privacy_or_data_access_change", "no_spend_or_budget_change",
            "no_destructive_or_irreversible_action", "no_external_contract_or_product_commitment",
            "no_genuine_ambiguity_or_novel_situation", "evidence_terminal_fresh_and_consistent",
            "original_protected_gate_not_authorized",
        ],
        "evidence": [{"task_id": child_id, "terminal_status": "completed"}],
    }


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

    r = client.post(
        "/api/v1/orgs/alpha/threads/THR-1/resolve-escalation",
        json=_autonomous_continue_payload(org, thread_id="THR-1", task_id="T-1", agent="engineering_head"),
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
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "wrong_invocation_purpose"


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

    r = client.post(
        "/api/v1/orgs/alpha/threads/THR-5/resolve-escalation",
        json=_autonomous_continue_payload(org, thread_id="THR-5", task_id="T-5", agent="engineering_head"),
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

    r = client.post(
        "/api/v1/orgs/alpha/threads/THR-6/resolve-escalation",
        json=_autonomous_continue_payload(org, thread_id="THR-6", task_id="T-CHD", agent="engineering_head"),
    )
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "continuation_not_root"


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

    r = client.post(
        "/api/v1/orgs/alpha/threads/THR-AUTH/resolve-escalation",
        json=_autonomous_continue_payload(org, thread_id="THR-AUTH", task_id="T-AUTH", agent="dev_agent"),
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

    # Try to spoof actor as "founder" while presenting engineering_head's token.
    r = client.post(
        "/api/v1/orgs/alpha/threads/THR-ACTOR/resolve-escalation",
        json=_autonomous_continue_payload(org, thread_id="THR-ACTOR", task_id="T-ACTOR", agent="engineering_head"),
    )
    assert r.status_code == 200, f"got {r.status_code} {r.text}"

    # The audit log should show the REAL actor (engineering_head), not
    # any spoofed value.
    logs = org.db.get_audit_logs("T-ACTOR")
    resolved_logs = [e for e in logs if e["action"] == "escalation_continued_autonomously"]
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

    payload = _autonomous_continue_payload(
        org, thread_id="THR-REPLAY", task_id="T-REPLAY", agent="engineering_head",
    )
    token = payload["invocation_token"]

    # First call: succeeds.
    r1 = client.post(
        "/api/v1/orgs/alpha/threads/THR-REPLAY/resolve-escalation",
        json=payload,
    )
    assert r1.status_code == 200, f"first call: got {r1.status_code} {r1.text}"

    # First turn must consume the token (not be classified no_callback).
    inv = org.db.get_invocation_any_status(token)
    assert inv is not None
    assert inv.status == ThreadInvocationStatus.CONSUMED, (
        f"first turn must consume the token (not no_callback), got {inv.status}"
    )
    assert inv.decline_reason is None, (
        f"consumed callback must have no decline_reason, got {inv.decline_reason}"
    )

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


@pytest.mark.asyncio
async def test_autonomous_continue_rejects_manager_prose_and_protected_attestation(
    client_with_runtime,
):
    """A brief/rationale cannot replace the founder policy or waive a fence."""
    client, org = client_with_runtime
    org.db.insert_thread(ThreadRecord(id="THR-POL", subject="Test", status=ThreadStatus.OPEN))
    org.db.insert_task(TaskRecord(id="T-POL", brief="manager says it is safe", dispatched_from_thread_id="THR-POL"))
    org.db.update_task("T-POL", status=TaskStatus.ESCALATED, block_kind=None)
    payload = _autonomous_continue_payload(
        org, thread_id="THR-POL", task_id="T-POL", agent="engineering_head",
    )
    payload["policy_id"] = "manager-brief-authority"
    payload["rationale"] = "this is only a frontend repair"
    r = client.post("/api/v1/orgs/alpha/threads/THR-POL/resolve-escalation", json=payload)
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "policy_or_attestation_mismatch"
    assert org.db.get_task("T-POL").status == TaskStatus.ESCALATED


@pytest.mark.asyncio
async def test_autonomous_continue_uses_only_post_escalation_bounded_lineage(
    client_with_runtime,
):
    """TASK-5000: REVISE repair/review/reverify, never a later unrelated PASS."""
    client, org = client_with_runtime
    org.db.insert_thread(ThreadRecord(id="THR-5000", subject="THR-166", status=ThreadStatus.OPEN))
    org.db.insert_task(TaskRecord(
        id="TASK-5000", brief="original destructive gate", assigned_agent="engineering_head",
        dispatched_from_thread_id="THR-5000", status=TaskStatus.ESCALATED,
    ))
    payload = _autonomous_continue_payload(
        org, thread_id="THR-5000", task_id="TASK-5000", agent="engineering_head",
    )
    helper_child = payload["evidence"][0]["task_id"]
    for task_id, parent_id in (
        ("TASK-5001", "TASK-5000"),
        ("TASK-5001-REPAIR", "TASK-5001"),
        ("TASK-5001-REVIEW", "TASK-5001"),
        ("TASK-5001-REVERIFY", "TASK-5001"),
    ):
        org.db.insert_task(TaskRecord(
            id=task_id, brief="bounded repair evidence", parent_task_id=parent_id,
            status=TaskStatus.COMPLETED,
        ))
    # This late result is neither a descendant nor evidence for the unpark.
    org.db.insert_task(TaskRecord(id="TASK-5025", brief="later pass", status=TaskStatus.COMPLETED))
    payload["evidence"] = [
        {"task_id": task_id, "terminal_status": "completed"}
        for task_id in ("TASK-5001", "TASK-5001-REPAIR", "TASK-5001-REVIEW", "TASK-5001-REVERIFY", helper_child)
    ]
    response = client.post("/api/v1/orgs/alpha/threads/THR-5000/resolve-escalation", json=payload)
    assert response.status_code == 200, response.text
    audit = [row for row in org.db.get_audit_logs("TASK-5000")
             if row["action"] == "escalation_continued_autonomously"]
    assert len(audit) == 1
    assert "TASK-5025" not in {row["task_id"] for row in audit[0]["payload"]["evidence"]}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (lambda payload: payload.update({"evidence": []}), "evidence_lineage_mismatch"),
        (lambda payload: payload["evidence"][0].update({"output_summary": "forged"}), "evidence_result_mismatch"),
        (lambda payload: payload.update({"dispatcher": "other_manager"}), "invocation_token_invalid"),
    ],
)
async def test_autonomous_continue_rejects_unrelated_conflicting_and_wrong_owner_proofs(
    client_with_runtime, mutate, expected,
):
    """Structured evidence and ownership are server-derived and fail closed."""
    client, org = client_with_runtime
    org.db.insert_thread(ThreadRecord(id="THR-FAIL", subject="Test", status=ThreadStatus.OPEN))
    org.db.insert_task(TaskRecord(id="T-FAIL", brief="test", dispatched_from_thread_id="THR-FAIL"))
    org.db.update_task("T-FAIL", status=TaskStatus.ESCALATED, block_kind=None)
    payload = _autonomous_continue_payload(
        org, thread_id="THR-FAIL", task_id="T-FAIL", agent="engineering_head",
    )
    mutate(payload)
    response = client.post("/api/v1/orgs/alpha/threads/THR-FAIL/resolve-escalation", json=payload)
    assert response.status_code in {401, 409}
    assert response.json()["detail"]["code"] == expected
    assert org.db.get_task("T-FAIL").status == TaskStatus.ESCALATED


@pytest.mark.asyncio
async def test_autonomous_continue_rejects_wrong_owner_and_noncausal_followup(
    client_with_runtime,
):
    client, org = client_with_runtime
    org.db.insert_thread(ThreadRecord(id="THR-CAUSE", subject="Test", status=ThreadStatus.OPEN))
    org.db.insert_task(TaskRecord(id="T-CAUSE", brief="test", dispatched_from_thread_id="THR-CAUSE"))
    org.db.update_task("T-CAUSE", status=TaskStatus.ESCALATED, block_kind=None)
    payload = _autonomous_continue_payload(
        org, thread_id="THR-CAUSE", task_id="T-CAUSE", agent="engineering_head",
    )
    org.db.update_task("T-CAUSE", assigned_agent="other_manager")
    wrong_owner = client.post("/api/v1/orgs/alpha/threads/THR-CAUSE/resolve-escalation", json=payload)
    assert wrong_owner.status_code == 409
    assert wrong_owner.json()["detail"]["code"] == "continuation_wrong_owner"

    org.db.update_task("T-CAUSE", assigned_agent="engineering_head")
    unrelated_seq = org.db.append_thread_message(
        thread_id="THR-CAUSE", speaker="system", kind=ThreadMessageKind.SYSTEM,
        system_payload={"kind_tag": "task_escalated", "task_id": "OTHER", "root_task_id": "OTHER"},
    )
    unrelated = org.db.mint_thread_invocation(
        thread_id="THR-CAUSE", agent_name="engineering_head", triggering_seq=unrelated_seq,
        purpose=ThreadInvocationPurpose.TASK_FOLLOWUP,
    )
    payload["invocation_token"] = unrelated.invocation_token
    noncausal = client.post("/api/v1/orgs/alpha/threads/THR-CAUSE/resolve-escalation", json=payload)
    assert noncausal.status_code == 409
    assert noncausal.json()["detail"]["code"] == "continuation_noncausal_followup"
    assert org.db.get_task("T-CAUSE").status == TaskStatus.ESCALATED


@pytest.mark.asyncio
async def test_autonomous_continue_rejects_stale_and_nonterminal_evidence(client_with_runtime):
    client, org = client_with_runtime
    org.db.insert_thread(ThreadRecord(id="THR-EVIDENCE", subject="Test", status=ThreadStatus.OPEN))
    org.db.insert_task(TaskRecord(id="T-EVIDENCE", brief="test", dispatched_from_thread_id="THR-EVIDENCE"))
    org.db.insert_task(TaskRecord(
        id="T-EVIDENCE-OLD", brief="pre-escalation", parent_task_id="T-EVIDENCE",
        status=TaskStatus.COMPLETED,
    ))
    org.db.update_task("T-EVIDENCE", status=TaskStatus.ESCALATED, block_kind=None)
    stale = _autonomous_continue_payload(
        org, thread_id="THR-EVIDENCE", task_id="T-EVIDENCE", agent="engineering_head",
    )
    stale["evidence"].append({"task_id": "T-EVIDENCE-OLD", "terminal_status": "completed"})
    stale_response = client.post("/api/v1/orgs/alpha/threads/THR-EVIDENCE/resolve-escalation", json=stale)
    assert stale_response.status_code == 409
    assert stale_response.json()["detail"]["code"] == "evidence_stale"

    org.db.update_task("T-EVIDENCE-OLD", status=TaskStatus.PENDING)
    nonterminal = client.post("/api/v1/orgs/alpha/threads/THR-EVIDENCE/resolve-escalation", json=stale)
    assert nonterminal.status_code == 409
    assert nonterminal.json()["detail"]["code"] == "cannot_continue_live_children"


@pytest.mark.asyncio
async def test_autonomous_continue_rejects_same_token_duplicate_with_one_audit_and_queue(
    client_with_runtime,
):
    """The route-level replay seam produces one durable continue and delivery."""
    client, org = client_with_runtime
    org.db.insert_thread(ThreadRecord(id="THR-ONCE", subject="Test", status=ThreadStatus.OPEN))
    org.db.insert_task(TaskRecord(id="T-ONCE", brief="test", dispatched_from_thread_id="THR-ONCE"))
    org.db.update_task("T-ONCE", status=TaskStatus.ESCALATED, block_kind=None)
    payload = _autonomous_continue_payload(
        org, thread_id="THR-ONCE", task_id="T-ONCE", agent="engineering_head",
    )
    queue = client.app.state.daemon.queue._queue
    while not queue.empty():
        queue.get_nowait()

    assert client.post("/api/v1/orgs/alpha/threads/THR-ONCE/resolve-escalation", json=payload).status_code == 200
    duplicate = client.post("/api/v1/orgs/alpha/threads/THR-ONCE/resolve-escalation", json=payload)
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"]["code"] == "invocation_token_consumed"
    assert [row[1] for row in list(queue._queue)] == ["T-ONCE"]
    assert len([row for row in org.db.get_audit_logs("T-ONCE")
                if row["action"] == "escalation_continued_autonomously"]) == 1


@pytest.mark.asyncio
async def test_autonomous_continue_cancel_wins_and_late_queue_claim_cannot_resurrect(
    client_with_runtime,
):
    """Cancel before continue fails closed; later cancel blocks stale delivery."""
    client, org = client_with_runtime
    org.db.insert_thread(ThreadRecord(id="THR-CANCEL", subject="Test", status=ThreadStatus.OPEN))
    org.db.insert_task(TaskRecord(id="T-CANCEL-BEFORE", brief="test", dispatched_from_thread_id="THR-CANCEL"))
    org.db.update_task("T-CANCEL-BEFORE", status=TaskStatus.ESCALATED, block_kind=None)
    before = _autonomous_continue_payload(
        org, thread_id="THR-CANCEL", task_id="T-CANCEL-BEFORE", agent="engineering_head",
    )
    assert client.post("/api/v1/orgs/alpha/tasks/T-CANCEL-BEFORE/cancel", json={}).status_code == 200
    rejected = client.post("/api/v1/orgs/alpha/threads/THR-CANCEL/resolve-escalation", json=before)
    assert rejected.status_code == 409
    assert org.db.get_task("T-CANCEL-BEFORE").status == TaskStatus.CANCELLED

    org.db.insert_task(TaskRecord(id="T-CANCEL-AFTER", brief="test", dispatched_from_thread_id="THR-CANCEL"))
    org.db.update_task("T-CANCEL-AFTER", status=TaskStatus.ESCALATED, block_kind=None)
    after = _autonomous_continue_payload(
        org, thread_id="THR-CANCEL", task_id="T-CANCEL-AFTER", agent="engineering_head",
    )
    assert client.post("/api/v1/orgs/alpha/threads/THR-CANCEL/resolve-escalation", json=after).status_code == 200
    assert client.post("/api/v1/orgs/alpha/tasks/T-CANCEL-AFTER/cancel", json={}).status_code == 200
    assert not org.db.try_claim_for_step(
        "T-CANCEL-AFTER", TaskStatus.PENDING, None, new_count=1,
    )
    assert org.db.get_task("T-CANCEL-AFTER").status == TaskStatus.CANCELLED


@pytest.mark.asyncio
@pytest.mark.parametrize("blocked_check", [
    "schema_or_overloaded_column_change",
    "permission_sandbox_or_allow_rule_change",
    "auth_credentials_security_privacy_or_data_access_change",
    "spend_or_budget_change",
    "destructive_or_irreversible_action",
    "external_contract_or_product_commitment",
    "genuine_ambiguity_or_novel_situation",
])
async def test_autonomous_continue_keeps_each_protected_boundary_escalated(
    client_with_runtime, blocked_check,
):
    """No structured attestation can turn a protected boundary into a continue."""
    client, org = client_with_runtime
    task_id = f"T-BLOCK-{blocked_check[:5]}"
    org.db.insert_thread(ThreadRecord(id=task_id, subject="Test", status=ThreadStatus.OPEN))
    org.db.insert_task(TaskRecord(id=task_id, brief="test", dispatched_from_thread_id=task_id))
    org.db.update_task(task_id, status=TaskStatus.ESCALATED, block_kind=None)
    payload = _autonomous_continue_payload(org, thread_id=task_id, task_id=task_id, agent="engineering_head")
    payload["attestation_checks"][0] = blocked_check
    response = client.post(f"/api/v1/orgs/alpha/threads/{task_id}/resolve-escalation", json=payload)
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "policy_or_attestation_mismatch"
    assert org.db.get_task(task_id).status == TaskStatus.ESCALATED


@pytest.mark.asyncio
async def test_autonomous_continue_rejects_exhausted_step_budget(client_with_runtime):
    client, org = client_with_runtime
    org.db.insert_thread(ThreadRecord(id="THR-BUDGET", subject="Test", status=ThreadStatus.OPEN))
    org.db.insert_task(TaskRecord(id="T-BUDGET", brief="test", dispatched_from_thread_id="THR-BUDGET"))
    org.db.update_task(
        "T-BUDGET", status=TaskStatus.ESCALATED, block_kind=None,
        orchestration_step_count=org.orchestrator._settings.max_orchestration_steps,
    )
    payload = _autonomous_continue_payload(
        org, thread_id="THR-BUDGET", task_id="T-BUDGET", agent="engineering_head",
    )
    response = client.post("/api/v1/orgs/alpha/threads/THR-BUDGET/resolve-escalation", json=payload)
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "continuation_budget_exhausted"
    assert org.db.get_task("T-BUDGET").status == TaskStatus.ESCALATED


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
