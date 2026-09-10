"""Retirement coverage for the thread resolve-escalation surface.

The former THR-166 acceptance/evaluator matrix is replaced with shipping-route
rejection coverage. Retained manual supersede behavior remains covered here.
"""
from __future__ import annotations

import json

import pytest

from runtime.infrastructure.audit_logger import AuditLogger
from runtime.models import (
    CompletionReport, NextStep, TaskRecord, TaskStatus, ThreadInvocationPurpose,
    ThreadInvocationStatus, ThreadMessageKind, ThreadRecord, ThreadStatus,
)
from runtime.orchestrator.executors import ExecutorResult


def _seed(org) -> None:
    org.db.insert_thread(ThreadRecord(id="THR-1", subject="Test", status=ThreadStatus.OPEN))
    org.db.insert_task(TaskRecord(id="T-1", brief="test", dispatched_from_thread_id="THR-1"))
    org.db.update_task("T-1", status=TaskStatus.ESCALATED, block_kind=None)


def _token(org) -> str:
    org.db.add_thread_participant("THR-1", "engineering_head", added_by="founder")
    return org.db.mint_thread_invocation(
        thread_id="THR-1", agent_name="engineering_head", triggering_seq=0,
        purpose=ThreadInvocationPurpose.REPLY,
    ).invocation_token


def _thread_payload(token: str, **extra: object) -> dict[str, object]:
    return {
        "task_id": "T-1", "decision": "supersede", "rationale": "reroute",
        "brief": "successor task", "dispatcher": "engineering_head",
        "invocation_token": token, **extra,
    }


def _frozen_formerly_valid_continue(org, monkeypatch) -> tuple[dict[str, object], str]:
    """Build the pre-retirement causal lifecycle without removed route symbols.

    These literals are the former served THR-166 request contract, deliberately
    frozen here so a deletion cannot make this regression test vacuous.
    """
    agent = "engineering_head"
    org.db.insert_thread(ThreadRecord(
        id="THR-1", subject="Test", composed_by="engineering_manager", status=ThreadStatus.OPEN,
    ))
    org.db.insert_task(TaskRecord(
        id="T-1", brief="original protected gate", assigned_agent=agent,
        dispatched_from_thread_id="THR-1",
    ))
    org.db.add_thread_participant("THR-1", agent, added_by="founder")
    AuditLogger(org.db).log_thread_dispatch(
        "THR-1", task_id="T-1", dispatcher=agent, target_agent=agent,
        team="engineering",
    )
    report = CompletionReport(
        task_id="T-1", agent=agent, status="completed", confidence=90,
        verdict="REQUEST_CHANGES", output_summary="review found bounded repair work",
        decision=NextStep(action="escalate", reason="review requires founder decision"),
    )
    org.db.insert_task_result(
        task_id="T-1", agent=agent, session_id="sess-lifecycle",
        status="completed", confidence_score=90, verdict="REQUEST_CHANGES",
        output_summary=report.output_summary,
    )
    monkeypatch.setattr(
        org.orchestrator, "_run_agent",
        lambda *args, **kwargs: (
            ExecutorResult(success=True, session_id="sess-lifecycle", duration_seconds=0), report,
        ),
    )
    org.orchestrator.run_step("T-1")
    causal_message = next(
        message for message in org.db.list_thread_messages("THR-1")
        if (message.system_payload or {}).get("kind_tag") == "task_escalated"
    )
    invocation = next(
        item for item in org.db.list_thread_invocations("THR-1")
        if item.purpose == ThreadInvocationPurpose.TASK_FOLLOWUP
    )
    result = org.db.get_task_results("T-1")[0]
    assert causal_message.system_payload == {
        "kind_tag": "task_escalated", "task_id": "T-1", "original_task_id": "T-1",
        "root_task_id": "T-1", "status": "escalated",
        "reason": "review requires founder decision", "revisit_chain_length": 1,
        "causal_terminal_result": {
            "task_id": "T-1", "result_id": result["id"], "terminal_status": "completed",
            "verdict": "REQUEST_CHANGES", "output_summary": report.output_summary,
            "created_at": result["created_at"],
        },
        "causal_escalation_audit_id": next(
            row["id"] for row in org.db.get_audit_logs("T-1") if row["action"] == "escalation"
        ),
    }
    return ({"task_id": "T-1", "decision": "continue", "dispatcher": agent,
             "invocation_token": invocation.invocation_token, "policy_id": "THR-166-genuine-human-blocker",
             "policy_version": "1", "policy_provenance": "founder:THR-166:seq-29",
             "continuation_class": "repair_review_reverify_reevaluate_original_gate",
             "attestation_checks": [
                 "no_schema_or_overloaded_column_change", "no_permission_sandbox_or_allow_rule_change",
                 "no_auth_credentials_security_privacy_or_data_access_change", "no_spend_or_budget_change",
                 "no_destructive_or_irreversible_action", "no_external_contract_or_product_commitment",
                 "no_genuine_ambiguity_or_novel_situation", "evidence_terminal_fresh_and_consistent",
                 "original_protected_gate_not_authorized",
             ],
             "evidence": [{"task_id": "T-1", "terminal_status": "completed",
                           "verdict": "REQUEST_CHANGES", "output_summary": report.output_summary}]},
            invocation.invocation_token)


