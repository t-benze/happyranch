from runtime.models import (
    ChainLeg,
    CompletionReport,
    NextStep,
    ReviewVerdict,
    StepRecord,
    TaskRecord,
    TaskStatus,
    TaskStep,
    TalkRecord,
    TalkStatus,
)


def test_task_status_values():
    assert TaskStatus.PENDING == "pending"
    assert TaskStatus.IN_PROGRESS == "in_progress"
    assert TaskStatus.COMPLETED == "completed"
    assert TaskStatus.BLOCKED == "blocked"
    assert TaskStatus.FAILED == "failed"


def test_review_verdict_values():
    assert ReviewVerdict.APPROVE == "approve"
    assert ReviewVerdict.REVISE == "revise"
    assert ReviewVerdict.REJECT == "reject"


def test_task_record_creation():
    record = TaskRecord(
        id="TASK-001",
        brief="Add Alipay support",
    )
    assert record.status == TaskStatus.PENDING
    assert record.revision_count == 0
    assert record.assigned_agent is None
    assert record.team == "engineering"
    assert record.completed_at is None
    assert record.created_at is not None
    assert record.updated_at is not None


def test_completion_report_creation():
    report = CompletionReport(
        task_id="TASK-001",
        agent="dev_agent",
        status="completed",
        confidence=85,
        output_summary="Implemented Alipay payment integration",
        risks_flagged=["Alipay sandbox differs from production"],
        dependencies=["Payment Agent gateway config"],
        suggested_reviewer_focus=["Error handling for failed callbacks"],
    )
    assert report.confidence == 85
    assert len(report.risks_flagged) == 1


def test_completion_report_rejects_invalid_confidence():
    import pytest

    with pytest.raises(Exception):
        CompletionReport(
            task_id="TASK-001",
            agent="dev_agent",
            status="completed",
            confidence=150,  # invalid: above 100
            output_summary="test",
        )


def test_task_step_creation():
    step = TaskStep(
        agent="product_manager",
        action="write_spec",
        description="Write feature specification",
    )
    assert step.agent == "product_manager"
    assert step.action == "write_spec"


def test_next_step_delegate():
    step = NextStep(action="delegate", agent="dev_agent", prompt="Implement feature X")
    assert step.action == "delegate"
    assert step.agent == "dev_agent"
    assert step.prompt == "Implement feature X"


def test_next_step_done():
    step = NextStep(action="done", summary="Explored the codebase, found no issues")
    assert step.action == "done"
    assert step.summary == "Explored the codebase, found no issues"


def test_next_step_escalate():
    step = NextStep(action="escalate", reason="Budget exceeds $200")
    assert step.action == "escalate"
    assert step.reason == "Budget exceeds $200"


def test_step_record():
    record = StepRecord(
        step_number=1,
        agent="dev_agent",
        action="delegate: implement feature",
        result_summary="Feature implemented with 3 tests",
        success=True,
    )
    assert record.step_number == 1
    assert record.success is True


def test_task_record_accepts_parent_task_id():
    t = TaskRecord(id="TASK-002", brief="child", parent_task_id="TASK-001")
    assert t.parent_task_id == "TASK-001"


def test_task_record_parent_defaults_to_none():
    t = TaskRecord(id="TASK-001", brief="root")
    assert t.parent_task_id is None


def test_completion_report_accepts_output_dir():
    r = CompletionReport(
        task_id="T", agent="dev_agent", status="completed",
        confidence=80, output_summary="done", output_dir="output/TASK-001",
    )
    assert r.output_dir == "output/TASK-001"


def test_completion_report_output_dir_defaults_to_none():
    r = CompletionReport(
        task_id="T", agent="dev_agent", status="completed",
        confidence=80, output_summary="done",
    )
    assert r.output_dir is None


def test_task_status_values():
    from runtime.models import TaskStatus
    assert {s.value for s in TaskStatus} == {
        "pending", "in_progress", "blocked", "completed", "failed",
        "resolved_superseded",
    }


def test_resolved_superseded_joins_every_terminal_predicate():
    """A missed terminal consumer that treats RESOLVED_SUPERSEDED as
    non-terminal (or crashes on it) is the main risk of this additive status,
    so lock the wiring across all three predicates."""
    from runtime.models import TaskStatus
    from runtime.orchestrator.run_step import TERMINAL_STATES
    from runtime.daemon.routes.tasks import _TERMINAL_TASK_STATUSES
    from runtime.daemon.org_state import OrgState
    assert TaskStatus.RESOLVED_SUPERSEDED in TERMINAL_STATES
    assert TaskStatus.RESOLVED_SUPERSEDED in _TERMINAL_TASK_STATUSES
    assert TaskStatus.RESOLVED_SUPERSEDED in OrgState._TERMINAL_STATUS_TO_EVENT


def test_block_kind_has_delegated_and_escalated():
    from runtime.models import BlockKind
    assert {b.value for b in BlockKind} == {"delegated", "escalated", "blocked_on_job"}


