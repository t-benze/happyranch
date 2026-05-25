"""Send the founder a Feishu message when a task escalates.

Phase 1 (this module) is outbound-only and persists a correlation row keyed
by the Feishu message_id. Phase 2's listener matches inbound replies against
those rows by `root_id`.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Protocol

from src.infrastructure.audit_logger import AuditLogger
from src.infrastructure.database import Database
from src.orchestrator.org_config import FeishuNotificationsConfig

logger = logging.getLogger(__name__)


class _Sender(Protocol):
    def send_post_message(
        self, *, chat_id: str, title: str, body_lines: list[str],
    ) -> str: ...

    def send_thread_reply(
        self, *, parent_message_id: str, title: str, body_lines: list[str],
    ) -> str: ...


_HINT_PREVIEW_CAP = 200
_SCRIPT_PREVIEW_CAP = 1500
_RESULT_OUTPUT_PREVIEW_CAP = 500


def _build_parse_hint_body(*, text_preview: str) -> tuple[str, list[str]]:
    """Body for the threaded reply we send when parse_reply rejects the founder's text."""
    preview = (
        text_preview if len(text_preview) <= _HINT_PREVIEW_CAP
        else text_preview[:_HINT_PREVIEW_CAP] + "…"
    )
    title = "Couldn't parse your reply — try again"
    lines = [
        "Your last reply couldn't be parsed.",
        "",
        "Got:",
        f"  {preview}" if preview else "  (empty)",
        "",
        "Expected the first non-empty line to be exactly one of:",
        "  APPROVE",
        "  REJECT",
        "  REVISIT",
        "",
        "Put your rationale on the lines after the verb. Example:",
        "  APPROVE",
        "  skip device verification this time",
        "",
        "Reply again in this thread to retry.",
    ]
    return title, lines


def _build_body_phase1(
    *,
    slug: str,
    task_id: str,
    brief: str,
    reason: str,
    last_summary: str = "",
) -> tuple[str, list[str]]:
    """Return (title, body_lines) for the post-format payload."""
    title = f"[Grassland {slug}] {task_id} escalated — action required"
    lines = ["Brief:", brief]
    if last_summary:
        lines += ["", "Result:", last_summary]
    lines += ["", "Escalation reason:", reason]
    return title, lines


def _build_failure_body(
    *,
    slug: str,
    task_id: str,
    brief: str,
    failure_kind: str,
    failure_note: str,
) -> tuple[str, list[str]]:
    """Return (title, body_lines) for the failure post-format payload."""
    title = f"[Grassland {slug}] {task_id} FAILED — review needed"
    lines = [
        "Brief:",
        brief,
        "",
        "Result:",
        f"{failure_kind}: {failure_note}",
        "",
        "Action — reply in this thread to retry:",
        "  REVISIT",
        "  <optional note>",
        "(Or ignore to leave it failed.)",
    ]
    return title, lines


def _build_script_request_body(
    *,
    slug: str,
    sr_id: str,
    agent: str,
    task_id: str,
    title: str,
    rationale: str,
    script_text: str,
    interpreter: str,
    cwd_hint: str | None,
) -> tuple[str, list[str]]:
    """Body for the script-request submit push (msg_type=post)."""
    header = f"[Grassland {slug}] {sr_id} submitted — review needed"
    if len(script_text) > _SCRIPT_PREVIEW_CAP:
        script_lines = script_text[:_SCRIPT_PREVIEW_CAP].split("\n") + [
            f"[truncated — see grassland scripts show {sr_id} for full script]"
        ]
    else:
        script_lines = script_text.split("\n")
    lines = [
        f"Agent:        {agent}",
        f"Task:         {task_id}",
        f"Interpreter:  {interpreter}",
        f"Cwd hint:     {cwd_hint or '(workspace root)'}",
        f"Title:        {title}",
        "",
        "Rationale:",
        rationale,
        "",
        "Script:",
        *script_lines,
        "",
        "To resolve, reply in this thread with one of:",
        "",
        "  APPROVE",
        "  <optional note>",
        "",
        "  —or—",
        "",
        "  REJECT",
        "  <reason>",
        "",
        "You can also resolve via CLI:",
        f"  grassland scripts show {sr_id}",
        f"  grassland scripts run {sr_id}",
        f"  grassland scripts reject {sr_id} --reason \"...\"",
    ]
    return header, lines


