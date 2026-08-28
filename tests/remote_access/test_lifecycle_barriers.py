"""Deterministic PUBLIC-GATEWAY lifecycle barrier battery (THR-097 Unit-C
fresh lifecycle redesign; TASK-5888 CRITICAL + the exhaustive matrix).

TASK-5888 found that ``StreamRegistry.close(stream_id)`` popped membership
under the lifecycle lock but sealed the retained wrapper only AFTERWARD: a
deterministic barrier paused after the pop, then ``close_all`` sealed an
empty registry and published success while the wrapper remained readable,
reported closed=False, and its transport remained open — an ownership escape.

The fixed contract (governing spec §9, lifecycle-matrix.json): admission and
EVERY ownership/membership mutation share ONE atomic lifecycle boundary —
the affected public wrapper(s) are sealed under that boundary before
membership/ownership can escape; external transport close/open callbacks
execute outside locks but remain INSIDE the revocation acknowledgement
barrier; revocation may report success only after every pre-seal stream is
unusable/closed, all required cleanup outcomes are terminal and acknowledged,
and no post-seal admission can return usable.

These tests pause at REAL linearization/callback barriers (entered/release
events, never timing sleeps) and prove wrapper receive/send/closed state,
inner transport state and close counts, registry membership, revocation
return/error, and NO false-success acknowledgement — at the registry public
seam AND the gateway production call site. Checked-in mutation proofs remove
or bypass the unified boundary and publish success before acknowledgement —
each mutation must demonstrably go red.
"""
from __future__ import annotations

import threading
from datetime import timedelta

import pytest

from runtime.remote_access import identity
from runtime.remote_access.authorization import AuthorizationVerifier
from runtime.remote_access.credentials import StaticDaemonCredentialProvider
from runtime.remote_access.forwarding import ForwardingHarness
from runtime.remote_access.gateway import ConnectorGateway, GatewayContext
from runtime.remote_access.revocation import RevocationCoordinator
from runtime.remote_access.stripping import CredentialScanner
from runtime.remote_access.streams import (
    StreamCloseError,
    StreamClosed,
    StreamRegistry,
    _TrackedStream,
)

from .conftest import (
    NOW,
    build_consumer,
    default_authorization_state,
    default_identity,
    load_fixture,
    make_request,
)

BEARER = "daemon-bearer-test-token-42"


# ── transport handle doubles ─────────────────────────────────────────────

class _LiveHandle:
    """A live transport handle that counts close attempts."""

    stream_id = "s"

    def __init__(self, stream_id: str = "s") -> None:
        self.stream_id = stream_id
        self._closed = False
        self.close_count = 0

    def receive(self) -> bytes | None:
        if self._closed:
            raise StreamClosed(self.stream_id)
        return b"still-live"

    def close(self) -> None:
        self.close_count += 1
        self._closed = True

    @property
    def closed(self) -> bool:
        return self._closed


class _BlockingOnceHandle:
    """A handle whose close() blocks ONCE on a test-controlled barrier, then
    returns normally. Later close attempts return immediately."""

    def __init__(self, stream_id: str, entered: threading.Event, release: threading.Event) -> None:
        self.stream_id = stream_id
        self._entered = entered
        self._release = release
        self._closed = False
        self.close_count = 0

    def receive(self) -> bytes | None:
        if self._closed:
            raise StreamClosed(self.stream_id)
        return b"still-live"

    def close(self) -> None:
        self.close_count += 1
        if self._closed:
            return
        self._entered.set()
        assert self._release.wait(timeout=15), "harness: transport close never released"
        self._closed = True

    @property
    def closed(self) -> bool:
        return self._closed


# ── gateway context (real pipeline, streaming forwarder) ─────────────────

def _gateway_context(registry: StreamRegistry, state=None, forwarder=None) -> GatewayContext:
    state = state or default_authorization_state()

    class _Streaming(ForwardingHarness):
        def open_stream(self, method, path, query, headers, body, bearer, stream_id):
            self.streams.append(stream_id)
            return super().open_stream(method, path, query, headers, body, bearer, stream_id)

    return GatewayContext(
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
        authorization=AuthorizationVerifier(state),
        policy=build_consumer(load_fixture("route-policy")),
        credential_provider=StaticDaemonCredentialProvider(BEARER),
        forwarder=forwarder or _Streaming(),
        stream_registry=registry,
        scanner=CredentialScanner(),
        now=NOW(),
    )


# ═════════════════════════════════════════════════════════════════════════
# TASK-5888 exact finding: single-stream close vs the revocation seal
# ═════════════════════════════════════════════════════════════════════════

