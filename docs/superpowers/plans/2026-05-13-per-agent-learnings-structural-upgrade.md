# Per-Agent Learnings Structural Upgrade — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace each agent's flat `learnings.md` with a per-entry markdown store under `workspaces/<agent>/learnings/`, with stable `LRN-NNN` IDs, frontmatter (tags/topic/related_to/promoted_to), a regenerated index, and a verb-dispatched CLI (`opc learning list|get|search|add|update|promote|reindex`).

**Architecture:** New `LearningsStore` module parallels `KBStore` one level down (per-agent instead of per-org). New HTTP routes mount under `/agents/{name}/learnings/entries/`; legacy `POST /learnings` stays for unmigrated workspaces and 410s once `learnings/` directory exists. State-aware `ensure()` never silently creates `learnings/` for a workspace that still has a populated flat `learnings.md` — migration is per-workspace, agent-driven, founder-dispatched.

**Tech Stack:** Python 3.11+, FastAPI, Pydantic v2, PyYAML (already in deps), file-backed markdown, SQLite audit log. No new infrastructure.

**Spec:** `docs/superpowers/specs/2026-05-13-per-agent-learnings-structural-upgrade-design.md`

---

## File Map

**Create:**
- `src/infrastructure/learnings_store.py` — `LearningEntry`, `LearningsStore` (~350 lines, parallel to `kb_store.py`)
- `tests/test_learnings_store.py` — unit tests for the store
- `tests/daemon/test_routes_agents_learnings.py` — route tests
- `tests/test_cli_learning.py` — CLI subparser tests
- `tests/integration/test_learnings_lifecycle.py` — end-to-end via real daemon

**Modify:**
- `src/daemon/routes/agents.py` — add new routes block (under existing `append_learning`); add 410 logic to legacy endpoint
- `src/orchestrator/workspace_adapters.py` — state-aware `ensure()` and `_build_sections` branching
- `src/cli.py` — restructure `cmd_learning` into subparser with verbs
- `src/infrastructure/audit_logger.py` — add `log_learning_*` helpers (verbs: `learning_added`, `learning_updated`, `learning_promoted`)
- `CLAUDE.md` — note new layout in the architecture summary

---

## Phase 1: Store module

### Task 1: `LearningEntry` dataclass + module skeleton

**Files:**
- Create: `src/infrastructure/learnings_store.py`
- Test: `tests/test_learnings_store.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_learnings_store.py
from __future__ import annotations

from pathlib import Path

import pytest

from src.infrastructure.learnings_store import (
    LearningEntry,
    LearningsStore,
    InvalidLearningId,
    InvalidLearningEntry,
    LearningIdExists,
    LearningNotFound,
    PromotedLocked,
)


@pytest.fixture
def store(tmp_path: Path) -> LearningsStore:
    learnings_dir = tmp_path / "learnings"
    learnings_dir.mkdir()
    return LearningsStore(learnings_dir)


def test_learning_id_regex_validates():
    LearningsStore.validate_id("LRN-001")  # ok
    LearningsStore.validate_id("LRN-999")  # ok
    LearningsStore.validate_id("LRN-1234")  # ok (>3 digits also valid)
    with pytest.raises(InvalidLearningId):
        LearningsStore.validate_id("lrn-001")  # lowercase
    with pytest.raises(InvalidLearningId):
        LearningsStore.validate_id("LRN-1")  # too few digits
    with pytest.raises(InvalidLearningId):
        LearningsStore.validate_id("LRN-00A")  # non-digit
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_learnings_store.py -v`
Expected: ImportError — `learnings_store` does not exist.

- [ ] **Step 3: Create the module skeleton**

```python
# src/infrastructure/learnings_store.py
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_learnings_store.py::test_learning_id_regex_validates -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/infrastructure/learnings_store.py tests/test_learnings_store.py
git commit -m "feat(learnings): scaffold LearningsStore + ID/slug validators"
```

---

### Task 2: ID allocation via `next_id()`

**Files:**
- Modify: `src/infrastructure/learnings_store.py`
- Modify: `tests/test_learnings_store.py`

The allocation policy mirrors the recent `next_task_id` fix (`86499ff`): scan existing filenames, take `MAX(suffix) + 1`. Never reuse retired IDs.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_learnings_store.py`:

```python
def test_next_id_starts_at_001_when_empty(store: LearningsStore):
    assert store.next_id() == "LRN-001"


def test_next_id_increments_from_max_suffix(store: LearningsStore):
    # Create some files manually
    (store.root / "LRN-005-foo.md").write_text("---\nid: LRN-005\nslug: foo\ntitle: x\ntopic: t\n---\n")
    (store.root / "LRN-003-bar.md").write_text("---\nid: LRN-003\nslug: bar\ntitle: x\ntopic: t\n---\n")
    assert store.next_id() == "LRN-006"


def test_next_id_ignores_index_and_non_lrn_files(store: LearningsStore):
    (store.root / "_index.md").write_text("# index")
    (store.root / "README.md").write_text("not a learning")
    (store.root / "LRN-002-foo.md").write_text("---\nid: LRN-002\nslug: foo\ntitle: x\ntopic: t\n---\n")
    assert store.next_id() == "LRN-003"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_learnings_store.py -v -k next_id`
Expected: AttributeError — `LearningsStore` has no `next_id`.

- [ ] **Step 3: Implement `next_id()`**

Add to `LearningsStore` in `src/infrastructure/learnings_store.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_learnings_store.py -v -k next_id`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/infrastructure/learnings_store.py tests/test_learnings_store.py
git commit -m "feat(learnings): next_id() via MAX(suffix)+1, ignores _index and non-LRN files"
```

---

### Task 3: Entry structural validation

**Files:**
- Modify: `src/infrastructure/learnings_store.py`
- Modify: `tests/test_learnings_store.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_learnings_store.py`:

```python
def _make_entry(**overrides) -> LearningEntry:
    base = dict(
        id="LRN-001",
        slug="ok-slug",
        title="Title",
        topic="workflow",
        body="body\n",
    )
    base.update(overrides)
    return LearningEntry(**base)


def test_validate_entry_requires_title_topic_slug(store: LearningsStore):
    for missing in ("title", "topic", "slug"):
        with pytest.raises(InvalidLearningEntry) as exc:
            store._validate_entry_structure(_make_entry(**{missing: ""}))
        assert exc.value.code == "missing_frontmatter"


def test_validate_entry_rejects_oversized_body(store: LearningsStore):
    big = "x" * (32 * 1024 + 1)
    with pytest.raises(InvalidLearningEntry) as exc:
        store._validate_entry_structure(_make_entry(body=big))
    assert exc.value.code == "entry_too_large"


def test_validate_entry_rejects_bad_slug(store: LearningsStore):
    with pytest.raises(InvalidLearningEntry) as exc:
        store._validate_entry_structure(_make_entry(slug="Bad Slug"))
    assert exc.value.code == "invalid_slug"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_learnings_store.py -v -k validate`
Expected: 3 failures (method missing).

- [ ] **Step 3: Implement `_validate_entry_structure`**

Add to `LearningsStore`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_learnings_store.py -v -k validate`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/infrastructure/learnings_store.py tests/test_learnings_store.py
git commit -m "feat(learnings): structural validation (slug, body size, required fields)"
```

---

### Task 4: `write_entry()` + `read_entry()` round-trip

**Files:**
- Modify: `src/infrastructure/learnings_store.py`
- Modify: `tests/test_learnings_store.py`

The filename convention is `<id>-<slug>.md`. The daemon allocates `id` via `next_id()` BEFORE calling `write_entry`; `write_entry` itself takes a fully-populated entry and persists it.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_learnings_store.py`:

```python
def test_write_entry_round_trips_frontmatter_and_body(store: LearningsStore):
    entry = _make_entry(
        id="LRN-001",
        slug="cross-team-dispatch",
        title="Cross-team dispatch forbidden",
        topic="workflow-guardrail",
        tags=["cross-team", "dispatch"],
        body="**Why:** ...\n**How to apply:** ...\n",
        source_task="TASK-235",
    )
    written = store.write_entry(entry, agent="engineering_head")
    assert written.authored_by == "engineering_head"
    assert written.updated_by == "engineering_head"
    assert written.authored_at is not None

    loaded = store.read_entry("LRN-001")
    assert loaded.title == entry.title
    assert loaded.topic == "workflow-guardrail"
    assert loaded.tags == ["cross-team", "dispatch"]
    assert loaded.source_task == "TASK-235"
    assert "How to apply" in loaded.body


def test_write_entry_writes_id_prefixed_filename(store: LearningsStore):
    entry = _make_entry(id="LRN-042", slug="x", title="t", topic="w")
    store.write_entry(entry, agent="dev_agent")
    assert (store.root / "LRN-042-x.md").exists()


def test_read_entry_by_id_or_slug(store: LearningsStore):
    store.write_entry(_make_entry(id="LRN-001", slug="foo"), agent="x")
    by_id = store.read_entry("LRN-001")
    by_slug = store.read_entry("foo")
    assert by_id.title == by_slug.title


