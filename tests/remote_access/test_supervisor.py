"""Connector supervisor tests (THR-097 phase unit 3).

Deterministic lifecycle coverage with fakes: readiness-gated listener start,
fail-closed listener stop on readiness loss, READY/WATCHDOG/STOPPING notify,
service-manager delegation (install/start/stop/upgrade/rollback), lab device
provisioning in first-run state, revocation across a restart, and redacted
local diagnostics.
"""
from __future__ import annotations

import json
import os
import socket
from datetime import timedelta
from pathlib import Path

import pytest

from runtime.remote_access.authorization import DeviceAuthorization, TrustState
from runtime.remote_access.credentials import (
    CredentialUnavailable,
    SystemdCredentialProvider,
)
from runtime.remote_access.lab_provider import LAB_ONLY_BANNER, LabProviderConfig, LabProviderError
from runtime.remote_access.readiness import (
    ConnectorReadiness,
    GateResult,
    ReadinessReport,
)
from runtime.remote_access.revocation import RevocationCoordinator
from runtime.remote_access.service_manager import ServiceStatus, UpgradeOutcome
from runtime.remote_access.state_store import (
    AtomicFileTrustStateStore,
    StateStoreError,
)
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
        assert not _notified("READY=1")  # never READY without a proven listener

    def test_provider_start_failure_never_emits_ready(self, tmp_path) -> None:
        """The reviewer's [HIGH] READY-ordering finding: READY=1 must NEVER be
        emitted unless the provider actually started (listener proven). On a
        bind/start failure the loop retries, reports STATUS only, and the
        supervisor stays deterministically not-running with no listener."""
        notifications.clear()
        provider = _FakeProvider(fail_start=True)
        supervisor = _supervisor(tmp_path, provider=provider)
        supervisor.run(max_iterations=2, poll_seconds=0)
        assert (
            sum("provider failed to start" in n for n in notifications) == 2
        )  # the loop retried each poll
        assert supervisor._provider_running is False
        assert not _notified("READY=1")
        assert not _notified("WATCHDOG=1")
        assert any("provider failed to start" in n for n in notifications)
        # fail-closed status/shutdown: still not running, still no READY
        supervisor.shutdown()
        assert supervisor._provider_running is False
        assert provider.stops == 0
        assert not _notified("READY=1")

    def test_real_occupied_port_keeps_supervised_retry_never_ready(self, tmp_path) -> None:
        """QA TASK-6014 structural diagnosis through the SHIPPING path: the REAL
        ``LabProviderAdapter`` bind failure (occupied port) raised a bare
        OSError that escaped ``_start_provider``'s ``LabProviderError`` catch,
        crashing ``run()`` instead of retrying. With the adapter boundary
        normalization: zero READY, secret-free STATUS, no listener, and the
        loop keeps re-evaluating (no process exit, no systemd restart
        needed)."""
        notifications.clear()
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as blocker:
            blocker.bind(("127.0.0.1", 0))
            blocker.listen(1)
            port = int(blocker.getsockname()[1])
            # Constructed directly (NOT via _config): the _config helper's
            # named ``lab`` param would shadow the occupied port with its
            # default ephemeral config.
            config = ConnectorConfig(
                tenant_id="tenant-a",
                home_id="home-a",
                connector_id="connector-a",
                daemon_port=8999,
                daemon_token_path=str(tmp_path / "daemon.token"),
                policy_path=str(tmp_path / "policy.json"),
                state_path=str(tmp_path / "state.json"),
                unit_name="happyranch-connector.service",
                system=False,
                lab=LabProviderConfig(
                    bind_host="127.0.0.1", bind_port=port, lab_only=True
                ),
            )
            # provider=None: NO injected fake — build_provider() constructs
            # the REAL LabProviderAdapter from the config.
            supervisor = ConnectorSupervisor(
                config=config,
                manager=FakeManager(),
                readiness=_FakeReadiness(ready=True),
                provider=None,
                now_fn=lambda: NOW(),
                notify_fn=lambda state: notifications.append(state),
            )
            supervisor.run(max_iterations=2, poll_seconds=0)  # must NOT raise
            assert supervisor._provider_running is False
            assert supervisor._provider is None  # no listener of ours
            assert (
                sum("provider failed to start" in n for n in notifications) == 2
            )  # supervised retry each poll
            assert not _notified("READY=1")
            assert not _notified("WATCHDOG=1")
            # secret-free STATUS: never leaks tokens/paths/exception text
            for n in notifications:
                assert "token" not in n.lower()
                assert "daemon" not in n.lower()
                assert "Errno" not in n
            supervisor.shutdown()
            assert supervisor._provider_running is False

    def test_real_occupied_port_recovers_when_freed(self, tmp_path) -> None:
        """QA TASK-6014 supervised retry/recovery: while the port is occupied
        the loop reports STATUS with zero READY; once the conflict clears the
        NEXT iteration binds a real listener and emits READY — the process
        never exits, so systemd never restart-throttles."""
        notifications.clear()
        blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        blocker.bind(("127.0.0.1", 0))
        blocker.listen(1)
        port = int(blocker.getsockname()[1])
        # Constructed directly (NOT via _config): see the sibling test above.
        config = ConnectorConfig(
            tenant_id="tenant-a",
            home_id="home-a",
            connector_id="connector-a",
            daemon_port=8999,
            daemon_token_path=str(tmp_path / "daemon.token"),
            policy_path=str(tmp_path / "policy.json"),
            state_path=str(tmp_path / "state.json"),
            unit_name="happyranch-connector.service",
            system=False,
            lab=LabProviderConfig(
                bind_host="127.0.0.1", bind_port=port, lab_only=True
            ),
        )
        supervisor = ConnectorSupervisor(
            config=config,
            manager=FakeManager(),
            readiness=_FakeReadiness(ready=True),
            provider=None,
            now_fn=lambda: NOW(),
            notify_fn=lambda state: notifications.append(state),
        )
        try:
            supervisor.run(max_iterations=1, poll_seconds=0)
            assert not _notified("READY=1")  # occupied: no listener, no READY
            blocker.close()  # release the conflict
            supervisor.run(max_iterations=1, poll_seconds=0)
            assert _notified("READY=1")  # freed: recovered and listening
            assert supervisor._provider_running is True
            adapter = supervisor._provider
            assert adapter is not None
            assert adapter.listening is True
            assert adapter.bound_port == port
        finally:
            blocker.close()
            supervisor.shutdown()

    def test_unexpected_provider_defect_propagates(self, tmp_path) -> None:
        """The LabProviderError category contract is OPERATIONAL-only: an
        unexpected defect inside provider start (a programming error, not a
        bind/listen OSError) must propagate loudly — never be normalized or
        swallowed into the retry loop."""
        notifications.clear()

        class _DefectiveProvider:
            def start(self) -> None:
                raise ValueError("programming defect")

            def stop(self) -> None:
                pass

        supervisor = _supervisor(tmp_path, provider=_DefectiveProvider())
        with pytest.raises(ValueError, match="programming defect"):
            supervisor.run(max_iterations=1, poll_seconds=0)
        assert not _notified("READY=1")

    def test_shutdown_stops_provider(self, tmp_path) -> None:
        notifications.clear()
        provider = _FakeProvider()
        supervisor = _supervisor(tmp_path, provider=provider)
        supervisor.run(max_iterations=1, poll_seconds=0)
        supervisor.shutdown()
        assert provider.stops == 1
        assert _notified("STOPPING=1")

    def test_run_without_provider_fails_closed(self, tmp_path) -> None:
        """The reviewer's [HIGH] provider-less READY finding: a RUNNABLE
        configuration without a concrete lab provider/listener must fail
        closed at startup and must NEVER emit READY=1 (no listener could back
        it). Non-run construction (status/readiness/diagnose/install) stays
        valid — only run() rejects."""
        notifications.clear()
        supervisor = _supervisor(tmp_path, provider=None, lab=False)
        with pytest.raises(ConnectorConfigError):
            supervisor.run(max_iterations=2, poll_seconds=0)
        assert notifications == []  # never entered the notify loop; no READY

    def test_start_provider_without_provider_returns_false(self, tmp_path) -> None:
        """Belt-and-braces: even if run()'s startup gate were bypassed,
        _start_provider must never report proven success without a listener."""
        notifications.clear()
        supervisor = _supervisor(tmp_path, provider=None, lab=False)
        assert supervisor._start_provider() is False
        assert supervisor._provider_running is False
        assert not _notified("READY=1")

    def test_run_with_injected_provider_needs_no_lab_config(self, tmp_path) -> None:
        """A concrete injected provider IS a listener source: run() accepts it
        even when the config carries no lab provider (test-only seam)."""
        notifications.clear()
        supervisor = _supervisor(tmp_path, provider=_FakeProvider(), lab=False)
        supervisor.run(max_iterations=1, poll_seconds=0)
        assert supervisor._provider_running is True
        assert _notified("READY=1")

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
        token = tmp_path / "daemon.token"
        token.write_text("token-x")
        token.chmod(0o600)
        (tmp_path / "policy.json").write_text("{}")
        manager = FakeManager()
        cfg = _config(tmp_path, managed_dir_root=str(tmp_path / "managed"))
        supervisor = _supervisor(tmp_path, manager=manager, config=cfg)
        supervisor.install()
        assert manager.calls == ["install"]
        assert manager.installed[0][0] == "happyranch-connector.service"
        unit_text = manager.installed[0][1]
        assert "NoNewPrivileges=yes" in unit_text
        assert "LoadCredential" in unit_text

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


