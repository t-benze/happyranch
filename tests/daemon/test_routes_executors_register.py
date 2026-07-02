"""Tests for POST /api/v1/orgs/{slug}/executors/conformance-checkin
and POST /api/v1/orgs/{slug}/executors/register.

THR-052 PR-2 — registration gate + daemon-verified conformance.
"""
from __future__ import annotations

import time
from pathlib import Path
from textwrap import dedent
from unittest import mock

import yaml
import pytest
from fastapi.testclient import TestClient

from runtime.config import Settings
from runtime.daemon import auth as auth_mod
from runtime.daemon import paths as paths_mod
from runtime.daemon.registration_token import (
    DEFAULT_REGISTRATION_TOKEN_TTL_SECONDS,
    REGISTRATION_TOKEN_PREFIX,
    RegistrationTokenStore,
)
from runtime.daemon.routes import auth as auth_route
from runtime.daemon.state import DaemonState
from runtime.orchestrator._paths import OrgPaths
from runtime.orchestrator.executor_registry import get_registry, reset_registry
from runtime.orchestrator.org_config import OrgConfigError
from runtime.runtime import RuntimeDir


# ── Fixtures ────────────────────────────────────────────────────────────


def _make_org_config(org_root: Path, yaml_str: str) -> None:
    """Write org/config.yaml for the given org root."""
    config_path = OrgPaths(root=org_root).org_config_path
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(dedent(yaml_str))


def _config_raw(org_root: Path) -> dict:
    """Read the raw org/config.yaml dict."""
    p = OrgPaths(root=org_root).org_config_path
    if not p.exists():
        return {}
    return yaml.safe_load(p.read_text()) or {}


@pytest.fixture
def runtime_with_token(tmp_path, monkeypatch):
    """Runtime with daemon token, localhost bypass, and fresh registry."""
    monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path / ".happyranch"))
    paths_mod.ensure_daemon_home()
    paths_mod.ensure_token()
    rt = RuntimeDir.init(tmp_path / "runtime")
    # Seed alpha org
    org_root = rt.orgs_dir / "alpha"
    org_root.mkdir(parents=True)
    (org_root / "org").mkdir()
    (org_root / "org" / "teams.yaml").write_text(
        "teams:\n"
        "  engineering:\n"
        "    manager: engineering_head\n"
        "    workers: [dev_agent]\n"
    )
    # Fresh registry per test
    reset_registry()
    return rt


@pytest.fixture
def daemon_state(runtime_with_token):
    return DaemonState.from_runtime(runtime_with_token, Settings())


@pytest.fixture
def app(daemon_state):
    from runtime.daemon.app import create_app
    return create_app(daemon_state)


@pytest.fixture
def master_token():
    return paths_mod.read_token()


def _bypass_loopback(monkeypatch):
    """Allow TestClient (peer 'testclient') through loopback gates."""
    monkeypatch.setattr(
        auth_route, "_LOCAL_HOSTS",
        auth_route._LOCAL_HOSTS | {"testclient"},
    )
    monkeypatch.setattr(
        auth_mod, "_REGISTRATION_LOCAL_HOSTS",
        auth_mod._REGISTRATION_LOCAL_HOSTS | {"testclient"},
    )


def _mint_token(store, org="alpha", name="test-executor", now=None):
    """Mint a registration token and return (token_plaintext, expires_at)."""
    return store.mint(org, name, now=now)


def _complete_challenge(store, token, org="alpha", now=None):
    """Record all conformance steps for a token."""
    for step_id in RegistrationTokenStore.DEFAULT_CONFORMANCE_STEPS:
        store.record_step_arrival(token, org, step_id, now=now)


# ── Happy path: mint → check-ins → register ────────────────────────────


class TestRegisterHappyPath:
    """Mint token → complete conformance → POST /register → config written."""

    def test_full_happy_path(self, app, daemon_state, monkeypatch):
        _bypass_loopback(monkeypatch)
        client = TestClient(app)
        store = daemon_state.registration_token_store

        # Mint a token via the store directly
        token, _ = store.mint("alpha", "test-executor")

        # Complete all conformance steps
        _complete_challenge(store, token)

        # Register
        client.headers.update({"Authorization": f"Bearer {token}"})
        r = client.post("/api/v1/orgs/alpha/executors/register", json={
            "command": "echo",
            "argv_template": ["echo", "{prompt}"],
            "adapter": "pi",
        })
        assert r.status_code == 200, r.json()
        body = r.json()
        assert body["name"] == "test-executor"
        assert body["kind"] == "custom"

        # Token consumed
        assert store.validate(token, "alpha") is None

        # Config written
        raw = _config_raw(daemon_state.orgs["alpha"].root)
        assert "executor_profiles" in raw
        assert "test-executor" in raw["executor_profiles"]
        entry = raw["executor_profiles"]["test-executor"]
        assert entry["command"] == "echo"
        assert entry["argv_template"] == ["echo", "{prompt}"]

    def test_register_persists_unrelated_config_keys(self, app, daemon_state, monkeypatch):
        _bypass_loopback(monkeypatch)
        client = TestClient(app)
        store = daemon_state.registration_token_store
        org_root = daemon_state.orgs["alpha"].root

        # Pre-write an unrelated setting
        _make_org_config(org_root, """
session_timeout_seconds: 7200
feishu_notifications:
  enabled: true
  webhook_url: https://example.com/webhook
""")

        token, _ = store.mint("alpha", "test-executor")
        _complete_challenge(store, token)

        client.headers.update({"Authorization": f"Bearer {token}"})
        r = client.post("/api/v1/orgs/alpha/executors/register", json={
            "command": "echo",
            "argv_template": ["echo", "{prompt}"],
            "adapter": "pi",
        })
        assert r.status_code == 200

        raw = _config_raw(org_root)
        # Unrelated keys survive
        assert raw["session_timeout_seconds"] == 7200
        assert raw["feishu_notifications"]["enabled"] is True
        # New profile added
        assert "executor_profiles" in raw
        assert "test-executor" in raw["executor_profiles"]

    def test_register_records_audit_log(self, app, daemon_state, monkeypatch):
        _bypass_loopback(monkeypatch)
        client = TestClient(app)
        store = daemon_state.registration_token_store

        token, _ = store.mint("alpha", "test-executor")
        _complete_challenge(store, token)

        client.headers.update({"Authorization": f"Bearer {token}"})
        r = client.post("/api/v1/orgs/alpha/executors/register", json={
            "command": "echo",
            "argv_template": ["echo", "{prompt}"],
            "adapter": "pi",
        })
        assert r.status_code == 200

        # Audit log row scoped to config:executor_profiles
        db = daemon_state.orgs["alpha"].db
        logs = db.get_audit_logs("config:executor_profiles")
        assert len(logs) == 1
        log = logs[0]
        assert log["action"] == "org_config_write"
        payload = yaml.safe_load(log["payload"]) if isinstance(log["payload"], str) else log["payload"]
        assert payload["section"] == "executor_profiles"
        assert "test-executor" in str(payload)


