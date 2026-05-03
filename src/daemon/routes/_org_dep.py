"""Shared FastAPI dependency: resolve a path slug to its OrgState."""
from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, Request, status

from src.daemon.org_state import OrgState
from src.daemon.state import DaemonState


def resolve_org(slug: str, request: Request) -> OrgState:
    state: DaemonState = request.app.state.daemon
    if state.is_idle:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "no_active_runtime"},
        )
    try:
        return state.get_org(slug)
    except KeyError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "unknown_org",
                "slug": slug,
                "available": sorted(state.orgs.keys()),
            },
        )


OrgDep = Annotated[OrgState, Depends(resolve_org)]
