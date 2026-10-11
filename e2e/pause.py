"""Finite Pause journeys through shipping public inputs and read-only oracles.

Called only after the unchanged baseline closure inside the daily launcher.
No product imports, fixture writes, alternative entrypoint or proof campaign.
"""
from __future__ import annotations

import json
import select
import time
from typing import TYPE_CHECKING

from oracles import require
from owned import identity
from pause_browser import PauseBrowser

if TYPE_CHECKING:
    from controller import Controller


def state(c: Controller) -> dict:
    """Control/queue/result/session semantics, excluding heartbeat timestamps."""
    stores = {}
    for org, store in c.stores.items():
        snap = store.snapshot()
        snap["tasks"] = [{k: v for k, v in row.items() if k not in {"last_heartbeat", "updated_at"}}
                         for row in snap["tasks"]]
        snap["controls"] = store.query("SELECT * FROM task_pause_controls ORDER BY root_task_id")
        stores[org] = snap
    metrics = c.get("/api/v1/metrics")
    return dict(stores=stores, starts=list(c.starts), queue=dict(
        task_depth=metrics["run_step_queue_depth"], active_sessions=metrics["executor_sessions_active"],
        host_depth=metrics["host_sessions"]["admission"]["queue_depth"],
        host_active=metrics["host_sessions"]["admission"]["active"]))


def control_row(c: Controller, root: str) -> dict:
    rows = c.stores["alpha"].query("SELECT * FROM task_pause_controls WHERE root_task_id=?", (root,))
    require(len(rows) == 1, "one actual root control", rows)
    return rows[0]


def detail(c: Controller, task: str) -> dict:
    return c.get(f"/api/v1/orgs/alpha/tasks/{task}")


def control(c: Controller, action: str, task: str, generation: int, *, changed: bool | None = None,
            error: dict | None = None) -> dict:
    """Observe the CLI's real transport, without assuming one-wire behavior."""
    before = state(c) if error is not None or changed is False else None
    offset = len(c.relay.records)
    result = c.cli([action, task, "--org", "alpha", "--expected-generation", str(generation), "--json"], relay=True)
    records = [r for r in c.relay.records[offset:] if r["method"] == "POST"
               and r["path"] == f"/api/v1/orgs/alpha/tasks/{task}/{action}"]
    require(c.relay.error is None and records, "real CLI control transport", c.relay.error)
    expected_status = 409 if error else 200
    require(result.returncode == (1 if error else 0) and all(r["status"] == expected_status for r in records),
            "CLI/control HTTP outcome", (result.returncode, records))
    if error:
        require(all(r["response"] == {"detail": error} for r in records), "exact control refusal", records)
        response = records[-1]["response"]
    else:
        response = json.loads(result.stdout)
        require(all(r["response"] == response for r in records) and response["changed"] is changed,
                "CLI JSON agrees with real control response", response)
    if before is not None:
        require(state(c) == before, "idempotent/denied control preserves both orgs", state(c))
    return dict(action=action, org="alpha", task=task, expected_generation=generation,
                exit=result.returncode, requests=records, response=response)


def witnesses(c: Controller) -> list[dict]:
    return [json.loads(line) for line in (c.root / "witness/identities.jsonl").read_text().splitlines()]


def host(c: Controller, name: str, total: int, active: int = 0) -> dict:
    """Exact process-local counts; receipts are deliberately not SID-labelled."""
    def settled() -> dict | None:
        metrics = c.get("/api/v1/metrics")
        h = metrics["host_sessions"]
        a, r = h["admission"], h["receipts"]
        require(a["admitted_total"] <= total and r["published_total"] <= total - active,
                "no unexpected admission or receipt", h)
        if (a["active"], a["admitted_total"], a["released_total"], r["published_total"]) != (active, total, total - active, total - active):
            return None
        require(h["wired"] and h["backend"]["healthy"] and h["backend"]["name"] != "passthrough",
                "real healthy containment", h)
        require(a["queue_depth"] == 0 and not h["residue"]["admission_blocked"]
                and h["residue"]["survivors_count"] == 0, "host queue/residue closure", h)
        require(r["window_size"] == len(r["recent"]) == total - active
                and r["quiescent_count"] == total - active and r["with_residue_count"] == 0
                and all(row["quiescent"] and row["survivors_count"] == 0 for row in r["recent"]),
                "exact genuine host receipt closure", h)
        require(metrics["executor_sessions_active"] == active, "actual tracker active count", metrics)
        return metrics
    metrics = c.poll(settled, name)
    c.durable.append(dict(pause_host_anchor=name, metrics=metrics, starts=len(c.starts),
                          daemon_pid=c.daemon.pid, daemon_start=identity(c.daemon.pid)))
    return metrics


