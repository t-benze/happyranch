from __future__ import annotations

import pytest

from src.tui.threads_app import ThreadsApp


async def test_app_mounts_three_panes():
    app = ThreadsApp(slug="alpha", base_url="http://test", token="tok")
    async with app.run_test() as pilot:
        assert app.query_one("#inbox-pane") is not None
        assert app.query_one("#right-pane") is not None
        assert app.query_one("#inbox-footer") is not None


async def test_inbox_renders_threads_when_set():
    app = ThreadsApp(slug="alpha", base_url="http://test", token="tok")
    async with app.run_test() as pilot:
        app.set_threads([
            {"thread_id": "THR-001", "subject": "smoke", "status": "open",
             "turns_used": 0, "turn_cap": 500, "transcript_path": None,
             "started_at": "2026-05-14T00:00:00+00:00", "archived_at": None,
             "forwarded_from_id": None, "forwarded_from_kind": None,
             "summary": None, "new_kb_slugs": []},
            {"thread_id": "THR-002", "subject": "refund", "status": "archived",
             "turns_used": 5, "turn_cap": 500, "transcript_path": "/tmp/x.md",
             "started_at": "2026-05-13T00:00:00+00:00",
             "archived_at": "2026-05-13T12:00:00+00:00",
             "forwarded_from_id": None, "forwarded_from_kind": None,
             "summary": None, "new_kb_slugs": []},
        ])
        await pilot.pause()
        from textual.widgets import ListView
        list_view = app.query_one("#inbox-list", ListView)
        assert len(list_view.children) == 2
