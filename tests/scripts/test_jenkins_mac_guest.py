from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shlex
import socket
import sqlite3
import subprocess
import sys
import time

import pytest

from scripts import jenkins_mac_guest as guest
from runtime.infrastructure.database import Database


def _owned_node(tmp_path: Path, *, alpha_status: str = "failed", large_note: bool = False) -> tuple[Path, Path]:
    base = tmp_path / "happyranch-pytest"
    node = base / "test_two_orgs_run_tasks_concur0"
    for org, status in (("alpha", alpha_status), ("beta", "completed")):
        directory = node / "runtime/orgs" / org
        directory.mkdir(parents=True)
        db = Database(directory / "happyranch.db")
        db._conn.execute(
            "INSERT INTO tasks(id,status,brief,created_at,updated_at,note) VALUES (?,?,?,?,?,?)",
            ("TASK-001", status, "PRIVATE_PROMPT_CANARY", "now", "now",
             "agent invocation failed: Authorization: Bearer SECRET_TOKEN_CANARY" + ("x" * 9000 if large_note else "")),
        )
        db._conn.execute(
            "INSERT INTO task_results(task_id,agent,session_id,created_at) VALUES (?,?,?,?)",
            ("TASK-001", "engineering_head", "SECRET_SESSION", "now"),
        )
        db._conn.execute(
            "INSERT INTO audit_log(task_id,agent,action,timestamp) VALUES (?,?,?,?)",
            ("TASK-001", "engineering_head", "session_start", "now"),
        )
        db._conn.commit()
        db.close()
    (node / ".happyranch").mkdir()
    (node / ".happyranch/daemon.log").write_text(
        "WorkspaceNotInitialized: PRIVATE_KEY_CANARY\n"
        "raw prompt SECRET_TOKEN_CANARY\n" * 400
    )
    (node / "credentials").write_text("UNRELATED_CREDENTIAL_CANARY")
    return base, node


def _file_facts(root: Path) -> dict[str, tuple[str, int]]:
    return {str(path.relative_to(root)): (hashlib.sha256(path.read_bytes()).hexdigest(),
                                        path.stat().st_mode)
            for path in root.rglob("*") if path.is_file() and not path.is_symlink()}


@pytest.mark.parametrize("alpha_status,large_note", [("failed", False), ("running", False), ("completed", False), ("failed", True)])
def test_diagnostics_correlate_orgs_omit_secrets_and_leave_inputs_unchanged(
    tmp_path: Path, alpha_status: str, large_note: bool,
) -> None:
    base, _ = _owned_node(tmp_path, alpha_status=alpha_status, large_note=large_note)
    before = _file_facts(base)
    receipt = guest.capture_diagnostics(base, deadline=time.monotonic() + 30)
    orgs = receipt["nodes"][0]["orgs"]
    assert all("tasks" in row for row in orgs[:2]), orgs
    assert [(row["org"], row["tasks"][0]["task_id"], row["tasks"][0]["status"])
            for row in orgs[:2]] == [("alpha", "TASK-001", alpha_status), ("beta", "TASK-001", "completed")]
    for row in orgs[:2]:
        assert row["tasks"][0] == {
            "task_id": "TASK-001", "status": row["tasks"][0]["status"],
            "note_category": "omitted" if large_note else "agent_invocation_failed", "result_count": 1,
            "session_start_count": 1, "executor_evidence": "unavailable",
        }
    assert receipt["cause"] == "unknown"
    assert receipt["nodes"][0]["event_categories"] == ["workspace_not_initialized"] * 32
    assert receipt["nodes"][0]["log_truncated"] is True
    encoded = json.dumps(receipt)
    assert "CANARY" not in encoded
    assert "SECRET_SESSION" not in encoded
    assert len(encoded.encode()) < 65536
    assert _file_facts(base) == before


