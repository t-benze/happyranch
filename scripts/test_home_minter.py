"""Standalone fake-backed tests; no Headscale execution or host administration.

Fake projection: v0.25.1 proto/headscale/v1/preauthkey.proto:7-37.
NAME lookup/RPCs: hscontrol/grpcv1.go:139-223. Expire updates expiration to now;
create persists a 24-byte lowercase-hex key, used=false: db/preauth_keys.go:37-86,
155-172. All upstream paths are https://github.com/juanfont/headscale/blob/v0.25.1/.
"""
from __future__ import annotations

import ast
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from runtime.remote_access import home_minter
from runtime.remote_access.home_minter import (
    AdminRefused, CreationWindow, HeadscaleJoinKeyMinter, MintUncertain, PreAuthKey,
)
from runtime.remote_access.invite_issuer import JoinKeyMinter, MintRefused
from scripts.test_invite_issuer import category, record as issuer_record, setup as issuer_setup

START = datetime(2026, 11, 17, 13, 0, 0, 750000, timezone.utc)
USER = "synthetic-mac-invites"
KEY = "a1" * 24
CANARY = "SYNTHETIC-TRANSPORT-SECRET"


class Clock:
    def __init__(self) -> None:
        self.now = START

    def __call__(self) -> datetime:
        return self.now


class Store:
    def __init__(self, events: list) -> None:
        self.events = events
        self.windows: dict[str, CreationWindow] = {}
        self.error = False

    def reserve(self, operation_id: str, window: CreationWindow) -> bool:
        self.events.append(("reserve", operation_id, window))
        if self.error:
            raise RuntimeError(CANARY)
        if operation_id in self.windows:
            return False
        self.windows[operation_id] = window
        return True


def key_record(**changes: object) -> PreAuthKey:
    return replace(PreAuthKey(USER, "1", KEY, False, False, False,
                              START + timedelta(seconds=200), START, ()), **changes)


class Admin:
    def __init__(self, clock: Clock, events: list) -> None:
        self.clock, self.events = clock, events
        self.keys: tuple[PreAuthKey, ...] = ()
        self.created: dict = {}
        self.create_error: BaseException | None = None
        self.fail_at: int | None = None
        self.ignore_expire = False
        self.list_result: object | None = None
        self.calls = 0
        self.delay = 0

    def call(self, name: str, values: dict) -> None:
        self.calls += 1
        self.events.append((name, values))
        if self.calls == self.fail_at:
            raise TimeoutError(CANARY)

    def list_pre_auth_keys(self, **values: object) -> tuple[PreAuthKey, ...]:
        self.call("list", values)
        self.clock.now += timedelta(seconds=self.delay)
        return self.keys if self.list_result is None else self.list_result  # type: ignore[return-value]

    def expire_pre_auth_key(self, **values: object) -> None:
        self.call("expire", values)
        if not self.ignore_expire:
            self.keys = tuple(replace(k, expiration=self.clock()) if k.key == values["key"] else k
                              for k in self.keys)

    def create_pre_auth_key(self, **values: object) -> PreAuthKey:
        self.call("create", values)
        if isinstance(self.create_error, AdminRefused):
            raise self.create_error
        record = key_record(**({"user": values["user"], "reusable": values["reusable"],
                                "ephemeral": values["ephemeral"], "expiration": values["expiration"],
                                "id": str(len(self.keys) + 1), "created_at": self.clock(),
                                "key": KEY if not self.keys else "e5" * 24} | self.created))
        self.keys += (record,)
        if self.create_error is not None:
            raise self.create_error
        return record


def setup() -> tuple[HeadscaleJoinKeyMinter, Admin, Store, Clock, list]:
    events: list = []
    clock = Clock()
    store = Store(events)
    admin = Admin(clock, events)
    minter = HeadscaleJoinKeyMinter(admin=admin, invite_user=USER, now_fn=clock, records=store)
    return minter, admin, store, clock, events


def inert_history(count: int, *, used: bool = False) -> tuple[PreAuthKey, ...]:
    return tuple(key_record(id=str(i + 1), key=f"{i + 1:048x}", used=used,
                            expiration=START + timedelta(seconds=200) if used else START)
                 for i in range(count))


