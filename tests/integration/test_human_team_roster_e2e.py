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
from typing import Callable

import httpx
import pytest
import yaml

from tests.integration.conftest import seed_workspace
from tests.integration.test_subtask_self_decompose_e2e import _auth_headers, _wait_for_terminal

pytestmark = pytest.mark.integration
VERDICTS = [(None, 'none'), ('', 'blank'), ('CUSTOM_REVIEW_OUTCOME', 'custom'),
            ('APPROVE', 'approve'), ('PASS', 'pass'), ('REQUEST_CHANGES', 'request_changes'),
            ('REVISE', 'revise'), ('BLOCK', 'block')]
C4_DRAIN_CUTS = ('native_drain_exception_same_result', 'native_drain_cancel_same_result')


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
import ast,asyncio,hashlib,json,marshal,os,runpy,sys,threading,types
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
drain_cuts={'native_drain_exception_same_result','native_drain_cancel_same_result'}
writer_cuts={'writer_busy_before_consumption-inline_worker','writer_reacquired_before_retry',
             'writer_cancelled_before_retry',
    'writer_busy_before_consumption-shared_loop', 'writer_busy_before_consumption-startup_loop',
    'writer_busy_before_consumption-zombie_loop', 'writer_busy_before_consumption-portability_loop',
    'writer_busy_after_job_drain', 'writer_busy_no_job_reentry',
    'loss-before_consumption-cancel', 'loss-before_consumption-binding_replacement',
    'loss-after_job_drain-cancel', 'loss-after_job_drain-binding_replacement',
    'no_job_reentry_terminal_cancel_refusal', 'loss-no_job_reentry-cancel', 'loss-no_job_reentry-binding_replacement',
    'shutdown_while_deferred', 'late_inline_unbound'} | drain_cuts
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
jobs_path=source/'runtime/daemon/jobs_runner.py'
# A finite test-side cut at the real SIGKILL statement; never replace the
# runner, callback, SQL or control map. Cancellation uses the actual drain Task.
jobs_tree=ast.parse(jobs_path.read_bytes())
native_drains=[node for node in jobs_tree.body if isinstance(node,ast.AsyncFunctionDef) and node.name=='terminate_jobs_for_task']
assert len(native_drains)==1
kill_lines=[node.lineno for node in ast.walk(native_drains[0])
    if isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute)
    and isinstance(node.func.value,ast.Name) and node.func.value.id=='os'
    and node.func.attr=='killpg' and len(node.args)==2
    and isinstance(node.args[1],ast.Attribute) and node.args[1].attr=='SIGKILL']
assert len(kill_lines)==1
grace_lines=[node.lineno for node in ast.walk(native_drains[0]) if isinstance(node,ast.Await)
    and isinstance(node.value,ast.Call) and isinstance(node.value.func,ast.Attribute)
    and isinstance(node.value.func.value,ast.Name) and node.value.func.value.id=='asyncio'
    and node.value.func.attr=='sleep']
assert len(grace_lines)==1
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
def native_controls(targets):
    from runtime.daemon.jobs_runner import _INFLIGHT
    return [{'job':job,'pid':proc.pid,'returncode':proc.returncode,
             'same_control':_INFLIGHT.get(job) is proc} for job,proc in targets]
async def drain_pending_join(drain_task,targets):
    from datetime import datetime,timezone
    from runtime.daemon.zombie_reaper import _consume_zombie_fingerprint
    orch=writer_state['orch'];identity=writer_state['identity']
    # Valid fixture marker through the native setter, then the actual reaper
    # consumer owns its native success-only clear/audit closure.
    flag=datetime.now(timezone.utc).isoformat()
    orch._db.update_task(identity['task'],zombie_flagged_at=flag)
    task=orch._db.get_task(identity['task'])
    row=orch._db.get_latest_task_result(task.id,identity['agent'],identity['session'])
    assert row['id']==identity['result']
    joined=_consume_zombie_fingerprint(orch._db,task.id,row,task,orch)
    writer_state['pending_join']=joined
    record_writer('drain-pending-join',pending=not joined.completion.done(),zombie_flag=flag,
        controls=native_controls(targets),**identity)
    if cut=='native_drain_cancel_same_result':
        record_writer('native-drain-fault',fault='cancel',controls=native_controls(targets),**identity)
        assert drain_task.cancel()  # real cancellation of native grace await
async def failed_drain_reentry():
    from runtime.daemon.zombie_reaper import _consume_zombie_fingerprint
    orch=writer_state['orch'];identity=writer_state['identity']
    pending=writer_state['pending_join']
    assert await asyncio.wrap_future(pending.completion)=='recovery_required'
    record_writer('drain-joined-finished',disposition=pending.disposition,**identity)
    release=receipt.with_name(receipt.name+'.same-result-reentry')
    while not release.exists():await asyncio.sleep(0.01)
    task=orch._db.get_task(identity['task'])
    row=orch._db.get_latest_task_result(task.id,identity['agent'],identity['session'])
    assert row['id']==identity['result']
    joined=_consume_zombie_fingerprint(orch._db,task.id,row,task,orch)
    disposition=await asyncio.wrap_future(joined.completion)
    retained=orch._human_failed_recovery_operations[(task.id,identity['agent'],identity['session'],identity['result'])]
    from runtime.daemon.jobs_runner import _INFLIGHT
    record_writer('same-result-reentered',disposition=disposition,phase=retained.phase,
        retained=retained is writer_state['operation'],job_ids=list(retained.job_ids),
        key=list(retained.key),controls=native_controls([(job,_INFLIGHT[job]) for job in retained.job_ids if job in _INFLIGHT]),
        **identity)
def observe_drain(frame,event,arg):
    if frame.f_code.co_filename!=str(jobs_path) or frame.f_code.co_qualname!='terminate_jobs_for_task':return
    assert hashlib.sha256(marshal.dumps(frame.f_code)).hexdigest()==extra_codes[(str(jobs_path),'terminate_jobs_for_task')]
    if (event=='line' and frame.f_lineno==kill_lines[0] and cut=='native_drain_exception_same_result'
            and writer_state and frame.f_locals['task_id']==writer_state['identity']['task']
            and 'fault' not in writer_state):
        writer_state['fault']=True
        record_writer('native-drain-fault',fault='exception',controls=native_controls(frame.f_locals['targets']),
            **writer_state['identity'])
        raise PermissionError('test-side native SIGKILL interruption')
    return observe_drain
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
        if 'after_job_drain' not in cut and cut!='loss-no_job_reentry-cancel' and cut not in drain_cuts:
            writer_state['writer']=asyncio.run_coroutine_threadsafe(held_writer(org),orch._main_loop)
            assert writer_state['ready'].wait(10),'native writer did not acquire actual async interval'
    elif frame.f_code.co_qualname=='_drive_human_failed_recovery' and event=='return' and writer_state:
        operation=local['operation']
        if operation.task_id!=writer_state['identity']['task']:return
        if cut in drain_cuts and 'operation' not in writer_state:
            writer_state['operation']=operation
        if operation.disposition=='writer_busy' and 'deferred' not in writer_state:
            assert operation.timer is not None and not operation.completion.done()
            writer_state['deferred']=True
            writer_state['operation']=operation
            record_writer('consumer-deferred',phase=operation.phase,**writer_state['identity'])
    elif frame.f_code.co_qualname=='_HumanFailedRecoveryOperation.finish' and event=='return' and writer_state:
        operation=local['self']
        if cut in drain_cuts and operation is not writer_state.get('operation'):return
        if operation.task_id==writer_state['identity']['task'] and 'finished' not in writer_state:
            writer_state['finished']=True
            record_writer('consumer-finished',disposition=operation.disposition,phase=operation.phase,
                key=list(operation.key) if operation.key is not None else None,**writer_state['identity'])
            if cut in drain_cuts:
                writer_state['reentry']=asyncio.get_running_loop().create_task(failed_drain_reentry())
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
        if (extra[1]=='terminate_jobs_for_task' and cut in drain_cuts and event=='return' and frame.f_lineno==grace_lines[0]
                and writer_state and local.get('task_id')==writer_state['identity']['task']
                and local.get('targets') and 'drain_join' not in writer_state):
            # Actual native coroutine has yielded inside its grace await,
            # after SIGTERM to real opaque controls. The job ignores TERM.
            writer_state['drain_join']=asyncio.get_running_loop().create_task(
                drain_pending_join(asyncio.current_task(),local['targets']))
        elif (extra[1]=='Database.admit_task_completion_callback' and event=='return' and arg
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
if cut=='native_drain_exception_same_result':sys.settrace(observe_drain)
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
    # The legacy C2 positive controls execute the persisted Engineering owner.
    # A definition alone does not meet the native workspace launch precondition.
    seed_workspace(runtime, 'engineering_head')
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
if parent is not None and c4_cut is not None and ('after_job_drain' in c4_cut or c4_cut.startswith('native_drain_')):
    # Genuine task-owned runner jobs, separate from the blocked result's empty
    # wait list. The original failure is retained and one opaque job drains.
    import time
    running_script = ("trap '' TERM; " if c4_cut.startswith('native_drain_') else '') + 'echo owned-running; while true; do sleep 1; done'
    for suffix, script in [('prior', 'echo retained-failure >&2; sleep 30'), ('running', running_script)]:
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
        if suffix == 'running' and c4_cut.startswith('native_drain_'):
            # The real shell installed its TERM behavior before admission to
            # the native drain. Status='running' alone is insufficient.
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                with sqlite3.connect((root / 'happyranch.db').as_uri() + '?mode=ro', uri=True) as observer:
                    log_path = observer.execute('SELECT stdout_path FROM jobs WHERE id=?', (job_ids[0],)).fetchone()[0]
                if log_path and 'owned-running' in pathlib.Path(log_path).read_text(): break
                time.sleep(0.01)
            assert log_path and 'owned-running' in pathlib.Path(log_path).read_text()
command = ['happyranch', 'report-completion', '--org', org, '--from-file', str(file)]
actual = subprocess.run(command, capture_output=True, text=True, timeout=30)
with pathlib.Path(str(witness) + '.callback.calls.jsonl').open('a') as out:
    out.write(json.dumps({'command': command, 'exit': actual.returncode,
        'stdout': actual.stdout, 'stderr': actual.stderr, 'task': T, 'session': S, 'agent': agent}) + '\\n')
print(actual.stdout, end='')
print(actual.stderr, end='', file=sys.stderr)
actual.check_returncode()
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
            assert final['task']['status'] == 'completed', final
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
    'shutdown_while_deferred', 'late_inline_unbound', *C4_DRAIN_CUTS)]


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
    if cut in C4_DRAIN_CUTS:
        _c4_failed_drain_reentry(request, port, root, reply['task_id'], verdict)
        return  # exceptional residue is preserved, never ordinary completion
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
        assert supplied, 'explicit independently selected exact b317 reader source required'
        selected = binding['roster']['old_reader']
        assert selected['source'] == supplied and selected['revision'] == old_sha
        assert binding['roster']['python'] == str(Path(sys.executable).resolve())
        assert binding['roster']['python_sha256'] == hashlib.sha256(Path(sys.executable).read_bytes()).hexdigest()
        for relative, expected_hash in selected['modules'].items():
            assert hashlib.sha256((Path(supplied) / relative).read_bytes()).hexdigest() == expected_hash
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
    assert Path(receipt['effective_python']).resolve() == Path(sys.executable).resolve()
    assert receipt['python_sha256'] == hashlib.sha256(Path(sys.executable).read_bytes()).hexdigest()
    assert receipt['imported_source_files']
    assert all(Path(filename).resolve().is_relative_to(reader_source.resolve())
               for filename in receipt['imported_source_files'].values())
    assert receipt['snapshot_digest'] == hashlib.sha256(raw).hexdigest()
    (tmp_path / 'C5-current-reader-receipt.json').write_text(json.dumps(
        {'command': command, 'daemon_home': str(reader_home), 'exit': actual.returncode,
         'receipt': receipt}, sort_keys=True))



