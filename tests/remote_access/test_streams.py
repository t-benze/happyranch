"""Revocation signal closing already-open stream/session abstractions
fail closed (contract §9; REV-002/REV-003).

A revocation signal closes every open stream; closed streams refuse further
frames/bytes; new streams are refused after revocation.
"""
from __future__ import annotations

import pytest

from runtime.remote_access.streams import StreamCloseError, StreamClosed, StreamRegistry


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

    def receive(self) -> bytes | None:
        if self.closed:
            raise StreamClosed(self.stream_id)
        return None

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


class _ExplodingHandle:
    """A handle whose close() raises — the registry must still seal."""

    def __init__(self, stream_id: str) -> None:
        self.stream_id = stream_id

    def close(self) -> None:
        raise OSError("cannot close")

    def receive(self) -> bytes | None:
        return None

    @property
    def closed(self) -> bool:
        return False


def test_close_all_fail_closed_when_handle_close_raises() -> None:
    """A raising handle close still drops the handle and seals the registry;
    the failure is surfaced after the registry is sealed."""
    reg = StreamRegistry()
    good = _FakeHandle("good")
    reg.open("good", good)
    reg.open("bad", _ExplodingHandle("bad"))

    with pytest.raises(StreamCloseError) as excinfo:
        reg.close_all()
    assert excinfo.value.stream_ids == ("bad",)

    # Fail closed: no live registry-tracked stream survives; new opens refused.
    assert reg.is_open("good") is False
    assert reg.is_open("bad") is False
    assert good.closed is True
    with pytest.raises(StreamClosed):
        reg.open("late", _FakeHandle("late"))


def test_close_all_is_idempotent() -> None:
    """A second close_all after sealing is a no-op and never raises."""
    reg = StreamRegistry()
    handle = _FakeHandle("s1")
    reg.open("s1", handle)
    reg.close_all()
    reg.close_all()  # must not raise
    assert reg.is_open("s1") is False
    with pytest.raises(StreamClosed):
        reg.open("s2", _FakeHandle("s2"))


def test_close_all_seals_before_closing() -> None:
    """The registry is sealed first: even a close that re-enters cannot open a
    new stream on a revoked registry."""
    reg = StreamRegistry()
    events: list[str] = []

    class _Reentrant:
        stream_id = "reentrant"

        def close(self) -> None:
            try:
                reg.open("late", _FakeHandle("late"))
            except StreamClosed:
                events.append("refused")

        def receive(self) -> bytes | None:
            return None

        @property
        def closed(self) -> bool:
            return True

    reg.open("reentrant", _Reentrant())
    reg.close_all()
    assert events == ["refused"]


def test_open_returns_tracked_wrapper_that_delegates() -> None:
    """``open`` returns a registry-owned tracked wrapper: before sealing it
    delegates receive/close/closed/send to the underlying handle and carries
    the stream id."""
    reg = StreamRegistry()
    inner = _FakeHandle("s1")
    tracked = reg.open("s1", inner)
    assert tracked is not inner
    assert tracked.stream_id == "s1"
    assert tracked.closed is False
    assert tracked.receive() is None
    tracked.send(b"data: ping\n\n")
    assert inner.sent == [b"data: ping\n\n"]
    tracked.close()
    assert inner.closed is True
    assert tracked.closed is True


def test_close_all_seals_every_wrapper_before_any_close_attempt() -> None:
    """Failure ordering: the externally retained wrapper of a handle whose
    ``close()`` raises is sealed BEFORE the transport close is attempted — a
    failed close can never leave the retained handle readable or untracked,
    and later handles are still closed."""
    reg = StreamRegistry()
    events: list[str] = []

    class _OrderRecording:
        def __init__(self, stream_id: str) -> None:
            self.stream_id = stream_id

        def receive(self) -> bytes | None:
            return b"still-live"

        def close(self) -> None:
            events.append(f"close:{self.stream_id}")
            if self.stream_id == "bad":
                raise OSError("boom")

        @property
        def closed(self) -> bool:
            return False

    good_inner = _OrderRecording("good")
    bad_inner = _OrderRecording("bad")
    good_tracked = reg.open("good", good_inner)
    bad_tracked = reg.open("bad", bad_inner)

    with pytest.raises(StreamCloseError) as excinfo:
        reg.close_all()
    assert excinfo.value.stream_ids == ("bad",)

    # Every close was still attempted (partial failure, not aborted).
    assert events == ["close:good", "close:bad"] or events == ["close:bad", "close:good"]
    # The retained wrappers are sealed regardless of the underlying outcome.
    assert good_tracked.closed is True
    assert bad_tracked.closed is True
    with pytest.raises(StreamClosed):
        bad_tracked.receive()
    with pytest.raises(StreamClosed):
        good_tracked.receive()
    assert reg.is_open("good") is False
    assert reg.is_open("bad") is False


def test_close_all_partial_multi_handle_failure_seals_all() -> None:
    """Partial multi-handle failure: with one good and two exploding handles,
    every retained wrapper is sealed, the good inner handle is physically
    closed, and the failure evidence names exactly the failed ids."""
    reg = StreamRegistry()
    good_inner = _FakeHandle("good")
    tracked: list = [
        reg.open("good", good_inner),
        reg.open("bad1", _ExplodingHandle("bad1")),
        reg.open("bad2", _ExplodingHandle("bad2")),
    ]

    with pytest.raises(StreamCloseError) as excinfo:
        reg.close_all()
    assert set(excinfo.value.stream_ids) == {"bad1", "bad2"}

    assert good_inner.closed is True
    for handle in tracked:
        assert handle.closed is True, "every retained wrapper must report closed"
        with pytest.raises(StreamClosed):
            handle.receive()
    assert reg.is_open("good") is False
    with pytest.raises(StreamClosed):
        reg.open("late", _FakeHandle("late"))


def test_sealed_wrapper_rejects_send_and_receive() -> None:
    """A WebSocket-shaped retained handle (receive + send) rejects BOTH
    directions after close_all — nothing readable or writable survives."""
    reg = StreamRegistry()
    inner = _FakeHandle("ws1")
    tracked = reg.open("ws1", inner)
    tracked.send(b"frame")
    reg.close_all()
    with pytest.raises(StreamClosed):
        tracked.send(b"data: late\n\n")
    with pytest.raises(StreamClosed):
        tracked.receive()


def test_sealed_wrapper_close_is_idempotent_noop() -> None:
    """Calling close() on a sealed wrapper is a no-op and never raises — the
    revocation transaction owns cleanup after sealing."""
    reg = StreamRegistry()
    inner = _FakeHandle("s1")
    tracked = reg.open("s1", inner)
    reg.close_all()
    tracked.close()
    tracked.close()
    assert tracked.closed is True


def test_single_stream_close_seals_that_wrapper_only() -> None:
    """A normal close of one stream seals that wrapper and closes its inner;
    other streams stay open."""
    reg = StreamRegistry()
    h1, h2 = _FakeHandle("s1"), _FakeHandle("s2")
    t1 = reg.open("s1", h1)
    t2 = reg.open("s2", h2)
    reg.close("s1")
    assert h1.closed is True
    assert h2.closed is False
    assert t1.closed is True
    assert t2.closed is False
    with pytest.raises(StreamClosed):
        t1.receive()
    assert reg.is_open("s1") is False
    assert reg.is_open("s2") is True
