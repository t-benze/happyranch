"""Checked-in regression battery replicating the exact TASK-5852 reviewer
probes
against the fix-forward head. Each probe must now close its finding."""
from __future__ import annotations

import datetime as dt
import json
from datetime import timedelta

import pytest

from runtime.remote_access import identity
from runtime.remote_access.authorization import AuthorizationVerifier, TrustState
from runtime.remote_access.credentials import StaticDaemonCredentialProvider
from runtime.remote_access.forwarding import LOOPBACK_HOST, HttpLoopbackForwarder, LoopbackTarget
from runtime.remote_access.gateway import ConnectorGateway, GatewayContext
from runtime.remote_access.policy import PolicyEnvelope, RoutePolicyConsumer
from runtime.remote_access.revocation import RevocationCoordinator, RevocationIncomplete
from runtime.remote_access.stripping import CredentialScanner
from runtime.remote_access.streams import StreamClosed, StreamRegistry

NOW = dt.datetime(2026, 8, 27, 12, 0, tzinfo=dt.timezone.utc)
BEARER = "daemon-bearer-test-token-42"
CONTRACT = "tests/contract/managed_remote_access/route-policy.json"

with open(CONTRACT) as fh:
    FIXTURE = json.load(fh)


def make_envelope(artifact, **kw):
    return PolicyEnvelope(
        schema_version=1,
        artifact=artifact,
        artifact_version=1,
        issued_at=NOW - timedelta(seconds=60),
        max_age_seconds=3600,
        revision=1,
        state="active",
        **kw,
    )


def consumer(artifact, **kw):
    return RoutePolicyConsumer.from_envelope(make_envelope(artifact, **kw), now=NOW)


def ctx(forwarder=None, registry=None, state=None):
    from .conftest import default_authorization_state, default_identity

    state = state or default_authorization_state()
    return GatewayContext(
        connector_identity=default_identity(),
        proof=identity.DeviceProof(
            device_id="device-a", tenant_id="tenant-a", home_id="home-a",
            nonce="n1", issued_at=NOW - timedelta(minutes=1),
            expires_at=NOW + timedelta(minutes=5),
        ),
        proof_verifier=identity.StaticProofVerifier(identity.ProofVerdict(ok=True)),
        single_use_guard=identity.SingleUseGuard(),
        authorization=AuthorizationVerifier(state),
        policy=consumer(FIXTURE),
        credential_provider=StaticDaemonCredentialProvider(BEARER),
        forwarder=forwarder,
        stream_registry=registry or StreamRegistry(),
        scanner=CredentialScanner(),
        now=NOW,
    )


