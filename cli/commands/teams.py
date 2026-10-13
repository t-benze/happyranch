"""Bounded founder-facing team provisioning and registry readback."""
from __future__ import annotations

import argparse
import json

from cli._shared import _ok
from cli.client.client import OpcClient


def cmd_teams_list(args: argparse.Namespace) -> None:
    response = OpcClient.from_env().get(f"/api/v1/orgs/{args.org}/teams")
    if _ok(response):
        print(json.dumps(response.json(), indent=2))


def cmd_teams_ensure_default(args: argparse.Namespace) -> None:
    response = OpcClient.from_env().post(f"/api/v1/orgs/{args.org}/teams/ensure-default")
    if _ok(response):
        result = response.json()
        print(json.dumps(result, indent=2))
        if not result["authority"]["ready"]:
            raise SystemExit(1)


def register(sub) -> None:
    parser = sub.add_parser("teams", help="Read teams or ensure an empty founder-managed Default")
    commands = parser.add_subparsers(dest="teams_command", required=True)
    for name, handler, help_text in (
        ("list", cmd_teams_list, "Read the current team registry"),
        ("ensure-default", cmd_teams_ensure_default, "Provision an absent empty Default; preserve existing workers"),
    ):
        command = commands.add_parser(name, help=help_text)
        command.add_argument("--org", required=True)
        command.set_defaults(func=handler)
