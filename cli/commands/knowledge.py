"""Thin read-only retired-source CLI. JSON preserves coverage and unknowns."""
from __future__ import annotations

import argparse
import json
from urllib.parse import quote

from cli import _shared
from cli._shared import _ok, resolve_org_slug
from cli.client.client import OpcClient


def _show(args: argparse.Namespace) -> None:
    client = OpcClient.from_env()
    slug = resolve_org_slug(args_org=args.org, available=_shared._fetch_available_orgs(client))
    path = f'/api/v1/orgs/{quote(slug, safe="")}/knowledge/retired-agents/{quote(args.source, safe="")}'
    if args.reference is not None:
        path += f'/sources/{quote(args.reference, safe="")}'
    response = client.get(path)
    if _ok(response):
        print(json.dumps(response.json(), ensure_ascii=False, indent=2))


def register(sub: argparse._SubParsersAction) -> None:
    group = sub.add_parser('knowledge', help='Read retained knowledge sources')
    verbs = group.add_subparsers(dest='knowledge_command', required=True)
    retired = verbs.add_parser('retired', help='Read retired sources without migration')
    commands = retired.add_subparsers(dest='retired_command', required=True)
    for command in ('show', 'source'):
        parser = commands.add_parser(command)
        parser.add_argument('source', metavar='SOURCE', help='Permanent retired agent ID')
        if command == 'source':
            parser.add_argument('reference', metavar='REFERENCE', help='Opaque captured reference ID')
        else:
            parser.set_defaults(reference=None)
        parser.add_argument('--org', default=None)
        parser.set_defaults(func=_show)
