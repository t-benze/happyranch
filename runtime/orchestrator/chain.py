"""Inline delegation chain — state model + pure-logic helpers.

A chain is a manager-authored multi-leg workflow declared in one `delegate`
decision (NextStep.then). The orchestrator auto-advances routine happy-path
legs on verdict match without consuming the manager's 50-step cap. See
docs/superpowers/specs/2026-05-30-inline-delegation-chain-design.md.

This module is pure logic — no DB, no orchestrator, no I/O. Integration with
the orchestrator lives in src/orchestrator/run_step.py.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Literal

from runtime.models import ChainLeg, CompletionReport

# Agent name whose chain leg acts as a review GATE by role. A code_reviewer
# leg that has a downstream leg must declare an explicit ``expect_verdict`` so
# the orchestrator can distinguish APPROVE (advance) from REQUEST_CHANGES
# (wake). Omitting it is fail-closed at both authoring (validation) and
# execution (compute_advance_action).
REVIEWER_AGENT = "code_reviewer"


def reviewer_downstream_omission(
    *, agent: str | None, expect_verdict: str | None, has_downstream: bool,
) -> bool:
    """True when a review-gate leg is unsafe to auto-advance.

    A ``code_reviewer`` leg that has a downstream leg must declare an explicit
    ``expect_verdict``. Without it the orchestrator cannot tell an APPROVE
    (advance) from a REQUEST_CHANGES (wake), so the omission must be
    fail-closed: reject at authoring, wake/clear rather than advance at
    execution. Ordinary non-review legs (and a reviewer FINAL leg, which has
    no downstream leg to wrongly advance) are unaffected.
    """
    return bool(agent == REVIEWER_AGENT and has_downstream and expect_verdict is None)


@dataclass
class ChainState:
    """In-flight chain stored as JSON on tasks.active_chain.

    step_index = 0 when the first leg (the implicit decision.agent+prompt) is
    in flight; 1..N when a subsequent leg (from `legs`) is in flight.
    """
    step_index: int
    first_leg_expect_verdict: str | None
    legs: list[ChainLeg]
    step_audit_id: int

    def serialize(self) -> str:
        return json.dumps({
            "step_index": self.step_index,
            "first_leg_expect_verdict": self.first_leg_expect_verdict,
            "legs": [leg.model_dump() for leg in self.legs],
            "step_audit_id": self.step_audit_id,
        })

    @classmethod
    def deserialize(cls, payload: str) -> ChainState:
        data = json.loads(payload)
        return cls(
            step_index=data["step_index"],
            first_leg_expect_verdict=data.get("first_leg_expect_verdict"),
            legs=[ChainLeg(**leg) for leg in data.get("legs", [])],
            step_audit_id=data["step_audit_id"],
        )

    def current_expect_verdict(self) -> str | None:
        """Expected verdict for the just-terminated child (the one at step_index)."""
        if self.step_index == 0:
            return self.first_leg_expect_verdict
        # step_index=1..N corresponds to legs[0..N-1].
        return self.legs[self.step_index - 1].expect_verdict

    def current_leg_agent(self) -> str | None:
        """Agent name of the just-terminated leg, or None for the first leg.

        The first leg's agent is not persisted in the chain payload (only its
        expect_verdict is, as ``first_leg_expect_verdict``), so a first-leg
        reviewer is only rejected at authoring-time validation, never here.
        """
        if self.step_index == 0:
            return None
        return self.legs[self.step_index - 1].agent


@dataclass
class AdvanceAction:
    """Outcome of compute_advance_action: either advance to the next leg or
    wake the manager (with a reason).
    """
    kind: Literal["advance", "wake"]
    # advance fields:
    next_leg: ChainLeg | None = None
    next_step_index: int | None = None
    # wake fields:
    reason: str | None = None    # "child_blocked" | "verdict_mismatch" | "reviewer_expectation_omitted" | "chain_complete"
    expected: str | None = None
    actual: str | None = None


def compute_advance_action(*, chain: ChainState, report: CompletionReport) -> AdvanceAction:
    """Decide whether to auto-advance to the next leg or wake the manager.

    Caller has already confirmed the child task is in a terminal COMPLETED
    state (failed/cancelled children take a separate cascade path). This
    function only handles the COMPLETED branch.
    """
    if report.status == "blocked":
        return AdvanceAction(kind="wake", reason="child_blocked")

    expected = chain.current_expect_verdict()
    if expected is not None and report.verdict != expected:
        return AdvanceAction(
            kind="wake", reason="verdict_mismatch",
            expected=expected, actual=report.verdict,
        )

    next_index = chain.step_index + 1
    # Total legs = 1 (first leg) + len(chain.legs). Next-leg index space is
    # 1..len(chain.legs); next_index > len(chain.legs) means no more legs.
    if next_index > len(chain.legs):
        return AdvanceAction(kind="wake", reason="chain_complete")

    # Fail-closed: a code_reviewer leg with a downstream leg and no explicit
    # expect_verdict must not auto-advance — the orchestrator cannot tell an
    # APPROVE from a REQUEST_CHANGES without a gate. Wake/clear instead.
    # Ordinary non-review legs (and the reviewer FINAL leg, which already
    # reached chain_complete above) are unaffected.
    if reviewer_downstream_omission(
        agent=chain.current_leg_agent(),
        expect_verdict=expected,
        has_downstream=True,
    ):
        return AdvanceAction(kind="wake", reason="reviewer_expectation_omitted")

    next_leg = chain.legs[next_index - 1]
    return AdvanceAction(
        kind="advance", next_leg=next_leg, next_step_index=next_index,
    )


def build_prior_leg_context(*, child_task_id: str, report: CompletionReport) -> str:
    """Render the orchestrator-appended Prior Leg Context block.

    Suffixed (not prepended) to every non-first leg's brief so the manager's
    authored brief remains the primary instruction surface.
    """
    verdict_line = f"Verdict:      {report.verdict}" if report.verdict else "Verdict:      -"
    lines = [
        "",
        "---",
        "## Prior leg context (auto-generated by orchestrator)",
        "",
        f"Prior leg:    {child_task_id}  (agent: {report.agent})",
        f"Status:       {report.status}",
        verdict_line,
        f"Confidence:   {report.confidence}",
        "Summary:",
    ]
    # Indent multi-line summary by two spaces for readability.
    for line in report.output_summary.splitlines() or [""]:
        lines.append(f"  {line}")
    if report.output_dir:
        lines.append("")
        lines.append(f"Output dir: {report.output_dir}")
    lines.append("---")
    return "\n".join(lines)