# C6/C8/C9 share this finite owned setup. It provisions fixture data only; the
# externally operated M machine must supply actual persistent launch inhibition.
# No environment variable certifies inhibition, and containment is never patched.
class _MaintenanceCase:
    def __init__(self, directory: Path, declaration: dict, default_shape: str, row_shape: str):
        import copy
        import uuid
        from runtime.config import Settings
        from runtime.daemon import runtimes
        from runtime.daemon.org_state import OrgState
        from runtime.infrastructure.database import Database
        from runtime.models import TaskRecord, TaskStatus, ThreadRecord, ThreadStatus
        from runtime.orchestrator._paths import OrgPaths
        from runtime.orchestrator.agent_def import AgentDef, render_agent_text
        from runtime.orchestrator.context_builder import ContextBuilder
        from runtime.orchestrator.prompt_loader import load_agent
        from runtime.orchestrator.workspace_adapters import materialize_workspace_skills_union, validate_workspace_skills_integrity
        from runtime.runtime import RuntimeDir
        from runtime.skills.canonical_store import _get_canonical_store_root
        from runtime.workflows.profile_coordinator import ProfileCoordinator
        from scripts import migrate_human_team_roster as utility
        from tests.workflows.test_authority_coordinator import _seed_org
        self.source = Path(__file__).resolve().parents[2]
        self.revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=self.source, text=True).strip()
        assert not subprocess.check_output(['git', 'status', '--porcelain'], cwd=self.source).strip()
        assert sys.version_info[:2] == (3, 14)
        assert utility.SOURCE == self.source
        self.directory = directory
        self.runtime = RuntimeDir.init(directory / 'runtime').root
        self.root = self.runtime / 'orgs/alpha'
        self.home = Path(declaration['daemon_home'])
        self.env = {**os.environ, 'HAPPYRANCH_DAEMON_HOME': str(self.home), 'PYTHONDONTWRITEBYTECODE': '1'}
        assert Path(os.environ['HAPPYRANCH_DAEMON_HOME']).resolve() == self.home.resolve()
        _seed_org(self.root)
        # Proven fresh fixture database: native complete initialization precedes attachment.
        from runtime.infrastructure.workflow_schema import initialize_complete_org_schema
        assert not (self.root / 'happyranch.db').exists()
        fresh = Database(self.root / 'happyranch.db')
        try: initialize_complete_org_schema(fresh, expected_org_slug='alpha')
        finally: fresh.close()
        teams_path = self.root / 'org/teams.yaml'
        teams_path.write_text(teams_path.read_text().replace('workers: [dev_agent, code_reviewer]', 'workers: [dev_agent, code_reviewer, qa_engineer]') + '  product:\n    manager: product_lead\n    workers: []\n' + '# unrelated literal engineering metadata remains\n'
            + '  consultant:\n    manager: consultant_head\n    workers: [consultant_codex]\n'
            + ('  default:\n    manager: {kind: human, principal: founder}\n    workers: []\n' if default_shape == 'correct-empty-default' else '')
            + 'default_team: engineering # preserve this pointer comment\n'
            + 'task_default_team: engineering\n')
        self.manager_sentence = 'You lead the Consultant team and manage its workers.'
        for agent in utility.AGENTS:
            definition = AgentDef(name=agent, team='consultant',
                role='manager' if agent == 'consultant_head' else 'worker',
                executor='claude' if agent == 'consultant_head' else 'codex',
                description='Retained consultant', allow_rules=('git',), repos={},
                enrolled_by='founder', enrolled_at_task='TASK-C6-FIXTURE',
                enrolled_at=datetime(2026, 10, 1, tzinfo=timezone.utc),
                system_prompt=(self.manager_sentence if agent == 'consultant_head' else 'Advise the founder.') + '\nRetained advisory context.')
            (self.root / 'org/agents' / (agent + '.md')).write_text(render_agent_text(definition))
            workspace = self.root / 'workspaces' / agent
            (workspace / 'memory').mkdir(parents=True)
            (workspace / 'memory/_index.md').write_text('# Synthetic retained memory index\nMEM-C6\n')
            (workspace / 'memory/MEM-C6.md').write_text('Synthetic retained memory body; hash only in evidence.\n')
            (workspace / '.provider-memory').mkdir()
            (workspace / '.provider-memory/prior-state').write_bytes(b'synthetic-provider-history')
            (workspace / 'task_history.md').write_text('TASK-C6-HISTORY Consultant historical label\n')
            repo = workspace / 'repos/preserved'; repo.mkdir(parents=True)
            subprocess.run(['git', 'init', '-q', str(repo)], check=True)
            (repo / 'retained.txt').write_text('retained repository output\n')
            subprocess.run(['git', '-C', str(repo), 'add', 'retained.txt'], check=True)
            subprocess.run(['git', '-C', str(repo), '-c', 'user.name=C6 fixture', '-c', 'user.email=c6@example.invalid', 'commit', '-qm', 'retained fixture'], check=True)
            worktree = workspace / '.claude/worktrees/C6-preserved'
            worktree.parent.mkdir(parents=True)
            subprocess.run(['git', '-C', str(repo), 'worktree', 'add', '-q', '-b', 'c6-preserved', str(worktree)], check=True)
            (workspace / 'output').mkdir()
            (workspace / 'output/prior.txt').write_text('retained artifact\n')
        for name, team, role in [('qa_engineer', 'engineering', 'worker'), ('product_lead', 'product', 'manager')]:
            definition = AgentDef(name=name, team=team, role=role, executor='claude', description='retained fixture',
                allow_rules=(), repos={}, enrolled_by='founder', enrolled_at_task='TASK-C6-FIXTURE',
                enrolled_at=datetime(2026, 10, 1, tzinfo=timezone.utc), system_prompt='Document only.')
            (self.root / 'org/agents' / (name + '.md')).write_text(render_agent_text(definition))
        org = OrgState.load(slug='alpha', root=self.root, settings=Settings(project_root=self.source))
        profiles = ProfileCoordinator(daemon_home=self.home, orgs={'alpha': org})
        org.workflow_authority._profile_coordinator = profiles
        profiles.reconcile_startup()
        org.workflow_authority.verify_admission_ready()
        now = datetime.now(timezone.utc)
        for index, status in enumerate((ThreadStatus.OPEN, ThreadStatus.ARCHIVED)):
            thread = 'THR-C6-' + str(index)
            org.db.insert_thread(ThreadRecord(id=thread, subject='retained continuity', status=status))
            for agent in (*utility.AGENTS, 'dev_agent'):
                if row_shape == 'zero_rows' and agent in utility.AGENTS:
                    continue
                org.db._conn.execute('INSERT INTO thread_participants VALUES (?,?,?,?,?,?)',
                    (thread, agent, now.isoformat(), 'founder', None if row_shape == 'already_null' and agent in utility.AGENTS else 'prior-' + agent,
                     0 if row_shape == 'already_null' and agent in utility.AGENTS else 7))
                org.db._conn.execute('''INSERT INTO thread_reply_delivery_state
                    (thread_id,agent_name,acknowledged_through_seq,required_through_seq,updated_at)
                    VALUES (?,?,?,?,?)''', (thread, agent, 7, 7, now.isoformat()))
                org.db._conn.execute('''INSERT INTO thread_reply_breaker_episodes
                    (thread_id,agent_name,executor_key,episode_id,state,consecutive_failures,opened_at,cooldown_until,last_failure_category,updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?)''', (thread, agent, 'codex' if agent.endswith('codex') else 'claude',
                        'prior-' + thread + '-' + agent, 'open', 3, now.isoformat(), (now + timedelta(days=1)).isoformat(), 'provider_failure', now.isoformat()))
        # Nonempty immutable conversation/exchange/invocation history makes
        # the retained delivery/frozen oracle meaningful. These are labelled
        # synthetic fixture records, never callback execution evidence.
        for index in range(2):
            thread = 'THR-C6-' + str(index)
            org.db._conn.execute('INSERT INTO thread_messages(thread_id,seq,speaker,kind,body_markdown,created_at) VALUES (?,?,?,?,?,?)',
                (thread, 7, 'consultant_head', 'reply', 'Retained Consultant fixture conversation', now.isoformat()))
            org.db._conn.execute('INSERT INTO thread_invocations(thread_id,agent_name,invocation_token,triggering_seq,purpose,status,enqueued_at,consumed_at,reply_message_seq) VALUES (?,?,?,?,?,?,?,?,?)',
                (thread, 'consultant_head', 'retained-' + thread, 7, 'reply', 'consumed', now.isoformat(), now.isoformat(), 7))
            org.db._conn.execute('INSERT INTO thread_reply_exchange(thread_id,exchange_id,state,open_seq,close_seq,opened_at,last_activity_at,closed_at,close_reason) VALUES (?,?,?,?,?,?,?,?,?)',
                (thread, 1, 'released', 1, 7, now.isoformat(), now.isoformat(), now.isoformat(), 'quiescence'))
            org.db._conn.execute('INSERT INTO thread_exchange_deferrals(thread_id,exchange_id,agent_name,state,created_at,released_at,mint_token_prefix) VALUES (?,?,?,?,?,?,?)',
                (thread, 1, 'consultant_codex', 'released', now.isoformat(), now.isoformat(), 'retained-frozen-' + thread))
        org.db._conn.commit()
        # This is explicitly old fixture DATA, never an observed callback.
        org.db.insert_task(TaskRecord(id='TASK-C6-HISTORY', assigned_agent='consultant_head',
            team='consultant', status=TaskStatus.COMPLETED, brief='Retained Consultant record', completed_at=now,
            current_session_id='retained-fixture-session', note='Retained Consultant historical note'))
        org.db.insert_audit_log(task_id='TASK-C6-HISTORY', agent='consultant_head', action='fixture_history', payload={'team': 'consultant'})
        self._materialize(org)
        org.close()
        from fastapi.testclient import TestClient
        from runtime.daemon import paths
        from runtime.daemon.app import create_app
        from runtime.daemon.state import DaemonState
        from tests.workflows.authority_test_support import C5_SCHEMA1_HISTORY, seed_c5_schema1_history
        containing = DaemonState.from_runtime(RuntimeDir.load(self.runtime), Settings(project_root=self.source))
        client = TestClient(create_app(containing), base_url='http://localhost')
        client.headers['Authorization'] = 'Bearer ' + paths.ensure_token()
        try:
            response = client.post('/api/v1/orgs/alpha/workflows/templates/publish', json=dict(operation_key='c6-template', team_slug='product', template_name='product-design', expected_current_version=0, definition=json.loads(C5_SCHEMA1_HISTORY['context_bytes'])['template']))
            assert response.status_code == 201, response.text
            enabled = client.post('/api/v1/orgs/alpha/workflows/cutover/requests', json={'operation_key': 'c6-enable', 'action': 'enable', 'expected_generation': 1})
            assert enabled.status_code == 200, enabled.text
            old = containing.orgs['alpha']
            historical = seed_c5_schema1_history(old, completed=True)
            old.db._conn.execute("UPDATE workflow_instances SET status='complete' WHERE id=?", (historical['receipt']['instance_id'],))
            old.db._conn.execute("INSERT INTO task_results(task_id,agent,session_id,status,output_summary,created_at) VALUES ('TASK-C6-HISTORY','consultant_head','retained-fixture-session','completed','historical Consultant fixture data','2026-10-01')")
            old.db._conn.commit()
        finally:
            client.close()
            import asyncio
            asyncio.run(containing.close_all())
        runtimes.register(self.runtime)
        state = runtimes.load()
        runtimes._save(runtimes.RegistryState(active=None, registered=state.registered))
        self.store = _get_canonical_store_root(Settings(project_root=self.source)).resolve(strict=True)
        self.original_files = utility.preservation_inventory(self.root)
        self.original_store = utility.preservation_inventory(self.store)
        self.closed = directory / 'closed-org'; self.closed_store = directory / 'closed-store'
        shutil.copytree(self.root, self.closed, symlinks=True)
        shutil.copytree(self.store, self.closed_store, symlinks=True)
        assert utility.preservation_inventory(self.closed) == self.original_files
        assert utility.preservation_inventory(self.closed_store) == self.original_store
        # Actual native preview at the same synthetic absolute paths. Restore
        # the complete closed before-copy before the measured real check. The
        # preview is setup data, never the final roster oracle or a manifest.
        self.canonical_before = {rel: (self.root / rel).read_bytes() for rel in utility.CANONICAL}
        for agent in utility.AGENTS:
            p = self.root / 'org/agents' / (agent + '.md')
            text = p.read_text().replace('team: consultant\n', 'team: default\n', 1)
            if agent == 'consultant_head':
                text = text.replace('role: manager\n', 'role: worker\n', 1).replace(self.manager_sentence, utility.ADVICE, 1)
            p.write_text(text)
        proposed = yaml.safe_load(teams_path.read_bytes())
        del proposed['teams']['consultant']
        proposed['teams']['default'] = {'manager': {'kind': 'human', 'principal': 'founder'}, 'workers': list(utility.AGENTS)}
        proposed.update(default_team='default', task_default_team='engineering')
        teams_path.write_text(yaml.safe_dump(proposed, sort_keys=False))
        preview = OrgState.load(slug='alpha', root=self.root, settings=Settings(project_root=self.source))
        try:
            start = preview.db._conn.execute('SELECT COALESCE(MAX(id),0) FROM skill_validation_events').fetchone()[0]
            self._materialize(preview)
            event_shapes = []
            for row in preview.db._conn.execute('SELECT * FROM skill_validation_events WHERE id>?', (start,)):
                value = dict(row); value.pop('id'); value.pop('created_at')
                if value not in event_shapes: event_shapes.append(value)
        finally:
            preview.close()
        from runtime.daemon.routes.agents import _BOOTSTRAP_OWNED_FILES
        generated = {}
        for agent in utility.AGENTS:
            for rel in (*_BOOTSTRAP_OWNED_FILES, '.claude', '.agents', '.claude/skills', '.agents/skills'):
                path = self.root / 'workspaces' / agent / rel
                value = utility.image(path)
                if value['kind'] != 'absent': generated[str(path.relative_to(self.root))] = value
            for provider in ('.claude', '.agents'):
                for path in sorted((self.root / 'workspaces' / agent / provider / 'skills').iterdir()):
                    generated[str(path.relative_to(self.root))] = utility.image(path)
        after_store = utility.preservation_inventory(self.store)
        global_images = {rel: utility.image(self.store / rel) for rel in after_store if rel != '.'}
        shutil.rmtree(self.root)
        shutil.copytree(self.closed, self.root, symlinks=True)
        # Preview may add native immutable-addressed packages. This fixture
        # deliberately retains them before check, so global refresh fsync is
        # exercised; it makes no new-package-stage crash claim.
        assert after_store == self.original_store, 'a new preview package requires an independent closed-store setup'
        self.operation_dir = directory / 'operation'; self.operation_dir.mkdir(mode=0o700)
        self.observers = directory / 'observers'; self.observers.mkdir(mode=0o700)
        self.plan = self.operation_dir / 'check-input.json'
        self.plan.write_text(json.dumps(dict(operation_id='C6-' + uuid.uuid4().hex,
            operation_dir=str(self.operation_dir), closed_restore_root=str(self.closed),
            closed_database_backup=str(self.closed / 'happyranch.db'), closed_canonical_store_restore=str(self.closed_store),
            head_manager_sentence=self.manager_sentence, containment=copy.deepcopy(declaration),
            reader_binding=utility.reader_binding(), generated_after_images=generated,
            canonical_store_after_images=global_images, materialization_event_shapes=event_shapes), sort_keys=True))
        self.baseline_rows = self.rows()
        self.baseline = utility.preservation_inventory(self.root)
        self.manifest_path = self.operation_dir / 'manifest.json'
        positive = self.run('--check', '--plan', str(self.plan))
        assert positive.returncode == 0, ('M prerequisite/positive check failed', positive.stderr)
        self.manifest_path.write_bytes(positive.stdout.encode())
        self.digest = hashlib.sha256(self.manifest_path.read_bytes()).hexdigest()
        self.manifest = json.loads(self.manifest_path.read_bytes())
        assert self.manifest['kind'] == 'THR296-checked-manifest-v1'
        assert self.manifest['source_sha'] == self.revision
        assert utility.preservation_inventory(self.root) == self.baseline
        assert self.rows() == self.baseline_rows
        self.event_map = self.observers / 'source-map.json'
        self.event_map.write_text(json.dumps(self.frame_map(), sort_keys=True))

    def _materialize(self, org):
        from runtime.orchestrator._paths import OrgPaths
        from runtime.orchestrator.context_builder import ContextBuilder
        from runtime.orchestrator.prompt_loader import load_agent
        from runtime.orchestrator.workspace_adapters import materialize_workspace_skills_union, validate_workspace_skills_integrity
        for agent in ('consultant_head', 'consultant_codex'):
            definition = load_agent(OrgPaths(root=org.root), agent)
            workspace = org.root / 'workspaces' / agent
            ContextBuilder(org.settings, OrgPaths(root=org.root), slug='alpha').ensure_workspace_ready(workspace, agent, definition.system_prompt, provider=definition.executor)
            specs = materialize_workspace_skills_union(workspace, org.settings, slug='alpha',
                contexts=['task', 'thread', 'wake', 'dream', 'schedule', 'bootstrap'], provider=definition.executor,
                agent_name=agent, team=definition.team, skills_root=self.source / 'runtime/skills', org_root=org.root, db=org.db)
            validate_workspace_skills_integrity(workspace, expected_specs=specs, settings=org.settings, db=org.db, agent_name=agent)

    def rows(self):
        from scripts import migrate_human_team_roster as utility
        with utility.read_db(self.root / 'happyranch.db') as conn:
            return {name: sorted(tuple(row) for row in conn.execute('SELECT * FROM "' + name + '"'))
                for name, in conn.execute("SELECT name FROM sqlite_schema WHERE type='table' ORDER BY name")}

    def run(self, *arguments):
        launcher = "import runpy,sys;from pathlib import Path;s=Path(sys.argv[1]);sys.dont_write_bytecode=True;sys.path.insert(0,str(s));p=s/'scripts/migrate_human_team_roster.py';sys.argv=[str(p),*sys.argv[2:]];runpy.run_path(str(p),run_name='__main__')"
        return subprocess.run([sys.executable, '-I', '-c', launcher, str(self.source),
            '--runtime-root', str(self.runtime), '--org', 'alpha', *arguments], cwd=self.source,
            env=self.env, capture_output=True, text=True, timeout=120)

    def apply_arguments(self, direction=None):
        result = ['--recover' if direction else '--apply', '--runtime-root', str(self.runtime), '--org', 'alpha',
            '--manifest', str(self.manifest_path), '--expected-digest', self.digest]
        if direction: result += ['--operation-id', self.manifest['operation_id'], '--direction', direction]
        return result

    def frame_map(self):
        owners = {
            'scripts/migrate_human_team_roster.py': ['restore_verified', 'final_cas', 'durable_replace', 'write_receipt'],
            'runtime/infrastructure/db/sessions.py': ['SessionsMixin.reset_thread_sessions_for_agent', 'SessionsMixin._reset_thread_sessions_for_agent_uncommitted'],
            'runtime/orchestrator/teams.py': ['TeamsRegistry.load'],
            'runtime/orchestrator/context_builder.py': ['ContextBuilder.ensure_workspace_ready'],
            'runtime/orchestrator/workspace_adapters.py': ['materialize_workspace_skills_union'],
            'runtime/workflows/profile_coordinator.py': ['ProfileCoordinator._capture_org_dependencies', 'ProfileCoordinator.reconcile_supported_roster_batch'],
            'runtime/workflows/authority.py': ['WorkflowAuthorityCoordinator._begin_publication_fence', 'WorkflowAuthorityCoordinator._publish_current_locked', 'WorkflowAuthorityCoordinator.publish_after_supported_change'],
        }
        sites = [dict(file=rel, sha256=hashlib.sha256((self.source / rel).read_bytes()).hexdigest(), qualname=name, events=['call', 'return']) for rel, names in owners.items() for name in names]
        cuts = {}
        for name, rel, qualname, agent in [
            ('restore_verified', 'scripts/migrate_human_team_roster.py', 'restore_verified', None),
            ('final_cas', 'scripts/migrate_human_team_roster.py', 'final_cas', None),
            ('head_reset', 'runtime/infrastructure/db/sessions.py', 'SessionsMixin.reset_thread_sessions_for_agent', 'consultant_head'),
            ('codex_reset', 'runtime/infrastructure/db/sessions.py', 'SessionsMixin.reset_thread_sessions_for_agent', 'consultant_codex'),
            ('registry_reload', 'runtime/orchestrator/teams.py', 'TeamsRegistry.load', None),
            ('profile_capture', 'runtime/workflows/profile_coordinator.py', 'ProfileCoordinator._capture_org_dependencies', None),
            ('profile_reconcile', 'runtime/workflows/profile_coordinator.py', 'ProfileCoordinator.reconcile_supported_roster_batch', None),
            ('head_refresh', 'runtime/orchestrator/context_builder.py', 'ContextBuilder.ensure_workspace_ready', 'consultant_head'),
            ('codex_refresh', 'runtime/orchestrator/context_builder.py', 'ContextBuilder.ensure_workspace_ready', 'consultant_codex'),
            ('publication_prepare', 'runtime/workflows/authority.py', 'WorkflowAuthorityCoordinator._begin_publication_fence', None),
            ('publication', 'runtime/workflows/authority.py', 'WorkflowAuthorityCoordinator._publish_current_locked', None),
            ('receipt', 'scripts/migrate_human_team_roster.py', 'write_receipt', None)]:
            for event in ('call', 'return'): cuts[name + '.' + event] = dict(file=rel, qualname=qualname, event=event, agent=agent)
        return dict(source_sha=self.revision, sites=sites, cuts=cuts)

    def observed(self, arguments, label, syscall='observe-only', frame='observe-only'):
        syscall_receipt = self.observers / (label + '-syscalls.jsonl')
        frame_receipt = self.observers / (label + '-frames.jsonl')
        command = [sys.executable, '-I', str(self.source / 'tests/helpers/roster_syscall_observer.py'),
            '--source', str(self.source), '--source-sha', self.revision, '--manifest', str(self.manifest_path),
            '--expected-digest', self.digest, '--boundary', syscall, '--receipt', str(syscall_receipt), '--',
            sys.executable, '-I', str(self.source / 'tests/helpers/roster_python_observer.py'),
            '--source', str(self.source), '--source-sha', self.revision, '--event-map', str(self.event_map),
            '--boundary', frame, '--receipt', str(frame_receipt), '--script', str(self.source / 'scripts/migrate_human_team_roster.py'), '--', *arguments]
        actual = subprocess.run(command, cwd=self.source, env=self.env, capture_output=True, text=True, timeout=120)
        # Mandatory receipts: inability/loss is an assertion failure, not a skip.
        assert syscall_receipt.is_file() and frame_receipt.is_file(), ('paired observation never reached the utility', actual.returncode, actual.stderr)
        syscalls = [json.loads(line) for line in syscall_receipt.read_text().splitlines()]
        frames = [json.loads(line) for line in frame_receipt.read_text().splitlines()]
        assert syscalls[0]['kind'] == frames[0]['kind'] == 'begin'
        assert syscalls[0]['source_sha'] == frames[0]['source_sha'] == self.revision
        assert syscalls[0]['manifest_sha256'] == self.digest
        assert Path(syscalls[0]['effective_python']).resolve() == Path(sys.executable).resolve()
        assert Path(frames[0]['effective_python']).resolve() == Path(sys.executable).resolve()
        assert frames[0]['script_sha256'] == hashlib.sha256((self.source / 'scripts/migrate_human_team_roster.py').read_bytes()).hexdigest()
        assert syscalls[-1]['kind'] == 'terminal' and syscalls[-1]['complete'], (actual.stderr, syscalls[-1])
        assert [row['sequence'] for row in syscalls] == list(range(1, len(syscalls) + 1))
        assert [row['sequence'] for row in frames] == list(range(1, len(frames) + 1))
        if syscall != 'observe-only':
            assert actual.returncode == 0 and syscalls[-1]['selected_fault']
            assert syscalls[-1]['target'] == {'signal': signal.SIGKILL}
            assert any(row['kind'] == 'selected-fault' and row['boundary'] == syscall for row in syscalls)
        elif frame != 'observe-only':
            assert syscalls[-1]['target'] == {'signal': signal.SIGKILL}
            assert frames[-1]['kind'] == 'selected-fault' and frames[-1]['boundary'] == frame
        else:
            assert frames[-1]['kind'] == 'terminal' and frames[-1]['complete'], frames[-1]
            assert syscalls[-1]['target'] == {'exit': actual.returncode}
        return actual, syscalls, frames

    @staticmethod
    def require_complete_observers(syscalls, frames):
        for rows in (syscalls, frames):
            assert rows and rows[-1]['kind'] == 'terminal' and rows[-1]['complete'], 'observer proof unavailable/incomplete'
            assert [row['sequence'] for row in rows] == list(range(1, len(rows) + 1)), 'observer receipt loss'

    def no_effects(self, syscalls, frames):
        self.require_complete_observers(syscalls, frames)
        forbidden = {'SessionsMixin.reset_thread_sessions_for_agent', 'SessionsMixin._reset_thread_sessions_for_agent_uncommitted',
            'ContextBuilder.ensure_workspace_ready', 'materialize_workspace_skills_union',
            'ProfileCoordinator.reconcile_supported_roster_batch', 'WorkflowAuthorityCoordinator._begin_publication_fence',
            'WorkflowAuthorityCoordinator._publish_current_locked', 'WorkflowAuthorityCoordinator.publish_after_supported_change'}
        assert not [row for row in frames if row['kind'] == 'frame' and row['event'] == 'call' and row['qualname'] in forbidden]
        assert not [row for row in syscalls if row['kind'] == 'syscall-entry' and row['protected']
                    and row['syscall'] not in ('fsync', 'fdatasync')]

    def preservation(self, direction='complete'):
        from scripts import migrate_human_team_roster as utility
        after = self.rows()
        excluded = {'sqlite_sequence', 'audit_log', 'thread_participants', 'workflow_authority_pointers',
            'workflow_publication_journals', 'workflow_publication_leases', 'workflow_profile_leases', 'skill_validation_events'}
        for table, before in self.baseline_rows.items():
            if table not in excluded: assert after[table] == before, table
        for before, current in zip(self.baseline_rows['thread_participants'], after['thread_participants'], strict=True):
            assert current == (*before[:4], None, 0) if before[1] in utility.AGENTS else current == before
        audit_before = self.baseline_rows['audit_log']
        assert after['audit_log'][:len(audit_before)] == audit_before
        for agent in utility.AGENTS:
            affected = [row for row in after['thread_participants'] if row[1] == agent]
            rows = [row for row in after['audit_log'] if row[1] == f"config:THR296:{self.manifest['operation_id']}:{agent}"]
            assert len(rows) == bool(affected)
        raw = (self.root / 'org/.workflow-authority.json').read_bytes()
        pointer = after['workflow_authority_pointers'][0]
        assert hashlib.sha256(raw).hexdigest() == pointer[3] and pointer[4] == 'ready'
        selected = [row for row in after['workflow_publication_journals'] if row[0] == pointer[2]][0]
        assert selected[4] == raw and selected[5] == pointer[3] and selected[9] == 'cache_installed'
        assert selected[6] == f"THR296:{self.manifest['operation_id']}:ready:{direction}"
        assert pointer[1] > self.baseline_rows['workflow_authority_pointers'][0][1]
        for rel in utility.CANONICAL[:2]:
            text = (self.root / rel).read_text()
            before = self.canonical_before[rel].decode()
            expected = before.replace('team: consultant\n', 'team: default\n', 1)
            if rel.endswith('consultant_head.md'):
                expected = expected.replace('role: manager\n', 'role: worker\n', 1).replace(self.manager_sentence, utility.ADVICE, 1)
            assert text == (before if direction == 'compensate' else expected)
        roster = yaml.safe_load((self.root / 'org/teams.yaml').read_bytes())
        old = yaml.safe_load(self.canonical_before['org/teams.yaml'])
        if direction == 'complete':
            assert roster['teams']['default'] == {'manager': {'kind': 'human', 'principal': 'founder'}, 'workers': ['consultant_head', 'consultant_codex']}
            assert 'consultant' not in roster['teams'] and roster['default_team'] == 'default' and roster['task_default_team'] == 'engineering'
            for team, value in old['teams'].items():
                if team not in ('consultant', 'default'): assert roster['teams'][team] == value
            assert '# unrelated literal engineering metadata remains' in (self.root / 'org/teams.yaml').read_text()
            assert '# preserve this pointer comment' in (self.root / 'org/teams.yaml').read_text()
        else: assert (self.root / 'org/teams.yaml').read_bytes() == self.canonical_before['org/teams.yaml']
        utility.verify_preserved_paths(self.root, self.manifest)
        utility.verify_global_assets(self.manifest)
        # Ordinary source-bound compatible cold reopen, not an after-roster mock.
        launcher = "import sys,json;from pathlib import Path;sys.path.insert(0,sys.argv[1]);from runtime.config import Settings;from runtime.daemon.org_state import OrgState;from runtime.workflows.profile_coordinator import ProfileCoordinator;o=OrgState.load(slug='alpha',root=Path(sys.argv[2]),settings=Settings(project_root=Path(sys.argv[1])));p=ProfileCoordinator(daemon_home=Path(sys.argv[3]),orgs={'alpha':o});p.reconcile_startup();o.workflow_authority.capture_admission();assert o.db.get_task('TASK-C6-HISTORY').team=='consultant';assert o.db.get_task('TASK-C6-HISTORY').current_session_id=='retained-fixture-session';r=o.db.get_recall_payload('TASK-C6-HISTORY');assert r['assigned_agent']=='consultant_head' and r['output_summary']=='Retained Consultant historical note';assert o.db.get_task_results('TASK-C6-HISTORY')[0]['session_id']=='retained-fixture-session';assert o.db.get_audit_logs('TASK-C6-HISTORY');o.close()"
        reopened = subprocess.run([sys.executable, '-I', '-c', launcher, str(self.source), str(self.root), str(self.home)],
            env=self.env, cwd=self.source, capture_output=True, text=True, timeout=60)
        assert reopened.returncode == 0, reopened.stderr
        assert self.rows() == after


