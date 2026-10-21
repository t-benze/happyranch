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
one previously authenticated live identity still holds the captured pgid). A
reused group number with no verified member is never signaled.
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Callable

from runtime.platform.process_census import (
    ProcessObservation,
    ProcessObservationUnavailable,
    ProcessTreeCensus,
    default_process_reader,
    is_zombie,
    merge_sample_peaks,
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
_MAX_TRUSTED_IDENTITIES = 1024


@dataclass
class _Session:
    root_pid: int
    root_identity: str
    pgid: int
    started: float
    # Only identities authenticated while the root/known group was still ours.
    members: dict[tuple[int, str], ProcessObservation] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)
    retired: bool = False
    observation_error: str | None = None


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
        self._sessions: dict[str, _Session] = {}

    # ── capability probe (real operations) ─────────────────────────

    def probe(self) -> CapabilityReport:
        """Operationally verify process-group + census machinery.

        Runs sleep directly in a new session/group, verifies the group id
        and ``killpg`` reachability, and waits for that nonforking child after
        TERM or fallback KILL. Unknown disposal refuses health; census
        enumeration alone does not prove cleanup. Reports
        ``best_effort`` cleanup/reporting and ``unavailable`` limits."""
        started = self._monotonic()
        probe_error = None
        try:
            child = subprocess.Popen(
                ["sleep", "0.2"],
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            try:
                pgid = os.getpgid(child.pid)
                if pgid != child.pid:
                    raise RuntimeError(
                        f"new session group mismatch: pgid={pgid} pid={child.pid}"
                    )
                # killpg(0) must not raise for a live group we own.
                os.killpg(child.pid, 0)
                self._census.start_identity(child.pid)
            except Exception as exc:  # retain the probe failure through disposal
                probe_error = exc
            finally:
                try:
                    try:
                        child.terminate()
                    except ProcessLookupError:
                        pass  # Already exited still requires a direct wait.
                    child.wait(timeout=5)
                except (subprocess.TimeoutExpired, OSError):
                    try:
                        try:
                            child.kill()
                        except ProcessLookupError:
                            pass
                    finally:
                        # Even a failed KILL must attempt the bounded direct wait.
                        child.wait(timeout=5)
            if probe_error is not None:
                raise probe_error
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
                    "probe: directly launched nonforking sleep in a new group; "
                    "signaled and waited for the direct child; "
                    "identity-safe census enumerated the live process "
                    "table"
                ),
                probed_at=started,
                healthy=True,
            )
        except Exception as exc:  # noqa: BLE001 — probe must degrade honestly
            reason = f"probe failed: {exc}"
            if probe_error is not None and exc is not probe_error:
                reason = f"probe failed: {probe_error}; helper disposal failed: {exc}"
            return self._unhealthy(started, reason)

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
            backend=_BACKEND_NAME,
            token=token,
            request_id=request.logical_id,
            invocation_kind=request.invocation_kind,
            executor_profile=request.executor_profile,
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
        identity = ""
        session = _Session(proc.pid, identity, pgid, self._monotonic())
        try:
            root = self._census.reader().read_process_checked(proc.pid)
            identity = root.start_identity if root is not None else ""
            if not identity:
                raise ProcessObservationUnavailable("launch identity unavailable")
            session.root_identity = identity
            self._snapshot_members(session)
        except Exception as exc:
            # Popen already exists. Keep its owner/handle even on an incomplete
            # initial census, so normal finish still waits the direct child.
            session.observation_error = str(exc)
        with self._lock:
            self._sessions[pending.token] = session
        return RunningHandle(
            backend=_BACKEND_NAME,
            token=pending.token,
            request_id=pending.request_id,
            root_pid=proc.pid,
            start_identity=identity,
            process=proc,
            invocation_kind=pending.invocation_kind,
            executor_profile=pending.executor_profile,
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
            # Finish owns this same lock through terminal observation/retirement.
            # A read already in flight settles before finish; a late captured
            # reference can neither recreate a token nor change terminal facts.
            if session.retired:
                return ResourceSample(sampled_at=self._monotonic(),
                                      provenance=MeasurementProvenance.UNAVAILABLE)
            try:
                members = self._snapshot_members(session)
            except Exception as exc:
                session.observation_error = str(exc)
                raise
            if session.observation_error is not None:
                raise ProcessObservationUnavailable(session.observation_error)
            with self._lock:
                self._sessions[running.token] = session
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

    def _snapshot_members(self, session: _Session) -> dict[int, ProcessObservation]:
        """Merge one complete checked observation into authenticated history.

        Fresh numeric-group membership cannot authenticate itself. First match
        the original root or a previously authenticated live same-group member;
        only then admit new group members/descendants from this observation.
        Missing identities in an incomplete scan never erase prior facts.
        """
        observations = self._census.reader().read_all_checked()
        by_pid = {o.pid: o for o in observations}
        root = by_pid.get(session.root_pid)
        root_matches = bool(root and session.root_identity
                            and root.start_identity == session.root_identity
                            and not is_zombie(root.state))
        witnesses = [
            current for (pid, identity) in session.members
            if (current := by_pid.get(pid)) is not None
            and current.start_identity == identity and not is_zombie(current.state)
        ]
        group_owned = (root_matches and root.pgid == session.pgid) or any(
            o.pgid == session.pgid for o in witnesses
        )
        live = {(o.pid, o.start_identity): o for o in witnesses}
        # Descendants of retained escaped members are still authenticated by
        # lineage; they do not grant ownership of their new numeric group.
        seeds = {o.pid for o in witnesses}
        if root_matches:
            seeds.add(root.pid)
            live[(root.pid, root.start_identity)] = root
        if group_owned:
            for o in observations:
                if o.pgid == session.pgid and not is_zombie(o.state):
                    if not o.start_identity:
                        raise ProcessObservationUnavailable("group member identity unavailable")
                    seeds.add(o.pid)
                    live[(o.pid, o.start_identity)] = o
        children: dict[int, list[ProcessObservation]] = {}
        for o in observations:
            children.setdefault(o.ppid, []).append(o)
        pending = list(seeds)
        seen = set(seeds)
        while pending:
            for o in children.get(pending.pop(), ()):
                if o.pid in seen or is_zombie(o.state):
                    continue
                if not o.start_identity:
                    raise ProcessObservationUnavailable("descendant identity unavailable")
                seen.add(o.pid)
                pending.append(o.pid)
                live[(o.pid, o.start_identity)] = o
        if len(session.members.keys() | live.keys()) > _MAX_TRUSTED_IDENTITIES:
            raise ProcessObservationUnavailable("trusted identity history exhausted")
        # Keep historical keys until retirement (at most1024); only current
        # matched live values are used for measurement. No silent eviction.
        session.members.update(live)
        return {o.pid: o for o in live.values()}

    def sample(self, running: RunningHandle) -> ResourceSample:
        """Backend ``sample`` (protocol): one portable tree sample."""
        return self.sampler()(running)

    def finish(
        self,
        running: RunningHandle,
        terminal_reason: str,
        grace_seconds: float,
        samples: tuple[ResourceSample, ...] | None = None,
        sample_prefix_gap: float = 0.0,
    ) -> Receipt:
        """Serialize final observation, safe TERM/KILL and direct-child wait.

        History remains owned through finish. Unknown observation is sticky and
        raises to the supervisor's cleanup_error/gate; no successful provider
        result can turn it into a clean receipt. Only Popen's direct child is
        waited here, with the existing grace plus bounded KILL allowance.
        """
        with self._lock:
            session = self._sessions.get(running.token)
        if session is None:
            raise ProcessObservationUnavailable("process-group session unavailable")
        started = self._monotonic()
        deadline = started + grace_seconds + min(grace_seconds, 2.0)
        with session.lock:
            if session.retired:
                raise ProcessObservationUnavailable("process-group session already retired")
            failure = None
            receipt = None
            try:
                receipt = self._finish_owned(
                    session, running, terminal_reason, grace_seconds,
                    tuple(samples or ()), sample_prefix_gap, started,
                )
            except Exception as exc:
                failure = exc
            finally:
                try:
                    if running.process is None:
                        raise ProcessObservationUnavailable("direct process handle unavailable")
                    # Also runs when shutdown froze before bind and skipped
                    # communicate. Never use wildcard/adopted-child waits.
                    running.process.wait(timeout=max(0.0, deadline - self._monotonic()))
                except Exception as exc:
                    if failure is None:
                        failure = exc
                    else:
                        failure = RuntimeError(f"{failure}; direct child wait failed: {exc}")
                session.retired = True
                with self._lock:
                    self._sessions.pop(running.token, None)
            if failure is not None:
                raise failure
            return receipt

    def _finish_owned(
        self, session: _Session, running: RunningHandle, terminal_reason: str,
        grace_seconds: float, samples: tuple[ResourceSample, ...],
        sample_prefix_gap: float, started: float,
    ) -> Receipt:
        # Fresh observation is required even if earlier sampling failed. A
        # later readable scan cannot erase the earlier ownership uncertainty.
        self._snapshot_members(session)
        if session.observation_error is not None:
            raise ProcessObservationUnavailable(session.observation_error)
        group = self._group_members_alive(session.pgid)
        escalated = False
        signaled = False
        if group:
            self._signal_owned_group(session, signal.SIGTERM)
            signaled = True
            deadline = started + grace_seconds
            while group and self._monotonic() < deadline:
                self._sleep(_POLL_INTERVAL)
                self._snapshot_members(session)
                group = self._group_members_alive(session.pgid)
            if group:
                self._signal_owned_group(session, signal.SIGKILL)
                escalated = True
                deadline = self._monotonic() + min(grace_seconds, 2.0)
                while group and self._monotonic() < deadline:
                    self._sleep(_POLL_INTERVAL)
                    self._snapshot_members(session)
                    group = self._group_members_alive(session.pgid)
        self._snapshot_members(session)
        survivors = self._verified_survivors(session.members)
        group = self._group_members_alive(session.pgid)
        return self._receipt(
            terminal_reason=terminal_reason,
            cleanup_status=(CleanupStatus.INCOMPLETE if group else
                            CleanupStatus.KILL if escalated else
                            CleanupStatus.TERM if signaled or survivors else CleanupStatus.CLEAN),
            quiescent=not group and not survivors,
            wall_start=started, session_started=session.started,
            samples=samples, survivors=survivors,
            sample_prefix_gap=sample_prefix_gap,
            invocation_kind=running.invocation_kind,
            executor_profile=running.executor_profile,
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
        sample_prefix_gap: float = 0.0,
        invocation_kind: str = "",
        executor_profile: str = "",
    ) -> Receipt:
        memory, cpu, process = merge_sample_peaks(samples)
        gaps = sample_gaps(samples)
        if sample_prefix_gap:
            gaps = (round(sample_prefix_gap, 6),) + gaps
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
            sample_gaps=gaps,
            enforcement_events=(),
            survivors=survivors,
            invocation_kind=invocation_kind,
            executor_profile=executor_profile,
        )

    def abandon(self, pending: PendingHandle) -> None:
        """Close a prepared-but-never-launched handle (no process exists)."""
        with self._lock:
            self._sessions.pop(pending.token, None)

    def recover(self, handle_token: str) -> RecoveryResult:
        """No durable recovery; daemon-crash cleanup is not guaranteed."""
        return RecoveryResult(
            recovered=False,
            evidence=(
                "macos process-group backend keeps no durable residue; "
                "daemon-crash cleanup is not guaranteed"
            ),
        )

    # ── internals ─────────────────────────────────────────────────

    def _group_is_ours(self, session: _Session) -> bool:
        """A current identity must match previously authenticated ownership.

        Fresh numeric-group membership alone is never a witness. Recheck for
        EACH signal, including escalation. POSIX check-to-signal races remain
        best effort; this is not a kernel containment guarantee.
        """
        current_by_pid = {o.pid: o for o in self._census.reader().read_all_checked()}
        keys = {(session.root_pid, session.root_identity)} | session.members.keys()
        for pid, identity in keys:
            if not identity:
                continue
            current = current_by_pid.get(pid)
            if (current is not None and current.start_identity == identity
                    and not is_zombie(current.state) and current.pgid == session.pgid):
                return True
        return False

    def _group_members_alive(self, pgid: int) -> dict[int, str]:
        observations = self._census.reader().read_all_checked()
        return {o.pid: o.start_identity for o in observations
                if o.pgid == pgid and not is_zombie(o.state)}

    def _signal_owned_group(self, session: _Session, sig: signal.Signals) -> None:
        if not self._group_is_ours(session):
            raise ProcessObservationUnavailable("live group has no authenticated ownership witness")
        try:
            os.killpg(session.pgid, sig)
        except ProcessLookupError:
            pass  # Proven disappearance, unlike permission or other errors.

    def _verified_survivors(
        self, history: dict[tuple[int, str], ProcessObservation]
    ) -> tuple[SurvivorRecord, ...]:
        """Proven death/disappearance/reuse clears liveness; unreadable raises."""
        now = self._monotonic()
        current_by_pid = {o.pid: o for o in self._census.reader().read_all_checked()}
        survivors: list[SurvivorRecord] = []
        for pid, identity in sorted(history):
            current = current_by_pid.get(pid)
            if current is None or is_zombie(current.state):
                continue
            if current.start_identity != identity:
                continue
            survivors.append(SurvivorRecord(
                pid=pid, start_identity=identity, backend=_BACKEND_NAME,
                discovered_at=now, last_seen_at=now,
            ))
        return tuple(survivors)


# Keep the Protocol check honest at import time.
def _protocol_check() -> None:
    assert isinstance(MacOSProcessGroupBackend(), SessionBackend)


_protocol_check()
