"""Read-only, team-scoped authority-policy projection."""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, HTTPException, status

from runtime.daemon.auth import require_token
from runtime.daemon.routes._org_dep import OrgDep
from runtime.orchestrator import prompt_loader
from runtime.orchestrator._paths import OrgPaths
from runtime.orchestrator.authority_policy_store import AuthorityPolicyStore

router = APIRouter(dependencies=[require_token()])
_logger = logging.getLogger(__name__)

_ELIGIBLE_AGENT = "engineering_manager"
_ELIGIBLE_TEAM = "engineering"
_SURFACE_UNAVAILABLE = {"code": "policy_surface_not_available"}
_STORE_UNAVAILABLE = {"code": "policy_store_unavailable"}


def _require_eligible_manager(org: OrgDep, agent_name: str) -> None:
    """Resolve the live roster on every request without creating an oracle."""
    try:
        agent = prompt_loader.load_agent(OrgPaths(root=org.root), agent_name)
    except Exception:
        agent = None
    if not (
        agent is not None
        and agent.name == _ELIGIBLE_AGENT
        and agent.role == "manager"
        and agent.team == _ELIGIBLE_TEAM
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_SURFACE_UNAVAILABLE)


@router.get("/agents/{agent_name}/team-escalation-policy")
def get_team_escalation_policy(slug: str, agent_name: str, org: OrgDep) -> dict:
    _require_eligible_manager(org, agent_name)
    try:
        store = AuthorityPolicyStore(org.db)
        activation = store.get_current_activation(_ELIGIBLE_TEAM)
        result = {
            "team": _ELIGIBLE_TEAM,
            "target_manager": _ELIGIBLE_AGENT,
            "can_mutate": True,
            "activation_guard": {
                "ready": False,
                "reason": "TASK-6335 production verification required",
            },
        }
        if activation is None:
            result["bootstrap_required"] = True
            return result
        release = store.get_release(activation.release_id)
        if release is None:
            raise ValueError("active release is missing")
        result["active"] = {
            "activation_id": activation.id,
            "epoch": activation.epoch,
            "release": {
                "id": release.id,
                "policy_id": release.policy_id,
                "version": release.version,
                "title": release.title,
                "normative_text": release.normative_text,
                "clauses": json.loads(release.clauses_json),
                "continuation_phrase": release.continuation_phrase,
                "digest": release.policy_digest,
                "created_at": release.created_at.isoformat(),
                "actor_attribution": "shared local operator credential",
            },
            "action": activation.action,
            "created_at": activation.created_at.isoformat(),
            "actor_attribution": "shared local operator credential",
        }
        return result
    except HTTPException:
        raise
    except Exception:
        _logger.exception("authority policy projection unavailable for org=%s", slug)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=_STORE_UNAVAILABLE,
        ) from None