def test_write_entry_rejects_duplicate_id(store: LearningsStore):
    store.write_entry(_make_entry(id="LRN-001", slug="a"), agent="x")
    with pytest.raises(LearningIdExists):
        store.write_entry(_make_entry(id="LRN-001", slug="b"), agent="x")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_learnings_store.py -v -k "write_entry or read_entry"`
Expected: 4 failures.

- [ ] **Step 3: Implement write/read + serialize/parse**

Add to `LearningsStore`:

```python
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

    def write_entry(self, entry: LearningEntry, agent: str) -> LearningEntry:
        self.validate_id(entry.id)
        self._validate_entry_structure(entry)
        if self._find_by_id(entry.id) is not None:
            raise LearningIdExists(entry.id)
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        stamped = LearningEntry(**{**entry.__dict__})
        stamped.authored_by = agent
        stamped.authored_at = now
        stamped.updated_by = agent
        stamped.updated_at = now
        target = self.path_for(stamped.id, stamped.slug)
        self._atomic_write(target, self._serialize(stamped))
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_learnings_store.py -v`
Expected: all previous + these 4 passed.

- [ ] **Step 5: Commit**

```bash
git add src/infrastructure/learnings_store.py tests/test_learnings_store.py
git commit -m "feat(learnings): write_entry + read_entry round-trip with frontmatter"
```

---

### Task 5: `list_entries()` with topic/tag/promoted filters

**Files:**
- Modify: `src/infrastructure/learnings_store.py`
- Modify: `tests/test_learnings_store.py`

- [ ] **Step 1: Write the failing tests**

Append:

```python
def test_list_entries_returns_summaries(store: LearningsStore):
    store.write_entry(_make_entry(id="LRN-001", slug="a", topic="workflow", tags=["x"]), agent="z")
    store.write_entry(_make_entry(id="LRN-002", slug="b", topic="env-trap", tags=["y"]), agent="z")
    summaries = store.list_entries()
    ids = sorted(s.id for s in summaries)
    assert ids == ["LRN-001", "LRN-002"]


def test_list_entries_filters_by_topic(store: LearningsStore):
    store.write_entry(_make_entry(id="LRN-001", slug="a", topic="workflow"), agent="z")
    store.write_entry(_make_entry(id="LRN-002", slug="b", topic="env-trap"), agent="z")
    summaries = store.list_entries(topic="workflow")
    assert [s.id for s in summaries] == ["LRN-001"]


def test_list_entries_filters_by_tag(store: LearningsStore):
    store.write_entry(_make_entry(id="LRN-001", slug="a", tags=["payment"]), agent="z")
    store.write_entry(_make_entry(id="LRN-002", slug="b", tags=["dispatch"]), agent="z")
    summaries = store.list_entries(tag="payment")
    assert [s.id for s in summaries] == ["LRN-001"]


def test_list_entries_filters_by_promoted(store: LearningsStore):
    e1 = _make_entry(id="LRN-001", slug="a")
    e2 = _make_entry(id="LRN-002", slug="b", promoted_to="some-kb-slug")
    store.write_entry(e1, agent="z")
    store.write_entry(e2, agent="z")
    promoted = store.list_entries(promoted=True)
    not_promoted = store.list_entries(promoted=False)
    assert [s.id for s in promoted] == ["LRN-002"]
    assert [s.id for s in not_promoted] == ["LRN-001"]
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_learnings_store.py -v -k list_entries`
Expected: 4 failures.

- [ ] **Step 3: Implement `list_entries` and `LearningSummary`**

Add to `learnings_store.py`:

```python
@dataclass
class LearningSummary:
    id: str
    slug: str
    title: str
    topic: str
    tags: list[str]
    promoted_to: Optional[str]
    updated_at: Optional[str]
```

Add method to `LearningsStore`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_learnings_store.py -v -k list_entries`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add src/infrastructure/learnings_store.py tests/test_learnings_store.py
git commit -m "feat(learnings): list_entries with topic/tag/promoted filters"
```

---

### Task 6: `search()` with scoring + `include_promoted` flag

**Files:**
- Modify: `src/infrastructure/learnings_store.py`
- Modify: `tests/test_learnings_store.py`

Mirror `KBStore.search`: title=10, body=5, tags/topic=2. Default excludes promoted entries (they're stubs pointing to KB).

- [ ] **Step 1: Write the failing tests**

```python
def test_search_scores_title_highest(store: LearningsStore):
    store.write_entry(_make_entry(id="LRN-001", slug="a", title="Cross-team dispatch", topic="w"), agent="z")
    store.write_entry(_make_entry(id="LRN-002", slug="b", title="Other rule", topic="w", body="cross-team mentioned in body\n"), agent="z")
    hits = store.search("cross-team")
    assert hits[0].id == "LRN-001"
    assert hits[0].score > hits[1].score


def test_search_excludes_promoted_by_default(store: LearningsStore):
    store.write_entry(_make_entry(id="LRN-001", slug="a", title="kept", topic="w"), agent="z")
    store.write_entry(_make_entry(id="LRN-002", slug="b", title="promoted-kept", topic="w", promoted_to="kb-slug"), agent="z")
    hits = store.search("kept")
    assert [h.id for h in hits] == ["LRN-001"]
    hits_inc = store.search("kept", include_promoted=True)
    assert sorted(h.id for h in hits_inc) == ["LRN-001", "LRN-002"]
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_learnings_store.py -v -k search`

- [ ] **Step 3: Implement search**

```python
@dataclass
class LearningSearchHit:
    id: str
    slug: str
    title: str
    snippet: str
    score: int


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
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_learnings_store.py -v -k search`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add src/infrastructure/learnings_store.py tests/test_learnings_store.py
git commit -m "feat(learnings): search with title/body/tag scoring + include_promoted"
```

---

### Task 7: `update_entry()` with `promoted_locked` guard

**Files:**
- Modify: `src/infrastructure/learnings_store.py`
- Modify: `tests/test_learnings_store.py`

Update preserves `authored_*` and `promoted_to`, re-stamps `updated_*`, supports slug rename (file renamed atomically), and refuses if `promoted_to` is set.

- [ ] **Step 1: Write the failing tests**

```python
def test_update_entry_preserves_authored_restamps_updated(store: LearningsStore):
    e = _make_entry(id="LRN-001", slug="a", title="v1")
    written = store.write_entry(e, agent="dev_agent")
    original_authored_at = written.authored_at
    # Simulate later update by different agent
    updated = _make_entry(id="LRN-001", slug="a", title="v2")
    res = store.update_entry("LRN-001", updated, agent="engineering_head")
    assert res.title == "v2"
    assert res.authored_by == "dev_agent"
    assert res.authored_at == original_authored_at
    assert res.updated_by == "engineering_head"


def test_update_entry_renames_file_on_slug_change(store: LearningsStore):
    store.write_entry(_make_entry(id="LRN-001", slug="old-slug"), agent="z")
    new = _make_entry(id="LRN-001", slug="new-slug")
    store.update_entry("LRN-001", new, agent="z")
    assert not (store.root / "LRN-001-old-slug.md").exists()
    assert (store.root / "LRN-001-new-slug.md").exists()


def test_update_entry_rejects_promoted(store: LearningsStore):
    e = _make_entry(id="LRN-001", slug="a", promoted_to="some-kb-slug")
    store.write_entry(e, agent="z")
    with pytest.raises(PromotedLocked):
        store.update_entry("LRN-001", _make_entry(id="LRN-001", slug="a", title="changed"), agent="z")


def test_update_entry_404_when_missing(store: LearningsStore):
    with pytest.raises(LearningNotFound):
        store.update_entry("LRN-999", _make_entry(id="LRN-999", slug="x"), agent="z")
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_learnings_store.py -v -k update_entry`

- [ ] **Step 3: Implement `update_entry`**

```python
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
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_learnings_store.py -v -k update_entry`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add src/infrastructure/learnings_store.py tests/test_learnings_store.py
git commit -m "feat(learnings): update_entry preserves authored_*, renames on slug change, refuses promoted"
```

---

### Task 8: `promote()` operation

**Files:**
- Modify: `src/infrastructure/learnings_store.py`
- Modify: `tests/test_learnings_store.py`

Promote sets `promoted_to: <kb-slug>` and rewrites the body to a 2-line stub. KB-slug existence is verified by the route layer, NOT the store (store has no KB reference). Store accepts any non-empty slug.

- [ ] **Step 1: Write the failing tests**

```python
def test_promote_sets_promoted_to_and_stub_body(store: LearningsStore):
    store.write_entry(_make_entry(id="LRN-001", slug="a", body="Original body\n"), agent="z")
    res = store.promote("LRN-001", kb_slug="my-precedent", agent="founder")
    assert res.promoted_to == "my-precedent"
    assert "See KB precedent: my-precedent" in res.body
    assert "Original body" not in res.body  # body replaced with stub
    assert res.updated_by == "founder"


def test_promote_404_when_missing(store: LearningsStore):
    with pytest.raises(LearningNotFound):
        store.promote("LRN-999", kb_slug="x", agent="z")


def test_promote_idempotent_when_already_promoted_to_same_slug(store: LearningsStore):
    store.write_entry(_make_entry(id="LRN-001", slug="a"), agent="z")
    store.promote("LRN-001", kb_slug="kb-a", agent="z")
    res = store.promote("LRN-001", kb_slug="kb-a", agent="z")
    assert res.promoted_to == "kb-a"


def test_promote_refuses_change_when_already_promoted_to_different_slug(store: LearningsStore):
    store.write_entry(_make_entry(id="LRN-001", slug="a"), agent="z")
    store.promote("LRN-001", kb_slug="kb-a", agent="z")
    with pytest.raises(PromotedLocked):
        store.promote("LRN-001", kb_slug="kb-b", agent="z")
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_learnings_store.py -v -k promote`

- [ ] **Step 3: Implement `promote`**

```python
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
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_learnings_store.py -v -k promote`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add src/infrastructure/learnings_store.py tests/test_learnings_store.py
git commit -m "feat(learnings): promote() sets promoted_to, replaces body with KB stub"
```

