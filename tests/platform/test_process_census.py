"""Tests for the portable identity-safe descendant census + sampler
(THR-207 Slice B measurement surface).

Hermetic: real subprocesses are spawned only as disposable process trees
(never the live daemon), start identities are read from the OS, and every
spawned tree is torn down in a fixture. On hosts without /proc (non-Linux,
non-macOS) the platform reader tests skip with an explicit reason.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time

import pytest

from runtime.platform.process_census import (
    DescendantSampler,
    LinuxProcReader,
    MacProcReader,
    ProcessTreeCensus,
    _parse_proc_stat,
    default_process_reader,
    merge_sample_peaks,
    sample_gaps,
)
from runtime.platform.session_backend import (
    MeasurementProvenance,
    ResourceSample,
    RunningHandle,
)
def _spawn_tree(sleep: float = 30) -> subprocess.Popen:
    """Spawn a process tree: root -> child -> grandchild.

    Returns the root Popen. Every level sleeps for *sleep* seconds, so the
    tree is stable while the census reads it. The root is placed in its own
    session so teardown can kill the whole group.
    """
    grandchild = "import time; time.sleep({sleep})".format(sleep=sleep)
    child = (
        "import subprocess, sys, time; "
        f"subprocess.Popen([sys.executable, '-c', {grandchild!r}]); "
        "time.sleep({sleep})".format(sleep=sleep)
    )
    code = (
        "import subprocess, sys, time; "
        f"subprocess.Popen([sys.executable, '-c', {child!r}]); "
        "time.sleep({sleep})".format(sleep=sleep)
    )
    return subprocess.Popen(
        [sys.executable, "-c", code],
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _kill_tree(root: subprocess.Popen) -> None:
    """Tear down the spawned tree: SIGKILL the session group, then reap."""
    try:
        os.killpg(root.pid, 9)
    except (ProcessLookupError, PermissionError):
        pass
    try:
        root.wait(timeout=5)
    except subprocess.TimeoutExpired:
        root.kill()
        root.wait(timeout=5)


@pytest.fixture
def tree():
    proc = _spawn_tree()
    try:
        yield proc
    finally:
        _kill_tree(proc)


def _reader() -> LinuxProcReader:
    return LinuxProcReader()


def _running(root_pid: int, identity: str | None) -> RunningHandle:
    return RunningHandle(
        backend="test",
        token="tok",
        request_id="req",
        root_pid=root_pid,
        start_identity=identity or "",
        process=None,
    )


# ── /proc stat parsing ───────────────────────────────────────────────


def test_parse_proc_stat_handles_spaces_and_parens_in_comm():
    line = "12345 (some comm with spaces) S 1 12345 12345 0 -1 4194560 0 0 0 0 0 0 0 20 0 1 0 123 456 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0"
    parsed = _parse_proc_stat(line)
    assert parsed is not None
    assert parsed["pid"] == 12345
    assert parsed["ppid"] == 1
    assert parsed["pgid"] == 12345
    assert parsed["start_identity"] == "456"  # field 22 -> rest[19]
    # rest[11]=utime(14) rest[12]=stime(15) — count: state(0) ppid(1) pgrp(2)
    # session(3) tty_nr(4) tpgid(5) flags(6) minflt(7) cminflt(8) majflt(9)
    # cmajflt(10) utime(11) stime(12)
    assert parsed["utime"] == 0
    assert parsed["stime"] == 0


def test_parse_proc_stat_rejects_garbage():
    assert _parse_proc_stat("not a stat line") is None
    assert _parse_proc_stat("1234 (comm) S 1") is None  # truncated


# ── real census on /proc ─────────────────────────────────────────────


@pytest.mark.skipif(
    not hasattr(os, "scandir") or not os.path.isdir("/proc"),
    reason="census requires a /proc filesystem (Linux)",
)
def test_census_enumerates_descendant_tree_real(tree):
    time.sleep(0.6)  # let the python tree finish spawning its descendants
    root = tree.pid
    census = ProcessTreeCensus(_reader())
    members = census.descendants(root, census.start_identity(root), include_root=True)
    assert root in {o.pid for o in members}
    # The tree spawns one child which spawns one grandchild.
    assert len(members) >= 3
    assert all(o.start_identity for o in members)


@pytest.mark.skipif(
    not hasattr(os, "scandir") or not os.path.isdir("/proc"),
    reason="census requires a /proc filesystem (Linux)",
)
def test_census_excludes_siblings(tree):
    time.sleep(0.6)  # let the python tree finish spawning its descendants
    root = tree.pid
    census = ProcessTreeCensus(_reader())
    members = census.descendants(root, census.start_identity(root))
    # Spawn an unrelated process; it must never appear as a descendant.
    outsider = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        time.sleep(0.3)
        members2 = census.descendants(root, census.start_identity(root))
        assert outsider.pid not in {o.pid for o in members2}
        assert {o.pid for o in members2} == {o.pid for o in members}
    finally:
        outsider.terminate()
        outsider.wait(timeout=5)


@pytest.mark.skipif(
    not hasattr(os, "scandir") or not os.path.isdir("/proc"),
    reason="census requires a /proc filesystem (Linux)",
)
def test_census_root_identity_mismatch_yields_empty(tree):
    """A reused root PID with a different start identity is never attributed.

    Simulated deterministically: ask for a root identity that cannot match
    the live process's starttime."""
    root = tree.pid
    census = ProcessTreeCensus(_reader())
    members = census.descendants(root, "definitely-not-the-starttime")
    assert members == ()


