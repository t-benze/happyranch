"""Advisory workspace-disk context packed for schedule-spawned task sessions.

THR-195 / TASK-5971: when the daemon triggers a recurring cleanup task (a root
task created by the schedule spawn callback), it packs a bounded, fail-open,
ADVISORY sizing snapshot into the task session prompt through the existing
``protocol_doc_manifest`` note seam (the same seam as the repo-freshness note).

The block is advisory sizing context ONLY. It is never an eligibility list,
never a candidate list, never a safety label, and never a removal
recommendation. Every fact and every path must be re-derived independently and
immediately before any action. The block contains only aggregate counts and
sizes — it never enumerates paths and never carries pending-job or
``blocked_on_job_ids`` liveness signals (those are workflow/lifecycle rows, not
OS liveness).

Scoping: the note is composed only for task sessions whose ``task_id`` appears
in a Schedule's ``spawned_task_ids`` (``ScheduleStore.find_by_spawned_task_id``)
— the existing durable Schedule→task link. Ordinary task, thread, wake, dream,
and schedule-fire sessions never receive the block. There is no content-based
"cleanup" discriminator: the reverse link is the accepted minimal marker
(THR-195 seq 118/119) and no heuristic is invented here.

Fail-open: measurement is bounded (deadline + traversal caps) and NEVER raises;
any timeout or error yields an explicit unavailable advisory note and can never
prevent session spawning.
"""
from __future__ import annotations

import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from runtime.models import TaskStatus

if TYPE_CHECKING:
    from runtime.daemon.sessions import SessionTracker
    from runtime.infrastructure.database import Database
    from runtime.orchestrator._paths import OrgPaths


# ── measurement bounds ────────────────────────────────────────────────────
# Tight, explicit bounds: the advisory walk must never stall a session launch.
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
    "This block is ADVISORY SIZING CONTEXT packed fresh at session trigger "
    "time. It is STALE ON ARRIVAL. It is NOT an eligibility list and NOT a "
    "candidate list. No path or fact here is labelled safe, and nothing here "
    "recommends or authorizes removal. Re-derive every path and every fact "
    "independently and immediately before any action."
)


def format_workspace_context_note(snapshot: WorkspaceContextSnapshot) -> str:
    """Render the advisory workspace-disk note.

    Always carries the advisory/stale/non-candidate/re-derive warning. The
    unavailable variant states explicitly that no sizing data was packed and
    that this failure does not affect the session.
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
            "  No sizing data was packed. This advisory failure does not affect this session."
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

def _git_worktree_paths(repo_dir: Path, timeout: float) -> list[Path]:
    """Registered linked-worktree paths for one repo (``git worktree list``).

    Returns the primary checkout itself never included. Any git failure or
    timeout yields an empty list (fail-open).
    """
    try:
        result = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            cwd=str(repo_dir),
            capture_output=True,
            timeout=timeout,
        )
    except (subprocess.TimeoutExpired, subprocess.SubprocessError, OSError):
        return []
    if result.returncode != 0:
        return []
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
    return paths[: _MAX_WORKTREES_PER_REPO]


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


def _registered_worktree_stats(
    paths: Any, db: "Database", *, deadline: float,
) -> _WorktreeStats:
    """Aggregate registered worktrees across every workspace/repo, joined to
    task status where the ``TASK-\\d+`` prefix resolves to a known task."""
    stats = _WorktreeStats()
    workspaces = _iter_workspaces(paths)
    for workspace in workspaces:
        if time.monotonic() > deadline:
            stats.timed_out = True
            break
        try:
            from runtime.orchestrator.workspace_adapters import (
                PersistentWorkspaceSetup,
            )
            repo_names = PersistentWorkspaceSetup.detect_repo_names(workspace)
        except OSError:
            continue
        for name in repo_names[: _MAX_REPOS_PER_WORKSPACE]:
            if time.monotonic() > deadline:
                stats.timed_out = True
                break
            repo_dir = workspace / "repos" / name
            for wt_path in _git_worktree_paths(repo_dir, _GIT_TIMEOUT_SECONDS):
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
    return stats


def _iter_workspaces(paths: Any) -> list[Path]:
    try:
        workspaces_dir = paths.workspaces_dir
        if not workspaces_dir.exists():
            return []
        return sorted(
            (
                p for p in workspaces_dir.iterdir()
                if p.is_dir() and not p.is_symlink()
            ),
            key=lambda p: p.name,
        )[:_MAX_WORKSPACES]
    except OSError:
        return []


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
    workspaces = _iter_workspaces(paths)
    snap.workspaces_count = len(workspaces)

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
    if wt.timed_out:
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


# ── orchestrator seam helper (never raises, never measures ordinary) ──────

def maybe_build_cleanup_context_note(
    *,
    db: "Database",
    paths: "OrgPaths",
    sessions: "SessionTracker | None" = None,
    task_id: str | None,
    deadline_seconds: float = _MEASURE_DEADLINE_SECONDS,
) -> str:
    """Return the advisory note for a schedule-spawned task session, else "".

    NEVER raises and NEVER runs the measurement for ordinary sessions: the
    reverse lookup over the existing ``spawned_task_ids`` contract gates every
    path, so ordinary task/thread/wake/dream/schedule-fire sessions see a
    byte-identical prompt.
    """
    if not task_id:
        return ""
    try:
        schedule = db.schedules.find_by_spawned_task_id(task_id)
    except Exception:
        return ""
    if schedule is None:
        return ""
    try:
        snapshot = measure_workspace_context(
            paths=paths, db=db, sessions=sessions,
            deadline_seconds=deadline_seconds,
        )
    except Exception as exc:  # pragma: no cover — measure_workspace_context
        # never raises by contract; this is a second fail-open wall.
        snapshot = WorkspaceContextSnapshot(
            available=False, reason=f"measurement error: {exc}",
        )
    return format_workspace_context_note(snapshot)
