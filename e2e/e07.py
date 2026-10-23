"""C only: public tree cancellation and the original delayed provider callback.

Abrupt restart remains held. No product import, writer, retry, or cleanup rescue. Missing
partial evidence is a failure; authored predicates are not execution proof.
"""
from __future__ import annotations

import hashlib
import json
import os
import select
import subprocess
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from late_transport import LateTransport, process_record, public_stream
from oracles import require
from owned import identity

if TYPE_CHECKING:
    from controller import Controller

VARIANTS = ["E07.cancel-cascade-browser", "E07.cancel-terminal-unchanged", "E07.cancelled-late-cli"]
DESIGN = "5e9f7f401389841748b0e018ff9988e126f7a2511dabcb140d19ee2e6dc580a7"


def witnesses(c: Controller) -> tuple[bytes, list[dict]]:
    raw = (c.root / "witness/identities.jsonl").read_bytes()
    require(raw.endswith(b"\n"), "E07 incomplete witness record")
    return raw, [json.loads(line) for line in raw.splitlines()]


def save(c: Controller) -> None:
    c.capture_thread_secrets()
    (c.root / "raw/e07.json").write_text(c.sanitize(json.dumps(c.e07, indent=2)))


def frame(c: Controller, parent: str | None = None, child: str | None = None) -> dict:
    record = dict(entered=time.monotonic(), sql={}, public={}, http_indices={})
    for org in ("alpha", "beta"):
        record["sql"][org] = c.stores[org].cancellation_frame()
        record["public"][org] = c.get(f"/api/v1/orgs/{org}/tasks?limit=100")
        record["http_indices"][org] = len(c.http)-1
        require(record["public"][org]["next_cursor"] is None, "E07 full public task page")
    if parent and child:
        for task in (parent, child):
            base = f"/api/v1/orgs/alpha/tasks/{task}"
            record["public"][task], record["http_indices"][task] = {}, {}
            for suffix in ("", "/recall", "/recall?tree=true"):
                record["public"][task][suffix] = c.get(base + suffix)
                record["http_indices"][task][suffix] = len(c.http)-1
    raw, rows = witnesses(c)
    record.update(metrics=c.get("/api/v1/metrics"), witnesses=rows, witness_bytes=len(raw),
                  pending=bool(select.select([c.server], [], [], 0)[0]))
    authenticate_public(record, c.http)
    public_content(record["public"])
    record["finished"] = time.monotonic()
    return record


def host(record: dict, admitted: int, released: int, active: int, previous: dict) -> dict:
    m = record["metrics"]
    h = m["host_sessions"]
    a, r = h["admission"], h["receipts"]
    require((a["admitted_total"], a["released_total"], r["published_total"], a["active"], a["queue_depth"],
             m["executor_sessions_active"], m["run_step_queue_depth"])
            == (admitted, released, released, active, 0, active, 0), "E07 exact host state", h)
    require(a["cancelled_queued_total"] == 0 and not a["shutdown"] and h["wired"]
            and h["backend"]["healthy"] and h["backend"]["name"] != "passthrough"
            and not h["residue"]["admission_blocked"] and h["residue"]["survivors_count"] == 0,
            "E07 healthy containment, no queued cancellation")
    require(r["window_size"] == len(r["recent"]) == r["quiescent_count"] == released <= 64
            and r["with_residue_count"] == 0
            and all(x["quiescent"] and x["survivors_count"] == 0 for x in r["recent"]), "E07 complete quiescent history")
    require(r["recent"][released-previous["published_total"]:] == previous["recent"], "E07 full ordered duplicate-preserving tail")
    require(not record["pending"], "E07 no unaccounted peer")
    return dict(admission=a, receipts=r, executor_sessions_active=m["executor_sessions_active"], run_step_queue_depth=0)


def register_owned(c: Controller, pids: list[int]) -> None:
    """Controller is the sole registry writer; publish the full union at once."""
    path = c.root / "owned.json"
    prior = json.loads(path.read_text()) if path.exists() else []
    temporary = c.root / "owned.tmp"
    temporary.write_text(json.dumps(sorted(set(prior + pids))))
    temporary.replace(path)


