"""Skill registry loader.

Reads skill packages from runtime/skills/<slug>/{skill.yaml, SKILL.md, references/, assets/}
and builds an in-memory catalog of SkillEntry objects.

System-contract skills (policy_class=system_contract) are skipped — they are
outside the toggleable catalog.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import yaml

from runtime.skills.models import (
    ApprovalState,
    PolicyClass,
    SkillEntry,
    SkillStatus,
)


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


def _parse_approval_state(raw: str) -> ApprovalState:
    """Parse an approval_state string to enum."""
    try:
        return ApprovalState(raw.lower())
    except ValueError:
        return ApprovalState.DRAFT


def _parse_status(raw: str) -> SkillStatus:
    """Parse a status string to enum."""
    try:
        return SkillStatus(raw.lower())
    except ValueError:
        return SkillStatus.ENABLED


class SkillRegistry:
    """In-memory catalog of managed skills loaded from a directory tree.

    Directory structure expected:
        <skills_root>/<slug>/skill.yaml    (required)
        <skills_root>/<slug>/SKILL.md      (required)
        <skills_root>/<slug>/references/   (optional)
        <skills_root>/<slug>/assets/       (optional)

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
        skill_md = slug_dir / "SKILL.md"

        return SkillEntry(
            id=data.get("id", f"hr:{slug_dir.name}"),
            slug=data.get("slug", slug_dir.name),
            name=data.get("name", slug_dir.name),
            version=str(data.get("version", "0.0.0")),
            description=data.get("description", ""),
            when_to_use=data.get("when_to_use", ""),
            owner=data.get("owner", ""),
            source=data.get("source", f"runtime/skills/{slug_dir.name}"),
            policy_class=policy_class,
            approval_state=_parse_approval_state(data.get("approval_state", "draft")),
            approved_by=data.get("approved_by") or None,
            approved_at=_parse_optional_datetime(data.get("approved_at")),
            status=_parse_status(data.get("status", "enabled")),
            compatibility=data.get("compatibility"),
            tags=data.get("tags"),
            supersedes=data.get("supersedes"),
            reviewed_at=_parse_optional_datetime(data.get("reviewed_at")),
            review_notes=data.get("review_notes"),
            approved_version=data.get("approved_version") or None,
            skill_md_path=skill_md if skill_md.is_file() else None,
        )
