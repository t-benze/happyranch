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
  directory is never read);
- **monotonic companion generation/digest anchor** (founder approval
  THR-097 seq163): an owner-only ``<state>.anchor`` file OUTSIDE the
  replaceable snapshot records the snapshot generation and a digest over the
  exact snapshot bytes. load() binds the pair — envelope generation must
  equal the anchor generation AND the anchor digest must cover the exact
  snapshot bytes — so a previously valid OLDER snapshot replayed after a
  newer revocation/generation (including across a new store/process
  instance) is deterministically rejected. Missing/mismatched/corrupt/
  stale anchors, snapshot/anchor partial-update states, symlinked anchors,
  and loose anchor permissions all fail closed.

The envelope is a versioned, **schema-agnostic, non-normative** JSON
document. It is NOT the founder-gated managed persistent schema: it defines
no database, no migration machinery, and no cross-version schema contract.
It is the local recovery aid contract §13 permits (atomic replace/fsync,
corruption detection, rollback-safe behavior, crash-safe trust updates),
explicitly replaceable by the future founder-gated store. It never co-mingles
with the daemon token file or mutable Services caches, and it never carries
the daemon bearer or any credential-shaped material — it holds authorization
state only.

Crash-consistency / rollback contract
-------------------------------------

Each of the two files (snapshot, anchor) is written atomically (temp file,
fsync, rename, directory fsync); the PAIR is updated snapshot-first then
anchor under LOCK_EX. load() takes LOCK_SH on the same stable owner-only
lock file, opened without following symlinks and validated as a regular,
single-link, owner-only inode distinct from both pair files. Lock acquisition
never changes pair-file metadata. Readers cannot sample an in-progress
publication. A crash between the two writes leaves a mismatched pair that
load() rejects — never a silently-accepted stale state. Recovery is an explicit
operator action: restore a consistent (snapshot + anchor) pair from backup,
or delete BOTH files to factory-reset to the first-run deny-all default
(partial deletion of one file fails closed). Generation is strictly
monotonic: save() never writes a generation at or below the anchored one.

