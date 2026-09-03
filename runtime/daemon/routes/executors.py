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

import logging
import threading

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field

from runtime.daemon.auth import require_registration_token, require_token
from runtime.daemon.registration_token import REGISTRATION_TOKEN_PREFIX
from runtime.orchestrator.executor_binary_registry import (
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
logger = logging.getLogger(__name__)

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


def _audit_runtime_removal(
    *,
    profile_name: str,
    command_adapter_id: str,
    actor: str = "founder",
) -> None:
    """Write a runtime-level executor removal audit row.

    Mirrors ``_audit_runtime_registration`` — same dedicated
    runtime-audit.db under daemon_home(), same scope-prefix task_id
    convention, same payload keys; only the action verb differs.

    Row shape (THR-107 S4a):
      task_id = "executor:<profile_name>"
      action  = "executor_removed"
      payload = {workspace_adapter_id, command_adapter_id}
    """
    from runtime.runtime import daemon_home

    audit_db_path = daemon_home() / "runtime-audit.db"
    db = Database(audit_db_path)
    try:
        logger = AuditLogger(db)
        logger.log_executor_removed(profile_name=profile_name,
                                    command_adapter_id=command_adapter_id,
                                    actor=actor)
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
    """Retired legacy registration payload; all fields are rejected."""
    model_config = ConfigDict(extra="allow")


_RETIRED_PROFILE_REGISTRATION = (
    "Legacy executor profile registration is retired. Register and approve an "
    "adapter executable, then bind the profile with "
    "command_adapter_id='custom-adapter:<id>'."
)


class ExecutorRegisterResponse(BaseModel):
    detail: str


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
) -> dict[str, str]:
    """Legacy registration is retired; custom adapters bind through the adapter routes."""
    raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=_RETIRED_PROFILE_REGISTRATION)


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
) -> dict[str, str]:
    """Legacy registration is retired; custom adapters bind through adapter routes."""
    raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=_RETIRED_PROFILE_REGISTRATION)


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
            detail=(
                "Registration token is invalid, expired, consumed, or not a runtime "
                "token. Regenerate the connect prompt from Settings > Executors or "
                "onboarding and run the full sequence again."
            ),
        )

    # 2. Assert purpose == 'binary'
    if record.purpose != "binary":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "token_purpose_mismatch",
                "expected": "binary",
                "actual": record.purpose,
                "hint": (
                    "This token was minted for a different purpose. "
                    "Built-in binary registration requires a purpose='binary' "
                    "token — regenerate from the built-in executor dropdown."
                ),
            },
        )

    kind = record.name  # The token's name IS the executor kind

    # 3. Conformance must be complete (SAME model as runtime_register_executor)
    if not store.is_challenge_complete_runtime(token_value):
        pending = store.get_pending_steps_runtime(token_value) or []
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Conformance incomplete — {len(pending)} step(s) remaining: "
                f"{pending}. Each step must be completed sequentially and the "
                f"emit_envelope (fourth) response must report "
                f"\"all_complete\":true before calling register-binary. "
                f"The token remains valid — retry after completing all steps."
            ),
        )

    # 4. Validate the binary path BEFORE any side effects
    try:
        validated = validate_binary(body.path)
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
            detail=(
                "Registration token could not be reserved — it may have been "
                "consumed by a concurrent request or expired. Regenerate the "
                "connect prompt and run the sequence again."
            ),
        )

    try:
        # 6. Write the binary path to the machine-local registry
        set_binary(kind, validated)
    except Exception:
        store.release_runtime(token_value)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "Failed to persist the binary path. The token has been released "
                "and can be retried. If the problem persists, check that the "
                "HappyRanch daemon has write access to its data directory."
            ),
        )
    else:
        # 7. COMMIT (permanent consume) ONLY on clean success
        store.commit_runtime(token_value)

    return RegisterBinaryResponse(
        kind=kind,
        path=validated,
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
    # D6 canonical fields
    workspace_adapter_id: str | None = Field(
        None, description="Workspace adapter id (claude/codex/opencode/pi) — canonical (D6)"
    )
    command_adapter_id: str | None = Field(
        None,
        description=(
            "Command adapter for execution — canonical (D6). "
            "Must be 'custom-adapter:<id>' (bound to a separately registered, "
            "founder-approved, hash-verified custom adapter executable — "
            "D7B, subprocess-only, mandatory v1 AdapterInput/AdapterOutput, "
            "D5 baseline-only posture)."
        ),
    )
    # DEPRECATED aliases (D6 — preserved for backward compat)
    adapter: str | None = Field(
        None, deprecated=True, description="Deprecated. Use workspace_adapter_id."
    )
    adapter_id: str | None = Field(
        None, deprecated=True,
        description="Deprecated alias for workspace_adapter_id. Same meaning as adapter.",
    )
    present: bool = Field(
        False,
        description=(
            "True when the profile has an entry in the machine-local "
            "binary registry (executors.json) keyed by the profile name "
            "with a valid stored path (THR-107 seq155). "
            "Custom profiles no longer derive presence from PATH "
            "resolvability — binary registration is the sole gate."
        ),
    )
    path: str | None = Field(
        None, description="The registered absolute path when present, else None"
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
    reports each profile's name, command, and adapter.

    **Custom profiles** derive ``present``/``path`` from the
    machine-local binary registry (``executors.json``) keyed by the
    profile name — the same gating as built-ins (THR-107 seq155).
    No PATH-based fallback is used.  The profile's declared ``command``
    is informational only.

    **Built-in profiles** (claude/codex/opencode/pi) are not returned by
    this route — it lists only custom profiles from the runtime store.
    Built-in presence remains registry-gated via ``/health/prereqs``.

    Malformed or missing ``command`` → present false, path null.

    Honesty fence: only real store data — no invented status.
    """
    stored = load_runtime_profiles()
    entries: list[RuntimeProfileEntry] = []
    for name in sorted(stored.keys()):
        entry = stored[name]
        # ── Determine present/path for this profile ──
        # Custom-adapter profiles: eligibility is from the adapter store
        # (APPROVED + hash-verified), NOT from executors.json.
        cmd_adapter_raw = entry.get("command_adapter_id")
        if isinstance(cmd_adapter_raw, str) and cmd_adapter_raw.startswith("custom-adapter:"):
            # Build a temporary ExecutorProfile for the eligibility check
            from runtime.orchestrator.executor_registry import ExecutorRegistry, ExecutorProfile
            temp_profile = ExecutorProfile(
                name=name,
                kind="custom",
                command_adapter_id=cmd_adapter_raw,
            )
            eligibility = ExecutorRegistry._resolve_custom_adapter_eligibility(temp_profile)
            present = eligibility is not None
            path = eligibility["executable"] if eligibility else None
        else:
            continue
        resolved_command_adapter: str | None = None
        cmd_canon = entry.get("command_adapter_id")
        if isinstance(cmd_canon, str):
            resolved_command_adapter = cmd_canon
        # D6: dual-read workspace adapter from store — canonical key
        # workspace_adapter_id wins, deprecated adapter/adapter_id
        # provide fallback.
        resolved_adapter: str | None = None
        ws_canon = entry.get("workspace_adapter_id")
        if isinstance(ws_canon, str):
            resolved_adapter = ws_canon
        else:
            adapter = entry.get("adapter")
            if isinstance(adapter, str):
                resolved_adapter = adapter
            else:
                adapter_id_val = entry.get("adapter_id")
                if isinstance(adapter_id_val, str):
                    resolved_adapter = adapter_id_val
        entries.append(RuntimeProfileEntry(
            name=name,
            # D6 canonical fields
            workspace_adapter_id=resolved_adapter,
            command_adapter_id=resolved_command_adapter,
            # D6 deprecated aliases (preserved for backward compat)
            adapter=resolved_adapter,
            adapter_id=resolved_adapter,
            present=present,
            path=path,
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
    registration (``task_id='executor:<name>'``, payload with canonical
    adapter identity); action verb ``executor_removed``.
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

        # 3. A direct-connect adapter is owned by its profile. Once the
        # profile is gone from both stores, remove an unbound one as well.
        # Founder-approved submission adapters deliberately remain reusable.
        command_adapter_id = entry.get("command_adapter_id")
        if isinstance(command_adapter_id, str) and command_adapter_id.startswith("custom-adapter:"):
            adapter_id = command_adapter_id.removeprefix("custom-adapter:")
            from runtime.daemon.routes.adapters import (
                AdapterRemovalAuditError,
                remove_unbound_direct_connect_adapter,
            )

            try:
                remove_unbound_direct_connect_adapter(adapter_id)
            except AdapterRemovalAuditError:
                # Steps 1-2 have already durably removed the profile.  The
                # helper restores the adapter, so reporting a failed profile
                # removal here would be a false negative.
                logger.warning(
                    "Restored direct-connect adapter after nested cleanup audit failure",
                    extra={"adapter_id": adapter_id, "profile_name": name},
                    exc_info=True,
                )

        # 4. Audit the removal (mirrors _audit_runtime_registration).
        _audit_runtime_removal(
            profile_name=name,
            command_adapter_id=str(entry.get("command_adapter_id") or ""),
        )
    finally:
        profile_lock.release()

    return RemoveRuntimeProfileResponse(name=name, removed=True)
