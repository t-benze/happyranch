"""Connector supervisor tests (THR-097 phase unit 3).

Deterministic lifecycle coverage with fakes: readiness-gated listener start,
fail-closed listener stop on readiness loss, READY/WATCHDOG/STOPPING notify,
service-manager delegation (install/start/stop/upgrade/rollback), lab device
provisioning in first-run state, revocation across a restart, and redacted
local diagnostics.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from runtime.remote_access.lab_provider import LAB_ONLY_BANNER, LabProviderConfig, LabProviderError
from runtime.remote_access.readiness import (
    ConnectorReadiness,
    GateResult,
    ReadinessReport,
)
from runtime.remote_access.revocation import RevocationCoordinator
from runtime.remote_access.service_manager import ServiceStatus, UpgradeOutcome
from runtime.remote_access.state_store import AtomicFileTrustStateStore
from runtime.remote_access.streams import StreamRegistry
from runtime.remote_access.supervisor import (
    ConnectorConfig,
    ConnectorConfigError,
    ConnectorSupervisor,
    sd_notify,
)

from .conftest import NOW, build_consumer, default_identity

_UNSET = object()


class FakeManager:
    def __init__(self) -> None:
        self.installed: list[tuple[str, str]] = []
        self.calls: list[str] = []
        self.status_result = ServiceStatus(
            unit_name="happyranch-connector.service",
            active_state="active",
            sub_state="running",
            load_state="loaded",
            pid=4242,
        )

    def install(self, unit_text, unit_name, *, enable=True):
        self.installed.append((unit_name, unit_text))
        self.calls.append("install")
        return Path("/fake/unit")

    def uninstall(self, unit_name):
        self.calls.append("uninstall")

    def start(self, unit_name):
        self.calls.append("start")

    def stop(self, unit_name):
        self.calls.append("stop")

    def restart(self, unit_name):
        self.calls.append("restart")

    def enable(self, unit_name):
        self.calls.append("enable")

    def disable(self, unit_name):
        self.calls.append("disable")

    def status(self, unit_name):
        self.calls.append("status")
        return self.status_result

    def upgrade(self, unit_text, unit_name, *, verify_start=True):
        self.calls.append("upgrade")
        return UpgradeOutcome(ok=True)

    def rollback(self, unit_name):
        self.calls.append("rollback")
        return UpgradeOutcome(ok=True)


class _FakeProvider:
    def __init__(self, fail_start: bool = False) -> None:
        self.starts = 0
        self.stops = 0
        self.listening = False
        self.bound_port = None
        self.fail_start = fail_start

    def start(self) -> None:
        if self.fail_start:
            raise LabProviderError("bind conflict")
        self.starts += 1
        self.listening = True

    def stop(self) -> None:
        self.stops += 1
        self.listening = False


class _FakeReadiness:
    """Readiness whose gates the test flips deterministically."""

    def __init__(self, ready: bool = True) -> None:
        self.ready = ready
        self.evaluations = 0

    def evaluate(self, now) -> ReadinessReport:
        self.evaluations += 1
        gates = {
            name: GateResult(True, f"{name}_ok", f"{name} ok")
            for name in ConnectorReadiness.GATE_NAMES
        }
        if not self.ready:
            gates["daemon_loopback"] = GateResult(
                False, "daemon_unavailable", "no daemon"
            )
        return ReadinessReport(ready=self.ready, gates=gates)


def _config(tmp_path, *, lab: bool = True, **overrides) -> ConnectorConfig:
    fields = dict(
        tenant_id="tenant-a",
        home_id="home-a",
        connector_id="connector-a",
        daemon_port=8999,
        daemon_token_path=str(tmp_path / "daemon.token"),
        policy_path=str(tmp_path / "policy.json"),
        state_path=str(tmp_path / "state.json"),
        unit_name="happyranch-connector.service",
        system=False,
        lab=LabProviderConfig(bind_host="127.0.0.1", lab_only=True)
        if lab
        else None,
    )
    fields.update(overrides)
    return ConnectorConfig(**fields)


def _supervisor(
    tmp_path,
    *,
    manager=None,
    readiness=None,
    provider=_UNSET,
    lab: bool = True,
    config=None,
    policy=None,
) -> ConnectorSupervisor:
    config = config or _config(tmp_path, lab=lab)
    if provider is _UNSET:
        provider = _FakeProvider()
    return ConnectorSupervisor(
        config=config,
        manager=manager or FakeManager(),
        readiness=readiness or _FakeReadiness(ready=True),
        provider=provider,
        policy=policy,
        now_fn=lambda: NOW(),
        notify_fn=lambda state: notifications.append(state),
    )


notifications: list[str] = []


def _notified(substring: str) -> bool:
    return any(substring in n for n in notifications)


class TestConfig:
    def test_validate_requires_identity_and_sources(self, tmp_path) -> None:
        with pytest.raises(ConnectorConfigError):
            ConnectorConfig().validate()
        with pytest.raises(ConnectorConfigError):
            _config(tmp_path, daemon_port=None).validate()
        with pytest.raises(ConnectorConfigError):
            _config(tmp_path, daemon_token_path=None, credentials_directory=None).validate()
        with pytest.raises(ConnectorConfigError):
            _config(tmp_path, policy_path=None).validate()
        with pytest.raises(ConnectorConfigError):
            cfg = _config(tmp_path)
            cfg.lab = LabProviderConfig(bind_host="127.0.0.1", lab_only=False)
            cfg.validate()

    def test_from_file_roundtrip(self, tmp_path) -> None:
        config = _config(tmp_path)
        path = tmp_path / "config.json"
        config.to_file(path)
        loaded = ConnectorConfig.from_file(path)
        assert loaded.tenant_id == "tenant-a"
        assert loaded.lab is not None
        assert loaded.lab.lab_only is True
        assert loaded.daemon_port == 8999


class TestRunLoop:
    def test_ready_starts_provider_and_notifies_readiness(
        self, tmp_path
    ) -> None:
        notifications.clear()
        provider = _FakeProvider()
        supervisor = _supervisor(tmp_path, provider=provider)
        supervisor.run(max_iterations=1, poll_seconds=0)
        assert provider.starts == 1
        assert supervisor._provider_running is True
        assert _notified("READY=1")

    def test_not_ready_never_starts_provider(self, tmp_path) -> None:
        notifications.clear()
        provider = _FakeProvider()
        supervisor = _supervisor(
            tmp_path, provider=provider, readiness=_FakeReadiness(ready=False)
        )
        supervisor.run(max_iterations=1, poll_seconds=0)
        assert provider.starts == 0
        assert _notified("STATUS=waiting for readiness")

    def test_readiness_loss_stops_listener_immediately(self, tmp_path) -> None:
        notifications.clear()
        provider = _FakeProvider()
        readiness = _FakeReadiness(ready=True)
        supervisor = _supervisor(tmp_path, provider=provider, readiness=readiness)
        supervisor.run(max_iterations=1, poll_seconds=0)
        assert provider.starts == 1
        readiness.ready = False  # daemon died / token perms loosened
        supervisor.run(max_iterations=1, poll_seconds=0)
        assert provider.stops == 1
        assert supervisor._provider_running is False
        assert _notified("STOPPING=1")

    def test_provider_refused_start_keeps_no_listener(self, tmp_path) -> None:
        notifications.clear()
        supervisor = _supervisor(tmp_path, provider=_FakeProvider(fail_start=True))
        supervisor.run(max_iterations=1, poll_seconds=0)
        assert supervisor._provider_running is False
        assert any("provider failed to start" in n for n in notifications)

    def test_shutdown_stops_provider(self, tmp_path) -> None:
        notifications.clear()
        provider = _FakeProvider()
        supervisor = _supervisor(tmp_path, provider=provider)
        supervisor.run(max_iterations=1, poll_seconds=0)
        supervisor.shutdown()
        assert provider.stops == 1
        assert _notified("STOPPING=1")

    def test_no_provider_configured_loop_is_safe(self, tmp_path) -> None:
        notifications.clear()
        supervisor = _supervisor(tmp_path, provider=None, lab=False)
        code = supervisor.run(max_iterations=2, poll_seconds=0)
        assert code == 0
        assert _notified("READY=1")  # readiness passes; no listener needed

    def test_restart_after_readiness_loss_rebuilds_adapter(
        self, tmp_path, route_policy_fixture
    ) -> None:
        """The REAL lab adapter is rebuilt per start: a stopped
        ThreadingHTTPServer cannot be restarted, so a readiness-loss cycle
        followed by re-ready must construct and bind a fresh adapter."""
        notifications.clear()
        token = tmp_path / "daemon.token"
        token.write_text("token-x")
        token.chmod(0o600)
        readiness = _FakeReadiness(ready=True)
        supervisor = _supervisor(
            tmp_path,
            provider=None,  # build the real adapter
            readiness=readiness,
            policy=build_consumer(route_policy_fixture),
        )
        supervisor.run(max_iterations=1, poll_seconds=0)
        first = supervisor._provider
        assert first is not None and first.listening is True
        port_before = first.bound_port
        assert port_before is not None

        readiness.ready = False
        supervisor.run(max_iterations=1, poll_seconds=0)
        assert supervisor._provider_running is False
        assert first.listening is False

        readiness.ready = True
        supervisor.run(max_iterations=1, poll_seconds=0)
        second = supervisor._provider
        assert second is not None
        assert second is not first  # rebuilt, not reused
        assert second.listening is True
        assert second.bound_port is not None
        supervisor.shutdown()


class TestServiceManagerDelegation:
    def test_install_renders_unit(self, tmp_path) -> None:
        manager = FakeManager()
        supervisor = _supervisor(tmp_path, manager=manager)
        supervisor.install()
        assert manager.calls == ["install"]
        assert manager.installed[0][0] == "happyranch-connector.service"
        unit_text = manager.installed[0][1]
        assert "NoNewPrivileges=yes" in unit_text
        assert "LoadCredential" in unit_text or "daemon_token_path" in unit_text

    def test_start_stop_restart_enable_disable_uninstall(self, tmp_path) -> None:
        manager = FakeManager()
        supervisor = _supervisor(tmp_path, manager=manager)
        supervisor.start()
        supervisor.stop()
        supervisor.restart()
        supervisor.enable()
        supervisor.disable()
        supervisor.uninstall()
        assert manager.calls == ["start", "stop", "restart", "enable", "disable", "uninstall"]

    def test_upgrade_and_rollback_delegate(self, tmp_path) -> None:
        manager = FakeManager()
        supervisor = _supervisor(tmp_path, manager=manager)
        outcome = supervisor.upgrade()
        assert outcome.ok is True
        assert manager.calls == ["upgrade"]
        outcome = supervisor.rollback()
        assert outcome.ok is True
        assert manager.calls[-1] == "rollback"

    def test_status_delegates(self, tmp_path) -> None:
        manager = FakeManager()
        supervisor = _supervisor(tmp_path, manager=manager)
        status = supervisor.status()
        assert status.active_state == "active"
        assert status.running is True


class TestDiagnostics:
    def test_diagnose_never_contains_bearer(self, tmp_path) -> None:
        (tmp_path / "daemon.token").write_text("top-secret-bearer-value")
        (tmp_path / "daemon.token").chmod(0o600)
        supervisor = _supervisor(tmp_path)
        report = supervisor.diagnose()
        blob = json.dumps(report)
        assert "top-secret-bearer-value" not in blob
        assert "Bearer" not in blob
        assert report["role"] == "happyranch-connector"
        assert report["secrets"] == "redacted"
        assert report["readiness"]["ready"] is True

    def test_diagnose_reports_gates_and_provider(self, tmp_path) -> None:
        supervisor = _supervisor(tmp_path, readiness=_FakeReadiness(ready=False))
        report = supervisor.diagnose()
        assert report["readiness"]["ready"] is False
        assert report["readiness"]["gates"]["daemon_loopback"]["ok"] is False
        assert report["provider"]["type"] == "lab"
        assert LAB_ONLY_BANNER in report["provider"]["banner"]


class TestLabProvisioning:
    def test_first_run_state_pairs_lab_device(self, tmp_path) -> None:
        supervisor = _supervisor(tmp_path)
        state = supervisor.initial_state()
        assert state.connector_identity == default_identity()
        assert state.current_device_id == "lab-client-1"
        assert state.devices["lab-client-1"].revoked is False

    def test_revocation_across_restart_at_supervisor_level(
        self, tmp_path, route_policy_fixture
    ) -> None:
        """The full revocation-across-restart path: revoke via the coordinator,
        persist, then a NEW supervisor (fresh process) loads the state and the
        lab context factory denies the device."""
        config = _config(tmp_path)
        supervisor = _supervisor(tmp_path, config=config, policy=build_consumer(route_policy_fixture))
        # first run: persist first-run state with the lab device paired
        store = AtomicFileTrustStateStore(
            Path(config.state_path), supervisor.initial_state()
        )
        store.save(supervisor.initial_state())
        state = store.load()
        RevocationCoordinator(state, StreamRegistry()).revoke(epoch=2)
        store.save(state)

        # restart: a new supervisor instance over the same state file
        restarted = _supervisor(tmp_path, config=config, policy=build_consumer(route_policy_fixture))
        loaded = restarted.state_store.load()
        assert loaded.revocation_epoch == 2
        ctx = restarted.build_ctx_factory()(NOW())
        verdict = ctx.authorization.check("tenant-a", "home-a", "lab-client-1", NOW())
        assert verdict.ok is False
        assert verdict.reason == "revocation"

    def test_ctx_factory_forwarder_targets_literal_loopback(
        self, tmp_path, route_policy_fixture
    ) -> None:
        config = _config(tmp_path, daemon_port=9876)
        supervisor = _supervisor(tmp_path, config=config, policy=build_consumer(route_policy_fixture))
        ctx = supervisor.build_ctx_factory()(NOW())
        assert ctx.forwarder.target.host == "127.0.0.1"
        assert ctx.forwarder.target.port == 9876


class TestSdNotify:
    def test_sd_notify_without_socket_returns_false(self) -> None:
        assert sd_notify("READY=1\n", notify_socket=None) is False

    def test_sd_notify_sends_datagram(self, tmp_path) -> None:
        import socket

        sock_path = tmp_path / "notify.sock"
        server = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        server.bind(str(sock_path))
        server.settimeout(2)
        try:
            assert sd_notify("READY=1\n", notify_socket=str(sock_path)) is True
            data, _ = server.recvfrom(64)
            assert data == b"READY=1\n"
        finally:
            server.close()

    def test_sd_notify_missing_socket_fails_safe(self, tmp_path) -> None:
        assert sd_notify("WATCHDOG=1\n", notify_socket=str(tmp_path / "nope.sock")) is False
