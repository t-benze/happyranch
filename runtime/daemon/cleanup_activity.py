"""Strict, receipt-only validation for workspace-cleanup completion reports."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


_MAX_INT = 2**63 - 1
_REASONS = {
    "not_measured", "measurement_unavailable", "truncated", "timeout",
    "permission_denied", "unsupported_platform", "changed_during_measurement",
    "receipt_missing", "context_unavailable", "ledger_unavailable", "invalid_record",
}


class CleanupActivityError(ValueError):
    """A bounded category suitable for returning from the callback route."""


def _integer(value: Any, field: str) -> int:
    if type(value) is not int or value < 0 or value > _MAX_INT:
        raise CleanupActivityError(f"invalid_{field}")
    return value


@dataclass(frozen=True)
class CleanupMeasurement:
    available: bool
    bytes: int | None
    inodes: int | None
    reason: str | None

    @classmethod
    def parse(cls, value: Any, field: str) -> "CleanupMeasurement":
        if not isinstance(value, dict) or set(value) != {"available", "bytes", "inodes", "reason"}:
            raise CleanupActivityError(f"invalid_{field}")
        available = value["available"]
        if type(available) is not bool:
            raise CleanupActivityError(f"invalid_{field}")
        count_bytes, inodes, reason = value["bytes"], value["inodes"], value["reason"]
        if available:
            if reason is not None:
                raise CleanupActivityError(f"invalid_{field}")
            return cls(True, _integer(count_bytes, f"{field}_bytes"), _integer(inodes, f"{field}_inodes"), None)
        if count_bytes is not None or inodes is not None or not isinstance(reason, str) or reason not in _REASONS:
            raise CleanupActivityError(f"invalid_{field}")
        return cls(False, None, None, reason)


@dataclass(frozen=True)
class CleanupActivityInput:
    version: int
    mode: str
    outcome: str
    measured_before: CleanupMeasurement
    measured_after: CleanupMeasurement
    reclaimed_bytes: int | None
    reclaimed_inodes: int | None
    removal_count: int | None
    skip_count: int | None
    error_summary: str | None
    ambiguity_summary: str | None


def _summary(value: Any, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > 240 or "\n" in value or "\r" in value or any(ord(c) < 32 for c in value):
        raise CleanupActivityError(f"invalid_{field}")
    return value


def validate_cleanup_activity(value: Any, *, callback_status: str, trigger_mode: str) -> CleanupActivityInput:
    keys = {"version", "mode", "outcome", "measured_before", "measured_after", "reclaimed_bytes", "reclaimed_inodes", "removal_count", "skip_count", "error_summary", "ambiguity_summary"}
    if not isinstance(value, dict) or set(value) != keys:
        raise CleanupActivityError("invalid_cleanup_activity")
    if type(value["version"]) is not int or value["version"] != 1:
        raise CleanupActivityError("invalid_cleanup_version")
    mode, outcome = value["mode"], value["outcome"]
    if mode not in {"report_only", "cleanup"} or mode != trigger_mode:
        raise CleanupActivityError("cleanup_mode_mismatch")
    if outcome not in {"completed", "partial", "failed", "blocked"} or (callback_status in {"failed", "blocked"} and outcome == "completed"):
        raise CleanupActivityError("cleanup_outcome_mismatch")
    before = CleanupMeasurement.parse(value["measured_before"], "measured_before")
    after = CleanupMeasurement.parse(value["measured_after"], "measured_after")
    reclaim_fields = ("reclaimed_bytes", "reclaimed_inodes", "removal_count")
    if mode == "report_only":
        # This is a reported no-operation contract, deliberately distinct
        # from a claim that either measurement observed zero bytes on disk.
        if any(_integer(value[field], field) != 0 for field in reclaim_fields):
            raise CleanupActivityError("invalid_report_only_operations")
    else:
        # B2/B3 own the authoritative ledger. Until then cleanup cannot
        # persist a client supplied reclamation figure, including zero.
        if (
            any(value[field] is not None for field in reclaim_fields)
            or not isinstance(value["ambiguity_summary"], str)
            or "ledger_unavailable" not in value["ambiguity_summary"]
        ):
            raise CleanupActivityError("cleanup_ledger_unavailable")
    skip = value["skip_count"]
    if skip is not None:
        skip = _integer(skip, "skip_count")
    return CleanupActivityInput(1, mode, outcome, before, after,
        value["reclaimed_bytes"], value["reclaimed_inodes"], value["removal_count"], skip,
        _summary(value["error_summary"], "error_summary"), _summary(value["ambiguity_summary"], "ambiguity_summary"))
