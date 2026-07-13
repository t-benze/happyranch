"""Tests for per-org user-skill store union with release registry.

Covers:
- Union of release registry + user store
- Release-wins on slug collision
- Empty/missing user store treated gracefully
- Entries carry source='user_authored'
"""
from __future__ import annotations

import pytest
from pathlib import Path


FIXTURES = Path(__file__).parent / "fixtures" / "skills"


def _write_user_skill(dir_path: Path, slug: str, skill_id: str, version: str = "0.1.0") -> None:
    """Write a minimal user-authored skill.yaml to a slug directory."""
    import yaml
    pkg_dir = dir_path / slug
    pkg_dir.mkdir(parents=True, exist_ok=True)
    (pkg_dir / "skill.yaml").write_text(yaml.dump({
        "id": skill_id,
        "slug": slug,
        "name": slug.replace("-", " ").title(),
        "version": version,
        "description": f"User-authored skill {slug}",
        "when_to_use": "When needed",
        "owner": "operator",
        "source": "user_authored",
        "policy_class": "standard_operational",
        "status": "enabled",
    }))
    (pkg_dir / "SKILL.md").write_text(f"# {slug}\n\nUser-authored content.\n")


class TestUnionReleaseWins:
    """Release registry wins on slug collision."""

    def test_release_wins_on_id_collision(self, tmp_path):
        """When a user skill has the same skill_id as a release skill,
        the release entry is kept and the user entry is discarded."""
        from runtime.skills.registry import SkillRegistry

        # Load the release fixtures as the "release" registry
        release_registry = SkillRegistry(skills_root=FIXTURES)

        # Create a user skill with an id that collides with a release skill
        user_dir = tmp_path / "user_skills"
        user_dir.mkdir()
        _write_user_skill(user_dir, "standard-skill", "hr:standard-skill", version="99.0.0")

        user_registry = SkillRegistry(skills_root=user_dir)

        # Union: release wins
        union: dict[str, tuple] = {}
        # User entries first, then release overwrites
        for entry in user_registry.list_all():
            union[entry.id] = (entry, "user_authored")
        for entry in release_registry.list_all():
            union[entry.id] = (entry, entry.source)

        # Check: the colliding entry is from release, not user
        assert union["hr:standard-skill"][0].version == "1.0.0"  # release version
        assert union["hr:standard-skill"][1] != "user_authored"

    def test_non_colliding_user_skill_is_kept(self, tmp_path):
        """A user skill with a non-colliding id is retained."""
        from runtime.skills.registry import SkillRegistry

        release_registry = SkillRegistry(skills_root=FIXTURES)
        release_ids = {e.id for e in release_registry.list_all()}

        user_dir = tmp_path / "user_skills"
        user_dir.mkdir()
        _write_user_skill(user_dir, "my-custom-skill", "hr:my-custom-skill")

        user_registry = SkillRegistry(skills_root=user_dir)

        # Verify no collision
        assert "hr:my-custom-skill" not in release_ids

        # Union
        union: dict[str, tuple] = {}
        for entry in user_registry.list_all():
            union[entry.id] = (entry, "user_authored")
        for entry in release_registry.list_all():
            union[entry.id] = (entry, entry.source)

        # User entry is preserved
        assert "hr:my-custom-skill" in union
        assert union["hr:my-custom-skill"][1] == "user_authored"

    def test_user_entry_has_user_authored_source(self, tmp_path):
        """User store entries carry source='user_authored'."""
        from runtime.skills.registry import SkillRegistry

        user_dir = tmp_path / "user_skills"
        user_dir.mkdir()
        _write_user_skill(user_dir, "my-custom-skill", "hr:my-custom-skill")

        user_registry = SkillRegistry(skills_root=user_dir)
        entries = user_registry.list_all()
        assert len(entries) == 1
        assert entries[0].source == "user_authored"


class TestEmptyOrMissingUserStore:
    """Missing or empty user store is handled gracefully."""

    def test_missing_user_dir_returns_empty(self, tmp_path):
        """When the user skills directory doesn't exist, union returns only release entries."""
        from runtime.skills.registry import SkillRegistry

        release_registry = SkillRegistry(skills_root=FIXTURES)
        nonexistent = tmp_path / "nonexistent"

        # We're testing the union pattern, not the registry itself
        user_registry = SkillRegistry(skills_root=nonexistent)
        assert user_registry.list_all() == []

        # Union fallback: empty user store, full release
        union: dict[str, tuple] = {}
        for entry in user_registry.list_all():
            union[entry.id] = (entry, "user_authored")
        for entry in release_registry.list_all():
            union[entry.id] = (entry, entry.source)

        assert len(union) == len(release_registry.list_all())

    def test_empty_user_dir_returns_empty(self, tmp_path):
        """When the user skills directory is empty, union returns only release entries."""
        from runtime.skills.registry import SkillRegistry

        release_registry = SkillRegistry(skills_root=FIXTURES)
        empty_dir = tmp_path / "empty_skills"
        empty_dir.mkdir()

        user_registry = SkillRegistry(skills_root=empty_dir)
        assert user_registry.list_all() == []

        union: dict[str, tuple] = {}
        for entry in user_registry.list_all():
            union[entry.id] = (entry, "user_authored")
        for entry in release_registry.list_all():
            union[entry.id] = (entry, entry.source)

        assert len(union) == len(release_registry.list_all())


class TestUnionCatalogFunction:
    """Test the reusable union_catalog function pattern."""

    def test_user_authored_skills_marked_as_type_user_authored(self, tmp_path):
        """User-authored skills in the union get type='user_authored'."""
        from runtime.skills.registry import SkillRegistry

        user_dir = tmp_path / "user_skills"
        user_dir.mkdir()
        _write_user_skill(user_dir, "my-skill", "hr:my-skill")

        release_registry = SkillRegistry(skills_root=FIXTURES)
        user_registry = SkillRegistry(skills_root=user_dir)

        # Build union (release-wins)
        union: dict[str, tuple] = {}
        for entry in user_registry.list_all():
            union[entry.id] = (entry, "user_authored")
        for entry in release_registry.list_all():
            union[entry.id] = (entry, entry.source)

        # All release entries are NOT user_authored
        for entry in release_registry.list_all():
            assert union[entry.id][1] != "user_authored"

        # User entry IS user_authored
        assert union["hr:my-skill"][1] == "user_authored"
        assert union["hr:my-skill"][0].source == "user_authored"
