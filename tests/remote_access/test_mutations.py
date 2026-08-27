"""Checked-in mutation-style tests: every load-bearing guard's removal changes
the security outcome, and the invariant battery detects it.

Each mutation is applied at the real production seam the code reads (module
constant / class attribute / step order), then the shared invariant helper is
re-run — proving the guard is what prevents the attack.
"""
from __future__ import annotations

from datetime import timedelta

import pytest

from runtime.remote_access import identity
from runtime.remote_access.authorization import AuthorizationVerifier
from runtime.remote_access.credentials import StaticDaemonCredentialProvider
from runtime.remote_access.forwarding import ForwardingHarness
from runtime.remote_access.gateway import ConnectorGateway, GatewayContext
from runtime.remote_access.policy import RoutePolicyConsumer
from runtime.remote_access.stripping import CredentialScanner
from runtime.remote_access.streams import StreamRegistry

from .conftest import NOW, build_consumer, default_authorization_state, default_identity, make_request

BEARER = "daemon-bearer-test-token-42"


def _ctx(route_policy_fixture, **overrides) -> GatewayContext:
    fields = dict(
        connector_identity=default_identity(),
        proof=identity.DeviceProof(
            device_id="device-a",
            tenant_id="tenant-a",
            home_id="home-a",
            nonce="n1",
            issued_at=NOW() - timedelta(minutes=1),
            expires_at=NOW() + timedelta(minutes=5),
        ),
        proof_verifier=identity.StaticProofVerifier(identity.ProofVerdict(ok=True)),
        single_use_guard=identity.SingleUseGuard(),
        authorization=AuthorizationVerifier(default_authorization_state()),
        policy=build_consumer(route_policy_fixture),
        credential_provider=StaticDaemonCredentialProvider(BEARER),
        forwarder=ForwardingHarness(),
        stream_registry=StreamRegistry(),
        scanner=CredentialScanner(),
        now=NOW(),
    )
    fields.update(overrides)
    return GatewayContext(**fields)


def _invariant_unclassified_denied(gateway, ctx) -> None:
    decision = gateway.decide(make_request("GET", "/api/v1/orgs/acme/some-new-route"), ctx)
    assert decision.allowed is False, "unclassified routes must be denied"
    assert decision.denied is not None
    assert decision.denied.audit_category == "unclassified_denied"


def test_mutation_default_behavior_allow_unclassified_is_detected(route_policy_fixture) -> None:
    gateway = ConnectorGateway()
    ctx = _ctx(route_policy_fixture)
    _invariant_unclassified_denied(gateway, ctx)

    original = RoutePolicyConsumer.DEFAULT_BEHAVIOR_OVERRIDE
    try:
        RoutePolicyConsumer.DEFAULT_BEHAVIOR_OVERRIDE = "allow_unclassified"
        with pytest.raises(AssertionError):
            _invariant_unclassified_denied(gateway, ctx)
    finally:
        RoutePolicyConsumer.DEFAULT_BEHAVIOR_OVERRIDE = original


def test_mutation_normalization_disabled_allows_traversal(route_policy_fixture) -> None:
    gateway = ConnectorGateway()
    ctx = _ctx(route_policy_fixture)

    def invariant() -> None:
        decision = gateway.decide(make_request("GET", "/api/v1/orgs/%2e%2e%2f%2e%2e/etc/passwd"), ctx)
        assert decision.allowed is False, "encoded traversal must be denied"
        assert decision.denied is not None
        assert decision.denied.audit_category == "normalization_denied"

    invariant()
    original = ConnectorGateway.NORMALIZATION_ENABLED
    try:
        ConnectorGateway.NORMALIZATION_ENABLED = False
        with pytest.raises(AssertionError):
            invariant()
    finally:
        ConnectorGateway.NORMALIZATION_ENABLED = original


def test_mutation_allowlist_disabled_allows_forbidden_route(route_policy_fixture) -> None:
    gateway = ConnectorGateway()
    ctx = _ctx(route_policy_fixture)

    def invariant() -> None:
        decision = gateway.decide(make_request("POST", "/api/v1/orgs/acme/tasks/T-1/completion"), ctx)
        assert decision.allowed is False, "agent-callback route must be denied"
        assert decision.denied.audit_category == "route_denied"

    invariant()
    original = ConnectorGateway.ALLOWLIST_ENABLED
    try:
        ConnectorGateway.ALLOWLIST_ENABLED = False
        with pytest.raises(AssertionError):
            invariant()
    finally:
        ConnectorGateway.ALLOWLIST_ENABLED = original


def test_mutation_revocation_check_disabled_allows_revoked_device(route_policy_fixture) -> None:
    state = default_authorization_state()
    state.apply_revocation(epoch=2)
    ctx = _ctx(route_policy_fixture, authorization=AuthorizationVerifier(state))
    gateway = ConnectorGateway()

    def invariant() -> None:
        decision = gateway.decide(make_request("GET", "/api/v1/health"), ctx)
        assert decision.allowed is False, "revoked device must be denied"
        assert decision.denied.audit_category == "revocation_denied"

    invariant()
    original = ConnectorGateway.AUTHORIZATION_ENABLED
    try:
        ConnectorGateway.AUTHORIZATION_ENABLED = False
        with pytest.raises(AssertionError):
            invariant()
    finally:
        ConnectorGateway.AUTHORIZATION_ENABLED = original


