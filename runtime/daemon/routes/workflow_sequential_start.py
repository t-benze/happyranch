"""Read-only own ordinary manager start context; no Founder or start audience."""
from __future__ import annotations

from collections.abc import Callable, Coroutine
import sqlite3
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

from runtime.daemon.routes._org_dep import OrgDep
from runtime.infrastructure.task_pause_controls import PauseControlError
from runtime.workflows.authority import WorkflowAuthorityError
from runtime.workflows.profile_coordinator import ProfileCoordinatorError
from runtime.workflows.sequential_coding_policy import CodingEvidenceRefusal
from runtime.workflows.sequential_engine import SequentialRefusal
from runtime.workflows.sequential_history import _bytes
from runtime.workflows.sequential_release import SequentialReleaseUnavailable
from runtime.workflows.sequential_views import ProjectionRefusal, StartContext, read_start_context


class _NoStoreRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handler = super().get_route_handler()

        async def no_store(request: Request) -> Response:
            try:
                response = await handler(request)
            except HTTPException as exc:
                # The SPA's global HTTPException projection does not preserve
                # exception headers. Keep this narrow route's refusal uncached.
                response = JSONResponse(status_code=exc.status_code,
                    content={'detail': exc.detail}, headers=exc.headers)
            except RequestValidationError:
                response = JSONResponse(status_code=422, content={
                    'detail': {'code': 'sequential_start_context_invalid'}})
            response.headers['Cache-Control'] = 'no-store'
            return response

        return no_store


router = APIRouter(route_class=_NoStoreRoute)
Sid = Annotated[str, Query(min_length=1, max_length=128)]
Selector = Annotated[str | None, Query(min_length=1, max_length=160)]


@router.get('/workflows/sequential/start-context', response_model=StartContext, responses={
    403: {'description': 'Own current ordinary executable team-manager session required; no bearer/browser/foreign/thread/recovery'},
    404: {'description': 'Registry-bound native task absent'},
    409: {'description': 'Finite unavailable definition, authority, storage or read-budget cause; no start effect'},
    422: {'description': 'Exactly one session_id, optional exact identity@version definition, empty GET body'},
})
async def start_context(request: Request, org: OrgDep, session_id: Sid,
                        definition: Selector = None) -> Response:
    if ('authorization' in request.headers or 'origin' in request.headers
            or request.headers.get('x-happyranch-surface') != 'cli'):
        raise HTTPException(403, detail={'code': 'sequential_task_session_required'})
    expected = [('session_id', session_id)] + ([] if definition is None else [('definition', definition)])
    if sorted(request.query_params.multi_items()) != sorted(expected):
        raise HTTPException(422, detail={'code': 'sequential_start_context_invalid'})
    async for chunk in request.stream():
        if chunk:
            raise HTTPException(422, detail={'code': 'sequential_start_context_invalid'})
    context = org.sessions.get_context_by_session(session_id)
    if context is None or context[0] != org.slug:
        raise HTTPException(403, detail={'code': 'sequential_task_session_required'})
    _, task_id, principal = context

    def revalidate() -> None:
        daemon = request.app.state.daemon
        if (daemon.is_idle or daemon.orgs.get(org.slug) is not org
                or org.sessions.get_context_by_session(session_id) != context
                or org.sessions.get_active(task_id, principal) != session_id
                or org.sessions.is_recovery_session(task_id, principal, session_id)):
            raise ProjectionRefusal('sequential_task_session_required', 403)

    try:
        value = await read_start_context(org, task_id=task_id, principal=principal,
            session_id=session_id, selector=definition, revalidate=revalidate)
        return Response(_bytes(value.model_dump(mode='json')), media_type='application/json')
    except ProjectionRefusal as exc:
        raise HTTPException(exc.status, detail={'code': exc.code}) from None
    except SequentialRefusal as exc:
        status = 404 if exc.code == 'task_not_found' else 403 if exc.code in (
            'sequential_task_session_required', 'sequential_authority_denied', 'role_unavailable', 'role_ineligible') else 409
        raise HTTPException(status, detail={'code': exc.code}) from None
    except SequentialReleaseUnavailable as exc:
        raise HTTPException(409, detail={'code': exc.code}) from None
    except (WorkflowAuthorityError, ProfileCoordinatorError, CodingEvidenceRefusal, OSError):
        raise HTTPException(409, detail={'code': 'sequential_authority_unverified'}) from None
    except (PauseControlError, sqlite3.Error, ValueError, TypeError, KeyError, AttributeError,
            IndexError, StopIteration, UnicodeError, RecursionError, TimeoutError):
        raise HTTPException(409, detail={'code': 'sequential_history_unverified'}) from None
