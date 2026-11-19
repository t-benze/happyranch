"""Standalone custody contracts; fake GI plus opt-in disposable real keyring."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

import client_model as owner

store = owner._store


def record(node: str = "node-one") -> object:
    return owner._native.SavedRecord(version=1, bearer="hrpin_1_" + "b" * 64,
        home_id="synthetic-home", control_plane_id="synthetic-control", stable_node_id=node,
        connector_id="connector-one", control_url="https://control.invalid", ca_der=b"synthetic-DER",
        fingerprint=hashlib.sha256(b"synthetic-DER").hexdigest(), dial_ip="100.64.0.1",
        home_endpoint="100.64.0.1:443", profile_id="a" * 32, hostname="happyranch-linux-" + "a" * 32,
        network_state=json.dumps(dict(version=1, controlPlaneID="synthetic-control", stableNodeID=node,
                                      store={"machine-key": "synthetic-private-canary"})).encode())


class RemoteError(Exception):
    def __init__(self, remote: str) -> None:
        self.remote = remote
        super().__init__("secret diagnostic canary")


class Item:
    def __init__(self, collection: object, attrs: dict, raw: str) -> None:
        self.collection, self.attrs, self.raw = collection, attrs, raw

    def get_attributes(self):
        return dict(self.attrs)

    def get_locked(self):
        return False

    def load_secret_sync(self, cancel):
        return True

    def get_secret(self):
        return NS(get_text=lambda: self.raw)

    def delete_sync(self, cancel):
        if self.collection.mode != "delete-lies":
            self.collection.items.remove(self)
        return True


class FakeSecret:
    def __init__(self) -> None:
        self.items, self.writes, self.mode = [], [], "ok"
        self.ServiceFlags = NS(OPEN_SESSION=2)
        self.SearchFlags = NS(ALL=2)
        self.CollectionFlags = NS(LOAD_ITEMS=2)
        self.SchemaFlags = NS(NONE=0)
        self.SchemaAttributeType = NS(STRING=0)
        self.ItemCreateFlags = NS(REPLACE=1)
        self.Service = NS(get_sync=self.service)
        self.Collection = NS(for_alias_sync=lambda *args: self)
        self.Schema = NS(new=lambda *args: args)
        self.Value = NS(new=lambda raw, *_: raw)
        self.Item = NS(create_sync=self.create)

    def service(self, *args):
        if self.mode == "unavailable":
            raise RemoteError("org.freedesktop.DBus.Error.ServiceUnknown")
        return self

    def get_object_path(self):
        return "/synced/collection" if self.mode == "synced" else store.LOGIN

    def get_locked(self):
        return self.mode == "locked"

    def refresh(self):
        pass

    def search_sync(self, schema, attrs, flags, cancel):
        return [i for i in self.items if all(i.attrs.get(k) == v for k, v in attrs.items())]

    def create(self, collection, schema, attrs, label, value, flags, cancel):
        if self.mode == "denied":
            raise RemoteError("org.freedesktop.DBus.Error.AccessDenied")
        if self.mode == "write-failed":
            raise RuntimeError("secret diagnostic canary")
        self.writes.append((collection, schema, attrs, label, flags))
        attrs = attrs | {"xdg:schema": store.SCHEMA}
        self.items[:] = [i for i in self.items if i.attrs != attrs]
        item = Item(self, attrs, "{}" if self.mode == "bad-readback" else value)
        self.items.append(item)
        return item


class CustodyTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeSecret()
        gio = NS(Cancellable=lambda: NS(cancel=lambda: None),
                 DBusError=NS(get_remote_error=lambda exc: getattr(exc, "remote", None)))
        patches = patch.dict("sys.modules", {"gi": NS(require_version=lambda *_: None),
            "gi.repository": NS(Secret=self.fake, Gio=gio)})
        patches.start()
        self.addCleanup(patches.stop)
        self.custody = store.SecretStore(owner._native)
        self.record = record()

    def test_roundtrip_exact_binding_readback_and_local_absence(self):
        foreign = [Item(self.fake, {"xdg:schema": "org.other.App", "index": str(i)}, "synthetic")
                   for i in range(256)]
        self.custody.save(self.record)
        self.fake.items[0:0] = foreign
        self.assertEqual(self.fake.writes[0][0], self.fake)
        self.assertEqual(self.fake.items[-1].attrs, {
            "xdg:schema": "ai.happyranch.LinuxClient.v1",
            "home": hashlib.sha256(b"synthetic-home").hexdigest(),
            "control": hashlib.sha256(b"synthetic-control").hexdigest(),
            "node": hashlib.sha256(b"node-one").hexdigest(),
            "profile": hashlib.sha256(b"a" * 32).hexdigest()})
        self.assertEqual(set(json.loads(self.fake.items[-1].raw)), {
            "version", "bearer", "home_id", "control_plane_id", "stable_node_id", "connector_id",
            "control_url", "ca_der", "fingerprint", "dial_ip", "home_endpoint", "profile_id",
            "hostname", "network_state"})
        records, state, observation = self.custody.list()
        self.assertEqual((state, observation, len(records)), ("saved", "known", 1))
        restored = self.custody.load(next(iter(records.values())))
        self.assertEqual(store.encode(restored), store.encode(self.record))
        self.assertEqual(repr(restored), "SavedRecord(<redacted>)")
        self.custody.save(record("node-two"))
        self.custody.delete(self.record)
        self.assertEqual(len(self.fake.items), 257)
        self.assertTrue(all(i in self.fake.items for i in foreign))
        self.assertEqual(self.custody.list()[0].keys(), {store.reference(record("node-two"))})

    def test_malformed_peers_only_block_possibly_matching_targets(self):
        schema = {"xdg:schema": store.SCHEMA}
        target = store.binding(self.record)
        for attrs in (schema | {"node": "0" * 64},
                      schema | store.binding(record("other-node")) | {"extra": "malformed"}):
            with self.subTest(attrs=attrs):
                peer = Item(self.fake, attrs, "invalid-synthetic")
                self.fake.items[:] = [peer]
                self.custody.save(self.record)
                self.assertEqual(self.custody.list()[1:], ("unknown", "unavailable"))
                self.assertEqual(store.encode(self.custody.load(self.record)), store.encode(self.record))
                self.custody.delete(self.record)
                self.assertEqual(self.fake.items, [peer])
                self.assertEqual(self.custody.list(), ({}, "unknown", "unavailable"))
        for attrs, unreadable in ((schema, False), (schema | target | {"extra": "malformed"}, False),
                                  (schema | target, True)):
            with self.subTest(ambiguous=attrs, unreadable=unreadable):
                self.fake.items[:] = []
                self.custody.save(self.record)
                peer = Item(self.fake, attrs, "invalid-synthetic")
                if unreadable:
                    def unavailable():
                        raise RuntimeError("synthetic attribute failure")
                    peer.get_attributes = unavailable
                self.fake.items.insert(0, peer)
                writes = len(self.fake.writes)
                with self.assertRaisesRegex(store.StoreError, "saveFailed"):
                    self.custody.save(self.record)
                self.assertEqual(len(self.fake.writes), writes)
                with self.assertRaisesRegex(store.StoreError, "removalIncomplete"):
                    self.custody.delete(self.record)
                self.assertEqual(len(self.fake.items), 2)

    def test_closed_failures_never_select_substitute_or_claim_saved(self):
        for mode, category in (("locked", "keychainLocked"), ("unavailable", "unavailable"),
                               ("synced", "unavailable"), ("denied", "keychainDenied"),
                               ("write-failed", "saveFailed"), ("bad-readback", "saveFailed")):
            with self.subTest(mode=mode):
                self.fake.mode, self.fake.items, self.fake.writes = mode, [], []
                with self.assertRaises(store.StoreError) as caught:
                    self.custody.save(self.record)
                self.assertEqual(str(caught.exception), category)
                self.assertNotIn("canary", repr(caught.exception))
                if mode in ("locked", "unavailable", "synced", "denied", "write-failed"):
                    self.assertEqual(self.fake.writes, [])

    def test_unreadable_peer_and_cross_binding_cannot_hide_or_substitute(self):
        self.custody.save(self.record)
        item = self.fake.items[0]
        self.fake.items.insert(0, Item(self.fake, {"xdg:schema": store.SCHEMA}, "bad-secret"))
        records, state, observation = self.custody.list()
        self.assertEqual((len(records), state, observation), (1, "unknown", "unavailable"))
        self.assertEqual(store.encode(self.custody.load(self.record)), store.encode(self.record))
        item.raw = item.raw.replace("b" * 64, "c" * 64)
        with self.assertRaisesRegex(store.StoreError, "stateChanged"):
            self.custody.load(self.record)
        item.attrs["node"] = "0" * 64
        self.assertEqual(self.custody.list()[0], {})
        for raw in (item.raw[:-1] + ',"version":1}', item.raw.replace('"version":1', '"version":true'),
                    item.raw.replace('"bearer":', '"pair_secret":'), "x" * (store.LIMIT + 1)):
            with self.subTest(raw=raw[:16]), self.assertRaises(store.StoreError):
                store.decode(raw, owner._native)

    def test_remove_requires_observed_absence(self):
        self.custody.save(self.record)
        self.fake.mode = "delete-lies"
        with self.assertRaisesRegex(store.StoreError, "removalIncomplete"):
            self.custody.delete(self.record)
        self.assertEqual(len(self.fake.items), 1)
        self.fake.mode = "ok"
        peer = Item(self.fake, {"xdg:schema": store.SCHEMA, "node": "0" * 64}, "invalid-synthetic")
        self.fake.items.insert(0, peer)
        self.custody.delete(self.record)
        self.assertEqual(self.fake.items, [peer])


class RealKeyringTests(unittest.TestCase):
    def test_disposable_login_roundtrip(self):
        root = os.environ.get("HAPPYRANCH_DISPOSABLE_KEYRING", "")
        if not root:
            self.skipTest("requires an explicitly isolated dbus-run-session keyring")
        self.assertTrue(Path(root).is_absolute())
        self.assertEqual(Path(os.environ["HOME"]), Path(root) / "home")
        self.assertIn("task-tmp", Path(root).parts)
        self.assertTrue(os.environ.get("DBUS_SESSION_BUS_ADDRESS"))
        custody = store.SecretStore(owner._native)
        original = record()
        self.assertEqual(custody.list(), ({}, "none", "known"))
        custody.save(original)
        def foreign_items(secret, collection, _schema, cancel):
            foreign_schema = secret.Schema.new("org.other.App", secret.SchemaFlags.NONE,
                                               {"index": secret.SchemaAttributeType.STRING})
            for i in range(256):
                secret.Item.create_sync(collection, foreign_schema, {"index": str(i)}, "Synthetic foreign item",
                    secret.Value.new("synthetic", -1, "text/plain"), secret.ItemCreateFlags.REPLACE, cancel)
        custody._run(foreign_items)
        records, state, observation = store.SecretStore(owner._native).list()
        self.assertEqual((len(records), state, observation), (1, "saved", "known"))
        self.assertEqual(store.encode(custody.load(next(iter(records.values())))), store.encode(original))
        custody.save(original)
        custody.delete(original)
        self.assertEqual(custody.list(), ({}, "none", "known"))
        def count_foreign(secret, collection, _schema, cancel):
            return len(collection.search_sync(None, {"xdg:schema": "org.other.App"}, secret.SearchFlags.ALL, cancel))
        self.assertEqual(custody._run(count_foreign), 256)


if __name__ == "__main__":
    unittest.main(verbosity=2)
