"""Tests for MetricsStore and compose_metrics_snapshot (THR-066 PR-1)."""
from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from runtime.config import Settings
from runtime.daemon.metrics_store import (
    MetricsStore,
    compose_metrics_snapshot,
    _RETENTION_DAYS,
)
from runtime.daemon.state import DaemonState


# ---------------------------------------------------------------------------
# MetricsStore unit tests
# ---------------------------------------------------------------------------

class TestMetricsStore:
    """Unit tests for MetricsStore (append, query, prune, idempotent init)."""

    def test_append_and_query_roundtrip(self, tmp_path: Path) -> None:
        store = MetricsStore(str(tmp_path / "metrics.db"))
        now = datetime(2026, 7, 4, 12, 0, 0, tzinfo=timezone.utc)
        snap = {"uptime_seconds": 42.0, "tasks": {"pending_and_in_flight": 3}}
        store.append_snapshot(now.isoformat(), snap)

        rows = store.query()
        assert len(rows) == 1
        assert rows[0]["captured_at"] == now.isoformat()
        assert json.loads(rows[0]["snapshot_json"]) == snap

    def test_query_newest_first(self, tmp_path: Path) -> None:
        store = MetricsStore(str(tmp_path / "metrics.db"))
        t1 = datetime(2026, 7, 4, 12, 0, 0, tzinfo=timezone.utc)
        t2 = datetime(2026, 7, 4, 12, 1, 0, tzinfo=timezone.utc)
        store.append_snapshot(t1.isoformat(), {"n": 1})
        store.append_snapshot(t2.isoformat(), {"n": 2})

        rows = store.query()
        assert len(rows) == 2
        # newest first
        assert json.loads(rows[0]["snapshot_json"]) == {"n": 2}
        assert json.loads(rows[1]["snapshot_json"]) == {"n": 1}

    def test_query_since_filter(self, tmp_path: Path) -> None:
        store = MetricsStore(str(tmp_path / "metrics.db"))
        t1 = datetime(2026, 7, 4, 12, 0, 0, tzinfo=timezone.utc)
        t2 = datetime(2026, 7, 4, 12, 5, 0, tzinfo=timezone.utc)
        t3 = datetime(2026, 7, 4, 12, 10, 0, tzinfo=timezone.utc)
        store.append_snapshot(t1.isoformat(), {"n": 1})
        store.append_snapshot(t2.isoformat(), {"n": 2})
        store.append_snapshot(t3.isoformat(), {"n": 3})

        rows = store.query(since=t2.isoformat())
        assert len(rows) == 2
        assert json.loads(rows[0]["snapshot_json"]) == {"n": 3}

    def test_query_until_filter(self, tmp_path: Path) -> None:
        store = MetricsStore(str(tmp_path / "metrics.db"))
        t1 = datetime(2026, 7, 4, 12, 0, 0, tzinfo=timezone.utc)
        t2 = datetime(2026, 7, 4, 12, 5, 0, tzinfo=timezone.utc)
        t3 = datetime(2026, 7, 4, 12, 10, 0, tzinfo=timezone.utc)
        store.append_snapshot(t1.isoformat(), {"n": 1})
        store.append_snapshot(t2.isoformat(), {"n": 2})
        store.append_snapshot(t3.isoformat(), {"n": 3})

        rows = store.query(until=t2.isoformat())
        assert len(rows) == 2  # t1 and t2
        assert json.loads(rows[0]["snapshot_json"]) == {"n": 2}

    def test_query_limit(self, tmp_path: Path) -> None:
        store = MetricsStore(str(tmp_path / "metrics.db"))
        for i in range(10):
            t = datetime(2026, 7, 4, 12, i, 0, tzinfo=timezone.utc)
            store.append_snapshot(t.isoformat(), {"n": i})

        rows = store.query(limit=3)
        assert len(rows) == 3
        # newest first
        assert json.loads(rows[0]["snapshot_json"]) == {"n": 9}

    def test_query_default_limit(self, tmp_path: Path) -> None:
        store = MetricsStore(str(tmp_path / "metrics.db"))
        for i in range(600):
            t = datetime(2026, 7, 4, 12, 0, 0, tzinfo=timezone.utc) + timedelta(seconds=i)
            store.append_snapshot(t.isoformat(), {"n": i})

        rows = store.query()
        assert len(rows) == 500  # default limit

    def test_prune_deletes_old_rows(self, tmp_path: Path) -> None:
        store = MetricsStore(str(tmp_path / "metrics.db"))
        old = datetime(2026, 6, 1, tzinfo=timezone.utc)  # >30 days ago
        recent = datetime(2026, 7, 4, tzinfo=timezone.utc)

        store.append_snapshot(old.isoformat(), {"n": 1})
        store.append_snapshot(recent.isoformat(), {"n": 2})

        cutoff = datetime(2026, 7, 1, tzinfo=timezone.utc)
        store.prune(cutoff.isoformat())

        rows = store.query()
        assert len(rows) == 1
        assert json.loads(rows[0]["snapshot_json"]) == {"n": 2}

    def test_prune_retains_exact_boundary(self, tmp_path: Path) -> None:
        store = MetricsStore(str(tmp_path / "metrics.db"))
        boundary = datetime(2026, 7, 4, 12, 0, 0, tzinfo=timezone.utc)
        store.append_snapshot(boundary.isoformat(), {"n": 1})

        # prune at the boundary — rows AT boundary should be retained
        store.prune(boundary.isoformat())
        rows = store.query()
        assert len(rows) == 1  # boundary row retained (not older THAN)

    def test_init_idempotent(self, tmp_path: Path) -> None:
        path = str(tmp_path / "metrics.db")
        store1 = MetricsStore(path)
        # Append something to verify data persists
        store1.append_snapshot(
            datetime(2026, 7, 4, tzinfo=timezone.utc).isoformat(), {"n": 1}
        )

        # Second init is a no-op
        store2 = MetricsStore(path)
        rows = store2.query()
        assert len(rows) == 1
        assert json.loads(rows[0]["snapshot_json"]) == {"n": 1}

    def test_schema_columns(self, tmp_path: Path) -> None:
        store = MetricsStore(str(tmp_path / "metrics.db"))
        # Verify table has the expected columns
        conn = sqlite3.connect(str(tmp_path / "metrics.db"))
        cursor = conn.execute("PRAGMA table_info(metrics_snapshots)")
        columns = {row[1]: row[2] for row in cursor.fetchall()}
        assert columns == {"id": "INTEGER", "captured_at": "TEXT", "snapshot_json": "TEXT"}
        conn.close()

    def test_index_exists(self, tmp_path: Path) -> None:
        store = MetricsStore(str(tmp_path / "metrics.db"))
        conn = sqlite3.connect(str(tmp_path / "metrics.db"))
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_metrics_snapshots_captured'"
        )
        assert cursor.fetchone() is not None
        conn.close()


