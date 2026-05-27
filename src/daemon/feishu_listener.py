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


# Type aliases for the callable dependencies injected into the listener.
ResolveFn = Callable[..., Awaitable[None]]
RevisitFn = Callable[..., Awaitable[object]]
DispatchFn = Callable[..., Awaitable[tuple[str, str]]]
SendConfirmFn = Callable[..., Awaitable[None]]
SendErrorFn = Callable[..., Awaitable[None]]
SendParseHintFn = Callable[..., Awaitable[None]]
ResolveThreadFn = Callable[..., Awaitable[None]]
RunJobFn = Callable[..., Awaitable[dict]]
RejectJobFn = Callable[..., Awaitable[object]]

# HTTPException detail codes raised by the script helpers that we want to
# preserve verbatim in the audit row (instead of bucketing them as the
# generic "handler_exception"). Keep in sync with
# src/daemon/routes/scripts.py — run_job_from_notification and
# reject_job_from_notification are the two raisers.
_SCRIPT_HELPER_DETAIL_CODES = frozenset({
    "not_pending", "cwd_missing", "interpreter_unavailable",
    "invalid_cwd_override", "invalid_timeout", "unknown_script_request",
    "empty_reason", "reason_too_long",
})


class FeishuEventListener:
    def __init__(
        self,
        *,
        slug: str,
        db: Database,
        audit: AuditLogger,
        chat_id: str,
        resolve_escalation: ResolveFn,
        revisit_from_notification: RevisitFn,
        dispatch_via_feishu: DispatchFn,
        send_dispatch_confirmation: SendConfirmFn,
        send_dispatch_error: SendErrorFn,
        allow_dispatch: bool,
        loop: asyncio.AbstractEventLoop,
        app_id: str,
        app_secret: str,
        domain: str,
        run_job_from_notification: RunJobFn | None = None,
        reject_job_from_notification: RejectJobFn | None = None,
        send_parse_hint: SendParseHintFn | None = None,
        resolve_thread_from_notification: ResolveThreadFn | None = None,
    ) -> None:
        self._slug = slug
        self._db = db
        self._audit = audit
        self._chat_id = chat_id
        self._resolve_escalation = resolve_escalation
        self._revisit_from_notification = revisit_from_notification
        self._dispatch_via_feishu = dispatch_via_feishu
        self._send_dispatch_confirmation = send_dispatch_confirmation
        self._send_dispatch_error = send_dispatch_error
        self._send_parse_hint = send_parse_hint
        self._resolve_thread_from_notification = resolve_thread_from_notification
        self._run_job_from_notification = run_job_from_notification
        self._reject_job_from_notification = reject_job_from_notification
        self._allow_dispatch = allow_dispatch
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

            def _close(outcome: str, reason: str | None = None) -> None:
                self._db.update_processed_event_outcome(
                    org_slug=self._slug, feishu_event_id=event_id,
                    outcome=outcome, reason=reason,
                )

            # 2. Chat filter
            if msg.chat_id != self._chat_id:
                _close("ignored", "wrong_chat")
                return

            # 3. Bifurcate: threaded reply vs. top-level dispatch
            if not msg.root_id:
                if not self._allow_dispatch:
                    _close("ignored", "dispatch_disabled")
                    return
                await self._handle_top_level_dispatch(data, msg, event_id, _close)
                return

            await self._handle_threaded_reply(data, msg, event_id, _close)

        except Exception:
            logger.exception("event handler error (org=%s)", self._slug)
            try:
                self._db.update_processed_event_outcome(
                    org_slug=self._slug,
                    feishu_event_id=getattr(data.header, "event_id", "?"),
                    outcome="rejected", reason="handler_exception",
                )
            except Exception:
                logger.exception("failed to record handler-exception outcome")

    async def _handle_threaded_reply(self, data, msg, event_id: str, _close) -> None:
        """Reply branch — handles founder APPROVE/REJECT replies in a Feishu thread."""
        # 4. Sender filter
        if data.event.sender.sender_type != "user":
            _close("ignored", "not_user_sender")
            return

        # 5. Notification lookup
        row = self._db.get_escalation_notification(msg.root_id)
        if row is None:
            _close("ignored", "notification_not_found")
            return
        if row["consumed_at"] is not None:
            _close("ignored", "notification_consumed")
            return
        expires_at = datetime.fromisoformat(row["expires_at"])
        if datetime.now(timezone.utc) >= expires_at:
            _close("ignored", "notification_expired")
            return

        # 6a. Thread reply — founder's text is freeform, not a decision verb.
        #     Route directly to resolve_thread_from_notification, skip parse_reply.
        if (row.get("kind") or "escalation") == "thread_addressed":
            freeform_text = extract_text_from_content(msg.message_type, msg.content)
            if freeform_text is None:
                _close("rejected", "unsupported_msg_type")
                return
            if self._resolve_thread_from_notification is None:
                _close("rejected", "handler_exception")
                return
            try:
                await self._resolve_thread_from_notification(
                    thread_id=row["task_id"],
                    founder_text=freeform_text,
                    message_id=msg.root_id,
                )
            except Exception:  # noqa: BLE001
                logger.exception(
                    "resolve_thread_from_notification raised for thread %s",
                    row["task_id"],
                )
                _close("rejected", "handler_exception")
                return
            _close("consumed", None)
            return

        # 6b. Escalation/failure reply — parse the decision verb.
        text = extract_text_from_content(msg.message_type, msg.content)
        if text is None:
            _close("rejected", "unsupported_msg_type")
            return
        parsed = parse_reply(text)
        if parsed is None:
            self._audit.log_escalation_reply_rejected(
                task_id=row["task_id"],
                reason="bad_decision",
                feishu_event_id=event_id,
                text_preview=text,
            )
            if self._send_parse_hint is not None:
                try:
                    await self._send_parse_hint(
                        parent_message_id=msg.message_id,
                        task_id=row["task_id"],
                        text_preview=text,
                        feishu_event_id=event_id,
                    )
                except Exception:  # noqa: BLE001
                    # Hint helper audits its own failure; never block the
                    # listener — the audit row + processed_event_ids outcome
                    # still record the parse failure.
                    logger.exception(
                        "send_parse_hint raised for task %s", row["task_id"],
                    )
            _close("rejected", "bad_decision")
            return

        # 7. Dispatch by kind × verb
        await self._dispatch_reply_action(row, parsed, msg, event_id, _close)

    async def _dispatch_reply_action(self, row, parsed, msg, event_id: str, _close) -> None:
        """Route a parsed threaded reply by notification kind × decision verb."""
        kind = row.get("kind") or "escalation"
        decision = parsed.decision
        task_id = row["task_id"]

        # Branch 1: escalation + approve/reject
        if kind == "escalation" and decision in ("approve", "reject"):
            try:
                await self._resolve_escalation(
                    slug=self._slug,
                    task_id=task_id,
                    decision=decision,
                    rationale=parsed.rationale,
                )
            except Exception:  # noqa: BLE001
                self._audit.log_escalation_reply_rejected(
                    task_id, "handler_exception", feishu_event_id=event_id,
                )
                _close("rejected", "handler_exception")
                return
            self._db.consume_escalation_notification(msg.root_id, consumed_by="feishu-reply")
            self._audit.log_escalation_reply_processed(
                task_id, decision, parsed.rationale,
            )
            _close("consumed", None)
            return

        # Branch 2: failure + revisit
        if kind == "failure" and decision == "revisit":
            try:
                result = await self._revisit_from_notification(
                    task_id=task_id,
                    founder_note=parsed.rationale,
                    actor="feishu-reply",
                )
            except Exception as exc:  # noqa: BLE001
                reason = "handler_exception"
                detail = getattr(exc, "detail", None)
                if isinstance(detail, dict) and detail.get("code") == "cannot_revisit":
                    reason = "cannot_revisit"
                self._audit.log_escalation_reply_rejected(
                    task_id, reason, feishu_event_id=event_id,
                )
                # Leave notification UNCONSUMED — preserves founder's intent
                _close("rejected", reason)
                return
            new_root_id = result.new_root_id
            self._db.consume_escalation_notification(msg.root_id, consumed_by="feishu-reply")
            self._audit.log_failure_revisit_via_reply(
                predecessor_task_id=task_id,
                new_root=new_root_id,
                founder_note=parsed.rationale,
                feishu_message_id=msg.root_id,
                feishu_event_id=event_id,
            )
            _close("consumed", None)
            return

        # Branch 3: script_request + approve/reject
        if kind == "script_request" and decision in ("approve", "reject"):
            if (
                self._run_job_from_notification is None
                or self._reject_job_from_notification is None
            ):
                self._audit.log_job_reply_rejected(
                    job_id=task_id, task_id=task_id,
                    reason="handler_exception", feishu_event_id=event_id,
                )
                _close("rejected", "handler_exception")
                return
            from src.infrastructure.feishu.reply_parser import NO_RATIONALE
            try:
                if decision == "approve":
                    await self._run_job_from_notification(job_id=task_id)
                else:  # reject
                    rationale = parsed.rationale
                    if not rationale or rationale == NO_RATIONALE:
                        rationale = "(no rationale provided via Feishu)"
                    await self._reject_job_from_notification(
                        job_id=task_id, reason=rationale,
                    )
            except Exception as exc:  # noqa: BLE001
                reason_code = "handler_exception"
                detail = getattr(exc, "detail", None)
                if isinstance(detail, dict):
                    code = detail.get("code")
                    if code in _SCRIPT_HELPER_DETAIL_CODES:
                        reason_code = code
                self._audit.log_job_reply_rejected(
                    job_id=task_id, task_id=task_id,
                    reason=reason_code, feishu_event_id=event_id,
                )
                _close("rejected", reason_code)
                return
            self._db.consume_escalation_notification(
                msg.root_id, consumed_by="feishu-reply",
            )
            self._audit.log_job_reply_processed(
                job_id=task_id, task_id=task_id,
                decision=decision, rationale=parsed.rationale,
                feishu_event_id=event_id,
            )
            _close("consumed", None)
            return

        # Branch 4: verb mismatch
        #   - escalation + revisit
        #   - failure + approve/reject
        #   - script_request + revisit  -> script-specific audit action
        if kind == "script_request":
            self._audit.log_job_reply_rejected(
                job_id=task_id, task_id=task_id,
                reason="verb_mismatch", feishu_event_id=event_id,
            )
        else:
            self._audit.log_escalation_reply_rejected(
                task_id, "verb_mismatch", feishu_event_id=event_id,
            )
        _close("rejected", "verb_mismatch")

    async def _handle_top_level_dispatch(self, data, msg, event_id: str, _close) -> None:
        """Dispatch branch — Task 12 stub. Task 14 fills in 5d–8d."""
        # 4d. Sender filter — only accept human-typed messages (matches reply branch).
        if data.event.sender.sender_type != "user":
            _close("ignored", "not_user_sender")
            return

        # 5d. Parse
        from src.infrastructure.feishu.reply_parser import (
            parse_top_level_message,
        )
        text = extract_text_from_content(msg.message_type, msg.content)
        intent = parse_top_level_message(text) if text else None
        sender_id = getattr(data.event.sender.sender_id, "open_id", "") or ""

        if intent is None:
            self._audit.log_dispatch_via_feishu_rejected(
                reason="parse_failed", sender_id=sender_id, feishu_event_id=event_id,
            )
            _close("rejected", "parse_failed")
            return

        # Step 6d: Dispatch via helper
        from src.daemon.routes.tasks import DispatchError
        try:
            task_id, team = await self._dispatch_via_feishu(
                intent=intent, sender_id=sender_id, event_id=event_id,
            )
        except DispatchError as exc:
            # Audit rejection — the helper raises BEFORE auditing for empty_brief /
            # unknown_team, so we are the only audit record for these reasons.
            self._audit.log_dispatch_via_feishu_rejected(
                reason=exc.reason, sender_id=sender_id, feishu_event_id=event_id,
            )
            # Step 8d (rejection path): send error card
            reason_text = exc.reason
            if exc.reason == "unknown_team" and intent.team:
                reason_text = f'unknown team "{intent.team}"'
            try:
                await self._send_dispatch_error(
                    reason=reason_text, valid_teams=exc.valid_teams,
                )
            except Exception:  # noqa: BLE001
                pass  # error-card send failure is itself silent
            _close("rejected", exc.reason)
            return

        # Step 7d: Confirmation card on success
        try:
            await self._send_dispatch_confirmation(
                task_id=task_id, team=team, brief=intent.brief,
            )
        except Exception:  # noqa: BLE001
            pass  # task already created; confirmation send failure is logged inside the notifier
        _close("consumed", None)


