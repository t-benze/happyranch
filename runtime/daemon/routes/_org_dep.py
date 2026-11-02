"""Shared FastAPI dependency: resolve a path slug to its OrgState."""
from __future__ import annotations

from typing import Annotated
from collections.abc import Iterator
from contextlib import ExitStack

from fastapi import Depends, HTTPException, Request, status

from runtime.daemon.org_state import OrgState, _OrgBusyError
from runtime.daemon.state import DaemonState


def resolve_org(slug: str, request: Request) -> Iterator[OrgState]:
    state: DaemonState = request.app.state.daemon
    if state.is_idle:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "no_active_runtime"},
        )
    with ExitStack() as stack:
        try:
            orgs = stack.enter_context(state.capture_orgs((slug,)))
        except _OrgBusyError as exc:
            raise HTTPException(status_code=409, detail={"code": exc.code}) from exc
        org = orgs.get(slug)
        if org is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "unknown_org", "slug": slug,
                        "available": sorted(state.orgs_snapshot())},
            )
        yield org


OrgDep = Annotated[OrgState, Depends(resolve_org)]
