#!/usr/bin/env python3
"""Manual SYNTHETIC reader observations; never automatic CI or authentic proof.

The parent imports only stdlib. All product/SQLite work is inside a qualified
namespace. Exit 2 includes missing assigned evidence, even if a subset passes.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import ast
import hashlib
import gzip
import importlib.metadata
import json
import os
from pathlib import Path
import re
import resource
import signal
import subprocess
import sys
import time
import tomllib
import zipfile

LIMIT = 2 * 1024 * 1024
NAMESPACES = ('user', 'mnt', 'net', 'pid', 'ipc', 'uts', 'cgroup')
INVENTORY = 'scripts/coding_reader_verification_manifest.json'
LOADER = '/usr/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2'
LOADER_ALIAS = '/lib64/ld-linux-x86-64.so.2'
SUPPORT_CASES = tuple([f'L{i}' for i in range(4)]+[f'R{i}' for i in range(8)]+['A-traversal','A-bytes'])
# Completion is tied to executed stable case IDs, never the absence of a TODO.
REQUIRED_CASES = (
    'F8-R1c.complete.constructor',
    'F5-R1c.absent.omitted', 'F5-R1c.absent.none',
    'F5-R1c.valid.omitted', 'F5-R1c.valid.none', 'F5-R1c.supplied.prior-work',
    'F5-R1c.wrong-budget', 'F5-R1c.foreign-org',
    *(f'F5-R1c.{control}.{outcome}' for control in
      ('malformed','full-row','strict-storage','command','journal','admission','extra','orphan')
      for outcome in ('positive','refusal')),
    'F8-R1c.queued.caller-success', 'F8-R1c.queued.supplied',
    'F8-R1c.schema.data-false', 'F8-R1c.queued.caller-uncommitted',
    'F8-R1c.queued.wrong-budget', 'F8-R1c.queued.caller-refusal',
    'F8-R1c.schema.refusal', 'F5-R1c.transaction-required', 'F8-R1c.queued.standalone',
    'F8-R1c.noncoding-old-history',
    'F5-R1c.aggregate-candidates', 'F5-R1c.aggregate-intermediate-bytes',
    'F5-R1c.aggregate-traversal',
    'F5-R1c.aggregate-candidates.component-a.positive',
    'F5-R1c.aggregate-candidates.component-b.positive',
    'F8-R1c.schema.aggregate-traversal.positive', 'F8-R1c.schema.aggregate-traversal.refusal',
    'F8-R1c.schema.aggregate-bytes.positive', 'F8-R1c.schema.aggregate-bytes.refusal',
    *(f'F8-R1c.complete.{case}' for case in (
        'empty.omitted', 'empty.none', 'empty.supplied', 'empty.native.positive',
        'empty.native.storage', 'empty.native.coding', 'empty.native.malformed',
        'omitted', 'none', 'supplied', 'wrong-budget', 'installed', 'public', 'data',
        'retained.positive', 'retained.storage', 'retained.provenance', 'retained.canonical',
        'caller-success', 'caller-refusal', 'fresh.before', 'fresh.changed', 'fresh.corrupt')),
)


# This driver is data in the host parent, never exec/imported there. Qualification
# precedes sys.path changes and the first product import/SQL constructor.
DRIVER = r'''
from __future__ import annotations
import base64, gzip, hashlib, json, os, signal, socket, sqlite3, sys, time
from contextlib import contextmanager
from collections.abc import Callable
from pathlib import Path
cfg = json.loads(Path('/fixture/config.json').read_text())
result = {'origin':'SYNTHETIC', 'cases':[], 'unproved':{}, 'required_cases':cfg['required_cases'], 'complete':False, 'support':[], 'outstanding_broader_targets':{'candidate8193':{'status':'UNPROVED','target':8193,'native18_overflow_visit_lower_bound':155649}}}
qualified = False

def save() -> None:
    raw=canonical(result)
    if len(raw)>16*1024*1024:
        raise RuntimeError('expanded observation bound')
    payload=gzip.compress(raw,mtime=0)
    if len(payload)>2*1024*1024:
        raise RuntimeError('compressed observation bound')
    Path('/result/observations.json.gz').write_bytes(payload)

def equal(checks: list, name: str, actual: object, expected: object) -> None:
    checks.append(dict(assertion=name, actual=actual, expected=expected, passed=actual == expected))

def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()

def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()

def encoded(value: object) -> dict:
    if isinstance(value, bytes):
        return {'type':'blob', 'base64':base64.b64encode(value).decode()}
    return {'type':type(value).__name__, 'value':value}

def saved_rows(conn: sqlite3.Connection) -> dict:
    rows = {}
    for table, in conn.execute("SELECT name FROM sqlite_schema WHERE type='table' ORDER BY name"):
        quoted = '"' + table.replace('"', '""') + '"'
        rows[table] = sorted([[encoded(v) for v in r] for r in conn.execute('SELECT * FROM '+quoted)], key=repr)
    return rows

def schema(conn: sqlite3.Connection) -> list:
    # Whole native schema, SQL and ordered columns/FKs/index metadata. No
    # candidate-derived reference or filtered workflow-only baseline.
    objects = []
    for kind, name, table, sql in conn.execute(
            'SELECT type,name,tbl_name,sql FROM sqlite_schema ORDER BY type,name'):
        item = dict(kind=kind, name=name, table=table, sql=sql)
        quote = '"'+name.replace('"','""')+'"'
        if kind == 'table':
            item['columns'] = [tuple(r) for r in conn.execute('PRAGMA table_xinfo('+quote+')')]
            item['foreign_keys'] = [tuple(r) for r in conn.execute('PRAGMA foreign_key_list('+quote+')')]
            item['indexes'] = [tuple(r) for r in conn.execute('PRAGMA index_list('+quote+')')]
        elif kind == 'index':
            item['index_columns'] = [tuple(r) for r in conn.execute('PRAGMA index_xinfo('+quote+')')]
        objects.append(item)
    return objects

# One resource scope for every reader, aggregate and finite support observation.
class SupportIncomplete(RuntimeError):
    pass

class ConstructorContractFailure(RuntimeError):
    pass


def check_trace(state: dict) -> None:
    if state['overflow']:
        raise SupportIncomplete('SUPPORT trace bound')


@contextmanager
def support_scope(connection: object):
    state=dict(trace=[], count=0, overflow=False)
    previous=signal.getsignal(signal.SIGALRM)
    def alarm(*_: object) -> None:
        raise SupportIncomplete('SUPPORT delivered alarm')
    def traced(sql: str) -> None:
        state['count']=min(2049,state['count']+1)
        if len(state['trace']) >= 2048:
            state['overflow']=True
            return
        raw=sql.encode()
        state['trace'].append(dict(sql=sql[:320],bytes=len(raw),sha256=digest(raw)))
    # Protection precedes even partial installation and every fallible snapshot.
    try:
        signal.signal(signal.SIGALRM,alarm)
        signal.setitimer(signal.ITIMER_REAL,2)
        connection.set_trace_callback(traced)
        yield state
        check_trace(state)  # sqlite3 deliberately swallows callback exceptions
    finally:
        try:
            connection.set_trace_callback(None)
        finally:
            try:
                signal.setitimer(signal.ITIMER_REAL,0)
            finally:
                signal.signal(signal.SIGALRM,previous)


@contextmanager
def contained_scope(connection: object):
    previous=signal.getsignal(signal.SIGALRM)
    try:
        with support_scope(connection) as state:
            yield state
    finally:
        # Independent immutable outer containment; R/A records observe first.
        signal.setitimer(signal.ITIMER_REAL,0)
        signal.signal(signal.SIGALRM,previous)
        connection.set_trace_callback(None)


class TraceSpy:
    # SUPPORT-only forwarding observer; readers keep the original connection.
    def __init__(self, connection: object, install_fault: bool = False) -> None:
        self.connection=connection
        self.events=[]
        self.installation_timers=[]
        self.calls=0
        self.install_fault=install_fault
        self.callback=None
    def set_trace_callback(self, callback: object) -> None:
        self.callback=callback
        self.events.append('detach' if callback is None else 'attach')
        if callback is not None:
            self.installation_timers.append(timer_observation())
        def forward(sql: str) -> None:
            self.calls+=1
            callback(sql)
        self.connection.set_trace_callback(None if callback is None else forward)
        if callback is not None and self.install_fault:
            self.install_fault=False
            raise LookupError('SUPPORT install')

    @contextmanager
    def fixture_connection(self, connection: object):
        # Forward the SAME bounded scope callback while independently owned
        # fixture input is observed. No timer or allowance is replenished.
        original=self.connection
        callback=self.callback
        original.set_trace_callback(None)
        self.connection=connection
        try:
            self.set_trace_callback(callback)
            yield
        finally:
            connection.set_trace_callback(None)
            self.connection=original
            self.set_trace_callback(callback)


def timer_observation() -> dict:
    remaining,interval=signal.getitimer(signal.ITIMER_REAL)
    return dict(remaining=remaining,interval=interval,handler=repr(signal.getsignal(signal.SIGALRM)))


def cleanup_observations(connection: object, spy: TraceSpy, state: dict | None,
                         previous: object, checks: list) -> dict:
    # Independent observation BEFORE immutable outer containment repairs leaks.
    after=timer_observation()
    equal(checks,'scope timer disarmed',list(signal.getitimer(signal.ITIMER_REAL)),[0.0,0.0])
    equal(checks,'previous handler restored',signal.getsignal(signal.SIGALRM)==previous,True)
    equal(checks,'actual detach observed',spy.events[-1:] ,['detach'])
    before_calls=spy.calls
    before_ledger=None if state is None else len(state['trace'])
    connection.execute('SELECT 73').fetchall()
    equal(checks,'post-scope SELECT detached',spy.calls,before_calls)
    if state is not None:
        equal(checks,'closed ledger unchanged',len(state['trace']),before_ledger)
    return after


def fresh_sibling(connection: object, checks: list) -> dict:
    # Immutable controller containment runs BEFORE this scope, not as its proof.
    spy=TraceSpy(connection)
    previous=signal.getsignal(signal.SIGALRM)
    try:
        with support_scope(spy) as sibling:
            timer=timer_observation()
            equal(checks,'fresh sibling timer armed',0<timer['remaining']<=2,True)
            connection.execute('SELECT 79').fetchall()
            equal(checks,'fresh sibling actual callbacks',spy.calls,1)
            equal(checks,'fresh sibling retained events',len(sibling['trace']),1)
            equal(checks,'fresh sibling no sticky predecessor',sibling['overflow'],False)
        after=cleanup_observations(connection,spy,sibling,previous,checks)
        return dict(timer=timer,after=after,events=spy.events,callbacks=spy.calls)
    finally:
        # Never mutate this controller containment for S7/S8.
        signal.setitimer(signal.ITIMER_REAL,0)
        signal.signal(signal.SIGALRM,previous)
        connection.set_trace_callback(None)


@contextmanager
def watched_scope(connection: object, name: str):
    checks=[]; samples=[]; spy=TraceSpy(connection)
    previous=signal.getsignal(signal.SIGALRM); profile=sys.getprofile()
    state=None
    def observer(frame: object, event: str, arg: object) -> None:
        if event == 'call' and frame.f_code.co_name in ('saved_rows','queued_storage','read_release_import'):
            timer=timer_observation()
            samples.append(dict(function=frame.f_code.co_name,**timer))
            equal(checks,'live timer at '+frame.f_code.co_name,0<timer['remaining']<=2,True)
    try:
        with support_scope(spy) as state:
            sys.setprofile(observer)
            try:
                yield state,spy
            finally:
                sys.setprofile(profile)
            equal(checks,'aggregate pre/post and isolated queued snapshot entries',sum(s['function']=='saved_rows' for s in samples),4)
            equal(checks,'actual queued entry',any(s['function']=='queued_storage' for s in samples),True)
            equal(checks,'actual release entries',any(s['function']=='read_release_import' for s in samples),True)
            equal(checks,'actual trace events retained',len(state['trace']),spy.calls)
            equal(checks,'bounded ledger',len(state['trace'])<=2048,True)
        after=cleanup_observations(connection,spy,state,previous,checks)
    finally:
        sys.setprofile(profile)
        # Immutable controller containment, after scope evidence is captured.
        signal.setitimer(signal.ITIMER_REAL,0)
        signal.signal(signal.SIGALRM,previous)
        connection.set_trace_callback(None)
    sibling=fresh_sibling(connection,checks)
    result['support'].append(dict(name=name,checks=checks,samples=samples,after=after,
        events=spy.events,callbacks=spy.calls,sibling=sibling,scope_outcome_code=0))


def resource_records() -> None:
    for index in range(8):
        connection=sqlite3.connect(':memory:')
        checks=[]; samples=[]; state=None; cause=None; entries=0
        spy=TraceSpy(connection,install_fault=index==7)
        previous=signal.getsignal(signal.SIGALRM)
        before=timer_observation()
        delivery=[]
        old_profile=sys.getprofile()
        def delivered(frame: object, event: str, arg: object) -> None:
            if event=='call' and frame.f_code.co_name=='alarm':
                delivery.append('SIGALRM')
        try:
            try:
                with support_scope(spy) as state:
                    sys.setprofile(delivered)
                    samples.append(timer_observation())  # pre-snapshot boundary
                    if index==5:
                        raise LookupError('SUPPORT pre-snapshot')
                    entries+=1
                    samples.append(timer_observation())
                    if index==1:
                        raise ValueError('SUPPORT refusal')
                    if index==2:
                        signal.raise_signal(signal.SIGALRM)
                    total=2048 if index==3 else 2050 if index==4 else 1
                    for event in range(total):
                        connection.execute('SELECT 71').fetchall()
                        if index==4 and event in (2048,2049):
                            samples.append(dict(event=event+1,count=state['count'],
                                retained=len(state['trace']),overflow=state['overflow']))
                    if index==6:
                        raise LookupError('SUPPORT post-reader')
                    samples.append(timer_observation())  # post-snapshot boundary
            except (SupportIncomplete,ValueError,LookupError) as exc:
                cause=type(exc).__name__+': '+str(exc)
            finally:
                sys.setprofile(old_profile)
            expected={1:'ValueError: SUPPORT refusal',2:'SupportIncomplete: SUPPORT delivered alarm',
                4:'SupportIncomplete: SUPPORT trace bound',5:'LookupError: SUPPORT pre-snapshot',
                6:'LookupError: SUPPORT post-reader',7:'LookupError: SUPPORT install'}.get(index)
            equal(checks,'exact original SUPPORT cause',cause,expected)
            equal(checks,'actual reader/action entry',entries,0 if index in (5,7) else 1)
            for sample in samples:
                if 'remaining' in sample:
                    equal(checks,'positive live timer',0<sample['remaining']<=2,True)
            if index==2:
                equal(checks,'one actual delivered handler',delivery,['SIGALRM'])
            if index in (3,4):
                equal(checks,'independent SQLite callbacks',spy.calls,2048 if index==3 else 2050)
                equal(checks,'retained boundary',len(state['trace']),2048)
                equal(checks,'bounded count',state['count'],2048 if index==3 else 2049)
                equal(checks,'sticky overflow',state['overflow'],index==4)
                if index==4:
                    equal(checks,'2049 plus fixed extra event sticky',[s for s in samples if 'event' in s],
                        [dict(event=n,count=2049,retained=2048,overflow=True) for n in (2049,2050)])
            for timer in spy.installation_timers:
                equal(checks,'timer during actual trace installation',0<timer['remaining']<=2,True)
            after=cleanup_observations(connection,spy,state,previous,checks)
        finally:
            # Immutable outer containment: record failed cleanup first, then repair.
            sys.setprofile(old_profile)
            signal.setitimer(signal.ITIMER_REAL,0)
            signal.signal(signal.SIGALRM,previous)
            connection.set_trace_callback(None)
        try:
            sibling=fresh_sibling(connection,checks)
            result['support'].append(dict(name='R'+str(index),checks=checks,before=before,
                inside=samples,installation_timers=spy.installation_timers,after=after,cause=cause,scope_outcome_code=2 if cause else 0,
                entries=entries,delivery=delivery,events=spy.events,callbacks=spy.calls,
                trace=None if state is None else dict(count=state['count'],retained=len(state['trace']),overflow=state['overflow']),
                sibling=sibling))
        finally:
            connection.close()

try:
    observation = dict(cwd=os.getcwd(), cwd_entries=os.listdir('.'), env=dict(os.environ),
        interfaces=socket.if_nameindex(), python=sys.version, executable=sys.executable,
        namespaces={n:os.readlink('/proc/self/ns/'+n) for n in cfg['host_ns']},
        absent={p:not Path(p).exists() for p in
            ['/home','/root','/run','/etc','/src/.git','/src/tests','/src/.venv','/src/.happyranch']})
    result['isolation'] = observation
    if (observation['cwd'] != '/work' or observation['cwd_entries']
            or observation['interfaces'] != [(1,'lo')]
            or not all(observation['absent'].values())
            or any(observation['namespaces'][n] == old for n,old in cfg['host_ns'].items())
            or observation['env'] != cfg['env'] or sys.version_info[:2] != (3,14)):
        raise RuntimeError('isolation qualification refused')
    qualified = True
    save()  # durable pre-import qualification observation
    # Redirect importlib's absent bytecode probes away from the sealed source.
    # -B still forbids writes; no cached bytecode is mounted or admitted.
    sys.pycache_prefix = '/tmp/absent-bytecode'
    sys.path[:0] = ['/src', '/deps']
    opened, imported = set(), set()
    admitted_source = set(cfg['source_files'])
    def audit(event: str, args: tuple) -> None:
        if event == 'open' and isinstance(args[0], str):
            p = Path(args[0])
            if str(p).startswith('/src/') and str(p)[5:] not in admitted_source:
                result.setdefault('audit_refusals',[]).append(dict(event=event,path=str(p)))
                raise RuntimeError('unlisted source/resource access: '+str(p))
            opened.add(str(p))
        if event == 'import':
            name = args[0]
            if name.split('.')[0] in ('runtime','cli'):
                if name not in cfg['modules']:
                    raise RuntimeError('unlisted product import: '+name)
                imported.add(name)
        if event in ('subprocess.Popen','os.system','os.exec','os.fork','os.posix_spawn','socket.connect','socket.bind'):
            raise RuntimeError('product external effect refused: '+event)
    sys.addaudithook(audit)
    resource_records()
    from runtime.infrastructure.database import Database
    from runtime.infrastructure.workflow_schema import (
        initialize_complete_org_schema, _validate_installed, _read_workflow_schema,
        validate_workflow_schema,
    )
    from runtime.infrastructure.workflow_coding_schema import validate_coding_data
    from runtime.workflows._coding_release_history import read_release_import
    from runtime.workflows.coding_authority_history import _Budget
    from runtime.workflows.coding_start import queued_storage
    # Fixtures use the ordinary full native and complete J initializer. The
    # independent whole release reference is constructed from pristine source.
    # Separate pristine full Database/J construction, never copied from the
    # candidate schema. In-memory SQLite avoids WAL scratch expansion under
    # the unchanged 2MiB per-file bound; both complete constructors are real.
    reference = Database(Path(':memory:'))
    try:
        # Observe actual full construction before any dependent fixture use.
        # A contract assertion failure is distinct from import/setup failure.
        constructor_checks=[]
        native_before=saved_rows(reference._conn)
        schema_before=schema(reference._conn)
        transaction_before=reference._conn.in_transaction
        constructor_error=None;constructor_return=None;constructor_layout=None
        try:
            constructor_return=initialize_complete_org_schema(
                reference, expected_org_slug='release-reference', layout='J')
            constructor_layout=validate_workflow_schema(
                reference._conn, expected_org_slug='release-reference')
        except Exception as exc:
            constructor_error=type(exc).__name__+': '+str(exc)
        equal(constructor_checks,'pristine complete-J constructor exception',constructor_error,None)
        equal(constructor_checks,'pristine complete-J constructor return',constructor_return,None)
        equal(constructor_checks,'pristine complete-J recognized layout',constructor_layout,'J')
        equal(constructor_checks,'constructor transaction ownership',
            reference._conn.in_transaction,transaction_before)
        native_after=saved_rows(reference._conn)
        equal(constructor_checks,'constructor preserves all original native rows',
            {name:native_after.get(name) for name in native_before},native_before)
        schema_after={row['name']:row for row in schema(reference._conn)}
        equal(constructor_checks,'constructor preserves original native schema',
            [schema_after.get(row['name']) for row in schema_before],schema_before)
        equal(constructor_checks,'constructor installs four complete coding tables',
            sorted(name for name in schema_after if name.startswith('workflow_coding_')),
            ['workflow_coding_attempts','workflow_coding_events','workflow_coding_mappings','workflow_coding_runs'])
        equal(constructor_checks,'constructor foreign keys',
            [tuple(r) for r in reference._conn.execute('PRAGMA foreign_key_check')],[])
        equal(constructor_checks,'constructor native integrity',
            [tuple(r) for r in reference._conn.execute('PRAGMA integrity_check')],[('ok',)])
        result['cases'].append(dict(name='F8-R1c.complete.constructor',checks=constructor_checks))
        if not all(check['passed'] for check in constructor_checks):
            result['missing_cases']=sorted(set(cfg['required_cases'])-{'F8-R1c.complete.constructor'})
            raise ConstructorContractFailure('pristine complete-J constructor assertion failed')
        reference_schema = schema(reference._conn)
        reference_digest = digest('\n'.join(str(r[0]) for r in reference._conn.execute(
            'SELECT sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY name')).encode())
    finally:
        reference.close()
    db = Database(Path(':memory:'))
    try:
        initialize_complete_org_schema(db, expected_org_slug=cfg['org_slug'], layout='J')
        conn = db._conn
        conn.row_factory = None
        actual_digest = digest('\n'.join(str(r[0]) for r in conn.execute(
            'SELECT sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY name')).encode())
        if schema(conn) != reference_schema or actual_digest != reference_digest or _validate_installed(
                conn, expected_org_slug=cfg['org_slug'], validate_data=False) != 'J':
            raise RuntimeError('complete independent J recognition failed')
        result['fixture_schema'] = schema(conn)
        result['reference_digest'] = reference_digest
        result['initial_rows'] = saved_rows(conn)
        org = cfg['org_slug']
        namespace = 'release/happyranch'
        stamp = '2001-02-03T04:05:06.000000Z'
        command = canonical(dict(format='workflow-coding-release-import-command@1',
                                 operation_key='synthetic-import', expected_current_version=0))
        # Empty authority roster is a valid retained v1 snapshot; these bytes
        # assert no real actor/session/publication and no current authority.
        snapshot = canonical(dict(schema_version=1, org_slug=org, agents=[], teams=[],
            reviewer_agents=[], machine_global_profiles=[], active_policy_selectors=[]))
        sd, cd = digest(snapshot), digest(command)
        pin = dict(namespace='org/'+org, generation=1, snapshot_digest=sd)
        admission = dict(id='synthetic-admission', namespace=pin['namespace'], generation=1,
                         request_digest=cd, admitted_by='human:founder')
        capture = dict(pin=pin, admission_record_id=admission['id'],
            snapshot_bytes_base64=base64.b64encode(snapshot).decode(),
            grant_bytes_base64=base64.b64encode(command).decode(), grant_digest=cd,
            profile_evidence=dict(ready=dict(**pin, snapshot_bytes_hex=snapshot.hex()),
                pointer=dict(current_generation=1, journal_id='synthetic-journal',
                             snapshot_digest=sd, state='ready', profile_fence=0), profiles=[], mirror=[]))
        provenance = dict(format='workflow-coding-release-import-provenance@1', org_slug=org,
            publisher=dict(principal_kind='human', principal_id='founder', proof_kind='founder_bearer'),
            command_bytes_base64=base64.b64encode(command).decode(), command_digest=cd,
            admission=dict(record=admission, capture=capture), created_at=stamp)
        definition = Path('/src/runtime/workflows/builtin_templates/coding.v1.json').read_bytes()
        # Independently fixed contract inputs; never publication_rows or the
        # returned history used as its own expected-value oracle.
        def stable(*parts: object) -> str:
            return str(parts[0])+':'+digest(canonical(list(parts)))
        identity = stable('workflow-template', namespace, 'coding')
        draft = stable('workflow-template-draft', identity, 1, cd)
        version = stable('workflow-template-version', identity, 1, digest(definition))
        common = dict(namespace=namespace, template_name='coding', definition_bytes=definition,
            definition_digest=digest(definition), compiler_pin='workflow-coding-compiler@1',
            validator_pin='workflow-coding-validator@1', source_pin='release-input@1')
        pub = canonical(provenance).decode()
        expected = [
            ('workflow_template_identities', dict(id=identity, namespace=namespace, template_name='coding',
                current_version=1, status='active', created_at=stamp)),
            ('workflow_template_drafts', dict(id=draft, **common, author_principal=pub, created_at=stamp)),
            ('workflow_template_versions', dict(id=version, draft_id=draft, version=1, **common,
                published_by=pub, published_at=stamp)),
            ('workflow_template_identity_versions', dict(template_identity_id=identity, version=1,
                template_version_id=version, content_digest=digest(definition))),
            ('workflow_template_publish_operations', dict(org_slug=org, principal='human:founder',
                operation_key='synthetic-import', request_digest=cd, template_identity_id=identity,
                expected_current_version=0, result_version=1, template_version_id=version)),
            ('workflow_admission_records', admission)]
        def insert(table: str, row: dict) -> None:
            conn.execute('INSERT INTO '+table+' ('+','.join(row)+') VALUES ('+
                         ','.join('?' for _ in row)+')', tuple(row.values()))
        def publication() -> None:
            insert('workflow_authority_pointers', dict(namespace=pin['namespace'], current_generation=1,
                journal_id='synthetic-journal', snapshot_digest=sd, state='ready', profile_fence=0))
            insert('workflow_publication_journals', dict(id='synthetic-journal', namespace=pin['namespace'],
                generation=1, expected_generation=0, snapshot_bytes=snapshot, snapshot_digest=sd,
                publisher='human:founder', publisher_invocation='synthetic-invocation', profile_fence=0,
                state='cache_installed', recovery_owner='synthetic-owner', file_phase_owner=None))
            for table,row in expected:
                insert(table,row)
        @contextmanager
        def independent_input():
            # Serialize fixed fixture INPUT before any candidate observation,
            # never reader-returned rows. The original fixture/savepoints are
            # untouched. A new real connection owns a committed publication
            # baseline and, when applicable, a genuine caller BEGIN/sentinel.
            global conn
            original=conn
            image=original.serialize()
            caller=original.in_transaction
            isolated=sqlite3.connect(':memory:')
            try:
                isolated.deserialize(image)
                isolated.execute('PRAGMA foreign_keys=ON')
                isolated.commit()
                conn=isolated
                db._conn=isolated
                if caller:
                    conn.execute('BEGIN')
                    insert('org_settings',dict(section='synthetic-isolated-sentinel',value_json='{}',
                        updated_at=stamp,updated_by='synthetic'))
                yield dict(serialized_input_sha256=digest(image),committed_baseline=True,
                           caller_begin=caller,sentinel='synthetic-isolated-sentinel' if caller else None)
            finally:
                conn=original
                db._conn=original
                isolated.close()
        def observe(name: str, action: Callable[[], object], expected_category: str | None = None,
                    expect_import: bool = False, supplied: _Budget | None = None,
                    kind: str | None = None, **options: object) -> None:
            with independent_input() as fixture_input:
                observe_input(name,action,expected_category,expect_import,supplied,kind,**options)
                result['cases'][-1]['fixture_input']=fixture_input
        def observe_input(name: str, action: Callable[[], object], expected_category: str | None,
                          expect_import: bool, supplied: _Budget | None, kind: str | None,
                          original_rows: list | None = None, source_change: object = None) -> None:
            checks = []
            phases=[]; acquisition=[]; allowances=[]; inventories=[]; changes=[]
            complete=kind in ('complete','public-complete','installed-complete','coding-data')
            oracle_rows=expected if original_rows is None else original_rows
            prior_work=None if supplied is None else vars(supplied).copy()
            old_profile=sys.getprofile()
            def phase(frame: object, event: str, arg: object) -> None:
                if (supplied is not None and 'aggregate-candidates' in name and event in ('call','return')
                        and frame.f_code.co_name=='read_release_import'):
                    phases.append(dict(event=event,counters=vars(supplied).copy(),
                        forwarded=frame.f_locals.get('budget') is supplied))
                module=frame.f_globals.get('__name__','')
                function=frame.f_code.co_name
                if not complete or not module.startswith('runtime.'):
                    return
                if event=='call' and function in ('_read_coding_data','read_release_import',
                        '_read_mapping_storage','source_storage','read_queued'):
                    acquisition.append(dict(module=module,source=frame.f_code.co_filename,function=function))
                if event=='call' and function in ('read_release_import','_read_mapping_storage',
                        'source_storage','read_queued','_bounded_rows','_refuse_coding_metadata'):
                    budget=frame.f_locals.get('budget')
                    allowances.append(dict(module=module,function=function,identity=id(budget),
                        counters=None if budget is None else vars(budget).copy()))
                if event=='return' and function=='_read_coding_data' and arg is not None:
                    inventories.append(arg)
                if (event=='call' and function=='_validate_source_data' and source_change is not None
                        and not changes):
                    # Test-side disposable SQL write AFTER independent acquisition.
                    # It cannot rewrite the already reconstructed original rows.
                    conn.execute('UPDATE workflow_template_drafts SET author_principal=? WHERE id=?',
                        (source_change,draft))
                    changes.append(encoded(source_change))
            with contained_scope(conn) as scope:
                trace = scope['trace']
                before = saved_rows(conn)
                schema_before=[tuple(r) for r in conn.execute(
                    'SELECT type,name,tbl_name,sql FROM sqlite_schema ORDER BY type,name')] if complete else None
                transaction = conn.in_transaction
                value=None
                try:
                    sys.setprofile(phase)
                    try:
                        value = action()
                    finally:
                        sys.setprofile(old_profile)
                    category = None
                    if kind == 'absent':
                        equal(checks,'complete absent import',value,None)
                    elif kind == 'queued':
                        equal(checks,'no synthetic coding run/callback',vars(value),
                              dict(admitted={},runs={},owners={},callbacks={}))
                    elif kind == 'schema':
                        equal(checks,'recognized complete layout',value,'J')
                    elif kind == 'complete':
                        layout,inventory=value
                        equal(checks,'private complete layout',layout,'J')
                        equal(checks,'private inventory is acquired inventory',
                            len(inventories)==1 and inventory is inventories[0],True)
                    elif kind in ('public-complete','installed-complete','coding-data'):
                        equal(checks,'independent public return',value,None if kind=='coding-data' else 'J')
                    if complete:
                        equal(checks,'one complete inventory',len(inventories),1)
                        if inventories:
                            inventory=inventories[0]
                            equal(checks,'no synthetic mapping prefix',inventory.mappings,None)
                            equal(checks,'complete queued outcomes',vars(inventory.queued),
                                dict(admitted={},runs={},owners={},callbacks={}))
                            equal(checks,'all child work uses acquired allowance',
                                all(a['identity']==id(inventory.budget) for a in allowances),True)
                            if supplied is not None:
                                equal(checks,'supplied allowance identity',inventory.budget is supplied,True)
                                equal(checks,'inherited work reaches first native acquisition',
                                    allowances[0]['counters'],prior_work)
                            if not oracle_rows:
                                equal(checks,'complete absent publication',inventory.imported,None)
                            else:
                                imported=inventory.imported
                                equal(checks,'complete original identity',imported.identity_id,identity)
                                equal(checks,'complete original version',imported.version_id,version)
                                equal(checks,'complete original command',encoded(imported.command_bytes),encoded(command))
                                equal(checks,'complete six original typed rows',
                                    [(t,{k:encoded(v) for k,v in row.items()}) for t,row in imported.rows],
                                    [(t,{k:encoded(v) for k,v in row.items()}) for t,row in oracle_rows])
                            for function in ('_read_coding_data','read_release_import',
                                    '_read_mapping_storage','source_storage','read_queued'):
                                equal(checks,'one complete acquisition '+function,
                                    sum(a['function']==function for a in acquisition),1)
                    if expect_import:
                        equal(checks, 'returned identity', value.identity_id, identity)
                        equal(checks, 'returned version', value.version_id, version)
                        equal(checks, 'original command', encoded(value.command_bytes), encoded(command))
                        actual = [(t,{k:encoded(v) for k,v in row.items()}) for t,row in value.rows]
                        oracle = [(t,{k:encoded(v) for k,v in row.items()}) for t,row in expected]
                        equal(checks, 'six full typed publication rows', actual, oracle)
                except ValueError as exc:
                    category = str(exc)
                if source_change is not None:
                    equal(checks,'retained comparison fixture change reached',changes,[encoded(source_change)])
                    equal(checks,'no partially accepted complete inventory',value is None,True)
                    # Independent pre-read whole-row oracle plus ONE intended
                    # fixture change; all other native bytes must be identical.
                    columns=next(r['columns'] for r in reference_schema if r['kind']=='table'
                                 and r['name']=='workflow_template_drafts')
                    names=[r[1] for r in columns]
                    for row in before['workflow_template_drafts']:
                        if row[names.index('id')]==encoded(draft):
                            row[names.index('author_principal')]=encoded(source_change)
                    before['workflow_template_drafts'].sort(key=repr)
                if supplied is not None and 'aggregate-candidates' in name:
                    equal(checks,'real release phase count',len(phases),2)
                    if len(phases)==2:
                        equal(checks,'real release candidate delta',phases[1]['counters']['accepted_rows']-phases[0]['counters']['accepted_rows'],12)
                        equal(checks,'real release uses caller allowance',[p['forwarded'] for p in phases],[True,True])
                check_trace(scope)
                equal(checks, 'category-only outcome', category, expected_category)
                equal(checks, 'caller transaction preserved', conn.in_transaction, transaction)
                equal(checks, 'all native rows unchanged', digest(canonical(saved_rows(conn))), digest(canonical(before)))
                equal(checks, 'no foreign key violation', list(conn.execute('PRAGMA foreign_key_check')), [])
                equal(checks, 'native integrity', list(conn.execute('PRAGMA integrity_check')), [('ok',)])
                if complete:
                    schema_after=[tuple(r) for r in conn.execute(
                        'SELECT type,name,tbl_name,sql FROM sqlite_schema ORDER BY type,name')]
                    # Full raw schema is retained ONCE in fixture_schema;
                    # whole-byte bookends avoid duplicating it per case under
                    # the unchanged expanded/compressed observation bounds.
                    equal(checks,'complete original schema unchanged',
                        digest(canonical(schema_after)),digest(canonical(schema_before)))
                    equal(checks,'independent whole native schema',digest(canonical(schema_after)),
                        digest(canonical([(r['kind'],r['name'],r['table'],r['sql']) for r in reference_schema])))
                    if not transaction:
                        equal(checks,'standalone complete BEGIN',sum(r['sql']=='BEGIN' for r in trace),1)
                        equal(checks,'standalone complete ROLLBACK',sum(r['sql']=='ROLLBACK' for r in trace),1)
                check_trace(scope)
            sibling=fresh_sibling(conn,checks) if complete else None
            result['cases'].append(dict(name=name, checks=checks, trace=trace,
                counters=None if supplied is None else vars(supplied).copy(),phases=phases,
                acquisition=acquisition,allowances=allowances,sibling=sibling,
                inventory_work=[vars(i.budget).copy() for i in inventories]))
            save()
        invalid = 'coding_release_import_history_invalid'
        conn.execute('BEGIN')
        for label,options in [('omitted',{}),('none',{'budget':None}),
                              ('supplied',{'budget':_Budget(consumed=17,accepted_rows=3,traversed=5)})]:
            observe('F8-R1c.complete.empty.'+label,
                lambda options=options:_read_workflow_schema(conn,expected_org_slug=org,**options),
                supplied=options.get('budget'),kind='complete',original_rows=[])
        conn.execute('SAVEPOINT empty_native')
        insert('audit_log',dict(timestamp=stamp,agent='synthetic',action='synthetic_observation',
            task_id=None,payload='{"note":"ordinary"}'))
        empty_budget=_Budget(consumed=17,accepted_rows=3,traversed=5)
        observe('F8-R1c.complete.empty.native.positive',
            lambda:_read_workflow_schema(conn,expected_org_slug=org,budget=empty_budget),
            supplied=empty_budget,kind='complete',original_rows=[])
        equal(result['cases'][-1]['checks'],'empty native bytes retain prior work',
            empty_budget.consumed>=17+len(stamp)+len('synthetic')+len('synthetic_observation')+len('{"note":"ordinary"}'),True)
        equal(result['cases'][-1]['checks'],'empty native candidates retain prior work',empty_budget.accepted_rows,4)
        for label,column,value,category in [
            ('storage','agent',b'synthetic','coding_authority_history_invalid'),
            ('coding','payload','{"format":"workflow-coding-unknown@1"}',
                'workflow_coding_data_unsupported: audit_log.payload; complete family reader required'),
            ('malformed','payload','{"format":"workflow-coding-',
                'workflow_coding_data_unsupported: audit_log.payload; malformed coding metadata'),
        ]:
            conn.execute('SAVEPOINT empty_refusal')
            conn.execute('UPDATE audit_log SET '+column+'=?',(value,))
            observe('F8-R1c.complete.empty.native.'+label,
                lambda:_read_workflow_schema(conn,expected_org_slug=org),category,
                kind='complete',original_rows=[])
            conn.execute('ROLLBACK TO empty_refusal');conn.execute('RELEASE empty_refusal')
        conn.execute('ROLLBACK TO empty_native');conn.execute('RELEASE empty_native')
        observe('F5-R1c.absent.omitted', lambda: read_release_import(conn,expected_org_slug=org),kind='absent')
        observe('F5-R1c.absent.none', lambda: read_release_import(conn,expected_org_slug=org,budget=None),kind='absent')
        publication()
        # Setup admission is separate: it must succeed before negative siblings.
        initial = read_release_import(conn,expected_org_slug=org)
        if initial is None:
            raise RuntimeError('positive full publication fixture unavailable')
        for label, kwargs in [('omitted',{}),('none',{'budget':None})]:
            observe('F5-R1c.valid.'+label,
                lambda kwargs=kwargs: read_release_import(conn,expected_org_slug=org,**kwargs), expect_import=True)
        b = _Budget(consumed=17,accepted_rows=3,traversed=5)
        observe('F5-R1c.supplied.prior-work',lambda: read_release_import(conn,expected_org_slug=org,budget=b),
                expect_import=True,supplied=b)
        # Only retention, NOT aggregate-composition proof or an exact-work oracle.
        equal(result['cases'][-1]['checks'],'prior byte work retained',b.consumed > 17,True)
        equal(result['cases'][-1]['checks'],'prior candidates retained',b.accepted_rows > 3,True)
        equal(result['cases'][-1]['checks'],'prior traversal retained',b.traversed > 5,True)
        observe('F5-R1c.wrong-budget',lambda: read_release_import(conn,expected_org_slug=org,budget={}),invalid)
        observe('F5-R1c.foreign-org',lambda: read_release_import(conn,expected_org_slug='foreign-org'),invalid)
        # Each controlled mutation is rolled back to the immediately admitted
        # fixture. These are SQL fixture siblings, not M1-M6 production mutants.
        for name,sql,args in [
            ('malformed', 'UPDATE workflow_template_versions SET published_by=?', ('{',)),
            ('full-row', 'UPDATE workflow_template_identities SET status=?', ('retired',)),
            ('strict-storage', 'UPDATE workflow_template_identities SET created_at=?', (stamp.encode(),)),
            ('command', 'UPDATE workflow_template_publish_operations SET request_digest=?', ('0'*64,)),
            ('journal', 'UPDATE workflow_publication_journals SET snapshot_digest=?', ('0'*64,)),
            ('admission', 'UPDATE workflow_admission_records SET admitted_by=?', ('agent:synthetic',)),
        ]:
            observe('F5-R1c.'+name+'.positive',lambda: read_release_import(conn,expected_org_slug=org),expect_import=True)
            conn.execute('SAVEPOINT negative')
            conn.execute(sql,args)
            observe('F5-R1c.'+name+'.refusal',lambda: read_release_import(conn,expected_org_slug=org),invalid)
            conn.execute('ROLLBACK TO negative'); conn.execute('RELEASE negative')
        observe('F5-R1c.extra.positive',lambda: read_release_import(conn,expected_org_slug=org),expect_import=True)
        conn.execute('SAVEPOINT extra')
        insert('workflow_template_drafts',dict(expected[1][1],id='synthetic-extra'))
        observe('F5-R1c.extra.refusal',lambda: read_release_import(conn,expected_org_slug=org),invalid)
        conn.execute('ROLLBACK TO extra');conn.execute('RELEASE extra')
        observe('F5-R1c.orphan.positive',lambda: read_release_import(conn,expected_org_slug=org),expect_import=True)
        conn.execute('SAVEPOINT orphan')
        for table in ['workflow_template_publish_operations','workflow_template_identity_versions',
                      'workflow_template_versions','workflow_template_drafts']:
            conn.execute('DELETE FROM '+table)
        observe('F5-R1c.orphan.refusal',lambda: read_release_import(conn,expected_org_slug=org),invalid)
        conn.execute('ROLLBACK TO orphan');conn.execute('RELEASE orphan')
        observe('F8-R1c.queued.caller-success',lambda: queued_storage(conn,org),kind='queued')
        qb=_Budget(consumed=17,accepted_rows=3,traversed=5)
        observe('F8-R1c.queued.supplied',lambda: queued_storage(conn,org,budget=qb),supplied=qb,kind='queued')
        # Independent saved bytes provide only a lower bound on retained work;
        # this still does NOT replace aggregate shared-allowance acceptance.
        minimum=sum(len(v.encode() if isinstance(v,str) else v) for _,row in expected
                    for v in row.values() if isinstance(v,(str,bytes)))
        equal(result['cases'][-1]['checks'],'release raw bytes charged to caller',qb.consumed >= 17+minimum,True)
        observe('F8-R1c.schema.data-false',lambda: _validate_installed(conn,expected_org_slug=org,validate_data=False),kind='schema')
        # Populated literal @1 history and ordinary native rows. The public
        # native constructors run only here, after qualification. None is an
        # authentic actor/callback/publication claim or grants live authority.
        from runtime.models import TaskRecord, TaskStatus
        from runtime.workflows.templates import WorkflowTemplateStore
        from datetime import datetime, timezone
        native_stamp = datetime(2001,2,3,4,5,6,tzinfo=timezone.utc)
        conn.commit()
        db.insert_task(TaskRecord(id='SYNTHETIC-OLD-TASK', status=TaskStatus.COMPLETED,
            assigned_agent='synthetic_worker', team='synthetic', brief='SYNTHETIC ordinary old task',
            created_at=native_stamp, updated_at=native_stamp, completed_at=native_stamp))
        db.insert_task_result(task_id='SYNTHETIC-OLD-TASK', agent='synthetic_worker',
            session_id='SYNTHETIC-OLD-SID', output_summary='SYNTHETIC old noncoding result',
            confidence_score=0, learnings='literal prose ☃\n', decision_json='{"action":"done"}')
        conn.execute('BEGIN')
        old_definition = canonical(dict(kind='product-design', schema_version=1,
            description='SYNTHETIC preserved @1 document',
            author=dict(role='product-lead',kind='agent',artifact='immutable-prd-revision'),
            reviewers=[dict(role='founder',kind='human'),dict(role='implementer',kind='agent'),
                       dict(role='tester',kind='agent')],
            approval=dict(mode='all',revision='current',required_roles=['founder','implementer','tester']),
            request_changes=dict(action='return-to-author')))
        old_ns='org/'+org+'/team/synthetic'
        old_identity=stable('workflow-template',old_ns,'legacy-document')
        old_draft='SYNTHETIC-OLD-DRAFT';old_version='SYNTHETIC-OLD-VERSION'
        old_publisher=canonical(dict(principal_kind='human',principal_id='founder',proof_kind='founder_bearer')).decode()
        old_common=dict(namespace=old_ns,template_name='legacy-document',definition_bytes=old_definition,
            definition_digest=digest(old_definition),compiler_pin='workflow-compiler@1',
            validator_pin='workflow-validator@1',source_pin='operator-input@1')
        insert('workflow_template_identities',dict(id=old_identity,namespace=old_ns,
            template_name='legacy-document',current_version=1,status='active',created_at=stamp))
        insert('workflow_template_drafts',dict(id=old_draft,**old_common,author_principal=old_publisher,created_at=stamp))
        insert('workflow_template_versions',dict(id=old_version,draft_id=old_draft,version=1,
            **old_common,published_by=old_publisher,published_at=stamp))
        insert('workflow_template_identity_versions',dict(template_identity_id=old_identity,version=1,
            template_version_id=old_version,content_digest=digest(old_definition)))
        def old_history() -> str:
            # The actual version-aware template parser validates literal @1.
            conn.row_factory=sqlite3.Row
            try:
                template=WorkflowTemplateStore(db).get(org_slug=org,namespace=old_ns,
                    template_name='legacy-document',version=1)
            finally:
                conn.row_factory=None
            if template.definition_bytes != old_definition or template.compiler_pin != 'workflow-compiler@1':
                raise RuntimeError('old @1 fixture setup did not retain original bytes/pins')
            queued_storage(conn,org)
            return _validate_installed(conn,expected_org_slug=org,validate_data=False)
        with independent_input():
            if old_history() != 'J':
                raise RuntimeError('populated native/document fixture not admitted')
        observe('F8-R1c.noncoding-old-history',old_history,kind='schema')
        result['populated_history_rows']=saved_rows(conn)

        for label,options in [('omitted',{}),('none',{'budget':None}),
                              ('supplied',{'budget':_Budget(consumed=17,accepted_rows=3,traversed=5)})]:
            observe('F8-R1c.complete.'+label,
                lambda options=options:_read_workflow_schema(conn,expected_org_slug=org,**options),
                supplied=options.get('budget'),kind='complete')
        observe('F8-R1c.complete.wrong-budget',
            lambda:_read_workflow_schema(conn,expected_org_slug=org,budget={}),
            'workflow_coding_data_invalid_budget',kind='complete')
        for label,action,kind in [
            ('installed',lambda:_validate_installed(conn,expected_org_slug=org,validate_data=False),'installed-complete'),
            ('public',lambda:validate_workflow_schema(conn,expected_org_slug=org),'public-complete'),
            ('data',lambda:validate_coding_data(conn,layout='J',expected_org_slug=org),'coding-data'),
            ('retained.positive',lambda:_read_workflow_schema(conn,expected_org_slug=org),'complete'),
        ]:
            observe('F8-R1c.complete.'+label,action,kind=kind)
        changed_provenance=json.loads(pub)
        changed_provenance['publisher']['principal_id']='synthetic-other'
        for label,changed in [('storage',pub.encode()),('provenance',canonical(changed_provenance).decode()),
                              ('canonical',' '+pub)]:
            observe('F8-R1c.complete.retained.'+label,
                lambda:_read_workflow_schema(conn,expected_org_slug=org),
                'workflow_source_data_corrupt',kind='complete',source_change=changed)

        # A real caller transaction/sentinel, on independently committed input.
        observe('F8-R1c.complete.caller-success',
            lambda:_read_workflow_schema(conn,expected_org_slug=org),kind='complete')
        conn.execute('SAVEPOINT complete_refusal')
        conn.execute("UPDATE workflow_template_versions SET published_by='{' ")
        observe('F8-R1c.complete.caller-refusal',
            lambda:_read_workflow_schema(conn,expected_org_slug=org),invalid,kind='complete')
        conn.execute('ROLLBACK TO complete_refusal');conn.execute('RELEASE complete_refusal')

        # The SAME ordinary connection across standalone read snapshots. The
        # controller commits supported fixture writes BETWEEN reads; no product
        # result supplies expected rows or repairs a product transaction.
        with independent_input() as fresh_input:
            conn.commit()
            observe_input('F8-R1c.complete.fresh.before',
                lambda:_read_workflow_schema(conn,expected_org_slug=org),None,False,None,'complete')
            later='2001-02-03T04:05:07.000000Z'
            later_provenance=json.loads(pub);later_provenance['created_at']=later
            later_pub=canonical(later_provenance).decode()
            later_rows=[(t,dict(row)) for t,row in expected]
            later_rows[0][1]['created_at']=later
            later_rows[1][1].update(author_principal=later_pub,created_at=later)
            later_rows[2][1].update(published_by=later_pub,published_at=later)
            conn.execute('UPDATE workflow_template_identities SET created_at=? WHERE id=?',(later,identity))
            conn.execute('UPDATE workflow_template_drafts SET author_principal=?,created_at=? WHERE id=?',
                (later_pub,later,draft))
            conn.execute('UPDATE workflow_template_versions SET published_by=?,published_at=? WHERE id=?',
                (later_pub,later,version))
            conn.commit()
            observe_input('F8-R1c.complete.fresh.changed',
                lambda:_read_workflow_schema(conn,expected_org_slug=org),None,False,None,'complete',
                original_rows=later_rows)
            conn.execute("UPDATE workflow_template_versions SET published_by='{' WHERE id=?",(version,))
            conn.commit()
            observe_input('F8-R1c.complete.fresh.corrupt',
                lambda:_read_workflow_schema(conn,expected_org_slug=org),invalid,False,None,'complete')
            for row in result['cases'][-3:]:
                row['fixture_input']=fresh_input

        # Natural prior reader work: never preset an exhausted counter or
        # change an allowance. Every component has an immediately admitted
        # fresh-budget positive sibling. The expected limits are the original
        # contract constants, not inferred from a second product call.
        def tokens(value: object) -> int:
            # Independent grammar arithmetic for canonical fixture inputs:
            # braces/brackets, separators, keys and scalar values, no regex
            # from the product or second reader invocation as numeric oracle.
            if type(value) is dict:
                return 2+max(0,len(value)-1)+2*len(value)+sum(tokens(v) for v in value.values())
            if type(value) is list:
                return 2+max(0,len(value)-1)+sum(tokens(v) for v in value)
            return 1
        def release_work(proof: dict) -> dict:
            cap=proof['admission']['capture']
            snap=base64.b64decode(cap['snapshot_bytes_base64'])
            raw_command=base64.b64decode(proof['command_bytes_base64'])
            published=canonical(proof)
            fixed=[(t,dict(row)) for t,row in expected]
            fixed[1][1]['author_principal']=published.decode()
            fixed[2][1]['published_by']=published.decode()
            journal=dict(id='synthetic-journal',namespace=pin['namespace'],generation=1,expected_generation=0,
                snapshot_bytes=snap,snapshot_digest=digest(snap),publisher='human:founder',
                publisher_invocation='synthetic-invocation',profile_fence=0,state='cache_installed',
                recovery_owner='synthetic-owner',file_phase_owner=None)
            def raw_bytes(row: dict) -> int:
                return sum(len(v.encode() if type(v) is str else v) for v in row.values() if type(v) in (str,bytes))
            admission_strings=sum(len(v.encode()) for v in admission.values() if type(v) is str)
            journal_strings=sum(len(v.encode()) for v in journal.values() if type(v) is str)
            copies=(21*len(published)+16*len(proof['command_bytes_base64'])+
                12*len(raw_command)+12*len(cap['snapshot_bytes_base64'])+10*len(snap)+
                2*(admission_strings+journal_strings)+raw_bytes(admission)+raw_bytes(journal)+
                5*len(definition)+4096+
                sum(raw_bytes(row)+4*sum(len(v) for v in row.values() if type(v) is str) for _,row in fixed))
            visits=(1+3+6*(tokens(proof)+1)+1+2*(tokens(json.loads(raw_command))+1)+
                1+8*(tokens(json.loads(snap))+1)+1+2*(tokens(json.loads(raw_command))+1)+2+
                38+21+3+2*(tokens(json.loads(definition))+1)+192+
                sum(4*len(row)+4 for _,row in fixed)+2)  # two empty SELECT probes, one visit each
            return dict(accepted_rows=12,consumed=copies,traversed=visits)
        native_rows=list(conn.execute('SELECT * FROM task_results WHERE decision_json IS NOT NULL'))
        native_bytes=sum(len(v.encode() if type(v) is str else v) for row in native_rows for v in row if type(v) in (str,bytes))
        native_visits=sum(len(row)+1 for row in native_rows)
        current_proof=provenance
        def aggregate(label: str, target: str) -> None:
            with watched_scope(conn, 'A-traversal' if target == 'traversed' else 'A-bytes') as (scope,spy):
                trace=scope['trace']
                checks=[]; components=[]
                before=saved_rows(conn); transaction=conn.in_transaction
                b=_Budget()
                expected_work=release_work(current_proof)
                category=None
                # queued's real mapping/import/native owners first, followed
                # by ordinary release reads using the identical actual budget.
                for sequence in range(24):
                    positive=read_release_import(conn,expected_org_slug=org)
                    if positive is None or positive.command_bytes != command:
                        raise RuntimeError('aggregate positive setup unavailable')
                    prior=vars(b).copy()
                    try:
                        if sequence == 0:
                            with independent_input() as fixture_input:
                                with spy.fixture_connection(conn):
                                    queued_before=saved_rows(conn)
                                    queued_transaction=conn.in_transaction
                                    try:
                                        queued_storage(conn,org,budget=b)
                                    finally:
                                        # Record actual destructive effects on
                                        # the SAME connection before disposal.
                                        equal(checks,'queued phase caller transaction preserved',conn.in_transaction,queued_transaction)
                                        equal(checks,'queued phase native rows unchanged',digest(canonical(saved_rows(conn))),digest(canonical(queued_before)))
                                        equal(checks,'queued phase no foreign key violation',list(conn.execute('PRAGMA foreign_key_check')),[])
                                        equal(checks,'queued phase native integrity',list(conn.execute('PRAGMA integrity_check')),[('ok',)])
                            result.setdefault('aggregate_fixture_inputs',{})[label]=fixture_input
                        else:
                            read_release_import(conn,expected_org_slug=org,budget=b)
                    except ValueError as exc:
                        category=str(exc)
                    components.append(dict(sequence=sequence,before=prior,after=vars(b).copy(),category=category))
                    if category is None:
                        delta={k:vars(b)[k]-prior[k] for k in vars(b)}
                        oracle=dict(expected_work)
                        if sequence == 0:
                            oracle['accepted_rows']+=len(native_rows)
                            oracle['consumed']+=native_bytes
                            oracle['traversed']+=5+native_visits
                        equal(checks,'component '+str(sequence)+' independently accounted work',delta,oracle)
                    if category is not None:
                        break
                equal(checks,'exact category-only refusal',category,invalid)
                equal(checks,'whole allowance never reset',all(
                    c['after'][key]>=c['before'][key] for c in components for key in vars(b)),True)
                equal(checks,'at least two individually admitted components',len(components)>=2,True)
                equal(checks,'caller transaction preserved',conn.in_transaction,transaction)
                equal(checks,'whole native typed rows preserved',digest(canonical(saved_rows(conn))),digest(canonical(before)))
                if target == 'traversed':
                    equal(checks,'original traversal allowance crossed',b.traversed,16385)
                    equal(checks,'byte allowance not first',b.consumed<=16777216,True)
                elif target == 'consumed':
                    # _copies checks before increment: the failing reservation
                    # can leave consumed below the limit. Its concrete next
                    # input-derived reservation is separately retained.
                    equal(checks,'traversal allowance not first',b.traversed<=16384,True)
                    equal(checks,'prior bytes actually consumed',b.consumed>8388608,True)
                else:
                    if b.accepted_rows <=8192:
                        result['unproved'][label]=dict(reason='Traversal/byte refusal precedes candidate target; no candidate aggregate PASS',
                            target=8193,observed=vars(b).copy(),components=len(components),
                            source_bounds='8192 candidates,16384 visits,16MiB; each release component has 12 candidates plus lexical/column visits; singleton fixed-release grammar; queued native rows visit every column')
                    equal(checks,'attempt reached actual earlier traversal bound',b.traversed,16385)
                check_trace(scope)
            result['cases'].append(dict(name=label,checks=checks,components=components,expected_work=expected_work,
                trace=trace,counters=vars(b).copy(),target=target))
            save()
        aggregate('F5-R1c.aggregate-traversal','traversed')
        # Two native ordinary-history components each fit independently;
        # together they expose which unchanged whole-call limit stops first.
        # All result rows are synthetic fixture data via the ordinary native
        # caller-owned constructor, never report-completion or genuine proof.
        def candidate_group(label: str) -> None:
            for i in range(400):
                db._insert_task_result(task_id='SYNTHETIC-OLD-TASK',agent='synthetic_worker',
                    session_id='SYNTHETIC-'+label+'-'+str(i),output_summary='SYNTHETIC ordinary history',
                    confidence_score=0,decision_json='{"action":"done"}')
        for label in ('a','b'):
            conn.execute('SAVEPOINT candidate_component')
            candidate_group(label)
            cb=_Budget()
            observe('F5-R1c.aggregate-candidates.component-'+label+'.positive',
                lambda:queued_storage(conn,org,budget=cb),supplied=cb,kind='queued')
            equal(result['cases'][-1]['checks'],'input-derived cumulative candidates',cb.accepted_rows,12+401)
            equal(result['cases'][-1]['checks'],'input-derived cumulative traversal',cb.traversed,1982+19*401)
            conn.execute('ROLLBACK TO candidate_component');conn.execute('RELEASE candidate_component')
        conn.execute('SAVEPOINT candidate_combined')
        candidate_group('a');candidate_group('b')
        cb=_Budget()
        observe('F5-R1c.aggregate-candidates',lambda:queued_storage(conn,org,budget=cb),
            'coding_authority_history_invalid',supplied=cb)
        equal(result['cases'][-1]['checks'],'actual earlier traversal bound',cb.traversed,16385)
        equal(result['cases'][-1]['checks'],'input-derived partial candidate accumulation',cb.accepted_rows,771)
        equal(result['cases'][-1]['checks'],'candidate guard not reached',cb.accepted_rows<8193,True)
        equal(result['cases'][-1]['checks'],'bytes not first',cb.consumed<=16777216,True)
        result['cases'][-1]['input_components']=dict(first=400,second=400,existing=1,native_columns=18)
        result['outstanding_broader_targets']['candidate8193'].update(
            observed=vars(cb).copy(),input_components=[400,400],
            grammar='ordinary18-column task_results plus fixed release; no global infeasibility claim')
        conn.execute('ROLLBACK TO candidate_combined');conn.execute('RELEASE candidate_combined')
        # A fixed larger VALID v1 snapshot exercises actual nested base64,
        # canonical UTF8 and hex copies without adding lexical member visits.
        conn.execute('SAVEPOINT larger_snapshot')
        large_snapshot=json.loads(snapshot)
        large_snapshot['agents']=[dict(name='synthetic_manager',role='manager',team='synthetic',status='active',
            executor='synthetic',model=None,repos={},allow_rules=['x'*20000],
            description_digest='0'*64,system_prompt_digest='0'*64)]
        large_snapshot['teams']=[dict(name='synthetic',manager='synthetic_manager',workers=[])]
        large_snapshot['active_policy_selectors']=[dict(team='synthetic',selector=None)]
        large_raw=canonical(large_snapshot);large_digest=digest(large_raw)
        large_provenance=json.loads(pub)
        large_capture=large_provenance['admission']['capture']
        large_capture['pin']['snapshot_digest']=large_digest
        large_capture['snapshot_bytes_base64']=base64.b64encode(large_raw).decode()
        large_capture['profile_evidence']['ready'].update(snapshot_digest=large_digest,snapshot_bytes_hex=large_raw.hex())
        large_capture['profile_evidence']['pointer']['snapshot_digest']=large_digest
        large_pub=canonical(large_provenance).decode()
        for table,column in [('workflow_template_versions','published_by'),('workflow_template_drafts','author_principal')]:
            conn.execute('UPDATE '+table+' SET '+column+'=? WHERE namespace=?',(large_pub,namespace))
        conn.execute('UPDATE workflow_publication_journals SET snapshot_bytes=?,snapshot_digest=? WHERE id=?',
                     (large_raw,large_digest,'synthetic-journal'))
        conn.execute('UPDATE workflow_authority_pointers SET snapshot_digest=? WHERE namespace=?',
                     (large_digest,pin['namespace']))
        # Input-derived conservative copies LOWER bound, independent of the
        # observed counter. No second invocation supplies a numeric oracle.
        result['aggregate_byte_inputs']=dict(snapshot_bytes=len(large_raw),provenance_bytes=len(large_pub.encode()),
            snapshot_base64_bytes=len(large_capture['snapshot_bytes_base64']),snapshot_hex_bytes=len(large_raw.hex()),
            per_component_minimum=12*len(large_pub.encode())+8*len(large_raw)+12*len(large_capture['snapshot_bytes_base64']))
        current_proof=large_provenance
        aggregate('F5-R1c.aggregate-intermediate-bytes','consumed')
        conn.execute('ROLLBACK TO larger_snapshot');conn.execute('RELEASE larger_snapshot')
        def snapshot_variant(rules: list[str]) -> dict:
            value=json.loads(large_raw)
            value['agents'][0]['allow_rules']=rules
            raw=canonical(value);sdigest=digest(raw)
            proof=json.loads(pub);cap=proof['admission']['capture']
            cap['pin']['snapshot_digest']=sdigest
            cap['snapshot_bytes_base64']=base64.b64encode(raw).decode()
            cap['profile_evidence']['ready'].update(snapshot_digest=sdigest,snapshot_bytes_hex=raw.hex())
            cap['profile_evidence']['pointer']['snapshot_digest']=sdigest
            return proof
        # Independent full-boundary arithmetic for a deliberately small retained
        # fixture: fixed release, ordinary native results and no old document
        # rows. The earlier complete/old-history cases retain those rows. This
        # removes no case/assertion and avoids a parser-dependent size oracle.
        scalar_names=set(('format compiler_pin validator_pin source_pin action owner kind '
            'artifact_kind source_kind business_purpose identity release_identity namespace '
            'authority_namespace org_namespace').split())
        whole_tables={t for t,_ in expected}|{'workflow_authorization_revisions',
            'workflow_active_authorizations','workflow_coding_mappings','workflow_coding_runs',
            'workflow_coding_attempts','workflow_coding_events'}
        scan_tables=[]
        for item in reference_schema:
            if item['kind']!='table' or not (item['name'].startswith('workflow_')
                    or item['name'] in ('tasks','task_results','audit_log')):
                continue
            columns=[r[1] for r in item['columns']]
            selected=columns if item['name'] in whole_tables else [c for c in columns
                if c in scalar_names or ((c.endswith(('_bytes','_json')) or c=='payload')
                    and c not in ('submission_bytes','candidate_bytes'))]
            if selected:
                scan_tables.append(item['name'])
        def raw_size(row: object) -> int:
            values=row.values() if type(row) is dict else row
            return sum(len(v.encode() if type(v) is str else v) for v in values if type(v) in (str,bytes))
        def decoded_visits(value: object) -> int:
            if type(value) is dict:
                return 1+sum(1+len(k)+(len(v) if type(v) is str else 0)+decoded_visits(v)
                             for k,v in value.items())
            if type(value) is list:
                return 1+sum(decoded_visits(v) for v in value)
            return 1
        def hook_visits(value: object) -> int:
            if type(value) is dict:
                return 1+sum(hook_visits(v)+1+len(k)+(len(v) if type(v) is str else 0)+decoded_visits(v)
                             for k,v in value.items())
            if type(value) is list:
                return sum(hook_visits(v) for v in value)
            return 0
        def metadata_visits(value: object) -> int:
            return hook_visits(value)+decoded_visits(value)
        def schema_work(proof: dict, native: list) -> dict:
            work=release_work(proof)
            snap=base64.b64decode(proof['admission']['capture']['snapshot_bytes_base64'])
            published=canonical(proof).decode()
            fixed=[(t,dict(row)) for t,row in expected]
            fixed[1][1]['author_principal']=published
            fixed[2][1]['published_by']=published
            fields=sum(len(row) for _,row in fixed)
            # Empty setup2/mapping1/source1/queued4 probes; whole selected
            # table terminal pages; six complete original-row comparisons;
            # source-data draft/version comparisons on BOTH outer passes.
            work['accepted_rows']+=8+2*len(native)
            work['consumed']+=(sum(raw_size(r) for r in native)+sum(raw_size(r) for _,r in fixed)
                +2*len(pin['namespace'])+len(snap)+sum(len(r[-1].encode()) for r in native))
            work['traversed']+=(8+sum(len(r)+1 for r in native)+len(scan_tables)
                +sum(1+len(row)+fields for _,row in fixed)+4*fields
                +3+5+metadata_visits(json.loads(snap))
                +sum(3+metadata_visits(json.loads(r[-1])) for r in native))
            return work
        def schema_variant(name: str, proof: dict, category: str | None, summary_size: int | None = None) -> None:
            conn.execute('SAVEPOINT schema_variant')
            try:
                # Disposable input-only simplification, preserved by rollback.
                for table,key,value in [('workflow_template_identity_versions','template_identity_id',old_identity),
                        ('workflow_template_versions','id',old_version),('workflow_template_drafts','id',old_draft),
                        ('workflow_template_identities','id',old_identity)]:
                    conn.execute('DELETE FROM '+table+' WHERE '+key+'=?',(value,))
                cap=proof['admission']['capture'];raw=base64.b64decode(cap['snapshot_bytes_base64'])
                published=canonical(proof).decode()
                for table,column in [('workflow_template_versions','published_by'),('workflow_template_drafts','author_principal')]:
                    conn.execute('UPDATE '+table+' SET '+column+'=? WHERE namespace=?',(published,namespace))
                conn.execute('UPDATE workflow_publication_journals SET snapshot_bytes=?,snapshot_digest=? WHERE id=?',
                             (raw,digest(raw),'synthetic-journal'))
                conn.execute('UPDATE workflow_authority_pointers SET snapshot_digest=? WHERE namespace=?',
                             (digest(raw),pin['namespace']))
                if summary_size is not None:
                    for index in range(16):
                        db._insert_task_result(task_id='SYNTHETIC-OLD-TASK',agent='synthetic_worker',
                            session_id='SYNTHETIC-BYTES-'+str(index),output_summary='x'*summary_size,
                            confidence_score=0,decision_json='{"action":"done"}')
                native=list(conn.execute('SELECT * FROM task_results WHERE decision_json IS NOT NULL'))
                # decision_json is explicitly selected below rather than assumed
                # to be the final native column in the independent input oracle.
                names=[r[1] for r in conn.execute('PRAGMA table_xinfo(task_results)')]
                decision_index=names.index('decision_json')
                native=[tuple(v for i,v in enumerate(r) if i!=decision_index)+(r[decision_index],) for r in native]
                work=schema_work(proof,native)
                # Each full component must fit BEFORE the composed boundary.
                value=read_release_import(conn,expected_org_slug=org)
                if value is None or value.command_bytes != command:
                    raise RuntimeError('schema aggregate release component unavailable')
                component=_Budget()
                queued=queued_storage(conn,org,budget=component)
                expected_component=release_work(proof)
                expected_component['accepted_rows']+=len(native)
                expected_component['consumed']+=sum(raw_size(r) for r in native)
                expected_component['traversed']+=5+sum(len(r)+1 for r in native)
                if vars(queued)!=dict(admitted={},runs={},owners={},callbacks={}):
                    raise RuntimeError('schema aggregate native component unavailable')
                budget=_Budget()
                observe(name,lambda:_read_workflow_schema(conn,expected_org_slug=org,budget=budget),
                        category,kind='complete',supplied=budget)
                row=result['cases'][-1]
                row.update(independent_release_work=release_work(proof),independent_complete_work=work,
                    native_input=dict(rows=len(native),bytes=sum(raw_size(r) for r in native),summary_size=summary_size))
                equal(row['checks'],'independently admitted full native component',vars(component),expected_component)
                if category is None:
                    equal(row['checks'],'independent complete row/copy/traversal arithmetic',vars(budget),work)
                elif summary_size is None:
                    equal(row['checks'],'independent traversal crossing',work['traversed']>16384,True)
                    equal(row['checks'],'actual original traversal guard',budget.traversed,16385)
                    equal(row['checks'],'bytes are not first',budget.consumed<=16777216,True)
                else:
                    equal(row['checks'],'independent byte crossing',work['consumed']>16777216,True)
                    equal(row['checks'],'traversal is not first',budget.traversed<=16384,True)
            finally:
                conn.execute('ROLLBACK TO schema_variant');conn.execute('RELEASE schema_variant')
        # Select sizes only by fixed input arithmetic, never reader counters or
        # a binary search of production outcomes. One rule separates siblings.
        native_columns=[r[1] for r in conn.execute('PRAGMA table_xinfo(task_results)')]
        decision_index=native_columns.index('decision_json')
        native_for_work=[tuple(v for i,v in enumerate(r) if i!=decision_index)+(r[decision_index],)
            for r in conn.execute('SELECT * FROM task_results WHERE decision_json IS NOT NULL')]
        traversal_size=next(n for n in range(1,1001)
            if schema_work(snapshot_variant(['x']*n),native_for_work)['traversed']>16384)
        schema_variant('F8-R1c.schema.aggregate-traversal.positive',snapshot_variant(['x']*(traversal_size-1)),None)
        schema_variant('F8-R1c.schema.aggregate-traversal.refusal',snapshot_variant(['x']*traversal_size),'coding_authority_history_invalid')
        # Free native output text is charged without JSON-character traversal.
        # Sixteen individually bounded values make the complete byte guard
        # reachable while release AND full queued/native components still fit.
        conn.execute('SAVEPOINT byte_size_input')
        for index in range(16):
            db._insert_task_result(task_id='SYNTHETIC-OLD-TASK',agent='synthetic_worker',
                session_id='SYNTHETIC-BYTES-'+str(index),output_summary='',
                confidence_score=0,decision_json='{"action":"done"}')
        byte_native=[tuple(v for i,v in enumerate(r) if i!=decision_index)+(r[decision_index],)
            for r in conn.execute('SELECT * FROM task_results WHERE decision_json IS NOT NULL')]
        base_bytes=schema_work(provenance,byte_native)['consumed']
        summary_size=(16777216-base_bytes)//16
        if not 0<summary_size<1048576:
            raise RuntimeError('independent native byte fixture per-value bound')
        conn.execute('ROLLBACK TO byte_size_input');conn.execute('RELEASE byte_size_input')
        schema_variant('F8-R1c.schema.aggregate-bytes.positive',provenance,None,summary_size)
        schema_variant('F8-R1c.schema.aggregate-bytes.refusal',provenance,'coding_authority_history_invalid',summary_size+1)
        conn.commit()
        # Caller-owned noncoding work uses a real native table; exact actual
        # schema columns are inspected rather than creating a harness store.
        conn.execute('BEGIN')
        insert('org_settings',dict(section='synthetic-reader',value_json='{}',updated_at=stamp,updated_by='synthetic'))
        observe('F8-R1c.queued.caller-uncommitted',lambda: queued_storage(conn,org),kind='queued')
        observe('F8-R1c.queued.wrong-budget',lambda: queued_storage(conn,org,budget={}), 'coding_start_history_invalid')
        conn.execute('SAVEPOINT refusal')
        conn.execute("UPDATE workflow_template_versions SET published_by='{' ")
        observe('F8-R1c.queued.caller-refusal',lambda: queued_storage(conn,org),invalid)
        observe('F8-R1c.schema.refusal',lambda: _validate_installed(conn,expected_org_slug=org,validate_data=False),invalid)
        conn.execute('ROLLBACK TO refusal');conn.execute('RELEASE refusal')
        conn.rollback()
        observe('F5-R1c.transaction-required',lambda: read_release_import(conn,expected_org_slug=org),invalid)
        checks=[];samples=[];spy=TraceSpy(conn);scope=None
        previous=signal.getsignal(signal.SIGALRM);profile=sys.getprofile()
        def standalone_observations(before: dict) -> None:
            equal(checks,'standalone owns and closes its read',conn.in_transaction,False)
            equal(checks,'standalone BEGIN count',sum(r['sql']=='BEGIN' for r in scope['trace']),1)
            equal(checks,'standalone ROLLBACK count',sum(r['sql']=='ROLLBACK' for r in scope['trace']),1)
            equal(checks,'standalone native rows unchanged',digest(canonical(saved_rows(conn))),digest(canonical(before)))
            equal(checks,'standalone no foreign key violation',list(conn.execute('PRAGMA foreign_key_check')),[])
            equal(checks,'standalone native integrity',list(conn.execute('PRAGMA integrity_check')),[('ok',)])
        def standalone_observer(frame: object, event: str, arg: object) -> None:
            if event=='call' and frame.f_code.co_name in ('saved_rows','queued_storage','read_release_import','standalone_observations'):
                if len(samples)>=2048:
                    raise SupportIncomplete('SUPPORT standalone profile bound')
                timer=timer_observation()
                samples.append(dict(function=frame.f_code.co_name,**timer))
                equal(checks,'standalone live timer at '+frame.f_code.co_name,0<timer['remaining']<=2,True)
        try:
            # Observer is outside the scope under test, including installation.
            sys.setprofile(standalone_observer)
            with support_scope(spy) as scope:
                before=saved_rows(conn)
                standalone=queued_storage(conn,org)
                standalone_observations(before)
                check_trace(scope)
                equal(checks,'standalone snapshot entries',sum(s['function']=='saved_rows' for s in samples),2)
                for function in ('queued_storage','read_release_import','standalone_observations'):
                    equal(checks,'standalone actual '+function+' entry',any(s['function']==function for s in samples),True)
                equal(checks,'standalone actual callbacks retained',len(scope['trace']),spy.calls)
                equal(checks,'standalone bounded ledger',len(scope['trace'])<=2048,True)
            after=cleanup_observations(conn,spy,scope,previous,checks)
        finally:
            sys.setprofile(profile)
            # Immutable containment follows actual cleanup observation.
            signal.setitimer(signal.ITIMER_REAL,0)
            signal.signal(signal.SIGALRM,previous)
            conn.set_trace_callback(None)
        sibling=fresh_sibling(conn,checks)
        result['cases'].append(dict(name='F8-R1c.queued.standalone',checks=checks,trace=scope['trace'],
            samples=samples,after=after,events=spy.events,callbacks=spy.calls,sibling=sibling))
    finally:
        db.close()
    module_paths={name:getattr(module,'__file__',None) for name,module in list(sys.modules.items())}
    for name,file in module_paths.items():
        if file and file != '/fixture/driver.py' and not file.startswith(('/src/','/deps/','/usr/lib/python3.14/')):
            raise RuntimeError('unadmitted imported module origin: '+name)
    result['module_paths']=module_paths
    result['imports'] = sorted(imported)
    result['opened'] = sorted(opened)
    names=[case['name'] for case in result['cases']]
    result['missing_cases']=sorted(set(cfg['required_cases'])-set(names))
    result['duplicate_cases']=sorted(name for name in set(names) if names.count(name)!=1)
    result['unknown_cases']=sorted(set(names)-set(cfg['required_cases']))
    support_names=[row['name'] for row in result['support']]
    result['missing_support']=sorted(set(cfg['native_support_cases'])-set(support_names))
    result['duplicate_support']=sorted(n for n in set(support_names) if support_names.count(n)!=1)
    result['unknown_support']=sorted(set(support_names)-set(cfg['native_support_cases']))
    result['assigned_completion']=dict(reader=not result['unproved'] and not result['missing_cases'] and not result['duplicate_cases'] and not result['unknown_cases'],
        support=not result['missing_support'] and not result['duplicate_support'] and not result['unknown_support'])
    result['complete'] = all(result['assigned_completion'].values())
except ConstructorContractFailure as exc:
    result['contract_error']=str(exc)
except BaseException as exc:
    import traceback
    result['setup_error'] = type(exc).__name__+': '+str(exc)
    result['setup_traceback'] = traceback.format_exc(limit=12)
finally:
    result['qualified'] = qualified
    save()
'''


def sha(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def canonical_path(path: Path) -> Path:
    if not path.is_absolute() or path.resolve() != path:
        raise ValueError('absolute canonical nonsymlink path required')
    return path


def run(argv: list[str], **kwargs: object) -> bytes:
    return subprocess.check_output(argv, env={'PATH':'/usr/bin:/bin'}, timeout=4, **kwargs)


def git(root: Path, *args: str) -> str:
    return run(['/usr/bin/git','-C',str(root),*args]).decode().strip()


def source_identity(root: Path, head: str) -> dict:
    canonical_path(root)
    if git(root,'rev-parse','HEAD') != head or git(root,'status','--porcelain','--untracked-files=all'):
        raise ValueError('source must be clean at expected HEAD')
    registered = git(root,'worktree','list','--porcelain').splitlines()
    if 'worktree '+str(root) not in registered or not (root/'.git').is_file():
        raise ValueError('registered non-primary source worktree required')
    if (root/INVENTORY).stat().st_size > LIMIT:
        raise ValueError('source inventory size bound')
    manifest = json.loads((root/INVENTORY).read_text())
    if set(manifest) != {'format','modules','resources','external_roots','literal_dynamic_edges'}:
        raise ValueError('inventory grammar')
    if manifest['format'] != 'coding-reader-source-inventory@1':
        raise ValueError('inventory version')
    if len(manifest['modules']) != 212:
        raise ValueError('unreviewed module inventory expansion')
    files = {item['path']:None for item in manifest['modules'].values()}
    files.update(manifest['resources'])
    # Script/inventory/lock are pinned too, but not exposed as product imports.
    for rel in [INVENTORY,'scripts/verify_coding_readers.py','pyproject.toml','uv.lock']:
        files[rel] = None
    dynamic = []
    for rel, expected in files.items():
        path = root/rel
        if not rel or Path(rel).is_absolute() or '..' in Path(rel).parts or path.resolve() != path:
            raise ValueError('uncontained inventory path')
        raw = path.read_bytes()
        if raw != run(['/usr/bin/git','-C',str(root),'show',head+':'+rel]):
            raise ValueError('uncommitted source bytes: '+rel)
        actual = hashlib.sha256(raw).hexdigest()
        if expected is not None and actual != expected:
            raise ValueError('inventory hash mismatch: '+rel)
        files[rel] = actual
        if rel in {v['path'] for v in manifest['modules'].values()}:
            tree = ast.parse(raw,filename=rel)
            module=next(k for k,v in manifest['modules'].items() if v['path'] == rel)
            package=module if rel.endswith('/__init__.py') else module.rpartition('.')[0]
            for node in ast.walk(tree):
                names=[]
                if isinstance(node,ast.Import):
                    names=[a.name for a in node.names]
                elif isinstance(node,ast.ImportFrom):
                    base=node.module or ''
                    if node.level:
                        parts=package.split('.')
                        if node.level > len(parts):
                            raise ValueError('escaping relative import')
                        base='.'.join(parts[:len(parts)-node.level+1]+([base] if base else []))
                    names=[base]
                for name in names:
                    top=name.split('.')[0]
                    if top in ('runtime','cli') and name not in manifest['modules']:
                        raise ValueError('unlisted static product edge: '+name)
                    if top not in ('runtime','cli',*manifest['external_roots']) and top not in sys.stdlib_module_names:
                        raise ValueError('unlisted external edge: '+name)
                if isinstance(node,ast.Call) and (
                    isinstance(node.func,ast.Name) and node.func.id == '__import__'
                    or isinstance(node.func,ast.Attribute) and node.func.attr == 'import_module'):
                    if not node.args or not isinstance(node.args[0],ast.Constant) or type(node.args[0].value) is not str:
                        raise ValueError('unknown dynamic source edge')
                    dynamic.append({'path':rel,'target':node.args[0].value})
    if sorted(dynamic,key=repr) != sorted(manifest['literal_dynamic_edges'],key=repr):
        raise ValueError('dynamic edge inventory mismatch')
    for module in manifest['modules']:
        parts=module.split('.')
        for i in range(1,len(parts)):
            ancestor='.'.join(parts[:i])
            if ancestor not in manifest['modules'] or manifest['modules'][ancestor]['path'] != '/'.join(parts[:i])+'/__init__.py':
                raise ValueError('missing package ancestor')
    return dict(head=head,tree=git(root,'rev-parse','HEAD^{tree}'),files=files,inventory=manifest)


def admit_loader(value: dict) -> str:
    files=value['files']; natives=value['native_libraries']
    if LOADER_ALIAS in files or LOADER_ALIAS in natives:
        raise ValueError('loader noncanonical')
    selected=LOADER
    if selected not in files or selected not in natives:
        raise ValueError('loader membership')
    if Path(selected).resolve() != Path(selected):
        raise ValueError('loader noncanonical')
    if sha(Path(selected)) != files[selected]:
        raise ValueError('loader digest')
    return selected


def loader_record(args: argparse.Namespace) -> int:
    """Fresh stdlib controller; its synchronous audit veto is immutable."""
    name=args.case_set.removeprefix('loader-')
    root=canonical_path(args.source_root)
    source=source_identity(root,args.expected_head)
    original=json.loads(args.environment_manifest.read_text())
    opening_environment=environment_identity(args.environment_manifest,source['files']['uv.lock'],root)
    # Establish the actual interpreter audit-before-spawn boundary as source.
    popen=Path(subprocess.__file__)
    popen_bytes=popen.read_bytes()
    body=popen_bytes[popen_bytes.index(b'            sys.audit("subprocess.Popen"'):]
    if not (body.index(b'sys.audit(')<body.index(b'self._posix_spawn(')<body.index(b'_fork_exec(')):
        raise RuntimeError('audit-before-OS-spawn unavailable')
    out=canonical_path(args.output_dir)
    if out.exists():
        raise ValueError('fresh loader controller output required')
    out.mkdir(mode=0o700)
    value=json.loads(json.dumps(original))
    if name=='L1':
        value['files'].pop(LOADER)
        value['native_libraries'].remove(LOADER)
    elif name=='L2':
        value['files'][LOADER]='0'*64 if value['files'][LOADER]!='0'*64 else '1'*64
    elif name=='L3':
        value['files'][LOADER_ALIAS]=value['files'].pop(LOADER)
        value['native_libraries']=[LOADER_ALIAS if p==LOADER else p for p in value['native_libraries']]
    manifest=out/'environment.json'
    manifest.write_text(json.dumps(value))
    probes=[]; attempts=[]; products=[]
    def veto(event: str, operands: tuple) -> None:
        # NEVER mutate this independent veto in loader support variants.
        if event=='subprocess.Popen':
            executable,argv,*_=operands
            if executable=='/usr/bin/bwrap':
                attempts.append(list(argv))
                raise RuntimeError('SUPPORT immutable bwrap audit veto')
            if executable!='/usr/bin/git':
                raise RuntimeError('SUPPORT non-git process refused')
            probes.append(list(argv))
        if event=='import' and operands[0].split('.')[0] in ('runtime','cli'):
            products.append(dict(event=event,name=operands[0]))
            raise RuntimeError('SUPPORT product import refused')
        if event=='exec' and '/runtime/' in getattr(operands[0],'co_filename',''):
            products.append(dict(event=event,path=operands[0].co_filename))
            raise RuntimeError('SUPPORT product exec refused')
    sys.addaudithook(veto)  # before the ACTUAL verifier main, not a fake planner
    original_argv=sys.argv
    sys.argv=[str(root/'scripts/verify_coding_readers.py'), '--source-root',str(root),
        '--expected-head',args.expected_head,'--case-set','release-accounting',
        '--history-manifest',str(args.history_manifest),'--environment-manifest',str(manifest),
        '--output-dir',str(out/'verifier')]
    try:
        # The inner actual main needs its L campaign suppressed to avoid recursion.
        raw_exit=main(loader_vetoed=True)
    finally:
        sys.argv=original_argv
    receipt=json.loads((out/'verifier/receipt.json').read_text())
    if source_identity(root,args.expected_head)!=source or environment_identity(args.environment_manifest,source['files']['uv.lock'],root)!=opening_environment:
        raise RuntimeError('loader controller bookend movement')
    checks=[]
    def check(label: str, actual: object, expected: object) -> None:
        checks.append(dict(assertion=label,actual=actual,expected=expected,passed=actual==expected))
    check('raw verifier setup exit',raw_exit,2)
    check('bwrap audit attempts',len(attempts),1 if name=='L0' else 0)
    expected={'L0':'RuntimeError: SUPPORT immutable bwrap audit veto','L1':'ValueError: loader membership',
        'L2':'ValueError: loader digest','L3':'ValueError: loader noncanonical'}[name]
    check('exact setup cause',receipt.get('setup_error'),expected)
    check('no product execution',products,[])
    # Project the ACTUAL emitted admission record. Presence is independent of
    # value/type, so null/empty/malformed never masquerades as ABSENT.
    admission=dict(present='environment_before' in receipt)
    if admission['present']:
        admitted=receipt['environment_before']
        admission['value_type']=type(admitted).__name__
        if type(admitted) is dict:
            files=admitted.get('files');natives=admitted.get('native_libraries')
            admission.update(admitted_loader=admitted.get('admitted_loader'),
                files_type=type(files).__name__,native_libraries_type=type(natives).__name__,
                fixed_files_member=type(files) is dict and LOADER in files,
                fixed_native_member=type(natives) is list and LOADER in natives,
                fixed_digest=files.get(LOADER) if type(files) is dict else None,
                alias_files_member=type(files) is dict and LOADER_ALIAS in files,
                alias_native_member=type(natives) is list and LOADER_ALIAS in natives,
                alias_digest=files.get(LOADER_ALIAS) if type(files) is dict else None)
        else:
            admission['value']=admitted
    check('emitted admitted environment present',admission['present'],name=='L0')
    if name=='L0':
        admitted=receipt.get('environment_before')
        check('emitted environment is object',type(admitted) is dict,True)
        admitted=admitted if type(admitted) is dict else {}
        check('emitted exact canonical admitted loader',admitted.get('admitted_loader'),LOADER)
        files=admitted.get('files');natives=admitted.get('native_libraries')
        check('emitted loader files membership',type(files) is dict and LOADER in files,True)
        check('emitted loader native membership',type(natives) is list and LOADER in natives,True)
        check('emitted loader independently authenticated digest',files.get(LOADER) if type(files) is dict else None,original['files'][LOADER])
        check('original loader actual bytes',sha(Path(LOADER)),original['files'][LOADER])
    if name=='L0' and len(attempts)==1:
        argv=attempts[0]
        mounts=[argv[i+1:i+3] for i,x in enumerate(argv) if x=='--ro-bind']
        check('canonical loader mounted', [LOADER,LOADER] in mounts,True)
        check('alias derived from admitted loader',[LOADER,LOADER_ALIAS] in mounts,True)
        check('loader in both collections',LOADER in original['files'] and LOADER in original['native_libraries'],True)
        check('actual admitted loader digest',sha(Path(LOADER)),original['files'][LOADER])
    record=dict(name=name,checks=checks,raw_verifier_exit=raw_exit,raw_setup_error=receipt.get('setup_error'),
        emitted_admission=admission,
        bwrap_attempts=attempts,product_attempts=products,git_source_probes=probes,
        interpreter_sha256=sha(Path(sys.executable)),subprocess_sha256=sha(popen),
        audit_before_os_spawn=True,controller_exit=1 if any(not c['passed'] for c in checks) else 0)
    (out/'loader-record.json').write_text(json.dumps(record,indent=2))
    return record['controller_exit']


def loader_campaign(args: argparse.Namespace, out: Path, started: float) -> list:
    records=[]
    for name in ('L0','L1','L2','L3'):
        target=out/name
        argv=[sys.executable,'-I','-S',str(args.source_root/'scripts/verify_coding_readers.py'),
            '--source-root',str(args.source_root),'--expected-head',args.expected_head,
            '--case-set','loader-'+name,'--history-manifest',str(args.history_manifest),
            '--environment-manifest',str(args.environment_manifest),'--output-dir',str(target)]
        process=None
        try:
            with (out/(name+'.stdout')).open('wb') as stdout,(out/(name+'.stderr')).open('wb') as stderr:
                process=subprocess.Popen(argv,env={},stdin=subprocess.DEVNULL,stdout=stdout,stderr=stderr,
                    close_fds=True,start_new_session=True,preexec_fn=limits)
                code=process.wait(timeout=max(.01,55-(time.monotonic()-started)))
            if code not in (0,1) or not (target/'loader-record.json').exists():
                raise RuntimeError('loader controller incomplete: '+name)
            record=json.loads((target/'loader-record.json').read_text())
            if record['controller_exit']!=code:
                raise RuntimeError('loader controller exit mismatch')
            records.append(record)
        finally:
            if process is not None and process.poll() is None:
                os.killpg(process.pid,signal.SIGKILL)
                process.wait()
    return records


def environment_identity(path: Path, lock: str, root: Path) -> dict:
    canonical_path(path)
    if path.stat().st_size > LIMIT:
        raise ValueError('environment manifest size bound')
    value=json.loads(path.read_text())
    if set(value) != {'format','lock_sha256','environment_root','interpreter','stdlib','site_packages',
                      'files','native_libraries','distributions','wheels','metadata'}:
        raise ValueError('closed environment manifest required')
    if value['format'] != 'coding-reader-frozen-environment@1' or value['lock_sha256'] != lock:
        raise ValueError('environment lock/version mismatch')
    admitted_loader=admit_loader(value)
    envroot=canonical_path(Path(value['environment_root']))
    if '.happyranch/task-tmp/TASK-' not in str(envroot):
        raise ValueError('environment must be task-owned scratch')
    site=canonical_path(Path(value['site_packages']))
    if envroot not in site.parents or site.name != 'site-packages':
        raise ValueError('private site-packages required')
    python=canonical_path(Path(value['interpreter']))
    stdlib=canonical_path(Path(value['stdlib']))
    if not str(python).startswith('/usr/bin/python3.14') or stdlib != Path('/usr/lib/python3.14'):
        raise ValueError('this qualified layout requires system Python3.14')
    if not value['distributions'] or not value['wheels'] or value['metadata']['version'][:2] != [3,14]:
        raise ValueError('frozen binary-wheel/interpreter evidence missing')
    installed={re.sub('[-_.]+','-',d.metadata['Name']).lower():d.version
               for d in importlib.metadata.distributions(path=[str(site)])}
    if installed != value['distributions']:
        raise ValueError('distribution metadata movement')
    locked=tomllib.loads((root/'uv.lock').read_text())['package']
    allowed={(p['name'],p['version']) for p in locked if p['name'] != 'happyranch'}
    if any((name,version) not in allowed for name,version in installed.items()):
        raise ValueError('unlocked distribution')
    direct=tomllib.loads((root/'pyproject.toml').read_text())['project']['dependencies']
    if any(re.match(r'[A-Za-z0-9_.-]+',d)[0].lower().replace('_','-') not in installed for d in direct):
        raise ValueError('production dependency absent')
    wheel_metadata={re.sub('[-_.]+','-',d.metadata['Name']).lower():sha(Path(d._path)/'WHEEL')
                    for d in importlib.metadata.distributions(path=[str(site)])}
    if wheel_metadata != value['wheels']:
        raise ValueError('installed wheel metadata mismatch')
    archives=value['metadata'].get('wheel_archives')
    if type(archives) is not dict or set(archives) != set(installed):
        raise ValueError('complete locked wheel archives required; metadata alone is insufficient')
    covered=set()
    for name,artifact in archives.items():
        if set(artifact) != {'path','sha256','filename'}:
            raise ValueError('closed wheel artifact required')
        archive=canonical_path(Path(artifact['path']))
        if (envroot.parent not in archive.parents or archive.name != artifact['filename']
                or archive.suffix != '.whl' or archive.stat().st_size > 20*1024*1024
                or sha(archive) != artifact['sha256']):
            raise ValueError('wheel artifact identity mismatch')
        package=next(p for p in locked if (p['name'],p['version']) == (name,installed[name]))
        if not any(w['hash'] == 'sha256:'+artifact['sha256']
                   and w['url'].rsplit('/',1)[-1] == archive.name for w in package['wheels']):
            raise ValueError('wheel archive absent from pinned lock')
        with zipfile.ZipFile(archive) as wheel:
            entries=wheel.infolist()
            if len(entries)>4096 or sum(e.file_size for e in entries)>128*1024*1024:
                raise ValueError('wheel expansion bound')
            for entry in entries:
                if entry.is_dir():
                    continue
                member=Path(entry.filename)
                if member.is_absolute() or '..' in member.parts or '.data' in entry.filename:
                    raise ValueError('unsupported wheel data path')
                target=site/member
                canonical_path(target)
                covered.add(str(target))
                if entry.filename.endswith('.dist-info/RECORD'):
                    continue
                if not target.is_file() or target.read_bytes() != wheel.read(entry):
                    raise ValueError('installed dependency differs from locked archive')
    files=value['files']
    actual_site={str(p):sha(p) for p in site.rglob('*') if p.is_file()}
    extras=set(actual_site)-covered
    if any(Path(name).relative_to(site).as_posix() not in {'_virtualenv.py','_virtualenv.pth'}
           and re.fullmatch(r'[^/]+[.]dist-info/(INSTALLER|REQUESTED|RECORD|uv_cache[.]json)',
                            Path(name).relative_to(site).as_posix()) is None for name in extras):
        raise ValueError('unarchived environment file; ambient/generated code refused')
    if any(p.is_symlink() for p in site.rglob('*')) or {k:v for k,v in files.items() if k.startswith(str(site)+'/')} != actual_site:
        raise ValueError('environment inventory/symlink drift')
    for name,expected in files.items():
        file=Path(name) if name == admitted_loader else canonical_path(Path(name))
        allowed=file in (python,envroot/'pyvenv.cfg') or stdlib in file.parents or site in file.parents or name in value['native_libraries']
        if not allowed or (name != admitted_loader and sha(file) != expected):
            raise ValueError('environment file mismatch/unadmitted path')
    if str(python) not in files or not all(x in files for x in value['native_libraries']):
        raise ValueError('missing interpreter/native hashes')
    for native in value['native_libraries']:
        if native != admitted_loader and not native.startswith(('/usr/lib/x86_64-linux-gnu/','/usr/lib64/')):
            raise ValueError('unadmitted native library root')
    for name in value['distributions']:
        if name.lower() in {'pytest','pytest-asyncio','happyranch','pyinstaller'}:
            raise ValueError('development/project environment refused')
    return dict(manifest_sha256=sha(path),admitted_loader=admitted_loader,**value)


def limits() -> None:
    resource.setrlimit(resource.RLIMIT_CPU,(40,40))
    resource.setrlimit(resource.RLIMIT_AS,(768*1024*1024,)*2)
    resource.setrlimit(resource.RLIMIT_FSIZE,(LIMIT,LIMIT))
    resource.setrlimit(resource.RLIMIT_NOFILE,(128,128))
    resource.setrlimit(resource.RLIMIT_CORE,(0,0))


def main(*, loader_vetoed: bool = False) -> int:
    started=time.monotonic()
    parser=argparse.ArgumentParser(description=__doc__,allow_abbrev=False)
    parser.add_argument('--source-root',required=True,type=Path)
    parser.add_argument('--expected-head',required=True)
    parser.add_argument('--case-set',required=True,choices=['release-accounting','reader-support',*(f'loader-L{i}' for i in range(4))])
    parser.add_argument('--history-manifest',required=True,type=Path)
    parser.add_argument('--environment-manifest',required=True,type=Path)
    parser.add_argument('--output-dir',required=True,type=Path)
    args=parser.parse_args()
    if not sys.flags.isolated or not sys.flags.no_site or sys.version_info[:2] != (3,14):
        parser.error('Python3.14 -I -S required')
    if not re.fullmatch('[0-9a-f]{40}',args.expected_head):
        parser.error('full lowercase source commit required')
    if Path(__file__).resolve() != args.source_root/'scripts/verify_coding_readers.py':
        parser.error('execute the verifier from the selected source worktree')
    out=canonical_path(args.output_dir)
    if (out.exists() or not out.parent.is_dir() or args.source_root == out or args.source_root in out.parents
            or out.parent.stat().st_uid != os.getuid() or '.happyranch/task-tmp/TASK-' not in str(out)):
        parser.error('fresh output outside source with existing canonical parent required')
    if args.case_set.startswith('loader-'):
        return loader_record(args)
    out.mkdir(mode=0o700)
    receipt=dict(argv=sys.argv,cwd=os.getcwd(),parent_python=sys.version,complete=False,case_set=args.case_set,
                 origin='SYNTHETIC',required_cases=REQUIRED_CASES,required_support=SUPPORT_CASES,children_reaped=True)
    process=None
    before=None
    environment=None
    input_hash=None
    try:
        before=source_identity(args.source_root,args.expected_head)
        receipt['source_before']=before
        canonical_path(args.history_manifest)
        if args.history_manifest.stat().st_size > 8192:
            raise ValueError('input manifest size bound')
        input_hash=sha(args.history_manifest)
        history=json.loads(args.history_manifest.read_text())
        if (set(history) != {'format','origin','org_slug','fixture','source_head'}
                or history['format'] != 'coding-reader-history-input@1' or history['origin'] != 'SYNTHETIC'
                or history['fixture'] != 'full-j-release-v1' or history['source_head'] != args.expected_head
                or history['org_slug'] != 'synthetic-reader'):
            raise ValueError('only the closed allocated SYNTHETIC fixture input is supported')
        receipt['input']=dict(sha256=input_hash,manifest=history)
        if not loader_vetoed:
            receipt['loader_support']=loader_campaign(args,out,started)
        environment=environment_identity(args.environment_manifest,before['files']['uv.lock'],args.source_root)
        receipt['environment_before']=environment
        fixture=out/'fixture';fixture.mkdir(mode=0o700)
        result=out/'result';result.mkdir(mode=0o700)
        closed_env={'HOME':'/work/home','PATH':'/no-executables','LC_ALL':'C.UTF-8','PWD':'/work'}
        product_files={k:v for k,v in before['files'].items() if k.startswith(('runtime/','cli/'))}
        cfg=dict(org_slug=history['org_slug'],env=closed_env,required_cases=REQUIRED_CASES,
                 host_ns={n:os.readlink('/proc/self/ns/'+n) for n in NAMESPACES},
                 source_files=product_files,modules=before['inventory']['modules'],
                 native_support_cases=[f'R{i}' for i in range(8)]+['A-traversal','A-bytes'])
        (fixture/'driver.py').write_text(DRIVER)
        (fixture/'config.json').write_text(json.dumps(cfg))
        command=['/usr/bin/bwrap','--unshare-all','--new-session','--die-with-parent','--unshare-cgroup','--cap-drop','ALL','--clearenv']
        for key,value in closed_env.items():
            command+=['--setenv',key,value]
        # Exact immutable file mounts: no /usr, host home or runtime tree bind.
        mounts={environment['interpreter']:environment['interpreter']}
        for name in environment['files']:
            path=Path(name)
            if Path(environment['stdlib']) in path.parents or name in environment['native_libraries']:
                mounts[name]=name
        for name,destination in mounts.items():
            command+=['--ro-bind',name,destination]
        command+=['--ro-bind',environment['admitted_loader'],LOADER_ALIAS]
        command+=['--ro-bind',environment['site_packages'],'/deps']
        for rel in sorted(product_files):
            command+=['--ro-bind',str(args.source_root/rel),'/src/'+rel]
        for destination,operand in environment['metadata'].get('native_aliases',{}).items():
            if (operand not in environment['native_libraries']
                    or not destination.startswith('/usr/lib/x86_64-linux-gnu/')
                    or Path(destination).name in ('','..') or '/..' in destination):
                raise ValueError('unadmitted native alias')
            command+=['--ro-bind',operand,destination]
        command+=['--proc','/proc','--dev','/dev','--tmpfs','/tmp','--tmpfs','/work',
                  '--ro-bind',str(fixture),'/fixture','--bind',str(result),'/result','--remount-ro','/','--chdir','/work',
                  environment['interpreter'],'-I','-S','-B','/fixture/driver.py']
        receipt['command']=command
        with (out/'stdout').open('wb') as stdout,(out/'stderr').open('wb') as stderr:
            process=subprocess.Popen(command,env={},stdin=subprocess.DEVNULL,stdout=stdout,stderr=stderr,
                                     close_fds=True,start_new_session=True,preexec_fn=limits)
            receipt['children_reaped']=False
            receipt['native_exit']=process.wait(timeout=max(0.01,55-(time.monotonic()-started)))
            receipt['children_reaped']=True
        if receipt['native_exit'] != 0:
            raise RuntimeError('native/isolation/fixture setup failure, never RED')
        observation_path=result/'observations.json.gz'
        with gzip.open(observation_path,'rb') as stream:
            raw=stream.read(16*1024*1024+1)
        if len(raw)>16*1024*1024:
            raise RuntimeError('expanded observation bound')
        observation=json.loads(raw)
        # The complete raw schema, rows, queries and assertions remain in the
        # bounded compressed original artifact. Receipt keeps a concise index.
        receipt['observations']={k:v for k,v in observation.items()
            if k not in ('fixture_schema','initial_rows','populated_history_rows','cases','module_paths','opened')}
        receipt['observation_artifact']=dict(path=str(observation_path),sha256=sha(observation_path),
            compressed_bytes=observation_path.stat().st_size,expanded_bytes=len(raw))
        receipt['observations']['cases']=[dict(name=c['name'],checks=len(c['checks']),
            passed=all(x['passed'] for x in c['checks']),counters=c.get('counters'),
            components=c.get('components')) for c in observation['cases']]
        if not observation.get('qualified') or observation.get('setup_error'):
            raise RuntimeError('qualification/import/fixture incomplete, never RED')
        receipt['failed_assertions']=[dict(case=row['name'],**c) for row in observation['cases']
                                      for c in row['checks'] if not c['passed']]
        receipt['failed_assertions'] += [dict(case=row['name'],**c) for row in receipt['loader_support'] for c in row['checks'] if not c['passed']]
        receipt['failed_assertions'] += [dict(case=row['name'],**c) for row in observation['support'] for c in row['checks'] if not c['passed']]
        support_names=[r['name'] for r in receipt['loader_support']+observation['support']]
        receipt['missing_support']=sorted(set(SUPPORT_CASES)-set(support_names))
        receipt['unknown_support']=sorted(set(support_names)-set(SUPPORT_CASES))
        receipt['duplicate_support']=sorted(n for n in set(support_names) if support_names.count(n)!=1)
        receipt['complete']=(observation['complete'] and not observation['unproved'] and
            not receipt['missing_support'] and not receipt['unknown_support'] and not receipt['duplicate_support'])
    except Exception as exc:
        receipt['setup_error']=type(exc).__name__+': '+str(exc)
    finally:
        if process is not None and process.poll() is None:
            os.killpg(process.pid,signal.SIGKILL)
            process.wait()
            receipt['children_reaped']=True
        # End identities are checked even after native/setup refusal. No partial
        # success survives source/dependency/input movement.
        try:
            if before is not None:
                receipt['source_after']=source_identity(args.source_root,args.expected_head)
                if receipt['source_after'] != before:
                    raise ValueError('source movement')
            if environment is not None and environment_identity(args.environment_manifest,before['files']['uv.lock'],args.source_root) != environment:
                raise ValueError('environment movement')
            if input_hash is not None and sha(args.history_manifest) != input_hash:
                raise ValueError('input movement')
        except Exception as exc:
            receipt['setup_error']='identity recheck: '+str(exc)
            receipt['complete']=False
        receipt['elapsed_seconds']=time.monotonic()-started
        if receipt['elapsed_seconds'] >= 60:
            receipt['complete']=False
            receipt['setup_error']='whole-invocation deadline exhausted'
        payload=json.dumps(receipt,indent=2)
        if len(payload.encode()) > LIMIT:
            receipt=dict(complete=False,children_reaped=receipt['children_reaped'],
                         setup_error='receipt exceeds 2MiB; retained raw native files remain setup evidence')
            payload=json.dumps(receipt)
        (out/'receipt.json').write_text(payload)
    code=2 if receipt.get('setup_error') or not receipt['complete'] else (1 if receipt.get('failed_assertions') else 0)
    print(json.dumps(dict(exit=code,complete=receipt['complete'],receipt=str(out/'receipt.json'),
                          setup_error=receipt.get('setup_error'),unproved=receipt.get('observations',{}).get('unproved',{}),
                          missing_cases=receipt.get('observations',{}).get('missing_cases',sorted(set(REQUIRED_CASES)-
                              {c['name'] for c in receipt.get('observations',{}).get('cases',[])})))))
    return code


def deadline(signum: int, frame: object) -> None:
    raise TimeoutError('60s whole-invocation bound')


if __name__ == '__main__':
    signal.signal(signal.SIGALRM,deadline)
    signal.setitimer(signal.ITIMER_REAL,60)
    try:
        sys.exit(main())
    finally:
        signal.setitimer(signal.ITIMER_REAL,0)
