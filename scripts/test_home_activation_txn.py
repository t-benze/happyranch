"""Standalone B4 fake-owner/real-files checks; no services, live keys or public caller.

Run: uv run --frozen --python /usr/bin/python3 pytest scripts/test_home_activation_txn.py -v --tb=short

Authoring answers (all use existing temp-root/root_owner and monkeypatch venues;
no test-only production seam, export or flag):
* test_closed_transaction and test_transaction_transitions: contract = closed,
  ordered, immutable B4 JSON; regression = accept reordered or extra entries.
  B1 parser tests cover a different format, not per-effect progress.
* test_a5_store: contract = no unsafe publication/adoption, durable literal bytes;
  regression = omit pending refusal or fsync. B1 tests exercise the receipt path,
  not this separate destination/transition parser.
* test_forward_order_and_fence: contract = intent durable before effect, done only
  after independent verification, one mint, no public ready; regression = mark done
  before verify or mint twice. B1/admin tests stop before coordinator composition.
* test_crash_boundaries (all 19 publications), test_failed_terminal_publication
  (both destinations, healed/still-failing markers, lost-start/invalid-source,
  all three reviewer boundaries and every completed effect prefix), and
  test_existing_checkpoint_refusal (all five drift/reuse cases): contract = ruling 7
  permits only terminal markers after any durable intent on a fresh run, with no
  new owner/mint/port/config effect or forward publication. Regression = restore
  fresh-instance resume at replayable/completed checkpoints. The old failed-terminal
  cases stopped at an unfinished service intent and kept the source invalid; minter
  tests lack cross-record crash boundaries. The forward-order test separately owns
  successful same-run ordering; its second invocation must now refuse terminally.
* test_lost_effect: contract = lost start/create/mint/enroll results persist terminal
  state without duplicate effects; regression = fail to persist terminal state.
  Existing minter tests lack cross-record crash boundaries.
* test_source_boundaries: contract = ruling-6 byte-identical
  pre-intent refusal/retry, terminal post-intent refusal, actual same-FD/UID checks;
  regression = skip source validation or gate failure markers on it. Installer token
  tests lack B4 records and this boundary; we keep their OS-observation technique.
* test_stale_and_ports: contract = reject stale policy/generation/occupied port
  before service/admin effects. Regression = omit preflight. Installer's inert preparation
  cannot prove the new forward path's admission and record preservation.
"""
from __future__ import annotations

import hashlib
import json
import os
import pwd
import socket
import stat
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from runtime.remote_access import headscale_service, home_admin, installer
from runtime.remote_access import release_verifier
from runtime.remote_access.home_activation import (
    ACTIVATION_PATH, MEMBER_PATHS, HomeActivationError, HomeActivationReceipt,
    HomeActivationStore,
)
from runtime.remote_access.home_activation_coordinator import ActivationRefused, HomeActivationCoordinator
from runtime.remote_access.home_activation_txn import (
    TRANSACTION_PATH, Effect, HomeActivationTransaction, HomeActivationTransactionStore,
)
from runtime.remote_access.invite_issuer import HomeInviteConfigRecord, IssuerError, MintedKey, read_home_invite_config

ORDER = ("preflight", "headscale_start_verify", "db_users", "local_node_mint",
         "credential_staging", "home_config", "managed_services", "joined")
CONFIGS = ("/etc/happyranch/connector.json", "/etc/happyranch/sidecar.json")
PORT_PREFLIGHT = headscale_service.preflight_ports


