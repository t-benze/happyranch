"""Current authorization / revocation checking (contract §6.1 step 2, §9).

Covers: pairing, current-device identity, revocation-before-request, monotonic
epoch trust updates with rollback rejection, and the revocation signal that
closes live streams fail closed.
"""
from __future__ import annotations

from datetime import timedelta

import pytest

from runtime.remote_access import authorization
from runtime.remote_access.authorization import (
    AuthorizationVerifier,
    DeviceAuthorization,
    RevocationSignal,
    TrustState,
)
from runtime.remote_access.revocation import RevocationCoordinator
from runtime.remote_access.streams import StreamRegistry

from .conftest import NOW, default_authorization_state, default_identity


def _revoke(state, epoch: int) -> None:
    """Revoke through the authoritative public transaction (contract §9)."""
    RevocationCoordinator(state, StreamRegistry()).revoke(epoch=epoch)


def _authz() -> AuthorizationVerifier:
    return AuthorizationVerifier(default_authorization_state())


def test_current_paired_device_ok() -> None:
    verdict = _authz().check("tenant-a", "home-a", "device-a", now=NOW())
    assert verdict.ok is True


def test_wrong_tenant_denied_identity() -> None:
    verdict = _authz().check("tenant-b", "home-a", "device-a", now=NOW())
    assert verdict.ok is False
    assert verdict.reason == "identity"


def test_wrong_home_denied_identity() -> None:
    verdict = _authz().check("tenant-a", "home-b", "device-a", now=NOW())
    assert verdict.ok is False
    assert verdict.reason == "identity"


def test_unpaired_device_denied_pairing() -> None:
    verdict = _authz().check("tenant-a", "home-a", "device-x", now=NOW())
    assert verdict.ok is False
    assert verdict.reason == "pairing"


def test_secondary_paired_device_is_current() -> None:
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
    verdict = AuthorizationVerifier(state).check("tenant-a", "home-a", "device-b", now=NOW())
    assert verdict.ok is True


def test_expired_pairing_denied_pairing() -> None:
    state = default_authorization_state()
    # Overwrite with an already-expired authorization.
    state.devices["device-a"] = DeviceAuthorization(
        device_id="device-a",
        tenant_id="tenant-a",
        home_id="home-a",
        authorization_epoch=1,
        expires_at=NOW() - timedelta(seconds=1),
    )
    verdict = AuthorizationVerifier(state).check("tenant-a", "home-a", "device-a", now=NOW())
    assert verdict.ok is False
    assert verdict.reason == "pairing"


def test_revoked_device_denied_before_request() -> None:
    state = default_authorization_state()
    _revoke(state, epoch=2)
    verdict = AuthorizationVerifier(state).check("tenant-a", "home-a", "device-a", now=NOW())
    assert verdict.ok is False
    assert verdict.reason == "revocation"


def test_revocation_epoch_monotonic() -> None:
    state = default_authorization_state()
    _revoke(state, epoch=1)
    _revoke(state, epoch=2)
    with pytest.raises(ValueError):
        _revoke(state, epoch=1)  # rollback


def test_pairing_epoch_monotonic() -> None:
    state = default_authorization_state()
    with pytest.raises(ValueError):
        state.apply_pairing(
            DeviceAuthorization(
                device_id="device-c",
                tenant_id="tenant-a",
                home_id="home-a",
                authorization_epoch=0,  # below current pairing_epoch
                expires_at=NOW() + timedelta(days=1),
            )
        )


def test_revocation_signal_fires_callbacks() -> None:
    signal = RevocationSignal()
    fired: list[int] = []
    signal.subscribe(lambda epoch: fired.append(epoch))
    signal.fire(3)
    assert fired == [3]


def test_revocation_signal_ignores_stale_epochs() -> None:
    signal = RevocationSignal()
    signal.fire(3)
    fired: list[int] = []
    signal.subscribe(lambda epoch: fired.append(epoch))
    signal.fire(2)  # stale — must not fire
    assert fired == []


def test_trust_state_keeps_connector_identity() -> None:
    state = default_authorization_state()
    assert state.connector_identity == default_identity()


def test_bind_rejects_unknown_connector_identity() -> None:
    state = TrustState(connector_identity=None, pairing_epoch=0, revocation_epoch=0)
    verdict = AuthorizationVerifier(state).check("tenant-a", "home-a", "device-a", now=NOW())
    assert verdict.ok is False
    assert verdict.reason == "identity"