def test_mutation_proof_verification_disabled_allows_wrong_identity(route_policy_fixture) -> None:
    ctx = _ctx(
        route_policy_fixture,
        proof=identity.DeviceProof(
            device_id="device-a",
            tenant_id="tenant-b",  # wrong tenant — must never authenticate
            home_id="home-a",
            nonce="n1",
            issued_at=NOW() - timedelta(minutes=1),
            expires_at=NOW() + timedelta(minutes=5),
        ),
        proof_verifier=identity.StaticProofVerifier(identity.ProofVerdict(ok=False, reason="wrong_audience")),
    )
    gateway = ConnectorGateway()

    def invariant() -> None:
        decision = gateway.decide(make_request("GET", "/api/v1/health"), ctx)
        assert decision.allowed is False, "wrong-audience proof must be denied"
        assert decision.denied.audit_category == "audience_denied"

    invariant()
    original = ConnectorGateway.PROOF_ENABLED
    try:
        ConnectorGateway.PROOF_ENABLED = False
        with pytest.raises(AssertionError):
            invariant()
    finally:
        ConnectorGateway.PROOF_ENABLED = original


def test_mutation_bearer_read_moved_before_policy_is_detected(route_policy_fixture) -> None:
    """Ordering mutation: reading the daemon bearer before validating policy
    changes the outcome — the bearer must never be read under a stale policy."""

    class _Recording(StaticDaemonCredentialProvider):
        def __init__(self) -> None:
            super().__init__(BEARER)
            self.reads = 0

        def read_bearer(self) -> str:
            self.reads += 1
            return super().read_bearer()

    def invariant(provider) -> None:
        decision = gateway.decide(request, ctx)
        assert decision.allowed is False
        assert decision.denied is not None
        assert decision.denied.audit_category == "policy_stale"
        assert provider.reads == 0, "bearer must never be read under stale policy"

    gateway = ConnectorGateway()
    provider = _Recording()
    stale = build_consumer(route_policy_fixture, max_age_seconds=60)
    ctx = _ctx(route_policy_fixture, policy=stale, credential_provider=provider, now=NOW() + timedelta(seconds=120))
    request = make_request("GET", "/api/v1/health")

    invariant(provider)
    original = ConnectorGateway._STEP_ORDER
    mutated = list(original)
    i, j = mutated.index("bearer"), mutated.index("policy")
    mutated[i], mutated[j] = mutated[j], mutated[i]
    try:
        ConnectorGateway._STEP_ORDER = tuple(mutated)
        with pytest.raises(AssertionError):
            invariant(provider)
    finally:
        ConnectorGateway._STEP_ORDER = original


def test_mutation_stream_revocation_ignored_is_detected(route_policy_fixture) -> None:
    """If the revocation signal stops closing streams, an open stream survives
    revocation — the registry's close_all is the load-bearing guard."""
    from runtime.remote_access.streams import StreamRegistry

    def invariant(registry, handle) -> None:
        registry.close_all()
        assert handle.closed is True, "revocation must close every open stream"

    registry = StreamRegistry()
    handle = _FakeHandle("s1")
    registry.open("s1", handle)
    invariant(registry, handle)  # guard present: closed

    # Mutation: close_all is a no-op (revocation does not close streams).
    original = StreamRegistry.close_all
    try:
        StreamRegistry.close_all = lambda self: None  # type: ignore[method-assign]
        handle2 = _FakeHandle("s2")
        registry2 = StreamRegistry()
        registry2.open("s2", handle2)
        with pytest.raises(AssertionError):
            invariant(registry2, handle2)
    finally:
        StreamRegistry.close_all = original


def test_mutation_strip_disabled_allows_auth_header_forward(route_policy_fixture) -> None:
    """Without stripping, the remote Authorization header would reach the
    daemon — the outbound leak check on the forward hop catches it."""
    from runtime.remote_access.forwarding import HttpLoopbackForwarder, LOOPBACK_HOST, LoopbackTarget, OutboundLeakError
    from .fake_daemon import FakeDaemon

    fake = FakeDaemon(expected_bearer=BEARER)
    fake.start()
    try:
        forwarder = HttpLoopbackForwarder(LoopbackTarget(LOOPBACK_HOST, fake.port))
        # Simulate the strip step having been removed: the remote Authorization
        # header reaches the forwarder alongside the injected bearer.
        outbound_headers = (
            __import__("runtime.remote_access.models", fromlist=["Header"]).Header("authorization", "Bearer remote-token"),
        )
        with pytest.raises(OutboundLeakError):
            forwarder.forward_once(
                "GET", "/api/v1/health", None, outbound_headers, None, BEARER
            )
        assert fake.requests == []
    finally:
        fake.stop()


class _FakeHandle:
    def __init__(self, stream_id: str) -> None:
        self.stream_id = stream_id
        self.closed = False

    def close(self) -> None:
        self.closed = True
