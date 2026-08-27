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
import time as _time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from runtime.daemon.host_session_store import compose_host_sessions_block

if TYPE_CHECKING:
    from runtime.daemon.state import DaemonState

logger = logging.getLogger(__name__)

_RETENTION_DAYS = 30
_THROTTLE_SECONDS = 55

# Explicit snapshot-payload format/version marker (THR-066 remediation,
# Slice 1).  ``2`` marks the route-template label semantics; a stored row
# WITHOUT this field is legacy raw-URL-path label format and remains
# queryable/readable (never rewritten in place).
_SNAPSHOT_FORMAT_VERSION = 2
_SNAPSHOT_FORMAT_FIELD = "format_version"


class MetricsMaintenanceError(Exception):
    """A daemon-owned offline metrics maintenance operation failed.

    Carries only a stable, non-sensitive classification ``code`` (never raw
    SQL results, filesystem paths, IDs, or exception text).  The optional
    ``detail`` is structured context for store-level debugging and is NEVER
    logged or interpolated into the message by the daemon entry seam.

    Raised only when the ordered maintenance sequence did NOT complete in
    full (e.g. a busy WAL checkpoint, or ``PRAGMA integrity_check`` did not
    return exactly ``ok``).  It never signals a partial success — callers
    must surface it and require a fresh explicit invocation.
    """

    def __init__(self, code: str, detail: str | None = None) -> None:
        self.code = code
        self.detail = detail
        # The message is the stable code only — ``str(exc)`` never embeds
        # raw content even if ``detail`` carried any.
        super().__init__(code)


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

    def prune(self, before_iso: str) -> int:
        """Delete all rows whose captured_at is strictly before *before_iso*.

        Returns the number of rows deleted.
        """
        cur = self._conn.execute(
            "DELETE FROM metrics_snapshots WHERE captured_at < ?",
            (before_iso,),
        )
        self._conn.commit()
        return cur.rowcount

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
    # Storage telemetry (read-only, non-sensitive)
    # ------------------------------------------------------------------

    def row_count(self) -> int:
        cur = self._conn.execute("SELECT COUNT(*) FROM metrics_snapshots")
        return int(cur.fetchone()[0])

    def oldest_captured_at(self) -> str | None:
        cur = self._conn.execute("SELECT MIN(captured_at) FROM metrics_snapshots")
        return cur.fetchone()[0]

    def newest_captured_at(self) -> str | None:
        cur = self._conn.execute("SELECT MAX(captured_at) FROM metrics_snapshots")
        return cur.fetchone()[0]

    def page_count(self) -> int | None:
        if self._db_path is None:
            return None
        cur = self._conn.execute("PRAGMA page_count")
        return int(cur.fetchone()[0])

    def freelist_count(self) -> int | None:
        if self._db_path is None:
            return None
        cur = self._conn.execute("PRAGMA freelist_count")
        return int(cur.fetchone()[0])

    def db_bytes(self) -> int | None:
        if self._db_path is None:
            return None
        return _file_size(self._db_path)

    def wal_bytes(self) -> int | None:
        if self._db_path is None:
            return None
        return _file_size(self._db_path + "-wal")

    # ------------------------------------------------------------------
    # Health / offline maintenance (startup-only, daemon-owned)
    # ------------------------------------------------------------------

    def health(self) -> dict[str, Any]:
        """Return non-sensitive storage-health aggregates (no row identifiers).

        Row count, oldest/newest ``captured_at``, total stored snapshot
        payload bytes, the latest snapshot's route-label count, SQLite
        page/free-list counts, and on-disk DB/WAL byte sizes (the last three
        are ``None`` for an in-memory store).
        """
        row = self._conn.execute(
            "SELECT COUNT(*) AS n, MIN(captured_at) AS oldest,"
            " MAX(captured_at) AS newest FROM metrics_snapshots"
        ).fetchone()
        result: dict[str, Any] = {
            "row_count": int(row["n"]),
            "oldest_captured_at": row["oldest"],
            "newest_captured_at": row["newest"],
            "total_snapshot_bytes": self._total_snapshot_bytes(),
            "route_label_count": self._latest_route_label_count(),
        }
        if self._db_path is None:
            result["page_count"] = None
            result["freelist_count"] = None
            result["db_bytes"] = None
            result["wal_bytes"] = None
            return result
        result["page_count"] = int(
            self._conn.execute("PRAGMA page_count").fetchone()[0]
        )
        result["freelist_count"] = int(
            self._conn.execute("PRAGMA freelist_count").fetchone()[0]
        )
        result["db_bytes"] = _file_size(self._db_path)
        result["wal_bytes"] = _file_size(self._db_path + "-wal")
        return result

    def _total_snapshot_bytes(self) -> int:
        """Sum of stored ``snapshot_json`` lengths (drives the DB footprint)."""
        cur = self._conn.execute(
            "SELECT COALESCE(SUM(LENGTH(snapshot_json)), 0) FROM metrics_snapshots"
        )
        return int(cur.fetchone()[0])

    def _latest_route_label_count(self) -> int:
        """Distinct route-label count of the newest stored snapshot (0 if none)."""
        row = self._conn.execute(
            "SELECT snapshot_json FROM metrics_snapshots"
            " ORDER BY captured_at DESC, id DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return 0
        try:
            parsed = json.loads(row["snapshot_json"])
        except (ValueError, TypeError):
            return 0
        return _route_label_count(parsed)

    def _wal_checkpoint(self) -> dict[str, int]:
        """Checkpoint + truncate the WAL; return the raw PRAGMA result row."""
        row = self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        return {
            "busy": int(row[0]),
            "log_frames": int(row[1]),
            "checkpointed_frames": int(row[2]),
        }

    def _integrity_check(self) -> str:
        """Return the joined ``PRAGMA integrity_check`` result."""
        rows = self._conn.execute("PRAGMA integrity_check").fetchall()
        return "\n".join(r[0] for r in rows)

    def _vacuum(self) -> None:
        """Run the controlled SQLite compaction (``VACUUM``)."""
        self._conn.execute("VACUUM")

    def maintenance(self, cutoff_iso: str) -> dict[str, Any]:
        """Run the offline maintenance sequence through this store connection.

        Ordered sequence: bounded pre-telemetry → unchanged strict-before
        prune at *cutoff_iso* → WAL checkpoint (TRUNCATE, fail-closed on
        ``busy``) → ``PRAGMA integrity_check`` (must be exactly ``ok``
        before compaction) → controlled ``VACUUM`` → post-VACUUM WAL
        checkpoint (TRUNCATE, fail-closed on ``busy``) → final
        ``PRAGMA integrity_check`` (must be exactly ``ok``) → bounded after
        telemetry.

        Returns a deterministic report dict (before/after DB/WAL bytes, rows,
        cutoff, page/free-list counts, duration, pre- and post-vacuum
        checkpoint outcomes, integrity outcomes, prune count, snapshot-size
        and route-label cardinality — no labels, IDs, slugs, or snapshot
        content).  Raises ``MetricsMaintenanceError`` (or a SQLite error) on
        any failure — never a partial success and never a success report
        after a failed post-VACUUM checkpoint.  Never touches the SQLite
        files at the filesystem level and never shells out; all work goes
        through this connection.  Intended for the startup-only maintenance
        one-shot — do not run against a live serving daemon.
        """
        t0 = _time.monotonic()
        before = self.health()
        pruned = self.prune(cutoff_iso)
        checkpoint = self._wal_checkpoint()
        if checkpoint["busy"] != 0:
            raise MetricsMaintenanceError("checkpoint-busy")
        integrity_before = self._integrity_check()
        if integrity_before != "ok":
            raise MetricsMaintenanceError("integrity-before-vacuum")
        self._vacuum()
        # Post-VACUUM WAL checkpoint: after controlled compaction, checkpoint
        # (TRUNCATE) the WAL again so the compaction's journal frames are
        # checkpointed back into the main file before the final integrity
        # evidence is recorded.  Fail closed on busy — a success report is
        # never produced after a failed post-vacuum checkpoint.
        post_vacuum_checkpoint = self._wal_checkpoint()
        if post_vacuum_checkpoint["busy"] != 0:
            raise MetricsMaintenanceError("post-vacuum-checkpoint-busy")
        integrity_after = self._integrity_check()
        if integrity_after != "ok":
            raise MetricsMaintenanceError("integrity-after-vacuum")
        after = self.health()
        duration_s = round(_time.monotonic() - t0, 6)
        return {
            "before": before,
            "after": after,
            "cutoff": cutoff_iso,
            "pruned_rows": pruned,
            "checkpoint": checkpoint,
            "post_vacuum_checkpoint": post_vacuum_checkpoint,
            "integrity_check_before_vacuum": integrity_before,
            "integrity_check_after_vacuum": integrity_after,
            "vacuum": "ok",
            "duration_seconds": duration_s,
        }


# ------------------------------------------------------------------
# Shared composer — called by BOTH the /metrics route and the
# periodic writer so the persisted payload stays byte-identical to
# the live route response.
# ------------------------------------------------------------------

def compose_metrics_snapshot(state: DaemonState) -> dict[str, Any]:
    """Return the full composed /metrics payload dict.

    Aggregates the in-memory registry snapshot + live pull-gauges
    (tasks, jobs, sessions, queue depth) across all loaded orgs, plus the
    bounded ``host_sessions`` observability block (THR-207): receipt
    aggregates/recent window from the daemon's HostSessionStore and the
    live supervisor admission/backpressure/capability view.
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
    snap["host_sessions"] = compose_host_sessions_block(state)
    snap[_SNAPSHOT_FORMAT_FIELD] = _SNAPSHOT_FORMAT_VERSION

    return snap


def _file_size(path: str) -> int | None:
    """Return the on-disk size in bytes of *path*, or None if absent."""
    try:
        return Path(path).stat().st_size
    except OSError:
        return None


def _route_label_count(snapshot: dict[str, Any]) -> int:
    """Number of distinct route labels, excluding the ``__all__`` aggregate."""
    http = snapshot.get("http")
    if not isinstance(http, dict):
        return 0
    return len(http) - (1 if "__all__" in http else 0)


def measure_storage_telemetry(
    store: MetricsStore, snapshot: dict[str, Any], prune_count: int
) -> dict[str, Any]:
    """Return a bounded, non-sensitive telemetry dict for one persist cycle.

    Deterministic given a fixed store + snapshot.  Contains only counts,
    byte sizes, ISO timestamps, and SQLite page counts — never route IDs,
    task IDs, thread IDs, org slugs, or snapshot contents.
    """
    return {
        "route_label_count": _route_label_count(snapshot),
        "serialized_bytes": len(json.dumps(snapshot)),
        "row_count": store.row_count(),
        "oldest_captured_at": store.oldest_captured_at(),
        "newest_captured_at": store.newest_captured_at(),
        "prune_count": prune_count,
        "db_bytes": store.db_bytes(),
        "wal_bytes": store.wal_bytes(),
        "page_count": store.page_count(),
        "freelist_count": store.freelist_count(),
    }


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
        prune_count = state.metrics_store.prune(cutoff)
    except Exception:
        logger.exception("Failed to persist metrics snapshot")
        return

    # Operational telemetry is measured and logged separately so a telemetry
    # failure can never mask a successful persist or crash the hosting loop.
    try:
        telemetry = measure_storage_telemetry(
            state.metrics_store, snapshot, prune_count
        )
    except Exception:
        logger.exception("Failed to measure metrics storage telemetry")
    else:
        logger.info("metrics snapshot persisted: %s", telemetry)
