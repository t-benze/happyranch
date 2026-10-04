"""Founder-only cutover requests and read-only status/downgrade decisions."""
from __future__ import annotations

import json
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, StrictInt, ValidationError

from runtime.daemon.auth import _require_human
from runtime.daemon.routes._org_dep import OrgDep
from runtime.workflows.cutover import WorkflowCutoverError, WorkflowCutoverStore

router = APIRouter(dependencies=[Depends(_require_human)])
_IDENTITY_FIELDS = frozenset({
    "actor", "actor_id", "principal", "principal_id", "principal_kind", "proof_kind", "publisher",
    "org", "org_slug", "owner", "owner_id", "recovery_owner", "proof", "proof_bytes",
    "proof_digest", "verified", "verified_by", "verification", "state", "receipt", "receipt_id",
    "namespace", "agent", "agent_name", "task", "task_id", "session_id",
})


class CutoverRequestBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    action: Literal["enable", "disable"]
    operation_key: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")
    expected_generation: StrictInt = Field(gt=0)


class CutoverEvent(BaseModel):
    id: str
    event_seq: int
    state_before: str | None
    state_after: str
    operation_key: str | None
    event_digest: str
    created_at: str


class CutoverBlocker(BaseModel):
    code: str
    record_id: str | None = None
    state: str | None = None
    owner: str
    required_action: str
    deferred_to: str | None = None


class CutoverVerification(BaseModel):
    event_id: str
    policy: Literal["workflow-cutover-verifier@1"]


class CutoverProjection(BaseModel):
    org_slug: str
    schema_version: int
    state: Literal["installed_legacy_only", "enable_requested", "compatibility_verified", "enabled",
                   "disable_requested", "draining", "drained"]
    recovery_owner: Literal["workflow_cutover_reconciler"]
    generation: int
    operation_key: str | None
    disable_reason: Literal["founder_disable_requested"] | None
    updated_at: str
    events: list[CutoverEvent]
    allowed_actions: list[Literal["enable", "disable"]]
    blockers: list[CutoverBlocker]
    reconciliation_required: bool
    verification: CutoverVerification | None


class CutoverRequestResponse(CutoverProjection):
    request_event_id: str
    request_generation: int
    request_action: Literal["enable", "disable"]
    replayed: bool


class CutoverDowngradePreflight(BaseModel):
    eligible: bool
    blockers: list[CutoverBlocker]
    projection: CutoverProjection


def _raise_error(exc: WorkflowCutoverError) -> None:
    code = exc.code
    status = 422 if code == "cutover_invalid_request" else (
        409 if code in {"cutover_operation_conflict", "cutover_generation_stale",
                       "cutover_transition_not_allowed"} else 500
    )
    raise HTTPException(status_code=status, detail={"code": code}) from exc


@router.get(
    "/workflows/cutover", response_model=CutoverProjection,
    responses={403: {"description": "Founder authority required"},
               404: {"description": "Unknown org"}, 500: {"description": "Safe storage/operation category"}},
)
async def show_cutover(org: OrgDep) -> dict:
    async with org.db_lock:
        try:
            return WorkflowCutoverStore(org.db, org_slug=org.slug).get()
        except WorkflowCutoverError as exc:
            _raise_error(exc)


@router.post(
    "/workflows/cutover/requests", response_model=CutoverRequestResponse,
    responses={403: {"description": "Founder authority/body identity refusal"},
               404: {"description": "Unknown org"}, 409: {"description": "Operation/CAS/edge conflict"},
               422: {"description": "Strict invalid request"}, 500: {"description": "Safe storage/operation category"}},
    openapi_extra={"requestBody": {"required": True, "content": {
        "application/json": {"schema": CutoverRequestBody.model_json_schema()},
    }}},
)
async def request_cutover(request: Request, org: OrgDep) -> dict:
    try:
        body_raw = json.loads(await request.body())
    except (ValueError, UnicodeError) as exc:
        raise HTTPException(status_code=422, detail={"code": "cutover_invalid_request"}) from exc
    # Authentication/actual org resolution are existing dependencies. Reject
    # claimed provenance before strict field decoding, never ignore it.
    if "session_id" in request.query_params or (
        isinstance(body_raw, dict) and set(body_raw) & _IDENTITY_FIELDS
    ):
        raise HTTPException(status_code=403, detail={"code": "body_identity_rejected"})
    try:
        body = CutoverRequestBody.model_validate(body_raw)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail={"code": "cutover_invalid_request"}) from exc
    async with org.db_lock:
        try:
            return WorkflowCutoverStore(org.db, org_slug=org.slug).request(**body.model_dump())
        except WorkflowCutoverError as exc:
            _raise_error(exc)
        except Exception as exc:
            # Category-only, including an ambiguous postcommit response. Retry
            # the same request body/key; never infer rollback from transport.
            raise HTTPException(status_code=500, detail={"code": "cutover_operation_failed"}) from exc


@router.get(
    "/workflows/cutover/downgrade-preflight", response_model=CutoverDowngradePreflight,
    responses={403: {"description": "Founder authority required"},
               404: {"description": "Unknown org"}, 500: {"description": "Safe storage/operation category"}},
)
async def downgrade_preflight(org: OrgDep) -> dict:
    async with org.db_lock:
        try:
            return WorkflowCutoverStore(org.db, org_slug=org.slug).downgrade_preflight()
        except WorkflowCutoverError as exc:
            _raise_error(exc)
