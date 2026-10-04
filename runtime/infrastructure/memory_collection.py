"""Observation-only census for real task-bootstrap invocations.

Audit storage is ordinary same-user storage, not an authority or security
boundary. A complete census never grants collection/epoch/installed acceptance.
Lock order is observer -> Database; callers enter without a Database lock.
No observer lock is retained across preparation, provider execution or callbacks.
"""
from __future__ import annotations

import copy
from collections import deque
import hashlib
import inspect
import json
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from runtime.infrastructure.database import Database
from runtime.infrastructure.learnings_store import MemoryDigestRender, MemoryStore

PHASES = ("intent", "identity", "expectation", "binding", "launched", "terminal")
EMPTY_DIGEST = hashlib.sha256(b"").hexdigest()
# Bound all acquired audit history, including unrelated/prior-boot rows. A
# limit is an unavailable observation, never permission to validate a prefix.
CENSUS_READ_PAGE_ROWS = 256
MAX_CENSUS_READ_ROWS = 100_000


class CensusReadUnavailable(Exception):
    """A bounded acquisition cannot establish a coherent complete history."""

    def __init__(self, category: str) -> None:
        super().__init__(category)
        self.category = category

RENDER_CONTRACT_DIGEST = hashlib.sha256(
    inspect.getsource(MemoryStore.render_memory_digest).encode("utf-8")
).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _advance(digest: str, record: dict) -> str:
    encoded = json.dumps(record, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False, allow_nan=False).encode("utf-8")
    return hashlib.sha256(bytes.fromhex(digest) + encoded).hexdigest()


def _projection(row: dict) -> dict:
    return {key: row[key] for key in ("task_id", "agent", "action", "payload")}


@dataclass
class InvocationObservation:
    observer: CollectionObserver
    ordinal: int
    task_id: str
    agent: str
    recovery: bool
    session_id: str | None = None
    expectation_recorded: bool = False
    bound: bool = False
    launched_callbacks: int = 0
    terminal: bool = False




