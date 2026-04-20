"""OPC daemon entry point.

Bootstraps from ~/.opc/runtimes.yaml, binds an ephemeral local port,
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

from src.config import Settings
from src.daemon import paths, runtimes
from src.daemon.app import create_app
from src.daemon.queue import TaskQueue
from src.daemon.state import DaemonState
from src.infrastructure.audit_logger import AuditLogger
from src.infrastructure.database import Database
from src.models import BlockKind, TaskStatus
from src.runtime import RuntimeDir

logger = logging.getLogger("opc.daemon")


def _sweep_on_startup(db: Database, queue: TaskQueue) -> None:
    """Post-restart recovery:
      - in_progress rows → failed (we killed the subprocess)
      - pending rows → re-enqueue (lost the original POST enqueue)
      - blocked(DELEGATED) with all children terminal → re-enqueue parent
      - blocked(ESCALATED) → leave alone (founder owns these)
    """
    audit = AuditLogger(db)

    for task_id in db.get_nonterminal_task_ids():
        t = db.get_task(task_id)
        if t is None:
            continue
        if t.status == TaskStatus.IN_PROGRESS:
            db.update_task(task_id, status=TaskStatus.FAILED, note="daemon restart")
            audit.log_escalation(task_id, "daemon", "daemon restarted mid-task")
            # Notify parent if this failure unblocks it
            parent_id = t.parent_task_id
            if parent_id is not None:
                parent = db.get_task(parent_id)
                if (parent is not None and parent.status == TaskStatus.BLOCKED
                        and parent.block_kind == BlockKind.DELEGATED):
                    children = [db.get_task(cid) for cid in db.get_children(parent_id)]
                    if all(c is not None and c.status in {TaskStatus.COMPLETED,
                                                         TaskStatus.FAILED}
                           for c in children):
                        queue.enqueue(parent_id)
        elif t.status == TaskStatus.PENDING:
            queue.enqueue(task_id)
        elif t.status == TaskStatus.BLOCKED and t.block_kind == BlockKind.DELEGATED:
            children = [db.get_task(cid) for cid in db.get_children(task_id)]
            if all(c is not None and c.status in {TaskStatus.COMPLETED,
                                                  TaskStatus.FAILED}
                   for c in children):
                queue.enqueue(task_id)
        # blocked(ESCALATED) falls through: founder owns the transition.


def _build_state(settings: Settings) -> DaemonState:
    reg = runtimes.load()
    if reg.active is None:
        logger.warning("no active runtime — starting in idle mode")
        return DaemonState.idle(settings)
    runtime = RuntimeDir.load(reg.active)
    state = DaemonState.from_runtime(runtime, settings)
    _sweep_on_startup(state.db, state.queue)
    # Worker-pool bootstrap is deferred to the FastAPI lifespan startup
    # event because we need a running event loop. See `create_app` →
    # lifespan.
    return state


def _bind_port(host: str) -> tuple[socket.socket, int]:
    """Bind an ephemeral port and return (socket, port)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((host, 0))
    port = sock.getsockname()[1]
    return sock, port


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
        if state.db is not None:
            state.db.close()
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

    sock, port = _bind_port(settings.daemon_bind_host)
    paths.port_file().write_text(str(port))
    paths.pid_file().write_text(str(os.getpid()))
    _install_signal_handlers(state)

    logger.info("OPC daemon listening on %s:%d", settings.daemon_bind_host, port)
    config = uvicorn.Config(app, log_level="info", lifespan="on")
    server = uvicorn.Server(config)
    # Hand the bound socket to uvicorn so we don't race the port number.
    server.run(sockets=[sock])


if __name__ == "__main__":
    main()