def no_launch(c: Controller, root: str, pending: str, *, total: int, starts: int) -> dict:
    """Bounded negative window backed by executable, admission and audit facts."""
    begin = time.monotonic()
    end = begin + c.remaining(1)
    observations = []
    while True:
        require(not select.select([c.server], [], [], 0)[0], "held tree has no unaccepted provider peer")
        require(len(c.starts) == starts and len([r for r in witnesses(c) if r["kind"] == "stub"]) == starts,
                "no extra executable launch witness")
        body = detail(c, pending)
        require(body["task"]["status"] == "pending" and body["task"]["current_session_id"] is None
                and body["task"]["parent_task_id"] == root, "genuine pending reviewer with no SID", body)
        audit = c.stores["alpha"].query("SELECT * FROM audit_log WHERE task_id=? AND action='session_start'", (pending,))
        require(not audit and not c.stores["alpha"].query("SELECT * FROM task_results WHERE task_id=?", (pending,)),
                "held reviewer has no session admission/result", audit)
        entries = json.loads(control_row(c, root)["admission_journal_json"])["entries"]
        require(not any(e["task_id"] == pending for e in entries), "no reviewer admission reservation while held", entries)
        observations.append(host(c, "held-reviewer-no-launch", total))
        if time.monotonic() >= end:
            break
        time.sleep(min(0.1, max(0, end - time.monotonic())))
    record = dict(root=root, pending=pending, seconds=time.monotonic() - begin, observations=observations)
    c.durable.append(dict(pause_no_launch=record))
    return record


def open_chain(c: Controller, prefix: str) -> tuple[str, dict, dict, list[dict]]:
    root = c.create("alpha", prefix + "_ROOT")
    manager = c.start("alpha", root, "case_manager")
    require(detail(c, root)["task"]["parent_task_id"] is None, "true public NULL-parent root")
    decision = dict(action="delegate", agent="case_worker", prompt=prefix + "_WORKER",
                    then=[dict(agent="code_reviewer", prompt=prefix + "_REVIEW", expect_verdict="APPROVE")])
    first = c.payload(manager, prefix + "_DELEGATE", decision)
    c.callback("alpha", first)
    c.stores["alpha"].result(first)
    c.release(manager)
    worker = c.start("alpha", None, "case_worker")
    task = detail(c, worker["task"])["task"]
    parent = detail(c, root)
    require(task["parent_task_id"] == root and task["brief"] == decision["prompt"], "real worker linkage", task)
    require(parent["task"]["status"] == "in_progress" and parent["task"]["block_kind"] == "delegated"
            and parent["active_chain"]["step_index"] == 0
            and parent["active_chain"]["legs"][0]["agent"] == "code_reviewer"
            and parent["active_chain"]["legs"][0]["expect_verdict"] == "APPROVE", "actual public chain", parent)
    # Socket arrival precedes the product's running publication; await its
    # affirmative journal before testing generation capture.
    c.poll(lambda: next((e for e in json.loads(control_row(c, root)["admission_journal_json"])["entries"]
                        if e["session_id"] == worker["session"] and e["phase"] == "running"), None), "real worker running journal")
    return root, manager, worker, [first]


