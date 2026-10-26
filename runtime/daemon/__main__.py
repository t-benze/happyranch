"""HappyRanch daemon entry point.

Bootstraps from ~/.happyranch/runtimes.yaml, binds an ephemeral local port,
writes pid/port files, and runs the FastAPI app under uvicorn.

Offline maintenance mode (TASK-5443 replacement, TASK-5505):
``python -m runtime.daemon --maintenance`` is an explicit STARTUP-ONLY
one-shot that runs the MetricsStore maintenance sequence and exits — it
never binds an HTTP listener, never runs the FastAPI lifespan, and never
starts a scheduler/worker.  Run it while the daemon is stopped; the SQLite
layer fail-closes (checkpoint busy / VACUUM locked) if a live daemon holds
the store.  Every failure path (including daemon-home initialization and
stale/malformed runtime state) returns 1 with a stable bounded classification
and fixed recovery guidance — never raw exception text, tracebacks, paths, or
injected content.
"""
from __future__ import annotations

import argparse
import logging
import os
import signal
import socket
import sys
from datetime import datetime, timezone
from types import FrameType
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from runtime.daemon.startup_recovery import RecoveryOutcome, StartupRecovery
    from runtime.daemon.org_state import OrgState

import uvicorn

from runtime.config import Settings
from runtime.daemon import paths, runtimes
from runtime.daemon.app import create_app
from runtime.daemon.queue import TaskQueue
from runtime.daemon.state import DaemonState
from runtime.infrastructure.audit_logger import AuditLogger
from runtime.infrastructure.database import Database
from runtime.models import BlockKind, TaskStatus
from runtime.orchestrator.active_authority_policy import (
    is_eligible_policy_manager,
)
from runtime.orchestrator.orchestrator import (
    Orchestrator,
    completion_report_from_result_row,
)
from runtime.runtime import RuntimeDir

logger = logging.getLogger("happyranch.daemon")


def _sweep_enqueue(
    queue: TaskQueue, slug: str, task_id: str,
    orchestrator: Orchestrator | None,
    *, pending_once: bool = False,
) -> None:
    """Startup-sweep enqueue through the common DB-aware boundary (C3d4a).

    When the orchestrator/DB is available the target root's durable v2
    generation is resolved at production time, so a ``pending(G)`` dispatch is
    published rather than emitted as an untagged fallback.  No orchestrator means
    there is no durable classifier to consult and the unchanged ordinary enqueue
    is preserved.
    """
    publication = None
    if orchestrator is not None and orchestrator._startup_state is not None:
        from runtime.daemon.startup_publication import _TaskPublication
        from runtime.orchestrator.run_step import _startup_sweep_owner
        publication = _TaskPublication(orchestrator._startup_recovery, orchestrator,
            task_id, "sweep", sweep_owner=_startup_sweep_owner(orchestrator))
    kwargs = {"_publication": publication} if publication is not None else {}
    def publish() -> None:
        if orchestrator is None:
            queue.enqueue(slug, task_id)
            return
        from runtime.orchestrator.authority import enqueue_task_generation_aware
        enqueue_task_generation_aware(orchestrator, queue, slug, task_id, **kwargs)

    if pending_once:
        queue.enqueue_if_absent(slug, task_id, publisher=publish, **kwargs)
    else:
        publish()


async def discover_completed_workflow_handoffs(state: DaemonState) -> None:
    """Completed source discovery on the existing lifespan loop before workers."""
    import logging
    for org in state.orgs.values():
        try:
            await org.workflow_submissions.discover_completed_handoffs(state.queue)
        except Exception:
            # A held source remains durable; it is never coerced or relaunched.
            logging.getLogger(__name__).exception('completed workflow handoff remains pending for %s', org.slug)


def discover_completed_workflow_handoffs_native(state: DaemonState, recovery: StartupRecovery) -> RecoveryOutcome:
    """P3 completed-document coordinator, called once by the native boot owner.

    The caller owes P2 and original v2 generation publication first. This entry
    owns only the workflows phase and never binds queues or releases readiness.
    The original native lifecycle invokes it once after all org sweeps.
    """
    from runtime.daemon.startup_recovery import RecoveryOutcome, StartupRecovery
    if not isinstance(recovery, StartupRecovery) or state is not recovery.state:
        raise RuntimeError('startup_workflow_state_owner_unavailable')
    held = False
    # phase() reserves before discovery, forbids repeat invocation in this epoch
    # and records the first original BaseException instead of logging success.
    with recovery.phase('workflows') as settle:
        state._assert_attachments_unchanged(recovery._attachments)
        for org in state.orgs.values():
            recovery.require_workflow_owner(org)
            # Invoke each existing selection once, in its original order. Read
            # outcomes immediately: tuple construction would run later producers
            # even after an earlier unknown/negative result.
            discoveries = (
                lambda: org.workflow_submissions.discover_completed_handoffs_native(state.queue, recovery),
                lambda: org.workflow_revisions.discover_native(state.queue, recovery),
                lambda: org.workflow_review_receipts.discover_completed_native(recovery),
                lambda: org.workflow_review_receipts.discover_collections_native(recovery),
            )
            for index, discover in enumerate(discoveries):
                recovery.require_workflow_owner(org)
                outcome = discover()
                if not isinstance(outcome, RecoveryOutcome) or not outcome.positive:
                    code = outcome.code if isinstance(outcome, RecoveryOutcome) else None
                    raise RuntimeError(code or 'startup_workflow_outcome_unavailable')
                held |= outcome.disposition == 'local_hold'
                recovery.require_workflow_owner(org)
                if index == 0 and outcome.code == 'startup_handoff_family_absent':
                    # Preserve the original entry's absent-H early return.
                    # P0 already validated the complete optional-family layout.
                    break
        state._assert_attachments_unchanged(recovery._attachments)
        outcome = RecoveryOutcome('local_hold' if held else 'settled')
        settle(outcome)
    return outcome


def bind_returned_native_on_startup(state: DaemonState, recovery: StartupRecovery) -> RecoveryOutcome:
    """Own P4 once: positively bind the real loop, then notify actual returns.

    The original lifecycle calls this entry once; dynamic attachment owns
    its separate explicit return obligation after the same gate is ready.
    """
    from runtime.daemon.startup_recovery import StartupRecovery
    from runtime.workflows.recovery import rediscover_returned
    if not isinstance(recovery, StartupRecovery) or state is not recovery.state:
        raise RuntimeError("startup_returned_state_owner_unavailable")
    with recovery.phase("returned") as settle:
        recovery.bind_returned_references()
        for org in state.orgs.values():
            recovery.require_returned_owner(org)
            coding = getattr(org.orchestrator, "_workflow_coding_dispatch", None)
            if coding is None:
                recovery.count_phase("returned", "coding_absent")
            else:
                if coding.org is not org or coding.db is not org.db:
                    raise RuntimeError("startup_returned_dispatcher_changed")
                # notify_returned already owns its per-selection observer and
                # drains outside DB/marker locks; do not nest that observer.
                with recovery.publications(org.orchestrator):
                    rediscover_returned(coding)
            recovery.require_returned_owner(org)
            recovery.count_phase("returned", "orgs_observed")
        outcome = recovery.publication_outcome()
        settle(outcome)
    return outcome


