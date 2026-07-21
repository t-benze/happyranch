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
    ) -> None:
        """Record that an escalated task was resolved.

        THR-080: `actor` records the real agent who resolved (manager/thread-
        originated continue, or founder). `thread_id` cites the dispatching
        thread when the resolution came from the thread surface. Back-compat:
        both params are keyword-only with founder/None defaults.
        """
        payload: dict = {"decision": decision, "rationale": rationale}
        if thread_id is not None:
            payload["thread_id"] = thread_id
        self._db.insert_audit_log(
            task_id=task_id,
            agent=actor,
            action="escalation_resolved",
            payload=payload,
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

    def log_memory_read(
        self,
        *,
        agent: str,
        id: str,
        slug: str,
    ) -> None:
        self._db.insert_audit_log(
            task_id=f"AGENT-{agent}",
            agent=agent,
            action="memory_read",
            payload={"id": id, "slug": slug},
        )

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

    # THR-091 WS-C: pull-through telemetry — of the memory pointers pushed in a
    # session's digest, how many were fetched via memory get. Built from the
    # memory_read audit event + digest pointer primitives.
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
