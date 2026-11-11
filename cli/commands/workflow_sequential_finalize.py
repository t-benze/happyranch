"""Unmounted U5b2b1 LOCAL Final adapter, not a command or callback producer.

Only a future genuine selected-manager transport may call this in its live
session. Saved records never authorize invocation/replay. U5b2b2 owns callback
validation/mount; U6/U7 remain required. No daemon PR I/O or merge executor.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from cli.commands import workflow_final_local as local
from cli.commands.workflow_sequential_final_journal import _current, _sync_directory, reserve_attempt
from runtime.daemon import pr_ci_merge, pr_ci_waiter
from runtime.workflows.coding_saved_records import _check, _hash
from runtime.workflows.coding_start_records import github_origin
from runtime.workflows.sequential_coding_final import SequentialCodingFinalSelection
from runtime.workflows.sequential_coding_final_records import (
    CodingFinalCommandResult, CodingFinalIntent, CodingFinalRemoteObservation, CodingFinalSelection,
    FinalLintObservation, final_disposition, read_final_command, read_final_intent,
    read_final_observation, record_digest,
)
from runtime.workflows.sequential_history import _bytes


@dataclass(frozen=True)
class LocalFinalObservation:
    """Local diagnostics only: never an accepted output, witness or callback."""
    disposition: str
    diagnostic: str
    directory: Path
    intent: CodingFinalIntent | None = None
    command: CodingFinalCommandResult | None = None
    remote: CodingFinalRemoteObservation | None = None


def _local_subject(selected: CodingFinalSelection, repository_path: Path) -> tuple[str, int, str]:
    workspace = local._literal_directory(Path(selected.native.workspace_root))
    checkout = local._literal_directory(workspace / 'repos' / selected.change.subject.repo_key)
    root = local._literal_directory(repository_path)
    _check(root == checkout or root.is_relative_to(checkout / '.claude' / 'worktrees'))
    cwd = local._literal_directory(Path.cwd())
    _check(cwd == workspace or cwd == checkout or cwd.is_relative_to(checkout / '.claude' / 'worktrees'))
    def git(*args: str) -> str:
        return local._run(['git', *args], cwd=root).strip()
    _check(git('rev-parse', '--show-object-format') == 'sha1'
           and git('rev-parse', '--show-toplevel') == str(root)
           and not (root / '.git').is_symlink())
    common = local._literal_directory(checkout / '.git')
    _check(git('rev-parse', '--path-format=absolute', '--git-common-dir') == str(common))
    origin = local._run(['git', 'config', '--get-all', 'remote.origin.url'], cwd=root).removesuffix('\n')
    _check(_hash(origin.encode('utf8')) == selected.change.subject.origin_digest
           and local._run(['git', 'remote', 'get-url', '--all', 'origin'], cwd=root).removesuffix('\n') == origin)
    repo = github_origin(origin)
    _check(repo == selected.change.pr.repository)
    for oid in (selected.change.subject.base, selected.change.head):
        _check(git('rev-parse', '--verify', oid + '^{commit}') == oid)
    _check(git('rev-parse', '--verify', 'HEAD') == selected.change.head)
    return repo, selected.change.pr.number, origin


def _pr(selected: CodingFinalSelection) -> pr_ci_waiter.PRState:
    pr = selected.change.pr
    value = local._remote_json(local._run(['gh', 'pr', 'view', str(pr.number), '--repo', pr.repository,
        '--json', 'number,url,headRefOid,isDraft,state,baseRefOid']).encode('utf8'), 65536)
    _check(type(value['number']) is int and value['number'] == pr.number and value['url'] == pr.url
           and value['baseRefOid'] == selected.change.subject.base and type(value['isDraft']) is bool
           and value['state'] in ('OPEN', 'CLOSED', 'MERGED'))
    return pr_ci_waiter.PRState(head_sha=value['headRefOid'], open=value['state'] == 'OPEN', draft=value['isDraft'])


def observe_lints(selected: CodingFinalSelection, directory: Path) -> tuple[FinalLintObservation, ...]:
    """Read raw check/run/status snapshots, never a caller Boolean/CI verdict."""
    checks: list[dict] = []
    observations: list[dict] = []
    local._checks(selected.change.pr.repository, selected.change.head, checks, directory, observations)
    # The shared historical reader keeps its original interpretation. This
    # adapter additionally binds the raw combined-status response's own head.
    with (directory / 'statuses.json').open('rb') as stream:
        statuses = local._remote_json(stream.read(1048577), 1048576)
    _check(statuses['sha'] == selected.change.head)
    result = tuple(FinalLintObservation(repository=selected.change.pr.repository, **check) for check in checks)
    _check(len(result) == 2 and tuple(check.name for check in result) == local.CHECKS
           and len({check.check_id for check in result}) == 2)
    return result


def execute_selected_final(*, selection: SequentialCodingFinalSelection, task_id: str, principal: str,
                           session_id: str, change: bytes, operation_key: str,
                           repository_path: Path) -> LocalFinalObservation:
    """Unregistered local seam; caller must be the original live Final manager.

    The concrete service replays evidence + current authority/native ownership
    on every selection. It preserves denied vs unverified causes. No local
    disposition writes business Held or grants callback acceptance/advance.
    """
    def reselect() -> CodingFinalSelection:
        return selection.select(task_id=task_id, principal=principal, session_id=session_id, change=change)
    selected = reselect()
    subject = _local_subject(selected, repository_path)
    attempt = reserve_attempt(selected, reselect=reselect)
    try:
        checks = observe_lints(selected, attempt.directory)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        # Raw/partial observations and the sole reservation remain for the
        # genuine manager's causal BLOCK; no intent or action on failed lints.
        local._write(attempt.directory / 'lint-refusal.json', dict(diagnostic=str(exc)))
        _sync_directory(attempt.directory)
        return LocalFinalObservation('held', str(exc), attempt.directory)
    intent = attempt.persist_intent(operation_key=operation_key, checks=checks, created_at=local._now(), reselect=reselect)
    evidence = dict(revalidated_at=None, guard_verdict='', command=None, remote=None, observation_diagnostic='')
    def revalidate() -> None:
        _current(selected, reselect)
        _check(_local_subject(selected, repository_path) == subject)
        attempt._same()
        with (attempt.directory / 'intent.json').open('rb') as stream:
            saved = read_final_intent(stream.read(65537))
        _check(saved == intent)
        # Closing native/current-authority check follows filesystem observation.
        _current(selected, reselect)
    guard = local.run_guarded_action(repo=subject[0], number=subject[1], head=selected.change.head,
        ci='ci_pass', directory=attempt.directory, evidence=evidence, revalidate=revalidate,
        fetch_pr_state=lambda: _pr(selected), fetch_review_verdict=lambda: 'APPROVE', fetch_qa_verdict=lambda: 'PASS')
    if evidence['command'] is None:
        local._write(attempt.directory / 'guard-refusal.json', dict(verdict=guard.verdict, diagnostic=guard.error_detail))
        _sync_directory(attempt.directory)
        return LocalFinalObservation('held', guard.error_detail or guard.verdict, attempt.directory, intent)
    original = evidence['command']
    # A failed/partial receipt write cannot become local success. Keep the
    # original reservation and native error; this adapter never reissues.
    if original['succeeded'] and guard.verdict != 'merged':
        return LocalFinalObservation('uncertain', guard.error_detail or guard.verdict, attempt.directory, intent)
    with (attempt.directory / 'action-finished.json').open('rb') as stream:
        _check(local._remote_json(stream.read(1048577), 1048576) == original)
    _sync_directory(attempt.directory)
    command = CodingFinalCommandResult(format='workflow-sequential-coding-final-command-result@1',
        intent_digest=record_digest(intent), selection_digest=record_digest(selected),
        started_at=original['started_at'], finished_at=original['finished_at'], exit_code=original['exit_code'],
        succeeded=original['succeeded'], diagnostic=original['diagnostic'][:4096])
    command = read_final_command(_bytes(command.model_dump(mode='json')), intent)
    local._write(attempt.directory / 'command-result.json', command.model_dump(mode='json'))
    _sync_directory(attempt.directory)
    # A successful adapter read preceded command-finished. Read independently
    # after that receipt; never move an observation timestamp to fake ordering.
    remote = evidence['remote']
    if command.succeeded:
        try:
            observed = pr_ci_merge._gh_fetch_merge_result(subject[0], subject[1], selected.change.head)
            remote = dict(repository=subject[0], number=subject[1], url=selected.change.pr.url,
                head=selected.change.head, state='MERGED', merge_commit=observed.merged_sha,
                merged_at=observed.merged_at, observed_at=local._now(), source='separate_read')
        except Exception as exc:
            remote = None
            evidence['observation_diagnostic'] = str(exc)
    observation = None
    if remote is not None:
        observation = CodingFinalRemoteObservation(format='workflow-sequential-coding-final-remote-observation@1',
            selection_digest=record_digest(selected), command_digest=record_digest(command), **remote)
        observation = read_final_observation(_bytes(observation.model_dump(mode='json')), selected, command)
        local._write(attempt.directory / 'remote-observation.json', observation.model_dump(mode='json'))
    local._write(attempt.directory / 'observation-diagnostic.json', dict(diagnostic=evidence['observation_diagnostic']))
    _sync_directory(attempt.directory)
    return LocalFinalObservation(final_disposition(command, observation),
        evidence['observation_diagnostic'], attempt.directory, intent, command, observation)
