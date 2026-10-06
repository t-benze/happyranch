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
from runtime.models import TaskRecord, TaskStatus


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
    facts = {}
    for path in root.rglob("*"):
        info = path.lstat()
        if path.is_symlink():
            data = os.readlink(path).encode()
        elif path.is_file():
            data = path.read_bytes()
        elif path.is_dir():
            data = b""
        else:
            continue
        facts[str(path.relative_to(root))] = (hashlib.sha256(data).hexdigest(), info.st_mode)
    return facts


# Independent shipping-receipt inputs, not an oracle derived from guest constants.
_NOTE_CASES = [
    ("I0-class", "WorkspaceNotInitialized", "workspace_not_initialized"),
    ("I0-class-envelope", "agent invocation failed: WorkspaceNotInitialized", "workspace_not_initialized"),
    ("I0-selector", "authority selector is uninitialized", "authority_selector_uninitialized"),
    ("I0-selector-envelope", "agent invocation failed: authority selector is uninitialized", "authority_selector_uninitialized"),
    ("I0-skill", "SymlinkMaterializationError", "skill_materialization_failed"),
    ("I0-skill-envelope", "agent invocation failed: SymlinkMaterializationError", "skill_materialization_failed"),
    ("I0-executor", "executor not found", "executor_missing"),
    ("I0-executor-envelope", "agent invocation failed: executor not found", "executor_missing"),
    ("I0-session", "session_mismatch", "callback_session_mismatch"),
    ("I0-session-envelope", "agent invocation failed: session_mismatch", "callback_session_mismatch"),
    ("I1", "agent invocation failed: workspace CANARY does not have the canonical AGENTS.md/CLAUDE.md instruction pair /CANARY", "workspace_instruction_pair_refused"),
    ("I1-alone", "workspace CANARY does not have the canonical AGENTS.md/CLAUDE.md instruction pair", "workspace_instruction_pair_refused"),
    ("I2", "agent invocation failed: workspace CANARY is not initialized (missing /CANARY)", "workspace_not_initialized"),
    ("I2-alone", "workspace CANARY is not initialized (missing /CANARY)", "workspace_not_initialized"),
    ("I3", "agent invocation failed: CANARY\nworkspace CANARY is not initialized (missing /CANARY)", "workspace_not_initialized"),
    ("I3-pair", "agent invocation failed: CANARY\nworkspace CANARY does not have the canonical pair", "workspace_instruction_pair_refused"),
    ("I4", "is not initialized (missing CANARY) does not have the canonical pair", "workspace_instruction_pair_refused"),
    ("I4-reverse", "does not have the canonical pair is not initialized (missing CANARY)", "workspace_instruction_pair_refused"),
    ("I5", "executor not found SymlinkMaterializationError", "skill_materialization_failed"),
    ("I5-reverse", "SymlinkMaterializationError executor not found", "skill_materialization_failed"),
    ("I6-envelope", "agent invocation failed: PRIVATE_EXCEPTION_CANARY", "agent_invocation_failed"),
    ("I6-unknown", "PRIVATE_EXCEPTION_CANARY", "omitted"),
    ("I6-empty", "", "none"), ("I6-null", None, "none"),
    ("I7-limit", "WorkspaceNotInitialized" + "x" * (8192 - 23), "workspace_not_initialized"),
    ("I7-over", "WorkspaceNotInitialized" + "x" * (8193 - 23), "omitted"),
    ("I8", "session_mismatch prompt CANARY token SECRET_SESSION /CANARY Traceback argv", "callback_session_mismatch"),
    ("I8-invalid", b"WorkspaceNotInitialized \xff CANARY", "workspace_not_initialized"),
]


def _diagnostic_node(
    base: Path, name: str, org: str,
    rows: list[tuple[str, str, int, int, object]], log: bytes = b"",
) -> Path:
    """Seed through production DDL/writers, then close before shipping collection."""
    node = base / name
    directory = node / "runtime/orgs" / org
    directory.mkdir(parents=True, exist_ok=True)
    db = Database(directory / "happyranch.db")
    try:
        for task_id, status, results, starts, note in rows:
            db.insert_task(TaskRecord(id=task_id, brief="PRIVATE_PROMPT_CANARY",
                                      parent_task_id="TASK-010" if task_id == "TASK-011" else None))
            # The writer admits real enums; literal SQL is confined to legacy/invalid inputs.
            if status in {"running", "blocked", "future_unknown"}:
                db._conn.execute("UPDATE tasks SET status=? WHERE id=?", (status, task_id))
                db._conn.commit()
            else:
                db.update_task(task_id, status=TaskStatus(status))
            db.update_task(task_id, note=note)
            for index in range(results):
                db.insert_task_result(task_id, "worker", f"SECRET_SESSION-{index}", "CANARY", 0)
            for _ in range(starts):
                db.insert_audit_log(task_id, "worker", "session_start", {"secret": "CANARY"})
            db.insert_audit_log(task_id, "worker", "unrelated", {"secret": "CANARY"})
    finally:
        db.close()
    (node / ".happyranch").mkdir(exist_ok=True)
    (node / ".happyranch/daemon.log").write_bytes(log)
    return node


