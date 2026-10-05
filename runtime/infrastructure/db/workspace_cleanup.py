from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from pydantic import ValidationError

from runtime.infrastructure.db._shared import _parse_dt, _synchronized
from runtime.models import TaskStatus


def _iso_datetime_separator_index(value: str) -> int | None:
    """Return CPython's single ISO date/time boundary for raw-hour checks.

    This intentionally mirrors only the standard parser's date-boundary
    choice, not its timestamp parser.  In the extended week-date overlap,
    ``YYYY-Www-`` is the separator at offset 8 when a digit at offset 10
    makes both readings possible.  Guessing both offsets would turn a valid
    minute field into an apparent hour 24.
    """
    if len(value) <= 7:
        return None
    if value[4] == "-":
        if len(value) > 5 and value[5] == "W":
            if len(value) > 8 and value[8] == "-":
                if len(value) > 10 and value[10].isascii() and value[10].isdigit():
                    return 8
                return 10
            return 8
        return 10
    if value[4] == "W":
        index = 7
        while index < len(value) and value[index].isascii() and value[index].isdigit():
            index += 1
        if index < 9:
            return index
        return 7 if index % 2 == 0 else 8
    return 8


def _has_raw_iso_hour_24(value: str) -> bool:
    """Identify parser-selected raw hour 24 without narrowing ISO parsing."""
    # ``_parse_dt`` replaces Z before delegating to the standard parser; that
    # replacement is after the date/time boundary and cannot alter this index.
    boundary = _iso_datetime_separator_index(value)
    return boundary is not None and value[boundary + 1:boundary + 3] == "24"


def _is_aware_datetime(value: object) -> bool:
    """Return whether one persisted ordering value is a usable aware ISO time.

    This deliberately shares the helper's existing parser rather than treating
    SQLite's permissive date functions as the timestamp authority.  It is used
    both inside the bounded newer-owner query and after bounded reads; it never
    creates a second lookup.
    """
    if not isinstance(value, str):
        return False
    # CPython 3.14 normalizes ISO hour 24 to the next day's midnight.  The
    # finite selector instead has an explicit persisted-ordering contract: an
    # hour-24 value is malformed.  Keep this a narrow exception around the
    # standard parser rather than a format whitelist, so all other parser-valid
    # ISO forms retain their existing behavior.
    try:
        parsed = _parse_dt(value)
    except (TypeError, ValueError):
        return False
    return parsed.tzinfo is not None and not _has_raw_iso_hour_24(value)

_WORKSPACE_CLEANUP_BRIEF_MARKER = "HAPPYRANCH SYSTEM WORKSPACE CLEANUP RUN (daemon-triggered)"
_WORKSPACE_CLEANUP_TERMINAL_STATUSES = frozenset({
    TaskStatus.COMPLETED.value,
    TaskStatus.FAILED.value,
    TaskStatus.CANCELLED.value,
    TaskStatus.SUPERSEDED.value,
})


@dataclass(frozen=True)
class WorkspaceCleanupReclamationCandidate:
    """One conservatively selected dormant scratch root.

    This is a read-only planning shape.  In particular, a candidate is not an
    action permit: the later hook must still make its fresh owner/config and
    unchanged consumer admissions.
    """

    task_id: str
    session_id: str
    scratch_path: Path
    result: dict


@dataclass(frozen=True)
class WorkspaceCleanupReclamationSelection:
    """Bounded output for the future cleanup-action hook.

    ``read_observations`` is deliberately exposed so the hook can account for
    its own config/owner observations without guessing at helper internals.
    """

    owner_task_id: str
    candidates: tuple[WorkspaceCleanupReclamationCandidate, ...]
    read_observations: tuple[str, ...]


@dataclass(frozen=True)
class WorkspaceCleanupMarkerHistorySummary:
    """Complete per-agent marker-row summary for the daily trigger decision.

    Read-only planning shape; grants no action authority.  ``count`` is the
    exact all-history marker-row count (no saturation/cutoff),
    ``newest_created_at`` is the newest marker instant by UTC comparison with
    microsecond precision (never the SQL text-sort winner), and
    ``has_unfinished`` is true when ANY marker row in the complete history is
    non-terminal.
    """

    count: int
    newest_created_at: datetime | None
    has_unfinished: bool


