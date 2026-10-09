"""Finite THR296 shipping cases. Execute only via authorized disposable parent.

No production-host runs. RF5/RF6 real-process cuts and ten real context sources
are authored here; C1–C5 writer/history/compatible-reader sources are finite sources; C6–C10 remaining
maintenance, context and browser closure stays with the parent. Authoring is never execution evidence.
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
writer_cuts={'writer_busy_before_consumption-inline_worker','writer_reacquired_before_retry',
             'writer_cancelled_before_retry',
    'writer_busy_before_consumption-shared_loop', 'writer_busy_before_consumption-startup_loop',
    'writer_busy_before_consumption-zombie_loop', 'writer_busy_before_consumption-portability_loop',
    'writer_busy_after_job_drain', 'writer_busy_no_job_reentry',
    'loss-before_consumption-cancel', 'loss-before_consumption-binding_replacement',
    'loss-after_job_drain-cancel', 'loss-after_job_drain-binding_replacement',
    'no_job_reentry_terminal_cancel_refusal', 'loss-no_job_reentry-cancel', 'loss-no_job_reentry-binding_replacement',
    'shutdown_while_deferred', 'late_inline_unbound'}
writer_path=source/'runtime/orchestrator/run_step.py'
writer_data=writer_path.read_bytes()
assert writer_data==subprocess.check_output(['git','-C',str(source),'show',revision+':runtime/orchestrator/run_step.py'])
writer_codes={code.co_qualname:hashlib.sha256(marshal.dumps(code)).hexdigest()
    for code in members(compile(writer_data,str(writer_path),'exec',dont_inherit=True,optimize=sys.flags.optimize))
    if code.co_qualname in {'_submit_human_failed_recovery','_drive_human_failed_recovery','_HumanFailedRecoveryOperation.finish'}}
assert len(writer_codes)==3
extra_codes={}
for rel,qualnames in {
    'runtime/infrastructure/database.py':{'Database.admit_task_completion_callback'},
    'runtime/daemon/jobs_runner.py':{'terminate_jobs_for_task'},
    'runtime/daemon/app.py':{'_wire_then_start_workers'},
    'runtime/daemon/routes/tasks.py':{'cancel_task'},
}.items():
    extra_path=source/rel; extra_data=extra_path.read_bytes()
    assert extra_data==subprocess.check_output(['git','-C',str(source),'show',revision+':'+rel])
    for code in members(compile(extra_data,str(extra_path),'exec',dont_inherit=True,optimize=sys.flags.optimize)):
        if code.co_qualname in qualnames:
            extra_codes[(str(extra_path),code.co_qualname)]=hashlib.sha256(marshal.dumps(code)).hexdigest()
assert len(extra_codes)==4
writer_state={}
def record_writer(event, **fields):
    target=receipt.with_name(receipt.name+'.'+event)
    value={'event':event,'pid':os.getpid(),'thread':threading.get_ident(),
        'source_sha':revision,'file_sha256':hashlib.sha256(writer_data).hexdigest(),**fields}
    fd=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'w') as out:
        json.dump(value,out,sort_keys=True);out.flush();os.fsync(out.fileno())
def selected_identity(orch,task_id,result_id):
    with orch._db._lock:
        rows=orch._db._conn.execute('SELECT * FROM task_completion_recoveries WHERE task_id=?',(task_id,)).fetchall()
        assert len(rows)==1
        episode=dict(rows[0])
    assert episode['accepted_result_id']==result_id and type(result_id) is int and result_id>0
    return {'task':task_id,'agent':episode['agent'],'origin':episode['origin_session_id'],
        'session':episode['recovery_session_id'],'result':result_id}
async def shipping_caller(orch,caller):
    from runtime.orchestrator.orchestrator import completion_report_from_result_row
    from runtime.orchestrator.run_step import _consume_completion_report,_handoff_consumed_recovery_terminal_effects,_enqueue_parent_if_waiting
    identity=writer_state['identity']; task=orch._db.get_task(identity['task'])
    selected=orch._db.get_latest_task_result(identity['task'],identity['agent'],identity['session'])
    assert selected['id']==identity['result']
    report=completion_report_from_result_row(task.id,selected,fallback_agent=identity['agent'])
    if caller=='shared_loop':
        operation=_consume_completion_report(orch,task.id,report,result_row_id=selected['id'])
    elif caller=='startup_loop':
        from runtime.daemon.__main__ import _sweep_on_startup
        # Child is still live: this sweep cannot independently orphan-wake its
        # waiting parent. No whole-parent sweep is used for the no-job case.
        assert task.status.value=='in_progress'
        _sweep_on_startup(orch._db,orch._queue,orch._slug,orch)
    elif caller=='zombie_loop':
        from runtime.daemon.zombie_reaper import _consume_zombie_fingerprint
        operation=_consume_zombie_fingerprint(orch._db,task.id,selected,task,orch)
    elif caller=='portability_loop':
        # A real fixture subprocess has exited. Its observed PID is used only
        # by native signal-0 liveness; no persisted PID is signalled as control.
        import httpx
        from runtime.daemon import paths
        from datetime import datetime,timedelta,timezone
        reaped=subprocess.Popen([sys.executable,'-c','pass'])
        assert reaped.wait(timeout=2)==0
        orch._db.update_task(task.id,executor_pid=reaped.pid,
            last_heartbeat=(datetime.now(timezone.utc)-timedelta(minutes=5)).isoformat())
        def request():
            return httpx.post('http://127.0.0.1:'+paths.port_file().read_text().strip()+
                '/api/v1/orgs/'+orch._slug+'/reconcile-portability',
                headers={'Authorization':'Bearer '+paths.read_token()},
                json={'candidate_task_id':task.id,'disposition':'consume_result',
                      'evidence':{'fixture':'actual reaped subprocess'}},timeout=30)
        reply=await asyncio.to_thread(request)
        record_writer('portability-response',status=reply.status_code,body=reply.json(),**identity)
    else:
        assert caller=='no_job_reentry'
        assert not orch._db.get_running_job_task_ids()
        operation=_handoff_consumed_recovery_terminal_effects(orch,task.id,identity['agent'],identity['session'],
            identity['result'],'failed',after_recovery_cleanup=lambda:_enqueue_parent_if_waiting(
                orch,task.id,root_auto_revisit_spawned=False))
async def prepared_late_cancel(org):
    # The authentic cancel route reads a live child, then waits on its own
    # existing org DB lock. Selected failure/drain uses publisher + DB RLock,
    # so that native route can commit a later cancellation winner unchanged.
    import httpx
    from runtime.daemon import paths
    identity=writer_state['identity']
    await org.db_lock.acquire()
    def request():
        return httpx.post('http://127.0.0.1:'+paths.port_file().read_text().strip()+
            '/api/v1/orgs/'+org.slug+'/tasks/'+identity['task']+'/cancel',
            headers={'Authorization':'Bearer '+paths.read_token()},
            json={'rationale':'actual late C4 cancellation','cascade':False},timeout=120)
    pending=asyncio.create_task(asyncio.to_thread(request))
    try:
        permission=receipt.with_name(receipt.name+'.commit-late-cancel')
        while not permission.exists():await asyncio.sleep(0.01)
    finally:
        org.db_lock.release()
    reply=await pending
    record_writer('late-cancel-response',status=reply.status_code,body=reply.json(),**identity)
async def held_writer(org,caller=None):
    for number in range(2 if cut=='writer_reacquired_before_retry' else 1):
        async with org._profile_coordinator.consumer_writer(org=org,
                publisher='THR296-isolated-writer-control',consumer='consultant_head',preserve=True):
            assert org.workflow_authority._async_writer_lock.locked()
            record_writer('held-'+str(number),loop=id(asyncio.get_running_loop()),**writer_state['identity'])
            writer_state['ready'].set()
            if caller is not None and number==0:
                writer_state['caller']=asyncio.create_task(shipping_caller(writer_state['orch'],caller))
            release=receipt.with_name(receipt.name+'.release-'+str(number))
            while not release.exists():
                control=receipt.with_name(receipt.name+'.binding-replacement')
                if control.exists() and 'replacement' not in writer_state:
                    orch=writer_state['orch'];identity=writer_state['identity']
                    # Same source-owned ordinary binding publication primitives;
                    # this is an explicit fixture winner, never a launch/result.
                    session=orch._build_session_id()
                    orch._db.update_task(identity['task'],assigned_agent=identity['agent'],current_session_id=session)
                    orch._sessions.set_active(identity['task'],identity['agent'],session,org_slug=orch._slug)
                    writer_state['replacement']=session
                    record_writer('binding-replaced',replacement=session,**identity)
                shutdown=receipt.with_name(receipt.name+'.shutdown')
                if shutdown.exists() and 'shutdown' not in writer_state:
                    await writer_state['orch']._queue.stop()
                    writer_state['shutdown']=True
                    record_writer('shutdown-observed',**writer_state['identity'])
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
        writer_state.update(identity=selected_identity(orch,task.id,local['result_id']),
            ready=threading.Event(),orch=orch)
        org=orch._workflow_drafts.org
        if cut in ('loss-after_job_drain-cancel','loss-no_job_reentry-cancel'):
            writer_state['cancel_ready']=threading.Event()
            writer_state['late_cancel']=asyncio.run_coroutine_threadsafe(prepared_late_cancel(org),orch._main_loop)
            assert writer_state['cancel_ready'].wait(10),'actual cancel route did not traverse live child'
        if 'after_job_drain' not in cut and cut!='loss-no_job_reentry-cancel':
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
        if operation.task_id==writer_state['identity']['task'] and 'finished' not in writer_state:
            writer_state['finished']=True
            record_writer('consumer-finished',disposition=operation.disposition,phase=operation.phase,
                key=list(operation.key) if operation.key is not None else None,**writer_state['identity'])
def observe(frame,event,arg):
    if (cut=='loss-no_job_reentry-cancel' and event=='return' and frame.f_code.co_filename==str(path)
            and frame.f_code.co_qualname=='TasksMixin.apply_human_failed_recovery_effect'
            and frame.f_locals.get('effect')=='marker' and arg=='progressed'):
        assert hashlib.sha256(marshal.dumps(frame.f_code)).hexdigest()==expected
        # C9.i's after-real-marker/before-parent localization, combined with
        # L4's genuine admission/process identities. The native SQL/effect
        # returned successfully; this test-side source observer interrupts its
        # finite tail before parent handoff. No result/marker/task is fabricated.
        # This fault is UNEXECUTED and is not a captured shipping receipt.
        loop=asyncio.get_running_loop();orch=writer_state['orch']
        loop.call_soon(sys.setprofile,observe)
        loop.create_task(held_writer(orch._workflow_drafts.org,'no_job_reentry'))
        raise RuntimeError('test-side actual marker return interruption')

    extra=(frame.f_code.co_filename,frame.f_code.co_qualname)
    if cut in writer_cuts and extra in extra_codes:
        assert hashlib.sha256(marshal.dumps(frame.f_code)).hexdigest()==extra_codes[extra]
        local=frame.f_locals
        if (extra[1]=='Database.admit_task_completion_callback' and event=='return' and arg
                and (cut.endswith(('shared_loop','startup_loop','zombie_loop','portability_loop'))
                     or cut=='late_inline_unbound') and not writer_state):
            db=local['self'];org=db._workflow_drafts.org;orch=org.orchestrator
            task=db.get_task(local['task_id'])
            if task is not None and task.task_type=='subtask' and task.team=='default':
                selected=db.get_accepted_task_completion_recovery_result(task_id=task.id,agent=task.assigned_agent)
                assert selected is not None
                writer_state.update(identity=selected_identity(orch,task.id,selected['id']),ready=threading.Event(),orch=orch)
                caller=cut.rsplit('-',1)[-1] if cut!='late_inline_unbound' else 'shared_loop'
                if cut=='late_inline_unbound':
                    writer_state['caller']=asyncio.get_running_loop().create_task(shipping_caller(orch,caller))
                else:
                    writer_state['writer']=asyncio.get_running_loop().create_task(held_writer(org,caller))
        elif (extra[1]=='cancel_task' and event=='return' and cut in ('loss-after_job_drain-cancel','loss-no_job_reentry-cancel')
                and writer_state and local.get('to_cancel')==[writer_state['identity']['task']]
                and 'cancel_traversed' not in writer_state):
            assert local['org'].db_lock.locked()
            writer_state['cancel_traversed']=True
            record_writer('cancel-traversed',to_cancel=local['to_cancel'],**writer_state['identity'])
            writer_state['cancel_ready'].set()
        elif (extra[1]=='terminate_jobs_for_task' and event=='call' and 'after_job_drain' in cut
                and writer_state and 'writer' not in writer_state and local['task_id']==writer_state['identity']['task']):
            orch=writer_state['orch']
            writer_state['writer']=asyncio.get_running_loop().create_task(held_writer(orch._workflow_drafts.org))
        elif (extra[1]=='_wire_then_start_workers' and event=='return' and 'no_job_reentry' in cut and not writer_state):
            state=local['state'];org=state.get_org('test');orch=org.orchestrator
            owners=org.db.get_consumed_task_completion_recovery_owners()
            assert len(owners)==1 and owners[0]['status']=='failed'
            selected=owners[0]
            writer_state.update(identity=selected_identity(orch,selected['task_id'],selected['accepted_result_id']),
                ready=threading.Event(),orch=orch)
            writer_state['writer']=asyncio.get_running_loop().create_task(held_writer(org,'no_job_reentry'))
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
if 'no_job_reentry' in cut and cut!='loss-no_job_reentry-cancel':
    # Actual cold compatible state + app/lifespan/queue/provider pipeline. This
    # specifically isolates consumed-child handoff: no generic parked-parent
    # startup sweep is invoked to satisfy the writer-only progression oracle.
    import uvicorn
    from runtime.config import Settings
    from runtime.daemon import paths,runtimes
    from runtime.daemon.state import DaemonState
    from runtime.runtime import RuntimeDir
    from runtime.daemon.app import create_app
    from runtime.daemon.__main__ import _bind_port,_install_signal_handlers
    paths.ensure_daemon_home();paths.ensure_token()
    state=DaemonState.from_runtime(RuntimeDir.load(runtimes.load().active),Settings())
    app=create_app(state)
    sock,port=_bind_port(state.settings.daemon_bind_host,state.settings.daemon_port)
    paths.port_file().write_text(str(port));paths.pid_file().write_text(str(os.getpid()))
    _install_signal_handlers(state)
    uvicorn.Server(uvicorn.Config(app,log_level='info',lifespan='on')).run(sockets=[sock])
else:
    sys.argv=['runtime.daemon']
    runpy.run_module('runtime.daemon',run_name='__main__')
'''
    def start(selected: str) -> int:
        process = subprocess.Popen([sys.executable, '-I', '-c', launcher, binding['source'],
            binding['revision'], selected, str(witness)], cwd=binding['source'],
            env=_nested_daemon_env(), stdout=log, stderr=log, start_new_session=True)
        processes.append(process)
        if hasattr(request.node, '_roster_fault_daemon'):
            request.node._roster_fault_daemon['process'] = process
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
        port = start('marker' if 'no_job_reentry' in cut and cut!='loss-no_job_reentry-cancel' else cut)
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
                administration: bool = False, c4_cut: str | None = None,
                policy_activation: dict | None = None, failure_blocker: bool = False) -> None:
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
''' + f"root = pathlib.Path({str(root)!r})\nwitness = pathlib.Path({str(witness)!r})\nstatus = {status!r}\nverdict = {verdict!r}\nself_child = {self_child!r}\nrecovery = {recovery!r}\nattempted_decision = {attempted_decision!r}\nadministration = {administration!r}\nc4_cut = {c4_cut!r}\npolicy_activation = {policy_activation!r}\nfailure_blocker = {failure_blocker!r}\n" + '''
with witness.open('a') as out:
    out.write(json.dumps({'task': T, 'session': S, 'agent': agent,
        'prompt': os.environ['HAPPYRANCH_TEST_ACTUAL_PROMPT'],
        'argv': json.loads(os.environ['HAPPYRANCH_TEST_CONTEXT_ARGV_JSON']),
        'workspace': workspace, 'pid': os.getpid()}) + '\\n')
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
    assert policy_activation is not None
    assert control['family'] == 'v2' and control['selector_id'] == policy_activation['expected_selector_id']
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
    activation = {**policy_activation, 'request_id': 'worker-existing-activate-' + T}
    reply = httpx.post(base + '/agents/' + agent + '/team-escalation-policy/v2/activations',
                      headers=headers, json=activation)
    assert reply.status_code == 404 and reply.json()['detail']['code'] == 'policy_surface_not_available', reply.text
    observed.append({'action': 'worker-policy-existing-activate', 'status': 404, 'detail': reply.json()['detail']})
    pathlib.Path(str(witness) + '.administration.json').write_text(json.dumps(observed))
payload = {'task_id': T, 'session_id': S, 'agent': agent, 'status': 'completed', 'summary': 'root done', 'confidence': 90}
if attempted_decision is not None and parent is None and prior == 0:
    payload['decision'] = attempted_decision
elif self_child and parent is None and children == 0:
    payload['decision'] = {'action': 'delegate', 'agent': agent, 'prompt': 'self child'}
elif parent is None and failure_blocker:
    with sqlite3.connect((root / 'happyranch.db').as_uri() + '?mode=ro', uri=True) as observer:
        failed = observer.execute('SELECT id,status,note FROM tasks WHERE parent_task_id=?', (T,)).fetchall()
    assert len(failed) == 1 and failed[0][1:] == ('failed', 'self-blocked: child outcome'), failed
    prompt = os.environ['HAPPYRANCH_TEST_ACTUAL_PROMPT']
    assert failed[0][0] in prompt and 'child outcome' in prompt, prompt
    payload['decision'] = {'action': 'escalate', 'reason': 'self child failed: founder decision required'}
elif parent is None:
    payload['decision'] = {'action': 'done', 'summary': 'root done'}
else:
    payload.update(status=status, verdict=verdict, summary='child outcome')
file = pathlib.Path(workspace) / ('completion-' + S + '.json')
file.write_text(json.dumps(payload))
if parent is not None and c4_cut is not None and ('after_job_drain' in c4_cut):
    # Genuine task-owned runner jobs, separate from the blocked result's empty
    # wait list. The original failure is retained and one opaque job drains.
    import time
    for suffix, script in [('prior', 'echo retained-failure >&2; sleep 30'),
                           ('running', 'echo owned-running; while true; do sleep 1; done')]:
        request = pathlib.Path(workspace) / ('job-' + suffix + '-' + S + '.json')
        request.write_text(json.dumps({'task_id': T, 'session_id': S, 'title': 'C4-' + suffix,
            'script': script, 'interpreter': 'bash', 'review_required': False, 'persistent': suffix == 'running',
            'max_runtime_seconds': 1 if suffix == 'prior' else 180}))
        actual = subprocess.run(['happyranch', 'jobs', 'submit', '--org', org, '--from-file', str(request)],
            capture_output=True, text=True, timeout=30)
        assert actual.returncode == 0, actual.stderr
        job_ids = re.findall(r'JOB-[0-9]+', actual.stdout)
        assert len(set(job_ids)) == 1, actual.stdout
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            with sqlite3.connect((root / 'happyranch.db').as_uri() + '?mode=ro', uri=True) as observer:
                job = observer.execute('SELECT status,exit_code,stderr_head FROM jobs WHERE id=?', (job_ids[0],)).fetchone()
            if job and job[0] == ('failed' if suffix == 'prior' else 'running'): break
            time.sleep(0.05)
        assert job and job[0] == ('failed' if suffix == 'prior' else 'running'), job
        if suffix == 'prior': assert job[1] != 0 and 'retained-failure' in job[2]
subprocess.run(['happyranch', 'report-completion', '--org', org, '--from-file', str(file)], check=True)
if parent is not None and c4_cut is not None and (c4_cut.endswith(('shared_loop', 'startup_loop', 'zombie_loop', 'portability_loop'))
        or c4_cut == 'late_inline_unbound') and 'after_job_drain' not in c4_cut and 'no_job_reentry' not in c4_cut:
    # Callback is genuinely accepted; executor remains alive so a shared
    # consumer can finish before the actual inline unbound audit on exit.
    import time
    permission = pathlib.Path(str(witness) + '.allow-child-exit')
    deadline = time.monotonic() + 150
    while not permission.exists() and time.monotonic() < deadline: time.sleep(0.01)
    assert permission.exists(), 'actual executor-return barrier not released'
PLAN
''')


def _base(port: int) -> str:
    return f'http://127.0.0.1:{port}/api/v1/orgs/test'


PARTIAL_ROSTERS = [(head, codex, roster, empty)
                   for empty in (False, True)
                   for head, codex, roster in ((True, False, False), (False, True, False),
                       (False, False, True), (True, True, False), (True, False, True), (False, True, True))]


def _attach_process(root: Path, slug: str = 'test', *, save: bool = False,
                    reader_source: Path | None = None, reader_sha: str | None = None) -> subprocess.CompletedProcess[str]:
    """Actual cold OrgState attachment in a fresh candidate interpreter."""
    from tests.helpers.integration_stub_guard.guard import manifest
    binding = manifest()
    source = Path(binding['source']) if reader_source is None else reader_source
    revision = binding['revision'] if reader_sha is None else reader_sha
    assert source.is_absolute() and not source.is_symlink()
    assert subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD'], text=True).strip() == revision
    assert not subprocess.check_output(['git', '-C', str(source), 'status', '--porcelain']).strip()
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
    print(json.dumps({'agents':org.teams.all_agents(),'default':getattr(org.teams,'default_team','engineering'),
                      'task_default':getattr(org.teams,'task_default_team','engineering')}))
finally:
    org.close()
'''
    return subprocess.run([sys.executable, '-I', '-c', script, str(source), str(root), slug,
                           'save' if save else 'read'],
                          capture_output=True, text=True, timeout=30)


C1_ATTACH_CASES = [
    'unknown-manager-kind', 'unknown-human-principal', 'extra-manager-tag',
    'missing-manager-principal', 'blank-agent-principal', 'duplicate-worker',
    'cross-team-worker', 'duplicate-agent-manager', 'missing-worker-definition',
    'unregistered-active-worker', 'wrong-worker-team', 'wrong-worker-role',
    'wrong-manager-role', 'pending-worker-control', 'pending-manager-refused',
    'legacy-save-control', 'human-save-control', 'pending-manager-control',
    'missing-pending-manager', 'wrong-pending-manager-team', 'duplicate-active-pending-manager',
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
        if partial not in ('legacy-save-control', 'wrong-manager-role', 'duplicate-agent-manager',
            'pending-manager-refused', 'pending-manager-control', 'missing-pending-manager',
            'wrong-pending-manager-team', 'duplicate-active-pending-manager'):
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
        elif partial in ('pending-worker-control', 'pending-manager-refused', 'pending-manager-control',
                         'missing-pending-manager', 'wrong-pending-manager-team', 'duplicate-active-pending-manager'):
            name = 'consultant_codex' if partial == 'pending-worker-control' else 'engineering_head'
            pending = cold / 'org/agents/_pending'
            pending.mkdir(exist_ok=True)
            target = pending / f'{name}.md'
            original = cold / 'org/agents' / f'{name}.md'
            if partial == 'duplicate-active-pending-manager':
                target.write_bytes(original.read_bytes())
            else:
                original.rename(target)
            if partial == 'missing-pending-manager':
                target.unlink()
            elif partial in ('pending-manager-refused', 'wrong-pending-manager-team'):
                from runtime.orchestrator.agent_def import parse_agent_text, render_agent_text
                from dataclasses import replace
                definition = parse_agent_text(target.read_text(), expected_name=name)
                target.write_text(render_agent_text(replace(definition, role='worker')
                    if partial == 'pending-manager-refused' else replace(definition, team='content')))
        path.write_text(yaml.safe_dump(data))
        before = {str(p.relative_to(cold)): p.read_bytes() for p in (cold / 'org').rglob('*') if p.is_file()}
        successful = partial in ('pending-worker-control', 'legacy-save-control', 'human-save-control', 'pending-manager-control')
        actual = _attach_process(cold, save=partial.endswith('save-control'))
        if successful:
            assert actual.returncode == 0, actual.stderr
            value = json.loads(actual.stdout.splitlines()[-1])
            assert 'founder' not in value['agents']
            assert value['default'] == ('engineering' if partial in ('legacy-save-control','pending-manager-control') else 'default')
            assert value['task_default'] == 'engineering'
            assert yaml.safe_load(path.read_text()) == data
            expected = sorted(name for entry in teams.values() for name in
                ([entry['manager']] if isinstance(entry['manager'], str) else []) + entry['workers'])
            assert sorted(value['agents']) == expected
            if partial == 'pending-manager-control':
                assert not (cold / 'org/agents/engineering_head.md').exists()
                assert (cold / 'org/agents/_pending/engineering_head.md').read_bytes() == before['org/agents/_pending/engineering_head.md']
                with sqlite3.connect(cold / 'happyranch.db') as observer:
                    assert observer.execute("SELECT state FROM workflow_authority_pointers WHERE namespace='org/test'").fetchone() == ('fenced',)
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
    deadline = time.monotonic() + 30
    while True:
        dashboard = httpx.get(_base(port) + '/dashboard/summary', headers=_auth_headers())
        if dashboard.status_code != 503 or time.monotonic() >= deadline:
            break
        time.sleep(0.1)
    assert dashboard.status_code == 200, dashboard.text
    assert next(row for row in dashboard.json()['org_pulse'] if row['team'] == 'default')['lead'] == 'founder'
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
    ('', 'unknown_owner', 400),
    ('wrong-team-head', 'unknown_owner', 400),
    ('malformed-head', 'unknown_owner', 400),
    *[(case, None, 0) for case in ('internal-human', 'internal-legacy', 'internal-pending-manager',
        'internal-role-drift', 'queue-ownerless-human', 'cli-human-missing', 'cli-worker', 'cli-legacy')],
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
], ids=['missing-owner', 'blank-owner', 'wrong-team-definition', 'malformed-definition',
        'internal-human', 'internal-legacy', 'internal-pending-manager', 'internal-role-drift',
        'queue-ownerless-human', 'cli-human-missing', 'cli-worker', 'cli-legacy',
        'founder', 'unknown-worker', 'other-team-worker',
        'pending-worker', 'inactive-worker', 'wrong-worker-role', 'unknown-team', 'fresh-default-omitted',
        'head-with-attachment', 'codex-owner-only-with-attachment', 'legacy-omitted-with-attachment'])
def test_c2_owner_required_before_persistence(human_daemon: tuple[int, Path],
                                             owner: str | None, code: str | None, http_status: int, tmp_path: Path) -> None:
    port, root = human_daemon
    if owner is not None and owner.startswith(('internal-', 'queue-')):
        _c2_internal_admission_source(root, tmp_path, owner)
        return
    if owner is not None and owner.startswith('cli-'):
        with sqlite3.connect(root / 'happyranch.db') as observer:
            before = {name: observer.execute('SELECT * FROM ' + name + ' ORDER BY rowid').fetchall()
                      for name in ('tasks', 'task_attachments', 'task_results')}
        command = ['happyranch', 'run', '--org', 'test', '--brief', 'C2 genuine CLI routing']
        if owner == 'cli-human-missing':
            command += ['--team', 'default']
        elif owner == 'cli-worker':
            command += ['--team', 'default', '--owner', 'consultant_codex']
        actual = subprocess.run(command, capture_output=True, text=True, timeout=30)
        if owner == 'cli-human-missing':
            assert 'owner_required_for_human_team' in actual.stdout + actual.stderr
            with sqlite3.connect(root / 'happyranch.db') as observer:
                assert {name: observer.execute('SELECT * FROM ' + name + ' ORDER BY rowid').fetchall()
                        for name in before} == before
        else:
            assert actual.returncode == 0, actual.stderr
            import re
            task_ids = re.findall(r'Submitted (TASK-[0-9]+)', actual.stdout)
            assert len(task_ids) == 1, actual.stdout
            final = _wait_for_terminal(_base(port), task_ids[0])
            assert final['task']['status'] == 'completed'
            with sqlite3.connect(root / 'happyranch.db') as observer:
                assert observer.execute('SELECT team,assigned_agent FROM tasks WHERE id=?', (task_ids[0],)).fetchone() == (
                    ('default', 'consultant_codex') if owner == 'cli-worker' else ('engineering', 'engineering_head'))
                results = observer.execute('SELECT id,agent,session_id FROM task_results WHERE task_id=?', (task_ids[0],)).fetchall()
                assert len(results) == 1 and type(results[0][0]) is int and results[0][0] > 0 and results[0][2]
        return
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
    elif owner in ('wrong-role-head', 'wrong-team-head', 'malformed-head'):
        from dataclasses import replace
        from runtime.orchestrator.agent_def import parse_agent_text, render_agent_text
        path = root / 'org/agents/consultant_head.md'
        definition = parse_agent_text(path.read_text(), expected_name='consultant_head')
        if owner == 'malformed-head':
            path.write_text('---\nname: consultant_head\nteam: default\nrole: invalid-role\n---\n')
        else:
            changed = replace(definition, role='manager') if owner == 'wrong-role-head' else replace(definition, team='engineering')
            path.write_text(render_agent_text(changed))
    upload = httpx.post(base + '/tasks/attachments', headers=_auth_headers(),
                        params={'agent': 'founder'},
                        files={'file': ('roster.png', b'\x89PNG\r\n\x1a\nfixture', 'image/png')})
    assert upload.status_code == 200, upload.text
    attachment = upload.json()
    uploaded_blob = root / 'task-attachments' / attachment['storage_key']
    blob_before = uploaded_blob.read_bytes()
    assert blob_before == b'\x89PNG\r\n\x1a\nfixture'
    with sqlite3.connect(root / 'happyranch.db') as conn:
        before = conn.execute('SELECT COUNT(*) FROM tasks').fetchone()[0]
        before_attachments = conn.execute('SELECT COUNT(*) FROM task_attachments').fetchone()[0]
    body = {'team': 'default', 'brief': 'owner guard before any durable allocation',
            'attachments': [{'storage_key': attachment['storage_key'], 'display_name': 'roster.png'}]}
    if owner in ('legacy-omitted', 'fresh-omitted'):
        del body['team']
    elif owner in ('pending-head', 'wrong-role-head', 'wrong-team-head', 'malformed-head'):
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
    assert uploaded_blob.read_bytes() == blob_before
    with sqlite3.connect(root / 'happyranch.db') as conn:
        assert conn.execute('SELECT COUNT(*) FROM tasks').fetchone()[0] == before
        assert conn.execute('SELECT COUNT(*) FROM task_attachments').fetchone()[0] == before_attachments
        assert conn.execute('SELECT COUNT(*) FROM task_results').fetchone()[0] == 0


def _c2_internal_admission_source(root: Path, tmp_path: Path, case: str) -> None:
    """Finite cold internal submission and corrupt-root queue controls.

    The corrupt ownerless row is declared fixture input. No callback/result or
    final transition is seeded; API/CLI positive siblings own actual execution.
    """
    from tests.helpers.integration_stub_guard.guard import manifest
    binding = manifest()
    cold = tmp_path / 'internal-runtime'
    script = r"""
