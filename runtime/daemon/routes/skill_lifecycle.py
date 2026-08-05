"""THR-055 Custom-skill lifecycle routes — agent proposals + human lifecycle management.

All routes live on ``dual_router`` (dual-auth — bearer OR session-binding).
Human-only mutations are gated by ``_require_human()`` which returns 403 for
non-bearer (agent-session) callers. Human/founder uses master bearer token.

Routes:
- POST /skill-lifecycle/proposals — Human/founder-only (bearer required; agent → 403)
- POST /skill-lifecycle/proposals/agent — Agent-only: opaque session-binding, NO bearer, server-derived four-part provenance
- POST /skill-lifecycle/{skill_id}/claim — Human-only: claim proposal → draft
- POST /skill-lifecycle/validate — Human-only: validate a version
- POST /skill-lifecycle/submit-review — Human-only: submit for review
- POST /skill-lifecycle/review — Human-only: approve/reject
- POST /skill-lifecycle/publish — Human-only: publish
- POST /skill-lifecycle/assign — Human-only: assign to agent
- POST /skill-lifecycle/rollback — Human-only: emergency rollback (atomic)
- POST /skill-lifecycle/retire — Human-only: retire
- GET /skill-lifecycle/{skill_id} — Founder-only: read lifecycle status
- GET /skill-lifecycle/catalog/custom — Dual-auth: list published custom skills
- GET /skill-lifecycle/events/{skill_id} — Founder-only: read event history
- GET /skill-lifecycle/proposals/queue — Founder-only: paginated/filterable proposal queue
- GET /skill-lifecycle/proposals/{version_id} — Founder-only: full proposal detail
- POST /skill-lifecycle/proposals/{version_id}/claim — Founder-only: claim with concurrency
- POST /skill-lifecycle/proposals/{version_id}/validate — Founder-only: validate with concurrency
- POST /skill-lifecycle/proposals/{version_id}/review — Founder-only: review with concurrency
- POST /skill-lifecycle/proposals/{version_id}/publish — Founder-only: publish with concurrency
- POST /skill-lifecycle/proposals/{version_id}/assign — Founder-only: assign with concurrency
- POST /skill-lifecycle/proposals/{version_id}/rollback — Founder-only: rollback with concurrency

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

from fastapi import APIRouter, Body as RequestBody, Depends, HTTPException, Query, Request, status

from runtime.daemon.auth import _check_optional_token, optional_bearer, require_token
from runtime.daemon.org_state import OrgState
from runtime.daemon.routes._org_dep import OrgDep
from runtime.skills.lifecycle import stores as lifecycle_stores
from runtime.skills.lifecycle.models import (
    AssignProposalRequest,
    AssignRequest,
    ClaimProposalRequest,
    ClaimProposalV2Request,
    LifecycleStatus,
    ProposalRequest,
    PublishProposalRequest,
    PublishRequest,
    ReviewDecisionRequest,
    ReviewProposalRequest,
    RollbackProposalRequest,
    RollbackRequest,
    SubmitForReviewRequest,
    SubmitReviewProposalRequest,
    ValidateProposalRequest,
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


# ── Agent-id × canonical-slug pilot policy (THR-055 corrective) ───────────

# Fixed map: agent name → allowed skill slug.
# This is enforced server-side, BEFORE any artifact creation or ledger write.
# It does NOT inspect team membership, prompts, org config, YAML eligibility,
# request metadata, or body identity claims.
_AGENT_PILOT_SLUG_MAP: dict[str, str] = {
    "frontend_engineer": "frontend-development",
    "product_lead": "product-manager-prd",
}


def _enforce_agent_pilot_policy(agent_name: str, slug: str) -> None:
    """Enforce the fixed agent-id × canonical-slug policy.

    Raises 403 if:
    - agent_name is not in the pilot map
    - agent_name is in the pilot map but slug doesn't match its canonical slug

    This is called BEFORE any artifact creation or ledger write.
    """
    allowed_slug = _AGENT_PILOT_SLUG_MAP.get(agent_name)
    if allowed_slug is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "agent_not_in_pilot",
                "detail": f"Agent '{agent_name}' is not in the custom-skill pilot. "
                          f"Only frontend_engineer and product_lead may submit proposals.",
            },
        )
    if slug != allowed_slug:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "slug_not_allowed_for_agent",
                "detail": f"Agent '{agent_name}' may only submit proposals with slug "
                          f"'{allowed_slug}', not '{slug}'.",
            },
        )


# ═══════════════════════════════════════════════════════════════════════════
# Agent-only route (opaque session-binding, NO bearer token)
# ═══════════════════════════════════════════════════════════════════════════

# ── Prohibited body identity/authority keys ───────────────────────────
# The agent-only route MUST reject the *presence* of every client-supplied
# trusted identity or authority field (including empty strings) BEFORE any
# session lookup, pilot-policy evaluation, artifact write, or ledger/event
# row. Server derives the canonical org/task/agent/session solely from the
# active context-bearing SessionTracker session.
_PROHIBITED_BODY_KEYS: frozenset[str] = frozenset({
    # Fields already in ProposalRequest (reject presence, not truthiness)
    "task_id", "session_id", "proposer_agent",
    # Fields Pydantic would silently drop — must inspect raw body
    "org", "org_slug", "agent", "agent_name",
    "actor", "eligibility", "permission", "permissions",
})


@dual_router.post("/proposals/agent", status_code=201)
def submit_proposal_agent_only(
    slug: str,
    org: OrgDep,
    request: Request,
    body_raw: dict = RequestBody(..., description="Proposal package metadata and content (no identity fields)"),
    session_id: str = Query(..., min_length=1),
    has_bearer: bool = Depends(_check_optional_token),
) -> dict:
    """Submit a skill proposal via opaque agent-session binding.

    **Agent-only.** This route does NOT accept the master bearer token.
    The caller provides only an opaque active session ID; the server
    independently derives org, task_id, agent_name, and session_id from
    the SessionTracker context (four-part server-authoritative provenance).

    - All four identity dimensions (org, task, agent, session) are derived
      from the opaque session capability — never from body/query/env/client
      claims, task lookup by agent, team membership, or config/YAML.
    - Path-selected org is cross-checked against the session's org; cross-org
      and mismatched contexts are denied with 403.
    - The fixed agent-id × canonical-slug pilot policy is enforced
      BEFORE any artifact creation or ledger write.
    - Body claims for org, agent, task, session, proposer_agent,
      eligibility, or permission identity are rejected BEFORE model
      parsing — exact 403 body_identity_rejected for every prohibited
      key, including empty values and keys the Pydantic model would
      otherwise silently drop.

    Returns 403 for:
    - Inactive, expired, unknown, ambiguous, colliding, or mismatched session
    - Cross-org session (session belongs to a different org than the URL path)
    - Bearer token present (agent path only)
    - Any prohibited body identity/authority key present (including empty values)
    - Agent not in pilot
    - Agent submitting a slug not matching their canonical slug
    """
    # Reject bearer token — this route is agent-session ONLY
    if has_bearer:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "code": "bearer_not_accepted",
                "detail": "This route is for agent-session proposals only. "
                          "Use POST /skill-lifecycle/proposals for bearer-authenticated proposals.",
            },
        )

    # ── Reject ANY prohibited body identity/authority key BEFORE
    #    Pydantic parsing, session lookup, or any persistence. ──
    # This catches non-empty values, empty strings, and keys the
    # ProposalRequest model would otherwise silently drop (e.g., org,
    # agent_name, eligibility, permission).
    for key in _PROHIBITED_BODY_KEYS:
        if key in body_raw:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "body_identity_rejected",
                    "detail": f"{key} must not be set in the proposal body. "
                              "Identity is derived from the server's verified session context.",
                },
            )

    # Parse the now-clean body through Pydantic for normal field validation
    body = ProposalRequest(**body_raw)

    # Step 1: Resolve (org_slug, task_id, agent_name) from opaque session.
    # This is a read-only lookup under _lock with no binding lease held.
    # The resolved pair will be re-verified under the binding lease below.
    context = org.sessions.get_context_by_session(session_id)
    if context is not None:
        verified_org, task_id, agent_name = context
        # Cross-check: the path-selected org MUST match the session's org.
        # This prevents caller-controlled org routing from determining the
        # persistence org independently of the opaque session context.
        if verified_org != slug:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "cross_org_session",
                    "detail": f"Session {session_id} belongs to org '{verified_org}', "
                              f"not '{slug}'. Org is derived from the server's "
                              "verified session context, not caller-selected path.",
                },
            )
    else:
        # No org context for this session. Check whether the session
        # exists at all (active but without context) or is truly unknown.
        resolved = org.sessions.get_by_session(session_id)
        if resolved is None:
            # Truly unknown / inactive session
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "unknown_session",
                    "detail": f"No active session found for session_id '{session_id}'. "
                              "The session may be inactive, expired, or never existed.",
                },
            )
        # Session exists but missing org context — deny before any policy
        # check or artifact write. The agent-only route requires a current,
        # context-bearing session whose server-owned context supplies all
        # four dimensions (org, task, agent, session).
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "missing_org_context",
                "detail": f"Session {session_id} has no org context. "
                          "The agent-only proposal route requires a context-bearing "
                          "active session with all four dimensions (org, task, agent, session). "
                          "Use set_active with org_slug to establish context.",
            },
        )

    # Step 2 (test seam): pre-lease barrier — pause BEFORE the
    # per-binding lease is acquired so terminal-wins concurrency
    # tests can drive clear()/set_active() to completion while
    # the route is still in its initial resolution phase.
    pre_lease = org.sessions._pre_lease_barrier
    if pre_lease is not None:
        reached = org.sessions._pre_lease_barrier_reached
        if reached is not None:
            reached.set()
        pre_lease.wait()

    # Step 3: Acquire the per-binding lease for the resolved
    # (task_id, agent_name) to linearize authorization + persistence
    # against concurrent clear()/set_active() on the SAME binding.
    # Unrelated bindings are NOT blocked — each (task_id, agent) pair
    # has its own independent Lock.
    binding_lease = org.sessions._get_binding_lease(task_id, agent_name)
    with binding_lease:
        # Step 3a: Under the binding lease, re-verify the session is
        # still CURRENTLY active for the (task_id, agent_name) binding.
        # This catches concurrent clear()/set_active() that won the
        # race before the lease was acquired (Step 3).
        expected_session = org.sessions.get_active(task_id, agent_name)
        if expected_session != session_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "session_not_current",
                    "detail": f"Session {session_id} is not the current active session "
                              f"for task {task_id} agent {agent_name}. "
                              "The session may have been cleared, superseded, or revoked.",
                },
            )

        # Step 3b: Enforce agent-id × canonical-slug policy BEFORE any
        # artifact/ledger write.
        _enforce_agent_pilot_policy(agent_name, body.slug)

        # Step 3c (test seam): post-authorization barrier — pause
        # AFTER session revalidation + policy enforcement but BEFORE
        # persistence.  Tests use this to prove proposal-wins
        # interleavings: an already-authorized proposal is held
        # pending, same-binding terminal mutations demonstrably block,
        # and exactly one immutable proposal commits on release.
        barrier = org.sessions._proposal_barrier
        if barrier is not None:
            # Signal that the route has reached the barrier.
            reached = org.sessions._barrier_reached
            if reached is not None:
                reached.set()
            barrier.wait()

        try:
            protected_slugs = _get_protected_slugs(org)
            pkg = _service.submit_proposal_agent_direct(
                db=_get_db(org),
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
        "published": pkg.status == "published",
    }


# ═══════════════════════════════════════════════════════════════════════════
# Dual-auth routes (bearer OR session-binding)
# ═══════════════════════════════════════════════════════════════════════════

@dual_router.post("/proposals", status_code=410)
def submit_proposal(
    slug: str,
    org: OrgDep,
    body: ProposalRequest,
    request: Request,
    has_bearer: bool = Depends(_check_optional_token),
) -> dict:
    """[RETIRED — THR-136] Direct proposal creation is retired.

    Agent proposals now use POST /skill-lifecycle/proposals/agent which
    synchronously validates and publishes with no human review gate.
    Human/founder callers receive 410 Gone.
    """
    if not has_bearer:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "human_only",
                "detail": "Agent proposals must use the dedicated agent-only route: "
                          "POST /skill-lifecycle/proposals/agent.",
            },
        )
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail={
            "code": "route_retired_thr136",
            "detail": "Direct proposal creation is retired (THR-136). "
                      "Agent-authored proposals are now synchronously validated "
                      "and published via POST /skill-lifecycle/proposals/agent. "
                      "Historical proposal rows remain readable for provenance.",
            "replacement": "POST /skill-lifecycle/proposals/agent (agent-only)",
        },
    )


@dual_router.get("/{skill_id}", dependencies=[Depends(_require_human)])
def get_lifecycle_status(
    slug: str,
    skill_id: str,
    org: OrgDep,
    has_bearer: bool = Depends(_check_optional_token),
) -> dict:
    """Read the full lifecycle status for a skill.

    Founder-only. Returns current status, version, assignments, event history,
    and provenance. Agent callers receive 403.
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


