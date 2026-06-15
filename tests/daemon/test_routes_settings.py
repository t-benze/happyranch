"""Tests for GET /api/v1/orgs/{slug}/settings + PUT /settings/org.

Key invariants:
- The response MUST NOT contain permission_mode, codex_sandbox_mode,
  daemon_bind_host, daemon_port, any feishu* key, or any daemon token.
- The allow-list serializer is load-bearing for secret safety.
- Each system entry carries its own ``value`` + ``restart_required`` as
  part of the GET /settings contract (no client-side hard-coded duplicate).
- PUT updates only allow-listed keys; unknown keys are carried through.
- PUT extra='forbid' rejects sensitive keys with 422.
- PUT validates agent names against the resolved agent list.
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


# ----------------------------------------------------------------
# PUT /settings/org — Phase 2 editable org settings
# ----------------------------------------------------------------

def test_put_org_settings_updates_and_returns_snapshot(
    tmp_home, app, org_state, auth_headers, tmp_path,
) -> None:
    """PUT /settings/org updates the config and returns the updated snapshot."""
    client = TestClient(app)
    r = client.put(
        f"/api/v1/orgs/{org_state.slug}/settings/org",
        headers=auth_headers,
        json={"session_timeout_seconds": 7200},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["org"]["session_timeout_seconds"] == 7200
    # Verify it persisted via GET
    r2 = client.get(
        f"/api/v1/orgs/{org_state.slug}/settings",
        headers=auth_headers,
    )
    assert r2.json()["org"]["session_timeout_seconds"] == 7200


def test_put_org_settings_requires_auth(tmp_home, app, org_state) -> None:
    """PUT /settings/org must reject unauthenticated requests."""
    client = TestClient(app)
    r = client.put(
        f"/api/v1/orgs/{org_state.slug}/settings/org",
        json={"session_timeout_seconds": 100},
    )
    assert r.status_code == 401


def test_put_org_settings_unknown_slug_returns_404(
    tmp_home, app, auth_headers,
) -> None:
    """PUT /settings/org must 404 for unknown orgs."""
    client = TestClient(app)
    r = client.put(
        "/api/v1/orgs/nope/settings/org",
        headers=auth_headers,
        json={"session_timeout_seconds": 100},
    )
    assert r.status_code == 404


def test_put_org_settings_rejects_feishu_key(
    tmp_home, app, org_state, auth_headers,
) -> None:
    """extra='forbid' must reject feishu_notifications with 422."""
    client = TestClient(app)
    r = client.put(
        f"/api/v1/orgs/{org_state.slug}/settings/org",
        headers=auth_headers,
        json={"feishu_notifications": {"chat_id": "test"}},
    )
    assert r.status_code == 422


def test_put_org_settings_rejects_unknown_key(
    tmp_home, app, org_state, auth_headers,
) -> None:
    """extra='forbid' must reject any unknown key (e.g. working_hours) with 422."""
    client = TestClient(app)
    r = client.put(
        f"/api/v1/orgs/{org_state.slug}/settings/org",
        headers=auth_headers,
        json={"working_hours": {"enabled": True}},
    )
    assert r.status_code == 422


def test_put_org_settings_rejects_negative_session_timeout(
    tmp_home, app, org_state, auth_headers,
) -> None:
    """session_timeout_seconds must be positive."""
    client = TestClient(app)
    r = client.put(
        f"/api/v1/orgs/{org_state.slug}/settings/org",
        headers=auth_headers,
        json={"session_timeout_seconds": 0},
    )
    assert r.status_code == 422


def test_put_org_settings_rejects_bad_threads_default_turn_cap(
    tmp_home, app, org_state, auth_headers,
) -> None:
    """threads.default_turn_cap must be positive."""
    client = TestClient(app)
    r = client.put(
        f"/api/v1/orgs/{org_state.slug}/settings/org",
        headers=auth_headers,
        json={"threads": {"default_turn_cap": -5}},
    )
    assert r.status_code == 422


def test_put_org_settings_updates_dreaming(
    tmp_home, app, org_state, auth_headers,
) -> None:
    """PUT with a dreaming block updates only dreaming, leaves threads + timeout alone."""
    client = TestClient(app)

    # Set a known baseline
    r0 = client.put(
        f"/api/v1/orgs/{org_state.slug}/settings/org",
        headers=auth_headers,
        json={"session_timeout_seconds": 3600},
    )
    assert r0.status_code == 200

    # Update dreaming only
    r = client.put(
        f"/api/v1/orgs/{org_state.slug}/settings/org",
        headers=auth_headers,
        json={
            "dreaming": {
                "enabled": True,
                "schedule": {"time": "03:00", "timezone": "America/New_York"},
                "catch_up_on_startup": False,
                "agents": {"mode": "whitelist", "include": [], "exclude": []},
            },
        },
    )
    assert r.status_code == 200
    body = r.json()["org"]
    assert body["dreaming"]["enabled"] is True
    assert body["dreaming"]["schedule"]["time"] == "03:00"
    assert body["dreaming"]["schedule"]["timezone"] == "America/New_York"
    assert body["dreaming"]["catch_up_on_startup"] is False
    assert body["dreaming"]["agents"]["mode"] == "whitelist"
    # session_timeout_seconds should still be 3600 (not touched)
    assert body["session_timeout_seconds"] == 3600
    # threads defaults should still be present
    assert body["threads"]["enabled"] is True


def test_put_org_settings_preserves_unmanaged_blocks(
    tmp_home, app, org_state, auth_headers, tmp_path,
) -> None:
    """If org/config.yaml has a working_hours block before the PUT, it must survive."""
    import yaml

    # The org_state fixture writes config.yaml into the org root on disk.
    # We add working_hours + feishu_notifications to that existing file.
    from pathlib import Path
    config_path = Path(org_state.root) / "org" / "config.yaml"
    raw = yaml.safe_load(config_path.read_text()) if config_path.exists() else {}
    raw["working_hours"] = {"enabled": True, "default": {"mode": "continuous", "timezone": "UTC", "interval": "1h"}}
    raw["feishu_notifications"] = {"chat_id": "secret-chat"}
    config_path.write_text(yaml.safe_dump(raw))

    client = TestClient(app)
    r = client.put(
        f"/api/v1/orgs/{org_state.slug}/settings/org",
        headers=auth_headers,
        json={"session_timeout_seconds": 999},
    )
    assert r.status_code == 200

    # Read the config back — unmanaged blocks must still be there
    raw2 = yaml.safe_load(config_path.read_text())
    assert raw2.get("working_hours") == {"enabled": True, "default": {"mode": "continuous", "timezone": "UTC", "interval": "1h"}}
    assert raw2.get("feishu_notifications") == {"chat_id": "secret-chat"}
    assert raw2.get("session_timeout_seconds") == 999


def test_put_org_settings_no_sensitive_keys_in_response(
    tmp_home, app, org_state, auth_headers,
) -> None:
    """After a PUT, the response must still exclude all sensitive keys."""
    client = TestClient(app)
    r = client.put(
        f"/api/v1/orgs/{org_state.slug}/settings/org",
        headers=auth_headers,
        json={"session_timeout_seconds": 500},
    )
    assert r.status_code == 200
    body = r.json()

    all_keys = _collect_all_keys(body)
    violations = []
    for pattern in FORBIDDEN_KEY_PATTERNS:
        for k in all_keys:
            if pattern.lower() in k.lower():
                violations.append(k)
                break
    assert violations == [], f"Forbidden keys in PUT response: {violations}"
    feishu_keys = [k for k in all_keys if "feishu" in k.lower()]
    assert feishu_keys == [], f"Feishu keys in PUT response: {feishu_keys}"


def test_put_org_settings_partial_update_only_touches_given_keys(
    tmp_home, app, org_state, auth_headers,
) -> None:
    """When only session_timeout_seconds is sent, dreaming + threads are unchanged."""
    client = TestClient(app)

    # Get baseline
    r0 = client.get(
        f"/api/v1/orgs/{org_state.slug}/settings",
        headers=auth_headers,
    )
    baseline = r0.json()["org"]

    # Update only session_timeout_seconds
    r = client.put(
        f"/api/v1/orgs/{org_state.slug}/settings/org",
        headers=auth_headers,
        json={"session_timeout_seconds": 42},
    )
    assert r.status_code == 200
    body = r.json()["org"]
    assert body["session_timeout_seconds"] == 42
    # dreaming + threads unchanged
    assert body["dreaming"] == baseline["dreaming"]
    assert body["threads"] == baseline["threads"]


def test_put_org_settings_rejects_bad_timezone(
    tmp_home, app, org_state, auth_headers,
) -> None:
    """Dreaming schedule timezone must be a valid IANA timezone."""
    client = TestClient(app)
    r = client.put(
        f"/api/v1/orgs/{org_state.slug}/settings/org",
        headers=auth_headers,
        json={"dreaming": {"schedule": {"timezone": "Mars/Olympus"}}},
    )
    assert r.status_code == 422


def test_put_org_settings_rejects_bad_agent_mode(
    tmp_home, app, org_state, auth_headers,
) -> None:
    """Dreaming agents mode must be 'all' or 'whitelist'."""
    client = TestClient(app)
    r = client.put(
        f"/api/v1/orgs/{org_state.slug}/settings/org",
        headers=auth_headers,
        json={"dreaming": {"agents": {"mode": "blocklist"}}},
    )
    assert r.status_code == 422


# ----------------------------------------------------------------
# Finding 1 regression: deep-merge preserves sibling leaves
# ----------------------------------------------------------------

def test_put_org_settings_deep_merge_preserves_sibling_leaves(
    tmp_home, app, org_state, auth_headers,
) -> None:
    """When patching ONE leaf of dreaming and ONE leaf of threads,
    every unpatched sibling leaf survives in the persisted YAML and on reload."""
    import yaml
    from pathlib import Path

    client = TestClient(app)
    config_path = Path(org_state.root) / "org" / "config.yaml"

    # Seed a fully-populated config
    raw = yaml.safe_load(config_path.read_text()) if config_path.exists() else {}
    raw["dreaming"] = {
        "enabled": False,
        "schedule": {"time": "06:00", "timezone": "Asia/Shanghai", "catch_up_on_startup": False},
        "agents": {"mode": "whitelist", "include": ["dev_agent"], "exclude": ["qa_engineer"]},
    }
    raw["threads"] = {
        "enabled": True,
        "default_turn_cap": 100,
        "invocation_timeout_seconds": 900,
    }
    raw["session_timeout_seconds"] = 3600
    config_path.write_text(yaml.safe_dump(raw))

    # Patch ONLY dreaming.enabled and threads.default_turn_cap
    r = client.put(
        f"/api/v1/orgs/{org_state.slug}/settings/org",
        headers=auth_headers,
        json={
            "dreaming": {"enabled": True},
            "threads": {"default_turn_cap": 200},
        },
    )
    assert r.status_code == 200
    body = r.json()["org"]

    # Response: patched values should match
    assert body["dreaming"]["enabled"] is True
    assert body["threads"]["default_turn_cap"] == 200

    # Response: unpatched sibling leaves must survive
    assert body["dreaming"]["schedule"]["time"] == "06:00"
    assert body["dreaming"]["schedule"]["timezone"] == "Asia/Shanghai"
    assert body["dreaming"]["catch_up_on_startup"] is False
    assert body["dreaming"]["agents"]["mode"] == "whitelist"
    assert body["dreaming"]["agents"]["include"] == ["dev_agent"]
    assert body["dreaming"]["agents"]["exclude"] == ["qa_engineer"]
    assert body["threads"]["enabled"] is True
    assert body["threads"]["invocation_timeout_seconds"] == 900
    assert body["session_timeout_seconds"] == 3600

    # Persisted YAML: unpatched sibling leaves must survive
    raw2 = yaml.safe_load(config_path.read_text())
    assert raw2["dreaming"]["enabled"] is True
    assert raw2["dreaming"]["schedule"] == {"time": "06:00", "timezone": "Asia/Shanghai", "catch_up_on_startup": False}
    assert raw2["dreaming"]["agents"] == {"mode": "whitelist", "include": ["dev_agent"], "exclude": ["qa_engineer"]}
    assert raw2["threads"] == {"enabled": True, "default_turn_cap": 200, "invocation_timeout_seconds": 900}


def test_put_org_settings_deep_merge_nested_partial_schedule(
    tmp_home, app, org_state, auth_headers,
) -> None:
    """Patching only one field inside dreaming.schedule leaves the other field intact."""
    import yaml
    from pathlib import Path

    client = TestClient(app)
    config_path = Path(org_state.root) / "org" / "config.yaml"

    raw = yaml.safe_load(config_path.read_text()) if config_path.exists() else {}
    raw["dreaming"] = {
        "enabled": True,
        "schedule": {"time": "02:00", "timezone": "UTC", "catch_up_on_startup": True},
        "agents": {"mode": "all", "include": [], "exclude": []},
    }
    config_path.write_text(yaml.safe_dump(raw))

    # Patch only dreaming.schedule.time
    r = client.put(
        f"/api/v1/orgs/{org_state.slug}/settings/org",
        headers=auth_headers,
        json={"dreaming": {"schedule": {"time": "08:00"}}},
    )
    assert r.status_code == 200
    body = r.json()["org"]

    assert body["dreaming"]["schedule"]["time"] == "08:00"
    # timezone and catch_up must survive
    assert body["dreaming"]["schedule"]["timezone"] == "UTC"
    assert body["dreaming"]["catch_up_on_startup"] is True

    # Persisted YAML
    raw2 = yaml.safe_load(config_path.read_text())
    assert raw2["dreaming"]["schedule"] == {"time": "08:00", "timezone": "UTC", "catch_up_on_startup": True}


# ----------------------------------------------------------------
# Finding 2 regression: nullable fields can be cleared
# ----------------------------------------------------------------

def test_put_org_settings_clears_session_timeout_via_explicit_null(
    tmp_home, app, org_state, auth_headers,
) -> None:
    """Sending explicit null for session_timeout_seconds clears the override."""
    client = TestClient(app)

    # First set a timeout
    r0 = client.put(
        f"/api/v1/orgs/{org_state.slug}/settings/org",
        headers=auth_headers,
        json={"session_timeout_seconds": 7200},
    )
    assert r0.status_code == 200
    assert r0.json()["org"]["session_timeout_seconds"] == 7200

    # Now clear it with explicit null
    r = client.put(
        f"/api/v1/orgs/{org_state.slug}/settings/org",
        headers=auth_headers,
        json={"session_timeout_seconds": None},
    )
    assert r.status_code == 200
    # After clearing, it should be None (reverting to system default)
    assert r.json()["org"]["session_timeout_seconds"] is None

    # Verify persisted
    r2 = client.get(
        f"/api/v1/orgs/{org_state.slug}/settings",
        headers=auth_headers,
    )
    assert r2.json()["org"]["session_timeout_seconds"] is None


def test_put_org_settings_clears_threads_invocation_timeout_via_explicit_null(
    tmp_home, app, org_state, auth_headers,
) -> None:
    """Sending explicit null for threads.invocation_timeout_seconds clears the override."""
    client = TestClient(app)

    r = client.put(
        f"/api/v1/orgs/{org_state.slug}/settings/org",
        headers=auth_headers,
        json={"threads": {"invocation_timeout_seconds": None}},
    )
    assert r.status_code == 200
    assert r.json()["org"]["threads"]["invocation_timeout_seconds"] is None


def test_put_org_settings_omitted_session_timeout_does_not_clear(
    tmp_home, app, org_state, auth_headers,
) -> None:
    """Omitting session_timeout_seconds entirely must NOT clear an existing override."""
    client = TestClient(app)

    # Set a timeout
    r0 = client.put(
        f"/api/v1/orgs/{org_state.slug}/settings/org",
        headers=auth_headers,
        json={"session_timeout_seconds": 7200},
    )
    assert r0.status_code == 200

    # Send a patch with dreaming only (session_timeout_seconds omitted)
    r = client.put(
        f"/api/v1/orgs/{org_state.slug}/settings/org",
        headers=auth_headers,
        json={"dreaming": {"enabled": True}},
    )
    assert r.status_code == 200
    # session_timeout_seconds should STILL be 7200
    assert r.json()["org"]["session_timeout_seconds"] == 7200


# ----------------------------------------------------------------
# PUT /settings/teams — Phase 2 teams membership editing
# ----------------------------------------------------------------

import pytest


def _seed_agent_file(paths, name: str, team: str, role: str = "worker") -> None:
    """Write a minimal agent file into the org's agents directory."""
    from textwrap import dedent
    path = paths.agents_dir / f"{name}.md"
    paths.agents_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(dedent(f"""\
        ---
        name: {name}
        team: {team}
        role: {role}
        executor: claude
        allow_rules: []
        repos:
          happyranch: https://github.com/t-benze/happyranch
        enrolled_by: founder
        enrolled_at_task: TASK-001
        enrolled_at: 2026-01-01T00:00:00Z
        system_prompt: test
        ---
        # {name}
        Test agent.
        """))