def test_task_record_has_new_columns():
    from runtime.models import TaskRecord
    t = TaskRecord(id="TASK-001", brief="x")
    assert t.block_kind is None
    assert t.note is None
    assert t.orchestration_step_count == 0


def test_task_record_accepts_block_kind():
    from runtime.models import TaskRecord, TaskStatus, BlockKind
    t = TaskRecord(
        id="TASK-001", brief="x",
        status=TaskStatus.BLOCKED, block_kind=BlockKind.DELEGATED,
        note="Delegated to dev_agent", orchestration_step_count=3,
    )
    assert t.block_kind == BlockKind.DELEGATED
    assert t.note == "Delegated to dev_agent"
    assert t.orchestration_step_count == 3


def test_talk_status_values():
    assert TalkStatus.OPEN.value == "open"
    assert TalkStatus.CLOSED.value == "closed"
    assert TalkStatus.ABANDONED.value == "abandoned"


def test_talk_record_defaults():
    rec = TalkRecord(id="TALK-001", agent_name="dev_agent")
    assert rec.status == TalkStatus.OPEN
    assert rec.ended_at is None
    assert rec.summary is None
    assert rec.topic_list == []
    assert rec.new_learnings_count == 0
    assert rec.new_kb_slugs == []
    assert rec.transcript_path is None


def test_script_request_status_values():
    from runtime.models import JobStatus
    assert JobStatus.PENDING == "pending"
    assert JobStatus.REJECTED == "rejected"
    assert JobStatus.RUNNING == "running"
    assert JobStatus.COMPLETED == "completed"
    assert JobStatus.FAILED == "failed"


def test_script_interpreter_values():
    from runtime.models import JobInterpreter
    assert JobInterpreter.BASH == "bash"
    assert JobInterpreter.SH == "sh"
    assert JobInterpreter.ZSH == "zsh"
    assert JobInterpreter.PYTHON3 == "python3"


def test_script_request_record_defaults():
    from runtime.models import JobRecord, JobStatus, JobInterpreter
    r = JobRecord(
        id="SR-001",
        task_id="TASK-001",
        agent_name="engineering_head",
        title="x",
        rationale="y",
        script_text="echo hi",
        interpreter=JobInterpreter.BASH,
        created_at="2026-05-23T10:00:00Z",
    )
    assert r.status == JobStatus.PENDING
    assert r.max_runtime_seconds is None  # unbounded by default
    assert r.cwd_hint is None
    assert r.exit_code is None


def test_chain_leg_roundtrip():
    leg = ChainLeg(agent="senior_dev", prompt="review", expect_verdict="APPROVE")
    assert leg.model_dump() == {
        "agent": "senior_dev",
        "prompt": "review",
        "expect_verdict": "APPROVE",
    }
    leg2 = ChainLeg(agent="qa_engineer", prompt="qa")
    assert leg2.expect_verdict is None


def test_next_step_accepts_then_and_expect_verdict():
    ns = NextStep(
        action="delegate",
        agent="dev_agent",
        prompt="build",
        expect_verdict=None,
        then=[
            {"agent": "senior_dev", "prompt": "review", "expect_verdict": "APPROVE"},
            {"agent": "qa_engineer", "prompt": "qa", "expect_verdict": "PASS"},
        ],
    )
    assert len(ns.then) == 2
    assert ns.then[0].agent == "senior_dev"
    assert ns.then[0].expect_verdict == "APPROVE"
    assert ns.then[1].expect_verdict == "PASS"


def test_next_step_then_defaults_to_empty_list():
    ns = NextStep(action="delegate", agent="dev", prompt="x")
    assert ns.then == []
    assert ns.expect_verdict is None


def test_completion_report_accepts_optional_verdict():
    r = CompletionReport(
        task_id="TASK-1",
        agent="senior_dev",
        status="completed",
        confidence=92,
        output_summary="LGTM",
        verdict="APPROVE",
    )
    assert r.verdict == "APPROVE"
    r2 = CompletionReport(
        task_id="TASK-2",
        agent="dev_agent",
        status="completed",
        confidence=80,
        output_summary="built it",
    )
    assert r2.verdict is None


def test_dream_status_values() -> None:
    from runtime.models import DreamStatus

    assert DreamStatus.PENDING.value == "pending"
    assert DreamStatus.RUNNING.value == "running"
    assert DreamStatus.COMPLETED.value == "completed"
    assert DreamStatus.FAILED.value == "failed"
    assert DreamStatus.TIMEOUT.value == "timeout"
    assert DreamStatus.SKIPPED.value == "skipped"


def test_dream_record_defaults() -> None:
    from datetime import datetime, timezone
    from runtime.models import DreamRecord, DreamStatus

    rec = DreamRecord(
        id="DREAM-001",
        agent_name="dev_agent",
        local_date="2026-06-09",
        scheduled_for=datetime(2026, 6, 9, 2, 0, tzinfo=timezone.utc),
        window_end=datetime(2026, 6, 9, 2, 0, tzinfo=timezone.utc),
    )

    assert rec.status == DreamStatus.PENDING
    assert rec.new_learnings_count == 0
    assert rec.kb_candidate_count == 0
    assert rec.founder_thread_id is None
