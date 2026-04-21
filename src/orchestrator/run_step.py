"""Implementation of Orchestrator.run_step — the single primitive that advances
a task one subprocess call at a time. Separate from orchestrator.py so the
algorithm has its own test surface.

Entry contract: task MUST be either
  (a) status=pending, or
  (b) status=blocked AND block_kind=DELEGATED AND all children are terminal.
Any other state = stale enqueue, silent no-op.

Exit contract: task ends in exactly one of {in_progress-then-crashed,
completed, failed, blocked(DELEGATED), blocked(ESCALATED)}.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from src.models import BlockKind, TaskStatus

if TYPE_CHECKING:
    from src.orchestrator.orchestrator import Orchestrator

logger = logging.getLogger(__name__)

TERMINAL_STATES = frozenset({TaskStatus.COMPLETED, TaskStatus.FAILED})


def run_step_impl(orch: "Orchestrator", task_id: str) -> None:
    db = orch._db
    task = db.get_task(task_id)
    if task is None:
        return

    # Cancellation short-circuit. Once /cancel marks a task FAILED + sets
    # cancelled_at, any late queue event (e.g., the parent auto-resume after
    # the SIGTERM'd child's audit arrives) must be a no-op. Checking status
    # alone isn't enough — blocked(DELEGATED) parents get cancelled too, and
    # the terminal-state test runs in step 1 below. Re-check before returning
    # so we don't enter the in_progress transition.
    if task.cancelled_at is not None:
        logger.debug("run_step %s: cancelled, skipping", task_id)
        return

    # ---- 1. Verify entry state ----
    if task.status == TaskStatus.PENDING:
        pass  # eligible
    elif task.status == TaskStatus.BLOCKED and task.block_kind == BlockKind.DELEGATED:
        children = [db.get_task(cid) for cid in db.get_children(task_id)]
        if any(c is None or c.status not in TERMINAL_STATES for c in children):
            logger.debug("run_step %s: child still running, skipping", task_id)
            return
    else:
        logger.debug(
            "run_step %s: not eligible (status=%s, block_kind=%s)",
            task_id, task.status, task.block_kind,
        )
        return

    # ---- 2. Budget guard (persisted, survives restarts) ----
    max_steps = orch._settings.max_orchestration_steps
    next_count = task.orchestration_step_count + 1
    if next_count > max_steps:
        reason = f"max steps ({max_steps}) exceeded"
        db.update_task(
            task_id,
            status=TaskStatus.BLOCKED,
            block_kind=BlockKind.ESCALATED,
            note=reason,
        )
        orch._audit.log_escalation(task_id, "orchestrator", reason)
        return

    # ---- 3. Atomic transition: unblock + increment + mark in_progress ----
    db.update_task(
        task_id,
        status=TaskStatus.IN_PROGRESS,
        block_kind=None,
        note=None,
        orchestration_step_count=next_count,
    )

    # ---- 4. Run the agent subprocess ----
    agent = task.assigned_agent or _default_agent_for_root(task)
    if task.assigned_agent is None:
        db.update_task(task_id, assigned_agent=agent)

    prompt = _build_agent_prompt(orch, task, agent)
    try:
        result, report = orch._run_agent(task_id, agent, prompt)
    except Exception as exc:
        _fail(orch, task_id, note=f"agent invocation failed: {exc}")
        _enqueue_parent_if_waiting(orch, task_id)
        return

    # ---- 5. Classify outcome ----
    if not result.success or report is None:
        _fail(orch, task_id, note=_session_failed_note(result, report))
        _enqueue_parent_if_waiting(orch, task_id)
        return

    orch._log_step_result(task_id, result, report)

    if report.status == "blocked":
        _fail(orch, task_id, note=f"self-blocked: {report.output_summary}")
        _enqueue_parent_if_waiting(orch, task_id)
        return

    # ---- 6. Parse next step ----
    # Only the Engineering Head speaks the NextStep JSON protocol. Worker
    # completions are plain prose/summary payloads — treating them as EH
    # decisions reclassifies every non-JSON output_summary as `escalate`
    # (see P1 in 2026-04-20 review).
    if agent == "engineering_head":
        decision = orch._parse_next_step(report)
        orch._audit.log_orchestration_step(
            task_id, next_count, decision.model_dump(exclude_none=True),
        )
    else:
        from src.models import NextStep
        decision = NextStep(action="done", summary=report.output_summary)

    # ---- 7. Dispatch on action ----
    if decision.action == "done":
        _complete(
            orch, task_id,
            note=decision.summary or report.output_summary,
            artifact_dir=report.artifact_dir,
        )
        _enqueue_parent_if_waiting(orch, task_id)
        return

    if decision.action == "escalate":
        reason = decision.reason or "Escalated"
        db.update_task(
            task_id,
            status=TaskStatus.BLOCKED,
            block_kind=BlockKind.ESCALATED,
            note=reason,
        )
        orch._audit.log_escalation(task_id, agent, reason)
        # parent stays blocked(DELEGATED) until this task reaches a terminal.
        return

    if decision.action == "delegate":
        err = _validate_delegate(orch, decision)
        if err is not None:
            _fail(orch, task_id, note=f"invalid delegate: {err}")
            _enqueue_parent_if_waiting(orch, task_id)
            return
        from src.models import TaskRecord
        child_id = db.next_task_id()
        db.insert_task(TaskRecord(
            id=child_id,
            type=task.type,
            brief=decision.prompt or "",
            assigned_agent=decision.agent,
            parent_task_id=task_id,
            status=TaskStatus.PENDING,
        ))
        db.update_task(
            task_id,
            status=TaskStatus.BLOCKED,
            block_kind=BlockKind.DELEGATED,
            note=f"Delegated to {decision.agent} (child={child_id})",
        )
        if orch._queue is not None:
            orch._queue.put_nowait(child_id)
        return

    # ---- 8. Unknown action ----
    _fail(orch, task_id, note=f"unknown action: {decision.action}")
    _enqueue_parent_if_waiting(orch, task_id)


def _validate_delegate(orch: "Orchestrator", decision) -> str | None:
    """Return a human-readable error string if the delegate decision is
    unusable, or None if it's good to spawn."""
    if not decision.agent:
        return "missing agent name"
    workspace = orch._runtime.workspaces_dir / decision.agent
    if not workspace.exists():
        return f"no workspace for agent {decision.agent!r}"
    return None


