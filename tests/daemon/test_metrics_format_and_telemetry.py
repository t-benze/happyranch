"""Tests for the snapshot format marker and storage telemetry (THR-066 Slice 1).

Covers: the additive ``format_version`` marker on live + persisted snapshots
via the shared composer; legacy rows (missing marker) staying readable and
unchanged; the exact 30-day strict-before prune boundary plus prune count;
and bounded, non-sensitive, deterministic storage telemetry that cannot crash
a scheduler loop.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from runtime.config import Settings
from runtime.daemon.metrics_store import (
    _RETENTION_DAYS,
    _SNAPSHOT_FORMAT_FIELD,
    _SNAPSHOT_FORMAT_VERSION,
    MetricsStore,
    compose_metrics_snapshot,
    maybe_persist_metrics_snapshot,
    measure_storage_telemetry,
)
from runtime.daemon.state import DaemonState


# ---------------------------------------------------------------------------
# Format marker
# ---------------------------------------------------------------------------

def test_compose_snapshot_carries_format_version() -> None:
    state = DaemonState.idle(Settings())
    snap = compose_metrics_snapshot(state)
    assert snap[_SNAPSHOT_FORMAT_FIELD] == _SNAPSHOT_FORMAT_VERSION


def test_live_route_carries_format_version(tmp_home, app_idle, auth_headers) -> None:
    client = TestClient(app_idle)
    r = client.get("/api/v1/metrics", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()[_SNAPSHOT_FORMAT_FIELD] == _SNAPSHOT_FORMAT_VERSION


def test_persisted_snapshot_matches_composed_structure(tmp_path: Path) -> None:
    """The periodic writer persists exactly the shared composer output
    (same marker, same http/structure) — the live/persisted invariant."""
    state = DaemonState.idle(Settings())
    state.metrics_store = MetricsStore(str(tmp_path / "metrics.db"))
    state._last_metrics_snapshot_at = 0.0  # defeat throttle

    state.metrics_registry.record_http_latency("GET /health", 0.001)
    now = datetime(2026, 7, 4, 12, 0, 0, tzinfo=timezone.utc)
    maybe_persist_metrics_snapshot(state, now)

    rows = state.metrics_store.query()
    assert len(rows) == 1
    stored = json.loads(rows[0]["snapshot_json"])
    composed = compose_metrics_snapshot(state)

    # Structure-identical: same marker and same http shape (uptime_seconds
    # is a live gauge that advances between the two reads, so compare the
    # deterministic parts).
    assert stored[_SNAPSHOT_FORMAT_FIELD] == composed[_SNAPSHOT_FORMAT_FIELD]
    assert stored["http"] == composed["http"]
    assert stored["tasks"] == composed["tasks"]
    assert stored["jobs_in_flight"] == composed["jobs_in_flight"]


def test_legacy_row_without_marker_remains_readable(tmp_path: Path) -> None:
    """A stored row WITHOUT the marker (legacy raw-label format) is returned
    unchanged and parses cleanly — never rewritten in place."""
    store = MetricsStore(str(tmp_path / "metrics.db"))
    legacy = {"uptime_seconds": 1.0, "http": {"GET /api/v1/orgs/tourism/tasks/TASK-1": {"count": 1, "p50": 0.1, "p95": 0.1, "max": 0.1}}}
    store.append_snapshot("2026-07-01T00:00:00+00:00", legacy)

    rows = store.query()
    assert len(rows) == 1
    parsed = json.loads(rows[0]["snapshot_json"])
    assert _SNAPSHOT_FORMAT_FIELD not in parsed
    assert parsed == legacy  # byte-for-byte identical contents
    # Legacy raw label is still queryable/readable.
    assert "GET /api/v1/orgs/tourism/tasks/TASK-1" in parsed["http"]


# ---------------------------------------------------------------------------
# 30-day strict-before prune boundary
# ---------------------------------------------------------------------------

def test_prune_boundary_retains_exact_cutoff_and_returns_count(tmp_path: Path) -> None:
    store = MetricsStore(str(tmp_path / "metrics.db"))
    now = datetime(2026, 7, 4, 12, 0, 0, tzinfo=timezone.utc)
    cutoff = (now - timedelta(days=_RETENTION_DAYS)).isoformat()

    older = (now - timedelta(days=_RETENTION_DAYS, seconds=1)).isoformat()
    boundary = cutoff  # exactly AT the cutoff — must be retained

    store.append_snapshot(older, {"n": "older"})
    store.append_snapshot(boundary, {"n": "boundary"})
    store.append_snapshot(now.isoformat(), {"n": "now"})

    deleted = store.prune(cutoff)
    assert deleted == 1  # only the strictly-older row

    rows = store.query()
    assert len(rows) == 2
    remaining = {json.loads(r["snapshot_json"])["n"] for r in rows}
    assert remaining == {"boundary", "now"}


# ---------------------------------------------------------------------------
# Storage telemetry
# ---------------------------------------------------------------------------

def test_telemetry_is_bounded_and_deterministic(tmp_path: Path) -> None:
    store = MetricsStore(str(tmp_path / "metrics.db"))
    now = datetime(2026, 7, 4, 12, 0, 0, tzinfo=timezone.utc)
    store.append_snapshot(now.isoformat(), {"http": {"__all__": {"count": 1, "p50": None, "p95": None, "max": None}, "GET /health": {"count": 1, "p50": 0.1, "p95": 0.1, "max": 0.1}}})

    snapshot = {"http": {"__all__": {"count": 1, "p50": None, "p95": None, "max": None}, "GET /health": {"count": 1, "p50": 0.1, "p95": 0.1, "max": 0.1}}}

    t1 = measure_storage_telemetry(store, snapshot, prune_count=0)
    t2 = measure_storage_telemetry(store, snapshot, prune_count=0)

    assert set(t1.keys()) == {
        "route_label_count",
        "serialized_bytes",
        "row_count",
        "oldest_captured_at",
        "newest_captured_at",
        "prune_count",
        "db_bytes",
        "wal_bytes",
        "page_count",
        "freelist_count",
    }
    assert t1 == t2  # deterministic
    assert t1["route_label_count"] == 1  # excludes __all__
    assert t1["serialized_bytes"] == len(json.dumps(snapshot))
    assert t1["row_count"] == 1
    assert t1["prune_count"] == 0
    assert t1["oldest_captured_at"] == now.isoformat()
    assert t1["newest_captured_at"] == now.isoformat()
    assert isinstance(t1["db_bytes"], int) and t1["db_bytes"] > 0
    assert isinstance(t1["page_count"], int)


def test_telemetry_is_non_sensitive() -> None:
    store = MetricsStore(None)  # in-memory
    snapshot = {
        "http": {
            "__all__": {"count": 1, "p50": None, "p95": None, "max": None},
            "GET /api/v1/orgs/{slug}/tasks/{task_id}/completion": {"count": 1, "p50": 0.1, "p95": 0.1, "max": 0.1},
        }
    }
    t = measure_storage_telemetry(store, snapshot, prune_count=3)

    # Serialize the whole telemetry payload and assert no raw IDs/slugs leak.
    blob = json.dumps(t, sort_keys=True)
    for sensitive in ("TASK-", "THR-", "slug", "tourism", "snapshot_json", "{slug}", "{task_id}"):
        assert sensitive not in blob
    # Values are bounded primitives or None.
    for v in t.values():
        assert v is None or isinstance(v, (int, str))


def test_telemetry_failure_does_not_crash_persist(tmp_path: Path) -> None:
    """A telemetry failure is isolated from persistence: the snapshot is still
    written and no exception escapes maybe_persist_metrics_snapshot."""
    import runtime.daemon.metrics_store as ms

    state = DaemonState.idle(Settings())
    state.metrics_store = MetricsStore(str(tmp_path / "metrics.db"))
    state._last_metrics_snapshot_at = 0.0

    state.metrics_registry.record_http_latency("GET /health", 0.001)
    now = datetime(2026, 7, 4, 12, 0, 0, tzinfo=timezone.utc)

    original = ms.measure_storage_telemetry

    def broken(*args, **kwargs):
        raise RuntimeError("telemetry boom")

    ms.measure_storage_telemetry = broken
    try:
        maybe_persist_metrics_snapshot(state, now)  # must not raise
    finally:
        ms.measure_storage_telemetry = original

    assert len(state.metrics_store.query()) == 1
