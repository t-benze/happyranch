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
    """Post-restart recovery for a single org:
      - in_progress rows → failed (we killed the subprocess); route through
        the unified auto-revisit primitive with failure_kind="daemon_restart"
        — same machinery that handles session_timeout / executor_error etc.
        The cascade-fail propagates upward; founder notification is suppressed
        when an auto-revisit covers the work.
      - pending rows → re-enqueue (lost the original POST enqueue)
      - blocked(DELEGATED) with all children terminal → re-enqueue parent
        (orphaned wake-up: the daemon died after a child terminated but
        before the parent saw the signal — distinct from the in_progress
        path above)
      - blocked(ESCALATED) → leave alone (founder owns these)

    When ``orchestrator`` is None (test harnesses that don't construct one),
    the in_progress branch degrades to mark-failed-and-audit only; no auto-
    revisit, no cascade, no notify. Production always passes an orchestrator.
    """
    # Imported lazily to avoid a startup-time cycle (run_step → daemon types).
    from runtime.orchestrator.run_step import (
        TERMINAL_STATES,
        _enqueue_parent_if_waiting,
        _maybe_spawn_auto_revisit,
    )

    audit = AuditLogger(db)
    # Per-restart dedup: a single daemon restart can force-fail multiple
    # in-flight tasks across one lineage. Each would otherwise spawn an
    # independent auto-revisit pointing at the same predecessor root, burning
    # the per-kind cap and producing parallel retry trees. Spawn at most one
    # auto-revisit per unique root per sweep; subsequent same-root failures
    # still propagate their cascade with auto_revisit_spawned=True so the
    # founder isn't pinged multiple times.
    revisited_roots: set[str] = set()

    for task_id in db.get_nonterminal_task_ids():
        t = db.get_task(task_id)
        if t is None:
            continue
        if t.status == TaskStatus.IN_PROGRESS:
            db.update_task(task_id, status=TaskStatus.FAILED, note="daemon restart")
            audit.log_daemon_restart_failure(task_id, t.assigned_agent or "daemon")
            if orchestrator is None:
                continue
            chain = db.walk_ancestors(task_id)
            root_id = chain[-1].id if chain else task_id
            if root_id in revisited_roots:
                # Earlier iteration already auto-revisited this lineage.
                spawned = True
            else:
                spawned = _maybe_spawn_auto_revisit(
                    orchestrator, task_id,
                    t.assigned_agent or "(unknown)",
                    failure_kind="daemon_restart",
                    error_context={"reason": "daemon restarted mid-task"},
                )
                if spawned:
                    revisited_roots.add(root_id)
            _enqueue_parent_if_waiting(
                orchestrator, task_id,
                root_auto_revisit_spawned=spawned,
            )
        elif t.status == TaskStatus.PENDING:
            queue.enqueue(slug, task_id)
        elif t.status == TaskStatus.BLOCKED and t.block_kind == BlockKind.DELEGATED:
            children = [db.get_task(cid) for cid in db.get_children(task_id)]
            if all(c is not None and c.status in TERMINAL_STATES
                   for c in children):
                queue.enqueue(slug, task_id)
        # blocked(ESCALATED) falls through: founder owns the transition.


def _build_state(settings: Settings) -> DaemonState:
    reg = runtimes.load()
    if reg.active is None:
        logger.warning("no active runtime — starting in idle mode")
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
