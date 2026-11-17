"""Standalone pure issuer checks, synthetic keys and owner-only temporary state."""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

import pytest

from runtime.remote_access.authorization import TrustState
from runtime.remote_access.identity import ConnectorIdentity
from runtime.remote_access.invite_issuer import (
    HomeInviteConfig, HomeInviteIssuer, IssuerError, MintedKey, MintRefused,
)
from runtime.remote_access.invite_v2 import decode
from runtime.remote_access.pairing import PinnedPairingManager
from runtime.remote_access.state_store import AtomicFileTrustStateStore
from runtime.remote_access.streams import StreamRegistry
from scripts.test_invite_v2 import SWIFT_CA

IDENTITY = ConnectorIdentity(tenant_id="tenant", home_id="home", connector_id="connector")
START = datetime(2026, 11, 17, 6, 0, 0, 750000, timezone.utc)


class Clock:
    def __init__(self) -> None:
        self.now = START

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


class Minter:
    def __init__(self, clock: Clock) -> None:
        self.clock = clock
        self.key = secrets.token_hex(24)
        self.calls: list[str] = []
        self.recoveries: list[str] = []
        self.behavior = "success"
        self.lifetime = 500.0
        self.delay = 0.0
        self.cleared = False
        self.observe: Callable[[], None] = lambda: None

    def mint(self, operation_id: str, expires_at: datetime) -> MintedKey:
        self.calls.append(operation_id)
        self.observe()
        expiry = self.clock() + timedelta(seconds=self.lifetime)
        self.clock.advance(self.delay)
        if self.behavior == "refused":
            raise MintRefused(self.key)
        if self.behavior == "uncertain":
            raise ValueError(self.key)
        if self.behavior == "malformed":
            return None  # type: ignore[return-value]
        if self.behavior == "crash":
            os._exit(23)
        return MintedKey(self.key, expiry)

    def recover(self, operation_id: str) -> bool:
        self.recoveries.append(operation_id)
        if self.behavior == "uncertain":
            raise RuntimeError(self.key)
        return self.cleared


def setup(root: Path) -> tuple[HomeInviteIssuer, Minter, PinnedPairingManager, AtomicFileTrustStateStore, Clock]:
    clock = Clock()
    issuer_dir = root / "issuer"
    issuer_dir.mkdir(mode=0o700, exist_ok=True)
    store = AtomicFileTrustStateStore(root / "pairing" / "trust.json", TrustState(IDENTITY, 0, 0))
    manager = PinnedPairingManager(state_store=store, identity=IDENTITY,
                                   registry=StreamRegistry(), now_fn=clock)
    minter = Minter(clock)
    config = HomeInviteConfig(home_id="home", connector_id="connector", control_plane_id="control",
                              ca_der=SWIFT_CA, dial_ip="192.168.1.40", home_endpoint="100.64.0.10:443")
    return (HomeInviteIssuer(config=config, minter=minter, pairing=manager,
                             state_dir=issuer_dir, now_fn=clock), minter, manager, store, clock)


def record(root: Path) -> dict:
    return json.loads((root / "issuer" / "invite-issuer.json").read_bytes())


def category(call: Callable[[], object]) -> str | None:
    try:
        call()
    except IssuerError as error:
        return str(error)
    return None


def test_roundtrip_and_real_one_use_redemption(tmp_path: Path) -> None:
    issuer, minter, manager, store, clock = setup(tmp_path)
    result = issuer.issue()
    parsed = decode(result.invite_text, now=int(clock().timestamp()))
    matches = parsed.join_key == minter.key
    assert matches, "delivered key must be the minted key"
    assert (parsed.home_id, parsed.control_plane_id, parsed.connector_id) == ("home", "control", "connector")
    assert parsed.home_endpoint == "100.64.0.10:443"
    persisted = store.load().invite_record
    assert persisted is not None
    matches = persisted.secret_digest == hashlib.sha256(parsed.pair_secret.encode()).hexdigest()
    assert matches, "only the delivered pairing-secret digest authorizes redemption"
    first = manager.redeem(parsed.pair_secret, "node", "control", clock())
    assert first is not None, "delivered invite must redeem against the real manager"
    assert manager.redeem(parsed.pair_secret, "node", "control", clock()) is None
    device = manager.approve_pending(first.generation, ("control", "node"))
    assert device is not None
    assert manager.complete(first.completion_handle, "node", "control", clock()) is not None
    assert manager.complete(first.completion_handle, "node", "control", clock()) is None


