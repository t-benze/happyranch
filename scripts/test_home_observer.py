"""Standalone real AF_UNIX/temp-root B2 and joined-to-ready boundary checks."""
from __future__ import annotations

import json
import os
import socket
import threading
from contextlib import contextmanager
from dataclasses import replace
from types import SimpleNamespace

import pytest

from runtime.remote_access import home_observer as h
from runtime.remote_access.home_activation import ACTIVATION_PATH
from runtime.remote_access.home_activation_coordinator import ActivationRefused
from runtime.remote_access.home_activation_txn import TRANSACTION_PATH
from runtime.remote_access.home_admin import HeadscaleNode, HeadscaleUser
from scripts.test_home_activation_txn import Venue


class NoObservation:
    def observe(self, *a, **kw):
        pytest.fail("inadmissible pair reached B2")


class Owner:
    def __init__(self) -> None:
        self.process = h.SidecarProcess(os.getuid(), os.getpid())

    def sidecar_process(self, receipt):
        return self.process


class Admin:
    def __init__(self) -> None:
        self.nodes = (HeadscaleNode(1, HeadscaleUser(1, "happyranch-node"), "c" * 64, ("100.64.0.10",)),)
        self.calls = []

    def list_nodes(self, **kwargs):
        self.calls.append(kwargs)
        return self.nodes


@pytest.fixture
def venue(tmp_path, monkeypatch):
    return Venue(tmp_path, monkeypatch)


def reply(receipt) -> dict:
    return dict(format="observe-self@1", stable_id="self-stable", tailnet_addresses=["100.64.0.10"],
                control_url=h.CONTROL_URL, node_key_sha256="c" * 64,
                binding={k: getattr(receipt, k) for k in h.BINDING})


@contextmanager
def peer(venue, data):
    path = venue.root / h.SOCKET_PATH.lstrip("/")
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(f"/proc/self/fd/{parent}/{path.name}")
    path.chmod(0o600)
    listener.listen(1)
    listener.settimeout(0.3)
    stop = threading.Event()
    requests = []
    def serve():
        try:
            conn, _ = listener.accept()
        except OSError:
            return
        with conn:
            conn.settimeout(1)
            try:
                request = b""
                while chunk := conn.recv(128):
                    request += chunk
                requests.append(request)
                if data is None:
                    stop.wait(6)
                else:
                    conn.sendall(data)
            except OSError:
                pass
    thread = threading.Thread(target=serve)
    thread.start()
    try:
        yield path, requests
    finally:
        stop.set()
        listener.close()
        thread.join(2)
        os.close(parent)
        assert not thread.is_alive()


def observer(venue, owner=None, admin=None):
    return h.HomeObserver(owner=owner or Owner(), admin=admin or Admin(), root=venue.root)


def test_observe_and_verify_ready(venue):
    joined = venue.coordinator().run(venue.prepared)
    txn = venue.path(TRANSACTION_PATH).read_bytes()
    before = list(venue.owner.calls), list(venue.mints)
    for addresses in (["100.64.0.10"], ["100.64.0.10/32"]):
        record = reply(joined)
        record["tailnet_addresses"] = addresses
        with peer(venue, json.dumps(record).encode()) as (path, requests):
            admin = Admin()
            fresh = venue.coordinator()
            if addresses[0].endswith("/32"):
                result = fresh.verify_ready(expected_generation=7, observer=observer(venue, admin=admin))
            else:
                result = fresh.publish_ready(expected_generation=7, observer=observer(venue, admin=admin))
            assert result.phase == "ready" and result.local_node_stable_id == "self-stable"
            assert result.home_endpoint == "100.64.0.10:443"
            assert requests == [b"observe-self@1\n"]
            assert admin.calls[0]["user"] == "happyranch-node" and 0 < admin.calls[0]["timeout_s"] <= 5
        path.unlink()
    assert venue.path(TRANSACTION_PATH).read_bytes() == txn
    assert (venue.owner.calls, venue.mints) == before


@pytest.mark.parametrize("case", ["zero", "multiple", "ipv6", "outside", "prefix", "netmask", "hostname",
    "malformed", "extra", "missing", "duplicate", "oversize", "format", "stable", "digest", "control",
    "home_id", "control_plane_id", "connector_id", "activation_generation", "binding-extra", "binding-missing",
    "binding-duplicate", "binding-bool", "nodes-zero", "nodes-duplicate", "nodes-digest", "nodes-ip", "nodes-failure",
    "peer-pid", "owner-changed", "expected-generation", "engine-failure", "listener-failure", "source"])
