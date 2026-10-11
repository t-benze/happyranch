"""Daily health: missing cells/variants, skips and unknown cleanup always fail."""
from __future__ import annotations

import argparse
import copy
import json
import os
import signal

from cancellation import validate as validate_cancellation
from run import BASELINE_ARTIFACTS, daily_admission, validate_timing
from pathlib import Path


MANIFEST = json.loads(Path(__file__).with_name("manifest.json").read_text())


def validate(rows: list[dict], expected: list[str], source: str, matrix_result: str) -> None:
    if matrix_result != "success":
        raise ValueError(f"matrix result is {matrix_result}")
    if len(rows) != len(expected) or sorted(r["python"] for r in rows) != sorted(expected):
        raise ValueError("missing, duplicate or unexpected matrix cell")
    for row in rows:
        validate_timing(row)
        if row["source_sha"] != source or row["status"] != "PASS" or row["skipped"] != 0:
            raise ValueError("source/result/skip mismatch")
        if set(row["variants"]) != set(MANIFEST["variants"]):
            raise ValueError("missing or unexpected variant")
        if any(v["status"] != "PASS" for v in row["variants"].values()):
            raise ValueError("failed or skipped variant")
        if row["cleanup"]["ok"] is not True or row["cleanup"]["data_absent"] is not True:
            raise ValueError("unsuccessful or unknown cleanup")
        if not row["cleanup"]["ports_absent"] or not all(value is True for value in row["cleanup"]["ports_absent"].values()):
            raise ValueError("missing port absence")
        if row["total_seconds"] > MANIFEST["hard_seconds"]:
            raise ValueError("hard deadline exceeded")
        required = {"dependencies", "browser-dependencies", "browser-install", "web-dependencies", "web-build", "scenarios"}
        if {p["name"] for p in row["phases"]} != required or any(p["exit"] != 0 for p in row["phases"]):
            raise ValueError("missing or failed execution phase")


def controls() -> dict:
    """Bounded validator controls only; never product-behavior evidence."""
    source = "1" * 40
    phases = [dict(name=n, exit=0, entered=0, finished=1,
                   cap_seconds=480 if n == "scenarios" else 240,
                   allowance_seconds=480 if n == "scenarios" else 240,
                   deadline=480 if n == "scenarios" else 240)
              for n in ("dependencies", "browser-dependencies", "browser-install", "web-dependencies", "web-build", "scenarios")]
    timing = dict(started=0, action_deadline=720, hard_deadline=900,
                  action=dict(finished=1, deadline=720, allowance_seconds=720, elapsed_seconds=1),
                  cleanup=dict(entered=1, finished=2, deadline=61, allowance_seconds=60, elapsed_seconds=1, ok=True),
                  export=dict(entered=2, finished=3, deadline=122, allowance_seconds=120, elapsed_seconds=1, ok=True))
    valid = [dict(python=p, source_sha=source, status="PASS", skipped=0, total_seconds=3, evidence_seconds=1,
                  exercise="baseline", timing=copy.deepcopy(timing),
                  variants={v: {"status": "PASS"} for v in MANIFEST["variants"]},
                  cleanup=dict(ok=True, data_absent=True, ports_absent={"1234": True}),
                  phases=copy.deepcopy(phases))
             for p in MANIFEST["daily_python"]]
    validate(valid, MANIFEST["daily_python"], source, "success")
    results = {}
    for name in ("missing-cell", "skip", "failed-variant", "missing-variant", "wrong-sha", "cleanup", "failed-job", "failed-export", "wrong-scenario-deadline"):
        rows = copy.deepcopy(valid)
        state = "success"
        if name == "missing-cell":
            rows.pop()
        elif name == "skip":
            rows[0]["skipped"] = 1
        elif name == "failed-variant":
            rows[0]["variants"][MANIFEST["variants"][0]]["status"] = "FAIL"
        elif name == "missing-variant":
            rows[0]["variants"].pop(MANIFEST["variants"][0])
        elif name == "wrong-sha":
            rows[0]["source_sha"] = "2" * 40
        elif name == "cleanup":
            rows[0]["cleanup"]["ok"] = False
        elif name == "failed-export":
            rows[0]["timing"]["export"]["ok"] = False
        elif name == "wrong-scenario-deadline":
            rows[0]["phases"][-1]["deadline"] = 720
        else:
            state = "failure"
        try:
            validate(rows, MANIFEST["daily_python"], source, state)
        except ValueError as exc:
            results[name] = str(exc)
        else:
            raise AssertionError(f"gate incorrectly accepted {name}")
    validate(valid, MANIFEST["daily_python"], source, "success")
    results.update(cancellation_controls())
    return results


