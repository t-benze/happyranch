from __future__ import annotations

from pathlib import Path
import os
import stat
from dataclasses import replace

import pytest

from runtime.daemon import task_scratch_report as reports
from runtime.daemon import task_scratch_coverage as coverage_module
from runtime.daemon import task_scratch_evidence as evidence_module
from runtime.daemon.sessions import SessionTracker
from runtime.daemon.task_scratch_report import AUDIT_ACTION, report_task_scratch
from runtime.infrastructure.database import Database
from runtime.models import TaskRecord, TaskStatus
from runtime.orchestrator.task_scratch import prepare_task_scratch


def _proc(tmp_path: Path) -> Path:
    proc = tmp_path / "proc"
    (proc / "sys/kernel/random").mkdir(parents=True)
    (proc / "sys/kernel/random/boot_id").write_text("123e4567-e89b-42d3-a456-426614174000")
    item = proc / "42"
    (item / "fd").mkdir(parents=True)
    (item / "stat").write_text("42 (agent) S 1 1 1 1 1 1 1 1 1 1 1 1 1 1 1 1 1 1 9 0")
    (item / "root").symlink_to("/")
    (item / "cwd").symlink_to("/")
    return proc


def test_terminal_report_is_audit_only_noop(tmp_path: Path) -> None:
    db = Database(tmp_path / "state.db")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    try:
        db.insert_task(TaskRecord(id="TASK-1", status=TaskStatus.COMPLETED, brief="x",
                                  assigned_agent="dev_agent", current_session_id="session"))
        contract = prepare_task_scratch(workspace=workspace, task_id="TASK-1",
                                        producer_kind="agent", producer_id="session")
        for index in range(100):
            (contract.root / f"payload-{index}").write_bytes(b"x" * 8192)
        before = sorted(path.relative_to(workspace) for path in workspace.rglob("*"))
        report = report_task_scratch(
            db=db, sessions=SessionTracker(), task_id="TASK-1", agent="dev_agent",
            workspace=workspace, source="teardown", observation_id="session",
            proc_root=_proc(tmp_path), daemon_started_monotonic=0,
        )
        assert report.decision == "would_reclaim"
        assert report.payload["actual_reclaimed_bytes"] == 0
        assert report.payload["actual_reclaimed_inodes"] == 0
        assert before == sorted(path.relative_to(workspace) for path in workspace.rglob("*"))
        assert db.get_audit_logs("TASK-1")[-1]["action"] == AUDIT_ACTION
    finally:
        db.close()


def test_missing_observer_never_invents_zero_measurement(tmp_path: Path) -> None:
    db = Database(tmp_path / "state.db")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    try:
        report = report_task_scratch(db=db, sessions=None, task_id="TASK-1", agent="dev_agent",
                                     workspace=workspace, source="weekly", observation_id="weekly")
        assert report.decision == "unavailable"
        assert "session_observer_unavailable" in report.reasons
    finally:
        db.close()


def _snapshot(workspace):
    """Bytes and stable identities, including protected parents and siblings."""
    result = {}
    for path in [workspace, *workspace.rglob("*")]:
        info = path.lstat()
        value = (os.readlink(path) if path.is_symlink() else
                 path.read_bytes() if stat.S_ISREG(info.st_mode) else None)
        result[str(path.relative_to(workspace))] = (
            info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns, value)
    return result


