from runtime.infrastructure.audit_logger import AuditLogger
from runtime.infrastructure.database import Database
from runtime.models import CompletionReport


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
    from runtime.infrastructure.audit_logger import AuditLogger
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
    from runtime.infrastructure.audit_logger import AuditLogger
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


def test_log_thread_message_sent_with_attachments(db) -> None:
    logger = AuditLogger(db)

    logger.log_thread_message_sent(
        "THR-001",
        seq=3,
        speaker="founder",
        kind="message",
        attachment_names=["a.pdf", "b.csv"],
    )

    rows = db.get_audit_logs("THR-001")
    assert rows[0]["action"] == "thread_message_sent"
    assert rows[0]["payload"]["attachment_count"] == 2
    assert rows[0]["payload"]["attachment_names"] == ["a.pdf", "b.csv"]


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


def test_log_job_submitted(db):
    from runtime.infrastructure.audit_logger import AuditLogger
    audit = AuditLogger(db)
    audit.log_job_submitted(
        task_id="TASK-001",
        job_id="SR-001",
        agent="engineering_head",
        title="x",
        interpreter="bash",
        cwd_hint="repos/web-app",
        byte_size=42,
        line_count=2,
    )
    logs = db.get_audit_logs("TASK-001")
    actions = [e["action"] for e in logs]
    assert "job_submitted" in actions
    payload = next(e["payload"] for e in logs if e["action"] == "job_submitted")
    # payload may be a JSON string or already-parsed dict; handle both like other tests.
    import json
    if isinstance(payload, str):
        payload = json.loads(payload)
    assert payload["script_request_id"] == "SR-001"
    assert payload["title"] == "x"


def test_log_job_run_completed(db):
    from runtime.infrastructure.audit_logger import AuditLogger
    audit = AuditLogger(db)
    audit.log_job_run_completed(
        task_id="TASK-001",
        job_id="SR-001",
        exit_code=0,
        duration_ms=1500,
        stdout_bytes=12,
        stderr_bytes=0,
        truncated_stdout=False,
        truncated_stderr=False,
    )
    logs = db.get_audit_logs("TASK-001")
    import json
    payload_entry = next(e for e in logs if e["action"] == "job_run_completed")
    payload = payload_entry["payload"]
    if isinstance(payload, str):
        payload = json.loads(payload)
    assert payload["exit_code"] == 0
    assert payload["duration_ms"] == 1500
    assert payload["script_request_id"] == "SR-001"


def test_log_job_notify_sent_records_payload(db):
    audit = AuditLogger(db)
    audit.log_job_notify_sent(
        task_id="TASK-91", job_id="SR-019", feishu_message_id="om_abc",
    )
    rows = db.get_audit_logs("TASK-91")
    assert len(rows) == 1
    r = rows[0]
    assert r["action"] == "job_notify_sent"
    assert r["agent"] == "daemon"
    assert r["payload"]["script_request_id"] == "SR-019"
    assert r["payload"]["feishu_message_id"] == "om_abc"


def test_log_job_notify_failed_records_error(db):
    audit = AuditLogger(db)
    audit.log_job_notify_failed(
        task_id="TASK-91", job_id="SR-019", error="ConnectionRefused: feishu",
    )
    rows = db.get_audit_logs("TASK-91")
    r = rows[0]
    assert r["action"] == "job_notify_failed"
    assert r["payload"]["script_request_id"] == "SR-019"
    assert r["payload"]["error"] == "ConnectionRefused: feishu"


def test_log_job_reply_processed_carries_decision_and_rationale(db):
    audit = AuditLogger(db)
    audit.log_job_reply_processed(
        job_id="SR-019", task_id="TASK-91",
        decision="approve", rationale="merge-close approved",
        feishu_event_id="evt_1",
    )
    rows = db.get_audit_logs("TASK-91")
    r = rows[0]
    assert r["action"] == "job_reply_processed"
    assert r["agent"] == "founder"
    assert r["payload"]["decision"] == "approve"
    assert r["payload"]["rationale"] == "merge-close approved"
    assert r["payload"]["script_request_id"] == "SR-019"
    assert r["payload"]["feishu_event_id"] == "evt_1"


def test_log_job_reply_rejected_records_reason(db):
    audit = AuditLogger(db)
    audit.log_job_reply_rejected(
        job_id="SR-019", task_id="TASK-91",
        reason="verb_mismatch", feishu_event_id="evt_1",
        text_preview="REVISIT please",
    )
    rows = db.get_audit_logs("TASK-91")
    r = rows[0]
    assert r["action"] == "job_reply_rejected"
    assert r["agent"] == "daemon"
    assert r["payload"]["reason"] == "verb_mismatch"
    assert r["payload"]["text_preview"] == "REVISIT please"


def test_log_job_reply_rejected_truncates_long_text(db):
    audit = AuditLogger(db)
    long_text = "x" * 500
    audit.log_job_reply_rejected(
        job_id="SR-019", task_id="TASK-91",
        reason="bad_decision", text_preview=long_text,
    )
    preview = db.get_audit_logs("TASK-91")[0]["payload"]["text_preview"]
    assert len(preview) == 200
    assert preview == "x" * 200


def test_log_job_reply_processed_omits_feishu_event_id_when_absent(db):
    audit = AuditLogger(db)
    audit.log_job_reply_processed(
        job_id="SR-019", task_id="TASK-91",
        decision="approve", rationale="merge-close approved",
    )
    payload = db.get_audit_logs("TASK-91")[0]["payload"]
    assert "feishu_event_id" not in payload
    assert payload["decision"] == "approve"


