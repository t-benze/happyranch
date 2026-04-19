"""File-backed knowledge-base store.

Flat markdown entries under ``<runtime>/kb/``; filename == slug.
YAML frontmatter + body. Daemon owns identity stamping.
"""
from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import yaml


SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
MAX_BODY_BYTES = 32 * 1024
VALID_TYPES = {"reference", "precedent"}


class InvalidSlug(ValueError):
    pass


class InvalidEntry(ValueError):
    """Raised for any structural validation failure other than slug.

    ``code`` encodes which §6 table row triggered: ``invalid_type``,
    ``missing_frontmatter``, ``entry_too_large``, ``invalid_supersedes``.
    """

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


class SlugExists(ValueError):
    def __init__(self, slug: str, existing_title: str) -> None:
        self.slug = slug
        self.existing_title = existing_title
        super().__init__(f"slug_exists: {slug}")


class NotFound(LookupError):
    pass


@dataclass
class KBEntry:
    slug: str
    title: str
    type: str
    topic: str
    body: str
    tags: list[str] = field(default_factory=list)
    source_task: Optional[str] = None
    supersedes: Optional[str] = None
    authored_by: Optional[str] = None
    authored_at: Optional[str] = None
    updated_by: Optional[str] = None
    updated_at: Optional[str] = None
    escalation_reason: Optional[str] = None
    founder_decision: Optional[str] = None
    founder_rationale: Optional[str] = None


class KBStore:
    def __init__(self, root: Path) -> None:
        self._root = root
        self._root.mkdir(parents=True, exist_ok=True)

    @property
    def root(self) -> Path:
        return self._root

    def path_for(self, slug: str) -> Path:
        return self._root / f"{slug}.md"

    def validate_slug(self, slug: str) -> None:
        if not SLUG_RE.match(slug):
            raise InvalidSlug(f"invalid_slug: {slug!r}")

    def _validate_entry_structure(self, entry: KBEntry) -> None:
        for required in ("title", "type", "topic"):
            val = getattr(entry, required, None)
            if not val or not isinstance(val, str):
                raise InvalidEntry("missing_frontmatter", f"missing field: {required}")
        if entry.type not in VALID_TYPES:
            raise InvalidEntry("invalid_type", f"type must be one of {VALID_TYPES}")
        if len(entry.body.encode("utf-8")) > MAX_BODY_BYTES:
            raise InvalidEntry("entry_too_large", f"body exceeds {MAX_BODY_BYTES}B")
        if entry.supersedes is not None:
            if not SLUG_RE.match(entry.supersedes) or not self.path_for(entry.supersedes).exists():
                raise InvalidEntry("invalid_supersedes", f"unknown slug {entry.supersedes!r}")

    def write_entry(self, entry: KBEntry, agent: str) -> KBEntry:
        self.validate_slug(entry.slug)
        self._validate_entry_structure(entry)
        target = self.path_for(entry.slug)
        if target.exists():
            existing = self.read_entry(entry.slug)
            raise SlugExists(entry.slug, existing.title)
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        stamped = KBEntry(**{**entry.__dict__})
        stamped.authored_by = agent
        stamped.authored_at = now
        stamped.updated_by = agent
        stamped.updated_at = now
        self._atomic_write(target, self._serialize(stamped))
        return stamped

    def read_entry(self, slug: str) -> KBEntry:
        path = self.path_for(slug)
        if not path.exists():
            raise NotFound(slug)
        return self._parse(path.read_text())

    def _serialize(self, entry: KBEntry) -> str:
        fm: dict = {
            "slug": entry.slug,
            "title": entry.title,
            "type": entry.type,
            "topic": entry.topic,
        }
        if entry.tags:
            fm["tags"] = entry.tags
        for key in (
            "authored_by",
            "authored_at",
            "updated_by",
            "updated_at",
            "source_task",
            "supersedes",
            "escalation_reason",
            "founder_decision",
            "founder_rationale",
        ):
            val = getattr(entry, key)
            if val is not None:
                fm[key] = val
        fm_text = yaml.safe_dump(fm, sort_keys=False).strip()
        body = entry.body if entry.body.endswith("\n") else entry.body + "\n"
        return f"---\n{fm_text}\n---\n\n{body}"

    def _parse(self, text: str) -> KBEntry:
        if not text.startswith("---"):
            raise InvalidEntry("missing_frontmatter", "no leading frontmatter")
        parts = text.split("---", 2)
        if len(parts) < 3:
            raise InvalidEntry("missing_frontmatter", "malformed frontmatter")
        fm = yaml.safe_load(parts[1]) or {}
        body = parts[2].lstrip("\n")
        return KBEntry(
            slug=fm.get("slug", ""),
            title=fm.get("title", ""),
            type=fm.get("type", ""),
            topic=fm.get("topic", ""),
            tags=list(fm.get("tags") or []),
            source_task=fm.get("source_task"),
            supersedes=fm.get("supersedes"),
            authored_by=fm.get("authored_by"),
            authored_at=fm.get("authored_at"),
            updated_by=fm.get("updated_by"),
            updated_at=fm.get("updated_at"),
            escalation_reason=fm.get("escalation_reason"),
            founder_decision=fm.get("founder_decision"),
            founder_rationale=fm.get("founder_rationale"),
            body=body,
        )

    def _atomic_write(self, target: Path, content: str) -> None:
        fd, tmp_path = tempfile.mkstemp(
            prefix=target.stem + ".", suffix=".tmp", dir=str(target.parent)
        )
        try:
            with os.fdopen(fd, "w") as f:
                f.write(content)
            os.replace(tmp_path, target)
        except Exception:
            try:
                os.unlink(tmp_path)
            except FileNotFoundError:
                pass
            raise
