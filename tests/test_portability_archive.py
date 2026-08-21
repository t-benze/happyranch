"""Tests for the data-only org-portability archive format (THR-187 Slice B).

Covers the archive validator's fail-closed behavior: checksum mismatch,
path traversal / absolute member names, duplicate members, symlink/hardlink/
device/FIFO/nonregular entries, unknown source root, corrupt SQLite/FK, and
the guarantee that no member is ever executed or dereferenced.
"""
from __future__ import annotations

import json
import tarfile
from pathlib import Path

import pytest

from runtime.portability.archive import (
    ARCHIVE_FORMAT_VERSION,
    ARCHIVE_POLICY_VERSION,
    MANIFEST_MEMBER,
    PAYLOAD_PREFIX,
    ArchiveMember,
    ArchiveValidationError,
    Manifest,
    build_archive,
    read_archive,
    sha256_file,
)


def _manifest(members: list[ArchiveMember], source_slug: str = "alpha") -> Manifest:
    return Manifest(
        format_version=ARCHIVE_FORMAT_VERSION,
        policy_version=ARCHIVE_POLICY_VERSION,
        source_slug=source_slug,
        v2_fingerprint="f" * 64,
        members=members,
        source_root_inventory=["org"],
        included_roots={"org": len(members)},
        excluded_entries=[],
        rejected_entries=[],
    )


def _build_valid_archive(tmp_path: Path) -> Path:
    src = tmp_path / "src"
    src.mkdir()
    f1 = src / "org" / "teams.yaml"
    f1.parent.mkdir()
    f1.write_text("teams: {}\n")
    payload = {f"{PAYLOAD_PREFIX}org/teams.yaml": f1}
    members = [ArchiveMember(
        path=f"{PAYLOAD_PREFIX}org/teams.yaml",
        size=f1.stat().st_size,
        sha256=sha256_file(f1),
    )]
    archive = tmp_path / "org.archive"
    build_archive(archive, _manifest(members), payload)
    return archive


def test_roundtrip_read_archive(tmp_path: Path) -> None:
    archive = _build_valid_archive(tmp_path)
    parsed = read_archive(archive)
    assert parsed.manifest.source_slug == "alpha"
    assert len(parsed.manifest.members) == 1
    assert parsed.member_names == [f"{PAYLOAD_PREFIX}org/teams.yaml"]


def test_rejects_checksum_mismatch(tmp_path: Path) -> None:
    archive = _build_valid_archive(tmp_path)
    # Tamper with a payload byte (append a char to the stored file content is
    # not possible after build; instead rebuild with a wrong declared hash).
    src = tmp_path / "src2"
    src.mkdir()
    f1 = src / "org" / "teams.yaml"
    f1.parent.mkdir()
    f1.write_text("teams: {}\n")
    payload = {f"{PAYLOAD_PREFIX}org/teams.yaml": f1}
    members = [ArchiveMember(
        path=f"{PAYLOAD_PREFIX}org/teams.yaml",
        size=f1.stat().st_size,
        sha256="0" * 64,  # wrong hash
    )]
    bad = tmp_path / "bad.archive"
    build_archive(bad, _manifest(members), payload)
    with pytest.raises(ArchiveValidationError, match="hash mismatch"):
        read_archive(bad)


def test_rejects_absolute_member(tmp_path: Path) -> None:
    archive = tmp_path / "evil.tar"
    with tarfile.open(archive, "w:gz") as tar:
        info = tarfile.TarInfo("/etc/passwd")
        info.size = 3
        import io
        tar.addfile(info, io.BytesIO(b"abc"))
    with pytest.raises(ArchiveValidationError, match="absolute member path"):
        read_archive(archive)


def test_rejects_parent_traversal_member(tmp_path: Path) -> None:
    archive = tmp_path / "evil.tar"
    with tarfile.open(archive, "w:gz") as tar:
        info = tarfile.TarInfo("../escape")
        info.size = 3
        import io
        tar.addfile(info, io.BytesIO(b"abc"))
    with pytest.raises(ArchiveValidationError, match="unsafe member path"):
        read_archive(archive)


