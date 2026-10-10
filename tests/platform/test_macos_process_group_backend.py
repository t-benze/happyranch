"""Tests for the honestly capped macOS process-group/census backend
(THR-207 Slice B).

Layers:

* **Deterministic unit tests** — fake census/process-table interactions:
  probe degradation, ownership refusal (PID-reuse safety), TERM/KILL
  escalation, survivor accounting, sampled-provenance receipts, abandon.
* **Real POSIX integration tests** (``-m integration``, gated on the real
  probe) — process-group launch/cleanup, the mandatory success-path
  descendant cleanup, and the documented best-effort limitation (an escaped
  descendant that calls ``setsid`` is censused as a survivor, never
  falsely claimed clean). These run on any POSIX host with an identity-safe
  census reader; libproc-specific assertions skip with an explicit reason on
  non-macOS hosts.

Hermetic: every spawned tree is killed in the test; no live daemon is
touched.
"""
from __future__ import annotations

import os
import signal
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from runtime.platform.macos_process_group import MacOSProcessGroupBackend
from runtime.platform.process_census import LinuxProcReader, ProcessTreeCensus
from runtime.platform.session_backend import (
    BackendLaunchError,
    CleanupStatus,
    LaunchSpec,
    MeasurementProvenance,
    ResourceSample,
    RunningHandle,
    SessionBackend,
)


def _policy():
    from runtime.orchestrator.host_supervisor import canary_policy

    return canary_policy()


def _request():
    from runtime.orchestrator.host_supervisor import AdmissionRequest

    return AdmissionRequest(
        org="test", invocation_kind="schedule", logical_id="sched-1",
        executor_profile="claude",
    )


def _running(pid: int, identity: str = "boot-1") -> RunningHandle:
    return RunningHandle(
        backend="macos-process-group",
        token="tok-1",
        request_id="req-1",
        root_pid=pid,
        start_identity=identity,
        process=None,
    )


class _CooperativeEscapedChild:
    """Test-only finite lifetime for a child that escapes the tested group.

    Removing the private control marker is an explicit cooperative request to
    this exact child, passed via ordinary argv rather than an unsupported
    LaunchSpec extension.  It is not an OS ownership claim about a PID or
    process group.  The child also expires on its own.
    """

    _CHILD = (
        "import os, pathlib, sys, time; "
        "control, report, deadline = sys.argv[1], sys.argv[2], float(sys.argv[3]); "
        "os.setsid(); pathlib.Path(report).write_text('R:'+str(os.getpid())); "
        "[(time.sleep(.02)) for _ in range(int(deadline * 50)) if pathlib.Path(control).exists()]; "
        "pathlib.Path(report).write_text(pathlib.Path(report).read_text()+'E')"
    )

    def __init__(self, deadline_seconds: float = 5.0) -> None:
        # Keep this tiny owned control directory under the test worktree: the
        # shared task tmp cleaner may otherwise delete it between launch and
        # the child's acknowledgement.
        self._root: Path | None = None
        self._control: Path | None = None
        self._report: Path | None = None
        self._deadline_seconds = deadline_seconds
        self._released = False
        self.release_error: OSError | None = None
        self.cleanup_errors: list[BaseException] = []
        try:
            self._root = Path(tempfile.mkdtemp(prefix=".pytest-owned-escaped-", dir=Path.cwd()))
            self._control = self._root / "release"
            self._report = self._root / "events"
            self._control.touch()
        except BaseException as primary:
            # A failed second construction step still owns the first resource.
            self.close()
            for error in self.cleanup_errors:
                primary.add_note(f"escaped-child construction cleanup: {error}")
            if self.release_error is not None:
                primary.add_note(f"escaped-child construction release: {self.release_error}")
            raise

    @property
    def argv(self) -> tuple[str, ...]:
        return (
            sys.executable,
            "-c",
            self._CHILD,
            str(self._control),
            str(self._report),
            str(self._deadline_seconds),
        )

    def wait_for(self, signal_byte: bytes, timeout: float = 2.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                if self._report is not None and signal_byte.decode() in self._report.read_text():
                    return True
            except FileNotFoundError:
                # The child has not yet created its report, unless cleanup
                # already removed the owned directory.
                if self._root is None or not self._root.exists():
                    self.cleanup_errors.append(AssertionError("report directory vanished"))
                    return False
            except OSError as exc:
                self.cleanup_errors.append(exc)
                return False
            time.sleep(.02)
        return False

    def observe_terminal(self, timeout: float = 2.0) -> bool:
        """Read-only bounded absence observation for this test-owned child.

        The E marker is written before interpreter exit, so it is only an
        acknowledgement.  This method never signals a numeric PID: inability
        to obtain the child identity or establish its absence remains visible
        cleanup uncertainty.
        """
        try:
            report = self._report.read_text() if self._report is not None else ""
            ready, pid_text = report.split(":", 1)
            if ready != "R":
                raise ValueError("missing child identity")
            pid = int(pid_text.split("E", 1)[0])
        except (OSError, ValueError) as exc:
            self.cleanup_errors.append(AssertionError(f"terminal identity unavailable: {exc}"))
            return False
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)  # read-only presence observation, never cleanup signalling
            except ProcessLookupError:
                return True
            except OSError as exc:
                self.cleanup_errors.append(AssertionError(f"terminal observation unavailable: {exc}"))
                return False
            time.sleep(.02)
        self.cleanup_errors.append(AssertionError("terminal absence not observed"))
        return False

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        try:
            if self._control is not None:
                self._control.unlink()
        except OSError as exc:
            self.release_error = exc

    def close(self) -> None:
        try:
            self.release()
        except BaseException as exc:
            self.cleanup_errors.append(exc)
        if self._root is not None:
            try:
                shutil.rmtree(self._root)
            except BaseException as exc:
                self.cleanup_errors.append(exc)
            finally:
                self._root = None


