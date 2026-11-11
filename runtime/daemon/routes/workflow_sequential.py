"""U7a browser read surface; existing Founder bearer, no agent transport."""
from __future__ import annotations

import sqlite3
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Query, Request, Response

from runtime.daemon.auth import require_token
from runtime.daemon.routes._org_dep import OrgDep
from runtime.workflows.authority import WorkflowAuthorityError
from runtime.workflows.profile_coordinator import ProfileCoordinatorError
from runtime.workflows.sequential_coding_policy import CodingEvidenceRefusal
from runtime.workflows.sequential_views import (
    DefinitionList, DefinitionView, ProjectionRefusal, RunDetail, RunList,
    SettingsView, TaskWorkflowView, read_projection,
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
