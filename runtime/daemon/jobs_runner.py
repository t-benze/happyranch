"""Subprocess execution for script_requests (spec §6).

Owns one short-lived asyncio coroutine per run: spawn → pump stdout/stderr →
fan out events to in-memory subscribers via the `publish` callback → terminate.

Module-level state: a registry of in-flight `asyncio.subprocess.Process` objects
keyed by actual DB attachment and job ID, with original runner/producer/Process
attribution for Stop, task termination and daemon shutdown.
"""
from __future__ import annotations

import asyncio
import inspect
import logging
import os
import shutil
import signal
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Awaitable, Callable

from runtime.orchestrator.executors import _sanitize_child_env
from runtime.orchestrator.task_scratch import (
    activate_task_scratch,
    apply_task_scratch_environment,
    prepare_task_scratch,
    reset_task_scratch,
)

if TYPE_CHECKING:
    from runtime.orchestrator.orchestrator import Orchestrator


def migrate_filesystem_layout(org_root: Path | str) -> None:
    """Rename ``<org_root>/scripts/`` → ``<org_root>/jobs/`` and each ``SR-*``
    file to ``JOB-*``.

    Idempotent: a no-op if ``jobs/`` already exists OR if ``scripts/`` doesn't
    exist. Called once per org at daemon startup, before any runner uses the
    directory. Companion to the DB-side ``script_requests`` → ``jobs`` rename
    in ``Database._migrate_jobs_table_if_needed``: without this, every historic
    row's ``stdout_path``/``stderr_path`` (now pointing under ``jobs/``) would
    404.
    """
    org_root = Path(org_root)
    scripts_dir = org_root / "scripts"
    jobs_dir = org_root / "jobs"

    if jobs_dir.exists():
        return  # already migrated (or fresh install with the new layout)
    if not scripts_dir.exists():
        return  # nothing to migrate

    scripts_dir.rename(jobs_dir)
    for entry in jobs_dir.iterdir():
        if entry.name.startswith("SR-"):
            entry.rename(jobs_dir / ("JOB-" + entry.name[len("SR-"):]))


def migrate_artifacts_layout(org_root: Path | str) -> None:
    """Idempotent on-disk migration for the 2026-06-02 assets→artifacts +
    per-agent artifacts→output rename. Safe to re-run.

    - ``<org_root>/assets/`` → ``<org_root>/artifacts/``
    - ``<org_root>/workspaces/*/artifacts/`` → ``<org_root>/workspaces/*/output/``

    Skips when source absent or destination exists. Logs each action. Without
    this, an upgraded daemon starting against an un-migrated runtime would
    create an empty ``artifacts/`` dir beside the populated ``assets/``,
    stranding the old store from ``happyranch artifacts list/get``.
    """
    org_root = Path(org_root)
    logger = logging.getLogger("happyranch.daemon")
    old_shared = org_root / "assets"
    new_shared = org_root / "artifacts"
    if old_shared.is_dir() and not new_shared.exists():
        shutil.move(str(old_shared), str(new_shared))
        logger.info("migrated org-shared store: %s → %s", old_shared, new_shared)
    elif old_shared.is_dir() and new_shared.exists():
        logger.warning(
            "migrate_artifacts_layout: both %s and %s exist — skipping move. "
            "Resolve manually (merge contents or remove one) before restarting the daemon.",
            old_shared, new_shared,
        )
    workspaces = org_root / "workspaces"
    if workspaces.is_dir():
        for ws in workspaces.iterdir():
            if not ws.is_dir():
                continue
            old_per_agent = ws / "artifacts"
            new_per_agent = ws / "output"
            if old_per_agent.is_dir() and not new_per_agent.exists():
                shutil.move(str(old_per_agent), str(new_per_agent))
                logger.info("migrated per-agent dir: %s → %s", old_per_agent, new_per_agent)
            elif old_per_agent.is_dir() and new_per_agent.exists():
                logger.warning(
                    "migrate_artifacts_layout: both %s and %s exist — skipping move. "
                    "Resolve manually before restarting the daemon.",
                    old_per_agent, new_per_agent,
                )


@dataclass(eq=False)
class _JobRegistration:
    # Strong references: neither a slug nor a recycled integer id owns a child.
    db: object
    org_slug: str
    job_id: str
    task_id: str
    agent_id: str
    task: asyncio.Task
    owner: object
    owner_identity: tuple | None
    startup_binding: tuple | None = None
    org_attachment: object | None = None
    attempt: tuple | None = None
    process: asyncio.subprocess.Process | None = None
    cause: object | None = None
    observed: bool = False
    tail_failed: bool = False
    cap_event: asyncio.Event | None = None
    released: bool = False
    output_tasks: tuple[asyncio.Task, ...] = ()
    output_process: asyncio.subprocess.Process | None = None
    output_error: Exception | None = None

    @property
    def key(self):
        return self.db, self.job_id


@dataclass(eq=False)
class _JobCause:
    registration: _JobRegistration
    process: asyncio.subprocess.Process
    reason: str
    attempt: tuple | None = None
    external: bool = False


_INFLIGHT: dict[tuple, _JobRegistration] = {}
_RUNNER_TASKS: dict[tuple, _JobRegistration] = {}
_KILL_REASON_OVERRIDE: dict[tuple, _JobCause] = {}


