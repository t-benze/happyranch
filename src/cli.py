"""OPC — unified CLI for the multi-agent tourism organization."""
from __future__ import annotations

import argparse
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


# ── subcommands ──────────────────────────────────────────────


def cmd_init(args: argparse.Namespace) -> None:
    """Register a runtime directory with the daemon."""
    try:
        client = OpcClient.from_env()
    except (DaemonNotRunning, DaemonStateInconsistent) as exc:
        print(f"Error: {exc}")
        sys.exit(1)
    r = client.post("/api/v1/runtimes/register", json={"path": str(Path(args.path).expanduser())})
    if r.status_code != 200:
        print(f"Error ({r.status_code}): {r.text}")
        sys.exit(1)
    body = r.json()
    print(f"Active runtime: {body['active']}")


def cmd_use(args: argparse.Namespace) -> None:
    """Switch the daemon's active runtime."""
    try:
        client = OpcClient.from_env()
    except (DaemonNotRunning, DaemonStateInconsistent) as exc:
        print(f"Error: {exc}")
        sys.exit(1)
    r = client.post("/api/v1/runtimes/activate", json={"path": str(Path(args.path).expanduser())})
    if r.status_code == 409:
        detail = r.json().get("detail", {})
        print(f"Cannot switch runtime: tasks in flight ({detail.get('task_ids')})")
        sys.exit(1)
    if r.status_code != 200:
        print(f"Error ({r.status_code}): {r.text}")
        sys.exit(1)
    body = r.json()
    print(f"Active runtime: {body['active']}")


def cmd_run(args: argparse.Namespace) -> None:
    """Submit a task and stream its events until terminal."""
    try:
        client = OpcClient.from_env()
    except (DaemonNotRunning, DaemonStateInconsistent) as exc:
        print(f"Error: {exc}")
        sys.exit(1)

    r = client.post("/api/v1/tasks", json={"type": args.task, "brief": args.brief})
    if not _ok(r):
        return
    task_id = r.json()["task_id"]
    print(f"Submitted {task_id}; streaming events (Ctrl-C to detach)...")
    _stream_task_events(client, task_id)


def cmd_tail(args: argparse.Namespace) -> None:
    """Reattach to a running task and stream its events until terminal."""
    try:
        client = OpcClient.from_env()
    except (DaemonNotRunning, DaemonStateInconsistent) as exc:
        print(f"Error: {exc}")
        sys.exit(1)
    _stream_task_events(client, args.task_id)


def _stream_task_events(client: OpcClient, task_id: str) -> None:
    import json as _json

    import httpx

    try:
        for payload in client.stream("GET", f"/api/v1/tasks/{task_id}/events"):
            try:
                event = _json.loads(payload)
            except _json.JSONDecodeError:
                print(payload)
                continue
            etype = event.get("type", "?")
            print(f"[{etype}] {event}")
            if etype in ("task_complete", "task_escalated", "task_rejected"):
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
    r = client.get("/api/v1/tasks", params={"limit": args.limit})
    if not _ok(r):
        return
    tasks = r.json()["tasks"]
    if not tasks:
        print("No tasks found.")
        return
    print(f"{'ID':<12} {'Type':<20} {'Status':<12}  Brief")
    print("-" * 76)
    for t in tasks:
        brief = t["brief"][:40] + "..." if len(t["brief"]) > 40 else t["brief"]
        print(f"{t['id']:<12} {t['type']:<20} {t['status']:<12}  {brief}")


def cmd_status(args: argparse.Namespace) -> None:
    """Show status of a specific task."""
    try:
        client = OpcClient.from_env()
    except (DaemonNotRunning, DaemonStateInconsistent) as exc:
        print(f"Error: {exc}")
        sys.exit(1)
    r = client.get(f"/api/v1/tasks/{args.task_id}")
    if r.status_code == 404:
        print(f"Task {args.task_id} not found.")
        sys.exit(1)
    if not _ok(r):
        return
    body = r.json()
    task = body["task"]
    print(f"Task:       {task['id']}")
    print(f"Type:       {task['type']}")
    print(f"Status:     {task['status']}")
    print(f"Agent:      {task.get('assigned_agent') or '-'}")
    print(f"Brief:      {task['brief']}")
    print(f"Created:    {task['created_at']}")
    print(f"Updated:    {task['updated_at']}")
    if body.get("results"):
        print(f"\nResults ({len(body['results'])}):")
        for r_ in body["results"]:
            print(f"  - [{r_['agent']}] confidence={r_['confidence_score']}  {r_['output_summary'][:80]}")
    if body.get("audit_log"):
        print(f"\nAudit log ({len(body['audit_log'])} entries):")
        for log in body["audit_log"]:
            print(f"  {log['timestamp'][:19]}  {log['agent']:20s}  {log['action']}")


