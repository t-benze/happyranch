"""Lifecycle-first, provider-independent read model for Usage v1.

Lifecycle rows establish membership and denominators. Optional usage rows only
populate normalized token observations; they never create a run or choose its
executor/model cohort.
"""

from __future__ import annotations

import json
import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from runtime.infrastructure.database import Database
from runtime.orchestrator.usage_normalization import ReportedState, normalize_usage


RUN_TYPES = (
    "worker_task", "manager_decision", "thread_reply", "thread_followup", "dream",
)
TOKEN_CLASSES = ("fresh_input", "reread", "output")
SYSTEM_DECLINE_REASONS = frozenset({
    "participant_removed", "agent_terminated", "agent_unavailable",
})


@dataclass(frozen=True)
class _Window:
    name: str
    start: datetime
    end: datetime


@dataclass
class _Run:
    run_type: str | None
    agent: str
    started_at: datetime
    executor: str | None
    model: str | None
    usage: dict[str, Any] | None
    status: str | None = None
    decline_reason: str | None = None
    unattributed_kind: str | None = None


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _parse_dt(value: str | datetime | None) -> datetime | None:
    if value is None:
        return None
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace("Z", "+00:00"))
    return _utc(parsed)


def _iso(value: datetime) -> str:
    return _utc(value).isoformat().replace("+00:00", "Z")


def _windows(now: datetime) -> tuple[_Window, _Window]:
    end = _utc(now)
    return (
        _Window("current", end - timedelta(days=7), end),
        _Window("previous", end - timedelta(days=14), end - timedelta(days=7)),
    )


def _window_dict(window: _Window, timezone_name: str) -> dict[str, str]:
    try:
        display_tz = ZoneInfo(timezone_name)
    except Exception:
        display_tz = timezone.utc
    return {
        "start_utc": _iso(window.start),
        "end_utc": _iso(window.end),
        "start_local": window.start.astimezone(display_tz).isoformat(),
        "end_local": window.end.astimezone(display_tz).isoformat(),
    }


def _metadata(now: datetime, timezone_name: str, compare: bool) -> dict[str, Any]:
    current, previous = _windows(now)
    return {
        "generated_at": _iso(now),
        "data_through": _iso(now),
        "timezone": timezone_name,
        "current_window": _window_dict(current, timezone_name),
        "previous_window": _window_dict(previous, timezone_name) if compare else None,
    }


def _period(when: datetime, current: _Window, previous: _Window) -> str | None:
    if current.start <= when < current.end:
        return "current"
    if previous.start <= when < previous.end:
        return "previous"
    return None


def _payload(raw: object) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str):
        return {}
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _source(db: Database, now: datetime) -> dict[str, list[dict]]:
    current, previous = _windows(now)
    return db.query_usage_lifecycle_snapshot(
        start_utc=previous.start.isoformat(), end_utc=current.end.isoformat(),
    )


def _usage_maps(rows: Iterable[dict]) -> tuple[dict, dict, dict]:
    tasks: dict[tuple, list[dict]] = defaultdict(list)
    threads: dict[tuple, list[dict]] = defaultdict(list)
    dreams: dict[tuple, list[dict]] = defaultdict(list)
    for row in rows:
        scope_type = row.get("scope_type") or "task"
        scope_id = row.get("scope_id") or row.get("task_id")
        if scope_type == "task":
            tasks[(scope_id, row.get("agent"), row.get("session_id"))].append(row)
        elif scope_type == "thread":
            threads[(scope_id, row.get("agent"), row.get("session_id"))].append(row)
        elif scope_type == "dream":
            dreams[(scope_id, row.get("agent"))].append(row)
    return tasks, threads, dreams


def _one(candidates: Iterable[dict]) -> dict[str, Any] | None:
    by_id = {row["id"]: row for row in candidates}
    return next(iter(by_id.values())) if len(by_id) == 1 else None


def _task_start_key(row: dict) -> tuple[str, str, object]:
    session_id = _payload(row.get("payload")).get("session_id")
    return (
        row["task_id"], row["agent"],
        session_id if isinstance(session_id, str) and session_id else row["id"],
    )


