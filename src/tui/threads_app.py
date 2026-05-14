"""Textual app for the `opc threads` TUI.

Entry point: `run(slug: str) -> None` launches the app for a specific org.
The CLI's `opc threads` (no subcommand) handler calls this.
"""
from __future__ import annotations

from pathlib import Path

from textual.app import App
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, Footer, Header, Input, ListItem, ListView, Label, RichLog, Static, TextArea


CSS_PATH = Path(__file__).parent / "threads_app.tcss"


class NewThreadScreen(ModalScreen):
    """Modal that collects subject/recipients/body for a new thread."""

    BINDINGS = [
        Binding("escape", "dismiss(None)", "Cancel"),
        Binding("ctrl+enter", "submit", "Send", priority=True),
    ]

    def __init__(self, prefill: dict | None = None) -> None:
        super().__init__()
        self._prefill = prefill or {}

    def compose(self):
        with Vertical(id="new-thread-modal"):
            yield Static("New thread — Ctrl+Enter to send, Esc to cancel", id="new-label")
            yield Input(placeholder="Subject", value=self._prefill.get("subject", ""),
                        id="new-subject")
            yield Input(placeholder="Recipients (comma-separated)", id="new-recipients")
            body = self._prefill.get("body", "")
            yield TextArea(body, id="new-body")
            yield Button("Send", id="new-submit", variant="primary")

    def action_submit(self) -> None:
        subject = self.query_one("#new-subject", Input).value.strip()
        recipients_raw = self.query_one("#new-recipients", Input).value.strip()
        body = self.query_one("#new-body", TextArea).text.strip()
        recipients = [r.strip() for r in recipients_raw.split(",") if r.strip()]
        if not subject or not recipients or not body:
            self.app.notify("subject, recipients, and body are all required",
                            severity="warning")
            return
        payload = {"subject": subject, "recipients": recipients, "body_markdown": body}
        if self._prefill.get("forwarded_from_id"):
            payload["forwarded_from_id"] = self._prefill["forwarded_from_id"]
            payload["forwarded_from_kind"] = self._prefill["forwarded_from_kind"]
        self.dismiss(payload)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "new-submit":
            self.action_submit()


class InviteScreen(ModalScreen):
    BINDINGS = [
        Binding("escape", "dismiss(None)", "Cancel"),
        Binding("ctrl+enter", "submit", "Invite", priority=True),
    ]

    def compose(self):
        with Vertical(id="invite-modal"):
            yield Static("Invite agent — Ctrl+Enter to invite, Esc to cancel",
                         id="invite-label")
            yield Input(placeholder="agent name", id="invite-agent")

    def action_submit(self) -> None:
        name = self.query_one("#invite-agent", Input).value.strip()
        if not name:
            self.app.notify("agent name required", severity="warning")
            return
        self.dismiss(name)


class AbandonScreen(ModalScreen[str | None]):
    BINDINGS = [
        Binding("escape", "dismiss(None)", "Cancel"),
        Binding("ctrl+enter", "submit", "Abandon", priority=True),
    ]

    def compose(self):
        with Vertical(id="abandon-modal"):
            yield Static("Abandon thread — Ctrl+Enter to abandon, Esc to cancel",
                         id="abandon-label")
            yield Input(placeholder="reason", id="abandon-reason")

    def action_submit(self) -> None:
        reason = self.query_one("#abandon-reason", Input).value.strip()
        if not reason:
            self.app.notify("reason required", severity="warning")
            return
        self.dismiss(reason)


class ArchiveScreen(ModalScreen[dict | None]):
    BINDINGS = [
        Binding("escape", "dismiss(None)", "Cancel"),
        Binding("ctrl+enter", "submit", "Archive", priority=True),
    ]

    def compose(self):
        with Vertical(id="archive-modal"):
            yield Static("Archive thread — Ctrl+Enter to archive, Esc to cancel",
                         id="archive-label")
            yield TextArea(id="archive-summary")
            yield Checkbox("Request close-outs from each participant",
                           value=True, id="archive-closeouts")

    def action_submit(self) -> None:
        summary = self.query_one("#archive-summary", TextArea).text.strip()
        if not summary:
            self.app.notify("summary required", severity="warning")
            return
        close_outs = self.query_one("#archive-closeouts", Checkbox).value
        self.dismiss({"summary": summary, "request_close_outs": close_outs})


