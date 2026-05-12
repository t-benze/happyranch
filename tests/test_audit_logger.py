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
    logger.log_session_end("TASK-001", "dev_agent", duration_seconds=120)
    logs = db.get_audit_logs("TASK-001")
    assert len(logs) == 1
    assert logs[0]["action"] == "session_end"
    assert logs[0]["payload"]["duration_seconds"] == 120
    # No token_usage provided -> token_count is null for back-compat readers.
    assert logs[0]["payload"]["token_count"] is None


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
    logger.log_completion_report(report)
    logs = db.get_audit_logs("TASK-001")
    assert len(logs) == 1
    assert logs[0]["action"] == "completion_report"
    assert logs[0]["payload"]["confidence"] == 85
    # task_results is owned by the agent callback (POST /tasks/{id}/completion);
    # log_completion_report no longer writes a row — that double-write produced
    # the duplicate task_results rows seen in TASK-137.
    assert db.get_task_results("TASK-001") == []


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


def test_log_revisit_of_records_predecessor_chain(db):
    from src.infrastructure.audit_logger import AuditLogger
    audit = AuditLogger(db)
    audit.log_revisit_of(
        task_id="TASK-072",
        predecessor_root="TASK-052",
        flagged="TASK-058",
        cascade=["TASK-052", "TASK-053", "TASK-058"],
        prior_status="failed",
        founder_note="PR #103 already merged",
    )
    logs = db.get_audit_logs("TASK-072")
    entry = next(e for e in logs if e["action"] == "revisit_of")
    assert entry["agent"] == "founder"
    assert entry["payload"]["predecessor_root"] == "TASK-052"
    assert entry["payload"]["flagged"] == "TASK-058"
    assert entry["payload"]["cascade"] == ["TASK-052", "TASK-053", "TASK-058"]
    assert entry["payload"]["prior_status"] == "failed"
    assert entry["payload"]["founder_note"] == "PR #103 already merged"


def test_log_revisit_spawned_records_new_root(db):
    from src.infrastructure.audit_logger import AuditLogger
    audit = AuditLogger(db)
    audit.log_revisit_spawned(predecessor_task_id="TASK-052", new_root="TASK-072")
    logs = db.get_audit_logs("TASK-052")
    entry = next(e for e in logs if e["action"] == "revisit_spawned")
    assert entry["agent"] == "founder"
    assert entry["payload"]["new_root"] == "TASK-072"


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


def test_log_task_dispatched_records_payload(db):
    AuditLogger(db).log_task_dispatched(
        task_id="TASK-001",
        talk_id="TALK-007",
        dispatcher_agent="dev_agent",
        dispatcher_role="worker",
        effective_target="dev_agent",
        team="engineering",
    )
    rows = db.get_audit_logs("TASK-001")
    assert len(rows) == 1
    row = rows[0]
    assert row["action"] == "task_dispatched"
    assert row["agent"] == "dev_agent"
    assert row["payload"] == {
        "talk_id": "TALK-007",
        "dispatcher_agent": "dev_agent",
        "dispatcher_role": "worker",
        "effective_target": "dev_agent",
        "team": "engineering",
    }


def test_log_escalation_notify_sent(db):
    logger = AuditLogger(db)
    logger.log_escalation_notify_sent(
        task_id="TASK-1", feishu_message_id="om_xyz",
    )
    rows = db.get_audit_logs("TASK-1")
    assert len(rows) == 1
    assert rows[0]["action"] == "escalation_notify_sent"
    assert rows[0]["payload"]["feishu_message_id"] == "om_xyz"


def test_log_escalation_notify_failed(db):
    logger = AuditLogger(db)
    logger.log_escalation_notify_failed(
        task_id="TASK-1", error="feishu send code=99991663",
    )
    rows = db.get_audit_logs("TASK-1")
    assert rows[0]["action"] == "escalation_notify_failed"
    assert rows[0]["payload"]["error"] == "feishu send code=99991663"


def test_log_escalation_reply_processed(db):
    logger = AuditLogger(db)
    logger.log_escalation_reply_processed(
        task_id="TASK-1", decision="approve", rationale="ok"
    )
    rows = db.get_audit_logs("TASK-1")
    assert rows[0]["action"] == "escalation_reply_processed"
    assert rows[0]["payload"]["decision"] == "approve"
    assert rows[0]["payload"]["rationale"] == "ok"


def test_log_escalation_reply_rejected(db):
    logger = AuditLogger(db)
    logger.log_escalation_reply_rejected(
        task_id="TASK-1", reason="bad_decision",
    )
    rows = db.get_audit_logs("TASK-1")
    assert rows[0]["action"] == "escalation_reply_rejected"
    assert rows[0]["payload"]["reason"] == "bad_decision"
    assert "text_preview" not in rows[0]["payload"]


def test_log_escalation_reply_rejected_preserves_text_preview(db):
    logger = AuditLogger(db)
    logger.log_escalation_reply_rejected(
        task_id="TASK-1",
        reason="bad_decision",
        feishu_event_id="evt_abc",
        text_preview="approve: skip device check",
    )
    payload = db.get_audit_logs("TASK-1")[0]["payload"]
    assert payload["reason"] == "bad_decision"
    assert payload["feishu_event_id"] == "evt_abc"
    assert payload["text_preview"] == "approve: skip device check"


def test_log_escalation_reply_rejected_truncates_long_text(db):
    logger = AuditLogger(db)
    long_text = "x" * 500
    logger.log_escalation_reply_rejected(
        task_id="TASK-1", reason="bad_decision", text_preview=long_text,
    )
    preview = db.get_audit_logs("TASK-1")[0]["payload"]["text_preview"]
    assert len(preview) == 200
    assert preview == "x" * 200


def test_log_parse_hint_sent(db):
    logger = AuditLogger(db)
    logger.log_parse_hint_sent(
        task_id="TASK-1",
        feishu_event_id="evt_a",
        hint_message_id="om_hint_7",
    )
    row = db.get_audit_logs("TASK-1")[0]
    assert row["action"] == "escalation_parse_hint_sent"
    assert row["payload"]["hint_message_id"] == "om_hint_7"
    assert row["payload"]["feishu_event_id"] == "evt_a"


def test_log_parse_hint_send_failed(db):
    logger = AuditLogger(db)
    logger.log_parse_hint_send_failed(
        task_id="TASK-1",
        feishu_event_id="evt_b",
        error="FeishuSendError: code=230020 msg=message_not_found",
    )
    row = db.get_audit_logs("TASK-1")[0]
    assert row["action"] == "escalation_parse_hint_send_failed"
    assert "message_not_found" in row["payload"]["error"]
    assert row["payload"]["feishu_event_id"] == "evt_b"
