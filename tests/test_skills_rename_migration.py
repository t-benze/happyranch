"""THR-106 one-shot startup migration: hr:review → hr:reflection in the
persisted per-org ``org/config.yaml`` skills eligibility section.

The managed self-reflection skill was renamed (id ``hr:review`` →
``hr:reflection``, slug ``review`` → ``reflection``). Skill eligibility is
read from each deployed org's ``org/config.yaml`` (no DB storage), so a bare
catalog rename would strand every org that persists ``allow: [hr:review]``
(unknown-skill-id warning, skill silently dropped from every effective set).

``migrate_hr_review_skill_id`` rewrites the old id to the new id inside the
``skills:`` section only (allow AND deny lists, org/team/agent scope),
guarded by a durable ``.hr_review_renamed`` sentinel in the org root —
mirroring the ``.agent_yaml_consumed`` one-shot pattern.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from runtime.orchestrator._paths import OrgPaths


SENTINEL = ".hr_review_renamed"


def _paths(tmp_path: Path) -> OrgPaths:
    paths = OrgPaths(root=tmp_path)
    paths.org_dir.mkdir(parents=True, exist_ok=True)
    return paths


def _write_config(paths: OrgPaths, body: str) -> None:
    paths.org_config_path.write_text(body, encoding="utf-8")


FULL_SCOPE_CONFIG = """\
# Seeded org config — comment must survive the migration byte-for-byte.
timezone: Asia/Shanghai

# ── Skill eligibility policy ────────────────────────────────────────
skills:
  org:
    allow:
      - hr:review
    deny:
      - hr:review
  teams:
    engineering:
      allow:
        - hr:review
      deny: []
  agents:
    product_lead:
      allow:
        - hr:review
        - hr:manage-agent
      deny:
        - hr:review
    engineering_manager:
      allow:
        - hr:manage-agent
      deny: []

