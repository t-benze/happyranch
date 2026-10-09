"""Founder-facing team registry reads."""
from __future__ import annotations

from fastapi import APIRouter

from runtime.daemon.auth import require_token
from runtime.daemon.routes._org_dep import OrgDep

router = APIRouter(dependencies=[require_token()])


@router.get("/teams")
def list_teams(slug: str, org: OrgDep) -> dict:
    """Return all registered teams + their managers + workers, sorted by team name."""
    return {"teams": [org.teams.team_row(name) for name in org.teams.teams()]}
