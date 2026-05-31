"""Tests for GET /api/v1/orgs/{slug}/dashboard/summary."""
from __future__ import annotations

from fastapi.testclient import TestClient


def test_summary_returns_full_shape(tmp_home, app, org_state, auth_headers) -> None:
    client = TestClient(app)
    r = client.get(
        f"/api/v1/orgs/{org_state.slug}/dashboard/summary",
        headers=auth_headers,
    )
    assert r.status_code == 200
    body = r.json()
    assert "heartbeat" in body
    assert len(body["heartbeat"]) == 24
    assert "narrative_counts" in body
    assert set(body["narrative_counts"].keys()) == {
        "completed_today", "failed_today", "escalated_open",
        "kb_added_today", "agents_active_now", "spend_today_usd",
    }
    assert "escalations" in body
    assert "active_by_team" in body
    assert "recent_activity" in body
    assert "updates_this_week" in body
    assert "org_pulse" in body
    assert "org_age_days" in body
    assert "server_now" in body


def test_summary_unknown_slug_returns_404(tmp_home, app, auth_headers) -> None:
    client = TestClient(app)
    r = client.get(
        "/api/v1/orgs/nope/dashboard/summary",
        headers=auth_headers,
    )
    assert r.status_code == 404


def test_summary_requires_auth(tmp_home, app, org_state) -> None:
    client = TestClient(app)
    r = client.get(f"/api/v1/orgs/{org_state.slug}/dashboard/summary")
    assert r.status_code == 401
