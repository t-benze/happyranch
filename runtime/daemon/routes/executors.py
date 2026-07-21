"""Executor registration routes — THR-052 PR-2 / THR-088 / THR-107.

POST /api/v1/orgs/{slug}/executors/conformance-checkin
    Loopback-only, scoped-token-only. Records a conformance step arrival
    for a pending registration token. The candidate CLI calls this for
    each required check-in step (workspace_access, loopback_reachable,
    cli_callback) before attempting registration.

POST /api/v1/orgs/{slug}/executors/register
    Loopback-only, scoped-token-only. Consumes a fully-conformant
    org-scoped registration token, validates the profile, and atomically
    writes it to the machine-global runtime store (THR-107: the per-org
    config.yaml executor_profiles surface is removed — both this route
    and the runtime-level route persist to the same store).
"""
from __future__ import annotations

import threading

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from runtime.daemon.auth import require_registration_token, require_token
from runtime.daemon.registration_token import REGISTRATION_TOKEN_PREFIX
from runtime.orchestrator.executor_binary_registry import (
    get_binary,
    is_binary_valid,
    set_binary,
    validate_binary,
)
from runtime.daemon.routes._org_dep import OrgDep
from runtime.infrastructure.audit_logger import AuditLogger
from runtime.infrastructure.database import Database
from runtime.orchestrator.executor_registry import get_registry, ExecutorRegistry
from runtime.orchestrator.executor_registry import (
    ExecutorProfileCollisionError,
    ExecutorProfile,
)
from runtime.orchestrator.runtime_executor_store import (
    save_runtime_profile,
    load_runtime_profiles,
    remove_runtime_profile,
)

router = APIRouter()

# ---------------------------------------------------------------------------
# Per-profile-name locks — serialize write+register for the same profile
# name so concurrent different-token registrations can't both pass the
# preflight check before either one publishes to the in-memory registry.
# ---------------------------------------------------------------------------

_profile_locks: dict[str, threading.Lock] = {}
_profile_locks_lock = threading.Lock()


def _acquire_profile_lock(name: str) -> threading.Lock:
    """Acquire and return the lock for a given profile name.

    Creates the lock on first access (under a creation lock so two
    threads don't race to insert). The caller MUST release the lock.
    """
    key = name.lower()
    with _profile_locks_lock:
        lock = _profile_locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _profile_locks[key] = lock
    lock.acquire()
    return lock


# ── Runtime audit helper (THR-088 Slice B) ─────────────────────────────


def _audit_runtime_registration(
    *,
    profile_name: str,
    command: str,
    argv_template: list[str],
    adapter: str,
    actor: str = "founder",
) -> None:
    """Write a runtime-level executor registration audit row.

    Opens (creating if needed) a dedicated runtime-audit.db under
    daemon_home(), then writes a single audit_log row via the
    existing AuditLogger + Database machinery.  Each call opens a
    fresh ``Database`` handle and closes it; registration is
    infrequent and serialized by ``_profile_locks``, so the overhead
    is negligible.

    Row shape (THR-088 Slice B):
      task_id = "executor:<profile_name>"
      action  = "executor_registered"
      payload = {command, argv_template, adapter}
    """
    from runtime.runtime import daemon_home

    audit_db_path = daemon_home() / "runtime-audit.db"
    db = Database(audit_db_path)
    try:
        logger = AuditLogger(db)
        logger.log_executor_registered(
            profile_name=profile_name,
            command=command,
            argv_template=argv_template,
            adapter=adapter,
            actor=actor,
        )
    finally:
        db.close()


