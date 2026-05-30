"""Founder-facing team registry reads."""
from __future__ import annotations

from fastapi import APIRouter

from src.daemon.auth import require_token
from src.daemon.routes._org_dep import OrgDep

router = APIRouter(dependencies=[require_token()])


@router.get("/teams")
def list_teams(slug: str, org: OrgDep) -> dict:
    """Return all registered teams + their managers + workers.

    Sorted by team name. When ``org.teams`` is None (legacy no-runtime
    branch) returns an empty list — same shape as an empty registry.
    """
    if org.teams is None:
        return {"teams": []}
    rows = []
    for name in org.teams.teams():
        m = org.teams.manager_for_team(name)
        rows.append({
            "name": name,
            "manager": m.name,
            "workers": list(m.workers),
        })
    return {"teams": rows}