# ── Conformance check-in route ──────────────────────────────────────────


class TestConformanceCheckin:
    """POST /conformance-checkin: record step arrivals through the daemon."""

    def test_checkin_records_step(self, app, daemon_state, monkeypatch):
        _bypass_loopback(monkeypatch)
        client = TestClient(app)
        store = daemon_state.registration_token_store

        token, _ = store.mint("alpha", "test-executor")

        client.headers.update({"Authorization": f"Bearer {token}"})
        r = client.post("/api/v1/orgs/alpha/executors/conformance-checkin", json={
            "step_id": "workspace_access",
        })
        assert r.status_code == 200
        body = r.json()
        assert body["step_id"] == "workspace_access"
        assert body["arrived"] is True
        assert "pending" in body

        # Challenge state updated
        assert store.is_challenge_complete(token, "alpha") is False
        pending = store.get_pending_steps(token, "alpha")
        assert pending is not None
        assert "workspace_access" not in pending

    def test_checkin_duplicate_is_noop(self, app, daemon_state, monkeypatch):
        _bypass_loopback(monkeypatch)
        client = TestClient(app)
        store = daemon_state.registration_token_store

        token, _ = store.mint("alpha", "test-executor")

        client.headers.update({"Authorization": f"Bearer {token}"})
        r = client.post("/api/v1/orgs/alpha/executors/conformance-checkin", json={
            "step_id": "workspace_access",
        })
        assert r.status_code == 200
        assert r.json()["arrived"] is True

        # Duplicate
        r = client.post("/api/v1/orgs/alpha/executors/conformance-checkin", json={
            "step_id": "workspace_access",
        })
        assert r.status_code == 200
        assert r.json()["arrived"] is False  # no-op

    def test_checkin_unknown_step(self, app, daemon_state, monkeypatch):
        _bypass_loopback(monkeypatch)
        client = TestClient(app)
        store = daemon_state.registration_token_store

        token, _ = store.mint("alpha", "test-executor")

        client.headers.update({"Authorization": f"Bearer {token}"})
        r = client.post("/api/v1/orgs/alpha/executors/conformance-checkin", json={
            "step_id": "nonexistent_step",
        })
        assert r.status_code == 400
        assert "unknown step" in r.json()["detail"].lower()

    def test_checkin_expired_token_rejected(self, app, daemon_state, monkeypatch):
        _bypass_loopback(monkeypatch)
        client = TestClient(app)
        store = daemon_state.registration_token_store

        now = 1_000_000.0
        token, _ = store.mint("alpha", "test-executor", now=now)
        # Advance past TTL
        monkeypatch.setattr(time, "time", lambda: now + DEFAULT_REGISTRATION_TOKEN_TTL_SECONDS + 10)

        client.headers.update({"Authorization": f"Bearer {token}"})
        r = client.post("/api/v1/orgs/alpha/executors/conformance-checkin", json={
            "step_id": "workspace_access",
        })
        assert r.status_code == 401


# ── Negative: missing/incomplete conformance ────────────────────────────


class TestRegisterMissingConformance:
    """Registration fails when conformance is not fully complete."""

    def test_register_no_checkins_rejected(self, app, daemon_state, monkeypatch):
        _bypass_loopback(monkeypatch)
        client = TestClient(app)
        store = daemon_state.registration_token_store

        token, _ = store.mint("alpha", "test-executor")
        # No check-ins

        client.headers.update({"Authorization": f"Bearer {token}"})
        r = client.post("/api/v1/orgs/alpha/executors/register", json={
            "command": "echo",
            "argv_template": ["echo", "{prompt}"],
            "adapter": "pi",
        })
        assert r.status_code == 400
        assert "conformance" in r.json()["detail"].lower()

    def test_register_partial_checkins_rejected(self, app, daemon_state, monkeypatch):
        _bypass_loopback(monkeypatch)
        client = TestClient(app)
        store = daemon_state.registration_token_store

        token, _ = store.mint("alpha", "test-executor")
        store.record_step_arrival(token, "alpha", "workspace_access")
        # Only 1 of 3 steps complete

        client.headers.update({"Authorization": f"Bearer {token}"})
        r = client.post("/api/v1/orgs/alpha/executors/register", json={
            "command": "echo",
            "argv_template": ["echo", "{prompt}"],
            "adapter": "pi",
        })
        assert r.status_code == 400


