"""Five finite E05 variants, after the complete 48-variant baseline/Pause/E04."""
from __future__ import annotations

import hashlib
import json
import os
import select
import socket
import struct
import time
from pathlib import Path
from datetime import datetime
from typing import TYPE_CHECKING

from jobs import anchor, witnesses
from oracles import require
from owned import identity
from transport import BrowserRelay

if TYPE_CHECKING:
    from controller import Controller


VARIANTS = ("E05.compose-race", "E05.reply-natural", "E05.self-dispatch-followup",
            "E05.consumed-token", "E05.nonparticipant-task-send")
OPENING = "E05 opening\nliteral beta"
REPLY = "E05 reply\n@case_worker received"


def frame(c: Controller) -> dict:
    raw = c.stores["alpha"].thread_frame()
    tokens = [r["invocation_token"] for r in raw["thread_invocations"]]
    for row in raw["thread_invocations"]:
        row["token_prefix"] = row["invocation_token"][:8]
    c.remember_thread_secrets(tokens)
    # Preserve equality/identity without publishing any invocation credential.
    text = json.dumps(raw, sort_keys=True)
    for token in tokens:
        text = text.replace(token, "sha256:" + hashlib.sha256(token.encode()).hexdigest())
    return json.loads(c.sanitize(text))


def stable_frame(c: Controller) -> dict:
    previous = None
    samples = []
    record = dict(samples=samples)
    c.durable.append(dict(thread_frame_samples=record))
    def read():
        nonlocal previous
        current = frame(c)
        samples.append(dict(at=time.monotonic(), content=current))
        if current == previous:
            record["content"] = current
            return record
        previous = current
        return None
    return c.poll(read, "coherent stable multi-table frame", cap=3)