@pytest.fixture
def maintenance_case(request, monkeypatch):
    """M location input is not authorization or a containment certificate.

    The external operator prepares the real unit inventory/inhibition and this
    private input file. This test does not stop/mask services or operate a guest.
    """
    import uuid
    supplied = os.environ.get('HAPPYRANCH_TEST_ROSTER_M_INPUT')
    assert supplied, 'M unprovisioned: externally authorized inhibition/input required'
    input_path = Path(supplied).resolve(strict=True)
    declaration = json.loads(input_path.read_bytes())
    venue = Path(declaration['venue']).resolve(strict=True)
    assert venue.is_absolute() and not venue.is_relative_to(Path('/tmp'))
    assert venue.stat().st_uid == os.getuid() and (venue.stat().st_mode & 0o777) == 0o700
    assert declaration['kind'] == 'THR296-test-fixture-input-not-manifest'
    home = Path(declaration['containment']['daemon_home']).resolve(strict=True)
    assert home.is_relative_to(venue)
    monkeypatch.setenv('HAPPYRANCH_DAEMON_HOME', str(home))
    monkeypatch.setenv('HAPPYRANCH_CANONICAL_STORE_ROOT', str(home / 'canonical-skills'))
    params = getattr(getattr(request.node, 'callspec', None), 'params', {})
    directory = venue / ('case-' + uuid.uuid4().hex); directory.mkdir(mode=0o700)
    default_shape = params.get('default_shape', params['refusal'][0] if isinstance(params.get('refusal'), tuple) else 'no-default')
    case = _MaintenanceCase(directory, declaration['containment'], default_shape, params.get('row_shape', 'ordinary_rows'))
    yield case
    # Preserve owned adverse/cut receipts for independent assessment. No broad
    # teardown sweep or production worktree cleanup is performed by this fixture.


@pytest.mark.parametrize('default_shape', ['no-default', 'correct-empty-default'])
def test_c6_exact_manifest_apply_and_preservation(maintenance_case, default_shape):
    case = maintenance_case
    applied, syscalls, frames = case.observed(case.apply_arguments(), 'apply')
    assert applied.returncode == 0, applied.stderr
    actual = json.loads(applied.stdout.splitlines()[-1])
    assert actual['operation_id'] == case.manifest['operation_id'] and actual['manifest_sha256'] == case.digest
    assert actual['direction'] == 'complete' and actual['traffic_released'] is False
    case.preservation()
    receipt = (case.operation_dir / 'receipt.json').read_bytes()
    from scripts import migrate_human_team_roster as utility
    for index in range(2):
        before = utility.preservation_inventory(case.root); rows = case.rows()
        repeated, syscalls, frames = case.observed(case.apply_arguments(), 'replay-' + str(index))
        assert repeated.returncode == 0 and json.loads(repeated.stdout.splitlines()[-1]) == actual
        case.no_effects(syscalls, frames)
        assert case.rows() == rows and utility.preservation_inventory(case.root) == before
        assert (case.operation_dir / 'receipt.json').read_bytes() == receipt



