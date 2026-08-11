"""Additive persistence helpers for THR-055 B2 custom skills.

The module intentionally has no proposal state.  Version rows are append-only
and all policy replacement is performed in one SQLite transaction.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _conn(db):
    return getattr(db, "_conn", db)


def validate_package(org, *, slug: str, name: str, skill_md: str) -> dict:
    """Run the built-in package validator for a custom-skill version."""
    # Keep custom skills on the same deterministic safety guard as the
    # built-in authoring pipeline; do not duplicate protected-slug policy.
    from runtime.daemon.routes.skills import _validate_skill_package

    return _validate_skill_package(
        org=org,
        slug=slug,
        skill_id=f"custom:{slug}",
        name=name,
        version="1",
        policy_class="standard_operational",
        skill_md=skill_md,
    )


def create_version(conn, *, skill_id: str, skill_md: str, actor_kind: str,
                   actor: str, artifact_key: str, validation: dict,
                   task_id: str | None = None, session_id: str | None = None,
                   brief_digest: str | None = None,
                   parent_id: int | None = None) -> tuple[int, str, str]:
    """Append and validate an immutable content version."""
    content_hash = hashlib.sha256(skill_md.encode()).hexdigest()
    valid = bool(validation["ok"])
    result = conn.execute(
        """INSERT INTO custom_skill_versions
           (skill_id,parent_version_id,content_hash,content_artifact_key,skill_md_cache,
            validation_state,validator_version,validation_findings,created_at,
            author_kind,author_identity,source_task_id,source_session_id,task_brief_digest)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (skill_id, parent_id, content_hash, artifact_key, skill_md,
         "valid" if valid else "invalid", "THR-055/1.0.0",
         json.dumps([] if valid else validation["errors"]), now(),
         actor_kind, actor, task_id, session_id, brief_digest),
    )
    return result.lastrowid, content_hash, "valid" if valid else "invalid"


def append_event(conn, skill_id: str, event_type: str, actor: str, version_id: int | None,
                 *, task_id: str | None = None, session_id: str | None = None) -> None:
    conn.execute("INSERT INTO custom_skill_events (skill_id,event_type,actor,version_id,created_at,task_id,session_id) VALUES (?,?,?,?,?,?,?)",
                 (skill_id, event_type, actor, version_id, now(), task_id, session_id))


def current(conn, skill_id: str):
    return conn.execute("""SELECT s.*,v.content_hash,v.content_artifact_key,v.skill_md_cache,
        v.validation_state,v.id AS version_id FROM custom_skills s
        JOIN custom_skill_versions v ON v.id=s.current_version_id WHERE s.id=?""", (skill_id,)).fetchone()


def current_rules(conn, skill_id: str):
    return conn.execute("SELECT scope_type,scope_target,effect FROM custom_skill_eligibility_rules WHERE skill_id=? AND superseded_at IS NULL", (skill_id,)).fetchall()


def replace_rules(conn, *, skill_id: str, actor: str, revision: int, rules: list[dict],
                  newly_visible: list[str], newly_hidden: list[str]) -> None:
    conn.execute("UPDATE custom_skill_eligibility_rules SET superseded_at=? WHERE skill_id=? AND superseded_at IS NULL", (now(), skill_id))
    for rule in rules:
        conn.execute("INSERT INTO custom_skill_eligibility_rules (skill_id,scope_type,scope_target,effect,created_at,created_by) VALUES (?,?,?,?,?,?)",
                     (skill_id, rule["scope_type"], rule.get("scope_target"), rule["effect"], now(), actor))
    conn.execute("""INSERT INTO custom_skill_eligibility_events
       (skill_id,actor,preview_revision,rule_set_json,affected_newly_visible,affected_newly_hidden,created_at)
       VALUES (?,?,?,?,?,?,?)""", (skill_id, actor, revision, json.dumps(rules), json.dumps(newly_visible), json.dumps(newly_hidden), now()))


def record_materialization(
    conn, *, skill_id: str, agent_name: str, task_id: str | None,
    session_context: str, session_id: str, version_id: int, content_hash: str,
    success: bool, error_message: str | None = None,
) -> None:
    """Persist one per-session custom-skill materialization outcome."""
    conn.execute(
        """INSERT INTO custom_skill_materializations
           (skill_id,agent_name,task_id,session_context,session_id,version_id,
            content_hash,success,error_message,created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (skill_id, agent_name, task_id, session_context, session_id, version_id,
         content_hash, int(success), error_message, now()),
    )