def compatibility_native_on_startup(state: DaemonState, recovery: StartupRecovery) -> RecoveryOutcome:
    """Own P5 once using exactly the shared legacy compatibility bodies."""
    from runtime.daemon.startup_recovery import RecoveryOutcome, StartupRecovery
    from runtime.daemon.app import recover_startup_compatibility
    if not isinstance(recovery, StartupRecovery) or state is not recovery.state:
        raise RuntimeError("startup_compatibility_state_owner_unavailable")
    with recovery.phase("compatibility") as settle:
        recovery.require_compatibility_owner()
        outcome = recover_startup_compatibility(state, recovery=recovery)
        if not isinstance(outcome, RecoveryOutcome) or not outcome.positive:
            raise RuntimeError("startup_compatibility_outcome_unavailable")
        recovery.require_compatibility_owner()
        settle(outcome)
    return outcome


def recover_producers_native_on_startup(state: DaemonState, recovery: StartupRecovery) -> RecoveryOutcome:
    """Own P7 once after P5/P6; no scheduler, consumer or job is started."""
    from runtime.daemon.startup_recovery import RecoveryOutcome, StartupRecovery
    from runtime.daemon.app import recover_startup_dreams, recover_startup_wakes, recover_startup_schedules
    if not isinstance(recovery, StartupRecovery) or state is not recovery.state:
        raise RuntimeError("startup_producers_state_owner_unavailable")
    held = False
    with recovery.phase("producers") as settle:
        # Each family retains its original all-org order and actual store;
        # inspect its result BEFORE invoking the next original family.
        for recover in (recover_startup_dreams, recover_startup_wakes, recover_startup_schedules):
            recovery.require_producers_owner()
            result = recover(state, recovery=recovery)
            if not isinstance(result, RecoveryOutcome) or not result.positive:
                raise RuntimeError("startup_producers_outcome_unavailable")
            held |= result.disposition == "local_hold"
            recovery.require_producers_owner()
        outcome = RecoveryOutcome("local_hold" if held else "settled")
        settle(outcome)
    return outcome


def redeliver_native_on_startup(state: DaemonState, recovery: StartupRecovery) -> RecoveryOutcome:
    """One callable P6 seam, after P4 actual-loop binding and P5 compatibility.

    The original lifecycle calls this entry once. It neither binds references
    nor activates consumers, and ordinary post-ready producers stay separate.
    """
    from runtime.daemon.startup_recovery import RecoveryOutcome, StartupRecovery
    from runtime.orchestrator.run_step import _maybe_resume_blocked_task, _StartupSweepOutcome
    from runtime.infrastructure.team_move_store import validate_team_move_schema
    from runtime.orchestrator.team_move_operation import TeamMoveOperation
    from runtime.orchestrator.task_pause import discover_pause_work_native
    from runtime.daemon.thread_queue import ThreadJob
    if not isinstance(recovery, StartupRecovery) or state is not recovery.state:
        raise RuntimeError("startup_redelivery_state_owner_unavailable")
    held = False
    with recovery.phase("redelivery") as settle:
        state._assert_attachments_unchanged(recovery._attachments)
        # Retain original all-org blocked -> move -> Pause -> token order.
        for org in state.orgs.values():
            recovery.require_redelivery_owner(org)
            with recovery.publications(org.orchestrator):
                for task_id in org.db.list_tasks_blocked_on_jobs():
                    _maybe_resume_blocked_task(org.orchestrator, task_id,
                        trigger="startup_recovery", triggering_job_id=None)
        for org in state.orgs.values():
            recovery.require_redelivery_owner(org)
            with org.db._lock:
                installed = validate_team_move_schema(org.db._conn, org_slug=org.slug)
                completed_move = installed and org.db._conn.execute(
                    "SELECT 1 FROM team_move_operations WHERE phase='complete' LIMIT 1"
                ).fetchone() is not None
            if completed_move:
                if state.profile_coordinator is None:
                    raise RuntimeError("startup_move_profile_owner_unavailable")
                with recovery.publications(org.orchestrator):
                    TeamMoveOperation(org, state.profile_coordinator).redeliver_native(recovery)
        for org in state.orgs.values():
            recovery.require_redelivery_owner(org)
            with recovery.publications(org.orchestrator):
                outcome = discover_pause_work_native(org, state.queue, recovery)
                if not isinstance(outcome, RecoveryOutcome) or not outcome.positive:
                    raise RuntimeError("startup_pause_outcome_unavailable")
                held |= outcome.disposition == "local_hold"
        for org in state.orgs.values():
            recovery.require_redelivery_owner(org)
            with recovery.publications(org.orchestrator) as publication:
                # Missing P2 completion is not an empty successful token set.
                tokens = getattr(org, "_startup_recovered_thread_tokens", None)
                if (not isinstance(tokens, _StartupSweepOutcome)
                        or tokens.disposition not in ("settled", "local_hold")
                        or tokens.first_cause is not None or tuple(tokens) != tokens.tokens
                        or any(type(token) is not str or not token for token in tokens)):
                    raise RuntimeError("startup_recovered_thread_tokens_unavailable")
                for token in tokens:
                    publication.make_room(org.orchestrator)
                    publication.submit_thread(org.orchestrator, org.thread_queue,
                        recovery._loop, ThreadJob(org_slug=org.slug, invocation_token=token))
            recovery.require_redelivery_owner(org)
        state._assert_attachments_unchanged(recovery._attachments)
        outcome = recovery.publication_outcome()
        if held and outcome.disposition == "settled":
            outcome = RecoveryOutcome("local_hold")
        settle(outcome)
    return outcome


