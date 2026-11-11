"""Feature-owned Instill intents and receipts. Files and SQL are not atomic."""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from typing import Iterator
from uuid import uuid4

from runtime.knowledge.archives import _json, _now
from runtime.knowledge.lifecycle_models import ImportItemResult, ImportOperation, InstillConfirmation

_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS knowledge_import_operations (
        operation_id TEXT PRIMARY KEY,
        org_slug TEXT NOT NULL,
        submission_id TEXT NOT NULL,
        archive_id TEXT NOT NULL,
        archive_version INTEGER NOT NULL CHECK (archive_version > 0),
        source_id TEXT NOT NULL,
        recipient_id TEXT NOT NULL,
        confirmation_json TEXT NOT NULL,
        results_json TEXT NOT NULL,
        state TEXT NOT NULL CHECK (state IN ('Queued','Processing','Succeeded','Partially failed','Failed')),
        phase TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        started_at TEXT,
        finished_at TEXT,
        UNIQUE (org_slug, recipient_id, submission_id)
    )""",
    """CREATE UNIQUE INDEX IF NOT EXISTS idx_knowledge_import_inflight
       ON knowledge_import_operations(org_slug, recipient_id) WHERE state IN ('Queued','Processing')""",
    """CREATE INDEX IF NOT EXISTS idx_knowledge_import_recipient
       ON knowledge_import_operations(org_slug, recipient_id, created_at)""",
    """CREATE TABLE IF NOT EXISTS knowledge_import_items (
        org_slug TEXT NOT NULL,
        archive_id TEXT NOT NULL,
        archive_version INTEGER NOT NULL CHECK (archive_version > 0),
        item_id TEXT NOT NULL,
        recipient_id TEXT NOT NULL,
        operation_id TEXT NOT NULL REFERENCES knowledge_import_operations(operation_id),
        memory_id TEXT NOT NULL,
        expected_content TEXT NOT NULL,
        state TEXT NOT NULL CHECK (state IN ('intent','writing','unwritten','held','completed')),
        reason TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        completed_at TEXT,
        PRIMARY KEY (org_slug, archive_id, archive_version, item_id, recipient_id),
        UNIQUE (org_slug, recipient_id, memory_id)
    )""",
    """CREATE INDEX IF NOT EXISTS idx_knowledge_import_items_operation
       ON knowledge_import_items(org_slug, operation_id)""",
    """CREATE TRIGGER IF NOT EXISTS knowledge_import_confirmation_fixed
       BEFORE UPDATE OF org_slug,submission_id,archive_id,archive_version,source_id,recipient_id,confirmation_json
       ON knowledge_import_operations BEGIN SELECT RAISE(ABORT, 'knowledge_import_confirmation_fixed'); END""",
    """CREATE TRIGGER IF NOT EXISTS knowledge_import_intent_fixed
       BEFORE UPDATE OF org_slug,archive_id,archive_version,item_id,recipient_id,operation_id,memory_id,expected_content
       ON knowledge_import_items BEGIN SELECT RAISE(ABORT, 'knowledge_import_intent_fixed'); END""",
    """CREATE TRIGGER IF NOT EXISTS knowledge_import_receipt_fixed
       BEFORE UPDATE ON knowledge_import_items WHEN OLD.state='completed'
       BEGIN SELECT RAISE(ABORT, 'knowledge_import_receipt_fixed'); END""",
    """CREATE TRIGGER IF NOT EXISTS knowledge_import_items_no_replace
       BEFORE INSERT ON knowledge_import_items WHEN EXISTS (
           SELECT 1 FROM knowledge_import_items WHERE
           (org_slug=NEW.org_slug AND archive_id=NEW.archive_id AND archive_version=NEW.archive_version
            AND item_id=NEW.item_id AND recipient_id=NEW.recipient_id)
           OR (org_slug=NEW.org_slug AND recipient_id=NEW.recipient_id AND memory_id=NEW.memory_id))
       BEGIN SELECT RAISE(ABORT, 'knowledge_import_intent_fixed'); END""",
    """CREATE TRIGGER IF NOT EXISTS knowledge_import_items_no_delete
       BEFORE DELETE ON knowledge_import_items BEGIN SELECT RAISE(ABORT, 'knowledge_import_history_fixed'); END""",
)


def install_import_schema(conn: sqlite3.Connection) -> None:
    """Additive installer, after fresh executescript; each index follows its table."""
    for statement in _SCHEMA:
        conn.execute(statement)


class ImportError(ValueError):
    """Body-free feature refusal, with actual Busy references where available."""
    def __init__(self, code: str, blockers: list[dict[str, str]] | None = None) -> None:
        self.code = code
        self.blockers = blockers or []
        super().__init__(code)


class KnowledgeImportStore:
    def __init__(self, conn: sqlite3.Connection, lock: threading.RLock, org_slug: str) -> None:
        self.conn, self.lock, self.org_slug = conn, lock, org_slug

    @contextmanager
    def transaction(self) -> Iterator[None]:
        with self.lock:
            if self.conn.in_transaction:
                raise ImportError('knowledge_import_caller_transaction')
            self.conn.execute('BEGIN IMMEDIATE')
            try:
                yield
                self.conn.commit()
            except BaseException:
                self.conn.rollback()
                raise

    def get(self, operation_id: str) -> dict:
        with self.lock:
            row = self.conn.execute(
                'SELECT * FROM knowledge_import_operations WHERE org_slug=? AND operation_id=?',
                (self.org_slug, operation_id),
            ).fetchone()
            if row is None:
                raise ImportError('knowledge_import_not_found')
            return dict(row)

    def find(self, recipient_id: str, submission_id: str) -> dict | None:
        with self.lock:
            row = self.conn.execute(
                'SELECT * FROM knowledge_import_operations WHERE org_slug=? AND recipient_id=? AND submission_id=?',
                (self.org_slug, recipient_id, submission_id),
            ).fetchone()
            return dict(row) if row else None

    def list(self, recipient_id: str) -> list[dict]:
        with self.lock:
            return [dict(row) for row in self.conn.execute(
                'SELECT * FROM knowledge_import_operations WHERE org_slug=? AND recipient_id=? ORDER BY created_at,operation_id',
                (self.org_slug, recipient_id),
            ).fetchall()]

    def create(self, recipient_id: str, source_id: str, confirmation: InstillConfirmation) -> dict:
        with self.transaction():
            if self.conn.execute(
                "SELECT 1 FROM knowledge_import_operations WHERE org_slug=? AND recipient_id=? AND state IN ('Queued','Processing')",
                (self.org_slug, recipient_id),
            ).fetchone():
                raise ImportError('knowledge_import_inflight_reopen_operation')
            operation_id, now = 'instill-' + uuid4().hex, _now()
            results = [ImportItemResult(item_id=item_id, outcome='Pending').model_dump() for item_id in confirmation.item_ids]
            self.conn.execute(
                'INSERT INTO knowledge_import_operations VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (operation_id, self.org_slug, confirmation.submission_id, confirmation.archive_id,
                 confirmation.archive_version, source_id, recipient_id, _json(confirmation.model_dump()),
                 _json(results), 'Queued', 'confirmed', now, now, None, None),
            )
            return self.get(operation_id)

    def item(self, operation: dict, item_id: str) -> dict | None:
        with self.lock:
            row = self.conn.execute(
                'SELECT * FROM knowledge_import_items WHERE org_slug=? AND archive_id=? AND archive_version=? AND item_id=? AND recipient_id=?',
                (self.org_slug, operation['archive_id'], operation['archive_version'], item_id, operation['recipient_id']),
            ).fetchone()
            return dict(row) if row else None

    def reserve(self, operation: dict, item_id: str, memory_id: str, expected_content: str) -> dict:
        with self.transaction():
            now = _now()
            self.conn.execute('INSERT INTO knowledge_import_items VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                              (self.org_slug, operation['archive_id'], operation['archive_version'], item_id,
                               operation['recipient_id'], operation['operation_id'], memory_id, expected_content,
                               'intent', None, now, now, None))
            return self.item(operation, item_id)

    def next_reserved_id(self, recipient_id: str, file_next: str) -> str:
        with self.lock:
            ids = [r[0] for r in self.conn.execute(
                'SELECT memory_id FROM knowledge_import_items WHERE org_slug=? AND recipient_id=?',
                (self.org_slug, recipient_id),
            ).fetchall()]
            number = max([int(file_next.split('-')[1]) - 1, *[int(v.split('-')[1]) for v in ids]]) + 1
            return f'MEM-{number:03d}'

    def transition_item(self, operation: dict, item_id: str, state: str, reason: str | None = None) -> None:
        with self.transaction():
            row = self.item(operation, item_id)
            if row is None or row['state'] == 'completed' or row['operation_id'] != operation['operation_id']:
                raise ImportError('knowledge_import_illegal_item_transition')
            if state == 'completed':
                # Receipt and the writing operation's outcome are one COMMIT.
                # Recovery never infers Imported from the reservation owner.
                current = self.get(operation['operation_id'])
                results = [ImportItemResult.model_validate(r) for r in json.loads(current['results_json'])]
                result = next(r for r in results if r.item_id == item_id)
                result.outcome, result.memory_id, result.reason = 'Imported', row['memory_id'], reason
                result.held, result.retryable = False, False
                self.conn.execute(
                    'UPDATE knowledge_import_operations SET results_json=? WHERE org_slug=? AND operation_id=?',
                    (_json([r.model_dump() for r in results]), self.org_slug, operation['operation_id']),
                )
            self.conn.execute(
                'UPDATE knowledge_import_items SET state=?,reason=?,updated_at=?,completed_at=? '
                'WHERE org_slug=? AND archive_id=? AND archive_version=? AND item_id=? AND recipient_id=?',
                (state, reason, _now(), _now() if state == 'completed' else None, self.org_slug,
                 operation['archive_id'], operation['archive_version'], item_id, operation['recipient_id']),
            )

    def save_results(self, operation_id: str, results: list[ImportItemResult], *, terminal: bool) -> None:
        outcomes = [r.outcome for r in results]
        if terminal and 'Pending' in outcomes:
            raise ImportError('knowledge_import_incomplete_accounting')
        failed = outcomes.count('Failed')
        state = ('Partially failed' if failed and any(v in outcomes for v in ('Imported', 'Skipped'))
                 else 'Failed' if failed else 'Succeeded') if terminal else 'Processing'
        now = _now()
        with self.transaction():
            self.conn.execute(
                'UPDATE knowledge_import_operations SET results_json=?,state=?,phase=?,updated_at=?,started_at=COALESCE(started_at,?),finished_at=? '
                'WHERE org_slug=? AND operation_id=?',
                (_json([r.model_dump() for r in results]), state, 'finished' if terminal else 'writing', now, now,
                 now if terminal else None, self.org_slug, operation_id),
            )

    def projection(self, operation_id: str) -> ImportOperation:
        operation = self.get(operation_id)
        confirmation = InstillConfirmation.model_validate_json(operation['confirmation_json'])
        results = [ImportItemResult.model_validate(r) for r in json.loads(operation['results_json'])]
        if [r.item_id for r in results] != confirmation.item_ids:
            raise ImportError('knowledge_import_selection_accounting_mismatch')
        # A crash between file creation and operation accounting cannot turn an
        # unfinished SQL intent into a safe-retry or Succeeded claim.
        for result in results:
            row = self.item(operation, result.item_id)
            if row is not None and row['state'] in {'held', 'writing'}:
                result.outcome, result.held, result.retryable = 'Failed', True, False
                result.reason = ('uncertainty_hold_reconcile_reserved_id'
                                 if row['operation_id'] == operation_id else
                                 'knowledge_import_incomplete_intent_retry_original_operation')
                result.memory_id = row['memory_id']
        counts = {name: sum(r.outcome == label for r in results) for name, label in (
            ('imported', 'Imported'), ('skipped', 'Skipped'), ('failed', 'Failed'), ('pending', 'Pending'))}
        state = operation['state']
        if state == 'Succeeded' and (counts['failed'] or counts['pending']):
            state = 'Partially failed' if counts['imported'] + counts['skipped'] else 'Failed'
        return ImportOperation(**{k: operation[k] for k in (
            'operation_id', 'submission_id', 'archive_id', 'archive_version', 'source_id', 'recipient_id',
            'phase', 'created_at', 'updated_at', 'started_at', 'finished_at')},
            state=state, item_ids=confirmation.item_ids, selected=len(confirmation.item_ids), results=results, **counts)
