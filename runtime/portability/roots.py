"""Pure fail-closed direct-org-root inventory/classifier (THR-187 Slice A).

For every direct child of a source organization root this module returns
exactly one of:

* ``include``            — an allow-listed portable root (whole-tree, except the
                           `workspaces/*/memory/**` carve-out which is the sole
                           workspace exception);
* ``exclude(<reason>)``  — a *named* exclusion (generated marker, derived
                           projection, SQLite sidecar, cache, zero-byte legacy
                           residue, non-memory workspace data, task output);
* ``reject(<reason>)``   — unknown, nonregular/unsafe, nonzero legacy residue,
                           or invalid legacy-skill roots.

There is no fall-through and no default/recursive-copy behavior: a root that is
not explicitly allow-listed and not explicitly excluded is rejected. This
fail-closed rule protects future durable roots from being silently lost.

This module is pure: it imports no daemon/DB state and reads only the filesystem.
"""
from __future__ import annotations

from enum import StrEnum
from pathlib import Path

import yaml
from pydantic import BaseModel


class RootClassification(StrEnum):
    INCLUDE = "include"
    EXCLUDE = "exclude"
    REJECT = "reject"


# ── Named exclusion reasons ──────────────────────────────────────────────────
EXCLUDE_DERIVED_PROJECTION = "derived_projection"
EXCLUDE_GENERATED_MARKER = "generated_marker"
EXCLUDE_SQLITE_SIDECAR = "sqlite_sidecar"
EXCLUDE_CACHE = "cache"
EXCLUDE_ZERO_BYTE_LEGACY_RESIDUE = "zero_byte_legacy_residue"
EXCLUDE_WORKSPACE_NON_MEMORY = "workspace_non_memory"
EXCLUDE_TASK_OUTPUT = "task_output"

# ── Reject reasons ───────────────────────────────────────────────────────────
REJECT_UNKNOWN_ROOT = "unknown_root"
REJECT_NONREGULAR = "nonregular"
REJECT_NONZERO_LEGACY_RESIDUE = "nonzero_legacy_residue"
REJECT_INVALID_SKILL = "invalid_skill"


# Allow-listed whole-tree portable roots (when present). ``happyranch.db`` is a
# regular file; every other member here is a directory.
ALLOWED_ROOTS = frozenset({
    "happyranch.db",
    "org",
    "artifacts",
    "kb",
    "threads",
    "task-attachments",
    "jobs",
    "dreams",
    "work_hours",
    "schedules",
    "talks",
})

# Generated sentinel markers (created by one-shot migrations).
GENERATED_MARKERS = frozenset({".hr_review_renamed", ".org_settings_seeded"})

# Derived dashboard projection — rebuilt on the target.
DERIVED_PROJECTIONS = frozenset({"dashboard_projection.json"})

# SQLite WAL/SHM sidecars — never carried; a coordinated backup is used instead.
SQLITE_SIDECARS = frozenset({"happyranch.db-wal", "happyranch.db-shm"})

# OS / tooling caches.
CACHE_ENTRIES = frozenset({".pytest_cache", ".DS_Store"})

# Historical DB residue. Zero-byte + unreferenced is a silent exclusion; anything
# else (nonempty, directory, or nonregular) is a rejection.
LEGACY_RESIDUE_DB = frozenset({"audit.db", "db.sqlite3"})

# The sole workspace carve-out directory name.
_MEMORY_DIR_NAME = "memory"


class ClassifiedEntry(BaseModel):
    path: str
    classification: RootClassification
    reason: str | None = None


class RootInventory(BaseModel):
    org_root: str
    entries: list[ClassifiedEntry]

    @property
    def included(self) -> list[ClassifiedEntry]:
        return [e for e in self.entries if e.classification == RootClassification.INCLUDE]

    @property
    def excluded(self) -> list[ClassifiedEntry]:
        return [e for e in self.entries if e.classification == RootClassification.EXCLUDE]

    @property
    def rejected(self) -> list[ClassifiedEntry]:
        return [e for e in self.entries if e.classification == RootClassification.REJECT]

    @property
    def has_rejections(self) -> bool:
        return bool(self.rejected)


def _is_regular_entry(path: Path) -> bool:
    """Regular file or directory only — symlinks and special files are unsafe."""
    if path.is_symlink():
        return False
    return path.is_file() or path.is_dir()


def classify_root_entries(org_root: Path) -> RootInventory:
    """Classify every direct child of ``org_root`` exactly once.

    Raises ``ValueError`` if ``org_root`` is not a directory. Returns a
    ``RootInventory`` whose ``rejected`` set is nonempty whenever any child is
    unknown, nonregular, a nonzero legacy-residue DB, or an invalid legacy
    skill package.
    """
    if not org_root.is_dir():
        raise ValueError(f"org root is not a directory: {org_root}")
    entries: list[ClassifiedEntry] = []
    for child in sorted(org_root.iterdir(), key=lambda p: p.name):
        entries.extend(_classify_child(child))
    return RootInventory(org_root=str(org_root), entries=entries)