@pytest.fixture
def candidate(tmp_path, monkeypatch):
    db = Database(tmp_path / "db.sqlite")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    db.insert_task(TaskRecord(id="TASK-1", status=TaskStatus.COMPLETED, brief="x",
                             assigned_agent="dev_agent", current_session_id="session"))
    scratch = prepare_task_scratch(workspace=workspace, task_id="TASK-1",
                                  producer_kind="agent", producer_id="session")
    for index in range(100):
        (scratch.root / str(index)).write_bytes(b"x" * 8192)
    (workspace / "repos").mkdir()
    (workspace / "repos/keep").write_bytes(b"repository bytes")
    (workspace / ".happyranch/task-tmp/sibling").mkdir()
    (workspace / ".happyranch/task-tmp/sibling/keep").write_bytes(b"sibling")
    proc = _proc(tmp_path)
    sessions = SessionTracker()
    monkeypatch.setattr(reports, "_PROC_ROOT", proc)
    monkeypatch.setattr(reports, "_STARTED_MONOTONIC", 0)
    from runtime.daemon import task_scratch_reclamation
    tripwire = []
    def forbidden(*args, **kwargs):
        tripwire.append(True)
        raise AssertionError("deletion invoked")
    monkeypatch.setattr(task_scratch_reclamation, "execute_ledger", forbidden)
    yield db, workspace, scratch, proc, sessions, tripwire
    assert not tripwire
    db.close()


def _report(candidate, **kwargs):
    db, workspace, _, _, sessions, _ = candidate
    return reports.report_task_scratch(db=db, sessions=sessions, task_id="TASK-1",
        agent="dev_agent", workspace=workspace, source="teardown", observation_id="session", **kwargs)


def test_append_only_truthful_observations_preserve_bytes_and_identities(candidate):
    db, workspace, _, _, _, _ = candidate
    before = _snapshot(workspace)
    one, two = _report(candidate), _report(candidate)
    assert one.decision == two.decision == "would_reclaim", one.reasons
    rows = [row for row in db.get_audit_logs("TASK-1") if row["action"] == AUDIT_ACTION]
    assert len(rows) == 2
    assert rows[0]["payload"]["observation_id"] != rows[1]["payload"]["observation_id"]
    for row in rows:
        payload = row["payload"]
        assert payload["report_only"] and payload["source"] == "teardown"
        assert payload["actual_reclaimed_bytes"] == payload["actual_reclaimed_inodes"] == 0
        assert payload["allocated_bytes"] > 0 and payload["entries"] >= 25
        assert payload["process_roots"] == payload["process_cwds"] == payload["open_fds"] == 0
        assert payload["boot_id"] == payload["coverage_boot_id"]
        assert payload["started_at_ns"] <= payload["evidence_observed_at_ns"] <= payload["coverage_observed_at_ns"] <= payload["observed_at_ns"]
        assert payload["candidate_identity"] and payload["freshness_limited"]
    assert before == _snapshot(workspace)


@pytest.mark.parametrize("case,reason", [
    ("active", "active_session"), ("nonterminal", "nonterminal_or_unresolved_lineage"),
    ("recovery", "nonterminal_or_unresolved_lineage"),
    ("revisit", "nonterminal_or_unresolved_lineage"), ("linked", "linked_job_authority_unavailable"),
    ("pid", "executor_pid_live_or_ambiguous"), ("root", "process_root_reference"),
    ("cwd", "process_cwd_reference"), ("fd", "open_fd_reference"),
])
def test_real_evidence_retention_propagates(candidate, case, reason):
    db, workspace, scratch, proc, sessions, _ = candidate
    if case == "active":
        sessions.set_active("TASK-1", "dev_agent", "newer")
    elif case == "nonterminal":
        db.update_task("TASK-1", status=TaskStatus.IN_PROGRESS)
    elif case == "recovery":
        db.update_task_active_chain("TASK-1", '{"state":"pending"}')
    elif case == "revisit":
        db.insert_task(TaskRecord(id="TASK-2", brief="retry", status=TaskStatus.IN_PROGRESS,
            assigned_agent="dev_agent", current_session_id="retry", revisit_of_task_id="TASK-1"))
    elif case == "linked":
        db.update_task("TASK-1", blocked_on_job_ids='["JOB-MISSING"]')
    elif case == "pid":
        db.update_task("TASK-1", executor_pid=42)
    else:
        path = proc / "42" / ("fd/3" if case == "fd" else case)
        if path.is_symlink():
            path.unlink()
        path.symlink_to(scratch.root)
    before = _snapshot(workspace)
    result = _report(candidate)
    assert result.decision == "retain", result.payload
    assert reason in result.reasons
    assert before == _snapshot(workspace)