class TestCredentialProvider:
    """Finding 3: the service path must automatically consume
    CREDENTIALS_DIRECTORY/LoadCredential — without redundant config and
    without ever falling back to reading the daemon home."""

    def test_auto_consumes_crdentials_directory_env(self, tmp_path, monkeypatch) -> None:
        creds = tmp_path / "creds"
        creds.mkdir()
        (creds / "daemon.token").write_text("svc-token")
        (creds / "daemon.token").chmod(0o600)
        monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(creds))
        supervisor = _supervisor(tmp_path)  # config also carries daemon_token_path
        provider = supervisor.credential_provider()
        assert isinstance(provider, SystemdCredentialProvider)
        assert provider.read_bearer() == "svc-token"

    def test_never_falls_back_to_daemon_home_under_systemd(
        self, tmp_path, monkeypatch
    ) -> None:
        """Under LoadCredential= the injected credential is the ONLY source:
        a missing credential fails closed — it must never fall through to the
        daemon-token file path (the service user may not read the daemon home)."""
        creds = tmp_path / "creds"
        creds.mkdir()  # empty: nothing injected
        monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(creds))
        supervisor = _supervisor(tmp_path)  # daemon_token_path set, file absent
        provider = supervisor.credential_provider()
        assert isinstance(provider, SystemdCredentialProvider)
        with pytest.raises(CredentialUnavailable):
            provider.read_bearer()

    def test_no_env_uses_configured_sources(self, tmp_path, monkeypatch) -> None:
        monkeypatch.delenv("CREDENTIALS_DIRECTORY", raising=False)
        token = tmp_path / "daemon.token"
        token.write_text("file-token")
        token.chmod(0o600)
        supervisor = _supervisor(tmp_path)
        provider = supervisor.credential_provider()
        assert provider.read_bearer() == "file-token"