def test_reviewer_probes() -> None:
    failures = []

    # ── Finding 1 (CRITICAL): revocation must close live streams ──────────
    from .fake_daemon import FakeDaemon
    from .conftest import default_authorization_state, make_request

    state = default_authorization_state()
    registry = StreamRegistry()
    fake = FakeDaemon(expected_bearer=BEARER, hold_open=True)
    fake.start()
    try:
        c = ctx(HttpLoopbackForwarder(LoopbackTarget(LOOPBACK_HOST, fake.port)), registry, state)
        decision = ConnectorGateway().decide(
            make_request("GET", "/api/v1/orgs/acme/threads/T-1/tail",
                         headers=[("accept", "text/event-stream")], stream_type="sse"),
            c,
        )
        assert decision.allowed and decision.stream is not None
        handle = decision.stream
        assert fake.started.wait(timeout=5)
        assert handle.receive() is not None
        # The old public bypass no longer exists.
        try:
            state.apply_revocation(2)
            failures.append("F1: old public apply_revocation still callable")
        except AttributeError:
            pass
        RevocationCoordinator(state, registry).revoke(epoch=2)
        if handle.closed is not True:
            failures.append("F1: stream not closed by revocation transaction")
        if registry.is_open(handle.stream_id):
            failures.append("F1: stream still registered after revocation")
        if state.revocation_epoch != 2:
            failures.append("F1: trust state not applied")
        try:
            handle.receive()
            failures.append("F1: closed stream still serves frames")
        except StreamClosed:
            pass
    finally:
        fake.stop()

    # ── Finding 2 (HIGH): locked decision order / nested values / states ──
    reversed_order = list(reversed(FIXTURE["decision_order"]))
    try:
        consumer({**FIXTURE, "decision_order": reversed_order})
        failures.append("F2: reversed decision_order accepted")
    except Exception as exc:
        if getattr(exc, "outcome", None) is None:
            failures.append(f"F2: non-PolicyError rejection: {type(exc).__name__}")

    for state_name in ("suspended", "active-ish", "revoked"):
        try:
            consumer(FIXTURE, state=state_name)
            failures.append(f"F2: unknown state {state_name!r} accepted")
        except Exception:
            pass

    for section, key in (
        ("normalization", "normalize_once"),
        ("header_stripping", "strip_authorization"),
        ("upgrade_semantics", "unsupported_upgrades_denied"),
    ):
        mutated = {**FIXTURE}
        mutated[section] = {**FIXTURE[section], key: False}
        try:
            consumer(mutated)
            failures.append(f"F2: nested flag {section}.{key}=False accepted")
        except Exception:
            pass

    # ── Finding 3 (HIGH): forward boundary normalization ──────────────────
    from .conftest import make_request

    refused = HttpLoopbackForwarder(LoopbackTarget(LOOPBACK_HOST, 1))
    decision = ConnectorGateway().decide(make_request(), ctx(refused))
    if decision.allowed:
        failures.append("F3: connection refused still allowed")
    if decision.denied is None or decision.denied.audit_category != "daemon_unavailable":
        failures.append(f"F3: wrong category {getattr(decision.denied, 'audit_category', None)}")
    if "ConnectionRefusedError" in (decision.denied.detail if decision.denied else "") or \
       "Errno" in (decision.denied.detail if decision.denied else ""):
        failures.append("F3: raw exception text leaked in detail")

    class _Hostile:
        target = type("T", (), {"host": LOOPBACK_HOST, "port": 0})()

        def open_stream(self, *a, **k):
            raise RuntimeError(f"Bearer {BEARER} tenant-b home-42 device-7 boom")

    decision = ConnectorGateway().decide(make_request(), ctx(_Hostile()))
    if decision.denied is None or decision.denied.audit_category != "internal_error":
        failures.append(f"F3: hostile exception category wrong: {getattr(decision.denied, 'audit_category', None)}")
    blob = (decision.denied.detail if decision.denied else "") + decision.audit_detail
    for secret in (BEARER, "tenant-b", "home-42", "device-7", "boom"):
        if secret in blob:
            failures.append(f"F3: secret leaked: {secret!r}")

    if failures:
        print("PROBE FAILURES:")
        for f in failures:
            print("  -", f)
        raise SystemExit(1)
    print("PROBE OK: all three TASK-5852 findings closed at the public seam")


def test_reviewer_critical_probe_close_failure_retained_handle() -> None:
    """TASK-5858 [CRITICAL] exact public-seam probe: when a transport
    ``close()`` raises, the coordinator reports incomplete as appropriate,
    state still denies, registry semantics are honest, and the retained
    ``handle.receive()`` cannot return still-live bytes.

    The retained handle must irrevocably reject receive/send — a failing
    underlying close is reported (RevocationIncomplete) but never leaves the
    externally retained handle readable, writable, or untracked.
    """
    from .conftest import default_authorization_state, make_request

    class _ExplodingStream:
        stream_id = "s-explode"

        def receive(self) -> bytes | None:
            return b"still-live"

        def close(self) -> None:
            raise OSError("transport close exploded")

        @property
        def closed(self) -> bool:
            return False

    class _ExplodingForwarder:
        target = type("T", (), {"host": LOOPBACK_HOST, "port": 0})()

        def open_stream(self, method, path, query, headers, body, bearer, stream_id):
            return _ExplodingStream()

    state = default_authorization_state()
    registry = StreamRegistry()
    c = ctx(_ExplodingForwarder(), registry, state)
    decision = ConnectorGateway().decide(
        make_request(
            "GET",
            "/api/v1/orgs/acme/threads/T-1/tail",
            headers=[("accept", "text/event-stream")],
            stream_type="sse",
        ),
        c,
    )
    assert decision.allowed and decision.stream is not None
    handle = decision.stream
    assert handle.receive() == b"still-live"  # live before revocation

    with pytest.raises(RevocationIncomplete) as excinfo:
        RevocationCoordinator(state, registry).revoke(epoch=2)
    assert excinfo.value.applied_epoch == 2
    assert excinfo.value.stream_ids == (handle.stream_id,)

    # State denies.
    assert state.revocation_epoch == 2
    # Registry semantics are honest: no longer tracked, sealed.
    assert registry.is_open(handle.stream_id) is False
    # The retained handle irrevocably rejects reads — no still-live bytes.
    assert handle.closed is True
    with pytest.raises(StreamClosed):
        handle.receive()