def _lifecycle_runs(snapshot: dict[str, list[dict]]) -> list[_Run]:
    task_usage, thread_usage, dream_usage = _usage_maps(snapshot["usage"])
    recovery_ids = {
        (row["task_id"], row["agent"], row["recovery_session_id"])
        for row in snapshot["recoveries"]
    }
    runs: list[_Run] = []
    seen_task_starts: set[tuple[str, str, object]] = set()
    for row in snapshot["audit"]:
        when = _parse_dt(row.get("timestamp"))
        if when is None:
            continue
        payload = _payload(row.get("payload"))
        if row["action"] == "session_start":
            start_key = _task_start_key(row)
            if start_key in seen_task_starts:
                continue
            seen_task_starts.add(start_key)
            purpose = payload.get("invocation_purpose")
            run_type = {
                "worker_execution": "worker_task",
                "manager_decision": "manager_decision",
            }.get(purpose)
            sid = payload.get("session_id")
            is_recovery = (row["task_id"], row["agent"], sid) in recovery_ids
            kind = "recovery" if is_recovery else ("task_unclassified" if run_type is None else None)
            if is_recovery:
                run_type = None
            usage = _one(task_usage.get((row["task_id"], row["agent"], sid), ())) if sid else None
            executor = payload.get("executor")
            model = payload.get("model")
            runs.append(_Run(
                run_type, row["agent"], when,
                executor if isinstance(executor, str) else None,
                model if isinstance(model, str) else None,
                usage, unattributed_kind=kind,
            ))
        elif row["action"] == "dream_started":
            executor = payload.get("executor")
            model = payload.get("model")
            runs.append(_Run(
                "dream", row["agent"], when,
                executor if isinstance(executor, str) else None,
                model if isinstance(model, str) else None,
                _one(dream_usage.get((row["task_id"], row["agent"]), ())),
            ))
    for row in snapshot["threads"]:
        run_type = {"reply": "thread_reply", "task_followup": "thread_followup"}.get(row.get("purpose"))
        when = _parse_dt(row.get("started_at"))
        if run_type is None or when is None:
            continue
        candidates: list[dict] = []
        for sid in (row.get("session_id"), row.get("invocation_token")):
            if sid:
                candidates.extend(thread_usage.get((row["thread_id"], row["agent_name"], sid), ()))
        runs.append(_Run(
            run_type, row["agent_name"], when, row.get("executor"), row.get("model"),
            _one(candidates), status=row.get("status"), decline_reason=row.get("decline_reason"),
        ))
    return runs


def _empty_workload() -> dict[str, Any]:
    return {
        "task_runs": 0, "thread_wakes": 0,
        "recorded_runtime": {"seconds": 0, "known": 0, "total": 0},
        "deliveries": 0, "delivery_unclassified_results": 0, "replies": 0,
    }


def _absolute_delta(current: int | float, previous: int | float) -> dict[str, Any]:
    value = current - previous
    return {"kind": "no_change" if value == 0 else "absolute", "value": value, "withheld_reason": None}


def _task_runtime_by_start(audit_rows: list[dict]) -> dict[int, int]:
    grouped: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in audit_rows:
        if row["action"] in {"session_start", "session_end"}:
            grouped[(row["task_id"], row["agent"])].append(row)
    paired: dict[int, int] = {}
    for rows in grouped.values():
        segment: list[dict] = []
        seen: set[tuple[str, str, object]] = set()
        for row in sorted(rows, key=lambda item: item["id"]):
            if row["action"] == "session_start":
                start_key = _task_start_key(row)
                if start_key in seen:
                    continue
                seen.add(start_key)
                segment.append(row)
            else:
                if len(segment) == 1:
                    duration = _payload(row.get("payload")).get("duration_seconds")
                    if type(duration) is int:
                        paired[segment[0]["id"]] = duration
                segment = []
    return paired


