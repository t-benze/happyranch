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
from src.daemon.state import DaemonState
from src.infrastructure.audit_logger import AuditLogger
from src.infrastructure.database import Database
from src.models import TaskStatus
from src.runtime import RuntimeDir

logger = logging.getLogger("opc.daemon")


def _escalate_in_flight_tasks(db: Database) -> None:
    """Mark nonterminal tasks (PENDING + IN_PROGRESS) as escalated — daemon restart
    kills any in-flight spawn and orphans queued runners. No resumption in Spec 1."""
    audit = AuditLogger(db)
    for task_id in db.get_nonterminal_task_ids():
        db.update_task(task_id, status=TaskStatus.ESCALATED)
        audit.log_escalation(task_id, "daemon", "daemon restarted mid-task")


def _build_state(settings: Settings) -> DaemonState:
    reg = runtimes.load()
    if reg.active is None:
        logger.warning("no active runtime — starting in idle mode")
        return DaemonState.idle(settings)
    runtime = RuntimeDir.load(reg.active)
    state = DaemonState.from_runtime(runtime, settings)
    _escalate_in_flight_tasks(state.db)
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
