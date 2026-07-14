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
from runtime.orchestrator.org_config import OrgConfigError
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


def _assignments_for_skill(policy: dict, skill_id: str, org: OrgState | None = None, current_version: str | None = None) -> list[dict]:
    """Return per-agent assignment list for a skill_id.

    When org + current_version are provided (Phase 3b+), compares each agent's
    last-materialized version against current_version to determine effective.
    Otherwise (Phase 1 back-compat), all assignments are
    assigned_not_yet_effective.
    """
    agents_policy = policy.get("agents", {})
    result: list[dict] = []
    for agent_name, agent_rules in sorted(agents_policy.items()):
        if isinstance(agent_rules, dict):
            allows = agent_rules.get("allow", [])
            if isinstance(allows, list) and skill_id in allows:
                effective = False
                materialized_version = None
                if org is not None and current_version is not None:
                    mat_event = org.db.get_latest_skill_materialization(
                        skill_id, agent_name
                    )
                    if mat_event is not None and mat_event["version"] == current_version:
                        effective = True
                        materialized_version = mat_event["version"]
                    elif mat_event is not None:
                        materialized_version = mat_event["version"]
                result.append({
                    "agent": agent_name,
                    "assigned": True,
                    "effective": effective,
                    "materialized_version": materialized_version,
                    "state": "effective" if effective else "assigned_not_yet_effective",
                })
    return result


def _get_validation_state(org: OrgState, skill_id: str, version: str) -> str:
    """Determine validation state for a user-authored skill from the store.

    Returns 'validated' if the latest validation event for this version is ok,
    'failed_validation' if the latest for this version failed,
    'in_catalog' if no validation event exists.
    """
    latest = org.db.get_latest_skill_validation(skill_id, version=version)
    if latest is None:
        return "in_catalog"
    return "validated" if latest["ok"] else "failed_validation"


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
            # P2: check validation store for latest result
            validation_state = _get_validation_state(org, entry.id, entry.version)
        else:
            validation_state = "validated"

        # Agent count rollups — now backed by materialization store (Phase 3b)
        assigned_count = _assigned_agent_count(policy, entry.id)
        assignments = _assignments_for_skill(
            policy, entry.id, org=org, current_version=entry.version
        )
        effective_count = sum(1 for a in assignments if a["effective"])
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
                validation_state = _get_validation_state(org, entry.id, entry.version)
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
                    "ok": validation_state == "validated",
                    "errors": [],
                }
                result["assignments"] = _assignments_for_skill(
                    policy, entry.id, org=org, current_version=entry.version
                )

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
            # Check materialization store for version match (Phase 3b)
            mat_event = org.db.get_latest_skill_materialization(skill_id, agent_id)
            if mat_event is not None and mat_event["version"] == entry.version:
                provenance = "catalog_and_eligible"  # effective
            else:
                provenance = "assigned_not_yet_effective"
        elif is_exposed:
            provenance = "catalog_and_eligible"
        elif is_disabled:
            provenance = "hidden_because:disabled"
        elif is_denied:
            provenance = "hidden_because:denied_by_eligibility"
        elif skill_id in agent_allow_ids and source_type == "user_authored":
            # Assigned but resolve_exposed_skills didn't expose it
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


# ══════════════════════════════════════════════════════════════════════════════
# PHASE 2 — Write endpoints + validation guard
# ══════════════════════════════════════════════════════════════════════════════

import json
import shutil
import tempfile
import uuid as _uuid
from datetime import datetime, timezone

from pydantic import BaseModel, Field

from runtime.skills.system_contracts import SYSTEM_CONTRACTS as _SYSTEM_CONTRACTS


# ── Request models ──────────────────────────────────────────────────────

class CreateSkillRequest(BaseModel):
    slug: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1)
    version: str = Field(default="0.1.0")
    policy_class: str = Field(default="standard_operational")
    summary: str = Field(default="")
    skill_md: str = Field(..., min_length=1)
    references: dict[str, str] = Field(default_factory=dict)
    assets: dict[str, str] = Field(default_factory=dict)


class EditSkillRequest(BaseModel):
    name: str | None = None
    summary: str | None = None
    version: str | None = None
    skill_md: str | None = None
    references: dict[str, str] | None = None
    assets: dict[str, str] | None = None


