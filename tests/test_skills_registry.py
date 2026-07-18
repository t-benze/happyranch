"""Tests for runtime.skills.registry — SkillRegistry loader."""

import pytest
from pathlib import Path


FIXTURES = Path(__file__).parent / "fixtures" / "skills"


class TestSkillRegistryLoad:
    """Tests for loading skills from a directory of packages."""

    def test_loads_standard_operational_skill(self):
        """A standard operational skill with complete metadata loads successfully."""
        from runtime.skills.registry import SkillRegistry

        registry = SkillRegistry(skills_root=FIXTURES)
        entry = registry.get("hr:standard-skill")

        assert entry is not None
        assert entry.id == "hr:standard-skill"
        assert entry.slug == "standard-skill"
        assert entry.name == "Standard Operational Skill"
        assert entry.version == "1.0.0"
        assert entry.policy_class == "standard_operational"
        assert entry.status == "enabled"
        assert entry.owner == "engineering_manager"
        assert entry.when_to_use == "Use when testing standard operational skills."

    def test_loads_high_impact_policy_skill(self):
        """A high_impact_policy skill loads successfully."""
        from runtime.skills.registry import SkillRegistry

        registry = SkillRegistry(skills_root=FIXTURES)
        entry = registry.get("hr:high-impact-skill")

        assert entry is not None
        assert entry.policy_class == "high_impact_policy"

    def test_skips_system_contract_skills(self):
        """system_contract skills are NOT loaded as toggleable entries."""
        from runtime.skills.registry import SkillRegistry

        registry = SkillRegistry(skills_root=FIXTURES)
        entry = registry.get("hr:system-contract-skill")

        assert entry is None
        # Confirm it is also absent from the full catalog
        all_ids = [e.id for e in registry.list_all()]
        assert "hr:system-contract-skill" not in all_ids

    def test_loads_all_non_system_skills(self):
        """list_all() returns all loaded skills (excluding system_contract)."""
        from runtime.skills.registry import SkillRegistry

        registry = SkillRegistry(skills_root=FIXTURES)
        all_entries = registry.list_all()

        ids = {e.id for e in all_entries}
        assert "hr:standard-skill" in ids
        assert "hr:high-impact-skill" in ids
        assert "hr:disabled-skill" in ids
        assert "hr:draft-skill" in ids
        assert "hr:minimal-skill" in ids
        # system_contract excluded
        assert "hr:system-contract-skill" not in ids

    def test_id_is_namespaced_hr_prefix(self):
        """Skill ids must use the hr:<slug> namespace."""
        from runtime.skills.registry import SkillRegistry

        registry = SkillRegistry(skills_root=FIXTURES)
        for entry in registry.list_all():
            assert entry.id.startswith("hr:"), f"Expected hr: prefix, got {entry.id}"

    def test_version_is_preserved(self):
        """Version string is loaded verbatim."""
        from runtime.skills.registry import SkillRegistry

        registry = SkillRegistry(skills_root=FIXTURES)
        entry = registry.get("hr:standard-skill")
        assert entry.version == "1.0.0"

    def test_optional_fields_loaded(self):
        """Optional fields like tags and compatibility are loaded when present."""
        from runtime.skills.registry import SkillRegistry

        registry = SkillRegistry(skills_root=FIXTURES)
        entry = registry.get("hr:standard-skill")

        assert entry.tags == ["testing", "standard"]
        assert entry.compatibility == {"executors": ["codex", "claude", "opencode"]}

    def test_optional_fields_none_when_absent(self):
        """Optional fields are None when not in the YAML."""
        from runtime.skills.registry import SkillRegistry

        registry = SkillRegistry(skills_root=FIXTURES)
        entry = registry.get("hr:minimal-skill")

        assert entry.tags is None
        assert entry.compatibility is None
        assert entry.supersedes is None

    def test_skill_with_missing_required_fields_still_loads_with_defaults(self):
        """Skills missing required fields should load, but might have validation warnings later.
        The registry loader itself should not crash on missing fields."""
        from runtime.skills.registry import SkillRegistry

        registry = SkillRegistry(skills_root=FIXTURES)
        # The missing-metadata skill should still be in the catalog
        all_ids = [e.id for e in registry.list_all()]
        assert "hr:missing-metadata-skill" in all_ids

    def test_disabled_skill_still_in_catalog(self):
        """A disabled skill is still in the catalog (gate check happens at exposure time)."""
        from runtime.skills.registry import SkillRegistry

        registry = SkillRegistry(skills_root=FIXTURES)
        entry = registry.get("hr:disabled-skill")
        assert entry is not None
        assert entry.status == "disabled"

    def test_get_nonexistent_returns_none(self):
        """get() returns None for unknown ids."""
        from runtime.skills.registry import SkillRegistry

        registry = SkillRegistry(skills_root=FIXTURES)
        assert registry.get("hr:nonexistent") is None

    def test_loader_reads_skill_md_path(self):
        """The registry records the path to SKILL.md for on-demand loading."""
        from runtime.skills.registry import SkillRegistry

        registry = SkillRegistry(skills_root=FIXTURES)
        entry = registry.get("hr:standard-skill")
        assert entry.skill_md_path is not None
        assert entry.skill_md_path.name == "SKILL.md"
        assert entry.skill_md_path.exists()


