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
from src.orchestrator.org_config import load_org_config

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
        orch.notify_escalated(
            task_id=task_id, agent="orchestrator", reason=reason,
        )
        return

    # ---- 3. Atomic claim: unblock + increment + mark in_progress ----
    # Conditional CAS on (expected_status, expected_block_kind) — if another
    # worker has already claimed this task_id (duplicate enqueue from a
    # multi-child fan-in race, or parent auto-resume colliding with a late
    # callback), the UPDATE matches zero rows and we return silently.
    claimed = db.try_claim_for_step(
        task_id,
        expected_status=task.status,
        expected_block_kind=task.block_kind,
        new_count=next_count,
    )
    if not claimed:
        logger.debug(
            "run_step %s: lost claim race (another worker is advancing it)",
            task_id,
        )
        return

    # ---- 4. Run the agent subprocess ----
    agent = task.assigned_agent or _default_agent_for_root(orch, task)
    if task.assigned_agent is None:
        db.update_task(task_id, assigned_agent=agent)

    prompt = _build_agent_prompt(orch, task, agent)
    try:
        result, report = orch._run_agent(task_id, agent, prompt)
    except Exception as exc:
        note = f"agent invocation failed: {exc}"
        _fail(orch, task_id, note=note)
        failure_kind = _classify_failure_kind(None, None, mode="exception")
        spawned = _maybe_spawn_auto_revisit(
            orch, task_id, agent,
            failure_kind=failure_kind,
            error_context={"mode": "exception", "detail": str(exc)},
        )
        _enqueue_parent_if_waiting(
            orch, task_id, root_auto_revisit_spawned=spawned,
        )
        _notify_failure_if_eligible(
            orch, task_id, failure_kind=failure_kind,
            failure_note=note, auto_revisit_spawned=spawned,
        )
        return

    # Persist token usage for this session, regardless of session outcome.
    # Spec 4.3: skip when None; otherwise write — including the parse-failure
    # case where token columns are NULL but ``usage_raw_json`` carries the
    # raw payload. Done before outcome classification so timeouts / blocked
    # sessions still land their usage row.
    if result.token_usage is not None:
        db.insert_session_token_usage(
            task_id=task_id,
            agent=agent,
            session_id=result.session_id,
            executor=orch._resolve_executor_name(agent),
            token_usage=result.token_usage,
        )

    # ---- 5. Classify outcome ----
    if not result.success or report is None:
        note = _session_failed_note(result, report)
        _fail(orch, task_id, note=note)
        failure_kind = _classify_failure_kind(
            result, report, mode="session_failure",
        )
        spawned = _maybe_spawn_auto_revisit(
            orch, task_id, agent,
            failure_kind=failure_kind,
            error_context=_executor_failure_context(result, report),
        )
        _enqueue_parent_if_waiting(
            orch, task_id, root_auto_revisit_spawned=spawned,
        )
        _notify_failure_if_eligible(
            orch, task_id, failure_kind=failure_kind,
            failure_note=note, auto_revisit_spawned=spawned,
        )
        return

    orch._log_step_result(task_id, result, report)

    if report.status == "blocked":
        note = f"self-blocked: {report.output_summary}"
        _fail(orch, task_id, note=note)
        _enqueue_parent_if_waiting(orch, task_id)
        _notify_failure_if_eligible(
            orch, task_id, failure_kind="self_blocked",
            failure_note=note, auto_revisit_spawned=False,
            last_summary=report.output_summary or "",
        )
        return

    # ---- 6. Parse next step ----
    # Only team managers speak the NextStep JSON protocol. Worker
    # completions are plain prose/summary payloads — treating them as
    # manager decisions reclassifies every non-JSON output_summary as
    # `escalate` (see P1 in 2026-04-20 review).
    if orch.teams.is_team_manager(agent):
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
        orch.notify_escalated(
            task_id=task_id, agent=agent, reason=reason,
            last_summary=getattr(report, "output_summary", "") or "",
        )
        # parent stays blocked(DELEGATED) until this task reaches a terminal.
        return

    if decision.action == "delegate":
        # First: hard-fail on structurally invalid delegate (no agent name or
        # missing workspace). These are unrecoverable for this step.
        err = _validate_delegate(orch, decision)
        if err is not None:
            note = f"invalid delegate: {err}"
            _fail(orch, task_id, note=note)
            _enqueue_parent_if_waiting(orch, task_id)
            _notify_failure_if_eligible(
                orch, task_id, failure_kind="invalid_delegate",
                failure_note=note, auto_revisit_spawned=False,
            )
            return
        # Cross-team delegation guard: a manager may only delegate to workers
        # on its own team. Violations feed a feedback step back (not a hard
        # fail) so the manager can correct its decision on the next step.
        caller_team = orch.teams.team_for_manager(agent)
        target_team = orch.teams.team_for_agent(decision.agent)
        if caller_team is None or target_team is None or caller_team != target_team:
            feedback = (
                f"Invalid delegation: you are on team {caller_team!r}, "
                f"but {decision.agent!r} is on team {target_team!r}. "
                "Pick a worker on your own team, or escalate."
            )
            db.insert_task_result(
                task_id=task_id,
                agent=agent,
                session_id="",
                status="completed",
                confidence_score=0,
                output_summary=feedback,
                risks_flagged=[],
            )
            orch._audit.log_orchestration_step(
                task_id, next_count, {"action": "feedback", "reason": feedback},
            )
            # step already counted on claim (try_claim_for_step increments
            # orchestration_step_count atomically before the agent runs).
            db.update_task(task_id, status=TaskStatus.PENDING, block_kind=None)
            if orch._queue is not None:
                orch._queue.put_nowait(orch._slug, task_id)
            return
        from src.models import TaskRecord
        # Revision tracking: bump the delegating task's revision_count only
        # when the manager re-delegates to the *worker-of-record* — i.e. the
        # earliest-completed child. By convention, the first delegated child
        # is the worker for this task (true for both Content Team and
        # Engineering Team flows); subsequent same-agent delegations are
        # genuine revise cycles. Re-delegating to QA/reviewer is *not* a
        # revision and must not bump the count (spec
        # `protocol/05a-teams.md`: "manager escalates after 2 rounds").
        existing_children = db.get_children(task_id)
        completed_children = []
        for cid in existing_children:
            c = db.get_task(cid)
            if c is not None and c.status in (TaskStatus.COMPLETED, TaskStatus.FAILED):
                completed_children.append(c)
        if completed_children:
            # Earliest by created_at; tie-break on id for determinism.
            completed_children.sort(key=lambda c: (c.created_at, c.id))
            worker_of_record = completed_children[0].assigned_agent
            if worker_of_record == decision.agent:
                db.increment_revision_count(task_id)
        child_id = db.next_task_id()
        db.insert_task(TaskRecord(
            id=child_id,
            team=task.team,
            brief=decision.prompt or "",
            assigned_agent=decision.agent,
            parent_task_id=task_id,
            status=TaskStatus.PENDING,
            session_timeout_seconds=task.session_timeout_seconds,
        ))
        db.update_task(
            task_id,
            status=TaskStatus.BLOCKED,
            block_kind=BlockKind.DELEGATED,
            note=f"Delegated to {decision.agent} (child={child_id})",
        )
        if orch._queue is not None:
            orch._queue.put_nowait(orch._slug, child_id)
        return

    # ---- 8. Unknown action ----
    note = f"unknown action: {decision.action}"
    _fail(orch, task_id, note=note)
    _enqueue_parent_if_waiting(orch, task_id)
    _notify_failure_if_eligible(
        orch, task_id, failure_kind="unknown_action",
        failure_note=note, auto_revisit_spawned=False,
    )


