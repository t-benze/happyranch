from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest


DRIVER = (
    Path(__file__).resolve().parents[1]
    / "runtime/skills/bundled/workspace-cleanup/scripts/run_cleanup_batch.py"
)


def _measurement(decision: str, path: str) -> str:
    return json.dumps({
        "decision": decision,
        "path": path,
        "apparent_bytes_before": 1,
        "allocated_bytes_before": 1,
        "apparent_bytes_after": 0,
        "allocated_bytes_after": 0,
        "filesystem_free_before": 1,
        "filesystem_free_after": 2,
        "filesystem_free_delta": 1,
    }, separators=(",", ":"))


def _stub(tmp_path: Path) -> tuple[Path, Path]:
    log = tmp_path / "runner.log"
    runner = tmp_path / "runner.sh"
    wrong_path = _measurement("removed_worktree", "/fixture/not-current")
    wrong_kind = _measurement("removed_cache", "/fixture/wrong-kind")
    anomaly = json.loads(_measurement("removed_with_anomaly", "/fixture/anomaly"))
    anomaly["anomaly"] = "fixture"
    anomaly["residual_locations"] = {
        name: {
            "path": f"/fixture/{name}", "exists": False,
            "apparent_bytes": 0, "allocated_bytes": 0,
            "measurement_error": None,
        }
        for name in ("original", "isolated_candidate", "isolation_directory_residue")
    }
    anomaly["measurement_error"] = None
    anomaly_wrong_path = {**anomaly, "path": "/fixture/not-current"}
    anomaly_wrong_kind = {
        **anomaly,
        "decision": "isolation_anomaly",
        "path": "/fixture/isolation-anomaly-wrong-kind",
    }
    runner.write_text(
        "#!/bin/bash\n"
        f"printf '%s\\n' \"$1\" >> {log}\n"
        "case \"$1\" in\n"
        "  *crash*) echo boom >&2; exit 7;;\n"
        "  *refuse*) echo '{\"decision\":\"refused\",\"reason\":\"fixture\"}'; exit 2;;\n"
        "  *malformed-three*) echo not-json; exit 3;;\n"
        "  *missing-output*) exit 0;;\n"
        "  *mismatch*) echo '{\"decision\":\"refused\",\"reason\":\"fixture\"}'; exit 0;;\n"
        "  *signal*) kill -TERM $$;;\n"
        f"  *anomaly-wrong-path*) echo '{json.dumps(anomaly_wrong_path, separators=(',', ':'))}'; exit 3;;\n"
        f"  *isolation-anomaly-wrong-kind*) echo '{json.dumps(anomaly_wrong_kind, separators=(',', ':'))}'; exit 3;;\n"
        f"  *anomaly*) echo '{json.dumps(anomaly, separators=(',', ':'))}'; exit 3;;\n"
        f"  *wrong-path*) echo '{wrong_path}'; exit 0;;\n"
        f"  *wrong-kind*) echo '{wrong_kind}'; exit 0;;\n"
        "  *) decision=removed_cache; [ \"$1\" = \"$2\" ] && decision=removed_worktree\n"
        "     printf '{\"decision\":\"%s\",\"path\":\"%s\",\"apparent_bytes_before\":1,\"allocated_bytes_before\":1,\"apparent_bytes_after\":0,\"allocated_bytes_after\":0,\"filesystem_free_before\":1,\"filesystem_free_after\":2,\"filesystem_free_delta\":1}\\n' \"$decision\" \"$1\"; exit 0;;\n"
        "esac\n"
    )
    runner.chmod(0o755)
    return runner, log


def _row(name: str, kind: str, size: int) -> dict:
    return {
        "candidate": f"/fixture/{name}",
        "containing": f"/fixture/{name if kind == 'worktree' else 'TASK-owner'}",
        "kind": kind,
        "allocated_bytes": size,
    }


def _invoke(
    tmp_path: Path, rows: list[dict], *extra: str, runner: Path | None = None,
) -> tuple[subprocess.CompletedProcess[str], list[dict], Path, Path]:
    if runner is None:
        runner, log = _stub(tmp_path)
    else:
        log = tmp_path / "runner.log"
    manifest = tmp_path / "manifest.json"
    journal = tmp_path / "journal.jsonl"
    manifest.write_text(json.dumps(rows))
    result = subprocess.run(
        [sys.executable, str(DRIVER), "--manifest", str(manifest),
         "--journal", str(journal), "--runner", str(runner), *extra],
        capture_output=True, text=True,
    )
    records = (
        [json.loads(line) for line in journal.read_text().splitlines()]
        if journal.exists() else []
    )
    return result, records, runner, log


