"""In-memory registration token store + conformance state machine.

PR-1 of THR-052 self-registration — daemon/auth foundation ONLY.

No DB schema, no migration. Tokens are stored hashed (sha256, never plaintext
at rest in process memory). Daemon restart invalidates outstanding tokens
(acceptable: they are short-lived and the founder re-mints in one click).

Token values carry the ``hrreg_`` prefix — distinct from the master bearer so
they can never be confused with it. The prefix also serves as the guard in
``require_token()`` (master bearer check) which rejects any token string
that doesn't match ``paths.read_token()`` exactly; no code change needed
there.

Conformance state machine: each minted token opens a challenge with a set of
required check-in steps. The daemon records step arrivals (PR-2 will add the
check-in route) and queries whether all steps are complete before allowing
registration to proceed.
"""
from __future__ import annotations

import hashlib
import secrets
import threading
import time
from dataclasses import dataclass, field

# Default TTL for registration tokens (10 minutes)
DEFAULT_REGISTRATION_TOKEN_TTL_SECONDS = 600

# Prefix to distinguish scoped registration tokens from the master bearer
REGISTRATION_TOKEN_PREFIX = "hrreg_"

# Sentinel org value for runtime-level (org-agnostic) registration tokens.
# Stored in the same _tokens dict as org-scoped tokens so that _validate_raw()
# (which is org-agnostic) finds both, enabling require_registration_token()
# to work for both org and runtime routes without modification.
_RUNTIME_ORG = "__runtime__"


@dataclass
class RegistrationTokenRecord:
    """A minted registration token, stored by hash.

    The plaintext token value is never retained after minting — the caller
    (mint route) is responsible for returning it to the requestor.
    """

    token_hash: str
    org: str
    name: str  # executor profile name this token is scoped to register
    purpose: str = 'profile'  # 'profile' for executor profile, 'binary' for binary-path
    issued_at: float = 0.0
    expires_at: float = 0.0
    consumed: bool = False
    reserved: bool = False  # set by reserve(), cleared by commit()/release()


@dataclass
class ConformanceStep:
    """One required check-in step in a conformance challenge."""

    step_id: str
    arrived: bool = False
    arrived_at: float | None = None


@dataclass
class ConformanceChallenge:
    """A conformance challenge bound to a registration token."""

    token_hash: str
    org: str
    name: str
    steps: list[ConformanceStep] = field(default_factory=list)

    def record_arrival(self, step_id: str, now: float) -> bool:
        """Record a step arrival. Returns True if the step was found and
        was not already arrived (idempotent — duplicate arrivals are no-ops
        that return False)."""
        for step in self.steps:
            if step.step_id == step_id:
                if step.arrived:
                    return False
                step.arrived = True
                step.arrived_at = now
                return True
        return False

    @property
    def all_steps_complete(self) -> bool:
        """True iff every required step has arrived."""
        return all(step.arrived for step in self.steps)

    def pending_steps(self) -> list[str]:
        """Return step_ids that have not yet arrived."""
        return [s.step_id for s in self.steps if not s.arrived]