def _audit_runtime_removal(
    *,
    profile_name: str,
    command: str,
    argv_template: list[str],
    adapter: str,
    actor: str = "founder",
) -> None:
    """Write a runtime-level executor removal audit row.

    Mirrors ``_audit_runtime_registration`` — same dedicated
    runtime-audit.db under daemon_home(), same scope-prefix task_id
    convention, same payload keys; only the action verb differs.

    Row shape (THR-107 S4a):
      task_id = "executor:<profile_name>"
      action  = "executor_removed"
      payload = {command, argv_template, adapter}
    """
    from runtime.runtime import daemon_home

    audit_db_path = daemon_home() / "runtime-audit.db"
    db = Database(audit_db_path)
    try:
        logger = AuditLogger(db)
        logger.log_executor_removed(
            profile_name=profile_name,
            command=command,
            argv_template=argv_template,
            adapter=adapter,
            actor=actor,
        )
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _extract_token(request: Request) -> str:
    """Extract the Bearer token plaintext from the Authorization header.

    Assumes the ``require_registration_token()`` dependency already passed,
    so the header is present, starts with ``Bearer `` and is a valid
    ``hrreg_`` token.
    """
    auth = request.headers.get("Authorization", "")
    return auth.removeprefix("Bearer ").strip()


def _token_org_name_mismatch(
    request: Request, org_slug: str, body_name: str
) -> str | None:
    """Return an error detail string if the token's org/name doesn't match
    the route parameters, or None if everything matches."""
    token_value = _extract_token(request)
    store = request.app.state.daemon.registration_token_store
    record = store.validate(token_value, org_slug)
    if record is None:
        return f"Registration token not valid for org {org_slug!r}"
    if record.name != body_name:
        return (
            f"Registration token scoped to name {record.name!r}, "
            f"but request asks for {body_name!r}"
        )
    return None


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class ConformanceCheckinRequest(BaseModel):
    """A single conformance step arrival from the candidate CLI.

    ``envelope`` is optional and only validated for the ``emit_envelope``
    conformance step (THR-107). Non-emit steps ignore it.
    """
    step_id: str = Field(..., min_length=1)
    envelope: dict | None = Field(None)


class ConformanceCheckinResponse(BaseModel):
    step_id: str
    arrived: bool
    pending: list[str]
    all_complete: bool


class ExecutorRegisterRequest(BaseModel):
    """Profile definition for a custom executor.

    ``command`` is the executable name validated via ``shutil.which()``.
    ``argv_template`` is the argument list with supported placeholders.
    ``adapter`` must be one of claude/codex/opencode/pi.

    The ``name`` is not in the body — it comes from the registration
    token's scope, ensuring one token = one named profile.
    """
    command: str = Field(..., min_length=1)
    argv_template: list[str] = Field(..., min_length=1)
    adapter: str = Field("pi", min_length=1)


class ExecutorRegisterResponse(BaseModel):
    name: str
    kind: str
    adapter_id: str
    command: str
    argv_template: list[str]


# Allowed token_usage keys — must match TokenUsage field names
# (runtime/models.py:302). model and usage_raw_json are str|null;
# all others are int|null.
_ALLOWED_TOKEN_USAGE_KEYS = frozenset({
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_creation_tokens",
    "reasoning_tokens",
    "model",
    "usage_raw_json",
})

# token_usage keys whose values must be int or None (not str, not bool)
_TOKEN_USAGE_INT_KEYS = _ALLOWED_TOKEN_USAGE_KEYS - {"model", "usage_raw_json"}


