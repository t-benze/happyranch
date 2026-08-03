"""THR-055 SkillLifecycleService — the single writer for all lifecycle transitions.

Enforces the lifecycle state machine, agent-only proposal submission,
human-only lifecycle management, and the two-published-cap constraint.

Agent context is derived from verified session state (SessionTracker),
never from request body claims.

Package content retention follows the task-artifact policy: proposal SKILL.md
content is stored in the org ArtifactStore under
``skill-lifecycle/<slug>/<version>/SKILL.md``; the ledger stores the
``content_artifact_key`` reference rather than unbounded inline bytes.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from . import stores
from .models import (
    AssignmentRecord,
    LifecycleEvent,
    LifecycleStatus,
    MaterializationRecord,
    PackageVersion,
    utcnow,
)

# ── Pilot caps ────────────────────────────────────────────────────────────

MAX_PUBLISHED_SKILLS = 2  # Maximum concurrently published custom skills
ALLOWED_POLICY_CLASSES = frozenset({"standard_operational"})

# ── Protected slugs — cannot be claimed by custom skills ──────────────────

_PROTECTED_SLUGS = frozenset({
    "start-task", "jobs", "make-worktree", "thread", "dream",
    "reflection", "manage-agent", "manage-repo", "brainstorming",
    "dispatching-parallel-agents", "executing-plans",
    "finishing-a-development-branch", "receiving-code-review",
    "requesting-code-review", "subagent-driven-development",
    "systematic-debugging", "test-driven-development",
    "using-git-worktrees", "using-superpowers",
    "verification-before-completion", "writing-plans", "writing-skills",
})


class LifecycleError(Exception):
    """Raised when a lifecycle transition is invalid."""
    def __init__(self, code: str, detail: str, status_code: int = 409):
        self.code = code
        self.detail = detail
        self.status_code = status_code
        super().__init__(detail)


class AgentForbiddenError(LifecycleError):
    """403 — Agent tried to perform a human-only action."""
    def __init__(self, action: str):
        super().__init__(
            code="agent_forbidden",
            detail=f"Agent callers may not perform action '{action}'. Only human lifecycle actors may do this.",
            status_code=403,
        )


# ── Helpers ──────────────────────────────────────────────────────────────


def _get_raw_connection(db):
    """Extract the raw sqlite3.Connection from either a raw connection
    or a Database wrapper (which stores it as ``_conn``)."""
    if hasattr(db, '_conn'):
        return db._conn
    return db


def _persist_package_to_artifact_store(
    store, slug: str, skill_md: str,
    references: dict[str, str],
    assets: dict[str, str],
) -> tuple[str, str, list[str]]:
    """Persist all package members (SKILL.md, references, assets) to the
    ArtifactStore as content-addressed immutable artifacts, then build and
    store a manifest artifact.

    Returns (package_content_hash, manifest_artifact_key, new_artifact_keys)
    where package_content_hash is the SHA-256 of the manifest JSON,
    manifest_artifact_key is the artifact key of the manifest, and
    new_artifact_keys tracks all artifact keys we created (for rollback).
    """
    import json

    members: list[dict] = []
    new_keys: list[str] = []
    prefix = f"skill-lifecycle/{slug}"

    def _put_if_new(artifact_key: str, content: bytes) -> None:
        """Put content into ArtifactStore; track if newly created."""
        try:
            store.read(artifact_key)
        except Exception:
            new_keys.append(artifact_key)
        store.put(artifact_key, content)

    def _compensate_new_keys() -> None:
        """Delete every artifact we created during this call (best-effort).

        Pre-existing dedup artifacts (not in ``new_keys``) are never touched.
        """
        for key in new_keys:
            try:
                store.delete(key)
            except Exception:
                pass

    try:
        # ── Store SKILL.md ───────────────────────────────────────────
        skill_md_bytes = skill_md.encode("utf-8")
        skill_hash = hashlib.sha256(skill_md_bytes).hexdigest()
        skill_key = f"{prefix}/{skill_hash[:16]}/SKILL.md"
        _put_if_new(skill_key, skill_md_bytes)
        members.append({
            "path": "SKILL.md",
            "hash": f"sha256:{skill_hash}",
            "artifact_key": skill_key,
            "size_bytes": len(skill_md_bytes),
        })

        # ── Store references ─────────────────────────────────────────
        for rel_path, content in sorted(references.items()):
            content_bytes = content.encode("utf-8")
            ref_hash = hashlib.sha256(content_bytes).hexdigest()
            ref_key = f"{prefix}/{ref_hash[:16]}/references/{rel_path}"
            _put_if_new(ref_key, content_bytes)
            members.append({
                "path": f"references/{rel_path}",
                "hash": f"sha256:{ref_hash}",
                "artifact_key": ref_key,
                "size_bytes": len(content_bytes),
            })

        # ── Store assets ─────────────────────────────────────────────
        for rel_path, content in sorted(assets.items()):
            content_bytes = content.encode("utf-8")
            asset_hash = hashlib.sha256(content_bytes).hexdigest()
            asset_key = f"{prefix}/{asset_hash[:16]}/assets/{rel_path}"
            _put_if_new(asset_key, content_bytes)
            members.append({
                "path": f"assets/{rel_path}",
                "hash": f"sha256:{asset_hash}",
                "artifact_key": asset_key,
                "size_bytes": len(content_bytes),
            })

        # ── Build and store manifest ─────────────────────────────────
        manifest = {
            "schema_version": 1,
            "skill_id": f"hr:{slug}",
            "slug": slug,
            "members": members,
        }
        manifest_json = json.dumps(manifest, sort_keys=True, indent=2)
        manifest_bytes = manifest_json.encode("utf-8")
        manifest_hash = hashlib.sha256(manifest_bytes).hexdigest()
        manifest_key = f"{prefix}/{manifest_hash[:16]}/manifest.json"
        _put_if_new(manifest_key, manifest_bytes)
        new_keys.append(manifest_key)  # Manifest is always "new" for this call

        # The package content_hash is the manifest's SHA-256.
        # This binds the full-package provenance (not just SKILL.md).
        return manifest_hash, manifest_key, new_keys
    except Exception:
        # Compensate every artifact we successfully wrote before the failure.
        # Pre-existing dedup artifacts are NOT in new_keys and are preserved.
        _compensate_new_keys()
        raise


# ── Service ───────────────────────────────────────────────────────────────

class SkillLifecycleService:
    """Single writer for custom-skill lifecycle operations.

    All methods accept an explicit `db`, `actor_kind` ("agent"|"human"),
    and optional task/session context for agent proposals.
    """

    def __init__(self):
        pass

    # ── Agent proposal submission ─────────────────────────────────────────

    def submit_proposal(
        self,
        db,
        actor_kind: str,
        slug: str,
        name: str,
        description: str,
        skill_md: str,
        version: str = "0.1.0",
        policy_class: str = "standard_operational",
        references: dict[str, str] | None = None,
        assets: dict[str, str] | None = None,
        task_id: str | None = None,
        session_id: str | None = None,
        proposer_agent: str | None = None,
        purpose: str = "",
        target_agent_suggestion: str = "",
        protected_slugs: frozenset | None = None,
        org_root: Path | str | None = None,
    ) -> PackageVersion:
        """Submit a task/session-bound proposal.

        Both agents (via verified task/session) and humans (via bearer token)
        may submit proposals. Proposal identity derives from verified
        task/session when agent, or from bearer when human.

        Package content is fully retained in the org ArtifactStore:
        - SKILL.md, each reference, and each asset stored as independent
          content-addressed immutable artifacts.
        - A manifest artifact lists all members with normalized paths,
          hashes, and artifact keys.
        - The package content_hash binds the full manifest (distinct from
          individual member hashes).
        - The ledger stores only metadata; artifact store is canonical.

        Constraints:
        - slug must not collide with protected slugs
        - policy_class must be standard_operational
        - task_id + session_id required for agent proposals
        - All member paths must be safe (no traversal, no absolute paths)
        """
        self._ensure_non_empty(skill_md, "skill_md")
        self._ensure_protected_slug(slug, protected_slugs)
        self._ensure_policy_class(policy_class)

        # Validate safe paths for all package members
        refs = references or {}
        asts = assets or {}
        self._validate_safe_paths(slug, refs, asts)

        # Agent proposals require verified task/session binding.
        if actor_kind == "agent" and (not task_id or not session_id):
            raise LifecycleError(
                code="missing_session_binding",
                detail="Agent proposals require verified task_id + session_id binding.",
                status_code=400,
            )

        skill_id = f"hr:{slug}"

        # Persist all package members to ArtifactStore under content-addressed
        # immutable keys, then build a manifest artifact. The manifest's hash
        # becomes the package content_hash (binding full package provenance).
        content_artifact_key: str | None = None
        content_hash: str
        new_artifact_keys: list[str] = []  # Track artifacts we created
        if org_root is not None:
            try:
                from runtime.infrastructure.artifact_store import ArtifactStore
                from runtime.orchestrator._paths import OrgPaths

                org_root_path = Path(org_root) if not isinstance(org_root, Path) else org_root
                store = ArtifactStore(OrgPaths(org_root_path).artifacts_dir)

                # Build manifest by storing each member independently
                content_hash, content_artifact_key, new_artifact_keys = (
                    _persist_package_to_artifact_store(
                        store, slug, skill_md, refs, asts,
                    )
                )
            except LifecycleError:
                raise
            except Exception as exc:
                raise LifecycleError(
                    code="artifact_store_failed",
                    detail=f"Failed to persist proposal to artifact store: {exc}",
                    status_code=500,
                ) from exc
        else:
            content_hash = PackageVersion.compute_content_hash(skill_md)

        # Check for duplicate content hash (idempotency)
        existing = stores.get_package_version_by_hash(db, skill_id, content_hash)
        if existing is not None:
            return existing  # Idempotent — return existing proposal

        # Wrap ledger writes in an explicit transaction so package + event
        # rows commit or roll back together atomically.
        conn = _get_raw_connection(db)
        prev_isolation = getattr(conn, 'isolation_level', 'auto')
        try:
            conn.isolation_level = None
            conn.execute("BEGIN IMMEDIATE")

            pkg = PackageVersion(
                skill_id=skill_id,
                slug=slug,
                name=name,
                version=version,
                content_hash=content_hash,
                policy_class=policy_class,
                description=description,
                skill_md="",  # Artifact store is canonical
                content_artifact_key=content_artifact_key,
                status=LifecycleStatus.PROPOSED,
                created_by=proposer_agent or "",
                proposal_task_id=task_id,
                proposal_session_id=session_id,
                proposer_agent=proposer_agent,
            )

            version_id = stores.insert_package_version(db, pkg)
            pkg.id = version_id

            stores.insert_lifecycle_event(db, LifecycleEvent(
                skill_id=skill_id,
                package_version_id=version_id,
                event_type="proposed",
                actor=proposer_agent or "",
                actor_role="agent",
                previous_status=None,
                new_status=LifecycleStatus.PROPOSED.value,
                content_hash=content_hash,
                metadata={
                    "purpose": purpose,
                    "target_agent_suggestion": target_agent_suggestion,
                    "content_artifact_key": content_artifact_key,
                },
                task_id=task_id,
                session_id=session_id,
            ))

            conn.execute("COMMIT")
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
            # Clean up artifacts ONLY we created (never delete pre-existing).
            if org_root is not None:
                try:
                    org_root_path = Path(org_root) if not isinstance(org_root, Path) else org_root
                    from runtime.infrastructure.artifact_store import ArtifactStore
                    from runtime.orchestrator._paths import OrgPaths
                    cleanup_store = ArtifactStore(OrgPaths(org_root_path).artifacts_dir)
                    for key in new_artifact_keys:
                        try:
                            cleanup_store.delete(key)
                        except Exception:
                            pass
                except Exception:
                    pass
            raise
        finally:
            conn.isolation_level = prev_isolation

        return pkg

    def _validate_safe_paths(
        self, slug: str, references: dict[str, str], assets: dict[str, str],
    ) -> None:
        """Validate that all reference and asset paths are safe.

        Rejects paths with '..' traversal, absolute paths (starting with /),
        and empty names.
        """
        for rel_path in list(references.keys()) + list(assets.keys()):
            if not rel_path or not rel_path.strip():
                raise LifecycleError(
                    code="unsafe_path",
                    detail=f"Member path must not be empty.",
                    status_code=400,
                )
            if rel_path.startswith("/") or "\\" in rel_path:
                raise LifecycleError(
                    code="unsafe_path",
                    detail=f"Member path '{rel_path}' must be relative.",
                    status_code=400,
                )
            if ".." in rel_path.split("/"):
                raise LifecycleError(
                    code="unsafe_path",
                    detail=f"Member path '{rel_path}' contains '..' traversal.",
                    status_code=400,
                )

    # ── Claim proposal → draft ────────────────────────────────────────────

    def claim_proposal(
        self, db, actor_kind: str, version_id: int, sponsor: str = "founder",
    ) -> PackageVersion:
        """A human sponsor claims an agent proposal, making it a draft.

        Preserves immutable agent proposer (created_by/proposer_agent).
        The founder claim is a SEPARATE claimed_by + claimed_at timestamp —
        never a rewrite of the original author identity.
        """
        self._ensure_human(actor_kind, "claim proposal")
        pkg = self._get_package(db, version_id)
        self._ensure_not_rejected(pkg)

        if pkg.status not in (LifecycleStatus.PROPOSED,):
            raise LifecycleError(
                code="invalid_transition",
                detail=f"Cannot claim a package in status '{pkg.status.value}'. Only PROPOSED packages can be claimed.",
            )

        now = utcnow()
        # Record claimant separately — never overwrite created_by/proposer_agent
        stores.update_package_claimed(db, version_id, sponsor, now)
        stores.update_package_status(db, version_id, LifecycleStatus.DRAFT)
        pkg.status = LifecycleStatus.DRAFT
        pkg.claimed_by = sponsor
        pkg.claimed_at = now

        stores.insert_lifecycle_event(db, LifecycleEvent(
            skill_id=pkg.skill_id,
            package_version_id=version_id,
            event_type="drafted",
            actor=sponsor,
            actor_role="human",
            previous_status=LifecycleStatus.PROPOSED.value,
            new_status=LifecycleStatus.DRAFT.value,
            content_hash=pkg.content_hash,
        ))

        return pkg

    # ── Validate ──────────────────────────────────────────────────────────

    def record_validation(
        self, db, actor_kind: str, version_id: int, ok: bool,
        findings: list[str] | None = None,
        validator_version: str | None = None,
        validator_key: str | None = None,
    ) -> PackageVersion:
        """Record validation result for a draft version.

        Validation is reproducible for an immutable package:
        - content_hash is already part of the immutable PackageVersion record
        - validator_version identifies the validator implementation
          (e.g. "THR-055/1.0.0")
        - validator_key is a stable deterministic identifier for this
          specific validator run (distinct from the event id — the event
          id is a distinct run/event identifier)
        - Re-runs append new events rather than overwriting history
        - previous_status is captured BEFORE any status update so the event
          accurately records the prior state for every invocation
        """
        self._ensure_human(actor_kind, "record validation")
        pkg = self._get_package(db, version_id)
        self._ensure_not_rejected(pkg)

        if pkg.status not in (LifecycleStatus.DRAFT, LifecycleStatus.VALIDATION_FAILED, LifecycleStatus.VALIDATED):
            raise LifecycleError(
                code="invalid_transition",
                detail=f"Cannot validate a package in status '{pkg.status.value}'. Only DRAFT/VALIDATION_FAILED/VALIDATED packages can be validated.",
            )

        # Capture previous_status BEFORE any status update — the event must
        # record the accurate prior state, not the new state.
        previous_status = pkg.status.value

        # validator_version is MANDATORY — reject blank/missing values.
        # Every validation path (legacy and v2) must supply a deterministic
        # version identifier. The documented deterministic validator key
        # derivation is only valid when a nonblank version is present.
        if not validator_version or not validator_version.strip():
            raise LifecycleError(
                code="missing_validator_version",
                detail="validator_version is mandatory for every validation. "
                       "Supply a deterministic version identifier (e.g. 'LEGACY/1.0.0' or 'THR-055/1.0.0').",
                status_code=400,
            )

        new_status = LifecycleStatus.VALIDATED if ok else LifecycleStatus.VALIDATION_FAILED
        stores.update_package_status(db, version_id, new_status)
        pkg.status = new_status

        meta: dict = {"findings": findings or []}
        meta["validator_version"] = validator_version
        if validator_key:
            meta["validator_key"] = validator_key
        else:
            meta["validator_key"] = validator_version
        # Include immutable content_hash for reproducibility
        meta["content_hash"] = pkg.content_hash

        stores.insert_lifecycle_event(db, LifecycleEvent(
            skill_id=pkg.skill_id,
            package_version_id=version_id,
            event_type="validated" if ok else "validation_failed",
            actor="validator",
            actor_role="service",
            previous_status=previous_status,
            new_status=new_status.value,
            content_hash=pkg.content_hash,
            metadata=meta,
        ))

        return pkg

    # ── Submit for review ─────────────────────────────────────────────────

    def submit_for_review(
        self, db, actor_kind: str, version_id: int, sponsor: str = "founder",
        intended_audience: str = "", review_notes: str = "",
    ) -> PackageVersion:
        """Submit a validated version for human review."""
        self._ensure_human(actor_kind, "submit for review")
        pkg = self._get_package(db, version_id)
        self._ensure_not_rejected(pkg)

        if pkg.status != LifecycleStatus.VALIDATED:
            raise LifecycleError(
                code="invalid_transition",
                detail=f"Can only submit VALIDATED packages for review, not '{pkg.status.value}'.",
            )

        previous_status = pkg.status.value
        stores.update_package_status(db, version_id, LifecycleStatus.IN_REVIEW)
        pkg.status = LifecycleStatus.IN_REVIEW

        stores.insert_lifecycle_event(db, LifecycleEvent(
            skill_id=pkg.skill_id,
            package_version_id=version_id,
            event_type="submitted_for_review",
            actor=sponsor,
            actor_role="human",
            previous_status=previous_status,
            new_status=LifecycleStatus.IN_REVIEW.value,
            content_hash=pkg.content_hash,
            metadata={
                "intended_audience": intended_audience,
                "review_notes": review_notes,
            },
        ))

        return pkg

    # ── Submit for review v2 (concurrency-protected) ───────────────────

    def submit_review_proposal(
        self, db, actor_kind: str, version_id: int, sponsor: str = "founder",
        intended_audience: str = "", review_notes: str = "",
    ) -> PackageVersion:
        """V2 submit-for-review (Founder-only, concurrency-protected).

        Identical semantics to submit_for_review but intended for the v2
        proposal-scoped route that includes expected_event_id.
        """
        return self.submit_for_review(
            db, actor_kind, version_id, sponsor, intended_audience, review_notes,
        )

    # ── Approve / reject ──────────────────────────────────────────────────

    def review_decision(
        self, db, actor_kind: str, version_id: int,
        decision: str, rationale: str, reviewer: str = "founder",
    ) -> PackageVersion:
        """Reviewer approves or rejects a submitted version.

        Reviewer must be distinct from author (maker-checker).

        REJECTED is a TERMINAL status — after rejection, every later claim,
        validation, review/approval, publish, assign, materialization,
        rollback/reopen/recovery attempt on that proposal/version is blocked.
        Rejection retains immutable package, all evidence, actor/time/rationale,
        and append-only history. A future change is a new proposal/version only.
        """
        self._ensure_human(actor_kind, "review")
        pkg = self._get_package(db, version_id)
        self._ensure_not_rejected(pkg)

        if pkg.status != LifecycleStatus.IN_REVIEW:
            raise LifecycleError(
                code="invalid_transition",
                detail=f"Can only review packages IN_REVIEW, not '{pkg.status.value}'.",
            )

        if decision not in ("approved", "rejected"):
            raise LifecycleError(
                code="invalid_decision",
                detail=f"Review decision must be 'approved' or 'rejected', got {decision!r}.",
                status_code=400,
            )

        # Maker-checker: reviewer must be distinct from proposer_agent (the immutable author)
        author = pkg.proposer_agent or pkg.created_by
        if reviewer == author and author:
            raise LifecycleError(
                code="reviewer_author_separation",
                detail=f"Reviewer '{reviewer}' must be distinct from author '{author}'.",
            )

        new_status = LifecycleStatus.APPROVED if decision == "approved" else LifecycleStatus.REJECTED
        stores.update_package_status(
            db, version_id, new_status,
            reviewer=reviewer,
            review_decision=decision,
            review_rationale=rationale,
            reviewed_at=utcnow(),
        )
        pkg.status = new_status
        pkg.reviewer = reviewer
        pkg.review_decision = decision
        pkg.review_rationale = rationale

        event = stores.insert_lifecycle_event(db, LifecycleEvent(
            skill_id=pkg.skill_id,
            package_version_id=version_id,
            event_type=decision,
            actor=reviewer,
            actor_role="reviewer",
            previous_status=LifecycleStatus.IN_REVIEW.value,
            new_status=new_status.value,
            content_hash=pkg.content_hash,
            metadata={"rationale": rationale},
        ))

        if decision == "approved":
            # Store the approval event id for publish gating
            stores.update_package_status(
                db, version_id, new_status,
                publication_decision_id=event,
            )
            pkg.publication_decision_id = event

        return pkg

    # ── Publish ───────────────────────────────────────────────────────────

    def publish(
        self, db, actor_kind: str, version_id: int,
        approval_event_id: int, publisher: str = "founder",
    ) -> PackageVersion:
        """Publish an approved version to the custom catalog.

        Enforces the two-published-cap.
        Requires matching approval event id as proof of review.
        """
        self._ensure_human(actor_kind, "publish")
        pkg = self._get_package(db, version_id)
        self._ensure_not_rejected(pkg)

        if pkg.status != LifecycleStatus.APPROVED:
            raise LifecycleError(
                code="invalid_transition",
                detail=f"Can only publish APPROVED packages, not '{pkg.status.value}'.",
            )

        # Verify approval event matches
        if pkg.publication_decision_id != approval_event_id:
            raise LifecycleError(
                code="approval_mismatch",
                detail=f"Approval event id {approval_event_id} does not match package's approval event {pkg.publication_decision_id}.",
            )

        # Enforce two-published cap
        current_published = stores.count_published_packages(db)
        already_published = stores.list_package_versions(
            db, skill_id=pkg.skill_id, status=LifecycleStatus.PUBLISHED
        )
        if not already_published and current_published >= MAX_PUBLISHED_SKILLS:
            raise LifecycleError(
                code="publish_cap_exceeded",
                detail=f"Maximum {MAX_PUBLISHED_SKILLS} concurrently published custom skills reached. Retire an existing skill first.",
            )

        now = utcnow()
        stores.update_package_status(
            db, version_id, LifecycleStatus.PUBLISHED,
            publisher=publisher,
            published_at=now,
        )
        pkg.status = LifecycleStatus.PUBLISHED
        pkg.publisher = publisher
        pkg.published_at = now

        stores.insert_lifecycle_event(db, LifecycleEvent(
            skill_id=pkg.skill_id,
            package_version_id=version_id,
            event_type="published",
            actor=publisher,
            actor_role="publisher",
            previous_status=LifecycleStatus.APPROVED.value,
            new_status=LifecycleStatus.PUBLISHED.value,
            content_hash=pkg.content_hash,
        ))

        return pkg

    # ── Assign ────────────────────────────────────────────────────────────

    def assign(
        self, db, actor_kind: str, skill_id: str, agent_name: str,
        version_id: int, assigner: str = "founder",
    ) -> AssignmentRecord:
        """Assign a published version to a named agent."""
        self._ensure_human(actor_kind, "assign")
        pkg = self._get_package(db, version_id)
        self._ensure_not_rejected(pkg)

        if pkg.status != LifecycleStatus.PUBLISHED:
            raise LifecycleError(
                code="invalid_transition",
                detail=f"Can only assign PUBLISHED packages, not '{pkg.status.value}'.",
            )

        if pkg.skill_id != skill_id:
            raise LifecycleError(
                code="skill_id_mismatch",
                detail=f"Version {version_id} has skill_id '{pkg.skill_id}', not '{skill_id}'.",
                status_code=400,
            )

        # Check for existing active assignment (replace)
        existing = stores.get_active_assignment(db, skill_id, agent_name)
        if existing is not None:
            stores.deactivate_assignment(db, skill_id, agent_name, unassigned_by=assigner)

        assign_record = AssignmentRecord(
            skill_id=skill_id,
            agent_name=agent_name,
            package_version_id=version_id,
            version=pkg.version,
            content_hash=pkg.content_hash,
            assigned_by=assigner,
        )

        assign_id = stores.insert_assignment(db, assign_record)
        assign_record.id = assign_id

        stores.insert_lifecycle_event(db, LifecycleEvent(
            skill_id=skill_id,
            package_version_id=version_id,
            event_type="assigned",
            actor=assigner,
            actor_role="human",
            previous_status=None,
            new_status="assigned_not_yet_effective",
            content_hash=pkg.content_hash,
            metadata={"agent_name": agent_name},
        ))

        return assign_record

    # ── Rollback (emergency unassign) ─────────────────────────────────────

    def rollback(
        self, db, actor_kind: str, skill_id: str,
        reason: str, rolled_back_by: str = "founder",
        target_version_id: int | None = None,
    ) -> int:
        """Emergency rollback: deactivate assignments for a skill.

        Assignment-level operation only — does NOT mutate package decision
        status. Package lifecycle ends at published (or rejected terminal);
        assignment/unassignment is a separate append-only projection.

        When target_version_id is supplied (v2 route), acts ONLY on
        assignments to that exact version. If that version is REJECTED,
        returns rejected_terminal with NO assignment/event mutation.

        When target_version_id is NOT supplied (legacy route), inspects
        every active assignment: if any points to a REJECTED package
        version the entire request is rejected before any mutation.

        The caller (HTTP handler) must wrap this in BEGIN IMMEDIATE/COMMIT.

        Returns count of deactivated assignments.
        """
        self._ensure_human(actor_kind, "rollback")

        if target_version_id is not None:
            # V2 exact-version rollback: act only on the addressed version.
            pkg = stores.get_package_version(db, target_version_id)
            if pkg is None:
                raise LifecycleError(
                    code="not_found",
                    detail=f"No package version found with id {target_version_id}.",
                    status_code=404,
                )
            if pkg.skill_id != skill_id:
                raise LifecycleError(
                    code="skill_id_mismatch",
                    detail=f"Version {target_version_id} belongs to skill '{pkg.skill_id}', not '{skill_id}'.",
                    status_code=400,
                )
            if pkg.status == LifecycleStatus.REJECTED:
                # Exact-version rejected: return rejected_terminal with NO mutation.
                raise LifecycleError(
                    code="rejected_terminal",
                    detail=f"Proposal version {target_version_id} is terminally REJECTED. "
                           f"No rollback is permitted on a rejected version.",
                    status_code=409,
                )
        else:
            # Legacy rollback (no version_id): inspect every active assignment.
            # If ANY active assignment points to a REJECTED package version,
            # reject the entire request before any mutation.
            if stores.has_active_assignment_on_rejected_version(db, skill_id):
                raise LifecycleError(
                    code="rejected_terminal",
                    detail=f"Skill '{skill_id}' has at least one active assignment on a "
                           f"terminally REJECTED version. Rollback would mutate a rejected "
                           f"assignment — rejected. Use a version-addressed rollback instead.",
                    status_code=409,
                )

            # For legacy path without a rejected assignment, still ensure we
            # don't use latest as a misplaced authorization proxy.
            pkg = stores.get_latest_package_version(db, skill_id)
            if pkg is not None:
                self._ensure_not_rejected(pkg)

        count = stores.deactivate_assignments_for_skill(
            db, skill_id,
            rolled_back_by=rolled_back_by,
            reason=reason,
            target_version_id=target_version_id,
        )

        # Use the addressed version for event metadata.
        event_version_id = target_version_id
        event_content_hash = None
        if event_version_id is not None:
            addressed_pkg = stores.get_package_version(db, event_version_id)
        else:
            addressed_pkg = stores.get_latest_package_version(db, skill_id)
        if addressed_pkg is not None:
            event_version_id = addressed_pkg.id
            event_content_hash = addressed_pkg.content_hash

        stores.insert_lifecycle_event(db, LifecycleEvent(
            skill_id=skill_id,
            package_version_id=event_version_id,
            event_type="rolled_back",
            actor=rolled_back_by,
            actor_role="human",
            previous_status=None,  # Assignment projection — does not touch package status
            new_status=None,
            content_hash=event_content_hash,
            metadata={
                "reason": reason,
                "assignments_deactivated": count,
                "target_version_id": target_version_id,
            },
        ))

        return count

    # ── Retire ────────────────────────────────────────────────────────────

    def retire(
        self, db, actor_kind: str, skill_id: str, reason: str,
        retired_by: str = "founder",
    ) -> PackageVersion:
        """Retire a published skill. Stops future materialization.

        Assignment-level operation only — does NOT mutate package decision
        status. Package lifecycle ends at published (or rejected terminal);
        assignment/unassignment is a separate append-only projection.
        Only PUBLISHED packages may be retired; REJECTED and all non-PUBLISHED
        states are rejected.

        Legacy path (skill_id only): inspects every active assignment.
        If ANY active assignment points to a REJECTED package version,
        the entire request is rejected before any mutation.
        """
        self._ensure_human(actor_kind, "retire")

        # Inspect active assignments: if any point to a REJECTED version,
        # reject before any mutation. The 'latest version' is NOT an
        # authorization proxy for a version-addressed action.
        if stores.has_active_assignment_on_rejected_version(db, skill_id):
            raise LifecycleError(
                code="rejected_terminal",
                detail=f"Skill '{skill_id}' has at least one active assignment on a "
                       f"terminally REJECTED version. Retire would mutate a rejected "
                       f"assignment — rejected.",
                status_code=409,
            )

        pkg = stores.get_latest_package_version(db, skill_id)
        if pkg is None:
            raise LifecycleError(
                code="not_found",
                detail=f"No skill found for skill_id '{skill_id}'.",
                status_code=404,
            )

        self._ensure_not_rejected(pkg)
        if pkg.status != LifecycleStatus.PUBLISHED:
            raise LifecycleError(
                code="invalid_transition",
                detail=f"Can only retire PUBLISHED packages, not '{pkg.status.value}'.",
            )

        # Deactivate all assignments — do NOT mutate package status.
        # Package lifecycle ends at published; assignment deactivation
        # is a separate append-only projection.
        count = stores.deactivate_assignments_for_skill(
            db, skill_id, rolled_back_by=retired_by, reason=reason,
        )

        stores.insert_lifecycle_event(db, LifecycleEvent(
            skill_id=skill_id,
            package_version_id=pkg.id,
            event_type="retired",
            actor=retired_by,
            actor_role="human",
            previous_status=pkg.status.value,
            new_status=None,  # Assignment projection — does not mutate package status
            content_hash=pkg.content_hash,
            metadata={"reason": reason, "assignments_deactivated": count},
        ))

        return pkg

    # ── Materialization ───────────────────────────────────────────────────

    def record_materialization(
        self, db, skill_id: str, agent_name: str, version_id: int,
        version: str, content_hash: str, success: bool, error_message: str | None = None,
        session_context: str | None = None,
    ) -> MaterializationRecord:
        """Record a skill materialization attempt at session spawn.

        Guarded: materialization must not proceed for terminally REJECTED packages.
        """
        # Check rejected terminal gate — materialization must not proceed for REJECTED.
        pkg = stores.get_package_version(db, version_id)
        if pkg is not None:
            # Rejection is terminal — no materialization event for rejected packages.
            if pkg.status == LifecycleStatus.REJECTED:
                raise LifecycleError(
                    code="rejected_terminal",
                    detail=f"Package version {version_id} is terminally REJECTED. "
                           f"No materialization is permitted.",
                    status_code=409,
                )

        mat = MaterializationRecord(
            skill_id=skill_id,
            agent_name=agent_name,
            package_version_id=version_id,
            version=version,
            content_hash=content_hash,
            success=success,
            error_message=error_message,
            session_context=session_context,
        )
        mat_id = stores.insert_materialization(db, mat)
        mat.id = mat_id

        stores.insert_lifecycle_event(db, LifecycleEvent(
            skill_id=skill_id,
            package_version_id=version_id,
            event_type="materialized" if success else "materialization_failed",
            actor="runtime",
            actor_role="service",
            previous_status=None,
            new_status="effective" if success else "materialization_failed",
            content_hash=content_hash,
            metadata={
                "agent_name": agent_name,
                "session_context": session_context,
                "error_message": error_message,
            },
        ))

        return mat

    # ── Read / query ──────────────────────────────────────────────────────

    def get_status(self, db, skill_id: str) -> dict:
        """Get the full lifecycle status for a skill."""
        pkg = stores.get_latest_package_version(db, skill_id)
        events = stores.list_lifecycle_events(db, skill_id=skill_id)
        assignments = stores.get_all_active_assignments_for_skill(db, skill_id)

        return {
            "skill_id": skill_id,
            "slug": pkg.slug if pkg else "",
            "current_status": pkg.status if pkg else None,
            "current_version": pkg.version if pkg else None,
            "current_version_id": pkg.id if pkg else None,
            "published_version": pkg.version if (pkg and pkg.status == LifecycleStatus.PUBLISHED) else None,
            "assignments": assignments,
            "events": events,
            "proposal_task_id": pkg.proposal_task_id if pkg else None,
            "proposer_agent": pkg.proposer_agent if pkg else None,
        }

    def get_effective_skills(self, db, agent_name: str) -> list[PackageVersion]:
        """Get the set of published + assigned skills for session materialization."""
        assignments = stores.get_active_assignments_for_agent(db, agent_name)
        skills = []
        for assign in assignments:
            pkg = stores.get_package_version(db, assign.package_version_id)
            if pkg is not None and pkg.status == LifecycleStatus.PUBLISHED:
                skills.append(pkg)
        return skills

    def list_catalog(self, db) -> list[PackageVersion]:
        """List all published custom skills for the union catalog.

        Excludes packages that have been explicitly retired (have a 'retired'
        lifecycle event AND no active assignments). Freshly published packages
        without assignments still appear in the catalog.
        """
        pkgs = stores.list_package_versions(db, status=LifecycleStatus.PUBLISHED)
        active = []
        for pkg in pkgs:
            # Check for an explicit retire event
            events = stores.list_lifecycle_events(db, skill_id=pkg.skill_id)
            has_retire_event = any(e.event_type == "retired" for e in events)
            has_active_assignments = bool(
                stores.get_all_active_assignments_for_skill(db, pkg.skill_id)
            )
            if has_retire_event and not has_active_assignments:
                continue  # Explicitly retired — exclude from catalog
            active.append(pkg)
        return active

    # ── THR-055 Founder-only proposal review ────────────────────────────

    def get_proposals_queue(
        self, db, status: str | None = None, page: int = 1, page_size: int = 20,
    ) -> dict:
        """Founder-only paginated/filterable proposal queue."""
        items, total = stores.list_proposals_queue(db, status=status, page=page, page_size=page_size)
        return {"items": items, "page": page, "page_size": page_size, "total": total}

    def get_proposal_detail(self, db, version_id: int) -> dict:
        """Founder-only full proposal detail by version id."""
        detail = stores.get_proposal_detail(db, version_id)
        if detail is None:
            raise LifecycleError(
                code="not_found",
                detail=f"No proposal found with version_id {version_id}.",
                status_code=404,
            )
        return detail

    def check_concurrency(
        self, db, version_id: int, expected_event_id: int,
    ) -> None:
        """Verify the concurrency marker matches the latest event id.

        Raises LifecycleError(409) with conflict code and current state
        if the marker is stale.
        """
        current = stores.get_latest_event_id_for_version(db, version_id)
        if current is None:
            raise LifecycleError(
                code="unknown_version",
                detail=f"Version {version_id} has no events.",
                status_code=409,
            )
        if current != expected_event_id:
            # Fetch current detail for the conflict response
            detail = stores.get_proposal_detail(db, version_id)
            raise LifecycleError(
                code="stale_concurrency",
                detail={
                    "message": f"Concurrency conflict: expected event {expected_event_id} but latest is {current}.",
                    "expected_event_id": expected_event_id,
                    "current_event_id": current,
                    "current_status": detail["status"].value if detail else None,
                },
                status_code=409,
            )

    # ── V2 proposal actions (Founder-only, concurrency-protected) ───────

    def claim_proposal_v2(
        self, db, actor_kind: str, version_id: int,
        sponsor: str = "founder",
    ) -> PackageVersion:
        """V2 claim (used by founder-only review route) — same semantics
        as claim_proposal but called with the pre-checked concurrency marker."""
        return self.claim_proposal(db, actor_kind, version_id, sponsor)

    def validate_proposal(
        self, db, actor_kind: str, version_id: int,
        validator_version: str, validator_key: str | None = None,
    ) -> PackageVersion:
        """Founder-triggered validation with reproducible metadata."""
        self._ensure_human(actor_kind, "validate")
        return self.record_validation(
            db, actor_kind, version_id, ok=True,
            validator_version=validator_version,
            validator_key=validator_key or validator_version,
        )

    def review_proposal(
        self, db, actor_kind: str, version_id: int,
        decision: str, rationale: str, reviewer: str = "founder",
    ) -> PackageVersion:
        """Founder review decision (v2 — concurrency-protected)."""
        return self.review_decision(
            db, actor_kind, version_id, decision, rationale, reviewer,
        )

    def publish_proposal(
        self, db, actor_kind: str, version_id: int,
        approval_event_id: int, publisher: str = "founder",
    ) -> PackageVersion:
        """Founder publish (v2 — concurrency-protected)."""
        return self.publish(db, actor_kind, version_id, approval_event_id, publisher)

    def assign_proposal(
        self, db, actor_kind: str, skill_id: str, agent_name: str,
        version_id: int, assigner: str = "founder",
    ) -> AssignmentRecord:
        """Founder assign (v2 — concurrency-protected)."""
        return self.assign(db, actor_kind, skill_id, agent_name, version_id, assigner)

    def rollback_proposal(
        self, db, actor_kind: str, skill_id: str,
        reason: str, rolled_back_by: str = "founder",
        target_version_id: int | None = None,
    ) -> int:
        """Founder rollback (v2 — assignment-level only, concurrency-protected).

        V2 rollback at /proposals/{version_id}/rollback acts only on the
        addressed version's assignments. target_version_id is passed
        through to the underlying rollback() to ensure exact-version
        targeting.
        """
        return self.rollback(db, actor_kind, skill_id, reason, rolled_back_by,
                            target_version_id=target_version_id)

    # ── Guards ────────────────────────────────────────────────────────────

    def _ensure_agent(self, actor_kind: str, action: str) -> None:
        """Agent-only actions. Block human callers (they use different routes)."""
        if actor_kind != "agent":
            raise AgentForbiddenError(action)

    def _ensure_human(self, actor_kind: str, action: str) -> None:
        """Human-only actions. Block agent callers."""
        if actor_kind == "agent":
            raise AgentForbiddenError(action)

    def _ensure_non_empty(self, value: str, field: str) -> None:
        if not value or not value.strip():
            raise LifecycleError(
                code="empty_field",
                detail=f"Field '{field}' must not be empty.",
                status_code=400,
            )

    def _ensure_protected_slug(self, slug: str, protected_slugs: frozenset | None = None) -> None:
        slugs = protected_slugs if protected_slugs is not None else _PROTECTED_SLUGS
        if slug in slugs:
            raise LifecycleError(
                code="protected_slug",
                detail=f"Slug '{slug}' is a protected release or system-contract slug and cannot be used for custom skills.",
                status_code=409,
            )

    def _ensure_policy_class(self, policy_class: str) -> None:
        if policy_class not in ALLOWED_POLICY_CLASSES:
            raise LifecycleError(
                code="policy_class_not_allowed",
                detail=f"Policy class '{policy_class}' is not allowed in the pilot. Only 'standard_operational' is supported.",
                status_code=400,
            )

    def _ensure_not_rejected(self, pkg: PackageVersion) -> None:
        """Reject any mutation attempt on a terminally REJECTED proposal.

        After rejection, every later claim, validation, review/approval,
        publish, assign, materialization, rollback/reopen/recovery attempt
        on that proposal/version is blocked. A future change is a new
        proposal/version only.
        """
        if pkg.status == LifecycleStatus.REJECTED:
            raise LifecycleError(
                code="rejected_terminal",
                detail=f"Proposal version {pkg.id} is terminally REJECTED. "
                       f"No further mutations are permitted. Submit a new proposal for changes.",
                status_code=409,
            )

    def _get_package(self, db, version_id: int) -> PackageVersion:
        pkg = stores.get_package_version(db, version_id)
        if pkg is None:
            raise LifecycleError(
                code="not_found",
                detail=f"No package version found with id {version_id}.",
                status_code=404,
            )
        return pkg
