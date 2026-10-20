"""Private C1b serving owner. No HTTP/CLI, startup selection or automatic release.

CURRENT guarded facts are not historical process/effect retirement. A separately
previewed/confirmed human risk override is the only supported mutation mode.
Native return, settings restoration AND outer retirement own the DB lifetime;
request cancellation and revoke timeout never discharge it.
"""
from __future__ import annotations

import asyncio
import itertools
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from runtime.daemon.org_state import OrgState, _OrgBusyError
from runtime.daemon.state import DaemonState
from runtime.infrastructure.database import Database
from runtime.runtime import RuntimeDir
from runtime.daemon.sessions import SessionTracker
from runtime.daemon.thread_queue import ThreadJob, ThreadQueue
from runtime.infrastructure.thread_admission import ThreadLifecycle
from runtime.infrastructure.producer_admission import ProducerLifecycle
from runtime.infrastructure.thread_delivery_release import (
    Budget, PreviewRequest, ReleaseCommand, ReleaseReader, ReleaseUnavailable,
    Selection, _serving_operation, canonical, decode, native_read, pair_permission,
)
from runtime.orchestrator.host_supervisor import HostSessionSupervisor
from runtime.orchestrator.producer_move import ProducerMoveAdmission, ProducerPreparation
from runtime.orchestrator.team_move import TeamMoveCoordinator
from runtime.orchestrator.thread_move import ThreadMoveAdmission, ThreadPreparation

_GENERATIONS = itertools.count(1)


@dataclass(eq=False)
class _Attachment:
    service: Any
    slug: str
    org: Any
    db: Any
    root: Any
    db_path: Any
    tracker: SessionTracker
    move: TeamMoveCoordinator
    threads: ThreadMoveAdmission
    producers: ProducerMoveAdmission
    orchestrator: Any
    queue: ThreadQueue
    generation: int = field(default_factory=lambda: next(_GENERATIONS))
    leaf: Any = field(default_factory=threading.Lock)
    intake: bool = True  # loop-owned, closes BEFORE trying the finite leaf
    revoked: bool = False  # only the C leaf arbitrates native revocation

    def current(self) -> bool:
        s, org = self.service, self.org
        return (s.app.state is s.app_state and s.app_state.daemon is s.state
            and s.state._thread_release_service is s
            and s.state.runtime is s.runtime and s.runtime is not None
            and type(s.runtime.root) is type(s.runtime_root)
            and s.runtime.root == s.runtime_root and s.state.orgs is s.org_map
            and s.org_map.get(self.slug) is org and org.slug == self.slug
            and type(org.root) is type(self.root) and org.root == self.root
            and org.db is self.db and type(self.db.db_path) is type(self.db_path)
            and self.db.db_path == self.db_path
            and org.sessions is self.tracker and s.state.host_supervisor is s.supervisor
            and org.orchestrator is self.orchestrator and self.orchestrator._db is self.db
            and org.thread_queue is self.queue
            and self.orchestrator._host_supervisor is s.supervisor
            and self.orchestrator._sessions is self.tracker
            and self.db._team_move_coordinator is self.move and self.move.store.db is self.db
            and self.move.store.org_slug == self.slug and self.move.threads is self.threads
            and self.move.producers is self.producers and self.threads.db is self.db
            and self.producers.db is self.db and self.threads.move is self.move
            and self.producers.move is self.move
            and org._thread_release_attachment is self
            and self.db._thread_release_serving_binding is self)

    def revoke(self) -> None:
        self.intake = False
        # No move/DB/host/tracker lock, no await or spin. Failure is incomplete.
        if not self.leaf.acquire(timeout=.025):
            raise _OrgBusyError("thread_release_revoke_incomplete")
        try:
            self.revoked = True
        finally:
            self.leaf.release()


