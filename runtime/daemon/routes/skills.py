"""Skills web-surface endpoints — PHASE 1 read foundation.

Operational endpoints:
- GET /skills/catalog — union managed catalog + system contracts
- GET /skills/catalog/{skill_id} — single-skill detail
- GET /agents/{agent_id}/skills/effective — effective + hidden skills
- POST /skills/recover — operator recovery for corrupted canonical packages

Per the THR-092 v3 endpoint spec (engineering_manager-2026-07-13-skills-web-v1-endpoint-spec-v3.md).
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Optional

import yaml
from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, field_validator

from runtime.config import settings
from runtime.daemon.auth import require_token
from runtime.daemon.org_state import OrgState
from runtime.daemon.routes._org_dep import OrgDep
from runtime.daemon.state import DaemonState
from runtime.orchestrator.org_config import OrgConfigError
from runtime.skills.exposure import catalog_gate, resolve_exposed_skills
from runtime.skills.models import PolicyClass, SkillEntry, SkillStatus
from runtime.skills.registry import SkillRegistry
from runtime.skills.resolver import EligibilityResolver
from runtime.skills.system_contracts import SYSTEM_CONTRACTS
from runtime.skills.canonical_store import parse_strict_sha256_hash

router = APIRouter(dependencies=[require_token()])


def _recover_audit_event_mandatory(
    db,
    *,
    slug: str,
    agent: str = "operator",
    detail: str,
    reason_codes: list[str] | None = None,
    skill_id: str | None = None,
    version: str | None = None,
    ok: bool = False,
    severity: str = "error",
    source: str = "operator_recovery",
) -> None:
    """Emit a durable audit event for operator recovery paths.

    Persistence is mandatory for refusal and recovery events.  On
    failure this helper raises HTTPException(500) — the caller must
    NOT return or proceed with any destructive action.
    """
    try:
        db.insert_skill_validation_event(  # type: ignore[union-attr]
            skill_id=skill_id or f"hr:{slug}",
            slug=slug,
            agent=agent,
            source=source,
            severity=severity,
            ok=ok,
            version=version,
            findings=[detail],
            reason_codes=reason_codes or [],
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                f"Recovery audit event persistence failed: {exc}. "
                f"No changes were made. Retry recovery after resolving "
                f"the persistence issue."
            ),
        )


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

    THR-055: This legacy store is RETIRED. The org_root/skills/ directory
    no longer feeds any catalog, effective, or detail API. All custom-skill
    discovery, assignment, and lifecycle management now routes through the
    lifecycle ledger (``/skill-lifecycle/*`` routes). This helper returns
    an empty registry; the quarantine migration copies legacy content to
    the immutable ArtifactStore for reference, never for materialization.

    Store directory: <org_root>/skills/ — exists only for legacy quarantine
    migration purposes, NOT for runtime skill resolution.
    """
    return SkillRegistry(skills_root=Path("/nonexistent"))


def _union_catalog(org: OrgState) -> list[tuple[SkillEntry, str]]:
    """Build the union of managed catalog and system contracts ONLY.

    THR-055: User-authored custom skills are excluded — the lifecycle
    ledger is the sole source for custom-skill discovery, assignment, and
    materialization. Legacy quarantined content from org_root/skills/
    must never appear in catalog or effective API results.

    Returns list of (SkillEntry, source_type) where source_type is one of:
    'managed', 'system_contract'.

    Release-wins on slug collision with system contracts.
    """
    release = _release_registry(org)

    union: dict[str, tuple[SkillEntry, str]] = {}

    # Release-shipped managed-catalog skills
    for entry in release.list_all():
        union[entry.id] = (entry, "managed")

    # System contracts — not in the registry
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

    # THR-055: Union is release-managed skills only.
    # User-authored custom skills are resolved via the lifecycle ledger
    # (/skill-lifecycle/* routes), not through this legacy effective API.
    union: dict[str, tuple[SkillEntry, str]] = {}
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

    # Build response: all managed entries + system contracts
    skills = []

    for entry, source_type in union.values():
        skill_id = entry.id
        is_exposed = skill_id in exposed_ids
        is_disabled = entry.status == SkillStatus.DISABLED
        is_denied = skill_id in agent_deny_ids

        # Determine provenance reason (managed skills only — THR-055)
        if is_exposed:
            provenance = "catalog_and_eligible"
        elif is_disabled:
            provenance = "hidden_because:disabled"
        elif is_denied:
            provenance = "hidden_because:denied_by_eligibility"
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