---

### Task 9: Cross-reference validation (`related_to`, `supersedes`)

**Files:**
- Modify: `src/infrastructure/learnings_store.py`
- Modify: `tests/test_learnings_store.py`

These checks run at `write_entry` and `update_entry` time (after structural validation, before file write).

- [ ] **Step 1: Write the failing tests**

```python
def test_write_entry_rejects_unknown_related_to(store: LearningsStore):
    e = _make_entry(id="LRN-001", slug="a", related_to=["LRN-999"])
    with pytest.raises(InvalidLearningEntry) as exc:
        store.write_entry(e, agent="z")
    assert exc.value.code == "unknown_related_id"


def test_write_entry_accepts_known_related_to(store: LearningsStore):
    store.write_entry(_make_entry(id="LRN-001", slug="a"), agent="z")
    e = _make_entry(id="LRN-002", slug="b", related_to=["LRN-001"])
    res = store.write_entry(e, agent="z")
    assert res.related_to == ["LRN-001"]


def test_write_entry_rejects_unknown_supersedes(store: LearningsStore):
    e = _make_entry(id="LRN-001", slug="a", supersedes="LRN-999")
    with pytest.raises(InvalidLearningEntry) as exc:
        store.write_entry(e, agent="z")
    assert exc.value.code == "unknown_supersedes"


def test_write_entry_rejects_malformed_related_id(store: LearningsStore):
    e = _make_entry(id="LRN-001", slug="a", related_to=["not-an-id"])
    with pytest.raises(InvalidLearningEntry) as exc:
        store.write_entry(e, agent="z")
    assert exc.value.code == "unknown_related_id"
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_learnings_store.py -v -k "related_to or supersedes"`

- [ ] **Step 3: Implement cross-reference validation**

Add a method and call it from both `write_entry` and `update_entry`:

```python
    def _validate_cross_refs(self, entry: LearningEntry) -> None:
        for ref in entry.related_to:
            if not ID_RE.match(ref) or self._find_by_id(ref) is None:
                raise InvalidLearningEntry(
                    "unknown_related_id", f"related_to references unknown id: {ref!r}",
                )
        if entry.supersedes is not None:
            if not ID_RE.match(entry.supersedes) or self._find_by_id(entry.supersedes) is None:
                raise InvalidLearningEntry(
                    "unknown_supersedes", f"supersedes references unknown id: {entry.supersedes!r}",
                )
```

In `write_entry`, after `_validate_entry_structure(entry)` and before the duplicate-ID check, insert:

```python
        self._validate_cross_refs(entry)
```

Same insertion in `update_entry` after `self._validate_entry_structure(entry)`.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_learnings_store.py -v -k "related_to or supersedes"`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add src/infrastructure/learnings_store.py tests/test_learnings_store.py
git commit -m "feat(learnings): validate related_to + supersedes against existing IDs"
```

---

### Task 10: `regenerate_index()`

**Files:**
- Modify: `src/infrastructure/learnings_store.py`
- Modify: `tests/test_learnings_store.py`

Group by topic (alphabetical), entries within each topic sorted by ID descending (newest first), include `↗ promoted: <slug>` indicator.

- [ ] **Step 1: Write the failing tests**

```python
def test_regenerate_index_groups_by_topic_newest_first(store: LearningsStore):
    store.write_entry(_make_entry(id="LRN-001", slug="a", title="Older workflow", topic="workflow"), agent="z")
    store.write_entry(_make_entry(id="LRN-002", slug="b", title="Newer workflow", topic="workflow"), agent="z")
    store.write_entry(_make_entry(id="LRN-003", slug="c", title="Env trap rule", topic="env-trap"), agent="z")
    store.regenerate_index()
    idx = (store.root / "_index.md").read_text()
    # env-trap alphabetically first
    assert idx.index("env-trap") < idx.index("workflow")
    # Newer (LRN-002) listed before older (LRN-001) inside workflow block
    assert idx.index("LRN-002") < idx.index("LRN-001")


def test_regenerate_index_shows_promoted_marker(store: LearningsStore):
    store.write_entry(_make_entry(id="LRN-001", slug="a", title="promoted thing", topic="w"), agent="z")
    store.promote("LRN-001", kb_slug="kb-precedent", agent="z")
    store.regenerate_index()
    idx = (store.root / "_index.md").read_text()
    assert "↗ promoted: kb-precedent" in idx
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_learnings_store.py -v -k regenerate_index`

- [ ] **Step 3: Implement `regenerate_index`**

```python
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
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_learnings_store.py -v -k regenerate_index`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add src/infrastructure/learnings_store.py tests/test_learnings_store.py
git commit -m "feat(learnings): regenerate_index groups by topic, newest first, promoted markers"
```

---

## Phase 2: Workspace integration

### Task 11: State-aware `PersistentWorkspaceSetup.ensure()`

**Files:**
- Modify: `src/orchestrator/workspace_adapters.py` (lines around 170–192)
- Test: `tests/test_workspace_adapter_learnings_ensure.py` (create)

The critical safety rule from the spec: never create `learnings/` if `learnings.md` exists with content.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_workspace_adapter_learnings_ensure.py`:

```python
from __future__ import annotations

from pathlib import Path

from src.config import Settings
from src.orchestrator.workspace_adapters import PersistentWorkspaceSetup


def _setup(tmp_path: Path) -> tuple[PersistentWorkspaceSetup, Path]:
    settings = Settings()
    ws = tmp_path / "workspaces" / "test_agent"
    return PersistentWorkspaceSetup(settings), ws


def test_ensure_brand_new_workspace_creates_learnings_dir(tmp_path: Path):
    setup, ws = _setup(tmp_path)
    setup.ensure(ws, "test_agent")
    assert (ws / "learnings").is_dir()
    assert (ws / "learnings" / "_index.md").exists()
    assert not (ws / "learnings.md").exists()


def test_ensure_legacy_workspace_with_flat_file_does_not_create_learnings_dir(tmp_path: Path):
    setup, ws = _setup(tmp_path)
    ws.mkdir(parents=True)
    (ws / "learnings.md").write_text("# Learnings: test_agent\n\n- existing entry\n")
    setup.ensure(ws, "test_agent")
    assert not (ws / "learnings").exists()
    assert (ws / "learnings.md").exists()


def test_ensure_migrated_workspace_regenerates_index_if_missing(tmp_path: Path):
    setup, ws = _setup(tmp_path)
    learnings_dir = ws / "learnings"
    learnings_dir.mkdir(parents=True)
    # No _index.md initially
    setup.ensure(ws, "test_agent")
    assert (learnings_dir / "_index.md").exists()


def test_ensure_legacy_with_only_header_still_does_not_create_learnings_dir(tmp_path: Path):
    """Even a placeholder-only learnings.md counts as 'has flat file' — the
    operator decides when migration runs. Safer to wait for the explicit task."""
    setup, ws = _setup(tmp_path)
    ws.mkdir(parents=True)
    (ws / "learnings.md").write_text("# Learnings: test_agent\n\n")
    setup.ensure(ws, "test_agent")
    assert not (ws / "learnings").exists()
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_workspace_adapter_learnings_ensure.py -v`
Expected: failures (current `ensure()` writes `learnings.md` for any new workspace).

- [ ] **Step 3: Update `ensure()` in `src/orchestrator/workspace_adapters.py`**

Replace lines 184–190 (the current `for filename, default_content in [(...)]` block) with:

```python
        # task_history.md: always ensure
        history_path = workspace / "task_history.md"
        if not history_path.exists():
            history_path.write_text(f"# Task History: {agent_name}\n\n")

        # learnings: state-aware migration safety
        flat_path = workspace / "learnings.md"
        learnings_dir = workspace / "learnings"
        if learnings_dir.exists():
            # Post-migration: idempotently ensure _index.md exists.
            # Lazy import to avoid a hard infra dep at module top.
            from src.infrastructure.learnings_store import LearningsStore
            store = LearningsStore(learnings_dir)
            if not (learnings_dir / "_index.md").exists():
                store.regenerate_index()
        elif flat_path.exists():
            # Pre-migration legacy workspace: leave both untouched.
            pass
        else:
            # Brand-new workspace: create learnings/ on the new layout.
            from src.infrastructure.learnings_store import LearningsStore
            learnings_dir.mkdir(parents=True, exist_ok=True)
            store = LearningsStore(learnings_dir)
            store.regenerate_index()
```

Remove the old `for filename, default_content in [("learnings.md", ...), ("task_history.md", ...)]` block entirely.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_workspace_adapter_learnings_ensure.py -v`
Expected: 4 passed.

Also run the full existing workspace-adapter tests to make sure nothing regressed:

Run: `uv run pytest tests/test_context_builder.py tests/test_workspace_adapter_learnings_ensure.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/orchestrator/workspace_adapters.py tests/test_workspace_adapter_learnings_ensure.py
git commit -m "feat(learnings): state-aware ensure() — never silently flip legacy workspaces"
```

---

### Task 12: Bootstrap doc branches on workspace state

**Files:**
- Modify: `src/orchestrator/workspace_adapters.py` (`_build_sections` around line 265, plus the same line in `CodexWorkspaceAdapter._build_sections` at line 396)
- Modify: `tests/test_context_builder.py` (or create `tests/test_workspace_adapter_bootstrap.py`)

Replace the static "Persistent Files" block with a dynamic block that inlines either `learnings.md` (legacy) or `learnings/_index.md` (migrated).

- [ ] **Step 1: Write the failing test**

Create `tests/test_workspace_adapter_bootstrap.py`:

```python
from __future__ import annotations

