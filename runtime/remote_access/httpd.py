"""Shared HTTP serving surface for the connector provider adapters
(THR-097 Unit 3 / Unit 3A).

Both the LAB-ONLY conformance adapter and the Supported-DIY customer-owned-
network adapter run the SAME security-critical serving mechanics: parse the
raw HTTP request into a ``RemoteRequest``, never log raw request lines,
serve a gateway ``Decision`` (deny => 403 category-only JSON, or 503 for
release verification refusal; allowed HTTP
=> relayed loopback response; allowed SSE => tracked-stream pump that stops
on revocation), and send only category-level JSON errors. Keeping one shared
implementation avoids divergent copies of the serving path across providers.
"""
from __future__ import annotations

import json
import os
import socket
import struct
from contextvars import ContextVar
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler
from socketserver import ThreadingUnixStreamServer

from runtime.remote_access.models import Decision, Header, RemoteRequest

_DENY_STATUS = 403

# Trusted connection metadata, scoped to the current handler/request thread.
# HTTP fields can neither set nor override this context. No authorization uses it.
ingress_node_identity: ContextVar[str | None] = ContextVar("ingress_node_identity", default=None)
_IDENTITY_PREFIXES = ("x-happyranch-", "x-tailscale-", "tailscale-", "x-tailnet-")


class IdentityUnixServer(ThreadingUnixStreamServer):
    """One protected stream per tailnet connection; no pooled identity state."""

    daemon_threads = True

    def __init__(self, path: str, handler: type[BaseHTTPRequestHandler], expected_uid: int) -> None:
        self.expected_uid = expected_uid
        self._socket_identity: tuple[int, int] | None = None
        super().__init__(path, handler, bind_and_activate=False)
        try:
            self.server_bind()
            info = os.lstat(path)
            self._socket_identity = (info.st_dev, info.st_ino)
            os.chmod(path, 0o600)
            self.server_activate()
        except Exception:
            self.server_close()
            raise

    def server_close(self) -> None:
        super().server_close()
        if self._socket_identity is not None:
            try:
                info = os.lstat(self.server_address)
                if (info.st_dev, info.st_ino) == self._socket_identity:
                    os.unlink(self.server_address)
            except FileNotFoundError:
                pass


def _connection_identity(handler: BaseHTTPRequestHandler) -> str:
    credentials = handler.connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
    _, uid, _ = struct.unpack("3i", credentials)
    if uid != handler.server.expected_uid:
        raise ValueError("peer refused")
    header = handler.rfile.read(7)
    if len(header) != 7 or header[:5] != b"HRID\x01":
        raise ValueError("frame refused")
    size = int.from_bytes(header[5:7], "big")
    if not 0 < size <= 1017:
        raise ValueError("frame refused")
    raw = handler.rfile.read(size)
    if len(raw) != size:
        raise ValueError("frame refused")
    identity = raw.decode("utf-8", errors="strict")
    if not identity.strip() or any(c in identity for c in "\r\n\x00"):
        raise ValueError("frame refused")
    return identity



def parse_request(handler: BaseHTTPRequestHandler) -> RemoteRequest:
    """Parse one raw HTTP request into a ``RemoteRequest`` (duplicates
    preserved for the gateway's locked strip step)."""
    raw_path = handler.path or "/"
    path, _, query = raw_path.partition("?")
    if not path:
        path = "/"
    length = int(handler.headers.get("Content-Length") or 0)
    body = handler.rfile.read(length) if length else None
    headers = [Header(k.lower(), v) for k, v in handler.headers.items()
               if not (isinstance(handler.server, IdentityUnixServer)
                       and k.lower() != "x-happyranch-device-credential"
                       and k.lower().startswith(_IDENTITY_PREFIXES))]
    stream_type = "http"
    if handler.headers.get("Upgrade", "").lower() == "websocket":
        stream_type = "websocket"
    elif handler.headers.get("Accept", "") == "text/event-stream":
        stream_type = "sse"
    return RemoteRequest(
        method=handler.command,
        path=path,
        query=query or None,
        headers=tuple(headers),
        body=body,
        stream_type=stream_type,
    )


def send_json(handler: BaseHTTPRequestHandler, status: int, payload: dict) -> None:
    """Send a small JSON error/status response and close the connection."""
    body = json.dumps(payload).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Connection", "close")
    handler.end_headers()
    handler.wfile.write(body)
    handler.wfile.flush()


def serve_decision(handler: BaseHTTPRequestHandler, decision: Decision) -> None:
    """Serve one gateway ``Decision`` to the wire. Denials are 403 with the
    stable category only (503 for release verification refusal); allowed HTTP relays the loopback response; allowed
    SSE pumps the tracked stream until EOF/closure (fail closed on
    revocation — the sealed handle raises and the pump terminates)."""
    if not decision.allowed:
        category = decision.audit_category or "denied"
        status = 503 if decision.denied and decision.denied.reason == "release_verification_refused" else _DENY_STATUS
        send_json(handler, status, {"error": category})
        return
    if decision.response is not None:
        handler.send_response(decision.response.status)
        for header in decision.response.headers:
            if header.name.lower() in {"content-length", "transfer-encoding"}:
                continue
            handler.send_header(header.name, header.value)
        body = decision.response.body or b""
        handler.send_header("Content-Length", str(len(body)))
        handler.send_header("Connection", "close")
        handler.end_headers()
        handler.wfile.write(body)
        handler.wfile.flush()
        return
    if decision.stream is not None:
        handler.send_response(200)
        handler.send_header("Content-Type", "text/event-stream")
        handler.send_header("Connection", "close")
        handler.end_headers()
        handler.wfile.flush()
        handle = decision.stream
        while True:
            try:
                chunk = handle.receive()
            except Exception:
                return  # stream sealed/closed (revocation): stop pumping
            if chunk is None:
                return
            try:
                handler.wfile.write(chunk)
                handler.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                return
        return
    send_json(handler, 500, {"error": "internal_error"})


class BaseConnectorHandler(BaseHTTPRequestHandler):
    """Base HTTP handler for the provider adapters: same request-parsing and
    decision-serving mechanics, no raw request logging; subclasses implement
    ``serve_request`` (pairing ceremony and/or gateway pipeline)."""

    protocol_version = "HTTP/1.1"

    def handle(self) -> None:
        if not isinstance(self.server, IdentityUnixServer):
            super().handle()
            return
        token = None
        try:
            self.connection.settimeout(2)
            identity = _connection_identity(self)
            token = ingress_node_identity.set(identity)
            super().handle()
        except (OSError, ValueError, UnicodeError):
            return
        finally:
            if token is not None:
                ingress_node_identity.reset(token)

    def parse_request(self) -> bool:
        if isinstance(self.server, IdentityUnixServer) and self.raw_requestline.startswith(b"HRID"):
            self.close_connection = True
            return False
        return super().parse_request()

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        return  # never log raw request lines (no paths/secrets in logs)

    def _handle(self) -> None:
        if isinstance(self.server, IdentityUnixServer):
            self.connection.settimeout(None)
            self.close_connection = True
        try:
            request = parse_request(self)
        except (ValueError, OSError):
            send_json(self, 400, {"error": "bad_request"})
            return
        self.serve_request(request, datetime.now(timezone.utc))

    def serve_request(self, request: RemoteRequest, now: datetime) -> None:
        raise NotImplementedError

    do_GET = _handle
    do_POST = _handle
    do_PUT = _handle
    do_DELETE = _handle
    do_PATCH = _handle
    do_HEAD = _handle
    do_OPTIONS = _handle
