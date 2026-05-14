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


async def test_thread_view_renders_selected_thread():
    app = ThreadsApp(slug="alpha", base_url="http://test", token="tok")
    async with app.run_test() as pilot:
        app.set_threads([
            {"thread_id": "THR-001", "subject": "smoke", "status": "open",
             "turns_used": 1, "turn_cap": 500, "transcript_path": None,
             "started_at": "2026-05-14T00:00:00+00:00", "archived_at": None,
             "forwarded_from_id": None, "forwarded_from_kind": None,
             "summary": None, "new_kb_slugs": []},
        ])
        app.set_thread_detail({
            "thread_id": "THR-001", "subject": "smoke", "status": "open",
            "participants": ["dev_agent"],
            "messages": [
                {"seq": 1, "speaker": "founder", "kind": "message",
                 "body_markdown": "hi", "addressed_to": ["@all"],
                 "decline_reason": None, "system_payload": None,
                 "created_at": "2026-05-14T00:00:00+00:00"},
                {"seq": 2, "speaker": "dev_agent", "kind": "message",
                 "body_markdown": "hello back", "addressed_to": None,
                 "decline_reason": None, "system_payload": None,
                 "created_at": "2026-05-14T00:01:00+00:00"},
            ],
        })
        await pilot.pause()
        from textual.widgets import RichLog
        view = app.query_one("#thread-view", RichLog)
        # RichLog stores written lines; concat them for substring checks.
        rendered = "\n".join(str(line) for line in view.lines)
        assert "dev_agent" in rendered
        assert "hello back" in rendered
        assert "smoke" in rendered


async def test_selecting_inbox_row_fetches_and_renders(monkeypatch):
    app = ThreadsApp(slug="alpha", base_url="http://test", token="tok")
    fetched: list[str] = []

    async def fake_get_thread(*, slug, thread_id):
        fetched.append(thread_id)
        return {
            "thread_id": thread_id, "subject": "x", "status": "open",
            "participants": ["dev_agent"],
            "messages": [{
                "seq": 1, "speaker": "founder", "kind": "message",
                "body_markdown": "fetched body", "addressed_to": ["@all"],
                "decline_reason": None, "system_payload": None,
                "created_at": "2026-05-14T00:00:00+00:00",
            }],
        }

    async with app.run_test() as pilot:
        monkeypatch.setattr(app, "_get_thread_impl", fake_get_thread)
        app.set_threads([
            {"thread_id": "THR-001", "subject": "x", "status": "open",
             "turns_used": 0, "turn_cap": 500, "transcript_path": None,
             "started_at": "2026-05-14T00:00:00+00:00", "archived_at": None,
             "forwarded_from_id": None, "forwarded_from_kind": None,
             "summary": None, "new_kb_slugs": []},
        ])
        list_view = app.query_one("#inbox-list")
        list_view.focus()
        list_view.index = 0
        await pilot.press("enter")
        await pilot.pause()
        assert fetched == ["THR-001"]
        from textual.widgets import RichLog
        view = app.query_one("#thread-view", RichLog)
        rendered = "\n".join(str(line) for line in view.lines)
        assert "fetched body" in rendered
