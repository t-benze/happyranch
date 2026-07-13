"""Cross-org endpoints: list, init, unload."""
from __future__ import annotations

import logging
import re
import shutil
import sqlite3
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel

from runtime.daemon.auth import require_token
from runtime.daemon.org_state import OrgState
from runtime.daemon.state import DaemonState
from runtime.orchestrator._paths import OrgPaths

logger = logging.getLogger(__name__)

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
    OrgPaths(org_root).artifacts_dir.mkdir(exist_ok=True)


@router.get("/orgs")
async def list_orgs(request: Request) -> dict:
    state: DaemonState = request.app.state.daemon
    _require_runtime(state)
    return {
        "orgs": [
            {"slug": slug, "root": str(org.root)}
            for slug, org in sorted(state.orgs.items())
        ],
        "broken": [
            {"slug": slug, "error": error}
            for slug, error in sorted(state.broken_orgs.items())
        ],
    }


def _is_reclaimable_partial(org_root: Path) -> bool:
    """Return True ONLY when `org_root` is a pristine runtime-seeded skeleton
    with zero real org data — safe to `rmtree` without dataloss risk.

    DEFAULT TO FALSE ON ANY DOUBT.
    """
    if not org_root.is_dir():
        return False
    # 1) happyranch.db must be empty across ALL durable data tables.
    #    A single row in any table (audit_log, task_results, jobs, threads,
    #    kb_views, dreams, memory, etc.) means the dir has real data and
    #    must NEVER be auto-removed. Default-to-protect: any read error or
    #    any non-zero row count -> non-reclaimable.
    db_path = org_root / "happyranch.db"
    if db_path.exists():
        try:
            conn = sqlite3.connect(str(db_path))
            tables = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
            for (table_name,) in tables:
                # sqlite_master returns only user-defined tables; internal
                # tables (sqlite_sequence, etc.) are excluded automatically.
                # Every durable-data table must have zero rows.
                cursor = conn.execute(
                    f'SELECT COUNT(*) FROM "{table_name}"'
                )
                if cursor.fetchone()[0] > 0:
                    conn.close()
                    return False
            conn.close()
        except Exception:
            # Can't read the DB — assume it holds data, do NOT touch.
            return False
    # 2) agents/ dir holds nothing beyond the seeded _pending skeleton.
    agents_dir = org_root / "org" / "agents"
    if agents_dir.is_dir():
        entries = {p.name for p in agents_dir.iterdir()}
        extra = entries - {"_pending", ".DS_Store"}
        if extra:
            return False
    # 3) kb/ and artifacts/ are empty (no user content).
    for sub in ["kb", "artifacts"]:
        p = org_root / sub
        if p.is_dir():
            entries = [e for e in p.iterdir() if e.name != ".DS_Store"]
            if entries:
                return False
    # 4) workspaces/ is empty (no agent workspace data).
    wsp = org_root / "workspaces"
    if wsp.is_dir():
        entries = [e for e in wsp.iterdir() if e.name != ".DS_Store"]
        if entries:
            return False
    return True


@router.post("/orgs")
async def init_org(body: InitOrgBody, request: Request) -> dict:
    state: DaemonState = request.app.state.daemon
    _require_runtime(state)
    if not _SLUG_RE.match(body.slug) or body.slug in _RESERVED:
        raise HTTPException(
            status_code=400,
            detail={"code": "invalid_slug", "slug": body.slug},
        )
    # Healthy loaded org → org_exists (fast path, no dir probe).
    if body.slug in state.orgs:
        raise HTTPException(
            status_code=409,
            detail={"code": "org_exists", "slug": body.slug},
        )
    org_root = state.runtime.orgs_dir / body.slug
    if org_root.exists():
        # The org is not loaded. Two paths:
        #  a) Pristine skeleton (no real data) → reclaim silently.
        #  b) Data-bearing dir → protective 409, do NOT touch.
        if _is_reclaimable_partial(org_root):
            logger.warning(
                "init_org: reclaiming stale pristine skeleton for slug %r",
                body.slug,
            )
            shutil.rmtree(org_root)
        else:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "org_dir_has_data",
                    "slug": body.slug,
                    "path": str(org_root),
                    "message": (
                        "A directory already exists for slug '%s' and contains "
                        "data that cannot be safely removed. The org may be "
                        "listed under 'broken' on GET /orgs. Remove the "
                        "directory manually or fix the org configuration "
                        "before recreating." % body.slug
                    ),
                },
            )
    from_example = Path(body.from_example).expanduser() if body.from_example else None
    try:
        _seed_skeleton(org_root, from_example=from_example)
    except Exception:
        # _seed_skeleton uses exist_ok=False, so the dir didn't exist before
        # this call. rollback anything we just created.
        if org_root.exists():
            shutil.rmtree(org_root)
        raise
    try:
        org = await state.add_org(body.slug)
    except Exception:
        # add_org failed AFTER we seeded the skeleton. Roll back the skeleton
        # (it was fresh — we 409'd on org_root.exists() above and mkdir uses
        # exist_ok=False, so we are only removing what THIS call created).
        if org_root.exists():
            shutil.rmtree(org_root)
        raise
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
    # Refuse to unload an org with non-terminal tasks: doing so would let the
    # dispatcher silently drop their re-enqueues as 'unknown org', and any
    # in-flight agent callback would hit a 404 because OrgDep can no longer
    # resolve the slug. blocked-escalated waits forever on a founder; do not
    # strand it.
    in_flight = state.orgs[slug].db.get_nonterminal_task_ids()
    if in_flight:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "active_tasks_in_flight",
                "slug": slug,
                "task_ids": in_flight,
            },
        )
    await state.remove_org(slug)
    return {"slug": slug, "unloaded": True}
