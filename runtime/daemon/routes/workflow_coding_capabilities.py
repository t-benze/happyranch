"""Closed Founder-only setup issue/withdraw and safe selected metadata read."""
from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Request

from runtime.daemon.auth import _require_human
from runtime.daemon.routes._org_dep import OrgDep
from runtime.workflows.authority import WorkflowAuthorityError
from runtime.workflows.coding_saved_records import COMMAND_LIMIT
from runtime.workflows.coding_setup import CodingSetupError, SetupCommand, SetupReceipt
from runtime.workflows.profile_coordinator import ProfileCoordinatorError
from runtime.workflows.templates import WorkflowTemplatePrincipal


router = APIRouter(dependencies=[Depends(_require_human)])
_RESPONSES = {403: {'description': 'Existing Founder authority required'},
              404: {'description': 'Unknown org or selected setup'},
              409: {'description': 'Stale authority, setup CAS or operation conflict; compatible J required'},
              422: {'description': 'Closed bounded canonical command required; no query parameters'},
              500: {'description': 'Corrupt retained setup closure'}}


def _principal(request: Request, org) -> WorkflowTemplatePrincipal:
    def revalidate() -> None:
        state = request.app.state.daemon
        if state.is_idle or state.orgs.get(org.slug) is not org:
            raise CodingSetupError('coding_setup_org_stale')
    revalidate()
    return WorkflowTemplatePrincipal.founder(org_slug=org.slug,
        team_slug=org.teams.default_team, revalidate=revalidate)


def _query(request: Request) -> None:
    # No identity/session/selector query is accepted, including duplicates.
    if request.scope.get('query_string', b''):
        raise CodingSetupError('coding_setup_invalid_request')


def _raise(exc: Exception) -> None:
    if isinstance(exc, CodingSetupError):
        code = exc.code
        status = (403 if code == 'human_only' else 404 if code == 'coding_setup_not_found'
                  else 422 if code == 'coding_setup_invalid_request'
                  else 500 if code in {'coding_setup_caller_transaction', 'coding_setup_whole_schema_mismatch'} else 409)
    elif isinstance(exc, (WorkflowAuthorityError, ProfileCoordinatorError)):
        code, status = 'coding_setup_authority_unavailable', 409
    else:
        code, status = 'coding_setup_storage_corrupt', 500
    raise HTTPException(status_code=status, detail={'code': code}) from None


def _command_schema() -> dict:
    # Inline the closed models for Request-based raw bounded parsing; FastAPI
    # must not consume an unbounded/duplicate-erased Body before this handler.
    schema = SetupCommand.model_json_schema()
    definitions = schema.pop('$defs', {})

    def inline(value):
        if isinstance(value, dict):
            if '$ref' in value:
                return inline(definitions[value['$ref'].rsplit('/', 1)[-1]])
            return {key: inline(item) for key, item in value.items()}
        if isinstance(value, list):
            return [inline(item) for item in value]
        return value
    schema = inline(schema)
    scope = schema['properties']['scope']['anyOf'][0]
    schema['oneOf'] = [dict(properties=dict(mode={'const': 'issue'}, scope=scope)),
                       dict(properties=dict(mode={'const': 'withdraw'}, scope={'type': 'null'}))]
    return schema


@router.post('/workflows/coding/capabilities', response_model=SetupReceipt,
             responses=_RESPONSES, openapi_extra={'requestBody': {'required': True,
                 'content': {'application/json': {'schema': _command_schema()}}}})
async def set_coding_capability(request: Request, org: OrgDep) -> dict:
    try:
        principal = _principal(request, org)
        _query(request)
        if request.headers.get('content-type', '').split(';', 1)[0].strip().lower() != 'application/json':
            raise CodingSetupError('coding_setup_invalid_request')
        chunks = bytearray()
        async for chunk in request.stream():
            if len(chunks) + len(chunk) > COMMAND_LIMIT:
                raise CodingSetupError('coding_setup_invalid_request')
            chunks.extend(chunk)
        principal.revalidate()  # Body streaming is an await boundary too.
        return await org.workflow_coding_setup.set(principal=principal, raw=bytes(chunks))
    except (ValueError, WorkflowAuthorityError, ProfileCoordinatorError, sqlite3.Error) as exc:
        _raise(exc)


@router.get('/workflows/coding/capabilities/{setup_key}', response_model=SetupReceipt,
            responses=_RESPONSES)
async def get_coding_capability(setup_key: str, request: Request, org: OrgDep) -> dict:
    try:
        principal = _principal(request, org)
        _query(request)
        return await org.workflow_coding_setup.get(principal=principal, setup_key=setup_key)
    except (ValueError, WorkflowAuthorityError, ProfileCoordinatorError, sqlite3.Error) as exc:
        _raise(exc)
