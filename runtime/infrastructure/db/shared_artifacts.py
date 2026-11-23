"""Shared-artifact rows and caller-owned transactional primitives.

No filesystem or scheduling policy lives in this mixin. The service owns byte
publication; B/D use these same rows, never a second metadata store.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from typing import Iterator

from runtime.infrastructure.db._shared import _synchronized
from runtime.models import ThreadAttachment

SHARED_ARTIFACT_SCHEMA = {
    'shared_artifacts': '''name TEXT PRIMARY KEY, current_generation INTEGER NOT NULL,
        revision INTEGER NOT NULL DEFAULT 1''',
    'shared_artifact_generations': '''name TEXT NOT NULL, generation INTEGER NOT NULL,
        owner_kind TEXT, owner TEXT, owner_agent_id TEXT,
        origin_task_id TEXT, origin_thread_id TEXT, purpose TEXT,
        retention_class TEXT, reference_reason TEXT, created_at TEXT,
        renewed_at TEXT, expires_at TEXT, size_bytes INTEGER NOT NULL,
        sha256 TEXT NOT NULL, state TEXT NOT NULL,
        expired_on TEXT, recovery_started_at TEXT, recoverable_until TEXT,
        deleted_at TEXT, reason TEXT, hold_reason TEXT, classification_review_id TEXT,
        attribution_status TEXT, bound_at TEXT, timeout_at TEXT,
        continuing_use TEXT, requested_expires_at TEXT, system_derived INTEGER NOT NULL DEFAULT 0,
        protected_source INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY(name,generation)''',
    'shared_artifact_operations': '''operation_id TEXT PRIMARY KEY,
        name TEXT NOT NULL, generation INTEGER NOT NULL, expected_revision INTEGER,
        expected_hash TEXT, operation TEXT NOT NULL, phase TEXT NOT NULL,
        reserved_bytes INTEGER NOT NULL DEFAULT 0, before_state TEXT, after_state TEXT,
        filesystem_identity TEXT, request_digest TEXT, created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL, failure TEXT, metadata_json TEXT''',
    'shared_artifact_notice_receipts': '''name TEXT NOT NULL, generation INTEGER NOT NULL,
        expires_at TEXT NOT NULL, notice_kind TEXT NOT NULL,
        destination_thread_id TEXT, message_seq INTEGER, delivery_state TEXT NOT NULL,
        outbox_id TEXT, payload_json TEXT,
        PRIMARY KEY(name,generation,expires_at,notice_kind)''',
    'shared_artifact_org_state': '''id INTEGER PRIMARY KEY CHECK(id=1),
        quota_above INTEGER NOT NULL DEFAULT 0, crossing_sequence INTEGER NOT NULL DEFAULT 0,
        reconciliation_status TEXT NOT NULL DEFAULT 'incomplete',
        rollout_started_at TEXT, last_completed_window TEXT, preview_acceptance TEXT,
        inventory_review_id TEXT, source_protection_verified INTEGER NOT NULL DEFAULT 0''',
    'shared_artifact_inventory_reviews': '''review_id TEXT PRIMARY KEY,
        inventory_digest TEXT NOT NULL, reviewer_agent_id TEXT NOT NULL,
        reviewer_task_id TEXT NOT NULL, ruling_citation TEXT NOT NULL,
        decisions_json TEXT NOT NULL, reviewed_at TEXT NOT NULL''',
    'shared_artifact_thread_links': '''thread_id TEXT NOT NULL, message_seq INTEGER NOT NULL,
        ordinal INTEGER NOT NULL, name TEXT NOT NULL, generation INTEGER NOT NULL,
        PRIMARY KEY(thread_id,message_seq,ordinal)''',
}


def install_shared_artifact_schema(conn: sqlite3.Connection) -> None:
    """Add tables only; preflight existing layouts before any use or writes."""
    expected = sqlite3.connect(':memory:')
    try:
        for table, columns in SHARED_ARTIFACT_SCHEMA.items():
            expected.execute(f'CREATE TABLE {table} ({columns})')
            actual = conn.execute(f'PRAGMA table_info({table})').fetchall()
            wanted = expected.execute(f'PRAGMA table_info({table})').fetchall()
            if actual and [tuple(row) for row in actual] != wanted:
                raise RuntimeError(f'shared_artifact_schema_unsupported:{table}')
        for table, columns in SHARED_ARTIFACT_SCHEMA.items():
            conn.execute(f'CREATE TABLE IF NOT EXISTS {table} ({columns})')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_shared_artifact_expiry '
                     'ON shared_artifact_generations(state,expires_at,name,generation)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_shared_artifact_operation_phase '
                     'ON shared_artifact_operations(phase,name)')
        conn.execute('INSERT OR IGNORE INTO shared_artifact_org_state(id) VALUES(1)')
        for verb in ('UPDATE','DELETE'):
            conn.execute(f"CREATE TRIGGER IF NOT EXISTS shared_artifact_reviews_no_{verb.lower()} BEFORE {verb} ON shared_artifact_inventory_reviews BEGIN SELECT RAISE(ABORT,'shared artifact inventory reviews are immutable'); END")
    finally:
        expected.close()


class SharedArtifactsMixin:
    @contextmanager
    def shared_artifact_transaction(self) -> Iterator[sqlite3.Connection]:
        """Own one writer transaction; never silently join a foreign owner."""
        with self._lock:
            if self._conn.in_transaction:
                raise RuntimeError('shared_artifact_transaction_already_owned')
            self._conn.execute('BEGIN IMMEDIATE')
            try:
                yield self._conn
                self._conn.commit()
            except BaseException:
                self._conn.rollback()
                raise

    @_synchronized
    def get_shared_artifact(self, name: str, generation: int | None = None) -> dict | None:
        return self._shared_artifact_uncommitted(self._conn, name, generation)

    @staticmethod
    def _shared_artifact_uncommitted(
        conn: sqlite3.Connection, name: str, generation: int | None = None,
    ) -> dict | None:
        row = conn.execute(
            'SELECT g.*, a.revision FROM shared_artifact_generations g '
            'JOIN shared_artifacts a ON a.name=g.name '
            'WHERE g.name=? AND g.generation=COALESCE(?,a.current_generation)',
            (name, generation),
        ).fetchone()
        return dict(row) if row else None

    def shared_artifact_notice_uncommitted(
        self, *, name: str, generation: int, expires_at: str, notice_kind: str,
        destination_thread_id: str, payload: dict, agent: str = 'daemon',
    ) -> int:
        """B's atomic message/receipt/audit seam; caller owns BEGIN IMMEDIATE."""
        from runtime.models import ThreadMessageKind
        if not self._conn.in_transaction:
            raise RuntimeError('shared_artifact_notice_requires_transaction')
        if notice_kind == 'quota_alert':
            if name or expires_at:
                raise ValueError('quota_notice_uses_empty_name_and_expiry')
            return self.shared_artifact_quota_alert_uncommitted(
                crossing_sequence=generation, destination_thread_id=destination_thread_id,
                payloads=[payload], agent=agent,
            )[0]
        key = (name, generation, expires_at, notice_kind)
        prior = self._conn.execute(
            'SELECT message_seq,delivery_state FROM shared_artifact_notice_receipts '
            'WHERE name=? AND generation=? AND expires_at=? AND notice_kind=?', key,
        ).fetchone()
        if prior and prior['delivery_state'] == 'delivered':
            return prior['message_seq']
        # Intentionally tagless full JSON: existing SystemEvent renders it.
        if 'kind_tag' in payload or 'event' in payload:
            raise ValueError('shared_artifact_notice_requires_tagless_payload')
        seq = self._append_thread_message_uncommitted(
            thread_id=destination_thread_id, speaker='system',
            kind=ThreadMessageKind.SYSTEM, system_payload=payload,
        )
        self._conn.execute(
            'INSERT INTO shared_artifact_notice_receipts '
            '(name,generation,expires_at,notice_kind,destination_thread_id,message_seq,'
            'delivery_state,payload_json) VALUES(?,?,?,?,?,?,?,?) '
            'ON CONFLICT(name,generation,expires_at,notice_kind) DO UPDATE SET '
            'destination_thread_id=excluded.destination_thread_id,message_seq=excluded.message_seq,'
            'delivery_state=excluded.delivery_state,payload_json=excluded.payload_json',
            (*key, destination_thread_id, seq, 'delivered', json.dumps(payload)),
        )
        quota = notice_kind == 'quota_alert'
        if quota and (name or expires_at):
            raise ValueError('quota_notice_uses_empty_name_and_expiry')
        row = self._shared_artifact_uncommitted(self._conn,name,generation) if not quota else None
        common = {key:(row or {}).get(key) for key in (
            'owner_kind','owner','owner_agent_id','origin_task_id','origin_thread_id',
            'state','reason','size_bytes','expires_at',
        )}
        self.insert_audit_log_uncommitted(
            task_id='config:shared_artifacts' if quota else f'artifact:{name}', agent=agent,
            action='artifact_quota_alert' if quota else 'artifact_notice',
            payload={**common,**payload,'source':'system','name':name,'generation':generation,
                     'operation_id':payload.get('operation_id'),'before_state':common['state'],
                     'after_state':common['state'],'effective_mode':payload.get('effective_mode','preview'),
                     'expires_at':expires_at,'notice_kind':notice_kind,
                     'crossing_sequence':generation if quota else None,
                     'destination_thread_id':destination_thread_id,
                     'message_seq':seq,'outcome':'delivered'},
        )
        return seq

    def shared_artifact_quota_alert_uncommitted(
        self, *, crossing_sequence: int, destination_thread_id: str,
        payloads: list[dict], agent: str = 'daemon',
    ) -> list[int]:
        """Commit all bounded chunks with one receipt/audit in the caller's fence.

        The storage writer uses json.dumps with SQLite's length limit, without
        an application message cap. Use a conservative 32 KiB subset (including
        escaped JSON), reserving room for the rest of the SQLite message row.
        """
        from runtime.models import ThreadMessageKind
        if not self._conn.in_transaction:
            raise RuntimeError('shared_artifact_notice_requires_transaction')
        key = ('', crossing_sequence, '', 'quota_alert')
        prior = self._conn.execute(
            'SELECT message_seq,delivery_state,payload_json FROM shared_artifact_notice_receipts '
            'WHERE name=? AND generation=? AND expires_at=? AND notice_kind=?', key,
        ).fetchone()
        if prior and prior['delivery_state'] == 'delivered':
            # Pre-chunk receipts contain a single payload, still a valid replay.
            stored = json.loads(prior['payload_json'])
            return stored.get('message_seqs', [prior['message_seq']])
        if not payloads:
            raise ValueError('quota_notice_requires_payloads')
        chunks = [dict(payload, crossing_sequence=crossing_sequence,
                       chunk_index=index, chunk_count=len(payloads))
                  for index, payload in enumerate(payloads)]
        limit = min(32 * 1024, self._conn.getlimit(sqlite3.SQLITE_LIMIT_LENGTH) // 2)
        for payload in chunks:
            if 'kind_tag' in payload or 'event' in payload:
                raise ValueError('shared_artifact_notice_requires_tagless_payload')
            if len(json.dumps(payload).encode('utf-8')) > limit:
                raise ValueError('quota_notice_chunk_too_large')
        seqs = [self._append_thread_message_uncommitted(
            thread_id=destination_thread_id, speaker='system',
            kind=ThreadMessageKind.SYSTEM, system_payload=payload,
        ) for payload in chunks]
        self._conn.execute(
            'INSERT INTO shared_artifact_notice_receipts '
            '(name,generation,expires_at,notice_kind,destination_thread_id,message_seq,'
            'delivery_state,payload_json) VALUES(?,?,?,?,?,?,?,?) '
            'ON CONFLICT(name,generation,expires_at,notice_kind) DO UPDATE SET '
            'destination_thread_id=excluded.destination_thread_id,message_seq=excluded.message_seq,'
            'delivery_state=excluded.delivery_state,payload_json=excluded.payload_json',
            (*key, destination_thread_id, seqs[0], 'delivered', json.dumps({
                'chunk_count':len(chunks), 'message_seqs':seqs, 'payloads':chunks})),
        )
        common = dict.fromkeys(('owner_kind','owner','owner_agent_id','origin_task_id',
                                'origin_thread_id','state','reason','size_bytes','expires_at'))
        self.insert_audit_log_uncommitted(
            task_id='config:shared_artifacts', agent=agent, action='artifact_quota_alert',
            payload={**common, **chunks[0], 'source':'system','name':'',
                     'generation':crossing_sequence,'operation_id':chunks[0].get('operation_id'),
                     'before_state':None,'after_state':None,
                     'effective_mode':chunks[0].get('effective_mode','preview'),
                     'expires_at':'','notice_kind':'quota_alert',
                     'crossing_sequence':crossing_sequence,
                     'destination_thread_id':destination_thread_id,'message_seq':seqs[0],
                     'chunk_count':len(chunks),'message_seqs':seqs,'outcome':'delivered'},
        )
        return seqs

    def _append_thread_message_uncommitted(self, **kwargs: object) -> int:
        """Join every existing attachment writer's transaction without committing."""
        service = getattr(self, '_shared_artifact_service', None)
        links = []
        if service:
            for ordinal, attachment in enumerate(kwargs.get('attachments') or []):
                if attachment.artifact_name:
                    row = service.bind_uncommitted(self._conn,name=attachment.artifact_name,
                        generation=getattr(attachment,'generation',None),thread_id=kwargs['thread_id'])
                    if row.get('generation'):
                        links.append((ordinal,row['name'],row['generation']))
        seq = self._append_thread_message_storage_uncommitted(**kwargs)
        for ordinal,name,generation in links:
            self._conn.execute('INSERT INTO shared_artifact_thread_links(thread_id,message_seq,ordinal,name,generation) VALUES(?,?,?,?,?)',
                               (kwargs['thread_id'],seq,ordinal,name,generation))
        return seq

    def _attachments_for_messages(self, thread_id: str, seqs: list[int]) -> dict:
        """Historical projections retain original links even when unavailable."""
        values = super()._attachments_for_messages(thread_id,seqs)
        service = getattr(self,'_shared_artifact_service',None)
        if not service:
            return values
        for seq,attachments in values.items():
            for ordinal,attachment in enumerate(attachments):
                if not attachment.artifact_name:
                    continue
                link = self._conn.execute('SELECT generation FROM shared_artifact_thread_links WHERE thread_id=? AND message_seq=? AND ordinal=?',(thread_id,seq,ordinal)).fetchone()
                row = self._shared_artifact_uncommitted(self._conn,attachment.artifact_name,link['generation'] if link else None)
                if row:
                    attachments[ordinal] = SharedThreadAttachment(**attachment.model_dump(),
                        generation=link['generation'] if link else None,lifecycle=service.projection(row))
        return values


class SharedThreadAttachment(ThreadAttachment):
    generation: int | None = None
    lifecycle: dict | None = None