# Each M row follows an actual successful check, then changes one condition.
# Earlier containment failures cannot stand in for these later guards.
C8_M_REFUSALS = [
    'pending-child', 'pending-ancestor', 'chain', 'fanout', 'callback', 'recovery',
    'invocation', 'delivery', 'probe', 'exchange', 'deferred-exchange', 'job', 'dream', 'wake', 'schedule',
    'schedule-session', 'enrollment', 'workflow-owner', 'unrelated-draft', 'remote-lease',
    'active-launch', 'unknown-launch', 'wrong-root', 'wrong-reader', 'registry-CAS',
    'canonical-hash', 'canonical-mode', 'canonical-type', 'generated-hash', 'generated-mode',
    'generated-type', 'generated-owner', 'authority-CAS', 'profile-CAS', 'foreign-journal',
    'foreign-lease', 'conflicting-default', 'duplicate-membership', 'incomplete-inventory',
    'inaccessible-memory', 'unknown-reference', 'unexpected-grant', 'storage-bytes', 'storage-inodes',
    'restore-corrupt', 'restore-metadata', 'restore-alias', 'backup-CAS', 'open-WAL',
    'global-restore-CAS',
    'observer-empty', 'observer-short', 'observer-no-isolation', 'observer-extra-flag',
    'observer-wrong-interpreter',
    'observer-wrong-script', 'observer-short-source', 'observer-short-source-sha',
    'observer-short-script', 'observer-duplicate-script', 'observer-utility-binding',
    'observer-no-separator', 'observer-empty-utility',
]


def _c8_maintenance_refusal(case, refusal):
    import copy
    import base64
    from scripts import migrate_human_team_roster as utility
    if refusal.startswith('observer-'):
        # Admit a complete paired observation of the genuine utility first.
        # Admission negatives are observer refusals, never migration evidence.
        arguments = ['--check', '--runtime-root', str(case.runtime), '--org', 'alpha', '--plan', str(case.plan)]
        positive, syscalls, frames = case.observed(arguments, 'paired-positive')
        assert positive.returncode == 0, positive.stderr
        case.require_complete_observers(syscalls, frames)
        assert json.loads(positive.stdout)['kind'] == 'THR296-checked-manifest-v1'
        case.no_effects(syscalls, frames)
        receipt = case.observers / 'paired-refused-syscalls.jsonl'
        frame_receipt = case.observers / 'paired-refused-frames.jsonl'
        paired = [sys.executable, '-I', str(case.source / 'tests/helpers/roster_python_observer.py'),
            '--source', str(case.source), '--source-sha', case.revision, '--event-map', str(case.event_map),
            '--boundary', 'observe-only', '--receipt', str(frame_receipt),
            '--script', str(case.source / 'scripts/migrate_human_team_roster.py'), '--', *arguments]
        expected = 'pinned_python_and_paired_frame_observer_required'
        if refusal == 'observer-empty': paired = []
        elif refusal == 'observer-short': paired = paired[:2]
        elif refusal == 'observer-no-isolation': paired.pop(1)
        elif refusal == 'observer-extra-flag': paired.insert(2, '-B')
        elif refusal == 'observer-wrong-interpreter': paired[0] = str(case.source)
        elif refusal == 'observer-wrong-script': paired[2] = str(case.source / 'tests/helpers/roster_syscall_observer.py')
        elif refusal in ('observer-short-source', 'observer-short-source-sha', 'observer-short-script'):
            flag = '--' + refusal.removeprefix('observer-short-')
            index = paired.index(flag); del paired[index:index + 2]
            index = paired.index('--'); paired[index:index] = [flag]
            expected = ('only_bounded_roster_utility_may_be_observed' if flag == '--script'
                        else 'paired_frame_source_binding_mismatch')
        elif refusal in ('observer-no-separator', 'observer-empty-utility'):
            index = paired.index('--')
            paired = paired[:index] if refusal == 'observer-no-separator' else paired[:index + 1]
            expected = 'only_bounded_roster_utility_may_be_observed'
        elif refusal == 'observer-duplicate-script':
            index = paired.index('--')
            paired[index:index] = ['--script', str(case.source / 'scripts/migrate_human_team_roster.py')]
            expected = 'only_bounded_roster_utility_may_be_observed'
        elif refusal == 'observer-utility-binding':
            index = paired.index('--script'); value = paired[index:index + 2]; del paired[index:index + 2]
            paired.extend(value)
            expected = 'only_bounded_roster_utility_may_be_observed'
        else: raise AssertionError('unowned observer admission parameter: ' + refusal)
        command = [sys.executable, '-I', str(case.source / 'tests/helpers/roster_syscall_observer.py'),
            '--source', str(case.source), '--source-sha', case.revision, '--manifest', str(case.manifest_path),
            '--expected-digest', case.digest, '--boundary', 'observe-only', '--receipt', str(receipt), '--', *paired]
        before, rows = utility.preservation_inventory(case.root), case.rows()
        refused = subprocess.run(command, cwd=case.source, env=case.env, capture_output=True, text=True, timeout=30)
        assert refused.returncode == 86 and expected in refused.stderr, refused.stderr
        assert not receipt.exists() and not frame_receipt.exists()
        assert utility.preservation_inventory(case.root) == before and case.rows() == rows
        return
    plan = json.loads(case.plan.read_bytes())
    mode = 'check'
    held = None
    child = None
    expected = None
    # Explicit SQL records below are synthetic pending-owner DATA, not actual
    # accepted callback/result/runner receipts. Real utility inventory owns the
    # observed refusal. Closed restore is renewed after fixture setup only.
    sql = {
        'invocation': ("UPDATE thread_invocations SET status='pending' WHERE invocation_token='retained-THR-C6-0'", (), 'thread_invocations'),
        'chain': ("UPDATE tasks SET active_chain='{}' WHERE id='TASK-C6-HISTORY'", (), 'tasks'),
        'fanout': ("UPDATE tasks SET active_fanout='{}' WHERE id='TASK-C6-HISTORY'", (), 'tasks'),
        'callback': ("INSERT INTO task_completion_recoveries(task_id,agent,origin_session_id,recovery_session_id,provider_session_id,claimed_at,expires_at,state) VALUES ('TASK-C6-HISTORY','consultant_head','fixture-origin','fixture-recovery','fixture-provider','2026-10-01','2026-10-31','callback_accepted')", (), 'task_completion_recoveries'),
        'recovery': ("INSERT INTO task_completion_recoveries(task_id,agent,origin_session_id,recovery_session_id,provider_session_id,claimed_at,expires_at,state) VALUES ('TASK-C6-HISTORY','consultant_head','fixture-origin','fixture-recovery','fixture-provider','2026-10-01','2026-10-31','claimed')", (), 'task_completion_recoveries'),
        'delivery': ("UPDATE thread_reply_delivery_state SET required_through_seq=8 WHERE thread_id='THR-C6-0' AND agent_name='consultant_head'", (), 'thread_reply_delivery_state'),
        'probe': ("UPDATE thread_reply_breaker_episodes SET state='probe',probe_lease_id='fixture-probe' WHERE thread_id='THR-C6-0' AND agent_name='consultant_head'", (), 'thread_reply_breaker_episodes'),
        'job': ("INSERT INTO jobs(id,task_id,agent_name,title,script_text,interpreter,status,created_at) VALUES ('JOB-C8','TASK-C6-HISTORY','consultant_head','fixture pending','true','bash','pending','2026-10-01')", (), 'jobs'),
        'dream': ("INSERT INTO dreams(id,agent_name,local_date,scheduled_for,window_end,status,created_at) VALUES ('DREAM-C8','consultant_head','2026-10-01','2026-10-01','2026-10-02','pending','2026-10-01')", (), 'dreams'),
        'wake': ("INSERT INTO work_hours(id,agent_name,local_date,slot,mode,scheduled_for,status,created_at) VALUES ('WAKE-C8','consultant_head','2026-10-01','09:00','routine','2026-10-01','pending','2026-10-01')", (), 'work_hours'),
        'schedule': ("INSERT INTO schedules(id,agent_name,kind,fire_at,normalized_brief,source_instruction,status,active,created_at,updated_at) VALUES ('SCHEDULE-C8','consultant_head','one_shot','2026-10-01','fixture','fixture','armed',1,'2026-10-01','2026-10-01')", (), 'schedules'),
        'schedule-session': ("INSERT INTO schedules(id,agent_name,kind,fire_at,normalized_brief,source_instruction,status,active,session_id,created_at,updated_at) VALUES ('SCHEDULE-C8','consultant_head','one_shot','2026-10-01','fixture','fixture','paused',0,'fixture-running','2026-10-01','2026-10-01')", (), 'schedules'),
    }
    try:
        if refusal in ('pending-child', 'pending-ancestor'):
            from runtime.infrastructure.database import Database
            from runtime.models import TaskRecord, TaskStatus
            db = Database(case.root / 'happyranch.db')
            try:
                db.insert_task(TaskRecord(id='TASK-C8-PENDING', status=TaskStatus.PENDING,
                    assigned_agent='consultant_codex', team='consultant', brief='Synthetic pending owner',
                    parent_task_id='TASK-C6-HISTORY' if refusal == 'pending-child' else None,
                    task_type='subtask' if refusal == 'pending-child' else 'task'))
                if refusal == 'pending-ancestor':
                    db.update_task('TASK-C6-HISTORY', parent_task_id='TASK-C8-PENDING')
            finally: db.close()
            expected = 'nonquiescent_tasks'
        elif refusal in sql:
            statement, parameters, table = sql[refusal]
            with sqlite3.connect(case.root / 'happyranch.db') as conn: conn.execute(statement, parameters)
            expected = 'nonquiescent_' + table
        elif refusal in ('exchange', 'deferred-exchange'):
            with sqlite3.connect(case.root / 'happyranch.db') as conn:
                conn.execute("UPDATE thread_reply_exchange SET state=? WHERE thread_id='THR-C6-0' AND exchange_id=1", ('open' if refusal == 'exchange' else 'released',))
                if refusal == 'deferred-exchange':
                    conn.execute("UPDATE thread_exchange_deferrals SET state='held' WHERE thread_id='THR-C6-0' AND exchange_id=1 AND agent_name='consultant_codex'")
            expected = 'nonquiescent_' + ('thread_reply_exchange' if refusal == 'exchange' else 'thread_exchange_deferrals')
        elif refusal in ('workflow-owner', 'unrelated-draft'):
            # Valid native producer, not an invented pending workflow row.
            from fastapi.testclient import TestClient
            from runtime.config import Settings
            from runtime.daemon import paths
            from runtime.daemon.app import create_app
            from runtime.daemon.state import DaemonState
            from runtime.runtime import RuntimeDir
            from tests.workflows.authority_test_support import C5_SCHEMA1_HISTORY
            state = DaemonState.from_runtime(RuntimeDir.load(case.runtime), Settings(project_root=case.source))
            client = TestClient(create_app(state), base_url='http://localhost')
            client.headers['Authorization'] = 'Bearer ' + paths.ensure_token()
            try:
                base = '/api/v1/orgs/alpha'
                request = copy.deepcopy(C5_SCHEMA1_HISTORY['request'])
                ready = state.orgs['alpha'].workflow_authority.capture_admission().ready
                request.update(operation_key='c8-pending-document', instance_id='c8-pending-document')
                request['authority'] = dict(namespace=ready.namespace, generation=ready.generation, snapshot_digest=ready.snapshot_digest)
                request['bindings']['product-lead'] = dict(kind='agent', principal='product_lead' if refusal == 'unrelated-draft' else 'consultant_head', team='product' if refusal == 'unrelated-draft' else 'consultant')
                graph = client.post(base + '/workflows/activations', json=request)
                assert graph.status_code == 201, graph.text
                assert graph.json()['root_task_id']
            finally:
                client.close()
                import asyncio
                asyncio.run(state.close_all())
            expected = 'nonquiescent_tasks'
        elif refusal == 'remote-lease':
            # A terminal local job is no proof that its remote workspace is
            # quiescent. Use the existing remote-store fixture owner.
            from tests.infrastructure.test_remote_job_schema_migration import _insert_runner, _insert_workspace
            with sqlite3.connect(case.root / 'happyranch.db') as conn:
                _insert_runner(conn, 'C8-fixture-runner')
                _insert_workspace(conn, 'workspace-1', 'C8-fixture-runner', agent='consultant_head', state='uncertain')
            expected = 'nonquiescent_remote_runner_workspaces'
        elif refusal == 'enrollment':
            pending = case.root / 'org/agents/_pending'; pending.mkdir(exist_ok=True)
            (pending / 'fixture.md').write_text('pending fixture data\n')
            expected = 'pending_or_uninspectable_enrollment'
        elif refusal == 'active-launch':
            child = subprocess.Popen([sys.executable, '-I', '-c', 'import time;time.sleep(120)'], cwd=case.root)
            expected = 'runtime_or_daemon_process_present'
        elif refusal in ('unknown-launch', 'generated-owner', 'storage-bytes', 'storage-inodes'):
            # These require an externally operated adverse condition. Merely
            # naming it does not fabricate unit/proc/owner/statvfs evidence.
            request = case.observers / ('operator-' + refusal + '.request')
            request.write_text(json.dumps({'condition': refusal, 'runtime': str(case.runtime), 'operation_dir': str(case.operation_dir), 'generated': str(case.root / 'workspaces/consultant_head/AGENTS.md')}))
            done = request.with_suffix('.ready')
            deadline = time.monotonic() + 30
            while not done.exists() and time.monotonic() < deadline: time.sleep(0.05)
            assert done.exists(), 'external adverse M condition unavailable: ' + refusal
            expected = {'unknown-launch': 'unproven_supervisor_launch_closure', 'generated-owner': 'preservation_owner_not_restorable',
                        'storage-bytes': 'insufficient_operation_storage_bytes_or_inodes', 'storage-inodes': 'insufficient_operation_storage_bytes_or_inodes'}[refusal]
            if refusal.startswith('storage-'):
                facts = os.statvfs(case.operation_dir)
                assert facts.f_bavail * facts.f_frsize == 0 if refusal == 'storage-bytes' else facts.f_favail == 0
        elif refusal in ('wrong-root', 'wrong-reader', 'registry-CAS'):
            mode = 'apply'
            if refusal == 'wrong-reader':
                changed = copy.deepcopy(case.manifest); changed['reader_binding']['source_sha'] = '0' * 40
                case.manifest_path.write_text(json.dumps(changed)); case.digest = hashlib.sha256(case.manifest_path.read_bytes()).hexdigest()
                expected = 'effective_reader_binding_mismatch'
            else:
                registry = case.home / 'runtimes.yaml'; raw = yaml.safe_load(registry.read_bytes())
                if refusal == 'wrong-root': raw['registered'].remove(str(case.runtime)); expected = 'effective_runtime_registration_mismatch'
                else: raw['fixture_comment'] = 'changed'; expected = 'runtime_registration_before_image_CAS_lost'
                registry.write_text(yaml.safe_dump(raw))
        elif refusal.startswith(('canonical-', 'generated-')):
            mode = 'apply'
            path = case.root / ('org/agents/consultant_head.md' if refusal.startswith('canonical-') else 'workspaces/consultant_head/AGENTS.md')
            if refusal.endswith('-hash'): path.write_bytes(path.read_bytes() + b'\nthird-state\n')
            elif refusal.endswith('-mode'): path.chmod(path.stat().st_mode ^ 0o100)
            elif refusal.endswith('-type'): path.unlink(); path.symlink_to('third-state')
            expected = 'canonical_path_redirected' if refusal == 'canonical-type' else 'unknown_third_state'
        elif refusal in ('authority-CAS', 'profile-CAS', 'foreign-journal', 'foreign-lease'):
            mode = 'apply'
            with sqlite3.connect(case.root / 'happyranch.db') as conn:
                if refusal == 'authority-CAS':
                    conn.execute("UPDATE workflow_authority_pointers SET profile_fence=profile_fence+1 WHERE namespace='org/alpha'")
                    expected = 'publication_profile_or_audit_before_image_CAS_lost'
                elif refusal == 'profile-CAS':
                    conn.execute("INSERT INTO workflow_profile_dependencies VALUES ('org/alpha','foreign-profile','consultant_head',1,'unbound')")
                    expected = 'retained_profile_dependencies_changed'
                elif refusal == 'foreign-lease':
                    conn.execute("INSERT INTO workflow_profile_leases VALUES ('foreign-profile','foreign-operation',?)", (os.getpid(),))
                    expected = 'foreign_profile_lease'
                else:
                    conn.execute("INSERT INTO workflow_publication_journals SELECT 'WAJ-c8-foreign',namespace,generation,expected_generation,snapshot_bytes,snapshot_digest,'foreign-operation',publisher_invocation,profile_fence,state,recovery_owner,file_phase_owner FROM workflow_publication_journals LIMIT 1")
                    expected = 'unowned_publication_residue'
        elif refusal in ('conflicting-default', 'duplicate-membership'):
            path = case.root / 'org/teams.yaml'; roster = yaml.safe_load(path.read_bytes())
            roster['teams']['default'] = {'manager': {'kind': 'human', 'principal': 'founder'}, 'workers': ['consultant_codex'] if refusal == 'duplicate-membership' else ['dev_agent']}
            if refusal == 'conflicting-default':
                # Valid membership, wrong allowed empty-Default before-image.
                roster['teams']['engineering']['workers'].remove('dev_agent')
                agent = case.root / 'org/agents/dev_agent.md'; agent.write_text(agent.read_text().replace('team: engineering', 'team: default'))
            path.write_text(yaml.safe_dump(roster))
            expected = 'conflicting_Default' if refusal == 'conflicting-default' else 'duplicate team memberships'
        elif refusal == 'incomplete-inventory':
            plan['generated_after_images'].pop('workspaces/consultant_codex/CLAUDE.md')
            expected = 'both_complete_native_instruction_pairs_required'
        elif refusal == 'inaccessible-memory':
            (case.root / 'workspaces/consultant_head/memory/MEM-C6.md').chmod(0)
            expected = 'Permission denied'
        elif refusal == 'unknown-reference':
            entry = dict(plan['generated_after_images']['workspaces/consultant_head/CLAUDE.md'])
            data = b'../../unknown-reference'; entry.update(bytes=base64.b64encode(data).decode(), sha256=hashlib.sha256(data).hexdigest())
            plan['generated_after_images']['workspaces/consultant_head/.agents/skills/unknown'] = entry
            expected = 'skill_link_outside_original_canonical_package'
        elif refusal == 'unexpected-grant':
            rel = 'workspaces/consultant_head/.claude/settings.json'; entry = plan['generated_after_images'][rel]
            settings = json.loads(base64.b64decode(entry['bytes'])); settings['permissions']['allow'].append('Bash(sudo:*)')
            raw = json.dumps(settings).encode(); entry.update(bytes=base64.b64encode(raw).decode(), sha256=hashlib.sha256(raw).hexdigest())
            expected = 'unexpected_generated_permission_grant'
        elif refusal.startswith('restore-') or refusal in ('backup-CAS', 'global-restore-CAS', 'open-WAL'):
            mode = 'apply'
            if refusal == 'restore-corrupt': (case.closed / 'happyranch.db').write_bytes(b'truncated closed backup')
            elif refusal == 'restore-metadata': (case.closed / 'workspaces/consultant_head/memory/MEM-C6.md').chmod(0o600)
            elif refusal == 'restore-alias':
                plan['closed_restore_root'] = str(case.root); plan['closed_database_backup'] = str(case.root / 'happyranch.db'); mode = 'check'
            elif refusal == 'global-restore-CAS':
                path = next(p for p in case.closed_store.rglob('*') if p.is_file() and not p.is_symlink())
                path.chmod(0o600); path.write_bytes(path.read_bytes() + b'third-state')
            elif refusal == 'open-WAL':
                mode = 'check'; held = sqlite3.connect(case.root / 'happyranch.db'); held.execute('PRAGMA journal_mode=WAL')
                held.execute("INSERT INTO audit_log(task_id,agent,action,payload,timestamp) VALUES ('config:c8','founder','fixture','{}','2026-10-01')"); held.commit()
            else:
                (case.closed / 'happyranch.db').write_bytes((case.closed / 'happyranch.db').read_bytes() + b'stale')
            expected = ('independent_closed_restore_required' if refusal == 'restore-alias' else 'database_not_closed_checkpointed' if refusal == 'open-WAL'
                        else 'canonical_store_closed_restore_changed' if refusal == 'global-restore-CAS' else 'closed_restore_changed')
        else: raise AssertionError('unowned C8 parameter: ' + refusal)
        if mode == 'check' and not refusal.startswith('restore-') and refusal not in ('global-restore-CAS', 'inaccessible-memory', 'generated-owner'):
            # This is fixture backup of the single varied condition, never
            # recovery of a foreign operation or cancellation-as-preflight.
            shutil.rmtree(case.closed); shutil.copytree(case.root, case.closed, symlinks=True)
        encoded = json.dumps(plan, sort_keys=True)
        if case.plan.read_text() != encoded: case.plan.write_text(encoded)
        before = utility.preservation_inventory(case.root) if refusal not in ('inaccessible-memory', 'generated-owner') else None
        rows = case.rows()
        arguments = case.apply_arguments() if mode == 'apply' else ['--check', '--runtime-root', str(case.runtime), '--org', 'alpha', '--plan', str(case.plan)]
        actual, syscalls, frames = case.observed(arguments, 'refusal-' + refusal)
        assert actual.returncode == 1 and expected in actual.stderr, (refusal, expected, actual.stdout, actual.stderr)
        case.no_effects(syscalls, frames)
        assert case.rows() == rows
        if before is not None: assert utility.preservation_inventory(case.root) == before
        assert not (case.operation_dir / 'receipt.json').exists()
    finally:
        if held is not None: held.close()
        if child is not None:
            child.terminate(); child.wait(timeout=10)


