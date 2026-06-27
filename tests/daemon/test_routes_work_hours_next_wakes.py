"""Tests for GET /work-hours/next-wakes — the server-side next-N-wake preview
that reuses the scheduler slot grid for an agent's RESOLVED effective schedule.

THR-035 / TASK-967. Additive read-only endpoint.
"""
from __future__ import annotations

import yaml
from fastapi.testclient import TestClient

from runtime.orchestrator._paths import OrgPaths


def _seed_working_hours(org_state, block: dict) -> None:
    p = OrgPaths(root=org_state.root).org_config_path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml.safe_dump({"working_hours": block}))


def test_next_wakes_returns_resolved_continuous_slots(
    tmp_home, app, org_state, auth_headers,
) -> None:
    _seed_working_hours(org_state, {
        "enabled": True,
        "default": {"mode": "continuous", "window": {"timezone": "UTC"}, "interval": "2h"},
    })
    client = TestClient(app)
    r = client.get(
        f"/api/v1/orgs/{org_state.slug}/work-hours/next-wakes",
        headers=auth_headers,
        params={"agent": "dev_agent", "count": 3},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["agent"] == "dev_agent"
    assert body["enabled"] is True
    assert body["mode"] == "continuous"
    assert body["error"] is None
    assert len(body["next_wakes"]) == 3
    # each entry is an ISO-8601 timestamp
    assert all("T" in t for t in body["next_wakes"])


def test_next_wakes_requires_auth(tmp_home, app, org_state) -> None:
    client = TestClient(app)
    r = client.get(
        f"/api/v1/orgs/{org_state.slug}/work-hours/next-wakes",
        params={"agent": "dev_agent"},
    )
    assert r.status_code == 401


def test_next_wakes_incomplete_schedule_returns_error_not_500(
    tmp_home, app, org_state, auth_headers,
) -> None:
    # windowed default missing days/window -> resolve_for raises OrgConfigError;
    # the endpoint surfaces it as a 200 with an error string + empty wakes.
    _seed_working_hours(org_state, {
        "enabled": True,
        "default": {"mode": "windowed", "window": {"timezone": "UTC"}, "interval": "1h"},
    })
    client = TestClient(app)
    r = client.get(
        f"/api/v1/orgs/{org_state.slug}/work-hours/next-wakes",
        headers=auth_headers,
        params={"agent": "dev_agent"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["next_wakes"] == []
    assert body["error"] is not None


def test_next_wakes_route_not_shadowed_by_id_route(
    tmp_home, app, org_state, auth_headers,
) -> None:
    """`next-wakes` must resolve to the preview endpoint, not the
    `/work-hours/{work_hour_id}` show route (which would 404)."""
    _seed_working_hours(org_state, {
        "enabled": True,
        "default": {"mode": "continuous", "window": {"timezone": "UTC"}, "interval": "6h"},
    })
    client = TestClient(app)
    r = client.get(
        f"/api/v1/orgs/{org_state.slug}/work-hours/next-wakes",
        headers=auth_headers,
        params={"agent": "dev_agent"},
    )
    assert r.status_code == 200
    assert "next_wakes" in r.json()
