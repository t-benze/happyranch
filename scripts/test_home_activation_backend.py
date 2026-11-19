"""E2b in the existing E1b temp-root/fake-native/AF_UNIX observation venue."""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import socket
import threading
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any, Iterator

import pytest

from runtime.remote_access import home_activation_backend as h, home_activation_owner as adapters
from runtime.remote_access.home_activation import ACTIVATION_PATH, MEMBER_PATHS, OWNED_LINK, OWNED_LINK_TARGET, HomeActivationReceipt
from runtime.remote_access.home_activation_coordinator import ActivationRefused
from runtime.remote_access.home_activation_txn import EFFECTS, TRANSACTION_PATH, Effect
from runtime.remote_access.home_admin import HeadscaleNode, HeadscaleUser
from runtime.remote_access.home_unit_gate import HomeUnitGate, HomeUnitGateError
from runtime.remote_access.installer import managed_units
from runtime.remote_access.service_manager import SystemdServiceManager
from runtime.remote_access.systemd_unit import render_headscale_unit
from scripts.test_home_activation_owner import consumed, venue as owner_venue
from scripts.test_home_live_config import venue as live_venue
from scripts.test_home_activation_txn import Venue, put
from scripts.test_home_observer import reply

SIDECAR = "/etc/systemd/system/happyranch-tsnet-sidecar.service"


@pytest.fixture
def venue(live_venue: Venue, monkeypatch: pytest.MonkeyPatch) -> Venue:
    v = live_venue
    monkeypatch.setattr(h.os, "geteuid", lambda: 0)
    v.path(ACTIVATION_PATH).unlink()
    v.path(MEMBER_PATHS[0]).write_bytes(render_headscale_unit().encode())
    v.legacy = managed_units("source")[adapters._SIDECAR].encode()
    v.home = v.legacy.replace(b"NotifyAccess=main", b"Environment=SSL_CERT_FILE=/etc/happyranch/control-ca.pem\nEnvironment=SSL_CERT_DIR=/etc/happyranch/empty-ca-directory\nNotifyAccess=main", 1)
    v.path(SIDECAR).write_bytes(v.legacy)
    v.path("/etc/happyranch/headscale").chmod(0o700)
    v.path("/run").mkdir(mode=0o755)
    v.active = False
    v.fail = None
    v.audit = []
    base_run = v.native.run

    def run(args: list[str], **kwargs: Any) -> tuple[int, str]:
        if args[0] == "ps":
            return 0, "99999 99999 99999 99999\n"
        if args[1] == "show":
            name = args[2]
            if name == adapters._HEADSCALE and not v.active:
                v.native.override.update(ActiveState="inactive", SubState="dead", MainPID="0")
            else:
                v.native.override.update(ActiveState="active", SubState="running", MainPID=str(os.getpid()))
            v.native.override.update(ControlPID="0", ControlGroup="", Job="")
        else:
            txn = v.transactions.read_optional()
            receipt_path = v.path(ACTIVATION_PATH)
            phase = v.receipts.read(expected_generation=7).phase if receipt_path.exists() else None
            v.audit.append((args[1:], phase, txn.effects[-1] if txn else None, v.path(SIDECAR).read_bytes()))
            # Independent descriptor must be excluded even between coordinator
            # contexts, including Prepare's reload and publish_ready's observer.
            assert_locked(v)
            if args[1] == "enable" and args[2] == adapters._HEADSCALE:
                link = v.path(OWNED_LINK)
                link.parent.mkdir(mode=0o755, exist_ok=True)
                link.symlink_to(OWNED_LINK_TARGET)
            if args[1] == "start" and args[2] == adapters._HEADSCALE:
                v.active = True
            if args[1:] == ["start", adapters._TARGET]:
                consumed(v)
            if args[1] == v.fail and phase == "activating":
                raise OSError("synthetic lost native reply")
        return base_run(args, **kwargs)

    io = v.receipts._io
    io.manager = SystemdServiceManager(system=True, run=run)
    io._run = run
    v.admin.nodes = (HeadscaleNode(1, HeadscaleUser(1, "happyranch-node"), "c" * 64, ("100.64.0.10",)),)
    v.admin.list_nodes = lambda **kw: v.admin.nodes
    monkeypatch.setattr(adapters.home_admin, "RootHeadscaleAdminOperations", lambda: v.admin)
    monkeypatch.setattr(adapters.VerifierClient, "resolve", lambda self: 12345)
    v.backend = h.HomeActivationBackend(io=io)
    return v


def assert_locked(v: Venue) -> None:
    fd = os.open(v.path("/var/lib/happyranch-remote-access"), os.O_RDONLY | os.O_DIRECTORY)
    try:
        with pytest.raises(BlockingIOError):
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    finally:
        os.close(fd)


