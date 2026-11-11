"""Feature-owned additive Distill storage; no dispatch or live memory access.

The caller supplies its org Database connection and SAME RLock, as with the
existing schedule/work-hours stores. Native processor admission/completion is
S2b's responsibility; IDs below are nullable data, never execution authority.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator
from uuid import uuid4

from runtime.knowledge.curation import CurationError, build_curation_input, validate_curation_output
from runtime.knowledge.sources import RetiredSource

_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS knowledge_distill_operations (
        operation_id TEXT PRIMARY KEY,
        org_slug TEXT NOT NULL,
        submission_id TEXT NOT NULL,
        source_id TEXT NOT NULL,
        confirmation_json TEXT NOT NULL,
        capture_json TEXT NOT NULL,
        cutoff TEXT NOT NULL,
        state TEXT NOT NULL CHECK (state IN ('Queued','Processing','Ready','Failed')),
        phase TEXT NOT NULL,
        requested_processor_id TEXT,
        processor_id TEXT,
        task_id TEXT,
        session_id TEXT,
        result_id INTEGER,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        failure_json TEXT,
        UNIQUE (org_slug, submission_id),
        CHECK ((state = 'Failed') = (failure_json IS NOT NULL))
    )""",
    """CREATE INDEX IF NOT EXISTS idx_knowledge_distill_source
       ON knowledge_distill_operations(org_slug, source_id, created_at)""",
    """CREATE UNIQUE INDEX IF NOT EXISTS idx_knowledge_distill_inflight
       ON knowledge_distill_operations(org_slug, source_id)
       WHERE state IN ('Queued','Processing')""",
    """CREATE TABLE IF NOT EXISTS knowledge_archives (
        archive_id TEXT PRIMARY KEY,
        org_slug TEXT NOT NULL,
        source_id TEXT NOT NULL,
        version INTEGER NOT NULL CHECK (version > 0),
        operation_id TEXT NOT NULL UNIQUE REFERENCES knowledge_distill_operations(operation_id),
        manifest_json TEXT NOT NULL,
        items_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        UNIQUE (org_slug, source_id, version)
    )""",
    """CREATE INDEX IF NOT EXISTS idx_knowledge_archives_org
       ON knowledge_archives(org_slug, created_at, archive_id)""",
    # SQLite REPLACE need not fire DELETE triggers when recursive_triggers is
    # off. Refuse collisions BEFORE INSERT as well as ordinary update/delete.
    """CREATE TRIGGER IF NOT EXISTS knowledge_archives_no_replace
       BEFORE INSERT ON knowledge_archives WHEN EXISTS (
           SELECT 1 FROM knowledge_archives WHERE archive_id=NEW.archive_id
           OR operation_id=NEW.operation_id
           OR (org_slug=NEW.org_slug AND source_id=NEW.source_id AND version=NEW.version)
       ) BEGIN SELECT RAISE(ABORT, 'knowledge_archive_immutable'); END""",
    """CREATE TRIGGER IF NOT EXISTS knowledge_archives_no_update
       BEFORE UPDATE ON knowledge_archives BEGIN
       SELECT RAISE(ABORT, 'knowledge_archive_immutable'); END""",
    """CREATE TRIGGER IF NOT EXISTS knowledge_archives_no_delete
       BEFORE DELETE ON knowledge_archives BEGIN
       SELECT RAISE(ABORT, 'knowledge_archive_immutable'); END""",
    """CREATE TRIGGER IF NOT EXISTS knowledge_distill_terminal_no_update
       BEFORE UPDATE ON knowledge_distill_operations
       WHEN OLD.state IN ('Ready','Failed') BEGIN
       SELECT RAISE(ABORT, 'knowledge_distill_terminal'); END""",
    """CREATE TRIGGER IF NOT EXISTS knowledge_distill_no_delete
       BEFORE DELETE ON knowledge_distill_operations BEGIN
       SELECT RAISE(ABORT, 'knowledge_distill_history_immutable'); END""",
    """CREATE TRIGGER IF NOT EXISTS knowledge_distill_no_replace
       BEFORE INSERT ON knowledge_distill_operations WHEN EXISTS (
           SELECT 1 FROM knowledge_distill_operations WHERE operation_id=NEW.operation_id
           OR (org_slug=NEW.org_slug AND submission_id=NEW.submission_id)
       ) BEGIN SELECT RAISE(ABORT, 'knowledge_distill_history_immutable'); END""",
)


def install_archive_schema(conn: sqlite3.Connection) -> None:
    """Called after the fresh schema block; indexes follow their own tables.

    No implicit commits: bootstrap retains transaction ownership. Existing
    tables/columns and retained source bytes are never changed by this owner.
    """
    for statement in _SCHEMA:
        conn.execute(statement)


