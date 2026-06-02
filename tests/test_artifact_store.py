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
    "with/slash",
    "with\\back",
    "with space",
    "a" * 201,
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
