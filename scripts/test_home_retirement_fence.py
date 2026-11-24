"""Persistent retirement refusal, through real no-follow I/O and directory flock.

Run: uv run --frozen pytest scripts/test_home_retirement_fence.py -v --tb=short
No service, revocation, installed-upgrade or complete-retirement claim is made.
"""
from __future__ import annotations

import fcntl
import os
import stat
from pathlib import Path

import pytest

from runtime.remote_access.headscale_service import HeadscaleServiceError
from runtime.remote_access.home_retirement import RetirementUncertain
from runtime.remote_access.home_retirement_fence import retirement_fence
from runtime.remote_access.installer import InstallerError
from scripts.test_home_retirement import Fixture, STATE

MARKER = STATE + "/.home-activation.json.pending"
MARKER_BYTES = b'{"format":"home-retirement-fence@1"}\n'


@pytest.fixture
def fixture(tmp_path: Path) -> Fixture:
    value = Fixture(tmp_path / "root")
    value.directory(STATE)
    value.file(STATE + "/home-activation.json", b"synthetic-existing-private-receipt")
    value.file("home/operator/local-data", b"local-data-sentinel")
    return value


def test_fence_persists_and_blocks_the_historical_pending_reader(fixture: Fixture) -> None:
    before = fixture.snapshot()
    with fixture.io._publication_parent(STATE) as (parent, _):
        fixture.io._headscale_no_pending(parent, "home-activation.json")
    with retirement_fence(fixture.io) as fence:
        fence.require()
        with fixture.io._publication_parent(STATE) as (parent, _):
            with pytest.raises(HeadscaleServiceError, match="publication-uncertain"):
                fixture.io._headscale_no_pending(parent, "home-activation.json")
        marker = fixture.root / MARKER
        assert marker.read_bytes() == MARKER_BYTES
        info = marker.lstat()
        assert stat.S_ISREG(info.st_mode) and info.st_nlink == 1
        assert (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) == (os.getuid(), os.getgid(), 0o600)
        # The same original directory remains the serialization domain.
        with fixture.io._publication_parent(STATE) as (other, _):
            with pytest.raises(BlockingIOError):
                fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
    with pytest.raises(RetirementUncertain):
        fence.require()
    after = fixture.snapshot()
    assert set(after) - set(before) == {MARKER}
    for name in before.keys() - {STATE}:
        assert after[name] == before[name], name
    assert not fixture.calls
    # Fresh explicit recovery accepts only the real complete produced fence;
    # it neither resets the old reader nor recreates its inode.
    with retirement_fence(fixture.io) as recovered:
        recovered.require()
    assert fixture.snapshot() == after
    fixture.detect("migration pending")


@pytest.mark.parametrize("kind", ["foreign-bytes", "empty", "torn", "extra", "mode", "directory",
                                  "symlink", "hardlink", "fifo"])
def test_foreign_or_interrupted_pending_evidence_is_never_adopted(fixture: Fixture, kind: str) -> None:
    marker = fixture.root / MARKER
    if kind == "directory":
        marker.mkdir(mode=0o600)
    elif kind == "symlink":
        marker.symlink_to(fixture.root / "home/operator/local-data")
    elif kind == "fifo":
        os.mkfifo(marker, mode=0o600)
    else:
        data = {"foreign-bytes": b"synthetic-private-foreign", "empty": b"", "torn": b'{"format":',
                "extra": MARKER_BYTES + b"private-extra", "mode": MARKER_BYTES, "hardlink": MARKER_BYTES}[kind]
        fixture.file(MARKER, data, 0o644 if kind == "mode" else 0o600)
        if kind == "hardlink":
            os.link(marker, fixture.root / "outside-link")
    before = fixture.snapshot()
    with pytest.raises((RetirementUncertain, OSError)):
        with retirement_fence(fixture.io):
            pytest.fail("untrusted pending entry yielded an admission capability")
    assert fixture.snapshot() == before
    assert not fixture.calls


def test_contended_original_directory_lock_has_no_effects(fixture: Fixture) -> None:
    before = fixture.snapshot()
    with fixture.io._publication_parent(STATE) as (holder, _):
        fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(InstallerError, match="installer state unavailable"):
            with retirement_fence(fixture.io):
                pytest.fail("retirement passed the competing original directory lock")
        assert fixture.snapshot() == before
    assert not fixture.calls
    with retirement_fence(fixture.io) as fence:
        fence.require()
    assert (fixture.root / MARKER).read_bytes() == MARKER_BYTES


def test_fence_never_initializes_a_fresh_or_missing_state_root(tmp_path: Path) -> None:
    value = Fixture(tmp_path / "root")
    value.file("home/operator/local-data")
    before = value.snapshot()
    with pytest.raises(InstallerError):
        with retirement_fence(value.io):
            pytest.fail("explicit legacy fence initialized a fresh install")
    assert value.snapshot() == before
    assert not value.calls


@pytest.mark.parametrize("failure", ["write", "file-sync", "directory-sync"])
def test_interruption_retains_denial_without_granting_completion(
        fixture: Fixture, failure: str, monkeypatch: pytest.MonkeyPatch) -> None:
    before = fixture.snapshot()
    original_write, original_sync = os.write, os.fsync

    def write(fd: int, data: bytes) -> int:
        if failure == "write":
            assert original_write(fd, data[:4]) == 4
            raise OSError("synthetic-private-write-error")
        return original_write(fd, data)

    def sync(fd: int) -> None:
        directory = stat.S_ISDIR(os.fstat(fd).st_mode)
        if failure == ("directory-sync" if directory else "file-sync"):
            raise OSError("synthetic-private-sync-error")
        original_sync(fd)

    with monkeypatch.context() as patch:
        patch.setattr(os, "write", write)
        patch.setattr(os, "fsync", sync)
        with pytest.raises(InstallerError, match="^installer state unavailable$"):
            with retirement_fence(fixture.io):
                pytest.fail("interrupted fence publication yielded completion")
    marker = fixture.root / MARKER
    assert marker.exists()
    after = fixture.snapshot()
    assert set(after) - set(before) == {MARKER}
    for name in before.keys() - {STATE}:
        assert after[name] == before[name]
    assert not fixture.calls
    # The original pending reader refuses even a torn file; detection performs
    # no replay. Only a complete positively observed fence can recover.
    with fixture.io._publication_parent(STATE) as (parent, _):
        with pytest.raises(HeadscaleServiceError):
            fixture.io._headscale_no_pending(parent, "home-activation.json")
    fixture.detect("migration pending")
    if failure == "write":
        with pytest.raises(RetirementUncertain):
            with retirement_fence(fixture.io):
                pytest.fail("torn pending data was adopted")
    else:
        with retirement_fence(fixture.io) as fence:
            fence.require()
        assert fixture.snapshot() == after


@pytest.mark.parametrize("change", ["marker", "parent"])
def test_live_fence_refuses_replaced_member_or_serialization_inode(fixture: Fixture, change: str) -> None:
    with pytest.raises(RetirementUncertain):
        with retirement_fence(fixture.io) as fence:
            path = fixture.root / (MARKER if change == "marker" else STATE)
            path.rename(Path(str(path) + ".original"))
            if change == "marker":
                fixture.file(MARKER, MARKER_BYTES)
            else:
                fixture.directory(STATE)
                fixture.file(MARKER, MARKER_BYTES)
            before = fixture.snapshot()
            fence.require()
    assert fixture.snapshot() == before
    assert not fixture.calls
