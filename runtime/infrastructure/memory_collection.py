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
import re
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

COLLECTION_TAG = "MemoryCollectionV1: "
MAX_ACCEPTANCE_BYTES = 1_048_576


class AcceptanceUnavailable(ValueError):
    """Category-only refusal; neither parsing nor a receipt grants authority."""


def _closed(value: Any, required: set[str], optional: set[str] = frozenset()) -> dict:
    if not isinstance(value, dict) or not required <= value.keys() or value.keys() - required - optional:
        raise AcceptanceUnavailable("acceptance_schema")
    return value


def _strict_json(text: str) -> Any:
    def members(pairs: list[tuple[str, Any]]) -> dict:
        result = {}
        for key, value in pairs:
            if key in result:
                raise AcceptanceUnavailable("acceptance_duplicate_key")
            result[key] = value
        return result
    try:
        return json.loads(text, object_pairs_hook=members,
                          parse_constant=lambda _: (_ for _ in ()).throw(AcceptanceUnavailable("acceptance_nonfinite")))
    except (TypeError, ValueError, RecursionError) as exc:
        raise AcceptanceUnavailable("acceptance_json") from exc


def _identifier(value: Any, prefix: str) -> None:
    if not isinstance(value, str) or re.fullmatch(prefix + r"-[0-9]{3,}", value) is None:
        raise AcceptanceUnavailable("acceptance_identifier")


def _sha256(value: Any) -> None:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise AcceptanceUnavailable("acceptance_digest")


def _reference(value: Any, *, own: bool = False) -> dict:
    _closed(value, {"task_id", "agent", "runtime_session_id"} | (set() if own else {"result_id"}),
            {"result_id"} if own else set())
    _identifier(value["task_id"], "TASK")
    for key in ("agent", "runtime_session_id"):
        if not isinstance(value[key], str) or not value[key].strip() or len(value[key]) > 256:
            raise AcceptanceUnavailable("acceptance_reference")
    if "result_id" in value and (type(value["result_id"]) is not int or value["result_id"] <= 0):
        raise AcceptanceUnavailable("acceptance_result_id")
    return value


def _identity_schema(value: Any) -> None:
    """Closed metadata shape; shape validity supplies no installed authority."""
    _closed(value, {"source_root", "runtime_root", "org_root", "package_version", "python", "loaded_code",
                    "files", "teams_sha256", "cohort", "profiles", "backend"})
    def text(item: Any, *, absolute: bool = False) -> None:
        if (not isinstance(item, str) or not item or len(item) > 4096
                or (absolute and not Path(item).is_absolute())):
            raise AcceptanceUnavailable("acceptance_identity_value")
    def rows(items: Any, maximum: int) -> list:
        if not isinstance(items, list) or not items or len(items) > maximum:
            raise AcceptanceUnavailable("acceptance_identity_array")
        return items
    for key in ("source_root", "runtime_root", "org_root"):
        text(value[key], absolute=True)
    text(value["package_version"])
    _sha256(value["teams_sha256"])
    _closed(value["python"], {"executable", "version", "implementation", "cache_tag"})
    for key, item in value["python"].items():
        text(item, absolute=key == "executable")
    files = rows(value["files"], MAX_IDENTITY_FILES)
    for row in files:
        _closed(row, {"path", "sha256"})
        text(row["path"], absolute=True)
        _sha256(row["sha256"])
    if [row["path"] for row in files] != sorted({row["path"] for row in files}):
        raise AcceptanceUnavailable("acceptance_identity_files")
    code = rows(value["loaded_code"], 256)
    for row in code:
        _closed(row, {"module", "qualname", "origin", "loaded_sha256", "source_code_sha256"})
        for key in ("module", "qualname", "origin"):
            text(row[key], absolute=key == "origin")
        for key in ("loaded_sha256", "source_code_sha256"):
            _sha256(row[key])
        if row["loaded_sha256"] != row["source_code_sha256"]:
            raise AcceptanceUnavailable("acceptance_identity_loaded_code")
    if [(r["module"], r["qualname"]) for r in code] != sorted({(r["module"], r["qualname"]) for r in code}):
        raise AcceptanceUnavailable("acceptance_identity_code")
    cohort = rows(value["cohort"], MAX_IDENTITY_AGENTS)
    for row in cohort:
        _closed(row, {"agent", "team", "role", "executor", "model"})
        for key in ("agent", "team", "role", "executor"):
            text(row[key])
        if row["role"] not in {"manager", "worker"}:
            raise AcceptanceUnavailable("acceptance_identity_role")
        if row["model"] is not None:
            text(row["model"])
    if [r["agent"] for r in cohort] != sorted({r["agent"] for r in cohort}):
        raise AcceptanceUnavailable("acceptance_identity_cohort")
    profiles = rows(value["profiles"], MAX_IDENTITY_AGENTS)
    for row in profiles:
        _closed(row, {"name", "kind", "workspace_adapter_id", "command_adapter_id", "readiness_marker_fragment",
                      "model_arg_sha256", "provider", "adapter"})
        for key in ("name", "kind", "workspace_adapter_id"):
            text(row[key])
        if row["command_adapter_id"] is not None:
            text(row["command_adapter_id"])
        if row["readiness_marker_fragment"] is not None:
            text(row["readiness_marker_fragment"])
        _sha256(row["model_arg_sha256"])
        if row["kind"] == "builtin":
            if row["adapter"] is not None:
                raise AcceptanceUnavailable("acceptance_identity_adapter")
            _closed(row["provider"], {"path", "sha256"})
            text(row["provider"]["path"], absolute=True)
            _sha256(row["provider"]["sha256"])
        elif row["kind"] == "custom":
            if row["provider"] is not None:
                raise AcceptanceUnavailable("acceptance_identity_provider")
            adapter = _closed(row["adapter"], {"id", "version", "contract_version", "dependency_manifest_version", "dependencies"})
            text(adapter["id"])
            text(adapter["version"])
            if (type(adapter["contract_version"]) is not int or adapter["contract_version"] != 1
                    or (adapter["dependency_manifest_version"] is not None and
                        (type(adapter["dependency_manifest_version"]) is not int or adapter["dependency_manifest_version"] <= 0))):
                raise AcceptanceUnavailable("acceptance_identity_adapter_version")
            for dependency in rows(adapter["dependencies"], 17):
                _closed(dependency, {"path", "sha256"})
                text(dependency["path"], absolute=True)
                _sha256(dependency["sha256"])
        else:
            raise AcceptanceUnavailable("acceptance_identity_profile_kind")
    if ([r["name"] for r in profiles] != sorted({r["name"] for r in profiles})
            or {r["executor"] for r in cohort} != {r["name"] for r in profiles}):
        raise AcceptanceUnavailable("acceptance_identity_profiles")
    backend = _closed(value["backend"], {"mode", "name", "version", "capabilities"})
    if backend["mode"] == "legacy":
        if any(backend[key] is not None for key in ("name", "version", "capabilities")):
            raise AcceptanceUnavailable("acceptance_identity_backend")
    elif backend["mode"] == "supervised":
        text(backend["name"])
        if backend["version"] is not None:
            text(backend["version"])
        if not isinstance(backend["capabilities"], dict) or not backend["capabilities"] or len(backend["capabilities"]) > 64:
            raise AcceptanceUnavailable("acceptance_identity_capabilities")
        for key, item in backend["capabilities"].items():
            text(key)
            text(item)
    else:
        raise AcceptanceUnavailable("acceptance_identity_backend")


