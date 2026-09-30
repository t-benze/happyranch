from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


DRIVER = (
    Path(__file__).resolve().parents[1]
    / "runtime/skills/bundled/workspace-cleanup/scripts/run_cleanup_batch.py"
)


def _stub(tmp_path: Path) -> tuple[Path, Path]:
    log = tmp_path / "runner.log"
    runner = tmp_path / "runner.sh"
    runner.write_text(
        "#!/bin/sh\n"
        f"printf '%s\\n' \"$1\" >> {log}\n"
        "case \"$1\" in\n"
        "  *crash*) echo boom >&2; exit 7;;\n"
        "  *refuse*) echo '{\"decision\":\"refused\",\"reason\":\"fixture\"}'; exit 2;;\n"
        "  *malformed*) echo not-json; exit 4;;\n"
        "  *anomaly*) echo '{\"decision\":\"removed_with_anomaly\",\"anomaly\":\"fixture\"}'; exit 3;;\n"
        "  *) echo '{\"decision\":\"removed_cache\",\"path\":\"fixture\"}'; exit 0;;\n"
        "esac\n"
    )
    runner.chmod(0o755)
    return runner, log


def _run(tmp_path: Path, rows: list[dict], *extra: str) -> tuple[subprocess.CompletedProcess[str], list[dict]]:
    runner, _ = _stub(tmp_path)
    manifest = tmp_path / "manifest.json"
    journal = tmp_path / "journal.jsonl"
    manifest.write_text(json.dumps(rows))
    result = subprocess.run(
        [sys.executable, str(DRIVER), "--manifest", str(manifest),
         "--journal", str(journal), "--runner", str(runner), *extra],
        capture_output=True, text=True,
    )
    records = [json.loads(line) for line in journal.read_text().splitlines()]
    return result, records


def _row(name: str, kind: str, size: int) -> dict:
    return {
        "candidate": f"/fixture/{name}",
        "containing": f"/fixture/{name if kind == 'worktree' else 'TASK-owner'}",
        "kind": kind,
        "allocated_bytes": size,
    }


def test_orders_worktrees_then_caches_largest_first(tmp_path):
    result, records = _run(tmp_path, [
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


def test_resume_skips_terminal_candidates(tmp_path):
    rows = [_row("one", "worktree", 2), _row("two", "cache", 1)]
    first, first_records = _run(tmp_path, rows, "--max-candidates", "1")
    assert first.returncode == 0
    runner, _ = _stub(tmp_path)
    manifest = tmp_path / "manifest.json"
    journal = tmp_path / "journal.jsonl"
    second = subprocess.run(
        [sys.executable, str(DRIVER), "--manifest", str(manifest),
         "--journal", str(journal), "--runner", str(runner)],
        capture_output=True, text=True,
    )
    records = [json.loads(line) for line in journal.read_text().splitlines()]
    assert second.returncode == 0
    assert len(first_records) == 1
    assert [Path(row["candidate"]).name for row in records] == ["one", "two"]


def test_bound_stops_cleanly_between_candidates(tmp_path):
    result, records = _run(
        tmp_path, [_row("a", "worktree", 3), _row("b", "worktree", 2)],
        "--max-candidates", "1",
    )
    assert result.returncode == 0
    assert len(records) == 1
    assert json.loads(result.stdout)["stop_reason"] == "max_candidates"


def test_error_isolation_continues_after_crash_and_refusal(tmp_path):
    result, records = _run(tmp_path, [
        _row("crash", "worktree", 4),
        _row("refuse", "cache", 3),
        _row("success", "cache", 2),
    ])
    assert result.returncode == 0
    assert [row["exit_code"] for row in records] == [7, 2, 0]
    assert records[0]["receipt"] is None and records[0]["stderr"] == "boom\n"
    assert records[1]["receipt"]["decision"] == "refused"
    assert records[2]["receipt"]["decision"] == "removed_cache"


def test_anomaly_is_journaled_then_halts(tmp_path):
    result, records = _run(tmp_path, [
        _row("anomaly", "worktree", 4),
        _row("must-not-run", "cache", 3),
    ])
    assert result.returncode == 3
    assert len(records) == 1
    assert records[0]["receipt"]["decision"] == "removed_with_anomaly"
    assert json.loads(result.stdout)["stop_reason"] == "removed_with_anomaly"