def test_journal_fsynced_before_mint_and_retry_never_redelivers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    issuer, minter, _, _, _ = setup(tmp_path)
    synced: set[str] = set()
    fsync = os.fsync
    def sync(fd: int) -> None:
        info = os.fstat(fd)
        for name, path in (("journal", tmp_path / "issuer" / "invite-issuer.pending"),
                           ("directory", tmp_path / "issuer")):
            if path.exists():
                named = path.stat()
                if (info.st_dev, info.st_ino) == (named.st_dev, named.st_ino):
                    synced.add(name)
        fsync(fd)
    monkeypatch.setattr(os, "fsync", sync)
    observed: dict = {}
    def inspect() -> None:
        path = tmp_path / "issuer" / "invite-issuer.json"
        observed.update(record(tmp_path) if path.exists() else {})
        observed["durable"] = synced == {"journal", "directory"}
    minter.observe = inspect
    result = issuer.issue()
    assert observed.get("operation_id") == minter.calls[0], "mint needs the journalled operation ID"
    assert observed.get("outcome") == "minting"
    assert observed["durable"], "file and parent must both fsync before mint"
    assert category(lambda: issuer.issue(result.operation_id)) == "already_issued"
    assert len(minter.calls) == 1, "retry must not reissue or redeliver"
    assert record(tmp_path)["outcome"] == "issued"


@pytest.mark.parametrize("stage", ["invite", "pending", "delivery"])
def test_replacement_burns_old_ceremony_even_when_mint_refuses(tmp_path: Path, stage: str) -> None:
    issuer, minter, manager, store, clock = setup(tmp_path)
    old = issuer.issue()
    parsed = decode(old.invite_text, now=int(clock().timestamp()))
    handle = None
    if stage != "invite":
        handle = manager.redeem(parsed.pair_secret, "node", "control", clock())
        assert handle is not None
        if stage == "delivery":
            assert manager.approve_pending(handle.generation, ("control", "node")) is not None
    clock.advance(60)
    minter.behavior = "refused"
    observed: list[bool] = []
    def inspect() -> None:
        state = store.load()
        observed.append(state.pending_approval is None and state.delivery is None
                        and state.invite_record is not None
                        and state.invite_record.secret_digest != hashlib.sha256(parsed.pair_secret.encode()).hexdigest())
    minter.observe = inspect
    assert category(issuer.issue) == "refused"
    assert observed == [True], "replacement must burn prior authority BEFORE mint"
    assert category(lambda: issuer.issue(record(tmp_path)["operation_id"])) == "refused"
    assert len(minter.calls) == 2, "a refused operation retry never mints again"
    assert manager.redeem(parsed.pair_secret, "node", "control", clock()) is None
    if handle is not None:
        assert manager.approve_pending(handle.generation, ("control", "node")) is None
        assert manager.complete(handle.completion_handle, "node", "control", clock()) is None
    assert category(lambda: issuer.issue(old.operation_id)) == "superseded"


@pytest.mark.parametrize("lifetime,delay,expected", [(900.7, 0, 300), (20.6, 0, 21), (0.1, 0, None), (500, 301, None), (500, "publication", None)])
def test_effective_deadline_and_slow_mint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                        lifetime: float, delay: float | str, expected: int | None) -> None:
    issuer, minter, _, store, clock = setup(tmp_path)
    minter.lifetime = lifetime
    if delay == "publication":
        fsync = os.fsync
        count = 0
        def sync(fd: int) -> None:
            nonlocal count
            info, parent = os.fstat(fd), (tmp_path / "issuer").stat()
            if (info.st_dev, info.st_ino) == (parent.st_dev, parent.st_ino):
                count += 1
                if count == 2:
                    clock.advance(301)
            fsync(fd)
        monkeypatch.setattr(os, "fsync", sync)
    else:
        minter.delay = delay
    if expected is None:
        assert category(issuer.issue) == "expired", "flooring or a slow mint must never extend the deadline"
        assert record(tmp_path)["outcome"] == "expired"
        return
    result = issuer.issue()
    parsed = decode(result.invite_text, now=int(clock().timestamp()))
    assert result.expires_at == int(START.timestamp()) + expected, "use the earlier deadline, floored"
    assert parsed.expires_at == result.expires_at
    assert store.load().invite_record.expires_at.timestamp() == result.expires_at


