from __future__ import annotations

from src.infrastructure.database import Database
from src.models import CompletionReport, TokenUsage


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
        self, task_id: str, decision: str, rationale: str
    ) -> None:
        self._db.insert_audit_log(
            task_id=task_id,
            agent="founder",
            action="escalation_resolved",
            payload={"decision": decision, "rationale": rationale},
        )

    def log_escalation_notify_sent(
        self, task_id: str, feishu_message_id: str,
    ) -> None:
        self._db.insert_audit_log(
            task_id=task_id,
            agent="daemon",
            action="escalation_notify_sent",
            payload={"feishu_message_id": feishu_message_id},
        )

    def log_escalation_notify_failed(self, task_id: str, error: str) -> None:
        self._db.insert_audit_log(
            task_id=task_id,
            agent="daemon",
            action="escalation_notify_failed",
            payload={"error": error},
        )

    def log_failure_notify_sent(
        self, task_id: str, feishu_message_id: str,
        failure_kind: str, expires_at: str,
    ) -> None:
        self._db.insert_audit_log(
            task_id=task_id,
            agent="daemon",
            action="failure_notify_sent",
            payload={
                "feishu_message_id": feishu_message_id,
                "failure_kind": failure_kind,
                "expires_at": expires_at,
            },
        )

    def log_failure_notify_failed(
        self, task_id: str, failure_kind: str, error: str,
    ) -> None:
        self._db.insert_audit_log(
            task_id=task_id,
            agent="daemon",
            action="failure_notify_failed",
            payload={"failure_kind": failure_kind, "error": error},
        )

    def log_dispatch_send_confirmation_failed(
        self, *, task_id: str, error: str,
    ) -> None:
        self._db.insert_audit_log(
            task_id=task_id,
            agent="daemon",
            action="dispatch_send_confirmation_failed",
            payload={"error": error},
        )

    def log_escalation_reply_processed(
        self, task_id: str, decision: str, rationale: str,
    ) -> None:
        self._db.insert_audit_log(
            task_id=task_id,
            agent="founder",
            action="escalation_reply_processed",
            payload={"decision": decision, "rationale": rationale},
        )

    def log_escalation_reply_rejected(
        self,
        task_id: str,
        reason: str,
        *,
        feishu_event_id: str | None = None,
        text_preview: str | None = None,
    ) -> None:
        payload: dict = {"reason": reason}
        if feishu_event_id is not None:
            payload["feishu_event_id"] = feishu_event_id
        if text_preview is not None:
            payload["text_preview"] = text_preview[:200]
        self._db.insert_audit_log(
            task_id=task_id,
            agent="daemon",
            action="escalation_reply_rejected",
            payload=payload,
        )

    def log_parse_hint_sent(
        self,
        task_id: str,
        *,
        hint_message_id: str,
        feishu_event_id: str | None = None,
    ) -> None:
        payload: dict = {"hint_message_id": hint_message_id}
        if feishu_event_id is not None:
            payload["feishu_event_id"] = feishu_event_id
        self._db.insert_audit_log(
            task_id=task_id,
            agent="daemon",
            action="escalation_parse_hint_sent",
            payload=payload,
        )

    def log_parse_hint_send_failed(
        self,
        task_id: str,
        *,
        error: str,
        feishu_event_id: str | None = None,
    ) -> None:
        payload: dict = {"error": error}
        if feishu_event_id is not None:
            payload["feishu_event_id"] = feishu_event_id
        self._db.insert_audit_log(
            task_id=task_id,
            agent="daemon",
            action="escalation_parse_hint_send_failed",
            payload=payload,
        )

    def log_failure_revisit_via_reply(
        self,
        *,
        predecessor_task_id: str,
        new_root: str,
        founder_note: str | None,
        feishu_message_id: str,
        feishu_event_id: str,
    ) -> None:
        self._db.insert_audit_log(
            task_id=new_root,
            agent="founder",
            action="failure_revisit_via_reply",
            payload={
                "predecessor_task_id": predecessor_task_id,
                "founder_note": founder_note,
                "feishu_message_id": feishu_message_id,
                "feishu_event_id": feishu_event_id,
            },
        )

    def log_task_cancelled(
        self, task_id: str, rationale: str, cascade: bool,
    ) -> None:
        self._db.insert_audit_log(
            task_id=task_id,
            agent="founder",
            action="task_cancelled",
            payload={"rationale": rationale, "cascade": cascade},
        )

    def log_progress(self, task_id: str, agent: str, message: str) -> None:
        """Record an agent-controlled mid-task progress note.

        Distinct from completion_report: this is a semantic checkpoint the
        agent emits while still working. Used by `grassland tail` and `grassland details`
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
    ) -> None:
        self._db.insert_audit_log(
            task_id=task_id,
            agent="orchestrator",
            action="orchestration_step",
            payload={"step_number": step_number, "decision": decision},
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
        (HTTP route / grassland revisit command) or "feishu-reply" (Feishu listener).
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

    def log_task_dispatched(
        self,
        *,
        task_id: str,
        talk_id: str,
        dispatcher_agent: str,
        dispatcher_role: str,
        effective_target: str,
        team: str,
    ) -> None:
        """Record on a NEW task that it was dispatched from a talk.

        `task_id` is the new task's id (NOT the talk id) -- the task_id scope
        is the new task; querying by talk_id uses the dispatched_from_talk_id
        column on tasks instead. `dispatcher_role` is "worker" or "manager"
        -- frozen at dispatch time so retroactive role changes don't rewrite
        history.
        """
        self._db.insert_audit_log(
            task_id=task_id,
            agent=dispatcher_agent,
            action="task_dispatched",
            payload={
                "talk_id": talk_id,
                "dispatcher_agent": dispatcher_agent,
                "dispatcher_role": dispatcher_role,
                "effective_target": effective_target,
                "team": team,
            },
        )

    # NOTE: audit_log.task_id is NOT NULL. Talk events reuse that column as a
    # generic "scope id" and store the talk id (TALK-NNN). Readers that filter
    # by talk id pass it in place of task_id.

    def log_asset_put(self, name: str, size_bytes: int, agent: str) -> None:
        self._db.insert_audit_log(
            task_id=name,  # asset name overloads task_id (NOT NULL) — same pattern as talks/scripts
            agent=agent,
            action="asset_put",
            payload={"name": name, "size_bytes": size_bytes},
        )

    def log_talk_started(
        self, talk_id: str, agent_name: str, resumed_from: str | None,
    ) -> None:
        self._db.insert_audit_log(
            task_id=talk_id,
            agent=agent_name,
            action="talk_started",
            payload={"resumed_from": resumed_from},
        )

    def log_talk_resumed(self, talk_id: str, agent_name: str) -> None:
        self._db.insert_audit_log(
            task_id=talk_id,
            agent=agent_name,
            action="talk_resumed",
            payload={},
        )

    def log_talk_abandoned(
        self, talk_id: str, agent_name: str, reason: str,
    ) -> None:
        self._db.insert_audit_log(
            task_id=talk_id,
            agent=agent_name,
            action="talk_abandoned",
            payload={"reason": reason},
        )

    def log_talk_ended(
        self,
        talk_id: str,
        agent_name: str,
        new_learnings_count: int,
        new_kb_slugs: list[str],
    ) -> None:
        self._db.insert_audit_log(
            task_id=talk_id,
            agent=agent_name,
            action="talk_ended",
            payload={
                "new_learnings_count": new_learnings_count,
                "new_kb_slugs": new_kb_slugs,
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
        described at line 173): TASK-xxx for task-path calls, TALK-xxx for
        talk-path calls. `source` is 'task' or 'talk' for quick filtering.
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

    def log_dispatch_via_feishu_accepted(
        self,
        *,
        task_id: str,
        team: str,
        sender_id: str,
        feishu_event_id: str,
    ) -> None:
        self._db.insert_audit_log(
            task_id=task_id,
            agent="founder",
            action="dispatch_via_feishu_accepted",
            payload={
                "team": team,
                "sender_id": sender_id,
                "feishu_event_id": feishu_event_id,
            },
        )

    def log_dispatch_via_feishu_rejected(
        self,
        *,
        reason: str,
        sender_id: str,
        feishu_event_id: str,
        task_id: str | None = None,
    ) -> None:
        self._db.insert_audit_log(
            task_id=task_id if task_id is not None else "(none)",
            agent="daemon",
            action="dispatch_via_feishu_rejected",
            payload={
                "reason": reason,
                "sender_id": sender_id,
                "feishu_event_id": feishu_event_id,
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
        composed_from_talk_id: str | None = None,
    ) -> None:
        self._db.insert_audit_log(
            task_id=thread_id,
            agent=composed_by,
            action="thread_started",
            payload={
                "subject": subject,
                "initial_recipients": initial_recipients,
                "forwarded_from_id": forwarded_from_id,
                "composed_by": composed_by,
                "composed_from_task_id": composed_from_task_id,
                "composed_from_talk_id": composed_from_talk_id,
            },
        )

    def log_thread_founder_addressed(
        self,
        thread_id: str,
        *,
        seq: int,
        speaker: str,
        notify_channel: str,
    ) -> None:
        self._db.insert_audit_log(
            task_id=thread_id,
            agent=speaker,
            action="thread_founder_addressed",
            payload={"seq": seq, "speaker": speaker, "notify_channel": notify_channel},
        )

    def log_thread_founder_notify_sent(
        self, *, thread_id: str, feishu_message_id: str,
    ) -> None:
        # agent="daemon" matches log_escalation_notify_sent / log_failure_notify_sent
        # — these are infrastructure-generated push events, not founder actions.
        self._db.insert_audit_log(
            task_id=thread_id, agent="daemon",
            action="thread_founder_notify_sent",
            payload={"feishu_message_id": feishu_message_id},
        )

    def log_thread_founder_notify_failed(
        self, *, thread_id: str, error: str,
    ) -> None:
        self._db.insert_audit_log(
            task_id=thread_id, agent="daemon",
            action="thread_founder_notify_failed",
            payload={"error": error},
        )

    def log_thread_message_sent(
        self,
        thread_id: str,
        *,
        seq: int,
        speaker: str,
        addressed_to: list[str] | None,
        kind: str,
    ) -> None:
        self._db.insert_audit_log(
            task_id=thread_id,
            agent=speaker,
            action="thread_message_sent",
            payload={"seq": seq, "addressed_to": addressed_to, "kind": kind},
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

    def log_thread_archive_requested(
        self,
        thread_id: str,
        *,
        close_out_count: int,
    ) -> None:
        self._db.insert_audit_log(
            task_id=thread_id,
            agent="founder",
            action="thread_archive_requested",
            payload={"close_out_count": close_out_count},
        )

    def log_thread_archived(
        self,
        thread_id: str,
        *,
        new_learnings_total: int,
        new_kb_slugs: list[str],
        turns_used: int,
    ) -> None:
        self._db.insert_audit_log(
            task_id=thread_id,
            agent="founder",
            action="thread_archived",
            payload={
                "new_learnings_total": new_learnings_total,
                "new_kb_slugs": new_kb_slugs,
                "turns_used": turns_used,
            },
        )

    def log_thread_abandoned(self, thread_id: str, *, reason: str) -> None:
        self._db.insert_audit_log(
            task_id=thread_id,
            agent="founder",
            action="thread_abandoned",
            payload={"reason": reason},
        )

    def log_thread_close_out_received(
        self,
        thread_id: str,
        *,
        agent: str,
        new_learnings_count: int,
        new_kb_slugs: list[str],
    ) -> None:
        self._db.insert_audit_log(
            task_id=thread_id,
            agent=agent,
            action="thread_close_out_received",
            payload={
                "new_learnings_count": new_learnings_count,
                "new_kb_slugs": new_kb_slugs,
            },
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
        Scope is the agent name itself (no task/talk context).
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

    def log_script_submitted(
        self,
        *,
        task_id: str,
        sr_id: str,
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
            action="script_submitted",
            payload={
                "script_request_id": sr_id,
                "title": title,
                "interpreter": interpreter,
                "cwd_hint": cwd_hint,
                "byte_size": byte_size,
                "line_count": line_count,
            },
        )

    def log_script_rejected(
        self, *, task_id: str, sr_id: str, reviewer: str, reason: str
    ) -> None:
        self._db.insert_audit_log(
            task_id=task_id,
            agent=reviewer,
            action="script_rejected",
            payload={
                "script_request_id": sr_id,
                "reviewer": reviewer,
                "reason": reason,
            },
        )

    def log_script_run_started(
        self,
        *,
        task_id: str,
        sr_id: str,
        reviewer: str,
        cwd_resolved: str,
        timeout_seconds: int,
        interpreter: str,
    ) -> None:
        self._db.insert_audit_log(
            task_id=task_id,
            agent=reviewer,
            action="script_run_started",
            payload={
                "script_request_id": sr_id,
                "reviewer": reviewer,
                "cwd_resolved": cwd_resolved,
                "timeout_seconds": timeout_seconds,
                "interpreter": interpreter,
            },
        )

    def log_script_run_completed(
        self,
        *,
        task_id: str,
        sr_id: str,
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
            action="script_run_completed",
            payload={
                "script_request_id": sr_id,
                "exit_code": exit_code,
                "duration_ms": duration_ms,
                "stdout_bytes": stdout_bytes,
                "stderr_bytes": stderr_bytes,
                "truncated_stdout": truncated_stdout,
                "truncated_stderr": truncated_stderr,
            },
        )

    def log_script_run_failed(
        self,
        *,
        task_id: str,
        sr_id: str,
        reason: str,
        exit_code: int | None = None,
        duration_ms: int | None = None,
    ) -> None:
        self._db.insert_audit_log(
            task_id=task_id,
            agent="founder",
            action="script_run_failed",
            payload={
                "script_request_id": sr_id,
                "exit_code": exit_code,
                "duration_ms": duration_ms,
                "reason": reason,
            },
        )

    # --- Feishu push correlation for script requests ---

    def log_script_notify_sent(
        self, *, task_id: str, sr_id: str, feishu_message_id: str,
    ) -> None:
        self._db.insert_audit_log(
            task_id=task_id,
            agent="daemon",
            action="script_notify_sent",
            payload={
                "script_request_id": sr_id,
                "feishu_message_id": feishu_message_id,
            },
        )

    def log_script_notify_failed(
        self, *, task_id: str, sr_id: str, error: str,
    ) -> None:
        self._db.insert_audit_log(
            task_id=task_id,
            agent="daemon",
            action="script_notify_failed",
            payload={
                "script_request_id": sr_id,
                "error": error,
            },
        )

    def log_script_reply_processed(
        self,
        *,
        sr_id: str,
        task_id: str,
        decision: str,
        rationale: str,
        feishu_event_id: str | None = None,
    ) -> None:
        payload: dict = {
            "script_request_id": sr_id,
            "decision": decision,
            "rationale": rationale,
        }
        if feishu_event_id is not None:
            payload["feishu_event_id"] = feishu_event_id
        self._db.insert_audit_log(
            task_id=task_id,
            agent="founder",
            action="script_reply_processed",
            payload=payload,
        )

    def log_script_reply_rejected(
        self,
        *,
        sr_id: str,
        task_id: str,
        reason: str,
        feishu_event_id: str | None = None,
        text_preview: str | None = None,
    ) -> None:
        payload: dict = {
            "script_request_id": sr_id,
            "reason": reason,
        }
        if feishu_event_id is not None:
            payload["feishu_event_id"] = feishu_event_id
        if text_preview is not None:
            payload["text_preview"] = text_preview[:200]
        self._db.insert_audit_log(
            task_id=task_id,
            agent="daemon",
            action="script_reply_rejected",
            payload=payload,
        )

    def log_script_run_result_notify_sent(
        self,
        *,
        sr_id: str,
        task_id: str,
        parent_message_id: str,
        follow_up_message_id: str,
        status: str,
    ) -> None:
        self._db.insert_audit_log(
            task_id=task_id,
            agent="daemon",
            action="script_run_result_notify_sent",
            payload={
                "script_request_id": sr_id,
                "parent_message_id": parent_message_id,
                "follow_up_message_id": follow_up_message_id,
                "status": status,
            },
        )

    def log_script_run_result_notify_failed(
        self,
        *,
        sr_id: str,
        task_id: str,
        error: str,
        status: str,
    ) -> None:
        self._db.insert_audit_log(
            task_id=task_id,
            agent="daemon",
            action="script_run_result_notify_failed",
            payload={
                "script_request_id": sr_id,
                "error": error,
                "status": status,
            },
        )
