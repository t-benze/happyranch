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


async def test_iter_sse_yields_data_events():
    async def handler(request: httpx.Request) -> httpx.Response:
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
