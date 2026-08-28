"""Admission/seal atomic ownership boundary — public-seam battery (TASK-5874
structural redesign).

TASK-5874 [CRITICAL] found that ``StreamRegistry.open`` read ``_revoked``
without the lifecycle lock: an admission could read ``_revoked=False``,
pause while ``close_all`` sealed, snapshotted, cleaned up and published a
successful terminal result, then insert and return a NEW UNSEALED stream —
a revoked device retained a live stream after revocation succeeded.

The structural contract (governing spec §9):

- Admission and revocation share ONE atomic ownership boundary: a single
  lifecycle lock guards BOTH the sealed flag and registry membership. The
  revocation seal is the linearization point and occurs before the live-
  stream snapshot.
- An admission is successful ONLY if it is fully registered before the
  seal. Any admission not fully registered before the seal fails closed and
  NEVER returns a usable wrapper — even when transport allocation or
  callbacks raced. The allocated transport is closed by the fail-closed
  admission itself (once the registry owns a handle, it owns its cleanup).
- A pre-seal registration is included in the revocation snapshot: its
  wrapper is irrevocably sealed and its physical-cleanup outcome is
  acknowledged before close_all success.
- Transport open/close and duplicate-replacement callbacks never execute
  under the lifecycle lock (no deadlock/reentrancy hazard); a transport-
  close callback that opens a stream on the same thread fails closed.

These tests drive the public registry seam AND the gateway production call
site (``gateway.py`` ``_forward``), assert lifecycle postconditions (never
timing sleeps), and include checked-in mutation proofs that remove or move
the admission/seal boundary and that return success before acknowledgement.
"""
from __future__ import annotations

import threading
from datetime import timedelta

import pytest

from runtime.remote_access import identity
from runtime.remote_access.authorization import AuthorizationVerifier
from runtime.remote_access.credentials import StaticDaemonCredentialProvider
from runtime.remote_access.forwarding import ForwardingHarness, _HarnessStreamHandle
from runtime.remote_access.gateway import ConnectorGateway, GatewayContext
from runtime.remote_access.revocation import RevocationCoordinator
from runtime.remote_access.stripping import CredentialScanner
from runtime.remote_access.streams import StreamClosed, StreamRegistry, _TrackedStream

from .conftest import (
    NOW,
    build_consumer,
    default_authorization_state,
    default_identity,
    load_fixture,
    make_request,
)

BEARER = "daemon-bearer-test-token-42"


# ── handle doubles ───────────────────────────────────────────────────────

class _LiveHandle:
    """A simple live transport handle that records close attempts."""

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
    """A handle whose close() blocks ONCE on a test-controlled event, then
    returns normally. Later close attempts return immediately."""

    def __init__(self, stream_id: str, entered: threading.Event, release: threading.Event) -> None:
        self.stream_id = stream_id
        self._entered = entered
        self._release = release
        self._closed = False

    def receive(self) -> bytes | None:
        if self._closed:
            raise StreamClosed(self.stream_id)
        return b"still-live"

    def close(self) -> None:
        if self._closed:
            return
        self._entered.set()
        assert self._release.wait(timeout=15), "harness: replacement close never released"
        self._closed = True

    @property
    def closed(self) -> bool:
        return self._closed


class _ExplodingCloseHandle:
    """A handle whose close() raises (used on fail-closed admission paths)."""

    def __init__(self, stream_id: str) -> None:
        self.stream_id = stream_id

    def receive(self) -> bytes | None:
        return None

    def close(self) -> None:
        raise OSError("transport close exploded on an unexposed handle")

    @property
    def closed(self) -> bool:
        return False


class _HostileHandle:
    """A handle whose close() raises a hostile secret-bearing exception."""

    def __init__(self, stream_id: str) -> None:
        self.stream_id = stream_id

    def receive(self) -> bytes | None:
        return None

    def close(self) -> None:
        raise RuntimeError(f"Bearer {BEARER} tenant-b home-42 boom")

    @property
    def closed(self) -> bool:
        return False


# ── deterministic race driver ────────────────────────────────────────────