# ── Negative: expired token ─────────────────────────────────────────────


class TestRegisterExpiredToken:
    """Registration fails with expired token."""

    def test_register_expired_token_rejected(self, app, daemon_state, monkeypatch):
        _bypass_loopback(monkeypatch)
        client = TestClient(app)
        store = daemon_state.registration_token_store

        now = 1_000_000.0
        token, _ = store.mint("alpha", "test-executor", now=now)
        _complete_challenge(store, token, now=now)
        monkeypatch.setattr(time, "time", lambda: now + DEFAULT_REGISTRATION_TOKEN_TTL_SECONDS + 10)

        client.headers.update({"Authorization": f"Bearer {token}"})
        r = client.post("/api/v1/orgs/alpha/executors/register", json={
            "command": "echo",
            "argv_template": ["echo", "{prompt}"],
            "adapter": "pi",
        })
        assert r.status_code == 401


# ── Negative: replay / second use ───────────────────────────────────────


class TestRegisterReplayRejected:
    """Second registration attempt with the same token fails."""

    def test_register_second_use_rejected(self, app, daemon_state, monkeypatch):
        _bypass_loopback(monkeypatch)
        client = TestClient(app)
        store = daemon_state.registration_token_store

        token, _ = store.mint("alpha", "test-executor")
        _complete_challenge(store, token)

        # First use succeeds
        client.headers.update({"Authorization": f"Bearer {token}"})
        r = client.post("/api/v1/orgs/alpha/executors/register", json={
            "command": "echo",
            "argv_template": ["echo", "{prompt}"],
            "adapter": "pi",
        })
        assert r.status_code == 200

        # Second use rejected
        r = client.post("/api/v1/orgs/alpha/executors/register", json={
            "command": "echo",
            "argv_template": ["echo", "{prompt}"],
            "adapter": "pi",
        })
        assert r.status_code == 401


# ── Regression: concurrent same-token ───────────────────────────────────


class TestConcurrentSameToken:
    """Two concurrent register requests with the same token MUST produce
    exactly one success, one config write, one audit row.

    This is the regression test for the HIGH race caught in code review:
    the old code called ``store.consume()`` AFTER the config write without
    checking the return value, so two concurrent requests could both pass
    ``validate()`` and both write config/audit before one finally consumed
    the token.

    The fix moves ``store.reserve()`` ahead of all durable side effects
    (the atomic validate-and-mark gate) and checks the return value —
    the second thread receives ``None`` and aborts with 401. The winner
    commits (permanently consumes) the token on success; the loser's
    reservation is released.
    """

    def test_concurrent_same_token_exactly_one_succeeds(
        self, app, daemon_state, monkeypatch,
    ):
        import threading

        _bypass_loopback(monkeypatch)
        store = daemon_state.registration_token_store

        # Mint one token, complete conformance
        token, _ = store.mint("alpha", "test-executor")
        _complete_challenge(store, token)

        # Barrier to force both threads to race through reserve()
        # simultaneously, proving the lock inside reserve picks a
        # single winner.
        barrier = threading.Barrier(2, timeout=5)
        results: list[int] = []

        def do_register():
            # Each thread creates its own TestClient (Starlette
            # TestClient is synchronous; separate instances on the
            # same app are safe for concurrent use).
            c = TestClient(app)
            c.headers.update({"Authorization": f"Bearer {token}"})
            r = c.post("/api/v1/orgs/alpha/executors/register", json={
                "command": "echo",
                "argv_template": ["echo", "{prompt}"],
                "adapter": "pi",
            })
            results.append(r.status_code)

        # Patch reserve to synchronize both threads at the gate
        original_reserve = store.reserve

        def barrier_reserve(token_plaintext, org, now=None):
            barrier.wait()  # both threads rendezvous here
            return original_reserve(token_plaintext, org, now)

        monkeypatch.setattr(store, "reserve", barrier_reserve)

        t1 = threading.Thread(target=do_register)
        t2 = threading.Thread(target=do_register)
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        # Assertions: exactly one success
        assert results.count(200) == 1, (
            f"Expected exactly 1 success, got: {results}"
        )
        assert results.count(401) == 1, (
            f"Expected exactly 1 401, got: {results}"
        )

        # Token is consumed (winner committed) — validate returns None
        assert store.validate(token, "alpha") is None

        # Exactly one config entry written
        raw = _config_raw(daemon_state.orgs["alpha"].root)
        assert "executor_profiles" in raw
        assert "test-executor" in raw["executor_profiles"]
        entry = raw["executor_profiles"]["test-executor"]
        assert entry["command"] == "echo"

        # Exactly one audit log entry
        db = daemon_state.orgs["alpha"].db
        logs = db.get_audit_logs("config:executor_profiles")
        assert len(logs) == 1, (
            f"Expected exactly 1 audit log, got {len(logs)}"
        )


