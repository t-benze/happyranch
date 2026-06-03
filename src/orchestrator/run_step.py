"""Implementation of Orchestrator.run_step — the single primitive that advances
a task one subprocess call at a time. Separate from orchestrator.py so the
algorithm has its own test surface.

Entry contract: task MUST be one of:
  (a) status=pending, or
  (b) status=blocked AND block_kind=DELEGATED AND all children are terminal, or
  (c) status=blocked AND block_kind=BLOCKED_ON_JOB AND all blocking jobs are terminal.
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


def run_step_impl(orch: "Orchestrator", task_id: str, metadata: dict | None = None) -> None:
    # metadata: optional resume context (trigger, triggering_job_id); read by the CAS-win audit hook in Task 11.
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
    elif task.status == TaskStatus.BLOCKED and task.block_kind == BlockKind.BLOCKED_ON_JOB:
        # Blocked-on-job task: re-check live job table to see whether all
        # blocking jobs have reached a terminal state. Spec §5.1.
        import json as _json
        try:
            job_ids = _json.loads(task.blocked_on_job_ids or "[]")
        except _json.JSONDecodeError:
            logger.debug("run_step %s: blocked_on_job_ids unparseable", task_id)
            return
        if not job_ids:
            logger.debug("run_step %s: blocked_on_job_ids empty", task_id)
            return
        _TERMINAL_JOB_STATES = {"completed", "failed", "rejected"}
        for jid in job_ids:
            jstatus = db.get_job_status(jid)
            if jstatus not in _TERMINAL_JOB_STATES:
                logger.debug(
                    "run_step %s: blocking job %s still in-flight (status=%s)",
                    task_id, jid, jstatus,
                )
                return
        # All jobs terminal — fall through to step 2 + step 3.
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

    # Spec §5.2: write task_resumed_from_jobs audit row immediately after the
    # CAS wins on a BLOCKED+BLOCKED_ON_JOB → IN_PROGRESS transition. The
    # prompt-build at step 4 reads this row to inject BLOCKED-JOBS-RESULTS.
    if (task.status == TaskStatus.BLOCKED
            and task.block_kind == BlockKind.BLOCKED_ON_JOB):
        import json as _json
        try:
            job_ids = _json.loads(task.blocked_on_job_ids or "[]")
        except _json.JSONDecodeError:
            job_ids = []
        job_outcomes = {jid: (db.get_job_status(jid) or "unknown")
                        for jid in job_ids}
        md = metadata or {}
        orch._audit.log_task_resumed_from_jobs(
            task_id=task_id,
            blocking_job_ids=job_ids,
            trigger=md.get("trigger", "unknown"),
            triggering_job_id=md.get("triggering_job_id"),
            job_outcomes=job_outcomes,
        )

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
        _maybe_post_thread_followup(
            orch, task_id,
            status=TaskStatus.FAILED, auto_revisit_spawned=spawned,
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

    # Cancel-race Guard B: /cancel can land between try_claim_for_step and
    # subprocess exit. The l.41 entry guard only catches NEW enqueues. If we
    # observe cancelled_at != NULL here, the report (if any) is from a
    # cancelled tree and must not feed the decision pipeline — otherwise the
    # `delegate` branch (no idempotence guard) will resurrect the parent and
    # spawn a child task on a tree the founder explicitly killed.
    # Token usage stays persisted above (provider really charged for it).
    # See docs/superpowers/specs/2026-05-26-cancel-race-design.md §5.2.
    refetch = db.get_task(task_id)
    if refetch is None or refetch.cancelled_at is not None:
        logger.debug(
            "run_step %s: cancelled during session, dropping report", task_id,
        )
        # IN_PROGRESS cancellation: the cancel route sent SIGTERM and set
        # cancelled_at BEFORE run_step reached Site B, so Site B never runs.
        # Fire the followup here instead.  Disjoint with the cancel route's
        # Phase 1b (which fires for PENDING/BLOCKED only — tasks that had no
        # live subprocess at cancel time), so no double-fire risk.
        if refetch is not None:
            _maybe_post_thread_followup(
                orch, task_id,
                status=TaskStatus.FAILED, auto_revisit_spawned=False,
            )
        return

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
        _maybe_post_thread_followup(
            orch, task_id,
            status=TaskStatus.FAILED, auto_revisit_spawned=spawned,
        )
        return

    orch._log_step_result(task_id, result, report)

    if report.status == "blocked":
        if report.waiting_on_job_ids:
            # Spec §5.3: block-on-jobs branch. In-place transition, NOT _fail.
            import json as _json
            deduped = sorted(set(report.waiting_on_job_ids))
            # Defensive re-validation: a job could have been deleted between the
            # route POST and run_step_impl consuming the report (extremely
            # unlikely; jobs are write-once + terminal-frozen). Degrade gracefully.
            for jid in deduped:
                if db.get_job_status(jid) is None:
                    note = f"self-blocked but job {jid} not found"
                    _fail(orch, task_id, note=note)
                    _enqueue_parent_if_waiting(orch, task_id)
                    _notify_failure_if_eligible(
                        orch, task_id, failure_kind="self_blocked",
                        failure_note=note, auto_revisit_spawned=False,
                        last_summary=report.output_summary or "",
                    )
                    _maybe_post_thread_followup(
                        orch, task_id,
                        status=TaskStatus.FAILED, auto_revisit_spawned=False,
                    )
                    return
            db.update_task(
                task_id,
                status=TaskStatus.BLOCKED,
                block_kind=BlockKind.BLOCKED_ON_JOB,
                blocked_on_job_ids=_json.dumps(deduped),
                note=report.output_summary,
            )
            orch._audit.log_task_blocked_on_jobs(
                task_id=task_id, agent=agent,
                blocking_job_ids=deduped,
                output_summary_excerpt=(report.output_summary or "")[:200],
            )
            # Immediate predicate check (caller B). Spec §5.6: runs HERE, after
            # the agent session has already been cleared by submit_completion.
            # No session race.
            _maybe_resume_blocked_task(
                orch, task_id,
                trigger="block_submit", triggering_job_id=None,
            )
            return
        # Existing escalated path (waiting_on_job_ids empty).
        note = f"self-blocked: {report.output_summary}"
        _fail(orch, task_id, note=note)
        _enqueue_parent_if_waiting(orch, task_id)
        _notify_failure_if_eligible(
            orch, task_id, failure_kind="self_blocked",
            failure_note=note, auto_revisit_spawned=False,
            last_summary=report.output_summary or "",
        )
        _maybe_post_thread_followup(
            orch, task_id,
            status=TaskStatus.FAILED, auto_revisit_spawned=False,
        )
        return

    # ---- 6. Parse next step ----
    # Orchestration is driven by task TYPE, not manager role. A type=task
    # owner (any agent) speaks the NextStep protocol; a type=subtask is
    # leaf-only. `task` is the early-fetched record; task_type is immutable
    # provenance, safe to read post-claim.
    if task.task_type == "task":
        decision = orch._parse_next_step(report)
        _step_audit_id = orch._audit.log_orchestration_step(
            task_id, next_count, decision.model_dump(exclude_none=True),
        )
    else:
        from src.models import NextStep
        decision = NextStep(action="done", summary=report.output_summary)
        _step_audit_id = None

    # ---- 7. Dispatch on action ----
    if decision.action == "done":
        _complete(
            orch, task_id,
            note=decision.summary or report.output_summary,
            output_dir=report.output_dir,
        )
        _enqueue_parent_if_waiting(orch, task_id)
        _maybe_post_thread_followup(
            orch, task_id,
            status=TaskStatus.COMPLETED, auto_revisit_spawned=False,
        )
        return

    if decision.action == "escalate":
        # Atomic CAS: transition to BLOCKED(ESCALATED) only if not cancelled
        # or terminal. Closes the post-_is_already_terminal race (Codex P2 on
        # PR #34) by serializing against /cancel via the Database RLock.
        # If False: founder cancellation landed between Guard B's re-fetch and
        # here. Drop the escalate silently — the founder's terminal state wins.
        reason = decision.reason or "Escalated"
        if not db.try_escalate(task_id, reason=reason):
            logger.debug(
                "run_step %s: cancelled between re-check and escalate, dropping",
                task_id,
            )
            return
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
            _maybe_post_thread_followup(
                orch, task_id,
                status=TaskStatus.FAILED, auto_revisit_spawned=False,
            )
            return
        # Target-scope guard. Managers: own-team agents or self. Non-manager
        # owners: self only. Violations feed a feedback step back (not a hard
        # fail) so the owner can correct its decision next step.
        out_of_scope = _legs_out_of_scope(orch, owner=agent, decision=decision)
        if out_of_scope:
            parts = [f"{name!r} ({reason})" for name, reason in out_of_scope]
            if orch.teams.is_team_manager(agent):
                caller_team = orch.teams.team_for_manager(agent)
                feedback = (
                    f"Invalid delegation: you are on team {caller_team!r}, but "
                    f"{'; '.join(parts)}. Pick agents on your own team or "
                    "yourself, or escalate."
                )
            else:
                feedback = (
                    f"Invalid delegation: {'; '.join(parts)}. You may only "
                    f"delegate sub-tasks to yourself ({agent!r}), or escalate."
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
            # Self-targeted delegation is a sequence step (self-decomposition),
            # NOT a revise cycle — only bump when re-delegating to a DIFFERENT
            # worker-of-record. `agent` is this task's owner.
            if worker_of_record == decision.agent and decision.agent != agent:
                db.increment_revision_count(task_id)
        child_id = db.next_task_id()
        child = TaskRecord(
            id=child_id,
            team=task.team,
            brief=decision.prompt or "",
            assigned_agent=decision.agent,
            parent_task_id=task_id,
            status=TaskStatus.PENDING,
            session_timeout_seconds=task.session_timeout_seconds,
            task_type="subtask",
        )
        # Atomic CAS: insert child + transition parent to BLOCKED(DELEGATED)
        # under the same RLock acquisition. Serializes against /cancel via
        # Database RLock — closes the spawn-new-work race (Codex P1 on PR #34).
        # If False: cancel landed between Guard B's re-fetch and here. No child
        # was inserted, no parent overwrite, no enqueue. The founder's terminal
        # state wins. Drop silently — the founder cancelled deliberately.
        if not db.try_delegate(
            task_id, child,
            parent_note=f"Delegated to {decision.agent} (child={child_id})",
        ):
            logger.debug(
                "run_step %s: cancelled between re-check and delegate, dropping",
                task_id,
            )
            return
        # Persist the chain on the parent so child terminals can auto-advance
        # via _enqueue_parent_if_waiting (Task 8). Skip if neither `then` nor
        # `expect_verdict` is set — that's a plain single-leg delegate.
        # MUST happen BEFORE enqueueing the child: a fast worker can otherwise
        # complete and reach `_enqueue_parent_if_waiting` while active_chain is
        # still NULL, missing the auto-advance gate and waking the parent as a
        # plain delegation.
        if decision.then or decision.expect_verdict is not None:
            from src.orchestrator.chain import ChainState
            chain = ChainState(
                step_index=0,
                first_leg_expect_verdict=decision.expect_verdict,
                legs=list(decision.then),
                step_audit_id=_step_audit_id,
            )
            db.update_task_active_chain(task_id, chain.serialize())
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
    _maybe_post_thread_followup(
        orch, task_id,
        status=TaskStatus.FAILED, auto_revisit_spawned=False,
    )


def _validate_one_leg(orch: "Orchestrator", *, agent: str | None, where: str) -> str | None:
    """Validate a single delegation leg (agent present + workspace exists).
    Returns None on success, a human-readable error string on failure.
    ``where`` is used only for chain-leg messages; the first-leg messages
    preserve the original wording for backward compatibility.
    """
    if not agent:
        return "missing agent name"
    workspace = orch._paths.workspaces_dir / agent
    if not workspace.exists():
        if where == "first leg":
            return f"no workspace for agent {agent!r}"
        return f"chain leg {where}: no workspace for agent {agent!r}"
    return None


def _validate_delegate(orch: "Orchestrator", decision) -> str | None:
    """Return a human-readable error string if the delegate decision is
    unusable, or None if it's good to spawn. Validates the first leg and
    every entry in ``decision.then`` (chain legs), returning on the first
    failure encountered."""
    err = _validate_one_leg(orch, agent=decision.agent, where="first leg")
    if err is not None:
        return err
    for i, leg in enumerate(decision.then or []):
        err = _validate_one_leg(orch, agent=leg.agent, where=str(i + 2))
        if err is not None:
            return err
    return None


def _legs_out_of_scope(orch: "Orchestrator", owner: str, decision) -> list[tuple[str, str]]:
    """Return [(agent_name, reason)] for delegation legs `owner` may not target.

    - Manager owner: may target agents on its own team, or itself.
    - Non-manager owner: may target ONLY itself (self-decomposition).

    Empty list = all legs in scope.
    """
    targets = [decision.agent] + [leg.agent for leg in (decision.then or [])]
    out: list[tuple[str, str]] = []
    if orch.teams.is_team_manager(owner):
        caller_team = orch.teams.team_for_manager(owner)
        for a in targets:
            if not a or a == owner:        # self always allowed
                continue
            t = orch.teams.team_for_agent(a)
            if caller_team is None or t != caller_team:
                out.append((a, f"on team {t!r}" if t else "not on a team"))
    else:
        for a in targets:
            if not a or a == owner:
                continue
            out.append((a, "non-manager owners may only delegate to themselves"))
    return out


def _default_agent_for_root(orch: "Orchestrator", task) -> str:
    """Root tasks default to the manager for their team."""
    return orch.teams.manager_for_team(task.team).name


def _build_agent_prompt(orch: "Orchestrator", task, agent: str) -> str:
    """Build the per-task `role_guidance` body — i.e., what gets indented under
    `role_guidance: |` in the outer wrapper built by
    ``Orchestrator._build_agent_prompt``.

    Workers return an empty string: their per-task instruction is the brief,
    which the outer wrapper already renders as ``Parameters.brief``. Echoing
    it here would duplicate the brief in every worker spawn (the wrapper
    drops the ``role_guidance:`` line when this returns empty).

    Managers return the capabilities prompt (decision schema, agent roster,
    prior steps). For revisited roots, a one-shot context header is prepended
    on the very first orchestration step (detected via audit log).
    """
    from src.orchestrator.capabilities import build_capabilities_prompt
    if not orch.teams.is_team_manager(agent):
        return ""
    from src.orchestrator import prompt_loader
    agent_names = _list_candidate_agents(orch, agent)
    agents_for_prompt = []
    for name in agent_names:
        candidate = prompt_loader.load_agent(orch._paths, name)
        desc = (candidate.description if candidate is not None else None) or name
        agents_for_prompt.append({
            "name": name,
            "description": desc,
        })
    prior_steps = _build_prior_steps_from_db(orch, task.id)
    base = build_capabilities_prompt(
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
    resume_header = _blocked_jobs_resume_header_if_applicable(orch, task.id)
    if resume_header is not None:
        headers.append(resume_header)
    resolved = _resolved_escalation_header_if_applicable(orch, task.id)
    if resolved is not None:
        headers.append(resolved)
    if headers:
        return "".join(headers) + base
    return base


def _list_candidate_agents(orch: "Orchestrator", calling_manager: str) -> list[str]:
    """Return the names of workers the calling manager can delegate to.

    Only includes workers on the calling manager's own team that have an
    existing workspace on disk. Returns an empty list when the calling_manager
    is not found in the registry (e.g. fallback / tests without a full layout).
    """
    caller_team = orch.teams.team_for_manager(calling_manager)
    if caller_team is None:
        return []
    team_members = set(orch.teams.manager_for_team(caller_team).workers)

    if orch._paths.workspaces_dir.exists():
        names = sorted(
            d.name for d in orch._paths.workspaces_dir.iterdir()
            if d.is_dir() and d.name in team_members
        )
    else:
        names = []
    return names


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
        f"Inspect via: `happyranch details {predecessor}`, "
        f"`happyranch audit {predecessor}`, `happyranch recall {predecessor}`."
    )
    lines.append(
        "You may reuse successful sub-tasks' artifacts (referenced by path in "
        "new child briefs); old child task rows stay frozen."
    )
    lines.extend(_REVISIT_DISCIPLINE_LINES)

    # JOB summary block — list any jobs submitted by the predecessor.
    predecessor_logs = orch._db.get_audit_logs(predecessor)
    sr_entries = [e for e in predecessor_logs if e.get("action") == "job_submitted"]
    if sr_entries:
        lines.append("")
        lines.append("This task previously submitted jobs:")
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
            lines.append(f"  happyranch jobs show {job_id}")
            lines.append(f"  happyranch jobs output {job_id}")

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
        f"Inspect via: `happyranch details {predecessor}`, "
        f"`happyranch audit {predecessor}`, `happyranch recall {predecessor}`.",
        "Re-evaluate the approach — the failure may be transient (worth "
        "the same plan with a fresh subprocess) or structural (a different "
        "decomposition is needed). Decide accordingly.",
    ]
    lines.extend(_REVISIT_DISCIPLINE_LINES)
    return "\n".join(lines) + "\n\n"


def _blocked_jobs_resume_header_if_applicable(
    orch: "Orchestrator", task_id: str,
) -> str | None:
    """Return a BLOCKED-JOBS-RESULTS header on the first agent step after a
    task resumes from a job-block, otherwise None.

    Trigger: the most recent `task_resumed_from_jobs` audit entry for this task
    has a higher row id than the most recent `orchestration_step` entry —
    i.e. the jobs are terminal AND the agent hasn't run yet. Audit `id` is
    autoincrement, so id-ordering is equivalent to chronological ordering.
    Once the agent produces its first decision after resume,
    `log_orchestration_step` writes a row with a higher id and this helper
    returns None on every subsequent call.

    Spec: §6.4.
    """
    import json as _json  # noqa: PLC0415

    logs = orch._db.get_audit_logs(task_id)
    last_resumed = None
    last_step = None
    for entry in logs:
        action = entry["action"]
        if action == "task_resumed_from_jobs":
            last_resumed = entry
        elif action == "orchestration_step":
            last_step = entry
    if last_resumed is None:
        return None
    if last_step is not None and last_step["id"] > last_resumed["id"]:
        return None

    payload = last_resumed["payload"] or {}
    if isinstance(payload, str):
        try:
            payload = _json.loads(payload)
        except Exception:
            payload = {}
    job_ids: list[str] = payload.get("blocking_job_ids", [])
    outcomes: dict[str, str] = payload.get("job_outcomes", {})

    lines: list[str] = [
        "=== BLOCKED-JOBS-RESULTS (system) ===",
        f"You self-blocked on {', '.join(job_ids)}. They are now terminal:",
        "",
    ]
    for jid in job_ids:
        status = outcomes.get(jid, "unknown")
        lines.append(f"  {jid}  {status}")
        lines.append(f"          → happyranch jobs show {jid}")
        lines.append(f"          → happyranch jobs output {jid}")
    lines.append("")
    lines.append("Re-read your task brief; decide whether to proceed, retry, or escalate.")
    lines.append("======================================")
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
    1-indexed.

    If a chain ran since the last manager wake, a synthetic chain-summary
    entry is appended so the manager can see what happened without re-deriving
    it from raw child task records.
    """
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
    # Append chain summary if a chain ran since the last manager wake.
    chain_summary = _summarize_recent_chain(orch, task_id)
    if chain_summary is not None:
        steps.append(StepRecord(
            step_number=len(steps) + 1,
            agent="orchestrator",
            action="chain summary",
            result_summary=chain_summary,
            success=True,
        ))
    return steps


