from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from datetime import datetime, timezone

from runtime.infrastructure.db._shared import _synchronized
from runtime.models import (
    BlockKind,
    TaskRecord,
    TaskStatus,
    ThreadInvocationPurpose,
    ThreadInvocationStatus,
)


class LineageTooDeep(Exception):
    """Ancestor walk exceeded the safety bound; indicates data corruption."""


@dataclass(frozen=True)
class VerifiedRetry:
    """Read-only provenance result; final spawn must revalidate under its writer lock."""

    path: tuple[str, ...]


@dataclass(frozen=True)
class InvalidLineage:
    reason: str


@dataclass(frozen=True)
class RetryClaim:
    """Original report ownership, captured before preflight or preparation."""

    task_id: str
    assigned_agent: str | None
    team: str
    current_session_id: str | None
    step: int
    active_chain: str | None
    active_fanout: str | None
    revision: int
    note: str | None
    status: TaskStatus
    block_kind: BlockKind | None
    cancelled_at: str | None
    result_row_id: int | None = None

    @classmethod
    def from_task(cls, task: TaskRecord, *, result_row_id: int | None = None):
        return cls(task.id, task.assigned_agent, task.team, task.current_session_id,
                   task.orchestration_step_count, task.active_chain, task.active_fanout,
                   task.revision_count, task.note, task.status, task.block_kind,
                   task.cancelled_at, result_row_id)


@dataclass(frozen=True)
class Committed:
    child_ids: tuple[str, ...]


@dataclass(frozen=True)
class LostClaim:
    """No durable mutation or enqueue is permitted for this stale input."""


SpawnOutcome = Committed | InvalidLineage | LostClaim


@dataclass(frozen=True)
class PendingRetry:
    claim: RetryClaim
    result_id: int
    feedback: str


class _RetryEvidenceRefusal(Exception):
    """Internal control flow for invalid recorded evidence, never SQLite errors."""


