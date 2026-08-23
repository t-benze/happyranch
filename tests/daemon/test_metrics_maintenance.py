"""Tests for POST /api/v1/metrics/maintenance (TASK-5443 maintenance slice).

Covers the daemon-owned maintenance admission/drain/exclusivity protocol plus
the ordered prune → checkpoint → integrity → VACUUM sequence and its failure
semantics.
"""
from __future__ import annotations

import json
import os
import shutil
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from runtime.config import Settings
from runtime.daemon.app import create_app
from runtime.daemon.maintenance_gate import MaintenanceGate
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


def _state_of(client: TestClient) -> DaemonState:
    return client.app.state.daemon


# ---------------------------------------------------------------------------
# MaintenanceGate — admission / drain / exclusivity unit tests
# ---------------------------------------------------------------------------

class TestMaintenanceGate:
    def test_atomic_enter_and_overlap_rejection(self) -> None:
        gate = MaintenanceGate()
        assert gate.try_enter_pending() is True
        assert gate.try_enter_pending() is False  # overlap rejected
        gate.release()
        assert gate.try_enter_pending() is True  # re-enterable after release
        gate.release()

    def test_admission_rejected_while_pending(self) -> None:
        gate = MaintenanceGate()
        assert gate.admit() is True
        gate.try_enter_pending()
        assert gate.admit() is False
        gate.release()
        assert gate.admit() is True

    def test_drain_waits_for_admitted_requests(self) -> None:
        gate = MaintenanceGate()
        assert gate.admit() is True
        result: dict[str, object] = {}
        drained = threading.Event()

        def do_drain() -> None:
            result["ok"] = gate.drain(timeout=5.0)
            drained.set()

        t = threading.Thread(target=do_drain)
        t.start()
        time.sleep(0.05)
        assert not drained.is_set()  # still waiting for the in-flight request
        gate.finish()
        assert drained.wait(timeout=5)
        assert result["ok"] is True
        t.join(timeout=5)

    def test_drain_timeout(self) -> None:
        gate = MaintenanceGate()
        gate.admit()
        assert gate.drain(timeout=0.05) is False  # never finished

    def test_release_resets_to_open(self) -> None:
        gate = MaintenanceGate()
        gate.try_enter_pending()
        gate.mark_active()
        assert gate.is_maintenance_in_progress() is True
        gate.release()
        assert gate.is_maintenance_in_progress() is False
        assert gate.admit() is True


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
# Admission / drain / exclusivity at the route boundary
# ---------------------------------------------------------------------------

def test_admission_middleware_rejects_new_traffic_while_gate_closed(
    tmp_home, app_idle, auth_headers
) -> None:
    """While the gate is pending/active, normal traffic is rejected with 503."""
    state = app_idle.state.daemon
    gate = state.maintenance_gate
    client = TestClient(app_idle)
    assert gate.try_enter_pending() is True
    try:
        r = client.get("/api/v1/metrics", headers=auth_headers)
        assert r.status_code == 503
        assert r.json()["detail"]["code"] == "maintenance_in_progress"
    finally:
        gate.release()
    # After release, traffic flows again.
    assert client.get("/api/v1/metrics", headers=auth_headers).status_code == 200


def test_overlap_rejection_second_maintenance_call(
    tmp_home, tmp_path, auth_headers
) -> None:
    """A second maintenance call while one is pending is deterministically
    rejected (409) and does NOT release the first caller's gate."""
    client = _maintenance_app(tmp_path, [])
    state = _state_of(client)
    assert state.maintenance_gate.try_enter_pending() is True
    try:
        r = client.post(
            "/api/v1/metrics/maintenance",
            headers=auth_headers,
            json={"confirm_quiescent": True},
        )
        assert r.status_code == 409
        assert r.json()["detail"]["code"] == "maintenance_in_progress"
        # The gate is still owned by the (simulated) first caller.
        assert state.maintenance_gate.is_maintenance_in_progress() is True
    finally:
        state.maintenance_gate.release()


