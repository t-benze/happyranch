"""Naming-only fixtures, exact observations and before-spawn process ownership.

No fixture models naming behavior. Product outcomes come from OrgState, real
routes, SQLite transactions and shipping authority/profile writer seams.
"""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from dataclasses import replace
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import signal
import sqlite3
import time

import pytest
from runtime.config import Settings
from runtime.daemon.org_state import OrgState
from runtime.infrastructure.database import Database
from runtime.infrastructure import workflow_schema
from runtime.orchestrator._paths import OrgPaths
from runtime.orchestrator.agent_def import AgentDef, render_agent_text
from runtime.orchestrator import prompt_loader
from runtime.orchestrator.teams import TeamsRegistry
from runtime.runtime import RuntimeDir
from runtime.identities.registry import refresh_names, rename_owner

ASSETS = Path(__file__).parents[1] / 'helpers' / 'identity_names'
MANIFEST = json.loads((ASSETS / 'case-manifest.json').read_text())


def emit_case(node, case, observation, sessions=()):
    """Called only after assertions AND exact child cleanup; never on setup refusal."""
    local_root = observation.pop('receipt_root')
    root = Path('/tmp/task/receipts')
    if not root.is_dir():
        # Local separately authorized execution retains evidence under basetemp.
        root = Path(local_root)
        root.mkdir(exist_ok=True, parents=True)
    destination = root / (node + '-' + case + '.json')
    data = json.loads(destination.read_text()) if destination.exists() else None
    if data is None:
        data = {'completed_case_ids': [], 'refused_setup_case_ids': [],
                'sessions_by_case': {}, 'observations_by_case': {}, 'cleanup_verified_absent': True}
    full = node + ':' + case
    assert full in MANIFEST['domains'][manifest_key(node)]
    assert full not in data['completed_case_ids']
    assert len(sessions) <= 16
    data['completed_case_ids'].append(full)
    data['sessions_by_case'][full] = list(sessions)
    data['observations_by_case'][full] = observation
    assert len({s for ids in data['sessions_by_case'].values() for s in ids}) <= 64
    raw = json.dumps(data, sort_keys=True).encode()
    assert len(raw) <= 1024 * 1024
    temp = destination.with_suffix('.tmp')
    temp.write_bytes(raw)
    os.replace(temp, destination)


def manifest_key(node):
    keys = [key for key in MANIFEST['domains'] if key.endswith('::' + node)]
    assert len(keys) == 1, (node, keys)
    return keys[0]


def cases(node, selected=None):
    available = [c.split(':', 1)[1] for c in MANIFEST['domains'][manifest_key(node)]]
    if selected is None:
        return available  # Historical direct nodes remain available, unselected.
    assert selected and len(set(selected)) == len(selected)
    assert set(selected) <= set(available), (node, selected)
    return list(selected)



def seed(root, *, worker='maker', lifecycle='active', executor='claude'):
    paths = OrgPaths(root)
    paths.agents_dir.mkdir(parents=True, exist_ok=True)
    teams = TeamsRegistry({}, root=root)
    teams.add_team('engineering', 'manager')
    for name, role in (('manager', 'manager'), (worker, 'worker')):
        definition = AgentDef(name=name, team='engineering', role=role, executor=executor,
                              allow_rules=(), repos={}, enrolled_by='founder', enrolled_at_task=None,
                              enrolled_at=None, system_prompt=f'You are {name}.', description=name, model=None)
        directory = paths.agents_dir
        if name == worker:
            if lifecycle == 'pending': directory = paths.pending_agents_dir
            elif lifecycle == 'terminated': directory /= '_terminated'
            if lifecycle != 'terminated': teams.add_worker('engineering', name)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f'{name}.md').write_text(render_agent_text(definition))
    (root / 'workspaces' / worker).mkdir(parents=True)
    (root / 'workspaces' / worker / 'owned.txt').write_bytes(b'workspace-original\n')
    return paths


def _execute_release_ddl(conn: sqlite3.Connection, ddl: str) -> None:
    """Execute independent literal DDL without splitting comments or committing."""
    statement = ''
    for line in ddl.splitlines(keepends=True):
        statement += line
        if sqlite3.complete_statement(statement):
            conn.execute(statement)
            statement = ''
    if statement.strip():
        raise ValueError('incomplete_identity_names_release_ddl')


