"""Agent administration: init-agent, manage-repo/agent, enrollment approvals."""
from __future__ import annotations

import argparse
import sys

from cli import _shared
from cli._shared import _fmt_ts, _ok, resolve_org_slug
from cli.client.client import DaemonNotRunning, DaemonStateInconsistent, OpcClient


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
        args_org=args.org, available=_shared._fetch_available_orgs(client),
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
            # Executor drift: org .md frontmatter disagrees with the workspace
            # agent.yaml. init does NOT auto-reconcile — surface the values and
            # the exact command to fix it.
            if phase == "executor_drift":
                print(
                    f"  [{agent}] WARNING executor drift: "
                    f"org={event.get('org_executor')} "
                    f"workspace={event.get('workspace_executor')}"
                )
                hint = event.get("hint")
                if hint:
                    print(f"           {hint}")
                continue
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



def _manage_repo_payload_from_file(path: str) -> tuple[str, dict]:
    """Load a manage-repo payload from a JSON file.

    Same pattern as report-completion: single-line `happyranch` invocation avoids
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

    The daemon (see ManageAgentBody in src/daemon/routes/agents.py) accepts
    (task_id + session_id) auth. This client-side check fast-fails obvious
    shape errors before the HTTP round trip.
    """
    import json as _json
    with open(path) as f:
        data = _json.load(f)
    missing_base = [k for k in ("action", "name") if not data.get(k)]
    if missing_base:
        raise ValueError(f"manage-agent file missing keys: {missing_base}")
    has_task = bool(data.get("task_id")) and bool(data.get("session_id"))
    has_partial_task = bool(data.get("task_id")) != bool(data.get("session_id"))
    if has_partial_task:
        raise ValueError("manage-agent file must supply task_id and session_id together")
    if not has_task:
        raise ValueError("manage-agent file must supply task_id and session_id")
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



def cmd_enrollments(args: argparse.Namespace) -> None:
    """List agent enrollment requests."""
    try:
        client = OpcClient.from_env()
    except (DaemonNotRunning, DaemonStateInconsistent) as exc:
        print(f"Error: {exc}")
        sys.exit(1)
    slug = resolve_org_slug(
        args_org=args.org, available=_shared._fetch_available_orgs(client),
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
        args_org=args.org, available=_shared._fetch_available_orgs(client),
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
        args_org=args.org, available=_shared._fetch_available_orgs(client),
    )
    r = client.post(f"/api/v1/orgs/{slug}/agents/{args.name}/reject", json={})
    if not _ok(r):
        return
    print(f"Rejected: {args.name}")



def cmd_set_model(args: argparse.Namespace) -> None:
    """Founder action: set or clear an existing agent's model.

    Reconciles the org .md frontmatter and the workspace agent.yaml in one
    call, then prints before/after state. Omit --model to clear (revert to
    CLI default).
    """
    try:
        client = OpcClient.from_env()
    except (DaemonNotRunning, DaemonStateInconsistent) as exc:
        print(f"Error: {exc}")
        sys.exit(1)
    slug = resolve_org_slug(
        args_org=args.org, available=_shared._fetch_available_orgs(client),
    )
    model = args.model if args.model else None
    r = client.request(
        "PUT",
        f"/api/v1/orgs/{slug}/agents/{args.agent}/model",
        json={"model": model},
    )
    if not _ok(r):
        return
    result = r.json()
    before = result["before"]
    after = result["after"]
    print(f"Model change for {result['agent']}:")
    print(f"  before: {before}")
    print(f"  after:  {after}")


def cmd_set_executor(args: argparse.Namespace) -> None:
    """Founder action: switch an existing agent's executor end-to-end.

    Reconciles the org .md frontmatter, the workspace agent.yaml, and the
    executor bootstrap in one call, then prints before/after state. Warns
    about stale Claude-only files when switching away from Claude; pass
    ``--clean`` to delete them.
    """
    try:
        client = OpcClient.from_env()
    except (DaemonNotRunning, DaemonStateInconsistent) as exc:
        print(f"Error: {exc}")
        sys.exit(1)
    slug = resolve_org_slug(
        args_org=args.org, available=_shared._fetch_available_orgs(client),
    )
    r = client.request(
        "PUT",
        f"/api/v1/orgs/{slug}/agents/{args.agent}/executor",
        json={"executor": args.executor, "clean": args.clean},
    )
    if not _ok(r):
        return
    result = r.json()
    before = result["before"]
    after = result["after"]

    def _fmt(val: object) -> str:
        return str(val) if val is not None else "(no workspace)"

    print(f"Executor switch for {result['agent']}:")
    print(f"  org frontmatter:      {before['org_executor']} -> {after['org_executor']}")
    print(
        f"  workspace agent.yaml: {_fmt(before['workspace_executor'])} -> "
        f"{_fmt(after['workspace_executor'])}"
    )
    stale = result.get("stale_files") or []
    if stale:
        if result.get("cleaned"):
            print(f"  removed stale Claude files: {', '.join(result.get('removed') or [])}")
        else:
            print("  WARNING: stale Claude-only files remain (no longer managed by the new executor):")
            for name in stale:
                print(f"    - {name}")
            print("  Re-run with --clean to delete them.")