class TasksMixin:
    @_synchronized
    def insert_task(self, task: TaskRecord) -> None:
        params = (
            task.id,
            task.status.value,
            task.assigned_agent,
            task.team,
            task.brief,
            task.revision_count,
            task.created_at.isoformat(),
            task.updated_at.isoformat(),
            task.completed_at.isoformat() if task.completed_at else None,
            task.parent_task_id,
            task.revisit_of_task_id,
            task.dispatched_from_thread_id,
            task.block_kind.value if task.block_kind else None,
            task.note,
            task.orchestration_step_count,
            task.session_timeout_seconds,
            task.task_type,
            task.active_fanout,
            task.current_session_id,
            task.zombie_flagged_at.isoformat() if task.zombie_flagged_at else None,
        )
        self._conn.execute(
            """INSERT INTO tasks (id, status, assigned_agent, team, brief,
               revision_count, created_at, updated_at, completed_at, parent_task_id,
               revisit_of_task_id, dispatched_from_thread_id,
               block_kind, note,
               orchestration_step_count, session_timeout_seconds, task_type, active_fanout,
               current_session_id, zombie_flagged_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            params,
        )
        self._conn.commit()

    @_synchronized
    def get_task(self, task_id: str) -> TaskRecord | None:
        cursor = self._conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
        row = cursor.fetchone()
        if row is None:
            return None
        return TaskRecord(
            id=row["id"],
            status=row["status"],
            assigned_agent=row["assigned_agent"],
            team=row["team"],
            brief=row["brief"],
            revision_count=row["revision_count"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            completed_at=row["completed_at"],
            parent_task_id=row["parent_task_id"],
            revisit_of_task_id=row["revisit_of_task_id"],
            dispatched_from_thread_id=row["dispatched_from_thread_id"],
            block_kind=row["block_kind"],
            blocked_on_job_ids=row["blocked_on_job_ids"],
            active_chain=row["active_chain"],
            active_fanout=row["active_fanout"],
            note=row["note"],
            orchestration_step_count=row["orchestration_step_count"] or 0,
            final_output_dir=row["final_output_dir"],
            cancelled_at=row["cancelled_at"],
            last_heartbeat=row["last_heartbeat"],
            session_timeout_seconds=row["session_timeout_seconds"],
            task_type=row["task_type"],
            executor_pid=row["executor_pid"],
            current_session_id=row["current_session_id"],
            zombie_flagged_at=row["zombie_flagged_at"],
        )

    @_synchronized
    def list_tasks(
        self,
        limit: int = 20,
        assigned_agent: str | None = None,
        before_task_id: str | None = None,
        status: TaskStatus | str | None = None,
        block_kind: BlockKind | str | None = None,
        blocked_on_job_id: str | None = None,
    ) -> list[TaskRecord]:
        # Cursor pagination: callers pass the last task_id of the previous page
        # as `before_task_id`; we resolve its created_at and emit the next page
        # using (created_at, id) DESC for a stable tiebreak. `status` and
        # `block_kind` are optional equality filters (read-only backlog queries).
        # `blocked_on_job_id` is a DERIVE filter for the Jobs "if-approved"
        # cascade — finds tasks blocked on a specific job id.
        cursor_created_at: str | None = None
        if before_task_id is not None:
            row = self._conn.execute(
                "SELECT created_at FROM tasks WHERE id = ?", (before_task_id,),
            ).fetchone()
            if row is None:
                return []
            cursor_created_at = row["created_at"]

        # Assemble the WHERE clause dynamically: with four optional filter
        # dimensions (agent, status, block_kind, cursor) an if/elif tree would
        # be 2**4 branches. StrEnum members stringify to their value, so str()
        # accepts both the enum and a raw query-param string.
        conditions: list[str] = []
        params: list = []
        if assigned_agent is not None:
            conditions.append("assigned_agent = ?")
            params.append(assigned_agent)
        if status is not None:
            conditions.append("status = ?")
            params.append(str(status))
        if block_kind is not None:
            conditions.append("block_kind = ?")
            params.append(str(block_kind))
        if blocked_on_job_id is not None:
            # Mirror jobs_runner.py canonic pred: status + block_kind + LIKE.
            # Without the status/block_kind guard a task that was once
            # blocked on JOB-X but is now done/running leaks into the
            # "if approved" cascade. Path B changed the parked carrier
            # from blocked(blocked_on_job) to in_progress(blocked_on_job).
            conditions.append(
                "status = ? AND block_kind = ? AND blocked_on_job_ids LIKE ?"
            )
            params.extend([
                TaskStatus.IN_PROGRESS.value,
                BlockKind.BLOCKED_ON_JOB.value,
                f'%"{blocked_on_job_id}"%',
            ])
        if cursor_created_at is not None:
            conditions.append("(created_at, id) < (?, ?)")
            params.extend([cursor_created_at, before_task_id])
        where = f"WHERE {' AND '.join(conditions)} " if conditions else ""
        params.append(limit)
        cursor = self._conn.execute(
            f"SELECT * FROM tasks {where}"
            "ORDER BY created_at DESC, id DESC LIMIT ?",
            tuple(params),
        )
        return [
            TaskRecord(
                id=row["id"],
                status=row["status"],
                assigned_agent=row["assigned_agent"],
                team=row["team"],
                brief=row["brief"],
                revision_count=row["revision_count"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
                completed_at=row["completed_at"],
                parent_task_id=row["parent_task_id"],
                revisit_of_task_id=row["revisit_of_task_id"],
                dispatched_from_thread_id=row["dispatched_from_thread_id"],
                block_kind=row["block_kind"],
                blocked_on_job_ids=row["blocked_on_job_ids"],
                note=row["note"],
                orchestration_step_count=row["orchestration_step_count"] or 0,
                final_output_dir=row["final_output_dir"],
                cancelled_at=row["cancelled_at"],
                last_heartbeat=row["last_heartbeat"],
                session_timeout_seconds=row["session_timeout_seconds"],
                task_type=row["task_type"],
                executor_pid=row["executor_pid"],
                current_session_id=row["current_session_id"],
                zombie_flagged_at=row["zombie_flagged_at"],
            )
            for row in cursor.fetchall()
        ]

    @_synchronized
    def get_children(self, parent_task_id: str) -> list[str]:
        """Return direct children of a task, ordered by creation time."""
        cursor = self._conn.execute(
            "SELECT id FROM tasks WHERE parent_task_id = ? ORDER BY created_at",
            (parent_task_id,),
        )
        return [row["id"] for row in cursor.fetchall()]

    @_synchronized
    def get_descendant_task_ids(self, root_task_id: str) -> list[str]:
        """Return all descendant task IDs in the parent_task_id subtree
        (direct children, grandchildren, etc.). Excludes the root itself.

        Uses the same iterative get_children() walk as get_subtree_statuses().
        """
        ids: list[str] = []
        stack = list(self.get_children(root_task_id))
        while stack:
            child_id = stack.pop()
            ids.append(child_id)
            stack.extend(self.get_children(child_id))
        return ids

    # Severity ranking for subtree rollup: lower = worse.
    # escalated is the attention-grabbing worst (genuine founder attention);
    # superseded is the calmest. Under the Path-B stored model
    # (THR-037 Change B) a delegating/parked parent is in_progress (rank 2),
    # so a healthy delegating parent NO LONGER dominates its subtree to amber —
    # only a real escalated (0) or failed (1) descendant pulls the rollup up.
    # cancelled is a deliberate terminal stop with no pending work, so it ranks
    # calmer than completed. The deprecated 'blocked' value is intentionally
    # absent: any lingering blocked row falls to the default rank (99, calmest).
    _SEVERITY_RANK: dict[str, int] = {
        "escalated": 0,
        "failed": 1,
        "in_progress": 2,
        "pending": 3,
        "completed": 4,
        "cancelled": 5,
        "superseded": 6,
    }

    @_synchronized
    def get_subtree_statuses(self, root_task_id: str) -> list[str]:
        """Return the status values of all descendant tasks in the
        parent_task_id subtree (direct children, grandchildren, etc.).

        Walks the tree recursively via get_children(). Excludes the root
        task itself; only descendants are collected. An empty list means the
        root has no children (rollup = the root's own status).

        This is a DERIVE — no schema change; uses existing parent_task_id
        and get_children().
        """
        statuses: list[str] = []
        stack = list(self.get_children(root_task_id))
        while stack:
            child_id = stack.pop()
            child = self.get_task(child_id)
            if child is not None:
                statuses.append(child.status.value)
                stack.extend(self.get_children(child_id))
        return statuses

    def _worst_subtree_status(self, root_status: str, child_statuses: list[str]) -> str:
        """Return the worst status among a root's own status and its
        descendants' statuses.

        The rollup of a singleton subtree is the root's own status (P1: no
        guessed severity). Uses _SEVERITY_RANK — lowest rank wins.
        """
        worst = root_status
        worst_rank = self._SEVERITY_RANK.get(worst, 99)
        for s in child_statuses:
            rank = self._SEVERITY_RANK.get(s, 99)
            if rank < worst_rank:
                worst = s
                worst_rank = rank
        return worst

    def _get_subtree_tasks(self, root_task_id: str) -> list[TaskRecord]:
        """Cycle-safe, iterative parent_task_id descendant snapshot.

        Exact bounds: each reachable descendant id enters ``seen`` once, so the
        walk makes <= D get_task() calls and <= D + 1 get_children() calls and
        holds <= D TaskRecords, where D = reachable descendant count. A
        malformed parent_task_id cycle supplied directly to this private helper
        terminates on ``seen``; it cannot be reached from a NULL-parent
        list_roots root. The iterative walk also avoids recursion limits for
        ordinary deep trees.

        Private helper: left undecorated and called only under an existing
        synchronized public entry (``list_roots``). If ever exposed as a public
        ``Database`` method it must gain ``@_synchronized``.
        """
        seen = {root_task_id}
        out: list[TaskRecord] = []
        stack = list(self.get_children(root_task_id))
        while stack:
            tid = stack.pop()
            if tid in seen:
                continue
            seen.add(tid)
            task = self.get_task(tid)
            if task is None:
                continue
            out.append(task)
            stack.extend(self.get_children(tid))
        return out

    def _current_failed_contributions(
        self,
        desc: list[TaskRecord],
        by_id: dict[str, TaskRecord],
        succ: dict[str, list[str]],
    ) -> set[str]:
        """IDs of FAILED descendants that still contribute ``failed``.

        Mirrors ``run_step._current_unresolved_failed_leaves`` /
        ``_collect_leaf`` on the finite descendant snapshot without importing
        orchestration policy. ``_collect_leaf`` returns a node only at its
        no-successor base case, so across all FAILED seeds the contributing set
        is exactly the reachable terminal FAILED nodes: a FAILED descendant
        with a forward same-parent successor has no unresolved failed leaf of
        its own. A cycle (malformed lineage) is ambiguous, so it fails
        conservative and is KEPT, never silently retired.

        Exact bounds: one iterative colored DFS over the descendant nodes/edges
        is O(D + E) time and O(D) space; every node is colored once and every
        edge examined once. No recursion, so no RecursionError.
        """
        WHITE, GREY, BLACK = 0, 1, 2
        color = dict.fromkeys(by_id, WHITE)
        reaches_cycle: set[str] = set()
        for start in by_id:
            if color[start] != WHITE:
                continue
            color[start] = GREY
            stack = [(start, iter(succ.get(start, ())))]
            while stack:
                node, it = stack[-1]
                advanced = False
                for nxt in it:
                    if color[nxt] == GREY:              # back edge -> cycle
                        reaches_cycle.add(nxt)
                    elif color[nxt] == WHITE:
                        color[nxt] = GREY
                        stack.append((nxt, iter(succ.get(nxt, ()))))
                        advanced = True
                        break
                if advanced:
                    continue
                color[node] = BLACK
                if node in reaches_cycle or any(
                    n in reaches_cycle for n in succ.get(node, ())
                ):
                    reaches_cycle.add(node)             # ancestor of a cycle
                stack.pop()

        return {
            nid
            for nid, task in by_id.items()
            if task.status == TaskStatus.FAILED
            and (not succ.get(nid) or nid in reaches_cycle)
        }

    def _current_severity_rollup(self, root: TaskRecord) -> str:
        """Worst CURRENT status over the root's own status and its
        parent_task_id descendants. Only stale FAILED-descendant contributions
        are removed; every other status (escalated, active, cancelled,
        completed, superseded, legacy) contributes exactly as before.

        Forward links are only ``revisit_of_task_id`` where both endpoints are
        descendants inside this root and the successor shares the
        predecessor's parent. No timestamp or agent is ever consulted (no
        latest-wins). Cancellation removes a predecessor's stale ``failed``
        only because the lineage then has no unresolved failed leaf — never
        because cancellation is successful retirement.

        Private helper: called only under ``list_roots`` (see
        ``_get_subtree_tasks``).
        """
        desc = self._get_subtree_tasks(root.id)
        by_id = {d.id: d for d in desc}
        succ: dict[str, list[str]] = {}
        for d in desc:
            pred = d.revisit_of_task_id
            if (
                pred is not None
                and pred in by_id                              # predecessor is a descendant
                and by_id[pred].parent_task_id == d.parent_task_id   # same parent
            ):
                succ.setdefault(pred, []).append(d.id)
        failed_now = self._current_failed_contributions(desc, by_id, succ)
        current = [
            d.status.value
            for d in desc
            if d.status != TaskStatus.FAILED or d.id in failed_now
        ]
        return self._worst_subtree_status(root.status.value, current)

    @_synchronized
    def list_roots(
        self,
        limit: int = 20,
        assigned_agent: str | None = None,
        before_task_id: str | None = None,
        status: TaskStatus | str | None = None,
        block_kind: BlockKind | str | None = None,
    ) -> list[TaskRecord]:
        """Return root tasks (parent_task_id IS NULL) with cursor pagination,
        same filter parameters as list_tasks(), plus a per-root _severity_rollup.

        The _severity_rollup attribute (str) is the worst CURRENT status among
        the root's own status and its parent_task_id subtree. Only a historical
        FAILED descendant contribution is curated: a FAILED descendant whose
        forward same-parent revisit lineage leaves no unresolved FAILED leaf
        (a COMPLETED/SUPERSEDED/active/cancelled successor, or a malformed
        cycle handled conservatively) no longer dominates. Every other status,
        the root's own severity, and all escalations are preserved; a root
        without children shows its own status. Set as a dynamic attribute on
        the TaskRecord (not a model field — DERIVE, no schema). See
        ``_current_severity_rollup``.
        """
        cursor_created_at: str | None = None
        if before_task_id is not None:
            row = self._conn.execute(
                "SELECT created_at FROM tasks WHERE id = ?", (before_task_id,),
            ).fetchone()
            if row is None:
                return []
            cursor_created_at = row["created_at"]

        conditions = ["parent_task_id IS NULL"]
        params: list = []
        if assigned_agent is not None:
            conditions.append("assigned_agent = ?")
            params.append(assigned_agent)
        if status is not None:
            conditions.append("status = ?")
            params.append(str(status))
        if block_kind is not None:
            conditions.append("block_kind = ?")
            params.append(str(block_kind))
        if cursor_created_at is not None:
            conditions.append("(created_at, id) < (?, ?)")
            params.extend([cursor_created_at, before_task_id])
        where = f"WHERE {' AND '.join(conditions)} "
        params.append(limit)
        cursor = self._conn.execute(
            f"SELECT * FROM tasks {where}"
            "ORDER BY created_at DESC, id DESC LIMIT ?",
            tuple(params),
        )
        results: list[TaskRecord] = []
        for row in cursor.fetchall():
            task = TaskRecord(
                id=row["id"],
                status=row["status"],
                assigned_agent=row["assigned_agent"],
                team=row["team"],
                brief=row["brief"],
                revision_count=row["revision_count"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
                completed_at=row["completed_at"],
                parent_task_id=row["parent_task_id"],
                revisit_of_task_id=row["revisit_of_task_id"],
                dispatched_from_thread_id=row["dispatched_from_thread_id"],
                block_kind=row["block_kind"],
                blocked_on_job_ids=row["blocked_on_job_ids"],
                note=row["note"],
                orchestration_step_count=row["orchestration_step_count"] or 0,
                final_output_dir=row["final_output_dir"],
                cancelled_at=row["cancelled_at"],
                last_heartbeat=row["last_heartbeat"],
                session_timeout_seconds=row["session_timeout_seconds"],
                task_type=row["task_type"],
                executor_pid=row["executor_pid"],
                current_session_id=row["current_session_id"],
                zombie_flagged_at=row["zombie_flagged_at"],
            )
            object.__setattr__(
                task, '_severity_rollup',
                self._current_severity_rollup(task),
            )
            results.append(task)
        return results

    @_synchronized
    def list_tasks_by_brief_prefix(
        self,
        brief_prefix: str,
        *,
        assigned_agent: str | None = None,
        limit: int = 200,
    ) -> list[TaskRecord]:
        """Tasks whose brief starts with ``brief_prefix``, newest-first.

        Authoritative SQL-side marker filter (THR-195 / TASK-6043): unlike a
        bounded scan of recent ordinary tasks, an SQL ``brief LIKE`` filter
        can never be exhausted by newer non-matching rows, so older
        marker-matched rows cannot be hidden. ``%`` and ``_`` in the prefix
        are escaped (treated literally). ``assigned_agent`` narrows to one
        agent's rows when given.
        """
        escaped = (
            brief_prefix.replace("\\", "\\\\")
            .replace("%", "\\%")
            .replace("_", "\\_")
        )
        conditions = ["brief LIKE ? ESCAPE '\\'"]
        params: list = [escaped + "%"]
        if assigned_agent is not None:
            conditions.append("assigned_agent = ?")
            params.append(assigned_agent)
        params.append(limit)
        cursor = self._conn.execute(
            f"SELECT * FROM tasks WHERE {' AND '.join(conditions)} "
            "ORDER BY created_at DESC, id DESC LIMIT ?",
            tuple(params),
        )
        return [
            TaskRecord(
                id=row["id"],
                status=row["status"],
                assigned_agent=row["assigned_agent"],
                team=row["team"],
                brief=row["brief"],
                revision_count=row["revision_count"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
                completed_at=row["completed_at"],
                parent_task_id=row["parent_task_id"],
                revisit_of_task_id=row["revisit_of_task_id"],
                dispatched_from_thread_id=row["dispatched_from_thread_id"],
                block_kind=row["block_kind"],
                blocked_on_job_ids=row["blocked_on_job_ids"],
                note=row["note"],
                orchestration_step_count=row["orchestration_step_count"] or 0,
                final_output_dir=row["final_output_dir"],
                cancelled_at=row["cancelled_at"],
                last_heartbeat=row["last_heartbeat"],
                session_timeout_seconds=row["session_timeout_seconds"],
                task_type=row["task_type"],
                executor_pid=row["executor_pid"],
                current_session_id=row["current_session_id"],
                zombie_flagged_at=row["zombie_flagged_at"],
            )
            for row in cursor.fetchall()
        ]

    @_synchronized
    def list_tasks_by_thread(
        self, thread_id: str,
    ) -> list[dict]:
        """Return tasks dispatched from a thread, newest-first.

        Uses the existing idx_tasks_dispatched_from_thread_id partial index.
        Returns lightweight summary dicts with the fields the frontend needs:
        id, status, brief, assigned_agent, created_at, parent_task_id.
        """
        cursor = self._conn.execute(
            "SELECT id, status, brief, assigned_agent, created_at, parent_task_id "
            "FROM tasks WHERE dispatched_from_thread_id = ? "
            "ORDER BY created_at DESC",
            (thread_id,),
        )
        return [
            {
                "id": row["id"],
                "status": row["status"],
                "brief": row["brief"],
                "assigned_agent": row["assigned_agent"],
                "created_at": row["created_at"],
                "parent_task_id": row["parent_task_id"],
            }
            for row in cursor.fetchall()
        ]

    @_synchronized
    def get_direct_revisits(self, task_id: str) -> list[str]:
        """Return IDs of tasks whose revisit_of_task_id points at this task,
        ordered by creation. Uses idx_tasks_revisit_of.
        """
        cursor = self._conn.execute(
            "SELECT id FROM tasks WHERE revisit_of_task_id = ? ORDER BY created_at",
            (task_id,),
        )
        return [row["id"] for row in cursor.fetchall()]

    @_synchronized
    def batch_get_direct_revisits(
        self, task_ids: list[str],
    ) -> dict[str, list[str]]:
        """Return direct revisits for multiple task_ids in a single query.

        Avoids the N+1 pattern when a list route needs direct_revisits for
        every returned item. Uses idx_tasks_revisit_of.
        """
        if not task_ids:
            return {}
        placeholders = ','.join(['?'] * len(task_ids))
        cursor = self._conn.execute(
            f"SELECT revisit_of_task_id, id FROM tasks"
            f" WHERE revisit_of_task_id IN ({placeholders})"
            f" ORDER BY created_at",
            tuple(task_ids),
        )
        result: dict[str, list[str]] = {tid: [] for tid in task_ids}
        for row in cursor.fetchall():
            root_id = row["revisit_of_task_id"]
            result.setdefault(root_id, []).append(row["id"])
        return result

    @_synchronized
    def walk_ancestors(self, task_id: str, max_hops: int = 20) -> list[TaskRecord]:
        """Return [task, parent, ..., root] by following parent_task_id.

        Raises LineageTooDeep if the walk exceeds max_hops (defensive bound;
        real lineages are 2-4 deep). A missing intermediate task truncates the
        walk silently — callers see the chain they could reconstruct.
        """
        chain: list[TaskRecord] = []
        current_id: str | None = task_id
        for _ in range(max_hops):
            if current_id is None:
                return chain
            task = self.get_task(current_id)
            if task is None:
                return chain
            chain.append(task)
            current_id = task.parent_task_id
        if current_id is not None:
            raise LineageTooDeep(f"walk from {task_id} exceeded {max_hops} hops")
        return chain

    @_synchronized
    def walk_revisit_chain(
        self, task_id: str, max_hops: int = 20, truncate: bool = False,
    ) -> list[TaskRecord]:
        """Return [task, predecessor, ..., original] by following revisit_of_task_id.

        Sideways edge — does NOT cross into parent_task_id ancestor space.
        Non-revisit tasks return [task]. Missing task returns []. Overruns
        raise LineageTooDeep by default (same pattern as walk_ancestors); pass
        truncate=True to return the first max_hops entries instead — read
        paths use this because revisit history grows naturally over a task's
        lifetime and must not 500 once it exceeds the defensive bound.
        """
        chain: list[TaskRecord] = []
        current_id: str | None = task_id
        for _ in range(max_hops):
            if current_id is None:
                return chain
            task = self.get_task(current_id)
            if task is None:
                return chain
            chain.append(task)
            current_id = task.revisit_of_task_id
        if current_id is not None and not truncate:
            raise LineageTooDeep(
                f"revisit chain from {task_id} exceeded {max_hops} hops"
            )
        return chain

    @_synchronized
    def get_recall_payload(self, task_id: str) -> dict | None:
        """Return a flat dict suitable for the /recall endpoint, or None.

        ``children`` is the list of direct child task ids — the route layer
        promotes them to full payloads when ``tree=true``.

        ``verdict`` is the structured verdict from the latest persisted
        ``task_results`` row (deterministic: ``ORDER BY created_at DESC, id DESC LIMIT 1``
        — result-recency first with a stable id tie-breaker).
        Absent / ``None`` when no result row exists or the latest row has no verdict.
        """
        task = self.get_task(task_id)
        if task is None:
            return None
        created_at = (
            task.created_at.isoformat()
            if hasattr(task.created_at, "isoformat")
            else task.created_at
        )
        completed_at = (
            task.completed_at.isoformat()
            if hasattr(task.completed_at, "isoformat")
            else task.completed_at
        )
        # Latest structured verdict from persisted task_results, if any.
        verdict: str | None = None
        latest = self._conn.execute(
            "SELECT verdict FROM task_results WHERE task_id = ? "
            "ORDER BY created_at DESC, id DESC LIMIT 1",
            (task_id,),
        ).fetchone()
        if latest is not None:
            verdict = latest["verdict"] if "verdict" in latest.keys() else None
        return {
            "task_id": task.id,
            "parent_task_id": task.parent_task_id,
            "revisit_of_task_id": task.revisit_of_task_id,
            "assigned_agent": task.assigned_agent,
            "brief": task.brief,
            "status": task.status.value,
            "created_at": created_at,
            "completed_at": completed_at,
            "output_summary": task.note,
            "output_dir": task.final_output_dir,
            "verdict": verdict,
            "children": self.get_children(task.id),
        }

    @_synchronized
    def list_agent_tasks(self, agent: str, limit: int = 50) -> list[TaskRecord]:
        """Return tasks assigned to an agent, newest-first.

        Orders by the latest available timestamp (completed_at > updated_at >
        created_at) as a lexicographic string compare — our ISO-8601 values
        include microseconds and +00:00 which SQLite's ``datetime()`` parser
        rejects, but they sort correctly as raw strings.
        """
        cursor = self._conn.execute(
            """SELECT * FROM tasks WHERE assigned_agent = ?
               ORDER BY COALESCE(completed_at, updated_at, created_at) DESC
               LIMIT ?""",
            (agent, limit),
        )
        return [
            TaskRecord(
                id=row["id"],
                status=row["status"],
                assigned_agent=row["assigned_agent"],
                team=row["team"],
                brief=row["brief"],
                revision_count=row["revision_count"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
                completed_at=row["completed_at"],
                parent_task_id=row["parent_task_id"],
                revisit_of_task_id=row["revisit_of_task_id"],
                dispatched_from_thread_id=row["dispatched_from_thread_id"],
                block_kind=row["block_kind"],
                blocked_on_job_ids=row["blocked_on_job_ids"],
                note=row["note"],
                orchestration_step_count=row["orchestration_step_count"] or 0,
                final_output_dir=row["final_output_dir"],
                cancelled_at=row["cancelled_at"],
                last_heartbeat=row["last_heartbeat"],
                session_timeout_seconds=row["session_timeout_seconds"],
                task_type=row["task_type"],
                executor_pid=row["executor_pid"],
                current_session_id=row["current_session_id"],
                zombie_flagged_at=row["zombie_flagged_at"],
            )
            for row in cursor.fetchall()
        ]

    @_synchronized
    def update_task(self, task_id: str, **fields: object) -> None:
        allowed = {
            "status", "assigned_agent", "revision_count", "completed_at",
            "block_kind", "blocked_on_job_ids", "note", "orchestration_step_count",
            "final_output_dir", "cancelled_at", "last_heartbeat",
            "executor_pid", "current_session_id", "zombie_flagged_at",
        }
        # NOTE: filter on membership, not on None-ness — block_kind must be
        # resettable to NULL when a task unblocks.
        updates: dict[str, object] = {}
        for k, v in fields.items():
            if k not in allowed:
                continue
            if hasattr(v, "value"):
                updates[k] = v.value
            else:
                updates[k] = v
        if not updates:
            return
        updates["updated_at"] = datetime.now(timezone.utc).isoformat()
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [task_id]
        self._conn.execute(f"UPDATE tasks SET {set_clause} WHERE id = ?", values)
        self._conn.commit()

    @_synchronized
    def update_task_active_chain(self, task_id: str, active_chain: str | None) -> None:
        """Set or clear tasks.active_chain. Pass None to clear (chain finished,
        aborted, or never declared)."""
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "UPDATE tasks SET active_chain = ?, updated_at = ? WHERE id = ?",
            (active_chain, now, task_id),
        )
        self._conn.commit()

    @_synchronized
    def update_task_active_fanout(self, task_id: str, active_fanout: str | None) -> None:
        """Set or clear tasks.active_fanout. Pass None to clear (fan-out join
        claimed or parent terminal)."""
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "UPDATE tasks SET active_fanout = ?, updated_at = ? WHERE id = ?",
            (active_fanout, now, task_id),
        )
        self._conn.commit()

    @staticmethod
    def _retry_object(raw: str) -> dict:
        try:
            value = json.loads(raw)
        except (TypeError, ValueError) as exc:
            raise _RetryEvidenceRefusal("malformed_evidence") from exc
        if not isinstance(value, dict):
            raise _RetryEvidenceRefusal("malformed_evidence")
        return value

    def _retry_audits(self, task_id: str, action: str) -> list[tuple[str, dict]]:
        # These are the actual org-local records, without a CLI pagination cap.
        return [
            (row["agent"], self._retry_object(row["payload"]))
            for row in self._conn.execute(
                "SELECT agent, payload FROM audit_log WHERE task_id=? AND action=?",
                (task_id, action),
            )
        ]

    def _retry_require_audit(
        self, task_id: str, action: str, agent: str, expected: dict, *,
        decision: str | None = None,
    ) -> None:
        observations = self._retry_audits(task_id, action)
        if decision is not None:
            # Earlier manual continues belong to this root's history, but do
            # not describe its eventual supersession. Malformed JSON still
            # fails in _retry_audits, rather than silently losing evidence.
            observations = [(actor, payload) for actor, payload in observations
                            if payload.get("decision") != "continue"]
        if not observations or any(
            actual_agent != agent or any(payload.get(k) != v for k, v in expected.items())
            for actual_agent, payload in observations
        ):
            raise _RetryEvidenceRefusal("malformed_evidence")

    def _retry_manager_edge(self, predecessor, successor, records: list) -> None:
        normalized = {tuple(row) for row in records}
        if len(normalized) != 1:
            raise _RetryEvidenceRefusal("ambiguous_predecessor")
        row = records[0]
        actor = successor["assigned_agent"]
        evidence = self._retry_object(row["attestation_evidence"])
        attestation = evidence.get("attestation")
        fields = (
            "policy_product_intent_unchanged", "no_budget_or_external_commitment",
            "no_permission_or_cross_team_change",
            "no_schema_auth_security_privacy_or_data_access_change",
            "no_unresolved_founder_gate",
        )
        if (
            row["actor_agent"] != actor
            or row["original_root_task_id"] != predecessor["id"]
            or not isinstance(row["actor_session_id"], str) or not row["actor_session_id"]
            or evidence.get("rule_version") != "manager_supersession_attestation.v1"
            or evidence.get("actor_agent") != actor
            or evidence.get("actor_session_id") != row["actor_session_id"]
            or not isinstance(attestation, dict)
            or not isinstance(attestation.get("recovery_reason"), str)
            or not attestation["recovery_reason"].strip()
            or any(attestation.get(field) is not True for field in fields)
        ):
            raise _RetryEvidenceRefusal("malformed_evidence")
        for name in ("predecessor_brief", "successor_brief"):
            if (not isinstance(row[name], str)
                or hashlib.sha256(row[name].encode()).hexdigest() != row[name + "_sha256"]):
                raise _RetryEvidenceRefusal("malformed_evidence")
        payload = {key: row[key] for key in (
            "original_root_task_id", "actor_session_id", "rationale",
            "predecessor_brief_sha256", "successor_brief_sha256",
        )}
        payload["attestation_evidence"] = evidence
        for task, other in ((predecessor, successor), (successor, predecessor)):
            self._retry_require_audit(task["id"], "manager_supersession", actor, {
                **payload, "counterpart_task_id": other["id"],
            })

    def _retry_dispatch_edge(self, predecessor, successor, evidence: dict) -> None:
        thread_id = evidence.get("thread_id")
        actor = successor["assigned_agent"]
        if (
            not isinstance(thread_id, str) or not thread_id
            or successor["dispatched_from_thread_id"] != thread_id
            or successor["revisit_of_task_id"] is not None
            or evidence.get("founder_note") != f"thread {thread_id} dispatch by {actor}"
        ):
            raise _RetryEvidenceRefusal("invocation_binding")
        bindings = self._conn.execute(
            "SELECT invocation_token FROM thread_invocations "
            "WHERE dispatched_task_id=? AND thread_id=? AND agent_name=?",
            (successor["id"], thread_id, actor),
        ).fetchall()
        if len(bindings) != 1:
            raise _RetryEvidenceRefusal("invocation_binding")
        try:
            inv = self.get_invocation_any_status(bindings[0]["invocation_token"])
        except ValueError as exc:
            raise _RetryEvidenceRefusal("invocation_binding") from exc
        if (
            inv is None or inv.dispatched_task_id != successor["id"]
            or inv.thread_id != thread_id or inv.agent_name != actor
            or inv.purpose not in (ThreadInvocationPurpose.REPLY, ThreadInvocationPurpose.BOOTSTRAP)
            or inv.status not in (
                ThreadInvocationStatus.PENDING, ThreadInvocationStatus.CONSUMED,
                ThreadInvocationStatus.DECLINED, ThreadInvocationStatus.TIMEOUT,
                ThreadInvocationStatus.FAILED,
            )
            or self._conn.execute(
                "SELECT 1 FROM thread_messages WHERE thread_id=? AND seq=?",
                (thread_id, inv.triggering_seq),
            ).fetchone() is None
            or self._conn.execute("SELECT 1 FROM threads WHERE id=?", (thread_id,)).fetchone() is None
        ):
            raise _RetryEvidenceRefusal("invocation_binding")
        expected = {"task_id": successor["id"], "dispatcher": actor,
                    "target_agent": actor, "team": successor["team"]}
        # Other dispatches in this thread are unrelated to this edge.
        audits = self._conn.execute(
            "SELECT agent, payload FROM audit_log WHERE task_id=? AND action='thread_dispatch' "
            "AND CASE WHEN json_valid(payload) THEN json_extract(payload,'$.task_id') END=?",
            (thread_id, successor["id"]),
        ).fetchall()
        if not audits or any(
            row["agent"] != actor or any(self._retry_object(row["payload"]).get(k) != v
                                          for k, v in expected.items()) for row in audits
        ):
            raise _RetryEvidenceRefusal("invocation_binding")
        messages = self._conn.execute(
            "SELECT speaker, kind, seq, system_payload_json FROM thread_messages "
            "WHERE thread_id=? AND CASE WHEN json_valid(system_payload_json) "
            "THEN json_extract(system_payload_json,'$.task_id') END=?",
            (thread_id, successor["id"]),
        ).fetchall()
        messages = [row for row in messages if self._retry_object(row["system_payload_json"]).get("kind_tag") == "task_dispatched"]
        if not messages or any(
            row["speaker"] != actor or row["kind"] != "system" or row["seq"] <= inv.triggering_seq
            or any(self._retry_object(row["system_payload_json"]).get(k) != v for k, v in expected.items())
            for row in messages
        ):
            raise _RetryEvidenceRefusal("invocation_binding")

    def _retry_escalation_edge(self, predecessor, successor, evidence: dict) -> None:
        actor = evidence.get("actor")
        prior = evidence.get("prior_block_kind")
        if prior not in ("escalated", "delegated"):
            raise _RetryEvidenceRefusal("malformed_evidence")
        if actor == "thread-dispatch":
            self._retry_dispatch_edge(predecessor, successor, evidence)
        elif actor == "cli":
            if successor["revisit_of_task_id"] != predecessor["id"]:
                raise _RetryEvidenceRefusal("malformed_evidence")
            audits = self._retry_audits(successor["id"], "revisit_of")
            if not audits:
                raise _RetryEvidenceRefusal("malformed_evidence")
            for audit_actor, payload in audits:
                cascade = payload.get("cascade")
                if (
                    audit_actor != "founder" or payload.get("actor") != "cli"
                    or payload.get("predecessor_root") != predecessor["id"]
                    or payload.get("prior_status") != "blocked-" + prior
                    or not isinstance(cascade, list) or not cascade
                    or not all(isinstance(item, str) for item in cascade)
                    or len(set(cascade)) != len(cascade)
                    or cascade[0] != predecessor["id"] or cascade[-1] != payload.get("flagged")
                ):
                    raise _RetryEvidenceRefusal("malformed_evidence")
                for parent_id, child_id in zip(cascade, cascade[1:]):
                    child = self._conn.execute("SELECT parent_task_id FROM tasks WHERE id=?", (child_id,)).fetchone()
                    if child is None or child[0] != parent_id:
                        raise _RetryEvidenceRefusal("malformed_evidence")
            self._retry_require_audit(predecessor["id"], "revisit_spawned", "founder", {"new_root": successor["id"]})
        elif actor in ("founder", successor["assigned_agent"]):
            thread_id = evidence.get("thread_id")
            expected = {"decision": "supersede", "resolution_path": "manual_break_glass"}
            if actor != "founder":
                if (
                    not isinstance(thread_id, str) or not thread_id
                    or successor["dispatched_from_thread_id"] != thread_id
                    or predecessor["dispatched_from_thread_id"] != thread_id
                    or self._conn.execute("SELECT 1 FROM threads WHERE id=?", (thread_id,)).fetchone() is None
                ):
                    raise _RetryEvidenceRefusal("malformed_evidence")
                # Thread manual resolution has no stored token-to-successor join.
                # The actual producer's resolution and supersession rows are authority.
                expected.update(resolution_path="thread_manual_supersede", thread_id=thread_id)
            if prior != "escalated" or successor["revisit_of_task_id"] is not None:
                raise _RetryEvidenceRefusal("malformed_evidence")
            self._retry_require_audit(
                predecessor["id"], "escalation_resolved", actor, expected, decision="supersede",
            )
        else:
            raise _RetryEvidenceRefusal("malformed_evidence")

    @_synchronized
    def verify_retry_link(self, parent_id: str, target_agent: str, failed_id: str | None) -> VerifiedRetry | InvalidLineage:
        """Verify an explicit retry through recorded supersessions in this org DB.

        Inclusive limit: twenty root task records, at most nineteen edges.
        This read-only check is advisory until repeated inside the spawn transaction.
        Brief text, task numbering and arbitrary revisit links confer no authority.
        """
        try:
            local_failed = {row[0] for row in self._conn.execute(
                "SELECT id FROM tasks WHERE parent_task_id=? AND assigned_agent=? AND status='failed'",
                (parent_id, target_agent),
            )}
            if failed_id is None:
                if local_failed:
                    raise _RetryEvidenceRefusal("retry_link_required")
                return VerifiedRetry((parent_id,))
            failed = self._conn.execute("SELECT * FROM tasks WHERE id=?", (failed_id,)).fetchone()
            if failed is None:
                raise _RetryEvidenceRefusal("retry_not_found")
            if failed["status"] != "failed":
                raise _RetryEvidenceRefusal("retry_not_failed")
            if failed["assigned_agent"] != target_agent:
                raise _RetryEvidenceRefusal("retry_agent_mismatch")
            if failed["parent_task_id"] == parent_id:
                return VerifiedRetry((parent_id,))
            # A remote historical link cannot retire an unresolved local retry.
            retired = {row[0] for row in self._conn.execute(
                "SELECT revisit_of_task_id FROM tasks WHERE parent_task_id=? AND revisit_of_task_id IS NOT NULL",
                (parent_id,),
            )}
            if local_failed - retired:
                raise _RetryEvidenceRefusal("retry_link_required")
            current = self._conn.execute("SELECT * FROM tasks WHERE id=?", (parent_id,)).fetchone()
            if current is None:
                raise _RetryEvidenceRefusal("no_verified_supersession")
            team, manager = current["team"], current["assigned_agent"]
            path: list[str] = []
            while True:
                if current["id"] in path:
                    raise _RetryEvidenceRefusal("lineage_cycle")
                if len(path) == 20:
                    raise _RetryEvidenceRefusal("lineage_record_limit_20")
                if current["parent_task_id"] is not None or current["task_type"] != "task":
                    raise _RetryEvidenceRefusal("no_verified_supersession")
                if current["team"] != team:
                    raise _RetryEvidenceRefusal("team_mismatch")
                if current["assigned_agent"] != manager:
                    raise _RetryEvidenceRefusal("manager_mismatch")
                path.append(current["id"])
                if current["id"] == failed["parent_task_id"]:
                    return VerifiedRetry(tuple(path))
                if len(path) == 20:
                    raise _RetryEvidenceRefusal("lineage_record_limit_20")
                manager_rows = self._conn.execute(
                    "SELECT * FROM manager_supersessions WHERE successor_task_id=?", (current["id"],),
                ).fetchall()
                escalation_rows = self._conn.execute(
                    "SELECT task_id,agent,payload FROM audit_log WHERE action='escalation_superseded' "
                    "AND CASE WHEN json_valid(payload) THEN json_extract(payload,'$.successor_root') END=?",
                    (current["id"],),
                ).fetchall()
                predecessors = {r["predecessor_task_id"] for r in manager_rows} | {r["task_id"] for r in escalation_rows}
                if not predecessors:
                    raise _RetryEvidenceRefusal("no_verified_supersession")
                if len(predecessors) != 1:
                    raise _RetryEvidenceRefusal("ambiguous_predecessor")
                predecessor_id = predecessors.pop()
                if predecessor_id in path:
                    raise _RetryEvidenceRefusal("lineage_cycle")
                predecessor = self._conn.execute("SELECT * FROM tasks WHERE id=?", (predecessor_id,)).fetchone()
                if predecessor is None or predecessor["status"] != "superseded":
                    raise _RetryEvidenceRefusal("predecessor_not_superseded")
                outgoing = {r[0] for r in self._conn.execute(
                    "SELECT successor_task_id FROM manager_supersessions WHERE predecessor_task_id=?", (predecessor_id,),
                )}
                outgoing_audits = self._retry_audits(predecessor_id, "escalation_superseded")
                for audit_actor, payload in outgoing_audits:
                    successor_id = payload.get("successor_root")
                    if audit_actor != "founder" or not isinstance(successor_id, str) or not successor_id:
                        raise _RetryEvidenceRefusal("malformed_evidence")
                    outgoing.add(successor_id)
                if len(outgoing) > 1:
                    raise _RetryEvidenceRefusal("competing_successors")
                if manager_rows and escalation_rows:
                    raise _RetryEvidenceRefusal("ambiguous_predecessor")
                if manager_rows:
                    self._retry_manager_edge(predecessor, current, manager_rows)
                else:
                    normalized = {json.dumps(payload, sort_keys=True) for _, payload in outgoing_audits}
                    if len(normalized) != 1:
                        raise _RetryEvidenceRefusal("ambiguous_predecessor")
                    self._retry_escalation_edge(predecessor, current, outgoing_audits[0][1])
                current = predecessor
        except _RetryEvidenceRefusal as exc:
            return InvalidLineage(str(exc))

    def _retry_claim_matches(self, claim: RetryClaim, *, pending: bool = False) -> bool:
        if claim.status != TaskStatus.IN_PROGRESS or claim.block_kind is not None or claim.cancelled_at is not None:
            return False
        task = self.get_task(claim.task_id)
        if (task is None or task.status != (TaskStatus.PENDING if pending else TaskStatus.IN_PROGRESS)
                or task.block_kind is not None or task.cancelled_at is not None
                or not claim.current_session_id or not claim.assigned_agent
                or replace(RetryClaim.from_task(task, result_row_id=claim.result_row_id),
                           status=TaskStatus.IN_PROGRESS if pending else task.status) != claim):
            return False
        if claim.result_row_id is not None:
            row = self._conn.execute(
                "SELECT task_id, agent, session_id FROM task_results WHERE id = ?",
                (claim.result_row_id,),
            ).fetchone()
            if row is None or tuple(row) != (claim.task_id, claim.assigned_agent, claim.current_session_id):
                return False
        return True

    def _retry_spawn_check(self, parent_id: str, children: list, claim: RetryClaim,
                           revision_delta: int = 0, revision_cap: int = 0,
                           *, ordinary: bool = False) -> InvalidLineage | LostClaim | None:
        if parent_id != claim.task_id or not self._retry_claim_matches(claim):
            return LostClaim()
        for child in children:
            if child.parent_task_id != parent_id:
                return InvalidLineage("child_parent_mismatch")
            outcome = self.verify_retry_link(parent_id, child.assigned_agent, child.revisit_of_task_id)
            if isinstance(outcome, InvalidLineage):
                return outcome
        delta = 0
        if ordinary:
            row = self._conn.execute(
                "SELECT assigned_agent FROM tasks WHERE parent_task_id = ? "
                "AND status IN ('completed', 'failed') ORDER BY created_at, id LIMIT 1",
                (parent_id,),
            ).fetchone()
            delta = int(row is not None and row[0] == children[0].assigned_agent
                        and row[0] != claim.assigned_agent)
        if delta != revision_delta or (delta and revision_cap > 0 and claim.revision >= revision_cap):
            return LostClaim()
        return None

    @_synchronized
    def try_retry_feedback(self, expected_claim: RetryClaim, feedback: str) -> PendingRetry | LostClaim:
        """A: commit existing feedback result/audit and PENDING as one owned write."""
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            if not self._retry_claim_matches(expected_claim):
                self._conn.rollback()
                return LostClaim()
            self._insert_task_result(
                task_id=expected_claim.task_id, agent=expected_claim.assigned_agent,
                session_id="", status="completed", confidence_score=0,
                output_summary=feedback, risks_flagged=[],
            )
            result_id = self._conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            self.insert_audit_log_uncommitted(
                task_id=expected_claim.task_id, agent="orchestrator", action="orchestration_step",
                payload={"step_number": expected_claim.step,
                         "decision": {"action": "feedback", "reason": feedback}},
            )
            self._conn.execute(
                "UPDATE tasks SET status = 'pending', block_kind = NULL, updated_at = ? WHERE id = ?",
                (datetime.now(timezone.utc).isoformat(), expected_claim.task_id),
            )
            self._conn.commit()
            return PendingRetry(expected_claim, result_id, feedback)
        except BaseException:
            self._conn.rollback()
            raise

    @_synchronized
    def admit_retry_feedback(self, pending: PendingRetry, enqueue) -> str | LostClaim:
        """B: no-write admission; hold the writer reservation through synchronous enqueue.

        Queue insertion is not a claim and is not crash-atomic with SQLite.
        Startup may enqueue PENDING again after either side of this boundary.
        """
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            if not self._retry_claim_matches(pending.claim, pending=True):
                return LostClaim()
            row = self._conn.execute(
                "SELECT task_id, agent, session_id, status, output_summary FROM task_results WHERE id = ?",
                (pending.result_id,),
            ).fetchone()
            if row is None or tuple(row) != (pending.claim.task_id, pending.claim.assigned_agent,
                                             "", "completed", pending.feedback):
                return LostClaim()
            if enqueue is None:
                return "not_configured"
            enqueue()
            return "enqueued"
        finally:
            self._conn.rollback()

    @_synchronized
    def try_delegate_many(
        self, parent_id: str, children: list, *, parent_note: str,
        active_fanout_json: str | None = None,
        children_attachments: list[list[dict] | None] | None = None,
        carrier_chains: list[dict] | None = None,
        uploaded_by: str = "orchestrator",
        expected_claim: RetryClaim,
    ) -> SpawnOutcome:
        """Atomic CAS: insert N child tasks + transition parent to
        IN_PROGRESS(DELEGATED) under a single explicit SQL transaction.

        All child inserts, parent status/block_kind/active_fanout update,
        note write, attachment links/audit rows, and pipeline carrier chain
        materialization (active_chain + first leg insert) happen in one
        transaction. On any exception the transaction rolls back — no partial
        children, no orphan rows, no orphan attachment links, no orphan
        carrier state.

        Same cancel-race semantics as try_delegate (single-child): if the
        parent is cancelled or already terminal at the time of the guarded
        SELECT, no children are inserted and the parent is not overwritten.

        When ``children_attachments`` is provided, it must be a list of the
        same length as ``children``; each element is either a list of
        attachment param dicts for that child, or None/empty.

        When ``carrier_chains`` is provided, it must be a list of dicts with
        keys ``child_index`` (int), ``active_chain_json`` (str), and
        ``first_leg`` (dict with keys: id, team, brief, assigned_agent,
        status, session_timeout_seconds, task_type). The first leg is
        inserted as a child of the carrier within the same transaction.

        Committed carries direct child/carrier IDs. InvalidLineage and
        LostClaim carry no writes; the original claim is checked before
        lineage. BEGIN IMMEDIATE precedes both authoritative rereads.

        Children must already have their IDs allocated (caller calls
        next_task_id() N times before invoking this method). Carrier first
        leg IDs must also be pre-allocated.
        """
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            refusal = self._retry_spawn_check(
                parent_id, children, expected_claim,

            )
            if refusal is not None:
                self._conn.rollback()
                return refusal
            now = now_ts = datetime.now(timezone.utc).isoformat()
            for i, child in enumerate(children):
                self._conn.execute(
                    """INSERT INTO tasks (id, status, assigned_agent, team, brief,
                       revision_count, created_at, updated_at, completed_at, parent_task_id,
                       revisit_of_task_id, dispatched_from_thread_id,
                       block_kind, note,
                       orchestration_step_count, session_timeout_seconds, task_type, active_fanout)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        child.id,
                        child.status.value,
                        child.assigned_agent,
                        child.team,
                        child.brief,
                        child.revision_count,
                        child.created_at.isoformat(),
                        child.updated_at.isoformat(),
                        child.completed_at.isoformat() if child.completed_at else None,
                        child.parent_task_id,
                        child.revisit_of_task_id,
                        child.dispatched_from_thread_id,
                        child.block_kind.value if child.block_kind else None,
                        child.note,
                        child.orchestration_step_count,
                        child.session_timeout_seconds,
                        child.task_type,
                        child.active_fanout,
                    ),
                )
                # Insert attachment links for this child if present.
                child_atts = None
                if children_attachments and i < len(children_attachments):
                    child_atts = children_attachments[i]
                if child_atts:
                    self._insert_task_attachments_txn(
                        child.id, child_atts, uploaded_by,
                    )
            # Materialize pipeline carrier chains within the same transaction.
            # active_chain on the carrier + first leg insert as child of carrier
            # are atomic with the fanout spawn — no partial carrier state.
            if carrier_chains:
                for cc in carrier_chains:
                    ci = cc["child_index"]
                    cid = children[ci].id
                    self._conn.execute(
                        "UPDATE tasks SET active_chain = ? WHERE id = ?",
                        (cc["active_chain_json"], cid),
                    )
                    fl = cc["first_leg"]
                    self._conn.execute(
                        """INSERT INTO tasks (id, status, assigned_agent, team, brief,
                           revision_count, created_at, updated_at, completed_at,
                           parent_task_id, revisit_of_task_id, dispatched_from_thread_id,
                           block_kind, note,
                           orchestration_step_count, session_timeout_seconds, task_type,
                           active_fanout)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            fl["id"],
                            fl["status"].value if isinstance(fl["status"], TaskStatus) else fl["status"],
                            fl["assigned_agent"],
                            fl["team"],
                            fl["brief"],
                            fl.get("revision_count", 0),
                            fl.get("created_at", now),
                            fl.get("updated_at", now),
                            None,
                            cid,
                            None,
                            None,
                            None,
                            None,
                            fl.get("orchestration_step_count", 0),
                            fl.get("session_timeout_seconds", 0),
                            fl.get("task_type", "subtask"),
                            None,
                        ),
                    )
            self._conn.execute(
                """UPDATE tasks
                   SET status = ?, block_kind = ?, note = ?, active_fanout = ?, updated_at = ?
                   WHERE id = ?""",
                (TaskStatus.IN_PROGRESS.value, BlockKind.DELEGATED.value, parent_note,
                 active_fanout_json, now, parent_id),
            )
            self._conn.commit()
            return Committed(tuple(child.id for child in children))
        except BaseException:
            self._conn.rollback()
            raise

    @_synchronized
    def try_delegate(
        self, parent_id: str, child: TaskRecord, *, parent_note: str,
        attachments: list[dict] | None = None,
        active_chain_json: str | None = None,
        uploaded_by: str = "orchestrator",
        expected_claim: RetryClaim,
        revision_delta: int = 0,
        revision_cap: int = 0,
    ) -> SpawnOutcome:
        """Commit the child, attachments, chain and ordinary revision atomically.

        BEGIN IMMEDIATE under RLock precedes original claim, callback binding,
        lineage and worker-of-record rereads. LostClaim takes precedence over
        InvalidLineage. Neither refusal writes; Committed alone permits enqueue.
        All actual write/serialization errors roll back and propagate, preserving
        prior callback results, decisions and parent state.
        """
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            refusal = self._retry_spawn_check(
                parent_id, [child], expected_claim,
                revision_delta, revision_cap, ordinary=True,
            )
            if refusal is not None:
                self._conn.rollback()
                return refusal
            now = now_ts = datetime.now(timezone.utc).isoformat()
            self._conn.execute(
                """INSERT INTO tasks (id, status, assigned_agent, team, brief,
                   revision_count, created_at, updated_at, completed_at, parent_task_id,
                   revisit_of_task_id, dispatched_from_thread_id,
                   block_kind, note,
                   orchestration_step_count, session_timeout_seconds, task_type, active_fanout,
                   current_session_id, zombie_flagged_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    child.id,
                    child.status.value,
                    child.assigned_agent,
                    child.team,
                    child.brief,
                    child.revision_count,
                    child.created_at.isoformat(),
                    child.updated_at.isoformat(),
                    child.completed_at.isoformat() if child.completed_at else None,
                    child.parent_task_id,
                    child.revisit_of_task_id,
                    child.dispatched_from_thread_id,
                    child.block_kind.value if child.block_kind else None,
                    child.note,
                    child.orchestration_step_count,
                    child.session_timeout_seconds,
                    child.task_type,
                    child.active_fanout,
                    child.current_session_id,
                    child.zombie_flagged_at.isoformat() if child.zombie_flagged_at else None,
                ),
            )
            if attachments:
                self._insert_task_attachments_txn(
                    child.id, attachments, uploaded_by,
                )
            # Write active_chain within the same transaction so a crash or
            # write failure rolls back child + parent + chain atomically.
            if active_chain_json is not None:
                self._conn.execute(
                    "UPDATE tasks SET active_chain = ? WHERE id = ?",
                    (active_chain_json, parent_id),
                )
            now = datetime.now(timezone.utc).isoformat()
            self._conn.execute(
                """UPDATE tasks
                   SET status = ?, block_kind = ?, note = ?, updated_at = ?, revision_count = revision_count + ?
                   WHERE id = ?""",
                (TaskStatus.IN_PROGRESS.value, BlockKind.DELEGATED.value, parent_note,
                 now, revision_delta, parent_id),
            )
            self._conn.commit()
            return Committed((child.id,))
        except BaseException:
            self._conn.rollback()
            raise

    @_synchronized
    def try_claim_for_step(
        self,
        task_id: str,
        expected_status: TaskStatus,
        expected_block_kind: BlockKind | None,
        new_count: int,
    ) -> bool:
        """Atomic compare-and-swap for the run_step entry transition.

        Transitions the row to status=in_progress, clears block_kind/note, and
        sets orchestration_step_count=new_count, but ONLY if the row currently
        matches (expected_status, expected_block_kind). Returns True iff the
        transition occurred.

        Why this exists: two workers can pop the same task_id (e.g. a multi-
        child fan-in double-enqueued the parent). Without this CAS, both pass
        the check-then-update at run_step steps 1→3 and both spawn an agent
        subprocess. The conditional WHERE ensures only the first writer wins.

        THR-229 C3d3b mandatory fallback fence: this ordinary claim is NOT
        generation admission.  In the SAME ``BEGIN IMMEDIATE`` that would claim
        the task, a root whose durable ``authority_policy_v2_root_dispatch``
        pointer is ``pending`` (a live v2 continuation generation) refuses --
        the task cannot win on status/block_kind alone.  An untagged or stale
        queue item therefore can never adopt today's generation: the tagged
        ``try_claim_v2_continuation_generation`` path is the only admission and
        it authenticates the exact metadata token G.  Roots with no pending
        pointer keep their unchanged ordinary behavior.
        """
        now = datetime.now(timezone.utc).isoformat()
        began = False
        if not self._conn.in_transaction:
            self._conn.execute("BEGIN IMMEDIATE")
            began = True
        try:
            pending_generation = self._conn.execute(
                """SELECT 1 FROM authority_policy_v2_root_dispatch
                    WHERE root_task_id=? AND state='pending'""",
                (task_id,),
            ).fetchone()
            if pending_generation is not None:
                if began:
                    self._conn.rollback()
                return False
            if expected_block_kind is None:
                cursor = self._conn.execute(
                    """UPDATE tasks
                       SET status = ?, block_kind = NULL, note = NULL,
                           orchestration_step_count = ?, updated_at = ?
                       WHERE id = ? AND status = ? AND block_kind IS NULL""",
                    (TaskStatus.IN_PROGRESS.value, new_count, now,
                     task_id, expected_status.value),
                )
            else:
                cursor = self._conn.execute(
                    """UPDATE tasks
                       SET status = ?, block_kind = NULL, note = NULL,
                           orchestration_step_count = ?, updated_at = ?
                       WHERE id = ? AND status = ? AND block_kind = ?""",
                    (TaskStatus.IN_PROGRESS.value, new_count, now,
                     task_id, expected_status.value, expected_block_kind.value),
                )
            self._conn.commit()
            return cursor.rowcount == 1
        except Exception:
            if began:
                self._conn.rollback()
            raise

    @_synchronized
    def try_fail_over_budget(
        self,
        task_id: str,
        *,
        expected_status: TaskStatus,
        expected_block_kind: BlockKind | None,
        note: str,
    ) -> bool:
        """Atomic CAS for the run_step max-steps budget guard — non-root variant.

        Mirror of ``try_escalate_over_budget`` (the root variant), but transitions
        the row to FAILED (block_kind NULL, completed_at set — FAILED is terminal,
        unlike the ESCALATED template) ONLY if it still matches
        (expected_status, expected_block_kind). Returns True iff it transitioned.

        Per THR-033 Change A a NON-root task that hits the step budget must not
        escalate directly to the founder — it fails and hands back to its parent
        (bounded failure-recovery carries it up). The CAS is required for the same
        reason as ``try_escalate_over_budget``: the budget guard runs BEFORE
        try_claim_for_step, so it has no upstream CAS. Two duplicate queue
        deliveries can both read the same stale at-cap eligible row; the
        conditional WHERE makes only the first writer win, so the parent enqueue +
        thread followup fire exactly once. A /cancel landing in the window moves
        the row out of the expected pre-state and the CAS rejects it for free.
        """
        now = datetime.now(timezone.utc).isoformat()
        if expected_block_kind is None:
            cursor = self._conn.execute(
                """UPDATE tasks
                   SET status = ?, block_kind = NULL, note = ?,
                       completed_at = ?, updated_at = ?
                   WHERE id = ? AND status = ? AND block_kind IS NULL""",
                (TaskStatus.FAILED.value, note, now, now,
                 task_id, expected_status.value),
            )
        else:
            cursor = self._conn.execute(
                """UPDATE tasks
                   SET status = ?, block_kind = NULL, note = ?,
                       completed_at = ?, updated_at = ?
                   WHERE id = ? AND status = ? AND block_kind = ?""",
                (TaskStatus.FAILED.value, note, now, now,
                 task_id, expected_status.value, expected_block_kind.value),
            )
        self._conn.commit()
        return cursor.rowcount == 1

    def _has_live_manager_supersession_family_work_uncommitted(
        self, task_id: str,
    ) -> bool:
        """Return whether a supersession family retains live task/job work."""
        return self._conn.execute(
            """WITH RECURSIVE family(id) AS (
                   SELECT ?
                   UNION ALL
                   SELECT t.id FROM tasks t JOIN family f ON t.parent_task_id = f.id
               )
               SELECT 1 FROM tasks
                WHERE id IN family AND id != ?
                  AND status NOT IN ('completed', 'failed', 'cancelled', 'superseded')
               UNION ALL
               SELECT 1 FROM jobs
                WHERE task_id IN family AND status IN ('pending', 'running')
               LIMIT 1""",
            (task_id, task_id),
        ).fetchone() is not None

    @_synchronized
    def try_fail_nonroot_manager_supersede(
        self,
        task_id: str,
        *,
        actor_agent: str,
        actor_session_id: str,
        expected_team: str,
        note: str,
    ) -> bool:
        """Fail one still-current non-root supersede claim atomically.

        Non-root identity is an explicit predicate, not an interpretation of
        ``try_manager_supersede`` returning ``None``.  A cancelled, replaced,
        blocked, terminal, root, or otherwise non-current row is untouched.
        The caller owns the ordinary FAILED terminal tail and parent handoff
        only after this transition succeeds.
        """
        now = datetime.now(timezone.utc).isoformat()
        cursor = self._conn.execute(
            """UPDATE tasks
                  SET status = ?, block_kind = NULL, note = ?, completed_at = ?,
                      updated_at = ?, active_chain = NULL, active_fanout = NULL
                WHERE id = ? AND parent_task_id IS NOT NULL
                  AND status = ? AND block_kind IS NULL
                  AND cancelled_at IS NULL AND task_type = 'task'
                  AND assigned_agent = ? AND team = ?
                  AND current_session_id = ?""",
            (
                TaskStatus.FAILED.value,
                note,
                now,
                now,
                task_id,
                TaskStatus.IN_PROGRESS.value,
                actor_agent,
                expected_team,
                actor_session_id,
            ),
        )
        self._conn.commit()
        return cursor.rowcount == 1

    @_synchronized
    def try_manager_supersede(
        self,
        task_id: str,
        *,
        actor_agent: str,
        actor_session_id: str,
        expected_team: str,
        successor_brief: str,
        rationale: str,
        attestation: dict[str, object],
    ) -> str | None:
        """Atomically replace one eligible claimed root with a pending successor.

        This intentionally has no generic target/actor override: callers supply
        only the server-derived current claim and the two decision fields.  A
        false return has no write side effects; exceptions roll the entire
        operation back.
        """
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            row = self._conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
            if row is None or (
                row["status"] != TaskStatus.IN_PROGRESS.value
                or row["block_kind"] is not None
                or row["cancelled_at"] is not None
                or row["task_type"] != "task"
                or row["parent_task_id"] is not None
                or row["assigned_agent"] != actor_agent
                or row["team"] != expected_team
                or row["current_session_id"] != actor_session_id
                or row["active_chain"] is not None
                or row["active_fanout"] is not None
                or row["blocked_on_job_ids"] is not None
                or row["dispatched_from_thread_id"] not in (None, "")
            ):
                self._conn.rollback()
                return None
            if self._has_live_manager_supersession_family_work_uncommitted(task_id):
                self._conn.rollback()
                return None
            predecessor = row["id"]
            prior = self._conn.execute(
                "SELECT original_root_task_id FROM manager_supersessions WHERE successor_task_id = ?",
                (predecessor,),
            ).fetchone()
            original_root = prior["original_root_task_id"] if prior else predecessor
            if self._conn.execute(
                "SELECT 1 FROM manager_supersessions WHERE original_root_task_id = ? LIMIT 1",
                (original_root,),
            ).fetchone() is not None:
                self._conn.rollback()
                return None
            successor_id = self.next_task_id()
            now = datetime.now(timezone.utc).isoformat()
            predecessor_brief = row["brief"]
            predecessor_hash = hashlib.sha256(predecessor_brief.encode()).hexdigest()
            successor_hash = hashlib.sha256(successor_brief.encode()).hexdigest()
            attestation_evidence = {
                "rule_version": "manager_supersession_attestation.v1",
                "actor_agent": actor_agent,
                "actor_session_id": actor_session_id,
                "attestation": attestation,
            }
            self._conn.execute(
                """INSERT INTO tasks (id, status, assigned_agent, team, brief, task_type,
                       revision_count, created_at, updated_at, parent_task_id,
                       orchestration_step_count, session_timeout_seconds)
                   VALUES (?, 'pending', ?, ?, ?, 'task', 0, ?, ?, NULL, 0, ?)""",
                (successor_id, actor_agent, row["team"], successor_brief, now, now,
                 row["session_timeout_seconds"]),
            )
            self._conn.execute(
                """INSERT INTO manager_supersessions
                   (predecessor_task_id, successor_task_id, original_root_task_id,
                    actor_agent, actor_session_id, rationale, attestation_evidence, predecessor_brief,
                    successor_brief, predecessor_brief_sha256, successor_brief_sha256, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (predecessor, successor_id, original_root, actor_agent, actor_session_id,
                 rationale, json.dumps(attestation_evidence, sort_keys=True), predecessor_brief, successor_brief, predecessor_hash,
                 successor_hash, now),
            )
            self._conn.execute(
                """UPDATE tasks SET status = 'superseded', block_kind = NULL,
                       blocked_on_job_ids = NULL, active_chain = NULL, active_fanout = NULL,
                       note = ?, completed_at = ?, updated_at = ?
                   WHERE id = ? AND status = 'in_progress' AND block_kind IS NULL
                     AND current_session_id = ?""",
                (f"manager-superseded by {successor_id}", now, now, predecessor,
                 actor_session_id),
            )
            if self._conn.execute("SELECT changes()").fetchone()[0] != 1:
                raise RuntimeError("supersession claim became stale")
            payload = {
                "original_root_task_id": original_root,
                "actor_session_id": actor_session_id,
                "rationale": rationale,
                "attestation_evidence": attestation_evidence,
                "predecessor_brief_sha256": predecessor_hash,
                "successor_brief_sha256": successor_hash,
            }
            for audit_task_id, counterpart_task_id in (
                (predecessor, successor_id), (successor_id, predecessor),
            ):
                audit_payload = {**payload, "counterpart_task_id": counterpart_task_id}
                self._conn.execute(
                    "INSERT INTO audit_log (task_id, agent, action, payload, timestamp) VALUES (?, ?, ?, ?, ?)",
                    (audit_task_id, actor_agent, "manager_supersession", json.dumps(audit_payload), now),
                )
            self._conn.commit()
            return successor_id
        except Exception:
            self._conn.rollback()
            raise

    @_synchronized
    def try_reject_thread_origin_manager_supersede(
        self,
        task_id: str,
        *,
        actor_agent: str,
        actor_session_id: str,
        expected_team: str,
        reason: str,
        reason_code: str,
    ) -> bool:
        """Atomically escalate one currently claimed, thread-origin manager root.

        This is the ineligible counterpart to ``try_manager_supersede``.  Its
        complete claim predicate is evaluated under the same transaction as
        the state and audit writes, so a competing continuation, block, or
        cancellation wins without any rejection side effects.
        """
        now = datetime.now(timezone.utc).isoformat()
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            if self._has_live_manager_supersession_family_work_uncommitted(task_id):
                self._conn.rollback()
                return False
            cursor = self._conn.execute(
                """UPDATE tasks
                   SET status = ?, block_kind = NULL, note = ?, updated_at = ?
                   WHERE id = ?
                     AND status = 'in_progress'
                     AND block_kind IS NULL
                     AND cancelled_at IS NULL
                     AND task_type = 'task'
                     AND parent_task_id IS NULL
                     AND assigned_agent = ?
                     AND team = ?
                     AND current_session_id = ?
                     AND active_chain IS NULL
                     AND active_fanout IS NULL
                     AND blocked_on_job_ids IS NULL
                     AND dispatched_from_thread_id IS NOT NULL
                     AND dispatched_from_thread_id != ''""",
                (
                    TaskStatus.ESCALATED.value, reason, now, task_id,
                    actor_agent, expected_team, actor_session_id,
                ),
            )
            if cursor.rowcount != 1:
                self._conn.rollback()
                return False
            escalation_audit_id = self.insert_audit_log_uncommitted(
                task_id=task_id,
                agent=actor_agent,
                action="escalation",
                payload={"reason": reason},
            )
            self.insert_audit_log_uncommitted(
                task_id=task_id,
                agent=actor_agent,
                action="authority_hook",
                payload={
                    "outcome": "not_applicable",
                    "reason_code": reason_code,
                    "reason": "runtime-raised escalation is not an authority decision",
                    "causal_escalation_audit_id": escalation_audit_id,
                },
            )
            self._conn.commit()
            return True
        except Exception:
            self._conn.rollback()
            raise

    @_synchronized
    def try_advance_chain(
        self,
        parent_id: str,
        active_chain_json: str,
        next_child: "TaskRecord",
        *,
        attachments: list[dict] | None = None,
        uploaded_by: str = "orchestrator",
    ) -> bool:
        """Atomically update parent active_chain + insert next child +
        attachment links/audit in a single explicit transaction.

        Replaces the prior two-step pattern (update_task_active_chain +
        insert_task_with_attachments) with a single transaction so a crash
        or child/link/audit write failure rolls back the chain advance.

        Returns True on success (parent chain advanced, child exists,
        attachments linked). Returns False and rolls back on any failure.
        """
        now_ts = datetime.now(timezone.utc).isoformat()
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            self._conn.execute(
                "UPDATE tasks SET active_chain = ? WHERE id = ?",
                (active_chain_json, parent_id),
            )
            self._conn.execute(
                """INSERT INTO tasks (id, status, assigned_agent, team, brief,
                   revision_count, created_at, updated_at, completed_at, parent_task_id,
                   revisit_of_task_id, dispatched_from_thread_id,
                   block_kind, note,
                   orchestration_step_count, session_timeout_seconds, task_type, active_fanout,
                   current_session_id, zombie_flagged_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    next_child.id,
                    next_child.status.value,
                    next_child.assigned_agent,
                    next_child.team,
                    next_child.brief,
                    next_child.revision_count,
                    next_child.created_at.isoformat(),
                    next_child.updated_at.isoformat(),
                    next_child.completed_at.isoformat() if next_child.completed_at else None,
                    next_child.parent_task_id,
                    next_child.revisit_of_task_id,
                    next_child.dispatched_from_thread_id,
                    next_child.block_kind.value if next_child.block_kind else None,
                    next_child.note,
                    next_child.orchestration_step_count,
                    next_child.session_timeout_seconds,
                    next_child.task_type,
                    next_child.active_fanout,
                    next_child.current_session_id,
                    next_child.zombie_flagged_at.isoformat() if next_child.zombie_flagged_at else None,
                ),
            )
            if attachments:
                self._insert_task_attachments_txn(
                    next_child.id, attachments, uploaded_by,
                )
            self._conn.commit()
            return True
        except Exception:
            self._conn.rollback()
            return False

    @_synchronized
    def increment_revision_count(self, task_id: str) -> None:
        self._conn.execute(
            "UPDATE tasks SET revision_count = revision_count + 1, updated_at = ? WHERE id = ?",
            (datetime.now(timezone.utc).isoformat(), task_id),
        )
        self._conn.commit()

    @_synchronized
    def next_task_id(self) -> str:
        # MAX(numeric_suffix) over TASK-NNN-shaped rows. Robust to gaps and
        # foreign-shape rows that a COUNT(*)-based allocator would mis-count
        # and then collide with on the next insert.
        cursor = self._conn.execute(
            "SELECT MAX(CAST(SUBSTR(id, 6) AS INTEGER)) AS m "
            "FROM tasks WHERE id GLOB 'TASK-[0-9]*'"
        )
        n = (cursor.fetchone()["m"] or 0) + 1
        return f"TASK-{n:03d}"

    @_synchronized
    def get_nonterminal_task_ids(self) -> list[str]:
        # Path B: blocked dropped (no live row is `blocked` after the boot
        # migration); escalated added so the restart sweep visits escalated
        # rows to leave them alone (§B Branch 5). cancelled is terminal →
        # excluded.
        nonterminal = (
            TaskStatus.PENDING.value,
            TaskStatus.IN_PROGRESS.value,
            TaskStatus.ESCALATED.value,
        )
        cursor = self._conn.execute(
            f"SELECT id FROM tasks WHERE status IN ({','.join('?' * len(nonterminal))})",
            nonterminal,
        )
        return [row["id"] for row in cursor.fetchall()]

    @_synchronized
    def list_blocked_with_kind(self, kind) -> list[str]:
        """Return IDs of parked tasks with the given block_kind.

        Queries by in_progress + block_kind — the stored Path-B representation.
        """
        kind_value = kind.value if hasattr(kind, "value") else kind
        cursor = self._conn.execute(
            "SELECT id FROM tasks "
            "WHERE status = 'in_progress' AND block_kind = ?",
            (kind_value,),
        )
        return [row["id"] for row in cursor.fetchall()]

    @_synchronized
    def list_tasks_blocked_on_jobs(self) -> list[str]:
        """Return ids of tasks currently parked waiting on jobs (BLOCKED_ON_JOB).

        Used by startup recovery (spec §5.7) to re-evaluate the predicate after
        `recover_orphaned_running_jobs` force-fails any leftovers.
        """
        rows = self._conn.execute(
            "SELECT id FROM tasks "
            "WHERE status = ? AND block_kind = ?",
            (TaskStatus.IN_PROGRESS.value, BlockKind.BLOCKED_ON_JOB.value),
        ).fetchall()
        return [row["id"] for row in rows]
