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
import logging
from pathlib import Path
from typing import Optional

import yaml
from fastapi import APIRouter, Body as RequestBody, HTTPException, Query, Request, status
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
_logger = logging.getLogger(__name__)

# ── Separate agent-only router (no global bearer requirement) ───────────
# Used for POST /skills/agent — the agent-session binding B1 create path.
# This router does NOT use require_token() because the route rejects
# bearer tokens and uses SessionTracker identity instead.
agent_skills_router = APIRouter()


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
    B2 custom-skill records. This helper returns
    an empty registry; the quarantine migration copies legacy content to
    the immutable ArtifactStore for reference, never for materialization.

    Store directory: <org_root>/skills/ — exists only for legacy quarantine
    migration purposes, NOT for runtime skill resolution.
    """
    return SkillRegistry(skills_root=Path("/nonexistent"))


def _union_catalog(org: OrgState) -> list[tuple[SkillEntry, str]]:
    """Build the union of managed catalog and system contracts ONLY.

    THR-055: User-authored custom skills are excluded — the B2 custom-skill is the sole source for custom-skill discovery, assignment, and
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
    # and are not exposed through this legacy effective API.
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

    # THR-055 B2: custom skills are a separate visibility projection, never
    # lifecycle assignments.  Do not infer materialization from an allow rule.
    try:
        from runtime.skills.custom import service as custom_service
        from runtime.skills.eligibility import (
            EligibilityRecipient, EligibilityRule, SkillEligibilityState,
            resolve_custom_skill_eligibility,
        )
        conn = getattr(org.db, "_conn", org.db)
        rows = conn.execute("""SELECT s.*, v.id AS version_id, v.content_hash,
            v.validation_state, m.created_at AS materialized_at,
            m.session_id AS materialized_session_id
            FROM custom_skills s JOIN custom_skill_versions v ON v.id=s.current_version_id
            LEFT JOIN custom_skill_materializations m ON m.id = (
                SELECT latest.id FROM custom_skill_materializations latest
                WHERE latest.skill_id=s.id AND latest.agent_name=?
                  AND latest.version_id=v.id AND latest.success=1
                ORDER BY latest.created_at DESC, latest.id DESC LIMIT 1
            ) WHERE s.org_slug=?""", (agent_id, slug)).fetchall()
        recipient = EligibilityRecipient(agent_id, (team,))
        for row in rows:
            rules = [EligibilityRule(**dict(rule)) for rule in custom_service.current_rules(org.db, row["id"])]
            result = resolve_custom_skill_eligibility(
                SkillEligibilityState(bool(row["retired_at"]), row["validation_state"]), rules, recipient)
            materialized = row["materialized_at"] is not None
            skills.append({
                "skill_id": row["id"], "name": row["name"], "type": "custom",
                "source": "custom_skill", "status": "retired" if row["retired_at"] else "active",
                "version": str(row["version_id"]), "summary": row["description"],
                "visible": result.visible, "hidden": not result.visible,
                "hidden_reason": result.reason, "current_version": row["version_id"],
                "current_hash": row["content_hash"], "validation_state": row["validation_state"],
                "winning_rule": (result.winning_rule.__dict__ if result.winning_rule else None),
                "materialized_at": row["materialized_at"],
                "materialized_session_id": row["materialized_session_id"],
                "materialization_state": (
                    "materialized" if materialized else
                    "visible_next_session" if result.visible else "not_visible"
                ),
            })
    except Exception:
        # Custom-skill read failure must not compromise the mature managed catalog.
        _logger.exception("custom-skill effective-skills projection failed for agent=%s", agent_id)

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

    Use POST /api/v1/orgs/{slug}/custom-skills for founder authoring.

    Existing legacy skills are quarantined and available read-only.
    """
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail={
            "code": "legacy_cutover",
            "detail": "Direct skill creation is retired. Use the B2 custom-skills routes.",
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

    Use the B2 custom-skills routes.

    Legacy validation read org_root/skills — that filesystem path is no longer
    an authoritative catalog or materialization source.
    """
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail={
            "code": "legacy_cutover",
            "detail": "Direct skill validation is retired. Use the B2 custom-skills routes.",
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

    Use the B2 custom-skills routes for skill management.
    Existing legacy skills are quarantined and available read-only.
    """
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail={
            "code": "legacy_cutover",
            "detail": "Direct skill editing is retired. Use the B2 custom-skills routes.",
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

    Use B2 custom-skill eligibility management.
    """
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail={
            "code": "legacy_cutover",
            "detail": "Direct skill assignment is retired. Use B2 custom-skill eligibility management.",
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



@agent_skills_router.post("/skills/agent", status_code=201)
def create_skill_agent(
    org: OrgDep,
    request: Request,
    body_raw: dict = RequestBody(..., description="B2 custom-skill metadata and content"),
    session_id: str = Query(..., min_length=1),
) -> dict:
    """Create a B2 custom skill from verified SessionTracker provenance."""
    from runtime.daemon.routes.custom_skills import create_agent_custom_skill

    return create_agent_custom_skill(org.slug, session_id, org, request, body_raw)