def _validate_delegate(orch: "Orchestrator", decision) -> str | None:
    """Return a human-readable error string if the delegate decision is
    unusable, or None if it's good to spawn."""
    if not decision.agent:
        return "missing agent name"
    workspace = orch._paths.workspaces_dir / decision.agent
    if not workspace.exists():
        return f"no workspace for agent {decision.agent!r}"
    return None


def _default_agent_for_root(orch: "Orchestrator", task) -> str:
    """Root tasks default to the manager for their team."""
    return orch.teams.manager_for_team(task.team).name


def _build_agent_prompt(orch: "Orchestrator", task, agent: str) -> str:
    """Build the capabilities prompt for a team-manager decision step, or pass
    the brief verbatim for a worker. Prior steps are rebuilt from the DB so
    this works identically on first pickup and on post-delegation resumption.

    For revisited roots, a one-shot context header is prepended to the
    manager prompt on the very first orchestration step (detected via audit
    log).
    """
    from src.orchestrator.capabilities import build_capabilities_prompt
    if not orch.teams.is_team_manager(agent):
        return task.brief
    from src.orchestrator import prompt_loader
    agent_names, tiers = _list_candidate_agents(orch, agent)
    agents_for_prompt = []
    for name in agent_names:
        candidate = prompt_loader.load_agent(orch._paths, name)
        desc = (candidate.description if candidate is not None else None) or name
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
        step_number=task.orchestration_step_count + 1,  # 1-indexed for manager display
        max_steps=orch._settings.max_orchestration_steps,
        prior_steps=prior_steps,
        manager_name=agent,
    )
    headers: list[str] = []
    revisit = _revisit_header_if_applicable(orch, task.id)
    if revisit is not None:
        headers.append(revisit)
    resolved = _resolved_escalation_header_if_applicable(orch, task.id)
    if resolved is not None:
        headers.append(resolved)
    if headers:
        return "".join(headers) + base
    return base


