"""LAB-ONLY conformance provider adapter tests (THR-097 phase unit 3).

Proves the fixed invariants at the adapter seam:

- no listener unless ALL readiness gates pass (and the adapter refuses to run
  without explicit ``lab_only`` and without a concrete bind address);
- every request runs the full gateway pipeline and forwards to literal
  loopback with the bearer injected on the final hop;
- forbidden routes deny 403 with category-level prose only (no bearer, no
  paths, no input, no exception text);
- a real socket end-to-end run against the harness fake daemon succeeds, and
  the adapter stops listening cleanly.
"""
from __future__ import annotations

import http.client
import json
import socket
from datetime import timedelta

import pytest

from runtime.remote_access import identity
from runtime.remote_access.authorization import AuthorizationVerifier
from runtime.remote_access.credentials import StaticDaemonCredentialProvider
from runtime.remote_access.forwarding import (
    LOOPBACK_HOST,
    ForwardingHarness,
    HttpLoopbackForwarder,
    LoopbackTarget,
)
from runtime.remote_access.gateway import GatewayContext
from runtime.remote_access.lab_provider import (
    LAB_ONLY_BANNER,
    LabProviderAdapter,
    LabProviderConfig,
    LabProviderError,
)
from runtime.remote_access.readiness import ConnectorReadiness
from runtime.remote_access.revocation import RevocationCoordinator
from runtime.remote_access.streams import StreamRegistry
from runtime.remote_access.stripping import CredentialScanner

from .conftest import (
    NOW,
    build_consumer,
    default_authorization_state,
    default_identity,
    make_request,
)
from .fake_daemon import FakeDaemon, assert_daemon_received

BEARER = "lab-bearer-42"


class _AlwaysReady:
    def evaluate(self, now):
        from runtime.remote_access.readiness import GateResult, ReadinessReport

        gates = {name: GateResult(True, f"{name}_ok", f"{name} ok") for name in ConnectorReadiness.GATE_NAMES}
        return ReadinessReport(ready=True, gates=gates)


class _NeverReady:
    def evaluate(self, now):
        from runtime.remote_access.readiness import GateResult, ReadinessReport

        gates = {
            name: GateResult(True, f"{name}_ok", f"{name} ok")
            for name in ConnectorReadiness.GATE_NAMES
        }
        gates["daemon_loopback"] = GateResult(False, "daemon_unavailable", "no daemon")
        return ReadinessReport(ready=False, gates=gates)


def _harness_ctx_factory(route_policy_fixture, forwarder=None):
    harness = forwarder or ForwardingHarness()

    def factory(now):
        ctx = GatewayContext(
            connector_identity=default_identity(),
            proof=identity.DeviceProof(
                device_id="device-a",
                tenant_id="tenant-a",
                home_id="home-a",
                nonce="lab-nonce-1",
                issued_at=now - timedelta(minutes=1),
                expires_at=now + timedelta(minutes=5),
            ),
            proof_verifier=identity.StaticProofVerifier(identity.ProofVerdict(ok=True)),
            single_use_guard=identity.SingleUseGuard(),
            authorization=AuthorizationVerifier(default_authorization_state()),
            # policy is current AT the decision time (issued 60s before now)
            policy=build_consumer(
                route_policy_fixture, issued_at=now - timedelta(seconds=60)
            ),
            credential_provider=StaticDaemonCredentialProvider(BEARER),
            forwarder=harness,
            stream_registry=StreamRegistry(),
            scanner=CredentialScanner(),
            now=now,
        )
        return ctx

    factory.harness = harness
    return factory


def _adapter(
    route_policy_fixture,
    *,
    readiness=None,
    lab_only: bool = True,
    bind_host: str = "127.0.0.1",
    bind_port: int = 0,
    ctx_factory=None,
) -> LabProviderAdapter:
    config = LabProviderConfig(bind_host=bind_host, bind_port=bind_port, lab_only=lab_only)
    return LabProviderAdapter(
        config=config,
        readiness=readiness or _AlwaysReady(),
        ctx_factory=ctx_factory or _harness_ctx_factory(route_policy_fixture),
    )


