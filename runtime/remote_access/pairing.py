"""Supported-DIY local pairing ceremony engine (THR-097 Unit 3A).

The customer-owned-network (DIY) lane pairs a client device to a home through
an EXPLICIT LOCAL ceremony, matching the inherited THR-034 wire contract the
signed macOS ``ClientBridge`` already speaks:

1. **Issue** — the home operator runs ``pair --device <name>``; the engine
   mints a SHORT-LIVED, SINGLE-USE pairing code (8 alphanumeric chars, like
   the legacy Swift ``RealPairingStore``) and persists only its sha256 digest.
2. **Redeem** — the away client sends ``POST /pair`` with the code in the
   body (the THR-034 contract). On success the engine consumes the code and
   issues a per-device ``hrpair_<hex>`` credential, persisting only its
   digest and recording a ``DeviceAuthorization`` at a MONOTONIC epoch.
   Re-pairing the same device mints a NEW credential at a HIGHER epoch —
   the old authority is invalidated (its digest is replaced and epoch
   rollback is rejected). An expired, consumed, or unknown code denies
   identically (no credential-existence oracle).
3. **Enforce** — every subsequent request presents
   ``X-HappyRanch-Device-Credential``; :class:`PairingCredentialVerifier`
   (a production :class:`DeviceProofVerifier` for the DIY lane) checks the
   digest against the current trust state. Absent / unknown / revoked /
   expired / removed credentials ALL deny with the identical category —
   the connector never reveals whether a device exists.
4. **Revoke / remove / recover** — ``revoke`` (one device or all) closes
   live streams through the authoritative ``RevocationCoordinator`` and
   persists the monotonic revocation epoch (survives restart);
   ``remove_device`` REVOKES FIRST (live streams close, epoch advances,
   persisted) and THEN deletes the record — a removed/lost device can never
   retain an open stream and the revocation survives restart; factory-reset
   recovery is an explicit operator action (delete BOTH snapshot+anchor
   files) handled by the CLI, per the store's crash-consistency contract.

Security properties (fixed invariants, THR-034 ceiling preserved):

- Only sha256 digests of codes/credentials are ever persisted, logged, or
  rendered; raw values exist once (issue print, redeem response) and never
  in argv, env, logs, diagnostics, errors, fixtures, or audit.
- The daemon bearer is never involved in pairing; it is read by the
  connector only on the final 127.0.0.1 hop (unchanged gateway contract).
- No existence oracle: denied pairings are externally identical for
  absent/unknown/expired/consumed/removed/revoked credentials.
- Epochs are monotonic; persisted revocation survives restart and replay of
  an older trust snapshot is rejected by the companion anchor.
"""
from __future__ import annotations

import hashlib
import secrets
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable

from runtime.remote_access.authorization import (
    DeviceAuthorization,
    PendingPairing,
    RevocationSignal,
    TrustState,
)
from runtime.remote_access.identity import (
    ConnectorIdentity,
    DeviceProof,
    ProofVerdict,
)
from runtime.remote_access.revocation import RevocationCoordinator, RevocationIncomplete
from runtime.remote_access.state import TrustStateStore
from runtime.remote_access.streams import StreamRegistry

# THR-034 wire contract constants (match the legacy Swift store so the
# existing macOS ClientBridge works unchanged).
CREDENTIAL_PREFIX = "hrpair_"
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O/1/I — human-friendly
CODE_LENGTH = 8
DEFAULT_CODE_TTL_SECONDS = 300  # 5 minutes (matches RealPairingStore)
DEFAULT_CREDENTIAL_TTL_DAYS = 365
_CREDENTIAL_RANDOM_BYTES = 16  # 32 hex chars after the prefix


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def generate_pairing_code(*, rng: Callable[[int], str] | None = None) -> str:
    """A fresh short-lived pairing code (8 chars from a confusion-free
    alphabet). ``rng`` is a test seam returning a fixed string."""
    if rng is not None:
        return rng(CODE_LENGTH)
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))


def generate_device_credential(*, rng: Callable[[int], str] | None = None) -> str:
    """A fresh per-device ``hrpair_`` credential (never persisted raw)."""
    if rng is not None:
        return f"{CREDENTIAL_PREFIX}{rng(_CREDENTIAL_RANDOM_BYTES)}"
    return f"{CREDENTIAL_PREFIX}{secrets.token_hex(_CREDENTIAL_RANDOM_BYTES)}"


@dataclass(frozen=True)
class DeviceInfo:
    """Operator-facing redacted device record (never digests/credentials)."""

    device_id: str
    authorization_epoch: int
    expires_at: datetime
    revoked: bool
    paired: bool
    credential_digest_present: bool


