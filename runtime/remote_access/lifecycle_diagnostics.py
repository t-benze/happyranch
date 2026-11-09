"""Closed same-invocation start diagnostics; never retain child text or operands."""
from __future__ import annotations

import json
import os

from runtime.remote_access.package_diagnostic_codes import SITE_CATEGORIES
from runtime.remote_access.package_diagnostics import exception_refusal


START_BRANCH_CATEGORIES = dict.fromkeys((
    "request_open", "stopped_current", "eligibility_preflight", "policy_write", "manager_start", "reply",
), "managed_lifecycle_refused")
HELPER_OUTCOME_CATEGORIES = dict.fromkeys((
    "nonzero", "deadline_elapsed", "nonzero_deadline_elapsed",
), "managed_lifecycle_refused")


def registered_origin(value: object, prefixes: tuple[str, ...]) -> dict[str, str] | None:
    if type(value) is not dict or set(value) != {"category", "site"}:
        return None
    category, site = value["category"], value["site"]
    if (type(category) is not str or type(site) is not str or not site.startswith(prefixes)
            or site not in SITE_CATEGORIES or SITE_CATEGORIES[site] != category):
        return None
    return {"category": category, "site": site}


def start_refusal_projection(value: object) -> dict[str, object] | None:
    if (type(value) is not dict or not {"version", "branch"} <= set(value)
            or set(value) - {"version", "branch", "origin", "eligibility"}
            or type(value["version"]) is not int or value["version"] != 1):
        return None
    branch = value["branch"]
    if (type(branch) is not str or branch not in START_BRANCH_CATEGORIES
            or START_BRANCH_CATEGORIES[branch] != "managed_lifecycle_refused"):
        return None
    result: dict[str, object] = {"version": 1, "branch": branch}
    for name, prefixes in (("origin", ("G",)), ("eligibility", ("P", "G"))):
        if name in value:
            origin = registered_origin(value[name], prefixes)
            if origin is None or name == "eligibility" and branch not in ("eligibility_preflight", "reply"):
                return None
            result[name] = origin
    return result


def start_refusal_frame(raw: bytes) -> dict[str, object] | None:
    # This is only a diagnostic projection inside the unchanged 4096-byte
    # transport. Malformed/duplicate/partial/extra output supplies no detail.
    if len(raw) > 512:
        return None
    try:
        value = json.loads(raw)
        result = start_refusal_projection(value)
        if result is not None and raw == (json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n").encode("ascii"):
            return result
    except (ValueError, TypeError, UnicodeError, RecursionError):
        pass
    return None


def exception_start_observation(error: BaseException) -> tuple[str, dict[str, object] | None] | None:
    seen = set()
    for _ in range(16):
        if id(error) in seen:
            return None
        seen.add(id(error))
        value = getattr(error, "_managed_lifecycle_observation", None)
        if type(value) is tuple and len(value) == 2:
            outcome, detail = value
            if (type(outcome) is str and outcome in HELPER_OUTCOME_CATEGORIES
                    and HELPER_OUTCOME_CATEGORIES[outcome] == "managed_lifecycle_refused"):
                # Late child output never establishes a branch/origin.
                return outcome, start_refusal_projection(detail) if outcome == "nonzero" else None
        error = error.__cause__
        if error is None:
            break
    return None


def write_eligibility_refusal(error: BaseException) -> None:
    refusal = exception_refusal(error)
    if refusal is None:
        return
    category, site = refusal
    origin = registered_origin({"category": category, "site": site}, ("P", "G"))
    if origin is not None:
        try:
            os.write(1, (json.dumps(origin, sort_keys=True, separators=(",", ":")) + "\n").encode("ascii"))
        except (OSError, ValueError):
            pass