def pause_running(c: Controller, root: str, worker: dict) -> dict:
    before = c.stores["alpha"].snapshot(root)
    record = control(c, "pause", root, 0, changed=True)
    p = record["response"]["pause"]
    require((p["root_task_id"], p["org_slug"], p["generation"], p["held"], p["effective_hold"], p["control_state"])
            == (root, "alpha", 1, True, True, "pausing"), "committed pausing projection", p)
    require(p["lifecycle_status"] == "in_progress" and p["evidence_complete"]
            and any(b["task_id"] == worker["task"] and b["kind"] == "task_session" and b["reason"] == "running"
                    for b in p["blockers"]), "authentic running worker blocker", p)
    after = c.stores["alpha"].snapshot(root)
    for snap in (before, after):
        for row in snap["tasks"]:
            row.pop("last_heartbeat", None)
            row.pop("updated_at", None)
    require(before["tasks"] == after["tasks"] and before["results"] == after["results"], "Pause preserves lifecycle/chain/links/results")
    entries = json.loads(control_row(c, root)["admission_journal_json"])["entries"]
    require(len([e for e in entries if e["session_id"] == worker["session"] and e["captured_generation"] == 1]) == 1,
            "actual worker captured once", entries)
    require(identity(worker["pid"]) == worker["pid_start"], "Pause keeps original worker alive")
    return record


def finish_worker(c: Controller, root: str, worker: dict, prefix: str, payloads: list[dict]) -> dict:
    payload = c.payload(worker, prefix + "_WORKER_OUTCOME")
    callback = c.callback("alpha", payload)
    committed = c.stores["alpha"].result(payload)
    require(type(committed["id"]) is int, "genuine INTEGER callback result")
    c.release(worker)
    c.finish("alpha", worker["task"])
    c.poll(lambda: identity(worker["pid"]) != worker["pid_start"], "original worker process gone")
    payloads.append(payload)
    return dict(callback=callback, result=committed)


def pending_reviewer(c: Controller, root: str, worker: dict) -> str:
    def pending() -> str | None:
        rows = c.stores["alpha"].snapshot(root)["tasks"]
        children = [r for r in rows if r["parent_task_id"] == root and r["assigned_agent"] == "code_reviewer"]
        if not children:
            return None
        require(len(children) == 1 and children[0]["status"] == "pending" and children[0]["current_session_id"] is None,
                "one pending actual reviewer", children)
        parent = detail(c, root)
        require(parent["task"]["status"] == "in_progress" and parent["task"]["block_kind"] == "delegated"
                and parent["active_chain"]["step_index"] == 1, "held chain advances only its settled leg", parent)
        return children[0]["id"]
    reviewer = c.poll(pending, "normal pending reviewer creation")
    require(reviewer != worker["task"], "genuine distinct reviewer child")
    return reviewer