def _note_cleanup(primary: BaseException | None, child: _CooperativeEscapedChild) -> None:
    """Keep the primary failure while making cleanup failure/unknown visible."""
    evidence = [str(error) for error in child.cleanup_errors]
    if child.release_error is not None:
        evidence.append(f"release: {child.release_error}")
    if evidence:
        if primary is not None:
            primary.add_note("escaped-child cleanup: " + "; ".join(evidence))
        else:
            raise AssertionError("escaped-child cleanup unknown: " + "; ".join(evidence))


def _finalize_owned_children(
    primary: BaseException | None,
    children: list[tuple[_CooperativeEscapedChild | None, object | None]],
) -> None:
    """Attempt every acquired test-owned finalizer before reporting evidence."""
    for child, process in reversed(children):
        if child is None:
            continue
        try:
            child.close()
        except BaseException as exc:
            child.cleanup_errors.append(exc)
        if process is not None:
            try:
                process.wait(timeout=2)
            except BaseException as exc:
                child.cleanup_errors.append(exc)
    evidence: list[str] = []
    for child, _process in children:
        if child is None:
            continue
        evidence.extend(str(error) for error in child.cleanup_errors)
        if child.release_error is not None:
            evidence.append(f"release: {child.release_error}")
    if evidence:
        message = "escaped-child cleanup: " + "; ".join(evidence)
        if primary is not None:
            primary.add_note(message)
        else:
            raise AssertionError("escaped-child cleanup unknown: " + "; ".join(evidence))


def _observe_terminal(child: _CooperativeEscapedChild) -> bool:
    observer = getattr(child, "observe_terminal", None)
    if observer is None:
        child.cleanup_errors.append(AssertionError("terminal identity unavailable"))
        return False
    return observer()


def test_cooperative_escaped_child_releases_only_its_owned_process():
    """The test control closes one same-command child without name/PID kills."""
    first = second = None
    first_process = second_process = None
    try:
        first = _CooperativeEscapedChild()
        first_process = subprocess.Popen(first.argv)
        second = _CooperativeEscapedChild()
        second_process = subprocess.Popen(second.argv)
        assert first.wait_for(b"R")
        assert second.wait_for(b"R")
        first.release()
        assert first.release_error is None
        assert first.wait_for(b"E")  # acknowledgement is not terminal proof
        assert first_process.wait(timeout=2) == 0
        assert second_process.poll() is None
    finally:
        primary = sys.exception()
        _finalize_owned_children(primary, [(first, first_process), (second, second_process)])


def test_escaped_shipping_seam_uses_supported_launchspec_and_finalizes_on_finish_error(monkeypatch):
    """Exercise the changed real test seam, not a direct-Popen-only helper."""
    class _Child:
        argv = ("owned-child", "control", "report", "1")
        release_error = None
        cleanup_errors: list[BaseException] = []
        _root = object()

        def wait_for(self, signal_byte, timeout=2.0):
            return signal_byte == b"R"

        def release(self):
            return None

        def close(self):
            self._root = None

    child = _Child()
    launched: list[LaunchSpec] = []

    class _Backend:
        def prepare(self, *_args):
            return object()

        def launch(self, _pending, spec):
            launched.append(spec)
            return SimpleNamespace(process=SimpleNamespace(poll=lambda: None))

        def finish(self, *_args, **_kwargs):
            raise RuntimeError("finish-primary")

    monkeypatch.setattr(sys.modules[__name__], "_CooperativeEscapedChild", lambda: child)
    with pytest.raises(RuntimeError, match="finish-primary"):
        test_escaped_descendant_is_best_effort_survivor_real(_Backend())
    assert launched and isinstance(launched[0], LaunchSpec)
    assert "pass_fds" not in repr(launched[0])
    assert child._root is None
    assert child.release_error is None