@pytest.mark.anyio
def test_put_teams_add_and_remove_workers(
    tmp_home, app, org_state, auth_headers,
) -> None:
    """PUT /settings/teams can add workers to a team.

    Removal of a worker whose agent file still declares that team triggers
    409 + rollback so the teams.yaml worker list is restored.
    """
    from pathlib import Path as _Path
    import yaml as _yaml
    from runtime.orchestrator._paths import OrgPaths

    client = TestClient(app)
    paths = OrgPaths(root=org_state.root)

    # Seed agent files for all seeded workers + manager
    _seed_agent_file(paths, "qa_engineer", "engineering")
    _seed_agent_file(paths, "product_manager", "engineering")
    _seed_agent_file(paths, "engineering_head", "engineering", role="manager")
    _seed_agent_file(paths, "dev_agent", "engineering")
    _seed_agent_file(paths, "payment_agent", "engineering")
    _seed_agent_file(paths, "content_manager", "content", role="manager")
    _seed_agent_file(paths, "content_writer", "content")
    _seed_agent_file(paths, "content_qa", "content")
    _seed_agent_file(paths, "seo_agent", "content")

    # Add a new worker to engineering
    r = client.put(
        f"/api/v1/orgs/{org_state.slug}/settings/teams",
        headers=auth_headers,
        json={"team": "engineering", "add_workers": ["qa_engineer"]},
    )
    assert r.status_code == 200
    teams = r.json()["teams"]
    eng = next(t for t in teams if t["name"] == "engineering")
    assert "qa_engineer" in eng["workers"]
    assert "product_manager" in eng["workers"]

    # Remove product_manager (agent file still declares team=engineering)
    # This should trigger 409 + rollback
    r2 = client.put(
        f"/api/v1/orgs/{org_state.slug}/settings/teams",
        headers=auth_headers,
        json={"team": "engineering", "remove_workers": ["product_manager"]},
    )
    assert r2.status_code == 409
    detail = r2.json().get("detail", {})
    assert "teams_consistency_drift" in str(detail.get("code", "")) or \
           "teams_worker_agent_drift" in str(detail.get("code", ""))

    # Teams.yaml worker set must be restored to its original value (rollback)
    teams_path_g = _Path(org_state.root) / "org" / "teams.yaml"
    loaded_g = _yaml.safe_load(teams_path_g.read_text())
    workers_g = loaded_g["teams"]["engineering"]["workers"]
    assert "product_manager" in workers_g
    assert "qa_engineer" in workers_g


