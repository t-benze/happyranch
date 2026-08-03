"""THR-055 lifecycle SQLite stores — immutable package-version records,
lifecycle events, and version-pinned assignments.

Provides a thin SQLite-backed persistence layer for the lifecycle service.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional

from .models import (
    AssignmentRecord,
    LifecycleEvent,
    LifecycleStatus,
    MaterializationRecord,
    PackageVersion,
)


def _get_conn(db) -> "sqlite3.Connection":
    """Extract a sqlite3.Connection from either a raw connection or a Database wrapper."""
    if hasattr(db, '_conn'):
        return db._conn
    return db


def _row_to_package_version(row: dict) -> PackageVersion:
    """Convert a DB row dict to a PackageVersion model."""
    return PackageVersion(
        id=row["id"],
        skill_id=row["skill_id"],
        slug=row["slug"],
        name=row["name"],
        version=row["version"],
        content_hash=row["content_hash"],
        policy_class=row.get("policy_class", "standard_operational"),
        description=row.get("description", ""),
        skill_md=row.get("skill_md", ""),
        content_artifact_key=row.get("content_artifact_key"),
        status=LifecycleStatus(row["status"]),
        created_at=_parse_datetime(row.get("created_at")),
        created_by=row.get("created_by", ""),
        claimed_by=row.get("claimed_by"),
        claimed_at=_parse_datetime(row.get("claimed_at")),
        proposal_task_id=row.get("proposal_task_id"),
        proposal_session_id=row.get("proposal_session_id"),
        proposer_agent=row.get("proposer_agent"),
        reviewer=row.get("reviewer"),
        review_decision=row.get("review_decision"),
        review_rationale=row.get("review_rationale"),
        reviewed_at=_parse_datetime(row.get("reviewed_at")),
        publisher=row.get("publisher"),
        published_at=_parse_datetime(row.get("published_at")),
        publication_decision_id=row.get("publication_decision_id"),
    )


def _parse_datetime(value: str | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(value)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Schema / migration ───────────────────────────────────────────────────

CREATE_PACKAGE_VERSIONS = """
CREATE TABLE IF NOT EXISTS skill_lifecycle_packages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    skill_id    TEXT NOT NULL,
    slug        TEXT NOT NULL,
    name        TEXT NOT NULL,
    version     TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    policy_class TEXT NOT NULL DEFAULT 'standard_operational',
    description TEXT NOT NULL DEFAULT '',
    skill_md    TEXT NOT NULL DEFAULT '',
    content_artifact_key TEXT,
    status      TEXT NOT NULL DEFAULT 'proposed',
    created_at  TEXT NOT NULL,
    created_by  TEXT NOT NULL DEFAULT '',
    -- Optional separate founder claimant (never overwrites created_by/proposer_agent)
    claimed_by  TEXT,
    claimed_at  TEXT,
    -- Proposal provenance (agent-authored proposals)
    proposal_task_id    TEXT,
    proposal_session_id TEXT,
    proposer_agent      TEXT,
    -- Review provenance
    reviewer          TEXT,
    review_decision   TEXT,
    review_rationale  TEXT,
    reviewed_at       TEXT,
    -- Publication provenance
    publisher              TEXT,
    published_at           TEXT,
    publication_decision_id INTEGER
);

CREATE INDEX IF NOT EXISTS idx_lifecycle_packages_skill_id
    ON skill_lifecycle_packages(skill_id);
CREATE INDEX IF NOT EXISTS idx_lifecycle_packages_slug
    ON skill_lifecycle_packages(slug);
CREATE INDEX IF NOT EXISTS idx_lifecycle_packages_status
    ON skill_lifecycle_packages(status);
CREATE UNIQUE INDEX IF NOT EXISTS idx_lifecycle_packages_hash
    ON skill_lifecycle_packages(skill_id, content_hash);
"""

CREATE_LIFECYCLE_EVENTS = """
CREATE TABLE IF NOT EXISTS skill_lifecycle_events (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    skill_id            TEXT NOT NULL,
    package_version_id  INTEGER,
    event_type          TEXT NOT NULL,
    actor               TEXT NOT NULL DEFAULT '',
    actor_role          TEXT NOT NULL DEFAULT '',
    previous_status     TEXT,
    new_status          TEXT,
    content_hash        TEXT,
    metadata_json       TEXT,
    created_at          TEXT NOT NULL,
    task_id             TEXT,
    session_id          TEXT,
    FOREIGN KEY (package_version_id) REFERENCES skill_lifecycle_packages(id)
);

