"""Task lifecycle, control, telemetry, and task-scoped agent callbacks."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from cli import _shared
from cli._shared import _fmt_ts, _ok, resolve_org_slug
from cli.client.client import DaemonNotRunning, DaemonStateInconsistent, OpcClient


def cmd_run(args: argparse.Namespace) -> None:
    """Submit a task and return immediately.

    The CLI does not stream events. Use `happyranch tail <task_id>` to attach to
    a running task, or `happyranch details <task_id>` for a snapshot.
    """
    try:
        client = OpcClient.from_env()
    except (DaemonNotRunning, DaemonStateInconsistent) as exc:
        print(f"Error: {exc}")
        sys.exit(1)

    slug = resolve_org_slug(
        args_org=args.org, available=_shared._fetch_available_orgs(client),
    )
    if args.brief_file:
        try:
            brief = Path(args.brief_file).expanduser().read_text(encoding="utf-8")
        except OSError as exc:
            print(f"Error reading brief file {args.brief_file}: {exc}")
            sys.exit(1)
    else:
        brief = args.brief
    if not brief.strip():
        print("Error: brief is empty")
        sys.exit(1)
    payload: dict = {"brief": brief}
    if args.team:
        payload["team"] = args.team
    r = client.post(f"/api/v1/orgs/{slug}/tasks", json=payload)
    if not _ok(r):
        return
    task_id = r.json()["task_id"]
    print(f"Submitted {task_id}. Attach with: happyranch tail {task_id}")



def cmd_tail(args: argparse.Namespace) -> None:
    """Reattach to a running task and stream its events until terminal."""
    try:
        client = OpcClient.from_env()
    except (DaemonNotRunning, DaemonStateInconsistent) as exc:
        print(f"Error: {exc}")
        sys.exit(1)
    slug = resolve_org_slug(
        args_org=args.org, available=_shared._fetch_available_orgs(client),
    )
    _stream_task_events(client, slug, args.task_id)



def _stream_task_events(client: OpcClient, slug: str, task_id: str) -> None:
    import json as _json

    import httpx

    try:
        for payload in client.stream("GET", f"/api/v1/orgs/{slug}/tasks/{task_id}/events"):
            try:
                event = _json.loads(payload)
            except _json.JSONDecodeError:
                print(payload)
                continue
            etype = event.get("type", "?")
            print(f"[{etype}] {event}")
            if etype in ("task_complete", "task_blocked", "task_failed"):
                return
    except httpx.HTTPStatusError as exc:
        # OpcClient.stream calls raise_for_status(), so a 404 (e.g. unknown
        # task id from `happyranch tail`) lands here. Surface a clean message instead
        # of an httpx traceback.
        print(f"Error: stream failed for {task_id} ({exc.response.status_code})")
        sys.exit(1)
    except KeyboardInterrupt:
        print(f"\nDetached. Reattach with: happyranch tail {task_id}")



def cmd_tasks(args: argparse.Namespace) -> None:
    """List recent tasks."""
    try:
        client = OpcClient.from_env()
    except (DaemonNotRunning, DaemonStateInconsistent) as exc:
        print(f"Error: {exc}")
        sys.exit(1)
    slug = resolve_org_slug(
        args_org=args.org, available=_shared._fetch_available_orgs(client),
    )
    r = client.get(f"/api/v1/orgs/{slug}/tasks", params={"limit": args.limit})
    if not _ok(r):
        return
    tasks = r.json()["tasks"]
    if not tasks:
        print("No tasks found.")
        return
    print(f"{'ID':<12} {'Team':<16} {'Status':<22} {'Agent':<18} Brief")
    print("-" * 102)
    for t in tasks:
        brief = t["brief"][:40] + "..." if len(t["brief"]) > 40 else t["brief"]
        agent = t.get("assigned_agent") or "-"
        status = t["status"]
        if t.get("block_kind"):
            status = f"{status}({t['block_kind']})"
        # Revisit marker — appended after the brief so row widths stay stable
        # for non-revisit rows. `↩` is a U+21A9 leftwards arrow with hook.
        if t.get("revisit_of_task_id"):
            brief = f"{brief}  ↩ {t['revisit_of_task_id']}"
        team = t.get("team") or "-"
        print(f"{t['task_id']:<12} {team:<16} {status:<22} {agent:<18} {brief}")



def cmd_details(args: argparse.Namespace) -> None:
    """Show status of a specific task."""
    try:
        client = OpcClient.from_env()
    except (DaemonNotRunning, DaemonStateInconsistent) as exc:
        print(f"Error: {exc}")
        sys.exit(1)
    slug = resolve_org_slug(
        args_org=args.org, available=_shared._fetch_available_orgs(client),
    )
    r = client.get(f"/api/v1/orgs/{slug}/tasks/{args.task_id}")
    if r.status_code == 404:
        print(f"Task {args.task_id} not found.")
        sys.exit(1)
    if not _ok(r):
        return
    body = r.json()
    task = body["task"]

    # Revisit header: shown only when this task IS a revisit.
    if task.get("revisit_of_task_id"):
        prior = body.get("predecessor_prior_status") or "unknown"
        print(f"Revisit of: {task['revisit_of_task_id']}  (predecessor: {prior})")
        chain = body.get("revisit_chain") or []
        if len(chain) > 1:
            # walk_revisit_chain returns [task, predecessor, ..., original].
            # Reverse so the oldest predecessor is leftmost and ← reads
            # "created from": original ← ... ← current (this).
            display = list(reversed(chain))
            display[-1] = f"{display[-1]} (this)"
            print(f"Chain:      {' ← '.join(display)}")

    # Dispatched-from header: shown only when this task was created via
    # POST /talks/{talk_id}/dispatch. Pulls dispatcher agent/role from the
    # task_dispatched audit row written at dispatch time.
    if task.get("dispatched_from_talk_id"):
        dispatcher = "?"
        role = "?"
        for log in body.get("audit_log") or []:
            if log.get("action") == "task_dispatched":
                payload = log.get("payload") or {}
                dispatcher = payload.get("dispatcher_agent", "?")
                role = payload.get("dispatcher_role", "?")
                break
        print(
            f"Dispatched from: {task['dispatched_from_talk_id']}  "
            f"(dispatcher: {dispatcher} / {role})"
        )

    print(f"Task:       {task['task_id']}")
    print(f"Team:       {task.get('team', '-')}")
    print(f"Status:     {task['status']}")
    print(f"Agent:      {task.get('assigned_agent') or '-'}")
    print(f"Brief:      {task['brief']}")
    print(f"Created:    {_fmt_ts(task['created_at'])}")
    print(f"Updated:    {_fmt_ts(task['updated_at'])}")
    # Liveness — useful only while a subprocess is alive. After terminal
    # transitions the heartbeat is stale by definition (queue worker
    # cancelled the heartbeat coroutine on completion), so suppress to
    # avoid implying the task is still moving.
    if task["status"] == "in_progress" and task.get("last_heartbeat"):
        print(f"Heartbeat:  {_fmt_ts(task['last_heartbeat'])}")
    if task.get("block_kind"):
        print(f"Block kind: {task['block_kind']}")
    if body.get("blocked_on_jobs"):
        print("Blocked on jobs:")
        for entry in body["blocked_on_jobs"]:
            print(f"  {entry['job_id']}  {entry['status']}")
    if body.get("active_chain"):
        chain = body["active_chain"]
        total_legs = 1 + len(chain.get("legs", []))
        current_idx = chain.get("step_index", 0)
        print(f"\nCurrent workflow chain (step {current_idx + 1} of {total_legs}):")
        # First leg is the implicit decision.agent/prompt — only the first_leg_expect_verdict
        # is captured in active_chain (the agent name/brief live in the orchestration_step
        # audit row referenced by step_audit_id, not in active_chain).
        first_marker = "▶" if current_idx == 0 else "✓"
        first_verdict_note = (
            f" (expecting: {chain['first_leg_expect_verdict']})"
            if chain.get("first_leg_expect_verdict") else ""
        )
        print(f"  {first_marker} Leg 1  (first leg — see orchestration_step audit){first_verdict_note}")
        for i, leg in enumerate(chain.get("legs", []), start=2):
            if current_idx == i - 1:
                marker = "▶"
            elif current_idx >= i:
                marker = "✓"
            else:
                marker = "⋯"
            verdict_note = (
                f" (expecting: {leg['expect_verdict']})"
                if leg.get("expect_verdict") else ""
            )
            agent = leg.get("agent", "")
            prompt_excerpt = (leg.get("prompt") or "")[:40]
            print(f"  {marker} Leg {i}  {agent:<14} {prompt_excerpt}{verdict_note}")
    if task.get("note"):
        print(f"Note:       {task['note']}")
    if body.get("results"):
        print(f"\nResults ({len(body['results'])}):")
        full = getattr(args, "full", False)
        for r_ in body["results"]:
            header = f"  - [{r_['agent']}] confidence={r_['confidence_score']}"
            if full:
                print(header)
                for line in (r_["output_summary"] or "").splitlines() or [""]:
                    print(f"      {line}")
            else:
                print(f"{header}  {r_['output_summary'][:80]}")
    if body.get("audit_log"):
        print(f"\nAudit log ({len(body['audit_log'])} entries):")
        for log in body["audit_log"]:
            line = (
                f"  {_fmt_ts(log['timestamp'])}  {log['agent']:20s}  {log['action']}"
            )
            # Inline the progress message so a long-running task's history
            # reads as a story instead of a sequence of identical "progress"
            # rows. Other actions stay terse — payload is in `happyranch audit --json`.
            if log["action"] == "progress":
                msg = (log.get("payload") or {}).get("message", "")
                if msg:
                    line += f"  {msg}"
            print(line)

    # Revisit footer: shown only when this task HAS been revisited.
    direct = body.get("direct_revisits") or []
    if direct:
        print(f"\nRevisited as: {', '.join(direct)}")



def cmd_audit(args: argparse.Namespace) -> None:
    """Show filtered audit-log entries via the daemon."""
    import json as _json

    try:
        client = OpcClient.from_env()
    except (DaemonNotRunning, DaemonStateInconsistent) as exc:
        print(f"Error: {exc}")
        sys.exit(1)

    slug = resolve_org_slug(
        args_org=args.org, available=_shared._fetch_available_orgs(client),
    )
    params: dict[str, str | int] = {}
    if args.task_id is not None:
        params["task_id"] = args.task_id
    if args.agent is not None:
        params["agent"] = args.agent
    if args.action is not None:
        params["action"] = args.action
    if args.since is not None:
        params["since"] = args.since
    if args.limit is not None:
        params["limit"] = args.limit

    r = client.get(f"/api/v1/orgs/{slug}/audit", params=params)
    if not _ok(r):
        return
    entries = r.json()["entries"]

    if args.json:
        print(_json.dumps(entries, indent=2))
        return

    if not entries:
        print("No audit entries match the filters.")
        return

    print(f"{'Timestamp':<20} {'Task':<10} {'Agent':<22} {'Action':<22} Payload")
    print("-" * 120)
    for e in entries:
        ts = _fmt_ts(e.get("timestamp"))
        task = e.get("task_id") or "-"
        agent = e.get("agent") or "-"
        action = e.get("action") or "-"
        payload = e.get("payload")
        payload_s = _json.dumps(payload, separators=(",", ":")) if payload else "-"
        if len(payload_s) > 60:
            payload_s = payload_s[:57] + "..."
        print(f"{ts:<20} {task:<10} {agent:<22} {action:<22} {payload_s}")



def cmd_tokens(args: argparse.Namespace) -> None:
    """Show per-session token usage rows or rollup aggregates via the daemon.

    Default view is the most recent N (20 by default) ``session_token_usage``
    rows, descending by ``created_at``. ``--by-*`` switches to a rollup keyed
    by that scope. ``--json`` emits raw JSON for either view. ``total =
    (input or 0) + (output or 0) + (reasoning or 0)`` — cache reads are
    reported separately, never folded into ``total``.
    """
    import json as _json

    try:
        client = OpcClient.from_env()
    except (DaemonNotRunning, DaemonStateInconsistent) as exc:
        print(f"Error: {exc}")
        sys.exit(1)

    slug = resolve_org_slug(
        args_org=args.org, available=_shared._fetch_available_orgs(client),
    )

    filters = dict(
        task_id=args.task_id,
        agent=args.agent,
        since=args.since,
        scope_type=args.scope_type,
        scope_id=args.scope_id,
        thread_id=args.thread_id,
        talk_id=args.talk_id,
        purpose=args.purpose,
    )

    if args.by_agent or args.by_task or args.by_thread or args.by_talk:
        if args.by_agent:
            group_by = "agent"
        elif args.by_task:
            group_by = "task"
        elif args.by_thread:
            group_by = "thread"
        else:
            group_by = "talk"
        rollup = client.aggregate_tokens(
            slug=slug, group_by=group_by,
            **filters,
        )
        if args.json:
            print(_json.dumps(rollup, indent=2))
            return
        if not rollup:
            print("No token usage rows match the filters.")
            return
        if group_by == "agent":
            header_label, key = "Agent", "agent"
            label_width = 22
        elif group_by == "task":
            header_label, key = "Task", "task_id"
            label_width = 14
        elif group_by == "thread":
            header_label, key = "Thread", "thread_id"
            label_width = 14
        else:
            header_label, key = "Talk", "talk_id"
            label_width = 14
        print(
            f"{header_label:<{label_width}} {'Sessions':>8} "
            f"{'Input':>12} {'Output':>12} {'CacheR':>12} {'Total':>14}"
        )
        print("-" * (label_width + 1 + 8 + 1 + 12 + 1 + 12 + 1 + 12 + 1 + 14))
        for r in rollup:
            inp = r.get("input_tokens") or 0
            out = r.get("output_tokens") or 0
            rea = r.get("reasoning_tokens") or 0
            cr = r.get("cache_read_tokens") or 0
            total = inp + out + rea
            label = r.get(key) or "-"
            print(
                f"{label:<{label_width}} {r['sessions']:>8} "
                f"{inp:>12,} {out:>12,} {cr:>12,} {total:>14,}"
            )
        return

    rows = client.list_tokens(
        slug=slug,
        since=args.since, limit=args.limit if args.limit is not None else 20,
        task_id=args.task_id, agent=args.agent, scope_type=args.scope_type,
        scope_id=args.scope_id, thread_id=args.thread_id, talk_id=args.talk_id,
        purpose=args.purpose,
    )
    if args.json:
        print(_json.dumps(rows, indent=2))
        return
    if not rows:
        print("No token usage rows match the filters.")
        return
    print(
        f"{'Created':<20} {'Task':<10} {'Agent':<22} {'Exec':<10} "
        f"{'Input':>12} {'Output':>12} {'CacheR':>12} {'Total':>14}"
    )
    print("-" * (20 + 1 + 10 + 1 + 22 + 1 + 10 + 1 + 12 + 1 + 12 + 1 + 12 + 1 + 14))
    for r in rows:
        ts = _fmt_ts(r.get("created_at"))
        inp = r.get("input_tokens") or 0
        out = r.get("output_tokens") or 0
        rea = r.get("reasoning_tokens") or 0
        cr = r.get("cache_read_tokens") or 0
        total = inp + out + rea
        print(
            f"{ts:<20} {(r.get('task_id') or '-'):<10} "
            f"{(r.get('agent') or '-'):<22} {(r.get('executor') or '-'):<10} "
            f"{inp:>12,} {out:>12,} {cr:>12,} {total:>14,}"
        )



def _completion_payload_from_file(path: str) -> tuple[str, dict]:
    """Load a completion payload from a JSON file.

    Agents use this path because multi-line bash commands (backslash
    continuations) count as separate subcommands under Claude Code's
    permission model, which breaks the narrow ``Bash(happyranch:*)`` allow rule.
    Writing a JSON file and invoking `happyranch report-completion --from-file
    <path>` keeps the tool call a single line.

    Returns ``(task_id, body)`` shaped for the daemon's completion endpoint.
    """
    import json as _json
    with open(path) as f:
        data = _json.load(f)
    required = ["task_id", "session_id", "agent", "status", "summary"]
    missing = [k for k in required if not data.get(k)]
    if missing:
        raise ValueError(f"completion file missing keys: {missing}")
    body = {
        "session_id": data["session_id"],
        "agent": data["agent"],
        "status": data["status"],
        "confidence": data.get("confidence", 80),
        "output_summary": data["summary"],
        "risks_flagged": data.get("risks") or [],
        "dependencies": data.get("dependencies") or [],
        "suggested_reviewer_focus": data.get("reviewer_focus") or [],
    }
    if data.get("output_dir"):
        body["output_dir"] = data["output_dir"]
    # Worker-reported verdict for inline delegation chains. Free string;
    # omit when the task is not part of a chain or the worker has no verdict.
    if data.get("verdict") is not None:
        body["verdict"] = data["verdict"]
    # Manager-only. Workers omit `decision`; team managers set it to a
    # NextStep object (delegate/done/escalate). Passed through verbatim —
    # the orchestrator parses it via the NextStep pydantic model.
    if data.get("decision") is not None:
        body["decision"] = data["decision"]
    # Agents self-blocking on jobs pass `waiting_on_job_ids` so the daemon's
    # block-on-jobs branch (run_step's self-blocked handler) transitions the
    # task to BLOCKED+BLOCKED_ON_JOB instead of the legacy self-escalate path.
    # Forward when explicitly present (membership check, NOT truthiness) so
    # the daemon sees an explicit `[]` and can reject it with the documented
    # 400 empty_waiting_on_job_ids — otherwise a malformed payload silently
    # bypasses the block-on-jobs contract.
    if "waiting_on_job_ids" in data:
        body["waiting_on_job_ids"] = data["waiting_on_job_ids"]
    return data["task_id"], body



def cmd_report_completion(args: argparse.Namespace) -> None:
    """Agent callback: report task completion to the daemon."""
    if not args.org:
        print("error: --org <slug> is required for agent callbacks", file=sys.stderr)
        sys.exit(1)
    try:
        client = OpcClient.from_env()
    except (DaemonNotRunning, DaemonStateInconsistent) as exc:
        print(f"Error: {exc}")
        sys.exit(1)

    import json as _json
    if args.from_file:
        try:
            task_id, body = _completion_payload_from_file(args.from_file)
        except (OSError, _json.JSONDecodeError, ValueError) as exc:
            print(f"Error reading completion file {args.from_file}: {exc}")
            sys.exit(1)
    else:
        missing = [
            flag for flag, val in [
                ("--task-id", args.task_id), ("--session-id", args.session_id),
                ("--agent", args.agent), ("--status", args.status),
                ("--summary", args.summary),
            ] if not val
        ]
        if missing:
            print(
                f"Error: missing required args: {', '.join(missing)} "
                f"(or pass --from-file <path>)"
            )
            sys.exit(1)
        task_id = args.task_id
        body = {
            "session_id": args.session_id,
            "agent": args.agent,
            "status": args.status,
            "confidence": args.confidence,
            "output_summary": args.summary,
            "risks_flagged": args.risks or [],
            "dependencies": args.dependencies or [],
            "suggested_reviewer_focus": args.reviewer_focus or [],
        }
        if args.output_dir:
            body["output_dir"] = args.output_dir
    r = client.post(f"/api/v1/orgs/{args.org}/tasks/{task_id}/completion", json=body)
    if not _ok(r):
        return



def cmd_progress(args: argparse.Namespace) -> None:
    """Agent callback: emit a mid-task progress note.

    Single-arg flow only — message text is short enough that a JSON file
    isn't needed. The Bash(happyranch:*) baseline allow rule matches the whole
    invocation as one line.
    """
    if not args.org:
        print("error: --org <slug> is required for agent callbacks", file=sys.stderr)
        sys.exit(1)
    try:
        client = OpcClient.from_env()
    except (DaemonNotRunning, DaemonStateInconsistent) as exc:
        print(f"Error: {exc}")
        sys.exit(1)
    r = client.post(
        f"/api/v1/orgs/{args.org}/tasks/{args.task_id}/progress",
        json={
            "session_id": args.session_id,
            "agent": args.agent,
            "message": args.message,
        },
    )
    if not _ok(r):
        return



def _dispatch_payload_from_file(path: str) -> dict:
    """Load a talk-dispatch payload from a JSON file.

    Same single-line `happyranch` constraint as the other agent callbacks. Required
    keys: ``talk_id`` (used in the URL path) and ``brief`` (the new task's
    description). Optional: ``target_agent``, ``team``.
    """
    import json as _json
    with open(path) as f:
        data = _json.load(f)
    talk_id = data.get("talk_id")
    if not talk_id or not str(talk_id).strip():
        raise ValueError("dispatch file missing or empty 'talk_id'")
    brief = data.get("brief")
    if not brief or not str(brief).strip():
        raise ValueError("dispatch file missing or empty 'brief'")
    return data



def cmd_dispatch(args: argparse.Namespace) -> None:
    """Agent callback: dispatch a new task from inside an open talk."""
    if not args.org:
        print("error: --org <slug> is required for agent callbacks", file=sys.stderr)
        sys.exit(1)
    try:
        client = OpcClient.from_env()
    except (DaemonNotRunning, DaemonStateInconsistent) as exc:
        print(f"Error: {exc}")
        sys.exit(1)

    import json as _json
    try:
        data = _dispatch_payload_from_file(args.from_file)
    except (OSError, _json.JSONDecodeError, ValueError) as exc:
        print(f"Error reading dispatch file {args.from_file}: {exc}")
        sys.exit(1)

    talk_id = data["talk_id"]
    body: dict = {"brief": data["brief"]}
    if data.get("target_agent"):
        body["target_agent"] = data["target_agent"]
    if data.get("team"):
        body["team"] = data["team"]

    r = client.post(f"/api/v1/orgs/{args.org}/talks/{talk_id}/dispatch", json=body)
    if not _ok(r):
        return
    result = r.json()
    print(
        f"ok: dispatched {result['task_id']} "
        f"(team={result['team']} agent={result['assigned_agent']} "
        f"from {result['dispatched_from_talk_id']})"
    )



def cmd_recall(args: argparse.Namespace) -> None:
    """Fetch a task's brief, canonical outcome, and optionally output files.

    Prints the daemon's JSON response as-is — agents consume it through the
    start-task skill, humans pipe it to ``jq``. A 404 is treated as an error
    and the process exits 1 so agent scripts can detect missing tasks.
    """
    import json as _json

    try:
        client = OpcClient.from_env()
    except (DaemonNotRunning, DaemonStateInconsistent) as exc:
        print(f"Error: {exc}")
        sys.exit(1)
    slug = resolve_org_slug(
        args_org=args.org, available=_shared._fetch_available_orgs(client),
    )
    params: dict[str, str] = {}
    if args.tree:
        params["tree"] = "true"
    if args.fetch_output:
        params["include_output"] = "true"
    r = client.get(f"/api/v1/orgs/{slug}/tasks/{args.task_id}/recall", params=params)
    if r.status_code == 404:
        print(f"Task {args.task_id} not found.")
        sys.exit(1)
    if not _ok(r):
        return
    print(_json.dumps(r.json(), indent=2))



def cmd_resolve_escalation(args: argparse.Namespace) -> None:
    client = OpcClient.from_env()
    slug = resolve_org_slug(
        args_org=args.org, available=_shared._fetch_available_orgs(client),
    )
    r = client.post(
        f"/api/v1/orgs/{slug}/tasks/{args.task_id}/resolve-escalation",
        json={"decision": args.decision, "rationale": args.rationale},
    )
    if not _ok(r):
        return
    body = r.json()
    print(f"ok: {args.task_id} -> {body['new_status']}")



def cmd_cancel(args: argparse.Namespace) -> None:
    """Cancel a task and (by default) its delegated subtree.

    Defaults attribution to the founder; an agent can attribute the cancel to
    itself via ``--as-agent`` (advisory — see the daemon cancel route).
    """
    try:
        client = OpcClient.from_env()
    except (DaemonNotRunning, DaemonStateInconsistent) as exc:
        print(f"Error: {exc}")
        sys.exit(1)
    slug = resolve_org_slug(
        args_org=args.org, available=_shared._fetch_available_orgs(client),
    )
    payload = {"rationale": args.rationale or "", "cascade": not args.no_cascade}
    if args.as_agent:
        payload["actor"] = args.as_agent
    r = client.post(
        f"/api/v1/orgs/{slug}/tasks/{args.task_id}/cancel",
        json=payload,
    )
    if r.status_code == 404:
        print(f"Task {args.task_id} not found.")
        sys.exit(1)
    if r.status_code == 409:
        detail = {}
        try:
            detail = r.json().get("detail", {})
        except ValueError:
            pass
        if detail.get("code") == "task_already_terminal":
            print(
                f"Task {args.task_id} is already {detail.get('current_status')}; "
                "nothing to cancel."
            )
            sys.exit(1)
        # fall through to generic error handler
    if not _ok(r):
        return
    body = r.json()
    cancelled = body.get("cancelled") or []
    killed = body.get("killed") or []
    print(f"Cancelled {len(cancelled)} task(s): {', '.join(cancelled)}")
    if killed:
        for k in killed:
            print(f"  SIGTERM -> {k['task_id']} ({k['agent']}, pid={k['pid']})")
    else:
        print("  No live subprocesses attached.")



def cmd_revisit(args: argparse.Namespace) -> None:
    """Founder action: spawn a NEW root task that inherits a terminal
    predecessor's brief, with the team manager gated on an audit-log-backed
    context header. TTY-gated — no --yes bypass."""
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        print("happyranch revisit requires an interactive terminal (no --yes bypass).")
        sys.exit(1)

    if args.note_file:
        try:
            note: str | None = Path(args.note_file).expanduser().read_text(encoding="utf-8")
        except OSError as exc:
            print(f"Error reading note file {args.note_file}: {exc}")
            sys.exit(1)
        if not note.strip():
            print("Error: note is empty")
            sys.exit(1)
    else:
        note = args.note

    print(f"About to revisit {args.task_id} (founder-initiated).")
    print("This creates a NEW root task that inherits the original brief.")
    print(
        f"The existing lineage rooted at {args.task_id} stays frozen "
        "(read-only history)."
    )
    print(
        "The team manager for the new root can inspect the old lineage via "
        "`happyranch details` / `happyranch audit` / `happyranch recall`."
    )
    reply = input("Continue? [y/N] ").strip().lower()
    if reply not in ("y", "yes"):
        print("Aborted.")
        sys.exit(1)

    try:
        client = OpcClient.from_env()
    except (DaemonNotRunning, DaemonStateInconsistent) as exc:
        print(f"Error: {exc}")
        sys.exit(1)

    if args.session_timeout_seconds is not None and args.session_timeout_seconds <= 0:
        print("Error: --session-timeout-seconds must be a positive integer")
        sys.exit(1)

    slug = resolve_org_slug(
        args_org=args.org, available=_shared._fetch_available_orgs(client),
    )
    payload: dict = {"founder_note": note}
    if args.session_timeout_seconds is not None:
        payload["session_timeout_seconds"] = args.session_timeout_seconds
    r = client.post(
        f"/api/v1/orgs/{slug}/tasks/{args.task_id}/revisit",
        json=payload,
    )
    if r.status_code == 404:
        print(f"Task {args.task_id} not found.")
        sys.exit(1)
    if r.status_code == 409:
        detail = {}
        try:
            detail = r.json().get("detail", {})
        except ValueError:
            pass
        if detail.get("code") == "cannot_revisit":
            print(
                f"Cannot revisit {args.task_id}: "
                f"predecessor {detail.get('predecessor_root_task_id')} "
                f"is {detail.get('predecessor_status')}."
            )
            sys.exit(1)
    if not _ok(r):
        return
    body = r.json()
    new_id = body["new_root_task_id"]
    print(
        f"Created {new_id} (predecessor: {body['predecessor_root_task_id']}, "
        f"flagged: {body['flagged_task_id']})."
    )
    print(f"Submitted {new_id}. Attach with: happyranch tail {new_id}")



def register(sub) -> None:
    p_run = sub.add_parser("run", help="Run a task")
    p_run.add_argument("--org", default=None, help="Org slug (or set HAPPYRANCH_ORG_SLUG; auto-inferred when only one org)")
    p_run.add_argument(
        "--team", default=None,
        help="Team to route the task to (default: engineering)",
    )
    p_run_brief = p_run.add_mutually_exclusive_group(required=True)
    p_run_brief.add_argument("--brief", help="Task description (inline string)")
    p_run_brief.add_argument(
        "--brief-file",
        help="Path to a file whose contents become the task brief",
    )
    p_run.set_defaults(func=cmd_run)

    p_details = sub.add_parser("details", help="Show task details")
    p_details.add_argument("--org", default=None, help="Org slug (or set HAPPYRANCH_ORG_SLUG; auto-inferred when only one org)")
    p_details.add_argument("task_id", help="Task ID (e.g. TASK-001)")
    p_details.add_argument(
        "--full",
        action="store_true",
        help="Show full per-step output summaries (no 80-char truncation)",
    )
    p_details.set_defaults(func=cmd_details)

    p_tail = sub.add_parser("tail", help="Stream events for an existing task")
    p_tail.add_argument("--org", default=None, help="Org slug (or set HAPPYRANCH_ORG_SLUG; auto-inferred when only one org)")
    p_tail.add_argument("task_id", help="Task ID")
    p_tail.set_defaults(func=cmd_tail)

    p_tasks = sub.add_parser("tasks", help="List recent tasks")
    p_tasks.add_argument("--org", default=None, help="Org slug (or set HAPPYRANCH_ORG_SLUG; auto-inferred when only one org)")
    p_tasks.add_argument("--limit", type=int, default=20, help="Max tasks to show")
    p_tasks.set_defaults(func=cmd_tasks)

    p_audit = sub.add_parser("audit", help="Show filtered audit-log entries")
    p_audit.add_argument("--org", default=None, help="Org slug (or set HAPPYRANCH_ORG_SLUG; auto-inferred when only one org)")
    p_audit.add_argument("task_id", nargs="?", default=None,
                         help="Optional task id to filter by (e.g. TASK-007)")
    p_audit.add_argument("--agent", default=None, help="Filter by agent name")
    p_audit.add_argument("--action", default=None,
                         help="Filter by action (session_start, session_end, completion_report, ...)")
    p_audit.add_argument("--since", default=None,
                         help="ISO-8601 timestamp; only entries at or after this time")
    p_audit.add_argument("--limit", type=int, default=None,
                         help="Cap to the most recent N entries")
    p_audit.add_argument("--json", action="store_true",
                         help="Emit raw JSON instead of the human-readable table")
    p_audit.set_defaults(func=cmd_audit)

    p_tokens = sub.add_parser(
        "tokens",
        help="Show scoped per-session token usage and rollups",
    )
    p_tokens.add_argument("--org", default=None,
                          help="Org slug (or set HAPPYRANCH_ORG_SLUG; auto-inferred when only one org)")
    p_tokens.add_argument("--task-id", dest="task_id", default=None,
                          help="Filter by task id (e.g. TASK-007)")
    p_tokens.add_argument("--agent", default=None, help="Filter by agent name")
    p_tokens.add_argument("--since", default=None,
                          help="ISO-8601 date or timestamp; only rows at or after this time")
    p_tokens.add_argument("--scope-type", dest="scope_type", default=None,
                          choices=["task", "thread", "talk"],
                          help="Filter by usage scope type")
    p_tokens.add_argument("--scope-id", dest="scope_id", default=None,
                          help="Filter by scope id, e.g. TASK-007 or THR-001")
    p_tokens.add_argument("--thread-id", dest="thread_id", default=None,
                          help="Filter by direct or task-attributed thread id")
    p_tokens.add_argument("--talk-id", dest="talk_id", default=None,
                          help="Filter by direct or task-attributed talk id")
    p_tokens.add_argument("--purpose", default=None,
                          help="Filter by thread invocation purpose")
    p_tokens.add_argument("--limit", type=int, default=None,
                          help="Cap to the most recent N rows (default: 20; ignored for rollups)")
    p_tokens.add_argument("--json", action="store_true",
                          help="Emit raw JSON instead of the human-readable table")
    p_tokens_group = p_tokens.add_mutually_exclusive_group()
    p_tokens_group.add_argument("--by-agent", dest="by_agent", action="store_true",
                                help="Rollup: one row per agent")
    p_tokens_group.add_argument("--by-task", dest="by_task", action="store_true",
                                help="Rollup: one row per task")
    p_tokens_group.add_argument("--by-thread", dest="by_thread", action="store_true",
                                help="Rollup: one row per thread")
    p_tokens_group.add_argument("--by-talk", dest="by_talk", action="store_true",
                                help="Rollup: one row per talk")
    p_tokens.set_defaults(func=cmd_tokens)

    p_dispatch = sub.add_parser("dispatch", help="Dispatch a new task from an open talk")
    p_dispatch.add_argument("--org", required=True, help="Org slug (required for agent callbacks)")
    p_dispatch.add_argument(
        "--from-file", dest="from_file", required=True,
        help="Path to JSON file with dispatch payload (talk_id, brief, optional target_agent/team)",
    )
    p_dispatch.set_defaults(func=cmd_dispatch)

    p_recall = sub.add_parser(
        "recall",
        help="Recall a task: brief, outcome, optional output contents",
    )
    p_recall.add_argument("--org", default=None, help="Org slug (or set HAPPYRANCH_ORG_SLUG; auto-inferred when only one org)")
    p_recall.add_argument("task_id", help="Task ID (e.g. TASK-001)")
    p_recall.add_argument("--tree", action="store_true",
                          help="Include the full subtree of child tasks")
    p_recall.add_argument("--fetch-output", dest="fetch_output",
                          action="store_true",
                          help="Inline output file contents (capped at 200KB)")
    p_recall.set_defaults(func=cmd_recall)

    p_rep = sub.add_parser("report-completion", help="Agent callback: report task completion")
    p_rep.add_argument("--org", required=True, help="Org slug (required for agent callbacks)")
    p_rep.add_argument(
        "--from-file", dest="from_file", default=None,
        help="Path to a JSON file containing the completion payload. "
             "Preferred by agents — keeps the tool call a single line so "
             "Claude Code's Bash(happyranch:*) allow rule matches. Keys: task_id, "
             "session_id, agent, status, summary (required), plus optional "
             "confidence, risks, dependencies, reviewer_focus.",
    )
    p_rep.add_argument("--task-id", default=None)
    p_rep.add_argument("--session-id", default=None)
    p_rep.add_argument("--agent", default=None)
    p_rep.add_argument("--status", default=None, choices=["completed", "blocked"])
    p_rep.add_argument("--confidence", type=int, default=80)
    p_rep.add_argument("--summary", default=None)
    p_rep.add_argument("--risks", action="append", default=[])
    p_rep.add_argument("--dependencies", action="append", default=[])
    p_rep.add_argument("--reviewer-focus", action="append", default=[], dest="reviewer_focus")
    p_rep.add_argument("--output-dir", dest="output_dir", default=None,
                       help="Relative path to the output directory under the agent workspace")
    p_rep.set_defaults(func=cmd_report_completion)

    p_prog = sub.add_parser(
        "progress", help="Agent callback: emit a mid-task progress note"
    )
    p_prog.add_argument("--org", required=True, help="Org slug (required for agent callbacks)")
    p_prog.add_argument("--task-id", required=True)
    p_prog.add_argument("--session-id", required=True)
    p_prog.add_argument("--agent", required=True)
    p_prog.add_argument(
        "--message", required=True,
        help="Short, human-readable description of the current step "
             "(e.g. 'Phase 3 of 6: tests passing'). Quote it as one shell arg.",
    )
    p_prog.set_defaults(func=cmd_progress)

    p_resolve = sub.add_parser("resolve-escalation", help="Resolve an escalated task (founder only)")
    p_resolve.add_argument("--org", default=None, help="Org slug (or set HAPPYRANCH_ORG_SLUG; auto-inferred when only one org)")
    p_resolve.add_argument("--task-id", required=True)
    p_resolve.add_argument("--decision", required=True, choices=["approve", "reject"])
    p_resolve.add_argument("--rationale", required=True)
    p_resolve.set_defaults(func=cmd_resolve_escalation)

    p_cancel = sub.add_parser(
        "cancel",
        help="Cancel a task: SIGTERMs live subprocesses and cascades down the subtree",
    )
    p_cancel.add_argument("task_id", help="Task ID to cancel (e.g. TASK-052)")
    p_cancel.add_argument(
        "--org", default=None,
        help="Org slug (or set HAPPYRANCH_ORG_SLUG; auto-inferred when only one org)",
    )
    p_cancel.add_argument(
        "--rationale", default="",
        help="Optional note recorded on every cancelled row",
    )
    p_cancel.add_argument(
        "--no-cascade", action="store_true",
        help="Cancel only this task, not its descendants "
             "(dangerous: leaves any live children parentless)",
    )
    p_cancel.add_argument(
        "--as-agent", default=None, metavar="NAME",
        help="Attribute the cancellation to this agent instead of the founder "
             "(advisory; recorded in the audit log and task note)",
    )
    p_cancel.set_defaults(func=cmd_cancel)

    p_revisit = sub.add_parser(
        "revisit",
        help=(
            "Spawn a NEW root that inherits a terminal predecessor's brief "
            "(founder; TTY-gated)"
        ),
    )
    p_revisit.add_argument("--org", default=None, help="Org slug (or set HAPPYRANCH_ORG_SLUG; auto-inferred when only one org)")
    p_revisit.add_argument("task_id", help="Any task id in the lineage to revisit")
    p_revisit_note = p_revisit.add_mutually_exclusive_group()
    p_revisit_note.add_argument(
        "--note", default=None,
        help="Optional founder hint surfaced to the team manager in the first-step prompt header",
    )
    p_revisit_note.add_argument(
        "--note-file", default=None,
        help="Path to a file whose contents become the founder note (mutually exclusive with --note)",
    )
    p_revisit.add_argument(
        "--session-timeout-seconds", type=int, default=None, dest="session_timeout_seconds",
        help=(
            "Per-task subprocess timeout in seconds. Persisted on the new root and "
            "inherited by every delegated child + auto-revisit. Omit to inherit from "
            "the predecessor (which itself falls through to org/Settings)."
        ),
    )
    p_revisit.set_defaults(func=cmd_revisit)
