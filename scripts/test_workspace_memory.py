"""Focused no-launch memory bootstrap checks; see docs/local-ci.md."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from runtime.config import Settings
from runtime.infrastructure.learnings_store import MemoryItem, MemoryStore
from runtime.orchestrator._paths import OrgPaths
from runtime.orchestrator.workspace_adapters import (
    CodexWorkspaceAdapter,
    PersistentWorkspaceSetup,
    _memory_bootstrap_section,
)


def add(store: MemoryStore, number: int, **fields: str) -> None:
    store.write_entry(MemoryItem(
        id=f"MEM-{number:03d}", slug=f"memory-{number}",
        title=fields.pop("title", f"Synthetic memory {number}"),
        topic=fields.pop("topic", f"topic-{number:03d}-分類"),
        body="Synthetic body stays on disk.", **fields,
    ), agent="synthetic")


def block(workspace: Path) -> str:
    return "\n".join(_memory_bootstrap_section(workspace)) + "\n"


@pytest.mark.parametrize("directive_count,title_chars", [(0, 20), (3, 20), (180, 20), (1, 2000)])
def test_memory_bootstrap_utf8_budget_and_whole_line_omissions(
    tmp_path: Path, directive_count: int, title_chars: int,
) -> None:
    store = MemoryStore(tmp_path / "memory")
    for number in range(1, 181):
        add(store, number, title="界" * title_chars + f" {number}",
            provenance="directive" if number <= directive_count else "experiential")
    store.regenerate_index()
    index_before = (store.root / "_index.md").read_bytes()
    rendered = block(tmp_path)
    assert len(rendered.encode("utf-8")) <= 4096
    assert rendered == block(tmp_path)
    assert (store.root / "_index.md").read_bytes() == index_before
    topic_lines = [line for line in rendered.splitlines() if line.startswith("- topic-")]
    assert topic_lines == [f"- topic-{n:03d}-分類 (1)" for n in range(1, len(topic_lines) + 1)]
    omitted = re.search(r"\.\.\. (\d+) more topics; run memory search", rendered)
    assert omitted and int(omitted[1]) + len(topic_lines) == 180
    directive_lines = [line for line in rendered.splitlines() if line.startswith("- `MEM-")]
    assert directive_lines == [
        f"- `MEM-{n:03d}` — " + "界" * title_chars + f" {n}"
        for n in range(1, len(directive_lines) + 1)
    ]
    directive_omitted = re.search(r"\.\.\. (\d+) more directives; run memory search", rendered)
    assert len(directive_lines) + (int(directive_omitted[1]) if directive_omitted else 0) == directive_count
    for verb in ("search", "get", "add", "update", "promote"):
        assert f"happyranch memory {verb} --org <slug> --agent <you>" in rendered
    prompt = CodexWorkspaceAdapter(Settings(), OrgPaths(tmp_path), slug="synthetic").render_agents_md(
        tmp_path, "synthetic", "Synthetic role.\n" * 400,
    )
    assert len(prompt.encode("utf-8")) < 32768
    for header in ("## Knowledge Base", "## Shared Artifacts", "## Task Recall", "## Workflow"):
        assert header in prompt


def test_memory_bootstrap_reads_current_entries_even_without_index(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory")
    add(store, 1, topic="current", title="Current directive", provenance="directive")
    add(store, 2, topic="current")
    add(store, 3, topic="hidden", provenance="directive", lifecycle="evicted")
    assert not (store.root / "_index.md").exists()
    rendered = block(tmp_path)
    assert "- current (2)" in rendered
    assert "- `MEM-001` — Current directive" in rendered
    assert "MEM-002" not in rendered
    assert "MEM-003" not in rendered
    assert "hidden" not in rendered
    assert "memory/_index.md" in rendered
    # A stale index must not become the source of the topic/directive overview.
    (store.root / "_index.md").write_text("## stale (99)\n")
    assert block(tmp_path) == rendered


def test_bootstrap_repairs_stale_index(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory")
    add(store, 1)
    store.regenerate_index()
    add(store, 2, topic="new-topic")
    PersistentWorkspaceSetup(Settings()).ensure(tmp_path, "synthetic")
    index = (store.root / "_index.md").read_text()
    assert "## new-topic (1)" in index
    assert "`MEM-002`" in index


@pytest.mark.parametrize("id_line", ["id: null\n", "id: 7\n", "id: malformed\n", ""],
                         ids=["null", "nonstring", "malformed", "missing"])
@pytest.mark.parametrize("existing_index", [False, True], ids=["missing-index", "existing-index"])
@pytest.mark.parametrize("operation", ["render", "bootstrap"])
def test_historical_memory_ids_at_public_workspace_boundaries(
    tmp_path: Path, id_line: str, existing_index: bool, operation: str,
) -> None:
    store = MemoryStore(tmp_path / "memory")
    add(store, 1, topic="historical", title="Historical row")
    add(store, 2, topic="historical", title="Valid neighbor", provenance="directive")
    if existing_index:
        store.regenerate_index()
    # Replay an accepted historical file, not input to the strict current writer.
    historical = store.path_for("MEM-001", "memory-1")
    historical.write_text(historical.read_text().replace("id: MEM-001\n", id_line, 1))
    entries_before = {p.name: p.read_bytes() for p in store.root.glob("MEM-*.md")}
    digest = store.render_memory_digest("historical neighbor", budget=4096)
    assert "Historical row" in digest.text
    assert digest.digest_ids == ("MEM-002",)

    adapter = CodexWorkspaceAdapter(Settings(), OrgPaths(tmp_path), slug="synthetic")
    if operation == "render":
        prompt = adapter.render_agents_md(tmp_path, "synthetic", "Synthetic role.")
    else:
        adapter.ensure_workspace_ready(tmp_path, "synthetic", "Synthetic role.")
        prompt = (tmp_path / "AGENTS.md").read_text()
        index = (store.root / "_index.md").read_text()
        assert "## historical (2)" in index
        assert "Historical row" in index
        assert index.index("Valid neighbor") < index.index("Historical row")
        assert (tmp_path / "CLAUDE.md").resolve() == tmp_path / "AGENTS.md"
    assert "- historical (2)" in prompt
    assert "- `MEM-002` — Valid neighbor" in prompt
    assert "## Workflow" in prompt
    assert {p.name: p.read_bytes() for p in store.root.glob("MEM-*.md")} == entries_before


def test_legacy_flat_memory_is_bounded_without_changing_file(tmp_path: Path) -> None:
    flat = tmp_path / "learnings.md"
    original = "Legacy memory 界\n" * 10000
    flat.write_text(original)
    rendered = block(tmp_path)
    assert len(rendered.encode("utf-8")) <= 4096
    assert original not in rendered
    assert "learnings.md" in rendered
    assert 'happyranch memory --org <slug> --task-id <task_id> --session-id <session_id> --agent <you> --text' in rendered
    assert flat.read_text() == original
    assert not (tmp_path / "memory").exists()
