"""Readiness gates (THR-097 phase unit 3).

Fixed invariant: **readiness must expose no listener unless daemon loopback
reachability, credential permissions, current policy, bind identity, and
non-corrupt trust state all pass.** Each gate fails closed with a stable,
secret-free category; a single failed gate means not-ready, and the
supervisor binds no listener.

The daemon-reachability gate connects to LITERAL ``127.0.0.1`` (never a
hostname, never 0.0.0.0, never any other interface) — the daemon stays
loopback-only and the connector only ever talks to it on the loopback hop.
"""
from __future__ import annotations

import socket
from datetime import datetime, timedelta, timezone
from typing import Callable

import pytest

from runtime.remote_access.authorization import DeviceAuthorization, TrustState
from runtime.remote_access.credentials import CredentialUnavailable
from runtime.remote_access.identity import ConnectorIdentity
from runtime.remote_access.policy import PolicyError
from runtime.remote_access.readiness import (
    ConnectorReadiness,
    GateResult,
    ReadinessReport,
    connect_loopback,
)
from runtime.remote_access.state_store import (
    AtomicFileTrustStateStore,
    CorruptTrustStateError,
)
from runtime.remote_access.credentials import StaticDaemonCredentialProvider

from .conftest import NOW, default_identity, build_consumer, route_policy_fixture  # noqa: F401


_UNSET = object()


class _RefusingConnect:
    """Inject a loopback connect that always refuses."""

    def __init__(self, fail: bool = True) -> None:
        self.fail = fail
        self.ports: list[int] = []

    def __call__(self, port: int) -> None:
        self.ports.append(port)
        if self.fail:
            raise ConnectionRefusedError("connection refused")


def _readiness(
    *,
    port: int | None = 8080,
    provider=None,
    policy=_UNSET,
    configured_identity=_UNSET,
    store=None,
    connect_fn: Callable[[int], None] | None = None,
    route_policy_fixture=None,
) -> ConnectorReadiness:
    from runtime.remote_access.credentials import StaticDaemonCredentialProvider
    from runtime.remote_access.identity import ConnectorIdentity

    if provider is None:
        provider = StaticDaemonCredentialProvider("test-bearer")
    if policy is _UNSET and route_policy_fixture is not None:
        policy = build_consumer(route_policy_fixture)
    if policy is _UNSET:
        policy = None
    if configured_identity is _UNSET:
        configured_identity = default_identity()
    if configured_identity is _UNSET:
        configured_identity = None
    if store is None:
        state = TrustState(
            connector_identity=configured_identity,
            pairing_epoch=0,
            revocation_epoch=0,
        )
        store = AtomicFileTrustStateStore(__import__("pathlib").Path("/unused-store"), state)
    if connect_fn is None:
        connect_fn = _RefusingConnect(fail=False)
    return ConnectorReadiness(
        daemon_port=port,
        credential_provider=provider,
        policy=policy,
        configured_identity=configured_identity,
        state_store=store,
        connect_fn=connect_fn,
    )


def test_gate_inventory_and_ready(route_policy_fixture) -> None:
    readiness = _readiness(route_policy_fixture=route_policy_fixture)
    report = readiness.evaluate(NOW())
    assert set(report.gates) == {
        "daemon_loopback",
        "credential_permissions",
        "current_policy",
        "bind_identity",
        "trust_state",
    }
    assert report.ready is True
    assert all(g.ok for g in report.gates.values())


def test_daemon_port_missing_fails_closed(route_policy_fixture) -> None:
    readiness = _readiness(port=None, route_policy_fixture=route_policy_fixture)
    report = readiness.evaluate(NOW())
    assert report.ready is False
    assert report.gates["daemon_loopback"].ok is False


def test_daemon_connect_refused_fails_closed(route_policy_fixture) -> None:
    readiness = _readiness(
        connect_fn=_RefusingConnect(fail=True),
        route_policy_fixture=route_policy_fixture,
    )
    report = readiness.evaluate(NOW())
    assert report.ready is False
    assert report.gates["daemon_loopback"].ok is False


def test_daemon_loopback_gate_uses_literal_loopback(route_policy_fixture) -> None:
    """The default connect implementation must target literal 127.0.0.1 — a
    real loopback listener on 127.0.0.1 is reachable, and the implementation
    never resolves or rewrites the host."""
    import socket as _socket

    listener = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    try:
        readiness = ConnectorReadiness(
            daemon_port=port,
            credential_provider=StaticDaemonCredentialProvider("b"),
            policy=build_consumer(route_policy_fixture),
            configured_identity=default_identity(),
            state_store=AtomicFileTrustStateStore(
                __import__("pathlib").Path("/unused"),
                TrustState(connector_identity=default_identity(), pairing_epoch=0, revocation_epoch=0),
            ),
            connect_fn=connect_loopback,
        )
        report = readiness.evaluate(NOW())
        assert report.gates["daemon_loopback"].ok is True
    finally:
        listener.close()


