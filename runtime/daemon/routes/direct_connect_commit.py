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
    operation. It deletes derived state for one terminal failed operation:
    only safe derived artifacts/projections and any retry-attempt row are
    removed. It retains the immutable parent authority, accepted candidate
    record, canonical identity history, receipt, operation row, and event
    trail. Its ``wrapper_status`` reports only ``already_absent``,
    ``preserved_changed``, or ``preserved_unsafe``. It refuses missing,
    planned, or committed projections, and refuses when retry validation is
    running or has succeeded, without deletion.
"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from runtime.daemon.auth import require_token
from runtime.daemon.direct_connect_projection import project
from runtime.daemon.direct_connect_retry import retry_validate
from runtime.daemon.direct_connect_store import DirectConnectRetryInProgress, canonical_wrapper_destination
from runtime.orchestrator.executor_registry import get_registry
from runtime.orchestrator.runtime_executor_store import load_runtime_profiles

router = APIRouter()


class DirectConnectStatusResponse(BaseModel):
    """Redacted, ledger-derived status for the browser-facing status route.

    Never includes token plaintext, fingerprint, identity digest/blob,
    candidate/artifact history, hashes, probe output, or error output.
    ``wrapper_destination`` remains the only required prompt value.
    """

    model_config = {"extra": "forbid"}

    wrapper_destination: str = Field(
        ..., description="Daemon-owned canonical path the wrapper must be created at."
    )
    operation_id: str | None = Field(
        None, description="Latest accepted candidate operation id, if any."
    )
    profile_state: Literal["planned", "committed", "failed"] | None = Field(
        None, description="Legacy compatibility mapping from the canonical candidate state."
    )
    reason: str | None = Field(None, description="Terminal or failure reason, category only.")
    state: Literal[
        "waiting", "active", "connected", "failed_retryable", "failed_nonretryable", "expired", "exhausted"
    ] | None = Field(None, description="Canonical candidate-ledger state.")
    retry_eligible: bool = Field(
        False, description="Server-authoritative retry eligibility for corrected-artifact retry."
    )
    historical_projection_state: Literal["failed"] | None = Field(
        None, description="Present when a retry validation established a live connection."
    )
    historical_projection_reason: str | None = Field(
        None, description="Immutable historical projection failure reason."
    )
    retry_state: Literal["succeeded"] | None = Field(
        None, description="Present when a retry validation succeeded."
    )


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


@router.get("/runtime/custom-cli/status", dependencies=[require_token()], response_model=DirectConnectStatusResponse)
async def status_for_profile(intended_profile_name: str, request: Request) -> DirectConnectStatusResponse:
    daemon = request.app.state.daemon
    authority_store = daemon.direct_connect_authority_store
    if authority_store is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="direct authority unavailable"
        )
    wrapper_destination = canonical_wrapper_destination(
        getattr(authority_store, "_runtime_root", None), intended_profile_name
    )
    # The canonical view is derived from the accepted-candidate ledger, not
    # from any deletable operation row, so forgetting a failed candidate never
    # loses the latest lifecycle state.
    candidate_status = authority_store.latest_candidate_status_for_profile(intended_profile_name)

    result: dict[str, object] = {
        "wrapper_destination": str(wrapper_destination),
        "operation_id": None,
        "profile_state": None,
        "reason": None,
        "state": None,
        "retry_eligible": False,
    }
    if candidate_status is None:
        return DirectConnectStatusResponse.model_validate(result)

    result["operation_id"] = candidate_status.operation_id
    result["reason"] = candidate_status.reason
    result["state"] = candidate_status.state
    result["retry_eligible"] = candidate_status.retry_eligible

    # Map the stable candidate-ledger state to the legacy profile_state field
    # for backward-compatible consumers.
    successful_retry = authority_store.get_successful_retry(candidate_status.operation_id)
    if successful_retry is not None:
        # A successful retry validation established a live connection while
        # preserving the immutable failed projection as historical evidence.
        result["profile_state"] = "committed"
        historical_projection = authority_store.get_projection(candidate_status.operation_id)
        result["historical_projection_state"] = "failed"
        result["historical_projection_reason"] = (
            historical_projection.reason if historical_projection is not None else candidate_status.reason
        )
        result["retry_state"] = "succeeded"
    elif candidate_status.state == "connected":
        stored_profiles = load_runtime_profiles()
        live_profile = get_registry().get_profile(intended_profile_name)
        if intended_profile_name in stored_profiles and live_profile is not None:
            result["profile_state"] = "committed"
        else:
            result["operation_id"] = None
            result["profile_state"] = None
            result["reason"] = None
            result["state"] = None
            result["retry_eligible"] = False
    elif candidate_status.state == "active":
        result["profile_state"] = "planned"
    elif candidate_status.state in {"failed_retryable", "failed_nonretryable", "expired", "exhausted"}:
        result["profile_state"] = "failed"

    return DirectConnectStatusResponse.model_validate(result)


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
        outcome = authority_store.forget_operation(operation_id)
    except DirectConnectRetryInProgress:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="refused: retry validation is running",
        ) from None
    if outcome is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="direct operation not found")
    return {
        "operation_id": operation_id,
        "status": "forgotten",
        "wrapper_status": outcome.wrapper_status,
    }