@pytest.mark.parametrize("phase", ("prepare", "launch", "finish", "assertion"))
def test_escaped_shipping_seam_finalizes_before_identity_or_survivor_assignment(monkeypatch, phase):
    """Every real-seam failure closes the test-owned transport and keeps its error."""
    class _Child:
        argv = ("owned-child",)
        release_error = PermissionError("release failed")
        cleanup_errors = [OSError("close failed")]
        _root = object()

        def wait_for(self, signal_byte, timeout=2.0):
            return phase != "assertion" or signal_byte != b"R"

        def release(self):
            return None

        def close(self):
            self._root = None

    child = _Child()

    class _Backend:
        def prepare(self, *_args):
            if phase == "prepare":
                raise RuntimeError("prepare-primary")
            return object()

        def launch(self, _pending, spec):
            assert isinstance(spec, LaunchSpec)
            if phase == "launch":
                raise RuntimeError("launch-primary")
            return SimpleNamespace(process=SimpleNamespace(poll=lambda: None))

        def finish(self, *_args, **_kwargs):
            if phase == "finish":
                raise RuntimeError("finish-primary")
            return SimpleNamespace(survivors=("owned",), quiescent=False, cleanup_status=CleanupStatus.TERM)

    monkeypatch.setattr(sys.modules[__name__], "_CooperativeEscapedChild", lambda: child)
    with pytest.raises((RuntimeError, AssertionError)) as raised:
        test_escaped_descendant_is_best_effort_survivor_real(_Backend())
    assert child._root is None
    notes = getattr(raised.value, "__notes__", ())
    assert any("release failed" in note and "close failed" in note for note in notes)


def test_escaped_shipping_seam_reports_missing_terminal_acknowledgement(monkeypatch):
    """An E marker without reaping is insufficient; missing E is visible as unknown."""
    class _Child:
        argv = ("owned-child",)
        release_error = None
        cleanup_errors: list[BaseException] = []
        _root = object()

        def wait_for(self, signal_byte, timeout=2.0):
            return signal_byte == b"R"

        def release(self):
            return None

        def close(self):
            self._root = None

    class _Backend:
        def prepare(self, *_args):
            return object()

        def launch(self, _pending, _spec):
            return SimpleNamespace(process=SimpleNamespace(poll=lambda: None))

        def finish(self, *_args, **_kwargs):
            return SimpleNamespace(survivors=("owned",), quiescent=False, cleanup_status=CleanupStatus.TERM)

    child = _Child()
    monkeypatch.setattr(sys.modules[__name__], "_CooperativeEscapedChild", lambda: child)
    with pytest.raises(AssertionError, match="escaped-child cleanup unknown: missing release acknowledgement"):
        test_escaped_descendant_is_best_effort_survivor_real(_Backend())
    assert child._root is None


def test_cooperative_child_partial_construction_closes_owned_directory(monkeypatch):
    """A failed control-marker creation cannot leak the already-created directory."""
    created: list[Path] = []
    real_mkdtemp = tempfile.mkdtemp

    def owned_mkdtemp(*args, **kwargs):
        root = Path(real_mkdtemp(*args, **kwargs))
        created.append(root)
        return str(root)

    monkeypatch.setattr(tempfile, "mkdtemp", owned_mkdtemp)
    monkeypatch.setattr(Path, "touch", lambda _self: (_ for _ in ()).throw(OSError("touch failed")))
    with pytest.raises(OSError, match="touch failed"):
        _CooperativeEscapedChild()
    assert created and not created[0].exists()


def test_cooperative_child_partial_construction_keeps_all_cleanup_errors(monkeypatch):
    """A constructor primary retains both concrete release/removal failures."""
    monkeypatch.setattr(tempfile, "mkdtemp", lambda **_kwargs: "/tmp/not-created-owned-child")
    monkeypatch.setattr(Path, "touch", lambda _self: (_ for _ in ()).throw(OSError("primary touch")))
    monkeypatch.setattr(Path, "unlink", lambda _self: (_ for _ in ()).throw(PermissionError("release failed")))
    monkeypatch.setattr(shutil, "rmtree", lambda _path: (_ for _ in ()).throw(PermissionError("remove failed")))
    with pytest.raises(OSError, match="primary touch") as raised:
        _CooperativeEscapedChild()
    notes = getattr(raised.value, "__notes__", ())
    assert any("release failed" in note for note in notes)
    assert any("remove failed" in note for note in notes)


def test_escaped_shipping_seam_rejects_preexit_acknowledgement_without_terminal_absence(monkeypatch):
    """The actual LaunchSpec seam cannot turn E plus a live child into clean-up."""
    class _Child:
        argv = ("owned-child",)
        release_error = None
        cleanup_errors: list[BaseException] = []
        _root = object()

        def wait_for(self, signal_byte, timeout=2.0):
            return signal_byte in (b"R", b"E")

        def observe_terminal(self):
            return False

        def release(self):
            return None

        def close(self):
            self._root = None

    class _Backend:
        def prepare(self, *_args):
            return object()

        def launch(self, _pending, spec):
            assert isinstance(spec, LaunchSpec)
            return SimpleNamespace(process=SimpleNamespace(poll=lambda: None))

        def finish(self, *_args, **_kwargs):
            return SimpleNamespace(survivors=("owned",), quiescent=False, cleanup_status=CleanupStatus.TERM)

    child = _Child()
    monkeypatch.setattr(sys.modules[__name__], "_CooperativeEscapedChild", lambda: child)
    with pytest.raises(AssertionError, match="terminal observation unknown"):
        test_escaped_descendant_is_best_effort_survivor_real(_Backend())
    assert child._root is None


