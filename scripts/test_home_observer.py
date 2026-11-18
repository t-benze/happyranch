"""Standalone real AF_UNIX/temp-root B2 tests; synthetic identity, no service action.

Authoring answers (named tables include all their parameter cases):
* test_observe / test_refusal: observe actual bounded socket bytes, peer custody,
  binding, endpoint and independent B3 inventory. Regression: drop the named
  guard, accept a second matching node or use the first address. Existing health/
  admin tests never cross observe.sock; fixture responses do not validate anything.
* test_preconnect: unsafe filesystem entries must receive zero connections.
  Regression: follow a link or omit the owner/mode/nlink gate. Installer tests own
  different paths; UID-only observations are synthetic where chown needs root.
* test_ready / test_pairs / test_ready_failure: actual receipt bytes change only
  for the completed pair, with no activation effect; verifier writes nothing.
  Regression: admit an unfinished pair, skip source validation or call _fail.
  B4 tests stop at joined; these reuse their real store/activation venue.
No test-only production export/flag/seam; owner/admin injection is the brief's
unmounted composition. Socket peer UID mismatch overrides only getsockopt; socket
creation/connect/read/write remain real. Native distinct-UID execution is NOT RUN.
"""
from __future__ import annotations

import json
import os
import socket
import struct
import tempfile
import threading
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from runtime.remote_access import home_observer as h
from runtime.remote_access.home_activation import ACTIVATION_PATH
from runtime.remote_access.home_activation_coordinator import ActivationRefused
from runtime.remote_access.home_activation_txn import TRANSACTION_PATH
from scripts import test_home_activation_txn as tx

BINDING = ("home_id", "control_plane_id", "connector_id", "activation_generation")


class Peer:
    def __init__(self, root: Path, receipt) -> None:
        self.path = root / "run/happyranch-tsnet-sidecar/observe.sock"
        self.path.parent.mkdir(parents=True, mode=0o700)
        self.server = socket.socket(socket.AF_UNIX)
        self.server.bind(str(self.path))
        self.path.chmod(0o600)
        self.server.listen(4)
        self.server.settimeout(0.05)
        self.owner = SimpleNamespace(current_sidecar=lambda: (os.getuid(), os.getpid()))
        self.value = dict(format="observe-self@1", stable_id="node-7", tailnet_addresses=["100.64.0.10"],
                          control_url="https://control.happyranch.invalid:8443", node_key_sha256="a" * 64,
                          binding={key: getattr(receipt, key) for key in BINDING})
        self.nodes = (h.home_admin.HeadscaleNode(7, h.home_admin.HeadscaleUser(1, "happyranch-node"),
                                               "a" * 64, ("100.64.0.10",)),)
        self.admin_calls, self.calls, self.requests = [], 0, []
        self.raw, self.delay, self.closed = None, 0, threading.Event()
        self.observer = h.HomeObserver(owner=self.owner, admin=self, root=root)
        self.thread = threading.Thread(target=self.serve)
        self.thread.start()

    def list_nodes(self, **kwargs):
        self.admin_calls.append(kwargs)
        return self.nodes

    def serve(self) -> None:
        while not self.closed.is_set():
            try:
                conn, _ = self.server.accept()
            except TimeoutError:
                continue
            with conn:
                self.calls += 1
                conn.settimeout(0.2)
                try:
                    request = bytearray()
                    while chunk := conn.recv(100): request.extend(chunk)
                    self.requests.append(bytes(request))
                    self.closed.wait(self.delay)
                    conn.sendall(json.dumps(self.value).encode() if self.raw is None else self.raw)
                except (OSError, TimeoutError):
                    pass

    def close(self) -> None:
        self.closed.set()
        self.thread.join(timeout=1)
        assert not self.thread.is_alive()
        self.server.close()


@pytest.fixture
def scene(monkeypatch, tmp_path):
    with tempfile.TemporaryDirectory(prefix="g2d-", dir="/tmp") as directory:
        venue = tx.Venue(tmp_path, monkeypatch)
        peer = Peer(Path(directory), venue.prepared)
        try: yield venue, peer
        finally: peer.close()


@pytest.mark.parametrize("address", ["100.64.0.10", "100.64.0.10/32"])
def test_observe(scene, address) -> None:
    venue, peer = scene
    peer.value["tailnet_addresses"] = [address]
    got = peer.observer.observe(venue.prepared, expected_generation=7)
    assert (got.stable_id, got.home_endpoint) == ("node-7", "100.64.0.10:443")
    assert peer.requests == [b"observe-self@1\n"]
    assert peer.admin_calls[0]["user"] == "happyranch-node" and 0 < peer.admin_calls[0]["timeout_s"] <= 5


