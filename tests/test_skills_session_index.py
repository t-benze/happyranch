"""Compact skill index injection into session prompts.

Slice 4 of THR-055: At session creation, inject a compact skill INDEX
(manifest only) for skills that pass BOTH catalog and eligibility gates.

The index is rendered alongside the existing current_time context in
every session prompt builder (task/subtask, wake, thread full + delta,
dream). Tests cover:
  - render_compact_skill_index core rendering (field set, hr: namespace,
    deterministic ordering, empty resolution)
  - Session prompt injection for all 4 builders
  - System/contract skills excluded
  - Global CLI skills untouched
  - current_time injection unchanged
"""
from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from runtime.config import Settings
from runtime.daemon.dream_runner import build_dream_prompt
from runtime.daemon.thread_runner import (
    build_thread_delta_prompt,
    build_thread_prompt,
    run_invocation,
)
from runtime.daemon.wake_runner import build_wake_prompt
from runtime.infrastructure.database import Database
from runtime.models import (
    DreamRecord,
    ThreadInvocationPurpose,
    ThreadMessage,
    ThreadMessageKind,
    ThreadParticipant,
    ThreadRecord,
)
from runtime.orchestrator.executors import ExecutorResult
from runtime.orchestrator.org_config import (
    OrgConfig,
    render_compact_skill_index,
)
from runtime.skills.exposure import resolve_exposed_skills
from runtime.skills.models import (
    ApprovalState,
    EligibilityRule,
    ExposedSkill,
    PolicyClass,
    SkillEntry,
    SkillStatus,
)
from runtime.skills.registry import SkillRegistry
from runtime.skills.resolver import EligibilityResolver

FIXTURES = Path(__file__).parent / "fixtures" / "skills"
_TZ_ORG = OrgConfig(timezone="Asia/Shanghai")


# ── helpers ─────────────────────────────────────────────────────────────


def _make_exposed(
    skill_id: str = "hr:test-skill",
    slug: str = "test-skill",
    name: str = "Test Skill",
    version: str = "1.0.0",
    description: str = "A test skill.",
    when_to_use: str = "Use when testing.",
    source: str = "runtime/skills/test-skill",
    policy_class: PolicyClass = PolicyClass.STANDARD_OPERATIONAL,
) -> ExposedSkill:
    entry = SkillEntry(
        id=skill_id,
        slug=slug,
        name=name,
        version=version,
        description=description,
        when_to_use=when_to_use,
        owner="test",
        source=source,
        policy_class=policy_class,
        approval_state=ApprovalState.APPROVED,
        approved_by="test",
        approved_at=datetime.now(timezone.utc),
        status=SkillStatus.ENABLED,
    )
    return ExposedSkill(
        skill=entry,
        catalog_approved=True,
        allowed_by=[EligibilityRule(scope="org", id="test", skill_id=skill_id, action="allow")],
        denied_by=[],
    )


# ══════════════════════════════════════════════════════════════════════════
# render_compact_skill_index — core renderer tests
# ══════════════════════════════════════════════════════════════════════════


class TestRenderCompactSkillIndex:
    """Tests for the pure render_compact_skill_index function."""

    def test_empty_list_returns_empty_string(self):
        """Empty resolution → empty string, no index lines."""
        result = render_compact_skill_index([])
        assert result == ""

    def test_single_skill_includes_all_spec_fields(self):
        """Each index line contains id, name, version, description,
        when_to_use, source, and load instruction."""
        exposed = _make_exposed(
            skill_id="hr:repo-review",
            name="Code Review Workflow",
            version="1.0.0",
            description="Code review workflow.",
            when_to_use="Use when asked to review a change;",
            source="runtime/skills/repo-review",
        )
        result = render_compact_skill_index([exposed])
        assert "hr:repo-review@1.0.0" in result
        assert "Code review workflow." in result
        assert "Use when asked to review a change;" in result
        assert "Load full instructions from runtime/skills/repo-review/SKILL.md." in result

    def test_hr_namespace_in_every_entry(self):
        """Every entry is namespaced hr:<slug>."""
        exposed1 = _make_exposed(skill_id="hr:skill-a", name="Skill A")
        exposed2 = _make_exposed(skill_id="hr:skill-b", name="Skill B")
        result = render_compact_skill_index([exposed1, exposed2])
        lines = result.strip().split("\n")
        for line in lines:
            assert line.startswith("- hr:"), f"Line missing hr: namespace: {line}"

    def test_deterministic_ordering_by_id(self):
        """Skills are sorted by id for deterministic output."""
        exposed_b = _make_exposed(skill_id="hr:zzz-skill", name="Z Skill")
        exposed_a = _make_exposed(skill_id="hr:aaa-skill", name="A Skill")
        result = render_compact_skill_index([exposed_b, exposed_a])
        lines = result.strip().split("\n")
        assert "aaa-skill" in lines[0]
        assert "zzz-skill" in lines[1]

    def test_version_included_in_display(self):
        """Version is displayed as @version in each line."""
        exposed = _make_exposed(version="2.3.1")
        result = render_compact_skill_index([exposed])
        assert "@2.3.1" in result

    def test_skills_omitted_by_policy_do_not_appear(self):
        """Only skills passed to renderer appear — the caller filters
        by catalog+eligibility gates before calling render."""
        # If the caller only passes 1 of 2 skills, only 1 appears
        exposed = _make_exposed(skill_id="hr:visible", name="Visible")
        result = render_compact_skill_index([exposed])
        lines = result.strip().split("\n")
        assert len(lines) == 1
        assert "hr:visible" in result
        assert "hr:hidden" not in result

    def test_compact_format_matches_spec_example(self):
        """The format matches the spec example:
        - hr:repo-review@1.0.0 — Code review workflow. Use when asked to
          review a change; load full instructions from runtime/skills/repo-review/SKILL.md.
        """
        exposed = _make_exposed(
            skill_id="hr:repo-review",
            name="Code Review Workflow",
            version="1.0.0",
            description="Code review workflow.",
            when_to_use="Use when asked to review a change;",
            source="runtime/skills/repo-review",
        )
        result = render_compact_skill_index([exposed])
        expected_line = (
            "- hr:repo-review@1.0.0 — Code review workflow. "
            "Use when asked to review a change; "
            "Load full instructions from runtime/skills/repo-review/SKILL.md."
        )
        assert result.strip() == expected_line

    def test_does_not_inline_skill_md_bodies(self):
        """The index is manifest only — no SKILL.md bodies."""
        exposed = _make_exposed()
        result = render_compact_skill_index([exposed])
        # Should mention SKILL.md path but not contain its content
        assert "SKILL.md" in result
        # The index is short — just metadata, not full body text
        assert len(result) < 500, f"Index too long for manifest-only: {len(result)} chars"