def release_seed(path, history, layout, *, names=False, slug='alpha'):
    literals = json.loads((ASSETS / 'release_literals.json').read_text())
    if history != 'fresh':
        conn = sqlite3.connect(path)
        try:
            if history == 'v0': conn.executescript(literals['histories'][0])
            else:
                for sql in literals['histories'][1]: conn.execute(sql)
                if history == 'v2-organic':
                    for table, column in (('tasks', 'note'), ('task_results', 'verdict'), ('thread_participants', 'last_resumed_seq')):
                        conn.execute(f'ALTER TABLE "{table}" DROP COLUMN "{column}"')
            conn.commit()
        finally: conn.close()
    db = Database(path)
    try:
        with db.workflow_schema_transaction() as conn:
            _execute_release_ddl(conn, literals['CANONICAL_WORKFLOW_DDL'])
            stamp='2026-10-09T00:00:00+00:00'
            event_digest=hashlib.sha256(json.dumps(literals['install_event'],sort_keys=True,separators=(',',':')).encode()).hexdigest()
            conn.execute('INSERT INTO workflow_adapter_versions VALUES (1)')
            conn.execute('INSERT INTO workflow_cutover_state VALUES (1,1,?,?,?,?,?,?)',
                ('installed_legacy_only','workflow_cutover_reconciler',1,None,None,stamp))
            conn.execute('INSERT INTO workflow_cutover_events VALUES (?,?,?,?,?,?,?)',
                ('cutover-event-1',1,None,'installed_legacy_only',None,event_digest,stamp))
        if layout in ('E', 'G'):
            with db.workflow_schema_transaction() as conn:
                # Independent literal reference: no candidate layout/DDL input.
                _execute_release_ddl(conn, literals['CANONICAL_WORKFLOW_DRAFT_DDL'])
                conn.execute('INSERT INTO workflow_draft_adapter_versions VALUES (1)')
                if layout == 'G':
                    conn.execute('DROP TABLE workflow_events')
                    conn.execute('DROP TABLE workflow_submissions')
                    _execute_release_ddl(conn, literals['CANONICAL_WORKFLOW_SUBMISSION_DDL'])
                    conn.execute('CREATE INDEX workflow_events_instance_idx ON workflow_events(instance_id)')
                    conn.execute('INSERT INTO workflow_submission_schema_versions VALUES (1)')
        if names:
            with db.workflow_schema_transaction() as conn:
                _execute_release_ddl(conn, (ASSETS / 'naming-v1.sql').read_text())
                conn.execute('INSERT INTO identity_name_schema VALUES (1,1,?)', (slug,))
                for key, life, label, rev in (('founder','founder','Founder',9), ('manager','active','Manager',4), ('maker','active','Sam',7)):
                    kind = 'founder' if key == 'founder' else 'agent'
                    conn.execute('INSERT INTO identity_name_owners VALUES (?,?,?,?,?)', (kind,key,life,label,rev))
                    for token in {key, label.lower()}:
                        conn.execute('INSERT INTO identity_name_claims VALUES (?,?,?,?,?)', (token,kind,key,int(token==key),1))
    finally: db.close()


@contextmanager
def owned_org(tmp_path, *, worker='maker', lifecycle='active', history='fresh', layout='F', names=False, slug='alpha'):
    runtime = RuntimeDir.init(tmp_path / 'runtime')
    root = runtime.orgs_dir / slug
    seed(root, worker=worker, lifecycle=lifecycle)
    release_seed(OrgPaths(root).db_path, history, layout, names=names, slug=slug)
    from runtime.daemon.state import DaemonState
    state = DaemonState.from_runtime(runtime, Settings())
    org = state.orgs[slug]
    assert org.naming_readiness == 'ready', org.naming_diagnostic
    org.sessions.set_active('TASK-OWNER', 'manager', 'sess-owner')
    try: yield org
    finally: org.close()


