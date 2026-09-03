"""Read-only, team-scoped authority-policy projection."""
from __future__ import annotations

import json
import logging
import hashlib
import re
import sqlite3
from typing import Literal

from fastapi import APIRouter, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from runtime.daemon.auth import require_token
from runtime.daemon.routes._org_dep import OrgDep
from runtime.orchestrator import prompt_loader
from runtime.orchestrator._paths import OrgPaths
from runtime.orchestrator.authority_policy_store import AuthorityPolicyStore
from runtime.models import AuthorityPolicyRelease
from runtime.orchestrator.authority_policy import (
    CONTINUE_ROUTINE_PHRASE,
    POLICY_BY_TEAM,
)

router = APIRouter(dependencies=[require_token()])
_logger = logging.getLogger(__name__)

_ELIGIBLE_AGENT = "engineering_manager"
_ELIGIBLE_TEAM = "engineering"
_SURFACE_UNAVAILABLE = {"code": "policy_surface_not_available"}
_STORE_UNAVAILABLE = {"code": "policy_store_unavailable"}
_ACTIVATION_GUARD = {
    "ready": False,
    "reason": "TASK-6335 production verification required",
}
_POLICY = POLICY_BY_TEAM[_ELIGIBLE_TEAM]
_KNOWN_CLAUSES = {clause.id: clause for clause in _POLICY.clauses}
_CANONICAL_CLAUSE_IDS = tuple(clause.id for clause in _POLICY.clauses)
_SECRET_SHAPE = re.compile(
    r"(?i)(?:authorization\s*:\s*bearer|bearer\s+[a-z0-9._-]{16,}|"
    r"(?:api[_-]?key|secret|password|token)\s*[:=]\s*\S{8,})"
)


class PolicyClauseInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=100)
    category: str = Field(min_length=1, max_length=100)
    condition: str = Field(min_length=1, max_length=4000)
    action: Literal["escalate_to_founder", "continue_same_root"]

    @field_validator("id", "category", "condition")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must be nonblank")
        return value


class CreatePolicyReleaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    based_on_release_id: str | None = Field(default=None, max_length=80)
    title: str = Field(min_length=1, max_length=200)
    normative_text: str = Field(min_length=1, max_length=20000)
    clauses: list[PolicyClauseInput] = Field(min_length=1, max_length=64)
    continuation_phrase: str = Field(max_length=200)
    request_id: str = Field(min_length=1, max_length=128)

    @field_validator("title", "normative_text", "request_id")
    @classmethod
    def bounded_nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must be nonblank")
        return value

    @model_validator(mode="after")
    def validate_policy_contract(self) -> "CreatePolicyReleaseRequest":
        if self.continuation_phrase != CONTINUE_ROUTINE_PHRASE:
            raise ValueError("continuation phrase must match the canonical phrase byte-for-byte")
        seen: set[str] = set()
        continuation_count = 0
        for clause in self.clauses:
            if clause.id in seen:
                raise ValueError("policy clause ids must be unique")
            seen.add(clause.id)
            expected = _KNOWN_CLAUSES.get(clause.id)
            if expected is None:
                raise ValueError("policy clause id is outside the closed vocabulary")
            if clause.category != expected.category or clause.action != expected.action:
                raise ValueError("policy clause category/action does not match its server contract")
            if clause.action == "continue_same_root":
                continuation_count += 1
        if seen != set(_KNOWN_CLAUSES):
            raise ValueError("all protected and mechanical policy clauses are required")
        if tuple(clause.id for clause in self.clauses) != _CANONICAL_CLAUSE_IDS:
            raise ValueError("policy clauses must use canonical server ordering")
        if continuation_count != 1:
            raise ValueError("exactly one continuation clause is required")
        material = self.model_dump_json()
        if len(material.encode("utf-8")) > 65536:
            raise ValueError("policy request exceeds the 65536-byte bound")
        if _SECRET_SHAPE.search(material):
            raise ValueError("policy request contains secret-shaped input")
        return self


class ActivatePolicyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    release_id: str = Field(min_length=1, max_length=80)
    expected_previous_epoch: int = Field(ge=0)
    request_id: str = Field(min_length=1, max_length=128)
    action: Literal["activate", "reactivate_rollback"] = "activate"
    acknowledge_shared_credential_attribution: Literal[True]


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
            "activation_guard": _ACTIVATION_GUARD,
            "bootstrap_template": _bootstrap_template(),
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


@router.post(
    "/agents/{agent_name}/team-escalation-policy/releases",
    status_code=status.HTTP_201_CREATED,
    responses={
        404: {"description": "Policy surface unavailable"},
        409: {"description": "Base, release, or idempotency conflict"},
        422: {"description": "Closed policy validation failed"},
        500: {"description": "Sanitized policy store failure"},
    },
)
def create_team_escalation_policy_release(
    slug: str,
    agent_name: str,
    body: CreatePolicyReleaseRequest,
    response: Response,
    org: OrgDep,
) -> dict:
    _require_eligible_manager(org, agent_name)
    try:
        store = AuthorityPolicyStore(org.db)
        clauses = [clause.model_dump(mode="json") for clause in body.clauses]
        clauses_json = json.dumps(
            clauses, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )
        release = AuthorityPolicyRelease(
            team=_ELIGIBLE_TEAM,
            policy_id=_POLICY.id,
            version=store.next_release_version(_ELIGIBLE_TEAM, _POLICY.id),
            title=body.title,
            normative_text=body.normative_text,
            clauses_json=clauses_json,
            continuation_phrase=body.continuation_phrase,
            based_on_release_id=body.based_on_release_id,
            actor_kind="shared_local_operator_credential",
        )
        request_json = json.dumps(
            body.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        request_digest = hashlib.sha256(request_json.encode("utf-8")).hexdigest()
        persisted = store.create_release_with_audit(
            release,
            request_id=body.request_id,
            request_digest=request_digest,
        )
        response.headers["ETag"] = f'"release-{persisted.policy_digest}"'
        return {
            "release": _project_release(persisted),
            "activated": False,
            "validation": {"canonical": True, "digest": persisted.policy_digest},
        }
    except HTTPException:
        raise
    except sqlite3.IntegrityError as exc:
        message = str(exc)
        if "idempotency" in message:
            detail = {"code": "idempotency_conflict"}
        elif "base" in message:
            detail = {"code": "base_release_changed"}
        else:
            detail = {"code": "release_conflict"}
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail) from None
    except Exception:
        _logger.exception("authority policy release creation unavailable for org=%s", slug)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=_STORE_UNAVAILABLE,
        ) from None


@router.post(
    "/agents/{agent_name}/team-escalation-policy/activations",
    responses={
        404: {"description": "Policy surface unavailable"},
        409: {"description": "CAS or idempotency conflict"},
        412: {"description": "Production verification guard is closed"},
        422: {"description": "Activation request validation failed"},
        500: {"description": "Sanitized policy store failure"},
    },
)
def activate_team_escalation_policy(
    slug: str,
    agent_name: str,
    body: ActivatePolicyRequest,
    org: OrgDep,
) -> dict:
    _require_eligible_manager(org, agent_name)
    # S3 deliberately returns before any store read/write so a guessed release
    # cannot become an oracle and no activation/audit residue is possible.
    raise HTTPException(
        status_code=status.HTTP_412_PRECONDITION_FAILED,
        detail={"code": "activation_guard_not_ready", **_ACTIVATION_GUARD},
    )


def _project_release(release: AuthorityPolicyRelease) -> dict:
    return {
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
    }


def _bootstrap_template() -> dict:
    """Project the one canonical server definition used by validation/runtime."""
    return {
        "title": _POLICY.title,
        "normative_text": _POLICY.normative_text,
        "clauses": [
            {
                "id": clause.id,
                "category": clause.category,
                "condition": clause.condition,
                "action": clause.action,
            }
            for clause in _POLICY.clauses
        ],
        "continuation_phrase": CONTINUE_ROUTINE_PHRASE,
    }