# ══════════════════════════════════════════════════════════════════════════
# System/contract skills exclusion
# ══════════════════════════════════════════════════════════════════════════


class TestSystemContractExclusion:
    """System/contract skills (policy_class=system_contract) are excluded
    from the toggleable catalog by the registry loader, so they never
    reach render_compact_skill_index."""

    def test_system_contract_skill_not_in_registry(self):
        """Registry.load skips system_contract skills."""
        registry = SkillRegistry(skills_root=FIXTURES)
        # system-contract-skill fixture has policy_class: system_contract
        entry = registry.get("hr:system-contract-skill")
        assert entry is None, "System contract skill should not be in toggleable catalog"

    def test_system_contract_not_in_exposed_list(self):
        """resolve_exposed_skills does not return system_contract skills."""
        registry = SkillRegistry(skills_root=FIXTURES)
        policy = {
            "org": {"allow": [
                "hr:standard-skill",
                "hr:system-contract-skill",  # won't be in catalog anyway
            ], "deny": []},
        }
        resolver = EligibilityResolver(policy)
        exposed = resolve_exposed_skills(
            registry, resolver, org="test", team="engineering", agent="dev_agent"
        )
        exposed_ids = {s.skill.id for s in exposed}
        assert "hr:system-contract-skill" not in exposed_ids


# ══════════════════════════════════════════════════════════════════════════
# Session prompt injection — all 4 builders
# ══════════════════════════════════════════════════════════════════════════

_FROZEN = datetime(2026, 6, 27, 4, 47, tzinfo=timezone.utc)
_EXPECTED_TIME = "current_time: 2026-06-27T12:47+08:00 (Asia/Shanghai)"


def _sample_skills_index() -> str:
    exposed = _make_exposed(
        skill_id="hr:repo-review",
        name="Code Review Workflow",
        version="1.0.0",
        description="Code review workflow.",
        when_to_use="Use when asked to review a change;",
        source="runtime/skills/repo-review",
    )
    return render_compact_skill_index([exposed])


class TestWakePromptSkillIndex:
    """Wake prompt includes the managed skills index."""

    def test_skills_index_appended_to_wake_prompt(self):
        skills_index = _sample_skills_index()
        prompt = build_wake_prompt(
            org_slug="happyranch",
            work_hour_id="WORKHOUR-1",
            agent_name="dev_agent",
            role="worker",
            team="engineering",
            local_date="2026-06-27",
            slot="09:00",
            mode="windowed",
            preamble="",
            routines=["- do a thing"],
            org_config=_TZ_ORG,
            now=lambda: _FROZEN,
            managed_skills_index=skills_index,
        )
        assert skills_index in prompt
        assert _EXPECTED_TIME in prompt  # current_time unchanged

    def test_empty_skills_index_no_op(self):
        """Empty index → no managed skills block in prompt."""
        prompt = build_wake_prompt(
            org_slug="happyranch",
            work_hour_id="WORKHOUR-1",
            agent_name="dev_agent",
            role="worker",
            team="engineering",
            local_date="2026-06-27",
            slot="09:00",
            mode="windowed",
            preamble="",
            routines=["- do a thing"],
            org_config=_TZ_ORG,
            now=lambda: _FROZEN,
            managed_skills_index="",
        )
        assert "hr:" not in prompt  # No skill ID should appear
        assert _EXPECTED_TIME in prompt  # current_time still present


class TestThreadPromptSkillIndex:
    """Thread prompts include the managed skills index."""

    def _thread(self) -> ThreadRecord:
        return ThreadRecord(
            id="THR-001", subject="Test",
            started_at=datetime(2026, 5, 13, tzinfo=timezone.utc),
        )

    def _msg(self, seq: int) -> ThreadMessage:
        return ThreadMessage(
            thread_id="THR-001", seq=seq, speaker="founder",
            kind=ThreadMessageKind.MESSAGE, body_markdown="hi",
        )

    def test_skills_index_in_thread_full_prompt(self):
        skills_index = _sample_skills_index()
        prompt = build_thread_prompt(
            thread=self._thread(),
            participants=[ThreadParticipant(thread_id="THR-001", agent_name="dev_agent")],
            messages=[self._msg(1)],
            invocation_token="TOK", invoked_agent="dev_agent",
            purpose="reply", triggering_seq=1,
            org_config=_TZ_ORG, now=lambda: _FROZEN,
            managed_skills_index=skills_index,
        )
        assert skills_index in prompt
        assert _EXPECTED_TIME in prompt

    def test_empty_skills_index_thread_no_op(self):
        prompt = build_thread_prompt(
            thread=self._thread(),
            participants=[ThreadParticipant(thread_id="THR-001", agent_name="dev_agent")],
            messages=[self._msg(1)],
            invocation_token="TOK", invoked_agent="dev_agent",
            purpose="reply", triggering_seq=1,
            org_config=_TZ_ORG, now=lambda: _FROZEN,
            managed_skills_index="",
        )
        assert "hr:" not in prompt
        assert _EXPECTED_TIME in prompt

    def test_skills_index_in_thread_delta_prompt(self):
        skills_index = _sample_skills_index()
        prompt = build_thread_delta_prompt(
            thread=self._thread(),
            new_messages=[self._msg(2)],
            invocation_token="TOK", invoked_agent="dev_agent",
            purpose="reply", triggering_seq=2, triggering_message=self._msg(2),
            org_config=_TZ_ORG, now=lambda: _FROZEN,
            managed_skills_index=skills_index,
        )
        assert skills_index in prompt
        assert _EXPECTED_TIME in prompt

    def test_empty_skills_index_delta_no_op(self):
        prompt = build_thread_delta_prompt(
            thread=self._thread(),
            new_messages=[self._msg(2)],
            invocation_token="TOK", invoked_agent="dev_agent",
            purpose="reply", triggering_seq=2, triggering_message=self._msg(2),
            org_config=_TZ_ORG, now=lambda: _FROZEN,
            managed_skills_index="",
        )
        assert "hr:" not in prompt
        assert _EXPECTED_TIME in prompt


