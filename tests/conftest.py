from pathlib import Path

import pytest

from src.config import Settings


@pytest.fixture
def tmp_dir(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
def test_settings(tmp_dir: Path) -> Settings:
    """Settings that use temporary directories for DB and workspaces."""
    return Settings(
        project_root=tmp_dir,
        db_path="test.db",
        workspaces_dir="workspaces",
    )
