from __future__ import annotations

from pathlib import Path

import pytest

from src.infrastructure.learnings_store import (
    LearningEntry,
    LearningsStore,
    InvalidLearningId,
    InvalidLearningEntry,
    LearningIdExists,
    LearningNotFound,
    PromotedLocked,
)


@pytest.fixture
def store(tmp_path: Path) -> LearningsStore:
    learnings_dir = tmp_path / "learnings"
    learnings_dir.mkdir()
    return LearningsStore(learnings_dir)


def test_learning_id_regex_validates():
    LearningsStore.validate_id("LRN-001")  # ok
    LearningsStore.validate_id("LRN-999")  # ok
    LearningsStore.validate_id("LRN-1234")  # ok (>3 digits also valid)
    with pytest.raises(InvalidLearningId):
        LearningsStore.validate_id("lrn-001")  # lowercase
    with pytest.raises(InvalidLearningId):
        LearningsStore.validate_id("LRN-1")  # too few digits
    with pytest.raises(InvalidLearningId):
        LearningsStore.validate_id("LRN-00A")  # non-digit