def parse_acceptance(summary: str) -> dict | None:
    """Parse the closed opt-in carrier only; untagged history is unchanged."""
    if not isinstance(summary, str):
        raise AcceptanceUnavailable("acceptance_summary")
    if "MemoryCollectionV1:" not in summary:
        return None
    try:
        if len(summary.encode("utf-8")) > MAX_ACCEPTANCE_BYTES:
            raise AcceptanceUnavailable("acceptance_work_limit")
        lines = [line for line in summary.splitlines() if "MemoryCollectionV1:" in line]
        if len(lines) != 1 or not lines[0].startswith(COLLECTION_TAG):
            raise AcceptanceUnavailable("acceptance_tag")
        raw = lines[0][len(COLLECTION_TAG):]
        value = _strict_json(raw)
        common = {"contract_version", "kind", "org", "operational_root_task_id",
                  "health_definition_sha256", "release_manifest_sha256", "installed_identity",
                  "cohort", "applicable_paths", "synthetic_task_ids", "probe_receipts", "result_ref"}
        kind = value.get("kind") if isinstance(value, dict) else None
        extra = {"venue"} if kind == "installed_qa" else {"action", "qa_ref", "predecessor_epoch_id", "reason"}
        _closed(value, common | extra)
        if type(value["contract_version"]) is not int or value["contract_version"] != 1:
            raise AcceptanceUnavailable("acceptance_version")
        if kind not in {"installed_qa", "manager_acceptance"}:
            raise AcceptanceUnavailable("acceptance_kind")
        if not isinstance(value["org"], str) or not value["org"].strip():
            raise AcceptanceUnavailable("acceptance_org")
        _identifier(value["operational_root_task_id"], "TASK")
        for key in ("health_definition_sha256", "release_manifest_sha256"):
            _sha256(value[key])
        _reference(value["result_ref"], own=True)
        if kind == "installed_qa":
            if value["venue"] != "installed":
                raise AcceptanceUnavailable("acceptance_venue")
        else:
            _reference(value["qa_ref"])
            if value["action"] not in {"accept", "invalidate"}:
                raise AcceptanceUnavailable("acceptance_action")
            if value["predecessor_epoch_id"] is not None:
                _sha256(value["predecessor_epoch_id"])
            if not isinstance(value["reason"], str) or not value["reason"].strip() or len(value["reason"]) > 2048:
                raise AcceptanceUnavailable("acceptance_reason")
        _identity_schema(value["installed_identity"])
        if value["cohort"] != value["installed_identity"]["cohort"]:
            raise AcceptanceUnavailable("acceptance_cohort")
        for key in ("cohort", "applicable_paths", "synthetic_task_ids", "probe_receipts"):
            items = value[key]
            if not isinstance(items, list) or not items or len(items) > MAX_IDENTITY_AGENTS:
                raise AcceptanceUnavailable("acceptance_array")
            encoded = [json.dumps(item, sort_keys=True, separators=(",", ":"), allow_nan=False) for item in items]
            if encoded != sorted(set(encoded)):
                raise AcceptanceUnavailable("acceptance_array_order")
        if any(not isinstance(path, str) or not path or len(path) > 512 for path in value["applicable_paths"]):
            raise AcceptanceUnavailable("acceptance_path")
        for task_id in value["synthetic_task_ids"]:
            _identifier(task_id, "TASK")
        for receipt in value["probe_receipts"]:
            _closed(receipt, {"path", "root", "child", "job_id", "script_sha256", "output_sha256"})
            if not isinstance(receipt["path"], str) or receipt["path"] not in value["applicable_paths"]:
                raise AcceptanceUnavailable("acceptance_probe_path")
            _identifier(receipt["job_id"], "JOB")
            for key in ("script_sha256", "output_sha256"):
                _sha256(receipt[key])
            for key in ("root", "child"):
                _closed(receipt[key], {"org", "agent", "task_id", "runtime_session_id"})
                _identifier(receipt[key]["task_id"], "TASK")
                if any(not isinstance(receipt[key][field], str) or not receipt[key][field]
                       or len(receipt[key][field]) > 256 for field in ("org", "agent", "runtime_session_id")):
                    raise AcceptanceUnavailable("acceptance_probe_tuple")
        if raw != json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False):
            raise AcceptanceUnavailable("acceptance_not_canonical")
        return value
    except (UnicodeError, TypeError, ValueError, RecursionError) as exc:
        raise AcceptanceUnavailable("acceptance_schema") from exc