def _validate_emit_envelope_step(body: ConformanceCheckinRequest) -> None:
    """Validate the envelope payload for the ``emit_envelope`` conformance step.

    THR-107 Phase 1: the ``emit_envelope`` step MUST carry a valid sample
    envelope. Other steps ignore the envelope field.

    Validation (per design spec §4.2):
    - ``envelope_version`` must be integer 1.
    - ``token_usage``, when present, must be a dict whose keys are known
      TokenUsage field names; unknown keys are rejected.
    - ``token_usage`` int fields must be int or None — string values,
      bools, and floats are rejected.
    """
    if body.step_id != "emit_envelope":
        return  # non-emit steps ignore envelope
    if body.envelope is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "envelope_required",
                "detail": "The 'emit_envelope' conformance step requires an envelope payload.",
            },
        )
    version = body.envelope.get("envelope_version")
    if version != 1 or not isinstance(version, int) or isinstance(version, bool):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "invalid_envelope_version",
                "detail": f"envelope_version must be integer 1, got {version!r}.",
            },
        )

    # Validate token_usage shape when present (THR-107 review-followup)
    token_usage = body.envelope.get("token_usage")
    if token_usage is not None:
        if not isinstance(token_usage, dict):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "invalid_token_usage",
                    "detail": "token_usage must be a dict, got " + type(token_usage).__name__ + ".",
                },
            )
        unknown_keys = set(token_usage) - _ALLOWED_TOKEN_USAGE_KEYS
        if unknown_keys:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "invalid_token_usage",
                    "detail": "Unknown token_usage keys: " + ", ".join(sorted(unknown_keys)) + ".",
                },
            )
        # Validate int-key value types: must be int or None (bool is int
        # subclass in Python, so reject bool explicitly before the int check).
        for key in _TOKEN_USAGE_INT_KEYS:
            val = token_usage.get(key)
            if val is not None and (isinstance(val, bool) or not isinstance(val, int)):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={
                        "code": "invalid_token_usage",
                        "detail": "token_usage." + key + " must be int or null, got " + type(val).__name__ + ".",
                    },
                )
        # Validate string-key value types: must be str or None
        for key in ("model", "usage_raw_json"):
            val = token_usage.get(key)
            if val is not None and not isinstance(val, str):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={
                        "code": "invalid_token_usage",
                        "detail": "token_usage." + key + " must be str or null, got " + type(val).__name__ + ".",
                    },
                )


# ---------------------------------------------------------------------------
# POST /conformance-checkin
# ---------------------------------------------------------------------------


@router.post(
    "/executors/conformance-checkin",
    dependencies=[require_registration_token()],
)
def conformance_checkin(
    request: Request,
    body: ConformanceCheckinRequest,
    org: OrgDep,
) -> ConformanceCheckinResponse:
    """Record a conformance step arrival for a pending registration token.

    Called by the candidate CLI after completing each required check-in
    step (workspace access, loopback reachability, CLI callback, emit_envelope).

    The step_id must be one of the known conformance steps.
    Returns the current conformance state so the CLI can report progress.
    """
    token_value = _extract_token(request)
    store = request.app.state.daemon.registration_token_store
    slug = org.slug

    # Validate org match
    record = store.validate(token_value, slug)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "token_not_valid_for_org", "org": slug},
        )

    # Validate step_id is known
    challenge = store.get_challenge(token_value)
    if challenge is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="No conformance challenge for this token",
        )
    valid_step_ids = {s.step_id for s in challenge.steps}
    if body.step_id not in valid_step_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown step {body.step_id!r}. Valid: {sorted(valid_step_ids)}",
        )

    # Validate envelope for emit_envelope step (THR-107)
    _validate_emit_envelope_step(body)

    # Record arrival
    arrived = store.record_step_arrival(token_value, slug, body.step_id)

    # Return current state
    pending = store.get_pending_steps(token_value, slug) or []
    all_complete = store.is_challenge_complete(token_value, slug)

    return ConformanceCheckinResponse(
        step_id=body.step_id,
        arrived=arrived,
        pending=pending,
        all_complete=all_complete,
    )


# ---------------------------------------------------------------------------
# POST /register
# ---------------------------------------------------------------------------