def finish_chain(c: Controller, root: str, manager: dict, worker: dict, reviewer: dict,
                 prefix: str, payloads: list[dict]) -> dict:
    review = dict(c.payload(reviewer, prefix + "_REVIEW_APPROVED"), verdict="APPROVE")
    c.callback("alpha", review)
    row = c.stores["alpha"].result(review)
    require(row["verdict"] == "APPROVE", "genuine reviewer verdict persisted", row)
    c.release(reviewer)
    c.finish("alpha", reviewer["task"])
    continuation = c.start("alpha", root, "case_manager")
    final = c.payload(continuation, prefix + "_DONE", dict(action="done", summary=prefix + "_DONE"))
    c.callback("alpha", final)
    c.release(continuation)
    c.finish("alpha", root)
    payloads.extend([review, final])
    snap = c.stores["alpha"].snapshot(root)
    expected = {root: "case_manager", worker["task"]: "case_worker", reviewer["task"]: "code_reviewer"}
    require(len(snap["tasks"]) == 3 and {r["id"] for r in snap["tasks"]} == set(expected)
            and len(snap["results"]) == 4, "exact chain task/result closure", snap)
    for task in snap["tasks"]:
        require(task["status"] == "completed" and task["completed_at"] is not None and task["assigned_agent"] == expected[task["id"]]
                and task["block_kind"] is None and task["active_chain"] is None and task["active_fanout"] is None,
                "natural final owner/lifecycle closure", task)
        require(task["parent_task_id"] == (None if task["id"] == root else root), "final parent linkage", task)
        require(task["current_session_id"] == next(p["session_id"] for p in reversed(payloads) if p["task_id"] == task["id"]),
                "final actual session binding", task)
        require(task["orchestration_step_count"] == (2 if task["id"] == root else 1), "unchanged chain step count", task)
    for p in payloads:
        c.stores["alpha"].result(p)
    audits = {key: [r for r in snap["audit"] if r["action"] == key]
              for key in ("session_start", "session_end", "completion_report", "orchestration_step", "chain_auto_advance")}
    starts = [manager, worker, reviewer, continuation]
    for key in ("session_start", "session_end", "completion_report"):
        require(len(audits[key]) == 4, f"exact chain {key} count", audits[key])
    require([s["session"] for s in starts] == [p["session_id"] for p in payloads], "real chain launch order")
    for index, p in enumerate(payloads):
        a, b, d = (audits[key][index] for key in ("session_start", "session_end", "completion_report"))
        consumed = json.loads(d["payload"])
        require(json.loads(a["payload"])["session_id"] == p["session_id"]
                and consumed["session_id"] == p["session_id"] and consumed["agent"] == p["agent"]
                and consumed["output_summary"] == p["summary"] and consumed.get("verdict") == p.get("verdict")
                and a["task_id"] == b["task_id"] == d["task_id"] == p["task_id"] and a["id"] < b["id"] < d["id"],
                "authentic start/end/consumption joins", (a, b, d))
    require([json.loads(a["payload"])["decision"]["action"] for a in audits["orchestration_step"]] == ["delegate", "done"],
            "two genuine manager decisions", audits)
    require(len(audits["chain_auto_advance"]) == 1, "one genuine chain advance", audits)
    advance = json.loads(audits["chain_auto_advance"][0]["payload"])
    require(advance["triggering_child_id"] == worker["task"] and advance["spawned_child_id"] == reviewer["task"]
            and advance["leg_index"] == 1 and advance["chain_origin_step_audit_id"] == audits["orchestration_step"][0]["id"],
            "chain advance actual owner/result lineage", advance)
    require(audits["completion_report"][1]["id"] < audits["session_start"][2]["id"]
            and audits["completion_report"][2]["id"] < audits["session_start"][3]["id"], "consumption before eligible next launch")
    public, recall = detail(c, root), c.get(f"/api/v1/orgs/alpha/tasks/{root}/recall?tree=true")
    require(public["task"]["status"] == recall["status"] == "completed" and recall["output_summary"] == prefix + "_DONE"
            and {r["id"] for r in public["results"]} == {r["id"] for r in snap["results"] if r["task_id"] == root},
            "public durable final result identity", public)
    require({r["task_id"] for r in recall["children"]} == {worker["task"], reviewer["task"]}
            and all(r["status"] == "completed" for r in recall["children"]), "public final child closure", recall)
    record = dict(root=root, snapshot=snap, public=public, recall=recall, starts=starts)
    c.durable.append(dict(pause_chain_closure=record))
    return record


def overview(c: Controller, root: str, section: str) -> dict:
    before = state(c)
    result = c.cli(["tasks", "--org", "alpha", "--pause-overview", "--json"], relay=True)
    require(result.returncode == 0, "explicit-org shipping overview CLI", result.stderr)
    response = json.loads(result.stdout)
    require(response["org_slug"] == "alpha" and response["next_cursor"] is None and response["evidence_complete"],
            "complete bounded ordinary overview", response)
    sections = ("unheld_runnable", "pausing", "paused", "terminal_drain", "unavailable_roots")
    require(response["counts"] == {name: (1 if name == section else 0) for name in sections}
            and [p["root_task_id"] for p in response[section]] == [root], "independent exact overview categories/counts", response)
    require(state(c) == before, "overview reads do not create controls/queues/results/sessions/audits")
    return response