# ---------------------------------------------------------------------------
# compose_metrics_snapshot tests
# ---------------------------------------------------------------------------

class TestComposeMetricsSnapshot:
    """Tests for the shared composer that both the route and writer use."""

    def test_compose_idle_state(self) -> None:
        """With idle state (no orgs), pull-gauges are zero."""
        state = DaemonState.idle(Settings())
        snap = compose_metrics_snapshot(state)
        assert "uptime_seconds" in snap
        assert "loops" in snap
        assert "http" in snap
        assert snap["tasks"] == {"pending_and_in_flight": 0}
        assert snap["jobs_in_flight"] == 0
        assert snap["executor_sessions_active"] == 0
        assert snap["run_step_queue_depth"] == 0

    def test_compose_shape_matches_route_output(self, app_idle, auth_headers) -> None:
        """Composer output keys match what the /metrics route returns."""
        from fastapi.testclient import TestClient

        client = TestClient(app_idle)
        r = client.get("/api/v1/metrics", headers=auth_headers)
        route_body = r.json()

        state = app_idle.state.daemon
        composer_body = compose_metrics_snapshot(state)

        # Same top-level keys
        assert set(composer_body.keys()) == set(route_body.keys())
        # Same shape for nested structures
        assert isinstance(composer_body["tasks"], dict)
        assert "pending_and_in_flight" in composer_body["tasks"]
        assert isinstance(composer_body["jobs_in_flight"], int)
        assert isinstance(composer_body["executor_sessions_active"], int)
        assert isinstance(composer_body["run_step_queue_depth"], int)

    def test_compose_has_loops(self, app_idle) -> None:
        """After a loop tick, composer includes loop data."""
        state = app_idle.state.daemon
        state.metrics_registry.record_loop_tick("test_loop", 60, 0.5)
        snap = compose_metrics_snapshot(state)
        assert "test_loop" in snap["loops"]
        assert snap["loops"]["test_loop"]["interval_seconds"] == 60


# ---------------------------------------------------------------------------
# DaemonState metrics_store construction tests
# ---------------------------------------------------------------------------