# Bounded PER-PAGE materialization for the complete marker-history reader —
# explicitly not a logical history cap.  Completeness is independent of this
# value: the rowid keyset loop ends only on a short/empty page.
_CLEANUP_HISTORY_PAGE_SIZE = 1000

# Shared SELECT for the stale never-started pending observation (THR-195): the
# single source of truth for BOTH the managed-store scan
# (``Database.list_stale_pending_jobs``) and the read-only registry scan
# (``scan_stale_pending_jobs_readonly``), so the observation predicate
# (``status='pending' AND started_at IS NULL AND created_at <= cutoff``) can
# never drift between the two paths.
_STALE_PENDING_JOBS_SCAN_SQL = (
    "SELECT id, task_id, agent_name, title, review_required, created_at "
    "FROM jobs WHERE status='pending' AND started_at IS NULL "
    "AND created_at <= ? ORDER BY created_at, id"
)

# Active-WAL observation is a DIRECT read of the source store (founder
# ruling TASK-5542/TASK-5544): the temporary snapshot/copy machinery is
# retired entirely. An active-WAL source is opened in place with a genuine
# SQLite read-only connection (``mode=ro``) which consults the ``-wal`` so
# WAL-only committed candidates are observed. The FOUNDER CONTRACT protects
# the durable source ``happyranch.db`` and ``happyranch.db-wal`` BYTES ONLY:
# SQLite's WAL reader — even ``mode=ro`` — initializes WAL shared memory and
# may CREATE, MODIFY, or REMOVE the WAL-index ``happyranch.db-shm`` as
# transient reader/lock/index behavior, and that is explicitly permitted
# (TASK-5544 ruling; creation/modification/removal are all allowed, and no
# ``-shm`` existence/hash/mtime identity is ever asserted). The source main
# DB and ``-wal`` are NEVER written: a read-only connection cannot append WAL
# frames, checkpoint, recover, or run DDL/DML, so both stay byte-identical
# before/after every observation. No snapshot, no copy, no temp directory
# anywhere.


