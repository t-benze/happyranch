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

from pathlib import Path

import pytest
import yaml
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
        "queue_workers", "host_global_session_cap", "protocol_dir",
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
    """Each system entry must have restart_required: true — all system-level
    settings including session_timeout_seconds are module-global singletons
    that require a daemon restart to apply."""
    client = TestClient(app)
    r = client.get(
        f"/api/v1/orgs/{org_state.slug}/settings",
        headers=auth_headers,
    )
    assert r.status_code == 200
    sys_ = r.json()["system"]

    restart_true = {
        "claude_cli_path", "codex_cli_path", "opencode_cli_path",
        "pi_cli_path", "session_timeout_seconds", "max_orchestration_steps",
        "queue_workers", "host_global_session_cap", "protocol_dir",
    }
    for key in restart_true:
        assert sys_[key]["restart_required"] is True, f"{key} restart_required must be True"
    assert sys_["session_timeout_seconds"]["restart_required"] is True


def test_settings_requires_auth(tmp_home, app, org_state) -> None:
    client = TestClient(app)
    r = client.get(f"/api/v1/orgs/{org_state.slug}/settings")
    assert r.status_code == 401


def test_daemon_capacity_requires_bearer_without_leaking_values(tmp_home, app, org_state) -> None:
    response = TestClient(app).get(f"/api/v1/orgs/{org_state.slug}/settings/daemon-capacity")
    assert response.status_code == 401
    assert "queue_workers" not in response.text
    assert "host_global_session_cap" not in response.text


@pytest.mark.parametrize("value", [True, "6", 6.0, None])
def test_daemon_capacity_rejects_non_exact_integer(tmp_home, app, org_state, auth_headers, value) -> None:
    client = TestClient(app)
    current = client.get(
        f"/api/v1/orgs/{org_state.slug}/settings/daemon-capacity", headers=auth_headers
    ).json()
    response = client.put(
        f"/api/v1/orgs/{org_state.slug}/settings/daemon-capacity",
        headers={**auth_headers, "If-Match": f'"{current["revision"]}"'},
        json={
            "queue_workers": value,
            "host_global_session_cap": 13,
            "rationale": "route validation",
            "confirm_environment_shadow": False,
        },
    )
    assert response.status_code == 422


@pytest.mark.parametrize(
    ("header", "status", "code"),
    [
        (None, 428, "if_match_required"),
        ('W/"sha256:abc"', 400, "if_match_invalid"),
        ("*", 400, "if_match_invalid"),
        ('"sha256:abc", "sha256:def"', 400, "if_match_invalid"),
        ("sha256:abc", 400, "if_match_invalid"),
    ],
)
def test_daemon_capacity_requires_one_quoted_strong_if_match(
    tmp_home, app, org_state, auth_headers, header, status, code,
) -> None:
    headers = dict(auth_headers)
    if header is not None:
        headers["If-Match"] = header
    response = TestClient(app).put(
        f"/api/v1/orgs/{org_state.slug}/settings/daemon-capacity",
        headers=headers,
        json={"queue_workers": 6, "host_global_session_cap": 13,
              "rationale": "conditional write", "confirm_environment_shadow": False},
    )
    assert response.status_code == status
    assert response.json()["detail"]["code"] == code


def test_daemon_capacity_stale_if_match_returns_latest_and_does_not_mutate(
    tmp_home, app, org_state, auth_headers,
) -> None:
    client = TestClient(app)
    before = client.get(
        f"/api/v1/orgs/{org_state.slug}/settings/daemon-capacity", headers=auth_headers
    ).json()
    response = client.put(
        f"/api/v1/orgs/{org_state.slug}/settings/daemon-capacity",
        headers={**auth_headers, "If-Match": f'"sha256:{"0" * 64}"'},
        json={"queue_workers": 7, "host_global_session_cap": 14,
              "rationale": "stale", "confirm_environment_shadow": False},
    )
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "stale_revision"
    assert detail["latest"]["revision"] == before["revision"]


def test_daemon_capacity_body_revision_is_rejected_as_extra(
    tmp_home, app, org_state, auth_headers,
) -> None:
    response = TestClient(app).put(
        f"/api/v1/orgs/{org_state.slug}/settings/daemon-capacity",
        headers={**auth_headers, "If-Match": '"sha256:value"'},
        json={"revision": "sha256:other", "queue_workers": 6,
              "host_global_session_cap": 13, "rationale": "extra",
              "confirm_environment_shadow": False},
    )
    assert response.status_code == 422