class ValidateRequest(BaseModel):
    pass  # no body needed — validates current store content


class AssignSkillRequest(BaseModel):
    action: str = Field(..., pattern="^(allow|remove)$")


# ── Validation guard ────────────────────────────────────────────────────

def _collect_protected_slugs(org: OrgState) -> set[str]:
    """Collect all slugs that a user-authored skill cannot shadow."""
    slugs: set[str] = set()
    try:
        release = _release_registry(org)
        slugs.update(entry.slug for entry in release.list_all())
    except Exception:
        pass
    slugs.update(sc.id for sc in _SYSTEM_CONTRACTS)
    return slugs


def _validate_artifact_filename(fname: str) -> None:
    """Validate a references/assets filename for path-traversal safety.

    Raises ValueError for:
    - Empty filenames
    - Absolute paths (starts with '/')
    - '..' traversal segments
    - Directory targets (contains '/')
    """
    if not fname or not fname.strip():
        raise ValueError("Artifact filename is empty")
    if fname.startswith("/"):
        raise ValueError(f"Artifact filename '{fname}' is absolute; must be a relative filename")
    if "/" in fname:
        raise ValueError(f"Artifact filename '{fname}' is a directory path; must be a plain filename")
    if ".." in Path(fname).parts:
        raise ValueError(f"Artifact filename '{fname}' contains '..' traversal segment")


def _validate_skill_package(
    org: OrgState,
    slug: str,
    skill_id: str,
    name: str,
    version: str,
    policy_class: str,
    skill_md: str,
    references: dict[str, str] | None = None,
    assets: dict[str, str] | None = None,
) -> dict:
    """Run the technical validate guard on a user-authored skill package.

    Checks (v3 §8.3):
    (a) parses / well-formed — skill_md is non-empty string
    (b) required metadata present — id, slug, name, version must all be
        non-empty strings
    (c) SKILL.md present — skill_md is not empty/just-whitespace
    (d) references + assets resolve — if provided, must be dicts of
        string→string
    (e) NO bundled-slug collision — custom slug must not collide with
        release-shipped or system_contract slugs
    (f) custom cannot mint system_contract
    (g) dry-materialization assemble-check — assemble into a TEMP dir,
        assert clean materialization; never write to a live workspace

    Returns dict with keys: ok, errors (list[str]), reason_codes (list[str])
    """
    references = references or {}
    assets = assets or {}
    errors: list[str] = []
    reason_codes: list[str] = []

    # (a) well-formed — skill_md must be a non-empty string
    if not isinstance(skill_md, str) or not skill_md.strip():
        errors.append("SKILL.md content is empty or missing")
        reason_codes.append("skill_md_empty")

    # (b) required metadata: id, slug, name, version
    if not skill_id or not isinstance(skill_id, str) or not skill_id.strip():
        errors.append("Required metadata 'id' is missing")
        reason_codes.append("missing_id")
    if not slug or not isinstance(slug, str) or not slug.strip():
        errors.append("Required metadata 'slug' is missing")
        reason_codes.append("missing_slug")
    if not name or not isinstance(name, str) or not name.strip():
        errors.append("Required metadata 'name' is missing")
        reason_codes.append("missing_name")
    if not version or not isinstance(version, str) or not version.strip():
        errors.append("Required metadata 'version' is missing")
        reason_codes.append("missing_version")

    # (c) SKILL.md present — already covered by (a) plus heading check
    if skill_md.strip() and not skill_md.strip().startswith("#"):
        errors.append("SKILL.md must start with a heading")
        reason_codes.append("skill_md_no_heading")

    # (d) references + assets resolve
    if not isinstance(references, dict):
        errors.append("'references' must be a map of filename→content")
        reason_codes.append("invalid_references_type")
    else:
        for k, v in references.items():
            if not isinstance(k, str) or not isinstance(v, str):
                errors.append(f"Reference '{k}' has an invalid value type")
                reason_codes.append("invalid_reference_value")
                break
            try:
                _validate_artifact_filename(k)
            except ValueError as exc:
                errors.append(f"Invalid reference filename: {exc}")
                reason_codes.append("invalid_reference_filename")
                break
    if not isinstance(assets, dict):
        errors.append("'assets' must be a map of filename→content")
        reason_codes.append("invalid_assets_type")
    else:
        for k, v in assets.items():
            if not isinstance(k, str) or not isinstance(v, str):
                errors.append(f"Asset '{k}' has an invalid value type")
                reason_codes.append("invalid_asset_value")
                break
            try:
                _validate_artifact_filename(k)
            except ValueError as exc:
                errors.append(f"Invalid asset filename: {exc}")
                reason_codes.append("invalid_asset_filename")
                break

    # (e) NO bundled-slug collision
    protected_slugs = _collect_protected_slugs(org)
    if slug in protected_slugs:
        errors.append(f"Slug '{slug}' collides with a release-shipped or system-contract skill")
        reason_codes.append("slug_collision")

    # (f) custom cannot mint system_contract
    if policy_class == "system_contract":
        errors.append("User-authored skills cannot use policy_class 'system_contract'")
        reason_codes.append("system_contract_forbidden")

    # (g) dry-materialization assemble-check
    try:
        _dry_materialize(slug, skill_md, references, assets)
    except Exception as exc:
        errors.append(f"Dry materialization failed: {exc}")
        reason_codes.append("materialization_error")

    return {
        "ok": len(errors) == 0,
        "errors": errors,
        "reason_codes": reason_codes,
    }