def _owner_identity(owner):
    s = owner.state
    return (s.org_slug, s.kind, s.source_id, s.occurrence_id, s.agent_id,
            s.task_id, s.session_id, s.preparation_id)


def _attempt_identity(owner):
    if not owner.state.attempts:
        return None
    a = owner.state.attempts[-1]
    return a.attempt_id, a.ordinal, a.boundary


def _require_job_attachment(db, org_slug):
    # Reuse the daemon's actual resolver, never a same-slug/path row join.
    # Standalone absent-layout callers have no resolver; production installs it.
    if _ORCH_RESOLVER is not None:
        current = _ORCH_RESOLVER(org_slug)
        if current is None or current._db is not db:
            from runtime.orchestrator.team_move import DeferredTeamMove
            raise DeferredTeamMove('job_registration_unavailable')


def _validate_registration_identity(registration, *, running_task=False):
    from runtime.orchestrator.producer_move import owner_for
    from runtime.orchestrator.team_move import DeferredTeamMove
    r = registration
    _require_job_attachment(r.db, r.org_slug)
    if (r.org_attachment is not None and (r.org_attachment.db is not r.db
            or r not in r.org_attachment._native_task_users)):
        raise DeferredTeamMove('job_registration_unavailable')
    if (_RUNNER_TASKS.get(r.key) is not r or r.released or r.task.done()
            or running_task and asyncio.current_task() is not r.task):
        raise DeferredTeamMove('job_registration_unavailable')
    admission = owner_for(r.db)
    if r.owner is None:
        if admission is not None:
            raise DeferredTeamMove('job_registration_unavailable')
    else:
        o = r.owner
        if (admission is not o.admission or admission.db is not r.db
                or admission.move.store.org_slug != r.org_slug
                or _owner_identity(o) != r.owner_identity
                or admission.live.get(('job', r.job_id)) is not o or not o.owner_live
                or _attempt_identity(o) != r.attempt):
            raise DeferredTeamMove('job_registration_unavailable')
    record = r.db.get_job(r.job_id)
    if record is None or (record.task_id, record.agent_name) != (r.task_id, r.agent_id):
        raise DeferredTeamMove('job_registration_unavailable')
    return r


def _validate_registration(registration, *, running_task=False):
    """Exhaustive durable validation at ownership/lifecycle boundaries."""
    from runtime.orchestrator.team_move import DeferredTeamMove
    r = _validate_registration_identity(registration, running_task=running_task)
    if r.owner is not None:
        with r.db._lock:
            states = r.owner.admission.history_uncommitted()
            if states.get(('job', r.job_id, r.owner.state.occurrence_id)) != r.owner.state:
                raise DeferredTeamMove('team_move_producer_evidence_changed')
    return r


def _validate_output_registration(registration):
    """Fresh native identity, not cached history clearance or process liveness.

    Only the two actual owned pumps may publish. A reaped Process may still
    have buffered output; its registration remains in-flight until pumps join.
    """
    from runtime.orchestrator.team_move import DeferredTeamMove
    r = _validate_registration_identity(registration)
    if (r.output_process is None or r.process is not r.output_process
            or _INFLIGHT.get(r.key) is not r
            or asyncio.current_task() not in r.output_tasks):
        raise DeferredTeamMove('job_registration_unavailable')
    return r


def _capture_registration(db, job_id, owner):
    from runtime.orchestrator.team_move import DeferredTeamMove
    r = _RUNNER_TASKS.get((db, job_id))
    if r is None or r.owner is not owner:
        raise DeferredTeamMove('job_registration_unavailable')
    return _validate_registration(r, running_task=True)


def _signal_registered_job(r, reason, sig=signal.SIGTERM):
    """No await between exact ownership/cause comparison and signal issuance."""
    if r.released or _RUNNER_TASKS.get(r.key) is not r:
        return False
    _validate_registration(r)
    proc = r.process
    if proc is None or _INFLIGHT.get(r.key) is not r or proc.returncode is not None:
        return False
    # The pump may already have detected its cap before its watcher runs.
    if r.cause is None and r.cap_event is not None and r.cap_event.is_set():
        r.cause = _JobCause(r, proc, 'output_cap', r.attempt)
    provisional = None
    if r.cause is None:
        provisional = _JobCause(r, proc, reason, r.attempt, external=True)
        r.cause = provisional
        _KILL_REASON_OVERRIDE[r.key] = provisional
    try:
        os.killpg(proc.pid, sig)
    except ProcessLookupError:
        if provisional is not None and r.cause is provisional:
            r.cause = None
            if _KILL_REASON_OVERRIDE.get(r.key) is provisional:
                del _KILL_REASON_OVERRIDE[r.key]
        return False
    except BaseException:
        if provisional is not None and r.cause is provisional:
            r.cause = None
            if _KILL_REASON_OVERRIDE.get(r.key) is provisional:
                del _KILL_REASON_OVERRIDE[r.key]
        raise
    return True


def _release_registration(r):
    from runtime.orchestrator.team_move import DeferredTeamMove
    _validate_registration(r, running_task=True)
    if r.tail_failed or any(not pump.done() for pump in r.output_tasks):
        raise DeferredTeamMove('job_output_tail_unfinished')
    r.released = True
    if _INFLIGHT.get(r.key) is r:
        del _INFLIGHT[r.key]
    cause = _KILL_REASON_OVERRIDE.get(r.key)
    if (cause is not None and cause is r.cause and cause.registration is r
            and cause.process is r.process and cause.attempt == r.attempt):
        del _KILL_REASON_OVERRIDE[r.key]


