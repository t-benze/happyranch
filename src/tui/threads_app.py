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
    ]

    def __init__(self, *, slug: str, base_url: str, token: str) -> None:
        super().__init__()
        self._slug = slug
        self._base_url = base_url
        self._token = token

    def compose(self):
        yield Header(show_clock=False)
        yield Static(f"opc threads · org: {self._slug}  (skeleton — Task 2)", id="placeholder")
        yield Footer()


def run(*, slug: str, base_url: str, token: str) -> int:
    """Launch the TUI for an org. Returns 0 on clean exit."""
    app = ThreadsApp(slug=slug, base_url=base_url, token=token)
    app.run()
    return 0
