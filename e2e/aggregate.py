"""Event health: missing cells/variants, skips and unknown cleanup always fail."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import signal
from datetime import datetime, timedelta

from cancellation import validate as validate_cancellation
from run import BASELINE_ARTIFACTS, daily_admission, validate_admission, validate_timing
from pathlib import Path
import completed


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
    results.update(admission_controls())
    results.update(completed_controls())
    return results


def completed_controls() -> dict:
    """Extend the existing reader controls; no Product/daemon execution."""
    import tempfile
    from controller import Controller
    results = {}
    with tempfile.TemporaryDirectory(prefix="e2e-completed-") as temporary:
        root = Path(temporary)
        (root / "coord").mkdir(mode=0o700)
        controller = Controller.__new__(Controller)
        controller.root = root
        controller.results = {}
        controller.completed_identity = dict(source_sha="1" * 40, source_tree="2" * 40,
            run_id="123", run_attempt="1", python="3.12", exercise="baseline")
        controller.completed = dict(schema=1, identity=controller.completed_identity, cases=[])
        controller.completed_files = {}
        controller.http, controller.commands, controller.starts, controller.durable = [], [], [], []
        # Actual publisher accepts only closed fields, even if future-secret raw
        # content is already present in the producer's original result evidence.
        secret = "future-secret-body-must-never-be-published"
        controller.mark(MANIFEST["variants"][0], dict(raw_body=secret))
        original_result = copy.deepcopy(controller.results)
        accepted, bundle = completed.read_bundle(root / "coord", controller.completed_identity)
        assert len(accepted["cases"]) == 1 and all(secret.encode() not in data for data in bundle.values())
        try:
            controller.mark(MANIFEST["variants"][0])
        except AssertionError:
            results["completed-duplicate"] = "refused"
        else:
            raise AssertionError("duplicate completed case accepted")
        # Later arbitrary evidence/export/cleanup failure does not touch the
        # separately committed prefix. It also supplies no independent PASS.
        controller.results.clear()
        again, after = completed.read_bundle(root / "coord", controller.completed_identity)
        assert again == accepted and after == bundle
        for name, data in bundle.items():
            completed.publish(root, name, data)
        copied, _ = completed.read_bundle(root, controller.completed_identity)
        assert copied == accepted
        files = {name: data for name, data in bundle.items() if name != "completed-cases.json"}
        for case in ("duplicate", "source_sha", "source_tree", "run_id", "run_attempt", "python", "exercise",
                     "variant", "sequence", "manifest_index", "unsafe-path", "missing-evidence", "raw-field", "digest"):
            value, evidence = copy.deepcopy(accepted), dict(files)
            if case == "duplicate":
                value["cases"].append(copy.deepcopy(value["cases"][0]))
            elif case in completed.IDENTITY:
                value["identity"][case] = dict(source_sha="3" * 40, source_tree="4" * 40,
                    run_id="124", run_attempt="2", python="3.14", exercise="cancellation")[case]
                # Coherently wrong artifact identity must still refuse the
                # independently expected source/run/lane, not another guard.
                body = completed.decode(evidence["case-01.json"])
                body["identity"] = dict(value["identity"])
                evidence["case-01.json"] = completed.encode(body)
                value["cases"][0]["evidence"].update(bytes=len(evidence["case-01.json"]),
                    sha256=hashlib.sha256(evidence["case-01.json"]).hexdigest())
            elif case in ("variant", "sequence", "manifest_index"):
                value["cases"][0][case] = MANIFEST["variants"][1] if case == "variant" else 2
            elif case == "unsafe-path":
                value["cases"][0]["evidence"]["path"] = "../raw/scenarios.txt"
            elif case == "missing-evidence":
                evidence.clear()
            elif case == "digest":
                value["cases"][0]["evidence"]["sha256"] = "0" * 64
            else:
                value["cases"][0]["raw_log"] = secret
            try:
                completed.validate(value, controller.completed_identity, evidence)
            except (ValueError, KeyError, TypeError):
                results["completed-" + case] = "refused"
            else:
                raise AssertionError("unsafe completed case accepted: " + case)
        try:
            completed.join_results(accepted, dict(controller.completed_identity,
                completed_cases=accepted, variants=controller.results))
        except ValueError:
            results["completed-prefix-only"] = "UNKNOWN; no complete original result/content proof"
        else:
            raise AssertionError("completion marker alone accepted as full results")
        # Continue the actual producer through all original manifest identities.
        # This checks the ledger bound/join, never the Product case assertions.
        controller.results = original_result
        for variant in MANIFEST["variants"][1:]:
            controller.mark(variant)
        full, _ = completed.read_bundle(root / "coord", controller.completed_identity)
        completed.join_results(full, dict(controller.completed_identity,
            completed_cases=full, variants=controller.results))
        assert len(full["cases"]) == 56
        results["completed-all56-consistency"] = "accepted metadata only; full content oracles remain required"
        try:
            completed.publish(root / "coord", "case-01.json", b"{}")
        except ValueError:
            results["completed-conflicting-publication"] = "refused"
        else:
            raise AssertionError("conflicting completed evidence overwritten")
        try:
            completed.decode(b'{"schema":1,"schema":1}')
        except ValueError:
            results["completed-duplicate-json-field"] = "refused"
        else:
            raise AssertionError("duplicate completed JSON field accepted")
        for name in ("case-02.json", "completed-cases.json"):
            target = root / "coord" / name
            if target.exists():
                target.unlink()  # Disposable synthetic fixture only.
            target.symlink_to(root / "outside")
            try:
                completed.publish(root / "coord", name, b"{}")
            except (OSError, ValueError):
                results["completed-symlink-" + name] = "refused"
            else:
                raise AssertionError("unsafe completed path accepted")
        assert not (root / "outside").exists()
    return results


def admission_fixture(event: str) -> dict:
    """Synthetic identity only; never an execution receipt."""
    return dict(event=event, cron="0 19 * * *" if event == "schedule" else None,
                repository="t-benze/happyranch",
                workflow_ref="t-benze/happyranch/.github/workflows/e2e.yml@refs/heads/main",
                workflow_sha="1" * 40, run_id="123", run_attempt="1",
                source_ref="refs/heads/main", source_sha="1" * 40, source_tree="2" * 40)


def admission_controls() -> dict:
    """Call only shipping admission/receipt predicates, with child launch forbidden.

    Actions-shaped payloads/env and git outputs below are isolated synthetic
    inputs, not authentic Actions evidence or permission to execute scenarios.
    """
    import tempfile
    from unittest.mock import patch

    results = {}
    with tempfile.TemporaryDirectory(prefix="e2e-admission-control-") as scratch:
        payload = Path(scratch) / "event.json"
        for event_name in ("schedule", "workflow_dispatch"):
            expected = admission_fixture(event_name)
            event = dict(repository=dict(full_name="t-benze/happyranch", default_branch="main"))
            if event_name == "schedule":
                event["schedule"] = "0 19 * * *"
            else:
                event.update(ref="refs/heads/main", inputs={})
            env = dict(GITHUB_ACTIONS="true", GITHUB_EVENT_NAME=event_name,
                       GITHUB_EVENT_PATH=str(payload), GITHUB_REPOSITORY="t-benze/happyranch",
                       GITHUB_REF="refs/heads/main", GITHUB_SHA="1" * 40,
                       GITHUB_WORKFLOW_SHA="1" * 40, GITHUB_RUN_ID="123", GITHUB_RUN_ATTEMPT="1",
                       GITHUB_WORKFLOW_REF="t-benze/happyranch/.github/workflows/e2e.yml@refs/heads/main")

            def admit(environment: dict, body: dict, head: str = "1" * 40, tree: str = "2" * 40) -> dict:
                payload.write_text(json.dumps(body))

                def git_read(command: list, **kwargs: object) -> str:
                    if kwargs != dict(cwd=Path(scratch), text=True):
                        raise AssertionError("unexpected git cwd/options")
                    if command == ["git", "rev-parse", "HEAD"]:
                        return head + "\n"
                    if command == ["git", "rev-parse", "HEAD^{tree}"]:
                        return tree + "\n"
                    raise AssertionError("non-admission command prohibited")

                with patch.dict(os.environ, environment, clear=True), \
                        patch("run.subprocess.check_output", side_effect=git_read), \
                        patch("subprocess.Popen", side_effect=AssertionError("child launch prohibited")):
                    return daily_admission(Path(scratch))

            if admit(env, event) != expected:
                raise AssertionError("wrong admitted event/run/source identity")
            results["admission-" + event_name] = "synthetic identity admitted; no child launch"
            cases = []
            for key in env:
                missing = dict(env)
                missing.pop(key)
                cases.append(("missing-" + key, missing, event, "1" * 40, "2" * 40))
            for key, value in (("GITHUB_ACTIONS", "false"), ("GITHUB_REF", "refs/heads/other"),
                               ("GITHUB_REPOSITORY", "other/repo"), ("GITHUB_SHA", "bad"),
                               ("GITHUB_WORKFLOW_SHA", "3" * 40), ("GITHUB_RUN_ID", "0"),
                               ("GITHUB_RUN_ATTEMPT", "-1"), ("GITHUB_RUN_ID", "１２３"),
                               ("GITHUB_WORKFLOW_REF", "t-benze/happyranch/.github/workflows/ci.yml@refs/heads/main")):
                cases.append(("wrong-" + key + "-" + value, dict(env, **{key: value}), event, "1" * 40, "2" * 40))
            for unsupported in ("pull_request", "push", "release", "workflow_call", "repository_dispatch", "unknown"):
                cases.append((unsupported, dict(env, GITHUB_EVENT_NAME=unsupported), event, "1" * 40, "2" * 40))
            cases.extend([("checkout-mismatch", env, event, "3" * 40, "2" * 40),
                          ("invalid-tree", env, event, "1" * 40, "bad"),
                          ("missing-repository", env, {}, "1" * 40, "2" * 40),
                          ("repository-mismatch", env, dict(event, repository=dict(full_name="other/repo", default_branch="main")), "1" * 40, "2" * 40),
                          ("default-mismatch", env, dict(event, repository=dict(full_name="t-benze/happyranch", default_branch="other")), "1" * 40, "2" * 40)])
            if event_name == "schedule":
                missing = dict(event)
                missing.pop("schedule")
                cases.extend([("missing-cron", env, missing, "1" * 40, "2" * 40),
                              ("wrong-cron", env, dict(event, schedule="0 7 * * *"), "1" * 40, "2" * 40)])
            else:
                missing = dict(event)
                missing.pop("ref")
                cases.extend([("missing-payload-ref", env, missing, "1" * 40, "2" * 40),
                              ("non-main-payload", env, dict(event, ref="refs/heads/other"), "1" * 40, "2" * 40),
                              ("invented-schedule", env, dict(event, schedule="0 19 * * *"), "1" * 40, "2" * 40)])
            for name, environment, body, head, tree in cases:
                try:
                    admit(environment, body, head, tree)
                except (RuntimeError, ValueError, KeyError, TypeError) as exc:
                    results["admission-" + event_name + "-" + name] = type(exc).__name__ + ": " + str(exc)
                else:
                    raise AssertionError("admission incorrectly accepted " + name)
            validate_admission(expected, expected)
            for key in expected:
                for missing in (False, True):
                    row = dict(expected)
                    if missing:
                        row.pop(key)
                    else:
                        row[key] = "conflicting-identity"
                    try:
                        validate_admission(row, expected)
                    except ValueError as exc:
                        results[f"receipt-{event_name}-{key}-{'missing' if missing else 'conflict'}"] = str(exc)
                    else:
                        raise AssertionError("receipt incorrectly accepted " + key)
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
    admission = admission_fixture("schedule")
    fixture.update(admission)
    fixture["launcher_result"].update(admission)
    validate_cancellation(fixture, source, "3.12", 15)
    results = {}
    for name in ("no-delivery", "wrong-signal", "false-success", "not-active", "missing-identity", "missing-unit", "live-cgroup", "missing-port", "data-residue", "setup-failure", "wrong-event", "wrong-run", "wrong-tree", "missing-admission"):
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
        elif name == "wrong-event": result["event"] = "workflow_dispatch"
        elif name == "wrong-run": result["run_id"] = "456"
        elif name == "wrong-tree": result["source_tree"] = "3" * 40
        elif name == "missing-admission": result.pop("cron")
        try:
            validate_cancellation(row, source, "3.12", 15)
        except ValueError as exc:
            results["cancellation-" + name] = str(exc)
        else:
            raise AssertionError(f"cancellation validator accepted {name}")
    manual = admission_fixture("workflow_dispatch")
    fixture.update(manual)
    fixture["launcher_result"].update(manual)
    validate_cancellation(fixture, source, "3.12", 15)
    results["cancellation-manual-identity"] = "synthetic wrapper/inner identity matched"
    return results


def validate_review_refusal(row: dict, observations: dict, directory: Path, record: dict, positive: dict) -> None:
    """Independently reconstruct the two refusals and original-SID continuation."""
    import hashlib

    def check(ok: bool, label: str) -> None:
        if not ok:
            raise ValueError("E04 refusal content: " + label)

    start, = record["starts"]
    task, refusal = record["task"], record["refusal"]
    check(start == observations["starts"][20] and start["task"] == task and start["org"] == "alpha"
          and start["agent"] == "case_manager" and start["pid_start"] is not None and record["jobs"] == [], "one actual alpha provider/zero jobs")
    commands = observations["provider_commands"][record["commands_begin"]:record["commands_end"]]
    check([cmd["action"] for cmd in commands] == ["jobs-refuse-review", "jobs-final"]
          and commands == [refusal["cli"], record["final_callback"]]
          and [cmd for cmd in observations["provider_commands"] if cmd["provider"]["session"] == start["session"]] == commands,
          "closed first-refusal/final-only protocol")
    command, final = commands
    check(not any(c.get("provider", {}).get("session") == start["session"] for c in observations["thread_commands"] + observations["e07_commands"]), "no mixed-mode commands")
    manifest = next(d["executable_manifest"] for d in observations["durable"] if "executable_manifest" in d)
    root = Path(manifest["root"])
    check(command["argv"][:6] == [manifest["callback"], "jobs", "submit", "--org", "alpha", "--from-file"]
          and len(command["argv"]) == 8 and command["argv"][-1] == "--json", "real shipping submit argv")
    file = command["payload_file"]
    raw = file["text"].encode("utf-8")
    payload = json.loads(raw)
    digest = hashlib.sha256(raw).hexdigest()
    check(file == dict(path=str(root/"coord"/(digest+".json")), text=raw.decode("utf-8"), bytes=len(raw), sha256=digest)
          and file["path"] == command["argv"][-2] and command["payload"] == payload
          and raw == json.dumps(payload,sort_keys=True).encode("utf-8"), "actual UTF8 payload file entity/path/hash")
    token = hashlib.sha256((row["source_sha"]+start["session"]+"REFUSED").encode()).hexdigest()[:24]
    sentinel, socket_path = str(root/"coord"/(token+".started")), str(root/"coord/jobs.sock")
    # The program is independently authored here; no scenario oracle is imported.
    program = f'''import json, os, signal, socket, sys, time
from pathlib import Path
Path({sentinel!r}).write_text(json.dumps(dict(token={token!r}, pid=os.getpid())))
sys.stdout.write('E04_REFUSED_OUT\\n'); sys.stdout.flush()
sys.stderr.write('E04_REFUSED_ERR\\n'); sys.stderr.flush()
end = {refusal["effective_end"]!r}
def remaining():
    value = end - time.monotonic()
    if value <= 0:
        raise TimeoutError("E04 script absolute deadline")
    return min(20, value)
with socket.socket(socket.AF_UNIX) as peer:
    peer.settimeout(remaining())
    peer.connect({socket_path!r})
    peer.settimeout(remaining())
    peer.sendall((json.dumps(dict(token={token!r}, pid=os.getpid())) + "\\n").encode())
    remaining()
    if False:
        while True:
            signal.pause()
    peer.settimeout(remaining())
    released = peer.recv(1)
    remaining()
    if released != b"R":
        raise RuntimeError("missing external release")
raise SystemExit(0)
'''
    check(record["entered"] < refusal["effective_end"] <= record["deadline"] and refusal["token"] == token and refusal["sentinel"] == sentinel and refusal["script"] == program
          and payload == dict(task_id=task, session_id=start["session"], title="E04 review refusal", rationale="Authorization mode removed",
              script=program, interpreter="python3", cwd_hint=None, review_required=True, persistent=False, max_runtime_seconds=30)
          and payload["review_required"] is True and payload["persistent"] is False and type(payload["max_runtime_seconds"]) is int,
          "independent valid sentinel-first bounded true payload")
    expected_stderr = (f"Error reading scripts-submit file {file['path']}: review_required_removed: obtain any required authorization in a thread before submitting; "
        "upgrade old clients and omit review_required or set it to false. This request was not submitted.\n")
    check(command["exit"] == 2 and command["stdout"] == "" and command["stderr"] == expected_stderr
          and command["timed_out"] is False and command["wire"] == [], "exact local refusal before HTTP")
    attempt, = [a for a in observations["provider_attempts"] if a["command_index"] == record["commands_begin"]]
    check(attempt["wire_begin"] == attempt["wire_end"] and "expected_exit" not in attempt["instruction"]
          and attempt["provider"] == start, "unchanged real relay interval/no arbitrary exit")
    check(all("expected_exit" not in a["instruction"] for a in observations["provider_attempts"]), "no caller-selected exceptional exit")
    for cmd in commands:
        check(cmd["provider"] == {k:start[k] for k in ("task","session","agent","org","pid")}
              and cmd["python"] == manifest["python"] and cmd["source_sha"] == row["source_sha"]
              and cmd["callback_sha256"] == manifest["callback_sha256"] and cmd["child_start"] is not None, "actual executable/PID/start/source binding")
        child_fields = cmd["child_stat"].rsplit(")",1)[1].split()
        check(int(cmd["child_stat"].split("(",1)[0]) == cmd["child_pid"] and child_fields[19] == cmd["child_start"]
              and int(child_fields[1]) == cmd["child_parent"] == start["pid"], "raw actual CLI child PID/start/parent")
        argv_hash = hashlib.sha256(json.dumps(cmd["argv"][1:]).encode()).hexdigest()
        witnesses = [w for w in observations["launch_witnesses"] if w["kind"] == "callback" and w["pid"] == cmd["child_pid"]]
        check(len(witnesses) == 1 and witnesses[0]["ppid"] == start["pid"] and witnesses[0]["argv_sha256"] == argv_hash
              and witnesses[0]["source_sha"] == row["source_sha"]
              and witnesses[0]["cli_sha256"] == hashlib.sha256((Path(__file__).resolve().parents[1]/"cli/main.py").read_bytes()).hexdigest(), "real intended CLI child witness")
    frames = refusal["frames"]
    check(len(frames) == 5 and refusal["closure"] == frames[3] and refusal["final_drain"] == frames[4], "complete before/after/closure/drain frames")
    before, after_cli, after_api = frames[:3]
    index = refusal["http_index"]
    http = observations["http"]
    api = http[index]
    check(type(index) is int and sum(h.get("capture") == "E04.review-required-refused" for h in http) == 1
          and api["index"] == index and api["capture"] == "E04.review-required-refused"
          and api["method"] == "POST" and api["path"] == "/api/v1/orgs/alpha/jobs/submit"
          and api["payload_file"] == file and api["request_text"] == raw.decode("utf-8")
          and api["request_bytes"] == len(raw) and api["request_sha256"] == digest and "error" not in api, "unique real identical-entity public POST")
    check(api["source_sha"] == row["source_sha"] and api["boot"] == observations["boots"][1]
          and api["daemon_pid"] == api["boot"]["pid"] and api["daemon_start"] == api["boot"]["start"] is not None
          and api["destination"]["host"] == "127.0.0.1" and api["destination"]["port"] in api["boot"]["discovered_ports"]
          and api["peer"] == ["127.0.0.1",api["destination"]["port"]], "actual loopback peer/daemon/boot source")
    response_raw = api["response_text"].encode("utf-8")
    check(api["status"] == 422 and api["response_bytes"] == len(response_raw)
          and api["response_sha256"] == hashlib.sha256(response_raw).hexdigest()
          and api["response"] == json.loads(response_raw) == {"detail":{"code":"review_required_removed","message":
              "Obtain any required authorization in a thread before submitting; omit review_required or set it to false. This request was not submitted."}}, "exact raw/parsed public refusal, no earlier guard")
    check(record["entered"] <= before["entered"] <= before["finished"] <= command["entered"] <= command["finished"]
          <= after_cli["entered"] <= after_cli["finished"] <= api["entered"] <= api["send"] <= api["response_at"] <= api["finished"]
          <= after_api["entered"] <= after_api["finished"] <= final["entered"] <= final["finished"] <= frames[3]["entered"]
          <= frames[3]["finished"] <= record["anchors"][-1]["entered"] < record["finished"] <= frames[4]["entered"], "monotonic real causal intervals")
    control_sentinel = positive["jobs"][0]["sentinel"]
    for i, frame in enumerate(frames):
        check(frame["coord_entries"] == sorted(set(frame["coord_entries"])) and Path(sentinel).name not in frame["coord_entries"]
              and Path(control_sentinel).name in frame["coord_entries"] and frame["socket"]["path"] == socket_path
              and type(frame["socket"]["fd"]) is int and frame["socket"]["fd"] >= 0 and frame["peer_ready"] == []
              and frame["sentinel_absent"] is True and frame["peer_pending"] is False
              and frame["provider_start"] == (start["pid_start"] if i < 3 else None), "sentinel/socket absence and live-then-gone original SID")
        for org in ("alpha","beta"):
            part, files = frame["sql"][org], frame["job_files"][org]
            check(frame["entered"] <= part["entered"] <= part["finished"] <= files["entered"] <= files["finished"] <= frame["finished"],
                  "coherent reopened SQL/file observation intervals, no claimed atomic files")
            content = part["content"]
            check({"tasks","task_results","audit_log","jobs","task_pause_controls"} <= set(content["tables"]), "complete relevant tables/control journal")
            check(all(r["review_required"] == 0 and r["status"] != "pending" for r in content["tables"]["jobs"]), "global no pending review row")
            check(not any(r["task_id"] == task for r in content["tables"]["jobs"]), "task no job allocation")
            for f in files["files"]:
                data = bytes.fromhex(f["hex"])
                check(f["bytes"] == len(data) and f["sha256"] == hashlib.sha256(data).hexdigest()
                      and dict(path="jobs/"+f["path"], bytes=len(data), sha256=f["sha256"]) in content["files"], "actual full job/output bytes and Store hashes")
            check(len(files["files"]) == sum(f["path"].startswith("jobs/") for f in content["files"]), "whole output file membership")
        procs = frame["processes"]
        check(frame["entered"] <= procs["entered"] <= procs["finished"] <= frame["finished"] and len(procs["rows"]) < 512
              and len({r["pid"] for r in procs["rows"]}) == len(procs["rows"]), "bounded actual descendant census")
        by_pid = {r["pid"]:r for r in procs["rows"]}
        check(api["daemon_pid"] in by_pid and by_pid[api["daemon_pid"]]["start"] == api["daemon_start"], "same daemon process")
        for r in procs["rows"]:
            fields = r["stat"].rsplit(")",1)[1].split()
            check(int(r["stat"].split("(",1)[0]) == r["pid"] and fields[19] == r["start"] and int(fields[1]) == r["ppid"]
                  and r["root_marker"] == f"E2E_ROOT={root}", "raw owned process identity")
            check(r["start"] is not None and all(child in by_pid and by_pid[child]["ppid"] == r["pid"] for child in r["children"]), "closed known descendant ownership")
        check((start["pid"] in by_pid) == (i < 3) and not any(cmd["child_pid"] in by_pid for cmd in commands), "intended CLI children separately accounted and gone")
    def unchanged(a: dict, b: dict) -> None:
        for org in ("alpha","beta"):
            left, right = a["sql"][org]["content"], b["sql"][org]["content"]
            check(left.keys() == right.keys() and left["files"] == right["files"] and left["tables"].keys() == right["tables"].keys()
                  and a["job_files"][org]["files"] == b["job_files"][org]["files"], "no product file or table membership change")
            for table, old_rows in left["tables"].items():
                new_rows = right["tables"][table]
                check(len(old_rows) == len(new_rows), "no row allocation " + table)
                for old,new in zip(old_rows,new_rows):
                    if org == "alpha" and table == "tasks" and old["id"] == task:
                        check(old.keys() == new.keys() and all(old[k] == new[k] for k in old if k not in {"last_heartbeat","updated_at"}), "exact current task unchanged")
                        pairs = [[datetime.fromisoformat(r[k]) for k in ("last_heartbeat","updated_at")] for r in (old,new)]
                        check(all(v.tzinfo is not None and v.utcoffset() is not None for pair in pairs for v in pair)
                              and all(pair[1] >= pair[0] for pair in pairs), "initialized valid aware heartbeat pairs")
                        check((all(old[k] == new[k] for k in ("last_heartbeat","updated_at")))
                              or all(y > x for x,y in zip(*pairs)), "only BOTH timestamps strictly forward")
                    else:
                        check(old == new, "no refusal SQL/audit/control/wait/result side effect " + table)
        keys = ("pid","start","ppid","children","root_marker")
        check([{k:r[k] for k in keys} for r in a["processes"]["rows"]] ==
              [{k:r[k] for k in keys} for r in b["processes"]["rows"]], "no refused descendant launch")
    unchanged(before,after_cli)
    unchanged(after_cli,after_api)
    baseline_tables = before["sql"]["alpha"]["content"]["tables"]
    baseline_task, = [t for t in baseline_tables["tasks"] if t["id"] == task]
    check(baseline_task["status"] == "in_progress" and baseline_task["current_session_id"] == start["session"]
          and baseline_task["assigned_agent"] == "case_manager" and baseline_task["orchestration_step_count"] == 1
          and baseline_task["parent_task_id"] is None and all(baseline_task[k] is None for k in ("block_kind","blocked_on_job_ids","active_chain","active_fanout"))
          and not any(r["task_id"] == task for r in baseline_tables["task_results"]), "valid initialized live ordinary task before refusal")
    check(refusal["positive_case"] == 0 and positive["index"] == 0 and positive["finished"] <= record["entered"]
          and len(positive["jobs"]) == 1, "immediately preceding genuine automatic positive")
    control = positive["jobs"][0]
    check(refusal["positive_job"] == control["id"] and refusal["positive_peer"] == control["peer"]
          and refusal["positive_oracle"] == control["oracle"] and json.loads(refusal["positive_sentinel"]) == control["peer"]["hello"]
          == {"token":control["token"],"pid":control["peer"]["pid"]}
          and control["peer"]["start"] is not None and control["code"] == control["oracle"]["row"]["exit_code"] == 0
          and control["oracle"]["outputs"][0]["text"] == "E04_OK_OUT\n"
          and [a["action"] for a in control["oracle"]["audits"]] == ["job_submitted","job_auto_started","job_run_completed"], "positive script/sentinel/socket/PID/output/audit control")
    summary = "E04_REVIEW_REQUIRED_REFUSED no job submitted"
    done = dict(action="done",summary=summary)
    expected_callback = dict(task_id=task,session_id=start["session"],agent="case_manager",status="completed",confidence=90,summary=summary,decision=done)
    check(final["payload"] == expected_callback and final["exit"] == 0 and not final["timed_out"]
          and final["argv"] == [manifest["callback"],"report-completion","--org","alpha","--from-file",final["payload_file"]["path"]], "genuine last final CLI callback")
    final_wire = [w for w in final["wire"] if w["method"] == "POST" and w["path"] == f"/api/v1/orgs/alpha/tasks/{task}/completion"]
    check(len(final_wire) == 1 and final_wire[0]["status"] == 200 and final_wire[0]["response"] == {"ok":True}
          and final_wire[0]["dropped"] is False and final_wire[0] in observations["relay"], "actual accepted callback200")
    snap = record["final_snapshot"]
    task_row, = snap["tasks"]
    result, = snap["results"]
    check(task_row["id"] == task and task_row["status"] == "completed" and task_row["completed_at"] is not None
          and task_row["parent_task_id"] is None and task_row["assigned_agent"] == "case_manager"
          and task_row["current_session_id"] == start["session"] and task_row["orchestration_step_count"] == 1
          and all(task_row[k] is None for k in ("block_kind","blocked_on_job_ids","active_chain","active_fanout")) and snap["jobs"] == [], "one-generation final no-wait task")
    check(type(result["id"]) is int and result["id"] > 0 and result["session_id"] == start["session"] and result["task_id"] == task
          and result["agent"] == "case_manager" and result["status"] == "completed" and type(result["confidence_score"]) is int
          and result["confidence_score"] == 90 and result["output_summary"] == summary and result["verdict"] is None
          and result["waiting_on_job_ids"] is None and json.loads(result["decision_json"]) == done, "actual positive INTEGER single truthful result")
    kinds = ("session_start","session_end","completion_report","orchestration_step","task_blocked_on_jobs","task_resumed_from_jobs")
    actions = {kind:[a for a in snap["audit"] if a["action"] == kind] for kind in kinds}
    check([len(actions[k]) for k in kinds] == [1,1,1,1,0,0] and len({a["id"] for a in snap["audit"]}) == len(snap["audit"])
          and all(type(a["id"]) is int and a["id"] > 0 for a in snap["audit"]), "one complete lifecycle/no park/wake")
    a,b,d,step = [actions[k][0] for k in kinds[:4]]
    consumed = json.loads(d["payload"])
    expanded = dict(done,then=[],children=[],**{k:None for k in ("agent","prompt","expect_verdict","width_cap_ack","revisit_of_task_id","attachments","join_summary","reason","successor_brief","rationale","attestation")})
    check(a["id"] < b["id"] < d["id"] < step["id"] and all(x["task_id"] == task for x in (a,b,d,step))
          and all(x["agent"] == "case_manager" for x in (a,b,d)) and step["agent"] == "orchestrator"
          and json.loads(a["payload"])["session_id"] == start["session"] and consumed["task_id"] == task and consumed["agent"] == "case_manager"
          and consumed["status"] == "completed" and type(consumed["confidence"]) is int and consumed["confidence"] == 90
          and consumed["output_summary"] == summary and consumed["verdict"] is None and consumed["waiting_on_job_ids"] == []
          and consumed["decision"] == expanded and json.loads(step["payload"]) == dict(step_number=1,decision=dict(done,then=[],children=[])), "ordered full callback consumption and step1")
    final_by_id = {a["id"]:a for a in snap["audit"]}
    check(all(final_by_id[a["id"]] == a for a in baseline_tables["audit_log"] if a["task_id"] == task), "all original lifecycle bytes retained")
    check([r for r in frames[3]["sql"]["alpha"]["content"]["tables"]["task_results"] if r["task_id"] == task] == [result], "closure retains same result")
    rendered = record["task_browser"]
    check(all(t in rendered["text"] for t in (summary,"RECALL TREE","OUTCOME","completed"))
          and rendered["screenshot"] == "e04-task-1.png" and (directory/"e04-task-1.png").read_bytes().startswith(b"\x89PNG\r\n\x1a\n"), "actual final task/list/Recall screenshot")


def validate_e04(row: dict, observations: dict, browser: list[dict], directory: Path) -> None:
    """Recompute actual artifact content independently of the scenario driver.

    This is a daily artifact oracle, not an execution entrypoint or merge test.
    The metadata-only controls above do not establish this oracle's sensitivity.
    """
    import hashlib
    from collections import Counter
    from urllib.parse import parse_qs, urlsplit

    def check(condition: bool, message: str) -> None:
        if not condition:
            raise ValueError("E04 content: " + message)

    check(observations["variants"] == row["variants"] and observations["observer_error"] is None,
          "scenario/observation disagreement or observer failure")
    starts, commands = observations["starts"], observations["provider_commands"]
    durable, observer = observations["durable"], observations["provider_observer"]
    check(len(starts) == 38 and len({s["session"] for s in starts[:29]}) == 29, "retained29 genuine prefix providers;38 C composition")
    check(len(observer) >= 2 and all(0 < b["at"] - a["at"] <= 1 for a, b in zip(observer, observer[1:])),
          "continuous provider observer interval")
    check(all(a["stubs"] <= b["stubs"] and a["calls"] <= b["calls"] for a, b in zip(observer, observer[1:])),
          "observer counter rollback")
    check(observer[-1]["stubs"] == 38 and not observer[-1]["pending"], "observer composed final closure")
    http = observations["http"]
    anchors = [r["jobs_anchor"] for r in durable if "jobs_anchor" in r and not r["jobs_anchor"]["name"].startswith("E05:")]
    check(len(anchors) == 27 and len({a["name"] for a in anchors}) == 27, "complete unique boot/case/drain anchors")
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
                          ("jobs_final_org", {"alpha": (19, 28), "beta": (1, 1)})):
        records = [d for d in durable if key in d]
        check(len(records) == 2, "retained full original and final org snapshots")
        for d in records:
            org = d["org"] if key == "final_snapshot" else d[key]
            snap = d[key] if key == "final_snapshot" else d["snapshot"]
            check(org in expected and (len(snap["tasks"]), len(snap["results"])) == expected[org]
                  and all(t["status"] == "completed" for t in snap["tasks"]), "literal org task/result closure")
    case_rows = [d["jobs_cases"] for d in durable if "jobs_cases" in d]
    check(len(case_rows) == 1 and len(case_rows[0]) == 6, "six actual case records")
    expected_variants = ["E04.approve-exit0", "E04.review-required-refused", "E04.completed-exit7", "E04.failed-timeout",
                         "E04.two-jobs-A-first", "E04.two-jobs-B-first"]
    expected_codes = [[0], [], [7], [-9], [0, 7], [7, 0]]
    all_jobs, peer_count, previous = [], 0, pause
    for index, record in enumerate(case_rows[0]):
        variant = expected_variants[index]
        check(record["variant"] == variant and record["index"] == index and record["source_sha"] == row["source_sha"]
              and record["method"] == "serial-public-metrics-window-causal-v1"
              and row["variants"][variant]["evidence"]["host_cleanup_attribution"] == record, "actual variant evidence reference")
        check(record["entered"] <= record["finished"] < record["deadline"]
              and record["deadline"] <= record["entered"] + [20, 15, 20, 25, 25, 25][index], "case bound")
        begin, end = ((18,20),(20,21),(21,23),(23,25),(25,27),(27,29))[index]
        check(record["starts"] == starts[begin:end], "actual sequential provider references")
        if index == 1:
            checkpoints = record["anchors"]
            check([a["name"] for a in checkpoints] == [variant+":pre",variant+":final"]
                  and all(by_name[a["name"]] == a for a in checkpoints), "refusal two settled anchors")
            anchor_content(checkpoints[0], 8, 20, previous)
            anchor_content(checkpoints[1], 9, 21, checkpoints[0])
            validate_review_refusal(row, observations, directory, record, case_rows[0][0])
            previous = checkpoints[1]
            continue
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
            count = begin + (0 if i == 0 else 2 if i == len(checkpoints) - 1 else 1)
            anchor_content(a, count - 12, count, previous)
            previous = a
        jobs = record["jobs"]
        ids = sorted(j["id"] for j in jobs)
        check([j["code"] for j in jobs] == expected_codes[index], "literal case set/order")
        block_payload = dict(record["block"]["payload"])
        for optional in ("verdict", "decision"):
            if optional in block_payload:
                check(block_payload.pop(optional) is None, "declared original block null default")
        check(block_payload == dict(task_id=task, session_id=first["session"], agent="case_manager", status="blocked",
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
              and live_task["assigned_agent"] == "case_manager"
              and all(live_task[k] is None for k in ("block_kind", "active_chain", "active_fanout", "parent_task_id"))
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
            check(raw["reviewed_by"] == ("founder" if code is None else "case_manager") and raw["reviewed_at"] is not None and raw["max_runtime_seconds"] == (5 if code == -9 else 30)
                  and raw["review_required"] == (1 if code is None else 0) and raw["persistent"] == 0 and raw["script_text"] == job["script"]
                  and hashlib.sha256(job["script"].encode()).hexdigest() == job["script_sha256"], "submitted script/cap/review join")
            submitted = json.loads(job["submit"]["stdout"])
            check(submitted["id"] == job["id"] and submitted["status"] == ("pending" if code is None else "running")
                  and submitted["authentication"] == dict(task_id=task, session_id=first["session"])
                  and job["submit"]["payload"]["script"] == raw["script_text"]
                  and job["submit"]["payload"]["review_required"] is (code is None), "actual returned ID and provider submit")
            submit_wire = [w for w in job["submit"]["wire"] if w["method"] == "POST" and w["path"] == "/api/v1/orgs/alpha/jobs/submit"]
            check(len(submit_wire) == 1 and submit_wire[0]["status"] == 201 and submit_wire[0]["response"] == submitted, "real submit201")
            check(len(job["pending"]) == (4 if index >= 4 and job is jobs[1] else 3) and job["pending"][0]["at"] < checkpoints[1]["entered"]
                  and job["pending"][1]["at"] >= checkpoints[1]["finished"], "pending observations at submit/park/dialog")
            check(job["scenario_deadline"] == next(p["deadline"] for p in row["phases"] if p["name"] == "scenarios")
                  and job["effective_end"] <= job["scenario_deadline"]
                  and job["effective_end"] == record["deadline"]
                  and ("end = " + repr(job["effective_end"])) in job["script"], "actual generated job absolute end")
            for pending in job["pending"]:
                if code is None:
                    check(pending["row"]["status"] == "pending" and pending["sentinel_absent"] is True and pending["peer_pending"] is False
                          and len(pending["files_absent"]) == 2 and all(pending["files_absent"].values())
                          and [a["action"] for a in pending["audit"]] == ["job_submitted"]
                          and all(pending["row"][k] is None for k in ("started_at", "finished_at", "reviewed_at", "exit_code", "stdout_path", "stderr_path")),
                          "historical pending no launch/file/audit evidence")
                else:
                    check(pending["row"]["status"] == "running" and pending["sentinel_absent"] is False
                          and pending["peer_pending"] is False and len(pending["files_absent"]) == 2
                          and not any(pending["files_absent"].values())
                          and pending["row"]["started_at"] and pending["row"]["reviewed_at"]
                          and pending["row"]["reviewed_by"] == "case_manager"
                          and all(pending["row"][k] is None for k in ("finished_at", "exit_code", "reason"))
                          and [a["action"] for a in pending["audit"]] == ["job_submitted", "job_auto_started"]
                          and all(a["agent"] == "case_manager" for a in pending["audit"]),
                          "automatic running witness and genuine agent start, not success")
            action = job["action"]
            check(action["at"] > checkpoints[1]["finished"] and action["no_run_controls"] is True
                  and action["method"] == ("POST" if code is None else "GET")
                  and action["path"] == f"/api/v1/orgs/alpha/jobs/{job['id']}" + ("/reject" if code is None else "")
                  and action["request"] == ({"reason": "E04_REJECTED_BY_REVIEW"} if code is None else None)
                  and action["status"] == 200 and action["response"]["id"] == job["id"]
                  and action["response"]["status"] == ("rejected" if code is None else "running")
                  and dict(event="job-action", **action) in browser, "actual browser no-run observation or historical individual rejection")
            if code is not None:
                check(any(e.get("event") == "job-read" and e["at"] <= action["at"]
                          and all(e[key] == action[key] for key in ("path", "method", "status", "response"))
                          for e in browser), "automatic UI observation joins the actual native read")
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
            check([a["action"] for a in audits] == (["job_submitted", terminal] if code is None else ["job_submitted", "job_auto_started", terminal])
                  and audits[0]["agent"] == "case_manager" and all(a["agent"] == ("founder" if code is None else "case_manager") for a in audits[1:])
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
        # Independent reconstruction from serialized frames and original
        # commands. Never import the maker oracle or trust its validation flag.
        summary = ["E04_OK completed exit0", "E04_REVIEW_REQUIRED_REFUSED no job submitted",
                   "E04_EXIT7 completed exit7; operation nonzero", "E04_TIMEOUT failed timeout exit-9",
                   "E04_PAIR A completed exit0; B completed exit7", "E04_PAIR A completed exit0; B completed exit7"][index]
        compact_done = {"action": "done", "summary": summary}
        semantic_done = {"action": "done", "summary": summary, "then": [], "children": [],
                         "agent": None, "prompt": None, "expect_verdict": None, "width_cap_ack": None,
                         "revisit_of_task_id": None, "attachments": None, "join_summary": None,
                         "reason": None, "successor_brief": None, "rationale": None, "attestation": None}
        check(all(isinstance(s["session"], str) and s["session"] for s in (first, second))
              and first["session"] != second["session"], "two distinct genuine provider SIDs")

        def completion_content(result: dict, start: dict, a: dict, b: dict, d: dict,
                               command: dict, generation: int) -> None:
            callback = command["payload"]
            semantic = json.loads(d["payload"])
            expected_status = "blocked" if generation == 0 else "completed"
            expected_summary = "waiting for genuine IDs" if generation == 0 else summary
            check(type(result["id"]) is int and result["id"] > 0
                  and result["session_id"] == start["session"] == callback["session_id"]
                  == json.loads(a["payload"])["session_id"], "genuine result/start/callback SID")
            check(a["task_id"] == b["task_id"] == d["task_id"] == result["task_id"]
                  == semantic["task_id"] == callback["task_id"] == start["task"] == task
                  and a["agent"] == b["agent"] == d["agent"] == result["agent"]
                  == semantic["agent"] == callback["agent"] == start["agent"] == "case_manager"
                  and a["id"] < b["id"] < d["id"], "complete ordered task/agent generation slot")
            check(semantic["status"] == result["status"] == callback["status"] == expected_status
                  and type(semantic["confidence"]) is int and type(result["confidence_score"]) is int
                  and type(callback["confidence"]) is int
                  and semantic["confidence"] == result["confidence_score"] == callback["confidence"] == 90
                  and semantic["output_summary"] == result["output_summary"] == callback["summary"] == expected_summary
                  and semantic["verdict"] is None and result["verdict"] is None,
                  "original accepted result vs every emitted completion semantic field")
            accepted = None if result["decision_json"] is None else json.loads(result["decision_json"])
            check(accepted == (None if generation == 0 else compact_done)
                  and semantic["decision"] == (None if generation == 0 else semantic_done)
                  and semantic["waiting_on_job_ids"] == ([j["id"] for j in jobs] if generation == 0 else []),
                  "closed actual NextStep defaults and original waiting list")
            check((json.loads(result["waiting_on_job_ids"]) == ids if generation == 0 else result["waiting_on_job_ids"] is None),
                  "accepted S1 sorted durable waits and S2 NULL")

        # Original public command bodies/actors/paths, including only declared
        # optional nulls. These are source witnesses, not a maker success flag.
        for generation, (start, command) in enumerate(((first, record["block"]), (second, record["final_callback"]))):
            expected_callback = dict(task_id=task, session_id=start["session"], agent="case_manager",
                                     status="blocked" if generation == 0 else "completed", confidence=90,
                                     summary="waiting for genuine IDs" if generation == 0 else summary)
            if generation == 0:
                expected_callback["waiting_on_job_ids"] = [j["id"] for j in jobs]
            else:
                expected_callback["decision"] = compact_done
            supplied = dict(command["payload"])
            if "verdict" in supplied:
                check(supplied.pop("verdict") is None, "original null callback verdict")
            if generation == 0 and "decision" in supplied:
                check(supplied.pop("decision") is None, "original null blocked decision")
            check(supplied == expected_callback and type(command["payload"]["confidence"]) is int
                  and command in commands and command["source_sha"] == row["source_sha"]
                  and command["provider"] == {k: start[k] for k in ("task", "session", "agent", "org", "pid")}
                  and command["action"] == ("jobs-block" if generation == 0 else "jobs-final")
                  and len(command["argv"]) == 6
                  and command["argv"][1:5] == ["report-completion", "--org", "alpha", "--from-file"] and Path(command["argv"][5]).is_absolute(), "original actual callback command identity")
            wire = [w for w in command["wire"] if w["method"] == "POST"
                    and w["path"] == f"/api/v1/orgs/alpha/tasks/{task}/completion"]
            check(len(wire) == 1 and wire[0]["status"] == 200 and wire[0]["response"] == {"ok": True}
                  and wire[0]["dropped"] is False, "genuine shipping accepted completion200")

        snap = record["final_snapshot"]
        # Original history is joined by unique INTEGER identity and full row,
        # including raw payload bytes. A duplicate cannot stand in for a row.
        for frame in (record["parked"], live_snapshot, snap):
            check(all(type(a["id"]) is int and a["id"] > 0 for a in frame["audit"])
                  and len({a["id"] for a in frame["audit"]}) == len(frame["audit"]), "unique positive audit identities")
        parked_audits = {a["id"]: a for a in record["parked"]["audit"]}
        live_audits = {a["id"]: a for a in live_snapshot["audit"]}
        final_audits = {a["id"]: a for a in snap["audit"]}
        check(all(identity in live_audits and live_audits[identity] == original for identity, original in parked_audits.items())
              and all(identity in final_audits and final_audits[identity] == original for identity, original in live_audits.items()),
              "all original parked/live audit rows and payload bytes retained")

        # J1.b: real live S2, before its reads/callback, with S1 consumed once.
        live_actions = {k: [a for a in live_snapshot["audit"] if a["action"] == k] for k in
                        ("session_start", "session_end", "completion_report", "task_blocked_on_jobs", "task_resumed_from_jobs", "orchestration_step")}
        check([len(live_actions[k]) for k in live_actions] == [2, 1, 1, 1, 1, 0], "live exact lifecycle, no S2 end/consume/done")
        check([json.loads(a["payload"])["session_id"] for a in live_actions["session_start"]]
              == [first["session"], second["session"]]
              and all(a["task_id"] == task and a["agent"] == "case_manager" for a in live_actions["session_start"]),
              "live two actual task/agent start bindings")
        completion_content(blocked, first, live_actions["session_start"][0], live_actions["session_end"][0],
                           live_actions["completion_report"][0], record["block"], 0)
        live_block, live_wake = live_actions["task_blocked_on_jobs"][0], live_actions["task_resumed_from_jobs"][0]
        check(live_block["task_id"] == live_wake["task_id"] == task and live_block["agent"] == "case_manager"
              and live_wake["agent"] == "orchestrator"
              and live_actions["completion_report"][0]["id"] < live_block["id"] < live_wake["id"] < live_actions["session_start"][1]["id"],
              "natural live S1 consume/park/wake/S2 start order")
        check(json.loads(live_wake["payload"]) == dict(blocking_job_ids=ids, trigger="job_terminal", triggering_job_id=jobs[-1]["id"],
              job_outcomes={j["id"]: j["oracle"]["row"]["status"] for j in jobs}), "independent live all-terminal wake map")

        # J1.c: complete final generations reconstructed independently of J1.a.
        check(len(snap["tasks"]) == 1 and len(snap["results"]) == 2
              and all(type(r["id"]) is int and r["id"] > 0 for r in snap["results"])
              and len({r["id"] for r in snap["results"]}) == 2
              and snap["results"][0]["id"] < snap["results"][1]["id"]
              and [r["status"] for r in snap["results"]] == ["blocked", "completed"]
              and [r["session_id"] for r in snap["results"]] == [first["session"], second["session"]]
              and snap["results"][:1] == live_snapshot["results"] == record["parked"]["results"], "distinct final results and unchanged complete S1")
        task_row = snap["tasks"][0]
        check(task_row["id"] == task and task_row["assigned_agent"] == "case_manager"
              and task_row["status"] == "completed" and task_row["completed_at"] is not None and task_row["parent_task_id"] is None
              and task_row["orchestration_step_count"] == 2 and task_row["current_session_id"] == second["session"]
              and all(task_row[k] is None for k in ("block_kind", "active_chain", "active_fanout"))
              and json.loads(task_row["blocked_on_job_ids"]) == ids, "terminal task/historical IDs")
        actions = {k: [a for a in snap["audit"] if a["action"] == k] for k in
                   ("session_start", "session_end", "completion_report", "task_blocked_on_jobs", "task_resumed_from_jobs", "orchestration_step")}
        check([len(actions[k]) for k in actions] == [2, 2, 2, 1, 1, 1], "complete final lifecycle cardinalities")
        for generation, start in enumerate((first, second)):
            a, b, d = (actions[k][generation] for k in ("session_start", "session_end", "completion_report"))
            completion_content(snap["results"][generation], start, a, b, d,
                               record["block"] if generation == 0 else record["final_callback"], generation)
        block, wake = actions["task_blocked_on_jobs"][0], actions["task_resumed_from_jobs"][0]
        check(block["task_id"] == wake["task_id"] == task and block["agent"] == "case_manager"
              and wake["agent"] == "orchestrator"
              and actions["completion_report"][0]["id"] < block["id"] < wake["id"] < actions["session_start"][1]["id"], "park before natural wake")
        wp = json.loads(wake["payload"])
        check(wp == dict(blocking_job_ids=ids, trigger="job_terminal", triggering_job_id=jobs[-1]["id"],
              job_outcomes={j["id"]: j["oracle"]["row"]["status"] for j in jobs}), "independent final all-terminal wake map")
        done_audit = actions["orchestration_step"][0]
        check(done_audit["task_id"] == task and done_audit["agent"] == "orchestrator"
              and done_audit["id"] > actions["completion_report"][1]["id"]
              and json.loads(done_audit["payload"]) == dict(step_number=2, decision=dict(compact_done, then=[], children=[])),
              "actual orchestrator final done with exclude_none defaults")
        if index >= 4:
            early = record["first_terminal"]
            check(len(early["jobs"]) == 2 and len(set(ids)) == 2
                  and sorted(j["id"] for j in early["jobs"]) == ids
                  and all(j["task_id"] == task and j["agent_name"] == "case_manager" for j in early["jobs"]),
                  "pair first-terminal exact submitted job identities/owner")
            early_jobs = {j["id"]: j for j in early["jobs"]}
            completed, running = (early_jobs[j["id"]] for j in jobs)
            check(completed == jobs[0]["oracle"]["row"]
                  and type(completed["exit_code"]) is int
                  and (completed["status"], completed["exit_code"], completed["reason"])
                  == ("completed", expected_codes[index][0], None)
                  and running == jobs[1]["pending"][-1]["row"]
                  and running["status"] == "running"
                  and all(running[k] is None for k in ("finished_at", "exit_code", "reason", "duration_ms")),
                  "pair authored first outcome and genuinely running second job")
            check(len(early["tasks"]) == 1 and len(early["results"]) == 1
                  and early["results"] == record["parked"]["results"]
                  and early["tasks"][0]["id"] == task
                  and early["tasks"][0]["assigned_agent"] == "case_manager"
                  and early["tasks"][0]["status"] == "in_progress"
                  and early["tasks"][0]["current_session_id"] == first["session"]
                  and early["tasks"][0]["orchestration_step_count"] == 1
                  and early["tasks"][0]["block_kind"] == "blocked_on_job"
                  and json.loads(early["tasks"][0]["blocked_on_job_ids"]) == ids
                  and not any(a["action"] == "task_resumed_from_jobs" for a in early["audit"])
                  and jobs[1]["action"]["at"] > checkpoints[-2]["finished"], "pair +0 no-early-resume barrier")
        check(snap["results"][1]["output_summary"] == summary, "independent truthful final summary")
        rendered = record["task_browser"]
        check(all(t in rendered["text"] for t in (summary, "RECALL TREE", "OUTCOME", "completed"))
              and rendered["screenshot"] == f"e04-task-{index}.png" and (directory / rendered["screenshot"]).is_file(), "rendered final task/Recall")
        own = commands[record["commands_begin"]:record["commands_end"]]
        check(own and own[-1] == record["final_callback"] and own[-1]["action"] == "jobs-final"
              and own[-1]["payload"]["task_id"] == task and own[-1]["payload"]["session_id"] == second["session"]
              and "waiting_on_job_ids" not in own[-1]["payload"]
              and own[-1]["payload"]["summary"] == snap["results"][1]["output_summary"]
              and json.loads(snap["results"][1]["decision_json"]) == own[-1]["payload"]["decision"], "last real final callback")
        check([x["action"] for x in own if x["provider"]["session"] == first["session"]] == [*["jobs-submit"] * len(jobs), "jobs-block"], "S1 final CLI action")
        check([x["action"] for x in own if x["provider"]["session"] == second["session"]] == [*["jobs-read"] * (5 * len(jobs)), "jobs-final"], "S2 reads before final callback")
    check(len(all_jobs) == len(set(all_jobs)) == 7 and peer_count == 7, "seven jobs/seven scripts")
    anchor_content(by_name["final-drain-B"], 17, 29, previous)
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
    check(len(commands) == 54 and Counter(c["action"] for c in commands) ==
          {"jobs-submit":7,"jobs-refuse-review":1,"jobs-block":5,"jobs-final":6,"jobs-read":35}, "exact54 provider command inventory")
    calls = [w for w in final_witness[0] if w["kind"] == "callback"]
    stubs = [w for w in final_witness[0] if w["kind"] == "stub"]
    check(len(calls) == len(prefix_commands) + len(commands) and len(stubs) == 29
          and all(w["source_sha"] == row["source_sha"] for w in calls + stubs), "all authentic source-bound executable witnesses")
    for command in commands:
        refused = command["action"] == "jobs-refuse-review"
        check(command["exit"] == (2 if refused else 0) and command["timed_out"] is False and command["source_sha"] == row["source_sha"]
              and command["entered"] <= command["finished"]
              and (command["wire"] == [] if refused else bool(command["wire"])) and all(w in observations["relay"] and w["dropped"] is False for w in command["wire"]), "actual CLI/wire record")
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
    check(len(starts) == 38 and len({s["session"] for s in starts[:36]}) == 36, "36 unique prefix sessions;38 C composition")
    providers = C["providers"] + N["providers"] + D["providers"]
    check(len(providers) == 6 and len({p["token_sha256"] for p in providers}) == 6
          and {s["session"] for s in starts[29:36]} == {p["session"] for p in providers + [D["task"]]}, "six genuine thread tokens and one task")
    check(all(p in starts and p["org"] == "alpha" and p["pid_start"] for p in providers), "actual thread socket identities")
    anchors = [d["jobs_anchor"] for d in durable if "jobs_anchor" in d]
    previous = next(a for a in anchors if a["name"] == "final-drain-B")
    check(len([a for a in anchors if a["name"].startswith("E05:")]) == 4, "four E05 group/drain boundaries")
    for group, total, count, allowance in zip(groups, (20,21,24), (32,33,36), (18,8,25)):
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
          and final_anchor["provider_count"] == 36 and final_anchor["entered"] >= previous["finished"], "unchanged final drain")
    check(len(final_anchor["samples"]) >= 2 and all(s["metrics"]["host_sessions"]["receipts"] == previous["projection"]["receipts"]
              for s in final_anchor["samples"][-2:]), "actual final rereads")
    check(Counter(x["invocation_kind"] for x in final_anchor["projection"]["receipts"]["recent"]) == {"task":18,"thread":6}, "B24 canonical task/thread mix")

    final = case["final"]["content"]
    expected_sources = ("e2e/threads.py", "e2e/thread_provider.py", "e2e/provider.py", "e2e/controller.py", "e2e/browser.py",
                        "e2e/transport.py", "e2e/oracles.py", "e2e/aggregate.py", "tests/integration/fake_claude.sh", "cli/commands/threads.py",
                        "runtime/daemon/routes/threads.py", "runtime/daemon/thread_runner.py", "web/src/shared/threads/NewThreadDialog.tsx", "cli/_shared.py", "cli/commands/tasks.py",
         "runtime/daemon/routes/tasks.py", "web/src/features/threads/ThreadsPage.tsx",
         "web/src/design-system/patterns/MessageBubble.tsx", "web/src/design-system/patterns/Markdown.tsx",
         "web/src/design-system/patterns/AgentChip.tsx")
    source_root = Path(__file__).resolve().parents[1]
    check(case["source_hashes"] == {name:hashlib.sha256((source_root/name).read_bytes()).hexdigest() for name in expected_sources}, "actual immutable producer/consumer source bytes")
    beta = [d["threads_beta"] for d in durable if "threads_beta" in d]
    check(len(beta) == 1 and not any(beta[0][k] for k in beta[0] if k.startswith("thread") or k in {"jobs","files"}), "beta has no thread/job/file effects")
    check(len(final["jobs"]) == 7 and {j["id"] for j in final["jobs"]} == {
        j["id"] for d in durable if "jobs_cases" in d for r in d["jobs_cases"] for j in r["jobs"]}, "unchanged seven prior jobs")
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
    boundary = observations["e07"]["prefix"]
    witnesses = observations["launch_witnesses"][:boundary["witness_records"]]
    check(case["witnesses"] == witnesses and len([w for w in witnesses if w["kind"] == "stub"]) == 36, "complete actual executable witnesses")
    calls = [w for w in witnesses if w["kind"] == "callback"]
    check(len(calls) == boundary["command_count"]+len(observations["provider_commands"])+len(commands), "every CLI accounted")
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
    for barrier, admitted, released, command_start in ((C["worker_closed"],19,18,C["reply"]["entered"]),
                                                      (D["dispatcher_closed"],23,22,D["denial"]["command"]["entered"])):
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
        check(all(type(cmd[k + '_bytes']) is int and cmd[k + '_bytes'] >= 0
                  and cmd[k + '_truncated'] == (cmd[k + '_bytes'] > 16384)
                  and len(cmd[k].encode()) <= 16384 for k in ('stdout','stderr'))
              and cmd['output_truncated'] == (cmd['stdout_truncated'] or cmd['stderr_truncated']),
              'R3 original byte counts and per-stream truncation diagnostics')
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
    # Fixed case receipts must be the actual eleven ledger entries, each joined
    # to its original finite instruction. Durable acceptance alone is not CLI success.
    specifications = [
        (C['initial_decline'], C['providers'][1], 'thread-decline', dict(reason='E05 initial observed', in_response_to_seq=1), 200, None),
        (C['reply'], C['providers'][0], 'thread-reply', dict(body_markdown='E05 reply\n@case_worker received', in_response_to_seq=1), 200, 2),
        (C['replay']['command'], C['providers'][0], 'thread-reply', dict(body_markdown='E05 reply\n@case_worker received', in_response_to_seq=1), 409, None),
        (C['last_decline'], C['providers'][2], 'thread-decline', dict(reason='E05 reply observed', in_response_to_seq=2), 200, None),
        (N['decline'], N['providers'][0], 'thread-decline', dict(reason='E05 denial venue observed', in_response_to_seq=1), 200, None),
        (D['dispatch'], D['providers'][0], 'thread-dispatch', dict(brief='E05 one owned task', target_agent='case_manager', team='engineering'), 200, None),
        (D['reply'], D['providers'][0], 'thread-reply', dict(body_markdown='E05 task accepted', in_response_to_seq=1), 200, 3),
        (D['denial']['command'], D['task'], 'thread-task-send', dict(body_markdown='E05 forbidden post'), 403, None),
        (D['callback'], D['task'], 'thread-task-final', dict(status='completed', confidence=90, summary='E05 task complete', decision=dict(action='done', summary='E05 task complete')), 200, None),
        (D['read'], D['providers'][1], 'thread-task-read', None, 200, None),
        (D['followup'], D['providers'][1], 'thread-reply', dict(body_markdown='E05 task complete', in_response_to_seq=4), 200, 5),
    ]
    check([s[0] for s in specifications] == commands, 'R1 every named receipt equals canonical ordered command ledger')
    for index, (cmd, provider, action, supplied, status, seq) in enumerate(specifications):
        attempt = attempts[index]
        expected_instruction = dict(action=action, deadline=attempt['instruction']['deadline'], execution_deadline=cmd['execution_deadline'])
        if supplied is not None:
            expected_instruction['payload'] = supplied
        if action == 'thread-task-send':
            expected_instruction['thread'] = N['thread']
        if action == 'thread-task-read':
            expected_instruction['task'] = T
        check(attempt['provider'] == provider and attempt['instruction'] == expected_instruction and cmd['action'] == action,
              'R1 named owner/action/original instruction equality')
        tid = provider.get('thread')
        expected_payload = None
        if action in {'thread-reply', 'thread-decline', 'thread-dispatch'}:
            operation = action.removeprefix('thread-')
            expected_payload = dict(supplied, thread_id=tid, invocation_token='[REDACTED]')
            expected_payload['dispatcher' if operation == 'dispatch' else 'speaker'] = provider['agent']
            args = ['threads', operation, '--org', 'alpha', '--thread-id', tid]
            method, path = 'POST', f'/api/v1/orgs/alpha/threads/{tid}/{operation}'
            request_body = expected_payload
        elif action == 'thread-task-send':
            expected_payload = dict(supplied, composer='case_manager')
            args = ['threads', 'send', '--thread-id', N['thread'], '--task-id', T, '--session-id', sid, '--org', 'alpha']
            method, path = 'POST', f"/api/v1/orgs/alpha/threads/{N['thread']}/send"
            request_body = dict(expected_payload, task_id=T, session_id=sid)
        elif action == 'thread-task-final':
            expected_payload = dict(supplied, task_id=T, session_id=sid, agent='case_manager')
            args = ['report-completion', '--org', 'alpha']
            method, path = 'POST', f'/api/v1/orgs/alpha/tasks/{T}/completion'
            request_body = dict(session_id=sid, agent='case_manager', status='completed', confidence=90,
                                output_summary='E05 task complete', risks_flagged=[], dependencies=[],
                                suggested_reviewer_focus=[], decision=dict(action='done', summary='E05 task complete'))
        else:
            args = ['details', '--org', 'alpha', T]
            method, path, request_body = 'GET', f'/api/v1/orgs/alpha/tasks/{T}', None
        if expected_payload is not None:
            private_path = cmd['argv'][-1]
            check(Path(private_path).is_absolute() and Path(private_path).parent == Path(cmd['argv'][0]).parent.parent / 'coord'
                  and Path(private_path).name.startswith('e05-private-'), 'R1 actual private payload argv scope')
            args += ['--from-file', private_path]
        check(cmd['argv'][1:] == args and cmd['payload'] == expected_payload and cmd['exit'] == (0 if status == 200 else 1)
              and cmd['stderr'] == '', 'R1 exact CLI argv/payload/success or denial exit and stderr')
        selected = [w for w in cmd['wire'] if w['method'] == method and w['path'] == path]
        check(len(selected) == 1, 'R1 exact selected method and endpoint')
        wire = selected[0]
        check(wire['status'] == status and wire['request'] == request_body and wire['response'] == cmd['selected_response']
              and json.loads(wire['response_text']) == wire['response'], 'R1 request/status/selected actual response equality')
        discovery = [w for w in cmd['wire'] if w != wire]
        check(len(discovery) == (0 if action == 'thread-task-final' else 1)
              and all(w['method'] == 'GET' and w['path'] == '/api/v1/orgs' and w['request'] is None and w['status'] == 200 for w in discovery)
              and all(cmd['entered'] <= w['entered'] <= cmd['finished'] for w in cmd['wire']), 'R1 full command wire window; only shipping org discovery')
        if status != 200:
            expected = {'detail': {'code':'invocation_token_consumed','status':'consumed'} if status == 409 else {'code':'not_a_participant','composer':'case_manager'}}
            stdout = f"Error ({status}): {wire['response_text']}\n"
        elif action == 'thread-decline':
            expected, stdout = dict(thread_id=tid, status='declined'), f'ok: declined on {tid}\n'
        elif action == 'thread-reply':
            expected, stdout = dict(thread_id=tid, seq=seq, kind='message'), f'ok: reply seq={seq} on {tid}\n'
        elif action == 'thread-dispatch':
            expected = dict(task_id=T, team='engineering', assigned_agent='case_manager', dispatched_from_thread_id=D['thread'], system_message_seq=2, superseded_task_id=None)
            stdout = f"ok: dispatched {T} from {D['thread']}\n"
        elif action == 'thread-task-final':
            expected, stdout = {'ok':True}, ''
        else:
            details = wire['response']
            actual_task, results = details['task'], details['results']
            check(all(actual_task[k] == v for k,v in dict(task_id=T, status='completed', assigned_agent='case_manager',
                  team='engineering', brief='E05 one owned task', dispatched_from_thread_id=D['thread'], parent_task_id=None).items())
                  and len(results) == 1 and all(results[0][k] == v for k,v in dict(task_id=T, session_id=sid, agent='case_manager',
                  status='completed', confidence_score=90, output_summary='E05 task complete').items()), 'R1 D2 actual dispatched completed task/result summary')
            lines = cmd['stdout'].splitlines()
            check(all(line in lines for line in (f'Task:       {T}', 'Team:       engineering', 'Status:     completed',
                  'Agent:      case_manager', 'Brief:      E05 one owned task', 'Results (1):',
                  '  - [case_manager] confidence=90  E05 task complete')), 'R1 D2 shipping literal details lines')
            expected, stdout = details, cmd['stdout']
        check(wire['response'] == expected and cmd['stdout'] == stdout, 'R1 literal selected outcome and shipping stdout')

    for group in groups:
        settled = group['settled']
        cmd, wires = settled['cli'], settled['wire_records']
        check(observations['commands'][settled['command_index']] == cmd and observations['commands'].count(cmd) == 1
              and cmd['argv'][1:] == ['threads','show','--org','alpha',group['thread'],'--json']
              and cmd['exit'] == 0 and cmd['stderr'] == '', 'R1 named settled read canonical ledger and literal CLI')
        check(len(wires) == 2 and [(w['method'],w['path']) for w in wires] == [('GET','/api/v1/orgs'),('GET',f"/api/v1/orgs/alpha/threads/{group['thread']}")]
              and all(w in observations['relay'] and w['status'] == 200 and w['request'] is None and w['dropped'] is False
                      and settled['cli_entered'] <= w['entered'] <= settled['cli_finished'] for w in wires)
              and wires[-1]['response'] == settled['public'] == json.loads(cmd['stdout']), 'R1 settled read full canonical actual wire window')

    for group, subject, recipient, body in ((N,'E05 denial venue','case_worker','E05 denial anchor'),
                                            (D,'E05 dispatch','case_manager','E05 do one task')):
        compose = group['compose']
        cmd, wire = compose['command'], compose['wire']
        check(observations['commands'].count(cmd) == 1 and wire in observations['relay'] and wire['dropped'] is False,
              'R1 named N/D compose joins canonical command and relay')
        check(cmd['argv'][1:] == ['threads','compose','--org','alpha','--subject',subject,'--recipients',recipient,'--body',body]
              and cmd['exit'] == 0 and cmd['stderr'] == '' and wire['method'] == 'POST' and wire['path'] == '/api/v1/orgs/alpha/threads'
              and wire['status'] == 200 and wire['request'] == dict(subject=subject, recipients=[recipient], body_markdown=body)
              and wire['response'] == compose['response'], 'R1 N/D exact compose command/request/response')
        check(observations['commands'][compose['command_index']] == cmd and len(compose['wire_records']) == 2
              and compose['wire_records'][-1] == wire
              and [(w['method'],w['path']) for w in compose['wire_records']] == [('GET','/api/v1/orgs'),('POST','/api/v1/orgs/alpha/threads')]
              and compose['wire_records'][0]['request'] is None
              and all(w in observations['relay'] and w['status'] == 200 and w['dropped'] is False
                      and compose['entered'] <= w['entered'] <= compose['finished'] for w in compose['wire_records']),
              'R1 N/D complete canonical wire and command-index window')
        response = compose['response']
        stored = next(r for r in final['threads'] if r['id'] == group['thread'])
        check(response == dict(thread_id=group['thread'], started_at=stored['started_at'], pending_replies=[recipient]),
              'R1 N/D original compose identity and literal pending list')
        # Producer records the child-local formatted timestamp; independently
        # validate its UTC offset conversion against the actual stored instant.
        from datetime import datetime, timedelta, timezone
        stamp = datetime.fromisoformat(response['started_at'].replace('Z', '+00:00'))
        offset = compose['utc_offset_seconds']
        check(type(offset) is int and abs(offset) < 86400, 'R1 compose local timezone evidence')
        formatted = stamp.astimezone(timezone(timedelta(seconds=offset))).strftime('%Y-%m-%d %H:%M:%S')
        check(cmd['stdout'] == f"{group['thread']}  started={formatted}  pending={[recipient]}\n",
              'R1 full shipping founder compose stdout')
        digest = hashlib.sha256(json.dumps(cmd['argv'][1:]).encode()).hexdigest()
        check(len([w for w in calls if w['argv_sha256'] == digest and w['source_sha'] == row['source_sha']]) == 1,
              'R1 N/D independent executable source/argv witness')

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
    check(all(isinstance(e['at'], (int, float)) and not isinstance(e['at'], bool) for e in events)
          and all(a['at'] <= b['at'] for a,b in zip(events, events[1:])), 'R2 complete browser ledger chronological order')
    for name, snapshot in (('held', C['held_events']), ('released', C['released_events'])):
        indices = [i for i,e in enumerate(events) if e['event'] == 'snapshot' and e['name'] == name]
        check(len(indices) == 1 and snapshot == events[:indices[0]+1], 'R2 actual held/released full ledger prefix equality')
    check([e['name'] for e in events if e['event'] == 'snapshot'] == ['held','released'], 'R2 exactly two ordered snapshot markers')
    check(wire['held'] <= C['held_events'][-1]['at'] <= C['released'] <= wire['release_observed']
          <= response['at'] <= C['released_events'][-1]['at'], 'R2 snapshots and response bind actual proxy release interval')
    detail = origin + f"/orgs/alpha/threads/{C['thread']}"
    inbox = origin + '/orgs/alpha/threads'
    # Classify EVERY navigation, including intentional revisits. An extra event
    # anywhere in the full ledger cannot be hidden by a case snapshot.
    navigation = [(e['event'], e['url']) for e in events if e['event'] in {'navigation','deliberate-revisit'}]
    expected_navigation = [('navigation',inbox), ('navigation',detail)]
    for target in (inbox, detail, detail, origin + f"/orgs/alpha/threads/{D['thread']}"):
        expected_navigation += [('deliberate-revisit',target), ('navigation',target)]
    check(navigation == expected_navigation, 'R2 one automatic detail entry and four explicitly paired later revisits; no extras')
    check(not any(e['at'] < wire['release_observed'] and (e['event'] == 'compose-response'
                  or (e['event'] == 'navigation' and e['url'] != inbox)) for e in events),
          'R2 zero held response/detail navigation recomputed from full ledger')
    auto_entry = [e for e in events if e['event'] == 'navigation'][1]
    first_revisit = next(e for e in events if e['event'] == 'deliberate-revisit')
    check(response['at'] <= auto_entry['at'] <= C['released_events'][-1]['at'] <= first_revisit['at']
          and C['created']['url'] == detail and C['created']['list_url'] == inbox,
          'R2 original response to automatic entry precedes every intentional revisit')
    batch = C["event_batch"]
    check(batch in events and batch["guard_sensitivity"] == "UNPROVEN"
          and [(e["id"],e["type"]) for e in batch["rows"] if e["phase"] == "before"] == [(1,"keydown"),(2,"click"),(3,"click")], "same synchronous finite input sequence; no causal guard claim")
    check(all(a["at"] <= b["at"] for a,b in zip(batch["rows"],batch["rows"][1:])), "actual DOM event ordering")
    views = (C['initial_view'], C['view'], D['view'])
    check([dict(event='thread-view', **v) for v in views] == [e for e in events if e['event'] == 'thread-view'],
          'R2 all three actual view records in ledger order')
    for view, group, count, name in zip(views, (C,C,D), (1,2,5), ('e05-compose.png','e05-reply.png','e05-dispatch.png')):
        tid = group['thread']
        public = group['settled']['public']
        body = view['body']
        check(view['screenshot'] == name and (directory/name).read_bytes().startswith(b'\x89PNG\r\n\x1a\n'),
              'R2 named PNG bytes only; rendered semantics checked separately')
        view_index = events.index(dict(event='thread-view', **view))
        revisit_index = max(i for i,e in enumerate(events[:view_index]) if e['event'] == 'deliberate-revisit')
        response_rows = [e for e in events[revisit_index+1:view_index] if e['event'] == 'thread-response'
                         and e['url'] == origin + f'/api/v1/orgs/alpha/threads/{tid}' and e['body'] == body]
        check(view['url'] == origin + f'/orgs/alpha/threads/{tid}' and view['response_url'] == origin + f'/api/v1/orgs/alpha/threads/{tid}'
              and view['method'] == 'GET' and view['status'] == 200 and response_rows
              and response_rows[0]['method'] == 'GET' and response_rows[0]['status'] == 200,
              'R2 rendered view joins exact thread and actual public GET response in its revisit interval')
        check(body['thread_id'] == tid and body['subject'] == public['subject'] and body['status'] == 'open'
              and body['turns_used'] == (1 if count == 1 else public['turns_used'])
              and sorted(body['participants']) == sorted(public['participants']) and len(body['messages']) == count,
              'R2 initial/reply/dispatch public identity and exact projection cardinality')
        for actual, expected in zip(body['messages'], public['messages'][:count]):
            check(all(actual[k] == expected[k] for k in ('seq','speaker','kind','body_markdown','attachments','system_payload')),
                  'R2 rendered response literal message and complete system-payload projection')
        message_rows = [e for e in events[revisit_index+1:view_index] if e['event'] == 'thread-response'
                        and e['url'] == view['messages_url'] and e['body'] == view['messages_body']]
        check(view['messages_url'].split('?')[0] == origin + f'/api/v1/orgs/alpha/threads/{tid}/messages'
              and view['messages_status'] == 200 and view['messages_method'] == 'GET' and message_rows
              and message_rows[0]['status'] == 200 and message_rows[0]['method'] == 'GET'
              and len(view['messages_body']['messages']) == count and view['messages_body']['has_more'] is False,
              'R2 actual browser message-page response joins displayed projection')
        for detail_message, page_message in zip(body['messages'], view['messages_body']['messages']):
            check(all(detail_message[k] == page_message[k] for k in ('seq','speaker','kind','body_markdown','attachments','system_payload','created_at')),
                  'R2 detail/message-page immutable content equality; initial responder state may advance')
        if count != 1:
            check(view['messages_body']['messages'] == public['messages'], 'R2 settled message-page full public projection')
        expected_bubbles = [m for m in body['messages'] if m['kind'] == 'message']
        check(len(view['rendered_messages']) == len(expected_bubbles), 'R2 exact rendered message bubble count')
        for rendered, message in zip(view['rendered_messages'], expected_bubbles):
            check(rendered['visible'] is True and f"#{message['seq']} · " in rendered['header']
                  and message['speaker'] in rendered['header']
                  and ' '.join(rendered['body'].split()) == ' '.join(message['body_markdown'].split()),
                  'R2 each literal reply/opening belongs to its own rendered sequence bubble')
        if count != 1:
            check(body == public, 'R2 settled rendered response equals full observed public projection')
        normalized = ' '.join(view['text'].split())
        literals = [public['subject']] + [m['body_markdown'] for m in public['messages'][:count] if m['kind'] == 'message']
        if group is D:
            literals += [f'dispatched {T}', f'task {T} completed · E05 task complete']
            check([m['system_payload'] for m in body['messages'] if m['kind'] == 'system'] == systems,
                  'R2 full D dispatched/completed payloads and summary')
        check(all(' '.join(literal.split()) in normalized for literal in literals),
              'R2 every full opening/reply/dispatch/followup/summary literal rendered (DOM whitespace normalized)')
    closure = [d["thread_proxy_closure"] for d in durable if "thread_proxy_closure" in d]
    check(len(closure) == 1 and closure[0]["port"] == transport["browser_port"] and not closure[0]["errors"]
          and closure[0]["workers_alive"] == closure[0]["connections"] == 0 and closure[0]["listener_alive"] is False, "bounded proxy/socket/listener closure")
    final_orgs = [d for d in durable if "threads_final_org" in d]
    check(len(final_orgs) == 2 and {d["threads_final_org"] for d in final_orgs} == {"alpha","beta"}, "both final orgs")
    for d in final_orgs:
        expected = (20,29) if d["threads_final_org"] == "alpha" else (1,1)
        check((len(d["snapshot"]["tasks"]),len(d["snapshot"]["results"])) == expected
              and all(t["status"] == "completed" for t in d["snapshot"]["tasks"]), "global21tasks30results")


def e07_sql_date(value: str | None) -> str | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(value)
    encoded = parsed.isoformat()
    if parsed.utcoffset() == timedelta(0):
        encoded = encoded.removesuffix("+00:00")+"Z"
    return encoded


def e07_pause_state(raw: dict) -> dict:
    """Independent serialized validator; never consume a producer verdict/copy."""
    if not isinstance(raw, dict) or not isinstance(raw["observed_at"], str):
        raise ValueError("E07 serialized Pause observation missing/string")
    stamp = raw["observed_at"]
    instant = datetime.fromisoformat(stamp)
    if "T" not in stamp or instant.tzinfo is None or instant.utcoffset() != timedelta(0):
        raise ValueError("E07 serialized Pause observation must be timezone-aware UTC")
    if not isinstance(raw["blockers"], list):
        raise ValueError("E07 serialized Pause blockers must be an ordered array")
    for blocker in raw["blockers"]:
        if not isinstance(blocker, dict) or not isinstance(blocker["observed_at"], str) or blocker["observed_at"] != stamp:
            raise ValueError("E07 serialized blocker observation disagrees")
    result = copy.deepcopy(raw)
    del result["observed_at"]
    for blocker in result["blockers"]:
        del blocker["observed_at"]
    return result


def e07_detail_state(raw: dict) -> str:
    value = copy.deepcopy(raw)
    value["pause"] = e07_pause_state(raw["pause"])
    return json.dumps(value, sort_keys=True)


def e07_public_state(sample: dict, http: list[dict]) -> str:
    raw = sample["public"]
    indices = sample["http_indices"]
    if set(indices) != set(raw):
        raise ValueError("E07 serialized HTTP observation keys")
    value = copy.deepcopy(raw)
    for key, body in raw.items():
        entries = [(indices[key], f"/api/v1/orgs/{key}/tasks?limit=100", body)] if key in ("alpha", "beta") else [
            (indices[key][suffix], f"/api/v1/orgs/alpha/tasks/{key}"+suffix, response) for suffix,response in body.items()]
        if key not in ("alpha", "beta") and set(indices[key]) != set(body):
            raise ValueError("E07 serialized detail/recall observation keys")
        for index, path, response in entries:
            if type(index) is not int or not 0 <= index < len(http):
                raise ValueError("E07 serialized HTTP observation index")
            actual = http[index]
            if ((actual["method"], actual["path"], actual["status"]) != ("GET", path, 200)
                    or json.dumps(actual["response"], sort_keys=True) != json.dumps(response, sort_keys=True)):
                raise ValueError("E07 raw same-observation HTTP response mismatch")
        # Authenticated RAW response above; only then construct comparison state.
        if key in ("alpha", "beta"):
            if not isinstance(body["tasks"], list):
                raise ValueError("E07 serialized task page array")
            for task in value[key]["tasks"]:
                task["pause"] = e07_pause_state(task["pause"])
        else:
            value[key][""] = json.loads(e07_detail_state(body[""]))
    return json.dumps(value, sort_keys=True)


def validate_e07(row: dict, observations: dict, directory: Path) -> None:
    """Reopen serialized C evidence; no producer oracle or expected helper import."""
    import hashlib
    import socket
    from collections import Counter

    def check(condition: bool, message: str) -> None:
        if not condition:
            raise ValueError("E07 content: " + message)

    case = json.loads((directory / "e07.json").read_text())
    check(case == observations["e07"] == row["e07"], "actual raw/observations/scenario export equality")
    check(case["launch_record_failures"] == [], "missing/malformed/incomplete/unexpected private launch record")
    variants = ["E07.cancel-cascade-browser","E07.cancel-terminal-unchanged","E07.cancelled-late-cli"]
    check(case["variants"] == variants and len(row["variants"]) == 56
          and list(row["variants"])[-3:] == variants and case["prefix"]["variants"] == list(row["variants"])[:53]
          and case["design_sha256"] == MANIFEST["e07_c_design_sha256"]
          and case["source_sha"] == row["source_sha"], "exact C manifest/design/source/preserved53")
    for name in variants:
        check(row["variants"][name]["status"] == "PASS" and row["variants"][name]["evidence"] == {"e07_file":"e07.json","source_sha":row["source_sha"]}
              and row["variants"][name]["at"] >= case["finished"], "new marks follow actual case content")
    phase = next(p for p in row["phases"] if p["name"] == "scenarios")
    check(case["end"] == min(case["entered"]+50,case["scenario_deadline"])
          and case["scenario_deadline"] == phase["deadline"] and case["entered"] < case["finished"] <= case["end"], "original C/scenario deadline binding")
    check([p["name"] for p in case["phases"]] == ["capture","cancel-and-close","terminal-ui-and-refusals","forward-and-observe","archive"], "finite C-only phases")
    for p,offset in zip(case["phases"],(16,28,36,42,50)):
        check(p["end"] == min(case["entered"]+offset,case["end"],case["scenario_deadline"]) and p["entered"] < p["end"], "fixed absolute phase ends")
    check(all(a["entered"] < b["entered"] <= a["end"] for a,b in zip(case["phases"],case["phases"][1:])), "no overrun borrows next phase")
    e05 = next(d["threads_cases"] for d in observations["durable"] if "threads_cases" in d)
    boundary = case["prefix"]
    archive_raw = (directory/"e07-prefix.json").read_bytes()
    archive = json.loads(archive_raw)
    check(hashlib.sha256(archive_raw).hexdigest() == boundary["archive_sha256"]
          and archive["starts"] == observations["starts"][:36]
          and archive["commands"] == observations["commands"][:boundary["command_count"]]
          and archive["provider_commands"] == observations["provider_commands"]
          and archive["thread_commands"] == observations["thread_commands"]
          and archive["provider_observer"] == observations["provider_observer"][:boundary["observer_end"]]
          and archive["variants"] == {k:observations["variants"][k] for k in boundary["variants"]},
          "actual pre-C archived original53 outcomes and command/observer prefix")
    check(case["anchors"] == [dict(name="E05-end-B24",samples=[case["baseline"]]),
          dict(name="P-success-W-live-B25",samples=case["live"]),dict(name="cancelled-B26",samples=case["freeze"]),
          dict(name="final-B26",samples=[case["final"]])], "dedicated complete E07 boundary content")
    raw = (directory / "launch-witnesses.jsonl").read_bytes()
    check(raw.endswith(b"\n") and len(raw) == observations["witness_bytes"]
          and hashlib.sha256(raw).hexdigest() == observations["witness_sha256"], "complete actual final append-only bytes")
    all_witness = [json.loads(line) for line in raw.splitlines()]
    prefix_raw = raw[:boundary["witness_bytes"]]
    check(prefix_raw.endswith(b"\n") and hashlib.sha256(prefix_raw).hexdigest() == boundary["witness_sha256"]
          and len(prefix_raw.splitlines()) == boundary["witness_records"]
          and [json.loads(line) for line in prefix_raw.splitlines()] == e05["witnesses"]
          and all_witness == observations["launch_witnesses"] and observations["witness_incomplete_line"] is False,
          "genuine E05 byte/record boundary with exact original content")
    check(boundary["e05_finished"] == e05["finished"] <= case["entered"]
          and observations["provider_observer"][boundary["observer_end"]-1]["stubs"] == 36,
          "original E05 observer boundary, no final38 substitution")
    starts = observations["starts"]
    parent,worker = case["parent"],case["worker"]
    P,W = parent["task"],worker["task"]
    check(len(starts) == len({s["session"] for s in starts}) == 38 and starts[36:] == [parent,worker]
          and P != W and parent["agent"] == "case_manager" and worker["agent"] == "case_worker"
          and parent["org"] == worker["org"] == "alpha", "exact two genuine suffix starts")
    check(Counter(w["kind"] for w in all_witness) == {"stub":38,"callback":len(observations["commands"])+len(observations["provider_commands"])+len(observations["thread_commands"])+4}, "all old/new executable witnesses accounted")
    commands = observations["e07_commands"]
    check(commands == case["commands"] and [c["action"] for c in commands] == ["e07-parent","e07-held","terminal-cancel","terminal-cancel"], "dedicated exact four-command ledger")
    suffix = all_witness[boundary["witness_records"]:]
    check(Counter(w["kind"] for w in suffix) == {"stub":2,"callback":4}, "every appended record classified")
    callbacks = [w for w in suffix if w["kind"] == "callback"]
    check(all(w["source_sha"] == row["source_sha"] for w in suffix), "new wrapper/stub source equality")
    cli_digest = hashlib.sha256((Path(__file__).resolve().parents[1]/"cli/main.py").read_bytes()).hexdigest()
    check(all(w["cli_sha256"] == cli_digest for w in callbacks), "actual shipping CLI source hash")
    manifest = next(d["executable_manifest"] for d in observations["durable"] if "executable_manifest" in d)
    check(case["python"] == manifest["python"], "bound interpreter")
    # The task templates select Claude with unset model/effort; these are fresh
    # P/W tasks, not resumed sessions. ClaudeAdapter sends seven arguments:
    # -p --permission-mode MODE --allowedTools RULES --output-format json.
    # fake_claude.sh preserves that original argv for the unchanged guard.
    # Recompute the exact task plan bytes independently, without importing the
    # producer or guard's expected-construction helpers.
    import shlex
    wrappers = next(d["wrappers"] for d in observations["durable"] if "executable_manifest" in d)
    source = Path(__file__).resolve().parents[1]
    stub = manifest["stubs"]["claude"]
    stub_sha = hashlib.sha256((source / "tests/integration/fake_claude.sh").read_bytes()).hexdigest()
    plan_path = str(Path(manifest["root"]) / "coord/plan.sh")
    plan_bytes = ("#!/bin/sh\nexec " + shlex.quote(manifest["python"]) + " -I "
                  + shlex.quote(str(Path(manifest["source"]) / "e2e/provider.py")) + ' "$@"\n').encode()
    plan_sha = hashlib.sha256(plan_bytes).hexdigest()
    check(manifest["revision"] == row["source_sha"]
          and stub["path"] == str(Path(manifest["root"]) / "bin/claude")
          and stub["sha256"] == wrappers[stub["path"]] == stub_sha
          and wrappers[plan_path] == plan_sha
          and wrappers[plan_path + ".sha256"] == hashlib.sha256(plan_sha.encode()).hexdigest(),
          "actual immutable source/stub/task-plan bindings")
    stubs = [w for w in suffix if w["kind"] == "stub"]
    for witness in stubs:
        check(set(witness) == {"kind","provider","stub_sha256","source_sha","plan_sha256","argc","flags"}
              and type(witness["argc"]) is int and witness == dict(
                  kind="stub", provider="claude", stub_sha256=stub_sha, source_sha=row["source_sha"],
                  plan_sha256=plan_sha, argc=7, flags=["-p","--output-format"]),
              "complete new stub record and concrete fresh Claude launch contract")
    source_names = ["e2e/owned.py","e2e/transport.py","e2e/jobs.py","e2e/threads.py",
        "e2e/thread_provider.py","e2e/pause_browser.py","runtime/daemon/__main__.py","runtime/daemon/paths.py","e2e/e07.py","e2e/late_transport.py","e2e/provider.py","e2e/controller.py","e2e/oracles.py",
                    "e2e/browser.py","e2e/aggregate.py","e2e/run.py","e2e/manifest.json","cli/commands/tasks.py",
                    "runtime/daemon/routes/tasks.py","runtime/models.py","runtime/infrastructure/db/tasks.py",
        "runtime/infrastructure/task_pause_controls.py","runtime/orchestrator/run_step.py","web/src/features/tasks/CancelTaskDialog.tsx"]
    check(set(case["source_files"]) == set(source_names) and all(
        hashlib.sha256((Path(__file__).resolve().parents[1]/name).read_bytes()).hexdigest() == case["source_files"][name]
        for name in source_names), "actual complete harness/shipping seam source hashes")
    check(case["partial_launches"] == [cmd["launch"] for cmd in commands[:2]]
          and all("incomplete" not in launch and launch["provider"] == {
              k:start[k] for k in ("task","session","agent","org","pid")}
              for launch,start in zip(case["partial_launches"],(parent,worker))),
          "both actual complete pre-wait launch records in issued-command order")
    for index,cmd in enumerate(commands):
        launch = cmd["launch"] if index < 2 else cmd
        child = launch["child"]
        argv = launch["argv"]
        match = [w for w in callbacks if w["pid"] == child["pid"] and w["argv_sha256"] == hashlib.sha256(json.dumps(argv[1:]).encode()).hexdigest()]
        check(len(match) == 1 and match[0]["ppid"] == child["ppid"]
              and launch["source_sha"] == row["source_sha"] and launch["python"] == manifest["python"]
              and argv[0] == manifest["callback"] and child["start"], "each genuine CLI authority/source/argv/PID-start")
        if index < 2:
            check(child["ppid"] == (parent if index == 0 else worker)["pid"]
                  and launch["callback_sha256"] == manifest["callback_sha256"]
                  and launch["entered"] <= launch["acknowledged"] <= case["phases"][1]["entered"]
                  and launch["execution_deadline"] == cmd["instruction"]["execution_deadline"], "provider pre-wait acknowledgement")
            expected = dict(task_id=P if index == 0 else W, session_id=parent["session"] if index == 0 else worker["session"],
                            agent="case_manager" if index == 0 else "case_worker",status="completed",confidence=90,
                            summary="E07 parent delegated" if index == 0 else "E07 cancelled child must not complete")
            if index == 0:
                expected["decision"] = {"action":"delegate","agent":"case_worker","prompt":"E07_CANCEL_CHILD"}
            check(launch["payload"] == expected
                  and hashlib.sha256(json.dumps(expected,sort_keys=True).encode()).hexdigest() == launch["payload_sha256"]
                  and len(json.dumps(expected,sort_keys=True).encode()) == launch["payload_bytes"], "actual provider-bound finite payload")
    check(case["parent_command"] == commands[0] and case["held"] == commands[1] and case["refusals"] == commands[2:], "case references exact ledger")
    parent_command = commands[0]
    ret = parent_command["receipt"]
    check(ret["launch"] == parent_command["launch"] and ret["exit"] == 0 and not ret["timed_out"]
          and all(not ret[k]["truncated"] and not ret[k]["incomplete"] for k in ("stdout","stderr")), "genuine parent success return")
    pw = parent_command["wire"]
    parent_body = dict(session_id=parent["session"],agent="case_manager",status="completed",confidence=90,
                       output_summary="E07 parent delegated",risks_flagged=[],dependencies=[],suggested_reviewer_focus=[],
                       decision={"action":"delegate","agent":"case_worker","prompt":"E07_CANCEL_CHILD"})
    check(len(pw) == 1 and pw[0] in observations["relay"] and pw[0]["path"] == f"/api/v1/orgs/alpha/tasks/{P}/completion"
          and pw[0]["method"] == "POST" and pw[0]["request"] == parent_body and pw[0]["status"] == 200 and pw[0]["response"] == {"ok":True}, "parent accepted actual CLI wire")
    check("receipt" not in commands[1] and case["provider_eof"]["bytes"] == 0, "no posthumous killed CLI return claim")
    transport = case["transport"]
    check(transport["port"] in case["ports"] and transport["upstream"] in case["ports"]
          and transport["port"] != transport["upstream"], "distinct owned registered listener/upstream")
    selected = [r for r in transport["records"] if r["selected"]]
    check(len(selected) == 1 and all(r["selected"] or (r["method"] == "GET" and r["path"] == "/api/v1/orgs") for r in transport["records"]), "one selected original request, no retries/extras")
    wire = selected[0]
    body = dict(session_id=worker["session"],agent="case_worker",status="completed",confidence=90,
                output_summary="E07 cancelled child must not complete",risks_flagged=[],dependencies=[],suggested_reviewer_focus=[])
    check(wire["upstream"] == transport["upstream"] and wire["method"] == "POST" and wire["path"] == f"/api/v1/orgs/alpha/tasks/{W}/completion"
          and wire["request"] == json.loads(wire["request_text"]) == body
          and wire["request_bytes"] == len(wire["request_text"].encode())
          and wire["request_sha256"] == hashlib.sha256(wire["request_text"].encode()).hexdigest()
          and wire["request_truncated"] is False and wire["authorization_sha256"] == transport["bearer_sha256"], "actual full body and private bearer equality")
    provenance = case["provenance"]
    child = commands[1]["launch"]["child"]
    check(provenance["process"]["pid"] == child["pid"] and provenance["process"]["start"] == child["start"]
          and provenance["process"]["ppid"] == worker["pid"] and provenance["process"]["state"] != "Z"
          and len(provenance["tcp"]) == len(provenance["fds"]) == 1, "captured live CLI owns exactly one selected socket")
    tcp,fd = provenance["tcp"][0],provenance["fds"][0]
    fields = tcp["raw"].split()
    def endpoint(value: str) -> list:
        host,port = value.split(":")
        return [socket.inet_ntoa(bytes.fromhex(host)[::-1]),int(port,16)]
    check(endpoint(fields[1]) == tcp["local"] == wire["peer"] and endpoint(fields[2]) == tcp["remote"] == wire["local"]
          and fields[3] == tcp["state"] == "01" and fields[9] == tcp["inode"]
          and fd["target"] == "socket:["+tcp["inode"]+"]" and type(fd["fd"]) is int
          and wire["local"] == ["127.0.0.1",transport["port"]]
          and wire["listener_fd"] == transport["listener"]["fd"] and wire["accepted_inode"].startswith("socket:["), "actual raw /proc tuple/inode/FD/listener join")
    restart = [d["pause_daemon_restart"] for d in observations["durable"] if "pause_daemon_restart" in d]
    check(len(restart) == 1 and transport["upstream"] == restart[0]["new"]["port"]
          and (transport["daemon"]["pid"],transport["daemon"]["start"]) == (restart[0]["new"]["pid"],restart[0]["new"]["start"]), "original boot B, no new restart")
    cancel = case["browser_cancel"]
    check(commands[1]["launch"]["acknowledged"] <= wire["captured"] <= provenance["sampled"] <= cancel["entered"]
          <= cancel["finished"] <= case["freeze"][0]["entered"] <= case["freeze"][1]["finished"] <= case["released"]
          <= wire["forwarded"] <= wire["upstream_complete"] <= wire["closed"] <= case["window"]["entered"], "original pre-forward/cancel/closure/late-response order")
    check(len(transport["accepted"]) == len(transport["records"]) and any(
        a["peer"] == wire["peer"] and a["local"] == wire["local"] and a["fd"] == wire["accepted_fd"]
        and a["inode"] == wire["accepted_inode"] and a["at"] <= wire["entered"] for a in transport["accepted"]),
          "all accepted sockets registered before header/body waits")
    check(provenance["process"]["argv"][0] == case["python"]
          and provenance["process"]["argv"][-5:] == commands[1]["launch"]["argv"][1:], "actual live command line belongs to hash-bound CLI")
    check(wire["status"] == 409 and wire["response"] == json.loads(wire["response_stream"]["text"]) == {"detail":{"code":"task_not_active","task_id":W,"status":"cancelled","cancelled":True}}
          and not wire["response_stream"]["truncated"] and not wire["response_stream"]["incomplete"]
          and wire["downstream"] in {"EOF","peer-gone"}, "literal task-active denial, dead downstream separately")

    def projection(frame: dict, admitted: int, released: int, active: int, tail: dict) -> dict:
        m = frame["metrics"]
        check(any(h["path"] == "/api/v1/metrics" and h["response"] == m for h in observations["http"]), "actual public metrics record")
        h = m["host_sessions"]
        a,r = h["admission"],h["receipts"]
        check((a["admitted_total"],a["released_total"],r["published_total"],a["active"],a["queue_depth"],m["executor_sessions_active"],m["run_step_queue_depth"])
              == (admitted,released,released,active,0,active,0) and a["cancelled_queued_total"] == 0 and not a["shutdown"], "exact counters, no queued cancellation")
        check(h["wired"] and h["backend"]["healthy"] and h["backend"]["name"] != "passthrough"
              and not h["residue"]["admission_blocked"] and h["residue"]["survivors_count"] == 0
              and r["published_total"] == r["window_size"] == r["quiescent_count"] == len(r["recent"]) <= 64
              and r["with_residue_count"] == 0 and all(x["quiescent"] and x["survivors_count"] == 0 for x in r["recent"]), "complete settled retained receipt window")
        check(r["recent"][released-tail["published_total"]:] == tail["recent"] and not frame["pending"], "entire ordered duplicate-preserving tail/no peer")
        return {"admission":a,"receipts":r,"executor_sessions_active":active,"run_step_queue_depth":0}

    live = case["live"]
    prior = e05["final_anchor"]["projection"]["receipts"]
    projection(case["baseline"],24,24,0,prior)
    check(len(live) == 2 and projection(live[0],26,25,1,prior) == projection(live[1],26,25,1,prior), "two B26/25/25/1 projections")
    check(live[1]["metrics"]["host_sessions"]["receipts"]["recent"][0]["terminal_reason"] == "success", "P success distinct from active cancellation")
    result = case["parent_result"]
    check(type(result["id"]) is int and result["task_id"] == P and result["session_id"] == parent["session"]
          and result["status"] == "completed" and result["confidence_score"] == 90 and result["output_summary"] == "E07 parent delegated"
          and json.loads(result["decision_json"]) == {"action":"delegate","agent":"case_worker","prompt":"E07_CANCEL_CHILD"}, "one real accepted delegation result")
    live_tasks = {t["id"]:t for t in live[1]["sql"]["alpha"]["tables"]["tasks"]}
    check(live_tasks[P]["status"] == live_tasks[W]["status"] == "in_progress" and live_tasks[P]["block_kind"] == "delegated"
          and live_tasks[W]["block_kind"] is None and live_tasks[W]["parent_task_id"] == P, "actual parked/live tree")
    freeze,second = case["freeze"]
    check(projection(freeze,26,26,0,live[1]["metrics"]["host_sessions"]["receipts"]) == projection(second,26,26,0,live[1]["metrics"]["host_sessions"]["receipts"]), "two settled B26 projections")
    newest = freeze["metrics"]["host_sessions"]["receipts"]["recent"][0]
    check(newest["terminal_reason"] == "cancelled" and newest["invocation_kind"] == "task", "one W active cancellation receipt")
    tables = freeze["sql"]["alpha"]["tables"]
    tasks = {t["id"]:t for t in tables["tasks"]}
    check(len(tasks) == 22 and len(tables["task_results"]) == 30 and Counter(t["status"] for t in tasks.values()) == {"completed":20,"cancelled":2}
          and len(freeze["sql"]["beta"]["tables"]["tasks"]) == len(freeze["sql"]["beta"]["tables"]["task_results"]) == 1, "global23/31 and21completed/two cancelled")
    for task,start in ((P,parent),(W,worker)):
        t = tasks[task]
        check(t["status"] == "cancelled" and t["block_kind"] is None and t["note"] == "cancelled by founder: E07_CANCEL_TREE"
              and t["cancelled_at"] is not None and t["cancelled_at"] == t["completed_at"] == tasks[P]["cancelled_at"]
              and t["current_session_id"] == start["session"], "exact shared cancellation stamp and owner")
        check(freeze["public"][task][""]["task"]["task_id"] == task
              and all(freeze["public"][task][""]["task"][k] == (e07_sql_date(t[k]) if k in ("cancelled_at","completed_at") else t[k])
                      for k in ("status","block_kind","note","cancelled_at","completed_at","current_session_id")), "actual detail fields agree with coherent frame")
    changed_fields = {"status","block_kind","note","cancelled_at","completed_at"}
    for task in (P,W):
        check({k:v for k,v in live_tasks[task].items() if k not in changed_fields}
              == {k:v for k,v in tasks[task].items() if k not in changed_fields}, "coherent literal cancellation field deltas")
        recall = freeze["public"][task]["/recall"]
        check(recall["task_id"] == task and recall["status"] == "cancelled", "literal public terminal recall")
        for suffix,body in freeze["public"][task].items():
            check(any(h["method"] == "GET" and h["path"] == f"/api/v1/orgs/alpha/tasks/{task}"+suffix
                      and h["status"] == 200 and h["response"] == body for h in observations["http"]), "real public detail/recall/tree response content")
    recall_tree = freeze["public"][P]["/recall?tree=true"]
    check(recall_tree["task_id"] == P and recall_tree["status"] == "cancelled" and len(recall_tree["children"]) == 1
          and recall_tree["children"][0]["task_id"] == W and recall_tree["children"][0]["status"] == "cancelled", "exact public recall lineage")
    check([r for r in tables["task_results"] if r["task_id"] in (P,W)] == [result], "no worker result or replacement")
    audits = [a for a in tables["audit_log"] if a["task_id"] in (P,W)]
    cancels = [a for a in audits if a["action"] == "task_cancelled"]
    check([a["task_id"] for a in cancels] == [P,W] and all(a["agent"] == "founder" and json.loads(a["payload"]) == {"rationale":"E07_CANCEL_TREE","cascade":True} for a in cancels), "exact two founder audits")
    check(sum(a["action"] == "session_end" and a["task_id"] == W for a in audits) == 1
          and not any(a["task_id"] == W and a["action"] in {"completion_report","orchestration_step"} for a in audits), "one W end, no result consumption")
    decisions = [a for a in audits if a["task_id"] == P and a["action"] == "orchestration_step"]
    check(len(decisions) == 1 and json.loads(decisions[0]["payload"])["decision"]["action"] == "delegate", "P INTEGER result consumed once as delegate")
    baseline = case["baseline"]["sql"]
    check(freeze["sql"]["beta"] == baseline["beta"], "complete unchanged beta")
    for name,old in baseline["alpha"]["tables"].items():
        now = tables[name]
        if name in {"tasks","task_results","audit_log"}:
            check([r for r in now if r.get("task_id",r.get("id")) not in (P,W)] == old, "full old task/result/audit prefix " + name)
        elif name.startswith("task_"):
            check([r for r in now if r.get("task_id",r.get("root_task_id")) not in (P,W)] == old, "full old task lineage/control prefix " + name)
        else:
            check(now == old, "unchanged entire jobs/thread/delivery content " + name)
    check(len(tables["jobs"]) == 7 and freeze["sql"]["alpha"]["files"] == baseline["alpha"]["files"], "unchanged7 jobs/seven scripts and product files")
    for sample in [second,case["after_refusals"],*case["window"]["samples"],case["final"]]:
        check(sample["sql"] == freeze["sql"] and e07_public_state(sample, observations["http"]) == e07_public_state(freeze, observations["http"]), "full task/result/audit/lineage/job/thread/public unchanged")
        projection(sample,26,26,0,freeze["metrics"]["host_sessions"]["receipts"])
    tree = case["tree"]
    check(tree["chain"][0]["pid"] == worker["pid"] and tree["chain"][-1]["pid"] == tree["launch_pid"] == live_tasks[W]["executor_pid"]
          and tree["launch_pid"] != worker["pid"] and all(a["ppid"] == b["pid"] for a,b in zip(tree["chain"],tree["chain"][1:])), "separate real launch root/plan ancestry")
    expected_pids = {(x["pid"],x["start"]) for x in tree["chain"]+[child]}
    for closed in [case["closure_samples"][-1],*[s["closure"] for s in case["window"]["samples"]]]:
        check({(p["pid"],p["start"]) for p in closed["processes"]} == expected_pids
              and all(p["observed_start"] != p["start"] for p in closed["processes"])
              and all(not g["pids"] for g in closed["groups"].values()) and not closed["pending"], "all actual process generations closed before rescue")
    check(case["closure_samples"][-1]["sampled"] <= freeze["entered"], "product closure precedes Fcancel")
    check({u["unit"] for u in case["units"]} == {g.rsplit("/",1)[-1] for g in case["closure_samples"][-1]["groups"]}
          and all("ActiveState=inactive" in u["stdout"]["text"] and not u["stdout"]["truncated"]
                  and u["at"] < freeze["entered"] for u in case["units"]), "every owned product unit inactive before delayed forward")
    for cmd,task,status,rationale in zip(commands[2:],(P,case["completed_target"]["id"]),("cancelled","completed"),("E07_DUPLICATE","E07_TERMINAL")):
        posts = [w for w in cmd["wire"] if w["method"] == "POST"]
        check(len(posts) == 1 and posts[0] in observations["relay"] and posts[0]["path"] == f"/api/v1/orgs/alpha/tasks/{task}/cancel"
              and posts[0]["request"] == {"rationale":rationale,"cascade":True} and posts[0]["status"] == 409
              and posts[0]["response"] == {"detail":{"code":"task_already_terminal","current_status":status}}
              and cmd["exit"] == 1 and cmd["stdout"]["text"] == f"Task {task} is already {status}; nothing to cancel.\n"
              and cmd["stderr"]["text"] == "" and all(not cmd[k]["truncated"] and not cmd[k]["incomplete"] for k in ("stdout","stderr"))
              and cmd["observed_start"] != cmd["child"]["start"] and cmd["entered"] >= second["finished"], "genuine terminal refusals and full human output")
    check(case["completed_target"] in baseline["alpha"]["tables"]["tasks"] and case["completed_target"]["status"] == "completed", "identified completed baseline target")
    window = case["window"]
    samples = window["samples"]
    check(window["end"] == window["entered"]+1 and len(samples) >= 21
          and samples[0]["entered"]-window["entered"] <= .05 and samples[-1]["entered"] >= window["end"]
          and window["finished"] >= samples[-1]["finished"] and window["finished"] <= case["phases"][3]["end"]
          and all(0 < b["entered"]-a["entered"] <= .05 for a,b in zip(samples,samples[1:]))
          and all(s["finished"]-s["entered"] <= .05 and s["witnesses"] == case["after_refusals"]["witnesses"] for s in samples), "entire fresh one-second <=50ms unchanged window")
    check(case["transport_closure"]["forward_count"] == 1 and case["transport_closure"]["port"] == transport["port"]
          and not any(case["transport_closure"][k] for k in ("listener_alive","workers_alive","sockets","errors")), "owned one-shot transport closure")
    events = case["browser_events"]
    check([e["event"] for e in events] == ["detail-reload","detail-reload","cancel","detail-reload","detail-reload","list-reload"], "full finite browser action ledger")
    check(cancel["status"] == 200 and cancel["method"] == "POST" and cancel["request"] == {"rationale":"E07_CANCEL_TREE"}
          and cancel["response"] == {"ok":True,"task_id":P,"cancelled":[P,W],"killed":[{"task_id":W,"agent":"case_worker"}]}
          and len(cancel["requests"]) == 1 and cancel["requests"][0]["body"] == cancel["request"], "one actual dialog cancellation/no invented PID")
    for view, task, qualifier in ((cancel["parent_view"], P, "· Waiting on child work"),
                                   (cancel["child_view"], W, None)):
        public = view["detail"]["task"]
        check(dict(event="detail-reload", **view) in events
              and view["task"] == public["task_id"] == view["recall"]["task_id"] == task
              and view["url"].endswith(f"/orgs/alpha/tasks/{task}")
              and view["detail_url"].endswith(f"/api/v1/orgs/alpha/tasks/{task}")
              and view["recall_url"] == view["detail_url"]+"/recall?tree=true"
              and view["detail_method"] == view["recall_method"] == "GET"
              and view["detail_status"] == view["recall_status"] == 200
              and view["header"] == dict(identity=task, status="in_progress", qualifier=qualifier)
              and public["status"] == view["recall"]["status"] == "in_progress"
              and view["cancel_disabled"] is False, "own rendered live header and actual detail/tree")
        if task == P:
            check(public["block_kind"] == "delegated" and public["child_wait"]["state"] == "waiting_children"
                  and [node["task_id"] for node in view["recall"]["children"]] == [W]
                  and view["recall"]["children"][0]["status"] == "in_progress", "public waiting parent/child tree")
        else:
            check(public["block_kind"] is None and public["child_wait"] is None
                  and public["parent_task_id"] == P and view["recall"]["parent_task_id"] == P,
                  "public running unblocked child")
    for view,task,name in zip(case["views"][:2],(P,W),("e07-parent.png","e07-child.png")):
        check(dict(event="detail-reload",**view) in events and view["url"].endswith(f"/orgs/alpha/tasks/{task}")
              and view["detail_url"].endswith(f"/api/v1/orgs/alpha/tasks/{task}") and view["recall_url"] == view["detail_url"]+"/recall?tree=true"
              and view["detail_method"] == view["recall_method"] == "GET" and view["detail_status"] == view["recall_status"] == 200
              and e07_detail_state(view["detail"]) == e07_detail_state(freeze["public"][task][""]) and view["recall"] == freeze["public"][task]["/recall?tree=true"]
              and view["cancel_disabled"] is True and view["cancel_title"] == "Cannot cancel a cancelled task"
              and view["badges"] and all(x == "cancelled" for x in view["badges"]) and "cancelled" in view["text"]
              and view["screenshot"] == name and (directory/name).read_bytes().startswith(b"\x89PNG\r\n\x1a\n"), "actual terminal detail/reload/literal badge/disabled control")
    listing = case["views"][2]
    check(dict(event="list-reload",**listing) in events and listing["method"] == "GET" and listing["status"] == 200
          and listing["url"].endswith("/orgs/alpha/tasks") and listing["roots_url"].split("?")[0].endswith("/api/v1/orgs/alpha/tasks/roots")
          and any(t["task_id"] == P and t["status"] == "cancelled" for t in listing["roots"]["tasks"])
          and "Cancelled" in listing["text"] and listing["parent_href"] == f"/orgs/alpha/tasks/{P}"
          and (directory/listing["screenshot"]).read_bytes().startswith(b"\x89PNG\r\n\x1a\n"), "actual list group and task link")

    # ID18 is an independent exported-content consumer. Producer labels and
    # private raw-byte digests cannot stand in for missing original members.
    from urllib.parse import parse_qs, urlsplit
    import re

    def image(value: object) -> str:
        return json.dumps(value, sort_keys=True, ensure_ascii=True, allow_nan=False, separators=(",", ":"))

    def strict_file(name: str) -> object:
        def pairs(items: list) -> dict:
            value = {}
            for key, item in items:
                check(key not in value, "duplicate exported JSON key")
                value[key] = item
            return value
        def nonfinite(_value: str) -> None:
            raise ValueError("E07 content: nonfinite exported JSON")
        return json.loads((directory/name).read_bytes().decode("utf-8"),
                          object_pairs_hook=pairs, parse_constant=nonfinite)

    check(image(strict_file("e07.json")) == image(case)
          and image(strict_file("observations.json")) == image(observations)
          and image(strict_file("result.json")) == image(row), "strict complete exported copies")
    admission = daily_admission(Path(__file__).resolve().parents[1])
    check(all(row[key] == value for key, value in admission.items()), "independent source/tree/event/run binding")
    check(directory.name == "e2e-"+row["python"] and row["python"] in ("3.12", "3.14")
          and any(d.get("python_version", "").startswith(row["python"]+".")
                  for d in observations["durable"]), "actual cell/interpreter version")
    trace = strict_file("browser-events.json")
    check(type(trace) is list and observations["failures"] == [] and observations["observer_error"] is None,
          "complete browser stream and no recorded ownership errors")
    ref = listing["roots_observation"]
    check(type(ref) is dict and set(ref) == {"group_id", "checkpoint_site", "selected_request_sequence", "query", "accepted_at"}
          and type(ref["group_id"]) is int and ref["group_id"] > 0
          and ref["checkpoint_site"] == "18", "public exact group/checkpoint reference")
    groups = [item for item in trace if item.get("event") == "response-observation"
              and item.get("group_id") == ref["group_id"]]
    check(len(groups) == 1, "one complete ID18 group in this browser stream")
    group = groups[0]
    origin = "http://127.0.0.1:"+str(restart[0]["new"]["port"])
    destination = origin+"/orgs/alpha/tasks"
    boots = observations["boots"]
    check(len(boots) == 2 and type(group["boot_index"]) is int and group["boot_index"] == 1
          and image(group["boot"]) == image(boots[1])
          and (boots[1]["pid"], boots[1]["start"]) == (restart[0]["new"]["pid"], restart[0]["new"]["start"])
          and restart[0]["new"]["port"] in boots[1]["discovered_ports"], "current original post-Pause boot/port")
    check(group["source_sha"] == case["source_sha"] == row["source_sha"]
          and group["python"] == case["python"] == manifest["python"]
          and group["origin"] == origin and group["org"] == "alpha"
          and group["phase"] == "terminal-ui-and-refusals" and group["checkpoint_site"] == "18"
          and group["path"] == "/api/v1/orgs/alpha/tasks/roots" and group["document"] == "/orgs/alpha/tasks"
          and group["source_url"] == listing["url"] == destination
          and group["mode"] == "new-document" and group["tree"] is False
          and group["capture_json"] is True, "exact source/cell/origin/document/phase")
    steps = group["steps"]
    check(type(steps) is list and steps and steps[0]["event"] == "arm", "original arm")
    end = group["deadline"]
    for step in steps:
        check(type(step["sequence"]) is int and step["sequence"] >= 0
              and type(step["at"]) in (int, float) and steps[0]["at"] <= step["at"] < end,
              "all trace work inside original bound")
    check(all(a["sequence"] <= b["sequence"] and a["at"] <= b["at"] for a,b in zip(steps,steps[1:])),
          "ordered native sequence and synchronous step positions")
    def positions(event: str) -> list[int]:
        return [i for i,step in enumerate(steps) if step["event"] == event]
    def only(event: str) -> int:
        found = positions(event)
        check(len(found) == 1, "one "+event)
        return found[0]
    arm,nav,commit,frozen,guarded,cleanup,sealed = (only(name) for name in
        ("arm", "navigation-request", "commit", "checkpoint", "final-guard", "local-cleanup", "seal"))
    check(arm == 0 and arm < nav < commit < frozen < guarded < cleanup < sealed == len(steps)-1
          and steps[nav]["expected"] is True and steps[nav]["main_frame"] is True
          and steps[nav]["method"] == "GET" and steps[nav]["unredirected"] is True
          and steps[nav]["url"] == steps[commit]["url"] == destination
          and steps[commit]["expected"] is True and steps[nav]["sequence"] < steps[commit]["sequence"]
          and steps[commit]["document_sequence"] == steps[nav]["sequence"], "actual unredirected navigation Request/commit")
    source_sequence = group["source_document_sequence"]
    check(type(source_sequence) is int and 0 < source_sequence < steps[arm]["sequence"]
          and group["source_document_url"] == group["source_url"] == destination
          and group["source_navigation_pending"] is False and group["source_unredirected"] is True,
          "armed exact original source document attribution")
    for i, step in enumerate(steps):
        if step["event"] != "same-document":
            continue
        check(arm < i and steps[arm]["sequence"] < step["sequence"]
              and step["expected"] is True and step["url"] == destination,
              "same-document exact nondeparting URL")
        if i < commit:
            check(step["document_sequence"] == source_sequence
                  and step["sequence"] < steps[commit]["sequence"]
                  and (step["sequence"] > steps[nav]["sequence"] if nav < i
                       else step["sequence"] < steps[nav]["sequence"])
                  and step["pending_navigation_sequence"] == (steps[nav]["sequence"] if nav < i else None),
                  "precommit remains old source with only selected pending Request")
        else:
            check(step["document_sequence"] == steps[nav]["sequence"]
                  and step["sequence"] > steps[commit]["sequence"]
                  and step["pending_navigation_sequence"] is None,
                  "postcommit retains new Request document lineage")
    check(steps[guarded]["document_sequence"] == steps[nav]["sequence"]
          and steps[guarded]["boot_index"] == 1 and steps[guarded]["failure"] is False
          and steps[guarded]["poisoned"] is False, "final non-yielding current-document/boot/error guard")
    check(not any(step["event"] in {"request-failed", "ineligible-response", "observation-error"} for step in steps),
          "no member failure or refused ownership")
    terminal_phase = case["phases"][2]
    check(boots[1]["entered"] < terminal_phase["entered"] <= steps[arm]["at"]
          and second["finished"] <= steps[arm]["at"]
          and steps[arm]["at"] <= listing["at"] <= steps[frozen]["at"]
          and steps[sealed]["at"] <= ref["accepted_at"] < end
          and end <= min(steps[arm]["at"]+4, terminal_phase["end"],case["end"],case["scenario_deadline"])
          and ref["accepted_at"] <= case["phases"][3]["entered"], "original4s/phase/arm/DOM/freeze/seal/accept join")
    required = ("pending", "in_progress", "escalated", "completed", "failed", "cancelled", "superseded")
    def query(url: str) -> dict | None:
        parsed = urlsplit(url)
        check((parsed.scheme,parsed.netloc,parsed.path) == ("http",urlsplit(origin).netloc,
              "/api/v1/orgs/alpha/tasks/roots") and not parsed.fragment, "literal roots origin/path/no fragment")
        value = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True, errors="strict")
        return value if value in [{"limit":["50"],"status":[status]} for status in required] else None
    members = {}
    for i,step in enumerate(steps):
        if step["event"] != "request-start":
            continue
        decoded = query(step["url"]) if step["url"] is not None else None
        eligible = (decoded is not None and step["main_frame"] is True and step["method"] == "GET"
                    and step["unredirected"] is True and i > commit
                    and step["commit"] == steps[commit]["sequence"]
                    and step["document_sequence"] == steps[nav]["sequence"])
        check(step["eligible"] is eligible, "independently reconstructed request eligibility")
        if eligible:
            seq = step["request_sequence"]
            check(type(seq) is int and seq == step["sequence"] and seq not in members and i < frozen
                  and image(decoded) == image(step["query"]), "unique original OPEN member/query, no late start")
            members[seq] = (i,step,decoded)
    check({item[2]["status"][0] for item in members.values()} == set(required), "all seven literal queries")
    frozen_members = steps[frozen]["members"]
    check(steps[frozen]["site"] == "18" and frozen_members == list(members)
          and steps[sealed]["members"] == frozen_members, "complete frozen/sealed member equality")
    bodies = group["bodies"]
    check(type(bodies) is list and len(bodies) == len(members)
          and {body["request_sequence"] for body in bodies} == set(members), "every original full body exactly once")
    member_events = {"selected", "body-begin", "body-settled", "body-end", "request-finished"}
    check(all(step["request_sequence"] in members for step in steps if step["event"] in member_events),
          "no unmatched response/body/finish or cross-group stitching")
    images = {}
    def stamp(value: object) -> None:
        check(type(value) is str and "T" in value, "UTC observation stamp shape")
        parsed = datetime.fromisoformat(value)
        check(parsed.tzinfo is not None and parsed.utcoffset() == timedelta(0), "UTC observation stamp")
    def fields(value: object, strings: str = "", nullable: str = "", integers: str = "", booleans: str = "") -> None:
        check(type(value) is dict, "complete object shape")
        for key in strings.split():
            check(key in value and type(value[key]) is str, "string "+key)
        for key in nullable.split():
            check(key in value and (value[key] is None or type(value[key]) is str), "nullable "+key)
        for key in integers.split():
            check(key in value and type(value[key]) is int, "integer "+key)
        for key in booleans.split():
            check(key in value and type(value[key]) is bool, "boolean "+key)
    for seq,(started,request,decoded) in members.items():
        joins = {}
        for event in member_events:
            found = [i for i,step in enumerate(steps) if step["event"] == event and step["request_sequence"] == seq]
            check(len(found) == 1, "one original "+event)
            joins[event] = found[0]
        response = steps[joins["selected"]]
        check(started < joins["selected"] < joins["body-begin"] < joins["body-settled"] < joins["body-end"] < guarded
              and joins["selected"] < joins["request-finished"] < guarded
              and response["status"] == 200 and type(response["status"]) is int
              and response["method"] == "GET" and response["main_frame"] is True and response["unredirected"] is True
              and response["request_url"] == request["url"] == response["response_url"]
              and response["document_sequence"] == steps[nav]["sequence"]
              and image(query(response["response_url"])) == image(decoded)
              and steps[joins["body-settled"]]["outcome"] == "success"
              and steps[joins["body-settled"]]["error"] is None, "own original response/body AND finish")
        raw_body = next(body for body in bodies if body["request_sequence"] == seq)
        check(type(raw_body["sha256"]) is str and re.fullmatch(r"[0-9a-f]{64}",raw_body["sha256"]) is not None,
              "diagnostic private raw-byte identity only")
        body = raw_body["body"]
        check("[REDACTED]" not in image(body) and type(body) is dict
              and type(body.get("tasks")) is list and "next_cursor" in body and body["next_cursor"] is None,
              "complete unredacted roots including unknown content")
        compared = copy.deepcopy(body)
        seen = set()
        status = decoded["status"][0]
        for index,task in enumerate(body["tasks"]):
            fields(task, "task_id status team task_type brief created_at updated_at severity_rollup",
                   "assigned_agent parent_task_id revisit_of_task_id dispatched_from_thread_id block_kind "
                   "blocked_on_job_ids active_chain active_fanout note final_output_dir completed_at cancelled_at "
                   "last_heartbeat current_session_id zombie_flagged_at", "orchestration_step_count revision_count")
            check(task["task_id"] not in seen and task["task_id"] != W and task["parent_task_id"] is None
                  and task["status"] == status and "session_timeout_seconds" in task
                  and (task["session_timeout_seconds"] is None or type(task["session_timeout_seconds"]) is int)
                  and type(task.get("direct_revisits")) is list, "unique null-parent requested-status full root")
            seen.add(task["task_id"])
            # ID18 is terminal: an undeclared delegated task cannot borrow a
            # stamp exclusion or a phase from another case/group.
            check("child_wait" in task and task["child_wait"] is None
                  and not (task["status"] == "in_progress" and task["block_kind"] == "delegated"), "terminal/undeclared child-wait refusal")
            hold = task["pause"]
            fields(hold, "org_slug root_task_id lifecycle_status control_state observed_at", "paused_at resumed_at",
                   "generation", "held effective_hold evidence_complete")
            stamp(hold["observed_at"])
            check(hold["org_slug"] == "alpha" and hold["root_task_id"] == task["task_id"]
                  and hold["lifecycle_status"] == task["status"] and type(hold.get("blockers")) is list, "root pause identity")
            for blocker in compared["tasks"][index]["pause"]["blockers"]:
                check(type(blocker) is dict and blocker.get("observed_at") == hold["observed_at"], "exact-slot blocker stamp")
                del blocker["observed_at"]
            del compared["tasks"][index]["pause"]["observed_at"]
            if task["task_id"] == P:
                want = dict(task_id=P, brief="E07_CANCEL_PARENT", team="engineering", status="cancelled",
                            assigned_agent="case_manager", parent_task_id=None, block_kind=None,
                            current_session_id=parent["session"], note="cancelled by founder: E07_CANCEL_TREE",
                            severity_rollup="cancelled")
                check(all(image(task[key]) == image(value) for key,value in want.items())
                      and hold["held"] is False and hold["effective_hold"] is False
                      and hold["control_state"] == "unheld" and hold["generation"] == 0
                      and hold["paused_at"] is None and hold["resumed_at"] is None,
                      "authored cancelled P bound to genuine allocation/session/DB/audit")
        check((P in seen) == (status == "cancelled"), "P exactly once only in cancelled partition; W never root")
        full_image = image(compared)
        check(status not in images or images[status] == full_image, "every same-query full type-sensitive image")
        images[status] = full_image
    for ready_at in positions("ready"):
        ready_members = steps[ready_at]["members"]
        check(ready_at < frozen and len(ready_members) == len(set(ready_members))
              and set(ready_members) <= set(members)
              and {members[seq][2]["status"][0] for seq in ready_members} == set(required)
              and all(any(i < ready_at and step["event"] == "body-end" and step["request_sequence"] == seq
                          for i,step in enumerate(steps)) for seq in ready_members), "OPEN all-query original-body readiness")
    check(positions("ready"), "readiness before DOM/checkpoint/freeze")
    check(steps[positions("ready")[0]]["at"] <= listing["at"], "all-query readiness before DOM snapshot")
    representatives = [dict(query={"limit":["50"],"status":[status]}, request_sequence=min(
        seq for seq,member in members.items() if member[2]["status"] == [status])) for status in required]
    check(image(steps[sealed]["representatives"]) == image(representatives)
          and steps[sealed]["body_tasks_settled"] is True and steps[sealed]["wakes_detached"] is True
          and steps[sealed]["disposals_complete"] is True and steps[cleanup]["aborted"] is False
          and steps[cleanup]["tasks"] == [], "whole frozen group settled/disposed without outstanding owner")
    selected = next(item["request_sequence"] for item in representatives if item["query"]["status"] == ["cancelled"])
    selected_body = next(body["body"] for body in bodies if body["request_sequence"] == selected)
    check(ref["selected_request_sequence"] == selected and type(ref["selected_request_sequence"]) is int
          and image(ref["query"]) == image({"status":["cancelled"],"limit":["50"]})
          and listing["roots_url"] == members[selected][1]["url"]
          and image(query(listing["roots_url"])) == image(ref["query"])
          and image(listing["roots"]) == image(selected_body)
          and image(dict(event="list-reload",**listing)) == image(events[-1])
          and image(case) == image(observations["e07"]) == image(row["e07"]),
          "earliest cancelled original, full selected body and identical public references/copies")

    # Optional diagnostics belong ONLY to complete cases. All original equality,
    # actual-source and behavioral gates above remain mandatory. This reader
    # neither imports the stat parser nor makes a claim that a syscall ran.
    def diag_number(value: object, *, zero: bool = False) -> bool:
        return type(value) is int and (0 if zero else 1) <= value < 10**20

    def diag_ticks(value: object) -> bool:
        return (type(value) is str and 1 <= len(value) <= 20 and value.isascii()
                and value.isdecimal() and int(value) > 0)

    def diag_time(value: object) -> bool:
        return type(value) in (int, float) and 0 <= value < float("inf")

    def read_observation(value: object, pid: int, lower: float, upper: float) -> None:
        check(type(value) is dict and set(value) == {
            "pid", "start", "state", "ppid", "reason", "entered", "finished"}, "diagnostic read keys")
        check(diag_number(value["pid"]) and value["pid"] == pid
              and diag_time(value["entered"]) and diag_time(value["finished"])
              and lower <= value["entered"] <= value["finished"] <= upper, "diagnostic read identity/interval")
        check(type(value["reason"]) is str and value["reason"] in {
            "observed", "absent", "unreadable", "interrupted", "empty", "partial", "oversized", "malformed"},
              "finite diagnostic read reason")
        if value["reason"] == "observed":
            check(diag_ticks(value["start"]) and diag_number(value["ppid"], zero=True)
                  and type(value["state"]) is str and len(value["state"]) == 1
                  and value["state"] in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz",
                  "same-buffer diagnostic scalars")
        else:
            check(all(value[k] is None for k in ("start", "state", "ppid")), "UNKNOWN/absence has no scalar fact")

    def association(value: object, pid: int, pin: str | None, pins: dict,
                    lower: float, upper: float) -> int:
        check(type(value) is dict and set(value) == {
            "entered", "finished", "expected_start", "target_a", "parent", "target_b", "parent_pin", "relation"},
              "diagnostic association keys")
        check(diag_time(value["entered"]) and diag_time(value["finished"])
              and lower <= value["entered"] <= value["finished"] <= upper
              and (value["expected_start"] is None if pin is None else
                   diag_ticks(value["expected_start"]) and value["expected_start"] == pin),
              "diagnostic original pin/interval")
        a, p, b = value["target_a"], value["parent"], value["target_b"]
        read_observation(a, pid, value["entered"], value["finished"])
        reads = 1
        if a["reason"] != "observed":
            expected = "target-absent" if a["reason"] == "absent" else "target-unknown"
        elif pin is not None and a["start"] != pin:
            expected = "target-reused"
        elif a["ppid"] == 0:
            expected = "no-parent"
        else:
            reads = 3
            read_observation(p, a["ppid"], a["finished"], value["finished"])
            read_observation(b, pid, p["finished"], value["finished"])
            parent_pin = pins.get(a["ppid"])
            check(value["parent_pin"] is None if parent_pin is None else
                  diag_ticks(value["parent_pin"]) and value["parent_pin"] == parent_pin,
                  "parent pin comes only from existing generation/context")
            if b["reason"] != "observed" or b["start"] != a["start"] or b["ppid"] != a["ppid"]:
                expected = "target-changed"
            elif p["reason"] != "observed":
                expected = "parent-missing" if p["reason"] == "absent" else "parent-unknown"
            elif parent_pin is not None and p["start"] != parent_pin:
                expected = "parent-reused"
            else:
                expected = "bracketed"
        if reads == 1:
            check(p is None and b is None and value["parent_pin"] is None, "unconfirmed target has no parent walk")
        check(type(value["relation"]) is str and value["relation"] == expected,
              "independent sequential relation, never closure/adoption/wait ownership")
        return reads

    def diagnostics(closed: dict, upper: float) -> None:
        processes = closed["processes"]
        present = "controller_diagnostic" in closed or any("diagnostic" in p for p in processes)
        if not present:
            return  # Historical absence is unobserved; no backfill or credit.
        check(type(processes) is list and len(processes) == len(tree["chain"])+1 <= 17
              and [(p["pid"], p["start"]) for p in processes]
              == [(p["pid"], p["start"]) for p in tree["chain"]+[child]],
              "all original targets/order, no diagnostic truncation")
        pins = {}
        for p in [*processes, transport["daemon"]]:
            check(diag_number(p["pid"]) and diag_ticks(p["start"])
                  and (p["pid"] not in pins or pins[p["pid"]] == p["start"]), "unambiguous original diagnostic pins")
            pins[p["pid"]] = p["start"]
        check(diag_time(closed["sampled"]) and case["entered"] <= closed["sampled"] <= upper,
              "original sample time before optional work")
        check("controller_diagnostic" in closed and all("diagnostic" in p for p in processes)
              and diag_number(phase["pid"]) and transport["daemon"]["ppid"] == phase["pid"],
              "complete optional records and controller invocation PID join")
        context = closed["controller_diagnostic"]
        reads = association(context, phase["pid"], None, pins, closed["sampled"], upper)
        # A surviving owner.add receipt can corroborate self generation. It
        # never supplies the missing independent launcher generation/role pin.
        identities = row["cleanup"].get("identities", {})
        check(type(identities) is dict, "optional controller owner identities")
        controller_pin = identities.get(str(phase["pid"]))
        if controller_pin is not None:
            check(diag_ticks(controller_pin), "retained controller generation pin")
            for observation in (context["target_a"], context["target_b"]):
                if observation is not None and observation["reason"] == "observed":
                    check(observation["start"] == controller_pin, "observed self matches retained owner pin")
        if context["relation"] == "bracketed":
            for observation in (context["target_a"], context["parent"]):
                pins.setdefault(observation["pid"], observation["start"])
        last = context["finished"]
        for p in processes:
            check(p["observed_start"] is None or diag_ticks(p["observed_start"]), "original observed start type")
            reads += association(p["diagnostic"], p["pid"], p["start"], pins, last, upper)
            last = p["diagnostic"]["finished"]
        check(reads <= 54, "per-closure finite diagnostic read cardinality")

    for index, closed in enumerate(case["closure_samples"]):
        upper = (case["closure_samples"][index+1]["sampled"]
                 if index+1 < len(case["closure_samples"]) else freeze["entered"])
        diagnostics(closed, min(upper, case["phases"][1]["end"], case["finished"]))
    for index, sample in enumerate(samples):
        upper = samples[index+1]["entered"] if index+1 < len(samples) else window["finished"]
        diagnostics(sample["closure"], min(upper, case["phases"][3]["end"], case["finished"]))


def _decode_diagnostic(path: Path) -> tuple[object, tuple]:
    """One exported-file decode; bound selected duplicate bookkeeping only."""
    vocabulary = ("diagnostic_read_observation", "source_sha", "failure_categories", "schema", "state", "outcome",
                  "controller", "launcher", "availability", "snapshot", "diagnostic_read", "first_observed",
                  "context_capture", "stage", "error", "at", "launcher_result")
    records = []  # Strong references: dictionary identities cannot be recycled.
    overflow = False
    root_mask = 0
    hidden_member = False

    def pairs(items: list[tuple]) -> dict:
        nonlocal overflow, root_mask, hidden_member
        result = {}
        mask = 0
        hidden = False
        for key, value in items:
            if key in result and key in vocabulary:
                mask |= 1 << vocabulary.index(key)
            if (key == "launcher_result" and type(value) is dict
                    and "diagnostic_read_observation" in value):
                hidden = True  # ANY candidate, before a duplicate parent overwrites it.
            result[key] = value  # Preserve unrelated legacy last-key semantics.
        if mask:
            if len(records) < 16:
                records.append((result, mask))
            else:
                overflow = True
        # For a dictionary root, its hook is last; independent of record overflow.
        root_mask, hidden_member = mask, hidden
        return result

    value = json.loads(path.read_text(encoding="utf-8", errors="strict"), object_pairs_hook=pairs)
    return value, (records, overflow, root_mask, hidden_member, vocabulary)


def _diagnostic_duplicates(value: dict, facts: tuple, keys: tuple[str, ...], *, root: bool = False) -> None:
    records, overflow, root_mask, _hidden, vocabulary = facts
    if overflow:
        raise ValueError("invalid diagnostic metadata")
    mask = root_mask if root else next((mask for item, mask in records if item is value), 0)
    if any(mask & (1 << vocabulary.index(key)) for key in keys):
        raise ValueError("invalid diagnostic metadata")


def _diagnostic_source(value: object, source: str) -> None:
    if (type(value) is not str or len(value) != 40 or any(c not in "0123456789abcdef" for c in value)
            or value != source):
        raise ValueError("invalid diagnostic metadata")


def _diagnostic_projection(result: dict, facts: tuple, source: str, *, root: bool) -> tuple | None:
    """Validate only the fixed-depth new member and its necessary relation."""
    if "diagnostic_read_observation" not in result:
        return None  # Legacy/unobserved, not unreached or diagnostic success.

    def closed(value: object, keys: tuple[str, ...]) -> dict:
        if type(value) is not dict or len(value) != len(keys) or any(key not in value for key in keys):
            raise ValueError("invalid diagnostic metadata")
        _diagnostic_duplicates(value, facts, keys)
        return value

    def schema_source(value: dict) -> None:
        if type(value["schema"]) is not int or value["schema"] != 1:
            raise ValueError("invalid diagnostic metadata")
        _diagnostic_source(value["source_sha"], source)

    def slot(value: object, *, read: bool = False) -> tuple | None:
        if value is None:
            return None
        value = closed(value, ("stage", "error", "at"))
        stage, error, at = (value[key] for key in ("stage", "error", "at"))
        if (type(stage) is not str or len(stage) > 8
                or stage not in ("phase", "bounded", "restore", "context", "action", "teardown", "evidence", "browser", "other")
                or (read and stage != "teardown")
                or type(error) is not str or len(error) > 12
                or error not in ("unicode", "timeout", "interruption", "assertion", "value", "io", "runtime", "other")
                or type(at) not in (int, float) or not 0 <= at <= 9007199254740991):
            raise ValueError("invalid diagnostic metadata")
        # The range check also rejects NaN/infinity without coercing large ints.
        return stage, error, type(at), at

    _diagnostic_duplicates(result, facts, ("diagnostic_read_observation", "source_sha", "failure_categories"), root=root)
    _diagnostic_source(result["source_sha"], source)
    observation = closed(result["diagnostic_read_observation"], ("schema", "source_sha", "state", "outcome"))
    schema_source(observation)
    state, outcome = observation["state"], observation["outcome"]
    if (type(state) is not str or len(state) > 9 or state not in ("unknown", "admitted", "unreached", "completed")
            or (outcome is not None and (type(outcome) is not str or len(outcome) > 18))):
        raise ValueError("invalid diagnostic metadata")
    if state == "completed":
        if outcome not in ("root_unset", "root_missing", "coord_missing", "final_missing", "refused", "available"):
            raise ValueError("invalid diagnostic metadata")
    elif outcome is not None:
        raise ValueError("invalid diagnostic metadata")
    # Serialize only the already bounded four validated values (never arbitrary input).
    if len(json.dumps(observation, separators=(",", ":")).encode("utf-8")) > 256:
        raise ValueError("invalid diagnostic metadata")

    categories = closed(result["failure_categories"], ("schema", "source_sha", "controller", "launcher"))
    schema_source(categories)
    controller = closed(categories["controller"], ("availability", "snapshot"))
    availability = controller["availability"]
    if (type(availability) is not str or len(availability) > 11
            or availability not in ("unavailable", "available")):
        raise ValueError("invalid diagnostic metadata")
    snapshot = controller["snapshot"]
    selected_snapshot = None
    if availability == "unavailable":
        if snapshot is not None:
            raise ValueError("invalid diagnostic metadata")
    else:
        snapshot = closed(snapshot, ("schema", "source_sha", "first_observed", "context_capture"))
        schema_source(snapshot)
        selected_snapshot = (slot(snapshot["first_observed"]), slot(snapshot["context_capture"]))
    launcher = categories["launcher"]
    if type(launcher) is not dict or "diagnostic_read" not in launcher:
        raise ValueError("invalid diagnostic metadata")
    _diagnostic_duplicates(launcher, facts, ("diagnostic_read",))
    read_slot = slot(launcher["diagnostic_read"], read=True)
    if ((state == "unreached" and (availability != "unavailable" or read_slot is not None))
            or (state == "completed" and (availability == "available") != (outcome == "available"))):
        raise ValueError("invalid diagnostic metadata")
    # Sources/schemas are independently exact above; numbers retain exact types.
    return state, outcome, availability, selected_snapshot, read_slot


def _diagnostic_cancellation(raw: object, raw_facts: tuple, row: object, row_facts: tuple, source: str) -> None:
    if type(raw) is not dict or type(row) is not dict or type(row["launcher_result"]) is not dict:
        raise ValueError("invalid diagnostic metadata")
    embedded = row["launcher_result"]
    for result in (raw, row, embedded):
        _diagnostic_source(result["source_sha"], source)
    present = "diagnostic_read_observation" in raw or "diagnostic_read_observation" in embedded
    hidden = row_facts[3]
    if present or hidden:
        _diagnostic_duplicates(raw, raw_facts, ("diagnostic_read_observation", "source_sha", "failure_categories"), root=True)
        _diagnostic_duplicates(row, row_facts, ("launcher_result", "source_sha"), root=True)
        if hidden and "diagnostic_read_observation" not in embedded:
            raise ValueError("invalid diagnostic metadata")
        if ("diagnostic_read_observation" in raw) != ("diagnostic_read_observation" in embedded):
            raise ValueError("invalid diagnostic metadata")
        if (_diagnostic_projection(raw, raw_facts, source, root=True)
                != _diagnostic_projection(embedded, row_facts, source, root=False)):
            raise ValueError("invalid diagnostic metadata")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--controls", action="store_true")
    parser.add_argument("--artifacts", type=Path)
    parser.add_argument("--source")
    parser.add_argument("--event", choices=["schedule", "workflow_dispatch"])
    parser.add_argument("--matrix-result")
    parser.add_argument("--cancellation-result")
    args = parser.parse_args()
    if args.controls:
        print(json.dumps(controls(), indent=2))
        return 0
    if not all((args.artifacts, args.source, args.event, args.matrix_result, args.cancellation_result)):
        parser.error("artifacts, source, event, matrix-result and cancellation-result are required")
    # Keep literal ancestors for completed.directory's no-follow validation.
    args.artifacts = Path.cwd() / args.artifacts
    expected = MANIFEST["daily_python"]
    names = ["e2e-" + version for version in expected] + ["e2e-cancel-TERM", "e2e-cancel-INT"]
    health = dict(status="FAIL", event=None, source_sha=None,
                  requested_event=args.event, requested_source_sha=args.source,
                  cells=expected, artifacts=names, errors=[], completed_cases={})
    try:
        admission = daily_admission(Path(__file__).resolve().parents[1])
        health.update(admission)
        if admission["source_sha"] != args.source or admission["event"] != args.event:
            raise ValueError("requested aggregate event/source differs from admitted Actions identity")
        rows = []
        content_accepted = set()
        required = ["result.json", *BASELINE_ARTIFACTS]
        phase_logs = [name + ".txt" for name in ("dependencies", "browser-dependencies", "browser-install",
                                                "web-dependencies", "web-build", "scenarios")]
        # Missing/empty sanitized artifacts cannot be represented as healthy.
        for version in expected:
            directory = args.artifacts / ("e2e-" + version)
            accepted = None
            try:
                accepted, _ = completed.read_bundle(directory, dict(admission, python=version, exercise="baseline"))
                health["completed_cases"][version] = [dict(row, status="UNKNOWN") for row in accepted["cases"]]
            except (OSError, ValueError, KeyError, TypeError, UnicodeError):
                health["completed_cases"][version] = []
                health["errors"].append(f"e2e-{version}: completed case evidence unavailable")
            required_evidence_accepted = True
            for name in required + phase_logs:
                path = directory / name
                if not path.is_file() or (name.endswith((".json", ".png")) and path.stat().st_size == 0):
                    required_evidence_accepted = False
                    health["errors"].append(f"missing/empty artifact: e2e-{version}/{name}")
            if (directory / "result.json").is_file():
                result = None
                diagnostic_accepted = False
                try:
                    result, diagnostic_facts = _decode_diagnostic(directory / "result.json")
                    if type(result) is not dict:
                        result = None
                        raise ValueError("invalid diagnostic metadata")
                except (OSError, UnicodeError, ValueError, TypeError, RecursionError, OverflowError):
                    health["errors"].append(f"e2e-{version}: invalid diagnostic metadata")
                else:
                    try:
                        _diagnostic_projection(result, diagnostic_facts, args.source, root=True)
                        diagnostic_accepted = True
                    except (ValueError, KeyError, TypeError, IndexError, RecursionError, OverflowError):
                        health["errors"].append(f"e2e-{version}: invalid diagnostic metadata")
                diagnostic_facts = None  # Release this decode's selected duplicate records.
                if result is not None:
                    rows.append(result)
                    if result.get("python") != version or result.get("exercise") != "baseline":
                        health["errors"].append(f"e2e-{version}: directory/interpreter/exercise mismatch")
                    complete_join = False
                    try:
                        if accepted is None:
                            raise ValueError("completed case evidence unavailable")
                        completed.join_results(accepted, result)
                        complete_join = True
                    except (ValueError, KeyError, TypeError, IndexError):
                        health["errors"].append(f"e2e-{version}: complete case/result join unavailable")
                    try:
                        validate_e04(result, json.loads((directory / "observations.json").read_text()),
                                     json.loads((directory / "browser-events.json").read_text()), directory)
                        validate_e05(result, json.loads((directory / "observations.json").read_text()), directory)
                        validate_e07(result, json.loads((directory / "observations.json").read_text()), directory)
                        # No case attribution exists for these lane-wide gates.
                        # Retain UNKNOWN if required evidence was not admitted.
                        if complete_join and required_evidence_accepted and diagnostic_accepted:
                            content_accepted.add(version)
                    except (OSError, ValueError, KeyError, TypeError, IndexError) as exc:
                        health["errors"].append(f"e2e-{version}: {exc}")
        unexpected = set(path.name for path in args.artifacts.glob("e2e-*") if path.is_dir()) - set(names)
        if unexpected:
            health["errors"].append("unexpected artifact directories: " + ", ".join(sorted(unexpected)))
        try:
            validate(rows, expected, args.source, args.matrix_result)
            for row in rows:
                validate_admission(row, admission)
            for version in content_accepted:
                for case in health["completed_cases"][version]:
                    case["status"] = "PASS"  # Full original content gates above, never a marker alone.
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
            raw = row = None
            metadata_invalid = False
            try:
                raw, raw_facts = _decode_diagnostic(directory / "launcher/result.json")
            except (OSError, UnicodeError, ValueError, TypeError, RecursionError, OverflowError):
                metadata_invalid = True
            try:
                row, row_facts = _decode_diagnostic(directory / "cancellation.json")
                if type(row) is not dict:
                    row = None
                    raise ValueError("invalid diagnostic metadata")
            except (OSError, UnicodeError, ValueError, TypeError, RecursionError, OverflowError):
                metadata_invalid = True
            # Independent reads and validation catches: metadata never skips a
            # readable row's original status/admission/signal/timing/closure group.
            if not metadata_invalid:
                try:
                    _diagnostic_cancellation(raw, raw_facts, row, row_facts, args.source)
                except (ValueError, KeyError, TypeError, IndexError, RecursionError, OverflowError):
                    metadata_invalid = True
            if metadata_invalid:
                health["errors"].append(f"{label}: invalid diagnostic metadata")
            raw_facts = row_facts = None  # No parser bookkeeping crosses label validation.
            if row is not None:
                try:
                    if row["status"] != "PASS":
                        raise ValueError("failed cancellation receipt")
                    validate_admission(row, admission)
                    validate_admission(row["launcher_result"], admission)
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
        event_label = health["event"] or "UNVERIFIED"
        event_detail = {"schedule": " (19:00 UTC)", "workflow_dispatch": " (manual main dispatch)"}.get(event_label, "")
        lines = ["## Product E2E health: " + health["status"],
                 "", "Default-branch source: `" + str(health["source_sha"] or "UNVERIFIED") + "`",
                 "Event: `" + event_label + "`" + event_detail,
                 "Run: `" + str(health.get("run_id", "UNVERIFIED")) + "`, attempt: `" + str(health.get("run_attempt", "UNVERIFIED")) + "`",
                 "Source tree: `" + str(health.get("source_tree", "UNVERIFIED")) + "`",
                 "", "Expected artifacts (7-day retention): " + ", ".join("`" + name + "`" for name in names),
                 "", "Baseline jobs: " + args.matrix_result + "; launcher diagnostics: " + args.cancellation_result,
                 "", "Engineering_manager owns triage through existing tasks/threads. This health result does not block merging."]
        lines.extend("\n- " + error.replace("\n", " ") for error in health["errors"])
        with Path(summary).open("a") as stream:
            stream.write("\n".join(lines) + "\n")
    return 0 if health["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
