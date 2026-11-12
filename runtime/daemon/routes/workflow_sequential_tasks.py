"""Own selected sequential task transport. No bearer/browser or effect executor."""
from __future__ import annotations

import sqlite3
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request, Response
from starlette.concurrency import run_in_threadpool

from runtime.daemon.routes._org_dep import OrgDep
from runtime.infrastructure.task_pause_controls import PauseControlError
from runtime.workflows.authority import WorkflowAuthorityError
from runtime.workflows.profile_coordinator import ProfileCoordinatorError
from runtime.workflows.sequential_coding_final_records import CodingFinalSelection
from runtime.workflows.sequential_coding_policy import CodingEvidenceRefusal
from runtime.workflows.sequential_dispatch import _cancelled, _discover_result, _native_task
from runtime.workflows.sequential_engine import SequentialRefusal
from runtime.workflows.sequential_history import _ReadBudget, _bytes, _sql_budget
from runtime.workflows.sequential_repository_command import read_repository_command
from runtime.workflows.sequential_repository_preflight import SequentialRepositoryPreflight
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


def _repository_owner(org: OrgDep, task_id: str, principal: str, sid: str, *, allow_held: bool) -> None:
    """SELECT-only pre-filter; the service still owns authoritative admission."""
    dispatcher = org.workflow_sequential_dispatch
    budget = _ReadBudget()
    with dispatcher._reader() as conn, _sql_budget(conn, budget):
        run, visit, compiled = dispatcher._entry(conn, task_id, budget=budget)
        conn.set_progress_handler(budget.progress, 1000)
        task = _native_task(conn, task_id)
        store = getattr(org.db, '_task_pause_store', None)
        live = []
        for invocation in (() if store is None else store._live.values()):
            budget.step()
            if (invocation.owner == 'sequential' and invocation.workflow_dispatcher is dispatcher
                    and (invocation.task.id, invocation.agent, invocation.session_id) == (task_id, principal, sid)):
                live.append(invocation)
        budget.work(len(compiled.definition.nodes))
        node = next(item for item in compiled.definition.nodes if item.id == visit['node_id'])
        if (len(live) != 1 or live[0].orch is not org.orchestrator
                or live[0].orch._paths.root != org.root
                or run['state'] not in (('active', 'held') if allow_held else ('active',))
                or run['active_visit_id'] != visit['id'] or visit['selected'] != 1
                or visit['state'] != ('held' if run['state'] == 'held' else 'running')
                or (visit['principal'], visit['session_id']) != (principal, sid)
                or task['status'] != 'in_progress' or visit['result_id'] is not None
                or _discover_result(conn, visit) is not None or _cancelled(conn, run, visit)
                or node.output_contract.type != 'code-change@1'):
            raise SequentialRefusal('sequential_repository_session_unavailable')
        budget.check()


async def _read(request: Request, response: Response, org: OrgDep, sid: str,
                change: str | None = None, *, repository_operation: str | None = None) -> object:
    response.headers['Cache-Control'] = 'no-store'
    if ('authorization' in request.headers or 'origin' in request.headers
            or request.headers.get('x-happyranch-surface') != 'cli'):
        raise HTTPException(403, detail={'code': 'sequential_task_session_required'})
    expected = [('session_id', sid)] + ([] if change is None else [('change', change)])
    if list(request.query_params.multi_items()) != expected:
        raise HTTPException(422, detail={'code': 'sequential_task_read_invalid'})
    raw = bytearray()
    async for chunk in request.stream():
        if chunk and repository_operation is None:
            raise HTTPException(422, detail={'code': 'sequential_task_read_invalid'})
        if len(raw) + len(chunk) > 49152:
            raise HTTPException(422, detail={'code': 'sequential_repository_command_invalid'})
        raw.extend(chunk)
    declaration = None
    if repository_operation is not None:
        try:
            declaration = read_repository_command(bytes(raw))
        except (ValueError, TypeError, RecursionError):
            raise HTTPException(422, detail={'code': 'sequential_repository_command_invalid'}) from None
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
        if repository_operation is not None:
            def invoke() -> bytes:
                revalidate()
                _repository_owner(org, task_id, principal, sid, allow_held=repository_operation == 'bind')
                # Selection can change after this read; the unchanged service
                # rechecks it under its existing transient publication lease.
                service = SequentialRepositoryPreflight(org)
                operation = service.bind if repository_operation == 'bind' else service.resolve
                result = operation(task_id=task_id, principal=principal, session_id=sid, request=declaration)
                revalidate()
                return result

            # The existing service schedules its short selected writers on the
            # daemon loop; local Git observations must run outside that loop.
            result = await run_in_threadpool(invoke)
            return Response(result, media_type='application/json', headers={'Cache-Control': 'no-store'})
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


_REPOSITORY_RESPONSES = {**_RESPONSES,
    200: {'description': 'Existing preflight receipt or durable repository Held; resolve never binds'},
    422: {'description': 'Exactly one session query and canonical closed format/repo_key/base/scope command, at most 48KiB'},
}


@router.post('/workflows/sequential/repository-resolve', responses=_REPOSITORY_RESPONSES)
async def repository_resolve(request: Request, response: Response, org: OrgDep, session_id: Sid) -> object:
    return await _read(request, response, org, session_id, repository_operation='resolve')


@router.post('/workflows/sequential/repository-bind', responses=_REPOSITORY_RESPONSES)
async def repository_bind(request: Request, response: Response, org: OrgDep, session_id: Sid) -> object:
    return await _read(request, response, org, session_id, repository_operation='bind')
