"""Tests for POST /api/v1/metrics/maintenance (TASK-5443)."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from runtime.config import Settings
from runtime.daemon.app import create_app
from runtime.daemon.metrics_store import MetricsStore
from runtime.daemon.state import DaemonState


def _maintenance_app(tmp_path: Path, rows: list[tuple[str, dict]]) -> TestClient:
    """Build a TestClient on an idle-state app whose metrics_store is a
    file-backed store pre-seeded with *rows* [(captured_at_iso, snapshot)]."""
    db_path = str(tmp_path / "metrics.db")
    store = MetricsStore(db_path)
    for iso, snap in rows:
        store.append_snapshot(iso, snap)
    state = DaemonState.idle(Settings())
    state.metrics_store = store
    return TestClient(create_app(state))


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def test_maintenance_requires_auth(tmp_home, app_idle) -> None:
    client = TestClient(app_idle)
    r = client.post("/api/v1/metrics/maintenance", json={"confirm_quiescent": True})
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# Confirmation refusal
# ---------------------------------------------------------------------------

def test_maintenance_refuses_absent_confirmation(tmp_home, tmp_path, auth_headers) -> None:
    client = _maintenance_app(tmp_path, [])
    r = client.post("/api/v1/metrics/maintenance", headers=auth_headers, json={})
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "confirmation_required"


def test_maintenance_refuses_false_confirmation(tmp_home, tmp_path, auth_headers) -> None:
    client = _maintenance_app(tmp_path, [])
    r = client.post(
        "/api/v1/metrics/maintenance",
        headers=auth_headers,
        json={"confirm_quiescent": False},
    )
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "confirmation_required"


# ---------------------------------------------------------------------------
# Non-quiescence refusal (no mutation)
# ---------------------------------------------------------------------------

def test_maintenance_refuses_when_not_quiescent(app, auth_headers, monkeypatch) -> None:
    """A nonterminal task blocks maintenance with 409 and zero mutation."""
    client = TestClient(app)
    state = app.state.daemon
    org = state.orgs["alpha"]
    monkeypatch.setattr(org.db, "get_nonterminal_task_ids", lambda: ["TASK-1"])

    r = client.post(
        "/api/v1/metrics/maintenance",
        headers=auth_headers,
        json={"confirm_quiescent": True},
    )
    assert r.status_code == 409
    body = r.json()
    assert body["detail"]["code"] == "not_quiescent"
    assert body["detail"]["nonterminal_tasks"] == 1


# ---------------------------------------------------------------------------
# Positive maintenance (old + recent rows; integrity ok)
# ---------------------------------------------------------------------------

def test_maintenance_success_prunes_and_reports(tmp_home, tmp_path, auth_headers) -> None:
    now = datetime.now(timezone.utc)
    old = (now - timedelta(days=60)).isoformat()
    recent = now.isoformat()
    client = _maintenance_app(tmp_path, [(old, {"n": 1}), (recent, {"n": 2})])

    r = client.post(
        "/api/v1/metrics/maintenance",
        headers=auth_headers,
        json={"confirm_quiescent": True},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["pruned_rows"] == 1
    assert body["integrity_check_before_vacuum"] == "ok"
    assert body["integrity_check_after_vacuum"] == "ok"
    assert body["before"]["row_count"] == 2
    assert body["after"]["row_count"] == 1
    assert body["cutoff"]
    assert body["duration_seconds"] >= 0
    assert set(body["checkpoint"].keys()) == {"busy", "log_frames", "checkpointed_frames"}
    # before/after DB + WAL + page + free-list evidence present
    for phase in ("before", "after"):
        assert body[phase]["db_bytes"] > 0
        assert body[phase]["wal_bytes"] >= 0
        assert body[phase]["page_count"] >= 1
        assert body[phase]["freelist_count"] >= 0

    # the retained recent row is still queryable via history (newest-first)
    h = client.get("/api/v1/metrics/history", headers=auth_headers)
    assert h.status_code == 200
    snapshots = h.json()["snapshots"]
    assert len(snapshots) == 1
    assert json.loads(snapshots[0]["snapshot_json"]) == {"n": 2}


# ---------------------------------------------------------------------------
# Failure behavior (no false success; pre-existing history queryable)
# ---------------------------------------------------------------------------

def test_maintenance_failure_no_false_success(tmp_home, tmp_path, auth_headers, monkeypatch) -> None:
    db_path = str(tmp_path / "metrics.db")
    store = MetricsStore(db_path)
    recent = datetime.now(timezone.utc).isoformat()
    store.append_snapshot(recent, {"n": 1})
    monkeypatch.setattr(store, "_integrity_check_locked", lambda: "not ok")

    state = DaemonState.idle(Settings())
    state.metrics_store = store
    client = TestClient(create_app(state))

    r = client.post(
        "/api/v1/metrics/maintenance",
        headers=auth_headers,
        json={"confirm_quiescent": True},
    )
    assert r.status_code == 500
    assert r.json()["detail"]["code"] == "maintenance_failed"

    # pre-existing valid history remains queryable (no teardown / deletion)
    h = client.get("/api/v1/metrics/history", headers=auth_headers)
    assert h.status_code == 200
    assert len(h.json()["snapshots"]) == 1
    assert json.loads(h.json()["snapshots"][0]["snapshot_json"]) == {"n": 1}
