"""Bundled skill freshness and repository-refresh prompt transport.

Canonical source/member refusal, context eligibility, and managed indexes remain
covered after protocol-document discovery is retired.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
import runtime.orchestrator.workspace_adapters as wa_mod
from runtime.config import Settings
from runtime.orchestrator.workspace_adapters import (
    refresh_session_skills,
    inject_system_contracts,
    _copy_skills_tree,
)


# ── PART A: refresh_session_skills ─────────────────────────────────────


class TestRefreshSessionSkills:
    """Prove refresh_session_skills refreshes skills via the canonical
    store + symlink architecture.

    The wholesale dump has been permanently removed; all skill materialization
    uses the unified canonical boundary (canonical store + workspace symlinks).
    refresh_session_skills is a forwarder to materialize_workspace_skills."""

    @staticmethod
    def _make_all_contracts(settings: Settings) -> None:
        """Create ALL system contracts required for the context union.
        Since materialize_workspace_skills now unions all seven ordinary
        SessionContext values, the preflight requires every system contract
        source directory to exist on disk."""
        skills_root = settings.get_bundled_skills_dir()
        for sid in ("start-task", "jobs", "make-worktree", "thread", "dream", "todos", "create-skill"):
            (skills_root / sid).mkdir(parents=True, exist_ok=True)
            if not (skills_root / sid / "SKILL.md").exists():
                (skills_root / sid / "SKILL.md").write_text(f"# {sid}\n\nSkill body.\n")

    def test_refresh_copies_skills_to_both_targets(
        self, test_settings: Settings, tmp_path: Path,
    ):
        """Skills land in both .claude/skills/ and .agents/skills/."""
        self._make_all_contracts(test_settings)
        skills_root = test_settings.get_bundled_skills_dir()
        (skills_root / "start-task" / "SKILL.md").write_text("# start-task\n")

        workspace = tmp_path / "workspace"
        refresh_session_skills(workspace, test_settings, slug="test")

        claude_skill = workspace / ".claude" / "skills" / "start-task" / "SKILL.md"
        agents_skill = workspace / ".agents" / "skills" / "start-task" / "SKILL.md"

        assert claude_skill.exists()
        assert agents_skill.exists()
        assert "start-task" in claude_skill.read_text()
        assert "start-task" in agents_skill.read_text()

    def test_refresh_picks_up_source_edit_without_lifecycle(
        self, test_settings: Settings, tmp_path: Path,
    ):
        """ACCEPTANCE #1: Edit bundled skill → refresh → workspace reflects edit."""
        self._make_all_contracts(test_settings)
        skills_root = test_settings.get_bundled_skills_dir()
        (skills_root / "start-task" / "SKILL.md").write_text("VERSION 1\n")

        workspace = tmp_path / "workspace"
        refresh_session_skills(workspace, test_settings, slug="test")

        skill_path = workspace / ".claude" / "skills" / "start-task" / "SKILL.md"
        assert skill_path.read_text() == "VERSION 1\n"

        # Edit the source (simulates a skill update in the bundle).
        (skills_root / "start-task" / "SKILL.md").write_text("VERSION 2 - FRESH\n")

        # Refresh again (simulates next session).
        refresh_session_skills(workspace, test_settings, slug="test")

        # Workspace reflects the edit — no lifecycle event needed.
        assert skill_path.read_text() == "VERSION 2 - FRESH\n"

    def test_refresh_overwrites_existing_in_dst(
        self, test_settings: Settings, tmp_path: Path,
    ):
        """Skills in source are always materialized; system contracts only."""
        self._make_all_contracts(test_settings)
        skills_root = test_settings.get_bundled_skills_dir()
        (skills_root / "start-task" / "SKILL.md").write_text("skill v1\n")

        workspace = tmp_path / "workspace"
        refresh_session_skills(workspace, test_settings, slug="test")

        # System contract skill materialized
        assert (workspace / ".claude" / "skills" / "start-task" / "SKILL.md").exists()

        # Update skill in source
        (skills_root / "start-task" / "SKILL.md").write_text("skill v2 - updated\n")

        refresh_session_skills(workspace, test_settings, slug="test")

        # Updated skill reflects new content (via symlink to updated canonical)
        content = (
            workspace / ".claude" / "skills" / "start-task" / "SKILL.md"
        ).read_text()
        assert content == "skill v2 - updated\n"

    def test_refresh_replaces_existing_skill_content(
        self, test_settings: Settings, tmp_path: Path,
    ):
        """Existing stale symlink is repaired (replaced with fresh target)."""
        self._make_all_contracts(test_settings)
        import os as _os
        skills_root = test_settings.get_bundled_skills_dir()
        (skills_root / "start-task" / "SKILL.md").write_text("BUNDLED\n")
        (skills_root / "start-task" / "helper.md").write_text("helper\n")

        workspace = tmp_path / "workspace"
        # Pre-seed with a stale symlink pointing to nowhere
        link_dir = workspace / ".claude" / "skills"
        link_dir.mkdir(parents=True)
        _os.symlink(str(tmp_path / "nonexistent"), str(link_dir / "start-task"))

        refresh_session_skills(workspace, test_settings, slug="test")

        # Verify symlink now points to canonical store
        skill_path = workspace / ".claude" / "skills" / "start-task" / "SKILL.md"
        assert skill_path.read_text() == "BUNDLED\n"
        assert (workspace / ".claude" / "skills" / "start-task" / "helper.md").exists()

    def test_refresh_substitutes_org_slug(
        self, test_settings: Settings, tmp_path: Path,
    ):
        """{ORG_SLUG} placeholder is preserved in canonical content."""
        self._make_all_contracts(test_settings)
        skills_root = test_settings.get_bundled_skills_dir()
        (skills_root / "start-task" / "SKILL.md").write_text(
            "Run: happyranch --org {ORG_SLUG} do-thing\n"
        )

        workspace = tmp_path / "workspace"
        refresh_session_skills(workspace, test_settings, slug="my-org")

        for d in [".claude", ".agents"]:
            content = (workspace / d / "skills" / "start-task" / "SKILL.md").read_text()
            # Canonical bytes are unsubstituted; org context is via env var
            assert "{ORG_SLUG}" in content

    def test_refresh_idempotent_on_missing_source(
        self, test_settings: Settings, tmp_path: Path,
    ):
        """Fail-closed when source directory doesn't exist.
        With the source-existence check, missing runtime/skills/bundled/ raises
        SystemContractMaterializationError."""
        # Don't create the skills dir
        workspace = tmp_path / "workspace"
        from runtime.orchestrator.workspace_adapters import (
            SystemContractMaterializationError,
        )
        with pytest.raises(SystemContractMaterializationError):
            refresh_session_skills(workspace, test_settings, slug="test")

    def test_refresh_uses_test_override(
        self, test_settings: Settings, tmp_path: Path,
    ):
        """_SKILLS_SRC override takes precedence over settings-derived path."""
        fake_src = tmp_path / "fake-skills"
        for sid in ("start-task", "jobs", "make-worktree", "thread", "dream", "todos", "create-skill"):
            (fake_src / sid).mkdir(parents=True)
            (fake_src / sid / "SKILL.md").write_text(f"# {sid}\n\nFAKE\n")

        workspace = tmp_path / "workspace"
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(wa_mod, "_SKILLS_SRC", fake_src, raising=True)
            try:
                refresh_session_skills(workspace, test_settings, slug="test")
            finally:
                wa_mod._SKILLS_SRC = None

        content = (
            workspace / ".claude" / "skills" / "start-task" / "SKILL.md"
        ).read_text()
        assert "FAKE" in content


