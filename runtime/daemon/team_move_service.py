"""Daemon-owned bounded execution, including unattached diagnostic orgs.

Tasks have strong lifetime ownership independent of HTTP callers. Shutdown
cancels/reaps them before DB close. Durable D holds and native owner audits,
never task-map absence or a timeout, govern forward recovery.
"""
from __future__ import annotations

import asyncio
import re
from contextlib import contextmanager

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
        if self.closing:
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
        if any(s == slug for s, _ in self.tasks):
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
        return (slug, key) in self.tasks

    def show(self, slug, *, operation_id=None, operation_key=None):
        with self.store(slug) as store:
            row = store.read(operation_id=operation_id, operation_key=operation_key)
        if row is None:
            raise TeamMoveError("team_move_unknown_operation")
        return operation_view(row, running=self.running(slug, row["operation_key"]),
            error=self.errors.get((slug, row["operation_key"])), current_observation=self.observe(slug, row))

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
                restricted, operation = open_recovery_context(root=self.diagnostic_root(slug), slug=slug,
                    settings=self.state.settings, profiles=self.state.profile_coordinator,
                    host_supervisor=self.state.host_supervisor, sessions=self.diagnostic_sessions.get(slug),
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

    def apply(self, slug, command, admission):
        # Same-key replay is read-only, including when source enablement changes.
        with self.store(slug) as store:
            row = store.read(operation_key=command.operation_key)
            if row is not None:
                if public_request(_decode(MoveRequest, row["request_bytes"])) != command.request:
                    raise TeamMoveError("team_move_changed_body")
                return self.show(slug, operation_key=command.operation_key)
        self.require_enabled(slug)
        self.assert_no_owner(slug)
        org = self.attached(slug)
        selection_ids(org, command.request)
        operation = TeamMoveOperation(org, self.state.profile_coordinator)
        prior = operation.store.completed_request(command.request.private_request_digest)
        if prior is not None:
            if public_request(_decode(MoveRequest, prior["request_bytes"])) != command.request:
                raise TeamMoveError("team_move_changed_body")
            result = operation_view(operation.verify_already_satisfied(prior))
            return dict(result, already_satisfied=True, requested_operation_key=command.operation_key)
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
        self.start(slug, row, recover=False)
        return self.show(slug, operation_key=command.operation_key)

    def recover(self, slug, operation_id, expected_revision):
        self.require_enabled(slug, recover=True)
        with self.store(slug) as store:
            row = store.read(operation_id=operation_id)
        if row is None:
            raise TeamMoveError("team_move_unknown_operation")
        if row["state_revision"] != expected_revision:
            raise TeamMoveError("team_move_stale_revision")
        if not self.running(slug, row["operation_key"]):
            self.start(slug, row, recover=True)
        return self.show(slug, operation_id=operation_id)

    def start(self, slug, row, *, recover):
        """Only called after admitted, validated, enabled public operations."""
        if self.closing or any(s == slug for s, _ in self.tasks):
            raise TeamMoveError("team_move_service_owner_busy")
        key = (slug, row["operation_key"])
        self.errors.pop(key, None)
        task = asyncio.create_task(self.execute(slug, row, recover=recover),
            name="team-move:" + row["operation_id"])
        self.tasks[key] = task
        task.add_done_callback(lambda finished: self.finished(key, finished))

    def finished(self, key, task):
        # Reap every exception; never log bodies, private definitions or tokens.
        if not task.cancelled():
            error = task.exception()
            if error is not None:
                self.errors[key] = error_view(error)
        else:
            self.errors.setdefault(key, error_view(TeamMoveError("team_move_owner_interrupted")))
        if self.tasks.get(key) is task:
            self.tasks.pop(key, None)

    async def execute(self, slug, row, *, recover):
        restricted = None
        primary = None
        try:
            # Deadline bounds native awaits (including the <=30s thread drain),
            # never grants TTL takeover, rollback, or a completion-time promise.
            async with asyncio.timeout(45):
                org = self.state.orgs.get(slug)
                if org is None:
                    if row["active_slot"] is None:
                        try:
                            attached = await self.state.add_org(slug)
                            await TeamMoveOperation(attached, self.state.profile_coordinator).redeliver()
                        except Exception as exc:
                            error = TeamMoveError("team_move_reattachment_or_redelivery_failed")
                            error.team_move_secondary_codes = (TeamMoveOperation._diagnostic(exc).code,)
                            raise error from exc
                        return
                    from runtime.daemon.sessions import SessionTracker
                    from runtime.orchestrator.team_move_recovery import open_recovery_context
                    root = self.diagnostic_root(slug)
                    sessions = self.diagnostic_sessions.setdefault(slug, SessionTracker())
                    restricted, operation = open_recovery_context(root=root, slug=slug,
                        settings=self.state.settings, profiles=self.state.profile_coordinator,
                        host_supervisor=self.state.host_supervisor, sessions=sessions)
                else:
                    operation = TeamMoveOperation(org, self.state.profile_coordinator)
                try:
                    if recover:
                        result = await operation.recover(operation_key=row["operation_key"], expected_revision=row["state_revision"])
                        if restricted is None and row["phase"] in {"waiting", "complete", "refused", "abandoned"} and result["active_slot"] is None:
                            await operation.redeliver()
                    else:
                        await operation.advance(operation_key=row["operation_key"], expected_revision=row["state_revision"])
                except TeamMoveError as exc:
                    if restricted is None or exc.code != "team_move_recovery_reattach_for_redelivery":
                        raise
                    # D saved/released the receipt before noticing this context
                    # cannot deliver. Never treat that exception as rollback.
                if restricted is not None:
                    result = operation.store.read(operation_key=row["operation_key"])
                    closing = restricted
                    restricted = None  # One close attempt; do not replace its failure.
                    closing.close()
                    if result["active_slot"] is None:
                        try:
                            attached = await self.state.add_org(slug)
                            await TeamMoveOperation(attached, self.state.profile_coordinator).redeliver()
                        except Exception as exc:
                            error = TeamMoveError("team_move_reattachment_or_redelivery_failed")
                            error.team_move_secondary_codes = (TeamMoveOperation._diagnostic(exc).code,)
                            raise error from exc
        except BaseException as exc:
            primary = exc
            try:
                with self.store(slug) as store:
                    saved = store.read(operation_key=row["operation_key"])
                if saved is not None:
                    exc.team_move_observation = self.observe(slug, saved)
            except Exception as observation_error:
                exc.team_move_secondary_codes = (*getattr(exc, "team_move_secondary_codes", ()),
                    TeamMoveOperation._diagnostic(observation_error).code)
            raise
        finally:
            if restricted is not None:
                try:
                    restricted.close()
                except BaseException as exc:
                    if primary is None:
                        raise
                    primary.team_move_secondary_codes = (*getattr(primary, "team_move_secondary_codes", ()),
                        TeamMoveOperation._diagnostic(exc).code)
                    primary.add_note("restricted team move context close failed; keep diagnostic org held")
            if primary is not None:
                # Task.cancelled() hides its exception from the done callback;
                # retain its safe primary/secondary facts before reaping.
                self.errors[(slug, row["operation_key"])] = error_view(primary)

    def assert_no_owner(self, slug=None):
        if any(slug is None or s == slug for s, _ in self.tasks):
            raise TeamMoveError("team_move_service_owner_busy")

    async def close(self):
        self.closing = True
        tasks = tuple(self.tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
