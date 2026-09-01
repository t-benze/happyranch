"""Adversarial shipping-seam tests for THR-097 managed N2 ingress."""
from __future__ import annotations

import http.client
import json
from datetime import datetime, timedelta, timezone

import pytest

from runtime.remote_access.authorization import TrustState
from runtime.remote_access.credentials import StaticDaemonCredentialProvider
from runtime.remote_access.diy_provider import (
    DEVICE_CREDENTIAL_HEADER,
    make_diy_context_factory,
    make_diy_loopback_forwarder,
)
from runtime.remote_access.identity import ConnectorIdentity
from runtime.remote_access.managed_provider import (
    ManagedProviderAdapter,
    ManagedProviderConfig,
    ManagedProviderError,
)
from runtime.remote_access.pairing import PairingManager
from runtime.remote_access.policy import RoutePolicyConsumer
from runtime.remote_access.readiness import ConnectorReadiness, GateResult, ReadinessReport
from runtime.remote_access.state import InMemoryTrustStateStore
from runtime.remote_access.streams import StreamRegistry

from .conftest import load_fixture, make_policy_envelope
from .fake_daemon import FakeDaemon

NOW = datetime(2026, 9, 1, tzinfo=timezone.utc)
IDENTITY = ConnectorIdentity(
    tenant_id="tenant-a", home_id="home-a", connector_id="connector-a"
)
BEARER = "managed-final-hop-bearer"


class _Readiness:
    def __init__(self, ready: bool = True) -> None:
        self.ready = ready

    def evaluate(self, now) -> ReadinessReport:
        gates = {
            name: GateResult(True, f"{name}_ok", "ok")
            for name in ConnectorReadiness.GATE_NAMES
        }
        if not self.ready:
            gates["daemon_loopback"] = GateResult(False, "daemon_unavailable", "unavailable")
        return ReadinessReport(ready=self.ready, gates=gates)


def _adapter(*, readiness=None):
    daemon = FakeDaemon(BEARER)
    daemon.start()
    registry = StreamRegistry()
    store = InMemoryTrustStateStore(
        TrustState(connector_identity=IDENTITY, pairing_epoch=0, revocation_epoch=0)
    )
    pairing = PairingManager(
        state_store=store, identity=IDENTITY, registry=registry, now_fn=lambda: NOW
    )
    fixture = load_fixture("route-policy")
    clock = datetime.now(timezone.utc)
    policy = RoutePolicyConsumer.from_envelope(
        make_policy_envelope(fixture, issued_at=clock - timedelta(seconds=30)),
        now=clock,
    )
    adapter = ManagedProviderAdapter(
        config=ManagedProviderConfig(bind_port=0),
        readiness=readiness or _Readiness(),
        pairing=pairing,
        identity=IDENTITY,
        ctx_factory=make_diy_context_factory(
            identity=IDENTITY,
            pairing=pairing,
            policy=policy,
            credential_provider=StaticDaemonCredentialProvider(BEARER),
            forwarder=make_diy_loopback_forwarder(daemon.port),
            registry=registry,
            now_fn=lambda: NOW,
        ),
    )
    return adapter, pairing, daemon


def _request(adapter, method, path, *, body=None, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", adapter.bound_port, timeout=5)
    conn.request(method, path, body=body, headers=headers or {})
    response = conn.getresponse()
    result = response.status, response.read()
    conn.close()
    return result


@pytest.mark.parametrize(
    "host", ["0.0.0.0", "127.0.0.2", "::1", "192.168.1.2", "localhost", ""]
)
def test_managed_ingress_rejects_every_nonliteral_loopback_host(host) -> None:
    with pytest.raises(ManagedProviderError, match="literal loopback"):
        ManagedProviderConfig(bind_host=host).validate()


def test_readiness_failure_never_creates_listener_and_is_redacted() -> None:
    adapter, _, daemon = _adapter(readiness=_Readiness(False))
    try:
        with pytest.raises(ManagedProviderError) as caught:
            adapter.start()
        assert adapter.listening is False
        assert str(caught.value) == "managed ingress unavailable"
        assert BEARER not in str(caught.value)
    finally:
        adapter.stop()
        daemon.stop()


def test_complete_gateway_pipeline_and_final_hop_bearer_placement() -> None:
    adapter, pairing, daemon = _adapter()
    try:
        adapter.start()
        assert adapter.bind_address == "127.0.0.1"
        issued = pairing.issue_pairing_code("mac")
        status, raw = _request(adapter, "POST", "/pair", body=issued.code)
        credential = json.loads(raw)["credential"]
        assert status == 200
        status, _ = _request(
            adapter,
            "GET",
            "/api/v1/health",
            headers={DEVICE_CREDENTIAL_HEADER: credential},
        )
        assert status == 200
        received = daemon.requests[-1]
        assert received["headers"]["authorization"] == f"Bearer {BEARER}"
        assert DEVICE_CREDENTIAL_HEADER not in received["headers"]
        status, raw = _request(
            adapter,
            "GET",
            "/api/v1/admin",
            headers={DEVICE_CREDENTIAL_HEADER: credential},
        )
        assert status == 403
        assert BEARER.encode() not in raw and credential.encode() not in raw
    finally:
        adapter.stop()
        daemon.stop()


def test_stop_is_idempotent_and_recovery_requires_fresh_readiness() -> None:
    readiness = _Readiness(True)
    adapter, _, daemon = _adapter(readiness=readiness)
    try:
        adapter.start()
        adapter.stop()
        adapter.stop()
        assert not adapter.listening
        readiness.ready = False
        with pytest.raises(ManagedProviderError):
            adapter.start()
        assert not adapter.listening
    finally:
        adapter.stop()
        daemon.stop()