@pytest.mark.parametrize("advance,expected,calls", [(59.999, "rate_limited", 1), (60, None, 2), (-1, "rate_limited", 1)])
def test_rate_limit_survives_new_issuer_and_clock_rollback(tmp_path: Path, advance: float, expected: str | None, calls: int) -> None:
    issuer, minter, manager, _, clock = setup(tmp_path)
    issuer.issue()
    clock.advance(advance)
    restarted = HomeInviteIssuer(config=issuer._config, minter=minter, pairing=manager,
                                 state_dir=tmp_path / "issuer", now_fn=clock)
    assert category(restarted.issue) == expected, "one mint attempt per sixty seconds, including after restart"
    assert len(minter.calls) == calls


@pytest.mark.parametrize("behavior", ["uncertain", "malformed", "crash"])
def test_uncertain_operation_never_reissued_after_restart_or_expiry(tmp_path: Path, behavior: str) -> None:
    if behavior == "crash":
        # Real abrupt process exit after the journal, no library/fixture crash.
        code = ("import sys; from pathlib import Path; from scripts.test_invite_issuer import setup; "
                "issuer,minter,*_=setup(Path(sys.argv[1])); minter.behavior='crash'; issuer.issue()")
        child = subprocess.run([sys.executable, "-c", code, str(tmp_path)], capture_output=True, timeout=15)
        assert child.returncode == 23
        journal = record(tmp_path)
        issuer, minter, _, _, clock = setup(tmp_path)
    else:
        issuer, minter, _, _, clock = setup(tmp_path)
        minter.behavior = behavior
        assert category(issuer.issue) == "uncertain", "unknown or malformed mint outcomes are uncertain"
        journal = record(tmp_path)
    before = len(minter.calls)
    clock.advance(3600)
    assert category(issuer.issue) == "uncertain", "expiry never grants automatic remint authority"
    assert category(lambda: issuer.issue(journal["operation_id"])) == "uncertain"
    assert len(minter.calls) == before, "uncertain operation must not mint under a new ID"
    assert record(tmp_path)["operation_id"] == journal["operation_id"]


def test_explicit_recovery_requires_confirmed_cleanup_and_never_mints(tmp_path: Path) -> None:
    issuer, minter, _, _, clock = setup(tmp_path)
    minter.behavior = "uncertain"
    assert category(issuer.issue) == "uncertain"
    operation_id = record(tmp_path)["operation_id"]
    assert category(lambda: issuer.recover(operation_id)) == "uncertain"
    minter.behavior = "success"
    assert category(lambda: issuer.recover(operation_id)) == "uncertain"
    assert category(lambda: issuer.recover("unknown")) == "superseded"
    assert len(minter.calls) == 1
    minter.cleared = True
    issuer.recover(operation_id)
    assert record(tmp_path)["outcome"] == "recovered"
    assert len(minter.calls) == 1, "recovery reconciles without minting"
    clock.advance(60)
    replacement = issuer.issue()
    assert replacement.operation_id != operation_id
    assert len(minter.calls) == 2


def test_concurrent_issuance_refuses_while_mint_in_flight(tmp_path: Path) -> None:
    issuer, minter, _, _, _ = setup(tmp_path)
    observed: list[str | None] = []
    def inspect() -> None:
        minter.observe = lambda: None
        observed.append(category(issuer.issue))
    minter.observe = inspect
    issuer.issue()
    assert observed == ["busy"], "a second attempt must refuse while the first owns the home lock"
    assert len(minter.calls) == 1


@pytest.mark.parametrize("case", ["directory-mode", "directory-alias", "journal-mode", "journal-fields", "binding", "pending"])
def test_unsafe_or_inconsistent_state_refuses_before_mint(tmp_path: Path, case: str) -> None:
    issuer, minter, _, _, clock = setup(tmp_path)
    issuer.issue()
    clock.advance(60)
    parent = tmp_path / "issuer"
    path = parent / "invite-issuer.json"
    if case == "directory-mode":
        parent.chmod(0o755)
    elif case == "directory-alias":
        alias = tmp_path / "alias"
        alias.symlink_to(parent, target_is_directory=True)
        issuer._state_dir = alias
    elif case == "journal-mode":
        path.chmod(0o644)
    elif case in ("journal-fields", "binding"):
        content = record(tmp_path)
        content["extra" if case == "journal-fields" else "binding_digest"] = "synthetic-foreign"
        path.write_text(json.dumps(content))
    else:
        pending = parent / "invite-issuer.pending"
        pending.write_text("synthetic-crash-residue")
        pending.chmod(0o600)
    assert category(issuer.issue) == "state", "unsafe metadata or mismatched state must never mint"
    assert len(minter.calls) == 1