_HEAD_CAP_BYTES = 65536  # spec §3.1, §6.2

# ---------------------------------------------------------------------------
# Job-terminal → resume-blocked-task bridge
# ---------------------------------------------------------------------------
# ``fire_resume_check_for_job`` is called from ``_run_and_persist`` (and from
# ``reject_job_from_notification``) after the terminal status has been
# committed to the DB.  Both call sites run on the daemon's main asyncio event
# loop, so the call is direct (no ``run_coroutine_threadsafe`` needed).
#
# The module-level ``attach_jobs_resume_main_loop`` wiring function is kept
# symmetric with ``Orchestrator.attach_thread_queue`` — a future caller that
# runs on a worker thread would use ``_RESUME_MAIN_LOOP`` to bridge across.
# The resolver also checks that job registrations still own the actual current
# DB attachment. It grants no ownership from matching slugs or rows.
# ---------------------------------------------------------------------------

_RESUME_MAIN_LOOP: asyncio.AbstractEventLoop | None = None
_ORCH_RESOLVER: "Callable[[str], Orchestrator | None] | None" = None
_JOBS_STARTUP: tuple | None = None
_RESUME_STARTUP_GATE: object | None = None


def bind_jobs_startup_references(state, org=None) -> None:
    """Reference-only early/dynamic attachment, before production exposure.

    The early singleton closes anonymous lower-runner admission before P4.
    P4 still owns the one actual loop/resolver binding; this performs no I/O.
    """
    from runtime.daemon.startup_recovery import StartupUnavailable
    global _JOBS_STARTUP
    gate = state._startup_recovery
    if gate is not None:
        from runtime.daemon.startup_recovery import StartupRecovery
        if (not isinstance(gate, StartupRecovery) or gate.state is not state
                or gate.app.state.daemon is not state):
            raise StartupUnavailable("startup_jobs_binding_changed")
        if _JOBS_STARTUP is None:
            _JOBS_STARTUP = (state, gate, gate.epoch)
        elif (_JOBS_STARTUP[0] is not state or _JOBS_STARTUP[1] is not gate
              or _JOBS_STARTUP[2] != gate.epoch):
            raise StartupUnavailable("startup_jobs_binding_changed")
    if org is not None:
        previous = getattr(org.db, "_jobs_startup_refs", None)
        if previous is not None and (not isinstance(previous, tuple) or len(previous) != 3
                or previous[2] is not org or (previous[0] is not state and previous[1] is not None)
                or (previous[1] is not None and previous[1] is not gate)):
            raise StartupUnavailable("startup_jobs_binding_changed")
        org.db._jobs_startup_refs = (state, gate, org)


def require_job_admission(org=None, *, db=None, org_slug=None) -> None:
    """Fresh work only; never use this for signals, pumps or settlement."""
    from runtime.daemon.startup_recovery import (
        StartupUnavailable, _require_ordinary_ready, require_ready,
    )
    if org is not None:
        _require_ordinary_ready(org)
        db, org_slug = org.db, org.slug
    binding = _JOBS_STARTUP
    if binding is None:
        if (getattr(db, "_jobs_startup_refs", None) is not None
                or org is not None and org._startup_state is not None
                or _RESUME_STARTUP_GATE is not None):
            raise StartupUnavailable("startup_jobs_binding_changed")
        return  # explicitly standalone: no mounted production state/DB/bridge
    state, gate, epoch = binding
    require_ready(state)
    try:
        current_loop = asyncio.get_running_loop()
    except RuntimeError as exc:
        raise StartupUnavailable("startup_jobs_binding_changed") from exc
    current = state.orgs.get(org_slug)
    refs = getattr(db, "_jobs_startup_refs", None)
    if (gate is not state._startup_recovery or epoch != gate.epoch
            or current is None or org is not None and current is not org
            or current.db is not db or current.orchestrator._db is not db
            or not isinstance(refs, tuple) or len(refs) != 3
            or refs[0] is not state or refs[1] is not gate or refs[2] is not current
            or current_loop is not gate._loop
            or not jobs_resume_binding_matches(gate._loop, gate._jobs_resolver, gate)
            or _ORCH_RESOLVER(org_slug) is not current.orchestrator):
        raise StartupUnavailable("startup_jobs_binding_changed")
    require_ready(current)


def _require_runner_admission(registration, job_id, task_id, move_owner) -> None:
    from runtime.daemon.startup_recovery import StartupUnavailable
    if registration is None:
        require_job_admission()
        if job_id is not None or move_owner is not None:
            raise ValueError("job_registration_unavailable")
        return
    require_job_admission(db=registration.db, org_slug=registration.org_slug)
    if (registration.startup_binding is not _JOBS_STARTUP
            or (registration.job_id, registration.task_id, registration.owner)
            != (job_id, task_id, move_owner)):
        raise StartupUnavailable("startup_jobs_binding_changed")
    _validate_registration(registration, running_task=True)
    require_job_admission(db=registration.db, org_slug=registration.org_slug)


