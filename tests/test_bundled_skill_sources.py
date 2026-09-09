from __future__ import annotations

import runpy
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from runtime.config import Settings
from runtime.orchestrator.workspace_adapters import _compute_dir_hash
from runtime.skills.sources import bundled_skills_dir
from runtime.skills.system_contracts import list_system_contracts


@pytest.fixture(autouse=True)
def isolated_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path / "daemon-home"))
    monkeypatch.delenv("HAPPYRANCH_PROTOCOL_DIR", raising=False)
    monkeypatch.delenv("HAPPYRANCH_PROJECT_ROOT", raising=False)


def test_release_contains_every_declared_system_contract() -> None:
    root = bundled_skills_dir()
    settings = Settings()
    assert settings.get_bundled_skills_dir() == root
    for contract in list_system_contracts():
        declared = settings.project_root / contract.source_path
        assert declared == root / contract.id / "SKILL.md"
        assert declared.is_file()
    assert (root / "make-worktree" / "worktree_guard.py").is_file()


def test_explicit_package_root_never_falls_back_to_this_checkout(tmp_path: Path) -> None:
    settings = Settings(project_root=tmp_path / "missing-release")
    root = settings.get_bundled_skills_dir()
    assert root == tmp_path / "missing-release/runtime/skills/bundled"
    assert not root.exists()


@pytest.mark.parametrize("source", ["constructor", "environment", "yaml"])
def test_nondefault_legacy_source_requires_explicit_migration(
    source: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    kwargs: dict[str, str] = {}
    if source == "constructor":
        kwargs["protocol_dir"] = "custom-contracts"
    elif source == "environment":
        monkeypatch.setenv("HAPPYRANCH_PROTOCOL_DIR", "custom-contracts")
    else:
        home = tmp_path / "daemon-home"
        home.mkdir()
        (home / "config.yaml").write_text("protocol_dir: custom-contracts\n")
    with pytest.raises(ValueError, match="protocol_dir overrides are retired"):
        Settings(**kwargs)


def test_explicit_legacy_default_is_inert() -> None:
    assert Settings(protocol_dir="protocol").get_bundled_skills_dir() == bundled_skills_dir()


def test_source_relocation_preserves_hash_but_member_mutation_does_not(tmp_path: Path) -> None:
    source = bundled_skills_dir() / "make-worktree"
    relocated = tmp_path / "other-release" / "make-worktree"
    shutil.copytree(source, relocated)
    assert _compute_dir_hash(source) == _compute_dir_hash(relocated)
    member = relocated / "worktree_guard.py"
    member.write_bytes(member.read_bytes() + b"\n# changed release member\n")
    assert _compute_dir_hash(source) != _compute_dir_hash(relocated)


def test_assistant_discovery_uses_stable_source_marker() -> None:
    from runtime.system_assistant import _KNOWLEDGE_SOURCES, _source_knowledge_root

    root = _source_knowledge_root()
    assert (root / "runtime/config.py").is_file()
    assert not (root / "protocol").exists()
    for source, _target in _KNOWLEDGE_SOURCES:
        assert not source.startswith("protocol/")
        assert (root / source).is_file()


def test_frozen_release_data_contains_skills_and_assistant_knowledge(tmp_path: Path) -> None:
    """Evaluate the real packaging recipe without running a native compiler."""
    from runtime.system_assistant import _KNOWLEDGE_SOURCES

    checkout = Path(__file__).resolve().parents[1]
    build_root = tmp_path / "release-source"
    (build_root / "packaging").mkdir(parents=True)
    for name in ("runtime", "docs", "skills", "README.md", "pyproject.toml"):
        (build_root / name).symlink_to(checkout / name)
    spec = build_root / "packaging/daemon.spec"
    shutil.copyfile(checkout / "packaging/daemon.spec", spec)
    analyses: list[dict] = []

    def analysis(_entrypoints: list[str], **kwargs: object) -> SimpleNamespace:
        analyses.append(kwargs)
        return SimpleNamespace(pure=[], scripts=[], binaries=[], datas=kwargs["datas"])

    runpy.run_path(str(spec), init_globals={
        "Analysis": analysis,
        "PYZ": lambda *args, **kwargs: None,
        "EXE": lambda *args, **kwargs: None,
        "COLLECT": lambda *args, **kwargs: None,
    })
    assert len(analyses) == 2  # daemon and CLI share the same release assets
    for result in analyses:
        shipped: dict[str, Path] = {}
        for source_name, destination in result["datas"]:
            source = Path(source_name)
            assert source.exists(), source
            if source.is_dir():
                for member in source.rglob("*"):
                    if member.is_file():
                        shipped[str(Path(destination) / member.relative_to(source))] = member
            else:
                shipped[str(Path(destination) / source.name)] = source
        for source_rel, _target in _KNOWLEDGE_SOURCES:
            assert f"runtime/system_knowledge/{source_rel}" in shipped
        for member in bundled_skills_dir().rglob("*"):
            if member.is_file():
                assert f"runtime/skills/bundled/{member.relative_to(bundled_skills_dir())}" in shipped
        assert not any(key.startswith("protocol/") for key in shipped)