def _serialized_capture(base: Path, *, deadline: float | None = None) -> dict:
    before = _file_facts(base)
    encoded = json.dumps(guest.capture_diagnostics(
        base, deadline=time.monotonic() + 30 if deadline is None else deadline))
    assert "CANARY" not in encoded and "SECRET_SESSION" not in encoded
    assert len(encoded.encode()) < 65536
    receipt = json.loads(encoded)
    assert receipt["cause"] == "unknown" and receipt["raw_text"] == "omitted"
    assert _file_facts(base) == before
    return receipt


def _window_cases() -> list[tuple[str, bytes, list[str], int, int]]:
    # Each B/R oracle is explicit byte arithmetic from stipulated physical offsets.
    marker = b"WorkspaceNotInitialized"
    event = "workspace_not_initialized"
    rows = [("W0", b"", [], 0, 0),
            ("W1", marker + b"\n" + marker, [event, event], 0, 47)]
    for size in (4096, 4097, 8191, 8192):
        rows.append((f"W2-{size}", b"q" * (size - 24) + b"\n" + marker, [event], 0, size))
    rows.extend([
        ("W3", marker + b"\n" + b"q" * (4096 - 24) + b"x" + b"r" * 31 + b"\n" + b"s" * 4063 + b"\n", [event], 4104, 8192),
        ("W4-head", marker + b"\n" + b"q" * (4096 - 24) + b"x" * 808 + b"r\n" + b"s" * 4093 + b"\n", [event], 4074, 8192),
        # Marker starts at byte 8977: outside the old first8192, after tail's firstLF.
        ("W4-tail", b"q" * 4095 + b"\n" + b"x" * 808 + b"r\n" + b"s" * 4070 + b"\n" + marker, [event], 2, 8192),
        ("W5", b"q" * 4095 + b"\n" + b"x" * 808 + b"r" * 31 + b"\n" + marker + b"\n" + b"s" * 4039 + b"\n", [event], 32, 8192),
        ("W6-head", b"q" * 4073 + marker + b"x" * 808 + b"r\n" + b"s" * 4093 + b"\n", [], 4098, 8192),
        ("W6-tail", b"q" * 4095 + b"\n" + b"x" * 808 + marker + b"r\n" + b"s" * 4070 + b"\n", [], 25, 8192),
        ("W7", b"q" * 4087 + b"Workspace" + b"x" * 808 + b"NotInitialized\n" + b"s" * 4080 + b"\n", [], 4111, 8192),
        ("W8", b"\xff" + marker + b"\nWorkspaceNotInit\xffialized", [event], 0, 49),
        ("W8-disjoint", b"q" * 4095 + b"\n" + b"x" * 808 + b"\xff\n" + marker + b" \xff\n" + b"s" * 4067 + b"\n", [event], 2, 8192),
        ("W9", b"q" * 9000, [], 8192, 8192),
        ("W10-32", (marker + b"\n") * 32, [event] * 32, 0, 768),
        ("W10-33", (marker + b"\n") * 33, [event] * 32, 0, 792),
        ("W11-contiguous", b"q" * 4087 + marker + b"s" * 4082, [event], 0, 8192),
        ("W11-disjoint", b"q" * 4087 + marker + b"s" * 4083, [], 8192, 8192),
        ("W-LF-only", b"Workspace\rNotInitialized\n", [], 0, 25),
    ])
    return rows