def _classify_child(child: Path) -> list[ClassifiedEntry]:
    name = child.name
    rel = name  # direct child — relative path is just the name

    if not _is_regular_entry(child):
        return [ClassifiedEntry(path=rel, classification=RootClassification.REJECT,
                                reason=REJECT_NONREGULAR)]

    if name in ALLOWED_ROOTS:
        # happyranch.db is a regular file; every other allowed root is a dir.
        if name == "happyranch.db":
            if not child.is_file():
                return [ClassifiedEntry(path=rel, classification=RootClassification.REJECT,
                                        reason=REJECT_NONREGULAR)]
        elif not child.is_dir():
            return [ClassifiedEntry(path=rel, classification=RootClassification.REJECT,
                                    reason=REJECT_NONREGULAR)]
        return [ClassifiedEntry(path=rel, classification=RootClassification.INCLUDE)]

    if name in GENERATED_MARKERS:
        return [ClassifiedEntry(path=rel, classification=RootClassification.EXCLUDE,
                                reason=EXCLUDE_GENERATED_MARKER)]
    if name in DERIVED_PROJECTIONS:
        return [ClassifiedEntry(path=rel, classification=RootClassification.EXCLUDE,
                                reason=EXCLUDE_DERIVED_PROJECTION)]
    if name in SQLITE_SIDECARS:
        return [ClassifiedEntry(path=rel, classification=RootClassification.EXCLUDE,
                                reason=EXCLUDE_SQLITE_SIDECAR)]
    if name in CACHE_ENTRIES:
        return [ClassifiedEntry(path=rel, classification=RootClassification.EXCLUDE,
                                reason=EXCLUDE_CACHE)]
    if name in LEGACY_RESIDUE_DB:
        if child.is_file() and child.stat().st_size == 0:
            return [ClassifiedEntry(path=rel, classification=RootClassification.EXCLUDE,
                                    reason=EXCLUDE_ZERO_BYTE_LEGACY_RESIDUE)]
        return [ClassifiedEntry(path=rel, classification=RootClassification.REJECT,
                                reason=REJECT_NONZERO_LEGACY_RESIDUE)]
    if name == "skills":
        return _classify_skills(child)
    if name == "workspaces":
        return _classify_workspaces(child)

    return [ClassifiedEntry(path=rel, classification=RootClassification.REJECT,
                            reason=REJECT_UNKNOWN_ROOT)]


# ── legacy skills (conditional allow) ────────────────────────────────────────

def _classify_skills(skills_dir: Path) -> list[ClassifiedEntry]:
    if not skills_dir.is_dir():
        return [ClassifiedEntry(path="skills", classification=RootClassification.REJECT,
                                reason=REJECT_NONREGULAR)]
    children = sorted(skills_dir.iterdir(), key=lambda p: p.name)
    if not children:
        # Present but empty — recognized, portable-as-empty.
        return [ClassifiedEntry(path="skills", classification=RootClassification.INCLUDE)]
    entries: list[ClassifiedEntry] = []
    for pkg in children:
        rel = f"skills/{pkg.name}"
        if not pkg.is_dir() or pkg.is_symlink():
            entries.append(ClassifiedEntry(path=rel, classification=RootClassification.REJECT,
                                           reason=REJECT_INVALID_SKILL))
            continue
        ok, _reason = _validate_legacy_skill_package(pkg)
        if ok:
            entries.append(ClassifiedEntry(path=rel, classification=RootClassification.INCLUDE))
        else:
            entries.append(ClassifiedEntry(path=rel, classification=RootClassification.REJECT,
                                           reason=REJECT_INVALID_SKILL))
    return entries


