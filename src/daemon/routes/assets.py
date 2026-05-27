"""Org-shared assets routes. Flat blob store, atomic writes, audited puts.

Auth: same bearer token as every other org-scoped route. No per-agent
authorization — any agent that can hit the daemon can put/list/get.
"""
from __future__ import annotations

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse

from src.daemon.auth import require_token
from src.daemon.org_state import OrgState
from src.daemon.routes._org_dep import OrgDep
from src.infrastructure.asset_store import (
    MAX_ASSET_BYTES,
    AssetStore,
    InvalidAssetName,
)
from src.infrastructure.audit_logger import AuditLogger
from src.orchestrator._paths import OrgPaths

# Token-required for every endpoint — matches kb/tasks/talks/scripts routers.
router = APIRouter(dependencies=[require_token()])


def _store(org: OrgState) -> AssetStore:
    return AssetStore(OrgPaths(org.root).assets_dir)


@router.post("/assets")
async def put_asset(
    slug: str,
    org: OrgDep,
    file: UploadFile = File(...),
    name: str | None = Query(None),
    agent: str = Query(...),
) -> dict:
    content = await file.read(MAX_ASSET_BYTES + 1)
    if len(content) > MAX_ASSET_BYTES:
        raise HTTPException(
            status_code=413,
            detail={
                "code": "asset_too_large",
                "max_bytes": MAX_ASSET_BYTES,
                "size_bytes": len(content),
            },
        )
    effective_name = name or file.filename or ""
    try:
        info = _store(org).put(effective_name, content)
    except InvalidAssetName as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": "invalid_asset_name", "name": effective_name, "message": str(exc)},
        ) from exc

    # Construct AuditLogger on demand — matches the routes/talks.py pattern.
    AuditLogger(org.db).log_asset_put(
        name=info.name,
        size_bytes=info.size_bytes,
        agent=agent,
    )

    return {
        "name": info.name,
        "size_bytes": info.size_bytes,
        "modified_at": info.modified_at,
    }


@router.get("/assets")
async def list_assets(slug: str, org: OrgDep) -> dict:
    return {
        "assets": [
            {"name": a.name, "size_bytes": a.size_bytes, "modified_at": a.modified_at}
            for a in _store(org).list_assets()
        ],
    }


@router.get("/assets/{name}")
async def get_asset(slug: str, name: str, org: OrgDep) -> FileResponse:
    try:
        path = _store(org).path_for(name)
    except InvalidAssetName as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": "invalid_asset_name", "name": name, "message": str(exc)},
        ) from exc
    if not path.exists():
        raise HTTPException(
            status_code=404,
            detail={"code": "asset_not_found", "name": name},
        )
    return FileResponse(path=str(path), filename=name)