def _list_candidate_agents(orch: "Orchestrator", calling_manager: str):
    """Return (agent_names, tiers_map) — same shape as orchestrator used.

    Only includes workers on the calling manager's own team that have an
    existing workspace on disk. Returns an empty list when the calling_manager
    is not found in the registry (e.g. fallback / tests without a full layout).
    """
    caller_team = orch.teams.team_for_manager(calling_manager)
    if caller_team is None:
        return [], {}
    team_members = set(orch.teams.manager_for_team(caller_team).workers)
    team_members.discard(calling_manager)  # manager should not delegate to itself

    if orch._paths.workspaces_dir.exists():
        names = sorted(
            d.name for d in orch._paths.workspaces_dir.iterdir()
            if d.is_dir() and d.name in team_members
        )
    else:
        names = []
    tiers = orch._tracker.get_all_tiers(names)
    return names, tiers


# Shared discipline tail appended to both revisit headers. Addresses the
# brief-vs-reality divergence failure mode (TALK-028, tourism-org): on a
# revisit-spawned session the literal brief is often stale, and the manager
# tends either to (a) execute the brief verbatim and stall against current
# state, or (b) improvise "the next obvious step" and get blocked by
# classifiers/workflow gates. The discipline frames the binary choice
# (execute-with-divergence-note OR escalate-with-diagnosis) and explicitly
# bans improvisation. Generic enough for any manager role.
_REVISIT_DISCIPLINE_LINES = [
    "Status-assess before acting on the brief below — it was authored before this "
    "revisit and may be stale. Inspect the predecessor (commands above) and verify "
    "ground truth for the work the brief describes. Then either: execute the real "
    "next step, noting any divergence from the brief in your output_summary; or "
    "escalate with a precise diagnosis (what the brief asked, what reality is, why "
    "the gap is unbridgeable). Do NOT improvise — half-completed work blocks the "
    "workstream.",
]