def test_single_close_vs_revoke_close_linearizes_first_ack_barrier_holds() -> None:
    """REV-005 (close linearizes first): the single-close mutation pops AND
    seals the wrapper in ONE atomic transition under the lifecycle lock, and
    its outside-lock transport close remains INSIDE the revocation
    acknowledgement barrier — close_all must NOT publish success while the
    pre-seal stream's transport close is in flight.

    Old behavior (TASK-5888): close_all sealed an empty registry and
    published success while the retained wrapper's transport was still open.
    """
    registry = StreamRegistry()
    entered = threading.Event()
    release = threading.Event()
    handle = _BlockingOnceHandle("s1", entered, release)
    wrapper = registry.open("s1", handle)

    closer = threading.Thread(target=lambda: registry.close("s1"))
    closer.start()
    assert entered.wait(timeout=15), "single close must reach the outside-lock transport close"
    # Atomic seal+membership: the retained wrapper is unusable from the
    # linearization point, even while its transport close is still running.
    assert wrapper.closed is True, "wrapper must be sealed atomically with membership removal"
    with pytest.raises(StreamClosed):
        wrapper.receive()

    done = threading.Event()

    def revoke() -> None:
        try:
            registry.close_all()
        finally:
            done.set()

    revoker = threading.Thread(target=revoke)
    try:
        revoker.start()
        assert done.wait(timeout=0.5) is False, (
            "close_all published success while the pre-seal transport close was "
            "in flight (revocation acknowledgement barrier bypassed)"
        )
    finally:
        release.set()
    assert done.wait(timeout=15), "close_all must complete after the barrier is acknowledged"
    revoker.join(timeout=15)
    closer.join(timeout=15)
    assert not revoker.is_alive() and not closer.is_alive()

    assert handle.closed is True, "pre-seal transport must be closed before close_all success"
    assert handle.close_count == 1, "the transport must be closed exactly once"
    assert wrapper.closed is True
    with pytest.raises(StreamClosed):
        wrapper.receive()
    assert registry.is_open("s1") is False


def test_single_close_vs_revoke_seal_linearizes_first_close_is_idempotent_noop() -> None:
    """REV-006 (seal linearizes first): after the revocation seal, an explicit
    single-stream close is an idempotent no-op — membership is already empty
    and the wrapper already sealed, so the transport is closed exactly once by
    the revocation snapshot and never double-closed."""
    registry = StreamRegistry()
    entered = threading.Event()
    release = threading.Event()
    handle = _BlockingOnceHandle("s1", entered, release)
    wrapper = registry.open("s1", handle)

    done = threading.Event()

    def revoke() -> None:
        try:
            registry.close_all()
        finally:
            done.set()

    revoker = threading.Thread(target=revoke)
    try:
        revoker.start()
        assert entered.wait(timeout=15), "close_all must reach the snapshot transport close"
        assert wrapper.closed is True
        registry.close("s1")  # post-seal single close: idempotent no-op
        registry.close("s1")  # missing/idempotent close: still a no-op
        registry.close("never-existed")  # unknown id: no-op
    finally:
        release.set()
    assert done.wait(timeout=15)
    revoker.join(timeout=15)
    assert not revoker.is_alive()

    assert handle.close_count == 1, "the revocation snapshot owns the exactly-once transport close"
    assert handle.closed is True
    assert registry.is_open("s1") is False
    with pytest.raises(StreamClosed):
        wrapper.receive()


def test_reviewer_exact_probe_close_pop_seal_window_eliminated(monkeypatch) -> None:
    """The TASK-5888 exact window — membership pop WITHOUT the wrapper seal —
    must not exist. If the old seam fires (``registry.close`` invoking the
    retained wrapper's ``close`` while the wrapper is still UNSEALED), the
    probe asserts the wrapper is sealed before close_all success (red on the
    old design). On the redesigned registry the seam never fires: the pop and
    the seal are ONE atomic critical section."""
    registry = StreamRegistry()
    handle = _LiveHandle("s1")
    wrapper = registry.open("s1", handle)
    entered = threading.Event()
    release = threading.Event()
    original_close = _TrackedStream.close

    def paused_close(self):
        entered.set()
        assert release.wait(timeout=15), "harness: wrapper close never released"
        original_close(self)

    monkeypatch.setattr(_TrackedStream, "close", paused_close)
    started = threading.Event()

    def run() -> None:
        started.set()
        registry.close("s1")

    closer = threading.Thread(target=run)
    closer.start()
    assert started.wait(timeout=15), "single close must start"
    seam_fired = entered.wait(timeout=5)
    try:
        if seam_fired:
            # Old design: the wrapper is UNSEALED between the pop (under the
            # lock) and tracked.close(). Revocation must not observe it.
            registry.close_all()
            assert wrapper.closed is True, (
                "revocation success observed a USABLE (unsealed) wrapper"
            )
            assert handle.closed is True, (
                "transport must be closed before revocation success"
            )
        else:
            # New design: pop + seal are one critical section — the old seam
            # is gone; the close completed atomically before the probe paused.
            assert wrapper.closed is True
            registry.close_all()
    finally:
        release.set()
    closer.join(timeout=15)
    assert not closer.is_alive()
    assert registry.is_open("s1") is False
    with pytest.raises(StreamClosed):
        wrapper.receive()


# ═════════════════════════════════════════════════════════════════════════
# Duplicate-id replacement vs the revocation seal
# ═════════════════════════════════════════════════════════════════════════

