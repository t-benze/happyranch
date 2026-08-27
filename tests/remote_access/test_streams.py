"""Revocation signal closing already-open stream/session abstractions
fail closed (contract §9; REV-002/REV-003).

A revocation signal closes every open stream; closed streams refuse further
frames/bytes; new streams are refused after revocation.
"""
from __future__ import annotations

import pytest

from runtime.remote_access.streams import StreamClosed, StreamRegistry


class _FakeHandle:
    """Minimal stream handle used by the registry tests."""

    def __init__(self, stream_id: str) -> None:
        self.stream_id = stream_id
        self.closed = False
        self.sent: list[bytes] = []

    def send(self, payload: bytes) -> None:
        if self.closed:
            raise StreamClosed(self.stream_id)
        self.sent.append(payload)

    def close(self) -> None:
        self.closed = True

    @property
    def is_closed(self) -> bool:
        return self.closed


def test_open_stream_tracked() -> None:
    reg = StreamRegistry()
    handle = _FakeHandle("s1")
    reg.open("s1", handle)
    assert reg.is_open("s1") is True


def test_close_all_closes_every_stream() -> None:
    reg = StreamRegistry()
    h1, h2 = _FakeHandle("s1"), _FakeHandle("s2")
    reg.open("s1", h1)
    reg.open("s2", h2)
    reg.close_all()
    assert h1.closed is True
    assert h2.closed is True
    assert reg.is_open("s1") is False
    assert reg.is_open("s2") is False


def test_closed_stream_refuses_frames() -> None:
    reg = StreamRegistry()
    handle = _FakeHandle("s1")
    reg.open("s1", handle)
    reg.close_all()
    with pytest.raises(StreamClosed):
        handle.send(b"data: late\n\n")
    with pytest.raises(StreamClosed):
        reg.raise_if_open("s1")


def test_single_stream_close() -> None:
    reg = StreamRegistry()
    h1, h2 = _FakeHandle("s1"), _FakeHandle("s2")
    reg.open("s1", h1)
    reg.open("s2", h2)
    reg.close("s1")
    assert h1.closed is True
    assert h2.closed is False
    assert reg.is_open("s1") is False


def test_unknown_stream_is_not_open() -> None:
    reg = StreamRegistry()
    assert reg.is_open("nope") is False


def test_duplicate_open_replaces() -> None:
    reg = StreamRegistry()
    h1, h2 = _FakeHandle("s1"), _FakeHandle("s1")
    reg.open("s1", h1)
    reg.open("s1", h2)
    assert reg.is_open("s1") is True
    assert h1.closed is True  # previous handle closed on replacement


def test_registry_refuses_open_after_revocation() -> None:
    reg = StreamRegistry()
    reg.close_all()
    with pytest.raises(StreamClosed):
        reg.open("late", _FakeHandle("late"))
