"""Shared bounded categorization for terminal reply-delivery outcomes."""

from __future__ import annotations

import re


def reply_failure_category(
    db_status: str, decline_reason: str | None,
) -> str | None:
    """Return the authoritative short category for a terminal invocation."""
    if db_status == "declined":
        return "declined"
    if db_status not in ("failed", "timeout"):
        return None

    reason = (decline_reason or "").lower()
    match = re.search(r"rc=(\d+)", reason, re.IGNORECASE)
    has_infra_signature = bool(match and int(match.group(1)) != 0) or any(
        marker in reason
        for marker in ("529", "overloaded", "quota", "usage limit", "unknown_session")
    )
    if reason.startswith("no_callback_after_reprompt:"):
        return "infra_fail" if has_infra_signature else "no_callback_after_reprompt"
    if reason.startswith("no_callback:"):
        return "infra_fail" if has_infra_signature else "no_callback"
    return "infra_fail"