def _revisit_header_if_applicable(orch: "Orchestrator", task_id: str) -> str | None:
    """Return a revisit context header, or None.

    Trigger: the task has a `revisit_of` OR `auto_revisit_of` audit entry
    AND no `orchestration_step` audit entry. The latter is how we detect
    "first step" without timestamps — once the team manager has produced
    a decision, `log_orchestration_step` writes a row and this helper
    returns None on every subsequent call.
    """
    logs = orch._db.get_audit_logs(task_id)
    revisit_entry = next(
        (e for e in logs if e["action"] in ("revisit_of", "auto_revisit_of")),
        None,
    )
    if revisit_entry is None:
        return None
    if any(e["action"] == "orchestration_step" for e in logs):
        return None

    if revisit_entry["action"] == "auto_revisit_of":
        return _auto_revisit_header(revisit_entry["payload"])

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
        f"Inspect via: `grassland details {predecessor}`, "
        f"`grassland audit {predecessor}`, `grassland recall {predecessor}`."
    )
    lines.append(
        "You may reuse successful sub-tasks' artifacts (referenced by path in "
        "new child briefs); old child task rows stay frozen."
    )
    lines.extend(_REVISIT_DISCIPLINE_LINES)

    # SR summary block — list any script requests submitted by the predecessor.
    predecessor_logs = orch._db.get_audit_logs(predecessor)
    sr_entries = [e for e in predecessor_logs if e.get("action") == "job_submitted"]
    if sr_entries:
        lines.append("")
        lines.append("This task previously submitted script requests:")
        for e in sr_entries:
            payload_e = e.get("payload") or {}
            if isinstance(payload_e, str):
                import json as _json  # noqa: PLC0415

                try:
                    payload_e = _json.loads(payload_e)
                except Exception:
                    payload_e = {}
            job_id = payload_e.get("script_request_id", "JOB-?")
            title = payload_e.get("title", "(no title)")
            sr = orch._db.get_job(job_id) if job_id != "JOB-?" else None
            status = sr.status.value if sr else "?"
            marker = ""
            if sr and sr.status.value in ("pending", "running"):
                marker = " [still pending — founder action needed]"
            lines.append(f"  - {job_id} ({status}) — {title}{marker}")
        lines.append("")
        lines.append("Read the outputs / rejection reasons before continuing:")
        for e in sr_entries:
            payload_e = e.get("payload") or {}
            if isinstance(payload_e, str):
                import json as _json  # noqa: PLC0415

                try:
                    payload_e = _json.loads(payload_e)
                except Exception:
                    payload_e = {}
            job_id = payload_e.get("script_request_id", "JOB-?")
            lines.append(f"  grassland scripts show {job_id}")
            lines.append(f"  grassland scripts output {job_id}")

    return "\n".join(lines) + "\n\n"


def _auto_revisit_header(payload: dict) -> str:
    """Render the first-step header for an orchestrator-triggered auto-revisit.

    Different language from the founder-revisit header: the manager needs
    to know an opaque agent failure happened (not a founder-flagged
    problem) and to consider whether the original approach is still sound
    or whether the failure mode suggests a different decomposition.
    """
    predecessor = payload["predecessor_root"]
    failed_task = payload["failed_task"]
    failed_agent = payload["failed_agent"]
    cascade = payload.get("cascade") or [failed_task]
    err = payload.get("error_context") or {}
    attempt = payload.get("attempt", 1)
    failure_kind = payload.get("failure_kind") or "session_failed"

    err_bits: list[str] = []
    mode = err.get("mode")
    if mode == "exception":
        err_bits.append(f"exception: {err.get('detail', '?')}")
    elif mode == "session_failure":
        rc = err.get("rc")
        err_bits.append(f"rc={rc if rc is not None else '?'}")
        if err.get("missing_callback"):
            err_bits.append("no completion callback")
        executor_error = err.get("executor_error")
        if executor_error:
            err_bits.append(executor_error)
        stderr_tail = err.get("stderr_tail") or ""
        stdout_tail = err.get("stdout_tail") or ""
        preview = stderr_tail or stdout_tail
        if preview:
            label = "stderr" if stderr_tail else "stdout"
            err_bits.append(f"{label}: {preview.replace(chr(10), ' ')}")
    err_summary = "; ".join(err_bits) if err_bits else "(no diagnostics)"

    lines = [
        f"AUTO-REVISIT CONTEXT (orchestrator-triggered, kind={failure_kind}, "
        f"attempt {attempt} of {_AUTO_REVISIT_CAP_PER_KIND} for this kind): "
        f"this root is a revisit of {predecessor}, "
        "spawned because an agent in the predecessor lineage hit an opaque "
        "failure.",
        f"Failed task: {failed_task} (agent: {failed_agent}).",
        f"Failure: {err_summary}",
        "Cascade chain (predecessor root -> failed task): "
        + " -> ".join(cascade),
        f"Inspect via: `grassland details {predecessor}`, "
        f"`grassland audit {predecessor}`, `grassland recall {predecessor}`.",
        "Re-evaluate the approach — the failure may be transient (worth "
        "the same plan with a fresh subprocess) or structural (a different "
        "decomposition is needed). Decide accordingly.",
    ]
    lines.extend(_REVISIT_DISCIPLINE_LINES)
    return "\n".join(lines) + "\n\n"


