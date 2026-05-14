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
    status: TaskStatus = TaskStatus.PENDING
    assigned_agent: str | None = None
    team: str = "engineering"
    brief: str
    parent_task_id: str | None = None
    revisit_of_task_id: str | None = None
    dispatched_from_talk_id: str | None = None
    dispatched_from_thread_id: str | None = None
    block_kind: BlockKind | None = None
    note: str | None = None
    final_artifact_dir: str | None = None
    orchestration_step_count: int = 0
    revision_count: int = 0
    # Per-task override for the agent-session subprocess timeout (seconds).
    # NULL → fall through to org/config.yaml, then Settings default. Set by
    # `opc revisit --session-timeout-seconds`; inherited from parent on
    # delegate, and from predecessor root on revisit.
    session_timeout_seconds: int | None = None
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)
    completed_at: datetime | None = None
    cancelled_at: datetime | None = None
    last_heartbeat: datetime | None = None


class NextStep(BaseModel):
    """Decision returned by a team manager for what the orchestrator should do next."""
    action: Literal["delegate", "done", "escalate"]
    agent: str | None = None
    prompt: str | None = None
    summary: str | None = None
    reason: str | None = None


class CompletionReport(BaseModel):
    task_id: str
    agent: str
    status: str
    confidence: int = Field(ge=0, le=100)
    output_summary: str
    # Manager-only: structured next-step decision. Workers leave this None.
    # Separating the decision from the prose summary eliminates the
    # double-encoding trap where the manager's output_summary had to itself
    # be JSON (see TASK-071 post-mortem).
    decision: NextStep | None = None
    risks_flagged: list[str] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)
    suggested_reviewer_focus: list[str] = Field(default_factory=list)
    artifact_dir: str | None = None


class TaskStep(BaseModel):
    agent: str
    action: str
    description: str


class StepRecord(BaseModel):
    """Record of a completed orchestration step, shown to the team manager as history."""
    step_number: int
    agent: str
    action: str
    result_summary: str
    success: bool


class TokenUsage(BaseModel):
    """Per-session token usage, unified across executors.

    All fields nullable so we can write a row even when parsing partially
    succeeds (per spec §4.3). `total` deliberately excludes cache reads —
    cache hits are an effectiveness signal, not new consumption.
    """
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_creation_tokens: int | None = None
    reasoning_tokens: int | None = None
    model: str | None = None
    usage_raw_json: str | None = None

    @property
    def total(self) -> int:
        return (self.input_tokens or 0) + (self.output_tokens or 0) + (self.reasoning_tokens or 0)


class TalkStatus(StrEnum):
    OPEN = "open"
    CLOSED = "closed"
    ABANDONED = "abandoned"


class TalkRecord(BaseModel):
    id: str
    agent_name: str
    status: TalkStatus = TalkStatus.OPEN
    started_at: datetime = Field(default_factory=_now)
    ended_at: datetime | None = None
    summary: str | None = None
    topic_list: list[str] = Field(default_factory=list)
    new_learnings_count: int = 0
    new_kb_slugs: list[str] = Field(default_factory=list)
    transcript_path: str | None = None


class ThreadStatus(StrEnum):
    OPEN = "open"
    ARCHIVING = "archiving"
    ARCHIVED = "archived"
    ABANDONED = "abandoned"


class ThreadMessageKind(StrEnum):
    MESSAGE = "message"
    DECLINE = "decline"
    SYSTEM = "system"


class ThreadInvocationStatus(StrEnum):
    PENDING = "pending"
    CONSUMED = "consumed"
    TIMEOUT = "timeout"
    FAILED = "failed"


class ThreadInvocationPurpose(StrEnum):
    REPLY = "reply"
    BOOTSTRAP = "bootstrap"
    CLOSE_OUT = "close_out"


class ThreadRecord(BaseModel):
    id: str
    subject: str
    status: ThreadStatus = ThreadStatus.OPEN
    started_at: datetime = Field(default_factory=_now)
    archived_at: datetime | None = None
    forwarded_from_id: str | None = None
    forwarded_from_kind: str | None = None  # 'thread' | 'talk'
    turn_cap: int = 500
    turns_used: int = 0
    summary: str | None = None
    new_kb_slugs: list[str] = Field(default_factory=list)
    new_learnings_total: int = 0
    transcript_path: str | None = None
    archive_requested_at: datetime | None = None


class ThreadParticipant(BaseModel):
    thread_id: str
    agent_name: str
    added_at: datetime = Field(default_factory=_now)
    added_by: str = "founder"


class ThreadMessage(BaseModel):
    id: int | None = None
    thread_id: str
    seq: int
    speaker: str
    kind: ThreadMessageKind
    body_markdown: str | None = None
    addressed_to: list[str] | None = None
    decline_reason: str | None = None
    system_payload: dict | None = None
    created_at: datetime = Field(default_factory=_now)


class ThreadInvocation(BaseModel):
    id: int | None = None
    thread_id: str
    agent_name: str
    invocation_token: str
    triggering_seq: int
    purpose: ThreadInvocationPurpose
    status: ThreadInvocationStatus = ThreadInvocationStatus.PENDING
    enqueued_at: datetime = Field(default_factory=_now)
    started_at: datetime | None = None
    consumed_at: datetime | None = None
    session_id: str | None = None
    dispatched_task_id: str | None = None
    decline_reason: str | None = None
