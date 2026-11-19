"""Daemon-independent local connection fallback (Linux host and foreground client)."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

HOST_ACTIONS = ("status", "devices", "next-device", "approve-device", "reject-device", "revoke-device")
UNAVAILABLE = ("enable", "turn-off", "create-invite", "cancel-invite", "replace-failed",
               "refresh-home-address", "retry-policy-renewal")

CLIENT_ACTIONS = ("status", "accept-invite", "saved-computers", "connect-saved", "remove-saved",
                  "disconnect", "cancel-attempt", "retry", "retry-save", "health")


class RoleParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        if " client" in self.prog:
            print('{"code":"invalidGrammar","effect":"none"}', file=sys.stderr)
            raise SystemExit(2)
        super().error(message)

    def parse_known_args(self, args: list | None = None, namespace: argparse.Namespace | None = None) -> tuple:
        result, extras = super().parse_known_args(args, namespace)
        if self.prog.endswith(" client") and extras:
            self.error("")  # Do not let the outer parser echo rejected invite/credential arguments.
        return result, extras


def native_entry(role: str = "host") -> Path:
    # Source checkout or relocatable Linux payload; never cwd/PYTHONPATH/config.
    filename = "client_cli.py" if role == "client" else "connection_cli.py"
    source = Path(__file__).resolve().parents[2] / "packaging/linux/desktop" / filename
    if source.is_file():
        return source
    return Path(sys.executable).resolve().parents[2] / "share/happyranch/desktop" / filename


def cmd_connection(args: argparse.Namespace) -> None:
    linux = sys.platform == "linux"
    if args.connection_role == "status":
        print(json.dumps({"platform": "linux" if linux else "mac" if sys.platform == "darwin" else None,
                          "roles": {"host": "available" if linux and native_entry().is_file() else "unavailable",
                                    "client": "available" if linux and native_entry("client").is_file() else "unavailable"}}))
        return
    entry = native_entry(args.connection_role)
    available = CLIENT_ACTIONS if args.connection_role == "client" else HOST_ACTIONS
    if linux and args.connection_action in available and entry.is_file():
        try:
            os.execv("/usr/bin/python3", ["/usr/bin/python3", "-I", str(entry), args.connection_action])
        except OSError:
            pass
    print('{"code":"unavailable","effect":"none","recovery":"nativeRecovery"}')
    raise SystemExit(1)


def register(sub: argparse._SubParsersAction) -> None:
    parser = sub.add_parser("connection", help="Local connection fallback; no daemon required")
    roles = parser.add_subparsers(dest="connection_role", required=True, parser_class=RoleParser)
    roles.add_parser("status", help="Nonsecret platform/role availability; no host health probe").set_defaults(func=cmd_connection)
    host = roles.add_parser("host", help="Linux host actions; Mac host is deferred",
                           description="Linux host fallback. Mutations require a desktop system prompt. "
                           "Revoke selects interactively in this process. "
                           "Enable/turn-off/create-invite and lifecycle recovery are unavailable.")
    actions = host.add_subparsers(dest="connection_action", required=True)
    for action in HOST_ACTIONS + UNAVAILABLE:
        actions.add_parser(action, help="Unavailable in this slice" if action in UNAVAILABLE else
                           "Local fixed helper action").set_defaults(func=cmd_connection)

    client = roles.add_parser("client", help="Linux foreground client; Mac client CLI is deferred",
                              description="Terminal-only invite and saved selection. Connections live in this "
                              "invocation until Ctrl-C/SIGTERM. Separate control actions return notOwned.")
    actions = client.add_subparsers(dest="connection_action", required=True)
    for action in CLIENT_ACTIONS:
        actions.add_parser(action, help="Local native client action").set_defaults(func=cmd_connection)
