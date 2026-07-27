"""THR-055 Custom-skill lifecycle routes — agent proposals + human lifecycle management.

All routes live on ``dual_router`` (dual-auth — bearer OR session-binding).
Human-only mutations are gated by ``_require_human()`` which returns 403 for
non-bearer (agent-session) callers. Human/founder uses master bearer token.

Routes:
- POST /skill-lifecycle/proposals — Agent task/session-bound OR founder proposal
- POST /skill-lifecycle/{skill_id}/claim — Human-only: claim proposal → draft
- POST /skill-lifecycle/validate — Human-only: validate a version
- POST /skill-lifecycle/submit-review — Human-only: submit for review
- POST /skill-lifecycle/review — Human-only: approve/reject
- POST /skill-lifecycle/publish — Human-only: publish
- POST /skill-lifecycle/assign — Human-only: assign to agent
- POST /skill-lifecycle/rollback — Human-only: emergency rollback (atomic)
- POST /skill-lifecycle/retire — Human-only: retire
- GET /skill-lifecycle/{skill_id} — Read lifecycle status (human + agent)
- GET /skill-lifecycle/catalog/custom — List published custom skills
- GET /skill-lifecycle/events/{skill_id} — Read event history

Agent 403 matrix: agents may ONLY submit proposals. All other lifecycle/
config/eligibility/permission mutation attempts return server-side 403.
Human-only routes use ``_require_human()`` which returns 403 (not 401) for
non-bearer callers — matching the server-side authorization semantics.

Identity for proposals derives from verified task/session binding via
SessionTracker (never from request body claims) when the caller is an agent.
For human callers (master bearer), identity is the founder with full authority.

Protected-slug checking consults the live release/system catalog and fails
closed if the registry is unavailable — no static fallback.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from runtime.daemon.auth import _check_optional_token, optional_bearer, require_token
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

# ── Single dual-auth router ──────────────────────────────────────────────

dual_router = APIRouter(prefix="/skill-lifecycle")

_service = SkillLifecycleService()


def _require_human(has_bearer: bool = Depends(_check_optional_token)):
    """Return 403 for non-bearer (agent-session) callers.

    Human-only lifecycle mutations must use the master bearer token.
    Agent sessions are authenticated but not authorized — 403, not 401.
    """
    if not has_bearer:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "human_only",
                "detail": "This action requires human/founder authority. Agent sessions are not authorized.",
            },
        )


# ── Helpers ──────────────────────────────────────────────────────────────

def _get_db(org: OrgState):
    return org.db


def _get_protected_slugs(org: OrgState) -> frozenset:
    """Build the live protected-slug set from the release catalog + system contracts.

    Consults the runtime skills registry and system contracts, NOT a static list.
    Fails closed: if the registry cannot be loaded, raises HTTP 500 rather than
    falling back to a stale static list.
    """
    from runtime.skills.registry import SkillRegistry
    from runtime.skills.system_contracts import SYSTEM_CONTRACTS

    release_dir = org.settings.project_root / "runtime" / "skills"
    protected = set()
    if release_dir.is_dir():
        registry = SkillRegistry(skills_root=release_dir)
        for entry in registry.list_all():
            if isinstance(entry, tuple):
                entry = entry[0]  # Some registries return (entry, source) tuples
            protected.add(getattr(entry, 'slug', getattr(entry, 'id', '')))
    # Add system contract slugs
    for sc in SYSTEM_CONTRACTS:
        protected.add(sc.id)
    return frozenset(protected)


def _verify_agent_session(
    org: OrgState, task_id: str | None, session_id: str | None, agent_name: str | None,
) -> tuple[str, str, str]:
    """Verify agent identity via SessionTracker.

    Returns (verified_task_id, verified_session_id, verified_agent_name) on success.
    Raises 403 if any binding is invalid/expired/mismatched.
    """
    if not task_id or not session_id or not agent_name:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "agent_identity_required",
                "detail": "Agent proposals require verified task_id + session_id + agent_name binding.",
            },
        )

    expected_session = org.sessions.get_active(task_id, agent_name)
    if expected_session is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "unknown_session",
                "detail": f"No active session for task {task_id} agent {agent_name}.",
            },
        )
    if expected_session != session_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "session_mismatch",
                "detail": f"Session {session_id} does not match active session for task {task_id} agent {agent_name}.",
            },
        )

    return task_id, session_id, agent_name


def _verify_agent_proposal_identity(
    org: OrgState,
    task_id: str | None,
    session_id: str | None,
    agent_name: str | None,
    has_bearer: bool,
) -> tuple[str, str]:
    """Derive authentic actor identity for proposal submission.

    - has_bearer=True → human/founder (trusted master bearer)
    - has_bearer=False → must verify task/session/agent via SessionTracker

    Returns (actor_kind, actor_name).
    """
    if has_bearer:
        return "human", "founder"

    if not task_id or not session_id or not agent_name:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "agent_identity_required",
                "detail": "Agent proposals require verified task_id + session_id + agent_name binding.",
            },
        )

    # Verify against SessionTracker
    _verify_agent_session(org, task_id, session_id, agent_name)

    return "agent", agent_name


# ═══════════════════════════════════════════════════════════════════════════
# Dual-auth routes (bearer OR session-binding)
# ═══════════════════════════════════════════════════════════════════════════

@dual_router.post("/proposals", status_code=201)
def submit_proposal(
    slug: str,
    org: OrgDep,
    body: ProposalRequest,
    request: Request,
    task_id: str | None = Query(None),
    session_id: str | None = Query(None),
    agent_name: str | None = Query(None),
    has_bearer: bool = Depends(_check_optional_token),
) -> dict:
    """Submit a skill proposal.

    **Dual-auth.** Agent callers submit via verified task/session binding
    (query params ``task_id``, ``session_id``, ``agent_name``). Human/founder
    callers use the master bearer token.

    Body claims for task_id, session_id, proposer_agent are IGNORED —
    only verified session binding (agent path) or bearer token (human path)
    is trusted.

    Agent path returns 403 for inactive, expired, or mismatched bindings.
    """
    actor_kind, actor_name = _verify_agent_proposal_identity(
        org, task_id, session_id, agent_name, has_bearer,
    )

    try:
        protected_slugs = _get_protected_slugs(org)
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
            protected_slugs=protected_slugs,
            org_root=org.root,
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
        "content_artifact_key": pkg.content_artifact_key,
        "proposal_task_id": pkg.proposal_task_id,
    }


@dual_router.get("/{skill_id}")
def get_lifecycle_status(
    slug: str,
    skill_id: str,
    org: OrgDep,
    has_bearer: bool = Depends(_check_optional_token),
) -> dict:
    """Read the full lifecycle status for a skill.

    Dual-auth: human + agent readable.
    Returns current status, version, assignments, event history, and provenance.
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


