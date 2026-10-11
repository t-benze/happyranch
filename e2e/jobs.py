"""Six finite E04 journeys, appended after the unchanged baseline and Pause.

Only the scheduled launcher calls exercise. All product state is created by
public CLI/browser actions; SQLite is a literal read-only independent oracle.
"""
from __future__ import annotations

import hashlib
import json
import os
import select
import socket
import struct
import time
from pathlib import Path
from typing import TYPE_CHECKING

from oracles import require
from owned import identity

if TYPE_CHECKING:
    from controller import Controller


CASES = (
    ("E04.approve-exit0", "OK", 0, 20, "E04_OK completed exit0"),
    ("E04.reject", "REJECT", None, 15, "E04_REJECT rejected E04_REJECTED_BY_REVIEW"),
    ("E04.completed-exit7", "EXIT7", 7, 20, "E04_EXIT7 completed exit7; operation nonzero"),
    ("E04.failed-timeout", "TIMEOUT", -9, 25, "E04_TIMEOUT failed timeout exit-9"),
    ("E04.two-jobs-A-first", "PAIR", 0, 25, "E04_PAIR A completed exit0; B completed exit7"),
    ("E04.two-jobs-B-first", "PAIR", 7, 25, "E04_PAIR A completed exit0; B completed exit7"),
)


def witnesses(c: Controller) -> list[dict]:
    return [json.loads(line) for line in (c.root / "witness/identities.jsonl").read_text().splitlines()]


def projection(metrics: dict, total: int) -> dict | None:
    """Non-atomic intermediate samples remain evidence; only conjunction settles."""
    h = metrics["host_sessions"]
    a, r = h["admission"], h["receipts"]
    for key in ("admitted_total", "released_total", "cancelled_queued_total", "active", "queue_depth"):
        require(type(a[key]) is int and a[key] >= 0, "available literal admission counter", a)
    for key in ("published_total", "window_size", "quiescent_count", "with_residue_count"):
        require(type(r[key]) is int and r[key] >= 0, "available literal receipt counter", r)
    require(a["admitted_total"] <= total and a["released_total"] <= total and r["published_total"] <= total,
            "no unexplained extra admission/publication", h)
    require(a["cancelled_queued_total"] == 0 and not a["shutdown"], "no cancelled/closed admission", a)
    require(h["wired"] is True and h["backend"]["healthy"] is True and h["backend"]["name"] != "passthrough",
            "wired healthy selected containment", h)
    require(h["residue"]["admission_blocked"] is False and h["residue"]["survivors_count"] == 0,
            "no host residue", h)
    require(r["window_size"] == len(r["recent"]) == r["published_total"] <= 64,
            "no eviction, reset or missing window", r)
    require(r["quiescent_count"] == r["window_size"] and r["with_residue_count"] == 0
            and all(x["quiescent"] is True and type(x["survivors_count"]) is int and x["survivors_count"] == 0 for x in r["recent"]),
            "every anonymous summary quiescent", r)
    if (a["admitted_total"], a["released_total"], r["published_total"], a["active"], a["queue_depth"],
        metrics["executor_sessions_active"], metrics["run_step_queue_depth"]) != (total, total, total, 0, 0, 0, 0):
        return None
    return dict(admission=a, receipts=r, executor_sessions_active=0, run_step_queue_depth=0)


