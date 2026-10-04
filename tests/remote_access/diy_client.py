"""Away-client wire-contract CLI for the Supported-DIY acceptance harness
(THR-097 Unit 3A).

Implements the THR-034 wire contract that the signed macOS ``ClientBridge``
speaks — ``POST /pair`` redemption and ``X-HappyRanch-Device-Credential``
authenticated requests — over a REAL network path, so the acceptance proves
the connector's wire behavior without needing the macOS binary (whose signed
launch/Keychain/tsnet surface remains a separately reported residual gap).

This script is a TEST HARNESS CLIENT: it prints machine-readable JSON to
stdout for the acceptance test to assert against, and never logs the pairing
code or the issued credential. The default terminal-only stream output is
unchanged. Opt-in observation emits flushed admission/terminal records; idle
timeout, unrelated I/O and protocol/deadline failures have nonzero exits.
"""
from __future__ import annotations

import argparse
import http.client
import json
import sys
import socket
import threading
import time
from contextlib import contextmanager


def _request(host: str, port: int, method: str, path: str, body: bytes | None = None, credential: str | None = None) -> dict:
    conn = http.client.HTTPConnection(host, port, timeout=15)
    headers = {}
    if credential is not None:
        headers["X-HappyRanch-Device-Credential"] = credential
    conn.request(method, path, body=body, headers=headers)
    resp = conn.getresponse()
    raw = resp.read()
    conn.close()
    try:
        payload = json.loads(raw.decode("utf-8", errors="replace")) if raw else None
    except ValueError:
        payload = raw.decode("utf-8", errors="replace")[:200]
    return {"status": resp.status, "body": payload}


class AdmissionError(Exception):
    def __init__(self, kind: str):
        self.kind = kind
        super().__init__(kind)


def _remaining(deadline: float) -> float:
    left = deadline - time.monotonic()
    if left <= 0:
        raise AdmissionError("deadline")
    return left


def _read_first_sse_frame(response, deadline: float) -> int:
    """Read exactly one bounded frame; EOF is never positive admission."""
    if response.status != 200 or response.getheader("Content-Type", "").split(";", 1)[0] != "text/event-stream":
        raise AdmissionError("protocol_error")
    frame = bytearray()
    while len(frame) < 64:
        _remaining(deadline)
        chunk = response.read1(1)
        _remaining(deadline)
        if not chunk:
            raise AdmissionError("truncated")
        frame.extend(chunk)
        if frame.endswith(b"\n\n"):
            if frame != b"data: hello\n\n":
                raise AdmissionError("protocol_error")
            return len(frame)
    raise AdmissionError("oversized")


@contextmanager
def _admission_response(host: str, port: int, path: str, headers: dict):
    """One connect/header/body budget, including segmented internal reads.

    Capture the socket before Connection: close detaches conn.sock. The
    watchdog interrupts blocking headers/body and is joined on every exit.
    """
    deadline = time.monotonic() + 10
    conn = http.client.HTTPConnection(host, port, timeout=_remaining(deadline))
    response = None
    captured = None
    expired = threading.Event()
    def interrupt():
        expired.set()
        if captured is not None:
            try:
                captured.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
    watchdog = threading.Timer(_remaining(deadline), interrupt)
    watchdog.daemon = True
    watchdog.start()
    try:
        conn.connect()
        captured = conn.sock
        captured.settimeout(_remaining(deadline))
        conn.request("GET", path, headers=headers)
        _remaining(deadline)
        captured.settimeout(_remaining(deadline))
        response = conn.getresponse()
        _remaining(deadline)
        yield response, captured, deadline, watchdog
        _remaining(deadline) if expired.is_set() else None
    except (OSError, http.client.HTTPException):
        if expired.is_set() or time.monotonic() >= deadline:
            raise AdmissionError("deadline") from None
        raise
    finally:
        watchdog.cancel()
        watchdog.join(timeout=1)
        try:
            if response is not None:
                response.close()
        finally:
            conn.close()
        if watchdog.is_alive():
            raise AdmissionError("watchdog_cleanup")


def _emit_lifecycle_record(record: dict) -> None:
    print(json.dumps(record, ensure_ascii=True, separators=(",", ":")), flush=True)


