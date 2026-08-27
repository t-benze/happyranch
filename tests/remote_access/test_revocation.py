"""Authoritative revocation transaction — public-seam tests (contract §9).

The connector revokes through exactly one public API: the
``RevocationCoordinator``, which composes live-stream closure with
trust-state application in the mandated order. These tests drive that seam
(never isolated ``close_all``), proving:

- one authoritative transaction closes every matching live HTTP/SSE/WebSocket
  handle before or atomically with applying trust-state revocation;
- rollback epochs are rejected before any side effect;
- when stream closure fails the registry is still sealed (a revoked device
  never retains a live registry-tracked stream) and trust-state revocation is
  still applied (state is never left ambiguously advanced);
- idempotency/at-most-once under concurrent revocation;
- the old public ``TrustState.apply_revocation`` bypass is removed.
"""
from __future__ import annotations

import threading
from datetime import timedelta

import pytest

from runtime.remote_access import identity
from runtime.remote_access.authorization import (
    AuthorizationVerifier,
    DeviceAuthorization,
    RevocationSignal,
    TrustState,
)
from runtime.remote_access.credentials import StaticDaemonCredentialProvider
from runtime.remote_access.forwarding import LOOPBACK_HOST, HttpLoopbackForwarder, LoopbackTarget
from runtime.remote_access.gateway import ConnectorGateway, GatewayContext
from runtime.remote_access.revocation import RevocationCoordinator, RevocationIncomplete
from runtime.remote_access.stripping import CredentialScanner
from runtime.remote_access.streams import StreamClosed, StreamRegistry

from .conftest import NOW, build_consumer, default_authorization_state, default_identity, make_request
from .fake_daemon import FakeDaemon

BEARER = "daemon-bearer-test-token-42"


def _gateway_context(route_policy_fixture, *, state=None, registry=None, forwarder=None, now=NOW()):
    state = state or default_authorization_state()
    registry = registry or StreamRegistry()
    return GatewayContext(
        connector_identity=default_identity(),
        proof=identity.DeviceProof(
            device_id="device-a",
            tenant_id="tenant-a",
            home_id="home-a",
            nonce="nonce-1",
            issued_at=now - timedelta(minutes=1),
            expires_at=now + timedelta(minutes=5),
        ),
        proof_verifier=identity.StaticProofVerifier(identity.ProofVerdict(ok=True)),
        single_use_guard=identity.SingleUseGuard(),
        authorization=AuthorizationVerifier(state),
        policy=build_consumer(route_policy_fixture),
        credential_provider=StaticDaemonCredentialProvider(BEARER),
        forwarder=forwarder or _streaming_forwarder(),
        stream_registry=registry,
        scanner=CredentialScanner(),
        now=now,
    )


def _streaming_forwarder():
    """A record-only forwarder that produces a live, closeable stream."""
    from runtime.remote_access.forwarding import ForwardingHarness

    class _Streaming(ForwardingHarness):
        def open_stream(self, method, path, query, headers, body, bearer, stream_id):
            from runtime.remote_access.forwarding import _HarnessStreamHandle

            self.streams.append(stream_id)
            return _HarnessStreamHandle(stream_id)

    return _Streaming()


def _open_sse(route_policy_fixture, *, state=None, registry=None):
    """Open a live SSE stream through the real gateway (POS-003 shape)."""
    ctx = _gateway_context(route_policy_fixture, state=state, registry=registry)
    decision = ConnectorGateway().decide(
        make_request(
            "GET",
            "/api/v1/orgs/acme/threads/T-1/tail",
            headers=[("accept", "text/event-stream")],
            stream_type="sse",
        ),
        ctx,
    )
    assert decision.allowed is True, "SSE positive control must open"
    assert decision.stream is not None
    return ctx, decision


def test_revoke_public_seam_closes_live_stream_and_denies_new_requests(route_policy_fixture) -> None:
    """The authoritative transaction closes the open SSE handle and the next
    request is denied with the normative revocation category."""
    state = default_authorization_state()
    registry = StreamRegistry()
    coordinator = RevocationCoordinator(state, registry)
    ctx, decision = _open_sse(route_policy_fixture, state=state, registry=registry)
    handle = decision.stream

    assert handle.closed is False
    assert registry.is_open(handle.stream_id) is True

    coordinator.revoke(epoch=2)

    # Live handle closed by the transaction.
    assert handle.closed is True
    assert registry.is_open(handle.stream_id) is False
    with pytest.raises(StreamClosed):
        handle.receive()

    # Trust state applied: new requests are denied with the normative category.
    assert state.revocation_epoch == 2
    followup = ConnectorGateway().decide(
        make_request("GET", "/api/v1/health"), ctx
    )
    assert followup.allowed is False
    assert followup.denied is not None
    assert followup.denied.deny_category == "revocation"
    assert followup.denied.audit_category == "revocation_denied"


