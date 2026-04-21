from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum

from typing import Literal

from pydantic import BaseModel, Field


class TaskStatus(StrEnum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    FAILED = "failed"


class BlockKind(StrEnum):
    DELEGATED = "delegated"
    ESCALATED = "escalated"


class TaskType(StrEnum):
    IMPLEMENT_FEATURE = "implement_feature"
    BUG_FIX = "bug_fix"
    PAYMENT_CHANGE = "payment_change"
    GENERAL = "general"


class PerformanceTier(StrEnum):
    GREEN = "green"
    YELLOW = "yellow"
    RED = "red"


class ReviewVerdict(StrEnum):
    APPROVE = "approve"
    REVISE = "revise"
    REJECT = "reject"


def _now() -> datetime:
    return datetime.now(timezone.utc)


class TaskRecord(BaseModel):
    id: str
    type: TaskType
    status: TaskStatus = TaskStatus.PENDING
    assigned_agent: str | None = None
    team: str = "product_engineering"
    brief: str
    parent_task_id: str | None = None
    block_kind: BlockKind | None = None
    note: str | None = None
    final_artifact_dir: str | None = None
    orchestration_step_count: int = 0
    revision_count: int = 0
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)
    completed_at: datetime | None = None
    cancelled_at: datetime | None = None


class CompletionReport(BaseModel):
    task_id: str
    agent: str
    status: str
    confidence: int = Field(ge=0, le=100)
    output_summary: str
    risks_flagged: list[str] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)
    suggested_reviewer_focus: list[str] = Field(default_factory=list)
    artifact_dir: str | None = None


class TaskStep(BaseModel):
    agent: str
    action: str
    description: str


class NextStep(BaseModel):
    """Decision returned by the Engineering Head for what the orchestrator should do next."""
    action: Literal["delegate", "done", "escalate"]
    agent: str | None = None
    prompt: str | None = None
    summary: str | None = None
    reason: str | None = None


class StepRecord(BaseModel):
    """Record of a completed orchestration step, shown to EH as history."""
    step_number: int
    agent: str
    action: str
    result_summary: str
    success: bool
