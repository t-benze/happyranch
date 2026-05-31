from __future__ import annotations

import sqlite3
from pathlib import Path


def _write_pre_migration_db(path: Path) -> None:
    """Build a SQLite DB with the pre-migration shape and a row per old status."""
    conn = sqlite3.connect(str(path))
    conn.executescript("""
        CREATE TABLE tasks (
            id TEXT PRIMARY KEY,
            type TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            assigned_agent TEXT,
            team TEXT NOT NULL DEFAULT 'product_engineering',
            brief TEXT NOT NULL,
            revision_count INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            completed_at TEXT,
            parent_task_id TEXT,
            final_output_summary TEXT,
            final_artifact_dir TEXT
        );
    """)
    ts = "2026-04-01T00:00:00+00:00"
    rows = [
        ("T-APR", "general", "approved", "agent-a", "done-summary", None),
        ("T-REJ", "general", "rejected", "agent-b", "rej-summary", None),
        ("T-ESC", "general", "escalated", "agent-c", "esc-reason", None),
        ("T-PEN", "general", "pending", None, None, None),
        ("T-PRO", "general", "in_progress", "agent-d", None, None),
        ("T-COMPLETED", "general", "completed", "agent-e", "old-complete", None),
        ("T-REVIEW", "general", "in_review", "agent-f", "old-review", None),
    ]
    for r in rows:
        conn.execute(
            "INSERT INTO tasks (id, type, status, assigned_agent, brief, "
            "revision_count, created_at, updated_at, final_output_summary, final_artifact_dir) "
            "VALUES (?, ?, ?, ?, 'brief', 0, ?, ?, ?, ?)",
            (r[0], r[1], r[2], r[3], ts, ts, r[4], r[5]),
        )
    conn.commit()
    conn.close()


def test_migration_maps_old_statuses(tmp_path: Path) -> None:
    db_path = tmp_path / "happyranch.db"
    _write_pre_migration_db(db_path)

    # Trigger the migration by opening the DB through our class.
    from src.infrastructure.database import Database
    db = Database(db_path)

    rows = {r["id"]: dict(r) for r in db._conn.execute("SELECT * FROM tasks")}

    # Status remaps
    assert rows["T-APR"]["status"] == "completed"
    assert rows["T-APR"]["block_kind"] is None
    assert rows["T-REJ"]["status"] == "failed"
    assert rows["T-REJ"]["block_kind"] is None
    assert rows["T-ESC"]["status"] == "blocked"
    assert rows["T-ESC"]["block_kind"] == "escalated"

    # Unchanged non-terminal rows remain unchanged
    assert rows["T-PEN"]["status"] == "pending"
    assert rows["T-PRO"]["status"] == "in_progress"

    # Dead-enum rows get normalized to failed (they were never written in
    # practice but a migration must still leave the table in a legal shape)
    assert rows["T-COMPLETED"]["status"] == "completed"  # already legal
    assert rows["T-REVIEW"]["status"] == "failed"         # in_review → failed

    # final_output_summary folded into note, column still present but unused
    assert rows["T-APR"]["note"] == "done-summary"
    assert rows["T-ESC"]["note"] == "esc-reason"

    # orchestration_step_count defaults to 0
    assert rows["T-PEN"]["orchestration_step_count"] == 0


def test_migration_is_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "happyranch.db"
    _write_pre_migration_db(db_path)
    from src.infrastructure.database import Database

    Database(db_path).close()
    # Re-open: migration already applied; this must not raise.
    db = Database(db_path)
    rows = list(db._conn.execute("SELECT status FROM tasks WHERE id='T-APR'"))
    assert rows[0]["status"] == "completed"


# ── Migration: <runtime>/teams.yaml + agent_enrollments → <runtime>/org/ ──

import pytest
import yaml

from src.orchestrator.migration import migrate_to_org_runtime, MigrationResult
from src.orchestrator._paths import OrgPaths
from src.runtime import RuntimeDir


def _build_legacy_runtime(tmp_path: Path, *, with_enrollments: bool = True) -> Path:
    """Construct a pre-org-cut runtime at tmp_path/legacy."""
    rt_root = tmp_path / "legacy"
    rt_root.mkdir()
    # happyranch.yaml without slug (the pre-cut shape).
    (rt_root / "happyranch.yaml").write_text("")
    (rt_root / "workspaces").mkdir()
    # teams.yaml at the OLD location (root, not under org/).
    (rt_root / "teams.yaml").write_text(
        "teams:\n  engineering:\n    manager: engineering_head\n    workers: [dev_agent]\n"
    )
    # SQLite with legacy agent_enrollments table.
    db_path = rt_root / "happyranch.db"
    conn = sqlite3.connect(db_path)
    conn.executescript("""
      CREATE TABLE agent_enrollments (
        name TEXT PRIMARY KEY,
        description TEXT,
        system_prompt TEXT,
        repos TEXT,
        executor TEXT,
        allow_rules TEXT,
        status TEXT,
        created_at TEXT
      );
    """)
    if with_enrollments:
        conn.execute(
            "INSERT INTO agent_enrollments VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("custom_dev", "A custom dev", "You are custom_dev.\n",
             '{"my-opc": "https://github.com/x/x.git"}', "claude",
             '[]', "approved", "2026-04-01T00:00:00Z"),
        )
        conn.execute(
            "INSERT INTO agent_enrollments VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("draft_writer", "Draft", "Body\n", '{}', "claude", '[]',
             "pending", "2026-04-02T00:00:00Z"),
        )
    conn.commit()
    conn.close()
    return rt_root


