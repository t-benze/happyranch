"""Shared value models for the portable supervised connector core.

Every object here is a plain immutable value; no credentials are ever stored
on these models.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Header:
    """A single HTTP header; names are normalized to lowercase."""

    name: str
    value: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", self.name.lower())


@dataclass(frozen=True)
class RemoteRequest:
    """A parsed remote request/stream frame (never carries the daemon bearer).

    ``path`` is the request-target path; ``query`` is the raw query string
    (without the ``?``) or ``None``. ``stream_type`` is one of
    ``http`` / ``sse`` / ``websocket``.
    """

    method: str
    path: str
    query: str | None = None
    headers: tuple[Header, ...] = ()
    body: bytes | None = None
    stream_type: str = "http"


@dataclass(frozen=True)
class NormalizedTarget:
    """The canonical, normalized request target — computed exactly once."""

    method: str
    path: str
    query: str | None
    collapsed: bool
    raw_path: str


@dataclass(frozen=True)
class DeniedOutcome:
    """A fail-closed denial expressed only in stable, tenant-neutral
    category terms — never raw input, exception text, or credentials."""

    deny_category: str
    audit_category: str
    detail: str
    reason: str | None = None


@dataclass(frozen=True)
class ForwardedResponse:
    """The loopback daemon's response, relayed back to the remote client."""

    status: int
    headers: tuple[Header, ...] = ()
    body: bytes = b""


@dataclass(frozen=True)
class Decision:
    """The gateway's decision for one request/stream.

    For an allowed HTTP request ``response`` is set; for an allowed
    SSE/WebSocket stream ``stream`` is set. Denials carry ``denied``.
    """

    allowed: bool
    audit_category: str
    audit_detail: str
    denied: DeniedOutcome | None = None
    response: ForwardedResponse | None = None
    stream: object | None = None


class LoopbackViolation(Exception):
    """Raised when a forward target is not literal loopback 127.0.0.1."""