def attach_jobs_resume_main_loop(
    loop: asyncio.AbstractEventLoop,
    orch_resolver: "Callable[[str], Orchestrator | None]",
    startup_recovery=None,
) -> None:
    """Bind the daemon's main loop + an org-slug → Orchestrator resolver.

    Production P4 calls this once on its actual retained binding Task, with
    the SAME gate and resolver. The early state/DB attachment closes lower
    admission before P4 without doing recovery or I/O here.
    """
    global _RESUME_MAIN_LOOP, _ORCH_RESOLVER, _RESUME_STARTUP_GATE
    from runtime.daemon.startup_recovery import StartupUnavailable
    if _JOBS_STARTUP is not None:
        state, gate, epoch = _JOBS_STARTUP
        if (startup_recovery is not gate or state._startup_recovery is not gate
                or epoch != gate.epoch or loop is not gate._loop
                or orch_resolver is not gate._jobs_resolver
                or gate._binding is None or gate._binding.task is not asyncio.current_task()
                or asyncio.get_running_loop() is not loop):
            raise StartupUnavailable("startup_jobs_binding_changed")
        gate._require_phase_attachments("returned", binding=gate._binding)
    elif startup_recovery is not None:
        raise StartupUnavailable("startup_jobs_binding_changed")
    _RESUME_MAIN_LOOP = loop
    _ORCH_RESOLVER = orch_resolver
    _RESUME_STARTUP_GATE = startup_recovery


def jobs_resume_binding_matches(
    loop: asyncio.AbstractEventLoop | None,
    orch_resolver: "Callable[[str], Orchestrator | None] | None",
    startup_recovery=None,
) -> bool:
    """Identity readback only; never invoke a resolver or recover a job."""
    return (_RESUME_MAIN_LOOP is loop and _ORCH_RESOLVER is orch_resolver
            and _RESUME_STARTUP_GATE is startup_recovery)


def fire_resume_check_for_job(org: "object", job_id: str) -> None:
    """Check whether any task blocked on *job_id* can now be resumed.

    Calls ``_maybe_resume_blocked_task`` for every BLOCKED_ON_JOB task whose
    ``blocked_on_job_ids`` list contains *job_id*.

    Called synchronously from ``_run_and_persist`` and
    ``reject_job_from_notification`` after the terminal DB commit, both of
    which run on the daemon's main asyncio event loop.

    Explicit standalone orgs retain the original absent-orchestrator no-op.
    Mounted production requires the actual ready attachment before its scan;
    refusal leaves the durable blocked waiter for its original rediscovery.
    """
    require_job_admission(org)
    orch = getattr(org, "orchestrator", None)
    if orch is None:
        return
    db = getattr(orch, "_db", None)
    if db is None:
        return
    from runtime.orchestrator.run_step import _maybe_resume_blocked_task

    # Quotes around job_id are intentional: suffix-anchored so JOB-1 doesn't match JOB-12.
    # Phase 3: only in_progress(blocked_on_job) tasks are parked on jobs.
    # The boot-time migration flips any legacy blocked(blocked_on_job) rows
    # before request handling, so this scanner never sees them at runtime.
    with db._lock:
        require_job_admission(org)
        rows = db._conn.execute(
            "SELECT id FROM tasks "
            "WHERE status = 'in_progress' "
            "AND block_kind = 'blocked_on_job' "
            "AND blocked_on_job_ids LIKE ?",
            (f'%"{job_id}"%',),
        ).fetchall()
    for row in rows:
        require_job_admission(org)
        from runtime.orchestrator.team_move import DeferredTeamMove
        try:
            _maybe_resume_blocked_task(orch, row['id'], trigger='job_terminal', triggering_job_id=job_id)
        except DeferredTeamMove:
            # C1 revalidates and redelivers the actual task; do not manufacture
            # another terminal callback or turn a committed job into failure.
            orch._team_moves.defer_task(row['id'], {'trigger': 'job_terminal', 'triggering_job_id': job_id})


def register_runner_task(job_id: str, task: asyncio.Task, *, db, org_slug, move_owner=None):
    """Called synchronously after create_task, before the caller yields."""
    from runtime.orchestrator.producer_move import owner_for
    from runtime.orchestrator.team_move import DeferredTeamMove
    key = db, job_id
    try:
        require_job_admission(db=db, org_slug=org_slug)
        if key in _RUNNER_TASKS:
            raise DeferredTeamMove('job_registration_unavailable')
        record = db.get_job(job_id)
        admission = owner_for(db)
        if record is None or (admission is None) != (move_owner is None):
            raise DeferredTeamMove('job_registration_unavailable')
        identity = None if move_owner is None else _owner_identity(move_owner)
        if identity is not None and identity[:6] != (
                org_slug, 'job', job_id, job_id, record.agent_name, record.task_id):
            raise DeferredTeamMove('job_registration_unavailable')
        require_job_admission(db=db, org_slug=org_slug)
        r = _JobRegistration(db, org_slug, job_id, record.task_id, record.agent_name,
                             task, move_owner, identity, startup_binding=_JOBS_STARTUP,
                             attempt=None if move_owner is None else _attempt_identity(move_owner))
        if _JOBS_STARTUP is not None:
            r.org_attachment = db._jobs_startup_refs[2]
            if not r.org_attachment._acquire_native_task(r):
                raise DeferredTeamMove('job_registration_unavailable')
        _RUNNER_TASKS[key] = r
        _validate_registration(r)
        require_job_admission(db=db, org_slug=org_slug)
    except BaseException:
        task.cancel()  # registration refusal must never leave an unregistered launch
        if 'r' in locals():
            if r.org_attachment is not None and r in r.org_attachment._native_task_users:
                r.org_attachment._release_native_task(r)
            if _RUNNER_TASKS.get(key) is r:
                del _RUNNER_TASKS[key]
        raise
    def finished(done):
        error = None if done.cancelled() else done.exception()
        # Failed/ambiguous persistence keeps the actual uncertain registration.
        if r.released and done is r.task and _RUNNER_TASKS.get(key) is r:
            if r.org_attachment is not None:
                r.org_attachment._release_native_task(r)
            del _RUNNER_TASKS[key]
            if error is not None:
                logging.getLogger(__name__).error('Released job postcommit tail failed: %r', error)
        elif _RUNNER_TASKS.get(key) is r:
            logging.getLogger(__name__).error(
                'job_liveness_unknown: retaining original job owner %s/%s after unfinished tail: %r',
                r.org_slug, r.job_id, error)
    task.add_done_callback(finished)
    return r


