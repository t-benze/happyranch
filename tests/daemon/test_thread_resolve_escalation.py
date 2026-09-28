"""Retirement coverage for the thread resolve-escalation surface.

The former THR-166 acceptance/evaluator matrix is replaced with shipping-route
rejection coverage. Retained manual supersede behavior remains covered here.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from runtime.infrastructure.audit_logger import AuditLogger
from runtime.infrastructure.database import Database
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


def _queue_contents(state, org) -> dict[str, object]:
    """Snapshot queue identity, not just cardinality, without consuming it."""
    return {
        "task_queue": list(state.queue._queue._queue),
        "thread_queue": [
            {"org_slug": job.org_slug, "invocation_token": job.invocation_token}
            for job in org.thread_queue._q._queue
        ],
    }


def _durable_rows(db, table: str, where: str = "", values: tuple = ()) -> list[dict]:
    """Use the shipping Database read API for an exact persisted-row snapshot."""
    return [dict(row) for row in db.execute(f"SELECT * FROM {table} {where} ORDER BY 1", values)]


def _rejection_snapshot(org, state, token: str, request_token: str | None = None) -> dict[str, object]:
    """All causal lifecycle state a retired request must leave untouched.

    Each named reader is deliberately separate: ``audit`` and ``intent`` are
    distinct persistence surfaces, and equal queue depth is not queue identity.
    """
    task = org.db.get_task("T-1")
    invocation = org.db.get_invocation_any_status(token)
    return {
        "task": task.model_dump(mode="json"),
        "all_tasks": _durable_rows(org.db, "tasks"),
        "results": org.db.get_task_results("T-1"),
        "children": [org.db.get_task(child_id).model_dump(mode="json")
                     for child_id in org.db.get_children("T-1")],
        "invocation": invocation.model_dump(mode="json") if invocation else None,
        # The request token can differ from the formerly-valid causal token.
        # Keep both readers explicit for stale/wrong-owner/wrong-thread cases.
        "request_invocation": (
            request_invocation.model_dump(mode="json")
            if (request_invocation := org.db.get_invocation_any_status(request_token or token))
            else None
        ),
        "invocations": _durable_rows(org.db, "thread_invocations"),
        "open_notifications": org.db.list_open_notifications_for_task("T-1"),
        "notification_history": _durable_rows(org.db, "escalation_notifications"),
        "task_audit": org.db.get_audit_logs("T-1"),
        "thread_audit": org.db.get_audit_logs("THR-1"),
        # There is no ``task_intents`` table in the shipping schema.  The
        # persisted decision/intent surface is task_results.decision_json;
        # retain it separately from audit rows rather than relabeling audit.
        "result_intents": _durable_rows(org.db, "task_results"),
        "thread_envelope": [message.model_dump(mode="json") for message in org.db.list_thread_messages("THR-1")],
        # The active-envelope reader alone deliberately omits terminal and
        # historical envelope rows.  Keep the raw durable surfaces separate.
        "thread_row": _durable_rows(org.db, "threads", "WHERE id = ?", ("THR-1",)),
        "thread_rows": _durable_rows(org.db, "threads"),
        "thread_messages": _durable_rows(org.db, "thread_messages"),
        "authority_envelope_rows": _durable_rows(org.db, "authority_continue_envelopes"),
        "authority_envelope": org.db.get_active_authority_continue_envelope("T-1"),
        "queues": _queue_contents(state, org),
    }


def _configure_retired_context(org, frozen_token: str, context: str) -> str:
    """Make each retired-request context a real isolated fixture state."""
    if context == "pending":
        assert org.db.get_invocation_any_status(frozen_token).status is ThreadInvocationStatus.PENDING
        return frozen_token
    if context == "consumed":
        assert org.db.consume_invocation(frozen_token)
        assert org.db.get_invocation_any_status(frozen_token).status is ThreadInvocationStatus.CONSUMED
        return frozen_token
    if context == "stale_token":
        assert org.db.get_invocation_any_status("stale-token") is None
        return "stale-token"
    if context == "wrong_token_owner":
        token = org.db.mint_thread_invocation(
            thread_id="THR-1", agent_name="other_manager", triggering_seq=1,
            purpose=ThreadInvocationPurpose.REPLY,
        ).invocation_token
        assert org.db.get_invocation_any_status(token).agent_name == "other_manager"
        return token
    if context == "wrong_token_thread":
        org.db.insert_thread(ThreadRecord(id="THR-OTHER", subject="Other", status=ThreadStatus.OPEN))
        org.db.add_thread_participant("THR-OTHER", "engineering_head", added_by="founder")
        token = org.db.mint_thread_invocation(
            thread_id="THR-OTHER", agent_name="engineering_head", triggering_seq=1,
            purpose=ThreadInvocationPurpose.REPLY,
        ).invocation_token
        assert org.db.get_invocation_any_status(token).thread_id == "THR-OTHER"
        return token
    if context == "missing_causal":
        # The task-escalated message retains a result id whose actual causal
        # task-result row is now absent, rather than adding an unrelated marker.
        org.db.execute("DELETE FROM task_results WHERE task_id = ?", ("T-1",))
        assert org.db.get_task_results("T-1") == []
    elif context == "unrelated_causal":
        org.db.execute("UPDATE task_results SET task_id = ? WHERE task_id = ?", ("T-UNRELATED", "T-1"))
        assert org.db.get_task_results("T-1") == []
    elif context == "malformed_causal":
        org.db.execute("UPDATE task_results SET verdict = NULL WHERE task_id = ?", ("T-1",))
        assert org.db.get_task_results("T-1")[0]["verdict"] is None
    elif context == "root_task":
        assert org.db.get_task("T-1").parent_task_id is None
    elif context == "non_root_task":
        org.db.insert_task(TaskRecord(id="T-PARENT", brief="parent"))
        # parent_task_id is immutable to ordinary lifecycle callers; this is
        # isolated fixture construction of an already-persisted non-root row.
        org.db.execute("UPDATE tasks SET parent_task_id = ? WHERE id = ?", ("T-PARENT", "T-1"))
        assert org.db.get_task("T-1").parent_task_id == "T-PARENT"
    elif context == "cancelled_task":
        org.db.update_task("T-1", status=TaskStatus.CANCELLED)
        assert org.db.get_task("T-1").status is TaskStatus.CANCELLED
    elif context == "live_child":
        org.db.insert_task(TaskRecord(id="T-CHILD", brief="live", parent_task_id="T-1"))
        assert org.db.get_children("T-1") == ["T-CHILD"]
    elif context != "repeated_identical_replay":
        raise AssertionError(f"unmapped context: {context}")
    return frozen_token


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


_RETIRED_FIELDS = (
    "policy_id", "policy_version", "policy_provenance", "continuation_class",
    "attestation_checks", "evidence", "invocation_token", "dispatcher",
)


def _presence_values(field: str) -> list[tuple[str, object]]:
    """Formerly-valid, empty, null, wrong-type, and malformed cells per key."""
    valid: dict[str, object] = {
        "policy_id": "THR-166-genuine-human-blocker", "policy_version": "1",
        "policy_provenance": "founder:THR-166:seq-29",
        "continuation_class": "repair_review_reverify_reevaluate_original_gate",
        # These values are copied from _frozen_formerly_valid_continue.  They
        # are valid *field* values, not a claim that a partial request is a
        # complete formerly-valid THR-166 request.
        "attestation_checks": ["evidence_terminal_fresh_and_consistent"],
        "evidence": [{"task_id": "T-1", "terminal_status": "completed",
                      "verdict": "REQUEST_CHANGES",
                      "output_summary": "review found bounded repair work"}],
        "invocation_token": "formerly-valid-token", "dispatcher": "engineering_head",
    }
    malformed: dict[str, object] = {
        "policy_id": "\x00bad", "policy_version": "not-a-version",
        "policy_provenance": "not:a:provenance", "continuation_class": "???",
        "attestation_checks": [None], "evidence": [{"task_id": None}],
        "invocation_token": "not-a-token", "dispatcher": "not a dispatcher",
    }
    empty: object = [] if field in {"attestation_checks", "evidence"} else ""
    wrong: object = {"wrong": True} if field not in {"attestation_checks", "evidence"} else "wrong"
    return [("formerly_valid", valid[field]), ("empty", empty), ("null", None),
            ("wrong_type", wrong), ("malformed", malformed[field])]


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value_class,value", [
    (field, value_class, value)
    for field in _RETIRED_FIELDS
    for value_class, value in _presence_values(field)
], ids=lambda cell: str(cell))
async def test_retired_presence_matrix_rejects_at_both_imported_resolver_lookups(
    client_with_runtime, monkeypatch, field, value_class, value,
):
    client, org = client_with_runtime
    _seed(org)
    token = _token(org)
    state = client.app.state.daemon
    state.queue.put_nowait("alpha", "SENTINEL-TASK")
    org.thread_queue._q.put_nowait(type("Sentinel", (), {"org_slug": "alpha", "invocation_token": "sentinel"})())
    from runtime.daemon.routes import tasks
    calls: list[object] = []

    async def resolver_spy(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("retired request reached shared resolver")

    # tasks.py is the direct task-route lookup and threads.py imports this
    # exact symbol function-locally immediately before its actual call site.
    monkeypatch.setattr(tasks, "resolve_escalation_in_process", resolver_spy)
    routes = [
        ("/api/v1/orgs/alpha/tasks/T-1/resolve-escalation", {
            "decision": "continue", "rationale": "human", field: value,
        }),
    ]
    # ``invocation_token``/``dispatcher`` are ordinary required transport
    # fields at thread ingress, but retired task identity markers.  The six
    # former policy/evidence keys are rejected at *both* ingress routes.
    if field not in {"invocation_token", "dispatcher"}:
        routes.insert(0, (
            "/api/v1/orgs/alpha/threads/THR-1/resolve-escalation",
            _thread_payload(token, **{field: value}),
        ))
    for route, payload in routes:
        before = _rejection_snapshot(org, state, token)
        response = client.post(route, json=payload)
        assert response.status_code == 410, (field, value_class, route, response.text)
        assert response.json()["detail"] == {"code": "retired_autonomous_continuation"}
        assert _rejection_snapshot(org, state, token) == before
    assert calls == []


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
@pytest.mark.parametrize("actor", [None, "", "founder", "engineering_manager"])
async def test_task_rejects_full_retired_envelope_for_every_actor_before_fallback(
    client_with_runtime, actor,
):
    client, org = client_with_runtime
    _seed(org)
    payload: dict[str, object] = {
        "decision": "continue", "rationale": "human", "policy_id": "THR-166-genuine-human-blocker",
        "policy_version": "1", "policy_provenance": "founder:THR-166:seq-29",
        "continuation_class": "repair_review_reverify_reevaluate_original_gate",
        "attestation_checks": ["evidence_terminal_fresh_and_consistent"],
        "evidence": [{"task_id": "T-1", "terminal_status": "completed"}],
        "invocation_token": "former-token", "dispatcher": "engineering_head",
    }
    if actor is not None:
        payload["actor"] = actor
    before = _rejection_snapshot(org, client.app.state.daemon, "missing-token")
    response = client.post("/api/v1/orgs/alpha/tasks/T-1/resolve-escalation", json=payload)
    assert response.status_code == 410
    assert _rejection_snapshot(org, client.app.state.daemon, "missing-token") == before


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
async def test_retired_rejection_survives_close_reopen_and_preserves_historical_causal_rows(
    client_with_runtime, monkeypatch,
):
    """The real persisted fixture remains readable after a separate DB open."""
    client, org = client_with_runtime
    payload, token = _frozen_formerly_valid_continue(org, monkeypatch)
    before = _rejection_snapshot(org, client.app.state.daemon, token)
    # These are actual historical causal fixture records, not an assertion
    # that the current task alone is a "legacy" proxy.
    assert before["results"] and before["results"][0]["task_id"] == "T-1"
    assert any(row["action"] == "escalation" for row in before["task_audit"])
    assert any(row["action"] == "thread_dispatch" for row in before["thread_audit"])
    assert any(
        (message.get("system_payload") or {}).get("kind_tag") == "task_escalated"
        for message in before["thread_envelope"]
    )
    assert any(row["purpose"] == ThreadInvocationPurpose.TASK_FOLLOWUP.value for row in before["invocations"])
    response = client.post("/api/v1/orgs/alpha/threads/THR-1/resolve-escalation", json=payload)
    assert response.status_code == 410
    db_path = Path(org.db.db_path)
    org.db.close()
    reopened = Database(db_path)
    try:
        reopened_snapshot = _rejection_snapshot(
            type("Org", (), {"db": reopened, "thread_queue": org.thread_queue})(),
            client.app.state.daemon, token,
        )
        # Queues are in-memory; they were compared before close.  Every
        # persisted reader, including historical authority envelopes and the
        # full thread record, remains byte-for-byte readable after reopen.
        assert {key: value for key, value in reopened_snapshot.items() if key != "queues"} == {
            key: value for key, value in before.items() if key != "queues"
        }
    finally:
        reopened.close()


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
@pytest.mark.parametrize(
    "context",
    [
        "pending", "consumed", "stale_token", "wrong_token_owner",
        "wrong_token_thread", "repeated_identical_replay", "missing_causal",
        "unrelated_causal", "malformed_causal", "root_task", "non_root_task",
        "cancelled_task", "live_child",
    ],
)
async def test_retired_contexts_reject_before_both_shared_resolver_lookups(
    client_with_runtime, monkeypatch, context,
):
    """Each formerly-live context is rejected before auth/lineage/fallback.

    The thread request transports an invocation token; task ingress treats
    that same key as retired envelope evidence.  Both therefore prove the
    source ``tasks.resolve_escalation_in_process`` lookup is unreachable.
    """
    client, org = client_with_runtime
    payload, frozen_token = _frozen_formerly_valid_continue(org, monkeypatch)
    token = _configure_retired_context(org, frozen_token, context)
    state = client.app.state.daemon
    state.queue.put_nowait("alpha", f"SENTINEL-{context}")
    before = _rejection_snapshot(org, state, frozen_token, token)
    from runtime.daemon.routes import tasks
    calls: list[object] = []

    async def resolver_spy(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("retired context reached shared resolver")

    monkeypatch.setattr(tasks, "resolve_escalation_in_process", resolver_spy)
    thread_payload = {**payload, "invocation_token": token}
    task_payload = {"decision": "continue", "rationale": "retired", "policy_id": payload["policy_id"],
                    "invocation_token": token, "dispatcher": "engineering_head"}
    requests = (
        ("/api/v1/orgs/alpha/threads/THR-1/resolve-escalation", thread_payload),
        ("/api/v1/orgs/alpha/tasks/T-1/resolve-escalation", task_payload),
    )
    for route, body in requests:
        response = client.post(route, json=body)
        assert response.status_code == 410, (context, route, response.text)
        assert response.json()["detail"] == {"code": "retired_autonomous_continuation"}
        assert _rejection_snapshot(org, state, frozen_token, token) == before
    if context == "repeated_identical_replay":
        for route, body in requests:
            replay = client.post(route, json=body)
            assert replay.status_code == 410, (context, route, replay.text)
            assert _rejection_snapshot(org, state, frozen_token, token) == before
    assert calls == []


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
    assert successor.parent_task_id is None
    assert successor.brief == "successor task"
    resolved = next(row for row in audits if row["action"] == "escalation_resolved")
    assert resolved["agent"] == "engineering_head"
    assert resolved["payload"]["resolution_path"] == "thread_manual_supersede"
    followups = [
        invocation for invocation in org.db.list_thread_invocations("THR-1")
        if invocation.purpose == ThreadInvocationPurpose.TASK_FOLLOWUP
    ]
    assert len(followups) == 1
    assert followups[0].agent_name == "engineering_head"
    assert ("alpha", successor.id, None) in list(client.app.state.daemon.queue._queue._queue)


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
async def test_thread_supersede_requires_its_authorized_invocation_token(client_with_runtime):
    """Retained thread supersede cannot fall back to a caller-declared actor."""
    client, org = client_with_runtime
    _seed(org)
    response = client.post(
        "/api/v1/orgs/alpha/threads/THR-1/resolve-escalation",
        json={
            "task_id": "T-1", "decision": "supersede", "rationale": "reroute",
            "brief": "successor task", "dispatcher": "engineering_head",
        },
    )
    assert response.status_code == 422
    assert response.json()["detail"] == {"code": "missing_invocation_token"}
    assert org.db.get_task("T-1").status is TaskStatus.ESCALATED
    assert not org.db.get_audit_logs("T-1")


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
    assert audit["payload"] == {
        "decision": "continue", "rationale": "founder direction",
        "resolution_path": "manual_break_glass",
    }
    assert ("alpha", "T-MANUAL", None) in list(client.app.state.daemon.queue._queue._queue)
    assert not [
        row for row in org.db.get_audit_logs("T-MANUAL")
        if row["action"] == "escalation_continued_autonomously"
    ]


def test_resolve_escalation_openapi_declares_retired_410(app):
    paths = app.openapi()["paths"]
    task_response = paths["/api/v1/orgs/{slug}/tasks/{task_id}/resolve-escalation"]["post"]["responses"]["410"]
    thread_response = paths["/api/v1/orgs/{slug}/threads/{thread_id}/resolve-escalation"]["post"]["responses"]["410"]
    task_description = task_response["description"]
    thread_description = thread_response["description"]
    legacy_fields = (
        "policy_id", "policy_version", "policy_provenance", "continuation_class",
        "attestation_checks", "evidence",
    )
    for description in (task_description, thread_description):
        assert "retired_autonomous_continuation" in description
        assert all(field in description for field in legacy_fields)
        assert "presence" in description.lower()
    assert "invocation_token" in task_description
    assert "dispatcher" in task_description
    assert "before human actor fallback or resolution" in task_description
    assert "an agent thread continue is retired even without those fields" in thread_description
    # The operation-level rule is independent of the decision value: a
    # supersede request carrying any legacy key is still rejected before the
    # retained resolver. The field matrix above executes that served cell.
    assert "any presence" in thread_description