def test_duplicate_replacement_vs_revoke_ack_barrier() -> None:
    """M3 (replacement linearizes first): the replaced wrapper is sealed under
    the lifecycle lock and its outside-lock transport close stays INSIDE the
    revocation acknowledgement barrier — close_all must not publish success
    while the pre-seal replacement transport close is in flight, and its seal
    section must still complete (no lock-across-callback deadlock)."""
    registry = StreamRegistry()
    entered = threading.Event()
    release = threading.Event()
    old_handle = _BlockingOnceHandle("s", entered, release)
    registry.open("s", old_handle)

    replacer = threading.Thread(target=lambda: registry.open("s", _LiveHandle("s")))
    done = threading.Event()

    def revoke() -> None:
        try:
            registry.close_all()
        finally:
            done.set()

    revoker = threading.Thread(target=revoke)
    try:
        replacer.start()
        assert entered.wait(timeout=15), "replacement transport close must be invoked"
        revoker.start()
        assert done.wait(timeout=0.5) is False, (
            "close_all published success while the pre-seal replacement "
            "transport close was in flight"
        )
        assert registry._revoked is True, (
            "close_all's seal must complete (no lock-across-callback deadlock)"
        )
    finally:
        release.set()
    replacer.join(timeout=15)
    revoker.join(timeout=15)
    assert not replacer.is_alive() and not revoker.is_alive()
    assert done.is_set(), "close_all must complete after the barrier is acknowledged"

    assert old_handle.closed is True
    assert old_handle.close_count == 1, "the replaced transport is closed exactly once"
    assert registry.is_open("s") is False


def test_duplicate_replacement_seal_first_fails_closed() -> None:
    """M3 (seal linearizes first): a duplicate-id replacement attempted after
    the revocation seal fails closed — the allocated transport is closed by
    the registry and never returned; the sealed membership is untouched."""
    registry = StreamRegistry()
    handle = _LiveHandle("s")
    registry.open("s", handle)
    registry.close_all()
    late = _LiveHandle("s")
    with pytest.raises(StreamClosed):
        registry.open("s", late)
    assert late.closed is True, "post-seal replacement transport must be closed (never leaked)"
    assert registry.is_open("s") is False


# ═════════════════════════════════════════════════════════════════════════
# Retained-wrapper (client) close vs the revocation seal
# ═════════════════════════════════════════════════════════════════════════

def test_retained_wrapper_close_routes_through_registry() -> None:
    """M6 (retained-wrapper public close): the client-driven wrapper close is
    a REGISTRY MEMBERSHIP MUTATION — it removes membership and seals the
    wrapper atomically, so close_all afterwards never double-closes the
    transport and the client-held wrapper never escapes the atomic seal."""
    registry = StreamRegistry()
    handle = _LiveHandle("s1")
    wrapper = registry.open("s1", handle)
    wrapper.close()  # client-driven public close
    assert registry.is_open("s1") is False, (
        "wrapper close must remove membership (routed through the registry)"
    )
    assert wrapper.closed is True
    assert handle.closed is True
    assert handle.close_count == 1
    with pytest.raises(StreamClosed):
        wrapper.receive()
    registry.close_all()
    assert handle.close_count == 1, "close_all must not double-close a closed wrapper"


def test_wrapper_close_vs_revoke_seal_first_idempotent() -> None:
    """M6 (seal linearizes first): a retained-wrapper close after the
    revocation seal is an idempotent no-op; the transport is closed exactly
    once by the revocation snapshot."""
    registry = StreamRegistry()
    handle = _LiveHandle("s1")
    wrapper = registry.open("s1", handle)
    registry.close_all()
    wrapper.close()
    wrapper.close()
    assert wrapper.closed is True
    assert handle.close_count == 1, "the snapshot owns the exactly-once transport close"
    assert registry.is_open("s1") is False


# ═════════════════════════════════════════════════════════════════════════
# Gateway production call sites
# ═════════════════════════════════════════════════════════════════════════

def test_gateway_http_error_path_does_not_double_close_raw_handle(route_policy_fixture) -> None:
    """M15 (gateway HTTP error-driven cleanup): a mid-stream receive failure
    drops the partial stream through the registry (which owns the handle once
    it was passed to open) — the gateway never double-closes the raw handle."""
    class _BrokenHandle:
        stream_id = "s-broken"
        status = 200
        headers: tuple = ()

        def __init__(self) -> None:
            self.close_count = 0
            self.closed = False

        def receive(self) -> bytes | None:
            raise ConnectionResetError("[Errno 104] connection reset by peer")

        def close(self) -> None:
            self.close_count += 1
            self.closed = True

    class _Broken(ForwardingHarness):
        def open_stream(self, method, path, query, headers, body, bearer, stream_id):
            handle = _BrokenHandle()
            self.last = handle
            return handle

    registry = StreamRegistry()
    forwarder = _Broken()
    ctx = _gateway_context(registry, forwarder=forwarder)
    decision = ConnectorGateway().decide(make_request(), ctx)
    assert decision.allowed is False
    assert decision.denied is not None
    assert decision.denied.audit_category == "daemon_unavailable"
    assert registry.is_open("s-broken") is False, "partial stream must be dropped from the registry"
    assert forwarder.last.close_count == 1, (
        "the gateway must not double-close a handle the registry owns"
    )


