"""End-to-end: build a v1-shape runtime, run migration, daemon serves it.

Mirrors ``test_end_to_end.py`` and ``test_two_orgs_concurrent.py`` —
direct ``httpx`` against the live daemon, no CLI subprocess. The plan
(docs/superpowers/plans/2026-04-28-parallel-multi-org-runtime.md
§Task 25) sketched the migration call as a subprocess invoking
``happyranch migrate-to-multi-org``, but the CLI hard-requires a TTY (no
``--yes`` bypass; see ``cmd_migrate_to_multi_org`` in src/cli.py). The
TTY gate is already covered by unit tests of the wrapper; what the
integration test contributes is the *post-migration daemon serves* leg
— so we call ``migrate_to_multi_org`` programmatically and exercise the
daemon path against the migrated container.
"""
from __future__ import annotations

import time
from pathlib import Path

import httpx
import pytest
import yaml

from src.daemon.migration_multi_org import migrate_to_multi_org
from src.infrastructure.database import Database


pytestmark = pytest.mark.integration


def _auth_headers() -> dict:
    from src.daemon import paths

    return {"Authorization": f"Bearer {paths.read_token()}"}


def _build_v1_runtime_fixture(rt: Path, *, slug: str) -> None:
    """Build a minimal v1-shape runtime: the legacy single-org tree the
    migration script consumes.

    Layout:
        <rt>/happyranch.yaml                    schema_version: 1, slug: <slug>
        <rt>/happyranch.db                      real schema via Database()
        <rt>/org/teams.yaml              engineering team
        <rt>/org/agents/                 (empty — EH dispatch tolerates
                                         missing agent files in tests)
        <rt>/workspaces/engineering_head/.claude/skills/start-task/SKILL.md
        <rt>/kb/                         empty
        <rt>/talks/                      empty
    """
    rt.mkdir(parents=True)
    (rt / "happyranch.yaml").write_text(yaml.safe_dump({
        "slug": slug,
        "schema_version": 1,
        "created_at": "2026-04-01T00:00:00Z",
    }, sort_keys=False))

    (rt / "org" / "agents").mkdir(parents=True)
    (rt / "org" / "teams.yaml").write_text(
        "teams:\n"
        "  engineering:\n"
        "    manager: engineering_head\n"
        "    workers: [dev_agent]\n"
    )

    workspace = rt / "workspaces" / "engineering_head"
    skill_dir = workspace / ".claude" / "skills" / "start-task"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# start-task (test stub)\n")

    (rt / "kb").mkdir()
    (rt / "talks").mkdir()

    # Materialize the SQLite schema so the post-migration daemon can
    # open the moved happyranch.db without surprises.
    db = Database(rt / "happyranch.db")
    db.close()


def _register_runtime(global_base: str, container: Path) -> None:
    r = httpx.post(
        f"{global_base}/runtime",
        json={"path": str(container)},
        headers=_auth_headers(),
        timeout=5.0,
    )
    assert r.status_code == 200, r.text


def _wait_for_completed(base: str, task_id: str, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    last: dict = {}
    while time.monotonic() < deadline:
        r = httpx.get(
            f"{base}/tasks/{task_id}",
            headers=_auth_headers(),
            timeout=5.0,
        )
        if r.status_code == 200:
            last = r.json()
            status = last["task"]["status"]
            if status in ("completed", "failed"):
                assert status == "completed", (task_id, last)
                return
        time.sleep(0.2)
    raise AssertionError(
        f"task {task_id} did not complete within {timeout}s (last={last})"
    )


def test_migrate_v1_runtime_then_daemon_serves_it(
    live_daemon_idle,
    tmp_path: Path,
    fake_plan_env: Path,
) -> None:
    """v1 runtime → migrate-to-multi-org → daemon registers the
    container → task submitted to the migrated org runs to completion."""
    port = live_daemon_idle
    slug = "hk-tourism"
    rt = tmp_path / "legacy"

    # 1. Build a v1-shape runtime on disk.
    _build_v1_runtime_fixture(rt, slug=slug)
    assert yaml.safe_load((rt / "happyranch.yaml").read_text())["schema_version"] == 1

    # 2. Migrate in place.
    report = migrate_to_multi_org(rt, apply=True, i_have_a_backup=True)
    assert report.get("already_migrated") is False
    assert report["slug"] == slug

    # 3. Verify the new layout.
    org_root = rt / "orgs" / slug
    assert (org_root / "org" / "teams.yaml").is_file()
    assert (org_root / "workspaces" / "engineering_head").is_dir()
    assert (org_root / "happyranch.db").is_file()
    assert not (rt / "org").exists()
    assert not (rt / "happyranch.db").exists()
    marker = yaml.safe_load((rt / "happyranch.yaml").read_text())
    assert marker["schema_version"] == 2
    assert marker["type"] == "multi-org-runtime"
    assert "slug" not in marker

    # 4. Plan: every fake-claude session reports completion.
    fake_plan_env.write_text(
        '#!/usr/bin/env bash\n'
        'set -e\n'
        'task_id=$1; session_id=$2; agent=$3; org_slug=$4\n'
        'happyranch report-completion --org "$org_slug" \\\n'
        '  --task-id "$task_id" --session-id "$session_id" \\\n'
        '  --agent engineering_head --status completed --confidence 90 \\\n'
        '  --summary \'{"action":"done","summary":"ok"}\'\n'
    )
    fake_plan_env.chmod(0o755)

    # 5. Register the migrated container with the idle daemon.
    global_base = f"http://127.0.0.1:{port}/api/v1"
    _register_runtime(global_base, rt)

    # 6. Submit a task to the migrated org and wait for completion.
    base = f"{global_base}/orgs/{slug}"
    r = httpx.post(
        f"{base}/tasks",
        json={"type": "general", "brief": "post-migration smoke"},
        headers=_auth_headers(),
        timeout=5.0,
    )
    assert r.status_code == 200, r.text
    task_id = r.json()["task_id"]
    assert task_id == "TASK-001", task_id  # fresh DB → fresh counter

    _wait_for_completed(base, task_id, timeout=30.0)

    # 7. Sanity: the migrated org shows up in /orgs.
    r = httpx.get(f"{global_base}/orgs", headers=_auth_headers(), timeout=5.0)
    assert r.status_code == 200, r.text
    slugs = {entry["slug"] for entry in r.json()["orgs"]}
    assert slug in slugs, slugs