def _sweep_on_startup(
    db: Database, queue: TaskQueue, slug: str,
    orchestrator: Orchestrator | None = None,
) -> list[str]:
    """Post-restart recovery for a single org (Path B — THR-037 Change B).

    Under Path B ``in_progress`` is two-valued, discriminated by ``block_kind``:
    a NULL discriminant means a subprocess was running (killed by the restart);
    a non-NULL discriminant (delegated/blocked_on_job) means the task was
    *parked* with no subprocess. Branching on ``status`` alone — as the pre-Path-B
    sweep did — would force-fail every parked parent and every blocked-on-job
    task on each restart (silent cascade corruption). The discriminant is what
    saves it:

      - Branch 1 — in_progress + block_kind IS NULL → pid-liveness probe
        (THR-079). Instead of assuming the subprocess died, the sweep reads
        the persisted ``executor_pid`` and probes with ``os.kill(pid, 0)``:
        * pid ALIVE → leave alone (session survived the daemon restart).
        * pid DEAD (ProcessLookupError) → ``FAILED`` with reason
          "session died on daemon restart — executor pid not alive".
        * pid NULL/undeterminable (PermissionError, etc.) → ``FAILED``
          with reason "session liveness undeterminable on daemon restart".
        No auto-revisit is spawned — the founder receives a
        ``daemon_restart_failure`` audit row and decides whether to
        re-dispatch. NOTE: a recycled pid could read as falsely-alive;
        the probe is the ratified THR-079 approach.
      - Branch 2 — in_progress + block_kind=DELEGATED with all children terminal
        → re-enqueue parent (orphaned wake-up: the daemon died after a child
        terminated but before the parent saw the signal). Else leave (children
        still live).
      - Branch 3 — in_progress + block_kind=BLOCKED_ON_JOB with all jobs terminal
        → re-enqueue (orphaned wake-up: jobs finished while the daemon was down).
        Else leave alone. This branch MUST exist: without it a parked-on-job
        task falls into Branch 1 and is wrongly failed on every restart.
      - Branch 4 — pending rows → re-enqueue (lost the original POST enqueue).
      - Branch 5 — escalated → leave alone (founder owns these); mirrors the
        pre-Path-B blocked(ESCALATED) fall-through.

    Phase 3: only in_progress(...) shapes are accepted; the boot migration
    flips any legacy blocked(...) rows before the sweep runs.

    When ``orchestrator`` is None (test harnesses that don't construct one),
    Branch 1 degrades to liveness-probe-and-mark-failed only; no cascade
    notification to parent. Production always passes an orchestrator.

    Returns the list of reply-delivery invocation tokens that must be
    re-enqueued onto the thread queue after commit (retained valid queued
    wakes and daemon_restart replacements from interrupted running replies);
    the caller enqueues them once the event loop is live.
    """
    # Imported lazily to avoid a startup-time cycle (run_step → daemon types).
    from runtime.orchestrator.run_step import (
        _capture_root_worktree_transition,
        _RootWorktreeTransitions, _ROOT_WORKTREE_OWNER_EVENTS,
        _StartupSweepOwner,
    )
    from runtime.infrastructure.db.authority_v2_attempts import _V2StartupConnectionUnsafe
    root_transitions = _RootWorktreeTransitions()
    root_owner = _StartupSweepOwner(orchestrator, root_transitions)
    root_delivery = root_owner.delivery
    recovery_gate = getattr(orchestrator, '_startup_recovery', None)
    if recovery_gate is not None:
        recovery_gate.retain_sweep(root_owner)
    root_token = _ROOT_WORKTREE_OWNER_EVENTS.set(root_delivery)
    try:
        root_owner.check()
        # A full sweep may commit in Pause/workflow/thread/batch consumers.
        # Reject caller-owned or unknown connections before reaching any of
        # them; this entry observation never settles or repairs caller work.
        startup_connection = db._authority_policy_v2_startup_reads()
        startup_connection.require_sweep_entry()
        from runtime.orchestrator.run_step import (
            TERMINAL_STATES,
            _enqueue_parent_if_waiting,
        )

        if orchestrator is not None:
            from runtime.orchestrator.task_pause import restore_pause_preparations
            restore_pause_preparations(orchestrator)
        startup_connection.require_sweep_entry()
        audit = AuditLogger(db)

        import json as _json
        # Path B: parked carriers are in_progress(...) with block_kind set.
        # A live subprocess is in_progress + block_kind IS NULL (Branch 1).
        _PARKED = {TaskStatus.IN_PROGRESS}
        _TERMINAL_JOB_STATES = {"completed", "failed", "rejected"}

        # THR-229: pre-final v2 attempt ownership precedes EVERY task-specific
        # startup branch.  Interrupted old-boot/failed-stage attempts receive only
        # exact refusal housekeeping; a live same-boot owner and any failed
        # refusal transaction retain their prior residue and are fenced from
        # accepted-recovery, pid failure and Pending enqueue effects in this pass.
        from runtime.orchestrator.authority import (
            refuse_authority_policy_v2_pre_final_on_startup,
        )
        v2_pre_final_roots = refuse_authority_policy_v2_pre_final_on_startup(
            db, orchestrator=orchestrator,
        )
        v2_discovery_unavailable = v2_pre_final_roots is None
        if v2_discovery_unavailable:
            # This normal-return negative is NOT an empty successful scan.
            # Retain the source-local branch fences; the owner cannot activate
            # or attempt a final cleanup batch on this observation.
            root_owner.negative('startup_v2_discovery_unavailable')
            root_owner.raise_if_failed()

        # Accepted recovery callbacks whose effects committed just before a crash
        # may now be terminal.  Include only the ledger's exact current-owner
        # bindings: a cancelled or newer generation must remain outside this
        # reconciliation and follow its own lifecycle.
        selected_recovery_owners = {
            owner["task_id"]: owner
            for owner in db.get_consumed_task_completion_recovery_owners()
        }
        consumed_parent_cleanup_ids = {
            task_id for task_id, owner in selected_recovery_owners.items()
            if owner["parent_task_id"] is not None
        }
        task_ids = dict.fromkeys([
            *db.get_accepted_task_completion_recovery_task_ids(),
            *selected_recovery_owners,
            *consumed_parent_cleanup_ids,
            *db.get_nonterminal_task_ids(),
        ])
        for task_id in task_ids:
            root_owner.check()
            t = db.get_task(task_id)
            if t is None:
                continue
            from runtime.orchestrator.task_pause import pause_deferred_owner
            if pause_deferred_owner(db, task_id):
                continue
            from runtime.workflows.recovery import recover_owned_task
            if recover_owned_task(db, queue, slug, task_id, orchestrator=orchestrator,
                                  startup_owner=root_owner):
                continue
            if v2_discovery_unavailable or task_id in v2_pre_final_roots:
                root_owner.local_hold()
                continue

            # A completed leaf receipt can commit immediately before its ordinary
            # post-commit job cleanup.  Restart has no inherited live PID/control,
            # so reuse the existing ownership-aware cleanup rather than signalling
            # a persisted PID; terminal-child handling below reconstructs its wake.
            selected_owner = selected_recovery_owners.get(task_id)
            if selected_owner is not None and orchestrator is not None:
                try:
                    human_recovery = orchestrator.teams.manager_for_team(t.team).kind == 'human'
                except KeyError:
                    human_recovery = False
                if (human_recovery and t.task_type == "subtask"
                        and selected_owner["status"] == TaskStatus.FAILED.value):
                    from runtime.orchestrator.run_step import _submit_human_failed_recovery
                    _submit_human_failed_recovery(orchestrator, task_id, selected_owner["agent"],
                        selected_owner["recovery_session_id"], selected_owner["accepted_result_id"])
                    continue
                # The task/ledger ownership is already durable, but the ordinary
                # cleanup helper below is deliberately asynchronous.  Record its
                # owned-job backstop before returning to the lifespan, whose
                # generic orphan scan otherwise can win the gap and permanently
                # label this exact recovery-owned job ``daemon_crash``.  This
                # transaction neither waits for nor signals a persisted PID; the
                # later helper retains live-control termination when one exists.
                # Restrict the eager settlement to the exact recovery-owned
                # terminal rows selected above, so unrelated terminal/generic
                # orphan behavior is unchanged.
                # Revalidate the selector's full durable fingerprint at each
                # effect boundary.  A replacement/cancellation after selection
                # must perform no stale backstop, live cleanup, or parent wake.
                # The guarded update itself rechecks the same fingerprint; its
                # rowcount may be zero when no owned job remains running.
                recovery_owner = (
                    selected_owner["agent"], selected_owner["recovery_session_id"],
                    selected_owner["accepted_result_id"], selected_owner["status"],
                )
                recovery_job_ids = db.settle_consumed_task_completion_recovery_jobs(
                    task_id=task_id, agent=recovery_owner[0],
                    recovery_session_id=recovery_owner[1], result_row_id=recovery_owner[2],
                    terminal_status=recovery_owner[3],
                    finished_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                )
                if recovery_job_ids is None:
                    continue
                from runtime.orchestrator.run_step import _kill_jobs_for_terminating_task
                _kill_jobs_for_terminating_task(
                    orchestrator, task_id, recovery_owner=recovery_owner,
                    recovery_job_ids=recovery_job_ids,
                    after_recovery_cleanup=(
                        lambda task_id=task_id: _enqueue_parent_if_waiting(
                            orchestrator, task_id, root_auto_revisit_spawned=False,
                        ) if task_id in consumed_parent_cleanup_ids else None
                    ),
                )

            # THR-247 recovery is not an ordinary THR-079 subprocess.  Its claim
            # is durably spent before launch/PID publication, so a restart must
            # settle it before the ordinary pid-alive leave-alone path.  Never use
            # a persisted PID here: without a live generation-owned control it may
            # be absent or recycled.  The transaction protects accepted callbacks,
            # cancellation, and a newer durable owner; those winners are left
            # wholly untouched for their own normal lifecycle path.
            if t.assigned_agent is not None:
                accepted_result = db.get_accepted_task_completion_recovery_result(
                    task_id=task_id, agent=t.assigned_agent,
                )
                if accepted_result is not None:
                    # An accepted recovery callback is authoritative even if a
                    # stale PID happens to look alive after restart.  The ledger
                    # selects this immutable row; never substitute a latest row.
                    if orchestrator is not None:
                        accepted_report = completion_report_from_result_row(
                            task_id, accepted_result, fallback_agent=t.assigned_agent,
                        )
                        from runtime.orchestrator.run_step import _consume_accepted_completion_recovery
                        _consume_accepted_completion_recovery(
                            orchestrator, task_id, accepted_report,
                            agent=t.assigned_agent, session_id=t.current_session_id or "",
                            result_row_id=accepted_result["id"],
                        )
                    continue
                recovery = db.get_claimed_task_completion_recovery(
                    task_id=task_id, agent=t.assigned_agent,
                )
                if recovery is not None:
                    settled_at = datetime.now(timezone.utc).isoformat()
                    with _capture_root_worktree_transition(orchestrator, task_id, '_sweep_on_startup.recovery', root_transitions):
                        settled = db.settle_interrupted_task_completion_recovery(
                            task_id=task_id, agent=t.assigned_agent,
                            settled_at=settled_at,
                            note=(
                                "unaccepted completion recovery interrupted by daemon restart; "
                                "recovery claim spent and settled fail-closed"
                            ),
                        )
                    if settled:
                        audit.log_daemon_restart_failure(task_id, t.assigned_agent)
                        if orchestrator is not None:
                            from runtime.orchestrator.run_step import _kill_jobs_for_terminating_task
                            _kill_jobs_for_terminating_task(orchestrator, task_id)
                            _enqueue_parent_if_waiting(
                                orchestrator, task_id,
                                root_auto_revisit_spawned=False,
                            )
                    # A claimed recovery must never fall through to PID liveness:
                    # if settlement lost, an accepted/cancelled/newer owner won.
                    continue

            # Branch 1 — genuinely running, killed by the restart.
            if t.status == TaskStatus.IN_PROGRESS and t.block_kind is None:
                # THR-079: use the persisted executor OS pid as the liveness
                # signal instead of assuming the subprocess is dead. A running
                # session survives a daemon restart; only genuinely dead pids
                # (or undeterminable ones, per fail-closed default) are failed.
                # NOTE: os.kill(pid, 0) carries a pid-recycle caveat — a recycled
                # pid could read as falsely-alive. The probe is the ratified
                # THR-079 approach; a falsely-alive false-positive is acceptable
                # relative to the risk of duplicate runs from false-negative.
                pid = t.executor_pid
                alive = False
                if pid is not None:
                    try:
                        os.kill(pid, 0)  # signal 0 = existence check, no signal sent
                    except ProcessLookupError:
                        alive = False
                    except Exception:
                        # PermissionError, recycled-pid uncertainty, any
                        # non-clean answer → founder fail-closed default.
                        # Do NOT leave-alone and do NOT auto-resume on ambiguity.
                        pid = None  # treat as undeterminable
                    else:
                        alive = True

                if alive:
                    # Session still running — leave alone. No reconcile, no
                    # re-enqueue, no auto-revisit. The live session will complete
                    # its work and report back normally.
                    continue

                # THR-090 Track A: before failing a dead-pid task, check for an
                # unconsumed task_result row from the CURRENT session (the
                # definitive TASK-2625 fingerprint: a completion callback that
                # landed after the daemon died). Session-scoping is mandatory:
                # a prior-step result row carries a different session uuid and
                # must never match — the task falls through to the dead-pid FAIL
                # path instead. Governing invariant: err toward a MISS
                # (fail-closed), NEVER replay an already-consumed decision.
                # Only act if current_session_id is not None AND the row is found;
                # otherwise fall through unchanged to the dead-pid FAIL path.
                orphaned_result_row = None
                if t.current_session_id is not None and t.assigned_agent is not None:
                    orphaned_result_row = db.get_latest_task_result(
                        task_id, t.assigned_agent, t.current_session_id,
                    )
                if orphaned_result_row is not None and orchestrator is not None:
                    orphaned_report = completion_report_from_result_row(
                        task_id, orphaned_result_row,
                        fallback_agent=t.assigned_agent or "unknown",
                    )
                    # Audit: log the completion report so the consumed result is
                    # visible — the original session's log_completion_report call
                    # never ran (the daemon died before that point).
                    orchestrator._audit.log_completion_report(report=orphaned_report)
                    from runtime.orchestrator.run_step import _consume_completion_report
                    outcome = _consume_completion_report(
                        orchestrator, task_id, orphaned_report,
                        result_row_id=orphaned_result_row.get("id"),
                        reclaim_terminal_worktree=False,
                    )
                    if isinstance(outcome, str):
                        # This is a selected human consumer's normal negative,
                        # not pre-selection non-applicability or empty success.
                        root_owner.negative('startup_completion_negative_outcome')
                    root_owner.raise_if_failed()
                    continue

                # Dead or undeterminable (no orphaned result to consume):
                # fail-closed. No auto-revisit spawn — the THR-079 ruling
                # supersedes the earlier heartbeat/revisit approach. The founder
                # receives a daemon_restart_failure audit row and decides whether
                # to re-dispatch.
                if pid is None:
                    reason = (
                        "session liveness undeterminable on daemon restart -- "
                        "executor pid null or probe inconclusive"
                    )
                else:
                    reason = (
                        "session died on daemon restart -- executor pid not alive"
                    )
                with _capture_root_worktree_transition(orchestrator, task_id, '_sweep_on_startup.failure', root_transitions):
                    db.update_task(task_id, status=TaskStatus.FAILED, note=reason)
                audit.log_daemon_restart_failure(task_id, t.assigned_agent or "daemon")
                if orchestrator is not None:
                    _enqueue_parent_if_waiting(
                        orchestrator, task_id,
                        root_auto_revisit_spawned=False,
                    )
                    from runtime.orchestrator.run_step import (
                        _reclaim_terminal_task_worktree,
                    )
                    _reclaim_terminal_task_worktree(orchestrator, task_id, original_task=t)

            # Branch 2 — parked on children (delegated). Re-enqueue only when all
            # children are terminal (orphaned wake-up); else leave it parked.
            elif t.status in _PARKED and t.block_kind == BlockKind.DELEGATED:
                children = [db.get_task(cid) for cid in db.get_children(task_id)]
                if all(c is not None and c.status in TERMINAL_STATES
                       for c in children):
                    # A recovery settlement can commit before its parent wake is
                    # delivered.  On a second startup the terminal child is still
                    # visible, but do not duplicate that already-pending bounded
                    # wake.  This is intentionally limited to the delegated
                    # parent path; ordinary pending-task startup enqueue behavior
                    # remains unchanged.
                    _sweep_enqueue(
                        queue, slug, task_id, orchestrator, pending_once=True,
                    )

            # Branch 3 — parked on jobs (blocked_on_job). Re-enqueue only when all
            # blocking jobs are terminal (jobs finished while the daemon was down);
            # else leave alone. MUST exist or these fall into Branch 1 and get
            # wrongly failed on every restart (the #1 reviewer-focus item).
            elif t.status in _PARKED and t.block_kind == BlockKind.BLOCKED_ON_JOB:
                try:
                    job_ids = _json.loads(t.blocked_on_job_ids or "[]")
                except _json.JSONDecodeError:
                    job_ids = []
                if job_ids and all(
                    db.get_job_status(j) in _TERMINAL_JOB_STATES for j in job_ids
                ):
                    # A second startup sweep can observe the same parked carrier
                    # before a worker claims the first wake.  Keep the ordinary
                    # resume wake one-shot in the in-memory queue, as for the
                    # delegated parked carrier above.
                    _sweep_enqueue(
                        queue, slug, task_id, orchestrator, pending_once=True,
                    )

            # Branch 4 — pending: re-enqueue (lost the original POST enqueue).
            elif t.status == TaskStatus.PENDING:
                _sweep_enqueue(queue, slug, task_id, orchestrator)

            # Branch 5 — escalated: leave alone (founder owns the transition).
            # Reached only because get_nonterminal_task_ids now yields escalated.
            elif t.status == TaskStatus.ESCALATED:
                pass
            # The boot-time migration flips any legacy blocked(escalated) row
            # before startup; this branch is reached only via new escalated rows.

        root_owner.check()
        # Branch 6 — thread reply delivery recovery (GitHub #688 Slice B).
        # Replace ONLY the conversational REPLY portion of the generic reaper with
        # the durable store-owned recovery primitives. BOOTSTRAP and TASK_FOLLOWUP
        # keep the generic daemon_restart reaping below.
        _now = datetime.now(timezone.utc).isoformat()

        # C1a: orphan/raw reaper paths below may never visit a participant
        # pair. Validate the COMPLETE companion family before any thread
        # recovery/rekeying. This is a reader, never an automatic release.
        from runtime.infrastructure.thread_delivery_release import delivery_view
        with db._lock:
            delivery_view(db)

        # 6a. Activate per-pair reply delivery state for every OPEN thread
        # (idempotent cutover: seeds/coalesces legacy pending REPLYs).
        move = getattr(db, "_team_move_coordinator", None)
        recovered_tokens: list[str] = []
        for thread_id in db.list_open_thread_ids():
            db.cutover_thread_reply_delivery_state(thread_id)

        # 6b. Durable reply-delivery recovery: retain valid queued wakes, replace
        # interrupted running attempts with exactly one daemon_restart replacement.
        for entry in db.recover_reply_delivery_state():
            recovered_tokens.append(entry.invocation_token)

        # 6e. TASK-5966 exchange reconcile: corruption sweep (fail-closed) then
        # idempotent closure evaluation for every open exchange. daemon_restart is
        # an INTERRUPTION, never terminal, while a recovery replacement covering
        # the exchange is queued/running (6b) — the quiescence predicate keeps the
        # cohort non-quiescent until that replacement settles. Catch-up tokens
        # minted by a stale-close are enqueued after commit like every other
        # startup token.
        for entry in db.reconcile_reply_exchanges():
            recovered_tokens.append(entry.invocation_token)

        # 6c. Reap the remaining conversational REPLY rows not governed by any
        # delivery-state ownership slot (orphan legacy receipts — e.g. archived
        # threads cutover does not reach). Never touch the governed queued wakes
        # retained above.
        # Cleanup backstops run asynchronously but use this same connection.
        # Own the whole raw UPDATE-to-commit transaction; never carry the lock
        # into process termination/waits above.
        with db._lock:
            protected_ids = move.threads.recovery_protected_ids() if move is not None else ()
            exclusions = " AND id NOT IN (" + ",".join("?" for _ in protected_ids) + ")" if protected_ids else ""
            db._conn.execute(
                "UPDATE thread_invocations SET status = 'failed', "
                "decline_reason = ?, consumed_at = ? "
                "WHERE status = 'pending' AND purpose = 'reply' "
                "AND invocation_token NOT IN "
                "(SELECT queued_invocation_token FROM thread_reply_delivery_state "
                " WHERE queued_invocation_token IS NOT NULL) "
                "AND invocation_token NOT IN "
                "(SELECT running_invocation_token FROM thread_reply_delivery_state "
                " WHERE running_invocation_token IS NOT NULL)" + exclusions,
                ("daemon_restart", _now, *protected_ids),
            )

            # 6d. Original reaper applies only outside held/C2-owned obligations.
            cursor = db._conn.execute(
                "UPDATE thread_invocations SET status = 'failed', "
                "decline_reason = ?, consumed_at = ? "
                "WHERE status = 'pending' AND purpose IN ('bootstrap', 'task_followup')" + exclusions,
                ("daemon_restart", _now, *protected_ids),
            )
            db._conn.commit()
        logger.debug(
            "startup sweep: reaped %d orphaned pending BOOTSTRAP/TASK_FOLLOWUP "
            "invocations; recovered %d reply-delivery tokens",
            cursor.rowcount, len(recovered_tokens),
        )
        if move is not None:
            recovered_tokens.extend(move.threads.runnable_tokens())
        recovered_tokens = list(dict.fromkeys(recovered_tokens))
    except _V2StartupConnectionUnsafe as exc:
        # Keep an original interrupt/SystemExit as a BaseException, with the
        # unsafe disposition chained and the finally suppression already set.
        for cause in exc.causes:
            if not isinstance(cause, Exception):
                root_owner.fail(cause)
                raise cause from exc
        root_owner.fail(exc)
        raise
    except BaseException as exc:
        root_owner.fail(exc)
        raise
    finally:
        # The owner remains registered through actual tail exit, every cold
        # human completion/bookkeeping result, context reset and ONE genuine
        # final batch. Failure/stop suppresses the whole batch, including late
        # transitions. A failed join/reset/attempt retains native ownership.
        root_owner.finalize(root_token)
        if recovery_gate is not None:
            try:
                recovery_gate.settle_sweep(root_owner)
            except BaseException as exc:
                root_owner.fail(exc)
        root_owner.raise_if_failed()
    return root_owner.outcome(recovered_tokens)


