"""Daemon-independent local connection fallback (Linux host slice 5a)."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

HOST_ACTIONS = ("status", "devices", "next-device", "approve-device", "reject-device", "revoke-device")
UNAVAILABLE = ("enable", "turn-off", "create-invite", "cancel-invite", "replace-failed",
               "refresh-home-address", "retry-policy-renewal")


def native_entry() -> Path:
    # Source checkout or relocatable Linux payload; never cwd/PYTHONPATH/config.
    source = Path(__file__).resolve().parents[2] / "packaging/linux/desktop/connection_cli.py"
    if source.is_file():
        return source
    return Path(sys.executable).resolve().parents[2] / "share/happyranch/desktop/connection_cli.py"


def cmd_connection(args: argparse.Namespace) -> None:
    linux = sys.platform == "linux"
    if args.connection_role == "status":
        print(json.dumps({"platform": "linux" if linux else "mac" if sys.platform == "darwin" else None,
                          "roles": {"host": "available" if linux and native_entry().is_file() else "unavailable",
                                    "client": "unavailable"}}))
        return
    if linux and args.connection_action in HOST_ACTIONS and native_entry().is_file():
        try:
            os.execv("/usr/bin/python3", ["/usr/bin/python3", "-I", str(native_entry()), args.connection_action])
        except OSError:
            pass
    print('{"code":"unavailable","effect":"none","recovery":"nativeRecovery"}')
    raise SystemExit(1)


def register(sub: argparse._SubParsersAction) -> None:
    parser = sub.add_parser("connection", help="Local connection fallback; no daemon required")
    roles = parser.add_subparsers(dest="connection_role", required=True)
    roles.add_parser("status", help="Nonsecret platform/role availability; no host health probe").set_defaults(func=cmd_connection)
    host = roles.add_parser("host", help="Linux host actions; Mac host is deferred",
                           description="Linux host fallback. Mutations require a desktop system prompt. "
                           "Revoke selects interactively in this process. Client CLI is unavailable. "
                           "Enable/turn-off/create-invite and lifecycle recovery are unavailable.")
    actions = host.add_subparsers(dest="connection_action", required=True)
    for action in HOST_ACTIONS + UNAVAILABLE:
        actions.add_parser(action, help="Unavailable in this slice" if action in UNAVAILABLE else
                           "Local fixed helper action").set_defaults(func=cmd_connection)