# ── Route: POST /skills (create/import) ── LEGACY CUTOVER ────────────────

@router.post("/skills", status_code=410)
def create_skill(
    slug: str,
    org: OrgDep,
    body: CreateSkillRequest,
) -> dict:
    """LEGACY-CUTOVER: Direct skill creation is retired.

    Use the THR-055 lifecycle routes instead:
    - Agents: POST /api/v1/orgs/{slug}/skill-lifecycle/proposals
    - Humans: POST /api/v1/orgs/{slug}/skill-lifecycle/proposals (then claim/validate/etc.)

    Existing legacy skills are quarantined and available read-only.
    """
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail={
            "code": "legacy_cutover",
            "detail": "Direct skill creation is retired. Use /skill-lifecycle/proposals for agent proposals and the lifecycle routes for management.",
            "migration": "Existing user-authored skills have been quarantined. Use GET /skills to list them read-only.",
        },
    )


# ── Route: POST /skills/{skill_id}/validate ──────────────────────────────

@router.post("/skills/{skill_id}/validate", status_code=410)
def validate_skill(
    slug: str,
    skill_id: str,
    org: OrgDep,
) -> dict:
    """LEGACY-CUTOVER: Direct skill validation is retired.

    Use THR-055 lifecycle routes:
    - POST /api/v1/orgs/{slug}/skill-lifecycle/validate

    Legacy validation read org_root/skills — that filesystem path is no longer
    an authoritative catalog or materialization source.
    """
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail={
            "code": "legacy_cutover",
            "detail": "Direct skill validation is retired. Use /skill-lifecycle/validate for lifecycle-managed validation.",
        },
    )


# ── Route: PATCH /skills/{skill_id} (edit) ── LEGACY CUTOVER ──────────────

@router.patch("/skills/{skill_id}")
def edit_skill(
    slug: str,
    skill_id: str,
    org: OrgDep,
    body: EditSkillRequest,
) -> dict:
    """LEGACY-CUTOVER: Direct skill editing is retired.

    Use THR-055 lifecycle routes for all skill management.
    Existing legacy skills are quarantined and available read-only.
    """
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail={
            "code": "legacy_cutover",
            "detail": "Direct skill editing is retired. Use /skill-lifecycle routes for lifecycle management.",
        },
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


# ── Route: POST /agents/{agent_id}/skills/{skill_id}/assign ── LEGACY CUTOVER

@router.post("/agents/{agent_id}/skills/{skill_id}/assign", status_code=410)
def assign_skill(
    slug: str,
    agent_id: str,
    skill_id: str,
    org: OrgDep,
    body: AssignSkillRequest,
) -> dict:
    """LEGACY-CUTOVER: Direct skill assignment is retired.

    Use THR-055 lifecycle routes:
    - POST /api/v1/orgs/{slug}/skill-lifecycle/assign
      with body: {"skill_id": "hr:my-skill", "agent_name": "dev_agent", "version_id": <id>}
    """
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail={
            "code": "legacy_cutover",
            "detail": "Direct skill assignment is retired. Use /skill-lifecycle/assign with lifecycle-managed versions.",
        },
    )


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


# ── Recovery request model ───────────────────────────────────────────────