def test_daemon_capacity_success_audits_honest_pre_replace_authorization(
    tmp_home, app, org_state, auth_headers,
) -> None:
    client = TestClient(app)
    current = client.get(
        f"/api/v1/orgs/{org_state.slug}/settings/daemon-capacity", headers=auth_headers
    ).json()
    response = client.put(
        f"/api/v1/orgs/{org_state.slug}/settings/daemon-capacity",
        headers={**auth_headers, "If-Match": f'"{current["revision"]}"'},
        json={"queue_workers": 6, "host_global_session_cap": 13,
              "rationale": "measured receipts", "confirm_environment_shadow": False},
    )
    assert response.status_code == 200
    rows = org_state.db.get_audit_logs("config:daemon_capacity")
    assert [row["action"] for row in rows] == [
        "daemon_capacity_config_write_authorized",
        "daemon_capacity_config_succeeded",
    ]
    assert all(row["agent"] == "daemon-bearer-holder" for row in rows)
    assert [row["payload"]["outcome"] for row in rows] == [
        "validated_write_authorized", "succeeded",
    ]
    assert len({row["payload"]["correlation_id"] for row in rows}) == 1
    for row in rows:
        assert row["payload"]["prior"] == {"queue_workers": None, "host_global_session_cap": None}
        assert row["payload"]["new"] == {"queue_workers": 6, "host_global_session_cap": 13}
        assert row["payload"]["revision_before"] == current["revision"]
        assert row["payload"]["revision_after"] == response.json()["revision"]
        assert row["payload"]["rationale"] == "measured receipts"
        assert "token" not in str(row["payload"]).lower()
    assert sum(row["action"] == "daemon_capacity_config_succeeded" for row in rows) == 1


def test_daemon_capacity_rejection_has_one_honest_terminal_audit(
    tmp_home, app, org_state, auth_headers,
) -> None:
    client = TestClient(app)
    response = client.put(
        f"/api/v1/orgs/{org_state.slug}/settings/daemon-capacity",
        headers={**auth_headers, "If-Match": f'"sha256:{"0" * 64}"'},
        json={"queue_workers": 6, "host_global_session_cap": 2,
              "rationale": "stale request", "confirm_environment_shadow": False},
    )
    assert response.status_code == 409
    rows = org_state.db.get_audit_logs("config:daemon_capacity")
    assert len(rows) == 1
    assert rows[0]["action"] == "daemon_capacity_config_rejected"
    assert rows[0]["agent"] == "daemon-bearer-holder"
    assert rows[0]["payload"]["outcome"] == "rejected"
    assert rows[0]["payload"]["error_class"] == "stale_revision"
    assert rows[0]["payload"]["new"] == {
        "queue_workers": 6, "host_global_session_cap": 2,
    }


def test_daemon_capacity_terminal_success_audit_failure_is_publication_uncertain(
    tmp_home, app, org_state, auth_headers, monkeypatch,
) -> None:
    client = TestClient(app)
    current = client.get(
        f"/api/v1/orgs/{org_state.slug}/settings/daemon-capacity", headers=auth_headers
    ).json()
    real_insert = org_state.db.insert_audit_log

    def fail_terminal(*, action, **kwargs):
        if action == "daemon_capacity_config_succeeded":
            raise OSError("terminal audit unavailable")
        return real_insert(action=action, **kwargs)

    monkeypatch.setattr(org_state.db, "insert_audit_log", fail_terminal)
    response = client.put(
        f"/api/v1/orgs/{org_state.slug}/settings/daemon-capacity",
        headers={**auth_headers, "If-Match": f'"{current["revision"]}"'},
        json={"queue_workers": 6, "host_global_session_cap": 13,
              "rationale": "terminal receipt fault", "confirm_environment_shadow": False},
    )
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "config_publication_uncertain"
    assert yaml.safe_load((tmp_home / "config.yaml").read_text())["queue_workers"] == 6
    rows = org_state.db.get_audit_logs("config:daemon_capacity")
    assert [row["action"] for row in rows] == ["daemon_capacity_config_write_authorized"]


