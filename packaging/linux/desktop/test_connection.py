"""Standalone native boundary exercises; no daemon, GI, installs or retired suite."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from connection_contract import Assets, Protocol, parse_json


class ConnectionBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.scratch = tempfile.TemporaryDirectory()
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name)
        self.entry = self.root / "index.html"
        self.entry.write_bytes(b"<html>synthetic packaged entry</html>")
        self.record = {"path": "index.html", "bytes": self.entry.stat().st_size,
                       "sha256": hashlib.sha256(self.entry.read_bytes()).hexdigest()}
        self.publish()
        self.protocol = Protocol(Path(__file__).parents[3] / "protocol/native-connection-bridge.v1.json")

    def publish(self) -> None:
        (self.root / "resource-manifest.json").write_text(json.dumps(
            {"version": 1, "entry": "index.html", "resources": [self.record]}))

    def test_asset_boundary(self) -> None:
        assets = Assets(self.root)
        self.assertEqual(assets.asset("index.html"), b"<html>synthetic packaged entry</html>")
        with self.assertRaises(ValueError):
            assets.asset("unknown.js")
        self.entry.write_bytes(b"changed after admission")
        with self.assertRaises(ValueError):
            assets.asset("index.html")

    def test_manifest_boundary(self) -> None:
        for field, wrong in (("bytes", True), ("bytes", 1), ("sha256", "0" * 64),
                             ("path", "../index.html")):
            with self.subTest(field=field, wrong=wrong):
                original = dict(self.record)
                self.record[field] = wrong
                self.publish()
                with self.assertRaises(ValueError):
                    Assets(self.root)
                self.record = original
        self.publish()
        extra = self.root / "extra.js"
        extra.write_text("unlisted")
        with self.assertRaises(ValueError):
            Assets(self.root)
        extra.unlink()
        assets = Assets(self.root)
        with tempfile.TemporaryDirectory() as external:
            outside = Path(external) / "outside.html"
            outside.write_bytes(self.entry.read_bytes())
            self.entry.unlink()
            self.entry.symlink_to(outside)
            with self.assertRaises((ValueError, OSError)):
                Assets(self.root)
            with self.assertRaises((ValueError, OSError)):
                assets.asset("index.html")

    def test_json_boundary(self) -> None:
        for raw in ('{', '{"x":1,"x":2}', '{"x":NaN}', '[' * 18 + '0' + ']' * 18,
                    '{}' + ' ' * 4096):
            with self.subTest(raw=raw[:80]), self.assertRaises(ValueError):
                parse_json(raw, 4096)

    def test_projection_boundary(self) -> None:
        hello = self.protocol.hello()
        self.assertEqual(hello["roles"], ["host", "client"])
        self.assertEqual([c["command"] for c in hello["capabilities"] if c["availability"] == "available"],
                         ["host.status", "host.enable", "host.turnOff", "host.createInvite",
                          "host.approveDevice", "host.rejectDevice", "host.nextDevice",
                          "host.revokeDevice", "client.status", "client.acceptInvite", "client.savedComputers",
                          "client.connectSaved", "client.removeSaved", "client.disconnect",
                          "client.cancelAttempt", "client.retry", "client.retrySave", "client.health"])
        device = {"label": "Computer &amp; desk", "fingerprint": "a" * 24,
                  "comparison_phrase": "synthetic phrase"}
        raw = {"phase": "ready", "ready": False, "devices": [device], "pending": None, "selected": device}
        good_raw = json.dumps(raw)
        snapshot = self.protocol.host(good_raw)
        self.assertEqual(snapshot["runtime"], "offline")
        self.assertEqual(snapshot["devices"], [{"label": "Computer & desk", "comparisonPhrase": "synthetic phrase"}])
        self.assertNotIn("fingerprint", json.dumps(snapshot))
        self.assertEqual((snapshot["inviteState"], snapshot["expiresAt"]), ("unknown", None))
        for case, label, expected in (
                ("86 CJK characters", "界" * 86, "界" * 84 + "…"),
                ("exactly 256 bytes", "界" * 85 + "a", "界" * 85 + "a"),
                ("one byte over", "界" * 85 + "ab", "界" * 84 + "…"),
                ("four-byte split", "😀" * 64 + "a", "😀" * 63 + "…"),
                ("marker fills bound", "界" * 84 + "ab界", "界" * 84 + "a…"),
                ("bound after HTML decoding", "界" * 85 + "&amp;", "界" * 85 + "&")):
            with self.subTest(case=case):
                labelled = {**device, "label": label}
                snapshot = self.protocol.host(json.dumps(
                    {**raw, "devices": [device, labelled], "pending": labelled, "selected": labelled}))
                projected = {"label": expected, "comparisonPhrase": "synthetic phrase"}
                self.assertEqual(snapshot["devices"], [
                    {"label": "Computer & desk", "comparisonPhrase": "synthetic phrase"}, projected])
                self.assertEqual(snapshot["pending"], projected)
                self.assertEqual(snapshot["selected"], projected)
                self.assertEqual((snapshot["runtime"], snapshot["observation"]), ("offline", "known"))
                self.assertLessEqual(len(snapshot["selected"]["label"].encode("utf-8")), 256)
        for change in ({"devices": [None]}, {"selected": {**device, "fingerprint": "b" * 24}},
                       {"pending": {**device, "label": "x" * 577}},
                       {"pending": {**device, "comparison_phrase": "x" * 577}},
                       {"pending": {**device, "fingerprint": "bad"}},
                       {"pending": {**device, "secret": "synthetic-secret"}}, {"ready": 1}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.protocol.host(json.dumps({**raw, **change}))
        for command in ("host.enable", "host.turnOff", "host.createInvite"):
            self.assertIn({"command": command, "availability": "available"}, hello["capabilities"])
            self.protocol.validate({}, self.protocol.commands[command]["args"])
            with self.assertRaises(ValueError):
                self.protocol.validate({"invite": "synthetic"}, self.protocol.commands[command]["args"])
        raw["devices"][0]["label"] = "\u202ebidi"
        with self.assertRaises(ValueError):
            self.protocol.host(json.dumps(raw))
        with self.assertRaises(ValueError):
            self.protocol.host(good_raw + " " * 32768)


if __name__ == "__main__":
    unittest.main(verbosity=2)