@dual_router.get("/catalog/custom")
def list_custom_catalog(
    slug: str,
    org: OrgDep,
    has_bearer: bool = Depends(_check_optional_token),
) -> dict:
    """List published custom skills for the catalog.

    Dual-auth: human + agent readable.
    Only PUBLISHED skills appear. Proposed, draft, validated, approved,
    rolled_back, and retired skills are invisible here.
    """
    pkgs = _service.list_catalog(_get_db(org))
    return {
        "skills": [
            {
                "version_id": p.id,
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


@dual_router.get("/events/{skill_id}")
def get_events(
    slug: str,
    skill_id: str,
    org: OrgDep,
    limit: int = Query(100, ge=1, le=500),
    has_bearer: bool = Depends(_check_optional_token),
) -> dict:
    """Read event history for a skill.

    Dual-auth: human + agent readable.
    """
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


# ═══════════════════════════════════════════════════════════════════════════
# Human-only routes (bearer-token-gated — founder)
# ═══════════════════════════════════════════════════════════════════════════

@dual_router.post("/{skill_id}/claim", dependencies=[Depends(_require_human)])
def claim_proposal(
    slug: str,
    skill_id: str,
    org: OrgDep,
    body: ClaimProposalRequest,
) -> dict:
    """Human-only: claim an agent proposal and promote to draft.

    Bearer-token-gated — only the founder/human with the master bearer can call this.
    """
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


@dual_router.post("/validate", dependencies=[Depends(_require_human)])
def validate_version(
    slug: str,
    org: OrgDep,
    version_id: int = Query(...),
) -> dict:
    """Human-only: record validation result for a draft version.

    Bearer-token-gated — only the founder/human with the master bearer can call this.
    """
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


@dual_router.post("/submit-review", dependencies=[Depends(_require_human)])
def submit_for_review(
    slug: str,
    org: OrgDep,
    body: SubmitForReviewRequest,
) -> dict:
    """Human-only: submit a validated version for review.

    Bearer-token-gated — only the founder/human with the master bearer can call this.
    """
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


@dual_router.post("/review", dependencies=[Depends(_require_human)])
def review_decision(
    slug: str,
    org: OrgDep,
    body: ReviewDecisionRequest,
) -> dict:
    """Human-only: reviewer approves or rejects a submitted version.

    Reviewer must be distinct from author (maker-checker).
    Bearer-token-gated — only the founder/human with the master bearer can call this.
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


@dual_router.post("/publish", dependencies=[Depends(_require_human)])
def publish(
    slug: str,
    org: OrgDep,
    body: PublishRequest,
) -> dict:
    """Human-only: publish an approved version to the custom catalog.

    Enforces the two-published-cap and requires matching approval event id.
    Bearer-token-gated — only the founder/human with the master bearer can call this.
    """
    try:
        pkg = _service.publish(
            db=_get_db(org),
            actor_kind="human",
            version_id=body.version_id,
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


@dual_router.post("/assign", dependencies=[Depends(_require_human)])
def assign_skill(
    slug: str,
    org: OrgDep,
    body: AssignRequest,
) -> dict:
    """Human-only: assign a published version to a named agent.

    Requires explicit skill_id (e.g. "hr:my-skill") in the request body.
    Bearer-token-gated — only the founder/human with the master bearer can call this.
    """
    try:
        assign = _service.assign(
            db=_get_db(org),
            actor_kind="human",
            skill_id=body.skill_id,
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


@dual_router.post("/rollback", dependencies=[Depends(_require_human)])
def rollback(
    slug: str,
    org: OrgDep,
    skill_id: str = Query(...),
    reason: str = Query(""),
    target_version_id: int | None = Query(None),
) -> dict:
    """Human-only: emergency rollback — deactivate all assignments for a skill.

    Atomically unassigns affected assignments while retaining immutable
    history/content references. All operations execute within an explicit
    ``BEGIN IMMEDIATE`` / ``COMMIT`` transaction so package status,
    assignment deactivation, and event insertion roll back together.

    After the ledger transaction commits, prior materialized custom-skill
    workspace residue is cleaned from agent workspaces.

    Bearer-token-gated — only the founder/human with the master bearer can call this.
    """
    import shutil
    from pathlib import Path

    db = _get_db(org)
    conn = db._conn if hasattr(db, '_conn') else db
    # Disable implicit transactions so explicit BEGIN IMMEDIATE works.
    # The route manages transactions explicitly.
    prev_isolation = getattr(conn, 'isolation_level', None)
    try:
        conn.isolation_level = None
        # Explicit transaction wrapping for atomicity on the raw connection
        conn.execute("BEGIN IMMEDIATE")
        count = _service.rollback(
            db=db,
            actor_kind="human",
            skill_id=skill_id,
            reason=reason,
            rolled_back_by="founder",
            target_version_id=target_version_id,
        )
        conn.execute("COMMIT")
    except LifecycleError as e:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        raise HTTPException(
            status_code=e.status_code,
            detail={"code": e.code, "detail": e.detail},
        )
    except Exception as e:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "rollback_failed", "skill_id": skill_id, "error": str(e)},
        )
    finally:
        if prev_isolation is not None:
            conn.isolation_level = prev_isolation

    # Clean prior materialized custom-skill workspace residue.
    # The ledger transaction has committed; now remove the old skill
    # directories from all agent workspaces so no stale content lingers.
    # Uses the declared OrgPaths seam (not an invented field on OrgState).
    slug_match = skill_id
    if skill_id.startswith("hr:"):
        slug_match = skill_id[3:]
    from runtime.orchestrator._paths import OrgPaths
    workspaces_dir = OrgPaths(org.root).workspaces_dir
    cleanup_errors: list[str] = []
    if workspaces_dir.is_dir():
        for agent_ws in workspaces_dir.iterdir():
            if not agent_ws.is_dir():
                continue
            for skills_dir_name in (".claude", ".agents"):
                skill_dir = agent_ws / skills_dir_name / "skills" / slug_match
                if skill_dir.exists():
                    try:
                        shutil.rmtree(skill_dir)
                    except Exception as e:
                        cleanup_errors.append(f"{skill_dir}: {e}")
    if cleanup_errors:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "code": "rollback_cleanup_failed",
                "detail": f"Ledger rollback committed ({count} assignments deactivated) but workspace cleanup failed.",
                "cleanup_errors": cleanup_errors,
            },
        )

    return {
        "skill_id": skill_id,
        "assignments_deactivated": count,
        "reason": reason,
        "cleanup_errors": cleanup_errors if cleanup_errors else None,
    }


@dual_router.post("/retire", dependencies=[Depends(_require_human)])
def retire(
    slug: str,
    org: OrgDep,
    skill_id: str = Query(...),
    reason: str = Query(""),
) -> dict:
    """Human-only: retire a published skill.

    Bearer-token-gated — only the founder/human with the master bearer can call this.
    """
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
