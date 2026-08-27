"""Threat-case conformance harness: drives the connector core against the
normative Unit-A threat matrix (tests/contract/managed_remote_access/threat-cases.json).

Every case is classified either ``core`` (applicable to the portable connector
core and consumed through the gateway) or ``control_plane`` (network/cell-level
semantics owned by Headscale/Services/DERP — explicitly outside this unit, with
a reason). The classification is checked in and exhaustive over the fixture.
"""
from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

from runtime.remote_access import identity
from runtime.remote_access.authorization import AuthorizationVerifier, DeviceAuthorization
from runtime.remote_access.credentials import CredentialUnavailable, StaticDaemonCredentialProvider
from runtime.remote_access.forwarding import ForwardingHarness
from runtime.remote_access.gateway import ConnectorGateway, GatewayContext
from runtime.remote_access.policy import PolicyError, RoutePolicyConsumer
from runtime.remote_access.stripping import CredentialScanner
from runtime.remote_access.streams import StreamRegistry

from .conftest import (
    NOW,
    default_authorization_state,
    default_identity,
    make_policy_envelope,
    make_request,
)

BEARER = "daemon-bearer-test-token-42"

# Case id -> (applicability, reason)
CLASSIFICATION: dict[str, tuple[str, str]] = {
    # Positive controls — consumed through the real loopback forwarder.
    "POS-001": ("core", "allowed HTTP control"),
    "POS-002": ("core", "allowed template HTTP control"),
    "POS-003": ("core", "allowed SSE upgrade control"),
    # Proof / credential cases — consumed at the authenticate + proof steps.
    "CRED-001": ("core", "expired proof window"),
    "CRED-002": ("core", "replayed proof nonce"),
    "CRED-003": ("core", "single-use binding consumed (no-oracle)"),
    "CRED-003b": ("core", "single-use binding absent (no-oracle)"),
    "CRED-004": ("core", "wrong-audience proof"),
    "CRED-005": ("core", "wrong-home proof"),
    # Device / pairing cases — consumed at the bind step.
    "DEV-001": ("core", "stale device identity (current-device check)"),
    "DEV-002": ("core", "pairing bound to different home/device"),
    # Internal redaction.
    "INT-001": ("core", "internal failure redacted to category"),
    # Local daemon boundary.
    "LOCAL-001": ("core", "bearer-shaped material in remote input"),
    "LOCAL-002": ("core", "daemon unavailable (token unreadable)"),
    "LOCAL-003": ("core", "daemon bind mismatch (non-loopback target)"),
    # Path normalization.
    "PATH-001": ("core", "percent-encoded traversal"),
    "PATH-002": ("core", "dot-segment traversal"),
    "PATH-003": ("core", "absolute-form request target"),
    # Policy fail-closed states.
    "POLICY-001": ("core", "empty policy"),
    "POLICY-002": ("core", "malformed policy artifact"),
    "POLICY-003": ("core", "stale policy"),
    "POLICY-004": ("core", "future policy"),
    "POLICY-005": ("core", "policy revision rollback"),
    "POLICY-006": ("core", "policy compile failure"),
    "POLICY-007": ("core", "policy apply failure"),
    # Revocation.
    "REV-001": ("core", "revoked before request"),
    "REV-002": ("core", "revoked mid HTTP stream"),
    "REV-003": ("core", "revoked mid SSE stream"),
    # Route / method / upgrade semantics.
    "ROUTE-001": ("core", "forbidden agent-callback route"),
    "ROUTE-002": ("core", "method not allowed on surface"),
    "ROUTE-003": ("core", "unclassified route denied by default"),
    "SMUG-001": ("core", "conflicting Content-Length/Transfer-Encoding"),
    "SMUG-002": ("core", "duplicate critical headers"),
    "UPG-001": ("core", "unsupported WebSocket upgrade"),
    "UPG-002": ("core", "unsupported body on SSE surface"),
    # Control-plane / network-level semantics — explicitly NOT connector-core.
    "CROSS-001": ("control_plane", "Headscale enrollment endpoint redemption"),
    "CROSS-002": ("control_plane", "Headscale enrollment endpoint redemption"),
    "CROSS-003": ("control_plane", "Headscale node registration identity"),
    "CROSS-004": ("control_plane", "Headscale enrollment key binding"),
    "CROSS-005": ("control_plane", "Services account entitlement"),
    "CROSS-005b": ("control_plane", "Services account entitlement"),
    "CROSS-006": ("control_plane", "Services home resolution"),
    "CROSS-007": ("control_plane", "cell-level device pairing registry"),
    "CROSS-008": ("control_plane", "peer map disclosure (Headscale)"),
    "CROSS-009": ("control_plane", "peer map disclosure (Headscale)"),
    "CROSS-010": ("control_plane", "WireGuard direct-path routing"),
    "CROSS-011": ("control_plane", "DERP relay admission"),
    "CROSS-012": ("control_plane", "DERP relay admission"),
    "CROSS-013": ("control_plane", "Headscale tag authority"),
    "CROSS-014": ("control_plane", "Headscale route advertisement"),
    "CROSS-015": ("control_plane", "Headscale route advertisement"),
    "CROSS-016": ("control_plane", "Headscale exit-node capability"),
    "CROSS-017": ("control_plane", "Headscale SSH capability"),
    "TOPO-001": ("control_plane", "tailnet topology (client-to-client)"),
    "TOPO-002": ("control_plane", "tailnet topology (home-to-client)"),
    "TOPO-003": ("control_plane", "network listener port surface"),
}