def test_revoke_rollback_rejected_before_any_side_effect(route_policy_fixture) -> None:
    """A stale epoch is rejected before streams are closed or state advanced."""
    state = default_authorization_state()
    registry = StreamRegistry()
    coordinator = RevocationCoordinator(state, registry)
    ctx, decision = _open_sse(route_policy_fixture, state=state, registry=registry)
    handle = decision.stream

    with pytest.raises(ValueError):
        coordinator.revoke(epoch=0)  # below current revocation_epoch (0 -> 0 is not an advance)

    assert handle.closed is False, "rollback must not close live streams"
    assert registry.is_open(handle.stream_id) is True
    assert state.revocation_epoch == 0


def test_revoke_fail_closed_when_handle_close_raises(route_policy_fixture) -> None:
    """A raising handle close still seals the registry and applies state
    revocation — no revoked device retains a live stream, state is applied
    (not ambiguous), and the caller is told closure was imperfect."""

    class _ExplodingHandle:
        stream_id = "s-explode"

        def receive(self) -> bytes | None:
            return None

        def close(self) -> None:
            raise OSError("disk exploded on close")

        @property
        def closed(self) -> bool:
            return True

    state = default_authorization_state()
    registry = StreamRegistry()
    registry.open("s-explode", _ExplodingHandle())
    coordinator = RevocationCoordinator(state, registry)

    with pytest.raises(RevocationIncomplete) as excinfo:
        coordinator.revoke(epoch=2)
    err = excinfo.value
    assert err.applied_epoch == 2
    assert err.stream_ids == ("s-explode",)

    # Fail closed: registry sealed, no live stream retained, state revoked.
    assert registry.is_open("s-explode") is False
    with pytest.raises(StreamClosed):
        registry.open("late", _ExplodingHandle())
    assert state.revocation_epoch == 2
    verdict = AuthorizationVerifier(state).check("tenant-a", "home-a", "device-a", now=NOW())
    assert verdict.ok is False
    assert verdict.reason == "revocation"


def test_revoke_fires_signal_after_application(route_policy_fixture) -> None:
    """The coordinator notifies signal subscribers only after state is applied."""
    signal = RevocationSignal()
    fired: list[int] = []
    signal.subscribe(lambda epoch: fired.append(epoch))
    state = default_authorization_state()
    coordinator = RevocationCoordinator(state, StreamRegistry(), signal=signal)

    coordinator.revoke(epoch=2)
    assert fired == [2]
    assert state.revocation_epoch == 2


def test_revoke_signal_not_fired_on_rollback(route_policy_fixture) -> None:
    signal = RevocationSignal()
    fired: list[int] = []
    signal.subscribe(lambda epoch: fired.append(epoch))
    state = default_authorization_state()
    coordinator = RevocationCoordinator(state, StreamRegistry(), signal=signal)

    with pytest.raises(ValueError):
        coordinator.revoke(epoch=0)
    assert fired == []


def test_concurrent_revokes_seal_once_and_advance_monotonically(route_policy_fixture) -> None:
    """Under concurrent revocation the registry is sealed and state advances to
    the maximum applied epoch; every caller either succeeds or sees the
    rollback rejection — never a partial/ambiguous advance."""
    state = default_authorization_state()
    registry = StreamRegistry()
    handles = []
    for i in range(4):
        handle = _RecordingHandle(f"s-{i}")
        handles.append(handle)
        registry.open(f"s-{i}", handle)
    coordinator = RevocationCoordinator(state, registry)

    results: list[tuple[int, str | None]] = []
    barrier = threading.Barrier(2)

    def worker(epoch: int) -> None:
        barrier.wait()
        try:
            applied = coordinator.revoke(epoch=epoch)
            results.append((epoch, None))
            assert applied == epoch
        except ValueError:
            results.append((epoch, "rollback"))

    t1 = threading.Thread(target=worker, args=(1,))
    t2 = threading.Thread(target=worker, args=(2,))
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)
    assert not t1.is_alive() and not t2.is_alive()

    assert state.revocation_epoch == 2, "state must advance to the maximum epoch"
    assert registry._revoked is True
    for handle in handles:
        assert handle.closed is True, "every open stream must be closed"
    epochs = [e for e, _ in results]
    assert set(epochs) == {1, 2}
    failures = [f for _, f in results if f is not None]
    assert all(f == "rollback" for f in failures), "only rollback rejections may surface"
    # At most one caller may have been rejected (the lower epoch loses the race
    # only if it applied after the higher one).
    assert len(results) == 2


def test_old_public_apply_revocation_bypass_is_removed(route_policy_fixture) -> None:
    """TrustState no longer exposes a public revocation method that could be
    called without closing live streams."""
    state = default_authorization_state()
    assert not hasattr(state, "apply_revocation")
    with pytest.raises(AttributeError):
        state.apply_revocation(epoch=2)  # type: ignore[attr-defined]


