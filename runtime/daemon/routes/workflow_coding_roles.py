"""Current-manager CLI role defaults and nonallocating pinned selection."""
from __future__ import annotations

import sqlite3

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from runtime.daemon.routes._org_dep import OrgDep
from runtime.infrastructure.task_pause_controls import PauseControlError
from runtime.orchestrator.task_pause import DeferredRootPause
from runtime.orchestrator.team_move import DeferredTeamMove
from runtime.workflows._coding_role_records import (
    COMMAND_LIMIT, RoleCommand, RoleReceipt, SelectionCommand, SelectionReceipt,
)
from runtime.workflows.coding_start_records import BoundFinalSelection, CodingRunReceipt
from runtime.workflows.artifacts import Token
from runtime.workflows.authority import WorkflowAuthorityError
from runtime.workflows.coding_roles import CodingRoleError
from runtime.workflows.profile_coordinator import ProfileCoordinatorError
from runtime.workflows.templates import WorkflowTemplateError, WorkflowTemplatePrincipal

router = APIRouter()
_RESPONSES = {
    403: {'description': 'Current executable agent manager and eligible roles/repository required; no bearer'},
    404: {'description': 'No selected role generation'},
    409: {'description': 'Native admission/current authority unavailable, stale CAS or conflicting command/pin'},
    422: {'description': 'Closed canonical bounded command and exactly one current session query'},
    500: {'description': 'Corrupt complete saved history/schema'},
}


def _body_schema(model: type) -> dict:
    schema = model.model_json_schema()
    definitions = schema.pop('$defs', {})

    def inline(value: Any) -> Any:
        if isinstance(value, dict):
            if '$ref' in value:
                return inline(definitions[value['$ref'].rsplit('/', 1)[-1]])
            return {key: inline(item) for key, item in value.items()}
        if isinstance(value, list):
            return [inline(item) for item in value]
        return value
    return {'requestBody': {'required': True, 'content': {
        'application/json': {'schema': inline(schema)}}}}


@router.post('/workflows/coding/mappings', response_model=RoleReceipt,
    responses={201: {'description': 'New append-only role generation'}, **_RESPONSES},
    openapi_extra=_body_schema(RoleCommand))
@router.get('/workflows/coding/mappings', response_model=RoleReceipt, responses=_RESPONSES)
async def coding_roles(request: Request, org: OrgDep, session_id: str) -> JSONResponse:
    return await _handle(request, org, session_id, selection=False)


@router.post('/workflows/coding/role-selections', response_model=SelectionReceipt,
    responses=_RESPONSES, openapi_extra=_body_schema(SelectionCommand))
async def coding_role_selection(request: Request, org: OrgDep, session_id: str) -> JSONResponse:
    return await _handle(request, org, session_id, selection=True)


@router.get('/workflows/coding/runs/{instance_id}', response_model=CodingRunReceipt, responses=_RESPONSES)
async def coding_run(request: Request, org: OrgDep, instance_id: Token, session_id: str) -> JSONResponse:
    return await _handle(request, org, session_id, selection=False, instance_id=instance_id)


@router.get('/workflows/coding/runs/{instance_id}/final-manager', response_model=BoundFinalSelection, responses=_RESPONSES)
async def coding_final_run(request: Request, org: OrgDep, instance_id: Token, session_id: str) -> JSONResponse:
    return await _handle(request, org, session_id, selection=False, instance_id=instance_id, final=True)


async def _handle(request: Request, org: Any, session_id: str, *, selection: bool,
                  instance_id: str | None = None, final: bool = False) -> JSONResponse:
    if 'authorization' in request.headers:
        raise HTTPException(403, detail={'code': 'bearer_not_accepted'})
    if list(request.query_params.multi_items()) != [('session_id', session_id)] or not session_id:
        raise HTTPException(422, detail={'code': 'coding_role_invalid_request'})
    context = org.sessions.get_context_by_session(session_id)
    if context is None or context[0] != org.slug:
        raise HTTPException(403, detail={'code': 'coding_role_manager_required'})
    _, task_id, agent = context
    try:
        teams = org.teams.teams_for_manager(agent)
        if len(teams) != 1:
            raise CodingRoleError('coding_role_manager_required')
        team = teams[0]

        def revalidate() -> None:
            state = request.app.state.daemon
            if (state.is_idle or state.orgs.get(org.slug) is not org
                    or org.sessions.get_context_by_session(session_id) != context
                    or org.sessions.get_active(task_id, agent) != session_id
                    or org.sessions.is_recovery_session(task_id, agent, session_id)
                    or org.teams.teams_for_manager(agent) != (team,)):
                raise CodingRoleError('coding_role_manager_revoked')
        principal = WorkflowTemplatePrincipal.agent(org_slug=org.slug, agent_name=agent,
            team_slug=team, task_id=task_id, session_id=session_id, revalidate=revalidate)
        revalidate()
        # Stream bounded bytes before the service acquires authority -> teams.
        raw = bytearray()
        async for chunk in request.stream():
            if len(raw) + len(chunk) > COMMAND_LIMIT:
                raise CodingRoleError('coding_role_invalid_request')
            raw.extend(chunk)
        revalidate()
        if request.method == 'GET':
            if raw:
                raise CodingRoleError('coding_role_invalid_request')
            if instance_id is None:
                result = await org.workflow_coding_roles.get(principal=principal)
            else:
                from runtime.workflows.coding_saved_records import _token
                _token(instance_id)
                if final:
                    result = await org.workflow_coding_start.get_final(principal=principal, instance_id=instance_id)
                else:
                    result = await org.workflow_coding_start.get(principal=principal, instance_id=instance_id)
        else:
            if request.headers.get('content-type', '').split(';', 1)[0].strip().lower() != 'application/json':
                raise CodingRoleError('coding_role_invalid_request')
            if selection:
                result = await org.workflow_coding_roles.select(principal=principal, raw=bytes(raw))
            else:
                result = await org.workflow_coding_roles.publish(principal=principal, raw=bytes(raw))
        revalidate()
        return JSONResponse(result, status_code=201 if request.method == 'POST'
            and not selection and not result['replayed'] else 200)
    except CodingRoleError as exc:
        status = (422 if exc.code == 'coding_role_invalid_request' else
                  404 if exc.code == 'coding_role_not_found' else
                  403 if any(word in exc.code for word in ('revoked', 'manager_required')) else
                  500 if exc.code == 'coding_role_whole_schema_mismatch' else 409)
        raise HTTPException(status, detail={'code': exc.code}) from None
    except (WorkflowAuthorityError, ProfileCoordinatorError, WorkflowTemplateError,
            PauseControlError, DeferredRootPause, DeferredTeamMove):
        raise HTTPException(409, detail={'code': 'coding_role_authority_unavailable'}) from None
    except (ValueError, sqlite3.Error):
        raise HTTPException(500, detail={'code': 'coding_role_history_invalid'}) from None
