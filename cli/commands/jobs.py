"""Jobs family (+ the deprecated `scripts` alias shim)."""
from __future__ import annotations

import argparse
import sys

from cli import _shared
from cli._shared import _ok, resolve_org_slug
from cli.client.client import DaemonNotRunning, DaemonStateInconsistent, OpcClient


def _jobs_submit_payload_from_file(path: str) -> dict:
    """Load a jobs-submit payload from a JSON file (mirrors manage-repo / dispatch pattern).

    Auth binding (same task/session shape as manage-agent / threads compose):

    - Task path: ``task_id`` + ``session_id`` from an active task session.

    The daemon enforces this again on the wire via SubmitBody's validator —
    we mirror it here so the CLI fails fast with a useful message instead of
    a generic 422 from the server.
    """
    import json as _json
    with open(path) as f:
        data = _json.load(f)
    base_required = ("title", "script", "interpreter")
    missing = [k for k in base_required if not data.get(k)]
    if missing:
        raise ValueError(f"jobs submit file missing keys: {missing}")
    has_task = bool(data.get("task_id"))
    has_session = bool(data.get("session_id"))
    if has_task != has_session:
        raise ValueError(
            "jobs submit file: task_id and session_id must be supplied together"
        )
    if not has_task:
        raise ValueError(
            "jobs submit file: supply task_id + session_id"
        )
    # Use the existing Pydantic Boolean parser, matching server coercion.
    from pydantic import TypeAdapter
    if TypeAdapter(bool).validate_python(data.get("review_required", False)):
        raise ValueError(
            "review_required_removed: obtain any required authorization in a thread "
            "before submitting; upgrade old clients and omit review_required or set "
            "it to false. This request was not submitted."
        )
    return data



def cmd_jobs_submit(args: argparse.Namespace) -> None:
    """Agent callback: submit a job for automatic admission."""
    if not args.org:
        print("error: --org <slug> is required for agent callbacks", file=sys.stderr)
        sys.exit(1)
    import json as _json
    try:
        body = _jobs_submit_payload_from_file(args.from_file)
    except (OSError, _json.JSONDecodeError, ValueError) as exc:
        print(f"Error reading scripts-submit file {args.from_file}: {exc}", file=sys.stderr)
        sys.exit(2)
    try:
        client = OpcClient.from_env()
    except (DaemonNotRunning, DaemonStateInconsistent) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    r = client.post(f"/api/v1/orgs/{args.org}/jobs/submit", json=body)
    if not _ok(r):
        return
    result = r.json()
    if getattr(args, "json", False):
        print(_json.dumps(result, sort_keys=True))
        return
    print(f"ok: submitted {result['id']} (status={result['status']}). Self-block your task referencing this ID.")



def cmd_jobs_list(args: argparse.Namespace) -> None:
    """Founder: list script requests."""
    try:
        client = OpcClient.from_env()
    except (DaemonNotRunning, DaemonStateInconsistent) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    slug = resolve_org_slug(args_org=args.org, available=_shared._fetch_available_orgs(client))
    params: dict = {"status": args.status, "limit": args.limit}
    if args.agent:
        params["agent"] = args.agent
    if args.task:
        params["task_id"] = args.task
    if getattr(args, "review_required", None) is not None:
        params["review_required"] = args.review_required
    if getattr(args, "persistent", None) is not None:
        params["persistent"] = args.persistent
    r = client.get(f"/api/v1/orgs/{slug}/jobs/", params=params)
    if not _ok(r):
        return
    rows = r.json()["jobs"]
    if not rows:
        print("(no script requests match)")
        return
    print(f"{'ID':<8} {'AGENT':<20} {'TASK':<12} {'STATUS':<10} TITLE")
    for row in rows:
        title = row["title"][:60]
        print(f"{row['id']:<8} {row['agent_name']:<20} {row['task_id']:<12} {row['status']:<10} {title}")



