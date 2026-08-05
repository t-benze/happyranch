"""THR-055 Custom Skill Creation + Eligibility service.

Provides:
- Custom skill create/update (agent + human)
- Version management with deterministic validation
- Eligibility policy management with atomic writes + audit
- Dry-run impact preview and effective-skill explanation
- Retire/restore lifecycle
- Migration from legacy proposals
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from . import eligibility_stores
from . import stores as lifecycle_stores
from .models import (
    CustomSkillRecord,
    CustomSkillVersionRecord,
    CustomSkillVisibility,
    EffectiveSkillExplanation,
    EligibilityAction,
    EligibilityDryRunResponse,
    EligibilityRule,
    EligibilityRuleInput,
    EligibilityTargetScope,
    LifecycleStatus,
    MaterializationRecord,
    utcnow,
)


# ── Constants ─────────────────────────────────────────────────────────────

_PROTECTED_SLUGS = frozenset({
    "start-task", "jobs", "make-worktree", "thread", "dream",
    "reflection", "manage-agent", "manage-repo", "brainstorming",
    "dispatching-parallel-agents", "executing-plans",
    "finishing-a-development-branch", "receiving-code-review",
    "requesting-code-review", "subagent-driven-development",
    "systematic-debugging", "test-driven-development",
    "using-git-worktrees", "using-superpowers",
    "verification-before-completion", "writing-plans", "writing-skills",
    "todos",
})

# First-party shipped skills from runtime/skills/
_FIRST_PARTY_SLUGS = frozenset({
    "reflection", "manage-agent", "manage-repo",
})

_ALLOWED_POLICY_CLASSES = frozenset({"standard_operational"})

# Prohibited content patterns — skills must not attempt to declare
# executable, credential, permission, sandbox, allow-rule, executor,
# or eligibility-writing behavior.
_PROHIBITED_CONTENT_PATTERNS = [
    (re.compile(r"(?i)permission"), "permission declaration"),
    (re.compile(r"(?i)allow\s*rule"), "allow rule declaration"),
    (re.compile(r"(?i)executor"), "executor configuration"),
    (re.compile(r"(?i)sandbox"), "sandbox configuration"),
    (re.compile(r"(?i)credential"), "credential reference"),
    (re.compile(r"(?i)eligibility"), "eligibility write attempt"),
    (re.compile(r"(?i)network\s*access"), "network access declaration"),
    (re.compile(r"(?i)filesystem\s*access"), "filesystem access declaration"),
    (re.compile(r"(?i)command\s*authority"), "command authority declaration"),
]


class CustomSkillError(Exception):
    """Raised when a custom skill operation is invalid."""
    def __init__(self, code: str, detail: str, status_code: int = 409):
        self.code = code
        self.detail = detail
        self.status_code = status_code
        super().__init__(detail)


class AgentForbiddenError(CustomSkillError):
    """403 — Agent tried to perform a human-only action."""
    def __init__(self, action: str):
        super().__init__(
            code="agent_forbidden",
            detail=f"Agent callers may not perform action '{action}'. Only human/founder actors may do this.",
            status_code=403,
        )


# ── Helpers ──────────────────────────────────────────────────────────────

def _get_raw_connection(db):
    """Extract the raw sqlite3.Connection from a Database wrapper."""
    if hasattr(db, '_conn'):
        return db._conn
    return db


# ── Deterministic validation ──────────────────────────────────────────────

VALIDATOR_VERSION = "THR-055/1.0.0"

def _validate_skill_content(
    slug: str, skill_md: str, policy_class: str,
) -> tuple[bool, list[str]]:
    """Deterministic validation of skill content.

    Returns (passed, findings). Findings are keyed to exact content hash
    + validator version.
    """
    findings: list[str] = []

    # Check protected slugs
    if slug in _PROTECTED_SLUGS:
        findings.append(f"protected_slug: '{slug}' is a protected system/first-party slug")
    if slug in _FIRST_PARTY_SLUGS:
        findings.append(f"first_party_slug: '{slug}' is a first-party shipped skill slug")

    # Check policy class
    if policy_class not in _ALLOWED_POLICY_CLASSES:
        findings.append(f"policy_class: '{policy_class}' not in {_ALLOWED_POLICY_CLASSES}")

    # Check prohibited content patterns
    for pattern, label in _PROHIBITED_CONTENT_PATTERNS:
        if pattern.search(skill_md):
            findings.append(f"prohibited_content: {label}")

    # Check for system contract declarations
    if skill_md.strip().startswith("---"):
        frontmatter_end = skill_md.find("---", 3)
        if frontmatter_end != -1:
            fm = skill_md[3:frontmatter_end]
            if "policy_class: system_contract" in fm.lower():
                findings.append("prohibited_content: system_contract declaration")

    return len(findings) == 0, findings


def compute_validation_key(content_hash: str, validator_version: str) -> str:
    """Deterministic validation key from content hash + validator version."""
    return hashlib.sha256(
        f"{content_hash}:{validator_version}".encode()
    ).hexdigest()[:16]


# ── Eligibility resolver ─────────────────────────────────────────────────

def resolve_effective_visibility(
    db,
    skill_id: str,
    agent_name: str,
    team_name: str | None = None,
    org_slug: str | None = None,
) -> tuple[bool, str | None, EligibilityRule | None]:
    """Resolve effective visibility for an agent on a skill.

    Returns (visible, hidden_reason, winning_rule).
    Semantics: additive allow; explicit deny wins.
    """
    rules = eligibility_stores.get_eligibility_rules_for_skill(db, skill_id)
    if not rules:
        return False, "no_eligibility_rules", None

    # Sort: deny rules take precedence; then by priority desc
    # Process: collect all applicable allows and denies
    allows: list[EligibilityRule] = []
    denies: list[EligibilityRule] = []

    for rule in rules:
        if not _rule_applies(rule, agent_name, team_name, org_slug):
            continue
        if rule.action == EligibilityAction.DENY:
            denies.append(rule)
        else:
            allows.append(rule)

    # Explicit deny wins
    if denies:
        return False, f"deny:{denies[0].target_scope.value}:{denies[0].created_by}:{denies[0].created_at.isoformat()}", denies[0]

    # At least one allow
    if allows:
        return True, None, allows[0]

    return False, "no_applicable_rules", None


def _rule_applies(
    rule: EligibilityRule,
    agent_name: str,
    team_name: str | None,
    org_slug: str | None,
) -> bool:
    """Check whether an eligibility rule applies to a given agent."""
    if rule.target_scope == EligibilityTargetScope.AGENT:
        return rule.target_name == agent_name
    elif rule.target_scope == EligibilityTargetScope.TEAM:
        return team_name is not None and rule.target_name == team_name
    elif rule.target_scope == EligibilityTargetScope.ORG:
        return org_slug is not None and rule.target_name == org_slug
    return False


def compute_dry_run(
    db,
    skill_id: str,
    proposed_rules: list[EligibilityRule],
    all_agents: list[tuple[str, str | None, str | None]],  # (name, team, org)
    current_rules: list[EligibilityRule] | None = None,
) -> EligibilityDryRunResponse:
    """Compute the impact of proposed eligibility rules.

    all_agents: list of (agent_name, team_name, org_slug).
    """
    if current_rules is None:
        current_rules = eligibility_stores.get_eligibility_rules_for_skill(db, skill_id)

    newly_visible: list[str] = []
    newly_hidden: list[str] = []
    unchanged_visible: list[str] = []
    unchanged_hidden: list[str] = []
    winning_rules: dict[str, str] = {}

    for agent_name, team_name, org_slug in all_agents:
        # Current visibility
        cur_visible, _, _ = _resolve_with_rules(current_rules, agent_name, team_name, org_slug)
        # Proposed visibility
        new_visible, _, winning = _resolve_with_rules(proposed_rules, agent_name, team_name, org_slug)

        if cur_visible and new_visible:
            unchanged_visible.append(agent_name)
        elif not cur_visible and not new_visible:
            unchanged_hidden.append(agent_name)
        elif not cur_visible and new_visible:
            newly_visible.append(agent_name)
        else:  # cur_visible and not new_visible
            newly_hidden.append(agent_name)

        if winning:
            winning_rules[agent_name] = f"{winning.action.value}:{winning.target_scope.value}:{winning.target_name}"

    return EligibilityDryRunResponse(
        skill_id=skill_id,
        newly_visible=newly_visible,
        newly_hidden=newly_hidden,
        unchanged_visible=unchanged_visible,
        unchanged_hidden=unchanged_hidden,
        winning_rules=winning_rules,
    )


def _resolve_with_rules(
    rules: list[EligibilityRule],
    agent_name: str,
    team_name: str | None,
    org_slug: str | None,
) -> tuple[bool, str | None, EligibilityRule | None]:
    """Resolve visibility with a specific set of rules."""
    allows = []
    denies = []
    for rule in rules:
        if not _rule_applies(rule, agent_name, team_name, org_slug):
            continue
        if rule.action == EligibilityAction.DENY:
            denies.append(rule)
        else:
            allows.append(rule)
    if denies:
        return False, f"deny:{denies[0].target_scope.value}", denies[0]
    if allows:
        return True, None, allows[0]
    return False, "no_applicable_rules", None


# ── CustomSkillService ────────────────────────────────────────────────────

class CustomSkillService:
    """Service for custom skill creation, versioning, eligibility, and lifecycle."""

    # ── Agent create / update ─────────────────────────────────────────────

    def create_skill_agent(
        self,
        db,
        slug: str,
        name: str,
        description: str,
        skill_md: str,
        agent_name: str,
        task_id: str,
        session_id: str,
        version: str = "0.1.0",
        policy_class: str = "standard_operational",
        purpose: str = "",
        org_root: Path | str | None = None,
    ) -> dict:
        """Agent creates a new custom skill from verified session context.

        Returns a dict with create result details.
        """
        self._ensure_non_empty(skill_md, "skill_md")
        self._ensure_non_empty(name, "name")
        self._ensure_protected_slug(slug)
        self._ensure_policy_class(policy_class)

        skill_id = f"hr:{slug}"

        # Check for existing skill with this slug
        existing = eligibility_stores.get_custom_skill_by_slug(db, slug)
        if existing is not None:
            raise CustomSkillError(
                code="slug_conflict",
                detail=f"A custom skill with slug '{slug}' already exists (skill_id: {existing.skill_id}). Use update instead.",
                status_code=409,
            )

        # Also check lifecycle packages for slug collision
        legacy = lifecycle_stores.get_latest_package_version(db, skill_id)
        if legacy is not None and legacy.status not in (
            LifecycleStatus.LEGACY_QUARANTINED,
            LifecycleStatus.REJECTED,
            LifecycleStatus.RETIRED,
            LifecycleStatus.ROLLED_BACK,
        ):
            raise CustomSkillError(
                code="slug_conflict",
                detail=f"A lifecycle package with skill_id '{skill_id}' already exists.",
                status_code=409,
            )

        # Compute content hash
        content_hash = hashlib.sha256(skill_md.encode("utf-8")).hexdigest()

        # Deterministic validation
        validation_passed, validation_findings = _validate_skill_content(
            slug, skill_md, policy_class,
        )
        validation_key = compute_validation_key(content_hash, VALIDATOR_VERSION)
        now = utcnow()

        # Write to artifact store if org_root available
        content_artifact_key: str | None = None
        if org_root is not None:
            try:
                from runtime.infrastructure.artifact_store import ArtifactStore
                from runtime.orchestrator._paths import OrgPaths
                org_root_path = Path(org_root) if not isinstance(org_root, Path) else org_root
                store = ArtifactStore(OrgPaths(org_root_path).artifacts_dir)
                prefix = f"skill-lifecycle/{slug}"
                artifact_key = f"{prefix}/{content_hash[:16]}/SKILL.md"
                store.put(artifact_key, skill_md.encode("utf-8"))
                content_artifact_key = artifact_key
            except Exception:
                pass  # Best-effort artifact storage

        # Transactional write
        conn = _get_raw_connection(db)
        prev_isolation = getattr(conn, 'isolation_level', 'auto')
        try:
            conn.isolation_level = None
            conn.execute("BEGIN IMMEDIATE")

            # Create version record
            version_rec = CustomSkillVersionRecord(
                skill_id=skill_id,
                slug=slug,
                name=name,
                version=version,
                content_hash=content_hash,
                policy_class=policy_class,
                description=description,
                content_artifact_key=content_artifact_key,
                status=LifecycleStatus.PUBLISHED,
                created_by=agent_name,
                proposer_agent=agent_name,
                proposal_task_id=task_id,
                proposal_session_id=session_id,
                validated_at=now,
                validator_version=VALIDATOR_VERSION,
                validation_passed=validation_passed,
                validation_findings=validation_findings,
            )
            version_id = eligibility_stores.insert_custom_skill_version(db, version_rec)

            # Create custom skill record (hidden by default)
            skill_rec = CustomSkillRecord(
                skill_id=skill_id,
                slug=slug,
                name=name,
                description=description,
                policy_class=policy_class,
                visibility=CustomSkillVisibility.HIDDEN,
                created_by=agent_name,
                proposer_agent=agent_name,
                proposal_task_id=task_id,
                proposal_session_id=session_id,
                current_version_id=version_id,
                current_version=version,
                current_content_hash=content_hash,
                last_validated_at=now,
                last_validator_version=VALIDATOR_VERSION,
            )
            eligibility_stores.insert_custom_skill(db, skill_rec)

            # Record lifecycle event in legacy table for audit continuity
            lifecycle_stores.insert_lifecycle_event(db, lifecycle_stores.LifecycleEvent(
                skill_id=skill_id,
                package_version_id=None,
                event_type="custom_skill_created",
                actor=agent_name,
                actor_role="agent",
                new_status="published",
                content_hash=content_hash,
                metadata={
                    "version": version,
                    "validation_passed": validation_passed,
                    "validation_key": validation_key,
                    "validator_version": VALIDATOR_VERSION,
                    "purpose": purpose,
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
            raise
        finally:
            conn.isolation_level = prev_isolation

        return {
            "skill_id": skill_id,
            "slug": slug,
            "name": name,
            "version": version,
            "content_hash": content_hash,
            "validation_passed": validation_passed,
            "validation_findings": validation_findings,
            "visibility": CustomSkillVisibility.HIDDEN.value,
            "version_id": version_id,
            "created_by": agent_name,
            "created_at": now.isoformat(),
            "proposal_task_id": task_id,
        }

    def update_skill_agent(
        self,
        db,
        slug: str,
        agent_name: str,
        task_id: str,
        session_id: str,
        name: str | None = None,
        description: str | None = None,
        skill_md: str | None = None,
        version: str | None = None,
        purpose: str = "",
        org_root: Path | str | None = None,
    ) -> dict:
        """Agent updates an existing custom skill they originated.

        Only the originating agent may update their own skill.
        Creates a new immutable version; never mutates historic content.
        """
        skill_id = f"hr:{slug}"
        skill_rec = eligibility_stores.get_custom_skill(db, skill_id)
        if skill_rec is None:
            raise CustomSkillError(
                code="not_found",
                detail=f"No custom skill found with slug '{slug}'.",
                status_code=404,
            )

        # Only the originating agent may update
        if skill_rec.proposer_agent and skill_rec.proposer_agent != agent_name:
            raise CustomSkillError(
                code="not_skill_owner",
                detail=f"Agent '{agent_name}' may not update skill '{slug}' — "
                       f"only the originating agent '{skill_rec.proposer_agent}' may update.",
                status_code=403,
            )
        if not skill_rec.proposer_agent and skill_rec.created_by != agent_name:
            raise CustomSkillError(
                code="not_skill_owner",
                detail=f"Agent '{agent_name}' may not update skill '{slug}' — "
                       f"only the originating actor '{skill_rec.created_by}' may update.",
                status_code=403,
            )

        if skill_rec.retired:
            raise CustomSkillError(
                code="skill_retired",
                detail=f"Skill '{slug}' is retired. Restore it before updating.",
                status_code=409,
            )

        new_skill_md = skill_md if skill_md is not None else ""
        new_name = name if name else skill_rec.name
        new_description = description if description is not None else skill_rec.description
        new_version = version if version else _bump_version(skill_rec.current_version or "0.1.0")

        if skill_md is not None:
            self._ensure_non_empty(skill_md, "skill_md")

        self._ensure_policy_class(skill_rec.policy_class)

        # Compute content hash
        content_hash = hashlib.sha256(new_skill_md.encode("utf-8")).hexdigest()

        # Check for duplicate content (idempotent)
        existing_hash = eligibility_stores.get_custom_skill_version_by_hash(db, skill_id, content_hash)
        if existing_hash is not None:
            return {
                "skill_id": skill_id,
                "slug": slug,
                "name": new_name,
                "version": existing_hash.version,
                "content_hash": content_hash,
                "validation_passed": existing_hash.validation_passed or False,
                "validation_findings": existing_hash.validation_findings or [],
                "visibility": skill_rec.visibility.value,
                "version_id": existing_hash.id,
                "created_by": agent_name,
                "created_at": existing_hash.created_at.isoformat(),
                "proposal_task_id": task_id,
            }

        # Validation
        validation_passed, validation_findings = _validate_skill_content(
            slug, new_skill_md, skill_rec.policy_class,
        )
        now = utcnow()

        conn = _get_raw_connection(db)
        prev_isolation = getattr(conn, 'isolation_level', 'auto')
        try:
            conn.isolation_level = None
            conn.execute("BEGIN IMMEDIATE")

            version_rec = CustomSkillVersionRecord(
                skill_id=skill_id,
                slug=slug,
                name=new_name,
                version=new_version,
                content_hash=content_hash,
                policy_class=skill_rec.policy_class,
                description=new_description,
                status=LifecycleStatus.PUBLISHED,
                created_by=agent_name,
                proposer_agent=agent_name,
                proposal_task_id=task_id,
                proposal_session_id=session_id,
                validated_at=now,
                validator_version=VALIDATOR_VERSION,
                validation_passed=validation_passed,
                validation_findings=validation_findings,
            )
            version_id = eligibility_stores.insert_custom_skill_version(db, version_rec)

            eligibility_stores.update_custom_skill(db, skill_id,
                name=new_name,
                description=new_description,
                current_version_id=version_id,
                current_version=new_version,
                current_content_hash=content_hash,
                last_validated_at=now,
                last_validator_version=VALIDATOR_VERSION,
            )

            lifecycle_stores.insert_lifecycle_event(db, lifecycle_stores.LifecycleEvent(
                skill_id=skill_id,
                event_type="custom_skill_updated",
                actor=agent_name,
                actor_role="agent",
                new_status="published",
                content_hash=content_hash,
                metadata={
                    "version": new_version,
                    "validation_passed": validation_passed,
                    "validator_version": VALIDATOR_VERSION,
                    "purpose": purpose,
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
            raise
        finally:
            conn.isolation_level = prev_isolation

        return {
            "skill_id": skill_id,
            "slug": slug,
            "name": new_name,
            "version": new_version,
            "content_hash": content_hash,
            "validation_passed": validation_passed,
            "validation_findings": validation_findings,
            "visibility": skill_rec.visibility.value,
            "version_id": version_id,
            "created_by": agent_name,
            "created_at": now.isoformat(),
            "proposal_task_id": task_id,
        }

    # ── Human create / edit / retire / restore ────────────────────────────

    def create_skill_human(
        self,
        db,
        slug: str,
        name: str,
        description: str,
        skill_md: str,
        actor: str = "founder",
        version: str = "0.1.0",
        policy_class: str = "standard_operational",
        org_root: Path | str | None = None,
    ) -> dict:
        """Human creates a custom skill."""
        self._ensure_non_empty(skill_md, "skill_md")
        self._ensure_non_empty(name, "name")
        self._ensure_protected_slug(slug)
        self._ensure_policy_class(policy_class)

        skill_id = f"hr:{slug}"
        existing = eligibility_stores.get_custom_skill_by_slug(db, slug)
        if existing is not None:
            raise CustomSkillError(
                code="slug_conflict",
                detail=f"A custom skill with slug '{slug}' already exists.",
                status_code=409,
            )

        content_hash = hashlib.sha256(skill_md.encode("utf-8")).hexdigest()
        validation_passed, validation_findings = _validate_skill_content(
            slug, skill_md, policy_class,
        )
        now = utcnow()

        conn = _get_raw_connection(db)
        prev_isolation = getattr(conn, 'isolation_level', 'auto')
        try:
            conn.isolation_level = None
            conn.execute("BEGIN IMMEDIATE")

            version_rec = CustomSkillVersionRecord(
                skill_id=skill_id, slug=slug, name=name,
                version=version, content_hash=content_hash,
                policy_class=policy_class, description=description,
                status=LifecycleStatus.PUBLISHED, created_by=actor,
                validated_at=now, validator_version=VALIDATOR_VERSION,
                validation_passed=validation_passed,
                validation_findings=validation_findings,
            )
            version_id = eligibility_stores.insert_custom_skill_version(db, version_rec)

            skill_rec = CustomSkillRecord(
                skill_id=skill_id, slug=slug, name=name,
                description=description, policy_class=policy_class,
                visibility=CustomSkillVisibility.HIDDEN,
                created_by=actor,
                current_version_id=version_id,
                current_version=version,
                current_content_hash=content_hash,
                last_validated_at=now,
                last_validator_version=VALIDATOR_VERSION,
            )
            eligibility_stores.insert_custom_skill(db, skill_rec)

            lifecycle_stores.insert_lifecycle_event(db, lifecycle_stores.LifecycleEvent(
                skill_id=skill_id,
                event_type="custom_skill_created",
                actor=actor,
                actor_role="human",
                new_status="published",
                content_hash=content_hash,
                metadata={"version": version, "validation_passed": validation_passed},
            ))

            conn.execute("COMMIT")
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
            raise
        finally:
            conn.isolation_level = prev_isolation

        return {
            "skill_id": skill_id, "slug": slug, "name": name,
            "version": version, "content_hash": content_hash,
            "validation_passed": validation_passed,
            "validation_findings": validation_findings,
            "visibility": CustomSkillVisibility.HIDDEN.value,
            "version_id": version_id, "created_by": actor,
            "created_at": now.isoformat(),
        }

    def retire_skill(
        self, db, slug: str, actor: str = "founder", reason: str = "",
    ) -> dict:
        """Retire a custom skill. Blocks future visibility but preserves history."""
        skill_id = f"hr:{slug}"
        skill_rec = eligibility_stores.get_custom_skill(db, skill_id)
        if skill_rec is None:
            raise CustomSkillError(code="not_found",
                                   detail=f"No custom skill found with slug '{slug}'.", status_code=404)
        if skill_rec.retired:
            raise CustomSkillError(code="already_retired",
                                   detail=f"Skill '{slug}' is already retired.", status_code=409)

        now = utcnow()
        eligibility_stores.update_custom_skill(db, skill_id,
            retired=True, retired_at=now, retired_by=actor,
            visibility=CustomSkillVisibility.HIDDEN,
        )

        lifecycle_stores.insert_lifecycle_event(db, lifecycle_stores.LifecycleEvent(
            skill_id=skill_id,
            event_type="custom_skill_retired",
            actor=actor, actor_role="human",
            metadata={"reason": reason},
        ))

        return {"skill_id": skill_id, "slug": slug, "retired": True, "retired_at": now.isoformat()}

    def restore_skill(self, db, slug: str, actor: str = "founder") -> dict:
        """Restore a retired custom skill."""
        skill_id = f"hr:{slug}"
        skill_rec = eligibility_stores.get_custom_skill(db, skill_id)
        if skill_rec is None:
            raise CustomSkillError(code="not_found",
                                   detail=f"No custom skill found with slug '{slug}'.", status_code=404)
        if not skill_rec.retired:
            raise CustomSkillError(code="not_retired",
                                   detail=f"Skill '{slug}' is not retired.", status_code=409)

        eligibility_stores.update_custom_skill(db, skill_id,
            retired=False, retired_at=None, retired_by=None,
        )

        lifecycle_stores.insert_lifecycle_event(db, lifecycle_stores.LifecycleEvent(
            skill_id=skill_id,
            event_type="custom_skill_restored",
            actor=actor, actor_role="human",
        ))

        return {"skill_id": skill_id, "slug": slug, "retired": False}

    # ── Eligibility ──────────────────────────────────────────────────────

    def set_eligibility_rules(
        self, db, skill_id: str, rules: list[EligibilityRuleInput],
        org_agents: list[str], org_teams: dict[str, list[str]],  # team_name -> [agent_names]
        org_slug: str,
        actor: str = "founder",
    ) -> dict:
        """Atomically set eligibility rules for a skill.

        Validates target references, records audit trail, returns impact preview.
        """
        # Validate targets exist
        for rule in rules:
            if rule.target_scope == EligibilityTargetScope.AGENT:
                if rule.target_name not in org_agents:
                    raise CustomSkillError(
                        code="invalid_target",
                        detail=f"Agent '{rule.target_name}' not found in org configuration.",
                        status_code=400,
                    )
            elif rule.target_scope == EligibilityTargetScope.TEAM:
                if rule.target_name not in org_teams:
                    raise CustomSkillError(
                        code="invalid_target",
                        detail=f"Team '{rule.target_name}' not found in org configuration.",
                        status_code=400,
                    )

        # Snapshot current rules for audit
        current = eligibility_stores.get_eligibility_rules_for_skill(db, skill_id)
        rules_before = [_rule_to_dict(r) for r in current]

        # Atomic write
        conn = _get_raw_connection(db)
        prev_isolation = getattr(conn, 'isolation_level', 'auto')
        try:
            conn.isolation_level = None
            conn.execute("BEGIN IMMEDIATE")

            # Delete existing
            eligibility_stores.delete_eligibility_rules_for_skill(db, skill_id)

            # Insert new
            new_rules = []
            for i, r in enumerate(rules):
                rule = EligibilityRule(
                    skill_id=skill_id,
                    target_scope=r.target_scope,
                    target_name=r.target_name,
                    action=r.action,
                    priority=len(rules) - i,
                    created_by=actor,
                )
                rid = eligibility_stores.insert_eligibility_rule(db, rule)
                rule.id = rid
                new_rules.append(rule)

            # Audit
            rules_after = [_rule_to_dict(r) for r in new_rules]
            eligibility_stores.insert_eligibility_audit(
                db, skill_id, "set_policy", actor, rules_before, rules_after,
            )

            lifecycle_stores.insert_lifecycle_event(db, lifecycle_stores.LifecycleEvent(
                skill_id=skill_id,
                event_type="eligibility_policy_set",
                actor=actor, actor_role="human",
                metadata={"rules_count": len(rules)},
            ))

            conn.execute("COMMIT")
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
            raise
        finally:
            conn.isolation_level = prev_isolation

        # Compute dry-run response
        all_agents = _build_agent_list(org_agents, org_teams, org_slug)
        dry_run = compute_dry_run(db, skill_id, new_rules, all_agents, current)

        return {
            "skill_id": skill_id,
            "rules_count": len(rules),
            "dry_run": dry_run.model_dump(),
        }

    def dry_run_eligibility(
        self, db, skill_id: str, proposed_rules: list[EligibilityRuleInput],
        org_agents: list[str], org_teams: dict[str, list[str]],
        org_slug: str,
    ) -> EligibilityDryRunResponse:
        """Dry-run preview of eligibility rules without persisting."""
        current = eligibility_stores.get_eligibility_rules_for_skill(db, skill_id)
        proposed = [
            EligibilityRule(
                skill_id=skill_id,
                target_scope=r.target_scope,
                target_name=r.target_name,
                action=r.action,
            )
            for r in proposed_rules
        ]
        all_agents = _build_agent_list(org_agents, org_teams, org_slug)
        return compute_dry_run(db, skill_id, proposed, all_agents, current)

    def get_effective_skills(
        self, db, agent_name: str, team_name: str | None = None,
        org_slug: str | None = None,
    ) -> list[EffectiveSkillExplanation]:
        """Get all custom skills with effective visibility for an agent."""
        skills, _ = eligibility_stores.list_custom_skills(db, retired=False)
        results = []
        for skill in skills:
            visible, hidden_reason, winning_rule = resolve_effective_visibility(
                db, skill.skill_id, agent_name, team_name, org_slug,
            )
            last_mat = eligibility_stores.get_latest_custom_materialization(
                db, skill.skill_id, agent_name,
            )
            mat_record = None
            if last_mat:
                mat_record = MaterializationRecord(
                    skill_id=last_mat["skill_id"],
                    agent_name=last_mat["agent_name"],
                    package_version_id=last_mat["version_id"],
                    version=last_mat["version"],
                    content_hash=last_mat["content_hash"],
                    success=last_mat["success"],
                    error_message=last_mat.get("error_message"),
                    session_context=last_mat.get("session_context"),
                )

            results.append(EffectiveSkillExplanation(
                skill_id=skill.skill_id,
                slug=skill.slug,
                name=skill.name,
                version=skill.current_version,
                content_hash=skill.current_content_hash,
                visible=visible,
                hidden_reason=hidden_reason,
                winning_rule=winning_rule,
                validation_passed=True,  # Will be set from version record
                last_materialization=mat_record,
                retired=skill.retired,
            ))

        return results

    def get_effective_skill(
        self, db, skill_id: str, agent_name: str,
        team_name: str | None = None, org_slug: str | None = None,
    ) -> EffectiveSkillExplanation | None:
        """Get effective skill explanation for a specific skill + agent."""
        skill = eligibility_stores.get_custom_skill(db, skill_id)
        if skill is None:
            return None

        visible, hidden_reason, winning_rule = resolve_effective_visibility(
            db, skill_id, agent_name, team_name, org_slug,
        )
        last_mat = eligibility_stores.get_latest_custom_materialization(
            db, skill_id, agent_name,
        )
        mat_record = None
        if last_mat:
            mat_record = MaterializationRecord(
                skill_id=last_mat["skill_id"],
                agent_name=last_mat["agent_name"],
                package_version_id=last_mat["version_id"],
                version=last_mat["version"],
                content_hash=last_mat["content_hash"],
                success=last_mat["success"],
                error_message=last_mat.get("error_message"),
                session_context=last_mat.get("session_context"),
            )

        return EffectiveSkillExplanation(
            skill_id=skill.skill_id,
            slug=skill.slug,
            name=skill.name,
            version=skill.current_version,
            content_hash=skill.current_content_hash,
            visible=visible,
            hidden_reason=hidden_reason,
            winning_rule=winning_rule,
            validation_passed=(skill.last_validator_version is not None),
            last_materialization=mat_record,
            retired=skill.retired,
        )

    # ── Materialization ──────────────────────────────────────────────────

    def get_visible_skills_for_agent(
        self, db, agent_name: str, team_name: str | None = None,
        org_slug: str | None = None,
    ) -> list[CustomSkillRecord]:
        """Get all visible, non-retired custom skills for an agent."""
        skills, _ = eligibility_stores.list_custom_skills(db, retired=False)
        visible = []
        for skill in skills:
            is_visible, _, _ = resolve_effective_visibility(
                db, skill.skill_id, agent_name, team_name, org_slug,
            )
            if is_visible and skill.current_version_id is not None:
                visible.append(skill)
        return visible

    def record_materialization(
        self, db, skill_id: str, agent_name: str,
        version_id: int, version: str, content_hash: str,
        success: bool, error_message: str | None = None,
        session_context: str | None = None,
        session_id: str | None = None,
    ) -> int:
        """Record a custom skill materialization."""
        return eligibility_stores.insert_custom_materialization(
            db, skill_id, agent_name, version_id, version, content_hash,
            success, error_message, session_context, session_id,
        )

    # ── Read / query ─────────────────────────────────────────────────────

    def get_skill_detail(self, db, slug: str) -> dict | None:
        """Get full detail for a custom skill."""
        skill_id = f"hr:{slug}"
        skill = eligibility_stores.get_custom_skill(db, skill_id)
        if skill is None:
            return None

        versions = eligibility_stores.list_custom_skill_versions(db, skill_id)
        rules = eligibility_stores.get_eligibility_rules_for_skill(db, skill_id)

        return {
            "id": skill.id,
            "skill_id": skill.skill_id,
            "slug": skill.slug,
            "name": skill.name,
            "description": skill.description,
            "policy_class": skill.policy_class,
            "visibility": skill.visibility.value,
            "retired": skill.retired,
            "retired_at": skill.retired_at.isoformat() if skill.retired_at else None,
            "retired_by": skill.retired_by,
            "created_at": skill.created_at.isoformat(),
            "created_by": skill.created_by,
            "proposer_agent": skill.proposer_agent,
            "proposal_task_id": skill.proposal_task_id,
            "current_version_id": skill.current_version_id,
            "current_version": skill.current_version,
            "current_content_hash": skill.current_content_hash,
            "versions": [_version_to_dict(v) for v in versions],
            "eligibility_rules": [_rule_to_dict(r) for r in rules],
            "materializations": [],
        }

    def list_catalog(
        self, db, page: int = 1, page_size: int = 20,
    ) -> tuple[list[dict], int]:
        """List all custom skills (catalog view)."""
        offset = (page - 1) * page_size
        skills, total = eligibility_stores.list_custom_skills(
            db, offset=offset, limit=page_size,
        )
        items = []
        for skill in skills:
            rules = eligibility_stores.get_eligibility_rules_for_skill(db, skill.skill_id)
            eligible_count = _count_eligible_agents(rules)
            items.append({
                "skill_id": skill.skill_id,
                "slug": skill.slug,
                "name": skill.name,
                "description": skill.description,
                "version": skill.current_version,
                "content_hash": skill.current_content_hash,
                "visibility": skill.visibility.value,
                "retired": skill.retired,
                "validation_passed": skill.last_validator_version is not None,
                "created_by": skill.created_by,
                "proposer_agent": skill.proposer_agent,
                "created_at": skill.created_at.isoformat(),
                "eligible_agents_count": eligible_count,
            })
        return items, total

    # ── Migration ─────────────────────────────────────────────────────────

    def migrate_legacy_proposals(
        self, db, org_root: Path | str | None = None,
    ) -> dict:
        """Migrate existing lifecycle proposals to custom_skills tables.

        Idempotent: skips already-migrated records.
        Preserves all provenance including legacy/null/invalid fixtures.
        """
        from .models import LifecycleStatus as LS

        conn = _get_raw_connection(db)
        migratable_statuses = [
            LS.PUBLISHED.value, LS.APPROVED.value,
            LS.VALIDATED.value, LS.IN_REVIEW.value,
            LS.DRAFT.value, LS.PROPOSED.value,
            LS.VALIDATION_FAILED.value,
        ]

        migrated = 0
        skipped = 0
        errors: list[str] = []

        for status in migratable_statuses:
            pkgs = lifecycle_stores.list_package_versions(db, status=LS(status))
            for pkg in pkgs:
                try:
                    skill_id = pkg.skill_id
                    # Skip if already migrated
                    existing = eligibility_stores.get_custom_skill(db, skill_id)
                    if existing is not None:
                        skipped += 1
                        continue

                    # Check for slug collision with existing custom skill
                    existing_by_slug = eligibility_stores.get_custom_skill_by_slug(db, pkg.slug)
                    if existing_by_slug is not None:
                        skipped += 1
                        continue

                    # Create custom skill record
                    is_published = pkg.status in (LS.PUBLISHED, LS.APPROVED)
                    version = pkg.version if is_published else None
                    content_hash = pkg.content_hash if is_published else None

                    skill_rec = CustomSkillRecord(
                        skill_id=skill_id,
                        slug=pkg.slug,
                        name=pkg.name,
                        description=pkg.description,
                        policy_class=pkg.policy_class,
                        visibility=CustomSkillVisibility.HIDDEN,
                        created_by=pkg.created_by or pkg.proposer_agent or "migration",
                        proposer_agent=pkg.proposer_agent,
                        proposal_task_id=pkg.proposal_task_id,
                        proposal_session_id=pkg.proposal_session_id,
                        current_version_id=pkg.id if is_published else None,
                        current_version=version,
                        current_content_hash=content_hash,
                        last_validated_at=pkg.reviewed_at if pkg.status == LS.APPROVED else None,
                    )
                    eligibility_stores.insert_custom_skill(db, skill_rec)

                    # Record migration event
                    lifecycle_stores.insert_lifecycle_event(db, lifecycle_stores.LifecycleEvent(
                        skill_id=skill_id,
                        package_version_id=pkg.id,
                        event_type="migrated_to_custom_skill",
                        actor="migration",
                        actor_role="service",
                        metadata={
                            "legacy_status": pkg.status.value,
                            "legacy_version": pkg.version,
                            "legacy_content_hash": pkg.content_hash,
                        },
                    ))

                    migrated += 1
                except Exception as exc:
                    errors.append(f"Error migrating {pkg.skill_id}: {exc}")
                    skipped += 1

        return {
            "migrated_count": migrated,
            "skipped_count": skipped,
            "errors": errors,
        }

    # ── Guards ────────────────────────────────────────────────────────────

    def _ensure_agent(self, actor_kind: str, action: str) -> None:
        if actor_kind != "agent":
            raise AgentForbiddenError(action)

    def _ensure_human(self, actor_kind: str, action: str) -> None:
        if actor_kind == "agent":
            raise AgentForbiddenError(action)

    def _ensure_non_empty(self, value: str, field: str) -> None:
        if not value or not value.strip():
            raise CustomSkillError(
                code="empty_field",
                detail=f"Field '{field}' must not be empty.",
                status_code=400,
            )

    def _ensure_protected_slug(self, slug: str) -> None:
        if slug in _PROTECTED_SLUGS or slug in _FIRST_PARTY_SLUGS:
            raise CustomSkillError(
                code="protected_slug",
                detail=f"Slug '{slug}' is a protected system or first-party slug and cannot be used for custom skills.",
                status_code=409,
            )

    def _ensure_policy_class(self, policy_class: str) -> None:
        if policy_class not in _ALLOWED_POLICY_CLASSES:
            raise CustomSkillError(
                code="policy_class_not_allowed",
                detail=f"Policy class '{policy_class}' is not allowed. Only 'standard_operational' is supported.",
                status_code=400,
            )


# ── Helpers ──────────────────────────────────────────────────────────────

def _rule_to_dict(rule: EligibilityRule) -> dict:
    return {
        "id": rule.id,
        "skill_id": rule.skill_id,
        "target_scope": rule.target_scope.value,
        "target_name": rule.target_name,
        "action": rule.action.value,
        "priority": rule.priority,
        "created_at": rule.created_at.isoformat(),
        "created_by": rule.created_by,
    }


def _version_to_dict(version: CustomSkillVersionRecord) -> dict:
    return {
        "id": version.id,
        "version": version.version,
        "content_hash": version.content_hash,
        "created_at": version.created_at.isoformat(),
        "created_by": version.created_by,
        "proposer_agent": version.proposer_agent,
        "proposal_task_id": version.proposal_task_id,
        "validation_passed": version.validation_passed,
        "validator_version": version.validator_version,
        "validation_findings": version.validation_findings,
    }


def _bump_version(current: str) -> str:
    """Bump the patch version of a semver string."""
    try:
        parts = current.split(".")
        parts[2] = str(int(parts[2]) + 1)
        return ".".join(parts)
    except (IndexError, ValueError):
        return "0.1.0"


def _build_agent_list(
    org_agents: list[str],
    org_teams: dict[str, list[str]],
    org_slug: str,
) -> list[tuple[str, str | None, str | None]]:
    """Build list of (agent_name, team_name, org_slug) for dry-run."""
    result: list[tuple[str, str | None, str | None]] = []
    seen: set[str] = set()
    for agent in org_agents:
        if agent not in seen:
            seen.add(agent)
            result.append((agent, None, org_slug))
    for team_name, agents in org_teams.items():
        for agent in agents:
            if agent not in seen:
                seen.add(agent)
                result.append((agent, team_name, org_slug))
    return result


def _count_eligible_agents(rules: list[EligibilityRule]) -> int:
    """Count distinct agents allowed by rules (approximate)."""
    agent_targets = set()
    for r in rules:
        if r.target_scope == EligibilityTargetScope.AGENT and r.action == EligibilityAction.ALLOW:
            agent_targets.add(r.target_name)
    return len(agent_targets)