class TestDreamPromptSkillIndex:
    """Dream prompt includes the managed skills index."""

    def test_skills_index_in_dream_prompt(self):
        skills_index = _sample_skills_index()
        prompt = build_dream_prompt(
            org_slug="happyranch",
            dream=DreamRecord(
                id="DREAM-1", agent_name="dev_agent", local_date="2026-06-27",
                scheduled_for=_FROZEN, window_start=_FROZEN, window_end=_FROZEN,
            ),
            workspace=Path("/tmp"),
            recent_audit=[], task_history="",
            org_config=_TZ_ORG, now=lambda: _FROZEN,
            managed_skills_index=skills_index,
        )
        assert skills_index in prompt
        assert _EXPECTED_TIME in prompt

    def test_empty_skills_index_dream_no_op(self):
        prompt = build_dream_prompt(
            org_slug="happyranch",
            dream=DreamRecord(
                id="DREAM-1", agent_name="dev_agent", local_date="2026-06-27",
                scheduled_for=_FROZEN, window_start=_FROZEN, window_end=_FROZEN,
            ),
            workspace=Path("/tmp"),
            recent_audit=[], task_history="",
            org_config=_TZ_ORG, now=lambda: _FROZEN,
            managed_skills_index="",
        )
        assert "hr:" not in prompt
        assert _EXPECTED_TIME in prompt


# ══════════════════════════════════════════════════════════════════════════
# Orchestrator prompt — managed skills index
# ══════════════════════════════════════════════════════════════════════════


class TestOrchestratorSkillIndex:
    """Orchestrator._build_agent_prompt includes the managed skills index."""

    @pytest.fixture
    def orch(self, test_settings, test_runtime):
        from runtime.infrastructure.database import Database
        from runtime.orchestrator.orchestrator import Orchestrator
        from runtime.orchestrator.teams import TeamsRegistry

        test_runtime.root.mkdir(parents=True, exist_ok=True)
        db = Database(test_runtime.db_path)
        teams = TeamsRegistry.load(test_runtime.root)
        return Orchestrator(
            db=db, settings=test_settings, paths=test_runtime, slug="test",
            teams=teams,
        )

    def test_skills_index_in_orchestrator_prompt(self, orch):
        skills_index = _sample_skills_index()
        prompt = orch._build_agent_prompt(
            "claude", "dev_agent", "TASK-1", "sess-1", "do a thing", "",
            now=lambda: _FROZEN,
            managed_skills_index=skills_index,
        )
        assert skills_index in prompt

    def test_empty_skills_index_orchestrator_no_op(self, orch):
        prompt = orch._build_agent_prompt(
            "claude", "dev_agent", "TASK-1", "sess-1", "do a thing", "",
            now=lambda: _FROZEN,
            managed_skills_index="",
        )
        assert "hr:" not in prompt

    def test_current_time_unchanged_with_skills_index(self, orch, test_runtime):
        """current_time injection is not affected by skills index addition."""
        path = test_runtime.org_config_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("timezone: Asia/Shanghai\n")
        skills_index = _sample_skills_index()
        prompt = orch._build_agent_prompt(
            "claude", "dev_agent", "TASK-1", "sess-1", "do a thing", "",
            now=lambda: _FROZEN,
            managed_skills_index=skills_index,
        )
        assert "  current_time: 2026-06-27T12:47+08:00 (Asia/Shanghai)\n" in prompt
        assert skills_index in prompt


# ══════════════════════════════════════════════════════════════════════════
# Integration: resolve + render via registry fixtures
# ══════════════════════════════════════════════════════════════════════════