def _resolved_escalation_header_if_applicable(
    orch: "Orchestrator", task_id: str,
) -> str | None:
    """Return a 2-3 line header on the first manager step after a founder
    `resolve-escalation --approve`, otherwise None.

    Trigger: the most recent `escalation_resolved` audit entry for this task
    has a higher row id than the most recent `orchestration_step` entry —
    i.e. the founder approved AND the manager hasn't run yet. Audit `id` is
    autoincrement, so id-ordering is equivalent to chronological ordering.
    Once the manager produces its first decision after re-enqueue,
    `log_orchestration_step` writes a row with a higher id and this helper
    returns None on every subsequent call.
    """
    logs = orch._db.get_audit_logs(task_id)
    last_resolved = None
    last_step = None
    for entry in logs:
        action = entry["action"]
        if action == "escalation_resolved":
            last_resolved = entry
        elif action == "orchestration_step":
            last_step = entry
    if last_resolved is None:
        return None
    if last_step is not None and last_step["id"] > last_resolved["id"]:
        return None
    payload = last_resolved["payload"] or {}
    decision = payload.get("decision", "approve")
    rationale = payload.get("rationale", "(no rationale recorded)")
    return (
        f"ESCALATION RESOLVED: founder {decision}d your prior escalation.\n"
        f"Rationale: {rationale}\n"
        "Continue from where you parked, with this verdict in mind.\n\n"
    )


def _build_prior_steps_from_db(orch: "Orchestrator", task_id: str):
    """Reconstruct StepRecord[] for the team manager by reading children's
    terminal outcomes from the DB. Only direct children of `task_id` count
    — each child is one past orchestration step. Order: creation order,
    1-indexed."""
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


def _notify_failure_if_eligible(
    orch: "Orchestrator",
    task_id: str,
    *,
    failure_kind: str,
    failure_note: str,
    auto_revisit_spawned: bool,
    last_summary: str = "",
) -> None:
    """Fire notify_failed if all gates open:
       1. feishu_notifications config exists AND notify_on_failure=true
       2. task not founder-cancelled (cancelled_at IS NULL)
       3. no auto-revisit spawned for this task

    All exceptions are swallowed — never crash the _fail caller.
    See docs/superpowers/specs/2026-05-12-feishu-interactive-actions-design.md §5.1.
    """
    if auto_revisit_spawned:
        return
    try:
        org = load_org_config(orch._paths)
        if org.feishu_notifications is None:
            return
        if not getattr(org.feishu_notifications, "notify_on_failure", False):
            return
        task = orch._db.get_task(task_id)
        if task is None or task.cancelled_at is not None:
            return
        agent = task.assigned_agent or "(unknown)"
        orch.notify_failed(
            task_id=task_id,
            agent=agent,
            failure_kind=failure_kind,
            failure_note=failure_note,
            last_summary=last_summary,
        )
    except Exception:  # noqa: BLE001
        # Gate must never crash _fail caller — _fail is on the critical path.
        return


