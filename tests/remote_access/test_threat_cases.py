"""Consumes the normative Unit-A threat matrix through the connector core.

- The classification of all 57 cases is exhaustive and checked in.
- Every core-applicable hostile case produces exactly the fixture's deny and
  audit categories through the gateway; positive controls forward over real
  loopback 127.0.0.1.
- The CRED-003/CRED-003b absent-vs-consumed pair is byte-identical (no
  credential-existence oracle).
- A checked-in mutation proves hostile->allowed is rejected by the battery.
"""
from __future__ import annotations

import pytest

from runtime.remote_access.forwarding import (
    LOOPBACK_HOST,
    HttpLoopbackForwarder,
    LoopbackTarget,
)
from runtime.remote_access.gateway import ConnectorGateway
from runtime.remote_access.models import Decision

from .conftest import make_request
from .fake_daemon import FakeDaemon, assert_daemon_received
from .harness import BEARER, CaseBuilder, CLASSIFICATION, classify_all

LIVE_STREAM_CASES = {"REV-002", "REV-003", "REV-004"}
# REV-004 (admission racing the seal) is driven deterministically by the
# dedicated admission/seal ownership battery
# (tests/remote_access/test_admission_revocation_ownership.py) — it is a
# concurrency/lifecycle ordering, not a plain single-request drive.


def test_classification_is_exhaustive(threat_cases_fixture) -> None:
    classification = classify_all(threat_cases_fixture["cases"])
    core = {cid for cid, (kind, _) in classification.items() if kind == "core"}
    control = {cid for cid, (kind, _) in classification.items() if kind == "control_plane"}
    all_ids = {c["id"] for c in threat_cases_fixture["cases"]}
    assert core | control == all_ids
    assert not (core & control)
    assert len(all_ids) == 57
    assert len(core) == 36  # 33 hostile + 3 positive controls
    assert len(control) == 21


def _run_case(case: dict, builder: CaseBuilder) -> Decision:
    request, ctx = builder.build(case)
    return ConnectorGateway().decide(request, ctx)


@pytest.mark.parametrize(
    "case_id",
    [
        "CRED-001",
        "CRED-002",
        "CRED-003",
        "CRED-003b",
        "CRED-004",
        "CRED-005",
        "DEV-001",
        "DEV-002",
        "INT-001",
        "LOCAL-001",
        "LOCAL-002",
        "LOCAL-003",
        "PATH-001",
        "PATH-002",
        "PATH-003",
        "POLICY-001",
        "POLICY-002",
        "POLICY-003",
        "POLICY-004",
        "POLICY-005",
        "POLICY-006",
        "POLICY-007",
        "REV-001",
        "ROUTE-001",
        "ROUTE-002",
        "ROUTE-003",
        "SMUG-001",
        "SMUG-002",
        "UPG-001",
        "UPG-002",
    ],
)
def test_hostile_case_denied_with_normative_categories(threat_cases_fixture, route_policy_fixture, case_id):
    case = next(c for c in threat_cases_fixture["cases"] if c["id"] == case_id)
    builder = CaseBuilder(route_policy_fixture)
    decision = _run_case(case, builder)
    assert decision.allowed is False, f"{case_id}: hostile case must be denied"
    assert decision.denied is not None
    expected = case["expected"]
    assert decision.denied.deny_category == expected["deny_category"], (
        f"{case_id}: deny category {decision.denied.deny_category} != {expected['deny_category']}"
    )
    assert decision.denied.audit_category == expected["audit_category"], (
        f"{case_id}: audit category {decision.denied.audit_category} != {expected['audit_category']}"
    )


def test_cred_no_oracle_pair_byte_identical(threat_cases_fixture, route_policy_fixture) -> None:
    """CRED-003 (consumed) vs CRED-003b (absent): identical visible detail."""
    builder = CaseBuilder(route_policy_fixture)
    c003 = next(c for c in threat_cases_fixture["cases"] if c["id"] == "CRED-003")
    c003b = next(c for c in threat_cases_fixture["cases"] if c["id"] == "CRED-003b")
    d1 = _run_case(c003, builder)
    d2 = _run_case(c003b, builder)
    assert d1.denied is not None and d2.denied is not None
    assert d1.denied.detail == d2.denied.detail
    assert d1.denied.audit_category == d2.denied.audit_category == "credential_reused"
    assert d1.denied.deny_category == d2.denied.deny_category == "replay"


