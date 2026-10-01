"""Read-only projection of the current task escalation episode."""
from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel


class EscalationReason(BaseModel):
    """Founder-facing reason for the current escalation episode."""

    primary: str | None
    refusal_code: str | None
    secondary: str | None


AUTHORITY_V2_REFUSAL_EXPLANATIONS: dict[str, str] = {
    "interrupted_pre_final": (
        "Automatic continuation was interrupted before it finished, so this was escalated to you."
    ),
    "claim_failed": (
        "The authority attempt couldn't claim its decision record, so this was escalated to you."
    ),
    "claim_audit_missing": (
        "The authority attempt's claim audit couldn't be confirmed, so this was escalated to you."
    ),
    "evaluation_failed": (
        "The authority evaluation couldn't be completed, so this was escalated to you."
    ),
    "evaluation_audit_missing": (
        "The authority evaluation audit couldn't be confirmed, so this was escalated to you."
    ),
    "consume_failed": (
        "The authority decision couldn't be consumed, so this was escalated to you."
    ),
    "consume_audit_missing": (
        "The authority decision's consume audit couldn't be confirmed, "
        "so this was escalated to you."
    ),
    "final_commit_failed": (
        "Automatic continuation couldn't be committed, so this was escalated to you."
    ),
    "identity_mismatch": (
        "The authority attempt no longer matched the active task session, "
        "so this was escalated to you."
    ),
    "owner_lost": (
        "The authority attempt lost ownership before it finished, so this was escalated to you."
    ),
    "cancelled": (
        "The task was cancelled before automatic continuation finished, "
        "so this was escalated to you."
    ),
    "decision_dispatch_interrupted": (
        "Automatic decision dispatch was interrupted, so this was escalated to you."
    ),
}


def derive_current_escalation_reason(
    *,
    task_status: str,
    audit_rows: Sequence[dict],
) -> EscalationReason | None:
    """Derive only from the current escalation episode.

    Consecutive ``escalation`` audit ids are the episode boundaries. For a v2
    refusal, only an ``escalate`` orchestration decision strictly after the
    previous escalation and before the current refusal can supply ``primary``.
    This deliberately permits no-primary refusal episodes such as
    ``interrupted_pre_final`` after a delegate/done/fanout callback.
    """
    if task_status != "escalated":
        return None

    rows = sorted(audit_rows, key=lambda row: int(row.get("id", 0)))
    escalations = [row for row in rows if row.get("action") == "escalation"]
    if not escalations:
        return EscalationReason(primary=None, refusal_code=None, secondary=None)

    current = escalations[-1]
    payload = current.get("payload") or {}
    stored_reason = payload.get("reason") or payload.get("question")
    ordinary_reason = stored_reason if isinstance(stored_reason, str) and stored_reason else None
    if ordinary_reason != "authority_v2_refusal":
        return EscalationReason(
            primary=ordinary_reason,
            refusal_code=None,
            secondary=None,
        )

    refusal_code_value = payload.get("refusal_code")
    refusal_code = (
        refusal_code_value
        if isinstance(refusal_code_value, str) and refusal_code_value
        else None
    )
    previous_escalation_id = (
        int(escalations[-2].get("id", 0)) if len(escalations) > 1 else 0
    )
    current_escalation_id = int(current.get("id", 0))
    primary: str | None = None
    for row in reversed(rows):
        row_id = int(row.get("id", 0))
        if not (previous_escalation_id < row_id < current_escalation_id):
            continue
        if row.get("action") != "orchestration_step":
            continue
        decision = (row.get("payload") or {}).get("decision")
        if not isinstance(decision, dict) or decision.get("action") != "escalate":
            continue
        reason = decision.get("reason")
        if isinstance(reason, str) and reason:
            primary = reason
            break

    secondary = (
        AUTHORITY_V2_REFUSAL_EXPLANATIONS.get(refusal_code, refusal_code)
        if refusal_code is not None
        else None
    )
    return EscalationReason(
        primary=primary,
        refusal_code=refusal_code,
        secondary=secondary,
    )