@pytest.mark.parametrize("used", [False, True], ids=["expired", "used-single-use"])
def test_inert_history_above_old_cap_still_mints(used: bool) -> None:
    minter, admin, store, _, events = setup()
    history = inert_history(1024, used=used)
    admin.keys = history + (key_record(id="live-unused", key="b2" * 24),
                            key_record(id="live-reusable", key="c3" * 24, used=True, reusable=True))
    minted = None
    try:
        minted = minter.mint("op", START + timedelta(seconds=300))
    except MintRefused:
        pass
    assert minted is not None, "inert retained history must not refuse a new mint"
    assert admin.keys[:1024] == history and "op" in store.windows
    assert [e[0] for e in events] == ["list", "expire", "expire", "list", "reserve", "create"]
    assert [e[1]["key"] for e in events if e[0] == "expire"] == ["b2" * 24, "c3" * 24]


@pytest.mark.parametrize("history_size", [128, 4096])
def test_lost_create_reply_large_history_recovers_real_issuer(tmp_path: Path, history_size: int) -> None:
    issuer, _, pairing, _, clock = issuer_setup(tmp_path)
    clock.now = START
    events: list = []
    store = Store(events)
    admin = Admin(clock, events)
    admin.keys = inert_history(history_size)
    admin.create_error = TimeoutError(CANARY)
    minter = HeadscaleJoinKeyMinter(admin=admin, invite_user=USER, now_fn=clock, records=store)
    # Reuse the real issuer/pairing journals and only replace its injected minter.
    issuer = type(issuer)(config=issuer._config, minter=minter, pairing=pairing,
                          state_dir=tmp_path / "issuer", now_fn=clock)
    assert category(issuer.issue) == "uncertain", "lost create reply must reach the real issuer journal"
    journal = issuer_record(tmp_path)
    operation = journal["operation_id"]
    assert journal["outcome"] == "minting" and operation in store.windows
    assert len(admin.keys) == history_size + 1
    assert category(lambda: issuer.recover(operation)) is None, "complete large history must permit recovery"
    assert issuer_record(tmp_path)["outcome"] == "recovered"
    assert len([e for e in events if e[0] == "create"]) == 1
    assert all(k.used and not k.reusable or k.expiration <= clock() for k in admin.keys)
    assert [e[0] for e in events][-3:] == ["list", "expire", "list"]
    assert category(lambda: issuer.issue(operation)) == "recovered"
    clock.advance(60)
    admin.create_error = None
    assert category(issuer.issue) is None
    assert len([e for e in events if e[0] == "create"]) == 2


@pytest.mark.parametrize("bad", ["user", "key", "used-type", "time", "duplicate-id", "unknown"])
def test_large_history_still_refuses_ambiguous_records(bad: str) -> None:
    minter, admin, store, _, events = setup()
    admin.keys = inert_history(1024)
    assert minter.recover("control") is True, "valid large history must first be recoverable"
    changes = {"user": {"user": "wrong"}, "key": {"key": "g" * 48},
               "used-type": {"used": 1}, "time": {"expiration": None},
               "duplicate-id": {"id": "1"}}
    malformed = object() if bad == "unknown" else key_record(id="bad", expiration=START)
    if bad != "unknown":
        malformed = replace(malformed, **changes[bad])
    # The bad row is inert-looking and late in a large otherwise valid list.
    admin.list_result = admin.keys + (malformed,)
    assert minter.recover("lost") is False
    with pytest.raises(MintRefused):
        minter.mint("op", START + timedelta(seconds=300))
    assert store.windows == {} and not any(e[0] in ("create", "expire") for e in events)


def test_inventory_ceiling_preserves_recovery_slot() -> None:
    minter, admin, store, _, events = setup()
    # Real configured ceiling, not a monkeypatched small substitute. More than
    # 366 * 24 * 60 mints covers a leap year at the issuer's maximum rate.
    admin.keys = inert_history(599999)
    minted = None
    try:
        minted = minter.mint("last-slot", START + timedelta(seconds=300))
    except MintRefused:
        pass
    assert minted is not None, "the last recovery-safe slot must permit creation"
    assert len(admin.keys) == 600000 and minter.recover("last-slot") is True
    with pytest.raises(MintRefused):
        minter.mint("no-room", START + timedelta(seconds=300))
    assert "no-room" not in store.windows
    admin.keys += (key_record(id="overflow", expiration=START),)
    assert minter.recover("last-slot") is False
    with pytest.raises(MintRefused):
        minter.mint("overflow", START + timedelta(seconds=300))
    assert len([e for e in events if e[0] == "create"]) == 1


