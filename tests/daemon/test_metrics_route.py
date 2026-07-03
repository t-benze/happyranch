"""Tests for GET /api/v1/metrics route."""
from __future__ import annotations

from fastapi.testclient import TestClient

from runtime.daemon.app import create_app


def test_metrics_requires_auth(tmp_home, app_idle) -> None:
    client = TestClient(app_idle)
    r = client.get("/api/v1/metrics")
    assert r.status_code == 401


def test_metrics_returns_200_with_auth(tmp_home, app_idle, auth_headers) -> None:
    client = TestClient(app_idle)
    r = client.get("/api/v1/metrics", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert "uptime_seconds" in body
    assert "loops" in body
    assert "http" in body
    # pull-gauges
    assert "tasks" in body
    assert "jobs_in_flight" in body
    assert "executor_sessions_active" in body
    assert "run_step_queue_depth" in body


def test_metrics_idle_state_zero_values(tmp_home, app_idle, auth_headers) -> None:
    client = TestClient(app_idle)
    r = client.get("/api/v1/metrics", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    # With idle state (no orgs), all pull-gauges should be 0
    assert body["uptime_seconds"] >= 0
    assert body["tasks"] == {"pending_and_in_flight": 0}
    assert body["jobs_in_flight"] == 0
    assert body["executor_sessions_active"] == 0
    assert body["run_step_queue_depth"] == 0
    # Scaffolds empty
    assert body["loops"] == {}
    assert body["http"] == {}


def test_metrics_with_org_populated(tmp_home, app, auth_headers) -> None:
    client = TestClient(app)
    r = client.get("/api/v1/metrics", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    # With an org loaded (app fixture has "alpha" org), pull-gauges may be
    # populated.  Tasks/jobs may be zero (empty DB), but the shape must be right.
    assert isinstance(body["uptime_seconds"], float)
    assert body["uptime_seconds"] >= 0
    assert isinstance(body["tasks"], dict)
    assert "pending_and_in_flight" in body["tasks"]
    assert isinstance(body["tasks"]["pending_and_in_flight"], int)
    assert isinstance(body["jobs_in_flight"], int)
    assert isinstance(body["executor_sessions_active"], int)
    assert isinstance(body["run_step_queue_depth"], int)
    assert isinstance(body["loops"], dict)
    assert isinstance(body["http"], dict)