def cancellation_controls() -> dict:
    """Synthetic refusal controls; these never count as actual signal evidence."""
    source = "1" * 40
    phases = [dict(name=n, exit=0, entered=0, finished=1, deadline=240, cap_seconds=240, allowance_seconds=240)
              for n in ("dependencies", "browser-dependencies", "browser-install", "web-dependencies", "web-build")]
    phases.append(dict(name="scenarios", exit=None, interrupted=True,
                       entered=1, finished=3, deadline=241, cap_seconds=240, allowance_seconds=240))
    timing = dict(started=0, action_deadline=720, hard_deadline=900,
                  action=dict(finished=3, deadline=720, allowance_seconds=720, elapsed_seconds=3),
                  cleanup=dict(entered=3, finished=4, deadline=63, allowance_seconds=60, elapsed_seconds=1, ok=True),
                  export=dict(entered=4, finished=5, deadline=124, allowance_seconds=120, elapsed_seconds=1, ok=True))
    fixture = dict(source_sha=source, launcher_exit=1, launcher_absent=True,
                   identities_live_at_signal=True, total_seconds=10,
                   sent=dict(signal=15, at=2, pid=10, start="100"),
                   active=dict(source_sha=source, at=1, launcher=dict(pid=10, start="100"),
                               provider=dict(pid=11, pid_start="101"), browser_started=True,
                               callback=dict(status=200, response={"ok": True}, dropped=False), ports=[1234, 1235],
                               owned=dict(identities={"11": "101"}, units=["happyranch-example.service"], groups=["/example"])),
                   launcher_result=dict(source_sha=source, python="3.12", exercise="cancellation", status="FAIL", skipped=0,
                                        signals=[dict(signal=15, at=3, pid=10, start="100")], total_seconds=5, evidence_seconds=1, phases=phases, timing=timing,
                                        cleanup=dict(ok=True, data_absent=True, sockets_absent=True, watcher_stopped=True,
                                                     survivors={}, errors=[], identities={"11": "101"},
                                                     units={"happyranch-example.service": {"stdout": "LoadState=not-found"}},
                                                     groups={"/example": False}, ports_absent={"1234": True, "1235": True})))
    validate_cancellation(fixture, source, "3.12", 15)
    results = {}
    for name in ("no-delivery", "wrong-signal", "false-success", "not-active", "missing-identity", "missing-unit", "live-cgroup", "missing-port", "data-residue", "setup-failure"):
        row = copy.deepcopy(fixture)
        result = row["launcher_result"]
        if name == "no-delivery": result["signals"] = []
        elif name == "wrong-signal": result["signals"][0]["signal"] = 2
        elif name == "false-success": row["launcher_exit"] = 0
        elif name == "not-active": row["identities_live_at_signal"] = False
        elif name == "missing-identity": result["cleanup"]["identities"] = {}
        elif name == "missing-unit": result["cleanup"]["units"] = {}
        elif name == "live-cgroup": result["cleanup"]["groups"]["/example"] = True
        elif name == "missing-port": result["cleanup"]["ports_absent"] = {}
        elif name == "data-residue": result["cleanup"]["data_absent"] = False
        elif name == "setup-failure": result["phases"][0]["exit"] = 1
        try:
            validate_cancellation(row, source, "3.12", 15)
        except ValueError as exc:
            results["cancellation-" + name] = str(exc)
        else:
            raise AssertionError(f"cancellation validator accepted {name}")
    return results