@pytest.mark.parametrize("hazard", ["node_symlink", "db_symlink", "wal_symlink", "unknown_schema", "replacement"])
def test_diagnostics_refuse_unsafe_or_changed_inputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, hazard: str,
) -> None:
    base, node = _owned_node(tmp_path)
    db = node / "runtime/orgs/alpha/happyranch.db"
    if hazard == "node_symlink":
        moved = tmp_path / "outside"
        node.rename(moved)
        node.symlink_to(moved, target_is_directory=True)
    elif hazard == "db_symlink":
        moved = tmp_path / "outside.db"
        db.rename(moved)
        db.symlink_to(moved)
    elif hazard == "wal_symlink":
        (tmp_path / "outside-wal").write_text("OUTSIDE_SECRET_CANARY")
        db.with_name("happyranch.db-wal").symlink_to(tmp_path / "outside-wal")
    elif hazard == "unknown_schema":
        connection = sqlite3.connect(db)
        try:
            connection.execute("ALTER TABLE tasks RENAME COLUMN note TO mystery")
            connection.commit()
        finally:
            connection.close()
    else:
        original = guest.sqlite3.connect
        def replace_before_query(*args: object, **kwargs: object) -> sqlite3.Connection:
            connection = original(*args, **kwargs)
            if "alpha" in os.readlink(str(args[0]).split("file:", 1)[1].split("?", 1)[0].rsplit("/", 1)[0]):
                db.rename(db.with_suffix(".saved"))
                db.write_text("replacement")
            return connection
        monkeypatch.setattr(guest.sqlite3, "connect", replace_before_query)
    receipt = guest.capture_diagnostics(base, deadline=time.monotonic() + 30)
    node_receipt = receipt["nodes"][0]
    if hazard == "node_symlink":
        assert node_receipt["unavailable"] == "unsafe_or_changed"
    else:
        assert node_receipt["orgs"][0].get("unavailable") == {
            "db_symlink": "unsafe_or_unreadable", "wal_symlink": "unsafe_sidecar",
            "unknown_schema": "unknown_schema", "replacement": "file_changed",
        }[hazard]
        assert "tasks" not in node_receipt["orgs"][0]
    assert "CANARY" not in json.dumps(receipt)


def test_diagnostics_sqlite_busy_deadline_and_row_node_caps(tmp_path: Path) -> None:
    base, node = _owned_node(tmp_path)
    db = node / "runtime/orgs/alpha/happyranch.db"
    connection = sqlite3.connect(db)
    try:
        connection.execute("PRAGMA journal_mode=DELETE")
        connection.execute("BEGIN EXCLUSIVE")
        started = time.monotonic()
        receipt = guest.capture_diagnostics(base, deadline=started + 30)
        assert 1.5 < time.monotonic() - started < 2.8
        assert receipt["nodes"][0]["orgs"][0]["unavailable"] == "sqlite_unavailable"
    finally:
        connection.rollback()
        connection.close()
    with sqlite3.connect(db) as connection:
        for index in range(2, 13):
            connection.execute("INSERT INTO tasks(id,brief,created_at,updated_at) VALUES (?,?,?,?)",
                               (f"TASK-{index:03}", "secret", "now", "now"))
    for index in range(1, 6):
        (base / f"test_two_orgs_run_tasks_concur{index}").mkdir()
    receipt = guest.capture_diagnostics(base, deadline=time.monotonic() + 30)
    assert len(receipt["nodes"]) == 4
    assert receipt["nodes_truncated"] is True
    assert receipt["nodes"][0]["orgs"][0]["tasks_truncated"] is True
    assert sum(len(org.get("tasks", [])) for org in receipt["nodes"][0]["orgs"]) == 8


@pytest.mark.parametrize("output,status,expected", [
    ("", 7, "nonzero"), ("not an address", 0, "malformed"),
    ("1: lo inet 127.0.0.1/8 scope host lo\n", 0, "loopback_only"),
    ("2: eth0 inet 0.0.0.0/24 scope global eth0\n", 0, "validator_rejected"),
    ("2: eth0 inet 192.0.2.43/24 scope global eth0\n", 0, "address"),
])
def test_guest_ipv4_uses_ip_shipping_validator_and_real_bind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, output: str, status: int, expected: str,
) -> None:
    path = tmp_path / "ip"
    path.write_text(f"#!/bin/sh\nprintf '%s' {shlex.quote(output)}\nexit {status}\n")
    path.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))
    receipt = guest._ipv4(time.monotonic() + 90)
    assert receipt["discovery"] == expected
    assert receipt["command_status"] == status
    if expected == "address":
        assert receipt["validator"] == "accepted"
        assert receipt["bind"] == "failed"  # Documentation-only address is not on this host.
    assert receipt["bind"] != "bound"