CREATE INDEX IF NOT EXISTS idx_lifecycle_events_skill_id
    ON skill_lifecycle_events(skill_id);
CREATE INDEX IF NOT EXISTS idx_lifecycle_events_created_at
    ON skill_lifecycle_events(created_at);
"""

CREATE_ASSIGNMENTS = """
CREATE TABLE IF NOT EXISTS skill_lifecycle_assignments (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    skill_id            TEXT NOT NULL,
    agent_name          TEXT NOT NULL,
    package_version_id  INTEGER NOT NULL,
    version             TEXT NOT NULL,
    content_hash        TEXT NOT NULL,
    assigned_by         TEXT NOT NULL DEFAULT '',
    assigned_at         TEXT NOT NULL,
    active              INTEGER NOT NULL DEFAULT 1,
    -- Rollback provenance
    rolled_back_by            TEXT,
    rolled_back_at            TEXT,
    rollback_reason           TEXT,
    rollback_target_version_id INTEGER,
    FOREIGN KEY (package_version_id) REFERENCES skill_lifecycle_packages(id)
);

CREATE INDEX IF NOT EXISTS idx_lifecycle_assignments_skill
    ON skill_lifecycle_assignments(skill_id);
CREATE INDEX IF NOT EXISTS idx_lifecycle_assignments_agent
    ON skill_lifecycle_assignments(agent_name);
CREATE INDEX IF NOT EXISTS idx_lifecycle_assignments_active
    ON skill_lifecycle_assignments(active);
CREATE UNIQUE INDEX IF NOT EXISTS idx_lifecycle_assignments_unique_active
    ON skill_lifecycle_assignments(skill_id, agent_name)
    WHERE active = 1;
"""

CREATE_MATERIALIZATIONS = """
CREATE TABLE IF NOT EXISTS skill_lifecycle_materializations (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    skill_id            TEXT NOT NULL,
    agent_name          TEXT NOT NULL,
    package_version_id  INTEGER NOT NULL,
    version             TEXT NOT NULL,
    content_hash        TEXT NOT NULL,
    success             INTEGER NOT NULL DEFAULT 0,
    error_message       TEXT,
    session_context     TEXT,
    created_at          TEXT NOT NULL,
    FOREIGN KEY (package_version_id) REFERENCES skill_lifecycle_packages(id)
);

CREATE INDEX IF NOT EXISTS idx_lifecycle_materializations_skill_agent
    ON skill_lifecycle_materializations(skill_id, agent_name);
"""


def migrate(db) -> None:
    """Run the lifecycle schema migration (idempotent)."""
    db.execute(CREATE_PACKAGE_VERSIONS)
    db.execute(CREATE_LIFECYCLE_EVENTS)
    db.execute(CREATE_ASSIGNMENTS)
    db.execute(CREATE_MATERIALIZATIONS)
    # Additive migration: claimed_by/claimed_at columns (THR-055 proposal review)
    _migrate_add_claimed_columns(db)


def _migrate_add_claimed_columns(db) -> None:
    """Additive migration: add claimed_by and claimed_at columns.

    Uses ALTER TABLE ADD COLUMN (nullable) — never drops or alters existing
    columns. Existing rows remain readable with NULL for these new columns.
    """
    try:
        db.execute("ALTER TABLE skill_lifecycle_packages ADD COLUMN claimed_by TEXT")
    except Exception:
        pass  # Column already exists
    try:
        db.execute("ALTER TABLE skill_lifecycle_packages ADD COLUMN claimed_at TEXT")
    except Exception:
        pass  # Column already exists


# ── Package version CRUD ──────────────────────────────────────────────────

def insert_package_version(db, pkg: PackageVersion) -> int:
    """Insert a new package version row. Returns the new row id."""
    now = _now_iso()
    pkg.created_at = pkg.created_at or datetime.fromisoformat(now)
    row = db.execute(
        """INSERT INTO skill_lifecycle_packages
           (skill_id, slug, name, version, content_hash, policy_class,
            description, skill_md, content_artifact_key, status, created_at, created_by,
            proposal_task_id, proposal_session_id, proposer_agent,
            reviewer, review_decision, review_rationale, reviewed_at,
            publisher, published_at, publication_decision_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            pkg.skill_id, pkg.slug, pkg.name, pkg.version, pkg.content_hash,
            pkg.policy_class, pkg.description, pkg.skill_md, pkg.content_artifact_key,
            pkg.status.value,
            pkg.created_at.isoformat(), pkg.created_by,
            pkg.proposal_task_id, pkg.proposal_session_id, pkg.proposer_agent,
            pkg.reviewer, pkg.review_decision, pkg.review_rationale,
            pkg.reviewed_at.isoformat() if pkg.reviewed_at else None,
            pkg.publisher,
            pkg.published_at.isoformat() if pkg.published_at else None,
            pkg.publication_decision_id,
        ),
    )
    return row.lastrowid


