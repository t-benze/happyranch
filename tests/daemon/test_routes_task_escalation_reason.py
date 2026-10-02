"""Route-level coverage for current-episode escalation reason projection."""
from __future__ import annotations

from datetime import datetime, timezone

from runtime.models import (
    AUTHORITY_POLICY_V2_HOUSEKEEPING_REFUSAL_CODES,
    TaskRecord,
    TaskStatus,
)
from runtime.orchestrator.escalation_reason import AUTHORITY_V2_REFUSAL_EXPLANATIONS


def _task(org, task_id: str, *, status: TaskStatus = TaskStatus.ESCALATED) -> None:
    now = datetime.now(timezone.utc)
    org.db.insert_task(TaskRecord(
        id=task_id,
        status=status,
        assigned_agent="engineering_manager",
        team="engineering",
        brief="brief",
        created_at=now,
        updated_at=now,
    ))


def _step(org, task_id: str, action: str, reason: str | None = None) -> None:
    decision: dict[str, object] = {"action": action, "then": [], "children": []}
    if reason is not None:
        decision["reason"] = reason
    org.db.insert_audit_log(
        task_id, "engineering_manager", "orchestration_step",
        {"step_number": 1, "decision": decision},
    )


def _escalation(org, task_id: str, *, reason: str, code: str | None = None) -> None:
    payload = {"reason": reason}
    if code is not None:
        payload.update({"refusal_code": code, "attempt_id": f"APV2R-{task_id}"})
    org.db.insert_audit_log(task_id, "engineering_manager", "escalation", payload)


def _reason(client, task_id: str) -> dict | None:
    response = client.get(f"/api/v1/orgs/alpha/tasks/{task_id}")
    assert response.status_code == 200, response.text
    return response.json()["escalation_reason"]


def test_v2_refusal_exposes_current_manager_reason_and_secondary(client_with_runtime) -> None:
    client, org = client_with_runtime
    _task(org, "TASK-A")
    _step(org, "TASK-A", "escalate", "Manager's current reason")
    _escalation(org, "TASK-A", reason="authority_v2_refusal", code="final_commit_failed")

    assert _reason(client, "TASK-A") == {
        "primary": "Manager's current reason",
        "refusal_code": "final_commit_failed",
        "secondary": "Automatic continuation couldn't be committed, so this was escalated to you.",
    }


def test_every_closed_refusal_code_has_plain_english_copy() -> None:
    assert set(AUTHORITY_V2_REFUSAL_EXPLANATIONS) == set(
        AUTHORITY_POLICY_V2_HOUSEKEEPING_REFUSAL_CODES
    )


def test_v2_refusal_never_reaches_into_previous_episode(client_with_runtime) -> None:
    client, org = client_with_runtime
    _task(org, "TASK-B")
    _step(org, "TASK-B", "escalate", "R1 must not leak")
    _escalation(org, "TASK-B", reason="R1 must not leak")
    org.db.insert_audit_log("TASK-B", "founder", "escalation_resolved", {"decision": "continue"})
    _step(org, "TASK-B", "escalate", "R2 is current")
    _escalation(org, "TASK-B", reason="authority_v2_refusal", code="identity_mismatch")

    reason = _reason(client, "TASK-B")
    assert reason is not None
    assert reason["primary"] == "R2 is current"
    assert "R1" not in str(reason)


def test_later_ordinary_escalation_has_no_stale_v2_secondary(client_with_runtime) -> None:
    client, org = client_with_runtime
    _task(org, "TASK-C")
    _step(org, "TASK-C", "escalate", "old v2 manager reason")
    _escalation(org, "TASK-C", reason="authority_v2_refusal", code="claim_failed")
    org.db.insert_audit_log("TASK-C", "founder", "escalation_resolved", {"decision": "continue"})
    _step(org, "TASK-C", "escalate", "Ordinary current reason")
    _escalation(org, "TASK-C", reason="Ordinary current reason")

    assert _reason(client, "TASK-C") == {
        "primary": "Ordinary current reason",
        "refusal_code": None,
        "secondary": None,
    }


def test_v2_refusal_without_escalate_step_shows_only_code_sentence(client_with_runtime) -> None:
    client, org = client_with_runtime
    _task(org, "TASK-D")
    _step(org, "TASK-D", "escalate", "older episode")
    _escalation(org, "TASK-D", reason="older episode")
    org.db.insert_audit_log("TASK-D", "founder", "escalation_resolved", {"decision": "continue"})
    _step(org, "TASK-D", "delegate")
    # A scope-prefixed row must never match the exact task id.
    _step(org, "config:TASK-D", "escalate", "wrong scope")
    _escalation(org, "TASK-D", reason="authority_v2_refusal", code="interrupted_pre_final")

    reason = _reason(client, "TASK-D")
    assert reason is not None
    assert reason["primary"] is None
    assert reason["refusal_code"] == "interrupted_pre_final"
    assert reason["secondary"] == (
        "Automatic continuation was interrupted before it finished, so this was escalated to you."
    )


def test_unknown_refusal_code_falls_back_to_raw_code(client_with_runtime) -> None:
    client, org = client_with_runtime
    _task(org, "TASK-E")
    _escalation(org, "TASK-E", reason="authority_v2_refusal", code="future_code")
    assert _reason(client, "TASK-E") == {
        "primary": None,
        "refusal_code": "future_code",
        "secondary": "future_code",
    }


def test_non_escalated_task_is_unaffected(client_with_runtime) -> None:
    client, org = client_with_runtime
    _task(org, "TASK-F", status=TaskStatus.COMPLETED)
    _step(org, "TASK-F", "escalate", "historical reason")
    _escalation(org, "TASK-F", reason="authority_v2_refusal", code="cancelled")
    assert _reason(client, "TASK-F") is None