@pytest.mark.parametrize("alpha_status,large_note,diagnostic_case", [
    ("failed", False, "original"), ("running", False, "original"),
    ("completed", False, "original"), ("failed", True, "original"),
] + [("failed", False, row[0]) for row in _NOTE_CASES + _window_cases()])
def test_diagnostics_correlate_orgs_omit_secrets_and_leave_inputs_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, alpha_status: str, large_note: bool, diagnostic_case: str,
) -> None:
    if diagnostic_case != "original":
        base = tmp_path / "cases"
        notes = {row[0]: row[1:] for row in _NOTE_CASES}
        if diagnostic_case in notes:
            note, expected = notes[diagnostic_case]
            log = (note if isinstance(note, bytes) else (note or "").encode())
            _diagnostic_node(base, "test_two_orgs_run_tasks_concur0", "test",
                             [("TASK-010", "failed", 0, 0, note)], log)
            receipt = _serialized_capture(base)
            node = receipt["nodes"][0]
            assert node["orgs"][2]["tasks"][0]["note_category"] == expected, diagnostic_case
            expected_events = [] if expected in {"none", "omitted"} else [expected]
            if diagnostic_case == "I3":
                expected_events = ["agent_invocation_failed", "workspace_not_initialized"]
            elif diagnostic_case == "I3-pair":
                expected_events = ["agent_invocation_failed", "workspace_instruction_pair_refused"]
            assert node["event_categories"] == expected_events, diagnostic_case
        else:
            _, log, events, discarded, read = next(row for row in _window_cases() if row[0] == diagnostic_case)
            owned = _diagnostic_node(base, "test_two_orgs_run_tasks_concur0", "test", [], log)
            log_info = (owned / ".happyranch/daemon.log").stat()
            identity = log_info.st_dev, log_info.st_ino
            reads = []
            original = guest.os.pread
            def observe(fd: int, length: int, offset: int) -> bytes:
                data = original(fd, length, offset)
                info = os.fstat(fd)
                if (info.st_dev, info.st_ino) == identity:
                    reads.append((offset, length, len(data)))
                return data
            monkeypatch.setattr(guest.os, "pread", observe)
            receipt = _serialized_capture(base)
            expected_reads = ([] if not log else [(0, len(log), len(log))]) if len(log) <= 8192 else [
                (0, 4096, 4096), (len(log) - 4096, 4096, 4096)]
            assert reads == expected_reads, (diagnostic_case, reads, expected_reads)
            node = receipt["nodes"][0]
            assert node["event_categories"] == events, diagnostic_case
            expected = {"log_size_bytes": len(log), "log_read_bytes": read,
                        "log_gap_bytes": len(log) - read, "log_boundary_discarded_bytes": discarded,
                        "log_scanned_bytes": read - discarded,
                        "log_omitted_bytes": len(log) - read + discarded}
            for field, value in expected.items():
                assert type(node[field]) is int and node[field] == value, (diagnostic_case, field, node, expected)
            assert node["log_truncated"] is (expected["log_omitted_bytes"] > 0)
            assert node["log_event_limit_reached"] is (diagnostic_case == "W10-33")
        assert node["log_text_omitted"] is True
        return
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


@pytest.mark.parametrize("hazard", ["node_symlink", "db_symlink", "wal_symlink", "unknown_schema", "replacement", "H0", "H1", "H2", "H3", "H4-append", "H4-truncate", "H5"])
def test_diagnostics_refuse_unsafe_or_changed_inputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, hazard: str,
) -> None:
    if hazard.startswith("H"):
        base = tmp_path / "cases"
        node = _diagnostic_node(base, "test_mixed_fleet_roundtrip_use0", "test", [],
                                b"WorkspaceNotInitialized\n" + b"q" * 4071 + b"\n" + b"x" * 808
                                + b"r\n" + b"s" * 4070 + b"\nWorkspaceNotInitialized")
        log = node / ".happyranch/daemon.log"
        assert log.stat().st_size == 9000
        fired = []
        after_trigger = []
        if hazard == "H0":
            moved = tmp_path / "outside-node"
            node.rename(moved)
            node.symlink_to(moved, target_is_directory=True)
        elif hazard == "H1":
            outside = tmp_path / "outside-log"
            outside.write_text("WorkspaceNotInitialized OUTSIDE_SECRET_CANARY")
            log.unlink()
            log.symlink_to(outside)
        else:
            original = guest.os.pread
            identity = log.stat().st_dev, log.stat().st_ino
            def race(fd: int, length: int, offset: int) -> bytes:
                data = original(fd, length, offset)
                info = os.fstat(fd)
                if not fired and (info.st_dev, info.st_ino) == identity and offset == 4904:
                    fired.append((info.st_dev, info.st_ino, offset))
                    if hazard in {"H2", "H3"}:
                        log.rename(log.with_suffix(".saved"))
                        if hazard == "H2":
                            log.write_text("REPLACEMENT_SECRET_CANARY")
                        else:
                            outside = tmp_path / "outside-log"
                            outside.write_text("WorkspaceNotInitialized OUTSIDE_SECRET_CANARY")
                            log.symlink_to(outside)
                    elif hazard == "H4-append":
                        with log.open("ab") as stream:
                            stream.write(b"CHANGED_CANARY")
                    elif hazard == "H4-truncate":
                        with log.open("wb") as stream:
                            stream.write(b"CHANGED_CANARY")
                    else:
                        directory = node / ".happyranch"
                        directory.rename(node / ".happyranch-saved")
                        directory.mkdir()
                        log.write_text("ANCESTOR_SECRET_CANARY")
                    after_trigger.append(_file_facts(tmp_path))
                return data
            monkeypatch.setattr(guest.os, "pread", race)
        before = _file_facts(tmp_path)
        receipt = json.loads(json.dumps(guest.capture_diagnostics(base, deadline=time.monotonic() + 30)))
        observed = receipt["nodes"][0]
        if hazard == "H0":
            assert observed == {"node": "test_mixed_fleet_roundtrip_use0", "unavailable": "unsafe_or_changed"}
        else:
            if hazard != "H1":
                assert fired == [(*identity, 4904)], "real owned-log tail syscall must run once"
            assert observed.get("log_unavailable") is True, (hazard, observed)
            assert observed["event_categories"] == [], (hazard, observed)
            assert not any(key.startswith("log_") and key != "log_unavailable" for key in observed), observed
            assert observed["orgs"][2]["tasks"] == []
        assert "CANARY" not in json.dumps(receipt)
        assert _file_facts(tmp_path) == (after_trigger[0] if after_trigger else before)
        return
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