def _summarize_recent_chain(orch: "Orchestrator", parent_task_id: str) -> str | None:
    """One-line summary of the most-recent chain that ran under parent_task_id.

    Returns None if no chain_auto_advance audit rows exist on the parent.
    Otherwise pairs the audit rows (which list triggering_child_id and
    spawned_child_id) with the final spawned child's terminal verdict to
    produce a human-readable line for the manager's wake context.
    """
    audit_logs = orch._db.get_audit_logs(parent_task_id)
    rows = [r for r in audit_logs if r["action"] == "chain_auto_advance"]
    if not rows:
        return None
    # Suppress the summary if a manager decision (orchestration_step) has
    # landed AFTER the most-recent chain advance — the manager has already
    # seen this chain summary in the wake where the chain ended, and the
    # current wake is for a later non-chain event. Showing it again would
    # place a stale chain summary at the end of prior_steps, misrepresenting
    # the latest event.
    max_chain_id = max(r["id"] for r in rows)
    max_step_id = max(
        (r["id"] for r in audit_logs if r["action"] == "orchestration_step"),
        default=0,
    )
    if max_step_id > max_chain_id:
        return None
    # Filter to the most-recent chain only — multiple sequential chains may
    # share the same parent across separate manager wakes, distinguished by
    # the chain_origin_step_audit_id of the orchestration_step that minted
    # each chain.
    latest_origin_id = rows[-1]["payload"]["chain_origin_step_audit_id"]
    rows = [
        r for r in rows
        if r["payload"]["chain_origin_step_audit_id"] == latest_origin_id
    ]
    triggers = [r["payload"]["triggering_child_id"] for r in rows]
    spawned = [r["payload"]["spawned_child_id"] for r in rows]
    chain_children = triggers + ([spawned[-1]] if spawned else [])
    last_child_id = chain_children[-1]
    last_report = orch._db.get_latest_completion_report(last_child_id)
    last_verdict = last_report.verdict if last_report else None
    arrow = " → ".join(chain_children)
    if last_report and last_report.status == "blocked":
        return f"Chain aborted at {last_child_id}: self-blocked"
    if last_verdict is not None:
        return f"Chain: {len(chain_children)} legs ({arrow}), final verdict {last_verdict}"
    return f"Chain: {len(chain_children)} legs ({arrow})"


