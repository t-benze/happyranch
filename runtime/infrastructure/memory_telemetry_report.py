"""Pure observation-only memory report reduction; no epoch authority input.

Audit acquisition owns the org boundary. Lifecycle starts establish runtime
identities, never provider identities or a complete process-launch census.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import json

METRIC_ACTIONS = ("session_start", "memory_digest_impression", "memory_read", "memory_search")
ACTIONS = (*METRIC_ACTIONS, "session_end", "task_resumed_from_jobs",
           "memory_runtime_identity", "memory_runtime_launched", "memory_runtime_terminal")
SESSION_KINDS = ("first_launch", "job_resume", "retry", "retry_job_resume", "other_continuation", "unknown")
_TASK_PURPOSES = {"manager_decision", "worker_execution"}
_EXCLUDED_PURPOSES = {
    "thread_reply": "thread", "thread_followup": "thread", "thread_follow_up": "thread",
    "dream": "dream", "unattributed": "recovery",
}
_REASON = "Missing trusted epoch/canary authority and independently complete launch/expectation census; counts are observation-only."


class ReportAcquisitionUnavailable(RuntimeError):
    """The existing read boundary could not acquire a complete stable report."""
    category = "acquisition_unavailable"

    def __init__(self):
        super().__init__(self.category)


def aware_utc(value: str | datetime) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
    if not isinstance(parsed, datetime) or parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must be aware")
    return parsed.astimezone(timezone.utc)


def _base(now: datetime) -> dict:
    return {
        "data_through": now.isoformat(), "measurement_timezone": "UTC", "acquisition_complete": True,
        "epoch": {"status": "unversioned", "id": None, "collection_started": False, "started_at": None},
        "observation_period": {
            "first_impression_at": None, "days_elapsed": 0, "required_days": 14,
            "total_correlated_sessions": 0, "required_sessions": 500,
            "days_met": False, "sessions_met": False, "thresholds_met": False,
            "diagnostics_valid_for_collection": False,
            "status": "insufficient_instrumentation", "reason_code": "missing_authority",
        },
        "instrumentation_health": {}, "aggregate": {}, "by_agent": {}, "by_role": {},
        "read_counts": {}, "excluded": {}, "diagnostic_errors": {}, "by_session_kind": {},
        "session_lifecycle": {"status": "unavailable", "reason_code": "missing_session_evidence"},
        "decision": "insufficient_instrumentation", "evaluation_candidate": False, "decision_detail": _REASON,
    }


def _error(now: datetime, errors: Counter) -> dict:
    report = _base(now)
    code = sorted(errors)[0]
    report["observation_period"]["reason_code"] = code
    report["instrumentation_health"] = {"status": "unhealthy", "malformed_records": sum(errors.values()), "reason_code": code}
    report["diagnostic_errors"] = dict(sorted(errors.items()))
    report["decision_detail"] = f"Malformed {code.removeprefix('malformed_').replace('_', ' ')} evidence; collection is not eligible."
    return report


def _text(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _ids(value) -> bool:
    return isinstance(value, list) and all(_text(mid) for mid in value)


def _payload(value):
    if isinstance(value, str):
        value = json.loads(value, parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite JSON")))
    if not isinstance(value, dict):
        raise ValueError("payload must be an object")
    json.dumps(value, allow_nan=False)
    return value


def _session_diagnostics(tables: dict | None, keys: set[tuple], now: datetime,
                         *, since: datetime | None = None, excluded_tasks: set[str] | None = None) -> tuple[dict, dict, set[int]]:
    """Join existing runtime facts; audit IDs establish actual retirement order.

    A SID-less end belongs only to a unique task/agent interval. An unfinished
    interval is lawful only with the snapshot's exact current task/agent/SID.
    This is observational correlation, not caller authentication or a writer.
    """
    kinds, rejected = {}, set()
    counts = {kind: {"sessions": 0, "credited_reads": 0, "credited_searches": 0,
                     "post_retirement_reads": 0, "post_retirement_searches": 0,
                     "unknown_sessions": 0, "active_sessions": 0} for kind in SESSION_KINDS}
    errors, flags = Counter(), Counter()
    def unavailable(reason: str) -> tuple[dict, dict, set[int]]:
        for kind, values in counts.items():
            values.update(sessions=0, unknown_sessions=0, active_sessions=0, credited_reads=0, credited_searches=0,
                          status="unknown" if kind == "unknown" and keys else "no_observed_population",
                          post_retirement_reads=None, post_retirement_searches=None)
        counts["unknown"].update(sessions=len(keys), unknown_sessions=len(keys))
        return {}, {"status": "unavailable", "reason_code": reason, "by_kind": counts,
                    "errors": {reason: 1}}, rejected
    if tables is None:
        return unavailable("missing_session_evidence")
    try:
        tasks = {}
        for task in tables["tasks"]:
            if not _text(task.get("id")) or task["id"] in tasks:
                raise ValueError("task identity")
            tasks[task["id"]] = task
        rows, seen = [], set()
        for raw in tables["audit_log"]:
            if type(raw.get("id")) is not int or raw["id"] <= 0 or raw["id"] in seen:
                raise ValueError("audit identity")
            seen.add(raw["id"])
            timestamp = aware_utc(raw["timestamp"])
            if timestamp >= now or raw["action"] not in ACTIONS:
                continue
            rows.append({**raw, "payload": _payload(raw["payload"]), "time": timestamp})
        rows.sort(key=lambda row: row["id"])
        starts, all_starts = defaultdict(list), defaultdict(list)
        phases, resumes, ends = defaultdict(list), defaultdict(list), []
        for row in rows:
            p, action = row["payload"], row["action"]
            key = (row["agent"], row["task_id"], p.get("session_id"))
            if action == "session_start":
                all_starts[key].append(row)
                if p.get("invocation_purpose") in _TASK_PURPOSES:
                    starts[key].append(row)
            elif action.startswith("memory_runtime_"):
                phases[(p.get("boot_id"), p.get("ordinal"))].append(row)
            elif action == "task_resumed_from_jobs":
                resumes[row["task_id"]].append(row)
            elif action == "session_end":
                ends.append(row)
        identities = defaultdict(list)
        for group in phases.values():
            for row in group:
                if row["action"] == "memory_runtime_identity":
                    identities[(row["agent"], row["task_id"], row["payload"].get("session_id"))].append(row)
        # Include credited old/unknown tuples in the measured window: dropping
        # them because they are not a new epoch invocation would hide damage.
        selected = set(keys)
        operations = []
        excluded_tasks = excluded_tasks or set()
        for row in rows:
            if row["action"] not in {"memory_read", "memory_search"} or (since is not None and row["time"] < since):
                continue
            p = row["payload"]
            tid = p.get("task_id") if row["action"] == "memory_read" else row["task_id"]
            sid = p.get("session_id")
            if row["action"] == "memory_read" and bool(tid) != bool(sid):
                errors["partial_memory_attribution"] += 1
            if not tid or not sid or tid in excluded_tasks:
                continue
            key = (row["agent"], tid, sid)
            # Thread/dream/recovery identity never creates a natural population.
            own = identities.get(key, [])
            if own and all(r["payload"].get("invocation_purpose") not in _TASK_PURPOSES for r in own):
                continue
            selected.add(key)
            operations.append((key, row))
        selected = {key for key in selected if key[1] not in excluded_tasks}
        ordered_tasks = defaultdict(list)
        for key, values in starts.items():
            ordered_tasks[key[1]].extend((row, key) for row in values)
        for values in ordered_tasks.values():
            values.sort(key=lambda item: item[0]["id"])

        intervals, unknown, by_owner = {}, set(), defaultdict(list)
        # Excluded recovery/thread/dream intervals also own SID-less ends.
        # Resolve ownership before selecting natural populations; excluded
        # starts never enter their classification history or source validation.
        for key, values in all_starts.items():
            own = identities.get(key, [])
            terminal = []
            if len(own) == 1:
                p = own[0]["payload"]
                group = phases[(p.get("boot_id"), p.get("ordinal"))]
                terminal = [r for r in group if r["action"] == "memory_runtime_terminal"]
            intervals[key] = {"start": values[0], "terminal": terminal, "ends": []}
            by_owner[key[:2]].append(key)
        for end in ends:
            owner = (end["agent"], end["task_id"])
            candidates = [key for key in by_owner[owner]
                          if intervals[key]["start"]["id"] < end["id"]
                          and (not intervals[key]["terminal"] or end["id"] < intervals[key]["terminal"][0]["id"])]
            if len(candidates) == 1:
                intervals[candidates[0]]["ends"].append(end)
            elif set(by_owner[owner]) & selected:
                errors["ambiguous_session_end"] += 1
                unknown.update(set(candidates or by_owner[owner]) & selected)

        retirement = {}
        for key in sorted(selected):
            agent, tid, sid = key
            task, own_starts = tasks.get(tid), starts.get(key, [])
            kind = "unknown"
            if task is None or "revisit_of_task_id" not in task or len(own_starts) != 1:
                errors["missing_task_or_start"] += 1
                unknown.add(key)
            else:
                start = own_starts[0]
                history = ordered_tasks[tid]
                prior = [row for row, _ in history if row["id"] < start["id"]]
                prior_id = prior[-1]["id"] if prior else None
                events = [row for row in resumes[tid] if (prior_id or 0) < row["id"] < start["id"]]
                job_resume = bool(prior and len(events) == 1)
                event = events[0]["payload"] if len(events) == 1 else {}
                job_ids, outcomes = event.get("blocking_job_ids"), event.get("job_outcomes")
                if events and (not prior or len(events) != 1 or not _ids(job_ids) or not job_ids
                               or len(job_ids) != len(set(job_ids)) or not isinstance(outcomes, dict)
                               or set(outcomes) != set(job_ids) or not all(_text(value) for value in outcomes.values())):
                    errors["ambiguous_job_resume"] += 1
                    unknown.add(key)
                revisit = task["revisit_of_task_id"]
                lineage, cursor = set(), task
                while cursor.get("revisit_of_task_id") is not None:
                    target = cursor["revisit_of_task_id"]
                    if not _text(target) or target in lineage or target == tid or target not in tasks:
                        errors["missing_or_cyclic_revisit"] += 1
                        unknown.add(key)
                        break
                    lineage.add(target)
                    cursor = tasks[target]
                    if "revisit_of_task_id" not in cursor:
                        errors["missing_or_cyclic_revisit"] += 1
                        unknown.add(key)
                        break
                kind = ("retry_job_resume" if job_resume else "retry") if revisit is not None else (
                    "job_resume" if job_resume else "other_continuation" if prior else "first_launch")
                if prior and not job_resume:
                    flags["later_session_without_job_resume"] += 1
            kinds[key] = kind
            counts[kind]["sessions"] += 1
            own = identities.get(key, [])
            interval = intervals.get(key)
            if len(own) != 1 or interval is None:
                errors["missing_runtime_identity"] += 1
                unknown.add(key)
                continue
            identity, p = own[0], own[0]["payload"]
            group = phases[(p.get("boot_id"), p.get("ordinal"))]
            launched = [r for r in group if r["action"] == "memory_runtime_launched"]
            terminal = interval["terminal"]
            if (not _text(p.get("boot_id")) or type(p.get("ordinal")) is not int or p["ordinal"] <= 0
                    or not launched or len(terminal) > 1
                    or any((r["agent"], r["task_id"], r["payload"].get("session_id")) != key
                           or r["payload"].get("task_id") != tid or r["payload"].get("agent") != agent for r in group)
                    or not identity["id"] < interval["start"]["id"] < launched[0]["id"]
                    or [r["payload"].get("callback_count") for r in launched] != list(range(1, len(launched) + 1))
                    or (terminal and (terminal[0]["id"] <= launched[-1]["id"]
                                      or terminal[0]["payload"].get("launched_callbacks") != len(launched)
                                      or type(terminal[0]["payload"].get("success")) is not bool))):
                errors["misjoined_runtime_interval"] += 1
                unknown.add(key)
            end_rows = interval["ends"]
            if len(end_rows) > 1:
                errors["ambiguous_session_end"] += 1
                unknown.add(key)
            boundaries = end_rows + terminal
            if boundaries:
                retirement[key] = min(r["id"] for r in boundaries)
            if terminal and len(end_rows) != 1:
                errors["missing_session_end"] += 1
                unknown.add(key)
            if not terminal:
                if (end_rows or task is None or task.get("status") != "in_progress"
                        or task.get("assigned_agent") != agent or task.get("current_session_id") != sid):
                    errors["missing_retirement_evidence"] += 1
                    unknown.add(key)
                else:
                    counts[kind]["active_sessions"] += 1
            # Supersession cannot transfer an old SID's credit to a later start.
            later = [row["id"] for row, _ in ordered_tasks[tid] if row["id"] > interval["start"]["id"]]
            if later:
                retirement[key] = min(retirement.get(key, later[0]), later[0])
        for key, row in operations:
            kind = kinds[key]
            suffix = "reads" if row["action"] == "memory_read" else "searches"
            counts[kind][f"credited_{suffix}"] += 1
            if key not in intervals or row["id"] <= intervals[key]["start"]["id"]:
                errors["operation_outside_session"] += 1
                unknown.add(key)
                rejected.add(row["id"])
            if key in retirement and row["id"] > retirement[key]:
                counts[kind][f"post_retirement_{suffix}"] += 1
                rejected.add(row["id"])
        for key in unknown:
            counts[kinds[key]]["unknown_sessions"] += 1
        violations = sum(c["post_retirement_reads"] + c["post_retirement_searches"] for c in counts.values())
        if violations:
            errors["credited_after_retirement"] += violations
        for values in counts.values():
            # Absence of a population/evidence is not an observed healthy zero.
            values["status"] = ("no_observed_population" if not values["sessions"] else
                                "unhealthy" if values["post_retirement_reads"] or values["post_retirement_searches"] else
                                "unknown" if values["unknown_sessions"] else "observed")
            if not values["sessions"] or values["unknown_sessions"]:
                for field in ("post_retirement_reads", "post_retirement_searches"):
                    if not values[field]:
                        values[field] = None
        return kinds, {"status": "unhealthy" if violations else "unknown" if errors else "observed" if selected else "no_observed_population",
                       "reason_code": "credited_after_retirement" if violations else "session_evidence_unknown" if errors else "observed",
                       "sessions": len(selected), "by_kind": counts, "errors": dict(errors), "flags": dict(flags)}, rejected
    except (KeyError, TypeError, ValueError, OverflowError):
        return unavailable("malformed_session_evidence")


def reduce_report(
    impression_rows: list[dict], read_rows: list[dict], search_rows: list[dict],
    agent_role_map: dict[str, str] | None = None, current_time: datetime | None = None,
    *, session_start_rows: list[dict] | None = None,
    evidence_tables: dict | None = None, collection_since: datetime | None = None,
    excluded_tasks: set[str] | None = None,
) -> dict:
    """Validate every stream before reducing; legacy helper calls stay closed."""
    now = aware_utc(current_time if current_time is not None else datetime.now(timezone.utc))
    errors = Counter()
    parsed = {action: [] for action in METRIC_ACTIONS}
    inputs = (session_start_rows if session_start_rows is not None else [], impression_rows, read_rows, search_rows)
    labels = ("session_start", "impression", "read", "search")
    for action, rows, label in zip(METRIC_ACTIONS, inputs, labels):
        if not isinstance(rows, list):
            errors[f"malformed_{label}"] += 1
            continue
        for row in rows:
            if not isinstance(row, dict):
                errors[f"malformed_{label}"] += 1
                continue
            try:
                if not isinstance(row.get("timestamp"), str):
                    raise ValueError("timestamp")
                timestamp = aware_utc(row["timestamp"])
            except (TypeError, ValueError, OverflowError):
                errors[f"malformed_{label}_timestamp"] += 1
                continue
            if timestamp >= now:
                continue
            try:
                p = _payload(row.get("payload"))
                if not _text(row.get("agent")) or not _text(row.get("task_id")):
                    raise ValueError("envelope")
                if "agent" in p and p["agent"] != row["agent"]:
                    raise ValueError("agent")
                # Missing optional legacy identity is classifiable, blank or wrong
                # types on a present identity are structural corruption.
                for key in ("session_id", "task_id"):
                    if key in p and p[key] is not None and not _text(p[key]):
                        raise ValueError(key)
                if action == "session_start":
                    purpose = p.get("invocation_purpose")
                    if purpose is not None and not _text(purpose):
                        raise ValueError("purpose")
                    if purpose in _TASK_PURPOSES and not _text(p.get("session_id")):
                        raise ValueError("task session")
                elif action == "memory_digest_impression":
                    if not _text(p.get("session_id")) or not _ids(p.get("digest_ids")):
                        raise ValueError("impression")
                    if "digest_count" in p and (type(p["digest_count"]) is not int or p["digest_count"] != len(p["digest_ids"])):
                        raise ValueError("count")
                    if any(k in p for k in ("memory_telemetry_version", "pointer_ids", "full_body_ids")):
                        from runtime.infrastructure.learnings_store import ID_RE
                        if "digest_count" not in p:
                            raise ValueError("exposure count")
                        if type(p.get("memory_telemetry_version")) is not int or p["memory_telemetry_version"] != 1:
                            raise ValueError("version")
                        for ids in (p["digest_ids"], p.get("pointer_ids"), p.get("full_body_ids")):
                            if not _ids(ids) or len(ids) != len(set(ids)) or any(ID_RE.fullmatch(mid) is None for mid in ids):
                                raise ValueError("exposure IDs")
                        if set(p["pointer_ids"]) & set(p["full_body_ids"]) or set(p["pointer_ids"]) | set(p["full_body_ids"]) != set(p["digest_ids"]):
                            raise ValueError("exposure union")
                elif action == "memory_read":
                    if not _text(p.get("id")):
                        raise ValueError("read id")
                elif action == "memory_search":
                    if not _ids(p.get("memory_ids")) or any(type(p.get(k)) is not int or p[k] < 0 for k in ("hit_count", "kb_hit_count")):
                        raise ValueError("search")
                parsed[action].append({**row, "payload": p, "time": timestamp})
            except (KeyError, TypeError, ValueError, OverflowError):
                errors[f"malformed_{label}"] += 1
    # Continue across well-formed rows so a structural failure in one stream
    # cannot hide a task-source error in another acquired stream.

    # SID is checked for ambiguity but never used as the credit key.
    starts = {}
    sid_bindings = {}
    excluded = Counter({"manual": 0, "thread": 0, "dream": 0, "recovery": 0, "legacy": 0})
    for row in parsed["session_start"]:
        p = row["payload"]
        sid = p.get("session_id")
        if not sid:
            excluded["legacy"] += 1
            continue
        key = (row["agent"], row["task_id"], sid)
        if sid in sid_bindings and sid_bindings[sid] != key:
            errors["ambiguous_session_binding"] += 1
        sid_bindings[sid] = key
        purpose = p.get("invocation_purpose")
        if key in starts and starts[key]["payload"].get("invocation_purpose") != purpose:
            errors["ambiguous_session_binding"] += 1
        if key not in starts or row["time"] < starts[key]["time"]:
            starts[key] = row
    task_starts = {k: v for k, v in starts.items() if v["payload"].get("invocation_purpose") in _TASK_PURPOSES}
    kinds, lifecycle, retired_operations = _session_diagnostics(
        evidence_tables, set(task_starts), now, since=collection_since, excluded_tasks=excluded_tasks)
    rejection = Counter()
    rejected_reads = 0

    def binding(row, action):
        if row.get("id") in retired_operations:
            return None, "rejected_attribution"
        p = row["payload"]
        sid = p.get("session_id")
        tid = p.get("task_id") if action == "memory_read" else row["task_id"]
        key = (row["agent"], tid, sid)
        known = starts.get(sid_bindings.get(sid))
        if known is not None:
            purpose = known["payload"].get("invocation_purpose")
            if purpose not in _TASK_PURPOSES:
                return None, _EXCLUDED_PURPOSES.get(purpose, "legacy")
        if not sid or not tid:
            return None, "manual" if row["task_id"] == f"AGENT-{row['agent']}" else "legacy"
        if key not in task_starts:
            # Unversioned impressions without any lifecycle start are legacy;
            # claimed task reads/searches cannot create a runtime invocation.
            if action == "memory_digest_impression" and known is None:
                return None, "legacy"
            return None, "rejected_attribution"
        if action == "memory_read" and row["task_id"] != f"AGENT-{row['agent']}":
            return None, "rejected_attribution"
        if action != "memory_read" and p.get("task_id", tid) != tid:
            return None, "rejected_attribution"
        if action != "memory_digest_impression" and row["time"] < task_starts[key]["time"]:
            return None, "rejected_attribution"
        return key, None

    impressions = defaultdict(list)
    searches = defaultdict(list)
    reads = []
    for action in METRIC_ACTIONS[1:]:
        for row in parsed[action]:
            key, why = binding(row, action)
            if why:
                if why == "rejected_attribution":
                    rejection[why] += 1
                    if action == "memory_read":
                        rejected_reads += 1
                else:
                    excluded[why] += 1
                    excluded[f"{why}_{action.removeprefix('memory_')}"] += 1
                continue
            if action == "memory_read":
                source = row["payload"].get("source")
                if not isinstance(source, str):
                    errors["malformed_read_source_type"] += 1
                elif source not in {"digest", "search", "explicit_or_other"}:
                    errors["invalid_source"] += 1
                else:
                    reads.append((key, row))
            elif action == "memory_search":
                searches[key].append(row)
            elif row["payload"]["digest_ids"]:
                impressions[key].append(row)
    if errors:
        return _error(now, errors)

    shown, pointers, bodies, unknown = {}, {}, {}, set()
    for key, rows in impressions.items():
        shown[key] = set().union(*(set(r["payload"]["digest_ids"]) for r in rows))
        if any("memory_telemetry_version" not in r["payload"] for r in rows):
            unknown.add(key)
        pointers[key] = set().union(*(set(r["payload"].get("pointer_ids", [])) for r in rows))
        bodies[key] = set().union(*(set(r["payload"].get("full_body_ids", [])) for r in rows))
        # Conflicting modes across repeated snapshots cannot invent opportunities.
        if pointers[key] & bodies[key]:
            errors["malformed_impression"] += 1
    if errors:
        return _error(now, errors)

    valid_reads = []
    pointer_reads = set()
    sourced = {s: set() for s in ("digest", "search", "explicit_or_other")}
    for key, row in reads:
        p, mid = row["payload"], row["payload"]["id"]
        # Audit ID resolves equal timestamp causality when acquisition supplies it.
        def precedes(other):
            return other["time"] < row["time"] or (other["time"] == row["time"] and other.get("id", 0) <= row.get("id", 0))
        was_shown = mid in shown.get(key, set())
        exposed_before = any(mid in r["payload"]["digest_ids"] and precedes(r) for r in impressions.get(key, []))
        found_before = any(mid in r["payload"]["memory_ids"] and precedes(r) for r in searches.get(key, []))
        source = p["source"]
        if ((source == "digest" and not exposed_before)
                or (source == "search" and (was_shown or not found_before))
                or (source == "explicit_or_other" and (exposed_before or found_before))):
            rejection["source_contradiction"] += 1
            rejected_reads += 1
            continue
        pair = (*key, mid)
        sourced[source].add(pair)
        valid_reads.append((key, mid))
        if key not in unknown and mid in pointers.get(key, set()) and exposed_before:
            pointer_reads.add(pair)

    def metrics(keys):
        keys = set(keys)
        q = keys & impressions.keys()
        ps = {k for k in q if pointers[k] and k not in unknown}
        rp = {pair for values in sourced.values() for pair in values if pair[:3] in keys}
        ops = [(k, m) for k, m in valid_reads if k in keys]
        x = {p for p in pointer_reads if p[:3] in keys}
        s = {p for p in sourced["search"] if p[:3] in keys}
        n = {p for p in s if p[3] not in shown.get(p[:3], set())}
        known = not bool(q & unknown)
        pn = sum(len(pointers[k]) for k in q) if known else None
        bn = sum(len(bodies[k]) for k in q) if known else None
        activated = len({p[:3] for p in x})
        return {
            "correlated_sessions": len(q), "pointer_opportunities": pn,
            "full_body_exposures": bn, "pointer_pairs_read": len(x) if known else None,
            "digest_pull_through": len(x) / pn if pn else None,
            "search_sourced_reads": len(s), "search_sourced_absent_from_digest": len(n),
            "search_absent_fraction": len(n) / len(s) if s else None,
            "distinct_valid_read_pairs": len(rp), "read_operations": len(ops),
            "pointer_sessions_activated": activated if known else None,
            "pointer_sessions": len(ps) if known else None,
            "session_activation": activated / len(ps) if known and ps else None,
        }

    report = _base(now)
    aggregate = metrics(task_starts)
    aggregate.update({
        "digest_sourced_read_pairs": len(sourced["digest"]), "explicit_read_pairs": len(sourced["explicit_or_other"]),
        "untrusted_task_reads": rejected_reads,
        "eligible_functional_agents": 0, "eligible_pointer_agents": 0, "eligible_agents_below_10_percent": 0,
    })
    report["aggregate"] = aggregate
    report["session_lifecycle"] = lifecycle
    for kind in SESSION_KINDS:
        values = lifecycle.get("by_kind", {}).get(kind, {})
        report["by_session_kind"][kind] = {
            **metrics(k for k in task_starts if kinds.get(k, "unknown") == kind),
            **values, "diagnostic_only": True,
        }
    if agent_role_map is not None and (not isinstance(agent_role_map, dict) or any(
        not _text(agent) or not _text(role) for agent, role in agent_role_map.items()
    )):
        agent_role_map = None
    roles = agent_role_map or {}
    for agent in sorted({k[0] for k in task_starts}):
        data = metrics(k for k in task_starts if k[0] == agent)
        report["by_agent"][agent] = {"role": roles.get(agent), "eligible": False, "activation_vote_eligible": False, **data}
    for role in sorted({roles[k[0]] for k in task_starts if k[0] in roles}):
        data = metrics(k for k in task_starts if roles.get(k[0]) == role)
        report["by_role"][role] = {**data, "retrieval_corroboration_eligible": False, "descriptive_only_for_activation_majority": True}
    counts = defaultdict(lambda: defaultdict(Counter))
    pairs = set(valid_reads)
    for key, mid in valid_reads:
        counts[key[0]][mid]["operations"] += 1
    for key, mid in pairs:
        counts[key[0]][mid]["distinct_pairs"] += 1
    report["read_counts"] = {a: {m: dict(sorted(c.items())) for m, c in sorted(ms.items())} for a, ms in sorted(counts.items())}
    first = min((r["time"] for rows in impressions.values() for r in rows), default=None)
    days = 0
    if first is not None:
        anchor = first.replace(hour=0, minute=0, second=0, microsecond=0)
        if first != anchor:
            anchor += timedelta(days=1)
        days = max(0, (now.replace(hour=0, minute=0, second=0, microsecond=0) - anchor).days)
    report["observation_period"].update({
        "first_impression_at": first.isoformat() if first else None, "days_elapsed": days,
        "total_correlated_sessions": len(impressions), "days_met": days >= 14, "sessions_met": len(impressions) >= 500,
    })
    report["instrumentation_health"] = {
        "status": "unhealthy" if rejection else "unavailable", "population": "audited intended task-session invocations",
        "audited_task_starts": len(task_starts), "intended_task_launches": None,
        "expected_nonempty_launches": None, "matching_exposures": len(impressions),
        "unknown_exposures": len(unknown) + len(task_starts.keys() - impressions.keys()), "validated_read_operations": len(valid_reads),
        "rejected_task_attribution": sum(rejection.values()), "malformed_records": 0,
        "complete_launch_census": "UNKNOWN", "probe_health": "UNKNOWN", "epoch_health": "UNKNOWN",
        "roles_available": agent_role_map is not None, "reason_code": "missing_authority",
        "duplicate_impressions": sum(len(rs) - 1 for rs in impressions.values()),
        "repeated_read_pairs": len(valid_reads) - len(pairs),
    }
    report["excluded"] = dict(sorted(excluded.items()))
    report["diagnostic_errors"] = dict(sorted(rejection.items()))
    if lifecycle["status"] in {"unknown", "unavailable", "unhealthy"}:
        report["diagnostic_errors"].update(lifecycle["errors"])
        report["instrumentation_health"].update(status=lifecycle["status"], reason_code=lifecycle["reason_code"])
    return report


def reduce_collection_report(tables: dict, view: dict, outputs: dict, agent_role_map: dict[str, str] | None = None,
                             current_time: datetime | None = None) -> dict:
    """Consume raw acquired facts, revalidate authority, then reduce natural rows."""
    from runtime.infrastructure.memory_collection import (
        AcceptanceUnavailable, CONTROL_ACTIONS, _decoded_tables, _strict_json,
        collection_control_head, collection_logical_key, parse_acceptance,
        validate_epoch_candidate, _boot_boundary,
    )
    now = aware_utc(current_time if current_time is not None else datetime.now(timezone.utc))
    rows = tables["audit_log"]
    def diagnostic() -> dict:
        # Historical lifecycle/proof rows are dependencies, not extra natural
        # observations. Current calls on old tuples remain in the diagnostic.
        since = aware_utc(_boot_boundary(view, [row for row in _decoded_tables(tables)["audit_log"]
                                               if row["action"] == "memory_runtime_intent"]))
        activity = [row for row in rows if aware_utc(row["timestamp"]) >= since]
        return reduce_report(*([row for row in activity if row["action"] == action] for action in METRIC_ACTIONS[1:]),
                             agent_role_map, now, session_start_rows=[row for row in activity if row["action"] == "session_start"],
                             evidence_tables=tables, collection_since=since)
    if not any(row["action"] in CONTROL_ACTIONS for row in rows):
        return diagnostic()
    report = None
    try:
        data = _decoded_tables(tables)
        rows = data["audit_log"]
        all_controls = [row for row in rows if row["action"] in CONTROL_ACTIONS]
        controls = [row for row in all_controls if aware_utc(row["timestamp"]) < now]
        if controls and controls[-1]["id"] != all_controls[-1]["id"]:
            raise AcceptanceUnavailable("epoch_control_after_cutoff")
        if not controls:
            return diagnostic()
        latest = controls[-1]["payload"]
        root = latest["operational_root_task_id"]
        head = collection_control_head(controls, view["org"], root)
        if head["action"] != "memory_collection_epoch_started" or head["payload"]["boot_id"] != view["boot_id"]:
            raise AcceptanceUnavailable("epoch_invalidated_or_boot_changed")
        body = head["payload"]
        prepared = validate_epoch_candidate(tables, head, view, outputs, current_time=now)
        roles = {member["agent"]: member["role"] for member in view["installed_identity"]["cohort"]}
        if agent_role_map is not None and agent_role_map != roles:
            raise AcceptanceUnavailable("epoch_role_drift")
        started = aware_utc(head["timestamp"])
        intents = {row["payload"]["ordinal"]: row for row in rows if row["action"] == "memory_runtime_intent"
                   and row["payload"].get("boot_id") == view["boot_id"]
                   and row["payload"]["ordinal"] > body["base_assigned_intents"]
                   and started <= aware_utc(row["payload"]["entered_at"]) < now
                   and not row["payload"].get("recovery")
                   and row["task_id"] not in prepared["synthetic_task_ids"]}
        keys = {(row["agent"], row["task_id"], row["payload"]["session_id"]) for row in rows
                if row["action"] == "memory_runtime_identity" and row["payload"].get("boot_id") == view["boot_id"]
                and row["payload"].get("ordinal") in intents}
        known_keys = {(row["agent"], row["task_id"], row["payload"].get("session_id")) for row in rows
                      if row["action"] == "memory_runtime_identity" and row["payload"].get("boot_id") == view["boot_id"]}
        excluded_keys = {(row["agent"], row["task_id"], row["payload"].get("session_id")) for row in rows
                         if row["action"] == "session_start" and row["payload"].get("invocation_purpose") not in _TASK_PURPOSES}
        streams = {action: [] for action in METRIC_ACTIONS}
        for row in rows:
            if row["action"] not in streams:
                continue
            payload = row["payload"]
            task_id = payload.get("task_id") if row["action"] == "memory_read" else row["task_id"]
            key = (row["agent"], task_id, payload.get("session_id"))
            if key in keys:
                streams[row["action"]].append(row)
            elif started <= aware_utc(row["timestamp"]) < now:
                if key in excluded_keys or (row["action"] == "memory_read" and not payload.get("session_id") and not task_id) or (
                        row["action"] == "memory_search" and not payload.get("session_id")
                        and not payload.get("task_id") and row["task_id"] == f"AGENT-{row['agent']}"):
                    streams[row["action"]].append(row)
                elif payload.get("session_id") and task_id and key not in known_keys:
                    raise AcceptanceUnavailable("epoch_unknown_task_tuple")
        # Old tuples omitted from qualifying streams remain visible to the
        # retirement check through the complete evidence_tables below.
        report = reduce_report(streams["memory_digest_impression"], streams["memory_read"], streams["memory_search"],
                               roles, now, session_start_rows=streams["session_start"], evidence_tables=tables,
                               collection_since=started, excluded_tasks=prepared["synthetic_task_ids"])
        if report["diagnostic_errors"] or report["aggregate"].get("pointer_opportunities") is None:
            raise AcceptanceUnavailable("epoch_natural_evidence_unhealthy")
        report["epoch"] = {"status": "accepted", "id": body["epoch_id"], "collection_started": True,
                           "started_at": head["timestamp"], "audit_id": head["id"]}
        obs = report["observation_period"]
        first = aware_utc(obs["first_impression_at"]) if obs["first_impression_at"] else None
        anchor = max(started, first) if first is not None else None
        complete_start = anchor.replace(hour=0, minute=0, second=0, microsecond=0) if anchor else None
        if anchor is not None and anchor != complete_start:
            complete_start += timedelta(days=1)
        days = max(0, (now.replace(hour=0, minute=0, second=0, microsecond=0) - complete_start).days) if complete_start else 0
        obs.update(days_elapsed=days, days_met=days >= 14, diagnostics_valid_for_collection=True,
                   thresholds_met=days >= 14 and obs["sessions_met"], reason_code="sample" )
        health = report["instrumentation_health"]
        health.update(status="healthy", unknown_exposures=0, intended_task_launches=len(intents),
                      expected_nonempty_launches=sum(row["payload"].get("state") == "nonempty" for row in rows
                          if row["action"] == "memory_runtime_expectation" and row["payload"].get("ordinal") in intents
                          and row["payload"].get("boot_id") == view["boot_id"]),
                      complete_launch_census="PASS", probe_health="PASS", epoch_health="PASS", reason_code="healthy",
                      epoch_id=body["epoch_id"], epoch_audit_id=head["id"], qa_ref=body["qa_ref"])
        eligible = []
        for agent, values in report["by_agent"].items():
            values["eligible"] = roles.get(agent) in {"manager", "worker"} and values["correlated_sessions"] >= 30
            values["activation_vote_eligible"] = values["eligible"] and bool(values["pointer_opportunities"])
            if values["activation_vote_eligible"]:
                eligible.append(values)
        low = sum(values["digest_pull_through"] < .10 for values in eligible)
        aggregate = report["aggregate"]
        aggregate.update(eligible_functional_agents=sum(values["eligible"] for values in report["by_agent"].values()),
                         eligible_pointer_agents=len(eligible), eligible_agents_below_10_percent=low)
        corroboration = {}
        for role, values in report["by_role"].items():
            qualified = {agent for agent, item in report["by_agent"].items() if item["role"] == role and item["eligible"]}
            qualified_report = reduce_report(*([row for row in streams[action] if row["agent"] in qualified] for action in METRIC_ACTIONS[1:]),
                                              roles, now, session_start_rows=[row for row in streams["session_start"] if row["agent"] in qualified])
            qualified_metrics = qualified_report["aggregate"]
            values["retrieval_corroboration_eligible"] = qualified_metrics["search_sourced_reads"] >= 30
            corroboration[role] = qualified_metrics["search_absent_fraction"]
        if not obs["thresholds_met"] or not eligible:
            decision = "insufficient_sample"
            obs["reason_code"] = "sample" if not obs["thresholds_met"] else "functional_population"
        elif aggregate["digest_pull_through"] is not None and aggregate["digest_pull_through"] < .10 and low * 2 > len(eligible):
            decision = "activation_loss"
        elif aggregate["search_absent_fraction"] is not None and aggregate["search_absent_fraction"] > .25 and any(
                item["retrieval_corroboration_eligible"] and corroboration[role] is not None and corroboration[role] > .25
                for role, item in report["by_role"].items()):
            decision = "retrieval_loss"
        else:
            decision = "no_demonstrated_problem"
        report.update(decision=decision, evaluation_candidate=decision in {"activation_loss", "retrieval_loss"},
                      decision_detail="Evaluation only; no tuning is executed." if decision in {"activation_loss", "retrieval_loss"}
                      else "Healthy collection; sample or functional evidence is insufficient." if decision == "insufficient_sample"
                      else "No demonstrated memory problem.")
        obs["status"] = decision
        return report
    except Exception:
        report = report if report is not None else diagnostic()
        reason = (report["session_lifecycle"]["reason_code"]
                  if report["session_lifecycle"]["status"] in {"unknown", "unavailable", "unhealthy"}
                  else "collection_authority_unavailable")
        report.update(epoch=_base(now)["epoch"], decision="insufficient_instrumentation", evaluation_candidate=False,
                      decision_detail="Collection evidence is unavailable or unhealthy; explicit reset rules still apply.")
        report["instrumentation_health"].update(status="unavailable", reason_code=reason,
                                                complete_launch_census="UNKNOWN", probe_health="UNKNOWN", epoch_health="UNKNOWN")
        report["observation_period"].update(status="insufficient_instrumentation", thresholds_met=False, diagnostics_valid_for_collection=False,
                                            reason_code=reason)
        return report