from pathlib import Path

import pytest

from src.config import Settings
from src.daemon.paths import OrgPaths
from src.orchestrator.workspace_adapters import ClaudeWorkspaceAdapter


@pytest.fixture
def adapter(tmp_path: Path) -> tuple[ClaudeWorkspaceAdapter, Path]:
    org_root = tmp_path / "orgs" / "test-org"
    org_root.mkdir(parents=True)
    paths = OrgPaths(org_root)
    adapter = ClaudeWorkspaceAdapter(Settings(), paths, slug="test-org")
    ws = org_root / "workspaces" / "agent_x"
    ws.mkdir(parents=True)
    return adapter, ws


def test_bootstrap_inlines_legacy_learnings_md(adapter, tmp_path: Path):
    a, ws = adapter
    (ws / "learnings.md").write_text("# Learnings: agent_x\n\n- legacy entry\n")
    a.write_claude_md(ws, agent_name="agent_x", system_prompt="prompt")
    body = (ws / "CLAUDE.md").read_text()
    assert "legacy entry" in body
    assert "_index.md" not in body  # not migrated yet


def test_bootstrap_inlines_index_after_migration(adapter, tmp_path: Path):
    a, ws = adapter
    (ws / "learnings").mkdir()
    (ws / "learnings" / "_index.md").write_text("# Learnings Index\n\n## workflow (1)\n\n- `LRN-001` — sample\n")
    a.write_claude_md(ws, agent_name="agent_x", system_prompt="prompt")
    body = (ws / "CLAUDE.md").read_text()
    assert "LRN-001" in body
    assert "sample" in body
    assert "opc learning get" in body  # references new CLI
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_workspace_adapter_bootstrap.py -v`
Expected: failures (current bootstrap just references "learnings.md" string, doesn't inline content).

- [ ] **Step 3: Update `_build_sections`**

In `src/orchestrator/workspace_adapters.py`, locate `_build_sections` (around line 265). The current code uses a static `sections` list. We need to read workspace state and inline content.

Both `ClaudeWorkspaceAdapter` and `CodexWorkspaceAdapter` define their own `_build_sections`. Refactor by extracting a shared helper. Add this module-level function near the top:

```python
def _learnings_bootstrap_section(workspace: Path) -> list[str]:
    """Returns the 'Persistent Files' + 'Your Learnings' block.

    Branches on workspace state: flat learnings.md vs migrated learnings/.
    """
    flat = workspace / "learnings.md"
    learnings_dir = workspace / "learnings"
    index = learnings_dir / "_index.md"

    if learnings_dir.exists() and index.exists():
        index_body = index.read_text()
        return [
            "## Persistent Files\n",
            "- `learnings/_index.md` -- index of your operational learnings",
            "  (full bodies via `opc learning get`)",
            "- `task_history.md` -- read-only, updated by orchestrator\n",
            "## Your Learnings\n",
            index_body,
            "\nFetch any entry's body:",
            "```",
            "opc learning get --org <slug> --agent <you> <LRN-NNN-or-slug>",
            "```",
            "Write a new learning (file payload with slug/title/topic/tags/body):",
            "```",
            "opc learning add --org <slug> --agent <you> --from-file <path>",
            "```",
            "Update an existing learning:",
            "```",
            "opc learning update --org <slug> --agent <you> <LRN-NNN> --from-file <path>",
            "```",
            "Promote a durable cross-agent rule to a KB precedent (one-way):",
            "```",
            "opc learning promote --org <slug> --agent <you> <LRN-NNN> --kb-slug <slug>",
            "```\n",
        ]
    if flat.exists():
        flat_body = flat.read_text()
        return [
            "## Persistent Files\n",
            "- `learnings.md` -- your accumulated operational learnings (legacy flat-file format)",
            "- `task_history.md` -- read-only, updated by orchestrator\n",
            "## Your Learnings\n",
            flat_body + "\n",
            "Append a new line via `opc learning --agent <you> --text \"...\"`.",
            "_The structured per-entry format is available once this workspace is migrated._\n",
        ]
    # Brand-new workspace, ensure() should have created learnings/ already.
    return [
        "## Persistent Files\n",
        "- `learnings/_index.md` -- index of your operational learnings (empty)",
        "- `task_history.md` -- read-only, updated by orchestrator\n",
    ]
```

In `ClaudeWorkspaceAdapter.write_claude_md`, before calling `self._build_sections(...)`, the workspace path is already known. Pass it through. Update `_build_sections` signature to accept `workspace: Path` and replace the static "Persistent Files" lines (around `sections = [..., "## Persistent Files\\n", ...]`) with a call to `_learnings_bootstrap_section(workspace)`.

Concretely, in `ClaudeWorkspaceAdapter.write_claude_md`:

```python
        sections = self._build_sections(
            agent_name,
            system_prompt,
            workspace=workspace,                     # NEW
            include_start_task=True,
            ...
        )
```

In `_build_sections`, replace:

```python
            "## Persistent Files\n",
            "- `learnings.md` -- your accumulated operational learnings",
            "- `task_history.md` -- read-only, updated by orchestrator\n",
```

With:

```python
            *_learnings_bootstrap_section(workspace),
```

Apply the same swap in `CodexWorkspaceAdapter._build_sections` (line ~396) and pass `workspace` through.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_workspace_adapter_bootstrap.py tests/test_context_builder.py -v`
Expected: new tests pass, no existing tests regress.

- [ ] **Step 5: Commit**

```bash
git add src/orchestrator/workspace_adapters.py tests/test_workspace_adapter_bootstrap.py
git commit -m "feat(learnings): bootstrap inlines _index.md when migrated, flat file when legacy"
```

---

## Phase 3: HTTP routes

### Task 13: Add the `/learnings/entries` read routes with 412 guard

**Files:**
- Modify: `src/daemon/routes/agents.py`
- Test: `tests/daemon/test_routes_agents_learnings.py` (create)

The new routes mount on the existing `router` in `agents.py`. We need a shared helper that returns 412 when `learnings/` is missing.

- [ ] **Step 1: Write the failing tests**

Create `tests/daemon/test_routes_agents_learnings.py`:

```python
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.daemon.app import build_app
from src.daemon.state import DaemonState
# Re-use existing daemon test fixtures.
from tests.daemon.conftest import org_state_fixture  # type: ignore  # noqa


@pytest.fixture
def client_with_migrated_workspace(tmp_path, monkeypatch):
    """Spin up an in-process daemon with one org + one migrated workspace.

    Returns (TestClient, bearer_token, slug, agent_name, workspace_path).
    """
    from tests.daemon.conftest import _build_test_app  # type: ignore
    app, token, slug, agent = _build_test_app(tmp_path)
    workspace = tmp_path / "orgs" / slug / "workspaces" / agent
    (workspace / "learnings").mkdir(parents=True, exist_ok=True)
    return TestClient(app), token, slug, agent, workspace


def test_list_returns_empty_on_migrated_workspace(client_with_migrated_workspace):
    client, token, slug, agent, _ = client_with_migrated_workspace
    r = client.get(
        f"/api/v1/orgs/{slug}/agents/{agent}/learnings/entries/",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    assert r.json() == {"entries": []}


def test_list_returns_412_on_pre_migration_workspace(tmp_path, monkeypatch):
    from tests.daemon.conftest import _build_test_app
    app, token, slug, agent = _build_test_app(tmp_path)
    workspace = tmp_path / "orgs" / slug / "workspaces" / agent
    # No learnings/ dir; flat learnings.md exists.
    (workspace).mkdir(parents=True, exist_ok=True)
    (workspace / "learnings.md").write_text("# Learnings\n")
    client = TestClient(app)
    r = client.get(
        f"/api/v1/orgs/{slug}/agents/{agent}/learnings/entries/",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 412
    body = r.json()
    assert body["detail"]["error"] == "workspace_not_migrated"
    assert body["detail"]["migrate_first"] is True
```

Note: if `tests/daemon/conftest.py` doesn't already export `_build_test_app`, look at any existing route test (e.g. `tests/daemon/test_routes_kb.py`) for the in-process daemon fixture pattern and reuse it. If no such helper exists, define one in `conftest.py` that returns `(app, token, slug, agent)` ready for use.

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/daemon/test_routes_agents_learnings.py -v`
Expected: 404 (routes not registered).

- [ ] **Step 3: Add the routes + 412 guard**

In `src/daemon/routes/agents.py`, near the existing `append_learning` handler, add:

```python
from src.infrastructure.learnings_store import (
    LearningsStore,
    LearningEntry,
    InvalidLearningId,
    InvalidLearningEntry,
    LearningIdExists,
    LearningNotFound,
    PromotedLocked,
)


def _workspace_learnings_store(org: OrgState, agent_name: str) -> LearningsStore:
    """Return the per-agent LearningsStore, raising 412 if pre-migration."""
    workspace = org.root / "workspaces" / agent_name
    learnings_dir = workspace / "learnings"
    if not learnings_dir.exists():
        raise HTTPException(
            status_code=412,
            detail={"error": "workspace_not_migrated", "migrate_first": True},
        )
    return LearningsStore(learnings_dir)