import asyncio,json,sqlite3,sys,shutil
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from runtime.runtime import RuntimeDir
from runtime.config import Settings
from runtime.daemon.org_state import OrgState
from runtime.daemon.state import DaemonState
from runtime.daemon.dispatcher import Dispatcher
from runtime.daemon.runner import enqueue_task
from runtime.models import TaskRecord
from runtime.orchestrator.agent_def import parse_agent_text,render_agent_text
from dataclasses import replace
rt=RuntimeDir.init(Path(sys.argv[2])); root=rt.orgs_dir/'test'
shutil.copytree(Path(sys.argv[3])/'org',root/'org')
org=OrgState.load(slug='test',root=root,settings=Settings(project_root=Path(sys.argv[1])))
case=sys.argv[4]
def snapshot():
    with sqlite3.connect(org.db.path) as observer:
        return {table:observer.execute('SELECT * FROM '+table+' ORDER BY rowid').fetchall()
                for table in ('tasks','task_results','task_attachments','audit_log')}
try:
    if case=='internal-pending-manager':
        pending=root/'org/agents/_pending';pending.mkdir(exist_ok=True)
        (root/'org/agents/engineering_head.md').rename(pending/'engineering_head.md')
    elif case=='internal-role-drift':
        path=root/'org/agents/engineering_head.md'
        definition=parse_agent_text(path.read_text(),expected_name='engineering_head')
        path.write_text(render_agent_text(replace(definition,role='worker')))
    before=snapshot()
    if case=='queue-ownerless-human':
        org.db.insert_task(TaskRecord(id='TASK-OWNERLESS',brief='declared corrupt ownerless root',team='default'))
        state=DaemonState(runtime=rt,settings=Settings(),orgs={'test':org})
        org.orchestrator.attach_queue(state.queue)
        async def drive():
            enqueue_task(state,'test','TASK-OWNERLESS')
            await state.queue.drain_sync(Dispatcher(state))
        asyncio.run(drive())
        after=snapshot()
        with sqlite3.connect(org.db.path) as observer:
            assert observer.execute('SELECT assigned_agent,current_session_id FROM tasks WHERE id=?',('TASK-OWNERLESS',)).fetchone()==(None,None)
        assert after['task_results']==before['task_results']
        assert after['task_attachments']==before['task_attachments']
        assert not [row for row in after['audit_log'] if row[3]=='session_start']
    elif case=='internal-legacy':
        task_id=org.orchestrator.create_task('native omitted owner control')
        with sqlite3.connect(org.db.path) as observer:
            assert observer.execute('SELECT team,status FROM tasks WHERE id=?',(task_id,)).fetchone()==('engineering','pending')
    else:
        try:org.orchestrator.create_task('must refuse before allocation',team='default' if case=='internal-human' else None)
        except ValueError as exc:
            assert str(exc)==('owner_required_for_human_team' if case=='internal-human' else 'unknown_owner'),str(exc)
        else:raise AssertionError('invalid internal owner allocated task')
        assert snapshot()==before
    assert not (root/'workspaces/founder').exists()
    print(json.dumps({'case':case,'checked':True}))
