"""Original-current author staging and current-authorized safe H receipts."""
from __future__ import annotations

import json
import re
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Query, Request, Response
from pydantic import ValidationError

from runtime.daemon.auth import _require_human, optional_bearer
from runtime.daemon.routes._org_dep import OrgDep
from runtime.daemon.routes.workflow_activations import _raise as activation_error
from runtime.workflows.activation import WorkflowActivationError
from runtime.workflows.authority import WorkflowAuthorityError
from runtime.workflows.profile_coordinator import ProfileCoordinatorError
from runtime.workflows.submissions import AuthorHandoffCommand, AuthorHandoffReceipt
from runtime.workflows.templates import WorkflowTemplateError

router = APIRouter()
IDENTITY_KEYS = {'actor', 'agent', 'agent_name', 'principal', 'principal_id', 'principal_kind', 'proof_kind',
                 'org', 'org_slug', 'task', 'task_id', 'session_id', 'result_id', 'handoff_id', 'submission_id'}
ERRORS = (WorkflowActivationError, WorkflowAuthorityError, WorkflowTemplateError, ProfileCoordinatorError)


def fail(code: str, status: int) -> None:
    raise HTTPException(status_code=status, detail={'code': code})


def raise_error(exc: Any) -> None:
    code = exc.code
    if code in {'unknown_session', 'cross_org_session', 'session_not_current', 'recovery_purpose_forbidden',
                'workflow_author_handoff_action_not_authorized', 'workflow_author_handoff_read_not_authorized'}:
        fail(code, 403)
    if code in {'workflow_author_handoff_source_not_current', 'workflow_author_handoff_operation_conflict',
                'workflow_author_handoff_schema_required'}:
        detail = {'code': code}
        if getattr(exc, 'owner', None):
            detail.update(owner=exc.owner, required_action=exc.required_action)
        raise HTTPException(status_code=409, detail=detail) from exc
    activation_error(exc)


def query_values(request: Request, allowed: set[str]) -> dict[str, str]:
    pairs = list(request.query_params.multi_items())
    values = dict(pairs)
    if (len(pairs) != len(values) or set(values) - allowed
            or any(not v or (k != 'proof_kind' and re.fullmatch(r'[A-Za-z0-9._:-]{1,128}', v) is None)
                   for k, v in values.items())):
        fail('workflow_author_handoff_invalid_request', 422)
    return values


def agent_transport(request: Request, has_bearer: bool) -> None:
    if has_bearer:
        fail('auth_path_conflict', 403)
    if 'authorization' in request.headers:
        fail('bearer_not_accepted', 403)


def decode_command(raw: bytes) -> dict:
    def pairs(items: list[tuple[str, object]]) -> dict:
        value = {}
        for k, v in items:
            if k in value:
                raise ValueError('duplicate')
            value[k] = v
        return value
    try:
        if not 1 <= len(raw) <= 2097152 or raw.startswith(b'\xef\xbb\xbf'):
            raise ValueError('size')
        value = json.loads(raw.decode('utf8'), object_pairs_hook=pairs)
        if isinstance(value, dict) and set(value) & IDENTITY_KEYS:
            fail('body_identity_rejected', 403)
        return AuthorHandoffCommand.model_validate(value).model_dump()
    except (ValueError, UnicodeError, ValidationError) as exc:
        raise HTTPException(status_code=422, detail={'code': 'workflow_author_handoff_invalid_request'}) from exc


@router.post('/workflows/submissions/handoffs', status_code=201, response_model=AuthorHandoffReceipt,
             responses={200: {'model': AuthorHandoffReceipt, 'description': 'Exact before-callback original-current replay'},
                        403: {'description': 'Current session/grant refused'}, 409: {'description': 'Source/schema/authority/conflict refusal'},
                        422: {'description': 'Closed bounded request refused'}, 500: {'description': 'Storage unavailable or corrupt'}},
             openapi_extra={'requestBody': {'required': True, 'content': {'application/json': {
                 'schema': AuthorHandoffCommand.model_json_schema()}}}})
async def stage_handoff(org: OrgDep, request: Request, response: Response, body: object = Body(...),
                        session_id: str = Query(min_length=1, max_length=128, pattern=r'^[A-Za-z0-9._:-]+$'),
                        has_bearer: bool = optional_bearer()) -> dict:
    query_values(request, {'session_id'})
    agent_transport(request, has_bearer)
    command = decode_command(await request.body())
    try:
        receipt = await org.workflow_submissions.stage_author_handoff(session_id=session_id, command=command)
    except ERRORS as exc:
        raise_error(exc)
    except sqlite_error_types() as exc:
        raise HTTPException(status_code=500, detail={'code': 'workflow_author_handoff_storage_unavailable'}) from exc
    response.status_code = 200 if receipt['replayed'] else 201
    return receipt


def sqlite_error_types() -> tuple[type[Exception], ...]:
    import sqlite3
    return (sqlite3.DatabaseError,)


@router.get('/workflows/submissions/handoffs/{handoff_id}', response_model=AuthorHandoffReceipt)
async def get_handoff(org: OrgDep, handoff_id: str, request: Request,
                      session_id: str | None = Query(default=None), activation_id: str | None = Query(default=None),
                      request_id: str | None = Query(default=None), proof_kind: str | None = Query(default=None),
                      has_bearer: bool = optional_bearer()) -> dict:
    values = query_values(request, {'session_id', 'activation_id', 'request_id', 'proof_kind'})
    if re.fullmatch(r'[A-Za-z0-9._:-]{1,128}', handoff_id) is None:
        fail('workflow_author_handoff_invalid_request', 422)
    if set(values) == {'proof_kind'} and proof_kind == 'owner-human':
        lane, selector = 'owner-human', None
        _require_human(has_bearer)
    elif set(values) == {'session_id', 'activation_id'}:
        lane, selector = 'source-agent', activation_id
        agent_transport(request, has_bearer)
    elif set(values) == {'session_id', 'request_id'}:
        lane, selector = 'request-agent', request_id
        agent_transport(request, has_bearer)
    else:
        fail('workflow_author_handoff_invalid_request', 422)
    try:
        return await org.workflow_submissions.get_handoff(handoff_id=handoff_id, lane=lane,
                                                          session_id=session_id, selector=selector)
    except ERRORS as exc:
        raise_error(exc)