@pytest.mark.parametrize("case", ["original", "M0-reader", "M0", "M1", "M2", "M3", "M4", "M4-reader", "M6", "M7", "deadline"]
    + ["M5-" + status for status in ("pending", "running", "blocked", "in_progress", "escalated", "completed", "failed", "cancelled", "superseded")]
    + ["suffix-" + suffix for suffix in ("0", "9999", "", "a", "00000", "0-extra", "lookalike")])
def test_diagnostics_sqlite_busy_deadline_and_row_node_caps(tmp_path: Path, case: str) -> None:
    if case != "original":
        base = tmp_path / "cases"
        if case.startswith("suffix-"):
            suffix = case.removeprefix("suffix-")
            name = "test_mixed_fleet_roundtrip_use" + suffix
            if suffix == "lookalike":
                name = "test_mixed_fleet_roundtrip_uses0"
            _diagnostic_node(base, name, "test", [])
            receipt = _serialized_capture(base)
            assert [node["node"] for node in receipt["nodes"]] == ([name] if suffix in {"0", "9999"} else [])
            return
        rows = [("TASK-010", "in_progress", 0, 0, None), ("TASK-011", "in_progress", 0, 0, None)]
        if case in {"M1", "M2", "M3"}:
            rows[0] = ("TASK-010", "in_progress", 1, 1, None)
        if case == "M2":
            rows[1] = ("TASK-011", "in_progress", 0, 2, None)
        elif case == "M3":
            rows[1] = ("TASK-011", "failed", 0, 0, "agent invocation failed: CANARY")
        elif case in {"M4", "M4-reader"}:
            rows = [("TASK-010", "escalated", 0, 0, None), ("TASK-011", "superseded", 1, 2, None)]
        elif case.startswith("M5-"):
            rows = [("TASK-010", case[3:], 1, 2, None)]
        elif case == "M6":
            rows[1] = ("TASK-011", "future_unknown", 0, 0, None)
        names = ["test_two_orgs_run_tasks_concur0", "test_mixed_fleet_roundtrip_use0",
                 "test_acceptance_cross_process_0", "test_acceptance_cross_process_1", "test_real_diy_acceptance0"]
        if case in {"M0-reader", "M4-reader"} or case.startswith("M5-"):
            _diagnostic_node(base, names[0], "test", rows)
        else:
            for org in ("alpha", "beta"):
                _diagnostic_node(base, names[0], org, [("TASK-001", "failed", 0, 0, "agent invocation failed: CANARY")])
            _diagnostic_node(base, names[1], "test", rows)
            for name in names[2:]:
                _diagnostic_node(base, name, "test", [])
        receipt = _serialized_capture(base, deadline=time.monotonic() - 1 if case == "deadline" else None)
        if case == "deadline":
            assert receipt.get("deadline_exhausted") is True and receipt["nodes"] == [], receipt
            return
        if case not in {"M0-reader", "M4-reader"} and not case.startswith("M5-"):
            assert [node["node"] for node in receipt["nodes"]] == names[:4], case
            assert [node["kind"] for node in receipt["nodes"]] == ["two_orgs", "mixed_fleet", "diy_revoke", "diy_revoke"]
            assert receipt["nodes_truncated"] is True
            for org in receipt["nodes"][0]["orgs"][:2]:
                assert [(t["task_id"], t["status"], t["result_count"], t["session_start_count"]) for t in org["tasks"]] == [("TASK-001", "failed", 0, 0)]
            node = receipt["nodes"][1]
            assert [org.get("unavailable") for org in node["orgs"][:2]] == ["missing", "missing"]
        else:
            node = receipt["nodes"][0]
        facts = node["orgs"][2]
        if case == "M6":
            assert facts.get("unavailable") == "unknown_record" and "tasks" not in facts, facts
        else:
            assert "tasks" in facts, (case, facts)
            assert [(t["task_id"], t["status"], t["result_count"], t["session_start_count"]) for t in facts["tasks"]] == [row[:4] for row in rows]
            assert all(t["executor_evidence"] == "unavailable" for t in facts["tasks"])
            if case == "M3":
                assert facts["tasks"][1]["note_category"] == "agent_invocation_failed"
        return
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


