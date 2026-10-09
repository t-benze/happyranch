"""Agent administration: init-agent, manage-repo/agent, enrollment approvals."""
from __future__ import annotations

import argparse
import sys

from cli import _shared
from cli import identities
from cli._shared import _fmt_ts, _ok, resolve_org_slug
from cli.client.client import DaemonNotRunning, DaemonStateInconsistent, OpcClient


def cmd_init_agent(args: argparse.Namespace) -> None:
    """Initialize agent workspaces by streaming progress from the daemon."""
    import json as _json

    import httpx

    identities.validate_address(args.agent)
    try:
        client = OpcClient.from_env()
    except (DaemonNotRunning, DaemonStateInconsistent) as exc:
        print(f"Error: {exc}")
        sys.exit(1)
    slug = resolve_org_slug(
        args_org=args.org, available=_shared._fetch_available_orgs(client),
    )
    target = identities.agent_target(client, slug, args.agent, lifecycles={"active"}) if args.agent is not None else None
    names = identities.labels(client, slug)
    try:
        for payload in client.stream(
            "POST", f"/api/v1/orgs/{slug}/agents/init", json={"agent": target},
        ):
            try:
                event = _json.loads(payload)
            except _json.JSONDecodeError:
                print(payload)
                continue
            if event.get("phase") == "all_done":
                print("Done.")
                return
            agent = identities.display(names, event.get("agent", ""))
            phase = event.get("phase", "")
            # Executor drift: pre-THR-095 daemons could report that the org
            # .md frontmatter disagreed with a workspace agent.yaml. THR-095
            # retired agent.yaml for org agents, so current daemons never emit
            # this phase; the branch is kept for older-daemon compatibility.
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
        return
    # GH-709 Slice C: the daemon emits all_done only after every target
    # reported done. A stream that ends without it means a per-agent error
    # stopped the run (or the connection dropped) — never report success.
    print(
        "Error: init did not complete — one or more agents failed "
        "(no all_done received).",
        file=sys.stderr,
    )
    sys.exit(1)



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
    """Agent callback: add, remove, or update a repo in AgentDef.repos (org/agents/<name>.md frontmatter)."""
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
        if getattr(args, "expected_revision", None):
            body["expected_revision"] = args.expected_revision
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
    names = identities.labels(client, slug)
    for e in enrollments:
        desc = e["description"][:37] + "..." if len(e["description"]) > 37 else e["description"]
        print(f"{identities.display(names, e['name']):<22} {e['status']:<12} {desc:<40} {_fmt_ts(e['created_at'])}")



def cmd_approve_agent(args: argparse.Namespace) -> None:
    """Founder action: approve a pending agent enrollment."""
    identities.validate_address(args.name)
    try:
        client = OpcClient.from_env()
    except (DaemonNotRunning, DaemonStateInconsistent) as exc:
        print(f"Error: {exc}")
        sys.exit(1)
    slug = resolve_org_slug(
        args_org=args.org, available=_shared._fetch_available_orgs(client),
    )
    target = identities.agent_target(client, slug, args.name, lifecycles={"pending"})
    r = client.post(f"/api/v1/orgs/{slug}/agents/{target}/approve", json={})
    if not _ok(r):
        return
    print(f"Approved: {identities.display(identities.labels(client, slug), target)}")



def cmd_reject_agent(args: argparse.Namespace) -> None:
    """Founder action: reject a pending agent enrollment."""
    identities.validate_address(args.name)
    try:
        client = OpcClient.from_env()
    except (DaemonNotRunning, DaemonStateInconsistent) as exc:
        print(f"Error: {exc}")
        sys.exit(1)
    slug = resolve_org_slug(
        args_org=args.org, available=_shared._fetch_available_orgs(client),
    )
    target = identities.agent_target(client, slug, args.name, lifecycles={"pending"})
    r = client.post(f"/api/v1/orgs/{slug}/agents/{target}/reject", json={})
    if not _ok(r):
        return
    print(f"Rejected: {identities.display(identities.labels(client, slug), target)}")



def cmd_set_model(args: argparse.Namespace) -> None:
    """Founder action: set or clear an existing agent's model.

    THR-095: writes to org/agents/<name>.md frontmatter ONLY
    (single source of truth). Omit --model to clear (revert to CLI default).
    """
    identities.validate_address(args.agent)
    try:
        client = OpcClient.from_env()
    except (DaemonNotRunning, DaemonStateInconsistent) as exc:
        print(f"Error: {exc}")
        sys.exit(1)
    slug = resolve_org_slug(
        args_org=args.org, available=_shared._fetch_available_orgs(client),
    )
    target = identities.agent_target(client, slug, args.agent, lifecycles={"active"})
    model = args.model if args.model else None
    r = client.request(
        "PUT",
        f"/api/v1/orgs/{slug}/agents/{target}/model",
        json={"model": model},
    )
    if not _ok(r):
        return
    result = r.json()
    before = result["before"]
    after = result["after"]
    print(f"Model change for {identities.display(identities.labels(client, slug), result['agent'])}:")
    print(f"  before: {before}")
    print(f"  after:  {after}")


