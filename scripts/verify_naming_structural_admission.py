#!/usr/bin/env python3
"""Finite N-SAC-1 synthetic verification. Never accepts a database or repair input.

Run from the selected source: python scripts/verify_naming_structural_admission.py
--case B-SA-01 --scratch NEW_CHILD --evidence NEW_CHILD. See fixture README.
No retired Python tests/helpers are imported. Literal JSON/SQL is data only.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import signal
import sqlite3
import sys
from types import SimpleNamespace

SOURCE = Path(__file__).resolve().parents[1]
ASSETS = SOURCE / 'scripts/fixtures/naming_structural_admission'
SLUG = 'synth-alpha'
STAMP = '2026-10-15T00:00:00+00:00'
RESULTS: list[dict] = []
SQL_TRACE: list[list[str]] = []
TRACE_BYTES = 0
TRACE_OVERFLOW = False
EVIDENCE: Path


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def save(name: str, value: object) -> None:
    (EVIDENCE / (name + '.json')).write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')


def typed(value: object) -> list:
    if isinstance(value, bytes):
        return ['blob', base64.b64encode(value).decode()]
    if isinstance(value, float):
        return ['float', value.hex()]
    return [type(value).__name__, value]


def qi(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def observe(org: object) -> dict:
    """OBS-1: independent complete objects/metadata/named typed rows/file identity."""
    conn = org.db._conn
    objects = {}
    tables = {}
    for schema in ('main', 'temp'):
        inventory = [tuple(r) for r in conn.execute(
            f'SELECT type,name,tbl_name,sql FROM {schema}.sqlite_schema ORDER BY type,name')]
        objects[schema] = inventory
        for kind, name, _, sql in inventory:
            if kind != 'table':
                continue
            columns = [tuple(r) for r in conn.execute(f'PRAGMA {schema}.table_xinfo({qi(name)})')]
            indexes = [tuple(r) for r in conn.execute(f'PRAGMA {schema}.index_list({qi(name)})')]
            column_names = sorted(r[1] for r in columns if r[6] == 0)
            select = ','.join(qi(c) for c in column_names)
            try:
                data = conn.execute(f'SELECT rowid,{select} FROM {schema}.{qi(name)}').fetchall()
                rowid = True
            except sqlite3.OperationalError:
                data = conn.execute(f'SELECT {select} FROM {schema}.{qi(name)}').fetchall()
                rowid = False
            tables[schema + '.' + name] = {
                'xinfo': columns, 'fk': [tuple(r) for r in conn.execute(f'PRAGMA {schema}.foreign_key_list({qi(name)})')],
                'indexes': indexes,
                'index_xinfo': {r[1]: [tuple(x) for x in conn.execute(f'PRAGMA {schema}.index_xinfo({qi(r[1])})')] for r in indexes},
                'columns': (['rowid'] if rowid else []) + column_names,
                'rows': sorted([[typed(v) for v in r] for r in data], key=lambda r: json.dumps(r)),
            }
    files = {}
    # DB/WAL physical bytes are deliberately excluded; logical DB is above.
    for directory in ('org', 'workspaces'):
        root = org.root / directory
        for path in sorted(root.rglob('*')) if root.exists() else ():
            st = path.lstat()
            files[str(path.relative_to(org.root))] = {
                'identity': [st.st_dev, st.st_ino, st.st_mode, st.st_size, st.st_mtime_ns, st.st_ctime_ns],
                'sha256': digest(path.read_bytes()) if path.is_file() and not path.is_symlink() else None,
                'link': os.readlink(path) if path.is_symlink() else None,
            }
    return {'objects': objects, 'tables': tables, 'files': files}


def unrelated(snapshot: dict) -> dict:
    return {'objects': {s: [r for r in rows if not r[1].startswith('identity_name_') and not r[2].startswith('identity_name_')] for s, rows in snapshot['objects'].items()},
            'tables': {k: v for k, v in snapshot['tables'].items() if not k.startswith('main.identity_name_')}, 'files': snapshot['files']}


def snapshot_row(snapshot: dict, table: str, **key: object) -> dict:
    """Select one complete typed row without consulting product readers."""
    data = snapshot['tables']['main.' + table]
    rows = [dict(zip(data['columns'], row, strict=True)) for row in data['rows']]
    selected = [row for row in rows if all(row[k] == typed(v) for k, v in key.items())]
    assert len(selected) == 1, (table, key, selected)
    return selected[0]


def replace_snapshot_row(snapshot: dict, table: str, row: dict, **changes: object) -> None:
    data = snapshot['tables']['main.' + table]
    original = [row[c] for c in data['columns']]
    index = data['rows'].index(original)
    updated = {**row, **{k: typed(v) for k, v in changes.items()}}
    data['rows'][index] = [updated[c] for c in data['columns']]
    data['rows'].sort(key=lambda r: json.dumps(r))


def append_snapshot_row(snapshot: dict, table: str, **values: object) -> None:
    data = snapshot['tables']['main.' + table]
    assert set(values) == set(data['columns']), (table, values)
    data['rows'].append([typed(values[c]) for c in data['columns']])
    data['rows'].sort(key=lambda r: json.dumps(r))


def assert_allowed_delta(name: str, before: dict, after: dict, expected: dict) -> None:
    """Compare every fact, retaining bounded real changed rows for handoff."""
    assert after == expected, (name, 'outside finite allowed delta')
    delta = {}
    for table, old in before['tables'].items():
        new = after['tables'][table]
        if old != new:
            delta[table] = {
                'columns': old['columns'],
                'removed_rows': [r for r in old['rows'] if r not in new['rows']],
                'added_rows': [r for r in new['rows'] if r not in old['rows']],
            }
    save(name + '-allowed-delta', {
        'before_sha256': digest(json.dumps(before, sort_keys=True).encode()),
        'after_sha256': digest(json.dumps(after, sort_keys=True).encode()),
        'expected_sha256': digest(json.dumps(expected, sort_keys=True).encode()),
        'changed_rows': delta, 'complete_snapshot_equal_to_expected': True,
        'objects_metadata_files_and_all_other_rows_exact': True,
    })


def assert_lifecycle_delta(before: dict, after: dict) -> None:
    expected = deepcopy(before)
    owner = snapshot_row(before, 'identity_name_owners', kind='agent', canonical_id='maker')
    assert [owner[k] for k in ('lifecycle', 'current_label', 'revision')] == [typed(v) for v in ('active', 'CurrentOne', 7)]
    # Frozen independent_names facts: terminal still reserves its ID. No claim
    # is added/removed/changed, including oldone and the absent gone owner.
    replace_snapshot_row(expected, 'identity_name_owners', owner, lifecycle='terminated', revision=8)
    assert_allowed_delta('terminal-lifecycle', before, after, expected)


def assert_rename_delta(before: dict, after: dict, *, ident: str, label: str,
                        revision: int, started: datetime, finished: datetime) -> None:
    # Only these three independently declared fixture transitions are exercised.
    old_label, old_permanent = {
        ('maker', 'CurrentOne', 1): ('maker', 0),
        ('maker', 'CurrentTwo', 2): ('CurrentOne', 1),
        ('founder', 'HumanOwner', 1): ('founder', 0),
    }[(ident, label, revision)]
    kind = 'founder' if ident == 'founder' else 'agent'
    expected = deepcopy(before)
    owner = snapshot_row(before, 'identity_name_owners', kind=kind, canonical_id=ident)
    assert [owner[k] for k in ('lifecycle', 'current_label', 'revision')] == [typed(v) for v in ('founder' if kind == 'founder' else 'active', old_label, revision)]
    replace_snapshot_row(expected, 'identity_name_owners', owner, current_label=label, revision=revision+1)
    claim = snapshot_row(before, 'identity_name_claims', normalized_name=old_label.lower())
    assert [claim[k] for k in ('owner_kind', 'owner_id', 'id_reserved', 'permanent')] == [typed(v) for v in (kind, ident, int(old_label == ident), old_permanent)]
    replace_snapshot_row(expected, 'identity_name_claims', claim, permanent=1)
    claims = before['tables']['main.identity_name_claims']
    assert all(r[claims['columns'].index('normalized_name')] != typed(label.lower()) for r in claims['rows'])
    claim_rowid = max(r[claims['columns'].index('rowid')][1] for r in claims['rows']) + 1
    append_snapshot_row(expected, 'identity_name_claims', rowid=claim_rowid,
                        normalized_name=label.lower(), owner_kind=kind, owner_id=ident,
                        id_reserved=0, permanent=1)
    sequence = snapshot_row(before, 'sqlite_sequence', name='audit_log')
    audit = before['tables']['main.audit_log']
    assert sequence['seq'] == typed(max(r[audit['columns'].index('id')][1] for r in audit['rows']))
    new_id = sequence['seq'][1] + 1
    replace_snapshot_row(expected, 'sqlite_sequence', sequence, seq=new_id)
    new_audit = snapshot_row(after, 'audit_log', id=new_id)
    timestamp = new_audit['timestamp']
    assert timestamp[0] == 'str'
    instant = datetime.fromisoformat(timestamp[1])
    assert instant.tzinfo == timezone.utc and instant.isoformat() == timestamp[1]
    assert started <= instant <= finished, (started, instant, finished)
    payload = {'org_slug': SLUG, 'kind': kind, 'canonical_id': ident,
               'old_label': old_label, 'new_label': label, 'old_revision': revision,
               'new_revision': revision+1, 'source': 'founder', 'actor': 'founder'}
    append_snapshot_row(expected, 'audit_log', rowid=new_id, id=new_id,
                        timestamp=timestamp[1], task_id='founder', agent='founder',
                        action='identity_name_changed', payload=json.dumps(payload, sort_keys=True))
    name = 'rename-' + ident + '-' + label
    assert_allowed_delta(name, before, after, expected)
    save(name + '-timestamp-bound', {'started': started.isoformat(), 'actual': timestamp[1], 'finished': finished.isoformat()})


def record(name: str, **details: object) -> None:
    RESULTS.append({'variant': name, **details})
    print(json.dumps(RESULTS[-1]), flush=True)


def frozen() -> tuple[dict, dict]:
    manifest = json.loads((ASSETS / 'manifest.json').read_text())
    for name, expected in manifest['assets'].items():
        assert digest((ASSETS / name).read_bytes()) == expected, name
    return manifest, json.loads((ASSETS / 'observed-eight.json').read_text())


def assert_eight(conn: sqlite3.Connection) -> None:
    manifest, _ = frozen()
    for table, hashes in manifest['ddl_hashes'].items():
        sql = conn.execute('SELECT sql FROM main.sqlite_schema WHERE name=?', (table,)).fetchone()[0]
        assert digest(sql.encode()) == hashes['live'], table


def canonical(root: Path) -> object:
    from runtime.orchestrator.agent_def import AgentDef, render_agent_text
    from runtime.orchestrator.teams import TeamManager, TeamsRegistry
    for name, lifecycle, role in [('manager', 'active', 'manager'), ('maker', 'active', 'worker'),
                                  ('pending_one', 'pending', 'worker'), ('retired_one', 'terminated', 'worker')]:
        directory = root / 'org/agents' / ({'active': '', 'pending': '_pending', 'terminated': '_terminated'}[lifecycle])
        directory.mkdir(parents=True, exist_ok=True)
        definition = AgentDef(name=name, team='engineering', role=role, executor='synthetic_unregistered', allow_rules=('happyranch',),
                              repos={}, enrolled_by=None, enrolled_at_task=None, enrolled_at=None, system_prompt='Synthetic inert definition.')
        (directory / (name + '.md')).write_text(render_agent_text(definition))
    teams = TeamsRegistry({'engineering': TeamManager('manager', 'engineering', ('maker', 'pending_one'))}, root=root)
    teams.save()
    (root / 'org/config.yaml').write_text('name: Synthetic\n')
    return teams


def fixture(base: Path, name: str, *, historical: bool = True, pause: bool = True) -> object:
    from runtime.infrastructure.database import Database
    from runtime.infrastructure.workflow_schema import install_or_recover
    from runtime.infrastructure.task_pause_controls import install_pause_schema
    root = base / name
    root.mkdir()
    teams = canonical(root)
    seed = root / 'seed.db'
    literals = json.loads((ASSETS / 'release_literals.json').read_text())
    with sqlite3.connect(seed) as c:
        for statement in literals['histories'][1]:
            c.execute(statement)
    with sqlite3.connect(seed) as seed_observer:
        save(name + '-S0-seed', observe(SimpleNamespace(root=root,db=SimpleNamespace(_conn=seed_observer))))
    seed_observer.close()
    db = Database(seed)
    db._conn.set_trace_callback(lambda sql: trace_setup(str(seed),sql))
    save(name + '-S1-constructor', observe(SimpleNamespace(root=root,db=db)))
    install_or_recover(db, expected_org_slug=SLUG)
    if pause:
        install_pause_schema(db, org_slug=SLUG)
    c = db._conn
    # Typed synthetic sentinels, never runnable/current sessions or receipts.
    for ident, parent in [('TASK-SYNTH-ROOT', None), ('TASK-SYNTH-CHILD', 'TASK-SYNTH-ROOT')]:
        c.execute('INSERT INTO tasks(id,status,brief,created_at,updated_at,parent_task_id) VALUES (?,\'completed\',\'inert\',?,?,?)', (ident, STAMP, STAMP, parent))
    c.execute('INSERT INTO threads(id,subject,started_at) VALUES (\'THR-SYNTH-1\',\'inert\',?)', (STAMP,))
    c.execute('INSERT INTO thread_messages(thread_id,seq,speaker,kind,body_markdown,created_at) VALUES (\'THR-SYNTH-1\',1,\'founder\',\'human\',\'inert\',?)', (STAMP,))
    c.execute('INSERT INTO thread_participants(thread_id,agent_name,added_at,added_by) VALUES (\'THR-SYNTH-1\',\'maker\',?,\'founder\')', (STAMP,))
    c.execute('INSERT INTO jobs(id,task_id,agent_name,title,script_text,interpreter,status,created_at) VALUES (\'JOB-SYNTH\',\'TASK-SYNTH-ROOT\',\'maker\',\'inert\',\'never executed\',\'bash\',\'completed\',?)', (STAMP,))
    c.execute('INSERT INTO session_token_usage(agent,session_id,executor,created_at,input_tokens) VALUES (\'maker\',\'synthetic-observation-only\',\'synthetic\',?,3)', (STAMP,))
    c.execute('INSERT INTO custom_skills(id,org_slug,slug,name,origin_kind,created_at,created_by) VALUES (\'SYNTH-SKILL\',?,\'inert\',\'Inert\',\'human\',?,\'founder\')', (SLUG, STAMP))
    c.execute('INSERT INTO audit_log(task_id,agent,action,payload,timestamp) VALUES (\'founder\',\'founder\',\'synthetic_sentinel\',\'{"v":1}\',?)', (STAMP,))
    c.commit()
    inventory = [tuple(r) for r in c.execute('SELECT type,name,tbl_name,sql FROM sqlite_schema ORDER BY rowid')]
    target = root / 'happyranch.db'
    dest = sqlite3.connect(target)
    dest.set_trace_callback(lambda sql: trace_setup(str(target),sql))
    _, obs = frozen()
    # New disposable DB only. No FK disabling at an operation seam.
    for kind, table, _, sql in inventory:
        if kind == 'table' and table != 'sqlite_sequence':
            dest.execute(obs['changed'].get(table, {}).get('live', sql) if historical else sql)
    for kind, table, _, _ in inventory:
        if kind != 'table':
            continue
        cols = [r[1] for r in c.execute(f'PRAGMA table_xinfo({qi(table)})') if r[6] == 0]
        names = ','.join(qi(n) for n in cols)
        rows = c.execute(f'SELECT rowid,{names} FROM {qi(table)}').fetchall()
        if table == 'sqlite_sequence':
            dest.execute('DELETE FROM sqlite_sequence')
        if rows:
            dest.executemany(f'INSERT INTO {qi(table)}(rowid,{names}) VALUES ({",".join("?" for _ in range(len(cols)+1))})', [tuple(r) for r in rows])
    for kind, _, _, sql in inventory:
        if kind in ('index', 'trigger', 'view') and sql:
            dest.execute(sql)
    dest.commit()
    dest.execute('PRAGMA foreign_keys=ON')
    assert dest.execute('PRAGMA integrity_check').fetchall() == [('ok',)]
    assert not dest.execute('PRAGMA foreign_key_check').fetchall()
    if historical:
        assert_eight(dest)
    # Compare reference and historical sibling: exactly eight raw SQL bodies.
    if historical:
        actual = {(r[0], r[1]): r[3] for r in dest.execute('SELECT type,name,tbl_name,sql FROM sqlite_schema')}
        expected = {(r[0], r[1]): r[3] for r in inventory}
        assert actual.keys() == expected.keys()
        assert {k for k in actual if actual[k] != expected[k]} == {('table', n) for n in obs['changed']}
    dest.close()
    db.close()
    db = Database(target)
    db._conn.set_trace_callback(lambda sql: trace_setup(str(target),sql))
    if historical:
        assert_eight(db._conn)
    org = SimpleNamespace(root=root, slug=SLUG, db=db, teams=teams, naming_ready=False, naming_diagnostic=None, naming_install_diagnostic=None)
    save(name + '-S1', observe(org))
    return org


def attempt(org: object, name: str, *, expected: str | None = None, install: bool = True) -> None:
    from runtime.identities.registry import capture_namespace, reconcile_namespace
    before = observe(org)
    save(name + '-before', before)
    trace = []
    org.db._conn.set_trace_callback(trace.append)
    error = None
    try:
        reconcile_namespace(org, capture_namespace(org.root, org.teams), install=install)
    except Exception as exc:
        error = getattr(exc, 'code', type(exc).__name__)
        import traceback
        save(name + '-exception', traceback.format_exc())
    finally:
        org.db._conn.set_trace_callback(None)
    after = observe(org)
    save(name + '-S2', after)
    save(name + '-trace', trace)
    if error:
        assert before == after, (name, 'refusal changed state')
    else:
        assert unrelated(before) == unrelated(after), (name, 'unrelated delta')
    record(name, actual=error or 'success', expected=expected or 'success', preservation=True)
    assert error == expected, (name, expected, error)


def case01(base: Path) -> None:
    from runtime.identities.registry import refresh_names
    org = fixture(base, 'h8-direct')
    attempt(org, 'h8-positive-contract')
    assert_eight(org.db._conn)
    owners = [tuple(r) for r in org.db._conn.execute('SELECT * FROM identity_name_owners ORDER BY canonical_id')]
    assert owners == [('founder', 'founder', 'founder', 'founder', 1), ('agent', 'maker', 'active', 'maker', 1),
                      ('agent', 'manager', 'active', 'manager', 1), ('agent', 'pending_one', 'pending', 'pending_one', 1), ('agent', 'retired_one', 'terminated', 'retired_one', 1)]
    assert org.db._conn.execute('SELECT count(*) FROM identity_name_claims').fetchone()[0] == 5
    before = observe(org)
    attempt(org, 'h8-idempotent')
    assert before == observe(org)
    org.db.close()
    org = fixture(base, 'h8-refresh')
    before = observe(org)
    assert refresh_names(org, install=True) and org.naming_readiness == 'ready' and org.naming_diagnostic is None
    assert unrelated(before) == unrelated(observe(org))
    org.db.close()


def setup_sql(org: object, statements: list[str]) -> None:
    for statement in statements:
        org.db._conn.execute(statement)
    org.db._conn.commit()


def independent_names(org: object) -> None:
    # Expected history comes from frozen SQL and declared cases, not candidate output.
    c = org.db._conn
    for statement in (ASSETS / 'naming-v1.sql').read_text().split(';'):
        if statement.strip():
            c.execute(statement)
    c.execute('INSERT INTO identity_name_schema VALUES (1,1,?)', (SLUG,))
    for kind, ident, life, label, rev in [('founder','founder','founder','founder',1), ('agent','manager','active','manager',1),
                                       ('agent','maker','active','CurrentOne',7), ('agent','pending_one','pending','pending_one',1),
                                       ('agent','retired_one','terminated','retired_one',1), ('agent','gone','absent','gonechosen',4)]:
        c.execute('INSERT INTO identity_name_owners VALUES (?,?,?,?,?)', (kind,ident,life,label,rev))
        c.execute('INSERT INTO identity_name_claims VALUES (?,?,?,?,?)', (ident,kind,ident,0 if life == 'absent' else 1,1 if life == 'absent' else 0))
        if label and label.lower() != ident and ident != 'gone':
            c.execute('INSERT INTO identity_name_claims VALUES (?,?,?,?,?)', (label.lower(),kind,ident,0,1))
    for token, ident in [('oldone','maker'), ('gonechosen','gone')]:
        c.execute('INSERT INTO identity_name_claims VALUES (?,?,?,?,?)', (token,'agent',ident,0,1))
    c.commit()


def rebuild(org: object, table: str, sql: str) -> None:
    """Fixture setup only, preserving rowids/named values/sequence/index order."""
    c = org.db._conn
    c.commit()
    indexes = [r[0] for r in c.execute('SELECT sql FROM sqlite_schema WHERE type=\'index\' AND tbl_name=? AND sql IS NOT NULL ORDER BY rowid', (table,))]
    cols = [r[1] for r in c.execute(f'PRAGMA table_xinfo({qi(table)})') if r[6] == 0]
    data = [tuple(r) for r in c.execute(f'SELECT rowid,{",".join(qi(n) for n in cols)} FROM {qi(table)}')]
    sequence = c.execute('SELECT seq FROM sqlite_sequence WHERE name=?', (table,)).fetchone()
    c.execute('PRAGMA foreign_keys=OFF')
    c.execute(f'DROP TABLE {qi(table)}')
    c.execute(sql)
    dest = {r[1].lower() for r in c.execute(f'PRAGMA table_xinfo({qi(table)})')}
    retained = [n for n in cols if n.lower() in dest]
    if data:
        c.executemany(f'INSERT INTO {qi(table)}(rowid,{",".join(qi(n) for n in retained)}) VALUES ({",".join("?" for _ in range(len(retained)+1))})',
                      [(r[0], *(r[cols.index(n)+1] for n in retained)) for r in data])
    for statement in indexes:
        c.execute(statement)
    if sequence:
        c.execute('UPDATE sqlite_sequence SET seq=? WHERE name=?', (sequence[0], table))
    c.commit()
    c.execute('PRAGMA foreign_keys=ON')


def reopen(org: object, name: str) -> object:
    from runtime.config import Settings
    from runtime.daemon.org_state import OrgState
    old = observe(org)
    org.db.close()
    loaded = OrgState.load(slug=SLUG, root=org.root, settings=Settings())
    after = observe(loaded)
    save(name + '-S3', after)
    # Full attach has separately owned publication/settings effects. Record every delta.
    save(name + '-attach-delta', {'changed_tables': [k for k in set(old['tables']) | set(after['tables']) if old['tables'].get(k) != after['tables'].get(k)],
                                 'files_changed': old['files'] != after['files']})
    for table in ('identity_name_schema', 'identity_name_owners', 'identity_name_claims'):
        if 'main.' + table in old['tables']:
            assert old['tables']['main.'+table] == after['tables']['main.'+table], table
    assert old['files'] == after['files']
    assert loaded.naming_readiness == 'ready', loaded.naming_diagnostic
    return loaded


def pause_journal_controls(base: Path) -> None:
    for variant in ('pause-valid-root-journal','pause-foreign-journal'):
        org = fixture(base,variant)
        entry = {'id':'synthetic-entry','org':SLUG if variant=='pause-valid-root-journal' else 'foreign-org',
                 'root_task_id':'TASK-SYNTH-ROOT','task_id':'TASK-SYNTH-CHILD','agent':'maker','session_id':'synthetic-history-only',
                 'owner':'ordinary','owner_identity':{},'fingerprint':{'status':'completed','block_kind':None,'count':0,'session_id':None,'agent':None,'fanout':None,'jobs':None,'parent':'TASK-SYNTH-ROOT','brief_digest':'0'*64},
                 'generation':0,'phase':'prepared','retry':{},'context':{},'started_at':None,'captured_generation':None}
        journal = json.dumps({'version':1,'entries':[entry]},sort_keys=True,separators=(',',':'))
        org.db._conn.execute("INSERT INTO task_pause_controls VALUES ('TASK-SYNTH-ROOT',1,0,0,NULL,NULL,?,'founder',NULL,?)",(STAMP,journal))
        org.db._conn.commit()
        attempt(org,variant,expected=None if variant=='pause-valid-root-journal' else 'naming_database_unavailable')
        org.db.close()


def dependency_history_controls(base: Path) -> None:
    from runtime.infrastructure.database import Database
    from runtime.infrastructure.workflow_schema import initialize_complete_org_schema, validate_workflow_schema
    from runtime.infrastructure.task_pause_controls import install_pause_schema
    from runtime.workflows.cutover import WorkflowCutoverStore
    # Actual owner writer produces an org-bound cutover event on an inert synthetic I DB.
    root = base / 'org-bound-workflow'
    root.mkdir()
    teams = canonical(root)
    db = Database(root / 'happyranch.db')
    initialize_complete_org_schema(db, expected_org_slug=SLUG, layout='I')
    install_pause_schema(db, org_slug=SLUG)
    WorkflowCutoverStore(db, org_slug=SLUG).request(action='enable', operation_key='synthetic-request', expected_generation=1)
    validate_workflow_schema(db._conn, expected_org_slug=SLUG)
    org = SimpleNamespace(root=root, slug='foreign-org', db=db, teams=teams)
    attempt(org, 'workflow-foreign-bound-history', expected='naming_database_unavailable')
    org.slug = SLUG
    attempt(org, 'workflow-bound-history-positive')
    db.close()
    # Stored WAITING data is a synthetic owner-validator fixture, not human proof,
    # and never feeds a move service/producer or live runtime.
    from runtime.infrastructure.team_move_store import TeamMoveStore, FounderAdmission, install_team_move_schema
    for variant in ('move-valid-waiting', 'move-foreign-row', 'move-bad-audit'):
        org = fixture(base, variant)
        install_team_move_schema(org.db, org_slug=SLUG)
        z = '0' * 64
        fields = ['canonical','roster','naming','profile','dependency','policy','skill_selection','permission']
        expected = {n+'_digest':z for n in fields}
        expected.update(authority_namespace='synthetic',authority_generation=1,authority_digest=z,agent_revisions=[z,z])
        identity = {'device':0,'inode':1,'mode':448}
        request = {'version':1,'org_slug':SLUG,'source_team':'consultant','target_team':'default','target_principal':'founder',
                   'affected_agents':['consultant_codex','consultant_head'],'demote':'consultant_head','retire_source':True,'drain_threads':False,
                   'expected':expected, 'files':[{'path':p,'identity':identity,'before_base64':'','before_digest':digest(b''),'after_base64':'','after_digest':digest(b'')} for p in
                                               ('org/agents/consultant_codex.md','org/agents/consultant_head.md','org/teams.yaml')],
                   'workspaces':[{'agent_id':a,'path':str(org.root / 'synthetic-workspaces' / a),'identity':identity} for a in ('consultant_codex','consultant_head')]}
        raw = json.dumps(request,sort_keys=True,separators=(',',':')).encode()
        TeamMoveStore(org.db,SLUG).reserve(operation_key='synthetic-only',request_bytes=raw,
            admission=FounderAdmission(principal='founder',boundary='_require_human',org_slug=SLUG,admission_id='synthetic-data-only',admitted_at=STAMP))
        if variant == 'move-foreign-row':
            org.slug = 'foreign-org'
        elif variant == 'move-bad-audit':
            setup_sql(org,["UPDATE audit_log SET payload='{}' WHERE action='team_move_reserved'"])
        attempt(org,variant,expected=None if variant=='move-valid-waiting' else 'naming_database_unavailable')
        org.db.close()


async def absent_public(org: object) -> None:
    from fastapi import FastAPI
    import httpx
    from runtime.config import Settings
    from runtime.daemon import paths
    from runtime.daemon.routes.identities import router
    from runtime.daemon.state import DaemonState
    from runtime.runtime import RuntimeDir
    app = FastAPI()
    runtime_root = org.root.parent / 'runtime-absent'
    runtime_root.mkdir()
    app.state.daemon = DaemonState(runtime=RuntimeDir(runtime_root), settings=Settings(), orgs={SLUG:org})
    app.include_router(router,prefix='/api/v1/orgs/{slug}')
    token = paths.ensure_token()
    before = observe(org)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://127.0.0.1') as client:
        unauthenticated = await client.get('/api/v1/orgs/'+SLUG+'/identities')
        assert unauthenticated.status_code == 401
        client.headers['Authorization'] = 'Bearer '+token
        response = await client.get('/api/v1/orgs/'+SLUG+'/identities')
        assert response.status_code == 200
        assert all(r['addressable_name'] is None and r['name_revision'] is None and r['naming_status']=='unavailable' for r in response.json()['identities'])
        response = await client.post('/api/v1/orgs/'+SLUG+'/identities/resolve',json={'addresses':['maker','Unchosen'],'context':'lookup'})
        assert response.status_code == 200
        rows=response.json()['resolutions']
        assert rows[0]['identity']['canonical_id']=='maker' and rows[1]['status']=='naming_unavailable'
    assert before==observe(org)
    record('absent-public',actual='real auth denial and null metadata/read-only; permanent ID usable')



def case02(base: Path) -> None:
    from runtime.infrastructure.database import Database
    from runtime.infrastructure.workflow_schema import initialize_complete_org_schema
    from runtime.identities.registry import refresh_names, read_name_metadata
    for name in ('generic.db', 'runtime-audit.db'):
        db = Database(base / name)
        assert not db._conn.execute("SELECT name FROM sqlite_schema WHERE name LIKE 'identity_name_%' OR name LIKE 'workflow_%'").fetchall()
        db.close()
        record(name, actual='no naming/workflow on generic open')
    org = fixture(base, 'fresh-f', historical=False)
    before = observe(org)
    read_name_metadata(org)
    assert not refresh_names(org) and org.naming_diagnostic == 'naming_not_installed'
    assert before == observe(org)
    org = reopen(org, 'fresh-f-first')
    org = reopen(org, 'fresh-f-repeat')
    org.db.close()
    root = base / 'fresh-i'
    root.mkdir()
    teams = canonical(root)
    db = Database(root / 'happyranch.db')
    initialize_complete_org_schema(db, expected_org_slug=SLUG, layout='I')
    org = SimpleNamespace(root=root, slug=SLUG, teams=teams, db=db)
    org = reopen(org, 'fresh-i-first')
    org = reopen(org, 'fresh-i-repeat')
    org.db.close()
    from runtime.daemon.org_state import OrgState
    from runtime.config import Settings
    org = fixture(base, 'absent-public')
    setup_sql(org, ['ALTER TABLE audit_log ADD COLUMN extra TEXT'])
    org.db.close()
    org = OrgState.load(slug=SLUG,root=org.root,settings=Settings())
    assert org.naming_readiness == 'unavailable'
    asyncio.run(absent_public(org))
    org.db.close()
    org = fixture(base, 'chosen-history')
    independent_names(org)
    initial = observe(org)
    attempt(org, 'chosen-history-install')
    assert initial == observe(org)
    org = reopen(org, 'chosen-history-reopen1')
    org = reopen(org, 'chosen-history-reopen2')
    org.db.close()
    record('fresh-complete-attach', actual='F/I/two reopens and chosen history preserved')


def case03(base: Path) -> None:
    org = fixture(base, 'harmless')
    # A ninth independent unrelated formatting/order variation.
    rebuild(org, 'jobs', org.db._conn.execute("SELECT sql FROM sqlite_schema WHERE name='jobs'").fetchone()[0].replace('title                    TEXT NOT NULL,', 'title TEXT NOT NULL,'))
    setup_sql(org, ['CREATE TABLE synth_extra (id INTEGER PRIMARY KEY, value BLOB, n INTEGER)',
                    "INSERT INTO synth_extra VALUES (9,x'00ff',1)",
                    'CREATE INDEX synth_extra_idx ON synth_extra(n)',
                    'CREATE TRIGGER synth_extra_update AFTER UPDATE OF n ON synth_extra BEGIN UPDATE synth_extra SET value=value WHERE id=NEW.id; END',
                    'CREATE VIEW synth_extra_view AS SELECT n FROM synth_extra'])
    attempt(org, 'harmless-objects')
    org.db.close()
    variants = {
        'external-trigger': 'CREATE TRIGGER synth_hazard AFTER UPDATE ON tasks BEGIN DELETE FROM identity_name_owners; END',
        'external-view': 'CREATE VIEW synth_hazard AS SELECT * FROM identity_name_owners',
        'incoming-fk': 'CREATE TABLE synth_hazard (x TEXT REFERENCES "IDENTITY_NAME_OWNERS"(canonical_id))',
        'temp-shadow': 'CREATE TEMP TABLE tasks (id TEXT PRIMARY KEY,parent_task_id TEXT)',
        'attached': "ATTACH DATABASE ':memory:' AS synth_other",
        'reserved-prefix': 'CREATE TABLE identity_name_future (x TEXT)',
        'comment-reference': "CREATE VIEW synth_hazard AS SELECT 'identity_name_owners' AS harmless",
        'audit-trigger': 'CREATE TRIGGER synth_hazard BEFORE INSERT ON audit_log BEGIN SELECT RAISE(IGNORE); END',
    }
    for name, statement in variants.items():
        org = fixture(base, name)
        setup_sql(org, [statement])
        attempt(org, name, expected='naming_database_unavailable')
        org.db.close()


def case04(base: Path) -> None:
    variants = ['partial', 'missing-marker', 'foreign-marker', 'duplicate-marker', 'newer-marker', 'extra-index', 'bad-revision', 'claim-corrupt', 'naming-trigger']
    for name in variants:
        org = fixture(base, name)
        c = org.db._conn
        independent_names(org)
        if name == 'partial':
            setup_sql(org, ['DROP TABLE identity_name_claims'])
        elif name == 'missing-marker':
            setup_sql(org, ['DELETE FROM identity_name_schema'])
        elif name == 'foreign-marker':
            setup_sql(org, ["UPDATE identity_name_schema SET org_slug='foreign'"])
        elif name == 'duplicate-marker':
            setup_sql(org, ['DROP TABLE identity_name_schema', 'CREATE TABLE identity_name_schema(singleton INTEGER,version INTEGER,org_slug TEXT)', "INSERT INTO identity_name_schema VALUES (1,1,'synth-alpha'),(1,1,'synth-alpha')"])
        elif name == 'newer-marker':
            setup_sql(org, ['DELETE FROM identity_name_schema'])
            rebuild(org, 'identity_name_schema', c.execute("SELECT sql FROM sqlite_schema WHERE name='identity_name_schema'").fetchone()[0].replace('CHECK(version=1)', 'CHECK(version=2)'))
            setup_sql(org, ["INSERT INTO identity_name_schema VALUES (1,2,'synth-alpha')"])
        elif name == 'extra-index':
            setup_sql(org, ['CREATE INDEX identity_name_extra ON identity_name_owners(current_label)'])
        elif name == 'bad-revision':
            c.execute('PRAGMA ignore_check_constraints=ON')
            setup_sql(org, ["UPDATE identity_name_owners SET revision=0 WHERE canonical_id='maker'"])
            c.execute('PRAGMA ignore_check_constraints=OFF')
        elif name == 'claim-corrupt':
            setup_sql(org, ["DELETE FROM identity_name_claims WHERE normalized_name='currentone'"])
        elif name == 'naming-trigger':
            setup_sql(org, ['CREATE TRIGGER identity_name_trig AFTER UPDATE ON identity_name_owners BEGIN SELECT 1; END'])
        # These are old correct refusal controls, not proof of new guards.
        before = observe(org)
        from runtime.identities.registry import capture_namespace, reconcile_namespace
        try:
            reconcile_namespace(org, capture_namespace(org.root, org.teams), install=True)
        except Exception as exc:
            assert before == observe(org)
            record(name, actual=getattr(exc, 'code', type(exc).__name__), attribution='retained validator', preservation=True)
        else:
            raise AssertionError(name + ' admitted')
        org.db.close()


def case05(base: Path) -> None:
    from runtime.infrastructure.team_move_store import TEAM_MOVE_SCHEMA_SQL
    from runtime.identities.registry import capture_namespace, reconcile_namespace
    from runtime.identities.admission import validate_naming_install_admission
    from runtime.identities.schema import transaction, NamingError
    for name, pause, move in [('optional-absent', False, False), ('complete-empty-move', True, True)]:
        org = fixture(base, name, pause=pause)
        if move:
            setup_sql(org, [TEAM_MOVE_SCHEMA_SQL])
        attempt(org, name)
        org.db.close()
    audit = 'CREATE TABLE audit_log (id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL, agent TEXT NOT NULL, action TEXT NOT NULL, payload TEXT, timestamp TEXT NOT NULL)'
    variants = {
        'audit-shipping': audit,
        'audit-reordered': 'CREATE TABLE "audit_log" ("timestamp" TEXT NOT NULL,"payload" TEXT,"action" TEXT NOT NULL,"id" INTEGER PRIMARY KEY AUTOINCREMENT,"agent" TEXT NOT NULL,"task_id" TEXT NOT NULL)',
        'audit-case-variation': audit.upper(),
        'audit-seventh': audit[:-1] + ',extra TEXT)',
        'audit-null-task': audit.replace('task_id TEXT NOT NULL','task_id TEXT'),
        'audit-required-payload': audit.replace('payload TEXT,','payload TEXT NOT NULL,'),
        'audit-no-autoincrement': audit.replace(' AUTOINCREMENT',''),
        'audit-check': audit.replace('payload TEXT,', 'payload TEXT CHECK(length(payload)>0),'),
    }
    for name, sql in variants.items():
        org = fixture(base, name)
        rebuild(org, 'audit_log', sql)
        attempt(org, name, expected=None if name in ('audit-shipping','audit-reordered','audit-case-variation') else 'naming_database_unavailable')
        org.db.close()
    hazards = {
        'workflow-partial': ['DROP INDEX workflow_events_instance_idx'],
        'pause-shape': ['DROP TABLE task_pause_controls', 'CREATE TABLE task_pause_controls(x TEXT)'],
        'pause-bad-root': ["INSERT INTO task_pause_controls VALUES ('TASK-SYNTH-CHILD',1,0,0,NULL,NULL,'"+STAMP+"','founder',NULL,'{\"version\":1,\"entries\":[]}')"],
        'move-shape': ['CREATE TABLE team_move_operations(x TEXT)'],
        'audit-absent': ['DROP TABLE audit_log'],
        'audit-view': ['DROP TABLE audit_log', 'CREATE VIEW audit_log AS SELECT id FROM tasks'],
        'audit-unique': ['CREATE UNIQUE INDEX audit_unique ON audit_log(agent)'],
        'audit-expression': ['CREATE INDEX audit_expression ON audit_log(lower(agent))'],
        'audit-partial-function': ["CREATE INDEX audit_partial ON audit_log(agent) WHERE length(action)>0"],
    }
    for name, statements in hazards.items():
        org = fixture(base, name)
        setup_sql(org, statements)
        attempt(org, name, expected='naming_database_unavailable')
        org.db.close()
    org = fixture(base, 'task-shape')
    sql = org.db._conn.execute("SELECT sql FROM sqlite_schema WHERE name='tasks'").fetchone()[0]
    for row in org.db._conn.execute("SELECT name,sql FROM sqlite_schema WHERE type='index' AND tbl_name='tasks' AND sql IS NOT NULL").fetchall():
        if 'parent_task_id' in row[1]:
            setup_sql(org, ['DROP INDEX '+qi(row[0])])
    rebuild(org, 'tasks', sql.replace('parent_task_id TEXT,',''))
    attempt(org, 'task-missing-parent', expected='naming_database_unavailable')
    org.db.close()
    # Advisory canonical capture cannot cache away a later committed bad dependency.
    for table in ('task_pause_controls', 'audit_log', 'team_move_operations'):
        org = fixture(base, 'revalidation-' + table)
        namespace = capture_namespace(org.root, org.teams)
        with sqlite3.connect(org.db.db_path) as other:
            other.execute(f'DROP TABLE IF EXISTS {table}')
            other.execute(f'CREATE TABLE {table}(x TEXT)')
        before = observe(org)
        try:
            reconcile_namespace(org, namespace, install=True)
        except NamingError as exc:
            assert exc.code == 'naming_database_unavailable' and before == observe(org)
            record('transaction-revalidate-' + table, actual=exc.code, preservation=True)
        else:
            raise AssertionError('stale admission')
        org.db.close()
    # Registry and helper associations isolated with valid F/Pause/canonical inputs.
    a = fixture(base, 'path-a')
    b = fixture(base, 'path-b')
    before_a, before_b = observe(a), observe(b)
    original = a.db
    a.db = b.db
    attempt(a, 'registry-selected-mismatch', expected='naming_database_unavailable')
    a.db = original
    assert before_a == observe(a) and before_b == observe(b)
    with transaction(a.db) as conn:
        try:
            validate_naming_install_admission(conn, org_slug=SLUG, expected_db_path=str(b.db.db_path))
        except NamingError as exc:
            assert exc.code == 'naming_database_unavailable' and conn.in_transaction
        else:
            raise AssertionError('helper main mismatch admitted')
    assert before_a == observe(a) and before_b == observe(b)
    with transaction(a.db) as conn:
        assert validate_naming_install_admission(conn, org_slug=SLUG, expected_db_path=str(a.db.db_path)) == 0
    # Same full prerequisites, actual in-memory main, helper retains transaction ownership.
    # Missing active transaction refuses without taking ownership or changing data.
    try:
        validate_naming_install_admission(a.db._conn, org_slug=SLUG, expected_db_path=str(a.db.db_path))
    except NamingError as exc:
        assert exc.code == 'naming_database_unavailable' and not a.db._conn.in_transaction
    else:
        raise AssertionError('helper outside transaction admitted')
    memory = sqlite3.connect(':memory:')
    a.db._conn.backup(memory)
    memory.execute('PRAGMA foreign_keys=ON')
    memory.execute('BEGIN IMMEDIATE')
    memory_org = SimpleNamespace(root=a.root,db=SimpleNamespace(_conn=memory))
    memory_before = observe(memory_org)
    try:
        validate_naming_install_admission(memory, org_slug=SLUG, expected_db_path=str(a.db.db_path))
    except NamingError as exc:
        assert exc.code == 'naming_database_unavailable' and memory.in_transaction
    else:
        raise AssertionError('memory main admitted')
    assert memory_before == observe(memory_org)
    save('memory-refusal-preserved', {'before_sha256':digest(json.dumps(memory_before,sort_keys=True).encode()), 'after_sha256':digest(json.dumps(observe(memory_org),sort_keys=True).encode())})
    memory.rollback()
    memory.close()
    a.db._conn.execute('PRAGMA foreign_keys=OFF')
    attempt(a, 'foreign-keys-off', expected='naming_database_unavailable')
    a.db._conn.execute('PRAGMA foreign_keys=ON')
    attempt(a, 'path-matching-positive')
    attempt(b, 'other-path-matching-positive')
    a.db.close()
    b.db.close()
    pause_journal_controls(base)
    record('path-helper-controls', actual='matching file positive; wrong main and valid memory negative', preservation=True)
    dependency_history_controls(base)


def case06(base: Path) -> None:
    from runtime.identities.registry import capture_namespace, reconcile_namespace
    from runtime.orchestrator.teams import TeamsRegistry, TeamManager
    for name in ('malformed', 'symlink', 'missing', 'memory-mismatch', 'duplicate', 'membership'):
        org = fixture(base, name)
        agent = org.root / 'org/agents/maker.md'
        if name == 'malformed':
            agent.write_text('bad')
        elif name == 'symlink':
            data = agent.read_bytes()
            agent.unlink()
            (org.root / 'other.md').write_bytes(data)
            agent.symlink_to(org.root / 'other.md')
        elif name == 'missing':
            agent.unlink()
        elif name == 'memory-mismatch':
            org.teams = TeamsRegistry({}, root=org.root)
        elif name == 'duplicate':
            (org.root / 'org/agents/_terminated/maker.md').write_bytes(agent.read_bytes())
        elif name == 'membership':
            org.teams = TeamsRegistry({'engineering': TeamManager('manager', 'engineering', ('maker', 'nonexistent'))}, root=org.root)
            org.teams.save()
        before = observe(org)
        try:
            reconcile_namespace(org, capture_namespace(org.root, org.teams), install=True)
        except (ValueError, OSError) as exc:
            assert before == observe(org)
            record(name, actual=getattr(exc, 'code', type(exc).__name__), preservation=True, attribution='retained canonical capture')
        else:
            raise AssertionError(name)
        org.db.close()
    org = fixture(base, 'collision')
    independent_names(org)
    setup_sql(org, ["INSERT INTO identity_name_claims VALUES ('new_one','agent','maker',0,1)"])
    path = org.root / 'org/agents/new_one.md'
    path.write_text((org.root / 'org/agents/maker.md').read_text().replace('name: maker','name: new_one'))
    org.teams = TeamsRegistry({'engineering': TeamManager('manager', 'engineering', ('maker','pending_one','new_one'))}, root=org.root)
    org.teams.save()
    attempt(org, 'permanent-id-collision', expected='identity_name_unavailable')
    org.db.close()
    org = fixture(base, 'lifecycle')
    independent_names(org)
    (org.root / 'org/agents/maker.md').rename(org.root / 'org/agents/_terminated/maker.md')
    org.teams = TeamsRegistry({'engineering': TeamManager('manager', 'engineering', ('pending_one',))}, root=org.root)
    org.teams.save()
    before = observe(org)
    attempt(org, 'terminal-lifecycle')
    assert_lifecycle_delta(before, observe(org))
    assert tuple(org.db._conn.execute("SELECT lifecycle,current_label,revision FROM identity_name_owners WHERE canonical_id='maker'").fetchone()) == ('terminated','CurrentOne',8)
    before = observe(org)
    attempt(org, 'terminal-repeat')
    assert before == observe(org)
    org.db.close()


def case07(base: Path) -> None:
    from runtime.identities.registry import refresh_names
    witnessed = False
    for extra in (1, 2, 3):
        org = fixture(base, 'capacity-' + str(extra))
        c = org.db._conn
        # Fixture-only capacity setting; each of at most three calibrations is fresh.
        pages = c.execute('PRAGMA page_count').fetchone()[0]
        c.execute(f'PRAGMA max_page_count={pages+extra}')
        before = observe(org)
        trace = []
        c.set_trace_callback(trace.append)
        from runtime.identities.registry import capture_namespace, reconcile_namespace
        sqlite_error = None
        try:
            reconcile_namespace(org, capture_namespace(org.root, org.teams), install=True)
        except sqlite3.DatabaseError as exc:
            sqlite_error = exc.sqlite_errorname
        c.set_trace_callback(None)
        direct_trace = list(trace)
        if sqlite_error:
            assert sqlite_error == 'SQLITE_FULL' and before == observe(org)
            trace.clear()
            c.set_trace_callback(trace.append)
            ready = refresh_names(org, install=True)
            c.set_trace_callback(None)
        else:
            ready = True
        save('capacity-' + str(extra) + '-direct', {'sqlite_error':sqlite_error,'trace':direct_trace})
        save('capacity-' + str(extra) + '-trace', trace)
        if not ready:
            assert before == observe(org)
            code = org.naming_install_diagnostic
            assert code == 'naming_database_unavailable'
            assert not refresh_names(org) and org.naming_diagnostic == code
            # A second CREATE reached means the first CREATE returned successfully.
            post_effect = any(s.lstrip().startswith('CREATE TABLE identity_name_owners') for s in trace)
            record('capacity-' + str(extra), actual=code, post_effect=post_effect, sqlite_error=sqlite_error, preservation=True)
            c.execute('PRAGMA max_page_count=1073741823')
            assert refresh_names(org, install=True) and org.naming_diagnostic is None and org.naming_install_diagnostic is None
            record('failure-absent-success-' + str(extra), actual=[code,code,None])
            witnessed |= post_effect
        else:
            record('capacity-' + str(extra), actual='success', post_effect_rollback='MISSING at this budget')
        org.db.close()
        if witnessed:
            break
    record('post-effect-rollback', actual='observed' if witnessed else 'MISSING after three natural calibrations')


def case08(base: Path) -> None:
    from runtime.identities.registry import refresh_names
    org = fixture(base, 'public-h8')
    assert refresh_names(org, install=True)
    org = reopen(org, 'public-first')
    asyncio.run(public(org, rename=True))
    org = reopen(org, 'public-second')
    before = observe(org)
    asyncio.run(public(org, rename=False))
    assert before == observe(org)
    assert_eight(org.db._conn)
    org.db.close()


async def public(org: object, *, rename: bool) -> None:
    from fastapi import FastAPI
    import httpx
    from runtime.daemon import paths
    from runtime.daemon.routes.identities import router
    from runtime.daemon.state import DaemonState
    from runtime.runtime import RuntimeDir
    from runtime.config import Settings
    app = FastAPI()
    (org.root.parent / 'runtime').mkdir(exist_ok=True)
    app.state.daemon = DaemonState(runtime=RuntimeDir(org.root.parent / 'runtime'), settings=Settings(), orgs={SLUG: org})
    app.include_router(router, prefix='/api/v1/orgs/{slug}')
    token = paths.ensure_token()
    prefix = '/api/v1/orgs/' + SLUG
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://127.0.0.1', headers={'Authorization':'Bearer '+token}) as client:
        before = observe(org)
        response = await client.get(prefix+'/identities')
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload['naming_diagnostic'] is None
        for identity in payload['identities']:
            if identity['kind'] == 'agent':
                directory = {'active':'', 'pending':'_pending', 'terminated':'_terminated'}[identity['lifecycle']]
                assert identity['canonical_definition_revision'] == digest((org.root / 'org/agents' / directory / (identity['canonical_id']+'.md')).read_bytes())
        assert before == observe(org)
        if rename:
            for ident, label, revision in [('maker','CurrentOne',1), ('maker','CurrentTwo',2), ('founder','HumanOwner',1)]:
                rename_before = observe(org)
                audit_before = org.db._conn.execute('SELECT count(*) FROM audit_log').fetchone()[0]
                seq_before = org.db._conn.execute("SELECT seq FROM sqlite_sequence WHERE name='audit_log'").fetchone()[0]
                suffix = '/founder/addressable-name' if ident == 'founder' else '/agents/'+ident+'/addressable-name'
                started = datetime.now(timezone.utc)
                response = await client.put(prefix+suffix, json={'addressable_name':label, 'expected_name_revision':revision})
                finished = datetime.now(timezone.utc)
                assert response.status_code == 200, response.text
                assert response.json()['name_revision'] == revision+1
                assert tuple(org.db._conn.execute('SELECT current_label,revision FROM identity_name_owners WHERE canonical_id=?',(ident,)).fetchone()) == (label,revision+1)
                assert org.db._conn.execute('SELECT count(*) FROM audit_log').fetchone()[0] == audit_before+1
                assert org.db._conn.execute("SELECT seq FROM sqlite_sequence WHERE name='audit_log'").fetchone()[0] == seq_before+1
                row = org.db._conn.execute('SELECT task_id,agent,action,payload FROM audit_log ORDER BY id DESC LIMIT 1').fetchone()
                assert tuple(row[:3]) == ('founder','founder','identity_name_changed')
                detail = json.loads(row[3])
                assert detail['actor'] == detail['source'] == 'founder'
                assert detail['old_label'] == {('maker',1):'maker',('maker',2):'CurrentOne',('founder',1):'founder'}[(ident,revision)]
                assert detail['old_revision'] == revision and detail['new_revision'] == revision+1 and detail['new_label'] == label
                rename_after = observe(org)
                save('rename-'+ident+'-'+label+'-before', rename_before)
                save('rename-'+ident+'-'+label+'-after', rename_after)
                assert_rename_delta(rename_before, rename_after, ident=ident, label=label,
                                    revision=revision, started=started, finished=finished)
                record('rename-'+ident+'-'+label, actual='one audit and revision increment', audit_payload=detail)
        before = observe(org)
        for context in ('lookup', 'thread_recipient', 'task_owner'):
            addresses = ['maker','CurrentTwo','CurrentOne','founder','HumanOwner','pending_one','retired_one']
            response = await client.post(prefix+'/identities/resolve', json={'addresses':addresses,'context':context})
            assert response.status_code == 200, response.text
            rows = response.json()['resolutions']
            assert rows[0]['identity']['canonical_id'] == rows[1]['identity']['canonical_id'] == 'maker'
            assert rows[2]['status'] == 'former_name' and not rows[2]['eligible']
            assert rows[3]['identity']['kind'] == rows[4]['identity']['kind'] == 'founder'
            if context == 'task_owner':
                assert all(not rows[i]['eligible'] for i in (3,4,5,6))
            if context == 'thread_recipient':
                assert rows[3]['eligible'] and rows[4]['eligible']
            record('resolve-'+context, actual=rows)
        assert before == observe(org)


def trace_setup(path: str, sql: str) -> None:
    global TRACE_BYTES, TRACE_OVERFLOW
    TRACE_BYTES += len(path.encode()) + len(sql.encode())
    if TRACE_BYTES > 32 * 1024 * 1024:
        TRACE_OVERFLOW = True
        return
    SQL_TRACE.append([path,sql])


def main() -> None:
    global EVIDENCE
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case', required=True, choices=[f'B-SA-{i:02}' for i in range(1, 9)])
    parser.add_argument('--scratch', type=Path, required=True)
    parser.add_argument('--evidence', type=Path, required=True)
    args = parser.parse_args()
    # Never allow existing contents, symlinks, URI mode or arbitrary DB input.
    scratch_parent = args.scratch.parent.resolve(strict=True)
    assert scratch_parent.name.startswith('TASK-') and scratch_parent.parent.name == 'task-tmp' and scratch_parent.parent.parent.name == '.happyranch', 'scratch must be a new child of an owned .happyranch/task-tmp/TASK-* directory'
    for path in (args.scratch, args.evidence):
        assert path.is_absolute() and not path.exists() and not path.is_symlink(), 'new absolute directory required'
        parent = path.parent.resolve(strict=True)
        assert parent.stat().st_uid == os.getuid() and path.parent == parent, 'owned canonical parent required'
        assert parent != Path('/'), 'contained scratch required'
        path.mkdir(mode=0o700)
    EVIDENCE = args.evidence
    os.environ['HAPPYRANCH_DAEMON_HOME'] = str(args.scratch / 'daemon-home')
    sys.path.insert(0, str(SOURCE))
    signal.alarm(120)
    save('provenance', {'python': sys.executable, 'version': sys.version, 'source': str(SOURCE),
                       'module_sha256': {str(p.relative_to(SOURCE)): digest(p.read_bytes()) for p in [SOURCE / 'runtime/identities/registry.py', SOURCE / 'runtime/identities/schema.py', SOURCE / 'runtime/infrastructure/workflow_schema.py', SOURCE / 'runtime/daemon/org_state.py']}, 'fixture_manifest': frozen()[0]})
    try:
        globals()['case' + args.case[-2:]](args.scratch)
    finally:
        save('results', RESULTS)
        save('setup-sql-trace', SQL_TRACE)
        save('trace-bound', {'bytes':TRACE_BYTES,'overflow':TRACE_OVERFLOW})
        assert not TRACE_OVERFLOW, 'setup trace exceeded explicit bound'


if __name__ == '__main__':
    main()