finally:org.close()
"""
    actual = subprocess.run([sys.executable, '-I', '-c', script, binding['source'],
        str(cold), str(root), case], capture_output=True, text=True, timeout=30)
    assert actual.returncode == 0, actual.stderr
    assert json.loads(actual.stdout.splitlines()[-1]) == {'case': case, 'checked': True}
    if case == 'queue-ownerless-human':
        assert 'owner_required_for_human_team' in actual.stderr


@pytest.mark.parametrize('agent', ['consultant_head', 'consultant_codex'], ids=['head', 'codex'])
@pytest.mark.parametrize('operation', ['root', 'self-child', 'self-child-failed', 'peer-delegate', 'peer-then', 'peer-fanout', 'supersede', 'administration'])
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
    policy_activation = None
    if operation == 'administration':
        # Actual authenticated synthetic Founder control creates a valid saved
        # release before the immutable denial baseline. This is fixture input,
        # never worker authority, live activation or product acceptance.
        control = httpx.get(_base(port) + '/agents/engineering_head/team-escalation-policy',
                            headers=_auth_headers()).raise_for_status().json()
        assert control['family'] == 'empty' and control['selector_epoch'] == 0
        saved = httpx.post(_base(port) + '/agents/engineering_head/team-escalation-policy/v2/releases',
            headers=_auth_headers(), json={**control['v2_starter'],
                'create_request_id': 'c3-fixture-create-' + agent,
                'activation_request_id': 'c3-fixture-select-' + agent,
                'based_on_selector_id': control['selector_id'], 'expected_selector_id': control['selector_id'],
                'action': 'bootstrap', 'acknowledge_shared_credential_attribution': True})
        assert saved.status_code == 201, saved.text
        receipt = saved.json()
        policy_activation = {'team': 'engineering', 'release_id': receipt['receipt']['release_id'],
            'expected_selector_id': receipt['selector_id'], 'action': 'activate',
            'acknowledge_shared_credential_attribution': True}
    _write_plan(plan, root, status='blocked' if operation == 'self-child-failed' else 'completed',
                verdict=None, self_child=operation in ('self-child', 'self-child-failed'),
                attempted_decision=decisions.get(operation), administration=operation == 'administration',
                policy_activation=policy_activation, failure_blocker=operation == 'self-child-failed')
    canonical_before = {str(path.relative_to(root)): path.read_bytes()
                        for path in (root / 'org').rglob('*.md') if path.is_file()}
    roster_before = (root / 'org/teams.yaml').read_bytes()
    tables = ('manager_supersessions', 'workflow_template_versions', 'workflow_template_publish_operations',
              'authority_policy_releases', 'authority_policy_activations', 'authority_policy_v2_releases',
              'authority_policy_v2_activations', 'authority_policy_active_selector',
              'authority_policy_active_selector_history', 'authority_policy_v2_control_audit')
    with sqlite3.connect(root / 'happyranch.db') as conn:
        rows_before = {name: conn.execute(f'SELECT * FROM {name}').fetchall() for name in tables}
    reply = httpx.post(_base(port) + '/tasks', json={'team': 'default', 'owner': agent, 'brief': 'ordinary root'}, headers=_auth_headers()).raise_for_status().json()
    final = _wait_for_terminal(_base(port), reply['task_id'])
    assert final['task']['status'] == ('failed' if operation == 'supersede' else
                                      'escalated' if operation == 'self-child-failed' else 'completed')
    with sqlite3.connect(root / 'happyranch.db') as conn:
        actual = conn.execute('SELECT id,session_id,agent FROM task_results WHERE task_id=?', (reply['task_id'],)).fetchall()
        genuine = [row for row in actual if row[1]]
        assert len(genuine) == (2 if operation.startswith('peer-') or operation.startswith('self-child') else 1)
        assert all(type(row[0]) is int and row[0] > 0 and row[2] == agent for row in genuine)
        children = conn.execute('SELECT id,team,assigned_agent,status FROM tasks WHERE parent_task_id=?', (reply['task_id'],)).fetchall()
        if operation.startswith('self-child'):
            assert len(children) == 1 and children[0][1:] == ('default', agent, 'failed' if operation == 'self-child-failed' else 'completed')
            child_results = conn.execute('SELECT id,agent,session_id FROM task_results WHERE task_id=?', (children[0][0],)).fetchall()
            assert len(child_results) == 1 and type(child_results[0][0]) is int and child_results[0][0] > 0
            assert child_results[0][1] == agent and child_results[0][2]
            assert genuine[0][1] != genuine[1][1]
        else:
            assert children == []
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
            'agent-manager-policy-control', 'worker-policy-read', 'worker-policy-create-activate',
            'worker-policy-existing-activate']
        assert not (root / 'org/agents/ungranted_worker.md').exists()
    # Current human-team worker is not an eligible manager policy target.
    denied = httpx.get(_base(port) + f'/agents/{agent}/team-escalation-policy', headers=_auth_headers())
    assert denied.status_code == 404


C4_SCENARIOS = [
    (agent, recovery, status, None)
    for agent, recovery in [('consultant_head', False), ('consultant_codex', False), ('consultant_codex', True)]
    for status in ('completed', 'blocked')
] + [('consultant_codex', True, 'blocked', cut) for cut in (
    'fail', 'review', 'writer_busy_before_consumption-inline_worker', 'writer_reacquired_before_retry',
    'writer_cancelled_before_retry',
    'writer_busy_before_consumption-shared_loop', 'writer_busy_before_consumption-startup_loop',
    'writer_busy_before_consumption-zombie_loop', 'writer_busy_before_consumption-portability_loop',
    'writer_busy_after_job_drain', 'writer_busy_no_job_reentry',
    'loss-before_consumption-cancel', 'loss-before_consumption-binding_replacement',
    'loss-after_job_drain-cancel', 'loss-after_job_drain-binding_replacement',
    'no_job_reentry_terminal_cancel_refusal', 'loss-no_job_reentry-cancel', 'loss-no_job_reentry-binding_replacement',
    'shutdown_while_deferred', 'late_inline_unbound')]


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
    _write_plan(plan, root, status=status, verdict=verdict, self_child=True, recovery=recovery, c4_cut=cut)
    reply = httpx.post(_base(port) + '/tasks', json={'team': 'default', 'owner': agent, 'brief': 'self child then final parent'}, headers=_auth_headers()).raise_for_status().json()
    original_review = None
    selected_before = None
    if cut is not None and cut not in ('fail', 'review', 'writer_busy_before_consumption-inline_worker',
            'writer_reacquired_before_retry', 'writer_cancelled_before_retry'):
        observed_cut = _c4_live_continuation_oracle(request, port, root, reply['task_id'], plan, cut, verdict)
        port, selected_before, original_review, stopped = observed_cut
        if stopped:
            return
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
    elif cut in ('writer_busy_before_consumption-inline_worker', 'writer_reacquired_before_retry',
                  'writer_cancelled_before_retry'):
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
        cancelled_before = None
        if cut == 'writer_cancelled_before_retry':
            cancelled = httpx.post(_base(port) + f'/tasks/{observed["task"]}/cancel',
                headers=_auth_headers(), json={'rationale': 'selected writer cancellation winner', 'cascade': False},
                timeout=10)
            assert cancelled.status_code == 200, cancelled.text
            assert observed['task'] in cancelled.json()['cancelled']
            with sqlite3.connect(root / 'happyranch.db') as conn:
                cancelled_before = conn.execute('SELECT status,cancelled_at,note,current_session_id FROM tasks WHERE id=?',
                    (observed['task'],)).fetchone()
                assert cancelled_before[0] == 'cancelled' and cancelled_before[1]
                assert cancelled_before[3] == observed['session']
        witness.with_name(witness.name + '.release-0').write_text('release owned native writer\n')
        if cut == 'writer_reacquired_before_retry':
            second = record('held-1')
            assert second['loop'] == observed['loop'] and second['task'] == observed['task']
            held_readback()
            witness.with_name(witness.name + '.release-1').write_text('release second owned native writer\n')
        record('writer-complete')
        completed = record('consumer-finished')
        if cut == 'writer_cancelled_before_retry':
            assert completed['disposition'] == 'lost_owner'
            actual_parent = _wait_for_terminal(_base(port), reply['task_id'])
            assert actual_parent['task']['status'] == 'completed'
            with sqlite3.connect(root / 'happyranch.db') as conn:
                assert conn.execute('SELECT status,cancelled_at,note,current_session_id FROM tasks WHERE id=?',
                    (observed['task'],)).fetchone() == cancelled_before
                assert conn.execute('SELECT * FROM task_results WHERE id=?', (observed['result'],)).fetchone() == selected_before
                assert conn.execute("SELECT COUNT(*) FROM audit_log WHERE task_id=? AND action='review_verdict'",
                    (observed['task'],)).fetchone() == (0,)
                assert conn.execute('SELECT state,accepted_result_id FROM task_completion_recoveries WHERE task_id=?',
                    (observed['task'],)).fetchone() == ('callback_accepted', observed['result'])
                parents = conn.execute('SELECT id,session_id FROM task_results WHERE task_id=? ORDER BY id',
                    (reply['task_id'],)).fetchall()
                assert len(parents) == 2 and all(type(row[0]) is int and row[0] > 0 for row in parents)
                assert parents[0][1] != parents[1][1]
            return  # cancellation's real parent progression is the winner
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



def _c5_shipping_graph_process(tmp_path: Path, *, restore: bool, explicit_profile: bool) -> None:
    """Finite real route/registration process. Restore requires separate M venue.

    This is authored source, not a successful restore or utility receipt. The
    clean parent supplies source/interpreter isolation; no new runner is added.
    """
    from tests.helpers.integration_stub_guard.guard import manifest
    binding = manifest()
    script = r'''
import asyncio,copy,hashlib,json,os,shutil,sqlite3,stat,sys
from dataclasses import asdict
from contextlib import ExitStack
from pathlib import Path
source,revision,venue,restore,explicit=sys.argv[1:]
source=Path(source);venue=Path(venue);restore=restore=='True';explicit=explicit=='True'
sys.dont_write_bytecode=True
sys.path.insert(0,str(source))
import subprocess
assert subprocess.check_output(['git','rev-parse','HEAD'],cwd=source,text=True).strip()==revision
assert not subprocess.check_output(['git','status','--porcelain'],cwd=source).strip()
assert sys.version_info[:2]==(3,14)
from fastapi.testclient import TestClient
from runtime.config import Settings
from runtime.daemon import paths,runtimes
from runtime.daemon.app import create_app
from runtime.daemon.org_state import OrgState
from runtime.daemon.state import DaemonState
from runtime.runtime import RuntimeDir
from runtime.workflows.authority import WorkflowAuthorityError
from runtime.workflows.draft_dispatch import DraftOwnershipError
from tests.workflows.authority_test_support import C5_SCHEMA1_HISTORY,C5_SCHEMA1_COMPLETED,seed_c5_schema1_history
from tests.helpers.human_team_incompatible_reader_probe import _closed_files
home=venue/'daemon-home';home.mkdir(mode=0o700)
os.environ['HAPPYRANCH_DAEMON_HOME']=str(home)
settings=Settings(project_root=source)
container=RuntimeDir.init(venue/'source-runtime')
runtimes.register(container.root)
state=DaemonState.from_runtime(container,settings)
client=TestClient(create_app(state))
client.headers.update({'Authorization':'Bearer '+paths.ensure_token()})
base='/api/v1/orgs/alpha'
owned=[]
def checked(method,path,body=None,status=200):
    response=getattr(client,method)(path,**({'json':body} if body is not None else {}))
    assert response.status_code==status,(path,response.status_code,response.text)
    return response.json()
def rows(org):
    with sqlite3.connect(org.db.path.resolve().as_uri()+'?mode=ro',uri=True) as reader:
        return {name:tuple(reader.execute('SELECT * FROM "'+name+'" ORDER BY rowid'))
                for name, in reader.execute("SELECT name FROM sqlite_schema WHERE type='table' ORDER BY name")}
def history(org):
    f=C5_SCHEMA1_HISTORY
    with sqlite3.connect(org.db.path.resolve().as_uri()+'?mode=ro',uri=True) as reader:
        assert reader.execute('SELECT snapshot_bytes,snapshot_digest FROM workflow_publication_journals WHERE id=?',('c5-fixed-schema1-history',)).fetchone()==(f['snapshot_bytes'],f['snapshot_digest'])
        for table,prefix in [('workflow_authorization_revisions','authorization'),('workflow_binding_snapshots','binding'),('workflow_contexts','context')]:
            column='authority' if prefix=='authorization' else prefix
            assert reader.execute('SELECT '+column+'_bytes,'+column+'_digest FROM '+table+' WHERE id=?',(f[prefix+'_id'],)).fetchone()==(f[prefix+'_bytes'],f[prefix+'_digest'])
        reader.row_factory=sqlite3.Row
        assert dict(reader.execute('SELECT * FROM task_results WHERE id=901').fetchone())==C5_SCHEMA1_COMPLETED['result']
        assert [dict(row) for row in reader.execute('SELECT * FROM workflow_draft_dispatch_events WHERE intent_id=? ORDER BY event_seq',(f['receipt']['intent_id'],))]==C5_SCHEMA1_COMPLETED['events']
        return {prefix:(f[prefix+'_id'],f[prefix+'_digest']) for prefix in ('authorization','binding','context')}
def profile_closure():
    from runtime.orchestrator.runtime_executor_store import load_runtime_profiles
    from runtime.orchestrator.executor_registry import get_registry
    from runtime.orchestrator.adapter_store import get_adapter
    profiles=load_runtime_profiles()
    if not explicit:
        assert profiles=={}
        return {}
    name='c5-portable-profile'
    profile=get_registry().get_profile(name);adapter=get_adapter(name+'-adapter')
    assert profile is not None and adapter is not None and adapter.status=='approved'
    assert profiles[name]=={'workspace_adapter_id':'pi','command_adapter_id':'custom-adapter:'+adapter.id}
    executable=Path(adapter.executable)
    assert executable.is_absolute() and executable.is_relative_to(home) and not executable.is_symlink()
    assert executable.stat().st_uid==os.getuid() and os.access(executable,os.X_OK)
    assert hashlib.sha256(executable.read_bytes()).hexdigest()==adapter.executable_hash
    files={}
    for path in (home/'executor_profiles.yaml',home/'adapters.yaml',executable):
        info=path.lstat()
        assert stat.S_ISREG(info.st_mode) and info.st_uid==os.getuid()
        files[str(path)]=(hashlib.sha256(path.read_bytes()).hexdigest(),stat.S_IMODE(info.st_mode),info.st_uid,info.st_gid)
    return dict(config=profiles,profile=asdict(profile),adapter=asdict(adapter),files=files)
def ready(org):
    capture=org.workflow_authority.capture_admission(); r=capture.ready
    raw=(org.root/'org/.workflow-authority.json').read_bytes()
    assert raw==r.snapshot_bytes and hashlib.sha256(raw).hexdigest()==r.snapshot_digest
    value=json.loads(raw)
    assert value['schema_version']==2
    assert value['default_team']==value['task_default_team']=='default'
    assert next(row for row in value['teams'] if row['name']=='default')=={'name':'default','manager':{'kind':'human','principal':'founder'},'workers':['consultant_codex','consultant_head']}
    assert all(row['name']!='founder' for row in value['agents'])
    assert [row['profile_name'] for row in value['machine_global_profiles']]==(['c5-portable-profile'] if explicit else [])
    with sqlite3.connect(org.db.path.resolve().as_uri()+'?mode=ro',uri=True) as reader:
        pointer=reader.execute('SELECT current_generation,journal_id,snapshot_digest,state FROM workflow_authority_pointers WHERE namespace=?',(r.namespace,)).fetchone()
        assert pointer[0]==r.generation and pointer[2:]==(r.snapshot_digest,'ready')
        assert reader.execute('SELECT snapshot_bytes,snapshot_digest,state FROM workflow_publication_journals WHERE id=?',(pointer[1],)).fetchone()==(raw,r.snapshot_digest,'cache_installed')
        assert reader.execute('SELECT COUNT(*) FROM workflow_publication_leases').fetchone()[0]==0
    return r
try:
    checked('post','/api/v1/orgs',{'slug':'alpha'})
    org=state.orgs['alpha'];owned.append(org)
    # Actual empty-org attachment is valid, but no reviewer means no authority.
    with sqlite3.connect(org.db.path.resolve().as_uri()+'?mode=ro',uri=True) as reader:
        assert reader.execute("SELECT state FROM workflow_authority_pointers WHERE namespace='org/alpha'").fetchone()==('fenced',)
        assert reader.execute('SELECT COUNT(*) FROM tasks').fetchone()[0]==0
    try: org.workflow_authority.capture_admission()
    except WorkflowAuthorityError as exc: assert exc.code=='authority_pointer_not_ready'
    else: raise AssertionError('missing-reviewer empty org was admitted')
    for name,team,role in [('engineering_manager','engineering','manager'),('code_reviewer','engineering','worker'),('dev_agent','engineering','worker'),('qa_engineer','engineering','worker'),('product_lead','product','manager')]:
        checked('post',base+'/agents',dict(name=name,role=role,executor='claude',description='C5 fixture',system_prompt='Bounded documents.',**({'new_team':team} if role=='manager' else {'team':team})))
    template=checked('post',base+'/workflows/templates/publish',dict(operation_key='c5-template',team_slug='product',template_name='product-design',expected_current_version=0,definition=json.loads(C5_SCHEMA1_HISTORY['context_bytes'])['template']),201)
    assert template['definition_digest']=='0d815497899d1e6f43022ec8c88574a36bbf295a05fbf00f411c8a8e324c7f57'
    assert checked('post',base+'/workflows/cutover/requests',dict(operation_key='c5-enable',action='enable',expected_generation=1))['state']=='enabled'
    f=seed_c5_schema1_history(org,completed=True)
    original=checked('post',base+'/workflows/activations',f['request'])
    assert {key:original[key] for key in f['receipt']}==f['receipt']
    assert original['state']=='completed' and original['pending'] is False
    from tests.workflows.authority_test_support import C5_SCHEMA1_COMPLETED
    with sqlite3.connect(org.db.path.resolve().as_uri()+'?mode=ro',uri=True) as reader:
        reader.row_factory=sqlite3.Row
        assert dict(reader.execute('SELECT * FROM task_results WHERE id=901').fetchone())==C5_SCHEMA1_COMPLETED['result']
    preserved=history(org)
    with ExitStack() as profile_stack:
        if explicit:
            # Existing finite profile fixture owner, not a profile-store seam.
            from tests.workflows.authority_test_support import c5_profile_fixture
            profile_stack.enter_context(c5_profile_fixture('c5-portable-profile'))
            checked('put',base+'/agents/dev_agent/executor',{'executor':'c5-portable-profile'})
        for name in ('consultant_head','consultant_codex'):
            checked('post',base+'/agents',dict(name=name,team='default',role='worker',executor='codex' if name.endswith('codex') else 'claude',description='C5 worker',system_prompt='Bounded documents.'))
        r=ready(org)
        request=copy.deepcopy(f['request']);request.update(operation_key='c5-schema2',instance_id='c5-schema2')
        request['authority']=dict(namespace=r.namespace,generation=r.generation,snapshot_digest=r.snapshot_digest)
        request['bindings']['product-lead']=dict(kind='agent',principal='consultant_head',team='default')
        sibling=copy.deepcopy(request);sibling.update(operation_key='c5-product-schema2',instance_id='c5-product-schema2')
        sibling['bindings']['product-lead']=dict(kind='agent',principal='product_lead',team='product')
        sibling_receipt=checked('post',base+'/workflows/activations',sibling,201)
        assert org.db.get_task(sibling_receipt['root_task_id']).assigned_agent=='product_lead'
        current=checked('post',base+'/workflows/activations',request,201)
        assert current['root_task_id']!=f['receipt']['root_task_id']
        assert org.db.get_task(current['root_task_id']).assigned_agent=='consultant_head'
        with sqlite3.connect(org.db.path.resolve().as_uri()+'?mode=ro',uri=True) as reader:
            raw,sha=reader.execute('SELECT context_bytes,context_digest FROM workflow_contexts WHERE id=(SELECT context_id FROM workflow_draft_dispatch_intents WHERE id=?)',(current['intent_id'],)).fetchone()
            assert hashlib.sha256(raw).hexdigest()==sha==current['context_digest']
            assert json.loads(raw)['authority_snapshot']==json.loads(r.snapshot_bytes)
            assert reader.execute("SELECT COUNT(*) FROM tasks WHERE assigned_agent='founder'").fetchone()[0]==0
            assert reader.execute('SELECT COUNT(*) FROM task_results WHERE task_id=?',(current['root_task_id'],)).fetchone()[0]==0
            assert reader.execute('SELECT state,session_id,final_result_id,host_launch_started FROM workflow_draft_dispatch_intents WHERE id=?',(current['intent_id'],)).fetchone()==('queued',None,None,0)
            sibling_context=reader.execute('SELECT context_bytes,context_digest FROM workflow_contexts WHERE id=(SELECT context_id FROM workflow_draft_dispatch_intents WHERE id=?)',(sibling_receipt['intent_id'],)).fetchone()
            assert hashlib.sha256(sibling_context[0]).hexdigest()==sibling_context[1]==sibling_receipt['context_digest']
            assert json.loads(sibling_context[0])['authority_snapshot']==json.loads(r.snapshot_bytes)
        # Current unknown-version negative is distinct from pinned old-reader
        # capture mismatch. Coherent adverse fixture bytes; no parser/cache patch.
        negative=copy.deepcopy(request);negative.update(operation_key='c5-unknown-version',instance_id='c5-unknown-version')
        corrupt=json.loads(r.snapshot_bytes);corrupt['schema_version']=999
        corrupt_raw=json.dumps(corrupt,sort_keys=True,separators=(',',':')).encode();corrupt_sha=hashlib.sha256(corrupt_raw).hexdigest()
        pointer=org.db.execute('SELECT journal_id FROM workflow_authority_pointers WHERE namespace=?',(r.namespace,)).fetchone()[0]
        org.db.execute('UPDATE workflow_publication_journals SET snapshot_bytes=?,snapshot_digest=? WHERE id=?',(corrupt_raw,corrupt_sha,pointer))
        org.db.execute('UPDATE workflow_authority_pointers SET snapshot_digest=? WHERE namespace=?',(corrupt_sha,r.namespace));org.db._conn.commit()
        org.workflow_authority.canonical_path.write_bytes(corrupt_raw)
        assert org.workflow_authority.recover()=='rehydrated_coherent'
        refused_before=rows(org)
        refused=client.post(base+'/workflows/activations',json=negative)
        assert refused.status_code==409 and refused.json()['detail']['code']=='authority_snapshot_version_unsupported',refused.text
        assert rows(org)==refused_before
        org.db.execute('UPDATE workflow_publication_journals SET snapshot_bytes=?,snapshot_digest=? WHERE id=?',(r.snapshot_bytes,r.snapshot_digest,pointer))
        org.db.execute('UPDATE workflow_authority_pointers SET snapshot_digest=? WHERE namespace=?',(r.snapshot_digest,r.namespace));org.db._conn.commit()
        org.workflow_authority.canonical_path.write_bytes(r.snapshot_bytes)
        assert org.workflow_authority.recover()=='rehydrated_coherent'
        before=rows(org)
        assert asyncio.run(org.workflow_drafts.claim(f['receipt']['root_task_id'])) is None
        # The completed interpretation is fixture history, never fresh callback
        # evidence. Actual current queued work revalidates after the next writer.
        checked('post',base+'/agents',dict(name='c5_unrelated_worker',role='worker',team='engineering',executor='claude',description='C5 unrelated writer',system_prompt='worker'))
        before=rows(org)
        try: asyncio.run(org.workflow_drafts.claim(current['root_task_id']))
        except DraftOwnershipError as exc: assert str(exc)=='workflow_activation_authority_stale'
        else: raise AssertionError('stale schema2 queued draft was claimed')
        try: asyncio.run(org.workflow_drafts.claim(sibling_receipt['root_task_id']))
        except DraftOwnershipError as exc: assert str(exc)=='workflow_activation_authority_stale'
        else: raise AssertionError('stale unrelated-team draft was claimed')
        assert tuple(org.db.execute('SELECT context_bytes,context_digest FROM workflow_contexts WHERE id=(SELECT context_id FROM workflow_draft_dispatch_intents WHERE id=?)',(sibling_receipt['intent_id'],)).fetchone())==sibling_context
        assert rows(org)==before
        assert history(org)==preserved
        for task_id in (current['root_task_id'],sibling_receipt['root_task_id']):
            checked('post',base+'/tasks/'+task_id+'/cancel',{'rationale':'close the disposable C5 fixture','cascade':False})
        # Native cancellations precede the closed-copy baseline. Drain only stale
        # local queue notifications after both tasks are terminal; no launch.
        while not state.queue._queue.empty(): state.queue._queue.get_nowait()
        before=rows(org)
        preflight=checked('get',base+'/portability-preflight')
        assert preflight['classification']['rejections']==[] and preflight['eligible'],preflight
        assert rows(org)==before
        retained=history(org);coherent=ready(org);retained_profiles=profile_closure()
        client.close()
        asyncio.run(state.close_all())
        state.metrics_store.close()
        state.metrics_store=None;state.direct_connect_authority_store=None
        # Each owner is closed/checkpointed before file hashing or a copy.
        assert not list(container.root.rglob('*-wal'))
        if restore:
            backup=venue/'closed-backup'
            copied=venue/'restored-runtime'
            shutil.copytree(container.root,backup,symlinks=True,copy_function=shutil.copy2)
            assert _closed_files(backup)==_closed_files(container.root)
            shutil.copytree(backup,copied,symlinks=True,copy_function=shutil.copy2)
            assert _closed_files(copied)==_closed_files(backup)
            target=RuntimeDir.load(copied)
            # Existing official registry/path owner, no export/import API.
            runtimes.register(target.root)
            registration=runtimes.load()
            assert registration.active==copied.resolve() and copied.resolve() in registration.registered
        else:
            target=container
        # Reconstruct the actual containing daemon/registration owner and its
        # machine profile coordinator, including every shared-store owner.
        state=DaemonState.from_runtime(target,settings)
        assert state.runtime.root.resolve()==target.root.resolve()
        assert state.broken_orgs=={} and set(state.orgs)=={'alpha'}
        reopened=state.orgs['alpha']
        assert reopened.root.resolve()==(target.root/'orgs/alpha').resolve()
        owned.append(reopened)
        assert history(reopened)==retained
        assert ready(reopened)==coherent
        # Shared profile requirements remain at owned, unchanged registered
        # paths. This checks same-machine restore, not an invented path rewrite.
        assert profile_closure()==retained_profiles
        assert state.profile_coordinator._closure_coherent(reopened)
        client=TestClient(create_app(state));client.headers.update({'Authorization':'Bearer '+paths.ensure_token()})
        frozen=rows(reopened)
        for old_request,receipt in ((f['request'],f['receipt']),(request,current),(sibling,sibling_receipt)):
            replay=checked('post',base+'/workflows/activations',old_request)
            assert replay['replayed']
            for key in ('activation_id','root_task_id','intent_id','context_digest','template','original_request_digest'):
                assert replay[key]==receipt[key]
            fetched=checked('get',base+'/workflows/activations/'+receipt['activation_id'])
            assert fetched['context_digest']==receipt['context_digest']
        assert rows(reopened)==frozen
        with sqlite3.connect(reopened.db.path.resolve().as_uri()+'?mode=ro',uri=True) as reader:
            assert reader.execute('SELECT COUNT(*) FROM task_results WHERE task_id IN (?,?)',(current['root_task_id'],sibling_receipt['root_task_id'])).fetchone()[0]==0
        preflight=checked('get',base+'/portability-preflight')
        assert preflight['eligible'] and preflight['classification']['rejections']==[],preflight
        assert rows(reopened)==frozen
        print(json.dumps(dict(source_sha=revision,python=sys.executable,version=sys.version,action='compatible-closed-restore' if restore else 'current-graph-reopen',profile='explicit' if explicit else 'none',schema1=retained,schema2_digest=coherent.snapshot_digest,registered_root=str(runtimes.load().active),graph=current['activation_id'],actual_result_rows=0,execution='schema1 completed fixture data only; no current provider/callback or migration utility execution',physical_no_write='not proved by row/file readback'),sort_keys=True))
finally:
    client.close()
    asyncio.run(state.close_all())
    if state.metrics_store is not None: state.metrics_store.close()
    for org in owned:
        try: org.close()
        except sqlite3.ProgrammingError: pass
'''
    if restore:
        # Location only: this environment value grants no capability or approval.
        # A separately accepted M task/receipt must provision the owned root.
        supplied = os.environ.get('HAPPYRANCH_TEST_ROSTER_M_VENUE')
        assert supplied, 'HELD: separately provisioned and authorized M venue required'
        parent = Path(supplied)
        assert parent.is_absolute() and not parent.is_symlink() and parent.is_dir()
        assert parent.stat().st_uid == os.getuid()
        venue = parent / ('C5-' + tmp_path.name)
    else:
        venue = tmp_path / 'C5-graph-venue'
    assert not venue.exists()
    venue.mkdir(mode=0o700)
    actual = subprocess.run([sys.executable,'-I','-c',script,binding['source'],binding['revision'],str(venue),
                             str(restore),str(explicit_profile)],capture_output=True,text=True,timeout=120)
    assert actual.returncode == 0, (actual.stdout,actual.stderr)
    receipt = json.loads(actual.stdout.splitlines()[-1])
    assert receipt['source_sha'] == binding['revision']
    assert receipt['action'] == ('compatible-closed-restore' if restore else 'current-graph-reopen')
    assert receipt['profile'] == ('explicit' if explicit_profile else 'none')
    assert receipt['actual_result_rows'] == 0
    (tmp_path / 'C5-graph-process-receipt.json').write_text(json.dumps({'receipt':receipt,'exit':actual.returncode},sort_keys=True))

@pytest.mark.parametrize('roster_kind,reader_kind', [
    ('human', 'current'), ('legacy-agent-control', 'current'),
    ('human','current-graph'), ('human','current-graph-explicit-profile'),
    ('human','compatible-restore'), ('human','compatible-restore-explicit-profile'),
    ('legacy-agent-control', 'pinned-b317-schema2-refusal'),
    ('legacy-agent-control', 'pinned-b317-schema1-control'),
], ids=['human-current', 'legacy-agent-current', 'current-graph', 'current-graph-explicit-profile',
        'compatible-restore', 'compatible-restore-explicit-profile', 'b317-schema2-refusal', 'b317-schema1-control'])
def test_c5_schema_history_publication_and_portability(runtime: Path, tmp_path: Path,
                                                       roster_kind: str, reader_kind: str) -> None:
    """Real current publication/cold reader plus a pinned admission input.

    This L subcase owns current JSON/cold-process admission. Historical
    activation/receipt fixtures, compatible M restore and old-source execution
    are separately required; this case does not substitute for those proofs.
    """
    if reader_kind.startswith(('current-graph','compatible-restore')):
        _c5_shipping_graph_process(tmp_path,restore=reader_kind.startswith('compatible-restore'),
                                   explicit_profile=reader_kind.endswith('explicit-profile'))
        return
    from tests.helpers.integration_stub_guard.guard import manifest
    binding = manifest()
    old_sha = 'b3179b123fddbb0f0f604ed9e0d148f1b23455f3'
    reader_source = Path(binding['source'])
    reader_sha = binding['revision']
    old_control = reader_kind == 'pinned-b317-schema1-control'
    if reader_kind != 'current':
        supplied = os.environ.get('HAPPYRANCH_TEST_ROSTER_OLD_READER_SOURCE')
        assert supplied, 'HELD: manager must authorize and provide independently owned exact b317 reader source'
        reader_source, reader_sha = Path(supplied), old_sha
        assert reader_source.is_absolute() and not reader_source.is_symlink()
        assert reader_source.resolve(strict=True) != Path(binding['source']).resolve(strict=True)
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
    attached = _attach_process(runtime, reader_source=reader_source if old_control else None,
                               reader_sha=reader_sha if old_control else None)
    assert attached.returncode == 0, attached.stderr
    authority = runtime / 'org/.workflow-authority.json'
    raw = authority.read_bytes()
    snapshot = json.loads(raw)
    assert snapshot['schema_version'] == (1 if old_control else 2)
    if not old_control:
        assert snapshot['task_default_team'] == 'engineering'
        assert all(set(row['manager']) == {'kind', 'principal'} for row in snapshot['teams'])
    else:
        assert all(isinstance(row['manager'], str) for row in snapshot['teams'])
    if roster_kind == 'human':
        assert snapshot['default_team'] == 'default'
        assert next(row for row in snapshot['teams'] if row['name'] == 'default') == {
            'name': 'default', 'manager': {'kind': 'human', 'principal': 'founder'},
            'workers': ['consultant_codex', 'consultant_head']}
        assert 'founder' not in [row['name'] for row in snapshot['agents']]
    elif not old_control:
        assert snapshot['default_team'] == 'engineering'
        assert all(row['manager']['kind'] == 'agent' for row in snapshot['teams'])
    with sqlite3.connect(runtime / 'happyranch.db') as conn:
        before = conn.execute('SELECT * FROM workflow_publication_journals ORDER BY rowid').fetchall()
        pointer = conn.execute('SELECT current_generation,snapshot_digest,state FROM workflow_authority_pointers WHERE namespace=?', ('org/test',)).fetchone()
        assert pointer == (1, hashlib.sha256(raw).hexdigest(), 'ready')
        assert conn.execute('SELECT COUNT(*) FROM workflow_publication_leases').fetchone()[0] == 0
    reopened = _attach_process(runtime, reader_source=reader_source if old_control else None,
                              reader_sha=reader_sha if old_control else None)
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
    expected = 'workflow_activation_authority_stale' if reader_kind == 'pinned-b317-schema2-refusal' else 'admitted'
    command = [sys.executable, '-I', str(probe), '--source', str(reader_source),
               '--source-sha', reader_sha, '--root', str(runtime), '--org', 'test',
               '--operation', 'capture-admission', '--expect', expected,
               '--snapshot-digest', hashlib.sha256(raw).hexdigest()]
    if expected == 'admitted': command.append('--positive-graph')
    actual = subprocess.run(command, env=reader_environment, capture_output=True, text=True, timeout=90)
    assert actual.returncode == 0, (actual.stdout, actual.stderr)
    receipt = json.loads(actual.stdout.splitlines()[-1])
    assert receipt['actual'] == expected and receipt['persisted_readback_unchanged']
    if expected == 'admitted':
        assert receipt['positive_graph_receipt']['root_task_id']
        assert receipt['positive_graph_receipt']['execution_started'] is False
    else:
        assert receipt['positive_graph_receipt'] is None
    assert receipt['reader_source_sha'] == reader_sha
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


@pytest.mark.parametrize('refusal', ['missing-direction', 'missing-operation', 'wrong-digest',
                                   'design-plan-not-manifest'])
def test_c9_crash_recovery_and_replay(runtime: Path, tmp_path: Path, refusal: str) -> None:
    """L-only recovery command refusal and repeated input preservation.

    These are process/argument subcases, not a successful maintenance/crash
    replay. M cuts, actual checked-manifest recovery, reboot and transient
    zero-write proof still require the independently authorized M capability.
    The distinct C8 risk is apply/check admission; C9 must reject recovery's
    absent direction/operation without treating a prior plan as owned state.
    """
    from tests.helpers.human_team_incompatible_reader_probe import _closed_files
    from tests.helpers.integration_stub_guard.guard import manifest
    binding = manifest()
    actual_attach = _attach_process(runtime)
    assert actual_attach.returncode == 0, actual_attach.stderr
    proposed = tmp_path / 'unexecuted-recovery-plan.json'
    raw = json.dumps({'kind': 'THR296-design-plan-v1', 'source_sha': binding['revision'],
                      'operation_id': 'unexecuted-proposal', 'containment': {}}).encode()
    proposed.write_bytes(raw)
    arguments = ['--recover', '--runtime-root', str(runtime.parent.parent), '--org', 'test',
                 '--manifest', str(proposed), '--expected-digest',
                 '0' * 64 if refusal == 'wrong-digest' else hashlib.sha256(raw).hexdigest()]
    if refusal != 'missing-direction':
        arguments.extend(['--direction', 'complete'])
    if refusal != 'missing-operation':
        arguments.extend(['--operation-id', 'unexecuted-proposal'])
    expected = ('manifest_digest_mismatch' if refusal == 'wrong-digest' else
                'real_checked_manifest_and_exact_candidate_required' if refusal == 'design-plan-not-manifest'
                else 'explicit_recovery_owner_and_direction_required')
    launcher = """
