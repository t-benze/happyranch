"""One private, read-only pass owned by the actual serving attachment lifetime.

Only the Server adapter signals readiness. Native return with restored settings,
not cancellation or a settled Future alone, releases an org's diagnostic guard.
Nothing here interprets selected-history agreement as process/effect closure.
"""
from __future__ import annotations

import asyncio
import itertools
import json
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from runtime.infrastructure.database import _CleanupReadBudget, _CleanupReadEvidence, _CleanupReadRefused

if TYPE_CHECKING:
    from fastapi import FastAPI
    from runtime.daemon.org_state import OrgState
    from runtime.daemon.state import DaemonState

logger = logging.getLogger("happyranch.daemon")
_GENERATIONS = itertools.count(1)
_VERSION = "cleanup-diagnostic@1"
_PREFIX = "cleanup_diagnostic "
_DRAIN_SECONDS = 3.0


@dataclass(eq=False)
class _Attachment:
    generation: int
    slug: str
    state: DaemonState
    runtime: Any
    runtime_root: Path
    org: OrgState
    org_root: Path
    db: Any
    db_path: Path
    supervisor: Any
    tracker: Any
    revoked: threading.Event = field(default_factory=threading.Event)
    native_finished: threading.Event = field(default_factory=threading.Event)
    future: asyncio.Future | None = None
    retirement: asyncio.Future | None = None
    safe_return: bool = False

    def revoke(self) -> None:
        self.revoked.set()

    def current(self) -> bool:
        # References and captured canonical paths are context, never historical
        # owner/process proof. No supervisor/tracker methods or DB reads here.
        return (
            not self.revoked.is_set()
            and self.state.runtime is self.runtime
            and self.runtime.root == self.runtime_root
            and self.org.slug == self.slug
            and self.state.orgs.get(self.slug) is self.org
            and self.org.root == self.org_root
            and self.org.db is self.db and self.db.db_path == self.db_path
            and self.state.host_supervisor is self.supervisor
            and self.org.sessions is self.tracker
            and self.org._cleanup_diagnostic_attachment is self
        )


@dataclass(frozen=True)
class _NativeResult:
    evidence: _CleanupReadEvidence
    records: tuple[dict[str, Any], ...]


def _late_failure(history: tuple[dict[str, Any], ...], check: Callable[[], None]) -> bool:
    """Require matching prior/final facts, beyond producer_settled alone."""
    if len(history) < 2:
        return False
    final = history[-1]
    attempts = final["attempts"]
    if (not final["failed_cleanup"] or not final["producer_settled"] or not attempts
            or not all(a["settled"] and a["quiescent"] for a in attempts)):
        return False
    stable = ("preparation_id", "session_id", "phase", "boundary", "retry_initialized",
              "ordinal", "schedule", "deferred", "attempts")
    for prior in history[:-1]:
        check()
        if not prior["failed_cleanup"] and all(prior[key] == final[key] for key in stable):
            return True
    return False


def _classify(evidence: _CleanupReadEvidence, token: _Attachment, budget: _CleanupReadBudget) -> tuple[dict, ...]:
    records = []
    for thread in evidence.threads:
        budget.check(token.revoked)
        native, history = thread["native"], thread["history"]
        labels = ["process_attempt_unknown", "effects_unknown", "completion_unrecorded_or_unknown"]
        if _late_failure(history, lambda: budget.check(token.revoked)):
            labels.append("late_producer_failure_after_recorded_attempt_quiescence")
        records.append({
            "owner": {"invocation_id": native["id"], "thread": native["thread_id"],
                      "agent": native["agent_name"], "runtime_sid": native["session_id"],
                      "preparation": history[-1]["preparation_id"]},
            "audit_prefix": thread["audit_ids"], "labels": labels,
            "status": "selected_history_only", "reason": "whole_cleanup_unknown",
        })
    for pause in evidence.pauses:
        budget.check(token.revoked)
        for entry in pause["entries"]:
            budget.check(token.revoked)
            job = entry["owner"] == "job"
            records.append({
                "owner": {"task": entry["task_id"], "root": pause["root_task_id"],
                          "journal_entry": entry["id"], "agent": entry["agent"],
                          "runtime_sid": entry["session_id"]},
                "journal_generation": pause["generation"], "journal_digest": pause["journal_digest"],
                "labels": ["process_attempt_unknown", "effects_unknown", "completion_unrecorded_or_unknown"]
                          + (["evidence_incomplete"] if job else []),
                "status": "incomplete" if job else "selected_history_only",
                "reason": "job_owner_uninterpreted" if job else "whole_cleanup_unknown",
            })
    # No affirmative whole-cleanup fact exists in these readers. In particular
    # no path can emit final_bookkeeping_write_failure or clear a native owner.
    return tuple(records)


