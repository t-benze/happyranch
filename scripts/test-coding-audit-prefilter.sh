#!/usr/bin/env bash
set -euo pipefail
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"
uv run --frozen --python 3.14 python - <<'PY'
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
import json
import io
import logging
import re
import sqlite3
import sys
import tempfile
import time

from runtime.infrastructure import workflow_coding_schema as coding
from runtime.infrastructure.database import Database
from runtime.infrastructure.workflow_schema import install_or_recover
from runtime.workflows.coding_authority_history import _Budget
from runtime.workflows._coding_registered_release import registration_rows
from runtime.workflows.release_templates import read_coding_release

logging.getLogger(coding.__name__).setLevel(logging.WARNING)
COMPLETE = 'workflow_coding_data_unsupported: audit_log.payload; complete family reader required'
MALFORMED = 'workflow_coding_data_unsupported: audit_log.payload; malformed coding metadata'
BOUND = 'workflow_coding_boundary_scan_bound: audit_log.payload'
SCALAR = 'workflow_coding_data_unsupported: audit_log.action; coding reader required'
inventory = [
    ('format', 'workflow-coding-unknown@99'), ('format', 'workflow-coding@99'),
    ('format', 'workflow-code-change@99'), ('format', 'coding-scope@1'),
    ('format', 'coding-replacement-selection@1'),
    ('format', 'WORKFLOW-CODING-BUILTIN-REGISTRATION@99'),
    ('compiler_pin', 'workflow-coding-x'), ('validator_pin', 'workflow-coding-x'),
    ('source_pin', 'workflow-coding-x'), ('source_pin', 'coding-capability-issuer@1'),
    ('owner', 'coding'), ('registrar', 'BuIlTiN:CoDiNg-V99'),
    ('kind', 'code-change'), ('artifact_kind', 'code-change'),
    ('source_kind', 'coding-manager-result'),
    ('business_purpose', 'coding-replacement-selection@1'),
    ('identity', 'release/happyranch/coding'), ('release_identity', 'release/happyranch/coding'),
    ('namespace', 'release/happyranch'), ('namespace', 'org/scratch/coding-setup/1'),
    ('authority_namespace', 'org/scratch/coding-setup'),
    ('org_namespace', 'org/scratch/coding-setup'),
    ('action', 'CoDiNg_unknown'), ('action', 'workflow_start'),
]
cases = []
for key, value in inventory:
    member = json.dumps(key) + ':' + json.dumps(value)
    cases.extend([
        (key + ':' + value + ':complete', 'ordinary', '{' + member + '}', COMPLETE, True),
        (key + ':' + value + ':nested', 'ordinary', '[{"nested":{' + member + '}}]', COMPLETE, True),
        (key + ':' + value + ':duplicate-first', 'ordinary', '{' + member + ',' + json.dumps(key) + ':"document"}', COMPLETE, True),
        (key + ':' + value + ':duplicate-last', 'ordinary', '{' + json.dumps(key) + ':"document",' + member + '}', COMPLETE, True),
        (key + ':' + value + ':unclosed', 'ordinary', '{' + member, MALFORMED, True),
    ])
