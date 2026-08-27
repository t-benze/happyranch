"""Stream/session registry — revocation closes open streams fail closed
(contract §9; REV-002/REV-003).

A closed stream refuses further frames; new streams are refused after a
revocation has closed the registry.
"""
from __future__ import annotations

from typing import Protocol


class StreamClosed(Exception):
    """Raised when a stream is closed (revocation) and further bytes/frames
    are attempted."""

    def __init__(self, stream_id: str) -> None:
        super().__init__(f"stream closed: {stream_id}")
        self.stream_id = stream_id


class StreamCloseError(Exception):
    """Raised by ``StreamRegistry.close_all`` when one or more handles failed
    to close.

    The registry is still sealed (fail closed): every handle was dropped and
    no new stream can open, so a revoked device never retains a live
    registry-tracked stream. The failed ids are exposed for diagnostics only.
    """

    def __init__(self, stream_ids: list[str] | tuple[str, ...]) -> None:
        self.stream_ids: tuple[str, ...] = tuple(stream_ids)
        super().__init__(
            f"stream close failed for {len(self.stream_ids)} stream(s)"
        )


class StreamHandle(Protocol):
    stream_id: str

    def receive(self) -> bytes | None: ...

    def close(self) -> None: ...

    @property
    def closed(self) -> bool: ...


class StreamRegistry:
    """Tracks open streams; ``close_all`` (revocation) closes every handle and
    permanently refuses new streams."""

    def __init__(self) -> None:
        self._streams: dict[str, StreamHandle] = {}
        self._revoked = False

    def open(self, stream_id: str, handle: StreamHandle) -> None:
        if self._revoked:
            raise StreamClosed(stream_id)
        previous = self._streams.get(stream_id)
        if previous is not None and previous is not handle:
            previous.close()
        self._streams[stream_id] = handle

    def close(self, stream_id: str) -> None:
        handle = self._streams.pop(stream_id, None)
        if handle is not None:
            handle.close()

    def close_all(self) -> None:
        """Seal the registry and close every open handle (idempotent).

        Fail-closed ordering: the registry is sealed FIRST so no new stream
        can open and no dropped handle can be re-served; each handle's
        ``close()`` is still attempted even when an earlier one raises. Any
        close failures are surfaced as ``StreamCloseError`` AFTER the registry
        is sealed — a revoked device never retains a live registry-tracked
        stream and the denial state is unambiguous.
        """
        if self._revoked:
            return  # idempotent
        self._revoked = True
        failed: list[str] = []
        for stream_id in list(self._streams):
            handle = self._streams.pop(stream_id, None)
            if handle is None:
                continue
            try:
                handle.close()
            except Exception:
                failed.append(stream_id)
        if failed:
            raise StreamCloseError(failed)

    def is_open(self, stream_id: str) -> bool:
        return stream_id in self._streams

    def raise_if_open(self, stream_id: str) -> None:
        if not self.is_open(stream_id):
            raise StreamClosed(stream_id)
