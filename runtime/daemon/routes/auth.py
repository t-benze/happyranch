"""Localhost-only auth endpoints: SPA bootstrap + registration token mint.

``GET /auth/bootstrap``
    Loopback-only. Returns the master bearer token so the local SPA can
    authenticate to privileged routes. The daemon is the terminal hop;
    ``X-Forwarded-For`` is ignored.

``POST /auth/registration-token`` (THR-052 PR-1)
    Loopback-only AND master-bearer-authed. Mints a scoped, single-use,
    ~30-minute-TTL ``hrreg_`` token that authorizes ONLY
    ``POST /executors/register`` (PR-2). Used by the Settings → Executors
    panel to generate a copy-paste prompt for a candidate CLI.
"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field, model_validator

from runtime.daemon import paths
from runtime.daemon.auth import require_token
from runtime.daemon.direct_connect_store import FIRST_PARTY_WORKSPACE_ADAPTER_IDS

router = APIRouter()

_LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost"}
WorkspaceAdapterId = Literal["claude", "codex", "opencode", "pi"]


@router.get("/auth/bootstrap")
def bootstrap(request: Request) -> dict:
    peer = request.client.host if request.client else None
    if peer not in _LOCAL_HOSTS:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "not_localhost", "peer": peer},
        )
    token = paths.read_token()
    if token is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="daemon token file missing",
        )
    return {"token": token}


# ── Registration token mint (THR-052 PR-1) ─────────────────────────────


class RegistrationTokenMintRequest(BaseModel):
    org: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1, description="Executor profile name")


class RegistrationTokenMintResponse(BaseModel):
    token: str
    expires_at: float


class RuntimeRegistrationTokenMintRequest(BaseModel):
    """Runtime-level mint: no org — the profile is machine-global."""
    name: str = Field(..., min_length=1, description="Executor profile name")
    purpose: str = Field(
        'profile',
        pattern=r'^(profile|binary|adapter)$',
        description="'profile' for executor profile registration, 'binary' for binary-path registration, 'adapter' for custom-adapter submission"
    )
    intended_profile_name: str | None = Field(
        None,
        min_length=1,
        description="For 'adapter' purpose: the profile name this adapter is bound to"
    )
    workspace_adapter_id: WorkspaceAdapterId | None = Field(
        None,
        description=(
            "Optional Slice-1A direct-authority workspace adapter. Accepted only "
            "for adapter-purpose mints and only as claude, codex, opencode, or pi."
        ),
    )

    @model_validator(mode="after")
    def validate_direct_authority_request(self) -> "RuntimeRegistrationTokenMintRequest":
        if self.purpose == "adapter" and (
            self.intended_profile_name is None or not self.intended_profile_name.strip()
        ):
            raise ValueError("intended_profile_name is required for adapter-purpose tokens")
        if self.workspace_adapter_id is None:
            return self
        if self.purpose != "adapter":
            raise ValueError("workspace_adapter_id is only valid for adapter-purpose tokens")
        if self.workspace_adapter_id not in FIRST_PARTY_WORKSPACE_ADAPTER_IDS:
            raise ValueError("workspace_adapter_id must be an exact first-party adapter id")
        return self


@router.post("/auth/registration-token")
def mint_registration_token(
    request: Request,
    body: RegistrationTokenMintRequest,
    _token_valid: None = require_token(),
) -> RegistrationTokenMintResponse:
    """Mint a scoped, single-use registration token.

    Loopback-only AND master-bearer-authed. Only the founder's local SPA
    (which already holds the master via ``/auth/bootstrap``) can mint.
    """
    peer = request.client.host if request.client else None
    if peer not in _LOCAL_HOSTS:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "not_localhost", "peer": peer},
        )

    store = request.app.state.daemon.registration_token_store
    token, expires_at = store.mint(body.org, body.name)
    return RegistrationTokenMintResponse(token=token, expires_at=expires_at)


# ── Runtime-level registration token mint (THR-088) ────────────────────


@router.post("/auth/registration-token/runtime")
def mint_runtime_registration_token(
    request: Request,
    body: RuntimeRegistrationTokenMintRequest,
    _token_valid: None = require_token(),
) -> RegistrationTokenMintResponse:
    """Mint a runtime-level (org-agnostic) registration token.

    Loopback-only AND master-bearer-authed. Same auth gating as the
    org-scoped mint, but omits org — the resulting token is valid for
    the runtime-level registration routes.
    """
    peer = request.client.host if request.client else None
    if peer not in _LOCAL_HOSTS:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "not_localhost", "peer": peer},
        )

    store = request.app.state.daemon.registration_token_store
    direct_store = request.app.state.daemon.direct_connect_authority_store

    def persist_direct_authority(token: str, record: object) -> None:
        if body.workspace_adapter_id is None:
            return
        if direct_store is None:
            raise RuntimeError("direct authority store is unavailable")
        # RegistrationTokenRecord is deliberately received as an opaque mint
        # result so this route cannot persist the raw authorization anywhere.
        issued_at = getattr(record, "issued_at")
        expires_at = getattr(record, "expires_at")
        direct_store.mint_authority(
            token_plaintext=token,
            name=body.name,
            intended_profile_name=body.intended_profile_name or "",
            workspace_adapter_id=body.workspace_adapter_id,
            issued_at=issued_at,
            expires_at=expires_at,
        )

    try:
        token, expires_at = store.mint_runtime(
            body.name,
            purpose=body.purpose,
            intended_profile_name=body.intended_profile_name,
            on_mint=persist_direct_authority if body.workspace_adapter_id is not None else None,
        )
    except Exception:
        # Do not expose a credential or a persistence implementation detail.
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="unable to mint registration token",
        ) from None
    return RegistrationTokenMintResponse(token=token, expires_at=expires_at)
