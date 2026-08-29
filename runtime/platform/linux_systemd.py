"""Real Linux systemd/cgroup-v2 session backend (THR-207 Slice B).

Implements the :class:`SessionBackend` protocol with **real systemd/cgroup
operations** — no OS/version inference:

* ``probe`` creates a harmless transient scope with tiny non-triggering
  limits (``MemoryMax``/``TasksMax``/``CPUQuota``), verifies the unit's
  ``ControlGroup``, the applied limit files (``memory.max``/``pids.max``/
  ``cpu.max``), live membership, and the authoritative counters
  (``memory.current``/``memory.peak``/``pids.peak``/``pids.current``/``cpu.stat``), then
  explicitly stops the scope, verifies cgroup emptiness, and removes the
  probe slice. It reports a capability as ``guaranteed`` **only** for what
  was actually created, applied, and verified; anything else stays
  ``unavailable`` (missing enforcement tightens admission, never widens it).
* ``launch`` runs the target into a per-session transient scope under the
  aggregate HappyRanch slice (``systemd-run --user --scope`` preserves the
  blocking stdio/PID model) and verifies the launched process's cgroup
  membership.
* ``finish`` **explicitly stops the whole scope on every terminal path,
  clean success included**, waits within the measured grace, escalates to
  ``KILL`` when the unit is still active, and verifies cgroup emptiness
  before declaring quiescence. Verified residue that survives a guaranteed
  cleanup is reported as :class:`SurvivorRecord` — the supervisor's
  ``ResidueAccountant`` blocks admission on ``kills_tree_guaranteed``
  residue until reconciliation.
* Counters are captured **while the scope is alive**: a per-session exit-
  watcher thread opens the authoritative kernel counter files
  (``memory.peak``, ``cpu.stat`` ``usage_usec``, ``pids.peak``)
  **independently** — an absent old-kernel ``pids.peak`` never discards
  the guaranteed memory/CPU capture and never invents provenance — and is
  woken by a deterministic exit notification (``pidfd`` poll, with a
  ``waitid(WNOWAIT)`` fallback; no polling cadence for the exit itself) at
  the instant the contained process exits. It preads the final counters
  before systemd garbage-collects the transient scope's cgroup (live
  evidence: the cgroup directory survives the exit by only ~0.3–0.6 ms,
  long before ``finish`` runs on a clean-success path, while the exit-
  instant preads take ~10–30 us on the open fds) and carries that
  immutable observation through wait/reap and actual
  drain/cancellation/cleanup into the finish-time receipt with honest
  KERNEL provenance. ``finish``'s own pre-stop read remains the
  authoritative fallback for paths where the process is still running at
  finish time (user cancellation / daemon drain). ``pids.current`` is only
  a best-effort live membership count — never labeled an authoritative
  kernel peak (an empty-tree teardown value of 0 must not masquerade as
  one); without ``pids.peak`` it is merged honestly with sampled evidence
  under ``sampled`` provenance. A cgroup that has vanished at finish time
  is never silently treated as verified evidence: it is recorded as an
  explicit ``cgroup_vanished`` event, and quiescence is only claimed when
  the unit state is positively terminal (an UNKNOWN unit-state
  interrogation still fails closed to ``INCOMPLETE``).

Slice B applies **no resource limits** to session scopes (the policy
snapshot carries no approved limit values — 16/64/72/4096/2800 remain
unapproved measurement candidates); the probe proves the enforcement
machinery itself works, and lifecycle containment (launch inside the scope,
whole-tree stop, emptiness verification) is what Slice B wires.

Slice C (THR-207) ships the founder-approved **fixed initial Linux
enforcement policy** for real session scopes: task sessions get
``MemoryHigh=14G`` / ``MemoryMax=24G`` and thread/dream/wake/schedule (and
any unknown kind, conservatively) get ``MemoryHigh=2G`` / ``MemoryMax=4G``,
with ``TasksMax=1024`` for every supervised session and **no ``CPUQuota``**
(see ``runtime/platform/enforcement_policy.py``). The immutable per-
invocation envelope is resolved from the existing ``AdmissionRequest``
``invocation_kind`` at ``prepare``/``launch`` and verified as applied to the
scope's cgroup at launch (fail-closed). The probe's deliberately tiny
probe-only limit values (16M/4/10% incl. ``CPUQuota``) are untouched and
are never confused with real session policy.
"""

from __future__ import annotations

import logging
import os
import select
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable

from runtime.platform.enforcement_policy import enforcement_policy_for
from runtime.platform.process_census import (
    LinuxProcReader,
    ProcessTreeCensus,
    is_zombie,
    merge_sample_peaks,
    sample_gaps,
)
from runtime.platform.session_backend import (
    BackendError,
    BackendLaunchError,
    BackendPrepareError,
    Capability,
    CapabilityLevel,
    CapabilityReport,
    CleanupStatus,
    MeasurementProvenance,
    PendingHandle,
    Receipt,
    RecoveryResult,
    ResourceSample,
    RunningHandle,
    SessionBackend,
    SurvivorRecord,
)

if TYPE_CHECKING:
    from runtime.platform.enforcement_policy import SessionEnforcementPolicy

logger = logging.getLogger(__name__)

_BACKEND_NAME = "linux-systemd-cgroup-v2"
_BACKEND_VERSION = "0.1"
_UNIT_PREFIX = "happyranch-session-"
_PROBE_UNIT_PREFIX = "happyranch-probe-"
# PID counts the kernel reports are always non-negative; a negative parseable
# value is semantically invalid and must be treated as absent (see
# ``_read_counters``) rather than trusted as authoritative evidence.
_NON_NEGATIVE_PID_COUNTERS = frozenset({"pids.current", "pids.peak"})
_ENFORCEMENT_PROPERTIES = (
    # 16M / 4 tasks / 10% CPU: small enough to prove the property applies,
    # large enough that the probe's own `sleep` pair is never OOM-killed
    # (a 1M limit reliably OOM-kills the probe scope itself).
    ("MemoryMax", "16M"),
    ("TasksMax", "4"),
    ("CPUQuota", "10%"),
)


def _kernel_max(*values: int | float | None) -> int | float | None:
    """Max of the valid kernel-counter observations (``None`` when none).

    The authoritative kernel counters are monotonic (``memory.peak`` /
    ``pids.peak`` are high-water marks, ``cpu.stat`` ``usage_usec`` is
    cumulative), so the max over the exit-watcher's capture and the
    finish-time pre-stop read is the best available KERNEL value.
    """
    valid = [v for v in values if v is not None]
    return max(valid) if valid else None