class TestDaemonStateMetricsStore:
    """Verify metrics_store is constructed on DaemonState."""

    def test_from_runtime_has_metrics_store(self, tmp_path: Path) -> None:
        """DaemonState.from_runtime constructs a metrics_store at runtime root."""
        from runtime.runtime import RuntimeDir

        rt = RuntimeDir.init(tmp_path / "runtime")
        settings = Settings()
        state = DaemonState.from_runtime(rt, settings)

        assert state.metrics_store is not None
        # Store file should exist at the expected path
        expected_path = rt.root / "metrics.db"
        assert expected_path.exists()

    def test_idle_has_metrics_store(self) -> None:
        """DaemonState.idle constructs a metrics_store (in-memory)."""
        state = DaemonState.idle(Settings())
        assert state.metrics_store is not None

    def test_idle_store_is_usable(self) -> None:
        """The idle store can be appended to and queried."""
        state = DaemonState.idle(Settings())
        now = datetime(2026, 7, 4, tzinfo=timezone.utc)
        state.metrics_store.append_snapshot(now.isoformat(), {"test": True})
        rows = state.metrics_store.query()
        assert len(rows) == 1
        assert json.loads(rows[0]["snapshot_json"]) == {"test": True}


# ---------------------------------------------------------------------------
# Periodic writer integration tests
# ---------------------------------------------------------------------------

class TestPeriodicWriterIntegration:
    """Integration tests for the periodic snapshot writer piggybacked on
    work_hours_scheduler_loop."""

    @pytest.mark.asyncio
    async def test_loop_tick_writes_snapshot(self, tmp_path: Path) -> None:
        """One iteration of work_hours_scheduler_loop writes exactly one row."""
        from runtime.runtime import RuntimeDir
        from runtime.daemon.work_hours_scheduler import work_hours_scheduler_loop

        rt = RuntimeDir.init(tmp_path / "runtime")
        state = DaemonState.from_runtime(rt, Settings())

        # Run one iteration of the loop (the loop sleeps 60s, so we need to
        # cancel after one tick). We run it as a task and cancel after the tick.
        import asyncio

        async def run_one_tick():
            # We monkey-patch asyncio.sleep to return immediately so the loop
            # doesn't actually sleep 60s.
            original_sleep = asyncio.sleep

            async def fast_sleep(seconds):
                if seconds == 60:
                    # After the first sleep, raise to break the loop
                    raise asyncio.CancelledError()
                return await original_sleep(seconds)

            with pytest.MonkeyPatch.context() as mp:
                mp.setattr(asyncio, "sleep", fast_sleep)
                try:
                    await work_hours_scheduler_loop(state)
                except asyncio.CancelledError:
                    pass

        await run_one_tick()

        # Should have written one row
        rows = state.metrics_store.query()
        assert len(rows) == 1
        snap = json.loads(rows[0]["snapshot_json"])
        assert "uptime_seconds" in snap
        assert "tasks" in snap
        assert "captured_at" in rows[0]

    @pytest.mark.asyncio
    async def test_throttle_prevents_duplicate_writes(self, tmp_path: Path) -> None:
        """Within the throttle window, a second tick does NOT write a new row."""
        from runtime.runtime import RuntimeDir
        from runtime.daemon.work_hours_scheduler import work_hours_scheduler_loop
        import asyncio

        rt = RuntimeDir.init(tmp_path / "runtime")
        state = DaemonState.from_runtime(rt, Settings())

        tick_count = 0

        async def fast_sleep(seconds):
            nonlocal tick_count
            tick_count += 1
            if tick_count >= 3:
                raise asyncio.CancelledError()
            # Return immediately (no real sleep)

        original_sleep = asyncio.sleep
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(asyncio, "sleep", fast_sleep)
            try:
                await work_hours_scheduler_loop(state)
            except asyncio.CancelledError:
                pass

        # Multiple ticks ran, but only one row should be written (throttle)
        rows = state.metrics_store.query()
        assert len(rows) == 1

    @pytest.mark.asyncio
    async def test_write_failure_does_not_crash_loop(self, tmp_path: Path) -> None:
        """A persistence error must never crash the scheduler loop."""
        from runtime.runtime import RuntimeDir
        from runtime.daemon.work_hours_scheduler import work_hours_scheduler_loop
        import asyncio

        rt = RuntimeDir.init(tmp_path / "runtime")
        state = DaemonState.from_runtime(rt, Settings())

        # Make append_snapshot raise
        original_append = state.metrics_store.append_snapshot

        def broken_append(*args, **kwargs):
            raise OSError("disk full")

        state.metrics_store.append_snapshot = broken_append

        tick_count = 0

        async def fast_sleep(seconds):
            nonlocal tick_count
            tick_count += 1
            if tick_count >= 2:
                raise asyncio.CancelledError()

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(asyncio, "sleep", fast_sleep)
            try:
                await work_hours_scheduler_loop(state)
            except asyncio.CancelledError:
                pass

        # Loop didn't crash — we got two ticks
        assert tick_count >= 2
