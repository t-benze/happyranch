"""Founder service: pure preview, gated apply, saved show and explicit recovery."""
from __future__ import annotations

import json
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from runtime.daemon.auth import _require_human, require_token
from runtime.infrastructure.team_move_store import (
    FounderAdmission, MAX_BYTES, _now, _unique_object,
)
from runtime.orchestrator.team_move_operation import TeamMoveOperation
from runtime.orchestrator.team_move_public import ApplyMove, MoveSelection, RecoverMove

router = APIRouter(dependencies=[require_token(), Depends(_require_human)], responses={
    401: {"description": "Existing bearer refused"}, 403: {"description": "Founder authority required"},
    404: {"description": "Unknown org or operation"}, 409: {"description": "Refused, stale, held or repair required"},
    422: {"description": "Closed request/query validation refused"},
    503: {"description": "Apply disabled or required native capability unavailable"}})
PRIVATE = {"Cache-Control": "no-store", "Pragma": "no-cache"}


def body_schema(model):
    """Inline the finite closed schema for the served OpenAPI request body."""
    schema = model.model_json_schema()
    definitions = schema.pop("$defs", {})
    def expand(value):
        if isinstance(value, dict):
            if "$ref" in value:
                return expand(definitions[value["$ref"].rsplit("/", 1)[-1]])
            return {k: expand(v) for k, v in value.items()}
        return [expand(v) for v in value] if isinstance(value, list) else value
    return {"requestBody": {"required": True, "content": {"application/json": {"schema": expand(schema)}}}}


def no_query(request):
    # No task/session/actor/invocation binding, force, or other query exception.
    if request.query_params:
        raise HTTPException(422, detail={"code": "team_move_query_not_supported", "committed": False})


async def command_body(request, model):
    no_query(request)
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw) + len(chunk) > MAX_BYTES:
            raise HTTPException(422, detail={"code": "team_move_request_bound", "committed": False})
        raw.extend(chunk)
    try:
        def invalid_constant(value):
            raise ValueError("nonfinite")
        # Reject duplicate members/BOM/invalid UTF8 before Pydantic decoding.
        json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object, parse_constant=invalid_constant)
        return model.model_validate_json(bytes(raw))
    except (ValueError, UnicodeError, ValidationError):
        raise HTTPException(422, detail={"code": "team_move_invalid_request", "committed": False}) from None


def service(request):
    no_query(request)
    return request.app.state.daemon.team_move_service


def invoke(owner, slug, callback, *, operation_key=None, operation_id=None):
    try:
        result = callback()
    except Exception as exc:
        code = TeamMoveOperation._diagnostic(exc).code
        observation = None
        if operation_key is not None or operation_id is not None:
            try:
                observation = owner.show(slug, operation_key=operation_key, operation_id=operation_id)
            except Exception:
                pass  # Unknown is not uncommitted or successful absence.
        status = 404 if code in {"unknown_org", "team_move_unknown_operation"} else (
            503 if code in {"team_move_apply_disabled", "team_move_native_profiles_unavailable", "team_move_schema_absent"} else 409)
        detail = {"code": code, "committed": observation["committed"] if observation else None,
            "recovery_required": observation["recovery_required"] if observation else True,
            "operation": observation, "next_action": "read_capability_and_show_same_key"}
        if observation is None and code in {"team_move_apply_disabled", "team_move_agents_busy", "team_move_stale_preview"}:
            detail.update(committed=False, recovery_required=False,
                next_action="resolve_named_blockers_or_release_dependency_then_preview")
        if hasattr(exc, "blockers"):
            detail["blockers"] = exc.blockers
        if code == "former_name":
            detail.update(current_name=exc.current_name, canonical_id=exc.canonical_id)
        return JSONResponse({"detail": detail}, status_code=status, headers=PRIVATE)
    status = 200
    headers = dict(PRIVATE)
    if "phase" in result:
        if result["running"]:
            status = 202
        elif result["phase"] == "complete" and not result["held"] and result["receipt"] is not None and not result["service_error"]:
            status = 200
        else:
            status = 409
        headers["Location"] = f"/api/v1/orgs/{slug}/team-moves/{result['operation_id']}"
    return JSONResponse(result, status_code=status, headers=headers)


@router.get("/team-moves/capability")
def capability(slug: str, request: Request):
    owner = service(request)
    return invoke(owner, slug, lambda: owner.capability(slug))


@router.post("/team-moves/preview", openapi_extra=body_schema(MoveSelection))
async def preview(slug: str, request: Request):
    body = await command_body(request, MoveSelection)
    owner = service(request)
    return invoke(owner, slug, lambda: owner.preview(slug, body))


@router.post("/team-moves", openapi_extra=body_schema(ApplyMove), responses={202: {"description": "Accepted/in-flight; not success"}})
async def apply(slug: str, request: Request):
    body = await command_body(request, ApplyMove)
    owner = service(request)
    # Both inherited dependencies have completed. Nothing in the request can
    # choose this actor or turn session/invocation proof into Founder authority.
    def submit():
        owner.require_runtime(slug)
        admission = FounderAdmission(principal="founder", boundary="_require_human", org_slug=slug,
            admission_id=uuid.uuid4().hex, admitted_at=_now())
        return owner.apply(slug, body, admission)
    return invoke(owner, slug, submit, operation_key=body.operation_key)


@router.get("/team-moves/by-key/{operation_key}")
def show_key(slug: str, operation_key: str, request: Request):
    owner = service(request)
    return invoke(owner, slug, lambda: owner.show(slug, operation_key=operation_key), operation_key=operation_key)


@router.get("/team-moves/{operation_id}")
def show(slug: str, operation_id: str, request: Request):
    owner = service(request)
    return invoke(owner, slug, lambda: owner.show(slug, operation_id=operation_id), operation_id=operation_id)


@router.post("/team-moves/{operation_id}/recover", openapi_extra=body_schema(RecoverMove),
             responses={202: {"description": "Bounded recovery accepted; not success"}})
async def recover(slug: str, operation_id: str, request: Request):
    body = await command_body(request, RecoverMove)
    owner = service(request)
    return invoke(owner, slug, lambda: owner.recover(slug, operation_id, body.expected_revision), operation_id=operation_id)