class TestReflectionSkillRegistration:
    """Tests for the reflection skill as a managed-catalog standard_operational entry."""

    def test_reflection_skill_loaded_as_standard_operational(self):
        """The reflection skill is loaded as a managed catalog entry (not system_contract)."""
        from runtime.skills.registry import SkillRegistry

        registry = SkillRegistry(skills_root=FIXTURES)
        entry = registry.get("hr:reflection")

        assert entry is not None, "reflection skill should be in managed catalog"
        assert entry.id == "hr:reflection"
        assert entry.slug == "reflection"
        assert entry.policy_class == "standard_operational"
        assert entry.status == "enabled"

    def test_reflection_skill_has_required_metadata(self):
        """The reflection skill carries owner, version, source."""
        from runtime.skills.registry import SkillRegistry

        registry = SkillRegistry(skills_root=FIXTURES)
        entry = registry.get("hr:reflection")

        assert entry.name == "Reflection"
        assert entry.version == "1.0.0"
        assert entry.owner == "engineering_manager"
        assert entry.source.startswith("runtime/skills/reflection")
        assert entry.when_to_use != ""
        assert entry.description != ""

    def test_reflection_skill_has_skill_md(self):
        """The reflection skill's SKILL.md body is available."""
        from runtime.skills.registry import SkillRegistry

        registry = SkillRegistry(skills_root=FIXTURES)
        entry = registry.get("hr:reflection")

        assert entry.skill_md_path is not None
        assert entry.skill_md_path.name == "SKILL.md"
        assert entry.skill_md_path.exists()

    def test_reflection_skill_appears_in_catalog_list(self):
        """list_all() includes the reflection skill."""
        from runtime.skills.registry import SkillRegistry

        registry = SkillRegistry(skills_root=FIXTURES)
        all_ids = {e.id for e in registry.list_all()}
        assert "hr:reflection" in all_ids


