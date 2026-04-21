from __future__ import annotations

from src.infrastructure.database import Database
from src.models import CompletionReport


class AuditLogger:
    def __init__(self, db: Database) -> None:
        self._db = db

    def log_session_start(self, task_id: str, agent: str, workspace: str) -> None:
        self._db.insert_audit_log(
            task_id=task_id,
            agent=agent,
            action="session_start",
            payload={"workspace": workspace},
        )

    def log_session_end(
        self,
        task_id: str,
        agent: str,
        duration_seconds: int,
        token_count: int | None = None,
    ) -> None:
        self._db.insert_audit_log(
            task_id=task_id,
            agent=agent,
            action="session_end",
            payload={
                "duration_seconds": duration_seconds,
                "token_count": token_count,
            },
        )

    def log_completion_report(
        self,
        report: CompletionReport,
        session_id: str,
        duration_seconds: int,
        token_count: int | None = None,
        estimated_cost: float | None = None,
    ) -> None:
        self._db.insert_audit_log(
            task_id=report.task_id,
            agent=report.agent,
            action="completion_report",
            payload=report.model_dump(),
        )
        self._db.insert_task_result(
            task_id=report.task_id,
            agent=report.agent,
            session_id=session_id,
            status=report.status,
            output_summary=report.output_summary,
            confidence_score=report.confidence,
            risks_flagged=report.risks_flagged,
            duration_seconds=duration_seconds,
            token_count=token_count,
            estimated_cost=estimated_cost,
            artifact_dir=report.artifact_dir,
        )

    def log_review_verdict(
        self,
        task_id: str,
        reviewer: str,
        verdict: str,
        feedback: str | None,
        reviewed_agent: str | None = None,
    ) -> None:
        self._db.insert_audit_log(
            task_id=task_id,
            agent=reviewer,
            action="review_verdict",
            payload={
                "verdict": verdict,
                "feedback": feedback,
                "reviewed_agent": reviewed_agent,
            },
        )

    def log_escalation(self, task_id: str, agent: str, reason: str) -> None:
        self._db.insert_audit_log(
            task_id=task_id,
            agent=agent,
            action="escalation",
            payload={"reason": reason},
        )

    def log_escalation_resolved(
        self, task_id: str, decision: str, rationale: str
    ) -> None:
        self._db.insert_audit_log(
            task_id=task_id,
            agent="founder",
            action="escalation_resolved",
            payload={"decision": decision, "rationale": rationale},
        )

    def log_task_cancelled(
        self, task_id: str, rationale: str, cascade: bool,
    ) -> None:
        self._db.insert_audit_log(
            task_id=task_id,
            agent="founder",
            action="task_cancelled",
            payload={"rationale": rationale, "cascade": cascade},
        )

    def log_cross_audit_stub(self, task_id: str, task_type: str) -> None:
        self._db.insert_audit_log(
            task_id=task_id,
            agent="orchestrator",
            action="cross_audit_requested",
            payload={
                "task_type": task_type,
                "auto_approved": True,
                "note": "Cross-audit stubbed -- Compliance Agent review pending Ops Team implementation",
            },
        )

    def log_orchestration_step(
        self, task_id: str, step_number: int, decision: dict
    ) -> None:
        self._db.insert_audit_log(
            task_id=task_id,
            agent="orchestrator",
            action="orchestration_step",
            payload={"step_number": step_number, "decision": decision},
        )

    # NOTE: audit_log.task_id is NOT NULL. Talk events reuse that column as a
    # generic "scope id" and store the talk id (TALK-NNN). Readers that filter
    # by talk id pass it in place of task_id.

    def log_talk_started(
        self, talk_id: str, agent_name: str, resumed_from: str | None,
    ) -> None:
        self._db.insert_audit_log(
            task_id=talk_id,
            agent=agent_name,
            action="talk_started",
            payload={"resumed_from": resumed_from},
        )

    def log_talk_resumed(self, talk_id: str, agent_name: str) -> None:
        self._db.insert_audit_log(
            task_id=talk_id,
            agent=agent_name,
            action="talk_resumed",
            payload={},
        )

    def log_talk_abandoned(
        self, talk_id: str, agent_name: str, reason: str,
    ) -> None:
        self._db.insert_audit_log(
            task_id=talk_id,
            agent=agent_name,
            action="talk_abandoned",
            payload={"reason": reason},
        )

    def log_talk_ended(
        self,
        talk_id: str,
        agent_name: str,
        new_learnings_count: int,
        new_kb_slugs: list[str],
    ) -> None:
        self._db.insert_audit_log(
            task_id=talk_id,
            agent=agent_name,
            action="talk_ended",
            payload={
                "new_learnings_count": new_learnings_count,
                "new_kb_slugs": new_kb_slugs,
            },
        )
