"""THR-070: Session-time skill body freshness + protocol doc manifest.

Acceptance criteria:
  1. Edit a bundled skill, start a NEW session — agent's loaded skill body
     reflects the edit WITHOUT a lifecycle event.
  2. Edit a protocol doc in the bundle → injected manifest points at the
     bundled absolute path; no path points at repos/happyranch/protocol.
  3. Injected manifest is minimal (1 line/item), smaller than full bodies.
  4. THR-055 capability-skill compact-index still renders (regression).
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
import runtime.orchestrator.workspace_adapters as wa_mod
from runtime.config import Settings
from runtime.orchestrator.org_config import (
    resolve_protocol_doc_manifest,
)
from runtime.orchestrator.workspace_adapters import (
    refresh_session_skills,
    inject_system_contracts,
    _copy_skills_tree,
)


# ── PART A: refresh_session_skills ─────────────────────────────────────


class TestRefreshSessionSkills:
    """Prove refresh_session_skills re-copies from source on every call.

    The Phase 4 cutover gates the wholesale dump behind _WHOLESALE_DUMP_ENABLED
    (default OFF). These tests re-enable it via monkeypatch so they continue
    to verify the wholesale-dump path independently of the cutover."""

    @pytest.fixture(autouse=True)
    def _enable_wholesale_dump(self, monkeypatch) -> None:
        """Re-enable the wholesale dump for these refresh_session_skills tests."""
        monkeypatch.setattr(
            "runtime.orchestrator.workspace_adapters._WHOLESALE_DUMP_ENABLED",
            True,
        )

    def test_refresh_copies_skills_to_both_targets(
        self, test_settings: Settings, tmp_path: Path,
    ):
        """Skills land in both .claude/skills/ and .agents/skills/."""
        skills_root = test_settings.get_protocol_dir() / "skills"
        (skills_root / "start-task").mkdir(parents=True)
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
        skills_root = test_settings.get_protocol_dir() / "skills"
        (skills_root / "start-task").mkdir(parents=True)
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
        """Skills in source are always copied; existing workspace files are replaced."""
        skills_root = test_settings.get_protocol_dir() / "skills"
        (skills_root / "start-task").mkdir(parents=True)
        (skills_root / "start-task" / "SKILL.md").write_text("skill v1\n")
        (skills_root / "other").mkdir(parents=True)
        (skills_root / "other" / "SKILL.md").write_text("other v1\n")

        workspace = tmp_path / "workspace"
        refresh_session_skills(workspace, test_settings, slug="test")

        # Both skills copied
        assert (workspace / ".claude" / "skills" / "start-task" / "SKILL.md").exists()
        assert (workspace / ".claude" / "skills" / "other" / "SKILL.md").exists()

        # Update other skill in source
        (skills_root / "other" / "SKILL.md").write_text("other v2 - updated\n")

        refresh_session_skills(workspace, test_settings, slug="test")

        # Updated skill reflects new content
        content = (
            workspace / ".claude" / "skills" / "other" / "SKILL.md"
        ).read_text()
        assert content == "other v2 - updated\n"

    def test_refresh_replaces_existing_skill_content(
        self, test_settings: Settings, tmp_path: Path,
    ):
        """Existing skill files are fully replaced (not merged)."""
        skills_root = test_settings.get_protocol_dir() / "skills"
        (skills_root / "start-task").mkdir(parents=True)
        (skills_root / "start-task" / "SKILL.md").write_text("BUNDLED\n")
        (skills_root / "start-task" / "helper.md").write_text("helper\n")

        workspace = tmp_path / "workspace"
        # Pre-seed with stale content
        (workspace / ".claude" / "skills" / "start-task").mkdir(parents=True)
        (workspace / ".claude" / "skills" / "start-task" / "SKILL.md").write_text("STALE\n")
        (workspace / ".claude" / "skills" / "start-task" / "extra.md").write_text("extra\n")

        refresh_session_skills(workspace, test_settings, slug="test")

        skill_path = workspace / ".claude" / "skills" / "start-task" / "SKILL.md"
        assert skill_path.read_text() == "BUNDLED\n"
        # extra.md from stale workspace is gone (dir is rmtree'd before copy)
        assert not (workspace / ".claude" / "skills" / "start-task" / "extra.md").exists()
        # helper.md from bundle is present
        assert (workspace / ".claude" / "skills" / "start-task" / "helper.md").exists()

    def test_refresh_substitutes_org_slug(
        self, test_settings: Settings, tmp_path: Path,
    ):
        """{ORG_SLUG} placeholder is substituted in skill .md files."""
        skills_root = test_settings.get_protocol_dir() / "skills"
        (skills_root / "start-task").mkdir(parents=True)
        (skills_root / "start-task" / "SKILL.md").write_text(
            "Run: happyranch --org {ORG_SLUG} do-thing\n"
        )

        workspace = tmp_path / "workspace"
        refresh_session_skills(workspace, test_settings, slug="my-org")

        for d in [".claude", ".agents"]:
            content = (workspace / d / "skills" / "start-task" / "SKILL.md").read_text()
            assert "{ORG_SLUG}" not in content
            assert "--org my-org" in content

    def test_refresh_idempotent_on_missing_source(
        self, test_settings: Settings, tmp_path: Path,
    ):
        """No-op when source directory doesn't exist."""
        # Don't create the skills dir
        workspace = tmp_path / "workspace"
        refresh_session_skills(workspace, test_settings, slug="test")
        # Should not crash; destination dirs may or may not exist
        assert not (workspace / ".claude" / "skills").exists()
        assert not (workspace / ".agents" / "skills").exists()

    def test_refresh_uses_test_override(
        self, test_settings: Settings, tmp_path: Path,
    ):
        """_SKILLS_SRC override takes precedence over settings-derived path."""
        fake_src = tmp_path / "fake-skills"
        (fake_src / "start-task").mkdir(parents=True)
        (fake_src / "start-task" / "SKILL.md").write_text("FAKE\n")

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
        assert content == "FAKE\n"


