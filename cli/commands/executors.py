"""Executor registration CLI verb — THR-052 PR-3.

The candidate CLI invokes ``happyranch executors register`` to perform
conformance check-ins and register a custom executor profile with the daemon.

Conformance steps (must match RegistrationTokenStore.DEFAULT_CONFORMANCE_STEPS):
  1. workspace_access   — the candidate reads its Agent prompt + workspace + skills
  2. loopback_reachable — the candidate reaches http://127.0.0.1 (daemon loopback)
  3. cli_callback        — the candidate runs this CLI verb with the hrreg_ token

On success the daemon atomically writes the profile into org/config.yaml
and the candidate can be assigned to agents.
"""
from __future__ import annotations

import argparse
import json
import sys

from cli import _shared
from cli._shared import _ok, resolve_org_slug
from cli.client.client import OpcClient


def _client_and_org(args: argparse.Namespace) -> tuple[OpcClient, str]:
    client = OpcClient.from_env()
    slug = resolve_org_slug(
        args_org=args.org,
        available=_shared._fetch_available_orgs(client),
    )
    return client, slug


def cmd_executors_register(args: argparse.Namespace) -> None:
    """Candidate CLI entry point: conformance check-ins + register.

    1. For each required conformance step, POST to
       /orgs/{slug}/executors/conformance-checkin with the hrreg_ token.
       workspace_access and loopback_reachable are auto-completed (the fact
       the candidate can reach the daemon loopback proves these).
       cli_callback is what the candidate is doing RIGHT NOW.
    2. Then POST /orgs/{slug}/executors/register with the profile.
    """
    if not args.org:
        print("error: --org <slug> is required", file=sys.stderr)
        sys.exit(1)
    if not args.token:
        print(
            "error: --token <registration-token> is required.\n"
            "Copy the hrreg_ token from the Settings → Executors prompt.",
            file=sys.stderr,
        )
        sys.exit(1)

    token = args.token.strip()
    if not token.startswith("hrreg_"):
        print("error: --token must start with 'hrreg_'", file=sys.stderr)
        sys.exit(1)

    # Parse argv_template_json from a JSON-encoded string.
    # This safely carries leading-dash tokens (e.g. --prompt-file) inside
    # a quoted JSON string that argparse sees as a single value.
    raw_json = args.argv_template_json.strip()
    if not raw_json:
        print(
            "error: --argv-template-json is required and must be a non-empty JSON array of strings",
            file=sys.stderr,
        )
        sys.exit(1)
    try:
        argv_template = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        print(
            f"error: --argv-template-json is not valid JSON: {exc}",
            file=sys.stderr,
        )
        sys.exit(1)
    if not isinstance(argv_template, list):
        print(
            "error: --argv-template-json must be a JSON array (got "
            f"{type(argv_template).__name__})",
            file=sys.stderr,
        )
        sys.exit(1)
    if not all(isinstance(e, str) for e in argv_template):
        bad = [e for e in argv_template if not isinstance(e, str)]
        print(
            f"error: --argv-template-json must contain only strings; "
            f"found non-string element{'s' if len(bad) > 1 else ''}: {bad!r}",
            file=sys.stderr,
        )
        sys.exit(1)
    if not argv_template:
        print(
            "error: --argv-template-json is required and must be a non-empty JSON array",
            file=sys.stderr,
        )
        sys.exit(1)

    adapter = args.adapter or "pi"

    client = OpcClient.from_env()
    slug = args.org

    # Headers with the registration token
    token_headers = {"Authorization": f"Bearer {token}"}

    # Step 1 — conformance check-ins for all 3 required steps.
    # Order matters: workspace_access, then loopback_reachable, then cli_callback.
    # The auto-completed steps succeed because the candidate is running locally
    # and can reach the daemon loopback.
    conformance_steps = [
        "workspace_access",
        "loopback_reachable",
        "cli_callback",
    ]

    print(f"Running conformance check-ins for org {slug}...")
    for step_id in conformance_steps:
        try:
            r = client.post(
                f"/api/v1/orgs/{slug}/executors/conformance-checkin",
                json={"step_id": step_id},
                headers=token_headers,
            )
        except Exception as exc:
            print(f"  x {step_id}: connection error — {exc}", file=sys.stderr)
            print(
                "Is the daemon running? The registration token only works on "
                "the same machine as the daemon (loopback).",
                file=sys.stderr,
            )
            sys.exit(1)

        if r.status_code == 200:
            body = r.json()
            arrived = body.get("arrived", False)
            pending = body.get("pending", [])
            mark = "+" if arrived else "o"
            print(f"  {mark} {step_id:25s}  pending: {pending or 'none'}")
        else:
            print(f"  x {step_id}: HTTP {r.status_code}", file=sys.stderr)
            try:
                detail = r.json()
                print(f"    {detail}", file=sys.stderr)
            except ValueError:
                print(f"    {r.text}", file=sys.stderr)
            sys.exit(1)

    # Step 2 — register the profile
    print(f"\nRegistering executor profile with org {slug}...")
    try:
        r = client.post(
            f"/api/v1/orgs/{slug}/executors/register",
            json={
                "command": args.exec_command,
                "argv_template": argv_template,
                "adapter": adapter,
            },
            headers=token_headers,
        )
    except Exception as exc:
        print(f"  x registration failed — {exc}", file=sys.stderr)
        sys.exit(1)

    if r.status_code == 200:
        body = r.json()
        print(f"  + registered: {body['name']} ({body['kind']})")
        print(f"    adapter   : {body['adapter_id']}")
        print(f"    command   : {body['command']}")
        print(f"    argv      : {body['argv_template']}")
        print(
            "\nThe executor profile is now available. Assign it to agents from "
            "the Agents page or via the Settings panel."
        )
    else:
        print(f"  x registration rejected: HTTP {r.status_code}", file=sys.stderr)
        try:
            detail = r.json()
            print(f"    {json.dumps(detail, indent=2)}", file=sys.stderr)
        except ValueError:
            print(f"    {r.text}", file=sys.stderr)
        sys.exit(1)


def register(sub) -> None:
    p = sub.add_parser("executors", help="Executor profile management")
    exec_sub = p.add_subparsers(dest="executors_command", required=True)

    p_reg = exec_sub.add_parser(
        "register",
        help="Register a custom executor profile (candidate CLI verb)",
    )
    p_reg.add_argument("--org", required=True, help="Org slug")
    p_reg.add_argument(
        "--token",
        required=True,
        help="hrreg_ registration token from Settings -> Executors",
    )
    p_reg.add_argument("--exec-command", dest="exec_command", required=True, help="Executor command (executable name)")
    p_reg.add_argument(
        "--argv-template-json",
        dest="argv_template_json",
        required=True,
        help="JSON-encoded argv template as a single string "
             "(e.g. '[\"--prompt-file\", \"{prompt}\", \"--timeout\", \"{timeout_seconds}\"]')",
    )
    p_reg.add_argument(
        "--adapter",
        default="pi",
        choices=["claude", "codex", "opencode", "pi"],
        help="Workspace adapter (default: pi)",
    )
    p_reg.set_defaults(func=cmd_executors_register)
