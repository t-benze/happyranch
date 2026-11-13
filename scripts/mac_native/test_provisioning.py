"""Portable native-probe logic; no network or tool installation in these tests."""

from __future__ import annotations

import copy
import hashlib
import io
import tarfile
import json
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from pathlib import Path

from scripts.mac_native.toolchain import (
    METRICS, STDERR_CAP, PINS, curl_attempt, environment, parse_pins, provision, verify_checksum,
)


class ProvisioningTests(unittest.TestCase):
    def test_pin_table_accepts_only_exact_official_pins(self) -> None:
        pins = parse_pins(PINS.read_text())
        self.assertEqual(pins["uv"]["version"], "0.12.21")
        self.assertEqual(pins["node"]["version"], "24.19.0")
        self.assertEqual(pins["go"]["version"], "1.23.12")
        for value in ("1.26.1", "1.23.11", "1.23.120"):
            broken = copy.deepcopy(pins)
            broken["go"]["version"] = value
            with self.assertRaises(ValueError):
                parse_pins(json.dumps(broken))
        for field, value in (("version", "24.x"), ("version", "26.7.0"),
                             ("origins", ["https://example.com/node.tar.gz"]),
                             ("origins", []), ("origins", "https://nodejs.org/"),
                             ("official_sources", ["https://example.com/"]),
                             ("sha256", "not-a-digest"), ("checksum_source", "https://example.com/checksums")):
            with self.subTest(field=field, value=value):
                broken = copy.deepcopy(pins)
                broken["node"][field] = value
                with self.assertRaises(ValueError):
                    parse_pins(json.dumps(broken))
        with self.assertRaises(ValueError):
            parse_pins('{"uv":{},"uv":{},"node":{}}')
        with self.assertRaises(ValueError):
            parse_pins(json.dumps({**pins, "other": {}}))

    def test_checksum_refuses_mismatch_before_use(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "archive"
            archive.write_bytes(b"abc")
            verify_checksum(archive, "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad", time.monotonic() + 5)
            with self.assertRaisesRegex(ValueError, "SHA256 mismatch"):
                verify_checksum(archive, "0" * 64, time.monotonic() + 5)
            with self.assertRaises(TimeoutError):
                verify_checksum(archive, "0" * 64, time.monotonic() - 1)
            # Replace only transport; production checksum and extraction remain real.
            def download(_url: str, archive: Path, *_args: object) -> dict:
                archive.write_bytes(b"abc")
                return {"curl_exit_code": 0}
            root = Path(directory).resolve() / "provision"
            with patch("scripts.mac_native.toolchain.curl_attempt", side_effect=download):
                with self.assertRaisesRegex(ValueError, "SHA256 mismatch"):
                    provision(root, parse_pins(PINS.read_text()), time.monotonic() + 5)
            self.assertEqual([p.name for p in root.iterdir()], ["uv.tar.gz"])

    def test_verified_archives_enforce_member_bounds(self) -> None:
        pins = parse_pins(PINS.read_text())
        payloads = {}
        for name, files in {
            "uv": ["uv-aarch64-apple-darwin/uv"],
            "node": ["node-v24.19.0-darwin-arm64/bin/node", "node-v24.19.0-darwin-arm64/bin/npm"],
            "go": ["go/bin/go"],
        }.items():
            data = io.BytesIO()
            with tarfile.open(fileobj=data, mode="w:gz") as bundle:
                for filename in files:
                    member = tarfile.TarInfo(filename)
                    member.size, member.mode = 3, 0o755
                    bundle.addfile(member, io.BytesIO(b"abc"))
            payloads[name] = data.getvalue()
            pins[name]["sha256"] = hashlib.sha256(payloads[name]).hexdigest()

        def download(url: str, path: Path, *_args: object) -> dict:
            if url == pins["go"]["origins"][0]:
                path.write_bytes(b"partial")
                return {"curl_exit_code": 28}
            path.write_bytes(payloads[path.name.split(".")[0]])
            return {"curl_exit_code": 0}

        with tempfile.TemporaryDirectory() as directory:
            for aggregate, per_archive, succeeds in ((4, 2, True), (3, 2, False), (4, 1, False)):
                with self.subTest(aggregate=aggregate, per_archive=per_archive):
                    root = Path(directory).resolve() / f"tools-{aggregate}-{per_archive}"
                    with (patch("scripts.mac_native.toolchain.curl_attempt", side_effect=download),
                          patch("scripts.mac_native.toolchain.MEMBER_CAP", aggregate),
                          patch("scripts.mac_native.toolchain.ARCHIVE_MEMBER_CAP", per_archive)):
                        if succeeds:
                            attempts = []
                            env = provision(root, pins, time.monotonic() + 5, attempts)
                            self.assertEqual([r["curl_exit_code"] for r in attempts], [0, 0, 28, 0])
                            self.assertTrue(attempts[-1]["sha256_verified"])
                            self.assertEqual(env["GOROOT"], str(root / "go"))
                            self.assertEqual((root / "go/bin/go").read_bytes(), b"abc")
                            self.assertEqual(list(root.glob("*.tar.gz")), [])
                        else:
                            with self.assertRaisesRegex(ValueError, "archive bounds/root"):
                                provision(root, pins, time.monotonic() + 5)

    def test_path_additions_and_caches_are_private(self) -> None:
        pins = parse_pins(PINS.read_text())
        inherited = {"PATH": "/host/user/bin", "HOME": "/job/home", "GOFLAGS": "-mod=readonly"}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve() / "tools"
            env = environment(root, pins, inherited)
            self.assertEqual(env["PATH"].split(":")[:3], [
                str(root / "uv-aarch64-apple-darwin"),
                str(root / "node-v24.19.0-darwin-arm64/bin"), str(root / "go/bin")])
            self.assertEqual(env["PATH"].split(":")[3:], [
                "/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin", "/usr/sbin", "/sbin"])
            for key in ("UV_CACHE_DIR", "npm_config_cache", "GOROOT", "GOPATH", "GOCACHE", "GOMODCACHE"):
                self.assertTrue(Path(env[key]).is_relative_to(root), key)
            self.assertEqual(env["GOTOOLCHAIN"], "local")
            self.assertEqual(env["GOFLAGS"], "-mod=readonly")
            self.assertEqual(inherited, {"PATH": "/host/user/bin", "HOME": "/job/home", "GOFLAGS": "-mod=readonly"})
            with self.assertRaises(ValueError):
                environment(Path("relative"), pins, inherited)
            alias = Path(directory) / "alias"
            alias.symlink_to(root.parent, target_is_directory=True)
            with self.assertRaises(ValueError):
                environment(alias / "tools", pins, inherited)

    def test_ordered_fallback_keeps_evidence_and_never_opens_bad_hash(self) -> None:
        pins = parse_pins(PINS.read_text())
        go = pins["go"]
        for success_on_second in (False, True):
            with self.subTest(success_on_second=success_on_second), tempfile.TemporaryDirectory() as directory:
                attempts, calls = [], []

                def download(url: str, archive: Path, seconds: float, deadline: float) -> dict:
                    # A failed partial must not contaminate the next origin.
                    self.assertFalse(archive.exists())
                    self.assertLessEqual(seconds, 30)
                    self.assertLessEqual(deadline, overall)
                    calls.append(url)
                    archive.write_bytes(b"synthetic untrusted archive")
                    code = 0 if success_on_second and len(calls) == 2 else 28
                    return {"curl_exit_code": code, "stderr_tail": "curl: (28) SSL connection timeout"}

                root = Path(directory).resolve() / "tools"
                overall = time.monotonic() + 5
                with (patch("scripts.mac_native.toolchain.curl_attempt", side_effect=download),
                      patch("scripts.mac_native.toolchain.tarfile.open") as tar_open):
                    error = "SHA256 mismatch" if success_on_second else "all official origins failed"
                    with self.assertRaisesRegex((RuntimeError, ValueError), error):
                        provision(root, {"go": go, "uv": pins["uv"], "node": pins["node"]}, overall, attempts)
                self.assertEqual(calls, go["origins"][:2] if success_on_second else go["origins"])
                self.assertEqual([r["curl_exit_code"] for r in attempts], [28, 0] if success_on_second else [28] * 3)
                self.assertTrue(all(r["artifact"] == "go" and not r["sha256_verified"] for r in attempts))
                self.assertIn("SSL connection timeout", attempts[0]["stderr_tail"])
                tar_open.assert_not_called()
                self.assertFalse((root / "go").exists())

    def test_curl_retains_bounded_metrics_without_secrets_and_reaps_timeout(self) -> None:
        real_popen = subprocess.Popen
        values = ["000", "https://user:synthetic-password@release-assets.githubusercontent.com/secret-path?token=synthetic-token",
                  "1", "192.0.2.1", "0.01", "0.02", "0.03", "0.04", "10.01", "123"]
        stderr = ("x" * 10000 + "\nAuthorization: synthetic-header-secret\n"
                  "Set-Cookie: synthetic-cookie-secret\n"
                  "curl: (35) https://user:synthetic-password@example.test/?token=synthetic-token\n"
                  "curl: (28) Operation timed out after 10000 milliseconds with 123 bytes received\n")
        calls = []
        script = "import sys; sys.stdout.write(" + repr("\n".join(values) + "\n") + "); sys.stderr.write(" + repr(stderr) + "); sys.exit(28)"

        def launch(argv: list[str], **kwargs: object) -> subprocess.Popen:
            calls.append(argv)
            return real_popen([sys.executable, "-c", script], **kwargs)

        with tempfile.TemporaryDirectory() as directory:
            with patch("scripts.mac_native.toolchain.subprocess.Popen", side_effect=launch):
                row = curl_attempt("https://go.dev/dl/go1.23.12.darwin-arm64.tar.gz",
                                   Path(directory) / "go.tar.gz", 2, time.monotonic() + 3)
            self.assertEqual(row["curl_exit_code"], 28)
            self.assertEqual(set(row["metrics"]), set(METRICS))
            self.assertEqual(row["metrics"]["url_effective"], "https://release-assets.githubusercontent.com/[path omitted]")
            self.assertEqual(row["metrics"]["remote_ip"], "192.0.2.1")
            self.assertEqual(row["metrics"]["time_total"], "10.01")
            self.assertEqual(row["metrics"]["size_download"], "123")
            self.assertIn("10000 milliseconds with 123 bytes", row["stderr_tail"])
            self.assertLessEqual(len(row["stderr_tail"].encode()), STDERR_CAP)
            self.assertNotIn("synthetic-", json.dumps(row))
            argv = calls[0]
            self.assertEqual(argv[:2], ["/usr/bin/curl", "-q"])
            for flag, value in (("--proto", "=https"), ("--proto-redir", "=https"),
                                ("--proxy", ""), ("--connect-timeout", "10"), ("--max-time", "2")):
                self.assertEqual(argv[argv.index(flag) + 1], value)
            script = "import time; time.sleep(10)"
            with patch("scripts.mac_native.toolchain.subprocess.Popen", side_effect=launch):
                row = curl_attempt("https://go.dev/", Path(directory) / "timeout", 1, time.monotonic() + .05)
            self.assertEqual(row["error"], "TimeoutError")
            self.assertEqual(row["curl_exit_code"], -9)
            self.assertTrue(all(value is None for value in row["metrics"].values()))


if __name__ == "__main__":
    unittest.main()
