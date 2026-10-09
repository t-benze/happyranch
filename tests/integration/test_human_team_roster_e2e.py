"""Finite THR296 shipping cases. Execute only via authorized disposable parent.

No production-host runs. RF5/RF6 real-process cuts and ten real context sources
are authored here; writer barriers, full history/portability and maintenance
observers still need further source. Authoring is never execution evidence.
"""
from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
import signal

import httpx
import pytest
import yaml

from tests.integration.conftest import seed_workspace
from tests.integration.test_subtask_self_decompose_e2e import _auth_headers, _wait_for_terminal

pytestmark = pytest.mark.integration
VERDICTS = [(None, 'none'), ('', 'blank'), ('CUSTOM_REVIEW_OUTCOME', 'custom'),
            ('APPROVE', 'approve'), ('PASS', 'pass'), ('REQUEST_CHANGES', 'request_changes'),
            ('REVISE', 'revise'), ('BLOCK', 'block')]


@pytest.fixture
def human_daemon(runtime: Path, request: pytest.FixtureRequest,
                 fake_claude_plan_env: Path, fake_codex_plan_env: Path,
                 tmp_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                 fake_claude: Path, fake_codex: Path, fake_opencode: Path):
    _seed_human_roster(runtime)
    # Plans are explicit; normal callback verification never invokes providers.
    _write_plan(fake_claude_plan_env, runtime, status='completed', verdict=None, self_child=False)
    _write_plan(fake_codex_plan_env, runtime, status='completed', verdict=None, self_child=False)
    cut = getattr(request.node, 'callspec', None)
    cut = cut.params.get('cut') if cut is not None else None
    if cut is None:
        yield request.getfixturevalue('live_daemon'), runtime
        return
    from tests.integration.conftest import _nested_daemon_env
    from runtime.daemon import paths as daemon_paths, runtimes
    from runtime.orchestrator.executor_binary_registry import save_registry
    from tests.helpers.integration_stub_guard.guard import manifest
    binding = manifest()
    monkeypatch.setenv('HAPPYRANCH_EXECUTOR_LAUNCH_SPACING_SECONDS', '0')
    save_registry({'claude': str(fake_claude), 'codex': str(fake_codex), 'opencode': str(fake_opencode)})
    runtimes.register(runtime.parent.parent)
    witness = tmp_path / 'real-failed-cut.json'
    log = (tmp_path / 'real-failed-daemon.log').open('w')
    processes = []
    # TEST-SIDE profile observes the actual effect method's successful return.
    # It installs before imports/all threads, hashes source and compiled code,
    # and abruptly exits only after the real separately committed effect. It
    # does not replace SQL, a method, an executor, or a final transition.
    launcher = r'''
import asyncio,hashlib,json,marshal,os,runpy,sys,threading,types
from pathlib import Path
source=Path(sys.argv[1]); revision=sys.argv[2]; cut=sys.argv[3]; receipt=Path(sys.argv[4])
sys.dont_write_bytecode=True
sys.path.insert(0,str(source))
import subprocess
assert subprocess.check_output(['git','-C',str(source),'rev-parse','HEAD'],text=True).strip()==revision
assert not subprocess.check_output(['git','-C',str(source),'status','--porcelain']).strip()
path=source/'runtime/infrastructure/db/tasks.py'
data=path.read_bytes()
assert data==subprocess.check_output(['git','-C',str(source),'show',revision+':runtime/infrastructure/db/tasks.py'])
def members(code):
    yield code
    for value in code.co_consts:
        if isinstance(value,types.CodeType): yield from members(value)
codes=[code for code in members(compile(data,str(path),'exec',dont_inherit=True,optimize=sys.flags.optimize))
       if code.co_qualname=='TasksMixin.apply_human_failed_recovery_effect']
assert len(codes)==1
expected=hashlib.sha256(marshal.dumps(codes[0])).hexdigest()
writer_cuts={'writer_busy_before_consumption-inline_worker','writer_reacquired_before_retry'}
writer_path=source/'runtime/orchestrator/run_step.py'
writer_data=writer_path.read_bytes()
assert writer_data==subprocess.check_output(['git','-C',str(source),'show',revision+':runtime/orchestrator/run_step.py'])
writer_codes={code.co_qualname:hashlib.sha256(marshal.dumps(code)).hexdigest()
    for code in members(compile(writer_data,str(writer_path),'exec',dont_inherit=True,optimize=sys.flags.optimize))
    if code.co_qualname in {'_submit_human_failed_recovery','_drive_human_failed_recovery','_HumanFailedRecoveryOperation.finish'}}
assert len(writer_codes)==3
writer_state={}
def record_writer(event, **fields):
    target=receipt.with_name(receipt.name+'.'+event)
    value={'event':event,'pid':os.getpid(),'thread':threading.get_ident(),
        'source_sha':revision,'file_sha256':hashlib.sha256(writer_data).hexdigest(),**fields}
    fd=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'w') as out:
        json.dump(value,out,sort_keys=True);out.flush();os.fsync(out.fileno())
async def held_writer(org):
    # Real supported context entry, on the daemon's actual owning loop. No
    # canonical segment is called: unrelated writer holds only its async bit.
    for number in range(2 if cut=='writer_reacquired_before_retry' else 1):
        async with org._profile_coordinator.consumer_writer(org=org,
                publisher='THR296-isolated-writer-control',consumer='consultant_head',preserve=True):
            assert org.workflow_authority._async_writer_lock.locked()
            record_writer('held-'+str(number),loop=id(asyncio.get_running_loop()),**writer_state['identity'])
            writer_state['ready'].set()
            release=receipt.with_name(receipt.name+'.release-'+str(number))
            while not release.exists():
                await asyncio.sleep(0.01)
        record_writer('released-'+str(number),**writer_state['identity'])
    record_writer('writer-complete',**writer_state['identity'])
def observe_writer(frame,event,arg):
    if frame.f_code.co_filename!=str(writer_path) or frame.f_code.co_qualname not in writer_codes:
        return
    assert hashlib.sha256(marshal.dumps(frame.f_code)).hexdigest()==writer_codes[frame.f_code.co_qualname]
    local=frame.f_locals
    if frame.f_code.co_qualname=='_submit_human_failed_recovery' and event=='call' and not writer_state:
        orch=local['orch']; task=orch._db.get_task(local['task_id'])
        if task is None or task.task_type!='subtask' or task.team!='default':return
        try:asyncio.get_running_loop()
        except RuntimeError:pass
        else:raise AssertionError('inline-worker barrier must not block daemon loop')
        assert local['agent']=='consultant_codex' and orch._main_loop.is_running()
        with orch._db._lock:
            rows=orch._db._conn.execute('SELECT * FROM task_completion_recoveries WHERE task_id=?',(task.id,)).fetchall()
            assert len(rows)==1
            episode=dict(rows[0])
        assert episode['state']=='callback_accepted' and episode['accepted_result_id']==local['result_id']
        writer_state.update(identity={'task':task.id,'agent':local['agent'],'origin':episode['origin_session_id'],
            'session':local['session_id'],'result':local['result_id']},ready=threading.Event())
        org=orch._workflow_drafts.org
        writer_state['writer']=asyncio.run_coroutine_threadsafe(held_writer(org),orch._main_loop)
        assert writer_state['ready'].wait(10),'native writer did not acquire actual async interval'
    elif frame.f_code.co_qualname=='_drive_human_failed_recovery' and event=='return' and writer_state:
        operation=local['operation']
        if operation.task_id!=writer_state['identity']['task']:return
        if operation.disposition=='writer_busy' and 'deferred' not in writer_state:
            assert operation.timer is not None and not operation.completion.done()
            writer_state['deferred']=True
            writer_state['operation']=operation
            record_writer('consumer-deferred',phase=operation.phase,**writer_state['identity'])
    elif frame.f_code.co_qualname=='_HumanFailedRecoveryOperation.finish' and event=='return' and writer_state:
        operation=local['self']
        if operation is writer_state.get('operation') and 'finished' not in writer_state:
            writer_state['finished']=True
            record_writer('consumer-finished',disposition=operation.disposition,phase=operation.phase,
                key=list(operation.key) if operation.key is not None else None,**writer_state['identity'])
def observe(frame,event,arg):
    if cut in writer_cuts:
        observe_writer(frame,event,arg)
        return
    if (event!='return' or frame.f_code.co_filename!=str(path)
            or frame.f_code.co_qualname!='TasksMixin.apply_human_failed_recovery_effect'
            or frame.f_locals.get('effect')!=cut or arg!='progressed'): return
    assert sys.getprofile() is observe
    assert hashlib.sha256(marshal.dumps(frame.f_code)).hexdigest()==expected
    local=frame.f_locals
    record={'pid':os.getpid(),'thread':threading.get_ident(),'source_sha':revision,
            'file_sha256':hashlib.sha256(data).hexdigest(),'code_sha256':expected,
            'effect':cut,'return':arg,'task':local['task_id'],'agent':local['agent'],
            'session':local['session_id'],'result':local['result_row_id']}
    fd=os.open(receipt,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'w') as out:
        json.dump(record,out,sort_keys=True);out.flush();os.fsync(out.fileno())
    os._exit(86)
if cut!='none':
    sys.setprofile(observe);threading.setprofile(observe)
sys.argv=['runtime.daemon']
runpy.run_module('runtime.daemon',run_name='__main__')
'''
    def start(selected: str) -> int:
        process = subprocess.Popen([sys.executable, '-I', '-c', launcher, binding['source'],
            binding['revision'], selected, str(witness)], cwd=binding['source'],
            env=_nested_daemon_env(), stdout=log, stderr=log, start_new_session=True)
        processes.append(process)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            assert process.poll() is None, f'daemon exited {process.returncode}; see {log.name}'
            if daemon_paths.port_file().exists():
                port = int(daemon_paths.port_file().read_text())
                try:
                    if httpx.get(f'http://127.0.0.1:{port}/api/v1/health', timeout=0.5).status_code == 200:
                        return port
                except httpx.HTTPError:
                    pass
            time.sleep(0.05)
        raise AssertionError('real daemon health unavailable')
    try:
        port = start(cut)
        request.node._roster_fault_daemon = {'process': processes[0], 'start': start, 'witness': witness}
        yield port, runtime
    finally:
        for process in processes:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=5)
        log.close()