def _publish_v2_generations_on_startup(org: OrgState, queue: TaskQueue) -> RecoveryOutcome:
    """Consume actual publisher values; opaque transaction failures are unknown.

    The original publisher retains complete discovery/order, receipts and
    per-target isolation. No additional query infers safety after a failure.
    """
    from runtime.daemon.startup_recovery import RecoveryOutcome
    from runtime.models import (AuthorityPolicyV2PublicationTarget,
        AuthorityPolicyV2PublicationClaimOutcome, AuthorityPolicyV2PublicationAckOutcome,
        AuthorityPolicyV2PublicationFailureOutcome)
    from runtime.orchestrator.authority import publish_authority_policy_v2_notifications
    held = False
    first = None
    observed = False

    def observe(stage: str, value: object, cause: BaseException | None) -> None:
        nonlocal held, first, observed
        if stage == "discovery":
            observed = True
            positive = (cause is None and type(value) is list
                        and all(type(item) is AuthorityPolicyV2PublicationTarget for item in value))
        elif stage == "claim":
            positive = type(value) is AuthorityPolicyV2PublicationClaimOutcome and (
                value.status == "claimed" or (value.status == "publication_pending" and value.reason in
                {"identity_mismatch", "evidence_drift", "owner_lost", "lease_live", "attempt_overflow", "not_publishable"}))
            held |= positive and value.status == "publication_pending"
        elif stage == "ack":
            positive = type(value) is AuthorityPolicyV2PublicationAckOutcome and (
                value.status in {"published", "publish_returned"} or (value.status == "ack_pending" and value.reason in
                {"identity_mismatch", "evidence_drift", "stale_claim", "admission_evidence_missing"}))
            held |= positive and value.status == "ack_pending"
        elif stage == "failure":
            # Only genuine successful audited queue-failure bookkeeping proves
            # an attributable pending hold. Opaque rollback/COMMIT loss refuses.
            positive = (type(value) is AuthorityPolicyV2PublicationFailureOutcome
                        and value.status == "failure_recorded")
            held |= positive
        else:
            positive = False
        if not positive:
            if first is None:
                first = cause if cause is not None else RuntimeError("startup_v2_" + stage + "_outcome_unknown")
            # Known target-local holds retain the original isolation. Unknown
            # connection/rollback/COMMIT safety forbids further writer use.
            raise first

    if getattr(org, "orchestrator", None) is None or queue is None:
        raise RuntimeError("startup_v2_owner_unavailable")
    try:
        receipts = publish_authority_policy_v2_notifications(org.orchestrator, queue,
            limit=None, startup_observer=observe, _startup_recovery=org._startup_recovery)
    except BaseException as exc:
        if first is not None and first is not exc:
            first.add_note("v2 publication also raised: " + type(exc).__name__)
            raise first from exc
        raise
    if first is not None:
        raise first
    if not observed or type(receipts) is not list:
        raise RuntimeError("startup_v2_discovery_unobserved")
    return RecoveryOutcome("local_hold" if held else "settled", "startup_v2_publication_observed")