def test_credential_unreadable_fails_closed(route_policy_fixture) -> None:
    class _Unreadable(StaticDaemonCredentialProvider):
        def read_bearer(self) -> str:
            raise CredentialUnavailable("token file missing")

    readiness = _readiness(
        provider=_Unreadable("unused"), route_policy_fixture=route_policy_fixture
    )
    report = readiness.evaluate(NOW())
    assert report.ready is False
    assert report.gates["credential_permissions"].ok is False


def test_policy_missing_fails_closed(route_policy_fixture) -> None:
    readiness = _readiness(policy=None, route_policy_fixture=route_policy_fixture)
    report = readiness.evaluate(NOW())
    assert report.ready is False
    assert report.gates["current_policy"].ok is False


def test_policy_stale_fails_closed(route_policy_fixture) -> None:
    stale = build_consumer(
        route_policy_fixture,
        issued_at=NOW() - timedelta(seconds=7200),
        max_age_seconds=60,
    )
    readiness = _readiness(policy=stale, route_policy_fixture=route_policy_fixture)
    report = readiness.evaluate(NOW())
    assert report.ready is False
    assert report.gates["current_policy"].ok is False


def test_policy_rollback_fails_closed(route_policy_fixture) -> None:
    rolled = build_consumer(route_policy_fixture, revision=1, last_revision=2)
    readiness = _readiness(policy=rolled, route_policy_fixture=route_policy_fixture)
    report = readiness.evaluate(NOW())
    assert report.ready is False
    assert report.gates["current_policy"].ok is False


def test_identity_absent_fails_closed(route_policy_fixture) -> None:
    readiness = _readiness(
        configured_identity=None, route_policy_fixture=route_policy_fixture
    )
    report = readiness.evaluate(NOW())
    assert report.ready is False
    assert report.gates["bind_identity"].ok is False


def test_identity_mismatch_between_config_and_state_fails_closed(
    route_policy_fixture, tmp_path
) -> None:
    state = TrustState(
        connector_identity=ConnectorIdentity(
            tenant_id="tenant-b", home_id="home-b", connector_id="connector-b"
        ),
        pairing_epoch=0,
        revocation_epoch=0,
    )
    store = AtomicFileTrustStateStore(tmp_path / "state.json", state)
    readiness = _readiness(
        store=store, route_policy_fixture=route_policy_fixture
    )  # config identity = tenant-a/home-a
    report = readiness.evaluate(NOW())
    assert report.ready is False
    assert report.gates["bind_identity"].ok is False


def test_corrupt_state_fails_closed(route_policy_fixture, tmp_path) -> None:
    state = TrustState(
        connector_identity=default_identity(), pairing_epoch=0, revocation_epoch=0
    )
    store = AtomicFileTrustStateStore(tmp_path / "state.json", state)
    store.save(state)
    path = tmp_path / "state.json"
    path.write_text("{ corrupted")
    readiness = _readiness(store=store, route_policy_fixture=route_policy_fixture)
    report = readiness.evaluate(NOW())
    assert report.ready is False
    assert report.gates["trust_state"].ok is False


def test_missing_state_is_first_run_not_corruption(route_policy_fixture, tmp_path) -> None:
    state = TrustState(
        connector_identity=default_identity(), pairing_epoch=0, revocation_epoch=0
    )
    store = AtomicFileTrustStateStore(tmp_path / "missing.json", state)
    readiness = _readiness(store=store, route_policy_fixture=route_policy_fixture)
    report = readiness.evaluate(NOW())
    assert report.ready is True
    assert report.gates["trust_state"].ok is True


def test_report_never_contains_bearer(route_policy_fixture) -> None:
    readiness = _readiness(
        provider=StaticDaemonCredentialProvider("super-secret-bearer-xyz"),
        route_policy_fixture=route_policy_fixture,
    )
    report = readiness.evaluate(NOW())
    blob = repr(report)
    assert "super-secret-bearer-xyz" not in blob
    assert "Bearer" not in blob


def test_report_categories_are_stable(route_policy_fixture) -> None:
    readiness = _readiness(port=None, route_policy_fixture=route_policy_fixture)
    report = readiness.evaluate(NOW())
    assert report.gates["daemon_loopback"].category == "daemon_unavailable"
    assert isinstance(report.gates["daemon_loopback"].detail, str)
    assert report.gates["daemon_loopback"].detail  # non-empty prose


def test_connect_loopback_refuses_non_loopback_argument() -> None:
    with pytest.raises(ValueError):
        connect_loopback(0, host="10.0.0.9")  # type: ignore[call-arg]
