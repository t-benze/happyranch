"""Real loopback HTTP relay; loss happens only AFTER independent commit proof."""
from __future__ import annotations

import hashlib
import http.client
import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable


class Relay:
    def __init__(self, upstream: int, proof: Callable[[dict], None]) -> None:
        self.upstream = upstream
        self.proof = proof
        self.records: list[dict] = []
        self.armed: tuple[str, str] | None = None
        self.error: str | None = None
        relay = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args: object) -> None:
                pass

            def do_POST(self) -> None:
                self.forward()

            def do_GET(self) -> None:
                self.forward()

            def forward(self) -> None:
                raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                digest = hashlib.sha256(raw).hexdigest()
                connection = http.client.HTTPConnection("127.0.0.1", relay.upstream, timeout=25)
                try:
                    connection.request(self.command, self.path, body=raw, headers=dict(self.headers))
                    response = connection.getresponse()
                    body = response.read()
                    parsed = json.loads(body)
                    record = dict(path=self.path, method=self.command, body_sha256=digest,
                                  status=response.status, response=parsed, dropped=False)
                    if (relay.armed is not None and self.command == "POST"
                            and relay.armed == (self.path, json.loads(raw).get("session_id"))):
                        relay.armed = None
                        if response.status != 200 or parsed != {"ok": True}:
                            raise AssertionError("loss relay requires actual upstream 200/ok")
                        relay.proof(json.loads(raw))
                        record["committed_before_drop"] = True
                        record["dropped"] = True
                        relay.records.append(record)
                        self.close_connection = True
                        self.connection.shutdown(socket.SHUT_RDWR)
                        self.connection.close()
                        return
                    relay.records.append(record)
                    self.send_response(response.status)
                    for key, value in response.getheaders():
                        if key.lower() not in {"connection", "transfer-encoding", "content-length"}:
                            self.send_header(key, value)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                except Exception as exc:
                    relay.error = repr(exc)
                    self.close_connection = True
                finally:
                    connection.close()

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_port
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