def _rejection_snapshot(org, state, token: str) -> dict[str, object]:
    """All causal lifecycle state a retired request must leave untouched."""
    task = org.db.get_task("T-1")
    invocation = org.db.get_invocation_any_status(token)
    return {
        "task": task.model_dump(mode="json"),
        "results": org.db.get_task_results("T-1"),
        "children": [child.model_dump(mode="json") for child in org.db.get_children("T-1")],
        "invocation": invocation.model_dump(mode="json") if invocation else None,
        "notifications": org.db.list_open_notifications_for_task("T-1"),
        "audit_and_intent": org.db.get_audit_logs("T-1"),
        "thread_envelope": [message.model_dump(mode="json") for message in org.db.list_thread_messages("THR-1")],
        "authority_envelope": org.db.get_active_authority_continue_envelope("T-1"),
        "queue_depth": state.queue._queue.qsize(),
    }


def _causal_transition_snapshot(org, state, token: str) -> dict[str, object]:
    """Compact diagnostic emitted when the immutable baseline remains live."""
    task = org.db.get_task("T-1")
    invocation = org.db.get_invocation_any_status(token)
    return {
        "fixture_db_path": str(org.db.db_path),
        "task_status": task.status.value if task else None,
        "task_note": task.note if task else None,
        "invocation_status": invocation.status.value if invocation else None,
        "escalation_resolved_audits": [
            row for row in org.db.get_audit_logs("T-1")
            if row["action"] == "escalation_resolved"
        ],
        "queue_depth": state.queue._queue.qsize(),
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [
    ("policy_id", "THR-166-genuine-human-blocker"), ("policy_version", ""),
    ("policy_provenance", None), ("continuation_class", {"bad": True}),
    ("attestation_checks", []), ("evidence", {"bad": "nested"}),
])
async def test_thread_rejects_retired_marker_by_presence(client_with_runtime, field, value):
    client, org = client_with_runtime
    _seed(org)
    token = _token(org)
    response = client.post(
        "/api/v1/orgs/alpha/threads/THR-1/resolve-escalation",
        json=_thread_payload(token, **{field: value}),
    )
    assert response.status_code == 410
    assert response.json()["detail"] == {"code": "retired_autonomous_continuation"}
    assert org.db.get_task("T-1").status is TaskStatus.ESCALATED
    assert org.db.get_invocation_any_status(token).status is ThreadInvocationStatus.PENDING
    assert org.db.get_children("T-1") == []


@pytest.mark.asyncio
async def test_thread_rejects_full_legacy_envelope_without_reflection(client_with_runtime):
    client, org = client_with_runtime
    _seed(org)
    response = client.post(
        "/api/v1/orgs/alpha/threads/THR-1/resolve-escalation",
        json=_thread_payload(_token(org), policy_id="secret-policy", policy_version=None,
                             policy_provenance="secret-source", continuation_class="continue",
                             attestation_checks=["secret-check"], evidence=[{"secret": "payload"}]),
    )
    assert response.status_code == 410
    assert "secret" not in response.text
    assert org.db.get_task("T-1").status is TaskStatus.ESCALATED


