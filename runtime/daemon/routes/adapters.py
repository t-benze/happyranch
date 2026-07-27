"""Custom adapter registration routes (THR-107 D3 + D4).

POST /api/v1/runtime/adapters/register
    Register a custom adapter executable. Validates absolute path, computes
    SHA-256, runs conformance probe, persists as PENDING only.

POST /api/v1/runtime/adapters/{adapter_id}/approve  (D4)
    Founder-gated explicit approval gate. Binds exact durable artifact
    snapshot, transitions PENDING → APPROVED, persists approved_at +
    approved_by provenance.

GET /api/v1/runtime/adapters
    List all registered custom adapters (D4: includes approved_at/by).

GET /api/v1/runtime/adapters/{adapter_id}
    Get a single adapter by id (D4: includes approved_at/by).

D4 scope: approval transition only. No D5 permission/sandbox expansion,
D7 profile binding/launch, D12 ExecutorResult/protocol changes, SQLite
changes, or auth/bearer-flow changes.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from runtime.daemon.auth import require_token
from runtime.orchestrator.custom_adapter_registry import (
    approve_adapter,
    get_adapter,
    list_adapters,
    register_custom_adapter,
    resolve_adapter,
)

router = APIRouter(dependencies=[require_token()])


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
    """Map an ``AdapterEntry`` to the response model (D4: includes approved_at/by)."""
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
    )


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