# Bounded grace for ``finish`` to wait for the exit-watcher's immutable
# observation when the contained process has already exited (the clean
# success / timeout paths where systemd collects the transient scope ~0.3–
# 0.6 ms after the exit-instant read). The watcher does a few microseconds
# of file reads after its exit notification wakes, so this bound is
# generous; a still-running process (user cancellation / daemon drain)
# never blocks on it.
_EXIT_CAPTURE_GRACE = 0.25

# Cadence of the exit-watcher's live ``pread`` of the authoritative kernel
# counter files while the contained process runs, used as the timeout of the
# ``pidfd`` poll between live reads. The exit-instant read (deterministic
# ``pidfd``/``waitid`` wake at process exit) is the authoritative capture;
# the last live read is only the honest fallback when that exit-instant read
# loses the race against systemd's collection of the transient scope (the
# cgroup directory can vanish within microseconds of the process exiting).
_EXIT_CAPTURE_POLL = 0.05


@dataclass(frozen=True)
class _KernelObservation:
    """Immutable authoritative kernel counters captured at the process-exit
    instant while the scope's cgroup was still valid.

    The transient scope's cgroup is collected by systemd milliseconds after
    the contained process exits — long before ``finish`` runs on a clean-
    success path — so the exit-watcher reads these counters in a thread
    woken by a deterministic exit notification (``pidfd`` poll / ``waitid``
    — no polling cadence for the exit itself) and carries the result
    through wait/reap and actual drain/cancellation/cleanup into the
    finish-time receipt. The three counter files are opened and read
    **independently**, so final-read validity is tracked **per counter**
    (``final_read_ok_memory`` / ``final_read_ok_cpu`` /
    ``final_read_ok_pids``): a counter whose exit-instant read succeeded
    holds the authoritative final total/peak; a counter whose exit-instant
    read lost the collection race retains only its last-live value while
    the scope was alive — a genuine kernel-sourced reading that may
    undercount the terminal window, never the authoritative final value.
    ``finish`` downgrades such a retained value (honest non-KERNEL
    provenance) and records a precise per-counter ``capture_final_read_lost``
    event instead of silently labeling it authoritative merely because
    another counter's final read succeeded.
    """

    captured_at: float
    memory_peak_bytes: int | None
    cpu_total_seconds: float | None
    process_peak: int | None
    final_read_ok_memory: bool = False
    final_read_ok_cpu: bool = False
    final_read_ok_pids: bool = False


@dataclass
class _LaunchState:
    """Per-session launch state captured at launch for finish-time checks."""

    unit: str
    cgroup: str
    root_pid: int
    started_at: float
    observation: _KernelObservation | None = None
    capture_done: threading.Event = field(default_factory=threading.Event)


