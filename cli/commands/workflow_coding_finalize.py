"""Agent-local, single-attempt finalization. Never called by daemon settlement.

Saved intent is a replay fence, not a remote exactly-once protocol. A lost
response or uncertain command requires owner inspection; it never retries.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx

from cli._shared import require_absolute_payload_path, resolve_org_slug
from runtime.runtime import port_file
from runtime.daemon import pr_ci_merge, pr_ci_waiter
from runtime.workflows.coding_saved_records import _canonical, _check, _constant, _hash, _json, _pairs
from runtime.workflows.coding_start_records import (
    CodingCheckEvidence, CodingFinalOutput, CodingFinalSelection, github_subject, parse_finalize,
)

CHECKS = ('Source Boundary Lint', 'Web Lint (Node 24)')


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write(path: Path, value: dict) -> None:
    raw = _canonical(value)
    _check(len(raw) <= 1048576)
    # No truncation/overwrite; even an incomplete receipt conservatively fences replay.
    with path.open('xb') as stream:
        _check(stream.write(raw) == len(raw))
        stream.flush()
        os.fsync(stream.fileno())


def _run(argv: list[str], *, cwd: Path | None = None) -> str:
    result = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        raise ValueError(f'command exit={result.returncode}: {result.stderr}')
    _check(len(result.stdout.encode('utf8')) <= 1048576)
    return result.stdout


def _remote_json(raw: bytes, limit: int) -> dict:
    # Provider/HTTP JSON is not our canonical persisted encoding. Preserve
    # native types and reject duplicates/nonfinite values without requiring
    # GitHub or JSONResponse to use our serializer/key order.
    _check(type(raw) is bytes and 0 < len(raw) <= limit)
    value = json.loads(raw.decode('utf8'), object_pairs_hook=_pairs, parse_constant=_constant)
    _check(type(value) is dict)
    return value


def _api(path: str) -> dict:
    return _remote_json(_run(['gh', 'api', path]).encode('utf8'), 1048576)


def _local_subject(command: dict, repo: str, number: int) -> None:
    root = Path(command['repository_path']).resolve(strict=True)
    workspace = Path.cwd().resolve(strict=True)
    _check(root.is_relative_to(workspace / 'repos') or root == workspace)
    def git(*args: str) -> str:
        return _run(['git', *args], cwd=root).strip()
    _check(git('rev-parse', '--show-object-format') == 'sha1')
    origin = git('remote', 'get-url', 'origin')
    match = re.fullmatch(r'(?:https://github\.com/|git@github\.com:)([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?)(?:\.git)?', origin)
    _check(match is not None and match[1] == repo)
    subject = command['subject']
    for oid in (subject['comparison']['base'], subject['head']):
        _check(git('rev-parse', '--verify', oid + '^{commit}') == oid)
    _check(git('rev-parse', '--verify', 'refs/remotes/origin/' + subject['navigation']['branch']) == subject['head'])
    _check(number > 0)


def _pr(repo: str, number: int, subject: dict) -> pr_ci_waiter.PRState:
    value = _remote_json(_run(['gh', 'pr', 'view', str(number), '--repo', repo, '--json',
        'number,url,headRefOid,headRefName,isDraft,state,baseRefOid']).encode('utf8'), 65536)
    _check(type(value['number']) is int and value['number'] == number
           and value['url'] == f'https://github.com/{repo}/pull/{number}'
           and value['headRefName'] == subject['navigation']['branch']
           and value['baseRefOid'] == subject['comparison']['base']
           and type(value['isDraft']) is bool and value['state'] in ('OPEN', 'CLOSED', 'MERGED'))
    return pr_ci_waiter.PRState(head_sha=value['headRefOid'], open=value['state'] == 'OPEN', draft=value['isDraft'])


def _checks(repo: str, head: str, evidence: list, directory: Path, observations: list) -> list:
    # Fetch all attempts, bounded to one complete page; never collapse duplicates.
    def observe(name: str, value: dict) -> None:
        path = directory / (name + '.json')
        _write(path, value)
        observations.append(dict(name=name, path=str(path), digest=_hash(_canonical(value))))
    raw = _api(f'repos/{repo}/commits/{head}/check-runs?per_page=100&filter=all')
    observe('check-runs', raw)
    statuses = _api(f'repos/{repo}/commits/{head}/status?per_page=100')
    observe('statuses', statuses)
    _check(type(raw['total_count']) is int and type(raw['check_runs']) is list
           and 0 <= raw['total_count'] == len(raw['check_runs']) <= 100
           and type(statuses['total_count']) is int and type(statuses['statuses']) is list
           and 0 <= statuses['total_count'] == len(statuses['statuses']) <= 100)
    _check(not any(v['context'] in CHECKS for v in statuses['statuses']))
    selected, runs = [], {}
    for name in CHECKS:
        candidates = [v for v in raw['check_runs'] if v['name'] == name]
        if len(candidates) != 1:
            raise ValueError(f'required check {name}: candidates={len(candidates)}; original snapshot retained')
        c = candidates[0]
        if c['status'] != 'completed' or c['conclusion'] != 'success':
            raise ValueError(f"required check {name}: id={c['id']} status={c['status']} conclusion={c['conclusion']}; original snapshot retained")
        _check(type(c['id']) is int and c['id'] > 0 and c['head_sha'] == head
               and c['status'] == 'completed' and c['conclusion'] == 'success')
        match = re.fullmatch(r'https://github\.com/' + re.escape(repo) + r'/actions/runs/([1-9][0-9]*)/job/([1-9][0-9]*)', c['details_url'])
        _check(match is not None and int(match[2]) == c['id'])
        run_id = int(match[1])
        if run_id not in runs:
            runs[run_id] = _api(f'repos/{repo}/actions/runs/{run_id}')
            observe('run-' + str(run_id), runs[run_id])
        run = runs[run_id]
        _check(type(run['id']) is int and run['id'] == run_id and run['repository']['full_name'] == repo
               and run['head_sha'] == head and run['check_suite_id'] == c['check_suite']['id']
               and run['status'] == 'completed' and run['conclusion'] == 'success')
        record = CodingCheckEvidence.model_validate(dict(name=name, check_id=c['id'], run_id=run_id,
            head=head, status=c['status'], conclusion=c['conclusion'], started_at=c['started_at'],
            completed_at=c['completed_at'], observed_at=_now())).model_dump()
        evidence.append(record)
        selected.append(pr_ci_waiter.CheckState(name=name, status='completed', conclusion='success'))
    return selected


def _selected(client: httpx.Client, org: str, sid: str, command: dict) -> dict:
    response = client.get(f"/api/v1/orgs/{org}/workflows/coding/runs/{command['instance_id']}/final-manager",
                          params={'session_id': sid})
    _check(response.status_code == 200 and len(response.content) <= 262144)
    result = CodingFinalSelection.model_validate(_remote_json(response.content, 262144)).model_dump()
    for key in ('instance_id', 'attempt_id', 'task_id', 'session_id', 'principal', 'revision', 'subject', 'upstream_results'):
        _check(result[key] == command[key])
    # Reparse the original selected full callback summaries locally. No recall fallback.
    for index, stage in enumerate(('development', 'code_review', 'qa')):
        original = result['results'][index]
        pin = result['upstream_results'][stage]
        _check(original['identity'] == pin and _hash(original['summary'].encode('utf8')) == pin['output_digest'])
        from runtime.workflows.coding_start_records import CodingStageOutput
        output = CodingStageOutput.model_validate(_json(original['summary'].encode('utf8'), 65536)).model_dump()
        _check((output['instance_id'], output['attempt_id'], output['revision'], output['stage'], output['subject'], output['outcome']) ==
               (command['instance_id'], pin['attempt_id'], command['revision'], stage, command['subject'], pin['outcome'])
               and original['verdict'] == (None if stage == 'development' else pin['outcome']))
    return result


def _execute(client: httpx.Client, org: str, sid: str, command: dict) -> dict:
    selected = _selected(client, org, sid, command)
    repo, number = github_subject(command['subject'])
    _local_subject(command, repo, number)
    # Exactly one task-owned attempt directory, independent of caller operation key.
    directory = Path.cwd() / 'output' / command['task_id'] / ('coding-finalize-' + _hash(command['attempt_id'].encode()))
    directory.parent.mkdir(parents=True, exist_ok=True)
    directory.mkdir()  # Existing/partial intent refuses; no automatic replay or second action.
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
    intent_body = dict(command=command, created_at=_now(), selected_digest=_hash(_canonical(selected)))
    _write(directory / 'selected.json', selected)
    _write(directory / 'checks.json', dict(checks=checks, observations=observations, ci_verdict=ci, diagnostic=error))
    _write(directory / 'intent.json', intent_body)
    evidence = dict(intent=dict(**intent_body, path=str(directory / 'intent.json'), digest=_hash(_canonical(intent_body))),
        checks=checks, observations=observations, revalidated_at=None, guard_verdict=ci, command=None, remote=None, observation_diagnostic=error)
    # Keep exact original command/diagnostic in a separate receipt even on failure.
    def perform(method: str) -> pr_ci_merge.MergeResult:
        _check(method == 'merge' and _selected(client, org, sid, command) == selected)
        evidence['revalidated_at'] = _now()
        argv = ['gh', 'pr', 'merge', str(number), '--repo', repo, '--merge', '--match-head-commit', command['subject']['head']]
        receipt = dict(argv=argv, started_at=_now(), finished_at='', exit_code=None, succeeded=False, diagnostic='')
        _write(directory / 'action-started.json', dict(receipt=receipt, revalidated_at=evidence['revalidated_at']))
        evidence['command'] = receipt
        try:
            remote = pr_ci_merge._gh_perform_merge(repo, number, method, expected_head_sha=command['subject']['head'])
            evidence['remote'] = dict(repository=repo, number=number, url=f'https://github.com/{repo}/pull/{number}',
                head=command['subject']['head'], state='MERGED', merge_commit=remote.merged_sha,
                merged_at=remote.merged_at, observed_at=_now(), source='action_adapter')
            receipt.update(exit_code=0, succeeded=True)
            return remote
        except Exception as exc:
            # Original adapter diagnostic retains failed command vs possible effect.
            receipt['diagnostic'] = str(exc)
            match = re.search(r'gh pr merge failed \(exit (-?[0-9]+)\)', str(exc))
            receipt['exit_code'] = int(match[1]) if match else (0 if 'gh pr merge succeeded but remote confirmation failed;' in str(exc) else None)
            raise
        finally:
            receipt['finished_at'] = _now()
            _write(directory / 'action-finished.json', receipt)
    guard = pr_ci_merge.guarded_merge(repo=repo, pr_number=number, pinned_head_sha=command['subject']['head'],
        merge_method='merge', ci_verdict=ci, fetch_pr_state=lambda: _pr(repo, number, command['subject']),
        fetch_mergeable=lambda: pr_ci_merge._gh_fetch_mergeable(repo, number),
        fetch_review_verdict=lambda: selected['upstream_results']['code_review']['outcome'],
        fetch_qa_verdict=lambda: selected['upstream_results']['qa']['outcome'], perform_merge=perform)
    evidence['guard_verdict'] = guard.verdict
    if guard.error_detail:
        evidence['observation_diagnostic'] = guard.error_detail
    # Failed action remains uncertain even when this separate read finds an effect.
    if evidence['command'] is not None and not evidence['command']['succeeded']:
        try:
            remote = pr_ci_merge._gh_fetch_merge_result(repo, number, command['subject']['head'])
            evidence['remote'] = dict(repository=repo, number=number, url=f'https://github.com/{repo}/pull/{number}',
                head=command['subject']['head'], state='MERGED', merge_commit=remote.merged_sha,
                merged_at=remote.merged_at, observed_at=_now(), source='separate_read')
        except Exception as exc:
            evidence['observation_diagnostic'] = str(exc)
    outcome = ('merged' if guard.verdict == 'merged' and evidence['command'] is not None
               and evidence['command']['succeeded'] else 'uncertain' if evidence['command'] is not None else 'held')
    findings = ('Performed expected-head guarded action and observed exact remote merge in this invocation.' if outcome == 'merged'
                else 'Finalization retained ' + outcome + ': ' + (guard.error_detail or error or guard.verdict))
    output = CodingFinalOutput.model_validate(dict(format='workflow-coding-final-output@1',
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