def _interpreter_binary(interpreter: str) -> str | None:
    return shutil.which(interpreter)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass
class JobRunResult:
    status: str           # "completed" | "failed"
    exit_code: int | None
    duration_ms: int | None
    stdout_head: str | None
    stderr_head: str | None
    stdout_bytes: int
    stderr_bytes: int
    truncated_stdout: bool
    truncated_stderr: bool
    reason: str | None = None   # populated only when status == "failed"


_READ_CHUNK_BYTES = 8192


async def _pump_stream(
    stream: asyncio.StreamReader,
    label: str,
    out_path: str,
    publish: Callable[[dict], Awaitable[None] | None],
    head_buf: list[bytes],
    head_capped: list[bool],
    byte_counter: list[int],
    max_bytes: int | None = None,
    cap_event: asyncio.Event | None = None,
) -> None:
    """Append bytes to disk, fan out line events, fill head buffer until cap.

    Reads in chunks (not by line) so the byte counter advances regardless of
    whether the source process emits newlines — a server writing a 200 KB
    progress bar without flushing newlines must still trip the output cap.
    Line events are emitted by buffering chunks and splitting on `\\n`; a
    trailing partial line is flushed on EOF.

    Each chunk is written to disk and counted before the cap check. When
    ``max_bytes`` and ``cap_event`` are supplied, this stream's counter is
    ``>= max_bytes``, and the shared event is not already set, set the event
    and return immediately, before head-buffer or line-event processing of
    that chunk. The ``run_job`` cap watcher closes the read transports and
    SIGKILLs the process group. This detecting branch does not establish a
    strict file-size or total overshoot bound for both streams sharing the event.
    """
    line_buf = bytearray()
    with open(out_path, "ab", buffering=0) as f:
        while True:
            chunk = await stream.read(_READ_CHUNK_BYTES)
            if not chunk:
                # EOF — flush any trailing partial line as its own event.
                if line_buf:
                    await _emit_line(bytes(line_buf), label, publish)
                    line_buf.clear()
                return
            f.write(chunk)
            byte_counter[0] += len(chunk)
            if (
                max_bytes is not None
                and cap_event is not None
                and byte_counter[0] >= max_bytes
                and not cap_event.is_set()
            ):
                cap_event.set()
                # Stop pumping immediately: backpressure the writer so the
                # OS pipe buffer fills, then SIGKILL (fired by the watcher)
                # closes the pipe before more bytes flow. Without this, on
                # platforms with large pipe buffers the writer can stream a
                # full payload through us between cap detection and SIGKILL.
                return
            if not head_capped[0]:
                head_so_far = sum(len(b) for b in head_buf)
                room = _HEAD_CAP_BYTES - head_so_far
                if len(chunk) <= room:
                    head_buf.append(chunk)
                else:
                    if room > 0:
                        head_buf.append(chunk[:room])
                    head_capped[0] = True
            # Split chunk on newlines to keep emitting line events for normal
            # well-behaved streams. Carry any partial trailing line forward.
            line_buf.extend(chunk)
            while True:
                nl = line_buf.find(b"\n")
                if nl < 0:
                    break
                line = bytes(line_buf[: nl + 1])
                del line_buf[: nl + 1]
                await _emit_line(line, label, publish)


async def _emit_line(line: bytes, label: str, publish: Callable[[dict], Awaitable[None] | None]) -> None:
    try:
        text = line.decode("utf-8", errors="replace").rstrip("\n")
    except Exception:
        text = "<binary>"
    # Existing standalone callbacks may be synchronous. Production awaits its
    # owned publication directly: no per-line Task or intermediate queue.
    pending = publish({"kind": "line", "stream": label, "line": text, "ts": _now_iso()})
    if inspect.isawaitable(pending):
        await pending