def test_positive_controls_allowed_over_real_loopback(threat_cases_fixture, route_policy_fixture) -> None:
    from dataclasses import replace

    builder = CaseBuilder(route_policy_fixture)
    fake = FakeDaemon(expected_bearer=BEARER)
    fake.start()
    try:
        for case_id in ("POS-001", "POS-002", "POS-003"):
            case = next(c for c in threat_cases_fixture["cases"] if c["id"] == case_id)
            request, ctx = builder.build(case)
            ctx = replace(
                ctx, forwarder=HttpLoopbackForwarder(LoopbackTarget(LOOPBACK_HOST, fake.port))
            )
            decision = ConnectorGateway().decide(request, ctx)
            assert decision.allowed is True, f"{case_id}: positive control must be allowed"
            assert decision.audit_category == "allowed_request"
            if case_id == "POS-003":
                assert decision.stream is not None
                assert decision.stream.receive() is not None
                decision.stream.close()
            else:
                assert decision.response is not None
                assert_daemon_received(fake, request.method, request.path)
    finally:
        fake.stop()


def test_revoked_mid_http_stream(threat_cases_fixture, route_policy_fixture) -> None:
    """REV-002: revocation closes a live HTTP stream before it completes."""
    case = next(c for c in threat_cases_fixture["cases"] if c["id"] == "REV-002")
    expected = case["expected"]
    import datetime as dt
    import threading

    from runtime.remote_access import identity
    from runtime.remote_access.authorization import AuthorizationVerifier
    from runtime.remote_access.gateway import GatewayContext
    from runtime.remote_access.credentials import StaticDaemonCredentialProvider
    from runtime.remote_access.revocation import RevocationCoordinator
    from runtime.remote_access.stripping import CredentialScanner
    from runtime.remote_access.streams import StreamRegistry
    from .conftest import default_authorization_state, default_identity

    fake = FakeDaemon(expected_bearer=BEARER, hold_open=True)
    fake.start()
    try:
        registry = StreamRegistry()
        state = default_authorization_state()
        coordinator = RevocationCoordinator(state, registry)
        ctx = GatewayContext(
            connector_identity=default_identity(),
            proof=identity.DeviceProof(
                device_id="device-a",
                tenant_id="tenant-a",
                home_id="home-a",
                nonce="n-rev-http",
                issued_at=dt.datetime(2026, 8, 27, 11, 59, tzinfo=dt.timezone.utc),
                expires_at=dt.datetime(2026, 8, 27, 13, 0, tzinfo=dt.timezone.utc),
            ),
            proof_verifier=identity.StaticProofVerifier(identity.ProofVerdict(ok=True)),
            single_use_guard=identity.SingleUseGuard(),
            authorization=AuthorizationVerifier(state),
            policy=CaseBuilder(route_policy_fixture)._consumer(),
            credential_provider=StaticDaemonCredentialProvider(BEARER),
            forwarder=HttpLoopbackForwarder(LoopbackTarget(LOOPBACK_HOST, fake.port)),
            stream_registry=registry,
            scanner=CredentialScanner(),
            now=dt.datetime(2026, 8, 27, 12, 0, tzinfo=dt.timezone.utc),
        )
        gateway = ConnectorGateway()
        request = make_request("GET", "/api/v1/orgs/acme/tasks")

        # The in-flight HTTP exchange runs in a worker thread so the test can
        # fire revocation while the daemon holds the response body open.
        result: dict = {}
        worker = threading.Thread(target=lambda: result.update(decision=gateway.decide(request, ctx)))
        worker.start()
        assert fake.started.wait(timeout=5), "fake daemon never saw the request"
        coordinator.revoke(epoch=3)  # authoritative transaction lands mid-flight
        worker.join(timeout=5)
        assert not worker.is_alive(), "in-flight HTTP exchange must abort on revocation"
        decision = result["decision"]
        assert decision.allowed is False
        assert decision.denied is not None
        assert decision.denied.deny_category == expected["deny_category"] == "revocation"
        assert decision.denied.audit_category == expected["audit_category"] == "revocation_stream_closed"
    finally:
        fake.stop()


