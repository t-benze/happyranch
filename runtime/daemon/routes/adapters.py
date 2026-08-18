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

POST /api/v1/runtime/adapters/{adapter_id}/reject  (THR-107 seq220)
    Founder-gated pending-adapter rejection gate. Atomically validates
    the exact PENDING durable snapshot and removes it. No persisted
    rejected status; no SQLite/schema change. Audit entry written.

D4 scope: approval transition only. No D5 permission/sandbox expansion,
D7 profile binding/launch, D12 ExecutorResult/protocol changes, SQLite
changes, or auth/bearer-flow changes.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, BeforeValidator, Field

from runtime.orchestrator.adapter_contract import _strict_int_for_manifest

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

    THR-107 seq244: adds ``dependency_manifest_version`` and ``dependencies``
    fields. New submissions MUST declare a non-empty dependencies list with
    ``dependency_manifest_version: 1``.
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
    dependency_manifest_version: Annotated[int, BeforeValidator(_strict_int_for_manifest)] = Field(
        ...,
        ge=1,
        le=1,
        description=(
            "Version of the dependency manifest contract. Required for new "
            "submissions (must be exactly 1)."
        ),
    )
    dependencies: list[dict] = Field(
        ...,
        min_length=1,
        description=(
            "List of declared child executable dependencies. Each entry must "
            "have 'executable' (absolute path) and 'sha256' (SHA-256 hex). "
            "Required and non-empty."
        ),
    )

    model_config = {"extra": "forbid"}


class AdapterEntryResponse(BaseModel):
    """Response body for a registered custom adapter entry.

    Mirrors ``AdapterEntry`` fields. D4: includes approved_at/approved_by.
    THR-107 seq141: includes intended_profile_name.

    THR-107 recovery eligibility (TASK-3784): includes server-authoritative
    ``eligibility`` — derived entirely from durable server state so the
    browser never recomputes hash/tamper eligibility.
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
    dependency_manifest_version: int | None = None
    dependencies: list[dict] = []
    eligibility: str | None = Field(
        None,
        description=(
            "Server-authoritative adapter recovery eligibility. One of:"
            " 'ready_to_bind' — approved, hash-valid, intended profile not yet bound;"
            " 'already_bound' — profile exists and is bound to this adapter;"
            " 'cross_profile' — profile exists but is bound to a DIFFERENT adapter;"
            " 'builtin_collision' — intended profile name is a built-in;"
            " 'tampered' — approved but on-disk hash mismatch or missing;"
            " 'pending' — adapter is PENDING (not yet approved);"
            " 'recovery_ready' — no intended_profile_name set; explicit Bind recovery available;"
            " None — not applicable (not approved, or unknown state)."
        ),
    )


class AdapterSubmitRequest(BaseModel):
    """Request body for the seq141 loopback-only adapter-submission endpoint.

    The candidate CLI submits its v1 adapter wrapper executable. The token
    carries the intended profile name — the candidate request does NOT
    choose a different adapter target.

    THR-107 seq244: adds ``dependency_manifest_version`` and ``dependencies``.
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
    dependency_manifest_version: Annotated[int, BeforeValidator(_strict_int_for_manifest)] = Field(
        ...,
        ge=1,
        le=1,
        description="Version of the dependency manifest contract (must be exactly 1 for new submissions).",
    )
    dependencies: list[dict] = Field(
        ...,
        min_length=1,
        description="List of declared child executable dependencies.",
    )

    model_config = {"extra": "forbid"}


class ContractReferenceResponse(BaseModel):
    """Response body for GET /runtime/adapters/contract-reference (THR-107 seq184/seq339).

    Browser-consumer classification: the web Settings/Onboarding shared
    connection flow fetches this endpoint with the scoped adapter-purpose
    token after minting to obtain the literal server-derived
    ``required_executable_path`` for the prompt builder.  This is NOT a
    CLI-only endpoint — the browser is an intentional scoped-token consumer.
    """

    contract_version: int = Field(..., description="v1 contract version (always 1)")
    canonical_adapter_id: str = Field(
        ..., description="The stable server-derived adapter ID for this token's intended profile"
    )
    canonical_adapter_id_description: str = Field(
        ..., description="Provenance invariant: the wrapper MUST echo this exact ID"
    )
    adapter_input_schema: dict = Field(
        ..., description="JSON Schema for AdapterInput (generated from authoritative Pydantic model)"
    )
    adapter_output_schema: dict = Field(
        ..., description="JSON Schema for AdapterOutput (generated from authoritative Pydantic model)"
    )
    rules: dict = Field(..., description="Output constraints (max size, stdout/stderr contract, etc.)")
    submission: dict = Field(..., description="Submit endpoint metadata (method, path, content-type, body schema)")
    dependency_manifest: dict = Field(..., description="Dependency manifest schema and rules")
    token_metering: dict = Field(..., description="Token-metering expectations for adapters declaring the capability")
    reapproval_rule: str = Field(..., description="Rule: any change requires re-submission and founder re-approval")
    probe: dict = Field(..., description="Minimal self-test input/output fixture for the conformance probe")
    canonical_directory: str = Field(
        ..., description="Absolute canonical path to the daemon-managed adapters directory"
    )
    canonical_directory_description: str = Field(
        ..., description="Description of the canonical adapters directory"
    )
    required_executable_path: str = Field(
        ..., description="Exact absolute canonical path where the wrapper executable MUST be created"
    )
    required_executable_path_description: str = Field(
        ..., description="Description: the filename is the canonical adapter ID itself"
    )

    model_config = {"extra": "allow"}