def _is_already_terminal(orch: "Orchestrator", task_id: str) -> bool:
    """Shared idempotence predicate for the four decision branches.

    Returns True when the row is gone, already terminal (COMPLETED / FAILED),
    or cancelled — any state where a subsequent decision must not overwrite
    the task's status, note, or spawn children.

    Includes `cancelled_at` explicitly as defense in depth: today `/cancel`
    always flips status to FAILED alongside stamping `cancelled_at`, so the
    `status in TERMINAL_STATES` check covers the cancelled case. But if a
    future code path ever stamps `cancelled_at` without touching status, this
    predicate still does the right thing.

    Closes the cancel-race documented in
    docs/superpowers/specs/2026-05-26-cancel-race-design.md §5.3.
    """
    existing = orch._db.get_task(task_id)
    return (
        existing is None
        or existing.status in TERMINAL_STATES
        or existing.cancelled_at is not None
    )


def _complete(orch: "Orchestrator", task_id: str, *, note: str, output_dir: str | None = None) -> None:
    from datetime import datetime, timezone
    # Idempotence guard: /cancel may have already taken this task to FAILED
    # between Popen return and here. Don't resurrect a cancelled task back to
    # COMPLETED just because the subprocess happened to finish cleanly before
    # SIGTERM arrived.
    if _is_already_terminal(orch, task_id):
        return
    orch._db.update_task(
        task_id,
        status=TaskStatus.COMPLETED,
        block_kind=None,
        note=note,
        final_output_dir=output_dir,
        completed_at=datetime.now(timezone.utc).isoformat(),
    )
    _log_verdict_if_delegated(orch, task_id, success=True)
    orch._update_task_history(task_id)
    _kill_jobs_for_terminating_task(orch, task_id)


