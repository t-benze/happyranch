"""No daemon bearer in remote input/output, logs, diagnostics, exceptions,
process arguments, fixtures, or any non-loopback hop (fixed invariant #5).

Scans every gateway-emitted detail, audit record, exception surface, and the
outbound request graph for the bearer and bearer-shaped material.
"""
from __future__ import annotations

from datetime import timedelta

import pytest

from runtime.remote_access import identity
from runtime.remote_access.audit import AuditRecord, redact_exception, scan_secret_shapes
from runtime.remote_access.credentials import CredentialUnavailable
from runtime.remote_access.gateway import ConnectorGateway
from runtime.remote_access.stripping import CredentialScanner

from .conftest import NOW, build_consumer, default_authorization_state, default_identity, make_request

BEARER = "daemon-bearer-test-token-42"


def _make_gateway(route_policy_fixture, *, provider_bearer: str = BEARER):
    from runtime.remote_access.authorization import AuthorizationVerifier
    from runtime.remote_access.credentials import StaticDaemonCredentialProvider
    from runtime.remote_access.forwarding import ForwardingHarness
    from runtime.remote_access.gateway import GatewayContext
    from runtime.remote_access.streams import StreamRegistry

    ctx = GatewayContext(
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
        credential_provider=StaticDaemonCredentialProvider(provider_bearer),
        forwarder=ForwardingHarness(),
        stream_registry=StreamRegistry(),
        scanner=CredentialScanner(),
        now=NOW(),
    )
    return ConnectorGateway(), ctx


def test_allowed_decision_never_contains_bearer(route_policy_fixture) -> None:
    gateway, ctx = _make_gateway(route_policy_fixture)
    decision = gateway.decide(make_request("GET", "/api/v1/health"), ctx)
    assert decision.allowed is True
    blob = " ".join([decision.audit_category, decision.audit_detail, str(decision.response)])
    assert BEARER not in blob


def test_denied_decisions_never_contain_bearer(route_policy_fixture) -> None:
    gateway, ctx = _make_gateway(route_policy_fixture)
    requests = [
        make_request("GET", "/api/v1/orgs/%2e%2e/admin"),
        make_request("DELETE", "/api/v1/health"),
        make_request("GET", "/api/v1/orgs/acme/nope"),
        make_request(headers=[("x-custom", "Bearer " + BEARER)]),
    ]
    for req in requests:
        decision = gateway.decide(req, ctx)
        assert decision.allowed is False
        assert decision.denied is not None
        blob = " ".join([decision.denied.detail, decision.audit_category, decision.audit_detail])
        assert BEARER not in blob
        assert "Bearer " not in blob


def test_exception_redaction_is_category_only(route_policy_fixture) -> None:
    raw = RuntimeError("token file /home/user/.happyranch/daemon.token contained daemon-bearer-test-token-42")
    outcome = redact_exception(raw)
    assert outcome.deny_category == "internal"
    assert outcome.audit_category == "internal_error"
    assert "daemon-bearer-test-token-42" not in outcome.detail
    assert "/home/user" not in outcome.detail
    assert "token file" not in outcome.detail


def test_audit_record_safe_serialization() -> None:
    record = AuditRecord(category="allowed_request", detail="allowed", deny=None)
    as_json = record.to_json()
    assert BEARER not in as_json
    assert "Bearer " not in as_json


def test_secret_shape_scanner_detects_bearer_values() -> None:
    assert scan_secret_shapes("Authorization: Bearer abc123") is True
    assert scan_secret_shapes("hrpair_somevalue") is True
    assert scan_secret_shapes("hrreg_somevalue") is True
    assert scan_secret_shapes("x=daemon-bearer-test-token-42", bearer=BEARER) is True
    assert scan_secret_shapes("plain benign text") is False


def test_credential_unavailable_message_not_echoed() -> None:
    exc = CredentialUnavailable("token file missing at /home/user/.happyranch")
    outcome = redact_exception(exc)
    assert "token file" not in outcome.detail
    assert "/home/user" not in outcome.detail


def test_forwarder_refuses_bearer_in_outbound(route_policy_fixture) -> None:
    """If remote input somehow survived stripping, the forwarder's outbound
    leak check must refuse — the bearer never appears on any hop."""
    from runtime.remote_access.forwarding import assert_no_credential_leak, OutboundLeakError
    from runtime.remote_access.models import Header

    with pytest.raises(OutboundLeakError):
        assert_no_credential_leak(
            "GET", "/api/v1/health", None,
            (Header("x-evil", BEARER),), None, BEARER,
        )


def test_denied_detail_for_bearer_shaped_input_is_static_prose(route_policy_fixture) -> None:
    gateway, ctx = _make_gateway(route_policy_fixture)
    decision = gateway.decide(
        make_request(headers=[("x-custom", f"Bearer {BEARER}")]), ctx
    )
    assert decision.denied is not None
    # The visible detail is category prose only — it never echoes the input.
    assert decision.denied.detail == (
        "Request rejected; credential-shaped material is never accepted in remote input."
    )