def test_finalization_attempts_every_registered_resource_after_wait_failure():
    """Later wait failure records evidence without bypassing earlier closes."""
    events: list[str] = []

    class _Child:
        release_error = None
        cleanup_errors: list[BaseException]

        def __init__(self, name):
            self.name = name
            self.cleanup_errors = []

        def close(self):
            events.append(f"close:{self.name}")

    class _Process:
        def __init__(self, name, fail=False):
            self.name, self.fail = name, fail

        def wait(self, **_kwargs):
            events.append(f"wait:{self.name}")
            if self.fail:
                raise TimeoutError(f"wait:{self.name}")

    first, second = _Child("first"), _Child("second")
    with pytest.raises(AssertionError, match="wait:second"):
        _finalize_owned_children(None, [(first, _Process("first")), (second, _Process("second", fail=True))])
    assert events == ["close:second", "wait:second", "close:first", "wait:first"]


def test_finalization_reports_every_resource_error_without_primary():
    """No-primary cleanup aggregates both errors after every finalizer runs."""
    events: list[str] = []

    class _Child:
        release_error = None

        def __init__(self, name):
            self.name = name
            self.cleanup_errors: list[BaseException] = []

        def close(self):
            events.append(f"close:{self.name}")

    class _Process:
        def __init__(self, name):
            self.name = name

        def wait(self, **_kwargs):
            events.append(f"wait:{self.name}")
            raise TimeoutError(f"wait:{self.name}")

    first, second = _Child("first"), _Child("second")
    with pytest.raises(AssertionError) as raised:
        _finalize_owned_children(None, [(first, _Process("first")), (second, _Process("second"))])
    assert "wait:first" in str(raised.value)
    assert "wait:second" in str(raised.value)
    assert events == ["close:second", "wait:second", "close:first", "wait:first"]


def test_finalization_keeps_primary_and_every_resource_error():
    """Cleanup evidence annotates, rather than replacing, an existing primary."""
    events: list[str] = []

    class _Child:
        release_error = None

        def __init__(self, name):
            self.name = name
            self.cleanup_errors: list[BaseException] = []

        def close(self):
            events.append(f"close:{self.name}")

    class _Process:
        def __init__(self, name):
            self.name = name

        def wait(self, **_kwargs):
            events.append(f"wait:{self.name}")
            raise TimeoutError(f"wait:{self.name}")

    primary = RuntimeError("shipping primary")
    first, second = _Child("first"), _Child("second")
    _finalize_owned_children(primary, [(first, _Process("first")), (second, _Process("second"))])
    assert str(primary) == "shipping primary"
    notes = getattr(primary, "__notes__", ())
    assert any("wait:first" in note and "wait:second" in note for note in notes)
    assert events == ["close:second", "wait:second", "close:first", "wait:first"]


def test_cooperative_child_intrinsic_expiry_has_terminal_absence_observation():
    """An unreleased owned surrogate expires, is reaped, then becomes absent."""
    child = None
    process = None
    try:
        child = _CooperativeEscapedChild(deadline_seconds=.05)
        process = subprocess.Popen(child.argv)
        assert child.wait_for(b"R")
        assert child.wait_for(b"E")
        assert process.wait(timeout=2) == 0
        assert child.observe_terminal()
    finally:
        _finalize_owned_children(sys.exception(), [(child, process)])


def test_intrinsic_expiry_popen_failure_retains_cleanup_evidence(monkeypatch):
    """The actual expiry seam finalizes an acquired helper if Popen fails."""
    child = SimpleNamespace(
        argv=("owned-child",),
        release_error=None,
        cleanup_errors=[],
        close_calls=0,
    )

    def close():
        child.close_calls += 1
        child.cleanup_errors.append(OSError("helper cleanup failed"))

    child.close = close
    monkeypatch.setattr(sys.modules[__name__], "_CooperativeEscapedChild", lambda **_kwargs: child)
    monkeypatch.setattr(subprocess, "Popen", lambda _argv: (_ for _ in ()).throw(OSError("popen failed")))
    with pytest.raises(OSError, match="popen failed") as raised:
        test_cooperative_child_intrinsic_expiry_has_terminal_absence_observation()
    assert child.close_calls == 1
    assert any("helper cleanup failed" in note for note in getattr(raised.value, "__notes__", ()))


def test_intrinsic_expiry_cleanup_failure_is_not_silent(monkeypatch):
    """A successful expiry still fails when its finalizer records an error."""
    child = SimpleNamespace(
        argv=("owned-child",),
        release_error=None,
        cleanup_errors=[],
        close_calls=0,
        wait_for=lambda _byte: True,
        observe_terminal=lambda: True,
    )

    def close():
        child.close_calls += 1
        child.cleanup_errors.append(OSError("helper cleanup failed"))

    child.close = close
    process = SimpleNamespace(wait=lambda **_kwargs: 0, poll=lambda: 0)
    monkeypatch.setattr(sys.modules[__name__], "_CooperativeEscapedChild", lambda **_kwargs: child)
    monkeypatch.setattr(subprocess, "Popen", lambda _argv: process)
    with pytest.raises(AssertionError, match="helper cleanup failed"):
        test_cooperative_child_intrinsic_expiry_has_terminal_absence_observation()
    assert child.close_calls == 1