def _valid_journal_row(
    item: dict, runner: Path, decision: str | None = None,
) -> dict:
    decision = decision or (
        "removed_worktree" if item["kind"] == "worktree" else "removed_cache"
    )
    receipt = (
        {"decision": "refused", "reason": "fixture"}
        if decision == "refused"
        else json.loads(_measurement(decision, item["candidate"]))
    )
    return {
        **item,
        "argv": ["bash", str(runner), item["candidate"], item["containing"]],
        "started_at": "2026-10-01T00:00:00+00:00",
        "ended_at": "2026-10-01T00:00:01+00:00",
        "terminal": True,
        "exit_code": 2 if decision == "refused" else 0,
        "receipt": receipt,
        "stdout": None,
        "stderr": "",
        "error": None,
    }


def test_orders_worktrees_then_caches_largest_first(tmp_path):
    result, records, _, _ = _invoke(tmp_path, [
        _row("cache-small", "cache", 1),
        _row("work-small", "worktree", 2),
        _row("cache-large", "cache", 9),
        _row("work-large", "worktree", 8),
    ])
    assert result.returncode == 0, result.stderr
    assert [Path(row["candidate"]).name for row in records] == [
        "work-large", "work-small", "cache-large", "cache-small",
    ]
    assert all(row["terminal"] is True for row in records)
    assert all(row["argv"][0] == "bash" for row in records)