@pytest.mark.anyio
def test_put_teams_requires_auth(tmp_home, app, org_state) -> None:
    """PUT /settings/teams must reject unauthenticated requests."""
    client = TestClient(app)
    r = client.put(
        f"/api/v1/orgs/{org_state.slug}/settings/teams",
        json={"team": "engineering", "add_workers": ["someone"]},
    )
    assert r.status_code == 401


@pytest.mark.anyio
def test_put_teams_unknown_team_returns_404(
    tmp_home, app, org_state, auth_headers,
) -> None:
    """PUT /settings/teams must 404 for unknown teams."""
    client = TestClient(app)
    r = client.put(
        f"/api/v1/orgs/{org_state.slug}/settings/teams",
        headers=auth_headers,
        json={"team": "nonexistent", "add_workers": ["someone"]},
    )
    assert r.status_code == 404


@pytest.mark.anyio
def test_put_teams_extra_forbidden(
    tmp_home, app, org_state, auth_headers,
) -> None:
    """PUT /settings/teams extra='forbid' rejects unknown fields."""
    client = TestClient(app)
    r = client.put(
        f"/api/v1/orgs/{org_state.slug}/settings/teams",
        headers=auth_headers,
        json={"team": "engineering", "manager": "new_manager"},
    )
    assert r.status_code == 422