@pytest.mark.parametrize("case", ["missing_boot", "warmup", "capped", "timeout", "unsupported", "partial", "nonready"])
def test_unavailable_evidence_or_coverage_preserves_retention(candidate, monkeypatch, case):
    db, workspace, _, proc, sessions, _ = candidate
    sessions.set_active("TASK-1", "dev_agent", "newer")
    if case == "missing_boot":
        (proc / "sys/kernel/random/boot_id").unlink()
    elif case == "warmup":
        monkeypatch.setattr(reports, "_STARTED_MONOTONIC", reports.time.monotonic())
    elif case == "capped":
        monkeypatch.setattr(coverage_module, "MAX_ENTRIES", 0)
    elif case == "timeout":
        monkeypatch.setattr(evidence_module, "SCAN_NS", 0)
    elif case == "unsupported":
        monkeypatch.setattr(coverage_module.sys, "platform", "win32")
    elif case == "partial":
        (proc / "42/cwd").unlink()
    else:
        (workspace / "repos/large").write_bytes(b"r" * 1024 * 1024)
    result = _report(candidate)
    assert result.decision != "would_reclaim"
    assert "active_session" in result.reasons or case == "timeout"
    if case in {"missing_boot", "timeout", "partial"}:
        assert result.payload["process_roots"] is None
    else:
        assert result.payload["process_roots"] == 0
    if not result.payload.get("coverage_complete"):
        assert "allocated_bytes" not in result.payload


@pytest.mark.parametrize("case", ["root", "manifest", "workspace", "stale", "boot"])
def test_identity_freshness_boot_changes_reject(candidate, monkeypatch, case):
    _, workspace, scratch, _, _, _ = candidate
    real = reports.collect_task_scratch_coverage
    def changed(**kwargs):
        observed = real(**kwargs)
        if case == "root":
            scratch.root.rename(scratch.root.with_name("old"))
            scratch.root.mkdir()
        elif case == "manifest":
            raw = scratch.manifest_path.read_bytes()
            scratch.manifest_path.rename(scratch.manifest_path.with_suffix(".old"))
            scratch.manifest_path.write_bytes(raw)
        elif case == "workspace":
            return replace(observed, workspace="/different")
        elif case == "stale":
            return replace(observed, observed_at_ns=1)
        elif case == "boot":
            return replace(observed, boot_id="different-boot")
        return observed
    monkeypatch.setattr(reports, "collect_task_scratch_coverage", changed)
    result = _report(candidate)
    assert result.decision == "unavailable"
    assert "allocated_bytes" not in result.payload


def test_old_supplied_coverage_is_recollected(candidate):
    result = _report(candidate, coverage=object())
    assert result.decision == "would_reclaim"
    assert result.payload["coverage_recollected"]


@pytest.mark.parametrize("case", ["missing", "malformed", "symlink", "repo", "legacy", "fifo"])
def test_canonical_boundaries_never_eligible(candidate, case):
    _, workspace, scratch, _, _, _ = candidate
    if case == "missing":
        scratch.manifest_path.unlink()
    elif case == "malformed":
        scratch.manifest_path.write_text("{}")
    elif case == "symlink":
        scratch.root.rename(scratch.root.with_name("old"))
        scratch.root.symlink_to(scratch.root.with_name("old"))
    elif case == "repo":
        (scratch.root / ".git").mkdir()
    elif case == "legacy":
        data = __import__('json').loads(scratch.manifest_path.read_text())
        data["required_root"] = "/tmp/TASK-1"
        scratch.manifest_path.write_text(__import__('json').dumps(data))
    else:
        os.mkfifo(scratch.root / "pipe")
    before = _snapshot(workspace)
    assert _report(candidate).decision != "would_reclaim"
    assert before == _snapshot(workspace)


def test_initial_observer_and_publisher_failures_are_contained(candidate, monkeypatch):
    monkeypatch.setattr(reports, "_identity", lambda *a: (_ for _ in ()).throw(OSError("bad")))
    assert _report(candidate).decision == "unavailable"
    monkeypatch.setattr(candidate[0], "insert_audit_log", lambda *a: (_ for _ in ()).throw(RuntimeError("db")))
    assert _report(candidate).decision == "unavailable"