def _dry_materialize(
    slug: str,
    skill_md: str,
    references: dict[str, str],
    assets: dict[str, str],
) -> None:
    """Dry-run materialization: assemble into a temp dir, verify it's clean.

    Never writes to a live workspace — operates entirely in a temp dir.
    """
    tmp = Path(tempfile.mkdtemp(prefix="skill_dry_mat_"))
    try:
        pkg_dir = tmp / slug
        pkg_dir.mkdir(parents=True)
        # Write SKILL.md
        (pkg_dir / "SKILL.md").write_text(skill_md, encoding="utf-8")
        # Write skill.yaml
        (pkg_dir / "skill.yaml").write_text(
            yaml.dump({
                "id": f"hr:{slug}",
                "slug": slug,
                "name": slug.replace("-", " ").title(),
                "version": "0.1.0",
                "description": "",
                "when_to_use": "",
                "owner": "operator",
                "source": "user_authored",
                "policy_class": "standard_operational",
                "status": "enabled",
            }),
            encoding="utf-8",
        )
        # Write references
        if references:
            ref_dir = pkg_dir / "references"
            ref_dir.mkdir()
            for fname, content in references.items():
                _validate_artifact_filename(fname)
                (ref_dir / fname).write_text(content, encoding="utf-8")
        # Write assets
        if assets:
            assets_dir = pkg_dir / "assets"
            assets_dir.mkdir()
            for fname, content in assets.items():
                _validate_artifact_filename(fname)
                (assets_dir / fname).write_text(content, encoding="utf-8")
        # Verify SKILL.md is present on disk
        if not (pkg_dir / "SKILL.md").is_file():
            raise FileNotFoundError("SKILL.md missing after materialization")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ── Store write helpers ─────────────────────────────────────────────────

def _write_user_skill_to_store(
    org: OrgState,
    slug: str,
    skill_id: str,
    name: str,
    version: str,
    summary: str,
    policy_class: str,
    skill_md: str,
    references: dict[str, str] | None = None,
    assets: dict[str, str] | None = None,
) -> None:
    """Persist a user-authored skill to the per-org store (§6).

    Store directory: <org.root>/skills/<slug>/
    """
    references = references or {}
    assets = assets or {}
    pkg_dir = org.root / "skills" / slug
    pkg_dir.mkdir(parents=True, exist_ok=True)

    # Write SKILL.md
    (pkg_dir / "SKILL.md").write_text(skill_md, encoding="utf-8")

    # Write skill.yaml
    skill_yaml = {
        "id": skill_id,
        "slug": slug,
        "name": name,
        "version": version,
        "description": summary,
        "when_to_use": "",
        "owner": "operator",
        "source": "user_authored",
        "policy_class": policy_class,
        "status": "enabled",
    }
    (pkg_dir / "skill.yaml").write_text(yaml.dump(skill_yaml), encoding="utf-8")

    # Write references
    if references:
        ref_dir = pkg_dir / "references"
        ref_dir.mkdir(exist_ok=True)
        for fname, content in references.items():
            try:
                _validate_artifact_filename(fname)
            except ValueError:
                # Belt-and-suspenders: skip unsafe filenames — do NOT write
                # them.  The validation guard has already recorded the error
                # so the skill stays in draft state.
                continue
            (ref_dir / fname).write_text(content, encoding="utf-8")

    # Write assets
    if assets:
        assets_dir = pkg_dir / "assets"
        assets_dir.mkdir(exist_ok=True)
        for fname, content in assets.items():
            try:
                _validate_artifact_filename(fname)
            except ValueError:
                continue
            (assets_dir / fname).write_text(content, encoding="utf-8")