def snapshot(org):
    with org.db.coherent_read_view() as conn:
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        rows = {table: tuple(tuple(row) for row in conn.execute(f'SELECT rowid,* FROM "{table}" ORDER BY rowid')) for table in tables}
        schema = tuple(tuple(row) for row in conn.execute('SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name'))
    files = {}
    for base in (org.root / 'org', org.root / 'workspaces'):
        for path in sorted(base.rglob('*')):
            st = path.lstat()
            files[str(path.relative_to(org.root))] = (st.st_mode, os.readlink(path) if path.is_symlink() else path.read_bytes() if path.is_file() else None)
    return {'schema': schema, 'rows': rows, 'files': files,
            'teams': tuple((t,org.teams.manager_for_team(t)) for t in org.teams.teams())}


def naming_snapshot(org):
    return {k: v for k, v in snapshot(org)['rows'].items() if k.startswith('identity_name_')}


def digest(value):
    return hashlib.sha256(repr(value).encode()).hexdigest()


def record(node, case, org, tmp_path, **facts):
    observed = snapshot(org)
    identity_rows={k:v for k,v in observed['rows'].items() if k.startswith('identity_name_')}
    def bounded_value(value):
        if isinstance(value,str) and len(value.encode())>128:
            return {'type':'text','bytes':len(value.encode()),'sha256':hashlib.sha256(value.encode()).hexdigest()}
        return value
    facts['identity_observations']={table:{'count':len(rows), 'all_rows_sha256':digest(rows),
        'bounded_rows':[[bounded_value(v) for v in row] for row in (rows if len(rows)<=8 else rows[:4]+rows[-4:])],
        'full_rows_retained':len(rows)<=8} for table,rows in identity_rows.items()}
    facts['canonical_file_observations']={path:{'mode':values[0],
        'content_sha256':hashlib.sha256(values[1]).hexdigest() if isinstance(values[1],bytes) else None}
        for path,values in observed['files'].items() if path.endswith(('maker.md','manager.md','teams.yaml'))}
    facts.update(snapshot_sha256=digest(observed), naming_sha256=digest(identity_rows),
                 readiness=org.naming_readiness, diagnostic=org.naming_diagnostic,
                 receipt_root=str(tmp_path / 'receipts'))
    # The case has reached its terminal assertion; close the exact fixture DB
    # before claiming its resource cleanup receipt. Context cleanup is idempotent.
    org.close()
    emit_case(node, case, facts)


async def rename(org, label, *, name='maker', kind='agent', revision=None):
    if revision is None:
        row = org.db.execute('SELECT revision FROM identity_name_owners WHERE kind=? AND canonical_id=?', (kind,name)).fetchone()
        revision = row[0]
    return await rename_owner(org, kind=kind, canonical_id=name, label=label, expected_revision=revision)


async def enroll(org, name):
    from runtime.daemon.routes.agents import ManageAgentBody, manage_agent
    return await manage_agent(org.slug, ManageAgentBody(action='enroll', name=name, task_id='TASK-OWNER', session_id='sess-owner', description=name, system_prompt='pending'), org)


async def founder_create(org, name, role='worker'):
    from runtime.daemon.routes.agents import FounderCreateAgentBody, founder_create_agent
    kw = {'team':'engineering'} if role == 'worker' else {'new_team': 'new-team'}
    return await founder_create_agent(org.slug, FounderCreateAgentBody(name=name,role=role,description=name,system_prompt=name,**kw), org)


async def terminate(org, name='maker'):
    from runtime.daemon.routes.agents import ManageAgentBody, manage_agent
    return await manage_agent(org.slug, ManageAgentBody(action='terminate',name=name,task_id='TASK-OWNER',session_id='sess-owner'),org)


class ConnectionFault:
    """Test-side statement fault, wrapping the actual shipping connection."""
    def __init__(self, conn, needle, *, death=None, after=True, arm_on=None):
        self.conn, self.needle, self.death, self.after = conn, needle, death, after
        self.fired = False
        self.arm_on, self.armed = arm_on, arm_on is None
    def __getattr__(self, name): return getattr(self.conn, name)
    def execute(self, sql, *args):
        if self.arm_on and self.arm_on in sql: self.armed = True
        match = self.armed and not self.fired and self.needle in sql
        if match and not self.after: self.hit()
        result = self.conn.execute(sql, *args)
        if match and self.after: self.hit()
        return result
    def commit(self):
        if self.armed and self.needle == 'COMMIT' and not self.fired:
            if not self.after: self.hit()
            result = self.conn.commit()
            self.hit()
            return result
        return self.conn.commit()
    def hit(self):
        self.fired = True
        if self.death is not None:
            self.death.send({'cut':self.needle, 'pid':os.getpid()})
            self.death.recv()  # Only parent kill/reap; no time-based cut.
        raise RuntimeError('injected statement fault: '+self.needle)