def test_gateway_http_eof_close_vs_revoke_no_usable_stream(route_policy_fixture) -> None:
    """M14 (gateway HTTP normal-EOF cleanup): the read loop's registry close
    drops+seals the HTTP stream; a revocation racing it never observes a
    usable stream, and the decision is either complete or the normative
    revocation denial."""
    class _EofHandle:
        stream_id = "s-eof"
        status = 200
        headers: tuple = ()

        def __init__(self) -> None:
            self.close_count = 0
            self._closed = False

        def receive(self) -> bytes | None:
            if self._closed:
                raise StreamClosed(self.stream_id)
            return None  # normal EOF

        def close(self) -> None:
            self.close_count += 1
            self._closed = True

    class _Eof(ForwardingHarness):
        def open_stream(self, method, path, query, headers, body, bearer, stream_id):
            handle = _EofHandle()
            self.last = handle
            return handle

    registry = StreamRegistry()
    forwarder = _Eof()
    ctx = _gateway_context(registry, forwarder=forwarder)
    decision = ConnectorGateway().decide(make_request(), ctx)
    assert decision.allowed is True
    assert decision.response is not None
    assert registry.is_open("s-eof") is False
    assert forwarder.last.close_count == 1, "the EOF close is owned by the registry, exactly once"


def test_revoke_transaction_waits_for_pre_seal_single_close_transport() -> None:
    """M13 × M4: the authoritative RevocationCoordinator transaction must NOT
    return success while a pre-seal single-close's transport close is in
    flight — the revocation acknowledgement barrier spans the whole
    transaction, and the applied epoch is observed only after the barrier."""
    state = default_authorization_state()
    registry = StreamRegistry()
    coordinator = RevocationCoordinator(state, registry)
    entered = threading.Event()
    release = threading.Event()
    handle = _BlockingOnceHandle("s1", entered, release)
    registry.open("s1", handle)

    closer = threading.Thread(target=lambda: registry.close("s1"))
    closer.start()
    assert entered.wait(timeout=15), "single close must reach the outside-lock transport close"

    results: dict = {}
    revoker = threading.Thread(
        target=lambda: results.setdefault("r", coordinator.revoke(epoch=2))
    )
    revoker.start()
    revoker.join(timeout=0.5)
    try:
        assert revoker.is_alive(), (
            "revocation returned success before the pre-seal transport close "
            "was acknowledged (false-success acknowledgement)"
        )
    finally:
        release.set()
    revoker.join(timeout=15)
    closer.join(timeout=15)
    assert not revoker.is_alive() and not closer.is_alive()
    assert results == {"r": 2}, f"revocation outcome: {results}"
    assert state.revocation_epoch == 2
    assert handle.closed is True
    assert registry.is_open("s1") is False


def test_single_close_callback_reentrant_close_all_fails_closed_no_deadlock() -> None:
    """M10 (founder ruling, THR-097 seq140): a transport-close callback running
    inside a SINGLE close's outside-lock transport close that calls close_all
    on the same thread is REJECTED with fail-closed non-success (RuntimeError)
    — never success, never an incomplete publish, no self-inflight exclusion.
    The callback completes its own close and a later close_all from a normal
    context publishes the terminal result."""
    registry = StreamRegistry()
    events: list[str] = []

    class _ReentrantCallback:
        stream_id = "s1"

        def __init__(self) -> None:
            self._closed = False

        def receive(self) -> bytes | None:
            return None

        def close(self) -> None:
            if self._closed:
                return
            events.append("callback-close_all")
            try:
                registry.close_all()  # same-thread re-entry from inside a single close
            except RuntimeError:
                events.append("callback-close_all-rejected")
            else:
                events.append("callback-close_all-returned")  # false success — forbidden
            self._closed = True

        @property
        def closed(self) -> bool:
            return self._closed

    handle = _ReentrantCallback()
    wrapper = registry.open("s1", handle)
    registry.close("s1")  # must not deadlock
    assert events == ["callback-close_all", "callback-close_all-rejected"]
    assert handle.closed is True
    assert wrapper.closed is True
    with pytest.raises(StreamClosed):
        wrapper.receive()
    assert registry.is_open("s1") is False
    # The rejected re-entry published NO success acknowledgement.
    assert registry._cleanup_done.is_set() is False
    registry.close_all()  # terminal success from a normal context
    assert registry._cleanup_done.is_set() is True
    registry.close_all()  # idempotent after terminal completion