@pytest.mark.asyncio
async def test_thread_rejects_frozen_formerly_valid_causal_lifecycle_before_shared_resolver(
    client_with_runtime, monkeypatch,
):
    """The exact former causal continuation is now terminally retired.

    The same test is RED on 5258bcad: that head continues this frozen request.
    """
    client, org = client_with_runtime
    payload, token = _frozen_formerly_valid_continue(org, monkeypatch)
    before = _rejection_snapshot(org, client.app.state.daemon, token)
    from runtime.daemon.routes import tasks

    async def must_not_enter(*args, **kwargs):
        raise AssertionError("retired envelope entered shared human resolver")

    monkeypatch.setattr(tasks, "resolve_escalation_in_process", must_not_enter)
    response = client.post("/api/v1/orgs/alpha/threads/THR-1/resolve-escalation", json=payload)
    assert response.status_code == 410, json.dumps({
        "response": {"status": response.status_code, "body": response.json()},
        "causal_transition": _causal_transition_snapshot(
            org, client.app.state.daemon, token,
        ),
    }, sort_keys=True)
    assert response.json()["detail"] == {"code": "retired_autonomous_continuation"}
    assert _rejection_snapshot(org, client.app.state.daemon, token) == before


@pytest.mark.asyncio
async def test_thread_agent_continue_is_retired_without_legacy_fields(client_with_runtime):
    client, org = client_with_runtime
    _seed(org)
    token = _token(org)
    response = client.post(
        "/api/v1/orgs/alpha/threads/THR-1/resolve-escalation",
        json={**_thread_payload(token), "decision": "continue"},
    )
    assert response.status_code == 410
    assert response.json()["detail"]["code"] == "retired_autonomous_continuation"
    assert org.db.get_task("T-1").status is TaskStatus.ESCALATED
    assert org.db.get_invocation_any_status(token).status is ThreadInvocationStatus.PENDING


@pytest.mark.asyncio
async def test_thread_supersede_remains_supported_and_replay_is_rejected(client_with_runtime):
    client, org = client_with_runtime
    _seed(org)
    # The retained followup seam resolves the dispatcher from its durable
    # thread-dispatch audit provenance, as shipping thread dispatch does.
    org.db.insert_audit_log(
        task_id="THR-1", agent="engineering_head", action="thread_dispatch",
        payload={"task_id": "T-1", "dispatcher": "engineering_head",
                 "target_agent": "dev_agent", "team": "engineering"},
    )
    token = _token(org)
    payload = _thread_payload(token)
    payload["actor"] = "founder"  # Untrusted extra: the validated dispatcher remains the actor.
    first = client.post("/api/v1/orgs/alpha/threads/THR-1/resolve-escalation", json=payload)
    assert first.status_code == 200, first.text
    assert first.json()["new_status"] == "superseded"
    assert org.db.get_task("T-1").status is TaskStatus.SUPERSEDED
    assert org.db.get_invocation_any_status(token).status is ThreadInvocationStatus.CONSUMED
    org.db.update_task("T-1", status=TaskStatus.ESCALATED, block_kind=None)
    replay = client.post("/api/v1/orgs/alpha/threads/THR-1/resolve-escalation", json=payload)
    assert replay.status_code == 409
    assert replay.json()["detail"]["code"] == "invocation_token_consumed"
    audits = org.db.get_audit_logs("T-1")
    superseded = next(row for row in audits if row["action"] == "escalation_superseded")
    successor = org.db.get_task(superseded["payload"]["successor_root"])
    assert successor is not None
    assert successor.dispatched_from_thread_id == "THR-1"
    resolved = next(row for row in audits if row["action"] == "escalation_resolved")
    assert resolved["agent"] == "engineering_head"
    assert resolved["payload"]["resolution_path"] == "thread_manual_supersede"
    assert any(
        invocation.purpose == ThreadInvocationPurpose.TASK_FOLLOWUP
        for invocation in org.db.list_thread_invocations("THR-1")
    )