@contextmanager
def observations(v: Venue) -> Iterator[list[bytes]]:
    """Existing observe-self protocol, repeated for real owner verification reads."""
    path = v.path("/run/happyranch-tsnet-sidecar/observe.sock")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(f"/proc/self/fd/{parent}/{path.name}")
    path.chmod(0o600)
    listener.listen(8)
    listener.settimeout(0.1)
    stop, requests, errors = threading.Event(), [], []
    def serve() -> None:
        while not stop.is_set():
            try:
                conn, _ = listener.accept()
            except socket.timeout:
                continue
            with conn:
                try:
                    conn.settimeout(1)
                    request = b""
                    while chunk := conn.recv(128):
                        request += chunk
                    requests.append(request)
                    assert_locked(v)
                    receipt = v.receipts.read(expected_generation=7)
                    conn.sendall(json.dumps(reply(receipt)).encode())
                except BaseException as error:
                    errors.append(error)
    thread = threading.Thread(target=serve)
    thread.start()
    try:
        yield requests
    finally:
        stop.set()
        thread.join(2)
        listener.close()
        os.close(parent)
        assert not thread.is_alive() and not errors


def gate(v: Venue) -> HomeUnitGate:
    return HomeUnitGate(root=v.root, root_owner=v.owner_ids,
        expected_release_uid=v.prepared.release_uid, expected_home_id="home",
        expected_control_plane_id="control", expected_connector_id="connector", expected_generation=7)


def test_prepare_order_and_exact_home_retry(venue: Venue) -> None:
    v = venue
    # Simulate the allowed residual from a crash after rename, before receipt.
    v.path(MEMBER_PATHS[0]).write_bytes(render_headscale_unit(home_transport=True).encode())
    with pytest.raises(HomeUnitGateError):
        gate(v).inspect()
    before = v.path(SIDECAR).read_bytes()
    generation, operation = v.backend._prepare.prepare(expected_generation=7, authenticated=True)
    receipt = v.receipts.read(expected_generation=7)
    assert (generation, operation) == (7, receipt.operation_id)
    assert receipt.phase == "prepared" and len(operation) == 32 and set(operation) <= set("0123456789abcdef")
    assert dict(receipt.member_digests) == {p: hashlib.sha256(v.path(p).read_bytes()).hexdigest() for p in MEMBER_PATHS}
    assert v.path(SIDECAR).read_bytes() == before == v.legacy
    assert v.audit == [(["daemon-reload"], None, None, v.legacy)]
    assert not v.path(OWNED_LINK).exists() and v.transactions.read_optional() is None
    gate(v).inspect()


@pytest.mark.parametrize("case", ["receipt", "txn", "receipt-pending", "txn-pending", "unit-pending",
    "wrong-G", "nonroot", "unauthenticated", "running", "link", "foreign-unit", "source",
    "member", "provision", "sidecar-unit"])
def test_prepare_refusals(venue: Venue, monkeypatch: pytest.MonkeyPatch, case: str) -> None:
    v = venue
    if case == "receipt": v.path(ACTIVATION_PATH).write_bytes(v.prepared.to_bytes())
    if case == "txn": v.path(TRANSACTION_PATH).write_bytes(b"{}")
    if case.endswith("pending"):
        path = {"receipt-pending": ACTIVATION_PATH, "txn-pending": TRANSACTION_PATH,
                "unit-pending": MEMBER_PATHS[0]}[case]
        v.path(path).with_name("." + Path(path).name + ".pending").write_bytes(b"uncertain")
    if case == "nonroot": monkeypatch.setattr(h.os, "geteuid", lambda: 1000)
    if case == "running": v.active = True
    if case == "link":
        link = v.path(OWNED_LINK)
        link.parent.mkdir(mode=0o755)
        link.symlink_to(OWNED_LINK_TARGET)
    if case == "foreign-unit": v.path(MEMBER_PATHS[0]).write_bytes(b"foreign")
    if case == "source": v.source.write_bytes(b"synthetic rotation")
    if case == "member": v.path("/etc/happyranch/headscale/policy.hujson").write_bytes(b"foreign")
    if case == "provision": v.path("/etc/happyranch/provision.json").write_bytes(b"{}")
    if case == "sidecar-unit": v.path(SIDECAR).write_bytes(b"foreign")
    before = {p: v.path(p).read_bytes() for p in (MEMBER_PATHS[0], SIDECAR)}
    if case == "sidecar-unit":
        # Prepare NEVER publishes or selects a sidecar; E1a refuses it before intent.
        v.backend._prepare.prepare(expected_generation=7, authenticated=True)
        prepared = v.receipts.read(expected_generation=7)
        coordinator, _, _ = v.backend._compose(prepared)
        with pytest.raises(ActivationRefused): coordinator.run(prepared)
        assert v.transactions.read_optional() is None
    else:
        with pytest.raises(ActivationRefused):
            v.backend._prepare.prepare(expected_generation=8 if case == "wrong-G" else 7,
                                       authenticated=case != "unauthenticated")
        if case not in ("member", "provision"):
            assert v.path(MEMBER_PATHS[0]).read_bytes() == before[MEMBER_PATHS[0]]
        else:
            # Validation after the only permitted unit mutation leaves drift.
            assert not v.path(ACTIVATION_PATH).exists()
            with pytest.raises(HomeUnitGateError): gate(v).inspect()
    assert v.path(SIDECAR).read_bytes() == before[SIDECAR]
    assert all(call[0] == ["daemon-reload"] for call in v.audit)