class LinuxSystemdBackend:
    """Real systemd/cgroup-v2 :class:`SessionBackend`.

    All systemd interactions shell out to the OS-shipped ``systemd-run`` /
    ``systemctl`` (no new dependency) and are injectable for deterministic
    unit tests. Every capability in the report is backed by an actual
    operation the probe performed and verified on the daemon's host.
    """

    name = _BACKEND_NAME

    def __init__(
        self,
        *,
        systemd_run: str = "systemd-run",
        systemctl: str = "systemctl",
        cgroup_root: str | os.PathLike = "/sys/fs/cgroup",
        slice_name: str = "happyranch.slice",
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        proc_reader: LinuxProcReader | None = None,
        max_launch_wait: float = 2.0,
    ) -> None:
        self._systemd_run = systemd_run
        self._systemctl_bin = systemctl
        self._cgroup_root = os.fspath(cgroup_root)
        self._slice_name = slice_name
        self._monotonic = monotonic
        self._sleep = sleep
        self._reader = proc_reader or LinuxProcReader()
        self._census = ProcessTreeCensus(self._reader)
        self._max_launch_wait = max_launch_wait
        self._launched: dict[str, _LaunchState] = {}
        self._launched_lock = threading.Lock()

    # ── shell-outs (single-line argv; injectable) ──────────────────

    def _run(self, argv: list[str], timeout: float = 10.0) -> tuple[int, str]:
        """Run one systemd command; returns (returncode, stdout+stderr)."""
        try:
            proc = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            return proc.returncode, (proc.stdout or "") + (proc.stderr or "")
        except subprocess.TimeoutExpired:
            return -1, f"timeout after {timeout}s: {' '.join(argv)}"
        except OSError as exc:
            return -1, f"cannot execute {' '.join(argv)}: {exc}"

    def _systemctl(self, *args: str, timeout: float = 10.0) -> tuple[int, str]:
        return self._run([self._systemctl_bin, "--user", *args], timeout=timeout)

    def _systemd_run_scope(
        self,
        unit: str,
        slice_name: str,
        argv: tuple[str, ...],
        *,
        properties: tuple[tuple[str, str], ...] = (),
        spec_stdin,
        spec_stdout,
        spec_stderr,
        spec_text: bool,
        spec_cwd: str,
        spec_env,
    ) -> subprocess.Popen:
        cmd = [
            self._systemd_run,
            "--user",
            "--scope",
            f"--unit={unit}",
            f"--slice={slice_name}",
        ]
        for name, value in properties:
            cmd.append(f"--property={name}={value}")
        cmd.append("--")
        cmd.extend(argv)
        env = dict(os.environ)
        if spec_env:
            env.update(spec_env)
        return subprocess.Popen(
            cmd,
            cwd=spec_cwd or ".",
            env=env,
            stdin=spec_stdin,
            stdout=spec_stdout,
            stderr=spec_stderr,
            text=spec_text,
        )

    # ── capability probe (real operations, no residue) ─────────────

    def probe(self) -> CapabilityReport:
        """Operationally probe the daemon's actual environment.

        Bounded, leaves no residue, and reports only capabilities that were
        created, applied, and verified on this host."""
        started = self._monotonic()
        try:
            code, out = self._systemctl("is-system-running", timeout=10)
            if code not in (0, 1):
                return self._unhealthy(
                    started, f"systemd user manager unreachable ({code}: {out.strip()[:200]})"
                )
            controllers = self._cgroup_v2_controllers()
            if controllers is None:
                return self._unhealthy(started, "cgroup v2 not mounted/readable")
            missing = {"memory", "pids", "cpu"} - controllers
            if missing:
                return self._unhealthy(
                    started, f"cgroup v2 controllers missing: {sorted(missing)}"
                )
            caps, evidence = self._probe_scope_operations()
            if not caps:
                return self._unhealthy(started, evidence)
            return CapabilityReport(
                backend=_BACKEND_NAME,
                backend_version=_BACKEND_VERSION,
                capabilities=caps,
                evidence=evidence,
                probed_at=started,
                healthy=True,
            )
        except Exception as exc:  # noqa: BLE001 — probe must degrade honestly
            return self._unhealthy(started, f"probe failed: {exc}")

    def _unhealthy(self, started: float, reason: str) -> CapabilityReport:
        return CapabilityReport(
            backend=_BACKEND_NAME,
            backend_version=_BACKEND_VERSION,
            capabilities={},
            evidence=f"linux backend unhealthy: {reason}",
            reason=reason,
            probed_at=started,
            healthy=False,
        )

    def _cgroup_v2_controllers(self) -> set[str] | None:
        try:
            with open(
                os.path.join(self._cgroup_root, "cgroup.controllers"),
                encoding="utf-8",
            ) as fh:
                return {tok for tok in fh.read().split()}
        except OSError:
            return None

    def _probe_scope_operations(self) -> tuple[dict[Capability, CapabilityLevel], str]:
        """Create + verify + tear down one transient probe scope.

        The scope carries a main process AND a descendant (the mandatory
        whole-tree shape), tiny non-triggering limits, and is verified for:
        ControlGroup resolution, applied limit files, live membership, and
        authoritative counter readability — then explicitly stopped with the
        cgroup verified empty and the probe slice removed. Returns
        (capabilities, evidence); an empty capability dict means unhealthy."""
        probe_id = f"{_PROBE_UNIT_PREFIX}{os.getpid()}-{uuid.uuid4().hex[:8]}"
        unit = f"{probe_id}.scope"
        slice_name = f"{probe_id}.slice"
        proc: subprocess.Popen | None = None
        try:
            proc = self._systemd_run_scope(
                unit,
                slice_name,
                ("sh", "-c", "sleep 30 & exec sleep 30"),
                properties=_ENFORCEMENT_PROPERTIES,
                spec_stdin=subprocess.DEVNULL,
                spec_stdout=subprocess.DEVNULL,
                spec_stderr=subprocess.DEVNULL,
                spec_text=True,
                spec_cwd=".",
                spec_env=None,
            )
            cg = self._wait_for_cgroup(unit, proc)
            if cg is None:
                return {}, f"probe scope {unit} never materialized"
            applied = self._applied_limits(cg)
            counters = self._read_counters(cg)
            members_before = self._cgroup_members(cg)
            ok, teardown_evidence = self._teardown_and_verify(unit, cg, grace=3.0)
            # Remove the probe slice chain (leaf + intermediates), never the
            # aggregate HappyRanch slice.
            self._cleanup_probe_slice(slice_name)
            caps: dict[Capability, CapabilityLevel] = {}
            if all(
                applied.get(k) for k in ("memory.max", "pids.max", "cpu.max")
            ):
                caps[Capability.LIMITS_MEMORY] = CapabilityLevel.GUARANTEED
                caps[Capability.LIMITS_PIDS] = CapabilityLevel.GUARANTEED
                caps[Capability.LIMITS_CPU] = CapabilityLevel.GUARANTEED
            if ok and len(members_before) >= 2:
                caps[Capability.KILLS_TREE_GUARANTEED] = CapabilityLevel.GUARANTEED
            if "memory.peak" in counters:
                caps[Capability.REPORTS_MEMORY_PEAK] = CapabilityLevel.GUARANTEED
            elif counters.get("memory.current") is not None:
                caps[Capability.REPORTS_MEMORY_PEAK] = CapabilityLevel.BEST_EFFORT
            if counters.get("cpu.stat") is not None:
                caps[Capability.REPORTS_CPU_TOTAL] = CapabilityLevel.GUARANTEED
            if counters.get("pids.peak") is not None:
                caps[Capability.REPORTS_PROCESS_PEAK] = CapabilityLevel.GUARANTEED
            elif counters.get("pids.current") is not None:
                caps[Capability.REPORTS_PROCESS_PEAK] = CapabilityLevel.BEST_EFFORT
            evidence = (
                f"probe scope {unit}: cg={cg}, limits applied="
                f"{[k for k, v in applied.items() if v]}, counters="
                f"{sorted(counters)}, members_before={len(members_before)}, "
                f"teardown={teardown_evidence}"
            )
            return caps, evidence
        except Exception as exc:  # noqa: BLE001
            return {}, f"probe scope failed: {exc}"
        finally:
            if proc is not None:
                try:
                    proc.terminate()
                    proc.wait(timeout=2)
                except (ProcessLookupError, subprocess.TimeoutExpired, OSError):
                    pass
            # Best-effort residue removal even on a partial probe failure.
            self._systemctl("stop", unit, timeout=5)
            self._systemctl("reset-failed", unit, timeout=5)
            self._cleanup_probe_slice(slice_name)

    def _cleanup_probe_slice(self, slice_name: str) -> None:
        """Stop the probe slice and every intermediate parent slice.

        systemd derives parent slices by splitting the unit name on ``-``
        (``a-b.slice`` sits under ``a.slice``); the probe must remove its
        whole chain so a bounded probe leaves no residue, stopping at the
        aggregate HappyRanch slice."""
        if not slice_name.endswith(".slice"):
            return
        parts = slice_name[: -len(".slice")].split("-")
        for i in range(len(parts), 0, -1):
            name = "-".join(parts[:i]) + ".slice"
            if name == self._slice_name:
                break
            self._systemctl("stop", name, timeout=5)

    def _wait_for_cgroup(
        self, unit: str, proc: subprocess.Popen, timeout: float | None = None
    ) -> str | None:
        """Resolve the unit's cgroup path, waiting for the scope to exist.

        Reads the launched process's own cgroup membership
        (``/proc/<pid>/cgroup``) — the direct membership proof — up to
        *timeout* (default ``max_launch_wait``). Returns the cgroup path
        relative to the cgroup root, or ``None`` if the scope never
        materialized."""
        timeout = self._max_launch_wait if timeout is None else timeout
        deadline = self._monotonic() + timeout
        while self._monotonic() < deadline:
            if proc.poll() is not None:
                return None  # process exited — no live scope to resolve
            cg = self._proc_cgroup(proc.pid)
            if cg is not None and unit in cg:
                return cg
            self._sleep(0.05)
        return None

    def _proc_cgroup(self, pid: int) -> str | None:
        """The launched process's cgroup path (relative to the cgroup root)."""
        try:
            with open(f"/proc/{pid}/cgroup", encoding="utf-8") as fh:
                line = fh.read().strip()
        except OSError:
            return None
        # Format: "0::/path" (cgroup v2) or "hierarchy:path:controllers" (v1).
        parts = line.split(":", 2)
        if len(parts) == 3 and parts[2]:
            return parts[2]
        return None

    def _control_group_of(self, unit: str) -> str | None:
        code, out = self._systemctl("show", "-p", "ControlGroup", "--value", unit)
        cg = out.strip()
        return cg or None if code == 0 else None

    def _applied_limits(self, cg: str) -> dict[str, bool]:
        """Verify the enforcement property files hold applied values.

        ``MemoryMax=16M`` -> ``memory.max == 16777216``; ``TasksMax=4`` ->
        ``pids.max == 4``; ``CPUQuota=10%`` -> ``cpu.max == "10000 100000"``.
        """
        expected = {
            "memory.max": "16777216",
            "pids.max": "4",
            "cpu.max": "10000 100000",
        }
        applied: dict[str, bool] = {}
        for name, want in expected.items():
            got = self._read_file(cg, name)
            applied[name] = got is not None and got.split()[0] == want.split()[0]
        return applied

    def _session_limits_applied(
        self, cg: str, policy: "SessionEnforcementPolicy"
    ) -> tuple[bool, str]:
        """Verify the session enforcement envelope is applied to *cg*.

        Real-session Slice C check: the scope's cgroup files must hold the
        exact per-invocation envelope resolved from the ``AdmissionRequest``
        ``invocation_kind`` — ``memory.high`` (MemoryHigh soft throttle),
        ``memory.max`` (MemoryMax hard ceiling) and ``pids.max``
        (TasksMax). Byte-for-byte comparison; any mismatch is a containment
        failure (fail-closed). ``CPUQuota`` is deliberately never emitted
        for real sessions, so ``cpu.max`` is not part of this verification.
        """
        expected = {
            "memory.high": str(policy.memory_high_bytes),
            "memory.max": str(policy.memory_max_bytes),
            "pids.max": str(policy.tasks_max),
        }
        missing: list[str] = []
        for name, want in expected.items():
            got = self._read_file(cg, name)
            if got is None or got.split()[0] != want:
                missing.append(f"{name}={got!r} (want {want})")
        if missing:
            return False, "; ".join(missing)
        return True, "envelope-applied"

    def _read_file(self, cg: str, name: str) -> str | None:
        try:
            with open(
                os.path.join(self._cgroup_root, cg.lstrip("/"), name),
                encoding="utf-8",
            ) as fh:
                return fh.read().strip()
        except OSError:
            return None

    def _read_counters(self, cg: str) -> dict[str, object]:
        """Authoritative cgroup counters for *cg* (empty when unreadable).

        Semantically invalid negative PID counts are treated as absent: the
        kernel never reports a negative membership/peak, and a negative value
        must never masquerade as authoritative KERNEL evidence (a
        ``pids.peak`` of ``-1`` must not earn KERNEL provenance nor a
        ``pids.current`` of ``-1`` contaminate the best-effort fallback).
        """
        counters: dict[str, object] = {}
        for name in (
            "memory.current",
            "memory.peak",
            "pids.current",
            "pids.peak",
            "pids.max",
        ):
            raw = self._read_file(cg, name)
            if raw is None:
                continue
            try:
                value = int(raw)
            except ValueError:
                continue
            if name in _NON_NEGATIVE_PID_COUNTERS and value < 0:
                continue
            counters[name] = value
        stat = self._read_file(cg, "cpu.stat")
        if stat is not None:
            usage = None
            for line in stat.splitlines():
                key, _, value = line.partition(" ")
                if key == "usage_usec":
                    usage = int(value)
                    break
            if usage is not None:
                counters["cpu.stat"] = usage / 1_000_000.0
        return counters

    def _cgroup_dir_exists(self, cg: str) -> bool:
        """True while the cgroup directory is still materialized on disk."""
        return os.path.isdir(os.path.join(self._cgroup_root, cg.lstrip("/")))

    def _cgroup_members(self, cg: str) -> dict[int, str] | None:
        """Current cgroup members: pid -> start identity (identity-safe).

        Returns ``None`` when the cgroup still exists but its membership
        cannot be READ (``cgroup.procs`` unreadable) — an unverifiable
        membership is never treated as empty (fail-closed). A fully removed
        cgroup (the scope was stopped) is genuinely empty.

        Unreaped zombies are excluded: a zombie is already dead and answers
        no signal — it must never be reported as surviving residue."""
        if not self._cgroup_dir_exists(cg):
            return {}
        raw = self._read_file(cg, "cgroup.procs")
        if raw is None:
            return None
        members: dict[int, str] = {}
        for tok in raw.split():
            try:
                pid = int(tok)
            except ValueError:
                continue
            observation = self._census.reader().read_process(pid)
            if observation is None or is_zombie(observation.state):
                continue
            if observation.start_identity is not None:
                members[pid] = observation.start_identity
        return members

    # ── lifecycle ─────────────────────────────────────────────────

    def prepare(self, request, policy) -> PendingHandle:
        """Reserve the per-session scope unit name; no scope exists yet.

        Carries the bounded receipt attribution (``invocation_kind`` /
        ``executor_profile``) from the ``AdmissionRequest`` onto the pending
        handle so ``launch`` can resolve the immutable per-invocation
        enforcement envelope and the eventual ``Receipt`` is attributed
        honestly (THR-207 Slice C).
        """
        if not request.logical_id:
            raise BackendPrepareError("logical_id is required")
        unit = f"{_UNIT_PREFIX}{request.logical_id}-{uuid.uuid4().hex[:8]}.scope"
        return PendingHandle(
            backend=_BACKEND_NAME,
            token=unit,
            request_id=request.logical_id,
            invocation_kind=request.invocation_kind,
            executor_profile=request.executor_profile,
        )

    def launch(self, pending: PendingHandle, spec) -> RunningHandle:
        """Launch *spec.argv* into the per-session scope and verify it landed.

        ``systemd-run --user --scope`` execs the target inside the transient
        scope (blocking stdio/PID model), so the returned handle's
        ``process`` is the contained target; membership is verified by
        reading the process's own cgroup before the handle is returned.

        **Slice C enforcement (THR-207):** real session scopes emit the
        immutable per-invocation envelope resolved from
        ``pending.invocation_kind`` — exact ``MemoryHigh`` / ``MemoryMax`` /
        ``TasksMax`` properties (never ``CPUQuota``). After the scope
        materializes, the applied cgroup files (``memory.high`` /
        ``memory.max`` / ``pids.max``) are verified byte-for-byte against the
        envelope; a mismatch raises :class:`BackendLaunchError` (fail-closed:
        a scope that claims guaranteed limits but did not apply them is not
        containment). The probe's tiny probe-only limit values are untouched
        and never confused with this session policy.
        """
        unit = pending.token
        policy = enforcement_policy_for(pending.invocation_kind)
        properties = policy.systemd_properties()
        try:
            proc = self._systemd_run_scope(
                unit,
                self._slice_name,
                tuple(spec.argv),
                properties=properties,
                spec_stdin=spec.stdin,
                spec_stdout=spec.stdout,
                spec_stderr=spec.stderr,
                spec_text=spec.text,
                spec_cwd=spec.cwd,
                spec_env=spec.env,
            )
        except OSError as exc:
            raise BackendLaunchError(
                f"cannot start systemd-run for scope {unit}: {exc}"
            ) from exc
        cg = self._wait_for_cgroup(unit, proc)
        if cg is None:
            # The scope never materialized while the process is still alive.
            if proc.poll() is None:
                self._safe_stop(unit)
                proc.kill()
                raise BackendLaunchError(f"scope {unit} was not created")
            # The process ran and exited before we could resolve the scope —
            # finish() will observe the unit gone and report CLEAN.
            cg = ""
        if cg:
            # Slice C: verify the enforcement envelope was actually applied to
            # the scope's cgroup — a guaranteed limit that did not land is a
            # containment failure, never a silent best-effort claim.
            ok, evidence = self._session_limits_applied(cg, policy)
            if not ok:
                self._safe_stop(unit)
                proc.kill()
                raise BackendLaunchError(
                    f"scope {unit} did not apply the session enforcement "
                    f"envelope ({evidence})"
                )
        root_pid = proc.pid
        identity = self._census.start_identity(root_pid) or ""
        state = _LaunchState(unit=unit, cgroup=cg, root_pid=root_pid, started_at=self._monotonic())
        with self._launched_lock:
            self._launched[unit] = state
        # The exit-watcher captures authoritative kernel counters at the
        # process-exit instant, while the scope's cgroup is still valid.
        self._start_exit_capture(state, proc)
        return RunningHandle(
            backend=_BACKEND_NAME,
            token=unit,
            request_id=pending.request_id,
            root_pid=root_pid,
            start_identity=identity,
            process=proc,
            invocation_kind=pending.invocation_kind,
            executor_profile=pending.executor_profile,
        )

    def _start_exit_capture(
        self, state: _LaunchState, proc: subprocess.Popen
    ) -> None:
        """Spawn the per-session exit-watcher thread.

        **Deterministic exit notification** (not polling): the watcher opens
        the authoritative kernel counter files (``memory.peak`` /
        ``cpu.stat`` ``usage_usec`` / ``pids.peak``) while the scope's
        cgroup is valid — each file opened **independently**, so an absent
        old-kernel ``pids.peak`` disables only that counter and never
        discards the guaranteed memory/CPU capture — then blocks on a
        ``pidfd`` poll (``select.poll``), which the kernel makes readable at
        the exact process-exit instant (part of the child-exit path),
        unlike ``proc.poll()`` which is only sampled every
        ``_EXIT_CAPTURE_POLL``. At wake it immediately preads the final
        counters: live evidence shows systemd collects the transient scope
        ~0.27–0.62 ms after the contained process exits while the three
        preads take ~10–30 us on the open fds, so the exit-instant read
        captures the authoritative final kernel counters **while the scope
        is alive** and the receipt includes terminal-window CPU/peak growth
        that any last-live poll would miss. The live pread on the poll
        timeout (every ``_EXIT_CAPTURE_POLL`` while the process runs)
        remains the honest fallback: on the rare path where collection
        beats the exit-instant read, that counter's final-read validity
        flag stays False and ``finish`` downgrades its retained last-live
        value (honest non-KERNEL provenance) and records a precise
        per-counter ``capture_final_read_lost`` enforcement event instead
        of silently labeling a possibly-stale last-live read as the
        authoritative final total/peak. The immutable observation (max of
        the final read and the last live read — the kernel counters are
        monotonic high-water/cumulative) is written on the launch state by
        reference, so it survives ``finish`` popping the state from the
        registry. A watcher failure is contained: it records nothing and
        the receipt degrades to the honest fallback chain.
        """
        cg = state.cgroup
        if not cg:
            # The scope never materialized (or already exited before launch
            # resolved it) — there is no cgroup to capture while alive.
            return
        pid = proc.pid

        def watch() -> None:
            pidfd: int | None = None
            fds: dict[str, int] = {}
            try:
                fds = self._open_observation_fds(cg)
                if not fds:
                    # Every counter file is already gone (the cgroup was
                    # collected between launch and now) — nothing to capture.
                    return
                # Baseline live read while the cgroup is guaranteed valid.
                last: dict[str, object] = self._pread_observation(fds)
                try:
                    # Deterministic exit notification: the kernel wakes this
                    # thread the moment the contained process exits.
                    pidfd = os.pidfd_open(pid)
                except OSError:
                    pidfd = None
                if pidfd is not None:
                    poller = select.poll()
                    poller.register(pidfd, select.POLLIN)
                    while True:
                        # Blocks up to the live-read cadence; returns
                        # immediately (non-empty) when the process exits.
                        if poller.poll(int(_EXIT_CAPTURE_POLL * 1000)):
                            break
                        current = self._pread_observation(fds)
                        last = {
                            name: _kernel_max(
                                current.get(name), last.get(name)
                            )
                            for name in fds
                        }
                else:
                    # Defensive fallback for a kernel without pidfd (every
                    # kernel exposing memory.peak is >= 5.19 and has pidfd):
                    # block on waitid — still a deterministic exit
                    # notification (WNOWAIT does not reap, so the executor's
                    # own communicate()/wait() still reaps normally).
                    try:
                        os.waitid(os.P_PID, pid, os.WEXITED | os.WNOWAIT)
                    except ChildProcessError:
                        pass  # already reaped — the process has exited
                # The process has exited: one final read while the cgroup
                # still exists (fast pread on the open fds wins the
                # collection race: measured ~0.27–0.62 ms of cgroup
                # lifetime after exit vs ~10–30 us for the reads). ``last``
                # remains the honest fallback when collection already won.
                final = self._pread_observation(fds)
                # Final-read validity is per counter: a counter whose
                # exit-instant read succeeded holds the authoritative final
                # total/peak; one that lost the collection race keeps only
                # its last-live value (downgraded honestly by ``finish``,
                # never silently KERNEL because another counter succeeded).
                final_read_ok_memory = final.get("memory.peak") is not None
                final_read_ok_cpu = final.get("cpu.stat") is not None
                final_read_ok_pids = final.get("pids.peak") is not None
            except Exception:  # noqa: BLE001 — watcher failure is contained
                return
            finally:
                for fd in fds.values():
                    try:
                        os.close(fd)
                    except OSError:
                        pass
                if pidfd is not None:
                    try:
                        os.close(pidfd)
                    except OSError:
                        pass
            observation = _KernelObservation(
                captured_at=self._monotonic(),
                memory_peak_bytes=_kernel_max(
                    final.get("memory.peak"), last.get("memory.peak")
                ),
                cpu_total_seconds=_kernel_max(
                    final.get("cpu.stat"), last.get("cpu.stat")
                ),
                process_peak=_kernel_max(
                    final.get("pids.peak"), last.get("pids.peak")
                ),
                final_read_ok_memory=final_read_ok_memory,
                final_read_ok_cpu=final_read_ok_cpu,
                final_read_ok_pids=final_read_ok_pids,
            )
            with self._launched_lock:
                state.observation = observation
                state.capture_done.set()

        threading.Thread(
            target=watch, name=f"hr-exit-capture-{state.unit}", daemon=True
        ).start()

    def _open_observation_fds(self, cg: str) -> dict[str, int]:
        """Open the authoritative kernel counter files while the cgroup is
        valid, each **independently**.

        An absent file (old-kernel ``pids.peak`` shape, or a counter the
        host's controllers do not expose) disables only that counter — the
        guaranteed ``memory.peak`` / ``cpu.stat`` capture is never discarded
        because one optional file is missing, and a missing file never
        invents provenance. Returns a (possibly empty) map of opened counter
        fds; an empty map means the cgroup is already gone (or no counter
        is readable) and the watcher records nothing, degrading honestly to
        the fallback chain."""
        base = os.path.join(self._cgroup_root, cg.lstrip("/"))
        fds: dict[str, int] = {}
        for name in ("memory.peak", "cpu.stat", "pids.peak"):
            try:
                fds[name] = os.open(os.path.join(base, name), os.O_RDONLY)
            except OSError:
                # Absent file (old-kernel shape) or the cgroup was already
                # collected: leave this counter unopened — the rest still
                # capture.
                continue
        return fds

    def _pread_observation(self, fds: dict[str, int]) -> dict[str, object]:
        """One fast kernel-counter read via pre-opened fds (``pread`` avoids
        the slow per-file ``open`` that would lose the collection race).
        Returns ``{memory.peak, cpu.stat, pids.peak}`` with ``None`` for
        files that vanished (ENODEV after cgroup collection) or carried a
        semantically invalid negative PID count."""
        result: dict[str, object] = {}
        for name, fd in fds.items():
            try:
                os.lseek(fd, 0, os.SEEK_SET)
                raw = os.read(fd, 256)
                if name == "cpu.stat":
                    usage = None
                    for line in raw.decode("utf-8", "replace").splitlines():
                        key, _, value = line.partition(" ")
                        if key == "usage_usec":
                            usage = int(value) / 1_000_000.0
                            break
                    result[name] = usage
                else:
                    value = int(raw.split()[0])
                    # The kernel never reports a negative membership/peak; a
                    # negative parseable value is semantically invalid and
                    # must never earn authoritative KERNEL evidence.
                    result[name] = value if value >= 0 else None
            except OSError:
                result[name] = None
            except (ValueError, IndexError):
                result[name] = None
        return result

    def _await_exit_capture(
        self, state: _LaunchState, running: RunningHandle
    ) -> _KernelObservation | None:
        """Bounded wait for the exit-watcher's immutable observation.

        Waits only when the contained process has already exited (the clean
        success / timeout / post-kill paths where systemd is about to
        collect the scope) and only up to ``_EXIT_CAPTURE_GRACE``; a
        still-running process (user cancellation / daemon drain) never
        blocks — ``finish``'s own pre-stop read is authoritative in that
        window. Returns the observation (possibly all-None when the watcher
        lost the cgroup race) or ``None`` when none exists yet.
        """
        proc = running.process
        if (
            state.cgroup
            and proc is not None
            and proc.poll() is not None
            and state.observation is None
        ):
            deadline = self._monotonic() + _EXIT_CAPTURE_GRACE
            while state.observation is None and self._monotonic() < deadline:
                if state.capture_done.wait(0.005):
                    break
        return state.observation

    def sample(self, running: RunningHandle) -> ResourceSample:
        """One authoritative cgroup-counter sample (kernel provenance)."""
        with self._launched_lock:
            state = self._launched.get(running.token)
        cg = state.cgroup if state else ""
        if not cg:
            return ResourceSample(
                sampled_at=self._monotonic(),
                provenance=MeasurementProvenance.UNAVAILABLE,
            )
        counters = self._read_counters(cg)
        return ResourceSample(
            sampled_at=self._monotonic(),
            memory_peak_bytes=(
                counters["memory.peak"]
                if "memory.peak" in counters
                else (counters["memory.current"] if "memory.current" in counters else None)
            ),
            cpu_total_seconds=counters.get("cpu.stat"),
            process_count=counters.get("pids.current"),
            provenance=MeasurementProvenance.KERNEL,
        )

    def finish(
        self,
        running: RunningHandle,
        terminal_reason: str,
        grace_seconds: float,
        samples: tuple[ResourceSample, ...] | None = None,
        sample_prefix_gap: float = 0.0,
    ) -> Receipt:
        """Explicit whole-scope stop on EVERY terminal path, then verify.

        Ordering: await the exit-watcher's immutable kernel observation and
        read authoritative counters (the cgroup disappears on stop — and on
        a clean-success path systemd collects the transient scope ~0.3–0.6
        ms after the contained process exits, before this method runs, so the
        exit-watcher's exit-instant capture while the scope was alive is the
        primary authoritative source) -> explicit ``systemctl stop`` -> bounded wait
        for **cgroup emptiness** (the unit can report ``inactive`` while a
        TERM-resistant member still lives in its cgroup — the control-group
        kill model: merely observing the main PID exit is insufficient) ->
        escalate to ``KILL`` when members remain -> verify cgroup emptiness
        -> identity-verified survivors (guaranteed-cleanup residue,
        admission-blocking) -> bounded receipt.

        Fail-closed: an unreadable ``cgroup.procs`` or an errored unit-state
        interrogation is UNKNOWN evidence — it can never yield ``CLEAN`` or
        ``quiescent`` (the receipt stays ``INCOMPLETE`` with explicit
        ``cgroup_procs_unreadable`` evidence so admission blocks). A cgroup
        that has **vanished** (scope collected after exit) is genuine
        emptiness corroborated by a positively-terminal unit state and is
        recorded as an explicit ``cgroup_vanished`` event — it never
        short-circuits to a silently-verified ``CLEAN`` and never fabricates
        kernel measurement from ``pids.current`` or a sampled fallback."""
        samples = tuple(samples or ())
        unit = running.token
        with self._launched_lock:
            state = self._launched.pop(unit, None)
        started = self._monotonic()
        if state is None:
            state = _LaunchState(unit=unit, cgroup="", root_pid=running.root_pid, started_at=started)
        cg = state.cgroup
        if not cg:
            cg = self._control_group_of(unit) or ""
        # 0. The exit-watcher captured authoritative kernel counters at the
        #    process-exit instant while the scope's cgroup was still valid
        #    (a deterministic pidfd/waitid exit notification wakes it at the
        #    exit itself, and systemd collects the transient scope ~0.3–0.6
        #    ms later — long before this finish-time read on a clean-success
        #    path). Wait a bounded moment for it when the process has already
        #    exited; a still-running process (cancel/timeout/drain path) has
        #    no exit capture yet and this method's own pre-stop read below is
        #    authoritative instead.
        observation = self._await_exit_capture(state, running)
        # 1. Authoritative counters BEFORE teardown. On a natural-exit path
        #    the cgroup is usually already collected here (the observation
        #    above is the primary source); on a cancel/timeout/drain path
        #    the process is still running and this read succeeds.
        counters = self._read_counters(cg) if cg else {}
        # Whether the scope's cgroup is already gone at finish time: genuine
        # emptiness (the transient scope was collected after the tree
        # exited) that must be recorded as explicit evidence, never silently
        # treated as a verified read.
        cgroup_vanished = bool(cg) and not self._cgroup_dir_exists(cg)
        # 2. Explicit stop — clean success included.
        self._systemctl("stop", unit, timeout=max(grace_seconds + 1.0, 2.0))
        # 3. Quiescence = cgroup empty (unit state is not authoritative:
        #    the scope can reach ``inactive`` while members still live in
        #    its cgroup after a TERM they ignored).
        members = self._cgroup_members(cg) if cg else {}
        members_unknown = members is None
        members_present = bool(members) or members_unknown
        deadline = started + grace_seconds
        escalated = False
        while members_present and self._monotonic() < deadline:
            self._sleep(0.05)
            members = self._cgroup_members(cg) if cg else {}
            members_unknown = members is None
            members_present = bool(members) or members_unknown
        if members_present:
            # 4. Escalate to KILL, bounded.
            escalated = True
            self._systemctl("kill", "--kill-who=all", "--signal=KILL", unit, timeout=5)
            self._systemctl("stop", unit, timeout=5)
            kill_deadline = self._monotonic() + min(grace_seconds, 2.0)
            while self._monotonic() < kill_deadline:
                members = self._cgroup_members(cg) if cg else {}
                if not members and members is not None:
                    break
                self._sleep(0.05)
            else:
                members = self._cgroup_members(cg) if cg else {}
            members_unknown = members is None
            members_present = bool(members) or members_unknown
        # 5. Identity-safe survivors (guaranteed-cleanup residue).
        survivors = tuple(
            SurvivorRecord(
                pid=pid,
                start_identity=ident,
                backend=_BACKEND_NAME,
                discovered_at=started,
                last_seen_at=started,
            )
            for pid, ident in sorted((members or {}).items())
        )
        active = self._unit_active(unit)
        quiescent = not members_present and not active
        cleanup = (
            CleanupStatus.INCOMPLETE
            if not quiescent
            else (CleanupStatus.KILL if escalated else CleanupStatus.CLEAN)
        )
        # Cleanup evidence is never silently inferred: an unreadable
        # cgroup is UNKNOWN (fail-closed); a vanished cgroup is genuine
        # emptiness only when corroborated by a positively-terminal unit
        # state above, and is recorded explicitly so the receipt never
        # presents an unverified ``CLEAN`` or a fabricated measurement.
        enforcement_events = ()
        if members_unknown:
            enforcement_events = ("cgroup_procs_unreadable",)
        elif cgroup_vanished:
            enforcement_events = ("cgroup_vanished",)
        # Measurement honesty, per counter: the exit-watcher tracks
        # final-read validity independently for ``memory.peak``,
        # ``cpu.stat`` and ``pids.peak``. A counter whose exit-instant read
        # lost the collection race retains only its last-live value while
        # the scope was alive — a genuine KERNEL-sourced reading that may
        # undercount the final CPU/peak window. That retained value is
        # NEVER published as the authoritative final total/peak merely
        # because another counter's final read succeeded: it is downgraded
        # to the sampled-evidence path below and the receipt records a
        # precise per-counter ``capture_final_read_lost`` event naming every
        # affected counter.
        lost_final_reads: list[str] = []
        if observation is not None:
            for counter, ok, retained in (
                ("memory.peak", observation.final_read_ok_memory, observation.memory_peak_bytes),
                ("cpu.stat", observation.final_read_ok_cpu, observation.cpu_total_seconds),
                ("pids.peak", observation.final_read_ok_pids, observation.process_peak),
            ):
                if not ok and retained is not None:
                    lost_final_reads.append(counter)
        if lost_final_reads:
            enforcement_events = enforcement_events + (
                "capture_final_read_lost:" + ",".join(lost_final_reads),
            )
        memory_peak, cpu_total, process_peak = merge_sample_peaks(samples)
        # A counter whose exit-instant final read failed keeps only its
        # last-live value (a point-in-time kernel-sourced reading): fold it
        # into the sampled-evidence path so it is published with honest
        # non-KERNEL provenance instead of being silently labeled the
        # authoritative final total/peak.
        if observation is not None:
            if not observation.final_read_ok_memory and observation.memory_peak_bytes is not None:
                memory_peak = _kernel_max(memory_peak, observation.memory_peak_bytes)
            if not observation.final_read_ok_cpu and observation.cpu_total_seconds is not None:
                cpu_total = _kernel_max(cpu_total, observation.cpu_total_seconds)
            if not observation.final_read_ok_pids and observation.process_peak is not None:
                process_peak = _kernel_max(process_peak, observation.process_peak)
        # Authoritative kernel sources, newest first, gated per counter on
        # the exit-instant read having succeeded: the exit-watcher's
        # observation value (captured at the process-exit instant while the
        # cgroup was valid) and this finish-time pre-stop read. The kernel
        # counters are monotonic (high-water peak / cumulative CPU), so the
        # max over the valid reads is the best available — both are KERNEL
        # provenance; ``pids.current`` or a sampled value is never
        # relabeled authoritative, and a retained last-live value whose
        # final read failed never earns KERNEL provenance.
        memory_peak_kernel = _kernel_max(
            observation.memory_peak_bytes
            if observation is not None and observation.final_read_ok_memory
            else None,
            counters["memory.peak"] if "memory.peak" in counters else None,
        )
        cpu_total_kernel = _kernel_max(
            observation.cpu_total_seconds
            if observation is not None and observation.final_read_ok_cpu
            else None,
            counters.get("cpu.stat"),
        )
        process_peak_kernel = _kernel_max(
            observation.process_peak
            if observation is not None and observation.final_read_ok_pids
            else None,
            counters.get("pids.peak"),
        )
        process_now = counters.get("pids.current")
        # Deterministic process-peak precedence (an authoritative zero must
        # never be fabricated from an empty-tree teardown pids.current):
        #   1. a valid kernel ``pids.peak`` (the exit-watcher's capture or
        #      the pre-stop read above, both while the cgroup was valid) is
        #      the authoritative peak — KERNEL provenance;
        #   2. absent/invalid ``pids.peak`` with sampled evidence: the
        #      sampled peak merged honestly with the best-effort live
        #      ``pids.current`` (max — a live count can only be a lower
        #      bound on the true peak) — SAMPLED provenance;
        #   3. absent/invalid ``pids.peak`` with ONLY ``pids.current``: the
        #      live count is an explicit best-effort fallback — SAMPLED
        #      provenance, never KERNEL;
        #   4. no usable evidence at all: UNAVAILABLE (None), never a
        #      fabricated 0.
        if process_peak_kernel is not None:
            process_peak_value = process_peak_kernel
            process_peak_provenance = MeasurementProvenance.KERNEL
        elif process_peak is not None:
            merged = process_peak
            if process_now is not None:
                merged = max(merged, process_now)
            process_peak_value = merged
            process_peak_provenance = MeasurementProvenance.SAMPLED
        elif process_now is not None:
            process_peak_value = process_now
            process_peak_provenance = MeasurementProvenance.SAMPLED
        else:
            process_peak_value = None
            process_peak_provenance = MeasurementProvenance.UNAVAILABLE
        gaps = sample_gaps(samples)
        if sample_prefix_gap:
            gaps = (round(sample_prefix_gap, 6),) + gaps
        return Receipt(
            backend=_BACKEND_NAME,
            terminal_reason=terminal_reason,
            cleanup_status=cleanup,
            cleanup_duration_seconds=self._monotonic() - started,
            quiescent=quiescent,
            wall_time_seconds=self._monotonic() - state.started_at,
            memory_peak_bytes=memory_peak_kernel if memory_peak_kernel is not None else memory_peak,
            memory_peak_provenance=(
                MeasurementProvenance.KERNEL
                if memory_peak_kernel is not None
                else (
                    MeasurementProvenance.SAMPLED
                    if memory_peak is not None
                    else MeasurementProvenance.UNAVAILABLE
                )
            ),
            cpu_total_seconds=cpu_total_kernel if cpu_total_kernel is not None else cpu_total,
            cpu_total_provenance=(
                MeasurementProvenance.KERNEL
                if cpu_total_kernel is not None
                else (
                    MeasurementProvenance.SAMPLED
                    if cpu_total is not None
                    else MeasurementProvenance.UNAVAILABLE
                )
            ),
            process_peak=process_peak_value,
            process_peak_provenance=process_peak_provenance,
            sample_gaps=gaps,
            enforcement_events=enforcement_events,
            survivors=survivors,
            invocation_kind=running.invocation_kind,
            executor_profile=running.executor_profile,
        )

    def abandon(self, pending: PendingHandle) -> None:
        """Close a prepared-but-never-launched handle.

        No scope exists for a prepared-only handle (the unit is created at
        launch), but a failed launch may have left a partial scope — stop it
        best-effort and drop any recorded state."""
        unit = pending.token
        with self._launched_lock:
            self._launched.pop(unit, None)
        self._safe_stop(unit)

    def recover(self, handle_token: str) -> RecoveryResult:
        """Observational daemon-crash recovery.

        The daemon is not a supervised systemd unit (a declared gap), so
        true crash-time cleanup needs the separate service-placement ruling.
        Slice B enumerates HappyRanch-owned scopes observationally for
        operator reconciliation and never auto-kills an ambiguous unit."""
        code, out = self._systemctl(
            "list-units", "--all", "--type=scope", "--plain", "--no-legend", timeout=10
        )
        owned = [
            line.split()[0]
            for line in out.splitlines()
            if line.strip().startswith(_UNIT_PREFIX) or line.strip().startswith(_PROBE_UNIT_PREFIX)
        ]
        if owned:
            return RecoveryResult(
                recovered=False,
                evidence=(
                    f"{len(owned)} HappyRanch-owned scope(s) observed: "
                    f"{', '.join(owned[:10])}; daemon-crash reaping requires the "
                    "supervised-service ruling (declared gap)"
                ),
            )
        return RecoveryResult(
            recovered=False,
            evidence="no HappyRanch-owned scopes observed; "
            "daemon-crash cleanup is not guaranteed on this backend",
        )

    # ── internals ─────────────────────────────────────────────────

    def _unit_active(self, unit: str) -> bool:
        """True while quiescence is NOT positively proven (fail-closed).

        Only POSITIVE terminal evidence (``inactive``/``failed``/``dead``/
        ``not-found`` state) yields False. An interrogation error (bad rc,
        timeout, empty output) leaves the unit state UNKNOWN, which is
        treated as still-active — an unknown unit state must never permit a
        CLEAN/quiescent claim."""
        code, out = self._systemctl("is-active", unit, timeout=5)
        state = out.strip()
        if state in ("inactive", "failed", "dead", "not-found"):
            return False
        return True

    def _safe_stop(self, unit: str) -> None:
        code, out = self._systemctl("stop", unit, timeout=5)
        if code not in (0, 1, 3):  # 0 stopped, 1 not-loaded-ish, 3 inactive
            logger.warning("systemctl stop %s: rc=%s %s", unit, code, out.strip()[:200])

    def _teardown_and_verify(self, unit: str, cg: str, grace: float) -> tuple[bool, str]:
        """Probe helper: stop the unit, verify cgroup emptiness, remove it."""
        self._systemctl("stop", unit, timeout=grace + 1)
        deadline = self._monotonic() + grace
        while self._unit_active(unit) and self._monotonic() < deadline:
            self._sleep(0.05)
        if self._unit_active(unit):
            self._systemctl("kill", "--kill-who=all", "--signal=KILL", unit, timeout=5)
            self._systemctl("stop", unit, timeout=5)
        members = self._cgroup_members(cg) if cg else {}
        if members is None:
            return False, "cgroup.procs unreadable"
        ok = not self._unit_active(unit) and not members
        return ok, "cgroup-empty" if ok else f"residue={sorted(members)}"


# Keep the Protocol check honest at import time.
def _protocol_check() -> None:
    assert isinstance(LinuxSystemdBackend(), SessionBackend)


_protocol_check()
