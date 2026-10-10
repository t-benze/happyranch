"""Org-owned root controls and bounded invocation evidence (THR-292).

This module is deliberately absent from generic Database initialization.
Installation belongs to OrgState, before recovery or admission. Old binaries
cannot enforce these controls; rollback requires a compatible reader.
"""
from __future__ import annotations

import json
import math
import re
import sqlite3
import threading
import time
from contextlib import contextmanager, nullcontext
from datetime import datetime, timezone
from typing import Any, Iterator


PAUSE_SCHEMA_SQL = """CREATE TABLE task_pause_controls (
    root_task_id TEXT NOT NULL PRIMARY KEY REFERENCES tasks(id),
    schema_version INTEGER NOT NULL CHECK (schema_version = 1),
    held INTEGER NOT NULL CHECK (held IN (0, 1)),
    generation INTEGER NOT NULL CHECK (typeof(generation) = 'integer' AND generation >= 0 AND generation <= 9223372036854775807),
    paused_at TEXT,
    resumed_at TEXT,
    updated_at TEXT NOT NULL,
    actor TEXT NOT NULL,
    release_pending_generation INTEGER CHECK (release_pending_generation >= 0),
    admission_journal_json TEXT NOT NULL
)"""
TERMINAL = frozenset({"completed", "failed", "cancelled", "superseded"})
MAX_ENTRIES = 256
MAX_JOURNAL_BYTES = 1024 * 1024
MAX_GENERATION = 2**63 - 1
PHASES = frozenset({"prepared", "possible_launch", "action_started", "running",
                    "result_processing", "retry_deferred", "recovery_deferred",
                    "pending_job", "unknown"})
OWNERS = frozenset({"ordinary", "v2", "draft", "recovery", "job"})
ENTRY_KEYS = frozenset({"id", "org", "root_task_id", "task_id", "agent", "session_id",
                        "owner", "owner_identity", "fingerprint", "generation", "phase",
                        "retry", "context", "started_at", "captured_generation"})
FINGERPRINT_KEYS = frozenset({"status", "block_kind", "count", "session_id", "agent",
                              "fanout", "jobs", "parent", "brief_digest"})
OWNER_IDENTITY_KEYS = frozenset({"authority_v2_generation", "trigger", "triggering_job_id", "job_id"})
RETRY_KEYS = frozenset({"ordinal", "budget", "enqueued_at", "not_before", "boundary", "schedule"})


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normal_session_entry(conn: sqlite3.Connection, entry: dict) -> bool:
    """Read actual admitted session purpose, independently of its claim owner.

    A v2 continuation can execute an ordinary manager turn. Workflow authors
    and completion recovery never acquire this entitlement. The journal and
    exact server session-start audit must agree; stored owner labels alone
    are insufficient. Call under the existing database ownership.
    """
    if entry["owner"] not in {"ordinary", "v2"}:
        return False
    rows = conn.execute("SELECT agent,payload FROM audit_log WHERE task_id=? AND action='session_start'",
                        (entry["task_id"],)).fetchall()
    matches = []
    for row in rows:
        try:
            payload = json.loads(row["payload"])
        except (ValueError, TypeError):
            return False
        if not isinstance(payload, dict):
            return False
        if payload.get("session_id") == entry["session_id"]:
            matches.append((row["agent"], payload.get("invocation_purpose")))
    return (len(matches) == 1 and matches[0][0] == entry["agent"]
            and matches[0][1] in {"manager_decision", "worker_execution"})


class PauseControlError(Exception):
    def __init__(self, code: str, *, root_task_id: str | None = None,
                 generation: int | None = None) -> None:
        super().__init__(code)
        self.detail = {"code": code}
        if root_task_id is not None:
            self.detail["root_task_id"] = root_task_id
        if generation is not None:
            self.detail["generation"] = generation


