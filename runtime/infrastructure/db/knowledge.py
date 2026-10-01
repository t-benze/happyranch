from __future__ import annotations

import json
from datetime import datetime, timezone

from runtime.infrastructure.db._shared import _synchronized


class KnowledgeMixin:
    # --- Org Settings (THR-095) ---

    @_synchronized
    def upsert_org_setting(
        self,
        section: str,
        value_json: str,
        *,
        before: dict | None = None,
        after: dict | None = None,
        actor: str = "founder",
    ) -> None:
        """Upsert an org_settings row AND insert its config:<section> audit
        row in one atomic transaction (same connection, single commit).

        A crash/failure before commit rolls BOTH back — no split-brain."""
        now = datetime.now(timezone.utc).isoformat()
        # F4 fix: emit only the actually-changed tiers, not the full before dict.
        if isinstance(before, dict) and isinstance(after, dict):
            _tiers = sorted(
                k for k in set(before) | set(after)
                if before.get(k) != after.get(k)
            )
        elif before is not None:
            _tiers = list(before) if isinstance(before, dict) else [section]
        else:
            _tiers = [section]
        audit_payload = json.dumps({
            "section": section,
            "tiers": _tiers,
            "before": before or {},
            "after": after or {},
        })
        self._conn.execute("BEGIN")
        try:
            self._conn.execute(
                "INSERT INTO org_settings (section, value_json, updated_at, updated_by) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(section) DO UPDATE SET "
                "value_json = excluded.value_json, "
                "updated_at = excluded.updated_at, "
                "updated_by = excluded.updated_by",
                (section, value_json, now, actor),
            )
            self._conn.execute(
                "INSERT INTO audit_log (task_id, agent, action, payload, timestamp) "
                "VALUES (?, ?, 'org_config_write', ?, ?)",
                (f"config:{section}", actor, audit_payload, now),
            )
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    @_synchronized
    def get_org_setting(self, section: str) -> str | None:
        """Return the value_json for *section* or None if no row exists."""
        row = self._conn.execute(
            "SELECT value_json FROM org_settings WHERE section = ?",
            (section,),
        ).fetchone()
        return row["value_json"] if row else None

    @_synchronized
    def get_all_org_settings(self) -> dict[str, str]:
        """Return {section: value_json} for every row in org_settings."""
        rows = self._conn.execute(
            "SELECT section, value_json FROM org_settings"
        ).fetchall()
        return {row["section"]: row["value_json"] for row in rows}

    # --- KB views ---

    @_synchronized
    def kb_view_stats(self) -> list[dict]:
        """Return per-slug view tallies, most-viewed first.

        Ordered by view_count DESC, then last_viewed_at DESC so ties surface
        the most recently read entry first.
        """
        rows = self._conn.execute(
            """SELECT slug, view_count, last_viewed_at
               FROM kb_views
               ORDER BY view_count DESC, last_viewed_at DESC"""
        ).fetchall()
        return [dict(r) for r in rows]

    # --- Skill validation events ---

    @_synchronized
    def list_skill_validation_events(
        self,
        *,
        skill_id: str | None = None,
        agent: str | None = None,
        source: str | None = None,
        since: str | None = None,
        severity: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        """List skill validation events with optional filters."""
        clauses = ["1=1"]
        params: list = []
        if skill_id is not None:
            clauses.append("skill_id = ?")
            params.append(skill_id)
        if agent is not None:
            clauses.append("agent = ?")
            params.append(agent)
        if source is not None:
            clauses.append("source = ?")
            params.append(source)
        if since is not None:
            clauses.append("created_at >= ?")
            params.append(since)
        if severity is not None:
            clauses.append("severity = ?")
            params.append(severity)
        params.append(limit)
        rows = self._conn.execute(
            f"""SELECT id, skill_id, slug, agent, source, severity, ok, version,
                       findings, reason_codes, created_at
                FROM skill_validation_events
                WHERE {' AND '.join(clauses)}
                ORDER BY created_at DESC
                LIMIT ?""",
            params,
        ).fetchall()
        result: list[dict] = []
        for r in rows:
            d = dict(r)
            d["ok"] = bool(d["ok"])
            d["findings"] = json.loads(d["findings"] or "[]")
            d["reason_codes"] = json.loads(d["reason_codes"] or "[]")
            result.append(d)
        return result

    @_synchronized
    def get_latest_skill_validation(
        self, skill_id: str, version: str | None = None
    ) -> dict | None:
        """Return the latest validation event for a skill, optionally for a specific version."""
        if version is not None:
            row = self._conn.execute(
                """SELECT id, skill_id, slug, agent, source, severity, ok, version,
                           findings, reason_codes, created_at
                    FROM skill_validation_events
                    WHERE skill_id = ? AND version = ?
                    ORDER BY created_at DESC LIMIT 1""",
                (skill_id, version),
            ).fetchone()
        else:
            row = self._conn.execute(
                """SELECT id, skill_id, slug, agent, source, severity, ok, version,
                           findings, reason_codes, created_at
                    FROM skill_validation_events
                    WHERE skill_id = ?
                    ORDER BY created_at DESC LIMIT 1""",
                (skill_id,),
            ).fetchone()
        if row is None:
            return None
        d = dict(row)
        d["ok"] = bool(d["ok"])
        d["findings"] = json.loads(d["findings"] or "[]")
        d["reason_codes"] = json.loads(d["reason_codes"] or "[]")
        return d

    @_synchronized
    def get_latest_skill_materialization(
        self, skill_id: str, agent: str
    ) -> dict | None:
        """Return the latest materialization event for a skill+agent pair.

        Used by effective-state computation (§7.1): a skill is effective for an
        agent iff the latest materialization event's version matches the current
        store version.
        """
        row = self._conn.execute(
            """SELECT id, skill_id, slug, agent, source, severity, ok, version,
                       findings, reason_codes, created_at
                FROM skill_validation_events
                WHERE skill_id = ? AND agent = ? AND source = 'materialization'
                ORDER BY created_at DESC LIMIT 1""",
            (skill_id, agent),
        ).fetchone()
        if row is None:
            return None
        d = dict(row)
        d["ok"] = bool(d["ok"])
        d["findings"] = json.loads(d["findings"] or "[]")
        d["reason_codes"] = json.loads(d["reason_codes"] or "[]")
        return d
