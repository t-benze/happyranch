"""Standalone response-boundary checks using real HTTP and GIO, no display."""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import socket
import struct
import threading
import time
import unittest

from gi.repository import Gio, GLib

from app_scheme_webkit import AppScheme, LocalDaemon, Transfer


class ResponseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.release = threading.Event()
        release = self.release

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args: object) -> None:
                pass

            def do_GET(self) -> None:
                if self.path in ("/api/v1/health", "/api/v1/auth/bootstrap"):
                    data = (b'{"api_contract":{"current":1,"min_client":1}}' if self.path.endswith("health")
                            else b'{"token":"synthetic-response-test"}')
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return
                self.send_response(200)
                if self.path == "/api/v1/quiet":
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Transfer-Encoding", "chunked")
                    self.end_headers()
                    try:
                        self.wfile.write(b"d\r\ndata: first\n\n\r\n")
                        self.wfile.flush()
                        if release.wait(31):
                            return
                        self.wfile.write(b"c\r\ndata: late\n\n\r\n0\r\n\r\n")
                        self.wfile.flush()
                    except OSError:
                        pass
                elif self.path == "/api/v1/silent":
                    self.send_header("Content-Type", "text/event-stream")
                    self.end_headers()
                    # Wait on actual upstream disconnection, without heartbeat.
                    self.connection.settimeout(3)
                    try:
                        self.connection.recv(1)
                    except OSError:
                        pass
                elif self.path == "/api/v1/chunked":
                    self.send_header("Transfer-Encoding", "chunked")
                    self.end_headers()
                    self.wfile.write(b"3\r\nabc\r\ninvalid\r\n")
                else:
                    if self.path != "/api/v1/reset":
                        self.send_header("Content-Length", "3" if self.path == "/api/v1/complete" else "4096")
                    self.end_headers()
                    self.wfile.write(b"abc")
                    self.wfile.flush()
                    if self.path == "/api/v1/reset":
                        time.sleep(0.1)  # Deliver headers before the genuine reset.
                        self.connection.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
                self.close_connection = True

        class Server(ThreadingHTTPServer):
            def shutdown_request(self, request: socket.socket) -> None:
                # Inherited SHUT_WR can mask SO_LINGER's reset as orderly EOF.
                # Close directly so the unknown-length case proves TCP reset.
                self.close_request(request)

        self.server = Server(("127.0.0.1", 0), Handler)
        self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.server_thread.start()
        self.addCleanup(self.cleanup_server)
        self.app = AppScheme.__new__(AppScheme)
        self.app.daemon = LocalDaemon(self.server.server_port)
        self.app.Gio, self.app.GLib, self.app.closed = Gio, GLib, False
        self.app.transfers, self.app.cancelled = {}, {}

    def cleanup_server(self) -> None:
        self.release.set()
        self.server.shutdown()
        self.server.server_close()
        self.server_thread.join(2)

    def consume(self, path: str, cancel: str = "") -> dict:
        result = {"body": bytearray(), "error": None, "early_error": None}
        transfer = Transfer(self.app.daemon)
        transfer.cancellable = Gio.Cancellable()
        self.app.transfers["test"] = transfer
        loop = GLib.MainLoop()
        finished = threading.Event()

        def finish(_request: object, status: int, source: object, length: int, headers: dict) -> None:
            result.update(status=status, length=length, headers=headers)

            def consume_body() -> None:
                try:
                    while True:
                        chunk = source.read_bytes(65536, None).get_data()
                        if not chunk:
                            break
                        result["body"].extend(chunk)
                except GLib.Error as error:
                    result["error"] = error
                finally:
                    source.close(None)
                    finished.set()
                    GLib.idle_add(loop.quit)

            threading.Thread(target=consume_body, daemon=True).start()
            if cancel:
                def stop() -> bool:
                    if cancel == "lifetime":
                        transfer.deadline = time.monotonic() - 1
                        self.app.monitor()
                    else:
                        transfer.cancel()
                    return False
                GLib.timeout_add(200, stop)

        self.app.finish = finish
        self.app.error = lambda _request, error: (result.update(early_error=error), loop.quit())
        worker = threading.Thread(target=self.app.forward, args=(object(), transfer, "GET", path, {}, None), daemon=True)
        worker.start()

        def timeout() -> bool:
            result["timed_out"] = True
            transfer.cancel()
            loop.quit()
            return False

        timer = GLib.timeout_add(35000, timeout)
        began = time.monotonic()
        loop.run()
        if not result.get("timed_out"):
            GLib.source_remove(timer)
        transfer.cancel()
        worker.join(2)
        self.assertFalse(worker.is_alive(), "upstream worker must stop promptly")
        self.assertTrue(finished.wait(2), "body consumer must finish")
        self.assertIsNone(result["early_error"], "must exercise delivered response body")
        self.assertNotIn("timed_out", result)
        result["elapsed"] = time.monotonic() - began
        return result

    def test_quiet_stream_delivers_later_event(self) -> None:
        result = self.consume("/api/v1/quiet")
        self.assertIsNone(result["error"])
        self.assertEqual(result["body"], b"data: first\n\ndata: late\n\n")
        self.assertGreater(result["elapsed"], 30)

    def test_incomplete_bodies_error(self) -> None:
        for path in ("short", "reset", "chunked"):
            with self.subTest(path=path):
                result = self.consume("/api/v1/" + path)
                self.assertEqual(result["status"], 200)
                self.assertIsInstance(result["error"], GLib.Error, "body must error, not successful EOF")
                if path == "reset":
                    self.assertEqual(result["length"], -1, "reset must not rely on Content-Length truncation")
                if path == "short":
                    self.assertEqual(result["body"], b"abc")
                    self.assertEqual(result["length"], 4096)

    def test_complete_body_preserves_framing(self) -> None:
        result = self.consume("/api/v1/complete")
        self.assertEqual(result["body"], b"abc")
        self.assertIsNone(result["error"])
        self.assertEqual(result["length"], 3)

    def test_abort_and_lifetime_error_with_bounded_cleanup(self) -> None:
        for cancel in ("abort", "lifetime"):
            with self.subTest(cancel=cancel):
                result = self.consume("/api/v1/silent", cancel)
                self.assertIsInstance(result["error"], GLib.Error)
                self.assertLess(result["elapsed"], 2)


if __name__ == "__main__":
    unittest.main()
