"""THR-107 Slice 1/3: direct-connect projection commit + status routes.

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

``GET /runtime/custom-cli/status``
    Master-bearer-authed, browser-facing polling route (Settings/onboarding
    Slice 3). Keyed ONLY by ``intended_profile_name`` — the founder's
    browser already knows this (it's the form input it minted with) and
    this route never accepts, logs, or looks up anything by token
    plaintext. Returns the deterministic wrapper destination (computable
    with no DB read) plus the latest operation's id and projection state,
    if any — the browser uses this to (a) show the candidate CLI where to
    write its wrapper in the connect prompt, and (b) detect when the
    candidate CLI's ``/connect`` call has landed so it knows when to call
    ``/commit``.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status

from runtime.daemon.auth import require_token
from runtime.daemon.direct_connect_projection import project
from runtime.daemon.direct_connect_store import canonical_wrapper_destination

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


@router.get("/runtime/custom-cli/status", dependencies=[require_token()])
async def status_for_profile(intended_profile_name: str, request: Request) -> dict[str, object]:
    daemon = request.app.state.daemon
    authority_store = daemon.direct_connect_authority_store
    if authority_store is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="direct authority unavailable"
        )
    wrapper_destination = canonical_wrapper_destination(
        getattr(authority_store, "_runtime_root", None), intended_profile_name
    )
    operation_id = authority_store.get_latest_operation_for_profile(intended_profile_name)
    profile_state = None
    reason = None
    if operation_id is not None:
        projection = authority_store.get_projection(operation_id)
        if projection is not None:
            profile_state = projection.state
            reason = projection.reason
    return {
        "wrapper_destination": str(wrapper_destination),
        "operation_id": operation_id,
        "profile_state": profile_state,
        "reason": reason,
    }
