from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum

from typing import Literal

from pydantic import BaseModel, Field, field_validator


class TaskStatus(StrEnum):
    # THR-037 Change B (Path B, stored source-of-truth): the surfaced `blocked`
    # vocabulary collapses into a model that stores what is actually true. A
    # parent waiting on its own children/jobs is IN_PROGRESS, not BLOCKED; the
    # waiting reason is preserved in the `block_kind` discriminant. See
    # docs/superpowers/specs/2026-06-27-task-status-pathB-stored-design.md.
    PENDING = "pending"
    # Two-valued, discriminated by `block_kind` (see BlockKind):
    #   block_kind IS NULL                       ⟺ a subprocess is running now.
    #   block_kind IN (delegated, blocked_on_job) ⟺ parked, no subprocess,
    #     waiting on children/jobs it manages internally.
    IN_PROGRESS = "in_progress"
    # NEW (Path B), non-terminal. A task that needs a founder decision (genuine
    # agent escalation, failure-round-bound exhaustion, or budget exhaustion).
    # Was the legacy blocked(escalated) state; `block_kind` is cleared. The
    # founder resolves it via resolve-escalation (approve → pending / reject →
    # failed). NOT in any terminal predicate.
    ESCALATED = "escalated"
    COMPLETED = "completed"              # terminal
    FAILED = "failed"                   # terminal
    # NEW (Path B), terminal. A founder-initiated stop (was failed + cancelled_at
    # set). Distinct from FAILED so the audit/event trail shows a deliberate
    # cancellation, not an agent/executor failure. `cancelled_at` is still set.
    # Replays as a failure-class terminal event with outcome="cancelled" (see
    # OrgState._TERMINAL_STATUS_TO_EVENT). Joins every terminal predicate.
    CANCELLED = "cancelled"
    # Terminal. An escalated|delegated task whose follow-up work moved to a
    # human-authorized continuation (founder `revisit` / thread-dispatch) is
    # closed here instead of re-running — distinct from COMPLETED so the audit
    # trail shows it was superseded, not finished by an agent. Joins every
    # terminal predicate (TERMINAL_STATES, _TERMINAL_TASK_STATUSES,
    # _TERMINAL_STATUS_TO_EVENT). See protocol/05c-orchestrator.md and
    # docs/agent-guides/features-and-invariants.md (escalation).
    #
    # Phase 3 (THR-037): BLOCKED and BlockKind.ESCALATED were fully retired
    # after the transition soak. No live row carries 'blocked' after the
    # idempotent boot migration. See
    # docs/superpowers/specs/2026-06-27-task-status-pathB-stored-design.md §I.
    RESOLVED_SUPERSEDED = "resolved_superseded"



class BlockKind(StrEnum):
    # Path B: the waiting-reason discriminant for an IN_PROGRESS task —
    # "what this task is internally waiting on (NULL = a subprocess is running
    # now)". Live domain narrowed to {DELEGATED, BLOCKED_ON_JOB}.
    DELEGATED = "delegated"
    BLOCKED_ON_JOB = "blocked_on_job"


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
    # Provenance, NOT a behavior label: "subtask" iff spawned from an ongoing
    # task; "task" otherwise (founder-dispatched root). The orchestration gate
    # in run_step keys on this — see
    # docs/superpowers/specs/2026-06-03-subtask-composite-task-design.md.
    task_type: Literal["task", "subtask"] = "task"
    brief: str
    parent_task_id: str | None = None
    revisit_of_task_id: str | None = None
    dispatched_from_thread_id: str | None = None
    block_kind: BlockKind | None = None
    blocked_on_job_ids: str | None = None
    # In-flight inline delegation chain (JSON-serialized ChainState). NULL when no
    # chain is active on this parent. See docs/superpowers/specs/2026-05-30-inline-
    # delegation-chain-design.md.
    active_chain: str | None = None
    # In-flight fan-out metadata (JSON-serialized FanoutState). NULL when no
    # fan-out is active on this parent. Set atomically with child spawns;
    # cleared on successful join claim or terminal parent close.
    active_fanout: str | None = None
    note: str | None = None
    final_output_dir: str | None = None
    orchestration_step_count: int = 0
    revision_count: int = 0
    # Per-task override for the agent-session subprocess timeout (seconds).
    # NULL → fall through to org/config.yaml, then Settings default. Set by
    # `happyranch revisit --session-timeout-seconds`; inherited from parent on
    # delegate, and from predecessor root on revisit.
    session_timeout_seconds: int | None = None
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)
    completed_at: datetime | None = None
    # Founder-initiated cancellation marker. Under Path B a new cancellation
    # sets status=CANCELLED alongside this timestamp; historical rows left
    # as-is carry the old status=FAILED + cancelled_at shape, so derivations
    # that must classify cancellation (e.g. _classify_predecessor_status) read
    # `cancelled_at` presence rather than the status label for backward compat.
    cancelled_at: datetime | None = None
    last_heartbeat: datetime | None = None


