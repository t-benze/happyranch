from __future__ import annotations

import json
import sqlite3
import sys
import time
import uuid
from collections import OrderedDict
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator, TypeVar

from runtime.infrastructure.db._shared import (
    _late_database_now as _now,
    _synchronized,
)
from runtime.models import (
    AUTHORITY_POLICY_V2_ATTEMPT_FINALIZATION_OWNER_LOST,
    AUTHORITY_POLICY_V2_ATTEMPT_FINALIZATION_REFUSED,
    AUTHORITY_POLICY_V2_ATTEMPT_STAGE_ADMITTED,
    AUTHORITY_POLICY_V2_ATTEMPT_STAGE_CLAIMED,
    AUTHORITY_POLICY_V2_ATTEMPT_STAGE_CLAIM_AUDITED,
    AUTHORITY_POLICY_V2_ATTEMPT_STAGE_CONSUMED,
    AUTHORITY_POLICY_V2_ATTEMPT_STAGE_CONSUMED_AUDITED,
    AUTHORITY_POLICY_V2_ATTEMPT_STAGE_EVALUATED,
    AUTHORITY_POLICY_V2_ATTEMPT_STAGE_EVALUATION_AUDITED,
    AUTHORITY_POLICY_V2_ATTEMPT_STAGE_TRANSITIONS,
    AUTHORITY_POLICY_V2_CANDIDATE_AUDIT_EVENT_CLAIMED,
    AUTHORITY_POLICY_V2_CANDIDATE_AUDIT_EVENT_CONSUMED,
    AUTHORITY_POLICY_V2_CANDIDATE_AUDIT_EVENT_EVALUATED,
    AUTHORITY_POLICY_V2_CANDIDATE_AUDIT_EVENT_REFUSED,
    AUTHORITY_POLICY_V2_CANDIDATE_LIFECYCLE_CONSUMED,
    AUTHORITY_POLICY_V2_CANDIDATE_LIFECYCLE_CREATED,
    AUTHORITY_POLICY_V2_CANDIDATE_LIFECYCLE_EVALUATED,
    AUTHORITY_POLICY_V2_CANDIDATE_LIFECYCLE_TRANSITIONS,
    AUTHORITY_POLICY_V2_ESCALATION_DECISION_ACTION,
    AUTHORITY_POLICY_V2_HOUSEKEEPING_OBLIGATION_ACTION,
    AUTHORITY_POLICY_V2_HOUSEKEEPING_REFUSAL_CODES,
    AUTHORITY_POLICY_V2_REFUSAL_TASK_FAILED_ACTION,
    AUTHORITY_POLICY_V2_RESULT_STAGE_ACTION,
    AUTHORITY_POLICY_V2_RESULT_STAGE_REFUSED,
    AuthorityPolicyV2Attempt,
    AuthorityPolicyV2Candidate,
    AuthorityPolicyV2CandidateAudit,
    AuthorityPolicyV2Evaluation,
    AuthorityPolicyV2HousekeepingOutcome,
    AuthorityPolicyV2HousekeepingTarget,
    AuthorityPolicyV2Pin,
    AuthorityPolicyV2SessionBinding,
    AuthorityPolicyV2StageOutcome,
    TaskStatus,
    authority_policy_v2_attempt_id,
    authority_policy_v2_candidate_claim_preimage,
    authority_policy_v2_canonical_json_bytes,
    authority_policy_v2_causal_result_digest,
    authority_policy_v2_sha256,
)


# Closed translation between the precise pre-final stage vocabulary and the
# terminal refusal-housekeeping vocabulary.  Both the stage writer (which must
# persist the obligation before dropping its live owner) and the hook consumer
# use this one table.
_AUTHORITY_POLICY_V2_STAGE_REFUSAL_TO_HOUSEKEEPING = {
    "owner_lost": "owner_lost",
    "cancelled": "cancelled",
    "claim_failed": "claim_failed",
    "claim_audit_missing": "claim_audit_missing",
    "evaluation_failed": "evaluation_failed",
    "evaluation_audit_missing": "evaluation_audit_missing",
    "evaluation_missing": "evaluation_audit_missing",
    "consume_failed": "consume_failed",
    "consume_audit_missing": "consume_audit_missing",
    "final_commit_failed": "final_commit_failed",
    "identity_mismatch": "identity_mismatch",
    "decision_dispatch_interrupted": "decision_dispatch_interrupted",
    "transaction_owned": "identity_mismatch",
    "evidence_drift": "identity_mismatch",
    "schema_drift": "identity_mismatch",
    "already_claimed": "interrupted_pre_final",
    "already_audited": "interrupted_pre_final",
    "already_evaluated": "interrupted_pre_final",
    "already_consumed": "interrupted_pre_final",
}


class _V2StartupConnectionUnsafe(RuntimeError):
    """Private startup abort; causes are retained, never formatted into logs."""

    def __init__(self, reason: str, *causes: BaseException) -> None:
        super().__init__(reason)
        self.causes = causes


class _V2StartupEvidenceUnavailable(ValueError):
    """Strict startup read failed before any general-reader conversion."""


@dataclass(frozen=True)
class _V2StartupAnchor:
    # Only immutable scalar values leave the owned read. This is exclusion
    # evidence, never an owner token or permission to invoke a terminal writer.
    identity: tuple[str | int | None, ...]


@dataclass
class _V2StartupRefusalPhases:
    begin_attempted: bool = False
    began: bool = False
    commit_attempted: bool = False
    committed: bool = False
    rolled_back: bool = False
    cleanup_errors: list[BaseException] = field(default_factory=list)

    def rollback(self, conn: Any) -> None:
        try:
            conn.rollback()
        except BaseException as exc:
            self.cleanup_errors.append(exc)
            raise
        self.rolled_back = True

    def commit(self, conn: Any) -> None:
        self.commit_attempted = True
        conn.commit()
        self.committed = True


_V2_STARTUP_ROOT_CAP = 8
_V2_STARTUP_ROW_CAP = 4096
_V2_STARTUP_BYTE_CAP = 8 * 1024 * 1024
_StartupResult = TypeVar("_StartupResult")
# Payload containers are private and read-only after their one complete charge.
_V2StartupPayload = tuple[str, tuple, list[dict]]
_V2StartupEntry = tuple[_V2StartupPayload, int]


def _v2_startup_retained_size(value: Any, *, opaque: set[int] | None = None) -> int:
    """Size a new graph, or metadata stopping ONLY at fully charged payloads."""
    seen: set[int] = set()
    pending = [value]
    size = 0
    while pending:
        item = pending.pop()
        if id(item) in seen or (opaque is not None and id(item) in opaque):
            continue
        seen.add(id(item))
        kind = type(item)
        if kind not in (dict, OrderedDict, tuple, list, str, bytes, int, float, bool, type(None)):
            raise ValueError("unsupported startup retained object")
        size += sys.getsizeof(item)
        if kind in (dict, OrderedDict):
            for key, child in item.items():
                pending.extend((key, child))
        elif kind in (tuple, list):
            pending.extend(item)
    return size


class _V2StartupReads:
    """Exact Database/connection/invocation owner of private full-root history.

    Only obligation and admitted-stage validators consume the decoded graph,
    read-only inside locked views. Targets/anchors contain scalars; public
    readers, shipping parsers and writers never receive these containers.
    """

    def __init__(self, db: Any) -> None:
        self.db = db
        # Fixed scalar event counters participate in the existing retention
        # accounting. No scan, payload serialization or telemetry root set.
        self._statistics = dict.fromkeys((
            "history_hits", "history_misses", "history_fetches", "history_rows",
            "history_payload_characters", "history_payload_size_unavailable",
            "history_decode_attempts", "history_decodes",
            "admissions", "retention_rejections", "invalidations", "uncached_reads",
            "epoch_changes", "moving_read_fallbacks", "discovery_calls", "discovery_ns",
            "exact_calls", "exact_ns", "failed_reads",
        ), 0)
        # Values are (payload, charge); payload includes the actual root key,
        # complete raw scalar row tuples and private decoded history.
        # Accepted entries move to pending during a speculative closure; they
        # become reusable across reads only after matching outside bookends.
        self._memo: OrderedDict[str, _V2StartupEntry] = OrderedDict()
        self._pending: OrderedDict[str, _V2StartupEntry] = OrderedDict()
        self._epoch: tuple | None = None
        self._groups: tuple[tuple[str, int, int], ...] = ()
        self._position = -1
        self._window_end: int | None = 0
        self._closed = False
        self.unsafe: _V2StartupConnectionUnsafe | None = None
        with db._lock:
            try:
                self.conn = db._conn
                self.caller_owned = self.conn.in_transaction
                if type(self.caller_owned) is not bool:
                    raise ValueError("unknown transaction ownership")
            except BaseException as exc:
                self._abort("connection_entry", exc)
            self.check()

    def __enter__(self) -> _V2StartupReads:
        self.check()
        return self

    def __exit__(self, *_exc: Any) -> None:
        with self.db._lock:
            self.invalidate()
            self._closed = True

    def invalidate(self) -> None:
        """Drop accepted AND speculative references before effects or escape."""
        with self.db._lock:
            self._statistics["invalidations"] += 1
            self._memo.clear()
            self._pending.clear()
            self._epoch = None
            self._groups = ()
            self._position = -1
            self._window_end = 0

    def statistics(self) -> dict[str, int | None]:
        """Actual performed work; characters are not claimed as UTF8 bytes."""
        with self.db._lock:
            return {**self._statistics, "history_payload_bytes": None}

    def _retention_fits(
        self, pending: OrderedDict[str, _V2StartupEntry] | None = None,
        *, groups: tuple | None = None, position: int | None = None,
        window_end: int | None = None,
    ) -> bool:
        """Conservative M + sum(C), without revisiting any old payload graph."""
        # Materialize the ACTUAL field dictionary before measuring its storage.
        fields = self.__dict__
        if self.unsafe is not None:
            return False  # Never size, replace or sanitize sticky safety causes.
        pending = self._pending if pending is None else pending
        if len(pending) + len(self._memo) > _V2_STARTUP_ROOT_CAP:
            return False
        opaque = set()
        charge = 0
        for entries in (pending, self._memo):
            for payload, entry_charge in entries.values():
                opaque.add(id(payload))
                charge += entry_charge
        children = []
        for key, value in fields.items():
            children.append(key)
            # Borrowed owners are excluded only at these explicit field edges;
            # their field names/reference slots remain in the measured dict.
            if key not in ("db", "conn"):
                if key == "_pending":
                    value = pending
                elif key == "_groups" and groups is not None:
                    value = groups
                elif key == "_position" and position is not None:
                    value = position
                elif key == "_window_end" and window_end is not None:
                    value = window_end
                children.append(value)
        metadata = (
            sys.getsizeof(self) + sys.getsizeof(fields)
            + _v2_startup_retained_size(tuple(children), opaque=opaque)
        )
        return metadata + charge <= _V2_STARTUP_BYTE_CAP

    def _reconcile_retention(self) -> None:
        # Catch accounting only. JSON, projection, authentication and view
        # cleanup failures must keep their original ordering/escape boundary.
        try:
            if not self._retention_fits():
                self.invalidate()
        except Exception:
            self.invalidate()

    def _protected(self, root: str) -> bool:
        return any(
            key == root and first <= self._position <= last
            for key, first, last in self._groups
        )

    def _admit(self, root: str, entry: _V2StartupEntry) -> None:
        # One-call trial locals never survive to the next projection. No edit
        # to the original map, including its allocation/order, until acceptance.
        trial = OrderedDict(self._pending)
        trial[root] = entry
        while True:
            if self._retention_fits(trial):
                self._pending = trial
                self._statistics["admissions"] += 1
                return
            victim = next(
                (key for key in trial if key != root and not self._protected(key)),
                None,
            )
            if victim is None:
                self._statistics["retention_rejections"] += 1
                return
            del trial[victim]

    def _discovery_position(self, rows: list, position: int) -> None:
        """Bounded raw-key planning only; never prefetch fallible evidence."""
        if self._epoch is None or self._window_end is None:
            return
        try:
            if position >= self._window_end:
                end = min(len(rows), position + 2 * _V2_STARTUP_ROOT_CAP)
                groups = []
                for first in range(position, end):
                    root = rows[first]["root_task_id"]
                    if type(root) is not str:
                        continue
                    # Bounded pairwise scans: no slices, per-root position
                    # lists, future projections or unbounded grouping index.
                    if any(
                        type(rows[prior]["root_task_id"]) is str
                        and rows[prior]["root_task_id"] == root
                        for prior in range(position, first)
                    ):
                        continue
                    last = first
                    for later in range(first + 1, end):
                        key = rows[later]["root_task_id"]
                        if type(key) is str and key == root:
                            last = later
                    if last != first:
                        groups.append((root, first, last))
                plan = tuple(groups)  # <=8 repeated roots in <=16 rows.
                # Trial field VALUES, actual session-dict allocation. A
                # nonfitting plan changes no entry or map allocation/order.
                if not self._retention_fits(
                    groups=plan, position=position, window_end=end,
                ):
                    # Ordinary LRU for the rest of this discovery. None is
                    # already charged at unsafe; replacing the uniquely owned
                    # group tuple with () cannot grow storage. Keep the charged
                    # position object. No entry is evicted merely for planning.
                    self._groups = ()
                    self._window_end = None
                    return
                self._groups = plan
                self._window_end = end
            else:
                self._groups = tuple(
                    group for group in self._groups if group[2] >= position
                )
            self._position = position
            self._reconcile_retention()
        except Exception:
            self.invalidate()

    def _clear_groups(self) -> None:
        self._groups = ()
        self._position = -1
        self._window_end = 0
        self._reconcile_retention()

    def _abort(self, reason: str, *causes: BaseException) -> None:
        self.invalidate()
        if self.unsafe is None:
            self.unsafe = _V2StartupConnectionUnsafe(reason, *causes)
        else:
            self.unsafe.causes = (
                *self.unsafe.causes, *(cause for cause in causes if cause is not self.unsafe),
            )
        raise self.unsafe

    def check(self) -> None:
        """Observe ownership/usability; never repair or settle a transaction."""
        if self.unsafe is not None:
            raise self.unsafe
        with self.db._lock:
            try:
                if (
                    self._closed
                    or self.db._conn is not self.conn
                    or self.conn.in_transaction is not self.caller_owned
                    or self.conn.execute("SELECT 1").fetchone()[0] != 1
                ):
                    raise ValueError("startup connection ownership changed")
            except BaseException as exc:
                self._abort("connection_exit", exc)

    def require_sweep_entry(self) -> None:
        self.check()
        if self.caller_owned:
            self._abort("caller_transaction")

    @contextmanager
    def _view(self) -> Iterator[None]:
        with self.db._lock:
            self.check()
            body_error = None
            try:
                with self.db.coherent_read_view() as conn:
                    try:
                        if conn is not self.conn:
                            self._abort("read_connection_changed")
                        # BEGIN alone does not pin a snapshot. Even an exact
                        # miss/cache hit must first read a real main table.
                        conn.execute(
                            "SELECT root_task_id FROM main.authority_policy_v2_attempts LIMIT 1",
                        ).fetchone()
                        yield
                    except BaseException as exc:
                        # Exit the existing view normally so its cleanup error
                        # cannot replace the retained body error unnoticed.
                        body_error = exc
                        if isinstance(exc, _V2StartupConnectionUnsafe):
                            self.unsafe = exc
            except BaseException as cleanup_error:
                causes = (() if body_error is None else (body_error,))
                self._abort("read_cleanup", *causes, cleanup_error)
            try:
                self.check()
            except _V2StartupConnectionUnsafe as exc:
                if body_error is not None and body_error is not exc:
                    exc.causes = (body_error, *exc.causes)
                raise
            if body_error is not None:
                if isinstance(body_error, Exception):
                    raise _V2StartupEvidenceUnavailable("startup_evidence_unavailable") from body_error
                raise body_error.with_traceback(body_error.__traceback__)

    def _bookend(self) -> tuple | None:
        """Outside autocommit observations only; metadata failure disables reuse."""
        self.check()
        if self.caller_owned:
            return None
        try:
            # The supported Database uses default DEFERRED isolation, no
            # attachments, and ordinary isolated reads. Drift bypasses sharing.
            databases = tuple(tuple(row) for row in self.conn.execute("PRAGMA database_list"))
            if (
                self.conn.isolation_level != ""
                or self.conn.autocommit != sqlite3.LEGACY_TRANSACTION_CONTROL
                or len(databases) != 1 or databases[0][1] != "main"
                or self.conn.execute("PRAGMA read_uncommitted").fetchone()[0] != 0
            ):
                return None
            data_version = self.conn.execute("PRAGMA main.data_version").fetchone()[0]
            changes = self.conn.total_changes
            schema_version = self.conn.execute("PRAGMA main.schema_version").fetchone()[0]
            if any(type(value) is not int for value in (data_version, changes, schema_version)):
                return None
            return (id(self.conn), data_version, changes, schema_version, databases,
                    self.conn.isolation_level, self.conn.autocommit)
        except Exception:
            # This is not affirmative connection health. The caller checks
            # ownership/usability again before any uncached fallback.
            return None

    def _read(self, operation: Callable[[], _StartupResult]) -> _StartupResult:
        with self.db._lock:
            try:
                opening = self._bookend()
                if opening is None:
                    self._statistics["uncached_reads"] += 1
                    self.invalidate()
                    with self._view():
                        return operation()
                if opening != self._epoch:
                    self._statistics["epoch_changes"] += 1
                    self.invalidate()
                    self._epoch = opening
                # Keep the already-accounted equal epoch object on hits.
                self._reconcile_retention()
                # Swapping the SAME two map references preserves field/dict
                # allocation, metadata reachability and every wrapper/charge.
                self._pending, self._memo = self._memo, self._pending
                with self._view():
                    result = operation()
                closing = self._bookend()
                if closing == opening:
                    self._memo, self._pending = self._pending, self._memo
                    return result
                # Never release a speculative target, anchor, absence or hit.
                del result
                self._statistics["moving_read_fallbacks"] += 1
                self.invalidate()
                self.check()
                # Exactly ONE uncached coherent fallback, after affirmative
                # clean owned exit. Unsafe view cleanup always escapes above.
                with self._view():
                    return operation()
            except BaseException:
                self._statistics["failed_reads"] += 1
                self.invalidate()
                raise

    def _history(self, root: str) -> list[dict]:
        # Only called by _project inside _read's locked coherent closure.
        if root in self._pending:
            self._statistics["history_hits"] += 1
            history = self._pending[root][0][2]
            try:
                self._pending.move_to_end(root)
                self._reconcile_retention()
            except Exception:
                self.invalidate()
            return history
        self._statistics["history_misses"] += 1
        rows = self.conn.execute(
            "SELECT * FROM audit_log WHERE task_id = ? ORDER BY id", (root,),
        ).fetchall()
        self._statistics["history_fetches"] += 1
        self._statistics["history_rows"] += len(rows)
        # Match get_audit_logs exactly, including truthy-payload decoding,
        # JSON scalar types, Unicode, empty payloads and malformed siblings.
        history = []
        for row in rows:
            item = dict(row)
            if item.get("payload"):
                if isinstance(item["payload"], str):
                    self._statistics["history_payload_characters"] += len(item["payload"])
                else:
                    self._statistics["history_payload_size_unavailable"] += 1
                self._statistics["history_decode_attempts"] += 1
                item["payload"] = json.loads(item["payload"])
                self._statistics["history_decodes"] += 1
            history.append(item)
        if self._epoch is not None and rows and len(rows) <= _V2_STARTUP_ROW_CAP:
            try:
                raw = tuple(tuple(row) for row in rows)
                payload = (root, raw, history)
                charge = _v2_startup_retained_size(payload)
                self._admit(root, (payload, charge))
            except Exception:
                # Unsupported/failed accounting never changes full evaluation.
                self.invalidate()
        return history

    def _project(
        self, row: Any,
    ) -> tuple[AuthorityPolicyV2HousekeepingTarget, _V2StartupAnchor | None]:
        # Full strict history precedes every legacy absence conversion. These
        # are the ONLY decoded aliases: both validators read without mutation,
        # and return scalars/frozen projections, never rows/payload containers.
        history = self._history(row["root_task_id"])
        target = self.db._v2_housekeeping_target_from_row(
            row, obligation_code=self.db._authenticate_v2_obligation_uncommitted(
                dict(row), _history=history,
            ),
        )
        anchor = self.db._v2_startup_anchor_uncommitted(row, history)
        return target, anchor

    def discover(
        self,
    ) -> list[tuple[AuthorityPolicyV2HousekeepingTarget, _V2StartupAnchor | None]]:
        def project() -> list[tuple[AuthorityPolicyV2HousekeepingTarget, _V2StartupAnchor | None]]:
            rows = self.conn.execute(
                """SELECT * FROM authority_policy_v2_attempts
                   WHERE finalization_state='unfinalized'
                   ORDER BY created_at, attempt_id""",
            ).fetchall()
            result = []
            try:
                self._discovery_position(rows, 0)
                for row in rows:
                    result.append(self._project(row))
                    # Release a group's protection immediately after last use,
                    # while its fitting history may remain ordinary LRU. The
                    # only across-projection index is the charged field itself.
                    self._discovery_position(rows, self._position + 1)
            except BaseException:
                # Release immediately without doing new accounting while an
                # evidence failure/interruption is already escaping.
                self.invalidate()
                raise
            self._clear_groups()
            return result
        started = time.monotonic_ns()
        self._statistics["discovery_calls"] += 1
        try:
            return self._read(project)
        finally:
            self._statistics["discovery_ns"] += time.monotonic_ns() - started

    def exact(
        self, target: AuthorityPolicyV2HousekeepingTarget,
    ) -> tuple[AuthorityPolicyV2HousekeepingTarget | None, _V2StartupAnchor | None]:
        def project() -> tuple[AuthorityPolicyV2HousekeepingTarget | None, _V2StartupAnchor | None]:
            row = self.conn.execute(
                """SELECT * FROM authority_policy_v2_attempts
                   WHERE root_task_id=? AND manager_agent=?
                     AND manager_session_id=? AND result_id=?""",
                (target.root_task_id, target.manager_agent,
                 target.manager_session_id, target.result_id),
            ).fetchone()
            return (None, None) if row is None else self._project(row)
        started = time.monotonic_ns()
        self._statistics["exact_calls"] += 1
        try:
            return self._read(project)
        finally:
            self._statistics["exact_ns"] += time.monotonic_ns() - started

    def refuse(self, **identity: Any) -> AuthorityPolicyV2HousekeepingOutcome:
        with self.db._lock:
            self.invalidate()
            self.check()
            phases = _V2StartupRefusalPhases()
            error = None
            try:
                outcome = self.db.finalize_authority_policy_v2_attempt_refusal(
                    **identity, _startup_phases=phases,
                )
            except BaseException as exc:
                error = exc
            # These are facts from the actual writer, including EVERY early
            # rollback. A later successful cleanup never erases an earlier one.
            if (
                phases.cleanup_errors
                or (phases.commit_attempted and not phases.committed)
                or (phases.begin_attempted and not (phases.committed or phases.rolled_back))
            ):
                causes = (() if error is None else (error,))
                self._abort("refusal_transaction", *causes, *phases.cleanup_errors)
            try:
                self.check()
            except _V2StartupConnectionUnsafe as exc:
                if error is not None:
                    exc.causes = (error, *exc.causes)
                raise
            if error is not None:
                raise error.with_traceback(error.__traceback__)
            return outcome


