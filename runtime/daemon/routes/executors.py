"""Executor registration routes — THR-052 PR-2.

POST /api/v1/orgs/{slug}/executors/conformance-checkin
    Loopback-only, scoped-token-only. Records a conformance step arrival
    for a pending registration token. The candidate CLI calls this for
    each required check-in step (workspace_access, loopback_reachable,
    cli_callback) before attempting registration.

POST /api/v1/orgs/{slug}/executors/register
    Loopback-only, scoped-token-only. Consumes a fully-conformant
    registration token, validates the profile, atomically writes it into
    org/config.yaml, and audits the write.
"""
from __future__ import annotations

import threading

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from runtime.daemon.auth import require_registration_token
from runtime.daemon.registration_token import REGISTRATION_TOKEN_PREFIX
from runtime.daemon.routes._org_dep import OrgDep
from runtime.infrastructure.audit_logger import AuditLogger
from runtime.orchestrator._paths import OrgPaths
from runtime.orchestrator.org_config import (
    OrgConfigError,
    load_org_config,
    write_executor_profile_entry,
)
from runtime.orchestrator.executor_registry import get_registry
from runtime.orchestrator.executor_registry import (
    ExecutorProfileCollisionError,
    ExecutorProfile,
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
    """A single conformance step arrival from the candidate CLI."""
    step_id: str = Field(..., min_length=1)


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
    step (workspace access, loopback reachability, CLI callback).

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
    """Register a custom executor profile and write it to org/config.yaml.

    Requirements (ALL must pass):
    1. Token is valid, unexpired, unconsumed, loopback (checked by
       ``require_registration_token``).
    2. Token record org matches {slug}.
    3. Token record name is used as the profile name (not from body).
    4. Conformance challenge is fully complete.
    5. Static validation passes (valid adapter, command on PATH, valid
       argv_template, no builtin collision).
    6. Token is atomically consumed BEFORE any durable side effects.
       Consume is the atomic validate-and-mark gate: if it returns None
       (expired, already-consumed, wrong-org) the route aborts with 401
       and no config/audit write occurs.
    7. On successful consume: write durable config first, then register
       the in-memory profile. Durable config is the source of truth;
       in-memory registration only happens after the config write
       succeeds so that a config-write failure does not leave a stale
       (unaudited, non-durable) profile in the process-wide registry.

    On success returns 200. On any validation failure returns 4xx
    without touching the config.
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

    # 3. Static validation — reuse ExecutorRegistry primitives
    from runtime.orchestrator.executor_registry import validate_argv_template
    import shutil

    # Validate adapter
    valid_adapters = {"claude", "codex", "opencode", "pi"}
    if body.adapter not in valid_adapters:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid adapter {body.adapter!r}. Must be one of: {sorted(valid_adapters)}",
        )

    # Validate argv_template
    argv_errors = validate_argv_template(body.argv_template)
    if argv_errors:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="; ".join(argv_errors),
        )

    # Validate command on PATH
    resolved = shutil.which(body.command)
    if resolved is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Command {body.command!r} not found on PATH",
        )

    # 4. Build candidate ExecutorProfile for preflight collision check.
    marker = (
        "AGENTS.md"
        if body.adapter in {"codex", "opencode", "pi"}
        else ".claude/skills/start-task/SKILL.md"
    )
    candidate = ExecutorProfile(
        name=profile_name,
        kind="custom",
        adapter_id=body.adapter,
        readiness_marker_fragment=marker,
        argv_template=[str(e) for e in body.argv_template],
        command=body.command,
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

    # 6. Atomically consume the token BEFORE any durable side effects.
    #    This is THE gate: consume() is a locked validate-and-mark that
    #    returns None if the token is expired, already-consumed, or
    #    wrong-org.  Two concurrent requests with the same token will race
    #    through this call — exactly one wins and proceeds to durable writes.
    consumed = store.consume(token_value, slug)
    if consumed is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Registration token is invalid, expired, consumed, or not for this org",
        )

    # 7. Acquire per-profile-name lock for the write+register critical
    #    section.  Two concurrent requests with DIFFERENT tokens for the
    #    same profile name both pass the preflight check (step 5) and
    #    consume (step 6) independently.  The lock serialises the
    #    write+register so that a double-check inside the lock sees the
    #    winner's published profile and rejects the loser with 409.
    #    Without this lock, the loser's config write would overwrite the
    #    winner's before the winner's register_custom_profile completes,
    #    and the loser's register_custom_profile would then raise
    #    ExecutorProfileCollisionError — leaving durable config (loser's)
    #    and in-memory registry (winner's) diverged with no audit.
    #
    #    The lock is acquired AFTER consume so the existing same-token
    #    concurrency test (which uses a threading.Barrier inside consume)
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

        # 8. Durable: write config.yaml entry.
        config_entry = {
            "command": body.command,
            "argv_template": [str(e) for e in body.argv_template],
            "adapter": body.adapter,
        }
        paths = OrgPaths(root=org.root)
        org_config_before = load_org_config(paths)
        before_snapshot = dict(org_config_before.executor_profiles)

        try:
            write_executor_profile_entry(paths, profile_name, config_entry)
        except OrgConfigError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Config write error: {exc}",
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

        # 10. Audit the write
        org_config_after = load_org_config(paths)
        after_snapshot = dict(org_config_after.executor_profiles)
        logger = AuditLogger(org.db)
        logger.log_org_config_write(
            section="executor_profiles",
            tiers=[profile_name],
            before=before_snapshot,
            after=after_snapshot,
            actor="founder",
        )
    finally:
        profile_lock.release()

    return ExecutorRegisterResponse(
        name=profile_name,
        kind="custom",
        adapter_id=body.adapter,
        command=body.command,
        argv_template=[str(e) for e in body.argv_template],
    )