@pytest.mark.parametrize("case", ["pid", "peer-uid", "owner-change", "timeout", "oversize", "json", "utf8",
    "extra", "missing", "duplicate", "binding-extra", "binding-duplicate", *BINDING, "control_url",
    "zero", "multiple", "ipv6", "outside", "prefix", "bad-ip", "stable", "digest", "node-zero",
    "node-duplicate", "node-digest", "node-ip", "node-user", "generation", "engine", "listener"])
def test_refusal(scene, monkeypatch, case) -> None:
    venue, peer = scene
    receipt = venue.prepared
    if case == "pid": peer.owner.current_sidecar = lambda: (os.getuid(), os.getpid() + 1)
    elif case == "peer-uid":
        class WrongUID(socket.socket):
            def getsockopt(self, *args):
                data = super().getsockopt(*args)
                pid, uid, gid = struct.unpack("3i", data)
                return struct.pack("3i", pid, uid + 1, gid)
        monkeypatch.setattr(h.socket, "socket", WrongUID)
    elif case == "owner-change":
        calls = []
        peer.owner.current_sidecar = lambda: (calls.append(1) or os.getuid(), os.getpid() + (len(calls) > 1))
    elif case == "timeout":
        peer.delay = 5.2
    elif case == "oversize": peer.raw = b"x" * 4097
    elif case == "json": peer.raw = b"{"
    elif case == "utf8": peer.raw = b"\xff"
    elif case == "extra": peer.value["extra"] = True
    elif case == "missing": del peer.value["stable_id"]
    elif case == "duplicate": peer.raw = json.dumps(peer.value).encode()[:-1] + b',"format":"observe-self@1"}'
    elif case == "binding-extra": peer.value["binding"]["extra"] = 1
    elif case == "binding-duplicate":
        peer.raw = json.dumps(peer.value).encode().replace(b'"home_id": "home"', b'"home_id":"home","home_id":"home"')
    elif case in BINDING: peer.value["binding"][case] = 8 if case == "activation_generation" else "wrong"
    elif case == "control_url": peer.value[case] += "/"
    elif case in ("zero", "multiple", "ipv6", "outside", "prefix", "bad-ip"):
        peer.value["tailnet_addresses"] = {"zero": [], "multiple": ["100.64.0.10"] * 2,
            "ipv6": ["fd7a::1"], "outside": ["192.168.1.1"], "prefix": ["100.64.0.10/24"],
            "bad-ip": ["100.064.0.10"]}[case]
    elif case == "stable": receipt = replace(receipt, phase="ready", local_node_stable_id="other", home_endpoint="100.64.0.10:443")
    elif case == "digest": peer.value["node_key_sha256"] = "A" * 64
    elif case == "node-zero": peer.nodes = ()
    elif case == "node-duplicate": peer.nodes *= 2
    elif case == "node-digest": peer.nodes = (replace(peer.nodes[0], node_key_sha256="b" * 64),)
    elif case == "node-ip": peer.nodes = (replace(peer.nodes[0], ip_addresses=("100.64.0.11",)),)
    elif case == "node-user": peer.nodes = (replace(peer.nodes[0], user=h.home_admin.HeadscaleUser(1, "happyranch-invite")),)
    elif case == "engine": peer.raw = b""  # no successful LocalAPI response
    elif case == "listener": peer.path.unlink()
    before = venue.records()
    with pytest.raises(h.ObservationRefused, match="^observation_refused$"):
        peer.observer.observe(receipt, expected_generation=8 if case == "generation" else 7)
    assert venue.records() == before


@pytest.mark.parametrize("case", ["symlink", "parent-link", "ancestor-link", "regular", "mode", "nlink",
                                 "parent-mode", "parent-file", "socket-owner", "parent-owner"])
def test_preconnect(scene, monkeypatch, case) -> None:
    venue, peer = scene
    path = peer.path
    if case == "symlink": path.rename(path.with_suffix(".old")); path.symlink_to(path.with_suffix(".old"))
    elif case == "parent-link": path.parent.rename(path.parent.with_suffix(".old")); path.parent.symlink_to(path.parent.with_suffix(".old"))
    elif case == "ancestor-link":
        run = path.parent.parent
        run.rename(run.with_name("other-run")); run.symlink_to(run.with_name("other-run"))
    elif case == "regular": path.unlink(); path.write_bytes(b"foreign")
    elif case == "mode": path.chmod(0o640)
    elif case == "nlink": os.link(path, path.with_suffix(".link"))
    elif case == "parent-mode": path.parent.chmod(0o777)
    elif case == "parent-file": path.unlink(); path.parent.rmdir(); path.parent.write_bytes(b"foreign")
    else:
        original = os.stat if case == "socket-owner" else os.fstat
        def foreign(*args, **kwargs):
            st = original(*args, **kwargs)
            values = list(st); values[4] += 1
            return os.stat_result(values)
        monkeypatch.setattr(h.os, "stat" if case == "socket-owner" else "fstat", foreign)
    with pytest.raises(h.ObservationRefused): peer.observer.observe(venue.prepared, expected_generation=7)
    assert peer.calls == 0 and peer.admin_calls == []


