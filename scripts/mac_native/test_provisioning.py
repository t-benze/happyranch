"""Portable native-probe logic; no network or tool installation in these tests."""

from __future__ import annotations

import copy
import hashlib
import io
import tarfile
import json
import tempfile
import time
import unittest
from unittest.mock import patch
from pathlib import Path

from scripts.mac_native.toolchain import PINS, environment, parse_pins, provision, verify_checksum


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
                             ("url", "https://example.com/node.tar.gz"),
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
            def download(argv: list[str], **_kwargs: object) -> None:
                Path(argv[argv.index("--output") + 1]).write_bytes(b"abc")
            root = Path(directory).resolve() / "provision"
            with patch("scripts.mac_native.toolchain.subprocess.run", side_effect=download):
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

        def download(argv: list[str], **_kwargs: object) -> None:
            path = Path(argv[argv.index("--output") + 1])
            path.write_bytes(payloads[path.name.split(".")[0]])

        with tempfile.TemporaryDirectory() as directory:
            for aggregate, per_archive, succeeds in ((4, 2, True), (3, 2, False), (4, 1, False)):
                with self.subTest(aggregate=aggregate, per_archive=per_archive):
                    root = Path(directory).resolve() / f"tools-{aggregate}-{per_archive}"
                    with (patch("scripts.mac_native.toolchain.subprocess.run", side_effect=download),
                          patch("scripts.mac_native.toolchain.MEMBER_CAP", aggregate),
                          patch("scripts.mac_native.toolchain.ARCHIVE_MEMBER_CAP", per_archive)):
                        if succeeds:
                            env = provision(root, pins, time.monotonic() + 5)
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


if __name__ == "__main__":
    unittest.main()
