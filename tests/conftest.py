from pathlib import Path

import pytest

from src.config import Settings
from src.infrastructure.database import Database
from src.runtime import RuntimeDir


@pytest.fixture
def tmp_dir(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
def test_settings(tmp_dir: Path) -> Settings:
    return Settings(project_root=tmp_dir)


@pytest.fixture
def test_runtime(tmp_dir: Path) -> RuntimeDir:
    return RuntimeDir.init(tmp_dir / "runtime")


@pytest.fixture
def db(tmp_dir: Path) -> Database:
    """A fresh Database instance backed by a temporary file."""
    return Database(tmp_dir / "test.db")