class TestInstallStaging:
    """Finding 3: install() stages config/state into the declared managed
    directories (accessible to the dedicated service user), never pointing the
    unit at ~/.happyranch paths the hardened service cannot read."""

    def _policy(self, tmp_path) -> Path:
        p = tmp_path / "policy.json"
        p.write_text("{}")
        return p

    def test_install_stages_managed_config_policy_and_state(self, tmp_path) -> None:
        token = tmp_path / "daemon.token"
        token.write_text("token-x")
        token.chmod(0o600)
        policy = self._policy(tmp_path)
        state = TrustState(connector_identity=default_identity(), pairing_epoch=0, revocation_epoch=0)
        store = AtomicFileTrustStateStore(tmp_path / "state.json", state)
        store.save(state)
        manager = FakeManager()
        cfg = _config(tmp_path, managed_dir_root=str(tmp_path / "managed"))
        supervisor = _supervisor(tmp_path, manager=manager, config=cfg)
        path = supervisor.install()
        managed_root = tmp_path / "managed" / "happyranch-connector"
        assert (managed_root / "config.json").is_file()
        assert (managed_root / "policy.json").is_file()
        assert (managed_root / "trust-state.json").is_file()
        assert (managed_root / "trust-state.json.anchor").is_file()
        managed_cfg = ConnectorConfig.from_file(managed_root / "config.json")
        assert managed_cfg.state_path == str(managed_root / "trust-state.json")
        assert managed_cfg.policy_path == str(managed_root / "policy.json")
        assert managed_cfg.tenant_id == "tenant-a"
        unit_text = manager.installed[0][1]
        assert str(managed_root / "config.json") in unit_text
        assert "--lab-only" in unit_text  # lab provider → unit passes --lab-only

    def test_install_staged_unit_never_points_at_daemon_home(self, tmp_path) -> None:
        """The rendered unit's --config must be the managed path — never a
        ~/.happyranch/... path the hardened service user cannot read."""
        token = tmp_path / "daemon.token"
        token.write_text("token-x")
        token.chmod(0o600)
        self._policy(tmp_path)
        manager = FakeManager()
        cfg = _config(tmp_path, managed_dir_root=str(tmp_path / "managed"))
        supervisor = _supervisor(tmp_path, manager=manager, config=cfg)
        supervisor.install()
        unit_text = manager.installed[0][1]
        assert ".happyranch" not in unit_text
        assert "--config" in unit_text

    def test_install_refuses_missing_policy(self, tmp_path) -> None:
        manager = FakeManager()
        cfg = _config(tmp_path, managed_dir_root=str(tmp_path / "managed"))
        supervisor = _supervisor(tmp_path, manager=manager, config=cfg)
        with pytest.raises(ConnectorConfigError):
            supervisor.install()
        assert manager.calls == []  # nothing staged, nothing installed

    def test_install_refuses_partial_source_state(self, tmp_path) -> None:
        """A source snapshot without its companion anchor is partial/corrupt
        state — install() must refuse to stage it (revocation history could be
        silently dropped)."""
        token = tmp_path / "daemon.token"
        token.write_text("token-x")
        token.chmod(0o600)
        self._policy(tmp_path)
        state = TrustState(connector_identity=default_identity(), pairing_epoch=0, revocation_epoch=0)
        store = AtomicFileTrustStateStore(tmp_path / "state.json", state)
        store.save(state)
        (tmp_path / "state.json.anchor").unlink()
        manager = FakeManager()
        cfg = _config(tmp_path, managed_dir_root=str(tmp_path / "managed"))
        supervisor = _supervisor(tmp_path, manager=manager, config=cfg)
        with pytest.raises(ConnectorConfigError):
            supervisor.install()
        assert manager.calls == []

    def test_install_staged_files_are_owner_only(self, tmp_path) -> None:
        token = tmp_path / "daemon.token"
        token.write_text("token-x")
        token.chmod(0o600)
        self._policy(tmp_path)
        manager = FakeManager()
        cfg = _config(tmp_path, managed_dir_root=str(tmp_path / "managed"))
        supervisor = _supervisor(tmp_path, manager=manager, config=cfg)
        supervisor.install()
        managed_root = tmp_path / "managed" / "happyranch-connector"
        import stat

        for name in ("config.json", "policy.json"):
            mode = stat.S_IMODE((managed_root / name).stat().st_mode)
            assert mode & 0o077 == 0


