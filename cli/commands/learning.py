"""Per-agent learnings commands."""
from __future__ import annotations

import argparse
import json
import sys

from cli import _shared
from cli._shared import _ok, resolve_org_slug
from cli.client.client import DaemonNotRunning, DaemonStateInconsistent, OpcClient


def cmd_learning(args: argparse.Namespace) -> None:
    """Agent callback: append a learning to the agent's learnings.md."""
    if not args.org:
        print("error: --org <slug> is required for agent callbacks", file=sys.stderr)
        sys.exit(1)
    try:
        client = OpcClient.from_env()
    except (DaemonNotRunning, DaemonStateInconsistent) as exc:
        print(f"Error: {exc}")
        sys.exit(1)
    r = client.post(
        f"/api/v1/orgs/{args.org}/agents/{args.agent}/memory",
        json={"session_id": args.session_id, "task_id": args.task_id, "text": args.text},
    )
    if not _ok(r):
        return



def _read_yaml_payload(path: str) -> dict:
    import yaml
    with open(path) as f:
        data = yaml.safe_load(f)
    if data is None:
        return {}
    if not isinstance(data, dict):
        print(
            f"error: payload file must be a YAML mapping, got {type(data).__name__}",
            file=sys.stderr,
        )
        sys.exit(1)
    return data



def _learning_client() -> OpcClient:
    """Return an OpcClient, exiting with a friendly message if the daemon is down."""
    try:
        return OpcClient.from_env()
    except (DaemonNotRunning, DaemonStateInconsistent) as exc:
        print(f"Error: {exc}")
        sys.exit(1)



def cmd_learning_list(args: argparse.Namespace) -> None:
    client = _learning_client()
    org = resolve_org_slug(args_org=args.org, available=_shared._fetch_available_orgs(client))
    params: dict = {}
    if args.topic:
        params["topic"] = args.topic
    if args.tag:
        params["tag"] = args.tag
    if args.promoted:
        params["promoted"] = True
    elif args.not_promoted:
        params["promoted"] = False
    r = client.get(f"/api/v1/orgs/{org}/agents/{args.agent}/memory/entries/", params=params)
    if not _ok(r):
        return
    entries = r.json().get("entries", [])
    if args.json:
        import json
        print(json.dumps(entries, indent=2))
        return
    if not entries:
        print("(no learnings)")
        return
    for e in entries:
        tags = ", ".join(e.get("tags", []))
        promo = f" ↗ {e['promoted_to']}" if e.get("promoted_to") else ""
        print(f"  {e['id']}  [{e['topic']}] {e['title']}  ({tags}){promo}")



def cmd_learning_get(args: argparse.Namespace) -> None:
    client = _learning_client()
    org = resolve_org_slug(args_org=args.org, available=_shared._fetch_available_orgs(client))
    params: dict = {}
    if getattr(args, "session_id", None):
        params["session_id"] = args.session_id
    r = client.get(
        f"/api/v1/orgs/{org}/agents/{args.agent}/memory/entries/{args.id_or_slug}",
        params=params or None,
    )
    if not _ok(r):
        return
    entry = r.json()
    if args.json:
        import json
        print(json.dumps(entry, indent=2))
        return
    print(f"# {entry['title']}\n")
    print(f"id: {entry['id']}  slug: {entry['slug']}  topic: {entry['topic']}")
    if entry.get("tags"):
        print(f"tags: {', '.join(entry['tags'])}")
    if entry.get("promoted_to"):
        print(f"promoted_to: {entry['promoted_to']}")
    # THR-091: surface entry age at recall
    age_days = entry.get("age_days")
    if age_days is not None:
        print(f"age: {age_days} days since last update")
    lv_age = entry.get("last_verified_age_days")
    if lv_age is not None:
        print(f"last verified: {lv_age} days ago")
    print()
    print(entry["body"])