def _prelaunch_record(org="alpha"):
    return {"version": 1, "source_sha": "a" * 40, "source_digest": "b" * 64,
            "org": org, "task_id": "TASK-001", "category": "prelaunch_exception",
            "exception": "builtins.RuntimeError", "symbol": "unknown", "code": "unknown"}


def test_two_org_prelaunch_observer_preserves_exception_return_and_write_failure(tmp_path):
    from tests.helpers.two_org_prelaunch_capture.sitecustomize import transparent, _write_record
    directory = tmp_path / "capture"
    directory.mkdir(mode=0o700)
    private = "PLANTED_NOTE PROMPT_TOKEN /private/path"
    primary = RuntimeError(private)
    def throwing():
        raise primary
    def capture(exc, args, kwargs):
        assert exc is primary
        _write_record(directory, _prelaunch_record())
    with pytest.raises(RuntimeError) as caught:
        transparent(throwing, capture)()
    assert caught.value is primary
    row = guest._read_two_org_prelaunch_exception(directory, "alpha", deadline=time.monotonic() + 1,
                                                  revision="a" * 40, source_digest="b" * 64)
    assert row == _prelaunch_record()
    assert all(raw not in json.dumps(row) for raw in private.split())
    # A successful call returns the same object and never invokes capture.
    returned = object()
    def must_not_capture(*args):
        raise AssertionError("successful call was observed as exception")
    assert transparent(lambda: returned, must_not_capture)() is returned
    assert list(directory.iterdir()) == [directory / "alpha-TASK-001.json"]
    def failed_write(*args):
        raise OSError(private)
    with pytest.raises(RuntimeError) as caught:
        transparent(throwing, failed_write)()
    assert caught.value is primary
    # At most one record: an attempted overwrite cannot replace the first.
    with pytest.raises(FileExistsError):
        _write_record(directory, {**_prelaunch_record(), "exception": "unknown"})
    assert json.loads((directory / "alpha-TASK-001.json").read_text()) == _prelaunch_record()
    assert not list(directory.glob(".*pending"))


@pytest.mark.parametrize("case", ["extra", "wrong_org", "wrong_task", "wrong_sha", "wrong_digest",
                                  "unknown_class", "unknown_symbol", "unknown_code", "version_bool",
                                  "oversized", "truncated", "duplicate", "symlink", "foreign_owner",
                                  "race", "deadline", "public_file", "public_directory"])
def test_two_org_prelaunch_capture_refuses_untrusted_or_private_records(tmp_path, monkeypatch, case):
    directory = tmp_path / "capture"
    directory.mkdir(mode=0o700)
    record = _prelaunch_record()
    changes = {"extra": {"raw": "PLANTED_PRIVATE"}, "wrong_org": {"org": "beta"},
               "wrong_task": {"task_id": "TASK-002"}, "wrong_sha": {"source_sha": "c" * 40},
               "wrong_digest": {"source_digest": "c" * 64}, "unknown_class": {"exception": "Private.Class"},
               "unknown_symbol": {"symbol": "Private.Symbol"}, "unknown_code": {"code": "secret"},
               "version_bool": {"version": True}}
    record.update(changes.get(case, {}))
    data = (json.dumps(record) + "\n").encode()
    if case == "oversized":
        data += b" " * 1024
    elif case == "truncated":
        data = data[:-5]
    elif case == "duplicate":
        data = data.replace(b'{', b'{"version":1,', 1)
    path = directory / "alpha-TASK-001.json"
    path.write_bytes(data)
    path.chmod(0o600)
    if case == "symlink":
        path.rename(directory / "target")
        path.symlink_to(directory / "target")
    elif case == "foreign_owner":
        original = guest.os.fstat
        def foreign(fd):
            value = original(fd)
            values = list(value)
            values[4] = os.getuid() + 1
            return os.stat_result(values)
        monkeypatch.setattr(guest.os, "fstat", foreign)
    elif case == "race":
        original = guest.os.read
        def changed(fd, length):
            raw = original(fd, length)
            path.write_bytes(data + b"changed")
            return raw
        monkeypatch.setattr(guest.os, "read", changed)
    elif case == "public_file":
        path.chmod(0o644)
    elif case == "public_directory":
        directory.chmod(0o755)
    result = guest._read_two_org_prelaunch_exception(
        directory, "alpha", deadline=time.monotonic() + (-1 if case == "deadline" else 1),
        revision="a" * 40, source_digest="b" * 64)
    assert result == {"org": "alpha", "prelaunch_capture": "unavailable"}, result
    assert "PLANTED_PRIVATE" not in json.dumps(result)


