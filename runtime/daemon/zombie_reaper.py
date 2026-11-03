"""Ongoing zombie-task reaper (THR-090 Track B).

Periodically scans in_progress tasks with no block_kind for zombie signatures
(dead process + stale heartbeat, with optional task_result fingerprint) and
applies flag-then-cancel-on-TTL discipline.

Design authority: THR-090 seq10 (consultant_head umbrella) + seq12 (founder
approval). This is a NARROW periodic reaper, NOT a general health monitor.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time as _time
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from pydantic import ValidationError

from runtime.daemon.queue import HEARTBEAT_INTERVAL_SECONDS
from runtime.infrastructure.audit_logger import AuditLogger
from runtime.models import TaskStatus

if TYPE_CHECKING:
    from runtime.daemon.state import DaemonState
    from runtime.daemon.queue import TaskQueue
    from runtime.infrastructure.database import Database
    from runtime.orchestrator.orchestrator import Orchestrator
    from runtime.models import TaskRecord

logger = logging.getLogger("happyranch.daemon.zombie_reaper")

# ---------------------------------------------------------------------------
# Tuning constants (founder-approved design — THR-090 seq10/seq12)
# ---------------------------------------------------------------------------

# Staleness threshold: ≥ 2 missed heartbeat intervals.
# A heartbeat that hasn't been stamped for this long is definitely stale.
STALE_HEARTBEAT_SECONDS = 2 * HEARTBEAT_INTERVAL_SECONDS  # 60s

# Tier 1 (WITH task_result fingerprint): shorter TTL — high confidence the
# task actually completed, so we can consume the result sooner.
FLAG_TTL_FINGERPRINT_SECONDS = HEARTBEAT_INTERVAL_SECONDS  # 30s

# Tier 2 (WITHOUT fingerprint): longer TTL — cancel-on-TTL is an inference
# that work didn't complete; stay flag-heavy and conservative.
FLAG_TTL_NO_FINGERPRINT_SECONDS = 5 * HEARTBEAT_INTERVAL_SECONDS  # 150s

# Interval between reaper sweeps. Aligned with the heartbeat cadence.
REAPER_INTERVAL_SECONDS = 30


def _session_policy_family(db: Database, task) -> str:
    """Classify the immutable launch binding; malformed/mixed fails closed."""
    if not task.assigned_agent or not task.current_session_id:
        return "legacy_or_none"
    from runtime.orchestrator.active_authority_policy import (
        ActiveAuthorityPolicyError,
        load_session_policy_binding,
        load_session_policy_snapshot,
    )
    from runtime.orchestrator.authority_policy_store import AuthorityPolicyStore
    try:
        binding = load_session_policy_binding(
            db=db, task_id=task.id, session_id=task.current_session_id,
            agent_name=task.assigned_agent,
        )
        if binding is None:
            return "legacy_or_none"
        if isinstance(binding, dict) and binding.get("mode") == "v2":
            return "v2"
        # The version-aware reader deliberately returns the legacy payload
        # without validating it.  Authenticate that immutable payload before
        # granting the unchanged generic v1 reaper path; unknown, corrupt or
        # mixed evidence must not become no-policy permission.
        load_session_policy_snapshot(
            db=db, store=AuthorityPolicyStore(db), task_id=task.id,
            session_id=task.current_session_id, agent_name=task.assigned_agent,
        )
    except ActiveAuthorityPolicyError:
        return "malformed"
    except ValueError as exc:
        # Match the existing v2 binding rejection partition. Successfully read
        # corrupt models/history deny; SQL/lock/decode/unexpected projection
        # failures retain their ORIGINAL observation, even under ValueError.
        if isinstance(exc, (json.JSONDecodeError, UnicodeError)) or (
            exc.__cause__ is not None
            and not isinstance(exc.__cause__, (ValidationError, KeyError, TypeError))
        ):
            raise
        return "malformed"
    return "legacy_or_none"


# ---------------------------------------------------------------------------
# PID liveness probe
# ---------------------------------------------------------------------------

def _pid_is_dead(pid: int) -> bool:
    """Return True if the given OS pid is definitively dead (ProcessLookupError).

    False for pid alive, None pid, or indeterminate (PermissionError, etc.).
    Err toward a miss: indeterminate → treat as alive (don't reap).
    """
    try:
        os.kill(pid, 0)  # signal 0 = existence check, no signal sent
    except ProcessLookupError:
        return True  # definitively dead
    except Exception:
        # PermissionError, recycled-pid uncertainty, etc. → indeterminate.
        # Err toward a miss: treat as alive.
        return False
    else:
        return False  # pid exists → alive


# ---------------------------------------------------------------------------
# Per-org sweep
# ---------------------------------------------------------------------------

def _sweep_org_zombies(
    db: Database,
    *,
    now: datetime,
    uptime: float,
    warm_up_seconds: float,
    orchestrator: Orchestrator | None = None,
    queue: TaskQueue | None = None,
    deferred_child_worktrees: list[TaskRecord] | None = None,
    _sweep_outcome: dict | None = None,
) -> None:
    """Sweep one org for zombie tasks.

    The periodic caller keeps lifecycle and queue publication on its event
    loop, deferring only the existing child hooks and captured root cleanup.
    Synchronous callers retain the original inline hook behavior.
    """
    from runtime.daemon.startup_recovery import ordinary_admission_validation, StartupUnavailable
    if orchestrator is not None:
        validate = ordinary_admission_validation(orchestrator)
        if orchestrator._db is not db or (queue is not None and queue is not orchestrator._queue):
            raise StartupUnavailable("startup_attachment_changed")
    else:
        refs = getattr(db, "_jobs_startup_refs", None)
        validate = (lambda: None) if refs is None else ordinary_admission_validation(refs[2])
    validate()

    def write(function, *args, **kwargs):
        validate()
        with db._lock:
            validate()
            return function(*args, **kwargs)

    # Warm-up grace: don't trust staleness until daemon has been up >= 1
    # heartbeat interval. Prevents false-reaping freshly-spawned sessions
    # whose heartbeat hasn't been stamped yet after a boot.
    from runtime.orchestrator.run_step import _capture_root_worktree_transition, _root_worktree_batch, _RootWorktreeTransitions, _ROOT_WORKTREE_OWNER_EVENTS
    # The caller holds this SAME record before delivery through contextlib.
    # Native occurrences are captured before reset, attachment or observation.
    sweep_outcome = {} if _sweep_outcome is None else _sweep_outcome
    failures = []
    sweep_outcome.update(failures=failures, prior=[], delivery=None, suppressed=False,
        deferred_batch=False)
    root_transitions = root_delivery = outer_delivery = root_token = None
    suppression_attempted = False

    def remember(kind: str | tuple, error: BaseException) -> None:
        failures.append((kind, error, BaseException.__traceback__.__get__(error)))
        sweep_outcome['suppressed'] = True

    def suppress_root_batch() -> None:
        nonlocal suppression_attempted
        if suppression_attempted:
            return
        suppression_attempted = True
        # Local suppression and the original manifest survive a failed merge.
        sweep_outcome['suppressed'] = True
        if root_delivery is not None:
            root_delivery['suppress_root_batch'] = True
            root_delivery['teardown_incomplete'] = True
        try:
            if outer_delivery is not None and outer_delivery['orch'] is orchestrator:
                outer_delivery['suppress_root_batch'] = True
                outer_delivery['teardown_incomplete'] = True
        except BaseException as error:
            remember('suppression_delivery', error)

    try:
        root_transitions = _RootWorktreeTransitions()
        sweep_outcome['manifest'] = root_transitions
        outer_delivery = _ROOT_WORKTREE_OWNER_EVENTS.get()
        root_delivery = dict(orch=orchestrator, events=root_transitions, active=True, teardown_incomplete=False)
        sweep_outcome['root_delivery'] = root_delivery
        root_token = _ROOT_WORKTREE_OWNER_EVENTS.set(root_delivery)
        if uptime < warm_up_seconds:
            return

        audit = AuditLogger(db)

        validate()
        task_ids = db.get_nonterminal_task_ids()
        validate()
        for task_id in task_ids:
            validate()
            t = db.get_task(task_id)
            validate()
            if t is None:
                continue

            from runtime.orchestrator.task_pause import pause_deferred_owner
            if pause_deferred_owner(db, task_id):
                continue
            from runtime.workflows.recovery import classify_task
            ownership = classify_task(db, task_id, org_slug=getattr(orchestrator, "_slug", None))
            if ownership.kind != "legacy":
                # TTL/dead PID are never proof of workflow host quiescence.
                drafts = getattr(orchestrator, "_workflow_drafts", None)
                if ownership.kind == "f5":
                    drafts = getattr(orchestrator, "_workflow_reviews", None)
                if ownership.kind in ("draft", "f5") and drafts is not None:
                    validate()
                    drafts.reconcile(task_id)
                    validate()
                    if queue is not None:
                        drafts.notify_queued(task_id, queue)
                continue

            # ── STATE ALLOWLIST (requirement 3) ──
            # Explicit allowlist: in_progress + block_kind NULL. Never touch a
            # healthy in_progress (fresh heartbeat), nor any blocked/terminal task.
            if t.status != TaskStatus.IN_PROGRESS or t.block_kind is not None:
                continue

            # ── PREDICATE (AND-gate) ──
            # All must hold to even consider a task as zombie.

            # 1. Stale heartbeat — must be older than STALE_HEARTBEAT_SECONDS.
            if t.last_heartbeat is None:
                continue  # err toward miss — can't determine staleness
            hb_age = (now - t.last_heartbeat).total_seconds()
            if hb_age < STALE_HEARTBEAT_SECONDS:
                # Heartbeat is fresh → healthy. If previously flagged, clear.
                if t.zombie_flagged_at is not None:
                    write(db.update_task, task_id, zombie_flagged_at=None)
                    write(audit.log_zombie_cleared,
                        task_id, t.assigned_agent or "unknown",
                    )
                continue

            # 2. Dead executor pid — must be definitively not alive.
            if t.executor_pid is None:
                continue  # err toward miss — can't probe
            if not _pid_is_dead(t.executor_pid):
                # Pid is alive (or indeterminate). If previously flagged, clear.
                if t.zombie_flagged_at is not None:
                    write(db.update_task, task_id, zombie_flagged_at=None)
                    write(audit.log_zombie_cleared,
                        task_id, t.assigned_agent or "unknown",
                    )
                continue

            # ── PREDICATE MATCHED — task is a zombie candidate ──

            # Determine fingerprint tier: is there an unconsumed task_result
            # from the current session?
            fingerprint = None
            if t.current_session_id is not None and t.assigned_agent is not None:
                fingerprint = db.get_latest_task_result(
                    task_id, t.assigned_agent, t.current_session_id,
                )

            # Tier-based TTL selection (requirement 2: fingerprint-tiered confidence)
            if fingerprint is not None:
                ttl = FLAG_TTL_FINGERPRINT_SECONDS  # shorter — high confidence
            else:
                ttl = FLAG_TTL_NO_FINGERPRINT_SECONDS  # longer — conservative

            agent = t.assigned_agent or "unknown"
            policy_family = _session_policy_family(db, t)

            if t.zombie_flagged_at is None:
                # ── FIRST DETECTION: FLAG only, do NOT cancel ──
                write(db.update_task, task_id, zombie_flagged_at=now.isoformat())
                write(audit.log_zombie_flagged, task_id, agent)
            else:
                # ── ALREADY FLAGGED ──
                # If a task_result fingerprint appears on an already-flagged zombie,
                # treat it as recovery IMMEDIATELY (recovery contract).
                # A real result is never a false-reap — honoring it immediately is
                # the safe direction (founder-approved loss function, THR-090 seq12).
                # Design note: merely clearing zombie_flagged_at without consuming
                # would re-flag next tick (task still in_progress + stale-hb +
                # dead-pid), so clear MUST be paired with consumption.
                if fingerprint is not None:
                    if orchestrator is not None:
                        validate()
                        consumption = _consume_zombie_fingerprint(
                            db, task_id, fingerprint, t, orchestrator,
                            pre_final_identity=(task_id, agent, t.current_session_id,
                                                fingerprint["id"])
                            if policy_family == "v2" else None,
                        )
                        from runtime.orchestrator.run_step import _HumanFailedRecoveryOperation
                        if isinstance(consumption, _HumanFailedRecoveryOperation) or consumption in (
                            "v2_pending", "startup_held", "lost_owner",
                        ):
                            # The exact selected operation owns legacy bookkeeping;
                            # pending/loss must never clear through this fallback.
                            continue
                        if policy_family == "v2":
                            # The real common consumer committed first.  The
                            # Database-owned CAS now re-reads the exact immutable
                            # result/session binding, v2 consumption receipt,
                            # owner/session and original marker under
                            # BEGIN IMMEDIATE.  A race preserves the winner.
                            write(db.consume_v2_fingerprint_and_clear_zombie,
                                task_id=task_id,
                                expected_agent=agent,
                                expected_session_id=t.current_session_id or "",
                                result_id=fingerprint.get("id"),
                                expected_zombie_flagged_at=t.zombie_flagged_at,
                                validate_admission=validate,
                            )
                        elif policy_family == "legacy_or_none":
                            # Unchanged v1/no-policy behavior.
                            db.update_task(task_id, zombie_flagged_at=None)
                            audit.log_zombie_cleared(task_id, agent)
                        # Malformed/mixed binding: the real consumer already
                        # failed closed; never clear through a legacy fallback.
                    # No orchestrator (unit-test context): leave flagged, retry
                    # next sweep when orchestrator is present.
                    continue
                # No fingerprint — check TTL for cancel.
                # zombie_flagged_at is parsed from TEXT column by Pydantic.
                # It may be a datetime object or a string.
                flag_time: datetime
                if isinstance(t.zombie_flagged_at, datetime):
                    flag_time = t.zombie_flagged_at
                else:
                    flag_time = datetime.fromisoformat(t.zombie_flagged_at)
                if (now - flag_time).total_seconds() >= ttl:
                    if policy_family == "malformed":
                        continue
                    if policy_family == "v2":
                        with _capture_root_worktree_transition(orchestrator, task_id, '_sweep_org_zombies.v2', root_transitions):
                            committed = write(db.cancel_zombie_without_fingerprint,
                                task_id=task_id,
                                expected_agent=agent,
                                expected_session_id=t.current_session_id or "",
                                expected_zombie_flagged_at=t.zombie_flagged_at,
                                cancelled_at=now.isoformat(),
                                validate_admission=validate,
                            )
                        if committed and orchestrator is not None:
                            from runtime.orchestrator.run_step import (
                                _enqueue_parent_if_waiting,
                                _reclaim_terminal_task_worktree,
                            )
                            try:
                                _enqueue_parent_if_waiting(orchestrator, task_id)
                            finally:
                                if t.parent_task_id is not None and deferred_child_worktrees is not None:
                                    deferred_child_worktrees.append(t)
                                else:
                                    _reclaim_terminal_task_worktree(orchestrator, task_id, original_task=t)
                        continue
                    # TTL expired — cancel.
                    # THR-079 ruling: no auto-revisit. Cancel via the existing
                    # cancelled status transition, routed through shared
                    # terminal side-effects so a delegated parent parked on
                    # this child is woken (code_reviewer FIX 2).
                    with _capture_root_worktree_transition(orchestrator, task_id, '_sweep_org_zombies.legacy', root_transitions):
                        write(db.update_task,
                            task_id,
                            status=TaskStatus.CANCELLED,
                            cancelled_at=now.isoformat(),
                            completed_at=now.isoformat(),
                            block_kind=None,
                            note="zombie reaped: session died without completing",
                        )
                    # Only the successful original COMMIT creates this
                    # obligation. Retain it before the separate audit or any
                    # import/publication tail; the periodic owner joins once.
                    deferred_child = (orchestrator is not None
                        and t.parent_task_id is not None
                        and deferred_child_worktrees is not None)
                    if deferred_child:
                        deferred_child_worktrees.append(t)
                    postcommit_error = None
                    try:
                        audit.log_zombie_cancelled(task_id, agent)
                        if orchestrator is not None:
                            from runtime.orchestrator.run_step import _enqueue_parent_if_waiting
                            _enqueue_parent_if_waiting(orchestrator, task_id)
                    except BaseException as exc:
                        postcommit_error = exc
                        raise
                    finally:
                        if orchestrator is not None and not deferred_child:
                            try:
                                from runtime.orchestrator.run_step import _reclaim_terminal_task_worktree
                                _reclaim_terminal_task_worktree(orchestrator, task_id, original_task=t)
                            except BaseException as exc:
                                if postcommit_error is None:
                                    raise
                                if exc is not postcommit_error:
                                    postcommit_error.add_note("zombie cleanup also failed: " + repr(exc)[:512])
    except BaseException as exc:
        remember('body', exc)
        suppress_root_batch()
    finally:
        if root_delivery is not None:
            root_delivery['active'] = False
        if root_token is not None:
            try:
                _ROOT_WORKTREE_OWNER_EVENTS.reset(root_token)
            except BaseException as exc:
                remember('root_owner_reset', exc)
                suppress_root_batch()
        if not failures:
            try:
                if _sweep_outcome is None:
                    _root_worktree_batch(orchestrator, root_transitions,
                        teardown_incomplete=root_delivery['teardown_incomplete'])
                else:
                    # The periodic owner must complete its OWN original reset
                    # before even entering the root-batch transport. It already
                    # holds this exact manifest; no new batch owner is created.
                    sweep_outcome['deferred_batch'] = True
            except BaseException as exc:
                remember('root_batch_delivery', exc)
                suppress_root_batch()
        if failures:
            # No failed/reset-failed path enters even root DB admission/report.
            # Each attachment is attempted once; failure remains in this live
            # record/frame rather than replacing the actual first cause.
            first = failures[0]
            for name, value in (('_zombie_root_transitions', root_transitions),
                                ('_zombie_sweep_failure', sweep_outcome)):
                try:
                    previous = BaseException.__getattribute__(first[1], '__dict__').get(name)
                    if previous is not None and previous is not value:
                        sweep_outcome['prior'].append((name, previous))
                    BaseException.__setattr__(first[1], name, value)
                except BaseException as exc:
                    remember(('attachment', name), exc)
            try:
                if outer_delivery is not None and outer_delivery['orch'] is orchestrator:
                    if root_transitions is not None:
                        outer_delivery['events'].extend(root_transitions)
            except BaseException as exc:
                remember('manifest_delivery', exc)
            try:
                raise BaseException.with_traceback(first[1], first[2])
            except BaseException as exc:
                # This is transport of first, not another native occurrence.
                sweep_outcome['delivery'] = (first, BaseException.__traceback__.__get__(exc))
                raise


# ---------------------------------------------------------------------------
# Fingerprint consumption
# ---------------------------------------------------------------------------

def _consume_zombie_fingerprint(
    db: Database,
    task_id: str,
    fingerprint: dict,
    task,
    orchestrator: Orchestrator,
    *, pre_final_identity: tuple[str, str, str, int] | None = None,
) -> object:
    """Consume an orphaned task_result discovered by the ongoing zombie reaper.

    Mirrors the Track A _sweep_on_startup orphaned-result consumption path:
    build a CompletionReport from the task_result row and feed it to
    _consume_completion_report (the same tail that run_step_impl uses for
    normal completions).
    """
    from runtime.orchestrator.orchestrator import completion_report_from_result_row

    orphaned_report = completion_report_from_result_row(
        task_id, fingerprint,
        fallback_agent=task.assigned_agent or "unknown",
    )
    from runtime.orchestrator.run_step import _consume_completion_report
    def bookkeeping() -> None:
        current = db.get_task(task_id)
        if (current is not None and current.assigned_agent == task.assigned_agent
                and current.current_session_id == task.current_session_id
                and current.zombie_flagged_at == task.zombie_flagged_at
                and current.zombie_flagged_at is not None):
            db.update_task(task_id, zombie_flagged_at=None)
            AuditLogger(db).log_zombie_cleared(task_id, task.assigned_agent or "unknown")
    return _consume_completion_report(
        orchestrator, task_id, orphaned_report,
        result_row_id=fingerprint.get("id"),
        recovery_reentry=True,
        reclaim_terminal_worktree=False,
        after_human_recovery=bookkeeping,
        pre_final_identity=pre_final_identity,
    )


# ---------------------------------------------------------------------------
# Periodic loop
# ---------------------------------------------------------------------------

async def zombie_reaper_loop(
    state: DaemonState,
    *,
    interval_seconds: int = REAPER_INTERVAL_SECONDS,
) -> None:
    """Periodic zombie-task reaper loop (THR-090 Track B).

    Runs per-org sweep on every tick. Mirrors the work_hours_scheduler_loop /
    dream_scheduler_loop pattern: asyncio.sleep interval, per-org iteration,
    metrics recording. Registered in runtime/daemon/app.py _lifespan alongside
    the existing scheduler tasks.
    """
    from runtime.daemon.startup_recovery import (
        maintenance_admission_validation, maintenance_service_validation, maintenance_runtime_replaced,
        ordinary_admission_validation, StartupUnavailable,
    )
    validate_service = maintenance_service_validation(state)
    boot_time = _time.monotonic()
    warm_up_seconds = HEARTBEAT_INTERVAL_SECONDS  # 30s post-boot grace

    while True:
        validate_service()
        runtime = state.runtime
        validate_state = maintenance_admission_validation(state)
        t0 = _time.monotonic()
        now = datetime.now(timezone.utc)
        uptime = t0 - boot_time

        for org in list(state.orgs_snapshot().values()):
            root_tail_failed = False
            failure_record = None
            try:
                validate_state()
                validate_org = ordinary_admission_validation(org)
                await org.workflow_submissions.discover_completed_handoffs(state.queue)
                validate_state()
                validate_org()
                from runtime.orchestrator.task_pause import discover_pause_work
                await discover_pause_work(org, state.queue)
                validate_state()
                validate_org()
                from runtime.orchestrator.run_step import (
                    _defer_root_worktree_batches, _RootWorktreeTransitions,
                    _root_worktree_batch, _reclaim_terminal_task_worktree,
                    _submit_worktree_cleanup, _join_worktree_cleanup,
                )
                root_transitions = _RootWorktreeTransitions()
                child_worktrees = []
                root_tail_failed = False
                primary = None
                primary_traceback = None
                cleanup_outcomes = []
                native_unsettled = False
                delivery = {"teardown_incomplete": False}
                sweep_outcome = {}
                failure_record = dict(occurrences=[], observations=[], prior=[],
                    sweep=sweep_outcome, cleanup_outcomes=cleanup_outcomes)
                attached = False
                uncertainty_attached = False
                root_batch_suppressed = False

                def capture_failure(kind: str, exc: BaseException,
                                    origin: tuple | None = None) -> tuple:
                    nonlocal primary, primary_traceback, root_tail_failed
                    if origin is None:
                        origin = (kind, exc, BaseException.__traceback__.__get__(exc))
                        failure_record['occurrences'].append(origin)
                    else:
                        failure_record['observations'].append((kind, origin))
                    if primary is None:
                        primary, primary_traceback = origin[1], origin[2]
                    root_tail_failed = True
                    return origin

                def retain_failure(exc: BaseException, *, kind: str = 'delivery',
                                   origin: tuple | None = None) -> None:
                    nonlocal primary, primary_traceback, root_tail_failed, native_unsettled
                    nonlocal attached, uncertainty_attached
                    # Actual capture precedes every fallible observer. Origins
                    # name an existing occurrence; they never add another throw.
                    capture_failure(kind, exc, origin)
                    try:
                        native_unsettled |= bool(getattr(exc, 'maintenance_native_unsettled', False))
                    except BaseException as secondary:
                        capture_failure('uncertainty_observation', secondary)
                    failure_record['native_unsettled'] = native_unsettled
                    if not attached:
                        attached = True
                        for name, value in (('_zombie_cleanup_outcomes', cleanup_outcomes),
                                            ('_zombie_failure_record', failure_record)):
                            try:
                                previous = BaseException.__getattribute__(primary, '__dict__').get(name)
                                if previous is not None and previous is not value:
                                    failure_record['prior'].append((name, previous))
                                BaseException.__setattr__(primary, name, value)
                            except BaseException as secondary:
                                capture_failure('error_attachment', secondary)
                    if native_unsettled and not uncertainty_attached:
                        uncertainty_attached = True
                        try:
                            BaseException.__setattr__(primary, 'maintenance_native_unsettled', True)
                        except BaseException as secondary:
                            capture_failure('uncertainty_attachment', secondary)
                    try:
                        state._startup_recovery._retain_secondary(exc)
                    except BaseException as secondary:
                        capture_failure('secondary_retention', secondary)
                    try:
                        rollback = getattr(exc, "_zombie_writer_rollback_secondary", None)
                        if rollback is not None:
                            secondary, traceback = rollback
                            capture_failure('writer_rollback_observation', secondary,
                                ('writer_rollback', secondary, traceback))
                            state._startup_recovery._retain_secondary(secondary)
                    except BaseException as secondary:
                        capture_failure('rollback_retention', secondary)

                def consume_sweep() -> None:
                    nonlocal root_batch_suppressed
                    root_batch_suppressed |= sweep_outcome.get('suppressed', False)
                    for occurrence in sweep_outcome.get('failures', ()):
                        retain_failure(occurrence[1], kind='sweep_observation', origin=occurrence)

                async def join_worktree(operation, *args, **kwargs) -> None:
                    # Registration preceded submission. The native owner retains
                    # its SAME result and fixed error/traceback slots even when
                    # cancellation selects another first cause at this wait.
                    nonlocal native_unsettled
                    owner = None
                    outcome = dict(owner=None, result=None, selected=None, observations=[])
                    cleanup_outcomes.append(outcome)
                    try:
                        owner = _submit_worktree_cleanup(operation, *args,
                            _cleanup_owner=org.orchestrator, **kwargs)
                        outcome['owner'] = owner
                        outcome['result'] = await _join_worktree_cleanup(owner)
                    except BaseException as exc:
                        traceback = BaseException.__traceback__.__get__(exc)
                        outcome['selected'] = (exc, traceback)
                        # Consume the actual failed join's earlier selection
                        # before its final (possibly virtual) delivery error.
                        origin = None
                        if owner is not None and owner.primary is not None:
                            origin = ('child_selected', owner.primary, owner.traceback)
                            retain_failure(owner.primary, kind='child_selection', origin=origin)
                        transport = traceback
                        while transport is not None and not (
                            transport.tb_frame.f_code is _join_worktree_cleanup.__code__
                            and origin is not None and transport.tb_next is origin[2]
                        ):
                            transport = transport.tb_next
                        if origin is not None and exc is origin[1]:
                            # Keep the raw delivered snapshot even for a virtual
                            # method throwing this same object again. Only the
                            # exact native edge qualifies it as transport; an
                            # unqualified delivery cannot invent a second native
                            # incident or erase either original traceback.
                            outcome['observations'].append((
                                'join_transport' if transport is not None else 'join_delivery_unqualified',
                                exc, traceback, origin))
                        else:
                            retain_failure(exc, kind='child_delivery')
                    finally:
                        if owner is not None:
                            outcome['result'] = owner.result
                            native_unsettled |= owner.unknown
                            failure_record['native_unsettled'] = native_unsettled
                            # Keep the ORIGINAL fixed slots and native snapshots,
                            # including equal-object occurrences. Reading these
                            # is observation, not a synthetic native raise.
                            for name, failure in owner.errors.items():
                                if failure is not None:
                                    occurrence = (name, failure, owner.error_tracebacks[name])
                                    outcome['observations'].append(('owner_slot', occurrence))
                                    retain_failure(failure, kind='child_slot', origin=occurrence)
                            try:
                                failure = owner.maintenance_error()
                            except BaseException as exc:
                                retain_failure(exc, kind='maintenance_observation')
                            else:
                                if failure is not None:
                                    occurrence = ('maintenance_unknown', failure,
                                        BaseException.__traceback__.__get__(failure))
                                    retain_failure(failure, kind='maintenance_observation', origin=occurrence)
                            for exc in owner.notifications:
                                occurrence = ('wait_notification', exc,
                                    BaseException.__traceback__.__get__(exc))
                                outcome['observations'].append(('notification', occurrence))
                                retain_failure(exc, kind='notification_observation', origin=occurrence)

                context = _defer_root_worktree_batches(org.orchestrator, root_transitions)
                # Hold the ORIGINAL helper frame before entry/exit can fail.
                # Its _JobTailErrors remains reachable even if every exception
                # attachment fails and contextlib closes the generator.
                context_frame = context.gen.gi_frame
                failure_record['root_context'] = (context, context_frame)
                context_failure = None
                try:
                    with context as delivery:
                        try:
                            _sweep_org_zombies(
                                org.db, now=now, uptime=uptime,
                                warm_up_seconds=warm_up_seconds,
                                orchestrator=org.orchestrator, queue=state.queue,
                                deferred_child_worktrees=child_worktrees,
                                _sweep_outcome=sweep_outcome,
                            )
                        except BaseException as exc:
                            # Select inner E1 BEFORE contextlib or its own reset.
                            # Successful handling is local transport only; this
                            # failed sweep can never authorize the root DB batch.
                            root_batch_suppressed = True
                            consume_sweep()
                            delivered = sweep_outcome.get('delivery')
                            if delivered is not None and exc is delivered[0][1]:
                                failure_record['observations'].append(
                                    ('sweep_delivery', exc, BaseException.__traceback__.__get__(exc), delivered))
                            else:
                                retain_failure(exc, kind='sweep_delivery')
                except BaseException as exc:
                    root_batch_suppressed = True
                    context_failure = (exc, BaseException.__traceback__.__get__(exc))
                finally:
                    # Consume the original helper ledger, including failed
                    # attachments, before its final exception observation.
                    context_errors = context_frame.f_locals.get('errors')
                    failure_record['context_errors'] = context_errors
                    if context_errors is not None:
                        for occurrence in context_errors._occurrences:
                            root_batch_suppressed = True
                            retain_failure(occurrence[1], kind='context_observation', origin=occurrence)
                    if context_failure is not None:
                        exc, traceback = context_failure
                        if context_errors is not None and exc is context_errors.primary:
                            failure_record['observations'].append(
                                ('context_delivery', exc, traceback, context_errors))
                        else:
                            retain_failure(exc, kind='context_delivery')
                    root_batch_suppressed |= (sweep_outcome.get('suppressed', False)
                        or delivery.get('suppress_root_batch', False)
                        or delivery.get('_context_incomplete', False))
                    if not root_batch_suppressed and sweep_outcome.get('deferred_batch', False):
                        try:
                            root_transitions.extend(sweep_outcome['manifest'])
                            delivery['teardown_incomplete'] |= sweep_outcome['root_delivery']['teardown_incomplete']
                        except BaseException as exc:
                            root_batch_suppressed = True
                            retain_failure(exc, kind='manifest_delivery')
                    # These are the original committed children captured by
                    # the sweep, including a failed parent-publication tail.
                    # Join each once outside capture/DB locks; one ended error
                    # or cancellation cannot discard the remaining obligations.
                    for task in child_worktrees:
                        try:
                            await join_worktree(
                                _reclaim_terminal_task_worktree, org.orchestrator, task.id)
                        except BaseException as exc:
                            retain_failure(exc, kind='child_outcome_consumption')
                    if not root_batch_suppressed and root_transitions:
                        await join_worktree(
                            _root_worktree_batch, org.orchestrator, root_transitions,
                            teardown_incomplete=root_tail_failed or delivery['teardown_incomplete'])
                if primary is not None:
                    raise BaseException.with_traceback(primary, primary_traceback)
                validate_state()
                validate_org()
            except StartupUnavailable as exc:
                if root_tail_failed or not maintenance_runtime_replaced(state, runtime, validate_service, exc):
                    raise
                logger.warning("zombie reaper selected runtime retired: %s", exc)
                break
            except Exception as exc:
                if failure_record is not None:
                    if primary is None:
                        retain_failure(exc, kind='periodic_delivery')
                    unsettled = native_unsettled
                else:
                    unsettled = getattr(exc, 'maintenance_native_unsettled', False)
                if unsettled:
                    # Independent original joins above are still owed, but no
                    # dependent org/tick may start after uncertain native cleanup.
                    raise
                try:
                    logger.exception(
                        "zombie reaper sweep failed for org %s", org.slug,
                    )
                except BaseException as secondary:
                    if failure_record is None:
                        raise
                    retain_failure(secondary, kind='failure_diagnostic')
                    raise BaseException.with_traceback(primary, primary_traceback)

        duration = _time.monotonic() - t0
        state.metrics_registry.record_loop_tick(
            "zombie_reaper", interval_seconds, duration,
        )
        await asyncio.sleep(interval_seconds)
        validate_service()