@pytest.mark.parametrize('refusal', ['no-inhibition', 'design-plan-not-manifest', 'wrong-digest', *C8_M_REFUSALS])
def test_c8_preflight_refusals_and_backup_cas(runtime: Path, tmp_path: Path, refusal: str, request: pytest.FixtureRequest) -> None:
    """L-only early input refusals through the genuine standalone utility.

    These controls require no systemd/process census or M simulation. Complete
    checked-backup CAS, mutation syscall/frame witnesses and successful recovery
    remain separate M/capability cases. Byte equality is not no-write proof.
    """
    if refusal in C8_M_REFUSALS:
        _c8_maintenance_refusal(request.getfixturevalue('maintenance_case'), refusal)
        return
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



C9_M_CASES = [(default, direction, 'syscall', family + '.' + effect + '.' + side)
    for default in ('no-default', 'correct-empty-default') for direction in ('complete', 'compensate')
    for family in ('head_replace', 'codex_replace', 'teams_replace', 'authority', 'receipt')
    for effect in ('stage_write', 'file_fsync', 'rename', 'dir_fsync') for side in ('before', 'after')]
C9_M_CASES += [(default, direction, 'frame', family + '.' + event)
    for default in ('no-default', 'correct-empty-default') for direction in ('complete', 'compensate')
    for family in ('restore_verified', 'final_cas', 'head_reset', 'codex_reset', 'registry_reload',
                   'head_refresh', 'codex_refresh', 'profile_capture', 'profile_reconcile', 'publication_prepare', 'publication', 'receipt')
    for event in ('call', 'return')]
C9_M_CASES += [(default, direction, 'checked-paths', 'generated-and-global')
    for default in ('no-default', 'correct-empty-default') for direction in ('complete', 'compensate')]
C9_M_CASES += [('no-default', 'complete', 'syscall', 'backup_durable.' + effect + '.' + side)
    for effect in ('file_fsync', 'dir_fsync') for side in ('before', 'after')]
C9_M_CASES += [(default, direction, 'syscall', 'global_store_root.dir_fsync.' + side)
    for default in ('no-default', 'correct-empty-default') for direction in ('complete', 'compensate') for side in ('before', 'after')]
C9_M_CASES += [('no-default', 'complete', 'observer-control', value) for value in ('syscall-loss', 'frame-loss', 'source-capability')]
C9_M_CASES += [('no-default', 'complete', 'third-state', value)
    for value in ('unknown-bytes', 'competing-writer', 'incomplete-backup', 'post-traffic')]


def _c9_partial_attachment_controls(case):
    """Every proper inconsistent subset is tested in its own closed copy."""
    import base64
    from scripts import migrate_human_team_roster as utility
    original = {rel: (case.root / rel).read_bytes() for rel in utility.CANONICAL}
    try:
        for mask in range(1, 7):
            for index, rel in enumerate(utility.CANONICAL):
                (case.root / rel).write_bytes(base64.b64decode(case.manifest['after' if mask & (1 << index) else 'before'][rel]['bytes']))
            before = utility.preservation_inventory(case.root); rows = case.rows()
            attached = _attach_process(case.root, slug='alpha')
            assert attached.returncode == 1 and 'org content is inconsistent' in attached.stderr, attached.stderr
            assert utility.preservation_inventory(case.root) == before and case.rows() == rows
    finally:
        for rel, raw in original.items(): (case.root / rel).write_bytes(raw)


def _c9_process_case(case, direction, kind, boundary):
    from scripts import migrate_human_team_roster as utility
    if (case.operation_dir / 'receipt.json').exists():
        import uuid
        directory = case.directory.parent / ('capability-' + uuid.uuid4().hex)
        directory.mkdir(mode=0o700)
        default = 'correct-empty-default' if 'default' in yaml.safe_load(case.canonical_before['org/teams.yaml'])['teams'] else 'no-default'
        case = _MaintenanceCase(directory, case.manifest['containment'], default, 'ordinary_rows')
    # Normal observer capability is a mandatory positive, then actual process
    # loss is selected. A Linux process kill is never called a guest reboot.
    observed, syscalls, frames = case.observed(case.apply_arguments(), 'capability-apply')
    assert observed.returncode == 0, observed.stderr
    assert any(row['kind'] == 'syscall-entry' and row['protected'] for row in syscalls)
    assert any(row['kind'] == 'syscall-exit' and row['successful'] and row.get('identity') for row in syscalls)
    assert {row['agent'] for row in frames if row['kind'] == 'frame' and row['event'] == 'call'
            and row['qualname'] == 'SessionsMixin.reset_thread_sessions_for_agent'} == {'consultant_head', 'consultant_codex'}
    case.preservation()
    # A NEW operation/fixture follows capability proof; do not rewind a real
    # operated DB, pointer, history or successful receipt to inject a cut.
    directory = case.directory.parent / (case.directory.name + '-cut-' + hashlib.sha256(boundary.encode()).hexdigest()[:12])
    directory.mkdir(mode=0o700)
    default = 'correct-empty-default' if 'default' in yaml.safe_load(case.canonical_before['org/teams.yaml'])['teams'] else 'no-default'
    cut = _MaintenanceCase(directory, case.manifest['containment'], default, 'ordinary_rows')
    _c9_partial_attachment_controls(cut)
    result, syscalls, frames = cut.observed(cut.apply_arguments(), 'selected-cut',
        syscall=boundary if kind == 'syscall' else 'observe-only', frame=boundary if kind == 'frame' else 'observe-only')
    # Capture true operated prefix after all traced processes stopped. Per-agent
    # reset and native publication rows are inspected; no COMMIT is inferred
    # from any observed WAL fsync. Interior SQL cuts belong to U9 below.
    prefix = cut.rows()
    prefix_files = utility.preservation_inventory(cut.root)
    (cut.observers / 'true-prefix.json').write_text(json.dumps({'source_sha': cut.revision,
        'manifest_sha256': cut.digest, 'boundary': boundary, 'direction': direction,
        'publication': prefix['workflow_publication_journals'], 'pointer': prefix['workflow_authority_pointers'],
        'reset': prefix['thread_participants'], 'audit_ids': [row[0] for row in prefix['audit_log']],
        'files': prefix_files, 'sql_commit_inferred_from_syscalls': False}, default=lambda value: hashlib.sha256(value).hexdigest() if isinstance(value, bytes) else str(value), sort_keys=True))
    completed_receipt = (cut.operation_dir / 'receipt.json').read_bytes() if (cut.operation_dir / 'receipt.json').exists() else None
    recovered, recovery_syscalls, recovery_frames = cut.observed(cut.apply_arguments(direction), 'actual-recovery')
    if kind == 'frame' and boundary == 'receipt.call' and direction == 'complete':
        # Durable completion is independently recorded in the prefix; only the
        # external operation receipt may be reconstructed by this new process.
        cut.require_complete_observers(recovery_syscalls, recovery_frames)
        forbidden = {'SessionsMixin.reset_thread_sessions_for_agent', 'SessionsMixin._reset_thread_sessions_for_agent_uncommitted',
            'ContextBuilder.ensure_workspace_ready', 'materialize_workspace_skills_union',
            'ProfileCoordinator.reconcile_supported_roster_batch', 'WorkflowAuthorityCoordinator._begin_publication_fence',
            'WorkflowAuthorityCoordinator._publish_current_locked', 'WorkflowAuthorityCoordinator.publish_after_supported_change'}
        assert not [row for row in recovery_frames if row['kind'] == 'frame' and row['event'] == 'call' and row['qualname'] in forbidden]
        assert not [row for row in recovery_syscalls if row['kind'] == 'syscall-entry' and row['protected']
            and row['syscall'] not in ('fsync', 'fdatasync')
            and any(Path(value).is_relative_to(cut.root) or Path(value).is_relative_to(cut.store) for value in row['paths'])]
        assert cut.rows()['workflow_authority_pointers'] == prefix['workflow_authority_pointers']
    assert recovered.returncode == 0, recovered.stderr
    cut.preservation(direction)
    if completed_receipt is not None:
        assert (cut.operation_dir / 'receipt.json').read_bytes() == completed_receipt
    for index in range(2):
        before = utility.preservation_inventory(cut.root); rows = cut.rows()
        repeated, syscalls, frames = cut.observed(cut.apply_arguments(direction), 'replay-' + str(index))
        assert repeated.returncode == 0, repeated.stderr
        cut.no_effects(syscalls, frames)
        assert cut.rows() == rows and utility.preservation_inventory(cut.root) == before
    if direction == 'compensate':
        refusal, syscalls, frames = cut.observed(cut.apply_arguments(), 'compensated-fresh-required')
        assert refusal.returncode == 1 and 'fresh_operation_required_after_compensation' in refusal.stderr
        cut.no_effects(syscalls, frames)
    if kind == 'frame' and boundary == 'receipt.call' and direction == 'complete':
        # Loss after durable completion before its receipt must reconstruct only
        # that external receipt. Observe this separately from later replay.
        # The two later replays enter neither receipt writer nor product helper.
        assert not any(row['kind'] == 'frame' and row['event'] == 'call'
                       and row['qualname'] == 'write_receipt' for row in frames)


