from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from runtime.infrastructure.kb_store import (
    KBEntry,
    KBStore,
    SlugExists,
    InvalidSlug,
    InvalidEntry,
    NotFound,
)


@pytest.fixture
def store(tmp_path: Path) -> KBStore:
    kb_dir = tmp_path / "kb"
    kb_dir.mkdir()
    return KBStore(kb_dir)


def test_valid_slugs_accepted(store: KBStore):
    for slug in ["a", "alipay-refund-endpoint", "precedent-task-037-approve", "v3"]:
        store.validate_slug(slug)  # no raise


def test_invalid_slugs_rejected(store: KBStore):
    for bad in ["", "A", "has space", "-leading", "has_underscore", "x" * 65, "has.dot"]:
        with pytest.raises(InvalidSlug):
            store.validate_slug(bad)


def test_write_entry_round_trips_frontmatter_and_body(store: KBStore):
    entry = KBEntry(
        slug="alipay-refund-endpoint",
        title="Alipay v3 refund endpoint quirks",
        type="reference",
        topic="payment",
        tags=["alipay", "refund"],
        body="# Alipay v3 refund endpoint quirks\n\nDetails here.\n",
    )
    written = store.write_entry(entry, agent="dev_agent")
    assert written.authored_by == "dev_agent"
    assert written.updated_by == "dev_agent"
    assert written.authored_at is not None

    loaded = store.read_entry("alipay-refund-endpoint")
    assert loaded.title == entry.title
    assert loaded.type == "reference"
    assert loaded.topic == "payment"
    assert loaded.tags == ["alipay", "refund"]
    assert "Details here." in loaded.body
    assert loaded.authored_by == "dev_agent"


def test_write_entry_rejects_slug_mismatch(store: KBStore):
    entry = KBEntry(
        slug="good-slug",
        title="Mismatch",
        type="reference",
        topic="visa",
        body="# x\n",
    )
    # Write successfully
    store.write_entry(entry, agent="dev_agent")
    # Writing a new entry with the same slug must raise SlugExists
    with pytest.raises(SlugExists):
        store.write_entry(entry, agent="dev_agent")


def test_write_entry_accepts_arbitrary_type(store: KBStore):
    # `type` is a freeform tag-like label — any non-empty string is allowed.
    entry = KBEntry(
        slug="custom-type",
        title="t",
        type="guide",
        topic="payment",
        body="# x\n",
    )
    written = store.write_entry(entry, agent="dev_agent")
    assert written.type == "guide"
    assert store.read_entry("custom-type").type == "guide"


def test_write_entry_rejects_empty_type(store: KBStore):
    entry = KBEntry(
        slug="empty-type",
        title="t",
        type="",
        topic="payment",
        body="# x\n",
    )
    with pytest.raises(InvalidEntry) as exc:
        store.write_entry(entry, agent="dev_agent")
    assert "missing_frontmatter" in str(exc.value)


def test_write_entry_rejects_oversized_body(store: KBStore):
    entry = KBEntry(
        slug="too-big",
        title="big",
        type="reference",
        topic="payment",
        body="x" * (32 * 1024 + 1),
    )
    with pytest.raises(InvalidEntry) as exc:
        store.write_entry(entry, agent="dev_agent")
    assert "entry_too_large" in str(exc.value)


def test_write_entry_rejects_dangling_supersedes(store: KBStore):
    entry = KBEntry(
        slug="replaces",
        title="t",
        type="reference",
        topic="payment",
        body="# x\n",
        supersedes="does-not-exist",
    )
    with pytest.raises(InvalidEntry) as exc:
        store.write_entry(entry, agent="dev_agent")
    assert "invalid_supersedes" in str(exc.value)


def test_write_entry_server_stamps_override_agent_supplied(store: KBStore):
    entry = KBEntry(
        slug="stamped",
        title="t",
        type="reference",
        topic="payment",
        body="# x\n",
        authored_by="malicious-spoof",
        authored_at="1970-01-01T00:00:00Z",
    )
    written = store.write_entry(entry, agent="dev_agent")
    assert written.authored_by == "dev_agent"
    assert written.authored_at != "1970-01-01T00:00:00Z"


def test_list_entries_returns_summaries(store: KBStore):
    for slug, topic, typ in [
        ("visa-a", "visa", "reference"),
        ("visa-b", "visa", "reference"),
        ("pay-c", "payment", "reference"),
        ("precedent-d", "payment", "precedent"),
    ]:
        store.write_entry(
            KBEntry(slug=slug, title=slug.title(), type=typ, topic=topic, body="# x\n"),
            agent="dev_agent",
        )
    summaries = store.list_entries()
    assert len(summaries) == 4
    assert {s.slug for s in summaries} == {"visa-a", "visa-b", "pay-c", "precedent-d"}


def test_list_entries_filter_by_topic(store: KBStore):
    for slug, topic in [("visa-a", "visa"), ("pay-c", "payment")]:
        store.write_entry(
            KBEntry(slug=slug, title=slug, type="reference", topic=topic, body="# x\n"),
            agent="dev_agent",
        )
    assert [s.slug for s in store.list_entries(topic="visa")] == ["visa-a"]


