"""Daemon-managed, system-default workspace cleanup scheduler (THR-195).

Founder ruling (THR-195 seq 129): workspace cleanup is a **daemon-managed,
system-default capability** that runs on its own without user configuration and
independent of all user Schedules. The daemon periodically performs a bounded
aggregate workspace measurement and — when the authoritative TASK-5552 /
THR-195 contract says cleanup investigation is warranted — triggers an
ordinary root task for the responsible agent with the fresh measurement packed
as advisory context at trigger time. It never uses, creates, or modifies a
Schedule, never injects anything into the shared session-prompt seam, and never
performs cleanup itself.

This is the "sixth loop" of the same shape as ``dream_scheduler`` /
``schedule_scheduler`` / ``zombie_reaper`` (one new module, one registration in
``runtime/daemon/app.py``). The measurement core is adopted from the retained
TASK-5974/TASK-5986 work (deadline + cardinality-cap fixes included); the
rejected Schedule-prompt-seam architecture is not carried forward.

Contract-relevant bounds (all documented in protocol/05b + 05c):

- Cadence: weekly, Sunday 03:30 in the org's effective timezone (TASK-5552
  §6). At most one trigger per weekly window; a missed window is never
  replayed (no backfill), matching the recurring Schedule contract.
- Trigger: the weekly occurrence is due AND this window is unserviced AND no
  prior cleanup task is non-terminal (TASK-5552 §3 "one run at a time").
- Report-only: the daemon-composed brief is REPORT-ONLY (TASK-5552 §6 rollout:
  the first runs produce an inventory and nothing else). No deletion/pruning/
  movement is authorized by this module or the shipped brief; the mutating
  brief is a separately approved follow-up.
- Advisory content: the packed block is aggregate-only sizing/status context,
  prominently ADVISORY / STALE ON ARRIVAL / not an eligibility or candidate
  list / no path safe / no removal recommended / re-derive before any action.
  It never enumerates paths and never uses pending jobs or
  ``blocked_on_job_ids`` as liveness.
- Measurement is explicitly bounded (wall-clock deadline, traversal and
  subprocess caps) and fail-open: every timeout/error/cap hit yields an
  explicit unavailable/truncated status and can never block daemon operation
  or task/session spawning.
- Reporting: the responsible agent reports to the founder in a single durable
  founder-visible thread (fixed subject; created by the daemon on first
  trigger). The daemon passes the thread id plus a daemon-minted single-use
  invocation token in the brief; the agent appends the report via the existing
  reply route. Silence on that thread is the loop-stopped signal.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone, tzinfo
from datetime import time as _dt_time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from runtime.models import TaskRecord, TaskStatus, ThreadInvocationPurpose

if TYPE_CHECKING:
    from runtime.daemon.org_state import OrgState
    from runtime.daemon.sessions import SessionTracker
    from runtime.daemon.state import DaemonState
    from runtime.infrastructure.database import Database
    from runtime.orchestrator._paths import OrgPaths

logger = logging.getLogger("happyranch.daemon.workspace_cleanup_scheduler")

# ---------------------------------------------------------------------------
# Responsible agent + cadence (derived from TASK-5552 / THR-195)
# ---------------------------------------------------------------------------

# The designated engineering agent: TASK-5552 §3 "an ordinary root task for
# the designated engineering agent"; every engineering implementation leg of
# this feature (TASK-5974/5986/6001/6016) is dev_agent, and dev_agent is the
# largest workspace consumer in THR-195 seq 131 measurements. Selection is
# documented as an assumption for the reviewer; no user configuration surface
# exists (founder: "runs on its own without configuration from the user").
_RESPONSIBLE_AGENT = "dev_agent"

# Weekly occurrence: Sunday 03:30 in the org's effective timezone.
# ``datetime.weekday()``: Monday=0 ... Sunday=6.
_OCCURRENCE_WEEKDAY = 6
_OCCURRENCE_TIME = _dt_time(hour=3, minute=30)

# Scheduler tick interval (cheap decision scan, mirrors schedule_scheduler).
_LOOP_INTERVAL_SECONDS = 60

# Boot warm-up grace before the first trigger scan (mirrors zombie_reaper's
# ``STALE_HEARTBEAT_SECONDS``-based warm-up). The daemon settles after a
# restart (dashboard warm, job recovery, producer wiring) before the cleanup
# loop may enqueue work; a weekly occurrence is unaffected by a 30s delay.
# It also keeps short-lived daemon-lifespan test contexts free of unexpected
# trigger side effects.
_WARM_UP_SECONDS = 30.0

# Fixed marker the daemon writes at the top of every cleanup brief. Used ONLY
# to identify daemon-created cleanup tasks for dedup/window bookkeeping — it is
# the daemon's own content, never a user-content heuristic.
_CLEANUP_BRIEF_MARKER = "HAPPYRANCH SYSTEM WORKSPACE CLEANUP RUN (daemon-triggered)"

# Durable founder-visible report thread (consultant THR-195 seq 131: "one
# durable thread, not one per run; the daemon passes a fixed thread id in the
# brief and lets the agent append"). The fixed subject is how the daemon
# resolves the durable thread identity without any new schema.
_CLEANUP_REPORT_THREAD_SUBJECT = "HappyRanch system workspace cleanup reports"

# Bound for the dedup/window scan over the responsible agent's recent tasks.
_MAX_CLEANUP_TASK_SCAN = 1000


# ── measurement bounds ────────────────────────────────────────────────────
# Tight, explicit bounds: the advisory walk must never stall the daemon loop.
_MEASURE_DEADLINE_SECONDS = 10.0
_GIT_TIMEOUT_SECONDS = 5.0
_MAX_WORKSPACES = 64
_MAX_REPOS_PER_WORKSPACE = 16
_MAX_WORKTREES_PER_REPO = 256
_MAX_ENTRIES_PER_WORKSPACE = 250_000
_MAX_DEPTH = 12
_TOP_WORKSPACES = 3

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
    """One bounded, fail-open workspace-disk snapshot (advisory only)."""

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

    def unavailable(self, reason: str) -> "WorkspaceContextSnapshot":
        return WorkspaceContextSnapshot(
            available=False, reason=reason, measured_at=self.measured_at,
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
        lines.append(
            f"  measurement unavailable at trigger time: {snapshot.reason or 'unknown'}"
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
        f"  workspaces total:   {_fmt_bytes(snapshot.workspaces_bytes)} across "
        f"{snapshot.workspaces_count}"
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
    paths: Any, db: "Database", *, deadline: float,
) -> _WorktreeStats:
    """Aggregate registered worktrees across every workspace/repo, joined to
    task status where the ``TASK-\\d+`` prefix resolves to a known task.

    Bounded by the shared wall-clock ``deadline``: every git subprocess
    receives ``min(_GIT_TIMEOUT_SECONDS, remaining)`` and expiry is re-checked
    after every subprocess and after the last repository, so a call begun just
    before the deadline can never publish a snapshot as available. Every
    cardinality-cap hit (workspaces/repos/worktrees) sets ``truncated`` so
    partial aggregates are never presented as complete.
    """
    stats = _WorktreeStats()
    workspaces, workspaces_truncated = _iter_workspaces(paths)
    if workspaces_truncated:
        stats.truncated = True
    deadline_hit = False
    for workspace in workspaces:
        if time.monotonic() > deadline:
            deadline_hit = True
            break
        try:
            from runtime.orchestrator.workspace_adapters import (
                PersistentWorkspaceSetup,
            )
            repo_names = PersistentWorkspaceSetup.detect_repo_names(workspace)
        except OSError:
            continue
        if len(repo_names) > _MAX_REPOS_PER_WORKSPACE:
            stats.truncated = True
        for name in repo_names[: _MAX_REPOS_PER_WORKSPACE]:
            if time.monotonic() > deadline:
                deadline_hit = True
                break
            repo_dir = workspace / "repos" / name
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                deadline_hit = True
                break
            wt_paths, wt_truncated = _git_worktree_paths(
                repo_dir, min(_GIT_TIMEOUT_SECONDS, remaining),
            )
            if wt_truncated:
                stats.truncated = True
            if time.monotonic() > deadline:
                deadline_hit = True
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
        if deadline_hit:
            break
    if time.monotonic() > deadline:
        deadline_hit = True
    if deadline_hit:
        stats.timed_out = True
    return stats


def _iter_workspaces(paths: Any) -> tuple[list[Path], bool]:
    """Sorted workspace directories up to ``_MAX_WORKSPACES`` plus a
    ``truncated`` flag set when more workspaces exist than the cap, so callers
    never present a partial workspace aggregate as complete."""
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
        truncated = len(all_workspaces) > _MAX_WORKSPACES
        return all_workspaces[:_MAX_WORKSPACES], truncated
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
    *,
    paths: "OrgPaths",
    db: "Database",
    sessions: "SessionTracker | None" = None,
    deadline_seconds: float = _MEASURE_DEADLINE_SECONDS,
) -> WorkspaceContextSnapshot:
    """Measure one bounded advisory workspace snapshot. NEVER raises.

    Fail-open: an unreadable workspaces dir, a deadline that fires before any
    workspace is measured, or any unexpected error yields ``available=False``
    with a reason. Any truncation (deadline or traversal cap) also yields
    ``available=False`` — partial numbers are never presented as a complete
    measurement. Per-workspace soft failures (e.g. an unreadable subtree) are
    reported via ``workspaces_unmeasured`` rather than silently claimed as
    complete.
    """
    deadline = time.monotonic() + max(0.001, deadline_seconds)
    try:
        return _measure(deadline=deadline, paths=paths, db=db, sessions=sessions)
    except Exception as exc:  # pragma: no cover — defensive, fail-open
        return WorkspaceContextSnapshot(
            available=False, reason=f"measurement error: {exc}",
        )


def _measure(
    *, deadline: float, paths: "OrgPaths", db: "Database",
    sessions: "SessionTracker | None",
) -> WorkspaceContextSnapshot:
    snap = WorkspaceContextSnapshot()
    workspaces, workspaces_truncated = _iter_workspaces(paths)
    snap.workspaces_count = len(workspaces)
    if workspaces_truncated:
        snap.truncated = True

    per_workspace: list[tuple[str, int]] = []
    measured = 0
    for workspace in workspaces:
        if time.monotonic() > deadline:
            snap.workspaces_unmeasured += 1
            continue
        walk = _walk_workspace(
            workspace,
            deadline=deadline,
            max_entries=_MAX_ENTRIES_PER_WORKSPACE,
            max_depth=_MAX_DEPTH,
        )
        measured += 1
        if walk.truncated:
            snap.truncated = True
        if walk.errors and walk.bytes_total == 0:
            snap.workspaces_unmeasured += 1
        else:
            snap.workspaces_bytes += walk.bytes_total
            per_workspace.append((workspace.name, walk.bytes_total))
        snap.dep_dirs += walk.dep_count
        snap.dep_bytes += walk.dep_bytes
        snap.dep_dirs_in_worktrees += walk.dep_in_wt_count
        snap.dep_bytes_in_worktrees += walk.dep_in_wt_bytes

    if workspaces and measured == 0:
        return snap.unavailable(
            "measurement deadline exceeded before any workspace"
        )
    if snap.truncated:
        # Strict fail-closed: partial numbers from a deadline/cap hit are
        # never presented as a complete measurement.
        return snap.unavailable(
            "workspace measurement did not complete within bounded limits "
            "(timeout or traversal cap)"
        )

    per_workspace.sort(key=lambda item: item[1], reverse=True)
    snap.largest = per_workspace[: _TOP_WORKSPACES]

    wt = _registered_worktree_stats(paths, db, deadline=deadline)
    snap.worktrees_registered = wt.registered
    snap.worktrees_terminal = wt.terminal
    snap.worktrees_non_terminal = wt.non_terminal
    snap.worktrees_unclassified = wt.unclassified
    if wt.timed_out or wt.truncated:
        snap.truncated = True

    snap.live_sessions_count, snap.live_sessions_agents = _live_sessions(sessions)

    if snap.truncated:
        # Strict fail-closed: partial numbers from a deadline/cap hit are
        # never presented as a complete measurement.
        return snap.unavailable(
            "workspace measurement did not complete within bounded limits "
            "(timeout or traversal cap)"
        )
    return snap


# ── weekly occurrence + trigger decision ──────────────────────────────────

def _previous_occurrence(now_local: datetime) -> datetime:
    """Most recent Sunday 03:30 at-or-before ``now_local`` (never future)."""
    days_since_sunday = (now_local.weekday() + 1) % 7
    occurrence = now_local.replace(
        hour=_OCCURRENCE_TIME.hour, minute=_OCCURRENCE_TIME.minute,
        second=0, microsecond=0,
    ) - timedelta(days=days_since_sunday)
    return occurrence


def _as_aware_utc(value: datetime) -> datetime:
    """Normalize a TaskRecord timestamp (naive UTC or aware) to aware UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _is_cleanup_brief(brief: str | None) -> bool:
    return bool(brief and brief.startswith(_CLEANUP_BRIEF_MARKER))


