from __future__ import annotations

from src.models import CompletionReport


def test_completion_report_default_waiting_on_job_ids_is_empty():
    """waiting_on_job_ids defaults to empty list when omitted."""
    report = CompletionReport(
        task_id="TASK-1", agent="a", status="completed",
        confidence=80, output_summary="done",
    )
    assert report.waiting_on_job_ids == []


def test_completion_report_accepts_waiting_on_job_ids():
    """waiting_on_job_ids deserializes from a list of strings."""
    report = CompletionReport(
        task_id="TASK-1", agent="a", status="blocked",
        confidence=0, output_summary="waiting",
        waiting_on_job_ids=["JOB-12", "JOB-13"],
    )
    assert report.waiting_on_job_ids == ["JOB-12", "JOB-13"]
