"""OPC — unified CLI for the multi-agent tourism organization."""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from src.client.client import DaemonNotRunning, DaemonStateInconsistent, OpcClient
from src.config import Settings
from src.infrastructure.database import Database
from src.models import AgentName, TaskType
from src.orchestrator.orchestrator import Orchestrator
from src.runtime import RuntimeDir


def _get_settings() -> Settings:
    return Settings()


def _require_runtime(args: argparse.Namespace) -> RuntimeDir:
    """Load RuntimeDir from --runtime flag, or detect from current directory."""
    path = Path(args.runtime) if args.runtime else Path.cwd()
    try:
        return RuntimeDir.load(path)
    except ValueError:
        if args.runtime:
            print(f"Error: {path} is not a valid OPC runtime directory (missing opc.yaml)")
        else:
            print("Error: not inside an OPC runtime directory (no opc.yaml found).")
            print("  Either run from inside a runtime directory, or pass --runtime <path>")
            print("  Create one with: opc init <path>")
        sys.exit(1)


def _get_db(runtime: RuntimeDir) -> Database:
    return Database(runtime.db_path)


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


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
    """Run a task through the Engineering Head-driven orchestration loop."""
    _setup_logging(args.verbose)
    runtime = _require_runtime(args)
    db = _get_db(runtime)
    orchestrator = Orchestrator(db=db, settings=_get_settings(), runtime=runtime)

    task_type = TaskType(args.task)
    task_id = orchestrator.create_task(task_type, args.brief)
    logging.info("Created task %s (%s): %s", task_id, args.task, args.brief)

    result = orchestrator.run_task(task_id)

    print(f"\n{'='*60}")
    print(f"Task ID:    {task_id}")
    print(f"Type:       {args.task}")
    print(f"Status:     {result}")
    print(f"{'='*60}")
    db.close()


def cmd_tasks(args: argparse.Namespace) -> None:
    """List recent tasks."""
    try:
        client = OpcClient.from_env()
    except (DaemonNotRunning, DaemonStateInconsistent) as exc:
        print(f"Error: {exc}")
        sys.exit(1)
    r = client.get("/api/v1/tasks", params={"limit": args.limit})
    if r.status_code != 200:
        print(f"Error ({r.status_code}): {r.text}")
        sys.exit(1)
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
    if r.status_code != 200:
        print(f"Error ({r.status_code}): {r.text}")
        sys.exit(1)
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
    if r.status_code != 200:
        print(f"Error ({r.status_code}): {r.text}")
        sys.exit(1)
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


def _load_agent_config(workspace: Path) -> dict:
    """Load agent.yaml from workspace, returning parsed dict (empty if missing)."""
    import yaml

    config_path = workspace / "agent.yaml"
    if not config_path.exists():
        return {}
    return yaml.safe_load(config_path.read_text()) or {}


def _write_default_agent_config(workspace: Path) -> None:
    """Write a default agent.yaml if one doesn't exist."""
    import yaml

    config_path = workspace / "agent.yaml"
    if config_path.exists():
        return
    default = {"repos": {}}
    config_path.write_text(yaml.dump(default, default_flow_style=False))


def cmd_init_agent(args: argparse.Namespace) -> None:
    """Initialize agent workspaces with real system prompts, repo clones, and agent-specific dirs."""
    from src.orchestrator.context_builder import ContextBuilder
    from src.orchestrator.prompt_loader import load_all_prompts

    _setup_logging(args.verbose)
    runtime = _require_runtime(args)
    db = _get_db(runtime)

    s = _get_settings()
    protocol_dir = s.get_protocol_dir()
    if not protocol_dir.exists():
        print(f"Error: protocol directory not found at {protocol_dir}")
        print("Expected: 02-system-prompts-managers.md, 03-system-prompts-workers.md")
        sys.exit(1)

    prompts = load_all_prompts(protocol_dir)
    ctx = ContextBuilder(s)

    agents_to_init = [AgentName(args.agent)] if args.agent else list(AgentName)

    for agent in agents_to_init:
        name = agent.value
        workspace = runtime.workspaces_dir / name
        workspace.mkdir(parents=True, exist_ok=True)

        # 0. Ensure agent.yaml exists
        _write_default_agent_config(workspace)
        agent_config = _load_agent_config(workspace)
        repos = agent_config.get("repos", {})

        prompt = prompts.get(name, "")
        if not prompt:
            print(f"  Warning: no system prompt found for {name}")

        # 1. Clone or pull repos
        if repos:
            results = ctx.clone_repos(workspace, repos)
            for repo_name, ok in results.items():
                status = "ready" if ok else "FAILED"
                print(f"  [{name}] repo {repo_name}: {status}")
        else:
            print(f"  [{name}] repos: none configured (edit {workspace}/agent.yaml)")

        # 2. Create workspace + persistent files + CLAUDE.md + settings.json
        ctx.initialize_workspace(workspace, name, prompt)
        print(f"  [{name}] workspace initialized")

        # 3. Create agent-specific dirs (specs/, proposals/)
        ctx.create_agent_dirs(workspace, name)

    print(f"\nDatabase: {db.db_path}")
    print("Done.")
    db.close()


# ── parser ───────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="opc",
        description="OPC — multi-agent tourism organization CLI",
    )
    parser.add_argument("--runtime", default=None, help="Path to OPC runtime directory")
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
    p_run.add_argument("--verbose", action="store_true", help="Debug logging")
    p_run.set_defaults(func=cmd_run)

    # opc status
    p_status = sub.add_parser("status", help="Show task status")
    p_status.add_argument("task_id", help="Task ID (e.g. TASK-001)")
    p_status.set_defaults(func=cmd_status)

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
    p_init_agent.add_argument("--verbose", action="store_true", help="Debug logging")
    p_init_agent.set_defaults(func=cmd_init_agent)

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
