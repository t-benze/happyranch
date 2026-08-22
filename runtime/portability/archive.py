"""Data-only versioned org-portability archive (THR-187 Slice B).

Standard-library only (``tarfile`` + ``hashlib`` + ``json`` + ``os`` +
``pathlib``). The archive is a plaintext, unsigned ``tar.gz`` with a leading
``manifest.json`` member and ``payload/...`` data members. Nothing inside the
archive is ever executed, ``import``-ed, or dereferenced: members are read as
opaque bytes and extracted only into a validated private staging directory.

Checksum semantics are kept separate from archive identity. Per-member SHA-256
hashes live in ``manifest.json`` (data integrity). The whole-archive digest is
the archive's *identity*: the exporter computes it after writing and returns it
in the export response; the inspector/importer computes it from the file bytes
and records it in the receipt. It is never recursively claimed inside the
archive's own bytes (that would be self-referential).
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import tarfile
from pathlib import Path

from pydantic import BaseModel, Field

from runtime.portability.roots import ALLOWED_ROOTS as _CANONICAL_ALLOWED_ROOTS

ARCHIVE_FORMAT_VERSION = 1
ARCHIVE_POLICY_VERSION = 1
MANIFEST_MEMBER = "manifest.json"
PAYLOAD_PREFIX = "payload/"
_MAX_MEMBER_PATH = 4096

# Directory roots admitted by the archive validator are DERIVED from Slice A's
# canonical policy source (``roots.ALLOWED_ROOTS``) rather than re-declared as a
# second unbound static allow-list. The derivation is: every canonical
# allow-listed root except the SQLite file itself (``happyranch.db`` is handled
# as the sole file root and backed up separately), plus the two *conditional*
# roots — ``skills`` and ``workspaces`` — which Slice A admits only under their
# own validation gates. Each conditional root is gated again at member level in
# ``validate_member_roots`` (declared-valid legacy skill / memory carve-out).
_ALLOWED_DIR_ROOTS = frozenset(
    (set(_CANONICAL_ALLOWED_ROOTS) - {"happyranch.db"}) | {"skills", "workspaces"}
)
_MEMORY_DIR_NAME = "memory"
_SQLITE_SIDECAR_SUFFIXES = ("-wal", "-shm")


class ArchiveValidationError(ValueError):
    """An archive member or manifest failed validation (fail-closed)."""


# ── Manifest models ─────────────────────────────────────────────────────────


class ArchiveMember(BaseModel):
    path: str
    size: int
    sha256: str


class LegacySkillEvidence(BaseModel):
    """Per-package legacy-skill validation evidence (quarantined, never active)."""

    slug: str
    metadata_hash: str
    content_hash: str
    member_hashes: dict[str, str]
    validation_result: str  # "valid" or a short failure reason
    references_resolved: list[str] = Field(default_factory=list)


class B2CustomSkillCheck(BaseModel):
    """Cross-check of one current B2 custom-skill version against its artifact."""

    skill_id: str
    slug: str
    version_id: int | None = None
    content_artifact_key: str
    content_hash: str
    valid: bool
    reason: str | None = None


class Manifest(BaseModel):
    format_version: int
    policy_version: int
    source_slug: str
    v2_fingerprint: str
    members: list[ArchiveMember]
    source_root_inventory: list[str]
    included_roots: dict[str, int]
    excluded_entries: list[dict]
    rejected_entries: list[dict]
    legacy_skills: list[LegacySkillEvidence] = Field(default_factory=list)
    b2_custom_skill_checks: list[B2CustomSkillCheck] = Field(default_factory=list)


class ParsedArchive(BaseModel):
    manifest: Manifest
    digest: str
    member_names: list[str]


# ── Hashing + pathname safety ───────────────────────────────────────────────


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def normalize_member_name(name: str) -> str:
    """Normalize a relative POSIX member name and reject unsafe forms.

    Rejects absolute paths, ``..`` segments, backslashes, empty segments, NUL,
    and the manifest reserved name (a payload member must not shadow it).
    """
    if not isinstance(name, str) or not name:
        raise ArchiveValidationError(f"empty member name")
    if len(name) > _MAX_MEMBER_PATH:
        raise ArchiveValidationError(f"member path too long: {name!r}")
    if "\x00" in name:
        raise ArchiveValidationError(f"member path contains NUL: {name!r}")
    if "\\" in name:
        raise ArchiveValidationError(f"member path contains backslash: {name!r}")
    if name.startswith("/"):
        raise ArchiveValidationError(f"absolute member path: {name!r}")
    # Collapse a leading './' that tar may introduce, then re-check.
    while name.startswith("./"):
        name = name[2:]
    parts = name.split("/")
    if any(part == "" or part == "." or part == ".." for part in parts):
        raise ArchiveValidationError(f"unsafe member path: {name!r}")
    return name


def _payload_arcname(rel: str) -> str:
    """Map an org-root-relative path to its archive member name."""
    normalized = normalize_member_name(rel)
    return f"{PAYLOAD_PREFIX}{normalized}"


# ── Build ───────────────────────────────────────────────────────────────────


def build_archive(
    dest_path: Path,
    manifest: Manifest,
    payload: dict[str, Path],
) -> str:
    """Write a deterministic data-only archive and return its whole-file digest.

    ``payload`` maps archive member name (already ``payload/...``) to the source
    file. Member order is deterministic (``manifest.json`` first, then payload
    members sorted by name). Directory members are synthesized implicitly by
    the reader; only regular files are emitted here. Returns the SHA-256 of the
    resulting archive file bytes (the archive identity).
    """
    if manifest.rejected_entries:
        raise ArchiveValidationError(
            "cannot build an archive with a nonempty rejected_entries set"
        )
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = dest_path.with_name(dest_path.name + ".tmp")
    try:
        with tarfile.open(tmp_path, "w:gz", format=tarfile.PAX_FORMAT) as tar:
            manifest_bytes = json.dumps(
                manifest.model_dump(), sort_keys=True, indent=2,
            ).encode("utf-8")
            info = tarfile.TarInfo(MANIFEST_MEMBER)
            info.size = len(manifest_bytes)
            info.mode = 0o644
            info.mtime = 0
            tar.addfile(info, io.BytesIO(manifest_bytes))

            for arcname in sorted(payload):
                src = payload[arcname]
                info = tar.gettarinfo(str(src), arcname=arcname)
                info.mtime = 0
                info.uid = 0
                info.gid = 0
                info.uname = ""
                info.gname = ""
                with src.open("rb") as fh:
                    tar.addfile(info, fh)
        os.replace(tmp_path, dest_path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise
    return sha256_file(dest_path)


# ── Read / validate ─────────────────────────────────────────────────────────


def read_archive(archive_path: Path) -> ParsedArchive:
    """Open and fully validate an archive; return its manifest + digest.

    Validation (fail-closed): every member is a regular file or directory;
    symlinks/hardlinks/devices/FIFOs/nonregular entries are rejected; every name
    is a normalized relative POSIX path (no absolute, no ``..``); duplicate
    names are rejected; the manifest parses and every declared member exists
    with the exact size and SHA-256. Returns the whole-archive digest (identity).
    """
    if not archive_path.is_file():
        raise ArchiveValidationError(f"archive does not exist: {archive_path}")
    digest = sha256_file(archive_path)
    seen: set[str] = set()
    member_map: dict[str, tuple[int, str]] = {}
    manifest_bytes: bytes | None = None

    try:
        tar = tarfile.open(archive_path, "r:gz")
    except tarfile.TarError as exc:
        raise ArchiveValidationError(f"cannot open archive: {exc}") from exc

    with tar:
        for member in tar.getmembers():
            name = member.name
            # Reject the unsafe classes BEFORE any name normalization, so a
            # symlink with a hostile name cannot slip past on a name check.
            if member.issym() or member.islnk():
                raise ArchiveValidationError(f"symlink/hardlink member rejected: {name!r}")
            if member.ischr() or member.isblk() or member.isfifo() or member.isdev():
                raise ArchiveValidationError(f"device/FIFO member rejected: {name!r}")
            normalized = normalize_member_name(name)
            if normalized in seen:
                raise ArchiveValidationError(f"duplicate member name: {normalized!r}")
            seen.add(normalized)
            if member.isdir():
                continue
            if not member.isfile():
                raise ArchiveValidationError(f"non-regular member rejected: {normalized!r}")
            if normalized == MANIFEST_MEMBER:
                fh = tar.extractfile(member)
                manifest_bytes = fh.read() if fh is not None else None
                continue
            if not normalized.startswith(PAYLOAD_PREFIX):
                raise ArchiveValidationError(
                    f"member outside payload/ rejected: {normalized!r}"
                )
            fh = tar.extractfile(member)
            if fh is None:
                raise ArchiveValidationError(f"unreadable member: {normalized!r}")
            h = hashlib.sha256()
            size = 0
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
                size += len(chunk)
            member_map[normalized] = (size, h.hexdigest())

    if manifest_bytes is None:
        raise ArchiveValidationError("archive is missing manifest.json")

    try:
        manifest = Manifest.model_validate(json.loads(manifest_bytes.decode("utf-8")))
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ArchiveValidationError(f"invalid manifest: {exc}") from exc

    if manifest.format_version != ARCHIVE_FORMAT_VERSION:
        raise ArchiveValidationError(
            f"unsupported archive format_version {manifest.format_version}"
        )
    if manifest.policy_version != ARCHIVE_POLICY_VERSION:
        raise ArchiveValidationError(
            f"unsupported archive policy_version {manifest.policy_version}"
        )
    if manifest.rejected_entries:
        raise ArchiveValidationError(
            "archive manifest has a nonempty rejected_entries set"
        )

    # Every declared member must exist with exact size + hash; every on-disk
    # payload member must be declared (no hidden members).
    declared = {m.path: m for m in manifest.members}
    if set(declared) != set(member_map):
        missing = sorted(set(declared) - set(member_map))
        extra = sorted(set(member_map) - set(declared))
        raise ArchiveValidationError(
            f"member inventory mismatch: missing={missing} extra={extra}"
        )
    for path, member in sorted(declared.items()):
        size, digest_val = member_map[path]
        if member.size != size:
            raise ArchiveValidationError(
                f"size mismatch for {path}: manifest={member.size} actual={size}"
            )
        if member.sha256 != digest_val:
            raise ArchiveValidationError(f"hash mismatch for member {path!r}")

    # Enforce the exact allow-listed root contract BEFORE any caller opens the
    # staged SQLite or publishes bytes. This rejects a *self-consistent*
    # hostile archive (manifest + member hashes all agree) whose members live
    # under a forbidden root (credentials, unknown roots, task output,
    # workspace siblings/repos, daemon-local data) and a manifest whose root
    # inventory disagrees with the actual member set.
    validate_member_roots(manifest, sorted(member_map))

    return ParsedArchive(
        manifest=manifest,
        digest=digest,
        member_names=sorted(member_map),
    )


def validate_member_roots(manifest: Manifest, member_names: list[str]) -> None:
    """Enforce the exact allow-listed root contract on the archive members.

    Called by :func:`read_archive` so inspection and import both reject a
    self-consistent-but-forbidden archive before any SQLite is opened or any
    byte is published. Every payload member must live under an approved root
    (``happyranch.db`` or one of the directory roots); ``workspaces`` members
    must be under the sole ``workspaces/<agent>/memory/**`` carve-out; and
    ``skills`` members must belong to a manifest-declared *valid* legacy skill.
    SQLite WAL/SHM sidecars are refused. Finally the manifest's
    ``included_roots`` file counts must agree exactly with the actual member
    counts (no missing/extra root claims).
    """
    skill_slugs = {e.slug for e in manifest.legacy_skills}
    actual_counts: dict[str, int] = {}
    for name in member_names:
        rel = name[len(PAYLOAD_PREFIX):] if name.startswith(PAYLOAD_PREFIX) else name
        if rel == "happyranch.db":
            actual_counts["happyranch.db"] = actual_counts.get("happyranch.db", 0) + 1
            continue
        if rel.endswith(_SQLITE_SIDECAR_SUFFIXES[0]) or rel.endswith(_SQLITE_SIDECAR_SUFFIXES[1]):
            raise ArchiveValidationError(f"sqlite sidecar member rejected: {name!r}")
        root = rel.split("/", 1)[0]
        if root not in _ALLOWED_DIR_ROOTS:
            raise ArchiveValidationError(f"member root not allow-listed: {name!r}")
        if root == "workspaces":
            parts = rel.split("/")
            if len(parts) < 3 or parts[2] != _MEMORY_DIR_NAME:
                raise ArchiveValidationError(
                    f"workspace member outside memory carve-out: {name!r}"
                )
        if root == "skills":
            parts = rel.split("/")
            if len(parts) < 2 or parts[1] not in skill_slugs:
                raise ArchiveValidationError(
                    f"legacy skill member not declared valid: {name!r}"
                )
        actual_counts[root] = actual_counts.get(root, 0) + 1

    claimed = dict(manifest.included_roots)
    if claimed != actual_counts:
        raise ArchiveValidationError(
            f"root inventory mismatch: manifest claimed "
            f"{sorted(claimed.items())}, actual {sorted(actual_counts.items())}"
        )
