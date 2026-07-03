"""Native fan-out (parallel) — state model + pure-logic helpers.

Phase 1: read-only fan-out core. A manager declares N children in one
NextStep.fanout decision; the orchestrator atomically mints all N,
parks the parent in in_progress(delegated) with active_fanout set, and
wakes the manager once when all children are terminal. The manager
receives a bounded structured join context.

This module is pure logic — no DB, no orchestrator, no I/O. Integration
with the orchestrator lives in run_step.py.

See KB fanout-primitive-founder-ratification and
output/TASK-1101/native-fanout-phase1-refresh.md.
"""
from __future__ import annotations

import json
from dataclasses import dataclass


# --- Constants (founder-ratified) ---

MAX_FANOUT_WIDTH = 8   # hard cap, parse-time rejection, no silent truncation
FANOUT_REVIEW_THRESHOLD = 4  # width > threshold → review_required gate


# --- FanoutState (in-flight metadata stored on tasks.active_fanout) ---


@dataclass
class FanoutState:
    """In-flight fan-out metadata stored as JSON on tasks.active_fanout.

    Set atomically with child spawns; cleared on successful join claim
    or terminal parent close. Exists so the manager's wake prompt can
    reconstruct structured join context without re-deriving it from raw
    task records.

    Status transitions:
    - ``"pending_review"`` — fan-out plan stored but children NOT yet
      spawned; parent is parked on BLOCKED_ON_JOB awaiting founder approval
      of the review_required gate job. The CAS-winner on resume skips
      agent re-run and proceeds directly to child spawn.
    - ``"spawned"`` — children have been atomically inserted; parent
      is in_progress(delegated) waiting for all children to become terminal.
    """
    children_ids: list[str]
    children_details: list[dict]  # [{"agent": ..., "prompt": ...}, ...]
    width: int
    manager_agent: str
    join_summary: str | None = None
    status: str = "spawned"       # "spawned" | "pending_review"

    def serialize(self) -> str:
        return json.dumps({
            "children_ids": self.children_ids,
            "children_details": self.children_details,
            "width": self.width,
            "manager_agent": self.manager_agent,
            "join_summary": self.join_summary,
            "status": self.status,
        })

    @classmethod
    def deserialize(cls, payload: str) -> FanoutState:
        data = json.loads(payload)
        return cls(
            children_ids=list(data["children_ids"]),
            children_details=list(data.get("children_details", [])),
            width=data["width"],
            manager_agent=data["manager_agent"],
            join_summary=data.get("join_summary"),
            status=data.get("status", "spawned"),
        )


# --- Join context rendering ---


def build_fanout_join_context(
    *,
    parent_task_id: str,
    fanout: FanoutState,
    child_results: list[_ChildJoinInfo],
) -> str:
    """Render the structured join context block appended to the manager's
    wake prompt after all fan-out children are terminal.

    The block lists each child with its outcome (status, agent, verdict,
    confidence, summary excerpt, output_dir, and failure note where
    relevant). The manager uses this to ground its next decision.
    """
    lines: list[str] = [
        "=== FAN-OUT JOIN CONTEXT (system) ===",
        f"All {fanout.width} fan-out children of {parent_task_id} are terminal.",
        "",
    ]
    for i, child in enumerate(child_results, start=1):
        lines.append(f"  [{i}/{fanout.width}] {child.id} ({child.agent})")
        lines.append(f"       Status: {child.status}")
        if child.verdict:
            lines.append(f"       Verdict: {child.verdict}")
        lines.append(f"       Confidence: {child.confidence}")
        if child.summary_excerpt:
            lines.append(f"       Summary: {child.summary_excerpt}")
        if child.output_dir:
            lines.append(f"       Output dir: {child.output_dir}")
        if child.failure_note:
            lines.append(f"       Failure: {child.failure_note}")
        lines.append("")
    if fanout.join_summary:
        lines.append(f"Manager's join directive: {fanout.join_summary}")
        lines.append("")
    lines.append(
        "Inspect any child via `happyranch details <id>` or "
        "`happyranch audit <id>`. Decide the next step (delegate, "
        "done, escalate, or fanout again) based on these outcomes."
    )
    lines.append("=========================================")
    return "\n".join(lines)


@dataclass
class _ChildJoinInfo:
    """Per-child result collected from the DB for join context rendering."""
    id: str
    agent: str
    status: str
    verdict: str | None
    confidence: int
    summary_excerpt: str | None
    output_dir: str | None
    failure_note: str | None


def collect_child_join_info(
    children: list,
    *,
    child_reports: dict[str, dict | None] | None = None,
) -> list[_ChildJoinInfo]:
    """Collect structured join info from a list of TaskRecord children.

    ``children`` is a list of TaskRecord objects for the fan-out's child
    tasks, fetched from the DB. Returns one _ChildJoinInfo per child in the
    order provided (caller maintains fan-out ordering).

    ``child_reports`` is an optional dict mapping child task_id to its
    latest task_results row (as a dict). When provided, verdict and
    confidence are read from the persisted completion report instead of
    defaulting to None/80. This ensures the manager's join context reflects
    the actual child outcomes.
    """
    reports = child_reports or {}
    result: list[_ChildJoinInfo] = []
    for child in children:
        # Summary excerpt: first 200 chars of note, or "(no summary)".
        note = child.note or ""
        excerpt = note[:200] if note else None
        report = reports.get(child.id)
        verdict = report.get("verdict") if report else None
        confidence = report.get("confidence_score", 80) if report else 80
        result.append(_ChildJoinInfo(
            id=child.id,
            agent=child.assigned_agent or "unknown",
            status=child.status.value if hasattr(child.status, 'value') else str(child.status),
            verdict=verdict,
            confidence=confidence,
            summary_excerpt=excerpt,
            output_dir=child.final_output_dir,
            failure_note=note if child.status.value == "failed" else None,
        ))
    return result


# --- Validation helpers ---


def validate_fanout_decision(
    decision,  # NextStep
) -> str | None:
    """Validate a fanout decision. Returns None on success or an error string.

    Checks:
    - children is non-empty and within MAX_FANOUT_WIDTH
    - width_cap_ack matches actual child count
    - No per-child then/expect_verdict (Phase 1 only)
    """
    n = len(decision.children)
    if n == 0:
        return "fanout requires at least one child"
    if n == 1:
        return "fanout with a single child: use delegate instead"
    if n > MAX_FANOUT_WIDTH:
        return f"fanout width {n} exceeds max {MAX_FANOUT_WIDTH}"
    if decision.width_cap_ack is None:
        return "fanout requires width_cap_ack to match child count"
    if decision.width_cap_ack != n:
        return (
            f"width_cap_ack ({decision.width_cap_ack}) does not match "
            f"actual child count ({n})"
        )
    for i, child in enumerate(decision.children):
        # Per-child then/expect_verdict are now supported for pipeline carriers (Phase 2).
        # Validate each then leg structurally (agent + prompt non-empty).
        for j, leg in enumerate(child.then):
            if not leg.agent:
                return f"fanout child {i + 1}, then leg {j + 1}: missing agent"
            if not leg.prompt:
                return f"fanout child {i + 1}, then leg {j + 1}: missing prompt"
    return None


def fanout_needs_review(width: int) -> bool:
    """Check if a fan-out width exceeds the review threshold."""
    return width > FANOUT_REVIEW_THRESHOLD


def fanout_child_targets(decision) -> list[str]:
    """Return the list of agent targets from a fanout decision's children.

    Used for scope validation (reusing _legs_out_of_scope).
    """
    return [child.agent for child in (decision.children or [])]
