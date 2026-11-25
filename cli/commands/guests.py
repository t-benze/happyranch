"""Founder-only local guest project setup."""
from __future__ import annotations

import argparse

from cli._shared import _fetch_available_orgs, _ok, resolve_org_slug
from cli.client.client import OpcClient


def cmd_guests_setup(args: argparse.Namespace) -> None:
    with OpcClient.from_env() as client:
        slug = resolve_org_slug(args_org=args.org, available=_fetch_available_orgs(client) if not args.org else [])
        response = client.post(f"/api/v1/orgs/{slug}/guests/setup/{args.app_id}")
        if _ok(response):
            print(response.json()["path"])


def register(sub: argparse._SubParsersAction) -> None:
    parser = sub.add_parser("guests", help="Founder local desktop guest setup")
    commands = parser.add_subparsers(dest="guests_action", required=True)
    setup = commands.add_parser("setup", help="Write the daemon-owned Codex guest project")
    setup.add_argument("app_id", choices=["codex_desktop"])
    setup.add_argument("--org")
    setup.set_defaults(func=cmd_guests_setup)