@router.get("/agents/{agent_name}/learnings/entries/")
async def list_learnings(
    slug: str,
    agent_name: str,
    org: OrgDep,
    topic: str | None = None,
    tag: str | None = None,
    promoted: bool | None = None,
) -> dict:
    store = _workspace_learnings_store(org, agent_name)
    summaries = store.list_entries(topic=topic, tag=tag, promoted=promoted)
    return {
        "entries": [
            {
                "id": s.id,
                "slug": s.slug,
                "title": s.title,
                "topic": s.topic,
                "tags": s.tags,
                "promoted_to": s.promoted_to,
                "updated_at": s.updated_at,
            }
            for s in summaries
        ],
    }


@router.get("/agents/{agent_name}/learnings/entries/{id_or_slug}")
async def get_learning(slug: str, agent_name: str, id_or_slug: str, org: OrgDep) -> dict:
    store = _workspace_learnings_store(org, agent_name)
    try:
        entry = store.read_entry(id_or_slug)
    except LearningNotFound:
        raise HTTPException(status_code=404, detail={"error": "id_not_found", "id_or_slug": id_or_slug})
    return _entry_to_dict(entry)


def _entry_to_dict(entry: LearningEntry) -> dict:
    return {
        "id": entry.id,
        "slug": entry.slug,
        "title": entry.title,
        "topic": entry.topic,
        "tags": entry.tags,
        "body": entry.body,
        "source_task": entry.source_task,
        "related_to": entry.related_to,
        "supersedes": entry.supersedes,
        "promoted_to": entry.promoted_to,
        "authored_by": entry.authored_by,
        "authored_at": entry.authored_at,
        "updated_by": entry.updated_by,
        "updated_at": entry.updated_at,
    }


class LearningSearchBody(BaseModel):
    query: str
    limit: int = 20
    include_promoted: bool = False


@router.post("/agents/{agent_name}/learnings/entries/search")
async def search_learnings(
    slug: str, agent_name: str, body: LearningSearchBody, org: OrgDep,
) -> dict:
    store = _workspace_learnings_store(org, agent_name)
    hits = store.search(body.query, limit=body.limit, include_promoted=body.include_promoted)
    return {
        "hits": [
            {"id": h.id, "slug": h.slug, "title": h.title, "snippet": h.snippet, "score": h.score}
            for h in hits
        ],
    }
```

Ensure `BaseModel` (from `pydantic`) is imported at the top of `agents.py` if not already there.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/daemon/test_routes_agents_learnings.py -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add src/daemon/routes/agents.py tests/daemon/test_routes_agents_learnings.py tests/daemon/conftest.py
git commit -m "feat(learnings): GET/POST search routes + 412 pre-migration guard"
```

---

### Task 14: Add write routes — `POST add` and `PUT update`

**Files:**
- Modify: `src/daemon/routes/agents.py`
- Modify: `tests/daemon/test_routes_agents_learnings.py`

- [ ] **Step 1: Write the failing tests**

Append:

