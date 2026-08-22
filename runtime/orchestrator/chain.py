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
    reason: str | None = None    # "child_blocked" | "verdict_mismatch" | "reviewer_non_approve" | "chain_complete"
    expected: str | None = None
    actual: str | None = None


def _current_leg_agent(chain: ChainState, completed_agent: str | None) -> str | None:
    """The agent identity of the just-terminated leg.

    ``step_index == 0`` is the implicit first leg (``decision.agent``), whose
    agent name is NOT persisted in the chain payload — derive it from the
    completed child's DB ``assigned_agent``.  Later legs carry their agent in
    ``legs[step_index - 1].agent``.
    """
    if chain.step_index == 0:
        return completed_agent
    return chain.legs[chain.step_index - 1].agent


def compute_advance_action(
    *,
    chain: ChainState,
    report: CompletionReport,
    completed_agent: str | None = None,
    reviewer_agents: frozenset[str] = frozenset(),
) -> AdvanceAction:
    """Decide whether to auto-advance to the next leg or wake the manager.

    Caller has already confirmed the child task is in a terminal COMPLETED
    state (failed/cancelled children take a separate cascade path). This
    function only handles the COMPLETED branch.

    ``reviewer_agents`` is the org's configured reviewer identity set (THR-175);
    ``completed_agent`` is the completed child's DB ``assigned_agent`` (used to
    identify the first leg, which is not serialized in the chain payload).
    """
    if report.status == "blocked":
        return AdvanceAction(kind="wake", reason="child_blocked")

    expected = chain.current_expect_verdict()
    if expected is not None and report.verdict != expected:
        return AdvanceAction(
            kind="wake", reason="verdict_mismatch",
            expected=expected, actual=report.verdict,
        )

    current_agent = _current_leg_agent(chain, completed_agent)
    has_downstream = chain.step_index + 1 <= len(chain.legs)

    # THR-175 reviewer fail-closed: a configured reviewer leg with a downstream
    # leg advances ONLY on an explicit APPROVE verdict.  Omitted expectation,
    # missing verdict, or any non-approve verdict (REQUEST_CHANGES / REVISE /
    # BLOCK / equivalent) clears the chain and wakes the parent — QA/downstream
    # is never spawned.  Ordinary verdict-less non-review legs are unaffected.
    if has_downstream and current_agent is not None and current_agent in reviewer_agents:
        if report.verdict != "APPROVE":
            return AdvanceAction(
                kind="wake", reason="reviewer_non_approve",
                expected="APPROVE", actual=report.verdict,
            )

    next_index = chain.step_index + 1
    # Total legs = 1 (first leg) + len(chain.legs). Next-leg index space is
    # 1..len(chain.legs); next_index > len(chain.legs) means no more legs.
    if next_index > len(chain.legs):
        return AdvanceAction(kind="wake", reason="chain_complete")

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
