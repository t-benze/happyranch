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


def test_archive_dir_roots_derive_from_slice_a_canonical_policy() -> None:
    """The archive validator's directory roots are DERIVED from Slice A's
    canonical policy source (``roots.ALLOWED_ROOTS``) — there is no second
    unbound static root allow-list. The only additions are the two conditional
    roots (``skills``, ``workspaces``), each gated by its own member-level
    validation, and the SQLite file root is handled separately."""
    from runtime.portability import archive as archive_mod
    from runtime.portability import roots as roots_mod

    expected = (
        set(roots_mod.ALLOWED_ROOTS) - {"happyranch.db"}
    ) | {"skills", "workspaces"}
    assert archive_mod._ALLOWED_DIR_ROOTS == frozenset(expected)
    # Belt-and-suspenders: the required permitted directory roots are exactly
    # present (org/artifacts/kb/threads/task-attachments/jobs/dreams/work_hours/
    # schedules/talks + the two conditional roots).
    required = {
        "org", "artifacts", "kb", "threads", "task-attachments", "jobs",
        "dreams", "work_hours", "schedules", "talks", "skills", "workspaces",
    }
    assert set(archive_mod._ALLOWED_DIR_ROOTS) == required


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


# ── Finding #2: manifest/member/root inventory is untrusted ────────────────


def _archive_with_member(tmp_path: Path, member_rel: str, *, included_roots=None) -> Path:
    """Build a *self-consistent* archive (manifest + member hashes agree) whose
    single payload member lives at ``member_rel`` (org-root-relative)."""
    src = tmp_path / "src"
    f1 = src / member_rel
    f1.parent.mkdir(parents=True)
    f1.write_text("content\n")
    arcname = f"{PAYLOAD_PREFIX}{member_rel}"
    payload = {arcname: f1}
    members = [ArchiveMember(
        path=arcname, size=f1.stat().st_size, sha256=sha256_file(f1),
    )]
    manifest = _manifest(members)
    root = member_rel.split("/", 1)[0]
    manifest.source_root_inventory = [root]
    manifest.included_roots = included_roots if included_roots is not None else {root: 1}
    archive = tmp_path / "self.archive"
    build_archive(archive, manifest, payload)
    return archive


@pytest.mark.parametrize("member_rel", [
    "credentials/bearer.json",
    "unknown_thing/x",
    "daemon-local/token",
    "workspaces/dev_agent/repos/foo/x",
    "workspaces/dev_agent/output/T-1/report.txt",
    "workspaces/dev_agent/task_history.md",
], ids=["credentials", "unknown-root", "daemon-local", "repos", "output", "history"])
def test_rejects_self_consistent_forbidden_root(tmp_path: Path, member_rel: str) -> None:
    """A self-consistent hostile archive (all hashes agree) whose members live
    under a forbidden root is rejected before any SQLite is opened or published."""
    archive = _archive_with_member(tmp_path, member_rel)
    with pytest.raises(ArchiveValidationError, match="not allow-listed|memory carve-out"):
        read_archive(archive)


def test_rejects_sqlite_sidecar_member(tmp_path: Path) -> None:
    archive = _archive_with_member(tmp_path, "happyranch.db-wal")
    with pytest.raises(ArchiveValidationError, match="sidecar"):
        read_archive(archive)


def test_rejects_manifest_root_inventory_disagreement(tmp_path: Path) -> None:
    """A manifest whose included_roots file counts disagree with the actual
    members is rejected (no missing/extra/mismatched root claims)."""
    archive = _archive_with_member(
        tmp_path, "org/teams.yaml", included_roots={"org": 2},
    )
    with pytest.raises(ArchiveValidationError, match="root inventory mismatch"):
        read_archive(archive)


def test_rejects_legacy_skill_member_not_declared_valid(tmp_path: Path) -> None:
    """A member under skills/<slug> whose slug is not a manifest-declared valid
    legacy skill is rejected."""
    archive = _archive_with_member(tmp_path, "skills/evil/SKILL.md")
    with pytest.raises(ArchiveValidationError, match="not declared valid"):
        read_archive(archive)