def classify_all(fixture_cases: list[dict]) -> dict[str, tuple[str, str]]:
    """Rebuild the classification over the fixture and verify completeness."""
    case_ids = [c["id"] for c in fixture_cases]
    assert set(case_ids) == set(CLASSIFICATION), (
        f"classification drift: fixture ids {sorted(set(case_ids) ^ set(CLASSIFICATION))}"
    )
    return CLASSIFICATION


class CaseBuilder:
    """Build (request, context) for each core-applicable threat case."""

    def __init__(self, route_policy_fixture: dict) -> None:
        self.policy_fixture = route_policy_fixture

    # ── per-case context construction ─────────────────────────────────────

    def _base_ctx(self, **overrides) -> GatewayContext:
        fields = dict(
            connector_identity=default_identity(),
            proof=identity.DeviceProof(
                device_id="device-a",
                tenant_id="tenant-a",
                home_id="home-a",
                nonce="nonce-1",
                issued_at=NOW() - timedelta(minutes=1),
                expires_at=NOW() + timedelta(minutes=5),
                binding_id=None,
            ),
            proof_verifier=identity.StaticProofVerifier(identity.ProofVerdict(ok=True)),
            single_use_guard=identity.SingleUseGuard(),
            authorization=AuthorizationVerifier(default_authorization_state()),
            policy=None,
            credential_provider=StaticDaemonCredentialProvider(BEARER),
            forwarder=ForwardingHarness(),
            stream_registry=StreamRegistry(),
            scanner=CredentialScanner(),
            now=NOW(),
        )
        fields.update(overrides)
        if fields["policy"] is None:
            fields["policy"] = self._consumer()
        return GatewayContext(**fields)

    def _consumer(self, **overrides) -> RoutePolicyConsumer:
        from runtime.remote_access.policy import RoutePolicyConsumer

        last_revision = overrides.pop("last_revision", None)
        revision = int(overrides.get("revision", 1))
        envelope = make_policy_envelope(self.policy_fixture, **overrides)
        return RoutePolicyConsumer.from_envelope(
            envelope,
            pinned_digest=None,
            last_revision=revision - 1 if last_revision is None else last_revision,
            now=NOW(),
        )

    def build(self, case: dict):
        """Return (request, context) for one threat case."""
        case_id: str = case["id"]
        method: str = case["inputs"]["method"]
        path: str = case["inputs"]["path"]
        policy_state: str = case["inputs"]["policy_state"]
        stream_type = "http"
        headers: list[tuple[str, str]] = []
        body: bytes | None = None
        verifier = identity.StaticProofVerifier(identity.ProofVerdict(ok=True))
        authz = AuthorizationVerifier(default_authorization_state())
        policy_kwargs: dict = {}
        provider = StaticDaemonCredentialProvider(BEARER)
        forwarder: ForwardingHarness = ForwardingHarness()
        proof = identity.DeviceProof(
            device_id="device-a",
            tenant_id="tenant-a",
            home_id="home-a",
            nonce="nonce-1",
            issued_at=NOW() - timedelta(minutes=1),
            expires_at=NOW() + timedelta(minutes=5),
            binding_id=None,
        )

        if case_id in {"POS-001", "POS-002"}:
            pass  # defaults are fully valid
        elif case_id == "POS-003":
            path = "/api/v1/orgs/acme/threads/T-1/tail"
            stream_type = "sse"
            headers = [("accept", "text/event-stream")]
        elif case_id == "CRED-001":
            proof = identity.DeviceProof(
                **{**proof.__dict__, "expires_at": NOW() - timedelta(seconds=1)}
            )
        elif case_id == "CRED-002":
            verifier = identity.ReplayGuardingVerifier(
                identity.StaticProofVerifier(identity.ProofVerdict(ok=True))
            )
            verifier.verify(proof, default_identity(), now=NOW())  # consume the nonce
        elif case_id in {"CRED-003", "CRED-003b"}:
            binding = case["inputs"]["credential_placeholder"]
            proof = identity.DeviceProof(
                **{**proof.__dict__, "binding_id": binding}
            )
            guard = identity.SingleUseGuard()
            if case_id == "CRED-003":
                guard.redeem(binding)  # consumed
            # CRED-003b: binding absent — identical denial expected.
            ctx = self._base_ctx(proof=proof, single_use_guard=guard, proof_verifier=verifier)
            return make_request(method, path, headers=headers, stream_type=stream_type), ctx
        elif case_id == "CRED-004":
            proof = identity.DeviceProof(
                **{**proof.__dict__, "tenant_id": "tenant-b"}
            )
            verifier = identity.AudienceCheckingVerifier(
                identity.StaticProofVerifier(identity.ProofVerdict(ok=True))
            )
        elif case_id == "CRED-005":
            proof = identity.DeviceProof(**{**proof.__dict__, "home_id": "home-b"})
            verifier = identity.AudienceCheckingVerifier(
                identity.StaticProofVerifier(identity.ProofVerdict(ok=True))
            )
        elif case_id == "DEV-001":
            # device-a was current, then device-b paired at a newer epoch.
            state = default_authorization_state()
            state.apply_pairing(
                DeviceAuthorization(
                    device_id="device-b",
                    tenant_id="tenant-a",
                    home_id="home-a",
                    authorization_epoch=2,
                    expires_at=NOW() + timedelta(days=30),
                )
            )
            authz = AuthorizationVerifier(state)
            proof = identity.DeviceProof(**{**proof.__dict__, "device_id": "device-a"})
        elif case_id == "DEV-002":
            # pairing record is bound to a different home than the request.
            state = default_authorization_state()
            state.devices["device-a"] = DeviceAuthorization(
                device_id="device-a",
                tenant_id="tenant-a",
                home_id="home-b",
                authorization_epoch=1,
                expires_at=NOW() + timedelta(days=30),
            )
            authz = AuthorizationVerifier(state)
        elif case_id == "INT-001":
            class _Exploding(identity.DeviceProofVerifier):
                def verify(self, proof, connector_identity, now):
                    raise RuntimeError("internal boom")

            verifier = _Exploding()
        elif case_id == "LOCAL-001":
            headers = [("x-custom", f"Bearer {BEARER}")]
        elif case_id == "LOCAL-002":
            class _Unavailable(StaticDaemonCredentialProvider):
                def read_bearer(self) -> str:
                    raise CredentialUnavailable("token file missing")

            provider = _Unavailable("unused")
        elif case_id == "LOCAL-003":
            class _NonLoopback(ForwardingHarness):
                def __init__(self) -> None:
                    self.target = SimpleNamespace(host="10.0.0.9", port=80)
                    self.forwarded = []
                    self.streams = []

            forwarder = _NonLoopback()
        elif case_id in {"PATH-001", "PATH-002", "PATH-003"}:
            pass  # the path itself triggers normalization denial
        elif case_id == "POLICY-001":
            policy_kwargs["state"] = "empty"
        elif case_id == "POLICY-002":
            policy_kwargs["state"] = "malformed"
        elif case_id == "POLICY-003":
            policy_kwargs["max_age_seconds"] = 60
            policy_kwargs["issued_at"] = NOW() - timedelta(seconds=120)
        elif case_id == "POLICY-004":
            policy_kwargs["issued_at"] = NOW() + timedelta(seconds=3600)
        elif case_id == "POLICY-005":
            policy_kwargs["revision"] = 1
        elif case_id == "POLICY-006":
            policy_kwargs["state"] = "compiler_failed"
        elif case_id == "POLICY-007":
            policy_kwargs["state"] = "apply_failed"
        elif case_id == "REV-001":
            state = default_authorization_state()
            state.apply_revocation(epoch=2)
            authz = AuthorizationVerifier(state)
        elif case_id in {"REV-002", "REV-003"}:
            # handled by the dedicated live-stream tests; not built here.
            raise AssertionError("REV-002/REV-003 require the live-stream harness")
        elif case_id == "ROUTE-001":
            path = "/api/v1/orgs/acme/tasks/T-1/completion"
            method = "POST"
        elif case_id == "ROUTE-002":
            method = "DELETE"
        elif case_id == "ROUTE-003":
            path = "/api/v1/orgs/acme/some-new-route"
        elif case_id == "SMUG-001":
            headers = [("content-length", "5"), ("transfer-encoding", "chunked")]
        elif case_id == "SMUG-002":
            headers = [("content-length", "5"), ("content-length", "6")]
        elif case_id == "UPG-001":
            stream_type = "websocket"
        elif case_id == "UPG-002":
            stream_type = "sse"
            path = "/api/v1/orgs/acme/threads/T-1/tail"
            body = b"unsupported"
        else:
            raise AssertionError(f"unclassified case {case_id}")

        # POLICY-005 (rollback) is expressed as a revision below the last one
        # applied; construction keeps the consumer but require_current denies.
        if case_id == "POLICY-005":
            policy_kwargs["last_revision"] = 2

        consumer = self._consumer(**policy_kwargs)
        ctx = GatewayContext(
            connector_identity=default_identity(),
            proof=proof,
            proof_verifier=verifier,
            single_use_guard=identity.SingleUseGuard(),
            authorization=authz,
            policy=consumer,
            credential_provider=provider,
            forwarder=forwarder,
            stream_registry=StreamRegistry(),
            scanner=CredentialScanner(),
            now=NOW(),
        )
        return make_request(method, path, headers=headers, body=body, stream_type=stream_type), ctx
