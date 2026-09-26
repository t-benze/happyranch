"""Daemon-managed, system-default workspace cleanup scheduler (THR-195).

Founder ruling (THR-195 seq 129): workspace cleanup is a **daemon-managed,
system-default capability** that runs on its own without user configuration and
independent of all user Schedules. The daemon periodically measures each agent
workspace. When measurement is available, only a numeric result below the
1 GiB founder threshold skips. A bounded timeout, error, or cap/truncation
result that makes measurement unavailable bypasses only numeric threshold
evaluation; otherwise-due spawning continues with honest unavailable advisory
context. The daemon triggers an ordinary root task for that owning agent when
the remaining TASK-5552 / THR-195 contract says cleanup investigation is
warranted. It never uses,
creates, or modifies a Schedule, never injects anything into the shared
session-prompt seam, and never performs cleanup itself.

This is the "sixth loop" of the same shape as ``dream_scheduler`` /
``schedule_scheduler`` / ``zombie_reaper`` (one new module, one registration in
``runtime/daemon/app.py``). The measurement core is adopted from the retained
TASK-5974/TASK-5986/TASK-6016 work (deadline + cardinality-cap fixes
included); the rejected Schedule-prompt-seam architecture is not carried
forward.

Founder-approved product defaults (TASK-6036, resolving the TASK-6029 stop
analysis): daemon-managed system cleanup independent of user Schedules; daily
local 03:30 in each org's timezone; measure per agent; only an available
numeric result below 1 GiB skips, while a bounded timeout, error, or
cap/truncation result that is unavailable bypasses only numeric threshold
evaluation and otherwise-due spawning continues with honest unavailable
advisory context; the first TWO triggered runs per
agent are strictly report-only; each triggered ordinary task is assigned to
its owning agent; suppress while that agent has a non-terminal cleanup task OR
a marker in the current window; one durable founder-visible report
thread PER AGENT via the existing participant-authorized thread-send path with
NO minted report token; one enabled-by-default kill switch (an org config
``workspace_cleanup.enabled`` flag).

Persistence uses only existing durable mechanisms (no schema/API/CLI change):
the per-agent triggered-run ordinal and current-window dedup are derived from
the owning agent's daemon-marked cleanup task rows via a complete read-only
marker-history summary (``Database.summarize_workspace_cleanup_marker_history``
— rowid keyset pages over the SQL-side ``brief`` prefix filter, exact count and
UTC-maximum with no logical cutoff, so an older unfinished row can never be
hidden and the ordinal never saturates; a lookup failure is
represented as indeterminate so triggering fails closed). The per-agent
durable thread identity is resolved by daemon-cleanup provenance
(``composed_from_task_id`` → the agent's daemon-marked cleanup task) plus the
fixed per-agent subject, participant membership of the owning agent, open
status, and the daemon's distinctive opening message — a fixed subject alone
is not identity, and a user-created subject collision is never selected. The
identity lookup is TRI-STATE (found / absent / indeterminate): only an
authoritative absence may create a thread; any lookup error fails closed —
no duplicate thread, no task, no enqueue — with an audited reason (TASK-6046
finding 2). On an agent's first trigger the report thread (row, participant,
opening message, turn, audits) and the cleanup task are created in ONE
atomic transaction (``Database.insert_cleanup_report_thread_and_task``) —
any failure rolls back every durable row (zero residue), nothing is
enqueued, and a later retry succeeds exactly once (TASK-6046 finding 1).

Contract-relevant bounds (all documented in the workspace-cleanup behavior tests):

- Cadence: daily local 03:30 in the org's effective timezone.  Existing
  occurrences delimit half-open windows compared as UTC instants; a
  nonexistent spring-forward wall time is skipped and an ambiguous fall-back
  wall time takes the first (fold=0) instance. A live scheduler evaluates the
  boundary once when its UTC scan cursor crosses it, so polling phase and
  bounded processing drift cannot skip it. Startup performs exactly one
  current-window catch-up after warm-up, never an earlier historical backfill;
  a delayed/multi-day scan evaluates only the single latest due window.
- Trigger: the latest due occurrence is unserviced for the agent (no marker
  at/after it) AND no prior cleanup task of that agent anywhere in the
  complete history is non-terminal (TASK-5552 §3 "one run at a time"). There
  is no rolling 24-hour or seven-day trigger cooldown. Only an available
  numeric result below 1 GiB
  skips; a bounded timeout, error, or cap/truncation result that is unavailable
  bypasses only numeric threshold evaluation, and otherwise-due spawning
  continues with honest unavailable advisory context.
- Report-only rollout: the first TWO triggered runs per agent are STRICTLY
  report-only (daemon-composed REPORT-ONLY brief — inventory and nothing
  else). From the third triggered run onward the daemon composes the approved
  TASK-5552 §4 fixed normalized cleanup brief (bounded, Git-aware, non-force,
  action-time-re-derived eligibility). The advisory block itself never
  authorizes removal in either variant.
- Advisory content: the packed block is aggregate-only sizing/status context,
  prominently ADVISORY / STALE ON ARRIVAL / not an eligibility or candidate
  list / no path safe / no removal recommended / re-derive before any action.
  It never enumerates paths and never uses pending jobs or
  ``blocked_on_job_ids`` as liveness.
- Measurement is explicitly bounded (wall-clock deadline, traversal and
  subprocess caps) and fail-open: every timeout/error/cap hit yields an
  explicit unavailable/truncated status and can never block daemon operation
  or task/session spawning.
- Reporting: the responsible agent reports to the founder in ONE durable
  founder-visible thread per agent (fixed per-agent subject; created by the
  daemon on first trigger with the owning agent as composer/participant and
  @founder as recipient). The daemon passes only the thread id in the brief —
  NO minted invocation token. The agent appends its report during the task
  session via the existing participant-authorized, task-bound
  ``happyranch threads send`` path (composer + task_id + session_id binding).
  Silence on that thread is the loop-stopped signal.
- Kill switch: ``workspace_cleanup.enabled`` in the org ``config.yaml``
  (default True). Setting it to false disables the whole capability for that
  org. This is an existing daemon/org config mechanism — no new public
  API/CLI/UI surface.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone, tzinfo
from datetime import time as _dt_time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from runtime.models import TaskRecord, TaskStatus, ThreadStatus
from runtime.daemon.task_scratch_report import report_registered_agent_task_scratch

if TYPE_CHECKING:
    from runtime.daemon.org_state import OrgState
    from runtime.daemon.sessions import SessionTracker
    from runtime.daemon.state import DaemonState
    from runtime.infrastructure.database import Database
    from runtime.orchestrator._paths import OrgPaths

logger = logging.getLogger("happyranch.daemon.workspace_cleanup_scheduler")

# ---------------------------------------------------------------------------
# Founder-approved cadence + per-agent policy (TASK-6036 defaults)
# ---------------------------------------------------------------------------

# Daily occurrence: local 03:30 in the org's effective timezone.  The wall
# time is resolved per civil date (``_local_occurrence_utc``): a nonexistent
# spring-forward wall time is skipped and an ambiguous fall-back wall time
# takes the FIRST instance (``fold=0``), mirroring
# ``schedule_rules._localize_or_skip``.
_OCCURRENCE_TIME = _dt_time(hour=3, minute=30)

# Inclusive civil-date lookback for the occurrence search: today through
# today-3.  Exhaustion fails the decision closed (no fabricated occurrence).
_MAX_OCCURRENCE_LOOKBACK_DAYS = 3

# Per-agent trigger threshold: trigger only when the owning agent's workspace
# total is >= 1 GiB (founder default).
_MIN_WORKSPACE_TRIGGER_BYTES = 1024 ** 3

# First TWO triggered runs per agent are strictly report-only; from the third
# run onward the daemon composes the approved TASK-5552 §4 cleanup brief.
_REPORT_ONLY_RUN_LIMIT = 2

# Scheduler tick interval (cheap decision scan, mirrors schedule_scheduler).
_LOOP_INTERVAL_SECONDS = 60

# Boot warm-up grace before the first trigger scan (mirrors zombie_reaper's
# ``STALE_HEARTBEAT_SECONDS``-based warm-up). The daemon settles after a
# restart (dashboard warm, job recovery, producer wiring) before the cleanup
# loop may enqueue work; a daily occurrence is unaffected by a 30s delay.
# It also keeps short-lived daemon-lifespan test contexts free of unexpected
# trigger side effects.
_WARM_UP_SECONDS = 30.0

# Fixed marker the daemon writes at the top of every cleanup brief (both the
# report-only and the cleanup variant). Used ONLY to identify daemon-created
# cleanup tasks for dedup/run-count bookkeeping — it is the daemon's
# own content, never a user-content heuristic (TASK-5552 §3 "the fixed
# cleanup task marker").
_CLEANUP_BRIEF_MARKER = "HAPPYRANCH SYSTEM WORKSPACE CLEANUP RUN (daemon-triggered)"

# Per-agent durable founder-visible report thread. The fixed per-agent subject
# is one component of the daemon's durable thread identity per agent (no new
# schema — consultant THR-195 seq 131: "one durable thread, not one per run" —
# one per AGENT per the founder default). Identity is NOT the subject alone:
# a thread is the agent's report thread only when the subject matches AND its
# daemon-cleanup provenance resolves (composed_from_task_id → one of the
# agent's daemon-marked cleanup tasks) AND the owning agent is a participant
# AND it is open (TASK-6043 finding 4).
_REPORT_THREAD_SUBJECT_PREFIX = "HappyRanch system workspace cleanup reports"

# Distinctive daemon-composed opening line of every daemon report thread. A
# user-created thread with the same subject (subject collision) never carries
# this exact opening, so provenance is checked on the thread itself, not just
# its subject.
_REPORT_THREAD_OPENING_PREFIX = "Daemon-managed workspace cleanup reporting thread for"

# PROVENANCE-ONLY bound: `_find_report_thread` scans at most this many newest
# daemon-marked cleanup task rows to resolve the per-agent report thread.  It
# is NOT the trigger-ordinal/dedup bound any more — the daily trigger
# decision/ordinal use the complete read-only marker-history summary
# (`Database.summarize_workspace_cleanup_marker_history`), which has no logical
# cutoff.  A report thread whose provenance lies beyond this bound is not found
# (disclosed, unchanged limitation).
_MAX_CLEANUP_TASK_SCAN = 1000


# ── measurement bounds ────────────────────────────────────────────────────
# Tight, explicit bounds: the advisory walk must never stall the daemon loop.
# The entry/depth caps are calibrated against real org workspaces (a 4.6 GiB
# workspace with node_modules/.venv trees measures ~300k entries at depth <=15
# in <1s on the live host); the wall-clock deadline is the hard bound.
_MEASURE_DEADLINE_SECONDS = 10.0
_GIT_TIMEOUT_SECONDS = 5.0
_MAX_WORKSPACES = 64
_MAX_REPOS_PER_WORKSPACE = 16
_MAX_WORKTREES_PER_REPO = 256
_MAX_ENTRIES_PER_WORKSPACE = 500_000
_MAX_DEPTH = 20
_TOP_WORKSPACES = 3
_INODE_ALERT_PERCENT = 90.0

_DEPENDENCY_DIR_NAMES = frozenset({"node_modules", ".venv"})
_WORKTREE_NAME_RE = re.compile(r"^TASK-(\d+)")

# Terminal task statuses for the worktree→task join. Mirrors the canonical
# authority ``run_step.TERMINAL_STATES``; a parity test keeps them in lockstep
# without an import cycle (run_step pulls in the orchestrator at TYPE_CHECKING
# only, but this module must stay importable by it).
_TERMINAL_TASK_STATUSES = frozenset({
    TaskStatus.COMPLETED,
    TaskStatus.FAILED,
    TaskStatus.SUPERSEDED,
    TaskStatus.CANCELLED,
})


@dataclass
class WorkspaceContextSnapshot:
    """One bounded, fail-open workspace-disk snapshot (advisory only).

    Scoped to ONE agent workspace (the owning agent of a trigger candidate):
    ``workspaces_count`` is 1, ``largest`` carries the single
    ``(agent_name, total_bytes)`` entry, and the worktree/dependency counts
    cover that workspace.
    """

    available: bool = True
    reason: str | None = None          # set when ``available`` is False
    measured_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    workspaces_count: int = 0
    workspaces_bytes: int = 0
    largest: list[tuple[str, int]] = field(default_factory=list)  # (name, bytes)
    workspaces_unmeasured: int = 0
    truncated: bool = False
    worktrees_registered: int = 0
    worktrees_terminal: int = 0
    worktrees_non_terminal: int = 0
    worktrees_unclassified: int = 0
    dep_dirs: int = 0
    dep_bytes: int = 0
    dep_dirs_in_worktrees: int = 0
    dep_bytes_in_worktrees: int = 0
    live_sessions_count: int = 0
    live_sessions_agents: list[str] = field(default_factory=list)
    inode_available: bool = True
    inode_reason: str | None = None
    inode_used: int = 0
    inode_free: int = 0
    inode_total: int = 0
    inode_percent: float = 0.0
    inode_threshold_state: str = "ok"

    def unavailable(
        self, reason: str, *, truncated: bool = False,
    ) -> "WorkspaceContextSnapshot":
        return WorkspaceContextSnapshot(
            available=False, reason=reason, measured_at=self.measured_at,
            truncated=truncated,
        )


# ── formatting helpers ────────────────────────────────────────────────────

def _fmt_bytes(n: int) -> str:
    """1024-based human size (e.g. ``4.8 GiB``, ``733 MiB``, ``42 B``)."""
    value = float(n)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024 or unit == "TiB":
            if unit == "B":
                return f"{int(value)} B"
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TiB"


_ADVISORY_WARNING = (
    "This block is ADVISORY SIZING CONTEXT packed fresh at trigger time. "
    "It is STALE ON ARRIVAL. It is NOT an eligibility list and NOT a "
    "candidate list. No path or fact here is labelled safe, and nothing here "
    "recommends or authorizes removal. Re-derive every path and every fact "
    "independently and immediately before any action."
)


def format_workspace_context_note(snapshot: WorkspaceContextSnapshot) -> str:
    """Render the advisory workspace-disk note.

    Always carries the advisory/stale/non-candidate/re-derive warning. The
    unavailable variant states explicitly that no sizing data was packed and
    that this failure does not affect the run.
    """
    lines = [
        "## Workspace disk context — daemon-measured, ADVISORY ONLY",
        "",
        _ADVISORY_WARNING,
        "",
    ]
    if not snapshot.available:
        state = (
            "measurement truncated/unavailable"
            if snapshot.truncated else "measurement unavailable"
        )
        lines.append(
            f"  {state} at trigger time: {snapshot.reason or 'unknown'}"
        )
        lines.append(
            "  No sizing data was packed. This advisory failure does not affect this run."
        )
        return "\n".join(lines)

    largest = " / ".join(
        f"{name} ({_fmt_bytes(size)})"
        for name, size in snapshot.largest[: _TOP_WORKSPACES]
    ) or "n/a"
    lines.append(f"  measured_at:        {snapshot.measured_at}")
    lines.append(
        f"  workspace total:    {_fmt_bytes(snapshot.workspaces_bytes)} "
        f"({snapshot.workspaces_count} workspace)"
    )
    lines.append(f"  largest:            {largest}")
    lines.append(
        f"  worktrees:          {snapshot.worktrees_registered} registered — "
        f"{snapshot.worktrees_terminal} terminal-task, "
        f"{snapshot.worktrees_non_terminal} non-terminal, "
        f"{snapshot.worktrees_unclassified} unclassified"
    )
    lines.append(
        f"  dependency dirs:    {snapshot.dep_dirs} / {_fmt_bytes(snapshot.dep_bytes)}"
    )
    lines.append(
        f"  — inside worktrees: {snapshot.dep_dirs_in_worktrees} / "
        f"{_fmt_bytes(snapshot.dep_bytes_in_worktrees)}"
    )
    agents = ", ".join(snapshot.live_sessions_agents) or "none"
    lines.append(
        f"  live sessions:      {snapshot.live_sessions_count} ({agents})"
    )
    if snapshot.inode_available:
        lines.append(
            f"  temp filesystem inodes: used={snapshot.inode_used} "
            f"free={snapshot.inode_free} total={snapshot.inode_total} "
            f"percent={snapshot.inode_percent:.1f}% "
            f"threshold={snapshot.inode_threshold_state}"
        )
        if snapshot.inode_threshold_state == "alert":
            lines.append(
                "  inode action: inspect temporary-file producers and filesystem usage; "
                "this alert is advisory and not cleanup authority."
            )
    else:
        lines.append(
            f"  temp filesystem inodes: unavailable ({snapshot.inode_reason or 'unknown'}); "
            "workspace measurement continues."
        )
    if snapshot.workspaces_unmeasured:
        lines.append(
            f"  note: {snapshot.workspaces_unmeasured} workspace(s) could not be "
            "measured at trigger time; their sizes are not included."
        )
    return "\n".join(lines)


# ── bounded walk ──────────────────────────────────────────────────────────

@dataclass
class _WorkspaceWalk:
    bytes_total: int = 0
    entries: int = 0
    truncated: bool = False
    timed_out: bool = False
    errors: int = 0
    dep_count: int = 0
    dep_bytes: int = 0
    dep_in_wt_count: int = 0
    dep_in_wt_bytes: int = 0


def _in_worktrees(path: Path) -> bool:
    parts = path.parts
    return any(
        p == ".claude" and i + 1 < len(parts) and parts[i + 1] == "worktrees"
        for i, p in enumerate(parts)
    )


def _walk_workspace(
    workspace: Path,
    *,
    deadline: float,
    max_entries: int,
    max_depth: int,
) -> _WorkspaceWalk:
    """Bounded aggregate walk of one workspace directory.

    Never follows symlinks (cycle/shared-target safety), never descends past
    ``max_depth``, stops at ``max_entries``, and honors the wall-clock
    ``deadline``. Apparent size = sum of regular-file ``st_size``. Unreadable
    subtrees are counted as errors and skipped — the walk never raises.
    """
    stats = _WorkspaceWalk()
    stack: list[tuple[Path, int, bool, bool]] = [(workspace, 0, False, False)]
    while stack:
        if time.monotonic() > deadline:
            stats.truncated = True
            stats.timed_out = True
            break
        current, depth, in_dep, dep_in_wt = stack.pop()
        if depth > max_depth:
            stats.truncated = True
            break
        try:
            with os.scandir(current) as it:
                entries = list(it)
        except OSError:
            stats.errors += 1
            continue
        for entry in entries:
            stats.entries += 1
            if stats.entries > max_entries:
                stats.truncated = True
                break
            try:
                is_symlink = entry.is_symlink()
                is_dir = entry.is_dir(follow_symlinks=False)
            except OSError:
                stats.errors += 1
                continue
            if is_symlink:
                continue  # never traverse symlinked subtrees
            child = Path(entry.path)
            if is_dir:
                is_dep = entry.name in _DEPENDENCY_DIR_NAMES
                child_in_wt = dep_in_wt or (is_dep and _in_worktrees(child))
                if is_dep:
                    stats.dep_count += 1
                    if _in_worktrees(child):
                        stats.dep_in_wt_count += 1
                stack.append((child, depth + 1, in_dep or is_dep, child_in_wt))
            else:
                try:
                    size = entry.stat(follow_symlinks=False).st_size
                except OSError:
                    stats.errors += 1
                    continue
                stats.bytes_total += size
                if in_dep:
                    stats.dep_bytes += size
                    if dep_in_wt:
                        stats.dep_in_wt_bytes += size
    return stats


# ── registered-worktree enumeration + task-status join ───────────────────

def _git_worktree_paths(repo_dir: Path, timeout: float) -> tuple[list[Path], bool]:
    """Registered linked-worktree paths for one repo (``git worktree list``).

    Returns ``(paths, truncated)`` where ``truncated`` is True when the repo
    registered more than ``_MAX_WORKTREES_PER_REPO`` linked worktrees, so a
    caller never presents a partial aggregate as complete. The primary
    checkout itself is never included. Any git failure or timeout yields
    ``([], False)`` (fail-open).
    """
    try:
        result = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            cwd=str(repo_dir),
            capture_output=True,
            timeout=timeout,
        )
    except (subprocess.TimeoutExpired, subprocess.SubprocessError, OSError):
        return [], False
    if result.returncode != 0:
        return [], False
    primary = repo_dir.resolve()
    paths: list[Path] = []
    for line in result.stdout.decode("utf-8", "replace").splitlines():
        if not line.startswith("worktree "):
            continue
        path = Path(line[len("worktree "):])
        try:
            if path.resolve() == primary:
                continue  # the primary checkout is not a linked worktree
        except OSError:
            pass
        paths.append(path)
    truncated = len(paths) > _MAX_WORKTREES_PER_REPO
    return paths[: _MAX_WORKTREES_PER_REPO], truncated


def _task_id_from_worktree_name(name: str) -> str | None:
    """Extract ``TASK-NNN`` from a worktree dir name, tolerating suffixes.

    Handles exact names (``TASK-5567``) and suffixed shapes observed in
    production (``TASK-5567-base691``, ``TASK-5829-base``,
    ``TASK-5603-baseline``) via a ``TASK-\\d+`` prefix match. A missing or
    unknown task id is classified conservatively as unclassified by the
    caller — never assumed terminal.
    """
    match = _WORKTREE_NAME_RE.match(name or "")
    if match is None:
        return None
    return f"TASK-{match.group(1)}"


@dataclass
class _WorktreeStats:
    registered: int = 0
    terminal: int = 0
    non_terminal: int = 0
    unclassified: int = 0
    timed_out: bool = False
    truncated: bool = False


def _registered_worktree_stats(
    workspace: Path, db: "Database", *, deadline: float,
) -> _WorktreeStats:
    """Aggregate registered worktrees of ONE agent workspace, joined to task
    status where the ``TASK-\\d+`` prefix resolves to a known task.

    Bounded by the shared wall-clock ``deadline``: every git subprocess
    receives ``min(_GIT_TIMEOUT_SECONDS, remaining)`` and expiry is re-checked
    after every subprocess and after the last repository, so a call begun just
    before the deadline can never publish a snapshot as available. Every
    cardinality-cap hit (repos/worktrees) sets ``truncated`` so partial
    aggregates are never presented as complete.
    """
    stats = _WorktreeStats()
    try:
        from runtime.orchestrator.workspace_adapters import (
            PersistentWorkspaceSetup,
        )
        repo_names = PersistentWorkspaceSetup.detect_repo_names(workspace)
    except OSError:
        repo_names = []
    if len(repo_names) > _MAX_REPOS_PER_WORKSPACE:
        stats.truncated = True
    for name in repo_names[: _MAX_REPOS_PER_WORKSPACE]:
        if time.monotonic() > deadline:
            stats.timed_out = True
            break
        repo_dir = workspace / "repos" / name
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            stats.timed_out = True
            break
        wt_paths, wt_truncated = _git_worktree_paths(
            repo_dir, min(_GIT_TIMEOUT_SECONDS, remaining),
        )
        if wt_truncated:
            stats.truncated = True
        if time.monotonic() > deadline:
            stats.timed_out = True
            break
        for wt_path in wt_paths:
            stats.registered += 1
            task_id = _task_id_from_worktree_name(wt_path.name)
            status = None
            if task_id is not None:
                try:
                    task = db.get_task(task_id)
                except Exception:
                    task = None
                status = getattr(task, "status", None)
            if status is None:
                stats.unclassified += 1
            elif status in _TERMINAL_TASK_STATUSES:
                stats.terminal += 1
            else:
                stats.non_terminal += 1
    if time.monotonic() > deadline:
        stats.timed_out = True
    return stats


def _iter_workspaces(paths: Any, *, offset: int = 0) -> tuple[list[Path], bool]:
    """One sorted batch of ``_MAX_WORKSPACES`` agent-workspace directories
    starting at ``offset``, plus a ``truncated`` flag set when more
    workspaces exist beyond this batch.

    The per-org trigger scan pages through batches so every registered agent
    workspace is processed (no alphabetical starvation beyond the first
    batch) while each enumeration call stays bounded (TASK-6043 finding 5).
    """
    try:
        workspaces_dir = paths.workspaces_dir
        if not workspaces_dir.exists():
            return [], False
        all_workspaces = sorted(
            (
                p for p in workspaces_dir.iterdir()
                if p.is_dir() and not p.is_symlink()
            ),
            key=lambda p: p.name,
        )
        batch = all_workspaces[offset:offset + _MAX_WORKSPACES]
        truncated = len(all_workspaces) > offset + _MAX_WORKSPACES
        return batch, truncated
    except OSError:
        return [], False


# ── live sessions (SessionTracker) ────────────────────────────────────────

def _live_sessions(sessions: "SessionTracker | None") -> tuple[int, list[str]]:
    if sessions is None:
        return 0, []
    try:
        active = sessions.iter_active()
    except Exception:
        return 0, []
    agents = sorted({agent for (_, agent, _) in active})
    return len(active), agents


# ── public measurement (never raises) ─────────────────────────────────────

def measure_workspace_context(
    workspace_dir: Path,
    *,
    db: "Database",
    sessions: "SessionTracker | None" = None,
    deadline_seconds: float = _MEASURE_DEADLINE_SECONDS,
) -> WorkspaceContextSnapshot:
    """Measure one bounded advisory snapshot of a single agent workspace.
    NEVER raises.

    Fail-open: a missing workspace dir measures as an empty available
    snapshot; a deadline that fires before the walk completes, any traversal
    cap hit, or any unexpected error yields ``available=False`` with a reason.
    Partial numbers from a deadline/cap hit are never presented as a complete
    measurement.
    """
    deadline = time.monotonic() + max(0.001, deadline_seconds)
    try:
        snapshot = _measure(
            workspace_dir=workspace_dir, deadline=deadline,
            db=db, sessions=sessions,
        )
        _observe_inodes(snapshot)
        return snapshot
    except Exception as exc:  # pragma: no cover — defensive, fail-open
        return WorkspaceContextSnapshot(
            available=False, reason=f"measurement error: {exc}",
        )


def _observe_inodes(snapshot: WorkspaceContextSnapshot) -> None:
    """Add fail-open statvfs inode telemetry for the temp filesystem."""
    try:
        values = os.statvfs(tempfile.gettempdir())
        total = int(values.f_files)
        free = int(values.f_favail)
        if total <= 0 or free < 0 or free > total:
            raise ValueError("invalid statvfs inode counters")
        used = total - free
        percent = used * 100.0 / total
        snapshot.inode_used = used
        snapshot.inode_free = free
        snapshot.inode_total = total
        snapshot.inode_percent = percent
        snapshot.inode_threshold_state = (
            "alert" if percent >= _INODE_ALERT_PERCENT else "ok"
        )
    except Exception as exc:
        snapshot.inode_available = False
        snapshot.inode_reason = str(exc)


def _measure(
    *, workspace_dir: Path, deadline: float, db: "Database",
    sessions: "SessionTracker | None",
) -> WorkspaceContextSnapshot:
    snap = WorkspaceContextSnapshot()
    if not workspace_dir.exists():
        # A missing workspace dir measures as an empty, complete snapshot
        # (0 bytes < threshold → no trigger).
        return snap
    walk = _walk_workspace(
        workspace_dir,
        deadline=deadline,
        max_entries=_MAX_ENTRIES_PER_WORKSPACE,
        max_depth=_MAX_DEPTH,
    )
    if walk.truncated:
        # Partial numbers from a deadline/cap hit are never presented as a
        # complete measurement. The trigger still fails open and carries
        # this honest truncated advisory state into the spawned task.
        return snap.unavailable(
            "workspace measurement did not complete within bounded limits "
            "(deadline exceeded)"
            if walk.timed_out else
            "workspace measurement did not complete within bounded limits "
            "(traversal cardinality cap exceeded)",
            truncated=True,
        )
    if walk.errors:
        return snap.unavailable("workspace could not be measured (unreadable)")

    snap.workspaces_count = 1
    snap.workspaces_bytes = walk.bytes_total
    snap.largest = [(workspace_dir.name, walk.bytes_total)]
    snap.dep_dirs = walk.dep_count
    snap.dep_bytes = walk.dep_bytes
    snap.dep_dirs_in_worktrees = walk.dep_in_wt_count
    snap.dep_bytes_in_worktrees = walk.dep_in_wt_bytes

    wt = _registered_worktree_stats(workspace_dir, db, deadline=deadline)
    snap.worktrees_registered = wt.registered
    snap.worktrees_terminal = wt.terminal
    snap.worktrees_non_terminal = wt.non_terminal
    snap.worktrees_unclassified = wt.unclassified
    if wt.timed_out or wt.truncated:
        return snap.unavailable(
            "workspace measurement did not complete within bounded limits "
            "(deadline exceeded)"
            if wt.timed_out else
            "workspace measurement did not complete within bounded limits "
            "(worktree cardinality cap exceeded)",
            truncated=True,
        )

    snap.live_sessions_count, snap.live_sessions_agents = _live_sessions(sessions)
    return snap


# ── daily occurrence + per-agent trigger decision ─────────────────────────

def _local_occurrence_utc(local_date: date, tz: tzinfo) -> datetime | None:
    """Return the UTC instant of local 03:30 on ``local_date``.

    ``None`` when that wall time does not exist (a spring-forward gap).  An
    ambiguous (fall-back) wall time takes the FIRST instance (``fold=0``),
    mirroring ``schedule_rules._localize_or_skip``.
    """
    naive = datetime.combine(local_date, _OCCURRENCE_TIME)
    candidate = naive.replace(tzinfo=tz, fold=0)
    back = candidate.astimezone(timezone.utc).astimezone(tz)
    if back.replace(tzinfo=None) != naive or back.fold != 0:
        # The wall time does not exist (normalization moved it) or the only
        # available instance is the second (fold=1) one.
        return None
    return candidate.astimezone(timezone.utc)


def _latest_due_occurrence_utc(
    now_utc: datetime,
    tz: tzinfo,
    *,
    lookback_days: int = _MAX_OCCURRENCE_LOOKBACK_DAYS,
) -> datetime | None:
    """Latest existing local 03:30 at-or-before ``now_utc``.

    Iterates civil dates today through today-``lookback_days`` inclusive in the
    effective timezone and returns the first existing occurrence whose UTC
    instant is not in the future.  Returns ``None`` when no date in the
    inclusive window has an existing occurrence (the caller fails closed).
    """
    now_utc = _as_aware_utc(now_utc)
    today_local = now_utc.astimezone(tz).date()
    for delta in range(0, lookback_days + 1):
        occurrence = _local_occurrence_utc(
            today_local - timedelta(days=delta), tz,
        )
        if occurrence is not None and occurrence <= now_utc:
            return occurrence
    return None


def _as_aware_utc(value: datetime) -> datetime:
    """Normalize a TaskRecord timestamp (naive UTC or aware) to aware UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