def exercise(c: Controller) -> None:
    require(len(c.starts) == 9 and len(c.results) == 23, "original baseline closure precedes Pause")
    begin = time.monotonic()
    host(c, "baseline-before-pause", 9)
    ui = PauseBrowser(c)
    root, manager, worker, payloads = open_chain(c, "PAUSE_RESTART")
    host(c, "restart-case-worker-running", 11, 1)
    paused = pause_running(c, root, worker)
    c.mark("Pause.chain-drain", paused)
    for action in ("pause", "resume"):
        denied = control(c, action, worker["task"], 1, error=dict(code="root_task_required", root_task_id=root))
        c.mark(f"Pause.child-{action}-denied", denied)
    duplicate = control(c, "pause", root, 1, changed=False)
    c.mark("Pause.pause-idempotent", duplicate)
    for locale in ("en", "zh-CN"):
        before = state(c)
        evidence = ui.held(root, worker["task"], "pausing", locale)
        require(state(c) == before, "live browser readback/locale is read-only")
        c.mark("Pause.browser-pausing-" + ("en" if locale == "en" else "zh"), evidence)
    c.mark("Pause.overview-readonly", overview(c, root, "pausing"))
    # Same-org available-capacity positive while the authentic worker is held.
    independent = c.create("alpha", "PAUSE_UNHELD_ROOT")
    other = c.start("alpha", independent, "case_manager")
    require(independent != root and detail(c, independent)["task"]["parent_task_id"] is None, "separate same-org true root")
    reads = state(c)
    p = detail(c, independent)["pause"]
    c.get("/api/v1/orgs/alpha/tasks/roots")
    require(not p["held"] and not p["effective_hold"] and p["generation"] == 0 and state(c) == reads,
            "unheld root reads do not manufacture Pause controls", p)
    independent_payload = c.payload(other, "PAUSE_UNHELD_DONE", dict(action="done", summary="PAUSE_UNHELD_DONE"))
    callback = c.callback("alpha", independent_payload)
    c.release(other)
    c.finish("alpha", independent)
    c.stores["alpha"].finished(independent, [independent_payload], [other])
    require(control_row(c, root)["held"] == 1 and identity(worker["pid"]) == worker["pid_start"], "unheld progress preserves held tree")
    c.mark("Pause.same-org-unheld", dict(root=independent, callback=callback, public=detail(c, independent)))
    worker_finish = finish_worker(c, root, worker, "PAUSE_RESTART", payloads)
    pending = pending_reviewer(c, root, worker)
    metrics = host(c, "pre-restart-12", 12)
    p = c.poll(lambda: (p if (p := detail(c, root)["pause"])["control_state"] == "paused" else None), "genuine drained Pause")
    require(p["held"] and p["generation"] == 1 and p["evidence_complete"] and p["blockers"] == [], "positive quiescent paused projection", p)
    bounded = no_launch(c, root, pending, total=12, starts=12)
    c.mark("Pause.drained-before-restart", dict(worker=worker_finish, metrics=metrics, no_launch=bounded))
    # Whole paused state including original INTEGER results and consumption
    # audits must survive same-files restart, without reinitialization.
    from jobs import anchor
    anchor(c, "archive-A", 12)
    before = state(c)
    restart = c.restart_owned_daemon()
    anchor(c, "initial-B", 0)
    host(c, "post-restart-zero", 0)
    require(state(c) == before, "same-files restart retains control/chain/results/consumption state", state(c))
    p = detail(c, root)["pause"]
    require(p["control_state"] == "paused" and p["held"] and p["generation"] == 1
            and p["evidence_complete"] and p["blockers"] == [], "held public projection survives restart", p)
    bounded = no_launch(c, root, pending, total=0, starts=12)
    c.mark("Pause.drained-restart", dict(restart=restart, no_launch=bounded, projection=p))
    for locale in ("en", "zh-CN"):
        before = state(c)
        evidence = ui.held(root, pending, "paused", locale)
        require(state(c) == before, "post-restart browser/reload reads preserve state")
        c.mark("Pause.browser-paused-" + ("en" if locale == "en" else "zh"), evidence)
    overview(c, root, "paused")
    resume = control(c, "resume", root, 1, changed=True)
    require(resume["response"]["pause"]["generation"] == 2 and not resume["response"]["pause"]["held"], "explicit root Resume")
    reviewer = c.start("alpha", pending, "code_reviewer")
    c.poll(lambda: any(e["session_id"] == reviewer["session"] and e["phase"] == "running"
                      for e in json.loads(control_row(c, root)["admission_journal_json"])["entries"]), "actual reviewer running journal")
    host(c, "post-restart-reviewer-running", 1, 1)
    retry = control(c, "resume", root, 2, changed=False)
    c.mark("Pause.resume-idempotent", retry)
    newer = control(c, "pause", root, 2, changed=True)
    require(newer["response"]["pause"]["generation"] == 3 and newer["response"]["pause"]["held"], "newer actual hold")
    stale = control(c, "resume", root, 1, error=dict(code="control_generation_conflict", root_task_id=root, generation=3))
    require(identity(reviewer["pid"]) == reviewer["pid_start"], "stale control preserves reviewer identity")
    c.mark("Pause.stale-resume", stale)
    control(c, "resume", root, 3, changed=True)
    closure = finish_chain(c, root, manager, worker, reviewer, "PAUSE_RESTART", payloads)
    require(c.stores["alpha"].result(payloads[1]) == worker_finish["result"], "original worker result byte equality after restart/Resume")
    host(c, "post-restart-first-chain-2", 2)
    c.mark("Pause.restart-final", closure)
    ui.terminal(root)
    for action in ("pause", "resume"):
        control(c, action, root, 4, error=dict(code="task_terminal", root_task_id=root))
    c.mark("Pause.terminal-denied", dict(root=root, generation=4, status=detail(c, root)["task"]["status"]))
    # Separate Resume-during-drain journey, with no daemon restart.
    root2, manager2, worker2, payloads2 = open_chain(c, "PAUSE_DRAIN")
    host(c, "drain-case-worker-running", 4, 1)
    pause_running(c, root2, worker2)
    resume2 = control(c, "resume", root2, 1, changed=True)
    require(resume2["response"]["pause"]["generation"] == 2 and not resume2["response"]["pause"]["held"]
            and identity(worker2["pid"]) == worker2["pid_start"], "Resume during drain preserves original worker", worker2)
    require(detail(c, worker2["task"])["task"]["current_session_id"] == worker2["session"]
            and not c.stores["alpha"].query("SELECT * FROM task_results WHERE task_id=?", (worker2["task"],)), "same live worker SID/no premature callback")
    host(c, "resumed-draining-worker-4", 4, 1)
    c.mark("Pause.resume-during-drain", dict(pause_root=root2, worker=worker2, resume=resume2))
    finish_worker(c, root2, worker2, "PAUSE_DRAIN", payloads2)
    reviewer2 = c.start("alpha", None, "code_reviewer")
    require(detail(c, reviewer2["task"])["task"]["parent_task_id"] == root2, "natural reviewer on second tree")
    closure2 = finish_chain(c, root2, manager2, worker2, reviewer2, "PAUSE_DRAIN", payloads2)
    c.mark("Pause.drain-final", closure2)
    final_metrics = host(c, "final-post-restart-6", 6)
    require(len(c.starts) == 18, "exact combined baseline/Pause launches", c.starts)
    for org, tasks, results in (("alpha", 13, 17), ("beta", 1, 1)):
        snap = c.stores[org].snapshot()
        require(len(snap["tasks"]) == tasks and len(snap["results"]) == results
                and all(r["status"] == "completed" for r in snap["tasks"]), "exact combined org closure", snap)
        c.durable.append(dict(pause_final_org=org, snapshot=snap))
    rows = witnesses(c)
    stubs, calls = ([r for r in rows if r["kind"] == key] for key in ("stub", "callback"))
    from controller import sha
    binding = json.loads((c.root / "coord/manifest.json").read_text())
    require(len(stubs) == 18 and all(r["provider"] == "claude" and r["source_sha"] == c.revision
            and r["stub_sha256"] == binding["stubs"]["claude"]["sha256"]
            and r["plan_sha256"] == (c.root / "coord/plan.sh.sha256").read_text() and "-p" in r["flags"] for r in stubs),
            "exact combined source-bound executable witnesses", stubs)
    require(len(calls) == len(c.commands) and all(r["source_sha"] == c.revision and r["cli_sha256"] == sha(c.source / "cli/main.py") for r in calls),
            "exact combined shipping CLI witnesses", calls)
    require(not c.holds and all(identity(s["pid"]) != s["pid_start"] for s in c.starts), "all owned provider identities gone")
    c.mark("Pause.exact-closure", dict(starts=18, pre_restart=12, post_restart=6, metrics=final_metrics))
    c.phases.append(dict(name="pause-actions", seconds=time.monotonic() - begin, pre_restart=12, post_restart=6))