# ── PART B: resolve_protocol_doc_manifest ──────────────────────────────


class TestResolveProtocolDocManifest:
    """Prove the manifest renders correctly and points at bundled paths."""

    def test_manifest_renders_one_line_per_doc(
        self, test_settings: Settings, tmp_path: Path,
    ):
        """Each .md file in protocol/ gets one line in the manifest."""
        protocol_dir = test_settings.get_protocol_dir()
        protocol_dir.mkdir(parents=True, exist_ok=True)
        (protocol_dir / "00-test.md").write_text("# Test Doc\nOne-line purpose.\n")
        (protocol_dir / "01-other.md").write_text("# Other Doc\nAnother purpose.\n")

        manifest = resolve_protocol_doc_manifest(settings=test_settings)

        assert "Test Doc" in manifest
        assert "Other Doc" in manifest
        assert "One-line purpose" in manifest
        assert "Another purpose" in manifest
        # Two docs → two list items
        assert manifest.count("\n- ") == 2

    def test_manifest_includes_absolute_bundled_path(
        self, test_settings: Settings, tmp_path: Path,
    ):
        """ACCEPTANCE #2: manifest points at bundled absolute paths,
        NOT at repos/happyranch/protocol."""
        protocol_dir = test_settings.get_protocol_dir()
        protocol_dir.mkdir(parents=True, exist_ok=True)
        (protocol_dir / "00-test.md").write_text("# Test Doc\nPurpose.\n")

        manifest = resolve_protocol_doc_manifest(settings=test_settings)

        abs_path = str((protocol_dir / "00-test.md").resolve())
        assert abs_path in manifest
        assert "repos/happyranch/protocol" not in manifest
        assert "Read:" in manifest

    def test_manifest_minimal_one_liner_per_doc(
        self, test_settings: Settings, tmp_path: Path,
    ):
        """ACCEPTANCE #3: one line per doc, minimal — smaller than full bodies."""
        protocol_dir = test_settings.get_protocol_dir()
        protocol_dir.mkdir(parents=True, exist_ok=True)
        # Write a large doc body
        large_body = "# Large Doc\nPurpose line.\n" + ("padding\n" * 500)
        (protocol_dir / "00-large.md").write_text(large_body)

        manifest = resolve_protocol_doc_manifest(settings=test_settings)

        # Manifest is much smaller than the full body
        assert len(manifest) < len(large_body)
        # One line per doc in the manifest
        assert manifest.count("\n- ") == 1

    def test_manifest_empty_when_no_protocol_dir(
        self, test_settings: Settings,
    ):
        """Empty string when protocol dir doesn't exist."""
        from unittest.mock import MagicMock
        mock_settings = MagicMock()
        mock_settings.get_protocol_dir.return_value = Path("/nonexistent/path")

        manifest = resolve_protocol_doc_manifest(settings=mock_settings)
        assert manifest == ""

    def test_manifest_empty_when_no_md_files(
        self, test_settings: Settings, tmp_path: Path,
    ):
        """Empty string when protocol dir exists but has no .md files."""
        protocol_dir = test_settings.get_protocol_dir()
        protocol_dir.mkdir(parents=True, exist_ok=True)
        (protocol_dir / "not-a-doc.txt").write_text("hello\n")

        manifest = resolve_protocol_doc_manifest(settings=test_settings)
        assert manifest == ""

    def test_manifest_extracts_title_from_h1(
        self, test_settings: Settings, tmp_path: Path,
    ):
        """Title comes from first # heading, not filename stem."""
        protocol_dir = test_settings.get_protocol_dir()
        protocol_dir.mkdir(parents=True, exist_ok=True)
        (protocol_dir / "99-foo.md").write_text(
            "# Orchestrator: Routing & State\nThe application layer.\n"
        )

        manifest = resolve_protocol_doc_manifest(settings=test_settings)

        assert "Orchestrator: Routing & State" in manifest
        assert "The application layer" in manifest

    def test_manifest_fallback_on_unreadable_file(
        self, test_settings: Settings, tmp_path: Path,
    ):
        """Graceful fallback when a .md file can't be read."""
        protocol_dir = test_settings.get_protocol_dir()
        protocol_dir.mkdir(parents=True, exist_ok=True)
        broken = protocol_dir / "broken.md"
        broken.write_text("# OK\nPurpose.\n")
        # Make unreadable
        broken.chmod(0o000)

        try:
            manifest = resolve_protocol_doc_manifest(settings=test_settings)
            # Should not crash; should still render something (filename-based fallback)
            assert "Protocol reference" in manifest
        finally:
            broken.chmod(0o644)

    def test_manifest_none_settings_returns_empty(self):
        """None settings → empty string."""
        manifest = resolve_protocol_doc_manifest(settings=None)
        assert manifest == ""

    def test_manifest_has_header_section(self, test_settings: Settings, tmp_path: Path):
        """Manifest starts with a ## Protocol Docs header."""
        protocol_dir = test_settings.get_protocol_dir()
        protocol_dir.mkdir(parents=True, exist_ok=True)
        (protocol_dir / "00-test.md").write_text("# Test\nPurpose.\n")

        manifest = resolve_protocol_doc_manifest(settings=test_settings)
        assert manifest.startswith("## Protocol Docs")