@contextmanager
def owned_child(target, args, ownership_path):
    """Parent records bounds/owner BEFORE spawn and reaps only its exact child."""
    context = multiprocessing.get_context('spawn')
    parent, child = context.Pipe()
    owner = {'parent_pid':os.getpid(), 'deadline_seconds':30,
             'target':target.__module__+'.'+target.__name__,
             'resource_enforcement':'outer disposable cgroup/tmpfs; pipe metadata is observation-only'}
    from tests.helpers.integration_stub_guard.guard import manifest
    binding = manifest()
    owner['source_sha'] = binding['revision']
    owner['parent_manifest_sha256'] = hashlib.sha256(Path(os.environ['HAPPYRANCH_TEST_PARENT_MANIFEST']).read_bytes()).hexdigest()
    ownership_path.write_text(json.dumps(owner))
    process = context.Process(target=target,args=(*args,child))
    process.start()
    child.close()
    owner['child_pid'] = process.pid
    ownership_path.write_text(json.dumps(owner))
    try:
        yield process, parent
    finally:
        if process.is_alive(): process.kill()
        process.join(5)
        assert not process.is_alive() and process.exitcode is not None
        parent.close()
        owner['exit'] = process.exitcode
        owner['reaped'] = True
        owner['cleanup_verified_absent'] = True
        ownership_path.write_text(json.dumps(owner))


def seed_cleanup(org):
    from datetime import datetime,timezone
    from runtime.models import ScheduleRecord,ScheduleKind
    org.db.schedules.insert(ScheduleRecord(id='SCHEDULE-001',agent_name='maker',kind=ScheduleKind.ONE_SHOT,
        fire_at=datetime(2030,1,1,tzinfo=timezone.utc),normalized_brief='owned cleanup',source_instruction='owned fixture'))


