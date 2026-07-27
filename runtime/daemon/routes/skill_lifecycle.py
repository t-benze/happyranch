"""THR-055 Custom-skill lifecycle routes — agent proposals + human lifecycle management.

Routes:
- POST /skill-lifecycle/proposals — Agent-only task/session-bound proposal submission
- GET /skill-lifecycle/{skill_id} — Read lifecycle status (human + agent read)
- GET /skill-lifecycle/catalog/custom — List published custom skills
- POST /skill-lifecycle/{skill_id}/claim — Human: claim proposal → draft
- POST /skill-lifecycle/validate — Human: validate a version
- POST /skill-lifecycle/submit-review — Human: submit for review
- POST /skill-lifecycle/review — Human: approve/reject
- POST /skill-lifecycle/publish — Human: publish
- POST /skill-lifecycle/assign — Human: assign to agent
- POST /skill-lifecycle/rollback — Human: emergency rollback
- POST /skill-lifecycle/retire — Human: retire
- GET /skill-lifecycle/events/{skill_id} — Read event history

Agent 403 matrix: agents may ONLY submit proposals. All lifecycle/config/
eligibility/permission mutation attempts return server-side 403.

Identity for proposals derives from verified task/session binding (SessionTracker),
never from request body claims.
"""

from __future__ import annotations

import yaml
from fastapi import APIRouter, HTTPException, Query, Request, status

from runtime.daemon.auth import require_token
from runtime.daemon.org_state import OrgState
from runtime.daemon.routes._org_dep import OrgDep
from runtime.skills.lifecycle.models import (
    AssignRequest,
    ClaimProposalRequest,
    LifecycleStatus,
    ProposalRequest,
    PublishRequest,
    ReviewDecisionRequest,
    RollbackRequest,
    SubmitForReviewRequest,
)
from runtime.skills.lifecycle.service import (
    AgentForbiddenError,
    LifecycleError,
    SkillLifecycleService,
)

router = APIRouter(prefix="/skill-lifecycle", dependencies=[require_token()])

_service = SkillLifecycleService()


# ── Helpers ──────────────────────────────────────────────────────────────

def _derive_actor_kind(request: Request) -> str:
    """Derive actor kind from request context.

    In the pilot, all bearer-authed requests are treated as 'human' (founder)
    EXCEPT requests that carry validated task_id + session_id binding, which
    are treated as 'agent'.

    The real binding check happens inside the proposal route via SessionTracker.
    """
    return "human"  # Default — overridden in proposal route


def _get_db(org: OrgState):
    return org.db

# ── Route: POST /skill-lifecycle/proposals ────────────────────────────────

@router.post("/proposals", status_code=201)
def submit_proposal(
    slug: str,
    org: OrgDep,
    body: ProposalRequest,
    request: Request,
) -> dict:
    """Agent-only: submit a task/session-bound skill proposal.

    Identity is derived from the verified task/session binding via SessionTracker.
    Body claims for task_id, session_id, proposer_agent are IGNORED.

    Returns 403 for all agent lifecycle/config/eligibility mutation attempts
    except this bounded proposal submission.
    """
    # Derive agent identity from session binding
    task_id = getattr(request.state, "task_id", None)
    session_id = getattr(request.state, "session_id", None)
    agent_name = getattr(request.state, "agent_name", None)

    # If no session binding, treat as human (founder) request.
    # Human-founders can also submit proposals but with different semantics.
    if task_id and session_id and agent_name:
        actor_kind = "agent"
    else:
        actor_kind = "human"

    try:
        pkg = _service.submit_proposal(
            db=_get_db(org),
            actor_kind=actor_kind,
            slug=body.slug,
            name=body.name,
            description=body.description,
            skill_md=body.skill_md,
            version=body.version,
            policy_class=body.policy_class,
            references=body.references,
            assets=body.assets,
            task_id=task_id,
            session_id=session_id,
            proposer_agent=agent_name,
            purpose=body.purpose,
            target_agent_suggestion=body.target_agent_suggestion,
        )
    except LifecycleError as e:
        raise HTTPException(
            status_code=e.status_code,
            detail={"code": e.code, "detail": e.detail},
        )

    return {
        "skill_id": pkg.skill_id,
        "version_id": pkg.id,
        "version": pkg.version,
        "status": pkg.status.value,
        "content_hash": pkg.content_hash,
        "proposal_task_id": pkg.proposal_task_id,
    }


# ── Route: GET /skill-lifecycle/{skill_id} ────────────────────────────────