def command(c: Controller, start: dict, action: str, wait: bool) -> dict:
    connection, stream = c.holds[start["session"]]
    record = dict(action=action, provider=start, entered=time.monotonic(), wire_begin=len(c.relay.records))
    c.e07_commands.append(record)
    end = c.case_deadline
    # Held CLI retains original C end, so it cannot time out before cancellation.
    instruction = dict(action=action, execution_deadline=c.e07["end"] if not wait else end, ack_deadline=end)
    record["instruction"] = instruction
    connection.settimeout(c.remaining())
    stream.write((json.dumps(instruction) + "\n").encode())
    connection.settimeout(c.remaining())
    stream.flush()
    connection.settimeout(c.remaining())
    launch = json.loads(stream.readline(131072))["e07_launch"]
    record["launch"] = launch
    c.remaining()
    c.e07["owned"].append(launch["child"])
    register_owned(c, [launch["child"]["pid"]])
    require(launch["provider"] == {k:start[k] for k in ("task", "session", "agent", "org", "pid")}
            and launch["source_sha"] == c.revision and launch["python"] == c.python
            and launch["execution_deadline"] == instruction["execution_deadline"], "E07 actual launch authority")
    require(launch == json.loads((c.root / "coord" / ("e07-launch-" + start["session"] + ".json")).read_text()),
            "E07 private durable launch acknowledgement")
    if wait:
        connection.settimeout(c.remaining())
        receipt = json.loads(stream.readline(131072))["e07_return"]
        record.update(receipt=receipt, wire=list(c.relay.records[record["wire_begin"]:]), finished=time.monotonic())
        require(receipt["launch"] == launch and receipt["exit"] == 0 and not receipt["timed_out"]
                and all(not receipt[k]["truncated"] and not receipt[k]["incomplete"] for k in ("stdout", "stderr")),
                "E07 genuine parent callback return")
        require(len(record["wire"]) == 1 and record["wire"][0]["status"] == 200
                and record["wire"][0]["response"] == {"ok": True} and not record["wire"][0]["dropped"], "E07 one accepted callback")
        c.payloads[start["session"]] = launch["payload"]
    save(c)
    return record


def register_tree(c: Controller, start: dict) -> dict:
    row = c.stores["alpha"].query("SELECT executor_pid FROM tasks WHERE id=?", (start["task"],))[0]
    launch_pid = row["executor_pid"]
    require(type(launch_pid) is int and launch_pid > 1, "E07 real launch root")
    chain = []
    pid = start["pid"]
    while pid > 1 and len(chain) < 16:
        proc = process_record(pid)
        require(f"E2E_ROOT={c.root}".encode() in Path(f"/proc/{pid}/environ").read_bytes().split(b"\0"), "E07 tree ownership")
        register_owned(c, [pid])
        c.e07["owned"].append(proc)
        chain.append(proc)
        if pid == launch_pid:
            break
        pid = proc["ppid"]
    require(chain[-1]["pid"] == launch_pid and launch_pid != start["pid"], "E07 separate launch and plan ancestry")
    return dict(launch_pid=launch_pid, chain=chain)


def closure(c: Controller, tree: dict, cli: dict) -> dict:
    processes = [*tree["chain"], cli]
    rows = [dict(pid=p["pid"], start=p["start"], observed_start=identity(p["pid"])) for p in processes]
    groups = {}
    for proc in processes:
        for line in proc["cgroup"].splitlines():
            if line.startswith("0::"):
                group = line[3:]
                leaf = group.rsplit("/", 1)[-1]
                if leaf.startswith("happyranch-") and leaf.endswith((".service", ".scope")):
                    directory = Path("/sys/fs/cgroup") / group.lstrip("/")
                    members = []
                    if directory.exists():
                        for file in [directory / "cgroup.procs", *directory.glob("**/cgroup.procs")]:
                            try:
                                members.extend(int(x) for x in file.read_text().split())
                            except FileNotFoundError:
                                continue
                    groups[group] = dict(exists=directory.exists(), pids=sorted(set(members)))
    return dict(processes=rows, groups=groups, sampled=time.monotonic(),
                pending=bool(select.select([c.server], [], [], 0)[0]))


