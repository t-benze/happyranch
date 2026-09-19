"""Read-only, team-scoped authority-policy projection."""
from __future__ import annotations

import json
import logging
import hashlib
import re
import sqlite3
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from runtime.daemon.auth import require_token
from runtime.daemon.routes._org_dep import OrgDep
from runtime.orchestrator import prompt_loader
from runtime.orchestrator._paths import OrgPaths
from runtime.orchestrator.authority_policy_store import AuthorityPolicyStore
from runtime.orchestrator.active_authority_policy import (
    SELF_EVALUATION_CONTRACT_DIGEST,
    SELF_EVALUATION_CONTRACT_ID,
    SELF_EVALUATION_CONTRACT_VERSION,
    SESSION_POLICY_BINDING_ACTION,
)
from runtime.models import (
    AuthorityPolicyLegacyActivationRequest,
    AuthorityPolicyLegacyReactivationRequest,
    AuthorityPolicyRelease,
    AuthorityPolicyV2ActivationControlRequest,
    AuthorityPolicyV2PairedControlRequest,
    decode_authority_policy_v2_json,
)
from runtime.orchestrator.authority_policy import (
    CONTINUE_ROUTINE_PHRASE,
    PROMPT_DIGEST,
    PROMPT_ID,
    PROMPT_VERSION,
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
_APS_SELECTOR_RE = re.compile(r"^APS-[0-9a-f]{64}$")


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
    # B2b: the selector CAS base. Required (missing is invalid); explicit null
    # means the observed initialized empty selector, never today's live value.
    expected_selector_id: str | None
    request_id: str = Field(min_length=1, max_length=128)
    action: Literal["activate", "reactivate_rollback"] = "activate"
    acknowledge_shared_credential_attribution: Literal[True]

    @field_validator("expected_selector_id")
    @classmethod
    def bounded_selector_ref(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if _APS_SELECTOR_RE.fullmatch(value) is None:
            raise ValueError("expected_selector_id must be null or APS- plus 64 lowercase hex")
        return value


# -- B2b: strict v2 / selector-aware legacy control wire bodies. The accepted
# R2 models are reused verbatim; the route adds only the required
# shared-credential acknowledgment and injects the eligible team server-side.
class V2PairedControlBody(AuthorityPolicyV2PairedControlRequest):
    acknowledge_shared_credential_attribution: Literal[True]


class V2ActivationControlBody(AuthorityPolicyV2ActivationControlRequest):
    acknowledge_shared_credential_attribution: Literal[True]


async def _decode_control_body(request: Request, wrapper) -> dict:
    """Strict transport decode before any model normalization.

    ``decode_authority_policy_v2_json`` rejects invalid UTF-8, BOM, duplicate
    JSON members, non-object roots and NaN/Infinity, so framework JSON
    normalization cannot erase a duplicate field. The eligible team is
    server-owned: a mismatched client ``team`` is rejected, otherwise injected.
    """
    raw = await request.body()
    try:
        payload = decode_authority_policy_v2_json(raw)
        if payload.get("team", _ELIGIBLE_TEAM) != _ELIGIBLE_TEAM:
            raise ValueError("team is not the eligible policy surface")
        payload["team"] = _ELIGIBLE_TEAM
        body = wrapper.model_validate(payload)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "invalid_policy_request"},
        ) from None
    data = body.model_dump(mode="json")
    data.pop("acknowledge_shared_credential_attribution", None)
    return data


def _control_conflict(exc: Exception) -> None:
    message = str(exc)
    if "idempotency" in message:
        code = "idempotency_conflict"
    elif "selector" in message:
        code = "selector_conflict"
    else:
        code = "control_conflict"
    raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail={"code": code}) from None