@pytest.mark.skipif(
    not hasattr(os, "scandir") or not os.path.isdir("/proc"),
    reason="census requires a /proc filesystem (Linux)",
)
def test_census_missing_root_yields_empty(tree):
    census = ProcessTreeCensus(_reader())
    assert census.descendants(999_999_999, None) == ()


@pytest.mark.skipif(
    not hasattr(os, "scandir") or not os.path.isdir("/proc"),
    reason="census requires a /proc filesystem (Linux)",
)
def test_sampler_returns_non_negative_metrics_real(tree):
    time.sleep(0.6)  # let the python tree finish spawning its descendants
    root = tree.pid
    sampler = DescendantSampler(census=ProcessTreeCensus(_reader()))
    sample = sampler.sample(_running(root, sampler._ensure_census().start_identity(root)))
    assert sample.provenance is MeasurementProvenance.SAMPLED
    assert sample.process_count is not None and sample.process_count >= 3
    assert sample.memory_peak_bytes is not None and sample.memory_peak_bytes >= 0
    assert sample.cpu_total_seconds is not None and sample.cpu_total_seconds >= 0


# ── sampler failure semantics ────────────────────────────────────────


def test_sampler_unavailable_provenance_when_root_unreadable():
    class _EmptyReader:
        def read_all(self):
            return []

        def start_identity(self, pid):
            return None

    sampler = DescendantSampler(census=ProcessTreeCensus(_EmptyReader()))
    sample = sampler.sample(_running(1234, "ident"))
    # An empty tree is not a fabricated zero — values stay None.
    assert sample.provenance is MeasurementProvenance.SAMPLED
    assert sample.memory_peak_bytes is None
    assert sample.cpu_total_seconds is None
    assert sample.process_count is None


def test_sampler_never_claims_unavailable_as_zero():
    class _BrokenReader:
        def read_all(self):
            raise OSError("cannot enumerate processes")

        def start_identity(self, pid):
            raise OSError("cannot read identity")

    sampler = DescendantSampler(census=ProcessTreeCensus(_BrokenReader()))
    with pytest.raises(OSError):
        # The supervisor's sampler records a measurement failure (fail-closed)
        # rather than a fabricated zero footprint.
        sampler.sample(_running(1234, "ident"))


# ── gaps and peak merging ────────────────────────────────────────────


def test_sample_gaps_records_intervals():
    samples = [
        ResourceSample(sampled_at=10.0, process_count=1),
        ResourceSample(sampled_at=11.5, process_count=2),
        ResourceSample(sampled_at=14.0, process_count=3),
    ]
    gaps = sample_gaps(samples)
    assert gaps == (1.5, 2.5)


def test_sample_gaps_single_sample_is_empty():
    assert sample_gaps([ResourceSample(sampled_at=1.0)]) == ()


def test_merge_sample_peaks():
    samples = [
        ResourceSample(sampled_at=1.0, memory_peak_bytes=100, cpu_total_seconds=1.0, process_count=2),
        ResourceSample(sampled_at=2.0, memory_peak_bytes=300, cpu_total_seconds=3.0, process_count=5),
        ResourceSample(sampled_at=3.0, memory_peak_bytes=200, cpu_total_seconds=4.0, process_count=4),
    ]
    memory, cpu, process = merge_sample_peaks(samples)
    assert memory == 300
    assert cpu == 4.0  # cumulative — last sample wins
    assert process == 5


def test_merge_sample_peaks_none_when_no_values():
    assert merge_sample_peaks([]) == (None, None, None)


# ── reader platform gates ────────────────────────────────────────────


def test_mac_reader_unavailable_on_non_macos():
    if sys.platform == "darwin":
        pytest.skip("this host IS macOS — libproc is expected to load")
    with pytest.raises(RuntimeError):
        MacProcReader()


def test_default_reader_matches_platform():
    if sys.platform == "linux":
        assert isinstance(default_process_reader(), LinuxProcReader)
    elif sys.platform == "darwin":
        assert isinstance(default_process_reader(), MacProcReader)
    else:
        with pytest.raises(RuntimeError):
            default_process_reader()
