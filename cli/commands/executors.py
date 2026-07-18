"""Executor registration CLI verb — THR-052 PR-3 / THR-088.

The candidate CLI invokes ``happyranch executors register`` to perform
conformance check-ins and register a custom executor profile with the daemon.

Conformance steps (must match RegistrationTokenStore.DEFAULT_CONFORMANCE_STEPS):
  1. workspace_access   — the candidate reads its Agent prompt + workspace + skills
  2. loopback_reachable — the candidate reaches http://127.0.0.1 (daemon loopback)
  3. cli_callback        — the candidate runs this CLI verb with the hrreg_ token

On success the daemon atomically writes the profile into the
machine-global runtime store (``<daemon-home>/executor_profiles.yaml``;
THR-107 — the per-org config.yaml surface is removed) and the candidate
can be assigned to agents.

For runtime-level registration (org-agnostic, visible to all orgs), use
``happyranch executors runtime-register``.
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


def _print_register_result(r) -> None:
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


def _parse_argv_template(args: argparse.Namespace) -> list[str]:
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
            f"error: --argv-template-json must be a JSON array (got "
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
    return argv_template


def _run_conformance_steps(
    client: OpcClient,
    checkin_path: str,
    token_headers: dict[str, str],
    label: str,
) -> None:
    conformance_steps = [
        "workspace_access",
        "loopback_reachable",
        "cli_callback",
    ]

    print(f"Running conformance check-ins for {label}...")
    for step_id in conformance_steps:
        try:
            r = client.post(
                checkin_path,
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

    argv_template = _parse_argv_template(args)
    adapter = args.adapter or "pi"

    client = OpcClient.from_env()
    slug = args.org

    token_headers = {"Authorization": f"Bearer {token}"}

    # Step 1 — conformance check-ins
    _run_conformance_steps(
        client,
        f"/api/v1/orgs/{slug}/executors/conformance-checkin",
        token_headers,
        f"org {slug}",
    )

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

    _print_register_result(r)


# ── Runtime-level registration CLI (THR-088) ───────────────────────────


def cmd_executors_runtime_register(args: argparse.Namespace) -> None:
    """Candidate CLI entry point for RUNTIME-LEVEL registration.

    Same conformance check-in flow as org-scoped registration, but using
    the runtime-level routes (no --org required). The resulting profile
    is visible to ALL orgs on this machine.
    """
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

    argv_template = _parse_argv_template(args)
    adapter = args.adapter or "pi"

    client = OpcClient.from_env()
    token_headers = {"Authorization": f"Bearer {token}"}

    # Step 1 — conformance check-ins at runtime level
    _run_conformance_steps(
        client,
        "/api/v1/executors/runtime/conformance-checkin",
        token_headers,
        "runtime level",
    )

    # Step 2 — register the profile at runtime level
    print("\nRegistering runtime-level executor profile...")
    try:
        r = client.post(
            "/api/v1/executors/runtime/register",
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

    _print_register_result(r)


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

    # Runtime-level registration subcommand (THR-088)
    p_runtime = exec_sub.add_parser(
        "runtime-register",
        help="Register a RUNTIME-level executor profile (visible to ALL orgs, no --org required)",
    )
    p_runtime.add_argument(
        "--token",
        required=True,
        help="hrreg_ registration token from Settings -> Executors (runtime)",
    )
    p_runtime.add_argument("--exec-command", dest="exec_command", required=True, help="Executor command (executable name)")
    p_runtime.add_argument(
        "--argv-template-json",
        dest="argv_template_json",
        required=True,
        help="JSON-encoded argv template as a single string",
    )
    p_runtime.add_argument(
        "--adapter",
        default="pi",
        choices=["claude", "codex", "opencode", "pi"],
        help="Workspace adapter (default: pi)",
    )
    p_runtime.set_defaults(func=cmd_executors_runtime_register)