def cmd_learning_search(args: argparse.Namespace) -> None:
    client = _learning_client()
    org = resolve_org_slug(args_org=args.org, available=_shared._fetch_available_orgs(client))
    # Build payload: only include fields the user explicitly supplied.
    # Omitted fields let the daemon apply org config defaults.
    payload: dict = {"query": args.query}
    if args.limit is not None:
        payload["limit"] = args.limit
    if args.include_promoted:
        payload["include_promoted"] = True
    if args.include_evicted is not None:
        payload["include_evicted"] = args.include_evicted
    if args.include_superseded is not None:
        payload["include_superseded"] = args.include_superseded
    if args.include_kb is not None:
        payload["include_kb"] = args.include_kb
    params: dict = {}
    # THR-091 Slice 2: optional session_id for search telemetry correlation
    if getattr(args, "session_id", None):
        params["session_id"] = args.session_id
    r = client.post(
        f"/api/v1/orgs/{org}/agents/{args.agent}/memory/entries/search",
        json=payload,
        params=params or None,
    )
    if not _ok(r):
        return
    resp = r.json()
    hits = resp.get("hits", [])
    warnings = resp.get("warnings", [])
    if args.json:
        import json
        print(json.dumps({"hits": hits, "warnings": warnings}, indent=2))
        return
    if not hits:
        print("(no matches)")
        if warnings:
            for w in warnings:
                print(f"warning: {w}")
        return
    for h in hits:
        source = h.get("source", "memory")
        src_label = f"[{source}]" if source != "memory" else ""
        lifecycle = h.get("lifecycle", "")
        lc_label = f" ({lifecycle})" if lifecycle and lifecycle != "valid" else ""
        print(f"  {h['id']}  score={h['score']}  {h['title']}{src_label}{lc_label}")
        print(f"      {h['snippet']}")
    if warnings:
        for w in warnings:
            print(f"warning: {w}")



def cmd_learning_reindex(args: argparse.Namespace) -> None:
    client = _learning_client()
    org = resolve_org_slug(args_org=args.org, available=_shared._fetch_available_orgs(client))
    r = client.post(f"/api/v1/orgs/{org}/agents/{args.agent}/memory/entries/reindex", json={})
    if not _ok(r):
        return
    print("ok: reindexed")


def cmd_memory_lifecycle(args: argparse.Namespace) -> None:
    """THR-032 P3a: transition a memory item's lifecycle."""
    client = _learning_client()
    org = resolve_org_slug(args_org=args.org, available=_shared._fetch_available_orgs(client))
    r = client.patch(
        f"/api/v1/orgs/{org}/agents/{args.agent}/memory/entries/{args.id}/lifecycle",
        json={"lifecycle": getattr(args, "set"), "reason": args.reason},
    )
    if not _ok(r):
        return
    resp = r.json()
    print(
        f"ok: {resp['id']} lifecycle {resp['previous_lifecycle']} → {resp['lifecycle']}"
    )


def cmd_memory_compact(args: argparse.Namespace) -> None:
    """THR-032 P3b: manual memory compaction dry-run or apply."""
    client = _learning_client()
    org = resolve_org_slug(args_org=args.org, available=_shared._fetch_available_orgs(client))
    dry_run = not getattr(args, "apply", False)
    r = client.post(
        f"/api/v1/orgs/{org}/agents/{args.agent}/memory/entries/compact",
        json={"dry_run": dry_run},
    )
    if not _ok(r):
        return
    resp = r.json()
    if resp["dry_run"]:
        print(f"DRY RUN — {len(resp['candidates'])} candidates, {len(resp['skipped'])} skipped")
        if resp["candidates"]:
            print()
            for c in resp["candidates"]:
                print(f"  {c['id']}  {c['reason']}  ({c['current_lifecycle']})  {c['title']}")
        if resp["skipped"]:
            print()
            print("Skipped:")
            for s in resp["skipped"]:
                print(f"  {s['id']}: {s['reason']}")
    else:
        print(f"APPLIED — {len(resp['evicted'])} evicted, {len(resp['skipped'])} skipped")
        if resp["evicted"]:
            for eid in resp["evicted"]:
                print(f"  evicted: {eid}")
        if resp["errors"]:
            for err in resp["errors"]:
                print(f"  error: {err}")



def cmd_learning_add(args: argparse.Namespace) -> None:
    client = _learning_client()
    org = resolve_org_slug(args_org=args.org, available=_shared._fetch_available_orgs(client))
    payload = _read_yaml_payload(args.from_file)
    r = client.post(
        f"/api/v1/orgs/{org}/agents/{args.agent}/memory/entries/",
        json=payload,
    )
    if not _ok(r):
        return
    resp = r.json()
    print(f"ok: {resp['id']} -> {resp['path']}")



