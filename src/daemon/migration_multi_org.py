"""One-shot migration from schema_version 1 (single-org runtime) to v2
(multi-org container with one org subfolder)."""
from __future__ import annotations

import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import yaml

_TERMINAL_TASK_STATUSES = {"completed", "failed"}
_OPEN_TALK_STATUS = "open"


def _read_marker(rt: Path) -> dict:
    marker = rt / "opc.yaml"
    if not marker.exists():
        raise RuntimeError(f"{marker} does not exist — not a runtime directory")
    return yaml.safe_load(marker.read_text()) or {}


def _list_active_tasks(db_path: Path) -> list[str]:
    if not db_path.exists():
        return []
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.execute("SELECT id, status FROM tasks")
        return [
            row[0] for row in cursor
            if row[1] not in _TERMINAL_TASK_STATUSES
            and row[1] != "blocked"  # blocked-delegated is in-flight
        ]
    finally:
        conn.close()


def _list_open_talks(db_path: Path) -> list[str]:
    if not db_path.exists():
        return []
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.execute(
            "SELECT id FROM talks WHERE status = ?", (_OPEN_TALK_STATUS,)
        )
        return [row[0] for row in cursor]
    finally:
        conn.close()


def migrate_to_multi_org(
    runtime_path: Path,
    *,
    apply: bool,
    i_have_a_backup: bool,
) -> dict:
    """Convert a v1 runtime into a v2 multi-org container in place.

    Returns a dict describing what was (or would be) done.
    """
    rt = runtime_path.resolve()
    if not i_have_a_backup:
        raise RuntimeError(
            "refusing to migrate without --i-have-a-backup. "
            "back up the runtime folder first."
        )

    marker = _read_marker(rt)
    version = marker.get("schema_version")
    if version == 2:
        return {"already_migrated": True, "runtime": str(rt)}

    if version != 1:
        raise RuntimeError(
            f"unsupported schema_version: {version!r}. "
            f"this script only migrates v1 → v2."
        )

    slug = marker.get("slug")
    if not slug:
        raise RuntimeError(f"v1 marker at {rt}/opc.yaml is missing 'slug'")

    db_path = rt / "opc.db"
    active = _list_active_tasks(db_path)
    if active:
        raise RuntimeError(
            f"cannot_migrate_with_active_tasks: {active}"
        )
    open_talks = _list_open_talks(db_path)
    if open_talks:
        raise RuntimeError(
            f"cannot_migrate_with_open_talks: {open_talks}"
        )

    new_org_root = rt / "orgs" / slug
    plan = {
        "runtime": str(rt),
        "slug": slug,
        "would_move": [
            (str(rt / "org"), str(new_org_root / "org")),
            (str(rt / "workspaces"), str(new_org_root / "workspaces")),
            (str(rt / "kb"), str(new_org_root / "kb")),
            (str(rt / "talks"), str(new_org_root / "talks")),
            (str(rt / "opc.db"), str(new_org_root / "opc.db")),
        ],
        "would_rewrite_marker": True,
    }

    if not apply:
        return plan

    # Apply phase
    new_org_root.mkdir(parents=True, exist_ok=False)
    for src_name in ("org", "workspaces", "kb", "talks"):
        src = rt / src_name
        if src.exists():
            shutil.move(str(src), str(new_org_root / src_name))
    if db_path.exists():
        shutil.move(str(db_path), str(new_org_root / "opc.db"))

    # Rewrite top-level marker (preserve created_at)
    new_marker = {
        "schema_version": 2,
        "type": "multi-org-runtime",
        "created_at": marker.get(
            "created_at",
            datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        ),
    }
    (rt / "opc.yaml").write_text(yaml.safe_dump(new_marker, sort_keys=False))

    plan["already_migrated"] = False
    return plan
