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
import threading
import time as _time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from runtime.daemon.state import DaemonState

logger = logging.getLogger(__name__)

_RETENTION_DAYS = 30
_THROTTLE_SECONDS = 55

# Snapshot format version (THR-066 remediation, TASK-5443).
#
# Version 2 snapshots carry route-TEMPLATE HTTP labels (bounded cardinality).
# Historical rows that predate this field used raw ``request.url.path`` labels
# (unbounded cardinality).  A MISSING ``format_version`` key therefore means
# "legacy / raw-label format" and must remain readable — rows are never
# rewritten in place; legacy rows age out under the unchanged 30-day policy.
SNAPSHOT_FORMAT_VERSION = 2
SNAPSHOT_FORMAT_VERSION_KEY = "format_version"


class MetricsMaintenanceError(Exception):
    """A daemon-owned metrics maintenance operation failed.

    Raised only when the maintenance sequence did NOT complete in full (e.g.
    ``PRAGMA integrity_check`` did not return ``ok``).  It never signals a
    partial success — callers must surface it and require a fresh explicit
    invocation.
    """


class MetricsStore:
    """Append-only metrics snapshot store backed by a daemon-global SQLite file.

    Constructed once at daemon startup on ``DaemonState``.  For idle state
    (no runtime), pass ``db_path=None`` to get an in-memory store.

    Threading: a single shared connection (``check_same_thread=False``)
    guarded by a ``threading.RLock`` so history reads/writes, the periodic
    writer, and the maintenance route cannot interleave on the same
    connection (mirrors ``runtime/infrastructure/database.py``).
    """

    def __init__(self, db_path: str | None) -> None:
        self._db_path = db_path
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            db_path if db_path is not None else ":memory:",
            check_same_thread=False,
        )
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
        with self._lock:
            self._conn.execute(
                "INSERT INTO metrics_snapshots (captured_at, snapshot_json) VALUES (?, ?)",
                (captured_at_iso, json.dumps(snapshot)),
            )
            self._conn.commit()

    # ------------------------------------------------------------------
    # Retention
    # ------------------------------------------------------------------

    def prune(self, before_iso: str) -> int:
        """Delete all rows whose captured_at is strictly before *before_iso*.

        Returns the number of rows deleted (0 when none matched).  Never
        touches the SQLite files on disk — only ``DELETE`` through the store.
        """
        with self._lock:
            cursor = self._conn.execute(
                "DELETE FROM metrics_snapshots WHERE captured_at < ?",
                (before_iso,),
            )
            self._conn.commit()
            return cursor.rowcount

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

        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM metrics_snapshots{where} ORDER BY captured_at DESC LIMIT ?",
                (*params, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Health / maintenance
    # ------------------------------------------------------------------

    def health(self) -> dict[str, Any]:
        """Return non-sensitive storage-health aggregates (no row identifiers).

        Deterministic dict: row count, oldest/newest ``captured_at``, SQLite
        page/free-list counts, and on-disk DB/WAL byte sizes (0 for the
        in-memory store).  Used for persistence telemetry and maintenance
        before/after evidence.
        """
        with self._lock:
            return self._health_locked()

    def _health_locked(self) -> dict[str, Any]:
        page_count = self._conn.execute("PRAGMA page_count").fetchone()[0]
        freelist_count = self._conn.execute("PRAGMA freelist_count").fetchone()[0]
        row = self._conn.execute(
            "SELECT COUNT(*) AS n, MIN(captured_at) AS oldest, MAX(captured_at) AS newest"
            " FROM metrics_snapshots"
        ).fetchone()
        db_bytes = 0
        wal_bytes = 0
        if self._db_path is not None:
            db_file = Path(self._db_path)
            try:
                if db_file.exists():
                    db_bytes = db_file.stat().st_size
            except OSError:
                pass
            wal_file = Path(self._db_path + "-wal")
            try:
                if wal_file.exists():
                    wal_bytes = wal_file.stat().st_size
            except OSError:
                pass
        return {
            "row_count": int(row["n"]),
            "oldest_captured_at": row["oldest"],
            "newest_captured_at": row["newest"],
            "page_count": int(page_count),
            "freelist_count": int(freelist_count),
            "db_bytes": db_bytes,
            "wal_bytes": wal_bytes,
        }

    def _wal_checkpoint_locked(self) -> dict[str, int]:
        """Checkpoint + truncate the WAL; return the raw PRAGMA result row."""
        row = self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        return {
            "busy": int(row[0]),
            "log_frames": int(row[1]),
            "checkpointed_frames": int(row[2]),
        }

    def _integrity_check_locked(self) -> str:
        rows = self._conn.execute("PRAGMA integrity_check").fetchall()
        return "\n".join(r[0] for r in rows)

    def maintenance(self, cutoff_iso: str) -> dict[str, Any]:
        """Run the daemon-owned maintenance sequence under the store lock.

        Sequence: prune (unchanged 30-day cutoff) → checkpoint WAL →
        ``PRAGMA integrity_check`` (fail closed on non-``ok``) → ``VACUUM`` →
        post-vacuum integrity + health evidence.

        Returns a deterministic report dict.  Raises ``MetricsMaintenanceError``
        (or a SQLite error) on any failure — never a partial success.  Never
        touches the SQLite files at the filesystem level and never shells out.
        """
        t0 = _time.monotonic()
        with self._lock:
            before = self._health_locked()
            pruned = self._prune_locked(cutoff_iso)
            checkpoint = self._wal_checkpoint_locked()
            integrity_before = self._integrity_check_locked()
            if integrity_before != "ok":
                raise MetricsMaintenanceError(
                    f"PRAGMA integrity_check did not return 'ok' before VACUUM: "
                    f"{integrity_before!r}"
                )
            self._conn.execute("VACUUM")
            integrity_after = self._integrity_check_locked()
            if integrity_after != "ok":
                raise MetricsMaintenanceError(
                    f"PRAGMA integrity_check did not return 'ok' after VACUUM: "
                    f"{integrity_after!r}"
                )
            after = self._health_locked()
        duration_s = round(_time.monotonic() - t0, 6)
        return {
            "before": before,
            "after": after,
            "cutoff": cutoff_iso,
            "pruned_rows": pruned,
            "checkpoint": checkpoint,
            "integrity_check_before_vacuum": integrity_before,
            "integrity_check_after_vacuum": integrity_after,
            "duration_seconds": duration_s,
        }

    def _prune_locked(self, before_iso: str) -> int:
        cursor = self._conn.execute(
            "DELETE FROM metrics_snapshots WHERE captured_at < ?",
            (before_iso,),
        )
        self._conn.commit()
        return cursor.rowcount

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        with self._lock:
            self._conn.close()


# ------------------------------------------------------------------
# Live-work counting — shared by the composer and the quiescence check.
# ------------------------------------------------------------------

def live_work_counts(state: DaemonState) -> dict[str, int]:
    """Return non-sensitive live-work counts across all loaded orgs.

    Nonterminal tasks, running jobs, and active executor sessions.  These are
    the same pull-gauges the composed snapshot surfaces.
    """
    task_count = 0
    job_count = 0
    session_count = 0
    for org in state.orgs.values():
        task_count += len(org.db.get_nonterminal_task_ids())
        job_count += len(org.db.list_jobs_db(status="running"))
        session_count += org.sessions.count_active()
    return {
        "nonterminal_tasks": task_count,
        "running_jobs": job_count,
        "active_executor_sessions": session_count,
    }


def daemon_is_quiescent(state: DaemonState) -> dict[str, Any]:
    """Return daemon quiescence facts (no raw identifiers).

    Quiescent == no nonterminal task, no running job, and no active executor
    session across all loaded orgs.
    """
    counts = live_work_counts(state)
    return {
        "nonterminal_tasks": counts["nonterminal_tasks"],
        "running_jobs": counts["running_jobs"],
        "active_executor_sessions": counts["active_executor_sessions"],
        "quiescent": (
            counts["nonterminal_tasks"] == 0
            and counts["running_jobs"] == 0
            and counts["active_executor_sessions"] == 0
        ),
    }


# ------------------------------------------------------------------
# Shared composer — called by BOTH the /metrics route and the
# periodic writer so the persisted payload stays byte-identical to
# the live route response.
# ------------------------------------------------------------------

def compose_metrics_snapshot(state: DaemonState) -> dict[str, Any]:
    """Return the full composed /metrics payload dict.

    Aggregates the in-memory registry snapshot + live pull-gauges
    (tasks, jobs, sessions, queue depth) across all loaded orgs, and stamps
    the additive ``format_version`` marker.
    """
    snap = state.metrics_registry.snapshot()

    counts = live_work_counts(state)
    snap["tasks"] = {"pending_and_in_flight": counts["nonterminal_tasks"]}
    snap["jobs_in_flight"] = counts["running_jobs"]
    snap["executor_sessions_active"] = counts["active_executor_sessions"]
    snap["run_step_queue_depth"] = state.queue._queue.qsize()

    snap[SNAPSHOT_FORMAT_VERSION_KEY] = SNAPSHOT_FORMAT_VERSION

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

    On success, emits a deterministic structured-log telemetry record with
    non-sensitive route-label cardinality, serialized snapshot bytes, prune
    count, and storage-health aggregates (DB/WAL bytes, page/free-list counts,
    oldest/newest capture, row count).  No raw identifiers are logged.
    """
    if state.metrics_store is None:
        return

    elapsed = _time.monotonic() - state._last_metrics_snapshot_at
    if elapsed < _THROTTLE_SECONDS:
        return

    try:
        snapshot = compose_metrics_snapshot(state)
        serialized_bytes = len(json.dumps(snapshot))
        route_label_cardinality = len(snapshot.get("http", {}))

        state.metrics_store.append_snapshot(now.isoformat(), snapshot)
        state._last_metrics_snapshot_at = _time.monotonic()

        # Prune rows older than retention window
        cutoff = (now - timedelta(days=_RETENTION_DAYS)).isoformat()
        pruned = state.metrics_store.prune(cutoff)
        health = state.metrics_store.health()

        logger.info(
            "metrics snapshot persisted",
            extra={
                "route_label_cardinality": route_label_cardinality,
                "serialized_snapshot_bytes": serialized_bytes,
                "pruned_rows": pruned,
                "row_count": health["row_count"],
                "db_bytes": health["db_bytes"],
                "wal_bytes": health["wal_bytes"],
                "page_count": health["page_count"],
                "freelist_count": health["freelist_count"],
                "oldest_captured_at": health["oldest_captured_at"],
                "newest_captured_at": health["newest_captured_at"],
            },
        )
    except Exception:
        logger.exception("Failed to persist metrics snapshot")