def _pause_wrapper_construction(monkeypatch, entered: threading.Event, release: threading.Event) -> None:
    """Pause every ``_TrackedStream`` construction on a barrier.

    At the vulnerable seam this is exactly the window between the admission's
    sealed-flag read and its registration (TASK-5874). Under the redesigned
    ownership boundary the wrapper is constructed OUTSIDE the lifecycle lock,
    so the pause only proves that close_all can complete while an admission
    is mid-flight — the admission must still fail closed afterwards.
    """
    original_init = _TrackedStream.__init__

    def paused_init(self, stream_id, inner, registry):
        entered.set()
        assert release.wait(timeout=15), "harness: admission never released"
        original_init(self, stream_id, inner, registry)

    monkeypatch.setattr(_TrackedStream, "__init__", paused_init)


def _admission_thread(registry: StreamRegistry, stream_id: str, handle, outcome: dict) -> threading.Thread:
    def run() -> None:
        try:
            wrapper = registry.open(stream_id, handle)
            outcome["wrapper"] = wrapper
        except StreamClosed:
            outcome["wrapper"] = None

    return threading.Thread(target=run)


def _assert_no_usable_wrapper(outcome: dict, handle, *, tag: str) -> None:
    """Postcondition: a post-seal admission never returns a usable wrapper.
    Either it was rejected outright, or it registered before the seal and is
    therefore irrevocably sealed by the revocation snapshot."""
    wrapper = outcome["wrapper"]
    assert wrapper is None or wrapper.closed is True, (
        f"{tag}: post-seal admission returned a USABLE (unsealed) wrapper: {outcome}"
    )
    if wrapper is not None:
        with pytest.raises(StreamClosed):
            wrapper.receive()
    assert handle.closed is True, (
        f"{tag}: allocated transport must be closed by the fail-closed admission "
        f"(registry owns the handle once it is passed to open)"
    )


def _gateway_context(registry: StreamRegistry, state) -> GatewayContext:
    """A real pipeline context (streaming forwarder) for the gateway probe."""
    class _Streaming(ForwardingHarness):
        def open_stream(self, method, path, query, headers, body, bearer, stream_id):
            self.streams.append(stream_id)
            return _HarnessStreamHandle(stream_id)

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
        forwarder=_Streaming(),
        stream_registry=registry,
        scanner=CredentialScanner(),
        now=NOW(),
    )


# ── TASK-5874 exact probe: admission racing the seal ─────────────────────

def test_reviewer_exact_probe_admission_racing_seal_never_returns_usable(monkeypatch) -> None:
    """Reproduce TASK-5874's exact race at the registry public seam: an
    admission reads the sealed flag, pauses, close_all seals + snapshots +
    cleans up + publishes a SUCCESSFUL terminal result, then the admission
    must NOT insert and return a new unsealed stream."""
    registry = StreamRegistry()
    entered = threading.Event()
    release = threading.Event()
    _pause_wrapper_construction(monkeypatch, entered, release)

    handle = _LiveHandle("s-race")
    outcome: dict = {}
    thread = _admission_thread(registry, "s-race", handle, outcome)
    try:
        thread.start()
        assert entered.wait(timeout=15), "admission must reach the paused seam"
        registry.close_all()  # completes fully: seal -> snapshot -> cleanup -> publish
    finally:
        release.set()
    thread.join(timeout=15)
    assert not thread.is_alive(), "admission thread did not resolve"

    # The old broken outcome was: a returned unsealed wrapper that still
    # served bytes after close_all returned. The ownership boundary makes it
    # impossible.
    _assert_no_usable_wrapper(outcome, handle, tag="registry-seam race")
    assert registry.is_open("s-race") is False