@dataclass(eq=False)
class _Operation:
    attachment: _Attachment
    kind: str
    raw: str | None
    key: str | None
    selection: Selection | None
    budget: Budget
    generation: int
    future: Any = None
    retirement: asyncio.Task | None = None
    finished: threading.Event = field(default_factory=threading.Event)
    safe_return: bool = False
    unsafe: bool = False
    outer_settled: bool = False
    captured: tuple | None = None
    db_facts: tuple | None = None

    @property
    def move(self):
        return self.attachment.move

    def _owns(self, db) -> bool:
        a = self.attachment
        return (a.db is db and a.generation == self.generation
                and a.service.operations.get(self) is self
                and self in a.org._thread_release_users and a.current())

    def _assert_selection(self, selection) -> None:
        if self.selection != selection or not self._owns(self.attachment.db):
            raise ReleaseUnavailable("serving_owner_unavailable")

    def _retain_native_close_obligation(self) -> None:
        self.unsafe = True

    def _capture(self) -> tuple:
        a, b = self.attachment, self.budget
        if not a.current():
            raise ReleaseUnavailable("attachment_changed")
        with a.service.supervisor._thread_release_observation_guard(self.selection, b) as host:
            with a.tracker._thread_release_observation_guard(
                    a.slug, self.selection.agent_id, self.selection.session_id, b) as tracker:
                return host, tracker

    def _revalidate_native_control_uncommitted(self, selection, *, reader) -> None:
        self._assert_selection(selection)
        facts = self.attachment.service._controls(self, reader)
        if self.db_facts is not None and self.db_facts != facts:
            raise ReleaseUnavailable("native_control_changed")
        self.db_facts = facts

    @contextmanager
    def _final_native_observation_guard(self, selection):
        """SQL/decoding finished. Only the existing COMMIT belongs inside."""
        self._assert_selection(selection)
        a, b = self.attachment, self.budget
        with a.service.supervisor._thread_release_observation_guard(selection, b) as host:
            with a.tracker._thread_release_observation_guard(
                    a.slug, selection.agent_id, selection.session_id, b) as tracker:
                if self.captured != (host, tracker):
                    raise ReleaseUnavailable("native_observation_changed")
                yield

    @contextmanager
    def _commit_leaf(self):
        a, b = self.attachment, self.budget
        if not a.leaf.acquire(timeout=b.lock_timeout()):
            raise ReleaseUnavailable("serving_leaf_busy")
        try:
            b.check()
            yield
        finally:
            a.leaf.release()

    def _assert_current_admission_leaf(self, db, selection) -> None:
        # Constant local checks ONLY. No SQL/reader/owner lock under this leaf.
        if (not self._owns(db) or self.selection != selection
                or self.attachment.revoked or not self.attachment.service.ready):
            raise ReleaseUnavailable("serving_admission_revoked")
        self.budget.check()


@dataclass(frozen=True)
class _NativeResult:
    view: dict | None
    replacement_token: str | None = None  # private; never projected/logged


