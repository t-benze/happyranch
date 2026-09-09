from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest


def _proposed_connection(tmp_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(tmp_path / "u0-proposed.sqlite")
    conn.execute("PRAGMA foreign_keys=ON")
    schema = (Path(__file__).parents[1] / "fixtures" / "workflow_u0" / "proposed_workflow_schema.sql").read_text()
    conn.executescript(schema)
    return conn


def _seed(conn: sqlite3.Connection) -> None:
    conn.execute("INSERT INTO workflow_templates VALUES ('t1','org/o/team/t',1,'body','author','now')")
    conn.execute("INSERT INTO workflow_template_roles VALUES ('t1','author','agent','purpose-author')")
    conn.execute("INSERT INTO workflow_inputs VALUES ('i1','task','TASK-0','input-digest','visible')")
    conn.execute("INSERT INTO workflow_runs VALUES ('run1','t1',1,'env','reviewing')")
    conn.execute("INSERT INTO workflow_run_inputs VALUES ('run1','i1')")
    conn.execute("INSERT INTO workflow_attempts VALUES ('a1','run1','author-work',NULL,'TASK-1','sess-1','maker-a',1,'completed')")
    conn.execute("INSERT INTO workflow_submissions VALUES ('s1','run1','a1',1,'digest-r1','maker-a')")
    conn.execute("INSERT INTO workflow_rounds VALUES ('round1','s1',1,'reviewing',1)")


def test_proposed_additive_schema_enforces_fk_replay_and_current_revision_shape(tmp_path: Path) -> None:
    conn = _proposed_connection(tmp_path); _seed(conn)
    conn.execute("INSERT INTO workflow_review_requests VALUES ('q1','round1',NULL,'founder','auth',1,'pending')")
    conn.execute("INSERT INTO workflow_review_receipts VALUES ('r1','q1','digest-r1','founder','approved','result-digest','now')")
    conn.execute("INSERT INTO workflow_operation_replays VALUES ('run1','op-1','request','receipt')")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO workflow_operation_replays VALUES ('run1','op-1','other','receipt')")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO workflow_review_receipts VALUES ('bad','missing','digest-r1','x','approved','result-digest','now')")


def test_proposed_interrupted_stage_rolls_back_without_residue_and_reopens(tmp_path: Path) -> None:
    conn = _proposed_connection(tmp_path); _seed(conn)
    conn.commit()
    conn.execute("BEGIN")
    conn.execute("INSERT INTO workflow_review_requests VALUES ('q1','round1',NULL,'founder','auth',1,'pending')")
    conn.rollback()
    assert conn.execute("SELECT count(*) FROM workflow_review_requests").fetchone()[0] == 0
    conn.close()
    reopened = sqlite3.connect(tmp_path / "u0-proposed.sqlite")
    reopened.execute("PRAGMA foreign_keys=ON")
    reopened.execute("INSERT INTO workflow_review_requests VALUES ('q1','round1',NULL,'founder','auth',1,'pending')")
    assert reopened.execute("SELECT count(*) FROM workflow_review_requests").fetchone()[0] == 1