def test_mint_sequence_and_server_expiry() -> None:
    minter, admin, store, _, events = setup()
    # Server may shorten the requested deadline; it is returned verbatim.
    server_expiry = START + timedelta(seconds=150)
    admin.created = {"expiration": server_expiry}
    deadline = START + timedelta(seconds=300)
    typed: JoinKeyMinter = minter
    minted = typed.mint("op", deadline)
    assert minted.key == KEY and minted.expires_at == server_expiry
    assert events == [
        ("list", {"user": USER, "timeout_s": 5.0}),
        ("list", {"user": USER, "timeout_s": 5.0}),
        ("reserve", "op", CreationWindow(START, deadline.replace(microsecond=0))),
        ("create", {"user": USER, "reusable": False, "ephemeral": False,
                    "expiration": deadline.replace(microsecond=0), "timeout_s": 5.0}),
    ]
    assert store.windows["op"].expires_at <= deadline


@pytest.mark.parametrize("deadline", [
    START, START - timedelta(seconds=1), START + timedelta(seconds=300, microseconds=1),
    START.replace(tzinfo=None), START.astimezone(timezone(timedelta(hours=8))),
    START.replace(microsecond=999999),
])
def test_invalid_deadline_zero_admin_calls(deadline: datetime) -> None:
    minter, admin, store, _, _ = setup()
    with pytest.raises(MintRefused):
        minter.mint("op", deadline)
    assert admin.calls == 0 and store.windows == {}


@pytest.mark.parametrize("changes", [
    {"expiration": START + timedelta(seconds=301)}, {"key": "A1" * 24},
    {"key": "a" * 47}, {"key": "g" * 48}, {"reusable": True}, {"ephemeral": True},
    {"used": True}, {"user": "other"}, {"reusable": 0}, {"id": ""},
    {"created_at": START.replace(tzinfo=None)}, {"expiration": START},
    {"acl_tags": ("tag:other",)},
])
def test_malformed_create_is_uncertain(changes: dict) -> None:
    minter, admin, store, _, events = setup()
    admin.created = changes
    with pytest.raises(MintUncertain):
        minter.mint("op", START + timedelta(seconds=300))
    assert len([e for e in events if e[0] == "create"]) == 1
    assert "op" in store.windows


@pytest.mark.parametrize("error", [TimeoutError(CANARY), RuntimeError(CANARY), MintRefused(CANARY)])
def test_transport_failure_uncertain_no_retry(error: BaseException) -> None:
    minter, admin, store, clock, events = setup()
    admin.create_error = error
    with pytest.raises(MintUncertain):
        minter.mint("op", START + timedelta(seconds=300))
    # Reconstruct with the same durable store: no second create for the ID.
    restarted = HeadscaleJoinKeyMinter(admin=admin, invite_user=USER, now_fn=clock, records=store)
    admin.create_error = None
    with pytest.raises(MintRefused):
        restarted.mint("op", START + timedelta(seconds=300))
    assert len([e for e in events if e[0] == "create"]) == 1


def test_prechange_refusal() -> None:
    minter, admin, _, _, _ = setup()
    admin.create_error = AdminRefused(CANARY)
    with pytest.raises(MintRefused):
        minter.mint("op", START + timedelta(seconds=300))
    assert admin.keys == ()


def test_cleanup_before_create() -> None:
    minter, admin, _, _, events = setup()
    admin.keys = (key_record(), key_record(id="2", key="b2" * 24),
                  key_record(id="3", key="c3" * 24, used=True),
                  key_record(id="4", key="d4" * 24, expiration=START))
    minter.mint("op", START + timedelta(seconds=300))
    assert [e[0] for e in events] == ["list", "expire", "expire", "list", "reserve", "create"]
    assert [e[1] for e in events if e[0] == "expire"] == [
        {"user": USER, "key": KEY, "timeout_s": 5.0},
        {"user": USER, "key": "b2" * 24, "timeout_s": 5.0},
    ]


