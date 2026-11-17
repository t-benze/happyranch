"""Read-only own-session composition for a genuine FINAL workflow_start decision."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Annotated

import httpx
from pydantic import Field

from cli._shared import require_absolute_payload_path, resolve_org_slug
from runtime.runtime import port_file
from runtime.workflows.sequential_contracts import (
    Closed, DEFINITION_LIMIT, InputValue, Limits, SettingsPin, Text, Token,
)
from runtime.workflows.sequential_definition import SequentialContractError, _json, parse_start, validate_start
from runtime.workflows.sequential_history import _bytes
from runtime.workflows.sequential_release import select_bundled_release
from runtime.workflows.sequential_start import SequentialStartDecision
from runtime.workflows.sequential_views import StartContext


class ComposeInput(Closed):
    """Discovery pins are required so reissuing the same file cannot repin."""

    definition: Annotated[str, Field(min_length=1, max_length=160)]
    settings: SettingsPin
    operation_key: Token
    request: Text
    inputs: Annotated[list[InputValue], Field(max_length=32)]
    lower_limits: Limits | None = None


def _input(path: str) -> ComposeInput:
    absolute = require_absolute_payload_path(path, kind='sequential-start-compose')
    try:
        with Path(absolute).open('rb') as stream:
            raw = stream.read(DEFINITION_LIMIT + 1)
        return ComposeInput.model_validate(_json(raw, DEFINITION_LIMIT))
    except (OSError, ValueError, TypeError, UnicodeError, RecursionError):
        raise ValueError('sequential_compose_input_invalid') from None


def compose(client: httpx.Client, org: str, sid: str, command: ComposeInput) -> bytes:
    """Only GET discovery and package reads; admission belongs to the callback."""
    with client.stream('GET', f'/api/v1/orgs/{org}/workflows/sequential/start-context',
            params=[('session_id', sid), ('definition', command.definition)]) as response:
        raw = bytearray()
        for chunk in response.iter_bytes():
            if len(raw) + len(chunk) > 262144:
                raise ValueError('sequential_start_context_too_large')
            raw.extend(chunk)
        if response.status_code != 200:
            # Route diagnostics are finite codes; never echo request/context bytes.
            try:
                code = json.loads(raw)['detail']['code']
            except (ValueError, KeyError, TypeError):
                code = 'unavailable'
            if type(code) is not str or len(code) > 128 or not code.replace('_', '').isascii() or not code.replace('_', '').isalpha():
                code = 'unavailable'
            raise ValueError(f'sequential_start_context_refused:{response.status_code}:{code}')
        context = StartContext.model_validate_json(bytes(raw), strict=True)
    if len(context.definitions) != 1 or context.definitions[0].selector != command.definition:
        raise ValueError('sequential_definition_conflict')
    selected = context.definitions[0]
    settings = selected.settings
    if settings is None or (settings.generation, settings.snapshot_digest) != (
            command.settings.generation, command.settings.snapshot_digest):
        raise ValueError('sequential_settings_conflict:409')
    if selected.unavailable_cause is not None or selected.admission != 'Allowed' or selected.limits is None:
        raise ValueError(selected.unavailable_cause or 'sequential_start_unavailable')
    compiled = select_bundled_release(selected.pin)
    lower = command.lower_limits or selected.limits
    if any(getattr(lower, key) > getattr(selected.limits, key) for key in Limits.model_fields):
        raise ValueError('sequential_lower_limits_exceed_settings')
    start = parse_start(_bytes(dict(format=context.start_format, operation_key=command.operation_key,
        definition=selected.pin.model_dump(mode='json'), settings=command.settings.model_dump(mode='json'),
        request=command.request, inputs=[item.model_dump(mode='json') for item in command.inputs],
        bindings=[item.model_dump(mode='json') for item in selected.defaults],
        lower_limits=lower.model_dump(mode='json'))))
    validate_start(start, compiled, original_limits=selected.limits)
    decision = SequentialStartDecision(action='workflow_start', workflow=start)
    return _bytes(decision.model_dump(mode='json'))


def cmd_workflow_sequential_start_compose(args: argparse.Namespace) -> None:
    try:
        command = _input(args.from_file)
        org = resolve_org_slug(args_org=args.org, available=[])
        port = port_file().read_text().strip()
        if not port.isascii() or not port.isdecimal() or not 0 < int(port) <= 65535:
            raise ValueError('daemon_port_unavailable')
        with httpx.Client(base_url=f'http://127.0.0.1:{port}',
                headers={'X-HappyRanch-Surface': 'cli'}, timeout=30.0) as client:
            decision = compose(client, org, args.session_id, command)
    except (OSError, ValueError, TypeError, httpx.HTTPError) as exc:
        # Pydantic errors may contain supplied content. Keep malformed response
        # and transport diagnostics categorical; preserve the input file/pins.
        code = str(exc) if type(exc) in (ValueError, SequentialContractError) else 'sequential_compose_unavailable'
        print(f'error: {code}; preserve original input, operation key and composed pins', file=sys.stderr)
        raise SystemExit(2) from None
    print(decision.decode('utf8'))