def _record_validation_event(
    org: OrgState,
    skill_id: str,
    slug: str,
    agent: str | None,
    version: str,
    validation_result: dict,
) -> None:
    """Record a validation event in the skill_validation_events store."""
    severity = "pass" if validation_result["ok"] else "error"
    org.db.insert_skill_validation_event(
        skill_id=skill_id,
        slug=slug,
        agent=agent,
        source="user_authored",
        severity=severity,
        ok=validation_result["ok"],
        version=version,
        findings=validation_result["errors"],
        reason_codes=validation_result["reason_codes"],
    )


def _is_editable(entry: SkillEntry) -> tuple[bool, int, str]:
    """Check if a skill is editable (v3 §9.5).

    Returns (editable, status_code, error_code).
    - user_authored → editable
    - first_party/runtime → 409 skill_not_editable
    - system_contract → 403 system_contract_read_only
    """
    if entry.policy_class == PolicyClass.SYSTEM_CONTRACT:
        return False, 403, "system_contract_read_only"
    if entry.source == "user_authored":
        return True, 200, ""
    return False, 409, "skill_not_editable"


# ── Route: POST /skills (create/import) ──────────────────────────────────

@router.post("/skills", status_code=201)
def create_skill(
    slug: str,
    org: OrgDep,
    body: CreateSkillRequest,
) -> dict:
    """Create/import a user-authored skill.

    Runs the technical validate guard synchronously.
    - Content-validation failure → 201 with validation.ok=false (draft saved)
    - Malformed request → 422 (nothing persisted)
    """
    skill_id = f"hr:{body.slug}"

    # Validate
    result = _validate_skill_package(
        org=org,
        slug=body.slug,
        skill_id=skill_id,
        name=body.name,
        version=body.version,
        policy_class=body.policy_class,
        skill_md=body.skill_md,
        references=body.references,
        assets=body.assets,
    )

    # Always persist (even on content-validation failure — v3 §9.1)
    _write_user_skill_to_store(
        org=org,
        slug=body.slug,
        skill_id=skill_id,
        name=body.name,
        version=body.version,
        summary=body.summary,
        policy_class=body.policy_class,
        skill_md=body.skill_md,
        references=body.references,
        assets=body.assets,
    )

    # Record validation event
    _record_validation_event(
        org=org,
        skill_id=skill_id,
        slug=body.slug,
        agent=None,
        version=body.version,
        validation_result=result,
    )

    validation_state = "validated" if result["ok"] else "in_catalog"
    return {
        "skill_id": skill_id,
        "source": "user_authored",
        "validation_state": validation_state,
        "validation": {"ok": result["ok"], "errors": result["errors"]},
    }


# ── Route: POST /skills/{skill_id}/validate ──────────────────────────────

