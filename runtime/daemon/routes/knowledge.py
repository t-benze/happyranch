"""Retained retired-agent reads; unchanged bearer/native/remote policy."""
from fastapi import APIRouter, HTTPException

from runtime.daemon.auth import require_token
from runtime.daemon.routes._org_dep import OrgDep
from runtime.knowledge.sources import (
    RetiredSource, SourceError, SourceReference, capture_reference, capture_retired_source,
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