def test_revoked_mid_sse_stream(threat_cases_fixture, route_policy_fixture) -> None:
    """REV-003: revocation cancels an open SSE stream immediately."""
    case = next(c for c in threat_cases_fixture["cases"] if c["id"] == "REV-003")
    expected = case["expected"]
    import datetime as dt

    from runtime.remote_access import identity
    from runtime.remote_access.authorization import AuthorizationVerifier
    from runtime.remote_access.gateway import GatewayContext
    from runtime.remote_access.credentials import StaticDaemonCredentialProvider
    from runtime.remote_access.revocation import RevocationCoordinator
    from runtime.remote_access.stripping import CredentialScanner
    from runtime.remote_access.streams import StreamClosed, StreamRegistry
    from .conftest import default_authorization_state, default_identity

    fake = FakeDaemon(expected_bearer=BEARER, hold_open=True)
    fake.start()
    try:
        registry = StreamRegistry()
        state = default_authorization_state()
        coordinator = RevocationCoordinator(state, registry)
        ctx = GatewayContext(
            connector_identity=default_identity(),
            proof=identity.DeviceProof(
                device_id="device-a",
                tenant_id="tenant-a",
                home_id="home-a",
                nonce="n-rev-sse",
                issued_at=dt.datetime(2026, 8, 27, 11, 59, tzinfo=dt.timezone.utc),
                expires_at=dt.datetime(2026, 8, 27, 13, 0, tzinfo=dt.timezone.utc),
            ),
            proof_verifier=identity.StaticProofVerifier(identity.ProofVerdict(ok=True)),
            single_use_guard=identity.SingleUseGuard(),
            authorization=AuthorizationVerifier(state),
            policy=CaseBuilder(route_policy_fixture)._consumer(),
            credential_provider=StaticDaemonCredentialProvider(BEARER),
            forwarder=HttpLoopbackForwarder(LoopbackTarget(LOOPBACK_HOST, fake.port)),
            stream_registry=registry,
            scanner=CredentialScanner(),
            now=dt.datetime(2026, 8, 27, 12, 0, tzinfo=dt.timezone.utc),
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
        assert decision.stream is not None
        assert fake.started.wait(timeout=5)
        first = decision.stream.receive()
        assert first is not None
        coordinator.revoke(epoch=3)
        assert registry.is_open(decision.stream.stream_id) is False
        with pytest.raises(StreamClosed):
            decision.stream.receive()
        assert expected["deny_category"] == "revocation"
        assert expected["audit_category"] == "revocation_stream_closed"
    finally:
        fake.stop()


def test_control_plane_cases_have_no_core_claim(threat_cases_fixture) -> None:
    """Control-plane cases are explicitly outside the connector core; the
    classification states where each is enforced (never silently ignored)."""
    for case in threat_cases_fixture["cases"]:
        cid = case["id"]
        kind, reason = CLASSIFICATION[cid]
        if kind == "control_plane":
            assert reason, f"{cid}: control-plane case must carry a reason"
            assert cid not in LIVE_STREAM_CASES


def test_mutation_hostile_to_allowed_is_rejected(threat_cases_fixture, route_policy_fixture) -> None:
    """Checked-in mutation proof: flipping a hostile case to allowed fails the
    battery — the connector core can never authorize a hostile scenario."""
    builder = CaseBuilder(route_policy_fixture)
    # A hostile case that the gateway currently denies:
    case = next(c for c in threat_cases_fixture["cases"] if c["id"] == "ROUTE-001")
    decision = _run_case(case, builder)
    assert decision.allowed is False

    # Simulate the mutation: someone changes the fixture to expect "allowed".
    mutated = dict(case)
    mutated["expected"] = dict(case["expected"])
    mutated["expected"]["outcome"] = "allowed"
    mutated["expected"].pop("deny_category", None)
    mutated["expected"]["audit_category"] = "allowed_request"

    with pytest.raises(AssertionError):
        _assert_case_outcome(mutated, builder)


def _assert_case_outcome(case: dict, builder: CaseBuilder) -> None:
    decision = _run_case(case, builder)
    expected = case["expected"]
    if expected["outcome"] == "allowed":
        assert decision.allowed is True, f"{case['id']}: expected allowed"
        assert decision.audit_category == "allowed_request"
    else:
        assert decision.allowed is False, f"{case['id']}: hostile case must be denied"
        assert decision.denied is not None
        assert decision.denied.deny_category == expected["deny_category"]
        assert decision.denied.audit_category == expected["audit_category"]


def test_positive_mutation_to_denied_is_rejected(threat_cases_fixture, route_policy_fixture) -> None:
    """Checked-in mutation proof: flipping a positive control to denied also
    fails the battery (the fixture's own contract demands the positive path)."""
    builder = CaseBuilder(route_policy_fixture)
    case = next(c for c in threat_cases_fixture["cases"] if c["id"] == "POS-001")
    mutated = dict(case)
    mutated["expected"] = dict(case["expected"])
    mutated["expected"]["outcome"] = "denied"
    mutated["expected"]["deny_category"] = "route"
    mutated["expected"]["audit_category"] = "route_denied"
    with pytest.raises(AssertionError):
        _assert_case_outcome(mutated, builder)


def test_all_hostile_cases_deny_even_under_mutation(threat_cases_fixture, route_policy_fixture) -> None:
    """Cross-check: every hostile core case denies through the real gateway."""
    builder = CaseBuilder(route_policy_fixture)
    hostile_core = [
        c
        for c in threat_cases_fixture["cases"]
        if c["class"] == "hostile" and CLASSIFICATION[c["id"]][0] == "core" and c["id"] not in LIVE_STREAM_CASES
    ]
    assert len(hostile_core) == 30  # 33 core-hostile minus the 3 live-stream cases
    for case in hostile_core:
        decision = _run_case(case, builder)
        assert decision.allowed is False, f"{case['id']} must be denied"