class ArchiveError(ValueError):
    """Closed storage failure code; never include captured bodies."""


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class KnowledgeArchiveStore:
    def __init__(self, conn: sqlite3.Connection, lock: threading.RLock, org_slug: str) -> None:
        if not org_slug.strip():
            raise ArchiveError('knowledge_org_missing')
        self._conn = conn
        self._lock = lock
        self._org = org_slug

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        with self._lock:
            if self._conn.in_transaction:
                raise ArchiveError('knowledge_caller_transaction_not_allowed')
            self._conn.execute('BEGIN IMMEDIATE')
            try:
                yield
                self._conn.commit()
            except BaseException:
                self._conn.rollback()
                raise

    def _operation(self, operation_id: str) -> dict:
        row = self._conn.execute(
            'SELECT * FROM knowledge_distill_operations WHERE org_slug=? AND operation_id=?',
            (self._org, operation_id),
        ).fetchone()
        if row is None:
            raise ArchiveError('knowledge_operation_not_found')
        return dict(row)

    def get_operation(self, operation_id: str) -> dict:
        with self._lock:
            return self._operation(operation_id)

    def find_submission(self, submission_id: str) -> dict | None:
        """Reopen before recapture: observation cutoff is fixed at first submit."""
        with self._lock:
            row = self._conn.execute(
                'SELECT operation_id FROM knowledge_distill_operations WHERE org_slug=? AND submission_id=?',
                (self._org, submission_id),
            ).fetchone()
            return self._operation(row['operation_id']) if row else None

    def list_inflight(self) -> list[dict]:
        with self._lock:
            return [dict(row) for row in self._conn.execute(
                "SELECT * FROM knowledge_distill_operations WHERE org_slug=? AND state IN ('Queued','Processing')",
                (self._org,),
            ).fetchall()]

    def select_processor(self, operation_id: str, processor_id: str) -> None:
        """Fix the resolved default before admission; retries cannot substitute."""
        with self._transaction():
            operation = self._operation(operation_id)
            if operation['state'] != 'Queued' or operation['processor_id'] not in (None, processor_id):
                raise ArchiveError('knowledge_illegal_transition')
            self._conn.execute(
                'UPDATE knowledge_distill_operations SET processor_id=?,updated_at=? WHERE org_slug=? AND operation_id=?',
                (processor_id, _now(), self._org, operation_id),
            )

    def bind_task_uncommitted(self, operation_id: str, processor_id: str, task_id: str) -> None:
        """Called only inside ordinary root submission's SAME transaction/lock."""
        if not self._conn.in_transaction:
            raise ArchiveError('knowledge_binding_transaction_required')
        operation = self._operation(operation_id)
        if operation['state'] != 'Queued' or operation['task_id'] is not None:
            raise ArchiveError('knowledge_illegal_transition')
        if operation['processor_id'] not in (None, processor_id):
            raise ArchiveError('knowledge_processor_mismatch')
        self._conn.execute(
            "UPDATE knowledge_distill_operations SET state='Processing',phase='awaiting_task',"
            'processor_id=?,task_id=?,updated_at=? WHERE org_slug=? AND operation_id=?',
            (processor_id, task_id, _now(), self._org, operation_id),
        )

    def observe(self, operation_id: str, phase: str, session_id: str | None) -> dict:
        """Persist an observed phase/generation, never manufacture progress."""
        with self._transaction():
            operation = self._operation(operation_id)
            if operation['state'] == 'Processing' and (operation['phase'], operation['session_id']) != (phase, session_id):
                self._conn.execute(
                    'UPDATE knowledge_distill_operations SET phase=?,session_id=?,updated_at=? '
                    'WHERE org_slug=? AND operation_id=?',
                    (phase, session_id, _now(), self._org, operation_id),
                )
            return self._operation(operation_id)

    def submit(self, submission_id: str, capture: RetiredSource, confirmation: dict,
               *, processor_id: str | None = None) -> dict:
        """Idempotent fixed submission; oversize/unavailable capture persists Failed.

        A different key cannot duplicate uncertain/in-flight work. Reusing a
        key with changed data refuses. Confirmed Failed retry uses a new key.
        """
        if not submission_id.strip() or not confirmation:
            raise ArchiveError('knowledge_confirmation_or_submission_missing')
        capture_json = _json(capture.model_dump(mode='json'))
        confirmation_json = _json(confirmation)
        failure = None
        try:
            build_curation_input(capture)
        except CurationError as error:
            failure = _json({'code': error.code, **error.counts})
        with self._transaction():
            existing = self._conn.execute(
                'SELECT operation_id FROM knowledge_distill_operations WHERE org_slug=? AND submission_id=?',
                (self._org, submission_id),
            ).fetchone()
            if existing is not None:
                operation = self._operation(existing['operation_id'])
                if (operation['capture_json'], operation['confirmation_json'], operation['requested_processor_id']) != (
                    capture_json, confirmation_json, processor_id,
                ):
                    raise ArchiveError('knowledge_submission_conflict')
                return operation
            inflight = self._conn.execute(
                "SELECT operation_id FROM knowledge_distill_operations WHERE org_slug=? AND source_id=? "
                "AND state IN ('Queued','Processing')", (self._org, capture.source_id),
            ).fetchone()
            if inflight is not None:
                raise ArchiveError('knowledge_source_inflight_reopen_operation')
            operation_id = 'distill-' + uuid4().hex
            now = _now()
            self._conn.execute(
                'INSERT INTO knowledge_distill_operations '
                '(operation_id,org_slug,submission_id,source_id,confirmation_json,capture_json,cutoff,'
                'state,phase,requested_processor_id,processor_id,created_at,updated_at,failure_json) '
                'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (operation_id, self._org, submission_id, capture.source_id, confirmation_json, capture_json,
                 capture.cutoff, 'Failed' if failure else 'Queued', 'input_failed' if failure else 'queued',
                 processor_id, processor_id, now, now, failure),
            )
            return self._operation(operation_id)

    def start(self, operation_id: str, *, processor_id: str | None = None,
              task_id: str | None = None, session_id: str | None = None) -> dict:
        """The only Queued -> Processing path; references are data for S2b."""
        with self._transaction():
            operation = self._operation(operation_id)
            if operation['state'] != 'Queued':
                raise ArchiveError('knowledge_illegal_transition')
            if (operation['processor_id'] is not None and processor_id is not None
                    and operation['processor_id'] != processor_id):
                raise ArchiveError('knowledge_processor_mismatch')
            self._conn.execute(
                "UPDATE knowledge_distill_operations SET state='Processing',phase='curation',"
                'processor_id=?,task_id=?,session_id=?,updated_at=? WHERE org_slug=? AND operation_id=?',
                (processor_id or operation['processor_id'], task_id, session_id, _now(), self._org, operation_id),
            )
            return self._operation(operation_id)

    def fail(self, operation_id: str, code: str, *, result_id: int | None = None) -> dict:
        """Record an actionable closed reason; never echo raw processing errors."""
        if not code or len(code) > 128 or any(c not in 'abcdefghijklmnopqrstuvwxyz0123456789_' for c in code):
            raise ArchiveError('knowledge_failure_code_invalid')
        self._result_reference(result_id)
        with self._transaction():
            operation = self._operation(operation_id)
            if operation['state'] not in ('Queued', 'Processing'):
                raise ArchiveError('knowledge_illegal_transition')
            self._fail_uncommitted(operation_id, {'code': code}, result_id)
            return self._operation(operation_id)

    def _fail_uncommitted(self, operation_id: str, cause: dict, result_id: int | None) -> None:
        self._conn.execute(
            "UPDATE knowledge_distill_operations SET state='Failed',phase='failed',failure_json=?,"
            'result_id=?,updated_at=? WHERE org_slug=? AND operation_id=?',
            (_json(cause), result_id, _now(), self._org, operation_id),
        )

    @staticmethod
    def _result_reference(result_id: int | None) -> None:
        if result_id is not None and (type(result_id) is not int or result_id < 1):
            raise ArchiveError('knowledge_result_reference_invalid')

    def publish(self, operation_id: str, raw_result: str, *, result_id: int | None = None) -> dict:
        """Single validated atomic Ready publication; invalid result commits Failed.

        No caller can supply a prevalidated object or arbitrary manifest. The
        stored fixed capture is validated again. INSERT + terminal UPDATE share
        one transaction, so failure cannot expose a partial/selectable archive.
        """
        self._result_reference(result_id)
        validation_error = None
        publication_error = False
        published = None
        with self._transaction():
            operation = self._operation(operation_id)
            if operation['state'] != 'Processing':
                raise ArchiveError('knowledge_illegal_transition')
            capture = RetiredSource.model_validate_json(operation['capture_json'])
            try:
                result = validate_curation_output(capture, raw_result)
            except CurationError as error:
                self._fail_uncommitted(operation_id, {'code': error.code, **error.counts}, result_id)
                validation_error = error
            else:
                archive_id = 'archive-' + uuid4().hex
                version = self._conn.execute(
                    'SELECT COALESCE(MAX(version),0)+1 FROM knowledge_archives WHERE org_slug=? AND source_id=?',
                    (self._org, capture.source_id),
                ).fetchone()[0]
                now = _now()
                manifest = {
                    'format': 'knowledge-archive@1', 'org_slug': self._org,
                    'archive_id': archive_id, 'version': version, 'operation_id': operation_id,
                    'source_id': capture.source_id, 'source_name': capture.name,
                    'source_team': capture.team, 'source_provider': capture.provider,
                    'cutoff': capture.cutoff, 'processed_at': now,
                    'processor_id': operation['processor_id'], 'task_id': operation['task_id'],
                    'session_id': operation['session_id'], 'result_id': result_id,
                    'capture_digest': result.capture_digest,
                    'explicitly_empty': result.explicitly_empty,
                    'coverage': [c.model_dump(mode='json') for c in capture.coverage],
                    'errors': capture.errors,
                    'dispositions': [d.model_dump(mode='json') for d in result.dispositions],
                    'category_counts': dict(Counter(i.category for i in result.items)),
                    'disposition_counts': dict(Counter(d.disposition for d in result.dispositions)),
                    'eligible_count': sum(i.instill_eligible for i in result.items),
                    'inventory': [{
                        'reference_id': r.reference_id, 'category': r.category,
                        'relative_path': r.relative_path, 'sha256': r.sha256,
                        # Original frontmatter can contain arbitrary uncurated
                        # fields. Retain only attribution/lifecycle pointers.
                        'metadata': {key: r.metadata.get(key) for key in (
                            'id', 'authored_by', 'authored_at', 'updated_at', 'last_verified',
                            'lifecycle', 'supersedes', 'promoted_to',
                        )},
                    } for r in capture.records],
                    'item_inventory': [i.item_id for i in result.items],
                }
                # No excluded bodies (including excluded metadata) in the Ready
                # manifest. The operation alone retains the authorized snapshot.
                excluded = {d.reference_id for d in result.dispositions if d.disposition == 'excluded'}
                for record in manifest['inventory']:
                    if record['reference_id'] in excluded:
                        record.pop('metadata')
                        record.pop('relative_path')
                self._conn.execute('SAVEPOINT knowledge_publish')
                try:
                    self._conn.execute(
                        'INSERT INTO knowledge_archives '
                        '(archive_id,org_slug,source_id,version,operation_id,manifest_json,items_json,created_at) '
                        'VALUES (?,?,?,?,?,?,?,?)',
                        (archive_id, self._org, capture.source_id, version, operation_id, _json(manifest),
                         _json([i.model_dump(mode='json') for i in result.items]), now),
                    )
                    self._conn.execute(
                        "UPDATE knowledge_distill_operations SET state='Ready',phase='published',result_id=?,updated_at=? "
                        'WHERE org_slug=? AND operation_id=?', (result_id, now, self._org, operation_id),
                    )
                    published = self._archive(archive_id)
                except sqlite3.Error:
                    # Only an acknowledged savepoint rollback establishes known
                    # no-publication and permits Failed. Rollback/COMMIT errors
                    # propagate with uncertainty; never advertise safe retry.
                    self._conn.execute('ROLLBACK TO knowledge_publish')
                    self._conn.execute('RELEASE knowledge_publish')
                    self._fail_uncommitted(operation_id, {'code': 'curation_publication_failed'}, result_id)
                    publication_error = True
                else:
                    self._conn.execute('RELEASE knowledge_publish')
        if validation_error is not None:
            raise validation_error
        if publication_error:
            raise ArchiveError('curation_publication_failed')
        assert published is not None
        return published

    def _archive(self, archive_id: str) -> dict:
        row = self._conn.execute(
            "SELECT a.* FROM knowledge_archives a JOIN knowledge_distill_operations o "
            "ON o.operation_id=a.operation_id AND o.org_slug=a.org_slug AND o.state='Ready' "
            'WHERE a.org_slug=? AND a.archive_id=?', (self._org, archive_id),
        ).fetchone()
        if row is None:
            raise ArchiveError('knowledge_ready_archive_not_found')
        archive = dict(row)
        archive['manifest'] = json.loads(archive.pop('manifest_json'))
        archive['items'] = json.loads(archive.pop('items_json'))
        return archive

    def get_archive(self, archive_id: str) -> dict:
        with self._lock:
            return self._archive(archive_id)

    def list_ready(self, *, source_id: str | None = None) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT a.archive_id FROM knowledge_archives a JOIN knowledge_distill_operations o "
                "ON o.operation_id=a.operation_id AND o.org_slug=a.org_slug AND o.state='Ready' "
                'WHERE a.org_slug=? AND (? IS NULL OR a.source_id=?) ORDER BY a.source_id,a.version',
                (self._org, source_id, source_id),
            ).fetchall()
            return [self._archive(row['archive_id']) for row in rows]