def test_gateway_production_call_site_admission_racing_revocation(monkeypatch) -> None:
    """TASK-5874's finding named gateway.py ``_forward`` (registry.open at the
    production call site) as the affected surface. Drive the REAL pipeline:
    transport allocation completes, the admission pauses at the vulnerable
    seam, the authoritative revocation transaction runs to completion, then
    the admission must not surface a live stream to the client."""
    state = default_authorization_state()
    registry = StreamRegistry()
    ctx = _gateway_context(registry, state)
    entered = threading.Event()
    release = threading.Event()
    _pause_wrapper_construction(monkeypatch, entered, release)

    coordinator = RevocationCoordinator(state, registry)
    outcomes: dict = {}

    def run_admission() -> None:
        outcomes["decision"] = ConnectorGateway().decide(
            make_request(
                "GET",
                "/api/v1/orgs/acme/threads/T-1/tail",
                headers=[("accept", "text/event-stream")],
                stream_type="sse",
            ),
            ctx,
        )

    admission_thread = threading.Thread(target=run_admission)
    try:
        admission_thread.start()
        assert entered.wait(timeout=15), "gateway admission must reach the paused seam"
        coordinator.revoke(epoch=2)  # completes fully while the admission is mid-flight
    finally:
        release.set()
    admission_thread.join(timeout=15)
    assert not admission_thread.is_alive(), "gateway admission did not resolve"

    decision = outcomes["decision"]
    # Postcondition: after the revocation transaction completed, the admission
    # either failed closed (revocation_stream_closed) or, if it registered
    # before the seal, its stream is irrevocably sealed by the snapshot.
    if decision.allowed:
        assert decision.stream is not None
        assert decision.stream.closed is True, (
            "gateway returned a LIVE stream after revocation succeeded"
        )
        with pytest.raises(StreamClosed):
            decision.stream.receive()
    else:
        assert decision.denied is not None
        assert decision.denied.audit_category == "revocation_stream_closed"
    assert state.revocation_epoch == 2
    assert not registry._streams, "revocation must leave no registered stream"


def test_transport_allocation_after_seal_fails_closed_and_closes_handle() -> None:
    """A transport handle that finishes allocating AFTER the seal is closed by
    the fail-closed admission and never returned — the registry owns the
    handle once it is passed to open()."""
    registry = StreamRegistry()
    registry.close_all()  # seal + publish before the admission is attempted
    handle = _LiveHandle("late")
    with pytest.raises(StreamClosed):
        registry.open("late", handle)
    assert handle.closed is True, "allocated transport must be closed (not leaked)"
    assert handle.close_count == 1, "the registry owns exactly one fail-closed close"
    assert registry.is_open("late") is False


# ── mirror ordering: registration BEFORE the seal ────────────────────────

def test_mirror_ordering_registration_before_seal_included_in_revocation() -> None:
    """Mirror ordering: an admission that FULLY REGISTERS before the seal is
    included in the revocation snapshot — its wrapper is irrevocably sealed
    and its physical cleanup is acknowledged before close_all success."""
    registry = StreamRegistry()
    handle = _LiveHandle("s1")
    wrapper = registry.open("s1", handle)  # fully registered BEFORE the seal
    registry.close_all()  # snapshot must include s1; cleanup acknowledged
    assert handle.closed is True, "pre-seal registration's physical cleanup must be acknowledged"
    assert wrapper.closed is True, "pre-seal wrapper must be irrevocably sealed"
    with pytest.raises(StreamClosed):
        wrapper.receive()
    assert registry.is_open("s1") is False


# ── concurrent admissions racing the seal ────────────────────────────────

def test_concurrent_admissions_racing_seal_every_surface_closed_or_denied(monkeypatch) -> None:
    """Four concurrent admissions race the seal: every one either registered
    before the seal (and is closed by the revocation snapshot) or fails
    closed. None may return a usable wrapper."""
    registry = StreamRegistry()
    entered = threading.Event()
    release = threading.Event()
    _pause_wrapper_construction(monkeypatch, entered, release)

    outcomes: dict = {}
    handles: dict = {}
    threads: list[threading.Thread] = []
    for idx in range(4):
        stream_id = f"race-{idx}"
        handle = _LiveHandle(stream_id)
        handles[stream_id] = handle
        outcomes[stream_id] = {}
        threads.append(_admission_thread(registry, stream_id, handle, outcomes[stream_id]))
    try:
        for thread in threads:
            thread.start()
        assert entered.wait(timeout=15), "admissions must reach the paused seam"
        registry.close_all()
    finally:
        release.set()
    for thread in threads:
        thread.join(timeout=15)
        assert not thread.is_alive(), "admission thread did not resolve"

    for stream_id, handle in handles.items():
        _assert_no_usable_wrapper(outcomes[stream_id], handle, tag=f"concurrent {stream_id}")
        assert registry.is_open(stream_id) is False