cases.extend([
    ('partial-format', 'ordinary', '{"format":"workflow-cod', MALFORMED, True),
    ('empty-partial', 'ordinary', '{"format":"', MALFORMED, True),
    ('partial-namespace', 'ordinary', '{"namespace":"org/scratch/cod', MALFORMED, True),
    ('invalid-escape-after-prefix', 'ordinary', '{"format":"workflow-cod\\q', MALFORMED, True),
    ('partial-escape-after-prefix', 'ordinary', '{"format":"workflow-cod\\u00', MALFORMED, True),
    ('escaped-key', 'ordinary', '{"fo\\u0072mat":"workflow-coding-x"}', COMPLETE, True),
    ('escaped-value', 'ordinary', '{"format":"\\u0077orkflow-coding-x"}', COMPLETE, True),
    ('all-escaped-members', 'ordinary', '{"\\u006b\\u0069\\u006e\\u0064":"\\u0063ode-change"}', COMPLETE, True),
    ('escaped-partial-key-value', 'ordinary', '{"\\u006b\\u0069\\u006e\\u0064":"\\u0063od', MALFORMED, True),
    ('ordinary-unicode-escape', 'ordinary', '{"ordinary":"\\u0061"}', None, True),
    ('utf8-blob', 'ordinary', b'{"format":"workflow-coding-x"}', COMPLETE, True),
    ('invalid-utf8-blob', 'ordinary', b'{"format":"workflow-coding-x"}\xff', MALFORMED, True),
    ('ordinary-blob', 'ordinary', b'{"ordinary":true}', None, True),
    ('null', 'ordinary', None, None, True),
    ('integer', 'ordinary', 123, None, True),
    ('real', 'ordinary', 1.25, None, True),
    ('unicode-text', 'ordinary', '{"ordinary":"你好"}', None, True),
    ('utf8-bom-text', 'ordinary', '\ufeff{"format":"workflow-cod', MALFORMED, True),
    ('nul-text', 'ordinary', '{"ordinary":true}\x00suffix', None, True),
    ('nul-hidden-coding', 'ordinary', '{"ordinary":true}\x00{"format":"workflow-cod', MALFORMED, True),
    ('deep-arrays', 'ordinary', '[' * 1500 + '0' + ']' * 1500, BOUND, True),
    ('visitor-depth-bound', 'ordinary', '[' * 600 + '0' + ']' * 600, BOUND, True),
    ('deep-objects', 'ordinary', '{"x":' * 1100 + '0' + '}' * 1100, BOUND, True),
    ('oversize', 'ordinary', 'x' * (16777216 + 1), BOUND, True),
    ('oversize-object', 'ordinary', '{"ordinary":"' + 'x' * 16777216 + '"}', BOUND, True),
    ('ordinary-long', 'ordinary', json.dumps({'ordinary': 'x' * 70000}), None, True),
    ('case-action', 'CoDiNg_unknown', '{"ordinary":true}', SCALAR, False),
    ('exact-action', 'workflow_start', '{}', SCALAR, False),
    ('blob-action', b'CoDiNg_unknown', '{}', SCALAR, False),
    ('null-action', None, '{}', None, True),
    ('ordinary', 'ordinary', '{"ordinary":true}', None, False),
    ('ordinary-duplicate', 'ordinary', '{"ordinary":1,"ordinary":2}', None, False),
    ('ordinary-malformed', 'ordinary', '{"ordinary":"unfinished', None, False),
    ('ordinary-prose', 'ordinary', 'workflow-coding-x is prose', None, False),
    ('quoted-prose', 'ordinary', json.dumps('{"format":"workflow-coding-x"}'), None, True),
    ('complete-near-miss', 'ordinary', '{"format":"workflow-cod"}', None, True),
    ('case-near-miss', 'ordinary', '{"format":"WORKFLOW-CODING-X"}', None, True),
    ('key-case-near-miss', 'ordinary', '{"Format":"workflow-coding-x"}', None, False),
    ('ordinary-flat-array', 'ordinary', '[1,true,null,"document"]', None, False),
    ('ordinary-nonfinite', 'ordinary', '[NaN,Infinity,-Infinity]', None, False),
    ('ordinary-nesting', 'ordinary', '{"nested":{"ordinary":true}}', None, True),
    ('invalid-utf8-text', 'ordinary', ('TEXT', b'{"ordinary":"\xff"}'), 'SQLITE_UTF8', True),
    ('invalid-utf8-action', ('TEXT', b'\xff'), '{}', 'SQLITE_UTF8', True),
])
if sys.get_int_max_str_digits():
    cases.append(('integer-digit-bound', 'ordinary', '1' * (sys.get_int_max_str_digits() + 1), 'INTEGER_DIGITS', True))
for encoding in ('utf-8-sig', 'utf-16', 'utf-16-le', 'utf-16-be', 'utf-32', 'utf-32-le', 'utf-32-be'):
    for suffix, raw, expected in (
        ('complete', '{"format":"workflow-coding-x"}', COMPLETE),
        ('partial', '{"format":"workflow-cod', MALFORMED),
        ('ordinary', '{"ordinary":true}', None),
    ):
        cases.append((encoding + '-' + suffix, 'ordinary', raw.encode(encoding), expected, True))
# Observe original TEXT decoding before all discrimination/candidate work.
cases.sort(key=lambda case: case[0] != 'invalid-utf8-text')


@contextmanager
def projection(enabled):
    # Disable only the SQL projection; retain the real scanner, row selection,
    # bounded acquisition, budget, snapshot and publication implementations.
    original = getattr(coding, '_coding_scan_projection', None)
    if original is not None and not enabled:
        coding._coding_scan_projection = lambda table, columns: ','.join('"' + col.replace('"', '""') + '"' for col in columns)
    try:
        yield
    finally:
        if original is not None:
            coding._coding_scan_projection = original


def insert(conn, action, payload):
    expr, params = [], []
    for value in (action, payload):
        text = isinstance(value, tuple)
        expr.append('CAST(? AS TEXT)' if text else '?')
        params.append(value[1] if text else value)
    columns = 'timestamp,agent,task_id,action,payload'
    conn.execute('INSERT INTO audit_log (' + columns + ') VALUES (\'2026-11-08T00:00:00Z\',\'synthetic\',\'TASK-1\',' + ','.join(expr) + ')', params)