class TestLabGating:
    def test_refuses_when_not_lab_only(self, route_policy_fixture) -> None:
        adapter = _adapter(route_policy_fixture, lab_only=False)
        with pytest.raises(LabProviderError, match="lab_only"):
            adapter.start()

    def test_refuses_wildcard_bind(self, route_policy_fixture) -> None:
        adapter = _adapter(route_policy_fixture, bind_host="0.0.0.0")
        with pytest.raises(LabProviderError, match="wildcard"):
            adapter.start()

    def test_refuses_empty_bind(self, route_policy_fixture) -> None:
        adapter = _adapter(route_policy_fixture, bind_host="")
        with pytest.raises(LabProviderError, match="wildcard"):
            adapter.start()

    def test_refuses_ipv6_wildcard_bind(self, route_policy_fixture) -> None:
        adapter = _adapter(route_policy_fixture, bind_host="::")
        with pytest.raises(LabProviderError, match="wildcard"):
            adapter.start()

    def test_banner_labels_lab_only(self) -> None:
        assert "LAB-ONLY" in LAB_ONLY_BANNER
        assert "THR-034" in LAB_ONLY_BANNER
        assert "not a product" in LAB_ONLY_BANNER.lower()


class TestReadinessGating:
    def test_no_listener_when_not_ready(self, route_policy_fixture) -> None:
        adapter = _adapter(route_policy_fixture, readiness=_NeverReady())
        with pytest.raises(LabProviderError, match="readiness failed"):
            adapter.start()
        assert adapter.listening is False
        assert adapter.bound_port is None

    def test_handle_request_before_start_fails_closed(self, route_policy_fixture) -> None:
        adapter = _adapter(route_policy_fixture)
        with pytest.raises(LabProviderError):
            adapter.handle_request(make_request("GET", "/api/v1/health"), now=NOW())

    def test_stop_after_failed_start_is_safe(self, route_policy_fixture) -> None:
        adapter = _adapter(route_policy_fixture, readiness=_NeverReady())
        with pytest.raises(LabProviderError):
            adapter.start()
        adapter.stop()  # must not raise


class TestListenerBinding:
    def test_occupied_port_fails_as_lab_provider_error(self, route_policy_fixture) -> None:
        """QA TASK-6014: a REAL bind conflict (occupied port) raises a bare
        OSError out of ``ThreadingHTTPServer``, escaping the documented
        ``LabProviderError`` startup category. Normalize at the adapter
        boundary: ``start()`` must raise ``LabProviderError`` (expected
        operational listener failure) — never a bare ``OSError`` — and leave
        no listener behind."""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as blocker:
            blocker.bind(("127.0.0.1", 0))
            blocker.listen(1)
            port = int(blocker.getsockname()[1])
            adapter = _adapter(
                route_policy_fixture, bind_host="127.0.0.1", bind_port=port
            )
            with pytest.raises(LabProviderError, match="bind"):
                adapter.start()
            assert adapter.listening is False
            assert adapter.bound_port is None


class TestPipeline:
    def test_allowed_request_forwards_with_bearer_on_final_hop(
        self, route_policy_fixture
    ) -> None:
        factory = _harness_ctx_factory(route_policy_fixture)
        adapter = _adapter(route_policy_fixture, ctx_factory=factory)
        adapter.start()
        try:
            decision = adapter.handle_request(
                make_request("GET", "/api/v1/health"), now=NOW()
            )
            assert decision.allowed is True
            assert decision.response is not None
            assert decision.response.status == 200
            # the gateway opens every request/stream through the forwarder
            # (the harness records the stream id) — the final-hop bearer
            # injection itself is asserted by the fake-daemon socket test
            # below and by the unit-C no-leak battery.
            assert factory.harness.streams
        finally:
            adapter.stop()

    def test_forbidden_route_denied_403(self, route_policy_fixture) -> None:
        adapter = _adapter(route_policy_fixture)
        adapter.start()
        try:
            decision = adapter.handle_request(
                make_request(
                    "POST", "/api/v1/orgs/acme/tasks/T-1/completion", stream_type="http"
                ),
                now=NOW(),
            )
            assert decision.allowed is False
            assert decision.denied is not None
            blob = " ".join(
                [
                    decision.audit_category,
                    decision.audit_detail,
                    decision.denied.detail,
                ]
            )
            assert BEARER not in blob
            assert "Bearer " not in blob
        finally:
            adapter.stop()

    def test_denied_never_leaks_bearer_or_input(self, route_policy_fixture) -> None:
        adapter = _adapter(route_policy_fixture)
        adapter.start()
        try:
            decision = adapter.handle_request(
                make_request("GET", "/api/v1/orgs/acme/secret-route"), now=NOW()
            )
            assert decision.allowed is False
            assert decision.denied is not None
            assert BEARER not in decision.denied.detail
            assert "secret-route" not in decision.denied.detail
        finally:
            adapter.stop()

    def test_denied_bearer_shaped_input(self, route_policy_fixture) -> None:
        adapter = _adapter(route_policy_fixture)
        adapter.start()
        try:
            decision = adapter.handle_request(
                make_request("GET", "/api/v1/health", headers=[("x-custom", f"Bearer {BEARER}")]),
                now=NOW(),
            )
            assert decision.allowed is False
        finally:
            adapter.stop()