@router.post(
    "/executors/register",
    dependencies=[require_registration_token()],
)
def register_executor(
    request: Request,
    body: ExecutorRegisterRequest,
    org: OrgDep,
) -> ExecutorRegisterResponse:
    """Register a custom executor profile via an org-scoped token.

    THR-107: the profile is persisted to the machine-global runtime store
    (``<daemon-home>/executor_profiles.yaml``) — the per-org config.yaml
    executor_profiles surface is removed. The org-scoped token still gates
    WHO may register; the resulting definition is machine-global, exactly
    as with ``POST /executors/runtime/register``. The write is audited in
    the org's audit log (``org_config_write`` row shape, section
    ``executor_profiles``) with before/after snapshots of the runtime
    store, so the org-token-gated action stays visible in that org's
    audit trail.

    Requirements (ALL must pass):
    1. Token is valid, unexpired, unconsumed, loopback (checked by
       ``require_registration_token``).
    2. Token record org matches {slug}.
    3. Token record name is used as the profile name (not from body).
    4. Conformance challenge is fully complete.
    5. Static validation passes (valid adapter, command on PATH, valid
       argv_template, no builtin collision).
    6. Token is atomically reserved BEFORE any durable side effects;
       committed only on clean success, released on any failure so the
       token stays valid for retry within its unexpired TTL.
    7. On successful reserve: write the durable runtime store first, then
       register the in-memory profile. The durable store is the source of
       truth; in-memory registration only happens after the store write
       succeeds so that a write failure does not leave a stale
       (unaudited, non-durable) profile in the process-wide registry.

    On success returns 200. On any validation failure returns 4xx
    without touching the store.
    """
    token_value = _extract_token(request)
    store = request.app.state.daemon.registration_token_store
    slug = org.slug

    # 1. Validate token (org-scoped, unexpired, unconsumed)
    record = store.validate(token_value, slug)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Registration token is invalid, expired, consumed, or not for this org",
        )

    profile_name = record.name

    # 2. Conformance must be complete
    if not store.is_challenge_complete(token_value, slug):
        pending = store.get_pending_steps(token_value, slug) or []
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Conformance incomplete. Pending steps: {pending}",
        )

    # 3. Static validation — drive through the CANONICAL validation path
    #    so the route can never silently diverge from startup config validation.
    config_cfg = {
        "command": body.command,
        "argv_template": body.argv_template,
        "adapter": body.adapter,
    }
    try:
        candidate = ExecutorRegistry.validate_custom_profile_config(
            profile_name, config_cfg
        )
    except ValueError as exc:
        # Map canonical ValueError -> existing HTTP codes (preserving tested behavior).
        detail = str(exc)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=detail,
        )

    # 5. Preflight collision check BEFORE any side effects.
    #    Detect a conflicting custom profile now so we can reject 409
    #    without consuming the token or touching durable config.
    #    Idempotent re-registration (identical profile) is allowed through.
    registry = get_registry()
    if registry.is_registered(profile_name):
        existing = registry.get_profile(profile_name)
        if existing is not None:
            if existing.kind == "builtin":
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail={
                        "code": "builtin_collision",
                        "name": profile_name,
                        "detail": f"Cannot override built-in executor {profile_name!r}.",
                    },
                )
            # Custom collision — only reject if the definition differs.
            # Identical definitions pass through (idempotent re-registration).
            if existing != candidate:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"Custom executor profile {profile_name!r} is already "
                    f"registered with a different definition.",
                )

    # 6. RESERVE the token (atomic gate) — same single-winner guarantee
    #    as consume() but does NOT permanently consume it. The token is
    #    reserved for the duration of this registration attempt.
    #    On success the token will be committed (permanently consumed).
    #    On ANY pre-success failure the reservation is released so the
    #    token stays valid for retry within its unexpired TTL.
    reserved = store.reserve(token_value, slug)
    if reserved is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Registration token is invalid, expired, consumed, or not for this org",
        )

    # 7. Acquire per-profile-name lock for the write+register critical
    #    section.  Two concurrent requests with DIFFERENT tokens for the
    #    same profile name both pass the preflight check (step 5) and
    #    reserve (step 6) independently.  The lock serialises the
    #    write+register so that a double-check inside the lock sees the
    #    winner's published profile and rejects the loser with 409.
    #    Without this lock, the loser's runtime-store write would
    #    overwrite the winner's before the winner's
    #    register_custom_profile completes, and the loser's
    #    register_custom_profile would then raise
    #    ExecutorProfileCollisionError — leaving durable store (loser's)
    #    and in-memory registry (winner's) diverged with no audit.
    #
    #    The lock is acquired AFTER reserve so the existing same-token
    #    concurrency test (which uses a threading.Barrier inside reserve)
    #    continues to exercise the atomic gate without deadlocking.
    profile_lock = _acquire_profile_lock(profile_name)
    try:
        # 7a. Double-check inside the lock: a concurrent registration
        #     for this profile name may have completed between our
        #     preflight check (step 5) and acquiring the lock.
        if registry.is_registered(profile_name):
            existing_inside = registry.get_profile(profile_name)
            if existing_inside is not None and existing_inside != candidate:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"Custom executor profile {profile_name!r} is already "
                    f"registered with a different definition.",
                )

        # 8. Durable: write the machine-global runtime store (THR-107 —
        #    the per-org config.yaml executor_profiles surface is removed).
        config_entry = {
            "command": body.command,
            "argv_template": [str(e) for e in body.argv_template],
            "adapter": body.adapter,
        }
        before_snapshot = dict(load_runtime_profiles())
        try:
            save_runtime_profile(profile_name, config_entry)
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Runtime profile write error: {exc}",
            )

        # 9. In-memory: register the profile in the process-wide registry.
        try:
            registry.register_custom_profile(candidate)
        except ExecutorProfileCollisionError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Profile collision: {exc}",
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=str(exc),
            )

        # 10. Audit the write. THR-107: the durable write went to the
        #     machine-global runtime store, so the before/after snapshots
        #     are runtime-store state (load_runtime_profiles) — the
        #     audit-row shape and the "executor_profiles" section label
        #     are unchanged so the org's audit trail still records what
        #     changed for this org-token-gated registration.
        after_snapshot = dict(load_runtime_profiles())
        logger = AuditLogger(org.db)
        logger.log_org_config_write(
            section="executor_profiles",
            tiers=[profile_name],
            before=before_snapshot,
            after=after_snapshot,
            actor="founder",
        )
    except BaseException:
        # Release reservation on ANY failure so the token stays valid
        # for retry within its unexpired TTL.
        store.release(token_value, slug)
        raise
    else:
        # COMMIT (permanent consume) ONLY on clean success.
        store.commit(token_value, slug)
    finally:
        profile_lock.release()

    return ExecutorRegisterResponse(
        name=profile_name,
        kind="custom",
        adapter_id=body.adapter,
        command=body.command,
        argv_template=[str(e) for e in body.argv_template],
    )