def observe(conn, enabled, *, scoped):
    visits, advances = [], []
    budget = _Budget()
    original = coding._refuse_coding_metadata

    def spy(raw, *, location, budget):
        if location == 'audit_log.payload':
            visits.append((type(raw).__name__, raw))
        return original(raw, location=location, budget=budget)

    coding._refuse_coding_metadata = spy
    try:
        with projection(enabled):
            try:
                value = coding._read_coding_data(conn, layout='F', expected_org_slug='scratch' if scoped else None,
                                               budget=budget if scoped else None, advances=advances)
                outcome = ('accepted', False if value is None else asdict(value))
            except Exception as exc:
                outcome = (type(exc).__name__, str(exc))
    finally:
        coding._refuse_coding_metadata = original
    return (outcome, asdict(budget), advances), visits


def check_corpus(conn, *, scoped):
    counts = 0
    for name, action, raw, expected, fallback in cases:
        # Native complete storage forbids NULL action/payload; minimal earlier-
        # layout discrimination still covers those storage classes explicitly.
        if scoped and (action is None or raw is None):
            continue
        conn.execute('SAVEPOINT fixture')
        try:
            insert(conn, action, raw)
            baseline, _ = observe(conn, False, scoped=scoped)
            actual, visits = observe(conn, True, scoped=scoped)
            assert actual == baseline, (name, scoped, actual, baseline)
            if not scoped:
                outcome = actual[0]
                if expected is None:
                    assert outcome == ('accepted', False), (name, outcome)
                elif expected == 'SQLITE_UTF8':
                    assert outcome[0] == 'OperationalError' and 'UTF-8' in outcome[1], (name, outcome)
                elif expected == 'INTEGER_DIGITS':
                    assert outcome[0] == 'ValueError' and 'integer string conversion' in outcome[1], (name, outcome)
                else:
                    assert outcome == ('ValueError', expected), (name, outcome)
            if fallback and not isinstance(raw, tuple) and expected != 'SQLITE_UTF8':
                # The uncertain ordinary BLOB/NULL/numeric/Unicode cases must
                # still enter the existing scanner even when both outcomes accept.
                # TEXT affinity converts numeric inputs in complete native storage.
                observed = str(raw) if scoped and type(raw) in (int, float) else raw
                assert any(type(item) is type(observed) and item == observed for _, item in visits), (name, 'fallback bypassed')
            if name == 'ordinary':
                assert visits and all(item is None for _, item in visits), 'ordinary JSON was not prefiltered'
            counts += 1
        finally:
            conn.execute('ROLLBACK TO fixture')
            conn.execute('RELEASE fixture')
    print('PASS differential corpus', 'complete scoped coding' if scoped else 'earlier/unscoped', counts,
          'cases; exact exceptions, budget counters and collected watermarks')