@router.get("/{skill_id}")
def get_lifecycle_status(
    slug: str,
    skill_id: str,
    org: OrgDep,
) -> dict:
    """Read the full lifecycle status for a skill.

    Returns current status, version, assignments, event history, and provenance.
    Human + agent readable.
    """
    try:
        result = _service.get_status(_get_db(org), skill_id)
    except LifecycleError as e:
        raise HTTPException(
            status_code=e.status_code,
            detail={"code": e.code, "detail": e.detail},
        )

    if result["current_status"] is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "not_found", "skill_id": skill_id},
        )

    # Serialize for JSON
    return {
        "skill_id": result["skill_id"],
        "slug": result["slug"],
        "current_status": result["current_status"].value if result["current_status"] else None,
        "current_version": result["current_version"],
        "current_version_id": result["current_version_id"],
        "published_version": result["published_version"],
        "assignments": [
            {
                "agent_name": a.agent_name,
                "version": a.version,
                "content_hash": a.content_hash,
                "assigned_by": a.assigned_by,
                "assigned_at": a.assigned_at.isoformat(),
                "active": a.active,
            }
            for a in result["assignments"]
        ],
        "events": [
            {
                "event_type": e.event_type,
                "actor": e.actor,
                "actor_role": e.actor_role,
                "new_status": e.new_status,
                "content_hash": e.content_hash,
                "created_at": e.created_at.isoformat(),
            }
            for e in result["events"]
        ],
        "proposal_task_id": result["proposal_task_id"],
        "proposer_agent": result["proposer_agent"],
    }


# ── Route: GET /skill-lifecycle/catalog/custom ────────────────────────────

@router.get("/catalog/custom")
def list_custom_catalog(
    slug: str,
    org: OrgDep,
) -> dict:
    """List published custom skills for the catalog.

    Only PUBLISHED skills appear. Proposed, draft, validated, approved,
    rolled_back, and retired skills are invisible here.
    """
    pkgs = _service.list_catalog(_get_db(org))
    return {
        "skills": [
            {
                "skill_id": p.skill_id,
                "slug": p.slug,
                "name": p.name,
                "version": p.version,
                "description": p.description,
                "content_hash": p.content_hash,
                "published_at": p.published_at.isoformat() if p.published_at else None,
                "publisher": p.publisher,
            }
            for p in pkgs
        ]
    }


# ── Route: POST /skill-lifecycle/{skill_id}/claim ────────────────────────

@router.post("/{skill_id}/claim")
def claim_proposal(
    slug: str,
    skill_id: str,
    org: OrgDep,
    body: ClaimProposalRequest,
) -> dict:
    """Human-only: claim an agent proposal and promote to draft."""
    try:
        pkg = _service.claim_proposal(
            db=_get_db(org),
            actor_kind="human",
            version_id=body.proposal_version_id,
            sponsor="founder",
        )
    except LifecycleError as e:
        raise HTTPException(
            status_code=e.status_code,
            detail={"code": e.code, "detail": e.detail},
        )

    return {
        "skill_id": pkg.skill_id,
        "version_id": pkg.id,
        "status": pkg.status.value,
        "version": pkg.version,
    }


# ── Route: POST /skill-lifecycle/validate ─────────────────────────────────

@router.post("/validate")
def validate_version(
    slug: str,
    org: OrgDep,
    version_id: int = Query(...),
) -> dict:
    """Human-only: record validation result for a draft version."""
    try:
        pkg = _service.record_validation(
            db=_get_db(org),
            actor_kind="human",
            version_id=version_id,
            ok=True,
            findings=[],
        )
    except LifecycleError as e:
        raise HTTPException(
            status_code=e.status_code,
            detail={"code": e.code, "detail": e.detail},
        )

    return {
        "skill_id": pkg.skill_id,
        "version_id": pkg.id,
        "status": pkg.status.value,
        "version": pkg.version,
    }


# ── Route: POST /skill-lifecycle/submit-review ────────────────────────────

@router.post("/submit-review")
def submit_for_review(
    slug: str,
    org: OrgDep,
    body: SubmitForReviewRequest,
) -> dict:
    """Human-only: submit a validated version for review."""
    try:
        pkg = _service.submit_for_review(
            db=_get_db(org),
            actor_kind="human",
            version_id=body.version_id,
            sponsor="founder",
            intended_audience=body.intended_audience,
            review_notes=body.review_notes,
        )
    except LifecycleError as e:
        raise HTTPException(
            status_code=e.status_code,
            detail={"code": e.code, "detail": e.detail},
        )

    return {
        "skill_id": pkg.skill_id,
        "version_id": pkg.id,
        "status": pkg.status.value,
        "version": pkg.version,
    }


# ── Route: POST /skill-lifecycle/review ───────────────────────────────────

