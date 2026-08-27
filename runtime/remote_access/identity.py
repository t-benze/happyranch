"""Connector identity + device-proof verifier seam (contract §6.1 steps 1-3,
§7 connector_device_proof / one_use_enrollment).

The verifier is a seam: the skeleton ships a static verifier and a
replay-guarding wrapper; a real implementation plugs in later without
changing the gateway. Single-use bindings deny identically for absent and
consumed bindings (no credential-existence oracle).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from pydantic import BaseModel, field_validator

from runtime.remote_access.audit import detail_for
from runtime.remote_access.models import DeniedOutcome


class ConnectorIdentity(BaseModel):
    """The connector's unambiguous home identity."""

    tenant_id: str
    home_id: str
    connector_id: str

    @field_validator("tenant_id", "home_id", "connector_id")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("identity components must be non-empty")
        return value


class DeviceProof(BaseModel):
    """A client-presented proof of device identity.

    ``binding_id`` optionally references a single-use enrollment/pairing
    binding; when present the gateway consults the ``SingleUseGuard``.
    """

    device_id: str
    tenant_id: str
    home_id: str
    nonce: str
    issued_at: datetime
    expires_at: datetime
    binding_id: str | None = None


@dataclass(frozen=True)
class ProofVerdict:
    ok: bool
    reason: str | None = None  # expired|replayed|wrong_audience|wrong_home|identity_unestablished


class DeviceProofVerifier(Protocol):
    """Verifies a device proof against the connector identity.

    Implementations raise only on verifier-level failure (registry
    unavailable, corruption); the gateway maps any raised exception to a
    redacted ``internal`` denial.
    """

    def verify(
        self, proof: DeviceProof, connector_identity: ConnectorIdentity, now: datetime
    ) -> ProofVerdict: ...


class StaticProofVerifier:
    """A fixed-verdict verifier for the harness and unit tests."""

    def __init__(self, verdict: ProofVerdict) -> None:
        self._verdict = verdict

    def verify(
        self, proof: DeviceProof, connector_identity: ConnectorIdentity, now: datetime
    ) -> ProofVerdict:
        del proof, connector_identity, now
        return self._verdict


class ReplayGuardingVerifier:
    """Wraps a base verifier and rejects replayed (device, nonce) pairs."""

    def __init__(self, base: DeviceProofVerifier) -> None:
        self._base = base
        self._seen: set[tuple[str, str]] = set()

    def verify(
        self, proof: DeviceProof, connector_identity: ConnectorIdentity, now: datetime
    ) -> ProofVerdict:
        key = (proof.device_id, proof.nonce)
        if key in self._seen:
            return ProofVerdict(ok=False, reason="replayed")
        verdict = self._base.verify(proof, connector_identity, now)
        if verdict.ok:
            self._seen.add(key)
        return verdict


class AudienceCheckingVerifier:
    """Wraps a base verifier and first validates the proof's audience binding
    (tenant/home) against the connector identity (CRED-004/CRED-005)."""

    def __init__(self, base: DeviceProofVerifier) -> None:
        self._base = base

    def verify(
        self, proof: DeviceProof, connector_identity: ConnectorIdentity, now: datetime
    ) -> ProofVerdict:
        if proof.tenant_id != connector_identity.tenant_id:
            return ProofVerdict(ok=False, reason="wrong_audience")
        if proof.home_id != connector_identity.home_id:
            return ProofVerdict(ok=False, reason="wrong_home")
        return self._base.verify(proof, connector_identity, now)


class SingleUseGuard:
    """Single-use binding redemption guard with no existence oracle.

    ``check`` returns the identical fail-closed denial for an absent binding
    and a consumed binding — a caller can never distinguish them.
    """

    _DENY = DeniedOutcome(
        deny_category="replay",
        audit_category="credential_reused",
        detail=detail_for("replay", "credential_reused"),
        reason="single_use",
    )

    def __init__(self) -> None:
        self._provisioned: set[str] = set()
        self._consumed: set[str] = set()

    def redeem(self, binding_id: str) -> None:
        """Mark a binding consumed (out-of-band provisioning flow)."""
        self._provisioned.add(binding_id)
        self._consumed.add(binding_id)

    def check(self, binding_id: str) -> DeniedOutcome | None:
        """Deny identically for absent and consumed bindings; only a valid,
        unconsumed binding passes (returns None)."""
        if binding_id in self._consumed:
            return self._DENY
        if binding_id not in self._provisioned:
            return self._DENY
        return None