@dataclass
class _CleanupTaskHistory:
    """One agent's complete daemon-marked cleanup-task summary.

    ``newest_utc`` feeds the current-window dedup, ``count`` is the exact
    per-agent "triggered runs so far" (first-two report-only bookkeeping, with
    no saturation/reset), and ``has_unfinished`` reports whether ANY marker row
    in the complete history is non-terminal.  All three derive from the
    read-only ``Database.summarize_workspace_cleanup_marker_history`` (rowid
    keyset pages over the tasks table, no logical cutoff).

    ``indeterminate`` is set when the history cannot be read authoritatively
    (lookup error).  Lifecycle/identity uncertainty must FAIL CLOSED for
    triggering: a dedup-blind daemon must never double-fire a run or reset the
    first-two counter (TASK-6043 finding 1).
    """

    newest_utc: datetime | None = None
    count: int = 0
    has_unfinished: bool = False
    indeterminate: bool = False


def _cleanup_task_history(
    db: "Database", agent: str,
) -> _CleanupTaskHistory:
    """Complete marker-filtered cleanup-task summary for one agent.

    Uses the additive read-only
    ``Database.summarize_workspace_cleanup_marker_history``: every
    daemon-marked cleanup task of the agent is counted, so a cleanup row can
    never be hidden behind newer ordinary tasks no matter how many exist, and
    an older unfinished row beyond any page remains visible.  Any lookup error
    is represented as ``indeterminate`` rather than as a clean "no history" —
    callers must suppress the trigger.
    """
    try:
        summary = db.summarize_workspace_cleanup_marker_history(
            _CLEANUP_BRIEF_MARKER,
            assigned_agent=agent,
        )
    except Exception:
        logger.exception(
            "workspace cleanup: task-history lookup failed for agent %s — "
            "suppressing trigger (fail closed)",
            agent,
        )
        return _CleanupTaskHistory(indeterminate=True)
    return _CleanupTaskHistory(
        newest_utc=summary.newest_created_at,
        count=summary.count,
        has_unfinished=summary.has_unfinished,
    )