# ---------------------------------------------------------------------------
# Module-level helpers — used by both daemon lifespan (app.py) and add_org
# (state.py) so neither has to import the other (which would be circular).
# ---------------------------------------------------------------------------


def maybe_start_feishu_listener_for_org(org, state, loop) -> None:
    """Construct and start a FeishuEventListener for one org IF its config is
    complete. Idempotent — does nothing if the listener is already running on
    this OrgState. Safe to call from both daemon startup and add_org."""
    if org.feishu_listener is not None:
        return
    if (
        org.feishu_app_id is None or org.feishu_app_secret is None
        or org.feishu_chat_id is None or org.feishu_domain is None
    ):
        return

    from src.daemon.routes.tasks import (
        resolve_escalation_in_process,
        revisit_from_notification,
        dispatch_via_feishu,
    )
    from src.infrastructure.audit_logger import AuditLogger
    from src.orchestrator.org_config import load_org_config

    # Fetch allow_dispatch from per-org config. If config load fails or
    # feishu_notifications is unset (shouldn't happen since we got this far,
    # but defensive), default to False.
    allow_dispatch = False
    try:
        cfg = load_org_config(org.orchestrator._paths)
        if cfg.feishu_notifications is not None:
            allow_dispatch = cfg.feishu_notifications.allow_dispatch
    except Exception:  # noqa: BLE001
        allow_dispatch = False

    async def _resolve_for_listener(_org=org, _state=state, **kw):
        # Strip slug kwarg forwarded by the listener — already bound via _org.
        kw.pop("slug", None)
        # NOTE: do NOT swallow exceptions here. If resolve_escalation_in_process
        # raises (e.g. 409 task_not_escalated because the task transitioned via
        # CLI fallback), the listener's outer try/except records the event as
        # rejected and leaves the notification row unconsumed. Swallowing here
        # would let the listener falsely claim success, consume the row, and
        # silently lose the founder's reply.
        await resolve_escalation_in_process(_org, _state, **kw)

    async def _revisit_for_listener(*, task_id, founder_note, actor):
        return await revisit_from_notification(
            org, state, task_id=task_id,
            founder_note=founder_note, actor=actor,
        )

    async def _dispatch_for_listener(*, intent, sender_id, event_id):
        return await dispatch_via_feishu(
            org, state, intent=intent,
            sender_id=sender_id, event_id=event_id,
        )

    async def _send_confirm_for_listener(*, task_id, team, brief):
        if org.notifier is None:
            return
        return await org.notifier.send_dispatch_confirmation(
            task_id=task_id, team=team, brief=brief,
        )

    async def _send_error_for_listener(*, reason, valid_teams):
        if org.notifier is None:
            return
        return await org.notifier.send_dispatch_error(
            reason=reason, valid_teams=valid_teams,
        )

    async def _send_parse_hint_for_listener(
        *, parent_message_id, task_id, text_preview, feishu_event_id,
    ):
        if org.notifier is None:
            return
        return await org.notifier.send_parse_hint(
            parent_message_id=parent_message_id,
            task_id=task_id,
            text_preview=text_preview,
            feishu_event_id=feishu_event_id,
        )

    async def _resolve_thread_for_listener(*, thread_id, founder_text, message_id):
        from src.daemon.routes.threads import resolve_thread_from_notification
        return await resolve_thread_from_notification(
            org,
            thread_id=thread_id, founder_text=founder_text,
            message_id=message_id, slug=org.slug,
        )

    async def _run_job_for_listener(*, job_id):
        from src.daemon.routes.jobs import run_job_from_notification
        return await run_job_from_notification(org, job_id=job_id)

    async def _reject_job_for_listener(*, job_id, reason):
        from src.daemon.routes.jobs import reject_job_from_notification
        return await reject_job_from_notification(
            org, job_id=job_id, reason=reason,
        )

    listener = FeishuEventListener(
        slug=org.slug,
        db=org.db,
        audit=AuditLogger(org.db),
        chat_id=org.feishu_chat_id,
        resolve_escalation=_resolve_for_listener,
        revisit_from_notification=_revisit_for_listener,
        dispatch_via_feishu=_dispatch_for_listener,
        send_dispatch_confirmation=_send_confirm_for_listener,
        send_dispatch_error=_send_error_for_listener,
        send_parse_hint=_send_parse_hint_for_listener,
        resolve_thread_from_notification=_resolve_thread_for_listener,
        run_job_from_notification=_run_job_for_listener,
        reject_job_from_notification=_reject_job_for_listener,
        allow_dispatch=allow_dispatch,
        loop=loop,
        app_id=org.feishu_app_id,
        app_secret=org.feishu_app_secret,
        domain=org.feishu_domain,
    )
    listener.start()
    org.feishu_listener = listener


def start_feishu_listeners_for_state(state, loop) -> None:
    """For each org in state with full Feishu config, ensure a listener exists."""
    for org in state.orgs.values():
        maybe_start_feishu_listener_for_org(org, state, loop)