def _project_v2_control(receipt) -> dict:
    """Discriminated v2 control projection with the observed selector identity.

    ``selector_id``/``selector_epoch``/``previous_selector_id`` are the CAS
    base a client must echo on its next selection; the full canonical receipt
    is carried verbatim so exact replay can be compared byte-for-byte.
    """
    return {
        "control": receipt.kind,
        "family": "v2",
        "contract_version": "v2",
        "selector_id": receipt.selector_id,
        "selector_epoch": receipt.selector_epoch,
        "previous_selector_id": receipt.previous_selector_id,
        "receipt": json.loads(receipt.canonical_json()),
    }


def _project_legacy_control(receipt) -> dict:
    return {
        "control": receipt.kind,
        "family": "legacy_v1",
        "contract_version": "v1",
        "receipt": json.loads(receipt.canonical_json()),
    }


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
        # Reader backstop for API-only fixtures that never ran daemon startup:
        # the serialized initializer is idempotent and observes authenticated
        # legacy history or truly empty state. Never reconstructs a selector.
        store.ensure_authority_selector(team)
        selector = store.get_authority_selector(team)
        if selector is None:
            raise ValueError("authority selector is unavailable after initialization")
        result = {
            "team": team,
            "target_manager": target_manager,
            "can_mutate": True,
            "bootstrap_template": _bootstrap_template(),
            "family": selector.family,
            "selector_id": selector.selector_id,
            "selector_epoch": selector.selector_epoch,
        }
        if selector.family == "empty":
            result["bootstrap_required"] = True
            return result
        if selector.family == "legacy_v1":
            activation = store.get_activation(selector.legacy_activation_id)
            if activation is None:
                raise ValueError("active release is missing")
            release = store.get_release(activation.release_id)
            if release is None:
                raise ValueError("active release is missing")
            result["contract_version"] = "v1"
            result["active"] = {
                "family": "legacy_v1",
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
        # A selected v2 family must never project the latest legacy activation.
        activation = store.get_v2_activation(selector.v2_activation_id)
        if activation is None:
            raise ValueError("active release is missing")
        release = store.get_v2_release(activation.release_id)
        if release is None:
            raise ValueError("active release is missing")
        result["contract_version"] = "v2"
        result["active"] = {
            "family": "v2",
            "activation_id": activation.id,
            "selector_epoch": activation.selector_epoch,
            "release": {
                "id": release.release_id,
                "policy_id": release.policy_id,
                "version": release.version,
                "title": release.title,
                "what_to_escalate": release.what_to_escalate,
                "what_not_to_escalate": release.what_not_to_escalate,
                "digest": release.policy_digest,
                "actor_attribution": "shared local operator credential",
            },
            "action": activation.action,
            "created_at": activation.created_at,
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
    cursor: str | None = Query(default=None, min_length=1, max_length=1024),
    limit: int = Query(default=20, ge=1, le=50),
) -> dict:
    team, _ = _manager_surface(org, agent_name)
    try:
        items, next_cursor = AuthorityPolicyStore(org.db).list_history(
            team, cursor=cursor, limit=limit,
        )
        return {"items": [{
            "family": "legacy_v1", "contract_version": "v1",
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
    except ValueError:
        raise HTTPException(status_code=422, detail={"code": "invalid_cursor"}) from None
    except Exception:
        _logger.exception("authority policy history unavailable for org=%s", slug)
        raise HTTPException(status_code=500, detail=_STORE_UNAVAILABLE) from None


@router.get("/agents/{agent_name}/team-escalation-policy/v2/history")
def get_team_escalation_policy_v2_history(
    slug: str, agent_name: str, org: OrgDep,
    cursor: str | None = Query(default=None, min_length=1, max_length=1024),
    limit: int = Query(default=20, ge=1, le=50),
) -> dict:
    """Bounded, newest-first immutable v2 paired-release history.

    Family-specific and read-only: it never rewrites or reinterprets the
    historical v1 stream, and it exposes no control audit or policy prose
    beyond the two accepted immutable texts.
    """
    team, _ = _manager_surface(org, agent_name)
    try:
        items, next_cursor = AuthorityPolicyStore(org.db).list_v2_history(
            team, cursor=cursor, limit=limit,
        )
        return {"items": [{
            "family": "v2", "contract_version": "v2",
            "release_id": row["release_id"], "policy_id": row["policy_id"],
            "version": row["version"], "title": row["title"],
            "what_to_escalate": row["what_to_escalate"],
            "what_not_to_escalate": row["what_not_to_escalate"],
            "contract_digest": row["contract_digest"],
            "policy_digest": row["policy_digest"],
            "release_created_at": row["release_created_at"],
            "activation": None if row["activation_id"] is None else {
                "id": row["activation_id"], "selector_epoch": row["selector_epoch"],
                "action": row["action"], "digest": row["activation_digest"],
                "created_at": row["activation_created_at"],
            },
            "actor_attribution": "shared local operator credential",
        } for row in items], "next_cursor": next_cursor}
    except ValueError:
        raise HTTPException(status_code=422, detail={"code": "invalid_cursor"}) from None
    except Exception:
        _logger.exception("authority policy v2 history unavailable for org=%s", slug)
        raise HTTPException(status_code=500, detail=_STORE_UNAVAILABLE) from None


def _outcome_receipts_complete(org: OrgDep, row: dict) -> tuple[bool, str | None]:
    """Authenticate every causal receipt promised by the S6b projection."""
    candidate_id = row["id"]
    result_id = row["causal_result_id"]
    if result_id is None and isinstance(row["causal_event_id"], str):
        _, separator, result_id = row["causal_event_id"].partition(":")
        if separator != ":":
            result_id = None
    if not isinstance(result_id, str) or not result_id.isdigit():
        return False, None
    result = org.db.execute("SELECT * FROM task_results WHERE id=?", (int(result_id),)).fetchone()
    task = org.db.execute("SELECT * FROM tasks WHERE id=?", (row["root_task_id"],)).fetchone()
    if result is None or task is None:
        return False, None
    thread_id = task["dispatched_from_thread_id"]
    thread = None if thread_id is None else org.db.execute(
        "SELECT id FROM threads WHERE id=?", (thread_id,),
    ).fetchone()
    expected_causal_id = f"result:{result_id}"
    expected_causal_digest = hashlib.sha256(f"task-result:{result_id}".encode()).hexdigest()
    if not (
        row["root_task_id"] == task["id"]
        and row["manager_agent"] == task["assigned_agent"]
        and result["task_id"] == task["id"]
        and result["agent"] == row["manager_agent"]
        and result["session_id"] == row["manager_session_id"]
        and row["causal_event_id"] == expected_causal_id
        and row["causal_event_digest"] == expected_causal_digest
        and (thread_id is None or (thread is not None and thread["id"] == thread_id))
    ):
        return False, thread_id
    release = org.db.execute("SELECT * FROM authority_policy_releases WHERE id=?", (row["release_id"],)).fetchone()
    activation = org.db.execute("SELECT * FROM authority_policy_activations WHERE id=?", (row["activation_id"],)).fetchone()
    evaluation = org.db.execute("SELECT * FROM authority_evaluations WHERE candidate_id=?", (candidate_id,)).fetchone()
    envelope = org.db.execute("SELECT * FROM authority_continue_envelopes WHERE candidate_id=?", (candidate_id,)).fetchone()
    if any(item is None for item in (release, activation, evaluation, envelope)):
        return False, thread_id
    if not (
        release["team"] == row["team"] and release["policy_id"] == row["policy_id"]
        and str(release["version"]) == row["policy_version"]
        and release["policy_digest"] == row["policy_digest"]
        and activation["team"] == row["team"] and activation["release_id"] == release["id"]
        and activation["epoch"] == row["activation_epoch"]
        and evaluation["candidate_id"] == candidate_id
        and evaluation["disposition"] == row["disposition"] == "continue_same_root"
        and envelope["candidate_id"] == candidate_id
        and envelope["root_task_id"] == row["root_task_id"]
        and envelope["team"] == row["team"]
        and envelope["manager_agent"] == row["manager_agent"]
        and envelope["manager_session_id"] == row["manager_session_id"]
        and envelope["causal_event_id"] == row["causal_event_id"]
        and envelope["causal_event_digest"] == row["causal_event_digest"]
        and envelope["policy_id"] == row["policy_id"]
        and envelope["policy_version"] == row["policy_version"]
        and envelope["policy_digest"] == row["policy_digest"]
        and envelope["action"] == "continue_same_root"
    ):
        return False, thread_id
    bindings = [item for item in org.db.get_audit_logs(row["root_task_id"])
                if item["action"] == SESSION_POLICY_BINDING_ACTION
                and item["agent"] == row["manager_agent"]
                and (item.get("payload") or {}).get("session_id") == row["manager_session_id"]]
    hooks = [item for item in org.db.get_audit_logs(row["root_task_id"])
             if item["action"] == "authority_hook"
             and (item.get("payload") or {}).get("candidate_id") == candidate_id]
    authority_events = org.db.list_authority_audit(candidate_id)
    if len(bindings) != 1 or len(hooks) != 1:
        return False, thread_id
    binding = bindings[0].get("payload") or {}
    expected_model_id = (
        f"manager/{binding.get('provider_id')}/{binding.get('executor_kind')}/"
        f"{binding.get('model_id')}"
    )
    expected_model_version = SELF_EVALUATION_CONTRACT_VERSION
    expected_model_digest = hashlib.sha256(
        f"{expected_model_id}:{expected_model_version}:"
        f"{SELF_EVALUATION_CONTRACT_DIGEST}".encode()
    ).hexdigest()
    hook = hooks[0].get("payload") or {}
    if not (
        binding.get("mode") == "db_release"
        and binding.get("release_id") == row["release_id"]
        and binding.get("activation_id") == row["activation_id"]
        and binding.get("activation_epoch") == row["activation_epoch"]
        and binding.get("policy_digest") == row["policy_digest"]
        and binding.get("provider_id") == row["provider_id"]
        and binding.get("executor_kind") == row["executor_kind"]
        and row["prompt_id"] == PROMPT_ID
        and row["prompt_version"] == PROMPT_VERSION
        and row["prompt_digest"] == PROMPT_DIGEST
        and row["model_id"] == expected_model_id
        and row["model_version"] == expected_model_version
        and row["model_digest"] == expected_model_digest
        and binding.get("self_evaluation_contract_id") == SELF_EVALUATION_CONTRACT_ID
        and binding.get("self_evaluation_contract_version") == SELF_EVALUATION_CONTRACT_VERSION
        and binding.get("self_evaluation_contract_digest") == SELF_EVALUATION_CONTRACT_DIGEST
        and hooks[0]["agent"] == row["manager_agent"]
        and hook.get("outcome") == "continued_same_root"
        and hook.get("candidate_id") == candidate_id
        and hook.get("causal_event_id") == row["causal_event_id"]
        and hook.get("causal_event_digest") == row["causal_event_digest"]
        and hook.get("causal_result_id") in (None, result_id)
        and hook.get("policy_id") == row["policy_id"]
        and str(hook.get("policy_version")) == row["policy_version"]
        and hook.get("policy_digest") == row["policy_digest"]
        and hook.get("prompt_id") == row["prompt_id"]
        and hook.get("prompt_version") == row["prompt_version"]
        and hook.get("prompt_digest") == row["prompt_digest"]
        and hook.get("model_id") == row["model_id"]
        and hook.get("model_version") == row["model_version"]
        and hook.get("model_digest") == row["model_digest"]
        and [event.event_type.value for event in authority_events] == [
            "candidate_claimed", "evaluation_recorded", "candidate_consumed"]
    ):
        return False, thread_id
    return True, thread_id


@router.get("/agents/{agent_name}/team-escalation-policy/outcomes")
def get_team_escalation_policy_outcomes(
    slug: str, agent_name: str, org: OrgDep,
    cursor: str | None = Query(default=None, min_length=1, max_length=1024),
    limit: int = Query(default=20, ge=1, le=50),
) -> dict:
    team, _ = _manager_surface(org, agent_name)
    try:
        items, next_cursor = AuthorityPolicyStore(org.db).list_outcomes(
            team, cursor=cursor, limit=limit,
        )
        projected = []
        for row in items:
            hook_rows = [item for item in org.db.get_audit_logs(row["root_task_id"])
                         if item["action"] == "authority_hook"
                         and (item.get("payload") or {}).get("candidate_id") == row["id"]]
            complete, thread_id = _outcome_receipts_complete(org, row)
            projected.append({
                "candidate_id": row["id"], "root_task_id": row["root_task_id"],
                "manager_session_id": row["manager_session_id"],
                "causal_event_id": row["causal_event_id"],
                "causal_result_id": row["causal_result_id"],
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
                "evaluator_contract": {
                    "id": SELF_EVALUATION_CONTRACT_ID,
                    "version": SELF_EVALUATION_CONTRACT_VERSION,
                    "digest": SELF_EVALUATION_CONTRACT_DIGEST,
                },
                "terminal_hook_outcome": None if not hook_rows else hook_rows[-1]["payload"].get("outcome"),
                "thread_id": thread_id,
                "envelope": None if row["envelope_id"] is None else {
                    "id": row["envelope_id"], "state": row["envelope_state"],
                    "consumed_at": row["envelope_consumed_at"],
                },
                "receipt_state": "complete" if complete else "receipt_incomplete",
            })
        return {"items": projected, "next_cursor": next_cursor}
    except ValueError:
        raise HTTPException(status_code=422, detail={"code": "invalid_cursor"}) from None
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
        # B2b: initialize the serialized selector BEFORE creating unselected
        # legacy history, so this route's own release cannot strand its later
        # initialization. A release-only write never implies a selection.
        store.ensure_authority_selector(_ELIGIBLE_TEAM)
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
    """Selector-CAS legacy v1 selection: new release or authenticated rollback.

    The observed ``expected_selector_id`` is the CAS base and the legacy family
    epoch is allocated server-side from the sealed legacy stream. The low-level
    selector-bypassing ``activate_with_audit`` path is no longer reachable from
    this shipping route, and a CAS/audit refusal never falls back to a
    legacy-only write.
    """
    team, _ = _manager_surface(org, agent_name)
    try:
        store = AuthorityPolicyStore(org.db)
        # Compatible writer backstop: the serialized initializer commits BEFORE
        # the policy transaction and never implies a selection.
        store.ensure_authority_selector(team)
        if body.action == "reactivate_rollback":
            # The v1 wire names a release; the transaction owner requires the
            # exact authenticated activation. Never fabricate a missing one.
            target = store.get_activation_for_release(team, body.release_id)
            if target is None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={"code": "activation_conflict"},
                )
            receipt = store.reactivate_legacy_authority_policy(
                AuthorityPolicyLegacyReactivationRequest(
                    team=team, activation_id=target.id, request_id=body.request_id,
                    expected_selector_id=body.expected_selector_id,
                )
            )
        else:
            receipt = store.activate_legacy_authority_policy(
                AuthorityPolicyLegacyActivationRequest(
                    team=team, release_id=body.release_id, request_id=body.request_id,
                    expected_selector_id=body.expected_selector_id,
                )
            )
        legacy_activation = store.get_activation(receipt.activation_id)
        if legacy_activation is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=_STORE_UNAVAILABLE,
            )
        return {
            **_project_legacy_control(receipt),
            "selector_id": receipt.selector_id,
            "selector_epoch": receipt.selector_epoch,
            "previous_selector_id": receipt.previous_selector_id,
            "activation": {
                "id": legacy_activation.id, "epoch": legacy_activation.epoch,
                "release_id": legacy_activation.release_id,
                "action": legacy_activation.action,
                "digest": legacy_activation.activation_digest,
                "created_at": legacy_activation.created_at.isoformat(),
                "actor_attribution": "shared local operator credential",
            },
        }
    except HTTPException:
        raise
    except sqlite3.IntegrityError as exc:
        _control_conflict(exc)
    except LookupError:
        raise HTTPException(status_code=409, detail={"code": "activation_conflict"}) from None
    except ValueError as exc:
        if "initialization" in str(exc):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "initialization_unavailable"},
            ) from None
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "invalid_policy_request"},
        ) from None
    except Exception:
        _logger.exception("authority policy activation unavailable for org=%s", slug)
        raise HTTPException(status_code=500, detail=_STORE_UNAVAILABLE) from None


