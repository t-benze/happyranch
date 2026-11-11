"""Agent-local, single-attempt finalization. Never called by daemon settlement.

Saved intent is a replay fence, not a remote exactly-once protocol. A lost
response or uncertain command requires owner inspection; it never retries.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

import httpx
from pydantic import TypeAdapter

from cli._shared import require_absolute_payload_path, resolve_org_slug
from runtime.runtime import port_file
from runtime.daemon import pr_ci_waiter
from runtime.workflows.coding_saved_records import _canonical, _check, _hash, _json
from runtime.workflows.coding_start_records import (
    CodingBoundFinalOutput, BoundFinalSelection, CodingRevisionFinalOutput,
    github_origin, github_subject, parse_finalize, validate_final_identity,
)

from cli.commands.workflow_final_local import (
    CHECKS, _checks, _literal_directory, _now, _pr, _remote_json, _run, _write, run_guarded_action,
)


def _local_subject(command: dict, selected: dict, org: str) -> tuple[str, int, str]:
    validate_final_identity(selected, org=org)
    reference = selected['repository_reference']
    workspace = _literal_directory(Path(selected['workspace']['workspace_root']))
    checkout = _literal_directory(workspace / 'repos' / reference['repo_key'])
    root = _literal_directory(Path(command['repository_path']))
    _check(str(root) == command['repository_path'] and
           (root == checkout or root.is_relative_to(checkout / '.claude' / 'worktrees')))
    cwd = _literal_directory(Path.cwd())
    _check(cwd == workspace or cwd == checkout or cwd.is_relative_to(checkout / '.claude' / 'worktrees'))
    def git(*args: str) -> str:
        return _run(['git', *args], cwd=root).strip()
    _check(git('rev-parse', '--show-object-format') == 'sha1')
    _check(git('rev-parse', '--show-toplevel') == str(root))
    common = _literal_directory(checkout / '.git')
    _check(git('rev-parse', '--path-format=absolute', '--git-common-dir') == str(common)
           and not (root / '.git').is_symlink())
    # Exactly ONE raw configured URL, with the EXISTING UTF8 hashing semantics.
    # get-url also detects url.* rewrites; ambiguity and unavailable identity refuse.
    origin = _run(['git', 'config', '--get-all', 'remote.origin.url'], cwd=root).removesuffix('\n')
    _check(_hash(origin.encode('utf8')) == reference['origin_digest'] and
           _run(['git', 'remote', 'get-url', '--all', 'origin'], cwd=root).removesuffix('\n') == origin)
    repo = github_origin(origin)
    navigation_repo, number = github_subject(command['subject'])
    _check(navigation_repo == repo)
    subject = command['subject']
    for oid in (subject['comparison']['base'], subject['head']):
        _check(git('rev-parse', '--verify', oid + '^{commit}') == oid)
    _check(git('rev-parse', '--verify', 'refs/remotes/origin/' + subject['navigation']['branch']) == subject['head'])
    _check(number > 0)
    return repo, number, origin


def _selected(client: httpx.Client, org: str, sid: str, command: dict) -> dict:
    response = client.get(f"/api/v1/orgs/{org}/workflows/coding/runs/{command['instance_id']}/final-manager",
                          params={'session_id': sid})
    _check(response.status_code == 200 and len(response.content) <= 262144)
    result = TypeAdapter(BoundFinalSelection).validate_python(_remote_json(response.content, 262144)).model_dump()
    validate_final_identity(result, org=org)
    for key in ('instance_id', 'attempt_id', 'task_id', 'session_id', 'principal', 'revision', 'subject', 'upstream_results'):
        _check(result[key] == command[key])
    # Reparse the original selected full callback summaries locally. No recall fallback.
    for index, stage in enumerate(('development', 'code_review', 'qa')):
        original = result['results'][index]
        pin = result['upstream_results'][stage]
        _check(original['identity'] == pin and _hash(original['summary'].encode('utf8')) == pin['output_digest'])
        from runtime.workflows.coding_start_records import stage_output
        output = stage_output(_json(original['summary'].encode('utf8'), 65536))
        _check((output['instance_id'], output['attempt_id'], output['revision'], output['stage'], output['subject'], output['outcome']) ==
               (command['instance_id'], pin['attempt_id'], command['revision'], stage, command['subject'], pin['outcome'])
               and original['verdict'] == (None if stage == 'development' else pin['outcome']))
    return result


def _execute(client: httpx.Client, org: str, sid: str, command: dict) -> dict:
    selected = _selected(client, org, sid, command)
    repo, number, origin = _local_subject(command, selected, org)
    # Exactly one task-owned attempt directory, independent of caller operation key.
    directory = Path(selected['workspace']['workspace_root']) / 'output' / command['task_id'] / ('coding-finalize-' + _hash(command['attempt_id'].encode()))
    _check(command['task_id'] not in ('.', '..') and '/' not in command['task_id'])
    _literal_directory(directory.parent, create=True)
    directory.mkdir()  # Existing/partial intent refuses; no automatic replay or second action.
    reserved = directory.stat()
    checks: list[dict] = []
    observations: list[dict] = []
    error = ''
    try:
        waiter = pr_ci_waiter.wait_for_ci(repo=repo, pr_number=number,
            pinned_head_sha=command['subject']['head'], expected_checks=list(CHECKS),
            settle_seconds=0, timeout_seconds=0, poll_interval_seconds=0,
            fetch_pr_state=lambda: _pr(repo, number, command['subject']),
            fetch_checks=lambda head: _checks(repo, head, checks, directory, observations), clock=pr_ci_waiter._RealClock())
        ci = waiter.verdict
        error = waiter.error_detail or ''
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        ci, error = 'github_error', str(exc)
    # An incomplete two-check observation is held even if the generic waiter is permissive.
    if ci == 'ci_pass' and (len(checks) != 2 or {v['name'] for v in checks} != set(CHECKS)):
        ci, error = 'checks_missing', 'strict two-check evidence missing'
    intent_body = dict(command=command, created_at=_now(), selected_digest=_hash(_canonical(selected)),
        repository_reference=selected['repository_reference'], workspace=selected['workspace'], origin=origin)
    _write(directory / 'selected.json', selected)
    _write(directory / 'checks.json', dict(checks=checks, observations=observations, ci_verdict=ci, diagnostic=error))
    _write(directory / 'intent.json', intent_body)
    evidence = dict(intent=dict(**intent_body, path=str(directory / 'intent.json'), digest=_hash(_canonical(intent_body))),
        checks=checks, observations=observations, revalidated_at=None, guard_verdict=ci, command=None, remote=None, observation_diagnostic=error)
    def revalidate() -> None:
        _check(_selected(client, org, sid, command) == selected)
        _check(_local_subject(command, selected, org) == (repo, number, origin))
        _literal_directory(directory)
        current = directory.stat()
        _check((current.st_dev, current.st_ino) == (reserved.st_dev, reserved.st_ino))
    guard = run_guarded_action(repo=repo, number=number, head=command['subject']['head'], ci=ci,
        directory=directory, evidence=evidence, revalidate=revalidate,
        fetch_pr_state=lambda: _pr(repo, number, command['subject']),
        fetch_review_verdict=lambda: selected['upstream_results']['code_review']['outcome'],
        fetch_qa_verdict=lambda: selected['upstream_results']['qa']['outcome'])
    outcome = ('merged' if guard.verdict == 'merged' and evidence['command'] is not None
               and evidence['command']['succeeded'] else 'uncertain' if evidence['command'] is not None else 'held')
    findings = ('Performed expected-head guarded action and observed exact remote merge in this invocation.' if outcome == 'merged'
                else 'Finalization retained ' + outcome + ': ' + (guard.error_detail or error or guard.verdict))
    revised = command['format'] == 'workflow-coding-finalize-command@2'
    output = (CodingRevisionFinalOutput if revised else CodingBoundFinalOutput).model_validate(dict(format='workflow-coding-final-output@3' if revised else 'workflow-coding-final-output@2',
        instance_id=command['instance_id'], attempt_id=command['attempt_id'], revision=command['revision'], stage='agent_manager',
        subject=command['subject'], upstream_results=command['upstream_results'], outcome=outcome,
        findings=findings, evidence=evidence)).model_dump()
    _write(directory / 'final-output.json', output)
    return output


def cmd_workflow_coding_finalize(args: argparse.Namespace) -> None:
    try:
        absolute = require_absolute_payload_path(args.from_file, kind='coding-finalize')
        with Path(absolute).open('rb') as stream:
            command = parse_finalize(stream.read(65537))
        _check(command['session_id'] == args.session_id)
        org = resolve_org_slug(args_org=args.org, available=[])
        port = port_file().read_text().strip()
        _check(port.isascii() and port.isdecimal() and 0 < int(port) <= 65535)
        with httpx.Client(base_url=f'http://127.0.0.1:{port}', headers={'X-HappyRanch-Surface': 'cli'}, timeout=30.0) as client:
            output = _execute(client, org, args.session_id, command)
        print(_canonical(output).decode('utf8'))
        if output['outcome'] != 'merged':
            raise SystemExit(2)
    except (OSError, ValueError, TypeError, KeyError, httpx.HTTPError, subprocess.SubprocessError) as exc:
        print(f'error: coding finalization refused or recording incomplete; preserve receipts; do not retry: {exc}', file=sys.stderr)
        raise SystemExit(2) from None
