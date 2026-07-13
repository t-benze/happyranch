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


# THR-032 Phase R (thorough rename): ids move LRN-NNN -> MEM-NNN. New items
# allocate the canonical MEM- prefix; the legacy LRN- prefix stays a permanent,
# never-removed resolution alias (§3.3, §7.2(b)) so any old LRN- reference —
# un-rewritten body ref, historical artifact, KB source_task, founder typing a
# remembered id — resolves forever. ID_RE accepts both; the two prefixes are
# aliases of the same opaque number.
CANONICAL_ID_PREFIX = "MEM"
LEGACY_ID_PREFIX = "LRN"
_ID_PREFIXES = (CANONICAL_ID_PREFIX, LEGACY_ID_PREFIX)
ID_RE = re.compile(r"^(?:LRN|MEM)-\d{3,}$")
_ID_PARTS_RE = re.compile(r"^(LRN|MEM)-(\d{3,})$")
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
MAX_BODY_BYTES = 32 * 1024


def _id_variants(id: str) -> list[str]:
    """Return the id and its prefix-swapped alias (the permanent LRN-/MEM-
    resolution shim). For a non-id string, return it unchanged. The id as
    given comes first so a same-prefix file is preferred when both exist."""
    m = _ID_PARTS_RE.match(id)
    if not m:
        return [id]
    prefix, num = m.group(1), m.group(2)
    other = CANONICAL_ID_PREFIX if prefix == LEGACY_ID_PREFIX else LEGACY_ID_PREFIX
    return [id, f"{other}-{num}"]

# THR-032 Phase 1 (harness-agnostic memory layer): additive frontmatter enums.
PROVENANCE_VALUES = {"experiential", "reflective", "directive"}
SCOPE_VALUES = {"agent", "team", "org"}
LIFECYCLE_VALUES = {"valid", "superseded", "evicted"}


def _clamp_salience(value: object) -> int:
    """Clamp salience into [0, 100]; both reads and writes normalize."""
    return max(0, min(100, int(value)))


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
    lifecycle: str
    provenance: str
    salience: int


@dataclass
class LearningSearchHit:
    id: str
    slug: str
    title: str
    snippet: str
    score: int
    # THR-032 P4a: additive fields for improved search ranking
    source: str = "memory"
    lifecycle: str = "valid"
    provenance: str = "experiential"
    salience: int = 50
    updated_at: str | None = None


@dataclass
class MemoryCompactionPolicy:
    """Policy knobs for memory compaction (THR-032 P3b)."""
    salience_floor: int = 10
    stale_days: int = 45
    superseded_grace_days: int = 7
    max_evictions_per_run: int = 25


@dataclass
class MemoryCompactionCandidate:
    id: str
    title: str
    reason: str
    current_lifecycle: str


@dataclass
class MemoryCompactionResult:
    dry_run: bool
    candidates: list[MemoryCompactionCandidate] = field(default_factory=list)
    evicted: list[str] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@dataclass
class MemoryItem:
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
    # THR-032 Phase 1 additive frontmatter (defaults reproduce pre-rename behavior).
    provenance: str = "experiential"
    scope: str = "agent"
    lifecycle: str = "valid"
    salience: int = 50
    # THR-091: optional last-verified timestamp (ISO-8601). None = never verified.
    last_verified: str | None = None

    def age_summary(self, now: datetime | None = None) -> dict:
        """Compute entry age + optional last_verified age for display.

        Returns a dict with ``age_days`` (now - updated_at) and,
        ONLY when ``last_verified`` is set, ``last_verified_age_days``.
        """
        if now is None:
            now = datetime.now(timezone.utc)

        def _days_since(ts: str | None) -> int | None:
            if not ts:
                return None
            try:
                s = ts
                if s.endswith("Z"):
                    s = s[:-1] + "+00:00"
                dt = datetime.fromisoformat(s)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                delta = now - dt
                return max(0, delta.days)
            except (ValueError, TypeError):
                return None

        result: dict = {"age_days": _days_since(self.updated_at)}
        if self.last_verified:
            lv_days = _days_since(self.last_verified)
            if lv_days is not None:
                result["last_verified_age_days"] = lv_days
        return result


def _lrn_numeric_suffix(s: "LearningSummary") -> int:
    """Extract the integer suffix of an LRN-NNN id for numeric ordering.

    String sort breaks at LRN-1000+ ('LRN-999' > 'LRN-1000' lexicographically).
    """
    return int(s.id.split("-", 1)[1])