class TestManageAgentManageRepoRegistration:
    """Tests for manage-agent and manage-repo as high_impact_policy managed-catalog entries (THR-055 Phase 3)."""

    def test_manage_agent_loaded_as_high_impact_policy(self):
        """manage-agent is loaded as a managed catalog entry with high_impact_policy."""
        from runtime.skills.registry import SkillRegistry

        registry = SkillRegistry(skills_root=FIXTURES)
        entry = registry.get("hr:manage-agent")

        assert entry is not None, "manage-agent should be in managed catalog"
        assert entry.id == "hr:manage-agent"
        assert entry.slug == "manage-agent"
        assert entry.policy_class == "high_impact_policy"
        assert entry.status == "enabled"

    def test_manage_repo_loaded_as_high_impact_policy(self):
        """manage-repo is loaded as a managed catalog entry with high_impact_policy."""
        from runtime.skills.registry import SkillRegistry

        registry = SkillRegistry(skills_root=FIXTURES)
        entry = registry.get("hr:manage-repo")

        assert entry is not None, "manage-repo should be in managed catalog"
        assert entry.id == "hr:manage-repo"
        assert entry.slug == "manage-repo"
        assert entry.policy_class == "high_impact_policy"
        assert entry.status == "enabled"

    def test_manage_agent_has_required_metadata(self):
        """manage-agent carries owner, version, source."""
        from runtime.skills.registry import SkillRegistry

        registry = SkillRegistry(skills_root=FIXTURES)
        entry = registry.get("hr:manage-agent")

        assert entry.name == "Manage Agent"
        assert entry.version == "1.0.0"
        assert entry.owner == "engineering_manager"
        assert entry.source.startswith("runtime/skills/manage-agent")
        assert entry.when_to_use != ""
        assert entry.description != ""

    def test_manage_repo_has_required_metadata(self):
        """manage-repo carries owner, version, source."""
        from runtime.skills.registry import SkillRegistry

        registry = SkillRegistry(skills_root=FIXTURES)
        entry = registry.get("hr:manage-repo")

        assert entry.name == "Manage Repo"
        assert entry.version == "1.0.0"
        assert entry.owner == "engineering_manager"
        assert entry.source.startswith("runtime/skills/manage-repo")
        assert entry.when_to_use != ""
        assert entry.description != ""

    def test_manage_agent_has_skill_md(self):
        """manage-agent SKILL.md body is available."""
        from runtime.skills.registry import SkillRegistry

        registry = SkillRegistry(skills_root=FIXTURES)
        entry = registry.get("hr:manage-agent")

        assert entry.skill_md_path is not None
        assert entry.skill_md_path.name == "SKILL.md"
        assert entry.skill_md_path.exists()

    def test_manage_repo_has_skill_md(self):
        """manage-repo SKILL.md body is available."""
        from runtime.skills.registry import SkillRegistry

        registry = SkillRegistry(skills_root=FIXTURES)
        entry = registry.get("hr:manage-repo")

        assert entry.skill_md_path is not None
        assert entry.skill_md_path.name == "SKILL.md"
        assert entry.skill_md_path.exists()

    def test_both_skills_appear_in_catalog_list(self):
        """list_all() includes manage-agent and manage-repo."""
        from runtime.skills.registry import SkillRegistry

        registry = SkillRegistry(skills_root=FIXTURES)
        all_ids = {e.id for e in registry.list_all()}
        assert "hr:manage-agent" in all_ids
        assert "hr:manage-repo" in all_ids

    def test_manager_skills_not_system_contracts(self):
        """manage-agent and manage-repo are NOT system contracts."""
        from runtime.skills.system_contracts import list_system_contracts

        contracts = list_system_contracts()
        ids = {sc.id for sc in contracts}
        assert "manage-agent" not in ids
        assert "manage-repo" not in ids


class TestSkillRegistryEmptyDirectory:
    """Tests for empty or missing skills root."""

    def test_empty_directory_returns_empty_catalog(self, tmp_path):
        """An empty skills directory yields an empty catalog (no crash)."""
        from runtime.skills.registry import SkillRegistry

        empty_dir = tmp_path / "empty_skills"
        empty_dir.mkdir()
        registry = SkillRegistry(skills_root=empty_dir)
        assert registry.list_all() == []

    def test_nonexistent_directory_returns_empty_catalog(self, tmp_path):
        """A non-existent skills root yields an empty catalog (no crash)."""
        from runtime.skills.registry import SkillRegistry

        nonexistent = tmp_path / "nonexistent_skills"
        registry = SkillRegistry(skills_root=nonexistent)
        assert registry.list_all() == []

    def test_directory_with_no_skill_yamls_skips(self, tmp_path):
        """A directory with only non-skill content is skipped gracefully."""
        from runtime.skills.registry import SkillRegistry

        dir_with_other = tmp_path / "not_a_skill"
        dir_with_other.mkdir()
        (dir_with_other / "README.md").write_text("not a skill")
        registry = SkillRegistry(skills_root=tmp_path)
        # not_a_skill has no skill.yaml, so it's skipped
        assert registry.get("hr:not-a-skill") is None
