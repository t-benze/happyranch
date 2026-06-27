from __future__ import annotations

from pathlib import Path

import pytest

from runtime.infrastructure.learnings_store import (
    LearningEntry,
    LearningsStore,
    MemoryItem,
    MemoryStore,
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
    # THR-032 Phase R: new ids allocate the canonical MEM- prefix.
    assert store.next_id() == "MEM-001"


def test_next_id_increments_from_max_suffix(store: LearningsStore):
    # The number line continues across BOTH prefixes, so the next id after a
    # legacy LRN-005 is MEM-006 (same line, new prefix — never a reset).
    (store.root / "LRN-005-foo.md").write_text("---\nid: LRN-005\nslug: foo\ntitle: x\ntopic: t\n---\n")
    (store.root / "MEM-003-bar.md").write_text("---\nid: MEM-003\nslug: bar\ntitle: x\ntopic: t\n---\n")
    assert store.next_id() == "MEM-006"


def test_next_id_ignores_index_and_non_lrn_files(store: LearningsStore):
    (store.root / "_index.md").write_text("# index")
    (store.root / "README.md").write_text("not a learning")
    (store.root / "LRN-002-foo.md").write_text("---\nid: LRN-002\nslug: foo\ntitle: x\ntopic: t\n---\n")
    assert store.next_id() == "MEM-003"


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
    assert "See KB precedent: `my-precedent`" in res.body
    assert "Original body" not in res.body  # body replaced with stub
    assert res.updated_by == "founder"


def test_promote_404_when_missing(store: LearningsStore):
    with pytest.raises(LearningNotFound):
        store.promote("LRN-999", kb_slug="x", agent="z")


def test_promote_idempotent_when_already_promoted_to_same_slug(store: LearningsStore):
    store.write_entry(_make_entry(id="LRN-001", slug="a"), agent="z")
    res1 = store.promote("LRN-001", kb_slug="kb-a", agent="z")
    res2 = store.promote("LRN-001", kb_slug="kb-a", agent="z")
    assert res2.promoted_to == "kb-a"
    assert res2.updated_at == res1.updated_at  # no re-stamp on idempotent re-promote


def test_promote_refuses_change_when_already_promoted_to_different_slug(store: LearningsStore):
    store.write_entry(_make_entry(id="LRN-001", slug="a"), agent="z")
    store.promote("LRN-001", kb_slug="kb-a", agent="z")
    with pytest.raises(PromotedLocked):
        store.promote("LRN-001", kb_slug="kb-b", agent="z")


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


def test_regenerate_index_orders_numerically_past_999(store: LearningsStore):
    """String sort breaks at LRN-1000+ (LRN-999 sorts after LRN-1000
    lexicographically). Index must order by numeric suffix, newest first."""
    store.write_entry(_make_entry(id="LRN-998", slug="a", title="prior", topic="w"), agent="z")
    store.write_entry(_make_entry(id="LRN-999", slug="b", title="older 3-digit", topic="w"), agent="z")
    store.write_entry(_make_entry(id="LRN-1000", slug="c", title="newer 4-digit", topic="w"), agent="z")
    store.regenerate_index()
    idx = (store.root / "_index.md").read_text()
    # LRN-1000 is newest and must appear first inside the topic block.
    assert idx.index("LRN-1000") < idx.index("LRN-999") < idx.index("LRN-998")


def test_regenerate_index_shows_promoted_marker(store: LearningsStore):
    store.write_entry(_make_entry(id="LRN-001", slug="a", title="promoted thing", topic="w"), agent="z")
    store.promote("LRN-001", kb_slug="kb-precedent", agent="z")
    store.regenerate_index()
    idx = (store.root / "_index.md").read_text()
    assert "↗ promoted: kb-precedent" in idx


def test_update_entry_rejects_self_reference_in_related_to(store: LearningsStore):
    store.write_entry(_make_entry(id="LRN-001", slug="a"), agent="z")
    updated = _make_entry(id="LRN-001", slug="a", related_to=["LRN-001"])
    with pytest.raises(InvalidLearningEntry) as exc:
        store.update_entry("LRN-001", updated, agent="z")
    assert exc.value.code == "self_reference"


def test_update_entry_rejects_self_reference_in_supersedes(store: LearningsStore):
    store.write_entry(_make_entry(id="LRN-001", slug="a"), agent="z")
    updated = _make_entry(id="LRN-001", slug="a", supersedes="LRN-001")
    with pytest.raises(InvalidLearningEntry) as exc:
        store.update_entry("LRN-001", updated, agent="z")
    assert exc.value.code == "self_reference"


# --- THR-032 Phase 1: harness-agnostic memory layer (additive store generalization) ---

# A REAL pre-Phase-1 entry (workspace LRN-001) — carries NONE of the four new
# frontmatter keys. Used as the golden corpus for the no-churn round-trip proof.
GOLDEN_RAW_ENTRY = """---
id: LRN-001
slug: rename-gotchas-and-gitnexus-worktree-blindspot
title: Package-rename safety + gitnexus_detect_changes is blind to worktrees
topic: refactoring
tags:
- rename
- gitnexus
- worktree
- imports
authored_by: dev_agent
authored_at: '2026-06-02T14:22:27Z'
updated_by: dev_agent
updated_at: '2026-06-02T14:22:27Z'
---

Two durable facts confirmed during THR-001 Phase 1 (src/ -> runtime/ rename,
TASK-011, commit 298c751).
"""


def test_alias_exports_resolve_to_renamed_symbols():
    """The two back-compat aliases must cover every importer's old names."""
    assert LearningsStore is MemoryStore
    assert LearningEntry is MemoryItem


def test_golden_entry_round_trips_with_no_new_key_churn(store: LearningsStore):
    """A real existing entry (none of the 4 new keys) must serialize identically
    under the new code — all-default new fields are omitted, so no byte churn."""
    # Capture the canonical (fixpoint) form: serialize(parse(raw)). This avoids a
    # false failure if the raw file's key order isn't already canonical.
    canonical = store._serialize(store._parse(GOLDEN_RAW_ENTRY))
    # None of the four additive keys leak into the serialization (all at default).
    for key in ("provenance:", "scope:", "lifecycle:", "salience:"):
        assert key not in canonical
    # Fixpoint: re-parsing then re-serializing the canonical form is byte-identical.
    assert store._serialize(store._parse(canonical)) == canonical


def test_parse_defaults_new_fields_when_absent(store: LearningsStore):
    parsed = store._parse(GOLDEN_RAW_ENTRY)
    assert parsed.provenance == "experiential"
    assert parsed.scope == "agent"
    assert parsed.lifecycle == "valid"
    assert parsed.salience == 50


def test_validate_rejects_bad_provenance(store: LearningsStore):
    with pytest.raises(InvalidLearningEntry) as exc:
        store._validate_entry_structure(_make_entry(provenance="bogus"))
    assert exc.value.code == "invalid_provenance"


def test_validate_rejects_bad_scope(store: LearningsStore):
    with pytest.raises(InvalidLearningEntry) as exc:
        store._validate_entry_structure(_make_entry(scope="bogus"))
    assert exc.value.code == "invalid_scope"


def test_validate_rejects_bad_lifecycle(store: LearningsStore):
    with pytest.raises(InvalidLearningEntry) as exc:
        store._validate_entry_structure(_make_entry(lifecycle="bogus"))
    assert exc.value.code == "invalid_lifecycle"


def test_salience_clamped_high_on_write_and_read(store: LearningsStore):
    written = store.write_entry(
        _make_entry(id="LRN-001", slug="a", salience=150), agent="z",
    )
    assert written.salience == 100  # clamped on write
    assert store.read_entry("LRN-001").salience == 100  # normalized on read


def test_salience_clamped_low_on_write_and_read(store: LearningsStore):
    written = store.write_entry(
        _make_entry(id="LRN-002", slug="b", salience=-5), agent="z",
    )
    assert written.salience == 0  # clamped on write
    assert store.read_entry("LRN-002").salience == 0  # normalized on read


def test_parse_clamps_out_of_range_salience(store: LearningsStore):
    raw_high = (
        "---\nid: LRN-009\nslug: hi\ntitle: t\ntopic: w\nsalience: 150\n---\n\nbody\n"
    )
    raw_low = (
        "---\nid: LRN-010\nslug: lo\ntitle: t\ntopic: w\nsalience: -5\n---\n\nbody\n"
    )
    assert store._parse(raw_high).salience == 100
    assert store._parse(raw_low).salience == 0


def test_regenerate_index_excludes_evicted(store: LearningsStore):
    store.write_entry(
        _make_entry(id="LRN-001", slug="a", title="kept entry", topic="w"), agent="z",
    )
    store.write_entry(
        _make_entry(
            id="LRN-002", slug="b", title="gone entry", topic="w", lifecycle="evicted",
        ),
        agent="z",
    )
    store.regenerate_index()
    idx = (store.root / "_index.md").read_text()
    assert "LRN-001" in idx
    assert "LRN-002" not in idx
    assert "gone entry" not in idx
    # Header count reflects only the one non-evicted entry.
    assert "1 entries" in idx


def test_regenerate_index_line_is_superset_with_salience_provenance(
    store: LearningsStore,
):
    store.write_entry(
        _make_entry(
            id="LRN-001", slug="a", title="My Title", topic="w",
            salience=88, provenance="directive",
        ),
        agent="z",
    )
    store.regenerate_index()
    idx = (store.root / "_index.md").read_text()
    # Still starts with today's exact line shape …
    assert "- `LRN-001` — My Title" in idx
    # … plus the appended salience + provenance superset.
    assert "(directive, salience 88)" in idx


def test_list_entries_populates_new_summary_fields_and_returns_all(
    store: LearningsStore,
):
    store.write_entry(
        _make_entry(id="LRN-001", slug="a", topic="w", provenance="directive", salience=70),
        agent="z",
    )
    store.write_entry(
        _make_entry(id="LRN-002", slug="b", topic="w", lifecycle="evicted"), agent="z",
    )
    summaries = {s.id: s for s in store.list_entries()}
    # list_entries returns ALL entries (evicted included) — the list route is unaffected.
    assert set(summaries) == {"LRN-001", "LRN-002"}
    assert summaries["LRN-001"].provenance == "directive"
    assert summaries["LRN-001"].salience == 70
    assert summaries["LRN-001"].lifecycle == "valid"
    assert summaries["LRN-002"].lifecycle == "evicted"