class HelpScreen(ModalScreen[None]):
    BINDINGS = [
        Binding("escape", "dismiss(None)", "Close"),
        Binding("question_mark", "dismiss(None)", "Close"),
    ]

    HELP_TEXT = (
        "Keybindings\n"
        "\n"
        "  N         New thread\n"
        "  R         Reply to current thread\n"
        "  F         Forward current thread\n"
        "  I         Invite a participant\n"
        "  A         Archive (modal: summary + close-outs checkbox)\n"
        "  X         Abandon (modal: reason)\n"
        "  Enter     Open selected thread\n"
        "  Tab       Cycle panes\n"
        "  Ctrl+R    Refresh inbox\n"
        "  Ctrl+Enter Send compose / submit modal\n"
        "  Esc       Cancel compose / dismiss modal\n"
        "  ?         This help\n"
        "  Ctrl+C    Quit\n"
    )

    def compose(self):
        with Vertical(id="help-modal"):
            yield Static(self.HELP_TEXT, id="help-body")


class ThreadsApp(App):
    """Three-pane email-style TUI: inbox, thread view, compose."""

    CSS_PATH = str(CSS_PATH)
    TITLE = "opc threads"

    BINDINGS = [
        Binding("ctrl+c", "quit", "Quit"),
        Binding("ctrl+r", "refresh", "Refresh"),
        Binding("tab", "focus_next", "Cycle panes"),
        Binding("n", "new_thread", "New"),
        Binding("i", "invite", "Invite"),
        Binding("a", "archive", "Archive"),
        Binding("x", "abandon", "Abandon"),
        Binding("f", "forward", "Forward"),
        Binding("question_mark", "help", "Help"),
        Binding("r", "focus_compose", "Reply"),
        Binding("ctrl+enter", "send_reply", "Send", priority=False),
        Binding("escape", "cancel_compose", "Cancel"),
    ]

    def __init__(self, *, slug: str, base_url: str, token: str) -> None:
        super().__init__()
        self._slug = slug
        self._base_url = base_url
        self._token = token
        self.sub_title = f"org: {slug}"
        self._inbox_rows: dict[str, dict] = {}
        self._current_thread_id: str | None = None
        self._client = None  # lazy AsyncOpcClient; created on first HTTP call
        self._inbox_event_task = None
        self._tail_task = None

    def compose(self):
        yield Header(show_clock=False)
        with Horizontal(id="main"):
            with Vertical(id="inbox-pane"):
                yield Static("Threads", id="inbox-title")
                yield ListView(id="inbox-list")
                yield Static("[N]ew  [F]orward  [I]nvite  [A]rchive  [X]bandon",
                             id="inbox-footer")
            with Vertical(id="right-pane"):
                yield RichLog(id="thread-view", wrap=True, highlight=False, markup=False)
                with Vertical(id="compose-pane"):
                    yield Static("Reply (To: @all) — Ctrl+Enter to send, Esc to cancel",
                                 id="compose-label")
                    yield TextArea(id="compose-body")
        yield Footer()

    async def _list_threads_impl(self, *, slug: str) -> list[dict]:
        """Real HTTP call. Overridable in tests."""
        from src.tui.api_client import AsyncOpcClient
        if self._client is None:
            self._client = AsyncOpcClient(base_url=self._base_url, token=self._token)
        return await self._client.list_threads(slug=slug)

    async def _iter_inbox_events_impl(self):
        """Yield inbox events from /threads/events SSE. Overridable in tests."""
        from src.tui.api_client import AsyncOpcClient
        if self._client is None:
            self._client = AsyncOpcClient(base_url=self._base_url, token=self._token)
        async for event in self._client.iter_sse(
            f"/api/v1/orgs/{self._slug}/threads/events"
        ):
            yield event

    async def _inbox_event_loop(self) -> None:
        try:
            async for _ in self._iter_inbox_events_impl():
                await self._refresh_inbox()
        except Exception as exc:  # noqa: BLE001
            self.notify(f"SSE inbox stream closed: {exc}", severity="warning")

    async def on_mount(self) -> None:
        await self._refresh_inbox()
        self._inbox_event_task = self.run_worker(
            self._inbox_event_loop(), exclusive=True, name="inbox_events",
        )

    async def _refresh_inbox(self) -> None:
        try:
            rows = await self._list_threads_impl(slug=self._slug)
        except Exception as exc:  # noqa: BLE001
            self.notify(f"failed to load inbox: {exc}", severity="error")
            return
        self.set_threads(rows)

    async def action_refresh(self) -> None:
        await self._refresh_inbox()
        self.notify("Inbox refreshed")

    def set_threads(self, rows: list[dict]) -> None:
        """Replace the inbox contents with the given thread rows."""
        list_view = self.query_one("#inbox-list", ListView)
        list_view.clear()
        for row in rows:
            indicator = "●" if row["status"] == "open" else "○"
            label = f"{indicator} {row['thread_id']:<8}  {row['subject'][:32]}"
            if row["status"] == "archived":
                label += "  (archived)"
            elif row["status"] == "abandoned":
                label += "  (abandoned)"
            list_view.append(ListItem(Label(label), id=f"row-{row['thread_id']}"))
        self._inbox_rows = {row["thread_id"]: row for row in rows}

    def set_thread_detail(self, thread: dict) -> None:
        """Render a thread's participants + messages into the thread pane."""
        view = self.query_one("#thread-view", RichLog)
        view.clear()
        title = f"{thread['thread_id']}  {thread['subject']}"
        view.write(f"=== {title} ===")
        view.write(f"Participants: {', '.join(thread.get('participants', []))}")
        view.write("")
        for m in thread.get("messages", []):
            ts = m.get("created_at", "")[:19]
            kind = m["kind"]
            speaker = m["speaker"]
            if kind == "message":
                addressed = m.get("addressed_to")
                head = f"{speaker} · {ts}"
                if addressed:
                    head += f"  · To: {', '.join(addressed)}"
                view.write(head)
                view.write(f"  {m.get('body_markdown', '')}")
                view.write("")
            elif kind == "decline":
                view.write(f"{speaker} · 👁 read, no reply")
                reason = m.get("decline_reason") or ""
                if reason:
                    view.write(f"  reason: {reason}")
                view.write("")
            elif kind == "system":
                payload = m.get("system_payload") or {}
                tag = payload.get("kind_tag", "system")
                if tag == "task_dispatched":
                    view.write(
                        f"─── system · {speaker} dispatched {payload.get('task_id')} "
                        f"to {payload.get('target_agent')} ───"
                    )
                elif tag == "participant_added":
                    view.write(f"─── system · {payload.get('agent_name')} added ───")
                elif tag == "archived":
                    view.write("─── system · thread archived ───")
                else:
                    view.write(f"─── system · {tag} ───")
                view.write("")
        self._current_thread_id = thread["thread_id"]

    async def _get_thread_impl(self, *, slug: str, thread_id: str) -> dict:
        """Real HTTP call. Overridable in tests."""
        from src.tui.api_client import AsyncOpcClient
        if self._client is None:
            self._client = AsyncOpcClient(base_url=self._base_url, token=self._token)
        return await self._client.get_thread(slug=slug, thread_id=thread_id)

    async def _iter_thread_tail_impl(self, thread_id: str):
        from src.tui.api_client import AsyncOpcClient
        if self._client is None:
            self._client = AsyncOpcClient(base_url=self._base_url, token=self._token)
        async for event in self._client.iter_sse(
            f"/api/v1/orgs/{self._slug}/threads/{thread_id}/tail"
        ):
            yield event

    async def _tail_loop(self, thread_id: str) -> None:
        try:
            async for _event in self._iter_thread_tail_impl(thread_id):
                if self._current_thread_id == thread_id:
                    try:
                        detail = await self._get_thread_impl(
                            slug=self._slug, thread_id=thread_id,
                        )
                    except Exception as exc:  # noqa: BLE001
                        self.notify(f"tail refresh failed: {exc}", severity="warning")
                        continue
                    self.set_thread_detail(detail)
        except Exception as exc:  # noqa: BLE001
            self.notify(f"SSE tail closed: {exc}", severity="warning")

    def action_focus_compose(self) -> None:
        if self._current_thread_id is None:
            self.notify("select a thread first", severity="warning")
            return
        self.query_one("#compose-body").focus()

    def action_cancel_compose(self) -> None:
        textarea = self.query_one("#compose-body", TextArea)
        textarea.text = ""
        self.query_one("#inbox-list").focus()

    async def _send_thread_impl(self, *, slug, thread_id, body_markdown, addressed_to):
        from src.tui.api_client import AsyncOpcClient
        if self._client is None:
            self._client = AsyncOpcClient(base_url=self._base_url, token=self._token)
        return await self._client.send_thread(
            slug=slug, thread_id=thread_id,
            body_markdown=body_markdown, addressed_to=addressed_to,
        )

    async def action_send_reply(self) -> None:
        if self._current_thread_id is None:
            self.notify("no thread selected", severity="warning")
            return
        textarea = self.query_one("#compose-body", TextArea)
        body = textarea.text.strip()
        if not body:
            self.notify("body is empty", severity="warning")
            return
        try:
            await self._send_thread_impl(
                slug=self._slug, thread_id=self._current_thread_id,
                body_markdown=body, addressed_to=["@all"],
            )
        except Exception as exc:  # noqa: BLE001
            self.notify(f"send failed: {exc}", severity="error")
            return
        textarea.text = ""
        self.notify("reply sent")

    async def _compose_thread_impl(self, **kwargs) -> dict:
        from src.tui.api_client import AsyncOpcClient
        if self._client is None:
            self._client = AsyncOpcClient(base_url=self._base_url, token=self._token)
        return await self._client.compose_thread(slug=self._slug, **kwargs)

    async def _submit_new_thread(self, result: dict | None) -> None:
        if result is None:
            return
        try:
            payload = {
                "subject": result["subject"],
                "recipients": result["recipients"],
                "body_markdown": result["body_markdown"],
                "addressed_to": ["@all"],
            }
            if result.get("forwarded_from_id"):
                payload["forwarded_from_id"] = result["forwarded_from_id"]
                payload["forwarded_from_kind"] = result["forwarded_from_kind"]
            await self._compose_thread_impl(**payload)
        except Exception as exc:  # noqa: BLE001
            self.notify(f"compose failed: {exc}", severity="error")
            return
        self.notify("thread created")
        await self._refresh_inbox()

    def action_new_thread(self) -> None:
        self.push_screen(NewThreadScreen(), self._submit_new_thread)

    def action_help(self) -> None:
        self.push_screen(HelpScreen())

    async def action_forward(self) -> None:
        if self._current_thread_id is None:
            self.notify("select a thread first", severity="warning")
            return
        try:
            detail = await self._get_thread_impl(
                slug=self._slug, thread_id=self._current_thread_id,
            )
        except Exception as exc:  # noqa: BLE001
            self.notify(f"forward failed to fetch source: {exc}", severity="error")
            return
        from src.daemon.thread_forward import build_forward_body_from_thread
        from src.models import ThreadMessage, ThreadMessageKind
        from datetime import datetime
        msgs = [
            ThreadMessage(
                thread_id=detail["thread_id"], seq=m["seq"], speaker=m["speaker"],
                kind=ThreadMessageKind(m["kind"]),
                body_markdown=m.get("body_markdown"),
                decline_reason=m.get("decline_reason"),
                system_payload=m.get("system_payload"),
                created_at=datetime.fromisoformat(m["created_at"]),
            )
            for m in detail.get("messages", [])
        ]
        quoted_body = build_forward_body_from_thread(
            source_id=detail["thread_id"], messages=msgs, subject=detail["subject"],
        )
        prefill = {
            "subject": f"Fwd: {detail['subject']}",
            "body": quoted_body,
            "forwarded_from_id": detail["thread_id"],
            "forwarded_from_kind": "thread",
        }
        self.push_screen(NewThreadScreen(prefill=prefill), self._submit_new_thread)

    async def _invite_impl(self, *, slug, thread_id, agent_name):
        from src.tui.api_client import AsyncOpcClient
        if self._client is None:
            self._client = AsyncOpcClient(base_url=self._base_url, token=self._token)
        return await self._client.invite(slug=slug, thread_id=thread_id, agent_name=agent_name)

    async def _handle_invite(self, name: str | None) -> None:
        if name is None:
            return
        try:
            await self._invite_impl(
                slug=self._slug, thread_id=self._current_thread_id,
                agent_name=name,
            )
        except Exception as exc:  # noqa: BLE001
            self.notify(f"invite failed: {exc}", severity="error")
            return
        self.notify(f"invited {name}")
        detail = await self._get_thread_impl(
            slug=self._slug, thread_id=self._current_thread_id,
        )
        self.set_thread_detail(detail)

    def action_invite(self) -> None:
        if self._current_thread_id is None:
            self.notify("select a thread first", severity="warning")
            return
        self.push_screen(InviteScreen(), self._handle_invite)

    async def _archive_impl(self, *, slug, thread_id, summary, request_close_outs):
        from src.tui.api_client import AsyncOpcClient
        if self._client is None:
            self._client = AsyncOpcClient(base_url=self._base_url, token=self._token)
        return await self._client.archive(
            slug=slug, thread_id=thread_id,
            summary=summary, request_close_outs=request_close_outs,
        )

    def action_archive(self) -> None:
        if self._current_thread_id is None:
            self.notify("select a thread first", severity="warning")
            return
        self.push_screen(ArchiveScreen(), self._handle_archive)

    async def _handle_archive(self, result: dict | None) -> None:
        if result is None:
            return
        try:
            await self._archive_impl(
                slug=self._slug, thread_id=self._current_thread_id,
                summary=result["summary"],
                request_close_outs=result["request_close_outs"],
            )
        except Exception as exc:  # noqa: BLE001
            self.notify(f"archive failed: {exc}", severity="error")
            return
        self.notify("archiving — close-outs in flight")
        await self._refresh_inbox()
        if self._current_thread_id is not None:
            detail = await self._get_thread_impl(
                slug=self._slug, thread_id=self._current_thread_id,
            )
            self.set_thread_detail(detail)

    async def _abandon_impl(self, *, slug, thread_id, reason):
        from src.tui.api_client import AsyncOpcClient
        if self._client is None:
            self._client = AsyncOpcClient(base_url=self._base_url, token=self._token)
        return await self._client.abandon(slug=slug, thread_id=thread_id, reason=reason)

    def action_abandon(self) -> None:
        if self._current_thread_id is None:
            self.notify("select a thread first", severity="warning")
            return
        self.push_screen(AbandonScreen(), self._handle_abandon)

    async def _handle_abandon(self, reason: str | None) -> None:
        if reason is None:
            return
        try:
            await self._abandon_impl(
                slug=self._slug, thread_id=self._current_thread_id, reason=reason,
            )
        except Exception as exc:  # noqa: BLE001
            self.notify(f"abandon failed: {exc}", severity="error")
            return
        self.notify("abandoned")
        await self._refresh_inbox()
        if self._current_thread_id is not None:
            detail = await self._get_thread_impl(
                slug=self._slug, thread_id=self._current_thread_id,
            )
            self.set_thread_detail(detail)

    async def on_list_view_selected(self, event) -> None:
        """ListView fires this when Enter is pressed on a row."""
        item_id = event.item.id  # "row-THR-NNN"
        if not item_id or not item_id.startswith("row-"):
            return
        thread_id = item_id[len("row-"):]
        try:
            detail = await self._get_thread_impl(slug=self._slug, thread_id=thread_id)
        except Exception as exc:  # noqa: BLE001
            self.notify(f"failed to load {thread_id}: {exc}", severity="error")
            return
        self.set_thread_detail(detail)
        # Cancel any previous tail worker; start a new one for this thread.
        if self._tail_task is not None:
            self._tail_task.cancel()
        self._tail_task = self.run_worker(
            self._tail_loop(thread_id), exclusive=True, name="thread_tail",
        )


def run(*, slug: str, base_url: str, token: str) -> int:
    """Launch the TUI for an org. Returns 0 on clean exit."""
    app = ThreadsApp(slug=slug, base_url=base_url, token=token)
    app.run()
    return 0
