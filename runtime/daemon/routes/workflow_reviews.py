"""Current selected-recipient reads of unsigned review requests."""
from __future__ import annotations

from fastapi import APIRouter, Query, Request

from runtime.daemon.auth import _require_human, optional_bearer
from runtime.daemon.routes._org_dep import OrgDep
from runtime.daemon.routes.workflow_submissions import ERRORS, agent_transport, fail, query_values, raise_error

router = APIRouter()


@router.get('/workflows/reviews/requests/{request_id}')
async def get_review_request(org: OrgDep, request_id: str, request: Request,
                              session_id: str | None = Query(default=None), proof_kind: str | None = Query(default=None),
                              has_bearer: bool = optional_bearer()) -> dict:
    values = query_values(request, {'session_id', 'proof_kind'})
    if set(values) == {'proof_kind'} and proof_kind == 'human':
        _require_human(has_bearer)
        human = True
    elif set(values) == {'session_id'}:
        agent_transport(request, has_bearer)
        human = False
    else:
        fail('workflow_author_handoff_invalid_request', 422)
    try:
        return await org.workflow_submissions.get_request(request_id=request_id, session_id=session_id, human=human)
    except ERRORS as exc:
        raise_error(exc)