def _log_verdict_if_delegated(
    orch: "Orchestrator", task_id: str, *, success: bool,
) -> None:
    """Emit the implicit manager review_verdict + refresh the worker scorecard.

    The team manager is the implicit reviewer of every delegated child:
    a COMPLETED child is an "approved" delegation, a FAILED child is
    "rejected". PerformanceTracker reads these rows to compute tiers, so
    skipping them leaves every delegated agent on stale performance data
    (see P1 in 2026-04-20 review).
    """
    task = orch._db.get_task(task_id)
    if task is None or task.parent_task_id is None:
        return
    agent = task.assigned_agent
    if not agent or orch.teams.is_team_manager(agent) or agent in ("orchestrator", "unknown"):
        return
    parent = orch._db.get_task(task.parent_task_id)
    reviewer_team = parent.team if parent else task.team
    try:
        reviewer = orch.teams.manager_for_team(reviewer_team).name
    except KeyError:
        reviewer = "unknown_manager"
    orch._audit.log_review_verdict(
        task_id=task_id,
        reviewer=reviewer,
        verdict="approved" if success else "rejected",
        feedback=task.note,
        reviewed_agent=agent,
    )
    orch._tracker.update_scorecard(agent)


def _enqueue_parent_if_waiting(
    orch: "Orchestrator",
    task_id: str,
    *,
    root_auto_revisit_spawned: bool = False,
) -> None:
    """Idempotent: advance the parent only if it's actually waiting on THIS
    lineage (blocked+DELEGATED) AND all its children are now terminal.

    Two outcomes:
      - every child COMPLETED → enqueue parent for its next manager decision
        step.
      - any child FAILED → cascade-fail the parent with a referencing note
        and recurse up. No retry: the team manager does not get another
        decision step after a failed delegation. The alternative
        (re-enqueueing so the manager can "try again") has historically
        produced runs of 6+ failed retries on the same brief
        (TASK-033..038, TASK-041..045), burning tokens and masking the real
        failure mode.

    ``root_auto_revisit_spawned`` is threaded through the cascade so every
    ancestor's Feishu-failure gate knows the founder-dispatched root has
    already been auto-revisited — the work IS being retried, so the
    cascading "cascade_fail" notifications are pure noise and must be
    suppressed. Callers that did not spawn an auto-revisit pass the
    default ``False``. See spec
    2026-05-25-session-timeout-auto-route-design.md §6.
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
        note = f"delegated child {first.id} failed: {first.note or '(no note)'}"
        _fail(orch, parent.id, note=note)
        _enqueue_parent_if_waiting(
            orch, parent.id,
            root_auto_revisit_spawned=root_auto_revisit_spawned,
        )
        _notify_failure_if_eligible(
            orch, parent.id, failure_kind="cascade_fail",
            failure_note=note,
            auto_revisit_spawned=root_auto_revisit_spawned,
        )
        return

    queue = getattr(orch, "_queue", None)
    if queue is not None:
        queue.put_nowait(orch._slug, parent.id)


_AUTO_REVISIT_CAP_PER_KIND = 2

# Triad of "agent died mid-flight, retry as-is" kinds. Routed identically by
# the auto-revisit machinery in v1; constant exists so future per-class policy
# (e.g., "fail-fast on rate_limit class for batch jobs") doesn't have to
# re-derive the set. See spec 2026-05-25-session-timeout-auto-route-design.md §4.1.
_SESSION_TIMEOUT_CLASS = frozenset({"session_timeout", "no_callback", "rate_limit"})


def _classify_failure_kind(result, report, *, mode: str) -> str:
    """Classify a failure into a granular kind for per-kind dedup + routing.

    ``mode`` ∈ {"exception", "session_failure"} — distinguishes the two
    opaque-failure entry points in ``run_step_impl``.

    Five canonical kinds plus a defensive ``session_failed`` fallback:
      - ``session_timeout`` — subprocess walltime exceeded
        ``session_timeout_seconds`` (executors.py:197 writes
        ``"Session timed out after {N} seconds"`` into ``result.error``).
      - ``no_callback`` — rc=0 but no completion callback (TASK-045 class:
        agent exited clean without invoking ``grassland report-completion``).
      - ``rate_limit`` — executor reported a provider rate limit on stdout/
        stderr/error (e.g., Claude's "hit your limit · resets at HH:MM").
      - ``executor_error`` — subprocess exited with non-zero ``returncode``;
        stderr tail is usually diagnostic.
      - ``agent_exception`` — Python exception escaped
        ``Orchestrator._run_agent`` before the subprocess boundary.

    Fallback ``session_failed`` preserves graceful degradation if a new
    executor surface introduces a failure shape we haven't classified yet.
    """
    if mode == "exception":
        return "agent_exception"
    if result is None:
        return "session_failed"

    err = getattr(result, "error", None) or ""
    success = getattr(result, "success", False)
    rc = getattr(result, "returncode", None)

    if err.startswith("Session timed out after"):
        return "session_timeout"

    haystack = (
        err.lower()
        + " "
        + (getattr(result, "stdout_tail", "") or "").lower()
        + " "
        + (getattr(result, "stderr_tail", "") or "").lower()
    )
    if ("hit your limit" in haystack and "reset" in haystack) or "rate limit" in haystack:
        return "rate_limit"

    if success and report is None:
        return "no_callback"

    if rc is not None and rc != 0:
        return "executor_error"

    return "session_failed"


_CHAIN_HOP_LIMIT_FOR_COUNTING = 200


def _count_prior_auto_revisits_by_kind(
    orch: "Orchestrator", root_id: str, kind: str,
) -> int:
    """Walk the revisit chain ending at ``root_id``; count ``auto_revisit_of``
    audit entries whose ``payload.failure_kind`` matches ``kind``.

    Founder revisits (``action="revisit_of"``) are excluded — they're
    intentional human retries, not part of the auto-retry budget. Auto-revisit
    rows written before this spec shipped (no ``failure_kind`` in payload)
    are also excluded; that's mildly lenient by design — see spec §10.

    Chain-walk safety: ``walk_revisit_chain`` has a defensive max-hop bound to
    prevent runaway lineage walks. Read-path callers pass ``truncate=True``
    to gracefully ignore the overflow, but here that would silently
    undercount older auto-revisits past the window and let the per-kind cap
    be exceeded on long-lived tasks (founder revisits also consume hops). We
    walk with a larger bound and ``truncate=False``; if the chain still
    overflows, we treat that as "cap definitively hit" — refusing to spawn
    is the conservative answer when we cannot verify the count, and it also
    acts as a circuit breaker against pathological revisit loops.
    """
    db = orch._db
    from src.infrastructure.database import LineageTooDeep  # local: avoid cycle
    try:
        chain = db.walk_revisit_chain(
            root_id,
            max_hops=_CHAIN_HOP_LIMIT_FOR_COUNTING,
            truncate=False,
        )
    except LineageTooDeep:
        return _AUTO_REVISIT_CAP_PER_KIND
    count = 0
    for r in chain:
        for entry in db.get_audit_logs(r.id):
            if entry["action"] != "auto_revisit_of":
                continue
            payload = entry.get("payload") or {}
            if payload.get("failure_kind") == kind:
                count += 1
    return count


def _executor_failure_context(result, report) -> dict:
    """Build the structured error_context payload for the auto_revisit_of audit.

    Captures rc, stderr/stdout tail, executor error string, and a flag for
    the "rc=0 but no completion callback" branch. The team manager's first
    step on the auto-revisit reads this back via the revisit header so the
    decision is grounded in the actual failure mode, not a free-form note.
    """
    if result is None:
        return {"mode": "exception"}
    rc = getattr(result, "returncode", None)
    err = (getattr(result, "stderr_tail", "") or "").strip()
    out = (getattr(result, "stdout_tail", "") or "").strip()
    return {
        "mode": "session_failure",
        "rc": rc,
        "stderr_tail": err[-300:],
        "stdout_tail": out[-300:],
        "executor_error": getattr(result, "error", None),
        "missing_callback": (
            report is None and getattr(result, "success", False)
        ),
    }


def _maybe_spawn_auto_revisit(
    orch: "Orchestrator",
    failed_task_id: str,
    failed_agent: str,
    *,
    failure_kind: str,
    error_context: dict,
) -> bool:
    """Spawn an orchestrator-triggered revisit on an opaque agent failure.

    Triggered ONLY by the two opaque agent-error paths in run_step (an
    exception escaping ``_run_agent`` or a non-success ``ExecutorResult``
    branch which subsumes both subprocess timeouts and rc=0-no-callback).
    Self-blocked workers, invalid-delegate JSON, max-step escalations, and
    founder cancellations do NOT auto-revisit — those failures are
    deliberate or load-bearing.

    Walks parent links to find the team-manager root (the original task the
    founder dispatched), then spawns a NEW root linked via
    ``revisit_of_task_id``. The original lineage's cascade-to-parent
    behavior is unchanged; the auto-revisit runs as an independent tree
    so the team manager can re-decide with the structured ``error_context``
    in hand.

    Capped at ``_AUTO_REVISIT_CAP_PER_KIND`` auto-revisits **per
    ``failure_kind`` per chain** — if two prior same-kind auto-revisits
    already exist in the predecessor chain, give up rather than burning
    tokens on the TASK-033..045 retry-loop pattern. A different kind in
    the same chain has its own independent budget, so a single executor
    crash does not exhaust the session-timeout cap. Founder revisits do
    not count toward the cap (they're intentional human retries). See
    spec 2026-05-25-session-timeout-auto-route-design.md §5.

    Returns True if a revisit row was inserted, False otherwise (no chain,
    cap hit, or future not-eligible cases).
    """
    db = orch._db
    chain = db.walk_ancestors(failed_task_id)
    if not chain:
        return False
    # Founder cancellation gate: /cancel stamps cancelled_at + flips status to
    # FAILED, then SIGTERMs the running subprocess. The dying subprocess returns
    # rc=-15, which run_step's classifier reads as executor_error and routes
    # here. Without this check the cancel would silently respawn a new root via
    # revisit_of_task_id and re-enqueue it — exactly the "respawn on cancel"
    # bug. Mirrors the docstring's explicit exclusion of founder cancellations.
    if chain[0].cancelled_at is not None:
        return False
    root = chain[-1]

    prior = _count_prior_auto_revisits_by_kind(orch, root.id, failure_kind)
    if prior >= _AUTO_REVISIT_CAP_PER_KIND:
        return False

    from src.models import TaskRecord

    new_id = db.next_task_id()
    db.insert_task(TaskRecord(
        id=new_id,
        brief=root.brief,
        team=root.team,
        assigned_agent=root.assigned_agent,
        status=TaskStatus.PENDING,
        parent_task_id=None,
        revisit_of_task_id=root.id,
        session_timeout_seconds=root.session_timeout_seconds,
    ))

    cascade = [t.id for t in reversed(chain)]
    orch._audit.log_auto_revisit_of(
        task_id=new_id,
        predecessor_root=root.id,
        failed_task=failed_task_id,
        failed_agent=failed_agent,
        cascade=cascade,
        failure_kind=failure_kind,
        error_context=error_context,
        attempt=prior + 1,
    )
    orch._audit.log_revisit_spawned(
        predecessor_task_id=root.id, new_root=new_id,
    )

    queue = getattr(orch, "_queue", None)
    if queue is not None:
        queue.put_nowait(orch._slug, new_id)
    return True


def _session_failed_note(result, report) -> str:
    """Build an enriched `agent session failed` note.

    The pre-TASK-045 version wrote a bare constant string, so when the
    Claude subprocess finished without calling `grassland report-completion`
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
