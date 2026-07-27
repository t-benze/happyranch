"""Custom adapter registration routes (THR-107 D3).

POST /api/v1/runtime/adapters/register
    Register a custom adapter executable. Validates absolute path, computes
    SHA-256, runs conformance probe, persists as PENDING only.

D3 ONLY — no approval/activation (D4), no profile binding/launch (D7), no
permission/sandbox expansion (D5), no SQLite changes.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from runtime.orchestrator.custom_adapter_registry import (
    list_adapters,
    register_custom_adapter,
    resolve_adapter,
)

router = APIRouter()


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

    Mirrors ``AdapterEntry`` fields. D3: status is always "pending".
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
    """Get a single registered custom adapter by id.

    Returns 404 if not found.
    """
    entry = resolve_adapter(adapter_id)
    if entry is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Adapter {adapter_id!r} not found",
        )
    return _entry_to_response(entry)
