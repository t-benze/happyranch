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
import marshal
import os
import stat
import sys
from importlib.metadata import distribution
from email.parser import BytesHeaderParser
from types import CodeType
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from runtime.infrastructure.database import Database
from runtime.infrastructure.learnings_store import MemoryDigestRender, MemoryStore
from runtime.orchestrator import executor_binary_registry, adapter_store
from runtime.orchestrator.agent_def import parse_agent_text
from runtime.orchestrator.executor_registry import get_registry
from runtime.adapters import get_first_party_adapter
import yaml

# Resolve installed distribution location during best-effort observer module
# initialization, never by scanning package directories on GET.
try:
    _PACKAGE_METADATA_PATH = Path(distribution("happyranch")._path) / "METADATA"
except Exception:
    _PACKAGE_METADATA_PATH = None

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


# Fixed work limits on the serving GET. No tree walk, provider invocation,
# backend probe, registry mutation or exhaustive census decoding occurs here.
MAX_IDENTITY_FILE_BYTES = 1024 * 1024
MAX_IDENTITY_BINARY_BYTES = 512 * 1024 * 1024
MAX_IDENTITY_TOTAL_BYTES = 1024 * 1024 * 1024
MAX_IDENTITY_FILES = 96
MAX_IDENTITY_AGENTS = 64


class IdentityUnavailable(Exception):
    """Category-only acquisition failure; no paths/content from exceptions."""


def _identity_bytes(path: Path, budget: list[int], *, limit: int = MAX_IDENTITY_FILE_BYTES) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0)
    fd = os.open(path, flags)
    try:
        before = os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or before.st_size > limit
                or budget[0] + before.st_size > MAX_IDENTITY_TOTAL_BYTES
                or budget[1] >= MAX_IDENTITY_FILES):
            raise IdentityUnavailable("identity_work_limit")
        budget[0] += before.st_size
        budget[1] += 1
        with os.fdopen(fd, "rb", closefd=False) as stream:
            data = stream.read(limit + 1)
        after = os.fstat(fd)
        if (len(data) != before.st_size or len(data) > limit
                or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
                or path.stat() != after):
            raise IdentityUnavailable("identity_file_moving")
        return data
    finally:
        os.close(fd)


def _identity_file_hash(path: Path, budget: list[int]) -> str:
    """Stream only a declared executable, bounded by bytes/files; no tree walk."""
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
    try:
        before = os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or before.st_size > MAX_IDENTITY_BINARY_BYTES
                or budget[0] + before.st_size > MAX_IDENTITY_TOTAL_BYTES
                or budget[1] >= MAX_IDENTITY_FILES or not os.access(path, os.X_OK)):
            raise IdentityUnavailable("identity_work_limit")
        budget[0] += before.st_size
        budget[1] += 1
        digest, remaining = hashlib.sha256(), before.st_size
        while remaining:
            chunk = os.read(fd, min(256 * 1024, remaining))
            if not chunk:
                raise IdentityUnavailable("identity_file_moving")
            digest.update(chunk)
            remaining -= len(chunk)
        after = os.fstat(fd)
        if (os.read(fd, 1) or path.stat() != after
                or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)):
            raise IdentityUnavailable("identity_file_moving")
        return digest.hexdigest()
    finally:
        os.close(fd)


