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
from runtime.remote_access.authorization import AuthorizationVerifier, DeviceAuthorization, TrustState
from runtime.remote_access.credentials import StaticDaemonCredentialProvider
from runtime.remote_access.forwarding import ForwardingHarness
from runtime.remote_access.gateway import ConnectorGateway, GatewayContext
from runtime.remote_access.policy import RoutePolicyConsumer
from runtime.remote_access.revocation import RevocationCoordinator
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
    from runtime.remote_access.revocation import RevocationCoordinator
    from runtime.remote_access.streams import StreamRegistry

    state = default_authorization_state()
    RevocationCoordinator(state, StreamRegistry()).revoke(epoch=2)
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


def test_mutation_revocation_transaction_skips_close_is_detected(route_policy_fixture) -> None:
    """The authoritative revocation transaction must compose registry closure:
    a mutation that applies state without closing streams leaves the revoked
    device's live stream open — the checked-in invariant catches it."""
    from runtime.remote_access.authorization import TrustState
    from runtime.remote_access.revocation import RevocationCoordinator, RevocationIncomplete
    from runtime.remote_access.streams import StreamRegistry

    def invariant(coordinator, registry, handle) -> None:
        coordinator.revoke(epoch=2)
        assert handle.closed is True, "revocation must close every open stream"
        assert registry.is_open(handle.stream_id) is False

    state = TrustState(
        connector_identity=identity.ConnectorIdentity(
            tenant_id="tenant-a", home_id="home-a", connector_id="connector-a"
        ),
        pairing_epoch=1,
        revocation_epoch=0,
    )
    state.devices["device-a"] = DeviceAuthorization(
        device_id="device-a",
        tenant_id="tenant-a",
        home_id="home-a",
        authorization_epoch=1,
        expires_at=NOW() + timedelta(days=30),
    )
    registry = StreamRegistry()
    handle = _FakeHandle("s1")
    registry.open("s1", handle)
    coordinator = RevocationCoordinator(state, registry)
    invariant(coordinator, registry, handle)  # guard present

    # Mutation: the transaction applies state but never closes streams.
    original = RevocationCoordinator.revoke

    def _state_only(self, epoch: int) -> int:
        if epoch <= self.state.revocation_epoch:
            raise ValueError("revocation epoch rollback rejected")
        self.state._apply_revocation(epoch)
        return epoch

    try:
        RevocationCoordinator.revoke = _state_only  # type: ignore[method-assign]
        fresh_state = TrustState(
            connector_identity=state.connector_identity,
            pairing_epoch=state.pairing_epoch,
            revocation_epoch=0,
        )
        fresh_state.devices["device-a"] = state.devices["device-a"]
        handle2 = _FakeHandle("s2")
        registry2 = StreamRegistry()
        registry2.open("s2", handle2)
        coordinator2 = RevocationCoordinator(fresh_state, registry2)
        with pytest.raises(AssertionError):
            invariant(coordinator2, registry2, handle2)
    finally:
        RevocationCoordinator.revoke = original


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


def test_mutation_close_all_deletes_without_sealing_is_detected(route_policy_fixture) -> None:
    """Guard removal: if close_all merely deleted the handle from the map
    without sealing the retained wrapper (the reviewer's anti-pattern), the
    retained handle would still serve bytes after revocation — the invariant
    battery catches it."""
    from runtime.remote_access.streams import StreamCloseError, StreamRegistry

    def invariant(registry, handle) -> None:
        try:
            registry.close_all()
        except Exception:
            pass  # closure imperfect/exploded — the retained handle must still reject
        try:
            handle.receive()
        except Exception:  # noqa: BLE001 - sealed wrapper rejects reads
            return
        raise AssertionError("retained handle still serves reads after revocation")

    class _Exploding:
        def __init__(self, stream_id: str) -> None:
            self.stream_id = stream_id

        def receive(self) -> bytes | None:
            return b"still-live"

        def close(self) -> None:
            raise OSError("boom")

        @property
        def closed(self) -> bool:
            return False

    registry = StreamRegistry()
    tracked = registry.open("s1", _Exploding("s1"))
    invariant(registry, tracked)  # guard present: sealed wrapper rejects

    # Mutation: close_all deletes the handles without sealing (old behavior).
    original = StreamRegistry.close_all

    def _delete_only(self) -> None:
        self._revoked = True
        self._streams.clear()

    try:
        StreamRegistry.close_all = _delete_only  # type: ignore[method-assign]
        registry2 = StreamRegistry()
        tracked2 = registry2.open("s2", _Exploding("s2"))
        with pytest.raises(AssertionError):
            invariant(registry2, tracked2)
    finally:
        StreamRegistry.close_all = original


