"""THR-055 B2 Slice A1 additive custom-skill schema tests."""

from __future__ import annotations

import sqlite3

import pytest

from runtime.infrastructure.database import Database
from runtime.skills import custom_store


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
    def test_logical_purge_schema_is_additive_and_restart_idempotent(self, tmp_path):
        path = tmp_path / "upgrade.db"
        baseline = Database(path)
        baseline.close()
        conn = sqlite3.connect(path)
        conn.execute("DROP TABLE custom_skill_purge_events")
        conn.execute("ALTER TABLE custom_skills DROP COLUMN purge_id")
        conn.execute("ALTER TABLE custom_skills DROP COLUMN purged_at")
        conn.commit()
        conn.close()

        upgraded = Database(path)
        try:
            assert {"purged_at", "purge_id"}.issubset(
                _table_columns(upgraded._conn, "custom_skills")
            )
            assert upgraded._conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' "
                "AND name='custom_skill_purge_events'"
            ).fetchone()
            event_sql = upgraded._conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' "
                "AND name='custom_skill_events'"
            ).fetchone()[0]
            assert "'retired'" in event_sql and "'restored'" in event_sql
            assert "purged" not in event_sql
        finally:
            upgraded.close()

        restarted = Database(path)
        try:
            assert _table_columns(restarted._conn, "custom_skills").count("purged_at") == 1
            assert _table_columns(restarted._conn, "custom_skills").count("purge_id") == 1
            assert restarted._conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        finally:
            restarted.close()

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

    def test_existing_db_retires_all_lifecycle_tables_without_touching_b2_collision(self, tmp_path):
        path = tmp_path / "retirement.db"
        db = Database(path)
        version_id = _create_skill_with_version(db._conn, "custom:collision")
        db._conn.commit()
        db.close()
        artifact = path.parent / "artifacts" / "legacy" / "content" / "SKILL.md"
        artifact.parent.mkdir(parents=True)
        artifact.write_text("# retired")
        conn = sqlite3.connect(path)
        conn.executescript(
            """
            CREATE TABLE skill_lifecycle_packages (
                id INTEGER PRIMARY KEY, status TEXT, content_artifact_key TEXT
            );
            CREATE TABLE skill_lifecycle_events (id INTEGER PRIMARY KEY);
            CREATE TABLE skill_lifecycle_assignments (id INTEGER PRIMARY KEY);
            CREATE TABLE skill_lifecycle_materializations (id INTEGER PRIMARY KEY);
            """
        )
        for status in ("proposed", "validated", "approved", "published", "rejected", "retired"):
            conn.execute(
                "INSERT INTO skill_lifecycle_packages (status, content_artifact_key) VALUES (?, ?)",
                (status, "legacy/content/SKILL.md"),
            )
        conn.commit()
        conn.close()

        retired = Database(path)
        try:
            assert retired._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'skill_lifecycle_%'"
            ).fetchall() == []
            assert not artifact.exists()
            row = retired._conn.execute(
                "SELECT current_version_id FROM custom_skills WHERE id='custom:collision'"
            ).fetchone()
            assert row[0] == version_id
            assert retired._conn.execute(
                "SELECT content_hash FROM custom_skill_versions WHERE id=?", (version_id,)
            ).fetchone()[0] == "hash-1"
        finally:
            retired.close()
