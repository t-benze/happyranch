"""Org-portability capture + validation helpers (THR-187 Slice B).

Filesystem + SQLite mechanics shared by export (capture) and import (verify).
No HTTP, no daemon state: every function here operates on paths and, where
needed, opens its own short-lived ``sqlite3`` connection to the staged backup
file. This keeps the archive capture/verify logic testable in isolation while
the route layer owns bearer auth, the transfer fence, and the daemon seams.
"""
from __future__ import annotations

import hashlib
import shutil
import sqlite3
from pathlib import Path

from runtime.portability.archive import (
    ArchiveValidationError,
    B2CustomSkillCheck,
    LegacySkillEvidence,
    PAYLOAD_PREFIX,
    normalize_member_name,
    sha256_bytes,
    sha256_file,
)


class CaptureError(ValueError):
    """A capture or verification failure (fail-closed)."""


def compute_v2_fingerprint(conn: sqlite3.Connection) -> str:
    """Hash of the sorted (table, columns) schema shape.

    A current-v2 DB and an old-shape DB produce different fingerprints; import
    compares the manifest fingerprint against the staged DB and refuses old
    shapes. The fingerprint is schema-shape only (table + column names), not
    data.
    """
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    parts: list[str] = []
    for (name,) in rows:
        cols = conn.execute(f"PRAGMA table_info({name!r})").fetchall()
        col_names = [c[1] for c in cols]
        parts.append(f"{name}({','.join(col_names)})")
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def backup_sqlite(db, dest_path: Path) -> None:
    """Coordinated SQLite backup (never a raw -wal/-shm copy)."""
    db.backup_to(dest_path)


def verify_sqlite_integrity(db_path: Path) -> None:
    """Reject a corrupt or FK-inconsistent staged DB.

    Runs ``PRAGMA integrity_check`` (must be exactly ``ok``) and
    ``PRAGMA foreign_key_check`` (must return no rows). Fail-closed.
    """
    try:
        conn = sqlite3.connect(str(db_path))
    except sqlite3.Error as exc:
        raise CaptureError(f"cannot open sqlite db: {exc}") from exc
    try:
        conn.row_factory = sqlite3.Row
        try:
            integrity = conn.execute("PRAGMA integrity_check").fetchall()
        except sqlite3.Error as exc:
            raise CaptureError(f"sqlite integrity_check failed: {exc}") from exc
        if [row[0] for row in integrity] != ["ok"]:
            raise CaptureError(
                f"sqlite integrity_check failed: {[row[0] for row in integrity]}"
            )
        fk_rows = conn.execute("PRAGMA foreign_key_check").fetchall()
        if fk_rows:
            raise CaptureError(
                f"sqlite foreign_key_check found {len(fk_rows)} violation(s)"
            )
    finally:
        conn.close()


def deactivate_schedules(db_path: Path) -> int:
    """Force every schedule row's ``active`` flag to 0 in the staged DB.

    Returns the number of rows updated. This does not alter schedule *status*
    semantics or introduce an inactive status — Slice C alone owns attach /
    rebind / rearm. Leaves the staged DB with no WAL/SHM sidecars.
    """
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("PRAGMA journal_mode=DELETE")
        cur = conn.execute("UPDATE schedules SET active = 0 WHERE active != 0")
        conn.commit()
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        return cur.rowcount
    finally:
        conn.close()


def _iter_regular_files(src: Path, base: Path) -> "list[tuple[str, Path]]":
    """Recursively collect regular files under ``src`` (rel to ``base``).

    Rejects symlinks, hard links, and special files — a portable archive must
    never carry a link that could dereference outside the org root on import.
    """
    out: list[tuple[str, Path]] = []
    stack = [src]
    while stack:
        current = stack.pop()
        for child in sorted(current.iterdir(), key=lambda p: p.name):
            if child.is_symlink():
                raise CaptureError(f"symlink rejected during capture: {child}")
            if child.is_file():
                rel = child.relative_to(base).as_posix()
                out.append((rel, child))
            elif child.is_dir():
                stack.append(child)
            else:
                raise CaptureError(f"nonregular entry rejected during capture: {child}")
    return out


def collect_source_files(
    org_root: Path,
    included_paths: list[str],
) -> tuple[dict[str, Path], dict[str, int]]:
    """Capture the allow-listed source files.

    ``included_paths`` are the classifier's INCLUDE entry paths (direct-child
    relative paths, e.g. ``org``, ``skills/qa-scroll-test``,
    ``workspaces/dev_agent/memory``). Returns ``(payload, included_roots)``
    where ``payload`` maps archive member names to source files and
    ``included_roots`` maps top-level root name to file count.
    """
    payload: dict[str, Path] = {}
    counts: dict[str, int] = {}
    for rel in sorted(included_paths):
        src = org_root / rel
        if not src.exists():
            raise CaptureError(f"included root missing: {rel}")
        if src.is_symlink():
            raise CaptureError(f"symlink rejected during capture: {rel}")
        root_name = rel.split("/", 1)[0]
        if src.is_file():
            # happyranch.db is excluded here (handled by the SQLite backup);
            # any other allow-listed file root is captured as a single member.
            arcname = PAYLOAD_PREFIX + rel
            payload[arcname] = src
            counts[root_name] = counts.get(root_name, 0) + 1
            continue
        for file_rel, abs_path in _iter_regular_files(src, org_root):
            arcname = PAYLOAD_PREFIX + file_rel
            if arcname in payload:
                raise CaptureError(f"duplicate captured member: {file_rel}")
            payload[arcname] = abs_path
            counts[root_name] = counts.get(root_name, 0) + 1
    return payload, counts