@router.post("/skills/{skill_id}/validate")
def validate_skill(
    slug: str,
    skill_id: str,
    org: OrgDep,
) -> dict:
    """Re-run the technical validate guard on an existing user-authored skill.

    Reads the skill from the per-org store and re-validates.
    Never mutates content.
    """
    # Find the skill in the union catalog
    union = _union_catalog(org)
    for entry, source_type in union:
        if entry.id == skill_id:
            if source_type != "user_authored":
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={"code": "skill_not_user_authored"},
                )
            # Read the skill_md from the store
            pkg_dir = org.root / "skills" / entry.slug
            skill_md_path = pkg_dir / "SKILL.md"
            if not skill_md_path.is_file():
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail={"code": "skill_content_missing", "skill_id": skill_id},
                )
            skill_md = skill_md_path.read_text(encoding="utf-8")

            # Load stored references and assets RECURSIVELY for re-validation.
            # Nested entries (e.g. references/subdir/evil.md) must not be
            # silently skipped — their relative name includes '/' which
            # _validate_artifact_filename rejects, yielding validation.ok=false.
            stored_refs: dict[str, str] = {}
            ref_dir = pkg_dir / "references"
            if ref_dir.is_dir():
                for fpath in sorted(ref_dir.rglob("*"), key=lambda p: str(p)):
                    if fpath.is_file():
                        rel_name = str(fpath.relative_to(ref_dir))
                        stored_refs[rel_name] = fpath.read_text(encoding="utf-8")

            stored_assets: dict[str, str] = {}
            assets_dir = pkg_dir / "assets"
            if assets_dir.is_dir():
                for fpath in sorted(assets_dir.rglob("*"), key=lambda p: str(p)):
                    if fpath.is_file():
                        rel_name = str(fpath.relative_to(assets_dir))
                        stored_assets[rel_name] = fpath.read_text(encoding="utf-8")

            result = _validate_skill_package(
                org=org,
                slug=entry.slug,
                skill_id=skill_id,
                name=entry.name,
                version=entry.version,
                policy_class=(entry.policy_class.value
                              if isinstance(entry.policy_class, PolicyClass)
                              else str(entry.policy_class)),
                skill_md=skill_md,
                references=stored_refs,
                assets=stored_assets,
            )

            _record_validation_event(
                org=org,
                skill_id=skill_id,
                slug=entry.slug,
                agent=None,
                version=entry.version,
                validation_result=result,
            )

            validation_state = "validated" if result["ok"] else "in_catalog"
            return {
                "skill_id": skill_id,
                "validation_state": validation_state,
                "validation": {"ok": result["ok"], "errors": result["errors"]},
            }

    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"code": "not_found", "skill_id": skill_id},
    )


# ── Route: PATCH /skills/{skill_id} (edit) ───────────────────────────────

@router.patch("/skills/{skill_id}")
def edit_skill(
    slug: str,
    skill_id: str,
    org: OrgDep,
    body: EditSkillRequest,
) -> dict:
    """Edit a user-authored skill (v3 §9.5).

    Only user_authored skills are editable.
    managed/system_contract → 409/403.

    DRAFT-PERSIST-ON-CONTENT-FAILURE (v3 §9.5 + MEM-288):
    - Well-formed request but content fails validation → 200 with
      validation.ok=false (draft IS saved)
    - Malformed request (no editable fields supplied) → 422 (nothing saved)
    """
    # Check if any editable field is present
    if (body.name is None and body.summary is None and body.version is None
            and body.skill_md is None and body.references is None
            and body.assets is None):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "malformed_request",
                "errors": ["no editable fields supplied"],
            },
        )

    # Find the skill in the union catalog
    union = _union_catalog(org)
    for entry, source_type in union:
        if entry.id == skill_id:
            # Editability gate
            editable, gate_status, gate_code = _is_editable(entry)
            if not editable:
                raise HTTPException(
                    status_code=gate_status,
                    detail={"code": gate_code},
                )

            # Determine the effective new values (merge with existing)
            pkg_dir = org.root / "skills" / entry.slug
            existing_skill_md = ""
            skill_md_path = pkg_dir / "SKILL.md"
            if skill_md_path.is_file():
                existing_skill_md = skill_md_path.read_text(encoding="utf-8")

            new_name = body.name if body.name is not None else entry.name
            new_version = body.version if body.version is not None else entry.version
            new_summary = body.summary if body.summary is not None else entry.description
            new_skill_md = body.skill_md if body.skill_md is not None else existing_skill_md
            new_references = body.references if body.references is not None else {}
            new_assets = body.assets if body.assets is not None else {}
            policy_class_val = (entry.policy_class.value
                                if isinstance(entry.policy_class, PolicyClass)
                                else str(entry.policy_class))

            # Run validation
            result = _validate_skill_package(
                org=org,
                slug=entry.slug,
                skill_id=skill_id,
                name=new_name,
                version=new_version,
                policy_class=policy_class_val,
                skill_md=new_skill_md,
                references=new_references,
                assets=new_assets,
            )

            # Always persist even on content-validation failure
            _write_user_skill_to_store(
                org=org,
                slug=entry.slug,
                skill_id=skill_id,
                name=new_name,
                version=new_version,
                summary=new_summary,
                policy_class=policy_class_val,
                skill_md=new_skill_md,
                references=new_references,
                assets=new_assets,
            )

            _record_validation_event(
                org=org,
                skill_id=skill_id,
                slug=entry.slug,
                agent=None,
                version=new_version,
                validation_result=result,
            )

            validation_state = "validated" if result["ok"] else "in_catalog"
            return {
                "skill_id": skill_id,
                "source": "user_authored",
                "validation_state": validation_state,
                "validation": {"ok": result["ok"], "errors": result["errors"]},
                "version": new_version,
            }

    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"code": "not_found", "skill_id": skill_id},
    )


