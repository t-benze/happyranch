from __future__ import annotations

import hashlib

from runtime.models import CompletionReport
from runtime.orchestrator.chain import build_prior_leg_context


def test_actual_prior_leg_context_is_immediate_report_only_not_authority_snapshot() -> None:
    report = CompletionReport(task_id="TASK-1", agent="maker_a", status="completed", confidence=90, output_summary="approved r1", verdict="APPROVE", output_dir="output/TASK-1")
    context = build_prior_leg_context(child_task_id="TASK-1", report=report)
    assert "maker_a" in context and "approved r1" in context
    assert "submission_digest" not in context
    assert "authority_envelope" not in context
    assert "current_revision" not in context


def test_content_hash_compare_and_swap_has_aba_counterexample() -> None:
    initial = b"revision-a"
    expected = hashlib.sha256(initial).hexdigest()
    changed_then_restored = b"revision-a"
    assert hashlib.sha256(changed_then_restored).hexdigest() == expected
    # A matching per-file digest cannot prove no intervening writer changed it.
    assert changed_then_restored == initial
