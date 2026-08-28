"""Away-client wire-contract CLI for the Supported-DIY acceptance harness
(THR-097 Unit 3A).

Implements the THR-034 wire contract that the signed macOS ``ClientBridge``
speaks — ``POST /pair`` redemption and ``X-HappyRanch-Device-Credential``
authenticated requests — over a REAL network path, so the acceptance proves
the connector's wire behavior without needing the macOS binary (whose signed
launch/Keychain/tsnet surface remains a separately reported residual gap).

This script is a TEST HARNESS CLIENT: it prints machine-readable JSON to
stdout for the acceptance test to assert against, and never logs the pairing
code or the issued credential.
"""
from __future__ import annotations

import argparse
import http.client
import json
import sys


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