@pytest.mark.anyio
def test_put_teams_noop_is_idempotent(
    tmp_home, app, org_state, auth_headers,
) -> None:
    """Re-adding an existing worker is a no-op."""
    from runtime.orchestrator._paths import OrgPaths
    paths = OrgPaths(root=org_state.root)
    # Seed agent files for ALL seeded workers
    _seed_agent_file(paths, "engineering_head", "engineering", role="manager")
    _seed_agent_file(paths, "product_manager", "engineering")
    _seed_agent_file(paths, "dev_agent", "engineering")
    _seed_agent_file(paths, "payment_agent", "engineering")
    _seed_agent_file(paths, "qa_engineer", "engineering")
    _seed_agent_file(paths, "content_manager", "content", role="manager")
    _seed_agent_file(paths, "content_writer", "content")
    _seed_agent_file(paths, "content_qa", "content")
    _seed_agent_file(paths, "seo_agent", "content")

    client = TestClient(app)
    r = client.put(
        f"/api/v1/orgs/{org_state.slug}/settings/teams",
        headers=auth_headers,
        json={"team": "engineering", "add_workers": ["product_manager"]},
    )
    assert r.status_code == 200
    teams = r.json()["teams"]
    eng = next(t for t in teams if t["name"] == "engineering")
    assert "product_manager" in eng["workers"]