def cmd_set_executor(args: argparse.Namespace) -> None:
    """Founder action: switch an existing agent's executor.

    THR-095: writes to org/agents/<name>.md frontmatter ONLY
    (single source of truth).  The executor bootstrap is regenerated.
    Warns about the stale Claude executor settings file when switching
    away from Claude; ``--clean`` removes only that accepted stale settings
    file (``.claude/settings.json``) and preserves the canonical
    ``AGENTS.md``/``CLAUDE.md`` instruction pair and ``.claude/skills``.
    """
    identities.validate_address(args.agent)
    try:
        client = OpcClient.from_env()
    except (DaemonNotRunning, DaemonStateInconsistent) as exc:
        print(f"Error: {exc}")
        sys.exit(1)
    slug = resolve_org_slug(
        args_org=args.org, available=_shared._fetch_available_orgs(client),
    )
    target = identities.agent_target(client, slug, args.agent, lifecycles={"active"})
    r = client.request(
        "PUT",
        f"/api/v1/orgs/{slug}/agents/{target}/executor",
        json={"executor": args.executor, "clean": args.clean},
    )
    if not _ok(r):
        return
    result = r.json()
    before = result["before"]
    after = result["after"]

    def _fmt(val: object) -> str:
        return str(val) if val is not None else "(no workspace)"

    print(f"Executor switch for {identities.display(identities.labels(client, slug), result['agent'])}:")
    print(f"  org .md frontmatter:  {before['org_executor']} -> {after['org_executor']}")
    stale = result.get("stale_files") or []
    if stale:
        if result.get("cleaned"):
            print(f"  removed stale Claude files: {', '.join(result.get('removed') or [])}")
        else:
            print("  WARNING: stale Claude executor settings remain (no longer managed by the new executor):")
            for name in stale:
                print(f"    - {name}")
            print("  Re-run with --clean to remove only those stale settings.")



def _identity_client(args):
    client = OpcClient.from_env()
    slug = resolve_org_slug(args_org=args.org, available=_shared._fetch_available_orgs(client))
    return client, slug


def cmd_identities_list(args: argparse.Namespace) -> None:
    import json
    client, slug = _identity_client(args)
    response = client.get(f"/api/v1/orgs/{slug}/identities")
    _ok(response)
    body = response.json()
    if args.json:
        print(json.dumps(body, indent=2))
        return
    for row in body["identities"]:
        label = row.get("addressable_name")
        name = f"{label} · {row['canonical_id']}" if label else row["canonical_id"]
        print(f"{name}  {row['kind']}/{row['lifecycle']}  "
              f"name_revision={row.get('name_revision')}  naming={row['naming_status']}")


def cmd_identities_resolve(args: argparse.Namespace) -> None:
    import json
    for address in args.addresses:
        identities.validate_address(address)
    if len(args.addresses) > 128 or (args.thread_id is not None and (
            args.context != "thread_recipient" or not 1 <= len(args.thread_id) <= 128)):
        identities.fail("resolve accepts 1–128 addresses; --thread-id requires thread_recipient", code=2)
    client, slug = _identity_client(args)
    payload = {"addresses": args.addresses, "context": args.context}
    if args.thread_id:
        payload["thread_id"] = args.thread_id
    response = client.post(f"/api/v1/orgs/{slug}/identities/resolve", json=payload)
    _ok(response)
    body = response.json()
    if args.json:
        print(json.dumps(body, indent=2))
        return
    for row in body["resolutions"]:
        identity = row.get("identity") or {}
        label, canonical = identity.get("addressable_name"), identity.get("canonical_id")
        shown = f"{label} · {canonical}" if label else canonical or "-"
        print(f"{row['address']}: {row['status']}  {shown}  eligible={row['eligible']}")


