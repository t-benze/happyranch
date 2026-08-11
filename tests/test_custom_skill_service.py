import sqlite3

from runtime.skills.custom import service


def _db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
    CREATE TABLE custom_skill_versions (id INTEGER PRIMARY KEY, skill_id TEXT, parent_version_id INTEGER,
      content_hash TEXT, content_artifact_key TEXT, skill_md_cache TEXT, validation_state TEXT,
      validator_version TEXT, validation_findings TEXT, created_at TEXT, author_kind TEXT, author_identity TEXT,
      source_task_id TEXT, source_session_id TEXT, task_brief_digest TEXT);
    CREATE TABLE custom_skill_eligibility_rules (id INTEGER PRIMARY KEY, skill_id TEXT, scope_type TEXT,
      scope_target TEXT, effect TEXT, created_at TEXT, created_by TEXT, superseded_at TEXT);
    CREATE TABLE custom_skill_eligibility_events (id INTEGER PRIMARY KEY, skill_id TEXT, actor TEXT,
      preview_revision INTEGER, rule_set_json TEXT, affected_newly_visible TEXT, affected_newly_hidden TEXT, created_at TEXT);
    CREATE TABLE custom_skill_events (id INTEGER PRIMARY KEY, skill_id TEXT, event_type TEXT, actor TEXT,
      version_id INTEGER, created_at TEXT, task_id TEXT, session_id TEXT);
    """)
    return conn


def test_version_is_append_only_and_validation_is_hash_bound():
    conn = _db()
    first = service.create_version(conn, skill_id="custom:one", skill_md="# One", actor_kind="human", actor="founder", artifact_key="one", validation={"ok": True, "errors": []})
    second = service.create_version(conn, skill_id="custom:one", skill_md="not markdown", actor_kind="human", actor="founder", artifact_key="two", validation={"ok": False, "errors": ["SKILL.md must start with a heading"]}, parent_id=first[0])
    assert first[2] == "valid"
    assert second[2] == "invalid"
    assert conn.execute("SELECT count(*) FROM custom_skill_versions").fetchone()[0] == 2


def test_rule_replacement_supersedes_prior_set_and_records_one_audit_event():
    conn = _db()
    service.replace_rules(conn, skill_id="custom:one", actor="founder", revision=1,
                          rules=[{"scope_type": "org", "scope_target": None, "effect": "allow"}],
                          newly_visible=["dev"], newly_hidden=[])
    service.replace_rules(conn, skill_id="custom:one", actor="founder", revision=1,
                          rules=[{"scope_type": "agent", "scope_target": "dev", "effect": "deny"}],
                          newly_visible=[], newly_hidden=["dev"])
    assert conn.execute("SELECT count(*) FROM custom_skill_eligibility_rules WHERE superseded_at IS NULL").fetchone()[0] == 1
    assert conn.execute("SELECT count(*) FROM custom_skill_eligibility_events").fetchone()[0] == 2
