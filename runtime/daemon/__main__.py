"""HappyRanch daemon entry point.

Bootstraps from ~/.happyranch/runtimes.yaml, binds an ephemeral local port,
writes pid/port files, and runs the FastAPI app under uvicorn.
"""
from __future__ import annotations

import logging
import os
import signal
import socket
import sys
from types import FrameType

import uvicorn

from runtime.config import Settings
from runtime.daemon import paths, runtimes
from runtime.daemon.app import create_app
from runtime.daemon.queue import TaskQueue
from runtime.daemon.state import DaemonState
from runtime.infrastructure.audit_logger import AuditLogger
from runtime.infrastructure.database import Database
from runtime.models import BlockKind, TaskStatus
from runtime.orchestrator.orchestrator import Orchestrator
from runtime.runtime import RuntimeDir

logger = logging.getLogger("happyranch.daemon")


def _sweep_on_startup(
    db: Database, queue: TaskQueue, slug: str,
    orchestrator: Orchestrator | None = None,
) -> None:
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
    """
    # Imported lazily to avoid a startup-time cycle (run_step → daemon types).
    from runtime.orchestrator.run_step import (
        TERMINAL_STATES,
        _enqueue_parent_if_waiting,
    )

    audit = AuditLogger(db)

    import json as _json
    # Path B: parked carriers are in_progress(...) with block_kind set.
    # A live subprocess is in_progress + block_kind IS NULL (Branch 1).
    _PARKED = {TaskStatus.IN_PROGRESS}
    _TERMINAL_JOB_STATES = {"completed", "failed", "rejected"}

    for task_id in db.get_nonterminal_task_ids():
        t = db.get_task(task_id)
        if t is None:
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
                from runtime.models import CompletionReport, NextStep
                import json as _json
                _raw_decision = orphaned_result_row.get("decision_json")
                _decision: NextStep | None = None
                if _raw_decision:
                    try:
                        _parsed = _json.loads(_raw_decision)
                        if isinstance(_parsed, dict):
                            _decision = NextStep(**_parsed)
                    except Exception:
                        _decision = None
                orphaned_report = CompletionReport(
                    task_id=task_id,
                    agent=orphaned_result_row.get("agent") or (t.assigned_agent or "unknown"),
                    status=orphaned_result_row.get("status") or "completed",
                    confidence=orphaned_result_row.get("confidence_score") or 0,
                    output_summary=orphaned_result_row.get("output_summary") or "",
                    verdict=orphaned_result_row.get("verdict"),
                    decision=_decision,
                    risks_flagged=orphaned_result_row.get("risks_flagged") or [],
                    output_dir=orphaned_result_row.get("output_dir"),
                    waiting_on_job_ids=orphaned_result_row.get("waiting_on_job_ids") or [],
                )
                # Audit: log the completion report so the consumed result is
                # visible — the original session's log_completion_report call
                # never ran (the daemon died before that point).
                orchestrator._audit.log_completion_report(report=orphaned_report)
                from runtime.orchestrator.run_step import _consume_completion_report
                _consume_completion_report(orchestrator, task_id, orphaned_report)
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
            db.update_task(task_id, status=TaskStatus.FAILED, note=reason)
            audit.log_daemon_restart_failure(task_id, t.assigned_agent or "daemon")
            if orchestrator is not None:
                _enqueue_parent_if_waiting(
                    orchestrator, task_id,
                    root_auto_revisit_spawned=False,
                )

        # Branch 2 — parked on children (delegated). Re-enqueue only when all
        # children are terminal (orphaned wake-up); else leave it parked.
        elif t.status in _PARKED and t.block_kind == BlockKind.DELEGATED:
            children = [db.get_task(cid) for cid in db.get_children(task_id)]
            if all(c is not None and c.status in TERMINAL_STATES
                   for c in children):
                queue.enqueue(slug, task_id)

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
                queue.enqueue(slug, task_id)

        # Branch 4 — pending: re-enqueue (lost the original POST enqueue).
        elif t.status == TaskStatus.PENDING:
            queue.enqueue(slug, task_id)

        # Branch 5 — escalated: leave alone (founder owns the transition).
        # Reached only because get_nonterminal_task_ids now yields escalated.
        elif t.status == TaskStatus.ESCALATED:
            pass
        # The boot-time migration flips any legacy blocked(escalated) row
        # before startup; this branch is reached only via new escalated rows.

    # Branch 6 — orphaned pending thread invocations: every reply subprocess
    # was killed by the restart, so every pending invocation is orphaned.
    # Reap them to 'failed' with decline_reason='daemon_restart' so the UI
    # reply box (queued/working render) clears on next poll.
    # A pending invocation is orphaned regardless of thread status (open or
    # archived), so we reap across ALL threads.
    #
    # This uses db._conn directly (bypassing the Database._synchronized
    # lock) because _sweep_on_startup runs synchronously at boot, before
    # the event loop or worker pool starts — there is no concurrent access
    # to the DB connection at this point. The UPDATE is guarded by
    # WHERE status='pending' so already-terminal rows are preserved.
    from datetime import datetime, timezone
    _now = datetime.now(timezone.utc).isoformat()
    cursor = db._conn.execute(
        "UPDATE thread_invocations SET status = 'failed', "
        "decline_reason = ?, consumed_at = ? "
        "WHERE status = 'pending'",
        ("daemon_restart", _now),
    )
    db._conn.commit()
    logger.debug(
        "startup sweep: reaped %d orphaned pending thread invocations",
        cursor.rowcount,
    )


def _build_state(settings: Settings) -> DaemonState:
    reg = runtimes.load()
    if reg.active is None:
        # Auto-provision a default runtime on first launch so the daemon
        # never starts idle unless the provisioning itself fails.
        # Precedence:
        #   1. Registered runtimes exist but none active → activate the first.
        #   2. Registry empty → create a default runtime at daemon_home/runtime.
        if reg.registered:
            target = reg.registered[0]
            logger.info("no active runtime — activating existing registered runtime: %s", target)
            runtimes.activate(target)
        else:
            default_path = paths.daemon_home() / "runtime"
            logger.info("no active runtime — auto-provisioning default runtime at %s", default_path)
            RuntimeDir.init(default_path)
            runtimes.register(default_path)
        reg = runtimes.load()
        if reg.active is None:
            # Defensive: if provisioning still yields no active runtime,
            # fall back to idle rather than raising. This path should be
            # unreachable in practice.
            logger.error("runtime auto-provision failed — starting in idle mode")
            return DaemonState.idle(settings)
    runtime = RuntimeDir.load(reg.active)
    state = DaemonState.from_runtime(runtime, settings)
    for org in state.orgs.values():
        _sweep_on_startup(org.db, state.queue, org.slug, org.orchestrator)
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
        # uvicorn handles its own SIGTERM/SIGINT to drain workers; here
        # we just make sure the lifecycle files get cleaned up.
        for f in (paths.pid_file(), paths.port_file()):
            try:
                f.unlink()
            except FileNotFoundError:
                pass
        for org in state.orgs.values():
            try:
                org.db.close()
            except Exception:
                pass
        sys.exit(0)

    signal.signal(signal.SIGTERM, _handle)
    signal.signal(signal.SIGINT, _handle)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    paths.ensure_daemon_home()
    paths.ensure_token()

    settings = Settings()
    # Normalise PATH so executor binaries (claude, codex, opencode, pi) are
    # findable even when the daemon is launched by Finder/launchd with a
    # stripped PATH (/usr/bin:/bin).  Must happen before any executor is
    # constructed (issue #254).
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
    server = uvicorn.Server(config)
    # Hand the bound socket to uvicorn so we don't race the port number.
    server.run(sockets=[sock])


if __name__ == "__main__":
    main()