@pytest.mark.parametrize("failure", ("second-helper", "first-popen", "second-popen"))
def test_cooperative_child_acquisition_failure_finalizes_registered_resources(monkeypatch, failure):
    """Each construction/launch gap closes every resource acquired so far."""
    children = []

    class _Child:
        argv = ("owned-child",)
        release_error = None

        def __init__(self):
            self.closed = False
            self.cleanup_errors: list[BaseException] = []
            children.append(self)
            if failure == "second-helper" and len(children) == 2:
                raise OSError("second helper")

        def wait_for(self, _byte, timeout=2.0):
            return True

        def release(self):
            return None

        def close(self):
            self.closed = True

    launches = 0

    def popen(_argv):
        nonlocal launches
        launches += 1
        if failure == "first-popen" or failure == "second-popen" and launches == 2:
            raise OSError(failure)
        return SimpleNamespace(wait=lambda **_kwargs: 0, poll=lambda: None)

    monkeypatch.setattr(sys.modules[__name__], "_CooperativeEscapedChild", _Child)
    monkeypatch.setattr(subprocess, "Popen", popen)
    with pytest.raises(OSError, match="helper|popen"):
        test_cooperative_escaped_child_releases_only_its_owned_process()
    assert children and all(child.closed for child in children if child is not children[-1] or failure != "second-helper")


# ── deterministic unit tests (fake census) ───────────────────────────


class _FakeObservation:
    def __init__(self, pid, ppid=None, pgid=None, identity=None, rss=None, cpu=None, state=None):
        self.pid = pid
        self.ppid = ppid
        self.pgid = pgid
        self.start_identity = identity
        self.rss_bytes = rss
        self.cpu_seconds = cpu
        self.state = state


class _FakeCensus:
    """Deterministic census/reader double with scriptable observations."""

    def __init__(self, observations=None, identities=None, pgids=None):
        self.observations = observations or []
        self._identities = identities or {}
        self._pgids = pgids or {}

    def start_identity(self, pid):
        return self._identities.get(pid)

    def reader(self):
        observations = self.observations

        class _FakeReader:
            def __init__(self, obs):
                self._obs = obs

            def read_process(self, pid):
                return next((o for o in self._obs if o.pid == pid), None)

        return _FakeReader(observations)

    def descendants(self, root_pid, root_identity=None, include_root=False):
        # Simplistic chain walk over the fake table.
        by_pid = {o.pid: o for o in self.observations}
        if root_pid not in by_pid:
            return ()
        root = by_pid[root_pid]
        if root_identity is not None and root.start_identity != root_identity:
            return ()
        found = [root] if include_root else []
        stack = [o for o in self.observations if o.ppid == root_pid]
        while stack:
            o = stack.pop()
            found.append(o)
            stack.extend(x for x in self.observations if x.ppid == o.pid)
        return tuple(found)

    def group_members(self, pgid):
        return tuple(o for o in self.observations if o.pgid == pgid)


def _fake_backend(census: _FakeCensus, pgids=None) -> MacOSProcessGroupBackend:
    backend = MacOSProcessGroupBackend(census=census)
    # Fake the OS-level group queries and liveness (deterministic).
    backend._os_getpgid = lambda pid: (pgids or {}).get(pid, pid)
    backend._group_members_alive = lambda pgid: {}
    backend._signal_group = lambda pgid, sig: None
    return backend


def test_probe_unhealthy_when_census_unavailable():
    class _BrokenCensus:
        def start_identity(self, pid):
            raise OSError("libproc unavailable")

        def descendants(self, *a, **k):
            raise OSError("libproc unavailable")

        def group_members(self, pgid):
            raise OSError("libproc unavailable")

    backend = MacOSProcessGroupBackend(census=_BrokenCensus())
    report = backend.probe()
    assert report.healthy is False
    assert report.capabilities == {}


def test_probe_reports_best_effort_capabilities():
    census = _FakeCensus()
    backend = MacOSProcessGroupBackend(census=census)
    report = backend.probe()
    assert report.healthy is True
    from runtime.platform.session_backend import Capability, CapabilityLevel

    assert report.level(Capability.KILLS_TREE_BEST_EFFORT) is CapabilityLevel.BEST_EFFORT
    assert report.level(Capability.REPORTS_MEMORY_PEAK) is CapabilityLevel.BEST_EFFORT
    assert report.level(Capability.LIMITS_MEMORY) is CapabilityLevel.UNAVAILABLE


def test_launch_failure_raises(monkeypatch):
    backend = MacOSProcessGroupBackend(census=_FakeCensus())

    def bad_popen(*args, **kwargs):
        raise OSError("no such binary")

    monkeypatch.setattr("runtime.platform.macos_process_group.subprocess.Popen", bad_popen)
    pending = backend.prepare(_request(), _policy())
    with pytest.raises(BackendLaunchError):
        backend.launch(pending, LaunchSpec(argv=("nonexistent-binary",)))


