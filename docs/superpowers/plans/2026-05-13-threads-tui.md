# Threads TUI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship `opc threads` (no subcommand) — a Textual TUI that turns the threads daemon API into an email-client-like founder workflow: inbox + thread view + compose panes, live SSE updates, keyboard-driven forward/invite/archive flows.

**Architecture:** Single Textual `App` (`src/tui/threads_app.py`) backed by an async HTTP/SSE wrapper (`src/tui/api_client.py`). The TUI is a pure consumer of the existing per-org daemon routes shipped in `docs/superpowers/plans/2026-05-13-threads-foundation.md` — no daemon-side changes. SSE keeps the inbox + active thread in sync; modals collect input for forward/invite/archive/abandon; failed sends surface as Textual notifications. `opc threads` (with no subcommand) launches the app; existing `opc threads <verb>` CLI subcommands stay intact for scripting.

**Tech Stack:** Python 3.13, [Textual](https://textual.textualize.io/) ≥ 1.0 for the UI, `httpx.AsyncClient` for HTTP + SSE, `pytest-asyncio` (already enabled) for tests, optional `pytest-textual-snapshot` for SVG snapshot comparisons.

**Spec reference:** `docs/superpowers/specs/2026-05-13-threads-design.md` §9.

**Foundation reference:** `docs/superpowers/plans/2026-05-13-threads-foundation.md` — this plan assumes that one has landed. The HTTP endpoints, SSE topics, and CLI subcommands referenced below all exist after the foundation merges.

**Out of scope:**
- New daemon endpoints (everything we need is already there).
- Persistence of TUI state (window size, last-selected thread). Defer.
- Search / full-text indexing of threads.
- Editing or deleting messages.
- Mouse interactions beyond what Textual gives us for free.

---

## File structure

**Create:**
- `src/tui/__init__.py` — empty package marker.
- `src/tui/api_client.py` — `AsyncOpcClient` (httpx.AsyncClient wrapper) + `iter_sse(url)` async generator.
- `src/tui/threads_app.py` — the `ThreadsApp(App)` class, screens, widgets, modals.
- `src/tui/threads_app.tcss` — Textual CSS for the three-pane layout.
- `tests/tui/__init__.py`
- `tests/tui/test_api_client.py` — unit tests for `AsyncOpcClient` against a stub httpx transport.
- `tests/tui/test_threads_app.py` — Textual `App.run_test()` pilot tests for keybindings, modals, and live-update wiring.

**Modify:**
- `pyproject.toml` — add `textual` to dependencies.
- `src/cli.py` — wire `opc threads` (no subcommand) to launch the TUI; keep all existing `opc threads <verb>` subcommands working.
- `README.md` — document the TUI launch.
- `CLAUDE.md` — note item 12 is now fully complete.

**Conventions:**
- All new code uses `from __future__ import annotations`.
- HTTP calls go through `AsyncOpcClient` — no direct httpx in the App class.
- CSS in `.tcss` (Textual CSS) keeps layout out of the Python.
- Pilot tests verify behavior; snapshot tests are optional and only worth it for stable layouts.
- Commits use conventional shape (`feat(tui):`, `test(tui):`, `chore(tui):`).

---

## Task 1: Add Textual dependency

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Confirm current dependency block**

Run: `grep -nE '\[project\]|dependencies =|^dependencies' pyproject.toml | head -10`

Expected: a `[project]` or `[tool.poetry]`/`[tool.uv]` section with a list of `dependencies = [...]`.

- [ ] **Step 2: Add `textual`**

Open `pyproject.toml`. In the dependency list (the same place `httpx`, `fastapi`, `pydantic` are declared), add:

```toml
    "textual>=1.0,<3",
```

Match the surrounding quote style (double quotes per PEP 508) and trailing-comma convention used by other entries.

- [ ] **Step 3: Sync the lockfile**

Run: `uv sync 2>&1 | tail -5`

Expected: textual is installed, no errors. If your environment uses `uv pip install -e .` or another flow, run that — the goal is `import textual` works.

- [ ] **Step 4: Smoke-import Textual**

Run: `uv run python -c "from textual.app import App; print(App)"`

Expected: prints `<class 'textual.app.App'>`. If it doesn't, stop and report.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "chore(deps): add textual for threads TUI"
```

---

## Task 2: Package skeleton

**Files:**
- Create: `src/tui/__init__.py`
- Create: `src/tui/threads_app.py`
- Create: `src/tui/threads_app.tcss`
- Create: `tests/tui/__init__.py`

- [ ] **Step 1: Create empty package files**

```bash
mkdir -p src/tui tests/tui
```

Write `src/tui/__init__.py`:

```python
"""Textual TUI for OPC. Currently: threads (email-style multi-agent workchannel)."""
```

Write `tests/tui/__init__.py`:

```python
```

(empty file)

- [ ] **Step 2: Stub `threads_app.py`**

Write `src/tui/threads_app.py`:

```python
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
```

- [ ] **Step 3: Empty stylesheet**

Write `src/tui/threads_app.tcss`:

```css
/* Layout for the threads TUI. Three panes — see Task 5 for the real grid. */

#placeholder {
    padding: 1 2;
    color: $text-muted;
}
```

- [ ] **Step 4: Verify import**

Run: `uv run python -c "from src.tui.threads_app import ThreadsApp; print(ThreadsApp)"`

Expected: prints the class. No tracebacks.

- [ ] **Step 5: Commit**

```bash
git add src/tui/__init__.py src/tui/threads_app.py src/tui/threads_app.tcss tests/tui/__init__.py
git commit -m "feat(tui): scaffold ThreadsApp + stylesheet"
```

---

## Task 3: AsyncOpcClient — HTTP wrapper

**Files:**
- Create: `src/tui/api_client.py`
- Create: `tests/tui/test_api_client.py`

The existing `OpcClient` in `src/client/client.py` is synchronous (uses `httpx.Client`). The TUI needs the async equivalent so it can call into the daemon without blocking the UI loop. This task only covers HTTP — SSE comes in Task 4.

- [ ] **Step 1: Failing test**

Write `tests/tui/test_api_client.py`:

```python
from __future__ import annotations

import httpx
import pytest

from src.tui.api_client import AsyncOpcClient


@pytest.fixture
def mock_transport() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/v1/orgs/alpha/threads" and request.method == "GET":
            return httpx.Response(200, json={"threads": [
                {"thread_id": "THR-001", "subject": "smoke", "status": "open",
                 "started_at": "2026-05-14T00:00:00+00:00", "archived_at": None,
                 "forwarded_from_id": None, "forwarded_from_kind": None,
                 "turn_cap": 500, "turns_used": 0, "summary": None,
                 "new_kb_slugs": [], "transcript_path": None},
            ]})
        if path == "/api/v1/orgs/alpha/threads/THR-001" and request.method == "GET":
            return httpx.Response(200, json={
                "thread_id": "THR-001", "subject": "smoke", "status": "open",
                "started_at": "2026-05-14T00:00:00+00:00", "archived_at": None,
                "forwarded_from_id": None, "forwarded_from_kind": None,
                "turn_cap": 500, "turns_used": 0, "summary": None,
                "new_kb_slugs": [], "transcript_path": None,
                "participants": ["dev_agent"],
                "messages": [{
                    "seq": 1, "speaker": "founder", "kind": "message",
                    "body_markdown": "hi", "addressed_to": ["@all"],
                    "decline_reason": None, "system_payload": None,
                    "created_at": "2026-05-14T00:00:00+00:00",
                }],
            })
        return httpx.Response(404)
    return httpx.MockTransport(handler)


