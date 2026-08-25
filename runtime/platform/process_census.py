"""Portable, identity-safe descendant-tree census and sampling (THR-207 Slice B).

Slice B of the host-resource concurrency architecture
(``docs/superpowers/specs/2026-08-24-host-resource-concurrency.md``) ships the
portable measurement surface: a per-session descendant-tree census that is
**identity-safe** (a PID is only acted upon when its recorded start identity
still matches — PID reuse is never mistaken for the same process) plus a
sampler that yields bounded ``ResourceSample`` values with explicit
``sampled`` provenance.

Design rules that are load-bearing:

1. **OS-shipped APIs only.** The Linux reader walks ``/proc`` (``stat`` /
   ``statm``); the macOS reader uses libproc through the standard library's
   ``ctypes`` (``proc_listpids`` / ``proc_pidinfo``). No third-party
   dependency (e.g. ``psutil``) is added — that requires separate founder
   approval.
2. **Never fabricated zeros.** A sample whose enumeration failed reports
   ``unavailable`` provenance with ``None`` values (or raises so the
   supervisor's sampler records a measurement failure, fail-closed) — it
   never claims a zero footprint it did not observe.
3. **Identity-safe census.** The descendant walk claims a process only when
   its (pid, start identity) is consistent with the recorded root identity;
   a reused root PID with a different start identity yields an **empty**
   census — the sampler never attributes another process's tree.
4. **Sampled values are never authoritative.** ``sampled`` provenance
   explicitly admits inter-sample undercounting; backends that own kernel
   counters (Linux cgroup v2) merge authoritative values at finish time.
"""

from __future__ import annotations

import os
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Protocol

from runtime.platform.session_backend import (
    MeasurementProvenance,
    ResourceSample,
    RunningHandle,
)

# ── observations ─────────────────────────────────────────────────────


def is_zombie(state: str | int | None) -> bool:
    """True when *state* marks an unreaped (already dead) zombie.

    Linux ``/proc`` state ``Z``; macOS ``pbi_status`` ``5`` (SZOMB). A
    zombie still answers ``kill(pid, 0)`` and keeps its pgid — it must never
    be counted as a live survivor."""
    if state is None:
        return False
    if isinstance(state, str):
        return state == "Z"
    return state == 5  # macOS SZOMB


def pid_live(pid: int, state: str | int | None = None) -> bool:
    """True when *pid* exists and is not an unreaped zombie."""
    if is_zombie(state):
        return False
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


@dataclass(frozen=True)
class ProcessObservation:
    """One process observed at one instant.

    ``start_identity`` is the kernel start-time string (Linux ``starttime``
    jiffies; macOS ``pbi_start_tvsec.usec``) — the identity used to reject
    PID-reuse aliases. ``rss_bytes`` / ``cpu_seconds`` are ``None`` when the
    platform cannot read them for that process (never a fabricated 0).
    """

    pid: int
    ppid: int | None
    pgid: int | None
    start_identity: str | None
    rss_bytes: int | None = None
    cpu_seconds: float | None = None
    comm: str = ""
    # Process state used to distinguish a live process from an unreaped
    # zombie (a zombie still carries its pid/pgid and answers ``kill(pid,0)``
    # but is already dead — it must never count as a survivor). Linux: a
    # one-letter state (``Z`` = zombie); macOS: the numeric ``pbi_status``
    # (``5`` = SZOMB).
    state: str | int | None = None


class ProcessReader(Protocol):
    """One snapshot reader for the host's process table.

    ``read_all`` returns every observable process in a **single pass** (the
    parent map is internally consistent at that instant). ``start_identity``
    reads one PID's start identity (used for root-PID-reuse rejection and
    survivor verification).
    """

    def read_all(self) -> list[ProcessObservation]: ...
    def start_identity(self, pid: int) -> str | None: ...
    def read_process(self, pid: int) -> ProcessObservation | None: ...


# ── Linux /proc reader ───────────────────────────────────────────────