class RegistrationTokenStore:
    """In-memory store for registration tokens + conformance challenges.

    Tokens are stored hashed (sha256). The plaintext token value is never
    retained after minting — the caller is responsible for returning it to
    the mint requestor.

    Conformance challenges are opened at mint time with a set of required
    check-in steps. The store exposes record/query APIs for the daemon's
    PR-2 check-in route to call.
    """

    # Required conformance steps. PR-2 will define the actual step identifiers
    # and the daemon check-in route. For now, the store supports an arbitrary
    # set of step_ids per challenge.
    DEFAULT_CONFORMANCE_STEPS = [
        "workspace_access",
        "loopback_reachable",
        "cli_callback",
    ]

    def __init__(self, ttl_seconds: int = DEFAULT_REGISTRATION_TOKEN_TTL_SECONDS):
        self._tokens: dict[str, RegistrationTokenRecord] = {}
        self._challenges: dict[str, ConformanceChallenge] = {}
        self._ttl_seconds = ttl_seconds
        self._lock = threading.Lock()

    # ── helpers ──────────────────────────────────────────────────────────

    @staticmethod
    def _hash(token_plaintext: str) -> str:
        return hashlib.sha256(token_plaintext.encode()).hexdigest()

    # ── mint ─────────────────────────────────────────────────────────────

    def mint(
        self, org: str, name: str, now: float | None = None
    ) -> tuple[str, float]:
        """Mint a new registration token and open a conformance challenge.

        Expires any prior unconsumed token for the same ``(org, name)`` so a
        stale prompt cannot be replayed.

        Args:
            org: Org slug the token is scoped to.
            name: Executor profile name the token is scoped to register.
            now: Injectable clock (seconds since epoch) for testing.

        Returns:
            ``(token_plaintext, expires_at)`` — the caller returns
            ``token_plaintext`` to the mint requestor. ``expires_at`` is
            epoch seconds.
        """
        if now is None:
            now = time.time()
        with self._lock:
            self._expire_prior_tokens(org, name)
            token = REGISTRATION_TOKEN_PREFIX + secrets.token_urlsafe(32)
            token_hash = self._hash(token)
            expires_at = now + self._ttl_seconds
            self._tokens[token_hash] = RegistrationTokenRecord(
                token_hash=token_hash,
                org=org,
                name=name,
                issued_at=now,
                expires_at=expires_at,
            )
            self._challenges[token_hash] = ConformanceChallenge(
                token_hash=token_hash,
                org=org,
                name=name,
                steps=[
                    ConformanceStep(step_id=s) for s in self.DEFAULT_CONFORMANCE_STEPS
                ],
            )
        return token, expires_at

    def _expire_prior_tokens(self, org: str, name: str) -> None:
        """Mark all unconsumed tokens for ``(org, name)`` as consumed."""
        for record in self._tokens.values():
            if record.org == org and record.name == name and not record.consumed:
                record.consumed = True

    # ── validate / consume ───────────────────────────────────────────────

    def _validate_raw(
        self, token_plaintext: str, now: float | None = None
    ) -> RegistrationTokenRecord | None:
        """Validate a token is present, unexpired, unconsumed, unreserved —
        org-agnostic.

        Used by ``require_registration_token()`` which gates at the dependency
        level; org/name matching is the consumer route's responsibility (PR-2).
        """
        if now is None:
            now = time.time()
        token_hash = self._hash(token_plaintext)
        record = self._tokens.get(token_hash)
        if record is None:
            return None
        if record.consumed:
            return None
        if record.reserved:
            return None
        if now > record.expires_at:
            return None
        return record

    def validate(
        self, token_plaintext: str, org: str, now: float | None = None
    ) -> RegistrationTokenRecord | None:
        """Validate a token is present, unexpired, unconsumed, unreserved,
        and org-scoped.

        Does NOT consume the token. Returns the record if valid, ``None``
        otherwise.
        """
        if now is None:
            now = time.time()
        token_hash = self._hash(token_plaintext)
        record = self._tokens.get(token_hash)
        if record is None:
            return None
        if record.consumed:
            return None
        if record.reserved:
            return None
        if now > record.expires_at:
            return None
        if record.org != org:
            return None
        return record

    def consume(
        self, token_plaintext: str, org: str, now: float | None = None
    ) -> RegistrationTokenRecord | None:
        """Validate AND consume a token atomically.

        Returns the record if valid and unconsumed, ``None`` otherwise.
        After a successful return the token is marked consumed.
        """
        if now is None:
            now = time.time()
        with self._lock:
            record = self.validate(token_plaintext, org, now)
            if record is not None:
                record.consumed = True
        return record

    def reserve(
        self, token_plaintext: str, org: str, now: float | None = None
    ) -> RegistrationTokenRecord | None:
        """Atomically validate AND reserve a token.

        Same single-winner guarantee as ``consume()``: exactly ONE concurrent
        caller can reserve a given token. Returns the record if successful,
        ``None`` otherwise (expired, already-consumed, already-reserved,
        or wrong org).

        A reserved token is NOT consumed — the caller must later ``commit()``
        or ``release()`` it.  ``validate()`` and ``consume()`` both reject
        reserved tokens.
        """
        if now is None:
            now = time.time()
        with self._lock:
            record = self.validate(token_plaintext, org, now)
            if record is not None:
                record.reserved = True
        return record

    def commit(
        self, token_plaintext: str, org: str, now: float | None = None
    ) -> bool:
        """Permanently consume a reserved token.

        Returns ``True`` if the token was reserved and is now consumed.
        Returns ``False`` if the token was not found or not reserved.
        """
        with self._lock:
            token_hash = self._hash(token_plaintext)
            record = self._tokens.get(token_hash)
            if record is None:
                return False
            if not record.reserved:
                return False
            if record.org != org:
                return False
            record.consumed = True
            record.reserved = False
        return True

    def release(
        self, token_plaintext: str, org: str, now: float | None = None
    ) -> bool:
        """Release a reservation so the token is valid for retry.

        Returns ``True`` if the token was reserved and is now released.
        Returns ``False`` if the token was not found, not reserved,
        or wrong org.
        """
        with self._lock:
            token_hash = self._hash(token_plaintext)
            record = self._tokens.get(token_hash)
            if record is None:
                return False
            if not record.reserved:
                return False
            if record.org != org:
                return False
            record.reserved = False
        return True

    # ── Conformance state machine ────────────────────────────────────────

    def get_challenge(self, token_plaintext: str) -> ConformanceChallenge | None:
        """Get the conformance challenge for a token (by its plaintext value)."""
        token_hash = self._hash(token_plaintext)
        return self._challenges.get(token_hash)

    def record_step_arrival(
        self,
        token_plaintext: str,
        org: str,
        step_id: str,
        now: float | None = None,
    ) -> bool:
        """Record a conformance step arrival for a valid, unexpired, unconsumed token.

        Returns ``True`` if the step was recorded, ``False`` otherwise (token
        invalid, expired, consumed, step unknown, or step already arrived).
        """
        if now is None:
            now = time.time()
        with self._lock:
            record = self.validate(token_plaintext, org, now)
            if record is None:
                return False
            challenge = self._challenges.get(record.token_hash)
            if challenge is None:
                return False
            return challenge.record_arrival(step_id, now)

    def is_challenge_complete(
        self, token_plaintext: str, org: str, now: float | None = None
    ) -> bool:
        """Check if all required steps have arrived for a valid, unexpired,
        unconsumed token.

        Returns ``False`` if the token is invalid, expired, or consumed.
        """
        if now is None:
            now = time.time()
        record = self.validate(token_plaintext, org, now)
        if record is None:
            return False
        challenge = self._challenges.get(record.token_hash)
        if challenge is None:
            return False
        return challenge.all_steps_complete

    def get_pending_steps(
        self, token_plaintext: str, org: str, now: float | None = None
    ) -> list[str] | None:
        """Return pending step_ids, or ``None`` if the token is
        invalid/expired/consumed."""
        if now is None:
            now = time.time()
        record = self.validate(token_plaintext, org, now)
        if record is None:
            return None
        challenge = self._challenges.get(record.token_hash)
        if challenge is None:
            return None
        return challenge.pending_steps()

    # ── Runtime-level (org-agnostic) token methods ─────────────────────
    #
    # These are additive PARALLEL methods that operate on the SAME internal
    # _tokens / _challenges dictionaries using a sentinel org value
    # (_RUNTIME_ORG). This lets _validate_raw() — which is org-agnostic —
    # find both org and runtime tokens, so require_registration_token()
    # can gate both org and runtime routes without modification.

    def mint_runtime(
        self, name: str, now: float | None = None, purpose: str = 'profile'
    ) -> tuple[str, float]:
        """Mint a runtime-level (org-agnostic) registration token.

        Expires any prior unconsumed runtime token for the same ``(name, purpose)``.

        Args:
            name: Executor profile name the token is scoped to register.
            now: Injectable clock (seconds since epoch) for testing.
            purpose: 'profile' for executor profile registration,
                     'binary' for binary-path registration.

        Returns:
            ``(token_plaintext, expires_at)``
        """
        if now is None:
            now = time.time()
        with self._lock:
            self._expire_prior_runtime(name, purpose)
            token = REGISTRATION_TOKEN_PREFIX + secrets.token_urlsafe(32)
            token_hash = self._hash(token)
            expires_at = now + self._ttl_seconds
            self._tokens[token_hash] = RegistrationTokenRecord(
                token_hash=token_hash,
                org=_RUNTIME_ORG,
                name=name,
                purpose=purpose,
                issued_at=now,
                expires_at=expires_at,
            )
            self._challenges[token_hash] = ConformanceChallenge(
                token_hash=token_hash,
                org=_RUNTIME_ORG,
                name=name,
                steps=[
                    ConformanceStep(step_id=s) for s in self.DEFAULT_CONFORMANCE_STEPS
                ],
            )
        return token, expires_at

    def _expire_prior_runtime(self, name: str, purpose: str = 'profile') -> None:
        """Mark all unconsumed RUNTIME tokens for ``(name, purpose)`` as consumed.

        Different purposes do NOT expire each other — a binary-purpose token
        and a profile-purpose token for the same name coexist.
        """
        for record in self._tokens.values():
            if (
                record.org == _RUNTIME_ORG
                and record.name == name
                and record.purpose == purpose
                and not record.consumed
            ):
                record.consumed = True

    def validate_runtime(
        self, token_plaintext: str, now: float | None = None
    ) -> RegistrationTokenRecord | None:
        """Validate a runtime token is present, unexpired, unconsumed, unreserved,
        and runtime-scoped.

        Does NOT consume the token. Returns the record if valid, ``None`` otherwise.
        """
        # Reuse the org-agnostic _validate_raw, then check it's a runtime token.
        record = self._validate_raw(token_plaintext, now)
        if record is None:
            return None
        if record.org != _RUNTIME_ORG:
            return None
        return record

    def consume_runtime(
        self, token_plaintext: str, now: float | None = None
    ) -> RegistrationTokenRecord | None:
        """Validate AND consume a runtime token atomically."""
        if now is None:
            now = time.time()
        with self._lock:
            record = self.validate_runtime(token_plaintext, now)
            if record is not None:
                record.consumed = True
        return record

    def reserve_runtime(
        self, token_plaintext: str, now: float | None = None
    ) -> RegistrationTokenRecord | None:
        """Atomically validate AND reserve a runtime token."""
        if now is None:
            now = time.time()
        with self._lock:
            record = self.validate_runtime(token_plaintext, now)
            if record is not None:
                record.reserved = True
        return record

    def commit_runtime(
        self, token_plaintext: str, now: float | None = None
    ) -> bool:
        """Permanently consume a reserved runtime token."""
        with self._lock:
            token_hash = self._hash(token_plaintext)
            record = self._tokens.get(token_hash)
            if record is None:
                return False
            if not record.reserved:
                return False
            if record.org != _RUNTIME_ORG:
                return False
            record.consumed = True
            record.reserved = False
        return True

    def release_runtime(
        self, token_plaintext: str, now: float | None = None
    ) -> bool:
        """Release a runtime reservation so the token is valid for retry."""
        with self._lock:
            token_hash = self._hash(token_plaintext)
            record = self._tokens.get(token_hash)
            if record is None:
                return False
            if not record.reserved:
                return False
            if record.org != _RUNTIME_ORG:
                return False
            record.reserved = False
        return True

    # ── Runtime conformance state machine ───────────────────────────────

    def get_challenge_runtime(
        self, token_plaintext: str
    ) -> ConformanceChallenge | None:
        """Get the conformance challenge for a runtime token."""
        token_hash = self._hash(token_plaintext)
        challenge = self._challenges.get(token_hash)
        if challenge is None:
            return None
        if challenge.org != _RUNTIME_ORG:
            return None
        return challenge

    def record_step_arrival_runtime(
        self,
        token_plaintext: str,
        step_id: str,
        now: float | None = None,
    ) -> bool:
        """Record a conformance step arrival for a valid runtime token."""
        if now is None:
            now = time.time()
        with self._lock:
            record = self.validate_runtime(token_plaintext, now)
            if record is None:
                return False
            challenge = self._challenges.get(record.token_hash)
            if challenge is None:
                return False
            return challenge.record_arrival(step_id, now)

    def is_challenge_complete_runtime(
        self, token_plaintext: str, now: float | None = None
    ) -> bool:
        """Check if all required steps have arrived for a valid runtime token."""
        if now is None:
            now = time.time()
        record = self.validate_runtime(token_plaintext, now)
        if record is None:
            return False
        challenge = self._challenges.get(record.token_hash)
        if challenge is None:
            return False
        return challenge.all_steps_complete

    def get_pending_steps_runtime(
        self, token_plaintext: str, now: float | None = None
    ) -> list[str] | None:
        """Return pending step_ids for a runtime token, or ``None`` if invalid."""
        if now is None:
            now = time.time()
        record = self.validate_runtime(token_plaintext, now)
        if record is None:
            return None
        challenge = self._challenges.get(record.token_hash)
        if challenge is None:
            return None
        return challenge.pending_steps()
