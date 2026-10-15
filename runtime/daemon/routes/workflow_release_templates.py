"""Authenticated existing-org GET for the always-disabled coding release catalog."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from runtime.daemon.auth import require_token
from runtime.daemon.routes._org_dep import OrgDep
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
