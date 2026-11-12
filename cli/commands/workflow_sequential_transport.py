"""Own-current repository transport and live-manager LOCAL single-attempt Final mount."""
from __future__ import annotations

import argparse
import base64
import subprocess
import sys
from pathlib import Path
from typing import Annotated, Literal

import httpx
from pydantic import Field

from cli._shared import require_absolute_payload_path, resolve_org_slug
from cli.commands import workflow_final_local as local
from cli.commands.workflow_sequential_finalize import execute_selected_final
from runtime.runtime import port_file
from runtime.workflows.sequential_coding_final_observation import read_final_callback_content
from runtime.workflows.sequential_coding_final_records import CodingFinalSelection, read_final_selection, record_digest
from runtime.workflows.sequential_coding_output import CodeChangeDeclaration
from runtime.workflows.sequential_contracts import Closed, Token
from runtime.workflows.sequential_definition import _json
from runtime.workflows.sequential_history import _bytes
from runtime.workflows.sequential_repository_command import read_repository_command
from runtime.workflows.sequential_repository_records import RepositoryChangeSubject


class FinalizeCommand(Closed):
    format: Literal['workflow-sequential-finalize-command@1']
    operation_key: Token
    repository_path: Annotated[str, Field(min_length=1, max_length=4096)]
    change: CodeChangeDeclaration


class RemoteSelection:
    """Fresh daemon owner selection on EVERY local adapter bookend; no cache."""

    def __init__(self, client: httpx.Client, org: str, sid: str) -> None:
        self.client, self.org, self.sid = client, org, sid

    def current(self, change: bytes) -> CodingFinalSelection:
        response = self.client.get(f'/api/v1/orgs/{self.org}/workflows/sequential/final-manager',
            params=[('session_id', self.sid), ('change', change.decode('utf8'))])
        if response.status_code != 200:
            raise ValueError(f'sequential_selection_refused:{response.status_code}:'
                             + response.text[:4096])
        selected = read_final_selection(response.content)
        if (selected.org_slug != self.org or selected.session_id != self.sid
                or _bytes(selected.change.model_dump(mode='json')) != change):
            raise ValueError('sequential_selection_changed')
        return selected

    def select(self, *, task_id: str, principal: str, session_id: str, change: bytes) -> CodingFinalSelection:
        selected = self.current(change)
        if (selected.task_id, selected.principal, selected.session_id) != (task_id, principal, session_id):
            raise ValueError('sequential_selection_changed')
        return selected


def finalize(client: httpx.Client, org: str, sid: str, command: FinalizeCommand) -> dict:
    """No supplied task/principal/selection pin, OrgState, job or daemon action."""
    change = _bytes(command.change.model_dump(mode='json'))
    selection = RemoteSelection(client, org, sid)
    selected = selection.current(change)
    result = execute_selected_final(selection=selection, task_id=selected.task_id,
        principal=selected.principal, session_id=selected.session_id, change=change,
        operation_key=command.operation_key, repository_path=Path(command.repository_path))
    content = b''
    outcome = 'BLOCK'
    findings = ['Final retained ' + result.disposition + ': ' + (result.diagnostic or 'observation unavailable')]
    if result.disposition == 'merged':
        content = _bytes(dict(format='workflow-sequential-coding-final-observation@1',
            change=selected.change.model_dump(mode='json'), selection_digest=record_digest(selected),
            intent=result.intent.model_dump(mode='json'), command=result.command.model_dump(mode='json'),
            remote=result.remote.model_dump(mode='json')))
        read_final_callback_content(content)  # Compatible closed U5b2b2 reader, before producing carrier.
        outcome, findings = 'merged', []
    callback = dict(format='workflow-sequential-callback@1', outcome=outcome,
        content_base64=base64.b64encode(content).decode('ascii'), findings=findings, capability_claims=[])
    local._write(result.directory / 'callback.json', callback)
    from cli.commands.workflow_sequential_final_journal import _sync_directory
    _sync_directory(result.directory)
    return callback


def cmd_workflow_sequential(args: argparse.Namespace) -> None:
    try:
        org = resolve_org_slug(args_org=args.org, available=[])
        port = port_file().read_text().strip()
        if not port.isascii() or not port.isdecimal() or not 0 < int(port) <= 65535:
            raise ValueError('daemon_port_unavailable')
        with httpx.Client(base_url=f'http://127.0.0.1:{port}',
                headers={'X-HappyRanch-Surface': 'cli'}, timeout=30.0) as client:
            if args.sequential_command == 'repository-preflight':
                response = client.get(f'/api/v1/orgs/{org}/workflows/sequential/repository-preflight',
                                      params={'session_id': args.session_id})
                if response.status_code != 200:
                    raise ValueError(f'sequential_preflight_refused:{response.status_code}:' + response.text[:4096])
                result = RepositoryChangeSubject.model_validate_json(response.content, strict=True).model_dump(mode='json')
            elif args.sequential_command in ('repository-resolve', 'repository-bind'):
                absolute = require_absolute_payload_path(args.from_file, kind='sequential-repository')
                with Path(absolute).open('rb') as stream:
                    raw = stream.read(49153)
                read_repository_command(raw)
                response = client.post(f'/api/v1/orgs/{org}/workflows/sequential/{args.sequential_command}',
                    params={'session_id': args.session_id}, content=raw,
                    headers={'Content-Type': 'application/json'})
                if response.status_code != 200:
                    raise ValueError(f'sequential_repository_refused:{response.status_code}:' + response.text[:4096])
                result = _json(response.content, 16384)
            else:
                absolute = require_absolute_payload_path(args.from_file, kind='sequential-finalize')
                with Path(absolute).open('rb') as stream:
                    raw = stream.read(49153)
                _json(raw, 49152)
                command = FinalizeCommand.model_validate_json(raw, strict=True)
                if _bytes(command.model_dump(mode='json')) != raw:
                    raise ValueError('sequential_finalize_noncanonical')
                result = finalize(client, org, args.session_id, command)
        print(_bytes(result).decode('utf8'))
        if args.sequential_command in ('repository-resolve', 'repository-bind') and (
                result.get('disposition') == 'Held' or result.get('format') == 'workflow-sequential-repository-hold@1'):
            raise SystemExit(2)
        if args.sequential_command == 'finalize' and result['outcome'] != 'merged':
            raise SystemExit(2)
    except (OSError, ValueError, TypeError, KeyError, httpx.HTTPError, subprocess.SubprocessError) as exc:
        if args.sequential_command in ('repository-resolve', 'repository-bind'):
            print(f'error: sequential repository transport refused or uncertain; no automatic retry; '
                  f'preserve the body for explicit reissue: {exc}', file=sys.stderr)
            raise SystemExit(2) from None
        print(f'error: sequential transport refused or recording uncertain; preserve original records; '
              f'do not reissue Final: {exc}', file=sys.stderr)
        raise SystemExit(2) from None
