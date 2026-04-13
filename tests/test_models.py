from src.models import (
    AgentName,
    CompletionReport,
    PerformanceTier,
    ReviewVerdict,
    TaskRecord,
    TaskStatus,
    TaskStep,
    TaskType,
)


def test_task_status_values():
    assert TaskStatus.PENDING == "pending"
    assert TaskStatus.IN_PROGRESS == "in_progress"
    assert TaskStatus.COMPLETED == "completed"
    assert TaskStatus.IN_REVIEW == "in_review"
    assert TaskStatus.APPROVED == "approved"
    assert TaskStatus.REJECTED == "rejected"
    assert TaskStatus.ESCALATED == "escalated"


def test_task_type_values():
    assert TaskType.IMPLEMENT_FEATURE == "implement_feature"
    assert TaskType.BUG_FIX == "bug_fix"
    assert TaskType.PAYMENT_CHANGE == "payment_change"


def test_agent_name_values():
    assert AgentName.ENGINEERING_HEAD == "engineering_head"
    assert AgentName.PRODUCT_MANAGER == "product_manager"
    assert AgentName.DEV_AGENT == "dev_agent"
    assert AgentName.PAYMENT_AGENT == "payment_agent"


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
        type=TaskType.IMPLEMENT_FEATURE,
        brief="Add Alipay support",
    )
    assert record.status == TaskStatus.PENDING
    assert record.revision_count == 0
    assert record.assigned_agent is None
    assert record.crew == "product_engineering"
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
        agent=AgentName.PRODUCT_MANAGER,
        action="write_spec",
        description="Write feature specification",
    )
    assert step.agent == AgentName.PRODUCT_MANAGER
    assert step.action == "write_spec"


from src.models import NextStep, StepRecord


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


def test_task_type_general():
    from src.models import TaskType
    assert TaskType.GENERAL == "general"
