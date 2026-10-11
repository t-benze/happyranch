"""Finite E07 R suffix: one actual crash gap and same-files failed recovery.

Fixture teardown is separately receipted. It cannot publish the missing B28
terminal receipt or stand in for product cancellation. Successful fit is unproven.
"""
from __future__ import annotations

import hashlib
import json
import os
import signal
import subprocess
import time
from pathlib import Path
from typing import TYPE_CHECKING

from e07 import frame, host, register_owned, register_tree, closure, witnesses
from late_transport import LateTransport, process_record, public_stream
from oracles import require
from owned import identity

if TYPE_CHECKING:
    from controller import Controller

VARIANTS = ["E07.abrupt-dead-session-recovery", "E07.restart-late-cli"]
AMENDMENT = "83d892a3e6eae329879ebf7be7a9dca47854f15f6bbe67be3811c4820e1fe184"
NOTE = "session died on daemon restart -- executor pid not alive"


def listener_absent(port: int) -> bool:
    # Observe kernel listener content, not a connect failure/intent to signal.
    return not any(int(line.split()[1].rsplit(":", 1)[1], 16) == port and line.split()[3] == "0A"
                   for table in ("tcp", "tcp6")
                   for line in Path("/proc/net/" + table).read_text().splitlines()[1:])


def listener_owner(pid: int, port: int) -> dict:
    rows = [line for line in Path(f"/proc/{pid}/net/tcp").read_text().splitlines()[1:]
            if int(line.split()[1].rsplit(":", 1)[1], 16) == port and line.split()[3] == "0A"]
    fds = []
    for fd in Path(f"/proc/{pid}/fd").iterdir():
        try:
            target = os.readlink(fd)
        except FileNotFoundError:
            continue
        if any(target == "socket:[" + row.split()[9] + "]" for row in rows):
            fds.append(dict(fd=int(fd.name), target=target))
    require(len(rows) == len(fds) == 1, "R actual new daemon listener ownership")
    return dict(pid=pid, start=identity(pid), port=port, rows=rows, fds=fds, at=time.monotonic())


def files(c: Controller) -> dict:
    paths = [c.root/"daemon", c.root/"runtime", *(s.path for s in c.stores.values())]
    identities = {str(p): dict(device=p.stat().st_dev, inode=p.stat().st_ino) for p in paths}
    selected = [c.root/"daemon/executors.json", *sorted((c.source/"e2e/template").rglob("*"))]
    return dict(identities=identities, fingerprints={str(p): hashlib.sha256(p.read_bytes()).hexdigest()
                                                   for p in selected if p.is_file()}, bindings=dict(c.bindings))


def save(c: Controller) -> None:
    c.capture_thread_secrets()
    text = c.sanitize(json.dumps(c.e07["restart"], indent=2))
    (c.root/"raw/e07-restart.json").write_text(text)
    (c.root/"raw/e07.json").write_text(c.sanitize(json.dumps(c.e07, indent=2)))


def recovered(c: Controller, before: dict, after: dict, start: dict) -> None:
    task = start["task"]
    old = next(t for t in before["alpha"]["tables"]["tasks"] if t["id"] == task)
    now = next(t for t in after["alpha"]["tables"]["tasks"] if t["id"] == task)
    require(now["status"] == "failed" and now["note"] == NOTE
            and now["current_session_id"] == start["session"] and now["executor_pid"] == old["executor_pid"]
            and now["orchestration_step_count"] == 1 and now["block_kind"] is None
            and now["completed_at"] is None and now["cancelled_at"] is None, "literal dead-non-NULL recovery")
    changed = {"status", "note", "updated_at"}
    require({k:v for k,v in old.items() if k not in changed} == {k:v for k,v in now.items() if k not in changed}, "R only recovery fields change")
    require(before["beta"] == after["beta"], "R beta content unchanged")
    for name, rows in before["alpha"]["tables"].items():
        actual = after["alpha"]["tables"][name]
        if name == "tasks":
            require([r for r in actual if r["id"] != task] == [r for r in rows if r["id"] != task], "all settled task fields retained")
        elif name == "audit_log":
            require(actual[:len(rows)] == rows, "full prior audit prefix retained")
            own = [r for r in actual[len(rows):] if r["task_id"] == task]
            require(len(own) == 1 and own[0]["action"] == "daemon_restart_failure"
                    and own[0]["agent"] == "case_manager" and json.loads(own[0]["payload"]) == {"reason":"daemon restarted mid-task"}, "exact R startup audit")
        else:
            require(actual == rows, "all settled results/jobs/threads/control rows retained: " + name)
    require(after["alpha"]["files"] == before["alpha"]["files"], "R settled product file content retained")