def _seed_human_roster(runtime: Path) -> None:
    roster = yaml.safe_load((runtime / 'org/teams.yaml').read_text())
    roster['teams']['default'] = {'manager': {'kind': 'human', 'principal': 'founder'},
                                 'workers': ['consultant_head', 'consultant_codex']}
    roster.update(default_team='default', task_default_team='engineering')
    (runtime / 'org/teams.yaml').write_text(yaml.safe_dump(roster))
    seed_workspace(runtime, 'consultant_head')
    seed_workspace(runtime, 'consultant_codex', executor='codex')


def _write_plan(path: Path, root: Path, *, status: str, verdict: str | None, self_child: bool,
                recovery: bool = False, attempted_decision: dict | None = None,
                administration: bool = False) -> None:
    witness = path.parent / (path.name + '.calls.jsonl')
    # Existing bound fake binaries supply real task/runtime-session arguments;
    # this plan calls the supported callback, never writes task/results/audits.
    path.write_text('''#!/usr/bin/env bash
set -euo pipefail
HAPPYRANCH_TEST_ACTUAL_PROMPT=$(cat)
export HAPPYRANCH_TEST_ACTUAL_PROMPT
python - "$1" "$2" "$PWD" <<'PLAN'
import json, os, pathlib, re, subprocess, sys
T, S, workspace = sys.argv[1:]
agent = pathlib.Path(workspace).name
org = pathlib.Path(workspace).parent.parent.name
''' + f"root = pathlib.Path({str(root)!r})\nwitness = pathlib.Path({str(witness)!r})\nstatus = {status!r}\nverdict = {verdict!r}\nself_child = {self_child!r}\nrecovery = {recovery!r}\nattempted_decision = {attempted_decision!r}\nadministration = {administration!r}\n" + '''
with witness.open('a') as out:
    out.write(json.dumps({'task': T, 'session': S, 'agent': agent,
        'prompt': os.environ['HAPPYRANCH_TEST_ACTUAL_PROMPT'],
        'argv': json.loads(os.environ['HAPPYRANCH_TEST_CONTEXT_ARGV_JSON']),
        'workspace': workspace}) + '\\n')
# Independent read determines actual root/child provenance. The plan never
# manufactures a result or seeds the final transition.
import sqlite3
with sqlite3.connect((root / 'happyranch.db').as_uri() + '?mode=ro', uri=True) as conn:
    parent = conn.execute('SELECT parent_task_id FROM tasks WHERE id=?', (T,)).fetchone()[0]
    children = conn.execute('SELECT COUNT(*) FROM tasks WHERE parent_task_id=?', (T,)).fetchone()[0]
    prior = conn.execute("SELECT COUNT(*) FROM task_results WHERE task_id=? AND session_id!=''", (T,)).fetchone()[0]
    if recovery and parent is not None:
        accepted = conn.execute('SELECT origin_session_id,recovery_session_id,provider_session_id,state FROM task_completion_recoveries WHERE task_id=?', (T,)).fetchone()
        if accepted is None:
            assert prior == 0
            sys.exit(0)  # genuine Codex clean omission; native runner claims recovery
        assert accepted[1] == S and accepted[0] != S and accepted[2] and accepted[3] == 'claimed', (accepted, S)
        prompt = os.environ['HAPPYRANCH_TEST_ACTUAL_PROMPT']
        explicit = re.findall(r'binding task=(TASK-[0-9]+) session=(sess-[a-f0-9]+)', prompt)
        assert explicit == [(T, S)], explicit
        argv = json.loads(os.environ['HAPPYRANCH_TEST_CONTEXT_ARGV_JSON'])
        assert 'resume' in argv and accepted[2] in argv, (accepted, argv)
        assert prior == 0
if administration:
    import ast, httpx
    from runtime.daemon.paths import port_file, read_token
    base = 'http://127.0.0.1:' + port_file().read_text().strip() + '/api/v1/orgs/' + org
    token = read_token()
    assert token
    headers = {'Authorization': 'Bearer ' + token}
    observed = []
    for action in ('enroll', 'update', 'terminate'):
        body = {'action': action, 'name': 'consultant_codex' if agent == 'consultant_head' else 'consultant_head',
                'task_id': T, 'session_id': S, 'description': 'valid advisory worker',
                'system_prompt': 'Advise the founder.', 'executor': 'codex'}
        if action == 'enroll':
            body['name'] = 'ungranted_worker'
        reply = httpx.post(base + '/agents/manage', json=body, headers=headers)
        assert reply.status_code == 403 and reply.json()['detail'] == 'manage-agent requires an active team-manager session', reply.text
        observed.append({'action': action, 'status': reply.status_code, 'detail': reply.json()['detail']})
    # Reuse the independently literal existing valid fixture without importing
    # or collecting its suspended unit owner. The real current session must
    # reach manager_required, rather than unknown_session or invalid_request.
    source = pathlib.Path(__import__('runtime').__file__).resolve().parent.parent
    tree = ast.parse((source / 'tests/workflows/test_template_store.py').read_text())
    definitions = [ast.literal_eval(node.value) for node in tree.body if isinstance(node, ast.Assign)
                   and any(isinstance(target, ast.Name) and target.id == 'VALID_DEFINITION' for target in node.targets)]
    assert len(definitions) == 1
    reply = httpx.post(base + '/workflows/templates/publish', params={'session_id': S}, json={
        'operation_key': 'worker-denied-' + T, 'template_name': 'product-design',
        'expected_current_version': 0, 'definition': definitions[0]})
    assert reply.status_code == 403 and reply.json()['detail']['code'] == 'manager_required', reply.text
    observed.append({'action': 'template-publish', 'status': reply.status_code, 'detail': reply.json()['detail']})
    # Token here belongs solely to the synthetic fixture harness. It reaches
    # the actual target eligibility boundary, without attributing operator
    # bearer authority to this worker's session or changing auth semantics.
    control = httpx.get(base + '/agents/engineering_head/team-escalation-policy', headers=headers)
    assert control.status_code == 200, control.text
    control = control.json()
    assert control['target_manager'] == 'engineering_head' and control['team'] == 'engineering'
    assert control['family'] == 'empty' and control['selector_epoch'] == 0
    observed.append({'action': 'agent-manager-policy-control', 'status': 200})
    reply = httpx.get(base + '/agents/' + agent + '/team-escalation-policy', headers=headers)
    assert reply.status_code == 404 and reply.json()['detail']['code'] == 'policy_surface_not_available', reply.text
    observed.append({'action': 'worker-policy-read', 'status': 404, 'detail': reply.json()['detail']})
    body = {**control['v2_starter'], 'create_request_id': 'worker-create-' + T,
        'activation_request_id': 'worker-activate-' + T, 'based_on_selector_id': control['selector_id'],
        'expected_selector_id': control['selector_id'], 'action': 'bootstrap',
        'acknowledge_shared_credential_attribution': True}
    reply = httpx.post(base + '/agents/' + agent + '/team-escalation-policy/v2/releases',
                      headers=headers, json=body)
    assert reply.status_code == 404 and reply.json()['detail']['code'] == 'policy_surface_not_available', reply.text
    observed.append({'action': 'worker-policy-create-activate', 'status': 404, 'detail': reply.json()['detail']})
    pathlib.Path(str(witness) + '.administration.json').write_text(json.dumps(observed))
payload = {'task_id': T, 'session_id': S, 'agent': agent, 'status': 'completed', 'summary': 'root done', 'confidence': 90}
if attempted_decision is not None and parent is None and prior == 0:
    payload['decision'] = attempted_decision
elif self_child and parent is None and children == 0:
    payload['decision'] = {'action': 'delegate', 'agent': agent, 'prompt': 'self child'}
elif parent is None:
    payload['decision'] = {'action': 'done', 'summary': 'root done'}
else:
    payload.update(status=status, verdict=verdict, summary='child outcome')
file = pathlib.Path(workspace) / ('completion-' + S + '.json')
file.write_text(json.dumps(payload))
subprocess.run(['happyranch', 'report-completion', '--org', org, '--from-file', str(file)], check=True)
PLAN
''')


