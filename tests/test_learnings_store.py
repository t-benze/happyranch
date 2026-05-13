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
