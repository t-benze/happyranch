"""One-shot: <runtime>/teams.yaml + agent_enrollments → <runtime>/org/.

Run via: grassland migrate-to-org-runtime <runtime-path> --slug <slug> --i-have-a-backup [--apply]
"""
from __future__ import annotations

import json
import re
import shutil
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import yaml

from src.orchestrator.agent_def import AgentDef, render_agent_text


_CONTRACT_HEADING_RE = re.compile(
    r"\n## Task completion report\b.*\Z",
    re.DOTALL,
)


@dataclass
class MigrationResult:
    runtime_path: Path
    slug: str
    applied: bool
    already_migrated: bool = False
    planned: list[str] = field(default_factory=list)
    exported_approved: list[str] = field(default_factory=list)
    exported_pending: list[str] = field(default_factory=list)


def _strip_contract_block(prompt: str) -> str:
    """Remove the canonical `## Task completion report` block (and everything
    after it within the prompt). Returns the prompt unchanged if not present.
    """
    return _CONTRACT_HEADING_RE.sub("\n", prompt).rstrip() + "\n"


def _load_existing_marker(path: Path) -> dict:
    if not path.exists():
        return {}
    raw = yaml.safe_load(path.read_text()) or {}
    return raw if isinstance(raw, dict) else {}


def _enrollment_to_agent(row: sqlite3.Row | tuple, *, team: str, role: str) -> AgentDef:
    """Convert an agent_enrollments row into an AgentDef."""
    cols = {
        "name": row[0], "description": row[1], "system_prompt": row[2],
        "repos": row[3], "executor": row[4], "allow_rules": row[5],
        "status": row[6], "created_at": row[7],
    }
    repos = json.loads(cols["repos"]) if cols["repos"] else {}
    allow_rules = json.loads(cols["allow_rules"]) if cols["allow_rules"] else []
    enrolled_at: datetime | None = None
    if cols["created_at"]:
        try:
            enrolled_at = datetime.fromisoformat(cols["created_at"].replace("Z", "+00:00"))
        except ValueError:
            enrolled_at = None
    body = _strip_contract_block(cols["system_prompt"] or "")
    if not body.strip():
        body = "Migrated agent — system prompt was empty.\n"
    return AgentDef(
        name=cols["name"],
        team=team,
        role=role,  # type: ignore[arg-type]
        executor=cols["executor"] or "claude",
        allow_rules=tuple(allow_rules),
        repos=dict(repos),
        enrolled_by=None,  # not recorded in the legacy table
        enrolled_at_task=None,
        enrolled_at=enrolled_at,
        system_prompt=body,
        description=cols["description"],
    )


def migrate_to_org_runtime(
    runtime_path: Path,
    *,
    slug: str,
    i_have_a_backup: bool,
    apply: bool = False,
) -> MigrationResult:
    """Migrate a pre-org-cut runtime in place.

    Steps:
      1. Validate flags.
      2. Detect already-migrated → return early.
      3. Validate slug consistency with existing grassland.yaml (if any).
      4. Plan / apply: write grassland.yaml, create org/ skeleton, move teams.yaml,
         export enrollments, drop agent_enrollments table.
    """
    if not i_have_a_backup:
        raise ValueError(
            "migrate_to_org_runtime requires i_have_a_backup=True (CLI flag "
            "--i-have-a-backup) to acknowledge that you've backed up the runtime folder."
        )
    runtime_path = runtime_path.resolve()
    marker = runtime_path / "grassland.yaml"
    if not marker.exists():
        raise ValueError(f"{marker} missing — not a valid pre-cut runtime")
    existing = _load_existing_marker(marker)
    existing_slug = existing.get("slug")
    if existing_slug and existing_slug != slug:
        raise ValueError(
            f"grassland.yaml slug ({existing_slug!r}) disagrees with --slug ({slug!r})"
        )
    org_dir = runtime_path / "org"
    teams_old = runtime_path / "teams.yaml"
    teams_new = org_dir / "teams.yaml"

    db_path = runtime_path / "grassland.db"
    table_present = False
    rows: list[tuple] = []
    if db_path.exists():
        conn = sqlite3.connect(db_path)
        try:
            cur = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='agent_enrollments'"
            )
            table_present = cur.fetchone() is not None
            if table_present:
                cur2 = conn.execute(
                    "SELECT name, description, system_prompt, repos, executor, "
                    "allow_rules, status, created_at FROM agent_enrollments"
                )
                rows = [tuple(r) for r in cur2.fetchall()]
        finally:
            conn.close()

    # Detect already-migrated: org/teams.yaml present AND no enrollments table.
    if teams_new.exists() and not table_present:
        return MigrationResult(
            runtime_path=runtime_path, slug=slug,
            applied=False, already_migrated=True,
        )

    planned: list[str] = []
    if not existing_slug:
        planned.append(f"write grassland.yaml with slug={slug}, schema_version=1")
    planned.append(f"create org/ skeleton at {org_dir}")
    if teams_old.exists():
        planned.append(f"move teams.yaml → {teams_new}")
    elif not teams_new.exists():
        planned.append(f"seed empty {teams_new}")
    for r in rows:
        if r[6] == "approved":
            planned.append(f"export approved enrollment: {r[0]}")
        elif r[6] == "pending":
            planned.append(f"export pending enrollment: {r[0]}")
        # rejected/terminated: skipped silently
    if table_present:
        planned.append("drop agent_enrollments table")

    if not apply:
        return MigrationResult(
            runtime_path=runtime_path, slug=slug,
            applied=False, planned=planned,
        )

    # APPLY ----------------------------------------------------------------
    # 1. Write/update grassland.yaml.
    if not existing_slug:
        payload = {
            "slug": slug,
            "created_at": existing.get("created_at")
                or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "schema_version": 1,
        }
        marker.write_text(yaml.safe_dump(payload, sort_keys=False))

    # 2. Create org/ skeleton.
    (org_dir / "agents" / "_pending").mkdir(parents=True, exist_ok=True)

    # 3. Move teams.yaml.
    if teams_old.exists() and not teams_new.exists():
        shutil.move(str(teams_old), str(teams_new))
    elif not teams_new.exists():
        teams_new.write_text("teams: {}\n")

    # 4. Load teams to determine team membership.
    teams_layout = (yaml.safe_load(teams_new.read_text()) or {}).get("teams") or {}

    def lookup_team_role(name: str) -> tuple[str, str]:
        for team_name, entry in teams_layout.items():
            if entry.get("manager") == name:
                return team_name, "manager"
            if name in (entry.get("workers") or []):
                return team_name, "worker"
        # Default for orphan rows: place under engineering as worker.
        return "engineering", "worker"

    exported_approved: list[str] = []
    exported_pending: list[str] = []
    for r in rows:
        status = r[6]
        if status not in ("approved", "pending"):
            continue
        team, role = lookup_team_role(r[0])
        agent = _enrollment_to_agent(r, team=team, role=role)
        target_dir = (
            org_dir / "agents" / "_pending" if status == "pending" else org_dir / "agents"
        )
        target_dir.mkdir(parents=True, exist_ok=True)
        (target_dir / f"{agent.name}.md").write_text(render_agent_text(agent))
        (exported_pending if status == "pending" else exported_approved).append(agent.name)

    # 5. Drop the table.
    if table_present:
        conn = sqlite3.connect(db_path)
        try:
            conn.execute("DROP TABLE agent_enrollments")
            conn.commit()
        finally:
            conn.close()

    return MigrationResult(
        runtime_path=runtime_path, slug=slug,
        applied=True, planned=planned,
        exported_approved=exported_approved,
        exported_pending=exported_pending,
    )
