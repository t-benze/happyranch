"""THR-107 Slice 1/3: direct-connect projection commit + status routes.

``POST /runtime/custom-cli/{operation_id}/commit``
    Drives a ``received_nonlaunchable`` direct-connect receipt to a durably
    COMMITTED, launch-eligible custom-adapter profile. Deliberately a
    SEPARATE route from ``/runtime/custom-cli/connect`` — that route is
    pinned to spawn zero subprocesses; this route's projection coordinator
    runs a bounded conformance probe against the wrapper, which does spawn
    one. Master-bearer-authed (unlike ``/connect``, which is authed by the
    now-already-consumed registration token) — this is a founder/local-SPA
    follow-up action, not something the candidate CLI calls. The daemon-owned
    periodic projection sweep also invokes the same coordinator directly,
    bypassing HTTP and auth so a receipt completes even if no browser calls
    this route. This route remains available and idempotent for browsers that
    do call it.

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
    candidate CLI's ``/connect`` call has landed and then observe the
    daemon-owned projection result by polling.

``POST /runtime/custom-cli/{operation_id}/forget``
    Master-bearer-authed cleanup route for a terminal FAILED custom-CLI
    operation. It is the ONLY route that deletes rows from the direct-connect
    authority store. It refuses a missing, planned, or committed projection,
    then removes its failed authority records and the derived wrapper file.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from runtime.daemon.auth import require_token
from runtime.daemon.direct_connect_projection import project
from runtime.daemon.direct_connect_retry import retry_validate
from runtime.daemon.direct_connect_store import DirectConnectRetryInProgress, canonical_wrapper_destination
from runtime.orchestrator.executor_registry import get_registry
from runtime.orchestrator.runtime_executor_store import load_runtime_profiles

router = APIRouter()


class DirectConnectStatusResponse(BaseModel):
    """Redacted browser status for one intended direct-connect profile."""

    model_config = ConfigDict(extra="forbid")

    wrapper_destination: str
    operation_id: str | None
    profile_state: str | None
    reason: str | None
    attempt_count: int = Field(ge=0, description="Received candidate attempts for this authority.")
    retry_eligible: bool = Field(description="Whether one changed-artifact candidate retry remains allowed.")
    expires_at: float | None = Field(description="Original registration-token expiry, as a Unix timestamp.")
    historical_projection_state: str | None = None
    historical_projection_reason: str | None = None
    retry_state: str | None = None


@router.post("/runtime/custom-cli/{operation_id}/commit", dependencies=[require_token()])
async def commit(operation_id: str, request: Request) -> dict[str, str | None]:
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


@router.post("/runtime/custom-cli/{operation_id}/retry", dependencies=[require_token()])
async def retry(operation_id: str, request: Request) -> dict[str, str]:
    """Revalidate only an immutable terminal-failed receipt snapshot."""
    daemon = request.app.state.daemon
    authority_store = daemon.direct_connect_authority_store
    if authority_store is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="direct authority unavailable"
        )
    projection = authority_store.get_projection(operation_id)
    if projection is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="direct operation not found")
    if projection.state != "failed":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"refused: projection state is '{projection.state}', not 'failed'",
        )
    outcome = retry_validate(authority_store, operation_id)
    result = {"operation_id": operation_id, "profile_state": outcome.state}
    if outcome.state == "committed":
        result["profile_name"] = outcome.profile_name
    else:
        result["reason"] = outcome.reason
    return result


@router.get(
    "/runtime/custom-cli/status",
    dependencies=[require_token()],
    response_model=DirectConnectStatusResponse,
)
async def status_for_profile(intended_profile_name: str, request: Request) -> JSONResponse:
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
            successful_retry = authority_store.get_successful_retry(operation_id)
            if projection.state == "failed" and successful_retry is not None:
                # The retry record is the fact of a live revalidation/bind.
                # Keep the immutable projection's state and reason available
                # separately; they are never rewritten to claim it committed.
                profile_state = "committed"
                reason = None
            if profile_state == "committed":
                # A projection is historical evidence of how a profile was
                # created, not proof that its profile still exists. The
                # durable runtime store is authoritative; the registry check
                # also ensures an immediately removed profile is not reported
                # as connected before a daemon restart.
                stored_profiles = load_runtime_profiles()
                live_profile = get_registry().get_profile(intended_profile_name)
                if intended_profile_name not in stored_profiles or live_profile is None:
                    operation_id = None
                    profile_state = None
                    reason = None
    result: dict[str, object] = {
        "wrapper_destination": str(wrapper_destination),
        "operation_id": operation_id,
        "profile_state": profile_state,
        "reason": reason,
    }
    # Parent attempt facts are intentionally nonsecret: no token, fingerprint,
    # artifact paths/hashes, or historical manifest material crosses this API.
    result.update(authority_store.status_for_profile(intended_profile_name))
    if operation_id is not None:
        projection = authority_store.get_projection(operation_id)
        successful_retry = authority_store.get_successful_retry(operation_id)
        if projection is not None and projection.state == "failed" and successful_retry is not None:
            result["historical_projection_state"] = "failed"
            result["historical_projection_reason"] = projection.reason
            result["retry_state"] = "succeeded"
    # Validate the actual response shape while preserving the established
    # omission of absent historical-retry fields from the JSON payload.
    DirectConnectStatusResponse.model_validate(result)
    return JSONResponse(result)


@router.post("/runtime/custom-cli/{operation_id}/forget", dependencies=[require_token()])
async def forget(operation_id: str, request: Request) -> dict[str, str]:
    daemon = request.app.state.daemon
    authority_store = daemon.direct_connect_authority_store
    if authority_store is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="direct authority unavailable"
        )
    projection = authority_store.get_projection(operation_id)
    if projection is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="direct operation not found")
    if authority_store.get_successful_retry(operation_id) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="refused: retry validation established a live connection",
        )
    if projection.state != "failed":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"refused: projection state is '{projection.state}', not 'failed' "
                "— this operation is still in flight or is a live connection"
            ),
        )
    try:
        intended_profile_name = authority_store.forget_operation(operation_id)
    except DirectConnectRetryInProgress:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="refused: retry validation is running",
        ) from None
    if intended_profile_name is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="direct operation not found")
    # Failed-operation cleanup is now an audit acknowledgement only.  The
    # append-only parent/attempt series may have a newer receipt using the
    # same canonical wrapper, so deleting either history or that wrapper is
    # unsafe.  This keeps the former endpoint compatible without turning an
    # old failed attempt into a cleanup bypass.
    return {"operation_id": operation_id, "status": "history_retained"}