def normalize_own_reference(candidate: dict, admitted_row: dict) -> dict:
    """Derive only the own ID/time from the explicitly selected immutable row."""
    own = _reference(candidate["result_ref"], own=True)
    if (type(admitted_row.get("id")) is not int or admitted_row["id"] <= 0
            or admitted_row.get("status") != "completed"
            or own["task_id"] != admitted_row.get("task_id")
            or own["agent"] != admitted_row.get("agent")
            or own["runtime_session_id"] != admitted_row.get("session_id")
            or ("result_id" in own and own["result_id"] != admitted_row["id"])
            or parse_acceptance(admitted_row.get("output_summary")) != candidate):
        raise AcceptanceUnavailable("acceptance_own_row")
    normalized = copy.deepcopy(candidate)
    normalized["result_ref"]["result_id"] = admitted_row["id"]
    normalized["published_at"] = admitted_row["created_at"]
    return normalized


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
        self._transition_lock = threading.Lock()
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

    def reconcile_acceptance(self, result_row_id: int) -> dict | None:
        context = getattr(self, "context", None)
        if context is None or context.db is not self.db or context.memory_collection is not self:
            return None
        return reconcile_acceptance(context, result_row_id)

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
                 org.orchestrator._build_executor, MemoryStore.render_memory_digest, CollectionObserver.__init__, CollectionObserver.begin,
                 CollectionObserver.observe, CollectionObserver.expectation,
                 CollectionObserver.snapshot, CollectionObserver._snapshot,
                 CollectionObserver._record, CollectionObserver._persist, CollectionObserver._seal,
                 _identity_bytes, _identity_file_hash, _hash_metadata, _code_projection, _find_code, _serving_snapshot,
                 _serving_revision, _check_serving_snapshot, _now, serving_observation, loaded_identity]
    from runtime.infrastructure.memory_telemetry_report import reduce_report, reduce_collection_report
    from runtime.infrastructure.audit_logger import AuditLogger
    from cli.commands.learning import cmd_memory_report, _compute_report, _print_report
    functions.extend([org.orchestrator._log_step_result, org.memory_collection.reconcile_acceptance,
                      org.memory_collection.context.memory_collection_observation,
                      inspect.unwrap(Database.read_memory_collection_evidence), Database.append_memory_collection_transition,
                      inspect.unwrap(Database.verify_retry_link), Database._retry_object, Database._retry_audits,
                      Database._retry_require_audit, Database._retry_manager_edge,
                      Database._retry_escalation_edge, Database._retry_dispatch_edge,
                      parse_acceptance, normalize_own_reference, _identity_schema, _closed, _strict_json, _identifier, _sha256, _reference,
                      collection_record_projection, collection_control_head, collection_logical_key, equivalent_collection_control, _decoded_tables,
                      _one, _decision, _exact_role_result, _approved_probe_plan, _validate_job, _resolve_probe_slots,
                      _validate_probe_operations, _census_from_view, validate_acceptance_evidence, _view_projection,
                      _referenced_probe_jobs, _local_job_outputs, _registered_cohort, validate_epoch_candidate, prepare_acceptance, reconcile_acceptance, acquire_collection_report,
                      _collection_pages, _http_collection_capture, acquire_http_collection_report,
                      current_epoch_references, reduce_report, reduce_collection_report, AuditLogger.compute_memory_telemetry_report,
                      cmd_memory_report, _compute_report, _print_report])
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
    disk_registered = [{"team": team, "manager": (entry["manager"] if isinstance(entry["manager"], str) else
                                    entry["manager"]["principal"] if entry["manager"]["kind"] == "agent" else None),
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


CONTROL_ACTIONS = frozenset({"memory_collection_epoch_started", "memory_collection_invalidated"})


def collection_record_projection(tables: dict) -> str:
    """Immutable comparison includes originals, jobs and exact admitted results."""
    return json.dumps({key: [row for row in rows if key != "audit_log" or row["action"] not in CONTROL_ACTIONS]
                       for key, rows in tables.items()}, sort_keys=True, separators=(",", ":"), allow_nan=False)


def collection_logical_key(candidate: dict) -> str:
    return _hash_metadata({key: value for key, value in candidate.items()
                           if key not in {"result_ref", "published_at", "reason"}})


def equivalent_collection_control(tables: dict, candidate: dict) -> dict | None:
    """Authenticate the chain before selecting an immutable replay boundary.

    Equivalence cannot revive a withdrawn or superseded start. Each proposed
    manager row is still validated separately against the original admission.
    """
    controls = [row for row in tables["audit_log"] if row["action"] in CONTROL_ACTIONS]
    head = collection_control_head(controls, candidate["org"], candidate["operational_root_task_id"])
    key = collection_logical_key(candidate)
    matches = [row for row in controls if
               (_strict_json(row["payload"]) if isinstance(row["payload"], str) else row["payload"])["logical_key"] == key]
    if not matches:
        return None
    original = _one(matches, "acceptance_equivalent_ambiguous")
    if head is None or head["id"] != original["id"]:
        raise AcceptanceUnavailable("acceptance_equivalent_inactive")
    return head


def collection_control_head(rows: list[dict], org: str, root: str) -> dict | None:
    """Never fall back across a malformed, branched or foreign-root control."""
    head = None
    for raw in sorted(rows, key=lambda row: row["id"]):
        body = _strict_json(raw["payload"]) if isinstance(raw["payload"], str) else raw["payload"]
        _closed(body, {"contract_version", "org", "operational_root_task_id", "epoch_id",
                       "predecessor_epoch_id", "logical_key", "manager_ref", "qa_ref",
                       "boot_id", "base_assigned_intents", "accepted_at", "projection"})
        if type(body["contract_version"]) is not int or body["contract_version"] != 1 or body["org"] != org or body["operational_root_task_id"] != root:
            raise AcceptanceUnavailable("acceptance_control_context")
        for key in ("epoch_id", "logical_key"):
            _sha256(body[key])
        _reference(body["manager_ref"])
        _reference(body["qa_ref"])
        if (body["manager_ref"]["task_id"] != raw["task_id"] or body["manager_ref"]["agent"] != raw["agent"]
                or body["predecessor_epoch_id"] != (head["payload"]["epoch_id"] if head else None)):
            raise AcceptanceUnavailable("acceptance_control_chain")
        if raw["action"] == "memory_collection_invalidated":
            if head is None or body["epoch_id"] != head["payload"]["epoch_id"]:
                raise AcceptanceUnavailable("acceptance_control_invalidation")
        elif raw["action"] != "memory_collection_epoch_started":
            raise AcceptanceUnavailable("acceptance_control_action")
        head = {**raw, "payload": body}
    return head


def _decoded_tables(tables: dict) -> dict:
    value = copy.deepcopy(tables)
    for row in value["audit_log"]:
        if isinstance(row["payload"], str):
            row["payload"] = _strict_json(row["payload"])
    return value


def _one(rows: list[dict], category: str) -> dict:
    if len(rows) != 1:
        raise AcceptanceUnavailable(category)
    return rows[0]


def _decision(row: dict) -> dict:
    from runtime.models import NextStep
    body = _strict_json(row["decision_json"])
    if not isinstance(body, dict):
        raise AcceptanceUnavailable("acceptance_decision")
    body = {key: value for key, value in body.items() if key != "_manager_self_evaluation"}
    return NextStep.model_validate(body).model_dump(exclude_none=True)


def _exact_role_result(data: dict, ref: dict, view: dict, *, role: str, root: str, withdrawal: bool = False) -> tuple[dict, dict]:
    _reference(ref)
    result = _one([row for row in data["task_results"] if row["id"] == ref["result_id"]], "acceptance_result_missing")
    task = _one([row for row in data["tasks"] if row["id"] == ref["task_id"]], "acceptance_task_missing")
    member = _one([row for row in view["installed_identity"]["cohort"] if row["agent"] == ref["agent"]], "acceptance_role_missing")
    if (result["task_id"] != ref["task_id"] or result["agent"] != ref["agent"] or result["session_id"] != ref["runtime_session_id"]
            or result["status"] != "completed" or task["assigned_agent"] != ref["agent"] or member["role"] != role
            or task["team"] != member["team"] or task["cancelled_at"] is not None
            or (task["status"] == "failed" and not withdrawal)):
        raise AcceptanceUnavailable("acceptance_role_binding")
    cursor, seen = task, set()
    while cursor["id"] != root:
        if cursor["id"] in seen or len(seen) >= MAX_IDENTITY_AGENTS or cursor["parent_task_id"] is None:
            raise AcceptanceUnavailable("acceptance_lineage")
        seen.add(cursor["id"])
        cursor = _one([row for row in data["tasks"] if row["id"] == cursor["parent_task_id"]], "acceptance_lineage")
    if role == "manager" and (cursor["assigned_agent"] != ref["agent"] or cursor["parent_task_id"] is not None
            or len([m for m in view["installed_identity"]["cohort"] if m["team"] == member["team"] and m["role"] == "manager"]) != 1):
        raise AcceptanceUnavailable("acceptance_manager")
    identities = [row for row in data["audit_log"] if row["action"] == "memory_runtime_identity"
                  and row["task_id"] == ref["task_id"] and row["agent"] == ref["agent"]
                  and row["payload"].get("session_id") == ref["runtime_session_id"]]
    identity = _one(identities, "acceptance_admission_identity")
    from runtime.infrastructure.memory_telemetry_report import aware_utc
    start = _one([row for row in data["audit_log"] if row["action"] == "session_start"
                  and row["task_id"] == ref["task_id"] and row["agent"] == ref["agent"]
                  and row["payload"].get("session_id") == ref["runtime_session_id"]], "acceptance_admission_start")
    facts = identity["payload"]
    if (facts.get("population") == "recovery" or facts.get("parent_known") is not True
            or facts.get("parent_task_id") != task["parent_task_id"]
            or facts.get("task_type") != task["task_type"]
            or facts.get("executor") != member["executor"] or facts.get("model") != member["model"]
            or any(start["payload"].get(key) != facts.get(key) for key in ("executor", "model", "invocation_purpose"))
            or not (aware_utc(task["created_at"]) <= aware_utc(identity["timestamp"])
                    <= aware_utc(start["timestamp"]) <= aware_utc(result["created_at"]))):
        raise AcceptanceUnavailable("acceptance_admission_binding")
    return result, task


def _approved_probe_plan(data: dict, candidate: dict, qa_task: dict, qa_result: dict, view: dict) -> dict:
    """Whole original prompt plus unique prior real delegation approves intent."""
    from runtime.infrastructure.memory_telemetry_report import aware_utc
    root = candidate["operational_root_task_id"]
    matches = []
    for result in data["task_results"]:
        if result["task_id"] != root or not result["decision_json"]:
            continue
        decision = _decision(result)
        if decision.get("action") == "delegate" and decision.get("agent") == qa_result["agent"] and decision.get("prompt") == qa_task["brief"]:
            matches.append((result, decision))
    if len(matches) != 1:
        raise AcceptanceUnavailable("acceptance_prior_plan")
    prior, decision = matches[0]
    _exact_role_result(data, {"task_id": prior["task_id"], "result_id": prior["id"], "agent": prior["agent"],
                             "runtime_session_id": prior["session_id"]}, view, role="manager", root=root)
    step = _one([row for row in data["audit_log"] if row["action"] == "orchestration_step" and row["task_id"] == root
                 and row["payload"].get("decision") == decision], "acceptance_prior_step")
    if qa_task["parent_task_id"] != root or not (aware_utc(prior["created_at"]) <= aware_utc(step["timestamp"]) <= aware_utc(qa_task["created_at"])):
        raise AcceptanceUnavailable("acceptance_prior_order")
    plan = _strict_json(qa_task["brief"])
    _closed(plan, {"run_id", "command", "slots"})
    _closed(plan["command"], {"script_text", "interpreter", "cwd_resolved"})
    if not isinstance(plan["run_id"], str) or not plan["run_id"] or not isinstance(plan["slots"], list) or not plan["slots"] or len(plan["slots"]) > MAX_IDENTITY_AGENTS:
        raise AcceptanceUnavailable("acceptance_plan_schema")
    command = plan["command"]
    if (any(not isinstance(item, str) or not item for item in command.values())
            or len(command["script_text"].encode()) > 65536 or not Path(command["cwd_resolved"]).is_absolute()
            or len({slot.get("path") for slot in plan["slots"]}) != len(plan["slots"])):
        raise AcceptanceUnavailable("acceptance_plan_command")
    for slot in plan["slots"]:
        _closed(slot, {"path", "root", "child"})
        for kind in ("root", "child"):
            _closed(slot[kind], {"agent", "team", "brief"})
            if any(not isinstance(value, str) or not value for value in slot[kind].values()):
                raise AcceptanceUnavailable("acceptance_plan_slot")
    return plan


HEALTH_DEFINITION_SHA256 = hashlib.sha256(
    b'H-v1:complete-intent-and-expectation-census;zero-false-credit;per-path-root-child:2/4/2;admission-age<=48h-at-final-commit;original-epoch-current-health;installed-identity;no-stitching'
).hexdigest()


def _validate_job(data: dict, receipt: dict, qa_result: dict, qa_task: dict, plan: dict, output: dict) -> tuple[dict, dict]:
    from runtime.infrastructure.memory_telemetry_report import aware_utc
    job = _one([row for row in data["jobs"] if row["id"] == receipt["job_id"]], "acceptance_job_missing")
    command = plan["command"]
    if (job["task_id"] != qa_task["id"] or job["agent_name"] != qa_result["agent"]
            or job["status"] != "completed" or type(job["exit_code"]) is not int or job["exit_code"] != 0
            or job["reason"] is not None or job["script_text"] != command["script_text"]
            or job["interpreter"] != command["interpreter"] or job["cwd_resolved"] != command["cwd_resolved"]
            or hashlib.sha256(job["script_text"].encode()).hexdigest() != receipt["script_sha256"]):
        raise AcceptanceUnavailable("acceptance_job_identity")
    if not (aware_utc(qa_task["created_at"]) <= aware_utc(job["created_at"]) <= aware_utc(job["started_at"])
            <= aware_utc(job["finished_at"]) <= aware_utc(qa_result["created_at"])):
        raise AcceptanceUnavailable("acceptance_job_order")
    events = [row for row in data["audit_log"] if isinstance(row["payload"], dict)
              and row["payload"].get("script_request_id") == job["id"]]
    submitted = _one([row for row in events if row["action"] == "job_submitted"], "acceptance_job_submit")
    started = _one([row for row in events if row["action"] in {"job_auto_started", "job_run_started"}], "acceptance_job_start")
    finished = _one([row for row in events if row["action"] == "job_run_completed"], "acceptance_job_finish")
    sessions = [row for row in data["audit_log"] if row["action"] == "memory_runtime_identity"
                and row["task_id"] == qa_task["id"] and row["agent"] == qa_result["agent"]
                and aware_utc(row["timestamp"]) <= aware_utc(submitted["timestamp"])]
    if not sessions:
        raise AcceptanceUnavailable("acceptance_job_session")
    latest_session = max(sessions, key=lambda row: row["id"])
    if (latest_session["payload"].get("session_id") != qa_result["session_id"]
            or any(row["action"] == "memory_runtime_identity" and row["task_id"] == qa_task["id"]
                   and row["agent"] == qa_result["agent"] and row["payload"].get("session_id") != qa_result["session_id"]
                   and aware_utc(submitted["timestamp"]) <= aware_utc(row["timestamp"]) <= aware_utc(qa_result["created_at"])
                   for row in data["audit_log"])):
        raise AcceptanceUnavailable("acceptance_job_session")
    if any(row["task_id"] != qa_task["id"] for row in (submitted, started, finished)) or submitted["agent"] != qa_result["agent"]:
        raise AcceptanceUnavailable("acceptance_job_owner")
    if (submitted["payload"].get("interpreter") != job["interpreter"]
            or submitted["payload"].get("byte_size") != len(job["script_text"].encode())
            or started["payload"].get("interpreter") != job["interpreter"]
            or started["payload"].get("cwd_resolved") != job["cwd_resolved"]
            or finished["payload"].get("exit_code") != 0
            or not (aware_utc(submitted["timestamp"]) <= aware_utc(started["timestamp"])
                    <= aware_utc(finished["timestamp"]) <= aware_utc(qa_result["created_at"]))):
        raise AcceptanceUnavailable("acceptance_job_audit")
    _closed(output, {"stdout", "stderr", "truncated_stdout", "truncated_stderr", "total_stdout_bytes", "total_stderr_bytes"})
    for stream in ("stdout", "stderr"):
        text, total = output[stream], output[f"total_{stream}_bytes"]
        if (not isinstance(text, str) or "\ufffd" in text or type(total) is not int or total <= 0
                or len(text.encode("utf-8")) != total or output[f"truncated_{stream}"] is not False
                or finished["payload"].get(f"{stream}_bytes") != total
                or finished["payload"].get(f"truncated_{stream}") is not False):
            raise AcceptanceUnavailable("acceptance_job_output")
    if not output["stdout"] or _hash_metadata(output) != receipt["output_sha256"]:
        raise AcceptanceUnavailable("acceptance_job_output_digest")
    transcript = _strict_json(output["stdout"])
    _closed(transcript, {"run_id", "returned_task_ids", "operation_audit_ids", "serving_observation", "cli_identity"})
    if transcript["run_id"] != plan["run_id"]:
        raise AcceptanceUnavailable("acceptance_job_plan")
    return job, transcript


def _resolve_probe_slots(data: dict, plan: dict, receipts: list[dict], jobs: dict, transcripts: dict,
                         *, retry_verifier: Any = None) -> set[str]:
    from runtime.infrastructure.memory_telemetry_report import aware_utc
    designated, per_job = set(), {}
    if len(plan["slots"]) != len(receipts) or len({slot["path"] for slot in plan["slots"]}) != len(plan["slots"]):
        raise AcceptanceUnavailable("acceptance_slot_count")
    for slot in plan["slots"]:
        receipt = _one([row for row in receipts if row["path"] == slot["path"]], "acceptance_slot_path")
        job, transcript = jobs[receipt["job_id"]], transcripts[receipt["job_id"]]
        original = slot["root"]
        roots = [row for row in data["tasks"] if row["parent_task_id"] is None
                 and row["brief"] == original["brief"] and row["assigned_agent"] == original["agent"]
                 and row["team"] == original["team"]
                 and aware_utc(job["started_at"]) <= aware_utc(row["created_at"]) <= aware_utc(job["finished_at"])]
        root = _one(roots, "acceptance_root_slot_ambiguous")
        child_template = slot["child"]
        child = _one([row for row in data["tasks"] if row["parent_task_id"] == root["id"]
                      and row["brief"] == child_template["brief"] and row["assigned_agent"] == child_template["agent"]
                      and row["team"] == child_template["team"]], "acceptance_child_slot_ambiguous")
        parent_decisions = []
        for result in data["task_results"]:
            if result["task_id"] != root["id"] or not result["decision_json"]:
                continue
            decision = _decision(result)
            if decision.get("action") == "delegate" and decision.get("agent") == child["assigned_agent"] and decision.get("prompt") == child["brief"]:
                parent_decisions.append((result, decision))
        if len(parent_decisions) != 1:
            raise AcceptanceUnavailable("acceptance_child_delegation")
        parent_result, decision = parent_decisions[0]
        if (parent_result["status"] != "completed" or parent_result["agent"] != root["assigned_agent"]
                or parent_result["session_id"] != receipt["root"]["runtime_session_id"]):
            raise AcceptanceUnavailable("acceptance_child_parent_result")
        parent_identity = _one([row for row in data["audit_log"] if row["action"] == "memory_runtime_identity"
                               and row["task_id"] == root["id"] and row["agent"] == parent_result["agent"]
                               and row["payload"].get("session_id") == parent_result["session_id"]], "acceptance_child_parent_identity")
        if parent_identity["payload"].get("population") != "root":
            raise AcceptanceUnavailable("acceptance_child_parent_identity")
        step = _one([row for row in data["audit_log"] if row["action"] == "orchestration_step" and row["task_id"] == root["id"]
                     and row["payload"].get("decision") == decision], "acceptance_child_step")
        if not (aware_utc(parent_result["created_at"]) <= aware_utc(step["timestamp"]) <= aware_utc(child["created_at"]) <= aware_utc(job["finished_at"])):
            raise AcceptanceUnavailable("acceptance_child_order")
        for kind, task in (("root", root), ("child", child)):
            claimed = receipt[kind]
            if (task["id"] in designated or claimed["task_id"] != task["id"] or claimed["agent"] != task["assigned_agent"]
                    or claimed["task_id"] not in transcript["returned_task_ids"]):
                raise AcceptanceUnavailable("acceptance_slot_mapping")
            designated.add(task["id"])
            per_job.setdefault(receipt["job_id"], set()).add(task["id"])
    # A cross-root closure requires the existing server verifier, including
    # its raw manager/escalation/thread joins. Public audit text is not a
    # replacement for those records. Supporting roots are excluded too.
    while True:
        extra = set()
        for task in data["tasks"]:
            predecessor = task["revisit_of_task_id"]
            if predecessor not in designated or task["id"] in designated:
                continue
            previous = _one([row for row in data["tasks"] if row["id"] == predecessor], "acceptance_retry_missing")
            if previous["status"] != "failed" or task["assigned_agent"] != previous["assigned_agent"]:
                raise AcceptanceUnavailable("acceptance_retry_lineage")
            roots = ()
            if retry_verifier is not None:
                from runtime.infrastructure.db.tasks import VerifiedRetry
                verified = retry_verifier(task["parent_task_id"], task["assigned_agent"], predecessor)
                if not isinstance(verified, VerifiedRetry):
                    raise AcceptanceUnavailable("acceptance_retry_lineage")
                roots = verified.path
                if (not roots or len(roots) > 20 or len(set(roots)) != len(roots)
                        or roots[0] != task["parent_task_id"] or roots[-1] != previous["parent_task_id"]):
                    raise AcceptanceUnavailable("acceptance_retry_lineage")
                for root_id in roots:
                    _one([row for row in data["tasks"] if row["id"] == root_id], "acceptance_retry_missing")
            elif task["parent_task_id"] != previous["parent_task_id"]:
                raise AcceptanceUnavailable("acceptance_retry_lineage")
            extra.update((task["id"], *roots))
            for expected in per_job.values():
                if predecessor in expected:
                    expected.update((task["id"], *roots))
        if not extra:
            break
        designated.update(extra)
        if len(designated) > MAX_IDENTITY_AGENTS:
            raise AcceptanceUnavailable("acceptance_retry_limit")
    for job_id, expected in per_job.items():
        returned = transcripts[job_id]["returned_task_ids"]
        if (not isinstance(returned, list) or any(not isinstance(task, str) for task in returned)
                or len(returned) != len(set(returned)) or set(returned) != expected):
            raise AcceptanceUnavailable("acceptance_returned_task_set")
    return designated


def _validate_probe_operations(data: dict, receipt: dict, transcript: dict, view: dict) -> set[int]:
    selected = []
    for kind in ("root", "child"):
        claim = receipt[kind]
        if claim["org"] != view["org"]:
            raise AcceptanceUnavailable("acceptance_probe_org")
        key = (claim["task_id"], claim["agent"], claim["runtime_session_id"])
        own = [row for row in data["audit_log"] if isinstance(row["payload"], dict)
               and row["payload"].get("session_id") == key[2]]
        # Scope is operation-specific. Redundant payload fields may never
        # rescue a wrong row scope or disagree with the admitted task tuple.
        for row in own:
            if row["action"] not in {"memory_runtime_identity", "memory_runtime_terminal",
                                     "memory_digest_impression", "memory_read", "memory_search"}:
                continue
            payload = row["payload"]
            scope = f"AGENT-{key[1]}" if row["action"] == "memory_read" else key[0]
            if (row["task_id"] != scope or row["agent"] != key[1]
                    or ("agent" in payload and payload["agent"] != key[1])
                    or ("task_id" in payload and payload["task_id"] != key[0])
                    or (row["action"] in {"memory_read", "memory_search"} and payload.get("task_id") != key[0])):
                raise AcceptanceUnavailable("acceptance_probe_binding")
        identity = _one([row for row in own if row["action"] == "memory_runtime_identity"], "acceptance_probe_identity")
        terminal = _one([row for row in own if row["action"] == "memory_runtime_terminal"], "acceptance_probe_terminal")
        member = _one([row for row in view["installed_identity"]["cohort"] if row["agent"] == key[1]], "acceptance_probe_member")
        task = _one([row for row in data["tasks"] if row["id"] == key[0]], "acceptance_probe_task")
        if (task["status"] != "completed" or task["cancelled_at"] is not None
                or task["assigned_agent"] != key[1] or task["team"] != member["team"]):
            raise AcceptanceUnavailable("acceptance_probe_task")
        expected_path = f"{member['executor']}:{view['installed_identity']['backend']['mode']}:{view['installed_identity']['backend']['name'] or 'none'}"
        if (identity["payload"].get("boot_id") != view["boot_id"] or terminal["payload"].get("success") is not True
                or identity["payload"].get("executor") != member["executor"]
                or identity["payload"].get("model") != member["model"] or receipt["path"] != expected_path
                or identity["payload"].get("population") != kind
                or identity["payload"].get("parent_known") is not True):
            raise AcceptanceUnavailable("acceptance_probe_success")
        impression = _one([row for row in own if row["action"] == "memory_digest_impression"], "acceptance_probe_impression")
        reads = [row for row in own if row["action"] == "memory_read"]
        searches = [row for row in own if row["action"] == "memory_search"]
        if len(reads) != 2 or len(searches) != 1:
            raise AcceptanceUnavailable("acceptance_probe_operations")
        shown = set(impression["payload"]["digest_ids"])
        digest_read = _one([row for row in reads if row["payload"].get("source") == "digest"], "acceptance_probe_shown")
        searched_read = _one([row for row in reads if row["payload"].get("source") == "search"], "acceptance_probe_nonshown")
        if (digest_read["payload"].get("id") not in shown or searched_read["payload"].get("id") in shown
                or searched_read["payload"].get("id") not in searches[0]["payload"]["memory_ids"]
                or not identity["id"] < impression["id"] < digest_read["id"] < searches[0]["id"] < searched_read["id"] < terminal["id"]):
            raise AcceptanceUnavailable("acceptance_probe_source_order")
        from runtime.infrastructure.memory_telemetry_report import aware_utc
        ordered = (identity, impression, digest_read, searches[0], searched_read, terminal)
        if any(aware_utc(before["timestamp"]) > aware_utc(after["timestamp"])
               for before, after in zip(ordered, ordered[1:])):
            raise AcceptanceUnavailable("acceptance_probe_source_order")
        selected.extend([impression["id"], digest_read["id"], searches[0]["id"], searched_read["id"]])
    claimed_ids = transcript["operation_audit_ids"]
    if (not isinstance(claimed_ids, list) or any(type(item) is not int or item <= 0 for item in claimed_ids)
            or len(set(claimed_ids)) != len(claimed_ids) or not set(selected) <= set(claimed_ids)):
        raise AcceptanceUnavailable("acceptance_probe_output_refs")
    return set(selected)


def _census_from_view(view: dict, rows: list[dict]) -> dict:
    own = [row for row in rows if isinstance(row["payload"], dict) and row["payload"].get("boot_id") == view["boot_id"]]
    seals = [row for row in own if row["action"] == "memory_collection_seal"]
    last = _one([row for row in seals if row["id"] == view["latest_seal_audit_id"]], "acceptance_current_seal")
    digest = EMPTY_DIGEST
    for index, row in enumerate(seals):
        body = row["payload"]
        if body["seal_attempts"] != index + 1 or body["seal_persisted"] != index or body["seal_digest"] != digest:
            raise AcceptanceUnavailable("acceptance_seal_history")
        digest = _advance(digest, _projection(row))
    snapshot = {**last["payload"], **{key: view[key] for key in (
        "org", "boot_id", "generation", "assigned_intents", "intent_digest", "phase_counts",
        "phase_digests", "active_preparations", "observation_error", "latest_seal_audit_id")},
        "seal_attempts": len(seals), "seal_persisted": len(seals), "seal_digest": digest}
    identities = {(row["task_id"], row["agent"], row["payload"].get("session_id")) for row in own if row["action"] == "memory_runtime_identity"}
    census_rows = own + [row for row in rows if row["action"] in {"session_start", "memory_digest_impression"}
                        and (row["task_id"], row["agent"], row["payload"].get("session_id")) in identities]
    if not validate_census(snapshot, census_rows)["census_valid"]:
        raise AcceptanceUnavailable("acceptance_census")
    return snapshot


def validate_acceptance_evidence(tables: dict, result_id: int, view: dict, outputs: dict, *, current_time: datetime,
                                 publication: bool = False, registered_cohort: list[dict] | None = None,
                                 retry_verifier: Any = None) -> dict:
    """All record/source/role/job/intent predicates are conjunctive, never flags."""
    from datetime import timedelta
    from runtime.infrastructure.memory_telemetry_report import aware_utc
    data = _decoded_tables(tables)
    raw = _one([row for row in data["task_results"] if row["id"] == result_id], "acceptance_manager_result")
    parsed = parse_acceptance(raw["output_summary"])
    if parsed is None or parsed["kind"] != "manager_acceptance":
        raise AcceptanceUnavailable("acceptance_manager_carrier")
    candidate = normalize_own_reference(parsed, raw)
    root = candidate["operational_root_task_id"]
    if candidate["org"] != view["org"]:
        raise AcceptanceUnavailable("acceptance_serving_context")
    if candidate["action"] == "invalidate" and registered_cohort is not None:
        view = {**view, "installed_identity": {"cohort": registered_cohort}}
    if not isinstance(view.get("installed_identity"), dict):
        raise AcceptanceUnavailable("acceptance_serving_context")
    manager_result, manager_task = _exact_role_result(data, candidate["result_ref"], view, role="manager", root=root,
                                                         withdrawal=candidate["action"] == "invalidate")
    _decision(manager_result)
    qa_result, qa_task = _exact_role_result(data, candidate["qa_ref"], view, role="worker", root=root,
                                               withdrawal=candidate["action"] == "invalidate")
    verdicts = re.findall(r"(?m)^Verdict:\s*(PASS|FAIL|BLOCK|REVISE|APPROVE|REQUEST_CHANGES)\b", qa_result["output_summary"])
    if qa_result["agent"] == manager_result["agent"] or qa_result["verdict"] != "PASS" or any(value != "PASS" for value in verdicts):
        raise AcceptanceUnavailable("acceptance_independent_qa")
    qa_parsed = parse_acceptance(qa_result["output_summary"])
    if qa_parsed is None or qa_parsed["kind"] != "installed_qa":
        raise AcceptanceUnavailable("acceptance_qa_carrier")
    qa = normalize_own_reference(qa_parsed, qa_result)
    common = {"contract_version", "org", "operational_root_task_id", "health_definition_sha256", "release_manifest_sha256",
              "installed_identity", "cohort", "applicable_paths", "synthetic_task_ids", "probe_receipts"}
    if any(qa[key] != candidate[key] for key in common) or aware_utc(qa_result["created_at"]) > aware_utc(manager_result["created_at"]):
        raise AcceptanceUnavailable("acceptance_qa_projection")
    if candidate["action"] == "invalidate":
        return {"candidate": candidate, "snapshot": None, "synthetic_task_ids": set(candidate["synthetic_task_ids"])}
    if view["observation_error"] is not None or view["data_through"] is None or view["active_preparations"]:
        raise AcceptanceUnavailable("acceptance_observation_unavailable")
    if (candidate["health_definition_sha256"] != HEALTH_DEFINITION_SHA256 or candidate["installed_identity"] != view["installed_identity"]
            or candidate["cohort"] != view["installed_identity"]["cohort"]
            or candidate["release_manifest_sha256"] != _hash_metadata(view["installed_identity"]["files"])):
        raise AcceptanceUnavailable("acceptance_installed_identity")
    snapshot = _census_from_view(view, data["audit_log"])
    plan = _approved_probe_plan(data, candidate, qa_task, qa_result, view)
    if any(slot[kind]["agent"] == qa_result["agent"] for slot in plan["slots"] for kind in ("root", "child")):
        raise AcceptanceUnavailable("acceptance_maker_independence")
    jobs, transcripts, operation_ids = {}, {}, {}
    for receipt in candidate["probe_receipts"]:
        job, transcript = _validate_job(data, receipt, qa_result, qa_task, plan, outputs[receipt["job_id"]])
        recorded = transcript["serving_observation"]
        if (recorded.get("installed_identity") != view["installed_identity"] or recorded.get("boot_id") != view["boot_id"]
                or recorded.get("org") != view["org"] or transcript["cli_identity"] != {
                    key: view["installed_identity"][key] for key in ("source_root", "python", "package_version", "files")}):
            raise AcceptanceUnavailable("acceptance_probe_installed_source")
        if receipt["job_id"] in jobs and (jobs[receipt["job_id"]] != job or transcripts[receipt["job_id"]] != transcript):
            raise AcceptanceUnavailable("acceptance_probe_job_conflict")
        jobs[receipt["job_id"]], transcripts[receipt["job_id"]] = job, transcript
        operation_ids.setdefault(receipt["job_id"], set()).update(_validate_probe_operations(data, receipt, transcript, view))
        for kind in ("root", "child"):
            claim = receipt[kind]
            terminal = _one([row for row in data["audit_log"] if row["action"] == "memory_runtime_terminal"
                             and row["task_id"] == claim["task_id"] and row["agent"] == claim["agent"]
                             and row["payload"].get("session_id") == claim["runtime_session_id"]], "acceptance_probe_finished")
            if not (aware_utc(job["started_at"]) <= aware_utc(terminal["timestamp"]) <= aware_utc(job["finished_at"])):
                raise AcceptanceUnavailable("acceptance_probe_job_order")
            # This instant is the final new-admission boundary, or the
            # authenticated original boundary for reports/equivalent replay.
            age = aware_utc(current_time) - aware_utc(terminal["timestamp"])
            if age < timedelta(0) or age > timedelta(hours=48):
                raise AcceptanceUnavailable("acceptance_probe_stale")
    for job_id, transcript in transcripts.items():
        if operation_ids[job_id] != set(transcript["operation_audit_ids"]):
            raise AcceptanceUnavailable("acceptance_probe_output_refs")
    designated = _resolve_probe_slots(data, plan, candidate["probe_receipts"], jobs, transcripts,
                                     retry_verifier=retry_verifier)
    if designated != set(candidate["synthetic_task_ids"]):
        raise AcceptanceUnavailable("acceptance_synthetic_set")
    paths = sorted(f"{profile['name']}:{view['installed_identity']['backend']['mode']}:{view['installed_identity']['backend']['name'] or 'none'}"
                   for profile in view["installed_identity"]["profiles"])
    if candidate["applicable_paths"] != paths or sorted(row["path"] for row in candidate["probe_receipts"]) != paths:
        raise AcceptanceUnavailable("acceptance_applicability")
    return {"candidate": candidate, "snapshot": snapshot, "synthetic_task_ids": designated}


def validate_epoch_candidate(tables: dict, head: dict, view: dict, outputs: dict, *, current_time: datetime,
                             retry_verifier: Any = None) -> dict:
    """Shared read authority, including newer malformed or conflicting results."""
    data = _decoded_tables(tables)
    from runtime.infrastructure.memory_telemetry_report import aware_utc
    body = head["payload"]
    admitted_at = aware_utc(head["timestamp"])
    if (body["boot_id"] != view["boot_id"] or admitted_at > aware_utc(current_time)
            or aware_utc(body["accepted_at"]) > admitted_at):
        raise AcceptanceUnavailable("epoch_admission_boundary")
    prepared = validate_acceptance_evidence(tables, body["manager_ref"]["result_id"], view, outputs, current_time=admitted_at,
                                            retry_verifier=retry_verifier)
    candidate = prepared["candidate"]
    if (candidate != body["projection"] or collection_logical_key(candidate) != body["logical_key"]
            or candidate["published_at"] != body["accepted_at"] or candidate["qa_ref"] != body["qa_ref"]
            or candidate["result_ref"] != body["manager_ref"]):
        raise AcceptanceUnavailable("epoch_projection_changed")
    expected_id = hashlib.sha256(json.dumps([candidate["org"], body["manager_ref"]["result_id"], body["logical_key"]], separators=(",", ":")).encode()).hexdigest()
    base_intents = [row for row in data["audit_log"] if row["action"] == "memory_runtime_intent"
                    and row["payload"].get("boot_id") == body["boot_id"] and row["id"] < head["id"]]
    if (body["epoch_id"] != expected_id or type(body["base_assigned_intents"]) is not int
            or body["base_assigned_intents"] != len(base_intents)):
        raise AcceptanceUnavailable("epoch_boundary_changed")
    root = candidate["operational_root_task_id"]
    for result in data["task_results"]:
        if result["task_id"] == root and result["id"] > body["manager_ref"]["result_id"] and "MemoryCollectionV1:" in result["output_summary"]:
            later = validate_acceptance_evidence(tables, result["id"], view, outputs, current_time=admitted_at,
                                                 retry_verifier=retry_verifier)["candidate"]
            if collection_logical_key(later) != body["logical_key"]:
                raise AcceptanceUnavailable("epoch_newer_control_candidate")
    return prepared


def _view_projection(view: dict) -> str:
    return _hash_metadata({key: value for key, value in view.items() if key not in {"sampled_at", "data_through", "epoch_id", "epoch_audit_id"}})


def _referenced_probe_jobs(results: list[dict]) -> set[str]:
    """Only closed tagged positive carriers select original probe output."""
    job_ids = set()
    for result in results:
        try:
            candidate = parse_acceptance(result["output_summary"])
        except AcceptanceUnavailable:
            continue  # Exact selected malformed controls fail in the validator.
        if candidate is not None and candidate.get("action") != "invalidate":
            job_ids.update(receipt["job_id"] for receipt in candidate["probe_receipts"])
    return job_ids


def _local_job_outputs(org: Any, tables: dict) -> dict:
    outputs = {}
    budget = [0, 0]
    referenced_jobs = _referenced_probe_jobs(tables["task_results"])
    for job in tables["jobs"]:
        if job["status"] != "completed":
            continue
        paths = {stream: org.root / "jobs" / (job["id"] + suffix) for stream, suffix in (("stdout", ".out"), ("stderr", ".err"))}
        # Read only exact owned outputs referenced by tagged acceptance rows.
        if job["id"] not in referenced_jobs:
            continue
        output = {}
        for stream, path in paths.items():
            if job[f"{stream}_path"] != str(path):
                raise AcceptanceUnavailable("acceptance_output_path")
            content = _identity_bytes(path, budget, limit=10 * 1_048_576)
            output[stream] = content.decode("utf-8", errors="strict")
            output[f"total_{stream}_bytes"] = len(content)
            output[f"truncated_{stream}"] = False
        outputs[job["id"]] = output
    return outputs


def _registered_cohort(org: Any) -> list[dict]:
    """Acquire current role registrations independently of probe/source health."""
    budget, cohort = [0, 0], []
    names = sorted(org.teams.all_agents())
    if not names or len(names) != len(set(names)) or len(names) > MAX_IDENTITY_AGENTS:
        raise AcceptanceUnavailable("acceptance_role_registration")
    disk = yaml.safe_load(_identity_bytes(org.root / "org" / "teams.yaml", budget))
    expected = {team: {"manager": org.teams.manager_for_team(team).name,
                       "workers": sorted(org.teams.manager_for_team(team).workers)} for team in org.teams.teams()}
    if {team: {"manager": (body["manager"] if isinstance(body["manager"], str) else
                              body["manager"]["principal"] if body["manager"]["kind"] == "agent" else None), "workers": sorted(body.get("workers") or [])}
            for team, body in disk["teams"].items()} != expected:
        raise AcceptanceUnavailable("acceptance_role_registration_drift")
    for name in names:
        definition = parse_agent_text(_identity_bytes(org.root / "org" / "agents" / f"{name}.md", budget).decode(), expected_name=name)
        if (definition.team not in expected or org.teams.is_team_manager(name) != (definition.role == "manager")
                or name not in [expected[definition.team]["manager"], *expected[definition.team]["workers"]]):
            raise AcceptanceUnavailable("acceptance_role_registration")
        cohort.append({"agent": name, "team": definition.team, "role": definition.role,
                       "executor": definition.executor, "model": definition.model})
    return cohort


def prepare_acceptance(org: Any, result_id: int) -> dict:
    opening = serving_observation(org)
    tables = org.db.read_memory_collection_evidence()
    selected = _one([row for row in tables["task_results"] if row["id"] == result_id], "acceptance_manager_result")
    parsed = parse_acceptance(selected["output_summary"])
    invalidation = parsed is not None and parsed.get("action") == "invalidate"
    roles = _registered_cohort(org) if invalidation else None
    outputs = {} if invalidation else _local_job_outputs(org, tables)
    candidate = normalize_own_reference(parsed, selected) if parsed is not None else None
    original = equivalent_collection_control(tables, candidate) if candidate is not None else None
    from runtime.infrastructure.memory_telemetry_report import aware_utc
    admission_time = aware_utc(original["timestamp"]) if original is not None else datetime.now(timezone.utc)
    if original is not None and not invalidation:
        validate_epoch_candidate(tables, original, opening, outputs, current_time=datetime.now(timezone.utc),
                                 retry_verifier=org.db.verify_retry_link)
    prepared = validate_acceptance_evidence(tables, result_id, opening, outputs,
                                            current_time=admission_time, registered_cohort=roles,
                                            retry_verifier=org.db.verify_retry_link)
    closing = serving_observation(org)
    if (_view_projection(opening) != _view_projection(closing)
            or (invalidation and roles != _registered_cohort(org))
            or (not invalidation and outputs != _local_job_outputs(org, tables))):
        raise AcceptanceUnavailable("acceptance_acquisition_moving")
    return {**prepared, "view": opening, "tables": tables, "outputs": outputs, "registered_cohort": roles}


def reconcile_acceptance(org: Any, result_id: int) -> dict | None:
    """Best-effort post-log observation; completion is never changed."""
    try:
        return org.db.append_memory_collection_transition(result_row_id=result_id)
    except Exception:
        return None


def acquire_collection_report(org: Any, *, current_time: datetime | None = None) -> tuple[dict, dict, dict, datetime]:
    from runtime.infrastructure.memory_telemetry_report import aware_utc, ReportAcquisitionUnavailable
    try:
        opening = serving_observation(org)
        tables = org.db.read_memory_collection_evidence()
        outputs = _local_job_outputs(org, tables)
        closing_tables = org.db.read_memory_collection_evidence()
        closing = serving_observation(org)
        if tables != closing_tables or _view_projection(opening) != _view_projection(closing) or outputs != _local_job_outputs(org, tables):
            raise ReportAcquisitionUnavailable()
        cutoff = aware_utc(current_time) if current_time is not None else aware_utc(opening["data_through"] or opening["sampled_at"])
        return tables, opening, outputs, cutoff
    except Exception as exc:
        raise ReportAcquisitionUnavailable() from exc


def _collection_pages(client: Any, org: str, *, tasks: bool = False) -> list[dict]:
    from runtime.infrastructure.memory_telemetry_report import ReportAcquisitionUnavailable
    items, seen, cursors, cursor = [], set(), set(), None
    while True:
        params = {"limit": 200 if tasks else 5000}
        if cursor is not None:
            params["before" if tasks else "cursor"] = cursor
        response = client.get(f"/api/v1/orgs/{org}/{'tasks' if tasks else 'audit'}", params=params)
        if response.status_code != 200:
            raise ReportAcquisitionUnavailable()
        page = response.json()
        _closed(page, {"tasks" if tasks else "entries", "next_cursor"})
        rows = page["tasks" if tasks else "entries"]
        if not isinstance(rows, list) or len(items) + len(rows) > MAX_CENSUS_READ_ROWS:
            raise ReportAcquisitionUnavailable()
        for index, row in enumerate(rows):
            if tasks and isinstance(row, dict):
                row = dict(row)
                row["id"] = row.pop("task_id")
                rows[index] = row
            if not isinstance(row, dict) or row.get("id") in seen:
                raise ReportAcquisitionUnavailable()
            seen.add(row["id"])
        items.extend(rows)
        next_cursor = page["next_cursor"]
        if next_cursor is None:
            break
        if not rows or not isinstance(next_cursor, str) or not next_cursor or next_cursor in cursors:
            raise ReportAcquisitionUnavailable()
        if tasks:
            if next_cursor != rows[-1]["id"] or (cursor is not None and next_cursor >= cursor):
                raise ReportAcquisitionUnavailable()
        else:
            from runtime.infrastructure.database import _decode_cursor
            anchor = _decode_cursor(next_cursor)
            if not any((row.get("timestamp"), row["id"]) == anchor for row in rows):
                raise ReportAcquisitionUnavailable()
            if cursor is not None and anchor >= _decode_cursor(cursor):
                raise ReportAcquisitionUnavailable()
        cursors.add(next_cursor)
        cursor = next_cursor
    return sorted(items, key=lambda row: row["id"])


def _http_collection_capture(client: Any, org: str) -> tuple[dict, dict, dict]:
    from runtime.infrastructure.memory_telemetry_report import ReportAcquisitionUnavailable
    def get(path: str, params: dict | None = None) -> dict:
        response = client.get(f"/api/v1/orgs/{org}/" + path, params=params)
        if response.status_code != 200:
            raise ReportAcquisitionUnavailable()
        body = response.json()
        if not isinstance(body, dict):
            raise ReportAcquisitionUnavailable()
        return body
    view = get("audit", {"action": "memory_collection_seal", "limit": 1})["memory_collection_observation"]
    audit_rows, tasks = _collection_pages(client, org), _collection_pages(client, org, tasks=True)
    results, jobs, outputs = [], [], {}
    # All original parent/plan results participate in prior-delegation and
    # retry validation; accepting only final references would omit evidence.
    for task_id in sorted(row["id"] for row in tasks):
        _identifier(task_id, "TASK")
        detail = get(f"tasks/{task_id}")
        if not isinstance(detail.get("results"), list):
            raise ReportAcquisitionUnavailable()
        results.extend(detail["results"])
    job_ids = _referenced_probe_jobs(results)
    for job_id in sorted(job_ids):
        jobs.append(get(f"jobs/{job_id}"))
        outputs[job_id] = get(f"jobs/{job_id}/output", {"stream": "both", "max_bytes": 10 * 1_048_576})
        # The job route reports a missing on-disk stream as an empty stream.
        # A referenced canary requires both original complete output streams;
        # refuse acquisition instead of rendering a partial evidence report.
        output = outputs[job_id]
        _closed(output, {"stdout", "stderr", "truncated_stdout", "truncated_stderr", "total_stdout_bytes", "total_stderr_bytes"})
        for stream in ("stdout", "stderr"):
            text, total = output[stream], output[f"total_{stream}_bytes"]
            if (not isinstance(text, str) or "\ufffd" in text or type(total) is not int or total <= 0
                    or len(text.encode("utf-8")) != total or output[f"truncated_{stream}"] is not False):
                raise ReportAcquisitionUnavailable()
    return {"tasks": tasks, "task_results": results, "jobs": jobs, "audit_log": audit_rows}, view, outputs


def acquire_http_collection_report(client: Any, org: str, *, current_time: datetime | None = None) -> tuple[dict, dict, dict, datetime]:
    from runtime.infrastructure.memory_telemetry_report import aware_utc, ReportAcquisitionUnavailable
    try:
        opening = _http_collection_capture(client, org)
        closing = _http_collection_capture(client, org)
        if opening[0] != closing[0] or opening[2] != closing[2] or _view_projection(opening[1]) != _view_projection(closing[1]):
            raise ReportAcquisitionUnavailable()
        cutoff = aware_utc(current_time) if current_time is not None else aware_utc(opening[1]["data_through"] or opening[1]["sampled_at"])
        return *opening, cutoff
    except Exception as exc:
        raise ReportAcquisitionUnavailable() from exc


def current_epoch_references(org: Any, view: dict) -> dict:
    """Project references only after actual current evidence validation; zero writes."""
    try:
        controls = [dict(row) for row in org.db.fetch_all_readonly(
            "SELECT * FROM audit_log WHERE action IN (?, ?) ORDER BY id LIMIT ?",
            (*sorted(CONTROL_ACTIONS), MAX_CENSUS_READ_ROWS + 1),
        )]
        if not controls:
            return view
        if len(controls) > MAX_CENSUS_READ_ROWS:
            raise AcceptanceUnavailable("acceptance_control_limit")
        last = _strict_json(controls[-1]["payload"])
        head = collection_control_head(controls, org.slug, last["operational_root_task_id"])
        if head["action"] != "memory_collection_epoch_started" or head["payload"]["boot_id"] != view["boot_id"]:
            raise AcceptanceUnavailable("acceptance_epoch_inactive")
        tables = org.db.read_memory_collection_evidence()
        outputs = _local_job_outputs(org, tables)
        # The serving view was acquired before this separate evidence SELECT.
        # Detect a terminal/seal arriving between them before validating an
        # opening census against later rows. Actual evidence damage remains a
        # validation failure; only measured bookend movement is transient.
        closing = serving_observation(org)
        if (_view_projection(closing) != _view_projection(view)
                or tables != org.db.read_memory_collection_evidence() or outputs != _local_job_outputs(org, tables)):
            return {**view, "observation_error": view["observation_error"] or "observation_moving", "data_through": None}
        candidate = validate_epoch_candidate(tables, head, view, outputs,
                                             current_time=datetime.now(timezone.utc),
                                             retry_verifier=org.db.verify_retry_link)["candidate"]
        closing = serving_observation(org)
        if candidate != head["payload"]["projection"]:
            raise AcceptanceUnavailable("epoch_projection_changed")
        if (_view_projection(closing) != _view_projection(view)
                or tables != org.db.read_memory_collection_evidence() or outputs != _local_job_outputs(org, tables)):
            return {**view, "observation_error": view["observation_error"] or "observation_moving", "data_through": None}
        return {**view, "epoch_id": head["payload"]["epoch_id"], "epoch_audit_id": head["id"]}
    except Exception:
        return {**view, "observation_error": view["observation_error"] or "epoch_validation_unavailable", "data_through": None}