def _base(port: int) -> str:
    return f'http://127.0.0.1:{port}/api/v1/orgs/test'


PARTIAL_ROSTERS = [(head, codex, roster, empty)
                   for empty in (False, True)
                   for head, codex, roster in ((True, False, False), (False, True, False),
                       (False, False, True), (True, True, False), (True, False, True), (False, True, True))]


def _attach_process(root: Path, slug: str = 'test', *, save: bool = False) -> subprocess.CompletedProcess[str]:
    """Actual cold OrgState attachment in a fresh candidate interpreter."""
    from tests.helpers.integration_stub_guard.guard import manifest
    binding = manifest()
    script = '''
import json,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from runtime.config import Settings
from runtime.daemon.org_state import OrgState
assert Path(sys.modules['runtime'].__file__).resolve().parent == Path(sys.argv[1])/'runtime'
org=OrgState.load(slug=sys.argv[3],root=Path(sys.argv[2]),settings=Settings(project_root=Path(sys.argv[1])))
try:
    if sys.argv[4]=='save': org.teams.save()
    print(json.dumps({'agents':org.teams.all_agents(),'default':org.teams.default_team,
                      'task_default':org.teams.task_default_team}))
finally:
    org.close()
'''
    return subprocess.run([sys.executable, '-I', '-c', script, binding['source'], str(root), slug,
                           'save' if save else 'read'],
                          capture_output=True, text=True, timeout=30)


C1_ATTACH_CASES = [
    'unknown-manager-kind', 'unknown-human-principal', 'extra-manager-tag',
    'missing-manager-principal', 'blank-agent-principal', 'duplicate-worker',
    'cross-team-worker', 'duplicate-agent-manager', 'missing-worker-definition',
    'unregistered-active-worker', 'wrong-worker-team', 'wrong-worker-role',
    'wrong-manager-role', 'pending-worker-control', 'pending-manager-refused',
    'legacy-save-control', 'human-save-control',
]


@pytest.mark.parametrize('partial', [None, *PARTIAL_ROSTERS, *C1_ATTACH_CASES], ids=[
    'human-get', *[f"partial-{int(h)}{int(c)}{int(r)}-{'empty-default' if e else 'absent-default'}"
                   for h, c, r, e in PARTIAL_ROSTERS], *C1_ATTACH_CASES])
def test_c1_registry_and_attachment(request: pytest.FixtureRequest, runtime: Path,
                                    tmp_path: Path, partial: tuple | str | None) -> None:
    if isinstance(partial, str):
        # Finite malformed/positive fixtures exercise the real cold attachment
        # process, not a unit call to the validator or an invented callback.
        cold = tmp_path / 'cold-attachment'
        shutil.copytree(runtime / 'org', cold / 'org')
        if partial not in ('legacy-save-control', 'wrong-manager-role', 'duplicate-agent-manager', 'pending-manager-refused'):
            _seed_human_roster(cold)
        path = cold / 'org/teams.yaml'
        data = yaml.safe_load(path.read_text())
        teams = data['teams']
        malformed = {
            'unknown-manager-kind': {'kind': 'robot', 'principal': 'founder'},
            'unknown-human-principal': {'kind': 'human', 'principal': 'someone_else'},
            'extra-manager-tag': {'kind': 'human', 'principal': 'founder', 'executor': 'codex'},
            'missing-manager-principal': {'kind': 'human'},
            'blank-agent-principal': {'kind': 'agent', 'principal': ''},
        }
        if partial in malformed:
            teams['default']['manager'] = malformed[partial]
        elif partial == 'duplicate-worker':
            teams['default']['workers'].append('consultant_codex')
        elif partial == 'cross-team-worker':
            teams['engineering']['workers'].append('consultant_codex')
        elif partial == 'duplicate-agent-manager':
            teams['content']['workers'].append('engineering_head')
        elif partial == 'missing-worker-definition':
            (cold / 'org/agents/consultant_codex.md').unlink()
        elif partial == 'unregistered-active-worker':
            teams['default']['workers'].remove('consultant_codex')
        elif partial in ('wrong-worker-team', 'wrong-worker-role', 'wrong-manager-role'):
            from runtime.orchestrator.agent_def import parse_agent_text, render_agent_text
            from dataclasses import replace
            name = 'engineering_head' if partial == 'wrong-manager-role' else 'consultant_codex'
            target = cold / 'org/agents' / f'{name}.md'
            definition = parse_agent_text(target.read_text(), expected_name=name)
            changed = replace(definition, team='engineering') if partial == 'wrong-worker-team' else replace(
                definition, role='worker' if partial == 'wrong-manager-role' else 'manager')
            target.write_text(render_agent_text(changed))
        elif partial in ('pending-worker-control', 'pending-manager-refused'):
            name = 'consultant_codex' if partial == 'pending-worker-control' else 'engineering_head'
            pending = cold / 'org/agents/_pending'
            pending.mkdir(exist_ok=True)
            (cold / 'org/agents' / f'{name}.md').rename(pending / f'{name}.md')
        path.write_text(yaml.safe_dump(data))
        before = {str(p.relative_to(cold)): p.read_bytes() for p in (cold / 'org').rglob('*') if p.is_file()}
        successful = partial in ('pending-worker-control', 'legacy-save-control', 'human-save-control')
        actual = _attach_process(cold, save=partial.endswith('save-control'))
        if successful:
            assert actual.returncode == 0, actual.stderr
            value = json.loads(actual.stdout.splitlines()[-1])
            assert 'founder' not in value['agents']
            assert value['default'] == ('engineering' if partial == 'legacy-save-control' else 'default')
            assert value['task_default'] == 'engineering'
            assert yaml.safe_load(path.read_text()) == data
            expected = sorted(name for entry in teams.values() for name in
                ([entry['manager']] if isinstance(entry['manager'], str) else []) + entry['workers'])
            assert sorted(value['agents']) == expected
            if partial == 'pending-worker-control':
                assert not (cold / 'org/agents/consultant_codex.md').exists()
                assert (cold / 'org/agents/_pending/consultant_codex.md').read_bytes() == before['org/agents/_pending/consultant_codex.md']
        else:
            assert actual.returncode != 0, actual.stdout
            expected = 'ValueError' if partial in malformed else 'OrgConsistencyError'
            assert expected in actual.stderr, actual.stderr
            assert {str(p.relative_to(cold)): p.read_bytes() for p in (cold / 'org').rglob('*') if p.is_file()} == before
        assert not (cold / 'workspaces/founder').exists()
        with sqlite3.connect(cold / 'happyranch.db') as conn:
            assert conn.execute('SELECT COUNT(*) FROM tasks').fetchone()[0] == 0
            assert conn.execute('SELECT COUNT(*) FROM task_results').fetchone()[0] == 0
            if not successful:
                assert conn.execute('SELECT COUNT(*) FROM workflow_publication_journals').fetchone()[0] == 0
        return
    if partial is not None:
        # A fresh fixture copy has no running database or worker. This drives
        # the shipped attachment owner, unlike the suspended validator units.
        from runtime.orchestrator.agent_def import AgentDef, render_agent_text
        cold = tmp_path / 'cold-attachment'
        shutil.copytree(runtime / 'org', cold / 'org')
        head, codex, roster_after, empty = partial
        data = yaml.safe_load((cold / 'org/teams.yaml').read_text())
        human = {'manager': {'kind': 'human', 'principal': 'founder'}, 'workers': []}
        data['teams']['consultant'] = {'manager': 'consultant_head', 'workers': ['consultant_codex']}
        if empty:
            data['teams']['default'] = human
        if roster_after:
            del data['teams']['consultant']
            data['teams']['default'] = {**human, 'workers': ['consultant_head', 'consultant_codex']}
        for name, after, executor in (('consultant_head', head, 'claude'), ('consultant_codex', codex, 'codex')):
            definition = AgentDef(name=name, team='default' if after else 'consultant',
                role='manager' if name == 'consultant_head' and not after else 'worker',
                executor=executor, allow_rules=(), repos={}, enrolled_by=None,
                enrolled_at_task=None, enrolled_at=None, system_prompt=f'You are {name}.')
            (cold / 'org/agents' / f'{name}.md').write_text(render_agent_text(definition))
        (cold / 'org/teams.yaml').write_text(yaml.safe_dump(data))
        before = {str(path.relative_to(cold)): path.read_bytes() for path in (cold / 'org').rglob('*') if path.is_file()}
        actual = _attach_process(cold)
        assert actual.returncode != 0, actual.stdout
        assert 'OrgConsistencyError: org content is inconsistent' in actual.stderr, actual.stderr
        assert {str(path.relative_to(cold)): path.read_bytes() for path in (cold / 'org').rglob('*') if path.is_file()} == before
        assert not (cold / 'workspaces/founder').exists()
        with sqlite3.connect(cold / 'happyranch.db') as conn:
            assert conn.execute('SELECT COUNT(*) FROM tasks').fetchone()[0] == 0
            assert conn.execute('SELECT COUNT(*) FROM workflow_publication_journals').fetchone()[0] == 0
        return
    port, root = request.getfixturevalue('human_daemon')
    reply = httpx.get(_base(port) + '/teams', headers=_auth_headers()).raise_for_status().json()
    assert next(row for row in reply['teams'] if row['name'] == 'default') == {
        'name': 'default', 'manager': None, 'manager_kind': 'human', 'human_manager': 'founder',
        'is_default': True, 'workers': ['consultant_head', 'consultant_codex']}
    agents = httpx.get(_base(port) + '/agents', headers=_auth_headers()).raise_for_status().json()['agents']
    assert not any(agent['name'] == 'founder' for agent in agents)
    assert not (root / 'workspaces/founder').exists()
    for name in ('consultant_head', 'consultant_codex'):
        assert next(agent for agent in agents if agent['name'] == name)['role'] == 'worker'
    # Deliberate new creation persists both Default pointers; cold reopen
    # preserves them while the empty roster remains fenced for reviewers.
    created = httpx.post(f'http://127.0.0.1:{port}/api/v1/orgs',
                         json={'slug': 'fresh-default'}, headers=_auth_headers())
    assert created.status_code == 200, created.text
    fresh = root.parent / 'fresh-default'
    canonical = (fresh / 'org/teams.yaml').read_bytes()
    data = yaml.safe_load(canonical)
    assert data['default_team'] == data['task_default_team'] == 'default'
    assert data['teams'] == {'default': {'manager': {'kind': 'human', 'principal': 'founder'}, 'workers': []}}
    fresh_teams = httpx.get(f'http://127.0.0.1:{port}/api/v1/orgs/fresh-default/teams', headers=_auth_headers()).raise_for_status().json()
    assert fresh_teams['teams'] == [{'name': 'default', 'manager': None, 'manager_kind': 'human',
                                    'human_manager': 'founder', 'is_default': True, 'workers': []}]
    with sqlite3.connect(fresh / 'happyranch.db') as conn:
        assert conn.execute('SELECT COUNT(*) FROM tasks').fetchone()[0] == 0
        assert conn.execute("SELECT state FROM workflow_authority_pointers WHERE namespace='org/fresh-default'").fetchone() == ('fenced',)
    # Reopen a closed independently created empty fixture rather than copy
    # or attach this live daemon-owned database in a second writer.
    empty = tmp_path / 'empty-cold'
    (empty / 'org/agents').mkdir(parents=True)
    (empty / 'org/teams.yaml').write_bytes(canonical)
    reopened = _attach_process(empty)
    assert reopened.returncode == 0, reopened.stderr
    assert json.loads(reopened.stdout.splitlines()[-1]) == {'agents': [], 'default': 'default', 'task_default': 'default'}
    assert (empty / 'org/teams.yaml').read_bytes() == canonical
    assert not (empty / 'workspaces/founder').exists()


