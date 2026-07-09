"""Tests for GET /api/v1/health/prereqs — executor CLI readiness."""
from __future__ import annotations

from fastapi.testclient import TestClient

from runtime.daemon.routes.health import _set_presence_checker


def test_prereqs_requires_no_auth(app_idle) -> None:
    """The prereqs endpoint is public — no bearer token needed."""
    client = TestClient(app_idle)
    r = client.get("/api/v1/health/prereqs")
    assert r.status_code == 200
    body = r.json()
    assert "prereqs" in body
    # The 4 built-in profiles must always be present.
    tools = {e["tool"] for e in body["prereqs"]}
    assert {"claude", "codex", "opencode", "pi"}.issubset(tools)


def test_prereqs_all_present_when_mocked() -> None:
    """When the mock reports every CLI present, all entries are present=True."""

    # Mock presence: every CLI is found at a fake path.
    def _mock_present(cli: str) -> str:
        return f"/fake/path/{cli}"

    _set_presence_checker(_mock_present)
    try:
        from runtime.daemon.app import create_app
        from runtime.daemon.state import DaemonState
        from runtime.config import Settings

        app = create_app(DaemonState.idle(Settings()))
        client = TestClient(app)
        r = client.get("/api/v1/health/prereqs")
        assert r.status_code == 200
        body = r.json()
        for entry in body["prereqs"]:
            assert entry["present"] is True, f"{entry['tool']} should be present"
            assert entry["path"] is not None
            assert entry["path"].startswith("/fake/path/")
            assert isinstance(entry["hint"], str)
            assert len(entry["hint"]) > 0
    finally:
        # Restore default so other tests are not affected.
        import shutil

        _set_presence_checker(shutil.which)


def test_prereqs_all_absent_when_mocked() -> None:
    """When the mock reports every CLI absent, all entries are present=False."""

    def _mock_absent(_cli: str) -> None:
        return None

    _set_presence_checker(_mock_absent)
    try:
        from runtime.daemon.app import create_app
        from runtime.daemon.state import DaemonState
        from runtime.config import Settings

        app = create_app(DaemonState.idle(Settings()))
        client = TestClient(app)
        r = client.get("/api/v1/health/prereqs")
        assert r.status_code == 200
        body = r.json()
        for entry in body["prereqs"]:
            assert entry["present"] is False, f"{entry['tool']} should be absent"
            assert entry["path"] is None
            assert isinstance(entry["hint"], str)
            assert len(entry["hint"]) > 0
    finally:
        import shutil

        _set_presence_checker(shutil.which)


def test_prereqs_with_runtime_includes_custom_profiles(app, tmp_home, auth_headers) -> None:
    """When an org with custom profiles is loaded, those profiles appear in prereqs.

    Because the idle app has no orgs, only built-ins show. With a runtime
    backed app, the 'alpha' org is loaded but has no custom profiles by
    default, so only built-ins appear — but the route still works.
    """
    client = TestClient(app)
    r = client.get("/api/v1/health/prereqs", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    tools = {e["tool"] for e in body["prereqs"]}
    # The 4 built-ins must always be present.
    assert {"claude", "codex", "opencode", "pi"}.issubset(tools)


def test_prereqs_response_shape() -> None:
    """Each prereq entry has exactly the documented fields and no extra."""
    from runtime.daemon.app import create_app
    from runtime.daemon.state import DaemonState
    from runtime.config import Settings

    app = create_app(DaemonState.idle(Settings()))
    client = TestClient(app)
    r = client.get("/api/v1/health/prereqs")
    assert r.status_code == 200
    body = r.json()
    for entry in body["prereqs"]:
        assert set(entry.keys()) == {"tool", "present", "path", "hint"}
        assert isinstance(entry["tool"], str)
        assert isinstance(entry["present"], bool)
        assert entry["path"] is None or isinstance(entry["path"], str)
        assert isinstance(entry["hint"], str)


def test_prereqs_uses_daemon_settings_not_global_settings() -> None:
    """health_prereqs must resolve CLI paths from the daemon's Settings,
    NOT the module-global settings singleton.

    Bug: health_prereqs() passed the module-global `_settings` to
    `_get_cli_binary`, so a daemon configured with a non-default CLI path
    (e.g. Settings(codex_cli_path='custom-codex')) would probe the default
    binary name 'codex' instead of the configured 'custom-codex'.
    """
    from runtime.daemon.app import create_app
    from runtime.daemon.state import DaemonState
    from runtime.config import Settings

    # Build a daemon whose settings carry a custom codex CLI path.
    custom_settings = Settings(codex_cli_path="custom-codex")
    daemon = DaemonState.idle(custom_settings)
    app = create_app(daemon)

    # Install a fake presence checker that records every CLI string it is
    # asked to resolve. This records at the source: if the route uses the
    # global singleton, we will see 'codex'; if it uses the daemon's
    # settings, we will see 'custom-codex'.
    seen: list[str] = []

    def _recording_checker(cli: str) -> str | None:
        seen.append(cli)
        return f"/fake/install/{cli}"

    _set_presence_checker(_recording_checker)
    try:
        client = TestClient(app)
        r = client.get("/api/v1/health/prereqs")
        assert r.status_code == 200

        # The checker must have been asked about 'custom-codex' — the
        # daemon-configured path — and NOT about the default 'codex'.
        assert "custom-codex" in seen, (
            f"Expected presence checker to be called with 'custom-codex', "
            f"but saw: {seen}"
        )
        assert "codex" not in seen, (
            f"Presence checker should NOT be called with the default 'codex' "
            f"when daemon settings override it. Saw: {seen}"
        )
    finally:
        import shutil

        _set_presence_checker(shutil.which)
