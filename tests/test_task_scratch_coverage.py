import json
import os
from pathlib import Path
from unittest.mock import patch

import runtime.daemon.task_scratch_coverage as coverage
from runtime.daemon.task_scratch_coverage import CoverageBucket, collect_task_scratch_coverage


def _boot(proc: Path) -> None:
    target = proc / "sys/kernel/random"; target.mkdir(parents=True)
    (target / "boot_id").write_text("01234567-0123-8123-8123-0123456789ab")


def _manifest(workspace: Path, task: str, *, producers: list[dict] | None = None) -> None:
    root = workspace / ".happyranch/task-tmp" / task
    parent = workspace / ".happyranch/task-scratch-manifests"; parent.mkdir(parents=True, exist_ok=True)
    producer = {"producer_kind": "agent", "producer_id": "sess-1", "required": {"canonical_root": str(root), "ownership": "runtime"}, "observed": {"canonical_root": str(root), "mode": "0700"}, "classification": "regenerable_scratch", "observed_at": "2026-01-01T00:00:00+00:00"}
    data = {"version": 1, "task_id": task, "required_root": str(root), "observed_root": str(root), "root_classification": "regenerable_scratch", "manifest_classification": "durable_recovery_artifact", "lock_classification": "durable_recovery_artifact", "producers": [producer] if producers is None else producers}
    (parent / f"{task}.json").write_text(json.dumps(data))


def test_collector_reads_real_temp_tree_and_accounts_residuals(tmp_path: Path) -> None:
    proc = tmp_path / "proc"; _boot(proc)
    workspace = tmp_path / "workspace"; root = workspace / ".happyranch/task-tmp/TASK-1"; root.mkdir(parents=True)
    (root / "payload").write_bytes(b"payload"); (workspace / "sidecar").write_bytes(b"sidecar")
    _manifest(workspace, "TASK-1")
    observation = collect_task_scratch_coverage(workspace=workspace, proc_root=proc)
    assert observation.complete
    assert any(row.relative_path == "sidecar" for row in observation.buckets)
    assert any(row.relative_path.endswith("TASK-1") and row.classification == "canonical_regenerable" for row in observation.buckets)
    assert not observation.coverage_ready


def test_empty_producers_are_not_canonical_source_authority(tmp_path: Path) -> None:
    proc = tmp_path / "proc"; _boot(proc)
    workspace = tmp_path / "workspace"; (workspace / ".happyranch/task-tmp/TASK-1").mkdir(parents=True)
    _manifest(workspace, "TASK-1", producers=[])
    observation = collect_task_scratch_coverage(workspace=workspace, proc_root=proc)
    bucket = next(row for row in observation.buckets if row.relative_path.endswith("TASK-1"))
    assert bucket.classification == "residual_unmanifested"
    assert not observation.coverage_ready


