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


def _build_body_phase1(
    *,
    slug: str,
    task_id: str,
    agent: str,
    team: str,
    brief: str,
    last_summary: str,
    reason: str,
    escalated_at: datetime,
) -> tuple[str, list[str]]:
    """Return (title, body_lines) for the post-format payload."""
    title = f"[OPC {slug}] {task_id} escalated — action required"
    lines = [
        f"Agent:        {agent}",
        f"Team:         {team}",
        f"Task:         {task_id}",
        f"Org:          {slug}",
        f"Escalated at: {escalated_at:%Y-%m-%d %H:%M:%S} UTC",
        "",
        "--- Brief ---",
        brief,
        "",
        "--- Last manager summary ---",
        last_summary or "(none)",
        "",
        "--- Escalation reason ---",
        reason,
        "",
        "--- To resolve ---",
        "Reply in this thread with one of:",
        "",
        "  APPROVE",
        "  <your rationale>",
        "",
        "  —or—",
        "",
        "  REJECT",
        "  <your rationale>",
        "",
        "You can also resolve via CLI:",
        f"  opc resolve-escalation --org {slug} --task-id {task_id} \\",
        "    --decision approve|reject --rationale \"...\"",
    ]
    return title, lines


def _build_failure_body(
    *,
    slug: str,
    task_id: str,
    agent: str,
    team: str,
    brief: str,
    last_summary: str,
    failure_kind: str,
    failure_note: str,
    failed_at: str,
) -> tuple[str, list[str]]:
    """Return (title, body_lines) for the failure post-format payload."""
    title = f"[OPC {slug}] {task_id} FAILED — review needed"
    lines = [
        f"Agent:        {agent}",
        f"Team:         {team}",
        f"Task:         {task_id}",
        f"Org:          {slug}",
        f"Failed at:    {failed_at}",
        f"Failure kind: {failure_kind}",
        "",
        "--- Brief ---",
        brief,
        "",
        "--- Last manager summary ---",
        last_summary or "(none)",
        "",
        "--- Failure detail ---",
        failure_note,
        "",
        "--- To revisit ---",
        "Reply in this thread with:",
        "",
        "  REVISIT",
        "  <optional note that becomes founder_note on the new root>",
        "",
        "(Or ignore this message — the task stays failed.)",
    ]
    return title, lines


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
            team = task.team or ""
            brief = task.brief or ""

            now = datetime.now(timezone.utc)
            title, body_lines = _build_body_phase1(
                slug=self._slug,
                task_id=task_id,
                agent=agent,
                team=team,
                brief=brief,
                last_summary=last_summary,
                reason=reason,
                escalated_at=now,
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
            team = task.team or ""
            brief = task.brief or ""

            now = datetime.now(timezone.utc)
            failed_at = task.completed_at or now.isoformat()
            title, body_lines = _build_failure_body(
                slug=self._slug,
                task_id=task_id,
                agent=agent,
                team=team,
                brief=brief,
                last_summary=last_summary,
                failure_kind=failure_kind,
                failure_note=failure_note,
                failed_at=failed_at,
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