def _build_script_result_body(
    *,
    slug: str,
    sr_id: str,
    status: str,
    exit_code: int | None,
    duration_ms: int,
    stdout_head: str | None,
    stderr_head: str | None,
    reason: str | None,
) -> tuple[str, list[str]]:
    """Body for the terminal-result threaded reply."""
    if status == "completed":
        descriptor = f"completed (exit {exit_code if exit_code is not None else '?'})"
    else:
        descriptor = f"failed ({reason or 'unknown'})"
    header = f"[Grassland {slug}] {sr_id} {descriptor}"

    def _preview(s: str | None) -> list[str]:
        if not s:
            return ["(empty)"]
        s = s.rstrip("\n")
        if len(s) <= _RESULT_OUTPUT_PREVIEW_CAP:
            return s.split("\n")
        return (
            s[:_RESULT_OUTPUT_PREVIEW_CAP].split("\n")
            + [f"[truncated — full output in grassland scripts output {sr_id}]"]
        )

    duration_s = duration_ms / 1000.0
    lines = [
        f"Duration: {duration_s:.1f}s",
        "",
        "stdout:",
        *_preview(stdout_head),
        "",
        "stderr:",
        *_preview(stderr_head),
    ]
    return header, lines


class EscalationNotifier:
    def __init__(
        self,
        *,
        slug: str,
        db: Database,
        audit: AuditLogger,
        client: _Sender,
        config: FeishuNotificationsConfig,
    ) -> None:
        self._slug = slug
        self._db = db
        self._audit = audit
        self._client = client
        self._config = config

    async def notify_escalated(
        self,
        *,
        task_id: str,
        agent: str,
        reason: str,
        last_summary: str = "",
    ) -> None:
        """Send + persist + audit. Errors are caught and audited; the
        orchestration loop never sees them."""
        try:
            task = self._db.get_task(task_id)
            if task is None:
                logger.warning("notify_escalated: task %s not found", task_id)
                return
            brief = task.brief or ""

            now = datetime.now(timezone.utc)
            title, body_lines = _build_body_phase1(
                slug=self._slug,
                task_id=task_id,
                brief=brief,
                reason=reason,
                last_summary=last_summary,
            )
            message_id = self._client.send_post_message(
                chat_id=self._config.chat_id,
                title=title,
                body_lines=body_lines,
            )
            expires = now + timedelta(hours=self._config.reply_ttl_hours)
            self._db.mint_escalation_notification(
                feishu_message_id=message_id,
                org_slug=self._slug,
                task_id=task_id,
                chat_id=self._config.chat_id,
                expires_at=expires,
            )
            self._audit.log_escalation_notify_sent(
                task_id=task_id, feishu_message_id=message_id,
            )
        except Exception as exc:
            logger.exception("notify_escalated failed for task %s", task_id)
            try:
                self._audit.log_escalation_notify_failed(
                    task_id=task_id, error=f"{type(exc).__name__}: {exc}",
                )
            except Exception:
                logger.exception("audit log_escalation_notify_failed also failed")

    async def send_failure(
        self,
        *,
        task_id: str,
        agent: str,
        failure_kind: str,
        failure_note: str,
        last_summary: str = "",
    ) -> None:
        """Mirrors notify_escalated for FAILED tasks. Mint-after-send.
        All exceptions are swallowed and audited."""
        try:
            task = self._db.get_task(task_id)
            if task is None:
                logger.warning("send_failure: task %s not found", task_id)
                return
            brief = task.brief or ""

            now = datetime.now(timezone.utc)
            title, body_lines = _build_failure_body(
                slug=self._slug,
                task_id=task_id,
                brief=brief,
                failure_kind=failure_kind,
                failure_note=failure_note,
            )
            message_id = self._client.send_post_message(
                chat_id=self._config.chat_id,
                title=title,
                body_lines=body_lines,
            )
            expires = now + timedelta(hours=self._config.reply_ttl_hours)
            self._db.mint_escalation_notification(
                feishu_message_id=message_id,
                org_slug=self._slug,
                task_id=task_id,
                chat_id=self._config.chat_id,
                expires_at=expires,
                kind="failure",
            )
            self._audit.log_failure_notify_sent(
                task_id=task_id,
                feishu_message_id=message_id,
                failure_kind=failure_kind,
                expires_at=expires.isoformat(),
            )
        except Exception as exc:
            logger.exception("send_failure failed for task %s", task_id)
            try:
                self._audit.log_failure_notify_failed(
                    task_id=task_id, failure_kind=failure_kind,
                    error=f"{type(exc).__name__}: {exc}",
                )
            except Exception:
                logger.exception("audit log_failure_notify_failed also failed")

    async def send_parse_hint(
        self,
        *,
        parent_message_id: str,
        task_id: str,
        text_preview: str,
        feishu_event_id: str | None = None,
    ) -> None:
        """Reply (threaded) to a founder's unparseable text with a grammar hint.

        Best-effort: any exception is swallowed and audited so the listener's
        bad_decision flow always completes. The notification row is left
        UNCONSUMED by the caller, so the founder can simply reply again in
        the same thread.
        """
        title, body_lines = _build_parse_hint_body(text_preview=text_preview)
        try:
            hint_msg_id = self._client.send_thread_reply(
                parent_message_id=parent_message_id,
                title=title,
                body_lines=body_lines,
            )
            self._audit.log_parse_hint_sent(
                task_id=task_id,
                hint_message_id=hint_msg_id,
                feishu_event_id=feishu_event_id,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("send_parse_hint failed for task %s", task_id)
            try:
                self._audit.log_parse_hint_send_failed(
                    task_id=task_id,
                    error=f"{type(exc).__name__}: {exc}",
                    feishu_event_id=feishu_event_id,
                )
            except Exception:
                logger.exception("audit log_parse_hint_send_failed also failed")

    async def send_dispatch_confirmation(
        self, *, task_id: str, team: str | None, brief: str,
    ) -> None:
        """Top-level post (not threaded) confirming a Feishu dispatch.
        Best-effort; swallows + audits exceptions."""
        try:
            brief_trunc = brief if len(brief) <= 240 else brief[:240] + "…"
            title = f"[Grassland {self._slug}] Task {task_id} dispatched"
            body_lines = [
                f"Team:  {team or '(auto)'}",
                f"Brief: {brief_trunc}",
                "",
                "Track with:",
                f"  grassland tail --org {self._slug} {task_id}",
            ]
            self._client.send_post_message(
                chat_id=self._config.chat_id, title=title, body_lines=body_lines,
            )
        except Exception as exc:  # noqa: BLE001
            self._audit.log_dispatch_send_confirmation_failed(
                task_id=task_id, error=str(exc),
            )

    async def send_dispatch_error(
        self, *, reason: str, valid_teams: list[str] | None = None,
    ) -> None:
        """Top-level post reporting a rejected DISPATCH. Best-effort."""
        try:
            title = f"[Grassland {self._slug}] Dispatch rejected"
            body_lines = [f"Reason: {reason}"]
            if valid_teams:
                body_lines.append(f"Valid teams: {', '.join(valid_teams)}")
            self._client.send_post_message(
                chat_id=self._config.chat_id, title=title, body_lines=body_lines,
            )
        except Exception:  # noqa: BLE001
            # Error-card send failure is itself an error — log nothing extra,
            # the original audit row for dispatch_via_feishu_rejected already exists.
            pass

    async def send_thread_addressed(
        self,
        *,
        thread_id: str,
        subject: str,
        composer: str,
        body_text: str,
        addressed_to: list[str],
    ) -> bool:
        """Push a card to the founder when an agent addresses `@founder` in a thread.

        Returns True iff a send was attempted (i.e., this notifier is configured
        and reached the send call). Delivery failures are swallowed and audited;
        caller still receives True so it can report `founder_notified: true` —
        the audit log is the canonical "did it actually deliver" record.
        Returns False only if a precondition failure prevents even attempting.
        """
        preview = (
            body_text if len(body_text) <= _HINT_PREVIEW_CAP
            else body_text[:_HINT_PREVIEW_CAP] + "…"
        )
        title = f"Thread {thread_id} · started by {composer}"
        lines = [
            f"Subject: {subject}",
            f"Recipients: {', '.join(addressed_to)}",
            "",
            preview,
        ]
        try:
            now = datetime.now(timezone.utc)
            message_id = self._client.send_post_message(
                chat_id=self._config.chat_id,
                title=title,
                body_lines=lines,
            )
            expires = now + timedelta(hours=self._config.reply_ttl_hours)
            self._db.mint_escalation_notification(
                feishu_message_id=message_id,
                org_slug=self._slug,
                task_id=thread_id,
                chat_id=self._config.chat_id,
                expires_at=expires,
                kind="thread_addressed",
            )
            self._audit.log_thread_founder_notify_sent(
                thread_id=thread_id, feishu_message_id=message_id,
            )
        except Exception as exc:
            logger.exception("send_thread_addressed failed for thread %s", thread_id)
            try:
                self._audit.log_thread_founder_notify_failed(
                    thread_id=thread_id, error=f"{type(exc).__name__}: {exc}",
                )
            except Exception:
                logger.exception("audit log_thread_founder_notify_failed also failed")
        return True

    async def send_script_request(
        self,
        *,
        sr_id: str,
        agent: str,
        task_id: str,
        title: str,
        rationale: str,
        script_text: str,
        interpreter: str,
        cwd_hint: str | None,
    ) -> None:
        """Push a Feishu post to the founder when an agent submits SR-NNN.

        Mint-after-send: the correlation row is keyed by the returned
        feishu_message_id, so a send failure leaves no orphan row. All
        exceptions are swallowed and audited so submit_script's caller
        (the agent) never sees a 5xx because Feishu is down.
        """
        try:
            now = datetime.now(timezone.utc)
            header, body_lines = _build_script_request_body(
                slug=self._slug, sr_id=sr_id, agent=agent, task_id=task_id,
                title=title, rationale=rationale, script_text=script_text,
                interpreter=interpreter, cwd_hint=cwd_hint,
            )
            message_id = self._client.send_post_message(
                chat_id=self._config.chat_id,
                title=header,
                body_lines=body_lines,
            )
            expires = now + timedelta(hours=self._config.reply_ttl_hours)
            self._db.mint_escalation_notification(
                feishu_message_id=message_id,
                org_slug=self._slug,
                task_id=sr_id,            # SR-NNN in task_id column (matches thread_addressed)
                chat_id=self._config.chat_id,
                expires_at=expires,
                kind="script_request",
            )
            self._audit.log_script_notify_sent(
                task_id=task_id, sr_id=sr_id, feishu_message_id=message_id,
            )
        except Exception as exc:
            logger.exception("send_script_request failed for SR %s", sr_id)
            try:
                self._audit.log_script_notify_failed(
                    task_id=task_id, sr_id=sr_id,
                    error=f"{type(exc).__name__}: {exc}",
                )
            except Exception:
                logger.exception("audit log_script_notify_failed also failed")

    async def send_script_run_result(
        self,
        *,
        sr_id: str,
        task_id: str,
        parent_message_id: str,
        status: str,
        exit_code: int | None,
        duration_ms: int,
        stdout_head: str | None,
        stderr_head: str | None,
        reason: str | None,
    ) -> None:
        """Post a threaded reply with the run's terminal result.

        Best-effort; no DB row minted (this is a leaf — no reply expected).
        Failures are swallowed and audited.
        """
        try:
            header, body_lines = _build_script_result_body(
                slug=self._slug, sr_id=sr_id, status=status,
                exit_code=exit_code, duration_ms=duration_ms,
                stdout_head=stdout_head, stderr_head=stderr_head,
                reason=reason,
            )
            follow_up_id = self._client.send_thread_reply(
                parent_message_id=parent_message_id,
                title=header,
                body_lines=body_lines,
            )
            self._audit.log_script_run_result_notify_sent(
                sr_id=sr_id, task_id=task_id,
                parent_message_id=parent_message_id,
                follow_up_message_id=follow_up_id,
                status=status,
            )
        except Exception as exc:
            logger.exception("send_script_run_result failed for SR %s", sr_id)
            try:
                self._audit.log_script_run_result_notify_failed(
                    sr_id=sr_id, task_id=task_id,
                    error=f"{type(exc).__name__}: {exc}",
                    status=status,
                )
            except Exception:
                logger.exception("audit log_script_run_result_notify_failed also failed")