def test_list_entries_filter_by_type(store: KBStore):
    store.write_entry(
        KBEntry(slug="ref-a", title="t", type="reference", topic="x", body="# x\n"),
        agent="dev_agent",
    )
    store.write_entry(
        KBEntry(slug="prec-a", title="t", type="precedent", topic="x", body="# x\n"),
        agent="dev_agent",
    )
    assert [s.slug for s in store.list_entries(type="precedent")] == ["prec-a"]


def test_update_entry_preserves_author_stamps_updated_by(store: KBStore):
    original = store.write_entry(
        KBEntry(slug="u1", title="t", type="reference", topic="x", body="# first\n"),
        agent="dev_agent",
    )
    revised = KBEntry(
        slug="u1",
        title="t revised",
        type="reference",
        topic="x",
        body="# second\n",
    )
    updated = store.update_entry(revised, agent="qa_engineer")
    assert updated.title == "t revised"
    assert updated.authored_by == "dev_agent"
    assert updated.authored_at == original.authored_at
    assert updated.updated_by == "qa_engineer"
    assert updated.updated_at >= original.updated_at


def test_update_entry_raises_notfound_on_missing_slug(store: KBStore):
    with pytest.raises(NotFound):
        store.update_entry(
            KBEntry(slug="missing", title="t", type="reference", topic="x", body="# x\n"),
            agent="dev_agent",
        )


def test_read_entry_raises_notfound_on_missing_slug(store: KBStore):
    with pytest.raises(NotFound):
        store.read_entry("ghost")


def test_find_near_duplicates_title_similarity(store: KBStore):
    store.write_entry(
        KBEntry(
            slug="alipay-refund-endpoint",
            title="Alipay v3 refund endpoint quirks",
            type="reference",
            topic="payment",
            body="# x\n",
        ),
        agent="dev_agent",
    )
    candidates = store.find_near_duplicates(
        title="Alipay v3 refund endpoint gotchas", tags=["alipay"]
    )
    assert len(candidates) >= 1
    assert candidates[0].slug == "alipay-refund-endpoint"
    assert candidates[0].similarity > 0.7


def test_find_near_duplicates_tag_overlap(store: KBStore):
    store.write_entry(
        KBEntry(
            slug="payment-a",
            title="Nothing alike here at all",
            type="reference",
            topic="payment",
            tags=["alipay", "refund", "v3"],
            body="# x\n",
        ),
        agent="dev_agent",
    )
    # Title dissimilar but 2 tags overlap → still a candidate
    candidates = store.find_near_duplicates(
        title="Totally different subject matter",
        tags=["alipay", "refund"],
    )
    assert [c.slug for c in candidates] == ["payment-a"]


def test_find_near_duplicates_returns_empty_on_distinct(store: KBStore):
    store.write_entry(
        KBEntry(slug="visa-a", title="Visa rule A", type="reference", topic="visa", body="# x\n"),
        agent="dev_agent",
    )
    assert store.find_near_duplicates(title="Restaurant opening hours", tags=[]) == []


def test_search_ranks_title_hits_above_body_hits(store: KBStore):
    store.write_entry(
        KBEntry(
            slug="alipay-body",
            title="Unrelated title about transit",
            type="reference",
            topic="payment",
            body="# x\n\nRefund flow on Alipay v3 endpoint.\n",
        ),
        agent="dev_agent",
    )
    store.write_entry(
        KBEntry(
            slug="alipay-title",
            title="Alipay refund endpoint reference",
            type="reference",
            topic="payment",
            body="# x\n\nBody says nothing useful.\n",
        ),
        agent="dev_agent",
    )
    hits = store.search("Alipay refund")
    assert hits[0].slug == "alipay-title"  # title hit ranks first


def test_search_returns_empty_on_no_match(store: KBStore):
    store.write_entry(
        KBEntry(slug="a", title="t", type="reference", topic="x", body="# nothing here\n"),
        agent="dev_agent",
    )
    assert store.search("QuantumFluxCapacitor") == []


def test_delete_entry_removes_file(store: KBStore):
    store.write_entry(
        KBEntry(slug="rm-me", title="t", type="reference", topic="x", body="# x\n"),
        agent="dev_agent",
    )
    assert store.path_for("rm-me").exists()
    store.delete_entry("rm-me")
    assert not store.path_for("rm-me").exists()


def test_delete_entry_raises_notfound(store: KBStore):
    with pytest.raises(NotFound):
        store.delete_entry("ghost")


def test_regenerate_index_groups_by_topic_alphabetical(store: KBStore):
    for slug, topic in [
        ("visa-b", "visa"),
        ("visa-a", "visa"),
        ("pay-c", "payment"),
    ]:
        store.write_entry(
            KBEntry(slug=slug, title=slug.title(), type="reference", topic=topic, body="# x\n"),
            agent="dev_agent",
        )
    store.regenerate_index()
    index = (store.root / "_index.md").read_text()
    # Topics alphabetized; within topic slugs alphabetized; both topics present
    assert index.index("## payment") < index.index("## visa")
    assert index.index("`visa-a`") < index.index("`visa-b`")
    assert "`pay-c`" in index


