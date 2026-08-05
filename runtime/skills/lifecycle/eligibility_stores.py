"""THR-055 Eligibility stores — custom skill records, eligibility rules,
and policy audit trail.

Additive tables only — no alteration/drop/reinterpretation of existing
lifecycle, assignment, materialization, audit, or task_id columns.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional

from .models import (
    CustomSkillRecord,
    CustomSkillVisibility,
    CustomSkillVersionRecord,
    EligibilityAction,
    EligibilityRule,
    EligibilityTargetScope,
    LifecycleStatus,
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ═══════════════════════════════════════════════════════════════════════════
# Schema / migration
# ═══════════════════════════════════════════════════════════════════════════

CREATE_CUSTOM_SKILLS = """
CREATE TABLE IF NOT EXISTS custom_skills (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    skill_id            TEXT NOT NULL UNIQUE,
    slug                TEXT NOT NULL,
    name                TEXT NOT NULL,
    description         TEXT NOT NULL DEFAULT '',
    policy_class        TEXT NOT NULL DEFAULT 'standard_operational',
    visibility          TEXT NOT NULL DEFAULT 'hidden',
    retired             INTEGER NOT NULL DEFAULT 0,
    retired_at          TEXT,
    retired_by          TEXT,
    created_at          TEXT NOT NULL,
    created_by          TEXT NOT NULL DEFAULT '',
    -- Agent provenance
    proposer_agent      TEXT,
    proposal_task_id    TEXT,
    proposal_session_id TEXT,
    -- Current version pointer
    current_version_id  INTEGER,
    current_version     TEXT,
    current_content_hash TEXT,
    -- Last validation
    last_validated_at       TEXT,
    last_validator_version  TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_custom_skills_slug
    ON custom_skills(slug);
CREATE INDEX IF NOT EXISTS idx_custom_skills_visibility
    ON custom_skills(visibility);
CREATE INDEX IF NOT EXISTS idx_custom_skills_retired
    ON custom_skills(retired);
"""

CREATE_CUSTOM_SKILL_VERSIONS = """
CREATE TABLE IF NOT EXISTS custom_skill_versions (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    skill_id            TEXT NOT NULL,
    slug                TEXT NOT NULL,
    name                TEXT NOT NULL,
    version             TEXT NOT NULL,
    content_hash        TEXT NOT NULL,
    policy_class        TEXT NOT NULL DEFAULT 'standard_operational',
    description         TEXT NOT NULL DEFAULT '',
    content_artifact_key TEXT,
    status              TEXT NOT NULL DEFAULT 'published',
    created_at          TEXT NOT NULL,
    created_by          TEXT NOT NULL DEFAULT '',
    -- Agent provenance
    proposer_agent      TEXT,
    proposal_task_id    TEXT,
    proposal_session_id TEXT,
    -- Validation
    validated_at        TEXT,
    validator_version   TEXT,
    validation_passed   INTEGER,
    validation_findings TEXT  -- JSON array of finding strings
);

CREATE INDEX IF NOT EXISTS idx_custom_skill_versions_skill_id
    ON custom_skill_versions(skill_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_custom_skill_versions_hash
    ON custom_skill_versions(skill_id, content_hash);
CREATE INDEX IF NOT EXISTS idx_custom_skill_versions_created
    ON custom_skill_versions(created_at);
"""

CREATE_ELIGIBILITY_RULES = """
CREATE TABLE IF NOT EXISTS skill_eligibility_rules (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    skill_id    TEXT NOT NULL,
    target_scope TEXT NOT NULL,
    target_name TEXT NOT NULL,
    action      TEXT NOT NULL,
    priority    INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL,
    created_by  TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_eligibility_rules_skill
    ON skill_eligibility_rules(skill_id);
CREATE INDEX IF NOT EXISTS idx_eligibility_rules_target
    ON skill_eligibility_rules(target_scope, target_name);
"""

CREATE_ELIGIBILITY_AUDIT = """
CREATE TABLE IF NOT EXISTS skill_eligibility_audit (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    skill_id    TEXT NOT NULL,
    action      TEXT NOT NULL,
    actor       TEXT NOT NULL DEFAULT '',
    rules_before TEXT,  -- JSON array of rule dicts
    rules_after  TEXT,  -- JSON array of rule dicts
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_eligibility_audit_skill
    ON skill_eligibility_audit(skill_id);
CREATE INDEX IF NOT EXISTS idx_eligibility_audit_created
    ON skill_eligibility_audit(created_at);
"""

CREATE_CUSTOM_SKILL_MATERIALIZATIONS = """
CREATE TABLE IF NOT EXISTS custom_skill_materializations (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    skill_id            TEXT NOT NULL,
    agent_name          TEXT NOT NULL,
    version_id          INTEGER NOT NULL,
    version             TEXT NOT NULL,
    content_hash        TEXT NOT NULL,
    success             INTEGER NOT NULL DEFAULT 0,
    error_message       TEXT,
    session_context     TEXT,
    session_id          TEXT,
    created_at          TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_custom_skill_mat_skill_agent
    ON custom_skill_materializations(skill_id, agent_name);
CREATE INDEX IF NOT EXISTS idx_custom_skill_mat_session
    ON custom_skill_materializations(session_id);
"""


def migrate_eligibility(db) -> None:
    """Run the eligibility schema migration (idempotent, additive only)."""
    # Use executescript for multi-statement DDL blocks (SQLite limitation)
    if hasattr(db, '_conn'):
        conn = db._conn
    else:
        conn = db
    conn.executescript(CREATE_CUSTOM_SKILLS)
    conn.executescript(CREATE_CUSTOM_SKILL_VERSIONS)
    conn.executescript(CREATE_ELIGIBILITY_RULES)
    conn.executescript(CREATE_ELIGIBILITY_AUDIT)
    conn.executescript(CREATE_CUSTOM_SKILL_MATERIALIZATIONS)


# ═══════════════════════════════════════════════════════════════════════════
# Custom skill CRUD
# ═══════════════════════════════════════════════════════════════════════════

def insert_custom_skill(db, skill: CustomSkillRecord) -> int:
    """Insert a new custom skill record. Returns the new row id."""
    now = _now_iso()
    skill.created_at = skill.created_at or datetime.fromisoformat(now)
    row = db.execute(
        """INSERT INTO custom_skills
           (skill_id, slug, name, description, policy_class,
            visibility, retired, retired_at, retired_by,
            created_at, created_by,
            proposer_agent, proposal_task_id, proposal_session_id,
            current_version_id, current_version, current_content_hash,
            last_validated_at, last_validator_version)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            skill.skill_id, skill.slug, skill.name, skill.description,
            skill.policy_class,
            skill.visibility.value, 1 if skill.retired else 0,
            skill.retired_at.isoformat() if skill.retired_at else None,
            skill.retired_by,
            skill.created_at.isoformat(), skill.created_by,
            skill.proposer_agent, skill.proposal_task_id, skill.proposal_session_id,
            skill.current_version_id, skill.current_version, skill.current_content_hash,
            skill.last_validated_at.isoformat() if skill.last_validated_at else None,
            skill.last_validator_version,
        ),
    )
    return row.lastrowid


def get_custom_skill(db, skill_id: str) -> CustomSkillRecord | None:
    """Fetch a custom skill by skill_id."""
    row = db.execute(
        "SELECT * FROM custom_skills WHERE skill_id = ?", (skill_id,)
    ).fetchone()
    if row is None:
        return None
    return _row_to_custom_skill(dict(row))


def get_custom_skill_by_slug(db, slug: str) -> CustomSkillRecord | None:
    """Fetch a custom skill by slug."""
    row = db.execute(
        "SELECT * FROM custom_skills WHERE slug = ?", (slug,)
    ).fetchone()
    if row is None:
        return None
    return _row_to_custom_skill(dict(row))


def update_custom_skill(db, skill_id: str, **kwargs) -> None:
    """Update mutable fields on a custom skill record."""
    allowed = {
        "name", "description", "visibility", "retired", "retired_at",
        "retired_by", "current_version_id", "current_version",
        "current_content_hash", "last_validated_at", "last_validator_version",
    }
    sets = []
    params = []
    for key, value in kwargs.items():
        if key in allowed:
            sets.append(f"{key} = ?")
            if isinstance(value, datetime):
                params.append(value.isoformat())
            elif hasattr(value, 'value'):
                params.append(value.value)
            elif isinstance(value, bool):
                params.append(1 if value else 0)
            else:
                params.append(value)
    if not sets:
        return
    params.append(skill_id)
    db.execute(
        f"UPDATE custom_skills SET {', '.join(sets)} WHERE skill_id = ?",
        tuple(params),
    )


def list_custom_skills(
    db,
    visibility: CustomSkillVisibility | None = None,
    retired: bool | None = None,
    offset: int = 0,
    limit: int = 50,
) -> tuple[list[CustomSkillRecord], int]:
    """List custom skills with optional filters. Returns (items, total)."""
    where = ["1=1"]
    params = []
    if visibility is not None:
        where.append("visibility = ?")
        params.append(visibility.value)
    if retired is not None:
        where.append("retired = ?")
        params.append(1 if retired else 0)
    where_sql = " AND ".join(where)

    count_row = db.execute(
        f"SELECT COUNT(*) FROM custom_skills WHERE {where_sql}", tuple(params)
    ).fetchone()
    total = count_row[0] if count_row else 0

    rows = db.execute(
        f"SELECT * FROM custom_skills WHERE {where_sql} ORDER BY created_at DESC LIMIT ? OFFSET ?",
        tuple(params + [limit, offset]),
    ).fetchall()
    return [_row_to_custom_skill(dict(r)) for r in rows], total


def _row_to_custom_skill(row: dict) -> CustomSkillRecord:
    return CustomSkillRecord(
        id=row["id"],
        skill_id=row["skill_id"],
        slug=row["slug"],
        name=row["name"],
        description=row.get("description", ""),
        policy_class=row.get("policy_class", "standard_operational"),
        visibility=CustomSkillVisibility(row.get("visibility", "hidden")),
        retired=bool(row.get("retired", False)),
        retired_at=_parse_dt(row.get("retired_at")),
        retired_by=row.get("retired_by"),
        created_at=_parse_dt(row.get("created_at")) or datetime.now(timezone.utc),
        created_by=row.get("created_by", ""),
        proposer_agent=row.get("proposer_agent"),
        proposal_task_id=row.get("proposal_task_id"),
        proposal_session_id=row.get("proposal_session_id"),
        current_version_id=row.get("current_version_id"),
        current_version=row.get("current_version"),
        current_content_hash=row.get("current_content_hash"),
        last_validated_at=_parse_dt(row.get("last_validated_at")),
        last_validator_version=row.get("last_validator_version"),
    )


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None


# ═══════════════════════════════════════════════════════════════════════════
# Custom skill version CRUD
# ═══════════════════════════════════════════════════════════════════════════

def insert_custom_skill_version(db, version: CustomSkillVersionRecord) -> int:
    """Insert a new immutable version record. Returns the new row id."""
    now = _now_iso()
    version.created_at = version.created_at or datetime.fromisoformat(now)
    row = db.execute(
        """INSERT INTO custom_skill_versions
           (skill_id, slug, name, version, content_hash, policy_class,
            description, content_artifact_key, status, created_at, created_by,
            proposer_agent, proposal_task_id, proposal_session_id,
            validated_at, validator_version, validation_passed, validation_findings)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            version.skill_id, version.slug, version.name,
            version.version, version.content_hash,
            version.policy_class, version.description,
            version.content_artifact_key, version.status.value,
            version.created_at.isoformat(), version.created_by,
            version.proposer_agent, version.proposal_task_id,
            version.proposal_session_id,
            version.validated_at.isoformat() if version.validated_at else None,
            version.validator_version,
            1 if version.validation_passed else 0 if version.validation_passed is not None else None,
            json.dumps(version.validation_findings) if version.validation_findings else None,
        ),
    )
    return row.lastrowid


def get_custom_skill_version(db, version_id: int) -> CustomSkillVersionRecord | None:
    """Fetch a version by primary key."""
    row = db.execute(
        "SELECT * FROM custom_skill_versions WHERE id = ?", (version_id,)
    ).fetchone()
    if row is None:
        return None
    return _row_to_version(dict(row))


def get_custom_skill_version_by_hash(
    db, skill_id: str, content_hash: str
) -> CustomSkillVersionRecord | None:
    """Fetch a version by skill_id + content_hash."""
    row = db.execute(
        "SELECT * FROM custom_skill_versions WHERE skill_id = ? AND content_hash = ?",
        (skill_id, content_hash),
    ).fetchone()
    if row is None:
        return None
    return _row_to_version(dict(row))


def list_custom_skill_versions(db, skill_id: str) -> list[CustomSkillVersionRecord]:
    """List all versions for a skill, newest first."""
    rows = db.execute(
        "SELECT * FROM custom_skill_versions WHERE skill_id = ? ORDER BY id DESC",
        (skill_id,),
    ).fetchall()
    return [_row_to_version(dict(r)) for r in rows]


def _row_to_version(row: dict) -> CustomSkillVersionRecord:
    findings_raw = row.get("validation_findings")
    findings = json.loads(findings_raw) if findings_raw else None
    return CustomSkillVersionRecord(
        id=row["id"],
        skill_id=row["skill_id"],
        slug=row["slug"],
        name=row["name"],
        version=row["version"],
        content_hash=row["content_hash"],
        policy_class=row.get("policy_class", "standard_operational"),
        description=row.get("description", ""),
        content_artifact_key=row.get("content_artifact_key"),
        status=LifecycleStatus(row["status"]),
        created_at=_parse_dt(row.get("created_at")) or datetime.now(timezone.utc),
        created_by=row.get("created_by", ""),
        proposer_agent=row.get("proposer_agent"),
        proposal_task_id=row.get("proposal_task_id"),
        proposal_session_id=row.get("proposal_session_id"),
        validated_at=_parse_dt(row.get("validated_at")),
        validator_version=row.get("validator_version"),
        validation_passed=bool(row.get("validation_passed")) if row.get("validation_passed") is not None else None,
        validation_findings=findings,
    )


# ═══════════════════════════════════════════════════════════════════════════
# Eligibility rules CRUD
# ═══════════════════════════════════════════════════════════════════════════

def insert_eligibility_rule(db, rule: EligibilityRule) -> int:
    """Insert a single eligibility rule."""
    row = db.execute(
        """INSERT INTO skill_eligibility_rules
           (skill_id, target_scope, target_name, action, priority, created_at, created_by)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (rule.skill_id, rule.target_scope.value, rule.target_name,
         rule.action.value, rule.priority, _now_iso(), rule.created_by),
    )
    return row.lastrowid


def delete_eligibility_rules_for_skill(db, skill_id: str) -> int:
    """Delete ALL eligibility rules for a skill. Returns count deleted."""
    row = db.execute(
        "DELETE FROM skill_eligibility_rules WHERE skill_id = ?", (skill_id,)
    )
    return row.rowcount


def get_eligibility_rules_for_skill(db, skill_id: str) -> list[EligibilityRule]:
    """Get all eligibility rules for a skill, ordered by priority desc."""
    rows = db.execute(
        "SELECT * FROM skill_eligibility_rules WHERE skill_id = ? ORDER BY priority DESC, id ASC",
        (skill_id,),
    ).fetchall()
    return [_row_to_rule(dict(r)) for r in rows]


def _row_to_rule(row: dict) -> EligibilityRule:
    return EligibilityRule(
        id=row["id"],
        skill_id=row["skill_id"],
        target_scope=EligibilityTargetScope(row["target_scope"]),
        target_name=row["target_name"],
        action=EligibilityAction(row["action"]),
        priority=row.get("priority", 0),
        created_at=_parse_dt(row.get("created_at")) or datetime.now(timezone.utc),
        created_by=row.get("created_by", ""),
    )


# ═══════════════════════════════════════════════════════════════════════════
# Eligibility audit CRUD
# ═══════════════════════════════════════════════════════════════════════════

def insert_eligibility_audit(
    db, skill_id: str, action: str, actor: str,
    rules_before: list[dict] | None = None,
    rules_after: list[dict] | None = None,
) -> int:
    """Insert an eligibility audit row."""
    row = db.execute(
        """INSERT INTO skill_eligibility_audit
           (skill_id, action, actor, rules_before, rules_after, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (skill_id, action, actor,
         json.dumps(rules_before) if rules_before else None,
         json.dumps(rules_after) if rules_after else None,
         _now_iso()),
    )
    return row.lastrowid


def list_eligibility_audit(
    db, skill_id: str | None = None, limit: int = 100,
) -> list[dict]:
    """List eligibility audit entries, newest first."""
    if skill_id:
        rows = db.execute(
            "SELECT * FROM skill_eligibility_audit WHERE skill_id = ? ORDER BY id DESC LIMIT ?",
            (skill_id, limit),
        ).fetchall()
    else:
        rows = db.execute(
            "SELECT * FROM skill_eligibility_audit ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [_row_to_audit(dict(r)) for r in rows]


def _row_to_audit(row: dict) -> dict:
    return {
        "id": row["id"],
        "skill_id": row["skill_id"],
        "action": row["action"],
        "actor": row.get("actor", ""),
        "rules_before": json.loads(row["rules_before"]) if row.get("rules_before") else None,
        "rules_after": json.loads(row["rules_after"]) if row.get("rules_after") else None,
        "created_at": row.get("created_at", ""),
    }


# ═══════════════════════════════════════════════════════════════════════════
# Custom skill materialization CRUD
# ═══════════════════════════════════════════════════════════════════════════

def insert_custom_materialization(
    db, skill_id: str, agent_name: str, version_id: int,
    version: str, content_hash: str, success: bool,
    error_message: str | None = None,
    session_context: str | None = None,
    session_id: str | None = None,
) -> int:
    """Insert a custom skill materialization record."""
    row = db.execute(
        """INSERT INTO custom_skill_materializations
           (skill_id, agent_name, version_id, version, content_hash,
            success, error_message, session_context, session_id, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (skill_id, agent_name, version_id, version, content_hash,
         1 if success else 0, error_message, session_context, session_id,
         _now_iso()),
    )
    return row.lastrowid


def get_latest_custom_materialization(
    db, skill_id: str, agent_name: str,
) -> dict | None:
    """Get the latest materialization for a skill + agent."""
    row = db.execute(
        """SELECT * FROM custom_skill_materializations
           WHERE skill_id = ? AND agent_name = ?
           ORDER BY id DESC LIMIT 1""",
        (skill_id, agent_name),
    ).fetchone()
    if row is None:
        return None
    d = dict(row)
    return {
        "id": d["id"],
        "skill_id": d["skill_id"],
        "agent_name": d["agent_name"],
        "version_id": d["version_id"],
        "version": d["version"],
        "content_hash": d["content_hash"],
        "success": bool(d["success"]),
        "error_message": d.get("error_message"),
        "session_context": d.get("session_context"),
        "session_id": d.get("session_id"),
        "created_at": d.get("created_at", ""),
    }
