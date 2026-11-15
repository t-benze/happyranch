"""Read-only, bounded update readiness observation; never stop authority."""
from __future__ import annotations

import asyncio
import sqlite3
from time import monotonic
from typing import TYPE_CHECKING

from pydantic import BaseModel

if TYPE_CHECKING:
    from runtime.daemon.state import DaemonState

# Saturating evidence counts, not distinct sessions. Never walk terminal history.
_CAP = 1000
_MAX_ORGS = 64
_READ_SECONDS = 0.25
_INVOCATIONS = (
    ("thread_invocations", "idx_thread_invocations_pending", "pending"),
    ("dreams", "idx_dreams_status", "running"),
    ("work_hours", "idx_work_hours_status", "running"),
    ("schedules", "idx_schedules_status_fire_at", "firing"),
)


class UpdateBusyCounts(BaseModel):
    host_active: int | None = None
    host_queued: int | None = None
    jobs_inflight: int | None = None
    jobs_nonterminal: int | None = None
    invocations_nonterminal: int | None = None


class UpdateBusy(BaseModel):
    busy: bool
    unknown: bool
    counts: UpdateBusyCounts


def _count(conn: sqlite3.Connection, table: str, index: str, status: str) -> int:
    # All identifiers/statuses are closed source constants. LIMIT bounds even
    # a large nonterminal population; INDEXED BY refuses a missing old index.
    row = conn.execute(
        f"SELECT COUNT(*) FROM (SELECT 1 FROM {table} INDEXED BY {index} "
        f"WHERE status = '{status}' LIMIT {_CAP})"
    ).fetchone()
    return int(row[0])


def compose_update_busy(state: DaemonState, host: dict | None) -> UpdateBusy:
    """Called on the event loop without awaits, after the host snapshot.

    Loop-owned runner/native-owner/job registrations cannot change during this
    read. They precede host admission and outlive attempt release/backoff and
    native finish. Read task session bindings LAST, so an admission grant
    between the host's separate active/queue reads cannot erase its logical
    owner. New work after this observation remains possible: explicit click
    consent and a fresh read are mandatory in the app, even for idle.
    """
    from runtime.daemon import jobs_runner

    counts = UpdateBusyCounts()
    unknown = False
    deadline = monotonic() + _READ_SECONDS
    try:
        asyncio.get_running_loop()  # These registries are event-loop-owned.
        if host is None or host["wired"] is not True:
            raise ValueError("host unavailable")
        admission, residue = host["admission"], host["residue"]
        if (host["backend"]["healthy"] is not True or admission["shutdown"] is not False
                or residue["admission_blocked"] is not False
                or type(residue["survivors_count"]) is not int or residue["survivors_count"] != 0):
            unknown = True
        for key, source in (("host_active", "active"), ("host_queued", "queue_depth")):
            value = admission[source]
            if type(value) is not int or value < 0:
                raise ValueError("invalid host count")
            setattr(counts, key, min(value, _CAP))
        if (state.runtime is None or state._runner_shutdown or state.broken_orgs
                or state._startup_held_orgs or state._attachment_closes or state._retiring_orgs):
            raise ValueError("producer inventory unavailable")
        with state.capture_orgs() as orgs:
            if len(orgs) > _MAX_ORGS:
                raise ValueError("org observation bound")
            # Include retained registrations even after their asyncio task is
            # done: an unsettled native/output tail is not idle evidence.
            counts.jobs_inflight = min(_CAP, len(jobs_runner._INFLIGHT) + len(jobs_runner._RUNNER_TASKS))
            jobs = 0
            queue = state.queue
            remaining = deadline - monotonic()
            if remaining <= 0 or not queue._admission_lock.acquire(timeout=remaining):
                raise ValueError("task queue unavailable")
            try:
                if queue._stopping:
                    raise ValueError("task queue stopping")
                invocations = queue._queue.qsize() + len(queue._native_work) + len(queue._admission_reservations)
            finally:
                queue._admission_lock.release()
            for org in orgs.values():
                if org._runner_admission_closed or org._startup_authority_error is not None:
                    raise ValueError("producer unavailable")
                invocations += len(org._runner_tasks) + len(org._native_task_users)
                invocations += len(org.thread_queue._published_once_tokens)
                invocations += sum(len(queue._owned) for queue in
                                   (org.dream_queue, org.wake_queue, org.schedule_queue))
                db = org.db
                remaining = deadline - monotonic()
                if remaining <= 0 or not db._lock.acquire(timeout=remaining):
                    raise ValueError("producer read bound")
                try:
                    # Existing native owners can survive the async runner's
                    # return (unknown preparation, worker or output teardown).
                    # Count their registries without interpreting/clearing them.
                    move = db._team_move_coordinator
                    invocations += (len(db._task_pause_store._live) + len(move._reservations)
                                    + len(move.threads.live) + len(move.threads.controls)
                                    + len(move.producers.live) + len(move.producers.writers))
                    # Pending jobs can already own preparation; retained running
                    # rows count even when there is no current native registration.
                    jobs += _count(db._conn, "jobs", "jobs_status_idx", "pending")
                    jobs += _count(db._conn, "jobs", "jobs_status_idx", "running")
                    for table, index, status in _INVOCATIONS:
                        invocations += _count(db._conn, table, index, status)
                finally:
                    db._lock.release()
                invocations += org.sessions.count_active()
                if monotonic() > deadline:
                    raise ValueError("producer read bound")
            counts.jobs_nonterminal = min(jobs, _CAP)
            counts.invocations_nonterminal = min(invocations, _CAP)
    except Exception:
        # Never expose exception text, row identities, paths or credentials.
        unknown = True
    values = counts.model_dump().values()
    unknown = unknown or any(value is None for value in values)
    return UpdateBusy(busy=unknown or any(value for value in values), unknown=unknown, counts=counts)
