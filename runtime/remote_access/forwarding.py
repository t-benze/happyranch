"""Loopback-only forwarding abstraction/harness (contract §6.1 step 8, §6.3).

The forwarder can target ONLY literal loopback 127.0.0.1: the target
constructor refuses every other host, and every forward/open re-verifies the
host. The daemon bearer is injected only on this final hop, and no outbound
request carrying credential-shaped material is ever transmitted.
"""
from __future__ import annotations

import http.client
from dataclasses import dataclass
from typing import Protocol

from runtime.remote_access.audit import scan_secret_shapes
from runtime.remote_access.models import (
    ForwardedResponse,
    Header,
    LoopbackViolation,
)
from runtime.remote_access.streams import StreamClosed, StreamHandle

LOOPBACK_HOST = "127.0.0.1"


class OutboundLeakError(Exception):
    """Raised when an outbound request would carry credential-shaped material."""


@dataclass(frozen=True)
class LoopbackTarget:
    """The only network target the connector core may use: literal 127.0.0.1."""

    host: str
    port: int

    def __post_init__(self) -> None:
        if self.host != LOOPBACK_HOST:
            raise LoopbackViolation(f"forward target must be 127.0.0.1, got {self.host!r}")
        if not isinstance(self.port, int) or not (0 <= self.port <= 65535):
            raise ValueError(f"invalid loopback port {self.port!r}")


def assert_no_credential_leak(
    method: str,
    path: str,
    query: str | None,
    headers: tuple[Header, ...],
    body: bytes | None,
    bearer: str,
) -> None:
    """Refuse to transmit any outbound request containing the daemon bearer or
    bearer-shaped material outside the single injected Authorization header."""
    expected_auth = f"Bearer {bearer}"
    seen_injected_auth = False
    for header in headers:
        if header.name == "authorization":
            if header.value != expected_auth:
                raise OutboundLeakError("remote authorization header survived stripping")
            if seen_injected_auth:
                raise OutboundLeakError("duplicate authorization header on outbound request")
            seen_injected_auth = True
            continue
        if scan_secret_shapes(header.value, bearer=bearer):
            raise OutboundLeakError("credential-shaped header on outbound request")
    for text in (path, query or ""):
        if scan_secret_shapes(text, bearer=bearer):
            raise OutboundLeakError("credential-shaped material in outbound target")
    if body and scan_secret_shapes(body.decode("utf-8", errors="replace"), bearer=bearer):
        raise OutboundLeakError("credential-shaped material in outbound body")


class LoopbackForwarder(Protocol):
    target: LoopbackTarget

    def forward_once(
        self,
        method: str,
        path: str,
        query: str | None,
        headers: tuple[Header, ...],
        body: bytes | None,
        bearer: str,
    ) -> ForwardedResponse: ...

    def open_stream(
        self,
        method: str,
        path: str,
        query: str | None,
        headers: tuple[Header, ...],
        body: bytes | None,
        bearer: str,
        stream_id: str,
    ) -> StreamHandle: ...


class ForwardingHarness:
    """Record-only forwarding harness (no network) for unit gateway tests.

    It enforces the same no-bearer-leak invariant as the real forwarder.
    """

    def __init__(self, response: ForwardedResponse | None = None) -> None:
        self.target = LoopbackTarget(LOOPBACK_HOST, 0)
        self._response = response
        self.forwarded: list[dict] = []
        self.streams: list[str] = []

    def forward_once(
        self,
        method: str,
        path: str,
        query: str | None,
        headers: tuple[Header, ...],
        body: bytes | None,
        bearer: str,
    ) -> ForwardedResponse:
        assert_no_credential_leak(method, path, query, headers, body, bearer)
        self.forwarded.append({"method": method, "path": path, "query": query, "headers": headers})
        if self._response is not None:
            return self._response
        return ForwardedResponse(status=200, headers=(), body=b'{"ok": true}')

    def open_stream(
        self,
        method: str,
        path: str,
        query: str | None,
        headers: tuple[Header, ...],
        body: bytes | None,
        bearer: str,
        stream_id: str,
    ) -> StreamHandle:
        assert_no_credential_leak(method, path, query, headers, body, bearer)
        self.streams.append(stream_id)
        return _HarnessStreamHandle(stream_id)