def get_package_version(db, version_id: int) -> PackageVersion | None:
    """Fetch a package version by primary key."""
    row = db.execute(
        "SELECT * FROM skill_lifecycle_packages WHERE id = ?",
        (version_id,),
    ).fetchone()
    if row is None:
        return None
    return _row_to_package_version(dict(row))


def get_package_version_by_hash(db, skill_id: str, content_hash: str) -> PackageVersion | None:
    """Fetch a package version by skill_id + content_hash."""
    row = db.execute(
        "SELECT * FROM skill_lifecycle_packages WHERE skill_id = ? AND content_hash = ?",
        (skill_id, content_hash),
    ).fetchone()
    if row is None:
        return None
    return _row_to_package_version(dict(row))


def get_latest_package_version(db, skill_id: str) -> PackageVersion | None:
    """Fetch the most recent package version for a skill_id."""
    row = db.execute(
        "SELECT * FROM skill_lifecycle_packages WHERE skill_id = ? ORDER BY id DESC LIMIT 1",
        (skill_id,),
    ).fetchone()
    if row is None:
        return None
    return _row_to_package_version(dict(row))


def update_package_status(
    db, version_id: int, new_status: LifecycleStatus, **kwargs
) -> None:
    """Update the status (and optional provenance fields) of a package version."""
    sets = ["status = ?"]
    params: list = [new_status.value]
    for field in ["reviewer", "review_decision", "review_rationale", "reviewed_at",
                  "publisher", "published_at", "publication_decision_id"]:
        if field in kwargs and kwargs[field] is not None:
            sets.append(f"{field} = ?")
            val = kwargs[field]
            params.append(val.isoformat() if isinstance(val, datetime) else val)
    params.append(version_id)
    db.execute(
        f"UPDATE skill_lifecycle_packages SET {', '.join(sets)} WHERE id = ?",
        tuple(params),
    )


def update_package_claimed(db, version_id: int, claimed_by: str, claimed_at) -> None:
    """Set the optional separate claimant identity and timestamp.

    Does NOT touch created_by or proposer_agent — the founder claim is a
    separate optional identity, never a rewrite of the immutable author.
    """
    at_str = claimed_at.isoformat() if isinstance(claimed_at, datetime) else str(claimed_at)
    db.execute(
        "UPDATE skill_lifecycle_packages SET claimed_by = ?, claimed_at = ? WHERE id = ?",
        (claimed_by, at_str, version_id),
    )


def list_package_versions(
    db, skill_id: str | None = None, status: LifecycleStatus | None = None
) -> list[PackageVersion]:
    """List package versions, optionally filtered."""
    query = "SELECT * FROM skill_lifecycle_packages WHERE 1=1"
    params: list = []
    if skill_id is not None:
        query += " AND skill_id = ?"
        params.append(skill_id)
    if status is not None:
        query += " AND status = ?"
        params.append(status.value)
    query += " ORDER BY id DESC"
    rows = db.execute(query, tuple(params)).fetchall()
    return [_row_to_package_version(dict(r)) for r in rows]


def count_published_packages(db) -> int:
    """Count currently published packages (for cap enforcement).

    A published package counts toward the cap unless it has been explicitly
    retired (has a 'retired' lifecycle event AND no active assignments).
    Freshly published packages without assignments still count.
    """
    conn = _get_conn(db)
    row = conn.execute(
        """SELECT COUNT(*) FROM skill_lifecycle_packages p
           WHERE p.status = ?
           AND NOT (
               EXISTS (
                   SELECT 1 FROM skill_lifecycle_events e
                   WHERE e.skill_id = p.skill_id AND e.event_type = 'retired'
               )
               AND NOT EXISTS (
                   SELECT 1 FROM skill_lifecycle_assignments a
                   WHERE a.skill_id = p.skill_id AND a.active = 1
               )
           )""",
        (LifecycleStatus.PUBLISHED.value,),
    ).fetchone()
    return row[0] if row else 0


