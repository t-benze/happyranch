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

# Parent admission precedes runtime/pytest/helper imports.
from tests.helpers.integration_stub_guard.guard import require_parent_environment
require_parent_environment()
from scripts.attachment_scoped_qa import EXECUTION_OWNER
if EXECUTION_OWNER is None:
    raise RuntimeError("THR304 missing manager execution binding; product/test imports refused")

from contextlib import contextmanager
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
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

import httpx
import pytest

from scripts.attachment_scoped_qa import (
    CAPS, EXECUTION_OWNER, IMPLEMENTATION_REF, IMPLEMENTATION_TASK, MANIFEST_SHA256, NODES,
    PARAMETERS, PHASE_LIMITS, ROOT_TASK, SELECTION, SOURCE_FILES, SUBOBSERVATIONS,
    closed_read, sha, write_receipt,
)
from tests.helpers.integration_stub_guard.guard import manifest
from tests.integration.conftest import seed_agent_definition, seed_workspace
from runtime.config import Settings
from runtime.daemon.org_state import OrgState
from runtime.orchestrator._paths import OrgPaths
from runtime.orchestrator.orchestrator import completion_report_from_result_row
from runtime.infrastructure.database import Database, LostClaim, PendingRetry, RetryClaim
from runtime.models import TaskStatus

_DESCRIPTOR = None
_OBSERVATIONS = {}


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
    phase(item.nodeid,"call","start")
    outcome=yield
    phase(item.nodeid,"call","finish",outcome="error" if outcome.excinfo else "returned")


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
    write_receipt(Path(_DESCRIPTOR["evidence"])/"observations.json",_OBSERVATIONS)
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


def await_condition(predicate, seconds: float = 5):
    deadline=time.monotonic()+seconds
    while time.monotonic()<deadline:
        result=predicate()
        if result:
            return result
        # Readiness polling only; races use actual commit/barrier receipts.
        time.sleep(.02)
    raise TimeoutError("bounded attachment readiness/readback missing")


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
        item=await_condition(find)
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


