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


class LearningSlugExists(ValueError):
    def __init__(self, slug: str) -> None:
        self.slug = slug
        super().__init__(f"slug_exists: {slug}")


class LearningNotFound(LookupError):
    pass


class PromotedLocked(ValueError):
    def __init__(self, id: str, kb_slug: str) -> None:
        self.id = id
        self.kb_slug = kb_slug
        super().__init__(f"promoted_locked: {id} -> {kb_slug}")


@dataclass
class LearningSummary:
    id: str
    slug: str
    title: str
    topic: str
    tags: list[str]
    promoted_to: Optional[str]
    updated_at: Optional[str]


@dataclass
class LearningSearchHit:
    id: str
    slug: str
    title: str
    snippet: str
    score: int


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

    def _validate_entry_structure(self, entry: LearningEntry) -> None:
        # Required fields (excluding id, which is server-allocated on add)
        for required in ("slug", "title", "topic"):
            val = getattr(entry, required, None)
            if not val or not isinstance(val, str):
                raise InvalidLearningEntry(
                    "missing_frontmatter", f"missing field: {required}"
                )
        # Slug shape
        if not SLUG_RE.match(entry.slug):
            raise InvalidLearningEntry(
                "invalid_slug", f"slug {entry.slug!r} fails regex"
            )
        # Body size
        if len(entry.body.encode("utf-8")) > MAX_BODY_BYTES:
            raise InvalidLearningEntry(
                "entry_too_large", f"body exceeds {MAX_BODY_BYTES}B"
            )

    def next_id(self) -> str:
        max_n = 0
        for path in self._root.glob("LRN-*.md"):
            m = self._ID_FILE_RE.match(path.name)
            if m:
                n = int(m.group(1))
                if n > max_n:
                    max_n = n
        return f"LRN-{max_n + 1:03d}"

    def path_for(self, id: str, slug: str) -> Path:
        return self._root / f"{id}-{slug}.md"

    def _find_by_id(self, id: str) -> Optional[Path]:
        for path in self._root.glob(f"{id}-*.md"):
            return path
        return None

    def _find_by_slug(self, slug: str) -> Optional[Path]:
        for path in self._root.glob(f"LRN-*-{slug}.md"):
            return path
        return None

    def _validate_cross_refs(self, entry: LearningEntry) -> None:
        for ref in entry.related_to:
            if ref == entry.id:
                raise InvalidLearningEntry(
                    "self_reference", f"related_to cannot reference self: {ref!r}",
                )
            if not ID_RE.match(ref) or self._find_by_id(ref) is None:
                raise InvalidLearningEntry(
                    "unknown_related_id", f"related_to references unknown id: {ref!r}",
                )
        if entry.supersedes is not None:
            if entry.supersedes == entry.id:
                raise InvalidLearningEntry(
                    "self_reference", f"supersedes cannot reference self: {entry.supersedes!r}",
                )
            if not ID_RE.match(entry.supersedes) or self._find_by_id(entry.supersedes) is None:
                raise InvalidLearningEntry(
                    "unknown_supersedes", f"supersedes references unknown id: {entry.supersedes!r}",
                )

    def write_entry(self, entry: LearningEntry, agent: str) -> LearningEntry:
        self.validate_id(entry.id)
        self._validate_entry_structure(entry)
        self._validate_cross_refs(entry)
        if self._find_by_id(entry.id) is not None:
            raise LearningIdExists(entry.id)
        if self._find_by_slug(entry.slug) is not None:
            raise LearningSlugExists(entry.slug)
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        stamped = LearningEntry(**{**entry.__dict__})
        stamped.authored_by = agent
        stamped.authored_at = now
        stamped.updated_by = agent
        stamped.updated_at = now
        target = self.path_for(stamped.id, stamped.slug)
        self._atomic_write(target, self._serialize(stamped))
        return stamped

    def update_entry(
        self, id: str, entry: LearningEntry, agent: str,
    ) -> LearningEntry:
        self.validate_id(id)
        existing_path = self._find_by_id(id)
        if existing_path is None:
            raise LearningNotFound(id)
        existing = self._parse(existing_path.read_text())
        if existing.promoted_to is not None:
            raise PromotedLocked(id, existing.promoted_to)
        # Force id consistency (positional arg wins)
        entry.id = id
        entry.promoted_to = existing.promoted_to  # always None at this point
        self._validate_entry_structure(entry)
        self._validate_cross_refs(entry)
        # Reject slug collision with a DIFFERENT entry
        if entry.slug != existing.slug:
            if self._find_by_slug(entry.slug) is not None:
                raise LearningSlugExists(entry.slug)
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        stamped = LearningEntry(**{**entry.__dict__})
        stamped.authored_by = existing.authored_by
        stamped.authored_at = existing.authored_at
        stamped.updated_by = agent
        stamped.updated_at = now
        target = self.path_for(stamped.id, stamped.slug)
        self._atomic_write(target, self._serialize(stamped))
        # Remove the old file if slug changed
        if existing_path != target and existing_path.exists():
            existing_path.unlink()
        return stamped

    def read_entry(self, id_or_slug: str) -> LearningEntry:
        path = (
            self._find_by_id(id_or_slug) if ID_RE.match(id_or_slug)
            else self._find_by_slug(id_or_slug)
        )
        if path is None:
            raise LearningNotFound(id_or_slug)
        return self._parse(path.read_text())

    def _serialize(self, entry: LearningEntry) -> str:
        fm: dict = {
            "id": entry.id,
            "slug": entry.slug,
            "title": entry.title,
            "topic": entry.topic,
        }
        if entry.tags:
            fm["tags"] = entry.tags
        for key in (
            "authored_by", "authored_at", "updated_by", "updated_at",
            "source_task", "supersedes", "promoted_to",
        ):
            val = getattr(entry, key)
            if val is not None:
                fm[key] = val
        if entry.related_to:
            fm["related_to"] = entry.related_to
        fm_text = yaml.safe_dump(fm, sort_keys=False).strip()
        body = entry.body if entry.body.endswith("\n") else entry.body + "\n"
        return f"---\n{fm_text}\n---\n\n{body}"

    def _parse(self, text: str) -> LearningEntry:
        if not text.startswith("---"):
            raise InvalidLearningEntry("missing_frontmatter", "no leading frontmatter")
        parts = text.split("---", 2)
        if len(parts) < 3:
            raise InvalidLearningEntry("missing_frontmatter", "malformed frontmatter")
        fm = yaml.safe_load(parts[1]) or {}
        body = parts[2].lstrip("\n")
        return LearningEntry(
            id=fm.get("id", ""),
            slug=fm.get("slug", ""),
            title=fm.get("title", ""),
            topic=fm.get("topic", ""),
            tags=list(fm.get("tags") or []),
            source_task=fm.get("source_task"),
            related_to=list(fm.get("related_to") or []),
            supersedes=fm.get("supersedes"),
            promoted_to=fm.get("promoted_to"),
            authored_by=fm.get("authored_by"),
            authored_at=fm.get("authored_at"),
            updated_by=fm.get("updated_by"),
            updated_at=fm.get("updated_at"),
            body=body,
        )

    def list_entries(
        self,
        topic: Optional[str] = None,
        tag: Optional[str] = None,
        promoted: Optional[bool] = None,
    ) -> list[LearningSummary]:
        out: list[LearningSummary] = []
        for path in sorted(self._root.glob("LRN-*.md")):
            try:
                entry = self._parse(path.read_text())
            except InvalidLearningEntry:
                continue
            if topic is not None and entry.topic != topic:
                continue
            if tag is not None and tag not in entry.tags:
                continue
            if promoted is True and entry.promoted_to is None:
                continue
            if promoted is False and entry.promoted_to is not None:
                continue
            out.append(LearningSummary(
                id=entry.id,
                slug=entry.slug,
                title=entry.title,
                topic=entry.topic,
                tags=entry.tags,
                promoted_to=entry.promoted_to,
                updated_at=entry.updated_at,
            ))
        return out

    def search(
        self, query: str, limit: int = 20, include_promoted: bool = False,
    ) -> list[LearningSearchHit]:
        q = query.lower().strip()
        if not q:
            return []
        hits: list[LearningSearchHit] = []
        for path in sorted(self._root.glob("LRN-*.md")):
            try:
                entry = self._parse(path.read_text())
            except InvalidLearningEntry:
                continue
            if entry.promoted_to is not None and not include_promoted:
                continue
            score = 0
            snippet = ""
            if q in entry.title.lower():
                score = 10
                snippet = entry.title
            elif q in entry.body.lower():
                score = 5
                snippet = self._snippet(entry.body, q)
            elif any(q in t.lower() for t in entry.tags) or q in entry.topic.lower():
                score = 2
                snippet = f"topic={entry.topic} tags={entry.tags}"
            if score > 0:
                hits.append(LearningSearchHit(
                    id=entry.id, slug=entry.slug, title=entry.title,
                    snippet=snippet, score=score,
                ))
        hits.sort(key=lambda h: h.score, reverse=True)
        return hits[:limit]

    @staticmethod
    def _snippet(body: str, q: str, width: int = 80) -> str:
        idx = body.lower().find(q)
        if idx < 0:
            return body[:width]
        start = max(0, idx - width // 2)
        end = min(len(body), idx + width // 2)
        return body[start:end].replace("\n", " ")

    def promote(self, id: str, kb_slug: str, agent: str) -> LearningEntry:
        self.validate_id(id)
        if not kb_slug:
            raise InvalidLearningEntry("kb_slug_missing", "kb_slug required")
        existing_path = self._find_by_id(id)
        if existing_path is None:
            raise LearningNotFound(id)
        existing = self._parse(existing_path.read_text())
        if existing.promoted_to is not None and existing.promoted_to != kb_slug:
            raise PromotedLocked(id, existing.promoted_to)
        if existing.promoted_to == kb_slug:
            return existing  # truly idempotent: no file write, no timestamp churn
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        stub_body = (
            f"See KB precedent: `{kb_slug}`.\n\n"
            f"_Promoted from local learning {id} on {now}._\n"
        )
        stamped = LearningEntry(**{**existing.__dict__})
        stamped.promoted_to = kb_slug
        stamped.body = stub_body
        stamped.updated_by = agent
        stamped.updated_at = now
        target = self.path_for(stamped.id, stamped.slug)
        self._atomic_write(target, self._serialize(stamped))
        return stamped

    def regenerate_index(self) -> None:
        summaries = self.list_entries()
        groups: dict[str, list[LearningSummary]] = {}
        for s in summaries:
            groups.setdefault(s.topic, []).append(s)
        for topic in groups:
            groups[topic].sort(key=lambda s: s.id, reverse=True)  # newest first
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        total = sum(len(v) for v in groups.values())
        lines = [
            f"# Learnings Index",
            "",
            f"_Generated {now} — {total} entries_",
            "",
        ]
        for topic in sorted(groups.keys()):
            count = len(groups[topic])
            lines.append(f"## {topic} ({count})")
            lines.append("")
            for s in groups[topic]:
                tags_part = f"  [tags: {', '.join(s.tags)}]" if s.tags else ""
                promo_part = f" ↗ promoted: {s.promoted_to}" if s.promoted_to else ""
                lines.append(f"- `{s.id}` — {s.title}{tags_part}{promo_part}")
            lines.append("")
        index_path = self._root / "_index.md"
        self._atomic_write(index_path, "\n".join(lines).rstrip() + "\n")

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