class TestRealSocket:
    @pytest.fixture
    def fake_daemon(self):
        daemon = FakeDaemon(BEARER)
        daemon.start()
        yield daemon
        daemon.stop()

    def test_end_to_end_loopback_forward(self, route_policy_fixture, fake_daemon, tmp_path) -> None:
        token_path = tmp_path / "daemon.token"
        token_path.write_text(BEARER)
        token_path.chmod(0o600)
        forwarder = HttpLoopbackForwarder(
            LoopbackTarget(LOOPBACK_HOST, fake_daemon.port)
        )
        factory = _harness_ctx_factory(route_policy_fixture, forwarder=forwarder)
        adapter = _adapter(route_policy_fixture, ctx_factory=factory)
        adapter.start()
        try:
            conn = http.client.HTTPConnection("127.0.0.1", adapter.bound_port, timeout=5)
            conn.request("GET", "/api/v1/health")
            response = conn.getresponse()
            payload = json.loads(response.read())
            assert response.status == 200
            assert payload["ok"] is True
            assert_daemon_received(fake_daemon, "GET", "/api/v1/health")
            # the fake daemon asserts the injected Authorization itself
            assert fake_daemon.requests[0]["headers"]["authorization"] == f"Bearer {BEARER}"
            conn.close()
        finally:
            adapter.stop()

    def test_socket_forbidden_route_403_category_only(
        self, route_policy_fixture, fake_daemon, tmp_path
    ) -> None:
        token_path = tmp_path / "daemon.token"
        token_path.write_text(BEARER)
        token_path.chmod(0o600)
        forwarder = HttpLoopbackForwarder(LoopbackTarget(LOOPBACK_HOST, fake_daemon.port))
        factory = _harness_ctx_factory(route_policy_fixture, forwarder=forwarder)
        adapter = _adapter(route_policy_fixture, ctx_factory=factory)
        adapter.start()
        try:
            conn = http.client.HTTPConnection("127.0.0.1", adapter.bound_port, timeout=5)
            conn.request("POST", "/api/v1/orgs/acme/tasks/T-1/completion")
            response = conn.getresponse()
            body = response.read()
            assert response.status == 403
            assert BEARER not in body.decode()
            assert "/api/v1/orgs" not in body.decode()
            conn.close()
        finally:
            adapter.stop()

    def test_listener_stops_cleanly(self, route_policy_fixture, fake_daemon, tmp_path) -> None:
        token_path = tmp_path / "daemon.token"
        token_path.write_text(BEARER)
        token_path.chmod(0o600)
        forwarder = HttpLoopbackForwarder(LoopbackTarget(LOOPBACK_HOST, fake_daemon.port))
        factory = _harness_ctx_factory(route_policy_fixture, forwarder=forwarder)
        adapter = _adapter(route_policy_fixture, ctx_factory=factory)
        adapter.start()
        port = adapter.bound_port
        assert port is not None
        adapter.stop()
        assert adapter.listening is False
        with pytest.raises(OSError):
            sock = socket.create_connection(("127.0.0.1", port), timeout=1)
            sock.close()


class TestRevocationAtAdapterSeam:
    def test_revoked_state_denies_through_adapter(self, route_policy_fixture) -> None:
        state = default_authorization_state()
        RevocationCoordinator(state, StreamRegistry()).revoke(epoch=2)

        from runtime.remote_access.authorization import AuthorizationVerifier

        def factory(now):
            return GatewayContext(
                connector_identity=default_identity(),
                proof=identity.DeviceProof(
                    device_id="device-a",
                    tenant_id="tenant-a",
                    home_id="home-a",
                    nonce="lab-nonce-2",
                    issued_at=NOW() - timedelta(minutes=1),
                    expires_at=NOW() + timedelta(minutes=5),
                ),
                proof_verifier=identity.StaticProofVerifier(identity.ProofVerdict(ok=True)),
                single_use_guard=identity.SingleUseGuard(),
                authorization=AuthorizationVerifier(state),
                policy=build_consumer(route_policy_fixture),
                credential_provider=StaticDaemonCredentialProvider(BEARER),
                forwarder=ForwardingHarness(),
                stream_registry=StreamRegistry(),
                scanner=CredentialScanner(),
                now=now,
            )

        adapter = _adapter(route_policy_fixture, ctx_factory=factory)
        adapter.start()
        try:
            decision = adapter.handle_request(
                make_request("GET", "/api/v1/health"), now=NOW()
            )
            assert decision.allowed is False
            assert decision.audit_category == "revocation_denied"
        finally:
            adapter.stop()
