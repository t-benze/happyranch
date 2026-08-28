"""Atomic, corruption-detecting, owner-only local trust-state store
(THR-097 phase unit 3, contract §13).

The connector's durable trust state — connector identity, pairing/revocation
epochs, paired devices, current device — must survive a service restart so
revocation stays effective across restarts. This module implements the
already-approved ``TrustStateStore`` protocol (``state.py``) as an atomic
file-backed store with:

- **atomic replace + fsync** (temp file in the same directory, fsync, rename,
  directory fsync) so a crash never leaves a half-written state;
- **owner-only permissions** (file ``0600``, parent directory ``0700``);
- **corruption detection** (sha256 digest over the canonical payload; any
  present-but-corrupt state fails closed — corruption could erase a
  revocation and must never be silently treated as a valid earlier epoch);
- **strict payload validation** (unknown keys, wrong types, bool-epochs,
  naive datetimes, key mismatches all fail closed);
- **symlink/directory rejection** (a state path that is a symlink or a
  directory is never read).

The envelope is a versioned, **schema-agnostic, non-normative** JSON
document. It is NOT the founder-gated managed persistent schema: it defines
no database, no migration machinery, and no cross-version schema contract.
It is the local recovery aid contract §13 permits (atomic replace/fsync,
corruption detection, rollback-safe behavior, crash-safe trust updates),
explicitly replaceable by the future founder-gated store. It never co-mingles
with the daemon token file or mutable Services caches, and it never carries
the daemon bearer or any credential-shaped material — it holds authorization
state only.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from runtime.remote_access.authorization import DeviceAuthorization, TrustState
from runtime.remote_access.identity import ConnectorIdentity
from runtime.remote_access.policy import canonical_json
# The non-normative local envelope (not a founder-gated schema). Bumping the
# version is a breaking change to the recovery aid; unknown versions fail
# closed rather than being guessed.
ENVELOPE_VERSION = 1
ENVELOPE_KIND = "connector-trust-state-nonnormative"

_STATE_DIR_MODE = 0o700
_STATE_FILE_MODE = 0o600

_PAYLOAD_KEYS = frozenset(
    {"connector_identity", "pairing_epoch", "revocation_epoch", "current_device_id", "devices"}
)
_IDENTITY_KEYS = frozenset({"tenant_id", "home_id", "connector_id"})
_DEVICE_KEYS = frozenset(
    {
        "device_id",
        "tenant_id",
        "home_id",
        "authorization_epoch",
        "expires_at",
        "revoked",
    }
)


class StateStoreError(Exception):
    """Raised when the trust-state store is unusable (missing, unreadable,
    loose permissions, symlinked path, write failure)."""


class CorruptTrustStateError(StateStoreError):
    """Raised when a present trust-state file is corrupt or violates the
    envelope/payload contract. Fail closed: corruption could erase a
    revocation, so corrupt state is never treated as a valid earlier state."""


def _require_int(payload: dict, key: str) -> int:
    value = payload.get(key)
    # bool is an int subclass — a bool epoch must be rejected, never cast.
    if isinstance(value, bool) or not isinstance(value, int):
        raise CorruptTrustStateError(f"trust state {key} must be an integer")
    return value


def _require_str(payload: dict, key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str):
        raise CorruptTrustStateError(f"trust state {key} must be a string")
    return value


def _require_tz_datetime(value: Any) -> datetime:
    if not isinstance(value, str):
        raise CorruptTrustStateError("trust state timestamp must be an ISO string")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise CorruptTrustStateError("trust state timestamp unparseable") from exc
    if parsed.tzinfo is None:
        raise CorruptTrustStateError("trust state timestamp must carry a timezone")
    return parsed


def _state_from_payload(payload: dict[str, Any]) -> TrustState:
    """Strictly validate and rebuild a TrustState from the envelope payload.

    Unknown keys, missing required keys, wrong types, bool-epochs, naive
    datetimes, identity/device extra keys, device-key mismatches, and
    current_device_id references to unknown devices all fail closed as
    corruption — a present-but-invalid state is never guessed.
    """
    unknown = set(payload) - _PAYLOAD_KEYS
    if unknown:
        raise CorruptTrustStateError("trust state payload has unknown keys")
    required = {"pairing_epoch", "revocation_epoch", "devices"}
    missing = required - set(payload)
    if missing:
        raise CorruptTrustStateError("trust state payload incomplete")
    pairing_epoch = _require_int(payload, "pairing_epoch")
    revocation_epoch = _require_int(payload, "revocation_epoch")
    if pairing_epoch < 0 or revocation_epoch < 0:
        raise CorruptTrustStateError("trust state epochs must be non-negative")

    identity_raw = payload.get("connector_identity")
    connector_identity: ConnectorIdentity | None
    if identity_raw is None:
        connector_identity = None
    else:
        if not isinstance(identity_raw, dict):
            raise CorruptTrustStateError("trust state connector_identity must be an object")
        unknown_identity = set(identity_raw) - _IDENTITY_KEYS
        if unknown_identity:
            raise CorruptTrustStateError("trust state connector_identity has unknown keys")
        missing_identity = _IDENTITY_KEYS - set(identity_raw)
        if missing_identity:
            raise CorruptTrustStateError("trust state connector_identity incomplete")
        connector_identity = ConnectorIdentity(
            tenant_id=_require_str(identity_raw, "tenant_id"),
            home_id=_require_str(identity_raw, "home_id"),
            connector_id=_require_str(identity_raw, "connector_id"),
        )

    devices_raw = payload["devices"]
    if not isinstance(devices_raw, dict):
        raise CorruptTrustStateError("trust state devices must be an object")
    devices: dict[str, DeviceAuthorization] = {}
    for device_id, raw in devices_raw.items():
        if not isinstance(device_id, str):
            raise CorruptTrustStateError("trust state device id must be a string")
        if not isinstance(raw, dict):
            raise CorruptTrustStateError("trust state device record must be an object")
        unknown_device = set(raw) - _DEVICE_KEYS
        if unknown_device:
            raise CorruptTrustStateError("trust state device record has unknown keys")
        missing_device = _DEVICE_KEYS - set(raw)
        if missing_device:
            raise CorruptTrustStateError("trust state device record incomplete")
        stored_id = _require_str(raw, "device_id")
        if stored_id != device_id:
            raise CorruptTrustStateError("trust state device key/record mismatch")
        revoked = raw.get("revoked")
        if not isinstance(revoked, bool):
            raise CorruptTrustStateError("trust state revoked must be a boolean")
        devices[device_id] = DeviceAuthorization(
            device_id=stored_id,
            tenant_id=_require_str(raw, "tenant_id"),
            home_id=_require_str(raw, "home_id"),
            authorization_epoch=_require_int(raw, "authorization_epoch"),
            expires_at=_require_tz_datetime(raw.get("expires_at")),
            revoked=revoked,
        )

    current_device_id = payload.get("current_device_id")
    if current_device_id is not None:
        if not isinstance(current_device_id, str):
            raise CorruptTrustStateError("trust state current_device_id must be a string")
        if current_device_id not in devices:
            raise CorruptTrustStateError(
                "trust state current_device_id references an unknown device"
            )
    return TrustState(
        connector_identity=connector_identity,
        pairing_epoch=pairing_epoch,
        revocation_epoch=revocation_epoch,
        devices=devices,
        current_device_id=current_device_id,
    )


def _payload_from_state(state: TrustState) -> dict[str, Any]:
    """Serialize a TrustState to the envelope payload (authorization state
    only — never the daemon bearer or any credential material)."""
    identity_raw = None
    if state.connector_identity is not None:
        identity_raw = {
            "tenant_id": state.connector_identity.tenant_id,
            "home_id": state.connector_identity.home_id,
            "connector_id": state.connector_identity.connector_id,
        }
    return {
        "connector_identity": identity_raw,
        "pairing_epoch": state.pairing_epoch,
        "revocation_epoch": state.revocation_epoch,
        "current_device_id": state.current_device_id,
        "devices": {
            device_id: {
                "device_id": device.device_id,
                "tenant_id": device.tenant_id,
                "home_id": device.home_id,
                "authorization_epoch": device.authorization_epoch,
                "expires_at": device.expires_at.isoformat(),
                "revoked": device.revoked,
            }
            for device_id, device in sorted(state.devices.items())
        },
    }


class AtomicFileTrustStateStore:
    """An atomic, corruption-detecting, owner-only ``TrustStateStore``.

    ``default_state`` is returned when no state file exists (first run: a
    fresh deny-all state). Any present-but-corrupt/loose/unreadable/symlinked
    state fails closed with :class:`StateStoreError` (or
    :class:`CorruptTrustStateError`).
    """

    def __init__(self, path: Path, default_state: TrustState) -> None:
        self._path = Path(path)
        self._default_state = default_state

    # ── TrustStateStore protocol ──────────────────────────────────────────

    def load(self) -> TrustState:
        path = self._path
        if not path.exists():
            return self._default_state
        if path.is_symlink():
            raise StateStoreError("trust state file must not be a symlink")
        if not path.is_file():
            raise StateStoreError("trust state path is not a file")
        try:
            mode = stat.S_IMODE(path.stat().st_mode)
        except OSError as exc:
            raise StateStoreError("trust state file unreadable") from exc
        if mode & 0o077:
            raise StateStoreError("trust state file permissions too loose")
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise StateStoreError("trust state file unreadable") from exc
        try:
            envelope = json.loads(raw)
        except (ValueError, TypeError) as exc:
            raise CorruptTrustStateError("trust state file is not valid JSON") from exc
        if not isinstance(envelope, dict):
            raise CorruptTrustStateError("trust state envelope must be an object")
        if envelope.get("version") != ENVELOPE_VERSION:
            raise CorruptTrustStateError("trust state envelope version unsupported")
        if envelope.get("kind") != ENVELOPE_KIND:
            raise CorruptTrustStateError("trust state envelope kind mismatch")
        payload = envelope.get("payload")
        if not isinstance(payload, dict):
            raise CorruptTrustStateError("trust state payload must be an object")
        digest = envelope.get("digest")
        expected = hashlib.sha256(canonical_json(payload)).hexdigest()
        if not isinstance(digest, str) or digest != expected:
            raise CorruptTrustStateError("trust state integrity check failed")
        return _state_from_payload(payload)

    def save(self, state: TrustState) -> None:
        payload = _payload_from_state(state)
        envelope = {
            "version": ENVELOPE_VERSION,
            "kind": ENVELOPE_KIND,
            "payload": payload,
            "digest": hashlib.sha256(canonical_json(payload)).hexdigest(),
        }
        self._write_envelope(envelope)

    def _render(self, state: TrustState) -> bytes:
        """Serialize *state* exactly as ``save`` would (deterministic)."""
        payload = _payload_from_state(state)
        envelope = {
            "version": ENVELOPE_VERSION,
            "kind": ENVELOPE_KIND,
            "payload": payload,
            "digest": hashlib.sha256(canonical_json(payload)).hexdigest(),
        }
        return json.dumps(
            envelope, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")

    def _write_envelope(self, envelope: dict) -> None:
        parent = self._path.parent
        fd = None
        tmp_name: str | None = None
        try:
            parent.mkdir(parents=True, exist_ok=True)
            # The state directory must be owner-only: a group/world-readable
            # state directory would leak device/epoch metadata and invite
            # tampering. Tightening is always safe for the connector state dir.
            os.chmod(parent, _STATE_DIR_MODE)
        except OSError as exc:
            raise StateStoreError("trust state directory unavailable") from exc
        try:
            fd, tmp_name = tempfile.mkstemp(
                prefix=f".{self._path.name}.", suffix=".tmp", dir=parent
            )
            os.fchmod(fd, _STATE_FILE_MODE)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fd = None
                json.dump(
                    envelope,
                    fh,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                )
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_name, self._path)
            tmp_name = None
            # fsync the directory so the rename is durable across a crash.
            dir_fd = os.open(parent, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except OSError as exc:
            raise StateStoreError("trust state write failed") from exc
        finally:
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
            if tmp_name is not None:
                try:
                    os.unlink(tmp_name)
                except OSError:
                    pass
