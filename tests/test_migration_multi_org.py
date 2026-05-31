from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
import yaml

from src.daemon.migration_multi_org import migrate_to_multi_org


def _make_v1_runtime(root: Path, slug: str = "hk-tourism") -> None:
    root.mkdir(parents=True)
    (root / "happyranch.yaml").write_text(yaml.safe_dump({
        "slug": slug,
        "schema_version": 1,
        "created_at": "2026-04-01T00:00:00Z",
    }, sort_keys=False))
    (root / "org").mkdir()
    (root / "org" / "teams.yaml").write_text("teams: {}\n")
    (root / "org" / "agents").mkdir()
    (root / "workspaces").mkdir()
    (root / "kb").mkdir()
    (root / "talks").mkdir()
    # Minimal DB with no in-flight tasks.
    conn = sqlite3.connect(root / "happyranch.db")
    conn.executescript("""
        CREATE TABLE tasks (id TEXT PRIMARY KEY, status TEXT);
        CREATE TABLE talks (id TEXT PRIMARY KEY, status TEXT);
    """)
    conn.commit()
    conn.close()


def test_migrate_dry_run_does_not_mutate(tmp_path: Path) -> None:
    rt = tmp_path / "rt"
    _make_v1_runtime(rt)
    report = migrate_to_multi_org(rt, apply=False, i_have_a_backup=True)
    assert (rt / "happyranch.yaml").exists()
    data = yaml.safe_load((rt / "happyranch.yaml").read_text())
    assert data["schema_version"] == 1  # unchanged
    assert "would_move" in report


def test_migrate_apply_moves_subfolders(tmp_path: Path) -> None:
    rt = tmp_path / "rt"
    _make_v1_runtime(rt, slug="hk")
    migrate_to_multi_org(rt, apply=True, i_have_a_backup=True)
    assert (rt / "orgs" / "hk" / "org" / "teams.yaml").is_file()
    assert (rt / "orgs" / "hk" / "workspaces").is_dir()
    assert (rt / "orgs" / "hk" / "happyranch.db").is_file()
    assert not (rt / "org").exists()
    assert not (rt / "happyranch.db").exists()
    data = yaml.safe_load((rt / "happyranch.yaml").read_text())
    assert data["schema_version"] == 2
    assert data["type"] == "multi-org-runtime"
    assert "slug" not in data


def test_migrate_idempotent(tmp_path: Path) -> None:
    rt = tmp_path / "rt"
    _make_v1_runtime(rt, slug="hk")
    migrate_to_multi_org(rt, apply=True, i_have_a_backup=True)
    # Second run is a no-op
    report = migrate_to_multi_org(rt, apply=True, i_have_a_backup=True)
    assert report["already_migrated"] is True


def test_migrate_refuses_without_backup_ack(tmp_path: Path) -> None:
    rt = tmp_path / "rt"
    _make_v1_runtime(rt)
    with pytest.raises(RuntimeError, match="i-have-a-backup"):
        migrate_to_multi_org(rt, apply=True, i_have_a_backup=False)


def test_migrate_refuses_with_active_tasks(tmp_path: Path) -> None:
    rt = tmp_path / "rt"
    _make_v1_runtime(rt)
    conn = sqlite3.connect(rt / "happyranch.db")
    conn.execute("INSERT INTO tasks(id, status) VALUES('TASK-001', 'in_progress')")
    conn.commit()
    conn.close()
    with pytest.raises(RuntimeError, match="cannot_migrate_with_active_tasks"):
        migrate_to_multi_org(rt, apply=True, i_have_a_backup=True)


def test_migrate_refuses_with_blocked_tasks(tmp_path: Path) -> None:
    """blocked is non-terminal — a blocked-escalated task is still waiting on
    a founder decision, and a blocked-delegated parent is still waiting on
    its child. Migrating either silently makes that work invisible."""
    rt = tmp_path / "rt"
    _make_v1_runtime(rt)
    conn = sqlite3.connect(rt / "happyranch.db")
    conn.execute("INSERT INTO tasks(id, status) VALUES('TASK-007', 'blocked')")
    conn.commit()
    conn.close()
    with pytest.raises(RuntimeError, match="cannot_migrate_with_active_tasks"):
        migrate_to_multi_org(rt, apply=True, i_have_a_backup=True)
