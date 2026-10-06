"""Scripted I/O only: no socket, timer thread, or daemon is created."""
from __future__ import annotations

import time

import pytest

from tests.remote_access import diy_client as client


@pytest.mark.parametrize("fault", ["cancel", "join", "response", "connection", "socket", "alive", "note"])
def test_admission_finalizers_preserve_primary_and_attempt_every_resource(monkeypatch, fault):
    events = []
    class Primary(RuntimeError):
        def add_note(self, value):
            if fault == "note":
                raise OSError("private note failure")
            super().add_note(value)
    primary = Primary("private primary")

    def operation(name):
        events.append(name)
        if name == fault or (fault == "note" and name == "cancel"):
            raise OSError("private cleanup")

    class Socket:
        def settimeout(self, value):
            pass
        def close(self):
            operation("socket")

    class Response:
        def close(self):
            operation("response")

    response = Response()

    class Connection:
        sock = Socket()
        def __init__(self, *args, **kwargs):
            pass
        def connect(self):
            pass
        def request(self, *args, **kwargs):
            pass
        def getresponse(self):
            return response
        def close(self):
            operation("connection")

    class Timer:
        def __init__(self, *args):
            pass
        def start(self):
            pass
        def cancel(self):
            operation("cancel")
        def join(self, **kwargs):
            operation("join")
        def is_alive(self):
            operation("alive")
            return False

    monkeypatch.setattr(client.http.client, "HTTPConnection", Connection)
    monkeypatch.setattr(client.threading, "Timer", Timer)
    with pytest.raises(RuntimeError) as caught:
        with client._admission_response("scripted", 0, "/tail", {}):
            raise primary
    assert caught.value is primary, "cleanup replaced the primary exception"
    assert {"cancel", "join", "response", "connection", "socket", "alive"} <= set(events), events
    assert "private cleanup" not in str(getattr(primary, "__notes__", ()))


@pytest.mark.parametrize("data,kind", [(b"", "truncated"), (b"bad\n\n", "protocol_error"),
                                      (b"x" * 64, "oversized")])
def test_scripted_first_frame_refuses_invalid_input(data, kind):
    class Response:
        status = 200
        def getheader(self, *args):
            return "text/event-stream"
        def read1(self, count):
            nonlocal data
            chunk, data = data[:count], data[count:]
            return chunk
    with pytest.raises(client.AdmissionError) as caught:
        client._read_first_sse_frame(Response(), time.monotonic() + 1)
    assert caught.value.kind == kind


def test_scripted_first_frame_reads_only_admission():
    data = bytearray(b"data: hello\n\n" + b"held response")
    class Response:
        status = 200
        def getheader(self, *args):
            return "text/event-stream"
        def read1(self, count):
            chunk = bytes(data[:count])
            del data[:count]
            return chunk
    assert client._read_first_sse_frame(Response(), time.monotonic() + 1) == 13
    assert data == b"held response", "admission drained the held body"