def test_daemon_capacity_post_replace_failure_requires_reload_before_retry(
    tmp_home, app, org_state, auth_headers, monkeypatch,
) -> None:
    client = TestClient(app)
    current = client.get(
        f"/api/v1/orgs/{org_state.slug}/settings/daemon-capacity", headers=auth_headers
    ).json()
    published = False
    real_replace = __import__("os").replace
    real_open = __import__("os").open

    def tracked_replace(source, target):
        nonlocal published
        real_replace(source, target)
        published = True

    def fail_directory_open(*args, **kwargs):
        if published:
            raise OSError("directory open fault")
        return real_open(*args, **kwargs)

    monkeypatch.setattr("runtime.daemon.capacity_config.os.replace", tracked_replace)
    monkeypatch.setattr("runtime.daemon.capacity_config.os.open", fail_directory_open)
    response = client.put(
        f"/api/v1/orgs/{org_state.slug}/settings/daemon-capacity",
        headers={**auth_headers, "If-Match": f'"{current["revision"]}"'},
        json={"queue_workers": 6, "host_global_session_cap": 13,
              "rationale": "fault probe", "confirm_environment_shadow": False},
    )
    assert response.status_code == 503
    assert response.json()["detail"] == {
        "code": "config_publication_uncertain",
        "message": "capacity configuration was published, but durability or verification did not complete; reload and inspect the authoritative configuration before retrying",
        "artifact_state": "absent",
    }
    assert yaml.safe_load((tmp_home / "config.yaml").read_text())["queue_workers"] == 6
    assert not list(tmp_home.glob(".*.tmp"))
    rows = org_state.db.get_audit_logs("config:daemon_capacity")
    assert [row["payload"]["outcome"] for row in rows] == [
        "validated_write_authorized", "config_publication_uncertain",
    ]
    assert len({row["payload"]["correlation_id"] for row in rows}) == 1

    latest = client.get(
        f"/api/v1/orgs/{org_state.slug}/settings/daemon-capacity", headers=auth_headers
    ).json()
    retry = client.put(
        f"/api/v1/orgs/{org_state.slug}/settings/daemon-capacity",
        headers={**auth_headers, "If-Match": f'"{current["revision"]}"'},
        json={"queue_workers": 7, "host_global_session_cap": 14,
              "rationale": "blind retry", "confirm_environment_shadow": False},
    )
    assert latest["persisted_yaml"] == {"queue_workers": 6, "host_global_session_cap": 13}
    assert retry.status_code == 409
    assert retry.json()["detail"]["latest"]["revision"] == latest["revision"]


@pytest.mark.parametrize(
    ("fault", "expected_artifact_state", "artifact_present"),
    [("exists", "unknown", False), ("unlink", "present", True)],
)
def test_daemon_capacity_post_replace_cleanup_failure_requires_reload_before_retry(
    tmp_home, app, org_state, auth_headers, monkeypatch,
    fault, expected_artifact_state, artifact_present,
) -> None:
    client = TestClient(app)
    config_path = tmp_home / "config.yaml"
    config_path.write_text("other: retained\nqueue_workers: 4\nhost_global_session_cap: 11\n")
    current = client.get(
        f"/api/v1/orgs/{org_state.slug}/settings/daemon-capacity", headers=auth_headers
    ).json()
    published = False
    artifact = None
    real_replace = __import__("os").replace
    real_exists = __import__("os").path.exists
    real_unlink = __import__("os").unlink

    def tracked_replace(source, target):
        nonlocal published, artifact
        real_replace(source, target)
        published = True
        artifact = Path(source)
        if fault == "unlink":
            artifact.write_bytes(b"leftover temp artifact")

    def cleanup_exists(candidate):
        if published and Path(candidate) == artifact and fault == "exists":
            raise OSError("cleanup inspection fault")
        return real_exists(candidate)

    def cleanup_unlink(candidate):
        if published and Path(candidate) == artifact:
            raise OSError("cleanup unlink fault")
        return real_unlink(candidate)

    monkeypatch.setattr("runtime.daemon.capacity_config.os.replace", tracked_replace)
    monkeypatch.setattr("runtime.daemon.capacity_config.os.path.exists", cleanup_exists)
    if fault == "unlink":
        monkeypatch.setattr("runtime.daemon.capacity_config.os.unlink", cleanup_unlink)

    response = client.put(
        f"/api/v1/orgs/{org_state.slug}/settings/daemon-capacity",
        headers={**auth_headers, "If-Match": f'"{current["revision"]}"'},
        json={"queue_workers": 6, "host_global_session_cap": 13,
              "rationale": "cleanup fault", "confirm_environment_shadow": False},
    )
    assert response.status_code == 503
    assert response.json()["detail"] == {
        "code": "config_publication_uncertain",
        "message": "capacity configuration was published, but durability, verification, or cleanup did not complete; reload and inspect the authoritative configuration and temporary artifact state before retrying",
        "artifact_state": expected_artifact_state,
    }
    assert yaml.safe_load(config_path.read_text()) == {
        "other": "retained", "queue_workers": 6, "host_global_session_cap": 13,
    }
    rows = org_state.db.get_audit_logs("config:daemon_capacity")
    assert [row["payload"]["outcome"] for row in rows] == [
        "validated_write_authorized", "config_publication_uncertain",
    ]
    assert len({row["payload"]["correlation_id"] for row in rows}) == 1
    assert bool(artifact and real_exists(artifact)) is artifact_present
    latest = client.get(
        f"/api/v1/orgs/{org_state.slug}/settings/daemon-capacity", headers=auth_headers
    ).json()
    assert latest["persisted_yaml"] == {"queue_workers": 6, "host_global_session_cap": 13}
    retry = client.put(
        f"/api/v1/orgs/{org_state.slug}/settings/daemon-capacity",
        headers={**auth_headers, "If-Match": f'"{current["revision"]}"'},
        json={"queue_workers": 7, "host_global_session_cap": 14,
              "rationale": "blind retry", "confirm_environment_shadow": False},
    )
    assert retry.status_code == 409
    assert retry.json()["detail"]["latest"]["revision"] == latest["revision"]
    assert len(org_state.db.get_audit_logs("config:daemon_capacity")) == 3


