from __future__ import annotations

from pathlib import Path

import pytest

from runtime.infrastructure.artifact_store import (
    ArtifactStore,
    ArtifactTooLarge,
    InvalidArtifactName,
    ArtifactNotFound,
    MAX_ARTIFACT_BYTES,
)


def test_put_creates_file(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    info = store.put("report.pdf", b"hello world")
    assert info.name == "report.pdf"
    assert info.size_bytes == 11
    assert (tmp_path / "artifacts" / "report.pdf").read_bytes() == b"hello world"


def test_put_overwrites_existing(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    store.put("x.txt", b"first")
    info = store.put("x.txt", b"second")
    assert info.size_bytes == 6
    assert (tmp_path / "artifacts" / "x.txt").read_bytes() == b"second"


def test_put_is_atomic_on_failure(tmp_path: Path, monkeypatch) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    store.put("x.txt", b"original")

    # Force os.replace to fail; assert original survives, no .tmp file lingers.
    import os
    real_replace = os.replace

    def boom(_src, _dst):
        raise RuntimeError("disk full")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(RuntimeError):
        store.put("x.txt", b"new")

    assert (tmp_path / "artifacts" / "x.txt").read_bytes() == b"original"
    # No stray temp files
    leftovers = [p.name for p in (tmp_path / "artifacts").iterdir() if p.name.startswith(".tmp")]
    assert leftovers == []
    monkeypatch.setattr(os, "replace", real_replace)


@pytest.mark.parametrize("bad_name", [
    "",
    ".",
    "..",
    ".hidden",
    "../escape",
    "with\\back",
    "with space",
    "a" * 201,
    # Traversal vectors:
    "/leading-slash",
    "trailing-slash/",
    "a//b",
    "/abs",
    # Backslash remains rejected:
    "a\\b",
    # Leading dot segment:
    ".hidden/x",
    # '..' segment anywhere:
    "reports/../escape",
])
def test_put_rejects_invalid_names(tmp_path: Path, bad_name: str) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    with pytest.raises(InvalidArtifactName):
        store.put(bad_name, b"x")


def test_put_rejects_oversized(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    with pytest.raises(ArtifactTooLarge):
        store.put("big.bin", b"x" * (MAX_ARTIFACT_BYTES + 1))


def test_put_accepts_exact_max(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    info = store.put("max.bin", b"x" * MAX_ARTIFACT_BYTES)
    assert info.size_bytes == MAX_ARTIFACT_BYTES


def test_get_returns_bytes(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    store.put("a.txt", b"content")
    assert store.read("a.txt") == b"content"


def test_get_missing_raises(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    with pytest.raises(ArtifactNotFound):
        store.read("missing.txt")


def test_get_rejects_invalid_name(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    with pytest.raises(InvalidArtifactName):
        store.read("../etc/passwd")


def test_list_returns_sorted_summaries(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    store.put("b.txt", b"22")
    store.put("a.txt", b"1")
    store.put("c.txt", b"333")
    names = [s.name for s in store.list_artifacts()]
    assert names == ["a.txt", "b.txt", "c.txt"]
    sizes = {s.name: s.size_bytes for s in store.list_artifacts()}
    assert sizes == {"a.txt": 1, "b.txt": 2, "c.txt": 3}


def test_list_skips_dotfiles_and_tmp(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    store.put("real.txt", b"x")
    # Plant a dotfile + a stale tmp directly on disk
    (tmp_path / "artifacts" / ".DS_Store").write_bytes(b"")
    (tmp_path / "artifacts" / ".tmp.abc123").write_bytes(b"")
    names = [s.name for s in store.list_artifacts()]
    assert names == ["real.txt"]


def test_path_for_returns_resolved_path(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    store.put("a.txt", b"x")
    p = store.path_for("a.txt")
    assert p == tmp_path / "artifacts" / "a.txt"
    assert p.exists()


def test_delete_removes_file(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    store.put("gone.txt", b"bye")
    assert (tmp_path / "artifacts" / "gone.txt").exists()
    store.delete("gone.txt")
    assert not (tmp_path / "artifacts" / "gone.txt").exists()


def test_delete_missing_raises(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    with pytest.raises(ArtifactNotFound):
        store.delete("missing.txt")


def test_delete_rejects_invalid_name(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    with pytest.raises(InvalidArtifactName):
        store.delete("../etc/passwd")


# ---------------------------------------------------------------------------
# Nested-key support (TASK-305)
# ---------------------------------------------------------------------------


def test_validate_accepts_nested_keys(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    # Nested keys with '/' separator should validate.
    store.validate_name("reports/2026/q2.pdf")
    store.validate_name("a/b/c.txt")
    # Existing flat names still work.
    store.validate_name("report.pdf")
    store.validate_name("dev_agent-2026-06-10-perf-report.pdf")


def test_validate_rejects_traversal_vectors(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    rejected = [
        "../escape",
        "reports/../evil",
        "/leading-slash",
        "trailing-slash/",
        "a//b",
        "/abs",
        ".hidden",
        ".hidden/x",
        "a\\b",
        "",
        "a" * 201,
    ]
    for name in rejected:
        with pytest.raises(InvalidArtifactName):
            store.validate_name(name)


def test_put_list_get_nested_roundtrip(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    name = "reports/2026/q2.pdf"
    content = b"quarterly-report"

    info = store.put(name, content)
    assert info.name == name
    assert info.size_bytes == len(content)

    # Read back.
    assert store.read(name) == content

    # List includes nested key.
    listing = store.list_artifacts()
    names = [a.name for a in listing]
    assert name in names

    # list_artifacts with prefix filter.
    filtered = store.list_artifacts(prefix="reports/")
    filtered_names = [a.name for a in filtered]
    assert name in filtered_names

    # Prefix that matches nothing.
    empty = store.list_artifacts(prefix="nonexistent/")
    assert empty == []


def test_list_returns_all_flat_and_nested(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    store.put("flat.txt", b"flat")
    store.put("reports/a.txt", b"a")
    store.put("reports/b.txt", b"b")
    store.put("reports/2026/q1.pdf", b"q1")

    listing = store.list_artifacts()
    names = sorted(a.name for a in listing)
    assert names == ["flat.txt", "reports/2026/q1.pdf", "reports/a.txt", "reports/b.txt"]


def test_list_skips_dotfiles_in_subdirs(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    store.put("a/real.txt", b"x")
    # Plant dotfiles inside subdirectories.
    (tmp_path / "artifacts" / "a" / ".DS_Store").write_bytes(b"")
    (tmp_path / "artifacts" / "a" / ".tmp.abc123").write_bytes(b"")
    names = [s.name for s in store.list_artifacts()]
    assert names == ["a/real.txt"]


def test_path_for_resolves_outside_root_rejected(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    # Physically create a symlink that points outside root.
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"danger")
    (tmp_path / "artifacts" / "link").symlink_to(outside)
    # The name "link" itself is valid, but path_for must detect the resolved
    # path is NOT relative to root.
    # The symlink + resolved-path guard: we let validate_name pass "link"
    # (valid segment), then path_for resolves it and the guard rejects.
    with pytest.raises(InvalidArtifactName, match="path_traversal"):
        store.path_for("link")


def test_delete_nested_removes_file(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    store.put("reports/gone.txt", b"bye")
    assert (tmp_path / "artifacts" / "reports" / "gone.txt").exists()
    store.delete("reports/gone.txt")
    assert not (tmp_path / "artifacts" / "reports" / "gone.txt").exists()


def test_exists_nested(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    store.put("reports/x.txt", b"x")
    assert store.exists("reports/x.txt")
    assert not store.exists("reports/missing.txt")
    assert not store.exists("../evil")


def test_put_creates_intermediate_dirs(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    info = store.put("a/b/c/d.txt", b"deep")
    assert info.name == "a/b/c/d.txt"
    assert (tmp_path / "artifacts" / "a" / "b" / "c" / "d.txt").read_bytes() == b"deep"


def test_put_atomic_tmp_in_destination_parent(tmp_path: Path, monkeypatch) -> None:
    """Ensure the mkstemp tmp file is created in the destination's parent dir
    so os.replace stays atomic on one filesystem."""
    store = ArtifactStore(tmp_path / "artifacts")
    name = "reports/x.txt"
    content = b"test"
    # Capture the tmp_path used by mkstemp.
    import tempfile as tempfile_mod
    real_mkstemp = tempfile_mod.mkstemp
    captured_dirs: list[str] = []

    def spy_mkstemp(prefix=".tmp.", dir=None):
        captured_dirs.append(dir if dir else "")
        return real_mkstemp(prefix=prefix, dir=dir)

    monkeypatch.setattr(tempfile_mod, "mkstemp", spy_mkstemp)
    store.put(name, content)
    # The tmp dir should be the parent of the destination.
    expected_parent = str(tmp_path / "artifacts" / "reports")
    assert captured_dirs[0] == expected_parent, f"expected {expected_parent}, got {captured_dirs[0]}"
