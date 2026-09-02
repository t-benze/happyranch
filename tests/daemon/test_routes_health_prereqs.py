"""Tests for GET /api/v1/health/prereqs — executor CLI readiness."""
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient


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


def test_prereqs_all_registered_when_all_valid(tmp_path: Path) -> None:
    """When the machine-local registry has valid entries for every built-in,
    all entries show present=True with the stored path."""
    import os as _os
    import json
    import stat

    _os.environ["HAPPYRANCH_DAEMON_HOME"] = str(tmp_path)

    # Create fake executables and register all four built-ins.
    entries: dict[str, str] = {}
    for tool in ("claude", "codex", "opencode", "pi"):
        fake = tmp_path / f"fake-{tool}"
        fake.write_text("#!/bin/sh\necho fake")
        fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
        entries[tool] = str(fake)
    (tmp_path / "executors.json").write_text(json.dumps(entries))

    try:
        from runtime.daemon.app import create_app
        from runtime.daemon.state import DaemonState
        from runtime.config import Settings

        app = create_app(DaemonState.idle(Settings()))
        client = TestClient(app)
        r = client.get("/api/v1/health/prereqs")
        assert r.status_code == 200
        body = r.json()
        # The 4 built-in profiles must all be present.
        builtins = [e for e in body["prereqs"] if e["tool"] in {"claude", "codex", "opencode", "pi"}]
        assert len(builtins) == 4, f"Expected 4 built-in prereqs, got {len(builtins)}"
        for entry in builtins:
            assert entry["present"] is True, f"{entry['tool']} should be present"
            assert entry["path"] is not None
            assert entry["path"].startswith(str(tmp_path))
            assert isinstance(entry["hint"], str)
            assert len(entry["hint"]) > 0
    finally:
        del _os.environ["HAPPYRANCH_DAEMON_HOME"]


def test_prereqs_all_absent_when_none_registered(tmp_path: Path) -> None:
    """When the machine-local registry is empty (no executors.json),
    all entries are present=False, path=None."""
    import os as _os

    _os.environ["HAPPYRANCH_DAEMON_HOME"] = str(tmp_path)

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
        del _os.environ["HAPPYRANCH_DAEMON_HOME"]


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


def test_prereqs_empty_registry_all_absent(tmp_path: Path) -> None:
    """Empty/fresh machine-local executor binary registry => every built-in
    shows present=false, path=None.

    The prereqs route must read from the machine-local executor binary
    registry (get_binary + is_binary_valid), NOT shutil.which. On a fresh
    runtime with nothing registered, every built-in must show present=false.
    """
    import os as _os

    _os.environ["HAPPYRANCH_DAEMON_HOME"] = str(tmp_path)

    try:
        from runtime.daemon.app import create_app
        from runtime.daemon.state import DaemonState
        from runtime.config import Settings

        app = create_app(DaemonState.idle(Settings()))
        client = TestClient(app)
        r = client.get("/api/v1/health/prereqs")
        assert r.status_code == 200
        body = r.json()
        tools = {e["tool"] for e in body["prereqs"]}
        assert {"claude", "codex", "opencode", "pi"}.issubset(tools)
        for entry in body["prereqs"]:
            assert entry["present"] is False, (
                f"{entry['tool']} should show present=false in empty registry"
            )
            assert entry["path"] is None
            assert isinstance(entry["hint"], str)
            assert len(entry["hint"]) > 0
            # Hint must mention registration, not PATH installation.
            assert "register" in entry["hint"].lower(), (
                f"hint for {entry['tool']} must mention registration: {entry['hint']}"
            )
    finally:
        del _os.environ["HAPPYRANCH_DAEMON_HOME"]


def test_prereqs_registered_one_kind_only_that_present(tmp_path: Path) -> None:
    """After registering ONE executor kind via the machine-local registry,
    only that kind shows present=true; all others remain present=false.
    """
    import os as _os
    import json
    import stat

    _os.environ["HAPPYRANCH_DAEMON_HOME"] = str(tmp_path)

    # Create a fake executable binary so is_binary_valid() passes.
    fake_claude = tmp_path / "fake-claude"
    fake_claude.write_text("#!/bin/sh\necho fake")
    fake_claude.chmod(fake_claude.stat().st_mode | stat.S_IEXEC)

    # Write the registry with only claude registered.
    (tmp_path / "executors.json").write_text(
        json.dumps({"claude": str(fake_claude)})
    )

    try:
        from runtime.daemon.app import create_app
        from runtime.daemon.state import DaemonState
        from runtime.config import Settings

        app = create_app(DaemonState.idle(Settings()))
        client = TestClient(app)
        r = client.get("/api/v1/health/prereqs")
        assert r.status_code == 200
        body = r.json()

        # Claude should be present=true with the registered path.
        claude_entry = next(
            e for e in body["prereqs"] if e["tool"] == "claude"
        )
        assert claude_entry["present"] is True, (
            "claude should be present (registered)"
        )
        assert claude_entry["path"] == str(fake_claude)
        assert isinstance(claude_entry["hint"], str)

        # All others must be present=false.
        for entry in body["prereqs"]:
            if entry["tool"] != "claude":
                assert entry["present"] is False, (
                    f"{entry['tool']} should be absent (not registered)"
                )
                assert entry["path"] is None
    finally:
        del _os.environ["HAPPYRANCH_DAEMON_HOME"]