def _latest_cleanup_task(db: "Database", agent: str) -> "TaskRecord | None":
    """Most recent cleanup-marked task for ``agent``, or None.

    Bounded scan over ``list_tasks`` pages (newest first). Any lookup error
    fails closed (returns None → caller skips the trigger) so a dedup-blind
    daemon can never double-fire a run.
    """
    before: str | None = None
    for _ in range(_MAX_CLEANUP_TASK_SCAN // 100 + 1):
        try:
            page = db.list_tasks(
                limit=100, assigned_agent=agent, before_task_id=before,
            )
        except Exception:
            return None
        if not page:
            return None
        for task in page:
            if _is_cleanup_brief(task.brief):
                return task
        before = page[-1].id
    return None


@dataclass
class CleanupTriggerDecision:
    should_trigger: bool
    reason: str | None = None


def decide_cleanup_trigger(
    *,
    db: "Database",
    agent: str = _RESPONSIBLE_AGENT,
    now_utc: datetime | None = None,
    tz: tzinfo | None = None,
) -> CleanupTriggerDecision:
    """Pure trigger decision: weekly occurrence due + window unserviced + no
    prior cleanup task in flight (TASK-5552 §3 one-run-at-a-time)."""
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)
    effective_tz = tz or timezone.utc
    now_local = now_utc.astimezone(effective_tz)
    occurrence = _previous_occurrence(now_local)
    if now_local < occurrence:
        return CleanupTriggerDecision(False, "not_due")

    latest = _latest_cleanup_task(db, agent)
    if latest is None:
        return CleanupTriggerDecision(True, None)
    if _as_aware_utc(latest.created_at) >= occurrence.astimezone(timezone.utc):
        return CleanupTriggerDecision(False, "already_triggered_this_window")
    if latest.status not in _TERMINAL_TASK_STATUSES:
        return CleanupTriggerDecision(False, "prior_run_in_flight")
    return CleanupTriggerDecision(True, None)