def test_two_org_prelaunch_capture_requires_independent_source_identity(tmp_path):
    directory = tmp_path / "capture"
    directory.mkdir(mode=0o700)
    from tests.helpers.two_org_prelaunch_capture.sitecustomize import _write_record
    _write_record(directory, _prelaunch_record())
    assert guest._read_two_org_prelaunch_exception(directory, "alpha", deadline=time.monotonic() + 1,
                                                   revision=None, source_digest="b" * 64) == {
                                                       "org": "alpha", "prelaunch_capture": "unavailable"}


@pytest.mark.parametrize("case", ["prelaunch", "session_already_started", "wrong_org", "wrong_task", "changed_source", "other_python_module"])
def test_two_org_prelaunch_installer_authenticates_real_source_and_closed_symbol(tmp_path, monkeypatch, case):
    """Offline negative control: missing agent refuses BEFORE executor construction.

    This is not a daemon launch or a diagnosis of historical two-org failures.
    """
    import threading
    from types import SimpleNamespace
    from tests.helpers.two_org_prelaunch_capture import sitecustomize as capture
    from runtime.orchestrator.orchestrator import Orchestrator, AgentUnavailableError
    from runtime.orchestrator._paths import OrgPaths
    source = Path(__file__).resolve().parents[2]
    directory = tmp_path / "capture"
    directory.mkdir(mode=0o700)
    binding = capture.source_binding(source, "a" * 40, deadline=time.monotonic() + 1)
    if case == "changed_source":
        binding["hashes"]["runtime.orchestrator.orchestrator"] = "c" * 64
    binding_file = directory / "binding.json"
    binding_file.write_text(json.dumps(binding))
    binding_file.chmod(0o600)
    monkeypatch.setenv("HAPPYRANCH_TWO_ORG_CAPTURE", str(binding_file))
    monkeypatch.setattr(sys, "orig_argv", [sys.executable, "-m", "cli.main" if case == "other_python_module" else "runtime.daemon"])
    monkeypatch.syspath_prepend(str(source / "tests/helpers/integration_stub_guard"))
    # Restore the class when this unit ends; no global wrapper escapes the test.
    monkeypatch.setattr(Orchestrator, "_run_agent", Orchestrator._run_agent)
    def no_executor(*args, **kwargs):
        raise AssertionError("offline control must never construct an executor")
    monkeypatch.setattr(Orchestrator, "_build_executor", no_executor)
    connection = sqlite3.connect(":memory:")
    connection.execute("CREATE TABLE audit_log(task_id TEXT,action TEXT)")
    if case == "session_already_started":
        connection.execute("INSERT INTO audit_log VALUES ('TASK-001','session_start')")
    owner = Orchestrator.__new__(Orchestrator)
    owner._slug = "gamma" if case == "wrong_org" else "alpha"
    owner._paths = OrgPaths(root=tmp_path / "missing-agent-org")
    owner._db = SimpleNamespace(_conn=connection, _lock=threading.RLock(), get_task=lambda task: None)
    capture._install_capture()
    try:
        with pytest.raises(AgentUnavailableError):
            owner._run_agent("TASK-002" if case == "wrong_task" else "TASK-001", "absent", "PLANTED_PROMPT_TOKEN")
        row = guest._read_two_org_prelaunch_exception(directory, "alpha", deadline=time.monotonic() + 1,
                                                      revision="a" * 40, source_digest=binding["digest"])
        if case == "prelaunch":
            assert row["exception"] == "unknown", row  # no source-owned class code
            assert row["symbol"] == "runtime.orchestrator.orchestrator.Orchestrator._resolve_executor_name", row
            assert "PLANTED_PROMPT_TOKEN" not in json.dumps(row)
        else:
            assert row == {"org": "alpha", "prelaunch_capture": "unavailable"}, row
    finally:
        connection.close()


