"""Standalone B7 fake-owner/admin, real A5 files and AF_UNIX B2 checks.

Run: uv run --frozen pytest scripts/test_home_activation_recovery.py -q
Four authoring answers and attributable mutation/restoration results are in the PR.
No production test seam; reuse the existing txn/observer temp-root venue.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from runtime.remote_access import home_admin
from runtime.remote_access.home_activation import (
    ACTIVATION_PATH, HomeActivationError, HomeActivationReceipt, validate_transition,
)
from runtime.remote_access.home_activation_coordinator import ActivationRefused
from runtime.remote_access.home_activation_recovery import (
    HomeActivationRecovery, RecoveryRefused, validate_recovery_transaction,
)
from runtime.remote_access.home_activation_txn import (
    EFFECTS, TRANSACTION_PATH, Effect, HomeActivationTransaction, validate_transaction_transition,
)
from runtime.remote_access.home_minter import AdminRefused, PreAuthKey
from scripts.test_home_activation_txn import Venue, put
from scripts.test_home_observer import Admin as NodeAdmin, Owner as ObservationOwner, observer, peer, reply


class Admin(NodeAdmin):
    def __init__(self) -> None:
        super().__init__()
        now = datetime.now(timezone.utc)
        self.keys = (PreAuthKey("happyranch-node", "1", "e" * 48, False, False, True,
                                now + timedelta(seconds=120), now, ()),)
        self.error = None
        self.node_reads = 0

    def list_pre_auth_keys(self, **kwargs: Any) -> tuple[PreAuthKey, ...]:
        self.calls.append(kwargs)
        if self.error == "keys":
            raise RuntimeError("synthetic-private-canary")
        return self.keys

    def list_nodes(self, **kwargs: Any) -> tuple[home_admin.HeadscaleNode, ...]:
        self.node_reads += 1
        if self.error == "nodes":
            raise RuntimeError("synthetic-private-canary")
        return super().list_nodes(**kwargs)


class RecoveryVenue(Venue):
    def __init__(self, tmp: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        super().__init__(tmp, monkeypatch)
        joined = self.coordinator().run(self.prepared)
        self.coordinator()._fail(joined)
        self.failed = self.receipts.read(expected_generation=7)
        self.admin = Admin()
        base = self.root / "var/lib/happyranch-remote-access/home-minter"
        base.mkdir(mode=0o700)
        (base / "happyranch-node").mkdir(mode=0o700)
        self.reservation = base / "happyranch-node" / ("operation-" + hashlib.sha256(self.failed.operation_id.encode()).hexdigest())
        now = self.admin.keys[0].created_at
        self.window = (now - timedelta(seconds=1), now + timedelta(seconds=120))
        put(self.reservation, ("\n".join(t.isoformat() for t in self.window) + "\n").encode())
        self.owner.calls.clear()
        self.owner.verifications.clear()
        self.mints.clear()
        self.port_checks.clear()

    def recovery(self) -> HomeActivationRecovery:
        return HomeActivationRecovery(owner=self.owner, admin=self.admin, root=self.root, root_owner=self.owner_ids)

    def recover(self, **kwargs: Any) -> HomeActivationReceipt:
        args = dict(expected_generation=7, expected_operation_id=self.failed.operation_id,
                    fresh_authenticated=True, observation_owner=ObservationOwner())
        args.update(kwargs)
        return self.recovery().recover(**args)

    def empty(self) -> None:
        self.admin.keys, self.admin.nodes = (), ()

    def snapshot(self) -> dict[str, bytes]:
        parent = self.path(ACTIVATION_PATH).parent
        return {p.name: p.read_bytes() for p in parent.iterdir() if p.is_file()}

    def no_effects(self) -> None:
        assert self.owner.calls == [] and self.mints == [] and self.port_checks == []
        assert all(c["user"] == "happyranch-node" and 0 < c["timeout_s"] <= 5 for c in self.admin.calls)


@pytest.fixture
def venue(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> RecoveryVenue:
    return RecoveryVenue(tmp_path, monkeypatch)


def half_publish(venue: RecoveryVenue, monkeypatch: pytest.MonkeyPatch) -> None:
    original = venue.receipts.__class__._publish_record
    def crash(store: Any, path: Any, *args: Any, **kwargs: Any) -> Any:
        if path == ACTIVATION_PATH:
            raise Crash()
        return original(store, path, *args, **kwargs)
    with monkeypatch.context() as patch, peer(venue, json.dumps(reply(venue.failed)).encode()) as (path, _):
        patch.setattr(venue.receipts.__class__, "_publish_record", crash)
        with pytest.raises(Crash):
            venue.recover()
    path.unlink()
    assert venue.receipts.read(expected_generation=7) == venue.failed
    txn = venue.transactions.read_optional()
    assert txn.phase == "joined" and txn.recovery_observation == {
        "local_node_stable_id": "self-stable", "home_endpoint": "100.64.0.10:443"}
    venue.owner.verifications.clear()
    venue.admin.calls.clear()
    venue.admin.node_reads = 0


@pytest.mark.parametrize("half", [False, True])
def test_resume_and_existing_ready_verifier(venue: RecoveryVenue, monkeypatch: pytest.MonkeyPatch, half: bool) -> None:
    if half:
        half_publish(venue, monkeypatch)
    else:
        # Lost done-records do not replace independent verification of all effects.
        txn = venue.transactions.read_optional()
        put(venue.path(TRANSACTION_PATH), replace(txn, effects=(Effect("preflight", "intent", {}),)).to_bytes())
    before = venue.snapshot()
    with peer(venue, json.dumps(reply(venue.failed)).encode()) as (path, requests):
        ready = venue.recover()
        assert requests == [b"observe-self@1\n"]
    path.unlink()
    assert ready.phase == "ready" and ready.activation_generation == 7
    assert ready.operation_id == venue.failed.operation_id
    assert (ready.local_node_stable_id, ready.home_endpoint) == ("self-stable", "100.64.0.10:443")
    assert venue.owner.verifications == list(EFFECTS)
    assert venue.admin.node_reads == 2 and len(venue.admin.calls) == 3
    txn = venue.transactions.read_optional()
    assert txn.phase == "joined" and tuple(e.name for e in txn.effects) == EFFECTS
    assert all(e.state == "done" for e in txn.effects)
    assert HomeActivationTransaction.parse(txn.to_bytes()) == txn
    if half:
        assert venue.snapshot()["home-activation-txn.json"] == before["home-activation-txn.json"]
    saved = venue.snapshot()
    with peer(venue, json.dumps(reply(ready)).encode()) as (path, _):
        assert venue.coordinator().verify_ready(expected_generation=7, observer=observer(venue)) == ready
    assert venue.snapshot() == saved
    path.unlink()
    for fresh in (False, True):
        with pytest.raises(RecoveryRefused):
            venue.recover(fresh_authenticated=fresh)
        assert venue.snapshot() == saved
    venue.no_effects()
    assert b"e" * 48 not in b"".join(venue.snapshot().values())


FAILURES = [*EFFECTS, "auth", "source", "release", "generation", "operation", "keys-error", "nodes-error",
            "keys-limit", "nodes-limit", "keys-list", "duplicate-key", "multiple-keys", "multiple-nodes",
            "zero-keys", "zero-nodes", "unused", "reusable", "ephemeral", "tags", "outside-window",
            "missing-reservation", "prepared-member", "live-member", "b2-digest", "b2-address",
            "b2-pid", "b2-binding", "b2-control", "b2-stable", "b2-endpoint", "b3-changed"]


@pytest.mark.parametrize("half", [False, True])
@pytest.mark.parametrize("case", FAILURES)
def test_each_resume_failure_is_unchanged(venue: RecoveryVenue, monkeypatch: pytest.MonkeyPatch, half: bool, case: str) -> None:
    if half:
        half_publish(venue, monkeypatch)
    kwargs, record = {}, reply(venue.failed)
    if case in EFFECTS:
        original = venue.owner.verify
        monkeypatch.setattr(venue.owner, "verify", lambda effect, receipt: False if effect == case else original(effect, receipt))
    if case == "auth": kwargs["fresh_authenticated"] = False
    if case == "source": put(venue.source, b"synthetic-rotated")
    if case == "release":
        path = venue.path("/etc/happyranch/release-daemon.json")
        data = json.loads(path.read_bytes()); data["uid"] += 1
        put(path, json.dumps(data).encode(), 0o644)
    if case == "generation": kwargs["expected_generation"] = 8
    if case == "operation": kwargs["expected_operation_id"] = "a" * 32
    if case in ("keys-error", "nodes-error"): venue.admin.error = case.split("-")[0]
    key = venue.admin.keys[0]
    if case == "keys-limit": venue.admin.keys = (key,) * 600001
    if case == "nodes-limit": venue.admin.nodes *= 513
    if case == "keys-list": venue.admin.keys = list(venue.admin.keys)
    if case == "duplicate-key": venue.admin.keys *= 2
    if case == "multiple-keys": venue.admin.keys += (replace(key, id="2"),)
    if case == "multiple-nodes": venue.admin.nodes += (replace(venue.admin.nodes[0], id=2),)
    # Keep a usable key when removing the sole node, so this is not rollover.
    if case == "zero-keys": venue.admin.keys = ()
    if case == "zero-nodes":
        venue.admin.nodes = (); venue.admin.keys = (replace(key, used=False),)
    if case in ("unused", "reusable", "ephemeral", "tags", "outside-window"):
        fields = {"unused": dict(used=False), "reusable": dict(reusable=True), "ephemeral": dict(ephemeral=True),
                  "tags": dict(acl_tags=("tag:foreign",)), "outside-window": dict(created_at=venue.window[0] - timedelta(seconds=1))}
        venue.admin.keys = (replace(key, **fields[case]),)
    if case == "missing-reservation": venue.reservation.unlink()
    if case == "prepared-member": put(venue.path(next(iter(venue.failed.member_digests))), b"drift")
    if case == "live-member": put(venue.path("/etc/happyranch/connector.json"), b"drift")
    if case == "b2-digest": record["node_key_sha256"] = "d" * 64
    if case == "b2-address": record["tailnet_addresses"] = ["192.168.1.40"]
    if case == "b2-pid":
        owner = ObservationOwner(); owner.process = replace(owner.process, main_pid=os.getpid() + 1)
        kwargs["observation_owner"] = owner
    if case == "b2-binding": record["binding"]["activation_generation"] = 8
    if case == "b2-control": record["control_url"] = "https://foreign.invalid"
    if case in ("b2-stable", "b2-endpoint"):
        if half:
            record["stable_id" if case == "b2-stable" else "tailnet_addresses"] = "changed-stable" if case == "b2-stable" else ["100.64.0.11"]
            if case == "b2-endpoint":
                venue.admin.nodes = (replace(venue.admin.nodes[0], ip_addresses=("100.64.0.11",)),)
        else:
            observed = replace(venue.failed, local_node_stable_id="prior-stable" if case == "b2-stable" else "self-stable",
                               home_endpoint="100.64.0.12:443")
            put(venue.path(ACTIVATION_PATH), observed.to_bytes())
    if case == "b3-changed":
        original_nodes = venue.admin.list_nodes
        def changed(**kw: Any) -> Any:
            value = original_nodes(**kw)
            return value if venue.admin.node_reads == 1 else (replace(value[0], id=2),)
        monkeypatch.setattr(venue.admin, "list_nodes", changed)
    before = venue.snapshot()
    with peer(venue, json.dumps(record).encode()):
        with pytest.raises(RecoveryRefused) as caught:
            venue.recover(**kwargs)
    assert str(caught.value) in ("recovery_refused", "token_source_invalid")
    assert venue.snapshot() == before
    venue.no_effects()
    if case in ("source", "release", "auth", "generation", "operation"):
        assert venue.admin.calls == []


@pytest.mark.parametrize("history", ["empty", "expired", "used"])
def test_rollover_retains_txn_and_has_no_effects(venue: RecoveryVenue, history: str) -> None:
    if history == "empty": venue.admin.keys = ()
    if history == "expired":
        venue.admin.keys = (replace(venue.admin.keys[0], used=False, expiration=datetime.now(timezone.utc) - timedelta(seconds=1)),)
    venue.admin.nodes = ()
    before = venue.records()[1], venue.reservation.read_bytes()
    result = venue.recover()
    assert result.phase == "prepared" and result.activation_generation == 8
    assert len(result.operation_id) == 32 and set(result.operation_id) <= set("0123456789abcdef")
    assert result.operation_id != venue.failed.operation_id
    assert result.local_node_stable_id is None and result.home_endpoint is None
    assert (venue.records()[1], venue.reservation.read_bytes()) == before
    assert venue.owner.verifications == []
    venue.no_effects()


@pytest.mark.parametrize("case", ["absent", "operation", "binding", "newer-failed", "older-active", "half-foreign"])
def test_other_pairs_refuse_without_recovery(venue: RecoveryVenue, case: str) -> None:
    txn = venue.transactions.read_optional()
    if case == "absent": venue.path(TRANSACTION_PATH).unlink()
    if case == "operation": txn = replace(txn, operation_id="a" * 32)
    if case == "newer-failed": txn = replace(txn, activation_generation=8)
    if case == "binding": txn = replace(txn, home_id="foreign")
    if case == "older-active": txn = replace(txn, activation_generation=6, phase="activating")
    if case == "half-foreign": txn = replace(txn, phase="joined", operation_id="a" * 32,
        recovery_observation=dict(local_node_stable_id="self-stable", home_endpoint="100.64.0.10:443"))
    if case != "absent": put(venue.path(TRANSACTION_PATH), txn.to_bytes())
    before = venue.snapshot()
    # Same terminal pair is valid entry but live usable key without a node forbids rollover.
    venue.admin.nodes = (); venue.admin.keys = (replace(venue.admin.keys[0], used=False),)
    with pytest.raises(RecoveryRefused): venue.recover()
    assert venue.snapshot() == before
    venue.no_effects()


@pytest.mark.parametrize("receipt_phase,txn_phase", [("prepared", "activating"), ("activating", "activating"),
    ("joined", "joined"), ("activating", "failed_uncertain"), ("failed_uncertain", "joined")])
def test_nonterminal_entry_is_terminal_only(venue: RecoveryVenue, receipt_phase: str, txn_phase: str) -> None:
    put(venue.path(ACTIVATION_PATH), replace(venue.failed, phase=receipt_phase).to_bytes())
    txn = replace(venue.transactions.read_optional(), phase=txn_phase)
    put(venue.path(TRANSACTION_PATH), txn.to_bytes())
    with pytest.raises(RecoveryRefused): venue.recover()
    venue.terminal()
    assert venue.admin.calls == [] and venue.owner.verifications == []
    venue.no_effects()


@pytest.mark.parametrize("destination", ["txn", "receipt", "rollover"])
def test_source_checked_before_each_publication(venue: RecoveryVenue, monkeypatch: pytest.MonkeyPatch, destination: str) -> None:
    if destination == "rollover": venue.empty()
    original = venue.recovery().__class__._source
    count = 0
    def rotate(self, receipt: HomeActivationReceipt) -> Any:
        nonlocal count
        count += 1
        if count == (3 if destination == "receipt" else 2):
            put(venue.source, b"synthetic-rotated")
        return original(self, receipt)
    monkeypatch.setattr(HomeActivationRecovery, "_source", rotate)
    before = venue.records()
    with peer(venue, json.dumps(reply(venue.failed)).encode()):
        with pytest.raises(RecoveryRefused, match="token_source_invalid"): venue.recover()
    assert venue.records()[0] == before[0]
    if destination == "receipt":
        assert venue.transactions.read_optional().recovery_observation is not None
    else:
        assert venue.records() == before
    venue.no_effects()


class Crash(BaseException):
    pass


BOUNDARIES = ["before-open", "after-open", "before-file-fsync", "after-file-fsync",
              "before-rename", "after-rename", "before-dir-fsync", "after-dir-fsync"]


@pytest.mark.parametrize("destination", ["txn", "receipt", "rollover"])
@pytest.mark.parametrize("boundary", BOUNDARIES)
def test_crash_boundaries(venue: RecoveryVenue, monkeypatch: pytest.MonkeyPatch, destination: str, boundary: str) -> None:
    if destination == "rollover": venue.empty()
    name = "home-activation-txn.json" if destination == "txn" else "home-activation.json"
    before = venue.snapshot()
    opened, renamed = False, False
    original_open, original_sync = os.open, os.fsync
    cls = venue.receipts._io.__class__
    original_rename = cls._rename_provision_entry
    def open_file(path: Any, flags: int, *args: Any, **kw: Any) -> int:
        nonlocal opened
        selected = path == "." + name + ".pending" and flags & os.O_CREAT
        if selected and boundary == "before-open": raise Crash()
        fd = original_open(path, flags, *args, **kw)
        if selected:
            opened = True
            if boundary == "after-open":
                os.close(fd); raise Crash()
        return fd
    def sync(fd: int) -> None:
        kind = "dir" if stat.S_ISDIR(os.fstat(fd).st_mode) else "file"
        selected = opened and ((kind == "file" and not renamed) or (kind == "dir" and renamed))
        if selected and boundary == "before-" + kind + "-fsync": raise Crash()
        result = original_sync(fd)
        if selected and boundary == "after-" + kind + "-fsync": raise Crash()
        return result
    def rename_entry(parent: int, pending: str, final: str, *, updating: bool=False) -> None:
        nonlocal renamed
        if final == name and boundary == "before-rename": raise Crash()
        result = original_rename(parent, pending, final, updating=updating)
        if final == name:
            renamed = True
            if boundary == "after-rename": raise Crash()
        return result
    with monkeypatch.context() as patch, peer(venue, json.dumps(reply(venue.failed)).encode()) as (path, _):
        patch.setattr(os, "open", open_file); patch.setattr(os, "fsync", sync)
        patch.setattr(cls, "_rename_provision_entry", staticmethod(rename_entry))
        with pytest.raises(Crash): venue.recover()
    path.unlink()
    after = venue.snapshot()
    pending = "." + name + ".pending"
    pending_boundaries = ("after-open", "before-file-fsync", "after-file-fsync", "before-rename")
    if boundary in pending_boundaries:
        assert pending in after
        # At receipt boundaries the txn-first target is already published.
        assert after["home-activation.json"] == before["home-activation.json"]
        if destination != "receipt": assert venue.records()[1] == before["home-activation-txn.json"]
        with pytest.raises(RecoveryRefused, match="pending_residue"): venue.recover()
        with pytest.raises(ActivationRefused, match="pending_residue"): venue.coordinator().run(venue.prepared)
        assert venue.snapshot() == after
    elif destination == "rollover" and renamed:
        assert venue.receipts.read(expected_generation=8).phase == "prepared"
        assert venue.records()[1] == before["home-activation-txn.json"]
    elif destination == "receipt" and renamed:
        assert venue.receipts.read(expected_generation=7).phase == "ready"
    else:
        if not renamed and destination != "receipt": assert after == before
        venue.owner.verifications.clear()
        venue.admin.calls.clear()
        venue.admin.node_reads = 0
        with peer(venue, json.dumps(reply(venue.failed)).encode()):
            result = venue.recover()
        assert result.phase == ("prepared" if destination == "rollover" else "ready")
        if destination != "rollover": assert venue.owner.verifications == list(EFFECTS)
    venue.no_effects()


@pytest.mark.parametrize("case", ["valid", "same", "newer", "active", "joined"])
def test_coordinator_rollover_admission(venue: RecoveryVenue, monkeypatch: pytest.MonkeyPatch, case: str) -> None:
    venue.empty()
    prepared = venue.recover()
    txn = venue.transactions.read_optional()
    if case == "same": txn = replace(txn, activation_generation=8, operation_id=prepared.operation_id)
    if case == "newer": txn = replace(txn, activation_generation=9)
    if case in ("active", "joined"): txn = replace(txn, phase="activating" if case == "active" else "joined")
    put(venue.path(TRANSACTION_PATH), txn.to_bytes())
    before = venue.records()
    # Observe the real first-intent publication, then simulate process death before effects.
    original = venue.receipts.__class__._publish_record
    def first_intent(store: Any, path: Any, data: bytes, **kwargs: Any) -> None:
        result = original(store, path, data, **kwargs)
        if path == TRANSACTION_PATH and HomeActivationTransaction.parse(data).phase == "activating": raise Crash()
        return result
    monkeypatch.setattr(venue.owner, "verify", lambda *a: True)
    monkeypatch.setattr(venue.receipts.__class__, "_publish_record", first_intent)
    if case == "valid":
        with pytest.raises(Crash): venue.coordinator().run(prepared)
        current = venue.transactions.read_optional()
        assert current.matches(prepared) and current.effects == (Effect("preflight", "intent", {}),)
        assert venue.records()[0] == before[0]
    else:
        with pytest.raises(ActivationRefused): venue.coordinator().run(prepared)
        if case in ("newer", "active", "joined"): assert venue.records() == before
        else: assert venue.receipts.read(expected_generation=8).phase == "failed_uncertain"
    venue.no_effects()


@pytest.mark.parametrize("case", ["missing", "symlink", "hardlink", "mode", "directory", "oversize", "truncated", "timezone", "window", "parent-link", "parent-mode"])
def test_reservation_lookup_refuses_unsafe_unchanged(venue: RecoveryVenue, case: str) -> None:
    path = venue.reservation
    if case == "missing": path.unlink()
    if case == "symlink":
        target = path.with_name("target"); path.rename(target); path.symlink_to(target)
    if case == "hardlink": os.link(path, path.with_name("linked"))
    if case == "mode": path.chmod(0o644)
    if case == "directory": path.unlink(); path.mkdir(mode=0o700)
    if case == "oversize": put(path, b"a" * 161)
    if case == "truncated": put(path, b"torn")
    if case == "timezone": put(path, b"2026-11-18T00:00:00\n2026-11-18T00:01:00\n")
    if case == "window": put(path, ("\n".join(t.isoformat() for t in reversed(venue.window)) + "\n").encode())
    if case == "parent-link":
        parent = path.parent; target = parent.with_name("other"); parent.rename(target); parent.symlink_to(target)
    if case == "parent-mode": path.parent.chmod(0o755)
    before = venue.records()
    with pytest.raises(AdminRefused, match="refused"):
        home_admin.read_local_node_window(venue.failed.operation_id, root=venue.root, root_owner=venue.owner_ids)
    assert venue.records() == before


def test_recovery_marker_is_closed_and_linear_validators_stay_terminal(venue: RecoveryVenue) -> None:
    txn = venue.transactions.read_optional()
    pair = dict(local_node_stable_id="self-stable", home_endpoint="100.64.0.10:443")
    target = replace(txn, phase="joined", recovery_observation=pair)
    ready = replace(venue.failed, phase="ready", **pair)
    with pytest.raises(HomeActivationError): validate_transition(venue.failed, ready)
    with pytest.raises(HomeActivationError): validate_transaction_transition(txn, target)
    for bad in ({}, dict(pair, secret="synthetic-private-canary"), dict(pair, home_endpoint="bad")):
        with pytest.raises(HomeActivationError): replace(target, recovery_observation=bad)
    with pytest.raises(HomeActivationError): replace(target, phase="activating")
    with pytest.raises(RecoveryRefused): validate_recovery_transaction(replace(txn, phase="joined"), target)
    data = json.loads(target.to_bytes()); data["recovery_observation"] = None
    with pytest.raises(HomeActivationError): HomeActivationTransaction.parse(json.dumps(data).encode())
    assert repr(venue.recovery()) == "HomeActivationRecovery(<redacted>)"
    assert str(RecoveryRefused("synthetic-private-canary")) == "recovery_refused"