# ── durable founder-report thread ─────────────────────────────────────────

def _find_report_thread(db: "Database") -> str | None:
    """Open thread id with the fixed cleanup-report subject, else None."""
    try:
        for thread in db.list_threads(status="open", limit=500):
            if thread.subject == _CLEANUP_REPORT_THREAD_SUBJECT:
                return thread.id
    except Exception:
        return None
    return None


def _resolve_or_create_report_thread(
    org: "OrgState", *, task_id: str,
) -> tuple[str | None, int | None]:
    """Resolve the durable report thread; create it (dream-complete pattern)
    on first trigger. Returns ``(thread_id, last_seq)`` or ``(None, None)``
    when the thread cannot be created (fail-open: the run still proceeds with
    a compose-a-new-thread fallback instruction)."""
    from runtime.daemon.routes.threads import FOUNDER_LITERAL
    from runtime.daemon.routes.threads import _create_agent_thread_locked
    from runtime.orchestrator.org_config import (
        OrgConfig,
        resolve_org_setting_threads,
    )

    existing = _find_report_thread(org.db)
    if existing is not None:
        messages = org.db.list_thread_messages(existing, limit=1000)
        last_seq = messages[-1].seq if messages else 0
        return existing, last_seq

    opening = (
        f"Daemon-managed workspace cleanup reporting thread. Cleanup run "
        f"{task_id} was triggered; the responsible agent appends its "
        f"report here."
    )
    try:
        turn_cap = resolve_org_setting_threads(
            org.db, code_default=OrgConfig(),
        )["default_turn_cap"]
        # Same shared compose helper the dream-complete route uses for
        # founder-only threads: identical participant/turn/audit semantics.
        thread_id, _seq, _tokens, _addressed = _create_agent_thread_locked(
            org,
            composer=_RESPONSIBLE_AGENT,
            subject=_CLEANUP_REPORT_THREAD_SUBJECT,
            body_text=opening,
            recipients=[FOUNDER_LITERAL],
            turn_cap=turn_cap,
            composed_from_task_id=task_id,
        )
        return thread_id, _seq
    except Exception:
        logger.exception(
            "workspace cleanup: report thread creation failed for org %s",
            org.slug,
        )
        return None, None