def cmd_identities_rename(args: argparse.Namespace) -> None:
    import json
    from pathlib import Path
    import httpx
    identities.validate_address(args.target)
    if args.from_file:
        if args.expected_name_revision is not None:
            identities.fail("--expected-name-revision belongs with --name; file carries its own revision", code=2)
        try:
            payload = json.loads(Path(args.from_file).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            identities.fail(f"cannot read rename payload: {exc}", code=2)
    else:
        payload = {"addressable_name": args.name, "expected_name_revision": args.expected_name_revision}
    identities.validate_rename(payload)
    client, slug = _identity_client(args)
    target = identities.resolve_targets(client, slug, [args.target])[0]
    path = (f"/api/v1/orgs/{slug}/founder/addressable-name" if target == "founder" else
            f"/api/v1/orgs/{slug}/agents/{target}/addressable-name")
    ambiguous = False
    identity = None
    try:
        response = client.request("PUT", path, json=payload)
        ambiguous = response.status_code >= 500
        if 200 <= response.status_code < 300:
            try:
                identity = response.json()
                ambiguous = (not isinstance(identity, dict)
                    or identity.get("canonical_id") != target
                    or identity.get("addressable_name") != payload["addressable_name"]
                    or type(identity.get("name_revision")) is not int)
            except ValueError:
                ambiguous = True
    except httpx.RequestError:
        ambiguous = True
    if ambiguous:
        rows = identities.identity_rows(client, slug)
        current = next((row for row in rows if row.get("canonical_id") == target), None)
        print("Rename outcome uncertain; readback (no automatic retry):", file=sys.stderr)
        print(json.dumps(current, indent=2) if current else "naming readback unavailable", file=sys.stderr)
        raise SystemExit(1)
    _ok(response)
    if args.json:
        print(json.dumps(identity, indent=2))
    else:
        print(f"{identity['addressable_name']} · {identity['canonical_id']}  "
              f"name_revision={identity['name_revision']}")


def register(sub) -> None:
    p_identity = sub.add_parser("identities", help="Inspect/resolve current names and permanent IDs; operator rename")
    identity_sub = p_identity.add_subparsers(dest="identity_command", required=True)
    p_list = identity_sub.add_parser("list", help="Current Name · ID and independent name revision")
    p_list.add_argument("--org", default=None)
    p_list.add_argument("--json", action="store_true")
    p_list.set_defaults(func=cmd_identities_list)
    p_resolve = identity_sub.add_parser("resolve", help="Read-only diagnostic; grants no action authority")
    p_resolve.add_argument("--org", default=None)
    p_resolve.add_argument("addresses", nargs="+")
    p_resolve.add_argument("--context", choices=["lookup", "task_owner", "thread_recipient"], default="lookup")
    p_resolve.add_argument("--thread-id", default=None)
    p_resolve.add_argument("--json", action="store_true")
    p_resolve.set_defaults(func=cmd_identities_resolve)
    p_rename = identity_sub.add_parser("rename", help="Operator agent/founder rename using name-revision CAS")
    p_rename.add_argument("--org", default=None)
    p_rename.add_argument("target", help="Permanent ID or current name (founder is the human)")
    group = p_rename.add_mutually_exclusive_group(required=True)
    group.add_argument("--name", default=None)
    group.add_argument("--from-file", default=None)
    p_rename.add_argument("--expected-name-revision", type=int, default=None)
    p_rename.add_argument("--json", action="store_true")
    p_rename.set_defaults(func=cmd_identities_rename)

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
    p_ma.add_argument("--expected-revision", dest="expected_revision", default=None,
                      help="Required roster revision for an update; copy it from the same GET /agents row")
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
        help="Switch an existing agent's executor (org .md frontmatter + bootstrap)",
    )
    p_setexec.add_argument("--org", default=None, help="Org slug (or set HAPPYRANCH_ORG_SLUG; auto-inferred when only one org)")
    p_setexec.add_argument("agent", help="Agent name to switch")
    p_setexec.add_argument(
        "--executor", required=True, metavar="EXECUTOR",
        help="New executor (registered profile name)",
    )
    p_setexec.add_argument(
        "--clean", action="store_true",
        help=(
            "Remove only the stale Claude executor settings "
            "(.claude/settings.json) when switching away from Claude; the "
            "canonical AGENTS.md/CLAUDE.md pair and .claude/skills are preserved"
        ),
    )
    p_setexec.set_defaults(func=cmd_set_executor)

    p_setmodel = sub.add_parser(
        "set-model",
        help="Set or clear an existing agent's model (org .md frontmatter)",
    )
    p_setmodel.add_argument("--org", default=None, help="Org slug (or set HAPPYRANCH_ORG_SLUG; auto-inferred when only one org)")
    p_setmodel.add_argument("agent", help="Agent name")
    p_setmodel.add_argument(
        "--model", required=False, default=None, metavar="MODEL",
        help="Model id (omit to clear — revert to CLI default)",
    )
    p_setmodel.set_defaults(func=cmd_set_model)