@dataclass
class CleanupTriggerDecision:
    should_trigger: bool
    reason: str | None = None


def decide_cleanup_trigger(
    *,
    db: "Database",
    agent: str,
    now_utc: datetime | None = None,
    previous_scan_utc: datetime | None = None,
    tz: tzinfo | None = None,
    first_scan: bool = False,
) -> CleanupTriggerDecision:
    """Pure per-agent trigger decision for the daily cadence.

    Order: (1) resolve the latest existing due local 03:30 occurrence; ``None``
    fails closed as ``occurrence_search_exhausted``; (2) the crossing gate
    ``previous_scan_utc < occ <= now_utc`` (or the explicit ``first_scan``
    current-window catch-up); (3) ``history_indeterminate``; (4) any marker
    at/after the current occurrence suppresses as
    ``already_triggered_this_window``; (5) any unfinished marker suppresses as
    ``prior_run_in_flight``; (6) trigger.  No rolling cooldown exists.

    The >= 1 GiB size gate is applied by the caller after the fresh
    measurement (it needs the measured bytes, so it lives with the trigger).
    """
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)
    now_utc = _as_aware_utc(now_utc)
    effective_tz = tz or timezone.utc
    occurrence = _latest_due_occurrence_utc(now_utc, effective_tz)
    if occurrence is None:
        return CleanupTriggerDecision(False, "occurrence_search_exhausted")
    if not first_scan:
        if previous_scan_utc is None:
            previous_scan_utc = now_utc - timedelta(seconds=_LOOP_INTERVAL_SECONDS)
        previous_scan_utc = _as_aware_utc(previous_scan_utc)
        if not (previous_scan_utc < occurrence <= now_utc):
            return CleanupTriggerDecision(False, "not_due")

    history = _cleanup_task_history(db, agent)
    if history.indeterminate:
        # Lifecycle/identity uncertainty fails closed for triggering.
        return CleanupTriggerDecision(False, "history_indeterminate")
    if history.newest_utc is not None and history.newest_utc >= occurrence:
        return CleanupTriggerDecision(False, "already_triggered_this_window")
    if history.has_unfinished:
        return CleanupTriggerDecision(False, "prior_run_in_flight")
    return CleanupTriggerDecision(True, None)