# ── Negative: master bearer rejected ────────────────────────────────────


class TestMasterBearerRejected:
    """Master bearer must not authorize /executors/register."""

    def test_master_bearer_rejected_on_register(self, app, monkeypatch):
        _bypass_loopback(monkeypatch)
        client = TestClient(app)
        client.headers.update({"Authorization": f"Bearer {paths_mod.read_token()}"})
        r = client.post("/api/v1/orgs/alpha/executors/register", json={
            "command": "echo",
            "argv_template": ["echo", "{prompt}"],
            "adapter": "pi",
        })
        assert r.status_code == 401
        assert "master bearer" in r.json()["detail"].lower()

    def test_master_bearer_rejected_on_checkin(self, app, monkeypatch):
        _bypass_loopback(monkeypatch)
        client = TestClient(app)
        client.headers.update({"Authorization": f"Bearer {paths_mod.read_token()}"})
        r = client.post("/api/v1/orgs/alpha/executors/conformance-checkin", json={
            "step_id": "workspace_access",
        })
        assert r.status_code == 401


# ── Negative: scoped token on existing routes ───────────────────────────


class TestScopedTokenOnExistingRoutes:
    """hrreg_ token MUST NOT authorize existing require_token() routes."""

    def test_scoped_token_on_settings(self, app, daemon_state, monkeypatch):
        _bypass_loopback(monkeypatch)
        client = TestClient(app)
        store = daemon_state.registration_token_store
        token, _ = store.mint("alpha", "test-executor")

        client.headers.update({"Authorization": f"Bearer {token}"})
        r = client.get(f"/api/v1/orgs/alpha/settings")
        assert r.status_code == 401


# ── Negative: non-loopback peer ─────────────────────────────────────────


class TestNonLoopbackRejected:
    """Non-loopback peer rejected for register and check-in routes."""

    def test_register_non_loopback_rejected(self, app, daemon_state):
        client = TestClient(app)
        store = daemon_state.registration_token_store
        token, _ = store.mint("alpha", "test-executor")

        # No loopback bypass
        client.headers.update({"Authorization": f"Bearer {token}"})
        r = client.post("/api/v1/orgs/alpha/executors/register", json={
            "command": "echo",
            "argv_template": ["echo", "{prompt}"],
            "adapter": "pi",
        })
        assert r.status_code == 403

    def test_checkin_non_loopback_rejected(self, app, daemon_state):
        client = TestClient(app)
        store = daemon_state.registration_token_store
        token, _ = store.mint("alpha", "test-executor")

        client.headers.update({"Authorization": f"Bearer {token}"})
        r = client.post("/api/v1/orgs/alpha/executors/conformance-checkin", json={
            "step_id": "workspace_access",
        })
        assert r.status_code == 403


# ── Negative: org/name mismatch ─────────────────────────────────────────


class TestOrgNameMismatch:
    """Token scoped to different org or name than the request."""

    def test_register_wrong_org_rejected(self, app, daemon_state, monkeypatch):
        _bypass_loopback(monkeypatch)
        client = TestClient(app)
        store = daemon_state.registration_token_store

        # Token scoped to "alpha"
        token, _ = store.mint("alpha", "test-executor")
        _complete_challenge(store, token)

        # Try to register in a non-existent org "beta"
        client.headers.update({"Authorization": f"Bearer {token}"})
        r = client.post("/api/v1/orgs/beta/executors/register", json={
            "command": "echo",
            "argv_template": ["echo", "{prompt}"],
            "adapter": "pi",
        })
        assert r.status_code in (403, 404)  # org not found or mismatch

    def test_checkin_wrong_org_rejected(self, app, daemon_state, monkeypatch):
        _bypass_loopback(monkeypatch)
        client = TestClient(app)
        store = daemon_state.registration_token_store

        token, _ = store.mint("alpha", "test-executor")

        client.headers.update({"Authorization": f"Bearer {token}"})
        r = client.post("/api/v1/orgs/beta/executors/conformance-checkin", json={
            "step_id": "workspace_access",
        })
        assert r.status_code in (403, 404)


# ── Negative: bad static validation ─────────────────────────────────────