def test_prereqs_uses_daemon_settings_not_global_settings(tmp_path: Path) -> None:
    """health_prereqs enumerates built-ins independently of Settings CLI
    metadata (THR-107 seq155 fix-forward).

    A daemon configured with an empty CLI path (e.g. ``codex_cli_path=''``)
    must STILL emit the codex built-in prereq — with ``present`` determined
    by the machine-local binary registry, not the optional Settings metadata.
    """
    import os as _os

    _os.environ["HAPPYRANCH_DAEMON_HOME"] = str(tmp_path)

    from runtime.daemon.app import create_app
    from runtime.daemon.state import DaemonState
    from runtime.config import Settings

    # Build a daemon whose settings omit codex default command metadata.
    custom_settings = Settings(codex_cli_path="")
    daemon = DaemonState.idle(custom_settings)
    app = create_app(daemon)

    try:
        client = TestClient(app)
        r = client.get("/api/v1/health/prereqs")
        assert r.status_code == 200
        body = r.json()
        tools = {e["tool"] for e in body["prereqs"]}

        # codex MUST appear — the Settings gate that suppressed it is removed.
        # Presence is still false (no registry pin), but the built-in is visible.
        assert "codex" in tools, (
            f"codex should appear even when daemon settings have empty "
            f"codex_cli_path. Got tools: {tools}"
        )
        codex = next(e for e in body["prereqs"] if e["tool"] == "codex")
        assert codex["present"] is False, (
            "codex should be present=false when no registry pin exists"
        )
        assert codex["path"] is None
        # All four built-ins must be visible.
        assert {"claude", "codex", "opencode", "pi"}.issubset(tools)
    finally:
        del _os.environ["HAPPYRANCH_DAEMON_HOME"]


def test_builtin_visible_with_valid_pin_despite_blank_settings_metadata(
    tmp_path: Path,
) -> None:
    """A built-in with a valid executors.json pin must remain present/visible
    even when the optional Settings CLI metadata is blank.

    THR-107 seq155 fix-forward: optional Settings CLI path fields are default
    command metadata only — they must not suppress a valid registry pin.
    """
    import os as _os
    import json
    import stat

    _os.environ["HAPPYRANCH_DAEMON_HOME"] = str(tmp_path)

    # Create a fake executable and register codex in the binary registry.
    fake_codex = tmp_path / "fake-codex"
    fake_codex.write_text("#!/bin/sh\necho fake")
    fake_codex.chmod(fake_codex.stat().st_mode | stat.S_IEXEC)
    (tmp_path / "executors.json").write_text(
        json.dumps({"codex": str(fake_codex)})
    )

    try:
        from runtime.daemon.app import create_app
        from runtime.daemon.state import DaemonState
        from runtime.config import Settings

        # Settings has blank codex_cli_path — the old code would skip codex.
        custom_settings = Settings(codex_cli_path="")
        daemon = DaemonState.idle(custom_settings)
        app = create_app(daemon)
        client = TestClient(app)
        r = client.get("/api/v1/health/prereqs")
        assert r.status_code == 200
        body = r.json()

        codex = next(
            (e for e in body["prereqs"] if e["tool"] == "codex"), None
        )
        assert codex is not None, (
            "codex must appear despite blank codex_cli_path — "
            "valid registry pin controls presence"
        )
        assert codex["present"] is True, (
            f"codex should be present=true with valid registry pin, "
            f"got present={codex['present']}"
        )
        assert codex["path"] == str(fake_codex), (
            f"codex path must be the registered path, got {codex['path']}"
        )
        assert isinstance(codex["hint"], str)
        assert len(codex["hint"]) > 0
    finally:
        del _os.environ["HAPPYRANCH_DAEMON_HOME"]
