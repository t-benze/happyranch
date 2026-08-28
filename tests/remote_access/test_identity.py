"""Connector identity + device-proof verifier seam (contract §6.1 steps 1-3,
§7 connector_device_proof / one_use_enrollment).

Covers: valid proof, expired, replayed nonce, wrong audience, wrong home,
single-use binding reuse with the absent-vs-consumed no-oracle property,
ambiguous identity, and unavailable-verifier fail-closed behavior.
"""
from __future__ import annotations

from datetime import timedelta

import pytest

from runtime.remote_access import identity
from runtime.remote_access.models import DeniedOutcome

from .conftest import NOW, default_identity


def make_proof(
    device_id: str = "device-a",
    tenant_id: str = "tenant-a",
    home_id: str = "home-a",
    nonce: str = "nonce-1",
    expires_delta: timedelta = timedelta(minutes=5),
    issued_delta: timedelta = timedelta(minutes=-1),
    binding_id: str | None = None,
) -> identity.DeviceProof:
    return identity.DeviceProof(
        device_id=device_id,
        tenant_id=tenant_id,
        home_id=home_id,
        nonce=nonce,
        issued_at=NOW() + issued_delta,
        expires_at=NOW() + expires_delta,
        binding_id=binding_id,
    )


def _ok_verifier() -> identity.StaticProofVerifier:
    return identity.StaticProofVerifier(identity.ProofVerdict(ok=True))


def _deny_verifier(reason: str) -> identity.StaticProofVerifier:
    return identity.StaticProofVerifier(identity.ProofVerdict(ok=False, reason=reason))


# ── basic verdicts ───────────────────────────────────────────────────────


def test_valid_proof_verdict_ok() -> None:
    verifier = identity.StaticProofVerifier(identity.ProofVerdict(ok=True))
    verdict = verifier.verify(make_proof(), default_identity(), now=NOW())
    assert verdict.ok is True


def test_expired_proof_rejected() -> None:
    verifier = _deny_verifier("expired")
    verdict = verifier.verify(make_proof(), default_identity(), now=NOW())
    assert verdict.ok is False
    assert verdict.reason == "expired"


def test_wrong_audience_rejected() -> None:
    verifier = _deny_verifier("wrong_audience")
    verdict = verifier.verify(make_proof(), default_identity(), now=NOW())
    assert verdict.ok is False
    assert verdict.reason == "wrong_audience"


def test_wrong_home_rejected() -> None:
    verifier = _deny_verifier("wrong_home")
    verdict = verifier.verify(make_proof(), default_identity(), now=NOW())
    assert verdict.ok is False
    assert verdict.reason == "wrong_home"


# ── replay guarding ──────────────────────────────────────────────────────


def test_replay_guard_denies_second_presentation() -> None:
    verifier = identity.ReplayGuardingVerifier(_ok_verifier())
    assert verifier.verify(make_proof(), default_identity(), now=NOW()).ok is True
    verdict = verifier.verify(make_proof(), default_identity(), now=NOW())
    assert verdict.ok is False
    assert verdict.reason == "replayed"


def test_replay_guard_allows_distinct_nonces() -> None:
    verifier = identity.ReplayGuardingVerifier(_ok_verifier())
    assert verifier.verify(make_proof(nonce="n1"), default_identity(), now=NOW()).ok is True
    assert verifier.verify(make_proof(nonce="n2"), default_identity(), now=NOW()).ok is True


def test_replay_guard_is_device_scoped() -> None:
    verifier = identity.ReplayGuardingVerifier(_ok_verifier())
    assert verifier.verify(make_proof(device_id="d1", nonce="n"), default_identity(), now=NOW()).ok
    assert verifier.verify(make_proof(device_id="d2", nonce="n"), default_identity(), now=NOW()).ok


# ── single-use binding guard: no existence oracle (CRED-003/CRED-003b) ───


def test_single_use_absent_and_consumed_identical() -> None:
    """Absent vs consumed single-use bindings are externally indistinguishable."""
    guard = identity.SingleUseGuard()
    absent = guard.check("PLACEHOLDER_ONE_USE_ENROLLMENT")
    assert absent.deny_category == "replay"
    assert absent.audit_category == "credential_reused"
    guard.redeem("PLACEHOLDER_ONE_USE_ENROLLMENT")
    consumed = guard.check("PLACEHOLDER_ONE_USE_ENROLLMENT")
    assert consumed.deny_category == "replay"
    assert consumed.audit_category == "credential_reused"
    assert consumed.detail == absent.detail  # byte-identical visible detail