def _default_agent_for_root(task) -> str:
    """Root tasks default to the Engineering Head as their assigned agent."""
    return "engineering_head"


def _build_agent_prompt(orch: "Orchestrator", task, agent: str) -> str:
    """Build the capabilities prompt for an EH decision step, or pass the
    brief verbatim for a worker. Prior steps are rebuilt from the DB so this
    works identically on first pickup and on post-delegation resumption.

    For revisited roots, a one-shot context header is prepended to the EH
    prompt on the very first orchestration step (detected via audit log).
    """
    from src.orchestrator.capabilities import build_capabilities_prompt
    if agent != "engineering_head":
        return task.brief
    agent_names, tiers = _list_candidate_agents(orch)
    agents_for_prompt = []
    for name in agent_names:
        enrollment = orch._db.get_enrollment(name)
        desc = enrollment["description"] if enrollment else name
        tier = tiers.get(name)
        agents_for_prompt.append({
            "name": name,
            "description": desc,
            "tier": tier.value if tier else "green",
        })
    prior_steps = _build_prior_steps_from_db(orch, task.id)
    base = build_capabilities_prompt(
        brief=task.brief,
        agents=agents_for_prompt,
        step_number=task.orchestration_step_count + 1,  # 1-indexed for EH display
        max_steps=orch._settings.max_orchestration_steps,
        prior_steps=prior_steps,
    )
    header = _revisit_header_if_applicable(orch, task.id)
    if header is not None:
        return header + base
    return base