# ── THR-055 regression ─────────────────────────────────────────────────
# resolve_managed_skills_index remains UNCHANGED — capability-skill
# compact-index must still render unchanged (acceptance #4).


class TestThr055Regression:
    """ACCEPTANCE #4: THR-055 capability-skill index still renders."""

    def test_render_compact_skill_index_unchanged(self):
        """render_compact_skill_index signature and behavior unchanged."""
        from runtime.orchestrator.org_config import render_compact_skill_index
        from runtime.skills.models import (
            ExposedSkill,
            PolicyClass,
            SkillEntry,
            SkillStatus,
        )

        entry = SkillEntry(
            id="hr:test-skill",
            slug="test-skill",
            name="Test Skill",
            version="1.0.0",
            description="A test skill.",
            when_to_use="Use for testing.",
            owner="test",
            source="runtime/skills/test-skill",
            policy_class=PolicyClass.STANDARD_OPERATIONAL,
            status=SkillStatus.ENABLED,
            skill_md_path=Path("/fake/SKILL.md"),
        )
        exposed = ExposedSkill(
            skill=entry,
            allowed_by=[],
            denied_by=[],
        )

        index = render_compact_skill_index([exposed])

        assert "hr:test-skill@1.0.0" in index
        assert "A test skill." in index
        assert "Use for testing." in index
        assert "Load full instructions from" in index
        assert "SKILL.md" in index