def test_reentrant_close_all_from_pre_seal_close_callback_success_then_terminal_success() -> None:
    """M10 mutation-first (founder ruling): a same-thread re-entrant close_all
    invoked from an UNFINISHED pre-seal single-close transport callback is
    REJECTED with fail-closed non-success — no success acknowledgement, no
    incomplete _cleanup_done publish, no self-inflight exclusion; the registry
    stays unsealed and a sibling stream stays registered and usable. After the
    callback's transport close completes successfully, a later close_all from
    a normal context seals everything and reports TERMINAL SUCCESS exactly
    once (transport close counts stay exactly-one)."""
    registry = StreamRegistry()
    entered = threading.Event()
    rejected = threading.Event()
    release = threading.Event()
    events: list[str] = []

    class _ReentrantOk:
        stream_id = "s1"

        def __init__(self) -> None:
            self._closed = False
            self.close_count = 0

        def receive(self) -> bytes | None:
            return None

        def close(self) -> None:
            self.close_count += 1
            if self._closed:
                return
            entered.set()
            assert release.wait(timeout=15), "harness: transport close never released"
            try:
                registry.close_all()  # same-thread re-entry from an unfinished callback
            except RuntimeError:
                events.append("reentrant-rejected")
            else:
                events.append("reentrant-returned")  # false success — forbidden
            rejected.set()
            self._closed = True

        @property
        def closed(self) -> bool:
            return self._closed

    s1 = _ReentrantOk()
    s2 = _LiveHandle("s2")
    w1 = registry.open("s1", s1)
    w2 = registry.open("s2", s2)
    closer = threading.Thread(target=lambda: registry.close("s1"), daemon=True)
    closer.start()
    assert entered.wait(timeout=15), "single close must reach the transport callback"
    assert w1.closed is True  # sealed atomically with the pop (M4)
    assert registry._cleanup_done.is_set() is False
    release.set()
    assert rejected.wait(timeout=15), "re-entrant close_all must return"
    assert events == ["reentrant-rejected"]
    # The rejection must NOT have published anything or sealed the registry.
    assert registry._cleanup_done.is_set() is False
    assert registry._revoked is False
    assert registry.is_open("s2") is True
    assert s2.close_count == 0
    closer.join(timeout=15)
    assert not closer.is_alive()
    assert s1._closed is True and s1.close_count == 1
    assert w2.closed is False  # sibling stream still usable (registry unsealed)
    # A later close_all from a NORMAL context reports terminal success.
    registry.close_all()
    assert registry._cleanup_done.is_set() is True
    assert s2.close_count == 1 and s1.close_count == 1  # exactly-once transport closes
    assert w2.closed is True
    assert registry.is_open("s1") is False and registry.is_open("s2") is False
    with pytest.raises(StreamClosed):
        w1.receive()
    with pytest.raises(StreamClosed):
        w2.receive()
    registry.close_all()  # idempotent after terminal completion, never raises


def test_reentrant_close_all_from_pre_seal_close_callback_failure_persisted() -> None:
    """M10 mutation-first + REV-008 (founder ruling; the TASK-5925 reviewer's
    exact probe). A transport-close callback inside a pre-seal single close
    re-enters close_all on the same thread; the re-entrant call is REJECTED
    (fail-closed non-success, no success acknowledgement, no incomplete
    failed-id publish) and the callback's close then FAILS — becoming terminal
    AFTER the rejection. A later close_all MUST re-surface the PERSISTED
    failed stream id (never erased), and the authoritative RevocationCoordinator
    surfaces it as RevocationIncomplete with the applied epoch."""
    from runtime.remote_access.revocation import RevocationIncomplete

    state = default_authorization_state()
    registry = StreamRegistry()
    coordinator = RevocationCoordinator(state, registry)
    entered = threading.Event()
    rejected = threading.Event()
    release = threading.Event()
    events: list[str] = []
    outcome: dict = {}

    class _ReentrantFailing:
        stream_id = "s1"

        def receive(self) -> bytes | None:
            return None

        def close(self) -> None:
            entered.set()
            assert release.wait(timeout=15), "harness: transport close never released"
            try:
                registry.close_all()
            except RuntimeError:
                events.append("reentrant-rejected")
            else:
                events.append("reentrant-returned")  # false success — forbidden
            rejected.set()
            raise OSError("transport close failed after the re-entrant rejection")

        @property
        def closed(self) -> bool:
            return False

    w1 = registry.open("s1", _ReentrantFailing())

    def closer_run() -> None:
        try:
            registry.close("s1")
        except Exception as exc:  # noqa: BLE001 - harness captures the outcome
            outcome["exc"] = exc

    closer = threading.Thread(target=closer_run, daemon=True)
    closer.start()
    assert entered.wait(timeout=15), "single close must reach the transport callback"
    release.set()
    assert rejected.wait(timeout=15), "re-entrant close_all must return"
    assert events == ["reentrant-rejected"]
    assert registry._cleanup_done.is_set() is False  # no incomplete publish
    closer.join(timeout=15)
    assert not closer.is_alive()
    assert isinstance(outcome["exc"], OSError), f"close outcome: {outcome}"
    assert w1.cleanup_failed is True
    assert w1.closed is True
    with pytest.raises(StreamClosed):
        w1.receive()
    assert registry.is_open("s1") is False
    # The failure became terminal AFTER the rejection and must be PERSISTED.
    with pytest.raises(StreamCloseError) as excinfo:
        registry.close_all()
    assert excinfo.value.stream_ids == ("s1",)
    # Every later close_all re-raises the persisted failure — never erased.
    with pytest.raises(StreamCloseError) as excinfo2:
        registry.close_all()
    assert excinfo2.value.stream_ids == ("s1",)
    # The authoritative transaction reports the persisted failure too.
    with pytest.raises(RevocationIncomplete) as excinfo3:
        coordinator.revoke(epoch=2)
    assert excinfo3.value.applied_epoch == 2
    assert excinfo3.value.stream_ids == ("s1",)
    assert state.revocation_epoch == 2