def cmd_jobs_show(args: argparse.Namespace) -> None:
    """Founder: show one script request."""
    try:
        client = OpcClient.from_env()
    except (DaemonNotRunning, DaemonStateInconsistent) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    slug = resolve_org_slug(args_org=args.org, available=_shared._fetch_available_orgs(client))
    if getattr(args, "json", False):
        import json as _json
        if not args.task_id or not args.session_id:
            print(
                "error: --json requires --task-id and --session-id",
                file=sys.stderr,
            )
            sys.exit(2)
        r = client.get(
            f"/api/v1/orgs/{slug}/jobs/{args.job_id}/receipt",
            params={"task_id": args.task_id, "session_id": args.session_id},
        )
        if not _ok(r):
            return
        print(_json.dumps(r.json(), sort_keys=True))
        return
    r = client.get(f"/api/v1/orgs/{slug}/jobs/{args.job_id}")
    if not _ok(r):
        return
    d = r.json()
    print(f"{d['id']}   {d['status']}   submitted {d['created_at']}")
    print(f"Agent:        {d['agent_name']}")
    # task_id is overloaded as scope_id — TASK-NNN for task-path submissions,
    scope_label = "Task"
    print(f"{scope_label}:         {d['task_id']}")
    print(f"Interpreter:  {d['interpreter']}")
    print(f"Cwd hint:     {d['cwd_hint'] or '(workspace root)'}")
    print()
    print(f"Title:        {d['title']}")
    print()
    print("Rationale:")
    for line in d["rationale"].splitlines():
        print(f"  {line}")
    print()
    print("Script:")
    for line in d["script_text"].splitlines():
        print(f"  {line}")
    if d["status"] in ("completed", "failed"):
        print()
        print(f"Exit code:    {d['exit_code']}")
        print(f"Duration:     {d['duration_ms']}ms")
        if d.get("reason") is not None:
            print(f"Reason:       {d['reason']}")
        if d["stdout_head"]:
            print("Stdout (head):")
            for line in d["stdout_head"].splitlines():
                print(f"  {line}")
        if d["stderr_head"]:
            print("Stderr (head):")
            for line in d["stderr_head"].splitlines():
                print(f"  {line}")
        print(f"Full output:  happyranch scripts output {d['id']}")
    elif d["status"] == "pending":
        print()
        print("Founder actions:")
        print(f"  happyranch scripts reject {d['id']} --reason \"...\"")
    elif d["status"] == "rejected":
        print()
        print(f"Reject reason: {d['reject_reason']}")



def cmd_jobs_reject(args: argparse.Namespace) -> None:
    """Founder: reject a pending script request."""
    try:
        client = OpcClient.from_env()
    except (DaemonNotRunning, DaemonStateInconsistent) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    slug = resolve_org_slug(args_org=args.org, available=_shared._fetch_available_orgs(client))
    reason = args.reason
    if not reason:
        print("Enter rejection reason (end with '.' on its own line):")
        lines: list[str] = []
        while True:
            try:
                line = input()
            except EOFError:
                break
            if line.strip() == ".":
                break
            lines.append(line)
        reason = "\n".join(lines).strip()
    if not reason:
        print("Error: empty reason", file=sys.stderr)
        sys.exit(2)
    r = client.post(f"/api/v1/orgs/{slug}/jobs/{args.job_id}/reject", json={"reason": reason})
    if not _ok(r):
        return
    print(f"ok: rejected {args.job_id}")



def cmd_jobs_output(args: argparse.Namespace) -> None:
    """Founder: fetch captured output of a terminal script request."""
    try:
        client = OpcClient.from_env()
    except (DaemonNotRunning, DaemonStateInconsistent) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    slug = resolve_org_slug(args_org=args.org, available=_shared._fetch_available_orgs(client))
    if getattr(args, "json", False):
        import json as _json
        if not args.task_id or not args.session_id:
            print(
                "error: --json requires --task-id and --session-id",
                file=sys.stderr,
            )
            sys.exit(2)
        r = client.get(
            f"/api/v1/orgs/{slug}/jobs/{args.job_id}/receipt",
            params={
                "task_id": args.task_id,
                "session_id": args.session_id,
                "stream": args.stream,
                "max_bytes": args.max_bytes,
            },
        )
        if not _ok(r):
            return
        print(_json.dumps(r.json(), sort_keys=True))
        return
    r = client.get(
        f"/api/v1/orgs/{slug}/jobs/{args.job_id}/output",
        params={"stream": args.stream, "max_bytes": args.max_bytes},
    )
    if not _ok(r):
        return
    body = r.json()
    if args.stream in ("stdout", "both"):
        print("--- stdout ---")
        print(body["stdout"], end="" if body["stdout"].endswith("\n") else "\n")
    if args.stream in ("stderr", "both"):
        print("--- stderr ---")
        print(body["stderr"], end="" if body["stderr"].endswith("\n") else "\n")



def cmd_jobs_run(args: argparse.Namespace) -> None:
    """Local compatibility tombstone; never looks up or calls a daemon."""
    print(
        "error: job_manual_run_removed: manual job execution is retired. "
        "This job was not run. Inspect its history; reject a legacy pending job "
        "with a reason if it should be closed.", file=sys.stderr,
    )
    sys.exit(2)


