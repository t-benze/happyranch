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
anchor. A crash between the two writes leaves a mismatched pair that load()
rejects — never a silently-accepted stale state. Recovery is an explicit
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
import hashlib
import json
import os
import stat
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

import fcntl

from runtime.remote_access.authorization import DeviceAuthorization, PendingPairing, TrustState
from runtime.remote_access.identity import ConnectorIdentity
from runtime.remote_access.policy import canonical_json
# The non-normative local envelope (not a founder-gated schema). Bumping the
# version is a breaking change to the recovery aid; unknown versions fail
# closed rather than being guessed.
#
# v2 adds the monotonic ``generation`` field (envelope-level) bound to the
# companion anchor. v1 envelopes (no generation, no anchor) are rejected as
# unsupported — a v1 snapshot can no longer be trusted without an anchor.
ENVELOPE_VERSION = 2
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
    }
)
# credential_digest is OPTIONAL (Unit 3A additive): pre-3A envelopes that
# predate it must still load (absent = None); a present value is validated
# strictly. Unknown keys are still rejected via ``_DEVICE_KEYS``.
_DEVICE_REQUIRED_KEYS = _DEVICE_KEYS - {"credential_digest"}
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
        missing_device = _DEVICE_REQUIRED_KEYS - set(raw)
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
        devices[device_id] = DeviceAuthorization(
            device_id=stored_id,
            tenant_id=_require_str(raw, "tenant_id"),
            home_id=_require_str(raw, "home_id"),
            authorization_epoch=_require_int(raw, "authorization_epoch"),
            expires_at=_require_tz_datetime(raw.get("expires_at")),
            revoked=revoked,
            credential_digest=credential_digest,
        )

    pending_raw = payload.get("pending_pairings")
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
    return TrustState(
        connector_identity=connector_identity,
        pairing_epoch=pairing_epoch,
        revocation_epoch=revocation_epoch,
        devices=devices,
        current_device_id=current_device_id,
        pending_pairings=pending_pairings,
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
            }
            for device_id, device in sorted(state.devices.items())
        },
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
        check-then-save race is closed). Reads stay lock-free and fail
        closed on a torn pair exactly as before.
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
    def _lock_file(self) -> Iterator[None]:
        """Owner-only INTER-PROCESS lock (``fcntl.flock``) over the state
        pair. The lock file lives beside the snapshot (``<state>.lock``),
        owner-only (0600; the parent directory is tightened to 0700 exactly
        like the snapshot writes), and is a STABLE inode that is NEVER
        unlinked: flock is bound to the open file description, so unlinking
        a held lock file would let a second inode be locked concurrently
        (the classic lock-file unlink race). There is no stale-lock
        recovery procedure to perform: the kernel releases the flock
        automatically when the holder's descriptor closes or its process
        dies (crash/outage fail open to the next legitimate writer). A
        bounded non-blocking acquire fails closed with ``StateStoreError``
        when the lock stays contended past ``lock_timeout``.
        """
        lock_path = Path(str(self._path) + ".lock")
        parent = lock_path.parent
        fd: int | None = None
        try:
            parent.mkdir(parents=True, exist_ok=True)
            os.chmod(parent, _STATE_DIR_MODE)
            fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, _STATE_FILE_MODE)
            # Tighten unconditionally: a pre-existing lock file with loose
            # permissions holds no data — tightening is always safe and the
            # file must never be group/world readable.
            os.fchmod(fd, _STATE_FILE_MODE)
            deadline = time.monotonic() + self._lock_timeout
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise StateStoreError(
                            "trust state lock contended; retry the ceremony operation"
                        )
                    time.sleep(_LOCK_ACQUIRE_SLEEP)
        except OSError as exc:
            if fd is not None:
                os.close(fd)
            raise StateStoreError("trust state lock unavailable") from exc
        try:
            yield
        finally:
            # Closing the descriptor releases the flock (also on crash).
            os.close(fd)

    # ── TrustStateStore protocol ──────────────────────────────────────────

    def load(self) -> TrustState:
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
            envelope = json.loads(raw)
        except (ValueError, TypeError) as exc:
            raise CorruptTrustStateError("trust state file is not valid JSON") from exc
        if not isinstance(envelope, dict):
            raise CorruptTrustStateError("trust state envelope must be an object")
        unknown = set(envelope) - _ENVELOPE_KEYS
        if unknown:
            raise CorruptTrustStateError("trust state envelope has unknown keys")
        if envelope.get("version") != ENVELOPE_VERSION:
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
        return _state_from_payload(payload)

    def save(self, state: TrustState) -> None:
        """Publish *state* at the next monotonic generation, under the SAME
        owner-only inter-process lock as ``transaction()`` — a direct save
        (install/test writers) can never interleave with a ceremony
        transaction and can never observe/regress the anchored generation.
        """
        with self._lock_file():
            self._write_pair(state, self._next_generation())

    def _write_pair(self, state: TrustState, generation: int) -> None:
        """Publish the snapshot then the companion anchor at *generation*
        (snapshot first, then anchor; a crash between the two leaves a
        mismatched pair that ``load()`` rejects — never a stale state).
        Only ever called while holding the inter-process lock."""
        payload = _payload_from_state(state)
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
            anchor = json.loads(raw)
        except (ValueError, TypeError) as exc:
            raise CorruptTrustStateError("trust state anchor is not valid JSON") from exc
        if not isinstance(anchor, dict):
            raise CorruptTrustStateError("trust state anchor must be an object")
        unknown = set(anchor) - _ANCHOR_KEYS
        if unknown:
            raise CorruptTrustStateError("trust state anchor has unknown keys")
        if anchor.get("version") != ANCHOR_VERSION:
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

    def _read_owner_only(self, path: Path, what: str) -> bytes:
        """Read a state/anchor file with the owner-only contract enforced:
        symlink, non-file, loose permissions, or unreadable all fail closed."""
        if path.is_symlink():
            raise StateStoreError(f"{what} must not be a symlink")
        if not path.is_file():
            raise StateStoreError(f"{what} path is not a file")
        try:
            mode = stat.S_IMODE(path.stat().st_mode)
        except OSError as exc:
            raise StateStoreError(f"{what} unreadable") from exc
        if mode & 0o077:
            raise StateStoreError(f"{what} permissions too loose")
        try:
            return path.read_bytes()
        except OSError as exc:
            raise StateStoreError(f"{what} unreadable") from exc

    def _write_bytes(self, path: Path, data: bytes) -> None:
        """Atomic owner-only write: temp file in the same directory, fsync,
        rename, directory fsync. A crash never leaves a half-written file."""
        parent = path.parent
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
                prefix=f".{path.name}.", suffix=".tmp", dir=parent
            )
            os.fchmod(fd, _STATE_FILE_MODE)
            with os.fdopen(fd, "wb") as fh:
                fd = None
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_name, path)
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


class _TrustStateTransaction:
    """Handle yielded by ``AtomicFileTrustStateStore.transaction()``.

    ``state`` is the working copy loaded under the inter-process lock;
    ``generation`` is the exact monotonic generation this commit publishes.
    ``commit()`` publishes the pair immediately (idempotent) — used when the
    deny side must be durable BEFORE a failure is surfaced; ``abort()``
    suppresses the normal-exit publish (a deny path has no side effect, not
    even a generation bump). An exception in the transaction body always
    suppresses the publish.
    """

    __slots__ = ("_store", "_state", "_generation", "_published", "_aborted")

    def __init__(
        self, store: AtomicFileTrustStateStore, state: TrustState, generation: int
    ) -> None:
        self._store = store
        self._state = state
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
