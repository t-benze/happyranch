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
        assert entry.approval_state == "approved"
        assert entry.status == "enabled"
        assert entry.owner == "engineering_manager"
        assert entry.when_to_use == "Use when testing standard operational skills."

    def test_loads_high_impact_policy_skill(self):
        """A high_impact_policy skill with founder approval loads successfully."""
        from runtime.skills.registry import SkillRegistry

        registry = SkillRegistry(skills_root=FIXTURES)
        entry = registry.get("hr:high-impact-skill")

        assert entry is not None
        assert entry.policy_class == "high_impact_policy"
        assert entry.approval_state == "approved"
        assert entry.approved_by == "founder"

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