@pytest.mark.parametrize("fresh", [False, True])
def test_ready(scene, fresh) -> None:
    venue, peer = scene
    coordinator = venue.coordinator()
    joined = coordinator.run(venue.prepared)
    effects, mints, txn = list(venue.owner.calls), list(venue.mints), venue.records()[1]
    if fresh: coordinator = venue.coordinator()
    ready = coordinator.publish_ready(observer=peer.observer, expected=joined, expected_generation=7)
    assert ready.phase == "ready" and ready.local_node_stable_id == "node-7" and ready.home_endpoint == "100.64.0.10:443"
    assert venue.receipts.read(expected_generation=7) == ready and venue.records()[1] == txn
    before = venue.records()
    assert venue.coordinator().verify_ready(observer=peer.observer, expected=ready, expected_generation=7) == ready
    peer.value["tailnet_addresses"] = ["100.64.0.11"]
    peer.nodes = (replace(peer.nodes[0], ip_addresses=("100.64.0.11",)),)
    with pytest.raises(ActivationRefused): coordinator.verify_ready(observer=peer.observer, expected=ready, expected_generation=7)
    assert venue.records() == before and venue.owner.calls == effects and venue.mints == mints


@pytest.mark.parametrize("phase", ["prepared", "activating", "joined", "ready", "failed_uncertain"])
@pytest.mark.parametrize("txn_case", ["absent", "prefix", "done", "joined", "terminal", "binding"])
def test_pairs(scene, phase, txn_case) -> None:
    venue, peer = scene
    joined = venue.coordinator().run(venue.prepared)
    txn = venue.transactions.read_optional()
    receipt = replace(joined, phase=phase, local_node_stable_id="node-7" if phase == "ready" else None,
                      home_endpoint="100.64.0.10:443" if phase == "ready" else None)
    tx.put(venue.path(ACTIVATION_PATH), receipt.to_bytes())
    if txn_case == "absent": venue.path(TRANSACTION_PATH).unlink()
    else:
        if txn_case == "prefix": txn = replace(txn, phase="activating", effects=txn.effects[:3])
        elif txn_case == "done": txn = replace(txn, phase="activating")
        elif txn_case == "terminal": txn = replace(txn, phase="failed_uncertain")
        elif txn_case == "binding": txn = replace(txn, connector_id="other")
        tx.put(venue.path(TRANSACTION_PATH), txn.to_bytes())
    before, effects = venue.records(), list(venue.owner.calls)
    if phase == "joined" and txn_case == "joined":
        assert venue.coordinator().publish_ready(observer=peer.observer, expected=receipt, expected_generation=7).phase == "ready"
    else:
        with pytest.raises(ActivationRefused): venue.coordinator().publish_ready(observer=peer.observer, expected=receipt, expected_generation=7)
        assert venue.records() == before and peer.calls == 0
    assert venue.owner.calls == effects and len(venue.mints) == 1


@pytest.mark.parametrize("case", ["engine", "listener", "source-before", "source-after", "config", "generation", "stable"])
def test_ready_failure(scene, case) -> None:
    venue, peer = scene
    joined = venue.coordinator().run(venue.prepared)
    if case == "engine": peer.raw = b""
    elif case == "listener": peer.path.unlink()
    elif case == "source-before": venue.source.write_bytes(b"rotated")
    elif case == "source-after":
        original = peer.list_nodes
        def rotated(**kwargs): venue.source.write_bytes(b"rotated"); return original(**kwargs)
        peer.list_nodes = rotated
    elif case == "config": venue.path(tx.CONFIGS[0]).write_bytes(b"changed")
    elif case == "stable":
        joined = replace(joined, local_node_stable_id="other", home_endpoint="100.64.0.10:443")
        tx.put(venue.path(ACTIVATION_PATH), joined.to_bytes())
    before, effects = venue.records(), list(venue.owner.calls)
    with pytest.raises(ActivationRefused):
        venue.coordinator().publish_ready(observer=peer.observer, expected=joined, expected_generation=8 if case == "generation" else 7)
    assert venue.records() == before and venue.owner.calls == effects and len(venue.mints) == 1