@pytest.mark.parametrize('case', ['known', 'foreign_inner', 'foreign_class', 'source_changed', 'copied_code', 'wrapped_code', 'copied_class', 'copied_class_exact', 'builtin_known', 'source_class', 'copied_method_class', 'rebound_closure_class', 'builtin_metaclass_spoof'])
def test_two_org_policy_exception_observes_exact_inner_identity(tmp_path, monkeypatch, case):
    """Pure resolver/thrower control: no executor, daemon or provider launch."""
    import threading
    from types import SimpleNamespace
    from tests.helpers.two_org_prelaunch_capture import sitecustomize as capture
    from runtime.orchestrator import active_authority_policy as policy
    from runtime.orchestrator.orchestrator import Orchestrator
    source = Path(__file__).resolve().parents[2]
    directory = tmp_path / 'capture'
    directory.mkdir(mode=0o700)
    binding = capture.source_binding(source, 'a' * 40, deadline=time.monotonic() + 1)
    if case == 'source_changed':
        binding['hashes']['runtime.orchestrator.active_authority_policy'] = 'c' * 64
    binding_file = directory / 'binding.json'
    binding_file.write_text(json.dumps(binding))
    binding_file.chmod(0o600)
    monkeypatch.setenv('HAPPYRANCH_TWO_ORG_CAPTURE', str(binding_file))
    monkeypatch.setattr(sys, 'orig_argv', [sys.executable, '-m', 'runtime.daemon'])
    monkeypatch.syspath_prepend(str(source / 'tests/helpers/integration_stub_guard'))
    monkeypatch.setattr(Orchestrator, '_run_agent', Orchestrator._run_agent)
    # The resolver itself is real; only roster eligibility and storage are doubles.
    monkeypatch.setattr(policy, 'resolve_policy_manager_team', lambda **kwargs: 'engineering')
    seen = []
    original_class = policy.ActiveAuthorityPolicyError
    disk_before = Path(policy.__file__).read_bytes()
    if case in {'copied_code', 'wrapped_code'}:
        namespace = {'ActiveAuthorityPolicyError': original_class}
        exec(compile("def resolve_active_team_policy_snapshot(**kwargs):\n    raise ActiveAuthorityPolicyError('PLANTED_CREDENTIAL /private/path')\n",
                     policy.__file__, 'exec'), namespace)
        changed = namespace['resolve_active_team_policy_snapshot']
        changed.__module__ = policy.__name__
        if case == 'wrapped_code':
            changed.__wrapped__ = policy.resolve_active_team_policy_snapshot
        assert changed.__code__ != policy.resolve_active_team_policy_snapshot.__code__
        assert changed.__code__.co_filename == policy.resolve_active_team_policy_snapshot.__code__.co_filename
        assert changed.__code__.co_qualname == policy.resolve_active_team_policy_snapshot.__code__.co_qualname
        monkeypatch.setattr(policy, 'resolve_active_team_policy_snapshot', changed)
    if case in {'copied_class', 'copied_class_exact'}:
        counterfeit = type('ActiveAuthorityPolicyError', (RuntimeError,), {
            '__module__': policy.__name__, '__qualname__': original_class.__qualname__,
            '__doc__': original_class.__doc__ if case == 'copied_class_exact' else 'counterfeit',
        })
        monkeypatch.setattr(policy, 'ActiveAuthorityPolicyError', counterfeit)
    from runtime.orchestrator import workspace_adapters as adapters
    adapters_disk_before = Path(adapters.__file__).read_bytes()
    if case in {'copied_method_class', 'rebound_closure_class', 'builtin_metaclass_spoof'}:
        original_integrity_class = adapters.WorkspaceIntegrityError
        class BuiltinEqualityMeta(type):
            def __hash__(cls):
                return hash(ValueError)
            def __eq__(cls, other):
                return other is ValueError or other is cls
        defining_type = BuiltinEqualityMeta if case == 'builtin_metaclass_spoof' else type
        counterfeit = defining_type('WorkspaceIntegrityError', (Exception,), {
            '__module__': adapters.__name__, '__qualname__': 'WorkspaceIntegrityError',
            '__doc__': original_integrity_class.__doc__,
            '__init__': original_integrity_class.__init__,
        })
        if case in {'rebound_closure_class', 'builtin_metaclass_spoof'}:
            from types import FunctionType
            def cell(value):
                return (lambda: value).__closure__[0]
            def foreign_string(self):
                raise AssertionError('exception text must never be inspected')
            original_init = original_integrity_class.__init__
            closure = tuple(cell(counterfeit) if name == '__class__' else old
                            for name, old in zip(original_init.__code__.co_freevars,
                                                 original_init.__closure__))
            counterfeit.__init__ = FunctionType(original_init.__code__, original_init.__globals__,
                                               original_init.__name__, original_init.__defaults__, closure)
            counterfeit.__str__ = foreign_string
            if case == 'builtin_metaclass_spoof':
                assert type(counterfeit) is BuiltinEqualityMeta
                assert counterfeit is not ValueError
            assert counterfeit is not original_integrity_class
            assert counterfeit.__init__ is not original_init
            assert counterfeit.__init__.__code__ is original_init.__code__
            cells = dict(zip(counterfeit.__init__.__code__.co_freevars,
                             counterfeit.__init__.__closure__))
            assert cells['__class__'].cell_contents is counterfeit
            assert vars(counterfeit)['__str__'] is foreign_string
        monkeypatch.setattr(adapters, 'WorkspaceIntegrityError', counterfeit)
    class Foreign(policy.ActiveAuthorityPolicyError):
        pass
    def invocation(*args, **kwargs):
        try:
            if case == 'foreign_class':
                raise Foreign('PLANTED_CREDENTIAL /private/path')
            if case == 'foreign_inner':
                raise policy.ActiveAuthorityPolicyError('PLANTED_CREDENTIAL /private/path')
            if case in {'source_class', 'copied_method_class', 'rebound_closure_class', 'builtin_metaclass_spoof'}:
                if case == 'copied_method_class':
                    counterfeit_error = adapters.WorkspaceIntegrityError.__new__(adapters.WorkspaceIntegrityError)
                    BaseException.__init__(counterfeit_error, 'PLANTED_CREDENTIAL /private/path')
                    raise counterfeit_error
                raise adapters.WorkspaceIntegrityError('private', 'PLANTED_CREDENTIAL /private/path')
            if case == 'builtin_known':
                from runtime.infrastructure.db.authority_policy import AuthorityPolicyMixin
                AuthorityPolicyMixin._validate_authority_selector_team('')
            policy.resolve_active_team_policy_snapshot(
                store=SimpleNamespace(get_authority_selector=lambda team: None),
                root=tmp_path, teams=None, team='engineering',
                agent_name='engineering_head', eligible=True,
            )
        except BaseException as exc:
            seen.append(exc)
            raise
    monkeypatch.setattr(Orchestrator, '_run_agent_impl', invocation)
    connection = sqlite3.connect(':memory:')
    connection.execute('CREATE TABLE audit_log(task_id TEXT,action TEXT)')
    owner = Orchestrator.__new__(Orchestrator)
    owner._slug = 'alpha'
    owner._db = SimpleNamespace(_conn=connection, _lock=threading.RLock())
    capture._install_capture()
    try:
        with pytest.raises(ValueError if case == 'builtin_known' else adapters.WorkspaceIntegrityError
                           if case in {'source_class', 'copied_method_class', 'rebound_closure_class', 'builtin_metaclass_spoof'} else policy.ActiveAuthorityPolicyError) as caught:
            owner._run_agent('TASK-001', 'engineering_head', 'PLANTED_PROMPT')
        assert caught.value is seen[0]
        assert disk_before == Path(policy.__file__).read_bytes()
        assert adapters_disk_before == Path(adapters.__file__).read_bytes()
        row = guest._read_two_org_prelaunch_exception(
            directory, 'alpha', deadline=time.monotonic() + 1,
            revision='a' * 40, source_digest=binding['digest'],
        )
        if case == 'source_changed':
            assert row == {'org': 'alpha', 'prelaunch_capture': 'unavailable'}
        else:
            if case in {'copied_class', 'copied_class_exact', 'foreign_class'}:
                assert row['exception'] == 'unknown'
            elif case == 'source_class':
                assert row['exception'] == 'unknown'  # custom defining types are not authenticated
            elif case == 'builtin_known':
                assert row['exception'] == 'builtins.ValueError'
            else:
                assert row['exception'] == 'unknown'  # custom defining types are not authenticated
            assert row['symbol'] == ('runtime.orchestrator.active_authority_policy.resolve_active_team_policy_snapshot'
                                     if case in {'known', 'copied_class', 'copied_class_exact'} else
                                     'runtime.infrastructure.db.authority_policy.AuthorityPolicyMixin._validate_authority_selector_team'
                                     if case == 'builtin_known' else 'unknown')
            assert 'PLANTED' not in json.dumps(row) and '/private/path' not in json.dumps(row)
    finally:
        connection.close()