async def test_list_threads_returns_typed_rows(mock_transport):
    client = AsyncOpcClient(base_url="http://test", token="tok", transport=mock_transport)
    try:
        rows = await client.list_threads(slug="alpha")
    finally:
        await client.aclose()
    assert len(rows) == 1
    assert rows[0]["thread_id"] == "THR-001"
    assert rows[0]["subject"] == "smoke"


async def test_get_thread_returns_messages(mock_transport):
    client = AsyncOpcClient(base_url="http://test", token="tok", transport=mock_transport)
    try:
        thread = await client.get_thread(slug="alpha", thread_id="THR-001")
    finally:
        await client.aclose()
    assert thread["participants"] == ["dev_agent"]
    assert thread["messages"][0]["body_markdown"] == "hi"


async def test_unknown_thread_raises(mock_transport):
    client = AsyncOpcClient(base_url="http://test", token="tok", transport=mock_transport)
    try:
        with pytest.raises(httpx.HTTPStatusError):
            await client.get_thread(slug="alpha", thread_id="THR-404")
    finally:
        await client.aclose()
```

- [ ] **Step 2: Run, verify failure**

Run: `uv run pytest tests/tui/test_api_client.py -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'src.tui.api_client'`.

- [ ] **Step 3: Implement HTTP surface**

Write `src/tui/api_client.py`:

```python
"""Async HTTP + SSE client for the threads TUI.

Mirrors the sync `OpcClient` in `src/client/client.py` for the subset of
endpoints the TUI needs. Methods return raw dicts (no Pydantic decoding) —
the TUI re-shapes them into Textual rows directly.
"""
from __future__ import annotations

from typing import Any

import httpx