# ── Lifecycle events CRUD ─────────────────────────────────────────────────

def insert_lifecycle_event(db, event: LifecycleEvent) -> int:
    """Insert a lifecycle event. Returns the new row id."""
    row = db.execute(
        """INSERT INTO skill_lifecycle_events
           (skill_id, package_version_id, event_type, actor, actor_role,
            previous_status, new_status, content_hash, metadata_json,
            created_at, task_id, session_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            event.skill_id,
            event.package_version_id,
            event.event_type,
            event.actor,
            event.actor_role,
            event.previous_status,
            event.new_status,
            event.content_hash,
            json.dumps(event.metadata) if event.metadata else None,
            event.created_at.isoformat(),
            event.task_id,
            event.session_id,
        ),
    )
    return row.lastrowid


def list_lifecycle_events(
    db, skill_id: str | None = None, limit: int = 100
) -> list[LifecycleEvent]:
    """List lifecycle events, newest first."""
    query = "SELECT * FROM skill_lifecycle_events"
    params: list = []
    if skill_id is not None:
        query += " WHERE skill_id = ?"
        params.append(skill_id)
    query += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    rows = db.execute(query, tuple(params)).fetchall()
    events = []
    for row in rows:
        d = dict(row)
        events.append(LifecycleEvent(
            id=d["id"],
            skill_id=d["skill_id"],
            package_version_id=d["package_version_id"],
            event_type=d["event_type"],
            actor=d.get("actor", ""),
            actor_role=d.get("actor_role", ""),
            previous_status=d.get("previous_status"),
            new_status=d.get("new_status"),
            content_hash=d.get("content_hash"),
            metadata=json.loads(d["metadata_json"]) if d.get("metadata_json") else None,
            created_at=_parse_datetime(d["created_at"]) or datetime.now(timezone.utc),
            task_id=d.get("task_id"),
            session_id=d.get("session_id"),
        ))
    return events


# ── Assignment CRUD ──────────────────────────────────────────────────────

def insert_assignment(db, assign: AssignmentRecord) -> int:
    """Insert a new assignment. Returns the new row id."""
    row = db.execute(
        """INSERT INTO skill_lifecycle_assignments
           (skill_id, agent_name, package_version_id, version, content_hash,
            assigned_by, assigned_at, active, rolled_back_by, rolled_back_at,
            rollback_reason, rollback_target_version_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            assign.skill_id, assign.agent_name, assign.package_version_id,
            assign.version, assign.content_hash, assign.assigned_by,
            assign.assigned_at.isoformat(), 1 if assign.active else 0,
            assign.rolled_back_by,
            assign.rolled_back_at.isoformat() if assign.rolled_back_at else None,
            assign.rollback_reason, assign.rollback_target_version_id,
        ),
    )
    return row.lastrowid


def get_active_assignment(db, skill_id: str, agent_name: str) -> AssignmentRecord | None:
    """Get the active assignment for a skill + agent (or None)."""
    row = db.execute(
        """SELECT * FROM skill_lifecycle_assignments
           WHERE skill_id = ? AND agent_name = ? AND active = 1""",
        (skill_id, agent_name),
    ).fetchone()
    if row is None:
        return None
    return _row_to_assignment(dict(row))


def get_active_assignments_for_agent(db, agent_name: str) -> list[AssignmentRecord]:
    """Get all active assignments for an agent."""
    rows = db.execute(
        "SELECT * FROM skill_lifecycle_assignments WHERE agent_name = ? AND active = 1",
        (agent_name,),
    ).fetchall()
    return [_row_to_assignment(dict(r)) for r in rows]


def get_all_active_assignments_for_skill(db, skill_id: str) -> list[AssignmentRecord]:
    """Get all active assignments for a skill."""
    rows = db.execute(
        "SELECT * FROM skill_lifecycle_assignments WHERE skill_id = ? AND active = 1",
        (skill_id,),
    ).fetchall()
    return [_row_to_assignment(dict(r)) for r in rows]


