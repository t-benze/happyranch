"""Durable metrics persistence store (THR-066 PR-1).

Provides an append-only snapshot store for the daemon's runtime metrics.
This is a daemon-global store (NOT per-org — the metrics aggregate spans
all orgs), stored at ``<runtime_root>/metrics.db``.

Pattern: same durable append-only pattern as audit_log, but a SEPARATE
additive store with its own schema.  Do NOT overload audit_log — its
task_id scope-prefix semantics are a load-bearing invariant.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from runtime.daemon.state import DaemonState

logger = logging.getLogger(__name__)

_RETENTION_DAYS = 30
_THROTTLE_SECONDS = 55


class MetricsStore:
    """Append-only metrics snapshot store backed by a daemon-global SQLite file.

    Constructed once at daemon startup on ``DaemonState``.  For idle state
    (no runtime), pass ``db_path=None`` to get an in-memory store.
    """

    def __init__(self, db_path: str | None) -> None:
        self._db_path = db_path
        self._conn = sqlite3.connect(db_path if db_path is not None else ":memory:")
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._init_schema()

    def _init_schema(self) -> None:
        """Create tables and indexes if they don't exist (idempotent)."""
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS metrics_snapshots (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                captured_at  TEXT NOT NULL,
                snapshot_json TEXT NOT NULL
            )"""
        )
        self._conn.execute(
            """CREATE INDEX IF NOT EXISTS idx_metrics_snapshots_captured
               ON metrics_snapshots(captured_at)"""
        )
        self._conn.commit()

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def append_snapshot(self, captured_at_iso: str, snapshot: dict[str, Any]) -> None:
        """Append a single metrics snapshot row."""
        self._conn.execute(
            "INSERT INTO metrics_snapshots (captured_at, snapshot_json) VALUES (?, ?)",
            (captured_at_iso, json.dumps(snapshot)),
        )
        self._conn.commit()

    # ------------------------------------------------------------------
    # Retention
    # ------------------------------------------------------------------

    def prune(self, before_iso: str) -> None:
        """Delete all rows whose captured_at is strictly before *before_iso*."""
        self._conn.execute(
            "DELETE FROM metrics_snapshots WHERE captured_at < ?",
            (before_iso,),
        )
        self._conn.commit()

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def query(
        self,
        since: str | None = None,
        until: str | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        """Return snapshot rows, newest-first.  *limit* caps returned rows.

        ``since`` / ``until`` are ISO-8601 strings filtering on ``captured_at``
        (inclusive bounds).
        """
        clauses: list[str] = []
        params: list[str | int] = []

        if since is not None:
            clauses.append("captured_at >= ?")
            params.append(since)
        if until is not None:
            clauses.append("captured_at <= ?")
            params.append(until)

        where = ""
        if clauses:
            where = " WHERE " + " AND ".join(clauses)

        rows = self._conn.execute(
            f"SELECT * FROM metrics_snapshots{where} ORDER BY captured_at DESC LIMIT ?",
            (*params, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        self._conn.close()


# ------------------------------------------------------------------
# Shared composer — called by BOTH the /metrics route and the
# periodic writer so the persisted payload stays byte-identical to
# the live route response.
# ------------------------------------------------------------------

def compose_metrics_snapshot(state: DaemonState) -> dict[str, Any]:
    """Return the full composed /metrics payload dict.

    Aggregates the in-memory registry snapshot + live pull-gauges
    (tasks, jobs, sessions, queue depth) across all loaded orgs.
    """
    snap = state.metrics_registry.snapshot()

    task_count = 0
    job_count = 0
    session_count = 0
    for org in state.orgs.values():
        task_count += len(org.db.get_nonterminal_task_ids())
        job_count += len(org.db.list_jobs_db(status="running"))
        session_count += org.sessions.count_active()

    snap["tasks"] = {"pending_and_in_flight": task_count}
    snap["jobs_in_flight"] = job_count
    snap["executor_sessions_active"] = session_count
    snap["run_step_queue_depth"] = state.queue._queue.qsize()

    return snap


# ------------------------------------------------------------------
# Periodic writer helper — called once per scheduler-loop tick.
# Throttled (write only if >= _THROTTLE_SECONDS since last write);
# prune old rows after each successful append.
# ------------------------------------------------------------------

def maybe_persist_metrics_snapshot(
    state: DaemonState, now: datetime
) -> None:
    """Append a metrics snapshot if the throttle window has elapsed.

    Errors are logged but never propagate — a persistence failure must
    NOT crash the hosting scheduler loop.
    """
    if state.metrics_store is None:
        return

    import time as _time

    elapsed = _time.monotonic() - state._last_metrics_snapshot_at
    if elapsed < _THROTTLE_SECONDS:
        return

    try:
        snapshot = compose_metrics_snapshot(state)
        state.metrics_store.append_snapshot(now.isoformat(), snapshot)
        state._last_metrics_snapshot_at = _time.monotonic()

        # Prune rows older than retention window
        cutoff = (now - timedelta(days=_RETENTION_DAYS)).isoformat()
        state.metrics_store.prune(cutoff)
    except Exception:
        logger.exception("Failed to persist metrics snapshot")