def test_dryrun_emits_planned_actions(tmp_path: Path) -> None:
    rt_root = _build_legacy_runtime(tmp_path)
    result = migrate_to_org_runtime(
        rt_root, slug="hk-tourism",
        i_have_a_backup=True, apply=False,
    )
    assert isinstance(result, MigrationResult)
    assert result.applied is False
    assert any("write happyranch.yaml" in step for step in result.planned)
    assert any("move teams.yaml" in step for step in result.planned)
    assert any("custom_dev" in step for step in result.planned)
    assert any("draft_writer" in step for step in result.planned)
    # Filesystem unchanged.
    assert not (rt_root / "org").exists()
    assert (rt_root / "teams.yaml").exists()


def test_apply_writes_org_tree(tmp_path: Path) -> None:
    rt_root = _build_legacy_runtime(tmp_path)
    result = migrate_to_org_runtime(
        rt_root, slug="hk-tourism",
        i_have_a_backup=True, apply=True,
    )
    assert result.applied is True
    # The v0→v1 migration writes a v1-marker (schema_version=1, slug=<slug>);
    # RuntimeDir.load now refuses v1, so verify the layout directly via OrgPaths
    # rooted at rt_root and read the marker for slug.
    marker = yaml.safe_load((rt_root / "happyranch.yaml").read_text())
    assert marker["slug"] == "hk-tourism"
    paths = OrgPaths(root=rt_root)
    # teams.yaml moved.
    assert paths.teams_config_path.exists()
    assert not (rt_root / "teams.yaml").exists()
    # Approved enrollment exported to active agents/.
    assert (paths.agents_dir / "custom_dev.md").exists()
    # Pending enrollment exported to _pending/.
    assert (paths.pending_agents_dir / "draft_writer.md").exists()
    # agent_enrollments table dropped.
    conn = sqlite3.connect(paths.db_path)
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='agent_enrollments'")
    assert cur.fetchone() is None
    conn.close()


def test_apply_idempotent(tmp_path: Path) -> None:
    rt_root = _build_legacy_runtime(tmp_path)
    migrate_to_org_runtime(rt_root, slug="x", i_have_a_backup=True, apply=True)
    second = migrate_to_org_runtime(rt_root, slug="x", i_have_a_backup=True, apply=True)
    assert second.already_migrated is True


def test_aborts_without_backup_flag(tmp_path: Path) -> None:
    rt_root = _build_legacy_runtime(tmp_path)
    with pytest.raises(ValueError, match="i_have_a_backup"):
        migrate_to_org_runtime(rt_root, slug="x", i_have_a_backup=False, apply=True)


def test_aborts_when_slug_disagrees_with_existing(tmp_path: Path) -> None:
    rt_root = _build_legacy_runtime(tmp_path)
    (rt_root / "happyranch.yaml").write_text("slug: existing\n")
    with pytest.raises(ValueError, match="slug.*disagrees"):
        migrate_to_org_runtime(rt_root, slug="other", i_have_a_backup=True, apply=True)


def test_apply_preserves_description_field(tmp_path: Path) -> None:
    """The legacy agent_enrollments.description column must survive into the
    AgentDef.description field — Codex review caught it being dropped."""
    rt_root = _build_legacy_runtime(tmp_path)
    migrate_to_org_runtime(rt_root, slug="hk", i_have_a_backup=True, apply=True)
    paths = OrgPaths(root=rt_root)
    custom_dev_text = (paths.agents_dir / "custom_dev.md").read_text()
    assert "description: A custom dev" in custom_dev_text


def test_strips_completion_contract_block(tmp_path: Path) -> None:
    rt_root = _build_legacy_runtime(tmp_path, with_enrollments=False)
    # Insert one enrollment whose system_prompt has the canonical contract block.
    conn = sqlite3.connect(rt_root / "happyranch.db")
    body = (
        "You are role_x.\n\nResponsibilities: do X.\n\n"
        "## Task completion report\n\n"
        "Format: confidence, risks, ...\n"
        "(this whole section should be stripped on migration.)\n"
    )
    conn.execute(
        "INSERT INTO agent_enrollments VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("role_x", "x", body, "{}", "claude", "[]", "approved", "2026-04-01T00:00:00Z"),
    )
    conn.commit()
    conn.close()
    migrate_to_org_runtime(rt_root, slug="x", i_have_a_backup=True, apply=True)
    paths = OrgPaths(root=rt_root)
    text = (paths.agents_dir / "role_x.md").read_text()
    assert "Task completion report" not in text
    assert "do X" in text