def test_single_json_object_manifest_is_one_jsonl_row(tmp_path):
    item = _row("one", "worktree", 2)
    runner, log = _stub(tmp_path)
    manifest = tmp_path / "manifest.jsonl"
    journal = tmp_path / "journal.jsonl"
    manifest.write_text(json.dumps(item) + "\n")
    result = subprocess.run(
        [sys.executable, str(DRIVER), "--manifest", str(manifest),
         "--journal", str(journal), "--runner", str(runner)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert log.read_text().splitlines() == [item["candidate"]]
    assert len(journal.read_text().splitlines()) == 1


@pytest.mark.parametrize(
    "name",
    ["wrong-path", "wrong-kind", "anomaly-wrong-path",
     "isolation-anomaly-wrong-kind"],
)
def test_live_receipt_must_match_candidate_path_and_kind(tmp_path, name):
    result, records, _, log = _invoke(tmp_path, [
        _row(name, "worktree", 4),
        _row("must-not-run", "cache", 2),
    ])
    assert result.returncode == 3
    assert len(records) == 1
    assert log.read_text().splitlines() == [f"/fixture/{name}"]
    expected = (
        "exit_3_unclassified" if "anomaly" in name else "unclassifiable_outcome"
    )
    assert json.loads(result.stdout)["stop_reason"] == expected


@pytest.mark.parametrize("fault", ["wrong-path", "wrong-kind"])
def test_journal_receipt_must_match_candidate_path_and_kind(tmp_path, fault):
    item = _row("one", "worktree", 2)
    runner, log = _stub(tmp_path)
    manifest = tmp_path / "manifest.json"
    journal = tmp_path / "journal.jsonl"
    manifest.write_text(json.dumps([item]))
    row = _valid_journal_row(item, runner)
    if fault == "wrong-path":
        row["receipt"]["path"] = "/fixture/not-current"
    else:
        row["receipt"]["decision"] = "removed_cache"
    journal.write_text(json.dumps(row) + "\n")
    result = subprocess.run(
        [sys.executable, str(DRIVER), "--manifest", str(manifest),
         "--journal", str(journal), "--runner", str(runner)],
        capture_output=True, text=True,
    )
    assert result.returncode == 2
    assert "exit/receipt mismatch" in result.stderr
    assert not log.exists()


def test_resume_skips_only_strictly_matching_terminal_candidates(tmp_path):
    rows = [_row("one", "worktree", 2), _row("two", "cache", 1)]
    runner, log = _stub(tmp_path)
    manifest = tmp_path / "manifest.json"
    journal = tmp_path / "journal.jsonl"
    manifest.write_text(json.dumps(rows))
    journal.write_text(json.dumps(_valid_journal_row(rows[0], runner)) + "\n")
    result = subprocess.run(
        [sys.executable, str(DRIVER), "--manifest", str(manifest),
         "--journal", str(journal), "--runner", str(runner)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert log.read_text().splitlines() == ["/fixture/two"]
    assert len(journal.read_text().splitlines()) == 2


def test_stale_manifest_terminal_row_halts_before_runner(tmp_path):
    item = _row("one", "worktree", 2)
    runner, log = _stub(tmp_path)
    manifest = tmp_path / "manifest.json"
    journal = tmp_path / "journal.jsonl"
    manifest.write_text(json.dumps([item]))
    stale = _valid_journal_row({**item, "containing": "/fixture/stale", "kind": "cache"}, runner)
    journal.write_text(json.dumps(stale) + "\n")
    result = subprocess.run(
        [sys.executable, str(DRIVER), "--manifest", str(manifest),
         "--journal", str(journal), "--runner", str(runner)],
        capture_output=True, text=True,
    )
    assert result.returncode == 2
    assert "does not match the manifest" in result.stderr
    assert not log.exists()


def test_malformed_terminal_row_halts_before_runner(tmp_path):
    item = _row("one", "worktree", 2)
    runner, log = _stub(tmp_path)
    manifest = tmp_path / "manifest.json"
    journal = tmp_path / "journal.jsonl"
    manifest.write_text(json.dumps([item]))
    malformed = _valid_journal_row(item, runner)
    malformed["exit_code"] = "0"
    journal.write_text(json.dumps(malformed) + "\n")
    result = subprocess.run(
        [sys.executable, str(DRIVER), "--manifest", str(manifest),
         "--journal", str(journal), "--runner", str(runner)],
        capture_output=True, text=True,
    )
    assert result.returncode == 2
    assert "invalid exit code" in result.stderr
    assert not log.exists()


def test_duplicate_terminal_rows_halt_before_runner(tmp_path):
    item = _row("one", "worktree", 2)
    runner, log = _stub(tmp_path)
    manifest = tmp_path / "manifest.json"
    journal = tmp_path / "journal.jsonl"
    manifest.write_text(json.dumps([item]))
    first = _valid_journal_row(item, runner)
    second = _valid_journal_row(item, runner, "refused")
    journal.write_text(json.dumps(first) + "\n" + json.dumps(second) + "\n")
    result = subprocess.run(
        [sys.executable, str(DRIVER), "--manifest", str(manifest),
         "--journal", str(journal), "--runner", str(runner)],
        capture_output=True, text=True,
    )
    assert result.returncode == 2
    assert "duplicate rows" in result.stderr
    assert not log.exists()


def test_bound_stops_cleanly_between_candidates(tmp_path):
    result, records, _, _ = _invoke(
        tmp_path, [_row("a", "worktree", 3), _row("b", "worktree", 2)],
        "--max-candidates", "1",
    )
    assert result.returncode == 0
    assert len(records) == 1
    assert json.loads(result.stdout)["stop_reason"] == "max_candidates"


def test_deadline_stops_only_between_candidates_and_journal_resumes(tmp_path):
    log = tmp_path / "runner.log"
    runner = tmp_path / "deadline-runner.sh"
    runner.write_text(
        "#!/bin/bash\n"
        f"printf '%s\\n' \"$1\" >> {log}\n"
        "case \"$1\" in *first*) sleep 0.15;; esac\n"
        "echo '{\"decision\":\"refused\",\"reason\":\"fixture\"}'\n"
        "exit 2\n"
    )
    runner.chmod(0o755)
    rows = [_row("first", "worktree", 3), _row("second", "worktree", 2)]
    manifest = tmp_path / "manifest.json"
    journal = tmp_path / "journal.jsonl"
    manifest.write_text(json.dumps(rows))
    command = [
        sys.executable, str(DRIVER), "--manifest", str(manifest),
        "--journal", str(journal), "--runner", str(runner),
        "--deadline-seconds", "0.05", "--candidate-timeout-seconds", "1",
    ]

    first = subprocess.run(command, capture_output=True, text=True)
    assert first.returncode == 0, first.stderr
    assert json.loads(first.stdout) == {
        "invoked": 1, "remaining": 1, "stop_reason": "deadline",
    }
    assert log.read_text().splitlines() == [rows[0]["candidate"]]
    first_record = json.loads(journal.read_text().splitlines()[0])
    assert first_record["receipt"] == {"decision": "refused", "reason": "fixture"}
    assert first_record["error"] is None

    resumed = subprocess.run(command, capture_output=True, text=True)
    assert resumed.returncode == 0, resumed.stderr
    assert "invalid captured output" not in resumed.stderr
    assert json.loads(resumed.stdout) == {
        "invoked": 1, "remaining": 0, "stop_reason": "complete",
    }
    assert log.read_text().splitlines() == [
        rows[0]["candidate"], rows[1]["candidate"],
    ]
    assert len(journal.read_text().splitlines()) == 2


def test_well_formed_refusal_continues_to_next_candidate(tmp_path):
    result, records, _, _ = _invoke(tmp_path, [
        _row("refuse", "worktree", 4),
        _row("success", "cache", 2),
    ])
    assert result.returncode == 0, result.stderr
    assert [row["exit_code"] for row in records] == [2, 0]
    assert [row["receipt"]["decision"] for row in records] == [
        "refused", "removed_cache",
    ]


def test_unreceipted_nonzero_halts_instead_of_error_isolation(tmp_path):
    result, records, _, _ = _invoke(tmp_path, [
        _row("crash", "worktree", 4),
        _row("must-not-run", "cache", 2),
    ])
    assert result.returncode == 3
    assert len(records) == 1
    assert records[0]["exit_code"] == 7
    assert records[0]["receipt"] is None and records[0]["stderr"] == "boom\n"
    assert json.loads(result.stdout)["stop_reason"] == "unclassifiable_outcome"


def test_exit_three_with_malformed_receipt_journals_then_halts(tmp_path):
    result, records, _, _ = _invoke(tmp_path, [
        _row("malformed-three", "worktree", 4),
        _row("must-not-run", "cache", 2),
    ])
    assert result.returncode == 3
    assert len(records) == 1
    assert records[0]["exit_code"] == 3 and records[0]["stdout"] == "not-json\n"
    assert json.loads(result.stdout)["stop_reason"] == "exit_3_unclassified"


def test_signal_death_journals_then_halts(tmp_path):
    result, records, _, _ = _invoke(tmp_path, [
        _row("signal", "worktree", 4),
        _row("must-not-run", "cache", 2),
    ])
    assert result.returncode == 3
    assert len(records) == 1
    assert records[0]["exit_code"] < 0
    assert json.loads(result.stdout)["stop_reason"] == "runner_signal"


def test_missing_or_mismatched_receipt_journals_then_halts(tmp_path):
    for name in ("missing-output", "mismatch"):
        case = tmp_path / name
        case.mkdir()
        result, records, _, _ = _invoke(case, [
            _row(name, "worktree", 4),
            _row("must-not-run", "cache", 2),
        ])
        assert result.returncode == 3
        assert len(records) == 1
        assert json.loads(result.stdout)["stop_reason"] == "unclassifiable_outcome"


def test_driver_exception_is_journaled_then_halts(tmp_path):
    runner, log = _stub(tmp_path)
    manifest = tmp_path / "manifest.json"
    journal = tmp_path / "journal.jsonl"
    manifest.write_text(json.dumps([
        _row("one", "worktree", 4), _row("two", "cache", 2),
    ]))
    result = subprocess.run(
        [sys.executable, str(DRIVER), "--manifest", str(manifest),
         "--journal", str(journal), "--runner", str(runner)],
        capture_output=True, text=True, env={**os.environ, "PATH": ""},
    )
    records = [json.loads(line) for line in journal.read_text().splitlines()]
    assert result.returncode == 3
    assert len(records) == 1
    assert records[0]["exit_code"] is None
    assert records[0]["error"].startswith("runner_error:FileNotFoundError:")
    assert not log.exists()


def test_anomaly_is_journaled_then_halts(tmp_path):
    result, records, _, _ = _invoke(tmp_path, [
        _row("anomaly", "worktree", 4),
        _row("must-not-run", "cache", 3),
    ])
    assert result.returncode == 3
    assert len(records) == 1
    assert records[0]["receipt"]["decision"] == "removed_with_anomaly"
    assert json.loads(result.stdout)["stop_reason"] == "removed_with_anomaly"


def _write_wait_owned_timeout_runner(tmp_path: Path) -> tuple[Path, Path]:
    """Exec the same-group direct wait owner; it never signals its leaf."""
    script = tmp_path / "timeout-owner.py"
    script.write_text(r"""import json
import os
import select
import signal
import sys
import time
from pathlib import Path

entry = time.monotonic()
ready_end = entry + 0.1
term_received = None
leaf_pid = None
raw_status = None
rd = wr = None
facts = {"entry": entry, "failures": [], "ready": False, "raw_status": None}
status_path = Path(__file__).with_suffix(".status.json")
ready_path = Path(__file__).with_suffix(".ready.json")
late_path = Path(__file__).parent / "late-effect"


def identity(pid):
    proc = Path("/proc") / str(pid)
    before = (proc / "stat").read_text().rsplit(") ", 1)[1].split()
    uid = proc.stat(follow_symlinks=False).st_uid
    after = (proc / "stat").read_text().rsplit(") ", 1)[1].split()
    if before[19] != after[19] or before[1:3] != after[1:3]:
        raise RuntimeError("generation bracket changed")
    return {"pid": pid, "starttime": int(after[19]), "ppid": int(after[1]),
            "pgid": int(after[2]), "uid": uid, "state": after[0]}


def term_handler(signum, frame):
    global term_received
    if term_received is None:
        term_received = time.monotonic()


def close_fd(name):
    fd = globals()[name]
    if fd is not None:
        try:
            os.close(fd)
        except OSError as exc:
            facts["failures"].append("close:" + str(exc.errno))
        finally:
            globals()[name] = None


signal.signal(signal.SIGTERM, term_handler)
old_mask = None
try:
    facts["runner"] = identity(os.getpid())
    facts["driver"] = identity(os.getppid())
    rd, wr = os.pipe()
    old_mask = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGTERM})
    if term_received is not None or signal.SIGTERM in signal.sigpending():
        facts["failures"].append("TERM before fork")
    else:
        facts["fork_at"] = time.monotonic()
        leaf_pid = os.fork()
        if leaf_pid == 0:
            os.close(rd)
            signal.signal(signal.SIGTERM, signal.SIG_DFL)
            signal.pthread_sigmask(signal.SIG_SETMASK, old_mask)
            os.close(1)
            os.close(2)
            action_start = time.monotonic()
            try:
                payload = json.dumps({"identity": identity(os.getpid()),
                                      "action_start": action_start}).encode() + b"\n"
                if len(payload) > 4096 or os.write(wr, payload) != len(payload):
                    os._exit(4)
            finally:
                os.close(wr)
            while time.monotonic() < action_start + 0.4:
                time.sleep(max(0, action_start + 0.4 - time.monotonic()))
            late_path.touch()
            os._exit(0)
        # From here this parent is the sole exact-leaf wait owner.
        facts["leaf_pid"] = leaf_pid
        close_fd("wr")
        signal.pthread_sigmask(signal.SIG_SETMASK, old_mask)
        old_mask = None
        os.set_blocking(rd, False)
        data = b""
        while b"\n" not in data and time.monotonic() < ready_end:
            got, status = os.waitpid(leaf_pid, os.WNOHANG)
            if got == leaf_pid:
                raw_status = status
                facts["wait_done"] = time.monotonic()
                facts["failures"].append("leaf exited before readiness")
                break
            if got != 0:
                raise RuntimeError("unexpected readiness wait pid")
            remaining = ready_end - time.monotonic()
            if remaining <= 0:
                break
            if not select.select([rd], [], [], remaining)[0]:
                break
            chunk = os.read(rd, 4096 - len(data))
            if not chunk:
                break
            data += chunk
            if len(data) >= 4096:
                raise RuntimeError("oversized readiness")
        if raw_status is None and data.endswith(b"\n"):
            ready = json.loads(data)
            facts["leaf_ready"] = ready
            actual = identity(leaf_pid)
            facts["leaf_observed"] = actual
            expected = ready["identity"]
            keys = ("pid", "starttime", "ppid", "pgid", "uid")
            facts["ready"] = (
                all(actual[key] == expected[key] for key in keys)
                and actual["ppid"] == os.getpid()
                and actual["pgid"] == os.getpid()
                and actual["uid"] == os.getuid()
                and actual["state"] not in ("Z", "X")
                and facts["runner"]["pgid"] == os.getpid()
                and facts["runner"]["ppid"] == facts["driver"]["pid"]
                and term_received is None
                and time.monotonic() <= ready_end
            )
            facts["ready_at"] = time.monotonic()
        if not facts["ready"]:
            facts["failures"].append("incomplete/late/nonlive readiness")
        with ready_path.open("w") as stream:
            json.dump(facts, stream)
except BaseException as exc:
    facts["failures"].append("setup:" + type(exc).__name__)
finally:
    close_fd("rd")
    close_fd("wr")
    if old_mask is not None:
        try:
            signal.pthread_sigmask(signal.SIG_SETMASK, old_mask)
        except BaseException as exc:
            facts["failures"].append("mask:" + type(exc).__name__)
    if leaf_pid is not None and leaf_pid > 0:
        facts["wait_enter"] = time.monotonic()
        action_start = facts.get("leaf_ready", {}).get("action_start", facts["fork_at"])
        wait_end = action_start + 0.4 + 2.0
        while raw_status is None and time.monotonic() < wait_end:
            try:
                got, status = os.waitpid(leaf_pid, os.WNOHANG)
            except InterruptedError:
                continue
            except OSError as exc:
                facts["failures"].append("wait:" + str(exc.errno))
                break
            if got == leaf_pid:
                raw_status = status
                facts["wait_done"] = time.monotonic()
            elif got != 0:
                facts["failures"].append("unexpected wait pid")
                break
            else:
                time.sleep(min(0.001, max(0, wait_end - time.monotonic())))
        if raw_status is None:
            facts["failures"].append("unsettled leaf")
    facts["raw_status"] = raw_status
    facts["term_received"] = term_received
    facts["readiness_fds_closed"] = rd is None and wr is None
    facts["status_written_at"] = time.monotonic()
    try:
        with status_path.open("w") as stream:
            json.dump(facts, stream)
    except BaseException:
        os._exit(5)

if term_received is not None or signal.SIGTERM in signal.sigpending():
    signal.signal(signal.SIGTERM, signal.SIG_DFL)
    signal.pthread_sigmask(signal.SIG_UNBLOCK, {signal.SIGTERM})
    os.kill(os.getpid(), signal.SIGTERM)
os._exit(4 if facts["failures"] else 0)
""")
    runner = tmp_path / "timeout-runner.sh"
    runner.write_text(
        "#!/bin/bash\n"
        "case \"$1\" in\n"
        f'  *timeout*) exec "{sys.executable}" "{script}" "$@";;\n'
        f'  *) touch "{tmp_path / "next-effect"}"; '
        "echo '{\"decision\":\"refused\",\"reason\":\"fixture\"}'; exit 2;;\n"
        "esac\n"
    )
    runner.chmod(0o755)
    return runner, script


def test_timeout_kills_process_group_before_journaling_and_halts(tmp_path):
    late_effect = tmp_path / "late-effect"
    next_effect = tmp_path / "next-effect"
    runner, script = _write_wait_owned_timeout_runner(tmp_path)
    result, records, _, _ = _invoke(
        tmp_path,
        [_row("timeout", "worktree", 4), _row("next", "cache", 2)],
        "--candidate-timeout-seconds", "0.1", runner=runner,
    )
    time.sleep(0.6)
    # Capture actual qualification facts before the first maintained assertion.
    facts = {}
    reads = {}
    for suffix in (".ready.json", ".status.json"):
        path = script.with_suffix(suffix)
        try:
            reads[suffix] = json.loads(path.read_text())
        except (OSError, ValueError) as exc:
            reads[suffix] = {"missing_or_invalid": type(exc).__name__}
    facts.update({"observations": reads, "driver_returncode": result.returncode,
                  "records": records, "stdout": result.stdout, "stderr": result.stderr,
                  "late_effect": late_effect.exists(), "next_effect": next_effect.exists()})
    (tmp_path / "timeout-qualification.json").write_text(json.dumps(facts, indent=2))
    print("timeout owned qualification:", json.dumps(facts))
    assert result.returncode == 3
    assert len(records) == 1
    assert records[0]["error"] == "timeout_group_terminated"
    assert records[0]["exit_code"] < 0
    assert not late_effect.exists()
    assert not next_effect.exists()
    assert json.loads(result.stdout)["stop_reason"] == "timeout_group_terminated"

    status = reads[".status.json"]
    assert status["ready"] and not status["failures"]
    assert status["readiness_fds_closed"]
    assert status["runner"]["ppid"] == status["driver"]["pid"]
    assert status["leaf_observed"]["ppid"] == status["runner"]["pid"]
    assert status["leaf_observed"]["pgid"] == status["runner"]["pid"]
    assert status["leaf_observed"]["uid"] == status["runner"]["uid"] == os.getuid()
    assert os.WIFSIGNALED(status["raw_status"])
    assert os.WTERMSIG(status["raw_status"]) == 15
    assert records[0]["exit_code"] == -15
    assert status["ready_at"] < status["term_received"] <= status["wait_done"]
    assert status["wait_done"] - status["term_received"] < 0.1
    assert status["wait_done"] <= status["status_written_at"]
