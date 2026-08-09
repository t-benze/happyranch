"""THR-055 B2 Slice A1 additive custom-skill schema tests."""

from __future__ import annotations

import sqlite3

import pytest

from runtime.infrastructure.database import Database
from runtime.skills import custom_store
from runtime.skills.lifecycle import stores as lifecycle_stores


def _table_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]


def _create_skill_with_version(conn: sqlite3.Connection, skill_id: str = "hr:example") -> int:
    return custom_store.create_skill_with_first_version(
        conn,
        skill_id=skill_id,
        org_slug="happyranch",
        slug=skill_id.removeprefix("hr:"),
        name="Example",
        origin_kind="agent",
        origin_agent="dev_agent",
        created_by="dev_agent",
        content_hash="hash-1",
        content_artifact_key="custom-skills/hash-1",
        author_kind="agent",
        author_identity="dev_agent",
        created_at="2026-08-09T00:00:00+00:00",
    )


class TestCustomSkillSchema:
    def test_creation_rolls_back_skill_when_first_version_insert_fails(self, tmp_path, monkeypatch):
        db = Database(tmp_path / "custom-skills.db")
        try:
            before_skills = db.execute("SELECT COUNT(*) FROM custom_skills").fetchone()[0]
            before_versions = db.execute("SELECT COUNT(*) FROM custom_skill_versions").fetchone()[0]

            def fail_after_skill_insert(*args, **kwargs):
                raise sqlite3.IntegrityError("forced first-version failure")

            monkeypatch.setattr(custom_store, "_insert_first_version", fail_after_skill_insert)
            with pytest.raises(sqlite3.IntegrityError, match="forced first-version failure"):
                _create_skill_with_version(db._conn, "hr:rollback")

            assert db.execute("SELECT COUNT(*) FROM custom_skills").fetchone()[0] == before_skills
            assert db.execute("SELECT COUNT(*) FROM custom_skill_versions").fetchone()[0] == before_versions
            assert db.execute(
                "SELECT 1 FROM custom_skills WHERE id = ?", ("hr:rollback",)
            ).fetchone() is None
        finally:
            db.close()

    def test_current_eligibility_rule_is_unique_and_replacement_is_atomic(self, tmp_path):
        db = Database(tmp_path / "custom-skills.db")
        conn = db._conn
        try:
            _create_skill_with_version(conn)
            common = ("hr:example", "agent", "dev_agent", "allow", "2026-08-09T00:00:00+00:00", "founder")
            conn.execute(
                """INSERT INTO custom_skill_eligibility_rules
                   (skill_id, scope_type, scope_target, effect, created_at, created_by)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                common,
            )
            conn.commit()

            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    """INSERT INTO custom_skill_eligibility_rules
                       (skill_id, scope_type, scope_target, effect, created_at, created_by)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    common,
                )
            conn.rollback()

            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """UPDATE custom_skill_eligibility_rules
                   SET superseded_at = ?
                   WHERE skill_id = ? AND scope_type = ? AND scope_target = ?
                     AND superseded_at IS NULL""",
                ("2026-08-09T00:01:00+00:00", "hr:example", "agent", "dev_agent"),
            )
            conn.execute(
                """INSERT INTO custom_skill_eligibility_rules
                   (skill_id, scope_type, scope_target, effect, created_at, created_by)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                ("hr:example", "agent", "dev_agent", "deny", "2026-08-09T00:01:00+00:00", "founder"),
            )
            conn.commit()

            assert conn.execute(
                """SELECT COUNT(*) FROM custom_skill_eligibility_rules
                   WHERE skill_id = ? AND scope_type = ? AND scope_target = ?
                     AND superseded_at IS NULL""",
                ("hr:example", "agent", "dev_agent"),
            ).fetchone()[0] == 1
        finally:
            db.close()

    def test_materializations_allow_all_session_contexts_with_task_only_requirement(self, tmp_path):
        db = Database(tmp_path / "custom-skills.db")
        conn = db._conn
        try:
            version_id = _create_skill_with_version(conn)
            for context, task_id in (("task", "TASK-1"), ("thread", None), ("wake", None), ("dream", None)):
                conn.execute(
                    """INSERT INTO custom_skill_materializations
                       (skill_id, agent_name, task_id, session_context, session_id,
                        version_id, content_hash, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    ("hr:example", "dev_agent", task_id, context, f"sess-{context}",
                     version_id, "hash-1", "2026-08-09T00:00:00+00:00"),
                )
            conn.commit()
            assert conn.execute("SELECT COUNT(*) FROM custom_skill_materializations").fetchone()[0] == 4

            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    """INSERT INTO custom_skill_materializations
                       (skill_id, agent_name, task_id, session_context, session_id,
                        version_id, content_hash, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    ("hr:example", "dev_agent", None, "task", "sess-invalid", version_id,
                     "hash-1", "2026-08-09T00:00:00+00:00"),
                )
        finally:
            db.close()

    def test_additive_migration_preserves_populated_legacy_lifecycle_fixture(self, tmp_path):
        path = tmp_path / "legacy-lifecycle.db"
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript(lifecycle_stores.CREATE_PACKAGE_VERSIONS)
        conn.executescript(lifecycle_stores.CREATE_LIFECYCLE_EVENTS)
        conn.executescript(lifecycle_stores.CREATE_ASSIGNMENTS)
        conn.executescript(lifecycle_stores.CREATE_MATERIALIZATIONS)
        conn.execute(
            """INSERT INTO skill_lifecycle_packages
               (skill_id, slug, name, version, content_hash, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            ("hr:proposed", "proposed", "Proposed", "0.1", "hash-proposed", "proposed", "2026-08-09T00:00:00+00:00"),
        )
        published_version = conn.execute(
            """INSERT INTO skill_lifecycle_packages
               (skill_id, slug, name, version, content_hash, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            ("hr:published", "published", "Published", "0.1", "hash-published", "published", "2026-08-09T00:00:00+00:00"),
        ).lastrowid
        conn.execute(
            """INSERT INTO skill_lifecycle_packages
               (skill_id, slug, name, version, content_hash, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            ("hr:rejected", "rejected", "Rejected", "0.1", "hash-rejected", "rejected", "2026-08-09T00:00:00+00:00"),
        )
        retired_version = conn.execute(
            """INSERT INTO skill_lifecycle_packages
               (skill_id, slug, name, version, content_hash, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            ("hr:retired", "retired", "Retired", "0.1", "hash-retired", "retired", "2026-08-09T00:00:00+00:00"),
        ).lastrowid
        conn.execute(
            """INSERT INTO skill_lifecycle_events
               (skill_id, package_version_id, event_type, actor, actor_role, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            ("hr:proposed", 1, "proposed", "dev_agent", "agent", "2026-08-09T00:00:00+00:00"),
        )
        conn.execute(
            """INSERT INTO skill_lifecycle_assignments
               (skill_id, agent_name, package_version_id, version, content_hash, assigned_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            ("hr:published", "dev_agent", published_version, "0.1", "hash-published", "2026-08-09T00:00:00+00:00"),
        )
        conn.execute(
            """INSERT INTO skill_lifecycle_materializations
               (skill_id, agent_name, package_version_id, version, content_hash, session_context, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            ("hr:retired", "dev_agent", retired_version, "0.1", "hash-retired", "task", "2026-08-09T00:00:00+00:00"),
        )
        conn.commit()
        legacy_tables = (
            "skill_lifecycle_packages",
            "skill_lifecycle_events",
            "skill_lifecycle_assignments",
            "skill_lifecycle_materializations",
        )
        before_rows = {
            table: [tuple(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY id")]
            for table in legacy_tables
        }
        before_info = {table: list(conn.execute(f"PRAGMA table_info({table})")) for table in legacy_tables}
        conn.close()

        db = Database(path)
        try:
            for table in legacy_tables:
                assert [tuple(row) for row in db._conn.execute(f"SELECT * FROM {table} ORDER BY id")] == before_rows[table]
                assert list(db._conn.execute(f"PRAGMA table_info({table})")) == before_info[table]

            expected_columns = {
                "custom_skills": ["id", "org_slug", "slug", "name", "description", "policy_class", "origin_kind", "origin_agent", "created_at", "created_by", "current_version_id", "retired_at", "retired_by", "retired_reason"],
                "custom_skill_versions": ["id", "skill_id", "parent_version_id", "content_hash", "content_artifact_key", "skill_md_cache", "references_manifest", "assets_manifest", "validation_state", "validator_version", "validation_findings", "created_at", "author_kind", "author_identity", "source_task_id", "source_session_id", "task_brief_digest"],
                "custom_skill_eligibility_rules": ["id", "skill_id", "scope_type", "scope_target", "effect", "created_at", "created_by", "superseded_at"],
                "custom_skill_eligibility_events": ["id", "skill_id", "actor", "preview_revision", "rule_set_json", "affected_newly_visible", "affected_newly_hidden", "created_at"],
                "custom_skill_materializations": ["id", "skill_id", "agent_name", "task_id", "session_context", "session_id", "version_id", "content_hash", "success", "error_message", "created_at"],
                "custom_skill_events": ["id", "skill_id", "event_type", "actor", "version_id", "metadata_json", "created_at", "task_id", "session_id"],
            }
            for table, columns in expected_columns.items():
                assert _table_columns(db._conn, table) == columns
        finally:
            db.close()