def crash_case(root_text,cut,pipe):
    """Child pauses at a real shipping write; parent hard-kills then fresh-loads."""
    from unittest.mock import patch
    from runtime.daemon.routes import agents,settings
    from runtime.identities import registry as names
    from runtime.identities import schema
    from runtime.config import Settings
    root=Path(root_text)
    org=OrgState.load(slug='alpha',root=root,settings=Settings())
    org.sessions.set_active('TASK-OWNER','manager','sess-owner')
    def stop():
        observed=snapshot(org)
        pipe.send({'cut':cut,'pid':os.getpid(),'snapshot':observed,'readiness':org.naming_readiness})
        pipe.recv()
        raise AssertionError('parent did not kill the owned child')
    from contextlib import ExitStack
    with ExitStack() as patches:
        def wrap(owner,attr,predicate,after=True,fail=False):
            original=getattr(owner,attr)
            def hooked(*a,**k):
                match=predicate(a,k)
                if match and not after:stop()
                if fail and match:raise OSError('owned cleanup/compensation fault')
                result=original(*a,**k)
                if match and after:stop()
                return result
            patches.enter_context(patch.object(owner,attr,hooked))
        operation=None
        if cut in ('C01','C02'):
            with org.db.workflow_schema_transaction() as conn:
                for table in ('identity_name_claims','identity_name_owners','identity_name_schema'):conn.execute(f'DROP TABLE {table}')
            original=org.db._conn
            class InstallFault(ConnectionFault):
                def hit(self):stop()
            org.db._conn=InstallFault(original,'COMMIT',after=cut=='C02')
            names.refresh_names(org,install=True)
        elif cut.startswith('C03') or cut=='C04':
            needles={'C03a':'INSERT INTO identity_name_claims','C03b':'UPDATE identity_name_claims SET permanent=1',
                     'C03c':'UPDATE identity_name_owners SET current_label','C03d':'INSERT INTO audit_log','C04':'COMMIT'}
            class RenameFault(ConnectionFault):
                def hit(self):stop()
            org.db._conn=RenameFault(org.db._conn,needles[cut],arm_on='UPDATE identity_name_owners SET current_label' if cut=='C04' else None)
            operation=rename(org,'Alex',revision=1)
        elif cut=='C08':
            prompt_loader.reject_agent(OrgPaths(root),'maker');org.teams.remove_worker('engineering','maker');stop()
        elif cut in ('C05','C06','C07'):
            if cut=='C05':wrap(os,'replace',lambda a,k:str(a[1]).endswith('/_pending/new_id.md'),after=False)
            elif cut=='C06':wrap(os,'replace',lambda a,k:str(a[1]).endswith('/_pending/new_id.md'))
            else:wrap(org.teams,'save',lambda a,k:True)
            operation=enroll(org,'new_id')
        elif cut in ('C09','C10','C11'):
            if cut=='C09':wrap(org.teams,'save',lambda a,k:True)
            elif cut=='C10':wrap(os,'replace',lambda a,k:str(a[1]).endswith('/agents/new_id.md'))
            else:
                from runtime.orchestrator.context_builder import ContextBuilder
                wrap(ContextBuilder,'ensure_workspace_ready',lambda a,k:True,after=False)
            operation=founder_create(org,'new_id')
        elif cut=='C12':
            wrap(os,'replace',lambda a,k:str(a[1]).endswith('/agents/maker.md'))
            operation=agents.approve_agent('alpha','maker',org)
        elif cut in ('C13','C14','C15a','C15b'):
            if cut in ('C13','C14'):wrap(Path,'unlink',lambda a,k:str(a[0]).endswith('/_pending/maker.md'),after=cut=='C14')
            elif cut=='C15a':wrap(org.teams,'save',lambda a,k:True)
            else:wrap(names,'refresh_names',lambda a,k:not (OrgPaths(root).pending_agents_dir/'maker.md').exists())
            operation=agents.reject_agent('alpha','maker',org)
        elif cut=='C20':
            wrap(os,'replace',lambda a,k:str(a[1]).endswith('/agents/maker.md'))
            rev=prompt_loader.agent_revision(OrgPaths(root),'maker')
            operation=agents.manage_agent('alpha',agents.ManageAgentBody(action='update',name='maker',task_id='TASK-OWNER',session_id='sess-owner',expected_revision=rev,description='new bytes'),org)
        elif cut=='C21':
            # Projection after the actual new pending file/team, no online DB-first writer.
            original=names.reconcile_namespace
            def projection(*a,**k):
                if (OrgPaths(root).pending_agents_dir/'new_id.md').exists():stop()
                return original(*a,**k)
            patches.enter_context(patch.object(names,'reconcile_namespace',projection))
            operation=enroll(org,'new_id')
        elif cut.startswith(('T1-','T2-')):
            stage=cut.split('-')[1];after=cut.endswith('after-save')
            if stage=='add':org.teams.remove_worker('engineering','maker')
            original_save=org.teams.save;calls=0
            def settings_save(*a,**k):
                nonlocal calls
                calls+=1
                trigger=(calls==2 if stage=='rollback' else calls==1)
                if trigger and not after:stop()
                result=original_save(*a,**k)
                if cut.startswith('T1-') and calls==1 and stage=='rollback':
                    # A real disk definition drift makes the FIRST shipping
                    # validator fail, distinct from T2 worker-membership drift.
                    path=OrgPaths(root).agents_dir/'manager.md'
                    definition=prompt_loader.load_agent(OrgPaths(root),'manager')
                    path.write_text(render_agent_text(replace(definition,team='missing-team')))
                if trigger and after:stop()
                return result
            patches.enter_context(patch.object(org.teams,'save',settings_save))
            operation=settings.put_teams('alpha',org,settings.TeamsPatch(team='engineering',
                add_workers=['maker'] if stage=='add' else [],
                remove_workers=[] if stage=='add' else ['maker']))
        else:
            seed_cleanup(org)
            if cut=='C16':wrap(os,'rename',lambda a,k:str(a[1]).endswith('/_terminated/maker.md'))
            elif cut=='C17a':wrap(agents,'_move_dir_atomically',lambda a,k:True)
            elif cut in ('C17b','C17c'):
                wrap(org.teams,'save',lambda a,k:True,after=cut=='C17c')
            elif cut in ('C17d','C18a'):
                class CleanupFault(ConnectionFault):
                    def hit(self):stop()
                org.db._conn=CleanupFault(org.db._conn,'COMMIT',after=cut=='C18a',arm_on='UPDATE schedules SET')
            elif cut=='C18b':
                wrap(names,'refresh_names',lambda a,k:prompt_loader.is_terminated(OrgPaths(root),'maker'))
            elif cut.startswith('C19'):
                original_cleanup=org.db.terminate_agent_cleanups
                def cleanup_fail(*a,**k):
                    if cut=='C19a':stop()
                    raise OSError('actual cleanup fault')
                patches.enter_context(patch.object(org.db,'terminate_agent_cleanups',cleanup_fail))
                if cut in ('C19b','C19c'):
                    original_save=org.teams.save;count=0
                    def save(*a,**k):
                        nonlocal count
                        count+=1
                        if count==2 and cut=='C19b':stop()
                        original_save(*a,**k)
                        if count==2 and cut=='C19c':stop()
                    patches.enter_context(patch.object(org.teams,'save',save))
                elif cut=='C19d':wrap(agents,'_move_dir_atomically',lambda a,k:Path(a[0]).parent.name=='_terminated')
                elif cut in ('C19e','C19f'):
                    original_move=agents._move_dir_atomically
                    def move(*a,**k):
                        if Path(a[0]).parent.name=='_terminated':raise OSError('workspace rollback failed')
                        return original_move(*a,**k)
                    patches.enter_context(patch.object(agents,'_move_dir_atomically',move))
                    if cut=='C19e':wrap(os,'rename',lambda a,k:str(a[0]).endswith('/_terminated/maker.md'))
                    else:
                        original_rename=os.rename
                        def refuse_restore(*a,**k):
                            if str(a[0]).endswith('/_terminated/maker.md'):raise OSError('canonical restore failed')
                            return original_rename(*a,**k)
                        patches.enter_context(patch.object(os,'rename',refuse_restore))
                        wrap(names,'refresh_names',lambda a,k:prompt_loader.is_terminated(OrgPaths(root),'maker'))
            else:raise AssertionError(cut)
            operation=terminate(org)
        if operation is not None:asyncio.run(operation)
    raise AssertionError('shipping cut not reached: '+cut)


