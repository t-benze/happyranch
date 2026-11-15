"""Founder-only setup status, independent of runnable org attachments."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response
from starlette.concurrency import run_in_threadpool

from runtime.daemon.auth import require_token
from runtime.daemon.workflow_setup_status import WorkflowSetupStatus, read_setup_status
from runtime.runtime import daemon_home

router = APIRouter(dependencies=[require_token()])


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
    root = await run_in_threadpool(state.workflow_setup_root, slug)
    if root is None:
        raise HTTPException(404, detail={'code': 'unknown_org'})
    value = await run_in_threadpool(read_setup_status, daemon_home(), runtime.root, slug)
    if state.runtime is not runtime:
        raise HTTPException(409, detail={'code': 'workflow_setup_runtime_changed'})
    return value