def test_reentrant_close_all_from_callback_while_cleanup_in_progress_fails_closed() -> None:
    """M12 (founder ruling): a transport-close callback re-enters close_all on
    the same thread while ANOTHER thread's close_all cleanup is in progress
    (the runner is waiting on THIS callback's close at the acknowledgement
    barrier). The re-entrant call is REJECTED with fail-closed non-success —
    it must never wait on the shared completion event (that would
    self-deadlock the barrier). The runner then publishes the complete
    terminal result, and the callback thread's later normal-context close_all
    observes it."""
    registry = StreamRegistry()
    entered = threading.Event()
    rejected = threading.Event()
    release = threading.Event()
    events: list[str] = []
    results: dict = {}

    class _BlockingReentrant:
        stream_id = "s1"

        def __init__(self) -> None:
            self._closed = False

        def receive(self) -> bytes | None:
            return None

        def close(self) -> None:
            entered.set()
            assert release.wait(timeout=15), "harness: transport close never released"
            try:
                registry.close_all()  # re-entry while another close_all is in progress
            except RuntimeError:
                events.append("reentrant-rejected")
            else:
                events.append("reentrant-returned")  # false success — forbidden
            rejected.set()
            self._closed = True

        @property
        def closed(self) -> bool:
            return self._closed

    s1 = _BlockingReentrant()
    s2 = _LiveHandle("s2")
    w1 = registry.open("s1", s1)
    w2 = registry.open("s2", s2)
    closer = threading.Thread(target=lambda: registry.close("s1"), daemon=True)
    closer.start()
    assert entered.wait(timeout=15), "single close must reach the transport callback"

    def revoke() -> None:
        try:
            registry.close_all()
            results["r"] = "ok"
        except StreamCloseError as exc:
            results["r"] = ("incomplete", exc.stream_ids)

    revoker = threading.Thread(target=revoke, daemon=True)
    revoker.start()
    revoker.join(timeout=0.5)
    try:
        assert revoker.is_alive(), (
            "revocation runner returned before the pre-seal transport close was terminal"
        )
    finally:
        release.set()
    assert rejected.wait(timeout=15), "re-entrant close_all must return non-success (no deadlock)"
    assert events == ["reentrant-rejected"]
    closer.join(timeout=15)
    revoker.join(timeout=15)
    assert not revoker.is_alive() and not closer.is_alive()
    assert results == {"r": "ok"}, f"runner outcome: {results}"
    assert s2.close_count == 1  # the runner closed the sibling stream exactly once
    assert w1.closed is True and w2.closed is True
    assert registry.is_open("s1") is False and registry.is_open("s2") is False
    # The callback thread's LATER normal-context close_all observes the terminal result.
    registry.close_all()  # idempotent no-op, never raises


def test_reentrant_close_all_from_snapshot_callback_fails_closed() -> None:
    """M10 seal-first (founder ruling): a transport-close callback that re-enters
    close_all DURING the runner's own snapshot cleanup (the registry is already
    sealed; the runner's cleanup is in progress on this thread) is REJECTED
    with fail-closed non-success — never a silent success, never an incomplete
    publish. A callback that handles the rejection completes its own close and
    the outer run publishes the real terminal result."""
    registry = StreamRegistry()
    events: list[str] = []

    class _SnapshotReentrant:
        stream_id = "s1"

        def __init__(self) -> None:
            self._closed = False

        def receive(self) -> bytes | None:
            return None

        def close(self) -> None:
            if self._closed:
                return
            try:
                registry.close_all()
            except RuntimeError:
                events.append("snapshot-reentrant-rejected")
            else:
                events.append("snapshot-reentrant-returned")  # false success — forbidden
            self._closed = True

        @property
        def closed(self) -> bool:
            return self._closed

    handle = _SnapshotReentrant()
    wrapper = registry.open("s1", handle)
    registry.close_all()  # outer run: snapshot close -> callback re-entry
    assert events == ["snapshot-reentrant-rejected"]
    assert handle.closed is True
    assert wrapper.closed is True
    assert registry.is_open("s1") is False
    registry.close_all()  # idempotent no-op after terminal completion


# ═════════════════════════════════════════════════════════════════════════
# Derived membership surface
# ═════════════════════════════════════════════════════════════════════════

def test_derived_membership_reads_report_sealed_truth() -> None:
    """M9: is_open/raise_if_open are synchronized on the lifecycle lock and
    report the sealed truth after every membership mutation."""
    registry = StreamRegistry()
    handle = _LiveHandle("s1")
    wrapper = registry.open("s1", handle)
    assert registry.is_open("s1") is True
    registry.close("s1")
    assert registry.is_open("s1") is False
    with pytest.raises(StreamClosed):
        registry.raise_if_open("s1")
    registry.close_all()
    assert registry.is_open("s1") is False
    with pytest.raises(StreamClosed):
        registry.raise_if_open("s1")
    assert wrapper.closed is True


def test_missing_and_idempotent_closes_are_noops() -> None:
    """M7: closing an unknown id or a sealed wrapper is an idempotent no-op
    that never raises and never re-closes a transport."""
    registry = StreamRegistry()
    handle = _LiveHandle("s1")
    wrapper = registry.open("s1", handle)
    registry.close_all()
    wrapper.close()
    wrapper.close()
    registry.close("s1")
    registry.close("never-existed")
    assert handle.close_count == 1
    assert wrapper.closed is True
    assert registry.is_open("s1") is False


