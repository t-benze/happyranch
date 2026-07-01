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


def test_web_dist_resolves_via_project_root_not_file_location(tmp_path, monkeypatch):
    """GH #254: web/dist MUST resolve from the configured project_root,
    NOT from __file__ (which varies depending on which copy of `runtime`
    was imported). Without HAPPYRANCH_WEB_DIST set, the dist is found
    at project_root/web/dist."""
    monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    paths.ensure_token()
    # Populate web/dist under a custom project_root
    project_root = tmp_path / "custom_project"
    dist = project_root / "web" / "dist"
    dist.mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>Canonical</title>")
    (dist / "assets").mkdir()
    (dist / "assets" / "app.js").write_text("console.log('canonical');")
    # Do NOT set HAPPYRANCH_WEB_DIST — dist must be found via project_root
    settings = Settings(project_root=project_root)
    state = DaemonState.idle(settings)
    app = create_app(state)
    client = TestClient(app)
    r = client.get("/")
    assert r.status_code == 200
    assert "Canonical" in r.text


def test_web_dist_override_still_highest_precedence(tmp_path, monkeypatch):
    """HAPPYRANCH_WEB_DIST MUST remain the highest-precedence override.
    Even when a web/dist exists at project_root, the override wins."""
    monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    paths.ensure_token()
    # Set up web/dist at project_root (should be ignored)
    project_root = tmp_path / "project"
    (project_root / "web" / "dist").mkdir(parents=True)
    (project_root / "web" / "dist" / "index.html").write_text("<title>WRONG</title>")
    # Set up web/dist at an override location
    override_dist = tmp_path / "override" / "dist"
    override_dist.mkdir(parents=True)
    (override_dist / "index.html").write_text("<!doctype html><title>OVERRIDE</title>")
    (override_dist / "assets").mkdir()
    monkeypatch.setenv("HAPPYRANCH_WEB_DIST", str(override_dist))
    settings = Settings(project_root=project_root)
    state = DaemonState.idle(settings)
    app = create_app(state)
    client = TestClient(app)
    r = client.get("/")
    assert r.status_code == 200
    assert "OVERRIDE" in r.text