class _ThreadDeliveryReleaseService:
    """Dormant until the real successful Server adapter signals this app.

    All callers remain private. Future human adapters must independently enforce
    existing require_token/_require_human; this owner makes no auth claim.
    """

    def __init__(self, state, app) -> None:
        self.state, self.app, self.app_state = state, app, app.state
        self.runtime = state.runtime if type(state) is DaemonState else None
        self.supervisor = state.host_supervisor if type(state) is DaemonState else None
        self.org_map = state.orgs if type(state) is DaemonState else {}
        self.runtime_root = self.runtime.root if self.runtime is not None else None
        self.ready = False
        self.intake = True
        self.attachments: dict[str, _Attachment] = {}
        self.operations: dict[_Operation, _Operation] = {}  # actual work only
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="thread-release")
        if (type(self.runtime) is not RuntimeDir or type(self.supervisor) is not HostSessionSupervisor
                or type(self.org_map) is not dict or len(self.org_map) > 64):
            return
        for slug, org in self.org_map.items():
            if type(org) is not OrgState or type(org.db) is not Database:
                continue
            move = getattr(org.db, "_team_move_coordinator", None)
            if (type(slug) is not str or not 0 < len(slug) <= 256
                    or type(org.sessions) is not SessionTracker
                    or type(org.thread_queue) is not ThreadQueue
                    or type(move) is not TeamMoveCoordinator
                    or type(move.threads) is not ThreadMoveAdmission
                    or type(move.producers) is not ProducerMoveAdmission
                    or org.root != self.runtime_root / "orgs" / slug
                    or not org.root.is_absolute() or org.db.db_path != org.root / "happyranch.db"
                    or org._thread_release_attachment is not None
                    or getattr(org.db, "_thread_release_serving_binding", None) is not None):
                continue
            a = _Attachment(self, slug, org, org.db, org.root, org.db.db_path,
                            org.sessions, move, move.threads, move.producers, org.orchestrator, org.thread_queue)
            org._thread_release_attachment = a
            org.db._thread_release_serving_binding = a
            self.attachments[slug] = a

    def signal_ready(self, app) -> None:
        # No operation/read/select/release, no task, timer or retry.
        if (app is self.app and app.state is self.app_state
                and self.app_state.daemon is self.state and self.intake):
            self.ready = True

    def capability(self, org_slug: str) -> dict:
        a = self.attachments.get(org_slug)
        available = bool(self.ready and self.intake and a is not None
                         and a.intake and not a.revoked and a.current())
        return dict(version=1, serving_admission_available=available,
                    proof_backed_available=False, external_adapters_available=False)

    async def preview(self, org_slug: str, raw: str) -> dict:
        return await self._submit(org_slug, "preview", raw=raw)

    async def apply(self, org_slug: str, raw: str) -> dict:
        return await self._submit(org_slug, "apply", raw=raw)

    async def show(self, org_slug: str, operation_key: str) -> dict | None:
        return await self._submit(org_slug, "show", key=operation_key)

    async def _submit(self, org_slug, kind, *, raw=None, key=None):
        # Admission/registration/submission/capture have no cancellable await.
        budget = Budget()
        budget.native(4)  # bounded command/scalar admission work, before decoding
        a = self.attachments.get(org_slug)
        if (not self.ready or not self.intake or a is None or not a.intake
                or a.revoked or not a.current() or len(self.operations) >= 64):
            raise ReleaseUnavailable("serving_owner_unavailable")
        selection = None
        if kind in {"preview", "apply"}:
            if type(raw) is not str or len(raw) > 16384:
                raise ReleaseUnavailable("payload_bound")
            budget.raw_bytes += len(raw.encode("utf8"))
            budget.check()
            request = decode(ReleaseCommand if kind == "apply" else PreviewRequest, raw)
            selection, key = request.selection, request.operation_key
            if selection.org_slug != org_slug:
                raise ReleaseUnavailable("selection")
        else:
            budget.native()
            budget.text(key)
        budget.check()
        op = _Operation(a, kind, raw, key, selection, budget, a.generation)
        if not a.org._acquire_thread_release(op):
            raise ReleaseUnavailable("serving_admission_revoked")
        self.operations[op] = op
        try:
            # Capture EVERY returned future before constructing any async waiter.
            op.future = self.executor.submit(self._native, op)
            retirement = self._settle(op)
            try:
                op.retirement = asyncio.create_task(retirement, name="thread-release-retirement")
            except BaseException:
                retirement.close()
                raise
            op.retirement.add_done_callback(self._observe_retirement)
        except BaseException:
            # Submission/capture uncertainty retains the exact registered guard.
            op.unsafe = True
            raise ReleaseUnavailable("native_submission_uncertain", committed=None) from None
        return await asyncio.shield(op.retirement)

    @staticmethod
    def _observe_retirement(task):
        if not task.cancelled():
            task.exception()  # disconnect does not leave an unobserved result

    @staticmethod
    def _safe(view):
        if view is None:
            return None
        # All fields originate in the strict native preview/show projection;
        # no exception text, raw audit, path, credential or provider data.
        if len(canonical(view)) > 32768:
            raise ReleaseUnavailable("output_bound", committed=view.get("committed"))
        return view

    def _native(self, op):
        a = op.attachment
        token = _serving_operation.set(op)
        try:
            op.budget.check()  # includes executor queue delay
            if op.selection is not None:
                op.captured = op._capture()
            if op.kind == "preview":
                if not a.move.gate.acquire(timeout=op.budget.lock_timeout()):
                    raise ReleaseUnavailable("move_busy")
                try:
                    op.budget.check()
                    return _NativeResult(self._safe(a.db.preview_exact_thread_delivery(op.raw)))
                finally:
                    a.move.gate.release()
            if op.kind == "show":
                result = a.db.show_exact_thread_delivery(org_slug=a.slug, operation_key=op.key)
                if result is not None:
                    # Strict history remains showable when current permission
                    # cannot be established. Show NEVER publishes a token.
                    result["serving_admission_available"] = a.current() and not a.revoked
                    result["incomplete_reasons"] = ["current_native_eligibility_unavailable"]
                    try:
                        op.captured = op._capture()
                        replacement = self._notification_token(op)
                        result["current_native_eligibility"] = replacement is not None
                        result["incomplete_reasons"] = []
                    except Exception:
                        pass
                return _NativeResult(self._safe(result))
            result = a.db.release_exact_thread_delivery(op.raw)
            result["serving_admission_available"] = a.current() and not a.revoked
            result["incomplete_reasons"] = ["current_native_eligibility_unavailable"]
            # Failure here cannot undo commit. Notification readback shares the
            # original allowance; an explicit later show gets a new read budget.
            try:
                replacement = self._notification_token(op)
                result["current_native_eligibility"] = replacement is not None
                result["incomplete_reasons"] = []
            except Exception:
                replacement = None
            return _NativeResult(self._safe(result), replacement)
        except ReleaseUnavailable as exc:
            if not exc.settings_restored:
                op._retain_native_close_obligation()
            raise
        finally:
            op.safe_return = not (op.unsafe or getattr(a.db, "_thread_release_settings_unsafe", False)
                                  or getattr(a.db, "_thread_release_read_active", False))
            _serving_operation.reset(token)
            op.finished.set()

    async def _settle(self, op):
        normal_return = False
        try:
            result = await asyncio.shield(asyncio.wrap_future(op.future))
            view = result.view
            a = op.attachment
            if op.kind == "apply" and view is not None and view.get("committed") is True:
                view["notification"] = "pending_or_skipped"
                if (result.replacement_token is not None and self.intake and a.intake
                        and not a.revoked and a.current()):
                    try:
                        # Existing unbounded queue's put_once has no suspension
                        # between this loop-owned attachment check and enqueue.
                        await a.org.thread_queue.put_once(ThreadJob(a.slug, result.replacement_token))
                        view["notification"] = "published_same_token"
                    except Exception:
                        view["notification"] = "unknown"
            normal_return = True
            return self._safe(view)
        except ReleaseUnavailable:
            normal_return = True  # actual exception + native return still observed
            raise
        except Exception:
            normal_return = True
            raise ReleaseUnavailable("native_operation_unavailable", committed=None) from None
        finally:
            op.outer_settled = normal_return
            if (normal_return and op.future is not None and op.future.done()
                    and op.finished.is_set() and op.safe_return and not op.unsafe):
                op.attachment.org._release_thread_release(op)
                self.operations.pop(op, None)
            # Cancellation/uncertain return/unsafe restoration keeps obligations.

    def revoke(self, org=None) -> None:
        if org is None:
            self.intake = False
        for a in self.attachments.values():
            if org is None or a.org is org:
                a.revoke()

    async def drain(self, org=None) -> None:
        self.revoke(org)
        tasks = [op.retirement for op in self.operations
                 if org is None or op.attachment.org is org]
        if any(task is None for task in tasks):
            raise _OrgBusyError("thread_release_native_submission_uncertain")
        if tasks:
            # wait never cancels service owners, including on drain cancellation.
            _, pending = await asyncio.wait(tasks, timeout=3)
            if pending:
                raise _OrgBusyError("thread_release_native_drain_incomplete")
            for task in tasks:
                if not task.cancelled():
                    task.exception()  # observe outcomes; failure is not clearance
        for a in self.attachments.values():
            if org is None or a.org is org:
                a.org._assert_thread_releases_idle()
        if org is None:
            self.executor.shutdown(wait=False, cancel_futures=False)

    def _controls(self, op, reader):
        """Fixed typed reads/maps on SAME transaction/budget; no broad census."""
        a, s, b = op.attachment, op.selection, op.budget
        b.check()
        tasks = a.move._thread_release_task_preparations_uncommitted(s, b)
        facts = [tasks]
        for mapping, cls, expected_owner in ((a.threads.live, ThreadPreparation, a.threads),
                                            (a.producers.live, ProducerPreparation, a.producers)):
            b.population(mapping)
            records = []
            for key, owner in mapping.items():
                if type(owner) is not cls or owner.admission is not expected_owner:
                    raise ReleaseUnavailable("preparation_unavailable")
                state = owner.state
                if type(state) is not (ThreadLifecycle if cls is ThreadPreparation else ProducerLifecycle):
                    raise ReleaseUnavailable("preparation_unavailable")
                for value in (state.org_slug, state.agent_id, state.session_id, state.preparation_id):
                    b.text(value)
                if (state.org_slug != a.slug or type(owner.owner_live) is not bool
                        or type(owner.attempt_open) is not bool
                        or type(state.failed_cleanup) is not bool or type(state.producer_settled) is not bool):
                    raise ReleaseUnavailable("preparation_unavailable")
                if cls is ThreadPreparation:
                    if type(key) is not int or key != state.invocation_id:
                        raise ReleaseUnavailable("preparation_identity_unavailable")
                else:
                    if type(key) is not tuple or len(key) != 2:
                        raise ReleaseUnavailable("preparation_identity_unavailable")
                    b.text(key[0])
                    b.text(key[1])
                    if key != (state.kind, state.source_id):
                        raise ReleaseUnavailable("preparation_identity_unavailable")
                if state.agent_id == s.agent_id:
                    raise ReleaseUnavailable("preparation_current_owner")
                records.append((key, state.agent_id, state.session_id, state.preparation_id,
                                owner.owner_live, owner.attempt_open, state.failed_cleanup, state.producer_settled))
            facts.append(tuple(records))
        # Any org writer conservatively refuses, without membership discovery.
        if b.population(a.producers.writers):
            raise ReleaseUnavailable("native_writer_current_owner")
        # Org-level held control is deliberately conservative. Do not discover
        # or invent a thread root from TASK_FOLLOWUP/text or missing tracker data.
        moves = reader.rows("team_move_operations", "active_slot=1", (), "operation_id")
        if moves:
            raise ReleaseUnavailable("team_move_held")
        from runtime.infrastructure.task_pause_controls import decode_journal
        pauses = reader.rows("task_pause_controls", "1=1", (), "root_task_id")
        for row in pauses:
            b.native(64)  # bounded decoder work reserved BEFORE native decoding
            if (type(row["schema_version"]) is not int or row["schema_version"] != 1
                    or type(row["held"]) is not int or row["held"] not in (0, 1)
                    or type(row["generation"]) is not int or row["generation"] < 0):
                raise ReleaseUnavailable("pause_control_unavailable")
            journal = decode_journal(row["admission_journal_json"])
            if len(journal["entries"]) > 64:
                raise ReleaseUnavailable("pause_control_unavailable")
            # An org-wide unresolved hold/journal supplies no native closure
            # entitlement. No root lock, journal edit or owner adoption follows.
            if row["held"] or row["release_pending_generation"] is not None or journal["entries"]:
                raise ReleaseUnavailable("pause_control_unsettled")
        native_tasks = reader.rows("tasks", "assigned_agent=? OR current_session_id=?",
                                   (s.agent_id, s.session_id), "id")
        for row in native_tasks:
            if row["current_session_id"] == s.session_id or row["status"] not in {
                    "completed", "failed", "cancelled", "superseded"}:
                raise ReleaseUnavailable("native_task_current_owner")
        b.check()
        return tuple(facts)

    def _notification_token(self, op):
        a, b, s = op.attachment, op.budget, op.selection
        if not a.move.gate.acquire(timeout=b.lock_timeout()):
            raise ReleaseUnavailable("move_busy", committed=True)
        try:
            with native_read(a.db, b) as reader:
                if a.db._conn.in_transaction:
                    raise ReleaseUnavailable("database_owned", committed=True)
                try:
                    reader.execute("BEGIN").close()
                    view = ReleaseReader(reader, a.slug)
                    view.inventory()
                    record = view.records.get(op.key)
                    if record is None:
                        raise ReleaseUnavailable("committed_readback_incomplete", committed=None)
                    _, companion, command = record
                    if companion.replacement_id is None or command.selection != s:
                        return None
                    op._revalidate_native_control_uncommitted(s, reader=reader)
                    if not view.released(s.invocation_id) or not pair_permission(view, s.thread_id, s.agent_id):
                        return None
                    evidence = view.thread(s.thread_id)
                    replacement = next((r for r in evidence.invocations if r["id"] == companion.replacement_id), None)
                    delivery = next((d for d in evidence.delivery if d["agent_name"] == s.agent_id), None)
                    thread = reader.one("threads", "id=?", (s.thread_id,), "id")
                    participant = next((p for p in evidence.participants if p["agent_name"] == s.agent_id), None)
                    if (replacement is None or replacement["status"] != "pending"
                            or replacement["started_at"] is not None or delivery is None
                            or delivery["queued_invocation_token"] != replacement["invocation_token"]
                            or delivery["running_invocation_token"] is not None
                            or thread is None or thread["status"] != "open" or participant is None):
                        return None
                    # Fresh owner guards before returning SAME private token;
                    # DB is a read snapshot, so rollback occurs AFTER guards.
                    with op._final_native_observation_guard(s):
                        with op._commit_leaf():
                            op._assert_current_admission_leaf(a.db, s)
                    return replacement["invocation_token"]
                finally:
                    try:
                        a.db._conn.rollback()
                    except BaseException:
                        a.db._thread_release_settings_unsafe = True
                        op._retain_native_close_obligation()
                        raise ReleaseUnavailable("notification_read_unrestored", committed=True,
                                                 settings_restored=False) from None
        finally:
            a.move.gate.release()
