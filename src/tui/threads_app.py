"""Textual app for the `opc threads` TUI.

Entry point: `run(slug: str) -> None` launches the app for a specific org.
The CLI's `opc threads` (no subcommand) handler calls this.
"""
from __future__ import annotations

from pathlib import Path

from textual.app import App
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Footer, Header, Static


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

    def compose(self):
        yield Header(show_clock=False)
        with Horizontal(id="main"):
            with Vertical(id="inbox-pane"):
                yield Static("Threads", id="inbox-title")
                yield Static("(no threads loaded yet)", id="inbox-list")
                yield Static("[N]ew  [F]orward  [I]nvite  [A]rchive  [X]bandon",
                             id="inbox-footer")
            with Vertical(id="right-pane"):
                yield Static("(select a thread)", id="thread-view")
                yield Static("Reply: press R", id="compose-pane")
        yield Footer()

    def action_refresh(self) -> None:
        """Placeholder — refresh wiring lands in Task 9."""
        self.notify("Refresh: not wired yet")


def run(*, slug: str, base_url: str, token: str) -> int:
    """Launch the TUI for an org. Returns 0 on clean exit."""
    app = ThreadsApp(slug=slug, base_url=base_url, token=token)
    app.run()
    return 0