@router.post(
    "/agents/{agent_name}/team-escalation-policy/v2/releases",
    status_code=status.HTTP_201_CREATED,
    responses={
        404: {"description": "Policy surface unavailable"},
        409: {"description": "Selector CAS, replay or idempotency conflict"},
        422: {"description": "Strict v2 request validation failed"},
        500: {"description": "Sanitized policy store failure"},
    },
)
async def create_and_activate_team_escalation_policy_v2(
    slug: str,
    agent_name: str,
    request: Request,
    org: OrgDep,
) -> dict:
    """Strict paired save+activate of one immutable dual-text v2 release.

    Raw HTTP bytes are decoded before any JSON/Pydantic normalization, so a
    duplicate member, invalid UTF-8/BOM, non-object root, ``NaN``/``Infinity``,
    wrong scalar type, unknown field or malformed bound refuses. The pair and
    its single selection, both control audits and the receipt commit in ONE
    transaction owned by ``Database.create_and_activate_authority_policy_v2``;
    there is no independent per-text save or draft-only v2 creation.
    """
    team, _ = _manager_surface(org, agent_name)
    data = await _decode_control_body(request, V2PairedControlBody)
    try:
        store = AuthorityPolicyStore(org.db)
        try:
            store.ensure_authority_selector(team)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "initialization_unavailable"},
            ) from None
        receipt = store.create_and_activate_v2(data)
    except HTTPException:
        raise
    except sqlite3.IntegrityError as exc:
        _control_conflict(exc)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail={"code": "control_conflict"},
        ) from None
    except Exception:
        _logger.exception("authority policy v2 paired control unavailable for org=%s", slug)
        raise HTTPException(status_code=500, detail=_STORE_UNAVAILABLE) from None
    return _project_v2_control(receipt)