@pytest.mark.parametrize('owner,code,http_status', [
    (None, 'owner_required_for_human_team', 422),
    ('founder', 'unknown_owner', 400),
    ('missing_worker', 'unknown_owner', 400),
    ('dev_agent', 'owner_team_mismatch', 400),
    ('pending-head', 'unknown_owner', 400),
    ('inactive-codex', 'unknown_owner', 400),
    ('wrong-role-head', 'unknown_owner', 400),
    ('unknown-team', 'unknown_team', 400),
    ('fresh-omitted', 'owner_required_for_human_team', 422),
    ('consultant_head', None, 200),
    ('consultant_codex', None, 200),
    ('legacy-omitted', None, 200),
], ids=['missing-owner', 'founder', 'unknown-worker', 'other-team-worker',
        'pending-worker', 'inactive-worker', 'wrong-worker-role', 'unknown-team', 'fresh-default-omitted',
        'head-with-attachment', 'codex-owner-only-with-attachment', 'legacy-omitted-with-attachment'])
def test_c2_owner_required_before_persistence(human_daemon: tuple[int, Path],
                                             owner: str | None, code: str, http_status: int) -> None:
    port, root = human_daemon
    base = _base(port)
    if owner == 'fresh-omitted':
        created = httpx.post(f'http://127.0.0.1:{port}/api/v1/orgs',
            json={'slug': 'fresh-owner'}, headers=_auth_headers())
        assert created.status_code == 200, created.text
        root = root.parent / 'fresh-owner'
        base = f'http://127.0.0.1:{port}/api/v1/orgs/fresh-owner'
    elif owner in ('pending-head', 'inactive-codex'):
        # Intentional test-fixture loss of active eligibility after attachment
        # must be observed again by the actual submission boundary.
        name = 'consultant_head' if owner == 'pending-head' else 'consultant_codex'
        destination = root / 'org/agents' / ('_pending' if owner == 'pending-head' else '_terminated')
        destination.mkdir(exist_ok=True)
        (root / 'org/agents' / f'{name}.md').rename(destination / f'{name}.md')
    elif owner == 'wrong-role-head':
        from dataclasses import replace
        from runtime.orchestrator.agent_def import parse_agent_text, render_agent_text
        path = root / 'org/agents/consultant_head.md'
        definition = parse_agent_text(path.read_text(), expected_name='consultant_head')
        path.write_text(render_agent_text(replace(definition, role='manager')))
    upload = httpx.post(base + '/tasks/attachments', headers=_auth_headers(),
                        params={'agent': 'founder'},
                        files={'file': ('roster.png', b'\x89PNG\r\n\x1a\nfixture', 'image/png')})
    assert upload.status_code == 200, upload.text
    attachment = upload.json()
    with sqlite3.connect(root / 'happyranch.db') as conn:
        before = conn.execute('SELECT COUNT(*) FROM tasks').fetchone()[0]
        before_attachments = conn.execute('SELECT COUNT(*) FROM task_attachments').fetchone()[0]
    body = {'team': 'default', 'brief': 'owner guard before any durable allocation',
            'attachments': [{'storage_key': attachment['storage_key'], 'display_name': 'roster.png'}]}
    if owner in ('legacy-omitted', 'fresh-omitted'):
        del body['team']
    elif owner in ('pending-head', 'wrong-role-head'):
        body['owner'] = 'consultant_head'
    elif owner == 'inactive-codex':
        body['owner'] = 'consultant_codex'
    elif owner == 'unknown-team':
        body.update(team='missing_team', owner='consultant_codex')
    elif owner == 'consultant_codex':
        del body['team']
        body['owner'] = owner
    elif owner is not None:
        body['owner'] = owner
    reply = httpx.post(base + '/tasks', json=body, headers=_auth_headers())
    assert reply.status_code == http_status
    if code is None:
        task_id = reply.json()['task_id']
        final = _wait_for_terminal(_base(port), task_id)
        expected_owner = 'engineering_head' if owner == 'legacy-omitted' else owner
        assert final['task']['status'] == 'completed', final
        with sqlite3.connect(root / 'happyranch.db') as conn:
            assert conn.execute('SELECT assigned_agent,team FROM tasks WHERE id=?', (task_id,)).fetchone() == (
                expected_owner, 'engineering' if owner == 'legacy-omitted' else 'default')
            assert conn.execute('SELECT storage_key FROM task_attachments WHERE task_id=?', (task_id,)).fetchall() == [(attachment['storage_key'],)]
            results = conn.execute('SELECT id,session_id,agent FROM task_results WHERE task_id=?', (task_id,)).fetchall()
            assert len(results) == 1 and type(results[0][0]) is int and results[0][0] > 0
            assert results[0][1] and results[0][2] == expected_owner
        return
    assert reply.json()['detail']['code'] == code
    with sqlite3.connect(root / 'happyranch.db') as conn:
        assert conn.execute('SELECT COUNT(*) FROM tasks').fetchone()[0] == before
        assert conn.execute('SELECT COUNT(*) FROM task_attachments').fetchone()[0] == before_attachments
        assert conn.execute('SELECT COUNT(*) FROM task_results').fetchone()[0] == 0


