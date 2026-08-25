"""Real Linux systemd/cgroup-v2 session backend (THR-207 Slice B).

Implements the :class:`SessionBackend` protocol with **real systemd/cgroup
operations** — no OS/version inference:

* ``probe`` creates a harmless transient scope with tiny non-triggering
  limits (``MemoryMax``/``TasksMax``/``CPUQuota``), verifies the unit's
  ``ControlGroup``, the applied limit files (``memory.max``/``pids.max``/
  ``cpu.max``), live membership, and the authoritative counters
  (``memory.current``/``memory.peak``/``pids.current``/``cpu.stat``), then
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
* Counters are read **before** teardown (the cgroup disappears once the
  scope is stopped): ``memory.peak`` (authoritative peak when the kernel
  exposes it), ``cpu.stat`` ``usage_usec`` (authoritative cumulative CPU),
  and ``pids.current`` (exact live membership count; the peak over samples
  is reported with ``sampled`` provenance because no kernel peak counter
  exists for pids).

Slice B applies **no resource limits** to session scopes (the policy
snapshot carries no approved limit values — 16/64/72/4096/2800 remain
unapproved measurement candidates); the probe proves the enforcement
machinery itself works, and lifecycle containment (launch inside the scope,
whole-tree stop, emptiness verification) is what Slice B wires.
"""

from __future__ import annotations

import logging
import os
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Callable

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

logger = logging.getLogger(__name__)

_BACKEND_NAME = "linux-systemd-cgroup-v2"
_BACKEND_VERSION = "0.1"
_UNIT_PREFIX = "happyranch-session-"
_PROBE_UNIT_PREFIX = "happyranch-probe-"
_ENFORCEMENT_PROPERTIES = (
    # 16M / 4 tasks / 10% CPU: small enough to prove the property applies,
    # large enough that the probe's own `sleep` pair is never OOM-killed
    # (a 1M limit reliably OOM-kills the probe scope itself).
    ("MemoryMax", "16M"),
    ("TasksMax", "4"),
    ("CPUQuota", "10%"),
)