def exercise(c: Controller) -> None:
    # C archive is complete before R admission; no final40 substitution.
    c.remaining()
    c.evidence()
    raw = (c.root/"raw/observations.json").read_bytes()
    (c.root/"raw/e07-c-prefix.json").write_bytes(raw)
    prefix = json.loads(raw)
    c.remaining()
    begin = time.monotonic()
    record = dict(variants=VARIANTS, amendment_sha256=AMENDMENT, entered=begin,
                  end=begin+75, scenario_deadline=c.deadline, source_sha=c.revision,
                  admitted=False, phases=[], commands=c.r_commands, frames=[], owned=[],
                  c_prefix=dict(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest(),
                                witness_bytes=prefix["witness_bytes"], witness_records=len(prefix["launch_witnesses"]),
                                observer_end=len(prefix["provider_observer"]), command_count=len(prefix["commands"])))
    c.e07["restart"] = record
    admission = dict(name="R", entered=begin, end=begin+75, downstream=25, admitted=False)
    c.allocation["stages"].append(admission)
    save(c)
    require(begin+100 < c.deadline and len(c.starts) == 39 and len(c.results) == 56, "R full downstream reservation and completed C")
    admission["admitted"] = record["admitted"] = True
    c.work_end = begin+75

    def phase(name: str, offset: int) -> None:
        if record["phases"]:
            c.remaining()
            record["phases"][-1]["finished"] = time.monotonic()
        c.case_deadline = min(begin+offset, c.deadline-25)
        record["phases"].append(dict(name=name, entered=time.monotonic(), end=c.case_deadline))
        c.publish_end()
        save(c)

    try:
        phase("capture",10)
        record["files_before"] = files(c)
        record["daemon_b"] = dict(process_record(c.daemon.pid), port=c.port)
        old_token = c.token
        record["bearer_b_sha256"] = hashlib.sha256(old_token.encode()).hexdigest()
        task = c.create("alpha", "E07_RESTART_ACTIVE")
        record["task"] = task
        start = c.start("alpha", task, "case_manager")
        record["start"] = start
        tree = register_tree(c, start)
        record["tree"] = tree
        payload = dict(session_id=start["session"], agent="case_manager", status="completed", confidence=90,
                       output_summary="E07 old boot must not complete", risks_flagged=[], dependencies=[],
                       suggested_reviewer_focus=[], decision=dict(action="done", summary="E07 old boot must not complete"))
        relay = LateTransport(c.root, c.port, task, payload, c.token, c.secrets, record["end"])
        c.late_transport = relay
        record["transport"] = dict(listener=relay.listener, port=relay.port, upstream=c.port,
                                   bearer_sha256=hashlib.sha256(("Bearer "+c.token).encode()).hexdigest(),
                                   records=relay.records, accepted=relay.accepted)
        home = c.root/"e07-r-client"
        home.mkdir(mode=0o700)
        for name,value in (("port",str(relay.port)), ("token",c.token)):
            path = home/("daemon."+name)
            path.write_text(value)
            path.chmod(0o600)
        instruction = dict(action="e07-restart-held", execution_deadline=record["end"], ack_deadline=c.case_deadline)
        command = dict(action=instruction["action"], provider=start, instruction=instruction, entered=time.monotonic())
        c.r_commands.append(command)
        save(c)
        connection,stream = c.holds[start["session"]]
        connection.settimeout(c.remaining())
        stream.write((json.dumps(instruction)+"\n").encode())
        connection.settimeout(c.remaining())
        stream.flush()
        connection.settimeout(c.remaining())
        launch = json.loads(stream.readline(131072))["e07_launch"]
        command["launch"] = launch
        record["owned"].append(launch["child"])
        register_owned(c, [launch["child"]["pid"]])
        c.remaining()
        require(launch == json.loads((c.root/"coord"/("e07-launch-"+start["session"]+".json")).read_text())
                and launch["provider"] == {k:start[k] for k in ("task","session","agent","org","pid")}
                and launch["execution_deadline"] == record["end"], "R durable genuine launch")
        require(relay.captured.wait(c.remaining()) and not relay.errors, "R complete original request capture")
        c.remaining()
        record["provenance"] = relay.provenance(launch)
        record["live"] = [frame(c,task,task),frame(c,task,task)]
        tail = c.e07["final"]["metrics"]["host_sessions"]["receipts"]
        require(host(record["live"][0],28,27,1,tail) == host(record["live"][1],28,27,1,tail), "two literal B28/27/27/1 projections")
        live = record["live"][-1]
        row = next(t for t in live["sql"]["alpha"]["tables"]["tasks"] if t["id"] == task)
        require(row["status"] == "in_progress" and row["block_kind"] is None and row["parent_task_id"] is None
                and row["executor_pid"] == tree["launch_pid"] and row["current_session_id"] == start["session"]
                and row["orchestration_step_count"] == 1 and row["completed_at"] is None and row["cancelled_at"] is None
                and not [r for r in live["sql"]["alpha"]["tables"]["task_results"] if r["task_id"] == task], "R genuine no-result active root")
        require(relay.forward_count == 0, "R no upstream before death")

        phase("pre-death-archive",15)
        # Freeze original E05 browser evidence before old-origin disposal.
        c.browser.close_threads_context()
        record["old_browser_closed"] = time.monotonic()
        proxy_close = c.browser_relay.close(c.case_deadline)
        c.durable.append(dict(thread_proxy_closure=proxy_close))
        require(not any(proxy_close[k] for k in ("workers_alive","listener_alive","connections","errors")), "old browser proxy closed before B death")
        c.browser_relay_closed = True
        record["predeath"] = frame(c,task,task)
        require(record["predeath"]["sql"] == live["sql"] and relay.forward_count == 0, "complete predeath state and held request")
        record["a12"] = next(d["jobs_anchor"] for d in prefix["durable"] if d.get("jobs_anchor",{}).get("name") == "archive-A")
        # Full duplicate-bearing A content remains in the original archive;
        # retain that entire content, not a digest-only assertion.
        record["original_prefix"] = json.loads((c.root/"raw/e07-prefix.json").read_text())
        record["b27"] = tail
        record["ports"] = json.loads((c.root/"ports.json").read_text())
        c.evidence()
        record["archived"] = time.monotonic()
        save(c)
        archive = (c.root/"raw/e07-restart.json").read_bytes()
        (c.root/"raw/e07-predeath.json").write_bytes(archive)
        record["predeath_archive"] = dict(bytes=len(archive),sha256=hashlib.sha256(archive).hexdigest())
        c.remaining()

        phase("death-and-owned-close",25)
        old = record["daemon_b"]
        require(identity(old["pid"]) == old["start"] and f"E2E_ROOT={c.root}".encode()
                in Path(f"/proc/{old['pid']}/environ").read_bytes().split(b"\0"), "R exact B ownership")
        descriptor = os.pidfd_open(old["pid"])
        record["death"] = dict(pid=old["pid"],start=old["start"],intent=time.monotonic())
        save(c)
        try:
            require(identity(old["pid"]) == old["start"], "R exact B before pidfd kill")
            signal.pidfd_send_signal(descriptor,signal.SIGKILL)
            record["death"]["signalled"] = time.monotonic()
        finally:
            os.close(descriptor)
        processes = [*tree["chain"],launch["child"]]
        request = dict(action="E07-R-only-close", processes=processes, daemon=old,
                       controller=process_record(os.getpid()), term_end=min(time.monotonic()+2,begin+17), end=begin+24)
        record["close_request"] = request
        temporary = c.root/"coord/r-close-request.tmp"
        temporary.write_text(json.dumps(request))
        temporary.replace(c.root/"coord/r-close-request.json")
        record["death"]["exit"] = c.daemon.wait(timeout=c.remaining(begin+24-time.monotonic()))
        record["death"]["waited"] = time.monotonic()
        require(record["death"]["exit"] == -9 and identity(old["pid"]) is None, "actual B Popen exit minus9")
        def closed():
            path = c.root/"coord/r-close-receipt.json"
            receipt = json.loads(path.read_text()) if path.exists() else None
            observed = closure(c,tree,launch["child"])
            record.setdefault("closure_samples",[]).append(observed)
            record["close_receipt"] = receipt
            if receipt and "error" in receipt:
                raise RuntimeError("R owned close failed: "+receipt["error"])
            return receipt and receipt["ok"] and all(p["observed_start"] is None for p in observed["processes"]) and not any(g["pids"] for g in observed["groups"].values()) and listener_absent(old["port"])
        c.poll(closed,"R numeric death, targeted reap, old tree/listener closure",cap=begin+24-time.monotonic())
        record["death"]["listener_absent"] = True
        record["death"]["closed"] = time.monotonic()
        record["units"] = []
        for group in record["closure_samples"][-1]["groups"]:
            unit = group.rsplit("/",1)[-1]
            argv = ["/usr/bin/systemctl","--user","show",unit,"--property=LoadState,ActiveState"]
            env = {k:c.env[k] for k in ("XDG_RUNTIME_DIR","DBUS_SESSION_BUS_ADDRESS") if k in c.env}
            env.update(PATH="/usr/bin:/bin",LANG="C.UTF-8")
            result = subprocess.run(argv,capture_output=True,env=env,timeout=c.remaining())
            c.remaining()
            value = dict(unit=unit,argv=argv,exit=result.returncode,stdout=public_stream(result.stdout,c.secrets,False),stderr=public_stream(result.stderr,c.secrets,False),at=time.monotonic())
            record["units"].append(value)
            require("ActiveState=inactive" in value["stdout"]["text"] and not value["stdout"]["truncated"], "R old unit inactive")
        connection.settimeout(c.remaining())
        require(stream.read(1) == b"", "R dead provider EOF")
        c.remaining()
        record["provider_eof"] = dict(bytes=0,at=time.monotonic())
        stream.close()
        connection.close()
        del c.holds[start["session"]]
        record["down"] = {org:store.cancellation_frame() for org,store in c.stores.items()}
        require(record["down"] == record["predeath"]["sql"] and identity(row["executor_pid"]) is None, "down-state unchanged with numeric executor PID absent")
        record["numeric_absent"] = dict(pid=row["executor_pid"],observed_start=identity(row["executor_pid"]),at=time.monotonic())

        phase("new-daemon-readiness",50)
        record["files_down"] = files(c)
        require(record["files_down"] == record["files_before"], "same files before C launch")
        program = f"import sys,runpy;sys.path.insert(0,{str(c.source)!r});runpy.run_module('runtime.daemon',run_name='__main__')"
        record["boot_c"] = dict(entered=time.monotonic(),launches=0)
        save(c)
        require(identity(row["executor_pid"]) is None, "numeric executor absent at C spawn boundary")
        c.daemon = subprocess.Popen([c.python,"-I","-c",program],cwd=c.root,env=c.env,
                                   stdout=c.daemon_log,stderr=subprocess.STDOUT,start_new_session=True)
        new = record["boot_c"]
        new.update(pid=c.daemon.pid,start=identity(c.daemon.pid),launches=1)
        register_owned(c,[c.daemon.pid])
        c.boots.append(new)
        def ready():
            require(c.daemon.poll() is None and identity(new["pid"]) == new["start"], "R C exact live process")
            home = c.root/"daemon"
            token_path,port_path = home/"daemon.token",home/"daemon.port"
            if token_path.exists():
                token = token_path.read_text().strip()
                if token:
                    c.secrets.add(token)
                    c.remember_thread_secrets([])
                    new["discovered_bearer_sha256"] = hashlib.sha256(token.encode()).hexdigest()
            if port_path.exists():
                port = int(port_path.read_text())
                ports = json.loads((c.root/"ports.json").read_text())
                if port not in ports:
                    (c.root/"ports.json").write_text(json.dumps([*ports,port]))
                new.setdefault("discovered_ports",[])
                if port not in new["discovered_ports"]:
                    new["discovered_ports"].append(port)
            save(c)
            if not (home/"daemon.pid").exists() or int((home/"daemon.pid").read_text()) != new["pid"]:
                return False
            if not token_path.exists() or not port_path.exists():
                return False
            c.port,c.token = port,token
            try:
                health = c.get("/api/v1/health")
            except ConnectionError:
                return False
            new.update(port=c.port,health=health,listener=listener_owner(new["pid"],c.port),ready=time.monotonic())
            return health["status"] == "ok"
        c.poll(ready,"R one same-files daemon C")
        require(c.token == old_token and new["start"] is not None and (new["pid"],new["start"]) != (old["pid"],old["start"]), "R persistent bearer and new boot identity")
        record["bearer_c_sha256"] = hashlib.sha256(c.token.encode()).hexdigest()
        c.validate_bindings()
        record["files_after"] = files(c)
        require(record["files_after"] == record["files_before"], "R same files/source/registration after C")
        c.relay.upstream = c.port
        c.browser.base = f"http://127.0.0.1:{c.port}"

        phase("recovery-readback",60)
        fresh = frame(c,task,task)
        record["recovered"] = fresh
        recovered(c,record["down"],fresh["sql"],start)
        record["view"] = c.browser.e07_detail(task,"failed","e07-restart.png")
        record["retained_views"] = [c.browser.e07_detail(c.e07[k]["task"],"cancelled") for k in ("parent","worker")]
        record["retained_views"].append(c.browser.e07_detail(c.e07["completed_target"]["id"],"completed"))
        record["threads"] = []
        for thread in fresh["sql"]["alpha"]["tables"]["threads"]:
            tid = thread["id"]
            public = c.get(f"/api/v1/orgs/alpha/threads/{tid}")
            record["threads"].append(dict(thread=tid,detail=public,browser=c.browser.restart_thread(tid,public)))
        record["authenticated"] = time.monotonic()

        phase("forward-and-observe",65)
        require(c.case_deadline-time.monotonic() > 1, "R full one-second window admission")
        relay.upstream = c.port
        relay.deadline = c.case_deadline
        record["released"] = time.monotonic()
        relay.release.set()
        require(relay.completed.wait(c.remaining()) and not relay.errors, "R original upstream response")
        c.poll(lambda: relay.selected.get("closed"),"R downstream outcome")
        require(relay.selected["status"] == 409 and relay.selected["response"] == {"detail":{"code":"task_not_active","task_id":task,"status":"failed","cancelled":False}}
                and relay.selected["downstream"] in {"EOF","peer-gone"}, "R literal failed-task denial separate from dead client")
        window = dict(entered=time.monotonic(),samples=[])
        record["window"] = window
        window["end"] = window["entered"]+1
        require(window["end"] < c.case_deadline, "R preserve entire second")
        while True:
            sample = frame(c,task,task)
            window["samples"].append(sample)
            require(sample["sql"] == fresh["sql"] and sample["public"] == fresh["public"]
                    and sample["witnesses"] == fresh["witnesses"], "R unchanged full recovery after old request")
            host(sample,0,0,0,dict(published_total=0,recent=[]))
            previous = window["entered"] if len(window["samples"]) == 1 else window["samples"][-2]["entered"]
            require(sample["entered"]-previous <= .05 and sample["finished"]-sample["entered"] <= .05, "R fresh <=50ms window samples")
            c.remaining()
            if sample["entered"] >= window["end"]:
                break
            time.sleep(min(.01,max(0,window["end"]-time.monotonic())))
        window["finished"] = time.monotonic()

        phase("final-content-and-close",75)
        record["final"] = [frame(c,task,task),frame(c,task,task)]
        require(host(record["final"][0],0,0,0,dict(published_total=0,recent=[])) == host(record["final"][1],0,0,0,dict(published_total=0,recent=[])), "two healthy empty C0 projections")
        require(all(f["sql"] == fresh["sql"] and f["public"] == fresh["public"] and f["witnesses"] == fresh["witnesses"] for f in record["final"]), "R final full content")
        require(len(c.starts) == len({s["session"] for s in c.starts}) == 40 and c.starts[:39] == prefix["starts"] and c.starts[39:] == [start] and not c.holds, "one R authentic suffix only")
        relay.deadline = c.case_deadline
        record["transport_closure"] = relay.close(c.case_deadline)
        require(not any(record["transport_closure"][k] for k in ("listener_alive","workers_alive","sockets","errors")), "R relay complete closure")
        c.late_transport = None
        record["ports"] = json.loads((c.root/"ports.json").read_text())
        c.remaining()
        record["phases"][-1]["finished"] = record["finished"] = time.monotonic()
        save(c)
        for variant in VARIANTS:
            c.mark(variant,dict(e07_file="e07-restart.json",source_sha=c.revision))
    finally:
        save(c)
        if hasattr(c,"case_deadline"):
            del c.case_deadline