@pytest.mark.parametrize('agent', ['consultant_head', 'consultant_codex'], ids=['head', 'codex'])
@pytest.mark.parametrize('operation', ['root', 'peer-delegate', 'peer-then', 'peer-fanout', 'supersede', 'administration'])
def test_c3_worker_lifecycle_and_denials(human_daemon: tuple[int, Path], agent: str, operation: str,
                                        fake_claude_plan_env: Path, fake_codex_plan_env: Path) -> None:
    port, root = human_daemon
    peer = 'consultant_codex' if agent == 'consultant_head' else 'consultant_head'
    decisions = {
        'peer-delegate': {'action': 'delegate', 'agent': peer, 'prompt': 'valid peer work'},
        'peer-then': {'action': 'delegate', 'agent': agent, 'prompt': 'valid own work',
                      'then': [{'agent': peer, 'prompt': 'valid peer continuation'}]},
        'peer-fanout': {'action': 'fanout', 'children': [{'agent': agent, 'prompt': 'own work'},
                          {'agent': peer, 'prompt': 'peer work'}], 'width_cap_ack': 2},
        'supersede': {'action': 'supersede', 'successor_brief': 'valid replacement', 'rationale': 'valid recovery',
            'attestation': {'recovery_reason': 'valid recovery', 'policy_product_intent_unchanged': True,
                'no_budget_or_external_commitment': True, 'no_permission_or_cross_team_change': True,
                'no_schema_auth_security_privacy_or_data_access_change': True, 'no_unresolved_founder_gate': True}},
    }
    plan = fake_claude_plan_env if agent == 'consultant_head' else fake_codex_plan_env
    _write_plan(plan, root, status='completed', verdict=None, self_child=False,
                attempted_decision=decisions.get(operation), administration=operation == 'administration')
    canonical_before = {str(path.relative_to(root)): path.read_bytes()
                        for path in (root / 'org').rglob('*.md') if path.is_file()}
    roster_before = (root / 'org/teams.yaml').read_bytes()
    tables = ('manager_supersessions', 'workflow_template_versions', 'workflow_template_publish_operations',
              'authority_policy_releases', 'authority_policy_activations')
    with sqlite3.connect(root / 'happyranch.db') as conn:
        rows_before = {name: conn.execute(f'SELECT * FROM {name}').fetchall() for name in tables}
    reply = httpx.post(_base(port) + '/tasks', json={'team': 'default', 'owner': agent, 'brief': 'ordinary root'}, headers=_auth_headers()).raise_for_status().json()
    final = _wait_for_terminal(_base(port), reply['task_id'])
    assert final['task']['status'] == ('failed' if operation == 'supersede' else 'completed')
    with sqlite3.connect(root / 'happyranch.db') as conn:
        actual = conn.execute('SELECT id,session_id,agent FROM task_results WHERE task_id=?', (reply['task_id'],)).fetchall()
        genuine = [row for row in actual if row[1]]
        assert len(genuine) == (2 if operation.startswith('peer-') else 1)
        assert all(type(row[0]) is int and row[0] > 0 and row[2] == agent for row in genuine)
        assert conn.execute('SELECT COUNT(*) FROM tasks WHERE parent_task_id=?', (reply['task_id'],)).fetchone()[0] == 0
        assert conn.execute('SELECT active_chain,active_fanout FROM tasks WHERE id=?', (reply['task_id'],)).fetchone() == (None, None)
        assert {name: conn.execute(f'SELECT * FROM {name}').fetchall() for name in tables} == rows_before
        if operation.startswith('peer-'):
            feedback = conn.execute("SELECT output_summary FROM task_results WHERE task_id=? AND session_id=''", (reply['task_id'],)).fetchall()
            assert len(feedback) == 1 and 'only' in feedback[0][0] and 'yourself' in feedback[0][0]
        if operation == 'supersede':
            assert final['task']['note'] == 'manager supersession claim is not current'
    assert (root / 'org/teams.yaml').read_bytes() == roster_before
    assert {str(path.relative_to(root)): path.read_bytes()
            for path in (root / 'org').rglob('*.md') if path.is_file()} == canonical_before
    if operation == 'administration':
        evidence = json.loads(Path(str(plan) + '.calls.jsonl.administration.json').read_text())
        assert [row['action'] for row in evidence] == ['enroll', 'update', 'terminate', 'template-publish',
            'agent-manager-policy-control', 'worker-policy-read', 'worker-policy-create-activate']
        assert not (root / 'org/agents/ungranted_worker.md').exists()
    # Current human-team worker is not an eligible manager policy target.
    denied = httpx.get(_base(port) + f'/agents/{agent}/team-escalation-policy', headers=_auth_headers())
    assert denied.status_code == 404


C4_SCENARIOS = [
    (agent, recovery, status, None)
    for agent, recovery in [('consultant_head', False), ('consultant_codex', False), ('consultant_codex', True)]
    for status in ('completed', 'blocked')
] + [('consultant_codex', True, 'blocked', cut) for cut in (
    'fail', 'review', 'writer_busy_before_consumption-inline_worker', 'writer_reacquired_before_retry')]


@pytest.mark.parametrize('agent,recovery,status,cut', C4_SCENARIOS, ids=[
    ('rf5-zero-review' if cut == 'fail' else 'rf6-one-review' if cut == 'review' else cut) if cut else
    ('head' if agent == 'consultant_head' else 'codex-accepted' if recovery else 'codex') +
    ('-completed' if status == 'completed' else '-failed')
    for agent, recovery, status, cut in C4_SCENARIOS])
