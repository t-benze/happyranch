"""Shared LOCAL Final observation/action core. Never a daemon effect owner.

Historical adapters retain their own commands, selections and output families.
The caller owns live selection/subject checks and before-effect durable intent.
No retries, admin fallback, access observer or native settlement is supplied.
"""
from __future__ import annotations

import json
import os
import re
import stat
import subprocess
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from runtime.daemon import pr_ci_merge, pr_ci_waiter
from runtime.workflows.coding_saved_records import _canonical, _check, _hash, _pairs, _constant
from runtime.workflows.coding_start_records import CodingCheckEvidence

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


def _literal_directory(path: Path, *, create: bool = False) -> Path:
    """Literal no-follow observations, not a racing-rename or OS security fence."""
    _check(path.is_absolute() and '..' not in path.parts)
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        if create:
            try:
                current.mkdir()
            except FileExistsError:
                pass
        _check(stat.S_ISDIR(current.lstat().st_mode))  # symlinks, including parents, refuse
    _check(path.resolve(strict=True) == path)
    return path


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


def run_guarded_action(*, repo: str, number: int, head: str, ci: str, directory: Path,
                       evidence: dict, revalidate: Callable[[], None],
                       fetch_pr_state: Callable[[], pr_ci_waiter.PRState],
                       fetch_review_verdict: Callable[[], str],
                       fetch_qa_verdict: Callable[[], str]) -> pr_ci_merge.GuardedMergeVerdict:
    """One guarded local command with original receipt and separate remote read.

    Revalidation runs inside the sole perform callback, after all guard reads.
    A failed/lost command keeps its disposition even after a later MERGED read.
    Existing historical receipt shape and error interpretation are preserved.
    """
    # Keep exact original command/diagnostic in a separate receipt even on failure.
    def perform(method: str) -> pr_ci_merge.MergeResult:
        _check(method == 'merge')
        revalidate()
        evidence['revalidated_at'] = _now()
        argv = ['gh', 'pr', 'merge', str(number), '--repo', repo, '--merge', '--match-head-commit', head]
        receipt = dict(argv=argv, started_at=_now(), finished_at='', exit_code=None, succeeded=False, diagnostic='')
        _write(directory / 'action-started.json', dict(receipt=receipt, revalidated_at=evidence['revalidated_at']))
        evidence['command'] = receipt
        try:
            remote = pr_ci_merge._gh_perform_merge(repo, number, method, expected_head_sha=head)
            evidence['remote'] = dict(repository=repo, number=number, url=f'https://github.com/{repo}/pull/{number}',
                head=head, state='MERGED', merge_commit=remote.merged_sha,
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
    guard = pr_ci_merge.guarded_merge(repo=repo, pr_number=number, pinned_head_sha=head,
        merge_method='merge', ci_verdict=ci, fetch_pr_state=fetch_pr_state,
        fetch_mergeable=lambda: pr_ci_merge._gh_fetch_mergeable(repo, number),
        fetch_review_verdict=fetch_review_verdict,
        fetch_qa_verdict=fetch_qa_verdict, perform_merge=perform)
    evidence['guard_verdict'] = guard.verdict
    if guard.error_detail:
        evidence['observation_diagnostic'] = guard.error_detail
    # Failed action remains uncertain even when this separate read finds an effect.
    if evidence['command'] is not None and not evidence['command']['succeeded']:
        try:
            remote = pr_ci_merge._gh_fetch_merge_result(repo, number, head)
            evidence['remote'] = dict(repository=repo, number=number, url=f'https://github.com/{repo}/pull/{number}',
                head=head, state='MERGED', merge_commit=remote.merged_sha,
                merged_at=remote.merged_at, observed_at=_now(), source='separate_read')
        except Exception as exc:
            evidence['observation_diagnostic'] = str(exc)
    return guard
