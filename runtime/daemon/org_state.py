"""Per-org runtime state: DB, queue events, sessions, teams, locks.

One ``OrgState`` per active org under ``<runtime>/orgs/<slug>/``. Constructed
once at daemon startup (via ``DaemonState.from_runtime``) or lazily on
``happyranch orgs init <slug>``. Each instance is fully self-contained — no
cross-references to other orgs.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from runtime.infrastructure.memory_collection import CollectionObserver
    from runtime.daemon.cleanup_diagnostics import _Attachment

from runtime.infrastructure.startup_observation import BootObservation, observed
from runtime.config import Settings
from runtime.daemon.dream_queue import DreamQueue
from runtime.daemon.event_bus import EventBus
from runtime.daemon.schedule_queue import ScheduleQueue
from runtime.daemon.wake_queue import WakeQueue
from runtime.daemon.sessions import SessionTracker
from runtime.daemon.thread_queue import ThreadQueue
from runtime.infrastructure.database import Database
from runtime.infrastructure.thread_store import ThreadStore
from runtime.infrastructure.workflow_schema import (
    draft_migration_guidance, install_or_recover, validate_workflow_schema, submission_migration_guidance,
    author_handoff_migration_guidance,
)
from runtime.workflows.cutover import WorkflowCutoverStore
from runtime.models import BlockKind, TaskStatus
from runtime.orchestrator._paths import OrgPaths
from runtime.orchestrator.dashboard_projection import DashboardProjectionManager
from runtime.orchestrator.orchestrator import Orchestrator
from runtime.orchestrator.org_validation import validate_team_membership
from runtime.orchestrator.teams import TeamsRegistry
from runtime.workflows.authority import WorkflowAuthorityCoordinator
from runtime.workflows.coding_release_import import CodingReleaseImportStore
from runtime.workflows.coding_builtin import CodingBuiltinStore
from runtime.workflows.coding_roles import CodingRoleStore
from runtime.workflows.coding_start import CodingStartStore
from runtime.workflows.activation import WorkflowActivationStore
from runtime.workflows.draft_dispatch import WorkflowDraftDispatcher
from runtime.workflows.submissions import WorkflowSubmissionStore
from runtime.workflows.revisions import WorkflowRevisionStore
from runtime.workflows.review_dispatch import WorkflowReviewDispatcher
from runtime.workflows.review_receipts import WorkflowReviewReceiptStore
from runtime.workflows.repository_artifacts import WorkflowRepositoryArtifactService

logger = logging.getLogger(__name__)


def _build_authority_evaluator():
    """S6a: production semantics come from the authenticated manager result.

    The injectable evaluator seam remains available to strict unit fakes and
    legacy static-policy tests; production never launches a second LLM.
    """
    return None


class _OrgBusyError(RuntimeError):
    """Process-local lifetime refusal, mapped by the existing unload route."""

    def __init__(self, code: str, *, task_ids: list[str] | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.task_ids = task_ids


@dataclass
class OrgState:
    slug: str
    root: Path                        # <runtime>/orgs/<slug>
    db: Database
    teams: TeamsRegistry
    settings: Settings
    orchestrator: Orchestrator
    workflow_authority: WorkflowAuthorityCoordinator = field(init=False)
    workflow_activations: WorkflowActivationStore = field(init=False)
    workflow_coding_release_import: CodingReleaseImportStore = field(init=False)
    workflow_coding_builtin: CodingBuiltinStore = field(init=False)
    workflow_coding_roles: CodingRoleStore = field(init=False)
    workflow_coding_start: CodingStartStore = field(init=False)
    workflow_drafts: WorkflowDraftDispatcher = field(init=False)
    workflow_coding_dispatch: Any = field(init=False)
    workflow_submissions: WorkflowSubmissionStore = field(init=False)
    workflow_revisions: WorkflowRevisionStore = field(init=False)
    workflow_reviews: WorkflowReviewDispatcher = field(init=False)
    workflow_review_receipts: WorkflowReviewReceiptStore = field(init=False)
    workflow_repository_artifacts: WorkflowRepositoryArtifactService = field(init=False)
    memory_collection: CollectionObserver | None = field(init=False, default=None)
    memory_collection_unavailable: str | None = field(init=False, default=None)
    naming_readiness: str = field(init=False, default="unavailable")
    naming_diagnostic: str | None = field(init=False, default="naming_not_initialized")
    naming_install_diagnostic: str | None = field(init=False, default=None)
    sessions: SessionTracker = field(default_factory=SessionTracker)
    db_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    kb_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    teams_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    thread_queue: ThreadQueue = field(default_factory=ThreadQueue)
    dream_queue: DreamQueue = field(default_factory=DreamQueue)
    wake_queue: WakeQueue = field(default_factory=WakeQueue)
    schedule_queue: ScheduleQueue = field(default_factory=ScheduleQueue)
    event_bus: EventBus = field(init=False)
    thread_store: ThreadStore = field(init=False)
    # Dashboard projection: per-org durable last-known-good cache, refreshed
    # every 10s by a coalesced asyncio scheduler. The HTTP route reads ONLY
    # from this projection; it never calls compose_dashboard_summary directly.
    dashboard_projection: DashboardProjectionManager = field(init=False)
    # THR-229 checkpoint C2: owning daemon-process UUID recorded as the
    # ``origin_boot_id`` on every admitted v2 attempt.  It is allocated once per
    # live OrgState (daemon process), never per request and never from an OS
    # boot identifier.  It is an ownership marker, not a credential.
    authority_v2_origin_boot_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    # Event-loop-only: admission and detach/close contain no await. This tracks
    # actual runner callers through native finish, not durable owner clearance.
    _runner_admission_closed: bool = field(init=False, default=False, repr=False)
    _runner_tasks: set[asyncio.Task] = field(init=False, default_factory=set, repr=False)
    # Queue tokens survive consumer cancellation through native retirement.
    _native_task_users: set[object] = field(init=False, default_factory=set, repr=False)

    # Diagnostic acquisition is separate from ordinary runner/task busy state.
    # A revoked attachment is never re-admitted by detach refusal or reload.
    _cleanup_diagnostic_attachment: _Attachment | None = field(init=False, default=None, repr=False)
    _cleanup_diagnostic_unavailable: str | None = field(init=False, default="not_initial_attachment", repr=False)
    _cleanup_diagnostic_users: set[object] = field(init=False, default_factory=set, repr=False)

    # C is independent of diagnostic B and ordinary runner/task ownership.
    _thread_release_attachment: Any = field(init=False, default=None, repr=False)
    _thread_release_users: set[object] = field(init=False, default_factory=set, repr=False)
    # Shared daemon boot owner; these references do not admit or launch work.
    # Admission captures orchestrator/DB/tracker/thread/dream/wake/schedule queues.
    _startup_admission_refs: Any = field(init=False, default=None, repr=False)
    _startup_document_refs: Any = field(init=False, default=None, repr=False)
    _startup_coding_refs: Any = field(init=False, default=None, repr=False)
    _startup_state: Any = field(init=False, default=None, repr=False)
    _startup_org: Any = field(init=False, default=None, repr=False)
    _startup_recovery: Any = field(init=False, default=None, repr=False)
    _startup_reads: Any = field(init=False, default=None, repr=False)
    _startup_read_revoked: bool = field(init=False, default=False, repr=False)
    _startup_read_file: Any = field(init=False, default=None, repr=False)
    _startup_detached: tuple | None = field(init=False, default=None, repr=False)

    def _acquire_thread_release(self, operation) -> bool:
        a = self._thread_release_attachment
        if (self._runner_admission_closed or a is None or operation.attachment is not a
                or not a.intake or a.revoked or not a.current()):
            return False
        self._thread_release_users.add(operation)
        return True

    def _release_thread_release(self, operation) -> None:
        self._thread_release_users.remove(operation)

    def _revoke_thread_releases(self) -> None:
        if self._thread_release_attachment is not None:
            self._thread_release_attachment.revoke()

    def _assert_thread_releases_idle(self) -> None:
        if self._thread_release_users or getattr(self.db, "_thread_release_settings_unsafe", False):
            raise _OrgBusyError("thread_release_native_in_flight_or_unsafe")

    def _acquire_cleanup_diagnostic(self, token: _Attachment) -> bool:
        if (self._runner_admission_closed or self._cleanup_diagnostic_attachment is not token
                or token.revoked.is_set()):
            return False
        self._cleanup_diagnostic_users.add(token)
        return True

    def _release_cleanup_diagnostic(self, token: _Attachment) -> None:
        self._cleanup_diagnostic_users.remove(token)

    def _revoke_cleanup_diagnostics(self) -> None:
        if self._cleanup_diagnostic_attachment is not None:
            self._cleanup_diagnostic_attachment.revoke()
        self._cleanup_diagnostic_unavailable = "attachment_revoked"

    def _assert_cleanup_diagnostics_idle(self) -> None:
        if self._cleanup_diagnostic_users:
            raise _OrgBusyError("cleanup_diagnostic_native_in_flight_or_unsafe")

    @contextmanager
    def _runner_lifetime(self) -> Iterator[bool]:
        if self._runner_admission_closed:
            # A queue consumer may still hold this old OrgState after detach.
            # Refuse before it can reserve an owner or touch the closed DB.
            yield False
            return
        task = asyncio.current_task()
        if task is None:
            raise RuntimeError("org runner requires an asyncio task")
        registered = task not in self._runner_tasks
        self._runner_tasks.add(task)
        try:
            yield True
        finally:
            if registered:
                self._runner_tasks.remove(task)

    def _acquire_native_task(self, owner: object) -> bool:
        # Like runner registration, submission/close is event-loop-only.
        if self._runner_admission_closed:
            return False
        self._native_task_users.add(owner)
        return True

    def _release_native_task(self, owner: object) -> None:
        self._native_task_users.remove(owner)

    def _assert_runners_idle(self, *, thread_releases: bool = True) -> None:
        if thread_releases:
            self._assert_thread_releases_idle()
        if self._native_task_users:
            raise _OrgBusyError("native_tasks_in_flight")
        if self._runner_tasks:
            raise _OrgBusyError("active_runners_in_flight")
        # A failed org-token release may already have removed that token while
        # the SAME original queue still retains the work. Closing is not fresh
        # admission: validate the original binding without requiring READY.
        from runtime.daemon.startup_recovery import StartupRecovery, StartupUnavailable
        try:
            state, gate, org = self._startup_state, self._startup_recovery, self._startup_org
            if state is None:
                if gate is not None or org is not None or self._startup_admission_refs is not None:
                    raise _OrgBusyError("native_tasks_in_flight")
                return  # Explicitly unbound standalone attachment.
            if not isinstance(gate, StartupRecovery) or org is not self or gate.state is not state:
                raise _OrgBusyError("native_tasks_in_flight")
            queue = gate._admission_queue  # Captured before production work.
            gate.require_attachment(self)
            gate.require_attachment(self.orchestrator)
            if queue is not state.queue or queue is not self.orchestrator._queue:
                raise _OrgBusyError("native_tasks_in_flight")
            retained = queue._has_native_work_for(self)
        except (AttributeError, StartupUnavailable) as exc:
            raise _OrgBusyError("native_tasks_in_flight") from exc
        # The registry mutex protects observation only; refusal is outside it.
        # Submission/preflight/close stay synchronous on the original loop.
        if retained:
            raise _OrgBusyError("native_tasks_in_flight")

    _TERMINAL_STATUS_TO_EVENT = {
        TaskStatus.COMPLETED: "task_complete",
        TaskStatus.FAILED: "task_failed",
        # A superseded-resolution is a non-failure terminal, so it replays as a
        # completion-class event; `_synthesize_terminal_event` carries the
        # precise label in `outcome` ("superseded").
        TaskStatus.SUPERSEDED: "task_complete",
        # Path B: cancellation is a non-success terminal, so it replays as a
        # failure-class event — mirroring how SUPERSEDED rides the
        # completion class — with the precise label in `outcome` ("cancelled").
        # Avoids inventing a new EventBus event type. (The founder-facing record
        # is the distinct `log_task_cancelled` audit row; this map only governs
        # terminal-event replay synthesis.)
        TaskStatus.CANCELLED: "task_failed",
    }

    def __post_init__(self) -> None:
        from runtime.identities.registry import prepare_message_addresses, validate_message_addresses
        self.db._thread_address_prepare = lambda body: prepare_message_addresses(self, body)
        self.db._thread_address_validate = lambda addresses: validate_message_addresses(self, addresses)
        try:
            from runtime.infrastructure.memory_collection import CollectionObserver
            self.memory_collection = CollectionObserver(org=self.slug, root=self.root, db=self.db)
            self.memory_collection.context = self
            self.db._memory_collection_context = self
            self.orchestrator.attach_memory_collection(self.memory_collection)
        except Exception:
            self.memory_collection_unavailable = "observer_initialization_failed"
            if self.memory_collection is not None:
                self.memory_collection.unavailable(self.memory_collection_unavailable)
            logger.exception("org %r: memory collection observation unavailable", self.slug)
        self.workflow_authority = WorkflowAuthorityCoordinator(
            db=self.db,
            org_slug=self.slug,
            root=self.root,
            teams=self.teams,
        )
        self.workflow_activations = WorkflowActivationStore(self)
        self.workflow_coding_builtin = CodingBuiltinStore(self)
        self.workflow_coding_release_import = CodingReleaseImportStore(self)
        self.workflow_coding_roles = CodingRoleStore(self)
        self.workflow_coding_start = CodingStartStore(self)
        self.db._workflow_coding_start = self.workflow_coding_start
        self.workflow_drafts = WorkflowDraftDispatcher(self)
        from runtime.workflows.coding_dispatch import CodingDispatcher
        self.workflow_coding_dispatch = CodingDispatcher(self)
        self.workflow_submissions = WorkflowSubmissionStore(self)
        self.workflow_revisions = WorkflowRevisionStore(self)
        self.workflow_reviews = WorkflowReviewDispatcher(self)
        self.workflow_review_receipts = WorkflowReviewReceiptStore(self)
        self._startup_document_refs = (
            self.workflow_drafts, self.workflow_reviews, self.workflow_submissions,
            self.workflow_revisions, self.workflow_review_receipts,
            self.workflow_authority, self.workflow_activations,
        )
        self._startup_coding_refs = (
            self.workflow_coding_start, self.workflow_coding_dispatch, self.workflow_coding_roles,
            self.workflow_coding_release_import, self.workflow_authority, self.workflow_activations,
            self.workflow_coding_start.support, self.workflow_coding_roles.support,
            self.workflow_coding_release_import._support,
        )
        self.workflow_repository_artifacts = WorkflowRepositoryArtifactService()
        if self.orchestrator is not None:
            self.orchestrator._workflow_coding_start = self.workflow_coding_start
            self.orchestrator._workflow_drafts = self.workflow_drafts
            self.orchestrator._workflow_coding_dispatch = self.workflow_coding_dispatch
            self.orchestrator._workflow_submissions = self.workflow_submissions
            self.orchestrator._workflow_reviews = self.workflow_reviews
        self.db._workflow_drafts = self.workflow_drafts
        self.db._workflow_coding_dispatch = self.workflow_coding_dispatch
        self.db._workflow_reviews = self.workflow_reviews
        self.dashboard_projection = DashboardProjectionManager(
            org_slug=self.slug, org_root=self.root,
        )
        def loader(task_id: str) -> list[dict]:
            task = self.db.get_task(task_id)
            if task is not None:
                # Task Activity: gather audit logs for the root task and all
                # descendants (children, grandchildren, etc.), then merge and
                # deduplicate in chronological order (by audit-log id).
                task_ids = [task_id]
                task_ids.extend(self.db.get_descendant_task_ids(task_id))
                all_logs: list[dict] = []
                for tid in task_ids:
                    all_logs.extend(self.db.get_audit_logs(tid))
                # Deduplicate by id, sort chronologically.
                seen: set[int] = set()
                history: list[dict] = []
                for log in sorted(all_logs, key=lambda x: x["id"]):
                    if log["id"] not in seen:
                        seen.add(log["id"])
                        history.append({"type": "audit", **log})
            else:
                # Non-task topic (thread:*, job:*) or unknown task id:
                # preserve existing behavior — try audit logs for the raw
                # topic string (always empty for non-task topics).
                history = [
                    {"type": "audit", **log}
                    for log in self.db.get_audit_logs(task_id)
                ]
            terminal = self._synthesize_terminal_event(task) if task else None
            if terminal is not None:
                history.append(terminal)
            return history
        self.event_bus = EventBus(history_loader=loader)
        self.thread_store = ThreadStore(self.root / "threads")

    def memory_collection_observation(self) -> dict:
        """Read this serving org; observation failure never changes launch state."""
        try:
            from runtime.infrastructure.memory_collection import serving_observation, current_epoch_references, _census_budget
            with _census_budget(captures=2):
                return current_epoch_references(self, serving_observation(self))
        except Exception:
            # Module/constructor acquisition can itself be unavailable. Keep the
            # ordinary audit route usable without importing a second observer.
            from datetime import datetime, timezone
            return {"contract_version": 1, "org": self.slug, "boot_id": None,
                    "installed_identity": None, "generation": None, "assigned_intents": None,
                    "intent_digest": None, "phase_counts": None, "phase_digests": None,
                    "active_preparations": None,
                    "observation_error": self.memory_collection_unavailable or "observation_unavailable",
                    "latest_seal_audit_id": None, "epoch_id": None, "epoch_audit_id": None,
                    "sampled_at": datetime.now(timezone.utc).isoformat(), "data_through": None}

    def bind_authority_v2_owner(self) -> None:
        """Bind the real owning-process identity + permission-surface reader.

        THR-229 checkpoint C3d4b: the v2 claim/publication/housekeeping writers
        read a trusted daemon-process boot identity and a server-owned
        permission-surface digest.  Both bindings previously existed only on the
        test/store seam; production now binds them once per live ``OrgState``,
        BEFORE any startup recovery/publication and before the API admits work.

        This allocates no new identity (``authority_v2_origin_boot_id`` already
        exists) and rewrites no permission generation: the reader is the existing
        strict server-side digest reader, closed over this org's orchestrator.
        """
        from runtime.orchestrator.authority import _strict_permission_surface_digest

        self.db.bind_authority_policy_v2_process_boot_id(
            self.authority_v2_origin_boot_id
        )
        orchestrator = self.orchestrator

        def _reader(agent: str) -> str:
            return _strict_permission_surface_digest(orchestrator, agent)

        self.db.bind_authority_policy_v2_permission_surface_reader(_reader)

    def _synthesize_terminal_event(self, task) -> dict | None:
        if task.status in self._TERMINAL_STATUS_TO_EVENT:
            return {
                "type": self._TERMINAL_STATUS_TO_EVENT[task.status],
                "outcome": task.status.value,
                "synthesized": True,
                "timestamp": self._terminal_event_timestamp(task),
            }
        # Path B: escalated is a top-level non-terminal status. Late
        # subscribers still get the right synthesized event.
        if task.status == TaskStatus.ESCALATED:
            return {
                "type": "task_blocked",
                "outcome": "escalated",
                "synthesized": True,
                "timestamp": self._terminal_event_timestamp(task),
            }
        return None

    @staticmethod
    def _terminal_event_timestamp(task) -> str:
        """Durable persisted timestamp for a synthesized terminal replay event.

        ``completed_at`` is authoritative when the task actually reached a
        terminal state (COMPLETED/FAILED/SUPERSEDED/CANCELLED all write it).
        Legacy rows with a NULL ``completed_at`` fall back to ``updated_at`` —
        the last durable write — mirroring ``list_agent_tasks``'s
        ``COALESCE(completed_at, updated_at, created_at)`` ordering. We never
        manufacture wall-clock time at replay.

        ESCALATED is non-terminal by design: ``try_escalate`` and
        ``try_escalate_over_budget`` write only ``updated_at`` (the transition
        timestamp), never ``completed_at`` — so the fallback naturally yields
        the escalation transition without reinterpreting ``completed_at``.
        """
        ts = task.completed_at or task.updated_at
        return ts.isoformat()

    @classmethod
    def load(cls, *, slug: str, root: Path, settings: Settings,
             observation: BootObservation | None = None,
             attachment_owner: dict | None = None,
             defer_coding_registration: bool = False) -> "OrgState":
        with observed(observation, "org_preflight"):
            paths = OrgPaths(root=root)
            from runtime.orchestrator.team_move_startup import before_org_attachment
            before_org_attachment(root, slug)
            from runtime.identities.schema import preflight
            naming_preflight = preflight(paths.db_path, org_slug=slug)
        # This invocation owns only a Database whose constructor returned.
        # Keep custody here even if forwarding to the original owner fails.
        load_outcome: dict[str, Any] = dict(
            attachment_owner=attachment_owner, db=None, state=None,
            close_attempted=False, close_returned=False, close_error=None,
            close_outcome=None, first=None, occurrences=[])
        db: Database | None = None

        def capture(phase: str, error: BaseException, *, observation: bool = False,
                    retain_history: bool = True) -> dict:
            # Native snapshots precede close, opaque attributes and all sinks.
            record = dict(phase=phase, error=error,
                arguments=BaseException.args.__get__(error),
                traceback=BaseException.__traceback__.__get__(error),
                context=BaseException.__context__.__get__(error),
                cause=BaseException.__cause__.__get__(error),
                suppress_context=BaseException.__suppress_context__.__get__(error),
                observation=observation, delivery=None, previous_occurrence=None, history=None)
            if observation:
                record["delivery"] = (type(error), record["traceback"])
            load_outcome["occurrences"].append(record)
            if load_outcome["first"] is None:
                load_outcome["first"] = record
            for previous in reversed(load_outcome["occurrences"][:-1]):
                if previous["error"] is error:
                    record["previous_occurrence"] = previous
                    break
            if retain_history:
                try:
                    # Prior attachments are history, never current close proof.
                    record["history"] = BaseException.__dict__["__dict__"].__get__(error).copy()
                except BaseException as later:
                    capture("load prior retention", later, retain_history=False)
            return record

        def retain(key: str, value: Any) -> dict | None:
            if attachment_owner is not None:
                try:
                    attachment_owner[key] = value
                except BaseException as later:
                    # One attempt per sink; never recurse into a failed owner.
                    return capture("load owner " + key, later)
            return None

        try:
            with observed(observation, "database"):
                if attachment_owner is not None:
                    attachment_owner["db_construction_started"] = True
                db = Database(paths.db_path)
                load_outcome["db"] = db
                if attachment_owner is not None:
                    attachment_owner["load_db"] = db
            with observed(observation, "workflow_install"):
                install_or_recover(db, expected_org_slug=slug)
            with observed(observation, "workflow_validate"):
                with db._lock:
                    layout = validate_workflow_schema(db._conn, expected_org_slug=slug)
            # Native org release preparation, after strict early move discovery
            # and complete workflow validation, before any roster/publication or
            # producer attachment. Generic Database and diagnostic recovery never
            # install this extension. An existing hold/unknown layout refuses.
            from runtime.infrastructure.team_move_store import install_team_move_schema
            with observed(observation, "move_schema"):
                install_team_move_schema(db, org_slug=slug)
            if layout == "F":
                logger.warning("org %r: %s", slug, draft_migration_guidance(
                    org_slug=slug, runtime_root=str(root.parent.parent),
                ))
            if layout in ("F", "E"):
                logger.warning("org %r: %s", slug, submission_migration_guidance(
                    org_slug=slug, runtime_root=str(root.parent.parent),
                ))
            if layout == "G":
                logger.warning("org %r: %s", slug, author_handoff_migration_guidance())
            with observed(observation, "cutover"):
                WorkflowCutoverStore(db, org_slug=slug).recover_authorized()
            from runtime.infrastructure.task_pause_controls import install_pause_schema, TaskPauseStore
            with observed(observation, "pause_schema"):
                install_pause_schema(db, org_slug=slug)
                db._task_pause_store = TaskPauseStore(db, slug)
            with observed(observation, "workflow_validate"):
                with db._lock:
                    validate_workflow_schema(db._conn, expected_org_slug=slug)
            from runtime.infrastructure.workflow_task_ownership import install_task_ownership
            install_task_ownership(db, org_slug=slug)
            with observed(observation, "roster"):
                teams = TeamsRegistry.load(root)
            # THR-095: one-shot seed — copy the 4 web-writable knobs from
            # config.yaml into the org_settings DB table exactly once per org.
            # Idempotent (sentinel); runs on every daemon startup but is a no-op
            # after the first run.
            try:
                from runtime.orchestrator.org_config import (
                    backfill_reviewer_agents_setting,
                    seed_org_settings_from_config,
                )
                seed_org_settings_from_config(paths, db)
                # THR-175: reviewer_agents is a 5th knob orgs seeded before this
                # change never received.  Backfill is idempotent (row-absent) and
                # never overwrites an explicit setting.
                backfill_reviewer_agents_setting(paths, db)
            except Exception as exc:
                if observation is not None:
                    observation.count("org_seed_nonfatal")
                logger.warning(
                    "org %r: org_settings seed skipped (non-fatal): %s", slug, exc
                )

            # Refuse to attach if agent files and teams.yaml disagree. Raises
            # OrgConsistencyError on drift; DaemonState.from_runtime catches
            # per-org so one broken org cannot crash daemon startup, while
            # add_org propagates so explicit founder actions fail loudly.
            with observed(observation, "roster"):
                validate_team_membership(paths, teams)
            with observed(observation, "orchestrator"):
                orchestrator = Orchestrator(
                    db=db,
                    settings=settings,
                    paths=paths,
                    slug=slug,
                    teams=teams,
                    authority_evaluator=_build_authority_evaluator(),
                )
            from runtime.orchestrator.task_pause import restore_pause_preparations
            with observed(observation, "org_services"):
                restore_pause_preparations(orchestrator)
                state = cls(
                    slug=slug,
                    root=root,
                    db=db,
                    teams=teams,
                    settings=settings,
                    orchestrator=orchestrator,
                )
                load_outcome["state"] = state
            if attachment_owner is not None:
                attachment_owner["org"] = state
            from runtime.identities.registry import read_name_metadata, refresh_names
            # Binding reads current validated org metadata at prompt construction;
            # no cached snapshot, reconciliation or new launch/readiness gate.
            orchestrator._name_metadata_reader = lambda: read_name_metadata(state)
            state.naming_diagnostic = naming_preflight
            # Cold load has no concurrent route coroutine. Capture/install is
            # outside short authority/profile leases, after baseline admission.
            with observed(observation, "identity_publication"):
                with state.workflow_authority.writer_interval(publisher="identity_names_load") as interval:
                    refresh_names(state, install=True, writer=interval.native_disposition)
            # U2A publishes/reconciles the org authority generation before the
            # state is returned. Failure remains fail-closed in the workflow
            # pointer but does not detach the org or alter legacy task/chain
            # behavior; later supported writers and the next cold start retain
            # the same recovery path.
            try:
                with observed(observation, "authority_publication"):
                    state.workflow_authority.recover_or_publish()
            except Exception:
                if observation is not None:
                    observation.count("authority_fenced")
                logger.exception(
                    "org %r: workflow authority startup publication remains fenced",
                    slug,
                )
            # P0, before publication: pin this validated attachment's file.
            # Early requests do no filesystem work on the request loop and
            # cannot adopt a replacement path while queued on their executor.
            read_file = paths.db_path.stat()
            state._startup_read_file = (db, root, paths.db_path, read_file.st_dev, read_file.st_ino)
            # Cold serving defers registration until its original P2 tail.
            # Direct/ready attachment retains the landed post-authority attempt.
            if not defer_coding_registration:
                state.workflow_coding_builtin.ensure_registered()
            return state
        except BaseException as primary:
            # This catch observes actual lower delivery; it is not another
            # inferred throw. Its traceback retains the actual child frames.
            first = capture("load failure delivery", primary, observation=True)
            retain("load_outcome", load_outcome)
            if db is not None:
                # Unpublished construction has no queue/runner admission yet.
                # Diagnostics/owner failures cannot skip this original duty.
                load_outcome["close_attempted"] = True
                retain("load_close_attempted", True)
                try:
                    db.close()
                except BaseException as cleanup:
                    record = capture("load close delivery", cleanup, observation=True)
                    load_outcome["close_error"] = cleanup
                    try:
                        # Only the immediate, actual SAME-call wrapper frame
                        # supplies lower evidence. Reused-error attrs do not.
                        lower = record["traceback"].tb_next
                        if lower is not None and lower.tb_frame.f_code is Database.close.__code__:
                            values = lower.tb_frame.f_locals
                            outcome = values.get("failure")
                            if (values.get("self") is db and type(outcome) is dict
                                    and outcome.get("method") is Database.close.__wrapped__):
                                load_outcome["close_outcome"] = outcome
                    except BaseException as later:
                        capture("load close evidence", later)
                else:
                    # Positive returned close differs from SQLite body return.
                    load_outcome["close_returned"] = True
                    delivery = retain("load_db_closed", True)
                    if delivery is not None:
                        load_outcome["close_error"] = delivery["error"]
                cleanup = load_outcome["close_error"]
                if cleanup is not None:
                    retain("load_close_error", cleanup)
                    try:
                        BaseException.__setattr__(primary, "_org_load_close_error", cleanup)
                    except BaseException as later:
                        capture("load close attachment", later)
                    try:
                        BaseException.add_note(primary, "unpublished org close failed")
                    except BaseException as later:
                        capture("load close note", later)
            try:
                BaseException.__setattr__(primary, "_org_load_outcome", load_outcome)
            except BaseException as later:
                capture("load outcome attachment", later)
            try:
                BaseException.args.__set__(primary, first["arguments"])
                BaseException.__context__.__set__(primary, first["context"])
                BaseException.__cause__.__set__(primary, first["cause"])
                BaseException.__suppress_context__.__set__(primary, first["suppress_context"])
            except BaseException as later:
                capture("load native restoration", later)
            # No virtual traceback setter/with_traceback, truth test or format.
            # Failed attachments retain this SAME owner/DB/record on this frame.
            raise BaseException.with_traceback(primary, first["traceback"])

    def close(self) -> None:
        self._runner_admission_closed = True
        primary = None
        traceback = None
        revokes = [self._revoke_thread_releases, self._revoke_cleanup_diagnostics]
        if self._startup_reads is not None:
            revokes.append(lambda: self._startup_reads.revoke(self))
        for revoke in revokes:
            try:
                revoke()
            except BaseException as exc:
                if primary is None:
                    primary, traceback = exc, exc.__traceback__
                else:
                    primary._org_close_revoke_error = (exc, exc.__traceback__)
        if primary is not None:
            raise primary.with_traceback(traceback)
        # Direct close cannot cancel/join readers or profiles. Refuse before
        # touching the connection unless every actual original owner is idle.
        if self._startup_state is not None:
            self._startup_state._assert_startup_idle(self)
            self._startup_state._assert_attachment_readers_idle((self,))
        if self._startup_recovery is not None:
            self._startup_recovery.assert_native_idle()
        if self._startup_reads is not None:
            self._startup_reads.assert_idle(self)
        self._assert_thread_releases_idle()
        self._assert_cleanup_diagnostics_idle()
        self._assert_runners_idle()
        self.db.close()
