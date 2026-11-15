#!/usr/bin/env bash
# Disposable SQLite checks, outside retired units/integration and required CI.
set -euo pipefail
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"
uv run --frozen --python 3.14 python - <<'PYTHON'
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from runtime.infrastructure.database import Database

STAMP = '2026-11-15T00:00:00+00:00'
DEAD = {'escalation_notifications', 'processed_event_ids'}
INDEX = 'idx_escalation_notifications_task'


@contextmanager
def opened(path: Path) -> Iterator[Database]:
    # The daemon's Database constructor, with no mocked migration or test hook.
    db = Database(path)
    try:
        yield db
    finally:
        db.close()


def quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def retained(conn: sqlite3.Connection, excluded: set[str] = DEAD) -> tuple:
    schema = tuple(tuple(row) for row in conn.execute(
        'SELECT type,name,tbl_name,sql FROM sqlite_schema ORDER BY type,name'
    ) if row[2] not in excluded)
    rows = {name: tuple(tuple(row) for row in conn.execute(
        f'SELECT * FROM {quote(name)} ORDER BY rowid'
    )) for (name,) in conn.execute("SELECT name FROM sqlite_schema WHERE type='table'")
        if name not in excluded}
    return schema, rows


def seed_notifications(conn: sqlite3.Connection, *, kind: bool = True) -> None:
    for table in DEAD:
        conn.execute(f'DROP TABLE IF EXISTS {table}')
    conn.execute('''CREATE TABLE escalation_notifications (
        feishu_message_id TEXT PRIMARY KEY, org_slug TEXT NOT NULL,
        task_id TEXT NOT NULL, chat_id TEXT NOT NULL, created_at TEXT NOT NULL,
        expires_at TEXT NOT NULL, consumed_at TEXT, consumed_by TEXT'''
        + (", kind TEXT NOT NULL DEFAULT 'escalation'" if kind else '') + ')')
    conn.execute(f'CREATE INDEX {INDEX} ON escalation_notifications(task_id)')
    conn.execute('''CREATE TABLE processed_event_ids (
        org_slug TEXT NOT NULL, feishu_event_id TEXT NOT NULL,
        processed_at TEXT NOT NULL, outcome TEXT NOT NULL, reason TEXT,
        PRIMARY KEY (org_slug, feishu_event_id))''')
    for notification_kind in ('escalation', 'failure', 'job_request'):
        for consumed in (False, True):
            ident = f'{notification_kind}-{consumed}'
            values = (ident, 'synthetic', 'TASK-sentinel', 'chat-synthetic', STAMP,
                      STAMP, STAMP if consumed else None, 'founder' if consumed else None)
            if kind:
                values += (notification_kind,)
            conn.execute('INSERT INTO escalation_notifications VALUES ('
                         + ','.join('?' for _ in values) + ')', values)
            conn.execute('INSERT INTO processed_event_ids VALUES (?,?,?,?,?)',
                         ('synthetic', ident, STAMP, 'consumed' if consumed else 'ignored', None))


def seed_retained(path: Path) -> None:
    with opened(path) as db:
        db._conn.execute('''INSERT INTO tasks(id,brief,created_at,updated_at)
                            VALUES ('TASK-sentinel','retained',?,?)''', (STAMP, STAMP))
        db._conn.execute('''INSERT INTO jobs(id,task_id,agent_name,title,script_text,
                            interpreter,status,created_at) VALUES
                            ('JOB-sentinel','TASK-sentinel','synthetic','retained',
                             'never executed','bash','completed',?)''', (STAMP,))
        db._conn.execute('''INSERT INTO audit_log(task_id,agent,action,payload,timestamp)
                            VALUES ('TASK-sentinel','synthetic','retained','{"v":1}',?)''', (STAMP,))
        db._conn.commit()


