"""OPC — unified CLI for the multi-agent tourism organization."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from src.client.client import DaemonNotRunning, DaemonStateInconsistent, OpcClient


def _ok(r) -> bool:
    """True if response is 2xx. On error: print a friendly message and exit(1).

    Translates the daemon's structured `{"detail": {"code": ...}}` errors into
    actionable user-facing sentences instead of dumping JSON.
    """
    if 200 <= r.status_code < 300:
        return True
    detail = {}
    try:
        body = r.json()
        if isinstance(body.get("detail"), dict):
            detail = body["detail"]
    except ValueError:
        pass
    code = detail.get("code")
    if code == "no_active_runtime":
        print("No active runtime. Run `opc use <runtime-path>` first (see `opc init`).")
    elif code == "active_tasks_in_flight":
        print(f"Cannot proceed: tasks still in flight ({detail.get('task_ids')}).")
    elif code == "unknown_session":
        print(
            f"Session not recognised by daemon for task {detail.get('task_id')} "
            f"(agent {detail.get('agent')}). The daemon may have restarted, or "
            "the task already completed.",
        )
    elif code == "session_mismatch":
        print(
            f"Session id mismatch — daemon expected {detail.get('active')} "
            f"but got {detail.get('got')}.",
        )
    else:
        print(f"Error ({r.status_code}): {r.text}")
    sys.exit(1)


def resolve_org_slug(*, args_org: str | None, available: list[str]) -> str:
    """Resolve the per-command --org per the spec §7.4 chain."""
    if args_org:
        return args_org
    env = os.environ.get("OPC_ORG_SLUG")
    if env:
        return env
    if len(available) == 1:
        return available[0]
    if not available:
        print(
            "error: no orgs registered yet\n"
            "create one with: opc orgs init <slug> [--from <example-path>]",
            file=sys.stderr,
        )
        sys.exit(1)
    print(
        "error: --org <slug> is required\navailable orgs:",
        file=sys.stderr,
    )
    for slug in sorted(available):
        print(f"  {slug}", file=sys.stderr)
    sys.exit(1)


def _fetch_available_orgs(client) -> list[str]:
    r = client.get("/api/v1/orgs")
    if r.status_code != 200:
        return []
    return [o["slug"] for o in r.json().get("orgs", [])]


# ── subcommands ──────────────────────────────────────────────


def cmd_init(args: argparse.Namespace) -> None:
    """Create + register a multi-org runtime container with the daemon."""
    try:
        client = OpcClient.from_env()
    except (DaemonNotRunning, DaemonStateInconsistent) as exc:
        print(f"Error: {exc}")
        sys.exit(1)
    r = client.post(
        "/api/v1/runtime",
        json={"path": str(Path(args.path).expanduser())},
    )
    if r.status_code != 200:
        print(f"Error ({r.status_code}): {r.text}")
        sys.exit(1)
    print(f"runtime: {r.json()['runtime']}")


def cmd_runtime(args: argparse.Namespace) -> None:
    """Show the active runtime container."""
    try:
        client = OpcClient.from_env()
    except (DaemonNotRunning, DaemonStateInconsistent) as exc:
        print(f"Error: {exc}")
        sys.exit(1)
    r = client.get("/api/v1/runtime")
    if r.status_code != 200:
        print(f"Error ({r.status_code}): {r.text}")
        sys.exit(1)
    body = r.json()
    if body["runtime"] is None:
        print("(no active runtime)")
    else:
        print(f"runtime: {body['runtime']}")


def cmd_use(args: argparse.Namespace) -> None:
    """Switch the daemon's active runtime container."""
    try:
        client = OpcClient.from_env()
    except (DaemonNotRunning, DaemonStateInconsistent) as exc:
        print(f"Error: {exc}")
        sys.exit(1)
    r = client.post(
        "/api/v1/runtime/use",
        json={"path": str(Path(args.path).expanduser())},
    )
    if r.status_code == 409:
        print(f"Cannot switch runtime: {r.json()['detail']}")
        sys.exit(1)
    if r.status_code != 200:
        print(f"Error ({r.status_code}): {r.text}")
        sys.exit(1)
    print(f"runtime: {r.json()['runtime']}")


def cmd_orgs(args: argparse.Namespace) -> None:
    """List orgs registered with the active runtime."""
    try:
        client = OpcClient.from_env()
    except (DaemonNotRunning, DaemonStateInconsistent) as exc:
        print(f"Error: {exc}")
        sys.exit(1)
    r = client.get("/api/v1/orgs")
    if not _ok(r):
        return
    for org in r.json()["orgs"]:
        print(f"  {org['slug']:30s}  {org['root']}")


def cmd_orgs_init(args: argparse.Namespace) -> None:
    """Create a new org subfolder inside the active runtime."""
    try:
        client = OpcClient.from_env()
    except (DaemonNotRunning, DaemonStateInconsistent) as exc:
        print(f"Error: {exc}")
        sys.exit(1)
    payload: dict = {"slug": args.slug}
    if args.from_path:
        payload["from_example"] = args.from_path
    r = client.post("/api/v1/orgs", json=payload)
    if not _ok(r):
        return
    print(f"created: {r.json()['slug']}")


def cmd_orgs_unload(args: argparse.Namespace) -> None:
    """Drop an org's state from the daemon's in-memory registry."""
    try:
        client = OpcClient.from_env()
    except (DaemonNotRunning, DaemonStateInconsistent) as exc:
        print(f"Error: {exc}")
        sys.exit(1)
    r = client.request("DELETE", f"/api/v1/orgs/{args.slug}")
    if not _ok(r):
        return
    print(f"unloaded: {r.json()['slug']}")


def _fmt_ts(iso: str | None, *, date_only: bool = False) -> str:
    """Render a UTC ISO timestamp from the daemon in the machine's local tz.

    Storage is always UTC; display is always local. Unknown or malformed
    values render as "-" so callers don't need to pre-check.
    """
    if not iso:
        return "-"
    from datetime import datetime
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return iso
    if dt.tzinfo is not None:
        dt = dt.astimezone()
    return dt.strftime("%Y-%m-%d" if date_only else "%Y-%m-%d %H:%M:%S")