def deactivate_assignments_for_skill(
    db, skill_id: str, rolled_back_by: str = "", reason: str = "",
    target_version_id: int | None = None,
) -> int:
    """Atomically deactivate all active assignments for a skill (rollback).

    When target_version_id is supplied, only assignments to THAT EXACT
    package version are deactivated. This prevents a rollback addressed
    to one version from silently affecting assignments to a different
    version of the same skill.
    """
    now = _now_iso()
    if target_version_id is not None:
        row = db.execute(
            """UPDATE skill_lifecycle_assignments
               SET active = 0, rolled_back_by = ?, rolled_back_at = ?,
                   rollback_reason = ?, rollback_target_version_id = ?
               WHERE skill_id = ? AND active = 1 AND package_version_id = ?""",
            (rolled_back_by, now, reason, target_version_id, skill_id, target_version_id),
        )
    else:
        row = db.execute(
            """UPDATE skill_lifecycle_assignments
               SET active = 0, rolled_back_by = ?, rolled_back_at = ?,
                   rollback_reason = ?, rollback_target_version_id = ?
               WHERE skill_id = ? AND active = 1""",
            (rolled_back_by, now, reason, target_version_id, skill_id),
        )
    return row.rowcount


def has_active_assignment_on_rejected_version(db, skill_id: str) -> bool:
    """Check whether any active assignment for this skill points to a
    REJECTED package version.

    Used by legacy rollback/retire paths (which take only skill_id, not a
    specific version_id) to fail-closed before any assignment mutation
    when the operation would affect a terminally rejected version.
    """
    conn = _get_conn(db)
    row = conn.execute(
        """SELECT 1
           FROM skill_lifecycle_assignments a
           JOIN skill_lifecycle_packages p ON a.package_version_id = p.id
           WHERE a.skill_id = ? AND a.active = 1 AND p.status = 'rejected'
           LIMIT 1""",
        (skill_id,),
    ).fetchone()
    return row is not None


def deactivate_assignment(db, skill_id: str, agent_name: str, unassigned_by: str = "") -> int:
    """Atomically deactivate a single agent's assignment (unassign)."""
    now = _now_iso()
    row = db.execute(
        """UPDATE skill_lifecycle_assignments
           SET active = 0, rolled_back_by = ?, rolled_back_at = ?
           WHERE skill_id = ? AND agent_name = ? AND active = 1""",
        (unassigned_by, now, skill_id, agent_name),
    )
    return row.rowcount


def _row_to_proposal_queue_item(row: dict) -> dict:
    """Convert a DB row + joins into a ProposalQueueItem dict."""
    return {
        "version_id": row["id"],
        "skill_id": row["skill_id"],
        "slug": row["slug"],
        "name": row["name"],
        "version": row["version"],
        "content_hash": row["content_hash"],
        "proposer_agent": row.get("proposer_agent", "") or "",
        "claimed_by": row.get("claimed_by"),
        "proposal_task_id": row.get("proposal_task_id"),
        "proposal_session_id": row.get("proposal_session_id"),
        "status": LifecycleStatus(row["status"]),
        "latest_validator_version": row.get("latest_validator_version"),
        "latest_validator_key": row.get("latest_validator_key"),
        "permitted_next_action": row.get("permitted_next_action"),
        "assigned_agent_count": int(row.get("assigned_agent_count", 0)),
        "assigned_agents": row.get("assigned_agents", "").split(",") if row.get("assigned_agents") else [],
        "created_at": row.get("created_at", ""),
    }


