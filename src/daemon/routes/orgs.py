"""Cross-org endpoints: list, init, unload."""
from __future__ import annotations

import re
import shutil
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel

from src.daemon.auth import require_token
from src.daemon.org_state import OrgState
from src.daemon.state import DaemonState

router = APIRouter(dependencies=[require_token()])

_SLUG_RE = re.compile(r"^[a-z0-9-]{1,40}$")
_RESERVED = frozenset({"_pending", "_archive"})


class InitOrgBody(BaseModel):
    slug: str
    from_example: str | None = None  # path to examples/orgs/<name> tree


def _require_runtime(state: DaemonState) -> None:
    if state.is_idle:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "no_active_runtime"},
        )


def _seed_skeleton(org_root: Path, *, from_example: Path | None) -> None:
    org_root.mkdir(parents=True, exist_ok=False)
    if from_example is not None:
        # Copy examples/orgs/<name>/org/ verbatim into <org_root>/org/.
        src_org = from_example / "org"
        if not src_org.is_dir():
            raise HTTPException(
                status_code=400,
                detail={"code": "example_missing_org_dir", "path": str(src_org)},
            )
        shutil.copytree(src_org, org_root / "org")
    else:
        (org_root / "org").mkdir()
        (org_root / "org" / "agents").mkdir()
        (org_root / "org" / "agents" / "_pending").mkdir()
        (org_root / "org" / "teams.yaml").write_text("teams: {}\n")
    (org_root / "workspaces").mkdir(exist_ok=True)
    (org_root / "kb").mkdir(exist_ok=True)
    (org_root / "talks").mkdir(exist_ok=True)


@router.get("/orgs")
async def list_orgs(request: Request) -> dict:
    state: DaemonState = request.app.state.daemon
    _require_runtime(state)
    return {
        "orgs": [
            {"slug": slug, "root": str(org.root)}
            for slug, org in sorted(state.orgs.items())
        ],
    }


@router.post("/orgs")
async def init_org(body: InitOrgBody, request: Request) -> dict:
    state: DaemonState = request.app.state.daemon
    _require_runtime(state)
    if not _SLUG_RE.match(body.slug) or body.slug in _RESERVED:
        raise HTTPException(
            status_code=400,
            detail={"code": "invalid_slug", "slug": body.slug},
        )
    if body.slug in state.orgs:
        raise HTTPException(
            status_code=409,
            detail={"code": "org_exists", "slug": body.slug},
        )
    org_root = state.runtime.orgs_dir / body.slug
    if org_root.exists():
        raise HTTPException(
            status_code=409,
            detail={"code": "org_dir_exists", "slug": body.slug, "path": str(org_root)},
        )
    from_example = Path(body.from_example).expanduser() if body.from_example else None
    _seed_skeleton(org_root, from_example=from_example)
    org = await state.add_org(body.slug)
    return {"slug": org.slug, "root": str(org.root)}


@router.delete("/orgs/{slug}")
async def unload_org(slug: str, request: Request) -> dict:
    state: DaemonState = request.app.state.daemon
    _require_runtime(state)
    if slug not in state.orgs:
        raise HTTPException(
            status_code=404,
            detail={"code": "unknown_org", "slug": slug},
        )
    await state.remove_org(slug)
    return {"slug": slug, "unloaded": True}