def test_reviewer_high_probe_contradictory_non_empty_percent_encoding() -> None:
    """TASK-5858 [HIGH] exact probe: a contradictory non-empty
    ``percent_encoding`` value must fail closed at load — non-empty prose is
    not a substitute for canonical semantic equality."""
    mutated = {**FIXTURE}
    mutated["normalization"] = {
        **FIXTURE["normalization"],
        "percent_encoding": "ALLOW invalid encoding",
    }
    with pytest.raises(Exception) as excinfo:  # noqa: BLE001 - probe
        consumer(mutated)
    assert getattr(excinfo.value, "outcome", None) is not None, (
        f"non-PolicyError rejection: {type(excinfo.value).__name__}"
    )
    assert excinfo.value.outcome.audit_category == "policy_malformed"


def test_reviewer_high_probe_racing_higher_epoch_loses_no_failure_evidence() -> None:
    """TASK-5867 [HIGH] exact public-seam probe: with a barrier-controlled
    blocked close, a racing higher-epoch revoke must NOT return success and
    the failed stream id stays observable. The old code produced
    ``[(1, ValueError, None), (2, return, 2)]`` with state epoch 2 and
    ``cleanup_failed=True`` — no caller reported ``RevocationIncomplete`` or
    the failed id. The corrected transaction serializes and shares/persists
    the cleanup terminal result across concurrent revocations."""
    import threading

    from runtime.remote_access.revocation import RevocationCoordinator, RevocationIncomplete
    from runtime.remote_access.streams import StreamRegistry

    from .conftest import default_authorization_state

    state = default_authorization_state()
    registry = StreamRegistry()
    coordinator = RevocationCoordinator(state, registry)
    release = threading.Event()
    entered = threading.Event()

    class _Blocking:
        stream_id = "s-race"

        def receive(self) -> bytes | None:
            return b"still-live"

        def close(self) -> None:
            entered.set()
            assert release.wait(timeout=15), "probe: close never released"
            raise OSError("transport close exploded after barrier")

        @property
        def closed(self) -> bool:
            return False

    registry.open("s-race", _Blocking())
    results: dict = {}

    def worker(tag: str, epoch: int) -> None:
        try:
            results[tag] = ("ok", coordinator.revoke(epoch))
        except RevocationIncomplete as exc:
            results[tag] = ("incomplete", exc.applied_epoch, exc.stream_ids)
        except ValueError:
            results[tag] = ("rollback",)

    t1 = threading.Thread(target=worker, args=("lower", 1))
    t2 = threading.Thread(target=worker, args=("higher", 2))
    t1.start()
    assert entered.wait(timeout=15), "lower revoke must reach the blocked close"
    t2.start()
    release.set()
    t1.join(timeout=15)
    t2.join(timeout=15)
    assert not t1.is_alive() and not t2.is_alive()
    # The old broken tuple [(1, ValueError, None), (2, return, 2)] must be
    # impossible: no caller returns success while a cleanup failure relevant
    # to the sealed generation is in flight or completed-but-unreported, and
    # the failed stream id / RevocationIncomplete stays observable.
    assert results["lower"][0] != "ok", f"lower returned success: {results}"
    assert results["higher"][0] != "ok", f"higher returned success: {results}"
    incomplete = [r for r in results.values() if r[0] == "incomplete"]
    assert incomplete and any("s-race" in r[2] for r in incomplete), (
        f"failed stream id / RevocationIncomplete lost: {results}"
    )
    assert state.revocation_epoch == 2
    assert registry.is_open("s-race") is False
    assert results["higher"][1] == 2  # type: ignore[index]
    assert results["higher"][2] == ("s-race",)  # type: ignore[index]


if __name__ == "__main__":
    main()
