"""Exact additive J definitions and K2a1's closed empty-coding boundary.

Populated coding readers belong to K2a2, before any producer. This module
neither installs a schema nor resolves repositories or current authority.
"""
from __future__ import annotations

import json
import re
import sqlite3

# Accepted TASK10693 step34: 3987 UTF8 bytes, SHA256
# 2c4b556671ef76582cdcbaed1e68d8575d9648ce29773f52c3d947ceaeeb0d70.
CANONICAL_WORKFLOW_CODING_DDL = """\
CREATE TABLE workflow_coding_mappings (
  id TEXT PRIMARY KEY,
  org_slug TEXT NOT NULL,
  release_identity TEXT NOT NULL CHECK(release_identity='release/happyranch/coding'),
  generation INTEGER NOT NULL CHECK(typeof(generation)='integer' AND generation>0),
  principal TEXT NOT NULL,
  operation_key TEXT NOT NULL,
  request_digest TEXT NOT NULL,
  mapping_bytes BLOB NOT NULL CHECK(typeof(mapping_bytes)='blob' AND length(mapping_bytes) BETWEEN 1 AND 1048576),
  mapping_digest TEXT NOT NULL UNIQUE,
  created_at TEXT NOT NULL,
  UNIQUE(org_slug,release_identity,generation),
  UNIQUE(org_slug,principal,operation_key)
);
CREATE TABLE workflow_coding_runs (
  instance_id TEXT PRIMARY KEY REFERENCES workflow_instances(id),
  mapping_id TEXT NOT NULL REFERENCES workflow_coding_mappings(id),
  cas_version INTEGER NOT NULL CHECK(typeof(cas_version)='integer' AND cas_version>0),
  current_revision INTEGER NOT NULL CHECK(typeof(current_revision)='integer' AND current_revision BETWEEN 0 AND 3),
  state TEXT NOT NULL CHECK(state IN ('development','code_review','qa','manager_closeout','held','complete','cancelled')),
  current_attempt_id TEXT REFERENCES workflow_coding_attempts(id),
  last_event_id TEXT REFERENCES workflow_coding_events(id),
  UNIQUE(instance_id,current_attempt_id)
);
CREATE TABLE workflow_coding_attempts (
  id TEXT PRIMARY KEY,
  instance_id TEXT NOT NULL REFERENCES workflow_coding_runs(instance_id),
  revision INTEGER NOT NULL CHECK(typeof(revision)='integer' AND revision BETWEEN 1 AND 3),
  stage TEXT NOT NULL CHECK(stage IN ('development','code_review','qa','manager_closeout')),
  attempt_sequence INTEGER NOT NULL CHECK(typeof(attempt_sequence)='integer' AND attempt_sequence>0),
  assignment_generation INTEGER NOT NULL CHECK(typeof(assignment_generation)='integer' AND assignment_generation>0),
  predecessor_id TEXT REFERENCES workflow_coding_attempts(id),
  task_id TEXT NOT NULL UNIQUE REFERENCES tasks(id),
  principal TEXT NOT NULL,
  session_id TEXT,
  result_id INTEGER UNIQUE REFERENCES task_results(id),
  submission_id TEXT REFERENCES workflow_submissions(id),
  review_request_id TEXT UNIQUE REFERENCES workflow_review_requests(id),
  state TEXT NOT NULL CHECK(state IN ('queued','claimed','running','uncertain','blocked','failed','completed','cancelled','superseded')),
  scope_bytes BLOB NOT NULL CHECK(typeof(scope_bytes)='blob' AND length(scope_bytes) BETWEEN 1 AND 1048576),
  scope_digest TEXT NOT NULL UNIQUE,
  UNIQUE(instance_id,revision,stage,attempt_sequence),
  UNIQUE(instance_id,stage,assignment_generation),
  CHECK(predecessor_id IS NULL OR predecessor_id!=id)
);
CREATE TABLE workflow_coding_events (
  id TEXT PRIMARY KEY,
  instance_id TEXT NOT NULL REFERENCES workflow_coding_runs(instance_id),
  event_seq INTEGER NOT NULL CHECK(typeof(event_seq)='integer' AND event_seq>0),
  attempt_id TEXT REFERENCES workflow_coding_attempts(id),
  org_slug TEXT NOT NULL,
  principal TEXT NOT NULL,
  operation_key TEXT NOT NULL,
  request_digest TEXT NOT NULL,
  event_kind TEXT NOT NULL CHECK(event_kind IN ('run_started','stage_queued','session_bound','launch_reserved','launch_observed','artifact_staged','review_staged','callback_recorded','terminal_recorded','advance','return','replacement','held','paused','resumed','cancelled','ci_observed','merge_reserved','merge_observed','contribution_recorded','host_reconciled','cleanup_settled','manager_completed','manager_control_queued','manager_control_session_bound','manager_control_launch_reserved','manager_control_launch_observed','manager_control_selected','manager_control_callback_recorded','manager_control_terminal_recorded','manager_control_cleanup_settled','manager_control_settled')),
  previous_digest TEXT,
  event_bytes BLOB NOT NULL CHECK(typeof(event_bytes)='blob' AND length(event_bytes) BETWEEN 1 AND 16777216),
  event_digest TEXT NOT NULL UNIQUE,
  created_at TEXT NOT NULL,
  UNIQUE(instance_id,event_seq),
  UNIQUE(org_slug,principal,operation_key)
);
"""