def test_guest_ipv4_valid_local_address_binds_and_missing_executable_is_distinct(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Use a measured host interface solely for safe ephemeral unit bind; no daemon/network changes.
    addresses = subprocess.run(["hostname", "-I"], capture_output=True, text=True, timeout=2).stdout.split()
    address = next(value for value in addresses if ":" not in value and not value.startswith("127."))
    path = tmp_path / "ip"
    path.write_text(f"#!/bin/sh\nprintf '2: eth0 inet {address}/24 scope global eth0\\n'\n")
    path.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))
    receipt = guest._ipv4(time.monotonic() + 90)
    assert receipt["address"] == address
    assert receipt["bind"] == "bound"
    with socket.socket() as listener:
        listener.bind((address, 0))
    path.unlink()
    receipt = guest._ipv4(time.monotonic() + 90)
    assert receipt["discovery"] == "executable_unavailable"
    assert receipt["bind"] == "not_run"


@pytest.mark.parametrize("scenario", ["timeout", "output_cap", "nonzero", "remaining_budget"])
def test_guest_deadline_stops_only_owned_self_expiring_processes(tmp_path: Path, scenario: str) -> None:
    pid_file = tmp_path / "pid"
    body = ("import os,signal,time,sys\nsignal.alarm(8)\n"
            f"open({str(pid_file)!r},'w').write(str(os.getpid()))\n")
    if scenario == "output_cap":
        body += "print('x'*9000,flush=True)\ntime.sleep(6)\n"
    elif scenario == "nonzero":
        body += "raise SystemExit(7)\n"
    else:
        body += "time.sleep(6)\n"
    started = time.monotonic()
    code, output, reason = guest.run_workload(
        [sys.executable, "-c", body], seconds=2.2, deadline=started + (2.2 if scenario == "remaining_budget" else 90),
        reserve=1 if scenario == "remaining_budget" else 0, output=True,
    )
    assert time.monotonic() - started < 3
    assert code == {"timeout": 124, "output_cap": 83, "nonzero": 7, "remaining_budget": 124}[scenario]
    assert reason == {"timeout": "timeout", "output_cap": "output_cap", "nonzero": "nonzero",
                      "remaining_budget": "budget_exhausted"}[scenario]
    assert len(output) <= 8192
    if pid_file.exists():
        with pytest.raises(ProcessLookupError):
            os.kill(int(pid_file.read_text()), 0)
    else:
        assert scenario == "remaining_budget"


def test_ipv4_probe_timeout_is_distinct_and_child_reaped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "ip"
    pid = tmp_path / "pid"
    path.write_text(f"#!{sys.executable}\nimport os,signal,time\nsignal.alarm(5)\n"
                    f"open({str(pid)!r},'w').write(str(os.getpid()))\ntime.sleep(4)\n")
    path.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))
    started = time.monotonic()
    receipt = guest._ipv4(started + 62.3)
    assert receipt["discovery"] == "timeout"
    assert receipt["command_status"] == 124
    assert receipt["bind"] == "not_run"
    assert time.monotonic() - started < 2
    with pytest.raises(ProcessLookupError):
        os.kill(int(pid.read_text()), 0)


def test_diagnostics_refuse_live_wal_without_ignoring_or_mutating_it(tmp_path: Path) -> None:
    base, node = _owned_node(tmp_path)
    db = node / "runtime/orgs/alpha/happyranch.db"
    connection = sqlite3.connect(db)
    try:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("UPDATE tasks SET status='running' WHERE id='TASK-001'")
        connection.commit()
        before = _file_facts(base)
        receipt = guest.capture_diagnostics(base, deadline=time.monotonic() + 30)
        assert receipt["nodes"][0]["orgs"][0].get("unavailable") == "wal_evidence_unavailable"
        assert _file_facts(base) == before
    finally:
        connection.close()