def cmd_agents(args: argparse.Namespace) -> None:
    """Show agent performance tiers."""
    try:
        client = OpcClient.from_env()
    except (DaemonNotRunning, DaemonStateInconsistent) as exc:
        print(f"Error: {exc}")
        sys.exit(1)
    r = client.get("/api/v1/agents")
    if not _ok(r):
        return
    body = r.json()
    print(f"{'Agent':<22} {'Tier':<8}")
    print("-" * 30)
    for entry in body["agents"]:
        print(f"{entry['name']:<22} {entry['tier']:<8}")
    if args.detail:
        print()
        for entry in body["agents"]:
            sc = entry.get("scorecard")
            if sc:
                print(f"{entry['name']}:")
                print(f"  Acceptance: {sc['acceptance_rate']:.0%}  Revision: {sc['revision_rate']:.0%}  Errors: {sc['error_count']}")
                print(f"  Period: {sc['period_start'][:10]} to {sc['period_end'][:10]}")


def cmd_init_agent(args: argparse.Namespace) -> None:
    """Initialize agent workspaces by streaming progress from the daemon."""
    import json as _json

    import httpx

    try:
        client = OpcClient.from_env()
    except (DaemonNotRunning, DaemonStateInconsistent) as exc:
        print(f"Error: {exc}")
        sys.exit(1)
    try:
        for payload in client.stream(
            "POST", "/api/v1/agents/init", json={"agent": args.agent},
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

    r = client.get("/api/v1/audit", params=params)
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
        ts = (e.get("timestamp") or "")[:19]
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
    return data["task_id"], body


def cmd_report_completion(args: argparse.Namespace) -> None:
    """Agent callback: report task completion to the daemon."""
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
    r = client.post(f"/api/v1/tasks/{task_id}/completion", json=body)
    if not _ok(r):
        return


def cmd_learning(args: argparse.Namespace) -> None:
    """Agent callback: append a learning to the agent's learnings.md."""
    try:
        client = OpcClient.from_env()
    except (DaemonNotRunning, DaemonStateInconsistent) as exc:
        print(f"Error: {exc}")
        sys.exit(1)
    r = client.post(
        f"/api/v1/agents/{args.agent}/learnings",
        json={"session_id": args.session_id, "task_id": args.task_id, "text": args.text},
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

    r = client.post(f"/api/v1/agents/{agent}/repos", json=body)
    if not _ok(r):
        return
    print(f"ok: {args.action or body['action']} {body['repo_name']}")


def _manage_agent_payload_from_file(path: str) -> dict:
    """Load a manage-agent payload from a JSON file."""
    import json as _json
    with open(path) as f:
        data = _json.load(f)
    required = ["action", "name", "task_id", "session_id"]
    missing = [k for k in required if not data.get(k)]
    if missing:
        raise ValueError(f"manage-agent file missing keys: {missing}")
    return data


def cmd_manage_agent(args: argparse.Namespace) -> None:
    """Agent callback: enroll, update, or terminate an agent."""
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
            "task_id": args.task_id,
            "session_id": args.session_id,
        }
        if args.description:
            body["description"] = args.description
        if args.system_prompt:
            body["system_prompt"] = args.system_prompt
        if args.repos:
            body["repos"] = _json.loads(args.repos)

    r = client.post("/api/v1/agents/manage", json=body)
    if not _ok(r):
        return
    result = r.json()
    status = result.get("status", "ok")
    print(f"ok: {body['action']} {body['name']} (status: {status})")


def cmd_enrollments(args: argparse.Namespace) -> None:
    """List agent enrollment requests."""
    try:
        client = OpcClient.from_env()
    except (DaemonNotRunning, DaemonStateInconsistent) as exc:
        print(f"Error: {exc}")
        sys.exit(1)
    params = {}
    if args.status:
        params["status"] = args.status
    r = client.get("/api/v1/agents/enrollments", params=params)
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
        print(f"{e['name']:<22} {e['status']:<12} {desc:<40} {e['created_at'][:19]}")


def cmd_approve_agent(args: argparse.Namespace) -> None:
    """Founder action: approve a pending agent enrollment."""
    try:
        client = OpcClient.from_env()
    except (DaemonNotRunning, DaemonStateInconsistent) as exc:
        print(f"Error: {exc}")
        sys.exit(1)
    r = client.post(f"/api/v1/agents/{args.name}/approve", json={})
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
    r = client.post(f"/api/v1/agents/{args.name}/reject", json={})
    if not _ok(r):
        return
    print(f"Rejected: {args.name}")


# ── parser ───────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="opc",
        description="OPC — multi-agent tourism organization CLI",
    )
    sub = parser.add_subparsers(dest="command")

    # opc init
    p_init_runtime = sub.add_parser("init", help="Initialize a new OPC runtime directory")
    p_init_runtime.add_argument("path", help="Path for the new runtime directory")
    p_init_runtime.set_defaults(func=cmd_init)

    # opc use
    p_use = sub.add_parser("use", help="Switch the daemon's active runtime")
    p_use.add_argument("path", help="Path of an already-registered runtime")
    p_use.set_defaults(func=cmd_use)

    # opc run
    p_run = sub.add_parser("run", help="Run a task")
    p_run.add_argument(
        "--task", default="general",
        choices=["general", "implement_feature", "bug_fix", "payment_change"],
        help="Task type hint (default: general -- EH decides the approach)",
    )
    p_run.add_argument("--brief", required=True, help="Task description")
    p_run.set_defaults(func=cmd_run)

    # opc status
    p_status = sub.add_parser("status", help="Show task status")
    p_status.add_argument("task_id", help="Task ID (e.g. TASK-001)")
    p_status.set_defaults(func=cmd_status)

    # opc tail
    p_tail = sub.add_parser("tail", help="Stream events for an existing task")
    p_tail.add_argument("task_id", help="Task ID")
    p_tail.set_defaults(func=cmd_tail)

    # opc tasks
    p_tasks = sub.add_parser("tasks", help="List recent tasks")
    p_tasks.add_argument("--limit", type=int, default=20, help="Max tasks to show")
    p_tasks.set_defaults(func=cmd_tasks)

    # opc agents
    p_agents = sub.add_parser("agents", help="Show agent performance tiers")
    p_agents.add_argument("--detail", action="store_true", help="Show detailed scorecards")
    p_agents.set_defaults(func=cmd_agents)

    # opc audit
    p_audit = sub.add_parser("audit", help="Show filtered audit-log entries")
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
    p_init_agent.add_argument("agent", nargs="?", default=None,
                        help="Specific agent to initialize (default: all)")
    p_init_agent.set_defaults(func=cmd_init_agent)

    # opc manage-repo
    p_repo = sub.add_parser("manage-repo", help="Add, remove, or update a repo in an agent's config")
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
    p_ma.add_argument("action", nargs="?", default=None, choices=["enroll", "update", "terminate"])
    p_ma.add_argument("--name", default=None, help="Agent name")
    p_ma.add_argument("--task-id", dest="task_id", default=None, help="Active task ID")
    p_ma.add_argument("--session-id", dest="session_id", default=None, help="Active EH session ID")
    p_ma.add_argument("--description", default=None, help="Agent description")
    p_ma.add_argument("--system-prompt", dest="system_prompt", default=None, help="System prompt")
    p_ma.add_argument("--repos", default=None, help="JSON dict of repos")
    p_ma.add_argument("--from-file", dest="from_file", default=None,
                       help="Path to JSON file with enrollment payload")
    p_ma.set_defaults(func=cmd_manage_agent)

    # opc enrollments
    p_enroll = sub.add_parser("enrollments", help="List agent enrollment requests")
    p_enroll.add_argument("--status", default=None, choices=["pending", "approved", "rejected", "terminated"])
    p_enroll.set_defaults(func=cmd_enrollments)

    # opc approve-agent
    p_approve = sub.add_parser("approve-agent", help="Approve a pending agent enrollment")
    p_approve.add_argument("name", help="Agent name to approve")
    p_approve.set_defaults(func=cmd_approve_agent)

    # opc reject-agent
    p_reject = sub.add_parser("reject-agent", help="Reject a pending agent enrollment")
    p_reject.add_argument("name", help="Agent name to reject")
    p_reject.set_defaults(func=cmd_reject_agent)

    p_rep = sub.add_parser("report-completion", help="Agent callback: report task completion")
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
    p_rep.set_defaults(func=cmd_report_completion)

    p_learn = sub.add_parser("learning", help="Agent callback: append a learning")
    p_learn.add_argument("--task-id", required=True)
    p_learn.add_argument("--session-id", required=True)
    p_learn.add_argument("--agent", required=True)
    p_learn.add_argument("--text", required=True)
    p_learn.set_defaults(func=cmd_learning)

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