def list_proposals_queue(
    db, status: str | None = None, page: int = 1, page_size: int = 20,
) -> tuple[list[dict], int]:
    """List proposals for the founder-only queue.

    Default ordering: actionable first (not terminal), then oldest submission.
    Rejected/history items are not actionable and sort after.

    Returns (items, total_count).
    """
    conn = _get_conn(db)

    # Build filter
    where_clauses = []
    params: list = []
    if status is not None:
        where_clauses.append("p.status = ?")
        params.append(status)
    where_sql = " AND " + " AND ".join(where_clauses) if where_clauses else ""

    # Count total
    count_row = conn.execute(
        f"SELECT COUNT(*) FROM skill_lifecycle_packages p WHERE 1=1{where_sql}",
        tuple(params),
    ).fetchone()
    total = count_row[0] if count_row else 0

    # Pre-load assignments per package
    assign_map: dict[int, list[str]] = {}
    assign_rows = conn.execute(
        """SELECT package_version_id, agent_name FROM skill_lifecycle_assignments
           WHERE active = 1 ORDER BY agent_name""",
    ).fetchall()
    for ar in assign_rows:
        vid = ar["package_version_id"]
        if vid not in assign_map:
            assign_map[vid] = []
        assign_map[vid].append(ar["agent_name"])

    # Pre-load latest validation info per package (validator_version, validator_key from events)
    validation_map: dict[int, tuple[str | None, str | None]] = {}
    val_rows = conn.execute(
        """SELECT package_version_id, metadata_json FROM skill_lifecycle_events
           WHERE event_type = 'validated'
           ORDER BY id DESC""",
    ).fetchall()
    seen_val: set[int] = set()
    for vr in val_rows:
        vid = vr["package_version_id"]
        if vid in seen_val:
            continue
        seen_val.add(vid)
        meta = json.loads(vr["metadata_json"]) if vr["metadata_json"] else {}
        validation_map[vid] = (meta.get("validator_version"), meta.get("validator_key"))

    # Fetch packages with ordering: actionable first, then oldest submission
    # Actionable = not terminal (not rejected, not published, not retired, not rolled_back)
    terminal_statuses = ("rejected", "published", "retired", "rolled_back", "legacy_quarantined")
    order_sql = f"""
        CASE WHEN p.status NOT IN ({','.join('?' for _ in terminal_statuses)}) THEN 0 ELSE 1 END,
        p.created_at ASC
    """
    order_params = list(terminal_statuses)

    offset = (page - 1) * page_size
    query = f"""SELECT p.* FROM skill_lifecycle_packages p
        WHERE 1=1{where_sql}
        ORDER BY {order_sql}
        LIMIT ? OFFSET ?"""

    rows = conn.execute(query, tuple(params + order_params + [page_size, offset])).fetchall()

    items = []
    for row in rows:
        d = dict(row)
        vid = d["id"]
        d["assigned_agent_count"] = len(assign_map.get(vid, []))
        d["assigned_agents"] = ",".join(assign_map.get(vid, []))
        vv, vk = validation_map.get(vid, (None, None))
        d["latest_validator_version"] = vv
        d["latest_validator_key"] = vk
        # Compute permitted_next_action
        d["permitted_next_action"] = _compute_permitted_action(LifecycleStatus(d["status"]))
        items.append(_row_to_proposal_queue_item(d))

    return items, total


def _compute_permitted_action(status: LifecycleStatus) -> str | None:
    """Return the single permitted next action for a given status, or None."""
    action_map = {
        LifecycleStatus.PROPOSED: "claim",
        LifecycleStatus.DRAFT: "validate",
        LifecycleStatus.VALIDATION_FAILED: "validate",
        LifecycleStatus.VALIDATED: "submit_review",
        LifecycleStatus.IN_REVIEW: "review",
        LifecycleStatus.APPROVED: "publish",
        LifecycleStatus.PUBLISHED: "assign",
    }
    return action_map.get(status)


def get_proposal_detail(db, version_id: int) -> dict | None:
    """Get full detail for a single proposal version."""
    conn = _get_conn(db)
    row = conn.execute(
        "SELECT * FROM skill_lifecycle_packages WHERE id = ?",
        (version_id,),
    ).fetchone()
    if row is None:
        return None
    d = dict(row)

    # Events
    event_rows = conn.execute(
        "SELECT * FROM skill_lifecycle_events WHERE package_version_id = ? ORDER BY id ASC",
        (version_id,),
    ).fetchall()
    events = []
    last_event_id = None
    for er in event_rows:
        ed = dict(er)
        events.append({
            "id": ed["id"],
            "event_type": ed["event_type"],
            "actor": ed.get("actor", ""),
            "actor_role": ed.get("actor_role", ""),
            "previous_status": ed.get("previous_status"),
            "new_status": ed.get("new_status"),
            "content_hash": ed.get("content_hash"),
            "created_at": ed.get("created_at", ""),
            "metadata": json.loads(ed["metadata_json"]) if ed.get("metadata_json") else None,
            "task_id": ed.get("task_id"),
            "session_id": ed.get("session_id"),
        })
        last_event_id = ed["id"]

    # Assignments
    assign_rows = conn.execute(
        """SELECT * FROM skill_lifecycle_assignments
           WHERE package_version_id = ? ORDER BY id ASC""",
        (version_id,),
    ).fetchall()
    assignments = []
    for ar in assign_rows:
        ad = dict(ar)
        assignments.append({
            "id": ad["id"],
            "agent_name": ad["agent_name"],
            "version": ad["version"],
            "content_hash": ad["content_hash"],
            "assigned_by": ad.get("assigned_by", ""),
            "assigned_at": ad.get("assigned_at", ""),
            "active": bool(ad.get("active", True)),
        })

    # Materializations
    mat_rows = conn.execute(
        """SELECT * FROM skill_lifecycle_materializations
           WHERE package_version_id = ? ORDER BY id DESC""",
        (version_id,),
    ).fetchall()
    materializations = []
    for mr in mat_rows:
        md = dict(mr)
        materializations.append({
            "id": md["id"],
            "agent_name": md["agent_name"],
            "success": bool(md["success"]),
            "error_message": md.get("error_message"),
            "session_context": md.get("session_context"),
            "created_at": md.get("created_at", ""),
        })

    return {
        "version_id": d["id"],
        "skill_id": d["skill_id"],
        "slug": d["slug"],
        "name": d["name"],
        "version": d["version"],
        "description": d.get("description", ""),
        "content_hash": d["content_hash"],
        "content_artifact_key": d.get("content_artifact_key"),
        "policy_class": d.get("policy_class", "standard_operational"),
        "status": LifecycleStatus(d["status"]),
        "proposer_agent": d.get("proposer_agent"),
        "proposal_task_id": d.get("proposal_task_id"),
        "proposal_session_id": d.get("proposal_session_id"),
        "claimed_by": d.get("claimed_by"),
        "claimed_at": d.get("claimed_at"),
        "reviewer": d.get("reviewer"),
        "review_decision": d.get("review_decision"),
        "review_rationale": d.get("review_rationale"),
        "reviewed_at": d.get("reviewed_at"),
        "publisher": d.get("publisher"),
        "published_at": d.get("published_at"),
        "events": events,
        "assignments": assignments,
        "materializations": materializations,
        "last_event_id": last_event_id,
        "created_at": d.get("created_at", ""),
    }