def test_finish_refuses_ambiguous_group_and_reports_survivors():
    """PID-reuse safety: a group we cannot prove is ours is never signaled."""
    census = _FakeCensus(
        observations=[
            _FakeObservation(pid=100, ppid=1, pgid=100, identity="root-1"),
        ],
        identities={100: "root-2"},  # root identity CHANGED (PID reused)
        pgids={100: 100},
    )
    backend = _fake_backend(census, pgids={100: 100})
    # Snapshot captured at launch with the ORIGINAL identity.
    with backend._lock:
        backend._sessions["tok-1"] = (100, "root-1", 100, 0.0, {100: _FakeObservation(100, ppid=1, pgid=100, identity="root-1")})
    signaled = []

    def signal_group(pgid, sig):
        signaled.append(sig)

    backend._signal_group = signal_group  # type: ignore[method-assign]
    # A verified member sits in the ambiguous group -> fail-closed INCOMPLETE.
    backend._group_members_alive = lambda pgid: {101: "child-1"}  # type: ignore[method-assign]
    receipt = backend.finish(_running(100, "root-1"), "success", grace_seconds=0.2)
    assert signaled == []  # never signaled an unverifiable group
    assert receipt.cleanup_status is CleanupStatus.INCOMPLETE
    assert receipt.quiescent is False


def test_finish_terms_then_kills_group():
    """TERM-resistant group members force the KILL escalation."""
    census = _FakeCensus(
        observations=[
            _FakeObservation(pid=100, ppid=1, pgid=100, identity="root-1"),
            _FakeObservation(pid=101, ppid=100, pgid=100, identity="child-1"),
        ],
        identities={100: "root-1", 101: "child-1"},
    )
    backend = _fake_backend(census, pgids={100: 100, 101: 100})
    # Simulate: the child ignores TERM and survives until KILL.
    alive = {"101": True}

    def group_members_alive(pgid):
        return {101: "child-1"} if alive["101"] else {}

    backend._group_members_alive = group_members_alive  # type: ignore[method-assign]

    def signal_group(pgid, sig):
        if sig == signal.SIGKILL:
            alive["101"] = False
            # The KILL takes effect in the (fake) process table.
            census.observations[:] = [
                o for o in census.observations if o.pid not in (100, 101)
            ]

    backend._signal_group = signal_group  # type: ignore[method-assign]
    backend._census.start_identity = lambda pid: {100: "root-1", 101: "child-1"}[pid]
    with backend._lock:
        backend._sessions["tok-1"] = (100, "root-1", 100, 0.0, {})
    receipt = backend.finish(_running(100, "root-1"), "failure", grace_seconds=0.2)
    assert receipt.cleanup_status is CleanupStatus.KILL
    assert receipt.quiescent is True
    assert receipt.survivors == ()


def test_finish_clean_success_no_residue():
    # The root exited cleanly before finish: the fresh final census is empty.
    census = _FakeCensus(observations=[], identities={100: "root-1"})
    backend = _fake_backend(census, pgids={100: 100})
    with backend._lock:
        backend._sessions["tok-1"] = (100, "root-1", 100, 0.0, {})
    receipt = backend.finish(_running(100, "root-1"), "success", grace_seconds=0.2)
    assert receipt.cleanup_status is CleanupStatus.CLEAN
    assert receipt.quiescent is True


def test_prepare_launch_finish_carry_receipt_attribution():
    """Slice C: the macOS backend carries the bounded receipt attribution
    (invocation_kind + executor_profile) from the AdmissionRequest through
    prepare/launch into the finish-time Receipt — same honest contract as
    the Linux backend, with no limits applied (macOS stays best-effort)."""
    census = _FakeCensus(
        observations=[_FakeObservation(pid=100, ppid=1, pgid=100, identity="root-1")],
        identities={100: "root-1"},
    )
    backend = _fake_backend(census, pgids={100: 100})
    pending = backend.prepare(_request(), _policy())
    assert pending.invocation_kind == "schedule"
    assert pending.executor_profile == "claude"

    # Direct launch is POSIX-only; assert the handle contract via the same
    # seam the supervisor uses (prepare -> launch with the real subprocess).
    import subprocess as _sp
    import sys

    proc = _sp.Popen(
        [sys.executable, "-c", "import time; time.sleep(0.2)"],
        stdout=_sp.DEVNULL,
        stderr=_sp.DEVNULL,
        start_new_session=True,
    )
    try:
        backend._census.start_identity = lambda pid: "root-1"  # type: ignore[method-assign]
        backend._os_getpgid = lambda pid: proc.pid  # type: ignore[method-assign]
        with backend._lock:
            backend._sessions[pending.token] = (proc.pid, "root-1", proc.pid, 0.0, {})
        running = RunningHandle(
            backend=backend.name,
            token=pending.token,
            request_id=pending.request_id,
            root_pid=proc.pid,
            start_identity="root-1",
            process=proc,
            invocation_kind=pending.invocation_kind,
            executor_profile=pending.executor_profile,
        )
        receipt = backend.finish(running, "success", grace_seconds=0.2)
        assert receipt.invocation_kind == "schedule"
        assert receipt.executor_profile == "claude"
        assert receipt.cleanup_status is CleanupStatus.CLEAN
    finally:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        proc.wait(timeout=2)