class AuthorityV2AttemptsMixin:

    def _authority_policy_v2_startup_reads(self) -> _V2StartupReads:
        return _V2StartupReads(self)

    def _v2_startup_anchor_uncommitted(
        self, row: Any, history: list[dict],
    ) -> _V2StartupAnchor | None:
        """Authenticate one complete immutable tuple, for exclusion ONLY.

        Absent admission/binding evidence yields no anchor, not a new normal
        eligibility restriction. The terminal writer still authenticates itself.
        """
        attempt = self._authority_policy_v2_attempt_from_row(row)
        if not self._authenticate_v2_result_stage_audit_uncommitted(
            dict(row), AUTHORITY_POLICY_V2_ATTEMPT_STAGE_ADMITTED,
            require_finalization=True, _history=history,
        ):
            return None
        result = self._conn.execute(
            "SELECT * FROM task_results WHERE id=?", (attempt.result_id,),
        ).fetchone()
        if result is None or (
            result["task_id"], result["agent"], result["session_id"]
        ) != (attempt.root_task_id, attempt.manager_agent, attempt.manager_session_id):
            return None
        try:
            binding = self.get_authority_policy_v2_session_binding(
                root_task_id=attempt.root_task_id, manager_agent=attempt.manager_agent,
                manager_session_id=attempt.manager_session_id,
            )
            if binding is None:
                return None
            self._authenticate_v2_session_binding_uncommitted(binding)
        except ValueError:
            return None
        joined = (
            "binding_id", "team", "root_task_id", "manager_agent", "manager_session_id",
            "contract_id", "contract_version", "contract_digest", "release_id",
            "activation_id", "activation_epoch", "selector_id",
        )
        if any(getattr(binding, key) != getattr(attempt, key) for key in joined):
            return None
        candidate_row = self._conn.execute(
            "SELECT * FROM authority_policy_v2_candidates WHERE result_id=?",
            (attempt.result_id,),
        ).fetchone()
        candidate_identity = (None,)
        if candidate_row is not None:
            try:
                candidate = self._authority_policy_v2_candidate_from_row(candidate_row)
            except ValueError:
                return None
            if any(getattr(candidate, key) != getattr(attempt, key) for key in (
                "attempt_id", "result_id", "binding_id", "team", "root_task_id",
                "manager_agent", "manager_session_id", "contract_id", "contract_version",
                "contract_digest", "release_id",
                "activation_id", "activation_epoch", "selector_id", "origin_boot_id",
                "owner_attempt_id",
            )) or any(getattr(candidate, key) != getattr(binding, key) for key in (
                "policy_digest", "policy_version", "provider_id", "executor_kind", "model_id",
            )):
                return None
            # Retain identity/linkage only. Claim-time schema observations stay
            # observed-only and candidate lifecycle is mutable stage evidence.
            candidate_identity = tuple(getattr(candidate, key) for key in (
                "candidate_id", "claim_key", "attempt_id", "result_id", "binding_id",
                "team", "root_task_id", "manager_agent", "manager_session_id",
                "contract_id", "contract_version", "contract_digest", "release_id",
                "activation_id", "activation_epoch", "selector_id", "origin_boot_id",
                "owner_attempt_id", "policy_digest", "policy_version", "provider_id",
                "executor_kind", "model_id", "causal_result_id", "causal_result_digest",
            ))
        identity = tuple(
            value for key, value in attempt.model_dump(mode="json").items()
            if key not in ("stage", "finalization_state", "refusal_code")
        ) + tuple(binding.model_dump(mode="json").values()) + candidate_identity
        if any(type(value) not in (str, int, type(None)) for value in identity):
            return None
        return _V2StartupAnchor(identity)

    def _authenticate_v2_attempt_admission_uncommitted(
        self, *, task_id: str, agent: str, session_id: str, admission: dict,
    ) -> bool:
        """Match the supplied v2 admission against the durable launch binding."""
        required = (
            "team", "binding_id", "contract_id", "contract_version",
            "contract_digest", "release_id", "activation_id", "activation_epoch",
            "selector_id", "assessment_digest", "assessment_canonical_json",
            "origin_boot_id",
        )
        if any(admission.get(key) in (None, "") for key in required):
            return False
        if len(str(admission["assessment_canonical_json"]).encode("utf-8")) > 65536:
            return False
        binding = self.get_authority_policy_v2_session_binding(
            root_task_id=task_id, manager_agent=agent, manager_session_id=session_id,
        )
        if binding is None:
            return False
        return (
            binding.binding_id == admission["binding_id"]
            and binding.team == admission["team"]
            and binding.contract_id == admission["contract_id"]
            and binding.contract_version == admission["contract_version"]
            and binding.contract_digest == admission["contract_digest"]
            and binding.release_id == admission["release_id"]
            and binding.activation_id == admission["activation_id"]
            and binding.activation_epoch == admission["activation_epoch"]
            and binding.selector_id == admission["selector_id"]
        )

    def _authenticate_v2_attempt_admission_audit_uncommitted(
        self, attempt_row: dict,
    ) -> bool:
        """Require exactly one closed admission-stage audit for one attempt.

        The immutable ``authority_policy_v2_attempts`` row is only authoritative
        admitted evidence together with its write-once
        ``authority_policy_v2_result_stage`` audit.  A deleted, duplicated,
        mutated or mismatched audit is missing evidence and refuses: the caller
        must not allocate, repair, or repair-by-reinsert anything.  The audit's
        admission-stage snapshot is compared against the immutable attempt
        identity/reference columns plus its own frozen stage/finalization
        markers, so a later legitimate finalization of the mutable attempt row
        does not invalidate the admission record.

        C3b stage scoping: this authenticates the **admitted** event only,
        inside its own stage.  Later legitimate stage events on the same
        attempt (``claim_audited``) add result-stage rows but never invalidate
        the single immutable ``a0`` admission event.
        """
        return self._authenticate_v2_attempt_stage_audit_uncommitted(
            attempt_row, AUTHORITY_POLICY_V2_ATTEMPT_STAGE_ADMITTED,
            require_unfinalized=True,
        )

    def _authenticate_v2_result_stage_audit_uncommitted(
        self, attempt_row: dict, stage: str, *, candidate_id: str | None = None,
        require_finalization: bool = False,
        _history: list[dict] | None = None,
    ) -> bool:
        """Require exactly one authentic CLOSED result-stage event for ``stage``.

        BOTH halves of every required prior stage are authenticated: this is the
        ``audit_log``/``authority_policy_v2_result_stage`` half (its sibling
        candidate-audit half is checked by
        ``_authenticate_v2_candidate_audit_uncommitted``).  The event must match
        the immutable attempt identity on every field, carry EXACTLY the closed
        payload key set for its stage (the later stages additionally carry the
        candidate identity), and — where the stage persists it — the required
        ``unfinalized`` finalization marker.  A missing, duplicated, mutated,
        foreign-candidate or malformed/extra-key event refuses.  Events
        belonging to a different stage are ignored, so a later legitimate stage
        never invalidates an earlier one and a not-yet-created stage is never
        required.
        """
        root_task_id = attempt_row["root_task_id"]
        manager_agent = attempt_row["manager_agent"]
        attempt_id = attempt_row["attempt_id"]
        try:
            candidates = [
                row for row in (
                    self.get_audit_logs(root_task_id) if _history is None else _history
                )
                if row.get("action") == AUTHORITY_POLICY_V2_RESULT_STAGE_ACTION
                and row.get("agent") == manager_agent
                and isinstance(row.get("payload"), dict)
                and row["payload"].get("attempt_id") == attempt_id
                and row["payload"].get("stage") == stage
            ]
        except Exception:
            return False
        if len(candidates) != 1:
            return False
        payload = candidates[0]["payload"]
        expected = {
            "stage": stage,
            "attempt_id": attempt_id,
            "result_id": attempt_row["result_id"],
            "binding_id": attempt_row["binding_id"],
            "contract_id": attempt_row["contract_id"],
            "contract_version": attempt_row["contract_version"],
            "contract_digest": attempt_row["contract_digest"],
            "release_id": attempt_row["release_id"],
            "activation_id": attempt_row["activation_id"],
            "activation_epoch": attempt_row["activation_epoch"],
            "selector_id": attempt_row["selector_id"],
            "assessment_digest": attempt_row["assessment_digest"],
            "owner_attempt_id": attempt_row["owner_attempt_id"],
            "origin_boot_id": attempt_row["origin_boot_id"],
        }
        if candidate_id is not None:
            expected["candidate_id"] = candidate_id
        if require_finalization:
            expected["finalization_state"] = "unfinalized"
        if set(payload.keys()) != set(expected.keys()):
            return False
        return all(payload.get(key) == value for key, value in expected.items())

    def _authenticate_v2_attempt_stage_audit_uncommitted(
        self, attempt_row: dict, stage: str, *, require_unfinalized: bool = False,
    ) -> bool:
        """Wrapper over the closed result-stage authentication for ``stage``.

        Every needed later stage is authenticated independently; a missing,
        duplicated, mutated, foreign-candidate or closed-key/malformed event for
        the requested stage refuses.  Events belonging to a different stage are
        ignored.
        """
        return self._authenticate_v2_result_stage_audit_uncommitted(
            attempt_row, stage, require_finalization=require_unfinalized,
        )

    def _authenticate_v2_prior_result_stages_uncommitted(
        self, attempt_row: dict, stages: tuple[str, ...], candidate_id: str,
    ) -> bool:
        """Require the result-stage half of every named prior stage."""
        return all(
            self._authenticate_v2_result_stage_audit_uncommitted(
                attempt_row, stage, candidate_id=candidate_id,
                require_finalization=True,
            )
            for stage in stages
        )

    def _insert_authority_policy_v2_attempt_uncommitted(
        self, *, task_id: str, agent: str, session_id: str, result_id: int,
        admission: dict, now: str,
    ) -> AuthorityPolicyV2Attempt:
        """Insert one admitted attempt row plus its admission audit.

        Runs inside the callback admission transaction.  The deterministic
        attempt ID and the randomly allocated ``owner_attempt_id`` are fixed by
        this winning insert; no second result/attempt/audit is ever allocated
        for the same exact tuple.
        """
        attempt_id = authority_policy_v2_attempt_id(
            manager_agent=agent, manager_session_id=session_id,
            result_id=result_id, root_task_id=task_id, team=admission["team"],
        )
        attempt = AuthorityPolicyV2Attempt(
            attempt_id=attempt_id,
            team=admission["team"],
            root_task_id=task_id,
            manager_agent=agent,
            manager_session_id=session_id,
            result_id=result_id,
            binding_id=admission["binding_id"],
            contract_id=admission["contract_id"],
            contract_version=admission["contract_version"],
            contract_digest=admission["contract_digest"],
            release_id=admission["release_id"],
            activation_id=admission["activation_id"],
            activation_epoch=admission["activation_epoch"],
            selector_id=admission["selector_id"],
            stage=AUTHORITY_POLICY_V2_ATTEMPT_STAGE_ADMITTED,
            finalization_state="unfinalized",
            refusal_code=None,
            origin_boot_id=admission["origin_boot_id"],
            owner_attempt_id=str(uuid.uuid4()),
            assessment_digest=admission["assessment_digest"],
        )
        snapshot = attempt.model_dump(mode="json")
        self._conn.execute(
            """INSERT INTO authority_policy_v2_attempts
               (attempt_id, team, root_task_id, manager_agent, manager_session_id,
                result_id, binding_id, contract_id, contract_version, contract_digest,
                release_id, activation_id, activation_epoch, selector_id, stage,
                finalization_state, refusal_code, origin_boot_id, owner_attempt_id,
                assessment_digest, canonical_payload_json, created_at)
               VALUES (:attempt_id,:team,:root_task_id,:manager_agent,
                       :manager_session_id,:result_id,:binding_id,:contract_id,
                       :contract_version,:contract_digest,:release_id,:activation_id,
                       :activation_epoch,:selector_id,:stage,:finalization_state,
                       :refusal_code,:origin_boot_id,:owner_attempt_id,
                       :assessment_digest,:canonical_payload_json,:created_at)""",
            {
                **snapshot,
                "attempt_id": attempt_id,
                "finalization_state": "unfinalized",
                "refusal_code": None,
                "owner_attempt_id": attempt.owner_attempt_id,
                "canonical_payload_json": authority_policy_v2_canonical_json_bytes(
                    snapshot
                ).decode("utf-8"),
            },
        )
        self.insert_audit_log_uncommitted(
            task_id, agent, AUTHORITY_POLICY_V2_RESULT_STAGE_ACTION,
            {
                "stage": AUTHORITY_POLICY_V2_ATTEMPT_STAGE_ADMITTED,
                "attempt_id": attempt_id,
                "result_id": result_id,
                "binding_id": admission["binding_id"],
                "contract_id": admission["contract_id"],
                "contract_version": admission["contract_version"],
                "contract_digest": admission["contract_digest"],
                "release_id": admission["release_id"],
                "activation_id": admission["activation_id"],
                "activation_epoch": admission["activation_epoch"],
                "selector_id": admission["selector_id"],
                "assessment_digest": admission["assessment_digest"],
                "owner_attempt_id": attempt.owner_attempt_id,
                "origin_boot_id": admission["origin_boot_id"],
                "finalization_state": "unfinalized",
            },
        )
        return attempt

    @_synchronized
    def get_authority_policy_v2_attempt_for_result(
        self, result_id: int,
    ) -> AuthorityPolicyV2Attempt | None:
        """Authenticated read of the admitted attempt bound to one result.

        Returns ``None`` when the attempt row is absent/corrupt or its unique
        matching admission-stage audit is missing, duplicated, mutated or
        mismatched — a partial attempt without its admitted audit is never a
        usable authenticated read.
        """
        row = self._conn.execute(
            "SELECT * FROM authority_policy_v2_attempts WHERE result_id=?",
            (result_id,),
        ).fetchone()
        if row is None:
            return None
        try:
            attempt = self._authority_policy_v2_attempt_from_row(row)
        except ValueError:
            return None
        if not self._authenticate_v2_attempt_admission_audit_uncommitted(dict(row)):
            return None
        return attempt

    @_synchronized
    def get_authority_policy_v2_attempt(
        self, *, root_task_id: str, manager_agent: str, manager_session_id: str,
        result_id: int,
    ) -> AuthorityPolicyV2Attempt | None:
        row = self._conn.execute(
            """SELECT * FROM authority_policy_v2_attempts
               WHERE root_task_id=? AND manager_agent=?
                 AND manager_session_id=? AND result_id=?""",
            (root_task_id, manager_agent, manager_session_id, result_id),
        ).fetchone()
        if row is None:
            return None
        try:
            attempt = self._authority_policy_v2_attempt_from_row(row)
        except ValueError:
            return None
        if not self._authenticate_v2_attempt_admission_audit_uncommitted(dict(row)):
            return None
        return attempt

    def _authority_policy_v2_attempt_from_row(self, row) -> AuthorityPolicyV2Attempt:
        try:
            attempt = AuthorityPolicyV2Attempt.model_validate_json(
                row["canonical_payload_json"]
            )
        except Exception as exc:
            raise ValueError("authority v2 attempt has a corrupt canonical payload") from exc
        if attempt.attempt_id != row["attempt_id"]:
            raise ValueError("authority v2 attempt identity mismatch")
        for column, value in attempt.model_dump(mode="json").items():
            if column == "finalization_state":
                continue
            if row[column] != value:
                raise ValueError("authority v2 attempt column/preimage mismatch")
        if row["finalization_state"] != attempt.finalization_state:
            raise ValueError("authority v2 attempt finalization mismatch")
        return attempt

    @_synchronized
    def list_authority_policy_v2_result_stage_audits(
        self, *, root_task_id: str, manager_agent: str,
    ) -> list[dict]:
        """Return the closed admission-stage audit rows for one root/agent."""
        return [
            row for row in self.get_audit_logs(root_task_id)
            if row["action"] == AUTHORITY_POLICY_V2_RESULT_STAGE_ACTION
            and row.get("agent") == manager_agent
        ]

    # -- THR-229 checkpoint C3b: durable v2 candidate/pin claim and the
    # separate claim-audit stage.  The Database owns synchronization, the
    # BEGIN IMMEDIATE boundary and every write; the store is a thin forwarder.
    # This checkpoint deliberately implements NO refusal housekeeping and NO
    # evaluation/consume/envelope/finalization: a refusal returns only a
    # bounded disposition for the later consumer.

    def bind_authority_policy_v2_permission_surface_reader(self, reader) -> None:
        """Bind the server-side permission reader ``reader(agent) -> digest``.

        The orchestration seam (the store the orchestrator constructs) supplies
        a callable that reads the live org permission surface; it is never a
        caller-supplied allow/deny boolean or a precomputed digest.  Unbinding
        (``None``) makes every claim refuse fail-closed.
        """
        self._v2_permission_surface_reader = reader

    def bind_authority_policy_v2_process_boot_id(self, boot_id: str | None) -> None:
        """Bind the trusted current daemon-process identity for housekeeping.

        This is the existing server-owned process context (the same per-daemon
        ``authority_v2_origin_boot_id`` recorded on admitted attempts), never a
        caller-supplied allow boolean.  When unbound, an attempt whose origin
        daemon boot may be gone cannot be safely attributed without the live
        owner token or a recorded failed-stage obligation; housekeeping then
        returns bounded ``housekeeping_pending`` without mutating task/Q/J.
        """
        self._v2_process_boot_id = boot_id

    def _forget_v2_live_owner(self, attempt_id: str, owner_attempt_id: str) -> None:
        if self._v2_live_attempt_owners.get(attempt_id) == owner_attempt_id:
            self._v2_live_attempt_owners.pop(attempt_id, None)

    def _mark_v2_refusal_failure_authority(
        self, attempt_id: str, owner_attempt_id: str,
    ) -> None:
        """Record that the authentic owner's refusal transaction failed.

        This is set only after the live-owner token proved the caller was the
        uninterrupted authentic owner, so a malformed/foreign/nested/duplicate
        contender can never poison a valid winner.  Claim/evaluate/consume/audit
        advancement is already prohibited because the live token was forgotten;
        this marker exists solely so safely attributable housekeeping may still
        retry when the durable obligation could not be persisted.
        """
        self._v2_refusal_failed_owners[attempt_id] = owner_attempt_id

    def _clear_v2_refusal_failure_authority(
        self, attempt_id: str, owner_attempt_id: str | None = None,
    ) -> None:
        if owner_attempt_id is None or (
            self._v2_refusal_failed_owners.get(attempt_id) == owner_attempt_id
        ):
            self._v2_refusal_failed_owners.pop(attempt_id, None)

    def _v2_refusal_failure_authority(
        self, *, attempt_id: str, owner_attempt_id: str | None,
    ) -> bool:
        """True only for the exact owner whose refusal transaction failed."""
        return (
            owner_attempt_id is not None
            and self._v2_refusal_failed_owners.get(attempt_id) == owner_attempt_id
        )

    def _v2_contender_is_authentic_owner(
        self, *, attempt_id: str, owner_attempt_id: str, origin_boot_id: str,
    ) -> bool:
        """True only when this caller presents the real uninterrupted owner proof.

        The registered in-memory token must match AND the durable attempt's
        ``owner_attempt_id``/``origin_boot_id`` must equal the caller's values.
        Persisted UUID strings are readable identity markers, not proof, so a
        wrong-boot / wrong-token / wrong-tuple contender is classified as
        unauthorized and never changes the original owner's liveness.
        """
        if self._v2_live_attempt_owners.get(attempt_id) != owner_attempt_id:
            return False
        try:
            row = self._conn.execute(
                """SELECT owner_attempt_id, origin_boot_id
                     FROM authority_policy_v2_attempts WHERE attempt_id=?""",
                (attempt_id,),
            ).fetchone()
        except Exception:
            return False
        if row is None:
            return False
        return (
            row["owner_attempt_id"] == owner_attempt_id
            and row["origin_boot_id"] == origin_boot_id
        )

    def _refuse_v2_stage(
        self, *, attempt_id: str, owner_attempt_id: str, code: str,
        origin_boot_id: str | None = None,
        candidate_id: str | None = None, claim_key: str | None = None,
        stage: str | None = None, poison: bool | None = None,
    ) -> AuthorityPolicyV2StageOutcome:
        """Return a bounded refusal, classifying liveness before poisoning.

        ``poison=None`` auto-classifies: only the authentic continuing owner
        (registered live token AND matching durable owner/boot) is forgotten, so
        an unauthorized/stale/duplicate contender cannot poison the winner.
        ``poison=True`` forces the owned-stage failure residue; ``poison=False``
        never forgets the owner (transaction-nesting and duplicate refusals).
        """
        if poison is None:
            forget = (
                origin_boot_id is not None
                and self._v2_contender_is_authentic_owner(
                    attempt_id=attempt_id, owner_attempt_id=owner_attempt_id,
                    origin_boot_id=origin_boot_id,
                )
            )
        else:
            forget = poison
        if forget:
            # THR-229 C3d1: record the durable server-owned failed-stage
            # obligation BEFORE forgetting the process-local token, so a
            # genuine failed-stage request remains safely attributable for
            # refusal housekeeping after the winner is poisoned.  Best-effort:
            # a storage failure here must never mask the primary refusal, and an
            # exhausted/unwritable store promises no new durable diagnostic.
            self._record_v2_failed_stage_obligation(
                attempt_id=attempt_id, owner_attempt_id=owner_attempt_id,
                code=code,
            )
            self._forget_v2_live_owner(attempt_id, owner_attempt_id)
        return AuthorityPolicyV2StageOutcome(
            status="refused", refusal_code=code, attempt_id=attempt_id,
            candidate_id=candidate_id, claim_key=claim_key, stage=stage,
        )

    def _record_v2_failed_stage_obligation(
        self, *, attempt_id: str, owner_attempt_id: str, code: str,
    ) -> None:
        """Persist the closed failed-stage housekeeping obligation (best effort).

        Only a server-owned attempt whose ``origin_boot_id`` equals the bound
        current daemon-process identity can produce an obligation, so the record
        is trusted context rather than a caller-supplied boolean.  It is written
        only for a closed refusal code on an unfinalized attempt, and never
        fabricates task/receipt ownership.
        """
        # Stage writers expose a more precise closed refusal vocabulary than
        # terminal housekeeping. Persist the same lossy mapping consumed by
        # run_authority_hook before the live owner is forgotten; otherwise a
        # schema/evidence refusal leaves no durable authority for the later
        # finalizer and the task remains a recoverable zombie forever.
        housekeeping_code = (
            _AUTHORITY_POLICY_V2_STAGE_REFUSAL_TO_HOUSEKEEPING.get(
                code, "interrupted_pre_final",
            )
        )
        if housekeeping_code not in AUTHORITY_POLICY_V2_HOUSEKEEPING_REFUSAL_CODES:
            return
        if self._v2_process_boot_id is None:
            return
        try:
            if self._conn.in_transaction:
                return
            row = self._conn.execute(
                """SELECT root_task_id, manager_agent, result_id, origin_boot_id,
                          owner_attempt_id, finalization_state
                     FROM authority_policy_v2_attempts WHERE attempt_id=?""",
                (attempt_id,),
            ).fetchone()
            if row is None or row["finalization_state"] != "unfinalized":
                return
            if row["origin_boot_id"] != self._v2_process_boot_id:
                return
            if row["owner_attempt_id"] != owner_attempt_id:
                return
            existing = self._conn.execute(
                """SELECT 1 FROM audit_log
                   WHERE action=? AND task_id=? AND agent=?
                     AND json_extract(payload,'$.attempt_id')=?""",
                (
                    AUTHORITY_POLICY_V2_HOUSEKEEPING_OBLIGATION_ACTION,
                    row["root_task_id"], row["manager_agent"], attempt_id,
                ),
            ).fetchone()
            if existing is not None:
                # Exactly one obligation authorizes housekeeping; a repeated
                # failure must never create a second row (which the
                # cardinality-1 authenticator would reject).
                return
            self._conn.execute(
                "INSERT INTO audit_log (task_id, agent, action, payload, timestamp) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    row["root_task_id"], row["manager_agent"],
                    AUTHORITY_POLICY_V2_HOUSEKEEPING_OBLIGATION_ACTION,
                    json.dumps({
                        "attempt_id": attempt_id,
                        "result_id": row["result_id"],
                        "refusal_code": housekeeping_code,
                        "origin_boot_id": row["origin_boot_id"],
                        "owner_attempt_id": row["owner_attempt_id"],
                    }),
                    _now().isoformat(),
                ),
            )
            self._conn.commit()
        except Exception:
            try:
                self._conn.rollback()
            except Exception:
                pass

    def _authenticate_v2_result_body_uncommitted(
        self, result_row, attempt: AuthorityPolicyV2Attempt,
    ) -> bool:
        """Authenticate the persisted result's normalized assessment body AND
        the actual persisted manager decision it carries.

        CRD is only the row-identity digest; the persisted decision carrier's
        ``_manager_self_evaluation`` value is re-canonicalized here and must
        hash to the admitted ``assessment_digest``.  SEPARATELY, the persisted
        manager decision's ``action`` must be the accepted root escalation
        action: eligible v2 authority evaluation is for the accepted root
        escalation decision only, so a missing/malformed/non-escalate decision
        (an ordinary ``delegate``/``supersede``/``done`` etc., or an unchanged
        assessment whose decision was drifted between stages) can never acquire
        the v2 continuation path.  This inspects persisted decision DATA only —
        no prose, clause or sentinel detector is introduced and ordinary manager
        validation is untouched.
        """
        raw = result_row["decision_json"]
        if not isinstance(raw, str) or not raw:
            return False
        try:
            parsed = json.loads(raw)
        except Exception:
            return False
        if not isinstance(parsed, dict):
            return False
        if parsed.get("action") != AUTHORITY_POLICY_V2_ESCALATION_DECISION_ACTION:
            return False
        carrier = parsed.get("_manager_self_evaluation")
        if not isinstance(carrier, dict):
            return False
        try:
            return authority_policy_v2_sha256(carrier) == attempt.assessment_digest
        except Exception:
            return False

    def _advance_v2_attempt_stage_uncommitted(
        self, attempt: AuthorityPolicyV2Attempt, new_stage: str,
    ) -> None:
        """Advance the mutable stage/payload pair in ONE consistent update."""
        expected = AUTHORITY_POLICY_V2_ATTEMPT_STAGE_TRANSITIONS.get(attempt.stage)
        if expected != new_stage:
            raise ValueError("illegal v2 attempt stage transition")
        snapshot = attempt.model_dump(mode="json")
        snapshot["stage"] = new_stage
        self._conn.execute(
            """UPDATE authority_policy_v2_attempts
                  SET stage=?, canonical_payload_json=?
                WHERE attempt_id=?""",
            (
                new_stage,
                authority_policy_v2_canonical_json_bytes(snapshot).decode("utf-8"),
                attempt.attempt_id,
            ),
        )

    def _authority_policy_v2_candidate_from_row(self, row) -> AuthorityPolicyV2Candidate:
        try:
            candidate = AuthorityPolicyV2Candidate.model_validate_json(
                row["canonical_payload_json"]
            )
        except Exception as exc:
            raise ValueError("authority v2 candidate has a corrupt canonical payload") from exc
        if candidate.candidate_id != row["candidate_id"]:
            raise ValueError("authority v2 candidate identity mismatch")
        for column, value in candidate.model_dump(mode="json").items():
            if row[column] != value:
                raise ValueError("authority v2 candidate column/preimage mismatch")
        return candidate

    def _authority_policy_v2_pin_from_row(self, row) -> AuthorityPolicyV2Pin:
        try:
            pin = AuthorityPolicyV2Pin.model_validate_json(
                row["canonical_payload_json"]
            )
        except Exception as exc:
            raise ValueError("authority v2 pin has a corrupt canonical payload") from exc
        if pin.candidate_id != row["candidate_id"]:
            raise ValueError("authority v2 pin identity mismatch")
        for column, value in pin.model_dump(mode="json").items():
            if row[column] != value:
                raise ValueError("authority v2 pin column/preimage mismatch")
        return pin

    def _retained_v2_mechanical_eligibility_uncommitted(
        self, task, *, max_revise_rounds: int,
    ) -> str | None:
        """Re-derive retained mechanical eligibility from persisted state.

        Used at EVERY v2 stage boundary (claim and all later pre-final
        boundaries): root-only, revisit/successor lineage, active chain/fanout,
        blocked job and the CURRENTLY-configured revise ceiling against the
        persisted ``tasks.revision_count``.  No caller boolean is trusted and no
        v1 adverse-review/partial-work/raw-DDL clause is a v2 veto.
        """
        if task["parent_task_id"] is not None or task["revisit_of_task_id"]:
            return "claim_failed"
        if (
            task["active_chain"]
            or task["active_fanout"]
            or task["blocked_on_job_ids"]
        ):
            return "claim_failed"
        successor = self._conn.execute(
            "SELECT 1 FROM manager_supersessions WHERE successor_task_id=? LIMIT 1",
            (task["id"],),
        ).fetchone()
        if successor is not None:
            return "claim_failed"
        if max_revise_rounds > 0 and int(task["revision_count"] or 0) >= max_revise_rounds:
            return "claim_failed"
        return None

    def _authority_policy_v2_attempt_id_for_identity(
        self, *, root_task_id: str, manager_agent: str,
        manager_session_id: str, result_id: int,
    ) -> str:
        """Derive an attempt identity from durable bound team evidence only.

        Existing attempt rows win. Before an attempt exists, the immutable v2
        session binding supplies the team; the persisted task team is the
        final pre-admission source. If none exists, the bounded refusal ID uses
        a null team member rather than inventing or defaulting a team.
        """
        row = self._conn.execute(
            """SELECT attempt_id FROM authority_policy_v2_attempts
               WHERE root_task_id=? AND manager_agent=?
                 AND manager_session_id=? AND result_id=?""",
            (root_task_id, manager_agent, manager_session_id, result_id),
        ).fetchone()
        if row is not None:
            return str(row["attempt_id"])
        binding = self._conn.execute(
            """SELECT team FROM authority_policy_v2_session_bindings
               WHERE root_task_id=? AND manager_agent=? AND manager_session_id=?""",
            (root_task_id, manager_agent, manager_session_id),
        ).fetchone()
        task = self._conn.execute(
            "SELECT team FROM tasks WHERE id=?", (root_task_id,),
        ).fetchone()
        team = binding["team"] if binding is not None else (
            task["team"] if task is not None else None
        )
        if isinstance(team, str) and team:
            return authority_policy_v2_attempt_id(
                manager_agent=manager_agent,
                manager_session_id=manager_session_id,
                result_id=result_id,
                root_task_id=root_task_id,
                team=team,
            )
        return "APV2R-" + authority_policy_v2_sha256({
            "manager_agent": manager_agent,
            "manager_session_id": manager_session_id,
            "result_id": result_id,
            "root_task_id": root_task_id,
            "team": None,
        })

    def _authenticate_v2_claim_evidence_uncommitted(
        self, *, root_task_id: str, manager_agent: str, manager_session_id: str,
        result_id: int, origin_boot_id: str, owner_attempt_id: str,
        max_revise_rounds: int = 0,
    ) -> tuple[str | None, dict | None]:
        """Re-read and authenticate the complete claim-stage evidence.

        Shared by BOTH the first (claim) and second (claim-audit) boundaries so
        the second boundary re-authenticates the causal result row/body, the
        immutable launch binding, the authenticated pinned
        release/activation/selector prefix, the single ``a0`` admitted audit and
        the current task ownership/cancellation — rather than trusting the
        already-persisted K/P row.  It also re-derives the retained mechanical
        eligibility from the persisted task at every boundary.  Returns
        ``(refusal_code, None)`` on any mismatch/mutation/deletion, or
        ``(None, ctx)`` with the authenticated values.
        """
        attempt_id = self._authority_policy_v2_attempt_id_for_identity(
            root_task_id=root_task_id, manager_agent=manager_agent,
            manager_session_id=manager_session_id, result_id=result_id,
        )
        row = self._conn.execute(
            """SELECT * FROM authority_policy_v2_attempts
               WHERE root_task_id=? AND manager_agent=?
                 AND manager_session_id=? AND result_id=?""",
            (root_task_id, manager_agent, manager_session_id, result_id),
        ).fetchone()
        if row is None:
            return "identity_mismatch", None
        try:
            attempt = self._authority_policy_v2_attempt_from_row(row)
        except ValueError:
            return "identity_mismatch", None
        if attempt.finalization_state != "unfinalized":
            return "owner_lost", None
        # Uninterrupted live-owner proof: the in-memory winning token, the
        # persisted random owner marker and the daemon-process boot UUID must
        # all agree.  Persisted UUID strings alone are never proof.
        if self._v2_live_attempt_owners.get(attempt.attempt_id) != owner_attempt_id:
            return "owner_lost", None
        if (
            owner_attempt_id != attempt.owner_attempt_id
            or origin_boot_id != attempt.origin_boot_id
        ):
            return "owner_lost", None
        if not self._authenticate_v2_attempt_admission_audit_uncommitted(dict(row)):
            return "identity_mismatch", None

        task = self._conn.execute(
            "SELECT * FROM tasks WHERE id=?", (root_task_id,)
        ).fetchone()
        if task is None:
            return "identity_mismatch", None
        if task["cancelled_at"] is not None or task["status"] in {
            "completed", "failed", "cancelled", "superseded",
        }:
            return "cancelled", None
        if task["status"] != "in_progress" or task["block_kind"] is not None:
            return "cancelled", None
        if (
            task["assigned_agent"] != manager_agent
            or task["current_session_id"] != manager_session_id
        ):
            return "owner_lost", None

        # THR-229 C3c correction: retained current mechanical eligibility is
        # re-derived from the authenticated task at EVERY stage boundary, not
        # only at claim.  Valid-at-claim is insufficient: a chain/fanout/blocked
        # job/revisit/successor/root change or a currently-exhausted configured
        # revise ceiling refuses here with the exact prior residue retained.
        eligibility = self._retained_v2_mechanical_eligibility_uncommitted(
            task, max_revise_rounds=max_revise_rounds,
        )
        if eligibility is not None:
            return eligibility, None

        result_row = self._conn.execute(
            "SELECT * FROM task_results WHERE id=?", (result_id,)
        ).fetchone()
        if (
            result_row is None
            or result_row["task_id"] != root_task_id
            or result_row["agent"] != manager_agent
            or result_row["session_id"] != manager_session_id
        ):
            return "identity_mismatch", None
        if not self._authenticate_v2_result_body_uncommitted(result_row, attempt):
            return "identity_mismatch", None

        binding = self.get_authority_policy_v2_session_binding(
            root_task_id=root_task_id, manager_agent=manager_agent,
            manager_session_id=manager_session_id,
        )
        if binding is None:
            return "identity_mismatch", None
        try:
            self._authenticate_v2_session_binding_uncommitted(binding)
        except Exception:
            return "identity_mismatch", None
        if (
            binding.binding_id != attempt.binding_id
            or binding.team != attempt.team
            or binding.contract_id != attempt.contract_id
            or binding.contract_version != attempt.contract_version
            or binding.contract_digest != attempt.contract_digest
            or binding.release_id != attempt.release_id
            or binding.activation_id != attempt.activation_id
            or binding.activation_epoch != attempt.activation_epoch
            or binding.selector_id != attempt.selector_id
        ):
            return "identity_mismatch", None

        release = self.get_authority_policy_v2_release(attempt.release_id)
        activation = self.get_authority_policy_v2_activation(attempt.activation_id)
        if release is None or activation is None:
            return "identity_mismatch", None
        if (
            release.team != attempt.team
            or release.policy_digest != binding.policy_digest
            or release.version != binding.policy_version
            or activation.team != attempt.team
            or activation.release_id != attempt.release_id
            or activation.release_digest != release.policy_digest
            or activation.selector_epoch != attempt.activation_epoch
            or binding.provider_id == "" or binding.executor_kind == ""
            or binding.model_id == ""
        ):
            return "identity_mismatch", None
        return None, {
            "attempt": attempt, "row": row, "task": task,
            "result_row": result_row, "binding": binding,
            "release": release, "activation": activation,
        }

    def _claim_authority_policy_v2_candidate_uncommitted(
        self, *, root_task_id: str, manager_agent: str, manager_session_id: str,
        result_id: int, origin_boot_id: str, owner_attempt_id: str,
        max_revise_rounds: int, now: str, schema_observation,
    ) -> AuthorityPolicyV2StageOutcome:
        from runtime.orchestrator.authority import (
            capture_authority_policy_v2_permission_surface,
        )

        attempt_id = self._authority_policy_v2_attempt_id_for_identity(
            root_task_id=root_task_id, manager_agent=manager_agent,
            manager_session_id=manager_session_id, result_id=result_id,
        )

        def _refused(code: str) -> AuthorityPolicyV2StageOutcome:
            return AuthorityPolicyV2StageOutcome(
                status="refused", refusal_code=code, attempt_id=attempt_id,
            )

        code, ctx = self._authenticate_v2_claim_evidence_uncommitted(
            root_task_id=root_task_id, manager_agent=manager_agent,
            manager_session_id=manager_session_id, result_id=result_id,
            origin_boot_id=origin_boot_id, owner_attempt_id=owner_attempt_id,
            max_revise_rounds=max_revise_rounds,
        )
        if code is not None:
            return _refused(code)
        assert ctx is not None
        attempt = ctx["attempt"]
        binding = ctx["binding"]
        release = ctx["release"]
        if attempt.stage != AUTHORITY_POLICY_V2_ATTEMPT_STAGE_ADMITTED:
            if attempt.stage in (
                AUTHORITY_POLICY_V2_ATTEMPT_STAGE_CLAIMED,
                AUTHORITY_POLICY_V2_ATTEMPT_STAGE_CLAIM_AUDITED,
            ):
                return _refused("already_claimed")
            return _refused("identity_mismatch")
        # The applicable mechanical eligibility predicates (revisit/successor
        # lineage, root-only, active chain/fanout, blocked job and the current
        # revise ceiling against the persisted revision_count) are re-derived
        # inside ``_authenticate_v2_claim_evidence_uncommitted``, which is
        # shared by every stage boundary.  No caller boolean and no
        # ``_server_fact_clause`` adverse-review/partial-work/raw-DDL clause is a
        # v2 veto or a phrase/clause unlock.

        # Freeze the read-only permission-surface evidence through the bound
        # server-side reader.  Captured after authentication (so an
        # unauthorized contender still refuses with its real code) but before
        # any K/P write; an unbound/read-failed/malformed read fails closed and
        # can never become a sentinel digest.
        permission = capture_authority_policy_v2_permission_surface(
            self, manager_agent,
        )
        if permission.evidence is None:
            return _refused("evidence_drift")

        claim_key = authority_policy_v2_sha256(
            authority_policy_v2_candidate_claim_preimage(
                activation_id=attempt.activation_id,
                activation_selector_epoch=attempt.activation_epoch,
                causal_result_digest=authority_policy_v2_causal_result_digest(result_id),
                causal_result_id=result_id,
                contract_digest=attempt.contract_digest,
                executor_kind=binding.executor_kind,
                manager_agent=manager_agent,
                manager_session_id=manager_session_id,
                model_id=binding.model_id,
                policy_digest=release.policy_digest,
                policy_version=release.version,
                provider_id=binding.provider_id,
                release_id=attempt.release_id,
                root_task_id=root_task_id,
                team=attempt.team,
            )
        )
        candidate = self._v2_candidate_from_claim_key(
            claim_key, attempt=attempt, binding=binding,
            origin_boot_id=origin_boot_id, owner_attempt_id=owner_attempt_id,
            schema_observation=schema_observation,
            permission_surface_digest=permission.evidence.digest,
        )
        return self._insert_v2_candidate_and_pin_uncommitted(
            candidate=candidate, attempt=attempt, binding=binding,
            owner_attempt_id=owner_attempt_id, origin_boot_id=origin_boot_id,
            now=now,
        )

    def _v2_candidate_from_claim_key(
        self, claim_key: str, *, attempt: AuthorityPolicyV2Attempt,
        binding: AuthorityPolicyV2SessionBinding, origin_boot_id: str,
        owner_attempt_id: str, schema_observation, permission_surface_digest: str,
    ) -> AuthorityPolicyV2Candidate:
        return AuthorityPolicyV2Candidate(
            candidate_id=f"APV2C-{claim_key}",
            claim_key=claim_key,
            team=attempt.team,
            root_task_id=attempt.root_task_id,
            manager_agent=attempt.manager_agent,
            manager_session_id=attempt.manager_session_id,
            attempt_id=attempt.attempt_id,
            result_id=attempt.result_id,
            binding_id=attempt.binding_id,
            contract_id=attempt.contract_id,
            contract_version=attempt.contract_version,
            contract_digest=attempt.contract_digest,
            release_id=attempt.release_id,
            policy_version=binding.policy_version,
            policy_digest=binding.policy_digest,
            activation_id=attempt.activation_id,
            activation_epoch=attempt.activation_epoch,
            selector_id=attempt.selector_id,
            provider_id=binding.provider_id,
            executor_kind=binding.executor_kind,
            model_id=binding.model_id,
            causal_result_id=attempt.result_id,
            causal_result_digest=authority_policy_v2_causal_result_digest(
                attempt.result_id
            ),
            origin_boot_id=origin_boot_id,
            owner_attempt_id=owner_attempt_id,
            schema_raw_digest=schema_observation.raw_digest,
            schema_inventory_digest=schema_observation.inventory_digest,
            schema_object_count=schema_observation.object_count,
            permission_surface_digest=permission_surface_digest,
        )

    def _insert_v2_candidate_and_pin_uncommitted(
        self, *, candidate: AuthorityPolicyV2Candidate,
        attempt: AuthorityPolicyV2Attempt,
        binding: AuthorityPolicyV2SessionBinding, owner_attempt_id: str,
        origin_boot_id: str, now: str,
    ) -> AuthorityPolicyV2StageOutcome:
        candidate_snapshot = candidate.model_dump(mode="json")
        self._conn.execute(
            """INSERT INTO authority_policy_v2_candidates
               (candidate_id, claim_key, team, root_task_id, manager_agent,
                manager_session_id, attempt_id, result_id, binding_id, contract_id,
                contract_version, contract_digest, release_id, policy_version,
                policy_digest, activation_id, activation_epoch, selector_id,
                provider_id, executor_kind, model_id, causal_result_id,
                causal_result_digest, origin_boot_id, owner_attempt_id,
                schema_raw_digest, schema_inventory_digest, schema_object_count,
                permission_surface_digest,
                lifecycle_stage, canonical_payload_json, created_at)
               VALUES (:candidate_id,:claim_key,:team,:root_task_id,:manager_agent,
                       :manager_session_id,:attempt_id,:result_id,:binding_id,
                       :contract_id,:contract_version,:contract_digest,:release_id,
                       :policy_version,:policy_digest,:activation_id,
                       :activation_epoch,:selector_id,:provider_id,:executor_kind,
                       :model_id,:causal_result_id,:causal_result_digest,
                       :origin_boot_id,:owner_attempt_id,:schema_raw_digest,
                       :schema_inventory_digest,:schema_object_count,
                       :permission_surface_digest,:lifecycle_stage,
                       :canonical_payload_json,
                       :created_at)""",
            {
                **candidate_snapshot,
                "canonical_payload_json": authority_policy_v2_canonical_json_bytes(
                    candidate_snapshot
                ).decode("utf-8"),
            },
        )
        pin = AuthorityPolicyV2Pin(
            candidate_id=candidate.candidate_id,
            claim_key=candidate.claim_key,
            team=candidate.team,
            root_task_id=candidate.root_task_id,
            manager_agent=candidate.manager_agent,
            manager_session_id=candidate.manager_session_id,
            attempt_id=candidate.attempt_id,
            result_id=candidate.result_id,
            binding_id=candidate.binding_id,
            release_id=candidate.release_id,
            activation_id=candidate.activation_id,
            activation_epoch=candidate.activation_epoch,
            selector_id=candidate.selector_id,
            policy_version=candidate.policy_version,
            policy_digest=candidate.policy_digest,
            contract_digest=candidate.contract_digest,
            provider_id=candidate.provider_id,
            executor_kind=candidate.executor_kind,
            model_id=candidate.model_id,
            schema_raw_digest=candidate.schema_raw_digest,
            schema_inventory_digest=candidate.schema_inventory_digest,
            schema_object_count=candidate.schema_object_count,
            permission_surface_digest=candidate.permission_surface_digest,
        )
        pin_snapshot = pin.model_dump(mode="json")
        self._conn.execute(
            """INSERT INTO authority_policy_v2_pins
               (candidate_id, claim_key, team, root_task_id, manager_agent,
                manager_session_id, attempt_id, result_id, binding_id, release_id,
                activation_id, activation_epoch, selector_id, policy_version,
                policy_digest, contract_digest, provider_id, executor_kind,
                model_id, schema_raw_digest, schema_inventory_digest,
                schema_object_count, permission_surface_digest,
                canonical_payload_json, created_at)
               VALUES (:candidate_id,:claim_key,:team,:root_task_id,:manager_agent,
                       :manager_session_id,:attempt_id,:result_id,:binding_id,
                       :release_id,:activation_id,:activation_epoch,:selector_id,
                       :policy_version,:policy_digest,:contract_digest,:provider_id,
                       :executor_kind,:model_id,:schema_raw_digest,
                       :schema_inventory_digest,:schema_object_count,
                       :permission_surface_digest,:canonical_payload_json,
                       :created_at)""",
            {
                **pin_snapshot,
                "canonical_payload_json": authority_policy_v2_canonical_json_bytes(
                    pin_snapshot
                ).decode("utf-8"),
            },
        )
        self._advance_v2_attempt_stage_uncommitted(
            attempt, AUTHORITY_POLICY_V2_ATTEMPT_STAGE_CLAIMED,
        )
        return AuthorityPolicyV2StageOutcome(
            status="claimed", attempt_id=attempt.attempt_id,
            candidate_id=candidate.candidate_id, claim_key=candidate.claim_key,
            stage=AUTHORITY_POLICY_V2_ATTEMPT_STAGE_CLAIMED,
        )

    @_synchronized
    def claim_authority_policy_v2_candidate(
        self, *, root_task_id: str, manager_agent: str, manager_session_id: str,
        result_id: int, origin_boot_id: str, owner_attempt_id: str,
        max_revise_rounds: int = 0,
    ) -> AuthorityPolicyV2StageOutcome:
        """First C3b transaction: atomically create K+P and advance J to claimed.

        Real schema values are observed once while holding BEGIN IMMEDIATE and
        stored on K/P as diagnostics.  They are not compared with any reference
        and are never rechecked.  The permission surface remains independently
        captured and rechecked.  This transaction never appends the separate
        ``a1`` claim-stage audit.

        Transaction ownership: when the caller ALREADY owns a transaction this
        method refuses with ``transaction_owned`` BEFORE it would BEGIN,
        ROLLBACK or invalidate the live owner, so the caller's transaction and
        its work are left untouched.  Moving the two stage commits into the
        caller's transaction, or silently using a savepoint, would change the
        R4 durability meaning and is deliberately not done.
        """
        from runtime.orchestrator.authority import (
            capture_authority_policy_v2_schema_observation,
        )

        attempt_id = self._authority_policy_v2_attempt_id_for_identity(
            root_task_id=root_task_id, manager_agent=manager_agent,
            manager_session_id=manager_session_id, result_id=result_id,
        )
        if self._conn.in_transaction:
            return self._refuse_v2_stage(
                attempt_id=attempt_id, owner_attempt_id=owner_attempt_id,
                code="transaction_owned",
                stage=AUTHORITY_POLICY_V2_ATTEMPT_STAGE_ADMITTED, poison=False,
            )
        now = _now().isoformat()
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            observation = capture_authority_policy_v2_schema_observation(self)
            if observation is None:
                self._conn.rollback()
                return self._refuse_v2_stage(
                    attempt_id=attempt_id, owner_attempt_id=owner_attempt_id,
                    code="claim_failed",
                    stage=AUTHORITY_POLICY_V2_ATTEMPT_STAGE_ADMITTED,
                    origin_boot_id=origin_boot_id,
                )
            outcome = self._claim_authority_policy_v2_candidate_uncommitted(
                root_task_id=root_task_id, manager_agent=manager_agent,
                manager_session_id=manager_session_id, result_id=result_id,
                origin_boot_id=origin_boot_id, owner_attempt_id=owner_attempt_id,
                max_revise_rounds=max_revise_rounds, now=now,
                schema_observation=observation,
            )
            if outcome.status == "refused":
                self._conn.rollback()
                return self._refuse_v2_stage(
                    attempt_id=attempt_id, owner_attempt_id=owner_attempt_id,
                    code=outcome.refusal_code or "claim_failed",
                    stage=AUTHORITY_POLICY_V2_ATTEMPT_STAGE_ADMITTED,
                    origin_boot_id=origin_boot_id,
                    poison=False if outcome.refusal_code in {
                        "already_claimed", "already_audited",
                    } else None,
                )
            self._conn.commit()
            return outcome
        except Exception:
            self._conn.rollback()
            self._refuse_v2_stage(
                attempt_id=attempt_id, owner_attempt_id=owner_attempt_id,
                code="claim_failed", origin_boot_id=origin_boot_id,
            )
            raise

    def _authenticate_v2_candidate_pin_joins_uncommitted(
        self, *, attempt: AuthorityPolicyV2Attempt,
        candidate: AuthorityPolicyV2Candidate, pin: AuthorityPolicyV2Pin,
        binding, release,
    ) -> bool:
        """The frozen K/P cross-row joins against the authenticated J/binding/release.

        Shared by the pre-final claim/evaluation boundaries and the post-final
        authenticator so both accept exactly the same immutable candidate/pin
        tuple; a mixed or drifted K/P row never matches.
        """
        return not (
            candidate.team != attempt.team
            or candidate.root_task_id != attempt.root_task_id
            or candidate.manager_agent != attempt.manager_agent
            or candidate.manager_session_id != attempt.manager_session_id
            or candidate.attempt_id != attempt.attempt_id
            or candidate.result_id != attempt.result_id
            or candidate.binding_id != attempt.binding_id
            or candidate.contract_id != attempt.contract_id
            or candidate.contract_version != attempt.contract_version
            or candidate.contract_digest != attempt.contract_digest
            or candidate.release_id != attempt.release_id
            or candidate.activation_id != attempt.activation_id
            or candidate.activation_epoch != attempt.activation_epoch
            or candidate.selector_id != attempt.selector_id
            or candidate.policy_version != release.version
            or candidate.policy_digest != release.policy_digest
            or candidate.provider_id != binding.provider_id
            or candidate.executor_kind != binding.executor_kind
            or candidate.model_id != binding.model_id
            or candidate.origin_boot_id != attempt.origin_boot_id
            or candidate.owner_attempt_id != attempt.owner_attempt_id
            or pin.candidate_id != candidate.candidate_id
            or pin.claim_key != candidate.claim_key
            or pin.team != candidate.team
            or pin.root_task_id != candidate.root_task_id
            or pin.manager_agent != candidate.manager_agent
            or pin.manager_session_id != candidate.manager_session_id
            or pin.attempt_id != candidate.attempt_id
            or pin.result_id != candidate.result_id
            or pin.binding_id != candidate.binding_id
            or pin.release_id != candidate.release_id
            or pin.activation_id != candidate.activation_id
            or pin.activation_epoch != candidate.activation_epoch
            or pin.selector_id != candidate.selector_id
            or pin.policy_version != candidate.policy_version
            or pin.policy_digest != candidate.policy_digest
            or pin.contract_digest != candidate.contract_digest
            or pin.provider_id != candidate.provider_id
            or pin.executor_kind != candidate.executor_kind
            or pin.model_id != candidate.model_id
            or pin.permission_surface_digest != candidate.permission_surface_digest
        )

    def _authenticate_v2_candidate_evidence_uncommitted(
        self, *, root_task_id: str, manager_agent: str, manager_session_id: str,
        result_id: int, origin_boot_id: str, owner_attempt_id: str,
        max_revise_rounds: int = 0,
    ) -> tuple[str | None, dict | None]:
        """Re-read and authenticate the complete claim-stage K/P evidence.

        Shared by the claim-audit boundary and every C3c evaluation/consumption
        boundary: the causal result row/body, the immutable launch binding, the
        authenticated pinned release/activation/selector prefix, the single a0
        admitted audit, the current task ownership/cancellation, the retained
        current mechanical eligibility AND the full candidate/pin cross-row
        joins with the frozen claim-time permission evidence.  The schema
        values remain immutable observations but are never compared.  A
        between-stage mutation, deletion or mixed identity refuses; the
        already-persisted K/P row is never trusted on its own.
        """
        code, ctx = self._authenticate_v2_claim_evidence_uncommitted(
            root_task_id=root_task_id, manager_agent=manager_agent,
            manager_session_id=manager_session_id, result_id=result_id,
            origin_boot_id=origin_boot_id, owner_attempt_id=owner_attempt_id,
            max_revise_rounds=max_revise_rounds,
        )
        if code is not None:
            return code, None
        assert ctx is not None
        attempt = ctx["attempt"]
        binding = ctx["binding"]
        release = ctx["release"]

        candidate_row = self._conn.execute(
            """SELECT * FROM authority_policy_v2_candidates
               WHERE root_task_id=? AND manager_agent=?
                 AND manager_session_id=? AND result_id=?""",
            (root_task_id, manager_agent, manager_session_id, result_id),
        ).fetchone()
        if candidate_row is None:
            return "claim_audit_missing", None
        try:
            candidate = self._authority_policy_v2_candidate_from_row(candidate_row)
        except ValueError:
            return "identity_mismatch", None
        pin_row = self._conn.execute(
            "SELECT * FROM authority_policy_v2_pins WHERE candidate_id=?",
            (candidate.candidate_id,),
        ).fetchone()
        if pin_row is None:
            return "claim_audit_missing", None
        try:
            pin = self._authority_policy_v2_pin_from_row(pin_row)
        except ValueError:
            return "identity_mismatch", None

        if not self._authenticate_v2_candidate_pin_joins_uncommitted(
            attempt=attempt, candidate=candidate, pin=pin, binding=binding,
            release=release,
        ):
            return "identity_mismatch", None

        from runtime.models import (
            AuthorityPolicyV2PermissionSurface as _PermissionSurface,
        )
        from runtime.orchestrator.authority import (
            V2_PERMISSION_SURFACE_CONTRACT,
            recheck_authority_policy_v2_permission_surface,
        )
        frozen_permission = _PermissionSurface(
            contract_version=V2_PERMISSION_SURFACE_CONTRACT,
            digest=candidate.permission_surface_digest,
        )
        if not recheck_authority_policy_v2_permission_surface(
            frozen_permission, self, manager_agent,
        ):
            return "evidence_drift", None
        return None, {**ctx, "candidate": candidate, "pin": pin}

    def _authenticate_v2_candidate_audit_uncommitted(
        self, candidate: AuthorityPolicyV2Candidate, event: str,
    ) -> bool:
        """Require exactly one authentic closed candidate-audit event.

        The event must carry the candidate/attempt/result/owner/boot identity
        and a canonical payload that re-validates to the same closed event.  A
        missing, duplicated, mutated or mismatched event refuses; a later
        legitimate event on the same candidate never invalidates an earlier one.
        """
        rows = self._conn.execute(
            """SELECT * FROM authority_policy_v2_candidate_audit
               WHERE candidate_id=? AND event=?""",
            (candidate.candidate_id, event),
        ).fetchall()
        if len(rows) != 1:
            return False
        row = rows[0]
        try:
            audit = AuthorityPolicyV2CandidateAudit.model_validate_json(
                row["canonical_payload_json"]
            )
        except Exception:
            return False
        # Every closed event field must equal the persisted column (identity,
        # event, candidate, attempt/result/owner/boot AND the created_at
        # preimage), the stored payload must be the canonical bytes of exactly
        # that value, and the event must belong to THIS candidate/event.  A
        # mutated column, a foreign candidate/identity or a malformed/extra-key
        # payload refuses.
        snapshot = audit.model_dump(mode="json")
        for column, value in snapshot.items():
            if row[column] != value:
                return False
        try:
            canonical = authority_policy_v2_canonical_json_bytes(snapshot).decode("utf-8")
        except Exception:
            return False
        if canonical != row["canonical_payload_json"]:
            return False
        if (
            audit.candidate_id != candidate.candidate_id
            or audit.event != event
            or audit.claim_key != candidate.claim_key
            or audit.attempt_id != candidate.attempt_id
            or audit.result_id != candidate.result_id
            or audit.owner_attempt_id != candidate.owner_attempt_id
            or audit.origin_boot_id != candidate.origin_boot_id
            or audit.team != candidate.team
            or audit.root_task_id != candidate.root_task_id
            or audit.manager_agent != candidate.manager_agent
            or audit.manager_session_id != candidate.manager_session_id
        ):
            return False
        return True

    def _insert_v2_candidate_audit_uncommitted(
        self, candidate: AuthorityPolicyV2Candidate, event: str, *,
        owner_attempt_id: str, origin_boot_id: str, now: str,
    ) -> None:
        """Append exactly one closed candidate-stage audit event (uncommitted)."""
        audit = AuthorityPolicyV2CandidateAudit(
            candidate_id=candidate.candidate_id,
            team=candidate.team,
            root_task_id=candidate.root_task_id,
            manager_agent=candidate.manager_agent,
            manager_session_id=candidate.manager_session_id,
            event=event,
            claim_key=candidate.claim_key,
            attempt_id=candidate.attempt_id,
            result_id=candidate.result_id,
            owner_attempt_id=owner_attempt_id,
            origin_boot_id=origin_boot_id,
        )
        audit_snapshot = audit.model_dump(mode="json")
        self._conn.execute(
            """INSERT INTO authority_policy_v2_candidate_audit
               (candidate_id, team, root_task_id, manager_agent, manager_session_id,
                event, claim_key, attempt_id, result_id, owner_attempt_id,
                origin_boot_id, canonical_payload_json, created_at)
               VALUES (:candidate_id,:team,:root_task_id,:manager_agent,
                       :manager_session_id,:event,:claim_key,:attempt_id,:result_id,
                       :owner_attempt_id,:origin_boot_id,:canonical_payload_json,
                       :created_at)""",
            {
                **audit_snapshot,
                "canonical_payload_json": authority_policy_v2_canonical_json_bytes(
                    audit_snapshot
                ).decode("utf-8"),
            },
        )

    def _advance_v2_candidate_lifecycle_uncommitted(
        self, candidate: AuthorityPolicyV2Candidate, new_stage: str,
    ) -> None:
        """Advance the candidate lifecycle in ONE consistent update."""
        expected = AUTHORITY_POLICY_V2_CANDIDATE_LIFECYCLE_TRANSITIONS.get(
            candidate.lifecycle_stage
        )
        if expected != new_stage:
            raise ValueError("illegal v2 candidate lifecycle transition")
        snapshot = candidate.model_dump(mode="json")
        snapshot["lifecycle_stage"] = new_stage
        self._conn.execute(
            """UPDATE authority_policy_v2_candidates
                  SET lifecycle_stage=?, canonical_payload_json=?
                WHERE candidate_id=?""",
            (
                new_stage,
                authority_policy_v2_canonical_json_bytes(snapshot).decode("utf-8"),
                candidate.candidate_id,
            ),
        )

    def _authority_policy_v2_evaluation_from_row(
        self, row,
    ) -> AuthorityPolicyV2Evaluation:
        try:
            evaluation = AuthorityPolicyV2Evaluation.model_validate_json(
                row["canonical_payload_json"]
            )
        except Exception as exc:
            raise ValueError("authority v2 evaluation has a corrupt canonical payload") from exc
        if evaluation.evaluation_id != row["evaluation_id"]:
            raise ValueError("authority v2 evaluation identity mismatch")
        for column, value in evaluation.model_dump(mode="json").items():
            if row[column] != value:
                raise ValueError("authority v2 evaluation column/preimage mismatch")
        return evaluation

    def _authenticate_v2_evaluation_evidence_uncommitted(
        self, *, candidate: AuthorityPolicyV2Candidate,
        attempt: AuthorityPolicyV2Attempt, binding, release,
    ) -> tuple[str | None, dict | None]:
        """Authenticate the stored V evidence WITHOUT re-deriving the outcome.

        Re-reads the immutable evaluation row and checks its canonical
        column/preimage consistency plus its full joins to the authenticated
        candidate/attempt/binding/release.  The outcome is never recomputed here.
        """
        row = self._conn.execute(
            "SELECT * FROM authority_policy_v2_evaluations WHERE candidate_id=?",
            (candidate.candidate_id,),
        ).fetchone()
        if row is None:
            return "evaluation_missing", None
        try:
            evaluation = self._authority_policy_v2_evaluation_from_row(row)
        except ValueError:
            return "identity_mismatch", None
        if (
            evaluation.candidate_id != candidate.candidate_id
            or evaluation.claim_key != candidate.claim_key
            or evaluation.team != attempt.team
            or evaluation.root_task_id != candidate.root_task_id
            or evaluation.manager_agent != candidate.manager_agent
            or evaluation.manager_session_id != candidate.manager_session_id
            or evaluation.attempt_id != attempt.attempt_id
            or evaluation.result_id != candidate.result_id
            or evaluation.binding_id != attempt.binding_id
            or evaluation.release_id != candidate.release_id
            or evaluation.activation_id != attempt.activation_id
            or evaluation.activation_epoch != attempt.activation_epoch
            or evaluation.selector_id != candidate.selector_id
            or evaluation.policy_version != release.version
            or evaluation.policy_digest != release.policy_digest
            or evaluation.contract_digest != attempt.contract_digest
            or evaluation.provider_id != binding.provider_id
            or evaluation.executor_kind != binding.executor_kind
            or evaluation.model_id != binding.model_id
            or evaluation.assessment_digest != attempt.assessment_digest
        ):
            return "identity_mismatch", None
        return None, {"evaluation": evaluation}

    def _audit_authority_policy_v2_candidate_claim_uncommitted(
        self, *, root_task_id: str, manager_agent: str, manager_session_id: str,
        result_id: int, origin_boot_id: str, owner_attempt_id: str, now: str,
        max_revise_rounds: int = 0,
    ) -> AuthorityPolicyV2StageOutcome:
        attempt_id = self._authority_policy_v2_attempt_id_for_identity(
            root_task_id=root_task_id, manager_agent=manager_agent,
            manager_session_id=manager_session_id, result_id=result_id,
        )

        def _refused(code: str, candidate_id: str | None = None) -> AuthorityPolicyV2StageOutcome:
            return AuthorityPolicyV2StageOutcome(
                status="refused", refusal_code=code, attempt_id=attempt_id,
                candidate_id=candidate_id,
            )

        # Re-authenticate the complete claim-stage evidence at the SECOND
        # boundary: the causal result row/body, the immutable launch binding,
        # the authenticated pinned release/activation/selector prefix, the
        # single a0 admitted audit, the current task ownership/cancellation AND
        # the full K/P cross-row joins, including the immutable claim-time
        # schema observation columns, plus the frozen permission evidence.
        # Permission evidence is rechecked; schema values are observed-only
        # and are never compared or rechecked.  The already-persisted K/P row
        # is NOT trusted on its own; a between-stage mutation/deletion/mixed
        # identity refuses without inventing evidence or advancing J.
        code, ctx = self._authenticate_v2_candidate_evidence_uncommitted(
            root_task_id=root_task_id, manager_agent=manager_agent,
            manager_session_id=manager_session_id, result_id=result_id,
            origin_boot_id=origin_boot_id, owner_attempt_id=owner_attempt_id,
            max_revise_rounds=max_revise_rounds,
        )
        if code is not None:
            return _refused(code)
        assert ctx is not None
        attempt = ctx["attempt"]
        candidate = ctx["candidate"]
        if attempt.stage == AUTHORITY_POLICY_V2_ATTEMPT_STAGE_CLAIM_AUDITED:
            return _refused("already_audited")
        if attempt.stage != AUTHORITY_POLICY_V2_ATTEMPT_STAGE_CLAIMED:
            return _refused("claim_audit_missing")

        existing_audit = self._conn.execute(
            """SELECT 1 FROM authority_policy_v2_candidate_audit
               WHERE candidate_id=? AND event=?""",
            (candidate.candidate_id, AUTHORITY_POLICY_V2_CANDIDATE_AUDIT_EVENT_CLAIMED),
        ).fetchone()
        if existing_audit is not None:
            return _refused("already_audited", candidate.candidate_id)

        audit = AuthorityPolicyV2CandidateAudit(
            candidate_id=candidate.candidate_id,
            team=candidate.team,
            root_task_id=root_task_id,
            manager_agent=manager_agent,
            manager_session_id=manager_session_id,
            event=AUTHORITY_POLICY_V2_CANDIDATE_AUDIT_EVENT_CLAIMED,
            claim_key=candidate.claim_key,
            attempt_id=attempt.attempt_id,
            result_id=result_id,
            owner_attempt_id=owner_attempt_id,
            origin_boot_id=origin_boot_id,
        )
        audit_snapshot = audit.model_dump(mode="json")
        self._conn.execute(
            """INSERT INTO authority_policy_v2_candidate_audit
               (candidate_id, team, root_task_id, manager_agent, manager_session_id,
                event, claim_key, attempt_id, result_id, owner_attempt_id,
                origin_boot_id, canonical_payload_json, created_at)
               VALUES (:candidate_id,:team,:root_task_id,:manager_agent,
                       :manager_session_id,:event,:claim_key,:attempt_id,:result_id,
                       :owner_attempt_id,:origin_boot_id,:canonical_payload_json,
                       :created_at)""",
            {
                **audit_snapshot,
                "canonical_payload_json": authority_policy_v2_canonical_json_bytes(
                    audit_snapshot
                ).decode("utf-8"),
            },
        )
        self.insert_audit_log_uncommitted(
            root_task_id, manager_agent, AUTHORITY_POLICY_V2_RESULT_STAGE_ACTION,
            {
                "stage": AUTHORITY_POLICY_V2_ATTEMPT_STAGE_CLAIM_AUDITED,
                "attempt_id": attempt.attempt_id,
                "candidate_id": candidate.candidate_id,
                "result_id": result_id,
                "binding_id": attempt.binding_id,
                "contract_id": attempt.contract_id,
                "contract_version": attempt.contract_version,
                "contract_digest": attempt.contract_digest,
                "release_id": attempt.release_id,
                "activation_id": attempt.activation_id,
                "activation_epoch": attempt.activation_epoch,
                "selector_id": attempt.selector_id,
                "assessment_digest": attempt.assessment_digest,
                "owner_attempt_id": owner_attempt_id,
                "origin_boot_id": origin_boot_id,
                "finalization_state": "unfinalized",
            },
        )
        self._advance_v2_attempt_stage_uncommitted(
            attempt, AUTHORITY_POLICY_V2_ATTEMPT_STAGE_CLAIM_AUDITED,
        )
        return AuthorityPolicyV2StageOutcome(
            status="claim_audited", attempt_id=attempt.attempt_id,
            candidate_id=candidate.candidate_id, claim_key=candidate.claim_key,
            stage=AUTHORITY_POLICY_V2_ATTEMPT_STAGE_CLAIM_AUDITED,
        )

    @_synchronized
    def audit_authority_policy_v2_candidate_claim(
        self, *, root_task_id: str, manager_agent: str, manager_session_id: str,
        result_id: int, origin_boot_id: str, owner_attempt_id: str,
        max_revise_rounds: int = 0,
    ) -> AuthorityPolicyV2StageOutcome:
        """Second C3b transaction: append the a1 claim event and audited stage.

        Re-authenticates the complete evidence (result row/body, immutable
        binding, authenticated pinned release/activation/selector prefix, K/P/J
        joins, prior a0, task ownership/cancellation), including the immutable
        claim-time schema observation values.  It rechecks only the frozen
        permission evidence; schema values are never compared or rechecked.
        Inserts exactly one candidate claim event plus the required
        ``claim_audited`` result-stage evidence and advances J to
        ``claim_audited`` atomically.  A failure preserves the claimed K/P.

        Transaction ownership: when the caller ALREADY owns a transaction this
        method refuses with ``transaction_owned`` BEFORE it would BEGIN,
        ROLLBACK or invalidate the live owner, so the caller's transaction and
        its work are left untouched.
        """
        attempt_id = self._authority_policy_v2_attempt_id_for_identity(
            root_task_id=root_task_id, manager_agent=manager_agent,
            manager_session_id=manager_session_id, result_id=result_id,
        )
        if self._conn.in_transaction:
            return self._refuse_v2_stage(
                attempt_id=attempt_id, owner_attempt_id=owner_attempt_id,
                code="transaction_owned",
                stage=AUTHORITY_POLICY_V2_ATTEMPT_STAGE_CLAIMED, poison=False,
            )
        now = _now().isoformat()
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            outcome = self._audit_authority_policy_v2_candidate_claim_uncommitted(
                root_task_id=root_task_id, manager_agent=manager_agent,
                manager_session_id=manager_session_id, result_id=result_id,
                origin_boot_id=origin_boot_id, owner_attempt_id=owner_attempt_id,
                now=now, max_revise_rounds=max_revise_rounds,
            )
            if outcome.status == "refused":
                self._conn.rollback()
                return self._refuse_v2_stage(
                    attempt_id=attempt_id, owner_attempt_id=owner_attempt_id,
                    code=outcome.refusal_code or "claim_audit_missing",
                    candidate_id=outcome.candidate_id,
                    stage=AUTHORITY_POLICY_V2_ATTEMPT_STAGE_CLAIMED,
                    origin_boot_id=origin_boot_id,
                    poison=False if outcome.refusal_code in {
                        "already_audited", "already_claimed",
                    } else None,
                )
            self._conn.commit()
            return outcome
        except Exception:
            self._conn.rollback()
            self._refuse_v2_stage(
                attempt_id=attempt_id, owner_attempt_id=owner_attempt_id,
                code="claim_audit_missing",
                stage=AUTHORITY_POLICY_V2_ATTEMPT_STAGE_CLAIMED,
                origin_boot_id=origin_boot_id,
            )
            raise

    # -- THR-229 checkpoint C3c: the four pre-final evaluation and single
    # consumption stages.  Each public method owns exactly ONE BEGIN
    # IMMEDIATE/commit/rollback and re-authenticates the complete prior evidence
    # (result/body, immutable binding, pinned history, K/P, required a0+a1 and,
    # from consumption onward, the stored V and a2) rather than trusting the
    # already-persisted rows.  The pure outcome is derived once, in the
    # evaluation transaction only; audit/consumption authenticate the canonical
    # stored evidence and never re-derive it.

    def _evaluate_authority_policy_v2_candidate_uncommitted(
        self, *, root_task_id: str, manager_agent: str, manager_session_id: str,
        result_id: int, origin_boot_id: str, owner_attempt_id: str, now: str,
        max_revise_rounds: int = 0,
    ) -> AuthorityPolicyV2StageOutcome:
        from runtime.models import AuthorityPolicyV2ManagerSelfEvaluation
        from runtime.orchestrator.authority_policy import (
            authority_policy_v2_persisted_assessment_outcome,
        )

        attempt_id = self._authority_policy_v2_attempt_id_for_identity(
            root_task_id=root_task_id, manager_agent=manager_agent,
            manager_session_id=manager_session_id, result_id=result_id,
        )

        def _refused(code: str, candidate_id: str | None = None) -> AuthorityPolicyV2StageOutcome:
            return AuthorityPolicyV2StageOutcome(
                status="refused", refusal_code=code, attempt_id=attempt_id,
                candidate_id=candidate_id,
            )

        code, ctx = self._authenticate_v2_candidate_evidence_uncommitted(
            root_task_id=root_task_id, manager_agent=manager_agent,
            manager_session_id=manager_session_id, result_id=result_id,
            origin_boot_id=origin_boot_id, owner_attempt_id=owner_attempt_id,
            max_revise_rounds=max_revise_rounds,
        )
        if code is not None:
            return _refused(code)
        assert ctx is not None
        attempt = ctx["attempt"]
        binding = ctx["binding"]
        release = ctx["release"]
        candidate = ctx["candidate"]
        if candidate.lifecycle_stage != AUTHORITY_POLICY_V2_CANDIDATE_LIFECYCLE_CREATED:
            return _refused("already_evaluated", candidate.candidate_id)
        if attempt.stage != AUTHORITY_POLICY_V2_ATTEMPT_STAGE_CLAIM_AUDITED:
            if attempt.stage in (
                AUTHORITY_POLICY_V2_ATTEMPT_STAGE_EVALUATED,
                AUTHORITY_POLICY_V2_ATTEMPT_STAGE_EVALUATION_AUDITED,
                AUTHORITY_POLICY_V2_ATTEMPT_STAGE_CONSUMED,
                AUTHORITY_POLICY_V2_ATTEMPT_STAGE_CONSUMED_AUDITED,
            ):
                return _refused("already_evaluated", candidate.candidate_id)
            return _refused("claim_audit_missing", candidate.candidate_id)
        # BOTH halves of the required prior stage: the candidate ``claimed``
        # event AND its sibling closed ``claim_audited`` result-stage audit.
        # Neither the audit created by this transaction nor any later/not-yet
        # created stage is required.
        if not self._authenticate_v2_candidate_audit_uncommitted(
            candidate, AUTHORITY_POLICY_V2_CANDIDATE_AUDIT_EVENT_CLAIMED,
        ):
            return _refused("claim_audit_missing", candidate.candidate_id)
        if not self._authenticate_v2_prior_result_stages_uncommitted(
            ctx["row"],
            (AUTHORITY_POLICY_V2_ATTEMPT_STAGE_CLAIM_AUDITED,),
            candidate.candidate_id,
        ):
            return _refused("claim_audit_missing", candidate.candidate_id)

        try:
            parsed = json.loads(ctx["result_row"]["decision_json"])
        except Exception:
            return _refused("identity_mismatch", candidate.candidate_id)
        carrier = parsed.get("_manager_self_evaluation") if isinstance(parsed, dict) else None
        if not isinstance(carrier, dict):
            return _refused("identity_mismatch", candidate.candidate_id)

        what_json: str | None = None
        not_json: str | None = None
        if "_error_code" not in carrier:
            try:
                assessment = AuthorityPolicyV2ManagerSelfEvaluation.model_validate(carrier)
            except Exception:
                return _refused("identity_mismatch", candidate.candidate_id)
            if (
                assessment.root_task_id != root_task_id
                or assessment.manager_session_id != manager_session_id
                or assessment.release_id != attempt.release_id
                or assessment.policy_digest != release.policy_digest
                or assessment.policy_version != release.version
                or assessment.activation_id != attempt.activation_id
                or assessment.activation_epoch != attempt.activation_epoch
                or assessment.contract_id != attempt.contract_id
                or assessment.contract_version != attempt.contract_version
                or assessment.contract_digest != attempt.contract_digest
                or assessment.provider_id != binding.provider_id
                or assessment.executor_kind != binding.executor_kind
                or assessment.model_id != binding.model_id
            ):
                return _refused("identity_mismatch", candidate.candidate_id)
            what_json = authority_policy_v2_canonical_json_bytes(
                carrier["what_to_escalate"]
            ).decode("utf-8")
            not_json = authority_policy_v2_canonical_json_bytes(
                carrier["what_not_to_escalate"]
            ).decode("utf-8")

        # The pure accepted precedence is invoked exactly once, here.
        outcome, diagnostic = authority_policy_v2_persisted_assessment_outcome(carrier)
        evaluation = AuthorityPolicyV2Evaluation(
            evaluation_id=candidate.candidate_id,
            candidate_id=candidate.candidate_id,
            claim_key=candidate.claim_key,
            team=attempt.team,
            root_task_id=root_task_id,
            manager_agent=manager_agent,
            manager_session_id=manager_session_id,
            attempt_id=attempt.attempt_id,
            result_id=result_id,
            binding_id=attempt.binding_id,
            release_id=attempt.release_id,
            activation_id=attempt.activation_id,
            activation_epoch=attempt.activation_epoch,
            selector_id=attempt.selector_id,
            policy_version=release.version,
            policy_digest=release.policy_digest,
            contract_digest=attempt.contract_digest,
            provider_id=binding.provider_id,
            executor_kind=binding.executor_kind,
            model_id=binding.model_id,
            outcome=outcome.value,
            assessment_digest=attempt.assessment_digest,
            diagnostic_code=diagnostic,
            what_to_escalate_json=what_json,
            what_not_to_escalate_json=not_json,
        )
        evaluation_snapshot = evaluation.model_dump(mode="json")
        self._conn.execute(
            """INSERT INTO authority_policy_v2_evaluations
               (evaluation_id, candidate_id, claim_key, team, root_task_id,
                manager_agent, manager_session_id, attempt_id, result_id,
                binding_id, release_id, activation_id, activation_epoch,
                selector_id, policy_version, policy_digest, contract_digest,
                provider_id, executor_kind, model_id, outcome, assessment_digest,
                diagnostic_code, what_to_escalate_json, what_not_to_escalate_json,
                canonical_payload_json, created_at)
               VALUES (:evaluation_id,:candidate_id,:claim_key,:team,:root_task_id,
                       :manager_agent,:manager_session_id,:attempt_id,:result_id,
                       :binding_id,:release_id,:activation_id,:activation_epoch,
                       :selector_id,:policy_version,:policy_digest,:contract_digest,
                       :provider_id,:executor_kind,:model_id,:outcome,
                       :assessment_digest,:diagnostic_code,:what_to_escalate_json,
                       :what_not_to_escalate_json,:canonical_payload_json,
                       :created_at)""",
            {
                **evaluation_snapshot,
                "canonical_payload_json": authority_policy_v2_canonical_json_bytes(
                    evaluation_snapshot
                ).decode("utf-8"),
            },
        )
        self._advance_v2_candidate_lifecycle_uncommitted(
            candidate, AUTHORITY_POLICY_V2_CANDIDATE_LIFECYCLE_EVALUATED,
        )
        self._advance_v2_attempt_stage_uncommitted(
            attempt, AUTHORITY_POLICY_V2_ATTEMPT_STAGE_EVALUATED,
        )
        return AuthorityPolicyV2StageOutcome(
            status="evaluated", attempt_id=attempt.attempt_id,
            candidate_id=candidate.candidate_id, claim_key=candidate.claim_key,
            stage=AUTHORITY_POLICY_V2_ATTEMPT_STAGE_EVALUATED,
        )

    @_synchronized
    def evaluate_authority_policy_v2_candidate(
        self, *, root_task_id: str, manager_agent: str, manager_session_id: str,
        result_id: int, origin_boot_id: str, owner_attempt_id: str,
        max_revise_rounds: int = 0,
    ) -> AuthorityPolicyV2StageOutcome:
        """C3c first transaction: persist one V, advance K/J to ``evaluated``.

        Derives the clause-free outcome exactly once from the persisted
        sanitized assessment; a missing/null/malformed/mismatched diagnostic
        carrier is retained honestly with the fail-closed ``invalid`` outcome.
        The task and recovery receipt are unchanged, and no envelope,
        notification or dispatch is created.
        """
        attempt_id = self._authority_policy_v2_attempt_id_for_identity(
            root_task_id=root_task_id, manager_agent=manager_agent,
            manager_session_id=manager_session_id, result_id=result_id,
        )
        if self._conn.in_transaction:
            return self._refuse_v2_stage(
                attempt_id=attempt_id, owner_attempt_id=owner_attempt_id,
                code="transaction_owned",
                stage=AUTHORITY_POLICY_V2_ATTEMPT_STAGE_CLAIM_AUDITED, poison=False,
            )
        now = _now().isoformat()
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            outcome = self._evaluate_authority_policy_v2_candidate_uncommitted(
                root_task_id=root_task_id, manager_agent=manager_agent,
                manager_session_id=manager_session_id, result_id=result_id,
                origin_boot_id=origin_boot_id, owner_attempt_id=owner_attempt_id,
                now=now, max_revise_rounds=max_revise_rounds,
            )
            if outcome.status == "refused":
                self._conn.rollback()
                return self._refuse_v2_stage(
                    attempt_id=attempt_id, owner_attempt_id=owner_attempt_id,
                    code=outcome.refusal_code or "evaluation_failed",
                    candidate_id=outcome.candidate_id,
                    stage=AUTHORITY_POLICY_V2_ATTEMPT_STAGE_CLAIM_AUDITED,
                    origin_boot_id=origin_boot_id,
                    poison=False if outcome.refusal_code in {
                        "already_evaluated", "already_consumed",
                    } else None,
                )
            self._conn.commit()
            return outcome
        except Exception:
            self._conn.rollback()
            self._refuse_v2_stage(
                attempt_id=attempt_id, owner_attempt_id=owner_attempt_id,
                code="evaluation_failed",
                stage=AUTHORITY_POLICY_V2_ATTEMPT_STAGE_CLAIM_AUDITED,
                origin_boot_id=origin_boot_id,
            )
            raise

    def _audit_evaluation_uncommitted(
        self, *, root_task_id: str, manager_agent: str, manager_session_id: str,
        result_id: int, origin_boot_id: str, owner_attempt_id: str, now: str,
        max_revise_rounds: int = 0,
    ) -> AuthorityPolicyV2StageOutcome:
        attempt_id = self._authority_policy_v2_attempt_id_for_identity(
            root_task_id=root_task_id, manager_agent=manager_agent,
            manager_session_id=manager_session_id, result_id=result_id,
        )

        def _refused(code: str, candidate_id: str | None = None) -> AuthorityPolicyV2StageOutcome:
            return AuthorityPolicyV2StageOutcome(
                status="refused", refusal_code=code, attempt_id=attempt_id,
                candidate_id=candidate_id,
            )

        code, ctx = self._authenticate_v2_candidate_evidence_uncommitted(
            root_task_id=root_task_id, manager_agent=manager_agent,
            manager_session_id=manager_session_id, result_id=result_id,
            origin_boot_id=origin_boot_id, owner_attempt_id=owner_attempt_id,
            max_revise_rounds=max_revise_rounds,
        )
        if code is not None:
            return _refused(code)
        assert ctx is not None
        attempt = ctx["attempt"]
        binding = ctx["binding"]
        release = ctx["release"]
        candidate = ctx["candidate"]
        if attempt.stage in (
            AUTHORITY_POLICY_V2_ATTEMPT_STAGE_EVALUATION_AUDITED,
            AUTHORITY_POLICY_V2_ATTEMPT_STAGE_CONSUMED,
            AUTHORITY_POLICY_V2_ATTEMPT_STAGE_CONSUMED_AUDITED,
        ):
            return _refused("already_audited", candidate.candidate_id)
        if attempt.stage != AUTHORITY_POLICY_V2_ATTEMPT_STAGE_EVALUATED:
            return _refused("evaluation_missing", candidate.candidate_id)
        if candidate.lifecycle_stage != AUTHORITY_POLICY_V2_CANDIDATE_LIFECYCLE_EVALUATED:
            return _refused("identity_mismatch", candidate.candidate_id)
        # BOTH halves of the required prior stage: the candidate ``claimed``
        # event AND its closed ``claim_audited`` result-stage audit.  The
        # ``evaluation_audited`` audit created by THIS transaction is not
        # required.
        if not self._authenticate_v2_candidate_audit_uncommitted(
            candidate, AUTHORITY_POLICY_V2_CANDIDATE_AUDIT_EVENT_CLAIMED,
        ):
            return _refused("claim_audit_missing", candidate.candidate_id)
        if not self._authenticate_v2_prior_result_stages_uncommitted(
            ctx["row"],
            (AUTHORITY_POLICY_V2_ATTEMPT_STAGE_CLAIM_AUDITED,),
            candidate.candidate_id,
        ):
            return _refused("claim_audit_missing", candidate.candidate_id)
        code, _ = self._authenticate_v2_evaluation_evidence_uncommitted(
            candidate=candidate, attempt=attempt, binding=binding, release=release,
        )
        if code is not None:
            return _refused(code, candidate.candidate_id)
        existing = self._conn.execute(
            """SELECT 1 FROM authority_policy_v2_candidate_audit
               WHERE candidate_id=? AND event=?""",
            (candidate.candidate_id, AUTHORITY_POLICY_V2_CANDIDATE_AUDIT_EVENT_EVALUATED),
        ).fetchone()
        if existing is not None:
            return _refused("already_audited", candidate.candidate_id)
        self._insert_v2_candidate_audit_uncommitted(
            candidate, AUTHORITY_POLICY_V2_CANDIDATE_AUDIT_EVENT_EVALUATED,
            owner_attempt_id=owner_attempt_id, origin_boot_id=origin_boot_id, now=now,
        )
        self.insert_audit_log_uncommitted(
            root_task_id, manager_agent, AUTHORITY_POLICY_V2_RESULT_STAGE_ACTION,
            {
                "stage": AUTHORITY_POLICY_V2_ATTEMPT_STAGE_EVALUATION_AUDITED,
                "attempt_id": attempt.attempt_id,
                "candidate_id": candidate.candidate_id,
                "result_id": result_id,
                "binding_id": attempt.binding_id,
                "contract_id": attempt.contract_id,
                "contract_version": attempt.contract_version,
                "contract_digest": attempt.contract_digest,
                "release_id": attempt.release_id,
                "activation_id": attempt.activation_id,
                "activation_epoch": attempt.activation_epoch,
                "selector_id": attempt.selector_id,
                "assessment_digest": attempt.assessment_digest,
                "owner_attempt_id": owner_attempt_id,
                "origin_boot_id": origin_boot_id,
                "finalization_state": "unfinalized",
            },
        )
        self._advance_v2_attempt_stage_uncommitted(
            attempt, AUTHORITY_POLICY_V2_ATTEMPT_STAGE_EVALUATION_AUDITED,
        )
        return AuthorityPolicyV2StageOutcome(
            status="evaluation_audited", attempt_id=attempt.attempt_id,
            candidate_id=candidate.candidate_id, claim_key=candidate.claim_key,
            stage=AUTHORITY_POLICY_V2_ATTEMPT_STAGE_EVALUATION_AUDITED,
        )

    @_synchronized
    def audit_authority_policy_v2_candidate_evaluation(
        self, *, root_task_id: str, manager_agent: str, manager_session_id: str,
        result_id: int, origin_boot_id: str, owner_attempt_id: str,
        max_revise_rounds: int = 0,
    ) -> AuthorityPolicyV2StageOutcome:
        """C3c second transaction: append a2 plus the audited stage.

        Re-authenticates the complete prior evidence INCLUDING the stored V and
        a0+a1, then appends exactly one candidate ``evaluated`` event plus the
        ``evaluation_audited`` result-stage evidence and advances J.  It never
        re-derives the outcome to authenticate V.  A failure preserves V and the
        evaluated K.
        """
        attempt_id = self._authority_policy_v2_attempt_id_for_identity(
            root_task_id=root_task_id, manager_agent=manager_agent,
            manager_session_id=manager_session_id, result_id=result_id,
        )
        if self._conn.in_transaction:
            return self._refuse_v2_stage(
                attempt_id=attempt_id, owner_attempt_id=owner_attempt_id,
                code="transaction_owned",
                stage=AUTHORITY_POLICY_V2_ATTEMPT_STAGE_EVALUATED, poison=False,
            )
        now = _now().isoformat()
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            outcome = self._audit_evaluation_uncommitted(
                root_task_id=root_task_id, manager_agent=manager_agent,
                manager_session_id=manager_session_id, result_id=result_id,
                origin_boot_id=origin_boot_id, owner_attempt_id=owner_attempt_id,
                now=now, max_revise_rounds=max_revise_rounds,
            )
            if outcome.status == "refused":
                self._conn.rollback()
                return self._refuse_v2_stage(
                    attempt_id=attempt_id, owner_attempt_id=owner_attempt_id,
                    code=outcome.refusal_code or "evaluation_audit_missing",
                    candidate_id=outcome.candidate_id,
                    stage=AUTHORITY_POLICY_V2_ATTEMPT_STAGE_EVALUATED,
                    origin_boot_id=origin_boot_id,
                    poison=False if outcome.refusal_code in {
                        "already_audited", "already_consumed",
                    } else None,
                )
            self._conn.commit()
            return outcome
        except Exception:
            self._conn.rollback()
            self._refuse_v2_stage(
                attempt_id=attempt_id, owner_attempt_id=owner_attempt_id,
                code="evaluation_audit_missing",
                stage=AUTHORITY_POLICY_V2_ATTEMPT_STAGE_EVALUATED,
                origin_boot_id=origin_boot_id,
            )
            raise

    def _consume_authority_policy_v2_candidate_uncommitted(
        self, *, root_task_id: str, manager_agent: str, manager_session_id: str,
        result_id: int, origin_boot_id: str, owner_attempt_id: str,
        max_revise_rounds: int = 0,
    ) -> AuthorityPolicyV2StageOutcome:
        attempt_id = self._authority_policy_v2_attempt_id_for_identity(
            root_task_id=root_task_id, manager_agent=manager_agent,
            manager_session_id=manager_session_id, result_id=result_id,
        )

        def _refused(code: str, candidate_id: str | None = None) -> AuthorityPolicyV2StageOutcome:
            return AuthorityPolicyV2StageOutcome(
                status="refused", refusal_code=code, attempt_id=attempt_id,
                candidate_id=candidate_id,
            )

        code, ctx = self._authenticate_v2_candidate_evidence_uncommitted(
            root_task_id=root_task_id, manager_agent=manager_agent,
            manager_session_id=manager_session_id, result_id=result_id,
            origin_boot_id=origin_boot_id, owner_attempt_id=owner_attempt_id,
            max_revise_rounds=max_revise_rounds,
        )
        if code is not None:
            return _refused(code)
        assert ctx is not None
        attempt = ctx["attempt"]
        binding = ctx["binding"]
        release = ctx["release"]
        candidate = ctx["candidate"]
        if (
            candidate.lifecycle_stage == AUTHORITY_POLICY_V2_CANDIDATE_LIFECYCLE_CONSUMED
            or attempt.stage == AUTHORITY_POLICY_V2_ATTEMPT_STAGE_CONSUMED_AUDITED
        ):
            return _refused("already_consumed", candidate.candidate_id)
        if attempt.stage != AUTHORITY_POLICY_V2_ATTEMPT_STAGE_EVALUATION_AUDITED:
            return _refused("evaluation_audit_missing", candidate.candidate_id)
        if candidate.lifecycle_stage != AUTHORITY_POLICY_V2_CANDIDATE_LIFECYCLE_EVALUATED:
            return _refused("identity_mismatch", candidate.candidate_id)
        # BOTH halves of every required prior stage: the candidate
        # ``claimed``/``evaluated`` events AND their closed ``claim_audited`` /
        # ``evaluation_audited`` result-stage audits.
        if not self._authenticate_v2_candidate_audit_uncommitted(
            candidate, AUTHORITY_POLICY_V2_CANDIDATE_AUDIT_EVENT_CLAIMED,
        ):
            return _refused("claim_audit_missing", candidate.candidate_id)
        if not self._authenticate_v2_candidate_audit_uncommitted(
            candidate, AUTHORITY_POLICY_V2_CANDIDATE_AUDIT_EVENT_EVALUATED,
        ):
            return _refused("evaluation_audit_missing", candidate.candidate_id)
        if not self._authenticate_v2_prior_result_stages_uncommitted(
            ctx["row"],
            (
                AUTHORITY_POLICY_V2_ATTEMPT_STAGE_CLAIM_AUDITED,
                AUTHORITY_POLICY_V2_ATTEMPT_STAGE_EVALUATION_AUDITED,
            ),
            candidate.candidate_id,
        ):
            return _refused("evaluation_audit_missing", candidate.candidate_id)
        code, _ = self._authenticate_v2_evaluation_evidence_uncommitted(
            candidate=candidate, attempt=attempt, binding=binding, release=release,
        )
        if code is not None:
            return _refused(code, candidate.candidate_id)
        self._advance_v2_candidate_lifecycle_uncommitted(
            candidate, AUTHORITY_POLICY_V2_CANDIDATE_LIFECYCLE_CONSUMED,
        )
        self._advance_v2_attempt_stage_uncommitted(
            attempt, AUTHORITY_POLICY_V2_ATTEMPT_STAGE_CONSUMED,
        )
        return AuthorityPolicyV2StageOutcome(
            status="consumed", attempt_id=attempt.attempt_id,
            candidate_id=candidate.candidate_id, claim_key=candidate.claim_key,
            stage=AUTHORITY_POLICY_V2_ATTEMPT_STAGE_CONSUMED,
        )

    @_synchronized
    def consume_authority_policy_v2_candidate(
        self, *, root_task_id: str, manager_agent: str, manager_session_id: str,
        result_id: int, origin_boot_id: str, owner_attempt_id: str,
        max_revise_rounds: int = 0,
    ) -> AuthorityPolicyV2StageOutcome:
        """C3c third transaction: CAS K/J to ``consumed`` exactly once.

        Re-authenticates the complete prior evidence and a0+a1+a2, requires the
        evaluated candidate and J ``evaluation_audited`` and consumes the
        persisted V with NO second model call and NO repeated pure derivation.
        It mints no authority and does not change the task.
        """
        attempt_id = self._authority_policy_v2_attempt_id_for_identity(
            root_task_id=root_task_id, manager_agent=manager_agent,
            manager_session_id=manager_session_id, result_id=result_id,
        )
        if self._conn.in_transaction:
            return self._refuse_v2_stage(
                attempt_id=attempt_id, owner_attempt_id=owner_attempt_id,
                code="transaction_owned",
                stage=AUTHORITY_POLICY_V2_ATTEMPT_STAGE_EVALUATION_AUDITED, poison=False,
            )
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            outcome = self._consume_authority_policy_v2_candidate_uncommitted(
                root_task_id=root_task_id, manager_agent=manager_agent,
                manager_session_id=manager_session_id, result_id=result_id,
                origin_boot_id=origin_boot_id, owner_attempt_id=owner_attempt_id,
                max_revise_rounds=max_revise_rounds,
            )
            if outcome.status == "refused":
                self._conn.rollback()
                return self._refuse_v2_stage(
                    attempt_id=attempt_id, owner_attempt_id=owner_attempt_id,
                    code=outcome.refusal_code or "consume_failed",
                    candidate_id=outcome.candidate_id,
                    stage=AUTHORITY_POLICY_V2_ATTEMPT_STAGE_EVALUATION_AUDITED,
                    origin_boot_id=origin_boot_id,
                    poison=False if outcome.refusal_code in {
                        "already_consumed",
                    } else None,
                )
            self._conn.commit()
            return outcome
        except Exception:
            self._conn.rollback()
            self._refuse_v2_stage(
                attempt_id=attempt_id, owner_attempt_id=owner_attempt_id,
                code="consume_failed",
                stage=AUTHORITY_POLICY_V2_ATTEMPT_STAGE_EVALUATION_AUDITED,
                origin_boot_id=origin_boot_id,
            )
            raise

    def _audit_consumption_uncommitted(
        self, *, root_task_id: str, manager_agent: str, manager_session_id: str,
        result_id: int, origin_boot_id: str, owner_attempt_id: str, now: str,
        max_revise_rounds: int = 0,
    ) -> AuthorityPolicyV2StageOutcome:
        attempt_id = self._authority_policy_v2_attempt_id_for_identity(
            root_task_id=root_task_id, manager_agent=manager_agent,
            manager_session_id=manager_session_id, result_id=result_id,
        )

        def _refused(code: str, candidate_id: str | None = None) -> AuthorityPolicyV2StageOutcome:
            return AuthorityPolicyV2StageOutcome(
                status="refused", refusal_code=code, attempt_id=attempt_id,
                candidate_id=candidate_id,
            )

        code, ctx = self._authenticate_v2_candidate_evidence_uncommitted(
            root_task_id=root_task_id, manager_agent=manager_agent,
            manager_session_id=manager_session_id, result_id=result_id,
            origin_boot_id=origin_boot_id, owner_attempt_id=owner_attempt_id,
            max_revise_rounds=max_revise_rounds,
        )
        if code is not None:
            return _refused(code)
        assert ctx is not None
        attempt = ctx["attempt"]
        binding = ctx["binding"]
        release = ctx["release"]
        candidate = ctx["candidate"]
        if attempt.stage == AUTHORITY_POLICY_V2_ATTEMPT_STAGE_CONSUMED_AUDITED:
            return _refused("already_audited", candidate.candidate_id)
        if attempt.stage != AUTHORITY_POLICY_V2_ATTEMPT_STAGE_CONSUMED:
            return _refused("consume_failed", candidate.candidate_id)
        if candidate.lifecycle_stage != AUTHORITY_POLICY_V2_CANDIDATE_LIFECYCLE_CONSUMED:
            return _refused("identity_mismatch", candidate.candidate_id)
        # BOTH halves of every required prior stage: the candidate
        # ``claimed``/``evaluated`` events AND their closed ``claim_audited`` /
        # ``evaluation_audited`` result-stage audits.  The ``consumed_audited``
        # audit created by THIS transaction is not required.
        if not self._authenticate_v2_candidate_audit_uncommitted(
            candidate, AUTHORITY_POLICY_V2_CANDIDATE_AUDIT_EVENT_CLAIMED,
        ):
            return _refused("claim_audit_missing", candidate.candidate_id)
        if not self._authenticate_v2_candidate_audit_uncommitted(
            candidate, AUTHORITY_POLICY_V2_CANDIDATE_AUDIT_EVENT_EVALUATED,
        ):
            return _refused("evaluation_audit_missing", candidate.candidate_id)
        if not self._authenticate_v2_prior_result_stages_uncommitted(
            ctx["row"],
            (
                AUTHORITY_POLICY_V2_ATTEMPT_STAGE_CLAIM_AUDITED,
                AUTHORITY_POLICY_V2_ATTEMPT_STAGE_EVALUATION_AUDITED,
            ),
            candidate.candidate_id,
        ):
            return _refused("evaluation_audit_missing", candidate.candidate_id)
        code, _ = self._authenticate_v2_evaluation_evidence_uncommitted(
            candidate=candidate, attempt=attempt, binding=binding, release=release,
        )
        if code is not None:
            return _refused(code, candidate.candidate_id)
        existing = self._conn.execute(
            """SELECT 1 FROM authority_policy_v2_candidate_audit
               WHERE candidate_id=? AND event=?""",
            (candidate.candidate_id, AUTHORITY_POLICY_V2_CANDIDATE_AUDIT_EVENT_CONSUMED),
        ).fetchone()
        if existing is not None:
            return _refused("already_audited", candidate.candidate_id)
        self._insert_v2_candidate_audit_uncommitted(
            candidate, AUTHORITY_POLICY_V2_CANDIDATE_AUDIT_EVENT_CONSUMED,
            owner_attempt_id=owner_attempt_id, origin_boot_id=origin_boot_id, now=now,
        )
        self.insert_audit_log_uncommitted(
            root_task_id, manager_agent, AUTHORITY_POLICY_V2_RESULT_STAGE_ACTION,
            {
                "stage": AUTHORITY_POLICY_V2_ATTEMPT_STAGE_CONSUMED_AUDITED,
                "attempt_id": attempt.attempt_id,
                "candidate_id": candidate.candidate_id,
                "result_id": result_id,
                "binding_id": attempt.binding_id,
                "contract_id": attempt.contract_id,
                "contract_version": attempt.contract_version,
                "contract_digest": attempt.contract_digest,
                "release_id": attempt.release_id,
                "activation_id": attempt.activation_id,
                "activation_epoch": attempt.activation_epoch,
                "selector_id": attempt.selector_id,
                "assessment_digest": attempt.assessment_digest,
                "owner_attempt_id": owner_attempt_id,
                "origin_boot_id": origin_boot_id,
                "finalization_state": "unfinalized",
            },
        )
        self._advance_v2_attempt_stage_uncommitted(
            attempt, AUTHORITY_POLICY_V2_ATTEMPT_STAGE_CONSUMED_AUDITED,
        )
        return AuthorityPolicyV2StageOutcome(
            status="consumed_audited", attempt_id=attempt.attempt_id,
            candidate_id=candidate.candidate_id, claim_key=candidate.claim_key,
            stage=AUTHORITY_POLICY_V2_ATTEMPT_STAGE_CONSUMED_AUDITED,
        )

    @_synchronized
    def audit_authority_policy_v2_candidate_consumption(
        self, *, root_task_id: str, manager_agent: str, manager_session_id: str,
        result_id: int, origin_boot_id: str, owner_attempt_id: str,
        max_revise_rounds: int = 0,
    ) -> AuthorityPolicyV2StageOutcome:
        """C3c fourth transaction: append a3 and advance J to consumed_audited.

        Re-authenticates the consumed K/P/V and a0+a1+a2, appends exactly one
        candidate ``consumed`` event plus the ``consumed_audited`` result-stage
        evidence and advances J in one transaction.  A failure preserves the
        earlier consumed K and V, leaves a3 absent and J consumed; the shipping
        hook remains fail-closed until the later finalization/refusal/recovery
        unit.
        """
        attempt_id = self._authority_policy_v2_attempt_id_for_identity(
            root_task_id=root_task_id, manager_agent=manager_agent,
            manager_session_id=manager_session_id, result_id=result_id,
        )
        if self._conn.in_transaction:
            return self._refuse_v2_stage(
                attempt_id=attempt_id, owner_attempt_id=owner_attempt_id,
                code="transaction_owned",
                stage=AUTHORITY_POLICY_V2_ATTEMPT_STAGE_CONSUMED, poison=False,
            )
        now = _now().isoformat()
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            outcome = self._audit_consumption_uncommitted(
                root_task_id=root_task_id, manager_agent=manager_agent,
                manager_session_id=manager_session_id, result_id=result_id,
                origin_boot_id=origin_boot_id, owner_attempt_id=owner_attempt_id,
                now=now, max_revise_rounds=max_revise_rounds,
            )
            if outcome.status == "refused":
                self._conn.rollback()
                return self._refuse_v2_stage(
                    attempt_id=attempt_id, owner_attempt_id=owner_attempt_id,
                    code=outcome.refusal_code or "consume_failed",
                    candidate_id=outcome.candidate_id,
                    stage=AUTHORITY_POLICY_V2_ATTEMPT_STAGE_CONSUMED,
                    origin_boot_id=origin_boot_id,
                    poison=False if outcome.refusal_code in {
                        "already_audited", "already_consumed",
                    } else None,
                )
            self._conn.commit()
            return outcome
        except Exception:
            self._conn.rollback()
            self._refuse_v2_stage(
                attempt_id=attempt_id, owner_attempt_id=owner_attempt_id,
                code="consume_failed",
                stage=AUTHORITY_POLICY_V2_ATTEMPT_STAGE_CONSUMED,
                origin_boot_id=origin_boot_id,
            )
            raise

    @_synchronized
    def get_authority_policy_v2_candidate(
        self, candidate_id: str,
    ) -> AuthorityPolicyV2Candidate | None:
        """Authenticated read of one persisted candidate (independent of claim)."""
        row = self._conn.execute(
            "SELECT * FROM authority_policy_v2_candidates WHERE candidate_id=?",
            (candidate_id,),
        ).fetchone()
        if row is None:
            return None
        try:
            return self._authority_policy_v2_candidate_from_row(row)
        except ValueError:
            return None

    @_synchronized
    def get_authority_policy_v2_candidate_for_result(
        self, result_id: int,
    ) -> AuthorityPolicyV2Candidate | None:
        row = self._conn.execute(
            "SELECT * FROM authority_policy_v2_candidates WHERE result_id=?",
            (result_id,),
        ).fetchone()
        if row is None:
            return None
        try:
            return self._authority_policy_v2_candidate_from_row(row)
        except ValueError:
            return None

    @_synchronized
    def get_authority_policy_v2_pin(
        self, candidate_id: str,
    ) -> AuthorityPolicyV2Pin | None:
        row = self._conn.execute(
            "SELECT * FROM authority_policy_v2_pins WHERE candidate_id=?",
            (candidate_id,),
        ).fetchone()
        if row is None:
            return None
        try:
            return self._authority_policy_v2_pin_from_row(row)
        except ValueError:
            return None

    @_synchronized
    def list_authority_policy_v2_candidate_audits(
        self, candidate_id: str,
    ) -> list[dict]:
        rows = self._conn.execute(
            """SELECT * FROM authority_policy_v2_candidate_audit
               WHERE candidate_id=? ORDER BY id""",
            (candidate_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    @_synchronized
    def get_authority_policy_v2_candidate_audit(
        self, candidate_id: str, event: str,
    ) -> dict | None:
        row = self._conn.execute(
            """SELECT * FROM authority_policy_v2_candidate_audit
               WHERE candidate_id=? AND event=?""",
            (candidate_id, event),
        ).fetchone()
        return None if row is None else dict(row)

    # -- THR-229 checkpoint C3c: authenticated V reads.  They authenticate the
    # canonical stored column/preimage evidence only; they never re-derive the
    # outcome and never authorize a stage advance.

    @_synchronized
    def get_authority_policy_v2_evaluation(
        self, candidate_id: str,
    ) -> AuthorityPolicyV2Evaluation | None:
        row = self._conn.execute(
            "SELECT * FROM authority_policy_v2_evaluations WHERE candidate_id=?",
            (candidate_id,),
        ).fetchone()
        if row is None:
            return None
        try:
            return self._authority_policy_v2_evaluation_from_row(row)
        except ValueError:
            return None

    @_synchronized
    def get_authority_policy_v2_evaluation_for_result(
        self, result_id: int,
    ) -> AuthorityPolicyV2Evaluation | None:
        row = self._conn.execute(
            "SELECT * FROM authority_policy_v2_evaluations WHERE result_id=?",
            (result_id,),
        ).fetchone()
        if row is None:
            return None
        try:
            return self._authority_policy_v2_evaluation_from_row(row)
        except ValueError:
            return None

    @_synchronized
    def list_authority_policy_v2_evaluations(
        self, *, root_task_id: str, manager_agent: str,
    ) -> list[AuthorityPolicyV2Evaluation]:
        rows = self._conn.execute(
            """SELECT * FROM authority_policy_v2_evaluations
               WHERE root_task_id=? AND manager_agent=?
               ORDER BY created_at, evaluation_id""",
            (root_task_id, manager_agent),
        ).fetchall()
        evaluations = []
        for row in rows:
            try:
                evaluations.append(self._authority_policy_v2_evaluation_from_row(row))
            except ValueError:
                continue
        return evaluations

    # -- THR-229 checkpoint C3d1: durable pre-final refusal and exact recovery
    # receipt housekeeping.  ONE Database-owned synchronized BEGIN IMMEDIATE
    # transaction over the exact attempt journal (J) / immutable causal result
    # (R), the current task and the optional exact recovery receipt (Q).  It
    # authenticates immutable OWNERSHIP evidence (attempt/result/binding
    # identity and the greatest durable stage) but never the failed CONTINUATION
    # evidence whose absence caused the refusal (missing stage audit, corrupt
    # assessment).  It mints no successor, envelope, notification or dispatch
    # generation and never touches an unrelated/mismatched receipt.  The store
    # is a thin forwarder; this method owns BEGIN/ROLLBACK/commit.

    def _v2_refusal_stage_payload(
        self, attempt_row: dict, candidate_id: str | None, refusal_code: str,
        finalization_state: str,
    ) -> dict:
        payload = {
            "stage": AUTHORITY_POLICY_V2_RESULT_STAGE_REFUSED,
            "attempt_id": attempt_row["attempt_id"],
            "result_id": attempt_row["result_id"],
            "binding_id": attempt_row["binding_id"],
            "contract_id": attempt_row["contract_id"],
            "contract_version": attempt_row["contract_version"],
            "contract_digest": attempt_row["contract_digest"],
            "release_id": attempt_row["release_id"],
            "activation_id": attempt_row["activation_id"],
            "activation_epoch": attempt_row["activation_epoch"],
            "selector_id": attempt_row["selector_id"],
            "assessment_digest": attempt_row["assessment_digest"],
            "owner_attempt_id": attempt_row["owner_attempt_id"],
            "origin_boot_id": attempt_row["origin_boot_id"],
            "refusal_code": refusal_code,
            "finalization_state": finalization_state,
        }
        if candidate_id is not None:
            payload["candidate_id"] = candidate_id
        return payload

    def _authenticate_v2_refusal_result_stage_uncommitted(
        self, attempt_row: dict, *, candidate_id: str | None, refusal_code: str,
        finalization_state: str,
    ) -> bool:
        """Require exactly one authentic closed terminal refusal stage event."""
        expected = self._v2_refusal_stage_payload(
            attempt_row, candidate_id, refusal_code, finalization_state,
        )
        try:
            candidates = [
                row for row in self.get_audit_logs(attempt_row["root_task_id"])
                if row.get("action") == AUTHORITY_POLICY_V2_RESULT_STAGE_ACTION
                and row.get("agent") == attempt_row["manager_agent"]
                and isinstance(row.get("payload"), dict)
                and row["payload"].get("attempt_id") == attempt_row["attempt_id"]
                and row["payload"].get("stage") == AUTHORITY_POLICY_V2_RESULT_STAGE_REFUSED
            ]
        except Exception:
            return False
        if len(candidates) != 1:
            return False
        payload = candidates[0]["payload"]
        if set(payload.keys()) != set(expected.keys()):
            return False
        return all(payload.get(key) == value for key, value in expected.items())

    def _v2_refusal_completion_payload(
        self, *, attempt_row: dict, refusal_code: str,
        recovery_session_id: str | None,
    ) -> dict:
        payload = {
            "attempt_id": attempt_row["attempt_id"],
            "result_id": attempt_row["result_id"],
            "refusal_code": refusal_code,
        }
        if recovery_session_id is not None:
            payload["_recovery_session_id"] = recovery_session_id
            payload["_result_row_id"] = attempt_row["result_id"]
        return payload

    def _authenticate_v2_refusal_completion_uncommitted(
        self, attempt_row: dict, *, refusal_code: str,
    ) -> tuple[bool, bool]:
        """Authenticate exactly one identity-scoped bounded refusal completion.

        Identity-scoped rows are enumerated FIRST (action/agent/attempt only),
        then the exact closed key set, types, values and cardinality are
        required.  A second ``completion_report`` for the same attempt with a
        DIFFERENT refusal code is a conflicting terminal-evidence row and
        refuses: mismatching codes/discriminator values are never filtered out
        of the uniqueness check.  Returns ``(authenticated, recovery_claimed)``
        where ``recovery_claimed`` says the stored evidence carries the exact
        recovery receipt fields.
        """
        try:
            candidates = [
                row for row in self.get_audit_logs(attempt_row["root_task_id"])
                if row.get("action") == "completion_report"
                and row.get("agent") == attempt_row["manager_agent"]
                and isinstance(row.get("payload"), dict)
                and row["payload"].get("attempt_id") == attempt_row["attempt_id"]
            ]
        except Exception:
            return False, False
        if len(candidates) != 1:
            return False, False
        payload = candidates[0]["payload"]
        base_keys = {"attempt_id", "result_id", "refusal_code"}
        recovery_keys = base_keys | {"_recovery_session_id", "_result_row_id"}
        keys = set(payload.keys())
        if keys != base_keys and keys != recovery_keys:
            return False, False
        if (
            not isinstance(payload.get("attempt_id"), str)
            or payload["attempt_id"] != attempt_row["attempt_id"]
        ):
            return False, False
        if (
            not isinstance(payload.get("result_id"), int)
            or isinstance(payload.get("result_id"), bool)
            or payload["result_id"] != attempt_row["result_id"]
        ):
            return False, False
        if (
            not isinstance(payload.get("refusal_code"), str)
            or payload["refusal_code"] != refusal_code
        ):
            return False, False
        if keys == recovery_keys:
            if (
                not isinstance(payload.get("_recovery_session_id"), str)
                or payload["_recovery_session_id"] != attempt_row["manager_session_id"]
            ):
                return False, False
            if (
                not isinstance(payload.get("_result_row_id"), int)
                or isinstance(payload.get("_result_row_id"), bool)
                or payload["_result_row_id"] != attempt_row["result_id"]
            ):
                return False, False
            return True, True
        return True, False

    def _authenticate_v2_refusal_escalation_uncommitted(
        self, attempt_row: dict, *, refusal_code: str,
    ) -> str | None:
        """Authenticate exactly one complete root-escalated or child-failed shape."""
        try:
            rows = [
                row for row in self.get_audit_logs(attempt_row["root_task_id"])
                if row.get("action") in (
                    "escalation", AUTHORITY_POLICY_V2_REFUSAL_TASK_FAILED_ACTION,
                )
                and row.get("agent") == attempt_row["manager_agent"]
                and isinstance(row.get("payload"), dict)
                and row["payload"].get("attempt_id") == attempt_row["attempt_id"]
            ]
            task = self._conn.execute(
                "SELECT * FROM tasks WHERE id=?", (attempt_row["root_task_id"],),
            ).fetchone()
        except Exception:
            return None
        if len(rows) != 1 or task is None:
            return None
        payload = rows[0]["payload"]
        note = f"authority_v2_refusal:{refusal_code}"
        if rows[0]["action"] == "escalation":
            expected = {
                "reason": "authority_v2_refusal",
                "refusal_code": refusal_code,
                "attempt_id": attempt_row["attempt_id"],
            }
            if (
                task["parent_task_id"] is None
                and task["status"] == TaskStatus.ESCALATED.value
                and task["note"] == note
                and set(payload) == set(expected)
                and all(payload.get(key) == value for key, value in expected.items())
            ):
                return "escalated"
            return None
        expected = {
            "reason": "authority_v2_refusal",
            "refusal_code": refusal_code,
            "attempt_id": attempt_row["attempt_id"],
            "result_id": attempt_row["result_id"],
            "parent_task_id": task["parent_task_id"],
        }
        if (
            task["parent_task_id"] is not None
            and task["status"] == TaskStatus.FAILED.value
            and task["note"] == note
            and task["completed_at"] is not None
            and set(payload) == set(expected)
            and all(payload.get(key) == value for key, value in expected.items())
        ):
            return "failed"
        return None

    def _authenticate_v2_obligation_uncommitted(
        self, attempt_row: dict, *, _history: list[dict] | None = None,
    ) -> str | None:
        """Return the closed code of exactly one authentic failed-stage obligation."""
        try:
            rows = [
                row for row in (
                    self.get_audit_logs(attempt_row["root_task_id"])
                    if _history is None else _history
                )
                if row.get("action") == AUTHORITY_POLICY_V2_HOUSEKEEPING_OBLIGATION_ACTION
                and row.get("agent") == attempt_row["manager_agent"]
                and isinstance(row.get("payload"), dict)
                and row["payload"].get("attempt_id") == attempt_row["attempt_id"]
            ]
        except Exception:
            return None
        if len(rows) != 1:
            return None
        payload = rows[0]["payload"]
        expected_keys = {
            "attempt_id", "result_id", "refusal_code", "origin_boot_id",
            "owner_attempt_id",
        }
        if set(payload.keys()) != expected_keys:
            return None
        if (
            payload["result_id"] != attempt_row["result_id"]
            or payload["origin_boot_id"] != attempt_row["origin_boot_id"]
            or payload["owner_attempt_id"] != attempt_row["owner_attempt_id"]
            or payload["refusal_code"]
            not in AUTHORITY_POLICY_V2_HOUSEKEEPING_REFUSAL_CODES
        ):
            return None
        return payload["refusal_code"]

    def _update_v2_attempt_finalization_uncommitted(
        self, attempt: AuthorityPolicyV2Attempt, finalization_state: str,
        refusal_code: str | None,
    ) -> None:
        """CAS the attempt to a terminal finalization, retaining its stage."""
        if attempt.finalization_state != "unfinalized":
            raise ValueError("v2 attempt is already finalized")
        if finalization_state not in (
            AUTHORITY_POLICY_V2_ATTEMPT_FINALIZATION_REFUSED,
            AUTHORITY_POLICY_V2_ATTEMPT_FINALIZATION_OWNER_LOST,
            "continued",
        ):
            raise ValueError("illegal v2 attempt finalization")
        if finalization_state == "continued":
            refusal_code = None
        elif refusal_code not in AUTHORITY_POLICY_V2_HOUSEKEEPING_REFUSAL_CODES:
            raise ValueError("finalized v2 attempt requires a closed refusal code")
        snapshot = attempt.model_dump(mode="json")
        snapshot["finalization_state"] = finalization_state
        snapshot["refusal_code"] = refusal_code
        cursor = self._conn.execute(
            """UPDATE authority_policy_v2_attempts
                  SET finalization_state=?, refusal_code=?, canonical_payload_json=?
                WHERE attempt_id=? AND finalization_state='unfinalized' AND stage=?""",
            (
                finalization_state, refusal_code,
                authority_policy_v2_canonical_json_bytes(snapshot).decode("utf-8"),
                attempt.attempt_id, attempt.stage,
            ),
        )
        if cursor.rowcount != 1:
            raise ValueError("v2 attempt finalization CAS lost")

    def _settle_v2_exact_receipt_uncommitted(
        self, receipt_row, *, root_task_id: str, manager_agent: str,
        manager_session_id: str, result_id: int, now: str,
    ) -> bool:
        """Settle exactly the still-accepted obsolete matching recovery receipt."""
        cursor = self._conn.execute(
            """UPDATE task_completion_recoveries
                  SET state='callback_consumed', accepted_result_session_id=?,
                      settled_at=?
                WHERE id=? AND task_id=? AND agent=? AND recovery_session_id=?
                  AND accepted_result_id=? AND accepted_result_session_id=?
                  AND state='callback_accepted'""",
            (
                manager_session_id, now, receipt_row["id"], root_task_id,
                manager_agent, manager_session_id, result_id, manager_session_id,
            ),
        )
        return cursor.rowcount == 1

    @_synchronized
    def finalize_authority_policy_v2_attempt_refusal(
        self, *, root_task_id: str, manager_agent: str, manager_session_id: str,
        result_id: int, refusal_code: str, owner_attempt_id: str | None = None,
        recovery_session_id: str | None = None,
        _startup_phases: _V2StartupRefusalPhases | None = None,
    ) -> AuthorityPolicyV2HousekeepingOutcome:
        """ONE terminal refusal-housekeeping transaction over exact J/R/task/Q.

        Authenticate-then-mutate: a wrong/mixed tuple, a missing binding, an
        unrelated receipt, an unauthorized contender or an unsafe attribution
        refuses BEFORE any task/Q/J mutation.  A still-current root owner is
        escalated while a delegated owner is failed; both refuse J.  A
        cancelled/terminal/replaced owner keeps the winning task row exactly
        and records the old attempt owner_lost.  An
        already-finalized J returns a read-only exact replay only when its exact
        refusal/completion evidence authenticates, and is never repaired.
        """
        # Ordinary callers use exactly their existing connection operations.
        # Private startup observes these REAL boundaries, including cleanup that
        # fails before the existing exception handler attempts cleanup again.
        def _rollback() -> None:
            if _startup_phases is None:
                self._conn.rollback()
            else:
                _startup_phases.rollback(self._conn)

        def _commit() -> None:
            if _startup_phases is None:
                self._conn.commit()
            else:
                _startup_phases.commit(self._conn)

        attempt_id = self._authority_policy_v2_attempt_id_for_identity(
            root_task_id=root_task_id, manager_agent=manager_agent,
            manager_session_id=manager_session_id, result_id=result_id,
        )

        def _pending(reason: str) -> AuthorityPolicyV2HousekeepingOutcome:
            return AuthorityPolicyV2HousekeepingOutcome(
                status="housekeeping_pending", refusal_code=reason,
                attempt_id=attempt_id,
            )

        if refusal_code not in AUTHORITY_POLICY_V2_HOUSEKEEPING_REFUSAL_CODES:
            raise ValueError("refusal code is not a closed v2 housekeeping value")
        if recovery_session_id is not None and recovery_session_id != manager_session_id:
            return _pending("identity_mismatch")
        # Reject caller-owned transaction nesting BEFORE BEGIN/ROLLBACK/liveness.
        if self._conn.in_transaction:
            return _pending("transaction_owned")
        attempt: AuthorityPolicyV2Attempt | None = None
        try:
            if _startup_phases is not None:
                _startup_phases.begin_attempted = True
            self._conn.execute("BEGIN IMMEDIATE")
            if _startup_phases is not None:
                _startup_phases.began = True
            row = self._conn.execute(
                """SELECT * FROM authority_policy_v2_attempts
                   WHERE root_task_id=? AND manager_agent=?
                     AND manager_session_id=? AND result_id=?""",
                (root_task_id, manager_agent, manager_session_id, result_id),
            ).fetchone()
            if row is None:
                _rollback()
                return _pending("identity_mismatch")
            try:
                attempt = self._authority_policy_v2_attempt_from_row(row)
            except ValueError:
                _rollback()
                return _pending("identity_mismatch")
            attempt_row = dict(row)
            if attempt_id != attempt.attempt_id:
                _rollback()
                return _pending("identity_mismatch")

            # Optional candidate (K) identity, if one was created.
            candidate = None
            candidate_row = self._conn.execute(
                """SELECT * FROM authority_policy_v2_candidates
                   WHERE root_task_id=? AND manager_agent=?
                     AND manager_session_id=? AND result_id=?""",
                (root_task_id, manager_agent, manager_session_id, result_id),
            ).fetchone()
            if candidate_row is not None:
                try:
                    candidate = self._authority_policy_v2_candidate_from_row(candidate_row)
                except ValueError:
                    _rollback()
                    return _pending("identity_mismatch")
                if (
                    candidate.attempt_id != attempt.attempt_id
                    or candidate.root_task_id != root_task_id
                    or candidate.manager_agent != manager_agent
                    or candidate.manager_session_id != manager_session_id
                ):
                    _rollback()
                    return _pending("identity_mismatch")
            candidate_id = None if candidate is None else candidate.candidate_id

            # Immutable attribution FIRST (never the failed continuation
            # evidence): the exact causal result row and the immutable launch
            # binding are authenticated BEFORE any terminal success or any
            # receipt classification.  A drifted result/session/binding can
            # therefore never return an ``already_refused`` success.  The
            # result BODY/assessment is deliberately NOT required: a corrupt
            # assessment or a missing pre-final audit is a legitimate refusal
            # cause and must not block terminal housekeeping or its exact replay.
            result_row = self._conn.execute(
                "SELECT * FROM task_results WHERE id=?", (result_id,)
            ).fetchone()
            if (
                result_row is None
                or result_row["task_id"] != root_task_id
                or result_row["agent"] != manager_agent
                or result_row["session_id"] != manager_session_id
            ):
                _rollback()
                return _pending("identity_mismatch")
            binding = self.get_authority_policy_v2_session_binding(
                root_task_id=root_task_id, manager_agent=manager_agent,
                manager_session_id=manager_session_id,
            )
            if (
                binding is None
                or binding.binding_id != attempt.binding_id
                or binding.root_task_id != root_task_id
                or binding.manager_agent != manager_agent
                or binding.manager_session_id != manager_session_id
            ):
                _rollback()
                return _pending("identity_mismatch")
            try:
                self._authenticate_v2_session_binding_uncommitted(binding)
            except Exception:
                _rollback()
                return _pending("identity_mismatch")

            # Optional exact Q: match only this attempt's exact recovery-session
            # identity.  A task may legitimately retain settled receipts from
            # prior generations; those rows are historical evidence, not a
            # claim that the current ordinary result is recovery-owned.
            receipts = self._conn.execute(
                "SELECT * FROM task_completion_recoveries WHERE task_id=? AND agent=?",
                (root_task_id, manager_agent),
            ).fetchall()
            matching_receipts = [
                receipt for receipt in receipts
                if receipt["recovery_session_id"] == manager_session_id
            ]
            if len(matching_receipts) > 1:
                _rollback()
                return _pending("identity_mismatch")
            exact_receipt = matching_receipts[0] if matching_receipts else None
            if any(
                receipt["recovery_session_id"] != manager_session_id
                and receipt["state"] in ("claimed", "callback_accepted")
                for receipt in receipts
            ):
                # A different still-live recovery obligation is not ordinary
                # absence and must never be settled as this attempt's receipt.
                _rollback()
                return _pending("identity_mismatch")
            if exact_receipt is not None:
                if (
                    exact_receipt["accepted_result_id"] != result_id
                    or exact_receipt["accepted_result_session_id"] != manager_session_id
                    or exact_receipt["state"]
                    not in ("callback_accepted", "callback_consumed")
                ):
                    _rollback()
                    return _pending("identity_mismatch")
            # Receipt identity comes from a REAL Q.  An explicit recovery
            # assertion with no actual matching receipt fails closed with no
            # task/Q/J change and never manufactures recovery-shaped evidence;
            # a genuine ordinary absence (no recovery asserted, no Q) stays
            # ordinary.
            if recovery_session_id is not None and exact_receipt is None:
                _rollback()
                return _pending("receipt_missing")
            effective_recovery_session_id = (
                manager_session_id if exact_receipt is not None else None
            )

            # Already-finalized J: read-only exact replay, never repair.  The
            # complete identity-scoped terminal evidence set must authenticate
            # as ONE closed set: the refusal result-stage event, the single
            # bounded completion audit, the normal escalation audit for the
            # still-current-owner refused outcome, and the candidate refused
            # audit when K exists.  Missing/deleted/mutated/duplicate/conflicting
            # /malformed evidence refuses with no repair and no allocation.
            if attempt.finalization_state in (
                AUTHORITY_POLICY_V2_ATTEMPT_FINALIZATION_REFUSED,
                AUTHORITY_POLICY_V2_ATTEMPT_FINALIZATION_OWNER_LOST,
            ):
                terminal_ok = self._authenticate_v2_refusal_result_stage_uncommitted(
                    attempt_row, candidate_id=candidate_id,
                    refusal_code=attempt.refusal_code,
                    finalization_state=attempt.finalization_state,
                )
                completion_ok, recovery_claimed = (
                    self._authenticate_v2_refusal_completion_uncommitted(
                        attempt_row, refusal_code=attempt.refusal_code,
                    )
                )
                terminal_ok = terminal_ok and completion_ok
                if terminal_ok:
                    if recovery_claimed:
                        # The stored recovery evidence is only authentic when the
                        # exact Q is durably `callback_consumed` with the exact
                        # task/agent/recovery-session/result/result-session tuple.
                        # A deleted/transition-only/replaced Q is missing
                        # evidence, never "ordinary".
                        terminal_ok = (
                            exact_receipt is not None
                            and exact_receipt["state"] == "callback_consumed"
                            and exact_receipt["recovery_session_id"] == manager_session_id
                            and exact_receipt["accepted_result_id"] == result_id
                            and exact_receipt["accepted_result_session_id"] == manager_session_id
                        )
                    else:
                        # Ordinary refusal evidence requires an ordinary absence.
                        terminal_ok = exact_receipt is None
                if terminal_ok and (
                    attempt.finalization_state
                    == AUTHORITY_POLICY_V2_ATTEMPT_FINALIZATION_REFUSED
                ):
                    task_disposition = self._authenticate_v2_refusal_escalation_uncommitted(
                        attempt_row, refusal_code=attempt.refusal_code,
                    )
                    terminal_ok = task_disposition is not None
                else:
                    task_disposition = None
                if terminal_ok and candidate is not None:
                    terminal_ok = self._authenticate_v2_candidate_audit_uncommitted(
                        candidate, AUTHORITY_POLICY_V2_CANDIDATE_AUDIT_EVENT_REFUSED,
                    )
                _rollback()
                self._clear_v2_refusal_failure_authority(attempt.attempt_id)
                if terminal_ok:
                    return AuthorityPolicyV2HousekeepingOutcome(
                        status="already_refused", refusal_code=attempt.refusal_code,
                        attempt_id=attempt.attempt_id, candidate_id=candidate_id,
                        stage=attempt.stage,
                        finalization_state=attempt.finalization_state,
                        receipt_settled=recovery_claimed,
                        task_disposition=task_disposition,
                    )
                return _pending("identity_mismatch")
            if attempt.finalization_state != "unfinalized":
                _rollback()
                return _pending("identity_mismatch")
            if exact_receipt is not None and exact_receipt["state"] == "callback_consumed":
                # An already-consumed exact Q without a finalized J is
                # inconsistent terminal evidence; never repair or re-settle it.
                _rollback()
                return _pending("identity_mismatch")

            task = self._conn.execute(
                "SELECT * FROM tasks WHERE id=?", (root_task_id,)
            ).fetchone()
            if task is None:
                _rollback()
                return _pending("identity_mismatch")

            now = _now().isoformat()
            still_current = (
                task["cancelled_at"] is None
                and task["status"] == TaskStatus.IN_PROGRESS.value
                and task["block_kind"] is None
                and task["assigned_agent"] == manager_agent
                and task["current_session_id"] == manager_session_id
            )

            if not still_current:
                # Preserve the winning task row exactly.  Record the old
                # attempt's owner_lost/cancellation disposition and audits; only
                # an exact obsolete matching Q may be settled.
                terminal = task["status"] in (
                    "completed", "failed", "superseded", "cancelled",
                )
                final_code = (
                    "cancelled"
                    if (task["cancelled_at"] is not None or terminal)
                    else "owner_lost"
                )
                self.insert_audit_log_uncommitted(
                    root_task_id, manager_agent,
                    AUTHORITY_POLICY_V2_RESULT_STAGE_ACTION,
                    self._v2_refusal_stage_payload(
                        attempt_row, candidate_id, final_code,
                        AUTHORITY_POLICY_V2_ATTEMPT_FINALIZATION_OWNER_LOST,
                    ),
                )
                if candidate is not None:
                    self._insert_v2_candidate_audit_uncommitted(
                        candidate, AUTHORITY_POLICY_V2_CANDIDATE_AUDIT_EVENT_REFUSED,
                        owner_attempt_id=attempt.owner_attempt_id,
                        origin_boot_id=attempt.origin_boot_id, now=now,
                    )
                self.insert_audit_log_uncommitted(
                    root_task_id, manager_agent, "completion_report",
                    self._v2_refusal_completion_payload(
                        attempt_row=attempt_row, refusal_code=final_code,
                        recovery_session_id=effective_recovery_session_id,
                    ),
                )
                settled = False
                if exact_receipt is not None:
                    if not self._settle_v2_exact_receipt_uncommitted(
                        exact_receipt, root_task_id=root_task_id,
                        manager_agent=manager_agent,
                        manager_session_id=manager_session_id, result_id=result_id,
                        now=now,
                    ):
                        _rollback()
                        return _pending("identity_mismatch")
                    settled = True
                self._update_v2_attempt_finalization_uncommitted(
                    attempt, AUTHORITY_POLICY_V2_ATTEMPT_FINALIZATION_OWNER_LOST,
                    final_code,
                )
                _commit()
                self._clear_v2_refusal_failure_authority(attempt.attempt_id)
                if owner_attempt_id is not None:
                    self._forget_v2_live_owner(attempt.attempt_id, owner_attempt_id)
                return AuthorityPolicyV2HousekeepingOutcome(
                    status="owner_lost", refusal_code=final_code,
                    attempt_id=attempt.attempt_id, candidate_id=candidate_id,
                    stage=attempt.stage,
                    finalization_state=AUTHORITY_POLICY_V2_ATTEMPT_FINALIZATION_OWNER_LOST,
                    receipt_settled=settled,
                )

            # Live-owner safety: only the authentic uninterrupted owner token, an
            # old-boot attempt (trusted current process identity differs), the
            # process-local marker left by THIS owner's failed refusal
            # transaction, or a server-written durable failed-stage obligation
            # may finalize.  A same-boot attempt with no token (e.g. a second
            # Database instance) cannot prove the winner is dead and returns
            # bounded pending.
            live_owner = (
                owner_attempt_id is not None
                and self._v2_contender_is_authentic_owner(
                    attempt_id=attempt.attempt_id,
                    owner_attempt_id=owner_attempt_id,
                    origin_boot_id=attempt.origin_boot_id,
                )
            )
            old_boot = (
                self._v2_process_boot_id is not None
                and attempt.origin_boot_id != self._v2_process_boot_id
            )
            refusal_failed = self._v2_refusal_failure_authority(
                attempt_id=attempt.attempt_id, owner_attempt_id=owner_attempt_id,
            )
            if not (live_owner or old_boot or refusal_failed):
                if self._authenticate_v2_obligation_uncommitted(attempt_row) is None:
                    _rollback()
                    return _pending("owner_lost")

            # Still-current causal owner: roots retain the existing escalation
            # shape; delegated tasks atomically fail with their own closed audit.
            self.insert_audit_log_uncommitted(
                root_task_id, manager_agent,
                AUTHORITY_POLICY_V2_RESULT_STAGE_ACTION,
                self._v2_refusal_stage_payload(
                    attempt_row, candidate_id, refusal_code,
                    AUTHORITY_POLICY_V2_ATTEMPT_FINALIZATION_REFUSED,
                ),
            )
            if candidate is not None:
                self._insert_v2_candidate_audit_uncommitted(
                    candidate, AUTHORITY_POLICY_V2_CANDIDATE_AUDIT_EVENT_REFUSED,
                    owner_attempt_id=attempt.owner_attempt_id,
                    origin_boot_id=attempt.origin_boot_id, now=now,
                )
            parent_task_id = task["parent_task_id"]
            if parent_task_id is None:
                task_disposition = "escalated"
                self.insert_audit_log_uncommitted(
                    root_task_id, manager_agent, "escalation",
                    {
                        "reason": "authority_v2_refusal",
                        "refusal_code": refusal_code,
                        "attempt_id": attempt.attempt_id,
                    },
                )
                cursor = self._conn.execute(
                    """UPDATE tasks SET status=?, block_kind=NULL, note=?, updated_at=?
                       WHERE id=? AND cancelled_at IS NULL AND status=?
                         AND block_kind IS NULL AND assigned_agent=?
                         AND current_session_id=? AND parent_task_id IS NULL""",
                    (
                        TaskStatus.ESCALATED.value,
                        f"authority_v2_refusal:{refusal_code}", now, root_task_id,
                        TaskStatus.IN_PROGRESS.value, manager_agent,
                        manager_session_id,
                    ),
                )
            else:
                task_disposition = "failed"
                self.insert_audit_log_uncommitted(
                    root_task_id,
                    manager_agent,
                    AUTHORITY_POLICY_V2_REFUSAL_TASK_FAILED_ACTION,
                    {
                        "reason": "authority_v2_refusal",
                        "refusal_code": refusal_code,
                        "attempt_id": attempt.attempt_id,
                        "result_id": result_id,
                        "parent_task_id": parent_task_id,
                    },
                )
                cursor = self._conn.execute(
                    """UPDATE tasks
                          SET status=?, block_kind=NULL, note=?, completed_at=?,
                              updated_at=?, active_chain=NULL, active_fanout=NULL
                        WHERE id=? AND cancelled_at IS NULL AND status=?
                          AND block_kind IS NULL AND assigned_agent=?
                          AND current_session_id=? AND parent_task_id IS NOT NULL""",
                    (
                        TaskStatus.FAILED.value,
                        f"authority_v2_refusal:{refusal_code}", now, now,
                        root_task_id, TaskStatus.IN_PROGRESS.value,
                        manager_agent, manager_session_id,
                    ),
                )
            if cursor.rowcount != 1:
                _rollback()
                return _pending("owner_lost")
            if task_disposition == "failed":
                env = self._conn.execute(
                    """SELECT * FROM authority_continue_envelopes
                       WHERE root_task_id=? AND state='active'""",
                    (root_task_id,),
                ).fetchone()
                if env is not None:
                    consumed = self._conn.execute(
                        """UPDATE authority_continue_envelopes
                              SET state='violated', consumed_at=?, updated_at=?
                            WHERE id=? AND state='active'""",
                        (now, now, env["id"]),
                    )
                    if consumed.rowcount != 1:
                        _rollback()
                        return _pending("owner_lost")
                    self.insert_audit_log_uncommitted(
                        root_task_id,
                        manager_agent,
                        "authority_continue_envelope_violated",
                        {
                            "envelope_id": env["id"],
                            "candidate_id": env["candidate_id"],
                            "root_task_id": root_task_id,
                            "decision_family": "aborted",
                            "clause_id": env["clause_id"],
                            "action": env["action"],
                            "policy_id": env["policy_id"],
                            "policy_version": env["policy_version"],
                            "policy_digest": env["policy_digest"],
                            "causal_event_id": env["causal_event_id"],
                            "causal_event_digest": env["causal_event_digest"],
                            "state": "violated",
                            "error": "task failed without the permitted continued-turn decision",
                        },
                    )
            self.insert_audit_log_uncommitted(
                root_task_id, manager_agent, "completion_report",
                self._v2_refusal_completion_payload(
                    attempt_row=attempt_row, refusal_code=refusal_code,
                    recovery_session_id=effective_recovery_session_id,
                ),
            )
            settled = False
            if exact_receipt is not None:
                if not self._settle_v2_exact_receipt_uncommitted(
                    exact_receipt, root_task_id=root_task_id,
                    manager_agent=manager_agent,
                    manager_session_id=manager_session_id, result_id=result_id,
                    now=now,
                ):
                    _rollback()
                    return _pending("identity_mismatch")
                settled = True
            self._update_v2_attempt_finalization_uncommitted(
                attempt, AUTHORITY_POLICY_V2_ATTEMPT_FINALIZATION_REFUSED,
                refusal_code,
            )
            _commit()
            self._clear_v2_refusal_failure_authority(attempt.attempt_id)
            if owner_attempt_id is not None:
                self._forget_v2_live_owner(attempt.attempt_id, owner_attempt_id)
            return AuthorityPolicyV2HousekeepingOutcome(
                status="refused", refusal_code=refusal_code,
                attempt_id=attempt.attempt_id, candidate_id=candidate_id,
                stage=attempt.stage,
                finalization_state=AUTHORITY_POLICY_V2_ATTEMPT_FINALIZATION_REFUSED,
                receipt_settled=settled,
                task_disposition=task_disposition,
            )
        except Exception:
            _rollback()
            # Failure ownership is established BEFORE poisoning: only the exact
            # process-local winning token that this caller presented proves the
            # authentic uninterrupted owner.  A malformed/foreign/nested/
            # duplicate contender returns bounded pending/refusal above and
            # never reaches this path, so it can never poison or finalize a
            # valid winner.  The authentic owner's failed refusal poisons the
            # live token (prohibiting any later claim/evaluate/consume/audit
            # advancement and final mint) and records a bounded best-effort
            # durable obligation; the process-local marker keeps safely
            # attributable housekeeping retry possible even when persisting that
            # diagnostic fails, so liveness is never reconstructed from durable
            # UUIDs.  The original exception is preserved truthfully.
            if (
                attempt is not None
                and owner_attempt_id is not None
                and owner_attempt_id == attempt.owner_attempt_id
                and self._v2_live_attempt_owners.get(attempt.attempt_id)
                == owner_attempt_id
            ):
                self._mark_v2_refusal_failure_authority(
                    attempt.attempt_id, owner_attempt_id,
                )
                self._record_v2_failed_stage_obligation(
                    attempt_id=attempt.attempt_id,
                    owner_attempt_id=owner_attempt_id, code=refusal_code,
                )
                self._forget_v2_live_owner(attempt.attempt_id, owner_attempt_id)
            raise

    def _v2_housekeeping_target_from_row(
        self, row, *, obligation_code: str | None,
    ) -> AuthorityPolicyV2HousekeepingTarget:
        attempt = self._authority_policy_v2_attempt_from_row(row)
        candidate_row = self._conn.execute(
            "SELECT candidate_id FROM authority_policy_v2_candidates WHERE result_id=?",
            (attempt.result_id,),
        ).fetchone()
        candidate_id = (
            None if candidate_row is None else candidate_row["candidate_id"]
        )
        return AuthorityPolicyV2HousekeepingTarget(
            attempt_id=attempt.attempt_id,
            team=attempt.team,
            root_task_id=attempt.root_task_id,
            manager_agent=attempt.manager_agent,
            manager_session_id=attempt.manager_session_id,
            result_id=attempt.result_id,
            origin_boot_id=attempt.origin_boot_id,
            owner_attempt_id=attempt.owner_attempt_id,
            stage=attempt.stage,
            finalization_state=attempt.finalization_state,
            candidate_id=candidate_id,
            obligation_code=obligation_code,
        )

    @_synchronized
    def list_authority_policy_v2_unfinalized_attempts(
        self,
    ) -> list[AuthorityPolicyV2HousekeepingTarget]:
        """Read-only discovery of every unfinalized pre-final attempt.

        Reads are nonmutating and authenticate the actual persisted attempt row;
        a failed claim that created no candidate (K) remains discoverable.  A
        corrupt/unreadable present attempt fails the whole discovery closed so
        startup cannot route its root through generic recovery by omission.
        """
        rows = self._conn.execute(
            """SELECT * FROM authority_policy_v2_attempts
               WHERE finalization_state='unfinalized'
               ORDER BY created_at, attempt_id""",
        ).fetchall()
        targets = []
        for row in rows:
            try:
                obligation_code = self._authenticate_v2_obligation_uncommitted(dict(row))
                targets.append(self._v2_housekeeping_target_from_row(
                    row, obligation_code=obligation_code,
                ))
            except ValueError as exc:
                # Startup cannot safely continue around a present malformed J:
                # silently omitting it would let generic recovery/failure/
                # enqueue logic mutate the same root.  The caller treats this
                # as a global fail-closed discovery result for that sweep.
                raise ValueError(
                    "authority v2 unfinalized attempt discovery is malformed"
                ) from exc
        return targets

    @_synchronized
    def get_authority_policy_v2_housekeeping_target(
        self, *, root_task_id: str, manager_agent: str, manager_session_id: str,
        result_id: int,
    ) -> AuthorityPolicyV2HousekeepingTarget | None:
        """Read-only discovery of one exact unfinalized attempt (nonmutating)."""
        row = self._conn.execute(
            """SELECT * FROM authority_policy_v2_attempts
               WHERE root_task_id=? AND manager_agent=?
                 AND manager_session_id=? AND result_id=?""",
            (root_task_id, manager_agent, manager_session_id, result_id),
        ).fetchone()
        if row is None:
            return None
        try:
            obligation_code = self._authenticate_v2_obligation_uncommitted(dict(row))
            return self._v2_housekeeping_target_from_row(
                row, obligation_code=obligation_code,
            )
        except ValueError:
            return None