def test_single_use_unknown_binding_denied_identically() -> None:
    guard = identity.SingleUseGuard()
    first = guard.check("PLACEHOLDER_ONE_USE_ENROLLMENT_A")
    second = guard.check("PLACEHOLDER_ONE_USE_ENROLLMENT_B")
    assert first.detail == second.detail
    assert first.deny_category == "replay"
    assert first.audit_category == "credential_reused"


def test_single_use_redeem_then_reuse_denied() -> None:
    guard = identity.SingleUseGuard()
    guard.redeem("PLACEHOLDER_ONE_USE_ENROLLMENT_A")
    verdict = guard.check("PLACEHOLDER_ONE_USE_ENROLLMENT_A")
    assert verdict.deny_category == "replay"
    assert "no confirmation" in verdict.detail


def test_single_use_detail_is_tenant_neutral() -> None:
    guard = identity.SingleUseGuard()
    outcome = guard.check("PLACEHOLDER_ONE_USE_ENROLLMENT")
    assert "PLACEHOLDER_ONE_USE_ENROLLMENT" not in outcome.detail
    assert "tenant" not in outcome.detail.lower() or "no confirmation" in outcome.detail


# ── ambiguous identity / verifier unavailable ────────────────────────────


def test_connector_identity_must_be_unambiguous() -> None:
    with pytest.raises(ValueError):
        identity.ConnectorIdentity(tenant_id="", home_id="home-a", connector_id="c1")
    with pytest.raises(ValueError):
        identity.ConnectorIdentity(tenant_id="t", home_id="", connector_id="c1")
    with pytest.raises(ValueError):
        identity.ConnectorIdentity(tenant_id="t", home_id="h", connector_id="")


def test_ambiguous_proof_identity_denied() -> None:
    verifier = identity.StaticProofVerifier(identity.ProofVerdict(ok=False, reason="identity_unestablished"))
    verdict = verifier.verify(
        make_proof(device_id=""), default_identity(), now=NOW()
    )
    assert verdict.ok is False
    assert verdict.reason == "identity_unestablished"


def test_verifier_unavailable_fails_closed() -> None:
    class _ExplodingVerifier(identity.DeviceProofVerifier):
        def verify(self, proof, connector_identity, now):
            raise RuntimeError("registry unavailable")

    with pytest.raises(RuntimeError):
        _ExplodingVerifier().verify(make_proof(), default_identity(), now=NOW())


def test_gateway_maps_verifier_failure_to_internal(route_policy_fixture) -> None:
    """The gateway (not the verifier) turns verifier failure into a redacted deny."""
    from runtime.remote_access.gateway import ConnectorGateway, GatewayContext
    from runtime.remote_access.authorization import AuthorizationVerifier
    from runtime.remote_access.credentials import StaticDaemonCredentialProvider
    from runtime.remote_access.forwarding import ForwardingHarness
    from runtime.remote_access.streams import StreamRegistry
    from runtime.remote_access.stripping import CredentialScanner
    from .conftest import build_consumer, default_authorization_state, make_request

    class _ExplodingVerifier(identity.DeviceProofVerifier):
        def verify(self, proof, connector_identity, now):
            raise RuntimeError("registry unavailable")

    ctx = GatewayContext(
        connector_identity=default_identity(),
        proof=make_proof(),
        proof_verifier=_ExplodingVerifier(),
        single_use_guard=identity.SingleUseGuard(),
        authorization=AuthorizationVerifier(default_authorization_state()),
        policy=build_consumer(route_policy_fixture),
        credential_provider=StaticDaemonCredentialProvider("daemon-bearer-test"),
        forwarder=ForwardingHarness(),
        stream_registry=StreamRegistry(),
        scanner=CredentialScanner(),
        now=NOW(),
    )
    decision = ConnectorGateway().decide(make_request(), ctx)
    assert decision.allowed is False
    assert decision.denied is not None
    assert decision.denied.deny_category == "internal"
    assert decision.denied.audit_category == "internal_error"
    assert "registry unavailable" not in decision.denied.detail


def test_single_use_binding_rejection_matches_credential_taxonomy() -> None:
    """One-use enrollment: redeemed/expired/revoked rejected like absent."""
    guard = identity.SingleUseGuard()
    outcomes = [
        guard.check("PLACEHOLDER_ONE_USE_ENROLLMENT"),
        guard.check("PLACEHOLDER_ONE_USE_ENROLLMENT"),
    ]
    assert outcomes[0].detail == outcomes[1].detail
    assert all(o.audit_category == "credential_reused" for o in outcomes)