# ── per-agent durable founder-report thread (no minted token) ─────────────

def report_thread_subject(agent: str) -> str:
    """Fixed per-agent subject of the durable cleanup-report thread."""
    return f"{_REPORT_THREAD_SUBJECT_PREFIX} — {agent}"


def _report_thread_opening(task_id: str, agent: str) -> str:
    """Daemon-composed opening line of the per-agent report thread."""
    return (
        f"Daemon-managed workspace cleanup reporting thread for {agent}. "
        f"Cleanup run {task_id} was triggered; the owning agent appends its "
        f"report here (one durable thread per agent)."
    )


@dataclass
class _ReportThreadResolution:
    """Tri-state outcome of the per-agent report-thread identity lookup.

    - ``found``: an authoritative thread matches every identity check.
    - ``absent``: the scan completed authoritatively with no match — ONLY
      this state may create a new thread.
    - ``indeterminate``: a lookup errored, so absence is NOT proven. The
      trigger must fail closed (no duplicate thread, no task, no enqueue)
      with an explicit audited reason (TASK-6046 finding 2).
    """

    state: str
    thread_id: str | None = None
    reason: str | None = None


def _find_report_thread(
    db: "Database", agent: str,
) -> _ReportThreadResolution:
    """Resolve this agent's durable cleanup-report thread by identity.

    A fixed subject alone is NOT identity (TASK-6043 finding 4). A thread is
    the agent's durable report thread only when ALL hold:

    - subject == the fixed per-agent subject (rejects unrelated threads),
    - daemon-cleanup provenance: ``composed_from_task_id`` resolves to one
      of this agent's daemon-marked cleanup tasks (SQL-side marker filter —
      no presentation-page bound can hide an older thread),
    - the owning agent is a participant (the participant-authorized,
      task-bound ``threads send`` path requires membership),
    - the thread is OPEN (a closed thread is never reused),
    - the opening message carries the daemon's distinctive composition text
      (rejects user-created subject collisions that otherwise match).

    Tri-state (TASK-6046 finding 2): a non-matching candidate is an
    authoritative negative, but ANY lookup error means absence is NOT proven
    — the result is ``indeterminate`` and the caller must fail closed rather
    than create a duplicate thread.
    """
    subject = report_thread_subject(agent)
    try:
        cleanup_tasks = db.list_tasks_by_brief_prefix(
            _CLEANUP_BRIEF_MARKER,
            assigned_agent=agent,
            limit=_MAX_CLEANUP_TASK_SCAN,
        )
    except Exception:
        logger.exception(
            "workspace cleanup: report-thread history lookup failed for "
            "agent %s — thread identity indeterminate (fail closed)",
            agent,
        )
        return _ReportThreadResolution(
            "indeterminate", reason="cleanup_history_lookup_failed",
        )
    for task in cleanup_tasks:  # newest cleanup task first
        try:
            candidates = db.list_threads_by_composed_from_task_id(task.id)
        except Exception:
            logger.exception(
                "workspace cleanup: report-thread provenance lookup failed "
                "for agent %s task %s — thread identity indeterminate "
                "(fail closed)",
                agent, task.id,
            )
            return _ReportThreadResolution(
                "indeterminate", reason="provenance_lookup_failed",
            )
        for thread in candidates:
            if thread.subject != subject:
                continue
            if thread.status is not ThreadStatus.OPEN:
                continue
            try:
                is_participant = db.is_thread_participant(thread.id, agent)
                opening = db.get_thread_message_by_seq(thread.id, 1)
            except Exception:
                logger.exception(
                    "workspace cleanup: report-thread membership/opening "
                    "lookup failed for agent %s thread %s — thread identity "
                    "indeterminate (fail closed)",
                    agent, thread.id,
                )
                return _ReportThreadResolution(
                    "indeterminate", reason="participant_or_opening_lookup_failed",
                )
            if not is_participant:
                # Authoritative negative: the owning agent is not a member.
                continue
            if opening is None or not (
                opening.body_markdown or ""
            ).startswith(_REPORT_THREAD_OPENING_PREFIX):
                # A subject-colliding thread without the daemon's opening
                # message is user-created — never selected.
                continue
            return _ReportThreadResolution("found", thread_id=thread.id)
    return _ReportThreadResolution("absent")