def test_log_job_reply_rejected_omits_optional_fields_when_absent(db):
    audit = AuditLogger(db)
    audit.log_job_reply_rejected(
        job_id="SR-019", task_id="TASK-91",
        reason="verb_mismatch",
    )
    payload = db.get_audit_logs("TASK-91")[0]["payload"]
    assert "feishu_event_id" not in payload
    assert "text_preview" not in payload
    assert payload["reason"] == "verb_mismatch"


def test_log_job_run_result_notify_sent(db):
    audit = AuditLogger(db)
    audit.log_job_run_result_notify_sent(
        job_id="SR-019", task_id="TASK-91",
        parent_message_id="om_root", follow_up_message_id="om_followup",
        status="completed",
    )
    rows = db.get_audit_logs("TASK-91")
    r = rows[0]
    assert r["action"] == "job_run_result_notify_sent"
    assert r["payload"]["parent_message_id"] == "om_root"
    assert r["payload"]["follow_up_message_id"] == "om_followup"
    assert r["payload"]["status"] == "completed"


def test_log_job_run_result_notify_failed(db):
    audit = AuditLogger(db)
    audit.log_job_run_result_notify_failed(
        job_id="SR-019", task_id="TASK-91",
        error="Timeout", status="failed",
    )
    rows = db.get_audit_logs("TASK-91")
    r = rows[0]
    assert r["action"] == "job_run_result_notify_failed"
    assert r["payload"]["error"] == "Timeout"
    assert r["payload"]["status"] == "failed"


def test_log_artifact_put_writes_event(db) -> None:
    logger = AuditLogger(db)
    logger.log_artifact_put(name="report.pdf", size_bytes=11, agent="dev_agent")

    rows = db.get_audit_logs_by_action("artifact_put")
    assert len(rows) == 1
    row = rows[0]
    assert row["task_id"] == "artifact:report.pdf"  # namespaced to avoid collision with TASK-/TALK-/SR- ids
    assert row["agent"] == "dev_agent"
    assert row["action"] == "artifact_put"
    assert row["payload"] == {"name": "report.pdf", "size_bytes": 11}


def test_log_artifact_delete_writes_event(db) -> None:
    logger = AuditLogger(db)
    logger.log_artifact_delete(name="report.pdf", agent="dev_agent")

    rows = db.get_audit_logs_by_action("artifact_delete")
    assert len(rows) == 1
    row = rows[0]
    # Mirrors log_artifact_put's exact row shape: same artifact:<name>
    # namespacing prefix, a new action string, payload carries the name.
    assert row["task_id"] == "artifact:report.pdf"
    assert row["agent"] == "dev_agent"
    assert row["action"] == "artifact_delete"
    assert row["payload"] == {"name": "report.pdf"}


def test_log_artifact_put_does_not_collide_with_task_id(tmp_path) -> None:
    """Artifact names like 'TASK-123' must NOT pollute task-scoped audit history."""
    from runtime.infrastructure.database import Database
    from runtime.infrastructure.audit_logger import AuditLogger

    db = Database(tmp_path / "test.db")
    logger = AuditLogger(db)

    # Write a session_start row for a real task
    logger.log_session_start("TASK-123", "dev_agent", "/some/workspace")
    # Upload an artifact whose name collides with the task id
    logger.log_artifact_put(name="TASK-123", size_bytes=42, agent="dev_agent")

    # get_audit_logs("TASK-123") must return ONLY the task's row, not the artifact
    rows = db.get_audit_logs("TASK-123")
    actions = [r["action"] for r in rows]
    assert actions == ["session_start"]

    # The artifact audit is under the namespaced scope
    artifact_rows = db.get_audit_logs("artifact:TASK-123")
    assert len(artifact_rows) == 1
    assert artifact_rows[0]["action"] == "artifact_put"


def test_log_chain_auto_advance_writes_expected_payload(db):
    from runtime.infrastructure.audit_logger import AuditLogger
    logger = AuditLogger(db)
    logger.log_chain_auto_advance(
        parent_task_id="TASK-1",
        leg_index=2,
        spawned_child_id="TASK-3",
        triggering_child_id="TASK-2",
        triggering_verdict="APPROVE",
        chain_origin_step_audit_id=4521,
    )
    rows = db.get_audit_logs("TASK-1")
    assert len(rows) == 1
    assert rows[0]["action"] == "chain_auto_advance"
    assert rows[0]["agent"] == "orchestrator"
    payload = rows[0]["payload"]
    assert payload["leg_index"] == 2
    assert payload["spawned_child_id"] == "TASK-3"
    assert payload["triggering_child_id"] == "TASK-2"
    assert payload["triggering_verdict"] == "APPROVE"
    assert payload["chain_origin_step_audit_id"] == 4521


def test_log_agent_session_reused_and_evicted(tmp_path):
    from runtime.infrastructure.database import Database
    from runtime.infrastructure.audit_logger import AuditLogger

    db = Database(tmp_path / "happyranch.db")
    audit = AuditLogger(db)

    audit.log_agent_session_reused(
        "THR-001", agent_name="alice", executor="claude",
        agent_session_id="sess-abc", triggering_seq=4,
    )
    audit.log_agent_session_evicted_fallback(
        "THR-001", agent_name="alice", executor="claude",
        stale_session_id="sess-old", error="No conversation found",
    )

    rows = db.get_audit_logs("THR-001")
    actions = {r["action"] for r in rows}
    assert "agent_session_reused" in actions
    assert "agent_session_evicted_fallback" in actions
    reused = next(r for r in rows if r["action"] == "agent_session_reused")
    assert reused["payload"]["agent_session_id"] == "sess-abc"
    assert reused["payload"]["triggering_seq"] == 4
    assert reused["payload"]["executor"] == "claude"
