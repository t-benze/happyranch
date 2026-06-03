"""Bearer-token auth dependency for the daemon's FastAPI routes."""
from __future__ import annotations

from fastapi import Depends, Header, HTTPException, status

from runtime.daemon import paths


def _check_token(authorization: str | None = Header(default=None)) -> None:
    expected = paths.read_token()
    if expected is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="daemon token file missing",
        )
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="missing bearer token")
    if authorization.removeprefix("Bearer ").strip() != expected:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="bad token")


def require_token() -> Depends:
    return Depends(_check_token)


def _check_optional_token(
    authorization: str | None = Header(default=None),
) -> bool:
    """Return True iff a valid bearer token is present. False otherwise.

    Used by dual-auth routes (bearer OR session-binding). Unlike
    ``_check_token``, this never raises — it lets the caller decide whether
    a missing/invalid bearer is acceptable when other proof of identity
    (e.g. session_id) is available.
    """
    expected = paths.read_token()
    if expected is None:
        # Daemon mis-configured. Fail closed for safety.
        return False
    if not authorization or not authorization.startswith("Bearer "):
        return False
    return authorization.removeprefix("Bearer ").strip() == expected


def optional_bearer() -> Depends:
    return Depends(_check_optional_token)
