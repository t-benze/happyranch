"""File-backed per-agent learnings store.

Per-entry markdown under ``<workspace>/learnings/``; filename is
``<id>-<slug>.md``. Mirrors the shape of ``kb_store.py`` one level down.
"""
from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Optional

import yaml


ID_RE = re.compile(r"^LRN-\d{3,}$")
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
MAX_BODY_BYTES = 32 * 1024


class InvalidLearningId(ValueError):
    pass


class InvalidLearningEntry(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


class LearningIdExists(ValueError):
    def __init__(self, id: str) -> None:
        self.id = id
        super().__init__(f"id_exists: {id}")


class LearningNotFound(LookupError):
    pass


class PromotedLocked(ValueError):
    def __init__(self, id: str, kb_slug: str) -> None:
        self.id = id
        self.kb_slug = kb_slug
        super().__init__(f"promoted_locked: {id} -> {kb_slug}")


@dataclass
class LearningEntry:
    id: str
    slug: str
    title: str
    topic: str
    body: str
    tags: list[str] = field(default_factory=list)
    source_task: Optional[str] = None
    related_to: list[str] = field(default_factory=list)
    supersedes: Optional[str] = None
    promoted_to: Optional[str] = None
    authored_by: Optional[str] = None
    authored_at: Optional[str] = None
    updated_by: Optional[str] = None
    updated_at: Optional[str] = None


class LearningsStore:
    def __init__(self, root: Path) -> None:
        self._root = root
        self._root.mkdir(parents=True, exist_ok=True)

    @property
    def root(self) -> Path:
        return self._root

    @staticmethod
    def validate_id(id: str) -> None:
        if not ID_RE.match(id):
            raise InvalidLearningId(f"invalid_id: {id!r}")

    @staticmethod
    def validate_slug(slug: str) -> None:
        if not SLUG_RE.match(slug):
            raise InvalidLearningEntry("invalid_slug", f"slug {slug!r} fails regex")

    _ID_FILE_RE = re.compile(r"^LRN-(\d{3,})-")

    def next_id(self) -> str:
        max_n = 0
        for path in self._root.glob("LRN-*.md"):
            m = self._ID_FILE_RE.match(path.name)
            if m:
                n = int(m.group(1))
                if n > max_n:
                    max_n = n
        return f"LRN-{max_n + 1:03d}"