# ── brief composition ─────────────────────────────────────────────────────

_REPORT_INSTRUCTION = (
    "3. Report to the founder by appending to the durable founder-visible "
    "thread {thread_id} (\"{subject}\"). You are a participant of that thread "
    "(the daemon composed it with you as composer and @founder as "
    "recipient); no invocation token is needed. Use the existing "
    "participant-authorized, task-bound send path with your current task "
    "session:\n"
    "\n"
    "    happyranch threads send --org {slug} --thread-id {thread_id} "
    "--task-id {task_id} --session-id <YOUR_CURRENT_SESSION_ID> "
    "--from-file <payload>\n"
    "\n"
    "    payload JSON: {{\"composer\": \"{agent}\", \"body_markdown\": "
    "\"<report: measured before/after sizes, exact removals, skips, and any "
    "ambiguity>\"}}\n"
    "\n"
    "  Append to that thread — do NOT compose a new thread. If the thread is "
    "unusable, compose a founder-visible thread titled \"{subject}\" "
    "(recipient @founder) with the same report content instead."
)

_FALLBACK_REPORT_INSTRUCTION = (
    "3. Report to the founder by composing a founder-visible thread titled "
    "\"{subject}\" (recipient @founder) with the report content: measured "
    "before/after sizes, exact removals (none in report-only), skips, and any "
    "ambiguity. (The daemon could not resolve the durable per-agent report "
    "thread at trigger time; the first successful report establishes it.)"
)