def anchor(c: Controller, name: str, total: int, previous: dict | None = None) -> dict:
    begin = time.monotonic()
    record = dict(name=name, total=total, source_sha=c.revision, daemon_pid=c.daemon.pid,
                  daemon_start=identity(c.daemon.pid), entered=begin, observer_begin=len(c.observer), samples=[])
    c.durable.append(dict(jobs_anchor=record))  # Partial chain is retained on failure.
    last = None
    def settled() -> dict | None:
        nonlocal last
        require(not select.select([c.server], [], [], 0)[0], "no unexpected provider at closed barrier")
        rows = witnesses(c)
        require(sum(r["kind"] == "stub" for r in rows) == len(c.starts), "all provider attempts accounted at barrier")
        entered = time.monotonic()
        metrics = c.get("/api/v1/metrics")
        sample = dict(entered=entered, finished=time.monotonic(), metrics=metrics)
        record["samples"].append(sample)
        p = projection(metrics, total)
        if previous:
            r = metrics["host_sessions"]["receipts"]
            old = previous["projection"]["receipts"]
            require(r["published_total"] >= old["published_total"], "no counter rollback")
            d = r["published_total"] - old["published_total"]
            require(r["recent"][d:] == old["recent"], "ordered anonymous tail, including duplicates")
        if p is None:
            last = None
            return None
        gone = [dict(pid=s["pid"], start=s["pid_start"], observed_start=identity(s["pid"])) for s in c.starts]
        if any(x["start"] == x["observed_start"] for x in gone):
            last = None
            return None
        if p != last:
            last = p
            return None
        record.update(projection=p, finished=time.monotonic(), observer_end=len(c.observer),
                      provider_count=len(c.starts), provider_closure=gone)
        return record
    c.poll(settled, name + " two fresh settled observations", cap=10)
    if previous:
        require((record["daemon_pid"], record["daemon_start"]) == (previous["daemon_pid"], previous["daemon_start"]),
                "no unexplained daemon replacement")
        d = total - previous["total"]
        require(d >= 0 and record["projection"]["receipts"]["recent"][d:] == previous["projection"]["receipts"]["recent"],
                "exact settled window extension")
    return record


def raw_job(c: Controller, task: str, job: str) -> dict:
    rows = c.stores["alpha"].jobs(task)["jobs"]
    matches = [r for r in rows if r["id"] == job]
    require(len(matches) == 1, "unique actual submitted job", rows)
    return matches[0]


def output_paths(c: Controller, job: str) -> list[Path]:
    return [c.root / "runtime/orgs/alpha/jobs" / (job + suffix) for suffix in (".out", ".err")]


def pending(c: Controller, task: str, job: dict, server: socket.socket) -> dict:
    row = raw_job(c, task, job["id"])
    absent = {str(p): not p.exists() for p in output_paths(c, job["id"])}
    snap = c.stores["alpha"].jobs(task)
    audit = [a for a in snap["audit"] if json.loads(a["payload"]).get("script_request_id") == job["id"]]
    record = dict(at=time.monotonic(), row=row, sentinel_absent=not Path(job["sentinel"]).exists(),
                  files_absent=absent, peer_pending=bool(select.select([server], [], [], 0)[0]), audit=audit)
    job["pending"].append(record)
    require(row["status"] == "pending" and all(row[k] is None for k in (
        "started_at", "finished_at", "reviewed_at", "reviewed_by", "exit_code", "reason", "duration_ms",
        "stdout_head", "stderr_head", "stdout_path", "stderr_path", "cwd_resolved", "reject_reason")), "pending literal no execution", row)
    require(record["sentinel_absent"] and all(absent.values()) and not record["peer_pending"], "pending no script/files/peer", record)
    require([a["action"] for a in audit] == ["job_submitted"], "pending no launch audit", audit)
    return record


def script(c: Controller, token: str, label: str, code: int, socket_path: Path) -> tuple[str, str]:
    sentinel = c.root / "coord" / (token + ".started")
    timeout = code == -9
    program = f'''import json, os, signal, socket, sys, time
from pathlib import Path
if {timeout!r}:
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
Path({str(sentinel)!r}).write_text(json.dumps(dict(token={token!r}, pid=os.getpid())))
sys.stdout.write({('E04_' + label + '_OUT' + chr(10))!r}); sys.stdout.flush()
sys.stderr.write({('E04_' + label + '_ERR' + chr(10))!r}); sys.stderr.flush()
end = {c.effective_end()!r}
def remaining():
    value = end - time.monotonic()
    if value <= 0:
        raise TimeoutError("E04 script absolute deadline")
    return min(20, value)
with socket.socket(socket.AF_UNIX) as peer:
    peer.settimeout(remaining())
    peer.connect({str(socket_path)!r})
    peer.settimeout(remaining())
    peer.sendall((json.dumps(dict(token={token!r}, pid=os.getpid())) + "\\n").encode())
    remaining()
    if {timeout!r}:
        while True:
            signal.pause()
    peer.settimeout(remaining())
    released = peer.recv(1)
    remaining()
    if released != b"R":
        raise RuntimeError("missing external release")
raise SystemExit({code})
'''
    return program, str(sentinel)