def _scan(token: _Attachment, budget: _CleanupReadBudget) -> _NativeResult:
    """Entire acquisition and classification body executes off the event loop."""
    try:
        try:
            # Executor queue delay consumes the ORIGINAL readiness budget.
            budget.check(token.revoked)
        except _CleanupReadRefused as exc:
            return _NativeResult(_CleanupReadEvidence("incomplete", exc.code, True, budget.counters()), ())
        evidence = token.db._read_cleanup_diagnostic_evidence(
            org_slug=token.slug, budget=budget, revoked=token.revoked,
        )
        try:
            records = _classify(evidence, token, budget) if evidence.settings_restored else ()
            budget.check(token.revoked)
        except _CleanupReadRefused as exc:
            evidence = _CleanupReadEvidence("incomplete", exc.code, evidence.settings_restored, budget.counters())
            records = ()
        return _NativeResult(evidence, records)
    finally:
        # This flag alone is deliberately NOT enough to release the guard.
        token.native_finished.set()


class _CleanupDiagnostics:
    def __init__(self, state: DaemonState, app: FastAPI) -> None:
        self.state, self.app = state, app
        self.runtime, self.supervisor = state.runtime, state.host_supervisor
        self.generation = next(_GENERATIONS)
        self.attachments: list[_Attachment] = []
        self.worker: asyncio.Task | None = None
        self.budget: _CleanupReadBudget | None = None
        self.revoked = False
        self.signalled = False
        self.unavailable: list[tuple[OrgState, dict[str, Any]]] = []
        # MUST precede copying or enumeration, even in the over-limit branch.
        count = len(state.orgs)
        self.initial_reason = "initial_contexts"
        if count > 8:
            self.initial_reason = "initial_population_over_limit"
            return
        if not count:
            self.initial_reason = "no_initial_contexts"
            return
        if state.runtime is None or state.host_supervisor is None:
            self.initial_reason = "serving_context_missing"
            return
        for slug, org in tuple(state.orgs.items()):
            try:
                runtime_root = state.runtime.root.resolve()
                org_root, db_path = org.root.resolve(), org.db.db_path.resolve()
                if (slug != org.slug or org.sessions is None
                        or state.runtime.root != runtime_root or org.root != org_root
                        or org.db.db_path != db_path
                        or org_root != runtime_root / "orgs" / slug
                        or db_path != org_root / "happyranch.db"
                        or org._cleanup_diagnostic_attachment is not None):
                    raise ValueError("unsupported attachment")
                token = _Attachment(next(_GENERATIONS), slug, state, state.runtime, runtime_root,
                                    org, org_root, org.db, db_path, state.host_supervisor, org.sessions)
                org._cleanup_diagnostic_attachment = token
                org._cleanup_diagnostic_unavailable = None
                self.attachments.append(token)
            except Exception:
                self.unavailable.append((org, {"org": slug, "status": "context_unsupported",
                                               "reason": "attachment_context_missing"}))

    def signal_ready(self, app: FastAPI) -> None:
        """Schedule once; called only by the real successful Server adapter."""
        if self.signalled or self.revoked or app is not self.app or app.state.daemon is not self.state:
            return
        self.signalled = True
        self.budget = _CleanupReadBudget(deadline=time.monotonic() + 3.0)
        coroutine = self._run()
        try:
            self.worker = asyncio.create_task(coroutine, name="cleanup-diagnostic")
        except Exception:
            coroutine.close()
            # No native submission occurred. Startup remains successful; the
            # pass stays unavailable with no retry or new deadline.
            self.initial_reason = "diagnostic_scheduling_unavailable"
            return
        self.worker.add_done_callback(self._observe_worker)

    @staticmethod
    def _observe_worker(worker: asyncio.Task) -> None:
        # Consume unexpected task failures without exposing raw errors/traceback.
        # Any acquired token remains in its org guard if settlement failed.
        if not worker.cancelled():
            worker.exception()

    def revoke(self) -> None:
        self.revoked = True
        for token in self.attachments:
            token.revoke()

    async def _native_return(self, token: _Attachment) -> _NativeResult | None:
        assert token.future is not None
        while True:
            try:
                return await asyncio.shield(token.future)
            except asyncio.CancelledError:
                # Caller cancellation never cancels the actually returned
                # native Future, including executor-queued work. A cancelled
                # native Future itself is unknown, even if its wrapper settled.
                if token.future.cancelled():
                    return None
            except BaseException:
                return None

    async def _run(self) -> None:
        assert self.budget is not None
        completed: list[tuple[_Attachment, _NativeResult]] = []
        for token in self.attachments:
            if self.revoked or not token.current():
                continue
            if not token.org._acquire_cleanup_diagnostic(token):
                continue
            token.retirement = asyncio.get_running_loop().create_future()
            try:
                # Registration, submission and capture of THAT returned Future
                # are one synchronous event-loop segment before ANY await.
                token.future = asyncio.get_running_loop().run_in_executor(None, _scan, token, self.budget)
            except BaseException:
                # Without a returned Future we cannot prove no callable was
                # admitted. Keep the token/DB guard; never infer safe retirement.
                completed.append((token, _NativeResult(
                    _CleanupReadEvidence("incomplete", "submission_unknown", False, self.budget.counters()), ())))
                token.retirement.set_result(None)
                break
            result = await self._native_return(token)
            if (isinstance(result, _NativeResult) and token.native_finished.is_set()
                    and result.evidence.settings_restored):
                # The native reader positively returned after restoration AND
                # classification completed; settle on the coordinator's loop.
                token.safe_return = True
                token.org._release_cleanup_diagnostic(token)
            if result is None:
                result = _NativeResult(_CleanupReadEvidence(
                    "incomplete", "native_return_unknown", False, self.budget.counters()), ())
            completed.append((token, result))
            token.retirement.set_result(None)
            if not token.safe_return:
                break
        self._publish(completed)

    async def drain(self, org: OrgState | None = None) -> None:
        """Bounded owned join, outside attachment/control locks; no task.cancel."""
        from runtime.daemon.org_state import _OrgBusyError
        deadline = time.monotonic() + _DRAIN_SECONDS
        cancellation = None
        selected = [t for t in self.attachments if org is None or t.org is org]
        # Org removal joins only ITS submitted native/settlement obligation.
        # Before submission, revoke prevents that token ever acquiring the DB;
        # another org's active/unsafe diagnostic must not block this detach.
        join = self.worker if org is None else next((t.retirement for t in selected), None)
        if join is not None:
            while not join.done():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise _OrgBusyError("cleanup_diagnostic_drain_incomplete")
                try:
                    await asyncio.wait_for(asyncio.shield(join), timeout=remaining)
                except asyncio.CancelledError as exc:
                    cancellation = cancellation or exc
                    if join.cancelled():
                        break
                except TimeoutError:
                    raise _OrgBusyError("cleanup_diagnostic_drain_incomplete") from None
                except Exception:
                    break
        # Future/task settlement alone never certifies native restoration.
        for token in selected:
            token.org._assert_cleanup_diagnostics_idle()
        if cancellation is not None:
            raise cancellation

    def _publish(self, completed: list[tuple[_Attachment, _NativeResult]]) -> None:
        if (self.revoked or self.app.state.daemon is not self.state
                or self.state._cleanup_diagnostics is not self
                or self.state.runtime is not self.runtime or self.state.host_supervisor is not self.supervisor):
            return
        try:
            self._log_projection(completed)
        except Exception:
            # Existing logging failures do not fail the service or retry work.
            pass

    def _log_projection(self, completed: list[tuple[_Attachment, _NativeResult]]) -> None:
        current = [(token, result) for token, result in completed if token.current()]
        output_incomplete = False
        encoded = []
        contexts = [record for org, record in self.unavailable if self.state.orgs.get(record["org"]) is org]
        for token, result in current:
            evidence = result.evidence
            contexts.append({"org": token.slug, "generation": token.generation,
                             "status": evidence.status, "reason": evidence.reason})
            for record in result.records:
                payload = {"version": _VERSION, "generation": token.generation,
                           "org": token.slug, "coverage": "selected_history_only",
                           "whole_cleanup_unknown": True, **record}
                line = _PREFIX + json.dumps(payload, ensure_ascii=True, separators=(",", ":"))
                # Never truncate an identity, audit prefix or whole owner record.
                if len(encoded) >= 16 or len(line.encode("utf8")) > 2048:
                    output_incomplete = True
                    continue
                encoded.append(line)
        counters = self.budget.counters() if self.budget is not None else (0, 0, 0, 0, 0)
        incomplete = (self.initial_reason != "initial_contexts" or bool(self.unavailable)
                      or len(current) != len(self.attachments)
                      or any(r.evidence.status != "selected_history_only" for _, r in current))
        summary = {"version": _VERSION, "generation": self.generation, "org": None,
                   "status": "evidence_incomplete" if incomplete else "selected_history_only",
                   "reason": self.initial_reason, "coverage": "selected_history_only",
                   "population_incomplete": incomplete, "history_incomplete": incomplete,
                   "whole_cleanup_unknown": True, "output_incomplete": output_incomplete,
                   "counters": dict(zip(("owners", "rows", "raw_bytes", "statements", "vm_steps"), counters)),
                   "contexts": contexts}
        def encode_summary() -> str:
            return _PREFIX + json.dumps(summary, ensure_ascii=True, separators=(",", ":"))
        summary_line = encode_summary()
        if len(summary_line.encode("utf8")) > 2048:
            # Suppress the entire context projection, never shorten an org ID.
            summary["contexts"] = []
            summary["output_incomplete"] = True
            summary_line = encode_summary()
        while encoded and len(summary_line.encode("utf8")) + sum(len(s.encode("utf8")) for s in encoded) > 32768:
            encoded.pop()
            summary["output_incomplete"] = True
            summary_line = encode_summary()
        # No await between final attachment validation and these bounded logs.
        # Logger framing (timestamps/handlers) is outside the message byte cap.
        logger.info("%s", summary_line)
        for line in encoded:
            logger.info("%s", line)