@pytest.mark.parametrize("fault", ["read", "parse"])
def test_daemon_capacity_final_snapshot_failure_requires_reload_before_retry(
    tmp_home, app, org_state, auth_headers, monkeypatch, fault,
) -> None:
    client = TestClient(app)
    config_path = tmp_home / "config.yaml"
    config_path.write_text("other: retained\nqueue_workers: 4\nhost_global_session_cap: 11\n")
    current = client.get(
        f"/api/v1/orgs/{org_state.slug}/settings/daemon-capacity", headers=auth_headers
    ).json()
    published = False
    real_replace = __import__("os").replace

    def tracked_replace(source, target):
        nonlocal published
        real_replace(source, target)
        published = True

    monkeypatch.setattr("runtime.daemon.capacity_config.os.replace", tracked_replace)
    if fault == "read":
        from pathlib import Path
        real_read = Path.read_bytes
        reads_after_publish = 0

        def fail_final_read(self):
            nonlocal reads_after_publish
            if published and self == config_path:
                reads_after_publish += 1
                if reads_after_publish == 2:
                    raise OSError("final snapshot read fault")
            return real_read(self)

        monkeypatch.setattr(Path, "read_bytes", fail_final_read)
    else:
        real_load = yaml.safe_load
        parses_after_publish = 0

        def fail_final_parse(value):
            nonlocal parses_after_publish
            if published:
                parses_after_publish += 1
                if parses_after_publish == 2:
                    raise yaml.YAMLError("final snapshot parse fault")
            return real_load(value)

        monkeypatch.setattr(yaml, "safe_load", fail_final_parse)

    response = client.put(
        f"/api/v1/orgs/{org_state.slug}/settings/daemon-capacity",
        headers={**auth_headers, "If-Match": f'"{current["revision"]}"'},
        json={"queue_workers": 6, "host_global_session_cap": 13,
              "rationale": "final snapshot fault", "confirm_environment_shadow": False},
    )
    assert response.status_code == 503
    assert response.json()["detail"] == {
        "code": "config_publication_uncertain",
        "message": "capacity configuration was published, but durability or verification did not complete; reload and inspect the authoritative configuration before retrying",
        "artifact_state": "absent",
    }
    assert yaml.safe_load(config_path.read_text()) == {
        "other": "retained", "queue_workers": 6, "host_global_session_cap": 13,
    }
    rows = org_state.db.get_audit_logs("config:daemon_capacity")
    assert [row["payload"]["outcome"] for row in rows] == [
        "validated_write_authorized", "config_publication_uncertain",
    ]
    assert len({row["payload"]["correlation_id"] for row in rows}) == 1
    assert not list(tmp_home.glob(".*.tmp"))
    assert not list(tmp_home.glob("*.bak"))
    latest = client.get(
        f"/api/v1/orgs/{org_state.slug}/settings/daemon-capacity", headers=auth_headers
    ).json()
    assert latest["persisted_yaml"] == {"queue_workers": 6, "host_global_session_cap": 13}
    retry = client.put(
        f"/api/v1/orgs/{org_state.slug}/settings/daemon-capacity",
        headers={**auth_headers, "If-Match": f'"{current["revision"]}"'},
        json={"queue_workers": 7, "host_global_session_cap": 14,
              "rationale": "blind retry", "confirm_environment_shadow": False},
    )
    assert retry.status_code == 409
    assert retry.json()["detail"]["latest"]["revision"] == latest["revision"]
    assert len(org_state.db.get_audit_logs("config:daemon_capacity")) == 3


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
    """SystemSettingsView must contain ONLY the 9 allow-listed fields."""
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
        "queue_workers", "host_global_session_cap", "protocol_dir",
    }
    assert system_keys == expected, (
        f"System settings keys: {sorted(system_keys)}\n"
        f"Expected: {sorted(expected)}"
    )