def _scan_stale_pending_jobs_direct_wal(
    db_path: Path, cutoff_iso: str,
) -> list[dict]:
    """Read-only WAL-aware observation directly on the SOURCE store.

    Founder-authorized fourth-round correction (TASK-5542): the active-WAL
    source is opened in place with a genuine SQLite read-only connection
    (``file:...?mode=ro``) for the duration of one query and closed. The
    reader consults the ``-wal`` so candidates committed only to the WAL are
    observed; SQLite's own WAL-reader protocol gives every reader a coherent
    committed view without any copy or stat-guard. The source main DB and
    ``-wal`` are never written (a read-only connection cannot append WAL
    frames, checkpoint, or recover), so both files stay byte-identical
    before/after every observation and no row/schema/audit state can change.

    SQLite's WAL reader may CREATE, MODIFY, or REMOVE the source
    ``-shm`` (WAL-index shared memory) as transient reader/lock/index
    behavior — explicitly permitted by the founder contract (TASK-5544); no
    ``-shm`` existence/hash/mtime identity is asserted. Only the durable
    source ``happyranch.db`` and ``happyranch.db-wal`` bytes are protected
    (byte-identical before/after).

    Fail closed: a missing main DB is handled by the caller (``[]``); a
    malformed main file raises ``sqlite3.DatabaseError`` and a schema without
    a ``jobs`` table raises ``sqlite3.OperationalError`` — observation never
    fabricates candidates and never mutates the source.
    """
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            _STALE_PENDING_JOBS_SCAN_SQL, (cutoff_iso,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def scan_stale_pending_jobs_readonly(
    db_path: Path, cutoff_iso: str,
) -> list[dict]:
    """Read-only stale-pending observation over one org store.

    THR-195 observation MUST NOT durably mutate any store: it never creates a
    missing DB, never enables WAL, never runs the schema migration guards, and
    never writes the source ``-wal``. The founder contract (TASK-5544)
    protects the durable source ``happyranch.db`` and ``happyranch.db-wal``
    BYTES ONLY — the SQLite WAL-index ``happyranch.db-shm`` may be created,
    modified, or removed by read-side WAL access and that is explicitly
    permitted, so no ``-shm`` identity is ever asserted. This helper
    therefore never opens the source with ``Database(db_path)`` (whose
    ``__init__`` creates the file, enables WAL, and runs migrations).

    Route selection: a cleanly-closed store has no ``-wal``/``-shm`` (SQLite
    checkpoints and removes them on the last close), so the main file holds
    every committed row — ``immutable=1`` reads it fully and provably cannot
    create sidecars. When sidecars exist (store open in this process — e.g. a
    loaded org — or crash leftovers), the scan opens the SOURCE directly with
    a genuine read-only WAL-aware connection (``mode=ro``): WAL-only
    committed candidates are observed, the source main DB and ``-wal`` stay
    byte-identical before/after, and the source ``-shm`` is the explicitly
    permitted shared-memory surface (creation/modification/removal by the
    WAL reader allowed; founder ruling TASK-5544; no snapshot/copy/temp
    directory is used). Either way the scan sees every committed candidate
    row and durably writes only the permitted ``-shm`` shared-memory surface
    (which it may create or remove) — never the source ``.db``/``-wal``.

    A missing DB file returns ``[]`` — nothing to observe, nothing created.
    A store that cannot be read (malformed file, or a pre-migration/
    irrelevant schema without a ``jobs`` table) raises
    ``sqlite3.DatabaseError``/``OperationalError``: fail closed at this leaf —
    never mutate, never fabricate candidates; the all-org coordinator
    (``scan_all_org_stale_pending``) isolates and logs such a failure so it
    cannot abort daemon startup or suppress other org roots.
    """
    if not db_path.is_file():
        return []
    wal = Path(f"{db_path}-wal")
    shm = Path(f"{db_path}-shm")
    if wal.exists() or shm.exists():
        return _scan_stale_pending_jobs_direct_wal(db_path, cutoff_iso)
    conn = sqlite3.connect(f"file:{db_path}?immutable=1", uri=True)
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(_STALE_PENDING_JOBS_SCAN_SQL, (cutoff_iso,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


class WorkspaceCleanupMixin:
    @_synchronized
    def list_workspace_cleanup_activity(self, agent: str, limit: int = 5) -> list[dict]:
        """Read latest distinct own-agent scheduled or exact-marker manual reports.

        Historical same-agent trigger audits remain eligible. Manual eligibility
        is exactly the first line (alone, LF or CRLF), never a prefix/substring.
        This display predicate grants no action authority or daemon run count.
        Eligibility precedes the limit; results remain latest same-agent by ID.
        """
        manual_marker = "HAPPYRANCH SYSTEM WORKSPACE CLEANUP RUN (manual-dispatch)"

        rows = self._conn.execute(
            """SELECT t.id AS task_id, t.status, t.created_at,
                      (SELECT r.status FROM task_results r
                       WHERE r.task_id=t.id AND r.agent=?
                       ORDER BY r.id DESC LIMIT 1) AS result_status,
                      (SELECT r.output_summary FROM task_results r
                       WHERE r.task_id=t.id AND r.agent=?
                       ORDER BY r.id DESC LIMIT 1) AS output_summary
               FROM tasks t
               WHERE t.assigned_agent=? AND (
                   EXISTS (SELECT 1 FROM audit_log a WHERE a.task_id=t.id
                           AND a.action='workspace_cleanup_triggered' AND a.agent=?)
                   OR t.brief=?
                   OR substr(t.brief, 1, length(?) + 1)=? || char(10)
                   OR substr(t.brief, 1, length(?) + 2)=? || char(13) || char(10)
               )
               ORDER BY t.created_at DESC, t.id DESC LIMIT ?""",
            (agent, agent, agent, agent, manual_marker, manual_marker, manual_marker,
             manual_marker, manual_marker, limit),
        ).fetchall()
        return [dict(row) for row in rows]

    @_synchronized
    def summarize_workspace_cleanup_marker_history(
        self,
        brief_prefix: str,
        *,
        assigned_agent: str,
        page_size: int = _CLEANUP_HISTORY_PAGE_SIZE,
    ) -> WorkspaceCleanupMarkerHistorySummary:
        """Complete read-only summary of one agent's daemon-cleanup marker rows.

        Predicate is exactly the existing marker/agent semantics
        (``assigned_agent = ? AND brief LIKE ? ESCAPE '\\'`` with the same
        ``\\ % _`` escaping as :meth:`list_tasks_by_brief_prefix`), but this
        reader scans ALL matching rows in bounded ``rowid`` keyset pages and
        computes the exact count, the newest UTC instant (microsecond
        precision, never the SQL text-sort winner) and whether ANY row is
        non-terminal.  ``page_size`` bounds materialization per page only; it
        is never a logical history cutoff.

        Snapshot semantics: the complete page loop is one synchronous
        ``_synchronized`` call with no awaits.  When the connection is not
        already in a transaction the reader begins its own read transaction,
        pins the snapshot with its first SELECT, and rolls it back in
        ``finally`` on success or failure.  When a caller already owns a
        transaction the reader neither begins, commits nor rolls it back; it
        preserves the caller's transaction and pending writes.

        Any SQLite/query/fetch failure or timestamp that cannot yield a
        datetime propagates — a partial or fabricated summary is never
        returned.
        """
        if isinstance(page_size, bool) or not isinstance(page_size, int):
            raise ValueError("page_size must be a positive integer")
        if page_size <= 0:
            raise ValueError("page_size must be a positive integer")

        escaped = (
            brief_prefix.replace("\\", "\\\\")
            .replace("%", "\\%")
            .replace("_", "\\_")
        )
        pattern = escaped + "%"
        owned = not self._conn.in_transaction
        if owned:
            self._conn.execute("BEGIN")
        try:
            count = 0
            newest: datetime | None = None
            has_unfinished = False
            last_rowid: int | None = None
            while True:
                if last_rowid is None:
                    cursor = self._conn.execute(
                        "SELECT rowid, created_at, status FROM tasks "
                        "WHERE assigned_agent = ? AND brief LIKE ? ESCAPE '\\' "
                        "ORDER BY rowid ASC LIMIT ?",
                        (assigned_agent, pattern, page_size),
                    )
                else:
                    cursor = self._conn.execute(
                        "SELECT rowid, created_at, status FROM tasks "
                        "WHERE assigned_agent = ? AND brief LIKE ? ESCAPE '\\' "
                        "AND rowid > ? ORDER BY rowid ASC LIMIT ?",
                        (assigned_agent, pattern, last_rowid, page_size),
                    )
                rows = cursor.fetchall()
                for row in rows:
                    count += 1
                    last_rowid = row["rowid"]
                    parsed = _parse_dt(row["created_at"])
                    if parsed.tzinfo is None:
                        parsed = parsed.replace(tzinfo=timezone.utc)
                    else:
                        parsed = parsed.astimezone(timezone.utc)
                    if newest is None or parsed > newest:
                        newest = parsed
                    if row["status"] not in _WORKSPACE_CLEANUP_TERMINAL_STATUSES:
                        has_unfinished = True
                if len(rows) < page_size:
                    break
            return WorkspaceCleanupMarkerHistorySummary(
                count=count,
                newest_created_at=newest,
                has_unfinished=has_unfinished,
            )
        finally:
            if owned:
                self._conn.rollback()

    @_synchronized
    def select_workspace_cleanup_reclamation_candidates(
        self,
        *,
        owner_task_id: str,
        agent: str,
        stale_orchestration_step_count: int,
        claimed_next_step_count: int,
        canonical_workspace: Path,
        authoritative_workspace: Path,
        admit_observation: Callable[[str], bool] | None = None,
    ) -> WorkspaceCleanupReclamationSelection | None:
        """Validate one claimed cleanup owner and build its finite shortlist.

        Every database read has a named pre-admission.  The later run-step
        hook supplies its monotonic deadline/read-budget callback; this helper
        neither starts a clock nor performs config, consumer, audit, or write
        work.  Refusal is intentionally represented by ``None`` so callers
        cannot confuse a partial shortlist with a safe action.
        """
        observations: list[str] = []

        def admit(name: str) -> bool:
            if admit_observation is not None and not admit_observation(name):
                return False
            observations.append(name)
            return True

        # These are invocation-local CAS inputs, not durable-owner facts.
        # Refuse malformed supplied claim context before even admitting the
        # owner read; a valid supplied pair still requires that fresh read.
        if stale_orchestration_step_count != 0 or claimed_next_step_count != 1:
            return None

        # 1. Current durable owner.  The registered identity and canonical
        # workspace are supplied by the authoritative caller; no roster read
        # is invented here.  The stale count and CAS-written next count bind
        # this read-only helper to the invocation that actually won the
        # initial (0 -> 1) claim; cleanup ordinal is not a claim count.
        if not admit("owner"):
            return None
        try:
            owner = self.get_task(owner_task_id)
        except (sqlite3.Error, ValidationError, TypeError, ValueError):
            return None
        if (
            owner is None
            or owner.id != owner_task_id
            or owner.assigned_agent != agent
            or owner.status is not TaskStatus.IN_PROGRESS
            or owner.block_kind is not None
            or owner.cancelled_at is not None
            or owner.orchestration_step_count != 1
            or canonical_workspace != authoritative_workspace
            or not owner.brief.startswith(_WORKSPACE_CLEANUP_BRIEF_MARKER)
        ):
            return None

        # 2. Exactly one marker on this owner, without an agent prefilter.
        if not admit("marker"):
            return None
        try:
            marker_rows = self._conn.execute(
                """SELECT agent, payload FROM audit_log
                   WHERE task_id=? AND action='workspace_cleanup_triggered'
                   ORDER BY id ASC LIMIT 2""",
                (owner_task_id,),
            ).fetchall()
        except sqlite3.Error:
            return None
        if len(marker_rows) != 1 or marker_rows[0]["agent"] != agent:
            return None
        try:
            marker_payload = json.loads(marker_rows[0]["payload"])
            run_number = marker_payload["run_number"]
            brief_kind = marker_payload["brief_kind"]
        except (TypeError, KeyError, UnicodeDecodeError, json.JSONDecodeError):
            return None
        if type(run_number) is not int or run_number < 3 or brief_kind != "cleanup":
            return None

        # 3. Complete bounded history.  Tuple comparison uses the stored
        # bytewise strings, never numeric TASK suffixes.
        if not admit("history"):
            return None
        escaped_marker = _WORKSPACE_CLEANUP_BRIEF_MARKER.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        try:
            history = self._conn.execute(
                """SELECT id, created_at FROM tasks
                   WHERE assigned_agent=? AND brief LIKE ? ESCAPE '\\'
                   ORDER BY created_at DESC, id DESC LIMIT 1001""",
                (agent, escaped_marker + "%"),
            ).fetchall()
        except sqlite3.Error:
            return None
        if len(history) == 1001:
            return None
        try:
            history_tuples = [(str(row["created_at"]), str(row["id"])) for row in history]
            if any(not _is_aware_datetime(created_at) for created_at, _ in history_tuples):
                return None
        except (TypeError, ValueError):
            return None
        if len(set(history_tuples)) != len(history_tuples) or history_tuples != sorted(history_tuples, reverse=True):
            return None
        owner_matches = [item for item in history_tuples if item[1] == owner.id]
        if len(owner_matches) != 1:
            return None
        owner_tuple = owner_matches[0]
        older_count = sum(1 for item in history_tuples if item < owner_tuple)
        if run_number != older_count + 1:
            return None

        # 4. A delayed original is permitted only when no later marker-bearing
        # owner (including an unreadable/orphaned one) exists.
        if not admit("newer_owner"):
            return None
        # SQLite's date functions accept and normalize values that the
        # history/candidate parser rejects, and they recognize only a subset
        # of accepted ISO representations.  Register the same pure validator
        # for this one bounded query, then immediately unregister it.  This
        # adds no SQL observation or connection-wide policy.
        timestamp_predicate = "_workspace_cleanup_is_aware_datetime"
        registered_timestamp_predicate = False
        try:
            self._conn.create_function(
                timestamp_predicate, 1, lambda value: int(_is_aware_datetime(value)),
            )
            registered_timestamp_predicate = True
            newer = self._conn.execute(
            f"""SELECT a.task_id, a.agent, a.payload, t.created_at, t.status
               FROM audit_log a LEFT JOIN tasks t ON t.id=a.task_id
               WHERE a.action='workspace_cleanup_triggered' AND a.task_id<>?
                 AND (t.id IS NULL OR {timestamp_predicate}(t.created_at)=0
                      OR t.created_at>? OR (t.created_at=? AND t.id>?))
               ORDER BY CASE WHEN t.id IS NULL OR {timestamp_predicate}(t.created_at)=0 THEN 0 ELSE 1 END,
                        t.created_at DESC, a.task_id DESC LIMIT 2""",
                (owner_task_id, owner_tuple[0], owner_tuple[0], owner_task_id),
            ).fetchall()
        except sqlite3.Error:
            return None
        finally:
            if registered_timestamp_predicate:
                self._conn.create_function(timestamp_predicate, 1, None)
        if newer:
            return None

        # 5. Read six raw terminal candidates before applying the age filter.
        if not admit("candidates"):
            return None
        try:
            raw_candidates = self._conn.execute(
                """SELECT id, status, assigned_agent, created_at, completed_at,
                          current_session_id
                   FROM tasks
                   WHERE assigned_agent=?
                     AND status IN ('completed','failed','cancelled','superseded')
                   ORDER BY completed_at ASC, id ASC LIMIT 6""",
                (agent,),
            ).fetchall()
        except sqlite3.Error:
            return None
        if len(raw_candidates) == 6:
            return None
        candidate_rows: list[sqlite3.Row] = []
        try:
            for row in raw_candidates:
                created_at = row["created_at"]
                completed_at = row["completed_at"]
                if (
                    not isinstance(created_at, str)
                    or not isinstance(completed_at, str)
                    or not _is_aware_datetime(created_at)
                    or not _is_aware_datetime(completed_at)
                ):
                    return None
                if row["status"] not in _WORKSPACE_CLEANUP_TERMINAL_STATUSES:
                    return None
                if (completed_at, str(row["id"])) < owner_tuple:
                    candidate_rows.append(row)
        except (TypeError, ValueError):
            return None

        # 6/7. Complete graph snapshots.  Only components touching the owner
        # or selected candidates are validated; unrelated malformed data is
        # deliberately ignored.
        if not admit("graph_tasks"):
            return None
        try:
            graph_tasks = self._conn.execute(
                """SELECT id, assigned_agent, status FROM tasks ORDER BY id ASC LIMIT 10001""",
            ).fetchall()
        except sqlite3.Error:
            return None
        if len(graph_tasks) == 10001:
            return None
        if not admit("graph_edges"):
            return None
        try:
            graph_edges = self._conn.execute(
                """SELECT child_id, relative_id FROM (
                        SELECT id AS child_id, parent_task_id AS relative_id FROM tasks
                        WHERE parent_task_id IS NOT NULL
                        UNION ALL
                        SELECT id AS child_id, revisit_of_task_id AS relative_id FROM tasks
                        WHERE revisit_of_task_id IS NOT NULL
                    ) ORDER BY child_id ASC, relative_id ASC LIMIT 20001""",
            ).fetchall()
        except sqlite3.Error:
            return None
        if len(graph_edges) == 20001:
            return None
        nodes = {str(row["id"]): row for row in graph_tasks}
        adjacency: dict[str, set[str]] = {node_id: set() for node_id in nodes}
        directed: dict[str, set[str]] = {node_id: set() for node_id in nodes}
        for edge in graph_edges:
            child_id, relative_id = str(edge["child_id"]), str(edge["relative_id"])
            if child_id in adjacency:
                adjacency[child_id].add(relative_id)
                directed[child_id].add(relative_id)
            if relative_id in adjacency:
                adjacency[relative_id].add(child_id)

        selected_ids = {str(row["id"]) for row in candidate_rows}
        required_roots = {owner_task_id, *selected_ids}
        for root in required_roots:
            if root not in nodes:
                return None
            visited: set[str] = set()
            stack: list[tuple[str, str | None]] = [(root, None)]
            while stack:
                node_id, parent_id = stack.pop()
                if node_id in visited:
                    return None
                node = nodes.get(node_id)
                if node is None or node["status"] not in {
                    "pending", "in_progress", "escalated", * _WORKSPACE_CLEANUP_TERMINAL_STATUSES,
                }:
                    return None
                visited.add(node_id)
                for neighbour in adjacency.get(node_id, ()):
                    if neighbour not in nodes:
                        return None
                    if neighbour == parent_id:
                        continue
                    relative = nodes[neighbour]
                    if relative["assigned_agent"] != agent and relative["status"] not in _WORKSPACE_CLEANUP_TERMINAL_STATUSES:
                        return None
                    stack.append((neighbour, node_id))
            if root != owner_task_id and owner_task_id in visited:
                return None
            # Directed parent/revisit cycles need a second traversal because
            # undirected deduplication intentionally suppresses reciprocal
            # edges.  Keep it iterative: a valid component may contain all
            # 10,000 admitted rows and must not depend on Python's recursion
            # limit.
            directed_state: dict[str, int] = {}
            for start in visited:
                if directed_state.get(start, 0) == 2:
                    continue
                directed_state[start] = 1
                directed_stack: list[tuple[str, object]] = [
                    (start, iter(relative for relative in directed.get(start, ()) if relative in visited)),
                ]
                while directed_stack:
                    node_id, relatives = directed_stack[-1]
                    try:
                        relative_id = next(relatives)
                    except StopIteration:
                        directed_state[node_id] = 2
                        directed_stack.pop()
                        continue
                    state = directed_state.get(relative_id, 0)
                    if state == 1:
                        return None
                    if state == 0:
                        directed_state[relative_id] = 1
                        directed_stack.append((
                            relative_id,
                            iter(relative for relative in directed.get(relative_id, ()) if relative in visited),
                        ))

        # One and only one exact persisted-result read follows for each
        # selected target.  A refusal starts no later result read.
        candidates: list[WorkspaceCleanupReclamationCandidate] = []
        for row in candidate_rows:
            task_id = str(row["id"])
            session_id = row["current_session_id"]
            if not isinstance(session_id, str) or not session_id:
                return None
            if not admit(f"result:{task_id}"):
                return None
            if not task_id.startswith("TASK-") or not task_id[5:].isdigit():
                return None
            try:
                result = self.get_latest_task_result(task_id, agent, session_id)
            except (sqlite3.Error, json.JSONDecodeError, ValidationError, TypeError, ValueError):
                return None
            if result is None or result.get("status") != row["status"]:
                return None
            candidates.append(WorkspaceCleanupReclamationCandidate(
                task_id=task_id,
                session_id=session_id,
                scratch_path=canonical_workspace / ".happyranch" / "task-tmp" / task_id,
                result=result,
            ))
        return WorkspaceCleanupReclamationSelection(
            owner_task_id=owner_task_id,
            candidates=tuple(candidates),
            read_observations=tuple(observations),
        )