def cancelled(c: Controller, value: dict, parent: dict, worker: dict, result: dict) -> None:
    tables = value["sql"]["alpha"]["tables"]
    tasks = {r["id"]:r for r in tables["tasks"]}
    p, w = tasks[parent["task"]], tasks[worker["task"]]
    require(len(tasks) == 22 and len(tables["task_results"]) == 30
            and sum(t["status"] == "completed" for t in tasks.values()) == 20, "E07 exact final alpha population")
    for t, start in ((p,parent), (w,worker)):
        require(t["status"] == "cancelled" and t["block_kind"] is None
                and t["note"] == "cancelled by founder: E07_CANCEL_TREE"
                and t["cancelled_at"] is not None and t["completed_at"] == t["cancelled_at"]
                and t["current_session_id"] == start["session"], "E07 terminal task fields", t)
        public = value["public"][t["id"]][""]["task"]
        require(all(public[k] == (sql_datetime(t[k]) if k in ("cancelled_at","completed_at") else t[k])
                    for k in ("status","block_kind","note","cancelled_at","completed_at","current_session_id")), "E07 fresh public task matches coherent SQL")
    require(p["cancelled_at"] == w["cancelled_at"] and w["parent_task_id"] == p["id"], "E07 single cascade stamp and lineage")
    tree = value["public"][p["id"]]["/recall?tree=true"]
    require(tree["task_id"] == p["id"] and tree["status"] == "cancelled"
            and len(tree["children"]) == 1 and tree["children"][0]["task_id"] == w["id"]
            and tree["children"][0]["status"] == "cancelled", "E07 actual public cancelled recall tree")
    for task in (p["id"],w["id"]):
        recall = value["public"][task]["/recall"]
        require(recall["task_id"] == task and recall["status"] == "cancelled", "E07 actual public terminal recall")
    own_results = [r for r in tables["task_results"] if r["task_id"] in (p["id"],w["id"])]
    require(own_results == [result], "E07 parent result retained, worker result absent")
    audits = [a for a in tables["audit_log"] if a["task_id"] in (p["id"],w["id"])]
    cancellations = [a for a in audits if a["action"] == "task_cancelled"]
    require([a["task_id"] for a in cancellations] == [p["id"],w["id"]]
            and all(a["agent"] == "founder" and json.loads(a["payload"]) == {"rationale":"E07_CANCEL_TREE","cascade":True} for a in cancellations), "E07 exact two founder cancellation audits")
    require(sum(a["task_id"] == w["id"] and a["action"] == "session_end" for a in audits) == 1
            and not any(a["task_id"] == w["id"] and a["action"] in {"completion_report","orchestration_step"} for a in audits), "E07 child ended once without decision/consumption")


def sql_datetime(value: str | None) -> str | None:
    # TaskRecord datetime -> FastAPI's dict response model -> Pydantic JSON.
    if value is None:
        return None
    stamp = datetime.fromisoformat(value).isoformat()
    return stamp[:-6]+"Z" if stamp.endswith("+00:00") else stamp


def pause_content(value: dict) -> dict:
    """Comparison copy only. Every raw response keeps its observation metadata."""
    require(isinstance(value, dict), "E07 Pause projection object")
    stamp = value["observed_at"]
    require(isinstance(stamp, str), "E07 required Pause observation string")
    parsed = datetime.fromisoformat(stamp)
    require("T" in stamp and parsed.tzinfo is not None and parsed.utcoffset() == timedelta(0),
            "E07 timezone-aware UTC Pause observation")
    blockers = value["blockers"]
    require(isinstance(blockers, list), "E07 ordered Pause blockers")
    for blocker in blockers:
        require(isinstance(blocker, dict) and isinstance(blocker["observed_at"], str)
                and blocker["observed_at"] == stamp, "E07 coherent blocker observation")
    # No recursion: durable timestamps, unknown fields and duplicate/order remain.
    result = dict(value)
    del result["observed_at"]
    result["blockers"] = [{k:v for k,v in b.items() if k != "observed_at"} for b in blockers]
    return result


def detail_content(value: dict) -> str:
    return json.dumps({**value, "pause": pause_content(value["pause"])}, sort_keys=True)


def public_content(value: dict) -> str:
    result = {}
    for key, body in value.items():
        if key in ("alpha", "beta"):
            require(isinstance(body["tasks"], list), "E07 public list shape")
            result[key] = {**body, "tasks": [{**t, "pause": pause_content(t["pause"])} for t in body["tasks"]]}
        else:
            # Only the detail envelope has Pause. Recalls remain complete.
            result[key] = {**body, "": json.loads(detail_content(body[""]))}
    # JSON comparison also distinguishes false from 0 and lists from objects.
    return json.dumps(result, sort_keys=True)