def test_observation_refusal_preserves_joined(venue, case):
    joined = venue.coordinator().run(venue.prepared)
    record, owner, admin = reply(joined), Owner(), Admin()
    addresses = {"zero": [], "multiple": ["100.64.0.10", "100.64.0.11"], "ipv6": ["fd00::1"],
                 "outside": ["192.168.1.10"], "prefix": ["100.64.0.10/24"],
                 "netmask": ["100.64.0.10/255.255.255.255"], "hostname": ["home.invalid"]}
    if case in addresses: record["tailnet_addresses"] = addresses[case]
    if case in h.BINDING: record["binding"][case] = 8 if case == "activation_generation" else "wrong"
    if case == "binding-extra": record["binding"]["extra"] = 1
    if case == "binding-missing": record["binding"].pop("home_id")
    if case == "binding-bool": record["binding"]["activation_generation"] = True
    if case == "extra": record["extra"] = "synthetic"
    if case == "missing": record.pop("stable_id")
    if case == "format": record["format"] = "wrong"
    if case == "stable": record["stable_id"] = "bad\x00"
    if case == "digest": record["node_key_sha256"] = "C" * 64
    if case == "control": record["control_url"] += "/"
    if case == "nodes-zero": admin.nodes = ()
    if case == "nodes-duplicate": admin.nodes *= 2
    if case == "nodes-digest": admin.nodes = (replace(admin.nodes[0], node_key_sha256="d" * 64),)
    if case == "nodes-ip": admin.nodes = (replace(admin.nodes[0], ip_addresses=("100.64.0.11",)),)
    if case == "nodes-failure": admin.list_nodes = lambda **kw: (_ for _ in ()).throw(OSError("synthetic"))
    if case == "peer-pid": owner.process = replace(owner.process, main_pid=os.getpid() + 1)
    if case == "owner-changed":
        calls = iter((owner.process, replace(owner.process, main_pid=os.getpid() + 1)))
        owner.sidecar_process = lambda receipt: next(calls)
    if case == "source": venue.source.write_bytes(b"rotated-synthetic-token")
    data = json.dumps(record).encode()
    if case == "malformed": data = b"{"
    if case == "duplicate": data = b'{"stable_id":"duplicate",' + data[1:]
    if case == "binding-duplicate": data = data.replace(b'"home_id":', b'"home_id":"duplicate","home_id":')
    if case == "oversize": data += b" " * 4097
    if case == "engine-failure": data = b""
    before = venue.records()
    with peer(venue, data) as (path, _):
        if case == "listener-failure": path.unlink()
        with pytest.raises(ActivationRefused):
            venue.coordinator().publish_ready(expected_generation=8 if case == "expected-generation" else 7,
                                               observer=observer(venue, owner, admin))
    assert venue.records() == before


@pytest.mark.parametrize("case", ["socket-symlink", "socket-file", "socket-mode", "socket-owner", "nlink",
                                 "parent-symlink", "parent-file", "parent-mode", "parent-owner", "peer-uid"])
def test_socket_admission(venue, monkeypatch, case):
    owner, admin = Owner(), Admin()
    with peer(venue, json.dumps(reply(venue.prepared)).encode()) as (path, _):
        if case in ("socket-symlink", "socket-file"):
            path.unlink()
            if case == "socket-symlink": path.symlink_to("missing")
            else: path.write_bytes(b"synthetic")
        if case == "socket-mode": path.chmod(0o660)
        if case == "nlink": os.link(path, path.with_name("link"))
        if case in ("parent-symlink", "parent-file"):
            actual = path.parent.with_name("actual")
            path.parent.rename(actual)
            if case == "parent-symlink": path.parent.symlink_to(actual)
            else: path.parent.write_bytes(b"synthetic")
        if case == "parent-mode": path.parent.chmod(0o770)
        if case in ("socket-owner", "parent-owner"):
            name = "stat" if case == "socket-owner" else "fstat"
            original = getattr(h.os, name)
            def wrong_owner(*args, **kw):
                st = original(*args, **kw)
                return SimpleNamespace(**{k: getattr(st, k) for k in dir(st) if k.startswith("st_") and k != "st_uid"}, st_uid=os.getuid() + 1)
            monkeypatch.setattr(h.os, name, wrong_owner)
        if case == "peer-uid":
            original = socket.socket
            class WrongPeer(original):
                def getsockopt(self, *args):
                    raw = super().getsockopt(*args)
                    if args[1] == socket.SO_PEERCRED:
                        pid, uid, gid = h.struct.unpack("3i", raw)
                        return h.struct.pack("3i", pid, uid + 1, gid)
                    return raw
            monkeypatch.setattr(socket, "socket", WrongPeer)
        with pytest.raises(h.ObservationRefused): observer(venue, owner, admin).observe(venue.prepared, expected_generation=7)
        assert not admin.calls