def test_mutation_open_returns_raw_handle_is_detected(route_policy_fixture) -> None:
    """Guard removal: if the registry handed out the raw transport handle
    instead of its tracked wrapper, a failed close would leave the retained
    handle readable — the invariant battery catches it."""
    from runtime.remote_access.streams import StreamCloseError, StreamRegistry

    def invariant(registry, handle) -> None:
        try:
            registry.close_all()
        except Exception:
            pass  # closure imperfect/exploded — the retained handle must still reject
        try:
            handle.receive()
        except Exception:  # noqa: BLE001 - sealed wrapper rejects reads
            return
        raise AssertionError("retained handle still serves reads after revocation")

    class _Exploding:
        def __init__(self, stream_id: str) -> None:
            self.stream_id = stream_id

        def receive(self) -> bytes | None:
            return b"still-live"

        def close(self) -> None:
            raise OSError("boom")

        @property
        def closed(self) -> bool:
            return False

    registry = StreamRegistry()
    tracked = registry.open("s1", _Exploding("s1"))
    invariant(registry, tracked)  # guard present: wrapper is the retained surface

    original = StreamRegistry.open

    def _raw_open(self, stream_id: str, handle):
        if self._revoked:
            from runtime.remote_access.streams import StreamClosed

            raise StreamClosed(stream_id)
        self._streams[stream_id] = handle
        return handle  # bypass: retain the raw handle, no tracked wrapper

    try:
        StreamRegistry.open = _raw_open  # type: ignore[method-assign]
        registry2 = StreamRegistry()
        raw = registry2.open("s2", _Exploding("s2"))
        with pytest.raises(AssertionError):
            invariant(registry2, raw)
    finally:
        StreamRegistry.open = original


def test_mutation_wrapper_receive_ignores_seal_is_detected(route_policy_fixture) -> None:
    """Guard removal: if the tracked wrapper's receive() stopped consulting the
    sealed flag, a failed transport close would leave the retained handle
    serving still-live bytes after revocation — the invariant battery catches
    it."""
    from runtime.remote_access.streams import StreamCloseError, StreamRegistry, _TrackedStream

    def invariant(registry, handle) -> None:
        try:
            registry.close_all()
        except Exception:
            pass  # closure imperfect/exploded — the retained handle must still reject
        try:
            handle.receive()
        except Exception:  # noqa: BLE001 - sealed wrapper rejects reads
            return
        raise AssertionError("retained handle still serves reads after revocation")

    class _Exploding:
        def __init__(self, stream_id: str) -> None:
            self.stream_id = stream_id

        def receive(self) -> bytes | None:
            return b"still-live"

        def close(self) -> None:
            raise OSError("boom")

        @property
        def closed(self) -> bool:
            return False

    registry = StreamRegistry()
    tracked = registry.open("s1", _Exploding("s1"))
    invariant(registry, tracked)  # guard present: sealed receive rejects

    original = _TrackedStream.receive

    def _ignore_seal(self):
        return self._inner.receive()

    try:
        _TrackedStream.receive = _ignore_seal  # type: ignore[method-assign]
        registry2 = StreamRegistry()
        tracked2 = registry2.open("s2", _Exploding("s2"))
        with pytest.raises(AssertionError):
            invariant(registry2, tracked2)
    finally:
        _TrackedStream.receive = original


class _FakeHandle:
    def __init__(self, stream_id: str) -> None:
        self.stream_id = stream_id
        self.closed = False

    def close(self) -> None:
        self.closed = True
