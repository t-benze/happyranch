"""Tests for GET /api/v1/orgs/{slug}/dashboard/summary."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import patch

from fastapi.testclient import TestClient

from runtime.infrastructure.kb_store import KBStore


def test_summary_returns_full_shape(tmp_home, app, org_state, auth_headers) -> None:
    """After projection warm, returns 200 with full shape including generated_at."""
    # Warm the projection before calling the route
    kb_store = KBStore(org_state.root / "kb")
    asyncio.run(org_state.dashboard_projection.warm(
        db=org_state.db, kb_store=kb_store, teams=org_state.teams,
    ))
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
    assert "generated_at" in body
    # generated_at must be non-null after warm
    assert body["generated_at"] is not None
    # generated_at must differ from server_now (response-time vs projection-time)
    assert body["generated_at"] != body["server_now"]


def test_summary_no_projection_returns_503(tmp_home, app, org_state, auth_headers) -> None:
    """Cold-start / no projection yet → 503 (cache-only, no synchronous compose)."""
    # Ensure no projection is in memory
    assert org_state.dashboard_projection.get_projection() is None
    client = TestClient(app)
    r = client.get(
        f"/api/v1/orgs/{org_state.slug}/dashboard/summary",
        headers=auth_headers,
    )
    assert r.status_code == 503
    body = r.json()
    assert "detail" in body
    assert "not yet available" in body["detail"]


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