# ----------------------------------------------------------------
# No-sensitive-keys: agents response (Phase 2 additive fields)
# ----------------------------------------------------------------

AGENTS_FORBIDDEN_KEY_PATTERNS = FORBIDDEN_KEY_PATTERNS + ["allow_rules"]


def test_agents_response_excludes_all_sensitive_fields(
    tmp_home, app, org_state, auth_headers,
) -> None:
    """Recursively assert NO sensitive key (allow_rules, permission_mode, etc.)
    appears anywhere in the GET /agents response."""
    client = TestClient(app)
    r = client.get(
        f"/api/v1/orgs/{org_state.slug}/agents",
        headers=auth_headers,
    )
    assert r.status_code == 200
    body = r.json()

    all_keys = _collect_all_keys(body)
    lower_keys = [k.lower() for k in all_keys]

    violations = []
    for pattern in AGENTS_FORBIDDEN_KEY_PATTERNS:
        for k in all_keys:
            if pattern.lower() in k.lower():
                violations.append(k)
                break

    assert violations == [], (
        f"Forbidden keys found in agents response: {violations}\n"
        f"All keys: {sorted(all_keys)}"
    )

    feishu_keys = [k for k in lower_keys if "feishu" in k]
    assert feishu_keys == [], f"Feishu-related keys found: {feishu_keys}"


