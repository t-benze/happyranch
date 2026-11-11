#!/usr/bin/env bash
set -euo pipefail
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"
uv run --frozen --python 3.14 python - <<'PYTHON'
from pathlib import Path
import gc
import sqlite3
import sys
import tempfile

from runtime.infrastructure import workflow_coding_schema as coding
from runtime.infrastructure.database import Database
from runtime.infrastructure.workflow_schema import install_or_recover
from runtime.workflows.coding_authority_history import _Budget
from runtime.workflows._coding_registered_release import registration_rows
from runtime.workflows.release_templates import read_coding_release


def retained_history(path):
    db = Database(path)
    try:
        install_or_recover(db, expected_org_slug='scratch')
        release = registration_rows('scratch', read_coding_release(), '2026-11-08T00:00:00.000000Z')
        for table, row in release.rows:
            db._conn.execute(f'INSERT INTO {table} ({",".join(row)}) VALUES ({",".join("?" for _ in row)})', tuple(row.values()))
        actual = coding._read_coding_data(db._conn, layout='F', expected_org_slug='scratch')
        assert actual is not None and actual.release is not None
        print('PASS retained accepted built-in coding history')
        for field, value in (('consumed', 16777216), ('accepted_rows', 8192), ('traversed', 16384)):
            try:
                coding._read_coding_data(db._conn, layout='F', expected_org_slug='scratch', budget=_Budget(**{field: value}))
            except ValueError:
                pass
            else:
                raise AssertionError((field, 'exhausted budget accepted'))
        print('PASS retained shared byte/candidate/visit exhaustion')
    finally:
        db.close()


def retained_refusal_finalization(path):
    db = Database(path)
    try:
        install_or_recover(db, expected_org_slug='scratch')
        db._conn.execute('INSERT INTO task_results (task_id,agent,session_id,created_at,decision_json) VALUES (?,?,?,?,?)',
                         ('TASK-1', 'synthetic', 'synthetic', '2026-11-08T00:00:00Z', '{"format":"workflow-coding-x"}'))
        db._conn.commit()
    finally:
        db.close()
    errors, retained = [], []
    original_hook = sys.unraisablehook
    gc.collect()
    conn = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)
    sys.unraisablehook = lambda error: errors.append((type(error.exc_value).__name__, str(error.exc_value)))
    try:
        try:
            coding._read_coding_data(conn, layout='F', expected_org_slug='scratch')
        except ValueError as exc:
            assert str(exc) == ('workflow_coding_data_unsupported: task_results.decision_json; complete family reader required')
            retained.append(exc)
        else:
            raise AssertionError('coding task result was not refused')
        finally:
            conn.close()
        retained.clear()
        gc.collect()
        assert not errors, errors
        print('PASS retained bounded task_results refusal closes scans before connection/traceback teardown')
    finally:
        retained.clear()
        gc.collect()
        sys.unraisablehook = original_hook


def audit_omission_and_non_audit_refusal():
    with sqlite3.connect(':memory:') as conn:
        conn.execute('CREATE TABLE audit_log (id INTEGER PRIMARY KEY AUTOINCREMENT,action,payload)')
        conn.execute('INSERT INTO audit_log (action,payload) VALUES (?,?)',
                     ('ordinary', '[' * 1500 + '0' + ']' * 1500))
        try:
            coding.validate_coding_data(conn, layout='F')
        except ValueError as exc:
            raise AssertionError('ordinary deep audit payload must no longer refuse') from exc
        conn.execute('INSERT INTO audit_log (action,payload) VALUES (?,?)',
                     ('coding_unknown', '{"format":"workflow-coding-unknown@99"}'))
        coding.validate_coding_data(conn, layout='F')
        for table in ('tasks', 'task_results'):
            conn.execute(f'CREATE TABLE {table} (decision_json)')
            conn.execute(f'INSERT INTO {table} VALUES (?)', ('{"format":"workflow-coding-unknown@99"}',))
            try:
                coding.validate_coding_data(conn, layout='F')
            except ValueError as exc:
                assert str(exc) == f'workflow_coding_data_unsupported: {table}.decision_json; complete family reader required'
            else:
                raise AssertionError(f'unknown non-audit coding row in {table} must still refuse')
            conn.execute(f'DROP TABLE {table}')
        print('PASS production seam: deep ordinary/coding audit accepted; unknown tasks/task_results coding refused')


with tempfile.TemporaryDirectory(prefix='coding-audit-removal-') as scratch:
    retained_history(Path(scratch) / 'history.db')
    retained_refusal_finalization(Path(scratch) / 'finalization.db')
    audit_omission_and_non_audit_refusal()
PYTHON