class TestInstallReinstallAuthority:
    """TASK-6004 [HIGH]: once a managed snapshot+anchor pair exists it is
    AUTHORITATIVE. install/reinstall must never overwrite or roll it back
    with a stale operator source pair — the source pair is only an initial
    seed when NO managed state exists."""

    def _policy(self, tmp_path) -> Path:
        p = tmp_path / "policy.json"
        p.write_text("{}")
        return p

    def _source_supervisor(self, tmp_path, *, manager=None, **overrides):
        token = tmp_path / "daemon.token"
        token.write_text("token-x")
        token.chmod(0o600)
        self._policy(tmp_path)
        cfg = _config(
            tmp_path, managed_dir_root=str(tmp_path / "managed"), **overrides
        )
        return _supervisor(tmp_path, manager=manager or FakeManager(), config=cfg)

    def _managed_root(self, tmp_path) -> Path:
        return tmp_path / "managed" / "happyranch-connector"

    def _revoked_state(self) -> TrustState:
        """A state whose lab device is revoked at epoch 1 (the managed state
        advances past the initial source seed)."""
        state = TrustState(
            connector_identity=default_identity(), pairing_epoch=0, revocation_epoch=1
        )
        state.apply_pairing(
            DeviceAuthorization(
                device_id="lab-client-1",
                tenant_id="tenant-a",
                home_id="home-a",
                authorization_epoch=1,
                expires_at=NOW() + timedelta(days=3650),
                revoked=True,
            )
        )
        return state

    def test_initial_install_seeds_managed_from_source(self, tmp_path) -> None:
        """No managed pair yet: the source pair is the initial seed (the
        pre-existing staging contract still holds)."""
        state = TrustState(
            connector_identity=default_identity(), pairing_epoch=0, revocation_epoch=0
        )
        store = AtomicFileTrustStateStore(tmp_path / "state.json", state)
        store.save(state)
        manager = FakeManager()
        supervisor = self._source_supervisor(tmp_path, manager=manager)
        supervisor.install()
        managed_root = self._managed_root(tmp_path)
        assert (managed_root / "trust-state.json").is_file()
        assert (managed_root / "trust-state.json.anchor").is_file()
        # A NEW store instance over the managed pair loads the seeded state.
        reloaded = AtomicFileTrustStateStore(
            managed_root / "trust-state.json", state
        ).load()
        assert reloaded.revocation_epoch == 0

    def test_reinstall_after_managed_revocation_refuses_and_preserves(
        self, tmp_path
    ) -> None:
        """The reviewer's exact repro: install epoch 0 (gen 1), advance the
        MANAGED pair to revoked epoch 1 (gen 2), then reinstall with the stale
        source pair — install must REFUSE (never roll back a revocation), and
        a NEW store instance must still load the revoked managed state."""
        state = TrustState(
            connector_identity=default_identity(), pairing_epoch=0, revocation_epoch=0
        )
        store = AtomicFileTrustStateStore(tmp_path / "state.json", state)
        store.save(state)
        manager = FakeManager()
        supervisor = self._source_supervisor(tmp_path, manager=manager)
        supervisor.install()
        managed_root = self._managed_root(tmp_path)
        # Advance the MANAGED pair (the running service revokes) → generation 2.
        managed_store = AtomicFileTrustStateStore(
            managed_root / "trust-state.json", state
        )
        managed_store.save(self._revoked_state())
        # Reinstall with the unchanged STALE source pair → must refuse.
        with pytest.raises(ConnectorConfigError):
            supervisor.install()
        # The managed pair is untouched: a NEW instance (restart) still denies.
        reloaded = AtomicFileTrustStateStore(
            managed_root / "trust-state.json", state
        ).load()
        assert reloaded.revocation_epoch == 1
        assert reloaded.devices["lab-client-1"].revoked is True

    def test_reinstall_with_newer_source_pair_adopts_monotonically(
        self, tmp_path
    ) -> None:
        """A strictly NEWER source pair (higher generation) is a monotonic
        advance: install adopts it, and the adopted pair is authoritative."""
        state = TrustState(
            connector_identity=default_identity(), pairing_epoch=0, revocation_epoch=0
        )
        store = AtomicFileTrustStateStore(tmp_path / "state.json", state)
        store.save(state)
        manager = FakeManager()
        supervisor = self._source_supervisor(tmp_path, manager=manager)
        supervisor.install()
        # The source advances (operator re-provisions a newer pair) → gen 2.
        store.save(self._revoked_state())
        supervisor.install()
        managed_root = self._managed_root(tmp_path)
        reloaded = AtomicFileTrustStateStore(
            managed_root / "trust-state.json", state
        ).load()
        assert reloaded.revocation_epoch == 1
        assert reloaded.devices["lab-client-1"].revoked is True

    def test_reinstall_with_equal_identical_pair_is_noop(self, tmp_path) -> None:
        """An equal (identical) pair is already in sync: install keeps the
        managed pair untouched and still succeeds."""
        state = TrustState(
            connector_identity=default_identity(), pairing_epoch=0, revocation_epoch=0
        )
        store = AtomicFileTrustStateStore(tmp_path / "state.json", state)
        store.save(state)
        manager = FakeManager()
        supervisor = self._source_supervisor(tmp_path, manager=manager)
        supervisor.install()
        managed_root = self._managed_root(tmp_path)
        managed_store = AtomicFileTrustStateStore(managed_root / "trust-state.json", state)
        managed_store.save(self._revoked_state())  # managed → gen 2
        # Operator re-saves the SAME pair at the source → source gen 2 == managed gen 2.
        store.save(self._revoked_state())
        supervisor.install()  # must not raise
        reloaded = AtomicFileTrustStateStore(
            managed_root / "trust-state.json", state
        ).load()
        assert reloaded.revocation_epoch == 1  # managed preserved (still revoked)

    def test_reinstall_with_conflicting_same_generation_refuses(self, tmp_path) -> None:
        """A different pair at the SAME generation is a split/conflict —
        install refuses and the managed pair stays authoritative."""
        state = TrustState(
            connector_identity=default_identity(), pairing_epoch=0, revocation_epoch=0
        )
        store = AtomicFileTrustStateStore(tmp_path / "state.json", state)
        store.save(state)
        manager = FakeManager()
        supervisor = self._source_supervisor(tmp_path, manager=manager)
        supervisor.install()
        managed_root = self._managed_root(tmp_path)
        managed_store = AtomicFileTrustStateStore(managed_root / "trust-state.json", state)
        managed_store.save(self._revoked_state())  # managed → gen 2 (revoked)
        # Source advances to gen 2 with a DIFFERENT (non-revoked) state.
        other = TrustState(
            connector_identity=default_identity(), pairing_epoch=0, revocation_epoch=0
        )
        other.apply_pairing(
            DeviceAuthorization(
                device_id="lab-client-1",
                tenant_id="tenant-a",
                home_id="home-a",
                authorization_epoch=1,
                expires_at=NOW() + timedelta(days=3650),
            )
        )
        store.save(other)
        assert store.anchored_generation() == 2
        with pytest.raises(ConnectorConfigError):
            supervisor.install()
        reloaded = AtomicFileTrustStateStore(
            managed_root / "trust-state.json", state
        ).load()
        assert reloaded.revocation_epoch == 1  # managed untouched

    def test_reinstall_interrupted_staging_leaves_no_usable_mixed_pair(
        self, tmp_path, monkeypatch
    ) -> None:
        """An interrupted/partial staging (second member write fails) must
        leave NO usable mixed pair: the managed pair either stays intact or
        fails closed under load() — never a loadable mixed snapshot, and
        never a rollback to a lower generation."""
        state = TrustState(
            connector_identity=default_identity(), pairing_epoch=0, revocation_epoch=0
        )
        store = AtomicFileTrustStateStore(tmp_path / "state.json", state)
        store.save(state)
        manager = FakeManager()
        supervisor = self._source_supervisor(tmp_path, manager=manager)
        supervisor.install()
        managed_root = self._managed_root(tmp_path)
        managed_store = AtomicFileTrustStateStore(managed_root / "trust-state.json", state)
        managed_store.save(state)  # managed → gen 2 (still unrevoked)
        # Source advances to gen 3 (strictly newer → adoption path); the
        # anchor publish fails mid-staging.
        store.save(state)
        store.save(self._revoked_state())
        real_replace = os.replace

        def _fail_second_replace(src, dst, *a, **kw):
            if "trust-state.json.anchor" in str(dst):
                raise OSError("injected anchor publish failure")
            return real_replace(src, dst, *a, **kw)

        monkeypatch.setattr(os, "replace", _fail_second_replace)
        with pytest.raises(ConnectorConfigError):
            supervisor.install()
        monkeypatch.undo()
        # The managed pair must NOT be a usable mixed pair: loading it fails
        # closed (new snapshot + old anchor = mismatched), never a loadable
        # un-revoked state.
        fresh = AtomicFileTrustStateStore(managed_root / "trust-state.json", state)
        with pytest.raises(StateStoreError):
            fresh.load()

    def test_reinstall_source_partial_pair_refuses(self, tmp_path) -> None:
        """A source snapshot without its companion anchor is partial state
        and must refuse reinstall exactly as it refuses initial install."""
        state = TrustState(
            connector_identity=default_identity(), pairing_epoch=0, revocation_epoch=0
        )
        store = AtomicFileTrustStateStore(tmp_path / "state.json", state)
        store.save(state)
        manager = FakeManager()
        supervisor = self._source_supervisor(tmp_path, manager=manager)
        supervisor.install()
        (tmp_path / "state.json.anchor").unlink()  # source becomes partial
        with pytest.raises(ConnectorConfigError):
            supervisor.install()
        assert manager.calls == ["install"]  # no second unit install