@pytest.mark.parametrize("mode", ["own_server", "system"])
def test_home_only_configuration(tmp_path: Path, mode: str) -> None:
    from dataclasses import replace
    issuer, minter, _, _, _ = setup(tmp_path)
    issuer._config = replace(issuer._config, mode=mode)
    assert category(issuer.issue) == "configuration", "reserved own-server modes have no issuer intake"
    assert minter.calls == []


@pytest.mark.parametrize("failure", ["refused", "uncertain", "codec", "pairing", "journal"])
def test_redaction_across_diagnostics_and_persisted_bytes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str) -> None:
    from runtime.remote_access import invite_issuer
    issuer, minter, manager, _, clock = setup(tmp_path)
    first = issuer.issue()
    parsed = decode(first.invite_text, now=int(clock().timestamp()))
    canaries = (first.invite_text, minter.key, parsed.pair_secret)
    private = "|".join(canaries)
    for value in (first, MintedKey(minter.key, clock()), issuer):
        clean = not any(canary in str(value) + repr(value) for canary in canaries)
        assert clean, "public representations must redact transport fields"
    clock.advance(60)
    if failure in ("refused", "uncertain"):
        minter.behavior = failure
        minter.key = private
    else:
        def leak(*args: object, **kwargs: object) -> object:
            raise ValueError(private)
        if failure == "codec":
            encode = invite_issuer.invite_v2.encode
            def codec(**kwargs: object) -> object:
                if kwargs["join_key"] == "0" * 48:
                    return encode(**kwargs)
                return leak()
            monkeypatch.setattr(invite_issuer.invite_v2, "encode", codec)
        elif failure == "pairing":
            monkeypatch.setattr(manager, "issue_invite_record", leak)
        else:
            monkeypatch.setattr(invite_issuer.json, "loads", leak)
    captured = None
    try:
        issuer.issue()
    except IssuerError as error:
        captured = error
    assert captured is not None
    clean_chain = captured.__cause__ is None and captured.__context__ is None
    assert clean_chain, "no raw library chain may escape"
    display = str(captured) + repr(captured) + repr(captured.args) + repr(vars(captured))
    clean = not any(canary in display for canary in canaries)
    assert clean, "exception diagnostics must contain closed codes only"
    for path in (tmp_path / "issuer").iterdir():
        clean = not any(canary.encode() in path.read_bytes() for canary in canaries)
        assert clean, "journal and lock bytes must never persist transport secrets"


def observation_snapshot(root: Path) -> dict:
    """Exclude read access times; retain names, bytes and mutation identities."""
    import stat
    result = {}
    for path in [root, *sorted(root.rglob("*"))]:
        info = path.lstat()
        result[str(path.relative_to(root))] = (
            info.st_dev, info.st_ino, info.st_mode, info.st_nlink,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns,
            path.read_bytes() if stat.S_ISREG(info.st_mode) else None,
        )
    return result


def forbid_observation_effects(issuer: HomeInviteIssuer, patch: pytest.MonkeyPatch) -> None:
    import fcntl
    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("observation attempted an effect or lock acquisition")
    for owner, names in ((issuer, ("_write", "_invalidate")),
                         (issuer._minter, ("mint", "recover")),
                         (issuer._pairing, ("issue_invite_record",)),
                         (fcntl, ("flock",)),
                         (os, ("mkdir", "unlink", "rename", "replace"))):
        for name in names:
            patch.setattr(owner, name, forbidden)
    original_open = os.open
    def read_only_open(path: object, flags: int, *args: object, **kwargs: object) -> int:
        assert not flags & (os.O_CREAT | os.O_TRUNC | os.O_RDWR | os.O_WRONLY | os.O_APPEND), "observation opened a writer"
        return original_open(path, flags, *args, **kwargs)
    patch.setattr(os, "open", read_only_open)


def test_observe_absent_journal_is_frozen_and_creates_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from dataclasses import FrozenInstanceError, fields
    issuer, minter, _, _, _ = setup(tmp_path)
    before = observation_snapshot(tmp_path)
    with monkeypatch.context() as patch:
        forbid_observation_effects(issuer, patch)
        observed = issuer.observe()
    assert (observed.phase, observed.expires_at) == ("none", None)
    assert [item.name for item in fields(observed)] == ["phase", "expires_at"]
    with pytest.raises(FrozenInstanceError):
        observed.phase = "issued"
    assert observation_snapshot(tmp_path) == before
    assert not (tmp_path / "issuer" / "invite-issuer.lock").exists()
    assert minter.calls == minter.recoveries == []