def provider_main():
    task,sid,agent,slug=sys.argv[2:6]
    control=Path(sys.argv[6])
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
    process=subprocess.Popen([manifest()["callback"],"report-completion","--org",slug,"--from-file",str(body)],
                             stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    selector=selectors.DefaultSelector()
    selector.register(process.stdout,selectors.EVENT_READ,'stdout')
    selector.register(process.stderr,selectors.EVENT_READ,'stderr')
    chunks={'stdout':bytearray(),'stderr':bytearray()};deadline=time.monotonic()+5
    try:
        while selector.get_map():
            if time.monotonic()>deadline:raise TimeoutError('bounded public callback deadline')
            for key,_ in selector.select(.02):
                data=os.read(key.fileobj.fileno(),4096)
                if not data:selector.unregister(key.fileobj);continue
                if sum(len(value) for value in chunks.values())+len(data)>4096:
                    raise RuntimeError('public callback capture overflow')
                chunks[key.data].extend(data)
        process.wait(timeout=max(.01,deadline-time.monotonic()))
    finally:
        selector.close()
    stdout,stderr=bytes(chunks['stdout']),bytes(chunks['stderr'])
    from types import SimpleNamespace
    result=SimpleNamespace(stdout=stdout,stderr=stderr,returncode=process.returncode)
    if len(result.stdout)+len(result.stderr)>4096:
        raise SystemExit("callback capture cap")
    write_receipt(control/f"callback-{sid}.json",{"exit":result.returncode,
        "stdout_sha256":hashlib.sha256(result.stdout).hexdigest(),"stderr_sha256":hashlib.sha256(result.stderr).hexdigest(),
        "parent_pid":os.getpid(),"child_pid":process.pid,"parent_wait_returncode":process.returncode},4096)
    if not payload["exit"]:
        await_condition(lambda:(control/f"exit-{sid}.json").exists(),20)
    write_receipt(control/f"provider-exit-{sid}.json",{"task":task,"sid":sid,"pid":os.getpid()},4096)
    raise SystemExit(result.returncode)


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
    script=("#!/bin/sh\nexec 3<&0\npython -m tests.helpers.attachment_feedback_fixture provider "
            '\"$1\" \"$2\" \"$3\" \"$4\" '+shlex.quote(str(controls))+" <&3 &\n"
            'attachment_child=$!\nattachment_exit=0\nwait "$attachment_child" || attachment_exit=$?\n'
            'python -m tests.helpers.attachment_feedback_fixture provider-wait "$1" "$2" "$$" "$attachment_child" "$attachment_exit" '
            +shlex.quote(str(controls))+'\nexit "$attachment_exit"\n')
    for plan in (fake_claude_plan_env,fake_codex_plan_env):
        plan.write_text(script)
    # Thread followup is explicitly declined through the real callback, not an implicit success.
    thread_script='#!/bin/sh\nexec happyranch thread-reply --org "$4" --thread-id "$1" --agent "$3" --invocation-token "$2" --decline\n'
    for plan in (fake_claude_thread_plan_env,fake_opencode_thread_plan_env):
        plan.write_text(thread_script)
    # Copy only declared seed inputs before the first daemon/history exists.
    template=controls/'matrix-seed'
    template.mkdir(mode=0o700)
    for name in ('org','workspaces'):
        shutil.copytree(runtime/name,template/name)
    return controls


@pytest.fixture
def attachment_live_daemon(attachment_runtime_seed, runtime, request):
    request.getfixturevalue("attachment_public_observer")
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


if __name__=="__main__" and len(sys.argv)>1:
    if sys.argv[1]=="provider":provider_main()
    elif sys.argv[1]=="provider-wait":
        task,sid,parent,child,status,control=sys.argv[2:8]
        launch=closed_read(Path(control)/f"launch-{sid}.json",16384)
        assert launch['task_id']==task and launch['driver_pid']==int(child) and launch['plan_parent_pid']==int(parent)
        write_receipt(Path(control)/f"provider-wait-{sid}.json",
            {'owner':int(parent),'child':int(child),'returncode':int(status),'sid':sid,'task':task},4096)
        raise SystemExit(0)

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
    lines=sorted({line for _,line in dis.findlinestarts(callback.__code__)})
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
    for name in ('org','workspaces'):
        shutil.copytree(original.controls/'matrix-seed'/name,root/name)
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
    plan=base/'provider.sh'
    plan.write_text('#!/bin/sh\nexec 3<&0\npython -m tests.helpers.attachment_feedback_fixture provider "$1" "$2" "$3" "$4" '+shlex.quote(str(controls))+' <&3 &\nattachment_child=$!\nattachment_exit=0\nwait "$attachment_child" || attachment_exit=$?\npython -m tests.helpers.attachment_feedback_fixture provider-wait "$1" "$2" "$$" "$attachment_child" "$attachment_exit" '+shlex.quote(str(controls))+'\nexit "$attachment_exit"\n')
    plan.chmod(0o700)
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
        subprocess.run([str(script),'start'],check=True,env=_nested_daemon_env(),timeout=5)
        port=await_condition(lambda:daemon_paths.port_file().read_text().strip() if daemon_paths.port_file().exists() else None)
        driver=PublicDriver(port,root,controls,original.node)
        driver.batch_index=index
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
        lines=sorted({line for _,line in dis.findlinestarts(callback.__code__)})
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
    if report.longrepr is not None and len(str(report.longrepr).encode())>16384:
        report.longrepr='THR304 required assertion traceback exceeded 16KiB; evidence INCOMPLETE'
        report.outcome='failed'
    if any(len(text.encode())>16384 for _,text in report.sections):
        report.sections=[];report.outcome='failed'
        report.longrepr='THR304 report section overflow; evidence INCOMPLETE'


def retain_node_receipt(node, name, value, cap=16384):
    directory=Path(_DESCRIPTOR['evidence'])/hashlib.sha256(node.encode()).hexdigest()[:20]
    directory.mkdir(mode=0o700,exist_ok=True)
    write_receipt(directory/name,value,cap)


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
    write_receipt(target,{'node':node,'head':_DESCRIPTOR['head'],'rows':rows},262144)
    return target