import runpy,sys
from pathlib import Path
source=Path(sys.argv[1]);sys.dont_write_bytecode=True;sys.path.insert(0,str(source))
script=source/'scripts/migrate_human_team_roster.py'
sys.argv=[str(script),*sys.argv[2:]]
runpy.run_path(str(script),run_name='__main__')
"""
    command = [sys.executable, '-I', '-c', launcher, binding['source'], *arguments]
    before = _closed_files(runtime.parent.parent)
    receipts = []
    for invocation in range(2):
        actual = subprocess.run(command, capture_output=True, text=True, timeout=30)
        assert actual.returncode == 1, (actual.stdout, actual.stderr)
        assert expected in actual.stderr, actual.stderr
        assert proposed.read_bytes() == raw
        assert _closed_files(runtime.parent.parent) == before
        receipts.append({'invocation': invocation, 'exit': actual.returncode, 'refusal': expected})
    (tmp_path / 'C9-L-refusal-receipt.json').write_text(json.dumps(
        {'source_sha': binding['revision'], 'command': command, 'invocations': receipts,
         'closed_file_readback_unchanged': True, 'transient_write_proof': 'UNAVAILABLE without observer',
         'M_crash_recovery_and_reboot': 'NOT EXECUTED'}, sort_keys=True))


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


def _c4_live_continuation_oracle(request: pytest.FixtureRequest, port: int, root: Path,
                                parent_id: str, plan: Path, cut: str, verdict: str | None):
    """Closed L4 native writer/drain observations and genuine final callbacks.

    No unit admission, task/result/final-transition INSERT, wrapped SQL writer
    or parent-only startup sweep can satisfy this shipping progress oracle.
    """
    owned = request.node._roster_fault_daemon
    witness = owned['witness']
    original_review = None
    def record(event: str) -> dict:
        path = witness.with_name(witness.name + '.' + event)
        deadline = time.monotonic() + 150
        while not path.exists() and time.monotonic() < deadline:
            assert owned['process'].poll() is None, 'actual C4 daemon exited before native observation'
            time.sleep(0.02)
        assert path.exists(), f'missing actual native C4 event {event}'
        return json.loads(path.read_text())
    if 'no_job_reentry' in cut and cut != 'loss-no_job_reentry-cancel':
        assert owned['process'].wait(timeout=150) == 86
        marker = json.loads(witness.read_text())
        assert marker['effect'] == 'marker' and type(marker['result']) is int and marker['result'] > 0
        with sqlite3.connect(root / 'happyranch.db') as observer:
            selected_before = observer.execute('SELECT * FROM task_results WHERE id=?', (marker['result'],)).fetchone()
            reviews = observer.execute("SELECT id,task_id,agent,action,payload,timestamp FROM audit_log WHERE task_id=? AND action='review_verdict'", (marker['task'],)).fetchall()
            assert len(reviews) == 1
            original_review = reviews[0]
            assert observer.execute('SELECT state,accepted_result_id FROM task_completion_recoveries WHERE task_id=?', (marker['task'],)).fetchone() == ('callback_consumed', marker['result'])
            assert observer.execute('SELECT parent_task_id,status,note FROM tasks WHERE id=?', (marker['task'],)).fetchone() == (parent_id, 'failed', 'self-blocked: child outcome')
            assert observer.execute('SELECT COUNT(*) FROM task_results WHERE task_id=?', (parent_id,)).fetchone()[0] == 1
        port = owned['start'](cut)
    elif cut == 'late_inline_unbound':
        observed = record('consumer-finished')
        assert observed['disposition'] == 'done'
        with sqlite3.connect(root / 'happyranch.db') as observer:
            selected_before = observer.execute('SELECT * FROM task_results WHERE id=?', (observed['result'],)).fetchone()
            original_review = observer.execute("SELECT id,task_id,agent,action,payload,timestamp FROM audit_log WHERE task_id=? AND action='review_verdict'", (observed['task'],)).fetchone()
            assert original_review is not None
            payloads = [json.loads(row[0]) for row in observer.execute("SELECT payload FROM audit_log WHERE task_id=? AND action='completion_report'", (observed['task'],))]
            assert len(payloads) == 1 and payloads[0]['_result_row_id'] == observed['result']
            assert observer.execute('SELECT state FROM task_completion_recoveries WHERE task_id=?', (observed['task'],)).fetchone() == ('callback_consumed',)
        Path(str(plan) + '.calls.jsonl.allow-child-exit').write_text('release genuine late inline executor return\n')
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            with sqlite3.connect(root / 'happyranch.db') as observer:
                rows = observer.execute("SELECT id,payload FROM audit_log WHERE task_id=? AND action='completion_report' ORDER BY id", (observed['task'],)).fetchall()
            if len(rows) == 2: break
            time.sleep(0.02)
        assert len(rows) == 2
        assert '_result_row_id' in json.loads(rows[0][1]) and '_result_row_id' not in json.loads(rows[1][1])
        assert rows[1][0] > original_review[0]
        owned['process'].terminate()
        assert owned['process'].wait(timeout=20) == 0
        port = owned['start']('none')
        return port, selected_before, original_review, False
    observed = record('held-0')
    deferred = record('consumer-deferred')
    assert observed['task'] == deferred['task'] and observed['origin'] != observed['session']
    assert type(observed['result']) is int and observed['result'] > 0
    assert deferred['phase'] == ('parent_handoff' if 'after_job_drain' in cut else 'evidence')
    with sqlite3.connect(root / 'happyranch.db') as observer:
        selected_before = observer.execute('SELECT * FROM task_results WHERE id=?', (observed['result'],)).fetchone()
        assert selected_before is not None
        task_before = observer.execute('SELECT status,cancelled_at,note,current_session_id,parent_task_id,completed_at FROM tasks WHERE id=?', (observed['task'],)).fetchone()
        episode_before = observer.execute('SELECT origin_session_id,recovery_session_id,accepted_result_id,state FROM task_completion_recoveries WHERE task_id=?', (observed['task'],)).fetchone()
        reviews_before = observer.execute("SELECT id,task_id,agent,action,payload,timestamp FROM audit_log WHERE task_id=? AND action='review_verdict' ORDER BY id", (observed['task'],)).fetchall()
        prior_jobs = observer.execute("SELECT * FROM jobs WHERE task_id=? AND title='C4-prior'", (observed['task'],)).fetchall()
        current_jobs = observer.execute("SELECT id,status,reason,stdout_path,stderr_path FROM jobs WHERE task_id=? AND title='C4-running'", (observed['task'],)).fetchall()
        parent_before = observer.execute('SELECT status,block_kind FROM tasks WHERE id=?', (parent_id,)).fetchone()
        assert parent_before == ('in_progress', 'delegated')
        assert observer.execute('SELECT COUNT(*) FROM task_results WHERE task_id=?', (parent_id,)).fetchone()[0] == 1
        assert episode_before[:3] == (observed['origin'], observed['session'], observed['result'])
        is_consumed = 'after_job_drain' in cut or 'no_job_reentry' in cut
        assert episode_before[3] == ('callback_consumed' if is_consumed else 'callback_accepted')
        assert task_before[0] == ('failed' if is_consumed else 'in_progress')
        assert len(reviews_before) == int(is_consumed)
        if is_consumed:
            assert reviews_before[0][2] == 'consultant_codex'
            assert json.loads(reviews_before[0][4]) == {'verdict': verdict if verdict is not None else 'rejected',
                'feedback': 'self-blocked: child outcome', 'reviewed_agent': 'consultant_codex'}
            original_review = reviews_before[0]
        if 'after_job_drain' in cut:
            assert len(prior_jobs) == 1 and len(current_jobs) == 1
            assert current_jobs[0][1:3] == ('failed', 'task_ended')
            assert current_jobs[0][3] and Path(current_jobs[0][3]).exists()
    # Check again while the *actual* supported writer remains entered. Only
    # diagnostic heartbeats may change; K/result/review/marker/parent effects do not.
    time.sleep(0.12)
    with sqlite3.connect(root / 'happyranch.db') as observer:
        assert observer.execute('SELECT * FROM task_results WHERE id=?', (observed['result'],)).fetchone() == selected_before
        assert observer.execute('SELECT status,cancelled_at,note,current_session_id,parent_task_id,completed_at FROM tasks WHERE id=?', (observed['task'],)).fetchone() == task_before
        assert observer.execute("SELECT id,task_id,agent,action,payload,timestamp FROM audit_log WHERE task_id=? AND action='review_verdict' ORDER BY id", (observed['task'],)).fetchall() == reviews_before
        assert observer.execute('SELECT COUNT(*) FROM task_results WHERE task_id=?', (parent_id,)).fetchone()[0] == 1
    loss = cut.rsplit('-', 1)[-1] if cut.startswith('loss-') else None
    if loss == 'cancel':
        if 'after_job_drain' in cut or cut == 'loss-no_job_reentry-cancel':
            traversed = record('cancel-traversed')
            assert traversed['to_cancel'] == [observed['task']]
            witness.with_name(witness.name + '.commit-late-cancel').write_text('release native staged cancellation request\n')
            cancelled = record('late-cancel-response')
            assert cancelled['status'] == 200 and observed['task'] in cancelled['body']['cancelled']
        else:
            cancelled = httpx.post(_base(port) + f'/tasks/{observed["task"]}/cancel', headers=_auth_headers(),
                json={'rationale': 'actual C4 cancellation winner', 'cascade': False}, timeout=10)
            assert cancelled.status_code == 200, cancelled.text
            assert observed['task'] in cancelled.json()['cancelled']
    elif cut == 'no_job_reentry_terminal_cancel_refusal':
        # A new request after the actual FAILED marker is independently
        # terminal-refused. It cannot manufacture the accepted late-cancel
        # winner; that earlier-traversal loss is localized separately in U9.
        cancelled = httpx.post(_base(port) + f'/tasks/{observed["task"]}/cancel', headers=_auth_headers(),
            json={'rationale': 'terminal request control', 'cascade': False}, timeout=10)
        assert cancelled.status_code == 409 and cancelled.json()['detail']['code'] == 'task_already_terminal'
    elif loss == 'binding_replacement':
        witness.with_name(witness.name + '.binding-replacement').write_text('publish native fixture replacement binding\n')
        replaced = record('binding-replaced')
        assert replaced['replacement'] != observed['session']
    elif cut == 'shutdown_while_deferred':
        witness.with_name(witness.name + '.shutdown').write_text('native queue shutdown\n')
        record('shutdown-observed')
    with sqlite3.connect(root / 'happyranch.db') as observer:
        winner = observer.execute('SELECT status,cancelled_at,note,current_session_id,parent_task_id,completed_at FROM tasks WHERE id=?', (observed['task'],)).fetchone()
        winner_reviews = observer.execute("SELECT id,task_id,agent,action,payload,timestamp FROM audit_log WHERE task_id=? AND action='review_verdict' ORDER BY id", (observed['task'],)).fetchall()
        winner_marker = observer.execute('SELECT origin_session_id,recovery_session_id,accepted_result_id,state FROM task_completion_recoveries WHERE task_id=?', (observed['task'],)).fetchone()
    witness.with_name(witness.name + '.release-0').write_text('release actual native writer interval\n')
    record('writer-complete')
    finished = record('consumer-finished')
    if loss or cut == 'shutdown_while_deferred':
        assert finished['disposition'] == ('recovery_required' if cut == 'shutdown_while_deferred' else 'lost_owner')
        for _ in range(2):
            time.sleep(0.12)
            with sqlite3.connect(root / 'happyranch.db') as observer:
                assert observer.execute('SELECT * FROM task_results WHERE id=?', (observed['result'],)).fetchone() == selected_before
                assert observer.execute('SELECT status,cancelled_at,note,current_session_id,parent_task_id,completed_at FROM tasks WHERE id=?', (observed['task'],)).fetchone() == winner
                assert observer.execute("SELECT id,task_id,agent,action,payload,timestamp FROM audit_log WHERE task_id=? AND action='review_verdict' ORDER BY id", (observed['task'],)).fetchall() == winner_reviews
                assert observer.execute('SELECT origin_session_id,recovery_session_id,accepted_result_id,state FROM task_completion_recoveries WHERE task_id=?', (observed['task'],)).fetchone() == winner_marker
                if loss != 'cancel':
                    assert observer.execute('SELECT COUNT(*) FROM task_results WHERE task_id=?', (parent_id,)).fetchone()[0] == 1
        return port, selected_before, original_review, True
    assert finished['disposition'] == 'done'
    assert finished['key'] == [observed[key] for key in ('task', 'agent', 'origin', 'session', 'result')]
    Path(str(plan) + '.calls.jsonl.allow-child-exit').write_text('release genuine executor return after selected shared consumer\n')
    if cut.endswith('portability_loop'):
        reply = record('portability-response')
        assert reply['status'] == 200 and reply['body']['disposition'] == 'consume_result'
    final = _wait_for_terminal(_base(port), parent_id)
    assert final['task']['status'] == 'completed'
    with sqlite3.connect(root / 'happyranch.db') as observer:
        parent_results = observer.execute('SELECT id,session_id,agent FROM task_results WHERE task_id=? ORDER BY id', (parent_id,)).fetchall()
        assert len(parent_results) == 2 and all(type(row[0]) is int and row[0] > 0 and row[1] for row in parent_results)
        assert parent_results[0][1] != parent_results[1][1]
        assert all(row[2] == 'consultant_codex' for row in parent_results)
        assert observer.execute('SELECT * FROM task_results WHERE id=?', (observed['result'],)).fetchone() == selected_before
        assert observer.execute("SELECT * FROM jobs WHERE task_id=? AND title='C4-prior'", (observed['task'],)).fetchall() == prior_jobs
        if original_review is not None:
            assert observer.execute("SELECT id,task_id,agent,action,payload,timestamp FROM audit_log WHERE task_id=? AND action='review_verdict'", (observed['task'],)).fetchall() == [original_review]
    return port, selected_before, original_review, False


@pytest.mark.parametrize('team', ['default', 'engineering'], ids=['human', 'legacy'])
def test_c1_settings_save_and_rollback(human_daemon: tuple[int, Path], team: str) -> None:
    """Finite current settings producer; deliberately staged canonical inputs."""
    from runtime.orchestrator.agent_def import AgentDef, render_agent_text
    port, root = human_daemon
    path = root / 'org/teams.yaml'
    before = yaml.safe_load(path.read_text())
    before_agents = {str(p.relative_to(root)): p.read_bytes() for p in (root / 'org/agents').glob('*.md')}
    worker = 'settings_fixture_worker'
    definition = root / 'org/agents' / (worker + '.md')
    definition.write_text(render_agent_text(AgentDef(name=worker, team=team, role='worker', executor='codex',
        allow_rules=(), repos={}, enrolled_by=None, enrolled_at_task=None, enrolled_at=None,
        system_prompt='Synthetic settings worker.')))
    # A new active definition is the supported settings add prerequisite. The
    # request publishes the matching roster; no executor task/session is made.
    reply = httpx.put(_base(port) + '/settings/teams', headers=_auth_headers(),
                     json={'team': team, 'add_workers': [worker]})
    assert reply.status_code == 200, reply.text
    added = {**before, 'teams': {**before['teams'], team: {**before['teams'][team],
             'workers': [*before['teams'][team]['workers'], worker]}}}
    assert yaml.safe_load(path.read_text()) == added
    roster = path.read_bytes()
    # Removing an active file's membership is a real post-save drift, not an
    # invalid name/auth failure; original typed manager and pointers survive.
    refused = httpx.put(_base(port) + '/settings/teams', headers=_auth_headers(),
                       json={'team': team, 'remove_workers': [worker]})
    assert refused.status_code == 409 and refused.json()['detail']['code'] == 'teams_consistency_drift', refused.text
    assert yaml.safe_load(path.read_text()) == added
    assert path.read_bytes() == roster
    assert {str(p.relative_to(root)): p.read_bytes() for p in (root / 'org/agents').glob('*.md') if p != definition} == before_agents
    assert definition.read_bytes() == render_agent_text(AgentDef(name=worker, team=team, role='worker', executor='codex',
        allow_rules=(), repos={}, enrolled_by=None, enrolled_at_task=None, enrolled_at=None,
        system_prompt='Synthetic settings worker.')).encode()
    with sqlite3.connect(root / 'happyranch.db') as observer:
        assert observer.execute('SELECT COUNT(*) FROM tasks').fetchone()[0] == 0
        assert observer.execute('SELECT COUNT(*) FROM task_results').fetchone()[0] == 0
    assert not (root / 'workspaces/founder').exists()