def _mint_report_token(
    db: "Database", *, thread_id: str, agent: str, last_seq: int,
) -> str | None:
    """Mint a single-use BOOTSTRAP invocation token for the agent on the
    report thread (consumable by the existing reply route). Returns None on
    failure (fail-open)."""
    try:
        inv = db.mint_thread_invocation(
            thread_id=thread_id,
            agent_name=agent,
            triggering_seq=max(1, last_seq),
            purpose=ThreadInvocationPurpose.BOOTSTRAP,
        )
        return inv.invocation_token
    except Exception:
        logger.exception(
            "workspace cleanup: report token mint failed for thread %s",
            thread_id,
        )
        return None


# ── brief composition ─────────────────────────────────────────────────────

def compose_cleanup_brief(
    *,
    org_slug: str,
    task_id: str,
    snapshot: WorkspaceContextSnapshot,
    thread_id: str | None,
    report_token: str | None,
    report_seq: int | None,
) -> str:
    """Daemon-composed REPORT-ONLY brief: fixed marker + fresh advisory block +
    founder-thread reporting instructions. Never a Schedule brief; nothing is
    persisted beyond this task row."""
    lines = [
        _CLEANUP_BRIEF_MARKER,
        "",
        "This is a daemon-managed, system-default workspace cleanup run, "
        "independent of all user Schedules. You are the responsible agent.",
        "",
        "THIS RUN IS REPORT-ONLY. Do NOT delete, prune, move, or modify any "
        "file, worktree, dependency directory, or workspace artifact. Do NOT "
        "create or modify any Schedule. No cleanup action is authorized in "
        "this run.",
        "",
        format_workspace_context_note(snapshot),
        "",
        "Work to do:",
        "",
        "1. Inventory current org workspace state read-only: registered "
        "linked worktrees, dependency directories, and task associations. "
        "Classify each worktree by its TASK-\\d+ prefix (suffixed names like "
        "TASK-5567-base691 resolve to TASK-5567); unknown or missing tasks "
        "are unclassified, never assumed terminal.",
        "2. Re-derive every fact and every path immediately before any action "
        "or recommendation. Write output/<task_id>/ with inventory.json, "
        "final-ledger.jsonl (all rows no-op), and report.md per the TASK-5552 "
        "cleanup design, including measured sizes, exact skips and reasons, "
        "and any ambiguity.",
    ]
    if thread_id and report_token:
        payload = {
            "thread_id": thread_id,
            "invocation_token": report_token,
            "speaker": _RESPONSIBLE_AGENT,
            "body_markdown": (
                "<report: measured before/after sizes, exact removals (none "
                "in report-only), skips, any ambiguity>"
            ),
            "in_response_to_seq": report_seq or 1,
        }
        lines.extend([
            f"3. Report to the founder by appending to the durable "
            f"founder-visible thread {thread_id} "
            f"(\"{_CLEANUP_REPORT_THREAD_SUBJECT}\") using the "
            f"daemon-minted single-use invocation token:",
            "",
            f"    happyranch threads reply --org {org_slug} "
            f"--thread {thread_id} --from-file <payload>",
            "",
            f"    payload JSON: {payload}",
            "",
            "Append to that thread — do NOT compose a new thread. If the "
            "token or thread is unusable, compose a new founder-visible "
            f"thread titled \"{_CLEANUP_REPORT_THREAD_SUBJECT}\" with the "
            "same report content instead.",
        ])
    else:
        lines.extend([
            "3. Report to the founder by composing a founder-visible thread "
            f"titled \"{_CLEANUP_REPORT_THREAD_SUBJECT}\" (recipient "
            "@founder) with the report content: measured before/after sizes, "
            "exact removals (none in report-only), skips, and any ambiguity.",
        ])
    return "\n".join(lines)