def _hash_metadata(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def _code_projection(code: CodeType) -> dict:
    # Structured constants avoid marshal's interning/reference-table differences
    # between an imported function and an independently compiled source object.
    return {"bytecode": code.co_code.hex(), "constants": [
                _code_projection(value) if isinstance(value, CodeType)
                else marshal.dumps(value).hex() for value in code.co_consts],
            "names": code.co_names, "variables": code.co_varnames,
            "freevars": code.co_freevars, "cellvars": code.co_cellvars,
            "flags": code.co_flags, "args": (code.co_argcount, code.co_posonlyargcount, code.co_kwonlyargcount),
            "filename": code.co_filename, "qualname": code.co_qualname,
            "firstline": code.co_firstlineno, "lines": code.co_linetable.hex(),
            "exceptions": code.co_exceptiontable.hex()}


def _find_code(code: CodeType, qualname: str) -> CodeType:
    if code.co_qualname == qualname:
        return code
    for item in code.co_consts:
        if isinstance(item, CodeType):
            try:
                return _find_code(item, qualname)
            except LookupError:
                pass
    raise LookupError(qualname)


def loaded_identity(org: Any) -> dict:
    """Actual interpreter/imported code and authoritative metadata, never attestation.

    Source is compiled without executing it, solely to compare declared loaded
    function fingerprints. No second checkout is imported to supply identity.
    """
    budget = [0, 0]
    files: dict[str, str] = {}
    def read(path: Path, *, limit: int = MAX_IDENTITY_FILE_BYTES) -> bytes:
        path = path.resolve(strict=True)
        data = _identity_bytes(path, budget, limit=limit)
        digest = hashlib.sha256(data).hexdigest()
        if str(path) in files and files[str(path)] != digest:
            raise IdentityUnavailable("identity_file_moving")
        files[str(path)] = digest
        return data

    def executable_hash(path: Path) -> str:
        path = path.resolve(strict=True)
        digest = _identity_file_hash(path, budget)
        if str(path) in files and files[str(path)] != digest:
            raise IdentityUnavailable("identity_file_moving")
        files[str(path)] = digest
        return digest

    python = {"executable": str(Path(sys.executable).resolve(strict=True)),
              "version": sys.version, "implementation": sys.implementation.name,
              "cache_tag": sys.implementation.cache_tag}
    executable_hash(Path(python["executable"]))
    source_root = Path(__file__).resolve(strict=True).parents[2]
    functions = [org.orchestrator._run_agent, org.orchestrator._run_agent_impl,
                 org.orchestrator._launch_agent_with_scratch,
                 org.orchestrator._run_agent_launch_contained,
                 org.orchestrator._resolve_executor_name, org.orchestrator._resolve_model_name,
                 org.orchestrator._build_executor, MemoryStore.render_memory_digest, CollectionObserver.begin,
                 CollectionObserver.observe, CollectionObserver.expectation,
                 CollectionObserver.snapshot, CollectionObserver._snapshot,
                 CollectionObserver._record, CollectionObserver._persist, CollectionObserver._seal,
                 _identity_bytes, _identity_file_hash, _hash_metadata, _code_projection, _find_code, _serving_snapshot,
                 _serving_revision, _check_serving_snapshot, _now, serving_observation, loaded_identity]
    code_rows = []
    compiled: dict[str, CodeType] = {}
    def fingerprint(function: Any) -> None:
        function = getattr(function, "__func__", function)
        code = function.__code__
        module = sys.modules[function.__module__]
        origin = str(Path(module.__spec__.origin).resolve(strict=True))
        if (Path(code.co_filename).resolve(strict=True) != Path(origin)
                or not Path(origin).is_relative_to(source_root)):
            raise IdentityUnavailable("identity_origin_mismatch")
        if origin not in compiled:
            compiled[origin] = compile(read(Path(origin), limit=1024 * 1024), code.co_filename,
                                       "exec", dont_inherit=True, optimize=sys.flags.optimize)
        on_disk = _find_code(compiled[origin], code.co_qualname)
        loaded_hash = _hash_metadata({"python": python, "code": _code_projection(code)})
        disk_hash = _hash_metadata({"python": python, "code": _code_projection(on_disk)})
        if loaded_hash != disk_hash:
            raise IdentityUnavailable("identity_loaded_source_mismatch")
        code_rows.append({"module": function.__module__, "qualname": code.co_qualname,
                          "origin": origin, "loaded_sha256": loaded_hash,
                          "source_code_sha256": disk_hash})
    for function in functions:
        fingerprint(function)

    if len(org.teams._teams) > MAX_IDENTITY_AGENTS or any(
            len(team.workers) > MAX_IDENTITY_AGENTS for team in org.teams._teams.values()):
        raise IdentityUnavailable("identity_work_limit")
    names = sorted(set(org.teams.all_agents()))
    if len(names) > MAX_IDENTITY_AGENTS:
        raise IdentityUnavailable("identity_cohort_unavailable")
    # This is the actual loaded TeamsRegistry, not a reconstructed replacement.
    registered = [{"team": team, "manager": org.teams.manager_for_team(team).name,
                   "workers": sorted(org.teams.manager_for_team(team).workers)} for team in org.teams.teams()]
    disk_teams = yaml.safe_load(read(org.root / "org" / "teams.yaml", limit=1024 * 1024))
    layout = disk_teams.get("teams") or {}
    disk_registered = [{"team": team, "manager": entry["manager"],
                        "workers": sorted(entry.get("workers") or [])} for team, entry in sorted(layout.items())]
    if registered != disk_registered:
        raise IdentityUnavailable("identity_cohort_mismatch")
    cohort = []
    profiles = {}
    registry = get_registry()
    binary_path = executor_binary_registry._registry_path()
    binaries: dict | None = None
    for name in names:
        definition = parse_agent_text(read(org.root / "org" / "agents" / f"{name}.md", limit=1024 * 1024).decode(),
                                      expected_name=name)
        if (definition.team not in org.teams.teams()
                or name not in (org.teams.manager_for_team(definition.team).name,
                                *org.teams.manager_for_team(definition.team).workers)
                or org.teams.is_team_manager(name) != (definition.role == "manager")):
            raise IdentityUnavailable("identity_cohort_unavailable")
        cohort.append({"agent": name, "team": definition.team, "role": definition.role,
                       "executor": definition.executor, "model": definition.model})
        profile = registry.get_profile(definition.executor)
        if profile is None:
            raise IdentityUnavailable("identity_profile_unavailable")
        if profile.name in profiles:
            continue
        if (profile.model_arg is not None and (len(profile.model_arg) > 16
                or any(not isinstance(arg, str) or len(arg) > 256 for arg in profile.model_arg))):
            raise IdentityUnavailable("identity_work_limit")
        metadata = {"name": profile.name, "kind": profile.kind,
                    "workspace_adapter_id": profile.workspace_adapter_id,
                    "command_adapter_id": profile.command_adapter_id,
                    "readiness_marker_fragment": profile.readiness_marker_fragment,
                    "model_arg_sha256": _hash_metadata(profile.model_arg),
                    "provider": None, "adapter": None}
        if profile.kind == "builtin":
            adapter_cls = get_first_party_adapter(profile.name)
            if adapter_cls is None:
                raise IdentityUnavailable("identity_adapter_unavailable")
            fingerprint(adapter_cls.build_argv)
            if binaries is None:
                binaries = json.loads(read(binary_path, limit=1024 * 1024))
                if not isinstance(binaries, dict):
                    raise IdentityUnavailable("identity_registry_unavailable")
            raw_path = binaries.get(profile.name)
            if not isinstance(raw_path, str) or not Path(raw_path).is_absolute():
                raise IdentityUnavailable("identity_provider_unavailable")
            provider = Path(raw_path).resolve(strict=True)
            metadata["provider"] = {"path": str(provider), "sha256": executable_hash(provider)}
        elif profile.kind == "custom":
            command = profile.command_adapter_id or ""
            if not command.startswith("custom-adapter:"):
                raise IdentityUnavailable("identity_adapter_unavailable")
            # Reuse the authoritative store path + entry parser, with a bounded
            # byte snapshot rather than its permissive/unbounded convenience GET.
            entries = yaml.safe_load(read(adapter_store._store_path(), limit=1024 * 1024))
            entry = adapter_store.AdapterEntry.from_dict(entries[command.split(":", 1)[1]])
            if (entry.status != "approved" or len(entry.dependencies) > 16
                    or type(entry.contract_version) is not int or entry.contract_version != 1
                    or (entry.dependency_manifest_version is not None
                        and (type(entry.dependency_manifest_version) is not int or entry.dependency_manifest_version < 1))):
                raise IdentityUnavailable("identity_adapter_unavailable")
            dependencies = []
            for dependency in [{"executable": entry.executable, "sha256": entry.executable_hash}, *entry.dependencies]:
                path = Path(dependency["executable"]).resolve(strict=True)
                digest = executable_hash(path)
                if digest != dependency["sha256"]:
                    raise IdentityUnavailable("identity_adapter_mismatch")
                dependencies.append({"path": str(path), "sha256": digest})
            metadata["adapter"] = {"id": entry.id, "version": entry.version,
                                   "contract_version": entry.contract_version,
                                   "dependency_manifest_version": entry.dependency_manifest_version,
                                   "dependencies": sorted(dependencies, key=lambda item: item["path"])}
        else:
            raise IdentityUnavailable("identity_profile_unavailable")
        profiles[profile.name] = metadata
    supervisor = org.orchestrator._host_supervisor
    if supervisor is None:
        backend = {"mode": "legacy", "name": None, "version": None, "capabilities": None}
    else:
        # Never call probe(): it can create a scope. The actual cached launch
        # capability observation must exist already or identity is unavailable.
        report = supervisor._capability_report
        if report is None:
            raise IdentityUnavailable("identity_backend_unavailable")
        fingerprint(supervisor._backend.launch)
        fingerprint(supervisor._backend.finish)
        backend = {"mode": "supervised", "name": report.backend,
                   "version": report.backend_version,
                   "capabilities": {key.value: value.value for key, value in sorted(report.capabilities.items())}}
    if _PACKAGE_METADATA_PATH is None:
        raise IdentityUnavailable("identity_package_unavailable")
    package_metadata = BytesHeaderParser().parsebytes(read(_PACKAGE_METADATA_PATH))
    package_versions = package_metadata.get_all("Version", [])
    if (package_metadata.get("Name") != "happyranch" or len(package_versions) != 1
            or not package_versions[0] or len(package_versions[0]) > 256):
        raise IdentityUnavailable("identity_package_unavailable")
    package_version = package_versions[0]
    root = org.root.resolve(strict=True)
    runtime_root = root.parent.parent if root.parent.name == "orgs" else root
    return {"source_root": str(source_root), "runtime_root": str(runtime_root), "org_root": str(root),
            "package_version": package_version, "python": python,
            "loaded_code": sorted(code_rows, key=lambda row: (row["module"], row["qualname"])),
            "files": [{"path": path, "sha256": digest} for path, digest in sorted(files.items())],
            "teams_sha256": _hash_metadata(registered), "cohort": cohort,
            "profiles": [profiles[name] for name in sorted(profiles)], "backend": backend}


def _serving_snapshot(observer: CollectionObserver) -> dict:
    # A held metadata lock is explicit unknown, never an unbounded GET wait.
    if not observer._lock.acquire(blocking=False):
        raise IdentityUnavailable("observer_busy")
    try:
        if len(observer._active) > MAX_IDENTITY_AGENTS:
            raise IdentityUnavailable("observer_work_limit")
        return observer._snapshot()
    finally:
        observer._lock.release()


def _serving_revision(observer: CollectionObserver) -> tuple[int, int, int]:
    # SELECT executes only after nonblocking admission to the existing DB lock;
    # release it before acquiring observer metadata or reading any files.
    if not observer.db._lock.acquire(blocking=False):
        raise IdentityUnavailable("observation_database_busy")
    try:
        return observer._read_revision()
    finally:
        observer.db._lock.release()


def _check_serving_snapshot(snapshot: dict) -> None:
    def integer(value: Any) -> bool:
        return type(value) is int and value >= 0
    def digest(value: Any) -> bool:
        return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)
    if (type(snapshot["contract_version"]) is not int or snapshot["contract_version"] != 1
            or not isinstance(snapshot["boot_id"], str)
            or str(uuid.UUID(snapshot["boot_id"])) != snapshot["boot_id"]
            or not integer(snapshot["generation"]) or not integer(snapshot["assigned_intents"])
            or not digest(snapshot["intent_digest"])
            or (snapshot["latest_seal_audit_id"] is not None
                and (not integer(snapshot["latest_seal_audit_id"]) or snapshot["latest_seal_audit_id"] == 0))
            or set(snapshot["phase_counts"]) != set(PHASES)
            or set(snapshot["phase_digests"]) != set(PHASES)):
        raise IdentityUnavailable("observer_snapshot_invalid")
    for phase in PHASES:
        counts, digests = snapshot["phase_counts"][phase], snapshot["phase_digests"][phase]
        if (set(counts) != {"attempted", "persisted"} or not all(integer(v) for v in counts.values())
                or set(digests) != {"attempted", "persisted"} or not all(digest(v) for v in digests.values())):
            raise IdentityUnavailable("observer_snapshot_invalid")