def test_regenerate_index_handles_empty_store(tmp_path: Path):
    empty = KBStore(tmp_path / "empty-kb")
    empty.regenerate_index()
    index = (empty.root / "_index.md").read_text()
    assert "Knowledge Base Index" in index


# ---------------------------------------------------------------------------
# Dashboard-summary helpers: count_entries_created_since +
# list_entries_created_since (see src/orchestrator/dashboard_summary.py)
# ---------------------------------------------------------------------------


def _backdate_authored_at(store: KBStore, slug: str, dt: datetime) -> None:
    """Rewrite the on-disk file so its frontmatter ``authored_at`` matches dt.

    The store stamps `authored_at` with `datetime.now(utc)` on write, so to
    test the time-range filter deterministically we re-serialize after the
    fact through the store's own primitives.
    """
    entry = store.read_entry(slug)
    entry.authored_at = dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    # _serialize round-trips all frontmatter fields including authored_at.
    store._atomic_write(store.path_for(slug), store._serialize(entry))


def test_count_entries_created_since_empty_store(store: KBStore):
    since = datetime(2026, 5, 23, 0, 0, 0, tzinfo=timezone.utc)
    assert store.count_entries_created_since(since) == 0


def test_count_entries_created_since_counts_only_after(store: KBStore):
    now = datetime(2026, 5, 30, 12, 0, 0, tzinfo=timezone.utc)
    week_ago = now - timedelta(days=7)
    eight_days_ago = now - timedelta(days=8)
    one_day_ago = now - timedelta(days=1)

    store.write_entry(
        KBEntry(slug="old-one", title="Old", type="reference", topic="x", body="# x\n"),
        agent="dev_agent",
    )
    store.write_entry(
        KBEntry(slug="new-one", title="New", type="reference", topic="x", body="# x\n"),
        agent="dev_agent",
    )
    _backdate_authored_at(store, "old-one", eight_days_ago)
    _backdate_authored_at(store, "new-one", one_day_ago)

    # since = exactly 7 days ago; only the one-day-ago entry qualifies.
    assert store.count_entries_created_since(week_ago) == 1


def test_count_entries_created_since_boundary_inclusive(store: KBStore):
    """An entry whose authored_at equals `since` exactly counts as included."""
    boundary = datetime(2026, 5, 30, 0, 0, 0, tzinfo=timezone.utc)
    store.write_entry(
        KBEntry(slug="boundary", title="t", type="reference", topic="x", body="# x\n"),
        agent="dev_agent",
    )
    _backdate_authored_at(store, "boundary", boundary)
    assert store.count_entries_created_since(boundary) == 1


def test_list_entries_created_since_returns_dashboard_shape(store: KBStore):
    now = datetime(2026, 5, 30, 12, 0, 0, tzinfo=timezone.utc)
    week_ago = now - timedelta(days=7)
    eight_days_ago = now - timedelta(days=8)
    two_days_ago = now - timedelta(days=2)
    one_day_ago = now - timedelta(days=1)

    store.write_entry(
        KBEntry(slug="old-one", title="Old One", type="reference", topic="x", body="# x\n"),
        agent="dev_agent",
    )
    store.write_entry(
        KBEntry(slug="newer", title="Newer", type="reference", topic="x", body="# x\n"),
        agent="dev_agent",
    )
    store.write_entry(
        KBEntry(slug="newest", title="Newest", type="precedent", topic="y", body="# x\n"),
        agent="dev_agent",
    )
    _backdate_authored_at(store, "old-one", eight_days_ago)
    _backdate_authored_at(store, "newer", two_days_ago)
    _backdate_authored_at(store, "newest", one_day_ago)

    rows = store.list_entries_created_since(week_ago)
    # The 8-day-old entry is excluded; the other two are present, newest first.
    assert [r["slug"] for r in rows] == ["newest", "newer"]
    first = rows[0]
    # Required dashboard shape: slug, title, type, created_at (ISO-parseable).
    assert set(first.keys()) >= {"slug", "title", "type", "created_at"}
    assert first["title"] == "Newest"
    assert first["type"] == "precedent"
    # dashboard_summary.py calls datetime.fromisoformat(entry["created_at"]).
    parsed = datetime.fromisoformat(first["created_at"].replace("Z", "+00:00"))
    assert parsed.tzinfo is not None


def test_list_entries_created_since_empty_when_none_match(store: KBStore):
    now = datetime(2026, 5, 30, 12, 0, 0, tzinfo=timezone.utc)
    eight_days_ago = now - timedelta(days=8)
    store.write_entry(
        KBEntry(slug="ancient", title="t", type="reference", topic="x", body="# x\n"),
        agent="dev_agent",
    )
    _backdate_authored_at(store, "ancient", eight_days_ago)
    week_ago = now - timedelta(days=7)
    assert store.list_entries_created_since(week_ago) == []
