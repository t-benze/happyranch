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


PUBLICATION_CASES = [(phase, boundary, direction)
    for phase in ('fence', 'prepared', 'file_phase_reserved', 'canonical_published',
                  'pointer_committed', 'cache_installed', 'profile_reconcile')
    for boundary in ('before_commit', 'after_commit')
    for direction in ('complete', 'compensate')]


class _PublicationCommitCut:
    """Pass through actual SQL/transactions; identify the owned phase, never nth commit.

    Immediate second-connection residue is captured at the selected boundary.
    Raised logical faults unwind normally; this is not an abrupt process loss
    or reboot. Later native compensation/retry is observed as its own effect.
    """
    def __init__(self, connection, path, operation, phase, boundary):
        self.connection = connection
        self.path = path
        self.owner = 'THR296:' + operation
        self.phase = phase
        self.boundary = boundary
        self.current_phase = None
        self.cut = False
        self.prefix = None
        self.sql = []
        self.owner_rollbacks = []

    def execute(self, statement, parameters=()):
        if statement == 'BEGIN IMMEDIATE':
            self.current_phase = None
            self.sql = []
        self.sql.append((statement, tuple(parameters)))
        owner_parameter = any(isinstance(value, str) and (value == self.owner or value.startswith(self.owner + ':')) for value in parameters)
        if 'INSERT INTO workflow_publication_journals(' in statement and owner_parameter:
            self.current_phase = 'fence'
        elif 'UPDATE workflow_publication_journals SET snapshot_bytes=' in statement and owner_parameter:
            self.current_phase = 'prepared'
        else:
            for state in ('file_phase_reserved', 'canonical_published', 'pointer_committed', 'cache_installed'):
                if "SET state='" + state + "'" in statement:
                    journal = next((value for value in parameters if isinstance(value, str) and value.startswith('WAJ-')), None)
                    row = None if journal is None else self.connection.execute('SELECT publisher,namespace FROM workflow_publication_journals WHERE id=?', (journal,)).fetchone()
                    if row is not None and (row[0] == self.owner or row[0].startswith(self.owner + ':')) and row[1] == 'org/alpha':
                        self.current_phase = state
        return self.connection.execute(statement, parameters)

    def _snapshot(self):
        # Never use immutable=1 while the owner has committed WAL data.
        with sqlite3.connect(self.path.as_uri() + '?mode=ro', uri=True) as observer:
            return {name: tuple(observer.execute('SELECT * FROM "' + name + '" ORDER BY rowid'))
                    for name in ('workflow_authority_pointers', 'workflow_publication_journals',
                                 'workflow_profile_dependencies', 'thread_participants', 'audit_log')}

    def commit(self):
        import inspect
        frame = inspect.currentframe().f_back
        profile_sync = False
        roster_batch = False
        while frame is not None:
            if frame.f_code.co_name == '_sync_org_dependencies' and frame.f_code.co_filename.endswith('/runtime/workflows/profile_coordinator.py'):
                profile_sync = True
            if frame.f_code.co_name == 'reconcile_supported_roster_batch' and frame.f_code.co_filename.endswith('/runtime/workflows/profile_coordinator.py'):
                roster_batch = True
            frame = frame.f_back
        if profile_sync and roster_batch:
            rows = self.connection.execute("SELECT publisher FROM workflow_publication_journals WHERE namespace='org/alpha' ORDER BY rowid DESC LIMIT 1").fetchall()
            if rows and (rows[0][0] == self.owner or rows[0][0].startswith(self.owner + ':')):
                self.current_phase = 'profile_reconcile'
        selected = not self.cut and self.current_phase == self.phase
        if selected:
            assert self.connection.in_transaction
            self.cut = True
            if self.boundary == 'before_commit':
                self.prefix = self._snapshot()
                raise RuntimeError('selected genuine publication before commit')
        self.connection.commit()
        if selected:
            self.prefix = self._snapshot()
            raise RuntimeError('selected genuine publication after commit')

    def rollback(self):
        import inspect
        caller = inspect.currentframe().f_back
        owner = (caller.f_code.co_filename, caller.f_code.co_name)
        before = self.connection.in_transaction
        self.connection.rollback()
        self.owner_rollbacks.append((owner, before, self.connection.in_transaction))

    def __getattr__(self, name):
        return getattr(self.connection, name)


# This finite fixture still needs real external M inhibition plus exact U9 release.
from tests.integration.test_human_team_roster_e2e import maintenance_case