def validate_e04(row: dict, observations: dict, browser: list[dict], directory: Path) -> None:
    """Recompute actual artifact content independently of the scenario driver.

    This is a daily artifact oracle, not an execution entrypoint or merge test.
    The metadata-only controls above do not establish this oracle's sensitivity.
    """
    import hashlib

    def check(condition: bool, message: str) -> None:
        if not condition:
            raise ValueError("E04 content: " + message)

    check(observations["variants"] == row["variants"] and observations["observer_error"] is None,
          "scenario/observation disagreement or observer failure")
    starts, commands = observations["starts"], observations["provider_commands"]
    durable, observer = observations["durable"], observations["provider_observer"]
    check(len(starts) == 30 and len({s["session"] for s in starts}) == 30, "30 genuine unique providers")
    check(len(observer) >= 2 and all(0 < b["at"] - a["at"] <= 1 for a, b in zip(observer, observer[1:])),
          "continuous provider observer interval")
    check(all(a["stubs"] <= b["stubs"] and a["calls"] <= b["calls"] for a, b in zip(observer, observer[1:])),
          "observer counter rollback")
    check(observer[-1]["stubs"] == 30 and not observer[-1]["pending"], "observer final closure")
    http = observations["http"]
    anchors = [r["jobs_anchor"] for r in durable if "jobs_anchor" in r]
    check(len(anchors) == 28 and len({a["name"] for a in anchors}) == 28, "complete unique boot/case/drain anchors")
    by_name = {a["name"]: a for a in anchors}

    def anchor_content(a: dict, total: int, count: int, previous: dict | None = None) -> None:
        check(a["total"] == total and a["source_sha"] == row["source_sha"] and a["provider_count"] == count,
              "anchor source/count")
        check(a["daemon_start"] is not None and a["entered"] <= a["finished"] and len(a["samples"]) >= 2,
              "timed fresh anchor")
        check(0 <= a["observer_begin"] < a["observer_end"] <= len(observer), "actual observer references")
        check(observer[0]["at"] <= a["entered"] <= a["finished"] <= observer[-1]["at"], "observer covers barrier")
        check(len(a["provider_closure"]) == count and a["provider_closure"] == [
            dict(pid=s["pid"], start=s["pid_start"], observed_start=x["observed_start"])
            for s, x in zip(starts[:count], a["provider_closure"])], "actual finished provider references")
        check(all(x["start"] is not None and x["start"] != x["observed_start"] for x in a["provider_closure"]), "provider identity closure")
        projections = []
        for sample in a["samples"]:
            check(a["entered"] <= sample["entered"] <= sample["finished"] <= a["finished"], "sample timing")
            m = sample["metrics"]
            check(any(h["method"] == "GET" and h["path"] == "/api/v1/metrics" and h["status"] == 200 and h["response"] == m for h in http),
                  "actual referenced metrics response")
            host = m["host_sessions"]
            r, admission = host["receipts"], host["admission"]
            check(host["wired"] is True and host["backend"]["healthy"] is True and host["backend"]["name"] != "passthrough"
                  and host["residue"]["admission_blocked"] is False and host["residue"]["survivors_count"] == 0, "host availability/residue")
            check(all(type(admission[k]) is int and 0 <= admission[k] <= total for k in ("admitted_total", "released_total"))
                  and admission["shutdown"] is False and admission["cancelled_queued_total"] == 0, "literal admissions")
            check(type(r["published_total"]) is int and 0 <= r["published_total"] <= total <= 64
                  and r["published_total"] == r["window_size"] == len(r["recent"]) == r["quiescent_count"]
                  and r["with_residue_count"] == 0 and all(x["quiescent"] is True and type(x["survivors_count"]) is int
                                                         and x["survivors_count"] == 0 for x in r["recent"]), "literal complete anonymous window")
            if previous:
                old = previous["projection"]["receipts"]
                d = r["published_total"] - old["published_total"]
                check(d >= 0 and r["recent"][d:] == old["recent"], "transient ordered tail/reset")
            projections.append(dict(admission=admission, receipts=r,
                                    executor_sessions_active=m["executor_sessions_active"], run_step_queue_depth=m["run_step_queue_depth"]))
        check(projections[-1] == projections[-2] == a["projection"], "two fresh settled projections")
        p = projections[-1]
        check(p["executor_sessions_active"] == p["run_step_queue_depth"] == p["admission"]["active"] == p["admission"]["queue_depth"] == 0
              and p["admission"]["admitted_total"] == p["admission"]["released_total"] == p["receipts"]["published_total"] == total,
              "settled publication/release/tracker conjunction")
        if previous:
            check(a["daemon_pid"] == previous["daemon_pid"] and a["daemon_start"] == previous["daemon_start"]
                  and previous["finished"] <= a["entered"], "same-boot serial ordering")
            d = total - previous["total"]
            check(d >= 0 and p["receipts"]["recent"][d:] == previous["projection"]["receipts"]["recent"], "ordered retained tail with duplicates")

    initial, baseline, archive, boot_b, pause = (by_name[n] for n in ("initial-A", "baseline-A", "archive-A", "initial-B", "pause-final-B"))
    anchor_content(initial, 0, 0)
    anchor_content(baseline, 9, 9, initial)
    anchor_content(archive, 12, 12, baseline)
    anchor_content(boot_b, 0, 12)
    anchor_content(pause, 6, 18, boot_b)
    restarts = [d["pause_daemon_restart"] for d in durable if "pause_daemon_restart" in d]
    check(len(restarts) == 1, "only named Pause restart")
    restart = restarts[0]
    check(restart["exit"] == 0 and (restart["old"]["pid"], restart["old"]["start"]) == (archive["daemon_pid"], archive["daemon_start"])
          and (restart["new"]["pid"], restart["new"]["start"]) == (boot_b["daemon_pid"], boot_b["daemon_start"])
          and restart["old"] != restart["new"] and archive["finished"] < boot_b["entered"], "actual old/new boot binding")
    for key, expected in (("final_snapshot", {"alpha": (6, 8), "beta": (1, 1)}),
                          ("pause_final_org", {"alpha": (13, 17), "beta": (1, 1)}),
                          ("jobs_final_org", {"alpha": (19, 29), "beta": (1, 1)})):
        records = [d for d in durable if key in d]
        check(len(records) == 2, "retained full original and final org snapshots")
        for d in records:
            org = d["org"] if key == "final_snapshot" else d[key]
            snap = d[key] if key == "final_snapshot" else d["snapshot"]
            check(org in expected and (len(snap["tasks"]), len(snap["results"])) == expected[org]
                  and all(t["status"] == "completed" for t in snap["tasks"]), "literal org task/result closure")
    case_rows = [d["jobs_cases"] for d in durable if "jobs_cases" in d]
    check(len(case_rows) == 1 and len(case_rows[0]) == 6, "six actual case records")
    expected_variants = ["E04.approve-exit0", "E04.reject", "E04.completed-exit7", "E04.failed-timeout",
                         "E04.two-jobs-A-first", "E04.two-jobs-B-first"]
    expected_codes = [[0], [None], [7], [-9], [0, 7], [7, 0]]
    all_jobs, peer_count, previous = [], 0, pause
    for index, record in enumerate(case_rows[0]):
        variant = expected_variants[index]
        check(record["variant"] == variant and record["index"] == index and record["source_sha"] == row["source_sha"]
              and record["method"] == "serial-public-metrics-window-causal-v1"
              and row["variants"][variant]["evidence"]["host_cleanup_attribution"] == record, "actual variant evidence reference")
        check(record["entered"] <= record["finished"] < record["deadline"]
              and record["deadline"] <= record["entered"] + [20, 15, 20, 25, 25, 25][index], "case bound")
        check(record["starts"] == starts[18 + index * 2:20 + index * 2], "two actual sequential provider references")
        first, second = record["starts"]
        task = record["task"]
        check(first["task"] == second["task"] == task and first["org"] == second["org"] == "alpha"
              and first["agent"] == second["agent"] == "case_manager", "genuine task/S1/S2 ownership")
        checkpoints = record["anchors"]
        names = [variant + ":pre", variant + ":S1"]
        if index >= 4:
            names += [variant + ":first-terminal", variant + ":pre-second-action"]
        names += [variant + ":final"]
        check([a["name"] for a in checkpoints] == names and all(by_name[a["name"]] == a for a in checkpoints), "complete causal checkpoint references")
        for i, a in enumerate(checkpoints):
            count = 18 + index * 2 + (0 if i == 0 else 2 if i == len(checkpoints) - 1 else 1)
            anchor_content(a, count - 12, count, previous)
            previous = a
        jobs = record["jobs"]
        ids = sorted(j["id"] for j in jobs)
        check([j["code"] for j in jobs] == expected_codes[index], "literal case set/order")
        check(record["block"]["payload"] == dict(task_id=task, session_id=first["session"], agent="case_manager", status="blocked",
              confidence=90, summary="waiting for genuine IDs", waiting_on_job_ids=[j["id"] for j in jobs]), "real blocked callback payload")
        check(record["parked"]["tasks"][0]["block_kind"] == "blocked_on_job"
              and record["parked"]["results"][0]["status"] == "blocked", "S1 parked durable evidence")
        for job in jobs:
            all_jobs.append(job["id"])
            label = (["OK"], ["REJECT"], ["EXIT7"], ["TIMEOUT"], ["A", "B"], ["B", "A"])[index][jobs.index(job)]
            check(job["label"] == label and job["out"] == ("" if index == 1 else f"E04_{label}_OUT\n")
                  and job["err"] == ("" if index == 1 else f"E04_{label}_ERR\n"), "independent literal stream expectations")
            check(job["submit"] in commands and all(r in commands for r in job["reads"].values()), "command content references")
            raw = job["oracle"]["row"]
            code = job["code"]
            status = "rejected" if code is None else "failed" if code == -9 else "completed"
            check((raw["id"], raw["task_id"], raw["agent_name"], raw["status"], raw["exit_code"], raw["reason"])
                  == (job["id"], task, "case_manager", status, code, "timeout" if code == -9 else None), "literal job outcome")
            check(raw["reviewed_by"] == "founder" and raw["reviewed_at"] is not None and raw["max_runtime_seconds"] == (5 if code == -9 else 30)
                  and raw["review_required"] == 1 and raw["persistent"] == 0 and raw["script_text"] == job["script"]
                  and hashlib.sha256(job["script"].encode()).hexdigest() == job["script_sha256"], "submitted script/cap/review join")
            submitted = json.loads(job["submit"]["stdout"])
            check(submitted["id"] == job["id"] and submitted["status"] == "pending"
                  and submitted["authentication"] == dict(task_id=task, session_id=first["session"])
                  and job["submit"]["payload"]["script"] == raw["script_text"], "actual returned ID and provider submit")
            submit_wire = [w for w in job["submit"]["wire"] if w["method"] == "POST" and w["path"] == "/api/v1/orgs/alpha/jobs/submit"]
            check(len(submit_wire) == 1 and submit_wire[0]["status"] == 201 and submit_wire[0]["response"] == submitted, "real submit201")
            check(len(job["pending"]) == (4 if index >= 4 and job is jobs[1] else 3) and job["pending"][0]["at"] < checkpoints[1]["entered"]
                  and job["pending"][1]["at"] >= checkpoints[1]["finished"], "pending observations at submit/park/dialog")
            for pending in job["pending"]:
                check(pending["row"]["status"] == "pending" and pending["sentinel_absent"] is True and pending["peer_pending"] is False
                      and len(pending["files_absent"]) == 2 and all(pending["files_absent"].values())
                      and [a["action"] for a in pending["audit"]] == ["job_submitted"]
                      and all(pending["row"][k] is None for k in ("started_at", "finished_at", "reviewed_at", "exit_code", "stdout_path", "stderr_path")),
                      "pending no launch/file/audit evidence")
            action = job["action"]
            check(action["at"] > checkpoints[1]["finished"] and action["request"] == ({"reason": "E04_REJECTED_BY_REVIEW"} if code is None else {})
                  and action["status"] == (200 if code is None else 202)
                  and dict(event="job-action", **action) in browser, "actual browser action after settled S1")
            check(job["browser"]["body"]["status"] == status and job["browser"]["body"]["exit_code"] == code, "browser literal outcome")
            image = job["browser"]["screenshot"]
            check(Path(image).name == image and (directory / image).is_file() and (directory / image).stat().st_size > 0, "actual outcome screenshot")
            output = dict(stdout=job["out"], stderr=job["err"], truncated_stdout=False, truncated_stderr=False,
                          total_stdout_bytes=len(job["out"].encode()), total_stderr_bytes=len(job["err"].encode()))
            check([o["text"] for o in job["oracle"]["outputs"]] == [job["out"], job["err"]]
                  and [o["bytes"] for o in job["oracle"]["outputs"]] == [len(job["out"].encode()), len(job["err"].encode())], "literal output file content")
            reads = job["reads"]
            receipt = json.loads(reads["receipt"]["stdout"])
            check(receipt["authentication"] == dict(task_id=task, session_id=second["session"])
                  and all(raw[k] == value for k, value in receipt["job"].items()) and receipt["output"] == output
                  and json.loads(reads["output"]["stdout"]) == receipt
                  and reads["tail"]["stdout"] == job["out"]
                  and json.loads(reads["wait"]["stdout"]) == dict(status=status, timed_out=False), "real live-S2 receipt/output/tail/wait")
            audits = job["oracle"]["audits"]
            terminal = "job_rejected" if code is None else "job_run_failed" if code == -9 else "job_run_completed"
            check([a["action"] for a in audits] == (["job_submitted", terminal] if code is None else ["job_submitted", "job_run_started", terminal])
                  and audits[0]["agent"] == "case_manager" and all(a["agent"] == "founder" for a in audits[1:])
                  and all(json.loads(a["payload"])["script_request_id"] == job["id"] for a in audits), "literal job audit links/actors")
            payload = json.loads(audits[-1]["payload"])
            if code is None:
                check(raw["reject_reason"] == payload["reason"] == "E04_REJECTED_BY_REVIEW" and "peer" not in job
                      and job["rejection_drain"] == job["oracle"] and all(o["exists"] is False for o in job["oracle"]["outputs"])
                      and all(raw[k] is None for k in ("started_at", "finished_at", "duration_ms", "cwd_resolved", "stdout_head", "stderr_head")), "rejection through final drain")
            else:
                peer_count += 1
                peer, closure = job["peer"], job["closure"]
                check(peer["hello"] == dict(token=job["token"], pid=peer["pid"]) and peer["pid"] == peer["pgid"] == closure["pgid"]
                      and peer["start"] == closure["start"] and closure["start"] != closure["observed_start"]
                      and closure["eof"] is True and closure["members"] == [] and payload["exit_code"] == code, "real job process/group/EOF closure")
                check(any(d.get("jobs_peer") == peer for d in durable), "retained owned script peer")
                check(str(peer["pid"]) in row["cleanup"]["identities"]
                      and row["cleanup"]["identities"][str(peer["pid"]) ] == peer["start"], "outer owned census join")
                if code == -9:
                    check(payload["reason"] == "timeout" and 5000 <= raw["duration_ms"] <= 17000 and "released" not in job, "product timeout without controller release")
                else:
                    check(payload["stdout_bytes"] == len(job["out"].encode()) and payload["stderr_bytes"] == len(job["err"].encode())
                          and payload["truncated_stdout"] is False and payload["truncated_stderr"] is False, "terminal audit exact bytes")
        snap = record["final_snapshot"]
        check(len(snap["tasks"]) == 1 and len(snap["results"]) == 2 and [r["status"] for r in snap["results"]] == ["blocked", "completed"]
              and [r["session_id"] for r in snap["results"]] == [first["session"], second["session"]], "actual final two-generation results")
        task_row = snap["tasks"][0]
        check(task_row["status"] == "completed" and task_row["completed_at"] and task_row["parent_task_id"] is None
              and task_row["orchestration_step_count"] == 2 and task_row["current_session_id"] == second["session"]
              and all(task_row[k] is None for k in ("block_kind", "active_chain", "active_fanout"))
              and json.loads(task_row["blocked_on_job_ids"]) == ids, "terminal task/historical IDs")
        actions = {k: [a for a in snap["audit"] if a["action"] == k] for k in
                   ("session_start", "session_end", "completion_report", "task_blocked_on_jobs", "task_resumed_from_jobs")}
        check(all(len(actions[k]) == 2 for k in ("session_start", "session_end", "completion_report"))
              and len(actions["task_blocked_on_jobs"]) == len(actions["task_resumed_from_jobs"]) == 1, "lifecycle counts")
        for i, session in enumerate((first["session"], second["session"])):
            a, b, d = (actions[k][i] for k in ("session_start", "session_end", "completion_report"))
            check(json.loads(a["payload"])["session_id"] == json.loads(d["payload"])["session_id"] == session
                  and a["id"] < b["id"] < d["id"], "natural start/exit/consume joins")
        wake = actions["task_resumed_from_jobs"][0]
        check(actions["completion_report"][0]["id"] < actions["task_blocked_on_jobs"][0]["id"] < wake["id"] < actions["session_start"][1]["id"], "park before natural wake")
        wp = json.loads(wake["payload"])
        check(sorted(wp["blocking_job_ids"]) == ids and wp["trigger"] == "job_terminal" and wp["triggering_job_id"] == jobs[-1]["id"]
              and wp["job_outcomes"] == {j["id"]: j["oracle"]["row"]["status"] for j in jobs}, "all-terminal wake mapping")
        if index >= 4:
            early = record["first_terminal"]
            check(len(early["results"]) == 1 and early["tasks"][0]["orchestration_step_count"] == 1
                  and early["tasks"][0]["block_kind"] == "blocked_on_job"
                  and not any(a["action"] == "task_resumed_from_jobs" for a in early["audit"])
                  and [j["status"] for j in early["jobs"]].count("pending") == 1
                  and jobs[1]["action"]["at"] > checkpoints[-2]["finished"], "pair +0 no-early-resume barrier")
        summary = ["E04_OK completed exit0", "E04_REJECT rejected E04_REJECTED_BY_REVIEW",
                   "E04_EXIT7 completed exit7; operation nonzero", "E04_TIMEOUT failed timeout exit-9",
                   "E04_PAIR A completed exit0; B completed exit7", "E04_PAIR A completed exit0; B completed exit7"][index]
        check(snap["results"][1]["output_summary"] == summary, "independent truthful final summary")
        rendered = record["task_browser"]
        check(all(t in rendered["text"] for t in (summary, "Recall tree", "Outcome", "completed"))
              and rendered["screenshot"] == f"e04-task-{index}.png" and (directory / rendered["screenshot"]).is_file(), "rendered final task/Recall")
        own = commands[record["commands_begin"]:record["commands_end"]]
        check(own and own[-1] == record["final_callback"] and own[-1]["action"] == "jobs-final"
              and "waiting_on_job_ids" not in own[-1]["payload"]
              and own[-1]["payload"]["summary"] == snap["results"][1]["output_summary"]
              and json.loads(snap["results"][1]["decision_json"]) == own[-1]["payload"]["decision"], "last real final callback")
        check([x["action"] for x in own if x["provider"]["session"] == first["session"]] == [*["jobs-submit"] * len(jobs), "jobs-block"], "S1 final CLI action")
        check([x["action"] for x in own if x["provider"]["session"] == second["session"]] == [*["jobs-read"] * (5 * len(jobs)), "jobs-final"], "S2 reads before final callback")
    check(len(all_jobs) == len(set(all_jobs)) == 8 and peer_count == 7, "eight jobs/seven scripts")
    anchor_content(by_name["final-drain-B"], 18, 30, previous)
    final_witness = [d["jobs_final_witnesses"] for d in durable if "jobs_final_witnesses" in d]
    check(len(final_witness) == 1, "actual final witness artifact content")
    calls = [w for w in final_witness[0] if w["kind"] == "callback"]
    stubs = [w for w in final_witness[0] if w["kind"] == "stub"]
    check(len(calls) == len(observations["commands"]) + len(commands) and len(stubs) == 30
          and all(w["source_sha"] == row["source_sha"] for w in calls + stubs), "all authentic source-bound executable witnesses")
    for command in commands:
        check(command["exit"] == 0 and command["source_sha"] == row["source_sha"] and command["entered"] <= command["finished"]
              and command["wire"] and all(w in observations["relay"] and w["dropped"] is False for w in command["wire"]), "actual CLI/wire record")
        if command["action"] in {"jobs-block", "jobs-final"}:
            callbacks = [w for w in command["wire"] if w["method"] == "POST" and w["path"] == f"/api/v1/orgs/alpha/tasks/{command['provider']['task']}/completion"]
            check(len(callbacks) == 1 and callbacks[0]["status"] == 200 and callbacks[0]["response"] == {"ok": True}, "actual callback200")
        digest = hashlib.sha256(json.dumps(command["argv"][1:]).encode()).hexdigest()
        check(len([w for w in calls if w["pid"] == command["child_pid"] and w["ppid"] == command["provider"]["pid"]
                   and w["argv_sha256"] == digest]) == 1, "provider child/PID/argv source witness")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--controls", action="store_true")
    parser.add_argument("--artifacts", type=Path)
    parser.add_argument("--source")
    parser.add_argument("--event", choices=["schedule"])
    parser.add_argument("--matrix-result")
    parser.add_argument("--cancellation-result")
    args = parser.parse_args()
    if args.controls:
        print(json.dumps(controls(), indent=2))
        return 0
    if not all((args.artifacts, args.source, args.event, args.matrix_result, args.cancellation_result)):
        parser.error("artifacts, source, event, matrix-result and cancellation-result are required")
    expected = MANIFEST["daily_python"]
    names = ["e2e-" + version for version in expected] + ["e2e-cancel-TERM", "e2e-cancel-INT"]
    health = dict(status="FAIL", event=args.event, source_sha=args.source,
                  cells=expected, artifacts=names, errors=[])
    try:
        admission = daily_admission(Path(__file__).resolve().parents[1])
        if admission["source_sha"] != args.source:
            raise ValueError("aggregate source differs from scheduled github.sha")
        rows = []
        required = ["result.json", *BASELINE_ARTIFACTS]
        phase_logs = [name + ".txt" for name in ("dependencies", "browser-dependencies", "browser-install",
                                                "web-dependencies", "web-build", "scenarios")]
        # Missing/empty sanitized artifacts cannot be represented as healthy.
        for version in expected:
            directory = args.artifacts / ("e2e-" + version)
            for name in required + phase_logs:
                path = directory / name
                if not path.is_file() or (name.endswith((".json", ".png")) and path.stat().st_size == 0):
                    health["errors"].append(f"missing/empty artifact: e2e-{version}/{name}")
            if (directory / "result.json").is_file():
                result = json.loads((directory / "result.json").read_text())
                rows.append(result)
                try:
                    validate_e04(result, json.loads((directory / "observations.json").read_text()),
                                 json.loads((directory / "browser-events.json").read_text()), directory)
                except (OSError, ValueError, KeyError, TypeError, IndexError) as exc:
                    health["errors"].append(f"e2e-{version}: {exc}")
        unexpected = set(path.name for path in args.artifacts.glob("e2e-*") if path.is_dir()) - set(names)
        if unexpected:
            health["errors"].append("unexpected artifact directories: " + ", ".join(sorted(unexpected)))
        try:
            validate(rows, expected, args.source, args.matrix_result)
            if any(any(row.get(key) != admission[key] for key in admission) for row in rows):
                raise ValueError("baseline daily event/source receipt mismatch")
        except (ValueError, KeyError, TypeError) as exc:
            health["errors"].append(str(exc))
        if args.cancellation_result != "success":
            health["errors"].append("cancellation jobs did not succeed: " + args.cancellation_result)
        for label, version, signum in (("TERM", "3.12", int(signal.SIGTERM)), ("INT", "3.14", int(signal.SIGINT))):
            directory = args.artifacts / ("e2e-cancel-" + label)
            for name in ["cancellation.json", "launcher.log", "launcher/active.json", "launcher/result.json"] + ["launcher/" + name for name in phase_logs]:
                path = directory / name
                if not path.is_file() or (name in {"cancellation.json", "launcher/active.json", "launcher/result.json"} and path.stat().st_size == 0):
                    health["errors"].append(f"missing/empty artifact: e2e-cancel-{label}/{name}")
            try:
                row = json.loads((directory / "cancellation.json").read_text())
                if row["status"] != "PASS" or any(row.get(key) != admission[key] for key in admission):
                    raise ValueError("failed or mismatched daily cancellation receipt")
                if any(row["launcher_result"].get(key) != admission[key] for key in admission):
                    raise ValueError("launcher daily event/source receipt mismatch")
                validate_cancellation(row, args.source, version, signum)
            except (OSError, ValueError, KeyError, TypeError) as exc:
                health["errors"].append(f"{label}: {exc}")
        if not health["errors"]:
            health["status"] = "PASS"
    except Exception as exc:
        health["errors"].append(str(exc))
    print(json.dumps(health, indent=2))
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        lines = ["## Daily Product E2E health: " + health["status"],
                 "", "Default-branch source: `" + args.source + "`", "Event: `schedule` (19:00 UTC)",
                 "", "Expected artifacts (7-day retention): " + ", ".join("`" + name + "`" for name in names),
                 "", "Baseline jobs: " + args.matrix_result + "; launcher diagnostics: " + args.cancellation_result,
                 "", "Engineering_manager owns triage through existing tasks/threads. This health result does not block merging."]
        lines.extend("\n- " + error.replace("\n", " ") for error in health["errors"])
        with Path(summary).open("a") as stream:
            stream.write("\n".join(lines) + "\n")
    return 0 if health["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