def test_all_discovery_entries_are_bounded(candidate, monkeypatch, caplog):
    db, workspace, _, _, sessions, _ = candidate
    parent = workspace / ".happyranch/task-scratch-manifests"
    for index in range(20):
        (parent / f"noise-{index}").write_text("")
    monkeypatch.setattr(reports, "_MAX_DISCOVERY_ENTRIES", 2)
    reports.report_registered_agent_task_scratch(db=db, sessions=sessions, agent="dev_agent", workspace=workspace)
    assert "discovery_entries_capped" in caplog.text


def test_discovery_deadline_and_candidate_isolation(candidate, monkeypatch, caplog):
    db, workspace, _, _, sessions, _ = candidate
    prepare_task_scratch(workspace=workspace, task_id="TASK-2", producer_kind="agent", producer_id="s")
    real = reports.report_task_scratch
    def one_bad(**kwargs):
        if kwargs["task_id"] == "TASK-1":
            raise RuntimeError("isolated")
        return real(**kwargs)
    monkeypatch.setattr(reports, "report_task_scratch", one_bad)
    result = reports.report_registered_agent_task_scratch(db=db, sessions=sessions, agent="dev_agent", workspace=workspace)
    assert len(result) == 1
    assert "candidate observation unavailable" in caplog.text
    monkeypatch.setattr(reports, "_SCAN_NS", 0)
    assert reports.report_registered_agent_task_scratch(db=db, sessions=sessions, agent="dev_agent", workspace=workspace) == ()
    assert "observation_timeout" in caplog.text


def test_cross_device_candidate_rejected(candidate, monkeypatch):
    from types import SimpleNamespace
    scratch = candidate[2]
    inode = scratch.root.stat().st_ino
    real = reports.os.fstat
    def changed(fd):
        value = real(fd)
        if value.st_ino == inode:
            return SimpleNamespace(**{name: getattr(value, name) for name in
                ("st_ino", "st_mode", "st_size", "st_mtime_ns", "st_ctime_ns")}, st_dev=value.st_dev + 1)
        return value
    monkeypatch.setattr(reports.os, "fstat", changed)
    result = _report(candidate)
    assert result.decision == "unavailable"
    assert "candidate_device_changed" in result.reasons


def test_zero_coverage_is_not_ready_and_payload_error_is_contained(candidate, monkeypatch):
    real = reports.collect_task_scratch_coverage
    def zero(**kwargs):
        value = real(**kwargs)
        return replace(value, complete=False, coverage_ready=False,
                       reasons=("zero_or_empty_observation",), buckets=())
    monkeypatch.setattr(reports, "collect_task_scratch_coverage", zero)
    result = _report(candidate)
    assert result.decision == "unavailable"
    assert "zero_or_empty_observation" in result.reasons
    assert "allocated_bytes" not in result.payload
    class Broken:
        @property
        def reasons(self):
            raise RuntimeError("malformed payload")
    monkeypatch.setattr(reports, "collect_task_scratch_coverage", lambda **kw: Broken())
    assert _report(candidate).decision == "unavailable"


def test_publication_failure_cannot_return_would_reclaim(candidate, monkeypatch):
    db = candidate[0]
    before = len(db.get_audit_logs("TASK-1"))
    monkeypatch.setattr(db, "insert_audit_log", lambda *a: (_ for _ in ()).throw(RuntimeError("unavailable")))
    result = _report(candidate)
    assert result.decision == "unavailable"
    assert "publication_unavailable" in result.reasons
    assert len(db.get_audit_logs("TASK-1")) == before


def test_expired_shared_deadline_never_starts_collectors(candidate, monkeypatch):
    calls = []
    monkeypatch.setattr(reports, "collect_task_scratch_evidence", lambda **kw: calls.append(kw))
    result = _report(candidate, deadline_ns=0)
    assert result.decision == "unavailable" and "observation_timeout" in result.reasons
    assert not calls
