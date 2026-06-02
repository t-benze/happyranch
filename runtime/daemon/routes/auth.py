"""Localhost-only bootstrap endpoint that hands the SPA the bearer token.

The web UI runs entirely on the founder's local machine. Browsers cannot read
``~/.happyranch/daemon.token`` themselves, so the SPA fetches it once on load via this
endpoint and stashes it in ``sessionStorage``. The endpoint refuses any peer
that isn't loopback so the token never escapes the host.

No reverse-proxy assumption: the daemon is the terminal hop, so
``request.client.host`` is the real peer. ``X-Forwarded-For`` is ignored.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status

from runtime.daemon import paths

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
