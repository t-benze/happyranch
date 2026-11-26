"""Standalone native boundary exercises; no daemon, GI, installs or retired suite."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from connection_contract import Assets, Protocol, parse_json


class ConnectionBoundaryTests(unittest.TestCase):
    def test_enable_completion_protocol_closed(self) -> None:
        good = dict(code="enablePreparationFailed", category="inventory", effect="unknown", recovery="nativeRecovery")
        error_schema = self.protocol.schema["$defs"]["error"]
        self.protocol.validate(good, error_schema)
        for changes in (dict(category="unknown"), dict(category=None), dict(effect="none"),
                        dict(effect="confirmed"), dict(recovery="refresh"), dict(message="synthetic"),
                        dict(code="unavailable")):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.protocol.validate(good | changes, error_schema)
        self.protocol.validate(good | dict(code="hostStateUnrecognized"), error_schema)
        missing = {k: v for k, v in good.items() if k != "category"}
        with self.assertRaises(ValueError):
            self.protocol.validate(missing, error_schema)
        event = dict(version=1, operationId="a"*32, method="Enable", outcome="failed", error=good)
        self.protocol.validate(event, self.protocol.events["host.enableFinished"])
        for changes in (dict(operationId="A"*32), dict(outcome="completed"), dict(extra=1),
                        dict(error=good | dict(effect="none")), dict(method="Disable")):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.protocol.validate(event | changes, self.protocol.events["host.enableFinished"])
        completed = dict(code="enableCompleted", effect="unknown", recovery="refresh")
        self.protocol.validate(completed, self.protocol.schema["$defs"]["enableCompleted"])
        with self.assertRaises(ValueError):
            self.protocol.validate(completed, error_schema)

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


    def test_firewall_status_projection(self) -> None:
        snapshot = dict(setup='ready', runtime='available', observation='known', devices=[],
                        pending=None, selected=None, inviteState='unknown', expiresAt=None)
        detail = dict(version=2, setup=dict(state='ready', age_seconds=0),
                      runtime=dict(state='available', age_seconds=0),
                      invite=dict(state='unknown', age_seconds=None, deadline_utc_seconds=None),
                      address=dict(state='unknown', age_seconds=None),
                      policy=dict(state='unknown', age_seconds=None),
                      cleanup=dict(state='unknown', age_seconds=None))
        for state in ('host_firewall_blocks_home', 'host_firewall_blocks_tcp_8443',
                      'host_firewall_blocks_udp_3478', 'unverified'):
            with self.subTest(state=state):
                raw = json.dumps(detail | dict(firewall=dict(state=state, age_seconds=0)))
                result = self.protocol.host_details(snapshot, raw)
                self.assertEqual(result, snapshot | dict(firewallWarning=state))
                self.protocol.validate(result, self.protocol.schema['$defs']['host'])
        for state, age in [('clear', 0), ('stale', 31), ('host_firewall_blocks_home', 31)]:
            self.assertEqual(self.protocol.host_details(snapshot,
                json.dumps(detail | dict(firewall=dict(state=state, age_seconds=age)))), snapshot)
        for firewall in (dict(state='/private/canary', age_seconds=0),
                         dict(state='unverified', age_seconds=True),
                         dict(state='unverified', age_seconds=0, rules='private')):
            with self.assertRaises(ValueError):
                self.protocol.host_details(snapshot, json.dumps(detail | dict(firewall=firewall)))


if __name__ == "__main__":
    unittest.main(verbosity=2)
