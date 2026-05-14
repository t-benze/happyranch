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


async def test_initial_load_populates_inbox(monkeypatch):
    app = ThreadsApp(slug="alpha", base_url="http://test", token="tok")
    listed = False

    async def fake_list_threads(*, slug, **kwargs):
        nonlocal listed
        listed = True
        return [
            {"thread_id": "THR-001", "subject": "first", "status": "open",
             "turns_used": 0, "turn_cap": 500, "transcript_path": None,
             "started_at": "2026-05-14T00:00:00+00:00", "archived_at": None,
             "forwarded_from_id": None, "forwarded_from_kind": None,
             "summary": None, "new_kb_slugs": []},
        ]

    monkeypatch.setattr(app, "_list_threads_impl", fake_list_threads)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert listed is True
        from textual.widgets import ListView
        list_view = app.query_one("#inbox-list", ListView)
        assert len(list_view.children) == 1


async def test_inbox_sse_event_triggers_refresh(monkeypatch):
    app = ThreadsApp(slug="alpha", base_url="http://test", token="tok")
    refresh_count = 0

    async def fake_list_threads(*, slug, **kwargs):
        nonlocal refresh_count
        refresh_count += 1
        return []

    async def fake_iter_events():
        yield {"thread_id": "THR-001", "event_kind": "message"}
        yield {"thread_id": "THR-002", "event_kind": "system"}

    monkeypatch.setattr(app, "_list_threads_impl", fake_list_threads)
    monkeypatch.setattr(app, "_iter_inbox_events_impl", fake_iter_events)

    async with app.run_test() as pilot:
        await pilot.pause()
        # Two SSE events → two extra refresh calls (plus 1 from on_mount).
        for _ in range(40):
            if refresh_count >= 3:
                break
            await pilot.pause()
    assert refresh_count >= 3


async def test_selecting_thread_subscribes_to_tail(monkeypatch):
    app = ThreadsApp(slug="alpha", base_url="http://test", token="tok")
    tail_started_for: list[str] = []

    async def fake_get_thread(*, slug, thread_id):
        return {
            "thread_id": thread_id, "subject": "x", "status": "open",
            "participants": ["dev_agent"], "messages": [],
        }

    async def fake_tail(thread_id: str):
        tail_started_for.append(thread_id)
        yield {"thread_id": thread_id, "seq": 99, "speaker": "dev_agent",
               "kind": "message", "preview": "live message"}

    monkeypatch.setattr(app, "_get_thread_impl", fake_get_thread)
    monkeypatch.setattr(app, "_iter_thread_tail_impl", fake_tail)
    # Stub the inbox sources so on_mount doesn't try to hit a real server.
    async def fake_list(*, slug, **kwargs): return []
    async def fake_inbox_events():
        if False: yield  # never-yielding generator
    monkeypatch.setattr(app, "_list_threads_impl", fake_list)
    monkeypatch.setattr(app, "_iter_inbox_events_impl", fake_inbox_events)

    async with app.run_test() as pilot:
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
        for _ in range(40):
            if "THR-001" in tail_started_for:
                break
            await pilot.pause()
        assert tail_started_for == ["THR-001"]
