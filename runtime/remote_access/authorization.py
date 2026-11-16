"""Current authorization/revocation checking (contract §6.1 step 2, §9).

The authorization verifier enforces the (tenant, home, device) binding,
current pairing, current-device identity, and revocation state. Trust
updates apply monotonically: revocation and pairing epochs can only move
forward; rollback is rejected. Revocation is applied through the
authoritative ``RevocationCoordinator`` transaction (``revocation`` module),
which closes live streams before or atomically with trust-state application;
``TrustState._apply_revocation`` is private and must not be called directly.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from runtime.remote_access.state import TrustStateStore

from runtime.remote_access.identity import ConnectorIdentity


@dataclass(frozen=True)
class DeviceAuthorization:
    """A paired device's authorization record.

    ``credential_digest`` (THR-097 Unit 3A Supported-DIY lane) is the sha256
    digest of the device's pairing credential — the verifier material the
    customer-owned-network adapter checks on every request. Only the digest
    is ever stored; the raw credential is generated once at pairing-redeem
    time and never persisted, logged, or rendered. Absent (None) keeps the
    managed/harness paths unchanged.
    """

    device_id: str
    tenant_id: str
    home_id: str
    authorization_epoch: int
    expires_at: datetime
    revoked: bool = False
    credential_digest: str | None = None
    control_plane_id: str | None = None
    stable_node_id: str | None = None

    @property
    def re_pair_required(self) -> bool:
        return self.control_plane_id is None or self.stable_node_id is None


@dataclass(frozen=True)
class InviteRecord:
    secret_digest: str
    expires_at: datetime
    generation: int


@dataclass(frozen=True)
class PendingApproval:
    generation: int
    control_plane_id: str
    stable_node_id: str
    handle_digest: str
    comparison_phrase: str
    expires_at: datetime
    device_id: str | None = None  # set only after durable approval


@dataclass(frozen=True)
class PendingPairing:
    """A single-use, expiring local pairing token awaiting redemption.

    Only the sha256 digest of the code is persisted (never the code itself);
    ``consumed`` marks single-use redemption so a replayed code denies
    identically to an absent one (no credential-existence oracle).
    """

    code_digest: str
    expires_at: datetime
    consumed: bool = False


@dataclass
class TrustState:
    """In-memory trust state: connector identity, paired devices, pending
    local pairing tokens, and the monotonic pairing/revocation epochs."""

    connector_identity: ConnectorIdentity | None
    pairing_epoch: int
    revocation_epoch: int
    devices: dict[str, DeviceAuthorization] = field(default_factory=dict)
    current_device_id: str | None = None
    pending_pairings: dict[str, PendingPairing] = field(default_factory=dict)
    invite_record: InviteRecord | None = None
    pending_approval: PendingApproval | None = None
    delivery: PendingApproval | None = None

    def apply_pairing(self, device: DeviceAuthorization) -> None:
        """Pair/re-pair a device; epochs are monotonic (rollback rejected)."""
        if device.authorization_epoch < self.pairing_epoch:
            raise ValueError("pairing epoch rollback rejected")
        self.pairing_epoch = device.authorization_epoch
        self.devices[device.device_id] = device
        self.current_device_id = device.device_id

    def _apply_revocation(self, epoch: int) -> None:
        """Apply trust-state revocation at a monotonic epoch; every device is
        marked revoked.

        PRIVATE: state revocation must only ever be applied through the
        authoritative ``RevocationCoordinator`` transaction (contract §9),
        which closes every live stream before or atomically with this
        application. Calling this directly would bypass live-stream closure.
        """
        if epoch <= self.revocation_epoch:
            raise ValueError("revocation epoch rollback rejected")
        self.revocation_epoch = epoch
        for device in self.devices.values():
            object.__setattr__(device, "revoked", True)

    def _revoke_device(self, device_id: str, epoch: int) -> None:
        """Revoke ONE paired device at a monotonic epoch (Supported-DIY
        lost-device flow); other devices stay authorized. The epoch still
        advances monotonically (rollback rejected) so a persisted revocation
        cannot be replayed away.

        PRIVATE: like ``_apply_revocation``, reachable only through the
        authoritative ``RevocationCoordinator`` transaction (contract §9) so
        live-stream closure always precedes the state change. Revoking a
        device that is no longer paired still advances the epoch (deny side).
        """
        if epoch <= self.revocation_epoch:
            raise ValueError("revocation epoch rollback rejected")
        self.revocation_epoch = epoch
        device = self.devices.get(device_id)
        if device is not None:
            object.__setattr__(device, "revoked", True)


@dataclass(frozen=True)
class AuthorizationVerdict:
    ok: bool
    reason: str | None = None  # identity|pairing|current_device|revocation
    device_id: str | None = None
    authorization_epoch: int | None = None


def admit(
    state_store: TrustStateStore, credential: str, stable_node_id: str,
    control_plane_id: str, now: datetime,
) -> AuthorizationVerdict:
    """Pure admission decision over CURRENT anchored trust; no HTTP identity input.

    The successful verdict carries the exact device/epoch to register a stream.
    Slice 2b owns request, WebSocket and SSE wiring and all remaining gateway gates.
    """
    import hashlib
    import hmac
    from runtime.remote_access.state_store import StateStoreError, _require_identity_text

    denied = AuthorizationVerdict(False, "identity_unestablished")
    try:
        _require_identity_text(stable_node_id)
        _require_identity_text(control_plane_id)
    except StateStoreError:
        return denied
    if (not isinstance(credential, str) or len(credential) > 256
            or not isinstance(stable_node_id, str) or not stable_node_id
            or not isinstance(control_plane_id, str) or not control_plane_id
            or not isinstance(now, datetime) or now.tzinfo is None):
        return denied
    parts = credential.split("_")
    if len(parts) != 3 or parts[0] != "hrpin" or not parts[1].isascii() or not parts[1].isdigit() or len(parts[1]) > 20:
        return denied
    epoch = int(parts[1])
    try:
        digest = hashlib.sha256(credential.encode("utf-8")).hexdigest()
    except UnicodeError:
        return denied
    try:
        state = state_store.load()
    except StateStoreError:
        return denied
    if state.connector_identity is None:
        return denied
    for device in state.devices.values():
        if device.credential_digest is None or not hmac.compare_digest(device.credential_digest, digest):
            continue
        if (device.re_pair_required or device.revoked or now >= device.expires_at
                or device.authorization_epoch != epoch
                or device.tenant_id != state.connector_identity.tenant_id
                or device.home_id != state.connector_identity.home_id
                or (device.control_plane_id, device.stable_node_id) != (control_plane_id, stable_node_id)
                or (state.delivery is not None and state.delivery.device_id == device.device_id)):
            return denied
        return AuthorizationVerdict(True, device_id=device.device_id, authorization_epoch=epoch)
    return denied


class AuthorizationVerifier:
    """Checks whether a (tenant, home, device) may reach this connector now."""

    def __init__(self, state: TrustState) -> None:
        self.state = state

    def check(self, tenant_id: str, home_id: str, device_id: str, now: datetime) -> AuthorizationVerdict:
        identity_ = self.state.connector_identity
        if identity_ is None:
            return AuthorizationVerdict(False, "identity")
        if tenant_id != identity_.tenant_id or home_id != identity_.home_id:
            return AuthorizationVerdict(False, "identity")
        device = self.state.devices.get(device_id)
        if device is None:
            return AuthorizationVerdict(False, "pairing")
        if device.tenant_id != tenant_id or device.home_id != home_id:
            return AuthorizationVerdict(False, "pairing")
        if device.revoked:
            return AuthorizationVerdict(False, "revocation")
        if now > device.expires_at:
            return AuthorizationVerdict(False, "pairing")
        if self.state.current_device_id is not None and device_id != self.state.current_device_id:
            return AuthorizationVerdict(False, "current_device")
        return AuthorizationVerdict(True)


class RevocationSignal:
    """A monotonic revocation signal; subscribers (e.g. the stream registry)
    are invoked only when the epoch moves forward."""

    def __init__(self) -> None:
        self._epoch = 0
        self._subscribers: list[Callable[[int], None]] = []

    @property
    def epoch(self) -> int:
        return self._epoch

    def subscribe(self, callback: Callable[[int], None]) -> None:
        self._subscribers.append(callback)

    def fire(self, epoch: int) -> None:
        if epoch <= self._epoch:
            return
        self._epoch = epoch
        for callback in self._subscribers:
            callback(epoch)