def start(c: Controller, thread: str, agents: set[str], purpose: str = "reply") -> dict:
    c.validate_bindings()
    c.server.settimeout(c.remaining(10))
    connection, _ = c.server.accept()
    connection.settimeout(c.remaining(10))
    stream = connection.makefile("rwb")
    c.peer_connections.append((connection, stream))
    row = json.loads(stream.readline(4096))
    c.remaining()
    pid, uid, _ = struct.unpack("3i", connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
    require(row["pid"] == pid and uid == os.getuid(), "genuine thread socket peer")
    row.update(pid_start=identity(pid), pgid=os.getpgid(pid))
    require(row["pid_start"] is not None and f"E2E_ROOT={c.root}".encode() in Path(f"/proc/{pid}/environ").read_bytes().split(b"\0"),
            "thread process belongs to disposable run")
    c.durable.append(dict(thread_peer=dict(row)))
    owned = c.root / "owned.json"
    prior = json.loads(owned.read_text()) if owned.exists() else []
    temporary = c.root / "owned.tmp"
    temporary.write_text(json.dumps(sorted(set([*prior, pid]))))
    temporary.replace(owned)
    require(row["thread"] == thread and row["org"] == "alpha" and row["agent"] in agents and row["purpose"] == purpose,
            "finite genuine thread identity", row)
    invocations = frame(c)["thread_invocations"]
    matches = [r for r in invocations if r["invocation_token"] == "sha256:" + row["token_sha256"]]
    require(len(matches) == 1, "delivered token digest joins actual durable invocation", matches)
    inv = matches[0]
    require(inv["thread_id"] == thread and inv["agent_name"] == row["agent"] and inv["purpose"] == purpose
            and inv["status"] == "pending" and inv["started_at"] and inv["session_id"], "real started thread authority", inv)
    row.update(session=inv["session_id"], invocation_id=inv["id"], triggering_seq=inv["triggering_seq"], at=time.monotonic())
    require(row["session"] not in {s["session"] for s in c.starts}, "runtime SID unique across task/thread")
    c.starts.append(row)
    c.holds[row["session"]] = connection, stream
    return row


def command(c: Controller, provider: dict, action: str, status: int = 200, **arguments) -> dict:
    require(identity(provider["pid"]) == provider["pid_start"], "original provider remains live for callback")
    connection, stream = c.holds[provider["session"]]
    entered = time.monotonic()
    end = entered + c.remaining(10)
    require(end - entered > 1, "E05 command reserves partial delivery")
    instruction = dict(action=action, execution_deadline=end - 1, deadline=end, **arguments)
    offset = len(c.relay.records)
    attempt = dict(provider=provider, entered=entered, instruction=instruction, observed_start=identity(provider["pid"]))
    c.thread_attempts.append(attempt)
    connection.settimeout(c.remaining(end - time.monotonic()))
    stream.write((json.dumps(instruction) + "\n").encode())
    connection.settimeout(c.remaining(end-time.monotonic()))
    stream.flush()
    c.remaining(end-time.monotonic())
    connection.settimeout(c.remaining(end - time.monotonic()))
    receipt = json.loads(stream.readline(262144))
    receipt["wire"] = list(c.relay.records[offset:])
    c.thread_commands.append(receipt)
    c.remaining(end-time.monotonic())
    attempt.update(finished=time.monotonic(), command_index=len(c.thread_commands) - 1)
    keys = ("thread", "token_sha256", "agent", "org", "purpose", "pid") if "thread" in provider else ("task", "session", "agent", "org", "pid")
    require(receipt["provider"] == {k: provider[k] for k in keys} and receipt["source_sha"] == c.revision
            and receipt["python"] == c.python and receipt["action"] == action, "E05 actual executable binding", receipt)
    require(receipt["exit"] == (0 if status == 200 else 1) and not receipt["timed_out"] and not receipt["output_truncated"]
            and receipt["execution_deadline"] == end - 1 and receipt["finished"] <= time.monotonic() < end
            and c.relay.error is None, "E05 real CLI exit within original deadline", receipt)
    if action == "thread-task-read":
        wire = [w for w in receipt["wire"] if w["method"] == "GET" and w["path"] == f"/api/v1/orgs/alpha/tasks/{arguments['task']}"]
        require(len(wire) == 1 and wire[0]["status"] == 200 and wire[0]["dropped"] is False,
                "actual followup task details response")
        receipt["selected_response"] = wire[0]["response"]
    else:
        if action == "thread-task-final":
            path = f"/api/v1/orgs/alpha/tasks/{provider['task']}/completion"
            expected = {"ok": True}
        else:
            operation = "send" if action == "thread-task-send" else action.removeprefix("thread-")
            path = f"/api/v1/orgs/alpha/threads/{arguments.get('thread', provider.get('thread'))}/{operation}"
            expected = ({"detail": {"code": "invocation_token_consumed", "status": "consumed"}} if status == 409 else
                        {"detail": {"code": "not_a_participant", "composer": "case_manager"}} if status == 403 else None)
        wire = [w for w in receipt["wire"] if w["method"] == "POST" and w["path"] == path]
        require(len(wire) == 1 and wire[0]["status"] == status and wire[0]["dropped"] is False,
                "exact E05 actual wire response", wire)
        require(expected is None or wire[0]["response"] == expected, "literal E05 refusal/acknowledgement", wire)
        receipt["selected_response"] = wire[0]["response"]
    return receipt


def release(c: Controller, provider: dict) -> dict:
    connection, _ = c.holds[provider["session"]]
    connection.settimeout(c.remaining(5))
    c.release(provider)
    c.poll(lambda: identity(provider["pid"]) != provider["pid_start"], "natural E05 provider exit", cap=5)
    key = "token_sha256" if "thread" in provider else "session"
    commands = [r for r in c.thread_commands if r["provider"].get(key) == provider[key]]
    private = []
    children = []
    for receipt in commands:
        children.append(dict(pid=receipt["child_pid"], start=receipt["child_start"], observed_start=identity(receipt["child_pid"])))
        if "--from-file" in receipt["argv"]:
            path = Path(receipt["argv"][receipt["argv"].index("--from-file")+1])
            require(path.is_relative_to(c.root / "coord"), "provider-owned private file scope")
            private.append(dict(path=str(path), exists=path.exists() or path.is_symlink()))
    record = dict(session=provider["session"], pid=provider["pid"], start=provider["pid_start"], observed_start=identity(provider["pid"]),
                  children=children, private_files=private, at=time.monotonic())
    c.durable.append(dict(thread_process_closed=record))
    require(all(not p["exists"] for p in private) and all(ch["observed_start"] != ch["start"] for ch in children), "real private-file/CLI-child closure")
    return record


def live_boundary(c: Controller, admitted: int, released: int) -> dict:
    record = dict(admitted=admitted, released=released, samples=[])
    c.durable.append(dict(thread_live_boundary=record))
    previous = None
    def read():
        nonlocal previous
        metrics = c.get("/api/v1/metrics")
        record["samples"].append(dict(metrics=metrics, at=time.monotonic()))
        h = metrics["host_sessions"]
        a, r = h["admission"], h["receipts"]
        value = (a["admitted_total"],a["released_total"],r["published_total"],a["active"],a["queue_depth"])
        require(value[0] <= admitted and value[1] <= released and value[2] <= released, "no extra provider at finite live barrier")
        expected = (admitted,released,released,1,0)
        if value == previous == expected:
            record["finished"] = time.monotonic()
            return record
        previous = value
        return None
    return c.poll(read, "one genuine live owner after other natural host release", cap=5)


def decline(c: Controller, provider: dict, reason: str) -> dict:
    receipt = command(c, provider, "thread-decline", payload=dict(reason=reason, in_response_to_seq=provider["triggering_seq"]))
    require(receipt["selected_response"] == dict(thread_id=provider["thread"], status="declined")
            and receipt["stdout"].strip() == f"ok: declined on {provider['thread']}", "shipping successful silent decline")
    release(c, provider)
    return receipt


def reply(c: Controller, provider: dict, body: str, seq: int) -> dict:
    receipt = command(c, provider, "thread-reply", payload=dict(body_markdown=body, in_response_to_seq=provider["triggering_seq"]))
    require(receipt["selected_response"] == dict(thread_id=provider["thread"], seq=seq, kind="message")
            and receipt["stdout"].strip() == f"ok: reply seq={seq} on {provider['thread']}", "literal accepted reply")
    return receipt


def compose(c: Controller, subject: str, recipient: str, body: str) -> dict:
    offset = len(c.relay.records)
    entered = time.monotonic()
    result = c.cli(["threads", "compose", "--org", "alpha", "--subject", subject, "--recipients", recipient, "--body", body], relay=True)
    wire = [w for w in c.relay.records[offset:] if w["method"] == "POST" and w["path"] == "/api/v1/orgs/alpha/threads"]
    require(result.returncode == 0 and len(wire) == 1 and wire[0]["status"] == 200, "real founder thread compose")
    response = wire[0]["response"]
    require(result.stdout.split()[0] == response["thread_id"] and response["pending_replies"] == [recipient], "actual compose identity")
    return dict(response=response, command=c.commands[-1], wire=wire[0],
                wire_records=list(c.relay.records[offset:]), entered=entered, finished=time.monotonic(),
                command_index=len(c.commands)-1,
                utc_offset_seconds=int(datetime.fromisoformat(response["started_at"].replace("Z", "+00:00")).astimezone().utcoffset().total_seconds()))


def settled_thread(c: Controller, thread: str, subject: str, bodies: list[tuple], providers: list[dict], turns: int) -> dict:
    snapshot = frame(c)
    c.durable.append(dict(thread_settlement_frame=snapshot, thread=thread))
    row = next(r for r in snapshot["threads"] if r["id"] == thread)
    require(row["subject"] == subject and row["status"] == "open" and row["turns_used"] == turns and row["turn_cap"] == 500,
            "literal finite thread state", row)
    messages = [r for r in snapshot["thread_messages"] if r["thread_id"] == thread]
    require([(m["seq"], m["speaker"], m["kind"], m["body_markdown"]) for m in messages] == bodies,
            "exact finite message content/order", messages)
    invocations = [r for r in snapshot["thread_invocations"] if r["thread_id"] == thread]
    require(len(invocations) == len(providers) and {r["invocation_token"] for r in invocations} == {"sha256:" + p["token_sha256"] for p in providers},
            "all genuine tokens accounted", invocations)
    for provider in providers:
        inv = next(r for r in invocations if r["session_id"] == provider["session"])
        require(inv["started_at"] and inv["consumed_at"] and inv["status"] in {"consumed", "declined"}
                and inv["agent_name"] == provider["agent"] and inv["purpose"] == provider["purpose"], "terminal invocation identity", inv)
    delivery = [r for r in snapshot["thread_reply_delivery_state"] if r["thread_id"] == thread]
    require(len(delivery) == len({p["agent"] for p in providers}) and all(
        r["required_through_seq"] == r["acknowledged_through_seq"] and all(r[k] is None for k in
        ("queued_invocation_token", "running_invocation_token", "running_from_seq", "running_through_seq")) for r in delivery),
        "all thread delivery obligations settled", delivery)
    require(not snapshot["thread_reply_exchange"] and not snapshot["thread_exchange_deferrals"]
            and not snapshot["thread_reply_breaker_episodes"] and not snapshot["thread_reply_breaker_receipts"], "no exchange/breaker leftovers")
    public = c.get(f"/api/v1/orgs/alpha/threads/{thread}")
    offset = len(c.relay.records)
    entered = time.monotonic()
    result = c.cli(["threads", "show", "--org", "alpha", thread, "--json"], relay=True)
    finished = time.monotonic()
    require(result.returncode == 0 and json.loads(result.stdout) == public, "actual thread CLI/public projection")
    require(public["subject"] == subject and public["turns_used"] == turns
            and len(public["participants"]) == len(set(public["participants"]))
            and set(public["participants"]) == {p["agent"] for p in providers}, "public finite thread roster/state", public)
    messages_page = c.get(f"/api/v1/orgs/alpha/threads/{thread}/messages")
    require(messages_page["messages"] == public["messages"] and messages_page["has_more"] is False
            and not public["reply_delivery"] and not messages_page["reply_delivery"], "public full messages and settled delivery")
    return dict(content=snapshot, public=public, messages_page=messages_page, cli=c.commands[-1],
                wire_records=list(c.relay.records[offset:]), command_index=len(c.commands)-1,
                cli_entered=entered, cli_finished=finished, at=time.monotonic())


def negative(c: Controller, provider: dict, action: str, status: int, **arguments) -> dict:
    record = dict(action=action, provider=provider, entered=time.monotonic())
    c.durable.append(dict(thread_negative=record))
    before = stable_frame(c)
    record["before"] = before
    receipt = command(c, provider, action, status, **arguments)
    record["command"] = receipt
    after = stable_frame(c)
    record["after"] = after
    require(before["content"] == after["content"], "denied real CLI leaves complete durable/file frame unchanged")
    return record


def exercise(c: Controller) -> None:
    require(len(c.results) == 48 and len(c.starts) == 29, "full unchanged 48-case prefix")
    c.threads_active = True
    c.relay.capture_e05 = True
    entered = time.monotonic()
    record = dict(source_sha=c.revision, entered=entered, groups=[], variants=list(VARIANTS), prefix_command_count=len(c.commands))
    record["source_hashes"] = {name: hashlib.sha256((c.source/name).read_bytes()).hexdigest() for name in
        ("e2e/threads.py", "e2e/thread_provider.py", "e2e/provider.py", "e2e/controller.py", "e2e/browser.py",
         "e2e/transport.py", "e2e/oracles.py", "e2e/aggregate.py", "tests/integration/fake_claude.sh", "cli/commands/threads.py",
         "runtime/daemon/routes/threads.py", "runtime/daemon/thread_runner.py", "web/src/shared/threads/NewThreadDialog.tsx", "cli/_shared.py", "cli/commands/tasks.py",
         "runtime/daemon/routes/tasks.py", "web/src/features/threads/ThreadsPage.tsx",
         "web/src/design-system/patterns/MessageBubble.tsx", "web/src/design-system/patterns/Markdown.tsx",
         "web/src/design-system/patterns/AgentChip.tsx")}
    c.durable.append(dict(threads_cases=record))
    previous = next(d["jobs_anchor"] for d in c.durable if d.get("jobs_anchor", {}).get("name") == "final-drain-B")
    require(previous["total"] == 17 and previous["projection"]["receipts"]["window_size"] + 7 <= 64, "B17 capacity before E05")
    c.case_deadline = min(c.deadline, entered + 18)
    c.publish_end()
    if c.browser_relay:
        c.browser_relay.deadline = c.effective_end()
    group = dict(name="C", entered=entered, deadline=c.case_deadline)
    record["groups"].append(group)
    try:
        require(not frame(c)["threads"], "no prior thread workload")
        proxy = BrowserRelay(c.port, c.effective_end())
        c.browser_relay = proxy
        ports = json.loads((c.root / "ports.json").read_text())
        require(proxy.port not in ports and proxy.port != c.relay.port, "D/L/P distinct")
        ports.append(proxy.port)
        (c.root / "ports.json").write_text(json.dumps(ports))
        record["transport"] = dict(daemon_port=c.port, cli_port=c.relay.port, browser_port=proxy.port,
                                   ports=ports, daemon_pid=c.daemon.pid, daemon_start=identity(c.daemon.pid), registered=time.monotonic())
        browser = c.browser
        browser.open_threads_context(proxy.port)
        browser.thread_compose_inputs()
        def proof(response, end):
            while time.monotonic() < end:
                with c.bounded(end):
                    content = frame(c)
                c.durable.append(dict(thread_compose_commit_frame=content, at=time.monotonic()))
                rows = [r for r in content["threads"] if r["subject"] == "E05 compose race"]
                if rows:
                    require(len(rows) == 1 and rows[0]["id"] == response["thread_id"] and rows[0]["turns_used"] == 1
                            and rows[0]["started_at"] == response["started_at"], "original200 independently committed thread")
                    messages = content["thread_messages"]
                    require(len(messages) == 1 and messages[0]["body_markdown"] == OPENING and messages[0]["speaker"] == "founder"
                            and messages[0]["seq"] == 1 and len(content["thread_participants"]) == 2
                            and {r["agent_name"] for r in content["thread_participants"]} == {"case_manager", "case_worker"}, "literal compose commit")
                    return content
                time.sleep(min(0.01, max(0, end-time.monotonic())))
            raise TimeoutError("independent compose commit")
        arm = time.monotonic()
        proxy.arm(proof, arm + c.remaining(5))
        group["armed"] = dict(at=arm, deadline=proxy.arm_end)
        group["event_batch"] = browser.thread_event_batch()
        while not proxy.held.is_set() and time.monotonic() < proxy.arm_end:
            browser.thread_page.wait_for_timeout(10)
            require(not proxy.errors, "original response hold", proxy.errors)
        require(proxy.held.is_set(), "real response entered hold")
        group["held_events"] = browser.thread_snapshot("held")
        require(len([r for r in browser.thread_events if r["event"] == "compose-request"]) == 1
                and not any(r["event"] == "compose-response" for r in browser.thread_events)
                and browser.thread_page.url == browser.thread_base + "/orgs/alpha/threads", "one POST/no detail while HELD")
        C = proxy.hold["response"]["thread_id"]
        group["thread"] = C
        group["released"] = time.monotonic()
        proxy.release.set()
        browser.thread_page.wait_for_url(browser.thread_base + f"/orgs/alpha/threads/{C}", timeout=c.remaining(5)*1000)
        group["released_events"] = browser.thread_snapshot("released")
        require(proxy.matches == 1 and not proxy.errors, "original response delivered once")
        group["created"] = browser.thread_created(C)
        group["initial_view"] = browser.thread_view(C, "E05 compose race", ["E05 opening", "literal beta"], "compose")
        first = start(c, C, {"case_manager", "case_worker"})
        second = start(c, C, {"case_manager", "case_worker"} - {first["agent"]})
        manager, worker = (first, second) if first["agent"] == "case_manager" else (second, first)
        group["providers"] = [manager, worker]
        group["initial_decline"] = decline(c, worker, "E05 initial observed")
        # A process exit precedes host release and post-exit transcript work.
        group["worker_closed"] = live_boundary(c,19,18)
        group["reply"] = reply(c, manager, REPLY, 2)
        worker2 = start(c, C, {"case_worker"})
        group["providers"].append(worker2)
        group["held_worker"] = dict(provider=worker2, before=identity(worker2["pid"]))
        group["replay"] = negative(c, manager, "thread-reply", 409,
                                   payload=dict(body_markdown=REPLY, in_response_to_seq=1))
        group["held_worker"]["after"] = identity(worker2["pid"])
        require(group["held_worker"]["before"] == group["held_worker"]["after"] == worker2["pid_start"], "W2 remains command-held during replay")
        require(group["reply"]["argv"] == group["replay"]["command"]["argv"]
                and group["reply"]["payload_sha256"] == group["replay"]["command"]["payload_sha256"], "same live provider file replay")
        release(c, manager)
        group["last_decline"] = decline(c, worker2, "E05 reply observed")
        previous = anchor(c, "E05:C", 20, previous)
        group["anchor"] = previous
        group["settled"] = settled_thread(c, C, "E05 compose race", [(1,"founder","message",OPENING),(2,"case_manager","message",REPLY)], group["providers"], 2)
        group["view"] = browser.thread_view(C, "E05 compose race", ["E05 opening", "literal beta", "E05 reply", "@case_worker received"], "reply")
        group["finished"] = time.monotonic()

        begin = time.monotonic()
        c.case_deadline = min(c.deadline, begin + 8)
        c.publish_end()
        if c.browser_relay:
            c.browser_relay.deadline = c.effective_end()
        group = dict(name="N", entered=begin, deadline=c.case_deadline)
        record["groups"].append(group)
        group["compose"] = compose(c, "E05 denial venue", "case_worker", "E05 denial anchor")
        N = group["compose"]["response"]["thread_id"]
        group["thread"] = N
        worker = start(c, N, {"case_worker"})
        group["providers"] = [worker]
        group["decline"] = decline(c, worker, "E05 denial venue observed")
        previous = anchor(c, "E05:N", 21, previous)
        group["anchor"] = previous
        group["settled"] = settled_thread(c, N, "E05 denial venue", [(1,"founder","message","E05 denial anchor")], [worker], 1)
        group["finished"] = time.monotonic()

        begin = time.monotonic()
        c.case_deadline = min(c.deadline, begin + 25)
        c.publish_end()
        if c.browser_relay:
            c.browser_relay.deadline = c.effective_end()
        group = dict(name="D", entered=begin, deadline=c.case_deadline)
        record["groups"].append(group)
        group["compose"] = compose(c, "E05 dispatch", "case_manager", "E05 do one task")
        D = group["compose"]["response"]["thread_id"]
        group["thread"] = D
        d1 = start(c, D, {"case_manager"})
        dispatch = command(c, d1, "thread-dispatch", payload=dict(brief="E05 one owned task", target_agent="case_manager", team="engineering"))
        group["dispatch"] = dispatch
        T = dispatch["selected_response"]["task_id"]
        task = c.start("alpha", T, "case_manager")
        group["task"] = task
        group["reply"] = reply(c, d1, "E05 task accepted", 3)
        release(c, d1)
        group["dispatcher_closed"] = live_boundary(c,23,22)
        group["denial"] = negative(c, task, "thread-task-send", 403, thread=N, payload=dict(body_markdown="E05 forbidden post"))
        group["callback"] = command(c, task, "thread-task-final", payload=dict(status="completed", confidence=90, summary="E05 task complete",
                                                                                 decision=dict(action="done", summary="E05 task complete")))
        c.payloads[task["session"]] = group["callback"]["payload"]
        release(c, task)
        c.finish("alpha", T)
        d2 = start(c, D, {"case_manager"}, "task_followup")
        group["providers"] = [d1, d2]
        require(d2["triggering_seq"] == 4, "natural completion followup at seq4")
        group["read"] = command(c, d2, "thread-task-read", task=T)
        require(T in group["read"]["stdout"] and "E05 task complete" in group["read"]["stdout"], "genuine followup reads actual task")
        group["followup"] = reply(c, d2, "E05 task complete", 5)
        release(c, d2)
        previous = anchor(c, "E05:D", 24, previous)
        group["anchor"] = previous
        group["settled"] = settled_thread(c, D, "E05 dispatch", [(1,"founder","message","E05 do one task"),
            (2,"case_manager","system",None),(3,"case_manager","message","E05 task accepted"),
            (4,"case_manager","system",None),(5,"case_manager","message","E05 task complete")], [d1,d2], 3)
        group["tasks_projection"] = c.get(f"/api/v1/orgs/alpha/threads/{D}/tasks")
        group["view"] = browser.thread_view(D, "E05 dispatch", ["E05 do one task", "E05 task accepted", "E05 task complete"], "dispatch", T)
        group["finished"] = time.monotonic()
        record["final_entered"] = time.monotonic()
        c.case_deadline = min(c.deadline, record["final_entered"] + 4)
        c.publish_end()
        if c.browser_relay:
            c.browser_relay.deadline = c.effective_end()
        record["final_deadline"] = c.case_deadline
        record["final"] = stable_frame(c)
        beta = c.stores["beta"].thread_frame()
        c.durable.append(dict(threads_beta=beta))
        require(not any(beta[k] for k in beta if k.startswith("thread") or k in {"jobs", "files"}), "no beta thread/job/file effects")
        for org, counts in (("alpha", (20,29)), ("beta", (1,1))):
            snapshot = c.stores[org].snapshot()
            require((len(snapshot["tasks"]), len(snapshot["results"])) == counts
                    and all(t["status"] == "completed" for t in snapshot["tasks"]), "final composed task/result closure")
            c.durable.append(dict(threads_final_org=org, snapshot=snapshot))
        require(len(c.starts) == 36 and not c.holds and not select.select([c.server],[],[],0)[0], "exact36 providers, no live or unknown peer")
        rows = witnesses(c)
        require(sum(r["kind"] == "stub" for r in rows) == 36 and sum(r["kind"] == "callback" for r in rows)
                == len(c.commands)+len(c.provider_commands)+len(c.thread_commands), "all E05 CLI/provider witnesses accounted")
        record.update(witnesses=rows, final_anchor=anchor(c,"E05:final",24,previous))
        # This context owns long-lived SSE reads. Settle it and its proxy in
        # the existing final4, before E07 can leave their original ends behind.
        browser.close_threads_context()
        closure = proxy.close(c.effective_end())
        c.browser_relay_closed = not (closure['workers_alive'] or closure['listener_alive'] or closure['connections'])
        c.durable.append(dict(thread_proxy_closure=closure))
        require(not closure['errors'] and not closure['workers_alive'] and not closure['listener_alive']
                and not closure['connections'], 'E05 original-budget browser/proxy closure', closure)
        record["finished"] = time.monotonic()
        require(record["finished"] <= c.case_deadline, "E05 final evidence stays within shared allowance")
        for variant in VARIANTS:
            c.mark(variant, dict(threads=record))
    finally:
        # Partial/error paths keep the SAME case/scenario end and retain all
        # failed teardown observations. Controller.close remains the fallback.
        if c.browser_relay and not getattr(c, 'browser_relay_closed', False):
            c.browser_relay.release.set()
            c._attempt('thread-context-close', c.browser.close_threads_context, blocking=False)
            closure = c._attempt('thread-proxy-close', lambda: c.browser_relay.close(c.effective_end()))
            if closure is not None:
                c.browser_relay_closed = not (closure['workers_alive'] or closure['listener_alive'] or closure['connections'])
                c.durable.append(dict(thread_proxy_closure=closure))
                if closure['errors'] or closure['workers_alive'] or closure['listener_alive'] or closure['connections']:
                    c.record_error('thread-proxy-closure', RuntimeError('proxy closure/transport failure'))
        if hasattr(c, "case_deadline"):
            del c.case_deadline
        c.publish_end()
        if c.browser_relay:
            c.browser_relay.release.set()