def test_prepare_rotation_before_rename_retains_pending(venue: Venue, monkeypatch: pytest.MonkeyPatch) -> None:
    v = venue
    original = os.fsync
    def rotate(descriptor: int) -> None:
        pending = v.path(MEMBER_PATHS[0]).with_name(".happyranch-headscale.service.pending")
        if pending.exists(): v.source.write_bytes(b"synthetic rotated source")
        original(descriptor)
    monkeypatch.setattr(os, "fsync", rotate)
    with pytest.raises(ActivationRefused):
        v.backend._prepare.prepare(expected_generation=7, authenticated=True)
    assert v.path(MEMBER_PATHS[0]).read_bytes() == render_headscale_unit().encode()
    assert v.path(MEMBER_PATHS[0]).with_name(".happyranch-headscale.service.pending").read_bytes() == render_headscale_unit(home_transport=True).encode()
    assert not v.path(ACTIVATION_PATH).exists() and not v.audit


def test_prepare_crash_residual(venue: Venue, monkeypatch: pytest.MonkeyPatch) -> None:
    v = venue
    def crash() -> None: raise OSError("synthetic reload loss")
    monkeypatch.setattr(v.receipts._io.manager, "daemon_reload", crash)
    with pytest.raises(ActivationRefused):
        v.backend._prepare.prepare(expected_generation=7, authenticated=True)
    assert v.path(MEMBER_PATHS[0]).read_bytes() == render_headscale_unit(home_transport=True).encode()
    assert v.path(SIDECAR).read_bytes() == v.legacy
    assert not v.path(ACTIVATION_PATH).exists() and not v.path(TRANSACTION_PATH).exists()
    with pytest.raises(HomeUnitGateError): gate(v).inspect()


def test_backend_enable_and_recover_real_owners(venue: Venue) -> None:
    v = venue
    with observations(v) as requests:
        ready = v.backend.enable(expected_generation=7, authenticated=True)
        assert ready.phase == "ready" and ready.local_node_stable_id == "self-stable"
        assert ready.home_endpoint == "100.64.0.10:443"
        assert v.path(SIDECAR).read_bytes() == v.home
        txn = v.transactions.read_optional()
        assert txn.phase == "joined" and tuple(e.name for e in txn.effects) == EFFECTS
        assert all(e.state == "done" for e in txn.effects)
        assert len(requests) == 4
        # First reload is Prepare (no receipt, legacy sidecar); second is
        # Headscale intent; final reload must follow managed_services intent.
        reloads = [row for row in v.audit if row[0] == ["daemon-reload"]]
        assert reloads[0][1:3] == (None, None)
        assert reloads[-1][1:3] == ("activating", Effect("managed_services", "intent", {}))
        assert reloads[-1][3] == v.home
        assert [row[0] for row in v.audit] == [["daemon-reload"], ["daemon-reload"],
            ["enable", adapters._HEADSCALE], ["start", adapters._HEADSCALE], ["daemon-reload"],
            ["enable", "happyranch-release-verifier.socket"], ["enable", adapters._TARGET], ["start", adapters._TARGET]]
        v.path(ACTIVATION_PATH).write_bytes(replace(ready, phase="failed_uncertain").to_bytes())
        v.path(TRANSACTION_PATH).write_bytes(replace(txn, phase="failed_uncertain").to_bytes())
        before = list(v.audit)
        recovered = v.backend.recover(expected_generation=7, expected_operation_id=ready.operation_id,
                                       fresh_authenticated=True)
        assert recovered == ready and v.audit == before and len(requests) == 7


