"""Guard-first, read-only, source-bound daemon trace observer for THR304."""
from __future__ import annotations

import os

import hashlib
import json
from pathlib import Path
import sys

if __name__=='sitecustomize' and 'HAPPYRANCH_ATTACHMENT_FROZEN_PYTHON' in os.environ:
    try:
        frozen=json.loads(os.environ['HAPPYRANCH_ATTACHMENT_FROZEN_PYTHON'])
        if (sys.version_info[:2]!=(3,14) or sys.prefix!=frozen['python_prefix']
                or sys.version!=frozen['python_version']
                or str(Path(sys.executable).resolve())!=frozen['python_binary']
                or hashlib.sha256(Path(sys.executable).resolve().read_bytes()).hexdigest()!=frozen['python_sha256']):
            raise ValueError('frozen interpreter/prefix drift')
        evidence=Path(os.environ['HAPPYRANCH_ATTACHMENT_PYTHON_EVIDENCE'])
        if evidence.resolve(strict=True)!=evidence or evidence.stat().st_uid!=os.getuid():
            raise ValueError('frozen interpreter receipt owner drift')
        start=Path('/proc/self/stat').read_text().rsplit(')',1)[1].split()[19]
        observed={'pid':os.getpid(),'start':start,'prefix':sys.prefix,'version':sys.version,
            'executable':os.path.abspath(sys.executable),'binary':str(Path(sys.executable).resolve()),
            'sha256':frozen['python_sha256'],'argv':sys.orig_argv}
        encoded=(json.dumps(observed,sort_keys=True)+'\n').encode()
        if len(encoded)>4096:raise ValueError('interpreter readback cap')
        from scripts.attachment_scoped_qa import evidence_publish
        evidence_publish(evidence/('python-'+str(os.getpid())+'-'+start+'.json'),
                         encoded,4096,evidence)
    except Exception:
        os.write(2,b'attachment frozen pre-import venv binding unavailable\n')
        os._exit(86)

if __name__ == "sitecustomize" and "HAPPYRANCH_TEST_PARENT_MANIFEST" in os.environ:
    try:
        from guard import install
        install()
    except Exception:
        os.write(2, b"attachment observer guard unavailable\n")
        os._exit(86)

import ast
import dis
import hashlib
import json
import marshal
from pathlib import Path
import select
import stat
import subprocess
import sys
import threading
import time
import types

PHASES = ("ready-before-claim", "before-gate", "claimed-before-body", "after-A-before-B",
          "before-ACK", "after-ACK-before-wrapper-return")


def source_binding(source: Path, revision: str) -> dict:
    """Compile bytes solely for code comparison/anchors; execute no reconstructed function."""
    path=source/"runtime/orchestrator/run_step.py"
    data=path.read_bytes()
    code=compile(data,str(path),"exec")
    targets={}
    def visit(value):
        for item in value.co_consts:
            if isinstance(item,types.CodeType):
                if item.co_name in ("_consume_completion_report","_v2_decision_dispatch_gate","_attachment_feedback_and_reenqueue"):
                    targets[item.co_name]=item
                visit(item)
    visit(code)
    tree=ast.parse(data)
    anchors={}
    for function in tree.body:
        if not isinstance(function,ast.FunctionDef) or function.name not in targets:
            continue
        # Bytecode without a source line is not an executable anchor.
        lines=sorted({line for _,line in dis.findlinestarts(targets[function.name]) if line is not None})
        for node in ast.walk(function):
            if not isinstance(node,(ast.Assign,ast.Expr)):
                continue
            calls=[call for call in ast.walk(node) if isinstance(call,ast.Call)]
            for call in calls:
                name=call.func.attr if isinstance(call.func,ast.Attribute) else call.func.id if isinstance(call.func,ast.Name) else None
                following=next((line for line in lines if line>node.end_lineno),None)
                if name=="spend_authority_policy_v2_continue_envelope":
                    anchors["ready-before-claim"]=(function.name,following)
                elif name=="_v2_decision_dispatch_gate":
                    anchors["before-gate"]=(function.name,node.lineno)
                elif name=="try_attachment_retry_feedback":
                    anchors["after-A-before-B"]=(function.name,following)
                elif name=="acknowledge_authority_policy_v2_decision_dispatch":
                    anchors["before-ACK"]=(function.name,node.lineno)
                    anchors["after-ACK-before-wrapper-return"]=(function.name,following)
        if function.name=="_consume_completion_report":
            admitted_try=next(node for node in function.body if isinstance(node,ast.Try)
                and any(isinstance(call,ast.Call) and isinstance(call.func,ast.Name)
                    and call.func.id=="_consume_completion_report_body" for call in ast.walk(node)))
            anchors["claimed-before-body"]=(function.name,admitted_try.body[0].lineno)
    if set(anchors)!=set(PHASES) or any(line is None for _,line in anchors.values()):
        raise ValueError("observer exact executable anchors unavailable")
    return {"revision":revision,"path":str(path),"source_sha256":hashlib.sha256(data).hexdigest(),
            "helper_sha256":hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "wait_code_sha256":hashlib.sha256(marshal.dumps(subprocess.Popen.wait.__code__,2)).hexdigest(),
            "callers":{name:hashlib.sha256((source/name).read_bytes()).hexdigest() for name in ("runtime/daemon/queue.py","runtime/daemon/dispatcher.py")},
            "codes":{name:hashlib.sha256(marshal.dumps(value, 2)).hexdigest() for name,value in targets.items()},
            "anchors":anchors}


