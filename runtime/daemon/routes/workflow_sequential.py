"""Sequential Founder projections and settings; no agent transport."""
from __future__ import annotations

import sqlite3
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Path, Query, Request, Response

from runtime.daemon.auth import require_token
from runtime.daemon.routes._org_dep import OrgDep
from runtime.workflows.authority import WorkflowAuthorityError
from runtime.workflows.profile_coordinator import ProfileCoordinatorError
from runtime.workflows.sequential_coding_policy import CodingEvidenceRefusal
from runtime.workflows.sequential_settings import (
    COMMAND_LIMIT, SettingsCommand, SettingsReceipt, parse_settings_command, settings_operation,
)
from runtime.workflows.sequential_views import (
    DefinitionList, DefinitionView, ProjectionRefusal, RunDetail, RunList,
    SettingsView, TaskWorkflowView, read_projection,
    GenericRunDetail, HistoricalRunDetail, ProjectionFailure, ProjectionRuns, ProjectionUnavailable,
    TaskRunsView, read_projection_v2,
)

router = APIRouter(dependencies=[require_token()])
Identifier = Annotated[str, Path(min_length=1, max_length=128)]
_RESPONSES = {
    401: {'description': 'Existing Founder bearer required; no task-session fallback'},
    403: {'description': 'Current fixed principal or repository authority denied'},
    404: {'description': 'Absent selected record or task (before ownership classification)'},
    409: {'description': 'Unavailable current authority, ownership, compatible history or finite read budget'},
    422: {'description': 'Unexpected/duplicate query or nonempty GET body'},
}


async def _read(request: Request, response: Response, org: OrgDep, kind: str,
                selected: str | None = None) -> object:
    response.headers['Cache-Control'] = 'no-store'
    expected = [('definition_identity', selected)] if kind == 'settings' else []
    if list(request.query_params.multi_items()) != expected:
        raise HTTPException(422, detail={'code': 'sequential_read_invalid'})
    async for chunk in request.stream():
        if chunk:
            raise HTTPException(422, detail={'code': 'sequential_read_invalid'})
    # This native existence check deliberately precedes authority/ownership work.
    if kind == 'task' and org.db.get_task(selected) is None:
        raise HTTPException(404, detail={'code': 'task_not_found'})
    try:
        return await read_projection(org, kind=kind, selected=selected)
    except ProjectionRefusal as exc:
        raise HTTPException(exc.status, detail={'code': exc.code}) from None
    except CodingEvidenceRefusal as exc:
        raise HTTPException(409, detail={'code': exc.cause.value}) from None
    except (WorkflowAuthorityError, ProfileCoordinatorError, OSError):
        raise HTTPException(409, detail={'code': 'sequential_authority_unverified'}) from None
    except (sqlite3.Error, ValueError, TypeError, KeyError, IndexError, StopIteration, RecursionError):
        raise HTTPException(409, detail={'code': 'sequential_history_unverified'}) from None


@router.get('/workflows/sequential/definitions', response_model=DefinitionList, responses=_RESPONSES)
async def definitions(request: Request, response: Response, org: OrgDep) -> object:
    return await _read(request, response, org, 'definitions')


@router.get('/workflows/sequential/definitions/{definition_id}', response_model=DefinitionView, responses=_RESPONSES)
async def definition(request: Request, response: Response, org: OrgDep, definition_id: Identifier) -> object:
    return await _read(request, response, org, 'definition', definition_id)


@router.get('/workflows/sequential/settings', response_model=SettingsView, responses=_RESPONSES)
async def settings(request: Request, response: Response, org: OrgDep,
                   definition_identity: Annotated[str, Query(max_length=140)]) -> object:
    return await _read(request, response, org, 'settings', definition_identity)


@router.get('/workflows/sequential/runs', response_model=RunList, responses=_RESPONSES)
async def runs(request: Request, response: Response, org: OrgDep) -> object:
    return await _read(request, response, org, 'runs')


@router.get('/workflows/sequential/runs/{run_id}', response_model=RunDetail, responses=_RESPONSES)
async def run(request: Request, response: Response, org: OrgDep, run_id: Identifier) -> object:
    return await _read(request, response, org, 'run', run_id)


@router.get('/tasks/{task_id}/sequential-workflow', response_model=TaskWorkflowView, responses=_RESPONSES)
async def task_workflow(request: Request, response: Response, org: OrgDep, task_id: Identifier) -> object:
    return await _read(request, response, org, 'task', task_id)


def _settings_body() -> dict:
    schema = SettingsCommand.model_json_schema()
    definitions = schema.pop('$defs', {})

    def inline(value: Any) -> Any:
        if isinstance(value, dict):
            if '$ref' in value:
                return inline(definitions[value['$ref'].rsplit('/', 1)[-1]])
            return {key: inline(item) for key, item in value.items()}
        if isinstance(value, list):
            return [inline(item) for item in value]
        return value
    return {'requestBody': {'required': True, 'content': {'application/json': {'schema': inline(schema)}}}}


