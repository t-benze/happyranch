"""Knowledge source, Distill and archive surfaces; existing bearer policy."""
from __future__ import annotations

from typing import Any, Callable, Coroutine

from fastapi import APIRouter, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from starlette.responses import Response

from runtime.daemon.auth import require_token
from runtime.daemon.routes._org_dep import OrgDep
from runtime.knowledge.sources import (
    RetiredSource, SourceError, SourceReference, capture_reference, capture_retired_source,
)
from runtime.knowledge.archives import ArchiveError
from runtime.knowledge.distill import DistillLifecycle
from runtime.knowledge.instill import InstillLifecycle
from runtime.knowledge.imports import ImportError
from runtime.knowledge.lifecycle_models import (
    ArchiveList, ArchiveSelection, DistillOperation, DistillSubmission, KnowledgeArchive,
    ImportEligibility, ImportHistory, ImportOperation, ImportRefusal, InstillConfirmation,
)


class KnowledgeRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handler = super().get_route_handler()

        async def validated(request: Request) -> Response:
            try:
                return await handler(request)
            except RequestValidationError as error:
                # Body-free transport errors; never echo confirmation content.
                if self.path.endswith('/agents/{recipient_id}/knowledge/imports') and request.method == 'POST':
                    empty = any(e['loc'] == ('body', 'item_ids') and e['type'] == 'too_short'
                                and e.get('input') == [] for e in error.errors())
                    code = 'knowledge_import_zero_selection' if empty else 'knowledge_import_invalid_confirmation'
                    raise HTTPException(status_code=422, detail={'code': code}) from None
                raise
        return validated


router = APIRouter(dependencies=[require_token()], route_class=KnowledgeRoute)


def _refuse(error: SourceError):
    code = error.code
    status = 404 if code == 'unknown_retired_source' else 422 if code == 'invalid_source_reference' else 409
    raise HTTPException(status_code=status, detail={'code': code}) from None


@router.get('/knowledge/retired-agents/{source_id}', response_model=RetiredSource)
def retired_source(slug: str, source_id: str, org: OrgDep) -> RetiredSource:
    try:
        return capture_retired_source(org.root, source_id)
    except SourceError as error:
        _refuse(error)


@router.get('/knowledge/retired-agents/{source_id}/sources/{reference_id}', response_model=SourceReference)
def retired_source_reference(slug: str, source_id: str, reference_id: str, org: OrgDep) -> SourceReference:
    try:
        return capture_reference(org.root, source_id, reference_id)
    except SourceError as error:
        _refuse(error)


def _archive_refuse(error: ArchiveError) -> None:
    code = str(error)
    status = 404 if code in {'knowledge_operation_not_found', 'knowledge_ready_archive_not_found'} else 409
    raise HTTPException(status_code=status, detail={'code': code}) from None


@router.post('/knowledge/distill', response_model=DistillOperation)
async def distill(slug: str, body: DistillSubmission, org: OrgDep, request: Request) -> DistillOperation:
    try:
        return await DistillLifecycle(org, request.app.state.daemon).submit(body)
    except SourceError as error:
        _refuse(error)
    except ArchiveError as error:
        _archive_refuse(error)


@router.get('/knowledge/distill/{operation_id}', response_model=DistillOperation)
async def distill_show(slug: str, operation_id: str, org: OrgDep, request: Request) -> DistillOperation:
    try:
        return await DistillLifecycle(org, request.app.state.daemon).show(operation_id)
    except ArchiveError as error:
        _archive_refuse(error)


@router.get('/knowledge/archives', response_model=ArchiveList)
async def archives(slug: str, org: OrgDep, request: Request) -> ArchiveList:
    lifecycle = DistillLifecycle(org, request.app.state.daemon)
    await lifecycle.reconcile_all()
    selections = []
    for archive in lifecycle.store.list_ready():
        manifest = archive['manifest']
        selections.append(ArchiveSelection(
            **{key: archive[key] for key in ('archive_id', 'version', 'operation_id', 'source_id', 'created_at')},
            **{key: manifest[key] for key in ('source_name', 'source_team', 'source_provider', 'cutoff',
                                             'processor_id', 'eligible_count', 'coverage')},
            item_count=len(archive['items']),
        ))
    return ArchiveList(archives=selections)


@router.get('/knowledge/archives/{archive_id}', response_model=KnowledgeArchive)
async def archive_show(slug: str, archive_id: str, org: OrgDep, request: Request) -> KnowledgeArchive:
    try:
        lifecycle = DistillLifecycle(org, request.app.state.daemon)
        await lifecycle.reconcile_all()
        return KnowledgeArchive.model_validate(lifecycle.store.get_archive(archive_id))
    except ArchiveError as error:
        _archive_refuse(error)


_IMPORT_RESPONSES = {
    401: {'description': 'Existing bearer authentication required'},
    404: {'model': ImportRefusal, 'description': 'Unknown or unavailable same-org identity'},
    409: {'model': ImportRefusal, 'description': 'Closed admission or selection refusal'},
    422: {'model': ImportRefusal, 'description': 'Invalid exact confirmation'},
}


def _import_refuse(error: ImportError) -> None:
    status = 404 if error.code in {
        'knowledge_import_not_found', 'knowledge_import_recipient_not_found',
        'knowledge_import_ready_same_org_archive_required',
        'knowledge_import_unknown_item', 'knowledge_import_recipient_not_active_authorized',
    } else 409
    detail = {'code': error.code, 'blockers': error.blockers}
    if error.code == 'knowledge_import_busy_finish_wait_then_retry':
        detail['message'] = 'finish/wait, then retry'
    raise HTTPException(status_code=status, detail=detail) from None


@router.post('/agents/{recipient_id}/knowledge/imports', response_model=ImportOperation, responses=_IMPORT_RESPONSES)
async def instill(slug: str, recipient_id: str, body: InstillConfirmation, org: OrgDep) -> ImportOperation:
    try:
        return await InstillLifecycle(org).submit(recipient_id, body)
    except ImportError as error:
        _import_refuse(error)


@router.get('/agents/{recipient_id}/knowledge/imports', response_model=ImportHistory, responses=_IMPORT_RESPONSES)
async def imports(slug: str, recipient_id: str, org: OrgDep) -> ImportHistory:
    try:
        return ImportHistory(operations=await InstillLifecycle(org).list(recipient_id))
    except ImportError as error:
        _import_refuse(error)


@router.get('/agents/{recipient_id}/knowledge/imports/eligibility/{archive_id}', response_model=ImportEligibility, responses=_IMPORT_RESPONSES)
async def import_eligibility(slug: str, recipient_id: str, archive_id: str, org: OrgDep) -> ImportEligibility:
    try:
        return await InstillLifecycle(org).eligibility(recipient_id, archive_id)
    except ImportError as error:
        _import_refuse(error)


@router.get('/knowledge/imports/{operation_id}', response_model=ImportOperation, responses=_IMPORT_RESPONSES)
async def import_show(slug: str, operation_id: str, org: OrgDep) -> ImportOperation:
    try:
        return await InstillLifecycle(org).show(operation_id)
    except ImportError as error:
        _import_refuse(error)


@router.post('/knowledge/imports/{operation_id}/retry', response_model=ImportOperation, responses=_IMPORT_RESPONSES)
async def import_retry(slug: str, operation_id: str, org: OrgDep) -> ImportOperation:
    try:
        return await InstillLifecycle(org).retry(operation_id)
    except ImportError as error:
        _import_refuse(error)