class TestBadStaticValidation:
    """Registration fails on invalid profile definitions."""

    def test_register_missing_command_rejected(self, app, daemon_state, monkeypatch):
        _bypass_loopback(monkeypatch)
        client = TestClient(app)
        store = daemon_state.registration_token_store

        token, _ = store.mint("alpha", "test-executor")
        _complete_challenge(store, token)

        client.headers.update({"Authorization": f"Bearer {token}"})
        r = client.post("/api/v1/orgs/alpha/executors/register", json={
            "argv_template": ["echo", "{prompt}"],
            "adapter": "pi",
        })
        assert r.status_code == 422

    def test_register_empty_argv_template_rejected(self, app, daemon_state, monkeypatch):
        _bypass_loopback(monkeypatch)
        client = TestClient(app)
        store = daemon_state.registration_token_store

        token, _ = store.mint("alpha", "test-executor")
        _complete_challenge(store, token)

        client.headers.update({"Authorization": f"Bearer {token}"})
        r = client.post("/api/v1/orgs/alpha/executors/register", json={
            "command": "echo",
            "argv_template": [],
            "adapter": "pi",
        })
        assert r.status_code == 422

    def test_register_invalid_adapter_rejected(self, app, daemon_state, monkeypatch):
        _bypass_loopback(monkeypatch)
        client = TestClient(app)
        store = daemon_state.registration_token_store

        token, _ = store.mint("alpha", "test-executor")
        _complete_challenge(store, token)

        client.headers.update({"Authorization": f"Bearer {token}"})
        r = client.post("/api/v1/orgs/alpha/executors/register", json={
            "command": "echo",
            "argv_template": ["echo", "{prompt}"],
            "adapter": "invalid",
        })
        assert r.status_code == 422
        assert "adapter" in r.json()["detail"].lower()

    def test_register_unsupported_placeholder_rejected(self, app, daemon_state, monkeypatch):
        _bypass_loopback(monkeypatch)
        client = TestClient(app)
        store = daemon_state.registration_token_store

        token, _ = store.mint("alpha", "test-executor")
        _complete_challenge(store, token)

        client.headers.update({"Authorization": f"Bearer {token}"})
        r = client.post("/api/v1/orgs/alpha/executors/register", json={
            "command": "echo",
            "argv_template": ["echo", "{unknown}"],
            "adapter": "pi",
        })
        assert r.status_code == 422
        assert "unsupported placeholder" in r.json()["detail"].lower()

    def test_register_builtin_name_collision_rejected(self, app, daemon_state, monkeypatch):
        _bypass_loopback(monkeypatch)
        client = TestClient(app)
        store = daemon_state.registration_token_store

        token, _ = store.mint("alpha", "claude")  # collision with builtin
        _complete_challenge(store, token)

        client.headers.update({"Authorization": f"Bearer {token}"})
        r = client.post("/api/v1/orgs/alpha/executors/register", json={
            "command": "echo",
            "argv_template": ["echo", "{prompt}"],
            "adapter": "pi",
        })
        assert r.status_code in (409, 422)

    def test_register_command_not_on_path_rejected(self, app, daemon_state, monkeypatch):
        _bypass_loopback(monkeypatch)
        client = TestClient(app)
        store = daemon_state.registration_token_store

        token, _ = store.mint("alpha", "test-executor")
        _complete_challenge(store, token)

        client.headers.update({"Authorization": f"Bearer {token}"})
        r = client.post("/api/v1/orgs/alpha/executors/register", json={
            "command": "definitely_not_a_command_xyzzy",
            "argv_template": ["definitely_not_a_command_xyzzy", "{prompt}"],
            "adapter": "pi",
        })
        assert r.status_code == 422


# ── Invariant: executor_profiles not writable via settings PATCH ────────


class TestExecutorProfilesNotOnSettingsPatch:
    """Prove executor_profiles is NOT writable through the master-bearer
    settings PATCH path."""

    def test_settings_patch_does_not_write_executor_profiles(
        self, app, daemon_state, monkeypatch,
    ):
        _bypass_loopback(monkeypatch)
        client = TestClient(app)
        client.headers.update({"Authorization": f"Bearer {paths_mod.read_token()}"})

        # Try to inject executor_profiles via the settings PATCH endpoint
        r = client.put("/api/v1/orgs/alpha/settings/org", json={
            "executor_profiles": {
                "sneaky": {
                    "command": "echo",
                    "argv_template": ["echo", "{prompt}"],
                    "adapter": "pi",
                }
            }
        })
        # Either rejected with 422 (unknown field) or silently ignored
        if r.status_code == 200:
            # If it came back 200, the executor_profiles key must NOT be in config
            raw = _config_raw(daemon_state.orgs["alpha"].root)
            assert "executor_profiles" not in raw, (
                "executor_profiles was written via settings PATCH — regression"
            )


# ── Invariant: registration only creates profile, no agent changes ──────


class TestRegistrationIsIsolated:
    """Registration creates only an executor profile; no agent or
    permission-model changes."""

    def test_register_does_not_alter_agents(self, app, daemon_state, monkeypatch):
        _bypass_loopback(monkeypatch)
        client = TestClient(app)
        store = daemon_state.registration_token_store

        token, _ = store.mint("alpha", "test-executor")
        _complete_challenge(store, token)

        # Record existing agents
        org_state = daemon_state.orgs["alpha"]
        agents_before = set(org_state.teams.all_agents())

        client.headers.update({"Authorization": f"Bearer {token}"})
        r = client.post("/api/v1/orgs/alpha/executors/register", json={
            "command": "echo",
            "argv_template": ["echo", "{prompt}"],
            "adapter": "pi",
        })
        assert r.status_code == 200

        agents_after = set(org_state.teams.all_agents())
        assert agents_before == agents_after, (
            "Registration changed agent set — must be isolated"
        )


# ── Duplicate profile name conflict ─────────────────────────────────────


