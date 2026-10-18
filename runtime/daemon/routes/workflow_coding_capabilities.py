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


# Manager mapping uses existing task-session transport exclusively. This
# separate router has no inherited Founder dependency or bearer fallback.
from runtime.workflows.coding_mapping import MappingCommand, MappingReceipt

mapping_router = APIRouter()


def _mapping_command_schema():
    schema = MappingCommand.model_json_schema()
    definitions = schema.pop('$defs', {})

    def inline(value):
        if isinstance(value, dict):
            if '$ref' in value:
                return inline(definitions[value['$ref'].rsplit('/', 1)[-1]])
            return {key: inline(item) for key, item in value.items()}
        if isinstance(value, list):
            return [inline(item) for item in value]
        return value
    return inline(schema)


@mapping_router.post('/workflows/coding/mappings', response_model=MappingReceipt,
    responses={201: {'description': 'New admitted mapping'}, **_RESPONSES},
    openapi_extra={'requestBody': {'required': True, 'content': {'application/json': {'schema': _mapping_command_schema()}}}})
@mapping_router.get('/workflows/coding/mappings', response_model=MappingReceipt, responses=_RESPONSES)
async def coding_mapping(request: Request, org: OrgDep, session_id: str) -> dict:
    from runtime.workflows.coding_mapping import CodingMappingError
    from runtime.workflows.templates import WorkflowTemplateError
    from runtime.infrastructure.task_pause_controls import PauseControlError
    from runtime.orchestrator.task_pause import DeferredRootPause
    from runtime.orchestrator.team_move import DeferredTeamMove
    if 'authorization' in request.headers:
        raise HTTPException(403, detail={'code': 'bearer_not_accepted'})
    if list(request.query_params.multi_items()) != [('session_id', session_id)] or not session_id:
        raise HTTPException(422, detail={'code': 'coding_mapping_invalid_request'})
    context = org.sessions.get_context_by_session(session_id)
    if context is None or context[0] != org.slug:
        raise HTTPException(403, detail={'code': 'coding_mapping_manager_required'})
    _, task_id, agent = context
    try:
        async with org.teams_lock:
            teams = org.teams.teams_for_manager(agent)
            if len(teams) != 1:
                raise CodingMappingError('coding_mapping_manager_required')
            team = teams[0]
            def revalidate():
                state = request.app.state.daemon
                if (state.is_idle or state.orgs.get(org.slug) is not org
                        or org.sessions.get_context_by_session(session_id) != context
                        or org.sessions.get_active(task_id, agent) != session_id
                        or org.sessions.is_recovery_session(task_id, agent, session_id)
                        or org.teams.teams_for_manager(agent) != (team,)):
                    raise CodingMappingError('coding_mapping_manager_revoked')
            principal = WorkflowTemplatePrincipal.agent(org_slug=org.slug, agent_name=agent,
                team_slug=team, task_id=task_id, session_id=session_id, revalidate=revalidate)
            revalidate()
            if request.method == 'GET':
                async for chunk in request.stream():
                    if chunk:
                        raise CodingMappingError('coding_mapping_invalid_request')
                result = await org.workflow_coding_mapping.get(principal=principal)
            else:
                if request.headers.get('content-type', '').split(';', 1)[0].strip().lower() != 'application/json':
                    raise CodingMappingError('coding_mapping_invalid_request')
                raw = bytearray()
                async for chunk in request.stream():
                    if len(raw) + len(chunk) > COMMAND_LIMIT:
                        raise CodingMappingError('coding_mapping_invalid_request')
                    raw.extend(chunk)
                revalidate()
                result = await org.workflow_coding_mapping.publish(principal=principal, raw=bytes(raw))
            from fastapi.responses import JSONResponse
            return JSONResponse(result, status_code=201 if request.method == 'POST' and not result['replayed'] else 200)
    except CodingMappingError as exc:
        status = (422 if exc.code == 'coding_mapping_invalid_request' else
                  404 if exc.code == 'coding_mapping_not_found' else
                  403 if any(word in exc.code for word in ('revoked', 'manager_required')) else 409)
        raise HTTPException(status, detail={'code': exc.code}) from None
    except (WorkflowAuthorityError, ProfileCoordinatorError, WorkflowTemplateError,
            PauseControlError, DeferredRootPause, DeferredTeamMove):
        raise HTTPException(409, detail={'code': 'coding_mapping_authority_unavailable'}) from None
    except (ValueError, OSError, sqlite3.Error):
        raise HTTPException(409, detail={'code': 'coding_mapping_admission_unavailable'}) from None