def _list_candidate_agents(orch: "Orchestrator"):
    """Return (agent_names, tiers_map) — same shape as orchestrator used."""
    if orch._runtime.workspaces_dir.exists():
        names = [
            d.name for d in orch._runtime.workspaces_dir.iterdir()
            if d.is_dir() and d.name != "engineering_head"
        ]
    else:
        names = []
    tiers = orch._tracker.get_all_tiers(names)
    return names, tiers


def _revisit_header_if_applicable(orch: "Orchestrator", task_id: str) -> str | None:
    """Return a 5-6 line revisit context header, or None.

    Trigger: the task has a `revisit_of` audit entry AND no `orchestration_step`
    audit entry. The latter is how we detect "first step" without timestamps —
    once the EH has produced a decision, `log_orchestration_step` writes a row
    and this helper returns None on every subsequent call.
    """
    logs = orch._db.get_audit_logs(task_id)
    revisit_entry = next(
        (e for e in logs if e["action"] == "revisit_of"), None,
    )
    if revisit_entry is None:
        return None
    if any(e["action"] == "orchestration_step" for e in logs):
        return None

    payload = revisit_entry["payload"]
    predecessor = payload["predecessor_root"]
    flagged = payload["flagged"]
    prior_status = payload["prior_status"]
    cascade = payload.get("cascade") or [predecessor]
    note = payload.get("founder_note")

    lines = [
        f"REVISIT CONTEXT: this root is a revisit of {predecessor} "
        f"(which ended in {prior_status}).",
        f"Founder flagged {flagged} in the predecessor lineage — "
        "start your investigation there.",
        "Cascade chain (predecessor root -> flagged): "
        + " -> ".join(cascade),
    ]
    if note:
        lines.append(f"Founder note: {note}")
    lines.append(
        f"Inspect via: `opc details {predecessor}`, "
        f"`opc audit {predecessor}`, `opc recall {predecessor}`."
    )
    lines.append(
        "You may reuse successful sub-tasks' artifacts (referenced by path in "
        "new child briefs); old child task rows stay frozen."
    )
    return "\n".join(lines) + "\n\n"


def _build_prior_steps_from_db(orch: "Orchestrator", task_id: str):
    """Reconstruct StepRecord[] for the EH by reading children's terminal
    outcomes from the DB. Only direct children of `task_id` count — each child
    is one past orchestration step. Order: creation order, 1-indexed."""
    from src.models import StepRecord
    steps: list[StepRecord] = []
    for i, child_id in enumerate(orch._db.get_children(task_id), start=1):
        child = orch._db.get_task(child_id)
        if child is None:
            continue
        success = child.status == TaskStatus.COMPLETED
        steps.append(StepRecord(
            step_number=i,
            agent=child.assigned_agent or "unknown",
            action=f"delegate: {(child.brief or '')[:100]}",
            result_summary=child.note or "(no summary)",
            success=success,
        ))
    return steps


def _complete(orch: "Orchestrator", task_id: str, *, note: str, artifact_dir: str | None = None) -> None:
    from datetime import datetime, timezone
    # Idempotence guard: /cancel may have already taken this task to FAILED
    # between Popen return and here. Don't resurrect a cancelled task back to
    # COMPLETED just because the subprocess happened to finish cleanly before
    # SIGTERM arrived.
    existing = orch._db.get_task(task_id)
    if existing is not None and existing.status in TERMINAL_STATES:
        return
    orch._db.update_task(
        task_id,
        status=TaskStatus.COMPLETED,
        block_kind=None,
        note=note,
        final_artifact_dir=artifact_dir,
        completed_at=datetime.now(timezone.utc).isoformat(),
    )
    _log_verdict_if_delegated(orch, task_id, success=True)
    orch._update_task_history(task_id)


def _fail(orch: "Orchestrator", task_id: str, *, note: str) -> None:
    from datetime import datetime, timezone
    # Idempotence guard — same rationale as _complete. When /cancel SIGTERMs
    # the subprocess, run_step re-enters via the post-execution classifier and
    # tries to write a "session failed (rc=-15; ...)" note. That must NOT
    # overwrite the founder's "cancelled by founder: ..." note.
    existing = orch._db.get_task(task_id)
    if existing is not None and existing.status in TERMINAL_STATES:
        return
    orch._db.update_task(
        task_id,
        status=TaskStatus.FAILED,
        block_kind=None,
        note=note,
        completed_at=datetime.now(timezone.utc).isoformat(),
    )
    _log_verdict_if_delegated(orch, task_id, success=False)
    orch._update_task_history(task_id)


