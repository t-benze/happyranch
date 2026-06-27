"""THR-032 Phase R — workspace memory migration (learnings/ -> memory/).

The thorough rename moves the per-agent structured store from ``learnings/`` to
``memory/`` and renumbers ids ``LRN-NNN`` -> ``MEM-NNN`` (same number, new
prefix). This module is the single, isolated home of that data-touching step so
it can be golden-file tested in isolation (see tests/test_memory_migration.py).

Guarantees (§7.2(c)):
- **Lazy**: runs at workspace-setup time, per workspace, never as a top-down sweep.
- **Idempotent**: ``memory/`` already present -> no-op; safe to re-run every setup.
- **Lossless**: every entry (and any stray file) is carried over; ids and every
  cross-reference (related_to / supersedes / [[…]] / free-prose) are rewritten,
  and the permanent LRN- resolution shim in MemoryStore covers anything missed.

The migration touches files only. It performs **zero** database writes, so
historical ``log_learning_*`` audit rows are untouched by construction (§7.2(a)).
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path

from runtime.infrastructure.learnings_store import (
    CANONICAL_ID_PREFIX,
    LEGACY_ID_PREFIX,
    MemoryStore,
)

# Rewrite any standalone LRN-NNN token to MEM-NNN. The word boundaries keep
# `[[LRN-001]]`, `related_to: LRN-001`, and free prose ("per LRN-001") all in
# scope while leaving unrelated text alone. promoted_to holds a KB slug, not an
# id, so it is deliberately NOT run through this (a KB slug is never LRN-shaped).
_LRN_TOKEN_RE = re.compile(rf"\b{LEGACY_ID_PREFIX}-(\d{{3,}})\b")


def _rewrite_ids(text: str) -> str:
    return _LRN_TOKEN_RE.sub(rf"{CANONICAL_ID_PREFIX}-\1", text)


def _rewrite_entry_file(store: MemoryStore, text: str) -> tuple[str, str]:
    """Return (serialized_text, filename) for the migrated entry."""
    entry = store._parse(text)
    entry.id = _rewrite_ids(entry.id)
    entry.related_to = [_rewrite_ids(r) for r in entry.related_to]
    if entry.supersedes:
        entry.supersedes = _rewrite_ids(entry.supersedes)
    entry.body = _rewrite_ids(entry.body)
    return store._serialize(entry), f"{entry.id}-{entry.slug}.md"


def migrate_workspace(workspace: Path) -> dict:
    """Migrate ``<workspace>/learnings/`` -> ``<workspace>/memory/`` in place.

    Returns a result dict: ``{"migrated": bool, "reason": str,
    "memory_dir": Path, "count": int}``.
    """
    memory_dir = workspace / "memory"
    learnings_dir = workspace / "learnings"

    if memory_dir.exists():
        # Idempotent: already migrated (or natively created on the new layout).
        return {
            "migrated": False,
            "reason": "already_migrated",
            "memory_dir": memory_dir,
            "count": 0,
        }
    if not learnings_dir.exists():
        # Flat learnings.md / brand-new workspace: no structured store to move.
        # The flat-file bootstrap branch handles these (workspace_adapters).
        return {
            "migrated": False,
            "reason": "no_structured_store",
            "memory_dir": memory_dir,
            "count": 0,
        }

    memory_dir.mkdir(parents=True, exist_ok=True)
    store = MemoryStore(memory_dir)
    entry_prefixes = (CANONICAL_ID_PREFIX, LEGACY_ID_PREFIX)
    count = 0
    for src in sorted(learnings_dir.iterdir()):
        if not src.is_file():
            continue
        name = src.name
        if name == "_index.md":
            continue  # regenerated under memory/ below
        is_entry = any(
            name.startswith(f"{p}-") and name.endswith(".md") for p in entry_prefixes
        )
        if is_entry:
            new_text, new_name = _rewrite_entry_file(store, src.read_text())
            store._atomic_write(memory_dir / new_name, new_text)
            count += 1
        else:
            # Carry over any stray file verbatim — lossless by construction.
            shutil.copy2(src, memory_dir / name)

    store.regenerate_index()
    # The move is complete: remove the now-superseded source dir so the workspace
    # presents a single canonical memory/ store (test asserts learnings/ is gone).
    shutil.rmtree(learnings_dir)

    return {
        "migrated": True,
        "reason": "migrated",
        "memory_dir": memory_dir,
        "count": count,
    }