from runtime.workflows.coding_source import SourceCommand, SourceReceipt


def _source_command_schema():
    schema = SourceCommand.model_json_schema()
    definitions = schema.pop('$defs', {})
    def inline(value):
        if isinstance(value, dict):
            if '$ref' in value:
                return inline(definitions[value['$ref'].rsplit('/', 1)[-1]])
            return {key: inline(item) for key, item in value.items()}
        if isinstance(value, list):
            return [inline(item) for item in value]
        return value
    return inline(schema)


@mapping_router.post('/workflows/coding/sources', response_model=SourceReceipt,
    responses={201: {'description': 'New nonallocating source capability'}, **_RESPONSES},
    openapi_extra={'requestBody': {'required': True, 'content': {'application/json': {'schema': _source_command_schema()}}}})
async def mint_coding_source(request: Request, org: OrgDep, session_id: str) -> dict:
    return await _coding_source(request, org, session_id, None)


@mapping_router.get('/workflows/coding/sources/{authorization_id}', response_model=SourceReceipt, responses=_RESPONSES)
async def get_coding_source(authorization_id: str, request: Request, org: OrgDep, session_id: str) -> dict:
    return await _coding_source(request, org, session_id, authorization_id)


async def _coding_source(request, org, session_id, authorization_id):
    from runtime.workflows.coding_source import CodingSourceError
    from runtime.workflows.templates import WorkflowTemplateError
    from runtime.infrastructure.task_pause_controls import PauseControlError
    from runtime.orchestrator.task_pause import DeferredRootPause
    from runtime.orchestrator.team_move import DeferredTeamMove
    if 'authorization' in request.headers:
        raise HTTPException(403, detail={'code': 'bearer_not_accepted'})
    if list(request.query_params.multi_items()) != [('session_id', session_id)] or not session_id or (request.method == 'POST' and authorization_id is not None):
        raise HTTPException(422, detail={'code': 'coding_source_invalid_request'})
    context = org.sessions.get_context_by_session(session_id)
    if context is None or context[0] != org.slug:
        raise HTTPException(403, detail={'code': 'coding_source_manager_required'})
    _, task_id, agent = context
    try:
        async with org.teams_lock:
            teams = org.teams.teams_for_manager(agent)
            if len(teams) != 1:
                raise CodingSourceError('coding_source_manager_required')
            team = teams[0]
            def revalidate():
                state = request.app.state.daemon
                if (state.is_idle or state.orgs.get(org.slug) is not org
                        or org.sessions.get_context_by_session(session_id) != context
                        or org.sessions.get_active(task_id, agent) != session_id
                        or org.sessions.is_recovery_session(task_id, agent, session_id)
                        or org.teams.teams_for_manager(agent) != (team,)):
                    raise CodingSourceError('coding_source_manager_revoked')
            principal = WorkflowTemplatePrincipal.agent(org_slug=org.slug, agent_name=agent,
                team_slug=team, task_id=task_id, session_id=session_id, revalidate=revalidate)
            revalidate()
            if request.method == 'GET':
                async for chunk in request.stream():
                    if chunk:
                        raise CodingSourceError('coding_source_invalid_request')
                result = await org.workflow_coding_source.get(principal=principal, authorization_id=authorization_id)
            else:
                if request.headers.get('content-type', '').split(';', 1)[0].strip().lower() != 'application/json':
                    raise CodingSourceError('coding_source_invalid_request')
                raw = bytearray()
                async for chunk in request.stream():
                    if len(raw) + len(chunk) > 65536:
                        raise CodingSourceError('coding_source_invalid_request')
                    raw.extend(chunk)
                revalidate()
                result = await org.workflow_coding_source.mint(principal=principal, raw=bytes(raw))
            from fastapi.responses import JSONResponse
            return JSONResponse(result, status_code=201 if request.method == 'POST' and not result['replayed'] else 200)
    except CodingSourceError as exc:
        status = (422 if exc.code == 'coding_source_invalid_request' else
                  404 if exc.code == 'coding_source_not_found' else
                  403 if any(word in exc.code for word in ('revoked', 'manager_required')) else 409)
        raise HTTPException(status, detail={'code': exc.code}) from None
    except (WorkflowAuthorityError, ProfileCoordinatorError, WorkflowTemplateError,
            PauseControlError, DeferredRootPause, DeferredTeamMove):
        raise HTTPException(409, detail={'code': 'coding_source_authority_unavailable'}) from None
    except (ValueError, OSError, sqlite3.Error):
        raise HTTPException(409, detail={'code': 'coding_source_admission_unavailable'}) from None