# ═════════════════════════════════════════════════════════════════════════
# Checked-in mutation proofs (each must go red)
# ═════════════════════════════════════════════════════════════════════════

def test_mutation_close_pops_without_seal_is_detected(monkeypatch) -> None:
    """Mutation: close() pops membership under the lifecycle lock but seals
    the wrapper only AFTER releasing it (the exact TASK-5888 anti-pattern).
    The reviewer-exact probe must go red: revocation success must never
    observe a usable (unsealed) wrapper."""
    def invariant() -> None:
        registry = StreamRegistry()
        handle = _LiveHandle("s1")
        wrapper = registry.open("s1", handle)
        entered = threading.Event()
        release = threading.Event()
        original_close = _TrackedStream.close

        def paused_close(self):
            entered.set()
            assert release.wait(timeout=15), "harness: wrapper close never released"
            original_close(self)

        monkeypatch.setattr(_TrackedStream, "close", paused_close)
        started = threading.Event()

        def run() -> None:
            started.set()
            registry.close("s1")

        closer = threading.Thread(target=run)
        closer.start()
        assert started.wait(timeout=15), "single close must start"
        seam_fired = entered.wait(timeout=5)
        try:
            if seam_fired:
                registry.close_all()
                assert wrapper.closed is True, (
                    "revocation success observed a USABLE (unsealed) wrapper"
                )
                assert handle.closed is True
            else:
                assert wrapper.closed is True
                registry.close_all()
        finally:
            release.set()
        closer.join(timeout=15)
        assert registry.is_open("s1") is False
        with pytest.raises(StreamClosed):
            wrapper.receive()

    invariant()  # guard present: the atomic seal+membership boundary holds

    original = StreamRegistry.close

    def _pop_without_seal(self, stream_id):
        # TASK-5888 anti-pattern: membership pop under the lock, wrapper seal
        # AFTER the lock is released — an ownership escape window.
        with self._lock:
            tracked = self._streams.pop(stream_id, None)
        if tracked is not None:
            tracked.close()

    try:
        StreamRegistry.close = _pop_without_seal  # type: ignore[method-assign]
        with pytest.raises(AssertionError):
            invariant()
    finally:
        StreamRegistry.close = original


def test_mutation_close_all_publishes_before_acknowledgement_is_detected() -> None:
    """Mutation: close_all publishes success WITHOUT waiting inside the
    revocation acknowledgement barrier. The close-vs-revoke barrier must go
    red: revocation reports success while a pre-seal transport close is still
    in flight (false-success acknowledgement)."""
    def invariant() -> threading.Event:
        registry = StreamRegistry()
        entered = threading.Event()
        release = threading.Event()
        handle = _BlockingOnceHandle("s1", entered, release)
        wrapper = registry.open("s1", handle)
        closer = threading.Thread(target=lambda: registry.close("s1"))
        closer.start()
        assert entered.wait(timeout=15), "single close must reach the transport close"
        assert wrapper.closed is True
        done = threading.Event()

        def revoke() -> None:
            try:
                registry.close_all()
            finally:
                done.set()

        revoker = threading.Thread(target=revoke)
        revoker.start()
        try:
            assert done.wait(timeout=0.5) is False, (
                "close_all published success before the pre-seal transport "
                "close was acknowledged"
            )
        finally:
            release.set()
        assert done.wait(timeout=15)
        revoker.join(timeout=15)
        closer.join(timeout=15)
        assert handle.closed is True
        assert registry.is_open("s1") is False
        return release

    release = invariant()  # guard present
    assert release.is_set()

    original_wait = StreamRegistry._wait_for_inflight_acknowledgement

    def _no_wait(self):
        return None  # mutation: publish success before callback/cleanup acknowledgement

    try:
        StreamRegistry._wait_for_inflight_acknowledgement = _no_wait  # type: ignore[method-assign]
        with pytest.raises(AssertionError):
            invariant()
    finally:
        StreamRegistry._wait_for_inflight_acknowledgement = original_wait


def test_mutation_no_acknowledgement_registration_is_detected() -> None:
    """Mutation: outside-lock transport closes are never registered on the
    revocation acknowledgement barrier. The close-vs-revoke barrier must go
    red: close_all cannot wait for a close it never registered."""
    def invariant() -> threading.Event:
        registry = StreamRegistry()
        entered = threading.Event()
        release = threading.Event()
        handle = _BlockingOnceHandle("s1", entered, release)
        wrapper = registry.open("s1", handle)
        closer = threading.Thread(target=lambda: registry.close("s1"))
        closer.start()
        assert entered.wait(timeout=15), "single close must reach the transport close"
        assert wrapper.closed is True
        done = threading.Event()

        def revoke() -> None:
            try:
                registry.close_all()
            finally:
                done.set()

        revoker = threading.Thread(target=revoke)
        revoker.start()
        try:
            assert done.wait(timeout=0.5) is False, (
                "close_all published success while the pre-seal transport "
                "close was in flight"
            )
        finally:
            release.set()
        assert done.wait(timeout=15)
        revoker.join(timeout=15)
        closer.join(timeout=15)
        assert handle.closed is True
        assert registry.is_open("s1") is False
        return release

    release = invariant()  # guard present
    assert release.is_set()

    original_begin = StreamRegistry._begin_inflight_close

    def _no_register(self):
        return None  # mutation: transport closes are never registered on the barrier

    try:
        StreamRegistry._begin_inflight_close = _no_register  # type: ignore[method-assign]
        with pytest.raises(AssertionError):
            invariant()
    finally:
        StreamRegistry._begin_inflight_close = original_begin


