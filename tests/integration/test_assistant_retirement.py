"""Finite shipping retirement cases; disposable parent required, no real provider.

These drive the daemon/HTTP/CLI and real ordinary executable callbacks. They
are new shipping cases, not relabeled unit bodies. Same-root callback tails are
characterization: run baseline and candidate independently; retain failures.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import time

import httpx
import pytest
import websockets.sync.client

from runtime.daemon import paths
from tests.helpers.assistant_retirement_artifact_driver import (
    LEGACY_CASES, RETIRED, owned_processes, process_table, resolve_refs, seed_legacy, snapshot,
)
from tests.helpers.integration_stub_guard.guard import assert_launch_witness
from tests.integration.conftest import seed_agent_definition
from tests.integration.test_end_to_end import _init_agent, _submit_task, _write_plan

pytestmark = pytest.mark.integration




@pytest.fixture
def retirement_settings(monkeypatch):
    monkeypatch.setenv('HAPPYRANCH_EXECUTOR_RATE_LIMIT_BACKOFF_SECONDS','[90]')
    monkeypatch.setenv('HAPPYRANCH_EXECUTOR_LAUNCH_SPACING_SECONDS','0')


@pytest.fixture
def shipping(retirement_settings, runtime, live_daemon_idle):
    # Static roster is set before runtime attachment; no fake session/DB writes.
    for agent in ('engineering_head','dev_agent'):
        seed_agent_definition(runtime,agent,executor='codex')
    return {'api':f'http://127.0.0.1:{live_daemon_idle}/api/v1',
            'org':runtime,'headers':{'Authorization':'Bearer '+paths.read_token()},
            'pid':int(paths.pid_file().read_text())}


def request(venue,method,path,payload=None):
    return httpx.request(method,venue['api']+path,json=payload,
                         headers=venue['headers'],timeout=10)


def cli(*args):
    # The supported parent binds this source-specific console to candidate CLI.
    return subprocess.run(['happyranch',*args],capture_output=True,text=True,timeout=30)


def wait_for(predicate,seconds=30):
    deadline=time.monotonic()+seconds
    while time.monotonic()<deadline:
        value=predicate()
        if value:return value
        time.sleep(.1)
    raise AssertionError('bounded shipping observation did not become ready')


def callback_recorder(receipt):
    """Observe the genuine CLI callback, including a failing characterization."""
    return f'''
retirement_callback() {{
    callback_out="$PWD/retirement-$session_id.stdout"
    callback_err="$PWD/retirement-$session_id.stderr"
    if report_completion "$@" > "$callback_out" 2> "$callback_err"; then
        callback_exit=0
    else
        callback_exit=$?
    fi
    python - "$task_id" "$session_id" "$1" "$callback_exit" "$callback_out" "$callback_err" <<'CALLBACK_OBSERVATION'
import json,sys
from pathlib import Path
task,session,agent,code,out,err=sys.argv[1:]
with Path({str(receipt)!r}).open('a') as stream:
    stream.write(json.dumps({{'task':task,'session':session,'agent':agent,'exit':int(code),
                             'stdout':Path(out).read_text(),'stderr':Path(err).read_text()}})+'\\n')
CALLBACK_OBSERVATION
    return "$callback_exit"
}}
'''


def reopen(venue):
    script=Path(__file__).resolve().parents[2]/'scripts/daemon.sh'
    env=dict(os.environ)
    for key in ('HAPPYRANCH_HOST_SESSION_ID','HAPPYRANCH_HOST_SESSION_MANIFEST'):
        env.pop(key,None)
    subprocess.run([str(script),'stop'],check=True,env=env,timeout=20)
    subprocess.run([str(script),'start'],check=True,env=env,timeout=30)
    def ready():
        if not paths.port_file().exists():return False
        api=f'http://127.0.0.1:{paths.port_file().read_text().strip()}/api/v1'
        try:
            if httpx.get(api+'/health',timeout=1).status_code!=200:return False
        except httpx.HTTPError:return False
        venue['api']=api;venue['pid']=int(paths.pid_file().read_text());return True
    wait_for(ready)


def test_fresh_cli_lifecycle_creates_no_assistant(shipping,runtime_container):
    assert cli('init',str(runtime_container)).returncode==0
    for stage in range(2):
        assert cli('use',str(runtime_container)).returncode==0
        for path in ('/runtime','/orgs','/health','/orgs/test/settings','/orgs/test/audit','/orgs/test/tokens'):
            assert request(shipping,'GET',path).status_code==200,path
        assert not (runtime_container/'system').exists()
        reopen(shipping)
        assert not (runtime_container/'system').exists()


@pytest.mark.parametrize('variant',LEGACY_CASES)
def test_legacy_no_follow_survives_init_use_shutdown_reopen(shipping,runtime_container,tmp_path,variant):
    outside=tmp_path/'outside';seed_legacy(runtime_container,outside,variant)
    old=snapshot(runtime_container/'system');sentinel=snapshot(outside)
    # Reserved org workspace fence is separately tested with ordinary migration.
    reserved=shipping['org']/'workspaces/system_assistant';reserved.mkdir(parents=True)
    (reserved/'agent.yaml').symlink_to(outside/'sentinel')
    fenced=snapshot(reserved)
    assert cli('init',str(runtime_container)).returncode==0
    for stage in range(2):
        assert cli('use',str(runtime_container)).returncode==0
        for path in ('/runtime','/health','/orgs/test/settings','/orgs/test/audit','/orgs/test/tokens'):
            assert request(shipping,'GET',path).status_code==200
        assert old==snapshot(runtime_container/'system') and sentinel==snapshot(outside)
        assert fenced==snapshot(reserved)
        reopen(shipping)
        assert old==snapshot(runtime_container/'system') and sentinel==snapshot(outside)
        assert fenced==snapshot(reserved)
    # Snapshot equality is no-write evidence only. Removed-reader inspection is
    # reported separately; these tests never infer a filesystem access trace.


def test_served_rest_ws_absence_and_surviving_auth(shipping,runtime_container):
    assert cli('init',str(runtime_container)).returncode==0
    full=httpx.get(shipping['api'].removesuffix('/api/v1')+'/openapi.json',timeout=10).json()
    resolve_refs(full)
    assert not any(path.startswith('/api/v1/assistant') for path in full['paths'])
    before=snapshot(runtime_container/'system')
    for method,path in (*RETIRED,('GET','/assistant/session')):
        for token in (shipping['headers'],{}, {'Authorization':'Bearer wrong-fixture-token'}):
            response=httpx.request(method,shipping['api']+path,headers=token,
                                   json={} if method!='GET' else None,timeout=10)
            assert response.status_code in (401,403,404,405),response.text
    for headers in ({},{'Authorization':'Bearer wrong-fixture-token'}):
        assert httpx.get(shipping['api']+'/runtime',headers=headers,timeout=5).status_code in (401,403)
    assert request(shipping,'GET','/runtime').status_code==200
    for headers in (shipping['headers'],{}, {'Authorization':'Bearer wrong-fixture-token'}):
        try:
            with websockets.sync.client.connect(shipping['api'].replace('http:','ws:')+'/assistant/a-mode',
                                                additional_headers=headers,open_timeout=3):
                pytest.fail('retired websocket accepted')
        except websockets.exceptions.InvalidStatus:pass
    assert before==snapshot(runtime_container/'system')


def test_cli_parser_retired_forms_do_not_touch_registry(shipping,tmp_path):
    good=tmp_path/'valid.json';good.write_text('{}');bad=tmp_path/'malformed.json';bad.write_bytes(b'\xff{')
    watched=[paths.runtimes_file(),paths.daemon_home()/'executors.json',good,bad]
    before=[snapshot(path) for path in watched]
    assert 'assistant' not in cli('--help').stdout
    rows=[[],['status'],['init'],['init','--repair'],['init','--reconfigure'],['repair']]
    rows += [['register','--from-file',str(p)] for p in (good,bad)]
    rows += [['register','--executor','codex','--command',str(tmp_path/'owned-stub'),'--argv',value]
             for value in ('["stub"]','{bad')]
    for row in rows:
        result=cli('assistant',*row)
        assert result.returncode==2 and 'invalid choice' in result.stderr
        assert not owned_processes(shipping['pid'],shipping['org'].parent.parent,[])
    assert before==[snapshot(path) for path in watched]
    for name in ('init','runtime','use','web','report-completion'):assert cli(name,'--help').returncode==0


def prepare_task(shipping,runtime_container,plan):
    assert cli('init',str(runtime_container)).returncode==0
    org_api=shipping['api']+'/orgs/test'
    for agent in ('engineering_head','dev_agent'):_init_agent(org_api,agent,shipping['headers'])
    return _submit_task(org_api,'retirement shipping task',shipping['headers'])


def detail(shipping,task):return request(shipping,'GET','/orgs/test/tasks/'+task).json()


def assert_terminal(shipping,task,launches,container,seconds=40):
    result=wait_for(lambda: (body if (body:=detail(shipping,task))['task']['status'] in ('completed','failed','cancelled') else None),seconds)
    assert result['task']['status']=='completed',result
    assert result['results'] and all(row['session_id'] for row in result['results'])
    wait_for(lambda:not owned_processes(shipping['pid'],container,launches),10)
    metrics=request(shipping,'GET','/metrics').json()
    assert metrics['executor_sessions_active']==0
    host=metrics['host_sessions']
    assert host['receipts']['recent'] and all(row['quiescent'] for row in host['receipts']['recent'])
    assert host['admission']['active']==0 and host['residue']['survivors_count']==0
    assert_launch_witness('codex')
    return result


@pytest.mark.parametrize('same_root_register',[False,True])
def test_held_owner_swap_and_same_root_characterization(shipping,runtime_container,fake_codex_plan_env,tmp_path,same_root_register):
    release=tmp_path/'release';started=tmp_path/'started.json';callbacks=tmp_path/'callbacks.jsonl'
    _write_plan(fake_codex_plan_env, callback_recorder(callbacks)+f'''
        task_id=$1; session_id=$2; org_slug=$3
        python - "$task_id" "$session_id" "$$" <<'OBSERVE'
import json,sys,time
from pathlib import Path
from tests.helpers.assistant_retirement_artifact_driver import process_table
Path({str(started)!r}).write_text(json.dumps({{'task':sys.argv[1],'session':sys.argv[2],'process':next(r for r in process_table() if r['pid']==int(sys.argv[3]))}}))
OBSERVE
        for attempt in $(seq 1 600); do test -e {shlex.quote(str(release))} && break; sleep .1; done
        test -e {shlex.quote(str(release))}
        retirement_callback engineering_head '{{"action":"done","summary":"ordinary tail"}}'
    ''')
    parent=prepare_task(shipping,runtime_container,fake_codex_plan_env)
    wait_for(started.exists);marker=json.loads(started.read_text());assert marker['task']==parent
    b=runtime_container.parent/'runtime-b'
    if same_root_register:
        assert request(shipping,'POST','/runtime',{'path':str(runtime_container)}).status_code==200
        release.write_text('release')
        # Characterization deliberately asserts no hardcoded unknown_session.
        wait_for(lambda: not owned_processes(shipping['pid'],runtime_container,[marker['process']]),15)
        body=detail(shipping,parent)
        assert callbacks.exists(), 'authentic CLI callback receipt missing'
        observed=[json.loads(line) for line in callbacks.read_text().splitlines()]
        print('R4.1 CHARACTERIZATION '+json.dumps({'callbacks':observed,'task':body['task'],
            'results':body['results'],'audit':body['audit_log'],'processes':
            owned_processes(shipping['pid'],runtime_container,[marker['process']])},sort_keys=True))
        callback_files=list((shipping['org']/'workspaces/engineering_head').glob('completion-*.json'))
        assert callback_files, 'authentic callback file missing'
        # A residual baseline failure must remain a failure in the report.
        if any(row['exit']!=0 for row in observed) or body['task']['status']!='completed':pytest.fail('R4.1 authentic first callback did not complete; compare separate baseline receipt; shared session/auth repair excluded')
        assert_terminal(shipping,parent,[marker['process']],runtime_container)
    else:
        for suffix,target in [('/use',runtime_container),('',b),('/use',b)]:
            response=request(shipping,'POST','/runtime'+suffix,{'path':str(target)})
            assert response.status_code==409 and response.json()['detail']['code']=='active_tasks_in_flight'
            assert parent in response.json()['detail']['task_ids']
            assert request(shipping,'GET','/runtime').json()['runtime']==str(runtime_container)
        release.write_text('release');history=assert_terminal(shipping,parent,[marker['process']],runtime_container)
        observed=[json.loads(line) for line in callbacks.read_text().splitlines()]
        assert observed and all(row['exit']==0 for row in observed),observed
        assert any(row['task']==parent and row['session']==result['session_id']
                   for row in observed for result in history['results'])
        assert request(shipping,'POST','/runtime/use',{'path':str(runtime_container)}).status_code==200
        assert request(shipping,'POST','/runtime/use',{'path':str(b)}).status_code==200
        assert request(shipping,'POST','/runtime',{'path':str(runtime_container)}).status_code==200
        assert detail(shipping,parent)['results']==history['results']


@pytest.mark.parametrize('same_root_register',[False,True])
def test_nonrunning_retry_owner_all_runtime_census(shipping,runtime_container,fake_codex_plan_env,tmp_path,same_root_register):
    marker=tmp_path/'retry-start.json'; delegated=tmp_path/'delegated';callbacks=tmp_path/'callbacks.jsonl'
    _write_plan(fake_codex_plan_env, callback_recorder(callbacks)+f'''
        task_id=$1; session_id=$2; org_slug=$3; agent="${{PWD##*/}}"
        if test "$agent" = engineering_head && ! test -e {shlex.quote(str(delegated))}; then
            touch {shlex.quote(str(delegated))}
            retirement_callback engineering_head '{{"action":"delegate","agent":"dev_agent","prompt":"ordinary retry child"}}'
        elif test "$agent" = dev_agent && ! test -e {shlex.quote(str(marker))}; then
            python - "$task_id" "$session_id" "$$" <<'OBSERVE'
import json,sys,time
from pathlib import Path
from tests.helpers.assistant_retirement_artifact_driver import process_table
Path({str(marker)!r}).write_text(json.dumps({{'task':sys.argv[1],'session':sys.argv[2],'at':time.monotonic(),'process':next(r for r in process_table() if r['pid']==int(sys.argv[3]))}}))
OBSERVE
            echo 'rate limit' >&2; exit 1
        else
            retirement_callback "$agent" '{{"action":"done","summary":"ordinary retry tail"}}'
        fi
    ''')
    parent=prepare_task(shipping,runtime_container,fake_codex_plan_env)
    wait_for(marker.exists);witness=json.loads(marker.read_text());child=witness['task']
    # All orgs/producers stay enabled. Detached scopes/groups and every daemon
    # descendant are censused; no sleeping/running child substitutes for retry.
    def window():
        p=detail(shipping,parent);metrics=request(shipping,'GET','/metrics').json()['host_sessions']
        recent=metrics['receipts']['recent']
        return (p['task'].get('block_kind')=='delegated' and recent and
                all(row['quiescent'] for row in recent) and
                not owned_processes(shipping['pid'],runtime_container,[witness['process']]))
    wait_for(window,20);assert time.monotonic()<witness['at']+60
    assert not detail(shipping,child)['results']
    assert detail(shipping,child)['task']['parent_task_id']==parent
    orgs=request(shipping,'GET','/orgs').json();print('R4.5 all-org inventory '+json.dumps(orgs))
    deadline=time.monotonic()+30;b=runtime_container.parent/'runtime-b'
    operations=[('',runtime_container)] if same_root_register else [('/use',runtime_container),('',b),('/use',b)]
    for suffix,target in operations:
        assert time.monotonic()<deadline
        before=owned_processes(shipping['pid'],runtime_container,[witness['process']]);assert not before
        response=request(shipping,'POST','/runtime'+suffix,{'path':str(target)})
        after=owned_processes(shipping['pid'],runtime_container,[witness['process']]);assert not after
        print('R4.5 census '+json.dumps({'before':before,'after':after,'status':response.status_code,'body':response.json()}))
        assert response.status_code==(200 if same_root_register else 409)
    # Natural supervisor wait/readmission, no forged session or direct result.
    try:
        assert_terminal(shipping,parent,[witness['process']],runtime_container,160)
        child_body=detail(shipping,child)
        observed=[json.loads(line) for line in callbacks.read_text().splitlines()]
        assert observed and all(row['exit']==0 for row in observed),observed
        assert child_body['task']['status']=='completed' and child_body['results']
        assert any(row['task']==child and row['session']==result['session_id']
                   for row in observed for result in child_body['results'])
    except AssertionError:
        if same_root_register:
            print('R4.5 SAME-ROOT CHARACTERIZATION FAILURE '+json.dumps({
                'parent':detail(shipping,parent),'child':detail(shipping,child),
                'callbacks':callbacks.read_text() if callbacks.exists() else None,
                'processes':owned_processes(shipping['pid'],runtime_container,[witness['process']])}))
        raise
    assert request(shipping,'POST','/runtime/use',{'path':str(runtime_container)}).status_code==200


def test_quiescent_concurrent_org_read_swap_shutdown_reopen(shipping,runtime_container):
    assert cli('init',str(runtime_container)).returncode==0
    b=runtime_container.parent/'runtime-b'
    assert request(shipping,'POST','/runtime',{'path':str(b)}).status_code==200
    assert request(shipping,'POST','/runtime/use',{'path':str(runtime_container)}).status_code==200
    a_orgs=request(shipping,'GET','/orgs').json()
    b_orgs={'orgs':[]}
    with ThreadPoolExecutor(max_workers=2) as pool:
        for target in (b,runtime_container,b,runtime_container):
            reads=pool.submit(lambda:[request(shipping,'GET','/orgs') for _ in range(8)])
            swapped=pool.submit(request,shipping,'POST','/runtime/use',{'path':str(target)})
            assert swapped.result(timeout=20).status_code==200
            for response in reads.result(timeout=20):
                assert response.status_code==200
                assert response.json() in (a_orgs,b_orgs),response.text
            assert request(shipping,'GET','/runtime').json()['runtime']==str(target)
            assert request(shipping,'GET','/metrics').status_code==200
    assert not owned_processes(shipping['pid'],runtime_container,[])
    reopen(shipping)
    assert request(shipping,'GET','/orgs').json()==a_orgs
    assert request(shipping,'GET','/runtime').json()['runtime']==str(runtime_container)
    assert not (runtime_container/'system').exists() and not (b/'system').exists()
