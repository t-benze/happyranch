"""Golden-file tests for the THR-032 Phase R thorough rename + data migration.

These tests are the load-bearing contract for the ``learnings/`` -> ``memory/``
directory move and the ``LRN-`` -> ``MEM-`` id rename. Each of the five §7.2
integrity safeguards has a dedicated assertion here:

    (1) no data loss        -> test_migration_is_content_equivalent
    (2) cross-refs resolve   -> test_cross_references_resolve_after_migration
    (3) LRN- and MEM- alias  -> test_lrn_and_mem_ids_resolve_to_same_item
    (4) idempotent re-run     -> test_migration_is_idempotent
    (5) audit immutability    -> test_historical_audit_rows_untouched_forward_only

Plus the v0/v1/pre-migration no-regression paths (§7.4).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from runtime.infrastructure.learnings_store import (
    ID_RE,
    MemoryItem,
    MemoryStore,
)
from runtime.infrastructure.memory_migration import migrate_workspace


# --------------------------------------------------------------------------
# Fixture: a REAL pre-rename workspace — learnings/ dir with several LRN-NNN
# entries, cross-refs (related_to / supersedes / [[…]] / free-prose), and a
# promoted entry.
# --------------------------------------------------------------------------

def _write_learning(learnings: Path, name: str, body: str) -> None:
    (learnings / name).write_text(body)


@pytest.fixture
def pre_rename_workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "agent_ws"
    learnings = ws / "learnings"
    learnings.mkdir(parents=True)

    # LRN-001 — plain experiential, referenced by others.
    _write_learning(learnings, "LRN-001-base-fact.md", (
        "---\n"
        "id: LRN-001\n"
        "slug: base-fact\n"
        "title: Base fact about worktrees\n"
        "topic: git-worktrees\n"
        "tags:\n- worktree\n- git\n"
        "authored_by: dev_agent\n"
        "authored_at: 2026-06-01T00:00:00Z\n"
        "updated_by: dev_agent\n"
        "updated_at: 2026-06-01T00:00:00Z\n"
        "---\n\n"
        "The base fact. See also nothing yet.\n"
    ))

    # LRN-002 — relates to LRN-001 via related_to AND a [[LRN-001]] body ref.
    _write_learning(learnings, "LRN-002-builds-on-base.md", (
        "---\n"
        "id: LRN-002\n"
        "slug: builds-on-base\n"
        "title: Builds on the base fact\n"
        "topic: git-worktrees\n"
        "tags:\n- worktree\n"
        "related_to:\n- LRN-001\n"
        "authored_by: dev_agent\n"
        "authored_at: 2026-06-02T00:00:00Z\n"
        "updated_by: dev_agent\n"
        "updated_at: 2026-06-02T00:00:00Z\n"
        "---\n\n"
        "This extends [[LRN-001]]. Per LRN-001 the worktree is isolated.\n"
    ))

    # LRN-003 — supersedes LRN-002, free-prose ref to LRN-001 and LRN-002.
    _write_learning(learnings, "LRN-003-supersede-002.md", (
        "---\n"
        "id: LRN-003\n"
        "slug: supersede-002\n"
        "title: Supersedes the builds-on entry\n"
        "topic: git-worktrees\n"
        "supersedes: LRN-002\n"
        "authored_by: dev_agent\n"
        "authored_at: 2026-06-03T00:00:00Z\n"
        "updated_by: dev_agent\n"
        "updated_at: 2026-06-03T00:00:00Z\n"
        "---\n\n"
        "Replaces LRN-002; still consistent with LRN-001.\n"
    ))

    # LRN-004 — a PROMOTED entry (locked, body is a stub, promoted_to = kb slug).
    _write_learning(learnings, "LRN-004-promoted.md", (
        "---\n"
        "id: LRN-004\n"
        "slug: promoted\n"
        "title: A promoted learning\n"
        "topic: testing\n"
        "promoted_to: some-kb-precedent\n"
        "authored_by: dev_agent\n"
        "authored_at: 2026-06-04T00:00:00Z\n"
        "updated_by: dev_agent\n"
        "updated_at: 2026-06-04T00:00:00Z\n"
        "---\n\n"
        "See KB precedent: `some-kb-precedent`.\n\n"
        "_Promoted from local learning LRN-004 on 2026-06-04T00:00:00Z._\n"
    ))

    # Pre-existing index (must be regenerated under memory/).
    MemoryStore(learnings).regenerate_index()
    return ws


def _items_by_number(store: MemoryStore) -> dict[int, MemoryItem]:
    out: dict[int, MemoryItem] = {}
    for summary in store.list_entries():
        item = store.read_entry(summary.id)
        out[int(item.id.split("-", 1)[1])] = item
    return out


# --------------------------------------------------------------------------
# Safeguard (1): no data loss — content equivalence.
# --------------------------------------------------------------------------

def test_migration_is_content_equivalent(pre_rename_workspace: Path) -> None:
    ws = pre_rename_workspace
    pre = _items_by_number(MemoryStore(ws / "learnings"))

    result = migrate_workspace(ws)

    assert result["migrated"] is True
    assert result["count"] == 4
    assert (ws / "memory").is_dir()
    assert not (ws / "learnings").exists()  # moved, not copied

    post = _items_by_number(MemoryStore(ws / "memory"))
    assert set(pre) == set(post)  # same set of entry numbers, none lost
    for n, pre_item in pre.items():
        post_item = post[n]
        assert post_item.id == f"MEM-{n:03d}"
        # Non-link content preserved verbatim.
        assert post_item.title == pre_item.title
        assert post_item.topic == pre_item.topic
        assert post_item.tags == pre_item.tags
        assert post_item.authored_by == pre_item.authored_by
        assert post_item.authored_at == pre_item.authored_at
        assert post_item.updated_at == pre_item.updated_at
        # The promoted link is a KB slug — never rewritten to MEM-.
        assert post_item.promoted_to == pre_item.promoted_to


# --------------------------------------------------------------------------
# Safeguard (2): every cross-reference resolves after migration.
# --------------------------------------------------------------------------

def test_cross_references_resolve_after_migration(pre_rename_workspace: Path) -> None:
    ws = pre_rename_workspace
    migrate_workspace(ws)
    store = MemoryStore(ws / "memory")

    item2 = store.read_entry("MEM-002")
    assert item2.related_to == ["MEM-001"]
    assert "[[MEM-001]]" in item2.body
    assert "Per MEM-001" in item2.body
    assert "LRN-001" not in item2.body  # rewritten, no stragglers

    item3 = store.read_entry("MEM-003")
    assert item3.supersedes == "MEM-002"
    assert "Replaces MEM-002" in item3.body
    assert "consistent with MEM-001" in item3.body

    # Every structured ref points at an entry that actually exists.
    for item in (item2, item3):
        for ref in item.related_to:
            assert store.read_entry(ref).id == ref
        if item.supersedes:
            assert store.read_entry(item.supersedes).id == item.supersedes


# --------------------------------------------------------------------------
# Safeguard (3): LRN-<n> and MEM-<n> both resolve to the SAME item, forever.
# --------------------------------------------------------------------------

def test_lrn_and_mem_ids_resolve_to_same_item(pre_rename_workspace: Path) -> None:
    ws = pre_rename_workspace
    migrate_workspace(ws)
    store = MemoryStore(ws / "memory")

    for n in (1, 2, 3, 4):
        via_mem = store.read_entry(f"MEM-{n:03d}")
        via_lrn = store.read_entry(f"LRN-{n:03d}")  # permanent shim
        assert via_mem.id == via_lrn.id == f"MEM-{n:03d}"
        assert via_mem.body == via_lrn.body


def test_lrn_shim_resolves_even_without_migration(tmp_path: Path) -> None:
    """A brand-new MEM- only store still resolves an old LRN- id handed to it."""
    mem = tmp_path / "memory"
    store = MemoryStore(mem)
    entry = MemoryItem(id="MEM-007", slug="fresh", title="Fresh", topic="t", body="b")
    store.write_entry(entry, agent="dev_agent")
    assert store.read_entry("LRN-007").id == "MEM-007"
    assert store.read_entry("MEM-007").id == "MEM-007"


# --------------------------------------------------------------------------
# Safeguard (4): re-running the migration is a no-op (idempotent).
# --------------------------------------------------------------------------

def test_migration_is_idempotent(pre_rename_workspace: Path) -> None:
    ws = pre_rename_workspace
    migrate_workspace(ws)
    snapshot = {
        p.name: p.read_text()
        for p in sorted((ws / "memory").glob("*.md"))
    }

    second = migrate_workspace(ws)
    assert second["migrated"] is False
    assert second["reason"] == "already_migrated"

    after = {
        p.name: p.read_text()
        for p in sorted((ws / "memory").glob("*.md"))
    }
    assert after == snapshot  # byte-for-byte unchanged on re-run
    assert not (ws / "learnings").exists()


def test_new_ids_continue_the_number_line(pre_rename_workspace: Path) -> None:
    """After migrating LRN-001..004, next_id() yields MEM-005 (no reset to 1)."""
    ws = pre_rename_workspace
    migrate_workspace(ws)
    store = MemoryStore(ws / "memory")
    assert store.next_id() == "MEM-005"
    assert ID_RE.match("MEM-005")
    assert ID_RE.match("LRN-005")  # both prefixes are valid ids


# --------------------------------------------------------------------------
# Safeguard (5): historical log_learning_* audit rows are untouched; new
# events are emitted forward-only under the log_memory_* names.
# --------------------------------------------------------------------------

def test_historical_audit_rows_untouched_forward_only(tmp_path: Path) -> None:
    from runtime.infrastructure.audit_logger import AuditLogger
    from runtime.infrastructure.database import Database

    db = Database(tmp_path / "audit.db")
    audit = AuditLogger(db)

    # A historical learning event (as written by the pre-rename runtime).
    audit.log_learning_added(
        agent="dev_agent", id="LRN-001", slug="base-fact",
        topic="git-worktrees", tags=["git"], source_task=None,
    )
    before = _audit_rows(db)
    assert any(r["action"] == "learning_added" for r in before)

    # A forward event under the new name.
    audit.log_memory_added(
        agent="dev_agent", id="MEM-005", slug="new-one",
        topic="testing", tags=["t"], source_task=None,
    )
    after = _audit_rows(db)

    # The historical row is byte-for-byte intact (no UPDATE/rewrite).
    historical_before = [r for r in before if r["action"] == "learning_added"]
    historical_after = [r for r in after if r["action"] == "learning_added"]
    assert historical_after == historical_before

    # The new event uses the new action name, forward only.
    assert any(r["action"] == "memory_added" for r in after)
    assert not any(r["action"] == "memory_added" for r in before)


def _audit_rows(db) -> list[dict]:
    cur = db._conn.execute(
        "SELECT id, task_id, agent, action, payload, timestamp FROM audit_log ORDER BY id"
    )
    return [dict(row) for row in cur.fetchall()]


# --------------------------------------------------------------------------
# §7.4 — v0 / v1 / pre-migration no-regression paths.
# --------------------------------------------------------------------------

def test_already_memory_workspace_is_noop(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    (ws / "memory").mkdir(parents=True)
    MemoryStore(ws / "memory").regenerate_index()
    result = migrate_workspace(ws)
    assert result["migrated"] is False
    assert result["reason"] == "already_migrated"


def test_flat_learnings_md_workspace_is_not_migrated(tmp_path: Path) -> None:
    """A pre-structured flat learnings.md workspace has no dir to migrate."""
    ws = tmp_path / "ws"
    ws.mkdir(parents=True)
    (ws / "learnings.md").write_text("# Learnings: dev_agent\n\n- a flat line\n")
    result = migrate_workspace(ws)
    assert result["migrated"] is False
    assert result["reason"] == "no_structured_store"
    # The flat file is left untouched — no regression, no stranded workspace.
    assert (ws / "learnings.md").read_text().endswith("- a flat line\n")
    assert not (ws / "memory").exists()


def test_brand_new_workspace_is_not_migrated(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir(parents=True)
    result = migrate_workspace(ws)
    assert result["migrated"] is False
    assert result["reason"] == "no_structured_store"