# ---------------------------------------------------------------------------
# Runtime-level routes (THR-088) — org-agnostic, machine-global
# ---------------------------------------------------------------------------

runtime_router = APIRouter()


@runtime_router.post(
    "/executors/runtime/conformance-checkin",
    dependencies=[require_registration_token()],
)
def runtime_conformance_checkin(
    request: Request,
    body: ConformanceCheckinRequest,
) -> ConformanceCheckinResponse:
    """Record a conformance step arrival for a pending RUNTIME registration token.

    Same conformance model as the org-scoped route, but operates on runtime
    tokens (no org). The candidate CLI calls this for each required check-in
    step before attempting registration.
    """
    token_value = _extract_token(request)
    store = request.app.state.daemon.registration_token_store

    # Validate runtime token
    record = store.validate_runtime(token_value)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "token_not_valid_runtime"},
        )

    # Validate step_id is known
    challenge = store.get_challenge_runtime(token_value)
    if challenge is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="No conformance challenge for this token",
        )
    valid_step_ids = {s.step_id for s in challenge.steps}
    if body.step_id not in valid_step_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown step {body.step_id!r}. Valid: {sorted(valid_step_ids)}",
        )

    # Validate envelope for emit_envelope step (THR-107)
    _validate_emit_envelope_step(body)

    # Record arrival
    arrived = store.record_step_arrival_runtime(token_value, body.step_id)

    # Return current state
    pending = store.get_pending_steps_runtime(token_value) or []
    all_complete = store.is_challenge_complete_runtime(token_value)

    return ConformanceCheckinResponse(
        step_id=body.step_id,
        arrived=arrived,
        pending=pending,
        all_complete=all_complete,
    )


