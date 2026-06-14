"""Org-shared artifacts routes. Flat blob store, atomic writes, audited puts.

Auth: same bearer token as every other org-scoped route. No per-agent
authorization — any agent that can hit the daemon can put/list/get.
"""
from __future__ import annotations

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse

from runtime.daemon.auth import require_token
from runtime.daemon.org_state import OrgState
from runtime.daemon.routes._org_dep import OrgDep
from runtime.infrastructure.artifact_store import (
    MAX_ARTIFACT_BYTES,
    ArtifactNotFound,
    ArtifactStore,
    InvalidArtifactName,
)
from runtime.infrastructure.audit_logger import AuditLogger
from runtime.orchestrator._paths import OrgPaths

# Token-required for every endpoint — matches kb/tasks/talks/scripts routers.
router = APIRouter(dependencies=[require_token()])


def _store(org: OrgState) -> ArtifactStore:
    return ArtifactStore(OrgPaths(org.root).artifacts_dir)


@router.post("/artifacts")
async def put_artifact(
    slug: str,
    org: OrgDep,
    file: UploadFile = File(...),
    name: str | None = Query(None),
    agent: str = Query(...),
) -> dict:
    content = await file.read(MAX_ARTIFACT_BYTES + 1)
    if len(content) > MAX_ARTIFACT_BYTES:
        raise HTTPException(
            status_code=413,
            detail={
                "code": "artifact_too_large",
                "max_bytes": MAX_ARTIFACT_BYTES,
                "size_bytes": len(content),
            },
        )
    effective_name = name or file.filename or ""
    try:
        info = _store(org).put(effective_name, content)
    except InvalidArtifactName as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": "invalid_artifact_name", "name": effective_name, "message": str(exc)},
        ) from exc

    # Construct AuditLogger on demand — matches the routes/talks.py pattern.
    AuditLogger(org.db).log_artifact_put(
        name=info.name,
        size_bytes=info.size_bytes,
        agent=agent,
    )

    return {
        "name": info.name,
        "size_bytes": info.size_bytes,
        "modified_at": info.modified_at,
    }


@router.get("/artifacts")
async def list_artifacts(
    slug: str,
    org: OrgDep,
    prefix: str = Query(""),
) -> dict:
    return {
        "artifacts": [
            {"name": a.name, "size_bytes": a.size_bytes, "modified_at": a.modified_at}
            for a in _store(org).list_artifacts(prefix=prefix)
        ],
    }


@router.get("/artifacts/{name:path}")
async def get_artifact(slug: str, name: str, org: OrgDep) -> FileResponse:
    try:
        path = _store(org).path_for(name)
    except InvalidArtifactName as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": "invalid_artifact_name", "name": name, "message": str(exc)},
        ) from exc
    if not path.exists():
        raise HTTPException(
            status_code=404,
            detail={"code": "artifact_not_found", "name": name},
        )
    return FileResponse(path=str(path), filename=name)


@router.delete("/artifacts/{name:path}")
async def delete_artifact(
    slug: str,
    name: str,
    org: OrgDep,
    agent: str = Query(...),
) -> dict:
    try:
        _store(org).delete(name)
    except InvalidArtifactName as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": "invalid_artifact_name", "name": name, "message": str(exc)},
        ) from exc
    except ArtifactNotFound as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": "artifact_not_found", "name": name},
        ) from exc

    # Construct AuditLogger on demand — matches put_artifact.
    AuditLogger(org.db).log_artifact_delete(name=name, agent=agent)

    return {"name": name, "deleted": True}
