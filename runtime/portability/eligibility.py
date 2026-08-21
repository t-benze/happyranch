"""Pure quiescence / zombie-detection eligibility check (THR-187 Slice A).

Eligibility is the fail-closed "no live work may be carried" predicate. It is
pure: the caller gathers concrete liveness facts (task rows, active session
count, queued items, pending invocations, active jobs/dreams/work-hours/
schedules) and this module computes blockers and possible-zombie candidates.

A "possible zombie" is reported but never resolved here. A parked task
(``block_kind`` in ``delegated``/``blocked_on_job``) is never a zombie merely
because it is old.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

# Mirrors runtime.daemon.queue.HEARTBEAT_INTERVAL_SECONDS (30s) * 2. Kept as a
# local constant so this module stays free of daemon imports.
STALE_HEARTBEAT_SECONDS = 60

# Nonterminal task statuses (the stored Path-B truth).
NONTERMINAL_STATUSES = frozenset({"pending", "in_progress", "escalated"})


@dataclass
class TaskLiveness:
    """The liveness facts needed to classify one task row."""

    task_id: str
    status: str
    block_kind: str | None
    last_heartbeat: datetime | None
    executor_pid: int | None
    assigned_agent: str | None


@dataclass
class ZombieCandidate:
    task_id: str
    assigned_agent: str | None


@dataclass
class Eligibility:
    eligible: bool
    tasks: list[str] = field(default_factory=list)
    active_session_count: int = 0
    queued_for_org: int = 0
    pending_invocation_count: int = 0
    active_jobs: list[str] = field(default_factory=list)
    active_dreams: list[str] = field(default_factory=list)
    active_work_hours: list[str] = field(default_factory=list)
    active_schedules: list[str] = field(default_factory=list)
    possible_zombies: list[ZombieCandidate] = field(default_factory=list)

    def blockers(self) -> dict[str, object]:
        """A human-readable roll-up of every concrete blocker."""
        return {
            "tasks": self.tasks,
            "active_sessions": self.active_session_count,
            "queued_items": self.queued_for_org,
            "pending_thread_invocations": self.pending_invocation_count,
            "active_jobs": self.active_jobs,
            "active_dreams": self.active_dreams,
            "active_work_hours": self.active_work_hours,
            "active_schedules": self.active_schedules,
        }


def _is_possible_zombie(
    task: TaskLiveness,
    *,
    now: datetime,
    pid_is_dead: Callable[[int], bool],
) -> bool:
    """The same AND-gate the ongoing zombie reaper uses: in_progress + no
    block_kind + stale heartbeat + dead executor pid."""
    if task.status != "in_progress":
        return False
    if task.block_kind is not None:
        return False  # parked (delegated/blocked_on_job) is never a zombie
    if task.last_heartbeat is None:
        return False  # err toward miss
    if (now - task.last_heartbeat).total_seconds() < STALE_HEARTBEAT_SECONDS:
        return False
    if task.executor_pid is None:
        return False  # can't probe — err toward miss
    return pid_is_dead(task.executor_pid)


def compute_eligibility(
    *,
    tasks: list[TaskLiveness],
    active_session_count: int,
    queued_for_org: int,
    pending_invocation_count: int,
    active_job_ids: list[str],
    active_dream_ids: list[str],
    active_work_hour_ids: list[str],
    active_schedule_ids: list[str],
    now: datetime,
    pid_is_dead: Callable[[int], bool],
) -> Eligibility:
    """Compute eligibility and possible-zombie candidates from liveness facts.

    ``eligible`` is True only when every blocker surface is empty. Every
    nonterminal task (pending/in_progress/escalated — including live, delegated,
    and job-parked forms) is a blocker.
    """
    nonterminal = [t for t in tasks if t.status in NONTERMINAL_STATUSES]
    possible_zombies = [
        ZombieCandidate(task_id=t.task_id, assigned_agent=t.assigned_agent)
        for t in nonterminal
        if _is_possible_zombie(t, now=now, pid_is_dead=pid_is_dead)
    ]

    eligible = (
        not nonterminal
        and active_session_count == 0
        and queued_for_org == 0
        and pending_invocation_count == 0
        and not active_job_ids
        and not active_dream_ids
        and not active_work_hour_ids
        and not active_schedule_ids
    )

    return Eligibility(
        eligible=eligible,
        tasks=[t.task_id for t in nonterminal],
        active_session_count=active_session_count,
        queued_for_org=queued_for_org,
        pending_invocation_count=pending_invocation_count,
        active_jobs=list(active_job_ids),
        active_dreams=list(active_dream_ids),
        active_work_hours=list(active_work_hour_ids),
        active_schedules=list(active_schedule_ids),
        possible_zombies=possible_zombies,
    )


def is_true_zombie(
    *,
    status: str,
    block_kind: str | None,
    last_heartbeat: datetime | None,
    executor_pid: int | None,
    now: datetime,
    pid_is_dead: Callable[[int], bool],
) -> tuple[bool, str | None]:
    """Revalidate a single candidate as a true zombie (reconcile gate).

    Returns ``(is_zombie, reason)``. A parked task (``block_kind`` non-None) is
    never a zombie merely because it is old — the founder must not reconcile it.
    """
    if status != "in_progress":
        return False, f"status is {status!r}, not in_progress"
    if block_kind is not None:
        return False, f"block_kind={block_kind!r}; parked tasks are never zombies"
    if last_heartbeat is None:
        return False, "no last_heartbeat to evaluate staleness"
    if (now - last_heartbeat).total_seconds() < STALE_HEARTBEAT_SECONDS:
        return False, "heartbeat is fresh"
    if executor_pid is None:
        return False, "no executor_pid to probe"
    if not pid_is_dead(executor_pid):
        return False, "executor pid is alive or indeterminate"
    return True, None