class _HarnessStreamHandle:
    """A trivial in-memory stream handle for the record-only harness."""

    status = 200
    headers: tuple = ()

    def __init__(self, stream_id: str) -> None:
        self.stream_id = stream_id
        self._closed = False
        self._events: list[bytes] = [b"data: hello\n\n"]

    def receive(self) -> bytes | None:
        if self._closed:
            raise StreamClosed(self.stream_id)
        if not self._events:
            return None
        return self._events.pop(0)

    def close(self) -> None:
        self._closed = True

    @property
    def closed(self) -> bool:
        return self._closed


class HttpStreamHandle:
    """A live loopback stream (HTTP/SSE/WebSocket) backed by http.client."""

    def __init__(self, stream_id: str, response: http.client.HTTPResponse, connection: http.client.HTTPConnection) -> None:
        self.stream_id = stream_id
        self._response = response
        self._connection = connection
        self._closed = False

    @property
    def status(self) -> int:
        return self._response.status

    @property
    def headers(self) -> tuple:
        return tuple(Header(k, v) for k, v in self._response.getheaders())

    def receive(self) -> bytes | None:
        if self._closed:
            raise StreamClosed(self.stream_id)
        try:
            line = self._response.readline()
        except (OSError, http.client.HTTPException) as exc:
            self.close()
            raise StreamClosed(self.stream_id) from exc
        if not line:
            self.close()
            return None
        return line

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._response.close()
        finally:
            self._connection.close()

    @property
    def closed(self) -> bool:
        return self._closed


class HttpLoopbackForwarder:
    """The real loopback forwarder: connects to 127.0.0.1 only, injects the
    daemon bearer on the final hop, and refuses credential-bearing outbound
    requests."""

    def __init__(self, target: LoopbackTarget) -> None:
        if target.host != LOOPBACK_HOST:
            raise LoopbackViolation("forwarder requires a literal 127.0.0.1 target")
        self.target = target

    def _require_loopback_host(self, host: str) -> None:
        if host != LOOPBACK_HOST:
            raise AssertionError(f"non-loopback host refused: {host!r}")

    def _connect(self) -> http.client.HTTPConnection:
        self._require_loopback_host(self.target.host)
        return http.client.HTTPConnection(LOOPBACK_HOST, self.target.port, timeout=10)

    def forward_once(
        self,
        method: str,
        path: str,
        query: str | None,
        headers: tuple[Header, ...],
        body: bytes | None,
        bearer: str,
    ) -> ForwardedResponse:
        assert_no_credential_leak(method, path, query, headers, body, bearer)
        target = path if query is None else f"{path}?{query}"
        outbound = tuple(headers) + (Header("Authorization", f"Bearer {bearer}"),)
        conn = self._connect()
        try:
            conn.request(method, target, body=body, headers={h.name: h.value for h in outbound})
            response = conn.getresponse()
            payload = response.read()
            return ForwardedResponse(
                status=response.status,
                headers=tuple(Header(k, v) for k, v in response.getheaders()),
                body=payload,
            )
        finally:
            conn.close()

    def open_stream(
        self,
        method: str,
        path: str,
        query: str | None,
        headers: tuple[Header, ...],
        body: bytes | None,
        bearer: str,
        stream_id: str,
    ) -> StreamHandle:
        assert_no_credential_leak(method, path, query, headers, body, bearer)
        target = path if query is None else f"{path}?{query}"
        outbound = tuple(headers) + (Header("Authorization", f"Bearer {bearer}"),)
        conn = self._connect()
        conn.request(method, target, body=body, headers={h.name: h.value for h in outbound})
        response = conn.getresponse()
        return HttpStreamHandle(stream_id, response, conn)