def test_mutation_wrapper_close_not_routed_through_registry_is_detected() -> None:
    """Mutation: the retained wrapper's close seals and closes the inner
    transport WITHOUT removing registry membership. The membership-truth
    invariant must go red: a client-closed stream stays registered and the
    revocation snapshot would double-close its transport."""
    def invariant() -> None:
        registry = StreamRegistry()
        handle = _LiveHandle("s1")
        wrapper = registry.open("s1", handle)
        wrapper.close()
        assert registry.is_open("s1") is False, (
            "wrapper close must remove membership (routed through the registry)"
        )
        assert wrapper.closed is True
        assert handle.close_count == 1
        registry.close_all()
        assert handle.close_count == 1, "close_all must not double-close"

    invariant()  # guard present: the wrapper close routes through the registry

    original = _TrackedStream.close

    def _unrouted_close(self):
        # Anti-pattern: seal + close the inner without the registry membership
        # transition — the stream stays registered and escapes the boundary.
        if self._sealed:
            return
        self._sealed = True
        self._close_inner()

    try:
        _TrackedStream.close = _unrouted_close  # type: ignore[method-assign]
        with pytest.raises(AssertionError):
            invariant()
    finally:
        _TrackedStream.close = original


def test_mutation_self_inflight_exclusion_into_false_success_is_detected() -> None:
    """Mutation (the TASK-5925 exact anti-pattern): close_all EXCLUDES the
    calling thread's own in-flight closes from the acknowledgement barrier and
    publishes an incomplete success. The reviewer-exact probe must go red: the
    re-entrant close_all must be REJECTED (fail-closed non-success) and a later
    close_all must re-surface the persisted callback failure — never an erased
    success."""
    def invariant() -> None:
        registry = StreamRegistry()
        events: list[str] = []

        class _ReentrantFailing:
            stream_id = "s1"

            def receive(self) -> bytes | None:
                return None

            def close(self) -> None:
                try:
                    registry.close_all()
                except RuntimeError:
                    events.append("rejected")
                else:
                    events.append("false-success")  # forbidden: exclusion into success
                raise OSError("boom")

            @property
            def closed(self) -> bool:
                return False

        registry.open("s1", _ReentrantFailing())
        with pytest.raises(OSError):
            registry.close("s1")
        assert events == ["rejected"], (
            "re-entrant close_all must be rejected, not excluded into success"
        )
        with pytest.raises(StreamCloseError) as excinfo:
            registry.close_all()
        assert excinfo.value.stream_ids == ("s1",), (
            "persisted callback failure must be re-surfaced by a later close_all"
        )
        with pytest.raises(StreamCloseError) as excinfo2:
            registry.close_all()
        assert excinfo2.value.stream_ids == ("s1",), (
            "later close_all must not erase the callback failure"
        )

    invariant()  # guard present: re-entrant close_all is rejected; failure persisted

    original = StreamRegistry.close_all

    def _exclude_self_inflight(self) -> None:
        # TASK-5925 anti-pattern: same-thread re-entrant close_all excludes its
        # own in-flight close from the acknowledgement barrier, publishes an
        # incomplete success, and erases the callback failure that becomes
        # terminal later.
        if getattr(self._in_cleanup, "active", False):
            return
        with self._lock:
            if self._cleanup_done.is_set():
                failures = self._cleanup_failed_ids
                role = "observe"
            elif self._cleanup_started:
                role = "wait"
            else:
                self._revoked = True
                for tracked in list(self._streams.values()):
                    tracked.seal()
                pending = list(self._streams.items())
                self._streams.clear()
                self._cleanup_started = True
                role = "run"
        if role == "wait":
            self._cleanup_done.wait()
            with self._lock:
                failures = self._cleanup_failed_ids
        elif role == "run":
            with self._cleanup_ack:
                # The bug: the barrier excludes the calling thread's OWN
                # in-flight closes, so an unfinished callback re-entry is
                # treated as fully acknowledged.
                while (
                    self._cleanup_in_flight
                    - getattr(self._self_inflight, "count", 0)
                    > 0
                ):
                    self._cleanup_ack.wait()
            failed: list[str] = []
            self._in_cleanup.active = True
            try:
                for stream_id, tracked in pending:
                    try:
                        tracked._close_inner()
                    except Exception:
                        failed.append(stream_id)
            finally:
                with self._lock:
                    self._cleanup_failed_ids = tuple(failed)
                    self._cleanup_done.set()
                self._in_cleanup.active = False
            if failed:
                raise StreamCloseError(failed)
            return
        if failures:
            raise StreamCloseError(failures)

    try:
        StreamRegistry.close_all = _exclude_self_inflight  # type: ignore[method-assign]
        with pytest.raises(AssertionError):
            invariant()
    finally:
        StreamRegistry.close_all = original
