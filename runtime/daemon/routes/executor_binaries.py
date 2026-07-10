"""Machine-local executor binary-path registry routes (THR-085).

Bearer-authenticated routes for reading, setting, and validating per-kind
executor binary paths. These are the WRITE surface of the machine-local
registry — a human operator (or setup tool) uses them to tell the daemon
where each executor CLI binary lives on THIS host.

Distinct from ``routes/executors.py`` (THR-052 profile registry — loopback
+ scoped-token for CLI self-registration). This router uses standard bearer
auth like other /api/v1 routes.
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from runtime.daemon.auth import require_token
from runtime.orchestrator.executor_binary_registry import (
    get_binary,
    is_binary_valid,
    load_registry,
    set_binary,
    validate_binary,
)

router = APIRouter(dependencies=[require_token()])

# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class BinaryRegistryEntry(BaseModel):
    """A single executor kind's entry in the registry."""
    kind: str = Field(..., description="Executor kind name, e.g. 'claude'")
    path: str | None = Field(
        None, description="Absolute path to the binary, or None if not registered"
    )
    valid: bool = Field(
        False,
        description="True when the stored path exists and is executable",
    )


class BinaryRegistryList(BaseModel):
    """Full machine-local registry listing."""
    entries: list[BinaryRegistryEntry]


class RegisterBinaryRequest(BaseModel):
    """Request to register or update a binary path for an executor kind."""
    kind: str = Field(..., min_length=1, description="Executor kind, e.g. 'claude'")
    path: str = Field(..., min_length=1, description="Absolute path to the binary")


class RegisterBinaryResponse(BaseModel):
    """Response after successfully registering a binary path."""
    kind: str
    path: str
    valid: bool


class ValidateBinaryRequest(BaseModel):
    """Request to validate a binary path without storing it."""
    path: str = Field(..., min_length=1, description="Absolute path to check")


class ValidateBinaryResponse(BaseModel):
    """Response after path validation."""
    path: str
    valid: bool
    error: str | None = Field(None, description="Error message if invalid")


# ---------------------------------------------------------------------------
# GET /api/v1/executor-binaries — list the registry
# ---------------------------------------------------------------------------


@router.get("/executor-binaries", response_model=BinaryRegistryList)
def list_binaries() -> BinaryRegistryList:
    """List all executor kinds with their stored binary paths and current validity.

    Returns every kind that has a stored path. Kinds never registered are not
    listed (the client can infer these from the absence of an entry + the known
    set of built-in kinds: claude, codex, opencode, pi).
    """
    registry = load_registry()
    entries: list[BinaryRegistryEntry] = []
    for kind in sorted(registry.keys()):
        path = registry[kind]
        entries.append(
            BinaryRegistryEntry(
                kind=kind,
                path=path,
                valid=is_binary_valid(path),
            )
        )
    return BinaryRegistryList(entries=entries)


# ---------------------------------------------------------------------------
# POST /api/v1/executor-binaries/register — set a binary path
# ---------------------------------------------------------------------------


@router.post(
    "/executor-binaries/register",
    response_model=RegisterBinaryResponse,
    status_code=status.HTTP_200_OK,
)
def register_binary(body: RegisterBinaryRequest) -> RegisterBinaryResponse:
    """Register or update the absolute binary path for an executor kind.

    Validates BEFORE storing:
    - Path must be absolute
    - Path must point to an existing file
    - File must be executable

    On success, the path is written to the machine-local registry and takes
    effect immediately for the next spawn.
    """
    try:
        resolved = validate_binary(body.path)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        )

    set_binary(body.kind, resolved)
    return RegisterBinaryResponse(
        kind=body.kind,
        path=resolved,
        valid=True,
    )


# ---------------------------------------------------------------------------
# POST /api/v1/executor-binaries/validate — check a path without storing
# ---------------------------------------------------------------------------


@router.post(
    "/executor-binaries/validate",
    response_model=ValidateBinaryResponse,
)
def validate_path(body: ValidateBinaryRequest) -> ValidateBinaryResponse:
    """Validate that a path is absolute, exists, and is executable.

    Does NOT store anything — pure validation for pre-commit UI checks.
    """
    try:
        resolved = validate_binary(body.path)
        return ValidateBinaryResponse(path=resolved, valid=True, error=None)
    except ValueError as exc:
        return ValidateBinaryResponse(
            path=body.path, valid=False, error=str(exc)
        )