class TestIntegrationResolveAndRender:
    """End-to-end: load registry, resolve, render compact index."""

    def test_resolve_and_render_eligible_skill(self):
        """A standard_operational skill that passes both gates
        appears in the rendered index."""
        registry = SkillRegistry(skills_root=FIXTURES)
        policy = {
            "org": {"allow": ["hr:standard-skill"], "deny": []},
        }
        resolver = EligibilityResolver(policy)
        exposed = resolve_exposed_skills(
            registry, resolver, org="test", team="engineering", agent="dev_agent"
        )
        result = render_compact_skill_index(exposed)
        assert "hr:standard-skill@1.0.0" in result
        assert "A standard operational skill for testing." in result
        assert "Load full instructions from runtime/skills/standard-skill/SKILL.md." in result

    def test_disabled_skill_not_in_index(self):
        """A disabled skill does not pass catalog gate → not in index."""
        registry = SkillRegistry(skills_root=FIXTURES)
        policy = {
            "org": {"allow": ["hr:disabled-skill"], "deny": []},
        }
        resolver = EligibilityResolver(policy)
        exposed = resolve_exposed_skills(
            registry, resolver, org="test", team="engineering", agent="dev_agent"
        )
        result = render_compact_skill_index(exposed)
        assert "hr:disabled-skill" not in result

    def test_draft_skill_not_in_index(self):
        """A draft skill fails catalog gate → not in index."""
        registry = SkillRegistry(skills_root=FIXTURES)
        policy = {
            "org": {"allow": ["hr:draft-skill"], "deny": []},
        }
        resolver = EligibilityResolver(policy)
        exposed = resolve_exposed_skills(
            registry, resolver, org="test", team="engineering", agent="dev_agent"
        )
        result = render_compact_skill_index(exposed)
        assert "hr:draft-skill" not in result

    def test_denied_skill_not_in_index(self):
        """An eligible-by-allow skill that is denied → not in index."""
        registry = SkillRegistry(skills_root=FIXTURES)
        policy = {
            "org": {"allow": ["hr:standard-skill"], "deny": ["hr:standard-skill"]},
        }
        resolver = EligibilityResolver(policy)
        exposed = resolve_exposed_skills(
            registry, resolver, org="test", team="engineering", agent="dev_agent"
        )
        result = render_compact_skill_index(exposed)
        assert result == ""  # deny wins → no exposed skills

    def test_multiple_skills_deterministic_ordering(self):
        """Multiple resolved skills are sorted deterministically by id."""
        registry = SkillRegistry(skills_root=FIXTURES)
        # standard-skill and high-impact-skill both pass catalog gate
        policy = {
            "org": {"allow": ["hr:standard-skill", "hr:high-impact-skill"], "deny": []},
        }
        resolver = EligibilityResolver(policy)
        exposed = resolve_exposed_skills(
            registry, resolver, org="test", team="engineering", agent="dev_agent"
        )
        result = render_compact_skill_index(exposed)
        lines = result.strip().split("\n")
        # high-impact-skill < standard-skill alphabetically
        assert "high-impact-skill" in lines[0]
        assert "standard-skill" in lines[1]

    def test_global_cli_skills_not_in_index(self):
        """The index only contains managed (registry) skills, not
        globally installed CLI skills like ./skills/."""
        registry = SkillRegistry(skills_root=FIXTURES)
        policy = {
            "org": {"allow": ["hr:standard-skill"], "deny": []},
        }
        resolver = EligibilityResolver(policy)
        exposed = resolve_exposed_skills(
            registry, resolver, org="test", team="engineering", agent="dev_agent"
        )
        result = render_compact_skill_index(exposed)
        # No reference to global skills directory
        assert "./skills/" not in result
        assert "hr:standard-skill" in result  # Only managed skills


# ══════════════════════════════════════════════════════════════════════════
# Call-site integration: resolve_managed_skills_index
# ══════════════════════════════════════════════════════════════════════════