# ----------------------------------------------------------------
# Finding 3 regression: teams pre-flight validation
# ----------------------------------------------------------------

@pytest.mark.anyio
def test_put_teams_rejects_unknown_agent_in_add_workers(
    tmp_home, app, org_state, auth_headers,
) -> None:
    """Adding an agent that doesn't exist must return 422, NOT 200.
    Pre-flight prevents mutation so teams.yaml stays untouched."""
    from runtime.orchestrator._paths import OrgPaths
    paths = OrgPaths(root=org_state.root)
    # Seed ALL workers so post-flight doesn't interfere
    _seed_agent_file(paths, "engineering_head", "engineering", role="manager")
    _seed_agent_file(paths, "product_manager", "engineering")
    _seed_agent_file(paths, "dev_agent", "engineering")
    _seed_agent_file(paths, "payment_agent", "engineering")
    _seed_agent_file(paths, "qa_engineer", "engineering")
    _seed_agent_file(paths, "content_manager", "content", role="manager")
    _seed_agent_file(paths, "content_writer", "content")
    _seed_agent_file(paths, "content_qa", "content")
    _seed_agent_file(paths, "seo_agent", "content")

    client = TestClient(app)
    r = client.put(
        f"/api/v1/orgs/{org_state.slug}/settings/teams",
        headers=auth_headers,
        json={"team": "engineering", "add_workers": ["nonexistent_agent"]},
    )
    assert r.status_code == 422

    # teams.yaml must NOT have been mutated
    import yaml
    from pathlib import Path
    teams_path = Path(org_state.root) / "org" / "teams.yaml"
    loaded = yaml.safe_load(teams_path.read_text())
    workers = loaded["teams"]["engineering"]["workers"]
    assert "nonexistent_agent" not in workers


