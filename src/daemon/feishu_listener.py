"""Long-lived Feishu event listener — subscribes to im.message.receive_v1
events and routes founder replies to resolve_escalation.

Architecture:
- One listener per org with feishu_notifications enabled.
- WS connection runs in a daemon thread (the lark-oapi SDK's start() is blocking).
- Inbound events are bridged from the WS thread to the asyncio loop via
  asyncio.run_coroutine_threadsafe; actual logic runs on the daemon's loop.
"""
from __future__ import annotations

import asyncio
import logging
import threading
from datetime import datetime, timezone
from typing import Awaitable, Callable

import lark_oapi as lark

from src.infrastructure.audit_logger import AuditLogger
from src.infrastructure.database import Database
from src.infrastructure.feishu.reply_parser import (
    extract_text_from_content,
    parse_reply,
)

logger = logging.getLogger(__name__)


# Type for the resolve_escalation callable — either the route handler bound
# to the org, or a test stub. Awaits a coroutine that performs the transition.
ResolveFn = Callable[..., Awaitable[None]]


class FeishuEventListener:
    def __init__(
        self,
        *,
        slug: str,
        db: Database,
        audit: AuditLogger,
        chat_id: str,
        resolve_escalation: ResolveFn,
        loop: asyncio.AbstractEventLoop,
        app_id: str,
        app_secret: str,
        domain: str,
    ) -> None:
        self._slug = slug
        self._db = db
        self._audit = audit
        self._chat_id = chat_id
        self._resolve_escalation = resolve_escalation
        self._loop = loop
        self._app_id = app_id
        self._app_secret = app_secret
        self._domain = domain
        self._ws_client = None
        self._thread: threading.Thread | None = None

    # ---- Lifecycle ----

    def start(self) -> None:
        """Construct the WS client and start it in a daemon thread."""
        handler = (
            lark.EventDispatcherHandler.builder("", "")
            .register_p2_im_message_receive_v1(self._on_message_event)
            .build()
        )
        self._ws_client = lark.ws.Client(
            self._app_id,
            self._app_secret,
            domain=self._domain,
            event_handler=handler,
            log_level=lark.LogLevel.WARNING,
        )

        def _run():
            try:
                self._ws_client.start()
            except Exception:
                logger.exception("Feishu WS client crashed (org=%s)", self._slug)

        self._thread = threading.Thread(
            target=_run, daemon=True, name=f"feishu-ws-{self._slug}",
        )
        self._thread.start()
        logger.info("started Feishu event listener for org=%s", self._slug)

    # ---- WS thread -> asyncio bridge ----

    def _on_message_event(self, data) -> None:  # called in WS thread
        try:
            asyncio.run_coroutine_threadsafe(
                self._handle_event_async(data),
                self._loop,
            )
        except Exception:
            logger.exception("failed to schedule event for org=%s", self._slug)

    # ---- Async handler ----

    async def _handle_event_async(self, data) -> None:
        try:
            event_id = data.header.event_id
            msg = data.event.message

            # 1. Dedup — first writer wins; redelivery silently dropped.
            if not self._db.record_processed_event(
                org_slug=self._slug, feishu_event_id=event_id,
                outcome="pending", reason=None,
            ):
                return

            # 2. Chat filter
            if msg.chat_id != self._chat_id:
                return

            # 3. Threading filter
            if not msg.root_id:
                return

            # 4. Sender filter
            if data.event.sender.sender_type != "user":
                return

            # 5. Notification lookup
            row = self._db.get_escalation_notification(msg.root_id)
            if row is None:
                return
            if row["consumed_at"] is not None:
                return
            expires_at = datetime.fromisoformat(row["expires_at"])
            if datetime.now(timezone.utc) >= expires_at:
                return

            # 6. Parse text
            text = extract_text_from_content(msg.message_type, msg.content)
            if text is None:
                return
            parsed = parse_reply(text)
            if parsed is None:
                self._audit.log_escalation_reply_rejected(
                    task_id=row["task_id"], reason="bad_decision",
                )
                return

            # 7. Apply
            await self._resolve_escalation(
                slug=self._slug,
                task_id=row["task_id"],
                decision=parsed.decision,
                rationale=parsed.rationale,
            )
            self._db.consume_escalation_notification(
                msg.root_id, consumed_by="feishu-reply",
            )
            self._audit.log_escalation_reply_processed(
                task_id=row["task_id"],
                decision=parsed.decision,
                rationale=parsed.rationale,
            )
        except Exception:
            logger.exception("event handler error (org=%s)", self._slug)