def put(path: Path, data: bytes, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    path.chmod(mode)


class Crash(BaseException):
    pass


class Owner:
    def __init__(self, venue: Venue) -> None:
        self.venue = venue
        self.calls: list[str] = []
        self.verifications: list[str] = []
        self.policy = True
        self.lost: str | None = None

    def marker(self, name: str) -> Path:
        return self.venue.root / "observed" / name

    def verify(self, effect: str, receipt: HomeActivationReceipt) -> bool:
        self.verifications.append(effect)
        if effect == "preflight":
            return self.policy and receipt.activation_generation == 7
        if effect == "joined":
            return self.marker("managed_services").read_bytes() == b"verified"
        if effect == "home_config":
            return all((self.venue.root / name.lstrip("/")).read_bytes() == b'{"synthetic":"live"}'
                       for name in CONFIGS)
        return self.marker(effect).read_bytes() == b"verified"

    def perform(self, effect: str, receipt: HomeActivationReceipt, credential: MintedKey | None) -> None:
        transaction = self.venue.transactions.read_optional()
        assert transaction is not None and transaction.effects[-1].name == effect
        assert transaction.effects[-1].state == "intent"
        assert self.venue.receipts.read(expected_generation=7).phase == "activating"
        self.calls.append(effect)
        if effect == "credential_staging":
            assert credential is not None and credential.key == "a" * 48
        else:
            assert credential is None
        if effect == "home_config":
            for name in CONFIGS:
                put(self.venue.root / name.lstrip("/"), b'{"synthetic":"live"}')
        else:
            put(self.marker(effect), b"verified")
        if self.lost == effect:
            raise OSError("synthetic lost reply")


class Venue:
    def __init__(self, tmp: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.root, self.release = tmp / "root", tmp / "release"
        self.root.mkdir(mode=0o700)
        self.release.mkdir(mode=0o700)
        user = pwd.getpwuid(os.getuid())
        isolated = list(user)
        isolated[5] = "/home/installer-release-fixture"
        monkeypatch.setattr(release_verifier.pwd, "getpwuid", lambda uid: pwd.struct_passwd(isolated))
        for relative in ("etc", "etc/happyranch", "var", "var/lib", "var/lib/happyranch-remote-access"):
            (self.root / relative).mkdir(parents=True, exist_ok=True,
                                        mode=0o700 if relative.endswith("remote-access") else 0o755)
        self.owner_ids = (os.getuid(), os.getgid())
        self.receipts = HomeActivationStore(root=self.root, root_owner=self.owner_ids)
        self.transactions = HomeActivationTransactionStore(root=self.root, root_owner=self.owner_ids)
        for name in MEMBER_PATHS:
            put(self.root / name.lstrip("/"), ("synthetic " + name).encode())
        config = dict(format="home-invite-config@2", release_uid=os.getuid(), home_id="home",
                      control_plane_id="control", connector_id="connector",
                      ca_der_sha256="c" * 64, dial_ip="192.168.1.40",
                      control_url="https://control.happyranch.invalid:28443")
        config_bytes = json.dumps(config).encode()
        HomeInviteConfigRecord.parse(config_bytes)
        put(self.root / "etc/happyranch/invite-issuer.json", config_bytes)
        digests = {name: hashlib.sha256((self.root / name.lstrip("/")).read_bytes()).hexdigest()
                   for name in MEMBER_PATHS}
        self.prepared = HomeActivationReceipt(
            release_uid=os.getuid(), home_id="home", control_plane_id="control", connector_id="connector",
            activation_generation=7, operation_id="b" * 32, ca_der_sha256="c" * 64,
            dial_ip="192.168.1.40", member_digests=digests, local_node_stable_id=None,
            home_endpoint=None, phase="prepared")
        self.receipts.publish(self.prepared)
        self.source = self.release / "daemon.token"
        put(self.source, b"synthetic-token")
        put(self.root / "etc/happyranch/daemon.token", b"synthetic-token")
        release_record = self.receipts._io._release_record(user.pw_name, self.release)
        put(self.root / "etc/happyranch/release-daemon.json", json.dumps(release_record).encode(), 0o644)
        self.owner = Owner(self)
        self.mints: list[str] = []
        self.lost_mint = False
        self.port_checks: list[str] = []
        monkeypatch.setattr(headscale_service, "preflight_ports", self.preflight)
        monkeypatch.setattr(home_admin, "mint_local_node", self.mint)

    def preflight(self, ip: str) -> None:
        transaction = self.transactions.read_optional()
        assert transaction.effects == (Effect("preflight", "intent", {}),)
        assert self.receipts.read(expected_generation=transaction.activation_generation).phase == "activating"
        self.port_checks.append(ip)

    def mint(self, operation_id: str, expires_at: datetime) -> MintedKey:
        transaction = self.transactions.read_optional()
        assert transaction is not None and transaction.effects[-1] == Effect("local_node_mint", "intent", {})
        assert operation_id not in self.mints
        self.mints.append(operation_id)
        put(self.owner.marker("local_node_mint"), b"verified")
        if self.lost_mint:
            raise OSError("synthetic lost mint result")
        assert datetime.now(timezone.utc) < expires_at <= datetime.now(timezone.utc) + timedelta(seconds=120)
        return MintedKey("a" * 48, expires_at)

    def coordinator(self) -> HomeActivationCoordinator:
        return HomeActivationCoordinator(owner=self.owner, root=self.root, root_owner=self.owner_ids)

    def path(self, path: str) -> Path:
        return self.root / path.lstrip("/")

    def records(self) -> tuple[bytes, bytes | None]:
        txn = self.path(TRANSACTION_PATH)
        return self.path(ACTIVATION_PATH).read_bytes(), txn.read_bytes() if txn.exists() else None

    def terminal(self) -> None:
        assert self.receipts.read(expected_generation=7).phase == "failed_uncertain"
        assert self.transactions.read_optional().phase == "failed_uncertain"
        for data in self.records():
            assert b"synthetic-token" not in data and b"a" * 48 not in data
            assert b"ControlURL" not in data and b"control_tls" not in data


@pytest.fixture
def venue(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Venue:
    return Venue(tmp_path, monkeypatch)


@pytest.mark.parametrize("case", ["extra", "missing", "duplicate", "oversized", "bool", "uid",
                                 "operation", "order", "intent-order", "effect-extra", "path", "digest", "joined"])
def test_closed_transaction(venue: Venue, case: str) -> None:
    transaction = HomeActivationTransaction.begin(venue.prepared)
    assert HomeActivationTransaction.parse(transaction.to_bytes()) == transaction
    padded = transaction.to_bytes() + b" " * (16384 - len(transaction.to_bytes()))
    assert HomeActivationTransaction.parse(padded) == transaction
    value = json.loads(transaction.to_bytes())
    if case == "extra": value["extra"] = None
    if case == "missing": value.pop("home_id")
    if case == "bool": value["activation_generation"] = True
    if case == "uid": value["release_uid"] = 0
    if case == "operation": value["operation_id"] = "x" * 32
    if case == "order": value["effects"][0]["name"] = "db_users"
    if case == "intent-order": value["effects"].append(dict(name="headscale_start_verify", state="intent", member_digests={}))
    if case == "effect-extra": value["effects"][0]["credential"] = "synthetic"
    if case == "path": value["effects"][0]["member_digests"] = {"/arbitrary": "a" * 64}
    if case == "digest":
        value["effects"] = [dict(name=name, state="done", member_digests={}) for name in ORDER[:6]]
        value["effects"][-1]["member_digests"] = {name: "A" * 64 for name in CONFIGS}
    if case == "joined": value["phase"] = "joined"
    data = json.dumps(value).encode()
    if case == "duplicate": data = b'{"home_id":"home",' + data[1:]
    if case == "oversized": data += b" " * (16385 - len(data))
    with pytest.raises(HomeActivationError):
        HomeActivationTransaction.parse(data)


def test_transaction_transitions(venue: Venue) -> None:
    first = HomeActivationTransaction.begin(venue.prepared)
    venue.transactions.publish(first)
    for bad in (first, replace(first, operation_id="d" * 32),
                replace(first, effects=(Effect("preflight", "done", {}), Effect("headscale_start_verify", "intent", {})))):
        with pytest.raises(HomeActivationError):
            venue.transactions.publish(bad, expected=first)
    done = replace(first, effects=(Effect("preflight", "done", {}),))
    venue.transactions.publish(done, expected=first)
    failed = replace(done, phase="failed_uncertain")
    venue.transactions.publish(failed, expected=done)
    with pytest.raises(HomeActivationError):
        venue.transactions.publish(done, expected=failed)


@pytest.mark.parametrize("case", ["durable", "pending", "symlink", "hardlink", "mode", "parent"])
def test_a5_store(venue: Venue, monkeypatch: pytest.MonkeyPatch, case: str) -> None:
    path = venue.path(TRANSACTION_PATH)
    pending = path.with_name("." + path.name + ".pending")
    value = HomeActivationTransaction.begin(venue.prepared)
    events: list[str] = []
    fsync = os.fsync
    def sync(fd: int) -> None:
        events.append("dir" if stat.S_ISDIR(os.fstat(fd).st_mode) else "file")
        fsync(fd)
    monkeypatch.setattr(os, "fsync", sync)
    if case == "pending": put(pending, b"retained")
    if case in ("symlink", "hardlink"):
        other = venue.root / "other"
        put(other, value.to_bytes())
        if case == "symlink": path.symlink_to(other)
        else: os.link(other, path)
    if case == "mode": put(path, value.to_bytes(), 0o644)
    if case == "parent": path.parent.chmod(0o755)
    if case == "durable":
        venue.transactions.publish(value)
        assert events == ["file", "dir"]
        assert path.read_bytes() == value.to_bytes()
        assert stat.S_IMODE(path.stat().st_mode) == 0o600 and path.stat().st_nlink == 1
        assert not pending.exists()
        with pytest.raises(HomeActivationError): venue.transactions.publish(value)
    else:
        with pytest.raises((HomeActivationError, installer.InstallerError)):
            venue.transactions.publish(value)
        assert events == []
        if case == "pending": assert pending.read_bytes() == b"retained"


def test_forward_order_and_fence(venue: Venue, monkeypatch: pytest.MonkeyPatch) -> None:
    writes: list[tuple] = []
    verified: set[str] = set()
    verify = venue.owner.verify
    def observe_verify(effect: str, receipt: HomeActivationReceipt) -> bool:
        result = verify(effect, receipt)
        if result: verified.add(effect)
        return result
    monkeypatch.setattr(venue.owner, "verify", observe_verify)
    publish = HomeActivationStore._publish_record
    def observe(store: HomeActivationStore, path: str, data: bytes, **kwargs: object) -> None:
        if path == TRANSACTION_PATH:
            value = HomeActivationTransaction.parse(data)
            last = value.effects[-1]
            if last.state == "done": assert last.name in verified
            writes.append((last.name, last.state, value.phase))
        else:
            value = HomeActivationReceipt.parse(data)
            assert value.phase in ("activating", "joined")
        publish(store, path, data, **kwargs)
        if path == ACTIVATION_PATH and value.phase == "activating":
            assert venue.receipts.read(expected_generation=7).phase == "activating"
            with pytest.raises(IssuerError):
                read_home_invite_config(root=venue.root, root_owner=venue.owner_ids, expected_generation=7)
    monkeypatch.setattr(HomeActivationStore, "_publish_record", observe)
    result = venue.coordinator().run(venue.prepared)
    assert result.phase == "joined" and result.local_node_stable_id is None and result.home_endpoint is None
    assert venue.owner.calls == ["headscale_start_verify", "db_users", "credential_staging", "home_config", "managed_services"]
    assert writes == [(name, state, "activating") for name in ORDER for state in ("intent", "done")] + [("joined", "done", "joined")]
    assert venue.port_checks == ["192.168.1.40"] and venue.mints == ["b" * 32]
    assert venue.transactions.read_optional().effects[5].member_digests == {
        name: hashlib.sha256(b'{"synthetic":"live"}').hexdigest() for name in CONFIGS}
    # Only the original run may progress; even a joined checkpoint cannot resume.
    monkeypatch.setattr(HomeActivationStore, "_publish_record", publish)
    with pytest.raises(ActivationRefused): venue.coordinator().run(venue.prepared)
    venue.terminal()
    assert len(venue.mints) == 1 and len(venue.owner.calls) == 5


@pytest.mark.parametrize("boundary", range(1, 20))
def test_crash_boundaries(venue: Venue, monkeypatch: pytest.MonkeyPatch, boundary: int) -> None:
    publish = HomeActivationStore._publish_record
    count = 0
    def crash(store: HomeActivationStore, *args: object, **kwargs: object) -> None:
        nonlocal count
        publish(store, *args, **kwargs)
        count += 1
        if count == boundary: raise Crash()
    with monkeypatch.context() as patch:
        patch.setattr(HomeActivationStore, "_publish_record", crash)
        with pytest.raises(Crash): venue.coordinator().run(venue.prepared)
    prior = list(venue.mints), list(venue.owner.calls), list(venue.port_checks)
    with pytest.raises(ActivationRefused): venue.coordinator().run(venue.prepared)
    venue.terminal()
    assert (venue.mints, venue.owner.calls, venue.port_checks) == prior


@pytest.mark.parametrize("lost", ["headscale_start_verify", "db_users", "local_node_mint", "credential_staging", "managed_services"])
def test_lost_effect(venue: Venue, lost: str) -> None:
    venue.owner.lost = lost
    venue.lost_mint = lost == "local_node_mint"
    with pytest.raises(ActivationRefused): venue.coordinator().run(venue.prepared)
    venue.terminal()
    effects, mints = list(venue.owner.calls), list(venue.mints)
    with pytest.raises(ActivationRefused): venue.coordinator().run(venue.prepared)
    assert venue.owner.calls == effects and venue.mints == mints


@pytest.mark.parametrize("destination", ["txn", "receipt"])
@pytest.mark.parametrize("markers_still_fail", [False, True], ids=["healed-markers", "failed-markers"])
@pytest.mark.parametrize("failure", ["lost-start", "invalid-source", "before-activating",
                                     "before-home-config-intent", "before-joined",
                                     *("done-" + name for name in ORDER)])
def test_failed_terminal_publication(venue: Venue, monkeypatch: pytest.MonkeyPatch,
                                     destination: str, failure: str, markers_still_fail: bool) -> None:
    venue.owner.lost = "headscale_start_verify" if failure == "lost-start" else None
    coordinator = venue.coordinator()
    store_type = HomeActivationTransactionStore if destination == "txn" else HomeActivationStore
    publish = store_type.publish
    def fail(store: HomeActivationStore, value: object, **kwargs: object) -> None:
        if value.phase == "failed_uncertain": raise HomeActivationError("state")
        publish(store, value, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(store_type, "publish", fail)
        if failure == "invalid-source":
            perform = venue.owner.perform
            def invalid(*args: object) -> None:
                try: perform(*args)
                finally: venue.source.chmod(0o644)
            patch.setattr(venue.owner, "perform", invalid)
        if failure in ("before-activating", "before-joined"):
            publish_receipt = coordinator._publish_receipt
            def reject_receipt(value: HomeActivationReceipt, previous: HomeActivationReceipt) -> None:
                if value.phase == failure.removeprefix("before-"):
                    venue.source.chmod(0o644)
                publish_receipt(value, previous)
            patch.setattr(coordinator, "_publish_receipt", reject_receipt)
        if failure == "before-home-config-intent" or failure.startswith("done-"):
            publish_txn = coordinator._publish_transaction
            def reject_txn(value: HomeActivationTransaction, receipt: HomeActivationReceipt,
                           previous: HomeActivationTransaction | None) -> None:
                last = value.effects[-1]
                if failure == "before-home-config-intent":
                    if last.name == "home_config" and last.state == "intent":
                        venue.source.chmod(0o644)
                    publish_txn(value, receipt, previous)
                else:
                    publish_txn(value, receipt, previous)
                    if last.name == failure.removeprefix("done-") and last.state == "done":
                        # The next source-gated publication/effect must fail after this done prefix.
                        venue.source.chmod(0o644)
            patch.setattr(coordinator, "_publish_transaction", reject_txn)
        with pytest.raises(ActivationRefused): coordinator.run(venue.prepared)
    transaction = venue.transactions.read_optional()
    assert transaction.phase == ("activating" if destination == "txn" else "failed_uncertain") or (
        destination == "txn" and failure == "before-joined" and transaction.phase == "joined")
    if failure.startswith("done-"):
        assert transaction.effects[-1].name == failure.removeprefix("done-")
        assert transaction.effects[-1].state == "done"
    if failure == "before-activating":
        assert transaction.effects == (Effect("preflight", "intent", {}),)
    if failure == "before-home-config-intent":
        assert transaction.effects[-1] == Effect("credential_staging", "done", {})
    assert venue.receipts.read(expected_generation=7).phase == (
        "prepared" if failure == "before-activating" else "activating")
    before = venue.records()
    effects = list(venue.owner.calls), list(venue.mints), list(venue.port_checks)
    configs = [venue.path(name).read_bytes() if venue.path(name).exists() else None for name in CONFIGS]
    # Heal BEFORE the fresh instance: source invalidity must not mask a resume bug.
    venue.source.chmod(0o600)
    publications: list[tuple[str, str]] = []
    publish_record = HomeActivationStore._publish_record
    def observe(store: HomeActivationStore, path: str, data: bytes, **kwargs: object) -> None:
        phase = json.loads(data)["phase"]
        publications.append((path, phase))
        assert phase == "failed_uncertain", "fresh instance attempted forward publication"
        publish_record(store, path, data, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(HomeActivationStore, "_publish_record", observe)
        if markers_still_fail: patch.setattr(store_type, "publish", fail)
        with pytest.raises(ActivationRefused): venue.coordinator().run(venue.prepared)
    assert (venue.owner.calls, venue.mints, venue.port_checks) == effects
    assert [venue.path(name).read_bytes() if venue.path(name).exists() else None for name in CONFIGS] == configs
    if markers_still_fail:
        assert venue.records() == before and publications == []
    else:
        venue.terminal()
        assert publications == ([(TRANSACTION_PATH, "failed_uncertain")] if destination == "txn" else []) + [
            (ACTIVATION_PATH, "failed_uncertain")]
    # Repeated fresh attempts retain terminality or refuse with unchanged bytes.
    stable = venue.records()
    with monkeypatch.context() as patch:
        patch.setattr(HomeActivationStore, "_publish_record", observe)
        if markers_still_fail: patch.setattr(store_type, "publish", fail)
        with pytest.raises(ActivationRefused): venue.coordinator().run(venue.prepared)
    assert venue.records() == stable
    assert (venue.owner.calls, venue.mints, venue.port_checks) == effects


def invalidate_source(venue: Venue, patch: pytest.MonkeyPatch, kind: str) -> None:
    info = venue.source.stat()
    if kind == "uid":
        fstat = os.fstat
        def wrong_uid(fd: int) -> os.stat_result:
            value = fstat(fd)
            if (value.st_dev, value.st_ino) == (info.st_dev, info.st_ino):
                fields = list(value)
                fields[4] += 1
                return os.stat_result(fields)
            return value
        patch.setattr(os, "fstat", wrong_uid)
    if kind == "fd":
        other = venue.release / "other"
        put(other, b"synthetic-token")
        opening = os.open
        def swapped(path: object, flags: int, *args: object, **kwargs: object) -> int:
            if path == "daemon.token" and "dir_fd" in kwargs and os.fstat(kwargs["dir_fd"]).st_ino == venue.release.stat().st_ino:
                return opening("other", flags, *args, **kwargs)
            return opening(path, flags, *args, **kwargs)
        patch.setattr(os, "open", swapped)
    if kind == "rotation": venue.source.write_bytes(b"synthetic-rotation")
    if kind == "mode": venue.source.chmod(0o644)


@pytest.mark.parametrize("fault", ["mode", "uid", "fd", "rotation"])
@pytest.mark.parametrize("when", ["start", "before-intent", "before-activating", "before-joined", "between-effects"])
def test_source_boundaries(venue: Venue, monkeypatch: pytest.MonkeyPatch, when: str, fault: str) -> None:
    before = venue.records()
    coordinator = venue.coordinator()
    with monkeypatch.context() as patch:
        def invalidate() -> None: invalidate_source(venue, patch, fault)
        if when == "start": invalidate()
        if when == "before-intent":
            publish_txn = coordinator._publish_transaction
            def change_txn(value: HomeActivationTransaction, receipt: HomeActivationReceipt,
                           previous: HomeActivationTransaction | None) -> None:
                if previous is None: invalidate()
                publish_txn(value, receipt, previous)
            patch.setattr(coordinator, "_publish_transaction", change_txn)
        if when in ("before-activating", "before-joined"):
            publish = coordinator._publish_receipt
            def change(value: HomeActivationReceipt, previous: HomeActivationReceipt) -> None:
                if value.phase == when.removeprefix("before-"): invalidate()
                publish(value, previous)
            patch.setattr(coordinator, "_publish_receipt", change)
        if when == "between-effects":
            perform = venue.owner.perform
            def change_effect(*args: object) -> None:
                perform(*args)
                invalidate()
            patch.setattr(venue.owner, "perform", change_effect)
        with pytest.raises(ActivationRefused, match="token_source_invalid"):
            coordinator.run(venue.prepared)
    if when in ("start", "before-intent"):
        assert venue.records() == before and venue.mints == [] and venue.owner.calls == []
        venue.source.chmod(0o600)
        venue.source.write_bytes(b"synthetic-token")
        assert venue.coordinator().run(venue.prepared).phase == "joined"
    else:
        venue.terminal()
        count = len(venue.mints), len(venue.owner.calls)
        with pytest.raises(ActivationRefused): venue.coordinator().run(venue.prepared)
        assert (len(venue.mints), len(venue.owner.calls)) == count


@pytest.mark.parametrize("case", ["member", "live-config", "policy", "operation-reuse", "generation"])
def test_existing_checkpoint_refusal(venue: Venue, case: str) -> None:
    venue.coordinator().run(venue.prepared)
    mints, effects = list(venue.mints), list(venue.owner.calls)
    if case == "member": venue.path(MEMBER_PATHS[3]).write_bytes(b"tampered")
    if case == "live-config": venue.path(CONFIGS[0]).write_bytes(b"tampered")
    if case == "policy": venue.owner.policy = False
    if case == "operation-reuse": put(venue.path(ACTIVATION_PATH), venue.prepared.to_bytes())
    prepared = replace(venue.prepared, activation_generation=8) if case == "generation" else venue.prepared
    before = venue.records()
    with pytest.raises(ActivationRefused): venue.coordinator().run(prepared)
    if case == "generation": assert venue.records() == before
    else: venue.terminal()
    assert venue.mints == mints and venue.owner.calls == effects


@pytest.mark.parametrize("case", ["policy", "generation", "ports"])
def test_stale_and_ports(venue: Venue, monkeypatch: pytest.MonkeyPatch, case: str) -> None:
    before = venue.records()
    prepared = venue.prepared
    if case == "policy": venue.owner.policy = False
    if case == "generation": prepared = replace(prepared, activation_generation=8)
    if case == "ports":
        # Drive the real four-bind probe on loopback; only its address predicate
        # and selected address are isolated, never its socket/bind implementation.
        # Occupy the LAN-side bind on separate loopback from installed services.
        monkeypatch.setattr(headscale_service, "_dial_ip_valid", lambda ip: True)
        monkeypatch.setattr(headscale_service, "preflight_ports", lambda ip: PORT_PREFLIGHT("127.0.0.2"))
        with socket.socket() as occupied:
            occupied.bind(("127.0.0.2", 28443))
            with pytest.raises(ActivationRefused): venue.coordinator().run(prepared)
    else:
        with pytest.raises(ActivationRefused): venue.coordinator().run(prepared)
    if case == "ports": venue.terminal()
    else: assert venue.records() == before
    assert venue.owner.calls == [] and venue.mints == []