def seed_pre_jobs(path: Path, *, notifications: bool) -> None:
    with sqlite3.connect(path) as conn:
        # Independent script_requests shape from pre-jobs 4b73416a, not current
        # jobs renamed backwards. No executable script or native process runs.
        conn.executescript('''
            CREATE TABLE script_requests (
                id TEXT PRIMARY KEY, task_id TEXT NOT NULL, agent_name TEXT NOT NULL,
                title TEXT NOT NULL, rationale TEXT NOT NULL, script_text TEXT NOT NULL,
                interpreter TEXT NOT NULL, cwd_hint TEXT,
                status TEXT NOT NULL DEFAULT 'pending', exit_code INTEGER,
                stdout_head TEXT, stderr_head TEXT, stdout_path TEXT, stderr_path TEXT,
                duration_ms INTEGER, started_at TEXT, finished_at TEXT,
                reviewed_at TEXT, reviewed_by TEXT, reject_reason TEXT, cwd_resolved TEXT,
                timeout_seconds INTEGER NOT NULL DEFAULT 300, created_at TEXT NOT NULL);
            CREATE INDEX idx_script_requests_task ON script_requests(task_id);
            CREATE INDEX idx_script_requests_agent ON script_requests(agent_name);
            CREATE INDEX idx_script_requests_status ON script_requests(status);
            CREATE INDEX idx_script_requests_created_at ON script_requests(created_at);
            CREATE TABLE audit_log (id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL, agent TEXT NOT NULL, action TEXT NOT NULL,
                payload TEXT, timestamp TEXT NOT NULL);
            CREATE TABLE unrelated_sentinel (id TEXT PRIMARY KEY, value BLOB);
            INSERT INTO unrelated_sentinel VALUES ('retained', X'0001FF');
        ''')
        for number, status in enumerate(('pending', 'running', 'completed', 'rejected'), 1):
            conn.execute('''INSERT INTO script_requests
                (id,task_id,agent_name,title,rationale,script_text,interpreter,status,
                 stdout_path,stderr_path,started_at,created_at,timeout_seconds)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (f'SR-{number}', 'TASK-sentinel', 'synthetic', status, 'retained',
                 'never executed', 'bash', status, f'/synthetic/scripts/SR-{number}/stdout',
                 f'/synthetic/scripts/SR-{number}/stderr', STAMP if status == 'running' else None,
                 STAMP, 123))
            conn.execute('''INSERT INTO audit_log(task_id,agent,action,payload,timestamp)
                VALUES ('TASK-sentinel','synthetic','script_submitted',?,?)''',
                (f'{{"script_id":"SR-{number}","retained":"value"}}', STAMP))
        conn.execute('''INSERT INTO audit_log(task_id,agent,action,payload,timestamp)
            VALUES ('config:synthetic','synthetic','unrelated','{"v":1}',?)''', (STAMP,))
        if notifications:
            seed_notifications(conn)
            conn.execute("UPDATE escalation_notifications SET kind='script_request', task_id='SR-1'")


class FeishuTableRemoval(unittest.TestCase):
    def setUp(self) -> None:
        self.scratch = tempfile.TemporaryDirectory(prefix='feishu-table-removal-')
        self.addCleanup(self.scratch.cleanup)
        self.path = Path(self.scratch.name) / 'disposable.db'

    def assert_removed(self, conn: sqlite3.Connection) -> None:
        objects = {row[0] for row in conn.execute('SELECT name FROM sqlite_schema')}
        self.assertFalse((DEAD | {INDEX}) & objects)
        self.assertEqual([tuple(row) for row in conn.execute('PRAGMA integrity_check')], [('ok',)])
        self.assertEqual(conn.execute('PRAGMA foreign_key_check').fetchall(), [])

    def test_fresh(self) -> None:
        with opened(self.path) as db:
            self.assert_removed(db._conn)

    def check_legacy(self, *, kind: bool) -> None:
        seed_retained(self.path)
        with sqlite3.connect(self.path) as conn:
            seed_notifications(conn, kind=kind)
            before = retained(conn)
        for _ in range(2):
            with opened(self.path) as db:
                self.assert_removed(db._conn)
                self.assertEqual(retained(db._conn), before)

    def test_legacy_with_kind(self) -> None:
        self.check_legacy(kind=True)

    def test_legacy_without_kind(self) -> None:
        self.check_legacy(kind=False)

    def check_pre_jobs(self, *, notifications: bool) -> None:
        seed_pre_jobs(self.path, notifications=notifications)
        with sqlite3.connect(self.path) as conn:
            sentinel = retained(conn, DEAD | {'script_requests', 'audit_log', 'sqlite_sequence'})
        for attempt in range(2):
            with opened(self.path) as db:
                conn = db._conn
                self.assert_removed(conn)
                self.assertNotIn('script_requests', db.list_tables())
                columns = {row[1] for row in conn.execute('PRAGMA table_info(jobs)')}
                self.assertNotIn('timeout_seconds', columns)
                jobs = [tuple(row) for row in conn.execute('''SELECT id,task_id,status,reason,
                    finished_at,review_required,persistent,max_runtime_seconds,max_output_bytes,
                    stdout_path,stderr_path,title,rationale,script_text,interpreter
                    FROM jobs ORDER BY id''')]
                self.assertEqual(jobs, [
                    (f'JOB-{number}', 'TASK-sentinel', 'failed' if status == 'running' else status,
                     'daemon_crash' if status == 'running' else None,
                     STAMP if status == 'running' else None, 1, 0, 123, 52428800,
                     f'/synthetic/jobs/JOB-{number}/stdout', f'/synthetic/jobs/JOB-{number}/stderr',
                     status, 'retained', 'never executed', 'bash')
                    for number, status in enumerate(('pending', 'running', 'completed', 'rejected'), 1)
                ])
                self.assertEqual([tuple(row) for row in conn.execute(
                    'SELECT id,task_id,agent,action,payload,timestamp FROM audit_log ORDER BY id')], [
                    (number, 'TASK-sentinel', 'synthetic', 'job_submitted',
                     f'{{"job_id":"JOB-{number}","retained":"value"}}', STAMP)
                    for number in range(1, 5)
                ] + [(5, 'config:synthetic', 'synthetic', 'unrelated', '{"v":1}', STAMP)])
                after = retained(conn)
                self.assertTrue(all(row in after[0] for row in sentinel[0]))
                for table, rows in sentinel[1].items():
                    self.assertEqual(after[1][table], rows)
                if attempt == 0:
                    first = after
                else:
                    self.assertEqual(after, first)

    def test_pre_jobs_with_notifications(self) -> None:
        self.check_pre_jobs(notifications=True)

    def test_pre_jobs_without_notifications(self) -> None:
        self.check_pre_jobs(notifications=False)

    def test_already_migrated_reopen_twice(self) -> None:
        seed_retained(self.path)
        with sqlite3.connect(self.path) as conn:
            # A database on which the forward drop has already run.
            for table in DEAD:
                conn.execute(f'DROP TABLE IF EXISTS {table}')
            before = retained(conn)
        for _ in range(2):
            with opened(self.path) as db:
                self.assert_removed(db._conn)
                self.assertEqual(retained(db._conn), before)


unittest.main(verbosity=2)
PYTHON