def recover_startup_native(recovery: StartupRecovery) -> RecoveryOutcome:
    """ONE P2-P7 operation; no asyncio loop exists on this native thread."""
    from runtime.daemon.startup_recovery import RecoveryOutcome
    from runtime.orchestrator.run_step import _StartupSweepOutcome
    state = recovery.state
    held = False
    with recovery.phase("sweep") as settle:
        state._assert_attachments_unchanged(recovery._attachments)
        for org in state.orgs.values():
            recovery.require_native_owner()
            state._assert_attachments_unchanged(recovery._attachments)
            tokens = _sweep_on_startup(org.db, state.queue, org.slug, org.orchestrator)
            if (not isinstance(tokens, _StartupSweepOutcome)
                    or tokens.disposition not in ("settled", "local_hold")
                    or tokens.first_cause is not None or tuple(tokens) != tokens.tokens
                    or any(type(token) is not str or not token for token in tokens)
                    or type(tokens.counters) is not dict
                    or any(type(value) is not int or value < 0 for value in tokens.counters.values())
                    or tokens.counters.get("tails_started") != tokens.counters.get("tails_settled")
                    or tokens.counters.get("effects_started") != tokens.counters.get("effects_settled")
                    or tokens.counters.get("human_started") != tokens.counters.get("human_done")
                    or tokens.counters.get("negative") != 0):
                raise RuntimeError("startup_sweep_outcome_unavailable")
            org._startup_recovered_thread_tokens = tokens
            held |= tokens.disposition == "local_hold"
            recovery.require_native_owner()
            state._assert_attachments_unchanged(recovery._attachments)
            outcome = _publish_v2_generations_on_startup(org, state.queue)
            if not isinstance(outcome, RecoveryOutcome) or not outcome.positive:
                raise RuntimeError("startup_v2_outcome_unavailable")
            held |= outcome.disposition == "local_hold"
        settle(RecoveryOutcome("local_hold" if held else "settled"))
    # Each entry reserves/settles its OWN phase. Consume before the next call.
    for operation in (discover_completed_workflow_handoffs_native,
                      bind_returned_native_on_startup, compatibility_native_on_startup,
                      redeliver_native_on_startup, recover_producers_native_on_startup):
        recovery.require_native_owner()
        outcome = operation(state, recovery)
        if not isinstance(outcome, RecoveryOutcome) or not outcome.positive:
            raise RuntimeError("startup_phase_outcome_unavailable")
        held |= outcome.disposition == "local_hold"
    return RecoveryOutcome("local_hold" if held else "settled")


