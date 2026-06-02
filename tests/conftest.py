from pathlib import Path

import pytest

from runtime.config import Settings
from runtime.infrastructure.database import Database
from runtime.orchestrator._paths import OrgPaths
from runtime.runtime import RuntimeDir


@pytest.fixture
def tmp_dir(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
def test_settings(tmp_dir: Path) -> Settings:
    return Settings(project_root=tmp_dir)


@pytest.fixture
def test_runtime(tmp_dir: Path) -> OrgPaths:
    """OrgPaths rooted at <tmp>/runtime/orgs/test/.

    Historical name kept for backward compatibility — tests treat this as the
    single per-org root, not the multi-org container. The multi-org RuntimeDir
    is materialized at <tmp>/runtime/ so ``RuntimeDir.load`` could re-read it.
    """
    rt = RuntimeDir.init(tmp_dir / "runtime")
    return OrgPaths(root=rt.orgs_dir / "test")


@pytest.fixture
def db(tmp_dir: Path) -> Database:
    """A fresh Database instance backed by a temporary file."""
    return Database(tmp_dir / "test.db")