def _c9_maintenance_selection(case, selection):
    default, direction, kind, boundary = selection
    if kind in ('syscall', 'frame'):
        _c9_process_case(case, direction, kind, boundary)
    elif kind == 'observer-control':
        from scripts import migrate_human_team_roster as utility
        applied, _, _ = case.observed(case.apply_arguments(), 'observer-positive-apply')
        assert applied.returncode == 0, applied.stderr
        case.preservation()
        replay, syscalls, frames = case.observed(case.apply_arguments(), 'observer-positive-replay')
        assert replay.returncode == 0, replay.stderr
        case.no_effects(syscalls, frames)
        if boundary == 'syscall-loss':
            with pytest.raises(AssertionError, match='observer proof unavailable/incomplete'):
                case.no_effects(syscalls[:-1], frames)
        elif boundary == 'frame-loss':
            with pytest.raises(AssertionError, match='observer proof unavailable/incomplete'):
                case.no_effects(syscalls, frames[:-1])
        else:
            original = case.event_map.read_bytes()
            wrong = json.loads(original); wrong['sites'][0]['sha256'] = '0' * 64
            before = utility.preservation_inventory(case.root); rows = case.rows()
            try:
                # Map scratch mutation invalidates the real observer before
                # product entry; no mock profile or fabricated receipt.
                case.event_map.write_text(json.dumps(wrong))
                with pytest.raises((AssertionError, FileNotFoundError), match='.*'):
                    case.observed(case.apply_arguments(), 'observer-invalid-source')
            finally:
                case.event_map.write_bytes(original)
            assert utility.preservation_inventory(case.root) == before and case.rows() == rows
    elif kind == 'checked-paths':
        # g/s IDs are bound to real checked path/hash/type images, never a live
        # hand-built manifest or an unbounded workspace discovery campaign.
        paths = []
        canonical = {'org/agents/consultant_head.md', 'org/agents/consultant_codex.md', 'org/teams.yaml'}
        for index, rel in enumerate(sorted(set(case.manifest['after']) - canonical)):
            before, after = case.manifest['before'][rel], case.manifest['after'][rel]
            if after == before or after['kind'] == 'directory': continue
            effects = ('symlink', 'rename', 'dir_fsync') if after['kind'] == 'link' else ('stage_write', 'file_fsync', 'rename', 'dir_fsync')
            # settings.json is the unchanged in-place writer: it has no rename.
            if rel.endswith('.claude/settings.json'): effects = ('stage_write', 'file_fsync')
            paths += [(f'g{index}', rel, after['sha256'], effect) for effect in effects]
        # Existing package addresses are reused; the actual utility flushes
        # their checked file descriptors. New-package staging is a different
        # unprovided preview prerequisite, never claimed from this fixture.
        for index, rel in enumerate(sorted(case.manifest['global_after'])):
            after = case.manifest['global_after'][rel]
            if after['kind'] == 'file':
                effects = ['file_fsync']
                if case.manifest['global_before'][rel]['kind'] == 'absent' and direction == 'complete':
                    effects.append('stage_write')
                paths.extend((f's{index}', rel, after['sha256'], effect) for effect in effects)
            elif after['kind'] == 'directory' and len(Path(rel).parts) == 3 and case.manifest['global_before'][rel]['kind'] == 'absent' and direction == 'complete':
                paths.extend((f's{index}', rel, None, effect) for effect in ('rename', 'dir_fsync'))
        assert paths, 'finite generated/global selection cannot be empty'
        (case.observers / 'checked-syscall-paths.json').write_text(json.dumps(paths))
        for family, rel, sha, effect in paths:
            for side in ('before', 'after'):
                _c9_process_case(case, direction, 'syscall', family + '.' + effect + '.' + side)
    else:
        from scripts import migrate_human_team_roster as utility
        _, _, _ = case.observed(case.apply_arguments(), 'third-state-positive')
        assert (case.operation_dir / 'receipt.json').exists()
        if boundary == 'unknown-bytes': (case.root / 'org/agents/consultant_head.md').write_bytes(b'unknown third state')
        elif boundary == 'competing-writer':
            with sqlite3.connect(case.root / 'happyranch.db') as conn:
                conn.execute("INSERT INTO workflow_profile_leases VALUES ('foreign-profile','competing-writer',?)", (os.getpid(),))
        elif boundary == 'incomplete-backup': (case.closed / 'happyranch.db').write_bytes(b'incomplete retained backup')
        else:
            with sqlite3.connect(case.root / 'happyranch.db') as conn:
                conn.execute("UPDATE tasks SET note='real later traffic fixture' WHERE id='TASK-C6-HISTORY'")
        before = utility.preservation_inventory(case.root); rows = case.rows()
        refused, syscalls, frames = case.observed(case.apply_arguments('complete'), 'third-state-refusal')
        assert refused.returncode == 1 and 'refused:' in refused.stderr, refused.stderr
        case.no_effects(syscalls, frames)
        assert utility.preservation_inventory(case.root) == before and case.rows() == rows


@pytest.mark.parametrize('refusal', ['missing-direction', 'missing-operation', 'wrong-digest',
                                   'design-plan-not-manifest', *C9_M_CASES], ids=lambda value: '-'.join(value) if isinstance(value, tuple) else value)
def test_c9_crash_recovery_and_replay(runtime: Path, tmp_path: Path, refusal: str, request: pytest.FixtureRequest) -> None:
    """Retained L command refusals plus externally held real M source selections.

    The original argument controls do not attest migration. New M process cuts
    require the real positive check and complete paired observers. Guest reboot
    and successful transient proof still require independent M capability.
    The distinct C8 risk is apply/check admission; C9 must reject recovery's
    absent direction/operation without treating a prior plan as owned state.
    """
    if isinstance(refusal, tuple):
        _c9_maintenance_selection(request.getfixturevalue('maintenance_case'), refusal)
        return
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


C7_ADVICE = ('Advise the founder and HappyRanch teams on product, services and business operations. '
             'You are an individual consultant reporting directly to the founder.')
C7_SKILLS = {'start-task', 'jobs', 'thread', 'dream', 'todos', 'workspace-cleanup'}