@pytest.mark.parametrize('verdict', [value for value, _ in VERDICTS], ids=[name for _, name in VERDICTS])
def test_c4_normal_and_recovered_verdict_attribution(
    human_daemon: tuple[int, Path], request: pytest.FixtureRequest,
    agent: str, recovery: bool, status: str, cut: str | None, verdict: str | None,
    fake_claude_plan_env: Path, fake_codex_plan_env: Path,
) -> None:
    # Normal and genuine two-invocation Codex admission, including actual
    # separately committed RF5/RF6 cuts. No manually inserted recovery marker.
    port, root = human_daemon
    plan = fake_claude_plan_env if agent == 'consultant_head' else fake_codex_plan_env
    _write_plan(plan, root, status=status, verdict=verdict, self_child=True, recovery=recovery)
    reply = httpx.post(_base(port) + '/tasks', json={'team': 'default', 'owner': agent, 'brief': 'self child then final parent'}, headers=_auth_headers()).raise_for_status().json()
    original_review = None
    selected_before = None
    if cut in ('fail', 'review'):
        owned = request.node._roster_fault_daemon
        assert owned['process'].wait(timeout=150) == 86
        observed = json.loads(owned['witness'].read_text())
        assert observed['effect'] == cut and observed['agent'] == agent
        assert type(observed['result']) is int and observed['result'] > 0
        with sqlite3.connect(root / 'happyranch.db') as conn:
            selected_before = conn.execute('SELECT * FROM task_results WHERE id=?', (observed['result'],)).fetchone()
            assert selected_before is not None
            child = conn.execute('SELECT parent_task_id,status,assigned_agent,current_session_id,note,completed_at FROM tasks WHERE id=?',
                                 (observed['task'],)).fetchone()
            assert child[:4] == (reply['task_id'], 'failed', agent, observed['session'])
            assert child[4] == 'self-blocked: child outcome' and child[5]
            assert conn.execute('SELECT state,accepted_result_id,accepted_result_session_id FROM task_completion_recoveries WHERE task_id=?',
                                (observed['task'],)).fetchone() == ('callback_accepted', observed['result'], observed['session'])
            bound = [json.loads(row[0]) for row in conn.execute("SELECT payload FROM audit_log WHERE task_id=? AND action='completion_report' ORDER BY id",
                                                              (observed['task'],))]
            assert sum(row.get('_result_row_id') == observed['result'] and row.get('_recovery_session_id') == observed['session'] for row in bound) == 1
            reviews = conn.execute("SELECT id,task_id,agent,action,payload,timestamp FROM audit_log WHERE task_id=? AND action='review_verdict' ORDER BY id",
                                   (observed['task'],)).fetchall()
            assert len(reviews) == (0 if cut == 'fail' else 1)
            if reviews:
                original_review = reviews[0]
                assert original_review[2] == agent
                assert json.loads(original_review[4]) == {'verdict': verdict if verdict is not None else 'rejected',
                    'feedback': 'self-blocked: child outcome', 'reviewed_agent': agent}
            # This is the committed failed child before parent progression;
            # a genuine callback/result for its next parent invocation is owed.
            assert conn.execute('SELECT COUNT(*) FROM task_results WHERE task_id=?', (reply['task_id'],)).fetchone()[0] == 1
        port = owned['start']('none')
    elif cut in ('writer_busy_before_consumption-inline_worker', 'writer_reacquired_before_retry'):
        owned = request.node._roster_fault_daemon
        witness = owned['witness']
        def record(event: str) -> dict:
            path = witness.with_name(witness.name + '.' + event)
            deadline = time.monotonic() + 150
            while not path.exists() and time.monotonic() < deadline:
                assert owned['process'].poll() is None, 'writer barrier daemon exited'
                time.sleep(0.05)
            assert path.exists(), f'no real native writer/consumer observation: {event}'
            return json.loads(path.read_text())
        observed = record('held-0')
        deferred = record('consumer-deferred')
        assert deferred['task'] == observed['task'] and deferred['phase'] == 'evidence'
        assert observed['pid'] == owned['process'].pid
        assert observed['origin'] != observed['session'] and type(observed['result']) is int
        assert observed['result'] > 0
        def held_readback() -> None:
            nonlocal selected_before
            with sqlite3.connect(root / 'happyranch.db') as conn:
                selected = conn.execute('SELECT * FROM task_results WHERE id=?', (observed['result'],)).fetchone()
                assert selected is not None
                if selected_before is None:
                    selected_before = selected
                assert selected == selected_before
                assert conn.execute('SELECT parent_task_id,status,assigned_agent,current_session_id,cancelled_at FROM tasks WHERE id=?',
                    (observed['task'],)).fetchone() == (reply['task_id'], 'in_progress', agent, observed['session'], None)
                assert conn.execute('SELECT state,accepted_result_id,accepted_result_session_id FROM task_completion_recoveries WHERE task_id=?',
                    (observed['task'],)).fetchone() == ('callback_accepted', observed['result'], observed['session'])
                logs = conn.execute("SELECT action,payload FROM audit_log WHERE task_id=?", (observed['task'],)).fetchall()
                assert not [row for row in logs if row[0] == 'review_verdict']
                assert not [row for row in logs if row[0] == 'completion_report' and '_result_row_id' in json.loads(row[1])]
                assert conn.execute('SELECT COUNT(*) FROM task_results WHERE task_id=?', (reply['task_id'],)).fetchone()[0] == 1
                assert conn.execute("SELECT COUNT(*) FROM jobs WHERE task_id=? AND reason='task_ended'", (observed['task'],)).fetchone()[0] == 0
        held_readback()
        witness.with_name(witness.name + '.release-0').write_text('release owned native writer\n')
        if cut == 'writer_reacquired_before_retry':
            second = record('held-1')
            assert second['loop'] == observed['loop'] and second['task'] == observed['task']
            held_readback()
            witness.with_name(witness.name + '.release-1').write_text('release second owned native writer\n')
        record('writer-complete')
        completed = record('consumer-finished')
        assert completed['disposition'] == 'done'
        assert completed['key'] == [observed[key] for key in ('task', 'agent', 'origin', 'session', 'result')]
    final = _wait_for_terminal(_base(port), reply['task_id'])
    assert final['task']['status'] == 'completed'
    with sqlite3.connect(root / 'happyranch.db') as conn:
        children = conn.execute('SELECT id,status,assigned_agent FROM tasks WHERE parent_task_id=?', (reply['task_id'],)).fetchall()
        assert len(children) == 1 and children[0][1:] == ('completed' if status == 'completed' else 'failed', agent)
        rows = conn.execute("SELECT agent,payload FROM audit_log WHERE task_id=? AND action='review_verdict'", (children[0][0],)).fetchall()
        assert len(rows) == 1 and rows[0][0] == agent
        assert json.loads(rows[0][1]) == {'verdict': verdict if verdict is not None else 'approved' if status == 'completed' else 'rejected',
            'feedback': 'child outcome' if status == 'completed' else 'self-blocked: child outcome', 'reviewed_agent': agent}
        parent_results = conn.execute('SELECT id,session_id FROM task_results WHERE task_id=? ORDER BY id', (reply['task_id'],)).fetchall()
        assert len(parent_results) == 2 and all(type(row[0]) is int and row[0] > 0 and row[1] for row in parent_results)
        assert parent_results[0][1] != parent_results[1][1]

        if recovery:
            episode = conn.execute('SELECT origin_session_id,recovery_session_id,accepted_result_id,accepted_result_session_id,state,provider_session_id FROM task_completion_recoveries WHERE task_id=?', (children[0][0],)).fetchall()
            assert len(episode) == 1
            S0, S1, R, accepted_session, state, provider = episode[0]
            assert S0 and S1 and S0 != S1 and provider
            assert type(R) is int and R > 0 and accepted_session == S1 and state == 'callback_consumed'
            selected = conn.execute('SELECT task_id,agent,session_id,verdict FROM task_results WHERE id=?', (R,)).fetchone()
            assert selected == (children[0][0], agent, S1, verdict)
            if selected_before is not None:
                assert conn.execute('SELECT * FROM task_results WHERE id=?', (R,)).fetchone() == selected_before
            if original_review is not None:
                assert conn.execute("SELECT id,task_id,agent,action,payload,timestamp FROM audit_log WHERE task_id=? AND action='review_verdict'", (children[0][0],)).fetchall() == [original_review]
            assert conn.execute('SELECT COUNT(*) FROM task_results WHERE task_id=?', (children[0][0],)).fetchone()[0] == 1
            bound = [json.loads(row[0]) for row in conn.execute(
                "SELECT payload FROM audit_log WHERE task_id=? AND action='completion_report' ORDER BY id",
                (children[0][0],)) if '_result_row_id' in json.loads(row[0])]
            assert len(bound) == 1 and bound[0]['_result_row_id'] == R and bound[0]['_recovery_session_id'] == S1
            witness = Path(str(plan) + '.calls.jsonl')
            calls = [json.loads(line) for line in witness.read_text().splitlines()]
            child_calls = [call for call in calls if call['task'] == children[0][0]]
            assert [{key: call[key] for key in ('task', 'session', 'agent')} for call in child_calls] == [
                {'task': children[0][0], 'session': S0, 'agent': agent},
                {'task': children[0][0], 'session': S1, 'agent': agent},
            ]
            assert all(call['workspace'] == str(root / 'workspaces' / agent) for call in child_calls)
            assert 'resume' not in child_calls[0]['argv']
            assert 'resume' in child_calls[1]['argv'] and provider in child_calls[1]['argv']
            assert f'binding task={children[0][0]} session={S1}' in child_calls[1]['prompt']
            # The genuine original tuple cannot append a second result or
            # replace the ledger after the selected recovery callback wins.
            frozen = conn.execute('SELECT * FROM task_results WHERE task_id=? ORDER BY id', (children[0][0],)).fetchall()
            stale = httpx.post(_base(port) + f'/tasks/{children[0][0]}/completion',
                headers=_auth_headers(), json={'session_id': S0, 'agent': agent,
                    'status': 'completed', 'output_summary': 'late original', 'confidence': 90})
            assert stale.status_code == 409, stale.text
            assert conn.execute('SELECT * FROM task_results WHERE task_id=? ORDER BY id', (children[0][0],)).fetchall() == frozen


@pytest.mark.parametrize('roster_kind', ['human', 'legacy-agent-control'])
def test_c5_schema_history_publication_and_portability(runtime: Path, tmp_path: Path,
                                                       roster_kind: str) -> None:
    """Real current publication/cold reader plus a pinned admission input.

    This L subcase owns current JSON/cold-process admission. Historical
    activation/receipt fixtures, compatible M restore and old-source execution
    are separately required; this case does not substitute for those proofs.
    """
    from tests.helpers.integration_stub_guard.guard import manifest
    binding = manifest()
    if roster_kind == 'human':
        data = yaml.safe_load((runtime / 'org/teams.yaml').read_text())
        data['teams']['default'] = {'manager': {'kind': 'human', 'principal': 'founder'},
                                    'workers': ['consultant_head', 'consultant_codex']}
        data.update(default_team='default', task_default_team='engineering')
        (runtime / 'org/teams.yaml').write_text(yaml.safe_dump(data))
        seed_workspace(runtime, 'consultant_head')
        seed_workspace(runtime, 'consultant_codex', executor='codex')
    # No daemon owns this fixture. Each actual attachment process exits/closes
    # before independent source/row readback and before the next process.
    attached = _attach_process(runtime)
    assert attached.returncode == 0, attached.stderr
    authority = runtime / 'org/.workflow-authority.json'
    raw = authority.read_bytes()
    snapshot = json.loads(raw)
    assert snapshot['schema_version'] == 2
    assert snapshot['task_default_team'] == 'engineering'
    assert all(set(row['manager']) == {'kind', 'principal'} for row in snapshot['teams'])
    if roster_kind == 'human':
        assert snapshot['default_team'] == 'default'
        assert next(row for row in snapshot['teams'] if row['name'] == 'default') == {
            'name': 'default', 'manager': {'kind': 'human', 'principal': 'founder'},
            'workers': ['consultant_codex', 'consultant_head']}
        assert 'founder' not in [row['name'] for row in snapshot['agents']]
    else:
        assert snapshot['default_team'] == 'engineering'
        assert all(row['manager']['kind'] == 'agent' for row in snapshot['teams'])
    with sqlite3.connect(runtime / 'happyranch.db') as conn:
        before = conn.execute('SELECT * FROM workflow_publication_journals ORDER BY rowid').fetchall()
        pointer = conn.execute('SELECT current_generation,snapshot_digest,state FROM workflow_authority_pointers WHERE namespace=?', ('org/test',)).fetchone()
        assert pointer == (1, hashlib.sha256(raw).hexdigest(), 'ready')
        assert conn.execute('SELECT COUNT(*) FROM workflow_publication_leases').fetchone()[0] == 0
    reopened = _attach_process(runtime)
    assert reopened.returncode == 0, reopened.stderr
    assert authority.read_bytes() == raw
    with sqlite3.connect(runtime / 'happyranch.db') as conn:
        assert conn.execute('SELECT * FROM workflow_publication_journals ORDER BY rowid').fetchall() == before
    probe = Path(binding['source']) / 'tests/helpers/human_team_incompatible_reader_probe.py'
    reader_home = tmp_path / 'reader-home'
    reader_home.mkdir(mode=0o700)
    # Parent admission already closes the environment. Override only this
    # separately owned reader's home; never attach an ambient registered org.
    reader_environment = {**os.environ, 'HAPPYRANCH_DAEMON_HOME': str(reader_home)}
    command = [sys.executable, '-I', str(probe), '--source', binding['source'],
               '--source-sha', binding['revision'], '--root', str(runtime), '--org', 'test',
               '--operation', 'capture-admission', '--expect', 'admitted',
               '--snapshot-digest', hashlib.sha256(raw).hexdigest()]
    actual = subprocess.run(command, env=reader_environment, capture_output=True, text=True, timeout=30)
    assert actual.returncode == 0, (actual.stdout, actual.stderr)
    receipt = json.loads(actual.stdout.splitlines()[-1])
    assert receipt['actual'] == 'admitted' and receipt['persisted_readback_unchanged']
    assert receipt['reader_source_sha'] == binding['revision']
    assert receipt['snapshot_digest'] == hashlib.sha256(raw).hexdigest()
    (tmp_path / 'C5-current-reader-receipt.json').write_text(json.dumps(
        {'command': command, 'daemon_home': str(reader_home), 'exit': actual.returncode,
         'receipt': receipt}, sort_keys=True))