# ── Route: GET /skills/validation (Runtime Validation) ───────────────────

@router.get("/skills/validation")
def skills_validation(
    slug: str,
    org: OrgDep,
    skill: str | None = Query(None, description="Filter by skill_id"),
    agent: str | None = Query(None, description="Filter by agent"),
    source: str | None = Query(None, description="Filter by source"),
    since: str | None = Query(None, description="ISO timestamp filter (>=)"),
    severity: str | None = Query(None, description="Filter by severity"),
    limit: int = Query(100, ge=1, le=500),
) -> dict:
    """Runtime Validation read surface.

    Filterable by skill, agent, source, time, severity.
    Label: 'Runtime Validation', NOT 'Audit'.
    """
    events = org.db.list_skill_validation_events(
        skill_id=skill,
        agent=agent,
        source=source,
        since=since,
        severity=severity,
        limit=limit,
    )
    return {"events": events, "label": "Runtime Validation"}


# ── Route: POST /agents/{agent_id}/skills/{skill_id}/assign ──────────────

@router.post("/agents/{agent_id}/skills/{skill_id}/assign")
def assign_skill(
    slug: str,
    agent_id: str,
    skill_id: str,
    org: OrgDep,
    body: AssignSkillRequest,
) -> dict:
    """Assign or unassign a skill to a single agent (v3 §9.3).

    Scoped eligibility write: single agent + single skill.
    - Precondition: the skill's current store version must be validated
      (a skill whose current version failed the technical guard cannot be
      assigned — retained correctness gate, ruling 2).
    - Assign = append an allow rule; remove = the inverse.
    - Writes through the scoped eligibility writer ONLY.
    - On success the assignment is durable + audited.
    - The skill stays assigned_not_yet_effective (Phase 3b materialization
      is NOT in this slice).
    """
    # Validate agent exists
    agent_def_path = org.root / "org" / "agents" / f"{agent_id}.md"
    if not agent_def_path.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "agent_not_found", "agent_id": agent_id},
        )

    # Find the skill in the union catalog
    union = _union_catalog(org)
    skill_entry = None
    skill_source_type = None
    for entry, source_type in union:
        if entry.id == skill_id:
            skill_entry = entry
            skill_source_type = source_type
            break

    if skill_entry is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "not_found", "skill_id": skill_id},
        )

    # Only user_authored skills can be assigned (v1 constraint)
    if skill_source_type != "user_authored":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "skill_not_assignable",
                    "reason": "Only user-authored skills can be assigned via this endpoint"},
        )

    # Precondition: current store version must be validated
    validation_state = _get_validation_state(org, skill_id, skill_entry.version)
    if validation_state != "validated":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "skill_not_validated",
                "validation_state": validation_state,
            },
        )

    # Read before state for audit
    policy_before = _read_eligibility_policy(org)
    agent_before = {}
    agents_before = policy_before.get("agents", {})
    if isinstance(agents_before, dict):
        agent_before = agents_before.get(agent_id, {})

    # Write through the scoped eligibility writer
    from runtime.orchestrator._paths import OrgPaths
    from runtime.orchestrator.org_config import write_skill_eligibility_entry

    try:
        write_skill_eligibility_entry(
            OrgPaths(root=org.root),
            agent=agent_id,
            skill_id=skill_id,
            action=body.action,
        )
    except OrgConfigError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "config_validation_failed", "error": str(exc)},
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "invalid_action", "error": str(exc)},
        )

    # Read after state for audit
    policy_after = _read_eligibility_policy(org)
    agent_after = {}
    agents_after = policy_after.get("agents", {})
    if isinstance(agents_after, dict):
        agent_after = agents_after.get(agent_id, {})

    # Emit audit row under config:skills:eligibility scope
    from runtime.infrastructure.audit_logger import AuditLogger
    AuditLogger(org.db).log_skills_config_write(
        subsection="eligibility",
        tiers=[agent_id],
        before=agent_before,
        after=agent_after,
        actor="operator",
    )

    # Determine new assignment state
    assigned = skill_id in (agent_after.get("allow") or [])

    return {
        "agent_id": agent_id,
        "skill_id": skill_id,
        "state": "assigned" if assigned else "unassigned",
        "effective_hint": "assigned_not_yet_effective" if assigned else None,
        "materializes_on": "next_session_spawn" if assigned else None,
    }