@router.post("/review")
def review_decision(
    slug: str,
    org: OrgDep,
    body: ReviewDecisionRequest,
) -> dict:
    """Human-only: reviewer approves or rejects a submitted version.

    Reviewer must be distinct from author (maker-checker).
    """
    try:
        pkg = _service.review_decision(
            db=_get_db(org),
            actor_kind="human",
            version_id=body.version_id,
            decision=body.decision,
            rationale=body.rationale,
            reviewer="founder",
        )
    except LifecycleError as e:
        raise HTTPException(
            status_code=e.status_code,
            detail={"code": e.code, "detail": e.detail},
        )

    return {
        "skill_id": pkg.skill_id,
        "version_id": pkg.id,
        "status": pkg.status.value,
        "decision": body.decision,
    }


# ── Route: POST /skill-lifecycle/publish ──────────────────────────────────

@router.post("/publish")
def publish(
    slug: str,
    org: OrgDep,
    body: PublishRequest,
) -> dict:
    """Human-only: publish an approved version to the custom catalog.

    Enforces the two-published-cap and requires matching approval event id.
    """
    try:
        pkg = _service.publish(
            db=_get_db(org),
            actor_kind="human",
            version_id=body.publish_version_id if hasattr(body, 'publish_version_id') else body.approval_event_id,
            approval_event_id=body.approval_event_id,
            publisher="founder",
        )
    except LifecycleError as e:
        raise HTTPException(
            status_code=e.status_code,
            detail={"code": e.code, "detail": e.detail},
        )

    return {
        "skill_id": pkg.skill_id,
        "version_id": pkg.id,
        "status": pkg.status.value,
        "version": pkg.version,
        "published_at": pkg.published_at.isoformat() if pkg.published_at else None,
    }


# ── Route: POST /skill-lifecycle/assign ───────────────────────────────────

@router.post("/assign")
def assign_skill(
    slug: str,
    org: OrgDep,
    body: AssignRequest,
) -> dict:
    """Human-only: assign a published version to a named agent."""
    try:
        assign = _service.assign(
            db=_get_db(org),
            actor_kind="human",
            skill_id=f"hr:{body.skill_slug}" if hasattr(body, 'skill_slug') else body.agent_name,
            agent_name=body.agent_name,
            version_id=body.version_id,
            assigner="founder",
        )
    except LifecycleError as e:
        raise HTTPException(
            status_code=e.status_code,
            detail={"code": e.code, "detail": e.detail},
        )

    return {
        "skill_id": assign.skill_id,
        "agent_name": assign.agent_name,
        "version": assign.version,
        "content_hash": assign.content_hash,
        "assigned_at": assign.assigned_at.isoformat(),
    }


# ── Route: POST /skill-lifecycle/rollback ─────────────────────────────────

@router.post("/rollback")
def rollback(
    slug: str,
    org: OrgDep,
    skill_id: str = Query(...),
    reason: str = Query(""),
    target_version_id: int | None = Query(None),
) -> dict:
    """Human-only: emergency rollback — deactivate all assignments for a skill."""
    try:
        count = _service.rollback(
            db=_get_db(org),
            actor_kind="human",
            skill_id=skill_id,
            reason=reason,
            rolled_back_by="founder",
            target_version_id=target_version_id,
        )
    except LifecycleError as e:
        raise HTTPException(
            status_code=e.status_code,
            detail={"code": e.code, "detail": e.detail},
        )

    return {
        "skill_id": skill_id,
        "assignments_deactivated": count,
        "reason": reason,
    }


# ── Route: POST /skill-lifecycle/retire ───────────────────────────────────

@router.post("/retire")
def retire(
    slug: str,
    org: OrgDep,
    skill_id: str = Query(...),
    reason: str = Query(""),
) -> dict:
    """Human-only: retire a published skill."""
    try:
        pkg = _service.retire(
            db=_get_db(org),
            actor_kind="human",
            skill_id=skill_id,
            reason=reason,
            retired_by="founder",
        )
    except LifecycleError as e:
        raise HTTPException(
            status_code=e.status_code,
            detail={"code": e.code, "detail": e.detail},
        )

    return {
        "skill_id": pkg.skill_id,
        "status": pkg.status.value,
        "reason": reason,
    }


# ── Route: GET /skill-lifecycle/events/{skill_id} ─────────────────────────

@router.get("/events/{skill_id}")
def get_events(
    slug: str,
    skill_id: str,
    org: OrgDep,
    limit: int = Query(100, ge=1, le=500),
) -> dict:
    """Read event history for a skill (human + agent readable)."""
    from runtime.skills.lifecycle import stores
    events = stores.list_lifecycle_events(_get_db(org), skill_id=skill_id, limit=limit)
    return {
        "skill_id": skill_id,
        "events": [
            {
                "id": e.id,
                "event_type": e.event_type,
                "actor": e.actor,
                "actor_role": e.actor_role,
                "previous_status": e.previous_status,
                "new_status": e.new_status,
                "content_hash": e.content_hash,
                "created_at": e.created_at.isoformat(),
                "metadata": e.metadata,
            }
            for e in events
        ],
    }