def test_finish_fresh_census_detects_late_escaped_descendant():
    """A descendant that escapes AFTER the last periodic sample (the stored
    session snapshot is EMPTY) is caught by finish's OWN fresh final
    identity-safe descendant census — no manual pre-sample refresh (the
    shipping finish seam)."""
    census = _FakeCensus(
        observations=[
            _FakeObservation(pid=100, ppid=1, pgid=100, identity="root-1"),
            _FakeObservation(pid=102, ppid=100, pgid=999, identity="esc-1"),
        ],
        identities={100: "root-1", 102: "esc-1"},
    )
    backend = _fake_backend(census, pgids={100: 100, 102: 999})
    with backend._lock:
        # The last periodic sample was taken BEFORE the escaped child existed.
        backend._sessions["tok-1"] = (100, "root-1", 100, 0.0, {})
    receipt = backend.finish(_running(100, "root-1"), "success", grace_seconds=0.1)
    assert any(sv.pid == 102 for sv in receipt.survivors), (
        "escaped descendant must be censused by finish's own fresh census"
    )
    assert receipt.quiescent is False


def test_finish_census_failure_raises_not_clean():
    """A census/measurement exception at finish is EXPLICIT failure evidence:
    it propagates out of finish (never collapsing into an empty CLEAN group);
    the supervisor turns teardown failure into fail-closed admission
    blocking."""
    census = _FakeCensus(
        observations=[
            _FakeObservation(pid=100, ppid=1, pgid=100, identity="root-1"),
        ],
        identities={100: "root-1"},
    )

    def boom_group_members(pgid):
        raise OSError("libproc enumeration failed")

    census.group_members = boom_group_members
    backend = _fake_backend(census, pgids={100: 100})
    with backend._lock:
        backend._sessions["tok-1"] = (
            100, "root-1", 100, 0.0, {100: census.observations[0]},
        )
    with pytest.raises(OSError, match="libproc enumeration failed"):
        backend.finish(_running(100, "root-1"), "success", grace_seconds=0.1)


def test_finish_merges_sampled_provenance():
    census = _FakeCensus(
        observations=[_FakeObservation(pid=100, ppid=1, pgid=100, identity="root-1")],
        identities={100: "root-1"},
    )
    backend = _fake_backend(census, pgids={100: 100})
    with backend._lock:
        backend._sessions["tok-1"] = (100, "root-1", 100, 0.0, {})
    samples = (
        ResourceSample(sampled_at=1.0, memory_peak_bytes=100, cpu_total_seconds=0.5, process_count=2),
        ResourceSample(sampled_at=2.0, memory_peak_bytes=300, cpu_total_seconds=1.0, process_count=4),
    )
    receipt = backend.finish(_running(100, "root-1"), "success", grace_seconds=0.2, samples=samples)
    assert receipt.memory_peak_bytes == 300
    assert receipt.memory_peak_provenance is MeasurementProvenance.SAMPLED
    assert receipt.cpu_total_seconds == 1.0
    assert receipt.process_peak == 4  # sampled peak from the samples
    assert receipt.process_peak_provenance is MeasurementProvenance.SAMPLED
    assert receipt.sample_gaps == (1.0,)


def test_finish_preserves_primary_terminal_reason():
    census = _FakeCensus(
        observations=[_FakeObservation(pid=100, ppid=1, pgid=100, identity="root-1")],
        identities={100: "root-1"},
    )
    backend = _fake_backend(census, pgids={100: 100})
    with backend._lock:
        backend._sessions["tok-1"] = (100, "root-1", 100, 0.0, {})
    for reason in ("success", "failure", "timeout", "cancelled", "shutdown"):
        receipt = backend.finish(_running(100, "root-1"), reason, grace_seconds=0.1)
        assert receipt.terminal_reason == reason


def test_abandon_and_recover():
    backend = MacOSProcessGroupBackend(census=_FakeCensus())
    pending = backend.prepare(_request(), _policy())
    backend.abandon(pending)
    result = backend.recover("tok")
    assert result.recovered is False


def test_backend_is_session_backend():
    assert isinstance(MacOSProcessGroupBackend(census=_FakeCensus()), SessionBackend)


# ── real POSIX integration (gated on the operational probe) ──────────

real_integration = pytest.mark.integration


def _require_ops():
    backend = MacOSProcessGroupBackend()
    report = backend.probe()
    if not report.healthy:
        pytest.skip(f"process-group/census ops unusable on this runner: {report.reason}")
    return backend


@pytest.fixture(scope="module")
def real_backend():
    return _require_ops()


def _spawn_and_wait(backend, argv, timeout=5.0):
    pending = backend.prepare(_request(), _policy())
    running = backend.launch(pending, LaunchSpec(argv=argv))
    deadline = time.monotonic() + timeout
    while running.process.poll() is None and time.monotonic() < deadline:
        time.sleep(0.05)
    return running


