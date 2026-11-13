from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Literal

from runtime.infrastructure.db._shared import _late_database_now as _now, _synchronized


LEGACY_REVIEWER_AGENTS: tuple[str, ...] = ("code_reviewer",)


class ReviewerPolicyError(ValueError):
    """The durable reviewer pair is unavailable or cannot be classified."""


def parse_reviewer_agents(raw: str | None) -> list[str]:
    if not isinstance(raw, str):
        raise ReviewerPolicyError("invalid reviewer_agents")
    try:
        value = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise ReviewerPolicyError("invalid reviewer_agents") from exc
    if (not isinstance(value, list)
            or any(not isinstance(name, str) or not name.strip() for name in value)
            or len(set(value)) != len(value)):
        raise ReviewerPolicyError("invalid reviewer_agents")
    return value


def validate_reviewer_policy(raw: str | None) -> None:
    if not isinstance(raw, str):
        raise ReviewerPolicyError("invalid reviewer_agents_policy")
    def closed_object(pairs: list[tuple]) -> dict:
        if len(pairs) != 1 or pairs[0][0] != "version":
            raise ReviewerPolicyError("invalid reviewer_agents_policy")
        return dict(pairs)
    try:
        value = json.loads(raw, object_pairs_hook=closed_object)
    except (TypeError, ValueError) as exc:
        raise ReviewerPolicyError("invalid reviewer_agents_policy") from exc
    if (not isinstance(value, dict) or set(value) != {"version"}
            or type(value["version"]) is not int or value["version"] != 2):
        raise ReviewerPolicyError("unsupported reviewer_agents_policy")


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
        if section in {"reviewer_agents", "reviewer_agents_policy"}:
            raise ReviewerPolicyError("reviewer settings require the composite pair owner")
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

    @_synchronized
    def get_reviewer_agents_pair(self) -> dict[str, str]:
        """One SQLite snapshot: never combine independently read rows."""
        return {row["section"]: row["value_json"] for row in self._conn.execute(
            "SELECT section, value_json FROM org_settings WHERE section IN (?, ?)",
            ("reviewer_agents", "reviewer_agents_policy"),
        ).fetchall()}

    def establish_reviewer_agents_policy(self, legacy_missing: list[str]) -> None:
        self._write_reviewer_agents_pair("legacy", legacy_missing, actor="orchestrator")

    def initialize_reviewer_agents(self, value: list[str]) -> None:
        """Only the proven fresh constructor may call this initializer."""
        self._write_reviewer_agents_pair("fresh", value, actor="orchestrator")

    def replace_reviewer_agents(self, value: list[str], *, actor: str = "founder",
                                validate_only: bool = False) -> None:
        self._write_reviewer_agents_pair("replace", value, actor=actor,
                                        validate_only=validate_only)

    @_synchronized
    def _write_reviewer_agents_pair(
        self, mode: Literal["legacy", "fresh", "replace"], value: list[str], *,
        actor: str, validate_only: bool = False,
    ) -> None:
        value = parse_reviewer_agents(json.dumps(value))
        # Validation-only preflight never starts a write transaction or stamps intent.
        if not validate_only:
            self._conn.execute("BEGIN IMMEDIATE")
        try:
            pair = self.get_reviewer_agents_pair()
            marker = pair.get("reviewer_agents_policy")
            raw = pair.get("reviewer_agents")
            if marker is not None:
                validate_reviewer_policy(marker)
            if mode == "fresh" and pair:
                raise ReviewerPolicyError("fresh reviewer pair already exists")
            before = None
            if raw is not None:
                try:
                    before = parse_reviewer_agents(raw)
                except ReviewerPolicyError:
                    if mode != "replace":
                        raise
                    # Preserve repair provenance without copying invalid stored bytes.
                    before = {"invalid": True}
            if mode == "legacy":
                if marker is not None:
                    if raw is None:
                        raise ReviewerPolicyError("reviewer policy has no value")
                    # Valid marked pairs are immutable on startup, including [].
                    if not validate_only:
                        self._conn.commit()
                    return
                if before is not None:
                    # Legacy [] had the old effective reviewer, never intentional none.
                    value = before if before else list(LEGACY_REVIEWER_AGENTS)
            changes: list[tuple[str, str, dict, dict]] = []
            if raw is None or before != value:
                changes.append(("reviewer_agents", json.dumps(value),
                                {"value": before} if before is not None else {},
                                {"value": value}))
            if marker is None:
                changes.append(("reviewer_agents_policy", '{"version":2}', {}, {"version": 2}))
            if not validate_only:
                now = datetime.now(timezone.utc).isoformat()
                for section, encoded, old, new in changes:
                    self._conn.execute(
                        "INSERT INTO org_settings (section,value_json,updated_at,updated_by) VALUES (?,?,?,?) "
                        "ON CONFLICT(section) DO UPDATE SET value_json=excluded.value_json, "
                        "updated_at=excluded.updated_at, updated_by=excluded.updated_by",
                        (section, encoded, now, actor),
                    )
                    self._conn.execute(
                        "INSERT INTO audit_log (task_id,agent,action,payload,timestamp) "
                        "VALUES (?,?,'org_config_write',?,?)",
                        (f"config:{section}", actor, json.dumps({
                            "section": section,
                            "tiers": sorted(key for key in set(old) | set(new) if old.get(key) != new.get(key)),
                            "before": old, "after": new,
                        }), now),
                    )
                self._conn.commit()
        except BaseException:
            if not validate_only:
                self._conn.rollback()
            raise

    # --- KB views ---

    @_synchronized
    def record_kb_view(self, slug: str) -> None:
        """Increment the view counter for a KB entry, stamping last_viewed_at.

        UPSERT: inserts the row at count 1 on first view, otherwise increments
        the existing count. Caller decides *when* to record (agent-CLI reads
        only — see kb-view-tracking-caller-signal). This is a metric write, not
        an audit row; it never routes through audit_log.
        """
        now = _now().isoformat()
        self._conn.execute(
            """INSERT INTO kb_views (slug, view_count, last_viewed_at)
               VALUES (?, 1, ?)
               ON CONFLICT(slug) DO UPDATE SET
                 view_count = view_count + 1,
                 last_viewed_at = excluded.last_viewed_at""",
            (slug, now),
        )
        self._conn.commit()
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
