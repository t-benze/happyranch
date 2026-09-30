from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path


DRIVER = (
    Path(__file__).resolve().parents[1]
    / "runtime/skills/bundled/workspace-cleanup/scripts/run_cleanup_batch.py"
)


def _measurement(decision: str) -> str:
    return json.dumps({
        "decision": decision,
        "path": "/fixture/result",
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
    success = _measurement("removed_cache")
    anomaly = json.loads(_measurement("removed_with_anomaly"))
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
        f"  *anomaly*) echo '{json.dumps(anomaly, separators=(',', ':'))}'; exit 3;;\n"
        f"  *) echo '{success}'; exit 0;;\n"
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


def _valid_journal_row(item: dict, runner: Path, decision: str = "removed_cache") -> dict:
    receipt = (
        {"decision": "refused", "reason": "fixture"}
        if decision == "refused" else json.loads(_measurement(decision))
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


def test_timeout_kills_process_group_before_journaling_and_halts(tmp_path):
    late_effect = tmp_path / "late-effect"
    next_effect = tmp_path / "next-effect"
    runner = tmp_path / "timeout-runner.sh"
    runner.write_text(
        "#!/bin/bash\n"
        "case \"$1\" in\n"
        f"  *timeout*) (sleep 0.4; touch {late_effect}) & wait;;\n"
        f"  *) touch {next_effect}; echo '{{\"decision\":\"refused\",\"reason\":\"fixture\"}}'; exit 2;;\n"
        "esac\n"
    )
    runner.chmod(0o755)
    result, records, _, _ = _invoke(
        tmp_path,
        [_row("timeout", "worktree", 4), _row("next", "cache", 2)],
        "--candidate-timeout-seconds", "0.1", runner=runner,
    )
    time.sleep(0.6)
    assert result.returncode == 3
    assert len(records) == 1
    assert records[0]["error"] == "timeout_group_terminated"
    assert records[0]["exit_code"] < 0
    assert not late_effect.exists()
    assert not next_effect.exists()
    assert json.loads(result.stdout)["stop_reason"] == "timeout_group_terminated"
