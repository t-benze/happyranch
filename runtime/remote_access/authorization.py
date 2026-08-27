"""Current authorization/revocation checking (contract §6.1 step 2, §9).

The authorization verifier enforces the (tenant, home, device) binding,
current pairing, current-device identity, and revocation state. Trust
updates apply monotonically: revocation and pairing epochs can only move
forward; rollback is rejected. A ``RevocationSignal`` closes live streams
before or atomically with durable desired-state application.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

from runtime.remote_access.identity import ConnectorIdentity


@dataclass(frozen=True)
class DeviceAuthorization:
    """A paired device's authorization record."""

    device_id: str
    tenant_id: str
    home_id: str
    authorization_epoch: int
    expires_at: datetime
    revoked: bool = False


@dataclass
class TrustState:
    """In-memory trust state: connector identity, paired devices, and the
    monotonic pairing/revocation epochs."""

    connector_identity: ConnectorIdentity | None
    pairing_epoch: int
    revocation_epoch: int
    devices: dict[str, DeviceAuthorization] = field(default_factory=dict)
    current_device_id: str | None = None

    def apply_pairing(self, device: DeviceAuthorization) -> None:
        """Pair/re-pair a device; epochs are monotonic (rollback rejected)."""
        if device.authorization_epoch < self.pairing_epoch:
            raise ValueError("pairing epoch rollback rejected")
        self.pairing_epoch = device.authorization_epoch
        self.devices[device.device_id] = device
        self.current_device_id = device.device_id

    def apply_revocation(self, epoch: int) -> None:
        """Revoke at a monotonic epoch; every device is marked revoked."""
        if epoch <= self.revocation_epoch:
            raise ValueError("revocation epoch rollback rejected")
        self.revocation_epoch = epoch
        for device in self.devices.values():
            object.__setattr__(device, "revoked", True)


@dataclass(frozen=True)
class AuthorizationVerdict:
    ok: bool
    reason: str | None = None  # identity|pairing|current_device|revocation


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
