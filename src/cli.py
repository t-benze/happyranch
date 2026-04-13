"""OPC — unified CLI for the multi-agent tourism organization."""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from src.config import settings
from src.infrastructure.database import Database
from src.models import AgentName, TaskType
from src.orchestrator.orchestrator import Orchestrator
from src.orchestrator.performance_tracker import PerformanceTracker


def _get_db(args: argparse.Namespace) -> Database:
    db_path = Path(args.db) if args.db else settings.get_db_path()
    return Database(db_path)


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


# ── subcommands ──────────────────────────────────────────────


def cmd_run(args: argparse.Namespace) -> None:
    """Run a task through the Engineering Head-driven orchestration loop."""
    _setup_logging(args.verbose)
    db = _get_db(args)
    orchestrator = Orchestrator(db=db, settings=settings)

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


def cmd_status(args: argparse.Namespace) -> None:
    """Show status of a specific task."""
    db = _get_db(args)
    task = db.get_task(args.task_id)
    if task is None:
        print(f"Task {args.task_id} not found.")
        sys.exit(1)

    print(f"Task:       {task.id}")
    print(f"Type:       {task.type.value}")
    print(f"Status:     {task.status.value}")
    print(f"Agent:      {task.assigned_agent or '-'}")
    print(f"Brief:      {task.brief}")
    print(f"Created:    {task.created_at}")
    print(f"Updated:    {task.updated_at}")

    results = db.get_task_results(args.task_id)
    if results:
        print(f"\nResults ({len(results)}):")
        for r in results:
            print(f"  - [{r['agent']}] confidence={r['confidence_score']}  {r['output_summary'][:80]}")

    logs = db.get_audit_logs(args.task_id)
    if logs:
        print(f"\nAudit log ({len(logs)} entries):")
        for log in logs:
            print(f"  {log['timestamp'][:19]}  {log['agent']:20s}  {log['action']}")

    db.close()


def cmd_tasks(args: argparse.Namespace) -> None:
    """List recent tasks."""
    db = _get_db(args)
    tasks = db.list_tasks(limit=args.limit)
    if not tasks:
        print("No tasks found.")
        db.close()
        return

    print(f"{'ID':<12} {'Type':<20} {'Status':<12}  Brief")
    print("-" * 76)
    for t in tasks:
        brief = t.brief[:40] + "..." if len(t.brief) > 40 else t.brief
        print(f"{t.id:<12} {t.type.value:<20} {t.status.value:<12}  {brief}")
    db.close()


def cmd_agents(args: argparse.Namespace) -> None:
    """Show agent performance tiers."""
    db = _get_db(args)
    tracker = PerformanceTracker(db, settings)
    tiers = tracker.get_all_tiers()

    print(f"{'Agent':<22} {'Tier':<8}")
    print("-" * 30)
    for agent in AgentName:
        tier = tiers.get(agent, "green")
        print(f"{agent.value:<22} {tier.value:<8}")

    # Show detailed scorecards if --detail
    if args.detail:
        print()
        for agent in AgentName:
            sc = db.get_scorecard(agent.value)
            if sc:
                print(f"{agent.value}:")
                print(f"  Acceptance: {sc['acceptance_rate']:.0%}  Revision: {sc['revision_rate']:.0%}  Errors: {sc['error_count']}")
                print(f"  Period: {sc['period_start'][:10]} to {sc['period_end'][:10]}")
    db.close()


def _resolve_repos() -> dict[str, str]:
    """Get repos from settings, falling back to auto-detect from current git remote."""
    if settings.repos:
        return settings.repos
    # Auto-detect: use the current repo's remote as a single-repo default
    import subprocess
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            url = result.stdout.strip()
            # Derive name from URL: https://github.com/user/my-opc.git → my-opc
            name = url.rstrip("/").rsplit("/", 1)[-1].removesuffix(".git")
            return {name: url}
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return {}


def cmd_init_agent(args: argparse.Namespace) -> None:
    """Initialize agent workspaces with real system prompts, repo clones, and agent-specific dirs."""
    from src.orchestrator.context_builder import ContextBuilder
    from src.orchestrator.prompt_loader import load_all_prompts

    _setup_logging(args.verbose)
    db = _get_db(args)

    protocol_dir = settings.get_protocol_dir()
    if not protocol_dir.exists():
        print(f"Error: protocol directory not found at {protocol_dir}")
        print("Expected: 02-system-prompts-managers.md, 03-system-prompts-workers.md")
        sys.exit(1)

    prompts = load_all_prompts(protocol_dir)
    ctx = ContextBuilder(settings)
    repos = _resolve_repos()

    agents_to_init = [AgentName(args.agent)] if args.agent else list(AgentName)

    for agent in agents_to_init:
        name = agent.value
        workspace = settings.get_workspaces_dir() / name
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
            print(f"  [{name}] repos skipped (no OPC_REPOS set and no git remote detected)")

        # 2. Create workspace + persistent files + CLAUDE.md + settings.json
        #    (runs after clone so CLAUDE.md can list available repos)
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
    parser.add_argument("--db", default=None, help="Path to SQLite database")
    sub = parser.add_subparsers(dest="command")

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
    p_init = sub.add_parser("init-agent", help="Initialize agent workspaces with system prompts and repo clone")
    p_init.add_argument("agent", nargs="?", default=None, choices=[a.value for a in AgentName],
                        help="Specific agent to initialize (default: all)")
    p_init.add_argument("--verbose", action="store_true", help="Debug logging")
    p_init.set_defaults(func=cmd_init_agent)

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
