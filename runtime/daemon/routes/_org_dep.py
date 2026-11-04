"""Shared FastAPI dependency: resolve a path slug to its OrgState."""
from __future__ import annotations

from typing import Annotated
from collections.abc import AsyncIterator, Iterator
from contextlib import AbstractContextManager, ExitStack
from copy import copy

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

# These responses own any later DB reads explicitly. Endpoint work is still
# protected, but a bus wait or a slow send cannot retain the request's reader.
OrgStreamDep = Annotated[OrgState, Depends(resolve_org, scope="function")]


def _stream_org_reader(request: Request, org: OrgState) -> AbstractContextManager[dict[str, OrgState]]:
    state: DaemonState = request.app.state.daemon
    return state.capture_orgs((org.slug,), original=org)


def _stream_org_events(request: Request, org: OrgState, topic: str) -> AsyncIterator[dict]:
    # Reuse EventBus.subscribe unchanged: registration, replay, terminal events
    # and subscriber cleanup still use the ORIGINAL bus's lock and registry.
    # Only this subscription's synchronous history loader gets a reader. A
    # shallow view shares those loop-owned objects without mutating the live
    # bus or holding a reader across __anext__'s potentially unbounded wait.
    bus = copy(org.event_bus)
    load_history = bus._history_loader

    def history_loader(key: str) -> list[dict]:
        with _stream_org_reader(request, org):
            return load_history(key)

    bus._history_loader = history_loader
    return bus.subscribe(topic)
