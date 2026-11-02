"""Daemon-owned bounded execution, including unattached diagnostic orgs.

Tasks have strong lifetime ownership independent of HTTP callers. Shutdown
cancels/reaps them before DB close. Durable D holds and native owner audits,
never task-map absence or a timeout, govern forward recovery.
"""
from __future__ import annotations

import asyncio
import logging
import re
import threading
from contextlib import contextmanager
from contextvars import copy_context
from typing import Any, Callable

from runtime.infrastructure.database import Database
from runtime.infrastructure.team_move_store import (
    MoveRequest, TeamMoveError, TeamMoveStore, _bytes, _decode,
    validate_team_move_schema,
)
from runtime.orchestrator._paths import OrgPaths
from runtime.orchestrator import team_move
from runtime.orchestrator.team_move_operation import TeamMoveOperation
from runtime.orchestrator.team_move_public import (
    blocker_view, error_view, operation_view, preview_view, public_request, selection_ids,
)


class TeamMoveService:
    def __init__(self, state):
        self.state = state
        self.tasks = {}
        self.errors = {}
        self.diagnostic_sessions = {}
        self.closing = False
        # Only short process-local observations/publications use this lock.
        # Never hold it across native DB/profile/roster work or an await.
        self._observations = threading.RLock()
        self._owners = {}
        self._workers = {}
        self._restricted = {}
        self._unsafe_handles = {}
        self._mutation_locks = {}
        self._close_task = None

    def start_request(self, slug: str, action: Callable) -> asyncio.Task:
        """Own GET and POST work before submission or a cancellable await."""
        with self._observations:
            if self.closing:
                raise TeamMoveError("team_move_service_owner_busy")
        return self._owned_task(slug, action, name="team-move-request:" + slug)

    def _owned_task(self, slug: str, action: Callable, *, name: str) -> asyncio.Task:
        # Admission, OrgState tokens and asyncio task publication are loop-only.
        # Diagnostic-only orgs have no attached token; the service owner fences
        # runtime swap/removal and retains any restricted handle instead.
        self.require_runtime(slug)
        org = self.state.orgs.get(slug)
        token = object()
        # An admitted request transfers its ORIGINAL native lifetime to its
        # runner. Shutdown may have fenced new OrgState tokens in the meantime.
        parent = asyncio.current_task()
        with self._observations:
            inherited = next((lease for _, task, lease in self._owners.values()
                if task is parent and lease is not None and lease[0] is org), None)
        lease = inherited
        if lease is not None:
            lease[2] += 1
        elif org is not None:
            if not org._acquire_native_task(token):
                raise TeamMoveError("team_move_service_owner_busy")
            lease = [org, token, 1]
        with self._observations:
            self._owners[token] = (slug, None, lease)
        coroutine = None
        try:
            coroutine = action()
            task = asyncio.create_task(coroutine, name=name)
        except BaseException:
            if coroutine is not None:
                coroutine.close()
            with self._observations:
                self._owners.pop(token)
            self._release_attachment(lease)
            raise
        with self._observations:
            self._owners[token] = (slug, task, lease)
        task.add_done_callback(lambda done: self._retire(token, done))
        return task

    @staticmethod
    def _release_attachment(lease: Any) -> None:
        if lease is not None:
            lease[2] -= 1
            if lease[2] == 0:
                lease[0]._release_native_task(lease[1])

    def _hold_attachment(self, slug: str, org: Any) -> None:
        """Capture a positively reattached org before its next native await."""
        owner = asyncio.current_task()
        with self._observations:
            token, (_slug, _task, lease) = next((token, entry) for token, entry in self._owners.items()
                if entry[1] is owner)
        if lease is not None:
            if lease[0] is not org:
                raise TeamMoveError("team_move_service_owner_busy")
            return
        native_token = object()
        if not org._acquire_native_task(native_token):
            raise TeamMoveError("team_move_service_owner_busy")
        with self._observations:
            self._owners[token] = (slug, owner, [org, native_token, 1])

    def _retire(self, token: object, task: asyncio.Task) -> None:
        # Always consume late errors, including an owner deliberately retained
        # after uncertain submission, native return or restricted-handle close.
        error = None if task.cancelled() else task.exception()
        with self._observations:
            uncertain = (task in self._restricted
                or any(owner is task for _, owner, *_ in self._workers.values()))
            lease = None if uncertain else self._owners.pop(token)[2]
        self._release_attachment(lease)
        if error is not None:
            logging.getLogger(__name__).warning("Team move owner failed: %s",
                TeamMoveOperation._diagnostic(error).code)

    async def native(self, slug: str, callback: Callable[[], Any], *,
                     publish: Callable | None = None, propagate_cancel: bool = True) -> Any:
        """One joined synchronous interval, retaining ContextVars and results.

        Only service-owned tasks call this leaf. No native lock or cursor may
        escape callback. Publication runs on this loop after actual return,
        including a reservation/open result received during cancellation.
        """
        owner = asyncio.current_task()
        with self._observations:
            if not any(s == slug and task is owner for s, task, _ in self._owners.values()):
                raise TeamMoveError("team_move_service_owner_busy")
        ticket = object()
        returned = threading.Event()
        outcome = {}
        context = copy_context()

        def run() -> None:
            try:
                outcome["result"] = context.run(callback)
            except BaseException as exc:
                outcome["error"] = exc
            finally:
                returned.set()

        # Record submission uncertainty BEFORE the executor call. Even a
        # handoff failure cannot remove the DB-close barrier speculatively.
        with self._observations:
            self._workers[ticket] = (slug, owner, None, returned, outcome, callback)
        try:
            worker = asyncio.get_running_loop().run_in_executor(None, run)
            with self._observations:
                self._workers[ticket] = (slug, owner, worker, returned, outcome, callback)
            cancellation = None
            while not worker.done():
                try:
                    await asyncio.shield(worker)
                except asyncio.CancelledError as exc:
                    cancellation = cancellation or exc
            # A private bridge cancellation/failed submission supplies no evidence
            # about native execution. Leave the original strong owner held.
            worker.result()
            if not returned.is_set():
                raise TeamMoveError("team_move_owner_interrupted")
        except BaseException as exc:
            # Same primary exception; this private fact prevents release of a
            # redelivery reservation while the recorded native owner is held.
            BaseException.__setattr__(exc, "_team_move_native_unretired", True)
            raise
        with self._observations:
            self._workers.pop(ticket)
        if "error" in outcome:
            # Preserve late native failure as primary, even after cancellation.
            raise outcome["error"]
        result = outcome["result"]
        if publish is not None:
            publish(result)
        if cancellation is not None and propagate_cancel:
            raise cancellation
        return result

    def require_runtime(self, slug):
        if re.fullmatch(r"[A-Za-z0-9_-]{1,256}", slug) is None:
            raise TeamMoveError("team_move_invalid_org")
        if self.state.is_idle:
            raise TeamMoveError("no_active_runtime")

    def attached(self, slug):
        self.require_runtime(slug)
        if slug not in self.state.orgs:
            if slug in self.state.broken_orgs:
                raise TeamMoveError("team_move_diagnostic_org_recover_required")
            raise TeamMoveError("unknown_org")
        if self.state.profile_coordinator is None:
            raise TeamMoveError("team_move_native_profiles_unavailable")
        return self.state.orgs[slug]

    def diagnostic_root(self, slug):
        self.require_runtime(slug)
        if "team_move_" not in self.state.broken_orgs.get(slug, ""):
            raise TeamMoveError("unknown_org")
        return self.state.runtime.orgs_dir / slug

    @contextmanager
    def store(self, slug):
        """Show uses only the native strict DB owner, even for a partial roster."""
        self.require_runtime(slug)
        if slug in self.state.orgs:
            yield TeamMoveStore(self.state.orgs[slug].db, slug)
            return
        root = self.diagnostic_root(slug)
        db = Database._open_team_move_recovery(OrgPaths(root).db_path, org_slug=slug, require_active=False)
        primary = None
        try:
            yield TeamMoveStore(db, slug)
        except BaseException as exc:
            primary = exc
            raise
        finally:
            try:
                db.close()
            except BaseException as exc:
                with self._observations:
                    self._unsafe_handles.setdefault(slug, []).append(db)
                if primary is None:
                    raise TeamMoveError("team_move_diagnostic_close_failed") from exc
                primary.add_note("team move diagnostic close failed; retain diagnostics")

    def availability(self, slug: str) -> dict:
        """Read actual release/attachment owners; never install or repair.

        This observation grants no reservation or quiescence. Apply and recovery
        recheck it at admission and the operation revalidates mutable native facts.
        Recovery deliberately permits a held diagnostic org and fenced authority.
        """
        self.require_runtime(slug)
        with self.store(slug) as store, store.db.coherent_read_view() as conn:
            installed = validate_team_move_schema(conn, org_slug=slug)
            row = store.read()
        reasons = list(team_move.NOT_INTEGRATED)
        if not team_move.APPLY_AVAILABLE:
            reasons.append("team_move_apply_disabled")
        if not installed:
            reasons.append("team_move_schema_absent")
        with self._observations:
            closing = self.closing
        if closing:
            reasons.append("daemon_owner_stopping")
        profiles, host = self.state.profile_coordinator, self.state.host_supervisor
        if profiles is None or profiles.orgs is not self.state.orgs:
            reasons.append("team_move_native_profiles_unavailable")
        if host is None:
            reasons.append("team_move_native_host_unavailable")
        elif host._admission.is_shutdown():
            reasons.append("daemon_owner_stopping")
        org = self.state.orgs.get(slug)
        if org is not None:
            move = getattr(org.db, "_team_move_coordinator", None)
            if (move is None or move.store.db is not org.db or move.store.org_slug != slug
                    or org.orchestrator._team_moves is not move or org.orchestrator._teams is not org.teams
                    or org.orchestrator._host_supervisor is not host):
                reasons.append("team_move_native_owner_unavailable")
            if (org.sessions is None or org.orchestrator._sessions is not org.sessions
                    or move is None or move.producers.sessions is not org.sessions):
                reasons.append("team_move_native_tracker_unavailable")
        elif "team_move_" not in self.state.broken_orgs.get(slug, ""):
            reasons.append("team_move_diagnostic_owner_unavailable")
        recovery_reasons = list(dict.fromkeys(reasons))
        if org is None:
            reasons.append("diagnostic_org_requires_recovery")
        else:
            if org.naming_readiness != "ready":
                reasons.append("team_move_names_unavailable")
            try:
                ready = org.workflow_authority.verify_admission_ready()
                if ready.snapshot_bytes != org.workflow_authority.capture_snapshot():
                    reasons.append("team_move_current_authority_stale")
            except Exception as exc:
                reasons.append("team_move_authority_not_ready")
                # Only a bounded native code, never exception prose/private bytes.
                authority_code = TeamMoveOperation._diagnostic(exc).code
            else:
                authority_code = None
        if row is not None:
            reasons.append("team_move_operation_held")
        with self._observations:
            busy = any(s == slug for s, _ in self.tasks)
        if busy:
            reasons.append("team_move_service_owner_busy")
        reasons = list(dict.fromkeys(reasons))
        return {"version": team_move.CAPABILITY_VERSION, "apply_available": not reasons,
            "recover_available": not recovery_reasons,
            "source_apply_enabled": team_move.APPLY_AVAILABLE, "incomplete_reasons": reasons,
            "recovery_incomplete_reasons": recovery_reasons,
            "authority_unavailable_code": authority_code if org is not None else None,
            "schema_installed": installed, "org_attached": slug in self.state.orgs,
            "active_operation_key": row["operation_key"] if row else None,
            "required_reader": "online-team-move@3", "human_boundary": "shared_bearer_no_human_presence_proof"}

    def capability(self, slug):
        result = self.availability(slug)
        key = result.pop("active_operation_key")
        return dict(result, active_operation=self.show(slug, operation_key=key) if key else None)

    def preview(self, slug, selection):
        org = self.attached(slug)
        selection_ids(org, selection)
        operation = TeamMoveOperation(org, self.state.profile_coordinator)
        preview = operation.preview(drain_threads=selection.drain_threads)
        return preview_view(operation, preview, self.capability(slug))

    def running(self, slug, key):
        # Include finished-but-not-yet-reaped tasks: their callback must publish
        # any redelivery/attachment error before a reader can report success.
        with self._observations:
            return (slug, key) in self.tasks

    def show(self, slug, *, operation_id=None, operation_key=None):
        with self.store(slug) as store:
            row = store.read(operation_id=operation_id, operation_key=operation_key)
        if row is None:
            raise TeamMoveError("team_move_unknown_operation")
        with self._observations:
            key = (slug, row["operation_key"])
            running, error = key in self.tasks, self.errors.get(key)
        return operation_view(row, running=running,
            error=error, current_observation=self.observe(slug, row))

    def observe(self, slug, row):
        """Read native diagnostics without attachment, preview or recovery writes.

        A missing process-local tracker remains missing, so the native census
        reports unknown ownership. Saved identities/diagnostics remain separate.
        """
        restricted = None
        result = {"source": "current_native_read", "state_revision": row["state_revision"],
            "census_available": False, "publication_available": False, "blockers": None,
            "owner_settlement": "not_observed", "unavailable_code": None}
        try:
            org = self.state.orgs.get(slug)
            if org is None:
                from runtime.orchestrator.team_move_recovery import open_recovery_context
                with self._observations:
                    sessions = self.diagnostic_sessions.get(slug)
                restricted, operation = open_recovery_context(root=self.diagnostic_root(slug), slug=slug,
                    settings=self.state.settings, profiles=self.state.profile_coordinator,
                    host_supervisor=self.state.host_supervisor, sessions=sessions,
                    diagnostic_only=True, operation_key=row["operation_key"])
            else:
                operation = TeamMoveOperation(org, self.state.profile_coordinator)
            result = operation.observe_recovery(row)
        except Exception as exc:
            result["unavailable_code"] = TeamMoveOperation._diagnostic(exc).code
        finally:
            if restricted is not None:
                try:
                    restricted.close()
                except Exception as exc:
                    # Keep useful observations and the primary refusal. A close
                    # failure is another diagnostic, never successful absence.
                    result["close_error"] = TeamMoveOperation._diagnostic(exc).code
                    with self._observations:
                        self._unsafe_handles.setdefault(slug, []).append(restricted)
        return result

    def require_enabled(self, slug: str, *, recover: bool = False) -> None:
        readiness = self.availability(slug)
        if not readiness["recover_available" if recover else "apply_available"]:
            error = TeamMoveError("team_move_capability_unavailable")
            error.incomplete_reasons = readiness["recovery_incomplete_reasons" if recover else "incomplete_reasons"]
            raise error

    def hard_blockers(self, operation, preview):
        """Only consider waiting on actual C2 currently launched preparations.

        Matching thread/delivery/host ownership permits bounded waiting, never
        clearance: D's unchanged full census must settle before forward commit.
        Unknown ownership and every other kind remain immediate hard refusals.
        """
        if not preview.request.drain_threads:
            return preview.blockers
        with operation.org.db._lock:
            live = tuple(o for o in operation.move.threads.live.values()
                if o.attempt_open and o.state.attempts[-1].launched
                and o.identity.agent_id in preview.request.affected_agents)
            invocations = {o.inv.id for o in live}
            threads = {o.inv.thread_id for o in live}
            deliveries = set()
            for owner in live:
                row = operation.org.db._conn.execute(
                    "SELECT running_invocation_token FROM thread_reply_delivery_state WHERE thread_id=? AND agent_name=?",
                    (owner.inv.thread_id, owner.identity.agent_id)).fetchone()
                if row is not None and row[0] == owner.inv.invocation_token:
                    deliveries.add((owner.inv.thread_id, owner.identity.agent_id))
        hard = []
        for blocker in preview.blockers:
            kind, fact = blocker["kind"], blocker.get("fact", {})
            waiting = ((kind == "thread_running" and fact.get("invocation_id") in invocations)
                or (kind == "thread_running_delivery" and (fact.get("thread_id"), fact.get("agent_id")) in deliveries)
                or (kind == "thread_open_exchange" and fact.get("thread_id") in threads)
                or (kind == "host_attempt_unsettled" and blocker["owner_id"] in threads))
            if not waiting:
                hard.append(blocker)
        return tuple(hard)

    async def apply(self, slug, command, admission):
        lock = self._mutation_locks.setdefault(slug, asyncio.Lock())
        async with lock:
            def publish(prepared: tuple) -> None:
                row, _result = prepared
                if row is not None:
                    self.start(slug, row, recover=False)
            row, result = await self.native(slug,
                lambda: self._prepare_apply(slug, command, admission), publish=publish)
            if row is None:
                return result
            return await self.native(slug, lambda: self.show(slug, operation_key=command.operation_key))

    def _prepare_apply(self, slug: str, command: Any, admission: Any) -> tuple:
        # Same-key replay is read-only, including when source enablement changes.
        with self.store(slug) as store:
            row = store.read(operation_key=command.operation_key)
            if row is not None:
                if public_request(_decode(MoveRequest, row["request_bytes"])) != command.request:
                    raise TeamMoveError("team_move_changed_body")
                return None, self.show(slug, operation_key=command.operation_key)
        self.require_enabled(slug)
        self._assert_no_runner(slug)
        org = self.attached(slug)
        selection_ids(org, command.request)
        operation = TeamMoveOperation(org, self.state.profile_coordinator)
        prior = operation.store.completed_request(command.request.private_request_digest)
        if prior is not None:
            if public_request(_decode(MoveRequest, prior["request_bytes"])) != command.request:
                raise TeamMoveError("team_move_changed_body")
            result = operation_view(operation.verify_already_satisfied(prior))
            return None, dict(result, already_satisfied=True, requested_operation_key=command.operation_key)
        fresh = operation.preview(drain_threads=command.request.drain_threads)
        if public_request(fresh.request) != command.request:
            raise TeamMoveError("team_move_stale_preview")
        hard = self.hard_blockers(operation, fresh)
        if hard:
            error = TeamMoveError("team_move_agents_busy")
            error.blockers = [blocker_view(blocker) for blocker in hard]
            raise error
        # D owns final census and the only bounded captured-thread drain. No
        # hard work is cancelled; all facts are rechecked before its decision.
        row = operation.reserve(operation_key=command.operation_key,
            request_bytes=_bytes(fresh.request), admission=admission)
        return row, None

    async def recover(self, slug, operation_id, expected_revision):
        lock = self._mutation_locks.setdefault(slug, asyncio.Lock())
        async with lock:
            def publish(row: dict) -> None:
                if not self.running(slug, row["operation_key"]):
                    self.start(slug, row, recover=True)
            await self.native(slug, lambda: self._prepare_recover(slug, operation_id, expected_revision),
                publish=publish)
            return await self.native(slug, lambda: self.show(slug, operation_id=operation_id))

    def _prepare_recover(self, slug: str, operation_id: str, expected_revision: int) -> dict:
        self.require_enabled(slug, recover=True)
        with self.store(slug) as store:
            row = store.read(operation_id=operation_id)
        if row is None:
            raise TeamMoveError("team_move_unknown_operation")
        if row["state_revision"] != expected_revision:
            raise TeamMoveError("team_move_stale_revision")
        return row

    def _assert_no_runner(self, slug: str) -> None:
        with self._observations:
            if any(s == slug for s, _ in self.tasks):
                raise TeamMoveError("team_move_service_owner_busy")

    def start(self, slug, row, *, recover):
        """Loop-only handoff by an already admitted request, even during close.

        Shutdown joins requests before taking its final runner snapshot. No
        await can strand a successful reservation between return and start.
        """
        self._assert_no_runner(slug)
        key = (slug, row["operation_key"])
        task = self._owned_task(slug, lambda: self.execute(slug, row, recover=recover),
            name="team-move:" + row["operation_id"])
        with self._observations:
            self.errors.pop(key, None)
            self.tasks[key] = task
        task.add_done_callback(lambda finished: self.finished(key, finished))

    def finished(self, key, task):
        # Reap every exception; never log bodies, private definitions or tokens.
        error = None
        if not task.cancelled():
            error = task.exception()
        view = error_view(error) if error is not None else None
        with self._observations:
            if view is not None:
                self.errors[key] = view
            elif task.cancelled():
                self.errors.setdefault(key, error_view(TeamMoveError("team_move_owner_interrupted")))
            if self.tasks.get(key) is task:
                self.tasks.pop(key, None)

    async def execute(self, slug, row, *, recover):
        restricted = None
        primary = None
        def opened(pair: tuple) -> None:
            nonlocal restricted
            restricted = pair[0]
            with self._observations:
                self._restricted[asyncio.current_task()] = restricted
        def closed(_result: Any) -> None:
            with self._observations:
                self._restricted.pop(asyncio.current_task())
        async def native(callback: Callable) -> Any:
            return await self.native(slug, callback)
        async def redeliver(operation: TeamMoveOperation) -> None:
            await self._redeliver(slug, operation)
        try:
            # Deadline bounds native awaits (including the <=30s thread drain),
            # never grants TTL takeover, rollback, or a completion-time promise.
            async with asyncio.timeout(45):
                org = self.state.orgs.get(slug)
                if org is None:
                    if row["active_slot"] is None:
                        try:
                            attached = await self.state.add_org(slug)
                            self._hold_attachment(slug, attached)
                            await self._redeliver(slug, TeamMoveOperation(attached, self.state.profile_coordinator))
                        except Exception as exc:
                            error = TeamMoveError("team_move_reattachment_or_redelivery_failed")
                            error.team_move_secondary_codes = (TeamMoveOperation._diagnostic(exc).code,)
                            raise error from exc
                        return
                    from runtime.daemon.sessions import SessionTracker
                    from runtime.orchestrator.team_move_recovery import open_recovery_context
                    root = self.diagnostic_root(slug)
                    with self._observations:
                        sessions = self.diagnostic_sessions.setdefault(slug, SessionTracker())
                    restricted, operation = await self.native(slug, lambda: open_recovery_context(root=root, slug=slug,
                        settings=self.state.settings, profiles=self.state.profile_coordinator,
                        host_supervisor=self.state.host_supervisor, sessions=sessions), publish=opened)
                else:
                    operation = TeamMoveOperation(org, self.state.profile_coordinator)
                try:
                    if recover:
                        result = await operation.recover(operation_key=row["operation_key"], expected_revision=row["state_revision"], native=native, redeliver=redeliver)
                        if restricted is None and row["phase"] in {"waiting", "complete", "refused", "abandoned"} and result["active_slot"] is None:
                            await self._redeliver(slug, operation)
                    else:
                        await operation.advance(operation_key=row["operation_key"], expected_revision=row["state_revision"], native=native, redeliver=redeliver)
                except TeamMoveError as exc:
                    if restricted is None or exc.code != "team_move_recovery_reattach_for_redelivery":
                        raise
                    # D saved/released the receipt before noticing this context
                    # cannot deliver. Never treat that exception as rollback.
                if restricted is not None:
                    result = await native(lambda: operation.store.read(operation_key=row["operation_key"]))
                    closing = restricted
                    # native() joins actual close before propagating cancellation.
                    restricted = None  # One attempt; retain the original failure.
                    await self.native(slug, closing.close, publish=closed)
                    if result["active_slot"] is None:
                        try:
                            attached = await self.state.add_org(slug)
                            self._hold_attachment(slug, attached)
                            await self._redeliver(slug, TeamMoveOperation(attached, self.state.profile_coordinator))
                        except Exception as exc:
                            error = TeamMoveError("team_move_reattachment_or_redelivery_failed")
                            error.team_move_secondary_codes = (TeamMoveOperation._diagnostic(exc).code,)
                            raise error from exc
        except BaseException as exc:
            primary = exc
            def observe_failure(error: BaseException = exc) -> None:
                try:
                    with self.store(slug) as store:
                        saved = store.read(operation_key=row["operation_key"])
                    if saved is not None:
                        error.team_move_observation = self.observe(slug, saved)
                except Exception as observation_error:
                    error.team_move_secondary_codes = (*getattr(error, "team_move_secondary_codes", ()),
                        TeamMoveOperation._diagnostic(observation_error).code)
            try:
                await self.native(slug, observe_failure, propagate_cancel=False)
            except BaseException as observation_error:
                exc.team_move_secondary_codes = (*getattr(exc, "team_move_secondary_codes", ()),
                    TeamMoveOperation._diagnostic(observation_error).code)
            raise
        finally:
            if restricted is not None:
                try:
                    await self.native(slug, restricted.close, publish=closed, propagate_cancel=False)
                except BaseException as exc:
                    if primary is None:
                        raise
                    primary.team_move_secondary_codes = (*getattr(primary, "team_move_secondary_codes", ()),
                        TeamMoveOperation._diagnostic(exc).code)
                    primary.add_note("restricted team move context close failed; keep diagnostic org held")
            if primary is not None:
                # Task.cancelled() hides its exception from the done callback;
                # retain its safe primary/secondary facts before reaping.
                try:
                    view = await self.native(slug, lambda: error_view(primary), propagate_cancel=False)
                except BaseException:
                    primary.add_note("team move error projection failed; original cause retained")
                else:
                    with self._observations:
                        self.errors[(slug, row["operation_key"])] = view

    async def _redeliver(self, slug: str, operation: TeamMoveOperation) -> None:
        """Join the complete claim/loop-put/ack lifetime before cancellation.

        The child shares the original org lease. Shutdown joins these admitted
        owners before its runner snapshot; direct loop cancellation is deferred
        through each native return and publication/ack before it propagates.
        """
        async def deliver() -> None:
            async def native(callback: Callable) -> Any:
                return await self.native(slug, callback, propagate_cancel=False)
            await operation.redeliver(native=native)
            if asyncio.current_task().cancelling():
                raise asyncio.CancelledError

        task = self._owned_task(slug, deliver, name="team-move-redelivery:" + slug)
        cancellation = None
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError as exc:
                cancellation = cancellation or exc
        task.result()  # A genuine native/publication failure stays primary.
        if cancellation is not None:
            raise cancellation

    def assert_no_owner(self, slug=None):
        with self._observations:
            if (any(slug is None or s == slug for s, _ in self.tasks)
                    or any(slug is None or s == slug for s, _, _ in self._owners.values())
                    or any(slug is None or s == slug for s, *_ in self._workers.values())
                    or any(slug is None or s == slug for s in self._unsafe_handles)):
                raise TeamMoveError("team_move_service_owner_busy")

    async def close(self):
        with self._observations:
            self.closing = True
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._close(), name="team-move-close")
        cancellation = None
        while not self._close_task.done():
            try:
                await asyncio.shield(self._close_task)
            except asyncio.CancelledError as exc:
                cancellation = cancellation or exc
        self._close_task.result()
        if cancellation is not None:
            raise cancellation

    async def _close(self) -> None:
        # Requests can still publish a previously reserved runner. Join those
        # handoffs before cancelling/snapshotting the operation runners.
        with self._observations:
            runners = set(self.tasks.values())
            requests = tuple(task for _, task, _ in self._owners.values()
                if task is not None and task not in runners)
        if requests:
            await asyncio.gather(*requests, return_exceptions=True)
        with self._observations:
            tasks = tuple(self.tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self.assert_no_owner()