class TestResolveManagedSkillsIndex:
    """Integration tests for resolve_managed_skills_index — the single helper
    reused by every session-creation path (task, thread, dream, wake).

    These tests exercise the REAL on-disk loading path (SkillRegistry +
    EligibilityResolver + resolve_exposed_skills + render_compact_skill_index)
    and assert the built prompt CONTAINS eligible skills and EXCLUDES
    ineligible/disabled/draft/system-contract skills.
    """

    @pytest.fixture
    def tmp_runtime(self, tmp_path):
        """Create a minimal org runtime directory with skills + config + agent."""
        runtime = tmp_path

        # Agent definition
        agents_dir = runtime / "org" / "agents"
        agents_dir.mkdir(parents=True)
        (agents_dir / "dev_agent.md").write_text(
            "---\nname: dev_agent\nteam: engineering\nrole: worker\nexecutor: claude\n---\n\n# Dev Agent\n\nBuild software.\n"
        )

        # Org config with skills eligibility
        org_dir = runtime / "org"
        org_dir.mkdir(parents=True, exist_ok=True)
        config = {
            "timezone": "Asia/Shanghai",
            "skills": {
                "org": {"allow": ["hr:standard-skill"], "deny": []},
            },
        }
        import yaml as _yaml
        (org_dir / "config.yaml").write_text(_yaml.dump(config))

        # Symlink skills fixtures to runtime/skills/
        skills_dir = runtime / "runtime" / "skills"
        skills_dir.parent.mkdir(parents=True, exist_ok=True)
        # Copy fixtures (or symlink — copy for test isolation)
        import shutil
        for fixture_dir in FIXTURES.iterdir():
            if fixture_dir.is_dir():
                target = skills_dir / fixture_dir.name
                shutil.copytree(fixture_dir, target)

        return runtime

    def test_eligible_skill_appears_in_index(self, tmp_runtime):
        """An eligible skill that passes both gates appears in the compact index."""
        from runtime.orchestrator._paths import OrgPaths
        from runtime.orchestrator.org_config import resolve_managed_skills_index

        paths = OrgPaths(root=tmp_runtime)
        result = resolve_managed_skills_index(paths=paths, agent_name="dev_agent")

        assert result, "Expected non-empty skills index"
        assert "hr:standard-skill@1.0.0" in result
        assert "Load full instructions from" in result
        assert "SKILL.md" in result

    def test_ineligible_skill_excluded(self, tmp_runtime):
        """Skills not in the eligibility allow list are excluded."""
        from runtime.orchestrator._paths import OrgPaths
        from runtime.orchestrator.org_config import resolve_managed_skills_index

        paths = OrgPaths(root=tmp_runtime)
        result = resolve_managed_skills_index(paths=paths, agent_name="dev_agent")

        # high-impact-skill is not in the allow list → excluded
        assert "hr:high-impact-skill" not in result
        # standard-skill IS in the allow list → present
        assert "hr:standard-skill" in result

    def test_disabled_skill_excluded(self, tmp_runtime):
        """A disabled skill does not pass catalog gate → excluded from index."""
        from runtime.orchestrator._paths import OrgPaths
        from runtime.orchestrator.org_config import resolve_managed_skills_index

        # Add disabled-skill to the allow list
        org_dir = tmp_runtime / "org"
        config = {
            "timezone": "Asia/Shanghai",
            "skills": {
                "org": {
                    "allow": ["hr:standard-skill", "hr:disabled-skill"],
                    "deny": [],
                },
            },
        }
        import yaml as _yaml
        (org_dir / "config.yaml").write_text(_yaml.dump(config))

        paths = OrgPaths(root=tmp_runtime)
        result = resolve_managed_skills_index(paths=paths, agent_name="dev_agent")

        assert "hr:disabled-skill" not in result  # disabled → excluded
        assert "hr:standard-skill" in result

    def test_draft_skill_excluded(self, tmp_runtime):
        """A draft skill does not pass catalog gate → excluded from index."""
        from runtime.orchestrator._paths import OrgPaths
        from runtime.orchestrator.org_config import resolve_managed_skills_index

        org_dir = tmp_runtime / "org"
        config = {
            "timezone": "Asia/Shanghai",
            "skills": {
                "org": {
                    "allow": ["hr:standard-skill", "hr:draft-skill"],
                    "deny": [],
                },
            },
        }
        import yaml as _yaml
        (org_dir / "config.yaml").write_text(_yaml.dump(config))

        paths = OrgPaths(root=tmp_runtime)
        result = resolve_managed_skills_index(paths=paths, agent_name="dev_agent")

        assert "hr:draft-skill" not in result  # draft → excluded
        assert "hr:standard-skill" in result

    def test_system_contract_skill_excluded(self, tmp_runtime):
        """A system_contract skill is NEVER in the toggleable catalog → excluded."""
        from runtime.orchestrator._paths import OrgPaths
        from runtime.orchestrator.org_config import resolve_managed_skills_index

        org_dir = tmp_runtime / "org"
        config = {
            "timezone": "Asia/Shanghai",
            "skills": {
                "org": {
                    "allow": ["hr:standard-skill", "hr:system-contract-skill"],
                    "deny": [],
                },
            },
        }
        import yaml as _yaml
        (org_dir / "config.yaml").write_text(_yaml.dump(config))

        paths = OrgPaths(root=tmp_runtime)
        result = resolve_managed_skills_index(paths=paths, agent_name="dev_agent")

        assert "hr:system-contract-skill" not in result  # never toggleable
        assert "hr:standard-skill" in result

    def test_missing_skills_directory_returns_empty(self, tmp_path):
        """When runtime/skills/ does not exist, return empty string gracefully."""
        from runtime.orchestrator._paths import OrgPaths
        from runtime.orchestrator.org_config import resolve_managed_skills_index

        runtime = tmp_path
        (runtime / "org" / "agents").mkdir(parents=True)
        (runtime / "org" / "agents" / "dev_agent.md").write_text(
            "---\nname: dev_agent\nteam: engineering\nrole: worker\nexecutor: claude\n---\n\n# Dev Agent\n"
        )

        paths = OrgPaths(root=runtime)
        result = resolve_managed_skills_index(paths=paths, agent_name="dev_agent")
        assert result == ""

    def test_no_eligibility_policy_admits_nothing(self, tmp_runtime):
        """With no skills eligibility section in org config (empty allow union),
        no skills are admitted — per the spec formula."""
        from runtime.orchestrator._paths import OrgPaths
        from runtime.orchestrator.org_config import resolve_managed_skills_index

        # Remove skills eligibility from config
        org_dir = tmp_runtime / "org"
        config = {"timezone": "Asia/Shanghai"}
        import yaml as _yaml
        (org_dir / "config.yaml").write_text(_yaml.dump(config))

        paths = OrgPaths(root=tmp_runtime)
        result = resolve_managed_skills_index(paths=paths, agent_name="dev_agent")
        assert result == ""  # empty union → nothing

    def test_deny_wins_over_allow(self, tmp_runtime):
        """When a skill is both allowed and denied, deny wins → excluded."""
        from runtime.orchestrator._paths import OrgPaths
        from runtime.orchestrator.org_config import resolve_managed_skills_index

        org_dir = tmp_runtime / "org"
        config = {
            "timezone": "Asia/Shanghai",
            "skills": {
                "org": {
                    "allow": ["hr:standard-skill"],
                    "deny": ["hr:standard-skill"],
                },
            },
        }
        import yaml as _yaml
        (org_dir / "config.yaml").write_text(_yaml.dump(config))

        paths = OrgPaths(root=tmp_runtime)
        result = resolve_managed_skills_index(paths=paths, agent_name="dev_agent")
        assert result == ""  # deny wins → no skills


# ══════════════════════════════════════════════════════════════════════════
# Call-path integration: REAL session-creation entrypoints
# ══════════════════════════════════════════════════════════════════════════


def _seed_skills_and_config(
    root: Path,
    allow: list[str] | None = None,
    deny: list[str] | None = None,
    extra_config: dict | None = None,
    agent_name: str = "dev_agent",
    agent_executor: str = "claude",
) -> None:
    """Seed on-disk skill packages and org config under an org root.

    ``root`` is the org root (e.g. ``test_runtime.root`` or ``org_state.root``).
    Skills are copied into ``root/runtime/skills/``.
    Org config is written to ``root/org/config.yaml``.
    An agent definition is written to ``root/org/agents/<agent_name>.md``.
    """
    # Skills directory — copy fixture skill packages
    skills_dir = root / "runtime" / "skills"
    if skills_dir.exists():
        shutil.rmtree(skills_dir)
    skills_dir.parent.mkdir(parents=True, exist_ok=True)
    for fixture_dir in FIXTURES.iterdir():
        if fixture_dir.is_dir():
            shutil.copytree(fixture_dir, skills_dir / fixture_dir.name)

    # Org config with skills eligibility
    org_dir = root / "org"
    org_dir.mkdir(parents=True, exist_ok=True)
    cfg: dict = {"timezone": "Asia/Shanghai"}
    if extra_config:
        cfg.update(extra_config)
    if allow is not None or deny is not None:
        cfg["skills"] = {
            "org": {
                "allow": allow or [],
                "deny": deny or [],
            },
        }
    import yaml as _yaml
    (org_dir / "config.yaml").write_text(_yaml.dump(cfg))

    # Agent definition
    agents_dir = org_dir / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    (agents_dir / f"{agent_name}.md").write_text(
        "---\n"
        f"name: {agent_name}\n"
        "team: engineering\n"
        "role: worker\n"
        f"executor: {agent_executor}\n"
        "---\n\n"
        f"# {agent_name}\n\nBuild software.\n"
        "## Routine Tasks\n\n- Triage open tickets.\n"
    )


