from __future__ import annotations

import json
from datetime import datetime

from runtime.infrastructure.db._shared import _synchronized
from runtime.models import (
    ThreadAttachment,
    ThreadInvocation,
    ThreadInvocationPurpose,
    ThreadInvocationStatus,
    ThreadMessage,
    ThreadMessageKind,
    ThreadParticipant,
    ThreadRecord,
    ThreadStatus,
)


class ThreadsMixin:
    # --- Threads Core ---

    @_synchronized
    def next_thread_id(self) -> str:
        """Return the next available THR-NNN id.

        Callers must hold DaemonState.db_lock across the next_thread_id() +
        insert_thread() pair to avoid duplicate IDs under concurrent requests
        (same requirement as next_task_id).
        """
        cursor = self._conn.execute(
            "SELECT MAX(CAST(SUBSTR(id, 5) AS INTEGER)) AS m "
            "FROM threads WHERE id GLOB 'THR-[0-9]*'"
        )
        n = (cursor.fetchone()["m"] or 0) + 1
        return f"THR-{n:03d}"

    @_synchronized
    def insert_thread(self, t: ThreadRecord) -> None:
        # Spec §3.1: composed_from_task_id is the sole composer attribution.
        self._conn.execute(
            """INSERT INTO threads (
                id, subject, started_at, archived_at, status,
                forwarded_from_id, forwarded_from_kind,
                turn_cap, turns_used, summary,
                transcript_path,
                composed_by, composed_from_task_id, composed_from_dream_id,
                pinned_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                t.id,
                t.subject,
                t.started_at.isoformat(),
                t.archived_at.isoformat() if t.archived_at else None,
                t.status.value,
                t.forwarded_from_id,
                t.forwarded_from_kind,
                t.turn_cap,
                t.turns_used,
                t.summary,
                t.transcript_path,
                t.composed_by,
                t.composed_from_task_id,
                t.composed_from_dream_id,
                t.pinned_at.isoformat() if t.pinned_at else None,
            ),
        )
        self._conn.commit()

    def _row_to_thread(self, row) -> ThreadRecord:
        keys = row.keys()
        return ThreadRecord(
            id=row["id"],
            subject=row["subject"],
            status=ThreadStatus(row["status"]),
            started_at=datetime.fromisoformat(row["started_at"]),
            archived_at=datetime.fromisoformat(row["archived_at"]) if row["archived_at"] else None,
            forwarded_from_id=row["forwarded_from_id"],
            forwarded_from_kind=row["forwarded_from_kind"],
            turn_cap=row["turn_cap"],
            turns_used=row["turns_used"],
            summary=row["summary"],
            transcript_path=row["transcript_path"],
            composed_by=row["composed_by"] if "composed_by" in keys else "founder",
            composed_from_task_id=row["composed_from_task_id"] if "composed_from_task_id" in keys else None,
            composed_from_dream_id=row["composed_from_dream_id"] if "composed_from_dream_id" in keys else None,
            last_speaker=row["last_speaker"] if "last_speaker" in keys else None,
            pinned_at=(
                datetime.fromisoformat(row["pinned_at"]) if row["pinned_at"] else None
            ) if "pinned_at" in keys else None,
            last_activity_at=(
                datetime.fromisoformat(row["last_activity_at"]) if row["last_activity_at"] else None
            ) if "last_activity_at" in keys else None,
        )

    @_synchronized
    def get_thread(self, thread_id: str) -> ThreadRecord | None:
        cursor = self._conn.execute(
            "SELECT * FROM threads WHERE id = ?", (thread_id,)
        )
        row = cursor.fetchone()
        return self._row_to_thread(row) if row else None

    @_synchronized
    def list_threads(self, *, status: str | None = None, limit: int = 50) -> list[ThreadRecord]:
        query = (
            "SELECT t.*, "
            "(SELECT tm.speaker FROM thread_messages tm "
            " WHERE tm.thread_id = t.id ORDER BY tm.seq DESC LIMIT 1) AS last_speaker, "
            "(SELECT MAX(tm.created_at) FROM thread_messages tm "
            " WHERE tm.thread_id = t.id) AS last_activity_at "
            "FROM threads t "
        )
        # THR-209 message-9 correction (TASK-5976): pinned threads rank above
        # unpinned ONLY in the OPEN list, ordered by immutable NUMERIC thread
        # ID descending (THR-10 above THR-2 — never lexicographic subject/text
        # and never activity). The numeric key is conditional on pinned_at
        # being set, so unpinned rows tie on it (NULL → 0) and fall through to
        # the exact existing ordinary key — ordinary order is byte-for-byte
        # unchanged when no pins exist. ARCHIVED and status-less views have
        # ZERO pin presentation: no pin rank at all, ordinary ordering only
        # (archived → archived_at DESC; status-less → started_at DESC), so
        # archived pin state can never leak into a mixed view.
        pinned_rank = "CASE WHEN t.pinned_at IS NOT NULL THEN 0 ELSE 1 END"
        pinned_numeric_id = (
            "CASE WHEN t.pinned_at IS NOT NULL THEN "
            "CAST(SUBSTR(t.id, 5) AS INTEGER) END DESC"
        )
        params: tuple
        if status == "archived":
            base_order = "COALESCE(t.archived_at, t.started_at) DESC"
            query += f"WHERE t.status = ? ORDER BY {base_order} LIMIT ?"
            params = (status, limit)
        elif status:
            query += (
                f"WHERE t.status = ? ORDER BY {pinned_rank}, {pinned_numeric_id}, "
                f"t.started_at DESC LIMIT ?"
            )
            params = (status, limit)
        else:
            query += f"ORDER BY t.started_at DESC LIMIT ?"
            params = (limit,)
        cursor = self._conn.execute(query, params)
        return [self._row_to_thread(r) for r in cursor.fetchall()]

    @_synchronized
    def list_threads_by_composed_from_task_id(
        self, task_id: str,
    ) -> list[ThreadRecord]:
        """Threads composed from a given task (daemon-cleanup provenance).

        Targeted identity lookup (THR-195 / TASK-6043) backed by the existing
        ``idx_threads_composed_from_task`` index: unlike a bounded
        presentation-page scan of open threads, this can locate an older
        thread no matter how many newer threads exist. Ordered newest-started
        first.
        """
        cursor = self._conn.execute(
            "SELECT * FROM threads WHERE composed_from_task_id = ? "
            "ORDER BY started_at DESC",
            (task_id,),
        )
        return [self._row_to_thread(r) for r in cursor.fetchall()]

    @_synchronized
    def is_thread_participant(self, thread_id: str, agent_name: str) -> bool:
        cursor = self._conn.execute(
            "SELECT 1 FROM thread_participants WHERE thread_id = ? AND agent_name = ?",
            (thread_id, agent_name),
        )
        return cursor.fetchone() is not None

    @_synchronized
    def list_thread_participants(self, thread_id: str) -> list[ThreadParticipant]:
        cursor = self._conn.execute(
            "SELECT thread_id, agent_name, added_at, added_by "
            "FROM thread_participants WHERE thread_id = ? ORDER BY added_at",
            (thread_id,),
        )
        return [
            ThreadParticipant(
                thread_id=r["thread_id"],
                agent_name=r["agent_name"],
                added_at=datetime.fromisoformat(r["added_at"]),
                added_by=r["added_by"],
            )
            for r in cursor.fetchall()
        ]

    @_synchronized
    def list_thread_participant_names_for_threads(
        self, thread_ids: list[str],
    ) -> dict[str, list[str]]:
        """Batch-read current participant names for a list projection.

        The public list endpoint deliberately preserves SQLite's historical
        negative-limit behaviour (``LIMIT -1`` means unbounded).  Therefore
        this *new* projection must not assume its input is capped: keep every
        returned id, but issue finite IN batches so one legacy response cannot
        exceed SQLite's parameter ceiling.  Ordering within each membership is
        still the real ``added_at`` order, and the result keys retain the
        caller's row order.
        """
        if not thread_ids:
            return {}
        result = {thread_id: [] for thread_id in thread_ids}
        for start in range(0, len(thread_ids), 500):
            batch = thread_ids[start:start + 500]
            placeholders = ", ".join("?" for _ in batch)
            cursor = self._conn.execute(
                "SELECT thread_id, agent_name FROM thread_participants "
                f"WHERE thread_id IN ({placeholders}) ORDER BY thread_id, added_at",
                tuple(batch),
            )
            for row in cursor.fetchall():
                result[row["thread_id"]].append(row["agent_name"])
        return result

    @_synchronized
    def remove_thread_participant(
        self, thread_id: str, agent_name: str
    ) -> bool:
        """Hard-delete a participant row. Returns True if a row was deleted."""
        cursor = self._conn.execute(
            "DELETE FROM thread_participants WHERE thread_id = ? AND agent_name = ?",
            (thread_id, agent_name),
        )
        self._conn.commit()
        return cursor.rowcount == 1

    @_synchronized
    def archive_thread_and_reset_sessions(
        self,
        thread_id: str,
        *,
        summary: str,
        audit_scope_id: str,
        audit_agent: str,
    ) -> None:
        """Archive a thread and invalidate every participant's resume state in
        ONE database transaction: the ``ARCHIVED`` status flip, the
        participant session resets (id NULL, watermark 0), and the
        ``thread_session_invalidated`` audit row commit atomically. A failure
        at any step rolls back the whole archive, leaving the thread OPEN
        with every session row and no audit residue.

        THR-200: the thread is closed; if it is ever re-opened, every
        participant resumes from a fresh full-prompt launch instead of a
        stale provider session.
        """
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            self._set_thread_status_archived_uncommitted(
                thread_id, summary=summary,
            )
            rows = self._reset_thread_sessions_for_thread_uncommitted(thread_id)
            if rows:
                self.insert_audit_log_uncommitted(
                    task_id=audit_scope_id,
                    agent=audit_agent,
                    action="thread_session_invalidated",
                    payload={"reason": "archive", "rows": rows},
                )
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    @_synchronized
    def append_thread_message(
        self,
        *,
        thread_id: str,
        speaker: str,
        kind: ThreadMessageKind,
        body_markdown: str | None = None,
        decline_reason: str | None = None,
        system_payload: dict | None = None,
        attachments: list[ThreadAttachment] | None = None,
        sent_from_task_id: str | None = None,
    ) -> int:
        """Append a message and return its allocated seq.

        Atomic against concurrent appends — both the seq allocation and the
        insert happen under the connection's transaction, and the unique
        index on (thread_id, seq) guards against any race.
        """
        try:
            self._conn.execute("BEGIN")
            next_seq = self._append_thread_message_uncommitted(
                thread_id=thread_id,
                speaker=speaker,
                kind=kind,
                body_markdown=body_markdown,
                decline_reason=decline_reason,
                system_payload=system_payload,
                attachments=attachments,
                sent_from_task_id=sent_from_task_id,
            )
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise
        return next_seq

    def _attachments_for_messages(
        self, thread_id: str, seqs: list[int]
    ) -> dict[int, list[ThreadAttachment]]:
        if not seqs:
            return {}
        placeholders = ",".join("?" for _ in seqs)
        cursor = self._conn.execute(
            "SELECT * FROM thread_message_attachments "
            f"WHERE thread_id = ? AND message_seq IN ({placeholders}) "
            "ORDER BY message_seq, ordinal",
            (thread_id, *seqs),
        )
        out: dict[int, list[ThreadAttachment]] = {seq: [] for seq in seqs}
        for row in cursor.fetchall():
            out.setdefault(row["message_seq"], []).append(
                ThreadAttachment(
                    artifact_name=row["artifact_name"],
                    display_name=row["display_name"],
                    size_bytes=row["size_bytes"],
                    content_type=row["content_type"],
                    uploaded_by=row["uploaded_by"],
                    thread_attachment_id=row["thread_attachment_id"],
                )
            )
        return out

    @_synchronized
    def list_thread_messages(
        self, thread_id: str, *, since_seq: int = 0, limit: int | None = 1000
    ) -> list[ThreadMessage]:
        """Messages for ``thread_id`` with ``seq > since_seq``, ascending.

        ``limit`` caps the returned row count; pass ``None`` for an uncapped
        load — the daemon's resume seam needs the complete canonical
        transcript to prove delta completeness (TASK-5989).
        """
        sql = (
            "SELECT * FROM thread_messages "
            "WHERE thread_id = ? AND seq > ? ORDER BY seq"
        )
        params: list = [thread_id, since_seq]
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        cursor = self._conn.execute(sql, params)
        rows = cursor.fetchall()
        attachments_by_seq = self._attachments_for_messages(
            thread_id,
            [r["seq"] for r in rows],
        )
        return [
            ThreadMessage(
                id=r["id"],
                thread_id=r["thread_id"],
                seq=r["seq"],
                speaker=r["speaker"],
                kind=ThreadMessageKind(r["kind"]),
                body_markdown=r["body_markdown"],
                decline_reason=r["decline_reason"],
                system_payload=json.loads(r["system_payload_json"]) if r["system_payload_json"] else None,
                attachments=attachments_by_seq.get(r["seq"], []),
                mentions=json.loads(r["mentions_json"]) if r["mentions_json"] else [],
                created_at=datetime.fromisoformat(r["created_at"]),
            )
            for r in rows
        ]

    @_synchronized
    def get_thread_max_message_seq(self, thread_id: str) -> int:
        """Authoritative highest transcript seq for ``thread_id`` (0 if empty).

        Read-only — the independent upper bound the thread runner uses to
        prove that the loaded transcript covers the complete required range
        before authorizing a resumed delta prompt (TASK-5989).
        """
        return self._thread_tail_seq(thread_id)

    @_synchronized
    def get_thread_message_by_seq(
        self, thread_id: str, seq: int
    ) -> ThreadMessage | None:
        cursor = self._conn.execute(
            "SELECT * FROM thread_messages WHERE thread_id = ? AND seq = ?",
            (thread_id, seq),
        )
        row = cursor.fetchone()
        if not row:
            return None
        attachments_by_seq = self._attachments_for_messages(thread_id, [seq])
        return ThreadMessage(
            id=row["id"],
            thread_id=row["thread_id"],
            seq=row["seq"],
            speaker=row["speaker"],
            kind=ThreadMessageKind(row["kind"]),
            body_markdown=row["body_markdown"],
            decline_reason=row["decline_reason"],
            system_payload=json.loads(row["system_payload_json"]) if row["system_payload_json"] else None,
            attachments=attachments_by_seq.get(seq, []),
            mentions=json.loads(row["mentions_json"]) if row["mentions_json"] else [],
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    def _insert_thread_uncommitted(self, t: ThreadRecord) -> None:
        """Insert a thread row WITHOUT committing — caller owns the transaction.

        Mirrors ``insert_audit_log_uncommitted`` / ``_append_thread_message_uncommitted``:
        the row joins the caller's open BEGIN IMMEDIATE transaction and is
        rolled back with it on any later failure.

        TASK-6082 (founder ruling): the legacy ``threads.mention_routing_enabled``
        column is OMITTED from the insert (TASK-6027 removed the field from
        ``ThreadRecord`` — routing is unconditional), matching ``insert_thread``;
        the shipped column keeps its NOT NULL DEFAULT 1 (inert storage).
        """
        self._conn.execute(
            """INSERT INTO threads (
                id, subject, started_at, archived_at, status,
                forwarded_from_id, forwarded_from_kind,
                turn_cap, turns_used, summary,
                transcript_path,
                composed_by, composed_from_task_id, composed_from_dream_id,
                pinned_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                t.id,
                t.subject,
                t.started_at.isoformat(),
                t.archived_at.isoformat() if t.archived_at else None,
                t.status.value,
                t.forwarded_from_id,
                t.forwarded_from_kind,
                t.turn_cap,
                t.turns_used,
                t.summary,
                t.transcript_path,
                t.composed_by,
                t.composed_from_task_id,
                t.composed_from_dream_id,
                t.pinned_at.isoformat() if t.pinned_at else None,
            ),
        )

    def _increment_thread_turns_used_uncommitted(
        self, thread_id: str, *, by: int = 1,
    ) -> None:
        """Raise ``threads.turns_used`` WITHOUT committing (caller owns the
        transaction)."""
        self._conn.execute(
            "UPDATE threads SET turns_used = turns_used + ? WHERE id = ?",
            (by, thread_id),
        )

    def _row_to_invocation(self, row) -> ThreadInvocation:
        return ThreadInvocation(
            id=row["id"],
            thread_id=row["thread_id"],
            agent_name=row["agent_name"],
            invocation_token=row["invocation_token"],
            triggering_seq=row["triggering_seq"],
            purpose=ThreadInvocationPurpose(row["purpose"]),
            status=ThreadInvocationStatus(row["status"]),
            enqueued_at=datetime.fromisoformat(row["enqueued_at"]),
            started_at=datetime.fromisoformat(row["started_at"]) if row["started_at"] else None,
            consumed_at=datetime.fromisoformat(row["consumed_at"]) if row["consumed_at"] else None,
            session_id=row["session_id"],
            executor=row["executor"],
            model=row["model"],
            reply_message_seq=row["reply_message_seq"],
            dispatched_task_id=row["dispatched_task_id"],
            decline_reason=row["decline_reason"],
        )

    @_synchronized
    def get_pending_invocation(self, token: str) -> ThreadInvocation | None:
        cursor = self._conn.execute(
            "SELECT * FROM thread_invocations "
            "WHERE invocation_token = ? AND status = 'pending'",
            (token,),
        )
        row = cursor.fetchone()
        return self._row_to_invocation(row) if row else None

    @_synchronized
    def get_invocation_any_status(self, token: str) -> ThreadInvocation | None:
        cursor = self._conn.execute(
            "SELECT * FROM thread_invocations WHERE invocation_token = ?",
            (token,),
        )
        row = cursor.fetchone()
        return self._row_to_invocation(row) if row else None

    @_synchronized
    def record_dispatch_on_invocation(
        self, token: str, *, task_id: str
    ) -> bool:
        cursor = self._conn.execute(
            "UPDATE thread_invocations SET dispatched_task_id = ? "
            "WHERE invocation_token = ? AND status = 'pending' "
            "AND dispatched_task_id IS NULL",
            (task_id, token),
        )
        self._conn.commit()
        return cursor.rowcount == 1

    @_synchronized
    def list_thread_invocations(
        self,
        thread_id: str,
        *,
        status: ThreadInvocationStatus | None = None,
    ) -> list[ThreadInvocation]:
        if status is not None:
            cursor = self._conn.execute(
                "SELECT * FROM thread_invocations "
                "WHERE thread_id = ? AND status = ? ORDER BY id",
                (thread_id, status.value),
            )
        else:
            cursor = self._conn.execute(
                "SELECT * FROM thread_invocations WHERE thread_id = ? ORDER BY id",
                (thread_id,),
            )
        return [self._row_to_invocation(r) for r in cursor.fetchall()]

    @_synchronized
    def list_pending_thread_invocations(self) -> list[ThreadInvocation]:
        """Return every org-wide ``pending`` thread invocation (any thread).

        Used by the portability preflight quiescence check: a pending reply/
        bootstrap/task-followup invocation is in-flight work and must block.
        """
        cursor = self._conn.execute(
            "SELECT * FROM thread_invocations "
            "WHERE status = ? ORDER BY id",
            (ThreadInvocationStatus.PENDING.value,),
        )
        return [self._row_to_invocation(r) for r in cursor.fetchall()]

    @_synchronized
    def list_started_invocations_for_agent(
        self, agent_name: str,
    ) -> list[tuple[str, str]]:
        """Return (invocation_token, thread_id) for pending invocations that
        have already started (``started_at`` is set) for ``agent_name``.
        """
        cursor = self._conn.execute(
            "SELECT invocation_token, thread_id FROM thread_invocations "
            "WHERE agent_name = ? AND status = 'pending' AND started_at IS NOT NULL",
            (agent_name,),
        )
        return [(row["invocation_token"], row["thread_id"]) for row in cursor.fetchall()]

    @_synchronized
    def list_invocations_for_thread_grouped_by_seq(
        self, thread_id: str
    ) -> dict[int, list[dict[str, object]]]:
        """Return {triggering_seq: [{agent_name, purpose, status, consumed_at}, ...]}
        for every REPLY and TASK_FOLLOWUP invocation in this thread.

        Used by GET /threads/{id} to build the per-message responder_status
        strip. Status values are the raw DB values (pending/consumed/declined/
        failed); the route's response builder renames consumed → replied.

        Each entry carries the authoritative ``purpose`` (''reply'' |
        ''task_followup'') so classification/dedup on the wire NEVER has to
        infer purpose from the triggering row's kind. A conversational REPLY
        invocation can hang off a SYSTEM row — the coalesced delivery range
        starts at the first unacknowledged sequence, which may be a system row
        (e.g. a resumed/terminal divider) rather than the founder message that
        caused the arrival. TASK_FOLLOWUP invocations hang off the SYSTEM row
        (task_completed / task_failed / task_escalated) that wakes a
        thread-dispatched agent (run_step._append_followup_system_and_reinvoke).
        Including TASK_FOLLOWUP lets the in-flight strip surface the woken agent
        on its system row. BOOTSTRAP is deliberately excluded — it has no
        triggering message row to attach a responder strip to.

        Note: ``consumed_at`` is set by both reply (``status='consumed'``) and
        decline (``status='declined'``) paths — the schema has no separate
        ``declined_at`` column. The wire ``responded_at`` field is sourced from
        this single timestamp regardless of which path consumed the invocation.
        """
        rows = self._conn.execute(
            "SELECT triggering_seq, agent_name, purpose, status, consumed_at, "
            "started_at, decline_reason "
            "FROM thread_invocations "
            "WHERE thread_id = ? AND purpose IN ('reply', 'task_followup') "
            "ORDER BY triggering_seq, agent_name",
            (thread_id,),
        ).fetchall()
        grouped: dict[int, list[dict[str, object]]] = {}
        for r in rows:
            entry = {
                "agent_name": r["agent_name"],
                "purpose": r["purpose"],
                "status": r["status"],
                "consumed_at": r["consumed_at"],
                "started_at": r["started_at"],
                "decline_reason": r["decline_reason"],
            }
            grouped.setdefault(r["triggering_seq"], []).append(entry)
        return grouped

    def _thread_tail_seq(self, thread_id: str) -> int:
        """Highest transcript seq for ``thread_id`` (0 for an empty thread)."""
        row = self._conn.execute(
            "SELECT COALESCE(MAX(seq), 0) AS tail "
            "FROM thread_messages WHERE thread_id = ?",
            (thread_id,),
        ).fetchone()
        return int(row["tail"])

    @_synchronized
    def list_open_thread_ids(self) -> list[str]:
        """Every OPEN thread id (activation cutover sweep at startup)."""
        cursor = self._conn.execute(
            "SELECT id FROM threads WHERE status = 'open' ORDER BY id",
        )
        return [r["id"] for r in cursor.fetchall()]

    @_synchronized
    def increment_thread_turns_used(self, thread_id: str, *, by: int = 1) -> None:
        self._conn.execute(
            "UPDATE threads SET turns_used = turns_used + ? WHERE id = ?",
            (by, thread_id),
        )
        self._conn.commit()

    @_synchronized
    def set_thread_status(
        self,
        thread_id: str,
        *,
        status: ThreadStatus,
        summary: str | None = None,
    ) -> None:
        if status is ThreadStatus.ARCHIVED:
            self._set_thread_status_archived_uncommitted(
                thread_id, summary=summary,
            )
        else:
            # OPEN (resume): plain status flip; archived_at + summary preserved as historical record.
            self._conn.execute(
                "UPDATE threads SET status = ? WHERE id = ?",
                (status.value, thread_id),
            )
        self._conn.commit()

    @_synchronized
    def set_thread_subject(self, thread_id: str, *, subject: str) -> None:
        """Update a thread's display title (THR-209 rename).

        Identity (id), participants, routing, unread, and lifecycle are
        untouched — only the durable ``subject`` changes. The caller is
        responsible for the ``thread_renamed`` audit row.
        """
        self._conn.execute(
            "UPDATE threads SET subject = ? WHERE id = ?",
            (subject, thread_id),
        )
        self._conn.commit()

    @_synchronized
    def set_thread_subject_uncommitted(self, thread_id: str, *, subject: str) -> None:
        """Update a thread's subject WITHOUT committing (THR-209 rename).

        Deliberately left UNCOMMITTED: the caller owns the surrounding
        transaction (``BEGIN IMMEDIATE`` … ``commit()``/``rollback()``) so the
        rename and its ``thread_renamed`` audit row commit atomically. This
        helper never commits independently inside an atomic unit (TASK-5644).
        """
        self._conn.execute(
            "UPDATE threads SET subject = ? WHERE id = ?",
            (subject, thread_id),
        )

    @_synchronized
    def rename_thread_with_audit(
        self, thread_id: str, *, subject: str, actor: str = "founder",
    ) -> bool:
        """Atomic founder rename + ``thread_renamed`` audit row (THR-209).

        ONE rollback-safe transaction: the authoritative subject read, the
        idempotence decision, the subject UPDATE, and the audit row insert
        commit together — and roll back together on ANY failure — so a rename
        can never survive without its audit row and concurrent renames always
        record the truthful sequential old→new chain (last successful save
        wins). The whole unit holds the connection lock, so no other thread
        can join or commit the open transaction from the inside.

        The audit row keeps the documented ``audit_log.task_id`` = THR-* scope
        (``task_id`` = thread id), the founder ``actor``, and the
        ``{old_subject, new_subject}`` payload shape.

        Returns True when a durable transition occurred; False for an
        identical (no-op) save — true no-ops write nothing and are not
        audited. Raises ValueError for an unknown thread.
        """
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            row = self._conn.execute(
                "SELECT subject FROM threads WHERE id = ?", (thread_id,),
            ).fetchone()
            if row is None:
                raise ValueError(f"thread {thread_id} not found")
            old_subject = row["subject"]
            if old_subject == subject:
                self._conn.rollback()
                return False
            self.set_thread_subject_uncommitted(thread_id, subject=subject)
            self.insert_audit_log_uncommitted(
                task_id=thread_id,
                agent=actor,
                action="thread_renamed",
                payload={"old_subject": old_subject, "new_subject": subject},
            )
            self._conn.commit()
            return True
        except BaseException:
            self._conn.rollback()
            raise

    @_synchronized
    def set_thread_pinned_with_audit(
        self, thread_id: str, *, pinned: bool, actor: str = "founder",
    ) -> bool:
        """Atomic founder pin/unpin + audit row (THR-209).

        ONE rollback-safe transaction: the authoritative ``pinned_at`` read,
        the idempotence decision, the pin state UPDATE, and the
        ``thread_pinned``/``thread_unpinned`` audit row commit together — and
        roll back together on ANY failure — so pin state can never survive
        without its audit row. Concurrent same-state requests yield exactly
        one audit row for the one durable transition (the loser is a true
        no-op); opposite-state requests re-read the durable state inside their
        transaction, so neither is misclassified from a stale pre-lock
        snapshot. The whole unit holds the connection lock, so no other thread
        can join the open transaction.

        The audit row keeps the documented ``audit_log.task_id`` = THR-* scope
        (``task_id`` = thread id), the founder ``actor``, and the
        ``{pinned}`` payload shape.

        Returns True when a durable transition occurred; False for a
        same-state (no-op) save — true no-ops write nothing and are not
        audited. Raises ValueError for an unknown thread.
        """
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            row = self._conn.execute(
                "SELECT pinned_at FROM threads WHERE id = ?", (thread_id,),
            ).fetchone()
            if row is None:
                raise ValueError(f"thread {thread_id} not found")
            currently_pinned = row["pinned_at"] is not None
            if currently_pinned == pinned:
                self._conn.rollback()
                return False
            self.set_thread_pinned_uncommitted(thread_id, pinned=pinned)
            self.insert_audit_log_uncommitted(
                task_id=thread_id,
                agent=actor,
                action="thread_pinned" if pinned else "thread_unpinned",
                payload={"pinned": pinned},
            )
            self._conn.commit()
            return True
        except BaseException:
            self._conn.rollback()
            raise

    @_synchronized
    def set_thread_transcript_path(
        self, thread_id: str, transcript_path: str,
    ) -> None:
        """Persist the transcript path for an archived thread."""
        self._conn.execute(
            "UPDATE threads SET transcript_path = ? WHERE id = ?",
            (transcript_path, thread_id),
        )
        self._conn.commit()

    @_synchronized
    def set_thread_turn_cap(self, thread_id: str, *, new_cap: int) -> None:
        self._conn.execute(
            "UPDATE threads SET turn_cap = ? WHERE id = ?",
            (new_cap, thread_id),
        )
        self._conn.commit()

    @_synchronized
    def bump_thread_turn_cap(self, thread_id: str, *, delta: int = 1) -> int:
        """Atomically increment turn_cap by ``delta`` and return the new value.

        Used by the task-followup hook to make room for the system-triggered
        re-invocation when the projected turn count would exceed the current
        cap.  Each bump is audited at the call site via
        log_thread_turn_cap_auto_extended.
        """
        cursor = self._conn.execute(
            "UPDATE threads SET turn_cap = turn_cap + ? WHERE id = ? "
            "RETURNING turn_cap",
            (delta, thread_id),
        )
        row = cursor.fetchone()
        self._conn.commit()
        if row is None:
            raise KeyError(f"thread {thread_id} not found")
        return int(row["turn_cap"])

    @_synchronized
    def mint_followup_invocation_with_cap_extend(
        self,
        thread_id: str,
        *,
        agent_name: str,
        triggering_seq: int,
        cap_delta_if_over: int = 1,
    ) -> "tuple[ThreadInvocation, int | None]":
        """Atomically mint a TASK_FOLLOWUP invocation, auto-extending turn_cap
        by ``cap_delta_if_over`` if the projection (turns_used + pending + 1)
        would exceed the current cap.

        Returns (minted_invocation, new_cap_if_bumped_else_None).

        Closes the TOCTOU race where two concurrent root-task completions on the
        same thread both observe pending=N, both skip the bump, both mint, and
        leave the thread with more counted obligations than turn_cap permits.
        The @_synchronized lock on this method (backed by threading.RLock)
        serializes the read-compare-bump-mint sequence.

        Because Database._lock is an RLock (re-entrant), calling
        self.mint_thread_invocation from within this @_synchronized method is
        safe — the same thread can re-acquire the lock without deadlock.
        """
        # Read thread state under the @_synchronized lock.
        cur = self._conn.execute(
            "SELECT turns_used, turn_cap FROM threads WHERE id = ?",
            (thread_id,),
        )
        row = cur.fetchone()
        if row is None:
            raise KeyError(f"thread {thread_id} not found")
        turns_used = int(row["turns_used"])
        turn_cap = int(row["turn_cap"])

        counted = (
            ThreadInvocationPurpose.REPLY.value,
            ThreadInvocationPurpose.BOOTSTRAP.value,
            ThreadInvocationPurpose.TASK_FOLLOWUP.value,
        )
        cur = self._conn.execute(
            "SELECT COUNT(*) AS n FROM thread_invocations "
            "WHERE thread_id = ? AND status = ? AND purpose IN ({})".format(
                ",".join("?" * len(counted))
            ),
            (thread_id, ThreadInvocationStatus.PENDING.value, *counted),
        )
        pending = int(cur.fetchone()["n"])

        projected = turns_used + pending + 1
        new_cap: int | None = None
        if projected > turn_cap:
            self._conn.execute(
                "UPDATE threads SET turn_cap = turn_cap + ? WHERE id = ?",
                (cap_delta_if_over, thread_id),
            )
            new_cap = turn_cap + cap_delta_if_over

        # Delegate to mint_thread_invocation — safe because RLock is re-entrant.
        inv = self.mint_thread_invocation(
            thread_id=thread_id,
            agent_name=agent_name,
            triggering_seq=triggering_seq,
            purpose=ThreadInvocationPurpose.TASK_FOLLOWUP,
        )
        # No separate commit needed: mint_thread_invocation commits inside its
        # own @_synchronized acquisition. The cap UPDATE above is committed by
        # mint_thread_invocation's commit (SQLite commits all pending changes).
        return inv, new_cap