def test_drain_timeout_releases_gate_and_no_operation(
    tmp_home, tmp_path, auth_headers, monkeypatch
) -> None:
    """If an admitted request never finishes, drain times out (503), the gate
    is released, and no maintenance runs."""
    client = _maintenance_app(tmp_path, [])
    state = _state_of(client)
    # Simulate one stuck in-flight request.
    assert state.maintenance_gate.admit() is True
    monkeypatch.setattr(
        "runtime.daemon.routes.metrics._MAINTENANCE_DRAIN_TIMEOUT_SECONDS", 0.05
    )
    try:
        r = client.post(
            "/api/v1/metrics/maintenance",
            headers=auth_headers,
            json={"confirm_quiescent": True},
        )
        assert r.status_code == 503
        assert r.json()["detail"]["code"] == "drain_timeout"
        # Gate released on the failure path.
        assert state.maintenance_gate.is_maintenance_in_progress() is False
    finally:
        state.maintenance_gate.finish()


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
    assert body["vacuum"] == "ok"
    assert body["before"]["row_count"] == 2
    assert body["after"]["row_count"] == 1
    assert body["cutoff"]
    assert body["duration_seconds"] >= 0
    assert set(body["checkpoint"].keys()) == {"busy", "log_frames", "checkpointed_frames"}
    assert body["checkpoint"]["busy"] == 0
    # before/after DB + WAL + page + free-list + snapshot-bytes + label-count
    for phase in ("before", "after"):
        assert body[phase]["db_bytes"] > 0
        assert body[phase]["wal_bytes"] is None or body[phase]["wal_bytes"] >= 0
        assert body[phase]["page_count"] >= 1
        assert body[phase]["freelist_count"] >= 0
        assert body[phase]["total_snapshot_bytes"] > 0
        assert body[phase]["route_label_count"] >= 0
    # total stored payload shrank (the pruned old row is gone)
    assert body["after"]["total_snapshot_bytes"] < body["before"]["total_snapshot_bytes"]

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
# Failure behavior (no false success; gate released; history queryable)
# ---------------------------------------------------------------------------

def _failure_app(tmp_path: Path, auth_headers, monkeypatch, method: str, fn) -> tuple[TestClient, MetricsStore]:
    db_path = str(tmp_path / "metrics.db")
    store = MetricsStore(db_path)
    recent = datetime.now(timezone.utc).isoformat()
    store.append_snapshot(recent, {"n": 1})
    monkeypatch.setattr(store, method, fn)
    state = DaemonState.idle(Settings())
    state.metrics_store = store
    return TestClient(create_app(state)), store


def test_maintenance_integrity_failure_no_false_success(
    tmp_home, tmp_path, auth_headers, monkeypatch
) -> None:
    client, _ = _failure_app(
        tmp_path, auth_headers, monkeypatch,
        "_integrity_check_locked", lambda: "not ok",
    )
    r = client.post(
        "/api/v1/metrics/maintenance",
        headers=auth_headers,
        json={"confirm_quiescent": True},
    )
    assert r.status_code == 500
    assert r.json()["detail"]["code"] == "maintenance_failed"
    assert "recovery" in r.json()["detail"]

    # gate released; pre-existing valid history remains queryable
    assert _state_of(client).maintenance_gate.is_maintenance_in_progress() is False
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
    assert _state_of(client).maintenance_gate.is_maintenance_in_progress() is False
    h = client.get("/api/v1/metrics/history", headers=auth_headers)
    assert h.status_code == 200
    assert len(h.json()["snapshots"]) == 1


def test_maintenance_vacuum_failure_no_false_success(
    tmp_home, tmp_path, auth_headers, monkeypatch
) -> None:
    client, _ = _failure_app(
        tmp_path, auth_headers, monkeypatch,
        "_vacuum_locked",
        lambda: (_ for _ in ()).throw(RuntimeError("vacuum boom")),
    )
    r = client.post(
        "/api/v1/metrics/maintenance",
        headers=auth_headers,
        json={"confirm_quiescent": True},
    )
    assert r.status_code == 500
    assert r.json()["detail"]["code"] == "maintenance_failed"
    assert _state_of(client).maintenance_gate.is_maintenance_in_progress() is False
    h = client.get("/api/v1/metrics/history", headers=auth_headers)
    assert h.status_code == 200
    assert len(h.json()["snapshots"]) == 1


