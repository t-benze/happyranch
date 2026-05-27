from __future__ import annotations

from pathlib import Path

import pytest

from src.infrastructure.asset_store import (
    AssetStore,
    AssetTooLarge,
    InvalidAssetName,
    AssetNotFound,
    MAX_ASSET_BYTES,
)


def test_put_creates_file(tmp_path: Path) -> None:
    store = AssetStore(tmp_path / "assets")
    info = store.put("report.pdf", b"hello world")
    assert info.name == "report.pdf"
    assert info.size_bytes == 11
    assert (tmp_path / "assets" / "report.pdf").read_bytes() == b"hello world"


def test_put_overwrites_existing(tmp_path: Path) -> None:
    store = AssetStore(tmp_path / "assets")
    store.put("x.txt", b"first")
    info = store.put("x.txt", b"second")
    assert info.size_bytes == 6
    assert (tmp_path / "assets" / "x.txt").read_bytes() == b"second"


def test_put_is_atomic_on_failure(tmp_path: Path, monkeypatch) -> None:
    store = AssetStore(tmp_path / "assets")
    store.put("x.txt", b"original")

    # Force os.replace to fail; assert original survives, no .tmp file lingers.
    import os
    real_replace = os.replace

    def boom(_src, _dst):
        raise RuntimeError("disk full")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(RuntimeError):
        store.put("x.txt", b"new")

    assert (tmp_path / "assets" / "x.txt").read_bytes() == b"original"
    # No stray temp files
    leftovers = [p.name for p in (tmp_path / "assets").iterdir() if p.name.startswith(".tmp")]
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
    store = AssetStore(tmp_path / "assets")
    with pytest.raises(InvalidAssetName):
        store.put(bad_name, b"x")


def test_put_rejects_oversized(tmp_path: Path) -> None:
    store = AssetStore(tmp_path / "assets")
    with pytest.raises(AssetTooLarge):
        store.put("big.bin", b"x" * (MAX_ASSET_BYTES + 1))


def test_put_accepts_exact_max(tmp_path: Path) -> None:
    store = AssetStore(tmp_path / "assets")
    info = store.put("max.bin", b"x" * MAX_ASSET_BYTES)
    assert info.size_bytes == MAX_ASSET_BYTES


def test_get_returns_bytes(tmp_path: Path) -> None:
    store = AssetStore(tmp_path / "assets")
    store.put("a.txt", b"content")
    assert store.read("a.txt") == b"content"


def test_get_missing_raises(tmp_path: Path) -> None:
    store = AssetStore(tmp_path / "assets")
    with pytest.raises(AssetNotFound):
        store.read("missing.txt")


def test_get_rejects_invalid_name(tmp_path: Path) -> None:
    store = AssetStore(tmp_path / "assets")
    with pytest.raises(InvalidAssetName):
        store.read("../etc/passwd")


def test_list_returns_sorted_summaries(tmp_path: Path) -> None:
    store = AssetStore(tmp_path / "assets")
    store.put("b.txt", b"22")
    store.put("a.txt", b"1")
    store.put("c.txt", b"333")
    names = [s.name for s in store.list_assets()]
    assert names == ["a.txt", "b.txt", "c.txt"]
    sizes = {s.name: s.size_bytes for s in store.list_assets()}
    assert sizes == {"a.txt": 1, "b.txt": 2, "c.txt": 3}


def test_list_skips_dotfiles_and_tmp(tmp_path: Path) -> None:
    store = AssetStore(tmp_path / "assets")
    store.put("real.txt", b"x")
    # Plant a dotfile + a stale tmp directly on disk
    (tmp_path / "assets" / ".DS_Store").write_bytes(b"")
    (tmp_path / "assets" / ".tmp.abc123").write_bytes(b"")
    names = [s.name for s in store.list_assets()]
    assert names == ["real.txt"]


def test_path_for_returns_resolved_path(tmp_path: Path) -> None:
    store = AssetStore(tmp_path / "assets")
    store.put("a.txt", b"x")
    p = store.path_for("a.txt")
    assert p == tmp_path / "assets" / "a.txt"
    assert p.exists()