def cmd_run(args: argparse.Namespace) -> None:
    """Submit a task and return immediately.

    The CLI does not stream events. Use `opc tail <task_id>` to attach to
    a running task, or `opc details <task_id>` for a snapshot.
    """
    try:
        client = OpcClient.from_env()
    except (DaemonNotRunning, DaemonStateInconsistent) as exc:
        print(f"Error: {exc}")
        sys.exit(1)

    slug = resolve_org_slug(
        args_org=args.org, available=_fetch_available_orgs(client),
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
    print(f"Submitted {task_id}. Attach with: opc tail {task_id}")


def cmd_tail(args: argparse.Namespace) -> None:
    """Reattach to a running task and stream its events until terminal."""
    try:
        client = OpcClient.from_env()
    except (DaemonNotRunning, DaemonStateInconsistent) as exc:
        print(f"Error: {exc}")
        sys.exit(1)
    slug = resolve_org_slug(
        args_org=args.org, available=_fetch_available_orgs(client),
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
        # task id from `opc tail`) lands here. Surface a clean message instead
        # of an httpx traceback.
        print(f"Error: stream failed for {task_id} ({exc.response.status_code})")
        sys.exit(1)
    except KeyboardInterrupt:
        print(f"\nDetached. Reattach with: opc tail {task_id}")


def cmd_tasks(args: argparse.Namespace) -> None:
    """List recent tasks."""
    try:
        client = OpcClient.from_env()
    except (DaemonNotRunning, DaemonStateInconsistent) as exc:
        print(f"Error: {exc}")
        sys.exit(1)
    slug = resolve_org_slug(
        args_org=args.org, available=_fetch_available_orgs(client),
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
        print(f"{t['id']:<12} {team:<16} {status:<22} {agent:<18} {brief}")


def cmd_details(args: argparse.Namespace) -> None:
    """Show status of a specific task."""
    try:
        client = OpcClient.from_env()
    except (DaemonNotRunning, DaemonStateInconsistent) as exc:
        print(f"Error: {exc}")
        sys.exit(1)
    slug = resolve_org_slug(
        args_org=args.org, available=_fetch_available_orgs(client),
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

    print(f"Task:       {task['id']}")
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
            # rows. Other actions stay terse — payload is in `opc audit --json`.
            if log["action"] == "progress":
                msg = (log.get("payload") or {}).get("message", "")
                if msg:
                    line += f"  {msg}"
            print(line)

    # Revisit footer: shown only when this task HAS been revisited.
    direct = body.get("direct_revisits") or []
    if direct:
        print(f"\nRevisited as: {', '.join(direct)}")


def cmd_agents(args: argparse.Namespace) -> None:
    """Show agent performance tiers."""
    try:
        client = OpcClient.from_env()
    except (DaemonNotRunning, DaemonStateInconsistent) as exc:
        print(f"Error: {exc}")
        sys.exit(1)
    slug = resolve_org_slug(
        args_org=args.org, available=_fetch_available_orgs(client),
    )
    r = client.get(f"/api/v1/orgs/{slug}/agents")
    if not _ok(r):
        return
    entries = r.json()["agents"]

    if args.agent is not None:
        match = next((e for e in entries if e["name"] == args.agent), None)
        if match is None:
            known = ", ".join(e["name"] for e in entries) or "(none)"
            print(f"Error: no agent named {args.agent!r}. Known: {known}")
            sys.exit(1)
        sc = match.get("scorecard")
        print(f"{match['name']}  Tier: {match['tier']}")
        if sc:
            print(f"  Acceptance: {sc['acceptance_rate']:.0%}  Revision: {sc['revision_rate']:.0%}  Errors: {sc['error_count']}")
            print(f"  Period: {_fmt_ts(sc['period_start'], date_only=True)} to {_fmt_ts(sc['period_end'], date_only=True)}")
            print(f"  Updated: {_fmt_ts(sc['updated_at'])}")
        else:
            print("  No performance data yet (default tier).")
        return

    print(f"{'Agent':<22} {'Tier':<8}")
    print("-" * 30)
    for entry in entries:
        print(f"{entry['name']:<22} {entry['tier']:<8}")
    if args.detail:
        print()
        for entry in entries:
            sc = entry.get("scorecard")
            if sc:
                print(f"{entry['name']}:")
                print(f"  Acceptance: {sc['acceptance_rate']:.0%}  Revision: {sc['revision_rate']:.0%}  Errors: {sc['error_count']}")
                print(f"  Period: {_fmt_ts(sc['period_start'], date_only=True)} to {_fmt_ts(sc['period_end'], date_only=True)}")


def cmd_init_agent(args: argparse.Namespace) -> None:
    """Initialize agent workspaces by streaming progress from the daemon."""
    import json as _json

    import httpx

    try:
        client = OpcClient.from_env()
    except (DaemonNotRunning, DaemonStateInconsistent) as exc:
        print(f"Error: {exc}")
        sys.exit(1)
    slug = resolve_org_slug(
        args_org=args.org, available=_fetch_available_orgs(client),
    )
    try:
        for payload in client.stream(
            "POST", f"/api/v1/orgs/{slug}/agents/init", json={"agent": args.agent},
        ):
            try:
                event = _json.loads(payload)
            except _json.JSONDecodeError:
                print(payload)
                continue
            if event.get("phase") == "all_done":
                print("Done.")
                return
            agent = event.get("agent", "")
            phase = event.get("phase", "")
            # The daemon emits {"phase": "error", "detail": "<reason>"} when a
            # workspace init fails. Surface the reason — without it the user
            # sees "[dev_agent] error" with no clue what broke.
            detail = event.get("detail")
            line = f"  [{agent}] {phase}"
            if detail:
                line += f": {detail}"
            print(line)
    except httpx.HTTPStatusError as exc:
        # OpcClient.stream calls raise_for_status(), so a 409 (e.g. idle
        # daemon — no active runtime) lands here. Match the cmd_tail pattern.
        print(f"Error: init stream failed ({exc.response.status_code})")
        sys.exit(1)
    except KeyboardInterrupt:
        print("Init cancelled (daemon will continue).")


def cmd_audit(args: argparse.Namespace) -> None:
    """Show filtered audit-log entries via the daemon."""
    import json as _json

    try:
        client = OpcClient.from_env()
    except (DaemonNotRunning, DaemonStateInconsistent) as exc:
        print(f"Error: {exc}")
        sys.exit(1)

    slug = resolve_org_slug(
        args_org=args.org, available=_fetch_available_orgs(client),
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


def _completion_payload_from_file(path: str) -> tuple[str, dict]:
    """Load a completion payload from a JSON file.

    Agents use this path because multi-line bash commands (backslash
    continuations) count as separate subcommands under Claude Code's
    permission model, which breaks the narrow ``Bash(opc:*)`` allow rule.
    Writing a JSON file and invoking `opc report-completion --from-file
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
    if data.get("artifact_dir"):
        body["artifact_dir"] = data["artifact_dir"]
    # Manager-only. Workers omit `decision`; team managers set it to a
    # NextStep object (delegate/done/escalate). Passed through verbatim —
    # the orchestrator parses it via the NextStep pydantic model.
    if data.get("decision") is not None:
        body["decision"] = data["decision"]
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
        if args.artifact_dir:
            body["artifact_dir"] = args.artifact_dir
    r = client.post(f"/api/v1/orgs/{args.org}/tasks/{task_id}/completion", json=body)
    if not _ok(r):
        return


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
        f"/api/v1/orgs/{args.org}/agents/{args.agent}/learnings",
        json={"session_id": args.session_id, "task_id": args.task_id, "text": args.text},
    )
    if not _ok(r):
        return


def cmd_progress(args: argparse.Namespace) -> None:
    """Agent callback: emit a mid-task progress note.

    Single-arg flow only — message text is short enough that a JSON file
    isn't needed. The Bash(opc:*) baseline allow rule matches the whole
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


def _manage_repo_payload_from_file(path: str) -> tuple[str, dict]:
    """Load a manage-repo payload from a JSON file.

    Same pattern as report-completion: single-line `opc` invocation avoids
    Claude Code's permission matcher splitting on newlines.

    Returns ``(agent, body)`` shaped for the daemon's manage-repo endpoint.
    """
    import json as _json
    with open(path) as f:
        data = _json.load(f)
    required = ["action", "agent", "repo_name"]
    missing = [k for k in required if not data.get(k)]
    if missing:
        raise ValueError(f"manage-repo file missing keys: {missing}")
    body = {"action": data["action"], "repo_name": data["repo_name"]}
    if data.get("url"):
        body["url"] = data["url"]
    return data["agent"], body


def cmd_manage_repo(args: argparse.Namespace) -> None:
    """Agent callback: add, remove, or update a repo in agent.yaml."""
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
            agent, body = _manage_repo_payload_from_file(args.from_file)
        except (OSError, _json.JSONDecodeError, ValueError) as exc:
            print(f"Error reading manage-repo file {args.from_file}: {exc}")
            sys.exit(1)
    else:
        agent = args.agent
        body = {"action": args.action, "repo_name": args.repo_name}
        if args.url:
            body["url"] = args.url

    r = client.post(f"/api/v1/orgs/{args.org}/agents/{agent}/repos", json=body)
    if not _ok(r):
        return
    print(f"ok: {args.action or body['action']} {body['repo_name']}")


def _manage_agent_payload_from_file(path: str) -> dict:
    """Load a manage-agent payload from a JSON file.

    The daemon (see ManageAgentBody in src/daemon/routes/agents.py) accepts two
    mutually-exclusive auth paths: (task_id + session_id) OR talk_id. This
    client-side check fast-fails obvious shape errors before the HTTP round trip.
    """
    import json as _json
    with open(path) as f:
        data = _json.load(f)
    missing_base = [k for k in ("action", "name") if not data.get(k)]
    if missing_base:
        raise ValueError(f"manage-agent file missing keys: {missing_base}")
    has_task = bool(data.get("task_id")) and bool(data.get("session_id"))
    has_partial_task = bool(data.get("task_id")) != bool(data.get("session_id"))
    has_talk = bool(data.get("talk_id"))
    if has_partial_task:
        raise ValueError("manage-agent file must supply task_id and session_id together")
    if has_task and has_talk:
        raise ValueError("manage-agent file must supply either (task_id + session_id) or talk_id, not both")
    if not has_task and not has_talk:
        raise ValueError("manage-agent file must supply either (task_id + session_id) or talk_id")
    return data


def cmd_manage_agent(args: argparse.Namespace) -> None:
    """Agent callback: enroll, update, or terminate an agent."""
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
            body = _manage_agent_payload_from_file(args.from_file)
        except (OSError, _json.JSONDecodeError, ValueError) as exc:
            print(f"Error reading manage-agent file {args.from_file}: {exc}")
            sys.exit(1)
    else:
        body = {
            "action": args.action,
            "name": args.name,
        }
        if args.task_id:
            body["task_id"] = args.task_id
        if args.session_id:
            body["session_id"] = args.session_id
        talk_id = getattr(args, "talk_id", None)
        if talk_id:
            body["talk_id"] = talk_id
        if args.description:
            body["description"] = args.description
        if args.system_prompt:
            body["system_prompt"] = args.system_prompt
        executor = getattr(args, "executor", None)
        if executor is not None:
            body["executor"] = executor
        if args.repos:
            body["repos"] = _json.loads(args.repos)

    r = client.post(f"/api/v1/orgs/{args.org}/agents/manage", json=body)
    if not _ok(r):
        return
    result = r.json()
    status = result.get("status", "ok")
    print(f"ok: {body['action']} {body['name']} (status: {status})")


def _dispatch_payload_from_file(path: str) -> dict:
    """Load a talk-dispatch payload from a JSON file.

    Same single-line `opc` constraint as the other agent callbacks. Required
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


def cmd_enrollments(args: argparse.Namespace) -> None:
    """List agent enrollment requests."""
    try:
        client = OpcClient.from_env()
    except (DaemonNotRunning, DaemonStateInconsistent) as exc:
        print(f"Error: {exc}")
        sys.exit(1)
    slug = resolve_org_slug(
        args_org=args.org, available=_fetch_available_orgs(client),
    )
    params = {}
    if args.status:
        params["status"] = args.status
    r = client.get(f"/api/v1/orgs/{slug}/agents/enrollments", params=params)
    if not _ok(r):
        return
    enrollments = r.json()["enrollments"]
    if not enrollments:
        print("No enrollments found.")
        return
    print(f"{'Name':<22} {'Status':<12} {'Description':<40} Created")
    print("-" * 90)
    for e in enrollments:
        desc = e["description"][:37] + "..." if len(e["description"]) > 37 else e["description"]
        print(f"{e['name']:<22} {e['status']:<12} {desc:<40} {_fmt_ts(e['created_at'])}")


def cmd_approve_agent(args: argparse.Namespace) -> None:
    """Founder action: approve a pending agent enrollment."""
    try:
        client = OpcClient.from_env()
    except (DaemonNotRunning, DaemonStateInconsistent) as exc:
        print(f"Error: {exc}")
        sys.exit(1)
    slug = resolve_org_slug(
        args_org=args.org, available=_fetch_available_orgs(client),
    )
    r = client.post(f"/api/v1/orgs/{slug}/agents/{args.name}/approve", json={})
    if not _ok(r):
        return
    print(f"Approved: {args.name}")


def cmd_reject_agent(args: argparse.Namespace) -> None:
    """Founder action: reject a pending agent enrollment."""
    try:
        client = OpcClient.from_env()
    except (DaemonNotRunning, DaemonStateInconsistent) as exc:
        print(f"Error: {exc}")
        sys.exit(1)
    slug = resolve_org_slug(
        args_org=args.org, available=_fetch_available_orgs(client),
    )
    r = client.post(f"/api/v1/orgs/{slug}/agents/{args.name}/reject", json={})
    if not _ok(r):
        return
    print(f"Rejected: {args.name}")


def cmd_backfill_enrollments(args: argparse.Namespace) -> None:
    """Founder recovery op: import pre-existing workspaces into the enrollment
    registry so `manage-agent update`/`terminate` can target them.

    TTY-gated — no --yes bypass. Safe to re-run (idempotent); second call
    reports all agents as already enrolled.
    """
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        print("opc backfill-enrollments requires an interactive terminal (no --yes bypass).")
        sys.exit(1)

    print("About to backfill the enrollment registry (founder-initiated).")
    print("This imports workspaces that lack enrollment rows into the registry")
    print("at status='approved'. No workspace files are modified.")
    reply = input("Continue? [y/N] ").strip().lower()
    if reply not in ("y", "yes"):
        print("Aborted.")
        sys.exit(1)

    try:
        client = OpcClient.from_env()
    except (DaemonNotRunning, DaemonStateInconsistent) as exc:
        print(f"Error: {exc}")
        sys.exit(1)

    slug = resolve_org_slug(
        args_org=args.org, available=_fetch_available_orgs(client),
    )
    r = client.post(f"/api/v1/orgs/{slug}/agents/backfill-enrollments", json={})
    if not _ok(r):
        return
    body = r.json()
    backfilled = body.get("backfilled", [])
    already = body.get("skipped_already_enrolled", [])
    unknown = body.get("skipped_unknown_prompt", [])
    if backfilled:
        print(f"Backfilled {len(backfilled)}:")
        for entry in backfilled:
            print(
                f"  - {entry['name']} (executor={entry['executor']}, "
                f"repos={entry['repos_count']})"
            )
    else:
        print("Backfilled 0.")
    if already:
        print(f"Already enrolled (skipped): {', '.join(already)}")
    if unknown:
        print(
            f"Unknown prompt (skipped — not in protocol loader): {', '.join(unknown)}"
        )


def cmd_recall(args: argparse.Namespace) -> None:
    """Fetch a task's brief, canonical outcome, and optionally artifact files.

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
        args_org=args.org, available=_fetch_available_orgs(client),
    )
    params: dict[str, str] = {}
    if args.tree:
        params["tree"] = "true"
    if args.fetch_artifact:
        params["include_artifact"] = "true"
    r = client.get(f"/api/v1/orgs/{slug}/tasks/{args.task_id}/recall", params=params)
    if r.status_code == 404:
        print(f"Task {args.task_id} not found.")
        sys.exit(1)
    if not _ok(r):
        return
    print(_json.dumps(r.json(), indent=2))


def _read_markdown_payload(path: str) -> dict:
    """Parse `/tmp/kb-<slug>.md` into the daemon's add/update JSON body.

    The file is the full entry (frontmatter + body); the daemon stamps
    `authored_*` / `updated_*` itself.
    """
    import yaml as _yaml
    with open(path) as f:
        text = f.read()
    if not text.startswith("---"):
        raise ValueError("missing frontmatter")
    if text.count("---") < 2:
        raise ValueError("missing closing '---' fence")
    _, fm_text, body = text.split("---", 2)
    fm = _yaml.safe_load(fm_text) or {}
    required = ("slug", "title", "type", "topic")
    missing = [k for k in required if not fm.get(k)]
    if missing:
        raise ValueError(f"frontmatter missing keys: {missing}")
    return {
        "slug": fm["slug"],
        "title": fm["title"],
        "type": fm["type"],
        "topic": fm["topic"],
        "tags": list(fm.get("tags") or []),
        "source_task": fm.get("source_task"),
        "supersedes": fm.get("supersedes"),
        "body": body.lstrip("\n"),
    }


def cmd_kb_list(args: argparse.Namespace) -> None:
    client = OpcClient.from_env()
    org_slug = resolve_org_slug(
        args_org=args.org, available=_fetch_available_orgs(client),
    )
    params = {}
    if args.topic:
        params["topic"] = args.topic
    if args.type:
        params["type"] = args.type
    r = client.get(f"/api/v1/orgs/{org_slug}/kb", params=params)
    if not _ok(r):
        return
    for e in r.json()["entries"]:
        print(f"{e['slug']:40s}  [{e['type']}/{e['topic']}]  {e['title']}")


def cmd_kb_get(args: argparse.Namespace) -> None:
    client = OpcClient.from_env()
    org_slug = resolve_org_slug(
        args_org=args.org, available=_fetch_available_orgs(client),
    )
    r = client.get(f"/api/v1/orgs/{org_slug}/kb/{args.slug}")
    if not _ok(r):
        return
    e = r.json()
    print(f"# {e['title']}")
    print(f"(slug={e['slug']}, type={e['type']}, topic={e['topic']}, "
          f"authored_by={e['authored_by']}, updated_at={_fmt_ts(e['updated_at'])})")
    print()
    print(e["body"])


def cmd_kb_search(args: argparse.Namespace) -> None:
    client = OpcClient.from_env()
    org_slug = resolve_org_slug(
        args_org=args.org, available=_fetch_available_orgs(client),
    )
    r = client.get(
        f"/api/v1/orgs/{org_slug}/kb/search",
        params={"q": args.query, "limit": args.limit},
    )
    if not _ok(r):
        return
    hits = r.json()["hits"]
    if args.json:
        import json as _json
        print(_json.dumps(hits, indent=2))
        return
    if not hits:
        print("no matches")
        return
    for h in hits:
        print(f"{h['slug']:40s}  {h['title']}")
        print(f"    {h['snippet']}")


def cmd_kb_add(args: argparse.Namespace) -> None:
    if not args.org:
        print("error: --org <slug> is required for agent callbacks", file=sys.stderr)
        sys.exit(1)
    client = OpcClient.from_env()
    org_slug = args.org
    try:
        body = _read_markdown_payload(args.from_file)
    except (OSError, ValueError) as exc:
        print(f"Error reading {args.from_file}: {exc}")
        sys.exit(1)
    body["agent"] = args.agent
    body["force_new_sibling"] = args.force_new_sibling
    r = client.post(f"/api/v1/orgs/{org_slug}/kb", json=body)
    if not _ok(r):
        return
    print(f"ok: added {r.json()['slug']}")


def cmd_kb_update(args: argparse.Namespace) -> None:
    if not args.org:
        print("error: --org <slug> is required for agent callbacks", file=sys.stderr)
        sys.exit(1)
    client = OpcClient.from_env()
    org_slug = args.org
    try:
        body = _read_markdown_payload(args.from_file)
    except (OSError, ValueError) as exc:
        print(f"Error reading {args.from_file}: {exc}")
        sys.exit(1)
    if body["slug"] != args.slug:
        print(f"Error: slug in file ({body['slug']!r}) does not match CLI arg ({args.slug!r})")
        sys.exit(1)
    body["agent"] = args.agent
    r = client.post(f"/api/v1/orgs/{org_slug}/kb/{args.slug}", json=body)
    if not _ok(r):
        return
    print(f"ok: updated {r.json()['slug']}")


def cmd_kb_delete(args: argparse.Namespace) -> None:
    if not args.org:
        print("error: --org <slug> is required for agent callbacks", file=sys.stderr)
        sys.exit(1)
    client = OpcClient.from_env()
    org_slug = args.org
    # OpcClient only wraps `get`/`post`; hit `._client` directly for DELETE
    # rather than expanding the client API for a single caller. If a second
    # DELETE ships later, promote this into a proper `client.delete(...)`.
    r = client._client.delete(
        f"/api/v1/orgs/{org_slug}/kb/{args.slug}",
        params={"agent": args.agent, "confirm": args.confirm, "as_founder": args.as_founder},
    )
    if not _ok(r):
        return
    print(f"ok: deleted {args.slug}")


def cmd_kb_reindex(args: argparse.Namespace) -> None:
    client = OpcClient.from_env()
    org_slug = resolve_org_slug(
        args_org=args.org, available=_fetch_available_orgs(client),
    )
    r = client.post(f"/api/v1/orgs/{org_slug}/kb/reindex")
    if not _ok(r):
        return
    print("ok: reindexed")


def cmd_kb_precedent(args: argparse.Namespace) -> None:
    if not args.org:
        print("error: --org <slug> is required for agent callbacks", file=sys.stderr)
        sys.exit(1)
    client = OpcClient.from_env()
    org_slug = args.org
    body = {
        "task_id": args.task_id,
        "decision": args.decision,
        "rationale": args.rationale,
        "as_founder": args.as_founder,
    }
    if args.slug:
        body["slug"] = args.slug
    r = client.post(f"/api/v1/orgs/{org_slug}/kb/precedent", json=body)
    if not _ok(r):
        return
    print(f"ok: wrote precedent {r.json()['slug']}")


def cmd_talk_start(args: argparse.Namespace) -> None:
    client = OpcClient.from_env()
    slug = resolve_org_slug(
        args_org=args.org, available=_fetch_available_orgs(client),
    )
    r = client.post(f"/api/v1/orgs/{slug}/talks", json={"agent_name": args.agent})
    if r.status_code == 409:
        detail = r.json().get("detail", {})
        if detail.get("code") == "talk_already_open":
            print(
                f"An open talk with {args.agent} already exists: "
                f"{detail['prior_open_talk_id']} (started {_fmt_ts(detail.get('prior_started_at'))}). "
                f"Use `opc talk resume --talk-id {detail['prior_open_talk_id']}` "
                f"or `opc talk abandon --talk-id {detail['prior_open_talk_id']} --reason orphan`."
            )
            sys.exit(1)
    if not _ok(r):
        return
    body = r.json()
    print(f"{body['talk_id']} (started {_fmt_ts(body['started_at'])})")


def cmd_talk_resume(args: argparse.Namespace) -> None:
    client = OpcClient.from_env()
    slug = resolve_org_slug(
        args_org=args.org, available=_fetch_available_orgs(client),
    )
    r = client.post(f"/api/v1/orgs/{slug}/talks/{args.talk_id}/resume")
    if not _ok(r):
        return
    print(f"ok: resumed {args.talk_id}")


def cmd_talk_abandon(args: argparse.Namespace) -> None:
    client = OpcClient.from_env()
    slug = resolve_org_slug(
        args_org=args.org, available=_fetch_available_orgs(client),
    )
    r = client.post(
        f"/api/v1/orgs/{slug}/talks/{args.talk_id}/abandon",
        json={"reason": args.reason or "manual"},
    )
    if not _ok(r):
        return
    print(f"ok: abandoned {args.talk_id}")


def cmd_talk_end(args: argparse.Namespace) -> None:
    import json as _json
    client = OpcClient.from_env()
    slug = resolve_org_slug(
        args_org=args.org, available=_fetch_available_orgs(client),
    )
    try:
        body = _json.loads(Path(args.from_file).read_text())
    except (OSError, ValueError) as exc:
        print(f"Error reading {args.from_file}: {exc}")
        sys.exit(1)
    r = client.post(f"/api/v1/orgs/{slug}/talks/{args.talk_id}/end", json=body)
    if not _ok(r):
        return
    resp = r.json()
    print(
        f"ok: closed {resp['talk_id']} — {resp['new_learnings_count']} learnings, "
        f"transcript at {resp['transcript_path']}"
    )


def cmd_talk_status(args: argparse.Namespace) -> None:
    client = OpcClient.from_env()
    slug = resolve_org_slug(
        args_org=args.org, available=_fetch_available_orgs(client),
    )
    params = {"status": "open"}
    if args.agent:
        params["agent"] = args.agent
    r = client.get(f"/api/v1/orgs/{slug}/talks", params=params)
    if not _ok(r):
        return
    talks = r.json()["talks"]
    if not talks:
        print("no open talks")
        return
    for t in talks:
        print(f"{t['talk_id']}  agent={t['agent_name']}  started={_fmt_ts(t['started_at'])}")


def cmd_talk_list(args: argparse.Namespace) -> None:
    client = OpcClient.from_env()
    slug = resolve_org_slug(
        args_org=args.org, available=_fetch_available_orgs(client),
    )
    params = {"limit": args.limit}
    if args.agent:
        params["agent"] = args.agent
    r = client.get(f"/api/v1/orgs/{slug}/talks", params=params)
    if not _ok(r):
        return
    for t in r.json()["talks"]:
        print(
            f"{t['talk_id']:10s}  {t['status']:10s}  {t['agent_name']:20s}  "
            f"{_fmt_ts(t.get('ended_at') or t['started_at'])}  "
            f"learnings={t['new_learnings_count']}"
        )


def cmd_talk_show(args: argparse.Namespace) -> None:
    import json as _json
    client = OpcClient.from_env()
    slug = resolve_org_slug(
        args_org=args.org, available=_fetch_available_orgs(client),
    )
    r = client.get(f"/api/v1/orgs/{slug}/talks/{args.talk_id}")
    if not _ok(r):
        return
    t = r.json()
    if args.json:
        print(_json.dumps(t, indent=2))
        return
    print(f"# {t['talk_id']} — {t['agent_name']}")
    print(
        f"status={t['status']} started={_fmt_ts(t['started_at'])} ended={_fmt_ts(t.get('ended_at'))}"
    )
    print(f"topics: {t.get('topic_list')}")
    print(f"learnings: {t['new_learnings_count']}  kb_slugs: {t.get('new_kb_slugs')}")
    if t.get("summary"):
        print("\n## Summary\n")
        print(t["summary"])
    if t.get("transcript"):
        print("\n## Transcript\n")
        print(t["transcript"])


def cmd_resolve_escalation(args: argparse.Namespace) -> None:
    client = OpcClient.from_env()
    slug = resolve_org_slug(
        args_org=args.org, available=_fetch_available_orgs(client),
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
    """Founder action: cancel a task and (by default) its delegated subtree."""
    try:
        client = OpcClient.from_env()
    except (DaemonNotRunning, DaemonStateInconsistent) as exc:
        print(f"Error: {exc}")
        sys.exit(1)
    slug = resolve_org_slug(
        args_org=args.org, available=_fetch_available_orgs(client),
    )
    r = client.post(
        f"/api/v1/orgs/{slug}/tasks/{args.task_id}/cancel",
        json={"rationale": args.rationale or "", "cascade": not args.no_cascade},
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
        print("opc revisit requires an interactive terminal (no --yes bypass).")
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
        "`opc details` / `opc audit` / `opc recall`."
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
        args_org=args.org, available=_fetch_available_orgs(client),
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
    print(f"Submitted {new_id}. Attach with: opc tail {new_id}")


def cmd_migrate_to_org_runtime(args: argparse.Namespace) -> int:
    """`opc migrate-to-org-runtime <path> --slug <slug> --i-have-a-backup [--apply]`."""
    from src.orchestrator.migration import migrate_to_org_runtime
    try:
        result = migrate_to_org_runtime(
            Path(args.runtime_path).expanduser().resolve(),
            slug=args.slug,
            i_have_a_backup=args.i_have_a_backup,
            apply=args.apply,
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if result.already_migrated:
        print(f"already migrated: {result.runtime_path}")
        return 0
    if not result.applied:
        print(f"DRY-RUN — would apply {len(result.planned)} actions:")
        for step in result.planned:
            print(f"  - {step}")
        print("\nRe-run with --apply to execute.")
        return 0
    print(f"migrated runtime: {result.runtime_path}")
    print(f"slug: {result.slug}")
    print(f"approved exports ({len(result.exported_approved)}): "
          f"{', '.join(result.exported_approved) or '(none)'}")
    print(f"pending exports ({len(result.exported_pending)}): "
          f"{', '.join(result.exported_pending) or '(none)'}")
    return 0


def cmd_migrate_to_multi_org(args: argparse.Namespace) -> None:
    """`opc migrate-to-multi-org <path> --i-have-a-backup [--apply]`."""
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        print("refusing to migrate without an attached terminal", file=sys.stderr)
        sys.exit(1)
    from src.daemon.migration_multi_org import migrate_to_multi_org

    rt = Path(args.path).expanduser().resolve()
    print(f"about to migrate {rt} from schema v1 → v2")
    print("this is a hard cut. there is no rollback path.")
    if not args.apply:
        print("(dry-run; pass --apply to execute)")
    confirm = input("Continue? [y/N] ").strip().lower()
    if confirm != "y":
        print("aborted")
        sys.exit(1)

    try:
        report = migrate_to_multi_org(
            rt, apply=args.apply, i_have_a_backup=args.i_have_a_backup,
        )
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)

    if report.get("already_migrated"):
        print(f"{rt} is already at schema v2 — nothing to do")
        return

    if not args.apply:
        print("would move:")
        for src, dst in report["would_move"]:
            print(f"  {src} → {dst}")
        print("\nrun with --apply to execute")
        return

    print(f"migrated. new layout:")
    print(f"  {rt}/orgs/{report['slug']}/")
    print(f"\nnext step:")
    print(f"  uv run opc init-agent --org {report['slug']}")


# ── parser ───────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="opc",
        description="OPC — multi-agent tourism organization CLI",
    )
    sub = parser.add_subparsers(dest="command")

    # opc init
    p_init_runtime = sub.add_parser(
        "init", help="create + register a multi-org runtime container",
    )
    p_init_runtime.add_argument("path", help="Path for the new runtime container")
    p_init_runtime.set_defaults(func=cmd_init)

    # opc runtime
    p_runtime = sub.add_parser("runtime", help="show the active runtime")
    p_runtime.set_defaults(func=cmd_runtime)

    # opc use
    p_use = sub.add_parser("use", help="switch the active runtime container")
    p_use.add_argument("path", help="Path of an already-registered runtime")
    p_use.set_defaults(func=cmd_use)

    # opc orgs
    p_orgs = sub.add_parser("orgs", help="manage orgs in the active runtime")
    p_orgs.set_defaults(orgs_cmd="list", func=cmd_orgs)
    orgs_sub = p_orgs.add_subparsers(dest="orgs_cmd")
    orgs_sub.required = False

    p_orgs_list = orgs_sub.add_parser("list", help="list orgs")
    p_orgs_list.set_defaults(func=cmd_orgs)

    p_orgs_init = orgs_sub.add_parser("init", help="create a new org")
    p_orgs_init.add_argument("slug")
    p_orgs_init.add_argument(
        "--from", dest="from_path", default=None,
        help="path to an examples/orgs/<name> tree to seed from",
    )
    p_orgs_init.set_defaults(func=cmd_orgs_init)

    p_orgs_unload = orgs_sub.add_parser(
        "unload", help="drop an org's state from the daemon",
    )
    p_orgs_unload.add_argument("slug")
    p_orgs_unload.set_defaults(func=cmd_orgs_unload)

    # opc run
    p_run = sub.add_parser("run", help="Run a task")
    p_run.add_argument("--org", default=None, help="Org slug (or set OPC_ORG_SLUG; auto-inferred when only one org)")
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

    # opc details
    p_details = sub.add_parser("details", help="Show task details")
    p_details.add_argument("--org", default=None, help="Org slug (or set OPC_ORG_SLUG; auto-inferred when only one org)")
    p_details.add_argument("task_id", help="Task ID (e.g. TASK-001)")
    p_details.add_argument(
        "--full",
        action="store_true",
        help="Show full per-step output summaries (no 80-char truncation)",
    )
    p_details.set_defaults(func=cmd_details)

    # opc tail
    p_tail = sub.add_parser("tail", help="Stream events for an existing task")
    p_tail.add_argument("--org", default=None, help="Org slug (or set OPC_ORG_SLUG; auto-inferred when only one org)")
    p_tail.add_argument("task_id", help="Task ID")
    p_tail.set_defaults(func=cmd_tail)

    # opc tasks
    p_tasks = sub.add_parser("tasks", help="List recent tasks")
    p_tasks.add_argument("--org", default=None, help="Org slug (or set OPC_ORG_SLUG; auto-inferred when only one org)")
    p_tasks.add_argument("--limit", type=int, default=20, help="Max tasks to show")
    p_tasks.set_defaults(func=cmd_tasks)

    # opc agents
    p_agents = sub.add_parser("agents", help="Show agent performance tiers")
    p_agents.add_argument("--org", default=None, help="Org slug (or set OPC_ORG_SLUG; auto-inferred when only one org)")
    p_agents.add_argument("agent", nargs="?", default=None,
                          help="Optional agent name; show that agent's scorecard only")
    p_agents.add_argument("--detail", action="store_true", help="Show detailed scorecards")
    p_agents.set_defaults(func=cmd_agents)

    # opc audit
    p_audit = sub.add_parser("audit", help="Show filtered audit-log entries")
    p_audit.add_argument("--org", default=None, help="Org slug (or set OPC_ORG_SLUG; auto-inferred when only one org)")
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

    # opc init-agent
    p_init_agent = sub.add_parser("init-agent", help="Initialize agent workspaces with system prompts and repo clone")
    p_init_agent.add_argument("--org", default=None, help="Org slug (or set OPC_ORG_SLUG; auto-inferred when only one org)")
    p_init_agent.add_argument("agent", nargs="?", default=None,
                        help="Specific agent to initialize (default: all)")
    p_init_agent.set_defaults(func=cmd_init_agent)

    # opc manage-repo
    p_repo = sub.add_parser("manage-repo", help="Add, remove, or update a repo in an agent's config")
    p_repo.add_argument("--org", required=True, help="Org slug (required for agent callbacks)")
    p_repo.add_argument("action", nargs="?", default=None, choices=["add", "remove", "update"],
                         help="Action to perform")
    p_repo.add_argument("--agent", default=None, help="Agent name")
    p_repo.add_argument("--repo-name", dest="repo_name", default=None, help="Repository name")
    p_repo.add_argument("--url", default=None, help="Repository URL (required for add/update)")
    p_repo.add_argument("--from-file", dest="from_file", default=None,
                         help="Path to JSON file with action/agent/repo_name/url keys")
    p_repo.set_defaults(func=cmd_manage_repo)

    # opc manage-agent
    p_ma = sub.add_parser("manage-agent", help="Enroll, update, or terminate an agent")
    p_ma.add_argument("--org", required=True, help="Org slug (required for agent callbacks)")
    p_ma.add_argument("action", nargs="?", default=None, choices=["enroll", "update", "terminate"])
    p_ma.add_argument("--name", default=None, help="Agent name")
    p_ma.add_argument("--task-id", dest="task_id", default=None, help="Active task ID (task auth path)")
    p_ma.add_argument("--session-id", dest="session_id", default=None, help="Active team-manager session ID (task auth path)")
    p_ma.add_argument("--talk-id", dest="talk_id", default=None, help="Open team-manager talk ID (talk auth path)")
    p_ma.add_argument("--description", default=None, help="Agent description")
    p_ma.add_argument("--system-prompt", dest="system_prompt", default=None, help="System prompt")
    p_ma.add_argument("--executor", default=None, help="Agent executor (default: claude)")
    p_ma.add_argument("--repos", default=None, help="JSON dict of repos")
    p_ma.add_argument("--from-file", dest="from_file", default=None,
                       help="Path to JSON file with enrollment payload")
    p_ma.set_defaults(func=cmd_manage_agent)

    # opc dispatch
    p_dispatch = sub.add_parser("dispatch", help="Dispatch a new task from an open talk")
    p_dispatch.add_argument("--org", required=True, help="Org slug (required for agent callbacks)")
    p_dispatch.add_argument(
        "--from-file", dest="from_file", required=True,
        help="Path to JSON file with dispatch payload (talk_id, brief, optional target_agent/team)",
    )
    p_dispatch.set_defaults(func=cmd_dispatch)

    # opc enrollments
    p_enroll = sub.add_parser("enrollments", help="List agent enrollment requests")
    p_enroll.add_argument("--org", default=None, help="Org slug (or set OPC_ORG_SLUG; auto-inferred when only one org)")
    p_enroll.add_argument("--status", default=None, choices=["pending", "approved", "rejected", "terminated"])
    p_enroll.set_defaults(func=cmd_enrollments)

    # opc approve-agent
    p_approve = sub.add_parser("approve-agent", help="Approve a pending agent enrollment")
    p_approve.add_argument("--org", default=None, help="Org slug (or set OPC_ORG_SLUG; auto-inferred when only one org)")
    p_approve.add_argument("name", help="Agent name to approve")
    p_approve.set_defaults(func=cmd_approve_agent)

    # opc reject-agent
    p_reject = sub.add_parser("reject-agent", help="Reject a pending agent enrollment")
    p_reject.add_argument("--org", default=None, help="Org slug (or set OPC_ORG_SLUG; auto-inferred when only one org)")
    p_reject.add_argument("name", help="Agent name to reject")
    p_reject.set_defaults(func=cmd_reject_agent)

    # opc backfill-enrollments — founder recovery op; TTY-gated; no --yes.
    p_backfill = sub.add_parser(
        "backfill-enrollments",
        help=(
            "Import pre-existing workspaces into the enrollment registry "
            "(founder; TTY-gated)"
        ),
    )
    p_backfill.add_argument("--org", default=None, help="Org slug (or set OPC_ORG_SLUG; auto-inferred when only one org)")
    p_backfill.set_defaults(func=cmd_backfill_enrollments)

    # opc recall
    p_recall = sub.add_parser(
        "recall",
        help="Recall a task: brief, outcome, optional artifact contents",
    )
    p_recall.add_argument("--org", default=None, help="Org slug (or set OPC_ORG_SLUG; auto-inferred when only one org)")
    p_recall.add_argument("task_id", help="Task ID (e.g. TASK-001)")
    p_recall.add_argument("--tree", action="store_true",
                          help="Include the full subtree of child tasks")
    p_recall.add_argument("--fetch-artifact", dest="fetch_artifact",
                          action="store_true",
                          help="Inline artifact file contents (capped at 200KB)")
    p_recall.set_defaults(func=cmd_recall)

    p_rep = sub.add_parser("report-completion", help="Agent callback: report task completion")
    p_rep.add_argument("--org", required=True, help="Org slug (required for agent callbacks)")
    p_rep.add_argument(
        "--from-file", dest="from_file", default=None,
        help="Path to a JSON file containing the completion payload. "
             "Preferred by agents — keeps the tool call a single line so "
             "Claude Code's Bash(opc:*) allow rule matches. Keys: task_id, "
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
    p_rep.add_argument("--artifact-dir", dest="artifact_dir", default=None,
                       help="Relative path to the artifact directory under the agent workspace")
    p_rep.set_defaults(func=cmd_report_completion)

    p_learn = sub.add_parser("learning", help="Agent callback: append a learning")
    p_learn.add_argument("--org", required=True, help="Org slug (required for agent callbacks)")
    p_learn.add_argument("--task-id", required=True)
    p_learn.add_argument("--session-id", required=True)
    p_learn.add_argument("--agent", required=True)
    p_learn.add_argument("--text", required=True)
    p_learn.set_defaults(func=cmd_learning)

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

    # opc kb ...
    p_kb = sub.add_parser("kb", help="Shared knowledge base")
    kb_sub = p_kb.add_subparsers(dest="kb_command", required=True)

    p_kb_list = kb_sub.add_parser("list", help="List KB entries")
    p_kb_list.add_argument("--org", default=None, help="Org slug (or set OPC_ORG_SLUG; auto-inferred when only one org)")
    p_kb_list.add_argument("--topic")
    p_kb_list.add_argument("--type", choices=["reference", "precedent"])
    p_kb_list.set_defaults(func=cmd_kb_list)

    p_kb_get = kb_sub.add_parser("get", help="Read a KB entry")
    p_kb_get.add_argument("--org", default=None, help="Org slug (or set OPC_ORG_SLUG; auto-inferred when only one org)")
    p_kb_get.add_argument("slug")
    p_kb_get.set_defaults(func=cmd_kb_get)

    p_kb_search = kb_sub.add_parser("search", help="Search KB entries")
    p_kb_search.add_argument("--org", default=None, help="Org slug (or set OPC_ORG_SLUG; auto-inferred when only one org)")
    p_kb_search.add_argument("query")
    p_kb_search.add_argument("--limit", type=int, default=20)
    p_kb_search.add_argument("--json", action="store_true")
    p_kb_search.set_defaults(func=cmd_kb_search)

    p_kb_add = kb_sub.add_parser("add", help="Add a KB entry from a markdown file")
    p_kb_add.add_argument("--org", required=True, help="Org slug (required for agent callbacks)")
    p_kb_add.add_argument("--agent", required=True)
    p_kb_add.add_argument("--from-file", required=True)
    p_kb_add.add_argument("--force-new-sibling", action="store_true")
    p_kb_add.set_defaults(func=cmd_kb_add)

    p_kb_update = kb_sub.add_parser("update", help="Update an existing KB entry")
    p_kb_update.add_argument("--org", required=True, help="Org slug (required for agent callbacks)")
    p_kb_update.add_argument("slug")
    p_kb_update.add_argument("--agent", required=True)
    p_kb_update.add_argument("--from-file", required=True)
    p_kb_update.set_defaults(func=cmd_kb_update)

    p_kb_delete = kb_sub.add_parser("delete", help="Delete a KB entry (team manager; founder may override with --as-founder)")
    p_kb_delete.add_argument("--org", required=True, help="Org slug (required for agent callbacks)")
    p_kb_delete.add_argument("slug")
    p_kb_delete.add_argument("--agent", required=True)
    p_kb_delete.add_argument("--confirm", action="store_true")
    p_kb_delete.add_argument("--as-founder", action="store_true")
    p_kb_delete.set_defaults(func=cmd_kb_delete)

    p_kb_reindex = kb_sub.add_parser("reindex", help="Regenerate _index.md")
    p_kb_reindex.add_argument("--org", default=None, help="Org slug (or set OPC_ORG_SLUG; auto-inferred when only one org)")
    p_kb_reindex.set_defaults(func=cmd_kb_reindex)

    p_kb_prec = kb_sub.add_parser("precedent", help="Record a precedent from an escalated task")
    p_kb_prec.add_argument("--org", required=True, help="Org slug (required for agent callbacks)")
    p_kb_prec.add_argument("--task-id", required=True)
    p_kb_prec.add_argument("--decision", required=True, choices=["approve", "reject"])
    p_kb_prec.add_argument("--rationale", required=True)
    p_kb_prec.add_argument("--slug")
    p_kb_prec.add_argument("--as-founder", action="store_true")
    p_kb_prec.set_defaults(func=cmd_kb_precedent)

    # opc talk ...
    p_talk = sub.add_parser("talk", help="Founder↔agent conversation flow")
    talk_sub = p_talk.add_subparsers(dest="talk_command", required=True)

    p_talk_start = talk_sub.add_parser("start", help="Start a new talk")
    p_talk_start.add_argument("--org", default=None, help="Org slug (or set OPC_ORG_SLUG; auto-inferred when only one org)")
    p_talk_start.add_argument("--agent", required=True)
    p_talk_start.set_defaults(func=cmd_talk_start)

    p_talk_resume = talk_sub.add_parser("resume", help="Resume an open talk")
    p_talk_resume.add_argument("--org", default=None, help="Org slug (or set OPC_ORG_SLUG; auto-inferred when only one org)")
    p_talk_resume.add_argument("--talk-id", required=True)
    p_talk_resume.set_defaults(func=cmd_talk_resume)

    p_talk_abandon = talk_sub.add_parser("abandon", help="Abandon an open talk")
    p_talk_abandon.add_argument("--org", default=None, help="Org slug (or set OPC_ORG_SLUG; auto-inferred when only one org)")
    p_talk_abandon.add_argument("--talk-id", required=True)
    p_talk_abandon.add_argument("--reason", default="manual")
    p_talk_abandon.set_defaults(func=cmd_talk_abandon)

    p_talk_end = talk_sub.add_parser("end", help="End a talk (agent callback)")
    p_talk_end.add_argument("--org", default=None, help="Org slug (or set OPC_ORG_SLUG; auto-inferred when only one org)")
    p_talk_end.add_argument("--talk-id", required=True)
    p_talk_end.add_argument("--from-file", required=True)
    p_talk_end.set_defaults(func=cmd_talk_end)

    p_talk_status = talk_sub.add_parser("status", help="List open talks")
    p_talk_status.add_argument("--org", default=None, help="Org slug (or set OPC_ORG_SLUG; auto-inferred when only one org)")
    p_talk_status.add_argument("--agent")
    p_talk_status.set_defaults(func=cmd_talk_status)

    p_talk_list = talk_sub.add_parser("list", help="List recent talks")
    p_talk_list.add_argument("--org", default=None, help="Org slug (or set OPC_ORG_SLUG; auto-inferred when only one org)")
    p_talk_list.add_argument("--agent")
    p_talk_list.add_argument("--limit", type=int, default=20)
    p_talk_list.set_defaults(func=cmd_talk_list)

    p_talk_show = talk_sub.add_parser("show", help="Show a talk's metadata + transcript")
    p_talk_show.add_argument("--org", default=None, help="Org slug (or set OPC_ORG_SLUG; auto-inferred when only one org)")
    p_talk_show.add_argument("talk_id")
    p_talk_show.add_argument("--json", action="store_true", help="Emit raw JSON instead of human output")
    p_talk_show.set_defaults(func=cmd_talk_show)

    # opc resolve-escalation
    p_resolve = sub.add_parser("resolve-escalation", help="Resolve an escalated task (founder only)")
    p_resolve.add_argument("--org", default=None, help="Org slug (or set OPC_ORG_SLUG; auto-inferred when only one org)")
    p_resolve.add_argument("--task-id", required=True)
    p_resolve.add_argument("--decision", required=True, choices=["approve", "reject"])
    p_resolve.add_argument("--rationale", required=True)
    p_resolve.set_defaults(func=cmd_resolve_escalation)

    # opc cancel — founder-initiated; cascades by default.
    p_cancel = sub.add_parser(
        "cancel",
        help="Cancel a task (founder): SIGTERMs live subprocesses and cascades down the subtree",
    )
    p_cancel.add_argument("task_id", help="Task ID to cancel (e.g. TASK-052)")
    p_cancel.add_argument(
        "--org", default=None,
        help="Org slug (or set OPC_ORG_SLUG; auto-inferred when only one org)",
    )
    p_cancel.add_argument(
        "--rationale", default="",
        help="Optional founder note recorded on every cancelled row",
    )
    p_cancel.add_argument(
        "--no-cascade", action="store_true",
        help="Cancel only this task, not its descendants "
             "(dangerous: leaves any live children parentless)",
    )
    p_cancel.set_defaults(func=cmd_cancel)

    # opc revisit — founder-initiated; TTY-gated; no --yes flag by design.
    p_revisit = sub.add_parser(
        "revisit",
        help=(
            "Spawn a NEW root that inherits a terminal predecessor's brief "
            "(founder; TTY-gated)"
        ),
    )
    p_revisit.add_argument("--org", default=None, help="Org slug (or set OPC_ORG_SLUG; auto-inferred when only one org)")
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

    # opc migrate-to-org-runtime — one-shot migration for pre-org-cut runtimes.
    mig = sub.add_parser(
        "migrate-to-org-runtime",
        help="One-shot: migrate <runtime>/teams.yaml + agent_enrollments → <runtime>/org/.",
    )
    mig.add_argument("runtime_path")
    mig.add_argument("--slug", required=True)
    mig.add_argument("--i-have-a-backup", action="store_true",
                     help="Mandatory acknowledgment that the runtime is backed up.")
    mig.add_argument("--apply", action="store_true",
                     help="Execute the migration. Without this, the command is a dry run.")
    mig.set_defaults(func=cmd_migrate_to_org_runtime)

    # opc migrate-to-multi-org — convert v1 single-org runtime → v2 multi-org container.
    mig2 = sub.add_parser(
        "migrate-to-multi-org",
        help="convert a v1 single-org runtime into a v2 multi-org container",
    )
    mig2.add_argument("path")
    mig2.add_argument(
        "--i-have-a-backup",
        action="store_true",
        help="acknowledgment that you have backed up the runtime folder",
    )
    mig2.add_argument(
        "--apply", action="store_true",
        help="actually execute (default: dry-run)",
    )
    mig2.set_defaults(func=cmd_migrate_to_multi_org)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)
    args.func(args)


if __name__ == "__main__":
    main()
