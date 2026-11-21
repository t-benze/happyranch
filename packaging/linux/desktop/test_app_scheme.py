"""Standalone native transport checks; system stdlib, no retired pytest suite."""
from __future__ import annotations

from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import tempfile
import threading
import unittest

from app_scheme import Bundle, LocalDaemon, ORIGIN, SchemeError, route


class BundleTests(unittest.TestCase):
    def setUp(self) -> None:
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name)
        (self.root / "index.html").write_bytes(b"<html>app</html>")
        (self.root / "assets").mkdir()
        (self.root / "assets/app.js").write_bytes(b"export default 42")
        (self.root / "fonts").mkdir()
        (self.root / "fonts/app.woff2").write_bytes(b"font")
        self.bundle = Bundle(self.root)

    def test_packaged_bytes_mime_and_spa_fallback(self) -> None:
        for path, data, mime in (("/", b"<html>app</html>", "text/html"),
                                 ("/settings/connection", b"<html>app</html>", "text/html"),
                                 ("/assets/app.js", b"export default 42", "text/javascript"),
                                 ("/fonts/app.woff2", b"font", "font/woff2")):
            with self.subTest(path=path):
                self.assertEqual(self.bundle.get(path), (data, mime))
        for path in ("/api", "/api/v2/unknown", "/assets", "/assets/"):
            with self.subTest(path=path), self.assertRaises(SchemeError):
                self.bundle.get(path)

    def test_traversal_origin_and_symlink_escape(self) -> None:
        for uri in (ORIGIN + "/../outside", ORIGIN + "/%2e%2e/outside",
                    ORIGIN + "/%252e%252e/outside", ORIGIN + "/assets/%2f../outside",
                    ORIGIN + "/assets/%5coutside", ORIGIN + "/%00", ORIGIN + "/bad%zz",
                    "happyranch-app://other/", "happyranch-app://app@other/", ORIGIN + "/#x"):
            with self.subTest(uri=uri), self.assertRaises(SchemeError):
                route(uri)
        outside = self.root / "private"
        outside.write_bytes(b"must not be served")
        asset = self.root / "assets/app.js"
        asset.unlink()
        asset.symlink_to(outside)
        with self.assertRaises(SchemeError):
            self.bundle.get("/assets/app.js")
        asset.unlink()
        (self.root / "assets").rmdir()
        (self.root / "assets").symlink_to(self.root)
        with self.assertRaises(SchemeError):
            self.bundle.get("/assets/app.js")
        with self.assertRaises(ValueError):
            Bundle(self.root)

    def test_allowlist_and_delivery_pin(self) -> None:
        (self.root / "added-after-start.txt").write_bytes(b"not admitted")
        self.assertEqual(self.bundle.get("/added-after-start.txt")[0], b"<html>app</html>")
        (self.root / "index.html").write_bytes(b"replaced")
        with self.assertRaises(SchemeError):
            self.bundle.get("/settings")


class LocalTransportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.seen = []
        seen = self.seen
        self.reject = False
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args: object) -> None:
                pass

            def do_GET(self) -> None:
                body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                seen.append((self.command, self.path, dict(self.headers), body))
                if self.path == "/api/v1/health":
                    data, status = b'{"api_contract":{"current":1,"min_client":1}}', 200
                elif self.path == "/api/v1/auth/bootstrap":
                    data = b'{"token":"synthetic-native-bearer"}'
                    status = 200
                elif self.path == "/api/v1/redirect":
                    data, status = b"", 302
                elif fixture.reject:
                    fixture.reject = False
                    data, status = b"", 401
                else:
                    data, status = body or b'{"ok":true}', 200
                self.send_response(status)
                if status == 302:
                    self.send_header("Location", "/api/v1/must-not-follow")
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            do_POST = do_PUT = do_GET

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.finish)
        self.daemon = LocalDaemon(self.server.server_port)
        self.daemon.bootstrap()

    def finish(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)

    def test_json_and_byte_multipart_native_authority(self) -> None:
        values = [("GET", b"", "application/json"),
                  ("POST", '{"hello":"你好"}'.encode(), "application/json"),
                  ("PUT", b'{"value":2}', "application/json"),
                  ("POST", b'--b\r\nContent-Disposition: form-data; name="file"; filename="x"\r\n\r\n\x00\xff\r\n--b--\r\n',
                   "multipart/form-data; boundary=b")]
        for method, body, content_type in values:
            with self.subTest(method=method, content_type=content_type):
                headers = {"Authorization": "page-bearer", "Cookie": "page-cookie", "Forwarded": "for=elsewhere",
                           "X-Forwarded-For": "elsewhere", "X-HappyRanch-Device-Credential": "page-identity",
                           "X-Tailscale-User": "page-user", "Proxy-Authorization": "page-proxy",
                           "Content-Type": content_type, "Last-Event-ID": "42", "If-Match": "version",
                           "Content-Length": "999999", "Host": "other:1", "Accept-Encoding": "gzip"}
                with closing(self.daemon.connection()) as connection:
                    response = self.daemon.send(connection, method, "/api/v1/echo?q=1", headers, body)
                    self.assertEqual(response.status, 200)
                    self.assertEqual(response.read(), body or b'{"ok":true}')
                actual_method, target, actual_headers, actual_body = self.seen[-1]
                actual_headers = {k.lower(): v for k, v in actual_headers.items()}
                self.assertEqual((actual_method, target, actual_body), (method, "/api/v1/echo?q=1", body))
                self.assertEqual(actual_headers["authorization"], "Bearer synthetic-native-bearer")
                self.assertEqual(actual_headers["content-length"], str(len(body)))
                self.assertEqual(actual_headers["content-type"], content_type)
                self.assertEqual(actual_headers["last-event-id"], "42")
                self.assertEqual(actual_headers["if-match"], "version")
                self.assertEqual(actual_headers["accept-encoding"], "identity")
                self.assertEqual(actual_headers["host"], f"127.0.0.1:{self.server.server_port}")
                self.assertFalse(set(actual_headers) & {"cookie", "forwarded", "x-forwarded-for",
                    "x-happyranch-device-credential", "x-tailscale-user", "proxy-authorization"})
        self.assertEqual(sum(path == "/api/v1/auth/bootstrap" for _, path, _, _ in self.seen), 1)

    def test_reset_and_401_rebootstrap_without_replaying_mutation(self) -> None:
        self.daemon.reset()
        self.assertIsNone(self.daemon._bearer)
        self.daemon.bootstrap()
        self.reject = True
        with closing(self.daemon.connection()) as connection:
            response = self.daemon.send(connection, "POST", "/api/v1/echo", {}, b"mutation")
            self.assertEqual(response.status, 401)
            response.close()
        self.daemon.bootstrap()
        with closing(self.daemon.connection()) as connection:
            response = self.daemon.send(connection, "GET", "/api/v1/echo", {}, b"")
            self.assertEqual(response.status, 200)
            response.read()
        self.assertEqual(sum(path == "/api/v1/auth/bootstrap" for _, path, _, _ in self.seen), 3)
        self.assertEqual(sum(method == "POST" for method, *_ in self.seen), 1)

    def test_redirect_and_non_api_never_forward(self) -> None:
        with closing(self.daemon.connection()) as connection, self.assertRaises(SchemeError) as caught:
            self.daemon.send(connection, "GET", "/api/v1/redirect", {}, b"")
        self.assertEqual((caught.exception.status, caught.exception.code), (502, "upstream_redirect"))
        for target in ("/settings", "//other/api/v1/echo", "/api/v1/auth/bootstrap"):
            count = len(self.seen)
            with closing(self.daemon.connection()) as connection, self.assertRaises(SchemeError):
                self.daemon.send(connection, "GET", target, {}, b"")
            self.assertEqual(len(self.seen), count)
        self.assertNotIn("/api/v1/must-not-follow", [path for _, path, _, _ in self.seen])


if __name__ == "__main__":
    unittest.main()
