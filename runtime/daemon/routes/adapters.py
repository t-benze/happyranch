"""Custom adapter registration routes (THR-107 D3 + D4 + seq141 + seq184).

POST /api/v1/runtime/adapters/register
    Register a custom adapter executable. Validates absolute path, computes
    SHA-256, runs conformance probe, persists as PENDING only.

POST /api/v1/runtime/adapters/{adapter_id}/approve  (D4)
    Founder-gated explicit approval gate. Binds exact durable artifact
    snapshot, transitions PENDING → APPROVED, persists approved_at +
    approved_by provenance.

POST /api/v1/runtime/adapters/submit (THR-107 seq141)
    Loopback-only, registration-token-scoped adapter submission. The
    candidate CLI submits its v1 adapter wrapper executable for a specific
    intended profile. Creates/re-registers ONLY that exact adapter, ONLY
    PENDING. Never approves, binds, or resolves.

POST /api/v1/runtime/adapters/{adapter_id}/bind-profile (THR-107 seq141)
    Standard bearer-authenticated management endpoint. Binds the
    intended profile name to an APPROVED adapter id via
    command_adapter_id: custom-adapter:<id>. Rejects PENDING, unknown,
    mismatched-profile, hash-changed/missing/stale adapters, built-in-name
    collisions, and generic-token callers.

GET /api/v1/runtime/adapters/contract-reference (THR-107 seq184)
    Loopback-only, registration-token-scoped contract-reference endpoint.
    Returns the canonical v1 AdapterInput/AdapterOutput JSON Schemas
    generated from the authoritative Pydantic models, plus version, output
    rules, and submission metadata. Reachable during registration through
    the existing scoped registration-token posture on loopback.
    Does NOT consume the token. Enforces adapter-purpose token at the
    route consumer; non-adapter-purpose tokens are rejected.

GET /api/v1/runtime/adapters
    List all registered custom adapters (D4: includes approved_at/by).

GET /api/v1/runtime/adapters/{adapter_id}
    Get a single adapter by id (D4: includes approved_at/by).

D4 scope: approval transition only. No D5 permission/sandbox expansion,
D7 profile binding/launch, D12 ExecutorResult/protocol changes, SQLite
changes, or auth/bearer-flow changes.
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from runtime.daemon.auth import require_registration_token, require_token
from runtime.orchestrator.adapter_store import acquire_store_lock, release_store_lock
from runtime.orchestrator.custom_adapter_registry import (
    approve_adapter,
    generate_adapter_id,
    get_adapter,
    list_adapters,
    register_custom_adapter,
    resolve_adapter,
)
from runtime.orchestrator.runtime_executor_store import (
    load_runtime_profiles,
    remove_runtime_profile,
    save_runtime_profile,
)

router = APIRouter(dependencies=[require_token()])

# Separate router for the seq141 submission endpoint — must NOT inherit
# master-bearer _check_token. It accepts ONLY registration-token auth.
submit_router = APIRouter()

# THR-107 seq184 contract-reference router — must NOT inherit master-bearer
# _check_token. Accepts ONLY registration-token auth (loopback + hrreg_ token).
# Does NOT consume the token; enforces adapter-purpose at the route consumer.
contract_reference_router = APIRouter()


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class AdapterRegisterRequest(BaseModel):
    """Request body for custom adapter registration.

    D3 fields only — no approval, no profile binding, no sandbox flags.
    """

    executable: str = Field(
        ...,
        description="Absolute path to the adapter executable. PATH lookup is not supported.",
        min_length=1,
    )
    version: str = Field(
        ...,
        description="Adapter version string (e.g. '1.0.0').",
        min_length=1,
    )
    capabilities: list[str] = Field(
        default_factory=list,
        description="Declared capabilities (e.g. ['token_metering']).",
    )
    workspace_adapter: str = Field(
        default="pi",
        description="Workspace preparation adapter: claude, codex, opencode, or pi.",
    )

    model_config = {"extra": "forbid"}


class AdapterEntryResponse(BaseModel):
    """Response body for a registered custom adapter entry.

    Mirrors ``AdapterEntry`` fields. D4: includes approved_at/approved_by.
    THR-107 seq141: includes intended_profile_name.
    """

    id: str
    name: str
    executable: str
    executable_hash: str
    version: str
    capabilities: list[str]
    contract_version: int
    workspace_adapter: str
    status: str
    registered_at: str
    registered_by: str
    approved_at: str | None = None
    approved_by: str | None = None
    intended_profile_name: str | None = None


class AdapterSubmitRequest(BaseModel):
    """Request body for the seq141 loopback-only adapter-submission endpoint.

    The candidate CLI submits its v1 adapter wrapper executable. The token
    carries the intended profile name — the candidate request does NOT
    choose a different adapter target.
    """

    executable: str = Field(
        ...,
        description="Absolute path to the adapter executable (v1 AdapterInput/AdapterOutput wrapper).",
        min_length=1,
    )
    version: str = Field(
        ...,
        description="Adapter version string (e.g. '1.0.0').",
        min_length=1,
    )
    capabilities: list[str] = Field(
        default_factory=list,
        description="Declared capabilities.",
    )
    workspace_adapter: str = Field(
        default="pi",
        description="Workspace preparation adapter.",
    )

    model_config = {"extra": "forbid"}


class BindProfileRequest(BaseModel):
    """Request body for the seq141 profile-binding management endpoint.

    The caller provides the profile name to bind. The server verifies:
    - adapter exists and is APPROVED
    - adapter's intended_profile_name matches the request's profile_name
    - D7B custom-adapter validation passes
    - profile_name does not collide with a built-in
    """

    profile_name: str = Field(
        ...,
        min_length=1,
        description="The executor profile name to bind to the approved adapter.",
    )

    model_config = {"extra": "forbid"}


class AdapterApproveRequest(BaseModel):
    """Request body for the D4 founder-gated approval route.

    Every field MUST match the durable store entry exactly.
    This binds the exact artifact snapshot the founder inspected.
    """

    executable: str = Field(
        ...,
        description="Absolute path to the adapter executable (must match store exactly).",
        min_length=1,
    )
    executable_hash: str = Field(
        ...,
        description="SHA-256 hex digest of the executable (must match store exactly).",
        min_length=1,
    )
    version: str = Field(
        ...,
        description="Adapter version string (must match store exactly).",
        min_length=1,
    )
    capabilities: list[str] = Field(
        ...,
        description="Declared capabilities (must match store exactly).",
    )
    contract_version: int = Field(
        ...,
        description="Contract version (must match store exactly).",
    )
    workspace_adapter: str = Field(
        ...,
        description="Workspace preparation adapter (must match store exactly).",
        min_length=1,
    )

    model_config = {"extra": "forbid"}


def _entry_to_response(entry) -> AdapterEntryResponse:
    """Map an ``AdapterEntry`` to the response model."""
    return AdapterEntryResponse(
        id=entry.id,
        name=entry.name,
        executable=entry.executable,
        executable_hash=entry.executable_hash,
        version=entry.version,
        capabilities=entry.capabilities,
        contract_version=entry.contract_version,
        workspace_adapter=entry.workspace_adapter,
        status=entry.status,
        registered_at=entry.registered_at,
        registered_by=entry.registered_by,
        approved_at=entry.approved_at,
        approved_by=entry.approved_by,
        intended_profile_name=entry.intended_profile_name,
    )


# Local-host policy — the same set used by the auth mint routes.
_LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost"}


def _extract_registration_token(request: Request) -> str:
    """Extract the registration token value from the Authorization header.

    Raises 401 if the header is missing or malformed.
    """
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing Authorization header or not Bearer",
        )
    return auth.removeprefix("Bearer ").strip()


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post("/runtime/adapters/register")
def register_adapter(body: AdapterRegisterRequest) -> AdapterEntryResponse:
    """Register a custom adapter executable.

    Validates the executable (absolute path, regular file, executable),
    computes SHA-256, runs a bounded conformance probe using the versioned
    ``AdapterInput``/``AdapterOutput`` contract, and persists the entry as
    PENDING.

    Re-registration: if the adapter id (derived from the executable filename)
    already exists with different path/hash/capabilities, the new entry
    replaces the old one — always as status="pending". Approval is never
    silently retained.

    D3: all registered adapters are PENDING. No adapter may be resolved for
    a profile or launched by the executor runtime. Approval/activation
    (D4) and profile binding (D7) are separate, founder-gated slices.
    """
    try:
        entry = register_custom_adapter(
            executable=body.executable,
            version=body.version,
            capabilities=body.capabilities,
            workspace_adapter=body.workspace_adapter,
            registered_by="",
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        )
    return _entry_to_response(entry)


@router.get("/runtime/adapters")
def list_registered_adapters() -> list[AdapterEntryResponse]:
    """List all registered custom adapters.

    D3: all entries are status "pending" only.
    """
    entries = list_adapters()
    return [_entry_to_response(e) for e in entries]


@router.get("/runtime/adapters/{adapter_id}")
def get_adapter_entry(adapter_id: str) -> AdapterEntryResponse:
    """Get a single registered custom adapter by id (read-only inspection).

    Uses the internal read-only ``get_adapter`` query — NOT ``resolve_adapter``,
    which is the binding/launch seam that rejects PENDING entries. This GET
    route exposes inspection regardless of status.

    Returns 404 if not found.
    """
    entry = get_adapter(adapter_id)
    if entry is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Adapter {adapter_id!r} not found",
        )
    return _entry_to_response(entry)


@router.post("/runtime/adapters/{adapter_id}/approve")
def approve_registered_adapter(
    adapter_id: str,
    body: AdapterApproveRequest,
) -> AdapterEntryResponse:
    """Approve a pending custom adapter (D4 founder-gated approval gate).

    This is a deliberate, explicit transition from durable PENDING to durable
    APPROVED. The request body carries the exact durable artifact snapshot the
    founder inspected — every material identity fact (executable, hash, version,
    capabilities, contract_version, workspace_adapter) is compared against the
    durable store entry.

    Exact-idempotence: if the adapter is already APPROVED with identical stored
    immutable facts, the existing entry is returned unchanged.

    Fails with 422 when:
      - Unknown adapter id
      - Entry is not PENDING (already-approved incompatible repeat, non-pending)
      - Any snapshot fact mismatches the store
      - Malformed/empty values

    This is an agent-only administrative route — no browser consumer exists.
    D5/D7/D12 changes are NOT authorized by this route.
    """
    try:
        entry = approve_adapter(
            adapter_id=adapter_id,
            executable=body.executable,
            executable_hash=body.executable_hash,
            version=body.version,
            capabilities=body.capabilities,
            contract_version=body.contract_version,
            workspace_adapter=body.workspace_adapter,
            approved_by="founder/master-bearer",
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        )
    return _entry_to_response(entry)


# ---------------------------------------------------------------------------
# THR-107 seq141: adapter submission + profile binding
# ---------------------------------------------------------------------------


@submit_router.post(
    "/runtime/adapters/submit",
    dependencies=[require_registration_token()],
)
def submit_adapter(
    request: Request,
    body: AdapterSubmitRequest,
) -> AdapterEntryResponse:
    """Submit a custom adapter executable via a scoped registration token.

    Loopback-only, registration-token-scoped adapter submission endpoint
    (THR-107 seq141). The candidate CLI submits its v1 adapter wrapper
    executable for a specific intended profile.

    Gating checks (exact order, every rejection returns 422 with a
    concrete error detail):
    1. Request is loopback (127.0.0.1, ::1, localhost)
       (checked by require_registration_token dependency)
    2. Token is a valid ``hrreg_`` runtime registration token
       (checked by require_registration_token dependency)
    3. Token purpose is exactly ``'adapter'``
    4. Token's intended_profile_name is present and non-empty
    5. Conformance challenge is complete
    6. Server-derived adapter id (``<profile>-adapter``) is computed
       — the candidate request MUST NOT choose a different target
    7. The submission creates/re-registers ONLY that exact adapter,
       ONLY as PENDING

    The token is consumed on success. On any failure the token is
    released so it remains retryable (within TTL boundaries).

    Never approves, resolves, launches, binds a profile, or accepts
    master bearer as an alternative.
    """
    # Extract raw token from Authorization header
    raw_token = _extract_registration_token(request)
    store = request.app.state.daemon.registration_token_store
    token_record = store.validate_runtime(raw_token)
    if token_record is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired registration token",
        )

    # 3. Token purpose must be exactly 'adapter'
    if token_record.purpose != "adapter":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Token purpose is {token_record.purpose!r}, not 'adapter'. "
                f"Adapter submission requires an adapter-purpose token."
            ),
        )

    # 4. Token's intended_profile_name must be present
    if not token_record.intended_profile_name:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Token is missing intended_profile_name — this token was not minted for adapter submission.",
        )
    intended_profile = token_record.intended_profile_name

    # 5. Conformance challenge must be complete
    if not store.is_challenge_complete_runtime(raw_token):
        pending = store.get_pending_steps_runtime(raw_token)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "challenge_incomplete",
                "pending_steps": pending or [],
            },
        )

    # Reserve the token so concurrent submissions get single-winner
    if not store.reserve_runtime(raw_token):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Token is already reserved or consumed by a concurrent submission.",
        )

    try:
        entry = register_custom_adapter(
            executable=body.executable,
            version=body.version,
            capabilities=body.capabilities,
            workspace_adapter=body.workspace_adapter,
            registered_by=f"adapter-submission:{intended_profile}",
            intended_profile_name=intended_profile,
        )
    except ValueError as exc:
        # Release the token on failure so it remains retryable
        store.release_runtime(raw_token)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        )

    # Consume the token permanently on success
    store.commit_runtime(raw_token)

    return _entry_to_response(entry)


# ---------------------------------------------------------------------------
# THR-107 seq184: adapter contract-reference endpoint
# ---------------------------------------------------------------------------


@contract_reference_router.get(
    "/runtime/adapters/contract-reference",
    dependencies=[require_registration_token()],
)
def get_contract_reference(request: Request) -> dict:
    """Return the canonical v1 AdapterInput/AdapterOutput contract reference.

    Loopback-only, registration-token-scoped (THR-107 seq184). The candidate
    CLI fetches this reference FIRST to learn the exact AdapterInput and
    AdapterOutput JSON Schemas before implementing a wrapper.

    Auth scope:
    - Must be loopback (127.0.0.1, ::1, localhost) — enforced by
      require_registration_token dependency.
    - Must carry a valid, unexpired, unconsumed ``hrreg_`` token.
    - Token purpose MUST be ``'adapter'`` — non-adapter-purpose tokens are
      rejected with 422. Master bearer is rejected at the dependency level.
    - Reading the contract-reference does NOT consume, reserve, or modify
      the token — the candidate may fetch this multiple times and still
      proceed to conformance check-ins and submission.

    Response shape (stable v1):
    - ``contract_version``: int (1)
    - ``adapter_input_schema``: JSON Schema for AdapterInput (generated from
      the authoritative Pydantic model at
      runtime/orchestrator/adapter_contract.py)
    - ``adapter_output_schema``: JSON Schema for AdapterOutput (same source)
    - ``rules``: output constraints (max size, stdout/stderr contract,
      exactly-one-object rule)
    - ``submission``: the submit endpoint URL, method, and content-type for
      the adapter submission step
    """
    raw_token = _extract_registration_token(request)
    store = request.app.state.daemon.registration_token_store
    token_record = store.validate_runtime(raw_token)
    if token_record is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired registration token",
        )

    # Enforce adapter-purpose token at the route consumer.
    if token_record.purpose != "adapter":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Token purpose is {token_record.purpose!r}, not 'adapter'. "
                f"The contract-reference endpoint requires an adapter-purpose token."
            ),
        )

    # Token is intentionally NOT consumed — this is a read-only reference
    # endpoint. The candidate may fetch the contract multiple times before
    # proceeding to conformance check-ins and submission.

    from runtime.orchestrator.adapter_contract import AdapterInput, AdapterOutput
    from runtime.orchestrator.custom_adapter_registry import build_probe_input

    # Build a minimal self-test fixture from the real probe builder.
    probe_input = build_probe_input("example-adapter")
    probe_fixture = {
        "description": (
            "A minimal self-test input/output fixture. The adapter receives "
            "this AdapterInput on stdin and MUST respond with an AdapterOutput "
            "where success=true. The server prepares/creates the probe workspace "
            "at the workspace path in the input — the adapter does not need to "
            "create it. The adapter has one 30-second wall-clock deadline "
            "(including post-EOF wait for the subprocess to exit after closing "
            "its stdout). Stdout and stderr are each capped at 1 MB. Only a "
            "single JSON AdapterOutput object is accepted on stdout."
        ),
        "input": json.loads(probe_input.model_dump_json()),
        "expected_output": {
            "success": True,
            "duration_seconds": 0,
            "session_id": "probe-sess-00000000-0000-0000-0000-000000000000",
            "returncode": 0,
            "stdout_tail": "conformance probe OK",
            "stderr_tail": "",
            "result": {"text": "conformance probe OK"},
            "token_usage": None,
            "error": None,
            "agent_session_id": None,
            "rate_limited": False,
            "adapter_metadata": {
                "adapter": "example-adapter",
                "adapter_version": "1.0.0",
                "contract_version": 1,
            },
            "child_session_id": None,
            "raw_forensics_ref": None,
        },
    }

    return {
        "contract_version": 1,
        "adapter_input_schema": AdapterInput.model_json_schema(),
        "adapter_output_schema": AdapterOutput.model_json_schema(),
        "rules": {
            "input": {
                "source": "stdin",
                "description": (
                    "Read exactly one v1 AdapterInput JSON object from stdin. "
                    "The daemon pipes the AdapterInput payload to the adapter's "
                    "stdin at launch time; the adapter must read it fully before "
                    "invoking the candidate CLI."
                ),
            },
            "output": {
                "target": "stdout",
                "description": (
                    "Write exactly one v1 AdapterOutput JSON object to stdout, "
                    "then exit. No non-JSON diagnostics, logging, or commentary "
                    "may appear on stdout."
                ),
                "max_size_bytes": 1_048_576,
                "max_size_human": "1 MB",
            },
            "diagnostics": {
                "target": "stderr",
                "description": "All diagnostics, logging, and error messages must go to stderr only.",
            },
            "exit": {
                "description": (
                    "Exit after writing the AdapterOutput JSON. The adapter is a "
                    "single-invocation wrapper; it must not loop, daemonize, or "
                    "persist across invocations."
                ),
            },
        },
        "submission": {
            "method": "POST",
            "path": "/api/v1/runtime/adapters/submit",
            "content_type": "application/json",
            "description": (
                "Submit the adapter wrapper executable for the intended profile. "
                "Requires the same adapter-purpose hrreg_ token and a completed "
                "conformance challenge. Submission creates ONLY the exact PENDING "
                "adapter; founder approval and management binding are separate steps."
            ),
        },
        "probe": {
            "description": (
                "Before registration, the server runs a conformance probe against "
                "the submitted executable. The server prepares/creates the probe "
                "workspace directory at the path specified in the probe input. "
                "The adapter has one 30-second wall-clock deadline including "
                "post-EOF wait. It must write exactly one syntactically valid "
                "AdapterOutput JSON object to stdout with success=true. Stdout "
                "and stderr are each capped at 1 MB. Diagnostics, logging, and "
                "errors must go to stderr only. A syntactically valid "
                "AdapterOutput with success=false returns a 4xx detail containing "
                "the safely capped error field and stderr_tail sufficient for "
                "debugging without daemon source access."
            ),
            "deadline_seconds": 30,
            "max_stdout_bytes": 1_048_576,
            "max_stderr_bytes": 1_048_576,
            "requires_success_true": True,
            "self_test_fixture": probe_fixture,
        },
    }


# ---------------------------------------------------------------------------
# Audit helper for adapter binding (mirrors _audit_runtime_registration in
# executors.py — same runtime-audit.db, same scope-prefix convention).
# ---------------------------------------------------------------------------


def _audit_adapter_bind(
    *,
    profile_name: str,
    adapter_id: str,
    workspace_adapter: str,
    actor: str = "founder",
) -> None:
    """Write a runtime-level adapter-profile binding audit row.

    Opens (creating if needed) a dedicated runtime-audit.db under
    daemon_home(), then writes a single audit_log row.  Each call opens a
    fresh ``Database`` handle and closes it.

    Row shape:
      task_id = "executor:<profile_name>"
      action  = "executor_registered"
      payload = {adapter_id, command_adapter_id, workspace_adapter_id}
    """
    from runtime.infrastructure.database import Database
    from runtime.runtime import daemon_home

    audit_db_path = daemon_home() / "runtime-audit.db"
    db = Database(audit_db_path)
    try:
        db.insert_audit_log_uncommitted(
            task_id=f"executor:{profile_name}",
            agent=actor,
            action="executor_registered",
            payload={
                "adapter_id": adapter_id,
                "command_adapter_id": f"custom-adapter:{adapter_id}",
                "workspace_adapter_id": workspace_adapter,
            },
        )
        db.commit()
    finally:
        db.close()


@router.post(
    "/runtime/adapters/{adapter_id}/bind-profile",
    dependencies=[require_token()],
)
def bind_adapter_profile(
    adapter_id: str,
    body: BindProfileRequest,
) -> dict:
    """Bind a profile name to an APPROVED custom adapter (THR-107 seq141).

    Standard daemon-bearer management endpoint. Binds the intended profile
    name to an APPROVED adapter id via ``command_adapter_id:
    custom-adapter:<id>``.

    Gating checks (exact order):
    1. Adapter exists (404 if unknown)
    2. Adapter is APPROVED (422 if PENDING or unknown status)
    3. Adapter's intended_profile_name matches request profile_name (422)
    4. Profile name does not collide with a built-in (422)
    5. On-disk adapter is still executable with matching SHA-256 (422)
    6. D7B custom-adapter validation passes (orchestrator-rejected → 422)
    7. Persist as custom-adapter profile atomically

    On any post-durable failure, restore pre-request state.

    Returns the created profile entry.
    """
    profile_name = body.profile_name.strip()

    # 1. Adapter exists
    entry = get_adapter(adapter_id)
    if entry is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Adapter {adapter_id!r} not found.",
        )

    # 2. Must be APPROVED
    if entry.status != "approved":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Adapter {adapter_id!r} is status={entry.status!r}, "
                f"not APPROVED. The adapter must be founder-approved "
                f"before profile binding."
            ),
        )

    # 3. intended_profile_name must match
    if entry.intended_profile_name != profile_name:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Adapter {adapter_id!r} is bound to intended profile "
                f"{entry.intended_profile_name!r}, not {profile_name!r}. "
                f"Cannot cross-bind adapters to different profiles."
            ),
        )

    # 4. Profile name must not collide with a built-in
    from runtime.orchestrator.executor_registry import get_registry
    registry = get_registry()
    existing = registry.get_profile(profile_name)
    if existing is not None and existing.kind == "builtin":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Profile name {profile_name!r} collides with a built-in "
                f"executor. Choose a different name."
            ),
        )

    # 5. On-disk integrity check via resolve_adapter (re-validates hash)
    resolved = resolve_adapter(adapter_id)
    if resolved is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Adapter {adapter_id!r} is approved but the on-disk "
                f"executable is missing, not executable, or has a hash "
                f"mismatch. Re-register the adapter."
            ),
        )

    # 6. D7B custom-adapter validation
    from runtime.orchestrator.executor_registry import ExecutorRegistry
    try:
        ExecutorRegistry._validate_custom_adapter_binding(adapter_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        )

    # 7. Persist profile atomically under the adapter-store lock.
    #
    # Lock order (documented, compatible with approval/re-registration):
    #   adapter_store_lock → durable runtime profile write → in-memory registry
    #
    # acquire_store_lock serializes against register_custom_adapter (submit)
    # and approve_adapter — both of which hold the same lock across their
    # critical sections.  Under the lock we re-read and re-validate the exact
    # adapter snapshot immediately before the durable profile write so no
    # concurrent re-registration can replace the adapter with PENDING in the
    # interval.
    acquire_store_lock()
    try:
        # Re-read the exact adapter snapshot under the lock.
        re_read_entry = get_adapter(adapter_id)
        if re_read_entry is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Adapter {adapter_id!r} disappeared before bind.",
            )
        if re_read_entry.status != "approved":
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"Adapter {adapter_id!r} changed to status={re_read_entry.status!r} "
                    f"before bind. The adapter must be founder-approved."
                ),
            )
        if re_read_entry.intended_profile_name != profile_name:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"Adapter {adapter_id!r} intended profile changed to "
                    f"{re_read_entry.intended_profile_name!r} before bind."
                ),
            )
        # Re-verify on-disk hash integrity under lock.
        re_resolved = resolve_adapter(adapter_id)
        if re_resolved is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"Adapter {adapter_id!r} on-disk artifact changed before bind. "
                    f"Re-register the adapter."
                ),
            )
        # Re-verify D7B validation
        try:
            ExecutorRegistry._validate_custom_adapter_binding(adapter_id)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=str(exc),
            )

        profile_cfg = {
            "command": None,
            "argv_template": None,
            "workspace_adapter_id": re_read_entry.workspace_adapter,
            "command_adapter_id": f"custom-adapter:{adapter_id}",
        }

        # (a) Validate config → build ExecutorProfile
        try:
            profile = ExecutorRegistry.validate_custom_profile_config(
                profile_name, profile_cfg
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=str(exc),
            )

        # Snapshot pre-request state for compensating rollback.
        pre_request_profiles = dict(load_runtime_profiles())
        pre_request_in_memory = registry.get_profile(profile_name)
        durable_committed = False

        # (b) Write the durable runtime store first.
        #     durable_committed becomes True immediately so that any
        #     post-save failure (registry, audit) triggers compensating
        #     rollback — including ExecutorProfileCollisionError from
        #     a legitimate active-registry replacement failure.
        save_runtime_profile(profile_name, profile_cfg)
        durable_committed = True

        # (c) Replace in the in-memory registry (D7A atomic-replacement seam).
        #     Uses replace_custom_profile to support the authorized
        #     legacy/simple → approved-adapter upgrade: a pre-existing
        #     non-builtin custom profile with the same name but different
        #     definition is safely replaced rather than raising
        #     ExecutorProfileCollisionError.
        registry.replace_custom_profile(profile)

        # (d) Audit the successful registration.
        #     Uses the same scope-prefix convention as executors.py:
        #     task_id = "executor:<profile_name>".
        _audit_adapter_bind(
            profile_name=profile_name,
            adapter_id=adapter_id,
            workspace_adapter=re_read_entry.workspace_adapter,
        )
    except HTTPException:
        raise
    except BaseException:
        if durable_committed:
            # Compensating rollback: restore both durable and in-memory
            # surfaces to pre-request state (mirrors TASK-3567).
            # Also covers ExecutorProfileCollisionError from the in-memory
            # registry write that occurs after the durable commit.
            if profile_name in pre_request_profiles:
                save_runtime_profile(profile_name, pre_request_profiles[profile_name])
            else:
                remove_runtime_profile(profile_name)
            if pre_request_in_memory is not None:
                registry.replace_custom_profile(pre_request_in_memory)
            else:
                registry.unregister_custom_profile(profile_name)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "Profile binding failed after durable write; "
                "pre-request state has been restored."
            ),
        )
    finally:
        release_store_lock()

    return {
        "profile_name": profile.name,
        "command_adapter_id": profile.command_adapter_id,
        "workspace_adapter_id": profile.workspace_adapter_id,
        "kind": profile.kind,
        "status": "connected",
        "adapter_id": adapter_id,
    }