class CollectionObserver:
    """One live org/boot owns counters independent of successful audit writes."""

    def __init__(self, *, org: str, root: Path, db: Database) -> None:
        self.org = org
        self.root = str(root.resolve())
        self.db = db
        self.boot_id = str(uuid.uuid4())
        self._lock = threading.RLock()
        self._writer = threading.Lock()
        self._pending: deque[tuple[InvocationObservation, str, dict]] = deque()
        self._assigned = 0
        self._generation = 0
        self._counts = {phase: {"attempted": 0, "persisted": 0} for phase in PHASES}
        self._digests = {phase: {"attempted": EMPTY_DIGEST, "persisted": EMPTY_DIGEST}
                         for phase in PHASES}
        self._active: dict[int, InvocationObservation] = {}
        self._error: str | None = None
        self._seal_attempts = 0
        self._seal_persisted = 0
        self._seal_digest = EMPTY_DIGEST
        self._latest_seal: int | None = None

    def unavailable(self, reason: str) -> None:
        """Sticky category-only failure; successful later writes cannot clear it."""
        with self._lock:
            if self._error is None:
                self._error = reason

    def begin(self, task_id: str, agent: str, *, recovery: bool) -> InvocationObservation:
        with self._lock:
            self._assigned += 1  # reserve BEFORE any telemetry insertion
            invocation = InvocationObservation(self, self._assigned, task_id, agent, recovery)
            self._active[invocation.ordinal] = invocation
            record = self._record(invocation, "intent", {"entered_at": _now(), "recovery": recovery})
        self._persist(record)
        return invocation

    def observe(self, invocation: InvocationObservation, phase: str, **facts: Any) -> None:
        with self._lock:
            if (invocation.observer is not self or invocation.terminal
                    or self._active.get(invocation.ordinal) is not invocation):
                self.unavailable("invocation_reference_invalid")
                return
            if phase == "identity":
                if invocation.session_id is not None:
                    self.unavailable("identity_already_recorded")
                    return
                invocation.session_id = facts["session_id"]
            elif phase == "expectation":
                if invocation.expectation_recorded:
                    self.unavailable("expectation_already_recorded")
                    return
                invocation.expectation_recorded = True
            elif phase == "binding":
                if invocation.bound:
                    self.unavailable("binding_already_recorded")
                    return
                invocation.bound = True
            elif phase == "launched":
                invocation.launched_callbacks += 1
                facts = {"callback_count": invocation.launched_callbacks}
            elif phase == "terminal":
                invocation.terminal = True
                facts.update(expectation_known=invocation.expectation_recorded,
                             binding_known=invocation.bound,
                             launched_callbacks=invocation.launched_callbacks)
                del self._active[invocation.ordinal]
            record = self._record(invocation, phase, facts)
        self._persist(record)

    def expectation(self, invocation: InvocationObservation, *, budget: int,
                    directory_present: bool | None, render: MemoryDigestRender | None) -> None:
        """Freeze the exact structured result already used by prompt assembly."""
        text_present = bool(render and render.text)
        pointers = list(render.pointer_ids) if render else []
        bodies = list(render.full_body_ids) if render else []
        ids = list(render.digest_ids) if render else []
        if budget == 0:
            state, reason = "disabled", "budget_zero"
        elif directory_present is False:
            state, reason = "empty", "memory_directory_absent"
        elif not text_present:
            state, reason = "empty", "renderer_empty"
        elif not ids:
            state, reason = "empty", "no_valid_rendered_ids"
        else:
            state, reason = "nonempty", "rendered_ids"
        self.observe(invocation, "expectation", state=state, reason=reason,
                     budget=budget, config_version=None, memory_telemetry_version=1,
                     renderer_contract_digest=RENDER_CONTRACT_DIGEST,
                     rendered_text_present=text_present, pointer_ids=pointers,
                     full_body_ids=bodies, digest_ids=ids, digest_count=len(ids))

    def _record(self, invocation: InvocationObservation, phase: str, facts: dict) -> bool:
        self._generation += 1
        payload = {"contract_version": 1, "org": self.org, "root": self.root,
                   "boot_id": self.boot_id, "ordinal": invocation.ordinal,
                   "generation": self._generation, "task_id": invocation.task_id,
                   "agent": invocation.agent, "session_id": invocation.session_id, **facts}
        row = {"task_id": invocation.task_id, "agent": invocation.agent,
               "action": f"memory_runtime_{phase}", "payload": payload}
        self._counts[phase]["attempted"] += 1
        self._digests[phase]["attempted"] = _advance(self._digests[phase]["attempted"], row)
        # Queue metadata under the short counter lock. A boundary already
        # persisting drains it in generation order; other callbacks never wait
        # on that writer. There is no timer, worker, or reconstructed phase.
        self._pending.append((invocation, phase, row))
        return self._writer.acquire(blocking=False)

    def _persist(self, acquired: bool) -> None:
        if not acquired:
            return
        try:
            while True:
                with self._lock:
                    if not self._pending:
                        # Release atomically with the empty observation so a
                        # concurrent boundary cannot leave an undrained tail.
                        self._writer.release()
                        return
                    invocation, phase, row = self._pending.popleft()
                try:
                    audit_id = self.db.insert_audit_log(**row)
                    if type(audit_id) is not int or audit_id <= 0:
                        raise ValueError("invalid audit reference")
                    with self._lock:
                        self._counts[phase]["persisted"] += 1
                        self._digests[phase]["persisted"] = _advance(self._digests[phase]["persisted"], row)
                except Exception:
                    self.unavailable(f"{phase}_write_failed")
                self._seal(invocation)
        except BaseException:
            self.unavailable("observation_drain_failed")
            self._writer.release()
            raise

    def _snapshot(self) -> dict:
        references = [{"ordinal": item.ordinal, "task_id": item.task_id, "agent": item.agent,
                       "session_id": item.session_id, "expectation_known": item.expectation_recorded}
                      for item in self._active.values()]
        return {"contract_version": 1, "org": self.org, "root": self.root,
                "boot_id": self.boot_id, "generation": self._generation,
                "assigned_intents": self._assigned,
                "intent_digest": self._digests["intent"]["attempted"],
                "phase_counts": copy.deepcopy(self._counts),
                "phase_digests": copy.deepcopy(self._digests),
                "active_references": references,
                "active_preparations": [item for item in references if not item["expectation_known"]],
                "observation_error": self._error, "pending_observations": len(self._pending),
                "writer_active": self._writer.locked(),
                "seal_attempts": self._seal_attempts,
                "seal_persisted": self._seal_persisted, "seal_digest": self._seal_digest,
                "latest_seal_audit_id": self._latest_seal,
                "sampled_at": _now()}

    def snapshot(self) -> dict:
        """Metadata only, zero durable writes; no installed/epoch authority fields."""
        with self._lock:
            return self._snapshot()

    def _read_revision(self) -> tuple[int, int, int]:
        # Same-connection mutations advance total_changes(); other connections'
        # commits advance data_version. The audit PK bounds pagination without
        # a JSON predicate or a lock spanning the complete history acquisition.
        row = self.db.fetch_one_readonly(
            "SELECT total_changes(), data_version, "
            "(SELECT coalesce(max(id), 0) FROM audit_log) FROM pragma_data_version"
        )
        return tuple(row)

    def _rows(self) -> list[dict]:
        revision = self._read_revision()
        upper = revision[2]
        cursor, acquired = 0, 0
        candidates: list[dict] = []
        actions = {f"memory_runtime_{phase}" for phase in PHASES}
        actions.update(("memory_collection_seal", "session_start", "memory_digest_impression"))
        while cursor < upper:
            limit = min(CENSUS_READ_PAGE_ROWS, MAX_CENSUS_READ_ROWS - acquired + 1)
            raw = self.db.fetch_all_readonly(
                "SELECT id, task_id, agent, action, payload FROM audit_log "
                "WHERE id>? AND id<=? ORDER BY id LIMIT ?", (cursor, upper, limit),
            )
            acquired += len(raw)
            if acquired > MAX_CENSUS_READ_ROWS:
                raise CensusReadUnavailable("census_read_work_limit")
            if not raw:
                break
            cursor = raw[-1]["id"]
            # Decode outside the shared Database lock. Counts/digests reject
            # malformed/missing current-boot rows; no lost row is reconstructed.
            for row in raw:
                if row["action"] not in actions:
                    continue
                payload = json.loads(row["payload"] or "null")
                if isinstance(payload, dict):
                    candidates.append({**dict(row), "payload": payload})
        if self._read_revision() != revision:
            raise CensusReadUnavailable("census_read_moving")
        identities = {(row["task_id"], row["agent"], row["payload"].get("session_id"))
                      for row in candidates if row["action"] == "memory_runtime_identity"
                      and row["payload"].get("boot_id") == self.boot_id}
        return [row for row in candidates if row["payload"].get("boot_id") == self.boot_id
                or (row["action"] in ("session_start", "memory_digest_impression")
                    and (row["task_id"], row["agent"], row["payload"].get("session_id")) in identities)]

    def validate(self) -> dict:
        """Zero-write, one bounded capture; moving/in-flight facts fail closed."""
        snapshot = self.snapshot()
        if snapshot["writer_active"] or snapshot["pending_observations"]:
            return _result(["observation_pending"], {})
        try:
            integrity = validate_census(snapshot, self._rows())
            closing = self.snapshot()
            # Compare all semantic facts, including sticky errors and seal
            # progress that need not change the observation generation. No
            # metadata lock is retained across SELECT/decode/reconciliation.
            if ({key: value for key, value in snapshot.items() if key != "sampled_at"}
                    != {key: value for key, value in closing.items() if key != "sampled_at"}):
                return _result(["census_moving"], {})
            return integrity
        except CensusReadUnavailable as exc:
            return _result([exc.category], {})
        except Exception:
            return _result(["census_read_unavailable"], {})

    def _seal(self, invocation: InvocationObservation) -> None:
        with self._lock:
            self._seal_attempts += 1
            snapshot = self._snapshot()
        try:
            # Execution/callback paths checkpoint independent counters only.
            # Exhaustive history validation belongs to the bounded read path;
            # a seal must never advertise a reconciliation it did not perform.
            integrity = _result(["census_not_reconciled"], {})
            payload = {**snapshot, "census_integrity": integrity}
            row = {"task_id": invocation.task_id, "agent": invocation.agent,
                   "action": "memory_collection_seal", "payload": payload}
            audit_id = self.db.insert_audit_log(**row)
            if type(audit_id) is not int or audit_id <= 0:
                raise ValueError("invalid seal reference")
            with self._lock:
                self._seal_persisted += 1
                self._seal_digest = _advance(self._seal_digest, row)
                self._latest_seal = audit_id
        except Exception:
            self.unavailable("seal_write_failed")