max_revise_rounds: 3
"""


class TestMigrateHrReviewSkillId:
    def test_rewrites_allow_and_deny_at_every_scope(self, tmp_path):
        from runtime.orchestrator.org_config import migrate_hr_review_skill_id

        paths = _paths(tmp_path)
        _write_config(paths, FULL_SCOPE_CONFIG)

        outcome = migrate_hr_review_skill_id(paths)

        assert "renamed" in outcome
        raw = yaml.safe_load(paths.org_config_path.read_text())
        skills = raw["skills"]
        assert skills["org"]["allow"] == ["hr:reflection"]
        assert skills["org"]["deny"] == ["hr:reflection"]
        assert skills["teams"]["engineering"]["allow"] == ["hr:reflection"]
        assert skills["agents"]["product_lead"]["allow"] == [
            "hr:reflection", "hr:manage-agent",
        ]
        assert skills["agents"]["product_lead"]["deny"] == ["hr:reflection"]
        # Untouched entries survive verbatim.
        assert skills["agents"]["engineering_manager"]["allow"] == ["hr:manage-agent"]
        assert "hr:review" not in paths.org_config_path.read_text()

    def test_unrelated_config_is_byte_identical(self, tmp_path):
        """Only the ``hr:review`` tokens change — comments, spacing, and
        every other section survive byte-for-byte (no YAML re-dump)."""
        from runtime.orchestrator.org_config import migrate_hr_review_skill_id

        paths = _paths(tmp_path)
        _write_config(paths, FULL_SCOPE_CONFIG)

        migrate_hr_review_skill_id(paths)

        after = paths.org_config_path.read_text()
        expected = FULL_SCOPE_CONFIG.replace("hr:review", "hr:reflection")
        assert after == expected

    def test_writes_sentinel_and_second_run_is_noop(self, tmp_path):
        from runtime.orchestrator.org_config import migrate_hr_review_skill_id

        paths = _paths(tmp_path)
        _write_config(paths, FULL_SCOPE_CONFIG)

        migrate_hr_review_skill_id(paths)
        assert (tmp_path / SENTINEL).exists()

        first_pass = paths.org_config_path.read_text()
        outcome2 = migrate_hr_review_skill_id(paths)
        assert outcome2 == "skipped (already migrated)"
        assert paths.org_config_path.read_text() == first_pass

    def test_existing_sentinel_blocks_rewrite_even_with_old_id(self, tmp_path):
        """One-shot semantics: once the sentinel exists, a reintroduced
        ``hr:review`` is NEVER rewritten again."""
        from runtime.orchestrator.org_config import migrate_hr_review_skill_id

        paths = _paths(tmp_path)
        _write_config(paths, FULL_SCOPE_CONFIG)
        (tmp_path / SENTINEL).write_text("")

        outcome = migrate_hr_review_skill_id(paths)

        assert outcome == "skipped (already migrated)"
        assert paths.org_config_path.read_text() == FULL_SCOPE_CONFIG

    def test_missing_config_locks_sentinel(self, tmp_path):
        from runtime.orchestrator.org_config import migrate_hr_review_skill_id

        paths = _paths(tmp_path)

        outcome = migrate_hr_review_skill_id(paths)

        assert outcome == "locked (no config.yaml)"
        assert (tmp_path / SENTINEL).exists()

    def test_no_skills_section_locks_sentinel_and_leaves_file_untouched(self, tmp_path):
        from runtime.orchestrator.org_config import migrate_hr_review_skill_id

        paths = _paths(tmp_path)
        body = "timezone: UTC\nmax_revise_rounds: 2\n"
        _write_config(paths, body)

        outcome = migrate_hr_review_skill_id(paths)

        assert outcome == "locked (no skills section)"
        assert (tmp_path / SENTINEL).exists()
        assert paths.org_config_path.read_text() == body

    def test_empty_flow_mapping_config_locks_sentinel(self, tmp_path):
        """The live post-THR-095 shape (``{}``) must lock cleanly."""
        from runtime.orchestrator.org_config import migrate_hr_review_skill_id

        paths = _paths(tmp_path)
        _write_config(paths, "{}\n")

        outcome = migrate_hr_review_skill_id(paths)

        assert outcome == "locked (no skills section)"
        assert (tmp_path / SENTINEL).exists()
        assert paths.org_config_path.read_text() == "{}\n"

    def test_skills_section_without_old_id_locks_without_rewrite(self, tmp_path):
        from runtime.orchestrator.org_config import migrate_hr_review_skill_id

        paths = _paths(tmp_path)
        body = (
            "skills:\n"
            "  teams:\n"
            "    engineering:\n"
            "      allow:\n"
            "        - hr:reflection\n"
            "      deny: []\n"
        )
        _write_config(paths, body)

        outcome = migrate_hr_review_skill_id(paths)

        assert outcome == "locked (no hr:review references)"
        assert (tmp_path / SENTINEL).exists()
        assert paths.org_config_path.read_text() == body

    def test_malformed_yaml_raises_and_leaves_no_sentinel(self, tmp_path):
        """A file we cannot parse is never rewritten and never locked —
        the next startup retries after the operator fixes it."""
        from runtime.orchestrator.org_config import (
            OrgConfigError,
            migrate_hr_review_skill_id,
        )

        paths = _paths(tmp_path)
        bad = "skills:\n  teams: [oops\n"
        _write_config(paths, bad)

        with pytest.raises(OrgConfigError):
            migrate_hr_review_skill_id(paths)

        assert not (tmp_path / SENTINEL).exists()
        assert paths.org_config_path.read_text() == bad

    def test_rewrite_is_scoped_to_the_skills_section(self, tmp_path):
        """A literal ``hr:review`` OUTSIDE the skills block (e.g. prose in an
        unrelated section) is left alone."""
        from runtime.orchestrator.org_config import migrate_hr_review_skill_id

        paths = _paths(tmp_path)
        body = (
            "notes: hr:review used to live here\n"
            "skills:\n"
            "  teams:\n"
            "    engineering:\n"
            "      allow:\n"
            "        - hr:review\n"
            "      deny: []\n"
            "max_revise_rounds: 4\n"
        )
        _write_config(paths, body)

        outcome = migrate_hr_review_skill_id(paths)

        assert "renamed" in outcome
        after = paths.org_config_path.read_text()
        assert "notes: hr:review used to live here\n" in after
        raw = yaml.safe_load(after)
        assert raw["skills"]["teams"]["engineering"]["allow"] == ["hr:reflection"]
        assert raw["max_revise_rounds"] == 4

    def test_does_not_mangle_longer_ids_sharing_the_prefix(self, tmp_path):
        """Token replacement must not rewrite ids that merely start with
        ``hr:review`` (e.g. a hypothetical ``hr:review-notes``)."""
        from runtime.orchestrator.org_config import migrate_hr_review_skill_id

        paths = _paths(tmp_path)
        body = (
            "skills:\n"
            "  teams:\n"
            "    engineering:\n"
            "      allow:\n"
            "        - hr:review\n"
            "        - hr:review-notes\n"
            "      deny: []\n"
        )
        _write_config(paths, body)

        migrate_hr_review_skill_id(paths)

        raw = yaml.safe_load(paths.org_config_path.read_text())
        assert raw["skills"]["teams"]["engineering"]["allow"] == [
            "hr:reflection", "hr:review-notes",
        ]

    def test_inline_flow_list_is_rewritten(self, tmp_path):
        """Deployed configs written by yaml.safe_dump may use flow lists
        (``allow: [hr:review]``)."""
        from runtime.orchestrator.org_config import migrate_hr_review_skill_id

        paths = _paths(tmp_path)
        body = (
            "skills:\n"
            "  teams:\n"
            "    engineering: {allow: [hr:review], deny: [hr:review]}\n"
        )
        _write_config(paths, body)

        migrate_hr_review_skill_id(paths)

        raw = yaml.safe_load(paths.org_config_path.read_text())
        assert raw["skills"]["teams"]["engineering"]["allow"] == ["hr:reflection"]
        assert raw["skills"]["teams"]["engineering"]["deny"] == ["hr:reflection"]
