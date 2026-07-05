"""System assistant setup and status commands."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Any

from cli.client.client import DaemonNotRunning, DaemonStateInconsistent, OpcClient


def _client() -> OpcClient:
    try:
        return OpcClient.from_env()
    except (DaemonNotRunning, DaemonStateInconsistent) as exc:
        print(f"Error: {exc}")
        sys.exit(1)


def _print_status(body: dict[str, Any]) -> None:
    print(f"state: {body['state']}")
    if body.get("selected_executor"):
        print(f"executor: {body['selected_executor']}")
    if body.get("workspace_path"):
        print(f"workspace: {body['workspace_path']}")
    if body.get("detail"):
        print(f"detail: {body['detail']}")


def cmd_assistant_status(args: argparse.Namespace) -> None:
    client = _client()
    r = client.get("/api/v1/assistant/status")
    if r.status_code != 200:
        print(f"Error ({r.status_code}): {r.text}")
        sys.exit(1)
    _print_status(r.json())


def cmd_assistant_init(args: argparse.Namespace) -> None:
    client = _client()
    if args.repair and not args.reconfigure:
        r = client.post("/api/v1/assistant/repair")
        if r.status_code != 200:
            print(f"Error ({r.status_code}): {r.text}")
            sys.exit(1)
        _print_status(r.json())
        return
    r = client.post(
        "/api/v1/assistant/init",
        json={"reconfigure": bool(args.reconfigure)},
    )
    if r.status_code != 200:
        print(f"Error ({r.status_code}): {r.text}")
        sys.exit(1)
    body = r.json()
    _print_status(body)
    if body["state"] != "configured":
        workspace = body.get("workspace_path") or "<runtime>/system/assistant/workspace"
        print()
        print("Next steps to register your assistant CLI:")
        print("1. Open your agentic CLI (claude, codex, opencode, pi, ...) in:")
        print(f"     {workspace}")
        print("2. Ask it to register itself; it will run:")
        print("     happyranch assistant register --from-file <payload.json>")


def cmd_assistant_register(args: argparse.Namespace) -> None:
    client = _client()
    import json as _json

    if args.from_file:
        try:
            body = _json.loads(Path(args.from_file).read_text())
        except (OSError, _json.JSONDecodeError, ValueError) as exc:
            print(f"Error reading register file {args.from_file}: {exc}")
            sys.exit(1)
    else:
        body = {
            "executor": args.executor,
            "command": args.register_command,
            "argv": _json.loads(args.argv) if args.argv else [],
        }

    r = client.post("/api/v1/assistant/register", json=body)
    if r.status_code != 200:
        print(f"Error ({r.status_code}): {r.text}")
        sys.exit(1)
    _print_status(r.json())



def register(
    sub: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    p = sub.add_parser("assistant", help="manage the system assistant")
    p.set_defaults(assistant_cmd="status", func=cmd_assistant_status)
    assistant_sub = p.add_subparsers(dest="assistant_cmd")
    assistant_sub.default = "status"
    assistant_sub.required = False

    p_init = assistant_sub.add_parser("init", help="initialize the system assistant")
    group = p_init.add_mutually_exclusive_group()
    group.add_argument("--repair", action="store_true")
    group.add_argument("--reconfigure", action="store_true")
    p_init.set_defaults(func=cmd_assistant_init)

    p_register = assistant_sub.add_parser(
        "register",
        help="register the current agentic CLI as the system assistant",
    )
    p_register.add_argument(
        "--from-file",
        dest="from_file",
        default=None,
        help="Path to a JSON file with {executor, command, argv}",
    )
    p_register.add_argument("--executor", default=None)
    # dest avoids colliding with the top-level subparsers' dest="command".
    p_register.add_argument("--command", dest="register_command", default=None)
    p_register.add_argument(
        "--argv",
        default=None,
        help="JSON array string for argv (e.g. '[\"claude\"]')",
    )
    p_register.set_defaults(func=cmd_assistant_register)

    p_status = assistant_sub.add_parser("status", help="show system assistant status")
    p_status.set_defaults(func=cmd_assistant_status)