def _result(problems: list[str], discrepancies: dict) -> dict:
    return {"census_valid": not problems, "problems": problems,
            "discrepancies": discrepancies,
            "collection_decision": "insufficient_instrumentation", "thresholds_met": False}


def validate_census(snapshot: dict, rows: list[dict], *, require_seal: bool = True) -> dict:
    """Reconcile independent live counts/digests with durable phase/start/exposure rows.

    This is census integrity only. Unknown/zero/pending populations cannot pass;
    a new boot cannot consume an old boot's complete prefix or recover lost tails.
    """
    problems: list[str] = []
    discrepancies: dict[str, int] = {}
    own = [row for row in rows if isinstance(row["payload"], dict)
           and row["payload"].get("boot_id") == snapshot["boot_id"]]
    phases = {phase: [row for row in own if row["action"] == f"memory_runtime_{phase}"]
              for phase in PHASES}
    n = snapshot["assigned_intents"]
    if n == 0:
        problems.append("zero_population")
    if snapshot["observation_error"]:
        problems.append("observation_error")
    if snapshot["active_preparations"]:
        problems.append("preparation_pending")
    for phase, phase_rows in phases.items():
        digest = EMPTY_DIGEST
        for row in phase_rows:
            digest = _advance(digest, _projection(row))
        counts = snapshot["phase_counts"][phase]
        digests = snapshot["phase_digests"][phase]
        discrepancies[phase] = counts["attempted"] - len(phase_rows)
        if (len(phase_rows) != counts["attempted"] or counts["persisted"] != counts["attempted"]
                or digest != digests["attempted"] or digest != digests["persisted"]):
            problems.append(f"{phase}_count_or_digest")
    intents = phases["intent"]
    if [row["payload"]["ordinal"] for row in intents] != list(range(1, n + 1)):
        problems.append("ordinal_set")
    # Exact digests above authenticate all metadata to this live observer. These
    # cross-stream joins independently detect lost starts/exposures at zero reads.
    by_ordinal: dict[int, dict[str, list[dict]]] = {}
    for phase, phase_rows in phases.items():
        for row in phase_rows:
            ordinal = row["payload"]["ordinal"]
            group = by_ordinal.setdefault(ordinal, {name: [] for name in PHASES})
            group[phase].append(row)
    starts_by_tuple: dict[tuple, list[dict]] = {}
    impressions_by_tuple: dict[tuple, list[dict]] = {}
    for row in rows:
        if row["action"] not in ("session_start", "memory_digest_impression") or not isinstance(row["payload"], dict):
            continue
        key = (row["task_id"], row["agent"], row["payload"].get("session_id"))
        target = starts_by_tuple if row["action"] == "session_start" else impressions_by_tuple
        target.setdefault(key, []).append(row)
    for intent in intents:
        ordinal = intent["payload"]["ordinal"]
        selected = by_ordinal[ordinal]
        identities, expectations = selected["identity"], selected["expectation"]
        if len(identities) != 1 or len(expectations) != 1:
            problems.append(f"unknown_preparation:{ordinal}")
            continue
        identity = identities[0]["payload"]
        expected = expectations[0]["payload"]
        population = identity.get("population")
        parent = identity.get("parent_task_id")
        if (identity.get("parent_known") is not True
                or identity.get("task_type") not in ("task", "subtask")
                or population not in ("root", "child", "recovery")
                or (population == "root" and parent is not None)
                or (population == "child" and not parent)):
            problems.append(f"population_unknown:{ordinal}")
        sid = identity["session_id"]
        key = (intent["task_id"], intent["agent"], sid)
        starts = starts_by_tuple.get(key, [])
        impressions = impressions_by_tuple.get(key, [])
        # A preparation is not complete until its trusted binding exists. Do
        # not treat an in-flight phase boundary as a proven dropped start.
        if len(selected["binding"]) != 1:
            problems.append(f"binding_unknown:{ordinal}")
        if selected["binding"] or selected["terminal"]:
            if len(starts) != 1:
                discrepancies["session_start"] = discrepancies.get("session_start", 0) + 1 - len(starts)
                problems.append(f"session_start_matching:{ordinal}")
            elif (starts[0]["payload"].get("executor") != identity["executor"]
                  or starts[0]["payload"].get("invocation_purpose") != identity["invocation_purpose"]
                  or starts[0]["payload"].get("model") != identity["model"]):
                problems.append(f"session_start_tuple:{ordinal}")
            if expected["state"] == "nonempty":
                if len(impressions) != 1:
                    discrepancies["impression"] = discrepancies.get("impression", 0) + 1 - len(impressions)
                    problems.append(f"impression_matching:{ordinal}")
                else:
                    payload = impressions[0]["payload"]
                    if (payload.get("memory_telemetry_version") != 1 or payload.get("agent") != intent["agent"]
                            or any(payload.get(key) != expected[key] for key in
                           ("digest_ids", "digest_count", "pointer_ids", "full_body_ids", "budget"))):
                        problems.append(f"impression_expectation:{ordinal}")
            elif impressions:
                problems.append(f"unexpected_impression:{ordinal}")
    if require_seal:
        seals = [row for row in own if row["action"] == "memory_collection_seal"]
        digest = EMPTY_DIGEST
        for row in seals:
            digest = _advance(digest, _projection(row))
        if (len(seals) != snapshot["seal_attempts"] or len(seals) != snapshot["seal_persisted"]
                or digest != snapshot["seal_digest"] or not seals
                or seals[-1]["id"] != snapshot["latest_seal_audit_id"]
                or seals[-1]["payload"]["generation"] != snapshot["generation"]):
            problems.append("seal_count_or_digest")
    return _result(problems, discrepancies)
