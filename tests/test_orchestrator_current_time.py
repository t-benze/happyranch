"""current_time injection into the shared agent-prompt Parameters block.

TASK-976 (THR-039): every provider's prompt carries the local wall-clock + zone,
fresh on every spawn/wake, with an injectable clock for deterministic tests.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from runtime.infrastructure.database import Database
from runtime.models import TaskStatus
from runtime.orchestrator.executors import ExecutorResult
from runtime.orchestrator.orchestrator import Orchestrator
from runtime.orchestrator.teams import TeamsRegistry

_FROZEN = datetime(2026, 6, 27, 4, 47, tzinfo=timezone.utc)  # 12:47 in +08:00

# System-contract IDs for "task" context with repos.
_TASK_CONTRACT_IDS = ["start-task", "jobs", "make-worktree", "thread"]


@pytest.fixture(autouse=True)
def _ensure_protocol_skills(test_settings):
    """TASK-2511: pre-create protocol/skills/ source dirs."""
    for sid in _TASK_CONTRACT_IDS:
        src = test_settings.get_protocol_dir() / "skills" / sid
        src.mkdir(parents=True, exist_ok=True)
        (src / "SKILL.md").write_text(f"# {sid}\n\nSkill body for {sid}.\n")


@pytest.fixture
def orch(test_settings, test_runtime):
    test_runtime.root.mkdir(parents=True, exist_ok=True)
    db = Database(test_runtime.db_path)
    teams = TeamsRegistry.load(test_runtime.root)
    return Orchestrator(
        db=db, settings=test_settings, paths=test_runtime, slug="test", teams=teams,
    )


def _write_org_tz(test_runtime, tz: str) -> None:
    path = test_runtime.org_config_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"timezone: {tz}\n")


def test_current_time_line_exact_format_with_configured_tz(orch, test_runtime) -> None:
    _write_org_tz(test_runtime, "Asia/Shanghai")
    prompt = orch._build_agent_prompt(
        "claude", "dev_agent", "TASK-1", "sess-1", "do a thing", "",
        now=lambda: _FROZEN,
    )
    assert "  current_time: 2026-06-27T12:47+08:00 (Asia/Shanghai)\n" in prompt


def test_current_time_line_machine_local_fallback_is_valid(orch) -> None:
    # No org config -> machine-local resolution; the line must still be valid.
    prompt = orch._build_agent_prompt(
        "claude", "dev_agent", "TASK-1", "sess-1", "do a thing", "",
        now=lambda: _FROZEN,
    )
    m = re.search(
        r"^  current_time: 2026-06-27T\d{2}:\d{2}[+-]\d{2}:\d{2} \(.+\)$",
        prompt,
        re.MULTILINE,
    )
    assert m is not None, prompt


@pytest.mark.parametrize("provider", ["claude", "codex", "opencode", "pi"])
def test_current_time_in_all_provider_prompts(orch, test_runtime, provider) -> None:
    _write_org_tz(test_runtime, "Asia/Shanghai")
    prompt = orch._build_agent_prompt(
        provider, "dev_agent", "TASK-1", "sess-1", "brief text", "",
        now=lambda: _FROZEN,
    )
    assert "  current_time: 2026-06-27T12:47+08:00 (Asia/Shanghai)\n" in prompt


def test_current_time_default_clock_emits_line(orch, test_runtime) -> None:
    # Omitting `now` falls back to the real UTC clock; the line must still emit.
    _write_org_tz(test_runtime, "Asia/Shanghai")
    prompt = orch._build_agent_prompt(
        "claude", "dev_agent", "TASK-1", "sess-1", "brief", "",
    )
    assert re.search(r"^  current_time: .+ \(Asia/Shanghai\)$", prompt, re.MULTILINE)


# ═══════════════════════════════════════════════════════════════════
# THR-032 Phase 2 — PUSH memory digest prompt injection
# ═══════════════════════════════════════════════════════════════════


class TestMemoryDigestPromptInjection:
    """Verify the MEMORY-DIGEST block is injected into _build_agent_prompt
    when non-empty and omitted when empty/None."""

    @pytest.fixture
    def orch(self, test_settings, test_runtime):
        test_runtime.root.mkdir(parents=True, exist_ok=True)
        db = Database(test_runtime.db_path)
        teams = TeamsRegistry.load(test_runtime.root)
        return Orchestrator(
            db=db, settings=test_settings, paths=test_runtime, slug="test",
            teams=teams,
        )

    _SAMPLE_DIGEST = (
        "=== MEMORY-DIGEST (system) ===\n"
        "Relevant memory (pointers only — "
        "fetch bodies with `happyranch memory get <id>`):\n"
        "\n"
        "- `MEM-001` — Test Memory  (experiential, salience 90)\n"
    )

    def test_digest_injected_when_non_empty(self, orch):
        """When a non-empty digest is passed, it appears after brief/role_guidance."""
        prompt = orch._build_agent_prompt(
            "claude", "dev_agent", "TASK-1", "sess-1", "do a thing", "",
            memory_digest=self._SAMPLE_DIGEST,
        )
        assert "=== MEMORY-DIGEST (system) ===" in prompt
        assert "MEM-001" in prompt
        # Digest should appear after the brief line
        brief_idx = prompt.index("brief: do a thing")
        digest_idx = prompt.index("=== MEMORY-DIGEST")
        assert digest_idx > brief_idx

    def test_digest_omitted_when_none(self, orch):
        """When memory_digest is None, no MEMORY-DIGEST block appears."""
        prompt = orch._build_agent_prompt(
            "claude", "dev_agent", "TASK-1", "sess-1", "do a thing", "",
            memory_digest=None,
        )
        assert "MEMORY-DIGEST" not in prompt

    def test_digest_omitted_when_empty_string(self, orch):
        """When memory_digest is an empty string, no MEMORY-DIGEST block appears."""
        prompt = orch._build_agent_prompt(
            "claude", "dev_agent", "TASK-1", "sess-1", "do a thing", "",
            memory_digest="",
        )
        assert "MEMORY-DIGEST" not in prompt

    def test_digest_injected_for_all_providers(self, orch):
        """The digest must appear for every provider (harness-agnostic)."""
        for provider in ("claude", "codex", "opencode", "pi"):
            prompt = orch._build_agent_prompt(
                provider, "dev_agent", "TASK-1", "sess-1", "brief", "",
                memory_digest=self._SAMPLE_DIGEST,
            )
            assert "=== MEMORY-DIGEST (system) ===" in prompt, f"missing for {provider}"

    def test_digest_does_not_interfere_with_role_guidance(self, orch):
        """When prompt (role_guidance) is non-empty, the digest still appears
        after it without interfering."""
        prompt = orch._build_agent_prompt(
            "claude", "engineering_head", "TASK-1", "sess-1",
            "manager brief", "Manager capabilities here",
            memory_digest=self._SAMPLE_DIGEST,
        )
        assert "role_guidance:" in prompt
        assert "Manager capabilities here" in prompt
        assert "=== MEMORY-DIGEST (system) ===" in prompt
        # role_guidance block comes before digest
        rg_idx = prompt.index("role_guidance:")
        digest_idx = prompt.index("=== MEMORY-DIGEST")
        assert digest_idx > rg_idx


# ── THR-032 Phase 2: real _run_agent launch-path tests ──

class TestRunAgentMemoryDigest:
    """Exercise the full _run_agent path: org config → MemoryStore →
    ancestor chain → build_memory_digest → _build_agent_prompt →
    captured full_prompt through a fake executor."""

    @pytest.fixture
    def orch(self, test_settings, test_runtime):
        test_runtime.root.mkdir(parents=True, exist_ok=True)
        db = Database(test_runtime.db_path)
        teams = TeamsRegistry.load(test_runtime.root)
        return Orchestrator(
            db=db, settings=test_settings, paths=test_runtime, slug="test",
            teams=teams,
        )

    def _setup_ws(self, test_runtime, agent: str = "dev_agent"):
        """Set up a workspace with start-task skill marker."""
        ws = test_runtime.workspaces_dir / agent
        ws.mkdir(parents=True, exist_ok=True)
        (ws / "task_history.md").write_text(f"# Task History: {agent}\n\n")
        skill = ws / ".claude" / "skills" / "start-task"
        skill.mkdir(parents=True, exist_ok=True)
        (skill / "SKILL.md").write_text("# start-task\n")

    def _seed_memory_store(self, test_runtime, agent: str = "dev_agent"):
        """Create a memory/ dir with seeded items and return the store root."""
        from runtime.infrastructure.learnings_store import MemoryStore, MemoryItem
        ws = test_runtime.workspaces_dir / agent
        mem_dir = ws / "memory"
        mem_dir.mkdir(parents=True, exist_ok=True)
        store = MemoryStore(mem_dir)
        items = [
            MemoryItem(
                id="MEM-001", slug="worktree-trap",
                title="Worktree edit-path trap",
                topic="git-worktrees",
                provenance="experiential", scope="agent",
                salience=90,
                body="Always edit inside the worktree checkout, not the main clone.",
            ),
            MemoryItem(
                id="MEM-002", slug="directive-rule",
                title="Never force-push to main",
                topic="workflow",
                provenance="directive", scope="agent",
                salience=70,
                body="Founder rule: never force-push to main.",
            ),
            MemoryItem(
                id="MEM-003", slug="ci-lockfile",
                title="CI lockfile frozen constraint",
                topic="ci",
                provenance="experiential", scope="agent",
                salience=60,
                source_task="TASK-100",
                body="Adding a Python minor to CI needs uv.lock re-lock.",
            ),
        ]
        for item in items:
            store.write_entry(item, agent=agent)
        return store

    def _write_org_config(self, test_runtime, budget: int):
        """Write org config with memory_digest_budget."""
        cfg_path = test_runtime.org_config_path
        cfg_path.parent.mkdir(parents=True, exist_ok=True)
        cfg_path.write_text(f"memory_digest_budget: {budget}\n")

    def test_budget_zero_disables_digest_in_run_agent(self, orch, test_runtime, monkeypatch):
        """When memory_digest_budget=0, _run_agent does NOT inject a
        MEMORY-DIGEST block into the full prompt."""
        self._setup_ws(test_runtime)
        self._seed_memory_store(test_runtime)
        self._write_org_config(test_runtime, budget=0)
        task_id = orch.create_task("Test memory digest budget=0")
        monkeypatch.setattr(orch, "_build_session_id", lambda: "sess-test")

        mock_executor = MagicMock()
        mock_executor.run.return_value = ExecutorResult(
            success=True, duration_seconds=1, session_id="sess-test",
        )
        with patch.object(orch, "_build_executor", return_value=mock_executor):
            orch._run_agent(task_id, "dev_agent", "")
        prompt = mock_executor.run.call_args.kwargs["prompt"]

        assert "MEMORY-DIGEST" not in prompt
        assert "Test memory digest budget=0" in prompt

    def test_missing_memory_dir_omits_digest(self, orch, test_runtime, monkeypatch):
        """When workspace memory/ dir does not exist, no digest is injected."""
        self._setup_ws(test_runtime)
        # Do NOT seed memory dir
        self._write_org_config(test_runtime, budget=1500)
        task_id = orch.create_task("Test without memory dir")
        monkeypatch.setattr(orch, "_build_session_id", lambda: "sess-test")

        mock_executor = MagicMock()
        mock_executor.run.return_value = ExecutorResult(
            success=True, duration_seconds=1, session_id="sess-test",
        )
        with patch.object(orch, "_build_executor", return_value=mock_executor):
            orch._run_agent(task_id, "dev_agent", "")
        prompt = mock_executor.run.call_args.kwargs["prompt"]

        assert "MEMORY-DIGEST" not in prompt

    def test_digest_injected_with_seeded_memory(self, orch, test_runtime, monkeypatch):
        """When memory/ dir exists with valid items and budget > 0,
        the MEMORY-DIGEST block is injected into the full prompt."""
        self._setup_ws(test_runtime)
        self._seed_memory_store(test_runtime)
        self._write_org_config(test_runtime, budget=2000)
        task_id = orch.create_task("Test memory digest injection")
        monkeypatch.setattr(orch, "_build_session_id", lambda: "sess-test")

        mock_executor = MagicMock()
        mock_executor.run.return_value = ExecutorResult(
            success=True, duration_seconds=1, session_id="sess-test",
        )
        with patch.object(orch, "_build_executor", return_value=mock_executor):
            orch._run_agent(task_id, "dev_agent", "")
        prompt = mock_executor.run.call_args.kwargs["prompt"]

        assert "=== MEMORY-DIGEST (system) ===" in prompt
        assert "MEM-001" in prompt
        assert "Worktree edit-path trap" in prompt
        # Must be pointer-only — no bodies
        assert "Always edit inside the worktree checkout" not in prompt

    def test_directive_scope_agent_boosted_in_full_prompt(self, orch, test_runtime, monkeypatch):
        """Agent-scope directive items get directive boost and rank above
        equal-salience experiential items in the full prompt."""
        self._setup_ws(test_runtime)
        # Seed with items where directive has same base salience as experiential
        from runtime.infrastructure.learnings_store import MemoryStore, MemoryItem
        ws = test_runtime.workspaces_dir / "dev_agent"
        mem_dir = ws / "memory"
        mem_dir.mkdir(parents=True, exist_ok=True)
        store = MemoryStore(mem_dir)
        store.write_entry(MemoryItem(
            id="MEM-010", slug="exp", title="Experiential Item",
            topic="test",
            provenance="experiential", scope="agent", salience=50,
            body="Some experiential body.",
        ), agent="dev_agent")
        store.write_entry(MemoryItem(
            id="MEM-011", slug="dir", title="Directive Rule",
            topic="test",
            provenance="directive", scope="agent", salience=50,
            body="Some directive body.",
        ), agent="dev_agent")
        store.write_entry(MemoryItem(
            id="MEM-012", slug="dirteam", title="Team Directive",
            topic="test",
            provenance="directive", scope="team", salience=50,
            body="Team-scoped directive.",
        ), agent="dev_agent")

        self._write_org_config(test_runtime, budget=2000)
        task_id = orch.create_task("Test directive scope boost")
        monkeypatch.setattr(orch, "_build_session_id", lambda: "sess-test")

        mock_executor = MagicMock()
        mock_executor.run.return_value = ExecutorResult(
            success=True, duration_seconds=1, session_id="sess-test",
        )
        with patch.object(orch, "_build_executor", return_value=mock_executor):
            orch._run_agent(task_id, "dev_agent", "")
        prompt = mock_executor.run.call_args.kwargs["prompt"]

        assert "=== MEMORY-DIGEST (system) ===" in prompt
        # Agent-scope directive (MEM-011) gets +10 boost → ranks above experiential (MEM-010)
        idx_dir = prompt.index("MEM-011")
        idx_exp = prompt.index("MEM-010")
        assert idx_dir < idx_exp
        # Team-scope directive does NOT get boost → ties with experiential on title order
        idx_team = prompt.index("MEM-012")
        # 'Experiential Item' < 'Team Directive' alphabetically
        assert idx_exp < idx_team

    def test_ancestor_boost_visible_in_full_prompt(self, orch, test_runtime, monkeypatch):
        """MEM-003 has source_task=TASK-100 and salience 60. When the current
        task's ancestor chain contains TASK-100, MEM-003 gets +20 ancestor
        boost (effective 80), ranking above MEM-002 (base salience 80,
        provenance='reflective' — no directive boost — so both land in the
        pointer group; alphabetical tie-break: 'C...' < 'Z...', so MEM-003
        first).  MEM-002 is intentionally NOT a directive so it doesn't get
        pulled into the directive-first full-body group (WS-B THR-091 seq7).
        """
        self._setup_ws(test_runtime)
        # Only seed MEM-002 (salience 80, reflective, agent scope — no boost)
        # and MEM-003 (salience 60, source_task=TASK-100). No MEM-001.
        from runtime.infrastructure.learnings_store import MemoryStore, MemoryItem
        ws = test_runtime.workspaces_dir / "dev_agent"
        mem_dir = ws / "memory"
        mem_dir.mkdir(parents=True, exist_ok=True)
        store = MemoryStore(mem_dir)
        store.write_entry(MemoryItem(
            id="MEM-002", slug="dir", title="Zebra habit",
            topic="workflow",
            provenance="reflective", scope="agent", salience=80,
            body="A reflective observation.",
        ), agent="dev_agent")
        store.write_entry(MemoryItem(
            id="MEM-003", slug="ci", title="CI lockfile frozen constraint",
            topic="ci",
            provenance="experiential", scope="agent", salience=60,
            source_task="TASK-100",
            body="Adding a Python minor to CI needs uv.lock re-lock.",
        ), agent="dev_agent")

        self._write_org_config(test_runtime, budget=2000)
        # Create ancestor chain: TASK-100 -> TASK-101 -> TASK-102
        from runtime.models import TaskStatus
        from runtime.infrastructure.database import TaskRecord
        grandparent = TaskRecord(
            id="TASK-100", status=TaskStatus.COMPLETED,
            brief="Grandparent task", assigned_agent="test",
        )
        orch._db.insert_task(grandparent)
        child = TaskRecord(
            id="TASK-101", status=TaskStatus.PENDING,
            brief="Child task", assigned_agent="test",
            parent_task_id="TASK-100",
        )
        orch._db.insert_task(child)
        target = TaskRecord(
            id="TASK-102", status=TaskStatus.PENDING,
            brief="Investigate UX bug", assigned_agent="dev_agent",
            parent_task_id="TASK-101",
        )
        orch._db.insert_task(target)

        monkeypatch.setattr(orch, "_build_session_id", lambda: "sess-test")

        mock_executor = MagicMock()
        mock_executor.run.return_value = ExecutorResult(
            success=True, duration_seconds=1, session_id="sess-test",
        )
        with patch.object(orch, "_build_executor", return_value=mock_executor):
            orch._run_agent("TASK-102", "dev_agent", "")
        prompt = mock_executor.run.call_args.kwargs["prompt"]

        assert "=== MEMORY-DIGEST (system) ===" in prompt
        # MEM-003 (60 + 20 ancestor boost = 80) should rank above
        # MEM-002 (base 80, no boost — both in pointer group,
        # alphabetical tie-break: 'CI lockfile...' < 'Zebra habit')
        idx_003 = prompt.index("MEM-003")
        idx_002 = prompt.index("MEM-002")
        assert idx_003 < idx_002, (
            f"MEM-003 (ancestor-boosted to 80) should rank before MEM-002 (80), "
            f"got idx_003={idx_003}, idx_002={idx_002}"
        )