def _owned_json(path: Path, cap: int=16384):
    info=path.lstat()
    if (path.resolve(strict=True)!=path or not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid()
            or stat.S_IMODE(info.st_mode)!=0o600 or info.st_size>cap):
        raise ValueError("observer descriptor ownership/cap unavailable")
    return json.loads(path.read_text())


def install_observer():
    if sys.version_info[:2]!=(3,14):
        raise ValueError("observer requires Python3.14")
    from guard import manifest
    parent=manifest()
    descriptor=_owned_json(Path(os.environ["HAPPYRANCH_ATTACHMENT_OBSERVER"]))
    binding=source_binding(Path(parent["source"]),parent["revision"])
    if descriptor["binding"]!=json.loads(json.dumps(binding)) or descriptor["node"] not in descriptor["released_nodes"]:
        raise ValueError("observer released source/node binding mismatch")
    output=Path(descriptor["output"])
    incoming=Path(descriptor["incoming"])
    for path in (output,incoming):
        info=path.lstat()
        if (not stat.S_ISFIFO(info.st_mode) or info.st_uid!=os.getuid() or stat.S_IMODE(info.st_mode)!=0o600
                or path.parent.resolve(strict=True)!=path.parent):
            raise ValueError("observer endpoint refused")
    reader=os.open(incoming,os.O_RDWR|os.O_NONBLOCK)
    writer=os.open(output,os.O_RDWR|os.O_NONBLOCK)
    wait_path=Path(descriptor["wait_log"])
    if wait_path.parent.resolve(strict=True)!=wait_path.parent or wait_path.exists():
        raise ValueError("wait journal path refused")
    from scripts.attachment_scoped_qa import evidence_append
    wait_identity=evidence_append(wait_path,b'',1048576,wait_path.parent,exclusive=True)
    wait_bytes=0
    armed=None
    entered=None
    observed=set()
    lock=threading.Lock()

    def receive(seconds):
        deadline=time.monotonic()+seconds
        buffer=b""
        while time.monotonic()<deadline:
            readable,_,_=select.select([reader],[],[],max(0,deadline-time.monotonic()))
            if not readable:
                break
            buffer+=os.read(reader,4096)
            if len(buffer)>4096:
                raise ValueError("observer handshake cap")
            if b"\n" in buffer:
                raw,extra=buffer.split(b"\n",1)
                if extra:
                    raise ValueError("observer multiple handshake commands")
                return json.loads(raw)
        raise TimeoutError("observer bounded handshake expired")

    def trace(frame,event,arg):
        nonlocal armed,entered,wait_bytes
        if event=='return' and frame.f_code is subprocess.Popen.wait.__code__:
            if hashlib.sha256(marshal.dumps(frame.f_code,2)).hexdigest()!=binding['wait_code_sha256']:
                os._exit(86)
            child=frame.f_locals['self']
            if type(arg) is not int:return trace
            row={'owner':os.getpid(),'child':child.pid,'returncode':arg,
                 'code_sha256':binding['wait_code_sha256'],'head':binding['revision'],'nonce':descriptor['nonce']}
            data=(json.dumps(row,sort_keys=True)+'\n').encode()
            with lock:
                wait_bytes+=len(data)
                if wait_bytes>1048576:os._exit(86)
                evidence_append(wait_path,data,1048576,wait_path.parent,identity=wait_identity)
            return trace
        if event!="line" or frame.f_code.co_filename!=binding["path"] or frame.f_code.co_name not in binding["codes"]:
            return trace
        code_hash=hashlib.sha256(marshal.dumps(frame.f_code, 2)).hexdigest()
        if code_hash!=binding["codes"][frame.f_code.co_name]:
            os.write(2,b"observer callable code mismatch\n");os._exit(86)
        if armed is None:
            readable,_,_=select.select([reader],[],[],0)
            if not readable:
                return trace
            with lock:
                if armed is None:
                    armed=receive(5)
                    if armed.get("nonce")!=descriptor["nonce"] or armed.get("command")!="arm":
                        os._exit(86)
        task=frame.f_locals.get("task_id")
        claim=frame.f_locals.get("expected_claim")
        if claim is not None:
            task=claim.task_id
        if task!=armed["task_id"]:
            return trace
        phase=next((name for name,(function,line) in binding["anchors"].items()
                    if function==frame.f_code.co_name and line==frame.f_lineno),None)
        if phase is None or phase not in armed["phases"] or phase in observed:
            return trace
        rowid=frame.f_locals.get("resolved_row_id",frame.f_locals.get("result_row_id"))
        if claim is not None:
            rowid=claim.result_row_id
        if type(rowid) is not int or rowid!=armed["result_id"]:
            return trace
        parent_frame=frame
        while parent_frame is not None and parent_frame.f_code.co_name!="_consume_completion_report":
            parent_frame=parent_frame.f_back
        if parent_frame is None:
            os._exit(86)
        token=(id(parent_frame),threading.get_ident(),threading.get_native_id())
        with lock:
            if entered is None:
                entered=token
            elif entered!=token:
                os.write(2,b"observer duplicate consumer owner\n");os._exit(86)
            observed.add(phase)
        outcome=parent_frame.f_locals.get("outcome")
        causal=parent_frame.f_locals.get("causal_result_id") or getattr(outcome,"causal_result_id",None)
        report=parent_frame.f_locals.get("report")
        if report is None:
            os._exit(86)
        report_digest=hashlib.sha256(json.dumps(report.model_dump(mode="json"),sort_keys=True,separators=(",", ":"),ensure_ascii=True).encode()).hexdigest()
        start=Path("/proc/self/stat").read_text().split(")",1)[1].split()[19]
        row={"nonce":descriptor["nonce"],"node":descriptor["node"],"phase":phase,
             "task_id":task,"result_id":rowid,"causal_result_id":causal,"pid":os.getpid(),
             "thread":token[1],"native_thread":token[2],"entry_token":token[0],"line":frame.f_lineno,
             "code_sha256":code_hash,"head":binding["revision"],"report_sha256":report_digest,
             "process_start":start,"manager_agent":parent_frame.f_locals.get("manager_agent")}
        encoded=(json.dumps(row,sort_keys=True)+"\n").encode()
        if len(encoded)>4096 or os.write(writer,encoded)!=len(encoded):
            os._exit(86)
        reply=receive(5)
        if reply=={"command":"ack-interrupt","nonce":descriptor["nonce"],"phase":phase}:
            acknowledged=(json.dumps({**row,"event":"acknowledged"},sort_keys=True)+"\n").encode()
            if os.write(writer,acknowledged)!=len(acknowledged):
                os._exit(86)
            # Driver has independently read committed state/event before this
            # acknowledgement. Remain in the SAME frame until owned interruption.
            receive(5)
            os._exit(86)
        if reply!={"command":"release","nonce":descriptor["nonce"],"phase":phase}:
            os._exit(86)
        return trace
    def guarded_trace(frame,event,arg):
        try:
            trace(frame,event,arg)
            return guarded_trace
        except BaseException:
            # An unavailable observer terminates only its owned daemon. It
            # never injects an exception into the production decision body.
            os.write(2,b"attachment observer handshake unavailable\n")
            os._exit(86)
    sys.settrace(guarded_trace)
    threading.settrace(guarded_trace)


if __name__=="sitecustomize" and "HAPPYRANCH_ATTACHMENT_OBSERVER" in os.environ:
    try:
        # Other guarded Python/uv/callback invocations do not install tracing.
        if any(sys.orig_argv[index:index+2]==["-m","runtime.daemon"] for index in range(len(sys.orig_argv)-1)):
            install_observer()
    except Exception:
        os.write(2,b"attachment observer unavailable\n")
        os._exit(86)