class TestManagedPaths:
    def test_system_mode_defaults_to_var_lib(self, tmp_path) -> None:
        cfg = _config(tmp_path, system=True)
        supervisor = _supervisor(tmp_path, config=cfg)
        assert str(supervisor._managed_state_root()) == "/var/lib/happyranch-connector"

    def test_user_mode_defaults_to_xdg_state_home(self, tmp_path) -> None:
        cfg = _config(tmp_path, system=False)
        supervisor = _supervisor(tmp_path, config=cfg)
        root = supervisor._managed_state_root()
        assert str(root).startswith(str(Path.home() / ".local" / "state"))

    def test_managed_dir_root_override_wins(self, tmp_path) -> None:
        cfg = _config(tmp_path, system=True, managed_dir_root=str(tmp_path / "m"))
        supervisor = _supervisor(tmp_path, config=cfg)
        assert str(supervisor._managed_state_root()) == str(tmp_path / "m" / "happyranch-connector")

    def test_unit_spec_default_exec_start_uses_managed_config(self, tmp_path) -> None:
        cfg = _config(tmp_path, managed_dir_root=str(tmp_path / "m"))
        supervisor = _supervisor(tmp_path, config=cfg)
        spec = supervisor.unit_spec()
        assert "--config" in spec.exec_start
        assert str(tmp_path / "m" / "happyranch-connector" / "config.json") in spec.exec_start
        assert "--lab-only" in spec.exec_start  # lab configured

    def test_unit_spec_no_lab_omits_lab_only(self, tmp_path) -> None:
        cfg = _config(tmp_path, lab=False, managed_dir_root=str(tmp_path / "m"))
        supervisor = _supervisor(tmp_path, config=cfg)
        spec = supervisor.unit_spec()
        assert "--lab-only" not in spec.exec_start
