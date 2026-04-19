from __future__ import annotations

from pathlib import Path

import pytest

from src.infrastructure.kb_store import (
    KBEntry,
    KBStore,
    SlugExists,
    InvalidSlug,
    InvalidEntry,
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


def test_write_entry_rejects_invalid_type(store: KBStore):
    entry = KBEntry(
        slug="bad-type",
        title="t",
        type="guide",  # not in {reference, precedent}
        topic="payment",
        body="# x\n",
    )
    with pytest.raises(InvalidEntry):
        store.write_entry(entry, agent="dev_agent")


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
