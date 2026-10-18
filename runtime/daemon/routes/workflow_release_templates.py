"""Write-free disabled catalog and explicit Founder fixed-release import."""
from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from runtime.daemon.auth import _require_human, require_token
from runtime.daemon.routes._org_dep import OrgDep
from runtime.daemon.routes.workflow_coding_capabilities import _principal
from runtime.workflows._coding_release_history import COMMAND_LIMIT
from runtime.workflows.authority import WorkflowAuthorityError
from runtime.workflows.coding_release_import import CodingReleaseImportError, ImportCommand, ImportReceipt
from runtime.workflows.coding_setup import CodingSetupError
from runtime.workflows.profile_coordinator import ProfileCoordinatorError
from runtime.workflows.release_templates import (
    ReleaseTemplateError,
    ReleaseTemplateReceipt,
    coding_release_projection,
    read_coding_release,
)


router = APIRouter(dependencies=[require_token()])


@router.get(
    "/workflows/release-templates/coding/1",
    response_model=ReleaseTemplateReceipt,
    responses={
        401: {"description": "Existing bearer authentication required"},
        404: {"description": "Unknown org, release kind or version"},
        409: {"description": "No active runtime"},
        500: {"description": "resource_corrupt"},
        503: {"description": "resource_unavailable"},
    },
)
def get_coding_release(org: OrgDep) -> ReleaseTemplateReceipt:
    # Authentication and existing-org resolution complete before any package read.
    try:
        return ReleaseTemplateReceipt.model_validate(coding_release_projection(read_coding_release()))
    except ReleaseTemplateError as exc:
        raise HTTPException(status_code=503 if exc.code == "resource_unavailable" else 500,
                            detail={"code": exc.code}) from None


# The catalog above remains an authenticated, write-free GET. Import has its
# own closed Founder-only boundary and never accepts caller-selected policy.
_IMPORT_RESPONSES = {
    401: {'description': 'Existing bearer authentication required'},
    403: {'description': 'Existing Founder predicate required; machine import unavailable'},
    404: {'description': 'No imported release or unknown org'},
    409: {'description': 'Compatible complete J/current native admission required; operation conflict or already imported'},
    422: {'description': 'Closed canonical bounded command; no query parameters'},
    500: {'description': 'Corrupt retained import or packaged release'},
    503: {'description': 'Fixed package resource unavailable'},
}


def _import_error(exc: Exception) -> None:
    if isinstance(exc, ReleaseTemplateError):
        code, status = exc.code, 503 if exc.code == 'resource_unavailable' else 500
    elif isinstance(exc, (CodingReleaseImportError, CodingSetupError)):
        code = exc.code
        status = (403 if code == 'human_only' else 404 if code == 'coding_release_import_not_found'
                  else 422 if code == 'coding_release_import_invalid'
                  else 500 if code in ('coding_setup_whole_schema_mismatch', 'coding_setup_caller_transaction') else 409)
    elif isinstance(exc, (WorkflowAuthorityError, ProfileCoordinatorError)):
        code, status = 'coding_release_import_authority_unavailable', 409
    else:
        code, status = 'coding_release_import_history_invalid', 500
    raise HTTPException(status_code=status, detail={'code': code}) from None


def _no_import_query(request: Request) -> None:
    if request.scope.get('query_string', b''):
        raise CodingReleaseImportError('coding_release_import_invalid')


@router.post('/workflows/release-templates/coding/1/import', response_model=ImportReceipt,
    dependencies=[Depends(_require_human)], status_code=201,
    responses={200: {'description': 'Exact original-command replay'}, **_IMPORT_RESPONSES},
    openapi_extra={'requestBody': {'required': True, 'content': {
        'application/json': {'schema': ImportCommand.model_json_schema()}}}})
async def import_coding_release(request: Request, response: Response, org: OrgDep) -> dict:
    try:
        principal = _principal(request, org)
        _no_import_query(request)
        if request.headers.get('content-type', '').split(';', 1)[0].strip().lower() != 'application/json':
            raise CodingReleaseImportError('coding_release_import_invalid')
        chunks = bytearray()
        async for chunk in request.stream():
            if len(chunks) + len(chunk) > COMMAND_LIMIT:
                raise CodingReleaseImportError('coding_release_import_invalid')
            chunks.extend(chunk)
        principal.revalidate()
        receipt = await org.workflow_coding_release_import.import_release(principal=principal, raw=bytes(chunks))
        response.status_code = 200 if receipt['replayed'] else 201
        return receipt
    except (ValueError, WorkflowAuthorityError, ProfileCoordinatorError, sqlite3.Error) as exc:
        _import_error(exc)


@router.get('/workflows/release-templates/coding/1/import', response_model=ImportReceipt,
    dependencies=[Depends(_require_human)], responses=_IMPORT_RESPONSES)
async def get_coding_import(request: Request, org: OrgDep) -> dict:
    try:
        principal = _principal(request, org)
        _no_import_query(request)
        return await org.workflow_coding_release_import.get(principal=principal)
    except (ValueError, WorkflowAuthorityError, ProfileCoordinatorError, sqlite3.Error) as exc:
        _import_error(exc)