class ChainLeg(BaseModel):
    """One leg of an inline delegation chain. The manager declares legs 2..N in
    NextStep.then; the first leg is the existing delegate payload (agent +
    prompt + optional expect_verdict).
    """
    agent: str
    prompt: str
    expect_verdict: str | None = None


class FanoutChild(BaseModel):
    """One child in a fanout/parallel NextStep. Phase 1: read-only children only —
    each carries agent + prompt. ``then`` and ``expect_verdict`` are NOT allowed in
    Phase 1 and are parse-rejected (mutating fan-out is out of scope).
    """
    agent: str
    prompt: str
    # Phase 1 rejects these fields — they exist only for Phase 2+ forward compat.
    then: list[ChainLeg] = Field(default_factory=list)
    expect_verdict: str | None = None


class NextStep(BaseModel):
    """Decision returned by a task owner for what the orchestrator should do next."""
    action: Literal["delegate", "done", "escalate", "fanout", "parallel"]
    agent: str | None = None
    prompt: str | None = None
    expect_verdict: str | None = None
    then: list[ChainLeg] = Field(default_factory=list)
    children: list[FanoutChild] = Field(default_factory=list)
    width_cap_ack: int | None = None

    @field_validator('action')
    @classmethod
    def _normalize_parallel_alias(cls, v: str) -> str:
        """Accept ``parallel`` as an alias for ``fanout``.

        Downstream code always sees ``fanout`` after validation so no
        dispatch changes are needed."""
        if v == "parallel":
            return "fanout"
        return v
    join_summary: str | None = None
    summary: str | None = None
    reason: str | None = None


class CompletionReport(BaseModel):
    task_id: str
    agent: str
    status: str
    confidence: int = Field(ge=0, le=100)
    output_summary: str
    # Optional structured outcome for review/QA-type workers (APPROVE, PASS,
    # REQUEST_CHANGES, etc.). Free-string; per-team vocabulary lives in each
    # team's workflow KB entry. Used by inline delegation chains to gate
    # auto-advance.
    verdict: str | None = None
    # Task-owner-only: structured next-step decision. Subtask agents leave this None.
    # Separating the decision from the prose summary eliminates the
    # double-encoding trap where the manager's output_summary had to itself
    # be JSON (see TASK-071 post-mortem).
    decision: NextStep | None = None
    risks_flagged: list[str] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)
    suggested_reviewer_focus: list[str] = Field(default_factory=list)
    output_dir: str | None = None
    waiting_on_job_ids: list[str] = Field(default_factory=list)


class TaskStep(BaseModel):
    agent: str
    action: str
    description: str


class StepRecord(BaseModel):
    """Record of a completed orchestration step, shown to the task owner as history."""
    step_number: int
    agent: str
    action: str
    result_summary: str
    success: bool


class DreamStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMEOUT = "timeout"
    SKIPPED = "skipped"


class DreamRecord(BaseModel):
    id: str
    agent_name: str
    local_date: str
    scheduled_for: datetime
    window_end: datetime
    window_start: datetime | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    status: DreamStatus = DreamStatus.PENDING
    summary: str | None = None
    transcript_path: str | None = None
    new_learnings_count: int = 0
    kb_candidate_count: int = 0
    founder_thread_id: str | None = None
    session_id: str | None = None
    error: str | None = None
    created_at: datetime = Field(default_factory=_now)


class WorkHourMode(StrEnum):
    WINDOWED = "windowed"
    CONTINUOUS = "continuous"


class WorkHourStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMEOUT = "timeout"
    SKIPPED = "skipped"


class WorkHourRecord(BaseModel):
    id: str
    agent_name: str
    local_date: str
    slot: str
    mode: WorkHourMode
    scheduled_for: datetime
    started_at: datetime | None = None
    ended_at: datetime | None = None
    status: WorkHourStatus = WorkHourStatus.PENDING
    routine_count: int = 0
    dropped_count: int = 0
    spawned_task_ids: list[str] = Field(default_factory=list)
    spawned_task_count: int = 0
    summary: str | None = None
    transcript_path: str | None = None
    session_id: str | None = None
    error: str | None = None
    created_at: datetime = Field(default_factory=_now)


class DreamKbCandidate(BaseModel):
    id: int | None = None
    dream_id: str
    agent_name: str
    slug: str
    title: str
    topic: str
    rationale: str
    body_markdown: str
    status: Literal["pending", "promoted", "rejected", "superseded"] = "pending"
    promoted_kb_slug: str | None = None
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


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


class ThreadStatus(StrEnum):
    OPEN = "open"
    ARCHIVED = "archived"