@pytest.mark.anyio
def test_put_teams_rejects_unknown_agent_in_remove_workers(
    tmp_home, app, org_state, auth_headers,
) -> None:
    """Removing an agent that doesn't exist in the active agent list must return 422."""
    from runtime.orchestrator._paths import OrgPaths
    paths = OrgPaths(root=org_state.root)
    _seed_agent_file(paths, "engineering_head", "engineering", role="manager")
    _seed_agent_file(paths, "product_manager", "engineering")
    _seed_agent_file(paths, "dev_agent", "engineering")
    _seed_agent_file(paths, "payment_agent", "engineering")
    _seed_agent_file(paths, "qa_engineer", "engineering")
    _seed_agent_file(paths, "content_manager", "content", role="manager")
    _seed_agent_file(paths, "content_writer", "content")
    _seed_agent_file(paths, "content_qa", "content")
    _seed_agent_file(paths, "seo_agent", "content")

    client = TestClient(app)
    r = client.put(
        f"/api/v1/orgs/{org_state.slug}/settings/teams",
        headers=auth_headers,
        json={"team": "engineering", "remove_workers": ["nonexistent_agent"]},
    )
    assert r.status_code == 422


@pytest.mark.anyio
def test_put_teams_rejects_manager_added_as_worker(
    tmp_home, app, org_state, auth_headers,
) -> None:
    """Adding the team's own manager as a worker must return 422."""
    from runtime.orchestrator._paths import OrgPaths
    paths = OrgPaths(root=org_state.root)
    _seed_agent_file(paths, "engineering_head", "engineering", role="manager")
    _seed_agent_file(paths, "product_manager", "engineering")
    _seed_agent_file(paths, "dev_agent", "engineering")
    _seed_agent_file(paths, "payment_agent", "engineering")
    _seed_agent_file(paths, "qa_engineer", "engineering")
    _seed_agent_file(paths, "content_manager", "content", role="manager")
    _seed_agent_file(paths, "content_writer", "content")
    _seed_agent_file(paths, "content_qa", "content")
    _seed_agent_file(paths, "seo_agent", "content")

    client = TestClient(app)
    r = client.put(
        f"/api/v1/orgs/{org_state.slug}/settings/teams",
        headers=auth_headers,
        json={"team": "engineering", "add_workers": ["engineering_head"]},
    )
    assert r.status_code == 422


@pytest.mark.anyio
def test_put_teams_rollback_removing_agent_still_declaring_team(
    tmp_home, app, org_state, auth_headers,
) -> None:
    """Removing a worker whose agent file still declares the team must 409
    AND roll back the teams.yaml worker list to its original value."""
    import yaml
    from pathlib import Path
    from runtime.orchestrator._paths import OrgPaths

    paths = OrgPaths(root=org_state.root)
    # Seed agent files for ALL workers in the seeded teams.yaml plus the manager
    _seed_agent_file(paths, "engineering_head", "engineering", role="manager")
    _seed_agent_file(paths, "product_manager", "engineering")
    _seed_agent_file(paths, "dev_agent", "engineering")
    _seed_agent_file(paths, "payment_agent", "engineering")
    _seed_agent_file(paths, "qa_engineer", "engineering")
    _seed_agent_file(paths, "content_manager", "content", role="manager")
    _seed_agent_file(paths, "content_writer", "content")
    _seed_agent_file(paths, "content_qa", "content")
    _seed_agent_file(paths, "seo_agent", "content")

    # Snapshot the pre-request worker list
    teams_path = Path(org_state.root) / "org" / "teams.yaml"
    before = yaml.safe_load(teams_path.read_text())
    before_workers = list(before["teams"]["engineering"]["workers"])

    client = TestClient(app)
    r = client.put(
        f"/api/v1/orgs/{org_state.slug}/settings/teams",
        headers=auth_headers,
        json={"team": "engineering", "remove_workers": ["product_manager"]},
    )
    assert r.status_code == 409

    # Verify the worker set is restored to its original value
    # (add_worker appends so order may differ; we assert set equality)
    after = yaml.safe_load(teams_path.read_text())
    assert set(after["teams"]["engineering"]["workers"]) == set(before_workers)