def read_workload(
    db: Database, *, now: datetime, timezone_name: str, compare: bool = False,
) -> dict[str, Any]:
    """Build Workload from lifecycle starts/results without token joins."""
    snapshot = _source(db, now)
    current_window, previous_window = _windows(now)
    values: dict[str, dict[str, dict[str, Any]]] = defaultdict(
        lambda: {"current": _empty_workload(), "previous": _empty_workload()}
    )
    runtime_by_start = _task_runtime_by_start(snapshot["audit"])
    seen_task_starts: set[tuple[str, str, object]] = set()
    for row in snapshot["audit"]:
        if row["action"] != "session_start":
            continue
        start_key = _task_start_key(row)
        if start_key in seen_task_starts:
            continue
        seen_task_starts.add(start_key)
        when = _parse_dt(row.get("timestamp"))
        period = _period(when, current_window, previous_window) if when else None
        if period is None:
            continue
        target = values[row["agent"]][period]
        target["task_runs"] += 1
        target["recorded_runtime"]["total"] += 1
        if row["id"] in runtime_by_start:
            target["recorded_runtime"]["known"] += 1
            target["recorded_runtime"]["seconds"] += runtime_by_start[row["id"]]
    for row in snapshot["threads"]:
        started = _parse_dt(row.get("started_at"))
        period = _period(started, current_window, previous_window) if started else None
        if period is not None:
            target = values[row["agent_name"]][period]
            target["thread_wakes"] += 1
            target["recorded_runtime"]["total"] += 1
            consumed = _parse_dt(row.get("consumed_at"))
            if consumed is not None and consumed >= started:
                target["recorded_runtime"]["known"] += 1
                target["recorded_runtime"]["seconds"] += (consumed - started).total_seconds()
        consumed = _parse_dt(row.get("consumed_at"))
        reply_period = _period(consumed, current_window, previous_window) if consumed else None
        if reply_period is not None and row.get("purpose") == "reply" and row.get("status") == "consumed":
            values[row["agent_name"]][reply_period]["replies"] += 1
    delivered: set[tuple[str, str, str]] = set()
    purposes = {
        (row["task_id"], row["agent"], _payload(row.get("payload")).get("session_id")):
            _payload(row.get("payload")).get("invocation_purpose")
        for row in snapshot["audit"] if row["action"] == "session_start"
    }
    for row in snapshot["results"]:
        when = _parse_dt(row.get("created_at"))
        period = _period(when, current_window, previous_window) if when else None
        if period is None or row.get("status") != "completed" or row.get("task_status") != "completed":
            continue
        purpose = purposes.get((row["task_id"], row["agent"], row["session_id"]))
        if purpose == "worker_execution":
            key = (period, row["agent"], row["task_id"])
            if key not in delivered:
                values[row["agent"]][period]["deliveries"] += 1
                delivered.add(key)
        elif purpose in {None, "unattributed"}:
            values[row["agent"]][period]["delivery_unclassified_results"] += 1
    output = _metadata(now, timezone_name, compare)
    output["agents"] = []
    for agent in sorted(values):
        current = values[agent]["current"]
        previous = values[agent]["previous"]
        deltas = None
        if compare:
            deltas = {
                "task_runs": _absolute_delta(current["task_runs"], previous["task_runs"]),
                "thread_wakes": _absolute_delta(current["thread_wakes"], previous["thread_wakes"]),
                "recorded_runtime_seconds": _absolute_delta(current["recorded_runtime"]["seconds"], previous["recorded_runtime"]["seconds"]),
                "deliveries": _absolute_delta(current["deliveries"], previous["deliveries"]),
                "replies": _absolute_delta(current["replies"], previous["replies"]),
            }
        output["agents"].append({"agent": agent, "current": current, "previous": previous if compare else None, "deltas": deltas})
    return output


def _metric(values: list[int], partial_count: int = 0) -> dict[str, Any]:
    return {"value": statistics.median(values) if values else None, "n_reported": len(values), "partial_count": partial_count}


def _known_total(values: list[int]) -> dict[str, Any]:
    return {"value": sum(values) if values else None, "n_reported": len(values)}


def _efficiency_period(runs: list[_Run], run_type: str) -> dict[str, Any]:
    known = 0
    class_values: dict[str, list[int]] = {name: [] for name in TOKEN_CLASSES}
    partial_count = 0
    declines: list[_Run] = []
    declined_known = 0
    decline_values: dict[str, list[int]] = {name: [] for name in TOKEN_CLASSES}
    for run in runs:
        normalized = normalize_usage(run.usage) if run.usage is not None else None
        if normalized is not None and normalized.parseable:
            known += 1
        if normalized is not None:
            for name in TOKEN_CLASSES:
                state = getattr(normalized, name)
                if state.state is ReportedState.REPORTED and state.value is not None:
                    class_values[name].append(state.value)
            if normalized.fresh_input.partial_uncached_subtotal is not None:
                partial_count += 1
        if run_type in {"thread_reply", "thread_followup"} and run.status == "declined" and run.decline_reason not in SYSTEM_DECLINE_REASONS:
            declines.append(run)
            if normalized is not None and normalized.parseable:
                declined_known += 1
            if normalized is not None:
                for name in TOKEN_CLASSES:
                    state = getattr(normalized, name)
                    if state.state is ReportedState.REPORTED and state.value is not None:
                        decline_values[name].append(state.value)
    total = len(runs)
    decline: dict[str, Any] | None = None
    if run_type in {"thread_reply", "thread_followup"}:
        decline = {
            "state": "reported" if declines else "no_declines",
            "declined": len(declines), "total": total,
            "rate": len(declines) / total if total else None,
            "usage_known": declined_known,
            **{name: _known_total(decline_values[name]) for name in TOKEN_CLASSES},
        }
    return {
        "runs": total,
        "usage_coverage": {"known": known, "total": total, "ratio": known / total if total else None},
        "fresh_input": _metric(class_values["fresh_input"], partial_count),
        "reread": _metric(class_values["reread"]),
        "output": _metric(class_values["output"]),
        "decline_waste": decline,
    }


def _withheld(reason: str) -> dict[str, Any]:
    return {"kind": "withheld", "value": None, "withheld_reason": reason}