@runtime_router.post(
    "/executors/runtime/register",
    dependencies=[require_registration_token()],
)
def runtime_register_executor(
    request: Request,
    body: ExecutorRegisterRequest,
) -> ExecutorRegisterResponse:
    """Register a runtime-level (org-agnostic) executor profile.

    Requirements (ALL must pass):
    1. Token is valid, unexpired, unconsumed, loopback (checked by
       ``require_registration_token``).
    2. Conformance challenge is fully complete.
    3. Static validation passes (valid adapter, command on PATH, valid
       argv_template, no builtin collision).
    4. Token is atomically reserved before any side effects. Commit on
       success, release on failure.
    5. On successful reserve: write durable runtime store first, then
       register the in-memory profile.
    """
    token_value = _extract_token(request)
    store = request.app.state.daemon.registration_token_store

    # 1. Validate runtime token
    record = store.validate_runtime(token_value)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Registration token is invalid, expired, consumed, or not a runtime token",
        )

    # 1b. Assert purpose is 'profile' (binary-purpose tokens NOT allowed here)
    if record.purpose != "profile":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "token_purpose_mismatch",
                "expected": "profile",
                "actual": record.purpose,
            },
        )

    profile_name = record.name

    # 2. Conformance must be complete
    if not store.is_challenge_complete_runtime(token_value):
        pending = store.get_pending_steps_runtime(token_value) or []
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Conformance incomplete. Pending steps: {pending}",
        )

    # 3. Static validation
    config_cfg = {
        "command": body.command,
        "argv_template": body.argv_template,
        "adapter": body.adapter,
    }
    try:
        candidate = ExecutorRegistry.validate_custom_profile_config(
            profile_name, config_cfg
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        )

    # 4. Preflight collision check
    registry = get_registry()
    if registry.is_registered(profile_name):
        existing = registry.get_profile(profile_name)
        if existing is not None:
            if existing.kind == "builtin":
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail={
                        "code": "builtin_collision",
                        "name": profile_name,
                        "detail": f"Cannot override built-in executor {profile_name!r}.",
                    },
                )
            if existing != candidate:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"Custom executor profile {profile_name!r} is already "
                    f"registered with a different definition.",
                )

    # 5. Reserve the token (atomic gate)
    reserved = store.reserve_runtime(token_value)
    if reserved is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Registration token is invalid, expired, consumed, or not a runtime token",
        )

    # 6. Acquire per-profile-name lock
    profile_lock = _acquire_profile_lock(profile_name)
    try:
        # Double-check inside lock
        if registry.is_registered(profile_name):
            existing_inside = registry.get_profile(profile_name)
            if existing_inside is not None and existing_inside != candidate:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"Custom executor profile {profile_name!r} is already "
                    f"registered with a different definition.",
                )

        # 7. Durable: write runtime store
        config_entry = {
            "command": body.command,
            "argv_template": [str(e) for e in body.argv_template],
            "adapter": body.adapter,
        }
        try:
            save_runtime_profile(profile_name, config_entry)
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Runtime profile write error: {exc}",
            )

        # 8. In-memory: register the profile
        try:
            registry.register_custom_profile(candidate)
        except ExecutorProfileCollisionError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Profile collision: {exc}",
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=str(exc),
            )
    except BaseException:
        store.release_runtime(token_value)
        raise
    else:
        # 9. Audit the successful runtime-level registration.
        #    Runtime-level registration is org-agnostic, so audit
        #    rows go to a dedicated runtime-audit.db (co-located
        #    with metrics.db under daemon_home()), NOT a per-org db.
        #    Uses the scope-prefix convention: task_id='executor:<name>'.
        _audit_runtime_registration(
            profile_name=profile_name,
            command=body.command,
            argv_template=body.argv_template,
            adapter=body.adapter,
        )
        store.commit_runtime(token_value)
    finally:
        profile_lock.release()

    return ExecutorRegisterResponse(
        name=profile_name,
        kind="custom",
        adapter_id=body.adapter,
        command=body.command,
        argv_template=[str(e) for e in body.argv_template],
    )