@pytest.mark.parametrize("lifetime,seconds", [(500.0, 300), (20.6, 21)])
@pytest.mark.parametrize("moment", ["before", "deadline", "after"])
def test_observe_effective_deadline_and_expiry(tmp_path: Path, lifetime: float, seconds: int, moment: str) -> None:
    issuer, minter, _, _, clock = setup(tmp_path)
    minter.lifetime = lifetime
    issued = issuer.issue()
    deadline = int(START.timestamp()) + seconds
    clock.now = datetime.fromtimestamp(deadline, timezone.utc) + timedelta(
        microseconds={"before": -1, "deadline": 0, "after": 1}[moment])
    before = observation_snapshot(tmp_path)
    observed = issuer.observe()
    if moment == "before":
        assert observed.phase == "issued"
        assert type(observed.expires_at) is int
        assert observed.expires_at == issued.expires_at == deadline
    else:
        assert (observed.phase, observed.expires_at) == ("expired", None)
    assert observation_snapshot(tmp_path) == before
    assert record(tmp_path)["outcome"] == "issued", "expiry observation must not rewrite issuance"
    assert category(lambda: issuer.issue(issued.operation_id)) == "already_issued"
    assert len(minter.calls) == 1


@pytest.mark.parametrize("phase", ["refused", "expired", "recovered", "uncertain", "minting", "encoding", "pairing"])
def test_observe_nonissued_phase_survives_expiry(tmp_path: Path, phase: str) -> None:
    issuer, minter, _, _, clock = setup(tmp_path)
    if phase == "refused":
        minter.behavior = "refused"
        assert category(issuer.issue) == "refused"
    elif phase == "expired":
        minter.lifetime = 0.1
        assert category(issuer.issue) == "expired"
    else:
        minter.behavior = "uncertain"
        assert category(issuer.issue) == "uncertain"
        if phase == "recovered":
            minter.behavior, minter.cleared = "success", True
            issuer.recover(record(tmp_path)["operation_id"])
        elif phase != "minting":
            journal = record(tmp_path)
            journal["outcome"] = phase
            (tmp_path / "issuer" / "invite-issuer.json").write_text(json.dumps(journal))
    clock.advance(3600)
    before = observation_snapshot(tmp_path)
    observed = issuer.observe()
    assert (observed.phase, observed.expires_at) == (phase, None)
    assert observation_snapshot(tmp_path) == before
    assert len(minter.calls) == 1


@pytest.mark.parametrize("journal_present", [False, True])
def test_observe_without_lock_file_and_while_lock_held(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                      journal_present: bool) -> None:
    import fcntl
    issuer, _, _, _, _ = setup(tmp_path)
    if journal_present:
        issuer.issue()
        (tmp_path / "issuer" / "invite-issuer.lock").unlink()
    before = observation_snapshot(tmp_path)
    with monkeypatch.context() as patch:
        forbid_observation_effects(issuer, patch)
        observed = issuer.observe()
    assert observed.phase == ("issued" if journal_present else "none")
    assert observation_snapshot(tmp_path) == before
    lock_path = tmp_path / "issuer" / "invite-issuer.lock"
    assert not lock_path.exists()
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        before = observation_snapshot(tmp_path)
        with monkeypatch.context() as patch:
            forbid_observation_effects(issuer, patch)
            held = issuer.observe()
        assert held == observed
        assert observation_snapshot(tmp_path) == before
    finally:
        os.close(fd)


@pytest.mark.parametrize("case", ["binding", "malformed", "extra-field", "duplicate-field", "unknown-phase",
                                  "journal-mode", "journal-symlink", "journal-dangling", "journal-hardlink",
                                  "directory-mode", "directory-symlink", "directory-missing"])