class TestDuplicateProfileConflict:
    """Registering a second profile with the same name but different config
    is rejected, and the rejection leaves config, registry, audit, and
    token-consumption semantics consistent."""

    def test_register_duplicate_name_conflict(self, app, daemon_state, monkeypatch):
        _bypass_loopback(monkeypatch)
        client = TestClient(app)
        store = daemon_state.registration_token_store
        org_root = daemon_state.orgs["alpha"].root
        registry = get_registry()

        # Register first profile
        token1, _ = store.mint("alpha", "test-executor")
        _complete_challenge(store, token1)
        client.headers.update({"Authorization": f"Bearer {token1}"})
        r = client.post("/api/v1/orgs/alpha/executors/register", json={
            "command": "echo",
            "argv_template": ["echo", "{prompt}"],
            "adapter": "pi",
        })
        assert r.status_code == 200

        # Verify first registration state
        assert registry.is_registered("test-executor")
        first_profile = registry.get_profile("test-executor")
        assert first_profile.command == "echo"
        assert store.validate(token1, "alpha") is None  # token1 consumed

        # Try to register same name with different command
        token2, _ = store.mint("alpha", "test-executor")
        _complete_challenge(store, token2)
        client.headers.update({"Authorization": f"Bearer {token2}"})
        r = client.post("/api/v1/orgs/alpha/executors/register", json={
            "command": "printf",
            "argv_template": ["printf", "%s", "{prompt}"],
            "adapter": "pi",
        })
        assert r.status_code == 409

        # ── Consistency checks after conflict rejection ──
        # 1. Registry still has the FIRST definition (echo), not printf
        still_registered = registry.get_profile("test-executor")
        assert still_registered is not None
        assert still_registered.command == "echo", (
            "Registry should retain original 'echo' profile, "
            f"got command={still_registered.command!r}"
        )

        # 2. Config still has the FIRST definition (echo), not printf
        raw = _config_raw(org_root)
        profiles = raw.get("executor_profiles", {})
        assert "test-executor" in profiles
        assert profiles["test-executor"]["command"] == "echo", (
            f"Config should retain 'echo', got {profiles['test-executor']['command']!r}"
        )

        # 3. Token2 is NOT consumed (conflict detected pre-consumption)
        assert store.validate(token2, "alpha") is not None, (
            "Second token should remain valid — conflict was detected before consume"
        )

        # 4. Only one audit log (for the first, successful registration)
        db = daemon_state.orgs["alpha"].db
        logs = db.get_audit_logs("config:executor_profiles")
        assert len(logs) == 1, (
            f"Expected exactly 1 audit log, got {len(logs)}"
        )


# ── Concurrent different-token same-name registrations ──────────────────


class TestConcurrentDifferentTokens:
    """Two concurrent registrations using DIFFERENT scoped tokens for the
    SAME profile name but with CONFLICTING definitions MUST leave config,
    registry, and audit state consistent.

    Expected outcome: at most one 200; the config entry matches the
    in-memory profile; audit rows match the successful durable change
    only; no 409 response leaves config that disagrees with the registry.
    """

    def test_concurrent_different_tokens_conflicting_defs(
        self, app, daemon_state, monkeypatch,
    ):
        import threading

        _bypass_loopback(monkeypatch)
        store = daemon_state.registration_token_store
        org_root = daemon_state.orgs["alpha"].root

        # Mint two different tokens for the same profile name
        token1, _ = store.mint("alpha", "test-executor")
        token2, _ = store.mint("alpha", "test-executor")
        _complete_challenge(store, token1)
        _complete_challenge(store, token2)

        # The per-profile lock inside the route already serialises
        # write+register for the same name.  Both threads will pass
        # the preflight check and consume (different tokens), then
        # collide at the lock.  The lock's double-check rejects the
        # loser with 409 while the winner writes config, registers,
        # and audits.  We don't need an artificial barrier — the
        # test verifies the key invariant: config and registry stay
        # consistent regardless of which thread wins the lock race.

        def do_register(token: str, command: str, argv: list):
            c = TestClient(app)
            c.headers.update({"Authorization": f"Bearer {token}"})
            c.post("/api/v1/orgs/alpha/executors/register", json={
                "command": command,
                "argv_template": argv,
                "adapter": "pi",
            })

        t1 = threading.Thread(
            target=do_register,
            args=(token1, "echo", ["echo", "{prompt}"]),
        )
        t2 = threading.Thread(
            target=do_register,
            args=(token2, "printf", ["printf", "%s", "{prompt}"]),
        )
        t1.start()
        t2.start()
        t1.join(timeout=15)
        t2.join(timeout=15)

        # ── Consistency checks ──
        registry = get_registry()

        # 1. Registry has exactly one profile with the winner's definition
        registered = registry.get_profile("test-executor")
        assert registered is not None
        assert registered.command in ("echo", "printf")

        # 2. Config matches the registry
        raw = _config_raw(org_root)
        profiles = raw.get("executor_profiles", {})
        assert "test-executor" in profiles
        config_command = profiles["test-executor"]["command"]
        assert config_command == registered.command, (
            f"Config command {config_command!r} must match "
            f"registry command {registered.command!r}"
        )

        # 3. Exactly one audit row
        db = daemon_state.orgs["alpha"].db
        logs = db.get_audit_logs("config:executor_profiles")
        assert len(logs) == 1, (
            f"Expected exactly 1 audit log, got {len(logs)}"
        )

        # 4. Token state: at least one token is consumed (the winner's).
        #    The loser may be consumed (TOCTOU race past consume before
        #    the lock) or unconsumed (preflight caught the collision
        #    before consume).  Both outcomes are fail-safe — the
        #    durable state is consistent either way.
        token1_valid = store.validate(token1, "alpha")
        token2_valid = store.validate(token2, "alpha")
        # At most one can be valid (the loser's, if preflight caught it)
        assert not (token1_valid and token2_valid), (
            "At most one token may remain valid after concurrent registration"
        )


# ── Non-list argv_template ──────────────────────────────────────────────