def _parse_proc_stat(line: str) -> dict | None:
    """Parse one ``/proc/<pid>/stat`` line.

    ``comm`` may contain spaces and parentheses, so the line is split on the
    LAST ``)``; everything before the first ``(`` is the pid, everything
    after the last ``)`` is the remaining fields (state, ppid, pgrp, ...
    starttime, ...).
    """
    try:
        open_idx = line.index("(")
        close_idx = line.rindex(")")
        pid = int(line[:open_idx].strip())
        rest = line[close_idx + 1 :].split()
        # rest[0]=state(3) rest[1]=ppid(4) rest[2]=pgrp(5)
        # rest[11]=utime(14) rest[12]=stime(15) rest[19]=starttime(22)
        ppid = int(rest[1])
        pgrp = int(rest[2])
        utime = int(rest[11])
        stime = int(rest[12])
        starttime = rest[19]
        return {
            "pid": pid,
            "ppid": ppid,
            "pgid": pgrp,
            "start_identity": starttime,
            "utime": utime,
            "stime": stime,
            "state": rest[0],
        }
    except (ValueError, IndexError):
        return None


class LinuxProcReader:
    """``/proc``-backed :class:`ProcessReader` (Linux).

    Reads ``stat`` (identity/ppid/pgid/CPU) and ``statm`` (RSS pages) in one
    pass over ``/proc``. A process whose files vanish mid-pass is skipped
    (it exited during the pass) — never fabricated.
    """

    def __init__(self, proc_root: str | os.PathLike = "/proc") -> None:
        self._proc_root = os.fspath(proc_root)
        self._page_size = os.sysconf("SC_PAGESIZE") or 4096

    def read_all(self) -> list[ProcessObservation]:
        obs: list[ProcessObservation] = []
        with os.scandir(self._proc_root) as it:
            for entry in it:
                if not entry.is_dir():
                    continue
                try:
                    pid = int(entry.name)
                except ValueError:
                    continue  # not a numeric /proc entry
                stat = self._read_stat(pid)
                if stat is None:
                    continue
                rss_pages = self._read_rss_pages(pid)
                obs.append(
                    ProcessObservation(
                        pid=pid,
                        ppid=stat["ppid"],
                        pgid=stat["pgid"],
                        start_identity=stat["start_identity"],
                        rss_bytes=(
                            rss_pages * self._page_size if rss_pages is not None else None
                        ),
                        cpu_seconds=(
                            (stat["utime"] + stat["stime"]) / os.sysconf("SC_CLK_TCK")
                            if os.sysconf("SC_CLK_TCK")
                            else None
                        ),
                        comm="",
                        state=stat["state"],
                    )
                )
        return obs

    def start_identity(self, pid: int) -> str | None:
        stat = self._read_stat(pid)
        return stat["start_identity"] if stat else None

    def read_process(self, pid: int) -> ProcessObservation | None:
        """One observation for *pid* (``None`` when it vanished mid-read)."""
        stat = self._read_stat(pid)
        if stat is None:
            return None
        rss_pages = self._read_rss_pages(pid)
        return ProcessObservation(
            pid=pid,
            ppid=stat["ppid"],
            pgid=stat["pgid"],
            start_identity=stat["start_identity"],
            rss_bytes=(
                rss_pages * self._page_size if rss_pages is not None else None
            ),
            cpu_seconds=(
                (stat["utime"] + stat["stime"]) / os.sysconf("SC_CLK_TCK")
                if os.sysconf("SC_CLK_TCK")
                else None
            ),
            comm="",
            state=stat["state"],
        )

    def _read_stat(self, pid: int) -> dict | None:
        try:
            with open(f"{self._proc_root}/{pid}/stat", "r", encoding="utf-8") as fh:
                return _parse_proc_stat(fh.read())
        except (OSError, ValueError):
            return None

    def _read_rss_pages(self, pid: int) -> int | None:
        try:
            with open(f"{self._proc_root}/{pid}/statm", "r", encoding="utf-8") as fh:
                fields = fh.read().split()
                return int(fields[1]) if len(fields) >= 2 else None
        except (OSError, ValueError, IndexError):
            return None


# ── macOS libproc reader ─────────────────────────────────────────────


