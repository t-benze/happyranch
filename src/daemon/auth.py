"""Bearer-token auth dependency for the daemon's FastAPI routes."""
from __future__ import annotations

from fastapi import Depends, Header, HTTPException, status

from src.daemon import paths


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