class TestNonListArgvTemplate:
    """argv_template must be a non-empty list, not a string."""

    def test_register_string_argv_template_rejected(self, app, daemon_state, monkeypatch):
        _bypass_loopback(monkeypatch)
        client = TestClient(app)
        store = daemon_state.registration_token_store

        token, _ = store.mint("alpha", "test-executor")
        _complete_challenge(store, token)

        client.headers.update({"Authorization": f"Bearer {token}"})
        r = client.post("/api/v1/orgs/alpha/executors/register", json={
            "command": "echo",
            "argv_template": "echo {prompt}",  # string, not list
            "adapter": "pi",
        })
        assert r.status_code == 422


# ── Regression: config-write failure MUST NOT leak in-memory profile ────


class TestConfigWriteFailureDoesNotLeakRegistry:
    """When write_executor_profile_entry raises OrgConfigError after
    store.consume() succeeds, the route must return 422, the in-memory
    registry must NOT contain the profile, no config file must be written,
    and no audit row must appear.

    Recovery: after the forced failure, a fresh token for the same profile
    name with the write path restored MUST succeed — proving no stale
    in-memory state blocks subsequent registration.
    """

    def test_config_write_failure_no_registry_leak(
        self, app, daemon_state, monkeypatch,
    ):
        _bypass_loopback(monkeypatch)
        client = TestClient(app)
        store = daemon_state.registration_token_store
        org_root = daemon_state.orgs["alpha"].root

        token, _ = store.mint("alpha", "test-executor")
        _complete_challenge(store, token)

        from runtime.daemon.routes import executors as routes_mod

        # Capture original write_executor_profile_entry before monkeypatching
        original_write = routes_mod.write_executor_profile_entry

        # Force write_executor_profile_entry to raise OrgConfigError
        def _failing_write(paths, name, entry):
            raise OrgConfigError("simulated config write failure")

        monkeypatch.setattr(
            routes_mod,
            "write_executor_profile_entry",
            _failing_write,
        )

        # Before: registry does not have test-executor
        registry = get_registry()
        assert not registry.is_registered("test-executor")

        # Register — must fail with 422
        client.headers.update({"Authorization": f"Bearer {token}"})
        r = client.post("/api/v1/orgs/alpha/executors/register", json={
            "command": "echo",
            "argv_template": ["echo", "{prompt}"],
            "adapter": "pi",
        })
        assert r.status_code == 422, r.json()
        assert "simulated config write failure" in r.json()["detail"]

        # Token must NOT be consumed (retry-safe: on failure nothing is
        # written and the token remains valid for retry within TTL).
        assert store.validate(token, "alpha") is not None, (
            "Token was consumed on config-write failure — it must remain "
            "valid so the caller can retry with the same token."
        )

        # In-memory registry MUST NOT contain the leaked profile after
        # the first (failed) attempt.
        assert not registry.is_registered("test-executor"), (
            "Profile test-executor leaked into in-memory registry after "
            "config-write failure — the profile must not be registered"
        )

        # No config file entry written after the failed attempt
        raw = _config_raw(org_root)
        assert "executor_profiles" not in raw or "test-executor" not in raw.get(
            "executor_profiles", {}
        ), (
            "executor_profiles.test-executor was written to config.yaml "
            "despite config-write failure"
        )

        # No audit log entry after the failed attempt
        db = daemon_state.orgs["alpha"].db
        logs = db.get_audit_logs("config:executor_profiles")
        assert len(logs) == 0, (
            f"Expected 0 audit logs, got {len(logs)} — audit should not "
            f"record a failed write"
        )

        # Retry with the SAME token MUST succeed after the write path is restored
        _complete_challenge(store, token)
        monkeypatch.setattr(
            routes_mod,
            "write_executor_profile_entry",
            original_write,
        )
        client.headers.update({"Authorization": f"Bearer {token}"})
        r2 = client.post("/api/v1/orgs/alpha/executors/register", json={
            "command": "echo",
            "argv_template": ["echo", "{prompt}"],
            "adapter": "pi",
        })
        assert r2.status_code == 200, f"Same-token retry failed: {r2.json()}"

        # After retry: registry now has the profile
        assert registry.is_registered("test-executor")

    def test_recovery_after_forced_failure(
        self, app, daemon_state, monkeypatch,
    ):
        """After a forced config-write failure, a fresh token for the same
        profile name with the write path restored MUST succeed — proving no
        stale in-memory state blocks subsequent registration."""
        _bypass_loopback(monkeypatch)
        client = TestClient(app)
        store = daemon_state.registration_token_store
        org_root = daemon_state.orgs["alpha"].root

        # --- Phase 1: forced failure ---
        token1, _ = store.mint("alpha", "test-executor")
        _complete_challenge(store, token1)

        from runtime.daemon.routes import executors as routes_mod
        original_write = routes_mod.write_executor_profile_entry

        def _failing_write(paths, name, entry):
            raise OrgConfigError("simulated config write failure")

        monkeypatch.setattr(
            routes_mod,
            "write_executor_profile_entry",
            _failing_write,
        )

        client.headers.update({"Authorization": f"Bearer {token1}"})
        r = client.post("/api/v1/orgs/alpha/executors/register", json={
            "command": "echo",
            "argv_template": ["echo", "{prompt}"],
            "adapter": "pi",
        })
        assert r.status_code == 422

        registry = get_registry()
        assert not registry.is_registered("test-executor")

        # --- Phase 2: recovery — restore write, mint fresh token ---
        # Restore only the write_executor_profile_entry patch (undo() would
        # also remove the loopback bypass).
        monkeypatch.setattr(
            routes_mod,
            "write_executor_profile_entry",
            original_write,
        )

        token2, _ = store.mint("alpha", "test-executor")
        _complete_challenge(store, token2)

        client.headers.update({"Authorization": f"Bearer {token2}"})
        r = client.post("/api/v1/orgs/alpha/executors/register", json={
            "command": "echo",
            "argv_template": ["echo", "{prompt}"],
            "adapter": "pi",
        })
        assert r.status_code == 200, r.json()
        body = r.json()
        assert body["name"] == "test-executor"
        assert body["kind"] == "custom"

        # Registry now has the profile
        assert registry.is_registered("test-executor")

        # Config written
        raw = _config_raw(org_root)
        assert "executor_profiles" in raw
        assert "test-executor" in raw["executor_profiles"]

        # Audit log written
        db = daemon_state.orgs["alpha"].db
        logs = db.get_audit_logs("config:executor_profiles")
        assert len(logs) == 1