@pytest.mark.parametrize('refusal', ['no-inhibition', 'design-plan-not-manifest', 'wrong-digest'])
def test_c8_preflight_refusals_and_backup_cas(runtime: Path, tmp_path: Path, refusal: str) -> None:
    """L-only early input refusals through the genuine standalone utility.

    These controls require no systemd/process census or M simulation. Complete
    checked-backup CAS, mutation syscall/frame witnesses and successful recovery
    remain separate M/capability cases. Byte equality is not no-write proof.
    """
    from tests.helpers.human_team_incompatible_reader_probe import _closed_files
    from tests.helpers.integration_stub_guard.guard import manifest
    binding = manifest()
    attached = _attach_process(runtime)
    assert attached.returncode == 0, attached.stderr
    # This is explicitly a proposed input, never a production check receipt.
    proposed = tmp_path / 'proposed-plan.json'
    raw = json.dumps({'kind': 'THR296-design-plan-v1', 'source_sha': binding['revision'],
                      'operation_id': 'unexecuted-proposal', 'containment': {}}).encode()
    proposed.write_bytes(raw)
    arguments = ['--runtime-root', str(runtime.parent.parent), '--org', 'test']
    if refusal == 'no-inhibition':
        arguments.extend(['--check', '--plan', str(proposed)])
        expected = 'actual_persistent_restart_inhibition_required'
    else:
        arguments.extend(['--apply', '--manifest', str(proposed), '--expected-digest',
                          '0' * 64 if refusal == 'wrong-digest' else hashlib.sha256(raw).hexdigest()])
        expected = ('manifest_digest_mismatch' if refusal == 'wrong-digest'
                    else 'real_checked_manifest_and_exact_candidate_required')
    before = _closed_files(runtime.parent.parent)
    launcher = '''
import runpy,sys
from pathlib import Path
source=Path(sys.argv[1])
sys.dont_write_bytecode=True
sys.path.insert(0,str(source))
script=source/'scripts/migrate_human_team_roster.py'
sys.argv=[str(script),*sys.argv[2:]]
runpy.run_path(str(script),run_name='__main__')
'''
    command = [sys.executable, '-I', '-c', launcher, binding['source'], *arguments]
    actual = subprocess.run(command, capture_output=True, text=True, timeout=30)
    assert actual.returncode == 1, (actual.stdout, actual.stderr)
    assert expected in actual.stderr, actual.stderr
    assert _closed_files(runtime.parent.parent) == before
    (tmp_path / 'C8-refusal-receipt.json').write_text(json.dumps(
        {'source_sha': binding['revision'], 'command': command, 'exit': actual.returncode,
         'refusal': expected, 'closed_file_readback_unchanged': True,
         'transient_write_proof': 'separate observer required', 'M_success': 'not attempted'}, sort_keys=True))


CONTEXTS = [(agent, kind) for agent in ('consultant_head', 'consultant_codex')
            for kind in ('task', 'thread', 'dream', 'wake', 'schedule')]


@pytest.mark.parametrize('agent,kind', CONTEXTS, ids=[
    ('head' if agent == 'consultant_head' else 'codex') + '-' + kind for agent, kind in CONTEXTS])