@dataclass(frozen=True)
class PairingIssued:
    """Outcome of ``pair --device``: the one-time code (printed ONCE by the
    CLI, never logged) plus its expiry. The digest is what is stored."""

    device_name: str
    code: str
    expires_at: datetime


@dataclass(frozen=True)
class RevokeOutcome:
    """Outcome of a revocation: the applied epoch and whether every live
    stream closed (mirrors ``RevocationIncomplete`` semantics without
    leaking stream ids)."""

    epoch: int
    complete: bool
    device_id: str | None


class PairingError(Exception):
    """Pairing ceremony failure (fail closed)."""


class PairingManager:
    """The local pairing ceremony + credential lifecycle for the DIY lane.

    All state persists through the approved ``TrustStateStore`` (digests
    only; atomic owner-only files; companion monotonic anchor). ``now_fn``
    is injectable for deterministic tests.
    """

    def __init__(
        self,
        *,
        state_store: TrustStateStore,
        identity: ConnectorIdentity,
        now_fn: Callable[[], datetime] | None = None,
        code_ttl_seconds: int = DEFAULT_CODE_TTL_SECONDS,
        credential_ttl_days: int = DEFAULT_CREDENTIAL_TTL_DAYS,
        registry: StreamRegistry | None = None,
        signal: RevocationSignal | None = None,
    ) -> None:
        self._state_store = state_store
        self._identity = identity
        self._now_fn = now_fn or (lambda: datetime.now(timezone.utc))
        self._code_ttl_seconds = code_ttl_seconds
        self._credential_ttl_days = credential_ttl_days
        self._registry = registry or StreamRegistry()
        self._signal = signal
        # Serializes every state-mutating ceremony operation (issue/redeem/
        # revoke/remove) — the load->mutate->save sequence is one critical
        # section, so concurrent redemption (ThreadingHTTPServer threads)
        # yields EXACTLY ONE credential and can never lose/corrupt state
        # (TASK-6039 reviewer [HIGH] finding 4).
        self._tx_lock = threading.Lock()

    # ── state access ──────────────────────────────────────────────────────

    def load_state(self) -> TrustState:
        return self._state_store.load()

    def _pin_generation(self) -> int | None:
        """The anchored generation at load time (None when the store does
        not expose it, e.g. the in-memory harness store)."""
        anchored = getattr(self._state_store, "anchored_generation", None)
        if anchored is None:
            return None
        return anchored()

    def _save(self, state: TrustState, pinned_generation: int | None = None) -> None:
        """Save *state* with a cross-process optimistic-concurrency guard:
        when the store exposes the anchored generation and a generation was
        pinned at load time, verify it did NOT advance before saving. A
        concurrent writer in ANOTHER process fails closed rather than
        silently overwriting newer state (no lost pairing/revocation update)
        — the in-process race is already excluded by ``_tx_lock``."""
        if pinned_generation is not None:
            anchored = getattr(self._state_store, "anchored_generation", None)
            current = anchored() if anchored is not None else None
            if current != pinned_generation:
                raise PairingError(
                    "trust state changed concurrently; retry the ceremony "
                    "operation"
                )
        self._state_store.save(state)

    # ── ceremony: issue / redeem ──────────────────────────────────────────

    def issue_pairing_code(self, device_name: str) -> PairingIssued:
        """Mint a single-use, expiring pairing code for *device_name* and
        persist ONLY its digest. The raw code is returned once for the CLI
        to print; it is never stored or logged. Serialized with every other
        ceremony mutation."""
        with self._tx_lock:
            name = self._validate_device_name(device_name)
            now = self._now_fn()
            code = generate_pairing_code()
            pinned = self._pin_generation()
            state = self.load_state()
            state.pending_pairings[name] = PendingPairing(
                code_digest=_sha256(code),
                expires_at=now + timedelta(seconds=self._code_ttl_seconds),
                consumed=False,
            )
            self._save(state, pinned)
            return PairingIssued(
                device_name=name,
                code=code,
                expires_at=state.pending_pairings[name].expires_at,
            )

    def redeem_pairing(self, code: str) -> str | None:
        """Redeem a one-time code (THR-034 ``POST /pair`` contract).

        Validates digest, expiry, and single-use; consumes the token;
        records a ``DeviceAuthorization`` at the next monotonic epoch with a
        fresh credential (re-pairing the same device replaces the old
        authority — epoch advances, old digest replaced); returns the raw
        credential exactly once. Any failure (unknown, expired, consumed,
        blank, or a concurrent cross-process writer) returns ``None`` —
        externally identical (no existence oracle).

        SERIALIZED: the whole load->mutate->save runs under the ceremony
        transaction lock, so concurrent redemption of the SAME code yields
        EXACTLY ONE credential (the second caller observes the consumed
        code) and concurrent redemption of different codes can never lose
        or corrupt persisted state (TASK-6039 finding 4)."""
        with self._tx_lock:
            if not code or not code.strip():
                return None
            now = self._now_fn()
            digest = _sha256(code)
            pinned = self._pin_generation()
            state = self.load_state()
            pending = self._find_pending(state, digest, now)
            if pending is None:
                return None
            # Single-use consumption is durable BEFORE the credential is
            # usable: a replayed code denies identically to an unknown one.
            name = _find_name(state, digest)
            state.pending_pairings[name] = PendingPairing(
                code_digest=pending.code_digest,
                expires_at=pending.expires_at,
                consumed=True,
            )
            credential = generate_device_credential()
            next_epoch = max(state.pairing_epoch, state.revocation_epoch) + 1
            device = DeviceAuthorization(
                device_id=name,
                tenant_id=self._identity.tenant_id,
                home_id=self._identity.home_id,
                authorization_epoch=next_epoch,
                expires_at=now + timedelta(days=self._credential_ttl_days),
                credential_digest=_sha256(credential),
            )
            state.apply_pairing(device)
            # Supported-DIY is multi-device: every successfully paired device
            # is a current device (do not let the last-paired device shadow
            # earlier ones via ``current_device_id``).
            state.current_device_id = None
            try:
                self._save(state, pinned)
            except PairingError:
                # A concurrent writer in another process advanced the state
                # between our load and save: fail closed with the identical
                # deny (never mint a credential from stale state, never
                # overwrite the newer state). The code is still pending;
                # the client may retry.
                return None
            return credential

    # ── revocation / removal / recovery ───────────────────────────────────

    def revoke(self, device_id: str | None = None) -> RevokeOutcome:
        """Revoke one device (lost-device flow) or ALL devices, through the
        authoritative ``RevocationCoordinator`` transaction: live streams
        close (fail closed) BEFORE the persisted state change, and the epoch
        advances monotonically. ``device_id=None`` revokes every device.
        Returns the applied epoch and closure completeness. Serialized with
        every other ceremony mutation."""
        with self._tx_lock:
            pinned = self._pin_generation()
            state = self.load_state()
            next_epoch = max(state.pairing_epoch, state.revocation_epoch) + 1
            coordinator = RevocationCoordinator(state, self._registry, signal=self._signal)
            try:
                if device_id is None:
                    coordinator.revoke(next_epoch)
                else:
                    # Per-device revocation runs the SAME authoritative
                    # transaction shape: every live stream closes
                    # (conservative, fail closed — device-level stream
                    # attribution is a later refinement), then the targeted
                    # per-device state change.
                    coordinator.revoke_device(device_id, next_epoch)
            except RevocationIncomplete as exc:
                # Deny side applied; closure imperfect. Persist and surface
                # the incompleteness truthfully (never claim full success).
                self._save(state, pinned)
                raise PairingError(
                    f"revocation epoch {exc.applied_epoch} applied; "
                    f"{len(exc.stream_ids)} stream(s) failed to close"
                ) from exc
            self._save(state, pinned)
            return RevokeOutcome(
                epoch=state.revocation_epoch, complete=True, device_id=device_id
            )

    def remove_device(self, device_id: str) -> None:
        """Remove a paired device AND its credential entirely (credential
        removal / lost-device recovery).

        REVOKE-FIRST (TASK-6039 reviewer [HIGH] finding 3): the device is
        revoked through the authoritative ``RevocationCoordinator``
        transaction — live streams close fail-closed and the revocation
        epoch advances (persisted, survives restart) — BEFORE the authority
        record and its credential digest are deleted. A removed/lost device
        can never retain an open stream, and the revocation is durable even
        if the process dies mid-removal. The removed credential thereafter
        denies identically to an absent one (no existence oracle).

        If a live stream fails to close, the removal fails closed with
        ``PairingError`` (the device is revoked and the epoch persisted, but
        the record is NOT deleted while closure is uncertain); the operator
        restarts the connector to clear the sealed registry and retries."""
        with self._tx_lock:
            pinned = self._pin_generation()
            state = self.load_state()
            next_epoch = max(state.pairing_epoch, state.revocation_epoch) + 1
            coordinator = RevocationCoordinator(state, self._registry, signal=self._signal)
            try:
                coordinator.revoke_device(device_id, next_epoch)
            except RevocationIncomplete as exc:
                self._save(state, pinned)
                raise PairingError(
                    f"removal epoch {exc.applied_epoch} applied; "
                    f"{len(exc.stream_ids)} stream(s) failed to close"
                ) from exc
            state.devices.pop(device_id, None)
            state.pending_pairings.pop(device_id, None)
            if state.current_device_id == device_id:
                state.current_device_id = None
            self._save(state, pinned)

    def list_devices(self) -> list[DeviceInfo]:
        """Redacted operator-facing device list — NEVER digests or
        credentials; only present/absent booleans."""
        state = self.load_state()
        now = self._now_fn()
        return [
            DeviceInfo(
                device_id=device.device_id,
                authorization_epoch=device.authorization_epoch,
                expires_at=device.expires_at,
                revoked=device.revoked,
                paired=not device.revoked and now <= device.expires_at,
                credential_digest_present=device.credential_digest is not None,
            )
            for device in sorted(state.devices.values(), key=lambda d: d.device_id)
        ]

    def pairing_status(self) -> dict:
        """Truthful, secret-free lifecycle summary for the operator/UI."""
        state = self.load_state()
        now = self._now_fn()
        devices = self.list_devices()
        return {
            "connector_identity": {
                "tenant_id": self._identity.tenant_id,
                "home_id": self._identity.home_id,
                "connector_id": self._identity.connector_id,
            },
            "pairing_epoch": state.pairing_epoch,
            "revocation_epoch": state.revocation_epoch,
            "devices": [
                {
                    "device_id": d.device_id,
                    "state": "revoked" if d.revoked else ("paired" if d.paired else "expired"),
                    "authorization_epoch": d.authorization_epoch,
                    "expires_at": d.expires_at.isoformat(),
                }
                for d in devices
            ],
            "pending_pairings": [
                {
                    "device": name,
                    "expires_at": p.expires_at.isoformat(),
                    "consumed": p.consumed,
                    "state": "consumed" if p.consumed else ("expired" if now > p.expires_at else "pending"),
                }
                for name, p in sorted(state.pending_pairings.items())
            ],
        }

    # ── verifier ──────────────────────────────────────────────────────────

    def credential_verifier(self) -> "PairingCredentialVerifier":
        """A production :class:`DeviceProofVerifier` for the DIY lane bound to
        the CURRENT persisted trust state."""
        return PairingCredentialVerifier(self.load_state())

    def find_device_by_credential(self, credential: str) -> DeviceAuthorization | None:
        """Resolve a presented credential to its paired device by digest;
        returns None for absent/revoked/expired (identical deny path)."""
        if not credential or not credential.strip():
            return None
        digest = _sha256(credential)
        state = self.load_state()
        now = self._now_fn()
        for device in state.devices.values():
            if device.credential_digest != digest:
                continue
            if device.revoked or now > device.expires_at:
                return None
            return device
        return None

    @staticmethod
    def _validate_device_name(device_name: str) -> str:
        name = (device_name or "").strip()
        if not name:
            raise PairingError("device name must be non-empty")
        if len(name) > 64:
            raise PairingError("device name too long")
        return name

    def _find_pending(self, state: TrustState, digest: str, now: datetime) -> PendingPairing | None:
        """Return the pending pairing whose digest matches, only when NOT
        consumed and NOT expired; anything else (unknown/consumed/expired)
        is identical ``None`` (no existence oracle)."""
        for pending in state.pending_pairings.values():
            if pending.code_digest != digest:
                continue
            if pending.consumed or now > pending.expires_at:
                return None
            return pending
        return None