def cmd_learning_update(args: argparse.Namespace) -> None:
    client = _learning_client()
    org = resolve_org_slug(args_org=args.org, available=_shared._fetch_available_orgs(client))
    payload = _read_yaml_payload(args.from_file)
    r = client.request("PUT", f"/api/v1/orgs/{org}/agents/{args.agent}/memory/entries/{args.id}", json=payload)
    if not _ok(r):
        return
    resp = r.json()
    print(f"ok: updated {resp['id']}")



def cmd_learning_promote(args: argparse.Namespace) -> None:
    client = _learning_client()
    org = resolve_org_slug(args_org=args.org, available=_shared._fetch_available_orgs(client))
    r = client.post(
        f"/api/v1/orgs/{org}/agents/{args.agent}/memory/entries/{args.id}/promote",
        json={"kb_slug": args.kb_slug},
    )
    if not _ok(r):
        return
    resp = r.json()
    print(f"ok: {resp['id']} promoted to KB precedent `{resp['promoted_to']}`")



def _paginate(client: OpcClient, org: str, action: str) -> list[dict]:
    """Cursor-paginate through all audit rows for the given action."""
    all_rows: list[dict] = []
    cursor: str | None = None
    while True:
        params: dict = {"action": action, "limit": 5000}
        if cursor is not None:
            params["cursor"] = cursor
        r = client.get(f"/api/v1/orgs/{org}/audit", params=params)
        if not _ok(r):
            break
        page = r.json()
        entries = page.get("entries", [])
        all_rows.extend(entries)
        cursor = page.get("next_cursor")
        if cursor is None:
            break
    return all_rows


def _fetch_agent_roles(client: OpcClient, org: str) -> dict[str, str]:
    """Fetch agent→role map from the authoritative /agents read surface.

    Returns empty dict when /agents is unavailable — the caller must
    report this as unavailable rather than falling back to agent names."""
    role_map: dict[str, str] = {}
    r = client.get(f"/api/v1/orgs/{org}/agents")
    if not _ok(r):
        print("warning: could not fetch agent roles from /agents;"
              " role analysis will be unavailable", file=sys.stderr)
        return role_map
    agents = r.json().get("agents", [])
    for agent in agents:
        name = agent.get("name")
        role = agent.get("role")
        if name and role:
            role_map[name] = role
    return role_map


def cmd_memory_report(args: argparse.Namespace) -> None:
    """THR-091 Slice 2: operator-facing memory telemetry report.

    Reads the org's audit_log to compute activation/retrieval telemetry
    from memory_digest_impression, memory_read, and memory_search rows.
    Uses cursor pagination (not fixed first-page caps).  Agent roles
    are fetched from the authoritative /agents read surface.
    Outputs in JSON or human-readable form.
    """
    client = _learning_client()
    org = resolve_org_slug(args_org=args.org, available=_shared._fetch_available_orgs(client))

    # Fetch agent roles from authoritative /agents surface
    role_map = _fetch_agent_roles(client, org)

    # Collect digest impressions (cursor-paginated)
    impression_rows = _paginate(client, org, "memory_digest_impression")

    # Collect memory_read events (cursor-paginated)
    read_rows = _paginate(client, org, "memory_read")

    # Collect memory_search events (cursor-paginated)
    search_rows = _paginate(client, org, "memory_search")

    # Compute telemetry client-side (mirrors compute_memory_telemetry_report)
    report = _compute_report(impression_rows, read_rows, search_rows,
                             agent_role_map=role_map if role_map else None)

    if args.json:
        print(json.dumps(report, indent=2))
        return

    # Human-readable output
    _print_report(report)