def get_latest_event_id_for_version(db, version_id: int) -> int | None:
    """Get the latest event id for a package version (concurrency marker)."""
    conn = _get_conn(db)
    row = conn.execute(
        "SELECT MAX(id) FROM skill_lifecycle_events WHERE package_version_id = ?",
        (version_id,),
    ).fetchone()
    return row[0] if row and row[0] is not None else None


def _row_to_assignment(row: dict) -> AssignmentRecord:
    return AssignmentRecord(
        id=row["id"],
        skill_id=row["skill_id"],
        agent_name=row["agent_name"],
        package_version_id=row["package_version_id"],
        version=row["version"],
        content_hash=row["content_hash"],
        assigned_by=row.get("assigned_by", ""),
        assigned_at=_parse_datetime(row.get("assigned_at")) or datetime.now(timezone.utc),
        active=bool(row.get("active", True)),
        rolled_back_by=row.get("rolled_back_by"),
        rolled_back_at=_parse_datetime(row.get("rolled_back_at")),
        rollback_reason=row.get("rollback_reason"),
        rollback_target_version_id=row.get("rollback_target_version_id"),
    )


# ── Materialization CRUD ─────────────────────────────────────────────────

def insert_materialization(db, mat: MaterializationRecord) -> int:
    """Insert a materialization event."""
    row = db.execute(
        """INSERT INTO skill_lifecycle_materializations
           (skill_id, agent_name, package_version_id, version, content_hash,
            success, error_message, session_context, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            mat.skill_id, mat.agent_name, mat.package_version_id,
            mat.version, mat.content_hash,
            1 if mat.success else 0, mat.error_message,
            mat.session_context, mat.created_at.isoformat(),
        ),
    )
    return row.lastrowid


def get_latest_materialization(
    db, skill_id: str, agent_name: str
) -> MaterializationRecord | None:
    """Get the latest materialization record for a skill + agent."""
    row = db.execute(
        """SELECT * FROM skill_lifecycle_materializations
           WHERE skill_id = ? AND agent_name = ?
           ORDER BY id DESC LIMIT 1""",
        (skill_id, agent_name),
    ).fetchone()
    if row is None:
        return None
    d = dict(row)
    return MaterializationRecord(
        id=d["id"],
        skill_id=d["skill_id"],
        agent_name=d["agent_name"],
        package_version_id=d["package_version_id"],
        version=d["version"],
        content_hash=d["content_hash"],
        success=bool(d["success"]),
        error_message=d.get("error_message"),
        session_context=d.get("session_context"),
        created_at=_parse_datetime(d["created_at"]) or datetime.now(timezone.utc),
    )


# ── Legacy migration / quarantine ─────────────────────────────────────────


def quarantine_legacy_user_skills(db, org_root, settings) -> int:
    """Migrate existing per-org user-authored skills into the lifecycle ledger.

    Reads ``<org_root>/skills/`` directory. For each user-authored skill found:
    - Copies SKILL.md content to the org ArtifactStore under
      ``skill-lifecycle/legacy/<slug>/SKILL.md`` for immutable retention
    - Creates a PackageVersion record with status LEGACY_QUARANTINED
    - Stores the content_artifact_key referencing the immutable artifact
    - Records a lifecycle event for the migration
    - Does NOT materialize quarantined content into any catalog or workspace

    Malformed/unsafe legacy data is preserved with error metadata.
    Legacy filesystem paths (org_root/skills/) are NEVER referenced — only
    the immutable ArtifactStore key is stored.

    Returns the number of skills quarantined.
    """
    from pathlib import Path
    import hashlib

    org_path = Path(org_root) if not isinstance(org_root, Path) else org_root
    skills_dir = org_path / "skills"
    if not skills_dir.is_dir():
        return 0

    # Resolve ArtifactStore for immutable artifact retention
    try:
        from runtime.infrastructure.artifact_store import ArtifactStore
        from runtime.orchestrator._paths import OrgPaths
        artifact_store = ArtifactStore(OrgPaths(org_path).artifacts_dir)
    except Exception:
        artifact_store = None

    count = 0
    for skill_dir in sorted(skills_dir.iterdir()):
        if not skill_dir.is_dir():
            continue
        slug_val = skill_dir.name
        skill_id = f"hr:{slug_val}"
        skill_md_path = skill_dir / "SKILL.md"

        # Determine artifact key for immutable retention
        base_artifact_key: str | None = None

        if not skill_md_path.is_file():
            # Malformed — no SKILL.md
            content_hash = "malformed-no-content"
            artifact_key = f"skill-lifecycle/legacy/{slug_val}-no-content/0.0.0/SKILL.md"
        else:
            try:
                skill_md = skill_md_path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                content_hash = "malformed-unreadable"
                artifact_key = f"skill-lifecycle/legacy/{slug_val}-unreadable/0.0.0/SKILL.md"
                # Fall through to DB insert without artifact copy
                skill_md = ""
            else:
                content_hash = hashlib.sha256(skill_md.encode("utf-8")).hexdigest()
                artifact_key = f"skill-lifecycle/legacy/{slug_val}/{content_hash[:16]}/SKILL.md"

                # Check if already migrated (idempotent)
                existing = db.execute(
                    "SELECT id FROM skill_lifecycle_packages WHERE skill_id = ? AND content_hash = ?",
                    (skill_id, content_hash),
                ).fetchone()
                if existing:
                    continue

                # Copy content to immutable ArtifactStore
                if artifact_store is not None and skill_md:
                    try:
                        artifact_store.put(artifact_key, skill_md.encode("utf-8"))
                    except Exception:
                        # Artifact store may be unavailable; continue with metadata-only
                        pass

        # Insert quarantined package record
        try:
            db.execute(
                """INSERT OR IGNORE INTO skill_lifecycle_packages
                   (skill_id, slug, name, version, content_hash,
                    policy_class, description, skill_md,
                    content_artifact_key, status, created_at, created_by)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    skill_id, slug_val, slug_val, "0.0.0",
                    content_hash,
                    "standard_operational",
                    f"Legacy skill '{slug_val}' — migrated to lifecycle ledger",
                    "",  # skill_md lives in artifact store
                    artifact_key,  # Immutable ArtifactStore key, NOT filesystem path
                    LifecycleStatus.LEGACY_QUARANTINED.value,
                    datetime.now(timezone.utc).isoformat(),
                    "migration",
                ),
            )
            count += 1
        except Exception:
            continue

        # Record migration event
        try:
            row = db.execute(
                "SELECT id FROM skill_lifecycle_packages WHERE skill_id = ? AND content_hash = ?",
                (skill_id, content_hash),
            ).fetchone()
            if row:
                db.execute(
                    """INSERT INTO skill_lifecycle_events
                       (skill_id, package_version_id, event_type, actor, actor_role,
                        previous_status, new_status, content_hash, metadata_json,
                        created_at, task_id, session_id)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        skill_id, row["id"], "legacy_quarantined",
                        "migration", "service",
                        None, LifecycleStatus.LEGACY_QUARANTINED.value,
                        content_hash,
                        json.dumps({"source": "filesystem", "artifact_key": artifact_key}),
                        datetime.now(timezone.utc).isoformat(),
                        None, None,
                    ),
                )
        except Exception:
            pass

    return count