def test_missing_boot_is_unavailable_not_passing_zero(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"; workspace.mkdir()
    observation = collect_task_scratch_coverage(workspace=workspace, proc_root=tmp_path / "missing-proc")
    assert not observation.complete and not observation.coverage_ready
    assert "boot_id_unavailable" in observation.reasons


def test_malformed_boot_and_expired_admission_fail_closed(tmp_path: Path) -> None:
    proc = tmp_path / "proc"; target = proc / "sys/kernel/random"; target.mkdir(parents=True)
    (target / "boot_id").write_text("not-a-boot-id")
    workspace = tmp_path / "workspace"; workspace.mkdir()
    malformed = collect_task_scratch_coverage(workspace=workspace, proc_root=proc)
    expired = collect_task_scratch_coverage(workspace=workspace, proc_root=proc, deadline_ns=0)
    assert "boot_id_unavailable" in malformed.reasons and not malformed.coverage_ready
    assert "observation_timeout" in expired.reasons and not expired.coverage_ready


def test_expired_observer_admits_no_scandir_start(tmp_path: Path) -> None:
    proc = tmp_path / "proc"; _boot(proc)
    workspace = tmp_path / "workspace"; (workspace / ".happyranch/task-tmp/TASK-1").mkdir(parents=True)
    calls: list[str] = []; real = coverage.os.scandir
    def watch(path: object, *args: object, **kwargs: object) -> object:
        calls.append(str(path)); return real(path, *args, **kwargs)
    with patch.object(coverage.os, "scandir", watch):
        observation = collect_task_scratch_coverage(workspace=workspace, proc_root=proc, deadline_ns=0)
    assert not calls
    assert "observation_timeout" in observation.reasons


def test_second_pass_detects_nested_metadata_and_manifest_change(tmp_path: Path) -> None:
    proc = tmp_path / "proc"; _boot(proc)
    workspace = tmp_path / "workspace"; root = workspace / ".happyranch/task-tmp/TASK-1"; root.mkdir(parents=True)
    payload = root / "payload"; payload.write_bytes(b"before"); _manifest(workspace, "TASK-1")
    calls = [0]; real = coverage.os.open
    def mutate(path: object, *args: object, **kwargs: object) -> int:
        if str(path).endswith("boot_id"):
            calls[0] += 1
            if calls[0] == 2:
                payload.write_bytes(b"after" * 100)
                _manifest(workspace, "TASK-1", producers=[{"producer_kind": "agent", "producer_id": "sess-2", "required": {"canonical_root": str(root), "ownership": "runtime"}, "observed": {"canonical_root": str(root), "mode": "0700"}, "classification": "regenerable_scratch", "observed_at": "2026-01-02T00:00:00+00:00"}])
        return real(path, *args, **kwargs)
    with patch.object(coverage.os, "open", mutate):
        observation = collect_task_scratch_coverage(workspace=workspace, proc_root=proc)
    assert not observation.complete and not observation.coverage_ready
    assert "metadata_changed_during_collection" in observation.reasons
    assert "classification_changed_during_collection" in observation.reasons


def test_repository_and_special_candidate_are_never_canonical(tmp_path: Path) -> None:
    proc = tmp_path / "proc"; _boot(proc)
    workspace = tmp_path / "workspace"; root = workspace / ".happyranch/task-tmp/TASK-1"; root.mkdir(parents=True)
    (root / ".git").mkdir(); os.mkfifo(root / "fifo"); _manifest(workspace, "TASK-1")
    observation = collect_task_scratch_coverage(workspace=workspace, proc_root=proc)
    bucket = next(row for row in observation.buckets if row.relative_path.endswith("TASK-1"))
    assert bucket.classification == "residual_repository_or_special"
    assert not observation.coverage_ready


def test_symlinked_happyranch_ancestor_is_counted_but_not_traversed(tmp_path: Path) -> None:
    proc = tmp_path / "proc"; _boot(proc)
    workspace = tmp_path / "workspace"; workspace.mkdir(); outside = tmp_path / "outside"; outside.mkdir()
    (workspace / ".happyranch").symlink_to(outside, target_is_directory=True)
    observation = collect_task_scratch_coverage(workspace=workspace, proc_root=proc)
    assert "noncanonical_ancestor" in observation.reasons
    assert all("task-tmp" not in row.relative_path for row in observation.buckets)
    assert not observation.coverage_ready


def test_dominant_integer_boundaries_ties_and_zero_are_deterministic() -> None:
    rows = [CoverageBucket("a", "canonical_regenerable", 80, 1, 1), CoverageBucket("b", "canonical_regenerable", 10, 80, 80), CoverageBucket("c", "residual_unknown", 10, 19, 19)]
    assert coverage._dominant(rows) == {"a", "b", "c"}
    assert coverage._dominant([CoverageBucket("z", "canonical_regenerable", 0, 0, 0)]) == set()


def test_empty_zero_and_shared_manifest_byte_cap_fail_closed(tmp_path: Path, monkeypatch: object) -> None:
    proc = tmp_path / "proc"; _boot(proc); workspace = tmp_path / "workspace"; workspace.mkdir()
    empty = collect_task_scratch_coverage(workspace=workspace, proc_root=proc)
    assert "zero_or_empty_observation" in empty.reasons and not empty.coverage_ready
    monkeypatch.setattr(coverage, "MAX_TOTAL_MANIFEST_BYTES", 1)
    root = workspace / ".happyranch/task-tmp/TASK-1"; root.mkdir(parents=True); _manifest(workspace, "TASK-1")
    capped = collect_task_scratch_coverage(workspace=workspace, proc_root=proc)
    assert "manifest_byte_cap" in capped.reasons and not capped.coverage_ready


def test_optional_parent_states_do_not_drop_ordinary_workspace_partition(tmp_path: Path) -> None:
    proc = tmp_path / "proc"; _boot(proc)
    workspace = tmp_path / "workspace"; workspace.mkdir(); (workspace / "ordinary").write_text("x")
    absent = collect_task_scratch_coverage(workspace=workspace, proc_root=proc)
    assert any(row.relative_path == "ordinary" for row in absent.buckets)
    owned = workspace / ".happyranch"; owned.mkdir(); (owned / "meta").write_text("x")
    (owned / "task-tmp").symlink_to(tmp_path / "outside", target_is_directory=True)
    malformed = collect_task_scratch_coverage(workspace=workspace, proc_root=proc)
    assert {"ordinary", ".happyranch/meta", ".happyranch/task-tmp"} <= {row.relative_path for row in malformed.buckets}
    assert "noncanonical_ancestor" in malformed.reasons


def test_absent_optional_parents_are_not_invented_as_residuals(tmp_path: Path) -> None:
    proc = tmp_path / "proc"; _boot(proc)
    workspace = tmp_path / "workspace"; workspace.mkdir(); (workspace / "ordinary").write_text("x")
    absent_owned = collect_task_scratch_coverage(workspace=workspace, proc_root=proc)
    assert "noncanonical_ancestor" not in absent_owned.reasons
    (workspace / ".happyranch").mkdir()
    absent_tmp = collect_task_scratch_coverage(workspace=workspace, proc_root=proc)
    assert "noncanonical_ancestor" not in absent_tmp.reasons
    assert {row.relative_path for row in absent_tmp.buckets} >= {".", "ordinary", ".happyranch"}


def test_symlink_candidate_and_manifest_parent_never_read_referent(tmp_path: Path) -> None:
    proc = tmp_path / "proc"; _boot(proc)
    workspace = tmp_path / "workspace"; outside = tmp_path / "outside"; outside.mkdir(parents=True)
    (outside / "secret").write_text("outside")
    task_parent = workspace / ".happyranch/task-tmp"; task_parent.mkdir(parents=True)
    (task_parent / "TASK-1").symlink_to(outside, target_is_directory=True)
    manifests = workspace / ".happyranch/task-scratch-manifests"; manifests.symlink_to(outside, target_is_directory=True)
    calls: list[str] = []; real = coverage.os.scandir
    def watch(path: object, *args: object, **kwargs: object) -> object:
        calls.append(str(path)); return real(path, *args, **kwargs)
    with patch.object(coverage.os, "scandir", watch):
        observation = collect_task_scratch_coverage(workspace=workspace, proc_root=proc)
    bucket = next(row for row in observation.buckets if row.relative_path.endswith("TASK-1"))
    assert bucket.classification == "residual_noncanonical"
    assert str(outside) not in calls


def test_manifest_parent_guard_is_reachable_for_literal_candidate(tmp_path: Path) -> None:
    proc = tmp_path / "proc"; _boot(proc)
    workspace = tmp_path / "workspace"; root = workspace / ".happyranch/task-tmp/TASK-1"; root.mkdir(parents=True)
    outside = tmp_path / "outside"; outside.mkdir(); _manifest(outside, "TASK-1")
    manifests = workspace / ".happyranch/task-scratch-manifests"
    manifests.symlink_to(outside, target_is_directory=True)
    opened: list[str] = []; paths_by_fd: dict[int, str] = {}; read_fds: list[int] = []; stated: list[tuple[str, bool]] = []; real_open = coverage.os.open; real_fdopen = coverage.os.fdopen; real_stat = Path.stat
    def watch(path: object, *args: object, **kwargs: object) -> int:
        fd = real_open(path, *args, **kwargs); opened.append(str(path)); paths_by_fd[fd] = str(path); return fd
    def watch_fdopen(fd: int, *args: object, **kwargs: object) -> object:
        handle = real_fdopen(fd, *args, **kwargs)
        original_read = handle.read
        def read(*read_args: object, **read_kwargs: object) -> bytes:
            read_fds.append(fd); return original_read(*read_args, **read_kwargs)
        handle.read = read  # type: ignore[method-assign]
        return handle
    def watch_stat(self: Path, *args: object, **kwargs: object) -> os.stat_result:
        stated.append((str(self), bool(kwargs.get("follow_symlinks", True))))
        return real_stat(self, *args, **kwargs)
    with patch.object(coverage.os, "open", watch), patch.object(coverage.os, "fdopen", watch_fdopen), patch.object(Path, "stat", watch_stat):
        observation = collect_task_scratch_coverage(workspace=workspace, proc_root=proc)
    bucket = next(row for row in observation.buckets if row.relative_path.endswith("TASK-1"))
    assert bucket.classification == "residual_unmanifested"
    lexical = manifests / "TASK-1.json"
    assert (str(manifests), False) in stated
    assert str(lexical) not in opened
    assert str(outside / "TASK-1.json") not in opened
    assert str(outside / "TASK-1.json") not in {paths_by_fd[fd] for fd in read_fds}

    # A literal parent is the permitted control: it opens and consumes the
    # manifest descriptor, unlike the lexical symlink parent above.
    workspace2 = tmp_path / "workspace2"; root2 = workspace2 / ".happyranch/task-tmp/TASK-1"; root2.mkdir(parents=True); _manifest(workspace2, "TASK-1")
    opened.clear(); paths_by_fd.clear(); read_fds.clear()
    with patch.object(coverage.os, "open", watch), patch.object(coverage.os, "fdopen", watch_fdopen):
        allowed = collect_task_scratch_coverage(workspace=workspace2, proc_root=proc)
    assert any(path.endswith("task-scratch-manifests/TASK-1.json") for path in opened)
    assert read_fds
    assert any(row.relative_path.endswith("TASK-1") and row.classification == "canonical_regenerable" for row in allowed.buckets)


def test_boot_post_open_timeout_closes_descriptor_in_each_pass(tmp_path: Path, monkeypatch: object) -> None:
    """The second admission protects the buffered boot read, not just open()."""
    for expire_on_boot_open in (1, 2):
        proc = tmp_path / f"proc-{expire_on_boot_open}"; _boot(proc)
        workspace = tmp_path / f"workspace-{expire_on_boot_open}"; workspace.mkdir()
        opens: list[int] = []; pending_boot_fdopens: dict[int, int] = {}; closes: list[int] = []; buffered_reads: list[int] = []; expired = False; real_open = coverage.os.open; real_close = coverage.os.close; real_fdopen = coverage.os.fdopen
        def watch_open(path: object, *args: object, **kwargs: object) -> int:
            nonlocal expired
            fd = real_open(path, *args, **kwargs)
            if str(path).endswith("boot_id"):
                opens.append(fd)
                pending_boot_fdopens[fd] = len(opens)
                expired = len(opens) == expire_on_boot_open
            return fd
        def watch_close(fd: int) -> None:
            closes.append(fd); pending_boot_fdopens.pop(fd, None); real_close(fd)
        def watch_fdopen(fd: int, *args: object, **kwargs: object) -> object:
            handle = real_fdopen(fd, *args, **kwargs)
            original_read = handle.read
            boot_index = pending_boot_fdopens.pop(fd, None)
            def read(*read_args: object, **read_kwargs: object) -> bytes:
                if boot_index is not None:
                    buffered_reads.append(boot_index)
                return original_read(*read_args, **read_kwargs)
            handle.read = read  # type: ignore[method-assign]
            return handle
        def clock() -> int:
            return 2 if expired else 0
        monkeypatch.setattr(coverage.time, "monotonic_ns", clock)
        with patch.object(coverage.os, "open", watch_open), patch.object(coverage.os, "close", watch_close), patch.object(coverage.os, "fdopen", watch_fdopen):
            observation = collect_task_scratch_coverage(workspace=workspace, proc_root=proc, deadline_ns=1)
        assert len(opens) == expire_on_boot_open
        denied_fd = opens[-1]
        assert denied_fd in closes
        assert expire_on_boot_open not in buffered_reads
        if expire_on_boot_open == 2:
            assert 1 in buffered_reads
        assert "observation_timeout" in observation.reasons
        monkeypatch.undo()


def test_boot_post_open_read_cap_closes_descriptor_in_each_pass(tmp_path: Path, monkeypatch: object) -> None:
    # First measure the real shared budget, then reduce the numerical cap so
    # pass one completes and pass two admits boot open but not its dependent read.
    proc = tmp_path / "proc"; _boot(proc); workspace = tmp_path / "workspace"; root = workspace / ".happyranch/task-tmp/TASK-1"; root.mkdir(parents=True); _manifest(workspace, "TASK-1")
    counts: list[int] = []; real_admit = coverage._Budget.admit
    def count_admit(self: coverage._Budget, *, read: bool = False, manifest_bytes: int = 0) -> bool:
        result = real_admit(self, read=read, manifest_bytes=manifest_bytes)
        if read and result:
            counts.append(self.reads)
        return result
    monkeypatch.setattr(coverage._Budget, "admit", count_admit)
    assert collect_task_scratch_coverage(workspace=workspace, proc_root=proc).complete
    first_pass_reads = len(counts) // 2
    monkeypatch.undo()

    monkeypatch.setattr(coverage, "MAX_READS", first_pass_reads + 1)
    opened: list[int] = []; pending_boot_fdopens: list[int] = []; closed: list[int] = []; buffered_boot_reads: list[int] = []; real_open = coverage.os.open; real_close = coverage.os.close; real_fdopen = coverage.os.fdopen
    def watch_open(path: object, *args: object, **kwargs: object) -> int:
        fd = real_open(path, *args, **kwargs)
        if str(path).endswith("boot_id"):
            opened.append(fd); pending_boot_fdopens.append(fd)
        return fd
    def watch_close(fd: int) -> None:
        closed.append(fd); real_close(fd)
    def watch_fdopen(fd: int, *args: object, **kwargs: object) -> object:
        handle = real_fdopen(fd, *args, **kwargs)
        original_read = handle.read
        boot_index = len(opened) if fd in pending_boot_fdopens else None
        if fd in pending_boot_fdopens:
            pending_boot_fdopens.remove(fd)
        def read(*read_args: object, **read_kwargs: object) -> bytes:
            if boot_index is not None:
                buffered_boot_reads.append(boot_index)
            return original_read(*read_args, **read_kwargs)
        handle.read = read  # type: ignore[method-assign]
        return handle
    with patch.object(coverage.os, "open", watch_open), patch.object(coverage.os, "close", watch_close), patch.object(coverage.os, "fdopen", watch_fdopen):
        observation = collect_task_scratch_coverage(workspace=workspace, proc_root=proc)
    assert len(opened) == 2
    denied_fd = opened[-1]
    assert denied_fd in closed
    assert 2 not in buffered_boot_reads
    assert 1 in buffered_boot_reads
    assert "read_cap" in observation.reasons and not observation.complete and not observation.coverage_ready


def test_candidate_timeout_cap_and_nested_incompleteness_remain_unknown(tmp_path: Path, monkeypatch: object) -> None:
    """A partial candidate walk is not evidence of a repository/special file."""
    for failure in ("timeout", "cap"):
        proc = tmp_path / f"proc-{failure}"; _boot(proc)
        workspace = tmp_path / f"workspace-{failure}"; root = workspace / ".happyranch/task-tmp/TASK-1"; (root / "nested").mkdir(parents=True)
        (root / "nested/payload").write_text("x"); _manifest(workspace, "TASK-1")
        candidate_scans = 0; nested_scans = 0; expired = False; real_scandir = coverage.os.scandir; real_admit = coverage._Budget.admit
        def clock() -> int:
            return 2 if expired else 0
        def watch_scandir(path: object, *args: object, **kwargs: object) -> object:
            nonlocal candidate_scans, nested_scans, expired
            if Path(path) == root:
                candidate_scans += 1
                # Each pass walks and populations the literal root before its
                # candidate inspection. Trigger only at the second pass so the
                # returned buckets remain the canonical first snapshot.
                if candidate_scans == 6:
                    expired = failure == "timeout"
            if Path(path) == root / "nested":
                nested_scans += 1
            return real_scandir(path, *args, **kwargs)
        def admit(self: coverage._Budget, *, read: bool = False, manifest_bytes: int = 0) -> bool:
            if failure == "cap" and candidate_scans == 6 and read:
                # Exercise the production counter/cap comparison, rather than
                # replacing admission with a synthetic denial.
                monkeypatch.setattr(coverage, "MAX_READS", self.reads)
            return real_admit(self, read=read, manifest_bytes=manifest_bytes)
        monkeypatch.setattr(coverage.time, "monotonic_ns", clock)
        monkeypatch.setattr(coverage._Budget, "admit", admit)
        with patch.object(coverage.os, "scandir", watch_scandir):
            observation = collect_task_scratch_coverage(workspace=workspace, proc_root=proc, deadline_ns=1)
        bucket = next(row for row in observation.buckets if row.relative_path.endswith("TASK-1"))
        assert candidate_scans >= 6 and nested_scans >= 1
        # The second-pass interruption does not rewrite the already returned
        # first-snapshot bucket; incomplete/nonready is conveyed by reasons.
        assert bucket.classification == "canonical_regenerable"
        assert not observation.complete and not observation.coverage_ready
        assert ("observation_timeout" if failure == "timeout" else "read_cap") in observation.reasons
        monkeypatch.undo()
