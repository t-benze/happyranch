"""Tests for GET /api/v1/metrics/history route (THR-066 PR-2)."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from runtime.config import Settings
from runtime.daemon.metrics_store import MetricsStore
from runtime.daemon.state import DaemonState
from runtime.daemon.app import create_app


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_cross_thread_store(db_path: str | None) -> MetricsStore:
    """Create a MetricsStore whose internal connection supports cross-thread
    access (check_same_thread=False).  Needed because TestClient dispatches
    HTTP handlers to a thread-pool worker, not the test thread."""
    store = MetricsStore.__new__(MetricsStore)
    store._db_path = db_path
    store._conn = sqlite3.connect(
        db_path if db_path is not None else ":memory:",
        check_same_thread=False,
    )
    store._conn.row_factory = sqlite3.Row
    store._conn.execute("PRAGMA journal_mode=WAL")
    store._init_schema()
    return store


def _make_app_with_store(tmp_path: Path, auth_headers) -> TestClient:
    """Create a TestClient on an app whose daemon state has a file-backed
    MetricsStore pre-seeded with three known rows (n=1, n=2, n=3)."""
    db_path = str(tmp_path / "metrics.db")
    store = _make_cross_thread_store(db_path)

    t1 = datetime(2026, 7, 4, 12, 0, 0, tzinfo=timezone.utc)
    t2 = datetime(2026, 7, 4, 12, 5, 0, tzinfo=timezone.utc)
    t3 = datetime(2026, 7, 4, 12, 10, 0, tzinfo=timezone.utc)
    store.append_snapshot(t1.isoformat(), {"n": 1})
    store.append_snapshot(t2.isoformat(), {"n": 2})
    store.append_snapshot(t3.isoformat(), {"n": 3})

    state = DaemonState.idle(Settings())
    state.metrics_store = store
    app = create_app(state)
    return TestClient(app)


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def test_history_requires_auth(tmp_home, app_idle) -> None:
    client = TestClient(app_idle)
    r = client.get("/api/v1/metrics/history")
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# Empty / idle store returns graceful empty, never 500
# ---------------------------------------------------------------------------

def test_history_idle_returns_empty(tmp_home, auth_headers) -> None:
    """Idle state's in-memory store has no rows; returns empty snapshots."""
    state = DaemonState.idle(Settings())
    # Replace with cross-thread in-memory store so TestClient handlers can query
    store = _make_cross_thread_store(None)
    state.metrics_store = store
    app = create_app(state)
    client = TestClient(app)
    r = client.get("/api/v1/metrics/history", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert "snapshots" in body
    assert body["snapshots"] == []


# ---------------------------------------------------------------------------
# Persisted rows return newest-first
# ---------------------------------------------------------------------------

def test_history_newest_first(tmp_home, tmp_path, auth_headers) -> None:
    client = _make_app_with_store(tmp_path, auth_headers)
    r = client.get("/api/v1/metrics/history", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert len(body["snapshots"]) == 3
    # newest first
    assert json.loads(body["snapshots"][0]["snapshot_json"]) == {"n": 3}
    assert json.loads(body["snapshots"][1]["snapshot_json"]) == {"n": 2}
    assert json.loads(body["snapshots"][2]["snapshot_json"]) == {"n": 1}


# ---------------------------------------------------------------------------
# since / until filtering
# ---------------------------------------------------------------------------

def test_history_since_filter(tmp_home, tmp_path, auth_headers) -> None:
    client = _make_app_with_store(tmp_path, auth_headers)
    since = datetime(2026, 7, 4, 12, 5, 0, tzinfo=timezone.utc)
    r = client.get("/api/v1/metrics/history", headers=auth_headers,
                   params={"since": since.isoformat()})
    assert r.status_code == 200
    body = r.json()
    assert len(body["snapshots"]) == 2
    assert json.loads(body["snapshots"][0]["snapshot_json"]) == {"n": 3}


def test_history_until_filter(tmp_home, tmp_path, auth_headers) -> None:
    client = _make_app_with_store(tmp_path, auth_headers)
    until = datetime(2026, 7, 4, 12, 5, 0, tzinfo=timezone.utc)
    r = client.get("/api/v1/metrics/history", headers=auth_headers,
                   params={"until": until.isoformat()})
    assert r.status_code == 200
    body = r.json()
    assert len(body["snapshots"]) == 2  # t1 and t2
    assert json.loads(body["snapshots"][0]["snapshot_json"]) == {"n": 2}


def test_history_since_and_until_filter(tmp_home, tmp_path, auth_headers) -> None:
    client = _make_app_with_store(tmp_path, auth_headers)
    since = datetime(2026, 7, 4, 12, 0, 0, tzinfo=timezone.utc)
    until = datetime(2026, 7, 4, 12, 5, 0, tzinfo=timezone.utc)
    r = client.get("/api/v1/metrics/history", headers=auth_headers,
                   params={"since": since.isoformat(), "until": until.isoformat()})
    assert r.status_code == 200
    body = r.json()
    assert len(body["snapshots"]) == 2  # t1 and t2


# ---------------------------------------------------------------------------
# limit
# ---------------------------------------------------------------------------

def test_history_limit_honored(tmp_home, tmp_path, auth_headers) -> None:
    """limit param caps returned rows."""
    db_path = str(tmp_path / "metrics.db")
    store = _make_cross_thread_store(db_path)
    for i in range(10):
        t = datetime(2026, 7, 4, 12, i, 0, tzinfo=timezone.utc)
        store.append_snapshot(t.isoformat(), {"n": i})

    state = DaemonState.idle(Settings())
    state.metrics_store = store
    app = create_app(state)
    client = TestClient(app)
    r = client.get("/api/v1/metrics/history", headers=auth_headers,
                   params={"limit": 4})
    assert r.status_code == 200
    body = r.json()
    assert len(body["snapshots"]) == 4
    assert json.loads(body["snapshots"][0]["snapshot_json"]) == {"n": 9}


def test_history_limit_clamped_to_max(tmp_home, tmp_path, auth_headers) -> None:
    """limit > max (5000) is rejected by FastAPI Query(le=5000) with 422."""
    db_path = str(tmp_path / "metrics.db")
    store = _make_cross_thread_store(db_path)
    for i in range(10):
        t = datetime(2026, 7, 4, 12, i, 0, tzinfo=timezone.utc)
        store.append_snapshot(t.isoformat(), {"n": i})

    state = DaemonState.idle(Settings())
    state.metrics_store = store
    app = create_app(state)
    client = TestClient(app)
    r = client.get("/api/v1/metrics/history", headers=auth_headers,
                   params={"limit": 9999})
    # FastAPI validates le=5000, so this returns 422
    assert r.status_code == 422


def test_history_limit_non_positive_rejected(tmp_home, tmp_path, auth_headers) -> None:
    """limit <= 0 is rejected with 422 (FastAPI ge=1 validation)."""
    db_path = str(tmp_path / "metrics.db")
    store = _make_cross_thread_store(db_path)

    state = DaemonState.idle(Settings())
    state.metrics_store = store
    app = create_app(state)
    client = TestClient(app)

    r = client.get("/api/v1/metrics/history", headers=auth_headers,
                   params={"limit": 0})
    assert r.status_code == 422

    r = client.get("/api/v1/metrics/history", headers=auth_headers,
                   params={"limit": -1})
    assert r.status_code == 422


def test_history_default_limit(tmp_home, tmp_path, auth_headers) -> None:
    """When limit is not provided, defaults to 500."""
    db_path = str(tmp_path / "metrics.db")
    store = _make_cross_thread_store(db_path)
    for i in range(600):
        t = datetime(2026, 7, 4, 12, 0, 0, tzinfo=timezone.utc)
        store.append_snapshot(t.isoformat(), {"n": i})

    state = DaemonState.idle(Settings())
    state.metrics_store = store
    app = create_app(state)
    client = TestClient(app)
    r = client.get("/api/v1/metrics/history", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert len(body["snapshots"]) == 500  # default limit


# ---------------------------------------------------------------------------
# metrics_store is None (not idle — actually None) returns empty
# ---------------------------------------------------------------------------

def test_history_none_store_returns_empty(tmp_home, auth_headers) -> None:
    """When DaemonState.metrics_store is None, return empty gracefully."""
    state = DaemonState(runtime=None, settings=Settings())
    # Deliberately set to None (bypassing __post_init__ / idle constructor)
    state.metrics_store = None
    app = create_app(state)
    client = TestClient(app)
    r = client.get("/api/v1/metrics/history", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["snapshots"] == []


# ---------------------------------------------------------------------------
# Response shape
# ---------------------------------------------------------------------------

def test_history_response_shape(tmp_home, tmp_path, auth_headers) -> None:
    """Each snapshot row has id, captured_at, snapshot_json keys."""
    db_path = str(tmp_path / "metrics.db")
    store = _make_cross_thread_store(db_path)
    t1 = datetime(2026, 7, 4, 12, 0, 0, tzinfo=timezone.utc)
    store.append_snapshot(t1.isoformat(), {"uptime_seconds": 100.0})

    state = DaemonState.idle(Settings())
    state.metrics_store = store
    app = create_app(state)
    client = TestClient(app)
    r = client.get("/api/v1/metrics/history", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert "snapshots" in body
    assert len(body["snapshots"]) == 1
    row = body["snapshots"][0]
    assert isinstance(row["id"], int)
    assert isinstance(row["captured_at"], str)
    assert isinstance(row["snapshot_json"], str)
    assert json.loads(row["snapshot_json"]) == {"uptime_seconds": 100.0}
