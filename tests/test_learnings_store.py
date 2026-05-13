from __future__ import annotations

from pathlib import Path

import pytest

from src.infrastructure.learnings_store import (
    LearningEntry,
    LearningsStore,
    InvalidLearningId,
    InvalidLearningEntry,
    LearningIdExists,
    LearningSlugExists,
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


def test_write_entry_rejects_duplicate_slug(store: LearningsStore):
    store.write_entry(_make_entry(id="LRN-001", slug="same-slug"), agent="z")
    with pytest.raises(LearningSlugExists):
        store.write_entry(_make_entry(id="LRN-002", slug="same-slug"), agent="z")


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


def test_update_entry_rejects_slug_collision_with_different_entry(store: LearningsStore):
    store.write_entry(_make_entry(id="LRN-001", slug="a"), agent="z")
    store.write_entry(_make_entry(id="LRN-002", slug="b"), agent="z")
    with pytest.raises(LearningSlugExists):
        store.update_entry("LRN-001", _make_entry(id="LRN-001", slug="b"), agent="z")


def test_update_entry_allows_same_slug_no_rename(store: LearningsStore):
    """Idempotent update keeping the same slug must not trip slug-collision."""
    store.write_entry(_make_entry(id="LRN-001", slug="a", title="v1"), agent="z")
    res = store.update_entry("LRN-001", _make_entry(id="LRN-001", slug="a", title="v2"), agent="z")
    assert res.title == "v2"


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
