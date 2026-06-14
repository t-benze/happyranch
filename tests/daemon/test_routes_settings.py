"""Tests for GET /api/v1/orgs/{slug}/settings — read-only allow-list serializer.

Key invariants:
- The response MUST NOT contain permission_mode, codex_sandbox_mode,
  daemon_bind_host, daemon_port, any feishu* key, or any daemon token.
- The allow-list serializer is load-bearing for secret safety.
- Each system entry carries its own ``value`` + ``restart_required`` as
  part of the GET /settings contract (no client-side hard-coded duplicate).
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


# ----------------------------------------------------------------
# Positive: correct shape
# ----------------------------------------------------------------

def test_settings_returns_200_with_system_and_org(tmp_home, app, org_state, auth_headers) -> None:
    client = TestClient(app)
    r = client.get(
        f"/api/v1/orgs/{org_state.slug}/settings",
        headers=auth_headers,
    )
    assert r.status_code == 200
    body = r.json()
    assert "system" in body
    assert "org" in body

    sys_ = body["system"]
    for key in (
        "claude_cli_path", "codex_cli_path", "opencode_cli_path",
        "pi_cli_path", "session_timeout_seconds", "max_orchestration_steps",
        "queue_workers", "protocol_dir",
    ):
        assert key in sys_, f"missing system field: {key}"
        entry = sys_[key]
        assert isinstance(entry, dict), f"{key} must be a SystemSettingEntry dict"
        assert "value" in entry, f"{key} missing value"
        assert "restart_required" in entry, f"{key} missing restart_required"
        assert isinstance(entry["restart_required"], bool), f"{key}.restart_required must be bool"

    org_ = body["org"]
    for key in ("session_timeout_seconds", "dreaming", "threads"):
        assert key in org_, f"missing org field: {key}"

    # dreaming nested shape
    dreaming = org_["dreaming"]
    assert "schedule" in dreaming
    assert "agents" in dreaming


def test_settings_system_entries_carry_correct_restart_flags(
    tmp_home, app, org_state, auth_headers,
) -> None:
    """Each system entry must have restart_required: true except session_timeout_seconds."""
    client = TestClient(app)
    r = client.get(
        f"/api/v1/orgs/{org_state.slug}/settings",
        headers=auth_headers,
    )
    assert r.status_code == 200
    sys_ = r.json()["system"]

    restart_true = {
        "claude_cli_path", "codex_cli_path", "opencode_cli_path",
        "pi_cli_path", "max_orchestration_steps", "queue_workers", "protocol_dir",
    }
    for key in restart_true:
        assert sys_[key]["restart_required"] is True, f"{key} restart_required must be True"
    assert sys_["session_timeout_seconds"]["restart_required"] is False


def test_settings_requires_auth(tmp_home, app, org_state) -> None:
    client = TestClient(app)
    r = client.get(f"/api/v1/orgs/{org_state.slug}/settings")
    assert r.status_code == 401


def test_settings_unknown_slug_returns_404(tmp_home, app, auth_headers) -> None:
    client = TestClient(app)
    r = client.get("/api/v1/orgs/nope/settings", headers=auth_headers)
    assert r.status_code == 404


# ----------------------------------------------------------------
# Allow-list enforcement: recursive forbidden-key check
# ----------------------------------------------------------------

FORBIDDEN_KEY_PATTERNS = [
    # Secret-level Settings fields that MUST be excluded
    "permission_mode",
    "codex_sandbox_mode",
    "daemon_bind_host",
    "daemon_port",
    # Any feishu key anywhere in the tree
    "feishu",
    # Daemon token / bind / port keys
    "daemon_token",
    "daemon_bind",
    "daemon_port",
    # Additional sensitive fields that must not leak
    "executor_ceiling",
    "executor_launch_spacing",
    "project_root",
]


def _collect_all_keys(obj, prefix: str = "") -> list[str]:
    """Recursively collect every dotted key path in a JSON object/dict."""
    keys: list[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            path = f"{prefix}.{k}" if prefix else k
            keys.append(path)
            keys.extend(_collect_all_keys(v, path))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            path = f"{prefix}[{i}]"
            keys.extend(_collect_all_keys(v, path))
    return keys


def test_settings_response_excludes_all_sensitive_fields(
    tmp_home, app, org_state, auth_headers,
) -> None:
    """Recursively assert NO forbidden key appears anywhere in the response."""
    client = TestClient(app)
    r = client.get(
        f"/api/v1/orgs/{org_state.slug}/settings",
        headers=auth_headers,
    )
    assert r.status_code == 200
    body = r.json()

    all_keys = _collect_all_keys(body)
    # Normalize to lowercase for substring matching against "feishu"
    lower_keys = [k.lower() for k in all_keys]

    violations = []
    for pattern in FORBIDDEN_KEY_PATTERNS:
        for k in all_keys:
            if pattern.lower() in k.lower():
                violations.append(k)
                break

    assert violations == [], (
        f"Forbidden keys found in settings response: {violations}\n"
        f"All keys: {sorted(all_keys)}"
    )

    # Extra hard check: NO key string contains "feishu" (case-insensitive)
    feishu_keys = [k for k in lower_keys if "feishu" in k]
    assert feishu_keys == [], f"Feishu-related keys found: {feishu_keys}"


def test_settings_system_only_has_allow_listed_fields(
    tmp_home, app, org_state, auth_headers,
) -> None:
    """SystemSettingsView must contain ONLY the 8 allow-listed fields."""
    client = TestClient(app)
    r = client.get(
        f"/api/v1/orgs/{org_state.slug}/settings",
        headers=auth_headers,
    )
    assert r.status_code == 200
    system_keys = set(r.json()["system"].keys())

    expected = {
        "claude_cli_path", "codex_cli_path", "opencode_cli_path",
        "pi_cli_path", "session_timeout_seconds", "max_orchestration_steps",
        "queue_workers", "protocol_dir",
    }
    assert system_keys == expected, (
        f"System settings keys: {sorted(system_keys)}\n"
        f"Expected: {sorted(expected)}"
    )


def test_settings_org_only_has_allow_listed_fields(
    tmp_home, app, org_state, auth_headers,
) -> None:
    """OrgSettingsView must contain ONLY session_timeout_seconds, dreaming, threads."""
    client = TestClient(app)
    r = client.get(
        f"/api/v1/orgs/{org_state.slug}/settings",
        headers=auth_headers,
    )
    assert r.status_code == 200
    org_keys = set(r.json()["org"].keys())

    expected = {"session_timeout_seconds", "dreaming", "threads"}
    assert org_keys == expected, (
        f"Org settings keys: {sorted(org_keys)}\n"
        f"Expected: {sorted(expected)}"
    )


# ----------------------------------------------------------------
# SYSTEM config presence (from org config.yaml)
# ----------------------------------------------------------------

def test_settings_reads_org_config_yaml(tmp_home, app, org_state, auth_headers, tmp_path) -> None:
    """If an org/config.yaml exists with values, they must flow into the response."""
    from pathlib import Path
    client = TestClient(app)
    r = client.get(
        f"/api/v1/orgs/{org_state.slug}/settings",
        headers=auth_headers,
    )
    assert r.status_code == 200

    # defaults for alpha org (no config.yaml)
    body = r.json()
    assert body["org"]["session_timeout_seconds"] is None
    assert body["org"]["dreaming"]["enabled"] is False
    assert body["org"]["threads"]["enabled"] is True





# ----------------------------------------------------------------
# Org config with threads fields
# ----------------------------------------------------------------

def test_settings_threads_nested_view(tmp_home, app, org_state, auth_headers) -> None:
    """Threads settings render as a nested object with enabled/default_turn_cap/invocation_timeout_seconds."""
    client = TestClient(app)
    r = client.get(
        f"/api/v1/orgs/{org_state.slug}/settings",
        headers=auth_headers,
    )
    assert r.status_code == 200
    threads = r.json()["org"]["threads"]
    assert set(threads.keys()) == {"enabled", "default_turn_cap", "invocation_timeout_seconds"}
    assert isinstance(threads["enabled"], bool)
    assert isinstance(threads["default_turn_cap"], int)
