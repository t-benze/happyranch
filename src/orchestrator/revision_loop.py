from __future__ import annotations

from dataclasses import dataclass

from src.models import ReviewVerdict


@dataclass
class NextAction:
    action: str  # "approved", "rejected", "revise", "escalated"
    target_agent: str | None = None
    feedback: str | None = None


def decide_next_action(
    verdict: ReviewVerdict,
    revision_count: int,
    max_rounds: int,
    feedback: str | None = None,
    target_agent: str | None = None,
) -> NextAction:
    """Decide what happens after a review verdict."""
    if verdict == ReviewVerdict.APPROVE:
        return NextAction(action="approved")

    if verdict == ReviewVerdict.REJECT:
        return NextAction(action="rejected")

    # verdict == REVISE
    if revision_count >= max_rounds:
        return NextAction(
            action="escalated",
            target_agent=target_agent,
            feedback=f"Max revision rounds ({max_rounds}) exceeded. Original feedback: {feedback}",
        )

    return NextAction(
        action="revise",
        target_agent=target_agent,
        feedback=feedback,
    )