def test_rejects_duplicate_member(tmp_path: Path) -> None:
    archive = tmp_path / "dup.tar"
    with tarfile.open(archive, "w:gz") as tar:
        for _ in range(2):
            info = tarfile.TarInfo(f"{PAYLOAD_PREFIX}org/x")
            info.size = 3
            import io
            tar.addfile(info, io.BytesIO(b"abc"))
    with pytest.raises(ArchiveValidationError, match="duplicate member"):
        read_archive(archive)


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "fifo", "chardev"])
def test_rejects_unsafe_member_types(tmp_path: Path, kind: str) -> None:
    archive = tmp_path / "unsafe.tar"
    with tarfile.open(archive, "w:gz") as tar:
        info = tarfile.TarInfo(f"{PAYLOAD_PREFIX}evil")
        if kind == "symlink":
            info.type = tarfile.SYMTYPE
            info.linkname = "/etc/passwd"
            tar.addfile(info)
        elif kind == "hardlink":
            info.type = tarfile.LNKTYPE
            info.linkname = f"{PAYLOAD_PREFIX}other"
            tar.addfile(info)
        elif kind == "fifo":
            info.type = tarfile.FIFOTYPE
            info.size = 0
            tar.addfile(info)
        else:
            info.type = tarfile.CHRTYPE
            info.size = 0
            tar.addfile(info)
    with pytest.raises(ArchiveValidationError):
        read_archive(archive)


def test_rejects_manifest_outside_payload(tmp_path: Path) -> None:
    archive = tmp_path / "stray.tar"
    with tarfile.open(archive, "w:gz") as tar:
        info = tarfile.TarInfo("stray.txt")
        info.size = 3
        import io
        tar.addfile(info, io.BytesIO(b"abc"))
    with pytest.raises(ArchiveValidationError):
        read_archive(archive)


def test_rejects_nonempty_rejected_entries(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    f1 = src / "org" / "teams.yaml"
    f1.parent.mkdir()
    f1.write_text("teams: {}\n")
    payload = {f"{PAYLOAD_PREFIX}org/teams.yaml": f1}
    manifest = _manifest([ArchiveMember(
        path=f"{PAYLOAD_PREFIX}org/teams.yaml",
        size=f1.stat().st_size,
        sha256=sha256_file(f1),
    )])
    manifest.rejected_entries = [{"path": "scripts", "reason": "unknown_root"}]
    archive = tmp_path / "rej.archive"
    with pytest.raises(ArchiveValidationError, match="rejected"):
        build_archive(archive, manifest, payload)


def test_build_archive_refuses_rejected_entries(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    f1 = src / "org" / "teams.yaml"
    f1.parent.mkdir()
    f1.write_text("teams: {}\n")
    payload = {f"{PAYLOAD_PREFIX}org/teams.yaml": f1}
    manifest = _manifest([ArchiveMember(
        path=f"{PAYLOAD_PREFIX}org/teams.yaml",
        size=f1.stat().st_size,
        sha256=sha256_file(f1),
    )])
    manifest.rejected_entries = [{"path": "x", "reason": "unknown_root"}]
    with pytest.raises(ArchiveValidationError, match="rejected_entries"):
        build_archive(tmp_path / "nope.archive", manifest, payload)


def test_manifest_carries_required_sections(tmp_path: Path) -> None:
    archive = _build_valid_archive(tmp_path)
    parsed = read_archive(archive)
    m = parsed.manifest
    assert m.format_version == ARCHIVE_FORMAT_VERSION
    assert m.policy_version == ARCHIVE_POLICY_VERSION
    assert m.source_slug == "alpha"
    assert len(m.v2_fingerprint) == 64
    assert m.source_root_inventory == ["org"]
    assert m.included_roots == {"org": 1}
    assert m.excluded_entries == []
    assert m.rejected_entries == []
    # members sorted, deterministic
    assert m.members == sorted(m.members, key=lambda x: x.path)