def cmd_jobs_tail(args: argparse.Namespace) -> None:
    """Print the tail of stdout/stderr for a job.

    Founder path: uses the bearer token already attached by ``OpcClient``.
    Agent path:
      - Task path: ``--task-id`` + ``--session-id``.
    """
    try:
        client = OpcClient.from_env()
    except (DaemonNotRunning, DaemonStateInconsistent) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    slug = resolve_org_slug(args_org=args.org, available=_shared._fetch_available_orgs(client))
    params: dict = {"stream": args.stream, "lines": args.lines}
    if args.task_id and args.session_id:
        params["task_id"] = args.task_id
        params["session_id"] = args.session_id
    r = client.get(f"/api/v1/orgs/{slug}/jobs/{args.job_id}/tail", params=params)
    if not _ok(r):
        return
    body = r.json()
    for line in body["lines"]:
        print(line)



def cmd_jobs_wait(args: argparse.Namespace) -> None:
    """Block until the job terminates or the timeout expires.

    Prints a one-line JSON object with ``status`` and ``timed_out``.
    Same auth shape as ``tail`` (task path).
    """
    import json as _json
    try:
        client = OpcClient.from_env()
    except (DaemonNotRunning, DaemonStateInconsistent) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    slug = resolve_org_slug(args_org=args.org, available=_shared._fetch_available_orgs(client))
    params: dict = {"timeout_seconds": args.timeout_seconds}
    if args.task_id and args.session_id:
        params["task_id"] = args.task_id
        params["session_id"] = args.session_id
    r = client.post(f"/api/v1/orgs/{slug}/jobs/{args.job_id}/wait", params=params)
    if not _ok(r):
        return
    body = r.json()
    print(_json.dumps({"status": body.get("status"), "timed_out": body.get("timed_out", False)}))



def cmd_jobs_stop(args: argparse.Namespace) -> None:
    """Stop a running job (SIGTERM via the daemon).

    Founder path: bearer token. Agent path:
      - Task path: ``--task-id`` + ``--session-id``.
    """
    try:
        client = OpcClient.from_env()
    except (DaemonNotRunning, DaemonStateInconsistent) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    slug = resolve_org_slug(args_org=args.org, available=_shared._fetch_available_orgs(client))
    params: dict = {}
    if args.task_id and args.session_id:
        params["task_id"] = args.task_id
        params["session_id"] = args.session_id
    r = client.post(f"/api/v1/orgs/{slug}/jobs/{args.job_id}/stop", params=params)
    if r.status_code in (409, 503):
        try:
            detail = r.json().get("detail", {})
        except (ValueError, AttributeError):
            detail = {}
        if isinstance(detail, dict) and detail.get("code") in {
            "not_running", "job_runner_pending", "job_runner_finalizing",
            "job_liveness_unknown", "job_owner_unavailable", "team_move_native_held",
            "job_stop_unavailable",
        }:
            print(f"Stop response HTTP{r.status_code}: {r.text}; inspect jobs show/wait before retrying.", file=sys.stderr)
            sys.exit(1)
    if not _ok(r):
        return
    if r.json().get("already_terminal"):
        print(f"Stop request unconfirmed for {args.job_id}; inspect show/wait for actual status", file=sys.stderr)
        sys.exit(1)
    print(f"ok: Stop requested for {args.job_id}; inspect show/wait for the actual result")