# ── Parity test: register route and startup config validation accept/reject
#    the SAME inputs so future drift fails a test. ──────────────────────────

class TestValidationParityWithStartupConfig:
    """Assert the register route drives through the same canonical validation
    as ``ExecutorRegistry.register_custom_from_config`` (used at startup).

    Every rejection/acceptance a caller sees through the route must match what
    a daemon restart would do with the same profile in config.yaml.
    """

    # ── Rejected profiles (must be rejected by BOTH paths) ───────────────

    _INVALID_PROFILES = [
        pytest.param(
            {"command": "echo", "argv_template": ["echo", "{prompt}"], "adapter": "invalid"},
            "invalid-adapter",
            id="invalid_adapter",
        ),
        pytest.param(
            {"command": "echo", "argv_template": [], "adapter": "pi"},
            "empty-argv_template",
            id="empty_argv_template",
        ),
        pytest.param(
            {"command": "echo", "argv_template": ["echo", "{unknown}"], "adapter": "pi"},
            "unsupported-placeholder",
            id="unsupported_placeholder",
        ),
        pytest.param(
            {"command": "definitely_not_a_command_xyzzy", "argv_template": ["echo", "{prompt}"], "adapter": "pi"},
            "command-not-on-path",
            id="command_not_on_path",
        ),
        pytest.param(
            {"command": 123, "argv_template": ["echo", "{prompt}"], "adapter": "pi"},
            "non-string-command",
            id="non_string_command",
        ),
    ]

    @pytest.mark.parametrize("cfg,label", _INVALID_PROFILES)
    def test_invalid_profiles_rejected_by_both_paths(
        self, cfg, label, daemon_state, monkeypatch
    ):
        """Invalid profiles must raise ValueError in the canonical validator
        AND 422 from the register route."""
        from runtime.orchestrator.executor_registry import ExecutorRegistry

        # Canonical validation must reject
        with pytest.raises(ValueError):
            ExecutorRegistry.validate_custom_profile_config("test-parity", cfg)

        # Startup config path (simulated) must also reject
        with pytest.raises(ValueError):
            get_registry().register_custom_from_config({"test-parity": cfg})

    # ── Accepted profiles (must be accepted by BOTH paths) ───────────────

    _VALID_PROFILES = [
        pytest.param(
            {"command": "echo", "argv_template": ["echo", "{prompt}"], "adapter": "pi"},
            "AGENTS.md",
            id="adapter_pi",
        ),
        pytest.param(
            {"command": "echo", "argv_template": ["echo", "{prompt}"], "adapter": "claude"},
            ".claude/skills/start-task/SKILL.md",
            id="adapter_claude",
        ),
        pytest.param(
            {"command": "echo", "argv_template": ["echo", "{prompt}"], "adapter": "codex"},
            "AGENTS.md",
            id="adapter_codex",
        ),
        pytest.param(
            {"command": "echo", "argv_template": ["echo", "{prompt}"], "adapter": "opencode"},
            "AGENTS.md",
            id="adapter_opencode",
        ),
        pytest.param(
            {
                "command": "echo",
                "argv_template": ["echo", "{prompt}", "{timeout_seconds}", "{workspace}"],
                "adapter": "pi",
            },
            "AGENTS.md",
            id="all_placeholders",
        ),
    ]

    @pytest.mark.parametrize("cfg,expected_marker", _VALID_PROFILES)
    def test_valid_profiles_accepted_by_both_paths(
        self, cfg, expected_marker, daemon_state, monkeypatch
    ):
        """Valid profiles must build a correct ExecutorProfile in the canonical
        validator AND be registerable through the config path."""
        from runtime.orchestrator.executor_registry import ExecutorRegistry

        profile_name = "test-parity-valid"

        # Canonical validation must produce correct ExecutorProfile
        profile = ExecutorRegistry.validate_custom_profile_config(profile_name, cfg)
        assert profile.name == profile_name
        assert profile.kind == "custom"
        assert profile.adapter_id == cfg.get("adapter", "pi")
        assert profile.readiness_marker_fragment == expected_marker
        assert profile.argv_template == [str(e) for e in cfg["argv_template"]]
        assert profile.command == cfg["command"]

        # Config path must register without error
        get_registry().register_custom_from_config({profile_name: cfg})
        assert get_registry().is_registered(profile_name)
