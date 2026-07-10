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
    detect_candidates,
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


# ---------------------------------------------------------------------------
# GET /api/v1/executor-binaries/detect — auto-detect candidate binaries
# ---------------------------------------------------------------------------


class CandidateBinary(BaseModel):
    """A single auto-detected binary candidate for one executor kind.

    For kinds with one or more detected binaries, one entry is emitted per
    valid path. For kinds with zero detected binaries, a single entry is
    emitted with ``path=""`` so the web UI can show "nothing detected —
    enter a path".
    """
    kind: str = Field(..., description="Executor kind, e.g. 'claude'")
    path: str = Field(
        ...,
        description="Absolute path to the detected binary, or '' if none found for this kind",
    )
    valid: bool = Field(
        True,
        description="Always True — only valid paths are returned, and empty-string entries signal a clean scan with no hits",
    )


class DetectCandidatesResponse(BaseModel):
    """Response for the auto-detect endpoint."""
    candidates: list[CandidateBinary] = Field(
        default_factory=list,
        description="One entry per detected candidate (plus one empty-path entry per kind with zero hits — always covers all 4 known kinds)",
    )


@router.get(
    "/executor-binaries/detect",
    response_model=DetectCandidatesResponse,
)
def detect_binaries() -> DetectCandidatesResponse:
    """Auto-detect executor binary candidates from standard install locations.

    Scans common directories (Homebrew, /usr/local/bin, ~/.local/bin, npm global
    prefix) and the system PATH for each known executor kind (claude, codex,
    opencode, pi). Returns only valid (exists + executable) paths.

    Every known kind has at least one entry in the response — kinds with zero
    detected candidates get a single entry with ``path=""`` so the web UI can
    show "nothing detected — enter a path".

    Pure read-only — does NOT read or mutate the stored registry.
    Detection is independent of registration.
    """
    found = detect_candidates()
    flat: list[CandidateBinary] = []
    for kind in ("claude", "codex", "opencode", "pi"):
        paths = found.get(kind, [])
        if paths:
            for path in paths:
                flat.append(CandidateBinary(kind=kind, path=path, valid=True))
        else:
            flat.append(CandidateBinary(kind=kind, path="", valid=True))
    return DetectCandidatesResponse(candidates=flat)
