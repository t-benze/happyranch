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
    from urllib.parse import parse_qs, urlsplit

    def check(condition: bool, message: str) -> None:
        if not condition:
            raise ValueError("E04 content: " + message)

    check(observations["variants"] == row["variants"] and observations["observer_error"] is None,
          "scenario/observation disagreement or observer failure")
    starts, commands = observations["starts"], observations["provider_commands"]
    durable, observer = observations["durable"], observations["provider_observer"]
    check(len(starts) == 37 and len({s["session"] for s in starts[:30]}) == 30, "retained30 genuine prefix providers;37 composed")
    check(len(observer) >= 2 and all(0 < b["at"] - a["at"] <= 1 for a, b in zip(observer, observer[1:])),
          "continuous provider observer interval")
    check(all(a["stubs"] <= b["stubs"] and a["calls"] <= b["calls"] for a, b in zip(observer, observer[1:])),
          "observer counter rollback")
    check(observer[-1]["stubs"] == 37 and not observer[-1]["pending"], "observer composed final closure")
    http = observations["http"]
    anchors = [r["jobs_anchor"] for r in durable if "jobs_anchor" in r and not r["jobs_anchor"]["name"].startswith("E05:")]
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
        live = record["live_s2"]
        live_snapshot = live["snapshot"]
        check(live["source_sha"] == row["source_sha"] and live["provider"] == second
              and live["observed_start"] == second["pid_start"] is not None
              and checkpoints[1]["finished"] < live["entered"] <= live["finished"]
              < record["final_callback"]["entered"], "source-bound live S2 frame before callback")
        check(len(live_snapshot["tasks"]) == 1 and len(live_snapshot["results"]) == 1,
              "live S2 one task and one historical result")
        live_task, blocked = live_snapshot["tasks"][0], live_snapshot["results"][0]
        check(live_task["id"] == blocked["task_id"] == task and live_task["status"] == "in_progress"
              and live_task["current_session_id"] == second["session"]
              and live_task["orchestration_step_count"] == 2 and live_task["completed_at"] is None
              and live_task["block_kind"] is None and live_task["parent_task_id"] is None
              and json.loads(live_task["blocked_on_job_ids"]) == ids
              and blocked["status"] == "blocked" and blocked["session_id"] == first["session"]
              and blocked["agent"] == "case_manager" and live_snapshot["results"] == record["parked"]["results"],
              "live S2 binding with unchanged blocked S1 result")
        check(live_snapshot["jobs"] == sorted([j["oracle"]["row"] for j in jobs], key=lambda j: j["id"]),
              "live S2 durable job outcomes")
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
            check(set(reads) == {"show", "receipt", "output", "tail", "wait"}, "all five declared read operations")
            # Shipping routes expose the full JobRecord for show/wait, and a
            # closed identity for receipt/output. Build both from the raw row,
            # never from whichever fields the CLI happened to return.
            detail = dict(raw, review_required=bool(raw["review_required"]), persistent=bool(raw["persistent"]))
            receipt = dict(authentication=dict(task_id=task, session_id=second["session"]),
                           job={k: raw[k] for k in ("id", "task_id", "agent_name", "title", "rationale", "script_text",
                                "interpreter", "cwd_hint", "cwd_resolved", "status", "exit_code", "reason", "duration_ms",
                                "created_at", "started_at", "finished_at")}, output=output)
            tail = dict(stream="stdout", lines=job["out"].splitlines())
            expected_bodies = dict(show=detail, receipt=receipt, output=receipt, tail=tail,
                                   wait=dict(detail, timed_out=False))
            previous_read_end = live["finished"]
            for operation in ("receipt", "show", "output", "tail", "wait"):
                command = reads[operation]
                check(command["action"] == "jobs-read" and command["operation"] == operation
                      and command["provider"] == {k: second[k] for k in ("task", "session", "agent", "org", "pid")}
                      and previous_read_end <= command["entered"] <= command["finished"] < record["final_callback"]["entered"],
                      "actual live S2 ordered read identity/time")
                previous_read_end = command["finished"]
                verb = "show" if operation == "receipt" else operation
                argv = ["jobs", verb, job["id"], "--org", "alpha"]
                if operation in {"receipt", "output"}:
                    argv += ["--json", "--task-id", task, "--session-id", second["session"]]
                if operation == "wait":
                    argv += ["--timeout-seconds", "1"]
                if operation in {"tail", "wait"}:
                    argv += ["--task-id", task, "--session-id", second["session"]]
                check(command["argv"][1:] == argv, "shipping read argv")
                suffix = "" if operation == "show" else "/receipt" if operation in {"receipt", "output"} else "/" + operation
                query = {} if operation == "show" else dict(task_id=[task], session_id=[second["session"]])
                if operation == "output":
                    query.update(stream=["both"], max_bytes=["1048576"])
                elif operation == "tail":
                    query.update(stream=["stdout"], lines=["50"])
                elif operation == "wait":
                    query.update(timeout_seconds=["1"])
                # Org discovery stays in wire evidence but cannot satisfy a
                # job read. Count only the job operation, not all requests.
                wire = [w for w in command["wire"] if "/jobs/" in urlsplit(w["path"]).path]
                check(len(wire) == 1, "one actual job operation per read")
                actual = wire[0]
                url = urlsplit(actual["path"])
                check(actual["method"] == ("POST" if operation == "wait" else "GET")
                      and url.path == f"/api/v1/orgs/alpha/jobs/{job['id']}{suffix}"
                      and not url.scheme and not url.netloc and not url.fragment
                      and parse_qs(url.query, keep_blank_values=True, strict_parsing=True) == query
                      and actual["status"] == 200 and actual["dropped"] is False
                      and actual["response"] == expected_bodies[operation], "shipping read HTTP200/query/actual body vs durable row")
                if operation in {"receipt", "output"}:
                    check(json.loads(command["stdout"]) == actual["response"], "CLI JSON equals actual receipt response")
                elif operation == "tail":
                    check(command["stdout"] == "".join(line + "\n" for line in actual["response"]["lines"]) == job["out"],
                          "CLI tail equals actual response and durable output")
                elif operation == "wait":
                    check(json.loads(command["stdout"]) == dict(status=actual["response"]["status"], timed_out=False),
                          "CLI wait equals actual terminal response")
                else:
                    lines = command["stdout"].splitlines()
                    check(bool(lines) and lines[0] == f"{job['id']}   {status}   submitted {raw['created_at']}",
                          "human show first-line ID/status/timestamp, independent of title")
                    if code is not None:
                        check(f"Exit code:    {code}" in lines, "human show literal exit")
                    if code == -9:
                        check("Reason:       timeout" in lines, "human show timeout reason")
                    if code is None:
                        check("Reject reason: E04_REJECTED_BY_REVIEW" in lines, "human show rejection reason")
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
        check(blocked == snap["results"][0] and all(a in snap["audit"] for a in live_snapshot["audit"]),
              "live S1 result/audit content retained through final S2 consumption")
        live_actions = {k: [a for a in live_snapshot["audit"] if a["action"] == k] for k in
                        ("session_start", "session_end", "completion_report", "task_blocked_on_jobs", "task_resumed_from_jobs")}
        check([len(live_actions[k]) for k in live_actions] == [2, 1, 1, 1, 1]
              and [json.loads(a["payload"])["session_id"] for a in live_actions["session_start"]]
                  == [first["session"], second["session"]]
              and json.loads(live_actions["completion_report"][0]["payload"])["session_id"] == first["session"]
              and all(a["task_id"] == task for rows in live_actions.values() for a in rows)
              and live_actions["completion_report"][0]["id"] < live_actions["task_blocked_on_jobs"][0]["id"]
                  < live_actions["task_resumed_from_jobs"][0]["id"] < live_actions["session_start"][1]["id"],
              "captured natural S2 start after consumed S1/wake and before S2 completion")
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
              and own[-1]["payload"]["task_id"] == task and own[-1]["payload"]["session_id"] == second["session"]
              and "waiting_on_job_ids" not in own[-1]["payload"]
              and own[-1]["payload"]["summary"] == snap["results"][1]["output_summary"]
              and json.loads(snap["results"][1]["decision_json"]) == own[-1]["payload"]["decision"], "last real final callback")
        check([x["action"] for x in own if x["provider"]["session"] == first["session"]] == [*["jobs-submit"] * len(jobs), "jobs-block"], "S1 final CLI action")
        check([x["action"] for x in own if x["provider"]["session"] == second["session"]] == [*["jobs-read"] * (5 * len(jobs)), "jobs-final"], "S2 reads before final callback")
    check(len(all_jobs) == len(set(all_jobs)) == 8 and peer_count == 7, "eight jobs/seven scripts")
    anchor_content(by_name["final-drain-B"], 18, 30, previous)
    final_witness = [d["jobs_final_witnesses"] for d in durable if "jobs_final_witnesses" in d]
    check(len(final_witness) == 1, "actual final witness artifact content")
    check(observations["launch_witnesses"][:len(final_witness[0])] == final_witness[0]
          and observations["witness_incomplete_line"] is False, "complete unchanged E04 witness prefix")
    e05 = [d["threads_cases"] for d in durable if "threads_cases" in d]
    check(len(e05) == 1 and e05[0]["entered"] >= by_name["final-drain-B"]["finished"], "E05 only after complete E04")
    prefix_commands = observations["commands"][:e05[0]["prefix_command_count"]]
    attempts = observations["provider_attempts"]
    check(len(attempts) == len(commands) and [a["command_index"] for a in attempts] == list(range(len(commands)))
          and all(a["entered"] <= a["finished"] and a["provider"]["session"] == c["provider"]["session"]
                  and a["instruction"]["action"] == c["action"]
                  and c["execution_deadline"] == a["instruction"]["execution_deadline"] == a["instruction"]["deadline"] - 1
                  and a["entered"] <= c["entered"] <= c["finished"] <= a["finished"] < a["instruction"]["deadline"]
                  <= a["entered"] + 10 for a, c in zip(attempts, commands)), "all CLI attempts accounted")
    calls = [w for w in final_witness[0] if w["kind"] == "callback"]
    stubs = [w for w in final_witness[0] if w["kind"] == "stub"]
    check(len(calls) == len(prefix_commands) + len(commands) and len(stubs) == 30
          and all(w["source_sha"] == row["source_sha"] for w in calls + stubs), "all authentic source-bound executable witnesses")
    for command in commands:
        check(command["exit"] == 0 and command["timed_out"] is False and command["source_sha"] == row["source_sha"]
              and command["entered"] <= command["finished"]
              and command["wire"] and all(w in observations["relay"] and w["dropped"] is False for w in command["wire"]), "actual CLI/wire record")
        if command["action"] in {"jobs-block", "jobs-final"}:
            callbacks = [w for w in command["wire"] if w["method"] == "POST" and w["path"] == f"/api/v1/orgs/alpha/tasks/{command['provider']['task']}/completion"]
            check(len(callbacks) == 1 and callbacks[0]["status"] == 200 and callbacks[0]["response"] == {"ok": True}, "actual callback200")
        digest = hashlib.sha256(json.dumps(command["argv"][1:]).encode()).hexdigest()
        check(len([w for w in calls if w["pid"] == command["child_pid"] and w["ppid"] == command["provider"]["pid"]
                   and w["argv_sha256"] == digest]) == 1, "provider child/PID/argv source witness")