def compose_cleanup_brief(
    *,
    org_slug: str,
    agent: str,
    task_id: str,
    run_number: int,
    snapshot: WorkspaceContextSnapshot,
    thread_id: str | None,
) -> str:
    """Daemon-composed brief for one triggered cleanup task of ``agent``.

    Always starts with the fixed daemon marker (dedup/run-count
    bookkeeping — TASK-5552 §3). The first ``_REPORT_ONLY_RUN_LIMIT`` runs per
    agent are STRICTLY report-only; later runs carry the approved TASK-5552
    §4 fixed normalized cleanup brief. Both pack the fresh advisory snapshot
    and the founder-thread reporting instruction. Never a Schedule brief;
    nothing is persisted beyond this task row.
    """
    report = (
        _REPORT_INSTRUCTION.format(
            thread_id=thread_id, subject=report_thread_subject(agent),
            slug=org_slug, task_id=task_id, agent=agent,
        )
        if thread_id
        else _FALLBACK_REPORT_INSTRUCTION.format(
            subject=report_thread_subject(agent),
        )
    )
    header = (
        _CLEANUP_BRIEF_MARKER,
        "",
        f"Daemon-triggered workspace cleanup run for agent {agent} (run "
        f"#{run_number}). This is a daemon-managed, system-default capability "
        "independent of all user Schedules. You are the responsible agent; "
        "you own this run.",
        "",
        "This run follows the shared `workspace-cleanup` system skill exactly "
        "(runtime/skills/bundled/workspace-cleanup/SKILL.md). Its manual-dispatch "
        "first line is `HAPPYRANCH SYSTEM WORKSPACE CLEANUP RUN "
        "(manual-dispatch)`; an unmarked manual request is inventory-only.",
        "",
    )
    if run_number <= _REPORT_ONLY_RUN_LIMIT:
        body = [
            "THIS RUN IS STRICTLY REPORT-ONLY (first-two per-agent rollout). "
            "Do NOT delete, prune, move, or modify any file, worktree, "
            "dependency directory, or workspace artifact. Do NOT create or "
            "modify any Schedule. No cleanup action is authorized in this "
            "run.",
            "",
            format_workspace_context_note(snapshot),
            "",
            "Work to do:",
            "",
            "1. Inventory your workspace read-only: registered linked "
            "worktrees, dependency directories, and task associations. "
            "Classify each worktree by its TASK-\\d+ prefix (suffixed names "
            "like TASK-5567-base691 resolve to TASK-5567); unknown or missing "
            "tasks are unclassified, never assumed terminal.",
            "2. Re-derive every fact and every path immediately before any "
            "action or recommendation. Write output/<task_id>/ with "
            "inventory.json, final-ledger.jsonl (all rows no-op), and "
            "report.md per the TASK-5552 cleanup design, including measured "
            "sizes, exact skips and reasons, and any ambiguity.",
        ]
    else:
        body = [
            "THIS RUN MAY PERFORM BOUNDED CLEANUP ACTIONS per the approved "
            "TASK-5552 §4 contract — but ONLY after the first two report-only "
            "runs, ONLY on action-time-re-derived eligibility, and ONLY with "
            "the exact non-force mechanisms below. Any uncertainty is a "
            "skip.",
            "",
            "Begin read-only and create output/<task_id>/inventory.json, "
            "final-ledger.jsonl, and report.md. Do not act on an inventory "
            "row. Immediately before each possible action, rebuild that "
            "exact row from current filesystem, Git, task/session, "
            "PR/protection, and OS process evidence; append the final row "
            "before action; act only when that same row says eligible. Any "
            "uncertainty is a skip.",
            "",
            "Current-use evidence follows the shared workspace-cleanup skill "
            "and the approved THR-259 seq171/seq185 rule: an authoritative "
            "recorded terminal status PLUS a fresh complete same-user process "
            "observation replaces separate live-session/task-to-process "
            "identity for terminal candidates, terminal cleanup peers, and the "
            "two prior joined terminal scheduled occurrences. Invoke the bundled "
            "read-only scripts/check_path_use.py helper only through the supported "
            "task-bound host-visible HappyRanch job path; authenticate its exact "
            "job receipt across task/session/job/status/exit/output/scanner fields, with no direct "
            "in-session fallback. Only clear_observation may act; blocked, unknown, "
            "rejected, failed, timed-out, capped, stale, mismatched, malformed, or "
            "missing job evidence always skips. The "
            "fixed login/session daemons (sshd-session, systemd --user, "
            "(sd-pam), ssh-agent, gpg-agent, gcr-ssh-agent) qualify only by "
            "exact readable process name AND exact bounded cgroup role and are "
            "deliberately uninspected; every other unreadable same-user "
            "process is unknown and skips. Pending job rows and "
            "blocked_on_job_ids are diagnostics only, never liveness proof.",
            "",
            "Allowed cache action: remove one literal real node_modules or "
            ".venv directory inside a registered, non-primary linked "
            "worktree of YOUR workspace, only when its immediate parent has "
            "the accepted lock/manifest, the owning task has been terminal "
            "for 24 hours, its commit is durably preserved with no open or "
            "closed-unmerged PR, the shared current-use job observation clears, "
            "path ownership is unambiguous, it is not a symlink/shared target, "
            "and it is not protected. For an otherwise dirty worktree, remove "
            "only that literal cache and prove tracked source bytes and Git "
            "status unchanged. Never remove or clean the dirty "
            "worktree, use a glob/parent root, or run git clean.",
            "",
            "Allowed whole-worktree action: after seven terminal days, remove "
            "one clean registered non-primary worktree only when the shared "
            "current-use observation clears, no open/unmerged PR uses it, "
            "HEAD is preserved by the existing accepted durable ref, a freshly "
            "verified matching remote task branch on origin, or a confirmed merged "
            "PR, and no protection applies. A merged PR preserves integrated "
            "content but may not preserve original commit topology. Use only "
            "git -C <primary> worktree remove <literal-path> without "
            "--force. Use git worktree prune only for an already-missing "
            "registered path after dry-run confirms the exact stale record.",
            "",
            "Never delete or mutate dirty work, detached/unreachable/"
            "local-only commits, broken or unregistered source-shaped paths, "
            "salvage residue, primary checkouts, workspace roots, "
            "output/memory/artifacts/config, databases/logs, canonical "
            "skills, or any unknown path. Record preservation "
            "recommendations only; do not auto-commit, archive, tag, bundle, "
            "move, repair, or quarantine them in this task.",
            "",
            "Record every candidate and skip reason; final pre-action proofs "
            "with timestamps; literal argv; exit; apparent and allocated "
            "bytes before/after; filesystem free space before/after; "
            "concurrent/unattributed delta separately; and protected-path "
            "postchecks. Stop further mutations on any action/evidence/"
            "report-write failure. Complete through the normal task contract "
            "with exact artifact path and honest zero-removal reporting.",
            "",
            format_workspace_context_note(snapshot),
        ]
    return "\n".join((*header, *body, "", report))


