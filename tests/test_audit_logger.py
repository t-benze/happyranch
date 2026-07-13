import pytest

from runtime.infrastructure.audit_logger import AuditLogger
from runtime.infrastructure.database import Database
from runtime.models import CompletionReport


def test_log_memory_lifecycle_changed(db):
    """THR-032 P3a: log_memory_lifecycle_changed records correct row shape."""
    logger = AuditLogger(db)
    logger.log_memory_lifecycle_changed(
        agent="dev_agent",
        id="MEM-001",
        from_lifecycle="valid",
        to_lifecycle="evicted",
        reason="superseded by MEM-002",
        source="manual",
    )
    logs = db.get_audit_logs("AGENT-dev_agent")
    assert len(logs) == 1
    assert logs[0]["action"] == "memory_lifecycle_changed"
    assert logs[0]["agent"] == "dev_agent"
    payload = logs[0]["payload"]
    assert payload["id"] == "MEM-001"
    assert payload["from_lifecycle"] == "valid"
    assert payload["to_lifecycle"] == "evicted"
    assert payload["reason"] == "superseded by MEM-002"
    assert payload["source"] == "manual"


def test_log_memory_read(db):
    """THR-091 WS-C: log_memory_read records correct row shape."""
    logger = AuditLogger(db)
    logger.log_memory_read(
        agent="dev_agent",
        id="MEM-001",
        slug="base-fact",
    )
    logs = db.get_audit_logs("AGENT-dev_agent")
    assert len(logs) == 1
    assert logs[0]["action"] == "memory_read"
    assert logs[0]["agent"] == "dev_agent"
    assert logs[0]["task_id"] == "AGENT-dev_agent"
    payload = logs[0]["payload"]
    assert payload["id"] == "MEM-001"
    assert payload["slug"] == "base-fact"


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


def test_log_fanout_spawned_writes_correct_shape(db):
    """log_fanout_spawned records correct row shape with children_ids."""
    from runtime.infrastructure.audit_logger import AuditLogger
    logger = AuditLogger(db)
    logger.log_fanout_spawned(
        task_id="TASK-FANOUT-S",
        agent="engineering_head",
        width=3,
        children_ids=["TASK-C1", "TASK-C2", "TASK-C3"],
    )
    rows = db.get_audit_logs("TASK-FANOUT-S")
    assert len(rows) == 1
    assert rows[0]["action"] == "fanout_spawned"
    assert rows[0]["agent"] == "engineering_head"
    payload = rows[0]["payload"]
    assert payload["width"] == 3
    assert payload["children_ids"] == ["TASK-C1", "TASK-C2", "TASK-C3"]


def test_log_fanout_join_writes_correct_shape(db):
    """log_fanout_join records correct row shape with context_markdown."""
    from runtime.infrastructure.audit_logger import AuditLogger
    logger = AuditLogger(db)
    markdown = "=== FAN-OUT JOIN CONTEXT ===\nAll children terminal.\n======"
    logger.log_fanout_join(
        task_id="TASK-FANOUT-J",
        width=2,
        children_ids=["TASK-CA", "TASK-CB"],
        context_markdown=markdown,
    )
    rows = db.get_audit_logs("TASK-FANOUT-J")
    assert len(rows) == 1
    assert rows[0]["action"] == "fanout_join"
    assert rows[0]["agent"] == "orchestrator"
    payload = rows[0]["payload"]
    assert payload["width"] == 2
    assert payload["children_ids"] == ["TASK-CA", "TASK-CB"]
    assert "FAN-OUT JOIN CONTEXT" in payload["context_markdown"]


def test_compute_memory_pull_through(db):
    """THR-091 WS-C: pull-through view computes read count from digest pointers."""
    logger = AuditLogger(db)
    # Seed: 5 memory_read events across 3 unique entries
    logger.log_memory_read(agent="dev_agent", id="MEM-001", slug="a")
    logger.log_memory_read(agent="dev_agent", id="MEM-002", slug="b")
    logger.log_memory_read(agent="dev_agent", id="MEM-001", slug="a")  # duplicate read
    logger.log_memory_read(agent="dev_agent", id="MEM-003", slug="c")
    logger.log_memory_read(agent="qa_engineer", id="MEM-004", slug="d")  # different agent
    # Digest contained MEM-001, MEM-002, MEM-005 (3 pointers, 2 read)
    result = logger.compute_memory_pull_through(
        agent="dev_agent",
        digest_ids={"MEM-001", "MEM-002", "MEM-005"},
    )
    assert result["digest_count"] == 3
    assert result["read_count"] == 2
    assert result["pull_through"] == pytest.approx(2 / 3)
    assert set(result["read_ids"]) == {"MEM-001", "MEM-002"}
    assert result["unread_ids"] == ["MEM-005"]