# ── Register-Binary request/response models (THR-088) ──────────────────


class RegisterBinaryRequest(BaseModel):
    """Register a binary path for an executor kind.

    The kind is determined from the registration token's ``name`` — there
    is NO ``kind`` field in the body. This ensures a token scoped to
    ``claude`` can only write the ``claude`` binary path.
    """
    path: str = Field(..., min_length=1, description="Absolute path to the executor binary")


class RegisterBinaryResponse(BaseModel):
    kind: str
    path: str
    valid: bool


# ── POST /executors/runtime/register-binary (THR-088) ──────────────────


@runtime_router.post(
    "/executors/runtime/register-binary",
    dependencies=[require_registration_token()],
)
def runtime_register_binary(
    request: Request,
    body: RegisterBinaryRequest,
) -> RegisterBinaryResponse:
    """Register a binary path for a built-in executor kind.

    Security model (FOUNDER-APPROVED Option B, THR-088):
    - Loopback-only + scoped-token (same ``require_registration_token`` gate
      as the sibling runtime routes).
    - Token MUST have ``purpose='binary'`` — profile-purpose tokens are rejected.
    - Kind comes from the token record's ``name``, NOT the request body.
      This guarantees one token = one kind (no cross-kind writes).
    - Reuses the same conformance-challenge model as ``runtime_register_executor``:
      the CLI must complete all check-in steps before calling this route.
    - ``validate_binary(path)`` is called before any registry write.
    - Token is atomically reserve→commit (release on failure) — same pattern as
      ``runtime_register_executor``.

    On any validation or conformance failure the token is NOT consumed and
    remains retryable within its TTL.
    """
    token_value = _extract_token(request)
    store = request.app.state.daemon.registration_token_store

    # 1. Validate runtime token
    record = store.validate_runtime(token_value)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Registration token is invalid, expired, consumed, or not a runtime token",
        )

    # 2. Assert purpose == 'binary'
    if record.purpose != "binary":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "token_purpose_mismatch",
                "expected": "binary",
                "actual": record.purpose,
            },
        )

    kind = record.name  # The token's name IS the executor kind

    # 3. Conformance must be complete (SAME model as runtime_register_executor)
    if not store.is_challenge_complete_runtime(token_value):
        pending = store.get_pending_steps_runtime(token_value) or []
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Conformance incomplete. Pending steps: {pending}",
        )

    # 4. Validate the binary path BEFORE any side effects
    try:
        resolved = validate_binary(body.path)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        )

    # 5. RESERVE the token (atomic gate)
    reserved = store.reserve_runtime(token_value)
    if reserved is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Registration token is invalid, expired, consumed, or not a runtime token",
        )

    try:
        # 6. Write the binary path to the machine-local registry
        set_binary(kind, resolved)
    except BaseException:
        store.release_runtime(token_value)
        raise
    else:
        # 7. COMMIT (permanent consume) ONLY on clean success
        store.commit_runtime(token_value)

    return RegisterBinaryResponse(
        kind=kind,
        path=resolved,
        valid=True,
    )


# ── Runtime profile management routes (THR-107 S4a) ──────────────────────
#
# LIST + REMOVE for custom profiles in the machine-global runtime store.
# These are founder-facing MANAGEMENT routes (standard daemon bearer auth,
# same posture as GET /executor-binaries) — NOT registration routes, so no
# registration-token dependency.


class RuntimeProfileEntry(BaseModel):
    """Summary of one custom executor profile in the runtime store."""
    name: str = Field(..., description="Profile name (runtime store key)")
    command: str | None = Field(
        None, description="Executable name from the stored profile definition"
    )
    adapter: str | None = Field(
        None, description="Workspace adapter id (claude/codex/opencode/pi)"
    )
    present: bool = Field(
        False,
        description=(
            "True when the machine-local binary registry holds a valid "
            "path for this profile name — same signal as /health/prereqs"
        ),
    )
    path: str | None = Field(
        None, description="The registered binary path when present, else None"
    )


