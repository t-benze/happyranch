from __future__ import annotations

import pytest

from src.tui.threads_app import ThreadsApp


async def test_app_mounts_three_panes():
    app = ThreadsApp(slug="alpha", base_url="http://test", token="tok")
    async with app.run_test() as pilot:
        assert app.query_one("#inbox-pane") is not None
        assert app.query_one("#right-pane") is not None
        assert app.query_one("#inbox-footer") is not None