def _validate_legacy_skill_package(pkg_dir: Path) -> tuple[bool, str | None]:
    """Structural validity of a legacy user-authored skill package.

    Returns ``(ok, reason)``. This is the Slice-A classification gate: it
    checks identity/metadata conformance and package member shape only. Full
    reference resolution and hash matching are import-time (Slice B) concerns.
    """
    try:
        skill_yaml_path = pkg_dir / "skill.yaml"
        skill_md_path = pkg_dir / "SKILL.md"
        # Fail closed: Path.is_file() follows symlinks, so a symlinked required
        # member would be read and accepted. Reject symlinks explicitly BEFORE
        # any read/parse so no content outside the org root can be certified
        # portable.
        if skill_yaml_path.is_symlink() or skill_md_path.is_symlink():
            return False, "skill.yaml or SKILL.md is a symlink"
        if not skill_yaml_path.is_file() or not skill_md_path.is_file():
            return False, "missing skill.yaml or SKILL.md"
        meta = yaml.safe_load(skill_yaml_path.read_text(encoding="utf-8")) or {}
        if not isinstance(meta, dict):
            return False, "skill.yaml is not a mapping"
        slug = meta.get("slug")
        if not isinstance(slug, str) or not slug or slug != pkg_dir.name:
            return False, "skill.yaml slug does not match package directory name"
        if meta.get("id") != f"hr:{slug}":
            return False, "skill.yaml id is not hr:<slug>"
        if meta.get("source") != "user_authored":
            return False, "skill.yaml source is not user_authored"
        if meta.get("policy_class") == "system_contract":
            return False, "system_contract legacy skill is not portable"
        for required in ("id", "slug", "name", "version"):
            value = meta.get(required)
            if not isinstance(value, str) or not value.strip():
                return False, f"skill.yaml missing nonempty {required}"

        md_text = skill_md_path.read_text(encoding="utf-8")
        stripped = md_text.lstrip()
        if not stripped:
            return False, "SKILL.md is empty"
        first_line = stripped.splitlines()[0]
        if not first_line.startswith("#"):
            return False, "SKILL.md must begin with a heading"

        # Only SKILL.md, skill.yaml, and optional references/ + assets/ dirs.
        allowed = {"SKILL.md", "skill.yaml", "references", "assets"}
        for member in pkg_dir.iterdir():
            if member.name in allowed:
                continue
            if member.name.startswith("."):
                return False, f"hidden member {member.name!r}"
            return False, f"unrecognized package member {member.name!r}"

        # references/ and assets/ hold regular files only — no symlinks, no
        # nested trees, no special files. Check is_symlink() BEFORE exists():
        # Path.exists() is False for a dangling symlink, so a dangling directory
        # link would otherwise be treated as absent and the package accepted.
        for sub in ("references", "assets"):
            sp = pkg_dir / sub
            if sp.is_symlink():
                return False, f"{sub} must be a directory of regular files"
            if not sp.exists():
                continue
            if not sp.is_dir():
                return False, f"{sub} must be a directory of regular files"
            for f in sp.iterdir():
                if f.is_symlink() or not f.is_file():
                    return False, f"{sub} member {f.name!r} is not a regular file"
        return True, None
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        return False, f"unreadable package: {exc}"


# ── workspaces (partial allow: only workspaces/<agent>/memory/**) ───────────

def _classify_workspaces(ws_dir: Path) -> list[ClassifiedEntry]:
    if not ws_dir.is_dir():
        return [ClassifiedEntry(path="workspaces", classification=RootClassification.REJECT,
                                reason=REJECT_NONREGULAR)]
    children = sorted(ws_dir.iterdir(), key=lambda p: p.name)
    if not children:
        return [ClassifiedEntry(path="workspaces", classification=RootClassification.INCLUDE)]
    entries: list[ClassifiedEntry] = []
    for agent_dir in children:
        rel_agent = f"workspaces/{agent_dir.name}"
        if agent_dir.is_symlink() or not agent_dir.is_dir():
            entries.append(ClassifiedEntry(path=rel_agent, classification=RootClassification.REJECT,
                                           reason=REJECT_NONREGULAR))
            continue
        subs = sorted(agent_dir.iterdir(), key=lambda p: p.name)
        if not subs:
            entries.append(ClassifiedEntry(path=rel_agent, classification=RootClassification.EXCLUDE,
                                           reason=EXCLUDE_WORKSPACE_NON_MEMORY))
            continue
        for sub in subs:
            rel_sub = f"{rel_agent}/{sub.name}"
            if sub.name == _MEMORY_DIR_NAME:
                if sub.is_dir() and not sub.is_symlink():
                    entries.append(ClassifiedEntry(path=rel_sub,
                                                   classification=RootClassification.INCLUDE))
                else:
                    entries.append(ClassifiedEntry(path=rel_sub,
                                                   classification=RootClassification.REJECT,
                                                   reason=REJECT_NONREGULAR))
            elif sub.name == "output":
                entries.append(ClassifiedEntry(path=rel_sub,
                                               classification=RootClassification.EXCLUDE,
                                               reason=EXCLUDE_TASK_OUTPUT))
            elif sub.name in CACHE_ENTRIES:
                entries.append(ClassifiedEntry(path=rel_sub,
                                               classification=RootClassification.EXCLUDE,
                                               reason=EXCLUDE_CACHE))
            elif sub.is_symlink() or (not sub.is_file() and not sub.is_dir()):
                entries.append(ClassifiedEntry(path=rel_sub,
                                               classification=RootClassification.REJECT,
                                               reason=REJECT_NONREGULAR))
            else:
                entries.append(ClassifiedEntry(path=rel_sub,
                                               classification=RootClassification.EXCLUDE,
                                               reason=EXCLUDE_WORKSPACE_NON_MEMORY))
    return entries