def test_deadline(venue):
    with peer(venue, None):
        start = h.time.monotonic()
        with pytest.raises(h.ObservationRefused): observer(venue).observe(venue.prepared, expected_generation=7)
        assert 4 <= h.time.monotonic() - start < 6


@pytest.mark.parametrize("case", ["stable", "endpoint", "binding"])
def test_ready_verifier_is_read_only(venue, case):
    joined = venue.coordinator().run(venue.prepared)
    with peer(venue, json.dumps(reply(joined)).encode()) as (path, _):
        ready = venue.coordinator().publish_ready(expected_generation=7, observer=observer(venue))
    path.unlink()
    record = reply(ready)
    if case == "stable": record["stable_id"] = "other"
    if case == "endpoint": record["tailnet_addresses"] = ["100.64.0.11"]
    if case == "binding": record["binding"]["home_id"] = "other"
    admin = Admin()
    admin.nodes = (replace(admin.nodes[0], ip_addresses=("100.64.0.10", "100.64.0.11")),)
    before = venue.records()
    with peer(venue, json.dumps(record).encode()):
        with pytest.raises(ActivationRefused): venue.coordinator().verify_ready(expected_generation=7, observer=observer(venue, admin=admin))
    assert venue.records() == before


@pytest.mark.parametrize("txn_phase,prefix,receipt_phase", [
    (phase, count, receipt) for phase in ("activating", "joined", "failed_uncertain")
    for count in range(1, 9) for receipt in ("prepared", "activating", "joined", "ready", "failed_uncertain")
    if phase != "joined" or count == 8])
def test_ready_only_completed_pair(venue, txn_phase, prefix, receipt_phase):
    # Use the existing record parsers on real files; valid non-admitted pairs only.
    venue.coordinator().run(venue.prepared)
    transaction = venue.transactions.read_optional()
    effects = transaction.effects[:prefix]
    txn = replace(transaction, phase=txn_phase, effects=effects)
    receipt = replace(venue.prepared, phase=receipt_phase,
                      local_node_stable_id="old" if receipt_phase == "ready" else None,
                      home_endpoint="100.64.0.10:443" if receipt_phase == "ready" else None)
    if txn_phase == "joined" and receipt_phase == "joined":
        txn = replace(txn, operation_id="d" * 32)
    venue.path(TRANSACTION_PATH).write_bytes(txn.to_bytes())
    venue.path(ACTIVATION_PATH).write_bytes(receipt.to_bytes())
    before = venue.records()
    with pytest.raises(ActivationRefused): venue.coordinator().publish_ready(expected_generation=7, observer=NoObservation())
    assert venue.records() == before


@pytest.mark.parametrize('case', ['absent', 'intent', 'observed', 'binding', 'member'])
def test_other_pairs_never_observe(venue, case):
    joined = venue.coordinator().run(venue.prepared)
    txn = venue.transactions.read_optional()
    if case == 'absent': venue.path(TRANSACTION_PATH).unlink()
    if case == 'intent':
        txn = replace(txn, phase='activating', effects=(*txn.effects[:-1], replace(txn.effects[-1], state='intent')))
        venue.path(TRANSACTION_PATH).write_bytes(txn.to_bytes())
    if case == 'observed':
        joined = replace(joined, local_node_stable_id='old', home_endpoint='100.64.0.10:443')
        venue.path(ACTIVATION_PATH).write_bytes(joined.to_bytes())
    if case == 'binding':
        venue.path(TRANSACTION_PATH).write_bytes(replace(txn, home_id='other').to_bytes())
    if case == 'member': venue.path('/etc/happyranch/sidecar.json').write_bytes(b'changed')
    before = venue.records()
    with pytest.raises(ActivationRefused): venue.coordinator().publish_ready(expected_generation=7, observer=NoObservation())
    assert venue.records() == before