def _fail(orch: "Orchestrator", task_id: str, *, note: str) -> None:
    from datetime import datetime, timezone
    # Idempotence guard — same rationale as _complete. When /cancel SIGTERMs
    # the subprocess, run_step re-enters via the post-execution classifier and
    # tries to write a "session failed (rc=-15; ...)" note. That must NOT
    # overwrite the founder's "cancelled by founder: ..." note.
    if _is_already_terminal(orch, task_id):
        return
    # Clear any in-flight chain so the CLI/Web UI doesn't show a chain strip
    # on a FAILED task. The chain can't re-activate (the task is terminal),
    # but the dangling state is cosmetically misleading. Always-clear is
    # cheap and works for cascade-fail, self-blocked, invalid-delegate, and
    # session-failure failure modes.
    orch._db.update_task_active_chain(task_id, None)
    orch._db.update_task(
        task_id,
        status=TaskStatus.FAILED,
        block_kind=None,
        note=note,
        completed_at=datetime.now(timezone.utc).isoformat(),
    )
    _log_verdict_if_delegated(orch, task_id, success=False)
    orch._update_task_history(task_id)
    _kill_jobs_for_terminating_task(orch, task_id)


def _kill_jobs_for_terminating_task(orch: "Orchestrator", task_id: str) -> None:
    """Fire-and-forget: kill all in-flight persistent jobs owned by ``task_id``.

    Called from ``_complete`` and ``_fail`` whenever a task transitions to a
    terminal state. The kill runs out-of-band so task-row progression never
    blocks on job cleanup (5s SIGTERM grace + SIGKILL).

    The DB row update for the killed jobs is a backstop in case the runner's
    own bookkeeping (via ``_KILL_REASON_OVERRIDE``) doesn't get to commit —
    e.g., the run_step thread exits before the runner coroutine finishes its
    final UPDATE. With persistent jobs the runner is its own background task
    and should complete normally; this path matters mostly during shutdown
    races. We use the same loop-detection / daemon-thread fallback as
    ``Orchestrator.notify_failed`` because ``run_step`` runs on a thread-pool
    worker with no event loop of its own.
    """
    db = orch._db
    rows = db._conn.execute(
        "SELECT id, task_id FROM jobs WHERE status='running'"
    ).fetchall()
    inflight_map = {row["id"]: row["task_id"] for row in rows}
    if not any(v == task_id for v in inflight_map.values()):
        return

    from datetime import datetime, timezone

    import asyncio
    import threading

    from src.daemon.jobs_runner import terminate_jobs_for_task

    async def _kill_and_backstop() -> None:
        await terminate_jobs_for_task(task_id, inflight_to_task=inflight_map)
        # Backstop DB update — guarded by status='running' so we don't trample
        # the runner's own terminal write if it got there first.
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        for row_id, row_task in inflight_map.items():
            if row_task == task_id:
                db._conn.execute(
                    "UPDATE jobs SET status='failed', reason='task_ended', "
                    "finished_at=? WHERE id=? AND status='running'",
                    (now, row_id),
                )
        db._conn.commit()

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # No event loop — run_step thread-pool worker. Spawn a daemon thread
        # that owns its own loop.
        threading.Thread(
            target=lambda: asyncio.run(_kill_and_backstop()),
            daemon=True,
        ).start()
    else:
        loop.create_task(_kill_and_backstop())


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
    """Emit the implicit manager review_verdict audit row for a delegated child.

    The team manager is the implicit reviewer of every delegated child:
    a COMPLETED child is an "approved" delegation, a FAILED child is
    "rejected". Audit rows are how the founder reviews which agents need
    attention; they are the canonical record of delegation outcomes.
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


def _advance_chain_for_completed_child(
    *,
    orch: "Orchestrator",
    parent_task_id: str,
    child_task_id: str,
) -> str:
    """Inspect the parent's active_chain against the just-completed child's
    report. Either spawn the next leg ("advance") or clear the chain so the
    caller falls through to the normal parent-wake path ("wake").

    Returns "advance" or "wake". When "advance" is returned, the parent is
    NOT re-enqueued and orchestration_step_count is NOT bumped.

    Only called when child.status == COMPLETED — failed/cancelled children
    cascade-fail as before; chains do NOT survive an opaque leg failure in v1.
    """
    from src.models import TaskRecord
    from src.orchestrator.chain import (
        ChainState,
        build_prior_leg_context,
        compute_advance_action,
    )

    parent = orch._db.get_task(parent_task_id)
    if parent is None or parent.active_chain is None:
        return "wake"

    chain = ChainState.deserialize(parent.active_chain)
    report = orch._db.get_latest_completion_report(child_task_id)
    if report is None:
        orch._db.update_task_active_chain(parent_task_id, None)
        return "wake"

    action = compute_advance_action(chain=chain, report=report)
    if action.kind == "wake":
        orch._db.update_task_active_chain(parent_task_id, None)
        return "wake"

    # Advance: bump chain state FIRST so a crash between this and insert_task
    # leaves a recoverable "stuck blocked-delegated waiting for missing child"
    # rather than a silently-mis-routed chain on the next terminal.
    next_child_id = orch._db.next_task_id()
    chain.step_index = action.next_step_index
    orch._db.update_task_active_chain(parent_task_id, chain.serialize())

    # Now spawn the next-leg child task.
    prior_context = build_prior_leg_context(
        child_task_id=child_task_id, report=report,
    )
    full_brief = action.next_leg.prompt + prior_context
    orch._db.insert_task(
        TaskRecord(
            id=next_child_id,
            team=parent.team,
            brief=full_brief,
            parent_task_id=parent_task_id,
            assigned_agent=action.next_leg.agent,
            status=TaskStatus.PENDING,
            session_timeout_seconds=parent.session_timeout_seconds,
        )
    )

    orch._audit.log_chain_auto_advance(
        parent_task_id=parent_task_id,
        leg_index=action.next_step_index,
        spawned_child_id=next_child_id,
        triggering_child_id=child_task_id,
        triggering_verdict=report.verdict,
        chain_origin_step_audit_id=chain.step_audit_id,
    )
    if orch._queue is not None:
        orch._queue.put_nowait(orch._slug, next_child_id)
    return "advance"


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

    # Chain-advance branch: if the parent has an active chain and the just-
    # completed child terminated cleanly, try to auto-advance to the next leg
    # instead of waking the parent. Failed children skip this branch and fall
    # through to the cascade-fail path below.
    child = orch._db.get_task(task_id)
    if (
        child is not None
        and child.status == TaskStatus.COMPLETED
        and parent.active_chain is not None
    ):
        outcome = _advance_chain_for_completed_child(
            orch=orch, parent_task_id=parent.id, child_task_id=task_id,
        )
        if outcome == "advance":
            return  # next leg spawned; parent stays blocked-delegated
        # outcome == "wake" → chain cleared; fall through to sibling-check
        # + parent-wake path below.

    siblings = [orch._db.get_task(cid) for cid in orch._db.get_children(parent.id)]
    if any(s is None or s.status not in TERMINAL_STATES for s in siblings):
        return

    failed = [s for s in siblings if s.status == TaskStatus.FAILED]
    if failed:
        first = failed[0]
        note = f"delegated child {first.id} failed: {first.note or '(no note)'}"
        if parent.active_chain is not None:
            orch._db.update_task_active_chain(parent.id, None)
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
        _maybe_post_thread_followup(
            orch, parent.id,
            status=TaskStatus.FAILED, auto_revisit_spawned=root_auto_revisit_spawned,
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
        agent exited clean without invoking ``happyranch report-completion``).
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


def _maybe_resume_blocked_task(
    orch: "Orchestrator",
    task_id: str,
    *,
    trigger: str,
    triggering_job_id: str | None,
) -> bool:
    """Check predicate (all blocking jobs terminal) and enqueue if satisfied.

    READ-ONLY: does NOT mutate task state. The state transition happens at
    run_step_impl step 3's CAS when the worker picks up the enqueued task.

    Returns True if it enqueued; False otherwise. Idempotent — extra enqueues
    are harmless (run_step_impl's CAS admits exactly one).

    Spec: docs/superpowers/specs/2026-05-28-task-blocked-by-job-design.md §5.4
    """
    import json as _json

    db = orch._db
    audit = orch._audit
    task = db.get_task(task_id)
    if task is None:
        return False
    if task.status != TaskStatus.BLOCKED or task.block_kind != BlockKind.BLOCKED_ON_JOB:
        return False  # silent — steady state

    try:
        job_ids = _json.loads(task.blocked_on_job_ids or "[]")
    except _json.JSONDecodeError:
        audit.log_task_resume_skipped(
            task_id=task_id, reason="empty_job_list",
            blocked_on_job_ids_raw=task.blocked_on_job_ids,
        )
        return False
    if not job_ids:
        audit.log_task_resume_skipped(
            task_id=task_id, reason="empty_job_list",
            blocked_on_job_ids_raw=task.blocked_on_job_ids,
        )
        return False

    _TERMINAL = {"completed", "failed", "rejected"}
    for jid in job_ids:
        if db.get_job_status(jid) not in _TERMINAL:
            return False  # silent — common steady state

    # All terminal — enqueue.
    queue = getattr(orch, "_queue", None)
    if queue is not None:
        queue.enqueue(
            orch._slug, task_id,
            metadata={"trigger": trigger, "triggering_job_id": triggering_job_id},
        )
    return True


def _maybe_post_thread_followup(
    orch: "Orchestrator",
    task_id: str,
    *,
    status: TaskStatus,
    auto_revisit_spawned: bool,
) -> None:
    """Post a task-followup system message + mint a re-invocation for the dispatcher.

    Fire predicate (spec §4):
      - status == COMPLETED                                → always fire
      - status == FAILED and not auto_revisit_spawned      → true terminal, fire
      - status == FAILED and auto_revisit_spawned          → no-op (revisit chain
                                                             will call this helper
                                                             again at its terminal)

    Only root tasks fire. Child terminals cascade-fail through the parent,
    which re-enters this helper there. The originating thread is found by
    walking ``walk_revisit_chain`` backward to the earliest predecessor and
    reading ``dispatched_from_thread_id`` off that row.

    Spec: docs/superpowers/specs/2026-05-28-thread-task-followup-design.md §4-§6
    """
    import json as _json

    # Predicate gate — first pass using caller's claim (cheap early-out).
    if status == TaskStatus.FAILED and auto_revisit_spawned:
        return
    if status not in (TaskStatus.COMPLETED, TaskStatus.FAILED):
        return

    db = orch._db
    audit = orch._audit
    terminal_task = db.get_task(task_id)
    if terminal_task is None:
        return

    # Re-read the persisted status. Site D's caller passes COMPLETED, but
    # /cancel may have raced past Guard B and flipped the row to
    # failed+cancelled_at; _complete() short-circuits in that race, leaving
    # the row at FAILED. Trust the DB, not the caller's claim.
    actual_status = terminal_task.status
    if actual_status not in (TaskStatus.COMPLETED, TaskStatus.FAILED):
        # Row isn't terminal yet — caller raced ahead of the DB write.
        # Bail; the eventual real terminal will re-enter this helper.
        return
    if actual_status == TaskStatus.FAILED and auto_revisit_spawned:
        return

    # Only root tasks fire. Children are handled at their parent's terminal site.
    if terminal_task.parent_task_id is not None:
        return

    # Find the original dispatched root via the revisit chain.
    # walk_revisit_chain returns [task, predecessor, ..., original].
    # Use a larger hop bound (200) consistent with _count_prior_auto_revisits_by_kind,
    # and handle LineageTooDeep defensively rather than crashing and silently
    # discarding the followup without any audit trail.
    from src.infrastructure.database import LineageTooDeep  # local: avoid cycle
    try:
        chain = db.walk_revisit_chain(task_id, max_hops=200)
    except LineageTooDeep:
        audit.log_thread_followup_skipped(
            "(unresolved)", original_task_id=task_id, terminal_task_id=task_id,
            reason="chain_too_deep",
        )
        return
    original = chain[-1] if chain else terminal_task
    thread_id = original.dispatched_from_thread_id
    if thread_id is None:
        # Not a thread-dispatched chain; silent no-op (no audit).
        return

    # Thread-state guard.
    thread = db.get_thread(thread_id)
    if thread is None:
        audit.log_thread_followup_skipped(
            thread_id, original_task_id=original.id, terminal_task_id=task_id,
            reason="thread_not_open", thread_status="missing",
            task_status=status.value,
        )
        return
    from src.models import ThreadStatus as _ThreadStatus
    if thread.status is not _ThreadStatus.OPEN:
        audit.log_thread_followup_skipped(
            thread_id, original_task_id=original.id, terminal_task_id=task_id,
            reason="thread_not_open",
            thread_status=thread.status.value,
            task_status=status.value,
        )
        return

    # Dispatcher identity: read from the thread_dispatch audit row on the
    # ORIGINAL task (revisit roots don't have their own dispatch row).
    dispatch_rows = [
        r for r in db.get_audit_logs(thread_id)
        if r["action"] == "thread_dispatch"
        and _payload_dict(r).get("task_id") == original.id
    ]
    if not dispatch_rows:
        audit.log_thread_followup_skipped(
            thread_id, original_task_id=original.id, terminal_task_id=task_id,
            reason="dispatcher_unresolved",
        )
        return
    dispatcher = _payload_dict(dispatch_rows[0])["dispatcher"]

    # Build system payload using DB-actual status (not the caller's claim) so
    # a cancel race at Site D doesn't emit task_completed for a FAILED row.
    kind_tag = "task_completed" if actual_status == TaskStatus.COMPLETED else "task_failed"
    system_payload = {
        "kind_tag": kind_tag,
        "task_id": task_id,
        "original_task_id": original.id,
        "root_task_id": original.id,
        "status": actual_status.value,
        "final_output_summary": terminal_task.note or "",
        "final_output_dir": terminal_task.final_output_dir,
        "cancelled": terminal_task.cancelled_at is not None,
        "revisit_chain_length": len(chain) if chain else 1,
    }

    # Append system message (separate from the atomic cap+mint below — the
    # system message ordering relative to concurrent system messages is not
    # part of the atomicity invariant we're protecting).
    from src.models import ThreadMessageKind as _TMK, ThreadInvocationPurpose as _TIP
    sys_seq = db.append_thread_message(
        thread_id=thread_id, speaker=dispatcher,
        kind=_TMK.SYSTEM,
        system_payload=system_payload,
    )

    # Atomic cap-projection + conditional bump + mint.  Closes the TOCTOU race
    # where two concurrent root completions on the same thread both read the
    # same pending count, both skip the bump, both mint, and leave the thread
    # with more obligations than turn_cap.  The @_synchronized RLock on
    # mint_followup_invocation_with_cap_extend serializes all three steps.
    inv, new_cap = db.mint_followup_invocation_with_cap_extend(
        thread_id=thread_id,
        agent_name=dispatcher,
        triggering_seq=sys_seq,
    )
    if new_cap is not None:
        audit.log_thread_turn_cap_auto_extended(
            thread_id, original_task_id=original.id,
            reason="task_followup", new_cap=new_cap,
        )
    audit.log_thread_task_followup_enqueued(
        thread_id, original_task_id=original.id, terminal_task_id=task_id,
        dispatcher=dispatcher, invocation_token=inv.invocation_token,
    )

    # Enqueue onto the org's thread queue. The queue is bound to the daemon's
    # main event loop, but run_step runs on a worker thread, so we cross the
    # loop boundary via run_coroutine_threadsafe — same pattern as
    # `_start_feishu_listeners` uses for cross-thread async bridging.
    import asyncio as _asyncio
    from src.daemon.thread_queue import ThreadJob as _ThreadJob
    thread_queue = getattr(orch, "_thread_queue", None)
    main_loop = getattr(orch, "_main_loop", None)
    if thread_queue is not None and main_loop is not None:
        try:
            _asyncio.run_coroutine_threadsafe(
                thread_queue.put(_ThreadJob(
                    org_slug=orch._slug,
                    invocation_token=inv.invocation_token,
                )),
                main_loop,
            )
        except Exception as exc:
            audit.log_thread_followup_skipped(
                thread_id, original_task_id=original.id, terminal_task_id=task_id,
                reason="enqueue_failed", detail=str(exc),
            )
    else:
        # Defence: queue or loop not yet wired (e.g., test orchestrator constructed
        # without daemon context). Invocation stays PENDING; audit so the
        # operator can detect it if needed. In production this path is never
        # taken because _lifespan always calls _attach_thread_queue_wiring before
        # the first task step runs.
        audit.log_thread_followup_skipped(
            thread_id, original_task_id=original.id, terminal_task_id=task_id,
            reason="enqueue_unavailable",
        )


def _payload_dict(row: dict) -> dict:
    """Coerce an audit row's ``payload`` field to a dict."""
    import json as _json
    p = row.get("payload")
    if p is None:
        return {}
    if isinstance(p, dict):
        return p
    return _json.loads(p)


def _session_failed_note(result, report) -> str:
    """Build an enriched `agent session failed` note.

    The pre-TASK-045 version wrote a bare constant string, so when the
    Claude subprocess finished without calling `happyranch report-completion`
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
