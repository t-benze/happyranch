from __future__ import annotations

import json
import os
import inspect
import threading
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest
import runtime.daemon.task_scratch_reclamation as reclamation
import runtime.daemon.task_scratch_coverage as scratch_coverage
import runtime.daemon.task_scratch_evidence as scratch_evidence

from runtime.daemon.task_scratch_reclamation import (
    CoverageAssertions, EvidencePlatform, LifecycleAssertions, LivenessAssertions,
    ReclamationAssertions, ReclamationError, ZombieRecoveryState, execute_ledger, seal_ledger_row,
)
from runtime.orchestrator.task_scratch import prepare_task_scratch
from runtime.daemon.sessions import SessionTracker
from runtime.infrastructure.database import Database
from runtime.models import JobInterpreter, JobRecord, JobStatus, TaskRecord, TaskStatus


def _candidate(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    contract = prepare_task_scratch(workspace=workspace, task_id="TASK-1",
                                    producer_kind="agent", producer_id="sess-1")
    (contract.root / "nested").mkdir()
    (contract.root / "nested/file").write_bytes(b"payload")
    old = 1_700_000_000_000_000_000
    for path in (contract.root / "nested/file", contract.root / "nested", contract.root):
        os.utime(path, ns=(old, old), follow_symlinks=False)
    lifecycle = LifecycleAssertions(
        "completed", "terminal:v1", old + 120_000_000_000, 0,
        ZombieRecoveryState.CLEAR, 0, 0, 0, True, False, False, False,
    )
    liveness = LivenessAssertions(
        "complete-proc-scan", EvidencePlatform.LINUX, "boot-1", "boot-1",
        True, False, False, False, False, 0, 0, 0, 0,
    )
    coverage = CoverageAssertions(
        "boot-coverage-1", "a" * 64, "boot-1", "boot-1",
        True, False, False, False, 0, 0, 0,
    )
    evidence = ReclamationAssertions("dev_agent", lifecycle, liveness, coverage)
    return workspace, contract, evidence, old


def _direct_accounting(root: Path) -> tuple[int, int]:
    """Independent fixture accounting: lstat every observed directory entry."""
    entries = [root, *sorted(root.rglob("*"))]
    return sum(os.lstat(path).st_blocks * 512 for path in entries), len(entries)


def _snapshot(paths: list[Path]) -> dict[Path, tuple[int, int, bytes | None]]:
    return {path: (path.stat().st_dev, path.stat().st_ino,
                   path.read_bytes() if path.is_file() else None) for path in paths}


def _selected_metric_paths(buckets, metric: str) -> set[str]:
    """Independent test arithmetic for the documented coverage selection rule."""
    total = sum(getattr(bucket, metric) for bucket in buckets)
    selected: set[str] = set()
    current = 0
    for bucket in sorted(buckets, key=lambda bucket: (-getattr(bucket, metric),
                                                       os.fsencode(bucket.relative_path))):
        current += getattr(bucket, metric)
        selected.add(bucket.relative_path)
        if current * 5 >= total * 4:
            break
    selected.update(bucket.relative_path for bucket in buckets
                    if getattr(bucket, metric) * 10 >= total)
    return selected


def test_removes_only_literal_root_and_preserves_sidecars(tmp_path):
    workspace, contract, evidence, old = _candidate(tmp_path)
    manifest_before = contract.manifest_path.read_bytes()
    lock_before = contract.manifest_path.with_suffix(".lock").read_bytes()
    sibling = prepare_task_scratch(workspace=workspace, task_id="TASK-2",
                                   producer_kind="agent", producer_id="sess-2")
    row = seal_ledger_row(workspace=workspace, task_id="TASK-1", assertions=evidence,
                          now_ns=old + 121_000_000_000)
    result, = execute_ledger((row,))
    assert result.outcome == "completed"
    assert (result.reclaimed_bytes, result.reclaimed_inodes) == (
        row.before.allocated_bytes, row.before.inodes)
    assert not contract.root.exists()
    assert contract.manifest_path.read_bytes() == manifest_before
    assert contract.manifest_path.with_suffix(".lock").read_bytes() == lock_before
    assert sibling.root.is_dir()


@pytest.fixture
def disposable_consumer(tmp_path):
    """One isolated DB/proc/workspace source for private consumer assertions."""
    workspace, contract, _assertions, old = _candidate(tmp_path)
    payload = contract.root / "nested/file"
    payload.write_bytes(b"x" * 8192)
    # Leave a real, still-dominant canonical remainder after the injected late unlink.
    for index in range(200):
        os.link(payload, contract.root / "nested" / f"payload-link-{index}")
    for path in (payload, contract.root / "nested", contract.root):
        os.utime(path, ns=(old, old), follow_symlinks=False)
    proc = tmp_path / "proc"
    (proc / "sys/kernel/random").mkdir(parents=True)
    (proc / "sys/kernel/random/boot_id").write_text("123e4567-e89b-42d3-a456-426614174000\n")
    process = proc / "42"; (process / "fd").mkdir(parents=True)
    (process / "stat").write_text("42 (agent) S 1 1 1 1 1 1 1 1 1 1 1 1 1 1 1 1 1 1 9 0")
    (process / "root").symlink_to("/"); (process / "cwd").symlink_to("/")
    db = Database(tmp_path / "state.db")
    completed_at = datetime.fromtimestamp((old + 120_000_000_000) / 1_000_000_000, tz=timezone.utc)
    for task_id, parent in (("TASK-0", None), ("TASK-1", "TASK-0"), ("TASK-2", "TASK-0")):
        db.insert_task(TaskRecord(id=task_id, status=TaskStatus.COMPLETED, brief="x",
                                  assigned_agent="dev_agent", current_session_id="session",
                                  parent_task_id=parent, completed_at=completed_at))
        db.insert_task_result(task_id, "dev_agent", "session", "done", 1, status="completed")
        db.insert_job(JobRecord(id=f"JOB-{task_id[-1]}", task_id=task_id,
                                agent_name="dev_agent", title="terminal", rationale="test",
                                script_text="true", interpreter=JobInterpreter.BASH,
                                status=JobStatus.COMPLETED, created_at=completed_at.isoformat()))
    sibling = prepare_task_scratch(workspace=workspace, task_id="TASK-2",
                                   producer_kind="agent", producer_id="sess-2")
    sibling_file = sibling.root / "sibling.txt"; sibling_file.write_text("keep sibling")
    sidecar = tmp_path / "repo" / ".git"; sidecar.mkdir(parents=True)
    (sidecar / "HEAD").write_text("ref: refs/heads/main\n")
    sessions = SessionTracker()
    from runtime.daemon.task_scratch_coverage import collect_task_scratch_coverage
    from runtime.daemon.task_scratch_evidence import collect_task_scratch_evidence

    def consume(**overrides):
        return reclamation.collect_revalidate_seal_consume_disposable(
            db=db, sessions=sessions, workspace=workspace, task_id="TASK-1", agent_name="dev_agent",
            proc_root=overrides.get("proc_root", proc),
            daemon_started_monotonic=overrides.get("daemon_started_monotonic", 0),
            monotonic_now=overrides.get("monotonic_now", 31),
            now_ns=overrides.get("now_ns", old + 121_000_000_000))

    yield {"workspace": workspace, "contract": contract, "proc": proc, "db": db,
           "sessions": sessions, "old": old, "consume": consume,
           "sibling": sibling, "sibling_file": sibling_file, "repo": sidecar.parent,
           "evidence": collect_task_scratch_evidence,
           "coverage": collect_task_scratch_coverage}
    db.close()


def _protected_snapshot(source):
    """Re-enumerate named protected descendants without scanning the workspace."""
    contract = source["contract"]
    protected = [source["workspace"], source["workspace"] / ".happyranch",
                 contract.manifest_path, contract.manifest_path.with_suffix(".lock"),
                 contract.root.parent]
    # Re-enumerate the named protected descendants at each observation.  This
    # catches additions/removals without turning the test into a workspace scan.
    for root in (source["sibling"].root, source["repo"]):
        protected.extend([root, *sorted(root.rglob("*"))])
    return _snapshot(protected)


def _named_membership(source):
    """Immediate sidecar/parent membership, without an arbitrary workspace walk."""
    roots = (source["contract"].root.parent, source["repo"] / ".git")
    return {root: tuple(sorted(child.name for child in root.iterdir())) for root in roots}


def _refusal_snapshot(source):
    """Capture the whole remaining target and all protected objects, not paths alone."""
    contract = source["contract"]
    target = [contract.root, *sorted(contract.root.rglob("*"))]
    return _snapshot(target) | _protected_snapshot(source)


def test_private_dormant_consumer_collects_real_disposable_sources_before_consuming(disposable_consumer):
    """P1 consumes only a complete disposable source with independent accounting."""
    source = disposable_consumer
    contract = source["contract"]
    workspace = source["workspace"]
    snapshots = _protected_snapshot(source)
    expected_bytes, expected_inodes = _direct_accounting(contract.root)
    assert source["evidence"](db=source["db"], sessions=source["sessions"], task_id="TASK-1",
                               root=contract.root, proc_root=source["proc"],
                               daemon_started_monotonic=0, monotonic_now=31).eligible
    assert source["coverage"](workspace=workspace, proc_root=source["proc"]).coverage_ready
    observed = reclamation._collect_private_evidence(
        db=source["db"], sessions=source["sessions"], task_id="TASK-1", root=contract.root,
        proc_root=source["proc"], daemon_started_monotonic=0, monotonic_now=31)
    assert observed.snapshot is not None
    assert {row[1] for row in observed.snapshot if row[0] == "task"} == {"TASK-0", "TASK-1", "TASK-2"}
    assert {row[1] for row in observed.snapshot if row[0] == "result"} == {"TASK-0", "TASK-1", "TASK-2"}
    assert {row[2] for row in observed.snapshot if row[0] == "job"} == {"JOB-0", "JOB-1", "JOB-2"}
    result = source["consume"]()
    assert result is not None and result.outcome == "completed"
    assert (result.reclaimed_bytes, result.reclaimed_inodes) == (
        expected_bytes, expected_inodes)
    assert not contract.root.exists()
    assert _protected_snapshot(source) == snapshots


@pytest.mark.parametrize("monotonic_now,daemon_started_monotonic,now_offset", [
    (29, 0, 121_000_000_000),       # startup < 30 seconds
    (31, None, 121_000_000_000),    # missing startup source
    (30, 0, 121_000_000_000),       # exact startup boundary admits
])
def test_private_consumer_startup_boundaries_are_actual_consumer_gates(
        disposable_consumer, monkeypatch, monotonic_now, daemon_started_monotonic, now_offset):
    source = disposable_consumer
    executed = []
    original = reclamation.execute_ledger
    monkeypatch.setattr(reclamation, "execute_ledger", lambda rows: (executed.extend(rows), original(rows))[1])
    before = _refusal_snapshot(source)
    protected_before = _protected_snapshot(source)
    expected_bytes, expected_inodes = _direct_accounting(source["contract"].root)
    result = source["consume"](monotonic_now=monotonic_now,
                               daemon_started_monotonic=daemon_started_monotonic,
                               now_ns=source["old"] + now_offset)
    if monotonic_now == 30:
        assert result is not None and result.outcome == "completed"
        assert len(executed) == 1
        assert (result.reclaimed_bytes, result.reclaimed_inodes) == (expected_bytes, expected_inodes)
        assert not source["contract"].root.exists()
        assert _protected_snapshot(source) == protected_before
    else:
        assert result is None and not executed
        assert _refusal_snapshot(source) == before


@pytest.mark.parametrize("newest_offset,completed_offset,wall_offset,accept", [
    (60_000_000_000, 100_000_000_000, 119_999_999_999, False), # < 60 mtime age only
    (60_000_000_000, 120_000_000_000, 120_000_000_000, True),  # = 60 and ordered
    (120_000_000_000, 120_000_000_000, 180_000_000_000, True), # newest == terminal admits
    (121_000_000_000, 120_000_000_000, 181_000_000_000, False), # newest after terminal
    (60_000_000_000, 121_000_000_000, 120_000_000_000, False),  # terminal after wall
])
def test_private_consumer_mtime_and_terminal_order_boundaries(
        disposable_consumer, monkeypatch, newest_offset, completed_offset, wall_offset, accept):
    source = disposable_consumer
    contract, old, db = source["contract"], source["old"], source["db"]
    for path in (contract.root / "nested/file", contract.root / "nested", contract.root):
        os.utime(path, ns=(old + newest_offset, old + newest_offset), follow_symlinks=False)
    db.update_task("TASK-1", completed_at=datetime.fromtimestamp(
        (old + completed_offset) / 1_000_000_000, tz=timezone.utc))
    executed = []
    original = reclamation.execute_ledger
    monkeypatch.setattr(reclamation, "execute_ledger", lambda rows: (executed.extend(rows), original(rows))[1])
    before = _refusal_snapshot(source)
    expected_bytes, expected_inodes = _direct_accounting(contract.root)
    result = source["consume"](now_ns=old + wall_offset)
    if accept:
        assert result is not None and result.outcome == "completed"
        assert executed[0].newest_mtime_ns == old + newest_offset
        assert old + newest_offset <= completed_offset + old <= old + wall_offset
        assert len(executed) == 1
        assert (result.reclaimed_bytes, result.reclaimed_inodes) == (expected_bytes, expected_inodes)
        assert not contract.root.exists()
        assert _protected_snapshot(source) == {path: value for path, value in before.items()
                                               if path != contract.root and not contract.root in path.parents}
    else:
        assert result is None and not executed
        assert _refusal_snapshot(source) == before


@pytest.mark.parametrize("residual", ["bytes", "entries"])
def test_private_consumer_requires_both_byte_and_entry_coverage_dominance(
        disposable_consumer, monkeypatch, residual):
    source = disposable_consumer
    extra = source["workspace"] / "residual"
    if residual == "bytes":
        extra.write_bytes(b"r" * 2_000_000)
    else:
        extra.mkdir()
        for index in range(240):
            (extra / str(index)).touch()
    observation = source["coverage"](workspace=source["workspace"], proc_root=source["proc"])
    assert not observation.coverage_ready
    buckets = {bucket.relative_path: bucket for bucket in observation.buckets}
    byte_paths = _selected_metric_paths(observation.buckets, "allocated_bytes")
    entry_paths = _selected_metric_paths(observation.buckets, "entries")
    if residual == "bytes":
        assert any(path.startswith("residual") for path in byte_paths)
        assert all(buckets[path].classification == "canonical_regenerable" for path in entry_paths)
    else:
        assert all(buckets[path].classification == "canonical_regenerable" for path in byte_paths)
        assert any(path.startswith("residual") for path in entry_paths)
    executed = []
    monkeypatch.setattr(reclamation, "execute_ledger", lambda rows: executed.extend(rows))
    before = _refusal_snapshot(source)
    assert source["consume"]() is None
    assert not executed and _refusal_snapshot(source) == before


@pytest.mark.parametrize("mode", ["missing_member", "old_target_session", "working", "in_progress", "lookup_error"])
def test_private_consumer_requires_current_terminal_result_for_each_component(
        disposable_consumer, monkeypatch, mode):
    """N2: public measurements survive result loss, private consumption does not."""
    source, db = disposable_consumer, disposable_consumer["db"]
    if mode == "missing_member":
        # The target remains valid: only the parent component lacks a result
        # for its own current session.
        db.update_task("TASK-0", current_session_id="new-parent-session")
    elif mode == "old_target_session":
        # These are real durable rows: the retained result belongs to the prior
        # session and cannot establish current private-consumption authority.
        db.update_task("TASK-1", current_session_id="new-session")
    elif mode == "working":
        db.insert_task_result("TASK-1", "dev_agent", "session", "still working", 2,
                              status="working")
    elif mode == "in_progress":
        db.insert_task_result("TASK-1", "dev_agent", "session", "still running", 2,
                              status="in_progress")
    else:
        original_lookup = db.get_latest_task_result
        monkeypatch.setattr(db, "get_latest_task_result", lambda *args: (_ for _ in ()).throw(OSError("lookup unavailable"))
                                 if args[0] == "TASK-1" else original_lookup(*args))
    public = source["evidence"](db=db, sessions=source["sessions"], task_id="TASK-1",
                                 root=source["contract"].root, proc_root=source["proc"],
                                 daemon_started_monotonic=0, monotonic_now=31)
    if mode in {"missing_member", "old_target_session"}:
        assert public.eligible
        assert (public.process_roots, public.process_cwds, public.open_fds) == (0, 0, 0)
    else:
        assert not public.eligible
    private = reclamation._collect_private_evidence(
        db=db, sessions=source["sessions"], task_id="TASK-1", root=source["contract"].root,
        proc_root=source["proc"], daemon_started_monotonic=0, monotonic_now=31)
    if mode == "missing_member":
        results = {row[1]: row[3] for row in private.snapshot if row[0] == "result"}
        assert results["TASK-1"] is not None and results["TASK-0"] is None
    executed = []
    monkeypatch.setattr(reclamation, "execute_ledger", lambda rows: executed.extend(rows))
    before = _refusal_snapshot(source)
    assert source["consume"]() is None
    assert not executed and _refusal_snapshot(source) == before


def test_private_consumer_distinguishes_measured_zero_from_unavailable_proc(disposable_consumer, monkeypatch):
    source = disposable_consumer
    complete = source["evidence"](db=source["db"], sessions=source["sessions"], task_id="TASK-1",
                                  root=source["contract"].root, proc_root=source["proc"],
                                  daemon_started_monotonic=0, monotonic_now=31)
    unavailable = source["evidence"](db=source["db"], sessions=source["sessions"], task_id="TASK-1",
                                     root=source["contract"].root, proc_root=source["proc"] / "missing",
                                     daemon_started_monotonic=0, monotonic_now=31)
    assert (complete.process_roots, complete.process_cwds, complete.open_fds) == (0, 0, 0)
    assert (unavailable.process_roots, unavailable.process_cwds, unavailable.open_fds) == (None, None, None)
    executed = []
    monkeypatch.setattr(reclamation, "execute_ledger", lambda rows: executed.extend(rows))
    before = _refusal_snapshot(source)
    assert source["consume"](proc_root=source["proc"] / "missing") is None
    assert not executed and _refusal_snapshot(source) == before
    # A readable proc tree with only the boot source missing is independently
    # unavailable; it is not the same as an absent proc root.
    (source["proc"] / "sys/kernel/random/boot_id").unlink()
    before = _refusal_snapshot(source)
    missing_boot = source["evidence"](db=source["db"], sessions=source["sessions"], task_id="TASK-1",
                                       root=source["contract"].root, proc_root=source["proc"],
                                       daemon_started_monotonic=0, monotonic_now=31)
    assert missing_boot.boot_id is None
    assert source["consume"]() is None
    assert not executed and _refusal_snapshot(source) == before


@pytest.mark.parametrize("coverage_mode", ["ready", "naturally_nonready"])
def test_private_consumer_partial_unlink_claims_zero_and_refuses_remainder(
        disposable_consumer, monkeypatch, coverage_mode):
    """R1 is a consumer-level refusal, never a repaired-timestamp recovery."""
    source = disposable_consumer
    contract = source["contract"]
    snapshots = _protected_snapshot(source)
    membership = _named_membership(source)
    before = {path.relative_to(contract.root) for path in contract.root.rglob("*")}
    rows = []
    seals = []
    original_execute = reclamation.execute_ledger
    original_seal = reclamation.seal_ledger_row
    original_unlink = os.unlink
    residual = source["workspace"] / "post-failure-residual"

    def record_execute(sealed):
        rows.extend(sealed)
        return original_execute(sealed)

    def record_seal(**kwargs):
        seals.append(kwargs)
        return original_seal(**kwargs)

    def fail_late(path, *args, **kwargs):
        if path == "payload-link-49":
            if coverage_mode == "naturally_nonready":
                residual.write_bytes(b"not canonical" * 300_000)
            raise OSError("injected partial unlink")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(reclamation, "execute_ledger", record_execute)
    monkeypatch.setattr(reclamation, "seal_ledger_row", record_seal)
    monkeypatch.setattr(os, "unlink", fail_late)
    failed = source["consume"]()
    monkeypatch.setattr(os, "unlink", original_unlink)
    assert failed is not None and failed.outcome == "failed"
    assert failed.reclaimed_bytes == failed.reclaimed_inodes == 0
    assert failed.after is not None
    assert len(rows) == 1 and contract.root.exists()
    after = {path.relative_to(contract.root) for path in contract.root.rglob("*")}
    assert after < before  # The injected syscall happened after real partial removal.
    assert _protected_snapshot(source) == snapshots
    assert _named_membership(source) == membership
    remainder = _refusal_snapshot(source)
    if residual.exists():
        remainder |= _snapshot([residual])
    old, = original_execute((rows[0],))
    assert old.outcome == "failed" and old.reclaimed_bytes == old.reclaimed_inodes == 0
    current = _refusal_snapshot(source)
    if residual.exists():
        current |= _snapshot([residual])
    assert current == remainder
    assert _protected_snapshot(source) == snapshots
    assert _named_membership(source) == membership
    if coverage_mode == "ready":
        fresh = source["coverage"](workspace=source["workspace"], proc_root=source["proc"])
        assert fresh.coverage_ready, (fresh.reasons, fresh.buckets)
    else:
        coverage = source["coverage"](workspace=source["workspace"], proc_root=source["proc"])
        assert not coverage.coverage_ready
    assert source["consume"]() is None
    assert len(seals) == (2 if coverage_mode == "ready" else 1)
    assert len(rows) == 1
    assert contract.root.exists()
    current = _refusal_snapshot(source)
    if residual.exists():
        current |= _snapshot([residual])
    assert current == remainder
    assert _protected_snapshot(source) == snapshots
    assert _named_membership(source) == membership


@pytest.mark.parametrize("field,value", [
    ("task_id", "TASK-other"), ("agent_name", "other-agent"),
    ("literal_root", "/wrong/root"), ("manifest_path", "/wrong/manifest"),
    ("root_inode", 0),
])
def test_private_consumer_refuses_actual_sealed_row_substitutions(
        disposable_consumer, monkeypatch, field, value):
    """N3: substitutions at the actual post-seal consumer seam never execute."""
    source = disposable_consumer
    original = reclamation.seal_ledger_row
    reached, executed = [], []
    before, membership = _refusal_snapshot(source), _named_membership(source)

    def substituted(**kwargs):
        reached.append(True)
        return replace(original(**kwargs), **{field: value})

    monkeypatch.setattr(reclamation, "seal_ledger_row", substituted)
    monkeypatch.setattr(reclamation, "execute_ledger", lambda rows: executed.extend(rows))
    assert source["consume"]() is None
    assert reached and not executed
    assert _refusal_snapshot(source) == before
    assert _named_membership(source) == membership


@pytest.mark.parametrize("mutation", ["manifest", "item_identity", "item_accounting", "population"])
def test_private_consumer_refuses_actual_c3_projection_substitutions(
        disposable_consumer, monkeypatch, mutation):
    """N3: C3's typed manifest/census projections must still bind the sealed row."""
    source = disposable_consumer
    original = reclamation._collect_private_coverage
    calls, reached, executed, sealed = 0, [], [], []
    before, membership = _refusal_snapshot(source), _named_membership(source)
    root_rel = ".happyranch/task-tmp/TASK-1"

    def collect(**kwargs):
        nonlocal calls
        calls += 1
        value = original(**kwargs)
        if calls != 3:
            return value
        reached.append("C3")
        snapshot = value.snapshot
        if mutation == "manifest":
            manifests = tuple((rel, b"{}" if rel == root_rel else raw, classification)
                              for rel, raw, classification in snapshot.manifests)
            snapshot = replace(snapshot, manifests=manifests)
        elif mutation in {"item_identity", "item_accounting"}:
            items = tuple(replace(item, **({"ino": item.ino + 1} if mutation == "item_identity"
                                             else {"blocks": item.blocks + 1}))
                          if item.rel == root_rel else item for item in snapshot.items)
            snapshot = replace(snapshot, items=items)
        else:
            snapshot = replace(snapshot, populations=(*snapshot.populations, ("injected", ("42",))))
        return replace(value, snapshot=snapshot)

    original_seal = reclamation.seal_ledger_row
    def seal(**kwargs):
        row = original_seal(**kwargs)
        sealed.append(row)
        return row

    monkeypatch.setattr(reclamation, "_collect_private_coverage", collect)
    monkeypatch.setattr(reclamation, "seal_ledger_row", seal)
    monkeypatch.setattr(reclamation, "execute_ledger", lambda rows: executed.extend(rows))
    assert source["consume"]() is None
    assert reached == ["C3"] and sealed and not executed
    assert _refusal_snapshot(source) == before
    assert _named_membership(source) == membership


def test_protected_named_membership_observes_add_and_remove(disposable_consumer):
    """The snapshot supplement sees immediate protected sidecar membership changes."""
    source = disposable_consumer
    before = _named_membership(source)
    extra = source["repo"] / ".git" / "config"
    extra.write_text("[core]\n")
    assert _named_membership(source) != before
    extra.unlink()
    assert _named_membership(source) == before


@pytest.mark.parametrize("mutation", ["process_identity", "sealed_agent", "coverage_workspace"])
def test_private_consumer_refuses_changed_liveness_or_canonical_binding_before_executor(
        tmp_path, monkeypatch, mutation, disposable_consumer):
    """Each final binding must correspond to this operation, not merely itself."""
    source = disposable_consumer
    executed, reached = [], []
    before, membership = _refusal_snapshot(source), _named_membership(source)
    original_execute = reclamation.execute_ledger
    monkeypatch.setattr(reclamation, "execute_ledger", lambda rows: (executed.extend(rows), original_execute(rows))[1])
    if mutation == "process_identity":
        original_seal = reclamation.seal_ledger_row
        def seal(**kwargs):
            row = original_seal(**kwargs)
            reached.append("seal")
            stat_path = tmp_path / "proc/42/stat"
            fields = stat_path.read_text().split(); fields[21] = str(int(fields[21]) + 1)
            stat_path.write_text(" ".join(fields))
            return row
        monkeypatch.setattr(reclamation, "seal_ledger_row", seal)
    elif mutation == "sealed_agent":
        original_seal = reclamation.seal_ledger_row
        def seal(**kwargs):
            reached.append("seal")
            return original_seal(**{**kwargs, "assertions": replace(
                kwargs["assertions"], agent_name="other_agent")})
        monkeypatch.setattr(reclamation, "seal_ledger_row", seal)
    else:
        original_collect = reclamation._collect_private_coverage
        calls = 0
        def collect(**kwargs):
            nonlocal calls
            calls += 1
            value = original_collect(**kwargs)
            if calls == 3:
                reached.append("C3")
                return replace(value, snapshot=replace(value.snapshot, workspace_id=(0, 0)))
            return value
        monkeypatch.setattr(reclamation, "_collect_private_coverage", collect)
    assert source["consume"]() is None
    assert reached and not executed
    assert _refusal_snapshot(source) == before
    assert _named_membership(source) == membership


def test_private_consumer_refuses_publicly_eligible_absent_result(tmp_path):
    """A public None-result observation is not authority to consume."""
    proc = tmp_path / "proc"
    (proc / "sys/kernel/random").mkdir(parents=True)
    (proc / "sys/kernel/random/boot_id").write_text("123e4567-e89b-42d3-a456-426614174000\n")
    process = proc / "42"; (process / "fd").mkdir(parents=True)
    (process / "stat").write_text("42 (agent) S 1 1 1 1 1 1 1 1 1 1 1 1 1 1 1 1 1 1 9 0")
    (process / "root").symlink_to("/"); (process / "cwd").symlink_to("/")
    db = Database(tmp_path / "state.db")
    try:
        db.insert_task(TaskRecord(id="TASK-1", status=TaskStatus.COMPLETED, brief="x",
                                  assigned_agent="dev_agent", current_session_id="session",
                                  completed_at=datetime.now(timezone.utc)))
        root = tmp_path / "root"; root.mkdir()
        from runtime.daemon.task_scratch_evidence import collect_task_scratch_evidence
        assert collect_task_scratch_evidence(
            db=db, sessions=SessionTracker(), task_id="TASK-1", root=root,
            proc_root=proc, daemon_started_monotonic=0, monotonic_now=31).eligible
        private = reclamation._collect_private_evidence(
            db=db, sessions=SessionTracker(), task_id="TASK-1", root=root,
            proc_root=proc, daemon_started_monotonic=0, monotonic_now=31)
        assert reclamation._private_evidence_ok(private, "TASK-1", "dev_agent") is None
    finally:
        db.close()


@pytest.mark.parametrize("stage", ["E1", "E2", "E3", "E4", "C1", "C2", "C3"])
@pytest.mark.parametrize("mode", ["none", "exception", "incomplete", "malformed", "timeout", "capped"])
def test_private_consumer_refuses_each_unavailable_collector_binding(
        tmp_path, monkeypatch, stage, mode, disposable_consumer):
    """Every collection boundary refuses before an executor can mutate a root."""
    symbol = "_collect_private_evidence" if stage[0] == "E" else "_collect_private_coverage"
    original = getattr(reclamation, symbol)
    calls = 0
    executed, sealed = [], []
    before, membership = _refusal_snapshot(disposable_consumer), _named_membership(disposable_consumer)
    original_seal = reclamation.seal_ledger_row

    def seal(**kwargs):
        row = original_seal(**kwargs)
        sealed.append(row)
        return row

    def collect(**kwargs):
        nonlocal calls
        calls += 1
        value = original(**kwargs)
        if calls != int(stage[1]):
            return value
        if mode == "none":
            return None
        if mode == "exception":
            raise OSError("disposable unavailable source")
        if mode == "timeout":
            raise TimeoutError("controlled collector deadline")
        if mode == "capped":
            raise RuntimeError("controlled collector cap")
        if mode == "malformed":
            if stage[0] == "E":
                return replace(value, evidence=object())
            return replace(value, observation=object())
        if stage[0] == "E":
            return replace(value, evidence=replace(value.evidence, eligible=False), snapshot=None)
        return replace(value, observation=replace(value.observation, complete=False), snapshot=None)

    monkeypatch.setattr(reclamation, symbol, collect)
    monkeypatch.setattr(reclamation, "seal_ledger_row", seal)
    monkeypatch.setattr(reclamation, "execute_ledger", lambda rows: executed.extend(rows))
    assert disposable_consumer["consume"]() is None
    assert calls >= int(stage[1]) and not executed
    # E3/C3/E4 are the post-seal admissions; their refusal cannot be hidden by
    # an earlier gate.  E1/C1/E2/C2 must remain pre-seal.
    assert bool(sealed) is (stage in {"E3", "C3", "E4"})
    assert _refusal_snapshot(disposable_consumer) == before
    assert _named_membership(disposable_consumer) == membership


@pytest.mark.parametrize("stage,kind,reason", [
    ("E1", "deadline", "observation_timeout"),
    ("E3", "deadline", "observation_timeout"),
    ("C1", "cap", "read_cap"),
    ("C3", "cap", "read_cap"),
])
def test_private_consumer_refuses_actual_collector_admission_exhaustion(
        disposable_consumer, monkeypatch, stage, kind, reason):
    """Real selected E/C admissions retain their deadline and finite-cap refusal."""
    source = disposable_consumer
    symbol = "_collect_private_evidence" if stage[0] == "E" else "_collect_private_coverage"
    original = getattr(reclamation, symbol)
    calls, exhausted, executed, sealed = 0, [], [], []
    before, membership = _refusal_snapshot(source), _named_membership(source)
    original_seal = reclamation.seal_ledger_row

    def seal(**kwargs):
        row = original_seal(**kwargs)
        sealed.append(row)
        return row

    def collect(**kwargs):
        nonlocal calls
        calls += 1
        if calls != int(stage[1]):
            return original(**kwargs)
        if kind == "deadline":
            with monkeypatch.context() as local:
                local.setattr(scratch_evidence, "SCAN_NS", -1)
                value = original(**kwargs)
            assert reason in value.evidence.reasons and value.snapshot is None
        else:
            with monkeypatch.context() as local:
                local.setattr(scratch_coverage, "MAX_READS", 0)
                value = original(**kwargs)
            assert reason in value.observation.reasons and value.snapshot is None
        exhausted.append(value)
        return value

    monkeypatch.setattr(reclamation, symbol, collect)
    monkeypatch.setattr(reclamation, "seal_ledger_row", seal)
    monkeypatch.setattr(reclamation, "execute_ledger", lambda rows: executed.extend(rows))
    assert source["consume"]() is None
    # The consumer finishes its fixed pre/post collection phase, but the
    # exhausted collector itself receives no retry: evidence is one pass and
    # coverage remains its own two-pass collection.
    assert calls == int(stage[1]) + (stage != "C3") and exhausted and not executed
    assert bool(sealed) is (stage in {"E3", "C3"})
    assert _refusal_snapshot(source) == before
    assert _named_membership(source) == membership


def test_private_consumer_allows_genuine_observed_timestamp_only_changes(
        disposable_consumer, monkeypatch):
    """Fresh typed collector timestamps alone are not a changed authoritative binding."""
    source = disposable_consumer
    evidence_seen, coverage_seen, executed = [], [], []
    evidence_original = reclamation._collect_private_evidence
    coverage_original = reclamation._collect_private_coverage
    execute_original = reclamation.execute_ledger
    expected_bytes, expected_inodes = _direct_accounting(source["contract"].root)
    protected = _protected_snapshot(source)

    def evidence(**kwargs):
        value = evidence_original(**kwargs)
        evidence_seen.append(value)
        return value

    def coverage(**kwargs):
        value = coverage_original(**kwargs)
        coverage_seen.append(value)
        return value

    def execute(rows):
        executed.extend(rows)
        return execute_original(rows)

    # These are the collectors' own clock reads: all typed return values are
    # real, with only their observation timestamps advancing between calls.
    tick = iter(range(10_000, 10_100))
    monkeypatch.setattr(scratch_evidence.time, "time_ns", lambda: next(tick))
    monkeypatch.setattr(reclamation, "_collect_private_evidence", evidence)
    monkeypatch.setattr(reclamation, "_collect_private_coverage", coverage)
    monkeypatch.setattr(reclamation, "execute_ledger", execute)
    result = source["consume"]()
    assert result is not None and result.outcome == "completed" and len(executed) == 1
    assert (result.reclaimed_bytes, result.reclaimed_inodes) == (expected_bytes, expected_inodes)
    assert len({item.evidence.observed_at_ns for item in evidence_seen}) > 1
    assert len({item.observation.observed_at_ns for item in coverage_seen}) > 1
    assert not source["contract"].root.exists()
    assert _protected_snapshot(source) == protected


@pytest.mark.parametrize("field", ["sessions", "process_identities"])
def test_private_consumer_refuses_unavailable_private_identity(
        tmp_path, monkeypatch, field, disposable_consumer):
    """None is unavailable; only measured (including empty) tuples may be stable."""
    original = reclamation._collect_private_evidence
    executed, reached = [], []
    before, membership = _refusal_snapshot(disposable_consumer), _named_membership(disposable_consumer)
    def unavailable(**kwargs):
        reached.append(True)
        return replace(original(**kwargs), **{field: None})
    monkeypatch.setattr(
        reclamation, "_collect_private_evidence",
        unavailable,
    )
    monkeypatch.setattr(reclamation, "execute_ledger", lambda rows: executed.extend(rows))
    assert disposable_consumer["consume"]() is None
    assert reached and not executed
    assert _refusal_snapshot(disposable_consumer) == before
    assert _named_membership(disposable_consumer) == membership


def test_private_consumer_refuses_coverage_snapshot_with_unbound_boot(
        tmp_path, monkeypatch, disposable_consumer):
    """A self-consistent coverage snapshot still must literally join public boot."""
    original = reclamation._collect_private_coverage
    executed, reached = [], []
    before, membership = _refusal_snapshot(disposable_consumer), _named_membership(disposable_consumer)

    def changed(**kwargs):
        reached.append(True)
        value = original(**kwargs)
        return replace(value, snapshot=replace(
            value.snapshot, boot="223e4567-e89b-42d3-a456-426614174000"))

    monkeypatch.setattr(reclamation, "_collect_private_coverage", changed)
    monkeypatch.setattr(reclamation, "execute_ledger", lambda rows: executed.extend(rows))
    assert disposable_consumer["consume"]() is None
    assert reached and not executed
    assert _refusal_snapshot(disposable_consumer) == before
    assert _named_membership(disposable_consumer) == membership


@pytest.mark.parametrize("field,value", [
    ("task_status", "in_progress"),
    ("nonterminal_revisits", 1),
    ("zombie_recovery", ZombieRecoveryState.PENDING),
    ("zombie_recovery", ZombieRecoveryState.AMBIGUOUS),
    ("active_jobs", 1),
    ("pending_recovery_consumers", 1),
    ("active_chain_members", 1),
    ("truncated", True),
    ("ambiguous", True),
    ("unavailable", True),
])
def test_incomplete_lifecycle_assertions_fail_before_ledger(field, value, tmp_path):
    workspace, _, assertions, old = _candidate(tmp_path)
    with pytest.raises(ReclamationError, match="lifecycle assertions"):
        seal_ledger_row(workspace=workspace, task_id="TASK-1",
                        assertions=replace(assertions, lifecycle=replace(
                            assertions.lifecycle, **{field: value})),
                        now_ns=old + 121_000_000_000)


@pytest.mark.parametrize("field,value", [
    ("complete", False), ("truncated", True), ("ambiguous", True),
    ("permission_denied", True), ("live_sessions", 1), ("process_roots", 1),
    ("process_cwds", 1), ("open_fds", 1), ("observed_boot_id", "stale-boot"),
])
def test_liveness_ambiguity_and_live_references_fail_closed(field, value, tmp_path):
    workspace, _, evidence, old = _candidate(tmp_path)
    with pytest.raises(ReclamationError, match="liveness"):
        seal_ledger_row(workspace=workspace, task_id="TASK-1",
                        assertions=replace(evidence, liveness=replace(evidence.liveness, **{field: value})),
                        now_ns=old + 121_000_000_000)


@pytest.mark.parametrize("field,value", [
    ("complete", False), ("truncated", True), ("ambiguous", True), ("unavailable", True),
    ("dominant_unclassified", 1), ("dominant_durable", 1),
    ("dominant_recovery", 1), ("observed_boot_id", "stale-boot"),
])
def test_coverage_missing_stale_truncated_or_ambiguous_fails_closed(field, value, tmp_path):
    workspace, _, evidence, old = _candidate(tmp_path)
    with pytest.raises(ReclamationError, match="coverage"):
        seal_ledger_row(workspace=workspace, task_id="TASK-1",
                        assertions=replace(evidence, coverage=replace(evidence.coverage, **{field: value})),
                        now_ns=old + 121_000_000_000)


@pytest.mark.parametrize("shape", [
    LifecycleAssertions, LivenessAssertions, CoverageAssertions,
])
def test_assertion_shapes_have_no_defaults(shape):
    assert all(parameter.default is inspect.Parameter.empty
               for parameter in inspect.signature(shape).parameters.values())


@pytest.mark.parametrize("component,field,value,error", [
    ("lifecycle", "terminal_assertion", "", "lifecycle"),
    ("lifecycle", "terminal_observed_at_ns", True, "lifecycle"),
    ("lifecycle", "nonterminal_revisits", True, "lifecycle"),
    ("lifecycle", "zombie_recovery", "clear", "lifecycle"),
    ("liveness", "census_assertion", "", "liveness"),
    ("liveness", "complete", 1, "liveness"),
    ("liveness", "unavailable", True, "liveness"),
    ("coverage", "coverage_assertion", "", "coverage"),
    ("coverage", "digest_assertion", "g" * 64, "coverage"),
    ("coverage", "dominant_durable", True, "coverage"),
])
def test_malformed_or_contradictory_assertions_fail_closed(
        component, field, value, error, tmp_path):
    workspace, _, assertions, old = _candidate(tmp_path)
    changed = replace(getattr(assertions, component), **{field: value})
    with pytest.raises(ReclamationError, match=error):
        seal_ledger_row(workspace=workspace, task_id="TASK-1",
                        assertions=replace(assertions, **{component: changed}),
                        now_ns=old + 121_000_000_000)


def test_assertion_provenance_boundary_is_honest(tmp_path):
    workspace, _, assertions, old = _candidate(tmp_path)
    fabricated = replace(
        assertions,
        lifecycle=replace(assertions.lifecycle, terminal_assertion="caller-made"),
        liveness=replace(assertions.liveness, census_assertion="caller-made"),
        coverage=replace(assertions.coverage, coverage_assertion="caller-made",
                         digest_assertion="0" * 64),
    )
    row = seal_ledger_row(workspace=workspace, task_id="TASK-1", assertions=fabricated,
                          now_ns=old + 121_000_000_000)
    assert row.terminal_assertion == "caller-made"
    assert "untrusted" in (ReclamationAssertions.__doc__ or "").lower()
    assert not any(name.endswith("Evidence") or name == "AuthorityEvidence"
                   for name in vars(reclamation))


def test_unsupported_platform_and_cross_evidence_boot_fail_closed(tmp_path):
    workspace, _, evidence, old = _candidate(tmp_path)
    with pytest.raises(ReclamationError, match="liveness"):
        seal_ledger_row(workspace=workspace, task_id="TASK-1",
                        assertions=replace(evidence, liveness=replace(
                            evidence.liveness, platform="windows")),  # type: ignore[arg-type]
                        now_ns=old + 121_000_000_000)
    with pytest.raises(ReclamationError, match="boot mismatch"):
        seal_ledger_row(workspace=workspace, task_id="TASK-1",
                        assertions=replace(evidence, coverage=replace(
                            evidence.coverage, boot_id="boot-2", observed_boot_id="boot-2")),
                        now_ns=old + 121_000_000_000)


def test_exact_mtime_boundary_and_terminal_order(tmp_path):
    workspace, _, evidence, old = _candidate(tmp_path)
    evidence = replace(evidence, lifecycle=replace(
        evidence.lifecycle, terminal_observed_at_ns=old))
    assert seal_ledger_row(workspace=workspace, task_id="TASK-1", assertions=evidence,
                           now_ns=old + 60_000_000_000).newest_mtime_ns == old
    with pytest.raises(ReclamationError, match="mtime"):
        seal_ledger_row(workspace=workspace, task_id="TASK-1", assertions=evidence,
                        now_ns=old + 59_999_999_999)
    with pytest.raises(ReclamationError, match="mtime"):
        seal_ledger_row(workspace=workspace, task_id="TASK-1",
                        assertions=replace(evidence, lifecycle=replace(
                            evidence.lifecycle, terminal_observed_at_ns=old - 1)),
                        now_ns=old + 60_000_000_000)


def test_manifest_path_mismatch_and_git_evidence_fail_closed(tmp_path):
    workspace, contract, evidence, old = _candidate(tmp_path)
    contract.manifest_path.write_text(contract.manifest_path.read_text().replace(
        str(contract.root), "/attacker/root"))
    with pytest.raises(ReclamationError, match="manifest invalid"):
        seal_ledger_row(workspace=workspace, task_id="TASK-1", assertions=evidence,
                        now_ns=old + 121_000_000_000)


@pytest.mark.parametrize("kind", ["symlink", "fifo"])
def test_manifest_symlink_or_fifo_fails_closed(kind, tmp_path):
    workspace, contract, assertions, old = _candidate(tmp_path)
    manifest = contract.manifest_path
    manifest.unlink()
    if kind == "symlink":
        target = tmp_path / "outside-manifest"
        target.write_text("{}")
        manifest.symlink_to(target)
    else:
        os.mkfifo(manifest)
    with pytest.raises(ReclamationError):
        seal_ledger_row(workspace=workspace, task_id="TASK-1", assertions=assertions,
                        now_ns=old + 121_000_000_000)


def test_root_symlink_and_task_prefix_confusion_fail_closed(tmp_path):
    workspace, contract, assertions, old = _candidate(tmp_path)
    moved = contract.root.with_name("TASK-10")
    contract.root.rename(moved)
    contract.root.symlink_to(moved, target_is_directory=True)
    with pytest.raises(ReclamationError, match="literal directory"):
        seal_ledger_row(workspace=workspace, task_id="TASK-1", assertions=assertions,
                        now_ns=old + 121_000_000_000)
    assert moved.is_dir()


def test_repository_stat_ambiguity_fails_closed(monkeypatch, tmp_path):
    workspace, contract, assertions, old = _candidate(tmp_path)
    original = Path.stat

    def denied(path, *args, **kwargs):
        if path == contract.root / ".git":
            raise PermissionError("injected")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", denied)
    with pytest.raises(ReclamationError, match="repository classification unavailable"):
        seal_ledger_row(workspace=workspace, task_id="TASK-1", assertions=assertions,
                        now_ns=old + 121_000_000_000)


def test_census_cap_and_read_failure_fail_closed(monkeypatch, tmp_path):
    workspace, _, assertions, old = _candidate(tmp_path)
    monkeypatch.setattr(reclamation, "MAX_CENSUS_ENTRIES", 1)
    with pytest.raises(ReclamationError, match="cap"):
        seal_ledger_row(workspace=workspace, task_id="TASK-1", assertions=assertions,
                        now_ns=old + 121_000_000_000)
    monkeypatch.setattr(reclamation, "MAX_CENSUS_ENTRIES", 100_000)
    original = os.scandir

    def unavailable(path):
        if Path(path).name == "nested":
            raise TimeoutError("injected timeout")
        return original(path)

    monkeypatch.setattr(os, "scandir", unavailable)
    with pytest.raises(ReclamationError, match="census unavailable"):
        seal_ledger_row(workspace=workspace, task_id="TASK-1", assertions=assertions,
                        now_ns=old + 121_000_000_000)


@pytest.mark.parametrize("location", ["root", "descendant", "ancestor"])
def test_repository_and_worktree_signatures_fail_closed(location, tmp_path):
    workspace, contract, evidence, old = _candidate(tmp_path)
    target = {"root": contract.root, "descendant": contract.root / "nested",
              "ancestor": workspace}[location]
    (target / ".git").write_text("gitdir: /hostile/worktree")
    with pytest.raises(ReclamationError, match="repository"):
        seal_ledger_row(workspace=workspace, task_id="TASK-1", assertions=evidence,
                        now_ns=old + 121_000_000_000)


def test_bare_repository_signature_fails_closed(tmp_path):
    workspace, contract, evidence, old = _candidate(tmp_path)
    (contract.root / "HEAD").write_text("ref: refs/heads/main\n")
    (contract.root / "objects").mkdir()
    (contract.root / "refs").mkdir()
    with pytest.raises(ReclamationError, match="repository"):
        seal_ledger_row(workspace=workspace, task_id="TASK-1", assertions=evidence,
                        now_ns=old + 121_000_000_000)


@pytest.mark.parametrize("mutation", [
    "missing", "corrupt", "oversized", "unknown", "stale-version", "producer-overflow",
    "nonregular",
])
def test_hostile_manifest_shapes_fail_closed(mutation, tmp_path):
    workspace, contract, evidence, old = _candidate(tmp_path)
    manifest = contract.manifest_path
    if mutation == "missing":
        manifest.unlink()
    elif mutation == "corrupt":
        manifest.write_text("{")
    elif mutation == "oversized":
        manifest.write_bytes(b"x" * (64 * 1024 + 1))
    elif mutation == "unknown":
        data = json.loads(manifest.read_text())
        data["hostile"] = True
        manifest.write_text(json.dumps(data))
    elif mutation == "stale-version":
        data = json.loads(manifest.read_text())
        data["version"] = 0
        manifest.write_text(json.dumps(data))
    elif mutation == "producer-overflow":
        data = json.loads(manifest.read_text())
        data["producers"] = data["producers"] * 129
        manifest.write_text(json.dumps(data))
    else:
        manifest.unlink()
        manifest.mkdir()
    with pytest.raises(ReclamationError):
        seal_ledger_row(workspace=workspace, task_id="TASK-1", assertions=evidence,
                        now_ns=old + 121_000_000_000)
    workspace, contract, evidence, old = _candidate(tmp_path / "second")
    (contract.root / ".git").mkdir()
    os.utime(contract.root / ".git", ns=(old, old))
    with pytest.raises(ReclamationError, match="git"):
        seal_ledger_row(workspace=workspace, task_id="TASK-1", assertions=evidence,
                        now_ns=old + 121_000_000_000)


def test_symlink_and_fifo_are_unlinked_without_following(tmp_path):
    workspace, contract, evidence, old = _candidate(tmp_path)
    outside = tmp_path / "outside"
    outside.write_text("protected")
    (contract.root / "link").symlink_to(outside)
    os.mkfifo(contract.root / "pipe")
    for path in (contract.root / "link", contract.root / "pipe", contract.root):
        os.utime(path, ns=(old, old), follow_symlinks=False)
    row = seal_ledger_row(workspace=workspace, task_id="TASK-1", assertions=evidence,
                          now_ns=old + 121_000_000_000)
    result, = execute_ledger((row,))
    assert result.outcome == "completed"
    assert outside.read_text() == "protected"


def test_post_finalization_mutation_is_rejected_without_reclaimed_claim(tmp_path):
    workspace, contract, evidence, old = _candidate(tmp_path)
    row = seal_ledger_row(workspace=workspace, task_id="TASK-1", assertions=evidence,
                          now_ns=old + 121_000_000_000)
    (contract.root / "late").write_text("race")
    result, = execute_ledger((row,))
    assert result.outcome == "failed"
    assert result.reclaimed_bytes == result.reclaimed_inodes == 0
    assert contract.root.exists()


@pytest.mark.parametrize("target", ["manifest", "lock", "parent-entry", "workspace"])
def test_protected_path_mutation_fails_with_zero_claim(target, tmp_path):
    workspace, contract, assertions, old = _candidate(tmp_path)
    row = seal_ledger_row(workspace=workspace, task_id="TASK-1", assertions=assertions,
                          now_ns=old + 121_000_000_000)
    if target == "manifest":
        contract.manifest_path.write_text(contract.manifest_path.read_text() + " ")
    elif target == "lock":
        contract.manifest_path.with_suffix(".lock").write_text("changed")
    elif target == "parent-entry":
        (contract.root.parent / "late-sibling").mkdir()
    else:
        workspace.rename(tmp_path / "moved-workspace")
    result, = execute_ledger((row,))
    assert result.outcome == "failed"
    assert result.reclaimed_bytes == result.reclaimed_inodes == 0


def test_accounting_mismatch_and_unavailable_after_are_zero_claim(monkeypatch, tmp_path):
    workspace, contract, assertions, old = _candidate(tmp_path)
    row = seal_ledger_row(workspace=workspace, task_id="TASK-1", assertions=assertions,
                          now_ns=old + 121_000_000_000)
    original = reclamation._census
    calls = 0

    def mismatched(root, **kwargs):
        nonlocal calls
        calls += 1
        entries, accounting = original(root, **kwargs)
        if calls == 1:
            return entries, replace(accounting, allocated_bytes=accounting.allocated_bytes + 1)
        contract.root.rename(contract.root.with_name("unavailable-after"))
        raise ReclamationError("after unavailable")

    monkeypatch.setattr(reclamation, "_census", mismatched)
    result, = execute_ledger((row,))
    assert result.outcome == "failed"
    assert result.after is None
    assert result.reclaimed_bytes == result.reclaimed_inodes == 0


def test_final_root_rename_recreate_cannot_report_completed(monkeypatch, tmp_path):
    workspace, contract, evidence, old = _candidate(tmp_path)
    row = seal_ledger_row(workspace=workspace, task_id="TASK-1", assertions=evidence,
                          now_ns=old + 121_000_000_000)
    original_rmdir = os.rmdir
    renamed = contract.root.with_name("renamed-sealed-root")

    def hostile_rmdir(path, *args, **kwargs):
        if path == row.task_id and kwargs.get("dir_fd") is not None:
            os.rename(contract.root, renamed)
            contract.root.mkdir()
        return original_rmdir(path, *args, **kwargs)

    monkeypatch.setattr(os, "rmdir", hostile_rmdir)
    result, = execute_ledger((row,))
    assert result.outcome == "failed"
    assert result.reclaimed_bytes == result.reclaimed_inodes == 0
    assert renamed.exists()


@pytest.mark.parametrize("entry_kind", ["file", "directory"])
def test_final_pathname_swap_is_characterized_without_false_success(
        entry_kind, monkeypatch, tmp_path):
    """Portable unlink/rmdir is name-bound, not inode-bound.

    A hostile same-UID replacement in the final syscall window is deliberately
    outside the threat contract.  Once the displaced ledger entry makes the
    mismatch detectable, the row must still fail with zero reclaimed claims.
    """
    workspace, contract, evidence, old = _candidate(tmp_path)
    row = seal_ledger_row(workspace=workspace, task_id="TASK-1", assertions=evidence,
                          now_ns=old + 121_000_000_000)
    nested = contract.root / "nested"

    if entry_kind == "file":
        original_action = os.unlink
        original = nested / "file"
        displaced = nested / "displaced-file"

        def hostile_action(path, *args, **kwargs):
            if path == "file" and kwargs.get("dir_fd") is not None:
                original.rename(displaced)
                original.write_bytes(b"hostile replacement")
            return original_action(path, *args, **kwargs)

        monkeypatch.setattr(os, "unlink", hostile_action)
    else:
        original_action = os.rmdir
        displaced = contract.root / "displaced-directory"

        def hostile_action(path, *args, **kwargs):
            if path == "nested" and kwargs.get("dir_fd") is not None:
                nested.rename(displaced)
                nested.mkdir()
            return original_action(path, *args, **kwargs)

        monkeypatch.setattr(os, "rmdir", hostile_action)

    result, = execute_ledger((row,))
    assert result.outcome == "failed"
    assert result.reclaimed_bytes == result.reclaimed_inodes == 0
    assert displaced.exists()
    # The same-name replacement can be removed by the final pathname syscall;
    # its survival is intentionally not promised by the portable contract.
    assert not (nested / "file" if entry_kind == "file" else nested).exists()


def test_concurrent_finalizers_yield_one_completion_and_one_failure(tmp_path):
    workspace, _, evidence, old = _candidate(tmp_path)
    row = seal_ledger_row(workspace=workspace, task_id="TASK-1", assertions=evidence,
                          now_ns=old + 121_000_000_000)
    barrier = threading.Barrier(2)
    results = []

    def run():
        barrier.wait()
        results.extend(execute_ledger((row,)))

    threads = [threading.Thread(target=run) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(result.outcome for result in results) == ["completed", "failed"]


def test_per_row_failure_isolated_and_protected_sibling_addition_detected(tmp_path):
    workspace, first, evidence, old = _candidate(tmp_path)
    second = prepare_task_scratch(workspace=workspace, task_id="TASK-2",
                                  producer_kind="agent", producer_id="sess-2")
    os.utime(second.root, ns=(old, old))
    first_row = seal_ledger_row(workspace=workspace, task_id="TASK-1", assertions=evidence,
                                now_ns=old + 121_000_000_000)
    (first.root.parent / "hostile-sibling").mkdir()
    second_row = seal_ledger_row(workspace=workspace, task_id="TASK-2", assertions=evidence,
                                 now_ns=old + 121_000_000_000)
    first_result, second_result = execute_ledger((first_row, second_row))
    assert first_result.outcome == "failed"
    assert second_result.outcome == "completed"


def test_partial_syscall_failure_requires_fresh_ledger_retry(monkeypatch, tmp_path):
    workspace, contract, evidence, old = _candidate(tmp_path)
    (contract.root / "nested/a").write_text("first")
    (contract.root / "nested/z").write_text("last")
    for path in (contract.root / "nested/a", contract.root / "nested/z",
                 contract.root / "nested", contract.root):
        os.utime(path, ns=(old, old))
    row = seal_ledger_row(workspace=workspace, task_id="TASK-1", assertions=evidence,
                          now_ns=old + 121_000_000_000)
    original_unlink = os.unlink

    def fail_last(path, *args, **kwargs):
        if path == "z":
            raise OSError("injected partial syscall")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(os, "unlink", fail_last)
    failed, = execute_ledger((row,))
    assert failed.outcome == "failed"
    assert failed.reclaimed_bytes == failed.reclaimed_inodes == 0
    replay, = execute_ledger((row,))
    assert replay.outcome == "failed"
    monkeypatch.setattr(os, "unlink", original_unlink)
    for path in (contract.root / "nested/file", contract.root / "nested/z",
                 contract.root / "nested", contract.root):
        if path.exists():
            os.utime(path, ns=(old, old))
    fresh = seal_ledger_row(workspace=workspace, task_id="TASK-1", assertions=evidence,
                            now_ns=old + 121_000_000_000)
    retried, = execute_ledger((fresh,))
    assert retried.outcome == "completed"


def test_replayed_row_is_rejected(tmp_path):
    workspace, _, evidence, old = _candidate(tmp_path)
    row = seal_ledger_row(workspace=workspace, task_id="TASK-1", assertions=evidence,
                          now_ns=old + 121_000_000_000)
    first, replay = execute_ledger((row, row))
    assert first.outcome == "completed"
    assert replay.reason == "replayed ledger"


def test_forged_or_cross_root_ledger_is_rejected(tmp_path):
    workspace, _, evidence, old = _candidate(tmp_path)
    row = seal_ledger_row(workspace=workspace, task_id="TASK-1", assertions=evidence,
                          now_ns=old + 121_000_000_000)
    forged = replace(row, literal_root=str(tmp_path))
    result, = execute_ledger((forged,))
    assert result.outcome == "failed"
    assert tmp_path.is_dir()


def test_module_has_no_production_importers():
    root = Path(__file__).parents[1]
    assert [path for path in (root / "runtime").rglob("*.py")
            if path.name != "task_scratch_reclamation.py"
            and "task_scratch_reclamation" in path.read_text()] == []