CODING_TABLES = (
    'workflow_coding_mappings', 'workflow_coding_runs',
    'workflow_coding_attempts', 'workflow_coding_events',
)


def _coding_value(value: object) -> bool:
    """Recognize reserved typed metadata, not mentions in document prose."""
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(item, str):
                if key == 'format' and item.startswith(('workflow-coding-', 'workflow-code-change@')):
                    return True
                if key in ('compiler_pin', 'validator_pin') and item.startswith('workflow-coding-'):
                    return True
                if key in ('kind', 'artifact_kind') and item == 'code-change':
                    return True
                if key == 'source_kind' and item == 'coding-manager-result':
                    return True
                if key == 'action' and item == 'workflow_start':
                    return True
            if _coding_value(item):
                return True
    elif isinstance(value, list):
        return any(_coding_value(item) for item in value)
    return False


def _refuse_coding_metadata(raw: object, *, location: str) -> None:
    if not isinstance(raw, (bytes, str)):
        return  # Existing retained-family readers check their required types.
    if len(raw) > 16777216:
        raise ValueError(f'workflow_coding_boundary_scan_bound: {location}')
    try:
        # Check every duplicate-key value too; malformed coding metadata must
        # not disappear behind JSON's usual last-key-wins interpretation.
        def pairs(items: list[tuple[str, object]]) -> dict:
            if any(_coding_value({key: value}) for key, value in items):
                raise ValueError(f'workflow_coding_data_unsupported: {location}; K2a2 reader required')
            return dict(items)
        value = json.loads(raw, object_pairs_hook=pairs)
    except (UnicodeError, json.JSONDecodeError):
        # Malformed reserved envelopes remain unsupported, including native
        # orphan payloads that have no retained document decoder to reject them.
        text = raw.decode('utf8', errors='replace') if isinstance(raw, bytes) else raw
        if re.search(r'"(?:format|compiler_pin|validator_pin)"\s*:\s*"workflow-(?:coding-|code-change@)', text):
            raise ValueError(f'workflow_coding_data_unsupported: {location}; malformed coding metadata') from None
        return  # This is a family discriminator, never a legacy validity PASS.
    except RecursionError as exc:
        raise ValueError(f'workflow_coding_boundary_scan_bound: {location}') from exc
    if _coding_value(value):
        raise ValueError(f'workflow_coding_data_unsupported: {location}; K2a2 reader required')


def validate_empty_coding_data(conn: sqlite3.Connection, *, layout: str) -> None:
    """No partial graph admission, even on validate_data=False or old layouts.

    Caller first matches the complete independent owned schema. Scan all
    structured workflow metadata plus native result decisions/audit metadata,
    so a coding-family orphan cannot be misclassified as legacy. Oversized
    metadata refuses; row traversal is complete, never a truncated prefix.
    Document content, briefs, findings and summaries are not discriminators.
    """
    if layout == 'J':
        for table in CODING_TABLES:
            if conn.execute(f'SELECT 1 FROM {table} LIMIT 1').fetchone() is not None:
                raise ValueError(f'workflow_coding_data_unsupported: {table}; K2a2 reader required')
    for table in ('workflow_template_drafts', 'workflow_template_versions', 'workflow_template_identities'):
        if conn.execute(f"SELECT 1 FROM {table} WHERE namespace='release/happyranch' LIMIT 1").fetchone():
            raise ValueError(f'workflow_coding_data_unsupported: {table}; release import reader required')
    for table in ('workflow_template_drafts', 'workflow_template_versions'):
        for compiler, validator in conn.execute(f'SELECT compiler_pin,validator_pin FROM {table}'):
            if any(isinstance(pin, str) and pin.startswith('workflow-coding-') for pin in (compiler, validator)):
                raise ValueError(f'workflow_coding_data_unsupported: {table}; coding pin reader required')
    if conn.execute("SELECT 1 FROM workflow_contexts WHERE source_kind='coding-manager-result' LIMIT 1").fetchone():
        raise ValueError('workflow_coding_data_unsupported: workflow_contexts; coding owner reader required')
    if conn.execute("SELECT 1 FROM audit_log WHERE action LIKE 'coding\\_%' ESCAPE '\\' LIMIT 1").fetchone():
        raise ValueError('workflow_coding_data_unsupported: audit_log; native coding reader required')
    tables = [row[0] for row in conn.execute(
        "SELECT name FROM sqlite_schema WHERE type='table' AND name LIKE 'workflow\\_%' ESCAPE '\\' ORDER BY name")]
    tables += ['tasks', 'task_results', 'audit_log']
    for table in tables:
        quoted = str(table).replace('"', '""')
        columns = [row[1] for row in conn.execute(f'PRAGMA table_xinfo("{quoted}")')
                   if (row[1].endswith(('_bytes', '_json')) or row[1] == 'payload')
                   and row[1] not in ('submission_bytes', 'candidate_bytes')]
        if not columns:
            continue
        selected = ','.join('"' + column.replace('"', '""') + '"' for column in columns)
        for row in conn.execute(f'SELECT {selected} FROM "{quoted}"'):
            for column, raw in zip(columns, row, strict=True):
                _refuse_coding_metadata(raw, location=f'{table}.{column}')