@pytest.mark.parametrize("case", ["enable-auth", "enable-root", "enable-G", "recover-auth", "recover-O", "recover-G", "recover-root"])
def test_backend_admission(venue: Venue, monkeypatch: pytest.MonkeyPatch, case: str) -> None:
    v = venue
    if case.startswith("recover"):
        with observations(v):
            ready = v.backend.enable(expected_generation=7, authenticated=True)
            txn = v.transactions.read_optional()
            put(v.path(ACTIVATION_PATH), replace(ready, phase="failed_uncertain").to_bytes())
            put(v.path(TRANSACTION_PATH), replace(txn, phase="failed_uncertain").to_bytes())
            before = v.records(), list(v.audit), list(v.admin.calls)
            if case == "recover-root": monkeypatch.setattr(h.os, "geteuid", lambda: 1000)
            with pytest.raises(ActivationRefused):
                v.backend.recover(expected_generation=8 if case == "recover-G" else 7,
                    expected_operation_id="e" * 32 if case == "recover-O" else ready.operation_id,
                    fresh_authenticated=case != "recover-auth")
            assert (v.records(), v.audit, v.admin.calls) == before
    else:
        if case == "enable-root": monkeypatch.setattr(h.os, "geteuid", lambda: 1000)
        with pytest.raises(ActivationRefused):
            v.backend.enable(expected_generation=8 if case == "enable-G" else 7,
                             authenticated=case != "enable-auth")
        assert not v.audit and not v.path(TRANSACTION_PATH).exists()


@pytest.mark.parametrize("case", ["sidecar-reload", "sidecar-start", "source-after-sidecar"])
def test_post_intent_failure_retains_home_sidecar(venue: Venue, monkeypatch: pytest.MonkeyPatch, case: str) -> None:
    v = venue
    original = v.receipts._io.manager.daemon_reload
    def reload() -> None:
        txn = v.transactions.read_optional()
        if txn is not None and txn.effects[-1] == Effect("managed_services", "intent", {}):
            if case == "sidecar-reload": raise OSError("synthetic reload loss")
            if case == "source-after-sidecar": v.source.write_bytes(b"synthetic rotation")
        original()
    monkeypatch.setattr(v.receipts._io.manager, "daemon_reload", reload)
    # Only the target start should fail; Headscale start must complete first.
    if case == "sidecar-start":
        original_start = v.receipts._io.manager.start
        v.fail = None
        def start(name: str, **kw: Any) -> None:
            original_start(name, **kw)
            if name == adapters._TARGET: raise OSError("synthetic start loss")
        monkeypatch.setattr(v.receipts._io.manager, "start", start)
    with pytest.raises(ActivationRefused):
        v.backend.enable(expected_generation=7, authenticated=True)
    assert v.receipts.read(expected_generation=7).phase == "failed_uncertain"
    txn = v.transactions.read_optional()
    assert txn.phase == "failed_uncertain" and txn.effects[-1] == Effect("managed_services", "intent", {})
    assert v.path(SIDECAR).read_bytes() == v.home
    assert not any(row[0][0] in ("stop", "restart", "disable") for row in v.audit)


def test_managed_done_and_joined_require_home_sidecar(venue: Venue) -> None:
    v = venue
    with observations(v):
        ready = v.backend.enable(expected_generation=7, authenticated=True)
        coordinator, _, _ = v.backend._compose(ready)
        before = v.records(), list(v.audit)
        v.path(SIDECAR).write_bytes(v.legacy)
        assert not coordinator._owner.verify("managed_services", ready)
        assert not coordinator._owner.verify("joined", ready)
        assert (v.records(), v.audit) == before
        v.path(SIDECAR).write_bytes(v.home)
        with v.backend._transactions.locked():
            assert coordinator._owner.verify("managed_services", ready)
            assert coordinator._owner.verify("joined", ready)


def test_competing_operation_refuses_without_effect(venue: Venue) -> None:
    v = venue
    with v.transactions.locked():
        with pytest.raises(ActivationRefused):
            v.backend.enable(expected_generation=7, authenticated=True)
    assert not v.audit and not v.path(ACTIVATION_PATH).exists()


@pytest.mark.parametrize("case", ["valid", "dropin", "reload", "foreign"])
def test_legacy_sidecar_preflight_stays_exact(venue: Venue, case: str) -> None:
    v = venue
    v.backend._prepare.prepare(expected_generation=7, authenticated=True)
    prepared = v.receipts.read(expected_generation=7)
    coordinator, _, _ = v.backend._compose(prepared)
    if case == "dropin": v.native.override["DropInPaths"] = "/etc/foreign.conf"
    if case == "reload": v.native.override["NeedDaemonReload"] = "yes"
    if case == "foreign": v.path(SIDECAR).write_bytes(v.legacy + b"\n# foreign")
    assert coordinator._owner.verify("preflight", prepared) is (case == "valid")
    assert v.transactions.read_optional() is None
    assert v.path(SIDECAR).read_bytes() == (v.legacy + b"\n# foreign" if case == "foreign" else v.legacy)
    assert len(v.audit) == 1 and v.audit[0][0] == ["daemon-reload"]
