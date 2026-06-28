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


# ═══════════════════════════════════════════════════════════════════
# THR-032 Phase 2 — PUSH memory digest (build_memory_digest)
# ═══════════════════════════════════════════════════════════════════


def _make_memory_item(store: MemoryStore, id: str, slug: str, title: str,
                      topic: str = "workflow", body: str = "some body content\n",
                      **overrides) -> MemoryItem:
    """Write an entry and return the stamped MemoryItem."""
    entry = MemoryItem(
        id=id, slug=slug, title=title, topic=topic, body=body, **overrides,
    )
    return store.write_entry(entry, agent="test-agent")


class TestBuildMemoryDigest:
    """THR-032 Phase 2: build_memory_digest — salience-ranked, pointer-only,
    budgeted PUSH digest."""

    @pytest.fixture
    def mem_store(self, tmp_path: Path) -> MemoryStore:
        return MemoryStore(tmp_path / "memory")

    # ── Budget enforcement ──

    def test_budget_enforcement_never_exceeds_cap(self, mem_store: MemoryStore):
        """The emitted digest (including header + nudge) never exceeds the
        configured char budget."""
        for i in range(30):
            _make_memory_item(
                mem_store,
                id=f"MEM-{i + 1:03d}", slug=f"item-{i}",
                title=f"Long descriptive title for memory item number {i}",
                salience=90,
            )
        budget = 400
        digest = mem_store.build_memory_digest("brief", budget=budget)
        assert digest is not None
        assert len(digest) <= budget, f"digest {len(digest)} chars > budget {budget}"

    def test_budget_enforcement_with_nudge_when_over_capacity(self, mem_store: MemoryStore):
        """When candidate set exceeds budget, a 'Pull the long tail' nudge
        is included."""
        for i in range(30):
            _make_memory_item(
                mem_store,
                id=f"MEM-{i + 1:03d}", slug=f"item-{i}",
                title=f"Title number {i}", salience=90,
            )
        digest = mem_store.build_memory_digest("brief", budget=400)
        assert digest is not None
        assert "Pull the long tail" in digest
        assert "happyranch memory search" in digest

    # ── Ranking order ──

    def test_higher_salience_sorts_first(self, mem_store: MemoryStore):
        """Higher effective salience items appear earlier in the digest."""
        _make_memory_item(mem_store, id="MEM-001", slug="low", title="Low", salience=30)
        _make_memory_item(mem_store, id="MEM-002", slug="high", title="High", salience=90)
        _make_memory_item(mem_store, id="MEM-003", slug="mid", title="Mid", salience=60)
        digest = mem_store.build_memory_digest("brief", budget=2000)
        assert digest is not None
        idx_high = digest.index("MEM-002")
        idx_mid = digest.index("MEM-003")
        idx_low = digest.index("MEM-001")
        assert idx_high < idx_mid < idx_low

    def test_deterministic_tie_breaking(self, mem_store: MemoryStore):
        """Items with equal effective score sort deterministically (by title)."""
        _make_memory_item(mem_store, id="MEM-001", slug="b-item", title="B Item", salience=50)
        _make_memory_item(mem_store, id="MEM-002", slug="a-item", title="A Item", salience=50)
        digest = mem_store.build_memory_digest("brief", budget=2000)
        assert digest is not None
        # 'A Item' should come before 'B Item' alphabetically
        idx_a = digest.index("MEM-002")
        idx_b = digest.index("MEM-001")
        assert idx_a < idx_b

    # ── Brief-relevance boost ──

    def test_brief_relevance_boost_title_match(self, mem_store: MemoryStore):
        """Memory whose title contains the brief query beats otherwise equal
        non-matching memory."""
        _make_memory_item(mem_store, id="MEM-001", slug="a", title="About Deployment",
                          salience=50)
        _make_memory_item(mem_store, id="MEM-002", slug="b", title="Database Tips",
                          salience=50)
        digest = mem_store.build_memory_digest("deployment config")
        assert digest is not None
        idx_deploy = digest.index("MEM-001")
        idx_db = digest.index("MEM-002")
        assert idx_deploy < idx_db

    def test_brief_relevance_boost_tag_match(self, mem_store: MemoryStore):
        """Memory with matching tags gets a boost."""
        _make_memory_item(mem_store, id="MEM-001", slug="a", title="Item A",
                          tags=["ci", "docker"], salience=50)
        _make_memory_item(mem_store, id="MEM-002", slug="b", title="Item B",
                          tags=["ui", "react"], salience=50)
        digest = mem_store.build_memory_digest("docker container issue")
        assert digest is not None
        idx_ci = digest.index("MEM-001")
        idx_ui = digest.index("MEM-002")
        assert idx_ci < idx_ui

    def test_brief_relevance_boost_topic_match(self, mem_store: MemoryStore):
        """Memory with matching topic gets a boost."""
        _make_memory_item(mem_store, id="MEM-001", slug="a", title="Item A",
                          topic="database", salience=50)
        _make_memory_item(mem_store, id="MEM-002", slug="b", title="Item B",
                          topic="frontend", salience=50)
        digest = mem_store.build_memory_digest("database migration")
        assert digest is not None
        idx_db = digest.index("MEM-001")
        idx_fe = digest.index("MEM-002")
        assert idx_db < idx_fe

    # ── Zero items emits nothing ──

    def test_zero_items_emits_none(self, mem_store: MemoryStore):
        """When no valid, unpromoted memories exist, the digest returns None."""
        result = mem_store.build_memory_digest("brief")
        assert result is None

    def test_all_evicted_emits_none(self, mem_store: MemoryStore):
        """When all items are evicted, digest returns None."""
        _make_memory_item(mem_store, id="MEM-001", slug="a", title="A",
                          lifecycle="evicted")
        _make_memory_item(mem_store, id="MEM-002", slug="b", title="B",
                          lifecycle="evicted")
        result = mem_store.build_memory_digest("brief")
        assert result is None

    def test_all_superseded_emits_none(self, mem_store: MemoryStore):
        """When all items are superseded, digest returns None."""
        _make_memory_item(mem_store, id="MEM-001", slug="a", title="A",
                          lifecycle="superseded")
        result = mem_store.build_memory_digest("brief")
        assert result is None

    # ── Exclusion filters ──

    def test_excludes_promoted_items(self, mem_store: MemoryStore):
        """Promoted items are excluded from the digest."""
        _make_memory_item(mem_store, id="MEM-001", slug="a", title="Valid", salience=90)
        _make_memory_item(mem_store, id="MEM-002", slug="b", title="Promoted",
                          promoted_to="kb-foo", salience=90)
        digest = mem_store.build_memory_digest("brief")
        assert digest is not None
        assert "MEM-001" in digest
        assert "MEM-002" not in digest

    def test_excludes_evicted_items(self, mem_store: MemoryStore):
        """Evicted items are excluded from the digest."""
        _make_memory_item(mem_store, id="MEM-001", slug="a", title="Valid", salience=90)
        _make_memory_item(mem_store, id="MEM-002", slug="b", title="Evicted",
                          lifecycle="evicted", salience=90)
        digest = mem_store.build_memory_digest("brief")
        assert digest is not None
        assert "MEM-001" in digest
        assert "MEM-002" not in digest

    def test_excludes_superseded_items(self, mem_store: MemoryStore):
        """Superseded items are excluded from the digest."""
        _make_memory_item(mem_store, id="MEM-001", slug="a", title="Valid", salience=90)
        _make_memory_item(mem_store, id="MEM-002", slug="b", title="Superseded",
                          lifecycle="superseded", salience=90)
        digest = mem_store.build_memory_digest("brief")
        assert digest is not None
        assert "MEM-001" in digest
        assert "MEM-002" not in digest

    # ── Pointer-only ──

    def test_pointer_only_no_body_leakage(self, mem_store: MemoryStore):
        """Body text from memory items must never leak into the digest."""
        _make_memory_item(
            mem_store,
            id="MEM-001", slug="secret", title="Safe Title",
            body="This is SECRET content that must NOT appear in the digest.\n"
            "API_KEY=sk-123456\n",
            salience=90,
        )
        digest = mem_store.build_memory_digest("brief")
        assert digest is not None
        assert "SECRET" not in digest
        assert "API_KEY" not in digest
        assert "sk-123456" not in digest
        # But the title and id should be there
        assert "MEM-001" in digest
        assert "Safe Title" in digest

    # ── Decay is read-only ──

    def test_build_digest_does_not_write_files(self, mem_store: MemoryStore):
        """Building the digest must not write any memory files or change
        mtimes / frontmatter."""
        entry = _make_memory_item(
            mem_store, id="MEM-001", slug="old", title="Old Entry",
            salience=50,
        )
        original_mtime = (mem_store.root / "MEM-001-old.md").stat().st_mtime
        # Build digest multiple times
        mem_store.build_memory_digest("brief")
        mem_store.build_memory_digest("different brief")
        mem_store.build_memory_digest("third brief")
        # Verify no files were written
        new_mtime = (mem_store.root / "MEM-001-old.md").stat().st_mtime
        assert new_mtime == original_mtime
        # Read-back salience should be unchanged
        re_read = mem_store.read_entry("MEM-001")
        assert re_read.salience == 50

    # ── Ancestor boost ──

    def test_ancestor_boost_promotes_matching_source_task(self, mem_store: MemoryStore):
        """Memory whose source_task is an ancestor of the current task gets a
        ranking boost."""
        _make_memory_item(
            mem_store, id="MEM-001", slug="a", title="Ancestor Memory",
            source_task="TASK-100", salience=50,
        )
        _make_memory_item(
            mem_store, id="MEM-002", slug="b", title="Unrelated Memory",
            salience=50,
        )
        digest = mem_store.build_memory_digest(
            "brief", ancestor_task_ids={"TASK-050", "TASK-100", "TASK-200"},
        )
        assert digest is not None
        idx_ancestor = digest.index("MEM-001")
        idx_unrelated = digest.index("MEM-002")
        assert idx_ancestor < idx_unrelated

    def test_no_ancestor_boost_when_no_match(self, mem_store: MemoryStore):
        """When no source_task matches ancestors, no boost is applied and
        equal-salience items tie-break on title."""
        _make_memory_item(
            mem_store, id="MEM-001", slug="b", title="B Item",
            source_task="TASK-999", salience=50,
        )
        _make_memory_item(
            mem_store, id="MEM-002", slug="a", title="A Item",
            salience=50,
        )
        digest = mem_store.build_memory_digest(
            "brief", ancestor_task_ids={"TASK-001"},
        )
        assert digest is not None
        idx_a = digest.index("MEM-002")
        idx_b = digest.index("MEM-001")
        assert idx_a < idx_b  # alphabetical, no boost

    # ── Directive boost ──

    def test_directive_items_get_provenance_boost(self, mem_store: MemoryStore):
        """Directive-provenance items get a boost over experiential items
        of equal base salience."""
        _make_memory_item(
            mem_store, id="MEM-001", slug="a", title="Experiential",
            provenance="experiential", salience=50,
        )
        _make_memory_item(
            mem_store, id="MEM-002", slug="b", title="Directive",
            provenance="directive", salience=50,
        )
        digest = mem_store.build_memory_digest("brief")
        assert digest is not None
        idx_dir = digest.index("MEM-002")
        idx_exp = digest.index("MEM-001")
        assert idx_dir < idx_exp

    # ── Digest format ──

    def test_digest_header_present(self, mem_store: MemoryStore):
        """The digest includes the standard MEMORY-DIGEST header."""
        _make_memory_item(mem_store, id="MEM-001", slug="ok", title="Hello",
                          salience=50)
        digest = mem_store.build_memory_digest("brief")
        assert digest is not None
        assert "=== MEMORY-DIGEST (system) ===" in digest

    def test_digest_line_format(self, mem_store: MemoryStore):
        """Each pointer line carries id, title, provenance, salience."""
        _make_memory_item(
            mem_store, id="MEM-001", slug="ok", title="Format Check",
            provenance="reflective", salience=73,
        )
        digest = mem_store.build_memory_digest("brief")
        assert digest is not None
        assert "MEM-001" in digest
        assert "Format Check" in digest
        assert "reflective" in digest
        assert "73" in digest

    def test_digest_uses_happyranch_memory_cli_references(self, mem_store: MemoryStore):
        """The digest references the post-rename `happyranch memory` CLI."""
        # Create enough items that budget overflow forces the nudge.
        for i in range(20):
            _make_memory_item(
                mem_store,
                id=f"MEM-{i + 1:03d}", slug=f"item-{i}",
                title=f"Memory item number {i} with a longish title",
                salience=90,
            )
        digest = mem_store.build_memory_digest("brief", budget=300)
        assert digest is not None
        assert "happyranch memory get" in digest
        assert "happyranch memory search" in digest

    def test_digest_pointer_lines_include_salience(self, mem_store: MemoryStore):
        """Each pointer line includes the effective salience score."""
        _make_memory_item(mem_store, id="MEM-001", slug="a", title="A", salience=50)
        digest = mem_store.build_memory_digest("brief")
        assert digest is not None
        assert "salience" in digest

    # ── Directive scope boost ──

    def test_directive_agent_scope_boosted_in_per_agent_digest(self, mem_store: MemoryStore):
        """Agent-scope directive memories get the directive boost when the
        digest scope is 'agent' (the default per-agent scope)."""
        _make_memory_item(
            mem_store, id="MEM-001", slug="a", title="Experiential",
            provenance="experiential", scope="agent", salience=50,
        )
        _make_memory_item(
            mem_store, id="MEM-002", slug="b", title="Directive Agent",
            provenance="directive", scope="agent", salience=50,
        )
        digest = mem_store.build_memory_digest("brief", scope="agent")
        assert digest is not None
        idx_dir = digest.index("MEM-002")
        idx_exp = digest.index("MEM-001")
        assert idx_dir < idx_exp

    def test_directive_team_scope_not_boosted_in_per_agent_digest(self, mem_store: MemoryStore):
        """Team-scope directive memories do NOT get the directive boost
        when the digest scope is 'agent' (team/org scoped memory is
        later/founder-gated per §11.5)."""
        _make_memory_item(
            mem_store, id="MEM-001", slug="a", title="Agent Directive",
            provenance="directive", scope="agent", salience=50,
        )
        _make_memory_item(
            mem_store, id="MEM-002", slug="b", title="Team Directive",
            provenance="directive", scope="team", salience=50,
        )
        digest = mem_store.build_memory_digest("brief", scope="agent")
        assert digest is not None
        # Agent-scope directive (MEM-001) gets boost, ranks above team-scope
        idx_agent = digest.index("MEM-001")
        idx_team = digest.index("MEM-002")
        assert idx_agent < idx_team

    def test_directive_org_scope_not_boosted_in_per_agent_digest(self, mem_store: MemoryStore):
        """Org-scope directive memories do NOT get the directive boost
        when the digest scope is 'agent'."""
        _make_memory_item(
            mem_store, id="MEM-001", slug="a", title="Agent Directive",
            provenance="directive", scope="agent", salience=50,
        )
        _make_memory_item(
            mem_store, id="MEM-002", slug="b", title="Org Directive",
            provenance="directive", scope="org", salience=50,
        )
        digest = mem_store.build_memory_digest("brief", scope="agent")
        assert digest is not None
        idx_agent = digest.index("MEM-001")
        idx_org = digest.index("MEM-002")
        assert idx_agent < idx_org

    def test_directive_nonmatching_scope_no_boost_vs_experiential(self, mem_store: MemoryStore):
        """A team-scope directive with equal base salience to an experiential
        item ties on effective salience (no boost for nonmatching scope),
        so the experiential item may tie-break via alphabetical ordering."""
        _make_memory_item(
            mem_store, id="MEM-001", slug="a", title="A Item",
            provenance="experiential", scope="agent", salience=50,
        )
        _make_memory_item(
            mem_store, id="MEM-002", slug="b", title="B Team Directive",
            provenance="directive", scope="team", salience=50,
        )
        digest = mem_store.build_memory_digest("brief", scope="agent")
        assert digest is not None
        # Both have effective salience 50 — 'A Item' alphabetically < 'B Team Directive'
        idx_a = digest.index("MEM-001")
        idx_b = digest.index("MEM-002")
        assert idx_a < idx_b

    # ── Budget boundary / tiny budget ──

    def test_budget_1_returns_none(self, mem_store: MemoryStore):
        """Budget of 1 char cannot fit header; returns None cleanly."""
        _make_memory_item(mem_store, id="MEM-001", slug="a", title="A", salience=90)
        result = mem_store.build_memory_digest("brief", budget=1)
        assert result is None

    def test_budget_10_returns_none(self, mem_store: MemoryStore):
        """Budget of 10 chars cannot fit header; returns None cleanly."""
        _make_memory_item(mem_store, id="MEM-001", slug="a", title="A", salience=90)
        result = mem_store.build_memory_digest("brief", budget=10)
        assert result is None

    def test_budget_50_returns_none(self, mem_store: MemoryStore):
        """Budget of 50 chars is below header + intro length; returns None."""
        _make_memory_item(mem_store, id="MEM-001", slug="a", title="A", salience=90)
        result = mem_store.build_memory_digest("brief", budget=50)
        assert result is None

    def test_budget_100_returns_header_only_or_none(self, mem_store: MemoryStore):
        """Budget of 100 chars may fit header but not a pointer line;
        returns None cleanly (header-only without pointer is omitted)."""
        _make_memory_item(mem_store, id="MEM-001", slug="a", title="A", salience=90)
        result = mem_store.build_memory_digest("brief", budget=100)
        assert result is None  # header too long for budget=100 to include a pointer

    def test_budget_header_size_boundary(self, mem_store: MemoryStore):
        """Budget equal to header size cannot include a pointer line;
        returns None."""
        _make_memory_item(mem_store, id="MEM-001", slug="a", title="Hello", salience=90)
        header = "=== MEMORY-DIGEST (system) ===\nRelevant memory (pointers only — fetch bodies with `happyranch memory get <id>`):\n\n"
        # Budget exactly header size — no room for pointer
        result = mem_store.build_memory_digest("brief", budget=len(header))
        assert result is None

    def test_budget_exactly_fits_one_pointer(self, mem_store: MemoryStore):
        """Budget exactly fits header + one pointer line (no nudge)."""
        _make_memory_item(mem_store, id="MEM-001", slug="a", title="X", salience=50)
        # Compute exact budget needed for header + one line
        header = "=== MEMORY-DIGEST (system) ===\nRelevant memory (pointers only — fetch bodies with `happyranch memory get <id>`):\n\n"
        one_line = "- `MEM-001` — X  (experiential, salience 50)\n"
        exact_budget = len(header + one_line)
        result = mem_store.build_memory_digest("brief", budget=exact_budget)
        assert result is not None
        assert len(result) <= exact_budget
        assert "MEM-001" in result
        assert "Pull the long tail" not in result  # no nudge for single item

    def test_budget_exactly_fits_one_pointer_minus_one(self, mem_store: MemoryStore):
        """Budget one char short of fitting one pointer — returns None
        because header+pointer doesn't fit."""
        _make_memory_item(mem_store, id="MEM-001", slug="a", title="X", salience=50)
        header = "=== MEMORY-DIGEST (system) ===\nRelevant memory (pointers only — fetch bodies with `happyranch memory get <id>`):\n\n"
        one_line = "- `MEM-001` — X  (experiential, salience 50)\n"
        exact_budget = len(header + one_line)
        result = mem_store.build_memory_digest("brief", budget=exact_budget - 1)
        assert result is None

    def test_budget_overflow_with_nudge_small(self, mem_store: MemoryStore):
        """When budget fits header + one pointer + nudge but not two pointers,
        digest includes the top pointer + nudge."""
        for i in range(5):
            _make_memory_item(
                mem_store, id=f"MEM-{i + 1:03d}", slug=f"item-{i}",
                title=f"Item {i}", salience=90 - i,
            )
        header = "=== MEMORY-DIGEST (system) ===\nRelevant memory (pointers only — fetch bodies with `happyranch memory get <id>`):\n\n"
        one_line = "- `MEM-001` — Item 0  (experiential, salience 90)\n"
        second_line = "- `MEM-002` — Item 1  (experiential, salience 89)\n"
        nudge = "Pull the long tail: `happyranch memory search \"<terms>\"`.\n"
        # Budget fits header + 1 pointer + nudge, but NOT 2 pointers + nudge
        budget = len(header + one_line + nudge)
        result = mem_store.build_memory_digest("brief", budget=budget)
        assert result is not None
        assert len(result) <= budget
        assert "MEM-001" in result
        assert "Pull the long tail" in result
        # MEM-002 should NOT appear — only one pointer fits + nudge
        assert "MEM-002" not in result

    def test_budget_overflow_no_room_for_nudge(self, mem_store: MemoryStore):
        """When there are remaining items but nudge doesn't fit after the last
        pointer, no nudge is included and the digest just omits the tail."""
        for i in range(10):
            _make_memory_item(
                mem_store, id=f"MEM-{i + 1:03d}", slug=f"item-{i}",
                title=f"Item number {i}", salience=90 - i,
            )
        header = "=== MEMORY-DIGEST (system) ===\nRelevant memory (pointers only — fetch bodies with `happyranch memory get <id>`):\n\n"
        one_line = "- `MEM-001` — Item number 0  (experiential, salience 90)\n"
        # Budget fits exactly header + 1 pointer, no room for nudge
        budget = len(header + one_line)
        result = mem_store.build_memory_digest("brief", budget=budget)
        assert result is not None
        assert len(result) <= budget
        assert "MEM-001" in result
        # Nudge doesn't fit — omitted
        assert "Pull the long tail" not in result

    def test_budget_zero_omits_digest(self, mem_store: MemoryStore):
        """Budget of 0 should return None — digest is disabled."""
        _make_memory_item(mem_store, id="MEM-001", slug="a", title="A", salience=90)
        result = mem_store.build_memory_digest("brief", budget=0)
        assert result is None

    # ── Edge cases ──

    def test_empty_brief_is_handled(self, mem_store: MemoryStore):
        """An empty brief string should still produce a digest ranked by
        salience alone."""
        _make_memory_item(mem_store, id="MEM-001", slug="a", title="A", salience=30)
        _make_memory_item(mem_store, id="MEM-002", slug="b", title="B", salience=90)
        digest = mem_store.build_memory_digest("")
        assert digest is not None
        idx_b = digest.index("MEM-002")
        idx_a = digest.index("MEM-001")
        assert idx_b < idx_a

    def test_single_item_within_budget(self, mem_store: MemoryStore):
        """A single item digest stays well within budget."""
        _make_memory_item(mem_store, id="MEM-001", slug="x", title="Single", salience=50)
        digest = mem_store.build_memory_digest("brief", budget=2000)
        assert digest is not None
        assert len(digest) < 500
        assert "Pull the long tail" not in digest  # single item fits, no nudge