@pytest.mark.asyncio
async def test_thread_supersede_rejects_non_manager_and_wrong_lineage(client_with_runtime):
    """Retained thread authorization and lineage fences remain independent."""
    client, org = client_with_runtime
    _seed(org)
    org.db.add_thread_participant("THR-1", "dev_agent", added_by="founder")
    worker_token = org.db.mint_thread_invocation(
        thread_id="THR-1", agent_name="dev_agent", triggering_seq=0,
        purpose=ThreadInvocationPurpose.REPLY,
    ).invocation_token
    worker = client.post(
        "/api/v1/orgs/alpha/threads/THR-1/resolve-escalation",
        json={**_thread_payload(worker_token), "dispatcher": "dev_agent"},
    )
    assert worker.status_code == 403
    assert worker.json()["detail"]["code"] == "resolve_escalation_not_authorized"

    org.db.insert_task(TaskRecord(id="T-OTHER", brief="other", dispatched_from_thread_id="THR-OTHER"))
    org.db.update_task("T-OTHER", status=TaskStatus.ESCALATED, block_kind=None)
    response = client.post(
        "/api/v1/orgs/alpha/threads/THR-1/resolve-escalation",
        json={**_thread_payload(_token(org)), "task_id": "T-OTHER"},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "task_not_in_thread_lineage"


@pytest.mark.asyncio
async def test_thread_invalid_decision_preserves_pending_invocation(client_with_runtime):
    client, org = client_with_runtime
    _seed(org)
    token = _token(org)
    response = client.post(
        "/api/v1/orgs/alpha/threads/THR-1/resolve-escalation",
        json={**_thread_payload(token), "decision": "cancel"},
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "invalid_decision"
    assert org.db.get_invocation_any_status(token).status is ThreadInvocationStatus.PENDING


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [
    ("policy_id", "old"), ("policy_version", ""), ("policy_provenance", None),
    ("continuation_class", []), ("attestation_checks", {}), ("evidence", [{"bad": True}]),
    ("invocation_token", "old-token"), ("dispatcher", "engineering_head"),
])
@pytest.mark.parametrize("actor", [None, "", "founder", "engineering_manager"])
async def test_task_ingress_rejects_retired_markers_before_human_fallback(
    client_with_runtime, field, value, actor,
):
    client, org = client_with_runtime
    org.db.insert_task(TaskRecord(id="T-MANUAL", brief="test"))
    org.db.update_task("T-MANUAL", status=TaskStatus.ESCALATED, block_kind=None)
    payload = {"decision": "continue", "rationale": "human", field: value}
    if actor is not None:
        payload["actor"] = actor
    response = client.post("/api/v1/orgs/alpha/tasks/T-MANUAL/resolve-escalation", json=payload)
    assert response.status_code == 410
    assert response.json()["detail"] == {"code": "retired_autonomous_continuation"}
    assert org.db.get_task("T-MANUAL").status is TaskStatus.ESCALATED
    assert not org.db.get_audit_logs("T-MANUAL")


@pytest.mark.asyncio
async def test_plain_task_human_continue_remains_supported(client_with_runtime):
    client, org = client_with_runtime
    org.db.insert_task(TaskRecord(id="T-MANUAL", brief="test"))
    org.db.update_task("T-MANUAL", status=TaskStatus.ESCALATED, block_kind=None)
    response = client.post(
        "/api/v1/orgs/alpha/tasks/T-MANUAL/resolve-escalation",
        json={"decision": "continue", "rationale": "founder direction"},
    )
    assert response.status_code == 200
    assert org.db.get_task("T-MANUAL").status is TaskStatus.PENDING
    audit = org.db.get_audit_logs("T-MANUAL")[-1]
    assert audit["action"] == "escalation_resolved"
    assert audit["agent"] == "founder"


def test_resolve_escalation_openapi_declares_retired_410(app):
    paths = app.openapi()["paths"]
    task_response = paths["/api/v1/orgs/{slug}/tasks/{task_id}/resolve-escalation"]["post"]["responses"]["410"]
    thread_response = paths["/api/v1/orgs/{slug}/threads/{thread_id}/resolve-escalation"]["post"]["responses"]["410"]
    assert "policy_id" in task_response["description"]
    assert "invocation_token" in task_response["description"]
    assert "agent thread continue is retired even without those fields" in thread_response["description"]
