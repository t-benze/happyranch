from pathlib import Path

import pytest

from src.config import Settings
from src.infrastructure.database import Database


@pytest.fixture
def tmp_dir(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
def test_settings(tmp_dir: Path) -> Settings:
    """Settings that use temporary directories for DB and workspaces."""
    return Settings(
        project_root=tmp_dir,
        data_dir=tmp_dir,
        db_path="test.db",
        workspaces_dir="workspaces",
    )


@pytest.fixture
def db(tmp_dir: Path) -> Database:
    """A fresh Database instance backed by a temporary file."""
    return Database(tmp_dir / "test.db")
