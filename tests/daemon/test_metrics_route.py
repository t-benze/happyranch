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
    # http{} may be empty on the very first request because the timing
    # middleware records after call_next() returns — the /metrics route
    # handler reads the snapshot before this request's latency is stored.
    assert isinstance(body["http"], dict)


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
    assert body["loops"] == {}
    assert isinstance(body["http"], dict)


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


def test_metrics_http_accumulates_over_requests(tmp_home, app_idle, auth_headers) -> None:
    """After multiple requests, the http histogram for the metrics route accumulates."""
    client = TestClient(app_idle)
    for _ in range(5):
        r = client.get("/api/v1/metrics", headers=auth_headers)
        assert r.status_code == 200
    # After 5 requests, the metrics route should have count >= 5
    # (the 6th request reads the snapshot, which sees the 5 prior requests)
    r = client.get("/api/v1/metrics", headers=auth_headers)
    body = r.json()
    assert body["http"]["GET /api/v1/metrics"]["count"] == 5
    # Aggregate bucket should also have 5 entries
    assert body["http"]["__all__"]["count"] == 5


def test_metrics_http_multiple_routes_tracked(tmp_home, app_idle, auth_headers) -> None:
    """Different routes are tracked independently in the http histogram."""
    client = TestClient(app_idle)
    # Hit /health (unauthed) and /metrics (authed)
    client.get("/api/v1/health")
    client.get("/api/v1/metrics", headers=auth_headers)
    # The third request reads the snapshot, which now includes the prior two.
    r = client.get("/api/v1/metrics", headers=auth_headers)
    body = r.json()
    assert "GET /api/v1/health" in body["http"]
    assert "GET /api/v1/metrics" in body["http"]
    assert body["http"]["GET /api/v1/health"]["count"] >= 1
    assert body["http"]["GET /api/v1/metrics"]["count"] >= 1
    # Aggregate bucket spans both routes
    assert "__all__" in body["http"]
    assert body["http"]["__all__"]["count"] >= 2
    assert body["http"]["__all__"]["p50"] is not None
    assert body["http"]["__all__"]["p95"] is not None
    assert body["http"]["__all__"]["max"] is not None


# ---------------------------------------------------------------------------
# Route-template labels through the real HTTP middleware (TASK-5443)
# ---------------------------------------------------------------------------

def test_http_dynamic_ids_coalesce_to_template(tmp_home, app_idle, auth_headers) -> None:
    """Two distinct slugs for the same handler coalesce into ONE histogram
    keyed by the route template; no raw slug leaks into any label."""
    client = TestClient(app_idle)
    # Idle state → the org-scoped route raises 409 (no_active_runtime) but
    # routing still resolves the template; the middleware labels by template.
    client.get("/api/v1/orgs/foo/agents", headers=auth_headers)
    client.get("/api/v1/orgs/bar/agents", headers=auth_headers)

    r = client.get("/api/v1/metrics", headers=auth_headers)
    body = r.json()
    template = "GET /api/v1/orgs/{slug}/agents"
    assert template in body["http"]
    assert body["http"][template]["count"] == 2
    # no literal org slug ever becomes a label
    assert not any("foo" in k or "bar" in k for k in body["http"])


def test_http_unmatched_route_uses_bounded_fallback(tmp_home, app_idle, auth_headers) -> None:
    """A request that matches no route records the stable 'METHOD __unmatched__'
    fallback, never a literal URL path."""
    client = TestClient(app_idle)
    client.get("/api/v1/definitely-not-a-route", headers=auth_headers)
    r = client.get("/api/v1/metrics", headers=auth_headers)
    body = r.json()
    assert "GET __unmatched__" in body["http"]
    assert body["http"]["GET __unmatched__"]["count"] == 1
    # the literal path never becomes a label
    assert not any("definitely-not-a-route" in k for k in body["http"])