class AsyncOpcClient:
    def __init__(
        self,
        *,
        base_url: str,
        token: str,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=30.0,
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    # -- Threads ---------------------------------------------------------

    async def list_threads(
        self, *, slug: str, status: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"limit": limit}
        if status:
            params["status"] = status
        r = await self._client.get(f"/api/v1/orgs/{slug}/threads", params=params)
        r.raise_for_status()
        return r.json()["threads"]

    async def get_thread(self, *, slug: str, thread_id: str) -> dict[str, Any]:
        r = await self._client.get(f"/api/v1/orgs/{slug}/threads/{thread_id}")
        r.raise_for_status()
        return r.json()

    async def compose_thread(
        self,
        *,
        slug: str,
        subject: str,
        recipients: list[str],
        body_markdown: str,
        addressed_to: list[str],
        forwarded_from_id: str | None = None,
        forwarded_from_kind: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "subject": subject,
            "recipients": recipients,
            "body_markdown": body_markdown,
            "addressed_to": addressed_to,
        }
        if forwarded_from_id is not None:
            payload["forwarded_from_id"] = forwarded_from_id
            payload["forwarded_from_kind"] = forwarded_from_kind
        r = await self._client.post(f"/api/v1/orgs/{slug}/threads", json=payload)
        r.raise_for_status()
        return r.json()

    async def send_thread(
        self, *, slug: str, thread_id: str, body_markdown: str, addressed_to: list[str],
    ) -> dict[str, Any]:
        r = await self._client.post(
            f"/api/v1/orgs/{slug}/threads/{thread_id}/send",
            json={"body_markdown": body_markdown, "addressed_to": addressed_to},
        )
        r.raise_for_status()
        return r.json()

    async def invite(self, *, slug: str, thread_id: str, agent_name: str) -> dict[str, Any]:
        r = await self._client.post(
            f"/api/v1/orgs/{slug}/threads/{thread_id}/invite",
            json={"agent_name": agent_name},
        )
        r.raise_for_status()
        return r.json()

    async def archive(
        self, *, slug: str, thread_id: str, summary: str, request_close_outs: bool,
    ) -> dict[str, Any]:
        r = await self._client.post(
            f"/api/v1/orgs/{slug}/threads/{thread_id}/archive",
            json={"summary": summary, "request_close_outs": request_close_outs},
        )
        r.raise_for_status()
        return r.json()

    async def abandon(self, *, slug: str, thread_id: str, reason: str) -> dict[str, Any]:
        r = await self._client.post(
            f"/api/v1/orgs/{slug}/threads/{thread_id}/abandon",
            json={"reason": reason},
        )
        r.raise_for_status()
        return r.json()

    async def extend(self, *, slug: str, thread_id: str, new_cap: int) -> dict[str, Any]:
        r = await self._client.post(
            f"/api/v1/orgs/{slug}/threads/{thread_id}/extend",
            json={"new_cap": new_cap},
        )
        r.raise_for_status()
        return r.json()
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/tui/test_api_client.py -v`

Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/tui/api_client.py tests/tui/test_api_client.py
git commit -m "feat(tui): AsyncOpcClient HTTP surface (list/get/compose/send/...)"
```

---

## Task 4: AsyncOpcClient — SSE subscriber

**Files:**
- Modify: `src/tui/api_client.py`
- Modify: `tests/tui/test_api_client.py`

- [ ] **Step 1: Failing test**

Append to `tests/tui/test_api_client.py`:

```python
async def test_iter_sse_yields_data_events():
    async def handler(request: httpx.Request) -> httpx.Response:
        # SSE: two events then end of stream.
        body = (
            "data: {\"thread_id\": \"THR-001\", \"seq\": 1, \"speaker\": \"founder\", \"kind\": \"message\"}\n\n"
            "data: {\"thread_id\": \"THR-001\", \"seq\": 2, \"speaker\": \"alice\", \"kind\": \"message\"}\n\n"
        )
        return httpx.Response(200, content=body.encode(), headers={"content-type": "text/event-stream"})

    transport = httpx.MockTransport(handler)
    client = AsyncOpcClient(base_url="http://test", token="tok", transport=transport)
    try:
        seen: list[dict] = []
        async for event in client.iter_sse(
            f"/api/v1/orgs/alpha/threads/THR-001/tail"
        ):
            seen.append(event)
            if len(seen) >= 2:
                break
    finally:
        await client.aclose()
    assert seen[0]["seq"] == 1
    assert seen[1]["speaker"] == "alice"
```

- [ ] **Step 2: Run, verify failure**

Run: `uv run pytest tests/tui/test_api_client.py::test_iter_sse_yields_data_events -v`

Expected: FAIL — `iter_sse` not defined.

- [ ] **Step 3: Implement**

Append to `src/tui/api_client.py`:

```python
import json


    async def iter_sse(self, path: str):
        """Yield parsed JSON event payloads from an SSE endpoint.

        Each event is the JSON-decoded `data:` line. Lines starting with
        `event:`, `id:`, `retry:`, or `:` (comment) are ignored. Empty lines
        delimit events.

        Caller breaks out of the loop to stop; this method does not close
        the underlying connection until the generator is garbage-collected
        or the caller calls aclose().
        """
        async with self._client.stream("GET", path) as response:
            response.raise_for_status()
            async for raw_line in response.aiter_lines():
                if not raw_line:
                    continue
                if not raw_line.startswith("data:"):
                    continue
                payload = raw_line[len("data:"):].strip()
                if not payload:
                    continue
                try:
                    yield json.loads(payload)
                except json.JSONDecodeError:
                    # Malformed event — skip silently. The daemon only emits
                    # JSON, so this only fires for proxy mangling.
                    continue
```

(Put `import json` at the top of the file with the other imports if not already there.)

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/tui/test_api_client.py -v`

Expected: all 4 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/tui/api_client.py tests/tui/test_api_client.py
git commit -m "feat(tui): iter_sse async generator for thread events"
```

---

## Task 5: App layout — three-pane shell

**Files:**
- Modify: `src/tui/threads_app.py`
- Modify: `src/tui/threads_app.tcss`

This task lands the visual skeleton — three panes wired into Textual's grid. No data yet; panes show placeholders.

- [ ] **Step 1: Replace `compose()` with the three-pane layout**

In `src/tui/threads_app.py`, replace the existing class body's `compose` method with:

```python
from textual.containers import Container, Horizontal, Vertical
from textual.widgets import Footer, Header, Static


class ThreadsApp(App):
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
```

(Make sure the existing `Static`, `Horizontal`, `Vertical`, `Container` imports are present; remove the old placeholder `Static`.)

- [ ] **Step 2: Replace the stylesheet**

Overwrite `src/tui/threads_app.tcss`:

```css
Screen {
    layout: vertical;
}

#main {
    height: 1fr;
}

#inbox-pane {
    width: 40;
    border: round $accent;
    padding: 0 1;
}

#inbox-title {
    text-style: bold;
    padding: 0 0 1 0;
}

#inbox-list {
    height: 1fr;
}

#inbox-footer {
    dock: bottom;
    color: $text-muted;
}

#right-pane {
    width: 1fr;
    border: round $accent;
    padding: 0 1;
}

#thread-view {
    height: 1fr;
    overflow-y: auto;
}

#compose-pane {
    dock: bottom;
    height: 5;
    border-top: solid $accent;
    padding: 0 1;
    color: $text-muted;
}
```

- [ ] **Step 3: Smoke-test with Textual's run_test pilot**

Append to a NEW file `tests/tui/test_threads_app.py`:

```python
from __future__ import annotations

import pytest

from src.tui.threads_app import ThreadsApp


async def test_app_mounts_three_panes():
    app = ThreadsApp(slug="alpha", base_url="http://test", token="tok")
    async with app.run_test() as pilot:
        assert app.query_one("#inbox-pane") is not None
        assert app.query_one("#right-pane") is not None
        assert app.query_one("#inbox-footer") is not None
```

- [ ] **Step 4: Run**

Run: `uv run pytest tests/tui/test_threads_app.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/tui/threads_app.py src/tui/threads_app.tcss tests/tui/test_threads_app.py
git commit -m "feat(tui): three-pane layout skeleton with Textual CSS"
```

---

## Task 6: Inbox pane — render thread rows

**Files:**
- Modify: `src/tui/threads_app.py`
- Modify: `src/tui/threads_app.tcss`
- Modify: `tests/tui/test_threads_app.py`

Replace the inbox placeholder with a real `ListView` of thread rows.

- [ ] **Step 1: Failing test**

Append to `tests/tui/test_threads_app.py`:

```python
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
        list_view = app.query_one("#inbox-list")
        # The rendered list should contain both thread IDs.
        text = list_view.render().__str__() if hasattr(list_view, "render") else str(list_view)
        # Less brittle: query the underlying ListView for items.
        from textual.widgets import ListView
        if isinstance(list_view, ListView):
            assert len(list_view.children) == 2
        else:
            assert "THR-001" in text
            assert "THR-002" in text
```

- [ ] **Step 2: Run, verify failure**

Run: `uv run pytest tests/tui/test_threads_app.py::test_inbox_renders_threads_when_set -v`

Expected: FAIL — `set_threads` doesn't exist.

- [ ] **Step 3: Replace the inbox `Static` with a `ListView`**

In `src/tui/threads_app.py`, update imports and the `compose` method:

```python
from textual.widgets import Footer, Header, ListItem, ListView, Label, Static
```

Replace the line `yield Static("(no threads loaded yet)", id="inbox-list")` with:

```python
            yield ListView(id="inbox-list")
```

Add a `set_threads` method on the App class:

```python
    def set_threads(self, rows: list[dict]) -> None:
        """Replace the inbox contents with the given thread rows.

        Called by the SSE wiring (Task 10) and on initial load (Task 9).
        Idempotent — re-calling with the same rows replaces the list.
        """
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
        # Track the underlying data so handlers can look up the selected row.
        self._inbox_rows = {row["thread_id"]: row for row in rows}
```

Add `self._inbox_rows: dict[str, dict] = {}` to `__init__`.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/tui/test_threads_app.py -v`

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/tui/threads_app.py src/tui/threads_app.tcss tests/tui/test_threads_app.py
git commit -m "feat(tui): inbox ListView with status indicators"
```

---

## Task 7: Thread pane — render the selected thread's messages

**Files:**
- Modify: `src/tui/threads_app.py`
- Modify: `tests/tui/test_threads_app.py`

When the founder presses Enter on an inbox item, the right pane shows that thread's participants + messages.

- [ ] **Step 1: Failing test**

Append:

```python
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
        view = app.query_one("#thread-view")
        rendered = view.renderable.__str__() if hasattr(view, "renderable") else str(view)
        assert "dev_agent" in rendered
        assert "hello back" in rendered
        assert "smoke" in rendered
```

- [ ] **Step 2: Run, verify failure**

Run: `uv run pytest tests/tui/test_threads_app.py::test_thread_view_renders_selected_thread -v`

Expected: FAIL — `set_thread_detail` doesn't exist.

- [ ] **Step 3: Implement `set_thread_detail`**

Replace the `#thread-view` Static with a `RichLog` (or keep `Static` and update text). RichLog is friendlier for scrolling.

In imports:

```python
from textual.widgets import RichLog
```

Replace `yield Static("(select a thread)", id="thread-view")` with:

```python
            yield RichLog(id="thread-view", wrap=True, highlight=False, markup=False)
```

Add the method:

```python
    def set_thread_detail(self, thread: dict) -> None:
        """Render a thread's participants + messages into the thread pane."""
        view = self.query_one("#thread-view", RichLog)
        view.clear()
        title = f"{thread['thread_id']}  {thread['subject']}"
        view.write(f"=== {title} ===")
        view.write(f"Participants: {', '.join(thread.get('participants', []))}")
        view.write("")
        for m in thread.get("messages", []):
            ts = m.get("created_at", "")[:19]  # YYYY-MM-DDTHH:MM:SS
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
```

Add `self._current_thread_id: str | None = None` to `__init__`.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/tui/test_threads_app.py -v`

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/tui/threads_app.py tests/tui/test_threads_app.py
git commit -m "feat(tui): RichLog thread view rendering messages/declines/system"
```

---

## Task 8: Inbox → thread-view selection wiring

**Files:**
- Modify: `src/tui/threads_app.py`
- Modify: `tests/tui/test_threads_app.py`

Pressing Enter on an inbox row fetches that thread's detail and renders it.

- [ ] **Step 1: Failing test**

Append:

```python
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
        # Patch the client's get_thread so we don't hit a real daemon.
        monkeypatch.setattr(app, "_get_thread_impl", fake_get_thread)
        app.set_threads([
            {"thread_id": "THR-001", "subject": "x", "status": "open",
             "turns_used": 0, "turn_cap": 500, "transcript_path": None,
             "started_at": "2026-05-14T00:00:00+00:00", "archived_at": None,
             "forwarded_from_id": None, "forwarded_from_kind": None,
             "summary": None, "new_kb_slugs": []},
        ])
        # Simulate selecting the row + pressing enter.
        list_view = app.query_one("#inbox-list")
        list_view.focus()
        list_view.index = 0
        await pilot.press("enter")
        await pilot.pause()
        assert fetched == ["THR-001"]
        # Body should be in the thread view.
        view = app.query_one("#thread-view")
        rendered = view.renderable.__str__() if hasattr(view, "renderable") else str(view)
        assert "fetched body" in rendered
```

- [ ] **Step 2: Run, verify failure**

Run: `uv run pytest tests/tui/test_threads_app.py::test_selecting_inbox_row_fetches_and_renders -v`

Expected: FAIL — `_get_thread_impl` doesn't exist; ListView selection handler not wired.

- [ ] **Step 3: Wire the selection handler**

In `src/tui/threads_app.py`, add inside the class:

```python
    async def _get_thread_impl(self, *, slug: str, thread_id: str) -> dict:
        """Real HTTP call. Overridable in tests."""
        from src.tui.api_client import AsyncOpcClient
        if self._client is None:
            self._client = AsyncOpcClient(base_url=self._base_url, token=self._token)
        return await self._client.get_thread(slug=slug, thread_id=thread_id)

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
```

Add to `__init__`:

```python
        self._client: "AsyncOpcClient | None" = None  # lazy; created on first HTTP call
```

(Forward reference `"AsyncOpcClient"` keeps the top-of-file imports lazy. If you prefer eager: `from src.tui.api_client import AsyncOpcClient` at the top and drop the quotes.)

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/tui/test_threads_app.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/tui/threads_app.py tests/tui/test_threads_app.py
git commit -m "feat(tui): inbox→thread-view fetch on Enter"
```

---

## Task 9: Initial load — fetch + populate inbox on mount

**Files:**
- Modify: `src/tui/threads_app.py`
- Modify: `tests/tui/test_threads_app.py`

App mount triggers an initial inbox fetch.

- [ ] **Step 1: Failing test**

Append:

```python
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
```

- [ ] **Step 2: Run, verify failure**

Run: `uv run pytest tests/tui/test_threads_app.py::test_initial_load_populates_inbox -v`

Expected: FAIL.

- [ ] **Step 3: Implement**

In the App class, add:

```python
    async def _list_threads_impl(self, *, slug: str) -> list[dict]:
        """Real HTTP call. Overridable in tests."""
        from src.tui.api_client import AsyncOpcClient
        if self._client is None:
            self._client = AsyncOpcClient(base_url=self._base_url, token=self._token)
        return await self._client.list_threads(slug=slug)

    async def on_mount(self) -> None:
        await self._refresh_inbox()

    async def _refresh_inbox(self) -> None:
        try:
            rows = await self._list_threads_impl(slug=self._slug)
        except Exception as exc:  # noqa: BLE001
            self.notify(f"failed to load inbox: {exc}", severity="error")
            return
        self.set_threads(rows)
```

Update the `action_refresh` placeholder to call into `_refresh_inbox`:

```python
    async def action_refresh(self) -> None:
        await self._refresh_inbox()
        self.notify("Inbox refreshed")
```

(Note the `async` — Textual supports async actions.)

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/tui/test_threads_app.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/tui/threads_app.py tests/tui/test_threads_app.py
git commit -m "feat(tui): initial inbox fetch on mount + Ctrl+R refresh"
```

---

## Task 10: SSE inbox-events live updates

**Files:**
- Modify: `src/tui/threads_app.py`
- Modify: `tests/tui/test_threads_app.py`

The TUI subscribes to `/threads/events` (org-wide SSE) and refreshes the inbox on each event.

- [ ] **Step 1: Failing test**

Append:

```python
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
        # Two SSE events → two refresh calls (debouncing not yet — see Task 18).
        # Allow a small window for the background task to drain.
        for _ in range(20):
            if refresh_count >= 3:  # 1 initial + 2 from SSE
                break
            await pilot.pause()
    assert refresh_count >= 3
```

- [ ] **Step 2: Run, verify failure**

Run: `uv run pytest tests/tui/test_threads_app.py::test_inbox_sse_event_triggers_refresh -v`

Expected: FAIL.

- [ ] **Step 3: Implement**

In the App class:

```python
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
```

And in `on_mount`, kick off the worker:

```python
    async def on_mount(self) -> None:
        await self._refresh_inbox()
        self._inbox_event_task = self.run_worker(self._inbox_event_loop(), exclusive=True)
```

Add `self._inbox_event_task = None` to `__init__`.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/tui/test_threads_app.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/tui/threads_app.py tests/tui/test_threads_app.py
git commit -m "feat(tui): inbox SSE worker triggers refresh on each event"
```

---

## Task 11: Per-thread SSE tail wiring

**Files:**
- Modify: `src/tui/threads_app.py`
- Modify: `tests/tui/test_threads_app.py`

When a thread is selected, subscribe to its `/threads/{id}/tail` stream so new messages appear live.

- [ ] **Step 1: Failing test**

Append:

```python
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
        # Pretend a single live message arrives.
        yield {"thread_id": thread_id, "seq": 99, "speaker": "dev_agent",
               "kind": "message", "preview": "live message"}

    monkeypatch.setattr(app, "_get_thread_impl", fake_get_thread)
    monkeypatch.setattr(app, "_iter_thread_tail_impl", fake_tail)

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
        # Tail should have been started for THR-001.
        for _ in range(20):
            if "THR-001" in tail_started_for:
                break
            await pilot.pause()
        assert tail_started_for == ["THR-001"]
```

- [ ] **Step 2: Run, verify failure**

Run: `uv run pytest tests/tui/test_threads_app.py::test_selecting_thread_subscribes_to_tail -v`

Expected: FAIL.

- [ ] **Step 3: Implement**

In the App class:

```python
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
                # On each event, re-fetch the thread detail so the view stays
                # canonical. Cheap and correct; later optimization could
                # append in place.
                if self._current_thread_id == thread_id:
                    try:
                        detail = await self._get_thread_impl(slug=self._slug, thread_id=thread_id)
                    except Exception as exc:  # noqa: BLE001
                        self.notify(f"tail refresh failed: {exc}", severity="warning")
                        continue
                    self.set_thread_detail(detail)
        except Exception as exc:  # noqa: BLE001
            self.notify(f"SSE tail closed: {exc}", severity="warning")
```

In `on_list_view_selected`, after `self.set_thread_detail(detail)`, add:

```python
        # Cancel any previous tail worker; start a new one for this thread.
        if getattr(self, "_tail_task", None) is not None:
            self._tail_task.cancel()
        self._tail_task = self.run_worker(self._tail_loop(thread_id), exclusive=True, name="thread_tail")
```

Add `self._tail_task = None` to `__init__`.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/tui/test_threads_app.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/tui/threads_app.py tests/tui/test_threads_app.py
git commit -m "feat(tui): per-thread tail SSE keeps active view live"
```

---

## Task 12: Reply compose pane

**Files:**
- Modify: `src/tui/threads_app.py`
- Modify: `src/tui/threads_app.tcss`
- Modify: `tests/tui/test_threads_app.py`

Press `R` to focus the compose pane; type a body; `Ctrl+Enter` posts via `/send`.

- [ ] **Step 1: Failing test**

Append:

```python
async def test_reply_posts_send_request(monkeypatch):
    app = ThreadsApp(slug="alpha", base_url="http://test", token="tok")
    sent: list[dict] = []

    async def fake_send_thread(*, slug, thread_id, body_markdown, addressed_to):
        sent.append({"thread_id": thread_id, "body_markdown": body_markdown,
                     "addressed_to": addressed_to})
        return {"thread_id": thread_id, "seq": 5, "pending_replies": []}

    async def fake_get_thread(*, slug, thread_id):
        return {
            "thread_id": thread_id, "subject": "x", "status": "open",
            "participants": ["dev_agent"], "messages": [],
        }

    monkeypatch.setattr(app, "_send_thread_impl", fake_send_thread)
    monkeypatch.setattr(app, "_get_thread_impl", fake_get_thread)

    async with app.run_test() as pilot:
        app.set_threads([
            {"thread_id": "THR-001", "subject": "x", "status": "open",
             "turns_used": 0, "turn_cap": 500, "transcript_path": None,
             "started_at": "2026-05-14T00:00:00+00:00", "archived_at": None,
             "forwarded_from_id": None, "forwarded_from_kind": None,
             "summary": None, "new_kb_slugs": []},
        ])
        # Select the thread first.
        list_view = app.query_one("#inbox-list")
        list_view.focus()
        list_view.index = 0
        await pilot.press("enter")
        await pilot.pause()
        # Focus compose.
        await pilot.press("r")
        textarea = app.query_one("#compose-body")
        textarea.text = "follow-up"
        # Send.
        await pilot.press("ctrl+enter")
        await pilot.pause()
    assert sent == [{"thread_id": "THR-001",
                     "body_markdown": "follow-up",
                     "addressed_to": ["@all"]}]
```

- [ ] **Step 2: Run, verify failure**

Run: `uv run pytest tests/tui/test_threads_app.py::test_reply_posts_send_request -v`

Expected: FAIL.

- [ ] **Step 3: Replace compose-pane Static with a real TextArea**

In `compose()`:

```python
            from textual.widgets import TextArea
            with Vertical(id="compose-pane"):
                yield Static("Reply (To: @all) — Ctrl+Enter to send, Esc to cancel",
                             id="compose-label")
                yield TextArea(id="compose-body")
```

Add bindings:

```python
    BINDINGS = [
        Binding("ctrl+c", "quit", "Quit"),
        Binding("ctrl+r", "refresh", "Refresh"),
        Binding("tab", "focus_next", "Cycle panes"),
        Binding("r", "focus_compose", "Reply"),
        Binding("ctrl+enter", "send_reply", "Send"),
        Binding("escape", "cancel_compose", "Cancel"),
    ]
```

Add handlers:

```python
    def action_focus_compose(self) -> None:
        if self._current_thread_id is None:
            self.notify("select a thread first", severity="warning")
            return
        self.query_one("#compose-body").focus()

    def action_cancel_compose(self) -> None:
        textarea = self.query_one("#compose-body")
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
        textarea = self.query_one("#compose-body")
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
```

Update the stylesheet for the new TextArea height:

In `threads_app.tcss`, replace the `#compose-pane` block:

```css
#compose-pane {
    dock: bottom;
    height: 8;
    border-top: solid $accent;
    padding: 0 1;
}

#compose-label {
    color: $text-muted;
    height: 1;
}

#compose-body {
    height: 1fr;
}
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/tui/test_threads_app.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/tui/threads_app.py src/tui/threads_app.tcss tests/tui/test_threads_app.py
git commit -m "feat(tui): R focuses compose, Ctrl+Enter posts /send"
```

---

## Task 13: New thread compose (full screen)

**Files:**
- Modify: `src/tui/threads_app.py`
- Modify: `tests/tui/test_threads_app.py`

Press `N` to open a full-screen compose modal: subject + recipients (comma-separated) + body.

- [ ] **Step 1: Failing test**

Append:

```python
async def test_new_thread_submits_via_modal(monkeypatch):
    app = ThreadsApp(slug="alpha", base_url="http://test", token="tok")
    composed: list[dict] = []

    async def fake_compose(**kwargs):
        composed.append(kwargs)
        return {"thread_id": "THR-NEW", "started_at": "now", "pending_replies": []}

    monkeypatch.setattr(app, "_compose_thread_impl", fake_compose)

    async with app.run_test() as pilot:
        await pilot.press("n")
        await pilot.pause()
        # Modal screen mounted.
        modal_subject = app.query_one("#new-subject")
        modal_subject.value = "smoke"
        modal_recipients = app.query_one("#new-recipients")
        modal_recipients.value = "dev_agent,qa_engineer"
        modal_body = app.query_one("#new-body")
        modal_body.text = "say hi to both of you"
        # Submit (button id "new-submit" or Ctrl+Enter inside the modal).
        await pilot.press("ctrl+enter")
        await pilot.pause()
    assert len(composed) == 1
    assert composed[0]["subject"] == "smoke"
    assert composed[0]["recipients"] == ["dev_agent", "qa_engineer"]
    assert composed[0]["body_markdown"] == "say hi to both of you"
    assert composed[0]["addressed_to"] == ["@all"]
```

- [ ] **Step 2: Run, verify failure**

Run: `uv run pytest tests/tui/test_threads_app.py::test_new_thread_submits_via_modal -v`

Expected: FAIL.

- [ ] **Step 3: Add a NewThreadScreen modal**

In `src/tui/threads_app.py`, define a new screen class:

```python
from textual.screen import ModalScreen
from textual.containers import Vertical
from textual.widgets import Button, Input, TextArea


class NewThreadScreen(ModalScreen[dict | None]):
    """Modal that collects subject/recipients/body for a new thread."""

    BINDINGS = [
        Binding("escape", "dismiss(None)", "Cancel"),
        Binding("ctrl+enter", "submit", "Send"),
    ]

    def compose(self):
        with Vertical(id="new-thread-modal"):
            yield Static("New thread — Ctrl+Enter to send, Esc to cancel", id="new-label")
            yield Input(placeholder="Subject", id="new-subject")
            yield Input(placeholder="Recipients (comma-separated)", id="new-recipients")
            yield TextArea(id="new-body")
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
        self.dismiss({"subject": subject, "recipients": recipients, "body_markdown": body})

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "new-submit":
            self.action_submit()
```

On the App class, add:

```python
    BINDINGS = [
        # ... existing ...
        Binding("n", "new_thread", "New"),
    ]

    async def _compose_thread_impl(self, **kwargs) -> dict:
        from src.tui.api_client import AsyncOpcClient
        if self._client is None:
            self._client = AsyncOpcClient(base_url=self._base_url, token=self._token)
        return await self._client.compose_thread(slug=self._slug, **kwargs)

    async def action_new_thread(self) -> None:
        result = await self.push_screen_wait(NewThreadScreen())
        if result is None:
            return
        try:
            await self._compose_thread_impl(
                subject=result["subject"],
                recipients=result["recipients"],
                body_markdown=result["body_markdown"],
                addressed_to=["@all"],
            )
        except Exception as exc:  # noqa: BLE001
            self.notify(f"compose failed: {exc}", severity="error")
            return
        self.notify("thread created")
        await self._refresh_inbox()
```

Add CSS for the modal:

```css
#new-thread-modal {
    width: 80;
    height: 24;
    background: $surface;
    border: round $accent;
    padding: 1 2;
}

#new-label {
    color: $text-muted;
    margin-bottom: 1;
}

#new-subject, #new-recipients {
    margin-bottom: 1;
}

#new-body {
    height: 10;
    margin-bottom: 1;
}
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/tui/test_threads_app.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/tui/threads_app.py src/tui/threads_app.tcss tests/tui/test_threads_app.py
git commit -m "feat(tui): N opens NewThreadScreen modal"
```

---

## Task 14: Invite modal (I key)

**Files:**
- Modify: `src/tui/threads_app.py`
- Modify: `tests/tui/test_threads_app.py`

`I` opens a tiny modal asking for an agent name; submits via `/invite`.

- [ ] **Step 1: Failing test**

Append:

```python
async def test_invite_modal_invokes_invite(monkeypatch):
    app = ThreadsApp(slug="alpha", base_url="http://test", token="tok")
    invited: list[dict] = []

    async def fake_invite(*, slug, thread_id, agent_name):
        invited.append({"thread_id": thread_id, "agent_name": agent_name})
        return {"thread_id": thread_id, "agent_name": agent_name, "system_message_seq": 5}

    async def fake_get_thread(*, slug, thread_id):
        return {"thread_id": thread_id, "subject": "x", "status": "open",
                "participants": ["dev_agent"], "messages": []}

    monkeypatch.setattr(app, "_invite_impl", fake_invite)
    monkeypatch.setattr(app, "_get_thread_impl", fake_get_thread)

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
        await pilot.press("i")
        await pilot.pause()
        modal_input = app.query_one("#invite-agent")
        modal_input.value = "qa_engineer"
        await pilot.press("ctrl+enter")
        await pilot.pause()
    assert invited == [{"thread_id": "THR-001", "agent_name": "qa_engineer"}]
```

- [ ] **Step 2: Run, verify failure**

Run: `uv run pytest tests/tui/test_threads_app.py::test_invite_modal_invokes_invite -v`

Expected: FAIL.

- [ ] **Step 3: Implement**

Add the modal class:

```python
class InviteScreen(ModalScreen[str | None]):
    BINDINGS = [
        Binding("escape", "dismiss(None)", "Cancel"),
        Binding("ctrl+enter", "submit", "Invite"),
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
```

On the App class, add to BINDINGS: `Binding("i", "invite", "Invite")`. Then:

```python
    async def _invite_impl(self, *, slug, thread_id, agent_name):
        from src.tui.api_client import AsyncOpcClient
        if self._client is None:
            self._client = AsyncOpcClient(base_url=self._base_url, token=self._token)
        return await self._client.invite(slug=slug, thread_id=thread_id, agent_name=agent_name)

    async def action_invite(self) -> None:
        if self._current_thread_id is None:
            self.notify("select a thread first", severity="warning")
            return
        name = await self.push_screen_wait(InviteScreen())
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
        # The system message will arrive via tail SSE; trigger a redraw.
        detail = await self._get_thread_impl(
            slug=self._slug, thread_id=self._current_thread_id,
        )
        self.set_thread_detail(detail)
```

Add CSS:

```css
#invite-modal {
    width: 60;
    height: 8;
    background: $surface;
    border: round $accent;
    padding: 1 2;
}

#invite-label {
    color: $text-muted;
    margin-bottom: 1;
}
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/tui/test_threads_app.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/tui/threads_app.py src/tui/threads_app.tcss tests/tui/test_threads_app.py
git commit -m "feat(tui): I opens InviteScreen modal"
```

---

## Task 15: Archive modal (A key)

**Files:**
- Modify: `src/tui/threads_app.py`
- Modify: `src/tui/threads_app.tcss`
- Modify: `tests/tui/test_threads_app.py`

`A` opens a modal asking for summary + a checkbox for `request_close_outs`.

- [ ] **Step 1: Failing test**

Append:

```python
async def test_archive_modal_invokes_archive(monkeypatch):
    app = ThreadsApp(slug="alpha", base_url="http://test", token="tok")
    archived: list[dict] = []

    async def fake_archive(*, slug, thread_id, summary, request_close_outs):
        archived.append({"thread_id": thread_id, "summary": summary,
                         "request_close_outs": request_close_outs})
        return {"thread_id": thread_id, "status": "archiving",
                "close_out_count": 0, "transcript_path": None}

    async def fake_get_thread(*, slug, thread_id):
        return {"thread_id": thread_id, "subject": "x", "status": "open",
                "participants": [], "messages": []}

    monkeypatch.setattr(app, "_archive_impl", fake_archive)
    monkeypatch.setattr(app, "_get_thread_impl", fake_get_thread)

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
        await pilot.press("a")
        await pilot.pause()
        app.query_one("#archive-summary").text = "wrapped"
        await pilot.press("ctrl+enter")
        await pilot.pause()
    assert archived == [{"thread_id": "THR-001", "summary": "wrapped",
                         "request_close_outs": True}]
```

- [ ] **Step 2: Run, verify failure**

Run: `uv run pytest tests/tui/test_threads_app.py::test_archive_modal_invokes_archive -v`

Expected: FAIL.

- [ ] **Step 3: Implement**

```python
from textual.widgets import Checkbox


class ArchiveScreen(ModalScreen[dict | None]):
    BINDINGS = [
        Binding("escape", "dismiss(None)", "Cancel"),
        Binding("ctrl+enter", "submit", "Archive"),
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
```

App-side:

```python
    BINDINGS = [
        # ... existing ...
        Binding("a", "archive", "Archive"),
    ]

    async def _archive_impl(self, *, slug, thread_id, summary, request_close_outs):
        from src.tui.api_client import AsyncOpcClient
        if self._client is None:
            self._client = AsyncOpcClient(base_url=self._base_url, token=self._token)
        return await self._client.archive(
            slug=slug, thread_id=thread_id,
            summary=summary, request_close_outs=request_close_outs,
        )

    async def action_archive(self) -> None:
        if self._current_thread_id is None:
            self.notify("select a thread first", severity="warning")
            return
        result = await self.push_screen_wait(ArchiveScreen())
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
        detail = await self._get_thread_impl(
            slug=self._slug, thread_id=self._current_thread_id,
        )
        self.set_thread_detail(detail)
```

CSS:

```css
#archive-modal {
    width: 80;
    height: 16;
    background: $surface;
    border: round $accent;
    padding: 1 2;
}

#archive-label {
    color: $text-muted;
    margin-bottom: 1;
}

#archive-summary {
    height: 6;
    margin-bottom: 1;
}
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/tui/test_threads_app.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/tui/threads_app.py src/tui/threads_app.tcss tests/tui/test_threads_app.py
git commit -m "feat(tui): A opens ArchiveScreen modal"
```

---

## Task 16: Abandon modal (X key)

**Files:**
- Modify: `src/tui/threads_app.py`
- Modify: `src/tui/threads_app.tcss`
- Modify: `tests/tui/test_threads_app.py`

`X` opens a modal asking for a reason; calls `/abandon`.

- [ ] **Step 1: Failing test**

Append:

```python
async def test_abandon_modal_invokes_abandon(monkeypatch):
    app = ThreadsApp(slug="alpha", base_url="http://test", token="tok")
    abandoned: list[dict] = []

    async def fake_abandon(*, slug, thread_id, reason):
        abandoned.append({"thread_id": thread_id, "reason": reason})
        return {"thread_id": thread_id, "status": "abandoned"}

    async def fake_get_thread(*, slug, thread_id):
        return {"thread_id": thread_id, "subject": "x", "status": "open",
                "participants": [], "messages": []}

    monkeypatch.setattr(app, "_abandon_impl", fake_abandon)
    monkeypatch.setattr(app, "_get_thread_impl", fake_get_thread)

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
        await pilot.press("x")
        await pilot.pause()
        app.query_one("#abandon-reason").value = "not relevant anymore"
        await pilot.press("ctrl+enter")
        await pilot.pause()
    assert abandoned == [{"thread_id": "THR-001", "reason": "not relevant anymore"}]
```

- [ ] **Step 2: Run, verify failure**

Run: `uv run pytest tests/tui/test_threads_app.py::test_abandon_modal_invokes_abandon -v`

Expected: FAIL.

- [ ] **Step 3: Implement**

```python
class AbandonScreen(ModalScreen[str | None]):
    BINDINGS = [
        Binding("escape", "dismiss(None)", "Cancel"),
        Binding("ctrl+enter", "submit", "Abandon"),
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
```

App:

```python
    BINDINGS = [
        # ... existing ...
        Binding("x", "abandon", "Abandon"),
    ]

    async def _abandon_impl(self, *, slug, thread_id, reason):
        from src.tui.api_client import AsyncOpcClient
        if self._client is None:
            self._client = AsyncOpcClient(base_url=self._base_url, token=self._token)
        return await self._client.abandon(slug=slug, thread_id=thread_id, reason=reason)

    async def action_abandon(self) -> None:
        if self._current_thread_id is None:
            self.notify("select a thread first", severity="warning")
            return
        reason = await self.push_screen_wait(AbandonScreen())
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
        detail = await self._get_thread_impl(
            slug=self._slug, thread_id=self._current_thread_id,
        )
        self.set_thread_detail(detail)
```

CSS:

```css
#abandon-modal {
    width: 60;
    height: 8;
    background: $surface;
    border: round $accent;
    padding: 1 2;
}

#abandon-label {
    color: $text-muted;
    margin-bottom: 1;
}
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/tui/test_threads_app.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/tui/threads_app.py src/tui/threads_app.tcss tests/tui/test_threads_app.py
git commit -m "feat(tui): X opens AbandonScreen modal"
```

---

## Task 17: Forward action (F key)

**Files:**
- Modify: `src/tui/threads_app.py`
- Modify: `tests/tui/test_threads_app.py`

`F` on a selected thread opens the same `NewThreadScreen` from Task 13 with the subject prefilled `Fwd: <source>` and the body prefilled with a quoted blockquote of the source thread's messages.

- [ ] **Step 1: Failing test**

Append:

```python
async def test_forward_prefills_compose(monkeypatch):
    app = ThreadsApp(slug="alpha", base_url="http://test", token="tok")

    async def fake_get_thread(*, slug, thread_id):
        return {
            "thread_id": thread_id, "subject": "refund policy", "status": "open",
            "participants": ["dev_agent"],
            "messages": [
                {"seq": 1, "speaker": "founder", "kind": "message",
                 "body_markdown": "should we cap?", "addressed_to": ["@all"],
                 "decline_reason": None, "system_payload": None,
                 "created_at": "2026-05-14T00:00:00+00:00"},
            ],
        }

    composed: list[dict] = []

    async def fake_compose(**kwargs):
        composed.append(kwargs)
        return {"thread_id": "THR-NEW", "started_at": "now", "pending_replies": []}

    monkeypatch.setattr(app, "_get_thread_impl", fake_get_thread)
    monkeypatch.setattr(app, "_compose_thread_impl", fake_compose)

    async with app.run_test() as pilot:
        app.set_threads([
            {"thread_id": "THR-001", "subject": "refund policy", "status": "open",
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
        await pilot.press("f")
        await pilot.pause()
        # The modal should be open with the quoted body prefilled.
        body = app.query_one("#new-body").text
        assert "should we cap?" in body
        assert "Forwarded from THR-001" in body
        subject = app.query_one("#new-subject")
        assert subject.value == "Fwd: refund policy"
        # Fill recipients and submit.
        app.query_one("#new-recipients").value = "qa_engineer"
        await pilot.press("ctrl+enter")
        await pilot.pause()
    assert composed[0]["forwarded_from_id"] == "THR-001"
    assert composed[0]["forwarded_from_kind"] == "thread"
```

- [ ] **Step 2: Run, verify failure**

Run: `uv run pytest tests/tui/test_threads_app.py::test_forward_prefills_compose -v`

Expected: FAIL.

- [ ] **Step 3: Implement**

Reuse the foundation's `build_forward_body_from_thread`. In the App:

```python
    BINDINGS = [
        # ... existing ...
        Binding("f", "forward", "Forward"),
    ]

    async def action_forward(self) -> None:
        if self._current_thread_id is None:
            self.notify("select a thread first", severity="warning")
            return
        # Fetch the latest detail so the quote is complete.
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
        screen = NewThreadScreen(prefill={
            "subject": f"Fwd: {detail['subject']}",
            "body": quoted_body,
            "forwarded_from_id": detail["thread_id"],
            "forwarded_from_kind": "thread",
        })
        result = await self.push_screen_wait(screen)
        if result is None:
            return
        try:
            await self._compose_thread_impl(
                subject=result["subject"],
                recipients=result["recipients"],
                body_markdown=result["body_markdown"],
                addressed_to=["@all"],
                forwarded_from_id=result.get("forwarded_from_id"),
                forwarded_from_kind=result.get("forwarded_from_kind"),
            )
        except Exception as exc:  # noqa: BLE001
            self.notify(f"forward send failed: {exc}", severity="error")
            return
        self.notify("forwarded")
        await self._refresh_inbox()
```

Update `NewThreadScreen` to accept a prefill:

```python
class NewThreadScreen(ModalScreen[dict | None]):
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
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/tui/test_threads_app.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/tui/threads_app.py tests/tui/test_threads_app.py
git commit -m "feat(tui): F forwards a thread via NewThreadScreen prefill"
```

---

## Task 18: Help overlay (? key)

**Files:**
- Modify: `src/tui/threads_app.py`
- Modify: `src/tui/threads_app.tcss`
- Modify: `tests/tui/test_threads_app.py`

`?` opens a modal listing all keybindings.

- [ ] **Step 1: Failing test**

Append:

```python
async def test_help_modal_lists_keybindings():
    app = ThreadsApp(slug="alpha", base_url="http://test", token="tok")
    async with app.run_test() as pilot:
        await pilot.press("question_mark")
        await pilot.pause()
        body = app.query_one("#help-body")
        text = body.renderable.__str__() if hasattr(body, "renderable") else str(body)
        for token in ("New", "Reply", "Forward", "Invite", "Archive", "Abandon"):
            assert token in text
```

(Textual emits `question_mark` as the key event for `?` on US keyboards; if the test fails on the press call, try `pilot.press("shift+slash")` instead.)

- [ ] **Step 2: Run, verify failure**

Run: `uv run pytest tests/tui/test_threads_app.py::test_help_modal_lists_keybindings -v`

Expected: FAIL.

- [ ] **Step 3: Implement**

```python
class HelpScreen(ModalScreen[None]):
    BINDINGS = [
        Binding("escape", "dismiss(None)", "Close"),
        Binding("?", "dismiss(None)", "Close"),
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
```

App-side:

```python
    BINDINGS = [
        # ... existing ...
        Binding("question_mark", "help", "Help"),
    ]

    async def action_help(self) -> None:
        await self.push_screen_wait(HelpScreen())
```

CSS:

```css
#help-modal {
    width: 60;
    height: 20;
    background: $surface;
    border: round $accent;
    padding: 1 2;
}

#help-body {
    color: $text;
}
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/tui/test_threads_app.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/tui/threads_app.py src/tui/threads_app.tcss tests/tui/test_threads_app.py
git commit -m "feat(tui): ? opens HelpScreen"
```

---

## Task 19: Clean disconnect on Ctrl+C + close http client

**Files:**
- Modify: `src/tui/threads_app.py`
- Modify: `tests/tui/test_threads_app.py`

The app should close the `AsyncOpcClient` on exit so background SSE streams release sockets.

- [ ] **Step 1: Failing test**

Append:

```python
async def test_async_client_is_closed_on_unmount():
    app = ThreadsApp(slug="alpha", base_url="http://test", token="tok")
    closed = False

    class FakeClient:
        async def aclose(self):
            nonlocal closed
            closed = True

    async with app.run_test() as pilot:
        app._client = FakeClient()
        await app.action_quit()
    assert closed is True
```

- [ ] **Step 2: Run, verify failure**

Run: `uv run pytest tests/tui/test_threads_app.py::test_async_client_is_closed_on_unmount -v`

Expected: FAIL (or pass trivially if Textual's default quit already calls unmount handlers — verify the assertion runs).

- [ ] **Step 3: Implement**

In the App class:

```python
    async def on_unmount(self) -> None:
        if self._client is not None:
            try:
                await self._client.aclose()
            except Exception:  # noqa: BLE001
                pass
            self._client = None
```

(Textual calls `on_unmount` during shutdown.)

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/tui/test_threads_app.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/tui/threads_app.py tests/tui/test_threads_app.py
git commit -m "feat(tui): close AsyncOpcClient on unmount"
```

---

## Task 20: CLI entrypoint — `opc threads` (no subcommand) launches the TUI

**Files:**
- Modify: `src/cli.py`

Today `opc threads` requires a subcommand (compose, list, …). When no subcommand is supplied, fall through to launching the TUI.

- [ ] **Step 1: Locate the threads subparser registration**

Run: `grep -n "p_threads = sub.add_parser" src/cli.py`

This finds the parser definition. Look at how the subparsers are configured — likely `threads_sub = p_threads.add_subparsers(dest="threads_command", required=True)`.

- [ ] **Step 2: Make the subcommand optional + add a TUI handler**

In `src/cli.py`, change `required=True` to `required=False` on the threads subparsers definition. Then add a handler:

```python
def cmd_threads_tui(args: argparse.Namespace) -> None:
    slug = _resolve_org_slug(args) if hasattr(args, "_resolve_org_slug") else resolve_org_slug(args)
    # The actual helper name in this codebase is `resolve_org_slug` — adapt as needed.
    client = OpcClient.from_env()
    from src.tui.threads_app import run as run_tui
    sys.exit(run_tui(slug=slug, base_url=client.base_url, token=_token_from_client(client)))
```

If the existing CLI exposes `client.base_url` and `client.headers["Authorization"]` cleanly, use those. The token can be read directly:

```python
from src.daemon import paths as _opc_paths
token = _opc_paths.read_token()
```

Adapt to whatever pattern other CLI commands use to materialize a token.

Then wire the parser's default handler — argparse + optional subparsers requires a `set_defaults` on the parent:

```python
    p_threads.set_defaults(func=cmd_threads_tui)
```

If the subcommand IS given, the subparser overrides the parent's default. If it isn't, `cmd_threads_tui` fires.

- [ ] **Step 3: Smoke check (manual)**

Run: `uv run opc threads --help`

Expected: shows the same subcommand list as before, with no breaking change.

Run: `uv run opc threads --org alpha` (without a subcommand, but the daemon must be up).

Expected: TUI launches. Press `Ctrl+C` to exit.

(If you don't have a dev runtime to point at, just verify `opc threads --help` still works and the new handler is wired.)

- [ ] **Step 4: Existing CLI tests still pass**

Run: `uv run pytest tests/ -v -k "cli or threads" 2>&1 | tail -10`

Expected: no regressions. Specifically, any existing `opc threads <subcommand>` invocations must still work.

- [ ] **Step 5: Commit**

```bash
git add src/cli.py
git commit -m "feat(cli): opc threads (no subcommand) launches the TUI"
```

---

## Task 21: README + CLAUDE.md doc updates

**Files:**
- Modify: `README.md`
- Modify: `CLAUDE.md`

- [ ] **Step 1: Update the README Threads section**

In `README.md`, find the Threads section. Replace the final paragraph:

```markdown
A Textual TUI (`opc threads` with no subcommand) is planned for a follow-up
release.
```

with:

```markdown
A Textual TUI launches when you run `opc threads` with no subcommand:

```bash
opc threads --org <slug>
```

Keybindings: `N` new, `R` reply, `F` forward, `I` invite, `A` archive,
`X` abandon, `Enter` open selected, `Ctrl+R` refresh, `?` help, `Ctrl+C` quit.
The TUI subscribes to per-org SSE events so the inbox and the selected
thread stay live without polling.
```

- [ ] **Step 2: Update CLAUDE.md**

Find item 12 in the Implementation Order list. Update it:

```markdown
12. ~~**Threads (foundation)**~~ done — email-style multi-agent workchannels with daemon-minted invocation tokens. CLI surface, end-to-end integration coverage via `fake_claude.sh` thread-prompt routing, plus a Textual TUI (`opc threads` no subcommand). Spec: `docs/superpowers/specs/2026-05-13-threads-design.md`. Plans: `docs/superpowers/plans/2026-05-13-threads-foundation.md`, `docs/superpowers/plans/2026-05-13-threads-tui.md`.
```

Find the directory layout section in CLAUDE.md and add the new `src/tui/` directory near the other `src/` entries:

```
|-- src/
|   ...
|   +-- tui/
|       |-- __init__.py
|       |-- threads_app.py
|       +-- threads_app.tcss
```

- [ ] **Step 3: Commit**

```bash
git add README.md CLAUDE.md
git commit -m "docs: threads TUI in README + CLAUDE.md"
```

---

## Task 22: Final test sweep + manual smoke

**Files:**
- (verification only)

- [ ] **Step 1: Full unit suite**

Run: `uv run pytest tests/ -v 2>&1 | tail -10`

Expected: all PASS. The TUI tests live under `tests/tui/`.

- [ ] **Step 2: Integration suite**

Run: `uv run pytest tests/ -v -m integration 2>&1 | tail -10`

Expected: all PASS (TUI doesn't add new integration tests in this plan; existing ones unaffected).

- [ ] **Step 3: Manual smoke (optional)**

Against a running dev daemon with at least one thread:

```bash
uv run opc threads --org <slug>
```

Verify:
- Inbox loads.
- Selecting a thread shows its messages.
- `R` focuses compose; Ctrl+Enter sends a reply.
- `N` opens the new-thread modal.
- `?` shows help.
- Ctrl+C exits cleanly.

If you don't have a runtime handy, skip this step.

- [ ] **Step 4: Open the PR**

```bash
git push -u origin <branch>
gh pr create --title "feat: threads TUI" --body "$(cat <<'EOF'
## Summary
- New `opc threads` (no subcommand) launches a Textual TUI: three-pane email-client layout (inbox / thread / compose) with live SSE updates.
- Keybindings for new/reply/forward/invite/archive/abandon mirror the spec.
- AsyncOpcClient is a focused async wrapper over the existing daemon endpoints.

## Test plan
- [x] `uv run pytest tests/tui/ -v` — unit + pilot tests
- [x] `uv run pytest tests/ -v` — no regressions
- [ ] Manual smoke against a live daemon

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 5: Done**

The TUI plan is complete.
