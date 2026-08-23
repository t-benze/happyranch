"""Tests for POST /api/v1/metrics/maintenance (TASK-5443 maintenance slice)."""
from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
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
    assert body["detail"]["running_jobs"] == 0
    assert body["detail"]["active_executor_sessions"] == 0


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
        assert body[phase]["wal_bytes"] is None or body[phase]["wal_bytes"] >= 0
        assert body[phase]["page_count"] >= 1
        assert body[phase]["freelist_count"] >= 0

    # the retained recent row is still queryable via history (newest-first)
    h = client.get("/api/v1/metrics/history", headers=auth_headers)
    assert h.status_code == 200
    snapshots = h.json()["snapshots"]
    assert len(snapshots) == 1
    assert json.loads(snapshots[0]["snapshot_json"]) == {"n": 2}


def test_maintenance_applies_30day_cutoff(tmp_home, tmp_path, auth_headers) -> None:
    """The route prunes at the unchanged 30-day cutoff: a 29-day-old row
    survives while a 31-day-old row is pruned (exact strict-before boundary is
    covered deterministically at the store level with an explicit cutoff)."""
    now = datetime.now(timezone.utc)
    keep = (now - timedelta(days=29)).isoformat()
    drop = (now - timedelta(days=31)).isoformat()
    client = _maintenance_app(
        tmp_path,
        [(drop, {"n": "drop"}), (keep, {"n": "keep"})],
    )

    r = client.post(
        "/api/v1/metrics/maintenance",
        headers=auth_headers,
        json={"confirm_quiescent": True},
    )
    assert r.status_code == 200
    assert r.json()["pruned_rows"] == 1

    h = client.get("/api/v1/metrics/history", headers=auth_headers)
    remaining = {json.loads(s["snapshot_json"])["n"] for s in h.json()["snapshots"]}
    assert remaining == {"keep"}


# ---------------------------------------------------------------------------
# Failure behavior (no false success; pre-existing history queryable)
# ---------------------------------------------------------------------------

def test_maintenance_integrity_failure_no_false_success(
    tmp_home, tmp_path, auth_headers, monkeypatch
) -> None:
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
    assert "recovery" in r.json()["detail"]

    # pre-existing valid history remains queryable (no teardown / deletion)
    h = client.get("/api/v1/metrics/history", headers=auth_headers)
    assert h.status_code == 200
    assert len(h.json()["snapshots"]) == 1
    assert json.loads(h.json()["snapshots"][0]["snapshot_json"]) == {"n": 1}


def test_maintenance_post_vacuum_integrity_failure(
    tmp_home, tmp_path, auth_headers, monkeypatch
) -> None:
    """A non-`ok` integrity check AFTER VACUUM is also fail-closed (500)."""
    db_path = str(tmp_path / "metrics.db")
    store = MetricsStore(db_path)
    store.append_snapshot(datetime.now(timezone.utc).isoformat(), {"n": 1})
    original = store._integrity_check_locked
    calls = {"n": 0}

    def flip():
        calls["n"] += 1
        return "ok" if calls["n"] == 1 else "not ok"

    monkeypatch.setattr(store, "_integrity_check_locked", flip)

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
    # history still queryable
    h = client.get("/api/v1/metrics/history", headers=auth_headers)
    assert h.status_code == 200
    assert len(h.json()["snapshots"]) == 1


def test_maintenance_vacuum_failure_no_false_success(
    tmp_home, tmp_path, auth_headers, monkeypatch
) -> None:
    """A VACUUM error returns 500 and leaves history queryable."""
    db_path = str(tmp_path / "metrics.db")
    store = MetricsStore(db_path)
    store.append_snapshot(datetime.now(timezone.utc).isoformat(), {"n": 1})

    def boom():
        raise RuntimeError("vacuum boom")

    monkeypatch.setattr(store, "_vacuum_locked", boom)

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

    h = client.get("/api/v1/metrics/history", headers=auth_headers)
    assert h.status_code == 200
    assert len(h.json()["snapshots"]) == 1


def test_maintenance_checkpoint_failure_no_false_success(
    tmp_home, tmp_path, auth_headers, monkeypatch
) -> None:
    """A WAL-checkpoint error returns 500 and leaves history queryable."""
    db_path = str(tmp_path / "metrics.db")
    store = MetricsStore(db_path)
    store.append_snapshot(datetime.now(timezone.utc).isoformat(), {"n": 1})

    def boom():
        raise RuntimeError("checkpoint boom")

    monkeypatch.setattr(store, "_wal_checkpoint_locked", boom)

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

    h = client.get("/api/v1/metrics/history", headers=auth_headers)
    assert h.status_code == 200
    assert len(h.json()["snapshots"]) == 1


# ---------------------------------------------------------------------------
# No raw filesystem-deletion route
# ---------------------------------------------------------------------------

def test_maintenance_never_deletes_files_at_fs_level(
    tmp_home, tmp_path, auth_headers, monkeypatch
) -> None:
    """Maintenance goes through SQLite only — no os.remove/unlink/rmtree."""
    db_path = tmp_path / "metrics.db"
    store = MetricsStore(str(db_path))
    store.append_snapshot(datetime.now(timezone.utc).isoformat(), {"n": 1})

    def _boom(*a, **k):
        raise AssertionError("filesystem deletion is forbidden")

    monkeypatch.setattr(os, "remove", _boom)
    monkeypatch.setattr(os, "unlink", _boom)
    monkeypatch.setattr(shutil, "rmtree", _boom)

    state = DaemonState.idle(Settings())
    state.metrics_store = store
    client = TestClient(create_app(state))

    r = client.post(
        "/api/v1/metrics/maintenance",
        headers=auth_headers,
        json={"confirm_quiescent": True},
    )
    assert r.status_code == 200
    assert db_path.exists()  # never deleted or recreated by hand


# ---------------------------------------------------------------------------
# Telemetry redaction
# ---------------------------------------------------------------------------

def test_maintenance_report_is_non_sensitive(tmp_home, tmp_path, auth_headers) -> None:
    """The maintenance report contains only counts/bytes/timestamps/outcomes —
    never raw task IDs, thread IDs, or org slugs."""
    now = datetime.now(timezone.utc)
    client = _maintenance_app(tmp_path, [(now.isoformat(), {"n": 1})])
    r = client.post(
        "/api/v1/metrics/maintenance",
        headers=auth_headers,
        json={"confirm_quiescent": True},
    )
    assert r.status_code == 200
    blob = json.dumps(r.json(), sort_keys=True)
    for sensitive in ("TASK-", "THR-", "slug", "tourism", "snapshot_json", "thread"):
        assert sensitive not in blob