def _read_db_rows(db_path: Path, query: str, params: tuple = ()) -> list[sqlite3.Row]:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(query, params).fetchall()
    finally:
        conn.close()


def verify_b2_custom_skills(
    db_path: Path, artifacts_root: Path,
) -> list[B2CustomSkillCheck]:
    """Cross-check every current B2 custom-skill version against its artifact.

    Reads ``custom_skills`` + ``custom_skill_versions`` from the staged DB and
    verifies the artifact bytes under ``artifacts_root`` hash-match the stored
    ``content_hash``. A missing artifact or hash mismatch is a fail-closed
    ``valid=False`` check (export refuses; import refuses before publish).
    Never copies or activates machine-global canonical-skill output.
    """
    rows = _read_db_rows(
        db_path,
        """SELECT s.id AS skill_id, s.slug, v.id AS version_id,
                  v.content_artifact_key, v.content_hash
             FROM custom_skills s
             JOIN custom_skill_versions v ON v.id = s.current_version_id
            ORDER BY s.slug""",
    )
    checks: list[B2CustomSkillCheck] = []
    for row in rows:
        key = row["content_artifact_key"]
        expected_hash = row["content_hash"]
        artifact_path = artifacts_root / key
        reason: str | None = None
        valid = False
        if not artifact_path.is_file() or artifact_path.is_symlink():
            reason = "artifact_missing"
        else:
            actual_hash = sha256_file(artifact_path)
            if actual_hash != expected_hash:
                reason = "artifact_hash_mismatch"
            else:
                valid = True
        checks.append(B2CustomSkillCheck(
            skill_id=row["skill_id"],
            slug=row["slug"],
            version_id=row["version_id"],
            content_artifact_key=key,
            content_hash=expected_hash,
            valid=valid,
            reason=reason,
        ))
    return checks


def _walk_regular_only(root: Path) -> list[tuple[str, Path]]:
    """Package-local regular-file walk (rel to ``root``), rejecting links."""
    out: list[tuple[str, Path]] = []
    for child in sorted(root.iterdir(), key=lambda p: p.name):
        if child.is_symlink():
            raise CaptureError(f"symlink rejected in legacy skill: {child}")
        if child.is_file():
            out.append((child.name, child))
        elif child.is_dir():
            for sub_rel, sub_path in _walk_regular_only(child):
                out.append((f"{child.name}/{sub_rel}", sub_path))
        else:
            raise CaptureError(f"nonregular entry in legacy skill: {child}")
    return out


def gather_legacy_skill_evidence(
    skills_root: Path, package_slugs: list[str],
) -> list[LegacySkillEvidence]:
    """Build per-package legacy-skill validation evidence (quarantined carry).

    ``package_slugs`` are the validated legacy package slugs (from the Slice-A
    classifier's INCLUDE entries under ``skills/``). Every member is hashed;
    local reference targets (``references/`` + ``assets/`` plain filenames) are
    resolved against the package's own member list. No package content is
    executed or materialized.
    """
    evidence: list[LegacySkillEvidence] = []
    for slug in sorted(package_slugs):
        pkg = skills_root / slug
        if not pkg.is_dir() or pkg.is_symlink():
            raise CaptureError(f"legacy skill package missing/unsafe: {slug}")
        member_hashes: dict[str, str] = {}
        for rel, abs_path in _walk_regular_only(pkg):
            member_hashes[rel] = sha256_file(abs_path)
        md_bytes = (pkg / "SKILL.md").read_bytes()
        yaml_bytes = (pkg / "skill.yaml").read_bytes()
        metadata_hash = sha256_bytes(yaml_bytes)
        content_hash = sha256_bytes(md_bytes)
        references_resolved = sorted(
            rel for rel in member_hashes
            if rel.startswith("references/") or rel.startswith("assets/")
        )
        evidence.append(LegacySkillEvidence(
            slug=slug,
            metadata_hash=metadata_hash,
            content_hash=content_hash,
            member_hashes=member_hashes,
            validation_result="valid",
            references_resolved=references_resolved,
        ))
    return evidence


def extract_archive(
    parsed, archive_path: Path, staging_dir: Path,
) -> list[str]:
    """Safely extract every payload member into ``staging_dir``.

    Does NOT use ``tarfile.extractall`` — each member is individually validated
    (already done by ``read_archive``) and its bytes written to a resolved path
    that is confirmed to stay under ``staging_dir``. Symlinks/hardlinks/devices
    were already rejected; here we re-confirm the resolved target. Returns the
    list of extracted relative member paths.
    """
    import tarfile

    staging_dir.mkdir(parents=True, exist_ok=True)
    resolved_root = staging_dir.resolve()
    extracted: list[str] = []
    with tarfile.open(archive_path, "r:gz") as tar:
        for member in tar.getmembers():
            name = normalize_member_name(member.name)
            if member.isdir():
                continue
            if name == "manifest.json":
                continue
            target = (staging_dir / name).resolve()
            if not target.is_relative_to(resolved_root):
                raise ArchiveValidationError(f"member escapes staging: {name!r}")
            fh = tar.extractfile(member)
            if fh is None:
                raise ArchiveValidationError(f"unreadable member: {name!r}")
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("wb") as out:
                shutil.copyfileobj(fh, out, length=1 << 20)
            extracted.append(name)
    return extracted