# ═══════════════════════════════════════════════════════════════════
# THR-032 P3a — explicit lifecycle transitions (set_lifecycle)
# ═══════════════════════════════════════════════════════════════════


class TestSetLifecycle:
    """THR-032 P3a: set_lifecycle — explicit lifecycle transition API."""

    @pytest.fixture
    def mem_store(self, tmp_path: Path) -> MemoryStore:
        return MemoryStore(tmp_path / "memory")

    # ── Allowed transitions ──

    def test_valid_to_superseded(self, mem_store: MemoryStore):
        _make_memory_item(mem_store, id="MEM-001", slug="a", title="T", lifecycle="valid")
        updated, prior = mem_store.set_lifecycle(
            "MEM-001", "superseded", agent="dev_agent", reason="superseded by MEM-002",
        )
        assert prior == "valid"
        assert updated.lifecycle == "superseded"
        assert updated.updated_by == "dev_agent"
        assert updated.updated_at is not None
        # Disk file reflects the change
        re_read = mem_store.read_entry("MEM-001")
        assert re_read.lifecycle == "superseded"

    def test_valid_to_evicted(self, mem_store: MemoryStore):
        _make_memory_item(mem_store, id="MEM-001", slug="a", title="T", lifecycle="valid")
        updated, prior = mem_store.set_lifecycle(
            "MEM-001", "evicted", agent="dev_agent", reason="no longer relevant",
        )
        assert prior == "valid"
        assert updated.lifecycle == "evicted"

    def test_superseded_to_evicted(self, mem_store: MemoryStore):
        _make_memory_item(mem_store, id="MEM-001", slug="a", title="T", lifecycle="superseded")
        updated, prior = mem_store.set_lifecycle(
            "MEM-001", "evicted", agent="dev_agent", reason="compaction candidate",
        )
        assert prior == "superseded"
        assert updated.lifecycle == "evicted"

    def test_evicted_to_valid(self, mem_store: MemoryStore):
        _make_memory_item(mem_store, id="MEM-001", slug="a", title="T", lifecycle="evicted")
        updated, prior = mem_store.set_lifecycle(
            "MEM-001", "valid", agent="dev_agent", reason="restored after review",
        )
        assert prior == "evicted"
        assert updated.lifecycle == "valid"

    def test_superseded_to_valid(self, mem_store: MemoryStore):
        _make_memory_item(mem_store, id="MEM-001", slug="a", title="T", lifecycle="superseded")
        updated, prior = mem_store.set_lifecycle(
            "MEM-001", "valid", agent="dev_agent", reason="correction — not actually superseded",
        )
        assert prior == "superseded"
        assert updated.lifecycle == "valid"

    # ── Rejections ──

    def test_rejects_invalid_lifecycle_name(self, mem_store: MemoryStore):
        _make_memory_item(mem_store, id="MEM-001", slug="a", title="T")
        with pytest.raises(InvalidLearningEntry) as exc:
            mem_store.set_lifecycle("MEM-001", "deleted", agent="x", reason="test")
        assert "invalid_lifecycle" in exc.value.code

    def test_rejects_unsupported_transition_evicted_to_superseded(self, mem_store: MemoryStore):
        _make_memory_item(mem_store, id="MEM-001", slug="a", title="T", lifecycle="evicted")
        with pytest.raises(InvalidLearningEntry) as exc:
            mem_store.set_lifecycle("MEM-001", "superseded", agent="x", reason="test")
        assert "unsupported_transition" in exc.value.code

    def test_rejects_unsupported_transition_superseded_to_valid_when_promoted(self, mem_store: MemoryStore):
        # superseded->valid IS supported; this test is about promoted lock
        pass

    def test_rejects_noop_transition(self, mem_store: MemoryStore):
        _make_memory_item(mem_store, id="MEM-001", slug="a", title="T", lifecycle="valid")
        with pytest.raises(InvalidLearningEntry) as exc:
            mem_store.set_lifecycle("MEM-001", "valid", agent="x", reason="test")
        assert "noop_transition" in exc.value.code

    def test_rejects_missing_reason(self, mem_store: MemoryStore):
        _make_memory_item(mem_store, id="MEM-001", slug="a", title="T")
        with pytest.raises(InvalidLearningEntry) as exc:
            mem_store.set_lifecycle("MEM-001", "evicted", agent="x", reason="")
        assert "reason_required" in exc.value.code

        with pytest.raises(InvalidLearningEntry) as exc:
            mem_store.set_lifecycle("MEM-001", "evicted", agent="x", reason="   ")
        assert "reason_required" in exc.value.code

    def test_rejects_promoted_locked(self, mem_store: MemoryStore):
        _make_memory_item(mem_store, id="MEM-001", slug="a", title="T", promoted_to="kb-rule")
        with pytest.raises(PromotedLocked) as exc:
            mem_store.set_lifecycle("MEM-001", "evicted", agent="x", reason="test")
        assert exc.value.id == "MEM-001"

    def test_rejects_not_found(self, mem_store: MemoryStore):
        with pytest.raises(LearningNotFound):
            mem_store.set_lifecycle("MEM-999", "evicted", agent="x", reason="test")

    def test_rejects_invalid_id_format(self, mem_store: MemoryStore):
        with pytest.raises(InvalidLearningId):
            mem_store.set_lifecycle("bad-id", "evicted", agent="x", reason="test")

    # ── LRN alias canonicalization ──

    def test_lrn_alias_input_writes_canonical_mem_file(self, mem_store: MemoryStore):
        """LRN-NNN input resolves to the on-disk MEM-NNN file and writes there."""
        _make_memory_item(mem_store, id="MEM-001", slug="a", title="T")
        updated, prior = mem_store.set_lifecycle(
            "LRN-001", "evicted", agent="dev_agent", reason="alias test",
        )
        assert prior == "valid"
        assert updated.id == "MEM-001"
        assert updated.lifecycle == "evicted"
        # File still on disk as MEM-001, never resurrected as LRN-001
        assert (mem_store.root / "MEM-001-a.md").exists()
        assert not (mem_store.root / "LRN-001-a.md").exists()
        re_read = mem_store.read_entry("MEM-001")
        assert re_read.lifecycle == "evicted"

    # ── No frontmatter lifecycle_reason churn ──

    def test_no_lifecycle_reason_in_frontmatter(self, mem_store: MemoryStore):
        _make_memory_item(mem_store, id="MEM-001", slug="a", title="T")
        mem_store.set_lifecycle(
            "MEM-001", "evicted", agent="dev_agent", reason="should be audit-only",
        )
        file_text = (mem_store.root / "MEM-001-a.md").read_text()
        assert "lifecycle_reason" not in file_text

    # ── Updated timestamp and agent tracking ──

    def test_updates_updated_at_and_updated_by(self, mem_store: MemoryStore):
        import time
        entry = _make_memory_item(mem_store, id="MEM-001", slug="a", title="T")
        original_updated_by = entry.updated_by
        # Small sleep to ensure timestamp changes
        time.sleep(1.1)
        updated, prior = mem_store.set_lifecycle(
            "MEM-001", "evicted", agent="new-agent", reason="test",
        )
        assert updated.updated_by == "new-agent"
        assert updated.updated_at is not None
        assert updated.updated_at != entry.updated_at
        assert updated.authored_by == original_updated_by
        assert updated.authored_at == entry.authored_at  # authored fields preserved

    # ── Evicted stays on disk ──

    def test_evicted_stays_on_disk(self, mem_store: MemoryStore):
        _make_memory_item(mem_store, id="MEM-001", slug="a", title="T")
        file_path = mem_store.root / "MEM-001-a.md"
        assert file_path.exists()
        mem_store.set_lifecycle("MEM-001", "evicted", agent="x", reason="test")
        assert file_path.exists()  # still exists — no hard delete

    # ── Index regeneration after evict/restore ──

    def test_index_excludes_evicted_after_set_lifecycle(self, mem_store: MemoryStore):
        _make_memory_item(mem_store, id="MEM-001", slug="a", title="Kept", topic="w")
        _make_memory_item(mem_store, id="MEM-002", slug="b", title="Gone", topic="w")
        mem_store.set_lifecycle("MEM-002", "evicted", agent="x", reason="test")
        # The set_lifecycle call does NOT auto-regenerate index;
        # regenerate_index must be called separately (as the route does).
        mem_store.regenerate_index()
        idx = (mem_store.root / "_index.md").read_text()
        assert "MEM-001" in idx
        assert "MEM-002" not in idx

    def test_index_includes_restored_after_set_lifecycle(self, mem_store: MemoryStore):
        _make_memory_item(mem_store, id="MEM-001", slug="a", title="Restored", topic="w", lifecycle="evicted")
        mem_store.regenerate_index()
        idx = (mem_store.root / "_index.md").read_text()
        assert "MEM-001" not in idx  # evicted → excluded
        mem_store.set_lifecycle("MEM-001", "valid", agent="x", reason="restore")
        mem_store.regenerate_index()
        idx2 = (mem_store.root / "_index.md").read_text()
        assert "MEM-001" in idx2  # restored → included