def _build_state(settings: Settings) -> DaemonState:
    reg = runtimes.load()
    move_diagnostics = {}
    if reg.active is None:
        # Auto-provision a default runtime on first launch so the daemon
        # never starts idle unless the provisioning itself fails.
        # Precedence:
        #   1. Registered runtimes exist but none active → activate the first.
        #   2. Registry empty → create a default runtime at daemon_home/runtime.
        if reg.registered:
            target = reg.registered[0]
            logger.info("no active runtime — activating existing registered runtime: %s", target)
            move_diagnostics.update(runtimes.activate(target))
        else:
            default_path = paths.daemon_home() / "runtime"
            logger.info("no active runtime — auto-provisioning default runtime at %s", default_path)
            RuntimeDir.init(default_path)
            move_diagnostics.update(runtimes.register(default_path))
        reg = runtimes.load()
        if reg.active is None:
            # Defensive: if provisioning still yields no active runtime,
            # fall back to idle rather than raising. This path should be
            # unreachable in practice.
            logger.error("runtime auto-provision failed — starting in idle mode")
            return DaemonState.idle(settings)
    runtime = RuntimeDir.load(reg.active)
    state = DaemonState.from_runtime(runtime, settings, move_diagnostics=move_diagnostics)
    for org in state.orgs.values():
        # THR-229 checkpoint C3d4b: bind the REAL owning-process boot marker
        # (the existing ``authority_v2_origin_boot_id``) and the existing
        # server-owned permission-surface reader before any startup recovery or
        # publication.  These bindings are the production owner setup; they were
        # previously exercised only by tests/store forwarders.
        org.bind_authority_v2_owner()
        # THR-229 checkpoint B2b2: initialize the eligible Engineering selector
        # through the existing serialized transaction owner BEFORE any startup
        # recovery/enqueue and before the API becomes available. Initialization
        # is observation of authentic v1 history or genuine empty state, never
        # activation: the owning transaction refuses missing/corrupt histories
        # and an initializer-audit failure rather than manufacturing empty state
        # or silently selecting legacy. A refusal propagates out of
        # ``_build_state`` so the daemon never binds the API or admits launch.
        for team in org.teams.teams():
            manager = org.teams.executable_manager_for_team(team)
            if manager is not None and is_eligible_policy_manager(
                root=org.root, agent_name=manager, team=team, teams=org.teams,
            ):
                org.workflow_authority.ensure_authority_selector(
                    team=team,
                    publisher="daemon-startup:selector-initialization",
                )
        # P0 ends here. The original ordered sweep/publication and tokens
        # belong exclusively to the lifespan's retained native P2-P7 owner.
    # Worker-pool bootstrap is deferred to the FastAPI lifespan startup
    # event because we need a running event loop. See `create_app` →
    # lifespan.
    return state


