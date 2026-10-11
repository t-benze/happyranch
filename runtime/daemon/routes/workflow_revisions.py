"""Explicit owner return, safe revision receipt, and selected new-author stage."""
from __future__ import annotations

import re

from fastapi import APIRouter, HTTPException, Query, Request, Response

from runtime.daemon.auth import optional_bearer
from runtime.daemon.routes._org_dep import OrgDep
from runtime.daemon.routes.workflow_reviews import human_principal
from runtime.daemon.routes.workflow_submissions import (
    ERRORS, READ_RESPONSES, TOKEN_SCHEMA, agent_transport, fail, proof_schema, query_values, raise_error, sqlite_error_types,
)
from runtime.workflows.artifacts import decode_artifact_json
from runtime.workflows.revisions import (
    AuthorRevisionCommand, DocumentRevisionCommand, DocumentRevisionReceipt, parse_revision,
)

router = APIRouter()


def decode_revision_command(raw: bytes, *, author: bool = False) -> dict:
    try:
        if not 1 <= len(raw) <= (2097152 if author else 1048576):
            raise ValueError('bounds')
        value = decode_artifact_json(raw)
        return parse_revision(value, author=author)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise HTTPException(status_code=422, detail={'code': 'workflow_document_revision_invalid_request'}) from exc


async def command_body(request: Request, *, author: bool = False) -> dict:
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw) + len(chunk) > (2097152 if author else 1048576):
            fail('workflow_document_revision_invalid_request', 422)
        raw.extend(chunk)
    return decode_revision_command(bytes(raw), author=author)


def revision_error(exc: Exception) -> None:
    code = exc.code
    if code == 'workflow_document_revision_not_found':
        fail(code, 404)
    if code == 'workflow_document_revision_read_not_authorized':
        fail(code, 403)
    if code in {'workflow_document_revision_operation_conflict', 'workflow_document_revision_source_not_current',
                'workflow_document_revision_predecessor_not_closed', 'workflow_document_revision_schema_required'}:
        detail = {'code': code}
        if getattr(exc, 'owner', None):
            detail.update(owner=exc.owner, required_action=exc.required_action)
        raise HTTPException(status_code=409, detail=detail) from exc
    if code == 'workflow_document_revision_invalid_request':
        fail(code, 422)
    if code in {'workflow_activation_storage_corrupt', 'workflow_document_revision_storage_corrupt',
                'template_version_not_found', 'workflow_activation_not_found', 'workflow_activation_invalid_request'}:
        fail('workflow_document_revision_storage_corrupt', 500)
    raise_error(exc)


async def result_or_error(coroutine) -> dict:
    try:
        return await coroutine
    except ERRORS as exc:
        revision_error(exc)
    except sqlite_error_types() as exc:
        raise HTTPException(status_code=500, detail={'code': 'workflow_document_revision_storage_unavailable'}) from exc
    except (ValueError, KeyError, TypeError) as exc:
        raise HTTPException(status_code=500, detail={'code': 'workflow_document_revision_storage_corrupt'}) from exc


WRITE_RESPONSES = dict(READ_RESPONSES)
WRITE_RESPONSES[200] = {'model': DocumentRevisionReceipt, 'description': 'Exact stable operation replay with live current projection'}


@router.post('/workflows/instances/{instance_id}/revisions', status_code=201,
    response_model=DocumentRevisionReceipt, responses=WRITE_RESPONSES,
    openapi_extra={'requestBody': {'required': True, 'content': {'application/json': {'schema': DocumentRevisionCommand.model_json_schema()}}}})
async def start_revision(org: OrgDep, instance_id: str, request: Request, response: Response,
                         has_bearer: bool = optional_bearer()) -> dict:
    principal = human_principal(org, has_bearer)
    if request.query_params or re.fullmatch(r'[A-Za-z0-9._:-]{1,128}', instance_id) is None:
        fail('workflow_document_revision_invalid_request', 422)
    command = await command_body(request)
    if command['instance_id'] != instance_id:
        fail('workflow_document_revision_invalid_request', 422)
    receipt = await result_or_error(org.workflow_revisions.start(principal=principal, command=command))
    response.status_code = 200 if receipt['replayed'] else 201
    if not receipt['replayed']:
        from runtime.daemon.runner import enqueue_task
        try:
            enqueue_task(request.app.state.daemon, org.slug, receipt['core']['author_task_id'])
        except Exception:
            import logging
            logging.getLogger(__name__).exception('workflow revision notification unavailable')
    return receipt


@router.get('/workflows/revisions/{revision_operation_id}', response_model=DocumentRevisionReceipt,
    responses=READ_RESPONSES, openapi_extra={'x-query-proof-schema': proof_schema(
        {'proof_kind': {'type': 'string', 'const': 'owner-human'}}, {'session_id': TOKEN_SCHEMA})})
async def get_revision(org: OrgDep, revision_operation_id: str, request: Request,
                       session_id: str | None = Query(default=None), proof_kind: str | None = Query(default=None),
                       has_bearer: bool = optional_bearer()) -> dict:
    values = query_values(request, {'session_id', 'proof_kind'})
    if re.fullmatch(r'[A-Za-z0-9._:-]{1,128}', revision_operation_id) is None:
        fail('workflow_document_revision_invalid_request', 422)
    principal = None
    if set(values) == {'proof_kind'} and proof_kind == 'owner-human':
        principal = human_principal(org, has_bearer)
    elif set(values) == {'session_id'}:
        agent_transport(request, has_bearer)
    else:
        fail('workflow_document_revision_invalid_request', 422)
    return await result_or_error(org.workflow_revisions.get(operation_id=revision_operation_id,
        principal=principal, session_id=session_id))


@router.post('/workflows/revisions/{revision_operation_id}/submissions', status_code=201,
    response_model=DocumentRevisionReceipt, responses=WRITE_RESPONSES,
    openapi_extra={'x-query-proof-schema': proof_schema({'session_id': TOKEN_SCHEMA}),
        'requestBody': {'required': True, 'content': {'application/json': {'schema': AuthorRevisionCommand.model_json_schema()}}}})
async def stage_revision(org: OrgDep, revision_operation_id: str, request: Request, response: Response,
                         session_id: str | None = Query(default=None), has_bearer: bool = optional_bearer()) -> dict:
    agent_transport(request, has_bearer)
    if (set(query_values(request, {'session_id'})) != {'session_id'}
            or re.fullmatch(r'[A-Za-z0-9._:-]{1,128}', revision_operation_id) is None):
        fail('workflow_document_revision_invalid_request', 422)
    command = await command_body(request, author=True)
    if command['revision_operation_id'] != revision_operation_id:
        fail('workflow_document_revision_invalid_request', 422)
    receipt = await result_or_error(org.workflow_revisions.stage(session_id=session_id, command=command))
    response.status_code = 200 if receipt['replayed'] else 201
    return receipt