class BindProfileRequest(BaseModel):
    """Request body for the seq141 profile-binding management endpoint.

    Two binding paths, server-enforced by durable adapter state:
    - When the adapter has an ``intended_profile_name``, the caller MUST
      supply that exact name — the server rejects any mismatch (422).
    - For an approved no-intended adapter whose server eligibility is
      ``recovery_ready``, Settings may supply a caller-selected valid
      profile name for advanced recovery. The server verifies D7B
      custom-adapter validation, on-disk integrity, and that the name
      does not collide with a built-in.
    The adapter must exist and be APPROVED; any other status is rejected.
    """

    profile_name: str = Field(
        ...,
        min_length=1,
        description=(
            "The executor profile name to bind. For adapters with an "
            "intended profile this must exactly match it. For approved "
            "no-intended (recovery_ready) adapters, provide a valid "
            "caller-selected profile name for explicit Bind recovery."
        ),
    )

    model_config = {"extra": "forbid"}


class AdapterRemoveRequest(BaseModel):
    """Request body for the THR-107 adapter removal management endpoint.

    Every material identity and binding fact MUST match the durable store
    entry exactly. This rejects stale snapshots, re-registered adapters,
    and wrong targets.
    """

    executable: str = Field(
        ...,
        min_length=1,
        description="Absolute path to the adapter executable (must match store exactly).",
    )
    executable_hash: str = Field(
        ...,
        min_length=1,
        description="SHA-256 hex digest of the executable (must match store exactly).",
    )
    version: str = Field(
        ...,
        min_length=1,
        description="Adapter version string (must match store exactly).",
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
        min_length=1,
        description="Workspace preparation adapter (must match store exactly).",
    )
    name: str = Field(
        ...,
        min_length=1,
        description="Adapter name (must match store exactly).",
    )
    intended_profile_name: str | None = Field(
        None,
        description="Intended profile name (must match store exactly, null allowed).",
    )
    dependency_manifest_version: int | None = Field(
        None,
        description=(
            "Dependency manifest version (must match store exactly). "
            "None for legacy entries without a manifest."
        ),
    )
    dependencies: list[dict] | None = Field(
        None,
        description=(
            "List of declared child executable dependencies (must match store "
            "exactly in order and content). None/empty for legacy entries."
        ),
    )

    model_config = {"extra": "forbid"}


class AdapterApproveRequest(BaseModel):
    """Request body for the D4 founder-gated approval route.

    Every field MUST match the durable store entry exactly.
    This binds the exact artifact snapshot the founder inspected.

    THR-107 seq244 fix-forward: includes dependency_manifest_version
    and dependencies so the founder attests to the immutable dependency
    manifest facts.
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
    dependency_manifest_version: int | None = Field(
        None,
        description=(
            "Dependency manifest version (must match store exactly). "
            "None for legacy entries without a manifest."
        ),
    )
    dependencies: list[dict] | None = Field(
        None,
        description=(
            "List of declared child executable dependencies (must match store "
            "exactly in order and content). None/empty for legacy entries."
        ),
    )

    model_config = {"extra": "forbid"}


class AdapterRejectRequest(BaseModel):
    """Request body for the THR-107 founder-gated pending-rejection route.

    Every field MUST match the durable store entry exactly.
    Rejects stale, re-registered, and hash-changed snapshots.
    Same material identity facts as approval — the caller attests to the
    exact PENDING artifact being rejected.

    THR-107 seq244 fix-forward: includes dependency_manifest_version
    and dependencies so the caller attests to the immutable dependency
    manifest facts.
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
    dependency_manifest_version: int | None = Field(
        None,
        description=(
            "Dependency manifest version (must match store exactly). "
            "None for legacy entries without a manifest."
        ),
    )
    dependencies: list[dict] | None = Field(
        None,
        description=(
            "List of declared child executable dependencies (must match store "
            "exactly in order and content). None/empty for legacy entries."
        ),
    )

    model_config = {"extra": "forbid"}


def _adapter_snapshot_mismatch(entry, body: AdapterRemoveRequest) -> str | None:
    """Exact-snapshot predicate: returns None when all 8 material
    identity/binding facts match, or a human-readable error detail.

    This is the SINGLE function used both pre-lock and under-lock so the
    two checks cannot drift.
    """
    facts = [
        ("executable", entry.executable, body.executable),
        ("executable_hash", entry.executable_hash, body.executable_hash),
        ("version", entry.version, body.version),
        ("capabilities", entry.capabilities, body.capabilities),
        ("contract_version", entry.contract_version, body.contract_version),
        ("workspace_adapter", entry.workspace_adapter, body.workspace_adapter),
        ("name", entry.name, body.name),
        ("intended_profile_name", entry.intended_profile_name, body.intended_profile_name),
        ("dependency_manifest_version", entry.dependency_manifest_version, body.dependency_manifest_version),
        ("dependencies", entry.dependencies, body.dependencies or []),
    ]
    for field, store_val, req_val in facts:
        if store_val != req_val:
            return (
                f"{field} mismatch for {entry.id!r}: "
                f"store has {store_val!r}, removal request has {req_val!r}"
            )
    return None


def _bound_profile_names(adapter_id: str) -> list[str]:
    """Return durable or live custom profiles bound to an adapter.

    Consults BOTH the durable runtime profile store AND the active
    in-memory ExecutorRegistry so that a profile loaded-only-into-memory
    (e.g. registered by a prior request that hasn't yet been written to
    disk, or a live-only test registration) blocks removal.
    """
    command_adapter_ref = f"custom-adapter:{adapter_id}"

    # Durable profiles (load_runtime_profiles).
    runtime_profiles = load_runtime_profiles()
    durable_bound = sorted(
        name
        for name, cfg in runtime_profiles.items()
        if cfg.get("command_adapter_id") == command_adapter_ref
    )

    # Live in-memory profiles (ExecutorRegistry).
    from runtime.orchestrator.executor_registry import get_registry
    registry = get_registry()
    live_bound = sorted(
        name
        for name in registry.list_profile_names()
        if getattr(registry.get_profile(name), "command_adapter_id", None) == command_adapter_ref
    )

    return sorted(set(durable_bound + live_bound))