@dual_router.get("/events/{skill_id}", dependencies=[Depends(_require_human)])
def get_events(
    slug: str,
    skill_id: str,
    org: OrgDep,
    limit: int = Query(100, ge=1, le=500),
    has_bearer: bool = Depends(_check_optional_token),
) -> dict:
    """Read event history for a skill.

    Founder-only. Agent callers receive 403.
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

@dual_router.post("/{skill_id}/claim", dependencies=[Depends(_require_human)], status_code=410)
def claim_proposal(
    slug: str,
    skill_id: str,
    org: OrgDep,
    body: ClaimProposalRequest,
) -> dict:
    """[RETIRED — THR-136] Claim proposal route retired.

    Agent proposals are now synchronously validated and published via
    POST /skill-lifecycle/proposals/agent. No human claim step is needed.
    """
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail={
            "code": "route_retired_thr136",
            "detail": "The claim proposal route is retired (THR-136). "
                      "Agent-authored proposals are now synchronously published "
                      "via POST /skill-lifecycle/proposals/agent with no human "
                      "review gate.",
            "replacement": "POST /skill-lifecycle/proposals/agent (agent-only)",
        },
    )


@dual_router.post("/validate", dependencies=[Depends(_require_human)], status_code=410)
def validate_version(
    slug: str,
    org: OrgDep,
    version_id: int = Query(...),
) -> dict:
    """[RETIRED — THR-136] Validation route retired.

    Agent proposals are now synchronously validated with a deterministic
    validator (THR-136/1.0.0) and published in a single atomic step via
    POST /skill-lifecycle/proposals/agent.
    """
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail={
            "code": "route_retired_thr136",
            "detail": "The validation route is retired (THR-136). "
                      "Agent proposals are now synchronously validated and "
                      "published via POST /skill-lifecycle/proposals/agent.",
            "replacement": "POST /skill-lifecycle/proposals/agent (agent-only)",
        },
    )


@dual_router.post("/submit-review", dependencies=[Depends(_require_human)], status_code=410)
def submit_for_review(
    slug: str,
    org: OrgDep,
    body: SubmitForReviewRequest,
) -> dict:
    """[RETIRED — THR-136] Submit-for-review route retired.

    Agent proposals are now synchronously validated and published in a
    single atomic step. No human submit-review step is needed.
    """
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail={
            "code": "route_retired_thr136",
            "detail": "The submit-for-review route is retired (THR-136). "
                      "Agent proposals are now synchronously validated and "
                      "published via POST /skill-lifecycle/proposals/agent.",
            "replacement": "POST /skill-lifecycle/proposals/agent (agent-only)",
        },
    )


@dual_router.post("/review", dependencies=[Depends(_require_human)], status_code=410)
def review_decision(
    slug: str,
    org: OrgDep,
    body: ReviewDecisionRequest,
) -> dict:
    """[RETIRED — THR-136] Review decision route retired.

    Agent proposals are now synchronously validated and published in a
    single atomic step via POST /skill-lifecycle/proposals/agent.
    No human review/approve/reject step is needed.
    """
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail={
            "code": "route_retired_thr136",
            "detail": "The review decision route is retired (THR-136). "
                      "Agent proposals are now synchronously validated and "
                      "published via POST /skill-lifecycle/proposals/agent.",
            "replacement": "POST /skill-lifecycle/proposals/agent (agent-only)",
        },
    )


@dual_router.post("/publish", dependencies=[Depends(_require_human)], status_code=410)
def publish(
    slug: str,
    org: OrgDep,
    body: PublishRequest,
) -> dict:
    """[RETIRED — THR-136] Publish route retired.

    Agent proposals are now published synchronously as part of the
    single atomic proposal-submission step via
    POST /skill-lifecycle/proposals/agent.
    """
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail={
            "code": "route_retired_thr136",
            "detail": "The publish route is retired (THR-136). "
                      "Agent proposals are now synchronously published "
                      "via POST /skill-lifecycle/proposals/agent.",
            "replacement": "POST /skill-lifecycle/proposals/agent (agent-only)",
        },
    )


@dual_router.post("/assign", dependencies=[Depends(_require_human)], status_code=410)
def assign_skill(
    slug: str,
    org: OrgDep,
    body: AssignRequest,
) -> dict:
    """[RETIRED — THR-136] Assignment route retired.

    Skill assignment is now managed exclusively through the catalog/detail
    per-agent assignment control surface. The proposal-specific assignment
    route is retired.
    """
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail={
            "code": "route_retired_thr136",
            "detail": "The proposal assignment route is retired (THR-136). "
                      "Skill assignment is now managed through the catalog/detail "
                      "per-agent assignment control.",
            "replacement": "Catalog/detail per-agent assignment control",
        },
    )


@dual_router.post("/rollback", dependencies=[Depends(_require_human)], status_code=410)
def rollback(
    slug: str,
    org: OrgDep,
    skill_id: str = Query(...),
    reason: str = Query(""),
    target_version_id: int | None = Query(None),
) -> dict:
    """[RETIRED — THR-136] Rollback route retired.

    Skill assignment deactivation is now managed through the catalog/detail
    per-agent assignment control surface.
    """
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail={
            "code": "route_retired_thr136",
            "detail": "The rollback route is retired (THR-136). "
                      "Assignment deactivation is now managed through the "
                      "catalog/detail per-agent assignment control.",
            "replacement": "Catalog/detail per-agent assignment control",
        },
    )


@dual_router.post("/retire", dependencies=[Depends(_require_human)], status_code=410)
def retire(
    slug: str,
    org: OrgDep,
    skill_id: str = Query(...),
    reason: str = Query(""),
) -> dict:
    """[RETIRED — THR-136] Retire route retired.

    Skill lifecycle management is now handled through the catalog/detail
    per-agent assignment control surface.
    """
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail={
            "code": "route_retired_thr136",
            "detail": "The retire route is retired (THR-136). "
                      "Skill lifecycle management is now handled through the "
                      "catalog/detail per-agent assignment control.",
            "replacement": "Catalog/detail per-agent assignment control",
        },
    )


# ═══════════════════════════════════════════════════════════════════════════
# THR-055 Founder-only proposal review routes
# ═══════════════════════════════════════════════════════════════════════════

def _run_concurrent_mutation(
    db, version_id: int, expected_event_id: int,
    mutation_fn, *,
    rollback_on_lifecycle: bool = True,
) -> dict:
    """Run a state-changing proposal action inside an atomic transaction.

    The concurrency check AND the mutation execute within the same
    ``BEGIN IMMEDIATE`` / ``COMMIT`` transaction so two identical-marker
    requests result in exactly one mutation success and one 409
    stale_concurrency.

    ``mutation_fn`` is called with the db parameter (inside the
    transaction). Its return value is returned to the caller.

    Raises HTTPException for stale_concurrency (409) or any LifecycleError
    the mutation may raise.
    """
    conn = db._conn if hasattr(db, '_conn') else db
    prev_isolation = getattr(conn, 'isolation_level', None)
    try:
        conn.isolation_level = None
        conn.execute("BEGIN IMMEDIATE")

        # Check concurrency INSIDE the transaction — this makes the
        # read + conditional-write atomic.
        try:
            _service.check_concurrency(db, version_id, expected_event_id)
        except LifecycleError as e:
            conn.execute("ROLLBACK")
            raise HTTPException(
                status_code=e.status_code,
                detail={"code": e.code, "detail": e.detail},
            )

        try:
            result = mutation_fn(db)
        except LifecycleError as e:
            if rollback_on_lifecycle:
                conn.execute("ROLLBACK")
            raise HTTPException(
                status_code=e.status_code,
                detail={"code": e.code, "detail": e.detail},
            )

        conn.execute("COMMIT")
        return result
    except HTTPException:
        raise
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        raise
    finally:
        if prev_isolation is not None:
            conn.isolation_level = prev_isolation

@dual_router.get("/proposals/queue", dependencies=[Depends(_require_human)], status_code=410)
def get_proposals_queue(
    slug: str,
    org: OrgDep,
    status_filter: str | None = Query(None, alias="status"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    validation_outcome: str | None = Query(None),
    search: str | None = Query(None),
    proposer: str | None = Query(None),
    submitted_after: str | None = Query(None),
    submitted_before: str | None = Query(None),
) -> dict:
    """[RETIRED — THR-136] Proposal queue route retired.

    The proposal review queue is retired. Agent-authored proposals are now
    synchronously published and visible immediately in the Skills catalog.
    Historical proposal rows remain readable via the existing lifecycle
    detail routes for provenance/audit.
    """
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail={
            "code": "route_retired_thr136",
            "detail": "The proposal queue route is retired (THR-136). "
                      "Published agent-authored skills are visible in the "
                      "Skills catalog. Historical proposals are readable "
                      "via lifecycle detail routes.",
            "replacement": "Skills catalog (GET /skill-lifecycle/catalog/custom)",
        },
    )


@dual_router.get("/proposals/{version_id}", dependencies=[Depends(_require_human)], status_code=410)
def get_proposal_detail(
    slug: str,
    version_id: int,
    org: OrgDep,
) -> dict:
    """[RETIRED — THR-136] Proposal detail route retired.

    The proposal detail page is retired. Published agent-authored skills
    are visible in the Skills catalog detail page with full provenance.
    Historical proposals remain readable via the existing lifecycle
    detail routes for provenance/audit.
    """
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail={
            "code": "route_retired_thr136",
            "detail": "The proposal detail route is retired (THR-136). "
                      "Published skills are visible in the Skills catalog. "
                      "Historical proposals are readable via lifecycle detail routes.",
            "replacement": "Skills catalog detail (GET /skill-lifecycle/{skill_id})",
        },
    )


@dual_router.post("/proposals/{version_id}/claim", dependencies=[Depends(_require_human)], status_code=410)
def claim_proposal_v2(
    slug: str,
    version_id: int,
    org: OrgDep,
    body: ClaimProposalV2Request,
) -> dict:
    """[RETIRED — THR-136] V2 claim proposal route retired.

    Agent proposals are now synchronously validated and published.
    No human claim step is needed.
    """
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail={
            "code": "route_retired_thr136",
            "detail": "The claim proposal route is retired (THR-136).",
            "replacement": "POST /skill-lifecycle/proposals/agent (agent-only)",
        },
    )


@dual_router.post("/proposals/{version_id}/validate", dependencies=[Depends(_require_human)], status_code=410)
def validate_proposal(
    slug: str,
    version_id: int,
    org: OrgDep,
    body: ValidateProposalRequest,
) -> dict:
    """[RETIRED — THR-136] V2 validate proposal route retired.

    Agent proposals are now synchronously validated with a deterministic
    validator (THR-136/1.0.0) and published in a single atomic step.
    """
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail={
            "code": "route_retired_thr136",
            "detail": "The validate proposal route is retired (THR-136).",
            "replacement": "POST /skill-lifecycle/proposals/agent (agent-only)",
        },
    )


@dual_router.post("/proposals/{version_id}/submit-review", dependencies=[Depends(_require_human)], status_code=410)
def submit_review_proposal(
    slug: str,
    version_id: int,
    org: OrgDep,
    body: SubmitReviewProposalRequest,
) -> dict:
    """[RETIRED — THR-136] V2 submit-review route retired."""
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail={
            "code": "route_retired_thr136",
            "detail": "The submit-review route is retired (THR-136).",
            "replacement": "POST /skill-lifecycle/proposals/agent (agent-only)",
        },
    )


@dual_router.post("/proposals/{version_id}/review", dependencies=[Depends(_require_human)], status_code=410)
def review_proposal(
    slug: str,
    version_id: int,
    org: OrgDep,
    body: ReviewProposalRequest,
) -> dict:
    """[RETIRED — THR-136] V2 review decision route retired."""
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail={
            "code": "route_retired_thr136",
            "detail": "The review decision route is retired (THR-136).",
            "replacement": "POST /skill-lifecycle/proposals/agent (agent-only)",
        },
    )


@dual_router.post("/proposals/{version_id}/publish", dependencies=[Depends(_require_human)], status_code=410)
def publish_proposal(
    slug: str,
    version_id: int,
    org: OrgDep,
    body: PublishProposalRequest,
) -> dict:
    """[RETIRED — THR-136] V2 publish proposal route retired."""
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail={
            "code": "route_retired_thr136",
            "detail": "The publish proposal route is retired (THR-136).",
            "replacement": "POST /skill-lifecycle/proposals/agent (agent-only)",
        },
    )


@dual_router.post("/proposals/{version_id}/assign", dependencies=[Depends(_require_human)], status_code=410)
def assign_proposal(
    slug: str,
    version_id: int,
    org: OrgDep,
    body: AssignProposalRequest,
) -> dict:
    """[RETIRED — THR-136] V2 assign proposal route retired.

    Skill assignment is now managed through the catalog/detail per-agent
    assignment control surface.
    """
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail={
            "code": "route_retired_thr136",
            "detail": "The assign proposal route is retired (THR-136).",
            "replacement": "Catalog/detail per-agent assignment control",
        },
    )


@dual_router.post("/proposals/{version_id}/rollback", dependencies=[Depends(_require_human)], status_code=410)
def rollback_proposal(
    slug: str,
    version_id: int,
    org: OrgDep,
    body: RollbackProposalRequest,
) -> dict:
    """[RETIRED — THR-136] V2 rollback proposal route retired.

    Assignment deactivation is now managed through the catalog/detail
    per-agent assignment control surface.
    """
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail={
            "code": "route_retired_thr136",
            "detail": "The rollback proposal route is retired (THR-136).",
            "replacement": "Catalog/detail per-agent assignment control",
        },
    )
