"""Machine-local executor binary-path registration CLI — THR-085.

The daemon stores per-executor-kind binary paths in a machine-local registry
at ``<daemon-home>/executors.json`` so that headless daemon launches (no web UI)
can self-heal: the operator runs ``happyranch executor-binaries`` commands to
tell the daemon where each executor CLI binary lives on this host.

Distinct from ``executors.py`` (THR-052 PROFILE registration — which executor
kinds/capabilities exist, org-portable). This group writes into the
machine-local binary registry, NOT org/config.yaml.

Commands:
  register <kind> --path <ABS_PATH>  — validate-then-register a binary path
  list                                — list registered binary paths
"""
from __future__ import annotations

import argparse
import sys

from cli.client.client import OpcClient


def cmd_executor_binaries_register(args: argparse.Namespace) -> None:
    """Register a binary path for an executor kind.

    Calls POST /api/v1/executor-binaries/register (validate-then-store).
    """
    if not args.path.startswith("/"):
        print(
            f"error: --path must be an absolute path (got {args.path!r})",
            file=sys.stderr,
        )
        sys.exit(1)

    client = OpcClient.from_env()

    try:
        r = client.post(
            "/api/v1/executor-binaries/register",
            json={"kind": args.kind, "path": args.path},
        )
    except Exception as exc:
        print(f"error: failed to reach daemon — {exc}", file=sys.stderr)
        print("Is the daemon running?", file=sys.stderr)
        sys.exit(1)

    if r.status_code == 200:
        body = r.json()
        print(f"  + registered: {body['kind']} -> {body['path']} (valid={body['valid']})")
    elif r.status_code == 422:
        body = r.json()
        print(f"error: {body.get('detail', 'validation failed')}", file=sys.stderr)
        sys.exit(1)
    else:
        print(f"error: HTTP {r.status_code}", file=sys.stderr)
        try:
            detail = r.json()
            print(f"  {detail}", file=sys.stderr)
        except ValueError:
            print(f"  {r.text}", file=sys.stderr)
        sys.exit(1)


def cmd_executor_binaries_list(args: argparse.Namespace) -> None:
    """List all registered binary paths.

    Calls GET /api/v1/executor-binaries.
    """
    client = OpcClient.from_env()

    try:
        r = client.get("/api/v1/executor-binaries")
    except Exception as exc:
        print(f"error: failed to reach daemon — {exc}", file=sys.stderr)
        print("Is the daemon running?", file=sys.stderr)
        sys.exit(1)

    if r.status_code == 200:
        body = r.json()
        entries = body.get("entries", [])
        if not entries:
            print("no registered executor binaries")
            return
        for entry in entries:
            status = "valid" if entry.get("valid") else "stale"
            path = entry.get("path") or "(none)"
            print(f"  {entry['kind']:12s}  {path:45s}  ({status})")
    else:
        print(f"error: HTTP {r.status_code}", file=sys.stderr)
        try:
            detail = r.json()
            print(f"  {detail}", file=sys.stderr)
        except ValueError:
            print(f"  {r.text}", file=sys.stderr)
        sys.exit(1)


def register(sub) -> None:
    p = sub.add_parser(
        "executor-binaries",
        help="Machine-local executor binary path management",
    )
    exec_sub = p.add_subparsers(dest="executor_binaries_command", required=True)

    p_reg = exec_sub.add_parser(
        "register",
        help="Register a binary path for an executor kind (validate-then-store)",
    )
    p_reg.add_argument("kind", help="Executor kind, e.g. 'claude', 'codex', 'pi'")
    p_reg.add_argument(
        "--path",
        required=True,
        help="Absolute path to the executor binary",
    )
    p_reg.set_defaults(func=cmd_executor_binaries_register)

    p_list = exec_sub.add_parser(
        "list",
        help="List registered executor binary paths",
    )
    p_list.set_defaults(func=cmd_executor_binaries_list)
