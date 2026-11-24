"""Retained token-source checks; neutral history, no activation owner.

Run: uv run --frozen pytest scripts/test_home_activation_owner.py -q
"""
from __future__ import annotations

from dataclasses import replace
import pytest
from runtime.remote_access import legacy_home_readback as h
from runtime.remote_access.legacy_home_receipt import ActivationRefused
from scripts.home_legacy_fixture import legacy_admission_fixture

@pytest.fixture
def venue(tmp_path, monkeypatch):
    return legacy_admission_fixture(tmp_path, monkeypatch, ready=False)


def test_source_verifier(venue):
    v = venue
    source = h.HomeTokenSourceVerifier(v.receipts._io)
    before = v.records()
    source.require(v.prepared)
    v.source.write_bytes(b"synthetic rotated token")
    with pytest.raises(ActivationRefused, match="^token_source_invalid$"):
        source.require(v.prepared)
    assert v.records() == before
    assert not v.native.calls


@pytest.mark.parametrize("case", ["mode", "symlink", "release-uid", "account"])
def test_source_custody(venue, monkeypatch, case):
    v = venue
    source = h.HomeTokenSourceVerifier(v.receipts._io)
    receipt = v.prepared
    if case == "mode": v.source.chmod(0o644)
    if case == "symlink":
        v.source.unlink()
        v.source.symlink_to(v.path("/etc/happyranch/daemon.token"))
    if case == "release-uid": receipt = replace(receipt, release_uid=receipt.release_uid + 1)
    if case == "account": monkeypatch.setattr(v.receipts._io, "_account", lambda **kw: None)
    with pytest.raises(ActivationRefused, match="^token_source_invalid$"):
        source.require(receipt)
    assert not v.native.calls