@router.post(
    "/agents/{agent_name}/team-escalation-policy/v2/activations",
    responses={
        404: {"description": "Policy surface unavailable"},
        409: {"description": "Selector CAS, replay or idempotency conflict"},
        422: {"description": "Strict v2 request validation failed"},
        500: {"description": "Sanitized policy store failure"},
    },
)
async def activate_team_escalation_policy_v2(
    slug: str,
    agent_name: str,
    request: Request,
    org: OrgDep,
) -> dict:
    """Strict select/rollback of an already saved immutable v2 release.

    Reuses the same transaction owner and strict transport decode as the paired
    route but never creates a release; ``action`` is exactly
    ``activate``/``reactivate_rollback`` (``bootstrap`` is reserved for a newly
    saved pair).
    """
    team, _ = _manager_surface(org, agent_name)
    data = await _decode_control_body(request, V2ActivationControlBody)
    try:
        store = AuthorityPolicyStore(org.db)
        try:
            store.ensure_authority_selector(team)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "initialization_unavailable"},
            ) from None
        receipt = store.activate_v2(data)
    except HTTPException:
        raise
    except sqlite3.IntegrityError as exc:
        _control_conflict(exc)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail={"code": "control_conflict"},
        ) from None
    except Exception:
        _logger.exception("authority policy v2 activation unavailable for org=%s", slug)
        raise HTTPException(status_code=500, detail=_STORE_UNAVAILABLE) from None
    return _project_v2_control(receipt)


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
    """Project the current canonical definition; step count is not a policy limit."""
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
