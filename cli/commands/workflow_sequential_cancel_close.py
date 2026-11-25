"""One-send human-only cancellation business close and exact-key readback."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from urllib.parse import quote

import httpx

from cli import _shared
from cli._shared import resolve_org_slug
from cli.client.client import DaemonNotRunning, DaemonStateInconsistent, OpcClient
from runtime.workflows.sequential_cancel_close import CancelCloseError, CancelCloseReceipt, parse_command, require


def command(args: argparse.Namespace) -> None:
    try:
        body = None
        if args.action == 'sequential-cancel-close-never-launched':
            path = Path(args.from_file)
            require(path.is_absolute() and path.is_file(), 'invalid_request', 422)
            with path.open('rb') as stream:
                body = parse_command(stream.read(16385)).model_dump()
    except (OSError, CancelCloseError):
        print(json.dumps({'detail': {'code': 'invalid_request'}}), file=sys.stderr)
        raise SystemExit(2) from None
    try:
        client = OpcClient.from_env()
        slug = resolve_org_slug(args_org=args.org, available=_shared._fetch_available_orgs(client))
        prefix = f'/api/v1/orgs/{quote(slug, safe="")}/workflows/sequential'
        if body is not None:
            response = client.post(prefix + '/cancel-close-never-launched', json=body)
        else:
            response = client.get(prefix + '/runs/' + quote(args.run_id, safe='') + '/cancel-close-never-launched',
                params={'operation_key': args.operation_key})
        value = response.json()
        if response.status_code == 200:
            value = CancelCloseReceipt.model_validate(value, strict=True).model_dump()
        if not isinstance(value, dict) or (response.status_code != 200 and not isinstance(value.get('detail'), dict)):
            raise ValueError('invalid_response')
        print(json.dumps(value, ensure_ascii=False))
        if response.status_code != 200:
            detail = value.get('detail', {})
            raise SystemExit(1 if detail.get('committed', False) is None else 2)
    except (DaemonNotRunning, DaemonStateInconsistent, httpx.HTTPError, OSError, ValueError, TypeError):
        print(json.dumps({'detail': {'code': 'transport_unavailable', 'committed': None, 'readback_required': True}}))
        raise SystemExit(1) from None


def register(sub: argparse._SubParsersAction) -> None:
    for action in ('sequential-cancel-close-never-launched', 'sequential-cancel-close-status'):
        parser = sub.add_parser(action, help='Human-only business close; native cleanup stays unresolved')
        parser.add_argument('--org')
        if action.endswith('status'):
            parser.add_argument('run_id')
            parser.add_argument('--operation-key', required=True)
        else:
            parser.add_argument('--from-file', required=True)
        parser.set_defaults(func=command, action=action)
