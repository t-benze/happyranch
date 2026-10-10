from __future__ import annotations

from runtime.infrastructure.db._shared import _synchronized
from runtime.models import TaskStatus


class JobsMixin:
    # --- Jobs ---

    @_synchronized
    def next_job_id(self) -> str:
        """Return the next available JOB-NNN id.

        Callers must hold DaemonState.db_lock across the next_job_id()
        + insert_job() pair to avoid duplicate IDs under concurrent
        requests (same requirement as next_task_id / next_thread_id).
        """
        cursor = self._conn.execute(
            "SELECT MAX(CAST(SUBSTR(id, 5) AS INTEGER)) AS m "
            "FROM jobs WHERE id GLOB 'JOB-[0-9]*'"
        )
        n = (cursor.fetchone()["m"] or 0) + 1
        return f"JOB-{n:03d}"

    @_synchronized
    def insert_job(self, r: "JobRecord") -> None:
        self._insert_job_uncommitted(r)
        self._conn.commit()

    def _insert_job_uncommitted(self, r: "JobRecord") -> None:
        self._conn.execute(
            """INSERT INTO jobs (
                id, task_id, agent_name, title, rationale, script_text,
                interpreter, cwd_hint, status, exit_code,
                stdout_head, stderr_head, stdout_path, stderr_path,
                duration_ms, started_at, finished_at,
                reviewed_at, reviewed_by, reject_reason,
                cwd_resolved, max_runtime_seconds, max_output_bytes,
                review_required, persistent, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                r.id, r.task_id, r.agent_name, r.title, r.rationale, r.script_text,
                r.interpreter.value, r.cwd_hint, r.status.value, r.exit_code,
                r.stdout_head, r.stderr_head, r.stdout_path, r.stderr_path,
                r.duration_ms, r.started_at, r.finished_at,
                r.reviewed_at, r.reviewed_by, r.reject_reason,
                r.cwd_resolved, r.max_runtime_seconds, r.max_output_bytes,
                int(r.review_required), int(r.persistent), r.created_at,
            ),
        )

    @_synchronized
    def get_job(self, job_id: str) -> "JobRecord | None":
        row = self._conn.execute(
            "SELECT * FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
        if row is None:
            return None
        return self._row_to_job(row)

    @staticmethod
    def _row_to_job(row) -> "JobRecord":
        from runtime.models import JobRecord, JobStatus, JobInterpreter
        # ``reason`` may be missing on rows from pre-migration installs that
        # never hit a terminal transition with the new schema — use defensive
        # key access via SQLite's Row mapping interface.
        keys = row.keys() if hasattr(row, "keys") else ()
        reason = row["reason"] if "reason" in keys else None

        return JobRecord(
            id=row["id"],
            task_id=row["task_id"],
            agent_name=row["agent_name"],
            title=row["title"],
            rationale=row["rationale"],
            script_text=row["script_text"],
            interpreter=JobInterpreter(row["interpreter"]),
            cwd_hint=row["cwd_hint"],
            status=JobStatus(row["status"]),
            exit_code=row["exit_code"],
            stdout_head=row["stdout_head"],
            stderr_head=row["stderr_head"],
            stdout_path=row["stdout_path"],
            stderr_path=row["stderr_path"],
            duration_ms=row["duration_ms"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
            reviewed_at=row["reviewed_at"],
            reviewed_by=row["reviewed_by"],
            reject_reason=row["reject_reason"],
            cwd_resolved=row["cwd_resolved"],
            max_runtime_seconds=row["max_runtime_seconds"],
            max_output_bytes=row["max_output_bytes"],
            review_required=bool(row["review_required"]),
            persistent=bool(row["persistent"]),
            reason=reason,
            created_at=row["created_at"],
        )

    @_synchronized
    def get_job_status(self, job_id: str) -> str | None:
        """Return jobs.status for the given job id, or None if not present.

        Used by the blocked-on-job predicate-check in _maybe_resume_blocked_task
        and by run_step_impl's entry-state branch (spec §5.1, §5.4).
        """
        row = self._conn.execute(
            "SELECT status FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
        return row["status"] if row is not None else None

    @_synchronized
    def get_job_owner_task_id(self, job_id: str) -> str | None:
        """Return jobs.task_id for the given job id, or None if not present.

        Used by the completion-route validation to verify that the agent
        submitting a blocked completion actually owns the referenced jobs.
        """
        row = self._conn.execute(
            "SELECT task_id FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
        return row["task_id"] if row is not None else None

    @_synchronized
    def get_running_job_task_ids(self) -> dict[str, str]:
        """Snapshot running jobs for terminal cleanup under the DB lock."""
        rows = self._conn.execute(
            "SELECT id, task_id FROM jobs WHERE status='running'"
        ).fetchall()
        return {row["id"]: row["task_id"] for row in rows}

    @_synchronized
    def backstop_terminated_task_jobs(self, task_id: str, *, finished_at: str) -> int:
        """Atomically settle only still-running jobs owned by a terminal task.

        The runner/process termination happens outside this short critical
        section.  This is solely the durable SQLite backstop for a runner that
        did not reach its own terminal write.
        """
        cursor = self._conn.execute(
            "UPDATE jobs SET status='failed', reason='task_ended', finished_at=? "
            "WHERE task_id=? AND status='running'",
            (finished_at, task_id),
        )
        self._conn.commit()
        return cursor.rowcount

    @_synchronized
    def backstop_consumed_task_completion_recovery_jobs(
        self, *, task_id: str, agent: str, result_row_id: int, finished_at: str,
    ) -> int:
        """Durably settle jobs only for the exact recovery owner just consumed.

        Startup calls this after the recovery consumer commits, before the
        ordinary orphan scan can classify a still-running owned job.  The
        guarded join deliberately rejects cancellation, replacement, or a
        different accepted result; it never turns a historical receipt into a
        generic terminal-job cleanup authority.
        """
        cursor = self._conn.execute(
            """UPDATE jobs SET status='failed', reason='task_ended', finished_at=?,
                              duration_ms=COALESCE(duration_ms, 0)
               WHERE task_id=? AND status='running'
                 AND EXISTS (
                   SELECT 1 FROM task_completion_recoveries AS r
                   JOIN task_results AS tr ON tr.id=r.accepted_result_id
                   JOIN tasks AS t ON t.id=r.task_id
                   WHERE r.task_id=? AND r.agent=? AND r.accepted_result_id=?
                     AND r.state='callback_consumed'
                     AND tr.task_id=r.task_id AND tr.agent=r.agent
                     AND tr.session_id=r.recovery_session_id
                     AND t.assigned_agent=r.agent
                     AND t.current_session_id=r.recovery_session_id
                     AND t.cancelled_at IS NULL
                     AND t.status IN ('completed', 'failed')
                 )""",
            (finished_at, task_id, task_id, agent, result_row_id),
        )
        self._conn.commit()
        return cursor.rowcount

    @_synchronized
    def settle_consumed_task_completion_recovery_jobs(
        self, *, task_id: str, agent: str, recovery_session_id: str,
        result_row_id: int, terminal_status: str, finished_at: str,
    ) -> tuple[str, ...] | None:
        """Capture and settle the exact current recovery owner's running jobs.

        ``None`` is a lost receipt; an empty tuple is a valid, owned zero-job
        handoff.  The returned IDs are intentionally the live-cleanup input.
        """
        if terminal_status not in (TaskStatus.COMPLETED.value, TaskStatus.FAILED.value):
            return None
        # Preserve the externally observable selection seam.  It is only an
        # admission hint: the identical SQL predicate below remains the
        # authoritative recheck in the write transaction.
        if not self.consumed_task_completion_recovery_owner_is_current(
            task_id=task_id, agent=agent,
            recovery_session_id=recovery_session_id,
            result_row_id=result_row_id, terminal_status=terminal_status,
        ):
            return None
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            # Keep the ownership validation inside this transaction.  Calling
            # the decorated public predicate here would either release the
            # lock or become unobservable to a test seam that intentionally
            # replaces it while exercising the post-consumption boundary.
            owner = self._conn.execute(
                """SELECT 1 FROM task_completion_recoveries AS r
                   JOIN task_results AS tr ON tr.id=r.accepted_result_id
                   JOIN tasks AS t ON t.id=r.task_id
                   WHERE r.task_id=? AND r.agent=? AND r.recovery_session_id=?
                     AND r.accepted_result_id=?
                     AND r.state='callback_consumed'
                     AND t.status=?
                     AND t.cancelled_at IS NULL
                     AND tr.task_id=r.task_id AND tr.agent=r.agent
                     AND tr.session_id=r.recovery_session_id
                     AND t.assigned_agent=r.agent
                     AND t.current_session_id=r.recovery_session_id""",
                (task_id, agent, recovery_session_id, result_row_id,
                 terminal_status),
            ).fetchone()
            if owner is None:
                self._conn.rollback()
                return None
            rows = self._conn.execute(
                "SELECT id FROM jobs WHERE task_id=? AND status='running' ORDER BY id",
                (task_id,),
            ).fetchall()
            job_ids = tuple(row["id"] for row in rows)
            if job_ids:
                placeholders = ",".join("?" for _ in job_ids)
                self._conn.execute(
                    f"UPDATE jobs SET status='failed', reason='task_ended', finished_at=?, "
                    f"duration_ms=COALESCE(duration_ms, 0) WHERE id IN ({placeholders}) "
                    "AND status='running'",
                    (finished_at, *job_ids),
                )
            self._conn.commit()
            return job_ids
        except Exception:
            self._conn.rollback()
            raise

    @_synchronized
    def list_jobs_db(
        self,
        *,
        status: str | list[str] | None = None,
        agent: str | None = None,
        task_id: str | None = None,
        review_required: bool | None = None,
        persistent: bool | None = None,
        limit: int = 50,
    ) -> list["JobRecord"]:
        clauses: list[str] = []
        params: list = []
        if status is not None:
            statuses = [status] if isinstance(status, str) else list(status)
            placeholders = ",".join("?" * len(statuses))
            clauses.append(f"status IN ({placeholders})")
            params.extend(statuses)
        if agent is not None:
            clauses.append("agent_name = ?")
            params.append(agent)
        if task_id is not None:
            clauses.append("task_id = ?")
            params.append(task_id)
        if review_required is not None:
            clauses.append("review_required = ?")
            params.append(1 if review_required else 0)
        if persistent is not None:
            clauses.append("persistent = ?")
            params.append(1 if persistent else 0)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(int(limit))
        rows = self._conn.execute(
            f"SELECT * FROM jobs {where} "
            f"ORDER BY created_at DESC, id DESC LIMIT ?",
            params,
        ).fetchall()
        return [self._row_to_job(r) for r in rows]

    @_synchronized
    def list_job_ids_by_status(self, statuses: set[str]) -> list[str]:
        """Exhaustive status-filtered job-id query (no cap, DB-side filter).

        ``list_jobs_db`` is a presentation list capped (default 50) and ordered
        newest-first; using it for a liveness check can hide an old active row
        behind newer terminal rows. This returns every job id whose status is
        in ``statuses`` so a portability preflight cannot miss an active job.
        Read-only; returns ids only, not full job records.
        """
        if not statuses:
            return []
        placeholders = ",".join("?" * len(statuses))
        rows = self._conn.execute(
            f"SELECT id FROM jobs WHERE status IN ({placeholders}) "
            "ORDER BY created_at, id",
            tuple(sorted(statuses)),
        ).fetchall()
        return [row["id"] for row in rows]

    @_synchronized
    def transition_job_to_rejected(
        self, job_id: str, *, reviewer: str, reason: str, reviewed_at: str
    ) -> None:
        cur = self._conn.execute(
            "UPDATE jobs "
            "SET status='rejected', reviewed_by=?, reject_reason=?, reviewed_at=? "
            "WHERE id=? AND status='pending'",
            (reviewer, reason, reviewed_at, job_id),
        )
        self._conn.commit()
        if cur.rowcount == 0:
            raise ValueError(f"not_pending: job {job_id} cannot be rejected")

    @_synchronized
    def transition_job_to_running(
        self,
        job_id: str,
        *,
        reviewer: str,
        reviewed_at: str,
        started_at: str,
        cwd_resolved: str,
        max_runtime_seconds: int | None,
        stdout_path: str,
        stderr_path: str,
    ) -> None:
        changed = self._transition_job_to_running_uncommitted(
            job_id, reviewer=reviewer, reviewed_at=reviewed_at, started_at=started_at,
            cwd_resolved=cwd_resolved, max_runtime_seconds=max_runtime_seconds,
            stdout_path=stdout_path, stderr_path=stderr_path,
        )
        self._conn.commit()
        if not changed:
            raise ValueError(f"not_pending: job {job_id} cannot transition to running")

    def _transition_job_to_running_uncommitted(
        self,
        job_id: str,
        *,
        reviewer: str,
        reviewed_at: str,
        started_at: str,
        cwd_resolved: str,
        max_runtime_seconds: int | None,
        stdout_path: str,
        stderr_path: str,
    ) -> bool:
        cur = self._conn.execute(
            "UPDATE jobs SET "
            "status='running', reviewed_by=?, reviewed_at=?, started_at=?, "
            "cwd_resolved=?, max_runtime_seconds=?, stdout_path=?, stderr_path=? "
            "WHERE id=? AND status='pending'",
            (reviewer, reviewed_at, started_at, cwd_resolved, max_runtime_seconds,
             stdout_path, stderr_path, job_id),
        )
        return cur.rowcount == 1

    @_synchronized
    def transition_job_to_terminal(
        self,
        job_id: str,
        *,
        status: "JobStatus",
        exit_code: int | None,
        finished_at: str,
        duration_ms: int,
        stdout_head: str | None,
        stderr_head: str | None,
        reason: str | None = None,
    ) -> None:
        if status.value not in ("completed", "failed"):
            raise ValueError(f"invalid terminal status: {status.value}")
        cur = self._conn.execute(
            "UPDATE jobs SET "
            "status=?, exit_code=?, finished_at=?, duration_ms=?, "
            "stdout_head=?, stderr_head=?, reason=? "
            "WHERE id=? AND status='running'",
            (status.value, exit_code, finished_at, duration_ms,
             stdout_head, stderr_head, reason, job_id),
        )
        self._conn.commit()
        if cur.rowcount == 0:
            raise ValueError(f"not_running: job {job_id} cannot transition to terminal")

    @_synchronized
    def recover_orphaned_running_jobs(self, *, now_iso: str) -> list[str]:
        """Force-transition any SR left in 'running' state to 'failed'.

        Called from the daemon FastAPI lifespan on startup. The subprocess
        and its parent daemon process are gone; partial output on disk is
        preserved but the row is marked failed so the founder UI doesn't
        leave them in a permanent running state.
        """
        rows = self._conn.execute(
            "SELECT id FROM jobs WHERE status='running'"
        ).fetchall()
        ids = [r["id"] for r in rows]
        if not ids:
            return []
        self._conn.executemany(
            "UPDATE jobs SET status='failed', reason='daemon_crash', finished_at=?, "
            "duration_ms=COALESCE(duration_ms, 0), "
            "stderr_head=COALESCE(stderr_head, '') || '\n[daemon restart killed run]' "
            "WHERE id=?",
            [(now_iso, job_id) for job_id in ids],
        )
        self._conn.commit()
        return ids
