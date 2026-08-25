"""Honestly capped macOS lifecycle/measurement backend (THR-207 Slice B).

The macOS initial backend is **honestly capped**, never falsely described as
hard containment: it launches each session in its own POSIX process
group/session, continuously samples the identity-safe descendant census
(RSS + CPU + process peaks, ``sampled`` provenance), and on every terminal
path TERMinates the group within the measured grace, escalates to KILL, and
accounts **identity-verified survivors** best-effort.

Truthful limitations (reported as capabilities, never hidden):

* ``limits_memory`` / ``limits_pids`` / ``limits_cpu`` are ``unavailable`` —
  macOS exposes no cgroup-equivalent descendant-tree controller.
* ``kills_tree_best_effort`` is ``best_effort``: a child that daemonizes /
  creates a new session escapes ``killpg``; surviving descendants are kept
  in the census (charged/visible) and block admission only on
  census/measurement failure or the conservative survivor threshold —
  never an automatic self-DoS from a single verified survivor.
* ``reports_*`` are ``best_effort`` (``sampled`` provenance) — a sampled peak
  can undercount between samples and is never labeled authoritative.

Group-ownership safety: the group is signaled only when the census proves
it is ours (the root's recorded start identity still matches, or at least
one census member currently holds the captured pgid). A reused group number
with no verified member is never signaled.
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import threading
import time
import uuid
from typing import Callable

from runtime.platform.process_census import (
    ProcessObservation,
    ProcessTreeCensus,
    default_process_reader,
    is_zombie,
    merge_sample_peaks,
    pid_live,
    sample_gaps,
)
from runtime.platform.session_backend import (
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

_BACKEND_NAME = "macos-process-group"
_BACKEND_VERSION = "0.1"
_POLL_INTERVAL = 0.05


class MacOSProcessGroupBackend:
    """Process-group + identity-safe descendant census :class:`SessionBackend`.

    The session root launches with ``start_new_session=True`` (its own POSIX
    session/process group); ordinary descendants inherit the group and are
    TERM/KILLed as a unit; descendants that leave the group are detected
    through the identity-safe census and reported as best-effort survivors.
    """

    name = _BACKEND_NAME

    def __init__(
        self,
        *,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        census: ProcessTreeCensus | None = None,
    ) -> None:
        self._monotonic = monotonic
        self._sleep = sleep
        self._census = census or ProcessTreeCensus(default_process_reader())
        self._lock = threading.Lock()
        # token -> (root_pid, root_identity, pgid, started_at, member snapshot)
        self._sessions: dict[str, tuple] = {}

    # ── capability probe (real operations) ─────────────────────────

    def probe(self) -> CapabilityReport:
        """Operationally verify process-group + census machinery.

        Creates one disposable child in a new session/group, verifies the
        group id, verifies ``killpg`` reaches it, reaps it, and verifies the
        identity-safe census reader works. Leaves no residue. Reports
        ``best_effort`` cleanup/reporting and ``unavailable`` limits."""
        started = self._monotonic()
        try:
            child = subprocess.Popen(
                ["sh", "-c", "sleep 0.2"],
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            try:
                pgid = os.getpgid(child.pid)
                if pgid != child.pid:
                    return self._unhealthy(
                        started, f"new session group mismatch: pgid={pgid} pid={child.pid}"
                    )
                # killpg(0) must not raise for a live group we own.
                os.killpg(child.pid, 0)
                self._census.start_identity(child.pid)
            finally:
                try:
                    child.terminate()
                    child.wait(timeout=5)
                except (ProcessLookupError, subprocess.TimeoutExpired, OSError):
                    try:
                        os.killpg(child.pid, signal.SIGKILL)
                    except (ProcessLookupError, OSError):
                        pass
            # A live census read proves enumeration is usable on this host.
            self._census.descendants(os.getpid())
            return CapabilityReport(
                backend=_BACKEND_NAME,
                backend_version=_BACKEND_VERSION,
                capabilities={
                    Capability.KILLS_TREE_BEST_EFFORT: CapabilityLevel.BEST_EFFORT,
                    Capability.REPORTS_MEMORY_PEAK: CapabilityLevel.BEST_EFFORT,
                    Capability.REPORTS_CPU_TOTAL: CapabilityLevel.BEST_EFFORT,
                    Capability.REPORTS_PROCESS_PEAK: CapabilityLevel.BEST_EFFORT,
                },
                evidence=(
                    "probe: created + signaled + reaped a disposable process "
                    "group; identity-safe census enumerated the live process "
                    "table"
                ),
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
            evidence=f"macos backend unhealthy: {reason}",
            reason=reason,
            probed_at=started,
            healthy=False,
        )

    # ── lifecycle ─────────────────────────────────────────────────

    def prepare(self, request, policy) -> PendingHandle:
        if not request.logical_id:
            raise BackendPrepareError("logical_id is required")
        token = f"pg-{request.logical_id}-{uuid.uuid4().hex[:8]}"
        return PendingHandle(
            backend=_BACKEND_NAME, token=token, request_id=request.logical_id
        )

    def launch(self, pending: PendingHandle, spec) -> RunningHandle:
        """Launch *spec.argv* in a new process group and verify the pgid."""
        env = dict(os.environ)
        if spec.env:
            env.update(spec.env)
        try:
            proc = subprocess.Popen(
                list(spec.argv),
                cwd=spec.cwd or ".",
                env=env,
                stdin=spec.stdin,
                stdout=spec.stdout,
                stderr=spec.stderr,
                text=spec.text,
                start_new_session=True,
            )
        except OSError as exc:
            raise BackendLaunchError(
                f"cannot launch {spec.argv[0]!r} in a new session: {exc}"
            ) from exc
        try:
            pgid = os.getpgid(proc.pid)
        except OSError as exc:
            raise BackendLaunchError(
                f"cannot read pgid of launched process {proc.pid}: {exc}"
            ) from exc
        if pgid != proc.pid:
            proc.kill()
            raise BackendLaunchError(
                f"launched process is not a group leader: pgid={pgid} pid={proc.pid}"
            )
        identity = self._census.start_identity(proc.pid) or ""
        snapshot = self._snapshot_members(proc.pid, identity)
        with self._lock:
            self._sessions[pending.token] = (
                proc.pid,
                identity,
                pgid,
                self._monotonic(),
                snapshot,
            )
        return RunningHandle(
            backend=_BACKEND_NAME,
            token=pending.token,
            request_id=pending.request_id,
            root_pid=proc.pid,
            start_identity=identity,
            process=proc,
        )

    def sampler(self) -> Callable[[RunningHandle], ResourceSample]:
        """A sampler callable for the supervisor's sampler seam.

        Refreshes the backend's per-session member snapshot (identity-safe)
        and returns a ``sampled``-provenance :class:`ResourceSample` — the
        honest portable measurement for a backend with no kernel counters.
        """

        def _sample(running: RunningHandle) -> ResourceSample:
            with self._lock:
                session = self._sessions.get(running.token)
            if session is None:
                return ResourceSample(
                    sampled_at=self._monotonic(),
                    provenance=MeasurementProvenance.UNAVAILABLE,
                )
            root_pid, identity, _pgid, _started, _snap = session
            members = self._snapshot_members(root_pid, identity)
            with self._lock:
                self._sessions[running.token] = (
                    root_pid,
                    identity,
                    _pgid,
                    _started,
                    members,
                )
            rss = sum(o.rss_bytes for o in members.values() if o.rss_bytes is not None)
            cpu = sum(o.cpu_seconds for o in members.values() if o.cpu_seconds is not None)
            return ResourceSample(
                sampled_at=self._monotonic(),
                memory_peak_bytes=rss if rss or members else None,
                cpu_total_seconds=cpu if cpu or members else None,
                process_count=len(members) if members else None,
                provenance=MeasurementProvenance.SAMPLED,
            )

        return _sample

    def _snapshot_members(
        self, root_pid: int, root_identity: str
    ) -> dict[int, ProcessObservation]:
        """Identity-safe union of the descendant tree + group members.

        Returns pid -> observation. The tree walk claims only processes whose
        ancestor chain reaches the (identity-verified) root; group members
        are added so reparented in-group processes are still accounted."""
        tree = self._census.descendants(root_pid, root_identity or None, include_root=True)
        members: dict[int, ProcessObservation] = {o.pid: o for o in tree}
        try:
            pgid = os.getpgid(root_pid)
        except OSError:
            pgid = None
        if pgid is not None:
            for o in self._census.group_members(pgid):
                members.setdefault(o.pid, o)
        return members

    def sample(self, running: RunningHandle) -> ResourceSample:
        """Backend ``sample`` (protocol): one portable tree sample."""
        return self.sampler()(running)

    def finish(
        self,
        running: RunningHandle,
        terminal_reason: str,
        grace_seconds: float,
        samples: tuple[ResourceSample, ...] | None = None,
    ) -> Receipt:
        """TERM -> bounded grace -> KILL the process group; census survivors.

        Runs on every terminal path, clean success included. The group is
        signaled only when the census proves ownership (root identity match
        or a verified member in the captured pgid); escaped descendants that
        survive are reported as best-effort :class:`SurvivorRecord` and stay
        censused/charged/visible (never an automatic admission block)."""
        samples = tuple(samples or ())
        token = running.token
        with self._lock:
            session = self._sessions.pop(token, None)
        started = self._monotonic()
        root_pid = running.root_pid
        root_identity = running.start_identity or ""
        pgid = running.root_pid  # start_new_session makes the root the leader
        if session is not None:
            root_pid, root_identity, pgid, _started, snapshot = session
        else:
            snapshot = self._snapshot_members(root_pid, root_identity)

        # ── group-ownership proof before signaling ──
        if not self._group_is_ours(root_pid, root_identity, pgid, snapshot):
            # Refuse to signal an ambiguous group number (PID-reuse safety).
            group_members = self._group_members_alive(pgid)
            survivors = self._verified_survivors(snapshot)
            if group_members:
                # Verified members sit in a group we cannot prove is ours —
                # fail-closed: residue is reported, cleanup incomplete.
                return self._receipt(
                    terminal_reason=terminal_reason,
                    cleanup_status=CleanupStatus.INCOMPLETE,
                    quiescent=False,
                    wall_start=started,
                    session_started=(session[3] if session is not None else started),
                    samples=samples,
                    survivors=survivors,
                )
            return self._receipt(
                terminal_reason=terminal_reason,
                cleanup_status=(
                    CleanupStatus.CLEAN if not survivors else CleanupStatus.TERM
                ),
                quiescent=not survivors,
                wall_start=started,
                session_started=(session[3] if session is not None else started),
                samples=samples,
                survivors=survivors,
            )

        # ── graceful TERM of the group, bounded by the measured grace ──
        group_members = self._group_members_alive(pgid)
        if not group_members:
            # The group is already empty (e.g. clean success, no residue) —
            # nothing to signal; escaped survivors are still accounted.
            survivors = self._verified_survivors(snapshot)
            return self._receipt(
                terminal_reason=terminal_reason,
                cleanup_status=(
                    CleanupStatus.CLEAN if not survivors else CleanupStatus.TERM
                ),
                quiescent=not survivors,
                wall_start=started,
                session_started=(session[3] if session is not None else started),
                samples=samples,
                survivors=survivors,
            )
        self._signal_group(pgid, signal.SIGTERM)
        deadline = started + grace_seconds
        group_members = self._group_members_alive(pgid)
        while group_members and self._monotonic() < deadline:
            self._sleep(_POLL_INTERVAL)
            group_members = self._group_members_alive(pgid)
        escalated = bool(group_members)
        if escalated:
            # ── escalate to KILL, bounded ──
            self._signal_group(pgid, signal.SIGKILL)
            kill_deadline = self._monotonic() + min(grace_seconds, 2.0)
            while self._group_members_alive(pgid) and self._monotonic() < kill_deadline:
                self._sleep(_POLL_INTERVAL)
        # ── identity-verified survivors (escaped or TERM-resistant) ──
        survivors = self._verified_survivors(snapshot)
        group_remaining = self._group_members_alive(pgid)
        cleanup = (
            CleanupStatus.INCOMPLETE
            if group_remaining
            else (CleanupStatus.KILL if escalated else CleanupStatus.TERM)
        )
        quiescent = not group_remaining and not survivors
        return self._receipt(
            terminal_reason=terminal_reason,
            cleanup_status=cleanup,
            quiescent=quiescent,
            wall_start=started,
            session_started=(session[3] if session is not None else started),
            samples=samples,
            survivors=survivors,
        )

    def _receipt(
        self,
        *,
        terminal_reason: str,
        cleanup_status: CleanupStatus,
        quiescent: bool,
        wall_start: float,
        session_started: float,
        samples: tuple[ResourceSample, ...],
        survivors: tuple[SurvivorRecord, ...],
    ) -> Receipt:
        memory, cpu, process = merge_sample_peaks(samples)
        return Receipt(
            backend=_BACKEND_NAME,
            terminal_reason=terminal_reason,
            cleanup_status=cleanup_status,
            cleanup_duration_seconds=self._monotonic() - wall_start,
            quiescent=quiescent,
            wall_time_seconds=self._monotonic() - session_started,
            memory_peak_bytes=memory,
            memory_peak_provenance=(
                MeasurementProvenance.SAMPLED if memory is not None else MeasurementProvenance.UNAVAILABLE
            ),
            cpu_total_seconds=cpu,
            cpu_total_provenance=(
                MeasurementProvenance.SAMPLED if cpu is not None else MeasurementProvenance.UNAVAILABLE
            ),
            process_peak=process,
            process_peak_provenance=(
                MeasurementProvenance.SAMPLED if process is not None else MeasurementProvenance.UNAVAILABLE
            ),
            sample_gaps=sample_gaps(samples),
            enforcement_events=(),
            survivors=survivors,
        )

    def abandon(self, pending: PendingHandle) -> None:
        """Close a prepared-but-never-launched handle (no process exists)."""
        with self._lock:
            self._sessions.pop(pending.token, None)

    def recover(self, handle_token: str) -> RecoveryResult:
        """No durable residue: process groups die with the session."""
        return RecoveryResult(
            recovered=False,
            evidence=(
                "macos process-group backend keeps no durable residue; "
                "daemon-crash cleanup is not guaranteed"
            ),
        )

    # ── internals ─────────────────────────────────────────────────

    def _group_is_ours(
        self,
        root_pid: int,
        root_identity: str,
        pgid: int,
        snapshot: dict[int, ProcessObservation],
    ) -> bool:
        """Proof of group ownership before any signal is sent.

        True when the root's identity still matches (it leads the group) or
        when at least one snapshot member currently holds the captured pgid.
        A reused group number with no verified member is never signaled."""
        ident_now = self._census.start_identity(root_pid)
        if ident_now is not None and root_identity and ident_now == root_identity:
            return True
        for pid in snapshot:
            try:
                if os.getpgid(pid) == pgid:
                    return True
            except (ProcessLookupError, PermissionError):
                continue
        return False

    def _group_members_alive(self, pgid: int) -> dict[int, str]:
        """Live (non-zombie) members of *pgid* (identity-verified)."""
        try:
            members = self._census.group_members(pgid)
        except Exception:  # noqa: BLE001 — treat unreadable as empty (fail-safe)
            return {}
        alive = {}
        for o in members:
            if is_zombie(o.state):
                continue  # a zombie is already dead — never a survivor
            if o.start_identity is not None:
                alive[o.pid] = o.start_identity
        return alive

    def _signal_group(self, pgid: int, sig: signal.Signals) -> None:
        try:
            os.killpg(pgid, sig)
        except (ProcessLookupError, PermissionError, OSError):
            pass  # group already gone or not ours — ownership was verified

    def _verified_survivors(self, snapshot: dict[int, ProcessObservation]) -> tuple[SurvivorRecord, ...]:
        """Identity-verified survivors from the observed member snapshot.

        A snapshot member is a survivor only when it is STILL alive right
        now (the CURRENT observation — a zombie that appeared since the
        snapshot is already dead and answers no signal) AND its start
        identity still matches the snapshot — PID reuse is never counted as
        the same process."""
        now = self._monotonic()
        survivors: list[SurvivorRecord] = []
        for pid in sorted(snapshot):
            current = self._census.reader().read_process(pid)
            if current is None or is_zombie(current.state):
                continue  # gone or an unreaped zombie — not a survivor
            if current.start_identity != snapshot[pid].start_identity:
                continue  # PID reused by an unrelated process
            survivors.append(
                SurvivorRecord(
                    pid=pid,
                    start_identity=current.start_identity,
                    backend=_BACKEND_NAME,
                    discovered_at=now,
                    last_seen_at=now,
                )
            )
        return tuple(survivors)


# Keep the Protocol check honest at import time.
def _protocol_check() -> None:
    assert isinstance(MacOSProcessGroupBackend(), SessionBackend)


_protocol_check()