def test_revoke_via_real_loopback_stream(route_policy_fixture) -> None:
    """The authoritative transaction closes a real loopback SSE stream."""
    fake = FakeDaemon(expected_bearer=BEARER, hold_open=True)
    fake.start()
    try:
        state = default_authorization_state()
        registry = StreamRegistry()
        coordinator = RevocationCoordinator(state, registry)
        ctx = _gateway_context(
            route_policy_fixture,
            state=state,
            registry=registry,
            forwarder=HttpLoopbackForwarder(LoopbackTarget(LOOPBACK_HOST, fake.port)),
        )
        decision = ConnectorGateway().decide(
            make_request(
                "GET",
                "/api/v1/orgs/acme/threads/T-1/tail",
                headers=[("accept", "text/event-stream")],
                stream_type="sse",
            ),
            ctx,
        )
        assert decision.allowed is True
        handle = decision.stream
        assert fake.started.wait(timeout=5)
        assert handle.receive() is not None

        coordinator.revoke(epoch=2)
        assert registry.is_open(handle.stream_id) is False
        with pytest.raises(StreamClosed):
            handle.receive()
        assert state.revocation_epoch == 2
    finally:
        fake.stop()


class _RecordingHandle:
    """Minimal registry handle that records close() calls."""

    def __init__(self, stream_id: str) -> None:
        self.stream_id = stream_id
        self.closed = False

    def receive(self) -> bytes | None:
        return None

    def close(self) -> None:
        self.closed = True

    @property
    def is_closed(self) -> bool:
        return self.closed


def test_revoke_http_sse_websocket_handles_all_sealed(route_policy_fixture) -> None:
    """The authoritative transaction seals retained HTTP-, SSE-, and
    WebSocket-shaped handles — nothing readable or writable survives, and
    WebSocket send() rejects after revocation."""
    state = default_authorization_state()
    registry = StreamRegistry()
    coordinator = RevocationCoordinator(state, registry)

    # HTTP-shaped handle (receive-only, status/headers surface).
    http = _WsHandle("http-1")
    http_tracked = registry.open("http-1", http)
    # SSE-shaped handle opened through the real gateway (POS-003 shape).
    ctx, decision = _open_sse(route_policy_fixture, state=state, registry=registry)
    sse_tracked = decision.stream
    # WebSocket-shaped handle (receive + send).
    ws = _WsHandle("ws-1")
    ws_tracked = registry.open("ws-1", ws)

    coordinator.revoke(epoch=2)

    for tracked in (http_tracked, sse_tracked, ws_tracked):
        assert tracked.closed is True
        assert registry.is_open(tracked.stream_id) is False
        with pytest.raises(StreamClosed):
            tracked.receive()
        with pytest.raises(StreamClosed):
            tracked.send(b"data: late\n\n")
    assert state.revocation_epoch == 2


def test_revoke_repeated_and_idempotent(route_policy_fixture) -> None:
    """Repeated revocation: re-revoking at the same epoch is rejected as
    rollback; a later higher epoch still seals (idempotent close_all never
    raises); retained handles stay sealed across both."""
    state = default_authorization_state()
    registry = StreamRegistry()
    coordinator = RevocationCoordinator(state, registry)
    handle = _WsHandle("s1")
    tracked = registry.open("s1", handle)

    coordinator.revoke(epoch=2)
    with pytest.raises(ValueError):
        coordinator.revoke(epoch=2)  # same epoch: not an advance
    coordinator.revoke(epoch=3)  # higher epoch: still applies

    assert state.revocation_epoch == 3
    assert tracked.closed is True
    with pytest.raises(StreamClosed):
        tracked.receive()
    with pytest.raises(StreamClosed):
        registry.open("late", _WsHandle("late"))


def test_concurrent_revoke_with_failing_handle_seals_all(route_policy_fixture) -> None:
    """Under concurrent revocation with a failing transport close, every
    retained wrapper is sealed, state advances to the maximum epoch, and only
    rollback rejections surface — never a live retained handle."""
    state = default_authorization_state()
    registry = StreamRegistry()

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

    good_inner = _RecordingHandle("good")
    good_tracked = registry.open("good", good_inner)
    bad_tracked = registry.open("bad", _Exploding("bad"))
    coordinator = RevocationCoordinator(state, registry)

    results: list[tuple[int, str | None]] = []
    barrier = threading.Barrier(2)

    def worker(epoch: int) -> None:
        barrier.wait()
        try:
            coordinator.revoke(epoch=epoch)
            results.append((epoch, None))
        except (ValueError, RevocationIncomplete):
            results.append((epoch, "rejected"))

    t1 = threading.Thread(target=worker, args=(1,))
    t2 = threading.Thread(target=worker, args=(2,))
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)

    assert state.revocation_epoch == 2
    assert registry._revoked is True
    assert good_inner.closed is True
    assert good_tracked.closed is True
    assert bad_tracked.closed is True
    with pytest.raises(StreamClosed):
        good_tracked.receive()
    with pytest.raises(StreamClosed):
        bad_tracked.receive()


class _WsHandle:
    """WebSocket-shaped handle: receive + send, closeable."""

    def __init__(self, stream_id: str) -> None:
        self.stream_id = stream_id
        self._closed = False

    def receive(self) -> bytes | None:
        if self._closed:
            raise StreamClosed(self.stream_id)
        return b"frame"

    def send(self, payload: bytes) -> None:
        if self._closed:
            raise StreamClosed(self.stream_id)

    def close(self) -> None:
        self._closed = True

    @property
    def closed(self) -> bool:
        return self._closed