def _c7_wait(probe: Callable[[], object], *, label: str, seconds: float = 150) -> object:
    """Wait only on absent/in-flight evidence; assertion/errors never retry."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = probe()
        if value:
            return value
        time.sleep(0.05)
    pytest.fail('C7 observation deadline: ' + label)


def _c7_launch_contract(actual: dict, binding: dict, runtime: Path, baseline: dict) -> None:
    from runtime.orchestrator.agent_def import parse_agent_text
    source = Path(binding['source'])
    agent, provider = actual['agent'], actual['provider']
    assert actual['source_sha'] == binding['revision']
    assert actual['workspace'] == str(runtime / 'workspaces' / agent)
    assert provider == ('claude' if agent == 'consultant_head' else 'codex')
    assert actual['stub_path'] == binding['stubs'][provider]['path']
    assert actual['stub_sha256'] == binding['stubs'][provider]['sha256']
    assert actual['helper_sha256'] == hashlib.sha256((source / 'tests/helpers/human_team_context_plan.py').read_bytes()).hexdigest()
    assert actual['plan_sha256'] == hashlib.sha256(Path(actual['plan_path']).read_bytes()).hexdigest()
    assert actual['callback_sha256'] == binding['callback_sha256']
    assert Path(actual['python_path']).resolve() == Path(binding['python']).resolve()
    for rel, digest in actual['native_source_sha256'].items():
        assert digest == hashlib.sha256((source / rel).read_bytes()).hexdigest()
    assert actual['execution_environment']['HOME'] == str(Path(binding['root']) / 'home')
    assert actual['execution_environment']['PATH'] == os.pathsep.join((str(Path(binding['root']) / 'bin'), '/usr/bin', '/bin'))
    assert actual['prompt_sha256'] == hashlib.sha256(actual['prompt'].encode()).hexdigest()
    definition = parse_agent_text(actual['definition_bytes'], expected_name=agent)
    assert (definition.name, definition.team, definition.role, definition.executor, definition.allow_rules, definition.repos) == (
        agent, 'default', 'worker', provider, (), {})
    assert definition.system_prompt == C7_ADVICE + ('\n\n## Routine Tasks\n- C7 own routine' if baseline['wake'] else '')
    files = actual['generated_files']
    assert files['CLAUDE.md']['raw_link'] == 'AGENTS.md'
    assert files['CLAUDE.md']['text'] == files['AGENTS.md']['text']
    assert 'the task-owner `decision` scope' in files['AGENTS.md']['text']
    assert 'Worker-owned roots use only server-authorized self decisions;' in files['AGENTS.md']['text']
    assert 'manager-only operations remain restricted.' in files['AGENTS.md']['text']
    assert 'the manager-only `decision` block' not in files['AGENTS.md']['text']
    # Complete output equality to the prelaunch worker materialization is a
    # continuity oracle, supplemented by literal supported worker expectations.
    assert files == baseline['files']
    assert files['AGENTS.md']['text'].startswith('# Agent: ' + agent + '\n\n## System Prompt\n\n' + definition.system_prompt + '\n')
    if provider == 'claude':
        assert json.loads(files['.claude/settings.json']['text']) == {
            'permissions': {'allow': ['Bash(happyranch:*)']}, 'hooks': {}}
        assert actual['stub_argv'] == ['-p', '--permission-mode', 'auto', '--allowedTools',
                                       'Bash(happyranch *)', '--output-format', 'json']
    else:
        assert '.claude/settings.json' not in files
        assert actual['stub_argv'] == ['exec', '--sandbox', 'workspace-write', '-c',
            'sandbox_workspace_write.network_access=true', '--skip-git-repo-check', '--json', '-']
    assert 'obsolete-' not in json.dumps(actual['stub_argv'])
    for root in ('.agents/skills', '.claude/skills'):
        exposed = actual['skill_manifests'][root]
        assert set(exposed) == C7_SKILLS
        for slug, item in exposed.items():
            bundled = source / 'runtime/skills/bundled' / slug
            expected = {str(p.relative_to(bundled)): hashlib.sha256(p.read_bytes()).hexdigest()
                        for p in sorted(bundled.rglob('*')) if p.is_file()}
            assert item['members'] == expected
        assert exposed == baseline['skills'][root]
    # Inspect the actual emitted canonical skill bytes in BOTH provider roots.
    for root in ('.agents/skills', '.claude/skills'):
        skill = Path(actual['skill_manifests'][root]['start-task']['target']) / 'SKILL.md'
        raw = skill.read_bytes()
        assert hashlib.sha256(raw).hexdigest() == actual['skill_manifests'][root]['start-task']['members']['SKILL.md']
        guidance = raw.decode()
        assert 'An ordinary worker root may use `done`, self-only `delegate` or `escalate`' in guidance
        assert "a decision-owning root's request for founder intervention" in guidance
        assert 'An attempted non-root founder escalation fails the child and wakes' in guidance
        assert 'manager-only request for founder intervention' not in guidance
    # Generic guidance conditionally mentioning managers is shared with workers.
    # Positive manager grants in either the ACTUAL prompt or system body refuse.
    authority = actual['prompt'] + '\n' + files['AGENTS.md']['text']
    for forbidden in ('Team Head', '### Available Agents', 'You can also delegate work to your team.',
                      '## [RESERVED] Active Team Escalation Policy', '<!-- BEGIN HAPPYRANCH ACTIVE TEAM POLICY -->',
                      'happyranch manage-agent', 'happyranch workflows templates publish',
                      'You are the Consultant Head. Analyze the brief above'):
        assert forbidden not in authority, forbidden
    assert not {'manage-agent', 'manage-repo', 'workflow-template'} & set(actual['skill_manifests']['.agents/skills'])
    if actual['kind'] == 'task':
        assert actual['runtime_session_hint'] == actual['identity']['session_id']
        if actual['stage'] in ('self-delegate', 'current-after-stale'):
            assert 'you may only delegate sub-tasks to yourself.' in actual['prompt']
            assert 'NOT available in self-only mode.' in actual['prompt']
            assert '**done** -- the whole task is complete:' in actual['prompt']
            assert '**escalate** -- needs founder attention:' in actual['prompt']
            assert '{"action": "escalate", "reason": "<why>"}' in actual['prompt']
    elif actual['kind'] == 'schedule':
        assert f'You are {agent} (worker) on the default team' in actual['prompt']
        # This shipping runner has no runtime session hint. Preserve the empty
        # contract rather than inventing one from schedule/provider identity.
        assert actual['runtime_session_hint'] == ''
    else:
        if actual['kind'] == 'wake':
            assert f'You are {agent} (worker) on the default team' in actual['prompt']
        assert actual['runtime_session_hint'].startswith('sess-')
    assert actual['runtime_session_hint'] != 'c7-' + provider + '-' + str(actual['provider_pid'])


def _c7_runner_exit(conn: sqlite3.Connection, actual: dict, exits: list[dict]) -> bool:
    owned = [row for row in exits if row.get('kind') == 'context_provider_exit'
             and row['pid'] == actual['provider_pid']]
    if not owned:
        return False
    assert len(owned) == 1
    exit_row = owned[0]
    assert exit_row['status'] == 0
    for key in ('source_sha', 'stub_sha256', 'plan_sha256'):
        assert exit_row[key] == actual[key]
    # This evidence is persisted by the runner AFTER waiting for the provider
    # and supervised terminal cleanup, independently of callback or exit trap.
    scope = {'task': 'task', 'thread': 'thread', 'dream': 'dream', 'wake': 'work_hour', 'schedule': 'schedule'}[actual['kind']]
    identity = actual['identity']
    scope_id = identity.get('task_id') or identity.get('thread_id') or identity['context_id']
    session = actual['runtime_session_hint'] if actual['kind'] in ('task', 'thread') else exit_row['provider_session_id']
    usage = conn.execute('SELECT agent,executor,session_id,input_tokens FROM session_token_usage WHERE scope_type=? AND scope_id=? AND session_id=?',
                         (scope, scope_id, session)).fetchall()
    if not usage:
        return False
    assert usage == [(actual['agent'], actual['provider'], session, 1000 if actual['provider'] == 'claude' else 1850)]
    try:
        os.kill(actual['provider_pid'], 0)
    except ProcessLookupError:
        return True
    return False


@pytest.mark.parametrize('agent,kind', CONTEXTS, ids=[
    ('head' if agent == 'consultant_head' else 'codex') + '-' + kind for agent, kind in CONTEXTS])
def test_c7_both_resume_resets_and_worker_contexts(
    runtime: Path, request: pytest.FixtureRequest, tmp_path: Path, agent: str, kind: str,
    fake_claude_plan_env: Path, fake_codex_plan_env: Path,
    fake_claude_thread_plan_env: Path,
) -> None:
    """L-only real context proof; no already-human fixture is called migration.

    A combined after-M claim requires real C6 utility --check/exact-digest apply,
    compatible readback and genuine operation receipt in the same supported M
    fixture BEFORE release and these launches. M remains unprovisioned; this L
    seed/reset does not satisfy that prerequisite or replace C6/C9/U9 oracles.
    """
    from dataclasses import replace
    from runtime.config import Settings
    from runtime.infrastructure.database import Database
    from runtime.infrastructure.learnings_store import MemoryItem, MemoryStore
    from runtime.models import ScheduleKind, ThreadMessageKind, ThreadRecord, ThreadStatus
    from runtime.orchestrator._paths import OrgPaths
    from runtime.orchestrator.agent_def import parse_agent_text, render_agent_text
    from runtime.orchestrator.context_builder import ContextBuilder
    from runtime.orchestrator.schedule_service import ScheduleService
    from runtime.orchestrator.workspace_adapters import materialize_workspace_skills
    from tests.helpers.human_team_context_plan import _outer_identity
    from tests.helpers.integration_stub_guard.guard import manifest
    binding = manifest()
    _seed_human_roster(runtime)
    retained_memory = {}
    baseline = {}
    for name in ('consultant_head', 'consultant_codex'):
        definition_path = runtime / 'org/agents' / (name + '.md')
        original = parse_agent_text(definition_path.read_text(), expected_name=name)
        wake = kind == 'wake' and name == agent
        body = C7_ADVICE + ('\n\n## Routine Tasks\n- C7 own routine' if wake else '')
        definition_path.write_text(render_agent_text(replace(original, system_prompt=body)))
        workspace = runtime / 'workspaces' / name
        # Independent retained memory DATA, not synthetic launch/callback facts.
        (workspace / 'learnings.md').write_text('C7 retained runtime memory\n')
        memory_store = MemoryStore(workspace / 'memory')
        memory_store.write_entry(MemoryItem(id=memory_store.next_id(), slug='c7-retained-knowledge',
            title='Retained C7 worker knowledge', topic='fixture', body='C7 retained structured memory\n'), agent=name)
        memory_store.regenerate_index()
        provider_memory = workspace / '.provider-memory'
        provider_memory.mkdir()
        (provider_memory / 'prior-state').write_bytes(b'C7 retained provider knowledge')
        # Real provider memory conventions in this owned HOME; fixture bytes
        # remain DATA and are never exposed in the evidence or provider prompt.
        home = Path(binding['root']) / 'home'
        native_memory = (home / '.claude/projects' / str(workspace).replace('/', '-') / 'memory/MEMORY.md'
                         if original.executor == 'claude' else home / '.codex/memories/memory_summary.md')
        native_memory.parent.mkdir(parents=True, exist_ok=True)
        native_memory.write_bytes(b'C7 retained native provider memory')
        retained_memory[native_memory] = (native_memory.resolve(), native_memory.read_bytes())
        ContextBuilder(Settings(), OrgPaths(root=runtime), slug=runtime.name).ensure_workspace_ready(
            workspace, name, body, provider=original.executor)
        # Native baseline materialization is fixture setup, not a launch. The
        # unchanged runtime repeats its own integrity/materialization at launch.
        materialize_workspace_skills(workspace, Settings(project_root=Path(binding['source'])),
            slug=runtime.name, context='bootstrap', provider=original.executor, agent_name=name,
            team='default', skills_root=Path(binding['source']) / 'runtime/skills', org_root=runtime)
        for path in [workspace / 'learnings.md', *sorted((workspace / 'memory').rglob('*')),
                     *sorted(provider_memory.rglob('*'))]:
            if path.is_file():
                retained_memory[path] = (path.resolve(), path.read_bytes())
        files = {}
        for rel in ('AGENTS.md', 'CLAUDE.md', '.claude/settings.json', 'opencode.json'):
            path = workspace / rel
            if path.exists():
                files[rel] = {'text': path.read_text(), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                              'raw_link': os.readlink(path) if path.is_symlink() else None}
        baseline[name] = {'wake': wake, 'files': files, 'skills': {}}
        assert Settings().permission_mode == 'auto' and Settings().codex_sandbox_mode == 'workspace-write'
        for root in ('.agents/skills', '.claude/skills'):
            baseline[name]['skills'][root] = {p.name: {'raw_link': os.readlink(p), 'target': str(p.resolve()),
                'members': {str(m.relative_to(p.resolve())): hashlib.sha256(m.read_bytes()).hexdigest()
                            for m in sorted(p.resolve().rglob('*')) if m.is_file()}}
                for p in sorted((workspace / root).iterdir())}
    attached = _attach_process(runtime)
    assert attached.returncode == 0, attached.stderr
    db = Database(runtime / 'happyranch.db')
    now = datetime.now(timezone.utc)
    controls = ('THR-001', 'THR-002')
    for control in controls:
        db.insert_thread(ThreadRecord(id=control, subject='retained cooldown fixture DATA',
                                      status=ThreadStatus.ARCHIVED, archived_at=now))
        for index in range(7):
            db.append_thread_message(thread_id=control, speaker='founder', kind=ThreadMessageKind.MESSAGE,
                body_markdown='Retained frozen history DATA ' + str(index))
        for name, provider in (('consultant_head', 'claude'), ('consultant_codex', 'codex'), ('dev_agent', 'claude')):
            db._conn.execute('INSERT INTO thread_participants VALUES (?,?,?,?,?,?)',
                (control, name, now.isoformat(), 'founder', 'obsolete-' + name + control, 7))
            db._conn.execute('''INSERT INTO thread_reply_delivery_state
                (thread_id,agent_name,acknowledged_through_seq,required_through_seq,updated_at)
                VALUES (?,?,?,?,?)''', (control, name, 7, 9, now.isoformat()))
            episode = 'retained-' + name + control
            token = 'retained-history-' + name + control
            db._conn.execute('''INSERT INTO thread_reply_breaker_episodes
                (thread_id,agent_name,executor_key,episode_id,state,consecutive_failures,
                 opened_at,cooldown_until,last_failure_category,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)''',
                (control, name, provider, episode, 'open', 3, now.isoformat(),
                 (now + timedelta(days=1)).isoformat(), 'provider_failure', now.isoformat()))
            db._conn.execute('INSERT INTO thread_reply_breaker_receipts VALUES (?,?,?,?,?)',
                             (token, episode, 'failure', 'provider_failure', now.isoformat()))
            db._conn.execute('''INSERT INTO thread_invocations
                (thread_id,agent_name,invocation_token,triggering_seq,purpose,status,enqueued_at,consumed_at)
                VALUES (?,?,?,?,?,?,?,?)''', (control, name, token, 7, 'reply', 'failed', now.isoformat(), now.isoformat()))
        db._conn.execute('''INSERT INTO thread_reply_exchange
            (thread_id,exchange_id,state,open_seq,close_seq,opened_at,last_activity_at,closed_at,close_reason,deferred_count)
            VALUES (?,?,?,?,?,?,?,?,?,?)''', (control, 1, 'released', 1, 7, now.isoformat(), now.isoformat(), now.isoformat(), 'quiescence', 1))
        db._conn.execute('''INSERT INTO thread_exchange_deferrals
            (thread_id,exchange_id,agent_name,state,created_at,released_at,mint_token_prefix)
            VALUES (?,?,?,?,?,?,?)''', (control, 1, 'consultant_codex', 'released', now.isoformat(), now.isoformat(), 'retained-frozen-' + control))
    eligible_thread = None
    if kind == 'thread':
        eligible_thread = 'THR-003'
        db.insert_thread(ThreadRecord(id=eligible_thread, subject='eligible full context'))
        for index in range(7):
            db.append_thread_message(thread_id=eligible_thread, speaker='founder', kind=ThreadMessageKind.MESSAGE,
                body_markdown='C7-EARLY-CONTEXT-MARKER' if index == 0 else f'retained context {index}')
        db._conn.execute('INSERT INTO thread_participants VALUES (?,?,?,?,?,?)',
            (eligible_thread, agent, now.isoformat(), 'founder', 'obsolete-' + agent, 7))
        db._conn.execute('''INSERT INTO thread_reply_delivery_state
            (thread_id,agent_name,acknowledged_through_seq,required_through_seq,updated_at)
            VALUES (?,?,?,?,?)''', (eligible_thread, agent, 7, 7, now.isoformat()))
    db._conn.commit()
    preserved_tables = ('threads', 'thread_messages', 'thread_invocations', 'thread_reply_delivery_state',
        'thread_reply_breaker_episodes', 'thread_reply_breaker_receipts', 'thread_reply_exchange',
        'thread_exchange_deferrals', 'escalation_notifications')
    before = {table: [tuple(row) for row in db._conn.execute(f'SELECT * FROM {table} ORDER BY rowid')]
              for table in preserved_tables}
    participants = [tuple(row) for row in db._conn.execute('SELECT * FROM thread_participants ORDER BY rowid')]
    reset_receipts = []
    for name in ('consultant_head', 'consultant_codex'):
        expected_rows = 3 if name == agent and kind == 'thread' else 2
        assert db.reset_thread_sessions_for_agent(name, audit_scope_id='config:human-team-roster:C7',
            audit_agent='founder', audit_reason='L fixture continuity reset; not M demotion') == expected_rows
        # Each per-agent commit is inspected from a separate connection. The
        # second agent remains unreset until its own audited helper commits.
        with sqlite3.connect(runtime / 'happyranch.db') as observer:
            observed = observer.execute('SELECT * FROM thread_participants ORDER BY rowid').fetchall()
            updated = {item['name'] for item in reset_receipts} | {name}
            assert observed == [row[:4] + ((None, 0) if row[1] in updated else row[4:]) for row in participants]
            invalidations = observer.execute("SELECT payload FROM audit_log WHERE task_id=? AND action='thread_session_invalidated' ORDER BY id",
                                           ('config:human-team-roster:C7',)).fetchall()
            expected = reset_receipts + [{'reason': 'L fixture continuity reset; not M demotion', 'rows': expected_rows, 'name': name}]
            assert [json.loads(row[0]) for row in invalidations] == expected
            assert {table: observer.execute(f'SELECT * FROM {table} ORDER BY rowid').fetchall()
                    for table in preserved_tables} == before
            reset_receipts = expected
    # Independent two-owner oracle: omitting either reset cannot make its
    # loop-derived receipt list become the expected successful contract.
    reset_participants = [row[:4] + ((None, 0) if row[1] in ('consultant_head', 'consultant_codex') else row[4:])
                          for row in participants]
    with sqlite3.connect(runtime / 'happyranch.db') as observer:
        assert observer.execute('SELECT * FROM thread_participants ORDER BY rowid').fetchall() == reset_participants
        assert [json.loads(row[0]) for row in observer.execute(
            "SELECT payload FROM audit_log WHERE task_id=? AND action='thread_session_invalidated' ORDER BY id",
            ('config:human-team-roster:C7',))] == [
                {'reason': 'L fixture continuity reset; not M demotion',
                 'rows': 3 if name == agent and kind == 'thread' else 2, 'name': name}
                for name in ('consultant_head', 'consultant_codex')]
    stable_timezone = 'UTC' if now.hour == 12 else f'Etc/GMT{now.hour - 12:+d}'
    if kind == 'dream':
        db.upsert_org_setting('dreaming', json.dumps({'enabled': True,
            'schedule': {'time': '00:00', 'timezone': stable_timezone, 'catch_up_on_startup': True},
            'agents': {'mode': 'whitelist', 'include': [agent]}}))
    if kind == 'wake':
        db.upsert_org_setting('working_hours', json.dumps({'enabled': True,
            'agents': {'mode': 'whitelist', 'include': [agent]},
            'default': {'mode': 'continuous', 'interval': '24h', 'timezone': stable_timezone, 'catch_up_on_startup': True}}))
    schedule_id = None
    if kind == 'schedule':
        schedule_id = ScheduleService(db).create(agent_name=agent, team='default', kind=ScheduleKind.ONE_SHOT,
            fire_at=now + timedelta(seconds=1), recurrence=None, timezone='UTC', normalized_brief='C7 own scheduled root',
            source_instruction='explicit isolated fixture one-shot').id
    db.close()
    capture = tmp_path / 'actual-contexts.jsonl'
    helper = Path(binding['source']) / 'tests/helpers/human_team_context_plan.py'
    import shlex
    for plan, provider in ((fake_claude_plan_env, 'claude'), (fake_claude_thread_plan_env, 'claude'), (fake_codex_plan_env, 'codex')):
        plan.write_text('#!/usr/bin/env bash\nset -euo pipefail\npython ' + shlex.quote(str(helper)) +
            ' --provider ' + provider + ' --capture ' + shlex.quote(str(capture)) +
            (' --stale-task-root' if kind == 'task' else '') + '\n')
    base = _base(request.getfixturevalue('live_daemon'))
    task_id = None
    if kind == 'task':
        task_id = httpx.post(base + '/tasks', headers=_auth_headers(), json={
            'owner': agent, 'team': 'default', 'brief': 'C7 actual worker root'}).raise_for_status().json()['task_id']
        assert _wait_for_terminal(base, task_id)['task']['status'] == 'completed'
    elif kind == 'thread':
        httpx.post(base + f'/threads/{eligible_thread}/send', headers=_auth_headers(), json={
            'body_markdown': 'C7 actual founder sends next message'}).raise_for_status()
    def captured() -> list[dict] | None:
        if not capture.exists():
            return None
        rows = [json.loads(line) for line in capture.read_text().splitlines()]
        matching = [row for row in rows if row['kind'] == kind and row['agent'] == agent]
        if not matching:
            return None
        assert all(row['callback_exit'] == 0 for row in matching), matching
        return rows
    records = _c7_wait(captured, label='actual ' + kind + ' callback')
    actual = next(row for row in records if row['kind'] == kind and row['agent'] == agent)
    with sqlite3.connect(runtime / 'happyranch.db') as conn:
        if kind in ('dream', 'wake', 'schedule'):
            table = {'dream': 'dreams', 'wake': 'work_hours', 'schedule': 'schedules'}[kind]
            context_id = actual['identity']['context_id']
            def context_terminal() -> dict | None:
                conn.row_factory = sqlite3.Row
                values = conn.execute('SELECT * FROM ' + table + ' WHERE id=?', (context_id,)).fetchall()
                conn.row_factory = None
                if not values or values[0]['status'] in ('pending', 'running', 'firing'):
                    return None
                assert len(values) == 1 and values[0]['status'] == ('fired' if kind == 'schedule' else 'completed'), dict(values[0])
                return dict(values[0])
            value = _c7_wait(context_terminal, label='terminal ' + kind, seconds=30)
            assert value['agent_name'] == agent and value['error'] is None
            assert conn.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0] == 1
            transcript = Path(value['transcript_path'])
            if not transcript.is_absolute():
                transcript = runtime / transcript
            assert transcript.is_file()
            text = transcript.read_text()
            assert agent in text and context_id in text
            summary = {'dream': 'C7 private worker reflection', 'wake': 'C7 current worker wake', 'schedule': 'C7 actual one-shot fire'}[kind]
            assert summary in text
            if kind == 'dream':
                assert value['ended_at'] and value['summary'] == summary
                assert value['new_learnings_count'] == value['kb_candidate_count'] == 0
                assert value['founder_thread_id'] is None
                assert conn.execute('SELECT COUNT(*) FROM tasks').fetchone()[0] == 0
                assert conn.execute('SELECT COUNT(*) FROM dream_kb_candidates').fetchone()[0] == 0
            else:
                spawned = json.loads(value['spawned_task_ids'])
                assert len(spawned) == 1
                task_id = spawned[0]
                assert task_id in text
                if kind == 'schedule':
                    assert context_id == schedule_id and value['active'] == 0 and value['fire_count'] == 1
                    assert value['session_id'] is None and value['last_fired_at']
                else:
                    assert value['ended_at'] and value['spawned_task_count'] == 1 and value['summary'] == summary
        if task_id is not None:
            assert _wait_for_terminal(base, task_id)['task']['status'] == 'completed'
            records = [json.loads(line) for line in capture.read_text().splitlines()]
            tasks = conn.execute('SELECT id,parent_task_id,assigned_agent,team,status FROM tasks ORDER BY id').fetchall()
            assert len(tasks) == (2 if kind == 'task' else 1)
            assert all(row[2:] == (agent, 'default', 'completed') for row in tasks)
            assert [row[0] for row in tasks if row[1] is None] == [task_id]
            task_records = [row for row in records if row['kind'] == 'task']
            results = conn.execute('SELECT id,task_id,agent,session_id FROM task_results ORDER BY id').fetchall()
            assert len(results) == len(task_records) == (3 if kind == 'task' else 1)
            assert all(type(row[0]) is int and row[0] > 0 for row in results)
            assert conn.execute("SELECT COUNT(*) FROM task_results WHERE status!='completed'").fetchone()[0] == 0
            assert {(row[1], row[2], row[3]) for row in results} == {
                (row['identity']['task_id'], agent, row['identity']['session_id']) for row in task_records}
            if kind == 'task':
                stale_records = [row for row in records if row['stage'] == 'current-after-stale']
                assert len(stale_records) == 1
                stale = stale_records[0]['stale_control']
                assert stale['earlier']['task_id'] == stale['current']['task_id'] == task_id
                assert stale['earlier']['session_id'] != stale['current']['session_id']
                assert stale['exit'] == 1 and stale['before_sha256'] == stale['after_sha256']
                assert stale['stdout'].strip() == f"Session id mismatch — daemon expected {stale['current']['session_id']} but got {stale['earlier']['session_id']}."
                assert conn.execute('SELECT current_session_id FROM tasks WHERE id=?', (task_id,)).fetchone() == (stale['current']['session_id'],)
        if kind == 'thread':
            token = actual['identity']['invocation_token']
            assert actual['identity']['thread_id'] == eligible_thread
            assert 'C7-EARLY-CONTEXT-MARKER' in actual['prompt']
            assert 'Full message history follows.' in actual['prompt']
            invocation = conn.execute('SELECT status,agent_name,triggering_seq,reply_message_seq FROM thread_invocations WHERE invocation_token=?', (token,)).fetchone()
            assert invocation and invocation[:3] == ('consumed', agent, 8), invocation
            assert invocation[3] == 9
            assert conn.execute('SELECT speaker,body_markdown FROM thread_messages WHERE thread_id=? AND seq=?',
                                (eligible_thread, 9)).fetchone() == (agent, 'C7 genuine current worker reply')
            assert conn.execute('SELECT COUNT(*) FROM thread_invocations WHERE thread_id=?', (eligible_thread,)).fetchone()[0] == 1
            assert conn.execute('SELECT COUNT(*) FROM thread_messages WHERE thread_id=?', (eligible_thread,)).fetchone()[0] == 9
        witness = Path(os.environ['HAPPYRANCH_TEST_WITNESS_DIR']) / 'identities.jsonl'
        def runner_settled() -> bool:
            exits = [json.loads(line) for line in witness.read_text().splitlines()] if witness.exists() else []
            return all(_c7_runner_exit(conn, row, exits) for row in records)
        _c7_wait(runner_settled, label='all actual provider exits and post-exit runner usage', seconds=30)
        for row in records:
            _c7_launch_contract(row, binding, runtime, baseline[agent])
        assert len(records) == (3 if kind == 'task' else 2 if kind in ('wake', 'schedule') else 1)
        assert conn.execute('SELECT COUNT(*) FROM tasks').fetchone()[0] == (2 if kind == 'task' else 1 if kind in ('wake', 'schedule') else 0)
        assert conn.execute('SELECT COUNT(*) FROM task_results').fetchone()[0] == (3 if kind == 'task' else 1 if kind in ('wake', 'schedule') else 0)
        for other, table in (('dream', 'dreams'), ('wake', 'work_hours'), ('schedule', 'schedules')):
            assert conn.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0] == (1 if kind == other else 0)
        # Once post-exit runner facts exist, separately inspect the thread's
        # legitimate delivery settlement; reset's zero-effects assertion does
        # not apply to this genuine new reply.
        if kind == 'thread':
            _c7_wait(lambda: conn.execute('SELECT acknowledged_through_seq,required_through_seq,running_invocation_token,queued_invocation_token FROM thread_reply_delivery_state WHERE thread_id=? AND agent_name=?',
                (eligible_thread, agent)).fetchone() == (8, 8, None, None), label='owned reply delivery settlement', seconds=30)
            _c7_wait(lambda: conn.execute('SELECT agent_session_id,last_resumed_seq FROM thread_participants WHERE thread_id=? AND agent_name=?',
                (eligible_thread, agent)).fetchone() == ('c7-' + actual['provider'] + '-' + str(actual['provider_pid']), 8),
                label='owned provider resume state after runner exit', seconds=30)
        for table in preserved_tables:
            # Ignore ONLY the expected eligible-thread changes, never archived
            # cooldown/frozen/notification facts or new unrelated artifacts.
            rows = conn.execute('SELECT * FROM ' + table + ' ORDER BY rowid')
            columns = [column[0] for column in rows.description]
            values = rows.fetchall()
            if kind == 'thread' and 'thread_id' in columns:
                index = columns.index('thread_id')
                assert [row for row in values if row[index] != eligible_thread] == [row for row in before[table] if row[index] != eligible_thread]
            elif kind == 'thread' and table == 'threads':
                assert [row for row in values if row[0] != eligible_thread] == [row for row in before[table] if row[0] != eligible_thread]
            elif kind == 'thread' and table == 'thread_reply_breaker_receipts':
                assert [row for row in values if row[0] != token] == before[table]
            else:
                assert values == before[table]
        assert conn.execute('SELECT * FROM thread_participants WHERE agent_name=? ORDER BY rowid', ('dev_agent',)).fetchall() == [row for row in participants if row[1] == 'dev_agent']
        assert conn.execute('SELECT * FROM thread_participants WHERE thread_id IN (?,?) ORDER BY rowid', controls).fetchall() == [row for row in reset_participants if row[0] in controls]
    # Parser refusal controls are source assertions, not independently observed
    # callback evidence. Only the shipping CLI/DB path above proves settlement.
    prompt = actual['prompt']
    if kind == 'task':
        line, field = '  session_id: ' + actual['identity']['session_id'], 'session'
        wrong = '  session_id: sess-0000'
        ignored = '\nSkill example:\n  task_id: TASK-999999\n  session_id: sess-0000\n'
    elif kind == 'thread':
        line, field = 'Your invocation_token for this turn is: ' + actual['identity']['invocation_token'], 'invocation_token'
        wrong = 'Your invocation_token for this turn is: 0000'
        # Examples INSIDE history cannot substitute for the outer callback ID.
        ignored = ''
        amended = prompt.replace('C7-EARLY-CONTEXT-MARKER', 'C7-EARLY-CONTEXT-MARKER\n' + wrong)
        assert _outer_identity(amended, runtime.name) == (kind, actual['identity'])
    else:
        line = next(value for value in prompt.splitlines() if value.startswith('happyranch ' + {'dream': 'dreams complete', 'wake': 'work-hours spawn', 'schedule': 'schedules spawn'}[kind]))
        field = kind + '_callback_id'
        wrong = line.replace(actual['identity']['context_id'], 'WRONG-ID')
        ignored = '\nSkill example:\n' + wrong + '\n'
    for label, changed in (('missing', prompt.replace(line + '\n', '')),
                           ('duplicate', prompt.replace(line, line + '\n' + line)),
                           ('conflicting', prompt.replace(line, line + '\n' + wrong))):
        with pytest.raises(ValueError, match='^' + label + '_' + field + '$'):
            _outer_identity(changed, runtime.name)
    assert _outer_identity(prompt + ignored, runtime.name) == (kind, actual['identity'])
    assert {path: (path.resolve(), path.read_bytes()) for path in retained_memory} == retained_memory
    (tmp_path / 'C7-context-receipt.json').write_text(json.dumps({
        'source_sha': binding['revision'], 'context': kind, 'agent': agent, 'captured_launches': records,
        'L_context_only': True, 'per_agent_reset_audits': reset_receipts,
        'retained_memory_sha256': {str(path): hashlib.sha256(raw).hexdigest()
                                   for path, (_, raw) in retained_memory.items()},
        'M_utility_reset_proof': 'HELD: C6 real check/apply/receipt same disposable fixture required; not established by L'}, sort_keys=True))



def _c4_failed_drain_reentry(request: pytest.FixtureRequest, port: int, root: Path,
                           parent_id: str, verdict: str | None) -> None:
    """Finite native exception/cancellation -> actual same-result consumer.

    AUTHORED ONLY: executable L, pre-fix RED/final GREEN and five isolated/sibling
    repetitions remain HELD. Native job/callback/DB boundaries are never mocked.
    """
    owned = request.node._roster_fault_daemon
    witness = owned['witness']
    def record(event: str) -> dict:
        path = witness.with_name(witness.name + '.' + event)
        deadline = time.monotonic() + 30
        while not path.exists() and time.monotonic() < deadline:
            assert owned['process'].poll() is None, 'native drain daemon exited'
            time.sleep(0.02)
        assert path.exists(), f'missing actual native drain observation: {event}'
        row = json.loads(path.read_text())
        assert row['pid'] == owned['process'].pid
        from tests.helpers.integration_stub_guard.guard import manifest
        binding = manifest()
        assert row['source_sha'] == binding['revision']
        assert row['file_sha256'] == hashlib.sha256(
            (Path(binding['source']) / 'runtime/orchestrator/run_step.py').read_bytes()).hexdigest()
        return row
    pending = record('drain-pending-join')
    fault = record('native-drain-fault')
    finished = record('consumer-finished')
    joined = record('drain-joined-finished')
    identity = [finished[key] for key in ('task', 'agent', 'origin', 'session', 'result')]
    assert identity[1] == 'consultant_codex' and identity[2] != identity[3]
    assert type(identity[4]) is int and identity[4] > 0
    for row in (pending, fault, joined):
        assert [row[key] for key in ('task', 'agent', 'origin', 'session', 'result')] == identity
    assert pending['pending'] is True
    assert finished['disposition'] == joined['disposition'] == 'recovery_required'
    assert finished['phase'] == 'drain_jobs' and finished['key'] == identity
    assert fault['fault'] == ('exception' if request.node.callspec.params['cut'] == C4_DRAIN_CUTS[0] else 'cancel')
    assert len(pending['controls']) == len(fault['controls']) == 1
    assert fault['controls'] == pending['controls']
    control = fault['controls'][0]
    assert control['same_control'] is True and control['returncode'] is None
    assert type(control['pid']) is int and control['pid'] > 0
    os.kill(control['pid'], 0)  # native-control witness, liveness only

    def snapshot() -> dict:
        with sqlite3.connect(root / 'happyranch.db') as observer:
            return {
                'selected': observer.execute('SELECT * FROM task_results WHERE id=?', (identity[4],)).fetchone(),
                'child': observer.execute('SELECT status,cancelled_at,note,current_session_id,parent_task_id,zombie_flagged_at FROM tasks WHERE id=?', (identity[0],)).fetchone(),
                'ledger': observer.execute('SELECT origin_session_id,recovery_session_id,accepted_result_id,state FROM task_completion_recoveries WHERE task_id=?', (identity[0],)).fetchone(),
                'reviews': observer.execute("SELECT id,task_id,agent,action,payload,timestamp FROM audit_log WHERE task_id=? AND action='review_verdict' ORDER BY id", (identity[0],)).fetchall(),
                'bookkeeping': observer.execute("SELECT * FROM audit_log WHERE task_id=? AND action='zombie_cleared'", (identity[0],)).fetchall(),
                'prior_jobs': observer.execute("SELECT * FROM jobs WHERE task_id=? AND title='C4-prior'", (identity[0],)).fetchall(),
                'jobs': observer.execute("SELECT id,status,reason,stdout_path,stderr_path,finished_at FROM jobs WHERE task_id=? AND title='C4-running'", (identity[0],)).fetchall(),
                'parent': observer.execute('SELECT status,block_kind FROM tasks WHERE id=?', (parent_id,)).fetchone(),
                'parent_results': observer.execute('SELECT * FROM task_results WHERE task_id=? ORDER BY id', (parent_id,)).fetchall(),
            }
    before = snapshot()
    assert before['selected'] is not None
    assert before['child'] == ('failed', None, 'self-blocked: child outcome', identity[3], parent_id, pending['zombie_flag'])
    assert before['ledger'] == (identity[2], identity[3], identity[4], 'callback_consumed')
    assert before['parent'] == ('in_progress', 'delegated') and len(before['parent_results']) == 1
    assert before['bookkeeping'] == [] and len(before['reviews']) == 1
    review = before['reviews'][0]
    assert review[2] == identity[1] and json.loads(review[4]) == {
        'verdict': verdict if verdict is not None else 'rejected',
        'feedback': 'self-blocked: child outcome', 'reviewed_agent': identity[1]}
    assert len(before['prior_jobs']) == len(before['jobs']) == 1
    job = before['jobs'][0]
    assert job[:3] == (control['job'], 'failed', 'task_ended') and job[5]
    logs = {str(path): Path(path).read_bytes() for path in job[3:5]}
    assert b'owned-running' in logs[job[3]]
    # The genuine consumed selection above would return no RUNNING DB jobs.
    # It still has a live native control, so replay must not become zero-job success.
    with sqlite3.connect(root / 'happyranch.db') as observer:
        assert observer.execute("SELECT id FROM jobs WHERE task_id=? AND status='running'", (identity[0],)).fetchall() == []
    witness.with_name(witness.name + '.same-result-reentry').write_text('release actual selected reaper consumer\n')
    reentered = record('same-result-reentered')
    # Pre-fix intended RED: completed recovery_required is replaced and yields
    # done/parent/bookkeeping here. Fixture/source admission failures are not RED.
    assert reentered['disposition'] == 'recovery_required', reentered
    assert reentered['retained'] is True and reentered['phase'] == 'drain_jobs'
    assert reentered['key'] == identity and reentered['job_ids'] == [control['job']]
    assert reentered['controls'] == fault['controls']
    assert snapshot() == before
    os.kill(control['pid'], 0)
    assert {path: Path(path).read_bytes() for path in logs} == logs

    # A different genuine root/child/result must not inherit the exceptional K.
    # This traverses the same omission/callback/job/consumer pipeline; its real
    # healthy drain must produce the second parent's actual final callback.
    distinct = httpx.post(_base(port) + '/tasks', headers=_auth_headers(), json={
        'team': 'default', 'owner': identity[1], 'brief': 'distinct identity healthy drain'}).raise_for_status().json()
    assert distinct['task_id'] != parent_id
    assert _wait_for_terminal(_base(port), distinct['task_id'])['task']['status'] == 'completed'
    with sqlite3.connect(root / 'happyranch.db') as observer:
        child = observer.execute('SELECT id,status FROM tasks WHERE parent_task_id=?', (distinct['task_id'],)).fetchall()
        assert len(child) == 1 and child[0][0] != identity[0] and child[0][1] == 'failed'
        selected = observer.execute('SELECT accepted_result_id,state FROM task_completion_recoveries WHERE task_id=?', (child[0][0],)).fetchone()
        assert type(selected[0]) is int and selected[0] > 0 and selected[0] != identity[4] and selected[1] == 'callback_consumed'
        results = observer.execute('SELECT id,session_id,agent FROM task_results WHERE task_id=? ORDER BY id', (distinct['task_id'],)).fetchall()
        assert len(results) == 2 and all(type(row[0]) is int and row[0] > 0 and row[1] and row[2] == identity[1] for row in results)
        assert results[0][1] != results[1][1]
    assert snapshot() == before  # no parent effect or success-only zombie clear
    os.kill(control['pid'], 0)
    assert {path: Path(path).read_bytes() for path in logs} == logs


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