# ── duplicate-id replacement: transport close outside the lock ───────────

def test_duplicate_id_replacement_close_runs_outside_lifecycle_lock() -> None:
    """Duplicate-id replacement: the old wrapper is sealed and its transport
    close runs OUTSIDE the lifecycle lock but INSIDE the revocation
    acknowledgement barrier. While the replacement close is blocked, a
    concurrent close_all must still take the lock and seal (no lock-across-
    callback deadlock) but must NOT publish success until the replacement
    transport close is terminal."""
    registry = StreamRegistry()
    entered = threading.Event()
    release = threading.Event()
    old_handle = _BlockingOnceHandle("s", entered, release)
    registry.open("s", old_handle)

    replacement_outcome: dict = {}
    replacer = threading.Thread(
        target=lambda: replacement_outcome.setdefault(
            "wrapper", registry.open("s", _LiveHandle("s"))
        )
    )
    done = threading.Event()

    def closer() -> None:
        try:
            registry.close_all()
        finally:
            done.set()

    closer_thread = threading.Thread(target=closer)
    try:
        replacer.start()
        assert entered.wait(timeout=15), "replacement transport close must be invoked"
        # The replacement close is running (outside the lock). A concurrent
        # close_all must still take the lifecycle lock and complete its seal
        # section — it would deadlock if the replacement close executed under
        # the lifecycle lock.
        closer_thread.start()
        # The acknowledgement barrier holds: close_all must NOT publish
        # success while the pre-seal replacement transport close is in flight.
        assert done.wait(timeout=0.5) is False, (
            "close_all published success while the replacement transport close "
            "was in flight (acknowledgement barrier bypassed)"
        )
        assert registry._revoked is True, (
            "close_all's seal must complete (no lock-across-callback deadlock)"
        )
    finally:
        release.set()
    replacer.join(timeout=15)
    closer_thread.join(timeout=15)
    assert not replacer.is_alive() and not closer_thread.is_alive()
    assert done.is_set(), "close_all must complete after the barrier is acknowledged"

    wrapper = replacement_outcome["wrapper"]
    assert wrapper is not None
    assert old_handle.closed is True, "replaced wrapper's transport must be closed"
    assert wrapper.closed is True, "post-seal replacement wrapper must be sealed"
    with pytest.raises(StreamClosed):
        wrapper.receive()
    assert registry.is_open("s") is False


# ── reentrancy: open from inside a transport-close callback ──────────────

def test_open_from_transport_close_callback_fails_closed_no_deadlock() -> None:
    """A transport-close callback (running outside the lifecycle lock during
    close_all) that attempts a NEW admission must fail closed: the allocated
    handle is closed by the registry, StreamClosed is raised inside the
    callback, and the outer cleanup completes normally — no deadlock, no
    usable stream."""
    registry = StreamRegistry()
    events: list[str] = []
    late_handle = _LiveHandle("late")

    class _CallbackClose:
        stream_id = "s1"

        def receive(self) -> bytes | None:
            return None

        def close(self) -> None:
            try:
                registry.open("late", late_handle)
                events.append("opened")  # must never happen
            except StreamClosed:
                events.append("denied")

        @property
        def closed(self) -> bool:
            return False

    registry.open("s1", _CallbackClose())
    registry.close_all()
    assert events == ["denied"], f"callback admission outcome: {events}"
    assert late_handle.closed is True, "fail-closed admission must close the allocated handle"
    assert registry.is_open("late") is False


# ── fail-closed admission robustness ─────────────────────────────────────

def test_fail_closed_admission_swallows_allocated_handle_close_failure() -> None:
    """The fail-closed admission closes the allocated handle; if that close
    raises, the admission still raises StreamClosed (the close failure of an
    unexposed handle never escapes as a different error)."""
    registry = StreamRegistry()
    registry.close_all()
    with pytest.raises(StreamClosed):
        registry.open("late", _ExplodingCloseHandle("late"))


