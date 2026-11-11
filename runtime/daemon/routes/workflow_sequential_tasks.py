"""Own selected sequential task reads. No bearer/browser or effect executor."""
from __future__ import annotations

import sqlite3
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request, Response

from runtime.daemon.routes._org_dep import OrgDep
from runtime.infrastructure.task_pause_controls import PauseControlError
from runtime.workflows.authority import WorkflowAuthorityError
from runtime.workflows.profile_coordinator import ProfileCoordinatorError
from runtime.workflows.sequential_coding_final_records import CodingFinalSelection
from runtime.workflows.sequential_coding_policy import CodingEvidenceRefusal
from runtime.workflows.sequential_engine import SequentialRefusal
from runtime.workflows.sequential_history import _bytes
from runtime.workflows.sequential_repository_records import RepositoryChangeSubject
from runtime.workflows.sequential_task_transport import selected_read
from runtime.workflows.sequential_views import ProjectionRefusal

router = APIRouter()
Sid = Annotated[str, Query(min_length=1, max_length=128)]
_RESPONSES = {
    403: {'description': 'Own current ordinary task-session only; no bearer/browser/foreign/recovery audience'},
    404: {'description': 'Absent native task before ownership classification'},
    409: {'description': 'Unbound subject, stale selection, cancellation, ownership/history/authority unavailable'},
    422: {'description': 'Exactly one session query, bounded canonical change for Final only; empty body'},
}


async def _read(request: Request, response: Response, org: OrgDep, sid: str,
                change: str | None = None) -> object:
    response.headers['Cache-Control'] = 'no-store'
    if ('authorization' in request.headers or 'origin' in request.headers
            or request.headers.get('x-happyranch-surface') != 'cli'):
        raise HTTPException(403, detail={'code': 'sequential_task_session_required'})
    expected = [('session_id', sid)] + ([] if change is None else [('change', change)])
    if list(request.query_params.multi_items()) != expected:
        raise HTTPException(422, detail={'code': 'sequential_task_read_invalid'})
    async for chunk in request.stream():
        if chunk:
            raise HTTPException(422, detail={'code': 'sequential_task_read_invalid'})
    context = org.sessions.get_context_by_session(sid)
    if context is None or context[0] != org.slug:
        raise HTTPException(403, detail={'code': 'sequential_task_session_required'})
    # SID is a lookup, never a client assertion of task/principal/native owner.
    _, task_id, principal = context

    def revalidate() -> None:
        daemon = request.app.state.daemon
        if (daemon.is_idle or daemon.orgs.get(org.slug) is not org
                or org.sessions.get_context_by_session(sid) != context
                or org.sessions.get_active(task_id, principal) != sid
                or org.sessions.is_recovery_session(task_id, principal, sid)):
            raise ProjectionRefusal('sequential_task_session_required', 403)

    try:
        revalidate()
        if org.db.get_task(task_id) is None:
            raise ProjectionRefusal('task_not_found', 404)
        result = await selected_read(org, task_id=task_id, principal=principal,
            session_id=org.sessions.get_active(task_id, principal),
            change=None if change is None else change.encode('utf8'), revalidate=revalidate)
        return Response(_bytes(result.model_dump(mode='json')), media_type='application/json',
                        headers={'Cache-Control': 'no-store'})
    except ProjectionRefusal as exc:
        raise HTTPException(exc.status, detail={'code': exc.code}) from None
    except (SequentialRefusal, CodingEvidenceRefusal) as exc:
        raise HTTPException(403 if exc.code == 'sequential_authority_denied' else 409,
                            detail={'code': exc.code}) from None
    except (WorkflowAuthorityError, ProfileCoordinatorError, OSError):
        raise HTTPException(409, detail={'code': 'sequential_authority_unverified'}) from None
    except (PauseControlError, sqlite3.Error, ValueError, TypeError, KeyError, AttributeError,
            IndexError, StopIteration, UnicodeError, RecursionError):
        raise HTTPException(409, detail={'code': 'sequential_history_unverified'}) from None


@router.get('/workflows/sequential/repository-preflight', response_model=RepositoryChangeSubject,
            responses=_RESPONSES)
async def repository_preflight(request: Request, response: Response, org: OrgDep, session_id: Sid) -> object:
    return await _read(request, response, org, session_id)


@router.get('/workflows/sequential/final-manager', response_model=CodingFinalSelection, responses=_RESPONSES)
async def final_manager(request: Request, response: Response, org: OrgDep, session_id: Sid,
                        change: Annotated[str, Query(min_length=1, max_length=49152)]) -> object:
    return await _read(request, response, org, session_id, change)