def validate_e05(row: dict, observations: dict, directory: Path) -> None:
    """Independent literal-content consumer, never import the scenario oracle."""
    import hashlib
    from collections import Counter

    def check(condition: bool, message: str) -> None:
        if not condition:
            raise ValueError("E05 content: " + message)

    durable = observations["durable"]
    cases = [d["threads_cases"] for d in durable if "threads_cases" in d]
    check(len(cases) == 1, "one finite C/N/D record")
    case = cases[0]
    variants = ["E05.compose-race", "E05.reply-natural", "E05.self-dispatch-followup", "E05.consumed-token", "E05.nonparticipant-task-send"]
    check(case["source_sha"] == row["source_sha"] and case["variants"] == variants
          and all(row["variants"][v]["evidence"]["threads"] == case for v in variants), "five actual-content references")
    check(case["final_entered"] <= case["finished"] <= case["final_deadline"] <= case["final_entered"]+4, "bounded final evidence allowance")
    groups = case["groups"]
    check([g["name"] for g in groups] == ["C", "N", "D"], "exact finite group order")
    C, N, D = groups
    starts = observations["starts"]
    check(len(starts) == len({s["session"] for s in starts}) == 37, "37 unique runtime sessions")
    providers = C["providers"] + N["providers"] + D["providers"]
    check(len(providers) == 6 and len({p["token_sha256"] for p in providers}) == 6
          and {s["session"] for s in starts[30:]} == {p["session"] for p in providers + [D["task"]]}, "six genuine thread tokens and one task")
    check(all(p in starts and p["org"] == "alpha" and p["pid_start"] for p in providers), "actual thread socket identities")
    anchors = [d["jobs_anchor"] for d in durable if "jobs_anchor" in d]
    previous = next(a for a in anchors if a["name"] == "final-drain-B")
    check(len([a for a in anchors if a["name"].startswith("E05:")]) == 4, "four E05 group/drain boundaries")
    for group, total, count, allowance in zip(groups, (21,22,25), (33,34,37), (18,8,25)):
        check(group["entered"] <= group["finished"] <= group["deadline"] <= group["entered"] + allowance,
              "original finite group allowance")
        a = group["anchor"]
        check(a in anchors and a["name"] == "E05:" + group["name"] and a["source_sha"] == row["source_sha"]
              and a["total"] == total and a["provider_count"] == count, "actual anchor joins")
        check((a["daemon_pid"],a["daemon_start"]) == (previous["daemon_pid"],previous["daemon_start"])
              and previous["finished"] <= a["entered"] <= a["finished"], "no reset or replacement")
        samples = a["samples"]
        check(len(samples) >= 2, "two independently read metrics samples")
        projections = []
        for sample in samples:
            m = sample["metrics"]
            check(any(h["method"] == "GET" and h["path"] == "/api/v1/metrics" and h["status"] == 200 and h["response"] == m
                      for h in observations["http"]), "actual metrics response content")
            h = m["host_sessions"]
            r, adm = h["receipts"], h["admission"]
            check(h["wired"] is True and h["backend"]["healthy"] is True and h["backend"]["name"] != "passthrough"
                  and h["residue"]["admission_blocked"] is False and h["residue"]["survivors_count"] == 0, "live host health")
            check(r["published_total"] == r["window_size"] == len(r["recent"]) == r["quiescent_count"] <= total <= 64
                  and r["with_residue_count"] == 0 and all(x["quiescent"] is True and x["survivors_count"] == 0 for x in r["recent"]), "no eviction/residue")
            delta = r["published_total"] - previous["total"]
            check(delta >= 0 and r["recent"][delta:] == previous["projection"]["receipts"]["recent"], "retained ordered duplicate-preserving tail")
            check(adm["cancelled_queued_total"] == 0 and adm["shutdown"] is False, "no cancelled admission")
            projections.append(dict(admission=adm, receipts=r, executor_sessions_active=m["executor_sessions_active"], run_step_queue_depth=m["run_step_queue_depth"]))
        p = a["projection"]
        check(projections[-1] == projections[-2] == p and p["admission"]["admitted_total"] == p["admission"]["released_total"]
              == p["receipts"]["published_total"] == total and p["admission"]["active"] == p["admission"]["queue_depth"]
              == p["executor_sessions_active"] == p["run_step_queue_depth"] == 0, "settled receipt/release/live conjunction")
        check(len(a["provider_closure"]) == count and all(x["pid"] == s["pid"] and x["start"] == s["pid_start"]
              and x["observed_start"] != x["start"] for x,s in zip(a["provider_closure"],starts[:count])), "natural provider closure")
        previous = a
    final_anchor = case["final_anchor"]
    check(final_anchor in anchors and final_anchor["name"] == "E05:final" and final_anchor["projection"] == previous["projection"]
          and final_anchor["provider_count"] == 37 and final_anchor["entered"] >= previous["finished"], "unchanged final drain")
    check(len(final_anchor["samples"]) >= 2 and all(s["metrics"]["host_sessions"]["receipts"] == previous["projection"]["receipts"]
              for s in final_anchor["samples"][-2:]), "actual final rereads")
    check(Counter(x["invocation_kind"] for x in final_anchor["projection"]["receipts"]["recent"]) == {"task":19,"thread":6}, "B25 canonical task/thread mix")

    final = case["final"]["content"]
    expected_sources = ("e2e/threads.py", "e2e/thread_provider.py", "e2e/provider.py", "e2e/controller.py", "e2e/browser.py",
                        "e2e/transport.py", "e2e/oracles.py", "e2e/aggregate.py", "tests/integration/fake_claude.sh", "cli/commands/threads.py",
                        "runtime/daemon/routes/threads.py", "runtime/daemon/thread_runner.py", "web/src/shared/threads/NewThreadDialog.tsx")
    source_root = Path(__file__).resolve().parents[1]
    check(case["source_hashes"] == {name:hashlib.sha256((source_root/name).read_bytes()).hexdigest() for name in expected_sources}, "actual immutable producer/consumer source bytes")
    beta = [d["threads_beta"] for d in durable if "threads_beta" in d]
    check(len(beta) == 1 and not any(beta[0][k] for k in beta[0] if k.startswith("thread") or k in {"jobs","files"}), "beta has no thread/job/file effects")
    check(len(final["jobs"]) == 8 and {j["id"] for j in final["jobs"]} == {
        j["id"] for d in durable if "jobs_cases" in d for r in d["jobs_cases"] for j in r["jobs"]}, "unchanged eight prior jobs")
    check(len(case["final"]["samples"]) >= 2 and case["final"]["samples"][-1]["content"]
          == case["final"]["samples"][-2]["content"] == final, "coherent final rereads")
    check([len(final[k]) for k in ("threads","thread_participants","thread_messages","thread_invocations","thread_reply_delivery_state")]
          == [3,4,8,6,4], "exact3 threads/4 participants/8 messages/6 invocations/4 delivery pairs")
    check(all(not final[k] for k in ("thread_reply_exchange","thread_exchange_deferrals","thread_reply_breaker_episodes",
          "thread_reply_breaker_receipts","thread_message_attachments","thread_scoped_attachments","task_attachments","files")), "no extra effect/attachment/breaker")
    check(Counter(i["status"] for i in final["thread_invocations"]) == {"consumed":3,"declined":3}
          and Counter(i["purpose"] for i in final["thread_invocations"]) == {"reply":5,"task_followup":1}, "terminal invocation outcome/purpose")
    expected_messages = [
        [(1,"founder","message","E05 opening\nliteral beta"),(2,"case_manager","message","E05 reply\n@case_worker received")],
        [(1,"founder","message","E05 denial anchor")],
        [(1,"founder","message","E05 do one task"),(2,"case_manager","system",None),(3,"case_manager","message","E05 task accepted"),
         (4,"case_manager","system",None),(5,"case_manager","message","E05 task complete")]]
    for group, expected, subject, turns, names in zip(groups,expected_messages,("E05 compose race","E05 denial venue","E05 dispatch"),
                                                    (2,1,3), ({"case_manager","case_worker"},{"case_worker"},{"case_manager"})):
        tid = group["thread"]
        thread = [t for t in final["threads"] if t["id"] == tid]
        check(len(thread) == 1 and thread[0]["subject"] == subject and thread[0]["status"] == "open"
              and thread[0]["turns_used"] == turns and thread[0]["turn_cap"] == 500, "literal thread fields")
        messages = [m for m in final["thread_messages"] if m["thread_id"] == tid]
        check([(m["seq"],m["speaker"],m["kind"],m["body_markdown"]) for m in messages] == expected, "literal message timeline")
        participants = [p for p in final["thread_participants"] if p["thread_id"] == tid]
        check({p["agent_name"] for p in participants} == names and len(participants) == len(names), "literal participants")
        invs = [i for i in final["thread_invocations"] if i["thread_id"] == tid]
        public = group["settled"]["public"]
        check(any(h["method"] == "GET" and h["path"] == f"/api/v1/orgs/alpha/threads/{tid}" and h["status"] == 200
                  and h["response"] == public for h in observations["http"]), "actual public response reference")
        check(public["thread_id"] == tid and public["subject"] == subject and public["turns_used"] == turns
              and not public["reply_delivery"] and set(public["participants"]) == names and len(public["messages"]) == len(messages), "actual public thread projection")
        check(json.loads(group["settled"]["cli"]["stdout"]) == public and group["settled"]["cli"] in observations["commands"]
              and group["settled"]["cli"]["exit"] == 0, "actual CLI readback")
        check(group["settled"]["messages_page"]["messages"] == public["messages"] and group["settled"]["messages_page"]["has_more"] is False,
              "actual complete message page")
        for message, projected in zip(messages,public["messages"]):
            check(all(message[k] == projected[k] for k in ("seq","speaker","kind","body_markdown"))
                  and projected["attachments"] == [], "public literal message content")
            responders = [i for i in invs if i["triggering_seq"] == message["seq"]]
            check(sorted((r["agent_name"],r["purpose"],r["status"]) for r in projected["responder_status"])
                  == sorted((i["agent_name"],i["purpose"],"replied" if i["status"] == "consumed" else "declined") for i in responders),
                  "public authentic responder purpose/outcome; no invented SID")
        for provider in group["providers"]:
            matches = [i for i in invs if i["invocation_token"] == "sha256:" + provider["token_sha256"]]
            check(len(matches) == 1, "genuine delivered token digest")
            i = matches[0]
            check(i["session_id"] == provider["session"] and i["agent_name"] == provider["agent"] and i["purpose"] == provider["purpose"]
                  and i["started_at"] and i["consumed_at"], "distinct genuine runtime identity")
            expected_reply = (2 if tid == C["thread"] else 5 if i["purpose"] == "task_followup" else 3) if i["agent_name"] == "case_manager" else None
            check(i["reply_message_seq"] == expected_reply and i["status"] == ("consumed" if expected_reply else "declined"), "exact reply linkage and decline outcomes")
            if i["purpose"] == "reply":
                claims = [json.loads(a["payload"]) for a in final["audit_log"] if a["action"] == "thread_reply_wake_claimed"
                          and a["task_id"] == tid and json.loads(a["payload"]).get("token_prefix") == i["token_prefix"]]
                seq = 2 if tid == C["thread"] and i["agent_name"] == "case_worker" and i["triggering_seq"] == 2 else 1
                check(len(claims) == 1 and claims[0]["agent_name"] == i["agent_name"]
                      and claims[0]["from_seq"] == claims[0]["through_seq"] == seq, "exact admitted delivery range")
        for delivery in [r for r in final["thread_reply_delivery_state"] if r["thread_id"] == tid]:
            watermark = 2 if tid == C["thread"] and delivery["agent_name"] == "case_worker" else 1
            check(delivery["acknowledged_through_seq"] == delivery["required_through_seq"] == watermark
                  and all(delivery[k] is None for k in ("queued_invocation_token","running_invocation_token","running_from_seq","running_through_seq")), "exact settled delivery watermarks")

    T, sid = D["task"]["task"], D["task"]["session"]
    dispatched = [t for t in final["tasks"] if t["dispatched_from_thread_id"] is not None]
    check(len(dispatched) == 1, "one sourced task only")
    t = dispatched[0]
    check(t["id"] == T and t["dispatched_from_thread_id"] == D["thread"] and t["assigned_agent"] == "case_manager"
          and t["team"] == "engineering" and t["brief"] == "E05 one owned task" and t["status"] == "completed"
          and t["completed_at"] and t["current_session_id"] == sid and t["orchestration_step_count"] == 1
          and t["note"] == "E05 task complete" and all(t[k] is None for k in ("parent_task_id","block_kind","active_chain","active_fanout","final_output_dir")), "actual single completed root")
    result = [r for r in final["task_results"] if r["task_id"] == T]
    check(len(result) == 1 and type(result[0]["id"]) is int and result[0]["session_id"] == sid
          and result[0]["status"] == "completed" and result[0]["output_summary"] == "E05 task complete"
          and json.loads(result[0]["decision_json"]) == {"action":"done","summary":"E05 task complete"}, "one genuine accepted task result")
    task_audit = [a for a in final["audit_log"] if a["task_id"] == T]
    required = [next((a for a in task_audit if a["action"] == name),None) for name in ("session_start","session_end","completion_report","orchestration_step")]
    check(all(required) and all(sum(a["action"] == name for a in task_audit) == 1 for name in ("session_start","session_end","completion_report","orchestration_step"))
          and [a["id"] for a in required] == sorted(a["id"] for a in required), "one ordered task lifecycle/consumption")
    systems = [json.loads(m["system_payload_json"]) for m in final["thread_messages"] if m["thread_id"] == D["thread"] and m["kind"] == "system"]
    dispatch_audit = [a for a in final["audit_log"] if a["action"] == "thread_dispatch"]
    check(len(dispatch_audit) == 1 and dispatch_audit[0]["task_id"] == D["thread"]
          and json.loads(dispatch_audit[0]["payload"])["task_id"] == T
          and json.loads(dispatch_audit[0]["payload"])["dispatcher"] == "case_manager", "real original dispatch audit ownership")
    check([i["dispatched_task_id"] for i in final["thread_invocations"] if i["dispatched_task_id"]] == [T]
          and not any(a["action"] in {"thread_followup_skipped","thread_invocation_failed"} for a in final["audit_log"]), "no duplicate/recursive dispatch or lost followup")
    check(systems == [dict(kind_tag="task_dispatched",task_id=T,dispatcher="case_manager",target_agent="case_manager",team="engineering",brief_preview="E05 one owned task"),
                      dict(kind_tag="task_completed",task_id=T,original_task_id=T,root_task_id=T,status="completed",final_output_summary="E05 task complete",final_output_dir=None,cancelled=False,revisit_chain_length=1,revisit_task_id=None)], "both literal system payloads")
    check(len(D["tasks_projection"]) == 1 and D["tasks_projection"][0]["id"] == T, "actual thread task link")

    for negative, status, detail in ((C["replay"],409,dict(code="invocation_token_consumed",status="consumed")),
                                     (D["denial"],403,dict(code="not_a_participant",composer="case_manager"))):
        before, after, cmd = negative["before"], negative["after"], negative["command"]
        check(before["content"] == after["content"] and before["samples"][-1]["at"] <= cmd["entered"] <= cmd["finished"] <= after["samples"][0]["at"], "coherent negative causal window")
        for f in (before,after):
            check(len(f["samples"]) >= 2 and f["samples"][-1]["content"] == f["samples"][-2]["content"] == f["content"], "two stable full frames")
        check(cmd["exit"] == 1 and cmd["selected_response"] == {"detail":detail}
              and len([w for w in cmd["wire"] if w["method"] == "POST" and w["status"] == status and w["response"] == {"detail":detail}]) == 1, "literal409/403 real CLI denial")
    check(C["reply"]["argv"] == C["replay"]["command"]["argv"] and C["reply"]["payload_sha256"] == C["replay"]["command"]["payload_sha256"], "exact same retained live reply file")

    commands, attempts = observations["thread_commands"], observations["thread_attempts"]
    check(len(commands) == len(attempts) == 11 and [a["command_index"] for a in attempts] == list(range(11)), "finite eleven provider CLI actions")
    witnesses = observations["launch_witnesses"]
    check(case["witnesses"] == witnesses and len([w for w in witnesses if w["kind"] == "stub"]) == 37, "complete actual executable witnesses")
    calls = [w for w in witnesses if w["kind"] == "callback"]
    check(len(calls) == len(observations["commands"])+len(observations["provider_commands"])+len(commands), "every CLI accounted")
    closures = [d["thread_process_closed"] for d in durable if "thread_process_closed" in d]
    check(len(closures) == 7 and {r["session"] for r in closures} == {p["session"] for p in providers+[D["task"]]}, "seven actual natural process closures")
    for provider in providers+[D["task"]]:
        closure = next(r for r in closures if r["session"] == provider["session"])
        key = "token_sha256" if "thread" in provider else "session"
        own = [cmd for cmd in commands if cmd["provider"].get(key) == provider[key]]
        check(closure["pid"] == provider["pid"] and closure["start"] == provider["pid_start"] != closure["observed_start"]
              and len(closure["children"]) == len(own) and all(ch["pid"] == cmd["child_pid"] and ch["start"] == cmd["child_start"] != ch["observed_start"]
              for ch,cmd in zip(closure["children"],own)), "actual provider and CLI child PID/start closure")
        paths = [cmd["argv"][cmd["argv"].index("--from-file")+1] for cmd in own if "--from-file" in cmd["argv"]]
        check(closure["private_files"] == [dict(path=path,exists=False) for path in paths], "actual named private payload absence")
    for barrier, admitted, released, command_start in ((C["worker_closed"],20,19,C["reply"]["entered"]),
                                                      (D["dispatcher_closed"],24,23,D["denial"]["command"]["entered"])):
        check(barrier in [d["thread_live_boundary"] for d in durable if "thread_live_boundary" in d]
              and barrier["finished"] <= command_start and len(barrier["samples"]) >= 2, "actual intermediate host barrier")
        for sample in barrier["samples"][-2:]:
            m = sample["metrics"]
            a,r = m["host_sessions"]["admission"],m["host_sessions"]["receipts"]
            check((a["admitted_total"],a["released_total"],r["published_total"],a["active"],a["queue_depth"]) == (admitted,released,released,1,0)
                  and any(h["path"] == "/api/v1/metrics" and h["response"] == m for h in observations["http"]), "two actual release/publication/live samples")
    check(C["held_worker"]["provider"] == C["providers"][2] and C["held_worker"]["before"] == C["held_worker"]["after"] == C["providers"][2]["pid_start"], "W2 held across same-process replay")
    for cmd,a in zip(commands,attempts):
        keys = ("thread","token_sha256","agent","org","purpose","pid") if "thread" in a["provider"] else ("task","session","agent","org","pid")
        check(cmd["provider"] == {k:a["provider"][k] for k in keys} and a["provider"] in starts, "actual provider receipt identity")
        check(cmd["source_sha"] == row["source_sha"] and cmd["timed_out"] is False and cmd["output_truncated"] is False and a["observed_start"] == a["provider"]["pid_start"]
              and a["entered"] <= cmd["entered"] <= cmd["finished"] <= a["finished"] < a["instruction"]["deadline"]
              <= a["entered"] + 10 and cmd["execution_deadline"] == a["instruction"]["deadline"] - 1, "original live owner/deadline")
        check(all(w in observations["relay"] and w["dropped"] is False for w in cmd["wire"]) and cmd["wire"], "actual complete relay references")
        digest = hashlib.sha256(json.dumps(cmd["argv"][1:]).encode()).hexdigest()
        check(len([w for w in calls if w["pid"] == cmd["child_pid"] and w["ppid"] == cmd["provider"]["pid"]
                   and w["argv_sha256"] == digest and w["source_sha"] == row["source_sha"]]) == 1, "independent actual CLI child/source join")
    expected_actions = [["thread-reply","thread-reply"],["thread-decline"],["thread-decline"],["thread-decline"],
                        ["thread-dispatch","thread-reply"],["thread-task-read","thread-reply"],["thread-task-send","thread-task-final"]]
    for provider, actions in zip(providers+[D["task"]],expected_actions):
        key = "token_sha256" if "thread" in provider else "session"
        own = [cmd for cmd in commands if cmd["provider"].get(key) == provider[key]]
        check([cmd["action"] for cmd in own] == actions, "finite per-provider action order/final callback last")
    check(D["callback"]["selected_response"] == {"ok":True} and D["callback"]["exit"] == 0
          and D["callback"] in commands and D["followup"] in commands and D["read"] in commands
          and D["callback"]["finished"] <= D["providers"][1]["at"] <= D["read"]["entered"] <= D["followup"]["entered"], "task callback to natural D2 read/reply")
    events = json.loads((directory / "e05-browser-events.json").read_text())
    check(C["created"] in events and C["created"]["dialog_count"] == 0 and C["created"]["list_rows"] == 1, "actual dialog closure and one list row")
    check(events and events[-1]["event"] == "context-closed" and not any(e["event"] in {"pageerror","origin-bypass"} for e in events), "real browser origin/closure")
    requests = [e for e in events if e["event"] == "compose-request"]
    responses = [e for e in events if e["event"] == "compose-response"]
    proxy = [p for p in observations["thread_proxy"] if p["ordinal"] is not None]
    check(len(requests) == len(responses) == len(proxy) == 1, "one page/upstream/original response")
    request, response, wire = requests[0],responses[0],proxy[0]
    transport = case["transport"]
    check(len({transport[k] for k in ("daemon_port","cli_port","browser_port")}) == 3
          and all(transport[k] in transport["ports"] for k in ("daemon_port","cli_port","browser_port")), "registered owned D/L/P")
    origin = f"http://127.0.0.1:{transport['browser_port']}"
    check(request["url"] == response["url"] == origin + "/api/v1/orgs/alpha/threads"
          and wire["target"] == "/api/v1/orgs/alpha/threads" and request["method"] == wire["method"] == "POST"
          and request["payload"] == wire["request"] == {"subject":"E05 compose race","recipients":["case_manager","case_worker"],"body_markdown":"E05 opening\nliteral beta"}
          and request["sha256"] == wire["request_sha256"] and request["bytes"] == wire["request_bytes"], "exact end-to-end compose bytes")
    check(wire["status"] == response["status"] == 200 and wire["response"] == response["body"]
          and wire["response_sha256"] == response["sha256"] and wire["response_bytes"] == response["bytes"]
          and wire["response"]["thread_id"] == C["thread"] and set(wire["response"]["pending_replies"]) == {"case_manager","case_worker"}, "original actual200 identity/payload")
    check(C["armed"]["at"] <= wire["forwarded"] <= wire["upstream_complete"] <= wire["committed"] <= wire["held"] <= C["released"]
          <= wire["delivered"] <= wire["closed"] <= C["armed"]["deadline"] <= C["armed"]["at"]+5, "single bounded hold/release timeline")
    check(len(wire["commit"]["threads"]) == len(wire["commit"]["thread_messages"]) == 1
          and wire["commit"]["thread_messages"][0]["body_markdown"] == "E05 opening\nliteral beta", "actual independent commit content")
    check(not any(e["event"] == "compose-response" or (e["event"] == "navigation" and e["url"] == origin+f"/orgs/alpha/threads/{C['thread']}") for e in C["held_events"]), "no response/detail before release")
    check(len([e for e in C["released_events"] if e["event"] == "navigation" and e["url"] == origin+f"/orgs/alpha/threads/{C['thread']}"]) == 1, "single real SPA detail entry")
    batch = C["event_batch"]
    check(batch in events and batch["guard_sensitivity"] == "UNPROVEN"
          and [(e["id"],e["type"]) for e in batch["rows"] if e["phase"] == "before"] == [(1,"keydown"),(2,"click"),(3,"click")], "same synchronous finite input sequence; no causal guard claim")
    check(all(a["at"] <= b["at"] for a,b in zip(batch["rows"],batch["rows"][1:])), "actual DOM event ordering")
    for view, name in zip((C["initial_view"],C["view"],D["view"]), ("e05-compose.png","e05-reply.png","e05-dispatch.png")):
        check(view["screenshot"] == name and any(e.get("event") == "thread-view" and all(e[k] == v for k,v in view.items()) for e in events)
              and (directory/view["screenshot"]).read_bytes().startswith(b"\x89PNG\r\n\x1a\n"), "actual browser view and screenshot content")
    check(f"dispatched {T}" in D["view"]["text"] and f"task {T} completed" in D["view"]["text"], "both rendered system rows")
    closure = [d["thread_proxy_closure"] for d in durable if "thread_proxy_closure" in d]
    check(len(closure) == 1 and closure[0]["port"] == transport["browser_port"] and not closure[0]["errors"]
          and closure[0]["workers_alive"] == closure[0]["connections"] == 0 and closure[0]["listener_alive"] is False, "bounded proxy/socket/listener closure")
    final_orgs = [d for d in durable if "threads_final_org" in d]
    check(len(final_orgs) == 2 and {d["threads_final_org"] for d in final_orgs} == {"alpha","beta"}, "both final orgs")
    for d in final_orgs:
        expected = (20,30) if d["threads_final_org"] == "alpha" else (1,1)
        check((len(d["snapshot"]["tasks"]),len(d["snapshot"]["results"])) == expected
              and all(t["status"] == "completed" for t in d["snapshot"]["tasks"]), "global21tasks31results")


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
                    validate_e05(result, json.loads((directory / "observations.json").read_text()), directory)
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