def test_open_error_never_embeds_hostile_exception_text() -> None:
    """The StreamClosed raised by a fail-closed admission never embeds the
    allocated handle's hostile exception text (stable redacted failure)."""
    registry = StreamRegistry()
    registry.close_all()
    with pytest.raises(StreamClosed) as excinfo:
        registry.open("late", _HostileHandle("late"))
    text = str(excinfo.value)
    for secret in (BEARER, "tenant-b", "home-42", "boom"):
        assert secret not in text


def test_fail_closed_admission_closes_handle_exactly_once() -> None:
    """Ownership postcondition: a handle passed to open() on a sealed registry
    is closed exactly once by the registry — never double-closed by the
    caller boundary."""
    registry = StreamRegistry()
    registry.close_all()
    handle = _LiveHandle("late")
    with pytest.raises(StreamClosed):
        registry.open("late", handle)
    assert handle.close_count == 1


# ── checked-in mutation proofs ───────────────────────────────────────────

def test_mutation_unlocked_admission_boundary_is_detected(monkeypatch) -> None:
    """Mutation: remove the atomic ownership boundary — the sealed check and
    the registration move OUTSIDE the lifecycle lock (the exact TASK-5874
    anti-pattern). The barrier race battery must go red."""
    def invariant() -> None:
        registry = StreamRegistry()
        entered = threading.Event()
        release = threading.Event()
        _pause_wrapper_construction(monkeypatch, entered, release)
        handle = _LiveHandle("s-race")
        outcome: dict = {}
        thread = _admission_thread(registry, "s-race", handle, outcome)
        try:
            thread.start()
            assert entered.wait(timeout=15), "admission must reach the paused seam"
            registry.close_all()
        finally:
            release.set()
        thread.join(timeout=15)
        assert not thread.is_alive(), "admission thread did not resolve"
        _assert_no_usable_wrapper(outcome, handle, tag="mutation-unlocked-boundary")

    invariant()  # guard present: the ownership boundary holds

    original = StreamRegistry.open

    def _unlocked_open(self, stream_id, handle):
        # TASK-5874 anti-pattern: sealed check and registration OUTSIDE the
        # single lifecycle lock — admission and seal are NOT one atomic
        # ownership boundary.
        if self._revoked:
            raise StreamClosed(stream_id)
        previous = self._streams.get(stream_id)
        if previous is not None and previous._inner is not handle:
            previous.close()
        tracked = _TrackedStream(stream_id, handle, self)
        self._streams[stream_id] = tracked
        return tracked

    try:
        StreamRegistry.open = _unlocked_open  # type: ignore[method-assign]
        with pytest.raises(AssertionError):
            invariant()
    finally:
        StreamRegistry.open = original


def test_mutation_success_returned_before_acknowledgement_is_detected() -> None:
    """Mutation: open() returns success (the wrapper) BEFORE the registration
    acknowledgement. The successful admission must be registered under the
    ownership boundary, so revocation's snapshot can close it — returning
    first leaves a live unsealed stream outside the snapshot."""
    def invariant(registry: StreamRegistry) -> None:
        handle = _LiveHandle("s1")
        wrapper = registry.open("s1", handle)
        assert registry.is_open("s1") is True, (
            "successful admission must be acknowledged (registered) before success returns"
        )
        registry.close_all()
        assert wrapper.closed is True and handle.closed is True

    invariant(StreamRegistry())  # guard present

    original = StreamRegistry.open

    def _return_before_ack(self, stream_id, handle):
        tracked = _TrackedStream(stream_id, handle, self)
        if self._revoked:
            raise StreamClosed(stream_id)
        return tracked  # SUCCESS returned before registration/acknowledgement

    try:
        StreamRegistry.open = _return_before_ack  # type: ignore[method-assign]
        with pytest.raises(AssertionError):
            invariant(StreamRegistry())
    finally:
        StreamRegistry.open = original