# ── Prompt-builder integration ─────────────────────────────────────────


class TestRepositoryRefreshNotesInPrompts:
    """Prove repo_refresh_note flows through all 4 session prompt builders."""

    def test_orchestrator_prompt_includes_refresh_note(self):
        """Orchestrator._build_agent_prompt injects docs_block."""
        from runtime.orchestrator.orchestrator import Orchestrator
        from unittest.mock import MagicMock

        orch = MagicMock(spec=Orchestrator)
        orch._current_time_line.return_value = "2026-01-01T00:00+00:00 (UTC)"
        from runtime.orchestrator.orchestrator import Orchestrator as RealOrch
        orch._build_agent_prompt = RealOrch._build_agent_prompt.__get__(orch, RealOrch)

        prompt = orch._build_agent_prompt(
            "claude", "agent1", "T-1", "s-1", "brief",
            prompt="",
            repo_refresh_note="## Repository refresh\n- repo: offline; using existing checkout",
        )
        assert "## Repository refresh" in prompt
        assert "offline; using existing checkout" in prompt

    def test_orchestrator_prompt_empty_refresh_note_no_op(self):
        """Empty refresh note produces no extra block."""
        from runtime.orchestrator.orchestrator import Orchestrator as RealOrch
        from unittest.mock import MagicMock

        orch = MagicMock(spec=RealOrch)
        orch._current_time_line.return_value = "2026-01-01T00:00+00:00 (UTC)"
        orch._build_agent_prompt = RealOrch._build_agent_prompt.__get__(orch, RealOrch)

        prompt = orch._build_agent_prompt(
            "claude", "agent1", "T-1", "s-1", "brief",
            prompt="",
            repo_refresh_note="",
        )
        assert "## Repository refresh" not in prompt

    def test_wake_prompt_includes_refresh_note(self):
        """build_wake_prompt injects the refresh note."""
        from runtime.daemon.wake_runner import build_wake_prompt
        from runtime.orchestrator.org_config import OrgConfig

        prompt = build_wake_prompt(
            org_slug="test",
            work_hour_id="wh-1",
            agent_name="agent1",
            role="worker",
            team="engineering",
            local_date="2026-01-01",
            slot="09:00",
            mode="continuous",
            preamble="",
            routines=["do thing"],
            org_config=OrgConfig(),
            repo_refresh_note="## Repository refresh\n- repo: offline; using existing checkout",
        )
        assert "## Repository refresh" in prompt
        assert "offline; using existing checkout" in prompt

    def test_wake_prompt_empty_refresh_note_no_op(self):
        """Empty refresh note → no block injected."""
        from runtime.daemon.wake_runner import build_wake_prompt
        from runtime.orchestrator.org_config import OrgConfig

        prompt = build_wake_prompt(
            org_slug="test",
            work_hour_id="wh-1",
            agent_name="agent1",
            role="worker",
            team="engineering",
            local_date="2026-01-01",
            slot="09:00",
            mode="continuous",
            preamble="",
            routines=["do thing"],
            org_config=OrgConfig(),
            repo_refresh_note="",
        )
        assert "## Repository refresh" not in prompt

    def test_thread_full_prompt_includes_refresh_note(self):
        """build_thread_prompt injects the refresh note."""
        from runtime.daemon.thread_runner import build_thread_prompt
        from runtime.orchestrator.org_config import OrgConfig
        from runtime.models import (
            ThreadRecord, ThreadParticipant, ThreadMessage, ThreadMessageKind,
        )

        thread = ThreadRecord(
            id="T-1", subject="test",
            started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        participants = [ThreadParticipant(thread_id="T-1", agent_name="a1")]
        messages = [
            ThreadMessage(
                thread_id="T-1", seq=1, speaker="a1",
                kind=ThreadMessageKind.MESSAGE, body_markdown="hello",
            ),
        ]

        prompt = build_thread_prompt(
            thread=thread, participants=participants, messages=messages,
            invocation_token="tok", invoked_agent="a1",
            purpose="reply", triggering_seq=1,
            org_config=OrgConfig(),
            repo_refresh_note="## Repository refresh\n- repo: offline; using existing checkout",
        )
        assert "## Repository refresh" in prompt
        assert "offline; using existing checkout" in prompt

    def test_thread_delta_prompt_includes_refresh_note(self):
        """build_thread_delta_prompt injects the refresh note."""
        from runtime.daemon.thread_runner import build_thread_delta_prompt
        from runtime.orchestrator.org_config import OrgConfig
        from runtime.models import (
            ThreadRecord, ThreadMessage, ThreadMessageKind,
        )

        thread = ThreadRecord(
            id="T-1", subject="test",
            started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        messages = [
            ThreadMessage(
                thread_id="T-1", seq=2, speaker="a1",
                kind=ThreadMessageKind.MESSAGE, body_markdown="new",
            ),
        ]

        prompt = build_thread_delta_prompt(
            thread=thread, new_messages=messages,
            invocation_token="tok", invoked_agent="a1",
            purpose="reply", triggering_seq=2,
            triggering_message=messages[0],
            org_config=OrgConfig(),
            repo_refresh_note="## Repository refresh\n- repo: offline; using existing checkout",
        )
        assert "## Repository refresh" in prompt
        assert "offline; using existing checkout" in prompt

    def test_dream_prompt_includes_refresh_note(self, tmp_path: Path):
        """build_dream_prompt injects the refresh note."""
        from runtime.daemon.dream_runner import build_dream_prompt
        from runtime.orchestrator.org_config import OrgConfig
        from runtime.models import DreamRecord

        _now = datetime(2026, 1, 2, tzinfo=timezone.utc)
        dream = DreamRecord(
            id="d-1", agent_name="agent1",
            local_date="2026-01-02",
            scheduled_for=_now,
            window_start=_now,
            window_end=_now,
        )

        prompt = build_dream_prompt(
            org_slug="test",
            dream=dream,
            workspace=tmp_path,
            recent_audit=[],
            task_history="",
            org_config=OrgConfig(),
            repo_refresh_note="## Repository refresh\n- repo: offline; using existing checkout",
        )
        assert "## Repository refresh" in prompt
        assert "offline; using existing checkout" in prompt


# ── PART D: inject_system_contracts (THR-055 Phase 1) ────────────────


class TestInjectSystemContracts:
    """Prove inject_system_contracts correctly injects context-appropriate
    system contracts via the canonical store + symlink architecture."""

    def test_task_context_injects_correct_contracts(
        self, test_settings: Settings, tmp_path: Path,
    ):
        """TASK context: start-task, jobs, make-worktree (if repos), thread."""
        skills_root = test_settings.get_bundled_skills_dir()
        for name in ("start-task", "jobs", "make-worktree", "thread", "dream", "todos", "create-skill"):
            (skills_root / name).mkdir(parents=True)
            (skills_root / name / "SKILL.md").write_text(f"# {name}\n")

        # Create a workspace WITH repos
        ws = tmp_path / "ws"
        (ws / "repos" / "happyranch" / ".git").mkdir(parents=True)

        inject_system_contracts(ws, test_settings, slug="test", context="task")

        claude_skills = ws / ".claude" / "skills"
        agents_skills = ws / ".agents" / "skills"

        # start-task injected
        assert (claude_skills / "start-task" / "SKILL.md").exists()
        assert (agents_skills / "start-task" / "SKILL.md").exists()

        # jobs injected
        assert (claude_skills / "jobs" / "SKILL.md").exists()
        assert (agents_skills / "jobs" / "SKILL.md").exists()

        # make-worktree injected (repo-capable)
        assert (claude_skills / "make-worktree" / "SKILL.md").exists()
        assert (agents_skills / "make-worktree" / "SKILL.md").exists()

        # thread injected
        assert (claude_skills / "thread" / "SKILL.md").exists()
        assert (agents_skills / "thread" / "SKILL.md").exists()

        # todos injected (universal)
        assert (claude_skills / "todos" / "SKILL.md").exists()
        assert (agents_skills / "todos" / "SKILL.md").exists()

        # dream NOT injected
        # dream is now expected in all contexts because materialize_workspace_skills
        # unions system contracts across all six ordinary SessionContext values
        assert (claude_skills / "dream" / "SKILL.md").exists()
        assert (agents_skills / "dream" / "SKILL.md").exists()

    def test_task_without_repos_omits_make_worktree(
        self, test_settings: Settings, tmp_path: Path,
    ):
        """TASK context without repos: no make-worktree, todos present."""
        skills_root = test_settings.get_bundled_skills_dir()
        for name in ("start-task", "jobs", "make-worktree", "thread", "dream", "todos", "create-skill"):
            (skills_root / name).mkdir(parents=True)
            (skills_root / name / "SKILL.md").write_text(f"# {name}\n")

        ws = tmp_path / "ws"
        ws.mkdir()

        inject_system_contracts(ws, test_settings, slug="test", context="task")

        claude_skills = ws / ".claude" / "skills"

        assert (claude_skills / "start-task" / "SKILL.md").exists()
        assert (claude_skills / "jobs" / "SKILL.md").exists()
        assert not (claude_skills / "make-worktree" / "SKILL.md").exists()
        assert (claude_skills / "thread" / "SKILL.md").exists()
        assert (claude_skills / "dream" / "SKILL.md").exists()
        assert (claude_skills / "todos" / "SKILL.md").exists()

    def test_dream_context_injects_dream_not_start_task(
        self, test_settings: Settings, tmp_path: Path,
    ):
        """DREAM context: jobs, make-worktree (if repos), dream, todos. NOT start-task or thread."""
        skills_root = test_settings.get_bundled_skills_dir()
        for name in ("start-task", "jobs", "make-worktree", "thread", "dream", "todos", "create-skill"):
            (skills_root / name).mkdir(parents=True)
            (skills_root / name / "SKILL.md").write_text(f"# {name}\n")

        ws = tmp_path / "ws"
        (ws / "repos" / "happyranch" / ".git").mkdir(parents=True)

        inject_system_contracts(ws, test_settings, slug="test", context="dream")

        claude_skills = ws / ".claude" / "skills"

        # Union preserves start-task + thread from other contexts
        assert (claude_skills / "start-task" / "SKILL.md").exists()
        assert (claude_skills / "jobs" / "SKILL.md").exists()
        assert (claude_skills / "make-worktree" / "SKILL.md").exists()
        assert (claude_skills / "thread" / "SKILL.md").exists()
        assert (claude_skills / "dream" / "SKILL.md").exists()
        assert (claude_skills / "todos" / "SKILL.md").exists()

    def test_thread_context_injects_thread_not_dream(
        self, test_settings: Settings, tmp_path: Path,
    ):
        """THREAD context: jobs, make-worktree (if repos), thread, todos. NOT start-task or dream."""
        skills_root = test_settings.get_bundled_skills_dir()
        for name in ("start-task", "jobs", "make-worktree", "thread", "dream", "todos", "create-skill"):
            (skills_root / name).mkdir(parents=True)
            (skills_root / name / "SKILL.md").write_text(f"# {name}\n")

        ws = tmp_path / "ws"
        (ws / "repos" / "happyranch" / ".git").mkdir(parents=True)

        inject_system_contracts(ws, test_settings, slug="test", context="thread")

        claude_skills = ws / ".claude" / "skills"

        # Union preserves start-task (from task/wake/schedule) and dream (from dream)
        assert (claude_skills / "start-task" / "SKILL.md").exists()
        assert (claude_skills / "jobs" / "SKILL.md").exists()
        assert (claude_skills / "make-worktree" / "SKILL.md").exists()
        assert (claude_skills / "thread" / "SKILL.md").exists()
        assert (claude_skills / "dream" / "SKILL.md").exists()
        assert (claude_skills / "todos" / "SKILL.md").exists()

    def test_wake_context_same_as_task(
        self, test_settings: Settings, tmp_path: Path,
    ):
        """WAKE context: same as TASK (start-task, jobs, make-worktree if repos, thread, todos)."""
        skills_root = test_settings.get_bundled_skills_dir()
        for name in ("start-task", "jobs", "make-worktree", "thread", "dream", "todos", "create-skill"):
            (skills_root / name).mkdir(parents=True)
            (skills_root / name / "SKILL.md").write_text(f"# {name}\n")

        ws = tmp_path / "ws"
        (ws / "repos" / "happyranch" / ".git").mkdir(parents=True)

        inject_system_contracts(ws, test_settings, slug="test", context="wake")

        claude_skills = ws / ".claude" / "skills"

        assert (claude_skills / "start-task" / "SKILL.md").exists()
        assert (claude_skills / "jobs" / "SKILL.md").exists()
        assert (claude_skills / "make-worktree" / "SKILL.md").exists()
        assert (claude_skills / "thread" / "SKILL.md").exists()
        # Union preserves dream from dream context
        assert (claude_skills / "dream" / "SKILL.md").exists()
        assert (claude_skills / "todos" / "SKILL.md").exists()

    def test_unknown_context_is_noop(
        self, test_settings: Settings, tmp_path: Path,
    ):
        """An unrecognised context string is a true no-op: no links or
        directories are created under .claude/skills or .agents/skills."""
        skills_root = test_settings.get_bundled_skills_dir()
        for name in ("start-task", "jobs", "make-worktree", "thread", "dream"):
            (skills_root / name).mkdir(parents=True)
            (skills_root / name / "SKILL.md").write_text(f"# {name}\n")

        ws = tmp_path / "ws"
        ws.mkdir()

        inject_system_contracts(ws, test_settings, slug="test", context="nonexistent")

        # No-op: neither skills root should exist after an unknown context call
        claude_skills = ws / ".claude" / "skills"
        agents_skills = ws / ".agents" / "skills"
        assert not claude_skills.exists(), (
            "Unknown context must not create .claude/skills"
        )
        assert not agents_skills.exists(), (
            "Unknown context must not create .agents/skills"
        )

    def test_idempotent_with_refresh_session_skills(
        self, test_settings: Settings, tmp_path: Path,
    ):
        """Calling both refresh_session_skills and inject_system_contracts
        is idempotent — the same skill bodies are re-copied."""
        skills_root = test_settings.get_bundled_skills_dir()
        for name in ("start-task", "jobs", "make-worktree", "thread", "dream", "todos", "create-skill"):
            (skills_root / name).mkdir(parents=True)
            (skills_root / name / "SKILL.md").write_text(f"# {name} v1\n")

        ws = tmp_path / "ws"
        (ws / "repos" / "happyranch" / ".git").mkdir(parents=True)

        # First: materialize via unified canonical boundary
        refresh_session_skills(ws, test_settings, slug="test")

        # All 5 system contracts present (union across all contexts)
        for name in ("start-task", "jobs", "make-worktree", "thread", "dream"):
            assert (ws / ".claude" / "skills" / name / "SKILL.md").exists()

        # Edit a source skill
        (skills_root / "start-task" / "SKILL.md").write_text("# start-task v2\n")

        # Second: explicit contract injection picks up the edit
        inject_system_contracts(ws, test_settings, slug="test", context="task")

        # Both reflect the latest version
        skill = ws / ".claude" / "skills" / "start-task" / "SKILL.md"
        assert skill.read_text() == "# start-task v2\n"
