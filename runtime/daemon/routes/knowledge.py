"""Knowledge source, Distill and archive surfaces; existing bearer policy."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from runtime.daemon.auth import require_token
from runtime.daemon.routes._org_dep import OrgDep
from runtime.knowledge.sources import (
    RetiredSource, SourceError, SourceReference, capture_reference, capture_retired_source,
)
from runtime.knowledge.archives import ArchiveError
from runtime.knowledge.distill import DistillLifecycle
from runtime.knowledge.lifecycle_models import (
    ArchiveList, ArchiveSelection, DistillOperation, DistillSubmission, KnowledgeArchive,
)

router = APIRouter(dependencies=[require_token()])


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