Honest limit (consultant seq162 / manager seq165): a local companion anchor
detects accidental or partial local rollback — a backup restore, a
half-copied state directory, a botched upgrade — and rejects naive replay
across restart. It does NOT resist a determined actor who can replace BOTH
local files; authoritative rollback protection for the managed lane is a
Units 4-5 control-plane responsibility (the control plane already knows the
revocation epoch) and is out of scope for this Unit-3 local mechanism.
"""
from __future__ import annotations

import contextlib
import copy
import hashlib
import json
import os
import stat
import secrets
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

import fcntl

from runtime.remote_access.authorization import (
    DeviceAuthorization, InviteRecord, PendingApproval, PendingPairing, TrustState,
)
from runtime.remote_access.identity import ConnectorIdentity
from runtime.remote_access.policy import canonical_json
# The non-normative local envelope (not a founder-gated schema). Bumping the
# version is a breaking change to the recovery aid; unknown versions fail
# closed rather than being guessed.
#
# v2 adds the monotonic ``generation`` field (envelope-level) bound to the
# companion anchor. v1 envelopes (no generation, no anchor) are rejected as
# unsupported — a v1 snapshot can no longer be trusted without an anchor.
# v3 adds strict pins/invite/pending/delivery records. Anchored v2 remains
# readable for explicit re-pairing; no legacy device acquires a pin.
ENVELOPE_VERSION = 3
LEGACY_ENVELOPE_VERSION = 2
MAX_PINNED_DEVICES = 32
MAX_STATE_BYTES = 1024 * 1024
ENVELOPE_KIND = "connector-trust-state-nonnormative"

# The companion monotonic generation/digest anchor (founder THR-097 seq163).
# Lives OUTSIDE the replaceable snapshot (``<state>.anchor``), owner-only,
# atomic, schema-agnostic/non-normative — no database, no migration.
ANCHOR_VERSION = 1
ANCHOR_KIND = "connector-trust-state-anchor-nonnormative"

_STATE_DIR_MODE = 0o700
_STATE_FILE_MODE = 0o600

# Default bound on the owner-only inter-process transaction lock acquire
# (flock). Ceremony operations are short (load + mutate + two small atomic
# writes); a holder that hangs past this bound fails the waiter closed with
# ``StateStoreError`` instead of blocking forever (fail closed on
# contention). ``LOCK_ACQUIRE_SLEEP`` is the non-blocking retry interval.
DEFAULT_LOCK_TIMEOUT_SECONDS = 30.0
_LOCK_ACQUIRE_SLEEP = 0.02

_ENVELOPE_KEYS = frozenset({"version", "kind", "generation", "payload", "digest"})
_ANCHOR_KEYS = frozenset({"version", "kind", "generation", "snapshot_digest", "digest"})

_PAYLOAD_KEYS = frozenset(
    {
        "connector_identity",
        "pairing_epoch",
        "revocation_epoch",
        "current_device_id",
        "devices",
        "pending_pairings",
        "invite_record",
        "pending_approval",
        "delivery",
    }
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
        "credential_digest",
        "control_plane_id",
        "stable_node_id",
    }
)
# credential_digest is OPTIONAL (Unit 3A additive): pre-3A envelopes that
# predate it must still load (absent = None); a present value is validated
# strictly. Unknown keys are still rejected via ``_DEVICE_KEYS``.
_DEVICE_REQUIRED_KEYS = _DEVICE_KEYS - {"credential_digest", "control_plane_id", "stable_node_id"}
_PENDING_KEYS = frozenset({"code_digest", "expires_at", "consumed"})


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


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise CorruptTrustStateError("trust state duplicate key")
        result[key] = value
    return result


def _require_identity_text(value: Any) -> str:
    if not isinstance(value, str) or not value or len(value) > 256 or any(ord(c) < 32 or ord(c) == 127 or 0xD800 <= ord(c) <= 0xDFFF for c in value):
        raise CorruptTrustStateError("trust state identity invalid")
    return value


def _digest(value: Any) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise CorruptTrustStateError("trust state digest invalid")
    return value


def _invite_from_payload(raw: Any) -> InviteRecord | None:
    if raw is None:
        return None
    if not isinstance(raw, dict) or set(raw) != {"secret_digest", "expires_at", "generation"}:
        raise CorruptTrustStateError("trust state invite invalid")
    generation = _require_int(raw, "generation")
    if generation < 1:
        raise CorruptTrustStateError("trust state invite generation invalid")
    return InviteRecord(_digest(raw["secret_digest"]), _require_tz_datetime(raw["expires_at"]), generation)


def _approval_from_payload(raw: Any, *, delivery: bool) -> PendingApproval | None:
    if raw is None:
        return None
    if not isinstance(raw, dict) or set(raw) != {
        "generation", "control_plane_id", "stable_node_id", "handle_digest",
        "comparison_phrase", "expires_at", "device_id",
    }:
        raise CorruptTrustStateError("trust state approval invalid")
    generation = _require_int(raw, "generation")
    device_id = raw["device_id"]
    if generation < 1 or (delivery and not isinstance(device_id, str)) or (not delivery and device_id is not None):
        raise CorruptTrustStateError("trust state approval generation/device invalid")
    if delivery:
        _require_identity_text(device_id)
    return PendingApproval(
        generation, _require_identity_text(raw["control_plane_id"]),
        _require_identity_text(raw["stable_node_id"]), _digest(raw["handle_digest"]),
        _require_identity_text(raw["comparison_phrase"]), _require_tz_datetime(raw["expires_at"]), device_id,
    )


def _record_payload(record: InviteRecord | PendingApproval | None) -> dict[str, Any] | None:
    if record is None:
        return None
    from dataclasses import asdict
    result = asdict(record)
    result["expires_at"] = record.expires_at.isoformat()
    return result


def _state_from_payload(payload: dict[str, Any], *, legacy: bool = False) -> TrustState:
    """Strictly validate and rebuild a TrustState from the envelope payload.

    Unknown keys, missing required keys, wrong types, bool-epochs, naive
    datetimes, identity/device extra keys, device-key mismatches, and
    current_device_id references to unknown devices all fail closed as
    corruption — a present-but-invalid state is never guessed.
    """
    allowed = _PAYLOAD_KEYS - {"invite_record", "pending_approval", "delivery"} if legacy else _PAYLOAD_KEYS
    unknown = set(payload) - allowed
    if unknown:
        raise CorruptTrustStateError("trust state payload has unknown keys")
    required = ({"pairing_epoch", "revocation_epoch", "devices"} if legacy else _PAYLOAD_KEYS)
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
        try:
            connector_identity = ConnectorIdentity(
                tenant_id=_require_str(identity_raw, "tenant_id"),
                home_id=_require_str(identity_raw, "home_id"),
                connector_id=_require_str(identity_raw, "connector_id"),
            )
        except ValueError as exc:
            raise CorruptTrustStateError("trust state connector identity invalid") from exc

    devices_raw = payload["devices"]
    if not isinstance(devices_raw, dict):
        raise CorruptTrustStateError("trust state devices must be an object")
    devices: dict[str, DeviceAuthorization] = {}
    for device_id, raw in devices_raw.items():
        if not isinstance(device_id, str):
            raise CorruptTrustStateError("trust state device id must be a string")
        if not isinstance(raw, dict):
            raise CorruptTrustStateError("trust state device record must be an object")
        allowed_device = _DEVICE_KEYS - {"control_plane_id", "stable_node_id"} if legacy else _DEVICE_KEYS
        unknown_device = set(raw) - allowed_device
        if unknown_device:
            raise CorruptTrustStateError("trust state device record has unknown keys")
        missing_device = (_DEVICE_REQUIRED_KEYS if legacy else _DEVICE_KEYS) - set(raw)
        if missing_device:
            raise CorruptTrustStateError("trust state device record incomplete")
        stored_id = _require_str(raw, "device_id")
        if stored_id != device_id:
            raise CorruptTrustStateError("trust state device key/record mismatch")
        revoked = raw.get("revoked")
        if not isinstance(revoked, bool):
            raise CorruptTrustStateError("trust state revoked must be a boolean")
        # Unit 3A Supported-DIY: the pairing-credential digest is OPTIONAL in
        # the non-normative envelope (managed/harness records predate it).
        # Present must be a non-empty 64-hex sha256 digest; anything else
        # fails closed. Absent stays None (no credential enforcement).
        credential_digest = raw.get("credential_digest")
        if credential_digest is not None:
            if (
                not isinstance(credential_digest, str)
                or len(credential_digest) != 64
                or any(c not in "0123456789abcdef" for c in credential_digest)
            ):
                raise CorruptTrustStateError(
                    "trust state device credential_digest must be a sha256 hex digest"
                )
        control_plane_id = raw.get("control_plane_id")
        stable_node_id = raw.get("stable_node_id")
        if (control_plane_id is None) != (stable_node_id is None):
            raise CorruptTrustStateError("trust state pin incomplete")
        if control_plane_id is not None:
            if credential_digest is None:
                raise CorruptTrustStateError("trust state pinned credential missing")
            _require_identity_text(control_plane_id)
            _require_identity_text(stable_node_id)
        epoch = _require_int(raw, "authorization_epoch")
        if epoch < 1 or epoch > pairing_epoch:
            raise CorruptTrustStateError("trust state device epoch invalid")
        devices[device_id] = DeviceAuthorization(
            device_id=stored_id,
            tenant_id=_require_str(raw, "tenant_id"),
            home_id=_require_str(raw, "home_id"),
            authorization_epoch=_require_int(raw, "authorization_epoch"),
            expires_at=_require_tz_datetime(raw.get("expires_at")),
            revoked=revoked,
            credential_digest=credential_digest,
            control_plane_id=control_plane_id,
            stable_node_id=stable_node_id,
        )

    pending_raw = payload.get("pending_pairings")
    if not legacy and not isinstance(pending_raw, dict):
        raise CorruptTrustStateError("trust state pending_pairings must be an object")
    pending_pairings: dict[str, PendingPairing] = {}
    if pending_raw is not None:
        if not isinstance(pending_raw, dict):
            raise CorruptTrustStateError("trust state pending_pairings must be an object")
        for device_name, raw in pending_raw.items():
            if not isinstance(device_name, str) or not device_name.strip():
                raise CorruptTrustStateError("trust state pending pairing name must be a string")
            if not isinstance(raw, dict):
                raise CorruptTrustStateError("trust state pending pairing record must be an object")
            unknown_pending = set(raw) - _PENDING_KEYS
            if unknown_pending:
                raise CorruptTrustStateError("trust state pending pairing has unknown keys")
            missing_pending = _PENDING_KEYS - set(raw)
            if missing_pending:
                raise CorruptTrustStateError("trust state pending pairing incomplete")
            code_digest = _require_str(raw, "code_digest")
            if len(code_digest) != 64 or any(c not in "0123456789abcdef" for c in code_digest):
                raise CorruptTrustStateError(
                    "trust state pending pairing code_digest must be a sha256 hex digest"
                )
            consumed = raw.get("consumed")
            if not isinstance(consumed, bool):
                raise CorruptTrustStateError("trust state pending pairing consumed must be a boolean")
            pending_pairings[device_name] = PendingPairing(
                code_digest=code_digest,
                expires_at=_require_tz_datetime(raw.get("expires_at")),
                consumed=consumed,
            )

    current_device_id = payload.get("current_device_id")
    if current_device_id is not None:
        if not isinstance(current_device_id, str):
            raise CorruptTrustStateError("trust state current_device_id must be a string")
        if current_device_id not in devices:
            raise CorruptTrustStateError(
                "trust state current_device_id references an unknown device"
            )
    if len(devices) > MAX_PINNED_DEVICES:
        raise CorruptTrustStateError("trust state device limit exceeded")
    invite = _invite_from_payload(payload.get("invite_record"))
    pending = _approval_from_payload(payload.get("pending_approval"), delivery=False)
    delivery = _approval_from_payload(payload.get("delivery"), delivery=True)
    if sum(x is not None for x in (invite, pending, delivery)) > 1:
        raise CorruptTrustStateError("trust state has competing ceremonies")
    if delivery is not None:
        device = devices.get(delivery.device_id)
        if device is None or (device.control_plane_id, device.stable_node_id) != (
            delivery.control_plane_id, delivery.stable_node_id
        ):
            raise CorruptTrustStateError("trust state delivery pin mismatch")
    return TrustState(
        connector_identity=connector_identity,
        pairing_epoch=pairing_epoch,
        revocation_epoch=revocation_epoch,
        devices=devices,
        current_device_id=current_device_id,
        pending_pairings=pending_pairings,
        invite_record=invite,
        pending_approval=pending,
        delivery=delivery,
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
                "credential_digest": device.credential_digest,
                "control_plane_id": device.control_plane_id,
                "stable_node_id": device.stable_node_id,
            }
            for device_id, device in sorted(state.devices.items())
        },
        "invite_record": _record_payload(state.invite_record),
        "pending_approval": _record_payload(state.pending_approval),
        "delivery": _record_payload(state.delivery),
        "pending_pairings": {
            device_name: {
                "code_digest": pending.code_digest,
                "expires_at": pending.expires_at.isoformat(),
                "consumed": pending.consumed,
            }
            for device_name, pending in sorted(state.pending_pairings.items())
        },
    }


class AtomicFileTrustStateStore:
    """An atomic, corruption-detecting, owner-only ``TrustStateStore`` with a
    monotonic companion generation/digest anchor (founder THR-097 seq163).

    ``default_state`` is returned when NO snapshot AND NO anchor exist (first
    run: a fresh deny-all state). Any present-but-corrupt/loose/unreadable/
    symlinked state or anchor, any partial pair (one file missing), any
    cross-generation pair, and any replayed older snapshot after a newer
    revocation/generation fails closed with :class:`StateStoreError` (or
    :class:`CorruptTrustStateError`).
    """

    def __init__(
        self,
        path: Path,
        default_state: TrustState,
        anchor_path: Path | None = None,
        lock_timeout: float = DEFAULT_LOCK_TIMEOUT_SECONDS,
    ) -> None:
        self._path = Path(path)
        self._anchor_path = (
            Path(anchor_path) if anchor_path is not None else Path(str(path) + ".anchor")
        )
        self._default_state = default_state
        self._lock_timeout = lock_timeout
        self._lock_owner = threading.local()

    # ── the one serialized mutation boundary (TASK-6045) ──────────────────

    @contextlib.contextmanager
    def transaction(self) -> Iterator["_TrustStateTransaction"]:
        """The ONE serialized inter-process mutation boundary for the
        trust-state pair: acquire the owner-only flock, LOAD the current
        persisted state, compute the next monotonic generation, yield the
        transaction handle, and on normal exit PUBLISH the snapshot+anchor
        pair at that generation — all under the same lock. An exception in
        the body publishes NOTHING (fail closed); ``tx.commit()`` publishes
        immediately (the deny side must be durable before a surfaced
        failure); ``tx.abort()`` publishes nothing on normal exit (a deny
        path has no side effect, not even a generation bump).

        Two processes can no longer both observe generation N, both pass a
        generation check, and both write N+1: the load, the generation
        computation, the snapshot publication, and the anchor publication
        are ONE serialized boundary (the TASK-6044 reviewer [HIGH] finding-1
        check-then-save race is closed). Reads take a shared lock on the
        same stable inode so they cannot sample an in-progress publication.
        A load by this transaction's owning thread reuses its exclusive
        lock; other threads and processes acquire their own shared lock.
        Stable torn pairs still fail closed exactly as before.

        The working copy handed to the transaction body is a DEEP COPY of
        the loaded state (mirroring ``InMemoryTrustStateStore``), so an
        abort or an exception discards the mutation even on FIRST-RUN —
        ``load()`` returns the mutable ``_default_state`` when no pair
        exists, and the transaction handle must never alias it (the
        TASK-6047 reviewer [HIGH] finding: a discarded first-run mutation
        leaked into later same-process loads/transactions).
        """
        with self._lock_file():
            tx = _TrustStateTransaction(self, self.load(), self._next_generation())
            try:
                yield tx
            except BaseException:
                # Never publish on failure (fail closed); a committed deny
                # side is already durable via ``tx.commit()``.
                raise
            else:
                tx.commit()

    @contextlib.contextmanager
    def _lock_file(self, *, shared: bool = False) -> Iterator[None]:
        """Owner-only INTER-PROCESS lock (``fcntl.flock``) over the state
        pair. The lock file lives beside the snapshot (``<state>.lock``),
        owner-only (created 0600; the parent directory is tightened to 0700
        exactly like the snapshot writes), and is a STABLE inode that is NEVER
        unlinked: flock is bound to the open file description, so unlinking
        a held lock file would let a second inode be locked concurrently
        (the classic lock-file unlink race). There is no stale-lock
        recovery procedure to perform: the kernel releases the flock
        automatically when the holder's descriptor closes or its process
        dies (crash/outage fail open to the next legitimate writer). A
        bounded non-blocking acquire fails closed with ``StateStoreError``
        when the lock stays contended past ``lock_timeout``.
        Writers acquire LOCK_EX; readers acquire LOCK_SH. The exclusive
        owner is recorded per store and thread only for nested load() calls.
        Symlink, hardlink, special, foreign-owner and loose lock inodes
        refuse, as does a lock identical to either pair inode. Existing
        lock metadata is never repaired; acquiring a lock must not chmod
        an aliased snapshot or anchor into an acceptable state.
        """
        lock_path = Path(str(self._path) + ".lock")
        fd: int | None = None
        try:
            with self._open_parent(lock_path) as parent_fd:
                fd = os.open(
                    lock_path.name,
                    os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK,
                    _STATE_FILE_MODE, dir_fd=parent_fd,
                )
                named = os.stat(lock_path.name, dir_fd=parent_fd, follow_symlinks=False)
                opened = os.fstat(fd)
                if (named.st_dev, named.st_ino) != (opened.st_dev, opened.st_ino):
                    raise OSError("trust state lock changed")
            lock_stat = os.fstat(fd)
            if (
                not stat.S_ISREG(lock_stat.st_mode)
                or lock_stat.st_nlink != 1
                or lock_stat.st_uid != os.geteuid()
                or stat.S_IMODE(lock_stat.st_mode) & 0o077
            ):
                raise OSError("unsafe trust state lock inode")
            for pair_path in (self._path, self._anchor_path):
                try:
                    pair_stat = pair_path.lstat()
                except FileNotFoundError:
                    continue
                if (lock_stat.st_dev, lock_stat.st_ino) == (
                    pair_stat.st_dev, pair_stat.st_ino
                ):
                    raise OSError("trust state lock aliases pair inode")
            deadline = time.monotonic() + self._lock_timeout
            while True:
                try:
                    mode = fcntl.LOCK_SH if shared else fcntl.LOCK_EX
                    fcntl.flock(fd, mode | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        os.close(fd)
                        raise StateStoreError(
                            "trust state lock contended; retry the ceremony operation"
                        )
                    time.sleep(_LOCK_ACQUIRE_SLEEP)
        except OSError as exc:
            if fd is not None:
                os.close(fd)
            raise StateStoreError("trust state lock unavailable") from exc
        if not shared:
            self._lock_owner.exclusive = True
        try:
            yield
        finally:
            if not shared:
                self._lock_owner.exclusive = False
            # Closing the descriptor releases the flock (also on crash).
            os.close(fd)

    # ── TrustStateStore protocol ──────────────────────────────────────────

    def load(self) -> TrustState:
        """Validate a stable snapshot+anchor pair under the shared lock.

        An in-progress writer must finish before any pair is sampled;
        contention times out with StateStoreError, never a default state.
        The same store's exclusive owner thread already holds the lock and
        reads directly to avoid self-deadlock inside transaction()/save().
        Lock acquisition never changes snapshot/anchor metadata; unsafe
        lock aliases refuse before reading. Every stable-pair validation
        remains, including refusal of loose snapshot/anchor permissions.
        """
        if getattr(self._lock_owner, "exclusive", False):
            return self._load_locked()
        with self._lock_file(shared=True):
            return self._load_locked()

    def _load_locked(self) -> TrustState:
        """Read and validate the pair while this thread holds SH or EX."""
        path = self._path
        anchor_path = self._anchor_path
        # Symlinks are rejected BEFORE existence is consulted: a dangling
        # symlink has Path.exists() == False and must never look like a
        # missing (first-run) file.
        if path.is_symlink():
            raise StateStoreError("trust state file must not be a symlink")
        if anchor_path.is_symlink():
            raise StateStoreError("trust state anchor must not be a symlink")
        path_exists = path.exists()
        anchor_exists = anchor_path.exists()
        if not path_exists and not anchor_exists:
            return self._default_state
        # Any partial pair is fail-closed: a snapshot without its anchor (or
        # an anchor without its snapshot) is an interrupted update or a
        # partial restore, never a trusted state.
        if not path_exists:
            raise CorruptTrustStateError(
                "trust state snapshot missing (anchor present)"
            )
        if not anchor_exists:
            raise CorruptTrustStateError(
                "trust state anchor missing (snapshot present)"
            )
        raw = self._read_owner_only(path, "trust state file")
        anchor = self._parse_anchor(self._read_owner_only(anchor_path, "trust state anchor"))
        try:
            envelope = json.loads(raw, object_pairs_hook=_unique_object)
        except (ValueError, TypeError, RecursionError) as exc:
            raise CorruptTrustStateError("trust state file is not valid JSON") from exc
        if not isinstance(envelope, dict):
            raise CorruptTrustStateError("trust state envelope must be an object")
        if set(envelope) != _ENVELOPE_KEYS:
            raise CorruptTrustStateError("trust state envelope has unknown keys")
        if type(envelope.get("version")) is not int or envelope["version"] not in (LEGACY_ENVELOPE_VERSION, ENVELOPE_VERSION):
            raise CorruptTrustStateError("trust state envelope version unsupported")
        if envelope.get("kind") != ENVELOPE_KIND:
            raise CorruptTrustStateError("trust state envelope kind mismatch")
        generation = envelope.get("generation")
        if (
            isinstance(generation, bool)
            or not isinstance(generation, int)
            or generation < 1
        ):
            raise CorruptTrustStateError(
                "trust state envelope generation must be a positive integer"
            )
        payload = envelope.get("payload")
        if not isinstance(payload, dict):
            raise CorruptTrustStateError("trust state payload must be an object")
        digest = envelope.get("digest")
        expected = hashlib.sha256(canonical_json(payload)).hexdigest()
        if not isinstance(digest, str) or digest != expected:
            raise CorruptTrustStateError("trust state integrity check failed")
        # ── companion anchor bind (monotonicity) ──────────────────────────
        # 1. The envelope generation must equal the anchored generation: a
        #    cross-generation pair (stale anchor / newer snapshot, or a
        #    restored old pair mislabeled) is rejected.
        if generation != anchor["generation"]:
            raise CorruptTrustStateError(
                "trust state generation does not match companion anchor"
            )
        # 2. The anchor digest must cover the EXACT snapshot bytes: any
        #    byte-level difference (a replayed older snapshot, a partially
        #    written snapshot, a re-serialized envelope) is rejected.
        if anchor["snapshot_digest"] != hashlib.sha256(raw).hexdigest():
            raise CorruptTrustStateError(
                "trust state snapshot does not match companion anchor"
            )
        state = _state_from_payload(payload, legacy=envelope["version"] == LEGACY_ENVELOPE_VERSION)
        for record in (state.invite_record, state.pending_approval, state.delivery):
            if record is not None and record.generation > generation:
                raise CorruptTrustStateError("trust state ceremony generation rollback")
        return state

    def save(self, state: TrustState) -> None:
        """Publish *state* at the next monotonic generation, under the SAME
        owner-only inter-process lock as ``transaction()`` — a direct save
        (install/test writers) can never interleave with a ceremony
        transaction and can never observe/regress the anchored generation.
        """
        with self._lock_file():
            self.load()  # a corrupt/torn pair cannot be overwritten into acceptance
            self._write_pair(state, self._next_generation())

    def _write_pair(self, state: TrustState, generation: int) -> None:
        """Publish the snapshot then the companion anchor at *generation*
        (snapshot first, then anchor; a crash between the two leaves a
        mismatched pair that ``load()`` rejects — never a stale state).
        Only ever called while holding the inter-process lock."""
        payload = _payload_from_state(state)
        _state_from_payload(payload)  # validate BEFORE either durable file changes
        envelope = {
            "version": ENVELOPE_VERSION,
            "kind": ENVELOPE_KIND,
            "generation": generation,
            "payload": payload,
            "digest": hashlib.sha256(canonical_json(payload)).hexdigest(),
        }
        envelope_bytes = json.dumps(
            envelope, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
        self._write_bytes(self._path, envelope_bytes)
        anchor_payload = {
            "version": ANCHOR_VERSION,
            "kind": ANCHOR_KIND,
            "generation": generation,
            "snapshot_digest": hashlib.sha256(envelope_bytes).hexdigest(),
        }
        anchor = dict(anchor_payload)
        anchor["digest"] = hashlib.sha256(canonical_json(anchor_payload)).hexdigest()
        self._write_bytes(
            self._anchor_path,
            json.dumps(
                anchor, sort_keys=True, separators=(",", ":"), ensure_ascii=False
            ).encode("utf-8"),
        )

    def _render(self, state: TrustState, generation: int = 1) -> bytes:
        """Serialize *state* exactly as ``save`` would at *generation*
        (deterministic; used by tests to construct/verify envelopes)."""
        payload = _payload_from_state(state)
        _state_from_payload(payload)  # validate BEFORE either durable file changes
        envelope = {
            "version": ENVELOPE_VERSION,
            "kind": ENVELOPE_KIND,
            "generation": generation,
            "payload": payload,
            "digest": hashlib.sha256(canonical_json(payload)).hexdigest(),
        }
        return json.dumps(
            envelope, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")

    # ── anchor / generation helpers ───────────────────────────────────────

    def _next_generation(self) -> int:
        """The next monotonic generation from the on-disk anchor. A snapshot
        without an anchor is partial state and must never be silently
        resurrected at a fresh generation."""
        anchor_path = self._anchor_path
        if not anchor_path.exists():
            if self._path.exists():
                raise StateStoreError(
                    "trust state anchor missing; cannot determine next generation "
                    "(refusing partial state)"
                )
            return 1
        anchor = self._parse_anchor(self._read_owner_only(anchor_path, "trust state anchor"))
        return anchor["generation"] + 1

    def anchored_generation(self) -> int | None:
        """The generation recorded in the on-disk companion anchor, or ``None``
        when NO pair exists (first run). Used to compare pairs (e.g. install
        re-seeding) WITHOUT loading the snapshot: a partial pair (snapshot
        without anchor) or a corrupt/loose/symlinked anchor fails closed — a
        generation must never be guessed from partial state."""
        if not self._path.exists() and not self._anchor_path.exists():
            return None
        anchor = self._parse_anchor(
            self._read_owner_only(self._anchor_path, "trust state anchor")
        )
        return anchor["generation"]

    def _parse_anchor(self, raw: bytes) -> dict[str, int | str]:
        """Strictly validate the companion anchor; any violation fails closed."""
        try:
            anchor = json.loads(raw, object_pairs_hook=_unique_object)
        except (ValueError, TypeError, RecursionError) as exc:
            raise CorruptTrustStateError("trust state anchor is not valid JSON") from exc
        if not isinstance(anchor, dict):
            raise CorruptTrustStateError("trust state anchor must be an object")
        if set(anchor) != _ANCHOR_KEYS:
            raise CorruptTrustStateError("trust state anchor has unknown keys")
        if type(anchor.get("version")) is not int or anchor["version"] != ANCHOR_VERSION:
            raise CorruptTrustStateError("trust state anchor version unsupported")
        if anchor.get("kind") != ANCHOR_KIND:
            raise CorruptTrustStateError("trust state anchor kind mismatch")
        generation = anchor.get("generation")
        if (
            isinstance(generation, bool)
            or not isinstance(generation, int)
            or generation < 1
        ):
            raise CorruptTrustStateError(
                "trust state anchor generation must be a positive integer"
            )
        snapshot_digest = anchor.get("snapshot_digest")
        if not isinstance(snapshot_digest, str) or len(snapshot_digest) != 64:
            raise CorruptTrustStateError("trust state anchor snapshot_digest invalid")
        digest = anchor.get("digest")
        expected = hashlib.sha256(
            canonical_json(
                {
                    "version": ANCHOR_VERSION,
                    "kind": ANCHOR_KIND,
                    "generation": generation,
                    "snapshot_digest": snapshot_digest,
                }
            )
        ).hexdigest()
        if not isinstance(digest, str) or digest != expected:
            raise CorruptTrustStateError("trust state anchor integrity check failed")
        return {"generation": generation, "snapshot_digest": snapshot_digest}

    @contextlib.contextmanager
    def _open_parent(self, path: Path) -> Iterator[int]:
        """Open literal parents without symlinks; never repair existing modes."""
        absolute = path.absolute()
        fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
        try:
            for part in absolute.parts[1:-1]:
                try:
                    child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                except FileNotFoundError:
                    os.mkdir(part, _STATE_DIR_MODE, dir_fd=fd)
                    child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd)
                fd = child
            metadata = os.fstat(fd)
            if metadata.st_uid != os.geteuid() or stat.S_IMODE(metadata.st_mode) != _STATE_DIR_MODE:
                raise OSError("unsafe trust state parent")
            yield fd
        finally:
            os.close(fd)

    @staticmethod
    def _file_signature(metadata: os.stat_result) -> tuple[int, ...]:
        # Reading may advance atime; identity/authority/content must stay fixed.
        return (metadata.st_dev, metadata.st_ino, metadata.st_mode,
                metadata.st_uid, metadata.st_gid, metadata.st_nlink,
                metadata.st_size, metadata.st_mtime_ns, metadata.st_ctime_ns)

    @staticmethod
    def _safe_file(metadata: os.stat_result) -> bool:
        return (stat.S_ISREG(metadata.st_mode) and metadata.st_nlink == 1
                and metadata.st_uid == os.geteuid()
                and stat.S_IMODE(metadata.st_mode) == _STATE_FILE_MODE
                and metadata.st_size <= MAX_STATE_BYTES)

    def _read_owner_only(self, path: Path, what: str) -> bytes:
        """Bounded read of one no-follow, owner-only, single-link stable inode."""
        try:
            with self._open_parent(path) as parent_fd:
                fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent_fd)
                try:
                    before = os.fstat(fd)
                    named = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
                    if not self._safe_file(before) or self._file_signature(before) != self._file_signature(named):
                        raise OSError("unsafe trust state inode")
                    chunks: list[bytes] = []
                    remaining = MAX_STATE_BYTES + 1
                    while remaining:
                        chunk = os.read(fd, min(65536, remaining))
                        if not chunk:
                            break
                        chunks.append(chunk)
                        remaining -= len(chunk)
                    after = os.fstat(fd)
                    named_after = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
                    if (self._file_signature(before) != self._file_signature(after)
                            or self._file_signature(after) != self._file_signature(named_after) or remaining == 0):
                        raise OSError("trust state inode changed or oversized")
                    return b"".join(chunks)
                finally:
                    os.close(fd)
        except OSError as exc:
            raise StateStoreError(f"{what} unavailable") from exc

    def _write_bytes(self, path: Path, data: bytes) -> None:
        """Exclusive temporary inode, fsync and descriptor-relative publication."""
        if len(data) > MAX_STATE_BYTES:
            raise StateStoreError("trust state exceeds size bound")
        try:
            with self._open_parent(path) as parent_fd:
                try:
                    previous = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
                except FileNotFoundError:
                    previous = None
                if previous is not None and not self._safe_file(previous):
                    raise OSError("unsafe trust state destination")
                temporary = f".{path.name}.{secrets.token_hex(16)}.tmp"
                fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             _STATE_FILE_MODE, dir_fd=parent_fd)
                try:
                    with os.fdopen(fd, "wb") as stream:
                        stream.write(data)
                        stream.flush()
                        os.fsync(stream.fileno())
                    try:
                        current = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
                    except FileNotFoundError:
                        current = None
                    if (current is None) != (previous is None) or (
                        current is not None and self._file_signature(current) != self._file_signature(previous)
                    ):
                        raise OSError("trust state destination changed")
                    os.replace(temporary, path.name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
                    os.fsync(parent_fd)
                finally:
                    try:
                        os.unlink(temporary, dir_fd=parent_fd)
                    except FileNotFoundError:
                        pass
        except OSError as exc:
            raise StateStoreError("trust state write failed") from exc


class _TrustStateTransaction:
    """Handle yielded by ``AtomicFileTrustStateStore.transaction()``.

    ``state`` is a DEEP COPY of the working state loaded under the
    inter-process lock (never the store's mutable ``_default_state`` — an
    abort or exception must discard the mutation, not leak it into later
    same-process loads); ``generation`` is the exact monotonic generation
    this commit publishes. ``commit()`` publishes the pair immediately
    (idempotent) — used when the deny side must be durable BEFORE a failure
    is surfaced; ``abort()`` suppresses the normal-exit publish (a deny
    path has no side effect, not even a generation bump). An exception in
    the transaction body always suppresses the publish.
    """

    __slots__ = ("_store", "_state", "_generation", "_published", "_aborted")

    def __init__(
        self, store: AtomicFileTrustStateStore, state: TrustState, generation: int
    ) -> None:
        self._store = store
        self._state = copy.deepcopy(state)
        self._generation = generation
        self._published = False
        self._aborted = False

    @property
    def state(self) -> TrustState:
        return self._state

    @property
    def generation(self) -> int:
        return self._generation

    def commit(self) -> None:
        """Publish the snapshot+anchor pair NOW at the transaction's
        generation (idempotent; a later abort cannot unpublish)."""
        if self._published or self._aborted:
            return
        self._store._write_pair(self._state, self._generation)
        self._published = True

    def abort(self) -> None:
        """Suppress the normal-exit publish: the mutation is discarded, no
        pair write and no generation bump occurs."""
        self._aborted = True