# ── Route: GET /skills/{skill_id}/status (lifecycle status) ──────────────

@router.get("/skills/{skill_id}/status")
def skill_status(
    slug: str,
    skill_id: str,
    org: OrgDep,
    agent: str | None = Query(None, description="Filter to a specific agent"),
) -> dict:
    """Read-only projection of the four-state model (§7.1/§7.4) for a skill.

    Compares each agent's last-materialized version to the current store
    version (§0.4). A skill is `effective` for an agent iff:
    (assigned == true) AND (last-materialized-version == current-store-version).

    `assigned_not_yet_effective` when assigned but versions differ (or not yet
    materialized).

    Query params:
    - agent: filter assignments to a specific agent (optional)
    """
    union = _union_catalog(org)
    policy = _read_eligibility_policy(org)

    # Find the skill in the union catalog
    skill_entry = None
    skill_source_type = None
    for entry, source_type in union:
        if entry.id == skill_id:
            skill_entry = entry
            skill_source_type = source_type
            break

    if skill_entry is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "not_found", "skill_id": skill_id},
        )

    # Determine validation state
    if skill_source_type == "user_authored":
        validation_state = _get_validation_state(org, skill_id, skill_entry.version)
    else:
        validation_state = "validated"

    # Last validation event
    last_validation = org.db.get_latest_skill_validation(skill_id, version=skill_entry.version)
    validation_block = None
    if last_validation is not None:
        validation_block = {
            "ok": last_validation["ok"],
            "version": last_validation.get("version"),
            "at": last_validation.get("created_at"),
        }

    # Build assignments[] — per-agent lifecycle state
    assignments: list[dict] = []
    agents_policy = policy.get("agents", {})

    for agent_name in sorted(agents_policy.keys()):
        if agent is not None and agent_name != agent:
            continue

        agent_rules = agents_policy.get(agent_name)
        if not isinstance(agent_rules, dict):
            continue

        allows = agent_rules.get("allow", []) or []
        assigned = skill_id in allows

        if not assigned:
            continue

        # Check materialization: latest mat event for this (skill, agent)
        mat_event = org.db.get_latest_skill_materialization(skill_id, agent_name)

        if mat_event is not None and mat_event["version"] == skill_entry.version:
            effective = True
            materialized_version = mat_event["version"]
            agent_state = "effective"
        else:
            effective = False
            materialized_version = mat_event["version"] if mat_event else None
            agent_state = "assigned_not_yet_effective"

        assignments.append({
            "agent": agent_name,
            "assigned": True,
            "effective": effective,
            "materialized_version": materialized_version,
            "state": agent_state,
        })

    return {
        "skill_id": skill_id,
        "source": skill_source_type,
        "in_catalog": True,
        "validated": validation_state == "validated",
        "current_version": skill_entry.version,
        "assignments": assignments,
        "last_validation": validation_block,
    }