def _find_name(state: TrustState, digest: str) -> str:
    for name, pending in state.pending_pairings.items():
        if pending.code_digest == digest:
            return name
    raise PairingError("pending pairing not found")  # unreachable after _find_pending


class PairingCredentialVerifier:
    """Production device-proof verifier for the Supported-DIY lane.

    The adapter resolves the presented ``X-HappyRanch-Device-Credential`` to
    the paired device (``PairingManager.find_device_by_credential`` — digest
    match, revoked/expiry fail closed) and builds the proof with the RESOLVED
    device identity. This verifier then re-checks the resolved record
    (defense in depth at the seam): the device must exist, carry a
    credential digest, not be revoked or expired, and match the proof's
    audience. Unknown, revoked, expired, removed, and digest-less records
    ALL return ``identity_unestablished`` — the connector never reveals
    whether a device exists (contract §8 no-existence-oracle;
    CRED-003/CRED-003b discipline).
    """

    def __init__(self, state: TrustState) -> None:
        self._state = state

    def verify(self, proof: DeviceProof, connector_identity: ConnectorIdentity, now: datetime) -> ProofVerdict:
        del connector_identity
        device = self._state.devices.get(proof.device_id)
        if device is None or device.credential_digest is None:
            return ProofVerdict(ok=False, reason="identity_unestablished")
        if device.revoked or now > device.expires_at:
            return ProofVerdict(ok=False, reason="identity_unestablished")
        if device.tenant_id != proof.tenant_id or device.home_id != proof.home_id:
            return ProofVerdict(ok=False, reason="identity_unestablished")
        return ProofVerdict(ok=True)