def register(sub) -> None:
    p_init_agent = sub.add_parser("init-agent", help="Initialize agent workspaces with system prompts and repo clone")
    p_init_agent.add_argument("--org", default=None, help="Org slug (or set HAPPYRANCH_ORG_SLUG; auto-inferred when only one org)")
    p_init_agent.add_argument("agent", nargs="?", default=None,
                        help="Specific agent to initialize (default: all)")
    p_init_agent.set_defaults(func=cmd_init_agent)

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

    p_ma = sub.add_parser("manage-agent", help="Enroll, update, or terminate an agent")
    p_ma.add_argument("--org", required=True, help="Org slug (required for agent callbacks)")
    p_ma.add_argument("action", nargs="?", default=None, choices=["enroll", "update", "terminate"])
    p_ma.add_argument("--name", default=None, help="Agent name")
    p_ma.add_argument("--task-id", dest="task_id", default=None, help="Active task ID (task auth path)")
    p_ma.add_argument("--session-id", dest="session_id", default=None, help="Active team-manager session ID (task auth path)")
    p_ma.add_argument("--description", default=None, help="Agent description")
    p_ma.add_argument("--system-prompt", dest="system_prompt", default=None, help="System prompt")
    p_ma.add_argument("--executor", default=None, help="Agent executor (default: claude)")
    p_ma.add_argument("--repos", default=None, help="JSON dict of repos")
    p_ma.add_argument("--from-file", dest="from_file", default=None,
                       help="Path to JSON file with enrollment payload")
    p_ma.set_defaults(func=cmd_manage_agent)

    p_enroll = sub.add_parser("enrollments", help="List agent enrollment requests")
    p_enroll.add_argument("--org", default=None, help="Org slug (or set HAPPYRANCH_ORG_SLUG; auto-inferred when only one org)")
    p_enroll.add_argument("--status", default=None, choices=["pending", "approved", "rejected", "terminated"])
    p_enroll.set_defaults(func=cmd_enrollments)

    p_approve = sub.add_parser("approve-agent", help="Approve a pending agent enrollment")
    p_approve.add_argument("--org", default=None, help="Org slug (or set HAPPYRANCH_ORG_SLUG; auto-inferred when only one org)")
    p_approve.add_argument("name", help="Agent name to approve")
    p_approve.set_defaults(func=cmd_approve_agent)

    p_reject = sub.add_parser("reject-agent", help="Reject a pending agent enrollment")
    p_reject.add_argument("--org", default=None, help="Org slug (or set HAPPYRANCH_ORG_SLUG; auto-inferred when only one org)")
    p_reject.add_argument("name", help="Agent name to reject")
    p_reject.set_defaults(func=cmd_reject_agent)

    p_setexec = sub.add_parser(
        "set-executor",
        help="Switch an existing agent's executor (org frontmatter + workspace agent.yaml + bootstrap)",
    )
    p_setexec.add_argument("--org", default=None, help="Org slug (or set HAPPYRANCH_ORG_SLUG; auto-inferred when only one org)")
    p_setexec.add_argument("agent", help="Agent name to switch")
    p_setexec.add_argument(
        "--executor", required=True, metavar="EXECUTOR",
        help="New executor (registered profile name)",
    )
    p_setexec.add_argument(
        "--clean", action="store_true",
        help="Delete stale Claude-only files (CLAUDE.md, .claude/) when switching away from Claude",
    )
    p_setexec.set_defaults(func=cmd_set_executor)

    p_setmodel = sub.add_parser(
        "set-model",
        help="Set or clear an existing agent's model (agent.yaml + org frontmatter)",
    )
    p_setmodel.add_argument("--org", default=None, help="Org slug (or set HAPPYRANCH_ORG_SLUG; auto-inferred when only one org)")
    p_setmodel.add_argument("agent", help="Agent name")
    p_setmodel.add_argument(
        "--model", required=False, default=None, metavar="MODEL",
        help="Model id (omit to clear — revert to CLI default)",
    )
    p_setmodel.set_defaults(func=cmd_set_model)