# ── trigger ───────────────────────────────────────────────────────────────

async def trigger_cleanup(
    org: "OrgState",
    *,
    agent: str,
    enqueue: Callable[[str, str], None],
    now_utc: datetime | None = None,
) -> str | None:
    """Create + enqueue one cleanup task for ``agent`` (the owning agent),
    packing the fresh advisory measurement and the per-agent report-thread
    seam into the daemon-composed brief.

    Fail-closed skip (returns None, never raises) when: the agent has no team
    in this org, an available fresh measurement is below the >= 1 GiB trigger
    threshold, or the agent's
    daemon-marked cleanup-task history cannot be read authoritatively
    (lifecycle/identity uncertainty fails closed). Each skip is audited so
    operators can see why no task was created.

    Atomic producer seam (TASK-6043 finding 3): the task id is allocated only
    AFTER every awaited step (the bounded measurement is done first), and
    allocation + thread identity resolution + brief composition + insertion
    run as one synchronous block under ``org.db_lock`` with no awaits between
    ``next_task_id`` and the insert — the same producer discipline as
    ``Orchestrator.create_task``. No id is ever selected before awaited work,
    so another producer cannot claim it mid-trigger and no collision can
    leave a report thread falsely linked to an unrelated task.

    Rollback-safe producer (TASK-6046 finding 1): on the FIRST trigger the
    report thread (row, participant, opening message, turn, audits) and the
    cleanup task it was composed from are created in ONE atomic
    transaction (``Database.insert_cleanup_report_thread_and_task``) — any
    failure rolls back EVERY durable row (zero residue), nothing is
    enqueued, and a later retry succeeds exactly once. When the thread
    already exists only the task is inserted, so an insert failure can never
    leave thread residue either. The inserted task is a clean root (no
    parent, no thread dispatch); every compensation path is audited.

    Fail-closed thread identity (TASK-6046 finding 2): the per-agent
    report-thread lookup is tri-state (found / absent / indeterminate). Only
    an authoritative absence may create the thread; a lookup error is
    ``indeterminate`` and suppresses the whole trigger — no duplicate
    thread, no task, no enqueue — with an explicit audited reason.
    """
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)

    from runtime.orchestrator._paths import OrgPaths

    team = org.teams.team_for_agent(agent)
    if team is None:
        logger.error(
            "workspace cleanup: agent %s has no team in org %s",
            agent, org.slug,
        )
        org.db.insert_audit_log(
            task_id="workspace-cleanup:skipped",
            agent=agent,
            action="workspace_cleanup_skipped",
            payload={"reason": "agent_team_unresolved", "agent": agent},
        )
        return None

    try:
        await asyncio.get_running_loop().run_in_executor(
            None,
            lambda: report_registered_agent_task_scratch(
                db=org.db, sessions=org.sessions, agent=agent,
                workspace=OrgPaths(root=org.root).workspaces_dir / agent,
            ),
        )
    except Exception:
        logger.exception("task scratch cleanup report failed for %s", agent)

    # Bounded, fail-open measurement of THE AGENT'S OWN workspace — the gate
    # for the >= 1 GiB founder threshold and the advisory snapshot packed at
    # trigger time. A disk gauge must never block the run; it runs in a
    # thread executor so the daemon event loop is never stalled by the
    # (deadline-bounded) traversal. NO task id is selected before this
    # awaited measurement (TASK-6043 finding 3).
    loop = asyncio.get_running_loop()
    snapshot = await loop.run_in_executor(
        None,
        lambda: measure_workspace_context(
            OrgPaths(root=org.root).workspaces_dir / agent,
            db=org.db,
            sessions=org.sessions,
        ),
    )
    if (
        snapshot.available
        and snapshot.workspaces_bytes < _MIN_WORKSPACE_TRIGGER_BYTES
    ):
        org.db.insert_audit_log(
            task_id="workspace-cleanup:skipped",
            agent=agent,
            action="workspace_cleanup_skipped",
            payload={
                "reason": "workspace_below_threshold",
                "agent": agent,
                "workspaces_bytes": snapshot.workspaces_bytes,
            },
        )
        return None

    # Per-agent triggered-run counter (first-two report-only bookkeeping),
    # derived from the owning agent's daemon-marked cleanup task rows via the
    # authoritative marker-filtered query. An indeterminate history (lookup
    # error) fails closed: a dedup-blind daemon must not double-fire, reset
    # the first-two counter (TASK-6043 finding 1).
    history = _cleanup_task_history(org.db, agent)
    if history.indeterminate:
        org.db.insert_audit_log(
            task_id="workspace-cleanup:skipped",
            agent=agent,
            action="workspace_cleanup_skipped",
            payload={"reason": "history_indeterminate", "agent": agent},
        )
        return None
    run_number = history.count + 1

    # Read-only thread-config pre-validation OUTSIDE the lock (KB
    # atomic-multi-table-persistence phase 1): the turn cap is a stable org
    # setting — never a TOCTOU target — and resolving it here keeps the
    # producer block purely a write path.
    from runtime.daemon.routes.threads import FOUNDER_LITERAL
    from runtime.orchestrator.org_config import (
        OrgConfig,
        resolve_org_setting_threads,
    )
    turn_cap = resolve_org_setting_threads(
        org.db, code_default=OrgConfig(),
    )["default_turn_cap"]

    # Rollback-safe atomic producer block: task-id allocation, tri-state
    # report-thread identity resolution, brief composition, and insertion all
    # run synchronously under org.db_lock with no awaits between
    # ``next_task_id`` and the insert. No minted token: the agent appends via
    # the participant-authorized, task-bound send path during its session. A
    # thread is only ever linked to the id this block actually inserts (never
    # a pre-selected id that another producer could claim).
    thread_id: str | None = None
    async with org.db_lock:
        task_id = org.db.next_task_id()
        # Tri-state identity: only authoritative absence may create a thread.
        # A lookup error is indeterminate — fail closed (no duplicate thread,
        # no task, no enqueue) with an explicit audited reason.
        resolution = _find_report_thread(org.db, agent)
        if resolution.state == "indeterminate":
            org.db.insert_audit_log(
                task_id="workspace-cleanup:skipped",
                agent=agent,
                action="workspace_cleanup_skipped",
                payload={
                    "reason": "report_thread_indeterminate",
                    "agent": agent,
                    "detail": resolution.reason,
                },
            )
            return None
        thread_id = resolution.thread_id
        if thread_id is None:
            # Allocate the thread id under the same lock the atomic write
            # uses — no other thread producer can interleave (the compose
            # routes hold org.db_lock across next_thread_id + insert).
            thread_id = org.db.next_thread_id()
        brief = compose_cleanup_brief(
            org_slug=org.slug,
            agent=agent,
            task_id=task_id,
            run_number=run_number,
            snapshot=snapshot,
            thread_id=thread_id,
        )
        try:
            if resolution.state == "absent":
                # First trigger for this agent: report thread + task in ONE
                # atomic transaction. On any failure EVERY row (thread,
                # participant, opening message, turns, audits, task) rolls
                # back — zero residue, no enqueue; a later retry succeeds
                # exactly once (TASK-6046 finding 1).
                org.db.insert_cleanup_report_thread_and_task(
                    thread_id=thread_id,
                    subject=report_thread_subject(agent),
                    composer=agent,
                    opening_body=_report_thread_opening(task_id, agent),
                    initial_recipients=[FOUNDER_LITERAL],
                    turn_cap=turn_cap,
                    task=TaskRecord(
                        id=task_id,
                        brief=brief,
                        team=team,
                        assigned_agent=agent,
                    ),
                )
            else:
                # Thread already exists: insert only the task (no thread work
                # that could leave residue on failure).
                org.db.insert_task(TaskRecord(
                    id=task_id,
                    brief=brief,
                    team=team,
                    assigned_agent=agent,
                ))
        except Exception:
            # Compensation: the atomic producer rolled back (or the plain
            # insert failed before any thread work). Zero durable residue, no
            # enqueue; the skip is audited loudly so operators see why no
            # task was created and a later retry succeeds cleanly.
            logger.exception(
                "workspace cleanup: task insert failed for org %s agent %s "
                "(task %s) — atomic producer rolled back, no run enqueued",
                org.slug, agent, task_id,
            )
            org.db.insert_audit_log(
                task_id="workspace-cleanup:skipped",
                agent=agent,
                action="workspace_cleanup_skipped",
                payload={
                    "reason": "task_insert_failed",
                    "agent": agent,
                    "task_id": task_id,
                },
            )
            return None
    enqueue(org.slug, task_id)

    org.db.insert_audit_log(
        task_id=task_id,
        agent=agent,
        action="workspace_cleanup_triggered",
        payload={
            "report_thread_id": thread_id,
            "measurement_available": snapshot.available,
            "measurement_reason": snapshot.reason,
            "measurement_truncated": snapshot.truncated,
            "run_number": run_number,
            "brief_kind": (
                "report_only" if run_number <= _REPORT_ONLY_RUN_LIMIT
                else "cleanup"
            ),
        },
    )
    logger.info(
        "workspace cleanup triggered for org %s: agent %s task %s (run #%s, "
        "thread %s)",
        org.slug, agent, task_id, run_number, thread_id,
    )
    return task_id