def _log_verdict_if_delegated(
    orch: "Orchestrator", task_id: str, *, success: bool,
) -> None:
    """Emit the implicit EH review_verdict + refresh the worker scorecard.

    The Engineering Head is the implicit reviewer of every delegated child:
    a COMPLETED child is an "approved" delegation, a FAILED child is
    "rejected". PerformanceTracker reads these rows to compute tiers, so
    skipping them leaves every delegated agent on stale performance data
    (see P1 in 2026-04-20 review).
    """
    task = orch._db.get_task(task_id)
    if task is None or task.parent_task_id is None:
        return
    agent = task.assigned_agent
    if not agent or agent in ("engineering_head", "orchestrator", "unknown"):
        return
    orch._audit.log_review_verdict(
        task_id=task_id,
        reviewer="engineering_head",
        verdict="approved" if success else "rejected",
        feedback=task.note,
        reviewed_agent=agent,
    )
    orch._tracker.update_scorecard(agent)


def _enqueue_parent_if_waiting(orch: "Orchestrator", task_id: str) -> None:
    """Idempotent: advance the parent only if it's actually waiting on THIS
    lineage (blocked+DELEGATED) AND all its children are now terminal.

    Two outcomes:
      - every child COMPLETED → enqueue parent for its next EH decision step.
      - any child FAILED → cascade-fail the parent with a referencing note
        and recurse up. No retry: the EH does not get another decision step
        after a failed delegation. The alternative (re-enqueueing so the EH
        can "try again") has historically produced runs of 6+ failed
        retries on the same brief (TASK-033..038, TASK-041..045), burning
        tokens and masking the real failure mode.
    """
    task = orch._db.get_task(task_id)
    if task is None or task.parent_task_id is None:
        return
    parent = orch._db.get_task(task.parent_task_id)
    if parent is None or parent.status != TaskStatus.BLOCKED:
        return
    if parent.block_kind != BlockKind.DELEGATED:
        return
    siblings = [orch._db.get_task(cid) for cid in orch._db.get_children(parent.id)]
    if any(s is None or s.status not in TERMINAL_STATES for s in siblings):
        return

    failed = [s for s in siblings if s.status == TaskStatus.FAILED]
    if failed:
        first = failed[0]
        _fail(
            orch, parent.id,
            note=f"delegated child {first.id} failed: {first.note or '(no note)'}",
        )
        _enqueue_parent_if_waiting(orch, parent.id)
        return

    queue = getattr(orch, "_queue", None)
    if queue is not None:
        queue.put_nowait(parent.id)


def _session_failed_note(result, report) -> str:
    """Build an enriched `agent session failed` note.

    The pre-TASK-045 version wrote a bare constant string, so when the
    Claude subprocess finished without calling `opc report-completion`
    there was no trace of WHY — rc, stderr, and stdout were all dropped
    on the floor. Now we surface rc and the tail of stderr (or stdout,
    if stderr is empty) so the next class-of-TASK-045 failure is
    self-diagnosing from the audit trail alone.
    """
    bits: list[str] = []
    rc = getattr(result, "returncode", None)
    bits.append(f"rc={rc}" if rc is not None else "rc=?")
    err = (getattr(result, "stderr_tail", "") or "").strip()
    out = (getattr(result, "stdout_tail", "") or "").strip()
    preview_src, label = (err, "stderr") if err else (out, "stdout") if out else ("", "")
    if label:
        preview = preview_src.replace("\n", " ")[-300:]
        bits.append(f"{label}: {preview}")
    error_str = getattr(result, "error", None)
    if error_str:
        bits.append(error_str)
    if report is None and getattr(result, "success", False):
        bits.append("no completion callback")
    return f"agent session failed ({'; '.join(bits)})"