def _compute_report(
    impression_rows: list[dict],
    read_rows: list[dict],
    search_rows: list[dict],
    agent_role_map: dict[str, str] | None = None,
    current_time: "datetime | None" = None,
) -> dict:
    """Client-side telemetry computation from audit rows.

    Args:
        current_time: UTC datetime for time-based threshold evaluation.
            When None (production), uses datetime.now(timezone.utc).
    """
    from datetime import datetime, timezone

    if current_time is None:
        current_time = datetime.now(timezone.utc)

    if not impression_rows:
        return {
            "observation_period": {
                "status": "insufficient_sample",
                "reason": "No memory_digest_impression rows found —"
                          " observation has not started.",
                "trigger": "First production memory_digest_impression row"
                           " emitted by the deployed revision.",
            },
            "aggregate": {},
            "by_role": {},
            "decision": "insufficient_sample",
        }

    # Parse impressions
    impressions: list[dict] = []
    for row in impression_rows:
        payload = row.get("payload", {})
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except Exception:
                continue
        session_id = payload.get("session_id")
        digest_ids = payload.get("digest_ids", [])
        agent = payload.get("agent", row.get("agent", ""))
        task_id = payload.get("task_id", "")
        if session_id and digest_ids:
            impressions.append({
                "session_id": session_id,
                "digest_ids": digest_ids,
                "agent": agent,
                "task_id": task_id,
                "timestamp": row.get("timestamp", ""),
            })

    if not impressions:
        return {
            "observation_period": {
                "status": "insufficient_sample",
                "reason": "No non-empty correlated digest impressions found.",
                "trigger": "First production memory_digest_impression row"
                           " emitted by the deployed revision.",
            },
            "aggregate": {},
            "by_role": {},
            "decision": "insufficient_sample",
        }

    # Observation period
    first_ts_str = impressions[0]["timestamp"]
    try:
        first_ts = datetime.fromisoformat(first_ts_str.replace("Z", "+00:00"))
    except Exception:
        first_ts = datetime.now(timezone.utc)
    days_elapsed = (current_time - first_ts).days

    all_sessions: set[str] = set()
    per_agent_sessions: dict[str, set[str]] = {}
    agent_roles: dict[str, str] = {}
    for imp in impressions:
        sid = imp["session_id"]
        all_sessions.add(sid)
        agent = imp["agent"]
        if agent not in per_agent_sessions:
            per_agent_sessions[agent] = set()
        per_agent_sessions[agent].add(sid)
        if agent_role_map is not None and agent not in agent_roles:
            role = agent_role_map.get(agent)
            if role is not None:
                agent_roles[agent] = role

    total_sessions = len(all_sessions)
    met_days = days_elapsed >= 14
    met_sessions = total_sessions >= 500

    observation = {
        "trigger": "First production memory_digest_impression row emitted"
                   " by the deployed revision.",
        "first_impression_at": first_ts_str,
        "days_elapsed": days_elapsed,
        "required_days": 14,
        "total_correlated_sessions": total_sessions,
        "required_sessions": 500,
        "thresholds_met": met_days and met_sessions,
        "days_met": met_days,
        "sessions_met": met_sessions,
    }

    if not (met_days and met_sessions):
        return {
            "observation_period": {
                **observation,
                "status": "insufficient_sample",
                "reason": (
                    f"Need 14 days (have {days_elapsed}) AND"
                    f" 500 sessions (have {total_sessions})."
                ),
            },
            "aggregate": {},
            "by_role": {},
            "decision": "insufficient_sample",
        }

    # Session digest maps.
    # Build validated (agent, task_id, session_id) tuples from trusted
    # impressions so reads can be verified against them.
    session_digest_ids: dict[str, set[str]] = {}
    session_agent: dict[str, str] = {}
    validated_impression_tuples: dict[str, tuple[str, str]] = {}  # sid→(agent, task_id)
    for imp in impressions:
        sid = imp["session_id"]
        session_digest_ids[sid] = set(imp["digest_ids"])
        session_agent[sid] = imp["agent"]
        imp_task_id = imp.get("task_id", "")
        if imp_task_id:
            validated_impression_tuples[sid] = (imp["agent"], imp_task_id)

    # Parse reads — only include tuple-verified reads (matching the
    # impression's agent+task_id+session_id) in tracked attribution.
    # Legacy/untrusted/mismatched reads are excluded from denominators.
    session_read_ids: dict[str, set[str]] = {}
    untrusted_reads: list[dict] = []
    digest_reads: list[dict] = []
    search_reads: list[dict] = []
    explicit_reads: list[dict] = []
    for row in read_rows:
        payload = row.get("payload", {})
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except Exception:
                continue
        mid = payload.get("id")
        source = payload.get("source", "explicit_or_other")
        rsid = payload.get("session_id")
        rtask_id = payload.get("task_id")
        # Verify tuple: read's (agent, task_id, session_id) must match
        # the impression's validated tuple.
        if rsid and rtask_id:
            validated_tuple = validated_impression_tuples.get(rsid)
            if validated_tuple is not None:
                imp_agent, imp_task_id = validated_tuple
                row_agent = payload.get("agent", row.get("agent", ""))
                if row_agent == imp_agent and rtask_id == imp_task_id:
                    if rsid not in session_read_ids:
                        session_read_ids[rsid] = set()
                    session_read_ids[rsid].add(mid)
                    entry = {"id": mid, "source": source,
                             "session_id": rsid, "task_id": rtask_id,
                             "agent": row_agent}
                    if source == "digest":
                        digest_reads.append(entry)
                    elif source == "search":
                        search_reads.append(entry)
                    else:
                        explicit_reads.append(entry)
                    continue
            # Tuple mismatch or unknown session — treat as untrusted
            untrusted_reads.append({"id": mid, "source": source,
                                   "session_id": rsid, "task_id": rtask_id})
        else:
            untrusted_reads.append({"id": mid, "source": source, "session_id": rsid})

    # Aggregate pull-through — per-session denominators (not globally unioned).
    # Each session's unique shown IDs and unique read-in-session IDs are summed
    # across all sessions.  A MEM-001 shown in 2 sessions counts as 2 in the
    # denominator.
    total_shown = 0
    total_read_in_session = 0
    for sid, d_ids in session_digest_ids.items():
        shown = len(d_ids)
        reads = session_read_ids.get(sid, set())
        read_in_this_session = len(reads & d_ids)
        total_shown += shown
        total_read_in_session += read_in_this_session

    agg_pull_through = (
        total_read_in_session / total_shown
        if total_shown else 0.0
    )

    # Search reads absent from session digest.
    # Only correlated read rows (have both session_id and task_id) are
    # evaluated.  Legacy/untrusted/unmatched rows are excluded rather than
    # treated as search misses.
    search_total = len(search_reads)
    search_absent_count = 0
    for sr in search_reads:
        sid = sr.get("session_id")
        if sid and sid in session_digest_ids:
            if sr["id"] not in session_digest_ids[sid]:
                search_absent_count += 1
        # No else — untrusted rows excluded, not counted as absent

    search_absent_frac = (
        search_absent_count / search_total if search_total > 0 else 0.0
    )

    # Per-role.
    # Only agents with an authoritative role from agent_role_map are included
    # in role-level decisions.  Agents without a known role are excluded.
    roles_unavailable = agent_role_map is None
    role_data: dict[str, dict] = {}
    unknown_agents: list[str] = []
    for agent, sessions in per_agent_sessions.items():
        role = agent_roles.get(agent)
        if role is None:
            unknown_agents.append(agent)
            continue
        if role not in role_data:
            role_data[role] = {"agents": [], "sessions": set()}
        role_data[role]["agents"].append(agent)
        role_data[role]["sessions"] |= sessions

    by_role: dict[str, dict] = {}
    eligible_roles: list[str] = []
    for role, data in role_data.items():
        role_sessions = data["sessions"]
        role_session_count = len(role_sessions)
        if role_session_count < 30:
            by_role[role] = {
                "correlated_sessions": role_session_count,
                "eligible": False,
                "reason": f"Need >=30 sessions (have {role_session_count})",
            }
            continue
        eligible_roles.append(role)

        # Per-session denominators (not globally unioned) for role pull-through
        role_total_shown = 0
        role_total_read_in_session = 0
        for sid in role_sessions:
            if sid in session_digest_ids:
                role_total_shown += len(session_digest_ids[sid])
                reads = session_read_ids.get(sid, set())
                role_total_read_in_session += len(
                    reads & session_digest_ids[sid]
                )

        role_pull_through = (
            role_total_read_in_session / role_total_shown
            if role_total_shown else 0.0
        )

        role_search_total = 0
        role_search_absent = 0
        for sr in search_reads:
            sid = sr.get("session_id")
            if sid and sid in role_sessions:
                role_search_total += 1
                if sid in session_digest_ids:
                    if sr["id"] not in session_digest_ids[sid]:
                        role_search_absent += 1
                # No else — untrusted rows excluded

        role_search_absent_frac = (
            role_search_absent / role_search_total
            if role_search_total > 0 else 0.0
        )

        by_role[role] = {
            "correlated_sessions": role_session_count,
            "eligible": True,
            "digest_pull_through": round(role_pull_through, 4),
            "unique_digest_ids_shown": role_total_shown,
            "unique_digest_ids_read_same_session": role_total_read_in_session,
            "search_sourced_reads": role_search_total,
            "search_sourced_absent_from_digest": role_search_absent,
            "search_absent_fraction": round(role_search_absent_frac, 4),
            "search_threshold_met": role_search_total >= 30,
        }

    # Decision
    decision: str
    decision_detail: str

    if agg_pull_through < 0.10:
        eligible_below_10 = sum(
            1 for r in eligible_roles
            if by_role[r]["digest_pull_through"] < 0.10
        )
        if eligible_roles and eligible_below_10 > len(eligible_roles) / 2:
            decision = "activation_loss"
            decision_detail = (
                "Aggregate pointer-level same-session pull-through"
                f" ({agg_pull_through:.2%}) < 10% AND majority of eligible"
                f" roles ({eligible_below_10}/{len(eligible_roles)}) < 10%."
                " Next step: provenance/push tuning only, no aliases/embeddings."
            )
        elif not eligible_roles:
            decision = "no_demonstrated_problem"
            decision_detail = (
                f"Aggregate pull-through ({agg_pull_through:.2%}) < 10%"
                " but no eligible roles to confirm (role analysis"
                " unavailable). Do not tune ranking/push."
            )
        else:
            decision = "no_demonstrated_problem"
            decision_detail = (
                f"Aggregate pull-through ({agg_pull_through:.2%}) < 10%"
                " but majority of eligible roles are NOT <10%."
                " Contradictory role visibility preserved."
            )
    elif search_absent_frac > 0.25:
        retrieval_roles = [
            r for r in eligible_roles
            if by_role[r].get("search_threshold_met")
            and by_role[r]["search_absent_fraction"] > 0.25
        ]
        if retrieval_roles:
            decision = "retrieval_loss"
            decision_detail = (
                "Search reads of IDs absent from digest >25% in"
                f" aggregate ({search_absent_frac:.2%}) AND in eligible"
                f" role(s): {retrieval_roles}."
                " Next step: alias/synonym-tag evaluation first."
                " Embeddings remain founder-gated."
            )
        else:
            decision = "no_demonstrated_problem"
            decision_detail = (
                "Search absent fraction >25% aggregate"
                f" ({search_absent_frac:.2%}) but no eligible role"
                " with >=30 search reads exceeds 25%."
            )
    else:
        decision = "no_demonstrated_problem"
        decision_detail = (
            "Aggregate pull-through >=10% and search absent"
            " fraction <=25%. No demonstrated problem."
        )

    result: dict = {
        "observation_period": {**observation, "status": "thresholds_met"},
        "aggregate": {
            "correlated_sessions": total_sessions,
            "unique_digest_ids_shown": total_shown,
            "unique_digest_ids_read_same_session": total_read_in_session,
            "digest_pull_through": round(agg_pull_through, 4),
            "search_sourced_reads": search_total,
            "search_sourced_absent_from_digest": search_absent_count,
            "search_absent_fraction": round(search_absent_frac, 4),
            "digest_sourced_reads": len(digest_reads),
            "explicit_or_other_sourced_reads": len(explicit_reads),
            "untrusted_uncorrelated_reads": len(untrusted_reads),
        },
        "by_role": by_role,
        "decision": decision,
        "decision_detail": decision_detail,
    }
    if roles_unavailable:
        result["roles_warning"] = (
            "Authoritative agent roles unavailable — /agents surface"
            " could not be read. Per-role analysis excluded."
        )
    elif unknown_agents:
        result["roles_warning"] = (
            f"{len(unknown_agents)} agent(s) have unknown roles"
            f" and are excluded from role decisions: {unknown_agents}"
        )
    return result