def group_members(pgid: int) -> list[dict]:
    members = []
    for path in Path("/proc").iterdir():
        if not path.name.isdecimal():
            continue
        try:
            fields = (path / "stat").read_text().rsplit(")", 1)[1].split()
            if int(fields[2]) == pgid:
                members.append(dict(pid=int(path.name), start=fields[19], state=fields[0]))
        except (FileNotFoundError, ProcessLookupError):
            continue
    return members


def run_script(c: Controller, task: str, job: dict, server: socket.socket) -> None:
    job["action"] = c.browser.job_action(job["id"], job["cap"], lambda: pending(c, task, job, server))
    server.settimeout(c.remaining(3))
    connection, _ = server.accept()
    # Retain and register even an unexpected peer before validating its token.
    c.job_connections.append(connection)
    connection.settimeout(c.remaining(3))
    pid, uid, _ = struct.unpack("3i", connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
    start = identity(pid)
    peer = dict(pid=pid, uid=uid, start=start, pgid=os.getpgid(pid), at=time.monotonic())
    c.durable.append(dict(jobs_peer=peer))
    require(uid == os.getuid() and start is not None, "owned live script peer", peer)
    require(f"E2E_ROOT={c.root}".encode() in Path(f"/proc/{pid}/environ").read_bytes().split(b"\0"), "script run ownership")
    owned = json.loads((c.root / "owned.json").read_text())
    (c.root / "owned.tmp").write_text(json.dumps([*owned, pid]))
    (c.root / "owned.tmp").replace(c.root / "owned.json")
    stream = connection.makefile("rb")
    connection.settimeout(c.remaining(3))
    hello = json.loads(stream.readline(4096))
    c.remaining()
    peer["hello"] = hello
    job["peer"] = peer
    require(hello == {"token": job["token"], "pid": pid} and peer["pgid"] == pid, "actual token/PID/PGID handshake", peer)
    require(json.loads(Path(job["sentinel"]).read_text()) == hello, "literal launch sentinel")
    peer["census"] = group_members(pid)
    require(peer["census"] == [dict(pid=pid, start=start, state=peer["census"][0]["state"])], "direct script owns exactly one process")
    job["running"] = raw_job(c, task, job["id"])
    require(job["running"]["status"] == "running" and identity(pid) == start, "genuine live running witness")
    job["running_browser"] = c.browser.job_open(job["id"], "running")
    require(identity(pid) == start, "script live through running browser read")
    if job["code"] != -9:
        connection.settimeout(c.remaining(3))
        connection.sendall(b"R")
        c.remaining()
        job["released"] = time.monotonic()
    def terminal() -> dict | None:
        row = raw_job(c, task, job["id"])
        return row if row["status"] in {"completed", "failed", "rejected"} else None
    job["terminal"] = c.poll(terminal, "real product terminal job", cap=17)
    c.poll(lambda: identity(pid) != start and not group_members(pid), "natural job process/group closure", cap=3)
    connection.settimeout(c.remaining(2))
    eof = stream.read(1)
    c.remaining()
    require(eof == b"", "script socket EOF after natural terminal")
    job["closure"] = dict(pid=pid, start=start, observed_start=identity(pid), pgid=pid,
                          members=group_members(pid), eof=True, at=time.monotonic())
    stream.close()
    connection.close()
    c.job_connections.remove(connection)
    if job["code"] == -9:
        require(5000 <= job["terminal"]["duration_ms"] <= 17000 and "released" not in job,
                "actual product timeout within cap/grace/observation bound", job["terminal"])


def terminal_oracle(c: Controller, task: str, job: dict) -> dict:
    row = raw_job(c, task, job["id"])
    code = job["code"]
    status = "rejected" if code is None else "failed" if code == -9 else "completed"
    require((row["status"], row["exit_code"], row["reason"]) == (status, code, "timeout" if code == -9 else None),
            "literal job outcome, no success laundering", row)
    require(row["reviewed_by"] == "founder" and row["reviewed_at"] is not None
            and row["max_runtime_seconds"] == job["cap"] and row["review_required"] == 1 and row["persistent"] == 0,
            "actual review and original cap", row)
    require(row["task_id"] == task and row["agent_name"] == "case_manager" and row["interpreter"] == "python3"
            and row["script_text"] == job["script"], "actual submit script/owner binding", row)
    outputs = []
    for key, path, expected in zip(("stdout", "stderr"), output_paths(c, job["id"]), (job["out"], job["err"])):
        if code is None:
            require(not path.exists() and row[key + "_path"] is None and row[key + "_head"] is None, "rejected has no output file")
            text = ""
        else:
            require(row[key + "_path"] == str(path) and path.is_file() and not path.is_symlink(), "owned literal output path")
            text = path.read_text()
            require(text == expected and row[key + "_head"] == expected, "complete exact stream bytes", text)
        outputs.append(dict(path=str(path), exists=path.exists(), text=text, bytes=len(text.encode())))
    if code is None:
        require(row["reject_reason"] == "E04_REJECTED_BY_REVIEW" and not Path(job["sentinel"]).exists()
                and all(row[k] is None for k in ("started_at", "finished_at", "duration_ms", "cwd_resolved")), "rejection never executed", row)
    else:
        require(row["started_at"] and row["finished_at"] and row["duration_ms"] >= 0 and row["cwd_resolved"], "real execution times/cwd")
    action = "job_rejected" if code is None else "job_run_failed" if code == -9 else "job_run_completed"
    def audited() -> list[dict] | None:
        audits = [a for a in c.stores["alpha"].jobs(task)["audit"] if json.loads(a["payload"]).get("script_request_id") == job["id"]]
        return audits if any(a["action"] == action for a in audits) else None
    audits = c.poll(audited, "eventual terminal audit (no imposed resume ordering)", cap=3)
    require([a["action"] for a in audits] == (["job_submitted", "job_rejected"] if code is None else
            ["job_submitted", "job_run_started", action]), "exact job audit actions", audits)
    require(audits[0]["agent"] == "case_manager" and all(a["agent"] == "founder" for a in audits[1:]), "actual audit actors", audits)
    payload = json.loads(audits[-1]["payload"])
    if code is None:
        require(payload["reason"] == "E04_REJECTED_BY_REVIEW" and payload["reviewer"] == "founder", "literal rejection audit")
    else:
        require(payload["exit_code"] == code, "literal terminal audit exit")
        if code == -9:
            require(payload["reason"] == "timeout", "literal timeout audit")
        else:
            require(payload["stdout_bytes"] == len(job["out"].encode()) and payload["stderr_bytes"] == len(job["err"].encode())
                    and payload["truncated_stdout"] is False and payload["truncated_stderr"] is False, "untruncated audit output counts")
    result = dict(row=row, outputs=outputs, audits=audits)
    job["oracle"] = result
    return result


def final_task(c: Controller, task: str, starts: list[dict], jobs: list[dict], summary: str) -> dict:
    snap = c.stores["alpha"].jobs(task)
    require(len(snap["tasks"]) == 1 and len(snap["results"]) == 2, "no children or extra result", snap)
    row = snap["tasks"][0]
    ids = sorted(j["id"] for j in jobs)
    require(row["status"] == "completed" and row["completed_at"] is not None and row["assigned_agent"] == "case_manager"
            and row["parent_task_id"] is None and row["orchestration_step_count"] == 2
            and row["current_session_id"] == starts[1]["session"]
            and all(row[k] is None for k in ("block_kind", "active_chain", "active_fanout"))
            and json.loads(row["blocked_on_job_ids"]) == ids, "final task retains historical wait IDs", row)
    require([r["status"] for r in snap["results"]] == ["blocked", "completed"]
            and [r["session_id"] for r in snap["results"]] == [s["session"] for s in starts]
            and all(r["agent"] == "case_manager" and r["confidence_score"] == 90 for r in snap["results"]), "two genuine accepted generations")
    require(snap["results"][0]["decision_json"] is None and snap["results"][1]["output_summary"] == summary
            and json.loads(snap["results"][1]["decision_json"]) == dict(action="done", summary=summary), "literal final done outcome")
    audits = {a: [r for r in snap["audit"] if r["action"] == a] for a in
              ("session_start", "session_end", "completion_report", "task_blocked_on_jobs", "task_resumed_from_jobs", "orchestration_step")}
    require(all(len(audits[a]) == 2 for a in ("session_start", "session_end", "completion_report"))
            and all(len(audits[a]) == 1 for a in ("task_blocked_on_jobs", "task_resumed_from_jobs", "orchestration_step")), "exact lifecycle/audit counts")
    for index, start in enumerate(starts):
        a, b, d = (audits[k][index] for k in ("session_start", "session_end", "completion_report"))
        require(json.loads(a["payload"])["session_id"] == start["session"] and json.loads(d["payload"])["session_id"] == start["session"]
                and a["id"] < b["id"] < d["id"], "genuine callback/exit/consumption order")
    block, wake = audits["task_blocked_on_jobs"][0], audits["task_resumed_from_jobs"][0]
    require(audits["completion_report"][0]["id"] < block["id"] < wake["id"] < audits["session_start"][1]["id"], "S1 park before one natural S2")
    p = json.loads(wake["payload"])
    require(sorted(p["blocking_job_ids"]) == ids and p["trigger"] == "job_terminal"
            and p["triggering_job_id"] == jobs[-1]["id"] and p["job_outcomes"] == {j["id"]: j["oracle"]["row"]["status"] for j in jobs},
            "literal all-terminal resume map", p)
    return snap


def exercise(c: Controller) -> None:
    require(len(c.results) == 42 and len(c.starts) == 18, "unchanged full baseline/Pause before E04")
    c.job_connections = []
    server = socket.socket(socket.AF_UNIX)
    path = c.root / "coord/jobs.sock"
    server.bind(str(path))
    server.listen(8)
    previous = anchor(c, "pause-final-B", 6)
    records = []
    c.durable.append(dict(jobs_cases=records))
    try:
        for index, (variant, label, code, allowance, summary) in enumerate(CASES):
            entered = time.monotonic()
            c.case_deadline = min(c.deadline, entered + allowance)
            c.publish_end()
            pre = anchor(c, variant + ":pre", 6 + 2 * index, previous)
            record = dict(method="serial-public-metrics-window-causal-v1", variant=variant, index=index, entered=entered, deadline=c.case_deadline, jobs=[], anchors=[pre],
                          source_sha=c.revision, observer_begin=len(c.observer), commands_begin=len(c.provider_commands))
            records.append(record)
            task = c.create("alpha", variant)
            record["task"] = task
            first = c.start("alpha", task, "case_manager")
            record["starts"] = [first]
            plan = [(label, code)] if label != "PAIR" else ([("A", 0), ("B", 7)] if code == 0 else [("B", 7), ("A", 0)])
            for job_label, job_code in plan:
                token = hashlib.sha256((c.revision + first["session"] + job_label).encode()).hexdigest()[:24]
                program, sentinel = script(c, token, job_label, 0 if job_code is None else job_code, path)
                cap = 5 if job_code == -9 else 30
                job = dict(token=token, effective_end=c.effective_end(), scenario_deadline=c.deadline, script=program, script_sha256=hashlib.sha256(program.encode()).hexdigest(), sentinel=sentinel,
                           code=job_code, cap=cap, label=job_label, pending=[], out="" if job_code is None else f"E04_{job_label}_OUT\n",
                           err="" if job_code is None else f"E04_{job_label}_ERR\n")
                record["jobs"].append(job)
                submit = c.provider_cli(first, "jobs-submit", payload=dict(title=variant + "/" + token, rationale="Disposable owned deterministic job",
                    script=program, interpreter="python3", cwd_hint=None, review_required=True, persistent=False, max_runtime_seconds=cap))
                job["submit"] = submit
                result = json.loads(submit["stdout"])
                require(result["status"] == "pending" and result["authentication"] == dict(task_id=task, session_id=first["session"]), "actual CLI submit ID/auth", result)
                job["id"] = result["id"]
                pending(c, task, job, server)
            ids = [j["id"] for j in record["jobs"]]
            block = c.provider_cli(first, "jobs-block", payload=dict(status="blocked", confidence=90, summary="waiting for genuine IDs", waiting_on_job_ids=ids))
            record["block"] = block
            c.release(first)
            record["parked"] = c.poll(lambda: c.stores["alpha"].jobs_parked(task, first["session"], ids), "consumed S1 park", cap=10)
            s1 = anchor(c, variant + ":S1", 7 + 2 * index, pre)
            record["anchors"].append(s1)
            for job in record["jobs"]:
                pending(c, task, job, server)
            record["waiting_browser"] = c.browser.jobs_waiting(task, ids)
            for offset, job in enumerate(record["jobs"]):
                if job["code"] is None:
                    job["action"] = c.browser.job_action(job["id"], job["cap"], lambda: pending(c, task, job, server), reject=True)
                else:
                    run_script(c, task, job, server)
                terminal_oracle(c, task, job)
                job["browser"] = c.browser.job_terminal(job["id"], job["oracle"]["row"]["status"], job["code"], job["out"], job["err"])
                if len(record["jobs"]) == 2 and offset == 0:
                    record["first_terminal"] = c.stores["alpha"].jobs_parked(task, first["session"], ids)
                    require(record["first_terminal"] is not None, "pair first terminal does not resume")
                    pending(c, task, record["jobs"][1], server)
                    command = c.cli(["details", task, "--org", "alpha"], relay=True)
                    require(command.returncode == 0 and all(j in command.stdout for j in ids) and "Blocked on jobs:" in command.stdout,
                            "actual CLI waiting pair", command.stdout)
                    record["first_terminal_cli"] = c.commands[-1]
                    record["first_terminal_browser"] = c.browser.jobs_waiting(task, ids)
                    record["anchors"].append(anchor(c, variant + ":first-terminal", 7 + 2 * index, s1))
                    record["anchors"].append(anchor(c, variant + ":pre-second-action", 7 + 2 * index, s1))
            second = c.start("alpha", task, "case_manager")
            record["starts"].append(second)
            live = dict(source_sha=c.revision, provider=dict(second), entered=time.monotonic())
            record["live_s2"] = live  # Retain a partial frame before any assertion.
            live["snapshot"] = c.stores["alpha"].jobs(task)
            live.update(observed_start=identity(second["pid"]), finished=time.monotonic())
            snap = live["snapshot"]
            require(len(snap["tasks"]) == 1 and snap["tasks"][0]["id"] == task
                    and snap["tasks"][0]["status"] == "in_progress"
                    and snap["tasks"][0]["current_session_id"] == second["session"]
                    and snap["results"] == record["parked"]["results"]
                    and len(snap["results"]) == 1 and snap["results"][0]["status"] == "blocked"
                    and snap["results"][0]["session_id"] == first["session"]
                    and live["observed_start"] == second["pid_start"],
                    "live S2 retains only the historical blocked S1 result before outcome reads")
            for job in record["jobs"]:
                reads = {}
                job["reads"] = reads
                for operation in ("receipt", "show", "output", "tail", "wait"):
                    reads[operation] = c.provider_cli(second, "jobs-read", operation=operation, job=job["id"])
                receipt = json.loads(reads["receipt"]["stdout"])
                require(receipt["authentication"] == dict(task_id=task, session_id=second["session"]), "receipt bound to new live SID")
                raw = job["oracle"]["row"]
                require(all(raw[k] == v for k, v in receipt["job"].items()), "closed receipt literal durable fields")
                expected_output = dict(stdout=job["out"], stderr=job["err"], truncated_stdout=False, truncated_stderr=False,
                                       total_stdout_bytes=len(job["out"].encode()), total_stderr_bytes=len(job["err"].encode()))
                require(receipt["output"] == expected_output and json.loads(reads["output"]["stdout"]) == receipt, "full actual structured output")
                require(reads["tail"]["stdout"] == job["out"] and json.loads(reads["wait"]["stdout"]) == dict(status=raw["status"], timed_out=False), "tail/wait outcome truth")
                show = reads["show"]["stdout"]
                require(show.splitlines() and show.splitlines()[0] == f"{job['id']}   {raw['status']}   submitted {raw['created_at']}",
                        "human CLI first-line ID/status/created timestamp")
                if job["code"] is not None:
                    require(f"Exit code:    {job['code']}" in show.splitlines(), "human CLI literal exit")
                if job["code"] == -9:
                    require("Reason:       timeout" in show.splitlines(), "human CLI timeout reason")
                if job["code"] is None:
                    require("Reject reason: E04_REJECTED_BY_REVIEW" in show.splitlines(), "human CLI rejection reason")
            final = c.provider_cli(second, "jobs-final", payload=dict(status="completed", confidence=90, summary=summary,
                                    decision=dict(action="done", summary=summary)))
            record["final_callback"] = final
            c.release(second)
            c.finish("alpha", task)
            c.poll(lambda: len(c.stores["alpha"].query("SELECT id FROM audit_log WHERE task_id=? AND action='orchestration_step'", (task,))) == 1,
                   "final done consumption audit", cap=3)
            record["final_snapshot"] = final_task(c, task, record["starts"], record["jobs"], summary)
            previous = anchor(c, variant + ":final", 8 + 2 * index, s1)
            record["anchors"].append(previous)
            c.browser.open_task("alpha", task, variant)
            c.browser.completed("alpha", task, variant, summary, shot="e04-task-" + str(index))
            record["task_browser"] = dict(text=c.browser.page.get_by_role("main").inner_text(timeout=c.remaining(c.browser.page_timeout)*1000),
                                          screenshot=f"e04-task-{index}.png", at=time.monotonic())
            c.remaining()
            record.update(finished=time.monotonic(), observer_end=len(c.observer), commands_end=len(c.provider_commands))
            require(record["finished"] < record["deadline"], "case stays within original shared allowance")
            c.mark(variant, dict(host_cleanup_attribution=record))
            del c.case_deadline
            c.publish_end()
        final = anchor(c, "final-drain-B", 18, previous)
        require(len(c.starts) == 30 and len(c.results) == 48 and sum(len(r["jobs"]) for r in records) == 8
                and sum("peer" in j for r in records for j in r["jobs"]) == 7, "exact E04 prefix workload")
        for org, tasks, results in (("alpha", 19, 29), ("beta", 1, 1)):
            snap = c.stores[org].snapshot()
            require(len(snap["tasks"]) == tasks and len(snap["results"]) == results and all(r["status"] == "completed" for r in snap["tasks"]), "literal final org closure")
            c.durable.append(dict(jobs_final_org=org, snapshot=snap))
        require(len(c.stores["alpha"].query("SELECT id FROM jobs")) == 8 and not c.stores["beta"].query("SELECT id FROM jobs"), "exact whole-org jobs, no extras")
        for record in records:
            for job in record["jobs"]:
                if job["code"] is None:
                    job["rejection_drain"] = terminal_oracle(c, record["task"], job)
        rows = witnesses(c)
        calls = [r for r in rows if r["kind"] == "callback"]
        require(len(calls) == len(c.commands) + len(c.provider_commands) and sum(r["kind"] == "stub" for r in rows) == 30, "all controller/provider CLI calls and starts accounted")
        for command in c.provider_commands:
            digest = hashlib.sha256(json.dumps(command["argv"][1:]).encode()).hexdigest()
            matches = [r for r in calls if r["pid"] == command["child_pid"] and r["ppid"] == command["provider"]["pid"] and r["argv_sha256"] == digest]
            require(len(matches) == 1 and matches[0]["source_sha"] == c.revision, "source/argv/provider-child witness join", matches)
        require(not c.holds and not select.select([server, c.server], [], [], 0)[0], "final no unexpected peer")
        c.durable.append(dict(jobs_final_witnesses=rows, final_anchor=final))
    finally:
        for connection in c.job_connections:
            connection.close()
        server.close()
        path.unlink(missing_ok=True)