with tempfile.TemporaryDirectory(prefix='coding-prefilter-') as scratch:
    conn = sqlite3.connect(':memory:')
    conn.execute('CREATE TABLE audit_log (id INTEGER PRIMARY KEY AUTOINCREMENT,timestamp,agent,task_id,action,payload)')
    check_corpus(conn, scoped=False)
    # Integer digit limits are connection-independent Python configuration.
    prior_limit = sys.get_int_max_str_digits()
    try:
        sys.set_int_max_str_digits(640)
        conn.execute('BEGIN')
        insert(conn, 'ordinary', '{"ordinary":' + '1' * 641 + '}')
        baseline, _ = observe(conn, False, scoped=False)
        actual, _ = observe(conn, True, scoped=False)
        assert actual == baseline and actual[0][0] == 'ValueError', (actual, baseline)
        conn.rollback()
    finally:
        sys.set_int_max_str_digits(prior_limit)
    print('PASS configured JSON integer-digit bound')
    conn.close()

    db = Database(Path(scratch) / 'complete.db')
    try:
        install_or_recover(db, expected_org_slug='scratch')
        check_corpus(db._conn, scoped=True)
        # A supported positive coding history, not merely the empty-family path.
        # These are the actual production fixture bytes/rows; the real complete
        # reader independently reconstructs and compares them in both modes.
        release = registration_rows('scratch', read_coding_release(), '2026-11-08T00:00:00.000000Z')
        for table, row in release.rows:
            db._conn.execute(f'INSERT INTO {table} ({",".join(row)}) VALUES ({",".join("?" for _ in row)})', tuple(row.values()))
        baseline, _ = observe(db._conn, False, scoped=True)
        actual, _ = observe(db._conn, True, scoped=True)
        assert actual == baseline and actual[0][0] == 'accepted' and actual[0][1]['release'] is not None
        print('PASS accepted built-in coding history: complete inventory and allowance equality')
        for field, value in (('consumed', 16777216), ('accepted_rows', 8192), ('traversed', 16384)):
            outcomes = []
            for enabled in (False, True):
                budget = _Budget(**{field: value})
                try:
                    with projection(enabled):
                        coding._read_coding_data(db._conn, layout='F', expected_org_slug='scratch', budget=budget)
                except ValueError as exc:
                    outcomes.append((type(exc).__name__, str(exc), asdict(budget)))
                else:
                    raise AssertionError((field, 'exhausted budget accepted'))
            assert outcomes[0] == outcomes[1], (field, outcomes)
        print('PASS shared byte/candidate/visit exhaustion')
    finally:
        db.close()

    for enabled in (False, True):
        conn = sqlite3.connect(Path(scratch) / ('watermark-' + str(enabled) + '.db'))
        try:
            conn.execute('CREATE TABLE audit_log (id INTEGER PRIMARY KEY AUTOINCREMENT,timestamp,agent,task_id,action,payload)')
            conn.execute('CREATE TABLE tasks (decision_json)')
            insert(conn, 'ordinary', '{"ordinary":true}')
            conn.commit()
            key = coding._audit_log_file_key(conn)
            with projection(enabled):
                coding.validate_coding_data(conn, layout='F')
                assert coding._AUDIT_LOG_WATERMARKS.get(key) == 1, 'committed prefix not published'
                insert(conn, 'ordinary', '{"ordinary":true}')
                advances = []
                coding._read_coding_data(conn, layout='F', expected_org_slug=None, advances=advances)
                assert advances == [(key, 2)] and coding._AUDIT_LOG_WATERMARKS[key] == 1
                conn.rollback()
                insert(conn, 'ordinary', '{"format":"workflow-cod')
                conn.commit()
                outcome, _ = observe(conn, enabled, scoped=False)
                assert outcome[0] == ('ValueError', MALFORMED)
                assert coding._AUDIT_LOG_WATERMARKS[key] == 1
                conn.execute('DELETE FROM audit_log WHERE id=2')
                insert(conn, 'ordinary', '{"ordinary":true}')
                conn.execute('INSERT INTO tasks VALUES (?)', ('{"format":"workflow-coding-x"}',))
                conn.commit()
                outcome, _ = observe(conn, enabled, scoped=False)
                assert outcome[0][0] == 'ValueError' and 'tasks.decision_json' in outcome[0][1]
                assert coding._AUDIT_LOG_WATERMARKS[key] == 1, 'late refusal published the audit prefix'
                conn.execute('DELETE FROM tasks')
                conn.commit()
                coding.validate_coding_data(conn, layout='F')
                assert coding._AUDIT_LOG_WATERMARKS[key] == 3
        finally:
            conn.close()
    print('PASS committed watermark, joined collector, rollback/reused rowid, refusal and late-failure publication')

    conn = sqlite3.connect(':memory:')
    messages = io.StringIO()
    handler = logging.StreamHandler(messages)
    logger = logging.getLogger(coding.__name__)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        conn.execute('CREATE TABLE audit_log (id INTEGER PRIMARY KEY AUTOINCREMENT,timestamp,agent,task_id,action,payload)')
        for fields in (1, 32):
            conn.execute('DELETE FROM audit_log')
            payload = json.dumps({f'field_{i}': 'x' * (900 // fields) for i in range(fields)})
            conn.executemany('INSERT INTO audit_log (action,payload) VALUES (?,?)', [('ordinary', payload)] * 20000)
            conn.commit()
            times = []
            for enabled in (False, True):
                start = time.perf_counter()
                with projection(enabled):
                    coding.validate_coding_data(conn, layout='F')
                times.append(time.perf_counter() - start)
            print(f'ISOLATED 20000 flat ordinary rows, {fields} fields ({len(payload) * 20000} payload bytes): before={times[0]:.6f}s after={times[1]:.6f}s')
        print('Synthetic local SQLite, baseline then candidate; not a serving-daemon restart or production-size estimate.')
        lines = messages.getvalue().splitlines()
        assert len(lines) == 4 and all(re.fullmatch(
            r'workflow_coding_audit_scan elapsed_seconds=\d+\.\d{6} observed_rows=20000 watermark=0', line
        ) for line in lines), ('additive scan diagnostic changed', lines)
        print('PASS successful scan diagnostic: elapsed_seconds, observed_rows and starting watermark')
    finally:
        logger.removeHandler(handler)
        logger.setLevel(logging.WARNING)
        conn.close()
PY