class _LibProc:
    """Bounded ``ctypes`` binding to macOS libproc (no third-party dep).

    Uses only ``proc_listpids`` / ``proc_pidinfo`` with the
    ``PROC_PIDTBSDINFO`` (parent/start identity) and ``PROC_PIDTASKINFO``
    (RSS/CPU) structs. Loaded lazily and only on macOS; raises
    :class:`LibProcUnavailable` when the host cannot provide it.
    """

    PROC_ALL_PIDS = 1
    PROC_PIDTBSDINFO = 3
    PROC_PIDTASKINFO = 4
    PROC_PIDLISTFD_SIZE = 30 * 4096

    def __init__(self) -> None:
        if not sys.platform == "darwin":
            raise LibProcUnavailable("libproc is macOS-only")
        try:
            import ctypes
            import ctypes.util

            lib_path = ctypes.util.find_library("proc") or "libproc.dylib"
            self._lib = ctypes.CDLL(lib_path, use_errno=True)
            self._ctypes = ctypes
        except (OSError, ImportError) as exc:
            raise LibProcUnavailable(f"cannot load libproc: {exc}") from exc
        self._setup_proc_pidinfo()

    def _setup_proc_pidinfo(self) -> None:
        ctypes = self._ctypes

        class ProcBsdinfo(ctypes.Structure):
            _fields_ = [
                ("pbi_flags", ctypes.c_uint32),
                ("pbi_status", ctypes.c_uint32),
                ("pbi_pid", ctypes.c_int),
                ("pbi_ppid", ctypes.c_int),
                ("pbi_uid", ctypes.c_uint32),
                ("pbi_gid", ctypes.c_uint32),
                ("pbi_ruid", ctypes.c_uint32),
                ("pbi_rgid", ctypes.c_uint32),
                ("pbi_svuid", ctypes.c_uint32),
                ("pbi_svgid", ctypes.c_uint32),
                ("pbi_rfu", ctypes.c_uint32),
                ("pbi_comm", ctypes.c_char * 16),
                ("pbi_name", ctypes.c_char * 32),
                ("pbi_nfiles", ctypes.c_int),
                ("pbi_pgid", ctypes.c_int),
                ("pbi_pjobc", ctypes.c_int),
                ("pbi_e_tdev", ctypes.c_uint32),
                ("pbi_e_tpgid", ctypes.c_int),
                ("pbi_nice", ctypes.c_int),
                ("pbi_start_tvsec", ctypes.c_uint64),
                ("pbi_start_tvusec", ctypes.c_uint64),
            ]

        class ProcTaskinfo(ctypes.Structure):
            _fields_ = [
                ("pti_virtual_size", ctypes.c_uint64),
                ("pti_resident_size", ctypes.c_uint64),
                ("pti_total_user", ctypes.c_uint64),
                ("pti_total_system", ctypes.c_uint64),
                ("pti_threads_user", ctypes.c_uint64),
                ("pti_threads_system", ctypes.c_uint64),
                ("pti_policy", ctypes.c_int32),
                ("pti_faults", ctypes.c_int32),
                ("pti_pageins", ctypes.c_int32),
                ("pti_cow_faults", ctypes.c_int32),
                ("pti_messages_sent", ctypes.c_int32),
                ("pti_messages_received", ctypes.c_int32),
                ("pti_syscalls_mach", ctypes.c_int32),
                ("pti_syscalls_unix", ctypes.c_int32),
                ("pti_csw", ctypes.c_int32),
                ("pti_threadnum", ctypes.c_int32),
                ("pti_numrunning", ctypes.c_int32),
                ("pti_priority", ctypes.c_int32),
            ]

        self._ProcBsdinfo = ProcBsdinfo
        self._ProcTaskinfo = ProcTaskinfo
        lib = self._lib
        lib.proc_listpids.argtypes = [ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_int]
        lib.proc_listpids.restype = ctypes.c_int
        lib.proc_pidinfo.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_uint64, ctypes.c_void_p, ctypes.c_int]
        lib.proc_pidinfo.restype = ctypes.c_int

    def list_pids(self) -> list[int]:
        ctypes = self._ctypes
        size = self.PROC_PIDLISTFD_SIZE
        buf = (ctypes.c_int * (size // ctypes.sizeof(ctypes.c_int)))()
        n = self._lib.proc_listpids(self.PROC_ALL_PIDS, 0, buf, size)
        if n <= 0:
            return []
        count = n // ctypes.sizeof(ctypes.c_int)
        return [int(buf[i]) for i in range(count) if buf[i] > 0]

    def bsd_info(self, pid: int) -> dict | None:
        ctypes = self._ctypes
        info = self._ProcBsdinfo()
        n = self._lib.proc_pidinfo(
            pid, self.PROC_PIDTBSDINFO, 0, ctypes.byref(info), ctypes.sizeof(info)
        )
        if n <= 0:
            return None
        return {
            "ppid": info.pbi_ppid,
            "pgid": info.pbi_pgid,
            "start_identity": f"{info.pbi_start_tvsec}.{info.pbi_start_tvusec}",
            "comm": info.pbi_comm.decode("utf-8", "replace"),
            "state": int(info.pbi_status),
        }

    def task_info(self, pid: int) -> dict | None:
        ctypes = self._ctypes
        info = self._ProcTaskinfo()
        n = self._lib.proc_pidinfo(
            pid, self.PROC_PIDTASKINFO, 0, ctypes.byref(info), ctypes.sizeof(info)
        )
        if n <= 0:
            return None
        return {
            "rss_bytes": int(info.pti_resident_size),
            "cpu_seconds": (info.pti_total_user + info.pti_total_system) / 1_000_000_000.0,
        }


class LibProcUnavailable(RuntimeError):
    """libproc cannot be loaded on this host (non-macOS or missing library)."""


class MacProcReader:
    """libproc-backed :class:`ProcessReader` (macOS).

    Enumerates every process with ``proc_listpids`` in one pass and reads
    parent/start identity + RSS/CPU per process with ``proc_pidinfo``. Raises
    :class:`LibProcUnavailable` when the host cannot provide libproc — the
    probe treats that as census-unavailable (never a fabricated census).
    """

    def __init__(self, libproc: _LibProc | None = None) -> None:
        self._libproc = libproc or _LibProc()

    def read_all(self) -> list[ProcessObservation]:
        obs: list[ProcessObservation] = []
        for pid in self._libproc.list_pids():
            bsd = self._libproc.bsd_info(pid)
            if bsd is None:
                continue  # exited during the pass
            task = self._libproc.task_info(pid) or {}
            obs.append(
                ProcessObservation(
                    pid=pid,
                    ppid=bsd["ppid"],
                    pgid=bsd["pgid"],
                    start_identity=bsd["start_identity"],
                    rss_bytes=task.get("rss_bytes"),
                    cpu_seconds=task.get("cpu_seconds"),
                    comm=bsd.get("comm", ""),
                    state=bsd.get("state"),
                )
            )
        return obs

    def start_identity(self, pid: int) -> str | None:
        bsd = self._libproc.bsd_info(pid)
        return bsd["start_identity"] if bsd else None

    def read_process(self, pid: int) -> ProcessObservation | None:
        """One observation for *pid* (``None`` when it vanished mid-read)."""
        bsd = self._libproc.bsd_info(pid)
        if bsd is None:
            return None
        task = self._libproc.task_info(pid) or {}
        return ProcessObservation(
            pid=pid,
            ppid=bsd["ppid"],
            pgid=bsd["pgid"],
            start_identity=bsd["start_identity"],
            rss_bytes=task.get("rss_bytes"),
            cpu_seconds=task.get("cpu_seconds"),
            comm=bsd.get("comm", ""),
            state=bsd.get("state"),
        )


# ── reader selection ─────────────────────────────────────────────────


def default_process_reader() -> ProcessReader:
    """The OS-shipped reader for the current platform.

    Linux -> :class:`LinuxProcReader`; macOS -> :class:`MacProcReader`;
    any other platform raises :class:`RuntimeError` — the factory's probe
    converts that into census-unavailable and selects the honest fallback.
    """
    if sys.platform == "linux":
        return LinuxProcReader()
    if sys.platform == "darwin":
        return MacProcReader()
    raise RuntimeError(f"no OS-shipped process reader for platform {sys.platform!r}")


# ── identity-safe census ─────────────────────────────────────────────


class ProcessTreeCensus:
    """Identity-safe descendant-tree census over one snapshot.

    ``descendants`` walks the parent map from *root_pid* and claims a
    process only when the root's recorded start identity still matches
    (PID-reuse safety). Group membership (for best-effort backends) is
    available via ``group_members``.
    """

    def __init__(self, reader: ProcessReader) -> None:
        self._reader = reader

    def reader(self) -> ProcessReader:
        return self._reader

    def descendants(
        self,
        root_pid: int,
        root_identity: str | None = None,
        *,
        include_root: bool = False,
    ) -> tuple[ProcessObservation, ...]:
        """Every descendant of *root_pid* in one snapshot.

        When *root_identity* is given and the observed root start identity
        differs, the census is **empty** — the PID was reused and no
        descendant can be safely attributed. A root that is not present in
        the snapshot yields an empty census (nothing alive to measure).
        """
        obs = self._reader.read_all()
        by_pid = {o.pid: o for o in obs}
        root = by_pid.get(root_pid)
        if root is None:
            return ()
        if root_identity is not None and root.start_identity != root_identity:
            return ()
        children: dict[int, list[ProcessObservation]] = {}
        for o in obs:
            if o.ppid is None or o.pid == root_pid:
                continue
            children.setdefault(o.ppid, []).append(o)
        found: list[ProcessObservation] = [root] if include_root else []
        stack = list(children.get(root_pid, []))
        seen = {root_pid}
        while stack:
            o = stack.pop()
            if o.pid in seen:
                continue
            seen.add(o.pid)
            found.append(o)
            stack.extend(children.get(o.pid, []))
        return tuple(found)

    def group_members(
        self,
        pgid: int,
        *,
        root_identity: str | None = None,
    ) -> tuple[ProcessObservation, ...]:
        """Every process currently in process group *pgid*.

        Used by the best-effort (macOS) backend to prove group ownership
        before signaling and to enumerate group residue after TERM/KILL.
        """
        obs = self._reader.read_all()
        return tuple(o for o in obs if o.pgid == pgid)

    def start_identity(self, pid: int) -> str | None:
        return self._reader.start_identity(pid)


# ── sampler ──────────────────────────────────────────────────────────


def sample_gaps(samples: Sequence[ResourceSample]) -> tuple[float, ...]:
    """Inter-sample gaps (seconds) between consecutive samples.

    Bounded by the sample count; cadence/gaps are recorded so sampled values
    are never presented as continuous truth. A single sample yields no gaps.
    """
    timestamps = [s.sampled_at for s in samples]
    return tuple(round(b - a, 6) for a, b in zip(timestamps, timestamps[1:]))


def merge_sample_peaks(
    samples: Sequence[ResourceSample],
) -> tuple[int | None, float | None, int | None]:
    """Sampled peaks over the sample series: (memory, cpu, process count).

    CPU is cumulative per process, so the CPU peak is the LAST sample's
    total; memory/process peaks are the max over samples. ``None`` when no
    sample carried a value — never a fabricated zero.
    """
    memory: int | None = None
    cpu: float | None = None
    process: int | None = None
    for s in samples:
        if s.memory_peak_bytes is not None:
            memory = s.memory_peak_bytes if memory is None else max(memory, s.memory_peak_bytes)
        if s.cpu_total_seconds is not None:
            cpu = s.cpu_total_seconds  # cumulative — last wins
        if s.process_count is not None:
            process = s.process_count if process is None else max(process, s.process_count)
    return memory, cpu, process


class DescendantSampler:
    """Portable sampler wired into the supervisor's sampler seam.

    Callable with a :class:`RunningHandle`: samples the identity-safe
    descendant tree of the handle's root PID at the supervisor's bounded
    cadence. Provenance is always ``sampled`` (never authoritative); an
    unreadable root yields an empty tree, a broken reader raises so the
    supervisor records a measurement failure (fail-closed).
    """

    def __init__(
        self,
        *,
        census: ProcessTreeCensus | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._census = census
        self._monotonic = monotonic

    def _ensure_census(self) -> ProcessTreeCensus:
        if self._census is None:
            self._census = ProcessTreeCensus(default_process_reader())
        return self._census

    def sample(self, running: RunningHandle) -> ResourceSample:
        census = self._ensure_census()
        members = census.descendants(
            running.root_pid, running.start_identity or None, include_root=True
        )
        rss = sum(o.rss_bytes for o in members if o.rss_bytes is not None)
        cpu = sum(o.cpu_seconds for o in members if o.cpu_seconds is not None)
        return ResourceSample(
            sampled_at=self._monotonic(),
            memory_peak_bytes=rss if rss or members else None,
            cpu_total_seconds=cpu if cpu or members else None,
            process_count=len(members) if members else None,
            provenance=MeasurementProvenance.SAMPLED,
        )