@pytest.mark.parametrize("failure", ["list", "expire", "relist", "unchanged", "malformed", "oversized"])
def test_cleanup_failure_refuses_create(failure: str) -> None:
    minter, admin, _, _, events = setup()
    admin.keys = (key_record(),)
    admin.fail_at = {"list": 1, "expire": 2, "relist": 3}.get(failure)
    admin.ignore_expire = failure == "unchanged"
    if failure == "malformed":
        admin.list_result = (key_record(user="wrong"),)
    if failure == "oversized":
        admin.keys = inert_history(1024)
        assert minter.recover("control") is True, "inert rows do not consume the operational cap"
        admin.keys += tuple(key_record(id=f"live-{i}") for i in range(129))
    with pytest.raises(MintRefused):
        minter.mint("op", START + timedelta(seconds=300))
    assert not any(e[0] == "create" for e in events)


@pytest.mark.parametrize("failure", ["list", "expire", "relist", "unchanged", "shape", "time", "used-type"])
def test_recover_false_on_uncertainty(failure: str) -> None:
    minter, admin, _, _, _ = setup()
    admin.keys = (key_record(),)
    admin.fail_at = {"list": 1, "expire": 2, "relist": 3}.get(failure)
    admin.ignore_expire = failure == "unchanged"
    if failure == "shape":
        admin.list_result = [key_record()]
    if failure == "time":
        admin.list_result = (key_record(expiration=None),)
    if failure == "used-type":
        admin.list_result = (key_record(used=1),)
    assert minter.recover("unrecorded-op") is False


def test_recover_clean_relist_idempotent_and_by_time() -> None:
    minter, admin, _, clock, events = setup()
    admin.keys = (key_record(),)
    admin.fail_at = 2
    assert minter.recover("lost-op") is False
    clock.now = admin.keys[0].expiration
    admin.fail_at = None
    assert minter.recover("lost-op") is True
    assert minter.recover("lost-op") is True
    # No repeated expire once natural expiration makes the key unusable.
    assert [e[0] for e in events] == ["list", "expire", "list", "list", "list", "list"]
    admin.keys = (key_record(expiration=clock.now + timedelta(seconds=30)),)
    events.clear()
    assert minter.recover("another-op") is True
    assert [e[0] for e in events] == ["list", "expire", "list"]


def test_used_reusable_key_still_needs_expiration() -> None:
    minter, admin, _, _, events = setup()
    admin.keys = (key_record(used=True, reusable=True),)
    assert minter.recover("op") is True
    assert [e[0] for e in events] == ["list", "expire", "list"]


def test_store_failure_and_cleanup_delay_no_create() -> None:
    minter, admin, store, _, events = setup()
    store.error = True
    with pytest.raises(MintRefused):
        minter.mint("op", START + timedelta(seconds=300))
    store.error = False
    admin.delay = 151
    with pytest.raises(MintRefused):
        minter.mint("later", START + timedelta(seconds=300))
    assert not any(e[0] == "create" for e in events)


@pytest.mark.parametrize("error_type", [RuntimeError, TimeoutError, AdminRefused])
def test_secrets_absent_from_errors_repr_and_logs(error_type: type[Exception], caplog: pytest.LogCaptureFixture) -> None:
    minter, admin, _, _, _ = setup()
    admin.create_error = error_type(CANARY, KEY)
    with pytest.raises((MintUncertain, MintRefused)) as caught:
        minter.mint("op", START + timedelta(seconds=300))
    error = caught.value
    assert error.__cause__ is None and error.__context__ is None
    rendered = str(error) + repr(error) + repr(error.args) + repr(minter) + repr(key_record()) + caplog.text
    assert CANARY not in rendered and KEY not in rendered
    assert error.args == (("refused",) if error_type is AdminRefused else ("uncertain",))


def test_module_has_no_transport_or_environment_access() -> None:
    # Shape is the independent security contract: transport must stay injected.
    tree = ast.parse(Path(home_minter.__file__).read_text())
    imports = {alias.name.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.Import)
               for alias in node.names}
    imports |= {node.module.split(".")[0] for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom) and node.module}
    assert not imports & {"subprocess", "socket", "grpc", "os"}
    assert not any(isinstance(node, ast.Name) and node.id in {"__import__", "open", "eval", "exec"}
                   for node in ast.walk(tree))