# ── THR-055 regression ─────────────────────────────────────────────────
# resolve_managed_skills_index remains UNCHANGED — capability-skill
# compact-index must still render unchanged (acceptance #4).


class TestThr055Regression:
    """ACCEPTANCE #4: THR-055 capability-skill index still renders."""

    def test_render_compact_skill_index_unchanged(self):
        """render_compact_skill_index signature and behavior unchanged."""
        from runtime.orchestrator.org_config import render_compact_skill_index
        from runtime.skills.models import (
            ApprovalState,
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
            approval_state=ApprovalState.APPROVED,
            approved_by="founder",
            approved_at=None,
            status=SkillStatus.ENABLED,
            skill_md_path=Path("/fake/SKILL.md"),
        )
        exposed = ExposedSkill(
            skill=entry,
            catalog_approved=True,
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


class TestProtocolDocManifestInPrompts:
    """Prove protocol_doc_manifest flows through all 4 session prompt builders."""

    def test_orchestrator_prompt_includes_doc_manifest(self):
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
            protocol_doc_manifest="## Protocol Docs\n- **Foo** — bar. Read: /abs/path",
        )
        assert "## Protocol Docs" in prompt
        assert "Read: /abs/path" in prompt

    def test_orchestrator_prompt_empty_manifest_no_op(self):
        """Empty manifest produces no extra block."""
        from runtime.orchestrator.orchestrator import Orchestrator as RealOrch
        from unittest.mock import MagicMock

        orch = MagicMock(spec=RealOrch)
        orch._current_time_line.return_value = "2026-01-01T00:00+00:00 (UTC)"
        orch._build_agent_prompt = RealOrch._build_agent_prompt.__get__(orch, RealOrch)

        prompt = orch._build_agent_prompt(
            "claude", "agent1", "T-1", "s-1", "brief",
            prompt="",
            protocol_doc_manifest="",
        )
        assert "## Protocol Docs" not in prompt

    def test_wake_prompt_includes_doc_manifest(self):
        """build_wake_prompt injects the doc manifest."""
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
            protocol_doc_manifest="## Protocol Docs\n- **Foo** — bar. Read: /abs/path",
        )
        assert "## Protocol Docs" in prompt
        assert "Read: /abs/path" in prompt

    def test_wake_prompt_empty_manifest_no_op(self):
        """Empty manifest → no block injected."""
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
            protocol_doc_manifest="",
        )
        assert "## Protocol Docs" not in prompt

    def test_thread_full_prompt_includes_doc_manifest(self):
        """build_thread_prompt injects the doc manifest."""
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
            protocol_doc_manifest="## Protocol Docs\n- **Foo** — bar. Read: /abs/path",
        )
        assert "## Protocol Docs" in prompt
        assert "Read: /abs/path" in prompt

    def test_thread_delta_prompt_includes_doc_manifest(self):
        """build_thread_delta_prompt injects the doc manifest."""
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
            protocol_doc_manifest="## Protocol Docs\n- **Foo** — bar. Read: /abs/path",
        )
        assert "## Protocol Docs" in prompt
        assert "Read: /abs/path" in prompt

    def test_dream_prompt_includes_doc_manifest(self, tmp_path: Path):
        """build_dream_prompt injects the doc manifest."""
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
            protocol_doc_manifest="## Protocol Docs\n- **Foo** — bar. Read: /abs/path",
        )
        assert "## Protocol Docs" in prompt
        assert "Read: /abs/path" in prompt


