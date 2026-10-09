"""Accepted C9 SQLite localization sources; execution SUSPENDED THR291.

These are unit owners, never L/M shipping, reboot or successful apply proof.
No collection, causal control or repetition is authorized by source authoring.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json
import sqlite3

import pytest

from runtime.infrastructure.database import Database
from runtime.models import ThreadRecord, ThreadStatus


class _ResetCommitCut:
    """Test-side pass-through connection; cut only the real reset transaction."""

    def __init__(self, connection: sqlite3.Connection, boundary: str):
        self.connection = connection
        self.boundary = boundary
        self.saw_reset = False
        self.saw_audit = False
        self.cut = False

    def execute(self, sql, parameters=()):
        if sql.startswith('UPDATE thread_participants SET agent_session_id = NULL'):
            self.saw_reset = True
        if 'INSERT INTO audit_log' in sql and 'thread_session_invalidated' in parameters:
            self.saw_audit = True
        return self.connection.execute(sql, parameters)

    def commit(self):
        assert self.saw_reset
        self.cut = True
        if self.boundary == 'before_commit':
            raise RuntimeError('selected genuine reset before commit')
        self.connection.commit()
        raise RuntimeError('selected genuine reset after commit')

    def __getattr__(self, name):
        return getattr(self.connection, name)


RESET_CASES = [(agent, boundary, shape)
               for agent in ('consultant_head', 'consultant_codex')
               for boundary in ('before_commit', 'after_commit')
               for shape in ('ordinary_rows', 'zero_rows', 'already_null')]


@pytest.mark.parametrize('agent,boundary,shape', RESET_CASES, ids=[
    ('head' if agent == 'consultant_head' else 'codex') + '-' + boundary +
    ('' if shape == 'ordinary_rows' else '-' + shape) for agent, boundary, shape in RESET_CASES])
def test_c9_reset_atomic_boundary(tmp_path: Path, agent: str, boundary: str, shape: str) -> None:
    """Second connection proves native per-agent reset/audit co-commit.

    Empty rows legitimately produce no audit; already-null existing rows are
    still helper invocations and produce the native audit. This distinction is
    why operator replay must avoid invoking a previously committed reset.
    """
    path = tmp_path / 'closed-localization.db'
    db = Database(path)
    now = datetime.now(timezone.utc).isoformat()
    original = db._conn
    try:
        for index, status in enumerate((ThreadStatus.OPEN, ThreadStatus.ARCHIVED)):
            thread = f'THR-{index + 1:03d}'
            db.insert_thread(ThreadRecord(id=thread, subject='native reset owner', status=status))
            original.execute('INSERT INTO thread_participants VALUES (?,?,?,?,?,?)',
                             (thread, 'dev_agent', now, 'founder', 'third-agent-provider', 7))
            if shape != 'zero_rows':
                original.execute('INSERT INTO thread_participants VALUES (?,?,?,?,?,?)',
                    (thread, agent, now, 'founder', None if shape == 'already_null' else 'old-provider',
                     0 if shape == 'already_null' else 7))
        original.commit()
        with sqlite3.connect(path) as observer:
            before = observer.execute('SELECT * FROM thread_participants ORDER BY thread_id,agent_name').fetchall()
        cut = _ResetCommitCut(original, boundary)
        db._conn = cut
        with pytest.raises(RuntimeError, match='selected genuine reset '):
            db.reset_thread_sessions_for_agent(agent, audit_scope_id='config:human-team-roster:localization',
                audit_agent='founder', audit_reason='demotion')
        assert cut.cut and cut.saw_reset
        assert cut.saw_audit == (shape != 'zero_rows')
        with sqlite3.connect(path) as observer:
            after = observer.execute('SELECT * FROM thread_participants ORDER BY thread_id,agent_name').fetchall()
            audits = observer.execute("SELECT task_id,agent,payload FROM audit_log WHERE action='thread_session_invalidated' ORDER BY id").fetchall()
            assert [row for row in after if row[1] == 'dev_agent'] == [row for row in before if row[1] == 'dev_agent']
            if boundary == 'before_commit':
                assert after == before and audits == []
            else:
                affected = [row for row in after if row[1] == agent]
                assert all(row[4:] == (None, 0) for row in affected)
                assert len(affected) == (0 if shape == 'zero_rows' else 2)
                assert audits == ([] if shape == 'zero_rows' else [
                    ('config:human-team-roster:localization', 'founder', audits[0][2])])
                if audits:
                    assert json.loads(audits[0][2]) == {'reason': 'demotion', 'rows': 2, 'name': agent}
            assert len(audits) == (1 if boundary == 'after_commit' and shape != 'zero_rows' else 0)
        # Restore the real connection before cleanup; the spy never supplies a
        # manufactured SQL outcome or suppresses a rollback.
        db._conn = original
    finally:
        db._conn = original
        db.close()