@pytest.mark.parametrize('phase,boundary,direction', PUBLICATION_CASES,
    ids=['-'.join(row) for row in PUBLICATION_CASES])
def test_c9_publication_commit_boundary(maintenance_case, monkeypatch, phase, boundary, direction):
    import argparse
    from scripts import migrate_human_team_roster as utility
    case = maintenance_case
    original_init = Database.__init__
    spies = []
    def construct(db, *args, **kwargs):
        original_init(db, *args, **kwargs)
        if db.path.resolve() == (case.root / 'happyranch.db').resolve():
            spy = _PublicationCommitCut(db._conn, db.path.resolve(), case.manifest['operation_id'], phase, boundary)
            spies.append(spy)
            db._conn = spy
    monkeypatch.setattr(Database, '__init__', construct)
    args = argparse.Namespace(runtime_root=case.runtime, org='alpha', expected_digest=case.digest,
        recover=False, direction=None, operation_id=None)
    try:
        utility.apply(args, case.manifest)
    except RuntimeError as exc:
        assert 'selected genuine publication' in str(exc), str(exc)
    finally:
        monkeypatch.setattr(Database, '__init__', original_init)
    assert len(spies) == 1 and spies[0].cut and spies[0].prefix is not None
    assert any(name == '_transaction' and filename.endswith(('/runtime/workflows/authority.py', '/runtime/workflows/profile_coordinator.py')) and not after
        for ((filename, name), before, after) in spies[0].owner_rollbacks), 'native transaction owner did not close selected commit failure'
    prefix = spies[0].prefix
    owned = [row for row in prefix['workflow_publication_journals'] if row[6] == spies[0].owner or row[6].startswith(spies[0].owner + ':')]
    if phase == 'fence' and boundary == 'before_commit':
        assert owned == []
        assert prefix['workflow_authority_pointers'][0][4] == 'ready'
    else:
        assert owned
        latest = owned[-1]
        expected_before = {'prepared': 'prepared', 'file_phase_reserved': 'prepared',
            'canonical_published': 'file_phase_reserved', 'pointer_committed': 'canonical_published',
            'cache_installed': 'pointer_committed', 'profile_reconcile': 'prepared'}
        expected_after = {'fence': 'prepared', 'prepared': 'prepared', 'file_phase_reserved': 'file_phase_reserved',
            'canonical_published': 'canonical_published', 'pointer_committed': 'pointer_committed',
            'cache_installed': 'cache_installed', 'profile_reconcile': 'prepared'}
        assert latest[9] == (expected_before[phase] if boundary == 'before_commit' else expected_after[phase])
        pointer = prefix['workflow_authority_pointers'][0]
        assert pointer[4] == ('ready' if phase == 'cache_installed' or phase == 'pointer_committed' and boundary == 'after_commit' else 'fenced')
    # Normal unwind can differ from the immediate selected prefix. Recover in
    # a NEW actual utility process without the test-side transaction wrapper.
    before_recovery = case.rows()
    recovered = case.run('--recover', '--manifest', str(case.manifest_path), '--expected-digest', case.digest,
        '--operation-id', case.manifest['operation_id'], '--direction', direction)
    assert recovered.returncode == 0, recovered.stderr
    case.preservation(direction)
    assert case.rows()['workflow_authority_pointers'][0][1] >= before_recovery['workflow_authority_pointers'][0][1]
    if direction == 'compensate':
        refused = case.run('--apply', '--manifest', str(case.manifest_path), '--expected-digest', case.digest)
        assert refused.returncode == 1 and 'fresh_operation_required_after_compensation' in refused.stderr


@pytest.mark.parametrize('row_shape', ['ordinary_rows', 'zero_rows', 'already_null'], ids=['ordinary_rows', 'zero_rows', 'already_null'])
def test_c9_replay_no_helpers(maintenance_case, row_shape):
    from scripts import migrate_human_team_roster as utility
    case = maintenance_case
    first, _, _ = case.observed(case.apply_arguments(), 'real-first-apply')
    assert first.returncode == 0, first.stderr
    case.preservation()
    primary = case.operation_dir / 'receipt.json'
    receipt_before = utility.image(primary)
    for number in range(2):
        before = utility.preservation_inventory(case.root)
        rows = case.rows()
        repeated, syscalls, frames = case.observed(case.apply_arguments(), 'same-manifest-replay-' + str(number))
        assert repeated.returncode == 0, repeated.stderr
        case.no_effects(syscalls, frames)
        assert case.rows() == rows and utility.preservation_inventory(case.root) == before
        assert utility.image(primary) == receipt_before