def test_observe_invalid_state_is_closed_and_unchanged(tmp_path: Path, case: str) -> None:
    issuer, _, _, _, _ = setup(tmp_path)
    issuer.issue()
    parent = tmp_path / "issuer"
    path = parent / "invite-issuer.json"
    if case == "journal-mode":
        path.chmod(0o644)
    elif case.startswith("journal-"):
        target = parent / "saved.json"
        path.rename(target)
        if case == "journal-hardlink":
            os.link(target, path)
        else:
            path.symlink_to(target if case == "journal-symlink" else parent / "missing.json")
    elif case == "directory-mode":
        parent.chmod(0o755)
    elif case in ("directory-symlink", "directory-missing"):
        issuer._state_dir = tmp_path / "other"
        if case == "directory-symlink":
            issuer._state_dir.symlink_to(parent, target_is_directory=True)
    elif case == "malformed":
        path.write_text('{"synthetic-private-canary":')
    elif case == "duplicate-field":
        path.write_text(path.read_text()[:-1] + ',"outcome":"issued"}')
    else:
        journal = record(tmp_path)
        field = {"binding": "binding_digest", "extra-field": "extra", "unknown-phase": "outcome"}[case]
        journal[field] = "synthetic-private-canary"
        path.write_text(json.dumps(journal))
    before = observation_snapshot(tmp_path)
    with pytest.raises(IssuerError) as caught:
        issuer.observe()
    error = caught.value
    assert error.args == ("state",)
    assert error.__cause__ is error.__context__ is None
    assert "synthetic-private-canary" not in str(error) + repr(error) + repr(error.args)
    assert observation_snapshot(tmp_path) == before


@pytest.mark.parametrize("case,expected", [("mode", "configuration"), ("codec", "encoding"),
                                          ("naive-clock", "configuration"), ("clock-range", "configuration"),
                                          ("io", "state"), ("adapter", "uncertain")])
def test_observe_validation_and_exception_boundary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                  case: str, expected: str) -> None:
    from dataclasses import replace
    issuer, _, _, _, clock = setup(tmp_path)
    issuer.issue()
    if case == "mode":
        issuer._config = replace(issuer._config, mode="own_server")
    elif case == "codec":
        issuer._config = replace(issuer._config, dial_ip="synthetic-private-canary")
    elif case == "naive-clock":
        clock.now = START.replace(tzinfo=None)
    elif case == "clock-range":
        clock.now = datetime.max.replace(tzinfo=timezone.utc)
    else:
        def fault(*args: object) -> None:
            raise (OSError if case == "io" else RuntimeError)("synthetic-private-canary")
        monkeypatch.setattr(issuer, "_read", fault)
    before = observation_snapshot(tmp_path)
    with pytest.raises(IssuerError) as caught:
        issuer.observe()
    error = caught.value
    assert error.args == (expected,)
    assert error.__cause__ is error.__context__ is None
    assert "synthetic-private-canary" not in str(error) + repr(error) + repr(error.args)
    assert observation_snapshot(tmp_path) == before


@pytest.mark.parametrize("failure", [None, "malformed", "adapter"])
def test_observe_never_discloses_private_fields(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                               failure: str | None) -> None:
    from dataclasses import asdict, replace
    issuer, minter, _, _, clock = setup(tmp_path)
    minter.key = "c7032a2e17007abc" * 3
    issuer._config = replace(issuer._config, home_id="canary-home-17007", control_plane_id="canary-control-17007",
                             connector_id="canary-connector-17007")
    issued = issuer.issue()
    parsed = decode(issued.invite_text, now=int(clock().timestamp()))
    canaries = (issued.invite_text, minter.key, parsed.pair_secret,
                hashlib.sha256(parsed.pair_secret.encode()).hexdigest(), issued.operation_id,
                record(tmp_path)["binding_digest"], "canary-home-17007", "canary-control-17007", "canary-connector-17007")
    if failure == "malformed":
        (tmp_path / "issuer" / "invite-issuer.json").write_text(json.dumps({"private": "|".join(canaries)})[:-1])
    elif failure == "adapter":
        def leak(*args: object) -> None:
            raise RuntimeError("|".join(canaries))
        monkeypatch.setattr(issuer, "_read", leak)
    if failure is None:
        observed = issuer.observe()
        assert set(asdict(observed)) == {"phase", "expires_at"}
        display = str(observed) + repr(observed) + repr(asdict(observed))
    else:
        with pytest.raises(IssuerError) as caught:
            issuer.observe()
        error = caught.value
        assert error.args == ("state" if failure == "malformed" else "uncertain",)
        assert error.__cause__ is error.__context__ is None
        display = str(error) + repr(error) + repr(error.args) + repr(vars(error))
    clean = not any(canary in display for canary in canaries)
    assert clean, "observation results and diagnostics must contain phase/deadline or closed codes only"