def _check_no_profile_bound(adapter_id: str) -> None:
    """Reject with 422 if ANY durable OR live custom profile references
    command_adapter_id: custom-adapter:<adapter_id>.

    Consults BOTH the durable runtime profile store AND the active
    in-memory ExecutorRegistry so that a profile loaded-only-into-memory
    (e.g. registered by a prior request that hasn't yet been written to
    disk, or a live-only test registration) blocks removal.
    """
    all_bound = _bound_profile_names(adapter_id)

    if all_bound:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Cannot remove adapter {adapter_id!r}: the following "
                f"custom runtime profile(s) are bound to it: "
                f"{', '.join(all_bound)}. "
                f"Remove the profile(s) first via Settings \u2192 Executors \u2192 "
                f"Custom CLIs, then retry adapter removal."
            ),
        )


def _compute_eligibility(entry) -> str | None:
    """Compute server-authoritative recovery eligibility for an adapter.

    This is the single source of truth — the browser MUST NOT recompute
    hash/tamper eligibility.  The eligibility captures exactly the
    conditions already enforced by bind_adapter_profile.

    Returns None when the adapter is not approved (PENDING / unknown status).
    """
    if entry.status != "approved":
        return None

    # No intended profile → advanced recovery if adapter is hash/integrity-valid.
    if not entry.intended_profile_name:
        resolved = resolve_adapter(entry.id)
        if resolved is None:
            return "tampered"
        return "recovery_ready"

    profile_name = entry.intended_profile_name

    # Check on-disk integrity (hash + executable).  resolve_adapter re-validates
    # file existence, executable permission, and SHA-256 hash.
    resolved = resolve_adapter(entry.id)
    if resolved is None:
        return "tampered"

    # Check if the intended profile name is a built-in.
    BUILTIN_KINDS_NAMES = {"claude", "codex", "opencode", "pi"}
    if profile_name.lower() in BUILTIN_KINDS_NAMES:
        return "builtin_collision"

    # Check profile existence and binding — BOTH durable and live.
    from runtime.orchestrator.executor_registry import get_registry
    command_adapter_ref = f"custom-adapter:{entry.id}"

    # Durable profiles.
    runtime_profiles = load_runtime_profiles()
    durable_has_this = any(
        cfg.get("command_adapter_id") == command_adapter_ref
        for cfg in runtime_profiles.values()
    )
    durable_has_other = entry.intended_profile_name and any(
        name == entry.intended_profile_name and cfg.get("command_adapter_id") != command_adapter_ref
        for name, cfg in runtime_profiles.items()
    )

    # Live profiles — scan EVERY custom profile in the ExecutorRegistry,
    # not just the one at entry.intended_profile_name. A differently-named
    # custom profile that references custom-adapter:<id> must be detected.
    registry = get_registry()
    live_bound_to_this = any(
        getattr(registry.get_profile(name), "command_adapter_id", None) == command_adapter_ref
        for name in registry.list_profile_names()
    )
    live_has_other = any(
        name == profile_name
        and getattr(registry.get_profile(name), "command_adapter_id", None) != command_adapter_ref
        for name in registry.list_profile_names()
    )

    if durable_has_this or live_bound_to_this:
        return "already_bound"

    if live_has_other or durable_has_other:
        return "cross_profile"

    # No profile exists with this name — adapter is bindable.
    return "ready_to_bind"


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
        dependency_manifest_version=entry.dependency_manifest_version,
        dependencies=entry.dependencies,
        eligibility=_compute_eligibility(entry),
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
            dependency_manifest_version=body.dependency_manifest_version,
            dependencies=body.dependencies,
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
) -> dict:
    """Approve a pending custom adapter (THR-107 seq237: approve + optionally bind profile).

    This is a deliberate, explicit transition from durable PENDING to durable
    APPROVED. The request body carries the exact durable artifact snapshot the
    founder inspected — every material identity fact (executable, hash, version,
    capabilities, contract_version, workspace_adapter) is compared against the
    durable store entry.

    **THR-107 seq237**: When the adapter has a nonempty ``intended_profile_name``,
    this endpoint atomically approves the snapshot AND creates/binds that same
    named custom profile (``command_adapter_id: custom-adapter:<id>``) in one
    server transaction. Settings' single confirmation must refetch durable state
    and show Connected; it must make no client-side bind follow-up.

    Exact-idempotence: if the adapter is already APPROVED with identical stored
    immutable facts, the existing entry is returned unchanged. If the profile is
    already bound, the response includes ``profile_bound: already_bound``.

    Fails closed with 422 when:
      - Unknown adapter id
      - Entry is not PENDING (already-approved incompatible repeat, non-pending)
      - Any snapshot fact mismatches the store
      - Malformed/empty values
      - Profile binding fails (name collision, builtin conflict, cross-adapter,
        validation, registry, audit) — approval is rolled back to PENDING

    No-intended/reusable adapters (no ``intended_profile_name``) are approved
    without auto-binding — they retain explicit advanced Bind recovery.
    """
    # Determine whether to auto-bind: only when the adapter has an
    # intended_profile_name (submitted via the adapter-submission path).
    # No-intended adapters (master-bearer registration path) retain
    # explicit advanced Bind.
    auto_bind = False
    adapter_pre_check = get_adapter(adapter_id)
    if adapter_pre_check is not None and adapter_pre_check.intended_profile_name:
        auto_bind = True

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
            auto_bind_profile=auto_bind,
            dependency_manifest_version=body.dependency_manifest_version,
            dependencies=body.dependencies,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        )

    # Build response with profile binding info when available
    response = _entry_to_response(entry).model_dump()
    profile_bound = getattr(entry, "profile_bound", None)
    if profile_bound is not None:
        response["profile_bound"] = profile_bound
    return response


# ---------------------------------------------------------------------------
# THR-107 seq141: adapter submission + profile binding
# ---------------------------------------------------------------------------


