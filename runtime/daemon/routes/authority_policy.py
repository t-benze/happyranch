"""Read-only, team-scoped authority-policy projection."""
from __future__ import annotations

import json
import logging
import hashlib
import re
import sqlite3
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from runtime.daemon.auth import require_token
from runtime.daemon.routes._org_dep import OrgDep
from runtime.orchestrator import prompt_loader
from runtime.orchestrator._paths import OrgPaths
from runtime.orchestrator.authority_policy_store import AuthorityPolicyStore
from runtime.orchestrator.authority_activation_readiness import activation_readiness
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


def _manager_surface(org: OrgDep, agent_name: str) -> tuple[str, str]:
    """Reusable role seam with an explicit current Engineering allowlist."""
    _require_eligible_manager(org, agent_name)
    return _ELIGIBLE_TEAM, _ELIGIBLE_AGENT


@router.get("/agents/{agent_name}/team-escalation-policy")
def get_team_escalation_policy(slug: str, agent_name: str, org: OrgDep) -> dict:
    team, target_manager = _manager_surface(org, agent_name)
    try:
        store = AuthorityPolicyStore(org.db)
        activation = store.get_current_activation(team)
        result = {
            "team": team,
            "target_manager": target_manager,
            "can_mutate": True,
            "activation_guard": activation_readiness(),
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


@router.get("/agents/{agent_name}/team-escalation-policy/history")
def get_team_escalation_policy_history(
    slug: str, agent_name: str, org: OrgDep,
    cursor: int = Query(default=0, ge=0), limit: int = Query(default=20, ge=1, le=50),
) -> dict:
    team, _ = _manager_surface(org, agent_name)
    try:
        items, next_cursor = AuthorityPolicyStore(org.db).list_history(
            team, cursor=cursor, limit=limit,
        )
        return {"items": [{
            "release_id": row["release_id"], "policy_id": row["policy_id"],
            "version": row["version"], "policy_digest": row["policy_digest"],
            "release_created_at": row["release_created_at"],
            "activation": None if row["activation_id"] is None else {
                "id": row["activation_id"], "epoch": row["epoch"],
                "action": row["action"], "digest": row["activation_digest"],
                "created_at": row["activation_created_at"],
            },
            "actor_attribution": "shared local operator credential",
        } for row in items], "next_cursor": next_cursor}
    except Exception:
        _logger.exception("authority policy history unavailable for org=%s", slug)
        raise HTTPException(status_code=500, detail=_STORE_UNAVAILABLE) from None


@router.get("/agents/{agent_name}/team-escalation-policy/outcomes")
def get_team_escalation_policy_outcomes(
    slug: str, agent_name: str, org: OrgDep,
    cursor: int = Query(default=0, ge=0), limit: int = Query(default=20, ge=1, le=50),
) -> dict:
    team, _ = _manager_surface(org, agent_name)
    try:
        items, next_cursor = AuthorityPolicyStore(org.db).list_outcomes(
            team, cursor=cursor, limit=limit,
        )
        projected = []
        for row in items:
            authority_audit = org.db.list_authority_audit(row["id"])
            hook_rows = [item for item in org.db.get_audit_logs(row["root_task_id"])
                         if item["action"] == "authority_hook"
                         and (item.get("payload") or {}).get("candidate_id") == row["id"]]
            task = org.db.get_task(row["root_task_id"])
            complete = bool(row["release_id"] and row["activation_id"]
                            and row["evaluation_id"] and authority_audit and hook_rows)
            projected.append({
                "candidate_id": row["id"], "root_task_id": row["root_task_id"],
                "manager_session_id": row["manager_session_id"],
                "causal_event_id": row["causal_event_id"],
                "release_id": row["release_id"], "activation_id": row["activation_id"],
                "activation_epoch": row["activation_epoch"],
                "policy_version": row["policy_version"], "policy_digest": row["policy_digest"],
                "prompt_id": row["prompt_id"], "prompt_version": row["prompt_version"],
                "prompt_digest": row["prompt_digest"], "provider_id": row["provider_id"],
                "executor_kind": row["executor_kind"], "model_id": row["model_id"],
                "model_version": row["model_version"], "model_digest": row["model_digest"],
                "disposition": row["evaluation_disposition"],
                "disposition_code": row["disposition_code"],
                "evaluation_created_at": row["evaluation_created_at"],
                "terminal_hook_outcome": None if not hook_rows else hook_rows[-1]["payload"].get("outcome"),
                "thread_id": None if task is None else task.dispatched_from_thread_id,
                "envelope": None if row["envelope_id"] is None else {
                    "id": row["envelope_id"], "state": row["envelope_state"],
                    "consumed_at": row["envelope_consumed_at"],
                },
                "receipt_state": "complete" if complete else "receipt_incomplete",
            })
        return {"items": projected, "next_cursor": next_cursor}
    except Exception:
        _logger.exception("authority policy outcomes unavailable for org=%s", slug)
        raise HTTPException(status_code=500, detail=_STORE_UNAVAILABLE) from None


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
    _manager_surface(org, agent_name)
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
    team, _ = _manager_surface(org, agent_name)
    readiness = activation_readiness()
    if not readiness["ready"]:
        raise HTTPException(status_code=412, detail={"code": "activation_guard_not_ready", **readiness})
    try:
        request_json = json.dumps(body.model_dump(mode="json"), sort_keys=True,
                                  separators=(",", ":"), ensure_ascii=False)
        request_digest = hashlib.sha256(request_json.encode()).hexdigest()
        store = AuthorityPolicyStore(org.db)
        current = store.get_current_activation(team)
        effective_action = "bootstrap" if current is None else body.action
        activation = store.activate_with_audit(
            team=team, release_id=body.release_id,
            expected_previous_epoch=body.expected_previous_epoch,
            action=effective_action, request_id=body.request_id,
            request_digest=request_digest,
        )
        return {"activation": {
            "id": activation.id, "epoch": activation.epoch,
            "release_id": activation.release_id, "action": activation.action,
            "digest": activation.activation_digest,
            "created_at": activation.created_at.isoformat(),
            "actor_attribution": "shared local operator credential",
        }}
    except sqlite3.IntegrityError as exc:
        code = "idempotency_conflict" if "idempotency" in str(exc) else "activation_conflict"
        raise HTTPException(status_code=409, detail={"code": code}) from None
    except LookupError:
        raise HTTPException(status_code=409, detail={"code": "activation_conflict"}) from None
    except Exception:
        _logger.exception("authority policy activation unavailable for org=%s", slug)
        raise HTTPException(status_code=500, detail=_STORE_UNAVAILABLE) from None


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