def test_maintenance_checkpoint_failure_no_false_success(
    tmp_home, tmp_path, auth_headers, monkeypatch
) -> None:
    client, _ = _failure_app(
        tmp_path, auth_headers, monkeypatch,
        "_wal_checkpoint_locked",
        lambda: (_ for _ in ()).throw(RuntimeError("checkpoint boom")),
    )
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


def test_maintenance_busy_checkpoint_fails_closed(
    tmp_home, tmp_path, auth_headers, monkeypatch
) -> None:
    """A checkpoint that reports ``busy != 0`` is a structured failure."""
    client, _ = _failure_app(
        tmp_path, auth_headers, monkeypatch,
        "_wal_checkpoint_locked",
        lambda: {"busy": 1, "log_frames": 1, "checkpointed_frames": 0},
    )
    r = client.post(
        "/api/v1/metrics/maintenance",
        headers=auth_headers,
        json={"confirm_quiescent": True},
    )
    assert r.status_code == 500
    assert r.json()["detail"]["code"] == "maintenance_failed"
    assert "busy" in r.json()["detail"]["detail"]
    assert _state_of(client).maintenance_gate.is_maintenance_in_progress() is False


def test_post_failure_fresh_retry_succeeds(
    tmp_home, tmp_path, auth_headers, monkeypatch
) -> None:
    """After a failure releases the gate, a fresh explicit invocation succeeds."""
    db_path = str(tmp_path / "metrics.db")
    store = MetricsStore(db_path)
    recent = datetime.now(timezone.utc).isoformat()
    store.append_snapshot(recent, {"n": 1})

    real_vacuum = store._vacuum_locked
    calls = {"n": 0}

    def flaky_vacuum():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("first vacuum boom")
        return real_vacuum()

    monkeypatch.setattr(store, "_vacuum_locked", flaky_vacuum)

    state = DaemonState.idle(Settings())
    state.metrics_store = store
    client = TestClient(create_app(state))

    r1 = client.post(
        "/api/v1/metrics/maintenance",
        headers=auth_headers,
        json={"confirm_quiescent": True},
    )
    assert r1.status_code == 500
    assert _state_of(client).maintenance_gate.is_maintenance_in_progress() is False

    # Fresh invocation (no automatic retry happened) now succeeds.
    r2 = client.post(
        "/api/v1/metrics/maintenance",
        headers=auth_headers,
        json={"confirm_quiescent": True},
    )
    assert r2.status_code == 200
    assert r2.json()["vacuum"] == "ok"


# ---------------------------------------------------------------------------
# Legacy readability
# ---------------------------------------------------------------------------

def test_legacy_history_readable_after_maintenance(
    tmp_home, tmp_path, auth_headers
) -> None:
    """A legacy row (no format_version marker) stays byte-identical and readable
    after maintenance — never rewritten in place."""
    now = datetime.now(timezone.utc)
    legacy_snap = {"http": {"__all__": {}, "GET /old/path": {}}, "tasks": {"pending_and_in_flight": 0}}
    client = _maintenance_app(tmp_path, [(now.isoformat(), legacy_snap)])

    r = client.post(
        "/api/v1/metrics/maintenance",
        headers=auth_headers,
        json={"confirm_quiescent": True},
    )
    assert r.status_code == 200

    h = client.get("/api/v1/metrics/history", headers=auth_headers)
    snapshots = h.json()["snapshots"]
    assert len(snapshots) == 1
    assert json.loads(snapshots[0]["snapshot_json"]) == legacy_snap
    assert "format_version" not in json.loads(snapshots[0]["snapshot_json"])


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
    never raw task IDs, thread IDs, org slugs, or snapshot content."""
    now = datetime.now(timezone.utc)
    client = _maintenance_app(tmp_path, [(now.isoformat(), {"n": 1})])
    r = client.post(
        "/api/v1/metrics/maintenance",
        headers=auth_headers,
        json={"confirm_quiescent": True},
    )
    assert r.status_code == 200
    blob = json.dumps(r.json(), sort_keys=True)
    for sensitive in ("TASK-", "THR-", "slug", "tourism", "snapshot_json", "thread", "JOB-"):
        assert sensitive not in blob
