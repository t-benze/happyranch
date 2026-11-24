"""Retained closed historical transaction parser and protected readback.

Run: uv run --frozen pytest scripts/test_home_activation_txn.py -q
"""
from __future__ import annotations

import json
import os
from types import SimpleNamespace
import pytest
from runtime.remote_access.legacy_home_receipt import HomeActivationTransaction, HomeActivationError
from runtime.remote_access.legacy_home_readback import LegacyHomeTransactions, TRANSACTION_PATH
from scripts.test_home_activation import receipt

ORDER = ("preflight", "headscale_start_verify", "db_users", "local_node_mint",
         "credential_staging", "home_config", "managed_services", "joined")
CONFIGS = ("/etc/happyranch/connector.json", "/etc/happyranch/sidecar.json")

@pytest.fixture
def venue():
    return SimpleNamespace(prepared=receipt())


@pytest.mark.parametrize("case", ["extra", "missing", "duplicate", "oversized", "bool", "uid",
                                 "operation", "order", "intent-order", "effect-extra", "path", "digest", "joined"])
def test_closed_transaction(venue: object, case: str) -> None:
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


@pytest.mark.parametrize("case", ["durable", "absent", "pending", "symlink", "hardlink", "mode", "parent"])
def test_legacy_transaction_readback(venue, tmp_path, case):
    value = HomeActivationTransaction.begin(venue.prepared)
    path = tmp_path / TRANSACTION_PATH.lstrip("/")
    path.parent.mkdir(parents=True, mode=0o700)
    for parent in (tmp_path / "var", tmp_path / "var/lib"):
        parent.chmod(0o755)
    if case != "absent":
        path.write_bytes(value.to_bytes())
        path.chmod(0o600)
    pending = path.with_name("." + path.name + ".pending")
    if case == "pending": pending.write_bytes(b"retained")
    if case in ("symlink", "hardlink"):
        other = tmp_path / "other"
        path.rename(other)
        if case == "symlink": path.symlink_to(other)
        else: os.link(other, path)
    if case == "mode": path.chmod(0o644)
    if case == "parent": path.parent.chmod(0o755)
    def inventory():
        return {str(p.relative_to(tmp_path)): (p.lstat(), p.read_bytes() if p.is_file() else None)
                for p in tmp_path.rglob("*")}
    # Warm directory access before snapshot; reads must never mutate inputs.
    inventory()
    before = inventory()
    store = LegacyHomeTransactions(root=tmp_path, root_owner=(os.getuid(), os.getgid()))
    if case in ("durable", "absent"):
        assert store.read_optional() == (value if case == "durable" else None)
    else:
        with pytest.raises(HomeActivationError): store.read_optional()
    assert inventory() == before
