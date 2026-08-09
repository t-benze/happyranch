"""THR-055 B2 Slice A1 persistence for dark custom-skill records.

This module deliberately owns no routes, eligibility resolution, or
materialization wiring.  It supplies only the atomic identity + first-version
write required by the additive schema.
"""

from __future__ import annotations

import sqlite3


def _insert_first_version(
    conn: sqlite3.Connection,
    *,
    skill_id: str,
    content_hash: str,
    content_artifact_key: str,
    author_kind: str,
    author_identity: str,
    created_at: str,
    parent_version_id: int | None = None,
    skill_md_cache: str | None = None,
    references_manifest: str | None = None,
    assets_manifest: str | None = None,
    source_task_id: str | None = None,
    source_session_id: str | None = None,
    task_brief_digest: str | None = None,
) -> int:
    """Insert the first immutable version and return its database id."""
    return conn.execute(
        """INSERT INTO custom_skill_versions
           (skill_id, parent_version_id, content_hash, content_artifact_key,
            skill_md_cache, references_manifest, assets_manifest, created_at,
            author_kind, author_identity, source_task_id, source_session_id,
            task_brief_digest)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            skill_id,
            parent_version_id,
            content_hash,
            content_artifact_key,
            skill_md_cache,
            references_manifest,
            assets_manifest,
            created_at,
            author_kind,
            author_identity,
            source_task_id,
            source_session_id,
            task_brief_digest,
        ),
    ).lastrowid


def create_skill_with_first_version(
    conn: sqlite3.Connection,
    *,
    skill_id: str,
    org_slug: str,
    slug: str,
    name: str,
    origin_kind: str,
    origin_agent: str | None,
    created_by: str,
    content_hash: str,
    content_artifact_key: str,
    author_kind: str,
    author_identity: str,
    created_at: str,
    description: str = "",
    parent_version_id: int | None = None,
    skill_md_cache: str | None = None,
    references_manifest: str | None = None,
    assets_manifest: str | None = None,
    source_task_id: str | None = None,
    source_session_id: str | None = None,
    task_brief_digest: str | None = None,
) -> int:
    """Atomically create a custom-skill identity and its first version.

    ``current_version_id`` is deliberately NULL only inside this transaction:
    it is set after the version insert succeeds, before COMMIT.  A version
    failure therefore rolls back the identity row rather than committing a
    live skill without a current version.
    """
    previous_isolation = conn.isolation_level
    try:
        conn.isolation_level = None
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            """INSERT INTO custom_skills
               (id, org_slug, slug, name, description, policy_class,
                origin_kind, origin_agent, created_at, created_by)
               VALUES (?, ?, ?, ?, ?, 'standard_operational', ?, ?, ?, ?)""",
            (
                skill_id,
                org_slug,
                slug,
                name,
                description,
                origin_kind,
                origin_agent,
                created_at,
                created_by,
            ),
        )
        version_id = _insert_first_version(
            conn,
            skill_id=skill_id,
            content_hash=content_hash,
            content_artifact_key=content_artifact_key,
            author_kind=author_kind,
            author_identity=author_identity,
            created_at=created_at,
            parent_version_id=parent_version_id,
            skill_md_cache=skill_md_cache,
            references_manifest=references_manifest,
            assets_manifest=assets_manifest,
            source_task_id=source_task_id,
            source_session_id=source_session_id,
            task_brief_digest=task_brief_digest,
        )
        conn.execute(
            "UPDATE custom_skills SET current_version_id = ? WHERE id = ?",
            (version_id, skill_id),
        )
        conn.commit()
        return version_id
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.isolation_level = previous_isolation