def _print_report(report: dict) -> None:
    """Print human-readable telemetry report."""
    obs = report.get("observation_period", {})
    print("=== THR-091 Memory Layer Slice 2 Telemetry Report ===")
    print()
    print("OBSERVATION PERIOD")
    print(f"  Status:        {obs.get('status', 'unknown')}")
    print(f"  Trigger:       {obs.get('trigger', 'N/A')}")
    print(f"  First event:   {obs.get('first_impression_at', 'N/A')}")
    print(f"  Days elapsed:  {obs.get('days_elapsed', 0)} / {obs.get('required_days', 14)}")
    print(f"  Sessions:      {obs.get('total_correlated_sessions', 0)} / {obs.get('required_sessions', 500)}")
    print(f"  Thresholds:    {'MET' if obs.get('thresholds_met') else 'NOT MET'}")
    print()

    if obs.get("status") != "thresholds_met":
        print(f"DECISION: insufficient_sample")
        print(f"  {obs.get('reason', '')}")
        return

    agg = report.get("aggregate", {})
    print("AGGREGATE")
    print(f"  Correlated digest sessions:     {agg.get('correlated_sessions', 0)}")
    print(f"  Unique digest IDs shown:        {agg.get('unique_digest_ids_shown', 0)}")
    print(f"  Same-session reads:             {agg.get('unique_digest_ids_read_same_session', 0)}")
    print(f"  Digest pull-through:            {agg.get('digest_pull_through', 0):.2%}")
    print(f"  Search-sourced reads:           {agg.get('search_sourced_reads', 0)}")
    print(f"  Search absent from digest:      {agg.get('search_sourced_absent_from_digest', 0)}")
    print(f"  Search absent fraction:         {agg.get('search_absent_fraction', 0):.2%}")
    print(f"  Digest-sourced reads:           {agg.get('digest_sourced_reads', 0)}")
    print(f"  Explicit/other reads:           {agg.get('explicit_or_other_sourced_reads', 0)}")
    print()

    by_role = report.get("by_role", {})
    if by_role:
        print("BY ROLE")
        for role, data in sorted(by_role.items()):
            eligible = data.get("eligible", False)
            marker = "" if eligible else " (INELIGIBLE)"
            print(f"  [{role}]{marker}")
            print(f"    Sessions:       {data.get('correlated_sessions', 0)}")
            if eligible:
                print(f"    Pull-through:   {data.get('digest_pull_through', 0):.2%}")
                print(f"    Search absent:  {data.get('search_absent_fraction', 0):.2%}")
            else:
                print(f"    Reason:         {data.get('reason', 'N/A')}")
            print()

    roles_warning = report.get("roles_warning")
    if roles_warning:
        print()
        print(f"ROLES WARNING: {roles_warning}")
    print()
    print(f"DECISION: {report.get('decision', 'unknown')}")
    print(f"  {report.get('decision_detail', '')}")


