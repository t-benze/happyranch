"""Skills web-surface endpoints — PHASE 1 read foundation.

Three GET endpoints, all bearer-authed + org-scoped:
- GET /skills/catalog — union managed catalog + system contracts + user store
- GET /skills/catalog/{skill_id} — single-skill detail
- GET /agents/{agent_id}/skills/effective — effective + hidden skills with provenance

Per the THR-092 v3 endpoint spec (engineering_manager-2026-07-13-skills-web-v1-endpoint-spec-v3.md).
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import yaml
from fastapi import APIRouter, HTTPException, Query, status

from runtime.config import settings
from runtime.daemon.auth import require_token
from runtime.daemon.org_state import OrgState
from runtime.daemon.routes._org_dep import OrgDep
from runtime.skills.exposure import catalog_gate, resolve_exposed_skills
from runtime.skills.models import PolicyClass, SkillEntry, SkillStatus
from runtime.skills.registry import SkillRegistry
from runtime.skills.resolver import EligibilityResolver
from runtime.skills.system_contracts import SYSTEM_CONTRACTS

router = APIRouter(dependencies=[require_token()])

# ── Helpers ──────────────────────────────────────────────────────────────

def _release_registry(org: OrgState) -> SkillRegistry:
    """Return the release-shipped managed-catalog registry.

    Loads skills from the project root's runtime/skills/ directory.
    Also checks org.root/runtime/skills/ for test fixtures seeded there
    (merges into the same registry; project-root entries take priority).
    """
    release_dir = settings.project_root / "runtime" / "skills"
    registry = SkillRegistry(skills_root=release_dir)

    # Also load from org-root runtime/skills/ (test fixtures)
    org_skills = org.root / "runtime" / "skills"
    if org_skills.is_dir() and org_skills != release_dir:
        org_registry = SkillRegistry(skills_root=org_skills)
        for entry in org_registry.list_all():
            if entry.id not in registry._entries:
                registry._entries[entry.id] = entry

    return registry


def _user_registry(org: OrgState) -> SkillRegistry:
    """Return the per-org user-skill store registry.

    Store directory: <org_root>/skills/ — a SIBLING of the org/ definition
    directory, NOT inside it (v3 s6.2). Missing / empty dir → empty
    SkillRegistry (graceful).
    """
    user_dir = org.root / "skills"
    return SkillRegistry(skills_root=user_dir)


def _union_catalog(org: OrgState) -> list[tuple[SkillEntry, str]]:
    """Build the union of managed catalog, system contracts, and user store.

    Returns list of (SkillEntry, source_type) where source_type is one of:
    'managed', 'system_contract', 'user_authored'.

    Release-wins on slug collision: a user-authored skill can NEVER shadow a
    release-shipped skill; the release entry is kept and the user entry
    discarded.
    """
    release = _release_registry(org)
    user = _user_registry(org)

    union: dict[str, tuple[SkillEntry, str]] = {}

    # Release entries are authoritative — collect their slugs so user-authored
    # entries with a colliding slug are discarded (release-wins on SLUG, not id;
    # v3 s6.3: a user package cannot shadow a shipped skill by reusing its slug
    # under a different id).
    release_slugs: set[str] = {entry.slug for entry in release.list_all()}
    sc_slugs: set[str] = {sc.id for sc in SYSTEM_CONTRACTS}
    protected_slugs = release_slugs | sc_slugs

    # User-authored entries — discard any whose slug collides with a release
    # or system-contract slug (release-wins on slug collision, v3 s6.3).
    for entry in user.list_all():
        if entry.slug not in protected_slugs:
            union[entry.id] = (entry, "user_authored")

    # Release entries added after users so any residual id collision resolves
    # to release (id-based backup — slug-based gate already handles the
    # canonical case).
    for entry in release.list_all():
        union[entry.id] = (entry, "managed")

    # System contracts — not in either registry
    for sc in SYSTEM_CONTRACTS:
        sc_entry = SkillEntry(
            id=f"hr:{sc.id}",
            slug=sc.id,
            name=sc.name,
            version="1.0.0",
            description=sc.description,
            when_to_use=sc.when_to_use,
            owner="runtime",
            source="first_party",
            policy_class=PolicyClass.SYSTEM_CONTRACT,
            status=SkillStatus.ENABLED,
        )
        union[sc_entry.id] = (sc_entry, "system_contract")

    return list(union.values())


def _read_eligibility_policy(org: OrgState) -> dict:
    """Read the skills eligibility policy from org/config.yaml.

    Returns the 'skills' section as a dict, or empty dict if not present.
    """
    config_path = org.root / "org" / "config.yaml"
    if not config_path.is_file():
        return {}
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            return raw.get("skills", {})
    except (yaml.YAMLError, OSError):
        pass
    return {}


def _assigned_agent_count(policy: dict, skill_id: str) -> int:
    """Count how many agents have an allow rule for this skill_id."""
    agents_policy = policy.get("agents", {})
    count = 0
    for agent_name, agent_rules in agents_policy.items():
        if isinstance(agent_rules, dict):
            allows = agent_rules.get("allow", [])
            if isinstance(allows, list) and skill_id in allows:
                count += 1
    return count


def _assignments_for_skill(policy: dict, skill_id: str) -> list[dict]:
    """Return per-agent assignment list for a skill_id.

    P1: effective is always False (no materialization store).
    All assignments are assigned_not_yet_effective.
    """
    agents_policy = policy.get("agents", {})
    result: list[dict] = []
    for agent_name, agent_rules in sorted(agents_policy.items()):
        if isinstance(agent_rules, dict):
            allows = agent_rules.get("allow", [])
            if isinstance(allows, list) and skill_id in allows:
                result.append({
                    "agent": agent_name,
                    "assigned": True,
                    "effective": False,  # P1: no materialization store
                    "state": "assigned_not_yet_effective",
                })
    return result


# ── Route: GET /skills/catalog ────────────────────────────────────────────

@router.get("/skills/catalog")
def skills_catalog(
    slug: str,
    org: OrgDep,
    filter: Optional[str] = Query(None, alias="filter", description="Bundled or Custom"),
) -> dict:
    """Return the union catalog: managed + system_contract + user_authored.

    Query params:
    - filter: 'Bundled' (managed + system_contract) or 'Custom' (user_authored only)
    """
    union = _union_catalog(org)
    policy = _read_eligibility_policy(org)

    items = []
    for entry, source_type in union:
        # Filter
        if filter == "Bundled" and source_type == "user_authored":
            continue
        if filter == "Custom" and source_type != "user_authored":
            continue

        # Visibility category
        if entry.policy_class == PolicyClass.SYSTEM_CONTRACT:
            visibility_category = "read_only"
        else:
            visibility_category = "toggleable"

        # System contract flag
        is_system_contract = entry.policy_class == PolicyClass.SYSTEM_CONTRACT

        # Validation state
        if source_type == "user_authored":
            # P1: no validation store → always in_catalog
            validation_state = "in_catalog"
        else:
            validation_state = "validated"

        # Agent count rollups
        assigned_count = _assigned_agent_count(policy, entry.id)
        # P1: no materialization store → effective_agent_count is always 0
        effective_count = 0
        has_stale = assigned_count > effective_count

        items.append({
            "skill_id": entry.id,
            "name": entry.name,
            "type": source_type,
            "source": entry.source,
            "system_contract": is_system_contract,
            "visibility_category": visibility_category,
            "policy_class": (entry.policy_class.value
                             if isinstance(entry.policy_class, PolicyClass)
                             else str(entry.policy_class)),
            "status": (entry.status.value
                       if isinstance(entry.status, SkillStatus)
                       else str(entry.status)),
            "version": entry.version,
            "validation_state": validation_state,
            "assigned_agent_count": assigned_count,
            "effective_agent_count": effective_count,
            "has_assigned_not_yet_effective": has_stale,
            "summary": entry.description,
        })

    # Sort: system_contract first, then managed, then user_authored
    type_order = {"system_contract": 0, "managed": 1, "user_authored": 2}
    items.sort(key=lambda x: (type_order.get(x["type"], 99), x["name"].lower()))

    return {"items": items}


# ── Route: GET /skills/catalog/{skill_id} ──────────────────────────────────

@router.get("/skills/catalog/{skill_id}")
def skills_catalog_detail(
    slug: str,
    skill_id: str,
    org: OrgDep,
) -> dict:
    """Return single-skill detail.

    For user_authored skills, includes validation block + assignments[].
    """
    union = _union_catalog(org)
    policy = _read_eligibility_policy(org)

    for entry, source_type in union:
        if entry.id == skill_id:
            is_system_contract = entry.policy_class == PolicyClass.SYSTEM_CONTRACT
            visibility_category = "read_only" if is_system_contract else "toggleable"

            if source_type == "user_authored":
                validation_state = "in_catalog"  # P1
            else:
                validation_state = "validated"

            result = {
                "skill_id": entry.id,
                "name": entry.name,
                "type": source_type,
                "source": entry.source,
                "system_contract": is_system_contract,
                "visibility_category": visibility_category,
                "policy_class": (entry.policy_class.value
                                 if isinstance(entry.policy_class, PolicyClass)
                                 else str(entry.policy_class)),
                "status": (entry.status.value
                           if isinstance(entry.status, SkillStatus)
                           else str(entry.status)),
                "version": entry.version,
                "validation_state": validation_state,
                "summary": entry.description,
                "description": entry.description,
                "when_to_use": entry.when_to_use,
                "owner": entry.owner,
            }

            # User-authored: include validation block + assignments
            if source_type == "user_authored":
                result["validation"] = {
                    "ok": True,  # P1: no validation store
                    "errors": [],
                }
                result["assignments"] = _assignments_for_skill(policy, entry.id)

            return result

    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"code": "not_found", "skill_id": skill_id},
    )


# ── Route: GET /agents/{agent_id}/skills/effective ─────────────────────────

@router.get("/agents/{agent_id}/skills/effective")
def agent_skills_effective(
    slug: str,
    agent_id: str,
    org: OrgDep,
) -> dict:
    """Return effective (exposed) AND hidden skills for one agent.

    Each skill carries a structured provenance reason code.
    Derived from resolve_exposed_skills (exposure.py, two gates) +
    EligibilityResolver (resolver.py).
    """
    # Validate agent exists
    agent_def_path = org.root / "org" / "agents" / f"{agent_id}.md"
    if not agent_def_path.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "agent_not_found", "agent_id": agent_id},
        )

    release = _release_registry(org)
    user = _user_registry(org)
    policy = _read_eligibility_policy(org)

    # Determine team from agent def
    team = "engineering"
    try:
        from runtime.orchestrator.prompt_loader import load_agent
        from runtime.orchestrator._paths import OrgPaths
        agent_def = load_agent(OrgPaths(root=org.root), agent_id)
        if agent_def is not None:
            team = agent_def.team
    except Exception:
        pass

    # Union catalog for resolution — release-wins on SLUG collision (v3 s6.3)
    release_slugs: set[str] = {entry.slug for entry in release.list_all()}
    union: dict[str, tuple[SkillEntry, str]] = {}
    for entry in user.list_all():
        if entry.slug not in release_slugs:
            union[entry.id] = (entry, "user_authored")
    for entry in release.list_all():
        union[entry.id] = (entry, "managed")

    catalog = [e for e, _ in union.values()]
    resolver = EligibilityResolver(policy)

    try:
        exposed = resolve_exposed_skills(
            registry=_build_registry_from_entries(catalog),
            resolver=resolver,
            org=slug,
            team=team,
            agent=agent_id,
        )
    except Exception:
        exposed = []

    exposed_ids = {es.skill.id for es in exposed}

    # Collect all rules for this agent
    agent_allow_ids: set[str] = set()
    agent_deny_ids: set[str] = set()
    agents_policy = policy.get("agents", {})
    agent_rules = agents_policy.get(agent_id, {})
    if isinstance(agent_rules, dict):
        agent_allow_ids = set(agent_rules.get("allow", []) or [])
        agent_deny_ids = set(agent_rules.get("deny", []) or [])

    # Build response: all union entries + system contracts
    skills = []

    for entry, source_type in union.values():
        skill_id = entry.id
        is_exposed = skill_id in exposed_ids
        is_disabled = entry.status == SkillStatus.DISABLED
        is_denied = skill_id in agent_deny_ids

        # Determine provenance reason
        if is_exposed and source_type == "user_authored":
            # P1: no current-version materialization signal to prove effectiveness.
            # An assigned+eligible user_authored skill surfaces as
            # assigned_not_yet_effective, NOT effective/catalog_and_eligible.
            provenance = "assigned_not_yet_effective"
        elif is_exposed:
            provenance = "catalog_and_eligible"
        elif is_disabled:
            provenance = "hidden_because:disabled"
        elif is_denied:
            provenance = "hidden_because:denied_by_eligibility"
        elif skill_id in agent_allow_ids and source_type == "user_authored":
            # Assigned but resolve_exposed_skills didn't expose it
            # (e.g., failed materialization check — conservatively hidden in P1)
            provenance = "assigned_not_yet_effective"
            is_exposed = True  # surface it as assigned, not hidden
        else:
            provenance = "hidden_because:not_in_eligibility"

        skills.append({
            "skill_id": skill_id,
            "name": entry.name,
            "type": source_type,
            "source": entry.source,
            "status": (entry.status.value
                       if isinstance(entry.status, SkillStatus)
                       else str(entry.status)),
            "version": entry.version,
            "provenance": provenance,
            "hidden": not is_exposed,
            "summary": entry.description,
        })

    # System contracts appear as read-only, always visible
    for sc in SYSTEM_CONTRACTS:
        sc_id = f"hr:{sc.id}"
        # Only add if not already in the catalog (shouldn't happen, but safety)
        if not any(s["skill_id"] == sc_id for s in skills):
            skills.append({
                "skill_id": sc_id,
                "name": sc.name,
                "type": "system_contract",
                "source": "first_party",
                "status": "enabled",
                "version": "1.0.0",
                "provenance": "catalog_and_eligible",
                "hidden": False,
                "summary": sc.description,
            })

    skills.sort(key=lambda x: (x["hidden"], x["name"].lower()))
    return {"skills": skills, "agent_id": agent_id}


def _build_registry_from_entries(entries: list[SkillEntry]) -> SkillRegistry:
    """Build a SkillRegistry from a list of entries (for resolver use).

    This avoids modifying SkillRegistry's constructor; we create an empty
    registry and directly inject entries.
    """
    # The simplest approach: use a registry rooted on a non-existent dir,
    # then manually populate the internal dict.
    registry = SkillRegistry(skills_root=Path("/nonexistent"))
    for entry in entries:
        registry._entries[entry.id] = entry
    return registry