def test_settings_org_only_has_allow_listed_fields(
    tmp_home, app, org_state, auth_headers,
) -> None:
    """OrgSettingsView must contain ONLY session_timeout_seconds, dreaming,
    threads, working_hours, reviewer_agents."""
    client = TestClient(app)
    r = client.get(
        f"/api/v1/orgs/{org_state.slug}/settings",
        headers=auth_headers,
    )
    assert r.status_code == 200
    org_keys = set(r.json()["org"].keys())

    expected = {
        "session_timeout_seconds", "dreaming", "threads", "working_hours",
        "reviewer_agents",
    }
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
    """extra='forbid' must reject any unknown key with 422.

    (``working_hours`` is now an allow-listed writable key — THR-035/TASK-967 —
    so a different unknown key is used here.)"""
    client = TestClient(app)
    r = client.put(
        f"/api/v1/orgs/{org_state.slug}/settings/org",
        headers=auth_headers,
        json={"permission_mode": "acceptAll"},
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
    """If org/config.yaml has a working_hours block before the PUT, it must survive.

    THR-095 F1: after the one-shot seed, the PUT path writes ONLY to the
    org_settings DB table — config.yaml is NEVER mutated on PUT.  The
    one-time seed (guarded by sentinel) strips writable keys from config.yaml;
    after that, every PUT leaves the file untouched.  This test verifies
    config.yaml is byte-identical after PUT (both writable and non-writable
    keys intact, non-migrated keys untouched)."""
    import yaml

    from pathlib import Path
    config_path = Path(org_state.root) / "org" / "config.yaml"
    raw = yaml.safe_load(config_path.read_text()) if config_path.exists() else {}
    raw["feishu_notifications"] = {"chat_id": "secret-chat"}
    config_path.write_text(yaml.safe_dump(raw))
    before_bytes = config_path.read_bytes()

    client = TestClient(app)
    r = client.put(
        f"/api/v1/orgs/{org_state.slug}/settings/org",
        headers=auth_headers,
        json={"session_timeout_seconds": 999},
    )
    assert r.status_code == 200

    # F1 fix: config.yaml is byte-unchanged after PUT — DB is the sole store.
    after_bytes = config_path.read_bytes()
    assert after_bytes == before_bytes, (
        "config.yaml must be byte-unchanged after PUT — "
        "the DB is the single authoritative store for writable keys"
    )
    raw2 = yaml.safe_load(after_bytes)
    # feishu_notifications still present (non-writable key)
    assert raw2.get("feishu_notifications") == {"chat_id": "secret-chat"}


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
    every unpatched sibling leaf survives in the DB and on re-read.

    THR-095: config.yaml is no longer the read/write source — the DB is.
    The seed already populated defaults; we override the DB directly to
    set up custom base values, then patch and verify sibling survival."""
    import json

    client = TestClient(app)

    # THR-095: seed custom base values directly in the DB (config.yaml
    # is no longer the read source for these keys).
    dreaming_base = {
        "enabled": False,
        "schedule": {"time": "06:00", "timezone": "Asia/Shanghai", "catch_up_on_startup": False},
        "agents": {"mode": "whitelist", "include": ["dev_agent"], "exclude": ["qa_engineer"]},
    }
    threads_base = {
        "enabled": True,
        "default_turn_cap": 100,
        "invocation_timeout_seconds": 900,
    }
    org_state.db.upsert_org_setting("dreaming", json.dumps(dreaming_base))
    org_state.db.upsert_org_setting("threads", json.dumps(threads_base))
    org_state.db.upsert_org_setting("session_timeout_seconds", json.dumps(3600))

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

    # DB: patched values should match unpatched sibling survival
    dreaming_after = json.loads(org_state.db.get_org_setting("dreaming"))
    assert dreaming_after["enabled"] is True
    assert dreaming_after["schedule"] == {"time": "06:00", "timezone": "Asia/Shanghai", "catch_up_on_startup": False}
    assert dreaming_after["agents"] == {"mode": "whitelist", "include": ["dev_agent"], "exclude": ["qa_engineer"]}
    threads_after = json.loads(org_state.db.get_org_setting("threads"))
    assert threads_after == {"enabled": True, "default_turn_cap": 200, "invocation_timeout_seconds": 900}


def test_put_org_settings_deep_merge_nested_partial_schedule(
    tmp_home, app, org_state, auth_headers,
) -> None:
    """Patching only one field inside dreaming.schedule leaves the other field intact.

    THR-095: base values are set in the DB, not config.yaml."""
    import json

    client = TestClient(app)

    # DB-seeded base values
    dreaming_base = {
        "enabled": True,
        "schedule": {"time": "02:00", "timezone": "UTC", "catch_up_on_startup": True},
        "agents": {"mode": "all", "include": [], "exclude": []},
    }
    org_state.db.upsert_org_setting("dreaming", json.dumps(dreaming_base))

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

    # DB: sibling survival
    dreaming_after = json.loads(org_state.db.get_org_setting("dreaming"))
    assert dreaming_after["schedule"] == {"time": "08:00", "timezone": "UTC", "catch_up_on_startup": True}


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
    """Sending explicit null for threads.invocation_timeout_seconds clears the override.

    THR-095: DB-backed storage — verification against DB, not config.yaml."""
    import json

    client = TestClient(app)

    # 1. First, set a non-null threads.invocation_timeout_seconds
    r0 = client.put(
        f"/api/v1/orgs/{org_state.slug}/settings/org",
        headers=auth_headers,
        json={"threads": {"invocation_timeout_seconds": 900}},
    )
    assert r0.status_code == 200
    assert r0.json()["org"]["threads"]["invocation_timeout_seconds"] == 900

    # Confirm it persisted to DB
    threads_after_set = json.loads(org_state.db.get_org_setting("threads"))
    assert threads_after_set["invocation_timeout_seconds"] == 900

    # 2. Now clear it with explicit null
    r = client.put(
        f"/api/v1/orgs/{org_state.slug}/settings/org",
        headers=auth_headers,
        json={"threads": {"invocation_timeout_seconds": None}},
    )
    assert r.status_code == 200
    # (a) Response body shows None
    assert r.json()["org"]["threads"]["invocation_timeout_seconds"] is None

    # (b) DB shows None
    threads_after_null = json.loads(org_state.db.get_org_setting("threads"))
    assert threads_after_null["invocation_timeout_seconds"] is None

    # (c) GET /settings reload shows None
    r2 = client.get(
        f"/api/v1/orgs/{org_state.slug}/settings",
        headers=auth_headers,
    )
    assert r2.json()["org"]["threads"]["invocation_timeout_seconds"] is None

    # (d) Sibling threads leaves are preserved unchanged
    body = r.json()["org"]["threads"]
    assert body.get("enabled") is not None  # survived the null clear
    assert body.get("default_turn_cap") is not None  # survived the null clear


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
def test_put_teams_rejects_different_team_manager_added_as_worker(
    tmp_home, app, org_state, auth_headers,
) -> None:
    """Adding a manager from a DIFFERENT team as a worker must return 422.

    Regression: the original _preflight_team_workers only rejected the
    CURRENT team's own manager. A manager from another team (e.g.
    content_manager added to engineering) passed preflight and was only
    caught by the post-flight 409 AFTER teams.yaml had already been
    mutated — a crash window writing invalid membership.

    This test asserts (a) HTTP 422 and (b) teams.yaml is unchanged.
    """
    import yaml
    from pathlib import Path
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

    # Snapshot pre-request worker list for engineering
    teams_path = Path(org_state.root) / "org" / "teams.yaml"
    before = yaml.safe_load(teams_path.read_text())
    before_workers = list(before["teams"]["engineering"]["workers"])

    client = TestClient(app)
    r = client.put(
        f"/api/v1/orgs/{org_state.slug}/settings/teams",
        headers=auth_headers,
        json={"team": "engineering", "add_workers": ["content_manager"]},
    )
    # (a) Must reject with 422 — pre-flight catches this before mutation
    assert r.status_code == 422

    # (b) teams.yaml must be unchanged
    after = yaml.safe_load(teams_path.read_text())
    assert set(after["teams"]["engineering"]["workers"]) == set(before_workers)
    assert "content_manager" not in after["teams"]["engineering"]["workers"]


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


# ----------------------------------------------------------------
# THR-175: reviewer_agents org setting (GET + PUT validation)
# ----------------------------------------------------------------

def _seed_reviewer_agents_agents(org_state) -> None:
    """Write agent files so _resolve_agent_names knows code_reviewer/senior_dev."""
    from tests.conftest import seed_test_agents
    from runtime.orchestrator._paths import OrgPaths
    seed_test_agents(
        OrgPaths(root=org_state.root),
        ("code_reviewer", "senior_dev", "qa_engineer", "dev_agent"),
    )


def test_get_settings_returns_reviewer_agents_default(
    tmp_home, app, org_state, auth_headers,
) -> None:
    client = TestClient(app)
    r = client.get(
        f"/api/v1/orgs/{org_state.slug}/settings", headers=auth_headers,
    )
    assert r.status_code == 200
    assert r.json()["org"]["reviewer_agents"] == ["code_reviewer"]


def test_put_settings_updates_reviewer_agents(
    tmp_home, app, org_state, auth_headers,
) -> None:
    _seed_reviewer_agents_agents(org_state)
    client = TestClient(app)
    r = client.put(
        f"/api/v1/orgs/{org_state.slug}/settings/org",
        headers=auth_headers,
        json={"reviewer_agents": ["senior_dev"]},
    )
    assert r.status_code == 200
    assert r.json()["org"]["reviewer_agents"] == ["senior_dev"]
    # Persisted in the DB.
    import json as _json
    assert _json.loads(org_state.db.get_org_setting("reviewer_agents")) == ["senior_dev"]


def test_put_settings_rejects_unknown_reviewer_agent(
    tmp_home, app, org_state, auth_headers,
) -> None:
    _seed_reviewer_agents_agents(org_state)
    client = TestClient(app)
    r = client.put(
        f"/api/v1/orgs/{org_state.slug}/settings/org",
        headers=auth_headers,
        json={"reviewer_agents": ["ghost_agent"]},
    )
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert detail["remediation"] and "HARD REJECT" in detail["remediation"]
    assert any("unknown agent" in e for e in detail["errors"])


def test_put_settings_rejects_empty_reviewer_agents(
    tmp_home, app, org_state, auth_headers,
) -> None:
    _seed_reviewer_agents_agents(org_state)
    client = TestClient(app)
    r = client.put(
        f"/api/v1/orgs/{org_state.slug}/settings/org",
        headers=auth_headers,
        json={"reviewer_agents": []},
    )
    assert r.status_code == 422


def test_put_settings_rejects_non_string_reviewer_agent(
    tmp_home, app, org_state, auth_headers,
) -> None:
    _seed_reviewer_agents_agents(org_state)
    client = TestClient(app)
    r = client.put(
        f"/api/v1/orgs/{org_state.slug}/settings/org",
        headers=auth_headers,
        json={"reviewer_agents": ["senior_dev", 123]},
    )
    assert r.status_code == 422


def test_get_settings_unknown_persisted_reviewer_agents_resolves_default(
    tmp_home, app, org_state, auth_headers,
) -> None:
    """The GET settings read path resolves an already-persisted UNKNOWN
    reviewer_agents value fail-closed to the code default — never exposes the
    unknown name that would demote code_reviewer from the reviewer set."""
    _seed_reviewer_agents_agents(org_state)
    import json as _json
    org_state.db.upsert_org_setting("reviewer_agents", _json.dumps(["ghost_agent"]))
    client = TestClient(app)
    r = client.get(
        f"/api/v1/orgs/{org_state.slug}/settings", headers=auth_headers,
    )
    assert r.status_code == 200
    assert r.json()["org"]["reviewer_agents"] == ["code_reviewer"]
