from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from runtime.infrastructure.database import Database


def _adapter(path: Path) -> sqlite3.Connection:
    """Clearly labeled isolated adapter; this SQL is not a Database migration."""
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys=ON")
    schema = (Path(__file__).parents[1] / "fixtures" / "workflow_u0" / "proposed_workflow_schema.sql").read_text()
    conn.executescript(schema)
    return conn


def _seed(conn: sqlite3.Connection) -> None:
    conn.execute("INSERT INTO workflow_template_drafts VALUES ('d','eng',X'61','d0','compiler@1','validator@1','source@1','founder','now')")
    conn.execute("INSERT INTO workflow_template_versions VALUES ('v','d','eng',1,X'61','v0','compiler@1','validator@1','source@1','founder')")
    conn.execute("INSERT INTO workflow_authorization_revisions VALUES ('a','eng',1,X'61','a0','source@1','now')")
    conn.execute("INSERT INTO workflow_active_authorizations VALUES ('eng','a')")
    conn.execute("INSERT INTO workflow_binding_snapshots VALUES ('b','v','a',X'61','b0','now')")
    conn.execute("INSERT INTO workflow_contexts VALUES ('c','b',X'61','c0','task','TASK-0')")
    conn.execute("INSERT INTO workflow_instances VALUES ('i','b','c','TASK-ROOT','founder','reviewing')")
    conn.execute("INSERT INTO workflow_instance_tasks VALUES ('i','TASK-1','sess-1','maker',1,'completed')")
    conn.execute("INSERT INTO workflow_events VALUES ('e','i','submitted',X'61','e0','now')")
    conn.execute("INSERT INTO workflow_submissions VALUES ('s','i',1,X'61','digest-r1',NULL,'TASK-1','sess-1','result-1','maker-a')")
    conn.execute("INSERT INTO workflow_submission_contributors VALUES ('s','maker-a','TASK-1','sess-1','result-1','maker')")
    conn.execute("INSERT INTO workflow_rounds VALUES ('r','i','s',1,'reviewing')")
    conn.execute("INSERT INTO workflow_review_requests VALUES ('q','r','reviewer-b',1,X'61','scope-r1','pending',NULL)")


def _assert_service_validation(conn: sqlite3.Connection, *, principal: str, digest: str) -> None:
    """Proposed service/transaction validation, deliberately not a SQLite claim."""
    makers = {row[0] for row in conn.execute("SELECT principal FROM workflow_submission_contributors WHERE submission_id='s'")}
    expected = conn.execute("SELECT submission_digest FROM workflow_submissions WHERE id='s'").fetchone()[0]
    if principal in makers:
        raise ValueError("historical_maker_cannot_review")
    if digest != expected:
        raise ValueError("wrong_submission_digest")


def test_actual_database_initialization_preserves_historical_rows_then_adapter_installs(tmp_path: Path) -> None:
    path = tmp_path / "historical-current.db"
    db = Database(path)
    db.execute("CREATE TABLE u0_historical_marker(id TEXT PRIMARY KEY, value TEXT NOT NULL)")
    db.execute("INSERT INTO u0_historical_marker VALUES ('v0-enrollment','preserved')")
    db._conn.commit()
    db.close()
    conn = _adapter(path)
    _seed(conn)
    conn.commit()
    assert conn.execute("SELECT value FROM u0_historical_marker WHERE id='v0-enrollment'").fetchone() == ("preserved",)
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    assert conn.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    conn.close()
    Database(path).close()
    assert sqlite3.connect(path).execute("SELECT value FROM u0_historical_marker").fetchone() == ("preserved",)


def test_proposed_adapter_rejects_wrong_digest_and_records_service_only_independence_limit(tmp_path: Path) -> None:
    conn = _adapter(tmp_path / "candidate.db")
    _seed(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO workflow_review_receipts VALUES ('bad','q','s','wrong',1,X'61','proof-bad','approved',NULL,'now')")
    with pytest.raises(ValueError, match="historical_maker"):
        _assert_service_validation(conn, principal="maker-a", digest="digest-r1")
    with pytest.raises(ValueError, match="wrong_submission"):
        _assert_service_validation(conn, principal="reviewer-b", digest="wrong")
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_interrupted_adapter_install_rolls_back_then_reopens_and_replays(tmp_path: Path) -> None:
    path = tmp_path / "interrupted.db"
    Database(path).close()
    conn = sqlite3.connect(path)
    conn.execute("BEGIN")
    conn.execute("CREATE TABLE workflow_install_marker(id TEXT PRIMARY KEY)")
    conn.rollback()
    assert conn.execute("SELECT name FROM sqlite_master WHERE name='workflow_install_marker'").fetchone() is None
    conn.close()
    installed = _adapter(path)
    _seed(installed)
    installed.commit()
    installed.close()
    reopened = sqlite3.connect(path)
    assert reopened.execute("SELECT count(*) FROM workflow_submissions").fetchone() == (1,)
    assert reopened.execute("PRAGMA integrity_check").fetchone() == ("ok",)
