from __future__ import annotations

from runtime.infrastructure.database import Database
from runtime.models import CompletionReport, TokenUsage


class AuditLogger:
    def __init__(self, db: Database) -> None:
        self._db = db

    def log_session_start(self, task_id: str, agent: str, workspace: str) -> None:
        self._db.insert_audit_log(
            task_id=task_id,
            agent=agent,
            action="session_start",
            payload={"workspace": workspace},
        )

    def log_session_end(
        self,
        task_id: str,
        agent: str,
        duration_seconds: int,
        token_usage: TokenUsage | None = None,
    ) -> None:
        payload: dict = {"duration_seconds": duration_seconds}
        if token_usage is not None:
            payload["token_usage"] = token_usage.model_dump()
            payload["token_count"] = token_usage.total
        else:
            payload["token_count"] = None
        self._db.insert_audit_log(
            task_id=task_id,
            agent=agent,
            action="session_end",
            payload=payload,
        )

    def log_completion_report(self, report: CompletionReport) -> None:
        # The task_results row is written by the agent callback at
        # POST /tasks/{task_id}/completion (routes/tasks.py); audit logger only
        # records the semantic event. Writing both produced duplicate rows
        # (one per task_result, ~20s apart) — see TASK-137 post-mortem.
        self._db.insert_audit_log(
            task_id=report.task_id,
            agent=report.agent,
            action="completion_report",
            payload=report.model_dump(),
        )

    def log_review_verdict(
        self,
        task_id: str,
        reviewer: str,
        verdict: str,
        feedback: str | None,
        reviewed_agent: str | None = None,
    ) -> None:
        self._db.insert_audit_log(
            task_id=task_id,
            agent=reviewer,
            action="review_verdict",
            payload={
                "verdict": verdict,
                "feedback": feedback,
                "reviewed_agent": reviewed_agent,
            },
        )

    def log_escalation(self, task_id: str, agent: str, reason: str) -> None:
        self._db.insert_audit_log(
            task_id=task_id,
            agent=agent,
            action="escalation",
            payload={"reason": reason},
        )

    def log_daemon_restart_failure(
        self, task_id: str, agent: str,
    ) -> None:
        """Recorded by _sweep_on_startup when an IN_PROGRESS task is FAILED
        due to a daemon restart. Distinct from log_escalation (which signals
        a manager-initiated escalate decision)."""
        self._db.insert_audit_log(
            task_id=task_id, agent=agent,
            action="daemon_restart_failure",
            payload={"reason": "daemon restarted mid-task"},
        )

    def log_escalation_resolved(
        self,
        task_id: str,
        decision: str,
        rationale: str,
        *,
        actor: str = "founder",
        thread_id: str | None = None,
        resolution_path: str = "manual_break_glass",
    ) -> None:
        """Record that an escalated task was resolved.

        THR-080: `actor` records the real agent who resolved (manager/thread-
        originated continue, or founder). `thread_id` cites the dispatching
        thread when the resolution came from the thread surface. Back-compat:
        both params are keyword-only with founder/None defaults.
        """
        payload: dict = {
            "decision": decision,
            "rationale": rationale,
            "resolution_path": resolution_path,
        }
        if thread_id is not None:
            payload["thread_id"] = thread_id
        self._db.insert_audit_log(
            task_id=task_id,
            agent=actor,
            action="escalation_resolved",
            payload=payload,
        )

    def log_escalation_continuation_rejected(
        self, task_id: str, *, actor: str, payload: dict,
    ) -> None:
        """Record a rejected THR-166 autonomous-continuation attempt.

        This is intentionally separate from the legacy/manual resolution audit:
        rejected policy attempts must remain reviewable without changing the
        meaning of ``audit_log.task_id``.
        """
        self._db.insert_audit_log(
            task_id=task_id, agent=actor,
            action="escalation_continuation_rejected", payload=payload,
        )

    def log_zombie_flagged(self, task_id: str, agent: str) -> None:
        """Recorded by the ongoing zombie reaper when a zombie task is first
        flagged (THR-090 Track B)."""
        self._db.insert_audit_log(
            task_id=task_id, agent=agent,
            action="zombie_flagged",
            payload={"reason": "zombie detected — dead pid + stale heartbeat"},
        )

    def log_zombie_cancelled(self, task_id: str, agent: str) -> None:
        """Recorded by the ongoing zombie reaper when a flagged zombie is
        cancelled after TTL expiry (THR-090 Track B)."""
        self._db.insert_audit_log(
            task_id=task_id, agent=agent,
            action="zombie_cancelled",
            payload={"reason": "zombie cancelled after TTL expiry"},
        )

    def log_zombie_cleared(self, task_id: str, agent: str) -> None:
        """Recorded by the ongoing zombie reaper when a flagged zombie recovers
        before TTL expiry (THR-090 Track B)."""
        self._db.insert_audit_log(
            task_id=task_id, agent=agent,
            action="zombie_cleared",
            payload={"reason": "zombie recovered — flag cleared"},
        )

    def log_portability_reconciled(
        self,
        *,
        task_id: str,
        actor: str,
        request_hash: str,
        evidence: dict,
        disposition: str,
        before: dict,
        after: dict,
    ) -> None:
        """Record a founder-authorized portability zombie reconciliation.

        Uses the ordinary ``task_id`` scope (the reconciled task), not a new
        scope prefix. Payload carries the SHA-256 request hash, founder evidence,
        disposition, and before/after state so the human decision is auditable.
        """
        self._db.insert_audit_log(
            task_id=task_id,
            agent=actor,
            action="portability_reconciled",
            payload={
                "request_hash": request_hash,
                "evidence": evidence,
                "disposition": disposition,
                "before": before,
                "after": after,
            },
        )

    def log_task_cancelled(
        self, task_id: str, rationale: str, cascade: bool, actor: str = "founder",
    ) -> None:
        self._db.insert_audit_log(
            task_id=task_id,
            agent=actor,
            action="task_cancelled",
            payload={"rationale": rationale, "cascade": cascade},
        )

    def log_progress(self, task_id: str, agent: str, message: str) -> None:
        """Record an agent-controlled mid-task progress note.

        Distinct from completion_report: this is a semantic checkpoint the
        agent emits while still working. Used by `happyranch tail` and `happyranch details`
        to give the founder visibility into long-running tasks without
        waiting for the final completion callback.
        """
        self._db.insert_audit_log(
            task_id=task_id,
            agent=agent,
            action="progress",
            payload={"message": message},
        )

    def log_auto_revisit_of(
        self,
        task_id: str,
        predecessor_root: str,
        failed_task: str,
        failed_agent: str,
        cascade: list[str],
        failure_kind: str,
        error_context: dict,
        attempt: int,
    ) -> None:
        """Record on the NEW root that it is an orchestrator-triggered revisit.

        Parallel to ``log_revisit_of`` (founder-triggered) but distinguished
        by action name so the prompt-injection step can render a different
        first-step header — and so we can count auto-revisits in the chain
        without conflating them with founder revisits when enforcing the
        per-chain cap.

        ``failure_kind`` is the classified granular failure mode
        (session_timeout / no_callback / rate_limit / executor_error /
        agent_exception / session_failed); hoisted to top-level of the
        payload so per-kind cap counting can read it with a single dict
        lookup without parsing ``error_context``. See
        ``docs/superpowers/specs/2026-05-25-session-timeout-auto-route-design.md``.

        ``error_context`` is the structured failure payload produced by
        ``_executor_failure_context``: mode, rc, stderr/stdout tail, etc.
        ``attempt`` is the 1-indexed auto-revisit number in this chain.
        """
        self._db.insert_audit_log(
            task_id=task_id,
            agent="orchestrator",
            action="auto_revisit_of",
            payload={
                "predecessor_root": predecessor_root,
                "failed_task": failed_task,
                "failed_agent": failed_agent,
                "cascade": cascade,
                "failure_kind": failure_kind,
                "error_context": error_context,
                "attempt": attempt,
            },
        )

    def log_orchestration_step(
        self, task_id: str, step_number: int, decision: dict
    ) -> int:
        return self._db.insert_audit_log(
            task_id=task_id,
            agent="orchestrator",
            action="orchestration_step",
            payload={"step_number": step_number, "decision": decision},
        )

    def log_chain_auto_advance(
        self,
        parent_task_id: str,
        *,
        leg_index: int,
        spawned_child_id: str,
        triggering_child_id: str,
        triggering_verdict: str | None,
        chain_origin_step_audit_id: int,
    ) -> None:
        """Audit row for an orchestrator-driven chain advance. Distinct from
        `orchestration_step` (which is manager-authored). Does NOT correspond to
        a tasks.orchestration_step_count bump — chains are one decision, multiple
        auto-advances.
        """
        self._db.insert_audit_log(
            task_id=parent_task_id,
            agent="orchestrator",
            action="chain_auto_advance",
            payload={
                "leg_index": leg_index,
                "spawned_child_id": spawned_child_id,
                "triggering_child_id": triggering_child_id,
                "triggering_verdict": triggering_verdict,
                "chain_origin_step_audit_id": chain_origin_step_audit_id,
            },
        )

    def log_task_blocked_on_jobs(
        self,
        task_id: str,
        agent: str,
        blocking_job_ids: list[str],
        output_summary_excerpt: str,
    ) -> None:
        """Written when run_step_impl transitions a task to in_progress+blocked_on_job
        in response to report.status=blocked + report.waiting_on_job_ids non-empty.
        Spec §7.
        """
        self._db.insert_audit_log(
            task_id=task_id,
            agent=agent,
            action="task_blocked_on_jobs",
            payload={
                "agent": agent,
                "blocking_job_ids": blocking_job_ids,
                "output_summary_excerpt": output_summary_excerpt,
            },
        )

    def log_fanout_spawned(
        self,
        task_id: str,
        agent: str,
        width: int,
        children_ids: list[str],
    ) -> None:
        """Written when run_step_impl atomically spawns all fan-out children."""
        self._db.insert_audit_log(
            task_id=task_id,
            agent=agent,
            action="fanout_spawned",
            payload={
                "agent": agent,
                "width": width,
                "children_ids": children_ids,
            },
        )

    def log_fanout_review_not_approved(
        self,
        task_id: str,
        *,
        reason: str,
    ) -> None:
        """Written when a pending-review fan-out re-enters and the review
        job was rejected or failed — children will NOT be spawned.

        Uses its own action so it does not suppress BLOCKED-JOBS-RESULTS
        (unlike ``log_orchestration_step``, which always writes
        ``action="orchestration_step"`` and would hide the job-outcome
        header from the manager prompt).
        """
        self._db.insert_audit_log(
            task_id=task_id,
            agent="orchestrator",
            action="fanout_review_not_approved",
            payload={"reason": reason},
        )

    def log_fanout_join(
        self,
        task_id: str,
        width: int,
        children_ids: list[str],
        context_markdown: str,
    ) -> None:
        """Written after try_claim_for_step wins on a fan-out parent and join
        context is built. Read by the fan-out join header injector."""
        self._db.insert_audit_log(
            task_id=task_id,
            agent="orchestrator",
            action="fanout_join",
            payload={
                "width": width,
                "children_ids": children_ids,
                "context_markdown": context_markdown,
            },
        )

    def log_task_resumed_from_jobs(
        self,
        task_id: str,
        blocking_job_ids: list[str],
        trigger: str,
        triggering_job_id: str | None,
        job_outcomes: dict[str, str],
    ) -> None:
        """Written immediately after try_claim_for_step wins on an in_progress+blocked_on_job
        row. Read by the resume header injector. Spec §5.2, §7.
        """
        self._db.insert_audit_log(
            task_id=task_id,
            agent="orchestrator",
            action="task_resumed_from_jobs",
            payload={
                "blocking_job_ids": blocking_job_ids,
                "trigger": trigger,
                "triggering_job_id": triggering_job_id,
                "job_outcomes": job_outcomes,
            },
        )

    def log_task_resume_skipped(
        self,
        task_id: str,
        reason: str,
        blocked_on_job_ids_raw: str | None = None,
    ) -> None:
        """Diagnostic-only: written when the resume helper returns False with
        reason=empty_job_list (the only audited skip reason). Spec §7.
        """
        payload: dict[str, object] = {"reason": reason}
        if blocked_on_job_ids_raw is not None:
            payload["blocked_on_job_ids_raw"] = blocked_on_job_ids_raw
        self._db.insert_audit_log(
            task_id=task_id,
            agent="orchestrator",
            action="task_resume_skipped",
            payload=payload,
        )

    def log_revisit_of(
        self,
        task_id: str,
        predecessor_root: str,
        flagged: str,
        cascade: list[str],
        prior_status: str,
        founder_note: str | None,
        actor: str = "cli",
    ) -> None:
        """Record on the NEW root that it is a revisit of `predecessor_root`.

        `cascade` is [predecessor_root, ..., flagged] -- the chain the founder
        walked from the flagged task back up to the predecessor root. The
        prompt-injection step in run_step reads this entry to build the
        first-step context header.

        `actor` identifies the surface that triggered the revisit: "cli"
        (HTTP route / happyranch revisit command) or "feishu-reply" (Feishu listener).
        Defaults to "cli" for backward compatibility with existing callers.
        """
        self._db.insert_audit_log(
            task_id=task_id,
            agent="founder",
            action="revisit_of",
            payload={
                "predecessor_root": predecessor_root,
                "flagged": flagged,
                "cascade": cascade,
                "prior_status": prior_status,
                "founder_note": founder_note,
                "actor": actor,
            },
        )

    def log_revisit_spawned(
        self, predecessor_task_id: str, new_root: str,
    ) -> None:
        """Record on the predecessor that it spawned a revisit (observational)."""
        self._db.insert_audit_log(
            task_id=predecessor_task_id,
            agent="founder",
            action="revisit_spawned",
            payload={"new_root": new_root},
        )

    def log_escalation_superseded(
        self,
        predecessor_task_id: str,
        *,
        successor_root: str,
        prior_block_kind: str,
        actor: str,
        founder_note: str | None = None,
        thread_id: str | None = None,
    ) -> None:
        """Record that an escalated or in_progress(delegated) task was auto-resolved to
        SUPERSEDED because a human-authorized continuation
        (`successor_root`) superseded it.

        The `successor_root` citation IS the maker-checker evidence: this
        transition NEVER fires without a concrete successor task_id, which only
        exists because a human (founder `revisit` / founder-or-manager
        thread-dispatch) authorized the continuation. `actor` records which
        surface triggered it; `thread_id` (set on the thread-dispatch path)
        cites the dispatching thread ruling. THR-018 tier #3, §3a.
        """
        self._db.insert_audit_log(
            task_id=predecessor_task_id,
            agent="founder",
            action="escalation_superseded",
            payload={
                "successor_root": successor_root,
                "prior_block_kind": prior_block_kind,
                "actor": actor,
                "founder_note": founder_note,
                "thread_id": thread_id,
            },
        )

    def log_artifact_put(self, name: str, size_bytes: int, agent: str) -> None:
        self._db.insert_audit_log(
            task_id=f"artifact:{name}",  # namespaced to avoid collision with TASK-/JOB- ids in get_audit_logs(task_id)
            agent=agent,
            action="artifact_put",
            payload={"name": name, "size_bytes": size_bytes},
        )

    def log_artifact_delete(self, name: str, agent: str) -> None:
        # Mirrors log_artifact_put's row shape: same artifact:<name> namespacing
        # so deletes never collide with TASK-/JOB- ids in get_audit_logs(task_id).
        self._db.insert_audit_log(
            task_id=f"artifact:{name}",
            agent=agent,
            action="artifact_delete",
            payload={"name": name},
        )

    # ── Task attachment audit actions (THR-109) ──────────────────────────────

    def log_task_attachment_uploaded(
        self, storage_key: str, display_name: str,
        size_bytes: int, content_type: str | None, agent: str,
    ) -> None:
        """Record a private-store upload (pre-task-creation).

        Uses a namespaced task_id so rows don't collide with TASK-/JOB- ids
        in get_audit_logs(task_id). Payload carries only metadata — never
        attachment bytes or local file paths.
        """
        self._db.insert_audit_log(
            task_id=f"task-attachment:{storage_key}",
            agent=agent,
            action="task_attachment_uploaded",
            payload={
                "storage_key": storage_key,
                "display_name": display_name,
                "size_bytes": size_bytes,
                "content_type": content_type,
            },
        )

    def log_task_attachment_added(
        self, task_id: str, storage_key: str, display_name: str,
        content_type: str | None, uploaded_by: str,
    ) -> None:
        """Record that an attachment was linked to a task on creation.

        Uses the concrete task_id — the ordinary primary use of the column.
        Payload carries metadata only; never bytes or local paths.
        """
        self._db.insert_audit_log(
            task_id=task_id,
            agent=uploaded_by,
            action="task_attachment_added",
            payload={
                "storage_key": storage_key,
                "display_name": display_name,
                "content_type": content_type,
            },
        )

    def log_task_attachment_materialized(
        self, task_id: str, session_id: str,
        count: int, materialized_keys: list[str],
    ) -> None:
        """Record that attachments were materialized for a session spawn.

        Uses the spawning task's id. Payload carries the session id and
        storage_key list — never bytes or local file paths.
        """
        self._db.insert_audit_log(
            task_id=task_id,
            agent="orchestrator",
            action="task_attachment_materialized",
            payload={
                "session_id": session_id,
                "count": count,
                "storage_keys": materialized_keys,
            },
        )

    def log_agent_managed(
        self,
        *,
        scope_id: str,
        action: str,
        name: str,
        source: str,
        actor: str,
    ) -> None:
        """Record a successful manage-agent call.

        `scope_id` populates `audit_log.task_id` (the generic scope column
        described at line 173): TASK-xxx for task-path calls.
        `source` is 'task' for quick filtering.
        `actor` is the manager_name resolved by the team-manager auth helper.
        """
        self._db.insert_audit_log(
            task_id=scope_id,
            agent=actor,
            action="agent_managed",
            payload={
                "action": action,
                "name": name,
                "source": source,
            },
        )

    def log_executor_registered(
        self,
        *,
        profile_name: str,
        command: str,
        argv_template: list[str],
        adapter: str,
        actor: str = "founder",
    ) -> None:
        """Record a successful runtime-level executor registration.

        THR-088 Slice B: runtime-level registration is org-agnostic, so it writes
        to a dedicated runtime audit database (not a per-org db). Uses the
        scope-prefix convention for ``task_id`` analogous to ``config:<section>``
        (THR-035 / TASK-967).

        Row shape:
          task_id = "executor:<profile_name>"
          action  = "executor_registered"
          payload = {command, argv_template, adapter}
        """
        self._db.insert_audit_log(
            task_id=f"executor:{profile_name}",
            agent=actor,
            action="executor_registered",
            payload={
                "command": command,
                "argv_template": [str(e) for e in argv_template],
                "adapter": adapter,
            },
        )

    def log_executor_removed(
        self,
        *,
        profile_name: str,
        command: str,
        argv_template: list[str],
        adapter: str,
        actor: str = "founder",
    ) -> None:
        """Record a successful runtime-level executor profile removal.

        THR-107 S4a: mirrors ``log_executor_registered`` — same dedicated
        runtime audit database, same scope-prefix ``task_id`` convention,
        same payload keys (the payload captures the REMOVED definition);
        only the action verb differs.

        Row shape:
          task_id = "executor:<profile_name>"
          action  = "executor_removed"
          payload = {command, argv_template, adapter}
        """
        self._db.insert_audit_log(
            task_id=f"executor:{profile_name}",
            agent=actor,
            action="executor_removed",
            payload={
                "command": command,
                "argv_template": [str(e) for e in argv_template],
                "adapter": adapter,
            },
        )

    def log_learning_added(
        self,
        *,
        agent: str,
        id: str,
        slug: str,
        topic: str,
        tags: list[str],
        source_task: str | None,
    ) -> None:
        self._db.insert_audit_log(
            task_id=source_task if source_task is not None else f"AGENT-{agent}",
            agent=agent,
            action="learning_added",
            payload={"id": id, "slug": slug, "topic": topic, "tags": tags, "source_task": source_task},
        )

    def log_learning_updated(
        self,
        *,
        agent: str,
        id: str,
        slug_changed: bool,
    ) -> None:
        self._db.insert_audit_log(
            task_id=f"AGENT-{agent}",
            agent=agent,
            action="learning_updated",
            payload={"id": id, "slug_changed": slug_changed},
        )

    def log_learning_promoted(
        self,
        *,
        agent: str,
        id: str,
        kb_slug: str,
    ) -> None:
        self._db.insert_audit_log(
            task_id=f"AGENT-{agent}",
            agent=agent,
            action="learning_promoted",
            payload={"id": id, "kb_slug": kb_slug},
        )

    # THR-032 Phase R — memory event names, emitted FORWARD ONLY. New writes use
    # these; the log_learning_* methods above remain so historical rows stay
    # truthful and any reader can still parse them (§7.2(a) audit immutability).
    # Additive event-name variants only — no column added/altered/dropped, and
    # audit_log.task_id scope-prefix overloading is untouched.
    def log_memory_added(
        self,
        *,
        agent: str,
        id: str,
        slug: str,
        topic: str,
        tags: list[str],
        source_task: str | None,
    ) -> None:
        self._db.insert_audit_log(
            task_id=source_task if source_task is not None else f"AGENT-{agent}",
            agent=agent,
            action="memory_added",
            payload={"id": id, "slug": slug, "topic": topic, "tags": tags, "source_task": source_task},
        )

    def log_memory_updated(
        self,
        *,
        agent: str,
        id: str,
        slug_changed: bool,
    ) -> None:
        self._db.insert_audit_log(
            task_id=f"AGENT-{agent}",
            agent=agent,
            action="memory_updated",
            payload={"id": id, "slug_changed": slug_changed},
        )

    def log_memory_promoted(
        self,
        *,
        agent: str,
        id: str,
        kb_slug: str,
    ) -> None:
        self._db.insert_audit_log(
            task_id=f"AGENT-{agent}",
            agent=agent,
            action="memory_promoted",
            payload={"id": id, "kb_slug": kb_slug},
        )

    def log_memory_digest_impression(
        self,
        *,
        agent: str,
        task_id: str,
        session_id: str,
        digest_ids: list[str],
        budget: int,
    ) -> None:
        """THR-091 Slice 2: log a digest impression at agent spawn.

        Emitted exactly once per non-empty digest injected into an agent
        prompt.  Carries the shown digest's memory IDs plus agent, current
        task ID, and generated session ID.  No digest text, titles,
        directive/full bodies, prompts, or briefs are stored.

        Row shape: ``task_id=<task_id>``, ``action="memory_digest_impression"``,
        ``payload`` includes ``agent``, ``session_id``, ``digest_ids``,
        ``digest_count``, ``budget``.
        """
        self._db.insert_audit_log(
            task_id=task_id,
            agent=agent,
            action="memory_digest_impression",
            payload={
                "agent": agent,
                "session_id": session_id,
                "digest_ids": digest_ids,
                "digest_count": len(digest_ids),
                "budget": budget,
            },
        )

    def log_memory_search(
        self,
        *,
        agent: str,
        session_id: str | None,
        memory_ids: list[str],
        hit_count: int,
        kb_hit_count: int,
        task_id: str | None = None,
    ) -> None:
        """THR-091 Slice 2: log a privacy-preserving search result telemetry event.

        Stores only returned memory IDs (and minimal counts/correlation/source
        metadata).  NEVER persists raw query text, query tokens/hashes, snippets,
        titles, bodies, KB body content, or prompt text.  KB hits are excluded
        from ``memory_ids``; their count is recorded as ``kb_hit_count``.

        When ``task_id`` is provided (validated server-side via SessionTracker),
        the row's ``task_id`` column is the actual task ID for trusted
        correlation.  Otherwise ``task_id="AGENT-{agent}"`` (legacy).

        Row shape: ``task_id=<task_id or AGENT-{agent}>``, ``action="memory_search"``,
        ``payload`` includes ``agent``, ``session_id`` (when available),
        ``memory_ids``, ``hit_count``, ``kb_hit_count``, and ``task_id``
        (when available).
        """
        payload: dict = {
            "agent": agent,
            "memory_ids": memory_ids,
            "hit_count": hit_count,
            "kb_hit_count": kb_hit_count,
        }
        if session_id is not None:
            payload["session_id"] = session_id
        if task_id is not None:
            payload["task_id"] = task_id
        row_task_id = task_id if task_id is not None else f"AGENT-{agent}"
        self._db.insert_audit_log(
            task_id=row_task_id,
            agent=agent,
            action="memory_search",
            payload=payload,
        )

    def log_memory_read(
        self,
        *,
        agent: str,
        id: str,
        slug: str,
        session_id: str | None = None,
        source: str | None = None,
        task_id: str | None = None,
    ) -> None:
        """Log a memory read with optional same-session attribution.

        THR-091 Slice 2: adds optional ``session_id``, ``source``, and
        ``task_id`` metadata.  ``source`` is one of ``digest``, ``search``,
        or ``explicit_or_other``.  When ``source`` is None and ``session_id``
        is provided, auto-resolve attribution via
        ``_resolve_read_source`` which now also validates ``task_id`` when
        known (server-side SessionTracker-confirmed).

        Legacy rows without these fields remain compatible.
        task_id column stays ``AGENT-{agent}`` (legacy scope convention).
        When a validated ``task_id`` is supplied it is stored in payload.

        Row shape: ``task_id="AGENT-{agent}"``, ``action="memory_read"``,
        ``payload`` includes ``id``, ``slug``, and (when available)
        ``source``, ``session_id``, ``task_id``.
        """
        resolved_source = source
        if resolved_source is None and session_id is not None:
            resolved_source = self._resolve_read_source(
                agent=agent, id=id, session_id=session_id, task_id=task_id,
            )
        payload: dict = {"id": id, "slug": slug}
        if resolved_source is not None:
            payload["source"] = resolved_source
        if session_id is not None:
            payload["session_id"] = session_id
        if task_id is not None:
            payload["task_id"] = task_id
        self._db.insert_audit_log(
            task_id=f"AGENT-{agent}",
            agent=agent,
            action="memory_read",
            payload=payload,
        )

    def _resolve_read_source(
        self,
        *,
        agent: str,
        id: str,
        session_id: str,
        task_id: str | None = None,
    ) -> str:
        """Resolve the source of a memory read for same-session attribution.

        When ``task_id`` is provided (server-side SessionTracker-validated),
        only matches events that share the same task_id — never cross-credits
        a different task's digest or search, even for the same agent+session.

        Returns ``digest`` when the id is in the session+task's digest impression;
        ``search`` when in the session+task's search result ids; otherwise
        ``explicit_or_other``.
        """
        import json
        # Check digest impression for this session (and task when known).
        # Digest impressions already store the actual task_id in the task_id
        # column, so we filter by it directly in SQL.
        if task_id is not None:
            rows = self._db.fetch_all_readonly(
                "SELECT payload FROM audit_log"
                " WHERE agent = ? AND action = 'memory_digest_impression'"
                " AND task_id = ?",
                (agent, task_id),
            )
        else:
            rows = self._db.fetch_all_readonly(
                "SELECT payload FROM audit_log"
                " WHERE agent = ? AND action = 'memory_digest_impression'",
                (agent,),
            )
        for row in rows:
            try:
                payload = json.loads(row["payload"])
            except (json.JSONDecodeError, TypeError):
                continue
            if payload.get("session_id") == session_id:
                digest_ids = payload.get("digest_ids", [])
                if id in digest_ids:
                    return "digest"
        # Check search events for this session (and task when known).
        # Search events with validated task_id store it in the task_id column;
        # unvalidated ones use "AGENT-{agent}".  Filter by task_id when known.
        if task_id is not None:
            rows = self._db.fetch_all_readonly(
                "SELECT payload FROM audit_log"
                " WHERE agent = ? AND action = 'memory_search'"
                " AND task_id = ?",
                (agent, task_id),
            )
        else:
            rows = self._db.fetch_all_readonly(
                "SELECT payload FROM audit_log"
                " WHERE agent = ? AND action = 'memory_search'",
                (agent,),
            )
        for row in rows:
            try:
                payload = json.loads(row["payload"])
            except (json.JSONDecodeError, TypeError):
                continue
            if payload.get("session_id") == session_id:
                memory_ids = payload.get("memory_ids", [])
                if id in memory_ids:
                    return "search"
        return "explicit_or_other"

    def log_memory_lifecycle_changed(
        self,
        *,
        agent: str,
        id: str,
        from_lifecycle: str,
        to_lifecycle: str,
        reason: str,
        source: str = "manual",
    ) -> None:
        """THR-032 P3a: audit a lifecycle transition.

        Row shape: ``task_id="AGENT-{agent}"``, ``action="memory_lifecycle_changed"``,
        ``payload`` includes id, from_lifecycle, to_lifecycle, reason, source.
        No column added; no historical row rewritten.
        """
        self._db.insert_audit_log(
            task_id=f"AGENT-{agent}",
            agent=agent,
            action="memory_lifecycle_changed",
            payload={
                "id": id,
                "from_lifecycle": from_lifecycle,
                "to_lifecycle": to_lifecycle,
                "reason": reason,
                "source": source,
            },
        )

    # THR-091 Slice 2: telemetry primitives — digest impression, memory_read
    # attribution, search telemetry, and the operator-facing telemetry report
    # (replacing the earlier WS-C pull-through stub).

    @staticmethod
    def _extract_digest_ids(digest_text: str) -> list[str]:
        """Extract MEM-NNN ids from a digest string.

        Matches markers like ``- `MEM-139` — ...`` or ``**Directive:** ``MEM-001`` ...``
        """
        import re
        ids = re.findall(r'MEM-\d{3,}', digest_text)
        # Preserve order, deduplicate
        seen: set[str] = set()
        result: list[str] = []
        for mid in ids:
            if mid not in seen:
                seen.add(mid)
                result.append(mid)
        return result

    def compute_memory_pull_through(
        self,
        *,
        agent: str,
        digest_ids: set[str],
    ) -> dict:
        """Compute pull-through: of digest pointers, how many were read.

        Returns a dict with:
        - digest_count: total pointers in the digest
        - read_count: how many were read at least once
        - pull_through: fraction read (0.0–1.0)
        - read_ids: sorted list of ids that were read
        - unread_ids: sorted list of ids in digest but never read

        THR-091 WS-C: retained for backward-compatible per-agent analysis.
        """
        if not digest_ids:
            return {
                "digest_count": 0,
                "read_count": 0,
                "pull_through": 0.0,
                "read_ids": [],
                "unread_ids": [],
            }
        # Query memory_read events for this agent and extract payload.id
        rows = self._db.fetch_all_readonly(
            "SELECT payload FROM audit_log WHERE agent = ? AND action = 'memory_read'",
            (agent,),
        )
        import json
        read_set: set[str] = set()
        for row in rows:
            try:
                payload = json.loads(row["payload"])
            except (json.JSONDecodeError, TypeError):
                continue
            mid = payload.get("id")
            if mid and mid in digest_ids:
                read_set.add(mid)
        digest_count = len(digest_ids)
        read_count = len(read_set)
        pull_through = read_count / digest_count if digest_count > 0 else 0.0
        return {
            "digest_count": digest_count,
            "read_count": read_count,
            "pull_through": pull_through,
            "read_ids": sorted(read_set),
            "unread_ids": sorted(digest_ids - read_set),
        }

    def compute_memory_telemetry_report(
        self,
        *,
        agent_role_map: dict[str, str] | None = None,
        current_time: "datetime | None" = None,
    ) -> dict:
        """THR-091 Slice 2: operator-facing telemetry report.

        Computes aggregate and per-role telemetry from audit_log rows.
        Returns a structured dict with:
        - observation_period: pre-registration + threshold status
        - aggregate: across all agents
        - by_role: per-role breakdown (when agent_role_map provided)
        - decision: activation_loss | retrieval_loss | no_demonstrated_problem
                    | insufficient_sample

        Pre-registration:
        - Observation starts only after the first production
          ``memory_digest_impression`` row.
        - Requires 14 complete calendar days AND at least 500 sessions with
          non-empty correlated digests.
        - Decision rules per the THR-091 Slice 2 spec.

        Args:
            agent_role_map: authoritative agent→role mapping from /agents.
                When None, roles are reported as unavailable.
            current_time: UTC datetime for time-based threshold evaluation.
                When None (production), uses datetime.now(timezone.utc).
                Tests seed this to simulate 14+ days elapsed.
        """
        import json
        from datetime import datetime, timedelta, timezone

        if current_time is None:
            current_time = datetime.now(timezone.utc)

        # Collect digest impressions with timestamps.
        # Include task_id column (impressions store the actual task_id there,
        # unlike legacy memory_read rows which use AGENT-{agent}).
        rows = self._db.fetch_all_readonly(
            "SELECT timestamp, agent, task_id, payload FROM audit_log"
            " WHERE action = 'memory_digest_impression'"
            " ORDER BY timestamp ASC",
            (),
        )
        if not rows:
            return {
                "observation_period": {
                    "status": "insufficient_sample",
                    "reason": "No memory_digest_impression rows found —"
                              " observation has not started.",
                    "trigger": "First production memory_digest_impression row"
                               " emitted by the deployed revision.",
                },
                "aggregate": {},
                "by_role": {},
                "decision": "insufficient_sample",
            }

        # Parse impressions
        impressions: list[dict] = []
        for row in rows:
            try:
                payload = json.loads(row["payload"])
            except (json.JSONDecodeError, TypeError):
                continue
            session_id = payload.get("session_id")
            digest_ids = payload.get("digest_ids", [])
            agent = payload.get("agent", row["agent"] if "agent" in row.keys() else "")
            row_task_id = row["task_id"] if "task_id" in row.keys() else ""
            if session_id and digest_ids:
                impressions.append({
                    "session_id": session_id,
                    "digest_ids": digest_ids,
                    "agent": agent,
                    "task_id": row_task_id,
                    "timestamp": row["timestamp"],
                })

        if not impressions:
            return {
                "observation_period": {
                    "status": "insufficient_sample",
                    "reason": "No non-empty correlated digest impressions found.",
                    "trigger": "First production memory_digest_impression row"
                               " emitted by the deployed revision.",
                },
                "aggregate": {},
                "by_role": {},
                "decision": "insufficient_sample",
            }

        # Determine observation start (first impression timestamp)
        first_ts_str = impressions[0]["timestamp"]
        try:
            first_ts = datetime.fromisoformat(
                first_ts_str.replace("Z", "+00:00")
            )
        except (ValueError, TypeError):
            first_ts = datetime.now(timezone.utc)

        days_elapsed = (current_time - first_ts).days

        # Unique sessions with non-empty digests
        all_sessions: set[str] = set()
        per_agent_sessions: dict[str, set[str]] = {}
        agent_roles: dict[str, str] = {}
        for imp in impressions:
            sid = imp["session_id"]
            all_sessions.add(sid)
            agent = imp["agent"]
            if agent not in per_agent_sessions:
                per_agent_sessions[agent] = set()
            per_agent_sessions[agent].add(sid)
            # Determine role from authoritative map.
            # Never fall back to agent name — unknown roles are excluded
            # from role decisions rather than guessed.
            if agent_role_map is not None and agent not in agent_roles:
                role = agent_role_map.get(agent)
                if role is not None:
                    agent_roles[agent] = role

        total_sessions = len(all_sessions)

        # Pre-registration thresholds
        met_days = days_elapsed >= 14
        met_sessions = total_sessions >= 500
        thresholds_met = met_days and met_sessions

        observation = {
            "trigger": "First production memory_digest_impression row emitted"
                       " by the deployed revision.",
            "first_impression_at": first_ts_str,
            "days_elapsed": days_elapsed,
            "required_days": 14,
            "total_correlated_sessions": total_sessions,
            "required_sessions": 500,
            "thresholds_met": thresholds_met,
            "days_met": met_days,
            "sessions_met": met_sessions,
        }

        if not thresholds_met:
            return {
                "observation_period": {
                    **observation,
                    "status": "insufficient_sample",
                    "reason": (
                        f"Need 14 days (have {days_elapsed}) AND"
                        f" 500 sessions (have {total_sessions})."
                    ),
                },
                "aggregate": {},
                "by_role": {},
                "decision": "insufficient_sample",
            }

        # Collect memory_read events for pull-through.
        # Include agent and task_id columns so we can verify read rows
        # match the impression's (agent, task_id, session_id) tuple.
        read_rows = self._db.fetch_all_readonly(
            "SELECT agent, task_id, payload FROM audit_log"
            " WHERE action = 'memory_read'",
            (),
        )

        # Compute per-session pull-through.
        # Build a validated (agent, task_id, session_id) tuple map from
        # trusted impressions so reads can be verified against it.
        session_digest_ids: dict[str, set[str]] = {}
        session_agent: dict[str, str] = {}
        validated_impression_tuples: dict[str, tuple[str, str]] = {}  # sid→(agent, task_id)
        for imp in impressions:
            sid = imp["session_id"]
            session_digest_ids[sid] = set(imp["digest_ids"])
            session_agent[sid] = imp["agent"]
            imp_task_id = imp.get("task_id", "")
            if imp_task_id:
                validated_impression_tuples[sid] = (imp["agent"], imp_task_id)
            session_agent[sid] = imp["agent"]

        # Which digest IDs were read in each session.
        # Only include correlated reads (have both session_id and task_id).
        # Legacy/untrusted reads excluded from pull-through and search-absent
        # denominators.
        session_read_ids: dict[str, set[str]] = {}
        search_reads: list[dict] = []
        digest_reads: list[dict] = []
        explicit_reads: list[dict] = []
        untrusted_reads: list[dict] = []
        for row in read_rows:
            try:
                payload = json.loads(row["payload"])
            except (json.JSONDecodeError, TypeError):
                continue
            mid = payload.get("id")
            source = payload.get("source", "explicit_or_other")
            rsid = payload.get("session_id")
            rtask_id = payload.get("task_id")
            # Verify that the read's (agent, task_id, session_id) tuple
            # matches the impression's validated tuple.  Rows that fail
            # this check are excluded from pull-through and search-absent
            # denominators rather than treated as matches.
            if rsid and rtask_id:
                validated_tuple = validated_impression_tuples.get(rsid)
                if validated_tuple is not None:
                    imp_agent, imp_task_id = validated_tuple
                    row_agent = row["agent"] if "agent" in row.keys() else ""
                    if row_agent == imp_agent and rtask_id == imp_task_id:
                        # Tuple-verified read — include in telemetry
                        if rsid not in session_read_ids:
                            session_read_ids[rsid] = set()
                        session_read_ids[rsid].add(mid)
                        entry = {"id": mid, "source": source,
                                 "session_id": rsid, "task_id": rtask_id,
                                 "agent": row_agent}
                        if source == "digest":
                            digest_reads.append(entry)
                        elif source == "search":
                            search_reads.append(entry)
                        else:
                            explicit_reads.append(entry)
                        continue
                # Tuple mismatch or unknown session — treat as untrusted
                untrusted_reads.append({"id": mid, "source": source,
                                       "session_id": rsid, "task_id": rtask_id})
            else:
                untrusted_reads.append({"id": mid, "source": source, "session_id": rsid})

        # Build per-role aggregates (roles with >=30 correlated digest sessions).
        # Only agents with an authoritative role from agent_role_map are included
        # in role-level decisions.  Agents without a known role are reported in
        # the by_role output as ineligible and excluded from role decisions.
        roles_unavailable = agent_role_map is None
        role_data: dict[str, dict] = {}
        unknown_agents: list[str] = []
        for agent, sessions in per_agent_sessions.items():
            role = agent_roles.get(agent)
            if role is None:
                unknown_agents.append(agent)
                continue
            if role not in role_data:
                role_data[role] = {
                    "agents": [],
                    "sessions": set(),
                }
            role_data[role]["agents"].append(agent)
            role_data[role]["sessions"] |= sessions

        # Aggregate pull-through — per-session denominators (not globally
        # unioned).  Each session's unique shown IDs and read-in-session IDs
        # are summed across all sessions.
        total_shown = 0
        total_read_in_session = 0
        for sid, d_ids in session_digest_ids.items():
            shown = len(d_ids)
            reads = session_read_ids.get(sid, set())
            read_in_this_session = len(reads & d_ids)
            total_shown += shown
            total_read_in_session += read_in_this_session

        agg_pull_through = (
            total_read_in_session / total_shown
            if total_shown else 0.0
        )

        # Search reads with IDs absent from session digest.
        # Only correlated read rows (have both session_id and task_id) are
        # evaluated.  Legacy/untrusted/unmatched rows are excluded rather than
        # treated as search misses.
        search_total = len(search_reads)
        search_absent_count = 0
        for sr in search_reads:
            sid = sr.get("session_id")
            if sid and sid in session_digest_ids:
                if sr["id"] not in session_digest_ids[sid]:
                    search_absent_count += 1
            # No else — untrusted rows excluded, not counted as absent

        search_absent_frac = (
            search_absent_count / search_total if search_total > 0 else 0.0
        )

        # Per-role computation
        by_role: dict[str, dict] = {}
        eligible_roles: list[str] = []
        for role, data in role_data.items():
            role_sessions = data["sessions"]
            role_session_count = len(role_sessions)
            if role_session_count < 30:
                by_role[role] = {
                    "correlated_sessions": role_session_count,
                    "eligible": False,
                    "reason": f"Need >=30 sessions (have {role_session_count})",
                }
                continue
            eligible_roles.append(role)

            # Role-specific per-session shown IDs (not globally unioned)
            role_total_shown = 0
            role_total_read_in_session = 0
            for sid in role_sessions:
                if sid in session_digest_ids:
                    role_total_shown += len(session_digest_ids[sid])
                    reads = session_read_ids.get(sid, set())
                    role_total_read_in_session += len(
                        reads & session_digest_ids[sid]
                    )

            role_pull_through = (
                role_total_read_in_session / role_total_shown
                if role_total_shown else 0.0
            )

            # Role-specific search stats — only correlated reads
            role_search_total = 0
            role_search_absent = 0
            for sr in search_reads:
                sid = sr.get("session_id")
                if sid and sid in role_sessions:
                    role_search_total += 1
                    if sid in session_digest_ids:
                        if sr["id"] not in session_digest_ids[sid]:
                            role_search_absent += 1
                    # No else — untrusted rows excluded

            role_search_absent_frac = (
                role_search_absent / role_search_total
                if role_search_total > 0 else 0.0
            )

            role_search_threshold_met = role_search_total >= 30

            by_role[role] = {
                "correlated_sessions": role_session_count,
                "eligible": True,
                "digest_pull_through": round(role_pull_through, 4),
                "unique_digest_ids_shown": role_total_shown,
                "unique_digest_ids_read_same_session": role_total_read_in_session,
                "search_sourced_reads": role_search_total,
                "search_sourced_absent_from_digest": role_search_absent,
                "search_absent_fraction": round(role_search_absent_frac, 4),
                "search_threshold_met": role_search_threshold_met,
            }

        # Decision logic
        # Rule 1: Activation loss — pointer pull-through <10% aggregate AND
        #          majority of eligible roles <10%
        # Rule 2: Retrieval loss — search-sourced reads of IDs absent from
        #          session digest >25% both aggregate AND in any eligible role
        #          with >=30 such reads
        # Rule 3: Otherwise no demonstrated problem

        decision: str
        decision_detail: str

        if agg_pull_through < 0.10:
            eligible_roles_below_10 = sum(
                1 for r in eligible_roles
                if by_role[r]["digest_pull_through"] < 0.10
            )
            if eligible_roles and eligible_roles_below_10 > len(eligible_roles) / 2:
                decision = "activation_loss"
                decision_detail = (
                    "Aggregate pointer-level same-session pull-through"
                    f" ({agg_pull_through:.2%}) < 10% AND majority of eligible"
                    f" roles ({eligible_roles_below_10}/{len(eligible_roles)}) < 10%."
                    " Next step: provenance/push tuning only, no aliases/embeddings."
                )
            elif not eligible_roles:
                decision = "no_demonstrated_problem"
                decision_detail = (
                    f"Aggregate pull-through ({agg_pull_through:.2%}) < 10%"
                    " but no eligible roles to confirm (role analysis"
                    " unavailable). Do not tune ranking/push."
                )
            else:
                # Aggregate <10% but majority of roles are NOT <10% => no global
                # remedy; fall through to retrieval check
                decision = "no_demonstrated_problem"
                decision_detail = (
                    f"Aggregate pull-through ({agg_pull_through:.2%}) < 10%"
                    " but majority of eligible roles are NOT <10%."
                    " No global remedy applied. Contradictory role visibility"
                    " preserved."
                )
        elif search_absent_frac > 0.25:
            # Check if any eligible role with >=30 search reads also >25%
            retrieval_roles = [
                r for r in eligible_roles
                if by_role[r]["search_threshold_met"]
                and by_role[r]["search_absent_fraction"] > 0.25
            ]
            if retrieval_roles:
                decision = "retrieval_loss"
                decision_detail = (
                    "Search reads of IDs absent from digest >25% in"
                    f" aggregate ({search_absent_frac:.2%}) AND in eligible"
                    f" role(s): {retrieval_roles}."
                    " Next step: alias/synonym-tag evaluation first."
                    " Embeddings remain founder-gated."
                )
            else:
                decision = "no_demonstrated_problem"
                decision_detail = (
                    "Search absent fraction >25% aggregate"
                    f" ({search_absent_frac:.2%}) but no eligible role"
                    " with >=30 search reads exceeds 25%."
                    " No demonstrated retrieval problem."
                )
        else:
            decision = "no_demonstrated_problem"
            decision_detail = (
                "Aggregate pull-through >=10% and search absent"
                " fraction <=25%. No demonstrated activation or retrieval"
                " problem. Do not tune ranking/push."
            )

        aggregate = {
            "correlated_sessions": total_sessions,
            "unique_digest_ids_shown": total_shown,
            "unique_digest_ids_read_same_session": total_read_in_session,
            "digest_pull_through": round(agg_pull_through, 4),
            "search_sourced_reads": search_total,
            "search_sourced_absent_from_digest": search_absent_count,
            "search_absent_fraction": round(search_absent_frac, 4),
            "digest_sourced_reads": len(digest_reads),
            "explicit_or_other_sourced_reads": len(explicit_reads),
            "untrusted_uncorrelated_reads": len(untrusted_reads),
        }

        result: dict = {
            "observation_period": {
                **observation,
                "status": "thresholds_met",
            },
            "aggregate": aggregate,
            "by_role": by_role,
            "decision": decision,
            "decision_detail": decision_detail,
        }
        if roles_unavailable:
            result["roles_warning"] = (
                "Authoritative agent roles unavailable — /agents surface"
                " could not be read. Per-role analysis excluded."
            )
        elif unknown_agents:
            result["roles_warning"] = (
                f"{len(unknown_agents)} agent(s) have unknown roles"
                f" and are excluded from role decisions: {unknown_agents}"
            )
        return result

    # NOTE: audit_log.task_id doubles as a generic scope id. Thread events store
    # the thread id (THR-NNN) in that column, matching the talk_* pattern above.

    def log_thread_started(
        self,
        thread_id: str,
        *,
        subject: str,
        initial_recipients: list[str],
        forwarded_from_id: str | None,
        composed_by: str = "founder",
        composed_from_task_id: str | None = None,
        composed_from_dream_id: str | None = None,
    ) -> None:
        payload: dict[str, object] = {
            "subject": subject,
            "initial_recipients": initial_recipients,
            "forwarded_from_id": forwarded_from_id,
            "composed_by": composed_by,
            "composed_from_task_id": composed_from_task_id,
            "composed_from_dream_id": composed_from_dream_id,
        }
        self._db.insert_audit_log(
            task_id=thread_id,
            agent=composed_by,
            action="thread_started",
            payload=payload,
        )

    def log_thread_message_sent(
        self,
        thread_id: str,
        *,
        seq: int,
        speaker: str,
        kind: str,
        attachment_names: list[str] | None = None,
    ) -> None:
        payload: dict[str, object] = {"seq": seq, "kind": kind}
        if attachment_names:
            payload["attachment_count"] = len(attachment_names)
            payload["attachment_names"] = attachment_names
        self._db.insert_audit_log(
            task_id=thread_id,
            agent=speaker,
            action="thread_message_sent",
            payload=payload,
        )

    def log_thread_decline_consumed(
        self,
        thread_id: str,
        *,
        agent_name: str,
        reason: str | None = None,
    ) -> None:
        payload: dict[str, object] = {"agent_name": agent_name}
        if reason:
            payload["reason"] = reason
        self._db.insert_audit_log(
            task_id=thread_id,
            agent=agent_name,
            action="thread_decline_consumed",
            payload=payload,
        )

    def log_thread_participant_added(
        self,
        thread_id: str,
        *,
        agent_name: str,
        added_by: str,
    ) -> None:
        self._db.insert_audit_log(
            task_id=thread_id,
            agent=added_by,
            action="thread_participant_added",
            payload={"agent_name": agent_name, "added_by": added_by},
        )

    def log_thread_participant_removed(
        self,
        thread_id: str,
        *,
        agent_name: str,
        removed_by: str,
    ) -> None:
        self._db.insert_audit_log(
            task_id=thread_id,
            agent=removed_by,
            action="thread_participant_removed",
            payload={"agent_name": agent_name, "removed_by": removed_by},
        )

    def log_thread_dispatch(
        self,
        thread_id: str,
        *,
        task_id: str,
        dispatcher: str,
        target_agent: str,
        team: str,
    ) -> None:
        self._db.insert_audit_log(
            task_id=thread_id,
            agent=dispatcher,
            action="thread_dispatch",
            payload={
                "task_id": task_id,
                "dispatcher": dispatcher,
                "target_agent": target_agent,
                "team": team,
            },
        )

    def log_agent_session_reused(
        self,
        thread_id: str,
        *,
        agent_name: str,
        executor: str,
        agent_session_id: str,
        triggering_seq: int,
    ) -> None:
        """Informational: a thread turn successfully resumed an agent session."""
        self._db.insert_audit_log(
            task_id=thread_id,
            agent=agent_name,
            action="agent_session_reused",
            payload={
                "executor": executor,
                "agent_session_id": agent_session_id,
                "triggering_seq": triggering_seq,
            },
        )

    def log_agent_session_evicted_fallback(
        self,
        thread_id: str,
        *,
        agent_name: str,
        executor: str,
        stale_session_id: str,
        error: str,
    ) -> None:
        """Fires when a resume reported session-not-found and we rebuilt a fresh
        full-context session. Watch frequency: high rates mean the agent CLI's
        local session TTL is shorter than our typical inter-turn gap."""
        self._db.insert_audit_log(
            task_id=thread_id,
            agent=agent_name,
            action="agent_session_evicted_fallback",
            payload={
                "executor": executor,
                "stale_session_id": stale_session_id,
                "error": error[:500],
            },
        )

    def log_thread_task_followup_enqueued(
        self,
        thread_id: str,
        *,
        original_task_id: str,
        terminal_task_id: str,
        dispatcher: str,
        invocation_token: str,
    ) -> None:
        self._db.insert_audit_log(
            task_id=terminal_task_id,
            agent=dispatcher,
            action="thread_task_followup_enqueued",
            payload={
                "thread_id": thread_id,
                "original_task_id": original_task_id,
                "dispatcher": dispatcher,
                "invocation_token_prefix": invocation_token[:8],
            },
        )

    def log_thread_followup_skipped(
        self,
        thread_id: str,
        *,
        original_task_id: str,
        terminal_task_id: str,
        reason: str,
        **extra,
    ) -> None:
        self._db.insert_audit_log(
            task_id=terminal_task_id,
            agent="orchestrator",
            action="thread_followup_skipped",
            payload={
                "thread_id": thread_id,
                "original_task_id": original_task_id,
                "reason": reason,
                **extra,
            },
        )

    def log_thread_turn_cap_auto_extended(
        self,
        thread_id: str,
        *,
        original_task_id: str,
        reason: str,
        new_cap: int,
    ) -> None:
        self._db.insert_audit_log(
            task_id=original_task_id,
            agent="orchestrator",
            action="thread_turn_cap_auto_extended",
            payload={
                "thread_id": thread_id,
                "reason": reason,
                "new_cap": new_cap,
            },
        )

    def log_thread_archived(
        self,
        thread_id: str,
        *,
        turns_used: int,
    ) -> None:
        self._db.insert_audit_log(
            task_id=thread_id,
            agent="founder",
            action="thread_archived",
            payload={"turns_used": turns_used},
        )

    def log_thread_renamed(
        self,
        thread_id: str,
        *,
        old_subject: str,
        new_subject: str,
        actor: str = "founder",
    ) -> None:
        """Record a founder rename (THR-209).

        Follows the existing thread-scope audit convention
        (``audit_log.task_id`` = THR-* id). This is an audit row only — it
        never appears as a thread message and changes no activity timestamps.
        """
        self._db.insert_audit_log(
            task_id=thread_id,
            agent=actor,
            action="thread_renamed",
            payload={"old_subject": old_subject, "new_subject": new_subject},
        )

    def log_thread_pin_state_changed(
        self,
        thread_id: str,
        *,
        pinned: bool,
        actor: str = "founder",
    ) -> None:
        """Record a founder pin/unpin (THR-209).

        Audit-only presentation-state write: emits no thread message,
        notification, or activity-timestamp change. Action is
        ``thread_pinned`` or ``thread_unpinned``.
        """
        self._db.insert_audit_log(
            task_id=thread_id,
            agent=actor,
            action="thread_pinned" if pinned else "thread_unpinned",
            payload={"pinned": pinned},
        )

    def log_thread_resumed(
        self, thread_id: str, *, prior_archived_at: str | None,
    ) -> None:
        self._db.insert_audit_log(
            task_id=thread_id,
            agent="founder",
            action="thread_resumed",
            payload={"prior_archived_at": prior_archived_at},
        )

    def log_thread_invocation_failed(
        self,
        thread_id: str,
        *,
        agent: str,
        token: str,
        purpose: str,
        reason: str,
        kind: str = "thread_invocation_failed",
    ) -> None:
        self._db.insert_audit_log(
            task_id=thread_id,
            agent=agent,
            action=kind,
            payload={"invocation_token": token[:8] + "…", "purpose": purpose, "reason": reason},
        )

    def log_agent_backfilled(
        self,
        *,
        name: str,
        repos_count: int,
        executor: str,
    ) -> None:
        """Record a founder-initiated enrollment backfill.

        Unlike `log_agent_managed`, the actor is 'founder' — this is a one-off
        recovery op for agents bootstrapped outside the enroll→approve flow.
        Scope is the agent name itself (no task context).
        """
        self._db.insert_audit_log(
            task_id=f"AGENT-{name}",
            agent="founder",
            action="agent_backfilled",
            payload={
                "name": name,
                "repos_count": repos_count,
                "executor": executor,
            },
        )

    def log_job_submitted(
        self,
        *,
        task_id: str,
        job_id: str,
        agent: str,
        title: str,
        interpreter: str,
        cwd_hint: str | None,
        byte_size: int,
        line_count: int,
    ) -> None:
        self._db.insert_audit_log(
            task_id=task_id,
            agent=agent,
            action="job_submitted",
            payload={
                "script_request_id": job_id,
                "title": title,
                "interpreter": interpreter,
                "cwd_hint": cwd_hint,
                "byte_size": byte_size,
                "line_count": line_count,
            },
        )

    def log_job_rejected(
        self, *, task_id: str, job_id: str, reviewer: str, reason: str
    ) -> None:
        self._db.insert_audit_log(
            task_id=task_id,
            agent=reviewer,
            action="job_rejected",
            payload={
                "script_request_id": job_id,
                "reviewer": reviewer,
                "reason": reason,
            },
        )

    def log_job_run_started(
        self,
        *,
        task_id: str,
        job_id: str,
        reviewer: str,
        cwd_resolved: str,
        timeout_seconds: int,
        interpreter: str,
    ) -> None:
        self._db.insert_audit_log(
            task_id=task_id,
            agent=reviewer,
            action="job_run_started",
            payload={
                "script_request_id": job_id,
                "reviewer": reviewer,
                "cwd_resolved": cwd_resolved,
                "timeout_seconds": timeout_seconds,
                "interpreter": interpreter,
            },
        )

    def log_job_auto_started(
        self,
        *,
        task_id: str,
        job_id: str,
        agent: str,
        cwd_resolved: str,
        timeout_seconds: int | None,
        interpreter: str,
        persistent: bool,
    ) -> None:
        """Agent-triggered auto-run path (review_required=False).

        Distinct action kind from ``job_run_started`` (founder-triggered) so
        audit log readers can tell apart the two run-initiation paths. The
        ``agent`` here is the requesting worker, not a founder reviewer.
        """
        self._db.insert_audit_log(
            task_id=task_id,
            agent=agent,
            action="job_auto_started",
            payload={
                "script_request_id": job_id,
                "agent": agent,
                "cwd_resolved": cwd_resolved,
                "timeout_seconds": timeout_seconds,
                "interpreter": interpreter,
                "persistent": persistent,
            },
        )

    def log_job_run_completed(
        self,
        *,
        task_id: str,
        job_id: str,
        exit_code: int,
        duration_ms: int,
        stdout_bytes: int,
        stderr_bytes: int,
        truncated_stdout: bool,
        truncated_stderr: bool,
    ) -> None:
        self._db.insert_audit_log(
            task_id=task_id,
            agent="founder",
            action="job_run_completed",
            payload={
                "script_request_id": job_id,
                "exit_code": exit_code,
                "duration_ms": duration_ms,
                "stdout_bytes": stdout_bytes,
                "stderr_bytes": stderr_bytes,
                "truncated_stdout": truncated_stdout,
                "truncated_stderr": truncated_stderr,
            },
        )

    def log_job_run_failed(
        self,
        *,
        task_id: str,
        job_id: str,
        reason: str,
        exit_code: int | None = None,
        duration_ms: int | None = None,
    ) -> None:
        self._db.insert_audit_log(
            task_id=task_id,
            agent="founder",
            action="job_run_failed",
            payload={
                "script_request_id": job_id,
                "exit_code": exit_code,
                "duration_ms": duration_ms,
                "reason": reason,
            },
        )

    def log_job_reconciled_orphaned(
        self,
        *,
        task_id: str,
        job_id: str,
        reason: str,
        evidence: dict,
        before: dict,
        after: dict,
    ) -> None:
        """Durable audit for the never-started pending-job reconciliation seam.

        Records the founder-authorized bookkeeping terminalization of an
        abandoned never-dispatched job (THR-195): the full non-live proof
        evidence, the row's before/after lifecycle state, and the reason, so
        the action is auditable and recovery-aware. ``agent`` is ``"system"``
        — the transition is a system reconciliation, not a founder or agent
        review decision.

        The row is inserted UNCOMMITTED (``insert_audit_log_uncommitted``) and
        participates in the caller's transaction: the caller must commit via
        ``Database.commit()`` — and ``rollback()`` on any failure — so the
        guarded job transition and this audit record are atomic; an audit
        failure can never leave a terminalized job without its durable
        non-live proof.
        """
        self._db.insert_audit_log_uncommitted(
            task_id=task_id,
            agent="system",
            action="job_reconciled_orphaned",
            payload={
                "job_id": job_id,
                "reason": reason,
                "evidence": evidence,
                "before": before,
                "after": after,
            },
        )

    def log_job_stopped(
        self, *, job_id: str, task_id: str, stopped_by: str,
    ) -> None:
        """Caller-triggered stop of a running job.

        ``stopped_by`` is ``"founder"`` (bearer-auth /stop) or ``"agent"``
        (session-bound /stop). The actual terminal transition still flows
        through the runner's normal exit path (``job_run_failed`` with
        ``reason="founder_stop"`` / ``"agent_stop"`` via
        ``_KILL_REASON_OVERRIDE``); this audit row records who pressed the
        button, separately from the runner's own bookkeeping.
        """
        self._db.insert_audit_log(
            task_id=task_id,
            agent=stopped_by,
            action="job_stopped",
            payload={
                "script_request_id": job_id,
                "stopped_by": stopped_by,
            },
        )

    # --- Dream audit events ---

    def log_dream_scheduled(self, dream_id: str, agent: str, *, local_date: str) -> None:
        self._db.insert_audit_log(
            task_id=dream_id, agent=agent,
            action="dream_scheduled",
            payload={"local_date": local_date},
        )

    def log_dream_started(self, dream_id: str, agent: str) -> None:
        self._db.insert_audit_log(
            task_id=dream_id, agent=agent,
            action="dream_started",
            payload={},
        )

    def log_dream_completed(
        self,
        dream_id: str,
        agent: str,
        *,
        new_learnings_count: int,
        kb_candidate_count: int,
        founder_thread_id: str | None,
    ) -> None:
        self._db.insert_audit_log(
            task_id=dream_id, agent=agent,
            action="dream_completed",
            payload={
                "new_learnings_count": new_learnings_count,
                "kb_candidate_count": kb_candidate_count,
                "founder_thread_id": founder_thread_id,
            },
        )

    def log_dream_failed(self, dream_id: str, agent: str, *, reason: str) -> None:
        self._db.insert_audit_log(
            task_id=dream_id, agent=agent,
            action="dream_failed",
            payload={"reason": reason},
        )

    def log_dream_timeout(self, dream_id: str, agent: str, *, reason: str) -> None:
        """Executor timeout for a dream. Distinct from log_dream_failed so the
        timeout failure mode is queryable separately (spec "Audit And Token
        Usage": dream_timeout). Does not advance the successful-dream window."""
        self._db.insert_audit_log(
            task_id=dream_id, agent=agent,
            action="dream_timeout",
            payload={"reason": reason},
        )

    def log_dream_founder_thread_created(
        self, dream_id: str, agent: str, *, founder_thread_id: str,
    ) -> None:
        """A dream completion created a founder-only thread (spec "Audit And
        Token Usage": dream_founder_thread_created). Scoped to the dream id;
        the thread itself separately emits thread_started/thread_message_sent."""
        self._db.insert_audit_log(
            task_id=dream_id, agent=agent,
            action="dream_founder_thread_created",
            payload={"founder_thread_id": founder_thread_id},
        )

    # --- Working Hours ---
    #
    # As with dreams, ``audit_log.task_id`` stores ``WORKHOUR-NNN`` for these
    # rows — the established generic-scope-id overload, NOT a new overload. The
    # spawned root tasks emit their own ordinary ``task_*`` rows; the two
    # streams correlate via the id list on ``work_hour_spawned``.

    def log_work_hour_scheduled(
        self, work_hour_id: str, agent: str, *, local_date: str, slot: str, mode: str,
        dropped: int = 0,
    ) -> None:
        # ``dropped`` records routines discarded past MAX_ROUTINES_PER_WAKE so
        # the cap leaves an audit trail (no silent truncation).
        self._db.insert_audit_log(
            task_id=work_hour_id, agent=agent,
            action="work_hour_scheduled",
            payload={"local_date": local_date, "slot": slot, "mode": mode, "dropped": dropped},
        )

    def log_work_hour_started(self, work_hour_id: str, agent: str) -> None:
        self._db.insert_audit_log(
            task_id=work_hour_id, agent=agent,
            action="work_hour_started",
            payload={},
        )

    def log_work_hour_spawned(
        self, work_hour_id: str, agent: str, *, task_ids: list[str],
    ) -> None:
        """A wake self-dispatched its routine root tasks. Payload carries the
        spawned root task_id list (the forward correlation to the task surface;
        the reverse linkage is ``work_hours.spawned_task_ids``)."""
        self._db.insert_audit_log(
            task_id=work_hour_id, agent=agent,
            action="work_hour_spawned",
            payload={"task_ids": list(task_ids), "spawned_task_count": len(task_ids)},
        )

    def log_work_hour_completed(
        self, work_hour_id: str, agent: str, *, spawned_task_count: int, routine_count: int,
    ) -> None:
        self._db.insert_audit_log(
            task_id=work_hour_id, agent=agent,
            action="work_hour_completed",
            payload={
                "spawned_task_count": spawned_task_count,
                "routine_count": routine_count,
            },
        )

    def log_work_hour_failed(self, work_hour_id: str, agent: str, *, reason: str) -> None:
        self._db.insert_audit_log(
            task_id=work_hour_id, agent=agent,
            action="work_hour_failed",
            payload={"reason": reason},
        )

    def log_work_hour_timeout(self, work_hour_id: str, agent: str, *, reason: str) -> None:
        """Executor timeout for a wake. Distinct from work_hour_failed so the
        timeout failure mode is queryable separately (spec "Audit And Token
        Usage": work_hour_timeout). No tasks are spawned on timeout."""
        self._db.insert_audit_log(
            task_id=work_hour_id, agent=agent,
            action="work_hour_timeout",
            payload={"reason": reason},
        )

    # --- Org config writes (Settings GUI) ---
    #
    # THR-035 / TASK-967. Like artifacts/threads/dreams, ``audit_log.task_id``
    # carries a generic *scope id* here — the namespaced ``config:<section>``
    # value (e.g. ``config:working_hours``). This reuses the established
    # generic-scope-id convention; it does NOT co-opt a real TASK-/JOB- id and
    # adds no column. The before→after snapshot + touched tiers make a
    # config-write fully reconstructable from the audit trail. The scope-prefix
    # convention is a load-bearing invariant — do NOT reinterpret (see the same
    # note over ``_THREAD_SCOPE_PREFIX`` in runtime/daemon/routes/audit.py).

    def log_org_config_write(
        self,
        *,
        section: str,
        tiers: list[str],
        before: dict,
        after: dict,
        actor: str = "founder",
    ) -> None:
        self._db.insert_audit_log(
            task_id=f"config:{section}",  # namespaced to avoid collision with TASK-/TALK-/SR-/JOB- ids in get_audit_logs(task_id)
            agent=actor,
            action="org_config_write",
            payload={
                "section": section,
                "tiers": tiers,
                "before": before,
                "after": after,
            },
        )

    # --- Skills config writes (THR-055) ---
    #
    # Follows the same config:<section> scope-prefix convention as
    # log_org_config_write (THR-035 / TASK-967). Uses ``config:skills``
    # as the namespaced task_id for registry and eligibility policy
    # mutation audit rows — no schema change, no task_id overload.

    def log_skills_config_write(
        self,
        *,
        subsection: str | None = None,
        tiers: list[str],
        before: dict,
        after: dict,
        actor: str = "founder",
    ) -> None:
        scope_id = "config:skills"
        if subsection:
            scope_id = f"config:skills:{subsection}"
        self._db.insert_audit_log(
            task_id=scope_id,
            agent=actor,
            action="skills_config_write",
            payload={
                "subsection": subsection,
                "tiers": tiers,
                "before": before,
                "after": after,
            },
        )
