"""Tests for team-based task routing (Task 4: retire TaskType, add --team)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.daemon import paths as paths_mod
from src.daemon.app import create_app


@pytest.fixture
def client_and_state(tmp_home, daemon_state):
    """TestClient bound to a runtime-backed app without triggering lifespan."""
    app = create_app(daemon_state)
    client = TestClient(app)
    client.headers.update({"Authorization": f"Bearer {paths_mod.read_token()}"})
    return client, daemon_state


def test_submit_with_engineering_team(client_and_state) -> None:
    client, state = client_and_state
    r = client.post("/api/v1/tasks", json={"team": "engineering", "brief": "x"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["assigned_agent"] == "engineering_head"
    assert body["team"] == "engineering"


def test_submit_with_content_team(client_and_state) -> None:
    client, state = client_and_state
    r = client.post("/api/v1/tasks", json={"team": "content", "brief": "y"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["assigned_agent"] == "content_manager"


def test_submit_with_unknown_team_400s(client_and_state) -> None:
    client, state = client_and_state
    r = client.post("/api/v1/tasks", json={"team": "ops", "brief": "z"})
    assert r.status_code == 400, r.text
    body = r.json()
    assert body["detail"]["code"] == "unknown_team"


def test_submit_without_team_defaults_to_engineering(client_and_state) -> None:
    client, state = client_and_state
    r = client.post("/api/v1/tasks", json={"brief": "default"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["team"] == "engineering"