async def _settings_operation(request: Request, response: Response, org: OrgDep,
                              operation_key: str | None = None) -> object:
    response.headers['Cache-Control'] = 'no-store'
    expected = [('operation_key', operation_key)] if request.method == 'GET' else []
    if list(request.query_params.multi_items()) != expected:
        raise HTTPException(422, detail={'code': 'sequential_settings_request_invalid'})
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw) + len(chunk) > COMMAND_LIMIT:
            raise HTTPException(422, detail={'code': 'sequential_settings_request_invalid'})
        raw.extend(chunk)
    command = None
    try:
        if request.method == 'PUT':
            if request.headers.get('content-type', '').split(';', 1)[0].strip().lower() != 'application/json':
                raise ValueError('content_type')
            command = parse_settings_command(bytes(raw))
            operation_key = command.operation_key
        elif raw:
            raise ValueError('unexpected_body')
        else:
            # Reuse the closed operation-key grammar; no principal is supplied.
            from pydantic import TypeAdapter
            from runtime.workflows.sequential_contracts import Token
            TypeAdapter(Token).validate_python(operation_key, strict=True)
    except (ValueError, TypeError, RecursionError):
        raise HTTPException(422, detail={'code': 'sequential_settings_request_invalid'}) from None

    def revalidate() -> None:
        daemon = request.app.state.daemon
        if daemon.is_idle or daemon.orgs.get(org.slug) is not org:
            raise ProjectionRefusal('sequential_authority_unverified')

    try:
        revalidate()
        return await settings_operation(org, key=operation_key, command=command, revalidate=revalidate)
    except ProjectionRefusal as exc:
        raise HTTPException(exc.status, detail={'code': exc.code}) from None
    except CodingEvidenceRefusal as exc:
        raise HTTPException(409, detail={'code': exc.cause.value}) from None
    except (WorkflowAuthorityError, ProfileCoordinatorError, OSError):
        raise HTTPException(409, detail={'code': 'sequential_authority_unverified'}) from None
    except (sqlite3.Error, ValueError, TypeError, KeyError, IndexError, StopIteration, RecursionError):
        raise HTTPException(409, detail={'code': 'sequential_history_unverified'}) from None


@router.put('/workflows/sequential/settings', response_model=SettingsReceipt,
    responses={**_RESPONSES, 422: {'description': 'Closed canonical repo-free command; no query/principal/admission selector'}},
    openapi_extra=_settings_body())
async def put_settings(request: Request, response: Response, org: OrgDep) -> object:
    return await _settings_operation(request, response, org)


@router.get('/workflows/sequential/settings/operations', response_model=SettingsReceipt,
    responses=_RESPONSES)
async def original_settings_operation(request: Request, response: Response, org: OrgDep,
                                      operation_key: Annotated[str, Query(min_length=1, max_length=128)]) -> object:
    return await _settings_operation(request, response, org, operation_key)


# S7a additive read-only surface. No Start@2/control/wait mutation is mounted.
_V2_RESPONSES = {**_RESPONSES,
    403: {'model': ProjectionFailure, 'description': 'Current authority denied'},
    409: {'model': ProjectionFailure, 'description': 'Explicit unavailable projection; never an empty run list'},
}


def _unavailable(org: OrgDep, code: str, status: int = 409) -> HTTPException:
    return HTTPException(status, detail=ProjectionUnavailable(org_slug=org.slug, code=code).model_dump(mode='json'),
                         headers={'Cache-Control': 'no-store'})


async def _read_v2(request: Request, response: Response, org: OrgDep, kind: str,
                   selected: str | None = None) -> object:
    response.headers['Cache-Control'] = 'no-store'
    if request.query_params:
        raise HTTPException(422, detail={'code': 'sequential_read_invalid'})
    async for chunk in request.stream():
        if chunk:
            raise HTTPException(422, detail={'code': 'sequential_read_invalid'})
    if kind == 'task' and org.db.get_task(selected) is None:
        raise HTTPException(404, detail={'code': 'task_not_found'})
    try:
        return await read_projection_v2(org, kind=kind, selected=selected)
    except ProjectionRefusal as exc:
        if exc.status == 404:
            raise HTTPException(404, detail={'code': exc.code}) from None
        raise _unavailable(org, 'authority_denied' if exc.status == 403 else 'history_unverified', exc.status) from None
    except TimeoutError:
        raise _unavailable(org, 'read_budget_exhausted') from None
    except (WorkflowAuthorityError, ProfileCoordinatorError, OSError):
        raise _unavailable(org, 'authority_unverified') from None
    except (sqlite3.Error, ValueError, TypeError, KeyError, IndexError, StopIteration, RecursionError) as exc:
        from runtime.workflows._read_exhaustion import ReadExhaustion
        causal, seen = exc, set()
        while causal is not None and id(causal) not in seen:
            seen.add(id(causal))
            if isinstance(causal, ReadExhaustion):
                raise _unavailable(org, 'read_budget_exhausted') from None
            causal = causal.__cause__
        raise _unavailable(org, 'history_unverified') from None


@router.get('/workflows/sequential/views/v2/runs', response_model=ProjectionRuns, responses=_V2_RESPONSES)
async def runs_v2(request: Request, response: Response, org: OrgDep) -> object:
    return await _read_v2(request, response, org, 'runs')


@router.get('/workflows/sequential/views/v2/runs/{run_id}',
    response_model=GenericRunDetail | HistoricalRunDetail, responses=_V2_RESPONSES)
async def run_v2(request: Request, response: Response, org: OrgDep, run_id: Identifier) -> object:
    return await _read_v2(request, response, org, 'run', run_id)


@router.get('/tasks/{task_id}/sequential-workflows', response_model=TaskRunsView, responses=_V2_RESPONSES)
async def task_workflows(request: Request, response: Response, org: OrgDep, task_id: Identifier) -> object:
    return await _read_v2(request, response, org, 'task', task_id)
