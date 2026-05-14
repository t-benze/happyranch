"""Textual app for the `opc threads` TUI.

Entry point: `run(slug: str) -> None` launches the app for a specific org.
The CLI's `opc threads` (no subcommand) handler calls this.
"""
from __future__ import annotations

from pathlib import Path

from textual.app import App
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Footer, Header, ListItem, ListView, Label, RichLog, Static


CSS_PATH = Path(__file__).parent / "threads_app.tcss"


class ThreadsApp(App):
    """Three-pane email-style TUI: inbox, thread view, compose."""

    CSS_PATH = str(CSS_PATH)
    TITLE = "opc threads"

    BINDINGS = [
        Binding("ctrl+c", "quit", "Quit"),
        Binding("ctrl+r", "refresh", "Refresh"),
        Binding("tab", "focus_next", "Cycle panes"),
    ]

    def __init__(self, *, slug: str, base_url: str, token: str) -> None:
        super().__init__()
        self._slug = slug
        self._base_url = base_url
        self._token = token
        self.sub_title = f"org: {slug}"
        self._inbox_rows: dict[str, dict] = {}
        self._current_thread_id: str | None = None

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
                yield Static("Reply: press R", id="compose-pane")
        yield Footer()

    def action_refresh(self) -> None:
        """Placeholder — refresh wiring lands in Task 9."""
        self.notify("Refresh: not wired yet")

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


def run(*, slug: str, base_url: str, token: str) -> int:
    """Launch the TUI for an org. Returns 0 on clean exit."""
    app = ThreadsApp(slug=slug, base_url=base_url, token=token)
    app.run()
    return 0
