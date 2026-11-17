"""Focused no-launch memory bootstrap checks; see docs/operations/local-ci.md."""
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


@pytest.mark.parametrize("context", ["task", "thread", "wake", "schedule", "dream",
                                     "task-codex", "task-opencode", "task-pi"])
@pytest.mark.parametrize("case", ["stale", "unchanged", "render-failure", "write-failure", "missing", "broken-link"])
async def test_session_spawn_instruction_refresh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                               context: str, case: str) -> None:
    """Drive shipping producer prefixes; stop at skill preparation, never a provider."""
    from types import SimpleNamespace
    from runtime.daemon import dream_runner, schedule_runner, thread_runner, wake_runner
    from runtime.models import ScheduleStatus, TaskRecord
    from runtime.orchestrator import orchestrator as task_runner
    from runtime.orchestrator.workspace_adapters import ClaudeWorkspaceAdapter
    from scripts.test_agent_retirement import fixture, seed_future
    from scripts.test_thread_prompt_size import seed

    # BaseException is an observation stop outside producer error handling.
    class Prepared(BaseException):
        pass

    async with fixture(tmp_path / "org") as (org, _client):
        provider = context.split("-", 1)[1] if "-" in context else "claude"
        context = context.split("-", 1)[0]
        definition = org.root / "org" / "agents" / "worker.md"
        definition.write_text(definition.read_text().replace("executor: claude", f"executor: {provider}"))
        workspace = org.root / "workspaces" / "worker"
        pair = workspace / "AGENTS.md"
        link = workspace / "CLAUDE.md"
        pair.write_text("STALE full inline memory index\n")
        link.symlink_to("AGENTS.md")
        # Permission/settings sentinels must never participate in prompt refresh.
        (workspace / ".claude").mkdir()
        settings_path = workspace / ".claude" / "settings.json"
        settings_path.write_text('{"synthetic": "retained"}\n')
        permission_path = workspace / "opencode.json"
        permission_path.write_text('{"synthetic": "retained"}\n')
        if case == "unchanged":
            adapter_type = ClaudeWorkspaceAdapter if provider == "claude" else CodexWorkspaceAdapter
            adapter = adapter_type(org.settings, org.orchestrator._paths, slug=org.slug)
            writer = adapter.write_claude_md if provider == "claude" else adapter.write_agents_md
            writer(workspace, "worker", "Synthetic role.\n")
        import os
        os.utime(pair, ns=(1_000_000_000, 1_000_000_000))
        before = (pair.read_bytes(), pair.stat().st_mtime_ns, link.lstat().st_mtime_ns)
        if case == "missing":
            pair.unlink()
        elif case == "broken-link":
            link.unlink()
            link.symlink_to("missing.md")
        elif case == "render-failure":
            def fail(*args: object, **kwargs: object) -> None:
                raise OSError("synthetic instruction render failure")
            adapter_type = ClaudeWorkspaceAdapter if provider == "claude" else CodexWorkspaceAdapter
            renderer = "render_claude_md" if provider == "claude" else "render_agents_md"
            monkeypatch.setattr(adapter_type, renderer, fail)
        elif case == "write-failure":
            def fail_write(*args: object, **kwargs: object) -> None:
                raise OSError("synthetic instruction write failure")
            monkeypatch.setattr("runtime.orchestrator.workspace_adapters._atomic_write_regular", fail_write)
        module = {"task": task_runner, "thread": thread_runner, "wake": wake_runner,
                  "schedule": schedule_runner, "dream": dream_runner}[context]
        observed = []

        def preparation_boundary(*args: object, **kwargs: object) -> None:
            observed.append(pair.read_text() if pair.exists() else None)
            raise Prepared

        monkeypatch.setattr(module, "materialize_workspace_skills", preparation_boundary)
        monkeypatch.setattr(thread_runner, "_build_executor_for_provider", lambda *a, **k: SimpleNamespace())
        monkeypatch.setattr(org.orchestrator, "_build_executor", lambda *a: SimpleNamespace())
        # Fail immediately if any production path escapes to a provider process.
        monkeypatch.setattr("subprocess.Popen", lambda *a, **k: pytest.fail("unexpected provider launch"))
        error = ""
        try:
            if context == "task":
                org.db.insert_task(TaskRecord(id="TASK-refresh", assigned_agent="worker", team="retiring",
                                              brief="Synthetic prompt refresh"))
                org.orchestrator._run_agent_impl("TASK-refresh", "worker", "Synthetic guidance")
            elif context == "thread":
                token = seed(org, history=[], required=["Synthetic instruction refresh"])
                await thread_runner.run_invocation(org_state=org, invocation_token=token, settings=org.settings)
                error = org.db.get_invocation_any_status(token).decline_reason or ""
            else:
                seed_future(org)
                if context == "wake":
                    await wake_runner.run_wake(org_state=org, work_hour_id="WH-001", settings=org.settings)
                    error = org.db.work_hours.get("WH-001").error or ""
                elif context == "schedule":
                    org.db.schedules.update("SCH-001", status=ScheduleStatus.FIRING)
                    await schedule_runner.run_schedule(org_state=org, schedule_id="SCH-001", settings=org.settings)
                    error = org.db.schedules.get("SCH-001").error or ""
                else:
                    await dream_runner.run_dream(org_state=org, dream_id="DRM-001", settings=org.settings)
                    error = org.db.get_dream("DRM-001").error or ""
        except Prepared:
            pass
        except RuntimeError as exc:
            error = str(exc)
        assert settings_path.read_text() == '{"synthetic": "retained"}\n'
        assert permission_path.read_text() == '{"synthetic": "retained"}\n'
        if case in {"render-failure", "write-failure", "missing", "broken-link"}:
            assert not observed, "invalid/failed instruction refresh reached spawn preparation"
            assert "instruction" in error.lower(), error
            if case in {"render-failure", "write-failure"}:
                if case == "render-failure":
                    assert "synthetic instruction render failure" in error
                else:
                    # InstructionPairConflict deliberately exposes a stable public
                    # operation diagnostic, not its raw underlying exception.
                    assert error.endswith("instruction_refresh_failed for 'worker': "
                                          "instruction pair conflict at AGENTS.md: canonical write failed")
                assert (pair.read_bytes(), pair.stat().st_mtime_ns) == before[:2]
            elif case == "missing":
                assert not pair.exists()
            else:
                assert os.readlink(link) == "missing.md"
                assert pair.read_bytes() == before[0]
        else:
            assert len(observed) == 1
            assert "## System Prompt\n\nSynthetic role." in observed[0]
            assert "STALE full inline memory index" not in observed[0]
            assert os.readlink(link) == "AGENTS.md"
            if case == "unchanged":
                assert (pair.read_bytes(), pair.stat().st_mtime_ns, link.lstat().st_mtime_ns) == before
