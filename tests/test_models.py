from src.models import (
    CompletionReport,
    NextStep,
    PerformanceTier,
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


def test_performance_tier_values():
    assert PerformanceTier.GREEN == "green"
    assert PerformanceTier.YELLOW == "yellow"
    assert PerformanceTier.RED == "red"


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


def test_completion_report_accepts_artifact_dir():
    r = CompletionReport(
        task_id="TASK-001", agent="dev_agent", status="completed",
        confidence=80, output_summary="done", artifact_dir="artifacts/TASK-001",
    )
    assert r.artifact_dir == "artifacts/TASK-001"


def test_completion_report_artifact_defaults_to_none():
    r = CompletionReport(
        task_id="T", agent="a", status="completed", confidence=0, output_summary="",
    )
    assert r.artifact_dir is None


def test_task_status_has_five_values():
    from src.models import TaskStatus
    assert {s.value for s in TaskStatus} == {
        "pending", "in_progress", "blocked", "completed", "failed",
    }


def test_block_kind_has_delegated_and_escalated():
    from src.models import BlockKind
    assert {b.value for b in BlockKind} == {"delegated", "escalated"}


def test_task_record_has_new_columns():
    from src.models import TaskRecord
    t = TaskRecord(id="TASK-001", brief="x")
    assert t.block_kind is None
    assert t.note is None
    assert t.orchestration_step_count == 0


def test_task_record_accepts_block_kind():
    from src.models import TaskRecord, TaskStatus, BlockKind
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
    from src.models import JobStatus
    assert JobStatus.PENDING == "pending"
    assert JobStatus.REJECTED == "rejected"
    assert JobStatus.RUNNING == "running"
    assert JobStatus.COMPLETED == "completed"
    assert JobStatus.FAILED == "failed"


def test_script_interpreter_values():
    from src.models import JobInterpreter
    assert JobInterpreter.BASH == "bash"
    assert JobInterpreter.SH == "sh"
    assert JobInterpreter.ZSH == "zsh"
    assert JobInterpreter.PYTHON3 == "python3"


def test_script_request_record_defaults():
    from src.models import JobRecord, JobStatus, JobInterpreter
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
    assert r.timeout_seconds == 300
    assert r.cwd_hint is None
    assert r.exit_code is None