# ── PART D: inject_system_contracts (THR-055 Phase 1) ────────────────


class TestInjectSystemContracts:
    """Prove inject_system_contracts correctly injects context-appropriate
    system contracts alongside the wholesale refresh_session_skills dump."""

    @pytest.fixture(autouse=True)
    def _enable_wholesale_dump(self, monkeypatch) -> None:
        """Re-enable the wholesale dump for idempotent test with refresh_session_skills."""
        monkeypatch.setattr(
            "runtime.orchestrator.workspace_adapters._WHOLESALE_DUMP_ENABLED",
            True,
        )

    def test_task_context_injects_correct_contracts(
        self, test_settings: Settings, tmp_path: Path,
    ):
        """TASK context: start-task, jobs, make-worktree (if repos), thread."""
        skills_root = test_settings.get_protocol_dir() / "skills"
        for name in ("start-task", "jobs", "make-worktree", "thread", "dream"):
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

        # dream NOT injected
        assert not (claude_skills / "dream" / "SKILL.md").exists()
        assert not (agents_skills / "dream" / "SKILL.md").exists()

    def test_task_without_repos_omits_make_worktree(
        self, test_settings: Settings, tmp_path: Path,
    ):
        """TASK context without repos: no make-worktree."""
        skills_root = test_settings.get_protocol_dir() / "skills"
        for name in ("start-task", "jobs", "make-worktree", "thread", "dream"):
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
        assert not (claude_skills / "dream" / "SKILL.md").exists()

    def test_dream_context_injects_dream_not_start_task(
        self, test_settings: Settings, tmp_path: Path,
    ):
        """DREAM context: jobs, make-worktree (if repos), dream. NOT start-task or thread."""
        skills_root = test_settings.get_protocol_dir() / "skills"
        for name in ("start-task", "jobs", "make-worktree", "thread", "dream"):
            (skills_root / name).mkdir(parents=True)
            (skills_root / name / "SKILL.md").write_text(f"# {name}\n")

        ws = tmp_path / "ws"
        (ws / "repos" / "happyranch" / ".git").mkdir(parents=True)

        inject_system_contracts(ws, test_settings, slug="test", context="dream")

        claude_skills = ws / ".claude" / "skills"

        assert not (claude_skills / "start-task" / "SKILL.md").exists()
        assert (claude_skills / "jobs" / "SKILL.md").exists()
        assert (claude_skills / "make-worktree" / "SKILL.md").exists()
        assert not (claude_skills / "thread" / "SKILL.md").exists()
        assert (claude_skills / "dream" / "SKILL.md").exists()

    def test_thread_context_injects_thread_not_dream(
        self, test_settings: Settings, tmp_path: Path,
    ):
        """THREAD context: jobs, make-worktree (if repos), thread. NOT start-task or dream."""
        skills_root = test_settings.get_protocol_dir() / "skills"
        for name in ("start-task", "jobs", "make-worktree", "thread", "dream"):
            (skills_root / name).mkdir(parents=True)
            (skills_root / name / "SKILL.md").write_text(f"# {name}\n")

        ws = tmp_path / "ws"
        (ws / "repos" / "happyranch" / ".git").mkdir(parents=True)

        inject_system_contracts(ws, test_settings, slug="test", context="thread")

        claude_skills = ws / ".claude" / "skills"

        assert not (claude_skills / "start-task" / "SKILL.md").exists()
        assert (claude_skills / "jobs" / "SKILL.md").exists()
        assert (claude_skills / "make-worktree" / "SKILL.md").exists()
        assert (claude_skills / "thread" / "SKILL.md").exists()
        assert not (claude_skills / "dream" / "SKILL.md").exists()

    def test_wake_context_same_as_task(
        self, test_settings: Settings, tmp_path: Path,
    ):
        """WAKE context: same as TASK (start-task, jobs, make-worktree if repos, thread)."""
        skills_root = test_settings.get_protocol_dir() / "skills"
        for name in ("start-task", "jobs", "make-worktree", "thread", "dream"):
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
        assert not (claude_skills / "dream" / "SKILL.md").exists()

    def test_unknown_context_is_noop(
        self, test_settings: Settings, tmp_path: Path,
    ):
        """An unknown context string gracefully degrades to a no-op."""
        skills_root = test_settings.get_protocol_dir() / "skills"
        (skills_root / "start-task").mkdir(parents=True)
        (skills_root / "start-task" / "SKILL.md").write_text("# start-task\n")

        ws = tmp_path / "ws"
        ws.mkdir()

        # Should not raise, just do nothing
        inject_system_contracts(ws, test_settings, slug="test", context="nonexistent")

        # No skills directory created at all since no contracts resolved
        assert not (ws / ".claude" / "skills").exists()

    def test_idempotent_with_refresh_session_skills(
        self, test_settings: Settings, tmp_path: Path,
    ):
        """Calling both refresh_session_skills and inject_system_contracts
        is idempotent — the same skill bodies are re-copied."""
        skills_root = test_settings.get_protocol_dir() / "skills"
        for name in ("start-task", "jobs", "make-worktree", "thread", "dream"):
            (skills_root / name).mkdir(parents=True)
            (skills_root / name / "SKILL.md").write_text(f"# {name} v1\n")

        ws = tmp_path / "ws"
        (ws / "repos" / "happyranch" / ".git").mkdir(parents=True)

        # First: wholesale dump
        refresh_session_skills(ws, test_settings, slug="test")

        # Edit a source skill
        (skills_root / "start-task" / "SKILL.md").write_text("# start-task v2\n")

        # Second: explicit contract injection picks up the edit
        inject_system_contracts(ws, test_settings, slug="test", context="task")

        # Both reflect the latest version
        skill = ws / ".claude" / "skills" / "start-task" / "SKILL.md"
        assert skill.read_text() == "# start-task v2\n"