def serving_observation(org: Any) -> dict:
    """Closed current view, paired without a DB-held observer callback.

    A stable cutoff is evidence for a future validator, never a health decision.
    Acquisition exceptions do not poison the observer or launch/callback paths.
    """
    view = {"contract_version": 1, "org": org.slug, "boot_id": None,
            "installed_identity": None, "generation": None, "assigned_intents": None,
            "intent_digest": None, "phase_counts": None, "phase_digests": None,
            "active_preparations": None, "observation_error": None,
            "latest_seal_audit_id": None, "epoch_id": None, "epoch_audit_id": None,
            "sampled_at": _now(), "data_through": None}
    observer = org.memory_collection
    if observer is None:
        view["observation_error"] = org.memory_collection_unavailable or "observer_unavailable"
        return view
    try:
        if observer.org != org.slug or observer.root != str(org.root.resolve()) or observer.db is not org.db:
            raise IdentityUnavailable("observer_context_mismatch")
        opening = _serving_snapshot(observer)
        _check_serving_snapshot(opening)
        for key in ("boot_id", "generation", "assigned_intents", "intent_digest", "phase_counts",
                    "phase_digests", "active_preparations", "latest_seal_audit_id", "sampled_at", "observation_error"):
            view[key] = opening[key]
        revision = _serving_revision(observer)
        identity = loaded_identity(org)
        cutoff = _now()
        closing_identity = loaded_identity(org)
        closing_revision = _serving_revision(observer)
        closing = _serving_snapshot(observer)
        if (org.memory_collection is not observer or revision != closing_revision
                or identity != closing_identity
                or {k: v for k, v in opening.items() if k != "sampled_at"}
                != {k: v for k, v in closing.items() if k != "sampled_at"}):
            raise IdentityUnavailable("observation_moving")
        view["installed_identity"] = identity
        if opening["writer_active"] or opening["pending_observations"] or opening["active_preparations"]:
            view["observation_error"] = opening["observation_error"] or "observation_pending"
        if view["observation_error"] is None:
            view["data_through"] = cutoff
    except IdentityUnavailable as exc:
        view["observation_error"] = view["observation_error"] or str(exc)
    except Exception:
        view["observation_error"] = view["observation_error"] or "observation_unavailable"
    return view