def test_c7_both_resume_resets_and_worker_contexts(
    runtime: Path, request: pytest.FixtureRequest, tmp_path: Path, agent: str, kind: str,
    fake_claude_plan_env: Path, fake_codex_plan_env: Path,
    fake_claude_thread_plan_env: Path,
) -> None:
    """L context proof through real producers, callbacks and final SQL.

    Closed fixture setup invokes both native reset owners; it establishes no
    successful operator migration, inhibition, backup or M receipt. The real M
    utility/reset/crash proof is a separate prerequisite, never inferred here.
    """
    from runtime.infrastructure.database import Database
    from runtime.models import ScheduleKind, ThreadMessageKind, ThreadRecord, ThreadStatus
    from runtime.orchestrator.schedule_service import ScheduleService
    from tests.helpers.integration_stub_guard.guard import manifest
    binding = manifest()
    _seed_human_roster(runtime)
    retained_memory = {}
    for name in ('consultant_head', 'consultant_codex'):
        memory = runtime / 'workspaces' / name / 'learnings.md'
        memory.write_text(f'# Retained memory: {name}\nC7 prior worker knowledge.\n')
        retained_memory[memory] = memory.read_bytes()
    attached = _attach_process(runtime)
    assert attached.returncode == 0, attached.stderr
    # A meaningful retained archived episode is unrelated to the new eligible
    # reply. The fixture may seed old continuity, never runtime results/claims.
    db = Database(runtime / 'happyranch.db')
    now = datetime.now(timezone.utc)
    control = 'THR-001'
    db.insert_thread(ThreadRecord(id=control, subject='retained control',
                                  status=ThreadStatus.ARCHIVED, archived_at=now))
    for name, provider in (('consultant_head', 'claude'), ('consultant_codex', 'codex'),
                           ('dev_agent', 'claude')):
        db._conn.execute('INSERT INTO thread_participants VALUES (?,?,?,?,?,?)',
                         (control, name, now.isoformat(), 'founder', 'obsolete-' + name, 7))
        db._conn.execute('''INSERT INTO thread_reply_delivery_state
            (thread_id,agent_name,acknowledged_through_seq,required_through_seq,updated_at)
            VALUES (?,?,?,?,?)''', (control, name, 7, 9, now.isoformat()))
        db._conn.execute('''INSERT INTO thread_reply_breaker_episodes
            (thread_id,agent_name,executor_key,episode_id,state,consecutive_failures,
             opened_at,cooldown_until,last_failure_category,updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?)''', (control, name, provider, 'retained-' + name,
                'open', 3, now.isoformat(), (now + timedelta(days=1)).isoformat(),
                'provider_failure', now.isoformat()))
    db._conn.commit()
    eligible_thread = None
    if kind == 'thread':
        eligible_thread = 'THR-002'
        db.insert_thread(ThreadRecord(id=eligible_thread, subject='eligible full context'))
        for index in range(7):
            db.append_thread_message(thread_id=eligible_thread, speaker='founder',
                kind=ThreadMessageKind.MESSAGE,
                body_markdown='C7-EARLY-CONTEXT-MARKER' if index == 0 else f'retained context {index}')
        db._conn.execute('INSERT INTO thread_participants VALUES (?,?,?,?,?,?)',
            (eligible_thread, agent, now.isoformat(), 'founder', 'obsolete-' + agent, 7))
        db._conn.execute('''INSERT INTO thread_reply_delivery_state
            (thread_id,agent_name,acknowledged_through_seq,required_through_seq,updated_at)
            VALUES (?,?,?,?,?)''', (eligible_thread, agent, 7, 7, now.isoformat()))
        db._conn.commit()
    preserved_tables = ('threads', 'thread_messages', 'thread_invocations',
                        'thread_reply_delivery_state', 'thread_reply_breaker_episodes',
                        'thread_reply_breaker_receipts')
    before = {table: db._conn.execute(f'SELECT * FROM {table} ORDER BY rowid').fetchall()
              for table in preserved_tables}
    third = tuple(db._conn.execute('SELECT * FROM thread_participants WHERE agent_name=?',
                                  ('dev_agent',)).fetchone())
    for name in ('consultant_head', 'consultant_codex'):
        expected_rows = 2 if name == agent and kind == 'thread' else 1
        assert db.reset_thread_sessions_for_agent(name, audit_scope_id='config:human-team-roster:C7',
            audit_agent='founder', audit_reason='fixture demotion continuity') == expected_rows
    assert {table: db._conn.execute(f'SELECT * FROM {table} ORDER BY rowid').fetchall()
            for table in preserved_tables} == before
    assert tuple(db._conn.execute('SELECT * FROM thread_participants WHERE agent_name=?',
                                  ('dev_agent',)).fetchone()) == third
    assert [tuple(row) for row in db._conn.execute('''SELECT agent_name,agent_session_id,last_resumed_seq
        FROM thread_participants WHERE thread_id='THR-001' AND agent_name LIKE 'consultant_%' ORDER BY agent_name''')] == [
        ('consultant_codex', None, 0), ('consultant_head', None, 0)]
    invalidations = db._conn.execute("SELECT payload FROM audit_log WHERE task_id=? AND action='thread_session_invalidated' ORDER BY id",
                                    ('config:human-team-roster:C7',)).fetchall()
    assert [json.loads(row[0]) for row in invalidations] == [
        {'reason': 'fixture demotion continuity', 'rows': 2 if name == agent and kind == 'thread' else 1, 'name': name}
        for name in ('consultant_head', 'consultant_codex')]
    if kind == 'dream':
        db.upsert_org_setting('dreaming', json.dumps({'enabled': True,
            'schedule': {'time': '00:00', 'timezone': 'UTC', 'catch_up_on_startup': True},
            'agents': {'mode': 'whitelist', 'include': [agent]}}))
    if kind == 'wake':
        definition = runtime / 'org/agents' / (agent + '.md')
        definition.write_text(definition.read_text() + '\n## Routine Tasks\n- C7 own routine\n')
        db.upsert_org_setting('working_hours', json.dumps({'enabled': True,
            'agents': {'mode': 'whitelist', 'include': [agent]},
            'default': {'mode': 'continuous', 'interval': '24h', 'timezone': 'UTC',
                        'catch_up_on_startup': True}}))
    schedule_id = None
    if kind == 'schedule':
        schedule_id = ScheduleService(db).create(agent_name=agent, team='default',
            kind=ScheduleKind.ONE_SHOT, fire_at=datetime.now(timezone.utc) + timedelta(seconds=1), recurrence=None,
            timezone='UTC', normalized_brief='C7 own scheduled root',
            source_instruction='explicit isolated fixture one-shot').id
    db.close()
    capture = tmp_path / 'actual-contexts.jsonl'
    helper = Path(binding['source']) / 'tests/helpers/human_team_context_plan.py'
    for plan, provider in ((fake_claude_plan_env, 'claude'), (fake_claude_thread_plan_env, 'claude'),
                           (fake_codex_plan_env, 'codex')):
        # DeterministicPlan authenticates exact bytes before the real stub runs.
        import shlex
        plan.write_text('#!/usr/bin/env bash\nset -euo pipefail\npython ' +
            shlex.quote(str(helper)) + ' --provider ' + provider + ' --capture ' +
            shlex.quote(str(capture)) + '\n')
    port = request.getfixturevalue('live_daemon')
    base = _base(port)
    task_id = thread_id = None
    if kind == 'task':
        task_id = httpx.post(base + '/tasks', headers=_auth_headers(), json={
            'owner': agent, 'team': 'default', 'brief': 'C7 actual worker root'}).raise_for_status().json()['task_id']
    if kind == 'thread':
        thread_id = eligible_thread
        httpx.post(base + f'/threads/{thread_id}/send', headers=_auth_headers(), json={
            'body_markdown': 'C7 actual founder sends next message'}).raise_for_status()
    deadline = time.monotonic() + 150
    records = []
    while time.monotonic() < deadline:
        if capture.exists():
            records = [json.loads(line) for line in capture.read_text().splitlines() if line]
            matching = [row for row in records if row['kind'] == kind and row['agent'] == agent]
            if matching and matching[0]['callback_exit'] == 0:
                break
        time.sleep(0.1)
    else:
        pytest.fail(f'actual {kind} callback absent: {records}')
    actual = matching[0]
    assert actual['source_sha'] == binding['revision']
    assert actual['workspace'] == str(runtime / 'workspaces' / agent)
    assert actual['provider'] == ('claude' if agent == 'consultant_head' else 'codex')
    assert 'obsolete-' not in json.dumps(actual['stub_argv'])
    assert 'Team Head' not in actual['prompt']
    assert 'role: worker' in actual['definition_bytes'] and 'team: default' in actual['definition_bytes']
    assert actual['generated_files']['CLAUDE.md']['raw_link'] == 'AGENTS.md'
    for provider_root in ('.agents/skills/', '.claude/skills/'):
        assert any(path.startswith(provider_root) for path in actual['skill_links'])
        assert not any(path == provider_root + 'manage-agent' for path in actual['skill_links'])
    with sqlite3.connect(runtime / 'happyranch.db') as conn:
        for table in ('thread_reply_delivery_state', 'thread_reply_breaker_episodes'):
            column_names = [column[0] for column in conn.execute(f'SELECT * FROM {table} LIMIT 0').description]
            index = column_names.index('thread_id')
            assert conn.execute(f'SELECT * FROM {table} WHERE thread_id=? ORDER BY rowid', (control,)).fetchall() == [tuple(row) for row in before[table] if row[index] == control]
        if kind == 'thread':
            token = actual['identity']['invocation_token']
            assert actual['identity']['thread_id'] == thread_id
            assert 'C7-EARLY-CONTEXT-MARKER' in actual['prompt']
            # Callback commit precedes provider exit; wait for the real runner's
            # consumption rather than declaring a successful callback terminal.
            final_deadline = time.monotonic() + 30
            while time.monotonic() < final_deadline:
                invocation = conn.execute('SELECT status,reply_message_seq FROM thread_invocations WHERE invocation_token=?', (token,)).fetchone()
                if invocation and invocation[0] == 'consumed':
                    break
                time.sleep(0.1)
            assert invocation and invocation[0] == 'consumed', invocation
            assert conn.execute('SELECT speaker,body_markdown FROM thread_messages WHERE thread_id=? AND seq=?',
                                (thread_id, invocation[1])).fetchone() == (agent, 'C7 genuine current worker reply')
        elif kind in ('dream', 'wake', 'schedule'):
            table = {'dream': 'dreams', 'wake': 'work_hours', 'schedule': 'schedules'}[kind]
            context_id = actual['identity']['context_id']
            row = conn.execute(f'SELECT * FROM {table} WHERE id=?', (context_id,))
            columns = [column[0] for column in row.description]
            value = dict(zip(columns, row.fetchone(), strict=True))
            assert value['agent_name'] == agent
            assert value['status'] == ('fired' if kind == 'schedule' else 'completed')
            transcript = Path(value['transcript_path'])
            if not transcript.is_absolute():
                transcript = runtime / transcript
            assert transcript.is_file() and transcript.read_text()
            if kind == 'dream':
                assert value['ended_at'] and value['new_learnings_count'] == value['kb_candidate_count'] == 0
                assert value['founder_thread_id'] is None
                assert conn.execute('SELECT COUNT(*) FROM tasks').fetchone()[0] == 0
            else:
                spawned = json.loads(value['spawned_task_ids'])
                assert len(spawned) == 1
                task_id = spawned[0]
                if kind == 'schedule':
                    assert context_id == schedule_id and value['active'] == 0 and value['fire_count'] == 1
                else:
                    assert value['ended_at'] and value['spawned_task_count'] == 1
    if task_id is not None:
        final = _wait_for_terminal(base, task_id)
        assert final['task']['status'] == 'completed'
        with sqlite3.connect(runtime / 'happyranch.db') as conn:
            assert conn.execute('SELECT assigned_agent,team FROM tasks WHERE id=?', (task_id,)).fetchone() == (agent, 'default')
            results = conn.execute('SELECT id,agent,session_id FROM task_results WHERE task_id=?', (task_id,)).fetchall()
            assert len(results) == 1 and type(results[0][0]) is int and results[0][0] > 0
            assert results[0][1] == agent and results[0][2]
    assert {path: path.read_bytes() for path in retained_memory} == retained_memory
    (tmp_path / 'C7-context-receipt.json').write_text(json.dumps({
        'source_sha': binding['revision'], 'context': kind, 'agent': agent,
        'captured_callback': actual, 'L_context_only': True,
        'retained_memory_sha256': {str(path.relative_to(runtime)): hashlib.sha256(raw).hexdigest()
                                   for path, raw in retained_memory.items()},
        'M_utility_reset_proof': 'separate unexecuted prerequisite'}, sort_keys=True))
