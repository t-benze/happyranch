"""THR-107 Slice 1: direct-connect projection commit route.

``POST /runtime/custom-cli/{operation_id}/commit``
    Drives a ``received_nonlaunchable`` direct-connect receipt to a durably
    COMMITTED, launch-eligible custom-adapter profile. Deliberately a
    SEPARATE route from ``/runtime/custom-cli/connect`` — that route is
    pinned to spawn zero subprocesses; this route's projection coordinator
    runs a bounded conformance probe against the wrapper, which does spawn
    one. Master-bearer-authed (unlike ``/connect``, which is authed by the
    now-already-consumed registration token) — this is a founder/local-SPA
    follow-up action, not something the candidate CLI calls.

    Idempotent: retries after a COMMITTED or FAILED outcome return the same
    result without redoing any work (see ``direct_connect_projection.project``).
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status

from runtime.daemon.auth import require_token
from runtime.daemon.direct_connect_projection import project

router = APIRouter()


@router.post("/runtime/custom-cli/{operation_id}/commit", dependencies=[require_token()])
async def commit(operation_id: str, request: Request) -> dict[str, str]:
    daemon = request.app.state.daemon
    authority_store = daemon.direct_connect_authority_store
    if authority_store is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="direct authority unavailable"
        )
    try:
        outcome = project(authority_store, operation_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from None

    result = {"operation_id": operation_id, "profile_state": outcome.state}
    if outcome.state == "committed":
        result["profile_name"] = outcome.profile_name
    else:
        result["reason"] = outcome.reason
    return result