class SkillRecoverRequest(BaseModel):
    """Operator-invoked recovery for a named corrupted canonical package.

    All fields required. Identity/path inputs are strictly validated before
    any deletion.
    """
    slug: str
    version: str
    content_hash: str

    @field_validator("slug")
    @classmethod
    def _slug_non_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("slug must not be empty")
        if ".." in v or "/" in v or "\\" in v:
            raise ValueError("slug must not contain path separators or '..'")
        return v

    @field_validator("version")
    @classmethod
    def _version_non_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("version must not be empty")
        if ".." in v or "/" in v or "\\" in v:
            raise ValueError("version must not contain path separators or '..'")
        return v

    @field_validator("content_hash")
    @classmethod
    def _content_hash_valid_sha256(cls, v: str) -> str:
        import re as _re
        v = v.strip()
        if not v:
            raise ValueError("content_hash must not be empty")
        if not _re.match(r"^[a-f0-9]{64}$", v):
            raise ValueError(
                "content_hash must be exactly 64 lowercase hex characters "
                "(raw SHA-256 hex digest)"
            )
        return v


# ── Route: POST /skills/recover ──────────────────────────────────────────

@router.post("/skills/recover", status_code=200)
def skill_recover(
    body: SkillRecoverRequest,
    request: Request,
    org: OrgDep,
):
    """Operator-invoked one-step recovery for a corrupted canonical package.

    Narrowly scoped: validates identity/path inputs, checks ledger provenance,
    revalidates member SHA-256 hashes against the ArtifactStore, then deletes
    ONLY the corrupted canonical package directory. The next materialization
    will rebuild from the ArtifactStore (which must be verified against
    the release source for same-owner deployments).

    Fails closed without valid authority/provenance. Never automatic.
    set-executor only repairs links after byte integrity passes; this endpoint
    is the sole operator surface for recovering corrupted bytes.
    """
    from runtime.config import settings as rt_settings
    from runtime.skills.canonical_store import CanonicalSkillStore
    from runtime.platform.isolation import detect_platform_isolation
    from runtime.skills.lifecycle.service import SkillLifecycleService
    from runtime.orchestrator._paths import OrgPaths
    from runtime.infrastructure.artifact_store import ArtifactStore
    from runtime.skills.lifecycle import stores as lifecycle_stores
    import json as _json
    import shutil

    slug = body.slug
    version = body.version
    content_hash = body.content_hash

    # ── 1. Validate ledger provenance ─────────────────────────────
    service = SkillLifecycleService()
    try:
        pkgs = service.list_catalog(org.db)
    except Exception as exc:
        # Emit durable failure event before refusing
        _recover_audit_event_mandatory(
            org.db, slug=slug, agent="operator",
            ok=False,
            detail=f"Ledger query failure for recover {slug}@{version}: {exc}",
            reason_codes=["ledger_query_failure"],
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to query lifecycle ledger: {exc}",
        )

    pkg_match = None
    for pkg in pkgs:
        if pkg.slug == slug and pkg.version == version:
            pkg_match = pkg
            break

    if pkg_match is None:
        _recover_audit_event_mandatory(
            org.db, slug=slug, agent="operator",
            ok=False,
            detail=(
                f"No PUBLISHED lifecycle package found for "
                f"{slug}@{version}"
            ),
            reason_codes=["package_not_found"],
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"No PUBLISHED lifecycle package found for "
                f"{slug}@{version}. Recovery requires an active "
                f"lifecycle ledger entry."
            ),
        )

    # Validate content_hash matches ledger
    if pkg_match.content_hash != content_hash:
        _recover_audit_event_mandatory(
            org.db, skill_id=pkg_match.skill_id, slug=slug,
            agent="operator", ok=False,
            detail=(
                f"content_hash mismatch: provided {content_hash[:16]}..., "
                f"ledger has {pkg_match.content_hash[:16]}..."
            ),
            reason_codes=["hash_mismatch"],
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"content_hash mismatch: provided {content_hash[:16]}..., "
                f"ledger has {pkg_match.content_hash[:16]}..."
            ),
        )

    # ── 2. Validate artifact store member hashes ──────────────────
    org_root = org.root
    artifact_store = ArtifactStore(OrgPaths(org_root).artifacts_dir)
    manifest = None  # May be populated if content_artifact_key exists

    if pkg_match.content_artifact_key:
        try:
            manifest_bytes = artifact_store.read(pkg_match.content_artifact_key)
        except Exception:
            _recover_audit_event_mandatory(
                org.db, skill_id=pkg_match.skill_id, slug=slug,
                agent="operator", ok=False,
                detail=(
                    f"Manifest artifact not found: "
                    f"{pkg_match.content_artifact_key}"
                ),
                reason_codes=["artifact_not_found"],
            )
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=(
                    f"Manifest artifact not found: "
                    f"{pkg_match.content_artifact_key}"
                ),
            )

        # Verify manifest hash against ledger
        actual_manifest_hash = hashlib.sha256(manifest_bytes).hexdigest()
        if actual_manifest_hash != content_hash:
            _recover_audit_event_mandatory(
                org.db, skill_id=pkg_match.skill_id, slug=slug,
                agent="operator", ok=False,
                detail=(
                    f"Manifest artifact hash mismatch: "
                    f"expected {content_hash[:16]}..., "
                    f"got {actual_manifest_hash[:16]}..."
                ),
                reason_codes=["manifest_hash_mismatch"],
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Manifest artifact hash mismatch: "
                    f"expected {content_hash[:16]}..., "
                    f"got {actual_manifest_hash[:16]}... "
                    f"ArtifactStore may also be corrupted."
                ),
            )

        # Parse manifest and validate member hashes
        try:
            manifest = _json.loads(manifest_bytes.decode("utf-8"))
        except Exception:
            _recover_audit_event_mandatory(
                org.db, skill_id=pkg_match.skill_id, slug=slug,
                agent="operator", ok=False,
                detail="Manifest artifact is not valid JSON",
                reason_codes=["invalid_manifest"],
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Manifest artifact is not valid JSON",
            )

        if isinstance(manifest, dict) and "members" in manifest:
            for member in manifest["members"]:
                member_path = member.get("path", "")
                member_hash = member.get("hash", "")
                member_key = member.get("artifact_key", "")

                try:
                    member_bytes = artifact_store.read(member_key)
                except Exception:
                    _recover_audit_event_mandatory(
                        org.db, skill_id=pkg_match.skill_id, slug=slug,
                        agent="operator", ok=False,
                        detail=(
                            f"Member artifact not found: {member_key}. "
                            f"Recovery requires intact ArtifactStore."
                        ),
                        reason_codes=["member_artifact_not_found"],
                    )
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=(
                            f"Member artifact not found: {member_key}. "
                            f"Recovery requires intact ArtifactStore."
                        ),
                    )

                # Validate strict sha256:<64 lowercase hex> format
                # Uses the single canonical validator from canonical_store
                try:
                    expected_hex = parse_strict_sha256_hash(member_hash)
                except ValueError as exc:
                    _recover_audit_event_mandatory(
                        org.db, skill_id=pkg_match.skill_id, slug=slug,
                        agent="operator", ok=False,
                        detail=(
                            f"Member {member_path} hash invalid: {exc}"
                        ),
                        reason_codes=["malformed_hash"],
                    )
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=(
                            f"Member {member_path} hash invalid: {exc}"
                        ),
                    )

                actual_hex = hashlib.sha256(member_bytes).hexdigest()
                if actual_hex != expected_hex:
                    _recover_audit_event_mandatory(
                        org.db, skill_id=pkg_match.skill_id, slug=slug,
                        agent="operator", ok=False,
                        detail=(
                            f"Member {member_path} hash mismatch: "
                            f"expected {expected_hex[:16]}..., "
                            f"got {actual_hex[:16]}..."
                        ),
                        reason_codes=["member_hash_mismatch"],
                    )
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=(
                            f"Member {member_path} hash mismatch: "
                            f"expected {expected_hex[:16]}..., "
                            f"got {actual_hex[:16]}... "
                            f"ArtifactStore appears corrupted — "
                            f"recovery requires intact artifact bytes."
                        ),
                    )

    # ── 3. Refuse recovery of valid (non-corrupted) targets ───────
    isolation = detect_platform_isolation()
    store = CanonicalSkillStore(settings=rt_settings, isolation=isolation)
    pkg_path = store.canonical_path(slug, version, content_hash)

    if not pkg_path.exists():
        _recover_audit_event_mandatory(
            org.db, skill_id=pkg_match.skill_id, slug=slug,
            agent="operator", ok=False,
            detail=(
                f"Canonical package not found at {pkg_path}. "
                f"Package will be rebuilt on next materialization."
            ),
            reason_codes=["package_not_found_on_disk"],
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"Canonical package not found at {pkg_path}. "
                f"Nothing to recover — package will be rebuilt on "
                f"next materialization."
            ),
        )

    # Verify the target is actually corrupted — refuse valid targets.
    # For manifest-based packages, validate each canonical member's
    # SHA-256 against the manifest's declared hashes. If ALL match,
    # the target is valid and recovery is refused.
    if isinstance(manifest, dict) and "members" in manifest:
        all_members_valid = True
        for member in manifest["members"]:
            member_path_str = member["path"]
            member_file = pkg_path / member_path_str
            if member_file.is_file():
                try:
                    expected_hex = parse_strict_sha256_hash(
                        member["hash"])
                except ValueError:
                    all_members_valid = False
                    break
                actual_hex = hashlib.sha256(
                    member_file.read_bytes()).hexdigest()
                if actual_hex != expected_hex:
                    all_members_valid = False
                    break
            else:
                all_members_valid = False
                break

        if all_members_valid:
            _recover_audit_event_mandatory(
                org.db, skill_id=pkg_match.skill_id, slug=slug,
                agent="operator", ok=False,
                detail=(
                    f"Recovery refused: canonical package {slug}@{version} "
                    f"at {pkg_path} is valid (all member hashes match ledger)."
                ),
                reason_codes=["valid_target_refused"],
            )
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Canonical package {slug}@{version} at {pkg_path} "
                    f"is valid (all member hashes match ledger). "
                    f"No recovery needed. Refusing to delete a valid "
                    f"target."
                ),
            )

    # ── 3. Emit durable recovery event BEFORE deletion ────────────
    # Persistence must succeed before any destructive action.
    # If the event write fails, the canonical package is left
    # untouched (fail-closed) — the operator can retry.
    try:
        org.db.insert_skill_validation_event(
            skill_id=pkg_match.skill_id,
            slug=slug,
            agent="operator",
            source="operator_recovery",
            severity="info",
            ok=True,
            version=version,
            findings=[
                f"Operator recovery: deleting corrupted canonical package "
                f"{slug}@{version} (hash={content_hash[:16]}...) at {pkg_path}. "
                f"Next materialization will rebuild from ArtifactStore."
            ],
            reason_codes=["operator_recovery"],
        )
    except Exception as exc:
        # Event persistence failed — fail closed.
        # Do NOT delete the package; canonical bytes stay unchanged.
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                f"Recovery event persistence failed: {exc}. "
                f"The canonical package was NOT deleted — it remains "
                f"intact on disk. Retry recovery after resolving the "
                f"persistence issue."
            ),
        )

    # ── 4. Delete the corrupted package (only after event persisted) ──
    try:
        # Make writable first (hardened packages are readonly)
        from runtime.skills.canonical_store import _make_writable_for_removal
        _make_writable_for_removal(pkg_path)
        shutil.rmtree(pkg_path)
    except Exception as exc:
        _recover_audit_event_mandatory(
            org.db, skill_id=pkg_match.skill_id, slug=slug,
            agent="operator", ok=False,
            detail=(
                f"Failed to delete corrupted package {slug}@{version} "
                f"at {pkg_path} (successful recovery event was "
                f"already persisted): {exc}"
            ),
            reason_codes=["deletion_failed"],
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete corrupted package: {exc}",
        )

    return {
        "ok": True,
        "action": "recovered",
        "slug": slug,
        "version": version,
        "content_hash": content_hash,
        "canonical_path": str(pkg_path),
        "message": (
            f"Corrupted canonical package {slug}@{version} deleted. "
            f"Restart daemon or trigger next launch to rebuild from "
            f"the ArtifactStore (which must be verified against the "
            f"release source for same-owner deployments)."
        ),
    }
