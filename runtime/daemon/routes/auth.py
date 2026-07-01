"""Localhost-only auth endpoints: SPA bootstrap + registration token mint.

``GET /auth/bootstrap``
    Loopback-only. Returns the master bearer token so the local SPA can
    authenticate to privileged routes. The daemon is the terminal hop;
    ``X-Forwarded-For`` is ignored.

``POST /auth/registration-token`` (THR-052 PR-1)
    Loopback-only AND master-bearer-authed. Mints a scoped, single-use,
    ~10-minute-TTL ``hrreg_`` token that authorizes ONLY
    ``POST /executors/register`` (PR-2). Used by the Settings → Executors
    panel to generate a copy-paste prompt for a candidate CLI.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from runtime.daemon import paths
from runtime.daemon.auth import require_token

router = APIRouter()

_LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost"}


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