def decode_journal(raw: str) -> dict[str, Any]:
    try:
        if len(raw.encode()) > MAX_JOURNAL_BYTES:
            raise ValueError("journal overflow")
        def unique_object(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("duplicate journal key")
                result[key] = value
            return result
        journal = json.loads(raw, object_pairs_hook=unique_object)
        if (not isinstance(journal, dict) or set(journal) != {"version", "entries"}
                or type(journal["version"]) is not int or journal["version"] != 1
                or not isinstance(journal["entries"], list)
                or len(journal["entries"]) > MAX_ENTRIES):
            raise ValueError("journal shape")
        identities = set()
        for e in journal["entries"]:
            if not isinstance(e, dict) or set(e) != ENTRY_KEYS:
                raise ValueError("entry shape")
            for key in ("id", "org", "root_task_id", "task_id", "agent", "session_id"):
                if key == "session_id" and e.get("owner") == "job" and e[key] is None:
                    continue  # Founder launch may have no submitting session.
                if not isinstance(e[key], str) or not e[key] or len(e[key]) > 256:
                    raise ValueError("entry identity")
            if e["id"] in identities:
                raise ValueError("duplicate identity")
            identities.add(e["id"])
            if (e["phase"] not in PHASES or e["owner"] not in OWNERS
                    or type(e["generation"]) is not int or e["generation"] < 0
                    or not isinstance(e["fingerprint"], dict)
                    or not isinstance(e["owner_identity"], dict)
                    or not isinstance(e["context"], dict)
                    or not isinstance(e["retry"], dict)):
                raise ValueError("entry fields")
            capture = e["captured_generation"]
            if capture is not None and (type(capture) is not int or capture < 1):
                raise ValueError("capture generation")
            if e["started_at"] is not None:
                if datetime.fromisoformat(e["started_at"]).tzinfo is None:
                    raise ValueError("naive running time")
            f = e["fingerprint"]
            if (set(f) != FINGERPRINT_KEYS or f["status"] not in TERMINAL | {"pending", "in_progress", "escalated"}
                    or f["block_kind"] not in (None, "delegated", "blocked_on_job")
                    or type(f["count"]) is not int or f["count"] < 0
                    or not isinstance(f["brief_digest"], str) or re.fullmatch(r"[0-9a-f]{64}", f["brief_digest"]) is None
                    or any(f[k] is not None and (not isinstance(f[k], str) or len(f[k]) > MAX_JOURNAL_BYTES)
                           for k in ("session_id", "agent", "fanout", "jobs", "parent"))):
                raise ValueError("fingerprint shape")
            if not set(e["owner_identity"]) <= OWNER_IDENTITY_KEYS:
                raise ValueError("owner identity shape")
            if any(v is not None and not isinstance(v, str) for v in e["owner_identity"].values()):
                raise ValueError("owner identity value")
            retry = e["retry"]
            if retry:
                if (set(retry) != RETRY_KEYS or retry["boundary"] not in {"host", "provider"}
                        or type(retry["ordinal"]) is not int or type(retry["budget"]) is not int
                        or not 0 <= retry["ordinal"] <= retry["budget"]):
                    raise ValueError("retry shape")
                if (not isinstance(retry["schedule"], list) or len(retry["schedule"]) > 256
                        or any(type(v) not in (int, float) or not math.isfinite(v) or v < 0 for v in retry["schedule"])
                        or retry["budget"] != len(retry["schedule"])
                        or datetime.fromisoformat(retry["enqueued_at"]).tzinfo is None
                        or datetime.fromisoformat(retry["not_before"]).tzinfo is None
                        or e["owner"] in {"draft", "recovery", "job"}):
                    raise ValueError("retry owner/time")
            context = e["context"]
            if not set(context) <= {"audit_ids", "cleanup", "recovery_return", "execution_unknown", "producer_settled", "job_submission"}:
                raise ValueError("context shape")
            if "job_submission" in context:
                proof = context["job_submission"]
                if (e["owner"] != "job" or not isinstance(proof, dict)
                        or set(proof) != {"audit_id", "digest"}
                        or type(proof["audit_id"]) is not int or proof["audit_id"] <= 0
                        or not isinstance(proof["digest"], str)
                        or re.fullmatch(r"[0-9a-f]{64}", proof["digest"]) is None):
                    raise ValueError("job submission proof")
            if "producer_settled" in context and (context["producer_settled"] is not True or context.get("execution_unknown") is not True):
                raise ValueError("settled producer uncertainty")
            if "execution_unknown" in context and context["execution_unknown"] is not True:
                raise ValueError("execution uncertainty")
            if "audit_ids" in context and (not isinstance(context["audit_ids"], list)
                    or any(type(i) is not int or i <= 0 for i in context["audit_ids"])):
                raise ValueError("context audit identity")
            if "recovery_return" in context:
                proof = context["recovery_return"]
                if (not isinstance(proof, dict)
                        or set(proof) != {"origin_session_id", "provider_session_id", "duration_seconds", "recovery_session_id", "deadline_at"}
                        or e["owner"] != "recovery"
                        or (proof["deadline_at"] is None and e["phase"] in {"possible_launch", "running"})
                        or any(not isinstance(proof[k], str) or not proof[k] or len(proof[k]) > 1024
                               for k in ("origin_session_id", "provider_session_id", "recovery_session_id"))
                        or (proof["deadline_at"] is not None and
                            (not isinstance(proof["deadline_at"], str)
                             or datetime.fromisoformat(proof["deadline_at"]).tzinfo is None))
                        or type(proof["duration_seconds"]) not in (int, float)
                        or not math.isfinite(proof["duration_seconds"]) or proof["duration_seconds"] < 0):
                    raise ValueError("recovery return proof")
            if "cleanup" in context:
                cleanup = context["cleanup"]
                if (not isinstance(cleanup, dict) or set(cleanup) != {"suffix_lines"}
                        or not isinstance(cleanup["suffix_lines"], list)
                        or any(not isinstance(line, str) for line in cleanup["suffix_lines"])
                        or sum(len(line.encode()) for line in cleanup["suffix_lines"]) > 65536):
                    raise ValueError("cleanup facts")
        return journal
    except (ValueError, TypeError, KeyError, OverflowError, RecursionError) as exc:
        raise PauseControlError("pause_control_unavailable") from exc


def validate_pause_schema(conn: sqlite3.Connection, *, org_slug: str | None = None,
                          allow_absent: bool = False) -> bool:
    """Compare the entire owned layout to independently constructed literals."""
    objects = conn.execute(
        "SELECT type,name,tbl_name,sql FROM sqlite_master "
        "WHERE tbl_name='task_pause_controls' OR name='task_pause_controls' ORDER BY name"
    ).fetchall()
    if not objects and allow_absent:
        return False
    with sqlite3.connect(":memory:") as reference:
        reference.execute("CREATE TABLE tasks(id TEXT PRIMARY KEY)")
        reference.execute(PAUSE_SCHEMA_SQL)
        expected = reference.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_master "
            "WHERE tbl_name='task_pause_controls' ORDER BY name"
        ).fetchall()
        if [tuple(r) for r in objects] != expected:
            raise PauseControlError("pause_control_unavailable")
        for pragma in ("table_xinfo", "foreign_key_list", "index_list"):
            actual = [tuple(r) for r in conn.execute(f"PRAGMA {pragma}(task_pause_controls)")]
            wanted = reference.execute(f"PRAGMA {pragma}(task_pause_controls)").fetchall()
            if actual != wanted:
                raise PauseControlError("pause_control_unavailable")
    for row in conn.execute("SELECT * FROM task_pause_controls"):
        d = dict(row)
        root = conn.execute("SELECT parent_task_id FROM tasks WHERE id=?", (d["root_task_id"],)).fetchone()
        if (root is None or root[0] is not None or d["schema_version"] != 1
                or d["held"] not in (0, 1) or type(d["generation"]) is not int
                or not 0 <= d["generation"] <= MAX_GENERATION or not isinstance(d["actor"], str) or not d["actor"]
                or d["held"] != d["generation"] % 2
                or (d["generation"] == 0 and (d["paused_at"] is not None or d["resumed_at"] is not None))
                or (d["generation"] > 0 and d["paused_at"] is None)
                or (d["generation"] > 1 and d["resumed_at"] is None)):

            raise PauseControlError("pause_control_unavailable")
        journal = decode_journal(d["admission_journal_json"])
        for key in ("paused_at", "resumed_at", "updated_at"):
            if d[key] is not None:
                try:
                    if datetime.fromisoformat(d[key]).tzinfo is None:
                        raise ValueError("naive timestamp")
                except (ValueError, TypeError) as exc:
                    raise PauseControlError("pause_control_unavailable") from exc
        release = d["release_pending_generation"]
        if release is not None and (type(release) is not int or release != d["generation"] or d["held"]):
            raise PauseControlError("pause_control_unavailable")
        for entry in journal["entries"]:
            if (entry["root_task_id"] != d["root_task_id"]
                    or (org_slug is not None and entry["org"] != org_slug)
                    or entry["generation"] > d["generation"]):
                raise PauseControlError("pause_control_unavailable")
            task_id = entry["task_id"]
            seen: set[str] = set()
            for _ in range(1024):
                if task_id in seen:
                    raise PauseControlError("pause_control_unavailable")
                seen.add(task_id)
                task = conn.execute("SELECT parent_task_id FROM tasks WHERE id=?", (task_id,)).fetchone()
                if task is None:
                    raise PauseControlError("pause_control_unavailable")
                if task[0] is None:
                    if task_id != d["root_task_id"]:
                        raise PauseControlError("pause_control_unavailable")
                    break
                task_id = task[0]
            else:
                raise PauseControlError("pause_control_unavailable")
    return True


