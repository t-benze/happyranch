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

from datetime import datetime, timezone
from pathlib import Path

import pytest

from runtime.daemon.dream_runner import build_dream_prompt
from runtime.daemon.thread_runner import (
    build_thread_delta_prompt,
    build_thread_prompt,
)
from runtime.daemon.wake_runner import build_wake_prompt
from runtime.models import (
    DreamRecord,
    ThreadMessage,
    ThreadMessageKind,
    ThreadParticipant,
    ThreadRecord,
)
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