def _register_jobs_verbs(
    parent: argparse._SubParsersAction, *, deprecated: bool
) -> None:
    """Register every `jobs`/`scripts` verb under the given subparser.

    When ``deprecated=True``, each verb's handler is wrapped to print a
    one-line deprecation warning to stderr before delegating to the real
    `cmd_jobs_*` function. The same arg shape is registered on both
    parsers so the shim accepts an identical CLI surface.
    """

    def wrap(handler):
        if not deprecated:
            return handler

        def _wrapped(args: argparse.Namespace) -> None:
            print(
                "[deprecated] `happyranch scripts` is renamed to `happyranch jobs` "
                "— alias removed in next release.",
                file=sys.stderr,
            )
            handler(args)

        return _wrapped

    p_submit = parent.add_parser(
        "submit", help="Agent callback: Submit a job for automatic admission; authorize in a thread first"
    )
    p_submit.add_argument(
        "--from-file", dest="from_file", required=True, help="JSON payload file"
    )
    p_submit.add_argument("--org", help="Org slug (required for agent callbacks)")
    p_submit.add_argument("--json", action="store_true", help="Emit structured JSON")
    p_submit.set_defaults(func=wrap(cmd_jobs_submit))

    p_list = parent.add_parser("list", help="List jobs")
    p_list.add_argument(
        "--status", default="pending", help="comma-separated statuses, or 'all'"
    )
    p_list.add_argument("--agent")
    p_list.add_argument("--task")
    p_list.add_argument(
        "--review-required",
        dest="review_required",
        choices=("true", "false"),
        default=None,
        help="filter by review_required flag",
    )
    p_list.add_argument(
        "--persistent",
        choices=("true", "false"),
        default=None,
        help="filter by persistent flag",
    )
    p_list.add_argument("--limit", type=int, default=50)
    p_list.add_argument("--org")
    p_list.set_defaults(func=wrap(cmd_jobs_list))

    p_show = parent.add_parser("show", help="Show one job")
    p_show.add_argument("job_id")
    p_show.add_argument("--org")
    p_show.add_argument("--json", action="store_true", help="Emit an authenticated structured receipt")
    p_show.add_argument("--task-id", dest="task_id", default=None)
    p_show.add_argument("--session-id", dest="session_id", default=None)
    p_show.set_defaults(func=wrap(cmd_jobs_show))

    p_reject = parent.add_parser("reject", help="Reject a pending job")
    p_reject.add_argument("job_id")
    p_reject.add_argument("--reason", help="rejection reason (prompted if omitted)")
    p_reject.add_argument("--org")
    p_reject.set_defaults(func=wrap(cmd_jobs_reject))

    p_output = parent.add_parser(
        "output", help="Fetch captured output of a terminal job"
    )
    p_output.add_argument("job_id")
    p_output.add_argument(
        "--stream", choices=["stdout", "stderr", "both"], default="both"
    )
    p_output.add_argument("--max-bytes", type=int, default=1_048_576)
    p_output.add_argument("--org")
    p_output.add_argument("--json", action="store_true", help="Emit an authenticated structured receipt")
    p_output.add_argument("--task-id", dest="task_id", default=None)
    p_output.add_argument("--session-id", dest="session_id", default=None)
    p_output.set_defaults(func=wrap(cmd_jobs_output))

    p_run = parent.add_parser("run", help="Retired: manual job execution is unavailable")
    p_run.add_argument("job_id")
    p_run.add_argument("--cwd", dest="cwd_override")
    p_run.add_argument("--timeout-seconds", type=int, dest="timeout_seconds")
    p_run.add_argument("--org")
    p_run.set_defaults(func=wrap(cmd_jobs_run))

    p_tail = parent.add_parser(
        "tail", help="agent/founder: tail stdout/stderr of a job"
    )
    p_tail.add_argument("job_id")
    p_tail.add_argument(
        "--stream", choices=("stdout", "stderr"), default="stdout"
    )
    p_tail.add_argument("--lines", type=int, default=50)
    p_tail.add_argument("--task-id", dest="task_id", default=None)
    p_tail.add_argument("--session-id", dest="session_id", default=None)
    p_tail.add_argument("--org")
    p_tail.set_defaults(func=wrap(cmd_jobs_tail))

    p_wait = parent.add_parser(
        "wait", help="agent/founder: wait for a job to terminate"
    )
    p_wait.add_argument("job_id")
    p_wait.add_argument(
        "--timeout-seconds", type=int, dest="timeout_seconds", default=30
    )
    p_wait.add_argument("--task-id", dest="task_id", default=None)
    p_wait.add_argument("--session-id", dest="session_id", default=None)
    p_wait.add_argument("--org")
    p_wait.set_defaults(func=wrap(cmd_jobs_wait))

    p_stop = parent.add_parser(
        "stop", help="founder/agent: stop a running job"
    )
    p_stop.add_argument("job_id")
    p_stop.add_argument("--task-id", dest="task_id", default=None)
    p_stop.add_argument("--session-id", dest="session_id", default=None)
    p_stop.add_argument("--org")
    p_stop.set_defaults(func=wrap(cmd_jobs_stop))



def register(sub) -> None:
    p_jobs = sub.add_parser("jobs", help="Jobs (agent → founder review / background work)")
    jobs_sub = p_jobs.add_subparsers(dest="jobs_cmd")
    _register_jobs_verbs(jobs_sub, deprecated=False)

    p_scripts = sub.add_parser(
        "scripts",
        help="[deprecated] renamed to `jobs`; use `happyranch jobs <verb>`",
    )
    scripts_sub = p_scripts.add_subparsers(dest="scripts_cmd")
    _register_jobs_verbs(scripts_sub, deprecated=True)
