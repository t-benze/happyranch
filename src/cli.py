"""OPC — unified CLI for the multi-agent tourism organization."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from src.client.client import DaemonNotRunning, DaemonStateInconsistent, OpcClient
from src.models import AgentName


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


def cmd_report_completion(args: argparse.Namespace) -> None:
    """Agent callback: report task completion to the daemon."""
    try:
        client = OpcClient.from_env()
    except (DaemonNotRunning, DaemonStateInconsistent) as exc:
        print(f"Error: {exc}")
        sys.exit(1)
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
    r = client.post(f"/api/v1/tasks/{args.task_id}/completion", json=body)
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

    # opc init-agent
    p_init_agent = sub.add_parser("init-agent", help="Initialize agent workspaces with system prompts and repo clone")
    p_init_agent.add_argument("agent", nargs="?", default=None, choices=[a.value for a in AgentName],
                        help="Specific agent to initialize (default: all)")
    p_init_agent.set_defaults(func=cmd_init_agent)

    p_rep = sub.add_parser("report-completion", help="Agent callback: report task completion")
    p_rep.add_argument("--task-id", required=True)
    p_rep.add_argument("--session-id", required=True)
    p_rep.add_argument("--agent", required=True)
    p_rep.add_argument("--status", required=True, choices=["completed", "blocked"])
    p_rep.add_argument("--confidence", type=int, default=80)
    p_rep.add_argument("--summary", required=True)
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