def _observe_stream(args) -> int:
    status = None
    received = 0
    kind = "protocol_error"
    lifetime = time.monotonic() + 90
    try:
        with _admission_response(args.host, args.port, args.path, {
            "X-HappyRanch-Device-Credential": args.credential,
            "Accept": "text/event-stream",
        }) as (response, sock, deadline, watchdog):
            status = response.status
            received = _read_first_sse_frame(response, deadline)
            watchdog.cancel()
            watchdog.join(timeout=1)
            _emit_lifecycle_record({"phase": "admitted", "child_id": args.observation_id,
                                   "status": status, "sse": True, "first_frame_ok": True,
                                   "first_frame_bytes": 13, "received_bytes": received})
            while True:
                remaining = lifetime - time.monotonic()
                if remaining <= 0:
                    kind = "budget_exhausted"
                    break
                sock.settimeout(min(args.idle_timeout, remaining))
                try:
                    chunk = response.read1(4096)
                except TimeoutError:
                    kind = "budget_exhausted" if time.monotonic() >= lifetime else "timeout"
                    break
                except ConnectionResetError:
                    kind = "reset"
                    break
                except (OSError, http.client.HTTPException):
                    kind = "read_error"
                    break
                if not chunk:
                    kind = "eof"
                    break
                received += len(chunk)
                if received > 1048576:
                    received = 1048576
                    kind = "budget_exhausted"
                    break
    except AdmissionError as exc:
        kind = "deadline" if exc.kind == "deadline" else "protocol_error"
    except (OSError, http.client.HTTPException):
        kind = "read_error"
    _emit_lifecycle_record({"phase": "terminal", "child_id": args.observation_id,
                           "status": status, "kind": kind, "received_bytes": received})
    return {"eof": 0, "reset": 0, "timeout": 2, "read_error": 3}.get(kind, 4)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="diy-client")
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", type=int, required=True)
    sub = parser.add_subparsers(dest="command", required=True)

    redeem = sub.add_parser("redeem")
    redeem.add_argument("--code", required=True)

    request = sub.add_parser("request")
    request.add_argument("--method", default="GET")
    request.add_argument("--path", required=True)
    request.add_argument("--credential", required=True)

    stream = sub.add_parser("stream")
    stream.add_argument("--path", required=True)
    stream.add_argument("--credential", required=True)
    stream.add_argument("--observe-lifecycle", action="store_true")
    stream.add_argument("--observation-id", type=int)
    stream.add_argument("--idle-timeout", type=int, choices=(4, 5), default=5)

    connect = sub.add_parser("connect")
    connect.add_argument("--path", default="/api/v1/health")

    args = parser.parse_args(argv)
    if args.command == "redeem":
        result = _request(args.host, args.port, "POST", "/pair", body=args.code.encode())
    elif args.command == "request":
        result = _request(
            args.host, args.port, args.method, args.path, credential=args.credential
        )
    elif args.command == "stream":
        if args.observe_lifecycle:
            if args.observation_id is None or not 1 <= args.observation_id <= 8:
                parser.error("lifecycle requires observation-id 1..8")
            return _observe_stream(args)
        if args.observation_id is not None or args.idle_timeout != 5:
            parser.error("lifecycle options require observe-lifecycle")
        # Open the SSE stream and read until it closes (revocation closes it
        # fail-closed). Prints ONLY the status and received byte count —
        # never the credential or payload content.
        conn = http.client.HTTPConnection(args.host, args.port, timeout=30)
        conn.connect()
        conn.sock.settimeout(30)  # type: ignore[union-attr]
        conn.request(
            "GET",
            args.path,
            headers={
                "X-HappyRanch-Device-Credential": args.credential,
                "Accept": "text/event-stream",
            },
        )
        resp = conn.getresponse()
        status = resp.status
        received = 0
        try:
            while True:
                chunk = resp.read1(4096)
                if not chunk:
                    break
                received += len(chunk)
        except (http.client.IncompleteRead, OSError, ConnectionError, TimeoutError):
            pass  # stream closed by the connector (revocation) — expected
        conn.close()
        print(json.dumps({"status": status, "received_bytes": received}))
        return 0
    else:  # connect — no credential (e.g. direct bearer attempt)
        result = _request(args.host, args.port, "GET", args.path)
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