def _has_traversal_spelling(raw_path: str) -> bool:
    """Return True if the raw path string contains any traversal component.

    Detects ``..`` directory components in the path before any normalization.
    This catches absolute traversal bypasses such as
    ``<daemon-home>/adapters/../adapters/<id>`` even when ``Path.resolve()``
    would collapse them to the canonical form.

    Only whole-segment ``..`` entries are flagged — a filename containing
    ``..`` as a substring (e.g. ``foo..bar``) is NOT flagged.
    """
    sep = os.sep
    parts = raw_path.split(sep)
    return ".." in parts


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
    direct_store = getattr(request.app.state.daemon, "direct_connect_authority_store", None)
    if direct_store is not None and direct_store.is_known_direct_token(raw_token):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="direct-connect authorities may only use /runtime/custom-cli/connect",
        )
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

    # 6. Validate body.executable matches the server-owned canonical path
    #    (THR-107 seq339/340).  The scoped adapter wrapper MUST be at the
    #    exact daemon-managed location — no foreign paths, no traversal
    #    spellings, no alternate filenames, no symlink escape.
    #    Compare the raw caller-provided string in its ORIGINAL LEXICAL
    #    FORM before any Path.resolve() — this catches absolute traversal
    #    bypasses such as <daemon-home>/adapters/../adapters/<id>.
    from runtime.orchestrator.custom_adapter_registry import (
        compute_canonical_adapter_path,
        generate_adapter_id as _gen_id,
    )
    scoped_adapter_id = _gen_id(f"{intended_profile}-adapter")
    try:
        _, required_path = compute_canonical_adapter_path(scoped_adapter_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "invalid_canonical_path",
                "message": str(exc),
            },
        )

    required_str = str(required_path)
    submitted_raw = body.executable

    # Pre-resolve checks on the RAW caller-provided string.
    if not submitted_raw or not os.path.isabs(submitted_raw):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "invalid_executable_path",
                "message": (
                    f"executable must be an absolute path, got {submitted_raw!r}. "
                    f"The scoped adapter wrapper must be created at the exact "
                    f"canonical path: {required_str}. Your token remains valid "
                    f"and retryable."
                ),
                "required_executable_path": required_str,
                "submitted_executable": submitted_raw,
            },
        )

    # Traversal-spelling check: the raw string must not contain any path
    # traversal components ("..").  This catches absolute normalization
    # bypasses before resolve() collapses them.
    if _has_traversal_spelling(submitted_raw):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "invalid_executable_path",
                "message": (
                    f"executable path contains traversal spelling: "
                    f"{submitted_raw!r}. The scoped adapter wrapper must be "
                    f"created at exactly the canonical path: {required_str}. "
                    f"No '..' components, symlinks, or alternate locations "
                    f"are accepted. Your token remains valid and retryable."
                ),
                "required_executable_path": required_str,
                "submitted_executable": submitted_raw,
            },
        )

    # Exact lexical equality: the raw caller-provided string must equal
    # the server-derived required path EXACTLY.  Do NOT resolve() before
    # this comparison — the canonical expected path is safely server-derived
    # and may be canonicalized; the caller's string must match it verbatim.
    if submitted_raw != required_str:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "invalid_executable_path",
                "message": (
                    f"The scoped adapter wrapper executable must be at the "
                    f"server-owned canonical path: {required_str}. "
                    f"Received: {submitted_raw!r}. "
                    f"Create your adapter wrapper at exactly the required "
                    f"executable path returned by GET /runtime/adapters/"
                    f"contract-reference — no other location, symlink, or "
                    f"alternate filename is accepted. Your token remains "
                    f"valid and retryable."
                ),
                "required_executable_path": required_str,
                "submitted_executable": submitted_raw,
            },
        )

    # 7. Reserve the token so concurrent submissions get single-winner
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
            dependency_manifest_version=body.dependency_manifest_version,
            dependencies=body.dependencies,
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
    response_model=ContractReferenceResponse,
)
def get_contract_reference(request: Request) -> ContractReferenceResponse:
    """Return the canonical v1 AdapterInput/AdapterOutput contract reference.

    Scoped-token browser-consumer endpoint (THR-107 seq184/seq339). The
    web Settings/Onboarding shared connection flow fetches this with the
    scoped adapter-purpose token after minting to obtain the literal
    server-derived ``required_executable_path`` for the prompt builder.
    The candidate CLI also fetches this reference FIRST to learn the exact
    AdapterInput and AdapterOutput JSON Schemas before implementing a wrapper.

    Auth scope:
    - Must be loopback (127.0.0.1, ::1, localhost) — enforced by
      require_registration_token dependency.
    - Must carry a valid, unexpired, unconsumed ``hrreg_`` token.
    - Token purpose MUST be ``'adapter'`` — non-adapter-purpose tokens are
      rejected with 422. Master bearer is rejected at the dependency level.
    - The browser Settings/Onboarding shared flow is an INTENTIONAL consumer
      of this scoped-token endpoint (fetches after minting to obtain the
      literal ``required_executable_path`` for the prompt builder).
    - Reading the contract-reference does NOT consume, reserve, or modify
      the token — the browser/CLI may fetch this multiple times and still
      proceed to conformance check-ins and submission.

    Response shape (stable v1):
    - ``contract_version``: int (1)
    - ``adapter_input_schema``: JSON Schema for AdapterInput (generated from
      the authoritative Pydantic model at
      runtime/orchestrator/adapter_contract.py)
    - ``adapter_output_schema``: JSON Schema for AdapterOutput (same source)
    - ``rules``: invocation and output constraints, including the wrapper-owned
      headless launch requirement (max size, stdout/stderr contract,
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

    from runtime.orchestrator.adapter_contract import AdapterInput, AdapterOutput, DependencyManifest, DependencyRecord
    from runtime.orchestrator.custom_adapter_registry import (
        build_probe_input,
        compute_canonical_adapter_path,
        generate_adapter_id,
    )

    # Derive the canonical server-authoritative adapter ID from the token's
    # intended_profile_name — this is the same stable ID generation used at
    # submission time.  The adapter wrapper MUST echo this exact ID in
    # adapter_metadata.adapter (provenance invariant).
    canonical_adapter_id = generate_adapter_id(
        f"{token_record.intended_profile_name}-adapter"
    )

    # Compute the daemon-managed canonical adapter path (THR-107 seq339/340).
    # Ensures the adapters directory exists with 0o700; rejects symlinks.
    # The candidate CLI must create its wrapper executable at exactly
    # required_executable_path.
    canonical_directory, required_executable_path = compute_canonical_adapter_path(
        canonical_adapter_id
    )

    # Build a minimal self-test fixture from the real probe builder.
    probe_input = build_probe_input(canonical_adapter_id)
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
                "adapter": canonical_adapter_id,
                "adapter_version": "1.0.0",
                "contract_version": 1,
            },
            "child_session_id": None,
            "raw_forensics_ref": None,
        },
    }

    return ContractReferenceResponse(
        contract_version=1,
        canonical_adapter_id=canonical_adapter_id,
        canonical_adapter_id_description=(
            f"The stable server-derived adapter ID for this token's "
            f"intended profile. The adapter wrapper's "
            f"adapter_metadata.adapter MUST exactly equal this value "
            f"({canonical_adapter_id!r}) — never a display name, provider "
            f"string, or arbitrary identity. A mismatch fails the conformance "
            f"probe at registration AND blocks every launch at runtime."
        ),
        adapter_input_schema=AdapterInput.model_json_schema(),
        adapter_output_schema=AdapterOutput.model_json_schema(),
        rules={
            "input": {
                "source": "stdin",
                "description": (
                    "Read exactly one v1 AdapterInput JSON object from stdin. "
                    "The daemon pipes the AdapterInput payload to the adapter's "
                    "stdin at launch time; the adapter must read it fully before "
                    "invoking the candidate CLI."
                ),
            },
            "headless_launch": {
                "description": (
                    "The wrapper MUST choose and apply its underlying CLI's own "
                    "non-interactive, sufficiently permissive launch posture for "
                    "this unattended daemon session. executor_context.permission_mode "
                    "remains a legacy nullable, provider-specific compatibility field; "
                    "for custom-adapter invocations, CustomAdapterExecutor supplies "
                    "null for this existing frozen nullable, provider-specific "
                    "compatibility field. Custom wrappers MUST NOT rely on it "
                    "for their CLI-specific "
                    "headless posture or on daemon translation of policy or provider-"
                    "specific allow-rule strings. The wrapper must preserve the daemon-"
                    "provided callback environment (including PATH) so the invoked "
                    "agent can perform ordinary workspace actions and invoke the "
                    "happyranch callback required by its session contract. This is "
                    "a wrapper implementation and approval responsibility: founder "
                    "approval must include evidence of a successful end-to-end "
                    "unattended session that invokes the required callback. This adds "
                    "no new daemon-supplied or daemon-translated permission policy or "
                    "field to AdapterInput."
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
        submission={
            "method": "POST",
            "path": "/api/v1/runtime/adapters/submit",
            "content_type": "application/json",
            "description": (
                "Submit the adapter wrapper executable for the intended profile. "
                "Requires the same adapter-purpose hrreg_ token and a completed "
                "conformance challenge. Submission creates ONLY the exact PENDING "
                "adapter; founder approval is a separate Settings-only step. "
                "For adapters with an intended profile, approval atomically creates "
                "and connects the profile (seq237); explicit Bind is only needed for "
                "advanced recovery of approved no-intended adapters. "
                "New submissions MUST include dependency_manifest_version: 1 and a "
                "non-empty dependencies list declaring every child executable the "
                "adapter wrapper invokes."
            ),
            "body_schema": {
                "description": "Adapter submission request body",
                "required_fields": [
                    "executable", "version", "capabilities",
                    "dependency_manifest_version", "dependencies",
                ],
                "fields": {
                    "executable": "Absolute path to the adapter wrapper executable",
                    "version": "Adapter version string",
                    "capabilities": "Declared capabilities list",
                    "workspace_adapter": "Workspace preparation adapter (default: pi)",
                    "dependency_manifest_version": (
                        "Integer. Must be 1 for new submissions. "
                        "Separate versioning space from contract_version."
                    ),
                    "dependencies": (
                        "Non-empty list of dependency records. Each record has: "
                        "executable (absolute path, must be regular + executable "
                        "on disk, never a bare command name or PATH-resolved) and "
                        "sha256 (64-char hex matching the on-disk file). "
                        "Duplicates are rejected. "
                        "An adapter declaring this manifest must invoke "
                        "child executables by their exact declared absolute "
                        "paths. HappyRanch never selects an agentic CLI via "
                        "ambient PATH — adapter wrapper and every declared "
                        "child CLI dependency are daemon-approved exact "
                        "absolute paths, hash-pinned/revalidated; no bare "
                        "executor command/name fallback. The wrapper "
                        "inherits HappyRanch's normalized environment/PATH; "
                        "normal utilities and required callbacks such as "
                        "happyranch report-completion remain reachable. "
                        "A dependency change requires re-submission and founder "
                        "re-approval."
                    ),
                },
            },
        },
        dependency_manifest={
            "description": (
                "The dependency manifest is an independently versioned extension "
                "that declares every child executable the adapter wrapper invokes. "
                "This is required for new submissions and provides durable trust "
                "boundaries: every declared dependency is hash-verified at "
                "registration and re-verified before EVERY launch attempt."
            ),
            "dependency_manifest_version": 1,
            "dependency_manifest_schema": DependencyManifest.model_json_schema(),
            "dependency_record_schema": DependencyRecord.model_json_schema(),
            "rules": {
                "new_submission_required": True,
                "non_empty_required": True,
                "duplicates_rejected": True,
                "absolute_path_only": True,
                "no_path_fallback": True,
                "hash_at_registration": True,
                "hash_before_every_launch": True,
                "change_requires_resubmit": True,
                "legacy_entries_preserved": True,
            },
            "example": {
                "dependency_manifest_version": 1,
                "dependencies": [
                    {
                        "executable": "/usr/local/bin/some-child-cli",
                        "sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
                    }
                ],
            },
        },
        token_metering={
            "description": (
                "Adapters that declare the token_metering capability MUST produce "
                "a valid, non-null token_usage in their AdapterOutput at conformance "
                "time. The token_usage object must not be null, and at least one of "
                "input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens, "
                "or reasoning_tokens must be a non-null integer. Zero is a legitimate "
                "count. An absent, null, or all-accounting-fields-null token_usage "
                "fails the conformance probe. Adapters without token_metering capability "
                "may omit token_usage and retain current behavior."
            ),
            "probe_expectation": (
                "The conformance probe runs the adapter with a sample AdapterInput. "
                "A token_metering adapter must return a structurally valid token_usage. "
                "The runtime does not fabricate, default, or infer accounting — the "
                "adapter must truthfully report what its child CLI consumed."
            ),
        },
        reapproval_rule=(
            "Any change to the adapter executable, its hash, declared dependencies, "
            "or capabilities requires re-submission and founder re-approval. The "
            "approved snapshot is immutable — a tampered or stale dependency blocks "
            "launch with an actionable error."
        ),
        probe={
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
        canonical_directory=str(canonical_directory),
        canonical_directory_description=(
            f"The absolute canonical path to the daemon-managed adapters "
            f"directory ({canonical_directory}). Created with restrictive "
            f"user-only mode (0700) if newly created. Never a symlink."
        ),
        required_executable_path=str(required_executable_path),
        required_executable_path_description=(
            f"The exact absolute canonical path where the adapter wrapper "
            f"executable MUST be created: {required_executable_path}. "
            f"The filename is the canonical adapter ID itself (lowercase "
            f"alnum/hyphen only). The candidate CLI must create a regular "
            f"executable file at exactly this path — no other location, "
            f"symlink, or alternate filename is accepted by the scoped "
            f"submission route. The adapters directory is already prepared "
            f"and ready for the file creation."
        ),
    )


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

    Standard daemon-bearer management endpoint. Two paths:
    - **Normal / intended-profile**: the request ``profile_name`` must
      exactly match the adapter's ``intended_profile_name``. This path is
      reachable during advanced recovery when an intended-profile adapter
      was approved without auto-bind (legacy state).
    - **Recovery**: for an approved adapter with no ``intended_profile_name``
      whose server eligibility is ``recovery_ready``, the caller supplies a
      valid profile name for explicit Bind recovery. The server validates
      D7B custom-adapter requirements, checks for built-in name collisions,
      and verifies on-disk integrity.
    Both paths bind via ``command_adapter_id: custom-adapter:<id>``.

    Gating checks (exact order):
    1. Adapter exists (404 if unknown)
    2. Adapter is APPROVED (422 if PENDING or unknown status)
    3. When intended_profile_name is set, the request profile_name must
       match exactly; when None (recovery_ready), the caller selects a
       valid profile name (422 on mismatch or invalid name)
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

    # 3. intended_profile_name must match when present.
    #     When intended is None (master-bearer registration), the caller
    #     explicitly provides the profile name for advanced Bind recovery.
    if entry.intended_profile_name is not None and entry.intended_profile_name != profile_name:
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
        if re_read_entry.intended_profile_name is not None and re_read_entry.intended_profile_name != profile_name:
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


# ---------------------------------------------------------------------------
# THR-107: adapter removal
# ---------------------------------------------------------------------------


def _audit_adapter_remove(
    *,
    adapter_id: str,
    adapter_name: str,
    removed_snapshot: dict,
    actor: str = "founder",
) -> None:
    """Write a runtime-level adapter-removal audit row.

    Opens (creating if needed) a dedicated runtime-audit.db under
    daemon_home(), then writes a single audit_log row.  Each call opens a
    fresh ``Database`` handle and closes it.

    Row shape:
      task_id = "adapter:<adapter_id>"
      action  = "adapter_removed"
      payload = {adapter_id, name, executable, executable_hash, version,
                 capabilities, contract_version, workspace_adapter,
                 intended_profile_name, status,
                 dependency_manifest_version, dependencies}
    """
    from runtime.infrastructure.database import Database
    from runtime.runtime import daemon_home

    audit_db_path = daemon_home() / "runtime-audit.db"
    db = Database(audit_db_path)
    try:
        db.insert_audit_log_uncommitted(
            task_id=f"adapter:{adapter_id}",
            agent=actor,
            action="adapter_removed",
            payload={
                "adapter_id": adapter_id,
                "name": removed_snapshot.get("name", ""),
                "executable": removed_snapshot.get("executable", ""),
                "executable_hash": removed_snapshot.get("executable_hash", ""),
                "version": removed_snapshot.get("version", ""),
                "capabilities": removed_snapshot.get("capabilities", []),
                "contract_version": removed_snapshot.get("contract_version"),
                "workspace_adapter": removed_snapshot.get("workspace_adapter", ""),
                "intended_profile_name": removed_snapshot.get("intended_profile_name"),
                "status": removed_snapshot.get("status", ""),
                "dependency_manifest_version": removed_snapshot.get("dependency_manifest_version"),
                "dependencies": removed_snapshot.get("dependencies", []),
            },
        )
        db.commit()
    finally:
        db.close()


class AdapterRemovalAuditError(RuntimeError):
    """Raised when a removed adapter has been restored after audit failure."""


def _remove_adapter_locked_with_audit(adapter_id: str, entry) -> None:
    """Remove ``entry`` and audit it, restoring its exact snapshot on failure.

    The caller must hold the reentrant adapter-store lock.  Keeping the
    durable removal, audit, and compensation in one primitive prevents a
    successful-looking deletion with no audit trail.
    """
    from runtime.orchestrator.adapter_store import (
        AdapterEntry,
        _save_adapter_locked,
        remove_adapter,
    )

    removed_snapshot = entry.to_dict()
    if not remove_adapter(adapter_id):
        return
    try:
        _audit_adapter_remove(
            adapter_id=adapter_id,
            adapter_name=entry.name,
            removed_snapshot=removed_snapshot,
        )
    except Exception as exc:
        _save_adapter_locked(AdapterEntry.from_dict(removed_snapshot))
        raise AdapterRemovalAuditError(
            f"Adapter {adapter_id!r} was restored after audit logging failed"
        ) from exc


def remove_unbound_direct_connect_adapter(adapter_id: str):
    """Remove an unbound direct-connect adapter under the store lock.

    A concurrent direct-connect projection uses this same lock while it
    creates and binds its adapter/profile pair.  Re-read every predicate at
    the lock boundary so a fresh bind cannot be mistaken for an orphan.
    """
    acquire_store_lock()
    try:
        entry = get_adapter(adapter_id)
        if entry is None or entry.registered_by != "direct-connect":
            return None
        if _bound_profile_names(adapter_id):
            return None
        _remove_adapter_locked_with_audit(adapter_id, entry)
        return entry
    finally:
        release_store_lock()


@router.delete(
    "/runtime/adapters/{adapter_id}",
    dependencies=[require_token()],
)
def remove_adapter_entry(
    adapter_id: str,
    body: AdapterRemoveRequest,
) -> dict:
    """Remove an APPROVED custom adapter (THR-107 founder-gated destructive action).

    Master-bearer-authenticated management endpoint. Removes an APPROVED custom
    adapter from the durable store. The caller MUST supply an exact durable
    snapshot (all material identity and binding facts) — the server rejects
    stale, re-registered, and wrong-target snapshots.

    Gating checks (exact order):
    1. Adapter exists (404 if unknown)
    2. Adapter is APPROVED (422 if PENDING or unknown status)
    3. Every snapshot fact matches the stored adapter (422 on mismatch)
    4. No custom runtime profile references command_adapter_id
       custom-adapter:<adapter_id> (422 if bound)

    Under the reentrant adapter-store lock, the adapter is durably removed
    and an audit entry is written. If auditing fails after durable removal,
    the exact adapter entry is restored under the lock and a failure is
    returned — a successful removal is always auditable.

    Lock ordering (documented, compatible with bind/registration):
      adapter_store_lock → durable removal → audit write
    """
    from runtime.orchestrator.runtime_executor_store import load_runtime_profiles

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
                f"not APPROVED. Only APPROVED adapters may be removed."
            ),
        )

    # 3. Exact-snapshot match against ALL material identity/binding facts.
    mis = _adapter_snapshot_mismatch(entry, body)
    if mis is not None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=mis,
        )

    # 4. Reject if ANY durable or live custom runtime profile references
    #    command_adapter_id: custom-adapter:<adapter_id>
    _check_no_profile_bound(adapter_id)

    # Snapshot the entry for the successful response.
    removed_snapshot = entry.to_dict()

    # Durable removal under the reentrant adapter-store lock.
    # Serializes against concurrent register/approve/remove operations.
    acquire_store_lock()
    try:
        # Re-read and re-validate at the lock boundary using the exact same
        # predicate so pre-lock and locked checks CANNOT drift.
        re_read_entry = get_adapter(adapter_id)
        if re_read_entry is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Adapter {adapter_id!r} disappeared before removal.",
            )
        if re_read_entry.status != "approved":
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"Adapter {adapter_id!r} changed to status={re_read_entry.status!r} "
                    f"before removal."
                ),
            )
        mis = _adapter_snapshot_mismatch(re_read_entry, body)
        if mis is not None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"Adapter {adapter_id!r} facts changed before removal. "
                    f"{mis} Refresh the snapshot and retry."
                ),
            )

        # Re-check profile binding under the lock (both durable + live).
        _check_no_profile_bound(adapter_id)

        try:
            _remove_adapter_locked_with_audit(adapter_id, re_read_entry)
        except AdapterRemovalAuditError:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=(
                    "Adapter removal succeeded but audit logging failed; "
                    "the adapter has been restored. Retry the operation."
                ),
            )

    except HTTPException:
        raise
    except BaseException:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Adapter removal failed.",
        )
    finally:
        release_store_lock()

    return {
        "id": adapter_id,
        "removed": True,
        "name": removed_snapshot.get("name", ""),
    }


# ---------------------------------------------------------------------------
# THR-107 seq220: pending adapter rejection
# ---------------------------------------------------------------------------


def _audit_adapter_reject(
    *,
    adapter_id: str,
    adapter_name: str,
    rejected_snapshot: dict,
    actor: str = "founder",
) -> None:
    """Write a runtime-level adapter-rejection audit row.

    Opens (creating if needed) a dedicated runtime-audit.db under
    daemon_home(), then writes a single audit_log row.  Each call opens a
    fresh ``Database`` handle and closes it.

    Row shape:
      task_id = "adapter:<adapter_id>"
      action  = "adapter_rejected"
      payload = {adapter_id, name, executable, executable_hash, version,
                 capabilities, contract_version, workspace_adapter,
                 intended_profile_name, status,
                 dependency_manifest_version, dependencies}
    """
    from runtime.infrastructure.database import Database
    from runtime.runtime import daemon_home

    audit_db_path = daemon_home() / "runtime-audit.db"
    db = Database(audit_db_path)
    try:
        db.insert_audit_log_uncommitted(
            task_id=f"adapter:{adapter_id}",
            agent=actor,
            action="adapter_rejected",
            payload={
                "adapter_id": adapter_id,
                "name": rejected_snapshot.get("name", ""),
                "executable": rejected_snapshot.get("executable", ""),
                "executable_hash": rejected_snapshot.get("executable_hash", ""),
                "version": rejected_snapshot.get("version", ""),
                "capabilities": rejected_snapshot.get("capabilities", []),
                "contract_version": rejected_snapshot.get("contract_version"),
                "workspace_adapter": rejected_snapshot.get("workspace_adapter", ""),
                "intended_profile_name": rejected_snapshot.get("intended_profile_name"),
                "status": rejected_snapshot.get("status", ""),
                "dependency_manifest_version": rejected_snapshot.get("dependency_manifest_version"),
                "dependencies": rejected_snapshot.get("dependencies", []),
            },
        )
        db.commit()
    finally:
        db.close()


@router.post(
    "/runtime/adapters/{adapter_id}/reject",
    dependencies=[require_token()],
)
def reject_pending_adapter(
    adapter_id: str,
    body: AdapterRejectRequest,
) -> dict:
    """Reject/remove a PENDING custom adapter (THR-107 seq220 founder-gated).

    Master-bearer-authenticated management endpoint. Rejects a PENDING custom
    adapter by atomically removing it from the durable store. The caller MUST
    supply an exact durable snapshot (all 6 material identity facts) — the
    server rejects stale, re-registered, and hash-changed snapshots.

    This endpoint does NOT introduce a persisted rejected status or any
    SQLite/schema change. Rejection is durable removal with audit.

    Gating checks (exact order):
    1. Adapter exists (404 if unknown)
    2. Adapter is PENDING (422 if APPROVED or unknown status)
    3. Every snapshot fact matches the stored adapter (422 on mismatch)

    Under the reentrant adapter-store lock, the adapter is durably removed
    and an audit entry is written. If auditing fails after durable removal,
    the exact adapter entry is restored under the lock and a failure is
    returned — a successful rejection is always auditable.

    Lock ordering (documented, compatible with register/approve/bind):
      adapter_store_lock → durable removal → audit write
    """
    # 1. Adapter exists
    entry = get_adapter(adapter_id)
    if entry is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Adapter {adapter_id!r} not found.",
        )

    # 2. Must be PENDING
    if entry.status != "pending":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Adapter {adapter_id!r} is status={entry.status!r}, "
                f"not PENDING. Only PENDING adapters may be rejected."
            ),
        )

    # 3. Exact-snapshot match against all material identity facts.
    #    Includes dependency manifest facts (THR-107 seq244 fix-forward).
    _req_deps = body.dependencies or []
    _entry_deps = entry.dependencies or []
    facts = [
        ("executable", entry.executable, body.executable),
        ("executable_hash", entry.executable_hash, body.executable_hash),
        ("version", entry.version, body.version),
        ("capabilities", entry.capabilities, body.capabilities),
        ("contract_version", entry.contract_version, body.contract_version),
        ("workspace_adapter", entry.workspace_adapter, body.workspace_adapter),
        ("dependency_manifest_version", entry.dependency_manifest_version, body.dependency_manifest_version),
        ("dependencies", _entry_deps, _req_deps),
    ]
    for field, store_val, req_val in facts:
        if store_val != req_val:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"{field} mismatch for {adapter_id!r}: "
                    f"store has {store_val!r}, reject request has {req_val!r}"
                ),
            )

    # Snapshot the entry for audit and potential rollback.
    rejected_snapshot = entry.to_dict()

    # Durable removal under the reentrant adapter-store lock.
    # Serializes against concurrent register/approve/remove operations.
    acquire_store_lock()
    try:
        # Re-read and re-validate at the lock boundary — same predicate,
        # so pre-lock and locked checks CANNOT drift.
        re_read_entry = get_adapter(adapter_id)
        if re_read_entry is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Adapter {adapter_id!r} disappeared before rejection.",
            )
        if re_read_entry.status != "pending":
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"Adapter {adapter_id!r} changed to status={re_read_entry.status!r} "
                    f"before rejection."
                ),
            )
        for field, store_val, req_val in [
            ("executable", re_read_entry.executable, body.executable),
            ("executable_hash", re_read_entry.executable_hash, body.executable_hash),
            ("version", re_read_entry.version, body.version),
            ("capabilities", re_read_entry.capabilities, body.capabilities),
            ("contract_version", re_read_entry.contract_version, body.contract_version),
            ("workspace_adapter", re_read_entry.workspace_adapter, body.workspace_adapter),
            ("dependency_manifest_version", re_read_entry.dependency_manifest_version, body.dependency_manifest_version),
            ("dependencies", re_read_entry.dependencies or [], body.dependencies or []),
        ]:
            if store_val != req_val:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=(
                        f"Adapter {adapter_id!r} facts changed before rejection. "
                        f"{field} mismatch: store has {store_val!r}, "
                        f"reject request has {req_val!r}. "
                        f"Refresh the snapshot and retry."
                    ),
                )

        # Durable removal via the atomic store helper.
        from runtime.orchestrator.adapter_store import remove_adapter as _store_remove
        _store_remove(adapter_id)

        # Audit the successful rejection.
        # If auditing fails, restore the exact adapter entry and return failure.
        try:
            _audit_adapter_reject(
                adapter_id=adapter_id,
                adapter_name=re_read_entry.name,
                rejected_snapshot=rejected_snapshot,
            )
        except Exception:
            # Restore the adapter under the lock.
            from runtime.orchestrator.adapter_store import _save_adapter_locked
            from runtime.orchestrator.adapter_store import AdapterEntry as AdapterEntryModel
            restored = AdapterEntryModel.from_dict(rejected_snapshot)
            _save_adapter_locked(restored)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=(
                    "Adapter rejection succeeded but audit logging failed; "
                    "the adapter has been restored. Retry the operation."
                ),
            )

    except HTTPException:
        raise
    except BaseException:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Adapter rejection failed.",
        )
    finally:
        release_store_lock()

    return {
        "id": adapter_id,
        "rejected": True,
        "name": rejected_snapshot.get("name", ""),
    }