@dataclass
class _LaunchState:
    """Per-session launch state captured at launch for finish-time checks."""

    unit: str
    cgroup: str
    root_pid: int
    started_at: float


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
            if counters.get("pids.current") is not None:
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
        """Authoritative cgroup counters for *cg* (empty when unreadable)."""
        counters: dict[str, object] = {}
        for name in ("memory.current", "memory.peak", "pids.current", "pids.max"):
            raw = self._read_file(cg, name)
            if raw is not None:
                try:
                    counters[name] = int(raw)
                except ValueError:
                    pass
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

    def _cgroup_members(self, cg: str) -> dict[int, str]:
        """Current cgroup members: pid -> start identity (identity-safe).

        Unreaped zombies are excluded: a zombie is already dead and answers
        no signal — it must never be reported as surviving residue."""
        raw = self._read_file(cg, "cgroup.procs")
        members: dict[int, str] = {}
        if raw is None:
            return members
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
        """Reserve the per-session scope unit name; no scope exists yet."""
        if not request.logical_id:
            raise BackendPrepareError("logical_id is required")
        unit = f"{_UNIT_PREFIX}{request.logical_id}-{uuid.uuid4().hex[:8]}.scope"
        return PendingHandle(
            backend=_BACKEND_NAME, token=unit, request_id=request.logical_id
        )

    def launch(self, pending: PendingHandle, spec) -> RunningHandle:
        """Launch *spec.argv* into the per-session scope and verify it landed.

        ``systemd-run --user --scope`` execs the target inside the transient
        scope (blocking stdio/PID model), so the returned handle's
        ``process`` is the contained target; membership is verified by
        reading the process's own cgroup before the handle is returned."""
        unit = pending.token
        try:
            proc = self._systemd_run_scope(
                unit,
                self._slice_name,
                tuple(spec.argv),
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
        root_pid = proc.pid
        identity = self._census.start_identity(root_pid) or ""
        state = _LaunchState(unit=unit, cgroup=cg, root_pid=root_pid, started_at=self._monotonic())
        with self._launched_lock:
            self._launched[unit] = state
        return RunningHandle(
            backend=_BACKEND_NAME,
            token=unit,
            request_id=pending.request_id,
            root_pid=root_pid,
            start_identity=identity,
            process=proc,
        )

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
                counters["memory.current"] if "memory.current" in counters else None
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
    ) -> Receipt:
        """Explicit whole-scope stop on EVERY terminal path, then verify.

        Ordering: read authoritative counters (the cgroup disappears on
        stop) -> explicit ``systemctl stop`` -> bounded wait for **cgroup
        emptiness** (the unit can report ``inactive`` while a TERM-resistant
        member still lives in its cgroup — the control-group kill model:
        merely observing the main PID exit is insufficient) -> escalate to
        ``KILL`` when members remain -> verify cgroup emptiness -> identity-
        verified survivors (guaranteed-cleanup residue, admission-blocking)
        -> bounded receipt."""
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
        # 1. Authoritative counters BEFORE teardown.
        counters = self._read_counters(cg) if cg else {}
        # 2. Explicit stop — clean success included.
        self._systemctl("stop", unit, timeout=max(grace_seconds + 1.0, 2.0))
        # 3. Quiescence = cgroup empty (unit state is not authoritative:
        #    the scope can reach ``inactive`` while members still live in
        #    its cgroup after a TERM they ignored).
        members = self._cgroup_members(cg) if cg else {}
        deadline = started + grace_seconds
        escalated = False
        while members and self._monotonic() < deadline:
            self._sleep(0.05)
            members = self._cgroup_members(cg) if cg else {}
        if members:
            # 4. Escalate to KILL, bounded.
            escalated = True
            self._systemctl("kill", "--kill-who=all", "--signal=KILL", unit, timeout=5)
            self._systemctl("stop", unit, timeout=5)
            kill_deadline = self._monotonic() + min(grace_seconds, 2.0)
            while self._monotonic() < kill_deadline:
                members = self._cgroup_members(cg) if cg else {}
                if not members:
                    break
                self._sleep(0.05)
            else:
                members = self._cgroup_members(cg) if cg else {}
        # 5. Identity-safe survivors (guaranteed-cleanup residue).
        survivors = tuple(
            SurvivorRecord(
                pid=pid,
                start_identity=ident,
                backend=_BACKEND_NAME,
                discovered_at=started,
                last_seen_at=started,
            )
            for pid, ident in sorted(members.items())
        )
        active = self._unit_active(unit)
        quiescent = not members and not active
        cleanup = (
            CleanupStatus.INCOMPLETE
            if not quiescent
            else (CleanupStatus.KILL if escalated else CleanupStatus.CLEAN)
        )
        memory_peak, cpu_total, process_peak = merge_sample_peaks(samples)
        memory_peak_kernel = (
            counters["memory.peak"] if "memory.peak" in counters else None
        )
        cpu_total_kernel = counters.get("cpu.stat")
        process_now = counters.get("pids.current")
        process_candidates = [v for v in (process_peak, process_now) if v is not None]
        process_peak_value = max(process_candidates) if process_candidates else None
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
                else MeasurementProvenance.SAMPLED
            ),
            cpu_total_seconds=cpu_total_kernel if cpu_total_kernel is not None else cpu_total,
            cpu_total_provenance=(
                MeasurementProvenance.KERNEL
                if cpu_total_kernel is not None
                else MeasurementProvenance.SAMPLED
            ),
            process_peak=process_peak_value,
            process_peak_provenance=MeasurementProvenance.SAMPLED,
            sample_gaps=sample_gaps(samples),
            enforcement_events=(),
            survivors=survivors,
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
        """True while the unit has not reached a terminal inactive state.

        ``deactivating`` / ``activating`` count as still-active: quiescence
        is only claimed once the unit is truly inactive AND its cgroup is
        empty."""
        code, out = self._systemctl("is-active", unit, timeout=5)
        return code == 0 and out.strip() in (
            "active", "activating", "deactivating", "reloading",
        )

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
        ok = not self._unit_active(unit) and not members
        return ok, "cgroup-empty" if ok else f"residue={sorted(members)}"


# Keep the Protocol check honest at import time.
def _protocol_check() -> None:
    assert isinstance(LinuxSystemdBackend(), SessionBackend)


_protocol_check()
