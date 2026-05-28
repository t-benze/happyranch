from __future__ import annotations

from src.models import BlockKind


def test_blocked_on_job_enum_value():
    """BlockKind has a BLOCKED_ON_JOB value with the string 'blocked_on_job'."""
    assert BlockKind.BLOCKED_ON_JOB.value == "blocked_on_job"
    assert BlockKind("blocked_on_job") is BlockKind.BLOCKED_ON_JOB