def _deprecation_wrapper(func):
    """Wrap a handler so the deprecated `learning` alias prints a one-line
    stderr notice before dispatching to the SAME handler (THR-032 Phase R).
    Kept for exactly one rollout cycle, then the alias is removed."""
    def wrapped(args: argparse.Namespace) -> None:
        print(
            "warning: `happyranch learning` is deprecated; use `happyranch memory` "
            "(this alias is removed next rollout cycle)",
            file=sys.stderr,
        )
        return func(args)

    return wrapped


def _register_group(sub, name: str, *, deprecated: bool) -> None:
    """Register the `memory`/`learning` verb group on `sub`.

    `memory` is canonical; `learning` is a thin deprecation alias dispatching to
    the SAME handlers — the only difference is a stderr deprecation notice."""
    noun = "memory items"
    help_text = (
        "DEPRECATED alias of `memory` (removed next rollout cycle)"
        if deprecated
        else "Per-agent memory (verb-dispatched)"
    )
    wrap = _deprecation_wrapper if deprecated else (lambda f: f)

    p = sub.add_parser(name, help=help_text)
    verb_sub = p.add_subparsers(dest=f"{name}_verb")

    # Agent callback: `happyranch memory --org <slug> --agent X --text "..."`.
    # NOT argparse-required: a real verb form (`memory get --org o ...`) puts
    # --org AFTER the verb, where the subparser consumes it, so requiring it on
    # the parent would reject the documented forms (exit 2). cmd_learning
    # enforces --org for the bare-callback path instead. Keep the default None
    # (not SUPPRESS) so args.org always exists for cmd_learning's check; the
    # subparser --org uses SUPPRESS so it never clobbers a parent-provided org.
    p.add_argument("--org", required=False)
    p.add_argument("--agent", required=False)
    p.add_argument("--text", required=False)
    p.add_argument("--task-id", required=False)
    p.add_argument("--session-id", required=False)
    p.set_defaults(func=wrap(cmd_learning))

    pl = verb_sub.add_parser("list", help=f"List {noun}")
    pl.add_argument("--org", required=False, default=argparse.SUPPRESS)
    pl.add_argument("--agent", required=True)
    pl.add_argument("--topic")
    pl.add_argument("--tag")
    pl.add_argument("--promoted", action="store_true")
    pl.add_argument("--not-promoted", action="store_true")
    pl.add_argument("--json", action="store_true")
    pl.set_defaults(func=wrap(cmd_learning_list))

    pg = verb_sub.add_parser("get", help="Get a memory item by ID or slug")
    pg.add_argument("--org", required=False, default=argparse.SUPPRESS)
    pg.add_argument("--agent", required=True)
    pg.add_argument("id_or_slug")
    pg.add_argument("--json", action="store_true")
    # THR-091 Slice 2: optional session_id for read-source attribution
    pg.add_argument("--session-id", required=False, default=None)
    pg.set_defaults(func=wrap(cmd_learning_get))

    ps = verb_sub.add_parser("search", help=f"Substring search over {noun}")
    ps.add_argument("--org", required=False, default=argparse.SUPPRESS)
    ps.add_argument("--agent", required=True)
    ps.add_argument("query")
    ps.add_argument("--limit", type=int, default=None)
    ps.add_argument("--include-promoted", action="store_true")
    ps.add_argument("--include-evicted", action=argparse.BooleanOptionalAction, default=None)
    ps.add_argument("--include-superseded", action=argparse.BooleanOptionalAction, default=None)
    ps.add_argument("--include-kb", action=argparse.BooleanOptionalAction, default=None)
    ps.add_argument("--json", action="store_true")
    # THR-091 Slice 2: optional session_id for search telemetry correlation
    ps.add_argument("--session-id", required=False, default=None)
    ps.set_defaults(func=wrap(cmd_learning_search))

    pa = verb_sub.add_parser("add", help="Add a new memory item (file payload)")
    pa.add_argument("--org", required=False, default=argparse.SUPPRESS)
    pa.add_argument("--agent", required=True)
    pa.add_argument("--from-file", required=True)
    pa.set_defaults(func=wrap(cmd_learning_add))

    pu = verb_sub.add_parser("update", help="Update an existing memory item by ID")
    pu.add_argument("--org", required=False, default=argparse.SUPPRESS)
    pu.add_argument("--agent", required=True)
    pu.add_argument("id")
    pu.add_argument("--from-file", required=True)
    pu.set_defaults(func=wrap(cmd_learning_update))

    pp = verb_sub.add_parser("promote", help="Promote a memory item to a KB precedent")
    pp.add_argument("--org", required=False, default=argparse.SUPPRESS)
    pp.add_argument("--agent", required=True)
    pp.add_argument("id")
    pp.add_argument("--kb-slug", required=True)
    pp.set_defaults(func=wrap(cmd_learning_promote))

    pr = verb_sub.add_parser("reindex", help="Regenerate _index.md")
    pr.add_argument("--org", required=False, default=argparse.SUPPRESS)
    pr.add_argument("--agent", required=True)
    pr.set_defaults(func=wrap(cmd_learning_reindex))

    # THR-032 P3a: lifecycle command
    plc = verb_sub.add_parser("lifecycle", help="Transition a memory item's lifecycle")
    plc.add_argument("--org", required=False, default=argparse.SUPPRESS)
    plc.add_argument("--agent", required=True)
    plc.add_argument("id")
    plc.add_argument("--set", required=True, choices=["valid", "superseded", "evicted"],
                      help="Target lifecycle state")
    plc.add_argument("--reason", required=True, help="Non-empty reason for the transition")
    plc.set_defaults(func=wrap(cmd_memory_lifecycle))

    # THR-032 P3b: compaction command
    pc = verb_sub.add_parser("compact", help="Manual memory compaction (dry-run or apply)")
    pc.add_argument("--org", required=False, default=argparse.SUPPRESS)
    pc.add_argument("--agent", required=True)
    pc_group = pc.add_mutually_exclusive_group(required=True)
    pc_group.add_argument("--dry-run", action="store_true", dest="dry_run", help="Report candidates only (no writes)")
    pc_group.add_argument("--apply", action="store_true", help="Evict eligible candidates")
    pc.set_defaults(func=wrap(cmd_memory_compact))

    # THR-091 Slice 2: telemetry report command
    prpt = verb_sub.add_parser("report", help="Memory telemetry report (THR-091 Slice 2)")
    prpt.add_argument("--org", required=False, default=argparse.SUPPRESS)
    prpt.add_argument("--agent", required=True)
    prpt.add_argument("--json", action="store_true", help="Output in JSON")
    # Roles are fetched from the authoritative /agents read surface — no
    # caller-supplied --role-map.
    prpt.set_defaults(func=wrap(cmd_memory_report))


def register(sub) -> None:
    # THR-032 Phase R thorough rename: `memory` is canonical; `learning` is a
    # one-cycle deprecation alias dispatching to the same handlers.
    _register_group(sub, "memory", deprecated=False)
    _register_group(sub, "learning", deprecated=True)