def _bind_port(host: str, port: int = 0) -> tuple[socket.socket, int]:
    """Bind to `port` (0 = ephemeral) and return (socket, actual_port)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((host, port))
    return sock, sock.getsockname()[1]


def _install_signal_handlers(state: DaemonState) -> None:
    def _handle(signum: int, _frame: FrameType | None) -> None:
        logger.info("received signal %s — shutting down", signum)
        # Uvicorn re-emits captured signals after its lifespan has run. Only
        # positive completion of the shared close barrier is a clean shutdown.
        # A direct/early signal or a timed-out drain never closes DBs here and
        # leaves lifecycle files as uncertainty rather than advertising clean.
        if not state._shutdown_complete:
            logger.error("shutdown incomplete; retaining database/lifecycle barriers")
            sys.exit(1)
        for f in (paths.pid_file(), paths.port_file()):
            try:
                f.unlink()
            except FileNotFoundError:
                pass
        sys.exit(0)

    signal.signal(signal.SIGTERM, _handle)
    signal.signal(signal.SIGINT, _handle)


def _daemon_pid_alive() -> bool:
    """True when the daemon pid file names a live process (fail-closed).

    A pid file naming a live process means a normal daemon is (or may be)
    serving traffic — offline maintenance must refuse.  A missing or
    dead/stale pid is fine; an ambiguous probe (e.g. PermissionError) is
    treated as alive so destructive compaction is never attempted when the
    daemon state cannot be verified.
    """
    try:
        raw = paths.pid_file().read_text().strip()
        pid = int(raw)
    except (OSError, ValueError):
        return False
    try:
        os.kill(pid, 0)  # signal 0 = existence check, no signal sent
    except ProcessLookupError:
        return False
    except OSError:
        # PermissionError / undeterminable → fail closed (don't run
        # destructive compaction when we cannot verify the daemon is stopped).
        return True
    return True


def run_maintenance() -> int:
    """Run the offline/startup-only metrics maintenance one-shot and exit.

    This function IS the entire process when ``--maintenance`` is passed:
    it runs BEFORE ``_build_state`` (no org load, no startup sweep, no
    task-state mutation), before ``create_app``, before ``_bind_port`` and
    before uvicorn — so no HTTP listener, lifespan, scheduler loop, worker,
    or task/job/session producer ever starts.  It opens the active runtime's
    ``metrics.db`` through ``MetricsStore``, runs the ordered
    prune → WAL checkpoint → integrity check → controlled VACUUM →
    post-VACUUM WAL checkpoint → final integrity check sequence, logs the
    bounded telemetry report, and returns a process exit code.

    Fail-closed and bounded: every preflight step that can throw — daemon-home
    initialization, the pid probe, the runtime registry load, ``RuntimeDir.load``
    (stale/malformed runtime state), the store open, and the ordered maintenance
    sequence — sits inside ONE controlled failure boundary.  Any failure returns 1
    with a stable, non-sensitive classification (``MetricsMaintenanceError.code``
    or ``operational-error``) plus fixed recovery guidance — never raw
    exception text, tracebacks, filesystem paths, IDs, or injected secrets,
    and never a success claim or an automatic retry.  A fresh explicit
    invocation is required for retry.
    """
    from datetime import datetime, timedelta, timezone

    from runtime.daemon.metrics_store import (
        MetricsMaintenanceError,
        MetricsStore,
        _RETENTION_DAYS,
    )

    # The ENTIRE maintenance preflight + run sits inside the bounded
    # failure boundary: daemon-home initialization, pid probe, registry load,
    # RuntimeDir.load (which raises on stale/malformed runtime state), store
    # open, and the ordered maintenance sequence.  Any throw converts to exit
    # 1 with bounded recovery guidance — never a raw traceback out of
    # ``main()``.
    try:
        # Daemon-home initialization first — inside the same controlled
        # boundary as every other maintenance preflight, so a hostile or
        # malformed home (unwritable parent, path-as-file, injected marker)
        # returns exit 1 with fixed redacted guidance instead of an uncaught
        # traceback out of ``main()``.
        paths.ensure_daemon_home()

        # Offline guard (belt-and-suspenders): SQLite fail-closes on a
        # concurrent holder anyway (checkpoint busy / VACUUM locked), but a
        # live-daemon pid probe fails fast with an actionable message.
        if _daemon_pid_alive():
            logger.error(
                "metrics maintenance aborted: a daemon process appears to be "
                "running (the daemon pid file in the daemon home names a "
                "live process). Maintenance is OFFLINE/STARTUP-ONLY — stop "
                "the daemon first, then re-run "
                "'python -m runtime.daemon --maintenance'."
            )
            return 1

        reg = runtimes.load()
        if reg.active is None:
            logger.error(
                "metrics maintenance aborted: no active runtime is registered "
                "(runtimes.yaml has no active runtime); nothing to maintain. "
                "Register/activate a runtime, then re-run "
                "'python -m runtime.daemon --maintenance'."
            )
            return 1

        runtime = RuntimeDir.load(reg.active)
        store = MetricsStore(str(runtime.root / "metrics.db"))
        try:
            now = datetime.now(timezone.utc)
            cutoff = (now - timedelta(days=_RETENTION_DAYS)).isoformat()
            report = store.maintenance(cutoff)
        finally:
            try:
                store.close()
            except Exception:
                pass
    except MetricsMaintenanceError as exc:
        logger.error(
            "metrics maintenance FAILED (%s). No automatic retry — re-run "
            "'python -m runtime.daemon --maintenance' after resolving the "
            "cause. The store was left queryable where SQLite guarantees it; "
            "metrics.db/-wal/-shm were never deleted or hand-edited.",
            exc.code,
        )
        return 1
    except Exception:
        logger.error(
            "metrics maintenance FAILED (operational-error). No automatic "
            "retry — re-run 'python -m runtime.daemon --maintenance' after "
            "resolving the cause. The store was left queryable where SQLite "
            "guarantees it; metrics.db/-wal/-shm were never deleted or "
            "hand-edited."
        )
        return 1

    logger.info("metrics maintenance complete: %s", report)
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m runtime.daemon",
        description="HappyRanch daemon (or offline metrics maintenance one-shot).",
    )
    parser.add_argument(
        "--maintenance",
        action="store_true",
        help=(
            "Run the OFFLINE/STARTUP-ONLY metrics maintenance sequence "
            "(strict-before prune at the 30-day cutoff, WAL checkpoint, "
            "integrity check, controlled VACUUM, post-VACUUM WAL "
            "checkpoint, final integrity check) against the active "
            "runtime's metrics.db, log the bounded telemetry report, and "
            "exit. Never starts the normal daemon; failure returns a "
            "bounded, redacted classification plus fixed recovery guidance "
            "and requires a fresh explicit invocation for retry."
        ),
    )
    parser.add_argument("--reader-guarded-start", nargs="?", const="online-team-move@1",
        choices=("online-team-move@1", "online-team-move@2", "online-team-move@3"),
        help="Supported launcher reader-capability protocol; incompatible older parsers refuse before startup.")
    return parser


class _DiagnosticServer(uvicorn.Server):
    """Signal only after actual ASGI startup AND listener creation returned."""

    def __init__(self, config: uvicorn.Config, app) -> None:
        super().__init__(config)
        self._diagnostic_app = app
        self._diagnostic_signalled = False

    async def startup(self, sockets=None) -> None:
        await super().startup(sockets=sockets)
        if (self._diagnostic_signalled or not self.started or self.should_exit
                or self.lifespan.should_exit or self.config.app is not self._diagnostic_app):
            return
        self._diagnostic_signalled = True
        app = self._diagnostic_app
        # Only this original adapter proves ASGI AND actual listener success.
        # Service admission additionally waits for positive recovery readiness.
        from runtime.daemon.startup_recovery import bind_http_listener
        bind_http_listener(app, self)
        app.state.daemon._startup_recovery.signal_serving(app)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    # Parse the command line BEFORE any daemon-home initialization that can
    # throw: a hostile/malformed daemon home must never leak an uncaught
    # traceback out of an otherwise-controlled invocation.
    args = _build_parser().parse_args(argv)

    # Offline maintenance is the ENTIRE process: it must run before any
    # HTTP listener binds, before the FastAPI lifespan, and before any
    # scheduler/worker starts — then exit.  It can never coexist with
    # serving normal traffic.  Daemon-home initialization for the
    # maintenance path happens INSIDE run_maintenance()'s single bounded
    # failure boundary.
    if args.maintenance:
        return run_maintenance()

    paths.ensure_daemon_home()

    paths.ensure_token()

    settings = Settings()
    # Normalise PATH so the bundled happyranch CLI and standard tool
    # directories are available even when the daemon is launched by
    # Finder/launchd with a stripped PATH (/usr/bin:/bin).  Executor
    # binary resolution is handled separately via the machine-local
    # executors.json registry (THR-107 seq155).  Must happen before any
    # executor is constructed (issue #254).
    from runtime.orchestrator.executors import _normalize_path
    _normalize_path()
    state = _build_state(settings)
    app = create_app(state)

    sock, port = _bind_port(settings.daemon_bind_host, settings.daemon_port)
    paths.port_file().write_text(str(port))
    paths.pid_file().write_text(str(os.getpid()))
    _install_signal_handlers(state)

    logger.info("HappyRanch daemon listening on %s:%d", settings.daemon_bind_host, port)
    config = uvicorn.Config(app, log_level="info", lifespan="on")
    server = _DiagnosticServer(config, app)
    # Hand the bound socket to uvicorn so we don't race the port number.
    server.run(sockets=[sock])
    return 0 if state._shutdown_complete else 1


if __name__ == "__main__":
    raise SystemExit(main())
