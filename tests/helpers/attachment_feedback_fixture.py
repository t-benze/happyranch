"""Explicit closed THR304 plugin; no successful business boundary is replaced."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys

# Bind the supervisor's frozen venv before pytest/guard/runtime dependencies.
_descriptor_arg=next((arg.split('=',1)[1] for arg in sys.argv if arg.startswith('--attachment-qa-descriptor=')),None)
if _descriptor_arg is not None:
    _frozen=json.loads(Path(_descriptor_arg).read_text())
    if (sys.version_info[:2]!=(3,14) or sys.prefix!=_frozen['python_prefix']
            or sys.version!=_frozen['python_version']
            or str(Path(sys.executable).resolve())!=_frozen['python_binary']
            or hashlib.sha256(Path(sys.executable).resolve().read_bytes()).hexdigest()!=_frozen['python_sha256']):
        raise RuntimeError('THR304 frozen pre-import venv binding refused')
    os.environ['HAPPYRANCH_ATTACHMENT_FROZEN_PYTHON']=json.dumps({key:_frozen[key] for key in
        ('python','python_binary','python_prefix','python_version','python_sha256')},sort_keys=True)
    os.environ['HAPPYRANCH_ATTACHMENT_PYTHON_EVIDENCE']=_frozen['evidence']

# The launched provider is a child context, not the pytest parent. Its exact
# guard/manifest, callable source and product-injected SID/org are admitted below
# before any pytest/conftest import. Parent admission remains unchanged.
_PROVIDER_ENTRY=__name__=='__main__' and len(sys.argv)>1 and sys.argv[1] in ('provider','provider-wait','provider-supervise')
if not _PROVIDER_ENTRY:
    from tests.helpers.integration_stub_guard.guard import require_parent_environment
    require_parent_environment()

from contextlib import contextmanager
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import base64
import select
import selectors
import shlex
import signal
import shutil
import sqlite3
import stat
import subprocess
import sys
import time

from tests.helpers.integration_stub_guard.guard import manifest, validate_callback, validate_plan, validate_stub
def _provider_admission():
    # The existing source-bound stub guard already ran in the public shell.
    # Recheck its registered identities here; do not strip runtime variables to
    # pretend this actual child satisfies the stricter pytest-parent contract.
    _parent=manifest()
    if Path(__file__).resolve()!=Path(_parent['source'])/'tests/helpers/attachment_feedback_fixture.py':
        raise RuntimeError('provider callable source mismatch')
    _root=Path(_parent['root'])
    if (os.environ.get('HOME')!=str(_root/'home')
            or os.environ.get('XDG_CONFIG_HOME')!=str(_root/'config')
            or os.environ.get('PATH')!=os.pathsep.join((str(_root/'bin'),'/usr/bin','/bin'))
            or os.environ.get('XDG_CACHE_HOME')!=str(Path.cwd()/'.happyranch/cache/xdg')
            or any(key in os.environ for key in ('ANTHROPIC_API_KEY','OPENAI_API_KEY','CODEX_HOME',
                'CLAUDE_CONFIG_DIR','HAPPYRANCH_RUNTIME','HAPPYRANCH_DAEMON_TOKEN'))):
        raise RuntimeError('provider owned environment mismatch')
    _child_frozen=json.loads(os.environ['HAPPYRANCH_ATTACHMENT_FROZEN_PYTHON'])
    if (sys.prefix!=_child_frozen['python_prefix'] or sys.version!=_child_frozen['python_version']
            or os.path.abspath(sys.executable)!=_parent['python']
            or str(Path(sys.executable).resolve())!=_child_frozen['python_binary']
            or hashlib.sha256(Path(sys.executable).resolve().read_bytes()).hexdigest()!=_child_frozen['python_sha256']):
        raise RuntimeError('provider frozen interpreter drift')
    validate_callback(_parent)
    for _provider,_plan_key in (('claude','FAKE_CLAUDE_PLAN'),('codex','FAKE_CODEX_PLAN')):
        validate_stub(_provider,_parent['stubs'][_provider]['path'],_parent)
        validate_plan(os.environ[_plan_key])
    if sys.version_info[:2]!=(3,14):raise RuntimeError('provider frozen interpreter missing')

from scripts.attachment_scoped_qa import (
    CAPS, EXECUTION_OWNER, IMPLEMENTATION_REF, IMPLEMENTATION_TASK, MANIFEST_SHA256, NODES,
    PARAMETERS, PHASE_LIMITS, ROOT_TASK, SELECTION, SOURCE_FILES, SUBOBSERVATIONS,
    closed_read, sha, write_receipt, evidence_publish,
)

if EXECUTION_OWNER is None:raise RuntimeError('THR304 missing manager execution binding; product/test imports refused')

def await_condition(predicate, seconds: float = 5):
    deadline=time.monotonic()+seconds
    while time.monotonic()<deadline:
        result=predicate()
        if result:
            return result
        # Readiness polling only; races use actual commit/barrier receipts.
        time.sleep(.02)
    raise TimeoutError("bounded attachment readiness/readback missing")


def _provider_diagnostic(kind):
    """Validate storage independently; a refused context grants no write rights."""
    task,sid,agent,slug=sys.argv[2:6]
    control=Path(sys.argv[6])
    assert control.resolve(strict=True)==control and control.stat().st_uid==os.getuid()
    assert task and sid and len(task)<=128 and len(sid)<=128
    assert all(c.isascii() and (c.isalnum() or c=='-') for c in task+sid)
    path=Path(os.environ['HAPPYRANCH_ATTACHMENT_OBSERVER'])
    assert path==control/'observer'/'descriptor.json'
    observer=closed_read(path,16384)
    assert observer['node'] in NODES
    evidence=Path(observer['wait_log']).parent
    info=evidence.lstat()
    assert evidence.resolve(strict=True)==evidence and stat.S_ISDIR(info.st_mode) and info.st_uid==os.getuid()
    target=evidence/(kind+'-'+hashlib.sha256(observer['node'].encode()).hexdigest()[:20]+'-'+sid+'.json')
    return task,sid,agent,slug,control,observer,target,(info.st_dev,info.st_ino)


def _diagnostic_checkpoint(target, identity, row, cap):
    # Every external writer shares allocation, including old+temporary bytes.
    evidence_publish(target,(json.dumps(row,sort_keys=True)+'\n').encode(),
                     cap,target.parent,replace=True,identity=identity)


def _raw_output(chunks, lengths):
    # Preview is not evidence: concatenate reconstructed raw bytes, not previews.
    return {key:{'raw_base64':base64.b64encode(bytes(value)).decode('ascii'),
        'raw_length':len(value),'observed_raw_length':lengths[key],
        'raw_sha256':hashlib.sha256(value).hexdigest(),
        'preview_utf8_replacement':bytes(value[:256]).decode('utf-8','replace')}
        for key,value in chunks.items()}


def provider_main():
    _provider_admission()
    task,sid,agent,slug=sys.argv[2:6]
    control=Path(sys.argv[6])
    assert os.environ.get('HAPPYRANCH_RUNTIME_SESSION_ID')==sid and os.environ.get('HAPPYRANCH_ORG_SLUG')==slug=='test'
    assert control.resolve(strict=True)==control and control.stat().st_uid==os.getuid()
    observer=closed_read(Path(os.environ['HAPPYRANCH_ATTACHMENT_OBSERVER']),16384)
    assert observer['binding']['revision']==manifest()['revision'] and observer['node'] in NODES
    assert Path(os.environ['HAPPYRANCH_ATTACHMENT_OBSERVER'])==control/'observer'/'descriptor.json'
    prompt=sys.stdin.read(262145)
    if len(prompt.encode())>262144 or not task or not sid or slug!="test":
        raise SystemExit("provider actual launch identity missing")
    self_evaluation=None
    token="Exact required `manager_self_evaluation` object shape, filled with this launch's bound values:"
    if token in prompt:
        tail=prompt.split(token,1)[1]
        self_evaluation=json.loads(tail.split("```json\n",1)[1].split("\n```",1)[0])
        if "what_to_escalate" in self_evaluation:
            self_evaluation["what_to_escalate"]={"applicability":"does_not_apply","confidence":100,"uncertainty_codes":[]}
            self_evaluation["what_not_to_escalate"]={"applicability":"applies","confidence":100,"uncertainty_codes":[]}
        assert self_evaluation["root_task_id"]==task and self_evaluation["manager_session_id"]==sid
    row={"task_id":task,"session_id":sid,"agent":agent,"plan_parent_pid":os.getppid(),
         "driver_pid":os.getpid(),"prompt_sha256":hashlib.sha256(prompt.encode()).hexdigest(),
         "self_evaluation":self_evaluation,"head":manifest()["revision"],
         "helper_sha256":sha(Path(__file__).resolve())}
    write_receipt(control/f"launch-{sid}.json",row,16384)
    command=control/f"command-{sid}.json"
    payload=await_condition(lambda: closed_read(command,16384) if command.exists() else None,20)
    if payload=={"exit_without_callback":True}:
        write_receipt(control/f"provider-exit-{sid}.json",{"task":task,"sid":sid,"pid":os.getpid(),"exit":1,"callback":False},4096)
        raise SystemExit(1)
    body=control/f"body-{sid}.json"
    write_receipt(body,payload["body"],16384)
    task,sid,agent,slug,control,observer,diagnostic,identity=_provider_diagnostic('callback')
    process=None;selector=None;waited=None;capture_error=None;cleanup_errors=[];stage='checkpoint';helper_hash=None
    chunks={'stdout':bytearray(),'stderr':bytearray()};lengths={'stdout':0,'stderr':0}
    deadline=time.monotonic()+5
    def callback_checkpoint():
        _diagnostic_checkpoint(diagnostic,identity,{'node':observer['node'],'head':observer['binding']['revision'],
            'source':__file__,'helper_sha256':helper_hash,'task':task,'sid':sid,
            'output':_raw_output(chunks,lengths),'complete':capture_error is None and not cleanup_errors and waited is not None,
            'error':capture_error,'stage':stage,'cleanup_errors':cleanup_errors,
            'exit':None if process is None else process.returncode,'parent_wait_returncode':waited,
            'parent_pid':os.getpid(),'child_pid':None if process is None else process.pid,
            'child_state':'absent' if process is None else 'waited' if waited is not None else 'spawned'},32768)
    try:
        callback_checkpoint()
        stage='callback-source-binding'
        helper_hash=sha(Path(__file__).resolve())
        stage='callback-spawn'
        process=subprocess.Popen([manifest()["callback"],"report-completion","--org",slug,"--from-file",str(body)],
                                 stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        stage='callback-selector'
        selector=selectors.DefaultSelector()
        selector.register(process.stdout,selectors.EVENT_READ,'stdout')
        selector.register(process.stderr,selectors.EVENT_READ,'stderr')
        stage='callback-capture'
        callback_checkpoint()
        while selector.get_map():
            if time.monotonic()>deadline:raise TimeoutError('bounded public callback deadline')
            for key,_ in selector.select(.02):
                data=os.read(key.fileobj.fileno(),4096)
                if not data:selector.unregister(key.fileobj);continue
                lengths[key.data]+=len(data)
                remaining=4096-sum(len(value) for value in chunks.values())
                chunks[key.data].extend(data[:remaining])
                if len(data)>remaining:raise RuntimeError('public callback capture overflow; bounded prefix retained')
                callback_checkpoint()
        stage='callback-wait'
        waited=process.wait(timeout=max(.01,deadline-time.monotonic()))
    except BaseException as exc:
        capture_error=type(exc).__name__+':'+str(exc)[:1024]
        raise
    finally:
        # Every acquired resource is independently cleaned; a cleanup/checkpoint
        # error cannot replace the original exception or invent a consuming wait.
        def cleanup(action):
            try:action()
            except BaseException as exc:cleanup_errors.append(type(exc).__name__+':'+str(exc)[:512])
        if process is not None:
            if waited is None:
                def stop_and_wait():
                    nonlocal waited
                    try:
                        cleanup(process.terminate)
                        waited=process.wait(timeout=1)
                    except subprocess.TimeoutExpired:
                        cleanup(process.kill)
                        waited=process.wait(timeout=1)
                cleanup(stop_and_wait)
            for stream in (process.stdout,process.stderr):
                if stream is not None:cleanup(stream.close)
        if selector is not None:cleanup(selector.close)
        cleanup(callback_checkpoint)
        if cleanup_errors:
            cleanup(callback_checkpoint)
            if capture_error is None:raise RuntimeError('callback cleanup/checkpoint incomplete: '+cleanup_errors[0])
    stdout,stderr=bytes(chunks['stdout']),bytes(chunks['stderr'])
    from types import SimpleNamespace
    result=SimpleNamespace(stdout=stdout,stderr=stderr,returncode=process.returncode)
    if len(result.stdout)+len(result.stderr)>4096:
        raise SystemExit("callback capture cap")
    write_receipt(control/f"callback-{sid}.json",{"exit":result.returncode,
        "stdout_sha256":hashlib.sha256(result.stdout).hexdigest(),"stderr_sha256":hashlib.sha256(result.stderr).hexdigest(),
        "diagnostic":str(diagnostic),"parent_pid":os.getpid(),"child_pid":process.pid,"parent_wait_returncode":process.returncode},4096)
    if not payload["exit"]:
        await_condition(lambda:(control/f"exit-{sid}.json").exists(),20)
    write_receipt(control/f"provider-exit-{sid}.json",{"task":task,"sid":sid,"pid":os.getpid()},4096)
    raise SystemExit(result.returncode)


def provider_supervise():
    """Own the actual provider Python child, bounded bytes and real parent wait."""
    task,sid,agent,slug,control,observer,target,identity=_provider_diagnostic('provider')
    argv=[sys.executable,'-m','tests.helpers.attachment_feedback_fixture','provider',task,sid,agent,slug,str(control)]
    child=None;selector=None;waited=None;error=None;cleanup_errors=[];stage='checkpoint';helper_hash=None
    chunks={'stdout':bytearray(),'stderr':bytearray()};lengths={'stdout':0,'stderr':0}
    def checkpoint():
        _diagnostic_checkpoint(target,identity,{'node':observer['node'],'head':observer['binding']['revision'],
            'source':__file__,'helper_sha256':helper_hash,'task':task,'sid':sid,'argv':argv,
            'parent_pid':os.getpid(),'child_pid':None if child is None else child.pid,'parent_wait_returncode':waited,
            'child_state':'absent' if child is None else 'waited' if waited is not None else 'spawned',
            'output':_raw_output(chunks,lengths),'complete':waited is not None and error is None and not cleanup_errors,
            'error':error,'stage':stage,'cleanup_errors':cleanup_errors},262144)
    try:
        checkpoint()
        stage='provider-source-binding'
        helper_hash=sha(Path(__file__).resolve())
        stage='provider-admission'
        _provider_admission()
        assert os.environ.get('HAPPYRANCH_RUNTIME_SESSION_ID')==sid and os.environ.get('HAPPYRANCH_ORG_SLUG')==slug=='test'
        assert observer['binding']['revision']==manifest()['revision']
        stage='provider-spawn'
        child=subprocess.Popen(argv,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        stage='provider-selector'
        selector=selectors.DefaultSelector()
        selector.register(child.stdout,selectors.EVENT_READ,'stdout')
        selector.register(child.stderr,selectors.EVENT_READ,'stderr')
        stage='provider-capture'
        checkpoint()
        while selector.get_map():
            for key,_ in selector.select(.02):
                data=os.read(key.fileobj.fileno(),4096)
                if not data:selector.unregister(key.fileobj);continue
                lengths[key.data]+=len(data)
                remaining=16384-len(chunks[key.data])
                chunks[key.data].extend(data[:remaining])
                if len(data)>remaining:raise RuntimeError('provider capture overflow; bounded prefix retained')
                checkpoint()
        stage='provider-wait'
        waited=child.wait(timeout=5)
        write_receipt(control/f'provider-wait-{sid}.json',
            {'owner':os.getpid(),'child':child.pid,'returncode':waited,'sid':sid,'task':task},4096)
    except BaseException as exc:
        error=type(exc).__name__+':'+str(exc)[:1024]
        raise
    finally:
        def cleanup(action):
            try:action()
            except BaseException as exc:cleanup_errors.append(type(exc).__name__+':'+str(exc)[:512])
        if child is not None:
            if waited is None:
                def stop_and_wait():
                    nonlocal waited
                    try:
                        cleanup(child.terminate)
                        waited=child.wait(timeout=1)
                    except subprocess.TimeoutExpired:
                        cleanup(child.kill)
                        waited=child.wait(timeout=1)
                cleanup(stop_and_wait)
            for stream in (child.stdout,child.stderr):
                if stream is not None:cleanup(stream.close)
        if selector is not None:cleanup(selector.close)
        cleanup(checkpoint)
        if cleanup_errors:
            cleanup(checkpoint)
            if error is None:raise RuntimeError('provider cleanup/checkpoint incomplete: '+cleanup_errors[0])
    raise SystemExit(waited)


if _PROVIDER_ENTRY:
    if sys.argv[1]=='provider-supervise':provider_supervise()
    elif sys.argv[1]=='provider':
        try:
            provider_main()
        except BaseException as exc:
            if not isinstance(exc,SystemExit) or exc.code not in (None,0):
                # stderr is captured by the owned provider supervisor, even if
                # launch admission failed before a JSON launch receipt existed.
                import traceback
                try:os.write(2,traceback.format_exc(limit=12).encode()[:16384])
                except BaseException as diagnostic_exc:
                    exc.add_note('provider stderr diagnostic unavailable: '+type(diagnostic_exc).__name__)
            raise
    else:
        _provider_admission()
        task,sid,parent,child,status,control=sys.argv[2:8]
        launch=closed_read(Path(control)/f"launch-{sid}.json",16384)
        assert launch['task_id']==task and launch['driver_pid']==int(child) and launch['plan_parent_pid']==int(parent)
        assert os.environ.get('HAPPYRANCH_RUNTIME_SESSION_ID')==sid and os.environ.get('HAPPYRANCH_ORG_SLUG')=='test'
        write_receipt(Path(control)/f"provider-wait-{sid}.json",
            {'owner':int(parent),'child':int(child),'returncode':int(status),'sid':sid,'task':task},4096)
        raise SystemExit(0)

import httpx
import pytest

from tests.integration.conftest import seed_agent_definition, seed_workspace
from tests.helpers.deterministic_plan import DeterministicPlan
from runtime.config import Settings
from runtime.daemon.org_state import OrgState
from runtime.orchestrator._paths import OrgPaths
from runtime.orchestrator.orchestrator import completion_report_from_result_row
from runtime.infrastructure.database import Database, LostClaim, PendingRetry, RetryClaim
from runtime.models import TaskStatus

_DESCRIPTOR = None
_OBSERVATIONS = {}
_ACTIVE_NODE = None
_CALL_DEADLINE = None


def pytest_addoption(parser):
    parser.addoption("--attachment-qa-descriptor", action="store", default=None)


def pytest_configure(config):
    global _DESCRIPTOR
    path = config.getoption("attachment_qa_descriptor")
    if not path:
        raise pytest.UsageError("THR304 released descriptor required before fixtures")
    descriptor = closed_read(Path(path), 16384)
    parent = manifest()
    expected_keys = {"version", "source", "head", "tree", "ref", "task", "root", "manifest_sha256",
                     "source_hashes", "python", "python_binary", "python_version", "python_prefix", "python_sha256", "run", "attempt",
                     "selection", "nodes", "nonce", "evidence", "scratch", "phase_pipe", "start", "deadline", "phase_limits", "caps"}
    if (set(descriptor) != expected_keys or descriptor["version"] != 1
            or descriptor["head"] != parent["revision"] or descriptor["source"] != parent["source"]
            or EXECUTION_OWNER is None or descriptor["task"] != EXECUTION_OWNER
            or descriptor["root"] != ROOT_TASK or descriptor["ref"] != IMPLEMENTATION_REF
            or descriptor["manifest_sha256"] != MANIFEST_SHA256 or descriptor["nodes"] != list(NODES)
            or config.args != list(NODES) or config.getoption("markexpr") != "integration"
            or config.getoption("capture") != "no" or descriptor["selection"] != SELECTION
            or os.environ.get("HAPPYRANCH_TEST_REAL_PLATFORM") != "1"
            or sys.version_info[:2] != (3,14)):
        raise pytest.UsageError("THR304 parent/source/closed argv admission refused")
    source = Path(parent["source"])
    if (descriptor["caps"]!=CAPS or descriptor["phase_limits"]!=PHASE_LIMITS
            or descriptor["python"]!=parent["python"]
            or descriptor["python_prefix"]!=sys.prefix
            or descriptor["python_version"]!=sys.version
            or descriptor["python_binary"]!=str(Path(sys.executable).resolve())
            or descriptor["python_sha256"]!=sha(Path(sys.executable).resolve())
            or set(descriptor["source_hashes"])!=set(SOURCE_FILES)):
        raise pytest.UsageError("THR304 complete runtime/cap/source binding refused")
    for name in SOURCE_FILES:
        if descriptor["source_hashes"].get(name) != sha(source/name):
            raise pytest.UsageError("THR304 source digest changed")
    evidence = Path(descriptor["evidence"])
    if evidence.resolve(strict=True) != evidence or evidence.stat().st_uid != os.getuid():
        raise pytest.UsageError("external evidence owner refused")
    pipe = Path(descriptor["phase_pipe"])
    info = pipe.lstat()
    if not stat.S_ISFIFO(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
        raise pytest.UsageError("phase endpoint refused")
    _DESCRIPTOR = descriptor


def phase(node: str, phase_name: str, event: str, **fields):
    global _ACTIVE_NODE
    _ACTIVE_NODE=node
    row = {"nonce": _DESCRIPTOR["nonce"], "node": node, "phase": phase_name,
           "event": event, "pid": os.getpid(), "time_ns": time.time_ns(), **fields}
    data = (json.dumps(row,sort_keys=True)+"\n").encode()
    if len(data)>4096:
        raise RuntimeError("phase cap exceeded")
    fd = os.open(_DESCRIPTOR["phase_pipe"], os.O_WRONLY|os.O_NONBLOCK)
    try:
        if os.write(fd,data)!=len(data):
            raise RuntimeError("phase short write")
    finally:
        os.close(fd)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_setup(item):
    phase(item.nodeid,"setup","start")
    outcome=yield
    phase(item.nodeid,"setup","finish",outcome="error" if outcome.excinfo else "returned")


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_call(item):
    global _CALL_DEADLINE
    _CALL_DEADLINE=min(_DESCRIPTOR['deadline'],time.monotonic()+PHASE_LIMITS['call'])
    phase(item.nodeid,"call","start")
    outcome=yield
    phase(item.nodeid,"call","finish",outcome="error" if outcome.excinfo else "returned")
    _CALL_DEADLINE=None


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_teardown(item):
    phase(item.nodeid,"teardown","start")
    outcome=yield
    phase(item.nodeid,"teardown","finish",outcome="error" if outcome.excinfo else "returned")


def pytest_collection_modifyitems(session, config, items):
    if tuple(item.nodeid for item in items) != NODES:
        raise pytest.UsageError("THR304 actual collection differs from literal manifest")


def pytest_sessionfinish(session, exitstatus):
    missing = set(SUBOBSERVATIONS)-set(_OBSERVATIONS)
    for subid in missing:
        _OBSERVATIONS[subid] = {"outcome":"missing", "reason":"no complete assertion receipt"}
    write_receipt(Path(_DESCRIPTOR["evidence"])/"observations.json",_OBSERVATIONS,
                  evidence=Path(_DESCRIPTOR["evidence"]))
    if missing or any(v["outcome"] != "passed" for v in _OBSERVATIONS.values()):
        session.exitstatus = 1


def observe(subid: str, *, trigger, expected, observable, defect, **facts):
    if subid not in SUBOBSERVATIONS or subid in _OBSERVATIONS:
        raise RuntimeError("unknown or duplicate connected observation")
    _OBSERVATIONS[subid] = {"outcome":"passed", "trigger":trigger, "expected":expected,
                           "observable":observable, "defect":defect, "head":_DESCRIPTOR["head"], **facts}
    if len(json.dumps(_OBSERVATIONS[subid]).encode())>16384:
        _OBSERVATIONS[subid]={"outcome":"incomplete","reason":"16KiB observer receipt cap"}
        raise RuntimeError("observer receipt overflow")
    retain_node_receipt(_ACTIVE_NODE,
        "observation-"+hashlib.sha256(subid.encode()).hexdigest()[:20]+".json",
        {"id":subid,**_OBSERVATIONS[subid]},32768)


def attachment_snapshot(root: Path, task_id: str) -> dict:
    """Independent consistent read-only projection; overflow is unavailable."""
    with sqlite3.connect(OrgPaths(root=root).db_path.as_uri()+"?mode=ro",uri=True) as connection:
        connection.row_factory=sqlite3.Row
        connection.execute("BEGIN")
        queries={
            "tasks":("SELECT * FROM tasks WHERE id=? OR parent_task_id=? ORDER BY id LIMIT 257",(task_id,task_id)),
            "results":("SELECT * FROM task_results WHERE task_id=? ORDER BY id LIMIT 257",(task_id,)),
            "child_results":("SELECT * FROM task_results WHERE task_id IN (SELECT id FROM tasks WHERE parent_task_id=?) ORDER BY id LIMIT 257",(task_id,)),
            "audits":("SELECT * FROM audit_log WHERE task_id=? ORDER BY id LIMIT 257",(task_id,)),
            "links":("SELECT * FROM task_attachments WHERE task_id IN (SELECT id FROM tasks WHERE id=? OR parent_task_id=?) ORDER BY id LIMIT 257",(task_id,task_id)),
            "envelopes":("SELECT * FROM authority_policy_v2_continue_envelopes WHERE root_task_id=? ORDER BY rowid LIMIT 257",(task_id,)),
            "attempts":("SELECT * FROM authority_policy_v2_attempts WHERE root_task_id=? ORDER BY rowid LIMIT 257",(task_id,)),
        }
        result={name:[dict(row) for row in connection.execute(query,args)] for name,(query,args) in queries.items()}
    if any(len(rows)>256 for rows in result.values()) or len(json.dumps(result))>65536:
        raise RuntimeError("snapshot overflow/unavailable")
    return result


def corrections(snapshot: dict) -> list[dict]:
    found=[]
    for row in snapshot["audits"]:
        payload=json.loads(row["payload"]) if row["payload"] else {}
        if "attachment_correction" in payload:
            found.append(payload)
    return found


def assert_prechild(snapshot: dict, task_id: str, opportunities: int, *, baseline: dict | None = None):
    if baseline is not None:
        assert snapshot["links"]==baseline["links"]
        assert snapshot["child_results"]==baseline["child_results"]
        old={row["id"]:row for row in baseline["tasks"]}
        assert {row["id"] for row in snapshot["tasks"]}==set(old)
        for row in snapshot["tasks"]:
            if row["id"]!=task_id:assert row==old[row["id"]]
            else:
                for field in ("active_chain","active_fanout"):
                    assert row[field]==old[task_id][field]
        assert all(row in snapshot["results"] for row in baseline["results"])
        assert all(row in snapshot["audits"] for row in baseline["audits"])
        assert len(corrections(snapshot))==opportunities
        assert [row for row in snapshot["audits"] if row["action"] in ("task_attachment_added","fanout_spawned")]==[row for row in baseline["audits"] if row["action"] in ("task_attachment_added","fanout_spawned")]
        return
    assert len(snapshot["tasks"])==1 and snapshot["tasks"][0]["id"]==task_id
    assert not snapshot["links"]
    task=snapshot["tasks"][0]
    assert task["active_chain"] is None and task["active_fanout"] is None
    assert len(corrections(snapshot))==opportunities
    assert not any(row["action"] in ("task_attachment_added","fanout_spawned") for row in snapshot["audits"])


class PublicDriver:
    def __init__(self, port: str, root: Path, controls: Path, node: str):
        from runtime.runtime import read_token
        self.base=f"http://127.0.0.1:{port}/api/v1/orgs/test"
        self.headers={"Authorization":f"Bearer {read_token()}"}
        self.root,self.controls,self.node=root,controls,node
        self.port=port
        self.launches=[]

    def request(self,method,path,body=None):
        response=httpx.request(method,self.base+path,headers=self.headers,json=body,timeout=3)
        response.raise_for_status()
        return response.json()

    def create(self):
        return self.request("POST","/tasks",{"type":"general","brief":"THR304 disposable attachment scenario"})["task_id"]

    def launch(self,task_id: str):
        def find():
            for path in sorted(self.controls.glob("launch-*.json")):
                item=closed_read(path,16384)
                if item["task_id"]==task_id and item["session_id"] not in [x["session_id"] for x in self.launches]:
                    return item
        try:item=await_condition(find)
        except BaseException:
            retain_node_receipt(self.node,'launch-missing-'+task_id+'.json',
                {'node':self.node,'head':_DESCRIPTOR['head'],'task_id':task_id,
                 'snapshot':self.snapshot(task_id),'reason':'actual launch receipt unavailable'},262144)
            raise
        row=next(row for row in self.snapshot(task_id)["tasks"] if row["id"]==task_id)
        assert row["current_session_id"]==item["session_id"] and type(row["executor_pid"]) is int and row["executor_pid"]>0
        item["actual_provider_pid"]=row["executor_pid"]
        write_receipt(self.controls/f"provider-{item['session_id']}.json",
            {"pid":row["executor_pid"],"sid":item["session_id"],"task":task_id,"head":_DESCRIPTOR["head"]},4096)
        self.launches.append(item)
        assert item["agent"]=="engineering_head" or item["agent"] in ("dev_agent","product_manager","qa_engineer","code_reviewer")
        return item

    def submit(self,launch: dict,decision: dict,*,exit_provider: bool=True,expect_success: bool=True):
        body={"task_id":launch["task_id"],"session_id":launch["session_id"],"agent":launch["agent"],
              "status":"completed","summary":"THR304 deterministic authorized scenario", "confidence":80,"decision":decision}
        if decision["action"]=="escalate" and launch.get("self_evaluation"):
            body["manager_self_evaluation"]=launch["self_evaluation"]
        command=self.controls/f"command-{launch['session_id']}.json"
        write_receipt(command,{"body":body,"exit":exit_provider},16384)
        receipt_path=self.controls/f"callback-{launch['session_id']}.json"
        receipt=await_condition(lambda: closed_read(receipt_path,4096) if receipt_path.exists() else None)
        if not expect_success:
            return receipt
        assert receipt["exit"]==0
        rows=await_condition(lambda: [row for row in attachment_snapshot(self.root,launch["task_id"])["results"]
            if row["session_id"]==launch["session_id"]])
        assert len(rows)==1 and type(rows[0]["id"]) is int
        return rows[0],body

    def repeat_callback(self,body):
        wire={k:v for k,v in body.items() if k not in ('task_id','summary','confidence')}
        wire.update(output_summary=body['summary'],confidence_score=body['confidence'])
        return httpx.post(self.base+'/tasks/'+body['task_id']+'/completion',
                          json=wire,headers=self.headers,timeout=3)

    def release_provider(self,launch):
        write_receipt(self.controls/f"exit-{launch['session_id']}.json",{"exit":True},4096)

    def upload(self,label: str="source",n: int=1) -> list[dict]:
        callback=manifest()["callback"]
        refs=[]
        for index in range(n):
            file=self.controls/f"{label}-{index}-{len(list(self.controls.glob('upload-*.json')))}.txt"
            file.write_text("Synthetic authorized task evidence\n")
            file.chmod(0o600)
            process=subprocess.run([callback,"attach-upload","--org","test","--as-agent","engineering_head","--file",str(file)],
                                   capture_output=True,text=True,timeout=5)
            assert process.returncode==0 and len(process.stdout)+len(process.stderr)<8192
            lines=[line.strip() for line in process.stdout.splitlines() if line.strip().startswith("storage_key:")]
            assert len(lines)==1
            refs.append({"storage_key":lines[0].split(":",1)[1].strip(),"display_name":file.name})
            write_receipt(self.controls/f"upload-{len(list(self.controls.glob('upload-*.json')))}.json",refs[-1],4096)
        return refs

    def snapshot(self,task_id):
        return attachment_snapshot(self.root,task_id)

    def await_correction(self,task_id,count,*,baseline=None):
        snapshot=await_condition(lambda: (s if len(corrections(s:=self.snapshot(task_id)))==count else None))
        assert_prechild(snapshot,task_id,count,baseline=baseline)
        marker=corrections(snapshot)[-1]["attachment_correction"]
        assert marker["opportunity"]==count and marker["version"]==1
        assert marker["accepted_result_id"]<marker["feedback_result_id"]
        return snapshot


def seed_copy_observation(root: Path) -> dict:
    """Read every seed link without following directory links during traversal."""
    links={}
    for name in ('org','workspaces'):
        assert (root/name).is_dir() and not (root/name).is_symlink()
        for directory,dirs,files in os.walk(root/name,followlinks=False):
            for entry in sorted(dirs+files):
                path=Path(directory)/entry
                if not path.is_symlink():continue
                raw=os.readlink(path)
                assert not Path(raw).is_absolute()
                target=path.resolve(strict=True)
                assert target.is_relative_to(root.resolve())
                links[str(path.relative_to(root))]={'raw':raw,'target':str(target.relative_to(root.resolve()))}
    pairs={}
    for workspace in sorted((root/'workspaces').iterdir()):
        assert workspace.is_dir() and not workspace.is_symlink()
        agents,claude=workspace/'AGENTS.md',workspace/'CLAUDE.md'
        assert stat.S_ISREG(agents.lstat().st_mode)
        assert stat.S_ISLNK(claude.lstat().st_mode)
        assert os.readlink(claude)=='AGENTS.md' and claude.resolve(strict=True)==agents.resolve(strict=True)
        # Bootstrap's header-only history is seed data, never completed task history.
        assert (workspace/'task_history.md').read_text()==f'# Task History: {workspace.name}\n\n'
        pairs[workspace.name]={'agents_sha256':sha(agents),'raw_claude_link':os.readlink(claude)}
    assert pairs
    assert not any(path.suffix=='.db' for name in ('org','workspaces') for path in (root/name).rglob('*'))
    return {'links':links,'instruction_pairs':pairs,'seed_history':'empty'}


def provider_plan_observation(plan: Path) -> dict:
    """Observe the actual approved bytes/owner/modes before registered stub launch."""
    sidecar=Path(str(plan)+'.sha256')
    for path,mode in ((plan,0o700),(sidecar,0o600)):
        info=path.lstat()
        assert stat.S_ISREG(info.st_mode) and info.st_uid==os.getuid() and info.st_nlink==1
        assert stat.S_IMODE(info.st_mode)==mode
    digest=hashlib.sha256(plan.read_bytes()).hexdigest()
    assert sidecar.read_bytes()==digest.encode('ascii')
    return {'plan':str(plan),'sha256':digest,'sidecar':str(sidecar),'owner':os.getuid(),
            'plan_mode':0o700,'sidecar_mode':0o600}


@pytest.fixture
def attachment_runtime_seed(runtime, tmp_path, fake_claude_plan_env, fake_codex_plan_env,
                            fake_claude_thread_plan_env, fake_opencode_thread_plan_env, request):
    controls=tmp_path/"attachment-controls"
    controls.mkdir(mode=0o700)
    if request.node.callspec.params.get("variant") in ("root-DDD","nonroot-DDD"):
        config=OrgPaths(root=runtime).org_config_path
        import yaml
        values=yaml.safe_load(config.read_text()) if config.exists() else {}
        config.write_text(yaml.safe_dump({**(values or {}),"max_revise_rounds":2}))
    seed_agent_definition(runtime,"engineering_head",executor="claude",pending=False)
    for agent in ("engineering_head","dev_agent","product_manager","qa_engineer","code_reviewer"):
        seed_workspace(runtime,agent)
    script=("#!/bin/sh\nexec python -m tests.helpers.attachment_feedback_fixture provider-supervise "
            '\"$1\" \"$2\" \"$3\" \"$4\" '+shlex.quote(str(controls))+"\n")
    for plan in (fake_claude_plan_env,fake_codex_plan_env):
        plan.write_text(script)
    # Thread followup is explicitly declined through the real callback, not an implicit success.
    thread_script='#!/bin/sh\nexec happyranch thread-reply --org "$4" --thread-id "$1" --agent "$3" --invocation-token "$2" --decline\n'
    for plan in (fake_claude_thread_plan_env,fake_opencode_thread_plan_env):
        plan.write_text(thread_script)
    # Copy only declared seed inputs before the first daemon/history exists.
    template=controls/'matrix-seed'
    template.mkdir(mode=0o700)
    source_seed=seed_copy_observation(runtime)
    for name in ('org','workspaces'):
        shutil.copytree(runtime/name,template/name,symlinks=True)
    assert seed_copy_observation(template)==source_seed
    return controls


@pytest.fixture
def attachment_live_daemon(attachment_runtime_seed, runtime, request):
    request.getfixturevalue("attachment_public_observer")
    request.getfixturevalue("tmp_home")
    from runtime.daemon import paths as daemon_paths
    phase(request.node.nodeid,'setup','daemon-log',daemon_log=str(daemon_paths.log_file().resolve()))
    port=request.getfixturevalue("live_daemon")
    driver=PublicDriver(port,runtime,attachment_runtime_seed,request.node.nodeid)
    pid,log,receipt=_case_endpoint(driver)
    try:
        yield driver
    finally:
        roles=retain_role_receipts(driver.controls,driver.node,"close")
        phase(driver.node,"teardown","case-close",daemon_pid=pid,port=int(port),closure=str(receipt),roles=str(roles))
        closed=await_condition(lambda:closed_read(receipt,262144) if receipt.exists() else None,5)
        assert closed["closed"] and closed["port_closed"]
        assert closed["node"]==driver.node
        from runtime.daemon import paths as daemon_paths
        for path,expected in ((daemon_paths.pid_file(),str(pid)),(daemon_paths.port_file(),str(port))):
            if path.exists():
                assert path.resolve().is_relative_to(Path(manifest()["root"]))
                assert path.stat().st_uid==os.getuid() and path.read_text().strip()==expected
                path.unlink()


@pytest.fixture
def attachment_policy_bootstrap(attachment_live_daemon):
    driver=attachment_live_daemon
    before=driver.request("GET","/agents/engineering_head/team-escalation-policy")
    assert before["family"]=="empty" and before["selector_epoch"]==0 and before["selector"] is None
    body={**before["v2_starter"],"team":"engineering","what_to_escalate":"Synthetic escalation only when attachment execution is unauthorized.",
          "what_not_to_escalate":"Synthetic bounded attachment correction with retained current authority.",
          "based_on_selector_id":None,"expected_selector_id":None,"action":"bootstrap",
          "create_request_id":"attachment-create-"+os.urandom(12).hex(),"activation_request_id":"attachment-activate-"+os.urandom(12).hex(),
          "acknowledge_shared_credential_attribution":True}
    driver.request("POST","/agents/engineering_head/team-escalation-policy/v2/releases",body)
    after=driver.request("GET","/agents/engineering_head/team-escalation-policy")
    assert after["family"]=="v2" and after["selector_epoch"]==1 and after["team"]=="engineering"
    assert after["active"]["release"]["what_to_escalate"]==body["what_to_escalate"]
    assert after["active"]["release"]["what_not_to_escalate"]==body["what_not_to_escalate"]
    assert body["create_request_id"]!=body["activation_request_id"]
    with sqlite3.connect(OrgPaths(root=driver.root).db_path.as_uri()+"?mode=ro",uri=True) as db:
        rows=db.execute("SELECT * FROM authority_policy_v2_control_audit ORDER BY rowid").fetchall()
        assert len(rows)==2
        release=db.execute("SELECT id,policy_digest,version FROM authority_policy_v2_releases").fetchall()
        activation=db.execute("SELECT id,release_id FROM authority_policy_v2_activations").fetchall()
        assert len(release)==len(activation)==1
        assert release[0][0]==after["active"]["release"]["id"] and activation[0][1]==release[0][0]
    retain_node_receipt(driver.node,"v2-bootstrap.json",{"readback":after,"control_count":2})
    return driver


@contextmanager
def attachment_failure_adapter(target,attribute,throw,*,predicate=lambda *a,**k:True):
    """Fixed failure-only seam; restores exact original identity on every path."""
    original=getattr(target,attribute)
    calls=[]
    def failure(*args,**kwargs):
        if predicate(*args,**kwargs):
            calls.append(True)
            raise throw
        return original(*args,**kwargs)
    setattr(target,attribute,failure)
    try:
        yield calls
    finally:
        setattr(target,attribute,original)
        assert getattr(target,attribute) is original


@contextmanager
def attachment_authorizer(database,*,commit=False,audit=False):
    statements,denials=[],[]
    database._conn.set_trace_callback(lambda statement: statements.append(statement[:4096]))
    def authorize(action,arg1,arg2,dbname,trigger):
        reject=(commit and action==sqlite3.SQLITE_TRANSACTION and arg1=="COMMIT") or (audit and action==sqlite3.SQLITE_INSERT and arg1=="audit_log")
        if reject:
            denials.append((action,arg1,arg2))
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK
    database._conn.set_authorizer(authorize)
    try:
        yield statements,denials
    finally:
        database._conn.set_authorizer(None)
        database._conn.set_trace_callback(None)
        assert not database._conn.in_transaction


def attachment_fingerprint_comparisons(database,claim: RetryClaim,foreign_claim: RetryClaim):
    """26 named stale-input comparisons; not relabeled reassignment/race evidence."""
    fields={"task_id":foreign_claim.task_id,"assigned_agent":"obsolete-owner","team":"obsolete-team",
            "current_session_id":foreign_claim.current_session_id,"step":claim.step+1,
            "active_chain":"obsolete-chain","active_fanout":"obsolete-fanout","revision":claim.revision+1,
            "note":"obsolete-note","status":TaskStatus.FAILED,"block_kind":"delegated",
            "cancelled_at":"2026-10-13T00:00:00+00:00","result_row_id":foreign_claim.result_row_id}
    # A must use the actual contemporaneous decision audit supplied by the real consumer.
    before=attachment_snapshot(database_path_root(database),claim.task_id)
    for field,value in fields.items():
        stale=replace(claim,**{field:value})
        assert isinstance(database.try_attachment_retry_feedback(stale,"stale comparison","task_attachment_not_found"),LostClaim)
        assert attachment_snapshot(database_path_root(database),claim.task_id)==before
        observe(f"C5.fingerprint.{field}.A",trigger="one changed original-claim input",expected="lost claim, zero writes",
                observable="real writer returns LostClaim",defect="omitted ownership field",field=field)
    pending=database.try_attachment_retry_feedback(claim,"genuine attachment feedback","task_attachment_not_found")
    assert isinstance(pending,PendingRetry)
    after_A=attachment_snapshot(database_path_root(database),claim.task_id)
    for field,value in fields.items():
        entered=[]
        stale=replace(pending,claim=replace(claim,**{field:value}))
        assert isinstance(database.admit_retry_feedback(stale,lambda:entered.append(True)),LostClaim) and not entered
        assert attachment_snapshot(database_path_root(database),claim.task_id)==after_A
        observe(f"C5.fingerprint.{field}.B",trigger="one changed pending-claim input",expected="A retained, no enqueue",
                observable="real B returns LostClaim",defect="omitted pending ownership field",field=field)
    return pending


class PublicObserver:
    def __init__(self,descriptor,path):
        self.descriptor,self.path=descriptor,path
        self.reader=os.open(descriptor['output'],os.O_RDWR|os.O_NONBLOCK)
        self.writer=os.open(descriptor['incoming'],os.O_RDWR|os.O_NONBLOCK)
        self.buffer=b''

    def send(self,row):
        data=(json.dumps(row,sort_keys=True)+'\n').encode()
        assert len(data)<=4096 and os.write(self.writer,data)==len(data)

    def arm(self,task_id,result_id,phases):
        assert set(phases)<=set(self.descriptor['binding']['anchors'])
        self.send({'command':'arm','nonce':self.descriptor['nonce'],'task_id':task_id,'result_id':result_id,'phases':phases})

    def pause(self,expected):
        deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            readable,_,_=select.select([self.reader],[],[],max(0,deadline-time.monotonic()))
            if readable:
                self.buffer+=os.read(self.reader,4096)
                assert len(self.buffer)<=8192
                if b'\n' in self.buffer:
                    raw,self.buffer=self.buffer.split(b'\n',1)
                    row=json.loads(raw)
                    assert row['phase']==expected and row['nonce']==self.descriptor['nonce']
                    assert row['head']==_DESCRIPTOR['head']
                    return row
        raise TimeoutError('source-bound observer pause missing')

    def release(self,phase_name):
        self.send({'command':'release','nonce':self.descriptor['nonce'],'phase':phase_name})

    def acknowledge_interruption(self,phase_name):
        self.send({'command':'ack-interrupt','nonce':self.descriptor['nonce'],'phase':phase_name})
        row=self.pause(phase_name)
        assert row['event']=='acknowledged'

    def interrupt_and_close(self,pause,port):
        receipt=Path(_DESCRIPTOR['evidence'])/('closure-'+str(pause['pid'])+'.json')
        suffix='interrupt-'+hashlib.sha256(str(self.path).encode()).hexdigest()[:12]
        roles=retain_role_receipts(self.path.parent.parent,self.descriptor['node'],suffix)
        phase(self.descriptor['node'],'call','interrupt',daemon_pid=pause['pid'],port=int(port),closure=str(receipt),roles=str(roles))
        closure=await_condition(lambda:closed_read(receipt,262144) if receipt.exists() else None,10)
        assert closure['closed'] is True and closure['pid']==pause['pid']
        assert closure['port_closed'] is True
        return closure


@pytest.fixture
def attachment_public_observer(attachment_runtime_seed,monkeypatch,request):
    from tests.helpers.attachment_feedback_prelaunch.sitecustomize import source_binding
    parent=manifest()
    control=attachment_runtime_seed/'observer'
    control.mkdir(mode=0o700)
    incoming,output=control/'commands.fifo',control/'events.fifo'
    os.mkfifo(incoming,0o600);os.mkfifo(output,0o600)
    descriptor={'binding':source_binding(Path(parent['source']),parent['revision']),
                'node':request.node.nodeid,'released_nodes':list(NODES),'nonce':os.urandom(24).hex(),
                'incoming':str(incoming),'output':str(output),
                'wait_log':str(Path(_DESCRIPTOR['evidence'])/('waits-'+hashlib.sha256(request.node.nodeid.encode()).hexdigest()[:20]+'.jsonl'))}
    path=control/'descriptor.json';write_receipt(path,descriptor,16384)
    observer=PublicObserver(descriptor,path)
    monkeypatch.setenv('HAPPYRANCH_ATTACHMENT_OBSERVER',str(path))
    monkeypatch.setenv('PYTHONPATH',str(Path(parent['source'])/'tests/helpers/attachment_feedback_prelaunch')+os.pathsep+os.environ['PYTHONPATH'])
    yield observer
    os.close(observer.reader);os.close(observer.writer)


@contextmanager
def attachment_supplement_driver(driver,closure):
    assert closure['closed'] is True and closure['port_closed'] is True
    org=OrgState.load(slug='test',root=driver.root,settings=Settings())
    try:
        # Genuine new owner/permission/boot bindings are supplied by OrgState.
        org.bind_authority_v2_owner()
        yield org.orchestrator
    finally:
        org.close()

@contextmanager
def _attachment_consumer_barrier(orch,task_id,report,result_id,*,function,anchor,after=False):
    """Source-bound supplemental trace/commit barrier, never a value rewrite."""
    import ast
    import dis
    import threading
    from runtime.orchestrator import run_step as source_module
    callback=getattr(source_module,function)
    path=Path(source_module.__file__).resolve()
    assert sha(path)==_DESCRIPTOR['source_hashes']['runtime/orchestrator/run_step.py']
    tree=ast.parse(path.read_bytes())
    node=next(item for item in tree.body if isinstance(item,ast.FunctionDef) and item.name==function)
    statements=[item for item in ast.walk(node) if isinstance(item,(ast.Assign,ast.Expr)) and
                any(isinstance(call,ast.Call) and ((isinstance(call.func,ast.Attribute) and call.func.attr==anchor)
                    or (isinstance(call.func,ast.Name) and call.func.id==anchor)) for call in ast.walk(item))]
    assert len(statements)==1
    statement=statements[0]
    lines=sorted({line for _,line in dis.findlinestarts(callback.__code__) if line is not None})
    target=next(line for line in lines if line>statement.end_lineno) if after else statement.lineno
    if after and function=='_consume_completion_report_body':
        # Success skips the except suite: use the first statement AFTER the
        # containing top-level try, rather than its handler's physical line.
        block=next(item for item in ast.walk(node) if isinstance(item,ast.Try) and statement in list(ast.walk(item)))
        following=[item for item in node.body if item.lineno>block.end_lineno]
        assert following
        target=following[0].lineno
    arrived,release=threading.Event(),threading.Event()
    boundary={'release':release,'errors':[],'entered':False,'source':sha(path),'line':target,'queue_events':[]}
    from runtime.daemon.queue import TaskQueue
    def trace(frame,event,arg):
        if event=='call' and frame.f_code is TaskQueue.put_nowait.__code__ and frame.f_locals.get('task_id')==task_id:
            boundary['queue_events'].append({'reservation':orch._db._conn.in_transaction,'thread':threading.get_native_id()})
        if event=='line' and frame.f_code is callback.__code__ and frame.f_lineno==target and not boundary['entered']:
            boundary['entered']=True
            original=frame
            while original is not None and 'expected_claim' not in original.f_locals:
                original=original.f_back
            assert original is not None
            claim=original.f_locals['expected_claim']
            assert claim.task_id==task_id and claim.result_row_id==result_id
            boundary['claim']=claim
            boundary['child_id']=original.f_locals.get('child_id')
            boundary['outcome']=frame.f_locals.get('outcome')
            boundary['thread']=threading.get_native_id()
            arrived.set()
            if not release.wait(5):
                # Supplemental observer timeout is incomplete; never a successful claim.
                raise TimeoutError('supplemental causal barrier expired')
        return trace
    def consume():
        sys.settrace(trace)
        try:
            source_module._consume_completion_report(orch,task_id,report,result_row_id=result_id,reclaim_terminal_worktree=False)
        except BaseException as exc:
            boundary['errors'].append(type(exc).__name__)
        finally:
            sys.settrace(None)
    thread=threading.Thread(target=consume)
    thread.start()
    if not arrived.wait(3):
        release.set();thread.join(2)
        raise RuntimeError('actual supplemental source boundary unavailable: '+repr(boundary['errors']))
    try:
        yield boundary
    finally:
        release.set();thread.join(5)
        assert not thread.is_alive()


def attachment_writer_barrier(orch,task_id,report,result_id,phase_name):
    return _attachment_consumer_barrier(orch,task_id,report,result_id,
        function='_attachment_feedback_and_reenqueue',anchor='try_attachment_retry_feedback',after=phase_name=='after-A')


def attachment_preparation_barrier(orch,task_id,report,result_id,*,before=False):
    return _attachment_consumer_barrier(orch,task_id,report,result_id,
        function='_consume_completion_report_body',anchor='_prepare_attachment_params',after=not before)


STAGED_PROVIDER_LIMIT = 5  # Leave a sixth worker for the original fixture's pending provider.


def stage_cases(driver, observer, decisions):
    """One bounded worker batch; every result comes through real admission."""
    assert 1<=len(decisions)<=STAGED_PROVIDER_LIMIT
    if getattr(driver,'batch_index',0):
        assert closed_read(driver.controls/'batch-prerequisites.json',16384)==driver.batch_prerequisites
        assert driver.batch_prerequisites['seed']['instruction_pairs']
        assert driver.batch_prerequisites['provider_plan']['sha256']
    staged=[]
    for decision in decisions:
        task=driver.create(); launch=driver.launch(task)
        result,body=driver.submit(launch,decision,exit_provider=False)
        staged.append((task,launch,result,body))
    for task,launch,result,_ in staged:
        before=driver.snapshot(task)
        assert len(before['tasks'])==1 and not before['links'] and not corrections(before)
        owner=before['tasks'][0]
        assert owner['status']=='in_progress' and owner['block_kind'] is None
        assert owner['current_session_id']==launch['session_id'] and owner['executor_pid']==launch['actual_provider_pid']
        assert not any(row['action']=='orchestration_step' for row in before['audits'])
        assert not (driver.controls/f"provider-exit-{launch['session_id']}.json").exists()
    task,launch,result,_=staged[-1]
    observer.arm(task,result['id'],['before-gate'])
    driver.release_provider(launch)
    pause=observer.pause('before-gate')
    assert pause['task_id']==task and pause['result_id']==result['id']
    observer.acknowledge_interruption('before-gate')
    closure=observer.interrupt_and_close(pause,driver.port)
    for task,launch,result,_ in staged:
        snapshot=driver.snapshot(task)
        assert len(snapshot['tasks'])==1 and not snapshot['links'] and not corrections(snapshot)
        assert any(row['id']==result['id'] and row['session_id']==launch['session_id'] for row in snapshot['results'])
    return staged,closure


@contextmanager
def matrix_batch_runtime(original: PublicDriver, index: int):
    """Fresh seeded venue; no task/result/audit/claim history is copied or restarted."""
    from runtime.runtime import RuntimeDir
    from runtime.daemon import paths as daemon_paths, runtimes
    from runtime.orchestrator.executor_binary_registry import save_registry
    from tests.integration.conftest import _nested_daemon_env
    from tests.helpers.attachment_feedback_prelaunch.sitecustomize import source_binding
    parent=manifest()
    base=original.controls/('matrix-batch-'+str(index))
    base.mkdir(mode=0o700)
    controls=base/'controls';controls.mkdir(mode=0o700)
    container=RuntimeDir.init(base/'runtime').root
    root=container/'orgs/test';root.mkdir(parents=True)
    source_seed=seed_copy_observation(original.controls/'matrix-seed')
    for name in ('org','workspaces'):
        shutil.copytree(original.controls/'matrix-seed'/name,root/name,symlinks=True)
    assert seed_copy_observation(root)==source_seed
    (root/'kb').mkdir()
    home=base/'daemon';home.mkdir(mode=0o700)
    observer_control=controls/'observer';observer_control.mkdir(mode=0o700)
    incoming,output=observer_control/'commands.fifo',observer_control/'events.fifo'
    os.mkfifo(incoming,0o600);os.mkfifo(output,0o600)
    descriptor={'binding':source_binding(Path(parent['source']),parent['revision']),
        'node':original.node,'released_nodes':list(NODES),'nonce':os.urandom(24).hex(),
        'incoming':str(incoming),'output':str(output),
        'wait_log':str(Path(_DESCRIPTOR['evidence'])/('waits-'+hashlib.sha256(original.node.encode()).hexdigest()[:20]+'-batch-'+str(index)+'.jsonl'))}
    observer_path=observer_control/'descriptor.json';write_receipt(observer_path,descriptor,16384)
    observer=PublicObserver(descriptor,observer_path)
    plan=DeterministicPlan(base/'provider.sh')
    plan.write_text('#!/bin/sh\nexec python -m tests.helpers.attachment_feedback_fixture provider-supervise "$1" "$2" "$3" "$4" '+shlex.quote(str(controls))+'\n')
    approved_plan=provider_plan_observation(plan)
    prerequisites={'seed':source_seed,'provider_plan':approved_plan}
    write_receipt(controls/'batch-prerequisites.json',prerequisites,16384)
    updates={'HAPPYRANCH_DAEMON_HOME':str(home),'HAPPYRANCH_DAEMON_PORT':'0',
        'HAPPYRANCH_ATTACHMENT_OBSERVER':str(observer_path),
        'FAKE_CLAUDE_PLAN':str(plan),'FAKE_CODEX_PLAN':str(plan)}
    previous={key:os.environ.get(key) for key in updates}
    os.environ.update(updates)
    pid=None
    try:
        save_registry({provider:item['path'] for provider,item in parent['stubs'].items()})
        runtimes.register(container)
        script=Path(parent['source'])/'scripts/daemon.sh'
        phase(original.node,'call','daemon-log',daemon_log=str(daemon_paths.log_file().resolve()))
        subprocess.run([str(script),'start'],check=True,env=_nested_daemon_env(),timeout=5)
        port=await_condition(lambda:daemon_paths.port_file().read_text().strip() if daemon_paths.port_file().exists() else None)
        driver=PublicDriver(port,root,controls,original.node)
        driver.batch_index=index
        driver.batch_prerequisites=prerequisites
        pid,log,receipt=_case_endpoint(driver,wait_log=descriptor['wait_log'])
        yield driver,observer
    finally:
        if pid is not None:
            roles=retain_role_receipts(controls,original.node,'batch-'+str(index)+'-close')
            phase(original.node,'call','case-close',daemon_pid=pid,port=int(port),closure=str(receipt),roles=str(roles))
            closed=await_condition(lambda:closed_read(receipt,262144) if receipt.exists() else None,5)
            assert closed['closed'] and closed['port_closed']
        os.close(observer.reader);os.close(observer.writer)
        for key,value in previous.items():
            if value is None:os.environ.pop(key,None)
            else:os.environ[key]=value


def stage_case_batches(driver: PublicDriver, observer: PublicObserver, decisions: list[dict]):
    """Up to three sequential <=5-provider venues within the SAME case deadline."""
    from contextlib import nullcontext
    assert 1<=len(decisions)<=13
    original=driver
    for index,offset in enumerate(range(0,len(decisions),STAGED_PROVIDER_LIMIT)):
        batch=decisions[offset:offset+STAGED_PROVIDER_LIMIT]
        context=nullcontext((driver,observer)) if index==0 else matrix_batch_runtime(original,index)
        with context as (selected,selected_observer):
            if index:
                # Fresh public uploads in the fresh org, never copied claim/history rows.
                mapping={}
                def reupload(value: object) -> None:
                    if isinstance(value,dict):
                        key=value.get('storage_key')
                        if key is not None:
                            if key not in mapping:
                                mapping[key]=selected.upload('matrix-input')[0]
                                mapping[mapping[key]['storage_key']]=mapping[key]
                            value['storage_key']=mapping[key]['storage_key']
                        else:
                            for child in value.values():reupload(child)
                    elif isinstance(value,list):
                        for child in value:reupload(child)
                for decision in batch:reupload(decision)
            staged,closure=stage_cases(selected,selected_observer,batch)
            yield selected,staged,closure,offset


def consume_staged(orch, staged):
    task,launch,result,_=staged
    report=completion_report_from_result_row(task,result,fallback_agent=launch['agent'])
    from runtime.orchestrator.run_step import _consume_completion_report
    _consume_completion_report(orch,task,report,result_row_id=result['id'],reclaim_terminal_worktree=False)


@contextmanager
def source_barrier(orch, staged, *, function, anchor, after=False, event='line'):
    """Observe only real callable frames; exceptions/returns/claims remain unchanged."""
    import ast
    import dis
    import threading
    from runtime.orchestrator import run_step as module
    callback=getattr(module,function)
    path=Path(module.__file__).resolve()
    assert sha(path)==_DESCRIPTOR['source_hashes']['runtime/orchestrator/run_step.py']
    node=next(x for x in ast.parse(path.read_bytes()).body if isinstance(x,ast.FunctionDef) and x.name==function)
    statements=[x for x in ast.walk(node) if isinstance(x,(ast.Expr,ast.Assign,ast.Return)) and
        any(isinstance(c,ast.Call) and (getattr(c.func,'attr',None)==anchor or getattr(c.func,'id',None)==anchor)
            for c in ast.walk(x))]
    if event=='line':
        assert len(statements)==1
        statement=statements[0]
        lines=sorted({line for _,line in dis.findlinestarts(callback.__code__) if line is not None})
        line=next(x for x in lines if x>statement.end_lineno) if after else statement.lineno
    else:
        assert event in ('call','return');line=None
    task,launch,result,_=staged
    arrived,release=threading.Event(),threading.Event()
    boundary={'release':release,'errors':[],'source':sha(path),'line':line,'event':event,'entered':False}
    def trace(frame,kind,value):
        if frame.f_code is callback.__code__ and kind==event and (line is None or frame.f_lineno==line) and not boundary['entered']:
            original=frame
            while original is not None and 'expected_claim' not in original.f_locals:
                original=original.f_back
            assert original is not None
            claim=original.f_locals['expected_claim']
            if claim.task_id!=task or claim.result_row_id!=result['id']:
                return trace
            boundary.update(entered=True,claim=claim,thread=threading.get_native_id(),
                pid=os.getpid(),locals=dict(frame.f_locals),returned=value)
            arrived.set()
            if not release.wait(5):raise TimeoutError('source-bound supplemental handshake expired')
        return trace
    def run():
        sys.settrace(trace)
        try:consume_staged(orch,staged)
        except BaseException as exc:boundary['errors'].append((type(exc).__name__,str(exc)[:512]))
        finally:sys.settrace(None)
    thread=threading.Thread(target=run)
    thread.start()
    if not arrived.wait(3):
        release.set();thread.join(2)
        raise RuntimeError('source boundary unavailable: '+repr(boundary['errors']))
    try:yield boundary
    finally:
        release.set();thread.join(5)
        assert not thread.is_alive()


@contextmanager
def attachment_history_failure_only_read_adapter(database, task, variant):
    """SYNTHETIC INTEGRITY NEGATIVE: copies read inputs; never writes history."""
    import copy
    rows=database._attachment_correction_rows_txn(task)
    markers=[dict(row) for row in rows if 'attachment_correction' in json.loads(row['payload'])]
    assert markers
    selected=markers[-1]
    payload=json.loads(selected['payload']);marker=payload['attachment_correction']
    malformed={'malformed-json':'{','duplicate-key':selected['payload'][:-1]+',"step_number":1}',
               'nonstandard-constant':selected['payload'][:-1]+',"bad":NaN}'}
    if variant in malformed:selected['payload']=malformed[variant]
    else:
        edits={'wrong-version':('version',2),'bool-version':('version',True),
            'wrong-code':('error_code','unknown'),'ordinal-gap':('opportunity',4),
            'third-ordinal':('opportunity',3),'bool-ordinal':('opportunity',True),
            'missing-accepted':('accepted_result_id',999999999),'orphan-result':('feedback_result_id',999999999),
            'cross-role':('feedback_result_id',marker['accepted_result_id']),
            'wrong-sid':('accepted_session_id','foreign-sid'),'bool-id':('accepted_result_id',True)}
        if variant in edits:
            field,value=edits[variant];marker[field]=value
        elif variant in ('duplicate-accepted','duplicate-feedback'):
            assert len(markers)==2
            prior=json.loads(markers[0]['payload'])['attachment_correction']
            field='accepted_result_id' if variant=='duplicate-accepted' else 'feedback_result_id'
            marker[field]=prior[field]
        elif variant=='reason-mismatch':payload['decision']['reason']+=' changed'
        elif variant=='ambiguous-step':
            original=next(dict(x) for x in rows if json.loads(x['payload'])['step_number']==payload['step_number']
                and json.loads(x['payload'])['decision']['action']!='feedback')
            # Two read-only contemporaneous originals, with distinct adapted IDs; no INSERT.
            # Adapt only audit identity spacing; both originals precede the marker.
            original_id=original['id']
            rows=[{**dict(row),'id':row['id']*2} for row in rows]
            selected['id']*=2
            original['id']=original_id*2+1
            rows=sorted([*rows,original],key=lambda row:row['id'])
        elif variant=='wrong-task':selected['task_id']='foreign-task'
        elif variant=='wrong-actor':selected['agent']='foreign-agent'
        elif variant=='wrong-step':payload['step_number']=True
        elif variant=='wrong-action':payload['decision']['action']='done'
        elif variant=='extra-marker':marker['unknown']=1
        elif variant not in ('status-mismatch','owner-mismatch','feedback-status','feedback-confidence','feedback-sid','feedback-decision','accepted-decision','accepted-status'):
            raise AssertionError('closed integrity variant missing')
        selected['payload']=json.dumps(payload)
    copied=[copy.deepcopy(selected) if row['id']==selected['id'] else dict(row) for row in rows]
    if variant=='ambiguous-step':
        parsed=[json.loads(row['payload']) for row in copied]
        initial=[value for value in parsed if value['step_number']==payload['step_number'] and value['decision']['action']!='feedback']
        assert len(initial)==2 and initial[0]==initial[1]
        assert all(row['id']<selected['id'] for row,value in zip(copied,parsed,strict=True) if value in initial)
        adapted_markers=[value['attachment_correction'] for value in parsed if 'attachment_correction' in value]
        assert adapted_markers==[json.loads(row['payload'])['attachment_correction'] for row in markers]
        assert [value['opportunity'] for value in adapted_markers]==list(range(1,len(markers)+1))
        assert len({row['id'] for row in copied})==len(copied)
        for value in adapted_markers:
            accepted=database._conn.execute('SELECT * FROM task_results WHERE id=?',(value['accepted_result_id'],)).fetchone()
            feedback=database._conn.execute('SELECT * FROM task_results WHERE id=?',(value['feedback_result_id'],)).fetchone()
            assert accepted is not None and feedback is not None
            assert accepted['task_id']==feedback['task_id']==task
            assert accepted['session_id']==value['accepted_session_id'] and feedback['session_id']==''
    original_rows=database._attachment_correction_rows_txn
    original_connection=database._conn
    accepted_id=marker['accepted_result_id'];feedback_id=marker['feedback_result_id']
    class ReadCursor:
        def __init__(self,row):self.row=row
        def fetchone(self):return self.row
    class ReadConnection:
        def __getattr__(self,name):return getattr(original_connection,name)
        def execute(self,sql,args=()):
            cursor=original_connection.execute(sql,args)
            if sql=='SELECT * FROM task_results WHERE id=?' and args[0] in (accepted_id,feedback_id):
                row=cursor.fetchone()
                if row is None:return ReadCursor(None)
                row=dict(row)
                if args[0]==accepted_id:
                    if variant in ('status-mismatch','accepted-status'):row['status']='failed'
                    if variant=='owner-mismatch':row['agent']='foreign-owner'
                    if variant=='accepted-decision':row['decision_json']='{"action":"done","summary":"synthetic negative"}'
                else:
                    if variant=='feedback-status':row['status']='failed'
                    if variant=='feedback-confidence':row['confidence_score']=1
                    if variant=='feedback-sid':row['session_id']='not-empty'
                    if variant=='feedback-decision':row['decision_json']='{}'
                return ReadCursor(row)
            return cursor
    database._attachment_correction_rows_txn=lambda selected_task: copied if selected_task==task else original_rows(selected_task)
    database._conn=ReadConnection()
    try:yield
    finally:
        database._conn=original_connection
        del database._attachment_correction_rows_txn
        assert database._attachment_correction_rows_txn.__func__ is original_rows.__func__
        assert database._conn is original_connection


def independent_projection(driver,task):
    snapshot=driver.snapshot(task)
    return hashlib.sha256(json.dumps(snapshot,sort_keys=True).encode()).hexdigest()


def v2_lineage_readback(driver,task,first,original,second,r2):
    """All predecessors are read from real committed rows, never reconstructed."""
    tables={'bindings':('authority_policy_v2_session_bindings','root_task_id'),
            'attempts':('authority_policy_v2_attempts','root_task_id'),
            'candidates':('authority_policy_v2_candidates','root_task_id'),
            'pins':('authority_policy_v2_pins','root_task_id'),
            'evaluations':('authority_policy_v2_evaluations','root_task_id'),
            'envelopes':('authority_policy_v2_continue_envelopes','root_task_id'),
            'notifications':('authority_policy_v2_recovery_notifications','root_task_id'),
            'dispatch':('authority_policy_v2_root_dispatch','root_task_id')}
    with sqlite3.connect(OrgPaths(root=driver.root).db_path.as_uri()+'?mode=ro',uri=True) as db:
        db.row_factory=sqlite3.Row;db.execute('BEGIN')
        rows={name:[dict(row) for row in db.execute('SELECT * FROM '+table+' WHERE '+field+'=?',(task,))]
              for name,(table,field) in tables.items()}
        rows['candidate_audits']=[dict(row) for row in db.execute(
            'SELECT * FROM authority_policy_v2_candidate_audit WHERE candidate_id IN '
            '(SELECT candidate_id FROM authority_policy_v2_candidates WHERE root_task_id=?) ORDER BY rowid',(task,))]
        rows['task_audits']=[dict(row) for row in db.execute('SELECT * FROM audit_log WHERE task_id=? ORDER BY id',(task,))]
    assert all(len(value)<=128 for value in rows.values()) and len(json.dumps(rows))<=262144
    assert first['session_id']!=second['session_id'] and original['id']!=r2['id']
    bindings={row['manager_session_id']:row for row in rows['bindings']}
    assert first['session_id'] in bindings and second['session_id'] in bindings
    attempt=next(row for row in rows['attempts'] if row['result_id']==original['id'])
    assert attempt['stage']=='consumed_audited' and attempt['finalization_state']=='continued'
    assert attempt['binding_id']==bindings[first['session_id']]['binding_id']
    envelope=next(row for row in rows['envelopes'] if row['result_id']==original['id'])
    assert envelope['attempt_id']==attempt['attempt_id'] and envelope['causal_result_id']==original['id']
    assert envelope['spending_result_id']==r2['id'] and envelope['evaluation_outcome']=='continue_applies'
    assert rows['candidates'] and rows['evaluations'] and rows['candidate_audits']
    assert rows['notifications'] and rows['dispatch']
    dispatch=rows['dispatch'][0]
    assert dispatch['expected_manager_agent']==second['agent'] and dispatch['expected_manager_session_id']==second['session_id']
    assert any(row['notification_id']==dispatch['generation_id'] for row in rows['notifications'])
    assert dispatch['state'] in ('admitted','retired')
    stages={json.loads(row['payload']).get('stage') for row in rows['task_audits'] if row['payload']}
    # The native stage vocabulary is checked from the actual protocol sequence.
    from runtime.orchestrator.authority import _V2_PRE_FINAL_STAGE_SEQUENCE
    for _,stage,_ in _V2_PRE_FINAL_STAGE_SEQUENCE:
        if stage.endswith("audited"):
            assert stage in stages
    assert "admitted" in stages
    assert rows["pins"] and all(row["lifecycle_stage"]=="consumed" for row in rows["candidates"])
    assert any(row["outcome"]=="continue_applies" for row in rows["evaluations"])
    assert {row["event"] for row in rows["candidate_audits"]} >= {"claimed","evaluated","consumed","final"}
    assert {"continued","publish_claimed","generation_claimed","notification_settled"} <= stages
    assert {"published","publish_returned"} & stages
    assert any(row["action"]=="authority_policy_v2_recovery_settled" for row in rows["task_audits"])
    notification=next(row for row in rows["notifications"] if row["notification_id"]==dispatch["generation_id"])
    assert notification["next_session_id"]==second["session_id"] and notification["publication_attempt"]>0
    assert notification["state"] in ("admitted","settled")
    retain_node_receipt(driver.node,"v2-lineage-"+str(r2["id"])+".json",rows,65536)
    return rows


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item,call):
    outcome=yield
    report=outcome.get_result()
    if _DESCRIPTOR is None or item.nodeid not in NODES:
        raise RuntimeError('unbound pytest report')
    raw='' if report.longrepr is None else str(report.longrepr)
    sections=[];section_remaining=16384;section_overflow=False
    for name,text in report.sections[:8]:
        encoded=text.encode()
        selected=encoded[:section_remaining]
        sections.append((name,selected.decode('utf-8','replace')))
        section_remaining-=len(selected)
        section_overflow=section_overflow or len(encoded)>len(selected)
    overflow=len(raw.encode())>16384 or len(report.sections)>8 or section_overflow
    # Preserve adverse report text before a teardown/closure failure kills pytest.
    retain_node_receipt(item.nodeid,'report-'+report.when+'.json',
        {'node':item.nodeid,'phase':report.when,'head':_DESCRIPTOR['head'],
         'helper_sha256':sha(Path(__file__).resolve()),'outcome':report.outcome,
         'longrepr':raw.encode()[:16384].decode('utf-8','replace'),'sections':sections,
         'duration':report.duration,'complete':not overflow},262144)
    if overflow:
        report.longrepr='THR304 bounded report prefix retained; evidence INCOMPLETE'
        report.sections=[];report.outcome='failed'


def retain_node_receipt(node, name, value, cap=16384):
    directory=Path(_DESCRIPTOR['evidence'])/hashlib.sha256(node.encode()).hexdigest()[:20]
    write_receipt(directory/name,value,cap,evidence=Path(_DESCRIPTOR["evidence"]))


def genuine_scheduler_contender(orch,driver,task):
    """Actual ordinary run_step allocates next step/SID; provider exits without callback."""
    import threading
    errors=[]
    def run():
        try:orch.run_step(task)
        except BaseException as exc:errors.append(type(exc).__name__)
    thread=threading.Thread(target=run)
    thread.start()
    launch=driver.launch(task)
    current=orch._db.get_task(task)
    assert current.current_session_id==launch['session_id'] and current.status==TaskStatus.IN_PROGRESS
    return thread,launch,errors


def database_path_root(database):
    rows=database._conn.execute("PRAGMA database_list").fetchall()
    path=Path(next(row[2] for row in rows if row[1]=="main"))
    assert path.resolve()==path and path.name=="happyranch.db"
    return path.parent


def _case_endpoint(driver,*,wait_log=None):
    from runtime.daemon import paths as daemon_paths
    pid=int(daemon_paths.pid_file().read_text().strip())
    log=daemon_paths.log_file().resolve()
    suffix='-batch-'+str(driver.batch_index) if hasattr(driver,'batch_index') else ''
    receipt=Path(_DESCRIPTOR['evidence'])/('case-close-'+hashlib.sha256(driver.node.encode()).hexdigest()[:20]+suffix+'.json')
    phase(driver.node,'setup','endpoint',daemon_pid=pid,port=int(driver.port),
          daemon_log=str(log),closure=str(receipt),
          wait_log=wait_log or str(Path(_DESCRIPTOR['evidence'])/('waits-'+hashlib.sha256(driver.node.encode()).hexdigest()[:20]+'.jsonl')))
    return pid,log,receipt


@contextmanager
def consumer_observation(orch,task,result_id):
    """Read-only invocation counts in the sole supplemental owner thread."""
    from runtime.orchestrator import run_step as module
    from runtime.daemon.queue import TaskQueue
    targets={getattr(module,name).__code__:name for name in (
        '_consume_completion_report','_consume_completion_report_body',
        '_attachment_feedback_and_reenqueue','_spawn_fanout_children','_prepare_attachment_params')}
    targets[TaskQueue.put_nowait.__code__]='queue_initiation'
    counts={name:0 for name in targets.values()}
    prior=sys.gettrace()
    if prior is not None:raise RuntimeError('supplemental observation already owned')
    def trace(frame,event,arg):
        if event=='call' and frame.f_code in targets:
            counts[targets[frame.f_code]]+=1
        return trace
    sys.settrace(trace)
    try:yield counts
    finally:
        sys.settrace(prior)
        assert counts['_consume_completion_report']==1
        assert all(value==0 for name,value in counts.items() if name!='_consume_completion_report')



def retain_role_receipts(controls,node,suffix):
    rows=[]
    for path in sorted(controls.glob('launch-*.json')):
        launch=closed_read(path,16384)
        sid=launch['session_id']
        item={'launch':launch}
        for kind in ('provider','callback','provider-exit','provider-wait'):
            selected=controls/f'{kind}-{sid}.json'
            if selected.exists():item[kind]=closed_read(selected,4096)
        rows.append(item)
    if len(rows)>128:raise RuntimeError('required role receipt inventory cap')
    target=Path(_DESCRIPTOR['evidence'])/('roles-'+hashlib.sha256(node.encode()).hexdigest()[:20]+'-'+suffix+'.json')
    write_receipt(target,{'node':node,'head':_DESCRIPTOR['head'],'rows':rows},262144,
                  evidence=Path(_DESCRIPTOR['evidence']))
    return target

# Supplemental finding checks run only inside the already released C01/direct
# call. They are not original connected observations or product execution facts.
@contextmanager
def harness_source_gate(function, anchor, action, *, occurrence=1, required=True):
    """Gate an exact current harness code line; no runtime claim or code rewrite."""
    import dis
    import inspect
    path=Path(inspect.getsourcefile(function)).resolve()
    source=Path(_DESCRIPTOR['source'])
    name=path.relative_to(source).as_posix()
    assert name in _DESCRIPTOR['source_hashes'] and sha(path)==_DESCRIPTOR['source_hashes'][name]
    lines,first=inspect.getsourcelines(function)
    matches=[first+i for i,line in enumerate(lines) if anchor in line]
    assert len(matches)>=occurrence and occurrence>0
    line=matches[occurrence-1]
    assert line in {number for _,number in dis.findlinestarts(function.__code__) if number is not None}
    prior=sys.gettrace();hits=[]
    def trace(frame,event,arg):
        if prior is not None:prior(frame,event,arg)
        if frame.f_code is function.__code__ and event=='line' and frame.f_lineno==line:
            hits.append({'source':name,'sha256':sha(path),'line':line})
            if len(hits)==1:action(frame)
        return trace
    try:
        sys.settrace(trace)
        yield hits
    finally:sys.settrace(prior)
    assert hits or not required, 'required exact-source action never reached'


@contextmanager
def _finding_patch(owner, **values):
    original={key:getattr(owner,key) for key in values}
    try:
        for key,value in values.items():setattr(owner,key,value)
        yield
    finally:
        for key,value in original.items():setattr(owner,key,value)


def _finding_sink(root):
    from scripts import attachment_scoped_qa as qa
    root.mkdir(mode=0o700)
    fd=os.open(root/qa.EVIDENCE_LOCK,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600);os.close(fd)
    return root


def _finding_assert_raises(action, exception=ValueError):
    try:action()
    except exception as exc:return str(exc)
    raise AssertionError('required refusal absent')


def _finding_subprocess(spawn):
    """Complete local dependency view; shared stdlib globals stay untouched."""
    from types import SimpleNamespace
    # wait_receipts compares the actual stdlib wait code, not the spawn adapter.
    spawn.wait = subprocess.Popen.wait
    local = SimpleNamespace(**{**vars(subprocess), 'Popen': spawn})
    assert local.PIPE is subprocess.PIPE and local.STDOUT is subprocess.STDOUT
    assert local.TimeoutExpired is subprocess.TimeoutExpired
    assert local.SubprocessError is subprocess.SubprocessError
    assert local.Popen.wait is subprocess.Popen.wait
    return local


def _finding_diagnostic_trial(root, kind, fault=None, payload=b'\xff\xe2\x82\xac'):
    """Synthetic harness-only sink/commands; never a task/result/callback claim."""
    from types import SimpleNamespace
    module=sys.modules[__name__]
    sink=_finding_sink(root/'evidence');control=root/'control';control.mkdir(mode=0o700)
    target=sink/'diagnostic.json';info=sink.stat();identity=(info.st_dev,info.st_ino)
    faults=set() if fault is None else set(fault.split('+'));primary=None if fault is None else fault.split('+')[0]
    trial_deadline=min(_CALL_DEADLINE,time.monotonic()+.5)
    node=NODES[0];observer={'node':node,'binding':{'revision':_DESCRIPTOR['head']}}
    # Synthetic identity is explicitly restricted to diagnostic scratch. No
    # daemon assignment, result or callback is created or asserted by this trial.
    task='synthetic-harness';sid='synthetic-harness';slug='test';processes=[];closes=[]
    argv=['harness','provider',task,sid,'dev_agent',slug,str(control)]
    write_receipt(control/f'command-{sid}.json',{'body':{},'exit':True},16384)
    (control/'observer').mkdir(mode=0o700)
    write_receipt(control/'observer'/'descriptor.json',observer,16384)
    real_popen=subprocess.Popen;real_selector=selectors.DefaultSelector
    injected={name:OSError('finding-primary-'+name) for name in faults}
    waits=[];seams=[];acquired_selectors=[]
    class Child(real_popen):
        def wait(self,timeout=None):
            remaining=max(0,trial_deadline-time.monotonic())
            value=super().wait(timeout=remaining if timeout is None else min(timeout,remaining))
            waits.append(value);return value
    def spawn(*args,**kwargs):
        assert kwargs['stdout'] is subprocess.PIPE and kwargs['stderr'] is subprocess.PIPE
        seams.append('spawn')
        if 'spawn' in faults:raise injected['spawn']
        code='import os,time;os.write(1,'+repr(payload[:2])+');time.sleep(.01);os.write(1,'+repr(payload[2:])+');os.write(2,b"\\xfe")'
        if payload==b'\xff\xe2\x82\xac':
            read_fd,write_fd=os.pipe();sync_fds.append(write_fd)
            code='import os;os.write(1,'+repr(payload[:2])+');assert os.read('+str(read_fd)+',1)==b"R";os.write(1,'+repr(payload[2:])+');os.write(2,b"\\xfe")'
            try:child=Child([sys.executable,'-c',code],stdout=subprocess.PIPE,stderr=subprocess.PIPE,pass_fds=(read_fd,))
            finally:os.close(read_fd)
        else:child=Child([sys.executable,'-c',code],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        processes.append(child);return child
    class Selected:
        def __init__(self):
            seams.append('selector-create')
            if 'selector-create' in faults:raise injected['selector-create']
            self.actual=real_selector();acquired_selectors.append(self)
        def register(self,*args):
            seams.append('selector-register')
            if 'selector-register' in faults:raise injected['selector-register']
            return self.actual.register(*args)
        def select(self,timeout=None):
            remaining=trial_deadline-time.monotonic()
            if remaining<=0:raise TimeoutError('finding diagnostic deadline')
            return self.actual.select(remaining if timeout is None else min(timeout,remaining))
        def __getattr__(self,name):return getattr(self.actual,name)
        def close(self):
            self.actual.close();closes.append('selector')
            if 'close' in faults:raise OSError('finding-secondary-close')
    def admission():
        # Parent/source guard was admitted before this helper. This is solely
        # a negative harness admission, not successful provider admission proof.
        if 'admission' in faults:raise injected['admission']
    original_checkpoint=_diagnostic_checkpoint
    calls=[];attempted_rows=[];sync_fds=[];split_reads=[]
    def checkpoint(*args):
        calls.append(args[2]['stage']);attempted_rows.append(json.loads(json.dumps(args[2])))
        size=args[2]['output']['stdout']['raw_length']
        if size and (not split_reads or split_reads[-1]!=size):split_reads.append(size)
        if sync_fds and size==2:
            fd=sync_fds.pop();os.write(fd,b'R');os.close(fd)
        if 'publication' in faults and len(calls)>1:raise injected['publication']
        return original_checkpoint(*args)
    failure=None;rows=[]
    # Replace only fixture references, not shared stdlib module attributes or
    # product code. The actual capture/cleanup/checkpoint implementation runs.
    with _finding_patch(module,_provider_diagnostic=lambda kind:(task,sid,'dev_agent',slug,control,observer,target,identity),
        _provider_admission=admission,manifest=lambda:{'revision':_DESCRIPTOR['head'],'callback':'synthetic-unexecuted-callback'},
        subprocess=_finding_subprocess(spawn),
        selectors=SimpleNamespace(DefaultSelector=Selected,EVENT_READ=selectors.EVENT_READ),_diagnostic_checkpoint=checkpoint):
        with _finding_patch(sys,argv=argv,stdin=__import__('io').StringIO('')), _finding_patch(os,environ=dict(os.environ,
            HAPPYRANCH_RUNTIME_SESSION_ID=sid,HAPPYRANCH_ORG_SLUG=slug,HAPPYRANCH_ATTACHMENT_OBSERVER=str(control/'observer'/'descriptor.json'))):
            try:(provider_supervise if kind=='provider' else provider_main)()
            except BaseException as exc:failure=exc
    for fd in sync_fds:os.close(fd)
    if target.exists():rows.append(closed_read(target,262144))
    if 'publication' in faults:
        assert rows and rows[-1]['complete'] is False
        rows.append(attempted_rows[-1])
    assert failure is not None
    if isinstance(failure,SystemExit):assert failure.code==0
    if primary and primary!='close':
        assert failure is injected[primary], 'owner must preserve the actual first native exception'
    if 'publication' in faults:
        assert len(calls)>1 and attempted_rows[-1]['complete'] is False
        assert any('finding-primary-publication' in error for error in attempted_rows[-1]['cleanup_errors'])
        assert rows[0]['complete'] is False and rows[0]!=attempted_rows[-1]
    if 'close' in faults:
        assert closes==['selector'] and any('finding-secondary-close' in error for error in rows[-1]['cleanup_errors'])
    if ('admission' in faults and kind=='provider') or 'spawn' in faults:
        row=rows[-1];assert row['child_pid'] is None and row['parent_wait_returncode'] is None and row['child_state']=='absent'
        assert row['complete'] is False and not processes and not waits
    elif processes:
        child=processes[0];row=rows[-1]
        assert row['child_pid']==child.pid and type(row['parent_wait_returncode']) is int
        assert row['child_state']=='waited' and child.returncode==row['parent_wait_returncode']
        assert waits and waits[-1]==row['parent_wait_returncode']
        assert child.stdout.closed and child.stderr.closed
        if fault:assert row['complete'] is False
        if primary not in ('publication','close') and primary:
            assert 'finding-primary-'+primary in row['error'] and not isinstance(failure,SystemExit)
        if 'close' in faults:assert row['cleanup_errors'] and not isinstance(failure,SystemExit)
    if 'spawn' in faults:assert seams==['spawn']
    if 'selector-create' in faults:assert seams==['spawn','selector-create']
    if 'selector-register' in faults:assert seams==['spawn','selector-create','selector-register']
    for selected in acquired_selectors:assert not selected.actual.get_map()
    assert time.monotonic()<trial_deadline, 'diagnostic trial exceeded the remaining call budget'
    return rows[-1] if rows else None, {'kind':kind,'fault':fault,'failure':type(failure).__name__,
        'intended_seams':seams,'spawned':len(processes),'consuming_waits':waits,'selector_closes':closes,'checkpoint_stages':calls,'split_read_lengths':split_reads,'stored_final':'publication' not in faults}


def assert_finding_admission(root):
    row,facts=_finding_diagnostic_trial(root,'provider','admission')
    assert row['stage']=='provider-admission' and 'finding-primary-admission' in row['error']
    assert facts['failure']=='OSError' and row['complete'] is False
    return facts


def assert_finding_popen(root):
    facts=[]
    for kind in ('provider','callback'):
        case=root/kind;case.mkdir(mode=0o700)
        row,receipt=_finding_diagnostic_trial(case,kind,'spawn')
        assert row['stage']==kind+'-spawn' and 'finding-primary-spawn' in row['error']
        assert receipt['failure']=='OSError' and row['cleanup_errors']==[]
        facts.append(receipt)
    return facts


def assert_finding_selector(root):
    facts=[]
    for kind in ('provider','callback'):
        for fault in ('selector-create','selector-register'):
            case=root/(kind+'-'+fault);case.mkdir(mode=0o700)
            row,receipt=_finding_diagnostic_trial(case,kind,fault)
            assert row['stage']==kind+'-selector' and receipt['spawned']==1 and receipt['failure']=='OSError'
            if fault=='selector-register':assert receipt['selector_closes']==['selector']
            facts.append(receipt)
    return facts


def assert_finding_raw(root):
    facts=[];payload=b'\xff\xe2\x82\xac'
    for kind in ('provider','callback'):
        case=root/kind;case.mkdir(mode=0o700)
        row,receipt=_finding_diagnostic_trial(case,kind,payload=payload)
        for channel,original in (('stdout',payload),('stderr',b'\xfe')):
            output=row['output'][channel];raw=base64.b64decode(output['raw_base64'],validate=True)
            assert raw==original and output['raw_length']==len(original)
            assert output['observed_raw_length']==len(original) and output['raw_sha256']==hashlib.sha256(original).hexdigest()
            assert output['preview_utf8_replacement']==original.decode('utf-8','replace')
            assert output['preview_utf8_replacement'].encode()!=original
        assert row['complete'] is True and receipt['failure']=='SystemExit'
        assert receipt['split_read_lengths']==[2,4]
        facts.append(receipt)
    return facts


@contextmanager
def _finding_owned_worker(action):
    """Finite exact child edge, real waitpid; absence never stands in for wait."""
    ready_r,ready_w=os.pipe();release_r,release_w=os.pipe()
    pid=os.fork()
    if pid==0:
        os.close(ready_r);os.close(release_w)
        try:
            action(ready_w,release_r)
            os._exit(0)
        except BaseException as exc:
            os.write(ready_w,('ERROR:'+type(exc).__name__+':'+str(exc)[:256]).encode())
            os._exit(1)
    os.close(ready_w);os.close(release_r);waited=None
    try:
        yield pid,ready_r,release_w
    finally:
        # Wait only on this genuine fork child. Bounded terminal wait, then
        # exact-child kill and consuming wait if the finite action did not exit.
        deadline=min(_CALL_DEADLINE,time.monotonic()+1)
        while time.monotonic()<deadline:
            got,status=os.waitpid(pid,os.WNOHANG)
            if got:waited=status;break
            time.sleep(.005)
        if waited is None:
            os.kill(pid,signal.SIGKILL);got,waited=os.waitpid(pid,0);assert got==pid
        os.close(ready_r);os.close(release_w)
        assert os.waitstatus_to_exitcode(waited)==0, 'owned supplemental worker failed/nonzero'


def _finding_message(fd,expected):
    assert select.select([fd],[],[],max(0,min(.1,_CALL_DEADLINE-time.monotonic())))[0], 'finite finding gate timeout'
    assert os.read(fd,4096)==expected


def _finding_prewrite_refusal(action):
    from scripts import attachment_scoped_qa as qa
    with harness_source_gate(qa.evidence_publish,'fd=os.open(temporary',lambda frame:None,required=False) as opened:
        with harness_source_gate(qa.evidence_publish,'if new_directory:path.parent.mkdir',lambda frame:None,required=False) as directories:
            with harness_source_gate(qa.evidence_append,'fd=os.open(path',lambda frame:None,required=False) as appends:
                error=_finding_assert_raises(action)
    assert not opened and not directories and not appends, 'refusal followed a protected write/open/mkdir'
    return error


def _finding_capacity(root):
    from scripts import attachment_scoped_qa as qa
    peaks=[];gates=[]
    def inventory(sink):
        value=qa.evidence_usage(sink);peaks.append(value[:2]);assert value[0]<=CAPS['aggregate'] and value[1]<=4096
        return value
    def fill(sink,size):
        # Synthetic scratch only; real physical inventory, never counterfeit
        # final evidence or product receipts. Each file obeys the1MiB cap.
        index=0
        while size:
            count=min(size,CAPS['metadata']);path=sink/f'padding-{index}'
            fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            try:assert os.write(fd,b'x'*count)==count
            finally:os.close(fd)
            size-=count;index+=1
    byte_sink=_finding_sink(root/'bytes')
    for name in ('A','B'):qa.evidence_publish(byte_sink/name,b'x'*1024,16384,byte_sink)
    T=CAPS['aggregate']-sum(qa.FINAL_CAPS.values())-12288
    fill(byte_sink,T-2048);assert inventory(byte_sink)[0]==T
    def producer(ready,release):
        def held(frame):
            os.write(ready,b'TEMP')
            assert select.select([release],[],[],.1)[0]
            assert os.read(release,1)==b'R'
        with harness_source_gate(qa.evidence_publish,'os.replace(temporary',held) as hits:
            qa.evidence_publish(byte_sink/'A',b'A'*8192,16384,byte_sink,replace=True)
        assert hits
    with _finding_owned_worker(producer) as (pid,ready,release):
        _finding_message(ready,b'TEMP')
        assert (byte_sink/'A').stat().st_size==1024 and (byte_sink/'A.tmp').stat().st_size==8192
        assert inventory(byte_sink)[0]==T+8192
        # Start a distinct real producer while A owns the permanent lock.
        def competing(ready2,release2):
            os.write(ready2,b'ENTER')
            _finding_prewrite_refusal(lambda:qa.evidence_publish(byte_sink/'B',b'B'*8192,16384,byte_sink,replace=True))
        with _finding_owned_worker(competing) as (pid2,ready2,release2):
            _finding_message(ready2,b'ENTER')
            def append_producer(r,w):
                os.write(r,b'ENTER')
                qa.evidence_append(byte_sink/'journal',b'J'*1024,2048,byte_sink)
            with _finding_owned_worker(append_producer) as (pid3,r3,w3):
                _finding_message(r3,b'ENTER');os.write(release,b'R')
        gates.append({'owner':pid,'competitor':pid2,'append_producer':pid3})
    assert (byte_sink/'B').read_bytes()==b'x'*1024 and not (byte_sink/'B.tmp').exists()
    before=inventory(byte_sink)[0]
    _finding_prewrite_refusal(lambda:qa.evidence_append(byte_sink/'journal',b'X'*8192,16384,byte_sink))
    assert (byte_sink/'journal').read_bytes()==b'J'*1024 and inventory(byte_sink)[0]==before
    (byte_sink/'padding-0').unlink()
    qa.evidence_publish(byte_sink/'B',b'B'*8192,16384,byte_sink,replace=True)
    assert (byte_sink/'B').read_bytes()==b'B'*8192
    entries=_finding_sink(root/'entries')
    for i in range(4089):
        fd=os.open(entries/f'p-{i}',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600);os.close(fd)
    assert inventory(entries)[1]==4090
    nested=entries/('a'*20)/'report.json'
    _finding_prewrite_refusal(lambda:qa.evidence_publish(nested,b'{}',100,entries))
    assert not nested.parent.exists() and inventory(entries)[1]==4090
    for name in ('p-0','p-1'):
        with harness_source_gate(qa.evidence_publish,'os.replace(temporary',lambda frame:inventory(entries)):
            qa.evidence_publish(entries/name,b'{}',100,entries,replace=True)
        assert (entries/name).read_bytes()==b'{}'
    source_log=root/'snapshot-source.log'
    fd=os.open(source_log,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    try:assert os.write(fd,b'S'*1024)==1024
    finally:os.close(fd)
    snapshot=qa.OwnedTree(entries)
    snapshot.logs={str(source_log):{'log':str(source_log),'log_identity':None,'node':NODES[0]}}
    def new_target(ready,release):
        def hold(frame):
            os.write(ready,b'TEMP');assert select.select([release],[],[],.1)[0];assert os.read(release,1)==b'R'
        with harness_source_gate(qa.evidence_publish,'os.replace(temporary',hold):
            qa.evidence_publish(entries/'new',b'{}',100,entries)
    with _finding_owned_worker(new_target) as (owner,r,w):
        _finding_message(r,b'TEMP');assert inventory(entries)[1]==4091
        def competitor(ready,release):
            os.write(ready,b'ENTER')
            _finding_prewrite_refusal(lambda:qa.evidence_publish(entries/'refused-new',b'{}',100,entries))
        def snapshot_producer(ready,release):
            os.write(ready,b'ENTER');_finding_prewrite_refusal(snapshot.capture_logs)
        with _finding_owned_worker(competitor) as (other,r2,w2):
            _finding_message(r2,b'ENTER')
            with _finding_owned_worker(snapshot_producer) as (writer,r3,w3):
                _finding_message(r3,b'ENTER');os.write(w,b'R')
        gates.append({'entry_owner':owner,'entry_competitor':other,'snapshot_producer':writer})
    assert inventory(entries)[1]==4091
    assert not (entries/'refused-new').exists() and not (entries/'refused-new.tmp').exists()
    _finding_prewrite_refusal(lambda:qa.evidence_publish(entries/'p-0',b'XX',100,entries,replace=True))
    assert (entries/'p-0').read_bytes()==b'{}' and not (entries/'p-0.tmp').exists()
    assert not any(path.name.startswith('daemon-') for path in entries.iterdir())
    for i in range(4):
        fd=os.open(entries/f'reserved-pressure-{i}',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600);os.close(fd)
    assert inventory(entries)[1]==4095
    _finding_prewrite_refusal(lambda:qa.evidence_publish(entries/'refused',b'{}',100,entries))
    assert not (entries/'refused').exists() and not (entries/'refused.tmp').exists()
    assert inventory(entries)[1]==4095
    # Remove only synthetic owned filler, then reach directory+temporary peak
    # through the exact source line while the common allocation is held.
    for i in range(10):(entries/f'p-{i+10}').unlink()
    def directory_peak(frame):
        assert nested.parent.is_dir() and nested.with_name('report.json.tmp').exists()
        inventory(entries)
    with harness_source_gate(qa.evidence_publish,'os.replace(temporary',directory_peak) as hits:
        qa.evidence_publish(nested,b'{}',100,entries)
    assert hits and nested.read_bytes()==b'{}'
    snapshot.capture_logs()
    assert any(path.name.startswith('daemon-') and path.read_bytes()==b'S'*1024 for path in entries.iterdir())
    return {'peaks':peaks,'gates':gates,'byte_target_restored':True,'entry_target_restored':True}


def _finding_lock_and_cleanup(root):
    from scripts import attachment_scoped_qa as qa
    sink=_finding_sink(root/'lock');lock=sink/qa.EVIDENCE_LOCK;identity=(lock.stat().st_dev,lock.stat().st_ino)
    def holder(ready,release):
        with qa.evidence_allocation(sink):
            os.write(ready,b'LOCKED')
            assert select.select([release],[],[],1)[0];assert os.read(release,1)==b'R'
    with _finding_owned_worker(holder) as (pid,ready,release):
        _finding_message(ready,b'LOCKED');start=time.monotonic()
        _finding_assert_raises(lambda:qa.evidence_publish(sink/'blocked',b'x',100,sink),TimeoutError)
        elapsed=time.monotonic()-start;assert .25<=elapsed<1
        assert not (sink/'blocked').exists() and not (sink/'blocked.tmp').exists()
        os.write(release,b'R')
    assert (lock.stat().st_dev,lock.stat().st_ino)==identity and qa.evidence_usage(sink)[1]==1
    def interrupted(frame):raise InterruptedError('finding-primary-interruption')
    with harness_source_gate(qa.evidence_publish,'os.replace(temporary',interrupted) as hits:
        error=_finding_assert_raises(lambda:qa.evidence_publish(sink/'caught',b'x',100,sink),InterruptedError)
    assert 'primary-interruption' in error and hits and not (sink/'caught.tmp').exists()
    def catchable(ready,release):
        def signal_error(signum,frame):raise InterruptedError('finding-primary-caught-signal')
        signal.signal(signal.SIGUSR1,signal_error)
        def signal_gate(frame):
            os.write(ready,b'TEMP')
            assert select.select([release],[],[],.1)[0]
            os.read(release,1)
        try:
            with harness_source_gate(qa.evidence_publish,'os.replace(temporary',signal_gate):
                qa.evidence_publish(sink/'signal-caught',b'x',100,sink)
        except InterruptedError as exc:
            assert 'primary-caught-signal' in str(exc) and not (sink/'signal-caught.tmp').exists()
            os.write(ready,b'CLEAN')
        else:raise AssertionError('caught signal absent')
    with _finding_owned_worker(catchable) as (caught_pid,ready,release):
        _finding_message(ready,b'TEMP');os.kill(caught_pid,signal.SIGUSR1);_finding_message(ready,b'CLEAN')
    qa.evidence_publish(sink/'after',b'x',100,sink)
    # An actual exact-child uncatchable interruption leaves its temp physically
    # charged; wait status is consumed and asserted independently.
    ready_r,ready_w=os.pipe();pid=os.fork()
    if pid==0:
        os.close(ready_r)
        def killed(frame):
            os.write(ready_w,b'TEMP');time.sleep(1);raise TimeoutError('kill gate expired')
        try:
            with harness_source_gate(qa.evidence_publish,'os.replace(temporary',killed):qa.evidence_publish(sink/'orphan',b'O'*100,100,sink)
        finally:os._exit(2)
    os.close(ready_w)
    try:
        _finding_message(ready_r,b'TEMP');os.kill(pid,signal.SIGKILL)
        got,status=os.waitpid(pid,0);assert got==pid and os.waitstatus_to_exitcode(status)==-signal.SIGKILL
    finally:os.close(ready_r)
    assert (sink/'orphan.tmp').stat().st_size==100
    with qa.evidence_allocation(sink):assert qa.evidence_usage(sink)[2]['orphan.tmp']==100
    tree=qa.OwnedTree(sink)
    assert 'interrupted evidence publication remains' in _finding_assert_raises(tree.monitor)
    assert (lock.stat().st_dev,lock.stat().st_ino)==identity
    return {'lock_identity':identity,'timeout_elapsed':elapsed,'caught_signal_child':caught_pid,'killed_child':pid,'consumed_status':status,'orphan_bytes':100}


def _finding_terminal_channels(root):
    from scripts import attachment_scoped_qa as qa
    sink=_finding_sink(root/'channels');events=[];failures=[]
    capture=qa.CaptureChannels(sink,events.append)
    phase_pipe=root/'phase.fifo';junit_pipe=root/'junit.fifo'
    for path in (phase_pipe,junit_pipe):os.mkfifo(path,0o600)
    phase_fd=os.open(phase_pipe,os.O_RDWR|os.O_NONBLOCK);junit_fd=os.open(junit_pipe,os.O_RDWR|os.O_NONBLOCK)
    raw=b'queued-final-stdout\xff';frame=b'{"event":"finish","phase":"teardown"}\n';xml=b'<testsuites><testsuite/></testsuites>'
    code='import os;os.write(1,'+repr(raw)+');p=os.open('+repr(str(phase_pipe))+',os.O_WRONLY);os.write(p,'+repr(frame)+');os.close(p);j=os.open('+repr(str(junit_pipe))+',os.O_WRONLY);os.write(j,'+repr(xml)+');os.close(j)'
    child=subprocess.Popen([sys.executable,'-c',code],stdout=subprocess.PIPE)
    def attempt(label,action):
        try:return action()
        except BaseException as exc:failures.append(label+':'+str(exc));capture.failed.add(label.split(' ',1)[1])
    try:
        waited=child.wait(timeout=max(0,min(1,_CALL_DEADLINE-time.monotonic())));assert waited==0
        for channel,fd in (('output',child.stdout.fileno()),('phase',phase_fd),('junit',junit_fd)):
            capture.drain(channel,fd,True,attempt,deadline=_CALL_DEADLINE)
        assert not failures and capture.junit==xml and (sink/'pytest.log').read_bytes()==raw
        assert events==[json.loads(frame)] and all(row['complete'] for row in capture.drains.values())
        assert capture.drains['output']['read_state']=='EOF'
        assert capture.drains['phase']['read_state']=='EAGAIN' and capture.drains['junit']['read_state']=='EAGAIN'
        qa.evidence_publish(sink/'cases.xml',bytes(capture.junit),CAPS['junit'],sink)
        # These are explicitly synthetic final-reservation exercise files, not
        # successful product observations or process/wait receipts.
        for name,cap in qa.FINAL_CAPS.items():
            if name!='cases.xml':qa.evidence_publish(sink/name,b'{}',cap,sink)
        _,_,files=qa.evidence_usage(sink);assert set(qa.FINAL_CAPS)<=set(files)
        _finding_assert_raises(lambda:qa.evidence_publish(sink/'result.json',b'{}',100,sink),FileExistsError)
        from _pytest import junitxml
        import xml.etree.ElementTree as ET
        junit_source=Path(junitxml.__file__).resolve()
        assert pytest.__version__=='9.0.3' and sha(junit_source)=='d71198950cf33bd1c7dc782167986ee130427fc57e221046e6455dc7a5b63d5a'
        independent=qa.CaptureChannels(_finding_sink(root/'pytest-junit'),events.append)
        def pytest_writer(ready,release):
            writer=junitxml.LogXML(str(junit_pipe),None)
            writer.pytest_sessionstart();writer.pytest_sessionfinish();os.write(ready,b'JUNIT')
        with _finding_owned_worker(pytest_writer) as (junit_pid,ready,release):_finding_message(ready,b'JUNIT')
        independent.drain('junit',junit_fd,True,attempt,deadline=_CALL_DEADLINE)
        assert independent.drains['junit']['complete'] and not failures
        actual_xml=bytes(independent.junit);assert actual_xml.startswith(b'<?xml')
        parsed=ET.fromstring(actual_xml);assert parsed.tag=='testsuites' and parsed.find('testsuite').get('tests')=='0'
        qa.evidence_publish(independent.evidence/'cases.xml',actual_xml,CAPS['junit'],independent.evidence)
        assert (independent.evidence/'cases.xml').read_bytes()==actual_xml
        partial=qa.CaptureChannels(sink,events.append);os.write(phase_fd,b'{"event":')
        _finding_assert_raises(lambda:partial.drain('phase',phase_fd,True,attempt,deadline=_CALL_DEADLINE))
        assert partial.drains['phase']['frame_complete'] is False and not partial.drains['phase']['complete']
    finally:
        child.stdout.close();os.close(phase_fd);os.close(junit_fd)
    assert child.stdout.closed
    return {'actual_wait':waited,'child':child.pid,'stdout_sha256':hashlib.sha256(raw).hexdigest(),
        'phase_events':events,'junit_sha256':hashlib.sha256(xml).hexdigest(),'drains':capture.drains,'finals':list(qa.FINAL_CAPS),'independent_pytest_writer':{'pid':junit_pid,'source_sha256':sha(junit_source),
        'raw_sha256':hashlib.sha256(actual_xml).hexdigest(),'drain':independent.drains['junit']}}


def _finding_outer_lifecycle(root):
    from scripts import attachment_scoped_qa as qa
    from types import SimpleNamespace
    facts=[];real_popen=subprocess.Popen;real_selector=selectors.DefaultSelector
    for fault in ('spawn','discover','selector-create','selector-register','close','publication','selector-register+publication+pidfd-close+phase-close+junit-close','fifo-open','source-publication','output-publication','phase-parse',
                  'tree-cleanup+terminal-output+publication+pidfd-close',
                  'phase-malformed+tree-phase','phase-action+tree-phase','phase-malformed+parse-deadline',
                  'tree-overflow+close+publication+pidfd-close'):
        trial_deadline=min(_CALL_DEADLINE,time.monotonic()+.5)
        faults=set(fault.split('+'));primary=fault.split('+')[0]
        case=root/fault;case.mkdir(mode=0o700);owned=[];selectors_acquired=[];published=[];pidfds=set();fifo_fds={};fifo_names=[];closed_fifos=[]
        constructions=[];injections=[];bound=[];observations=[];waits=[];gates=[];native=[]
        injected={name:OSError('finding-primary-'+name) for name in ('spawn','selector-create','selector-register')}
        cleanup_error=PermissionError('finding-primary-tree-cleanup')
        phase_error=ValueError('finding-primary-phase-action')
        tree_error=PermissionError('finding-later-phase-tree')
        overflow_error=PermissionError('finding-primary-overflow-'+('X'*2048))
        recorded=[];earliest=[];trees=[];captures=[];diagnostics={};overflow_facts={};log_fds=set()
        def spy(record):
            def save(label,exc):
                value=record(label,exc)
                if not earliest:earliest.append(value)
                assert value is earliest[0], 'shared recorder replaced the actual earliest exception'
                if len(recorded)<qa.ERROR_LIMIT and not any(e is exc for _,e in recorded):recorded.append((label,exc))
                return value
            return save
        class Channels(qa.CaptureChannels):
            def __init__(self,evidence,on_phase,record=None):
                shared=spy(record)
                def phase_record(label,exc):
                    value=shared(label,exc)
                    if 'parse-deadline' in faults and label=='phase parse':
                        # Select and actually exhaust the parser's real bound
                        # after recording the malformed frame, before the next.
                        self.processing_deadline=min(_CALL_DEADLINE,time.monotonic()+.001)
                        while time.monotonic()<=self.processing_deadline:
                            time.sleep(min(.0002,max(0,self.processing_deadline-time.monotonic())))
                        assert time.monotonic()<_CALL_DEADLINE
                    return value
                def action(row):
                    if primary=='phase-action' and row.get('event')=='finding-refusal':raise phase_error
                    return on_phase(row)
                super().__init__(evidence,action,phase_record);captures.append(self)
        def release():
            if gates:
                fd=gates.pop();os.write(fd,b'R');os.close(fd)
        class Child(real_popen):
            def wait(self,timeout=None):
                remaining=max(0,trial_deadline-time.monotonic())
                value=super().wait(timeout=remaining if timeout is None else min(timeout,remaining))
                waits.append(value);return value
        def spawn(*args,**kwargs):
            assert kwargs['stdout'] is subprocess.PIPE and kwargs['stderr'] is subprocess.STDOUT
            assert qa.subprocess.Popen.wait is real_popen.wait
            if 'spawn' in faults:raise injected['spawn']
            descriptor_path=next(value.split('=',1)[1] for value in args[0] if value.startswith('--attachment-qa-descriptor='))
            descriptor=closed_read(Path(descriptor_path),16384)
            frame=(json.dumps({'nonce':descriptor['nonce'],'node':NODES[0],'phase':'teardown','event':'finish'})+'\n').encode()
            if 'phase-parse' in faults:frame=b'not-json\n'+frame
            if primary in ('phase-malformed','phase-action'):
                if 'tree-phase' in faults:
                    log=Path(descriptor['scratch'])/'daemon'/'daemon.log'
                    log.parent.mkdir(mode=0o700);log.write_bytes(b'owned synthetic log')
                    later=(json.dumps({'nonce':descriptor['nonce'],'node':NODES[0],
                        'phase':'call','event':'daemon-log','daemon_log':str(log)})+'\n').encode()
                else:later=b''
                refusal=b'not-json\n' if primary=='phase-malformed' else (json.dumps({
                    'nonce':descriptor['nonce'],'node':NODES[0],'phase':'call','event':'finding-refusal'})+'\n').encode()
                frame=refusal+later+frame
            junit_path=next(value.split('=',1)[1] for value in args[0] if value.startswith('--junitxml='))
            ready_r,ready_w=os.pipe();release_r,release_w=os.pipe();gates.append(release_w)
            code=('import os,select;os.write('+str(ready_w)+',b"READY");'
                'assert select.select(['+str(release_r)+'],[],[],.5)[0];'
                'assert os.read('+str(release_r)+',1)==b"R";'
                'os.write(1,b"queued");p=os.open('+repr(descriptor['phase_pipe'])+',os.O_WRONLY);'
                'os.write(p,'+repr(frame)+');os.close(p);j=os.open('+repr(junit_path)+',os.O_WRONLY);'
                'os.write(j,b"<testsuites/>");os.close(j)')
            try:
                child=Child([sys.executable,'-c',code],stdout=subprocess.PIPE,pass_fds=(ready_w,release_r))
                owned.append(child);_finding_message(ready_r,b'READY')
            finally:os.close(ready_r);os.close(ready_w);os.close(release_r)
            if primary in ('output-publication','phase-parse'):
                release();assert child.wait(timeout=1)==0
            return child
        class Tree(qa.OwnedTree):
            in_cleanup=False
            def __init__(self,evidence,record=None):
                super().__init__(evidence,spy(record));trees.append(self);self.seeded=False
            def discover(self):
                super().discover();pidfds.update(row['fd'] for row in self.identities.values() if row.get('fd') is not None)
                for child in owned:
                    if child.pid in self.identities and child.pid not in bound:
                        row=self.identities[child.pid]
                        assert row['exited'] is False and row['reaped'] is False and child.poll() is None
                        bound.append(child.pid)
                if 'tree-overflow' in faults and not self.seeded:
                    self.seeded=True
                    for index in range(qa.ERROR_LIMIT+4):
                        exc=overflow_error if index==0 else OSError('finding-discover-'+str(index)+('X'*2048))
                        self.failure('discover '+str(index),exc)
                        self.failure('same propagation',exc)
                    assert len(self.errors)==len(self.reported)==qa.ERROR_LIMIT
                    assert max(map(len,self.errors))<=qa.ERROR_LABEL_LIMIT+1+256
                    assert self.reported[0] is overflow_error and self.error_overflow==8
                    rows=list(self.errors)
                    self.failure('retained again',overflow_error)
                    assert self.errors==rows and self.error_overflow==8
                    self.record('overflow propagation',exc);self.record('overflow propagation',exc)
                    for _ in range(qa.ERROR_OVERFLOW_LIMIT+1):self.failure('overflow replay',exc)
                    assert self.error_overflow==qa.ERROR_OVERFLOW_LIMIT and self.errors==rows
                    assert not getattr(overflow_error,'__notes__',())
                    note_error=OSError('bounded evidence cleanup notes')
                    for _ in range(qa.ERROR_LIMIT+4):qa.error_note(note_error,'X'*2048)
                    assert len(note_error.__notes__)==qa.ERROR_LIMIT
                    assert max(map(len,note_error.__notes__))<=qa.ERROR_TEXT_LIMIT
                    assert note_error.__notes__[-1]=='additional evidence cleanup notes truncated'
                    overflow_facts.update(fresh_errors=qa.ERROR_LIMIT+4,retained=len(rows),
                        observed_overflow_before_saturation=8,saturated=self.error_overflow,
                        meaning=qa.ERROR_OVERFLOW_MEANING,bounded_notes=len(note_error.__notes__))
            def children(self,pid):
                children=super().children(pid)
                return children & {p.pid for p in owned} if pid==self.self_pid else children
            def observe_exits(self,pid):
                super().observe_exits(pid)
                observations.append({'exited':{p:row['exited'] for p,row in self.identities.items()},
                    'indicated':[row['pid'] for row in self.events if row['kind']=='exit_indication']})
            def cleanup(self,*args,**kwargs):
                self.in_cleanup=True
                if primary=='tree-overflow':
                    retained=list(self.errors)
                    for index in range(4):self.failure('settlement fresh '+str(index),OSError('finding-settlement-'+str(index)))
                    assert self.errors==retained and self.error_overflow==qa.ERROR_OVERFLOW_LIMIT
                return super().cleanup(*args,**kwargs)
            def capture_logs(self):
                if 'tree-cleanup' in faults and self.in_cleanup and not native:
                    raise cleanup_error
                return super().capture_logs()
            def failure(self,label,exc):
                if len(native)<qa.ERROR_LIMIT:native.append((label,exc))
                super().failure(label,exc)
        class Selector:
            def __init__(self):
                # Binding/exit observers now acquire selectors before capture.
                self.outer=sys._getframe(1).f_code is qa.supervise.__code__
                constructions.append(self.outer)
                if self.outer and 'selector-create' in faults:
                    injections.append('selector-create');raise injected['selector-create']
                self.actual=real_selector();selectors_acquired.append(self);self.registrations=0
            def register(self,*args):
                self.registrations+=1
                if self.outer and self.registrations==1 and 'selector-register' in faults:
                    injections.append('selector-register');raise injected['selector-register']
                value=self.actual.register(*args)
                if self.outer and self.registrations==3:
                    release()
                    if primary in ('phase-malformed','phase-action'):
                        # Bind the real held child first, then guarantee the
                        # complete phase batch and JUnit are queued together.
                        assert owned[0].wait(timeout=max(0,cleanup_deadline-time.monotonic()))==0
                return value
            def __getattr__(self,name):return getattr(self.actual,name)
            def close(self):
                self.actual.close()
                if self.outer and 'close' in faults:raise OSError('finding-secondary-close')
        real_os=qa.os
        class OS:
            def __getattr__(self,name):return getattr(real_os,name)
            def open(self,path,*args,**kwargs):
                if 'fifo-open' in faults and Path(path).name=='phases.fifo':raise OSError('finding-primary-fifo-open')
                fd=real_os.open(path,*args,**kwargs)
                if Path(path).name in ('phases.fifo','junit.fifo'):
                    fifo_fds[fd]=Path(path).name;fifo_names.append(Path(path).name)
                if Path(path).name=='daemon.log':log_fds.add(fd)
                return fd
            def fdopen(self,fd,*args,**kwargs):
                actual=real_os.fdopen(fd,*args,**kwargs)
                if fd not in log_fds or 'tree-phase' not in faults:return actual
                class Read:
                    def __enter__(self):actual.__enter__();return self
                    def __exit__(self,*args):return actual.__exit__(*args)
                    def read(self,*args):
                        if 'log-read' not in injections:
                            injections.append('log-read');raise tree_error
                        return actual.read(*args)
                return Read()
            def read(self,fd,*args):
                if ('terminal-output' in faults and native and owned and fd==owned[0].stdout.fileno()
                        and 'terminal-output' not in injections):
                    injections.append('terminal-output');raise OSError('finding-secondary-terminal-output')
                return real_os.read(fd,*args)
            def close(self,fd):
                real_os.close(fd)
                log_fds.discard(fd)
                if fd in fifo_fds:
                    name=fifo_fds.pop(fd);closed_fifos.append(name)
                    if ('phase-close' in faults and name=='phases.fifo'
                        or 'junit-close' in faults and name=='junit.fifo'):raise OSError('finding-secondary-fifo-close')
                if fd in pidfds:
                    pidfds.remove(fd)
                    if 'pidfd-close' in faults:raise OSError('finding-secondary-pidfd-close')
        original_write=qa.write_receipt;original_append=qa.evidence_append
        def append(path,data,*args,**kwargs):
            if 'output-publication' in faults and data:raise OSError('finding-primary-output-publication')
            return original_append(path,data,*args,**kwargs)
        def publish(path,*args,**kwargs):
            published.append(Path(path).name)
            if 'source-publication' in faults and Path(path).name=='source.json':raise OSError('finding-primary-source-publication')
            if 'publication' in faults and Path(path).name=='processes.json':raise OSError('finding-secondary-publication')
            return original_write(path,*args,**kwargs)
        binding={key:value for key,value in _DESCRIPTOR.items() if key in
            ('source','head','tree','ref','root','manifest_sha256','source_hashes','python','python_binary','python_version','python_prefix','python_sha256')}
        binding.update(task=EXECUTION_OWNER,run='synthetic-'+fault,attempt='1')
        scratch=case/'attachment-qa'/binding['task']/(binding['run']+'-1')/'scratch'
        def discover_failure(frame):raise OSError('finding-primary-discover')
        def diagnostic_snapshot(frame):
            state=frame.f_locals
            if earliest:assert state['first'] is earliest[0]
            else:earliest.append(state['first'])
            assert len(state['errors'])==len(state['reported'])<=qa.ERROR_LIMIT
            assert len({id(exc) for exc in state['reported']})==len(state['reported'])
            assert all(len(row['operation'])<=qa.ERROR_LABEL_LIMIT and len(row['error'])<=qa.ERROR_TEXT_LIMIT for row in state['errors'])
            assert not getattr(state['first'],'__notes__',())
            if primary=='tree-overflow':
                assert state['first'] is overflow_error and len(state['errors'])==qa.ERROR_LIMIT
                assert state['overflow']['observations']==qa.ERROR_OVERFLOW_LIMIT
                assert state['overflow']['meaning']==qa.ERROR_OVERFLOW_MEANING
                assert all(len(t.errors)==len(t.reported)==qa.ERROR_LIMIT and t.error_overflow==qa.ERROR_OVERFLOW_LIMIT for t in trees)
            diagnostics.update(retained=len(state['reported']),rows=len(state['errors']),
                overflow=dict(state['overflow']),notes=len(getattr(state['first'],'__notes__',())),
                representation_bytes=len(json.dumps({'errors':state['errors'],'error_overflow':state['overflow']})))
            assert diagnostics['representation_bytes']<=qa.ERROR_LIMIT*(6*(qa.ERROR_LABEL_LIMIT+qa.ERROR_TEXT_LIMIT)+128)+512
        # Leave time in this SAME half-second slice for drains/closes/publication.
        cleanup_deadline=min(trial_deadline,time.monotonic()+.25)
        try:
            with _finding_patch(qa,OwnedTree=Tree,CaptureChannels=Channels,write_receipt=publish,evidence_append=append,os=OS(),
                subprocess=_finding_subprocess(spawn),
                selectors=SimpleNamespace(DefaultSelector=Selector,EVENT_READ=selectors.EVENT_READ)):
                with _finding_patch(os,environ=dict(os.environ,RUNNER_TEMP=str(case))):
                    if 'discover' in faults:
                        with harness_source_gate(qa.supervise,'tree.discover()',discover_failure) as hits:
                            exit_code=qa.supervise(binding,scratch,deadline=cleanup_deadline)
                        assert hits
                    else:
                        from contextlib import ExitStack
                        with ExitStack() as gates_context:
                            gates_context.enter_context(harness_source_gate(qa.supervise,'result_stored=False',diagnostic_snapshot))
                            exit_code=qa.supervise(binding,scratch,deadline=cleanup_deadline)
        finally:
            for fd in gates:os.close(fd)
            # Independent real owner fallback if the assertion/gate failed.
            for child in owned:
                if child.returncode is None:child.kill();child.wait(timeout=max(0,trial_deadline-time.monotonic()))
                if child.stdout is not None and not child.stdout.closed:child.stdout.close()
        evidence=scratch.parent/'evidence';result=closed_read(evidence/'result.json',CAPS['metadata'])
        assert exit_code!=0 and result['complete'] is False
        assert 'phase publication' not in result['error'] and 'phases.json' in published and 'result.json' in published
        if primary in ('spawn','fifo-open','source-publication'):
            assert not owned and not waits and result['command_exit'] is None and result['closure'] is False
        else:
            child=owned[0];assert child.returncode is not None and child.stdout.closed and waits
            if 'publication' not in faults:
                process=closed_read(evidence/'processes.json',qa.FINAL_CAPS['processes.json'])
                assert type(process['direct_wait_returncode']) is int and process['direct_wait_returncode']==waits[-1]
        if primary in ('spawn','discover','selector-create','selector-register','fifo-open','source-publication'):
            assert 'finding-primary-'+primary in result['error']
        if primary in injected:assert earliest[0] is injected[primary]
        if primary in ('selector-create','selector-register'):
            assert injections==[primary] and bound==[owned[0].pid] and len(constructions)>1
            assert any(owned[0].pid in row['indicated'] for row in observations), 'independent pidfd exit observation unavailable'
        if 'publication' in faults:
            assert 'processes.json' in published
            assert primary=='tree-overflow' or any(e['operation']=='process publication' for e in result['errors'])
        if 'pidfd-close' in faults:assert primary=='tree-overflow' or any(row['operation']=='pidfd close' for row in result['errors'])
        assert not fifo_fds and sorted(fifo_names)==sorted(closed_fifos)
        if 'phase-close' in faults and 'junit-close' in faults:
            assert any(row['operation']=='phase fd close' for row in result['errors'])
            assert any(row['operation']=='junit fd close' for row in result['errors'])
        if primary in ('output-publication','phase-parse','tree-cleanup'):
            phases=closed_read(evidence/'phases.json',qa.FINAL_CAPS['phases.json'])
            assert set(phases['drains'])=={'output','phase','junit'}
            assert phases['drains']['junit']['complete'] and (evidence/'cases.xml').read_bytes()==b'<testsuites/>'
            assert any(row['event']=='finish' and row['phase']=='teardown' for row in phases['events'])
            assert not phases['drains']['output' if primary in ('output-publication','tree-cleanup') else 'phase']['complete']
        if primary=='tree-cleanup':
            assert native[0][0]=='log capture' and native[0][1] is cleanup_error
            assert result['error']=='PermissionError:finding-primary-tree-cleanup'
            operations=[row['operation'] for row in result['errors']]
            assert operations[0]=='log capture' and operations.count('log capture')==1
            assert operations.index('terminal output')<operations.index('pidfd close')<operations.index('process publication')
            assert len(constructions)>=1 and injections==['terminal-output'] and bound==[owned[0].pid]
        if primary in ('phase-malformed','phase-action'):
            operations=[row['operation'] for row in result['errors']]
            expected_type=json.JSONDecodeError if primary=='phase-malformed' else ValueError
            assert type(earliest[0]) is expected_type and result['error']==qa.error_text(earliest[0])
            assert operations[0]==('phase parse' if primary=='phase-malformed' else 'phase action')
            assert operations.count(operations[0])==1
            phases=closed_read(evidence/'phases.json',qa.FINAL_CAPS['phases.json'])
            assert set(phases['drains'])=={'output','phase','junit'} and phases['drains']['junit']['complete']
            assert not phases['drains']['phase']['complete'] and (evidence/'cases.xml').read_bytes()==b'<testsuites/>'
            if 'tree-phase' in faults:
                assert any(exc is tree_error for _,exc in recorded)
                assert operations.index('log capture')>0 and operations.count('log capture')==1
                assert 'PermissionError:finding-later-phase-tree' in result['errors'][operations.index('log capture')]['error']
                assert injections==['log-read'] and not log_fds
                assert any(row['event']=='finish' for row in phases['events'])
            else:
                assert operations.index('phase parsing deadline')>0 and operations.count('phase parsing deadline')==1
                assert any(type(exc) is TimeoutError for _,exc in recorded)
                assert not any(row['event']=='finish' for row in phases['events']) and captures[0].buffer
            if primary=='phase-action':assert earliest[0] is phase_error
        if primary=='tree-overflow':
            assert earliest[0] is overflow_error and result['error']==qa.error_text(overflow_error)
            assert len(result['errors'])==qa.ERROR_LIMIT and result['closure'] is False
            assert result['error_overflow']=={'observations':qa.ERROR_OVERFLOW_LIMIT,'meaning':qa.ERROR_OVERFLOW_MEANING}
            assert waits and set(published)>={'processes.json','phases.json','result.json'}
            assert all(c.drains.keys()=={'output','phase','junit'} for c in captures)
            assert all(len(t.errors)==len(t.reported)==qa.ERROR_LIMIT for t in trees)
        for selected in selectors_acquired:assert not selected.actual.get_map()
        assert time.monotonic()<trial_deadline, 'outer lifecycle exceeded the remaining original call budget'
        facts.append({'fault':fault,'exit':exit_code,'first_error':result['error'],'errors':result['errors'],
            'spawned':[p.pid for p in owned],'consuming_waits':waits,'bound':bound,'selector_injections':injections,
            'independent_observations':observations,'publications_attempted':published,
            'bounded_diagnostics':diagnostics,'overflow_assertions':overflow_facts})
    return facts

def _finding_process_binding(root):
    """Real short-lived children, finite exact-source gates, no product claims."""
    from scripts import attachment_scoped_qa as qa
    facts = []
    for fault in ('exited', 'vanished', 'reparent', 'reparent-late', 'uid', 'start', 'foreign-parent'):
        trial_deadline = min(_CALL_DEADLINE, time.monotonic()+.5)
        case = root/fault; case.mkdir(mode=0o700)
        sink = _finding_sink(case/'evidence')
        child_r, child_w = os.pipe()
        grand = None; released = False; parent_released = False; armed = False
        owned_fds = []; closed_fds = []; signals = []; recorded = []; terminal = []
        def worker(ready, release):
            os.close(child_w)
            pid = os.fork()
            if pid == 0:
                os.close(ready); os.close(release)
                try:
                    assert select.select([child_r], [], [], max(0, trial_deadline-time.monotonic()))[0]
                    assert os.read(child_r, 1) == b'R'
                    os._exit(0)
                except BaseException:os._exit(1)
            os.close(child_r)
            os.write(ready, ('CHILD:'+str(pid)).encode())
            assert select.select([release], [], [], max(0, trial_deadline-time.monotonic()))[0]
            command = os.read(release, 1)
            if command == b'P':return  # actual subreaper adoption, child still live
            assert command == b'W'
            got, status = os.waitpid(pid, 0)
            assert got == pid and os.waitstatus_to_exitcode(status) == 0
            os.write(ready, b'WAIT0')
            assert select.select([release], [], [], max(0, trial_deadline-time.monotonic()))[0]
            assert os.read(release, 1) == b'X'
        real_os = qa.os
        class OS:
            def __getattr__(self, name):return getattr(real_os, name)
            def pidfd_open(self, pid):
                fd = real_os.pidfd_open(pid); owned_fds.append(fd); return fd
            def close(self, fd):
                real_os.close(fd)
                if fd in owned_fds:closed_fds.append(fd)
        class Signal:
            def __getattr__(self, name):return getattr(signal, name)
            def pidfd_send_signal(self, *args):
                signals.append(args); raise AssertionError('binding must never signal')
        class Tree(qa.OwnedTree):
            def children(self, pid):
                return super().children(pid) & {owner, grand}
            def identity(self, pid):
                actual = qa.OwnedTree.identity(pid)
                if armed and pid == grand:
                    if fault == 'uid':return actual[0]+1, actual[1], actual[2]
                    if fault == 'start':return actual[0], actual[1]+1, actual[2]
                    if fault == 'foreign-parent':return actual[0], actual[1], 1
                return actual
        with _finding_owned_worker(worker) as (owner, ready, parent_release):
            os.close(child_r)
            tree = None
            try:
                assert select.select([ready], [], [], max(0, min(.1, trial_deadline-time.monotonic())))[0]
                message = os.read(ready, 64)
                assert message.startswith(b'CHILD:')
                grand = int(message[6:]); assert qa.OwnedTree.identity(grand)[2] == owner
                with _finding_patch(qa, os=OS(), signal=Signal()):
                    tree = Tree(sink, record=lambda label, exc:recorded.append((label, exc)))
                    tree.bind_child(owner, tree.self_pid)
                    def gate(frame):
                        nonlocal armed, released, parent_released
                        assert frame.f_locals['pid'] == grand
                        assert frame.f_locals['before'][:2] == qa.OwnedTree.identity(grand)[:2]
                        armed = True
                        if fault in ('exited', 'vanished'):
                            os.write(child_w, b'R'); released = True
                            os.write(parent_release, b'W')
                            _finding_message(ready, b'WAIT0')
                            terminal.append({'actual_owner':owner, 'child':grand, 'wait_exit':0})
                            assert not Path('/proc/'+str(grand)).exists()
                        elif fault in ('reparent', 'reparent-late'):
                            os.write(parent_release, b'P'); parent_released = True
                            bound = min(trial_deadline, time.monotonic()+.1)
                            while qa.OwnedTree.identity(grand)[2] != tree.self_pid:
                                assert time.monotonic() < bound
                                time.sleep(.001)
                    anchor = ('member = pid in self.children' if fault in ('exited', 'reparent-late') else
                              'after = self.identity(pid)' if fault == 'vanished' else 'fd = os.pidfd_open(pid)')
                    with harness_source_gate(qa.OwnedTree.bind_child, anchor, gate) as hits:
                        tree.discover()
                    assert hits and not signals
                    if fault in ('exited', 'reparent', 'reparent-late'):
                        assert not tree.errors and grand in tree.identities
                        entry = tree.identities[grand]
                        assert entry['birth_parent'] == owner and entry['parent_wait'] is None
                        assert entry['exited'] is False and entry['reaped'] is False
                        if fault == 'exited':
                            observation = next(e for e in tree.events if e['kind']=='binding_observation' and e['pid']==grand)
                            assert observation['before'][:2] == observation['after'][:2]
                            assert observation['exit_indicated'] and observation['member'] is False
                            assert observation['terminal_identity'] is None
                            tree.required_roles.add(grand)
                            tree.observe_exits(owner)
                            assert entry['removed_after_exit'] and entry['parent_wait'] is None and not entry['reaped']
                            # The actual parent's private wait above was NOT an
                            # admitted role receipt. Disappearance grants no wait.
                            assert not tree.closed_identity(grand)
                        else:
                            assert entry['parent'] == tree.self_pid and not tree.binding_exit(entry['fd'])
                            assert any(e['kind']=='reparent' and e['pid']==grand and e['before']==owner
                                       and e['after']==tree.self_pid for e in tree.events)
                    else:
                        assert grand not in tree.identities and tree.errors and recorded
                        first = recorded[0][1]
                        assert first is tree.reported[0]
                        assert type(first) is (FileNotFoundError if fault=='vanished' else ValueError)
                        refused = next(e for e in tree.events if e['kind']=='binding_refused' and e['pid']==grand)
                        assert refused['before'] is not None and not any(e['kind']=='bound' and e['pid']==grand for e in tree.events)
                        assert any('binding context' in note for note in first.__notes__)
                        if fault in ('uid', 'start'):assert refused['before'][:2] != refused['after'][:2]
                        if fault == 'foreign-parent':assert refused['after'][2] == 1
                        # Every acquired but untransferred child descriptor closed
                        # before publication; no signals, wait or closure invented.
                        assert len(owned_fds)==2 and closed_fds==[owned_fds[-1]]
                        _finding_assert_raises(lambda:os.fstat(owned_fds[-1]), OSError)
                    assert tree.finalize_cleanup(False) is False
                    process = closed_read(sink/'processes.json', qa.FINAL_CAPS['processes.json'])
                    assert process['closure'] is False and process['direct_wait_returncode'] is None
                    assert sorted(owned_fds)==sorted(closed_fds) and not signals
                    facts.append({'fault':fault, 'binding_events':[e for e in tree.events if e.get('pid')==grand],
                                  'errors':tree.errors, 'actual_parent_wait':terminal, 'role_wait_admitted':False,
                                  'closed_pidfds':len(closed_fds), 'signals':len(signals), 'closure':process['closure']})
            finally:
                if tree is not None and any(e.get('fd') is not None for e in tree.identities.values()):
                    tree.finalize_cleanup(False)
                if not released:os.write(child_w, b'R'); released = True
                if parent_released:
                    got, status = os.waitpid(grand, 0)
                    assert got==grand and os.waitstatus_to_exitcode(status)==0
                else:
                    if not terminal:
                        os.write(parent_release, b'W'); _finding_message(ready, b'WAIT0')
                    os.write(parent_release, b'X')
                os.close(child_w)
            assert time.monotonic() < trial_deadline, 'binding trial exhausted the original call budget'
    return facts


def assert_finding_capture(root):
    facts={}
    for kind in ('provider','callback'):
        for fault in ('close','publication','spawn+publication','selector-register+close+publication',None):
            name=kind+'-'+str(fault);case=root/name;case.mkdir(mode=0o700)
            payload=b'X'*(16385 if kind=='provider' else 4097) if fault is None else b'X'
            row,receipt=_finding_diagnostic_trial(case,kind,fault,payload)
            if fault is None:
                assert row['complete'] is False and 'overflow' in row['error']
                assert row['output']['stdout']['raw_length']<=(16384 if kind=='provider' else 4096)
                assert row['output']['stdout']['observed_raw_length']>row['output']['stdout']['raw_length']
            if fault=='spawn+publication':
                assert row['error'].startswith('OSError:finding-primary-spawn') and receipt['spawned']==0
                assert receipt['stored_final'] is False
            if fault=='selector-register+close+publication':
                assert row['error'].startswith('OSError:finding-primary-selector-register')
                assert any('secondary-close' in error for error in row['cleanup_errors']) and receipt['stored_final'] is False
            facts[name]=receipt
    for name,check in (('capacity',_finding_capacity),('lock',_finding_lock_and_cleanup),
                       ('terminal',_finding_terminal_channels),('outer',_finding_outer_lifecycle),
                       ('binding',_finding_process_binding)):
        case=root/name;case.mkdir(mode=0o700);facts[name]=check(case)
    return facts


def run_finding_assertions():
    from scripts.attachment_scoped_qa import FINDING_ROWS
    assert _ACTIVE_NODE==NODES[0] and _DESCRIPTOR['manifest_sha256']==MANIFEST_SHA256
    assert _CALL_DEADLINE is not None and time.monotonic()<_CALL_DEADLINE
    root=Path(_DESCRIPTOR['scratch'])/'finding-checks';root.mkdir(mode=0o700)
    checks=(assert_finding_admission,assert_finding_popen,assert_finding_selector,assert_finding_raw,assert_finding_capture)
    receipts={}
    try:
        for row,check in zip(FINDING_ROWS,checks,strict=True):
            case=root/('row-'+str(len(receipts)));case.mkdir(mode=0o700)
            try:
                facts=check(case)
                assert time.monotonic()<_CALL_DEADLINE, 'finding assertions exhausted the original call budget'
                receipts[row]={'outcome':'passed','node':NODES[0],'head':_DESCRIPTOR['head'],
                    'source_hashes':_DESCRIPTOR['source_hashes'],'assertion':check.__name__,'facts':facts}
            except BaseException as exc:
                receipts[row]={'outcome':'incomplete','error':type(exc).__name__+':'+str(exc)[:1024],
                    'node':NODES[0],'head':_DESCRIPTOR['head'],'assertion':check.__name__}
                raise
    finally:
        for row in FINDING_ROWS:receipts.setdefault(row,{'outcome':'missing','reason':'assertions not completed'})
        write_receipt(Path(_DESCRIPTOR['evidence'])/'finding-assertions.json',receipts,262144,
                      evidence=Path(_DESCRIPTOR['evidence']))
    assert set(receipts)==set(FINDING_ROWS) and all(row['outcome']=='passed' for row in receipts.values())