def authenticate_public(record: dict, http: list[dict]) -> None:
    require(set(record["http_indices"]) == set(record["public"]), "E07 complete HTTP observation indices")
    for key, body in record["public"].items():
        if key in ("alpha", "beta"):
            entries = [(record["http_indices"][key], f"/api/v1/orgs/{key}/tasks?limit=100", body)]
        else:
            require(set(record["http_indices"][key]) == set(body), "E07 detail/recall HTTP indices")
            entries = [(record["http_indices"][key][suffix], f"/api/v1/orgs/alpha/tasks/{key}"+suffix, response)
                       for suffix,response in body.items()]
        for index, path, response in entries:
            require(type(index) is int and 0 <= index < len(http), "E07 actual HTTP observation index")
            observed = http[index]
            require((observed["method"], observed["path"], observed["status"]) == ("GET", path, 200)
                    and json.dumps(observed["response"], sort_keys=True) == json.dumps(response, sort_keys=True),
                    "E07 exact same-observation raw response join")


def content(record: dict) -> dict:
    return dict(sql=record["sql"], public=public_content(record["public"]))


def terminal_cli(c: Controller, task: str, status: str, rationale: str) -> dict:
    from thread_provider import redaction_context
    secrets = redaction_context(c.root, None)
    argv = [str(c.root / "bin/happyranch"), "cancel", task, "--org", "alpha", "--rationale", rationale]
    record = dict(action="terminal-cancel", argv=argv, entered=time.monotonic(), task=task, status=status,
                  wire_begin=len(c.relay.records), source_sha=c.revision, python=c.python)
    c.e07_commands.append(record)
    child = subprocess.Popen(argv, cwd=c.root, env=dict(c.env, HAPPYRANCH_DAEMON_HOME=str(c.root / "client")),
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    register_owned(c, [child.pid])
    record["child"] = process_record(child.pid)
    c.e07["owned"].append(record["child"])
    try:
        out, err = child.communicate(timeout=c.remaining())
    except subprocess.TimeoutExpired as exc:
        record.update(stdout=public_stream(exc.stdout or b"",secrets,True), stderr=public_stream(exc.stderr or b"",secrets,True), timed_out=True)
        raise RuntimeError("E07 terminal CLI timeout") from None
    record.update(exit=child.returncode, stdout=public_stream(out,secrets,child.returncode < 0),
                  stderr=public_stream(err,secrets,child.returncode < 0), wire=list(c.relay.records[record["wire_begin"]:]),
                  finished=time.monotonic(), observed_start=identity(child.pid))
    require(child.returncode == 1 and record["stdout"]["text"] == f"Task {task} is already {status}; nothing to cancel.\n"
            and record["stderr"]["text"] == "" and not record["stdout"]["truncated"], "E07 literal terminal CLI human response")
    posts = [w for w in record["wire"] if w["method"] == "POST"]
    require(len(posts) == 1 and posts[0]["path"] == f"/api/v1/orgs/alpha/tasks/{task}/cancel"
            and posts[0]["request"] == {"rationale":rationale,"cascade":True}
            and posts[0]["status"] == 409 and posts[0]["response"] == {"detail":{"code":"task_already_terminal","current_status":status}}, "E07 exact terminal guard HTTP")
    return record


def exercise(c: Controller) -> None:
    # Archive the fully evaluated original53 before entering C. This is scenario
    # work under the unchanged original end, never borrowed export reserve.
    c.remaining()
    c.evidence()
    prefix_content = (c.root / "raw/observations.json").read_bytes()
    (c.root / "raw/e07-prefix.json").write_bytes(prefix_content)
    archived_prefix = json.loads(prefix_content)
    c.remaining()
    begin = time.monotonic()
    prior = next(d["threads_cases"] for d in c.durable if "threads_cases" in d)
    raw, prefix = witnesses(c)
    require(len(c.results) == 53 and len(c.starts) == 36 and prefix == prior["witnesses"], "E07 enters only after original53 and full E05 witness prefix")
    c.e07 = dict(variants=VARIANTS, design_sha256=DESIGN, source_sha=c.revision, python=c.python,
                 entered=begin, scenario_deadline=c.deadline, end=min(begin+50,c.deadline),
                 prefix=dict(witness_bytes=len(raw), witness_records=len(prefix), witness_sha256=hashlib.sha256(raw).hexdigest(),
                             observer_end=len(archived_prefix["provider_observer"]), archive_sha256=hashlib.sha256(prefix_content).hexdigest(), command_count=len(c.commands), e05_finished=prior["finished"], variants=list(c.results)),
                 phases=[], anchors=[], owned=[], frames=[])
    c.e07["commands"] = c.e07_commands
    c.e07["source_files"] = {name:hashlib.sha256((c.source/name).read_bytes()).hexdigest() for name in (
        "e2e/owned.py","e2e/transport.py","e2e/jobs.py","e2e/threads.py",
        "e2e/thread_provider.py","e2e/pause_browser.py","runtime/daemon/__main__.py","runtime/daemon/paths.py","e2e/e07.py","e2e/late_transport.py","e2e/provider.py","e2e/controller.py","e2e/oracles.py",
        "e2e/browser.py","e2e/aggregate.py","e2e/run.py","e2e/manifest.json","cli/commands/tasks.py",
        "runtime/daemon/routes/tasks.py","runtime/models.py","runtime/infrastructure/db/tasks.py",
        "runtime/infrastructure/task_pause_controls.py","runtime/orchestrator/run_step.py",
        "web/src/features/tasks/CancelTaskDialog.tsx")}
    c.e07["ports"] = json.loads((c.root / "ports.json").read_text())
    def phase(name: str, offset: int) -> None:
        if c.e07["phases"]:
            require(time.monotonic() <= c.e07["phases"][-1]["end"], "E07 prior partition overrun")
        c.case_deadline = min(begin+offset,c.e07["end"],c.deadline)
        c.e07["phases"].append(dict(name=name, entered=time.monotonic(), end=c.case_deadline))
        c.publish_end()
        c.remaining()
    outgoing = outgoing_tb = None
    try:
        try:
            phase("capture",16)
            baseline = frame(c)
            c.e07["baseline"] = baseline
            host(baseline,24,24,0,prior["final_anchor"]["projection"]["receipts"])
            c.e07["anchors"].append(dict(name="E05-end-B24",samples=[baseline]))
            P = c.create("alpha","E07_CANCEL_PARENT")
            parent = c.start("alpha",P,"case_manager")
            c.e07["parent"] = parent
            parent_command = command(c,parent,"e07-parent",True)
            c.e07["parent_command"] = parent_command
            c.release(parent)
            worker = c.start("alpha",None,"case_worker")
            W = worker["task"]
            c.e07["worker"] = worker
            tree = register_tree(c,worker)
            c.e07["tree"] = tree
            result = c.stores["alpha"].result(parent_command["launch"]["payload"])
            require(type(result["id"]) is int, "E07 real INTEGER parent result")
            c.e07["parent_result"] = result
            def parked() -> dict | None:
                snap = frame(c,P,W)
                c.e07["frames"].append(snap)
                t = {x["id"]:x for x in snap["sql"]["alpha"]["tables"]["tasks"]}
                if t[P]["block_kind"] != "delegated":
                    return None
                require(t[P]["status"] == t[W]["status"] == "in_progress" and t[W]["parent_task_id"] == P
                        and t[W]["brief"] == "E07_CANCEL_CHILD" and t[W]["block_kind"] is None, "E07 genuine delegated tree")
                a = snap["metrics"]["host_sessions"]["admission"]
                if a["released_total"] != 25:
                    return None
                host(snap,26,25,1,prior["final_anchor"]["projection"]["receipts"])
                return snap
            live = c.poll(parked,"E07 P released, W sole live")
            second = frame(c,P,W)
            require(host(second,26,25,1,prior["final_anchor"]["projection"]["receipts"]) == host(live,26,25,1,prior["final_anchor"]["projection"]["receipts"]), "E07 two live projections")
            c.e07["live"] = [live,second]
            c.e07["anchors"].append(dict(name="P-success-W-live-B25",samples=[live,second]))
            new = second["metrics"]["host_sessions"]["receipts"]["recent"][0]
            require(new["terminal_reason"] == "success" and new["invocation_kind"] == "task", "E07 P success receipt")
            wire_payload = dict(session_id=worker["session"],agent="case_worker",status="completed",confidence=90,
                                output_summary="E07 cancelled child must not complete",risks_flagged=[],dependencies=[],suggested_reviewer_focus=[])
            c.capture_thread_secrets()
            relay = LateTransport(c.root,c.port,W,wire_payload,c.token,c.secrets,c.e07["end"])
            c.late_transport = relay
            c.e07["ports"] = json.loads((c.root / "ports.json").read_text())
            c.e07["transport"] = dict(listener=relay.listener, port=relay.port, upstream=c.port,
                                      daemon=process_record(c.daemon.pid), bearer_sha256=hashlib.sha256(("Bearer "+c.token).encode()).hexdigest(), records=relay.records, accepted=relay.accepted)
            home = c.root / "e07-client"
            home.mkdir(mode=0o700)
            for key,value in (("port",str(relay.port)),("token",c.token)):
                path = home / ("daemon."+key)
                path.write_text(value)
                path.chmod(0o600)
            held = command(c,worker,"e07-held",False)
            c.e07["held"] = held
            require(relay.captured.wait(c.remaining()) and not relay.errors, "E07 entire real CLI request captured")
            c.e07["provenance"] = relay.provenance(held["launch"])
            raw, rows = witnesses(c)
            call = [x for x in rows if x["kind"] == "callback" and x["pid"] == held["launch"]["child"]["pid"]]
            require(len(call) == 1 and call[0]["ppid"] == worker["pid"]
                    and call[0]["argv_sha256"] == hashlib.sha256(json.dumps(held["launch"]["argv"][1:]).encode()).hexdigest(), "E07 live real wrapper witness")
            c.e07["held_witness"] = call[0]
            require(relay.forward_count == 0 and "forwarded" not in relay.selected, "E07 no upstream request before public cancellation")
            save(c)

            phase("cancel-and-close",28)
            def browser_expected(snapshot: dict) -> dict:
                result = {}
                for task in (P, W):
                    detail = snapshot["public"][task][""]
                    core = {key: detail["task"][key] for key in (
                        "task_id", "brief", "team", "status", "parent_task_id", "assigned_agent",
                        "block_kind", "current_session_id", "note")}
                    if core["status"] == "in_progress" and task == P:
                        core["child_wait_state"] = "waiting_children"
                    result[task] = dict(task=core, recall=snapshot["public"][task]["/recall?tree=true"],
                                        result_ids=[row["id"] for row in detail["results"]])
                    if core["status"] == "cancelled":
                        result[task]["detail_snapshot"] = detail
                return result
            c.e07["browser_cancel"] = c.browser.e07_cancel(P,W, expected=browser_expected(second))
            def closed() -> dict | None:
                observed = closure(c,tree,held["launch"]["child"])
                c.e07.setdefault("closure_samples",[]).append(observed)
                if any(p["start"] == p["observed_start"] for p in observed["processes"]) or any(g["pids"] for g in observed["groups"].values()):
                    return None
                require(not observed["pending"], "E07 no resumption peer")
                snap = frame(c,P,W)
                c.e07["frames"].append(snap)
                audits = snap["sql"]["alpha"]["tables"]["audit_log"]
                if not any(a["task_id"] == W and a["action"] == "session_end" for a in audits):
                    return None
                if snap["metrics"]["host_sessions"]["admission"]["released_total"] != 26:
                    return None
                cancelled(c,snap,parent,worker,result)
                host(snap,26,26,0,second["metrics"]["host_sessions"]["receipts"])
                return snap
            freeze = c.poll(closed,"E07 product closure before rescue")
            freeze2 = frame(c,P,W)
            require(content(freeze) == content(freeze2)
                    and host(freeze2,26,26,0,second["metrics"]["host_sessions"]["receipts"]) == host(freeze,26,26,0,second["metrics"]["host_sessions"]["receipts"]), "E07 settled cancellation frame")
            receipt = freeze["metrics"]["host_sessions"]["receipts"]["recent"][0]
            require(receipt["terminal_reason"] == "cancelled" and receipt["invocation_kind"] == "task", "E07 one active cancellation receipt")
            c.e07["freeze"] = [freeze,freeze2]
            unit_rows = []
            for group in c.e07["closure_samples"][-1]["groups"]:
                unit = group.rsplit("/",1)[-1]
                argv = ["/usr/bin/systemctl","--user","show",unit,"--property=LoadState,ActiveState"]
                env = {key:c.env[key] for key in ("XDG_RUNTIME_DIR","DBUS_SESSION_BUS_ADDRESS") if key in c.env}
                env.update(PATH="/usr/bin:/bin",LANG="C.UTF-8")
                observed = subprocess.run(argv,capture_output=True,timeout=c.remaining(),env=env)
                record = dict(unit=unit,argv=argv,exit=observed.returncode,
                              stdout=public_stream(observed.stdout,c.secrets,False),stderr=public_stream(observed.stderr,c.secrets,False),at=time.monotonic())
                unit_rows.append(record)
                c.e07["units"] = unit_rows
                require(not record["stdout"]["truncated"] and "ActiveState=inactive" in record["stdout"]["text"],"E07 product unit inactive")
            c.e07["units"] = unit_rows
            # Freeze only after unit inactivity too, before any terminal CLI action.
            freeze,freeze2 = frame(c,P,W),frame(c,P,W)
            cancelled(c,freeze,parent,worker,result)
            require(content(freeze) == content(freeze2)
                    and host(freeze,26,26,0,second["metrics"]["host_sessions"]["receipts"])
                    == host(freeze2,26,26,0,second["metrics"]["host_sessions"]["receipts"]), "E07 Fcancel after all product closure")
            c.e07["freeze"] = [freeze,freeze2]
            c.e07["anchors"].append(dict(name="cancelled-B26",samples=[freeze,freeze2]))
            connection, stream = c.holds.pop(worker["session"])
            connection.settimeout(c.remaining())
            c.e07["provider_eof"] = dict(bytes=len(stream.read(1)),at=time.monotonic())
            require(c.e07["provider_eof"]["bytes"] == 0,"E07 killed provider EOF, no fabricated CLI return")
            stream.close()
            connection.close()

            phase("terminal-ui-and-refusals",36)
            roots_expected = dict(org="alpha", task=dict(task_id=P, brief="E07_CANCEL_PARENT", team="engineering",
                status="cancelled", parent_task_id=None, assigned_agent="case_manager", block_kind=None,
                current_session_id=parent["session"], note="cancelled by founder: E07_CANCEL_TREE"),
                severity_rollup="cancelled", pause=dict(org_slug="alpha", root_task_id=P,
                    lifecycle_status="cancelled", held=False, effective_hold=False, control_state="unheld",
                    generation=0, paused_at=None, resumed_at=None),
                forbidden=[W])
            c.e07["views"] = c.browser.e07_terminal(P,W, expected=browser_expected(freeze), roots_expected=roots_expected)
            for view, task in zip(c.e07["views"][:2], (P,W)):
                require(detail_content(view["detail"]) == detail_content(freeze["public"][task][""])
                        and view["recall"] == freeze["public"][task]["/recall?tree=true"], "C complete browser terminal content")
            complete = next(t for t in baseline["sql"]["alpha"]["tables"]["tasks"] if t["parent_task_id"] is None and t["status"] == "completed")
            c.e07["completed_target"] = complete
            c.e07["refusals"] = [terminal_cli(c,P,"cancelled","E07_DUPLICATE"), terminal_cli(c,complete["id"],"completed","E07_TERMINAL")]
            after = frame(c,P,W)
            c.e07["after_refusals"] = after
            require(content(after) == content(freeze),"E07 both terminal refusals leave full content unchanged")

            phase("forward-and-observe",42)
            require(c.case_deadline-time.monotonic() > 1,"E07 full negative window must fit")
            relay.deadline = c.case_deadline
            c.e07["released"] = time.monotonic()
            relay.release.set()
            require(relay.completed.wait(c.remaining()) and not relay.errors,"E07 original request upstream response")
            c.poll(lambda: relay.selected.get("closed"),"E07 downstream outcome")
            require(relay.selected["status"] == 409 and relay.selected["response"] == {"detail":{"code":"task_not_active","task_id":W,"status":"cancelled","cancelled":True}}
                    and relay.selected["downstream"] in {"EOF","peer-gone"},"E07 task-active denial separate from dead CLI")
            window = dict(entered=time.monotonic(),samples=[])
            c.e07["window"] = window
            window["end"] = window["entered"]+1
            require(window["end"] < c.case_deadline,"E07 reserve full second")
            while True:
                sample = frame(c,P,W)
                sample["closure"] = closure(c,tree,held["launch"]["child"])
                window["samples"].append(sample)
                require(content(sample) == content(freeze) and sample["witnesses"] == after["witnesses"],"E07 bounded full content/launch absence")
                host(sample,26,26,0,freeze["metrics"]["host_sessions"]["receipts"])
                require(not any(p["start"] == p["observed_start"] for p in sample["closure"]["processes"])
                        and not any(g["pids"] for g in sample["closure"]["groups"].values()) and not sample["closure"]["pending"],"E07 product stays closed")
                previous = window["entered"] if len(window["samples"]) == 1 else window["samples"][-2]["entered"]
                require(sample["entered"]-previous <= .05 and sample["finished"]-sample["entered"] <= .05,"E07 fresh sample interval <=50ms")
                c.remaining()
                if sample["entered"] >= window["end"]:
                    break
                time.sleep(min(.01,max(0,window["end"]-time.monotonic())))
            window["finished"] = time.monotonic()

            phase("archive",50)
            final = frame(c,P,W)
            c.e07["final"] = final
            c.e07["anchors"].append(dict(name="final-B26",samples=[final]))
            live_tasks = {t["id"]:t for t in second["sql"]["alpha"]["tables"]["tasks"]}
            final_tasks = {t["id"]:t for t in final["sql"]["alpha"]["tables"]["tasks"]}
            changed_fields = {"status","block_kind","note","cancelled_at","completed_at"}
            for task in (P,W):
                require({k:v for k,v in live_tasks[task].items() if k not in changed_fields}
                        == {k:v for k,v in final_tasks[task].items() if k not in changed_fields}, "E07 exact cancellation field delta")
            cancelled(c,final,parent,worker,result)
            require(content(final) == content(freeze) and final["sql"]["beta"] == baseline["sql"]["beta"],"E07 final immutable tree/beta")
            raw, rows = witnesses(c)
            require(len(c.starts) == 38 and len({s["session"] for s in c.starts}) == 38 and not c.holds
                    and sum(w["kind"] == "stub" for w in rows) == 38
                    and sum(w["kind"] == "callback" for w in rows) == len(c.commands)+len(c.provider_commands)+len(c.thread_commands)+len(c.e07_commands),"E07 exact38 starts and every CLI accounted")
            relay.deadline = c.case_deadline
            c.e07["transport_closure"] = relay.close(c.case_deadline)
            c.late_transport = None
            require(not any(c.e07["transport_closure"][k] for k in ("listener_alive","workers_alive","sockets","errors")),"E07 transport final closure")
            c.e07["finished"] = time.monotonic()
            require(c.e07["finished"] < c.case_deadline,"E07 complete evidence within C50")
            save(c)
            for variant in VARIANTS:
                c.mark(variant,dict(e07_file="e07.json",source_sha=c.revision))
        except BaseException as exc:
            c.record_error("controller-action", exc)
            raise
        finally:
            save(c)
    except BaseException as exc:
        # Reserve the combined body/final-save outgoing before recording or release.
        outgoing, outgoing_tb = exc, exc.__traceback__
        try:
            c.record_error("controller-action", exc)
        finally:
            raise outgoing.with_traceback(outgoing_tb)
    finally:
        try:
            try:
                if hasattr(c, "case_deadline"):
                    require(c.e07["phases"] and
                            c.case_deadline is c.e07["phases"][-1]["end"],
                            "E07 case owner unavailable")
                    # ALL entries refuse, including positive/native owners. Only
                    # Controller.phase appends on this sequential main-thread path;
                    # no callback/appender runs between this proof and deletion.
                    require(type(c.timer_phases) is list and not c.timer_phases,
                            "E07 residual phase owner prevents release")
                    try:
                        del c.case_deadline
                    except BaseException as exc:
                        if outgoing is None:
                            outgoing, outgoing_tb = exc, exc.__traceback__
                        c.record_error("controller-action", exc)
                    finally:
                        # Independent of deletion/recorder success once eligible.
                        try:
                            if time.monotonic() < c.deadline:
                                c.publish_end()
                            else:
                                c._timer_sync()
                        except BaseException as exc:
                            if outgoing is None:
                                outgoing, outgoing_tb = exc, exc.__traceback__
                            try:
                                c.record_error("controller-action", exc)
                            finally:
                                c._timer_sync()
                else:
                    c._timer_sync()
            except BaseException as exc:
                # Failed/interrupted proof never enters deletion or publication.
                if outgoing is None:
                    outgoing, outgoing_tb = exc, exc.__traceback__
                try:
                    c.record_error("controller-action", exc)
                finally:
                    c._timer_sync()
        finally:
            if outgoing is not None:
                raise outgoing.with_traceback(outgoing_tb)