# ── per-org tick + async loop ─────────────────────────────────────────────

async def _tick_org(
    org: "OrgState",
    state: "DaemonState",
    now_utc: datetime,
    previous_scan_utc: datetime | None = None,
    first_scan: bool = False,
) -> None:
    """One org's per-agent decision+trigger pass. Never raises (loop-level
    isolation).

    Workspace enumeration is paged in bounded batches of ``_MAX_WORKSPACES``
    so ALL registered agent workspaces are reached — an org with more than
    one batch of agents never silently starves the alphabetically-later ones
    (TASK-6043 finding 5).
    """
    from runtime.orchestrator._paths import OrgPaths
    from runtime.orchestrator.org_config import (
        _resolve_timezone,
        load_org_config,
    )

    paths = OrgPaths(root=org.root)
    cfg = load_org_config(paths)
    if not cfg.workspace_cleanup_enabled:
        # Kill switch (enabled-by-default org config flag).
        return
    tz = _resolve_timezone(cfg.timezone)[0]

    offset = 0
    while True:
        workspaces, truncated = _iter_workspaces(paths, offset=offset)
        if not workspaces:
            break
        for workspace in workspaces:
            agent = workspace.name
            if org.teams.team_for_agent(agent) is None:
                # Only registered org agents own cleanup runs (skips special
                # dirs like ``_terminated`` and unregistered workspaces).
                continue
            decision = decide_cleanup_trigger(
                db=org.db,
                agent=agent,
                now_utc=now_utc,
                previous_scan_utc=previous_scan_utc,
                tz=tz,
                first_scan=first_scan,
            )
            if not decision.should_trigger:
                if decision.reason == "history_indeterminate":
                    org.db.insert_audit_log(
                        task_id="workspace-cleanup:skipped",
                        agent=agent,
                        action="workspace_cleanup_skipped",
                        payload={
                            "reason": "history_indeterminate",
                            "agent": agent,
                        },
                    )
                # Skip reasons are derivable from the tasks table; only the
                # boundary-level history failure above and the trigger itself
                # (including fail-closed skips inside trigger_cleanup) carry
                # audit rows, so the daily loop never spams the ledger.
                continue
            await trigger_cleanup(
                org,
                agent=agent,
                enqueue=lambda slug, tid: _enqueue_task(state, slug, tid),
                now_utc=now_utc,
            )
        if not truncated:
            break
        offset += _MAX_WORKSPACES


def _enqueue_task(state: "DaemonState", slug: str, task_id: str) -> None:
    from runtime.daemon.runner import enqueue_task
    enqueue_task(state, slug, task_id)


async def workspace_cleanup_scheduler_loop(
    state: "DaemonState", *,
    interval_seconds: int = _LOOP_INTERVAL_SECONDS,
    warm_up_seconds: float = _WARM_UP_SECONDS,
) -> None:
    """Daily workspace-cleanup trigger loop (THR-195 seq 129).

    Mirrors ``dream_scheduler_loop`` / ``zombie_reaper_loop``: per-tick
    per-org decision with a boot warm-up grace, exception isolation, and
    loop-tick metrics. Registered in ``runtime/daemon/app.py`` _lifespan;
    cancelled in its finally block.
    """
    boot_time = time.monotonic()
    # The very first post-warmup scan is an explicit current-window catch-up
    # (``first_scan=True``) rather than a guessed elapsed cursor; it evaluates
    # only the single latest due local 03:30 window.  Thereafter the ordinary
    # UTC crossing gate applies against the real previous scan instant.
    previous_scan_utc: datetime | None = None
    first_scan = True
    while True:
        t0 = time.monotonic()
        now_utc = datetime.now(timezone.utc)
        uptime = t0 - boot_time
        if uptime >= warm_up_seconds:
            for org in list(state.orgs.values()):
                try:
                    await _tick_org(
                        org, state, now_utc,
                        previous_scan_utc=previous_scan_utc,
                        first_scan=first_scan,
                    )
                except Exception:
                    logger.exception(
                        "workspace cleanup scheduling skipped for org %s",
                        org.slug,
                    )
            previous_scan_utc = now_utc
            first_scan = False
        duration = time.monotonic() - t0
        state.metrics_registry.record_loop_tick(
            "workspace_cleanup_scheduler", interval_seconds, duration,
        )
        await asyncio.sleep(interval_seconds)
