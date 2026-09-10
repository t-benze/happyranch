"""Retirement coverage for the thread resolve-escalation surface.

The former THR-166 acceptance/evaluator matrix is replaced with shipping-route
rejection coverage. Retained manual supersede behavior remains covered here.
"""
from __future__ import annotations

import pytest

from runtime.models import (
    TaskRecord, TaskStatus, ThreadInvocationPurpose, ThreadInvocationStatus,
    ThreadRecord, ThreadStatus,
)


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
    token = _token(org)
    payload = _thread_payload(token)
    first = client.post("/api/v1/orgs/alpha/threads/THR-1/resolve-escalation", json=payload)
    assert first.status_code == 200, first.text
    assert first.json()["new_status"] == "superseded"
    assert org.db.get_task("T-1").status is TaskStatus.SUPERSEDED
    assert org.db.get_invocation_any_status(token).status is ThreadInvocationStatus.CONSUMED
    org.db.update_task("T-1", status=TaskStatus.ESCALATED, block_kind=None)
    replay = client.post("/api/v1/orgs/alpha/threads/THR-1/resolve-escalation", json=payload)
    assert replay.status_code == 409
    assert replay.json()["detail"]["code"] == "invocation_token_consumed"


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
