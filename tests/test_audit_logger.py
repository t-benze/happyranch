from src.infrastructure.audit_logger import AuditLogger
from src.infrastructure.database import Database
from src.models import CompletionReport


def test_log_session_start(db):
    logger = AuditLogger(db)
    logger.log_session_start("TASK-001", "dev_agent", "/tmp/workspace")
    logs = db.get_audit_logs("TASK-001")
    assert len(logs) == 1
    assert logs[0]["action"] == "session_start"
    assert logs[0]["payload"]["workspace"] == "/tmp/workspace"


def test_log_session_end(db):
    logger = AuditLogger(db)
    logger.log_session_end("TASK-001", "dev_agent", duration_seconds=120, token_count=5000)
    logs = db.get_audit_logs("TASK-001")
    assert len(logs) == 1
    assert logs[0]["action"] == "session_end"
    assert logs[0]["payload"]["duration_seconds"] == 120


def test_log_completion_report(db):
    logger = AuditLogger(db)
    report = CompletionReport(
        task_id="TASK-001",
        agent="dev_agent",
        status="completed",
        confidence=85,
        output_summary="Implemented feature",
        risks_flagged=["sandbox mismatch"],
    )
    logger.log_completion_report(report, session_id="sess-abc", duration_seconds=120, token_count=5000, estimated_cost=0.15)
    logs = db.get_audit_logs("TASK-001")
    assert len(logs) == 1
    assert logs[0]["action"] == "completion_report"
    results = db.get_task_results("TASK-001")
    assert len(results) == 1
    assert results[0]["confidence_score"] == 85
    assert results[0]["duration_seconds"] == 120


def test_log_review_verdict(db):
    logger = AuditLogger(db)
    logger.log_review_verdict(
        task_id="TASK-001",
        reviewer="engineering_head",
        verdict="approve",
        feedback=None,
        reviewed_agent="dev_agent",
    )
    logs = db.get_audit_logs("TASK-001")
    assert len(logs) == 1
    assert logs[0]["action"] == "review_verdict"
    assert logs[0]["payload"]["verdict"] == "approve"
    assert logs[0]["payload"]["reviewed_agent"] == "dev_agent"


def test_log_escalation(db):
    logger = AuditLogger(db)
    logger.log_escalation(
        task_id="TASK-001",
        agent="dev_agent",
        reason="Max revision rounds exceeded",
    )
    logs = db.get_audit_logs("TASK-001")
    assert len(logs) == 1
    assert logs[0]["action"] == "escalation"
    assert "Max revision" in logs[0]["payload"]["reason"]


def test_log_cross_audit_stub(db):
    logger = AuditLogger(db)
    logger.log_cross_audit_stub("TASK-001", "payment_change")
    logs = db.get_audit_logs("TASK-001")
    assert len(logs) == 1
    assert logs[0]["action"] == "cross_audit_requested"
    assert logs[0]["payload"]["auto_approved"] is True


def test_log_orchestration_step(db):
    logger = AuditLogger(db)
    logger.log_orchestration_step("TASK-001", step_number=1, decision={
        "action": "delegate",
        "agent": "dev_agent",
        "prompt": "Implement feature",
    })
    logs = db.get_audit_logs("TASK-001")
    assert len(logs) == 1
    assert logs[0]["action"] == "orchestration_step"
    assert logs[0]["payload"]["step_number"] == 1
    assert logs[0]["payload"]["decision"]["action"] == "delegate"


def test_log_escalation_resolved_persists_decision_and_rationale(db):
    logger = AuditLogger(db)
    logger.log_escalation_resolved(
        task_id="TASK-042",
        decision="approve",
        rationale="Refund justified: vendor error per partner-log",
    )
    rows = db.get_audit_logs("TASK-042")
    matches = [r for r in rows if r["action"] == "escalation_resolved"]
    assert len(matches) == 1
    assert matches[0]["payload"]["decision"] == "approve"
    assert "Refund justified" in matches[0]["payload"]["rationale"]
    assert matches[0]["agent"] == "founder"


def test_log_talk_started(db):
    AuditLogger(db).log_talk_started("TALK-001", "dev_agent", resumed_from=None)
    rows = db.get_audit_logs("TALK-001")
    assert len(rows) == 1
    assert rows[0]["action"] == "talk_started"
    assert rows[0]["agent"] == "dev_agent"
    assert rows[0]["payload"] == {"resumed_from": None}


def test_log_talk_resumed(db):
    AuditLogger(db).log_talk_resumed("TALK-001", "dev_agent")
    rows = db.get_audit_logs("TALK-001")
    assert rows[0]["action"] == "talk_resumed"


def test_log_talk_abandoned(db):
    AuditLogger(db).log_talk_abandoned("TALK-001", "dev_agent", reason="orphan_at_new_start")
    rows = db.get_audit_logs("TALK-001")
    assert rows[0]["action"] == "talk_abandoned"
    assert rows[0]["payload"]["reason"] == "orphan_at_new_start"


def test_log_talk_ended(db):
    AuditLogger(db).log_talk_ended(
        "TALK-001",
        "dev_agent",
        new_learnings_count=2,
        new_kb_slugs=["alipay-refund"],
    )
    rows = db.get_audit_logs("TALK-001")
    assert rows[0]["action"] == "talk_ended"
    assert rows[0]["payload"]["new_learnings_count"] == 2
    assert rows[0]["payload"]["new_kb_slugs"] == ["alipay-refund"]
