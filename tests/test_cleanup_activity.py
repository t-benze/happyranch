from __future__ import annotations

import pytest

from runtime.daemon.cleanup_activity import CleanupActivityError, validate_cleanup_activity


def _receipt(**changes):
    receipt = {
        "version": 1, "mode": "report_only", "outcome": "completed",
        "measured_before": {"available": False, "bytes": None, "inodes": None, "reason": "not_measured"},
        "measured_after": {"available": False, "bytes": None, "inodes": None, "reason": "not_measured"},
        "reclaimed_bytes": 0, "reclaimed_inodes": 0, "removal_count": 0, "skip_count": None,
        "error_summary": None, "ambiguity_summary": "ledger unavailable",
    }
    receipt.update(changes)
    return receipt


def test_cleanup_measurement_strict_types_and_bounds() -> None:
    good = _receipt(measured_before={"available": True, "bytes": 2**63 - 1, "inodes": 0, "reason": None})
    assert validate_cleanup_activity(good, callback_status="completed", trigger_mode="report_only").measured_before.bytes == 2**63 - 1
    for bad in (True, 1.0, "1", -1, 2**63):
        with pytest.raises(CleanupActivityError):
            validate_cleanup_activity(_receipt(measured_before={"available": True, "bytes": bad, "inodes": 0, "reason": None}), callback_status="completed", trigger_mode="report_only")


def test_cleanup_measurement_availability_truth_table() -> None:
    with pytest.raises(CleanupActivityError):
        validate_cleanup_activity(_receipt(measured_after={"available": False, "bytes": 0, "inodes": None, "reason": "not_measured"}), callback_status="completed", trigger_mode="report_only")


def test_cleanup_receipt_extra_keys_and_version_rejected() -> None:
    with pytest.raises(CleanupActivityError):
        validate_cleanup_activity(_receipt(version=True), callback_status="completed", trigger_mode="report_only")
    with pytest.raises(CleanupActivityError):
        validate_cleanup_activity(_receipt(extra=None), callback_status="completed", trigger_mode="report_only")


def test_report_only_zero_operations_not_zero_measurements() -> None:
    parsed = validate_cleanup_activity(_receipt(), callback_status="completed", trigger_mode="report_only")
    assert parsed.reclaimed_bytes == 0 and not parsed.measured_before.available
    with pytest.raises(CleanupActivityError):
        validate_cleanup_activity(_receipt(removal_count=None), callback_status="completed", trigger_mode="report_only")


def test_cleanup_without_ledger_keeps_totals_null() -> None:
    receipt = _receipt(mode="cleanup", reclaimed_bytes=None, reclaimed_inodes=None, removal_count=None, ambiguity_summary="ledger_unavailable")
    assert validate_cleanup_activity(receipt, callback_status="completed", trigger_mode="cleanup").reclaimed_bytes is None


def test_unverified_reclaimed_totals_rejected() -> None:
    receipt = _receipt(mode="cleanup", reclaimed_bytes=0, reclaimed_inodes=None, removal_count=None, ambiguity_summary="ledger_unavailable")
    with pytest.raises(CleanupActivityError, match="cleanup_ledger_unavailable"):
        validate_cleanup_activity(receipt, callback_status="completed", trigger_mode="cleanup")