class MemoryStore:
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

    _ID_FILE_RE = re.compile(r"^(?:LRN|MEM)-(\d{3,})-")

    def _entry_paths(self) -> list["Path"]:
        """All entry files under either prefix, numeric-suffix ordered.

        Globbing both MEM-*.md and LRN-*.md lets a store hold a mix of migrated
        (MEM-) and not-yet-migrated (LRN-) files and still list/search/index
        them all — the permanent resolution shim at the directory level."""
        paths = [
            p
            for prefix in _ID_PREFIXES
            for p in self._root.glob(f"{prefix}-*.md")
            if p.name != "_index.md"
        ]

        def _num(p: "Path") -> int:
            m = self._ID_FILE_RE.match(p.name)
            return int(m.group(1)) if m else 0

        return sorted(paths, key=_num)

    def _validate_entry_structure(self, entry: MemoryItem) -> None:
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
        # THR-032 additive fields: enum-validate the three enums; clamp salience
        # (clamp, never reject) so the stored value is always in [0, 100].
        if entry.provenance not in PROVENANCE_VALUES:
            raise InvalidLearningEntry(
                "invalid_provenance", f"provenance {entry.provenance!r} not allowed"
            )
        if entry.scope not in SCOPE_VALUES:
            raise InvalidLearningEntry(
                "invalid_scope", f"scope {entry.scope!r} not allowed"
            )
        if entry.lifecycle not in LIFECYCLE_VALUES:
            raise InvalidLearningEntry(
                "invalid_lifecycle", f"lifecycle {entry.lifecycle!r} not allowed"
            )
        entry.salience = _clamp_salience(entry.salience)

    def next_id(self) -> str:
        # Continue the single per-agent number line across BOTH prefixes so the
        # first post-rename id is MEM-<max+1> (e.g. MEM-074 follows LRN-073) —
        # never a reset to 1 (§3.3).
        max_n = 0
        for path in self._entry_paths():
            m = self._ID_FILE_RE.match(path.name)
            if m:
                n = int(m.group(1))
                if n > max_n:
                    max_n = n
        return f"{CANONICAL_ID_PREFIX}-{max_n + 1:03d}"

    def path_for(self, id: str, slug: str) -> Path:
        return self._root / f"{id}-{slug}.md"

    def _find_by_id(self, id: str) -> Optional[Path]:
        # Permanent shim: try the id as given, then its prefix-swapped alias,
        # so `read LRN-061` resolves a migrated MEM-061 file forever (§7.2(b)).
        for variant in _id_variants(id):
            for path in self._root.glob(f"{variant}-*.md"):
                return path
        return None

    def _find_by_slug(self, slug: str) -> Optional[Path]:
        for prefix in _ID_PREFIXES:
            for path in self._root.glob(f"{prefix}-*-{slug}.md"):
                return path
        return None

    def _validate_cross_refs(self, entry: MemoryItem) -> None:
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

    def write_entry(self, entry: MemoryItem, agent: str) -> MemoryItem:
        self.validate_id(entry.id)
        self._validate_entry_structure(entry)
        self._validate_cross_refs(entry)
        if self._find_by_id(entry.id) is not None:
            raise LearningIdExists(entry.id)
        if self._find_by_slug(entry.slug) is not None:
            raise LearningSlugExists(entry.slug)
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        stamped = MemoryItem(**{**entry.__dict__})
        stamped.authored_by = agent
        stamped.authored_at = now
        stamped.updated_by = agent
        stamped.updated_at = now
        target = self.path_for(stamped.id, stamped.slug)
        self._atomic_write(target, self._serialize(stamped))
        # THR-032 P3b: when a new entry supersedes an existing one, mark the
        # predecessor `superseded` under the same serialized write. This closes
        # the gap where `supersedes` exists but the predecessor can still appear
        # `valid`. Existing historical supersedes do not need a migration.
        _superseded_prior = None
        if entry.supersedes is not None:
            _superseded_prior = self._mark_superseded_by(entry.supersedes, stamped.id, agent)
        # Store on the stamped item for route handler audit
        stamped._superseded_target_id = entry.supersedes if _superseded_prior else None
        return stamped

    def update_entry(
        self, id: str, entry: MemoryItem, agent: str,
    ) -> MemoryItem:
        self.validate_id(id)
        existing_path = self._find_by_id(id)
        if existing_path is None:
            raise LearningNotFound(id)
        existing = self._parse(existing_path.read_text())
        if existing.promoted_to is not None:
            raise PromotedLocked(id, existing.promoted_to)
        # Canonicalize the WRITE id to the resolved item's own on-disk id.
        # The LRN-/MEM- shim accepts a legacy id at the resolve boundary (`id`
        # may be LRN-061), but a migrated item must stay canonical MEM forever:
        # updating via the LRN- alias must NOT rewrite MEM-061 back to LRN-061
        # (§3.3/§7.2(b)). existing.id is the parsed canonical id, so this is
        # pure id-normalization — never a prefix flip.
        entry.id = existing.id
        entry.promoted_to = existing.promoted_to  # always None at this point
        self._validate_entry_structure(entry)
        self._validate_cross_refs(entry)
        # Reject slug collision with a DIFFERENT entry
        if entry.slug != existing.slug:
            if self._find_by_slug(entry.slug) is not None:
                raise LearningSlugExists(entry.slug)
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        stamped = MemoryItem(**{**entry.__dict__})
        stamped.authored_by = existing.authored_by
        stamped.authored_at = existing.authored_at
        stamped.updated_by = agent
        stamped.updated_at = now
        target = self.path_for(stamped.id, stamped.slug)
        self._atomic_write(target, self._serialize(stamped))
        # Remove the old file if slug changed
        if existing_path != target and existing_path.exists():
            existing_path.unlink()
        # THR-032 P3b: supersede marking on update
        _superseded_prior = None
        if entry.supersedes is not None:
            _superseded_prior = self._mark_superseded_by(entry.supersedes, stamped.id, agent)
        stamped._superseded_target_id = entry.supersedes if _superseded_prior else None
        return stamped

    def read_entry(self, id_or_slug: str) -> MemoryItem:
        path = (
            self._find_by_id(id_or_slug) if ID_RE.match(id_or_slug)
            else self._find_by_slug(id_or_slug)
        )
        if path is None:
            raise LearningNotFound(id_or_slug)
        return self._parse(path.read_text())

    def _serialize(self, entry: MemoryItem) -> str:
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
        # THR-032 additive keys — omit when equal to default so existing
        # files (which carry none of these keys) round-trip byte-identically.
        if entry.provenance != "experiential":
            fm["provenance"] = entry.provenance
        if entry.scope != "agent":
            fm["scope"] = entry.scope
        if entry.lifecycle != "valid":
            fm["lifecycle"] = entry.lifecycle
        if entry.salience != 50:
            fm["salience"] = entry.salience
        # THR-091: omit last_verified when None (byte-identical round-trip)
        if entry.last_verified is not None:
            fm["last_verified"] = entry.last_verified
        fm_text = yaml.safe_dump(fm, sort_keys=False).strip()
        body = entry.body if entry.body.endswith("\n") else entry.body + "\n"
        return f"---\n{fm_text}\n---\n\n{body}"

    def _parse(self, text: str) -> MemoryItem:
        if not text.startswith("---"):
            raise InvalidLearningEntry("missing_frontmatter", "no leading frontmatter")
        parts = text.split("---", 2)
        if len(parts) < 3:
            raise InvalidLearningEntry("missing_frontmatter", "malformed frontmatter")
        fm = yaml.safe_load(parts[1]) or {}
        body = parts[2].lstrip("\n")
        return MemoryItem(
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
            provenance=fm.get("provenance", "experiential"),
            scope=fm.get("scope", "agent"),
            lifecycle=fm.get("lifecycle", "valid"),
            salience=_clamp_salience(fm.get("salience", 50)),
            last_verified=fm.get("last_verified"),
            body=body,
        )

    def list_entries(
        self,
        topic: Optional[str] = None,
        tag: Optional[str] = None,
        promoted: Optional[bool] = None,
    ) -> list[LearningSummary]:
        out: list[LearningSummary] = []
        for path in self._entry_paths():
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
                lifecycle=entry.lifecycle,
                provenance=entry.provenance,
                salience=entry.salience,
            ))
        return out

    def search(
        self, query: str, limit: int = 20, include_promoted: bool = False,
    ) -> list[LearningSearchHit]:
        q = query.lower().strip()
        if not q:
            return []
        hits: list[LearningSearchHit] = []
        for path in self._entry_paths():
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

    def promote(self, id: str, kb_slug: str, agent: str) -> MemoryItem:
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
        stamped = MemoryItem(**{**existing.__dict__})
        stamped.promoted_to = kb_slug
        stamped.body = stub_body
        stamped.updated_by = agent
        stamped.updated_at = now
        target = self.path_for(stamped.id, stamped.slug)
        self._atomic_write(target, self._serialize(stamped))
        return stamped

    def regenerate_index(self) -> None:
        # THR-032: evicted items stay on disk for audit/undo but leave the index.
        summaries = [s for s in self.list_entries() if s.lifecycle != "evicted"]
        groups: dict[str, list[LearningSummary]] = {}
        for s in summaries:
            groups.setdefault(s.topic, []).append(s)
        for topic in groups:
            groups[topic].sort(key=_lrn_numeric_suffix, reverse=True)  # newest first
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        total = sum(len(v) for v in groups.values())
        lines = [
            f"# Memory Index",
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
                lines.append(
                    f"- `{s.id}` — {s.title}{tags_part}{promo_part}"
                    f"  ({s.provenance}, salience {s.salience})"
                )
            lines.append("")
        index_path = self._root / "_index.md"
        self._atomic_write(index_path, "\n".join(lines).rstrip() + "\n")

    # ── THR-032 Phase 2: PUSH memory digest (mechanism A) ──

    # Scoring constants — read-time only, never written back.
    _AGE_DECAY_PER_DAY = 2         # lose 2 salience points per day
    _AGE_DECAY_CAP = 30             # max age penalty
    _BRIEF_TITLE_BOOST = 10        # brief substring in title
    _BRIEF_TAG_TOPIC_BOOST = 5     # brief substring in tag or topic
    _ANCESTOR_BOOST = 20           # source_task is ancestor of current task
    _DIRECTIVE_BOOST = 10          # provenance == directive
    _DEFAULT_BUDGET = 1500

    _ALLOWED_TRANSITIONS: dict[str, set[str]] = {
        "valid": {"superseded", "evicted"},
        "superseded": {"evicted", "valid"},
        "evicted": {"valid"},
    }

    _DIGEST_HEADER = "=== MEMORY-DIGEST (system) ==="
    _DIGEST_INTRO = (
        "Relevant memory (pointers only — "
        "fetch bodies with `happyranch memory get <id>`):"
    )
    _DIGEST_NUDGE = (
        'Pull the long tail: `happyranch memory search "<terms>"`.'
    )
    _DIGEST_LINE_FMT = "- `{id}` — {title}  ({provenance}, salience {salience})"

    def _effective_salience(
        self,
        entry: MemoryItem,
        brief_lower: str,
        ancestor_ids: set[str] | None,
        now_dt: datetime,
        *,
        scope: str = "agent",
    ) -> int:
        """Compute effective salience at digest time (read-only).

        effective = base_salience - age_decay + relevance_boost
                    + ancestor_boost + directive_boost

        Age decay is computed from ``updated_at``; if unset, no decay.
        All boosts are additive and computed at read time — nothing is
        written back.
        """
        effective = entry.salience

        # Age decay
        if entry.updated_at:
            try:
                # updated_at is ISO-8601 with optional tz suffix
                ts = entry.updated_at
                if ts.endswith("Z"):
                    ts = ts[:-1] + "+00:00"
                updated = datetime.fromisoformat(ts)
                if updated.tzinfo is None:
                    updated = updated.replace(tzinfo=timezone.utc)
                delta = now_dt - updated
                age_days = max(0, delta.days)
                age_penalty = min(age_days * self._AGE_DECAY_PER_DAY,
                                  self._AGE_DECAY_CAP)
                effective -= age_penalty
            except (ValueError, TypeError):
                pass  # unparseable timestamp → no decay

        # Brief relevance boost (cheap substring family, same as search())
        if brief_lower:
            if brief_lower in entry.title.lower():
                effective += self._BRIEF_TITLE_BOOST
            elif brief_lower in entry.body.lower():
                # body match is weaker — just the tag/topic level
                effective += self._BRIEF_TAG_TOPIC_BOOST
            elif any(brief_lower in t.lower() for t in entry.tags):
                effective += self._BRIEF_TAG_TOPIC_BOOST
            elif brief_lower in entry.topic.lower():
                effective += self._BRIEF_TAG_TOPIC_BOOST
            else:
                # Multi-word brief: check individual words
                words = brief_lower.split()
                if len(words) > 1:
                    for w in words:
                        if len(w) >= 3 and w in entry.title.lower():
                            effective += self._BRIEF_TAG_TOPIC_BOOST
                            break
                        elif len(w) >= 3 and (
                            any(w in t.lower() for t in entry.tags)
                            or w in entry.topic.lower()
                        ):
                            effective += max(1, self._BRIEF_TAG_TOPIC_BOOST // 2)
                            break

        # Ancestor boost
        if ancestor_ids and entry.source_task:
            if entry.source_task in ancestor_ids:
                effective += self._ANCESTOR_BOOST

        # Directive provenance boost — only for matching scope.
        # Per-agent MemoryStore digests boost agent-scope directives;
        # team/org-scoped directives do not boost in per-agent digests.
        # (Team/org scoped memory store is later/founder-gated per §11.5.)
        if entry.provenance == "directive" and entry.scope == scope:
            effective += self._DIRECTIVE_BOOST

        return effective

    def build_memory_digest(
        self,
        brief: str,
        *,
        budget: int | None = None,
        ancestor_task_ids: set[str] | None = None,
        scope: str = "agent",
    ) -> str | None:
        """Build a salience-ranked, pointer-only, budgeted push digest.

        Returns the ``=== MEMORY-DIGEST (system) ===`` block as a string,
        or ``None`` when no candidate memories exist or the budget is too
        small to fit any valid digest content.

        Candidate set: ``lifecycle == valid`` AND ``promoted_to is None``.
        Ranking: effective salience (base - age decay + boosts) descending,
                 then title alphabetically for deterministic tie-breaking.
        Budget: char-capped (default ~1500); includes header + nudge when
                the candidate set overflows. For budgets too small to fit
                even the header + intro, returns None cleanly.
        Pointer-only: id, title, provenance, effective salience — NEVER body.
        Read-only: no files are written, no mtimes/timestamps are churned.
        """
        if budget is None:
            budget = self._DEFAULT_BUDGET

        now_dt = datetime.now(timezone.utc)
        brief_lower = brief.lower().strip() if brief else ""

        candidates: list[tuple[int, str, MemoryItem]] = []
        # (effective_salience, title_lower, MemoryItem) for sorting

        for path in self._entry_paths():
            try:
                entry = self._parse(path.read_text())
            except InvalidLearningEntry:
                continue
            # Exclusion filters
            if entry.lifecycle != "valid":
                continue
            if entry.promoted_to is not None:
                continue

            score = self._effective_salience(
                entry, brief_lower, ancestor_task_ids, now_dt, scope=scope,
            )
            candidates.append((score, entry.title.lower(), entry))

        if not candidates:
            return None

        # Sort: effective salience descending, then title ascending for
        # deterministic tie-breaking.
        candidates.sort(key=lambda c: (-c[0], c[2].title))

        # Fixed header block — always emitted first when any output is produced.
        header = f"{self._DIGEST_HEADER}\n{self._DIGEST_INTRO}\n\n"
        header_len = len(header)

        # If budget can't even fit the header, return None cleanly.
        if budget < header_len:
            return None

        nudge_line = f"{self._DIGEST_NUDGE}\n"
        nudge_len = len(nudge_line)

        # Build the digest: add pointer lines and nudge, tracking exact length.
        result_parts: list[str] = [header]
        used = header_len
        nudged = False

        for i, (score, _title_lower, entry) in enumerate(candidates):
            line = self._DIGEST_LINE_FMT.format(
                id=entry.id,
                title=entry.title,
                provenance=entry.provenance,
                salience=score,
            ) + "\n"
            line_len = len(line)
            remaining = len(candidates) - i - 1

            if remaining == 0 or nudged:
                # Last item or nudge already emitted — just check line fit.
                if used + line_len <= budget:
                    result_parts.append(line)
                    used += line_len
                else:
                    break
            else:
                # More candidates follow: try to reserve for nudge.
                if used + line_len + nudge_len <= budget:
                    # Both line + future nudge fit — add line, continue.
                    result_parts.append(line)
                    used += line_len
                elif used + nudge_len <= budget:
                    # Line + nudge don't fit, but nudge alone fits — emit nudge.
                    result_parts.append(nudge_line)
                    nudged = True
                    break
                elif used + line_len <= budget:
                    # Even nudge alone doesn't fit, but the line does — add it
                    # and stop (no nudge will be emitted).
                    result_parts.append(line)
                    used += line_len
                    break
                else:
                    # Nothing fits — stop.
                    break

        # If we didn't emit a nudge but there ARE remaining items and the
        # last added line left room, emit nudge now.
        if not nudged and len(result_parts) - 1 < len(candidates):
            if used + nudge_len <= budget:
                result_parts.append(nudge_line)

        result = "".join(result_parts)

        # If output is header-only (no pointer lines or nudge), return None.
        if len(result_parts) <= 1:
            return None

        # Final safety: result must never exceed budget.
        if len(result) > budget:
            return None

        return result

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

    # ── THR-032 P3a: explicit lifecycle transitions ──

    def set_lifecycle(
        self,
        id: str,
        lifecycle: str,
        *,
        agent: str,
        reason: str,
    ) -> tuple[MemoryItem, str]:
        """Transition a memory entry to a new lifecycle state.

        Returns ``(updated_item, prior_lifecycle)``.

        Raises:
            InvalidLearningId, LearningNotFound, InvalidLearningEntry,
            PromotedLocked
        """
        self.validate_id(id)
        if not reason or not reason.strip():
            raise InvalidLearningEntry(
                "reason_required", "reason must be non-empty"
            )
        reason = reason.strip()
        if lifecycle not in LIFECYCLE_VALUES:
            raise InvalidLearningEntry(
                "invalid_lifecycle",
                f"lifecycle {lifecycle!r} not in {LIFECYCLE_VALUES}",
            )
        existing_path = self._find_by_id(id)
        if existing_path is None:
            raise LearningNotFound(id)
        existing = self._parse(existing_path.read_text())
        if existing.promoted_to is not None:
            raise PromotedLocked(id, existing.promoted_to)
        prior = existing.lifecycle
        if prior == lifecycle:
            raise InvalidLearningEntry(
                "noop_transition",
                f"lifecycle is already {lifecycle!r}",
            )
        allowed = self._ALLOWED_TRANSITIONS.get(prior, set())
        if lifecycle not in allowed:
            raise InvalidLearningEntry(
                "unsupported_transition",
                f"cannot transition from {prior!r} to {lifecycle!r}",
            )
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        stamped = MemoryItem(**{**existing.__dict__})
        stamped.lifecycle = lifecycle
        stamped.updated_by = agent
        stamped.updated_at = now
        # Write to the canonical on-disk id file (never resurrect LRN)
        target = self.path_for(existing.id, existing.slug)
        self._atomic_write(target, self._serialize(stamped))
        return stamped, prior

    # ── THR-032 P3b: manual memory compaction ──

    def _mark_superseded_by(
        self, target_id: str, by_id: str, agent: str,
    ) -> str | None:
        """Mark an existing memory as superseded by another entry.

        Called within write_entry to maintain supersede consistency.
        Returns the prior lifecycle if a transition occurred, or None if no-op.
        """
        if not ID_RE.match(target_id):
            return None
        existing_path = self._find_by_id(target_id)
        if existing_path is None:
            return None
        existing = self._parse(existing_path.read_text())
        if existing.lifecycle in ("evicted", "superseded"):
            return None  # already transitioned
        if existing.promoted_to is not None:
            return None  # promoted items are locked
        prior = existing.lifecycle
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        stamped = MemoryItem(**{**existing.__dict__})
        stamped.lifecycle = "superseded"
        stamped.updated_by = agent
        stamped.updated_at = now
        target = self.path_for(existing.id, existing.slug)
        self._atomic_write(target, self._serialize(stamped))
        return prior

    # Cross-reference detection helpers for compaction protection
    def _is_referenced_by(self, entry_id: str, exclude_lifecycles: set[str] | None = None) -> bool:
        """Check if any non-evicted/non-superseded entry references ``entry_id``
        via ``related_to``, ``supersedes``, or body tokens ``MEM-NNN``/``LRN-NNN``."""
        if exclude_lifecycles is None:
            exclude_lifecycles = {"evicted"}
        # Build the set of id variants to search for (MEM-NNN and LRN-NNN aliases)
        variants = set(_id_variants(entry_id))
        for path in self._entry_paths():
            try:
                entry = self._parse(path.read_text())
            except InvalidLearningEntry:
                continue
            if entry.lifecycle in exclude_lifecycles:
                continue
            # direct refs — check all variants
            if any(v in entry.related_to for v in variants):
                return True
            if entry.supersedes in variants:
                return True
            # body token refs (MEM-NNN / LRN-NNN)
            for variant in variants:
                if re.search(rf"\b{re.escape(variant)}\b", entry.body):
                    return True
        return False

    def _compact_protected(self, entry: MemoryItem) -> str | None:
        """Return a reason string if an entry is protected from auto-eviction,
        or None if it is eligible."""
        if entry.promoted_to is not None:
            return "promoted"
        if entry.provenance == "directive":
            return "directive"
        if entry.lifecycle == "evicted":
            return "already_evicted"
        if entry.scope in ("team", "org"):
            return f"scope:{entry.scope}"
        if self._is_referenced_by(entry.id):
            return "referenced_by_others"
        return None

    def _compact_candidate(
        self,
        entry: MemoryItem,
        policy: MemoryCompactionPolicy,
        now_dt: datetime,
    ) -> MemoryCompactionCandidate | None:
        """Evaluate a single entry for compaction eligibility.

        Returns a candidate with reason, or None if not eligible.
        """
        # Protection checks first
        protected = self._compact_protected(entry)
        if protected is not None:
            return None

        # Superseded beyond grace period
        if entry.lifecycle == "superseded":
            if entry.updated_at:
                try:
                    ts = entry.updated_at
                    if ts.endswith("Z"):
                        ts = ts[:-1] + "+00:00"
                    updated = datetime.fromisoformat(ts)
                    if updated.tzinfo is None:
                        updated = updated.replace(tzinfo=timezone.utc)
                    age_days = (now_dt - updated).days
                    if age_days >= policy.superseded_grace_days:
                        return MemoryCompactionCandidate(
                            id=entry.id,
                            title=entry.title,
                            reason=f"superseded_{age_days}d (>={policy.superseded_grace_days}d grace)",
                            current_lifecycle="superseded",
                        )
                except (ValueError, TypeError):
                    pass
            return None

        # Low effective salience + stale
        # Compute effective salience at read time (never write back)
        effective = entry.salience
        if entry.updated_at:
            try:
                ts = entry.updated_at
                if ts.endswith("Z"):
                    ts = ts[:-1] + "+00:00"
                updated = datetime.fromisoformat(ts)
                if updated.tzinfo is None:
                    updated = updated.replace(tzinfo=timezone.utc)
                age_days = max(0, (now_dt - updated).days)
                age_penalty = min(age_days * self._AGE_DECAY_PER_DAY, self._AGE_DECAY_CAP)
                effective -= age_penalty
                if effective <= policy.salience_floor and age_days >= policy.stale_days:
                    return MemoryCompactionCandidate(
                        id=entry.id,
                        title=entry.title,
                        reason=f"stale_{age_days}d_salience_{effective}_<=_{policy.salience_floor}",
                        current_lifecycle="valid",
                    )
            except (ValueError, TypeError):
                pass

        return None

    def compact(
        self,
        *,
        dry_run: bool,
        now: datetime | None = None,
        policy: MemoryCompactionPolicy | None = None,
    ) -> MemoryCompactionResult:
        """Manual compaction dry-run or apply.

        dry_run=True: compute candidates and reasons, write nothing, return result.
        dry_run=False: re-evaluate under serialization boundary, evict eligible
        entries, regenerate index, return summary. Idempotent on rerun.

        No hard deletes, no renumbering, no history rewrite, no automatic
        scheduling.
        """
        if policy is None:
            policy = MemoryCompactionPolicy()
        if now is None:
            now = datetime.now(timezone.utc)

        # Phase 1: scan all entries and compute candidates
        candidates: list[MemoryCompactionCandidate] = []
        skipped: list[dict] = []

        for path in self._entry_paths():
            try:
                entry = self._parse(path.read_text())
            except InvalidLearningEntry:
                skipped.append({"id": path.name, "reason": "parse_error"})
                continue

            protected = self._compact_protected(entry)
            if protected is not None:
                skipped.append({"id": entry.id, "reason": protected})
                continue

            cand = self._compact_candidate(entry, policy, now)
            if cand is not None:
                if len(candidates) < policy.max_evictions_per_run:
                    candidates.append(cand)
                else:
                    skipped.append({"id": entry.id, "reason": "cap_reached"})
            else:
                skipped.append({"id": entry.id, "reason": "not_eligible"})

        if dry_run:
            return MemoryCompactionResult(
                dry_run=True,
                candidates=candidates,
                skipped=skipped,
            )

        # Phase 2: apply — recompute under same conditions and evict
        evicted: list[str] = []
        errors: list[str] = []

        for cand in candidates:
            existing_path = self._find_by_id(cand.id)
            if existing_path is None:
                errors.append(f"{cand.id}: not_found_on_apply")
                skipped.append({"id": cand.id, "reason": "not_found_on_apply"})
                continue
            try:
                existing = self._parse(existing_path.read_text())
            except InvalidLearningEntry:
                errors.append(f"{cand.id}: parse_error_on_apply")
                skipped.append({"id": cand.id, "reason": "parse_error_on_apply"})
                continue
            # Re-check protection and eligibility under write boundary
            if self._compact_protected(existing) is not None:
                skipped.append({"id": cand.id, "reason": "became_protected"})
                continue
            recheck = self._compact_candidate(existing, policy, now)
            if recheck is None:
                skipped.append({"id": cand.id, "reason": "no_longer_eligible"})
                continue
            if existing.lifecycle == "evicted":
                skipped.append({"id": cand.id, "reason": "already_evicted"})
                continue
            now_str = now.strftime("%Y-%m-%dT%H:%M:%SZ")
            stamped = MemoryItem(**{**existing.__dict__})
            stamped.lifecycle = "evicted"
            stamped.updated_by = "compaction"
            stamped.updated_at = now_str
            target = self.path_for(existing.id, existing.slug)
            self._atomic_write(target, self._serialize(stamped))
            evicted.append(cand.id)

        # Regenerate index after all writes
        self.regenerate_index()

        return MemoryCompactionResult(
            dry_run=False,
            candidates=candidates,
            evicted=evicted,
            skipped=skipped,
            errors=errors,
        )

    # ── THR-032 P4a: improved memory search ranking ──

    # Scoring constants for P4a multi-term ranking
    _SEARCH_TITLE_EXACT = 40
    _SEARCH_TITLE_TERM = 12
    _SEARCH_TAG_TOPIC_TERM = 8
    _SEARCH_BODY_TERM = 4
    _SEARCH_BODY_CAP = 16
    _SEARCH_PROVENANCE_BOOST: dict[str, int] = {"directive": 8, "reflective": 3, "experiential": 0}
    _SEARCH_SUPERSEDED_PENALTY = 20
    _SEARCH_SALIENCE_DIVISOR = 10
    _SEARCH_SALIENCE_MAX = 10

    def search(
        self,
        query: str,
        limit: int = 20,
        include_promoted: bool = False,
        include_evicted: bool = False,
        include_superseded: bool = False,
    ) -> list[LearningSearchHit]:
        """Multi-term ranked search over memory items.

        Scoring:
        - title exact full-query substring: +40
        - title term match: +12 per term
        - tag/topic term match: +8 per term
        - body term match: +4 per term, capped at +16
        - provenance boost: directive +8, reflective +3, experiential +0
        - lifecycle penalty: superseded -20
        - effective salience: round(salience/10), max +10
        - recency tie-breaker from updated_at

        Excludes evicted and superseded by default; include with flags.
        Empty query returns no hits and exit 0.
        """
        q = query.lower().strip()
        if not q:
            return []

        # Normalize: split on whitespace, drop terms shorter than 3
        # unless the whole query is short
        terms = [t for t in q.split() if len(t) >= 3]
        if not terms:
            # query is only short words — use the whole query as a single term
            terms = [q]

        hits: list[tuple[int, LearningSearchHit]] = []

        for path in self._entry_paths():
            try:
                entry = self._parse(path.read_text())
            except InvalidLearningEntry:
                continue

            # Exclusion filters
            if entry.promoted_to is not None and not include_promoted:
                continue
            if entry.lifecycle == "evicted" and not include_evicted:
                continue
            if entry.lifecycle == "superseded" and not include_superseded:
                continue

            score = 0
            matched = False
            title_l = entry.title.lower()
            topic_l = entry.topic.lower()
            tags_l = [t.lower() for t in entry.tags]
            body_l = entry.body.lower()

            # Title exact match
            if q in title_l:
                score += self._SEARCH_TITLE_EXACT
                matched = True

            # Term-level scoring
            for term in terms:
                if term in title_l:
                    score += self._SEARCH_TITLE_TERM
                    matched = True
                if term in topic_l or any(term in t for t in tags_l):
                    score += self._SEARCH_TAG_TOPIC_TERM
                    matched = True

            # Body term match (capped)
            body_points = 0
            for term in terms:
                if term in body_l:
                    body_points += self._SEARCH_BODY_TERM
                    matched = True
            body_points = min(body_points, self._SEARCH_BODY_CAP)
            score += body_points

            # Only include entries with at least one actual query-term match.
            # Provenance, salience, and lifecycle modifiers only rank matched
            # entries — they never cause an unrelated entry to appear.
            if not matched:
                continue

            # Provenance boost
            score += self._SEARCH_PROVENANCE_BOOST.get(entry.provenance, 0)

            # Lifecycle penalty
            if entry.lifecycle == "superseded":
                score -= self._SEARCH_SUPERSEDED_PENALTY

            # Effective salience contribution
            salience_contrib = min(
                round(max(0, entry.salience) / self._SEARCH_SALIENCE_DIVISOR),
                self._SEARCH_SALIENCE_MAX,
            )
            score += salience_contrib

            # Snippet: prefer matching portion of body, else title
            snippet = self._snippet(entry.body, q)

            hits.append((score, LearningSearchHit(
                id=entry.id,
                slug=entry.slug,
                title=entry.title,
                snippet=snippet,
                score=score,
                source="memory",
                lifecycle=entry.lifecycle,
                provenance=entry.provenance,
                salience=entry.salience,
                updated_at=entry.updated_at,
            )))

        # Deterministic sort: score desc, salience desc, updated_at desc,
        # title asc, id asc. Multi-pass stable sort with least-significant
        # key first so most-significant key wins on ties.
        hits.sort(key=lambda item: item[1].id)                     # id asc
        hits.sort(key=lambda item: item[1].title)                   # title asc
        # updated_at desc: None/empty → last; ISO 8601 lexicographic = chronological
        hits.sort(
            key=lambda item: (1 if (item[1].updated_at or "") else 0,
                              item[1].updated_at or ""),
            reverse=True,
        )
        hits.sort(key=lambda item: (item[1].salience or 0,), reverse=True)   # salience desc
        hits.sort(key=lambda item: (item[0],), reverse=True)                  # score desc

        return [h[1] for h in hits[:limit]]


# Back-compat aliases (THR-032 Phase 1). The class + dataclass are renamed to
# MemoryStore / MemoryItem; these aliases keep all current importers
# (workspace_adapters.py, dreams.py, agents.py, tests) resolving the pre-rename
# names with zero signature change. Retired in a later cleanup, not in Phase 1.
LearningsStore = MemoryStore
LearningEntry = MemoryItem