async def run_job(
    *,
    job_id: str | None = None,
    script_text: str,
    interpreter: str,
    cwd: str,
    stdout_path: str,
    stderr_path: str,
    max_runtime_seconds: int | None,
    publish: Callable[[dict], Awaitable[None] | None],
    max_output_bytes: int | None = None,
    task_id: str | None = None,
    workspace_root: str | None = None,
    move_owner=None,
    registration=None,
) -> JobRunResult:
    """Spawn the script, pump streams, return JobRunResult.

    `publish` carries output only. The original route owner commits terminal
    composition before publishing its durable terminal hint.
    Every named production job requires its actual registration.

    `max_runtime_seconds=None` means unbounded — the runner awaits the
    subprocess without an ``asyncio.wait_for`` wrapper. Positive int means
    the subprocess is SIGTERM'd (then SIGKILL'd) on expiry and the result's
    ``reason`` is set to ``"timeout"``.

    `max_output_bytes=None` means unbounded per stream. Positive int means
    either stream exceeding the threshold triggers an immediate SIGKILL of
    the process group; result is ``status="failed", reason="output_cap"``.
    The first established real local or successfully signalled external cause
    wins for this execution. A later Stop cannot rewrite timeout/output_cap.
    """
    _require_runner_admission(registration, job_id, task_id, move_owner)
    binary = _interpreter_binary(interpreter)
    if binary is None:
        raise FileNotFoundError(f"interpreter unavailable: {interpreter}")

    started = datetime.now(timezone.utc)
    scratch_token = None
    from runtime.orchestrator.producer_move import producer_preparation_effects
    if task_id is not None and workspace_root is not None:
        with producer_preparation_effects(move_owner):
            scratch = prepare_task_scratch(
                workspace=Path(workspace_root), task_id=task_id,
                producer_kind="job", producer_id=job_id or "anonymous",
            )
            scratch_token = activate_task_scratch(scratch)
    try:
        with producer_preparation_effects(move_owner):
            child_env = _sanitize_child_env(dict(os.environ))
            if scratch_token is not None:
                # A daemon launched by an agent inherits that parent session's
                # containment markers. This job owns a new, task-bound contract;
                # discard only superseded markers before shared validation.
                # TMPDIR/TMP/TEMP retain unexpected-steering refusal.
                child_env.pop("HAPPYRANCH_TASK_TMP_ROOT", None)
                child_env.pop("HAPPYRANCH_TASK_SCRATCH_MANIFEST", None)
            child_env = apply_task_scratch_environment(child_env)
        if move_owner is not None:
            move_owner.executor = interpreter
            move_owner.before_fifo()
            move_owner.commit()
            if registration is not None:
                registration.attempt = _attempt_identity(move_owner)
                _validate_registration(registration, running_task=True)
        # Last fresh-effect fence. No gate/DB lock spans native creation.
        _require_runner_admission(registration, job_id, task_id, move_owner)
        runner_task = asyncio.current_task()

        async def create_process():
            # The shield introduces a scheduling turn before actual creation.
            # Authenticate this original parent/registration again here, not
            # a caller flag or an arbitrary supplied launch coroutine.
            if registration is not None:
                from runtime.daemon.startup_recovery import StartupUnavailable
                require_job_admission(db=registration.db, org_slug=registration.org_slug)
                if (registration.task is not runner_task
                        or registration.startup_binding is not _JOBS_STARTUP):
                    raise StartupUnavailable("startup_jobs_binding_changed")
                _validate_registration(registration)
                require_job_admission(db=registration.db, org_slug=registration.org_slug)
            else:
                require_job_admission()
            if runner_task.cancelling():
                raise asyncio.CancelledError()
            return await asyncio.create_subprocess_exec(
                binary,
                "-",  # read script from stdin (bash/sh/zsh/python3 all honor this)
                cwd=cwd,
                env=child_env,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
            )

        spawn = asyncio.create_task(create_process())
        cancellation = None
        cancellation_traceback = None
        spawn_errors = []

        def retain_spawn_error(error):
            if len(spawn_errors) < 8 and not any(error is item for item in spawn_errors):
                spawn_errors.append(error)

        from runtime.daemon.startup_recovery import _await_startup_owned
        try:
            proc = await _await_startup_owned(spawn, retain_spawn_error)
        except BaseException as primary:
            primary_traceback = primary.__traceback__
            native_error = None if not spawn.done() or spawn.cancelled() else spawn.exception()
            seen = set()
            for secondary in (native_error, *spawn_errors):
                if secondary is not None and secondary is not primary and id(secondary) not in seen:
                    seen.add(id(secondary))
                    primary.add_note("original job spawn secondary: " + repr(secondary)[:512])
            if not isinstance(primary, asyncio.CancelledError):
                raise
            cancellation, cancellation_traceback = primary, primary_traceback
            # The same owned completion has been consumed. Obtain a handle only
            # for genuine success; a later failure cannot replace cancellation.
            if spawn.cancelled() or spawn.exception() is not None:
                raise primary.with_traceback(primary_traceback)
            proc = spawn.result()
        if registration is not None:
            # Capture even when scratch reset or later accounting fails. The
            # original org native-user barrier retains DB-close ownership.
            registration.process = proc
            _INFLIGHT[registration.key] = registration
            _validate_registration(registration, running_task=True)
    finally:
        if scratch_token is not None:
            reset_task_scratch(scratch_token)
    if move_owner is not None:
        move_owner.on_started()

    startup_closed = (registration is not None and registration.startup_binding is not None
                      and (registration.startup_binding[1]._stop.is_set()
                           or registration.startup_binding[1]._status != "ready"
                           or registration.startup_binding[1]._closed))
    if registration is not None and registration.startup_binding is not None:
        from runtime.daemon.startup_recovery import StartupUnavailable
        try:
            require_job_admission(db=registration.db, org_slug=registration.org_slug)
        except StartupUnavailable:
            # Creation already happened; actual listener loss forbids script
            # input while identity-only original-owner stop/reap remains owed.
            startup_closed = True
    if cancellation is None and not startup_closed:
        proc.stdin.write(script_text.encode("utf-8"))
    proc.stdin.close()

    stdout_head: list[bytes] = []
    stderr_head: list[bytes] = []
    stdout_capped: list[bool] = [False]
    stderr_capped: list[bool] = [False]
    stdout_bytes = [0]
    stderr_bytes = [0]

    # Shared cap event — set by EITHER pump when its per-stream byte counter
    # crosses max_output_bytes. The watcher coroutine awaits it and SIGKILLs.
    cap_event = asyncio.Event()
    if registration is not None:
        registration.cap_event = cap_event

    def signal_child(sig):
        if registration is None:
            try:
                os.killpg(proc.pid, sig)
            except ProcessLookupError:
                pass
        else:
            _signal_registered_job(registration, 'timeout' if sig == signal.SIGTERM else 'output_cap', sig)

    pump_out = asyncio.create_task(_pump_stream(
        proc.stdout, "stdout", stdout_path, publish, stdout_head, stdout_capped, stdout_bytes,
        max_bytes=max_output_bytes, cap_event=cap_event,
    ))
    pump_err = asyncio.create_task(_pump_stream(
        proc.stderr, "stderr", stderr_path, publish, stderr_head, stderr_capped, stderr_bytes,
        max_bytes=max_output_bytes, cap_event=cap_event,
    ))

    pumps = (pump_out, pump_err)
    if registration is not None:
        registration.output_tasks = pumps
        registration.output_process = proc

    async def _watch_cap() -> None:
        await cap_event.wait()
        if registration is not None and registration.cause is None:
            registration.cause = _JobCause(registration, proc, "output_cap", registration.attempt)
        # Close the stdout/stderr read transports BEFORE SIGKILL. Two reasons:
        # 1. The writer gets SIGPIPE on its next write — stops pumping data
        #    into the pipe between our cap-detection and SIGKILL. Without
        #    this, on macOS the writer can flush its full payload (e.g. a
        #    200 KB single `write()`) through us during the kill race.
        # 2. asyncio's `proc.wait()` only resolves once the SubprocessTransport
        #    has torn down — which requires the pipe transports to close. If
        #    we leave bytes unread in stdout, the pipe stays open and
        #    `proc.wait()` hangs forever, even after the child is reaped.
        for fd in (1, 2):  # stdout, stderr
            try:
                # touches asyncio internal: proc._transport is BaseSubprocessTransport;
                # get_pipe_transport(fd) is documented but Process._transport isn't.
                t = proc._transport.get_pipe_transport(fd)
                if t is not None:
                    t.close()
            except Exception:
                pass
        try:
            signal_child(signal.SIGKILL)
        except ProcessLookupError:
            pass

    watcher = asyncio.create_task(_watch_cap())

    reason: str | None = None
    primary_failure = None
    try:
        if cancellation is not None:
            raise cancellation.with_traceback(cancellation_traceback)
        if startup_closed:
            _signal_registered_job(registration, "daemon_shutdown")
            try:
                await asyncio.wait_for(proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                _signal_registered_job(registration, "daemon_shutdown", signal.SIGKILL)
                await proc.wait()
        if max_runtime_seconds is None:
            await proc.wait()
        else:
            await asyncio.wait_for(proc.wait(), timeout=max_runtime_seconds)
        # Child exit does not end owned buffered output. Await the actual
        # pumps/publications; a fixed drain deadline would cut off a verbose
        # but legitimate tail. Shutdown/cancellation still reconciles below.
        pump_results = await asyncio.gather(pump_out, pump_err, return_exceptions=True)
        if registration is not None:
            registration.tail_failed |= any(isinstance(x, BaseException) for x in pump_results)
        if cap_event.is_set():
            status = "failed"
            reason = "output_cap"
        else:
            status = "completed"
        exit_code = proc.returncode
    except asyncio.TimeoutError:
        reason = "timeout"
        if registration is not None and registration.cause is None:
            registration.cause = _JobCause(registration, proc, "timeout", registration.attempt)
        try:
            signal_child(signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(proc.wait(), timeout=5)
        except asyncio.TimeoutError:
            try:
                signal_child(signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(proc.wait(), timeout=2)
            except asyncio.TimeoutError:
                pass
        # Drain pumps briefly; ignore errors.
        try:
            pump_results = await asyncio.wait_for(asyncio.gather(pump_out, pump_err, return_exceptions=True), timeout=2)
            if registration is not None:
                registration.tail_failed |= any(isinstance(x, BaseException) for x in pump_results)
        except asyncio.TimeoutError:
            if registration is not None:
                registration.tail_failed = True
        status = "failed"
        exit_code = proc.returncode
    except asyncio.CancelledError as primary:
        primary_failure = primary
        # Includes cancellation while native creation was in flight. Cleanup
        # uses identity only; closure of fresh admission cannot forbid reap.
        try:
            if registration is None:
                if proc.returncode is None:
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
            elif startup_closed:
                _signal_registered_job(registration, "daemon_shutdown", signal.SIGKILL)
            else:
                # Cancellation alone does not prove a task-ended/Stop cause.
                # Keep any real earlier cause, signal only this original owner.
                _validate_registration(registration, running_task=True)
                if (_INFLIGHT.get(registration.key) is registration
                        and registration.process is proc and proc.returncode is None):
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
            await proc.wait()
        except BaseException as cleanup:
            primary.add_note(f"job cancellation cleanup unfinished: {cleanup!r}")
        raise
    finally:
        # A pump owns its awaited publication, including buffered output after
        # child exit. Error/cancel/timeout must not leave that work detached.
        # Cancellation during reconciliation propagates without retirement;
        # the registration and its actual output_tasks remain attributable.
        for pump in pumps:
            if not pump.done():
                if registration is not None:
                    registration.tail_failed = True
                pump.cancel()
        watcher.cancel()
        try:
            pump_results = await asyncio.gather(*pumps, return_exceptions=True)
            if registration is not None:
                registration.tail_failed |= any(isinstance(x, BaseException) for x in pump_results)
            await watcher
        except asyncio.CancelledError as cleanup:
            # Ignore only the watcher's own cancellation. Preserve the first
            # runner cancellation when its output reconciliation is interrupted.
            if asyncio.current_task().cancelling():
                if registration is not None:
                    registration.tail_failed = True
                if primary_failure is not None:
                    primary_failure.add_note(f"job output reconciliation cancelled: {cleanup!r}")
                    raise primary_failure
                raise
        if (registration is not None and _INFLIGHT.get(registration.key) is registration
                and registration.process is proc and proc.returncode is not None
                and all(pump.done() for pump in pumps)):
            del _INFLIGHT[registration.key]

    finished = datetime.now(timezone.utc)
    duration_ms = int((finished - started).total_seconds() * 1000)

    head_marker_stdout = b"\n[truncated; see file]" if stdout_capped[0] else b""
    head_marker_stderr = b"\n[truncated; see file]" if stderr_capped[0] else b""
    stdout_head_str = b"".join(stdout_head).decode("utf-8", errors="replace") + head_marker_stdout.decode()
    stderr_head_str = b"".join(stderr_head).decode("utf-8", errors="replace") + head_marker_stderr.decode()

    if registration is not None:
        _validate_registration(registration, running_task=True)
        cause = registration.cause
        if cause is not None:
            if (cause.registration is not registration or cause.process is not proc
                    or cause.attempt != registration.attempt
                    or cause.external and _KILL_REASON_OVERRIDE.get(registration.key) is not cause):
                from runtime.orchestrator.team_move import DeferredTeamMove
                raise DeferredTeamMove('job_registration_unavailable')
            status, reason = "failed", cause.reason
        registration.observed = proc.returncode is not None

    result = JobRunResult(
        status=status,
        exit_code=exit_code,
        duration_ms=duration_ms,
        stdout_head=stdout_head_str,
        stderr_head=stderr_head_str,
        stdout_bytes=stdout_bytes[0],
        stderr_bytes=stderr_bytes[0],
        truncated_stdout=stdout_capped[0],
        truncated_stderr=stderr_capped[0],
        reason=reason,
    )

    return result


def in_flight_job_ids(*, db) -> list[str]:
    return [_validate_registration(r).job_id for r in tuple(_INFLIGHT.values()) if r.db is db]


async def terminate_jobs_for_task(
    task_id: str, *, db, org_slug: str,
    inflight_to_task: dict[str, str] | None = None,
    grace_seconds: float = 5.0,
) -> list[str]:
    # DB rows select candidates; only the original actual registration owns signals.
    candidates = inflight_to_task or {}
    targets = [r for r in tuple(_INFLIGHT.values())
               if r.db is db and r.org_slug == org_slug and r.task_id == task_id
               and candidates.get(r.job_id) == task_id]
    signalled = []
    for r in targets:
        if _signal_registered_job(r, 'task_ended'):
            signalled.append(r)
    if signalled:
        await asyncio.sleep(grace_seconds)
    for r in signalled:
        _signal_registered_job(r, 'task_ended', signal.SIGKILL)
    return [r.job_id for r in signalled]


async def terminate_all_inflight(
    *, grace_seconds: int = 5, persist_timeout_seconds: float = 5.0,
) -> None:
    logger = logging.getLogger(__name__)
    signalled = []
    for r in tuple(_INFLIGHT.values()):
        try:
            if _signal_registered_job(r, 'daemon_shutdown'):
                signalled.append(r)
        except Exception:
            logger.exception('Retaining unattributable job shutdown ownership')
    if signalled:
        await asyncio.sleep(grace_seconds)
    for r in signalled:
        try:
            _signal_registered_job(r, 'daemon_shutdown', signal.SIGKILL)
        except Exception:
            logger.exception('Retaining changed job shutdown ownership')
    runners = [r.task for r in tuple(_RUNNER_TASKS.values()) if not r.task.done()]
    if runners:
        # Timeout does not cancel finalization or certify cleanup.
        await asyncio.wait(runners, timeout=persist_timeout_seconds)