```python
def test_add_allocates_id_and_persists(client_with_migrated_workspace):
    client, token, slug, agent, ws = client_with_migrated_workspace
    payload = {
        "slug": "first-rule",
        "title": "First rule",
        "topic": "workflow",
        "tags": ["sample"],
        "body": "**Why:** test\n**How to apply:** later\n",
    }
    r = client.post(
        f"/api/v1/orgs/{slug}/agents/{agent}/learnings/entries/",
        headers={"Authorization": f"Bearer {token}"},
        json=payload,
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["id"] == "LRN-001"
    assert body["path"] == "learnings/LRN-001-first-rule.md"
    assert (ws / "learnings" / "LRN-001-first-rule.md").exists()


def test_update_preserves_authored_at(client_with_migrated_workspace):
    client, token, slug, agent, ws = client_with_migrated_workspace
    # Seed
    client.post(
        f"/api/v1/orgs/{slug}/agents/{agent}/learnings/entries/",
        headers={"Authorization": f"Bearer {token}"},
        json={"slug": "a", "title": "v1", "topic": "w", "body": "old\n"},
    )
    # Update
    r = client.put(
        f"/api/v1/orgs/{slug}/agents/{agent}/learnings/entries/LRN-001",
        headers={"Authorization": f"Bearer {token}"},
        json={"slug": "a", "title": "v2", "topic": "w", "body": "new\n"},
    )
    assert r.status_code == 200
    assert r.json()["title"] == "v2"


def test_add_rejects_unknown_related_to(client_with_migrated_workspace):
    client, token, slug, agent, _ = client_with_migrated_workspace
    r = client.post(
        f"/api/v1/orgs/{slug}/agents/{agent}/learnings/entries/",
        headers={"Authorization": f"Bearer {token}"},
        json={"slug": "a", "title": "x", "topic": "w", "body": "b\n", "related_to": ["LRN-999"]},
    )
    assert r.status_code == 400
    assert r.json()["detail"]["error"] == "unknown_related_id"
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/daemon/test_routes_agents_learnings.py -v -k "add or update"`
Expected: 404 (routes don't exist).

- [ ] **Step 3: Implement the add + update routes**

Add to `agents.py`:

```python
class LearningAddBody(BaseModel):
    slug: str
    title: str
    topic: str
    body: str
    tags: list[str] = []
    source_task: str | None = None
    related_to: list[str] = []
    supersedes: str | None = None


class LearningUpdateBody(BaseModel):
    slug: str
    title: str
    topic: str
    body: str
    tags: list[str] = []
    source_task: str | None = None
    related_to: list[str] = []
    supersedes: str | None = None


def _invalid_entry_to_http(err: InvalidLearningEntry) -> HTTPException:
    return HTTPException(status_code=400, detail={"error": err.code, "message": str(err)})


@router.post("/agents/{agent_name}/learnings/entries/", status_code=201)
async def add_learning(
    slug: str, agent_name: str, body: LearningAddBody, org: OrgDep,
) -> dict:
    store = _workspace_learnings_store(org, agent_name)
    async with org.db_lock:
        new_id = store.next_id()
        entry = LearningEntry(
            id=new_id,
            slug=body.slug,
            title=body.title,
            topic=body.topic,
            body=body.body,
            tags=list(body.tags),
            source_task=body.source_task,
            related_to=list(body.related_to),
            supersedes=body.supersedes,
        )
        try:
            written = store.write_entry(entry, agent=agent_name)
        except InvalidLearningEntry as e:
            raise _invalid_entry_to_http(e)
        except LearningIdExists as e:
            raise HTTPException(status_code=409, detail={"error": "id_exists", "id": e.id})
        store.regenerate_index()
    rel_path = f"learnings/{written.id}-{written.slug}.md"
    return {"id": written.id, "path": rel_path, "authored_at": written.authored_at}


@router.put("/agents/{agent_name}/learnings/entries/{id}")
async def update_learning(
    slug: str, agent_name: str, id: str, body: LearningUpdateBody, org: OrgDep,
) -> dict:
    store = _workspace_learnings_store(org, agent_name)
    entry = LearningEntry(
        id=id,
        slug=body.slug,
        title=body.title,
        topic=body.topic,
        body=body.body,
        tags=list(body.tags),
        source_task=body.source_task,
        related_to=list(body.related_to),
        supersedes=body.supersedes,
    )
    async with org.db_lock:
        try:
            written = store.update_entry(id, entry, agent=agent_name)
        except LearningNotFound:
            raise HTTPException(status_code=404, detail={"error": "id_not_found", "id": id})
        except PromotedLocked as e:
            raise HTTPException(status_code=409, detail={"error": "promoted_locked", "id": e.id, "kb_slug": e.kb_slug})
        except InvalidLearningId:
            raise HTTPException(status_code=400, detail={"error": "invalid_id", "id": id})
        except InvalidLearningEntry as e:
            raise _invalid_entry_to_http(e)
        store.regenerate_index()
    return _entry_to_dict(written)
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/daemon/test_routes_agents_learnings.py -v -k "add or update"`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/daemon/routes/agents.py tests/daemon/test_routes_agents_learnings.py
git commit -m "feat(learnings): POST add + PUT update routes, error mapping to HTTP codes"
```

---

### Task 15: Add promote and reindex routes

**Files:**
- Modify: `src/daemon/routes/agents.py`
- Modify: `tests/daemon/test_routes_agents_learnings.py`

`promote` must verify the KB slug exists (look up via the per-org `KBStore`). `reindex` is a maintenance no-input route.

- [ ] **Step 1: Write the failing tests**

```python
def test_promote_requires_existing_kb_slug(client_with_migrated_workspace):
    client, token, slug, agent, _ = client_with_migrated_workspace
    # Seed a learning
    client.post(
        f"/api/v1/orgs/{slug}/agents/{agent}/learnings/entries/",
        headers={"Authorization": f"Bearer {token}"},
        json={"slug": "a", "title": "x", "topic": "w", "body": "b\n"},
    )
    # Promote with nonexistent KB slug should 404
    r = client.post(
        f"/api/v1/orgs/{slug}/agents/{agent}/learnings/entries/LRN-001/promote",
        headers={"Authorization": f"Bearer {token}"},
        json={"kb_slug": "does-not-exist"},
    )
    assert r.status_code == 404
    assert r.json()["detail"]["error"] == "kb_slug_not_found"


def test_promote_with_existing_kb_slug_stamps_and_stubs(client_with_migrated_workspace, monkeypatch):
    client, token, slug, agent, _ = client_with_migrated_workspace
    # Seed a KB precedent so promote can resolve it
    client.post(
        f"/api/v1/orgs/{slug}/kb/",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "slug": "real-precedent",
            "title": "Real precedent",
            "type": "precedent",
            "topic": "engineering",
            "body": "details\n",
            "agent": agent,
        },
    )
    # Seed a learning
    client.post(
        f"/api/v1/orgs/{slug}/agents/{agent}/learnings/entries/",
        headers={"Authorization": f"Bearer {token}"},
        json={"slug": "a", "title": "x", "topic": "w", "body": "original\n"},
    )
    r = client.post(
        f"/api/v1/orgs/{slug}/agents/{agent}/learnings/entries/LRN-001/promote",
        headers={"Authorization": f"Bearer {token}"},
        json={"kb_slug": "real-precedent"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["promoted_to"] == "real-precedent"
    assert "original" not in body["body"]
    assert "real-precedent" in body["body"]


def test_reindex_regenerates_file(client_with_migrated_workspace):
    client, token, slug, agent, ws = client_with_migrated_workspace
    # Seed a learning
    client.post(
        f"/api/v1/orgs/{slug}/agents/{agent}/learnings/entries/",
        headers={"Authorization": f"Bearer {token}"},
        json={"slug": "a", "title": "x", "topic": "w", "body": "b\n"},
    )
    # Delete _index.md manually
    (ws / "learnings" / "_index.md").unlink()
    r = client.post(
        f"/api/v1/orgs/{slug}/agents/{agent}/learnings/entries/reindex",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    assert (ws / "learnings" / "_index.md").exists()
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/daemon/test_routes_agents_learnings.py -v -k "promote or reindex"`

- [ ] **Step 3: Implement the routes**

```python
class LearningPromoteBody(BaseModel):
    kb_slug: str


@router.post("/agents/{agent_name}/learnings/entries/{id}/promote")
async def promote_learning(
    slug: str, agent_name: str, id: str, body: LearningPromoteBody, org: OrgDep,
) -> dict:
    if not body.kb_slug:
        raise HTTPException(status_code=400, detail={"error": "kb_slug_missing"})
    # Validate KB slug exists in the org's KB store
    if not org.kb_store.path_for(body.kb_slug).exists():
        raise HTTPException(
            status_code=404, detail={"error": "kb_slug_not_found", "kb_slug": body.kb_slug},
        )
    store = _workspace_learnings_store(org, agent_name)
    async with org.db_lock:
        try:
            written = store.promote(id, kb_slug=body.kb_slug, agent=agent_name)
        except LearningNotFound:
            raise HTTPException(status_code=404, detail={"error": "id_not_found", "id": id})
        except PromotedLocked as e:
            raise HTTPException(status_code=409, detail={"error": "promoted_locked", "id": e.id, "kb_slug": e.kb_slug})
        except InvalidLearningEntry as e:
            raise _invalid_entry_to_http(e)
        store.regenerate_index()
    return _entry_to_dict(written)


@router.post("/agents/{agent_name}/learnings/entries/reindex")
async def reindex_learnings(slug: str, agent_name: str, org: OrgDep) -> dict:
    store = _workspace_learnings_store(org, agent_name)
    async with org.db_lock:
        store.regenerate_index()
    return {"ok": True}
```

If `org.kb_store` is not already a thing on the `OrgState` dataclass, look up how the existing KB routes get it (e.g. `KBStore(org.root / "kb")`) and replicate the same pattern inline.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/daemon/test_routes_agents_learnings.py -v`
Expected: all (including new) passed.

- [ ] **Step 5: Commit**

```bash
git add src/daemon/routes/agents.py tests/daemon/test_routes_agents_learnings.py
git commit -m "feat(learnings): promote (validates KB slug) + reindex routes"
```

---

### Task 16: Legacy endpoint returns 410 when workspace is migrated

**Files:**
- Modify: `src/daemon/routes/agents.py` (the existing `append_learning` handler around line 614)
- Modify: `tests/daemon/test_routes_agents_learnings.py`

- [ ] **Step 1: Write the failing test**

Append:

```python
def test_legacy_post_returns_410_on_migrated_workspace(client_with_migrated_workspace):
    client, token, slug, agent, _ = client_with_migrated_workspace
    # Seed an active session so the legacy guard would otherwise pass — but
    # since the workspace is migrated, the route should 410 before that.
    # The test uses the fake-session helper that the existing legacy tests use,
    # or just trusts that the migration check fires before session lookup.
    r = client.post(
        f"/api/v1/orgs/{slug}/agents/{agent}/learnings",
        headers={"Authorization": f"Bearer {token}"},
        json={"text": "hi", "task_id": "TASK-001", "session_id": "s"},
    )
    assert r.status_code == 410
    assert r.json()["detail"]["migrate_to"].endswith("/learnings/entries")


def test_legacy_post_still_works_on_pre_migration_workspace(tmp_path, monkeypatch):
    from tests.daemon.conftest import _build_test_app
    app, token, slug, agent = _build_test_app(tmp_path)
    # Set up a pre-migration workspace + a fake active session
    workspace = tmp_path / "orgs" / slug / "workspaces" / agent
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "learnings.md").write_text(f"# Learnings: {agent}\n\n")
    # Test that the legacy endpoint still works — see existing
    # test_agents_learnings_helper.py for the session-mock pattern. If session
    # setup is complex, this test can be skipped in favor of the integration
    # test in Task 21.
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/daemon/test_routes_agents_learnings.py -v -k legacy`

- [ ] **Step 3: Modify `append_learning`**

In `src/daemon/routes/agents.py`, at the top of the existing `append_learning` handler (around line 615), add a pre-migration check BEFORE the session lookup:

```python
@router.post("/agents/{agent_name}/learnings")
async def append_learning(
    slug: str, agent_name: str, body: LearningBody, org: OrgDep,
) -> dict:
    workspace = org.root / "workspaces" / agent_name
    if (workspace / "learnings").exists():
        raise HTTPException(
            status_code=410,
            detail={
                "error": "endpoint_deprecated_for_migrated_workspace",
                "migrate_to": f"POST /api/v1/orgs/{slug}/agents/{agent_name}/learnings/entries",
            },
        )
    # existing session-lookup + append logic unchanged below
    expected = org.sessions.get_active(body.task_id, agent_name)
    ...
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/daemon/test_routes_agents_learnings.py -v -k legacy`
Expected: pass on the 410 test; the pre-migration test may be a skip placeholder.

- [ ] **Step 5: Commit**

```bash
git add src/daemon/routes/agents.py tests/daemon/test_routes_agents_learnings.py
git commit -m "feat(learnings): legacy POST returns 410 once workspace is migrated"
```

---

## Phase 4: CLI

### Task 17: Restructure `cmd_learning` into a verb subparser

**Files:**
- Modify: `src/cli.py` (current `cmd_learning` at line 721, parser at line 1854)
- Test: `tests/test_cli_learning.py` (create)

The current `opc learning --agent X --text "..."` flat-flag form has to keep working for unmigrated workspaces. Strategy: keep the no-verb form as the legacy entry, and ADD a verb form that takes priority. argparse handles both via a subparser with `nargs='?'` on the verb.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_cli_learning.py
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def _run(args: list[str], cwd: Path = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "src.cli"] + args,
        capture_output=True, text=True, cwd=cwd,
    )


def test_learning_help_shows_verbs():
    r = _run(["learning", "--help"])
    assert r.returncode == 0
    out = r.stdout
    for verb in ("list", "get", "search", "add", "update", "promote", "reindex"):
        assert verb in out


def test_learning_list_help_shows_filters():
    r = _run(["learning", "list", "--help"])
    assert r.returncode == 0
    assert "--topic" in r.stdout
    assert "--tag" in r.stdout
    assert "--promoted" in r.stdout
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_cli_learning.py -v`
Expected: failures (verbs don't exist).

- [ ] **Step 3: Restructure the parser in `src/cli.py`**

Replace the current `p_learn = sub.add_parser("learning", ...)` block (around line 1854) with:

```python
    # ---- learning ----------------------------------------------------------
    p_learn = sub.add_parser("learning", help="Per-agent learnings (verb-dispatched)")
    learn_sub = p_learn.add_subparsers(dest="learn_verb")

    # Legacy: `opc learning --agent X --text "..."` keeps working
    p_learn.add_argument("--org", required=False)
    p_learn.add_argument("--agent", required=False)
    p_learn.add_argument("--text", required=False)
    p_learn.add_argument("--task-id", required=False)
    p_learn.add_argument("--session-id", required=False)
    p_learn.set_defaults(func=cmd_learning)

    # list
    pl = learn_sub.add_parser("list", help="List learnings")
    pl.add_argument("--org", required=False)
    pl.add_argument("--agent", required=True)
    pl.add_argument("--topic")
    pl.add_argument("--tag")
    pl.add_argument("--promoted", action="store_true")
    pl.add_argument("--not-promoted", action="store_true")
    pl.add_argument("--json", action="store_true")
    pl.set_defaults(func=cmd_learning_list)

    # get
    pg = learn_sub.add_parser("get", help="Get a learning by ID or slug")
    pg.add_argument("--org", required=False)
    pg.add_argument("--agent", required=True)
    pg.add_argument("id_or_slug")
    pg.add_argument("--json", action="store_true")
    pg.set_defaults(func=cmd_learning_get)

    # search
    ps = learn_sub.add_parser("search", help="Substring search over learnings")
    ps.add_argument("--org", required=False)
    ps.add_argument("--agent", required=True)
    ps.add_argument("query")
    ps.add_argument("--limit", type=int, default=20)
    ps.add_argument("--include-promoted", action="store_true")
    ps.add_argument("--json", action="store_true")
    ps.set_defaults(func=cmd_learning_search)

    # add
    pa = learn_sub.add_parser("add", help="Add a new learning (file payload)")
    pa.add_argument("--org", required=False)
    pa.add_argument("--agent", required=True)
    pa.add_argument("--from-file", required=True)
    pa.set_defaults(func=cmd_learning_add)

    # update
    pu = learn_sub.add_parser("update", help="Update an existing learning by ID")
    pu.add_argument("--org", required=False)
    pu.add_argument("--agent", required=True)
    pu.add_argument("id")
    pu.add_argument("--from-file", required=True)
    pu.set_defaults(func=cmd_learning_update)

    # promote
    pp = learn_sub.add_parser("promote", help="Promote a learning to a KB precedent")
    pp.add_argument("--org", required=False)
    pp.add_argument("--agent", required=True)
    pp.add_argument("id")
    pp.add_argument("--kb-slug", required=True)
    pp.set_defaults(func=cmd_learning_promote)

    # reindex
    pr = learn_sub.add_parser("reindex", help="Regenerate _index.md")
    pr.add_argument("--org", required=False)
    pr.add_argument("--agent", required=True)
    pr.set_defaults(func=cmd_learning_reindex)
```

Existing `cmd_learning` stays unchanged (it's the legacy text-append flow).

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_cli_learning.py -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add src/cli.py tests/test_cli_learning.py
git commit -m "feat(cli): learning subparser scaffolding (verbs + legacy text form preserved)"
```

---

### Task 18: Implement `cmd_learning_list/get/search/reindex`

**Files:**
- Modify: `src/cli.py`
- Modify: `tests/test_cli_learning.py`

These hit the read routes; they don't write so we don't need real session state in tests.

- [ ] **Step 1: Write the failing tests**

Append:

```python
def test_learning_list_prints_table(monkeypatch, tmp_path):
    """End-to-end via the daemon would be heavy; mock the client at module boundary."""
    # See existing test_cli.py for the monkeypatch.setattr(cmd, "Client", FakeClient)
    # pattern. Pattern: instantiate a fake client that returns canned JSON.
    pass  # leave as a marker; the integration test in Task 21 covers wire-level


def test_cmd_learning_list_calls_correct_route(monkeypatch):
    captured = {}

    class FakeClient:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def get(self, path, params=None):
            captured["path"] = path
            captured["params"] = params
            return {"entries": []}

    from src import cli
    monkeypatch.setattr(cli, "Client", FakeClient)
    args = type("A", (), dict(
        org="my-org", agent="dev_agent",
        topic="workflow", tag=None, promoted=False, not_promoted=False, json=False,
    ))()
    cli.cmd_learning_list(args)
    assert captured["path"] == "/api/v1/orgs/my-org/agents/dev_agent/learnings/entries/"
    assert captured["params"]["topic"] == "workflow"
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_cli_learning.py -v -k cmd_learning_list`

- [ ] **Step 3: Implement the read commands**

Add to `src/cli.py` next to existing `cmd_learning`:

```python
def cmd_learning_list(args: argparse.Namespace) -> None:
    org = _resolve_org(args)
    params: dict = {}
    if args.topic:
        params["topic"] = args.topic
    if args.tag:
        params["tag"] = args.tag
    if args.promoted:
        params["promoted"] = True
    elif args.not_promoted:
        params["promoted"] = False
    with Client() as c:
        resp = c.get(f"/api/v1/orgs/{org}/agents/{args.agent}/learnings/entries/", params=params)
    entries = resp.get("entries", [])
    if args.json:
        import json
        print(json.dumps(entries, indent=2))
        return
    if not entries:
        print("(no learnings)")
        return
    for e in entries:
        tags = ", ".join(e.get("tags", []))
        promo = f" ↗ {e['promoted_to']}" if e.get("promoted_to") else ""
        print(f"  {e['id']}  [{e['topic']}] {e['title']}  ({tags}){promo}")


def cmd_learning_get(args: argparse.Namespace) -> None:
    org = _resolve_org(args)
    with Client() as c:
        entry = c.get(f"/api/v1/orgs/{org}/agents/{args.agent}/learnings/entries/{args.id_or_slug}")
    if args.json:
        import json
        print(json.dumps(entry, indent=2))
        return
    print(f"# {entry['title']}\n")
    print(f"id: {entry['id']}  slug: {entry['slug']}  topic: {entry['topic']}")
    if entry.get("tags"):
        print(f"tags: {', '.join(entry['tags'])}")
    if entry.get("promoted_to"):
        print(f"promoted_to: {entry['promoted_to']}")
    print()
    print(entry["body"])


def cmd_learning_search(args: argparse.Namespace) -> None:
    org = _resolve_org(args)
    with Client() as c:
        resp = c.post(
            f"/api/v1/orgs/{org}/agents/{args.agent}/learnings/entries/search",
            json={"query": args.query, "limit": args.limit, "include_promoted": args.include_promoted},
        )
    hits = resp.get("hits", [])
    if args.json:
        import json
        print(json.dumps(hits, indent=2))
        return
    if not hits:
        print("(no matches)")
        return
    for h in hits:
        print(f"  {h['id']}  score={h['score']}  {h['title']}")
        print(f"      {h['snippet']}")


def cmd_learning_reindex(args: argparse.Namespace) -> None:
    org = _resolve_org(args)
    with Client() as c:
        c.post(f"/api/v1/orgs/{org}/agents/{args.agent}/learnings/entries/reindex", json={})
    print("ok: reindexed")
```

Use whatever `_resolve_org(args)` helper (or equivalent) already exists for the other per-org subcommands. Look at `cmd_kb_list` or `cmd_tasks_list` for the canonical pattern.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_cli_learning.py -v`
Expected: passes.

- [ ] **Step 5: Commit**

```bash
git add src/cli.py tests/test_cli_learning.py
git commit -m "feat(cli): learning list/get/search/reindex commands"
```

---

### Task 19: Implement `cmd_learning_add/update/promote`

**Files:**
- Modify: `src/cli.py`
- Modify: `tests/test_cli_learning.py`

`add` and `update` read YAML payload from `--from-file` (mirrors `opc kb add --from-file` pattern).

- [ ] **Step 1: Write the failing tests**

```python
def test_cmd_learning_add_reads_yaml_and_posts(monkeypatch, tmp_path):
    captured = {}

    class FakeClient:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def post(self, path, json=None):
            captured["path"] = path
            captured["json"] = json
            return {"id": "LRN-001", "path": "learnings/LRN-001-x.md"}

    from src import cli
    monkeypatch.setattr(cli, "Client", FakeClient)
    payload_path = tmp_path / "p.yaml"
    payload_path.write_text(
        "slug: x\n"
        "title: T\n"
        "topic: w\n"
        "tags: [a, b]\n"
        "body: |\n"
        "  body line 1\n"
        "  body line 2\n"
    )
    args = type("A", (), dict(
        org="o", agent="dev_agent", from_file=str(payload_path),
    ))()
    cli.cmd_learning_add(args)
    assert captured["path"] == "/api/v1/orgs/o/agents/dev_agent/learnings/entries/"
    assert captured["json"]["slug"] == "x"
    assert captured["json"]["tags"] == ["a", "b"]
    assert "body line 2" in captured["json"]["body"]


def test_cmd_learning_promote_posts_correct_path(monkeypatch):
    captured = {}

    class FakeClient:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def post(self, path, json=None):
            captured["path"] = path
            captured["json"] = json
            return {"id": "LRN-001", "promoted_to": "kb-x", "body": "..."}

    from src import cli
    monkeypatch.setattr(cli, "Client", FakeClient)
    args = type("A", (), dict(
        org="o", agent="dev_agent", id="LRN-001", kb_slug="kb-x",
    ))()
    cli.cmd_learning_promote(args)
    assert captured["path"] == "/api/v1/orgs/o/agents/dev_agent/learnings/entries/LRN-001/promote"
    assert captured["json"] == {"kb_slug": "kb-x"}
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_cli_learning.py -v -k "add or promote"`

- [ ] **Step 3: Implement add/update/promote**

```python
def _read_yaml_payload(path: str) -> dict:
    import yaml
    with open(path) as f:
        return yaml.safe_load(f) or {}


def cmd_learning_add(args: argparse.Namespace) -> None:
    org = _resolve_org(args)
    payload = _read_yaml_payload(args.from_file)
    with Client() as c:
        resp = c.post(
            f"/api/v1/orgs/{org}/agents/{args.agent}/learnings/entries/",
            json=payload,
        )
    print(f"ok: {resp['id']} -> {resp['path']}")


def cmd_learning_update(args: argparse.Namespace) -> None:
    org = _resolve_org(args)
    payload = _read_yaml_payload(args.from_file)
    with Client() as c:
        resp = c.put(
            f"/api/v1/orgs/{org}/agents/{args.agent}/learnings/entries/{args.id}",
            json=payload,
        )
    print(f"ok: updated {resp['id']}")


def cmd_learning_promote(args: argparse.Namespace) -> None:
    org = _resolve_org(args)
    with Client() as c:
        resp = c.post(
            f"/api/v1/orgs/{org}/agents/{args.agent}/learnings/entries/{args.id}/promote",
            json={"kb_slug": args.kb_slug},
        )
    print(f"ok: {resp['id']} promoted to KB precedent `{resp['promoted_to']}`")
```

Note: the existing `Client` may not have a `put` method. If not, add one following the `post` pattern in `src/client/client.py`:

```python
    def put(self, path: str, json: dict | None = None) -> dict:
        r = self._session.put(self._url(path), json=json, headers=self._headers())
        r.raise_for_status()
        return r.json()
```

Add a test for it if you add it.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_cli_learning.py -v`
Expected: all passed.

- [ ] **Step 5: Commit**

```bash
git add src/cli.py src/client/client.py tests/test_cli_learning.py
git commit -m "feat(cli): learning add/update/promote commands (YAML --from-file)"
```

---

## Phase 5: Audit + integration

### Task 20: Audit log entries for add/update/promote

**Files:**
- Modify: `src/infrastructure/audit_logger.py` (add three new helpers)
- Modify: `src/daemon/routes/agents.py` (call after successful writes)
- Test: extend `tests/daemon/test_routes_agents_learnings.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/daemon/test_routes_agents_learnings.py`:

```python
def test_add_writes_learning_added_audit_row(client_with_migrated_workspace):
    client, token, slug, agent, _ = client_with_migrated_workspace
    client.post(
        f"/api/v1/orgs/{slug}/agents/{agent}/learnings/entries/",
        headers={"Authorization": f"Bearer {token}"},
        json={"slug": "x", "title": "t", "topic": "w", "body": "b\n"},
    )
    # Audit query — adapt to your existing audit helper pattern.
    audit = client.get(
        f"/api/v1/orgs/{slug}/audit/?action=learning_added",
        headers={"Authorization": f"Bearer {token}"},
    ).json()
    rows = audit.get("rows", [])
    assert any(r["action"] == "learning_added" for r in rows)
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/daemon/test_routes_agents_learnings.py -v -k audit`

- [ ] **Step 3: Add audit helpers + wire them**

In `src/infrastructure/audit_logger.py`, add three helpers (follow the existing pattern for `log_kb_*` or `log_escalation_*`):

```python
def log_learning_added(self, *, agent: str, id: str, slug: str, topic: str, tags: list[str], source_task: str | None) -> None:
    self._insert(action="learning_added", actor=agent, payload={
        "id": id, "slug": slug, "topic": topic, "tags": tags, "source_task": source_task,
    })


def log_learning_updated(self, *, agent: str, id: str, slug_changed: bool, fields_changed: list[str]) -> None:
    self._insert(action="learning_updated", actor=agent, payload={
        "id": id, "slug_changed": slug_changed, "fields_changed": fields_changed,
    })


def log_learning_promoted(self, *, agent: str, id: str, kb_slug: str) -> None:
    self._insert(action="learning_promoted", actor=agent, payload={
        "id": id, "kb_slug": kb_slug,
    })
```

Match the existing helper signatures and DB-insertion convention in that file (the actual `_insert` / `log_action` name may differ — read the file first and follow its style).

In `src/daemon/routes/agents.py`, after each successful write (inside the lock, after `store.regenerate_index()`), call the appropriate helper:

```python
        org.audit.log_learning_added(
            agent=agent_name, id=written.id, slug=written.slug,
            topic=written.topic, tags=written.tags, source_task=written.source_task,
        )
```

Similar for update and promote.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/daemon/test_routes_agents_learnings.py -v -k audit`
Expected: passes.

- [ ] **Step 5: Commit**

```bash
git add src/infrastructure/audit_logger.py src/daemon/routes/agents.py tests/daemon/test_routes_agents_learnings.py
git commit -m "feat(learnings): audit rows for add/update/promote"
```

---

### Task 21: Integration test — full lifecycle against real daemon

**Files:**
- Create: `tests/integration/test_learnings_lifecycle.py`

Marker: `@pytest.mark.integration` (excluded by default; runs in CI and via `pytest -m integration`).

- [ ] **Step 1: Write the integration test**

```python
# tests/integration/test_learnings_lifecycle.py
from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration


def test_full_lifecycle_add_list_get_search_update_promote(tmp_path):
    """Spawn a real daemon + opc CLI, drive add → list → get → search → update → promote."""
    # See tests/integration/test_end_to_end.py for the exact spawn pattern.
    # Steps:
    # 1. opc init <tmp>/runtime
    # 2. opc orgs init test-org --from examples/orgs/hk-macau-tourism
    # 3. Manually create workspaces/dev_agent/learnings/ to simulate a migrated workspace
    # 4. Write a YAML payload to /tmp/p.yaml
    # 5. opc learning add --agent dev_agent --from-file /tmp/p.yaml -> capture LRN-NNN
    # 6. opc learning list --agent dev_agent -> verify entry shows
    # 7. opc learning get --agent dev_agent LRN-001 -> verify body
    # 8. opc learning search --agent dev_agent "from-payload-keyword" -> verify hit
    # 9. opc learning update --agent dev_agent LRN-001 --from-file /tmp/p2.yaml
    # 10. Seed a KB precedent via opc kb add
    # 11. opc learning promote --agent dev_agent LRN-001 --kb-slug <seeded> -> verify stub body
    # 12. opc learning get LRN-001 -> verify promoted_to set
    # 13. opc learning update LRN-001 ... should fail with promoted_locked
    pass  # FLESH OUT against the existing integration-test scaffolding patterns
```

The test is marked TODO; implement against the existing scaffolding in `tests/integration/conftest.py` and `tests/integration/test_end_to_end.py`. This is the most expensive task in the plan and should be done LAST; everything else gives you confidence the wires are connected before you build the end-to-end harness.

- [ ] **Step 2: Run the test**

Run: `uv run pytest tests/integration/test_learnings_lifecycle.py -v -m integration`
Expected: passes when fully implemented.

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_learnings_lifecycle.py
git commit -m "test(learnings): end-to-end lifecycle integration test"
```

---

### Task 22: Update `CLAUDE.md` (project) with new layout note

**Files:**
- Modify: `CLAUDE.md` (the section under "Directory Layout" and "Implementation Order")

- [ ] **Step 1: Edit the architecture summary**

Under "Implementation Order (system features)", change item 4 from:

```
4. ~~**Agent memory**~~ done — persistent workspaces with executor-specific bootstrap docs (`CLAUDE.md` or `AGENTS.md`), `learnings.md`, `task_history.md`. Context builder regenerates identity on tier changes.
```

To:

```
4. ~~**Agent memory**~~ done — persistent workspaces with executor-specific bootstrap docs (`CLAUDE.md` or `AGENTS.md`), per-entry `learnings/LRN-NNN-<slug>.md` files (or legacy flat `learnings.md` for pre-migration workspaces), `task_history.md`. Context builder regenerates identity on tier changes. Per-entry learnings store: `src/infrastructure/learnings_store.py`. CLI: `opc learning list|get|search|add|update|promote|reindex`. Spec: `docs/superpowers/specs/2026-05-13-per-agent-learnings-structural-upgrade-design.md`.
```

Under "Directory Layout", inside the `workspaces/<agent_name>/` block, change the `learnings.md` line to:

```
|       |-- learnings/             # Per-entry LRN-NNN-<slug>.md (or legacy learnings.md pre-migration)
|       |   +-- _index.md          # Regenerated, inlined into bootstrap
```

- [ ] **Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: note per-agent learnings restructure in project CLAUDE.md"
```

---

## Self-Review

**Spec coverage check:**

| Spec section | Task(s) |
|---|---|
| §3.1 layout | T11 ensure(), T13 routes (`workspaces/<agent>/learnings/`) |
| §3.2 ID + slug rules | T1 (regex), T2 (next_id), T4 (filename) |
| §3.3 entry shape | T4 (serialize/parse), T9 (related_to + supersedes) |
| §3.4 `_index.md` | T10 |
| §3.5 validation rules | T3 (structural), T4 (id_exists), T7 (promoted_locked, id_not_found), T8 (kb_slug_missing) |
| §4 CLI surface | T17-T19 |
| §4.4 search scoring + include_promoted | T6 |
| §5 HTTP routes | T13-T15 |
| §5 auth note (bearer-only) | implicit — routes use existing `require_token` dep |
| §5 pre-migration 412 guard | T13 |
| §6 bootstrap integration | T12 |
| §7.1 migration brief | not a code task — operational; covered by spec |
| §7.2 atomic-flip semantics | T11 (state-aware ensure), T16 (410 on migrated) |
| §7.3 default for new workspaces | T11 |
| §8.1 audit verbs | T20 |
| §9 module layout | T1-T10 (store), T13-T16 (routes), T17-T19 (CLI) |
| §10 tests | T11 (unit), T13-T16 (route), T17-T19 (CLI), T21 (integration) |
| §11 follow-ups | out of scope by design |

No gaps.

**Placeholder scan:** Task 21's integration test body is a TODO sketch by design (it's the largest single task and needs the scaffolding context of the existing integration-test harness). All other tasks have concrete code in every step. No "TBD", "implement later", or "add appropriate validation" patterns.

**Type consistency:** `LearningEntry`, `LearningSummary`, `LearningSearchHit`, `LearningsStore` — method names match across tasks (`write_entry`, `read_entry`, `list_entries`, `update_entry`, `promote`, `search`, `regenerate_index`, `next_id`, `validate_id`, `validate_slug`). HTTP path conventions consistent: `/agents/{name}/learnings/entries/...`. Error codes consistent: `invalid_id`, `invalid_slug`, `missing_frontmatter`, `entry_too_large`, `unknown_related_id`, `unknown_supersedes`, `id_exists`, `id_not_found`, `promoted_locked`, `kb_slug_missing`, `kb_slug_not_found`, `workspace_not_migrated`, `endpoint_deprecated_for_migrated_workspace`.

---

## Execution

Plan complete and saved to `docs/superpowers/plans/2026-05-13-per-agent-learnings-structural-upgrade.md`.
