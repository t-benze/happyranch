"""Async HTTP + SSE client for the threads TUI.

Mirrors the sync `OpcClient` in `src/client/client.py` for the subset of
endpoints the TUI needs. Methods return raw dicts (no Pydantic decoding) —
the TUI re-shapes them into Textual rows directly.
"""
from __future__ import annotations

import json
from typing import Any, AsyncIterator

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

    async def extend(self, *, slug: str, thread_id: int, new_cap: int) -> dict[str, Any]:
        r = await self._client.post(
            f"/api/v1/orgs/{slug}/threads/{thread_id}/extend",
            json={"new_cap": new_cap},
        )
        r.raise_for_status()
        return r.json()

    async def iter_sse(self, path: str) -> AsyncIterator[dict[str, Any]]:
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