def install_pause_schema(db: Any, *, org_slug: str) -> None:
    with db._lock:
        if db._conn.in_transaction:
            raise PauseControlError("pause_control_unavailable")
        db._conn.execute("BEGIN IMMEDIATE")
        try:
            if not validate_pause_schema(db._conn, org_slug=org_slug, allow_absent=True):
                db._conn.execute(PAUSE_SCHEMA_SQL)
            validate_pause_schema(db._conn, org_slug=org_slug)
            db._conn.commit()
        except BaseException:
            db._conn.rollback()
            raise


class TaskPauseStore:
    def __init__(self, db: Any, org_slug: str) -> None:
        self.db, self.org_slug = db, org_slug
        self._gates_lock = threading.Lock()
        self._gates: dict[str, threading.RLock] = {}
        self._live: dict[str, Any] = {}

    def root_uncommitted(self, task_id: str) -> dict:
        seen: set[str] = set()
        for _ in range(1024):
            if task_id in seen:
                break
            seen.add(task_id)
            row = self.db._conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            if row is None:
                raise PauseControlError("unknown_task" if len(seen) == 1 else "pause_control_unavailable")
            task = dict(row)
            if task["parent_task_id"] is None:
                return task
            task_id = task["parent_task_id"]
        raise PauseControlError("pause_control_unavailable")

    def gate(self, root_id: str) -> threading.RLock:
        with self._gates_lock:
            return self._gates.setdefault(root_id, threading.RLock())

    def row_uncommitted(self, root_id: str) -> dict | None:
        row = self.db._conn.execute("SELECT * FROM task_pause_controls WHERE root_task_id=?", (root_id,)).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["journal"] = decode_journal(result["admission_journal_json"])
        if (result["schema_version"] != 1 or type(result["generation"]) is not int
                or not 0 <= result["generation"] <= MAX_GENERATION
                or result["held"] not in (0, 1) or result["held"] != result["generation"] % 2
                or any(e["org"] != self.org_slug or e["root_task_id"] != root_id
                       or e["generation"] > result["generation"] for e in result["journal"]["entries"])):
            raise PauseControlError("pause_control_unavailable")
        return result

    def held_uncommitted(self, task_id: str) -> tuple[dict, dict | None]:
        root = self.root_uncommitted(task_id)
        row = self.row_uncommitted(root["id"])
        if row and row["held"] and root["status"] not in TERMINAL:
            raise PauseControlError("root_paused", root_task_id=root["id"], generation=row["generation"])
        return root, row

    @contextmanager
    def writer(self, task_id: str, *, commit_guard: Any = None, committed: Any = None) -> Iterator[tuple[dict, dict | None]]:
        with self.db._lock:
            root_id = self.root_uncommitted(task_id)["id"]
        with self.gate(root_id), self.db._lock:
            if self.db._conn.in_transaction:
                raise PauseControlError("pause_control_unavailable")
            self.db._conn.execute("BEGIN IMMEDIATE")
            with commit_guard if commit_guard is not None else nullcontext():
                try:
                    root = self.root_uncommitted(task_id)
                    if root["id"] != root_id:
                        raise PauseControlError("pause_control_unavailable")
                    yield root, self.row_uncommitted(root_id)
                    self.db._conn.commit()
                    if committed is not None:
                        committed()
                except BaseException:
                    self.db._conn.rollback()
                    raise

    def ensure_uncommitted(self, root_id: str) -> dict:
        row = self.row_uncommitted(root_id)
        if row is None:
            self.db._conn.execute(
                "INSERT INTO task_pause_controls VALUES (?,1,0,0,NULL,NULL,?,'runtime',NULL,?)",
                (root_id, now(), '{"version":1,"entries":[]}'),
            )
            row = self.row_uncommitted(root_id)
        return row

    def save_journal_uncommitted(self, row: dict) -> None:
        raw = json.dumps(row["journal"], sort_keys=True, separators=(",", ":"), allow_nan=False)
        decode_journal(raw)
        self.db._conn.execute("UPDATE task_pause_controls SET admission_journal_json=? WHERE root_task_id=?",
                              (raw, row["root_task_id"]))

    def control(self, task_id: str, *, held: bool, expected_generation: int,
                actor: str, sessions: Any) -> bool:
        if type(expected_generation) is not int or not 0 <= expected_generation <= MAX_GENERATION or not actor:
            raise PauseControlError("pause_control_unavailable")
        running: set[tuple[str, str, str]] = set()
        # iter_active() is advisory: admission/binding/running can move before
        # root arbitration. Read current bindings only after acquiring the
        # root/DB writer, and freeze publication/clear through its COMMIT.
        # This leaf tracker mutex never acquires a binding lease in reverse.
        capture_guard = sessions._pause_capture_guard(running) if held else None
        with self.writer(task_id, commit_guard=capture_guard) as (root, row):
            if root["id"] != task_id:
                raise PauseControlError("root_task_required", root_task_id=root["id"])
            if root["status"] in TERMINAL:
                raise PauseControlError("task_terminal", root_task_id=task_id)
            generation = row["generation"] if row else 0
            current = bool(row and row["held"])
            if generation != expected_generation:
                if (generation == expected_generation + 1 and row
                        and row["actor"] == actor and current == held):
                    return False
                raise PauseControlError("control_generation_conflict", root_task_id=task_id, generation=generation)
            if held and not current and root["status"] != "in_progress":
                raise PauseControlError("task_not_in_progress", root_task_id=task_id)
            if current == held:
                return False
            if generation == MAX_GENERATION:
                raise PauseControlError("pause_control_unavailable")
            row = row or self.ensure_uncommitted(task_id)
            generation += 1
            stamp = now()
            if held:
                for entry in row["journal"]["entries"]:
                    entry["captured_generation"] = None
                    if (entry["phase"] == "running" and normal_session_entry(self.db._conn, entry)
                            and (entry["task_id"], entry["agent"], entry["session_id"]) in running):
                        task = self.db._conn.execute("SELECT assigned_agent,current_session_id,status,cancelled_at FROM tasks WHERE id=?",
                                                     (entry["task_id"],)).fetchone()
                        if (task is not None and tuple(task[:2]) == (entry["agent"], entry["session_id"])
                                and task[2] == "in_progress" and task[3] is None):
                            entry["captured_generation"] = generation
                self.save_journal_uncommitted(row)
            self.db._conn.execute(
                "UPDATE task_pause_controls SET held=?,generation=?,actor=?,updated_at=?,"
                "paused_at=CASE WHEN ? THEN ? ELSE paused_at END,"
                "resumed_at=CASE WHEN ? THEN resumed_at ELSE ? END,release_pending_generation=? WHERE root_task_id=?",
                (int(held), generation, actor, stamp, int(held), stamp, int(held), stamp,
                 None if held else generation, task_id),
            )
            self.db.insert_audit_log_uncommitted(task_id, actor, "task_paused" if held else "task_resumed",
                                                {"generation": generation, "held": held})
            return True

    def projection(self, task_id: str, *, deadline: float | None = None) -> dict:
        """A coherent read; never reconcile, probe a PID, cache or write."""
        with self.db.coherent_read_view() as conn:
            root = self.root_uncommitted(task_id)
            stamp = now()
            deadline = deadline if deadline is not None else time.monotonic() + 0.25
            blockers: list[dict] = []
            complete = True

            def block(kind: str, identity: str, owner: str, reason: str, job_id: str | None = None) -> None:
                nonlocal complete
                if kind == "unknown":
                    complete = False
                if len(blockers) >= 100:
                    complete = False
                    blockers[-1] = {"kind": "unknown", "task_id": root["id"], "job_id": None,
                                    "owner_kind": "scan", "reason": "scan_incomplete", "observed_at": stamp}
                    return
                blockers.append({"kind": kind, "task_id": identity,
                                 "job_id": job_id, "owner_kind": owner,
                                 "reason": reason, "observed_at": stamp})

            try:
                row = self.row_uncommitted(root["id"])
            except PauseControlError:
                row = None
                block("unknown", root["id"], "control", "pause_control_unavailable")
            tasks = {root["id"]: root}
            frontier = [root["id"]]
            while frontier:
                if time.monotonic() >= deadline:
                    block("unknown", root["id"], "tree", "scan_incomplete")
                    break
                parent = frontier.pop()
                children = conn.execute("SELECT * FROM tasks WHERE parent_task_id=? LIMIT 10001", (parent,)).fetchall()
                for child in children:
                    task = dict(child)
                    if task["id"] in tasks or len(tasks) >= 10000:
                        block("unknown", root["id"], "tree", "scan_incomplete")
                        frontier.clear()
                        break
                    tasks[task["id"]] = task
                    frontier.append(task["id"])
            covered: set[str] = set()
            if row:
                for entry in row["journal"]["entries"]:
                    if entry["context"].get("execution_unknown"):
                        block("unknown", entry["task_id"], entry["owner"], "execution_tree_evidence_unavailable")
                    if entry["phase"] in {"prepared", "pending_job", "retry_deferred", "recovery_deferred"}:
                        if entry["owner"] != "job":
                            covered.add(entry["task_id"])
                            sid = (entry["context"]["recovery_return"]["origin_session_id"]
                                   if entry["phase"] == "recovery_deferred" else entry["session_id"])
                            if conn.execute("SELECT 1 FROM task_results WHERE task_id=? AND agent=? AND session_id=?",
                                            (entry["task_id"], entry["agent"], sid)).fetchone():
                                block("result_processing", entry["task_id"], entry["owner"], "deferred_callback_pending")
                        continue
                    if entry["task_id"] not in tasks:
                        block("unknown", root["id"], "journal", "owner_tree_unavailable")
                    covered.add(entry["task_id"])
                    phase = entry["phase"]
                    kind = {"running": "task_session", "possible_launch": "launch",
                            "action_started": "result_processing", "result_processing": "result_processing"}.get(phase, "unknown")
                    if entry["owner"] == "job":
                        kind = "launch" if phase == "possible_launch" else "job" if phase == "running" else "unknown"
                    block(kind, entry["task_id"], entry["owner"], phase,
                          entry["owner_identity"].get("job_id"))
            for tid, task in tasks.items():
                if time.monotonic() >= deadline:
                    block("unknown", root["id"], "scan", "scan_incomplete")
                    break
                if tid not in covered and task["status"] == "in_progress" and task["block_kind"] is None:
                    block("unknown", tid, "legacy", "execution_evidence_unavailable")
                for job in conn.execute("SELECT id FROM jobs WHERE task_id=? AND status='running' LIMIT 101", (tid,)):
                    if len(blockers) >= 100:
                        block("unknown", root["id"], "scan", "scan_incomplete")
                        break
                    blockers.append({"kind": "job", "task_id": tid, "job_id": job[0],
                                     "owner_kind": "job", "reason": "running_or_committed_job",
                                     "observed_at": stamp})
                for intent in (conn.execute("SELECT state FROM workflow_draft_dispatch_intents WHERE task_id=?", (tid,))
                               if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='workflow_draft_dispatch_intents'").fetchone() else []):
                    if intent[0] == "uncertain":
                        block("unknown", tid, "draft", "workflow_host_evidence_unavailable")
            if len(blockers) > 100:
                blockers = blockers[:99]
                block("unknown", root["id"], "scan", "scan_incomplete")
            stored_held = bool(row and row["held"])
            effective = stored_held and root["status"] not in TERMINAL
            return {"org_slug": self.org_slug, "root_task_id": root["id"],
                    "lifecycle_status": root["status"], "held": stored_held,
                    "effective_hold": effective, "generation": row["generation"] if row else 0,
                    "control_state": "unheld" if not effective else "pausing" if blockers or not complete else "paused",
                    "paused_at": row["paused_at"] if row else None,
                    "resumed_at": row["resumed_at"] if row else None,
                    "blockers": blockers, "evidence_complete": complete, "observed_at": stamp}