# ── trigger ───────────────────────────────────────────────────────────────

async def trigger_cleanup(
    org: "OrgState",
    *,
    enqueue: Callable[[str, str], None],
    now_utc: datetime | None = None,
) -> str | None:
    """Create + enqueue one report-only cleanup task for the responsible
    agent, packing the fresh advisory measurement and the report-thread seam
    into the daemon-composed brief. Returns the task id, or None when the
    responsible agent/team cannot be resolved (fail-closed skip, never
    raised)."""
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)

    from runtime.orchestrator._paths import OrgPaths

    team = org.teams.team_for_agent(_RESPONSIBLE_AGENT)
    if team is None:
        logger.error(
            "workspace cleanup: responsible agent %s has no team in org %s",
            _RESPONSIBLE_AGENT, org.slug,
        )
        org.db.insert_audit_log(
            task_id="workspace-cleanup:skipped",
            agent=_RESPONSIBLE_AGENT,
            action="workspace_cleanup_skipped",
            payload={"reason": "responsible_agent_team_unresolved"},
        )
        return None

    task_id = org.db.next_task_id()

    # Durable founder-report thread (create-on-first-trigger) + token.
    thread_id: str | None = None
    report_seq: int | None = None
    async with org.db_lock:
        thread_id, report_seq = _resolve_or_create_report_thread(
            org, task_id=task_id,
        )
    report_token = None
    if thread_id is not None:
        report_token = _mint_report_token(
            org.db, thread_id=thread_id, agent=_RESPONSIBLE_AGENT,
            last_seq=report_seq or 0,
        )

    # Bounded, fail-open measurement — a disk gauge must never block the run.
    snapshot = measure_workspace_context(
        paths=OrgPaths(root=org.root),
        db=org.db,
        sessions=org.sessions,
    )

    brief = compose_cleanup_brief(
        org_slug=org.slug,
        task_id=task_id,
        snapshot=snapshot,
        thread_id=thread_id,
        report_token=report_token,
        report_seq=report_seq,
    )
    org.db.insert_task(TaskRecord(
        id=task_id,
        brief=brief,
        team=team,
        assigned_agent=_RESPONSIBLE_AGENT,
    ))
    enqueue(org.slug, task_id)

    org.db.insert_audit_log(
        task_id=task_id,
        agent=_RESPONSIBLE_AGENT,
        action="workspace_cleanup_triggered",
        payload={
            "report_thread_id": thread_id,
            "measurement_available": snapshot.available,
            "measurement_reason": snapshot.reason,
        },
    )
    logger.info(
        "workspace cleanup triggered for org %s: task %s (thread %s, "
        "measurement_available=%s)",
        org.slug, task_id, thread_id, snapshot.available,
    )
    return task_id