def _setup_orch_workspace(test_runtime, agent: str = "dev_agent") -> None:
    """Create a workspace with the start-task skill marker so _run_agent
    passes the readiness check."""
    ws = test_runtime.workspaces_dir / agent
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "task_history.md").write_text(f"# Task History: {agent}\n\n")
    skill = ws / ".claude" / "skills" / "start-task"
    skill.mkdir(parents=True, exist_ok=True)
    (skill / "SKILL.md").write_text("# start-task\n")




class TestCallPathManagedSkillsIndex:
    """Call-path integration: prove resolve_managed_skills_index flows
    through the REAL session-creation entrypoints (not just the builders).

    Each test exercises the full entrypoint with a fake/stub executor
    that captures the emitted prompt. Assertions verify:
    - Eligible skills appear in the compact hr: index.
    - Disabled, draft, system_contract, denied, and empty-allow-union
      cases are EXCLUDED.
    - The current_time line is present and unchanged (THR-039).
    """

    # ── Orchestrator._run_agent (task/subtask) ──────────────────────────

    @pytest.fixture
    def orch(self, test_settings, test_runtime):
        from runtime.infrastructure.database import Database
        from runtime.orchestrator.orchestrator import Orchestrator
        from runtime.orchestrator.teams import TeamsRegistry

        test_runtime.root.mkdir(parents=True, exist_ok=True)
        db = Database(test_runtime.db_path)
        teams = TeamsRegistry.load(test_runtime.root)
        return Orchestrator(
            db=db, settings=test_settings, paths=test_runtime, slug="test",
            teams=teams,
        )

    def test_orchestrator_run_agent_injects_skills_index(
        self, orch, test_runtime, monkeypatch,
    ):
        """Orchestrator._run_agent resolves skills and passes the index
        into the prompt emitted to the executor."""
        # Seed skills + org config under test_runtime.root
        _seed_skills_and_config(
            test_runtime.root, allow=["hr:standard-skill"],
        )
        _setup_orch_workspace(test_runtime)

        task_id = orch.create_task("Test skill index in agent prompt")
        monkeypatch.setattr(orch, "_build_session_id", lambda: "sess-skills")

        mock_executor = MagicMock()
        mock_executor.run.return_value = ExecutorResult(
            success=True, duration_seconds=1, session_id="sess-skills",
        )
        with patch.object(orch, "_build_executor", return_value=mock_executor):
            orch._run_agent(task_id, "dev_agent", "")

        prompt: str = mock_executor.run.call_args.kwargs["prompt"]

        # Eligible skill present
        assert "hr:standard-skill@1.0.0" in prompt
        assert "A standard operational skill for testing." in prompt
        # Ineligible skills excluded
        assert "hr:disabled-skill" not in prompt
        assert "hr:draft-skill" not in prompt
        assert "hr:system-contract-skill" not in prompt
        assert "hr:high-impact-skill" not in prompt
        # current_time co-injected (THR-039)
        assert "current_time:" in prompt
        assert "Asia/Shanghai" in prompt

    def test_orchestrator_no_eligible_skills_empty_index(
        self, orch, test_runtime, monkeypatch,
    ):
        """When no skills match eligibility, the prompt still contains
        current_time but no hr: entries."""
        _seed_skills_and_config(
            test_runtime.root, allow=["hr:disabled-skill"],
        )
        _setup_orch_workspace(test_runtime)

        task_id = orch.create_task("Test empty skills index")
        monkeypatch.setattr(orch, "_build_session_id", lambda: "sess-empty")

        mock_executor = MagicMock()
        mock_executor.run.return_value = ExecutorResult(
            success=True, duration_seconds=1, session_id="sess-empty",
        )
        with patch.object(orch, "_build_executor", return_value=mock_executor):
            orch._run_agent(task_id, "dev_agent", "")

        prompt = mock_executor.run.call_args.kwargs["prompt"]
        assert "hr:" not in prompt
        assert "current_time:" in prompt
        assert "Asia/Shanghai" in prompt

    # ── Thread runner: run_invocation ───────────────────────────────────

    def _make_thread_state(self, tmp_path: Path, agent: str = "alice"):
        """Create a FakeOrgState with thread, messages, and invocation."""
        db = Database(tmp_path / "happyranch.db")
        db.insert_thread(ThreadRecord(
            id="THR-001", subject="Test thread",
            started_at=datetime(2026, 7, 2, tzinfo=timezone.utc),
        ))
        db.add_thread_participant("THR-001", agent, added_by="founder")
        db.append_thread_message(
            thread_id="THR-001", speaker="founder",
            kind=ThreadMessageKind.MESSAGE, body_markdown="hello",
        )
        inv = db.mint_thread_invocation(
            thread_id="THR-001", agent_name=agent,
            triggering_seq=1, purpose=ThreadInvocationPurpose.REPLY,
        )
        ws = tmp_path / "workspaces" / agent
        ws.mkdir(parents=True)
        (ws / "agent.yaml").write_text("executor: claude\n")
        return db, inv

    @pytest.mark.asyncio
    async def test_thread_runner_injects_skills_index(
        self, tmp_path, monkeypatch,
    ):
        """run_invocation resolves skills and injects the index into the
        full thread prompt."""
        _seed_skills_and_config(
            tmp_path, allow=["hr:standard-skill"],
        )
        db, inv = self._make_thread_state(tmp_path)

        import runtime.daemon.thread_runner as runner_mod

        class _FakeResult:
            success = True
            error = None
            returncode = 0
            session_id = "sess-thread"
            duration_seconds = 1
            agent_session_id = None
            stdout_tail = ""
            stderr_tail = ""
            token_usage = None

        class _CapturingExec:
            def __init__(self, **kwargs):
                pass
            def run(self, **kwargs):
                self._last_prompt = kwargs.get("prompt", "")
                return _FakeResult()

        capturer = _CapturingExec()
        monkeypatch.setattr(
            runner_mod, "_build_executor_for_provider",
            lambda provider, settings, paths: capturer,
        )

        # FakeOrgState mirroring the test_thread_runner pattern
        class Org:
            def __init__(self):
                self.db = db
                self.root = tmp_path

        await run_invocation(
            org_state=Org(), invocation_token=inv.invocation_token,
            settings=Settings(),
        )

        prompt: str = capturer._last_prompt
        assert "hr:standard-skill@1.0.0" in prompt
        assert "A standard operational skill for testing." in prompt
        assert "hr:disabled-skill" not in prompt
        assert "hr:draft-skill" not in prompt
        assert "hr:system-contract-skill" not in prompt
        assert "hr:high-impact-skill" not in prompt
        assert "current_time:" in prompt
        assert "Asia/Shanghai" in prompt

    @pytest.mark.asyncio
    async def test_thread_runner_empty_index_no_hr_entries(
        self, tmp_path, monkeypatch,
    ):
        """When no skills are eligible, the thread prompt contains no
        hr: entries."""
        _seed_skills_and_config(
            tmp_path, allow=["hr:disabled-skill"],
        )
        db, inv = self._make_thread_state(tmp_path)

        import runtime.daemon.thread_runner as runner_mod

        class _FakeResult:
            success = True
            error = None
            returncode = 0
            session_id = "sess-thread"
            duration_seconds = 1
            agent_session_id = None
            stdout_tail = ""
            stderr_tail = ""
            token_usage = None

        class _CapturingExec:
            def __init__(self, **kwargs):
                pass
            def run(self, **kwargs):
                self._last_prompt = kwargs.get("prompt", "")
                return _FakeResult()

        capturer = _CapturingExec()
        monkeypatch.setattr(
            runner_mod, "_build_executor_for_provider",
            lambda provider, settings, paths: capturer,
        )

        class Org:
            def __init__(self):
                self.db = db
                self.root = tmp_path

        await run_invocation(
            org_state=Org(), invocation_token=inv.invocation_token,
            settings=Settings(),
        )

        assert "hr:" not in capturer._last_prompt
        assert "current_time:" in capturer._last_prompt
        assert "Asia/Shanghai" in capturer._last_prompt

    # ── Thread runner: DELTA branch ──────────────────────────────────

    @pytest.mark.asyncio
    @pytest.mark.parametrize("allow,deny,expect_includes,expect_excludes", [
        # Original case: single eligible standard_operational skill
        (
            ["hr:standard-skill"], [],
            ["hr:standard-skill@1.0.0", "standard operational skill"],
            ["hr:disabled-skill", "hr:draft-skill", "hr:system-contract-skill",
             "hr:high-impact-skill"],
        ),
        # DENY-OVER-ALLOW: standard-skill both allowed+denied → excluded;
        # independently-allowed high-impact-skill → still appears.
        (
            ["hr:standard-skill", "hr:high-impact-skill"], ["hr:standard-skill"],
            ["hr:high-impact-skill@1.0.0", "high-impact policy skill"],
            ["hr:standard-skill", "hr:disabled-skill", "hr:draft-skill",
             "hr:system-contract-skill"],
        ),
        # EMPTY-ALLOW-UNION: no eligibility rules → nothing admitted.
        (
            None, None,
            [],
            ["hr:standard-skill", "hr:high-impact-skill", "hr:disabled-skill",
             "hr:draft-skill", "hr:system-contract-skill"],
        ),
    ], ids=["eligible_only", "deny_over_allow", "empty_allow_union"])
    async def test_thread_runner_delta_injects_skills_index(
        self, tmp_path, monkeypatch, allow, deny,
        expect_includes, expect_excludes,
    ):
        """When a stored thread session exists, run_invocation takes the
        resume/delta path and injects the skills index into the delta prompt."""
        _seed_skills_and_config(
            tmp_path, allow=allow, deny=deny,
        )

        db = Database(tmp_path / "happyranch.db")
        db.insert_thread(ThreadRecord(
            id="THR-001", subject="Test delta skills",
            started_at=datetime(2026, 7, 2, tzinfo=timezone.utc),
        ))
        db.add_thread_participant("THR-001", "alice", added_by="founder")
        db.append_thread_message(
            thread_id="THR-001", speaker="founder",
            kind=ThreadMessageKind.MESSAGE, body_markdown="m1 old",
        )
        db.append_thread_message(
            thread_id="THR-001", speaker="bob",
            kind=ThreadMessageKind.MESSAGE, body_markdown="m2 newest",
        )
        db.update_thread_session(
            "THR-001", "alice",
            agent_session_id="claude-prior", last_resumed_seq=1,
        )
        inv = db.mint_thread_invocation(
            thread_id="THR-001", agent_name="alice",
            triggering_seq=2, purpose=ThreadInvocationPurpose.REPLY,
        )
        ws = tmp_path / "workspaces" / "alice"
        ws.mkdir(parents=True)
        (ws / "agent.yaml").write_text("executor: claude\n")

        import runtime.daemon.thread_runner as runner_mod

        class _FakeResult:
            success = True
            error = None
            returncode = 0
            session_id = "sess-delta"
            duration_seconds = 1
            agent_session_id = None
            stdout_tail = ""
            stderr_tail = ""
            token_usage = None
            rate_limited = False

        # Scripted executor that records the prompt from run()
        class _DeltaCapturingExec:
            def __init__(self, resume_session_id=None, **kwargs):
                self.calls = []
            def run(self, **kwargs):
                self.calls.append(kwargs)
                r = _FakeResult()
                r.agent_session_id = "claude-prior"
                return r

        capturer = _DeltaCapturingExec()
        monkeypatch.setattr(
            runner_mod, "_build_executor_for_provider",
            lambda provider, settings, paths: capturer,
        )

        class Org:
            def __init__(self):
                self.db = db
                self.root = tmp_path

        await run_invocation(
            org_state=Org(), invocation_token=inv.invocation_token,
            settings=Settings(),
        )

        # Delta path was taken (resume_session_id present)
        assert len(capturer.calls) == 1
        assert capturer.calls[0].get("resume_session_id") == "claude-prior"
        delta_prompt: str = capturer.calls[0]["prompt"]
        # New message present, old message excluded
        assert "m2 newest" in delta_prompt
        assert "m1 old" not in delta_prompt
        # Eligibility assertions (parametrized by case)
        for inc in expect_includes:
            assert inc in delta_prompt, f"Expected '{inc}' in delta prompt"
        for exc in expect_excludes:
            assert exc not in delta_prompt, f"Expected '{exc}' NOT in delta prompt"
        # Empty-allow-union: NO hr: entries should appear at all
        if allow is None and deny is None:
            assert "hr:" not in delta_prompt
        # current_time co-injected (unchanged by skill index, THR-039)
        assert "current_time:" in delta_prompt
        assert "Asia/Shanghai" in delta_prompt

    # ── Thread runner: FALLBACK branch ───────────────────────────────

    @pytest.mark.asyncio
    @pytest.mark.parametrize("allow,deny,expect_includes,expect_excludes", [
        # Original case: single eligible standard_operational skill
        (
            ["hr:standard-skill"], [],
            ["hr:standard-skill@1.0.0", "standard operational skill"],
            ["hr:disabled-skill", "hr:draft-skill", "hr:system-contract-skill",
             "hr:high-impact-skill"],
        ),
        # DENY-OVER-ALLOW: standard-skill both allowed+denied → excluded;
        # independently-allowed high-impact-skill → still appears.
        (
            ["hr:standard-skill", "hr:high-impact-skill"], ["hr:standard-skill"],
            ["hr:high-impact-skill@1.0.0", "high-impact policy skill"],
            ["hr:standard-skill", "hr:disabled-skill", "hr:draft-skill",
             "hr:system-contract-skill"],
        ),
        # EMPTY-ALLOW-UNION: no eligibility rules → nothing admitted.
        (
            None, None,
            [],
            ["hr:standard-skill", "hr:high-impact-skill", "hr:disabled-skill",
             "hr:draft-skill", "hr:system-contract-skill"],
        ),
    ], ids=["eligible_only", "deny_over_allow", "empty_allow_union"])
    async def test_thread_runner_fallback_injects_skills_index(
        self, tmp_path, monkeypatch, allow, deny,
        expect_includes, expect_excludes,
    ):
        """When the evicted-session retry fires, the fallback full prompt
        also injects the skills index."""
        _seed_skills_and_config(
            tmp_path, allow=allow, deny=deny,
        )

        db = Database(tmp_path / "happyranch.db")
        db.insert_thread(ThreadRecord(
            id="THR-001", subject="Test fallback skills",
            started_at=datetime(2026, 7, 2, tzinfo=timezone.utc),
        ))
        db.add_thread_participant("THR-001", "alice", added_by="founder")
        db.append_thread_message(
            thread_id="THR-001", speaker="founder",
            kind=ThreadMessageKind.MESSAGE, body_markdown="hello",
        )
        db.update_thread_session(
            "THR-001", "alice",
            agent_session_id="claude-evicted", last_resumed_seq=0,
        )
        inv = db.mint_thread_invocation(
            thread_id="THR-001", agent_name="alice",
            triggering_seq=1, purpose=ThreadInvocationPurpose.REPLY,
        )
        ws = tmp_path / "workspaces" / "alice"
        ws.mkdir(parents=True)
        (ws / "agent.yaml").write_text("executor: claude\n")

        import runtime.daemon.thread_runner as runner_mod

        # Evicted result: non-success with session-not-found error
        class _EvictedResult:
            success = False
            error = "No conversation found for session claude-evicted"
            returncode = 1
            session_id = ""
            duration_seconds = 1
            agent_session_id = None
            stdout_tail = ""
            stderr_tail = "No conversation found"
            token_usage = None
            rate_limited = False
        evicted = _EvictedResult()

        class _OkResult:
            success = True
            error = None
            returncode = 0
            session_id = "sess-fresh"
            duration_seconds = 1
            agent_session_id = "claude-fresh"
            stdout_tail = ""
            stderr_tail = ""
            token_usage = None
            rate_limited = False
        ok_result = _OkResult()

        class _FallbackCapturingExec:
            def __init__(self, **kwargs):
                self.calls = []
                self._scripted = [evicted, ok_result]
            def run(self, **kwargs):
                self.calls.append(kwargs)
                return self._scripted.pop(0)

        capturer = _FallbackCapturingExec()
        monkeypatch.setattr(
            runner_mod, "_build_executor_for_provider",
            lambda provider, settings, paths: capturer,
        )

        class Org:
            def __init__(self):
                self.db = db
                self.root = tmp_path

        await run_invocation(
            org_state=Org(), invocation_token=inv.invocation_token,
            settings=Settings(),
        )

        # Two invocations: first evicted (delta), second fallback (full)
        assert len(capturer.calls) == 2
        assert capturer.calls[0].get("resume_session_id") == "claude-evicted"
        assert "resume_session_id" not in capturer.calls[1]
        # Fallback prompt contains the full message history
        fallback_prompt: str = capturer.calls[1]["prompt"]
        assert "Full message history follows" in fallback_prompt
        # Eligibility assertions (parametrized by case)
        for inc in expect_includes:
            assert inc in fallback_prompt, f"Expected '{inc}' in fallback prompt"
        for exc in expect_excludes:
            assert exc not in fallback_prompt, f"Expected '{exc}' NOT in fallback prompt"
        # Empty-allow-union: NO hr: entries should appear at all
        if allow is None and deny is None:
            assert "hr:" not in fallback_prompt
        # current_time co-injected (unchanged by skill index, THR-039)
        assert "current_time:" in fallback_prompt
        assert "Asia/Shanghai" in fallback_prompt
