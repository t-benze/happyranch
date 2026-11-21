"""Skill registry loader.

Reads catalog manifests from runtime/skills/<slug>/skill.yaml and builds an
in-memory catalog of SkillEntry objects. The three release catalog packages
use instructions and supporting assets from runtime/skills/bundled/<slug>/.
Generic packages keep their content beside the manifest.

System-contract skills (policy_class=system_contract) are skipped — they are
outside the toggleable catalog.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import yaml

from runtime.skills.models import (
    PolicyClass,
    SkillEntry,
    SkillStatus,
)


def resolve_skill_content_dir(skills_root: Path, slug: str, source: str) -> Path:
    """Resolve the three declared bundled catalog sources within this root.

    Source metadata is not an arbitrary filesystem path. Other packages,
    including fixtures and user-authored entries, retain sibling content.
    A missing bundled package never falls back to the manifest directory.
    """
    if (slug in ("manage-agent", "manage-repo", "reflection")
            and source == f"runtime/skills/bundled/{slug}"):
        return skills_root / "bundled" / slug
    return skills_root / slug


def _parse_optional_datetime(value: Any) -> Optional[datetime]:
    """Parse an ISO-8601 timestamp to a datetime, or return None.

    PyYAML may auto-parse YAML timestamps into datetime objects, so we handle
    both raw strings and already-parsed datetimes.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return None
    return None


def _parse_policy_class(raw: str) -> PolicyClass:
    """Parse a policy_class string to enum, case-insensitive."""
    try:
        return PolicyClass(raw.lower())
    except ValueError:
        # Default to standard_operational for unknown values
        return PolicyClass.STANDARD_OPERATIONAL


def _parse_status(raw: str) -> SkillStatus:
    """Parse a status string to enum."""
    try:
        return SkillStatus(raw.lower())
    except ValueError:
        return SkillStatus.ENABLED


class SkillRegistry:
    """In-memory catalog of managed skills loaded from a directory tree.

    Generic directory structure expected:
        <skills_root>/<slug>/skill.yaml    (required)
        <skills_root>/<slug>/SKILL.md      (required)
        <skills_root>/<slug>/references/   (optional)
        <skills_root>/<slug>/assets/       (optional)

    Release manage-agent/manage-repo/reflection manifests instead declare
    runtime/skills/bundled/<slug> and use <skills_root>/bundled/<slug>/ content.

    System-contract skills are loaded but excluded from the catalog
    (they are not toggleable).
    """

    def __init__(self, skills_root: Path | str):
        self._skills_root = Path(skills_root)
        self._entries: dict[str, SkillEntry] = {}
        self._load()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get(self, skill_id: str) -> Optional[SkillEntry]:
        """Return the SkillEntry for skill_id, or None if not in the catalog."""
        return self._entries.get(skill_id)

    def list_all(self) -> list[SkillEntry]:
        """Return all loaded, non-system-contract entries."""
        return list(self._entries.values())

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _load(self) -> None:
        """Scan the skills root and load every valid skill package."""
        if not self._skills_root.is_dir():
            return

        for slug_dir in sorted(self._skills_root.iterdir()):
            if not slug_dir.is_dir():
                continue
            yaml_path = slug_dir / "skill.yaml"
            if not yaml_path.is_file():
                continue
            entry = self._load_entry(yaml_path, slug_dir)
            if entry is not None:
                self._entries[entry.id] = entry

    def _load_entry(self, yaml_path: Path, slug_dir: Path) -> Optional[SkillEntry]:
        """Parse a single skill.yaml and build a SkillEntry."""
        try:
            raw = yaml_path.read_text(encoding="utf-8")
        except Exception:
            return None

        try:
            data = yaml.safe_load(raw)
        except yaml.YAMLError:
            return None

        if not isinstance(data, dict):
            return None

        # --- Policy class check: skip system_contract ---
        policy_class = _parse_policy_class(data.get("policy_class", ""))
        if policy_class == PolicyClass.SYSTEM_CONTRACT:
            return None  # not toggleable

        # --- Build entry ---
        source = data.get("source", f"runtime/skills/{slug_dir.name}")
        skill_md = resolve_skill_content_dir(self._skills_root, slug_dir.name, source) / "SKILL.md"

        return SkillEntry(
            id=data.get("id", f"hr:{slug_dir.name}"),
            slug=data.get("slug", slug_dir.name),
            name=data.get("name", slug_dir.name),
            version=str(data.get("version", "0.0.0")),
            description=data.get("description", ""),
            when_to_use=data.get("when_to_use", ""),
            owner=data.get("owner", ""),
            source=source,
            policy_class=policy_class,
            status=_parse_status(data.get("status", "enabled")),
            compatibility=data.get("compatibility"),
            tags=data.get("tags"),
            supersedes=data.get("supersedes"),
            reviewed_at=_parse_optional_datetime(data.get("reviewed_at")),
            review_notes=data.get("review_notes"),
            skill_md_path=skill_md if skill_md.is_file() else None,
        )