@real_integration
def test_probe_real_creates_signals_reaps_group_no_residue(real_backend):
    backend = _require_ops()
    report = backend.probe()
    assert report.healthy is True
    from runtime.platform.session_backend import Capability, CapabilityLevel

    assert report.level(Capability.KILLS_TREE_BEST_EFFORT) is CapabilityLevel.BEST_EFFORT


@real_integration
def test_launch_creates_new_process_group_real(real_backend):
    running = _spawn_and_wait(real_backend, ("sh", "-c", "sleep 30"))
    try:
        assert os.getpgid(running.root_pid) == running.root_pid
        assert running.start_identity
    finally:
        real_backend.finish(running, "success", grace_seconds=2.0)


@real_integration
def test_finish_clean_success_cleans_group_real(real_backend):
    running = _spawn_and_wait(real_backend, ("sh", "-c", "sleep 30"))
    receipt = real_backend.finish(running, "success", grace_seconds=2.0)
    assert receipt.cleanup_status is CleanupStatus.TERM  # live group -> TERM
    assert receipt.quiescent is True
    assert receipt.survivors == ()


@real_integration
def test_finish_clean_success_with_surviving_descendant_real(real_backend):
    """MANDATORY success-path descendant cleanup: the parent exits 0 while a
    child keeps running in the group — finish must TERM/KILL the whole group."""
    pending = real_backend.prepare(_request(), _policy())
    running = real_backend.launch(
        pending, LaunchSpec(argv=("sh", "-c", "sleep 60 & sleep 0.2"))
    )
    deadline = time.monotonic() + 5
    while running.process.poll() is None and time.monotonic() < deadline:
        time.sleep(0.05)
    assert running.process.returncode == 0
    receipt = real_backend.finish(running, "success", grace_seconds=2.0)
    assert receipt.cleanup_status is CleanupStatus.TERM
    assert receipt.quiescent is True
    assert receipt.survivors == ()


@real_integration
def test_escaped_descendant_is_best_effort_survivor_real(real_backend):
    """SHIPPING finish seam: a descendant that ``setsid``s away after launch
    (no sampler ever ran — only the launch-time snapshot, taken before the
    child existed) is censused by finish's OWN fresh final identity-safe
    descendant census. No manual pre-sample refresh.

    Documented best-effort limitation: the census must run while the root
    still lives — a descendant that escapes AND is reparented before finish
    (root already exited) is unobservable by any process-table walk."""
    escaped = _CooperativeEscapedChild()
    try:
        pending = real_backend.prepare(_request(), _policy())
        running = real_backend.launch(
            pending,
            LaunchSpec(
                argv=("sh", "-c", f"{shlex.join(escaped.argv)} & sleep 5"),
            ),
        )
        assert escaped.wait_for(b"R")
        # The escaped child is spawned within the first instant; finish runs
        # while the root still lives so the fresh census can see the child.
        time.sleep(0.3)
        assert running.process.poll() is None  # root still alive
        receipt = real_backend.finish(running, "success", grace_seconds=1.0)
        # The escaped child survives (new session) — best-effort truth:
        # censused survivor, never a fabricated clean claim.
        assert receipt.survivors, "escaped descendant must be censused by finish's own census"
        assert receipt.quiescent is False
        assert receipt.cleanup_status is not CleanupStatus.INCOMPLETE
    except BaseException as primary:
        raise
    finally:
        # Run only after the shipping census/assertions. The control marker is
        # owned by this test child; no process-group or name signal is
        # attempted.  E is pre-exit acknowledgement, never terminal proof.
        primary = sys.exception()
        try:
            escaped.release()
            if not escaped.wait_for(b"E"):
                escaped.cleanup_errors.append(AssertionError("missing release acknowledgement"))
            if not _observe_terminal(escaped):
                escaped.cleanup_errors.append(AssertionError("terminal observation unknown"))
        except BaseException as cleanup_error:
            escaped.cleanup_errors.append(cleanup_error)
        finally:
            escaped.close()
            _note_cleanup(primary, escaped)


@real_integration
def test_sampler_identity_safety_real(real_backend):
    running = _spawn_and_wait(real_backend, ("sh", "-c", "sleep 30"))
    try:
        sample = real_backend.sampler()(running)
        assert sample.provenance is MeasurementProvenance.SAMPLED
        assert sample.process_count is not None and sample.process_count >= 1
        assert sample.memory_peak_bytes is not None
    finally:
        real_backend.finish(running, "success", grace_seconds=2.0)


@real_integration
def test_finish_merges_real_sampled_receipt(real_backend):
    running = _spawn_and_wait(real_backend, ("sh", "-c", "sleep 30"))
    try:
        sample = real_backend.sampler()(running)
        receipt = real_backend.finish(running, "success", grace_seconds=2.0, samples=(sample,))
    except Exception:
        running.process.kill()
        raise
    assert receipt.memory_peak_provenance is MeasurementProvenance.SAMPLED
    assert receipt.process_peak is not None and receipt.process_peak >= 1