class ThreadMessageKind(StrEnum):
    MESSAGE = "message"
    DECLINE = "decline"
    SYSTEM = "system"


class ThreadInvocationStatus(StrEnum):
    PENDING = "pending"
    CONSUMED = "consumed"
    DECLINED = "declined"
    TIMEOUT = "timeout"
    FAILED = "failed"


class ThreadInvocationPurpose(StrEnum):
    REPLY = "reply"
    BOOTSTRAP = "bootstrap"
    TASK_FOLLOWUP = "task_followup"


class ThreadRecord(BaseModel):
    id: str
    subject: str
    status: ThreadStatus = ThreadStatus.OPEN
    started_at: datetime = Field(default_factory=_now)
    archived_at: datetime | None = None
    forwarded_from_id: str | None = None
    forwarded_from_kind: str | None = None  # 'thread'
    turn_cap: int = 500
    turns_used: int = 0
    summary: str | None = None
    transcript_path: str | None = None
    composed_by: str = "founder"
    composed_from_task_id: str | None = None
    composed_from_dream_id: str | None = None
    last_speaker: str | None = None


class ThreadParticipant(BaseModel):
    thread_id: str
    agent_name: str
    added_at: datetime = Field(default_factory=_now)
    added_by: str = "founder"


class ThreadAttachment(BaseModel):
    artifact_name: str
    display_name: str
    size_bytes: int | None = None
    content_type: str | None = None
    uploaded_by: str
    # Thread-scoped attachment id (mutually exclusive with artifact_name when non-None).
    # When set, the attachment is a thread-scoped file stored in the thread's
    # private attachment store rather than the org-shared ArtifactStore.
    thread_attachment_id: str | None = None


class ThreadScopedAttachment(BaseModel):
    """A file stored in a thread's private attachment store."""
    attachment_id: str
    thread_id: str
    display_name: str
    size_bytes: int | None = None
    content_type: str | None = None
    uploaded_by: str
    created_at: str = ""


class ThreadMessage(BaseModel):
    id: int | None = None
    thread_id: str
    seq: int
    speaker: str
    kind: ThreadMessageKind
    body_markdown: str | None = None
    decline_reason: str | None = None
    system_payload: dict | None = None
    attachments: list[ThreadAttachment] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_now)


class ResponderStatusEntry(BaseModel):
    agent_name: str
    status: Literal["queued", "working", "replied", "declined", "failed"]
    responded_at: str | None
    started_at: str | None = None


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


class JobStatus(StrEnum):
    PENDING   = "pending"
    REJECTED  = "rejected"
    RUNNING   = "running"
    COMPLETED = "completed"
    FAILED    = "failed"


class JobInterpreter(StrEnum):
    BASH    = "bash"
    SH      = "sh"
    ZSH     = "zsh"
    PYTHON3 = "python3"


class JobRecord(BaseModel):
    id:               str
    # Scope id of the submission context. Always a TASK-NNN id for task-originated
    # jobs. Keeping one column avoids plumbing a ``scope_id`` everywhere it's
    # already in use.
    task_id:          str
    agent_name:       str
    title:            str
    rationale:        str
    script_text:      str
    interpreter:      JobInterpreter
    cwd_hint:         str | None = None
    status:           JobStatus = JobStatus.PENDING
    exit_code:        int | None = None
    stdout_head:      str | None = None
    stderr_head:      str | None = None
    stdout_path:      str | None = None
    stderr_path:      str | None = None
    duration_ms:      int | None = None
    started_at:       str | None = None
    finished_at:      str | None = None
    reviewed_at:      str | None = None
    reviewed_by:      str | None = None
    reject_reason:    str | None = None
    cwd_resolved:     str | None = None
    max_runtime_seconds: int | None = None
    # Per-stream output-size cap (bytes). Either stdout OR stderr crossing
    # this triggers SIGKILL with reason="output_cap". 50 MiB default matches
    # the column default in the jobs table schema.
    max_output_bytes: int | None = 52428800
    # Founder-review gate. True → row inserted as `pending`, awaits explicit
    # /run. False (default) → auto-run inline at /submit.
    review_required:  bool = False
    # Long-running flag. True → no default runtime cap (unbounded unless an
    # explicit max_runtime_seconds is provided), killed only by /stop or the
    # task-terminal kill hook. False (default) → 300s default cap when no
    # explicit override is provided.
    persistent:       bool = False
    # Terminal-status reason — populated by the runner when status='failed'.
    # Examples: "timeout", "output_cap", "founder_stop", "agent_stop",
    # "task_ended", "spawn_failed", "internal_error", "daemon_crash".
    # NULL when status='completed' or the job hasn't reached terminal yet.
    reason:           str | None = None
    created_at:       str
