"""Thin knowledge CLI, with local validation before daemon discovery."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from urllib.parse import quote

from pydantic import ValidationError

from runtime.knowledge.lifecycle_models import DistillSubmission

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


def _lifecycle(args: argparse.Namespace) -> None:
    # Everything dependent only on local input precedes from_env/org discovery.
    body = None
    try:
        if args.org is not None and not re.fullmatch(r'[a-z0-9-]{1,40}', args.org):
            raise ValueError
        if args.knowledge_command == 'distill':
            path = Path(args.from_file)
            if not path.is_absolute() or not path.is_file():
                raise ValueError
            with path.open('rb') as stream:
                raw = stream.read(16385)
            if len(raw) > 16384:
                raise ValueError
            def unique(pairs: list[tuple[str, object]]) -> dict:
                value = {}
                for key, child in pairs:
                    if key in value:
                        raise ValueError
                    value[key] = child
                return value
            parsed = json.loads(raw, object_pairs_hook=unique)
            body = DistillSubmission.model_validate(parsed).model_dump(mode='json')
            suffix = '/distill'
        elif args.knowledge_command == 'distill-show':
            if not re.fullmatch(r'distill-[0-9a-f]{32}', args.operation):
                raise ValueError
            suffix = '/distill/' + args.operation
        elif args.archive_command == 'list':
            suffix = '/archives'
        else:
            if not re.fullmatch(r'archive-[0-9a-f]{32}', args.archive):
                raise ValueError
            suffix = '/archives/' + args.archive
    except (OSError, ValueError, ValidationError):
        print(json.dumps({'detail': {'code': 'invalid_knowledge_request'}}))
        raise SystemExit(2) from None
    client = OpcClient.from_env()
    slug = resolve_org_slug(args_org=args.org, available=_shared._fetch_available_orgs(client))
    url = f'/api/v1/orgs/{quote(slug, safe="")}/knowledge' + suffix
    response = client.post(url, json=body) if body is not None else client.get(url)
    if _ok(response):
        print(json.dumps(response.json(), ensure_ascii=False, indent=2))


def register(sub: argparse._SubParsersAction) -> None:
    group = sub.add_parser('knowledge', help='Read sources, distill knowledge, and inspect archives')
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

    for command in ('distill', 'distill-show'):
        parser = verbs.add_parser(command)
        if command == 'distill':
            parser.add_argument('--from-file', required=True, metavar='ABS', help='Absolute JSON confirmation file')
        else:
            parser.add_argument('operation', metavar='OP')
        parser.add_argument('--org', default=None)
        parser.set_defaults(func=_lifecycle)
    archives = verbs.add_parser('archives')
    commands = archives.add_subparsers(dest='archive_command', required=True)
    for command in ('list', 'show'):
        parser = commands.add_parser(command)
        if command == 'show':
            parser.add_argument('archive', metavar='ARCHIVE')
        parser.add_argument('--org', default=None)
        parser.set_defaults(func=_lifecycle)