def _count_delta(current: int, previous: int) -> dict[str, Any]:
    if previous == 0 and current > 0:
        return {"kind": "new_from_zero", "value": current, "withheld_reason": None}
    if current == previous == 0:
        return {"kind": "no_change", "value": 0, "withheld_reason": None}
    return {"kind": "absolute", "value": current - previous, "withheld_reason": None}


def _percent_delta(current: float | int | None, previous: float | int | None) -> dict[str, Any]:
    if current is None or previous is None:
        return _withheld("invalid_baseline")
    if previous == 0:
        if current == 0:
            return {"kind": "no_change", "value": 0, "withheld_reason": None}
        return {"kind": "new_from_zero", "value": current, "withheld_reason": None}
    return {"kind": "percent", "value": ((current - previous) / previous) * 100, "withheld_reason": None}


def _row_deltas(current: dict[str, Any], previous: dict[str, Any], *, unattributed: bool) -> dict[str, Any]:
    ratios = (current["usage_coverage"]["ratio"], previous["usage_coverage"]["ratio"])
    reason = None
    if any(ratio is not None and ratio < 0.95 for ratio in ratios):
        reason = "usage_coverage_below_95_percent"
    elif unattributed:
        reason = "unattributed_lifecycle_runs"
    names = ["runs", *TOKEN_CLASSES]
    if current["decline_waste"] is not None:
        names.extend(("decline_rate", "decline_fresh_input", "decline_reread", "decline_output"))
    if reason:
        return {name: _withheld(reason) for name in names}
    deltas = {"runs": _count_delta(current["runs"], previous["runs"])}
    for name in TOKEN_CLASSES:
        deltas[name] = _percent_delta(current[name]["value"], previous[name]["value"])
    if current["decline_waste"] is not None:
        current_decline = current["decline_waste"]
        previous_decline = previous["decline_waste"]
        deltas["decline_rate"] = _percent_delta(current_decline["rate"], previous_decline["rate"])
        for name in TOKEN_CLASSES:
            deltas[f"decline_{name}"] = _percent_delta(current_decline[name]["value"], previous_decline[name]["value"])
    return deltas


def read_efficiency(
    db: Database, *, now: datetime, timezone_name: str, compare: bool = False,
    executor: str | None = None, model: str | None = None,
    model_unpinned: bool = False,
) -> dict[str, Any]:
    """Build cohort options and, when selected, five Efficiency rows."""
    snapshot = _source(db, now)
    current_window, previous_window = _windows(now)
    runs = _lifecycle_runs(snapshot)
    output = _metadata(now, timezone_name, compare)
    cohorts: dict[tuple[str, str | None], dict[str, int]] = defaultdict(lambda: {"current": 0, "previous": 0})
    keys = (*RUN_TYPES, "task_unclassified", "recovery")
    unattributed = {"current": {name: 0 for name in keys}, "previous": {name: 0 for name in keys}}
    for run in runs:
        period = _period(run.started_at, current_window, previous_window)
        if period is None:
            continue
        if run.run_type is None:
            unattributed[period][run.unattributed_kind or "task_unclassified"] += 1
        elif run.executor is None:
            unattributed[period][run.run_type] += 1
        else:
            cohorts[(run.executor, run.model)][period] += 1
    output["cohorts"] = [
        {"executor": ex, "model": cohort_model, "model_unpinned": cohort_model is None,
         "current_runs": counts["current"], "previous_runs": counts["previous"] if compare else 0}
        for (ex, cohort_model), counts in sorted(cohorts.items(), key=lambda item: (item[0][0], item[0][1] is not None, item[0][1] or ""))
        if counts["current"] > 0 or (compare and counts["previous"] > 0)
    ]
    output["unattributed"] = {"current": unattributed["current"], "previous": unattributed["previous"] if compare else None}
    output["rows"] = []
    if executor is None:
        return output
    selected_model = None if model_unpinned else model
    for run_type in RUN_TYPES:
        current_runs = [run for run in runs if run.run_type == run_type and run.executor == executor and run.model == selected_model and _period(run.started_at, current_window, previous_window) == "current"]
        previous_runs = [run for run in runs if run.run_type == run_type and run.executor == executor and run.model == selected_model and _period(run.started_at, current_window, previous_window) == "previous"]
        current_value = _efficiency_period(current_runs, run_type)
        previous_value = _efficiency_period(previous_runs, run_type)
        deltas = None
        if compare:
            could_belong = (
                unattributed["current"][run_type] > 0 or unattributed["previous"][run_type] > 0
                or (run_type in {"worker_task", "manager_decision"} and (
                    unattributed["current"]["task_unclassified"] > 0
                    or unattributed["previous"]["task_unclassified"] > 0
                ))
            )
            deltas = _row_deltas(current_value, previous_value, unattributed=could_belong)
        output["rows"].append({"run_type": run_type, "current": current_value, "previous": previous_value if compare else None, "deltas": deltas})
    return output