@contextmanager
def mounted_naming_app(tmp_path, *, lifecycle='active'):
    """Real two-org state and shipping app, without lifespan/provider launches.

    Used only after a separate disposable-execution admission. Auth is the
    actual isolated token dependency, never an overridden test principal.
    """
    from runtime.daemon import paths as daemon_paths
    from runtime.daemon.app import create_app
    from runtime.daemon.state import DaemonState
    runtime = RuntimeDir.init(tmp_path / 'runtime')
    for slug, worker, life in (('alpha', 'maker', lifecycle), ('beta', 'foreign', 'active')):
        root = runtime.orgs_dir / slug
        seed(root, worker=worker, lifecycle=life)
        release_seed(OrgPaths(root).db_path, 'fresh', 'F', slug=slug)
    state = DaemonState.from_runtime(runtime, Settings())
    assert set(state.orgs) == {'alpha', 'beta'}
    for org in state.orgs.values():
        assert org.naming_readiness == 'ready', org.naming_diagnostic
        org.sessions.set_active('TASK-OWNER', 'manager', 'sess-owner')
    daemon_paths.ensure_daemon_home()
    daemon_paths.ensure_token()
    headers = {'Authorization': 'Bearer ' + daemon_paths.read_token()}
    try:
        yield create_app(state), state.orgs, headers
    finally:
        for org in state.orgs.values():
            org.close()


def http_naming_snapshot(orgs):
    """Exact persistent effects in BOTH orgs plus actual live session bindings."""
    return {slug: {'persistent': snapshot(org),
                   'manager_session': org.sessions.get_active_session_id('TASK-OWNER', 'manager')}
            for slug, org in orgs.items()}
