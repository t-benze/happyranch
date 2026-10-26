"""Singular runtime endpoints: get info, register, switch."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel

from runtime.daemon import runtimes as reg
from runtime.daemon.auth import require_token
from runtime.daemon.org_state import _OrgBusyError
from runtime.daemon.state import DaemonState
from runtime.runtime import RuntimeDir

router = APIRouter(dependencies=[require_token()])


class RuntimePath(BaseModel):
    path: str


def _assert_can_swap(state: DaemonState) -> None:
    """Preflight ALL old orgs without fencing or closing any of them."""
    state._require_startup_ready()
    state._assert_startup_idle()
    if state._runner_shutdown:
        raise HTTPException(status_code=409, detail={"code": "daemon_shutting_down"})
    state.team_move_service.assert_no_owner()
    from runtime.orchestrator.team_move_startup import refuse_runtime_detach
    refuse_runtime_detach(state.runtime, diagnostics=state.broken_orgs)
    for org in state.orgs.values():
        try:
            org._assert_runners_idle()
        except _OrgBusyError as exc:
            raise HTTPException(status_code=409, detail={"code": exc.code, "org": org.slug}) from exc
    for org in state.orgs.values():
        if org.db._team_move_coordinator.store.read() is not None:
            from runtime.infrastructure.team_move_store import TeamMoveError
            raise TeamMoveError("team_move_runtime_detach_held")
        try:
            in_flight = org.db.get_nonterminal_task_ids()
            if in_flight:
                raise _OrgBusyError("active_tasks_in_flight", task_ids=in_flight)
        except _OrgBusyError as exc:
            detail = {"code": exc.code, "org": org.slug}
            if exc.task_ids is not None:
                detail["task_ids"] = exc.task_ids
            raise HTTPException(status_code=409, detail=detail) from exc


def _swap(state: DaemonState, runtime: RuntimeDir, *, move_diagnostics=None) -> None:
    """Replace the runtime synchronously; route callers hold orgs_lock."""
    # Same-root registration also reconstructs OrgStates. Consult the OLD
    # instances before construction, then fence stale references via close.
    # No await separates the all-org admission decision from map replacement.
    _assert_can_swap(state)
    state._revoke_thread_releases()
    for org in state.orgs.values():
        org._assert_thread_releases_idle()
    state._revoke_cleanup_diagnostics()
    # Synchronous callers cannot bypass the asynchronous drain barrier.
    for org in state.orgs.values():
        try:
            org._assert_cleanup_diagnostics_idle()
        except _OrgBusyError as exc:
            raise HTTPException(status_code=409, detail={"code": exc.code, "org": org.slug}) from exc
    from runtime.orchestrator.team_move_startup import require_runtime_reader
    diagnostics = dict(move_diagnostics or {})
    diagnostics.update(require_runtime_reader(runtime))
    for org in state.orgs.values():
        org.close()
    new_state = DaemonState.from_runtime(runtime, state.settings, move_diagnostics=diagnostics)
    # Move the worker queue and lock instances into the new state so an
    # in-flight worker pool keeps consuming. Old orgs have no nonterminal tasks
    # or active native runners (checked above).
    new_state.queue = state.queue
    new_state.orgs_lock = state.orgs_lock
    # Replacement retains the ORIGINAL ready epoch. Bind every new OrgState
    # before map publication; never recapture boot's original attachments.
    for org in new_state.orgs.values():
        state._attach_startup_owner(org)
    state.runtime = new_state.runtime
    state.orgs = new_state.orgs
    for org in state.orgs.values():
        org._cleanup_diagnostic_unavailable = "runtime_attachment_unsupported"
    state.broken_orgs = new_state.broken_orgs
    state.profile_coordinator = new_state.profile_coordinator
    from runtime.daemon.direct_connect_projection_sweep import bind_startup_references
    bind_startup_references(state)
    # No live owner can cross the switch (checked above). Keep diagnostic
    # trackers/errors scoped to this runtime rather than the reused org slug.
    from runtime.daemon.team_move_service import TeamMoveService
    state.team_move_service = TeamMoveService(state)
    # Close the old metrics store and adopt the new runtime's store so
    # persisted snapshots always target the ACTIVE runtime's metrics.db.
    if state.metrics_store is not None:
        try:
            state.metrics_store.close()
        except Exception:
            pass  # best-effort close (TestClient cross-thread access can
                  # raise sqlite3.ProgrammingError; connection is discarded)
    state.metrics_store = new_state.metrics_store
    # Reset the snapshot throttle so the freshly-active runtime snapshots
    # promptly on the next scheduler tick.
    state._last_metrics_snapshot_at = 0.0


async def _drain_before_swap(daemon: DaemonState) -> tuple:
    daemon._require_startup_ready()
    try:
        async with daemon.orgs_lock:
            daemon._require_startup_ready()
            daemon._assert_startup_idle()
            captured = daemon._capture_attachments()
            daemon._revoke_thread_releases()
        # C can be active at the initial capture. Join outside org/native locks,
        # then run ALL ordinary busy/hold checks before any switch effects.
        await daemon._drain_thread_releases()
        async with daemon.orgs_lock:
            daemon._assert_attachments_unchanged(captured)
            _assert_can_swap(daemon)
            daemon._revoke_cleanup_diagnostics()
        await daemon._drain_cleanup_diagnostics()
    except _OrgBusyError as exc:
        raise HTTPException(status_code=409, detail={"code": exc.code}) from exc
    return captured


def _renew_swap_preflight(daemon: DaemonState, captured: tuple) -> None:
    try:
        daemon._assert_attachments_unchanged(captured)
        for org in daemon.orgs.values():
            org._assert_thread_releases_idle()
    except _OrgBusyError as exc:
        raise HTTPException(status_code=409, detail={"code": exc.code}) from exc
    _assert_can_swap(daemon)


@router.get("/runtime")
async def get_runtime(request: Request) -> dict:
    state: DaemonState = request.app.state.daemon
    return {"runtime": str(state.runtime.root) if state.runtime else None}


@router.post("/runtime")
async def register_runtime(body: RuntimePath, request: Request) -> dict:
    from runtime.daemon.app import _wire_then_start_workers

    daemon: DaemonState = request.app.state.daemon
    path = Path(body.path).expanduser()
    captured = await _drain_before_swap(daemon)
    async with daemon.orgs_lock:
        # Registration writes the active registry even for the same root.
        # Refuse before those effects, not just before publishing the new map.
        _renew_swap_preflight(daemon, captured)
        if not path.exists():
            path.mkdir(parents=True, exist_ok=True)
        runtime = RuntimeDir.init(path)
        diagnostics = reg.register(path)
        _swap(daemon, runtime, move_diagnostics=diagnostics)
    import asyncio
    _wire_then_start_workers(daemon, asyncio.get_running_loop())
    return {"runtime": str(path.resolve())}


@router.post("/runtime/use")
async def use_runtime(body: RuntimePath, request: Request) -> dict:
    from runtime.daemon.app import _wire_then_start_workers

    daemon: DaemonState = request.app.state.daemon
    path = Path(body.path).expanduser().resolve()
    captured = await _drain_before_swap(daemon)
    async with daemon.orgs_lock:
        _renew_swap_preflight(daemon, captured)
        from runtime.orchestrator.team_move_startup import require_runtime_reader
        runtime = RuntimeDir.load(path)
        diagnostics = require_runtime_reader(runtime)
        diagnostics.update(reg.activate(path))
        _swap(daemon, runtime, move_diagnostics=diagnostics)
    import asyncio
    _wire_then_start_workers(daemon, asyncio.get_running_loop())
    return {"runtime": str(path)}
