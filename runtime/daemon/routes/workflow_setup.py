"""Founder-only setup status, independent of runnable org attachments."""
from __future__ import annotations

import sqlite3
import time

from fastapi import APIRouter, HTTPException, Request, Response
from starlette.concurrency import run_in_threadpool

from runtime.daemon.auth import require_token
from runtime.daemon.workflow_setup_status import WorkflowSetupStatus, read_setup_status
from runtime.infrastructure.memory_collection import CensusReadUnavailable
from runtime.runtime import daemon_home

router = APIRouter(dependencies=[require_token()])


def _running_job_ids(org: object | None) -> list[str] | None:
    """Read only through an existing A1 attachment; no extra opener or fence."""
    if org is None:
        return None
    try:
        with org.db._memory_collection_read_budget():
            fence = getattr(org.db, '_org_access_fence', None)
            if fence is None:
                return None
            fence.assert_held()
            rows = org.db._conn.execute("SELECT id FROM jobs WHERE status='running' LIMIT 1025").fetchall()
            fence.assert_held()
            return [row[0] for row in rows]
    except (CensusReadUnavailable, OSError, ValueError, sqlite3.Error):
        return None


@router.get('/runtime/workflow-setup/orgs/{slug}', response_model=WorkflowSetupStatus,
            responses={401: {'description': 'Founder bearer required'},
                       404: {'description': 'Unknown org; no inventory disclosure'},
                       409: {'description': 'No active runtime'},
                       422: {'description': 'Unexpected query or body'}})
async def setup_status(slug: str, request: Request, response: Response) -> WorkflowSetupStatus:
    response.headers['Cache-Control'] = 'no-store'
    if request.query_params:
        raise HTTPException(422, detail={'code': 'workflow_setup_request_invalid'})
    async for chunk in request.stream():
        if chunk:
            raise HTTPException(422, detail={'code': 'workflow_setup_request_invalid'})
    state = request.app.state.daemon
    runtime = state.runtime
    if runtime is None:
        raise HTTPException(409, detail={'code': 'no_active_runtime'})
    deadline = time.monotonic() + 1
    root = await run_in_threadpool(state.workflow_setup_root, slug)
    if root is None:
        raise HTTPException(404, detail={'code': 'unknown_org'})
    value = await run_in_threadpool(read_setup_status, daemon_home(), runtime.root, slug, deadline=deadline)
    if state.runtime is not runtime:
        raise HTTPException(409, detail={'code': 'workflow_setup_runtime_changed'})
    return value