class RuntimeProfileList(BaseModel):
    """All custom profiles in the machine-global runtime store."""
    profiles: list[RuntimeProfileEntry]


class RemoveRuntimeProfileResponse(BaseModel):
    name: str
    removed: bool


@runtime_router.get(
    "/executors/runtime/profiles",
    response_model=RuntimeProfileList,
    dependencies=[require_token()],
)
def list_runtime_executor_profiles() -> RuntimeProfileList:
    """List custom executor profiles from the machine-global runtime store.

    Reads ``load_runtime_profiles()`` — the durable source of truth — and
    reports each profile's name, command, and adapter. ``present``/``path``
    mirror the /health/prereqs signal: the machine-local executor binary
    registry (a profile counts as connected only after its binary is
    explicitly registered; being on PATH is NOT sufficient).

    Honesty fence: only real store data — no invented status.
    """
    stored = load_runtime_profiles()
    entries: list[RuntimeProfileEntry] = []
    for name in sorted(stored.keys()):
        entry = stored[name]
        command = entry.get("command")
        adapter = entry.get("adapter")
        bin_path = get_binary(name)
        registered = bin_path is not None and is_binary_valid(bin_path)
        entries.append(RuntimeProfileEntry(
            name=name,
            command=command if isinstance(command, str) else None,
            adapter=adapter if isinstance(adapter, str) else None,
            present=registered,
            path=bin_path if registered else None,
        ))
    return RuntimeProfileList(profiles=entries)


@runtime_router.delete(
    "/executors/runtime/profiles/{name}",
    response_model=RemoveRuntimeProfileResponse,
    dependencies=[require_token()],
)
def remove_runtime_executor_profile(name: str) -> RemoveRuntimeProfileResponse:
    """Remove a custom executor profile (durable store + in-memory registry).

    Symmetric inverse of the register path: registration writes the durable
    runtime store FIRST, then publishes the transient in-memory profile —
    removal clears the durable store FIRST (source of truth; a store-write
    failure must not leave a resurrectable entry behind), then unregisters
    the in-process profile so it does not linger until restart.

    404 when the name is not in the runtime store. Built-in executor names
    can never be removed (422) — the store never legitimately holds them.

    The removal is audited to runtime-audit.db with the same row shape as
    registration (``task_id='executor:<name>'``, payload {command,
    argv_template, adapter}); action verb ``executor_removed``.
    """
    registry = get_registry()

    # Serialize against concurrent register/remove for the same name —
    # same per-profile-name lock as the register routes.
    profile_lock = _acquire_profile_lock(name)
    try:
        stored = load_runtime_profiles()
        entry = stored.get(name)
        if entry is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No custom executor profile named {name!r} "
                f"in the runtime store",
            )

        existing = registry.get_profile(name)
        if existing is not None and existing.kind == "builtin":
            # Pathological hand-edited store carrying a built-in name:
            # refuse without touching either surface.
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "builtin_collision",
                    "name": name,
                    "detail": f"Cannot remove built-in executor {name!r}.",
                },
            )

        # 1. Durable: remove from the machine-global runtime store (the
        #    source of truth) — mirrors register's durable-first ordering.
        remove_runtime_profile(name)

        # 2. In-memory: clear the transient process-wide profile so the
        #    removed executor is immediately unresolvable (no restart
        #    needed). No-op when the profile was never loaded in-process.
        registry.unregister_custom_profile(name)

        # 3. Audit the removal (mirrors _audit_runtime_registration).
        argv = entry.get("argv_template")
        _audit_runtime_removal(
            profile_name=name,
            command=str(entry.get("command") or ""),
            argv_template=argv if isinstance(argv, list) else [],
            adapter=str(entry.get("adapter") or ""),
        )
    finally:
        profile_lock.release()

    return RemoveRuntimeProfileResponse(name=name, removed=True)