# ── per-org tick + async loop ─────────────────────────────────────────────

async def _tick_org(org: "OrgState", state: "DaemonState", now_utc: datetime) -> None:
    """One org's decision+tick. Never raises (loop-level isolation)."""
    from runtime.orchestrator._paths import OrgPaths
    from runtime.orchestrator.org_config import (
        _resolve_timezone,
        load_org_config,
    )

    tz = _resolve_timezone(load_org_config(OrgPaths(root=org.root)).timezone)[0]
    decision = decide_cleanup_trigger(db=org.db, now_utc=now_utc, tz=tz)
    if not decision.should_trigger:
        # Skip reasons are derivable from the tasks table; only the trigger
        # itself (and the team-unresolved fail-closed skip in trigger_cleanup)
        # carry audit rows, so the weekly loop never spams the ledger.
        return

    await trigger_cleanup(
        org,
        enqueue=lambda slug, tid: _enqueue_task(state, slug, tid),
        now_utc=now_utc,
    )


def _enqueue_task(state: "DaemonState", slug: str, task_id: str) -> None:
    from runtime.daemon.runner import enqueue_task
    enqueue_task(state, slug, task_id)


async def workspace_cleanup_scheduler_loop(
    state: "DaemonState", *,
    interval_seconds: int = _LOOP_INTERVAL_SECONDS,
    warm_up_seconds: float = _WARM_UP_SECONDS,
) -> None:
    """Weekly workspace-cleanup trigger loop (THR-195 seq 129/131).

    Mirrors ``dream_scheduler_loop`` / ``zombie_reaper_loop``: per-tick
    per-org decision with a boot warm-up grace, exception isolation, and
    loop-tick metrics. Registered in ``runtime/daemon/app.py`` _lifespan;
    cancelled in its finally block.
    """
    boot_time = time.monotonic()
    while True:
        t0 = time.monotonic()
        now_utc = datetime.now(timezone.utc)
        uptime = t0 - boot_time
        if uptime >= warm_up_seconds:
            for org in list(state.orgs.values()):
                try:
                    await _tick_org(org, state, now_utc)
                except Exception:
                    logger.exception(
                        "workspace cleanup scheduling skipped for org %s",
                        org.slug,
                    )
        duration = time.monotonic() - t0
        state.metrics_registry.record_loop_tick(
            "workspace_cleanup_scheduler", interval_seconds, duration,
        )
        await asyncio.sleep(interval_seconds)
