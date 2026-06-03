"""Tests for the SPA static mount + fallback handler."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from runtime.config import Settings
from runtime.daemon import paths
from runtime.daemon.app import create_app
from runtime.daemon.state import DaemonState


@pytest.fixture
def app_with_dist(tmp_path, monkeypatch):
    monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    paths.ensure_token()
    dist = tmp_path / "web" / "dist"
    dist.mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>HappyRanch</title>")
    (dist / "assets").mkdir()
    (dist / "assets" / "app.js").write_text("console.log('happyranch');")
    monkeypatch.setenv("HAPPYRANCH_WEB_DIST", str(dist))
    state = DaemonState.idle(Settings())
    app = create_app(state)
    return TestClient(app)


def test_serves_index_at_root(app_with_dist):
    r = app_with_dist.get("/")
    assert r.status_code == 200
    assert "<title>HappyRanch</title>" in r.text


def test_serves_static_assets(app_with_dist):
    r = app_with_dist.get("/assets/app.js")
    assert r.status_code == 200
    assert "console.log" in r.text


def test_spa_fallback_returns_index_for_unknown_path(app_with_dist):
    r = app_with_dist.get("/orgs/foo/threads")
    assert r.status_code == 200
    assert "<title>HappyRanch</title>" in r.text


def test_api_routes_still_404_cleanly(app_with_dist):
    # /api/v1/* must NOT be swallowed by the SPA fallback.
    r = app_with_dist.get("/api/v1/does-not-exist")
    assert r.status_code == 404


def test_no_dist_renders_placeholder(tmp_path, monkeypatch):
    monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path))
    paths.ensure_token()
    monkeypatch.setenv("HAPPYRANCH_WEB_DIST", str(tmp_path / "nonexistent"))
    state = DaemonState.idle(Settings())
    app = create_app(state)
    client = TestClient(app)
    r = client.get("/")
    assert r.status_code == 200
    assert "build_web.sh" in r.text
