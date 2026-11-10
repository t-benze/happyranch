"""Unmounted task-session-only repository preflight. No writes or progression.

Only the selected live code-change invocation may observe its own configured
clone. The receipt is an observation, never edit/publication permission. U4b
must bind it durably before any producer consumes it; U7 owns public transport.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import subprocess
import time
from typing import Any

from runtime.workflows.repository_artifact_objects import (
    _Budget, _DIR_FLAGS, _config, _directory, _git_environment, _regular, _same,
)
from runtime.workflows.repository_artifacts import WorkflowRepositoryError, _oid
from runtime.workflows.sequential_definition import parse_start
from runtime.workflows.sequential_dispatch import _cancelled, _discover_result, _native_task
from runtime.workflows.sequential_engine import SequentialRefusal, _bound_agents
from runtime.workflows.sequential_history import _ReadBudget, _WIRE_TOKENS, _bytes, _parse, _sql_budget
from runtime.workflows.sequential_repository_records import (
    RepositoryChangeSubject, RepositoryPreflightCause, RepositoryPreflightReceipt,
    RepositoryPreflightRequest, read_repository_preflight,
)


class _ObservationHeld(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _git(repository: Path, args: tuple[str, ...], deadline: float,
         refusal: str = 'repository_unavailable') -> str:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise _ObservationHeld('repository_unavailable')
    # Only two fixed metadata/commit-existence commands reach this function.
    # No fetch, source content, hooks, shell, credentials or arbitrary ref input.
    result = subprocess.run(['/usr/bin/git', '--no-replace-objects',
        '--git-dir=' + str(repository / '.git'), '--work-tree=' + str(repository), *args],
        env=_git_environment(), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, timeout=remaining, check=False)
    if result.returncode != 0:
        raise _ObservationHeld(refusal)
    if not 0 < len(result.stdout) <= 128:
        raise _ObservationHeld('repository_unavailable')
    return result.stdout.decode('ascii', errors='strict').rstrip('\n')


def _observe(org_root: Path, org_slug: str, principal: str, origin: str | None,
             request: RepositoryPreflightRequest) -> RepositoryChangeSubject:
    if origin is None or not 0 < len(origin.encode('utf8')) <= 4096:
        raise _ObservationHeld('unsupported_repository_scope')
    budget = _Budget()
    repository = org_root / 'workspaces' / principal / 'repos' / request.repo_key
    root = os.open(org_root, _DIR_FLAGS)
    try:
        with (_directory(root, 'workspaces', budget) as workspaces,
              _directory(workspaces, principal, budget) as workspace,
              _directory(workspace, 'repos', budget) as repos,
              _directory(repos, request.repo_key, budget) as repo,
              _directory(repo, '.git', budget) as git):
            before_repo, before_git = os.fstat(repo), os.fstat(git)
            raw = _regular(git, 'config', budget, 65536)
            try:
                observed_origin = _config(raw, 'sha1')
            except WorkflowRepositoryError:
                # Recognize a valid SHA256 config only to give the F8 cause.
                _config(raw, 'sha256')
                raise _ObservationHeld('unsupported_repository_format') from None
            if observed_origin != origin:
                raise _ObservationHeld('repository_changed')
            if _git(repository, ('rev-parse', '--show-object-format=storage'), budget.deadline) != 'sha1':
                raise _ObservationHeld('unsupported_repository_format')
            try:
                _oid(request.base, 'sha1')
                origin_digest = hashlib.sha256(observed_origin.encode('utf8')).hexdigest()
                subject = RepositoryChangeSubject(repository_id='workflow-repository:' + hashlib.sha256(
                    _bytes([org_slug, request.repo_key, origin_digest])).hexdigest(),
                    repo_key=request.repo_key, origin_digest=origin_digest, object_format='sha1',
                    base=request.base, scope=request.scope)
            except ValueError:
                raise _ObservationHeld('unsupported_repository_scope') from None
            if _git(repository, ('rev-parse', '--verify', request.base + '^{commit}'), budget.deadline,
                    'unsupported_repository_scope') != request.base:
                raise _ObservationHeld('unsupported_repository_scope')
            budget.check()
            if (_regular(git, 'config', budget, 65536) != raw
                    or not _same(before_repo, os.fstat(repo)) or not _same(before_git, os.fstat(git))
                    or not _same(before_repo, repository.stat(follow_symlinks=False))
                    or not _same(before_git, (repository / '.git').stat(follow_symlinks=False))):
                raise _ObservationHeld('repository_changed')
            return subject
    finally:
        os.close(root)


class SequentialRepositoryPreflight:
    """Internal worker-thread service; intentionally no OrgState mount/caller."""

    def __init__(self, org: Any) -> None:
        self.org = org

    def _selected(self, task_id: str, principal: str, session_id: str,
                  request: RepositoryPreflightRequest) -> tuple[dict, str | None]:
        dispatcher = self.org.workflow_sequential_dispatch
        capture = self.org.workflow_authority.capture_admission()
        budget = _ReadBudget()
        # Account for the authority decoder below and admission's decoder.
        for _ in range(2):
            budget.reserve(48 * len(capture.ready.snapshot_bytes) + 512)
            for _ in _WIRE_TOKENS.finditer(capture.ready.snapshot_bytes):
                budget.step()

        async def select() -> tuple[dict, str | None]:
            async with self.org.workflow_authority._async_writer_lock:
                async with self.org.teams_lock:
                    async with self.org.db_lock:
                        with self.org.workflow_authority.admission_writer(capture) as conn:
                            with _sql_budget(conn, budget):
                                run, visit, compiled = dispatcher._entry(conn, task_id, budget=budget)
                                task = _native_task(conn, task_id)
                                store = getattr(self.org.db, '_task_pause_store', None)
                                live = [] if store is None else [inv for inv in store._live.values()
                                    if inv.owner == 'sequential' and inv.workflow_dispatcher is dispatcher
                                    and (inv.task.id, inv.agent, inv.session_id) == (task_id, principal, session_id)]
                                if (len(live) != 1 or live[0].orch is not self.org.orchestrator
                                        or live[0].orch._paths.root != self.org.root
                                        or run['state'] != 'active' or run['active_visit_id'] != visit['id']
                                        or visit['selected'] != 1 or visit['state'] != 'running'
                                        or (visit['principal'], visit['session_id']) != (principal, session_id)
                                        or task['status'] != 'in_progress' or visit['result_id'] is not None
                                        or _discover_result(conn, visit) is not None
                                        or _cancelled(conn, run, visit)):
                                    raise SequentialRefusal('sequential_repository_session_unavailable')
                                self.org.db._check_team_move_task_uncommitted(task_id)
                                start = _parse(run['start_bytes'], parse_start, budget)
                                bound = _bound_agents(compiled, start, capture)
                                node = next(node for node in compiled.definition.nodes if node.id == visit['node_id'])
                                if (node.output_contract.type != 'code-change@1'
                                        or bound[node.role]['name'] != principal):
                                    raise SequentialRefusal('sequential_repository_session_unavailable')
                                origin = bound[node.role]['repos'].get(request.repo_key)
                                budget.work(len(bound) + len(live))
                                context = dict(org_slug=self.org.slug, run_id=run['id'], visit_id=visit['id'],
                                    task_id=task_id, principal=principal, session_id=session_id,
                                    definition_digest=compiled.pin.definition_digest, start_digest=run['start_digest'],
                                    authority_generation=capture.ready.generation,
                                    authority_digest=capture.ready.snapshot_digest)
                                budget.check()
                                return context, origin
        return dispatcher._on_loop(select())

    def resolve(self, *, task_id: str, principal: str, session_id: str,
                request: RepositoryPreflightRequest) -> bytes:
        """Authenticate before probing, revalidate before returning private metadata.

        Scope is the selected task's declaration, checked for supported syntax;
        current role eligibility/configuration supplies no effective-access proof.
        No file/network await or Git command occurs inside admission ownership.
        """
        context, origin = self._selected(task_id, principal, session_id, request)
        subject, cause = None, None
        try:
            subject = _observe(self.org.root, self.org.slug, principal, origin, request)
        except _ObservationHeld as exc:
            cause = exc.code
        except (OSError, UnicodeError, subprocess.SubprocessError, WorkflowRepositoryError):
            cause = 'repository_unavailable'
        current, current_origin = self._selected(task_id, principal, session_id, request)
        if current != context or current_origin != origin:
            raise SequentialRefusal('sequential_repository_authority_changed')
        receipt = RepositoryPreflightReceipt(format='workflow-sequential-repository-preflight@1',
            **context, request_digest=hashlib.sha256(_bytes(request.model_dump())).hexdigest(),
            disposition='resolved' if subject is not None else 'Held', subject=subject,
            cause=None if cause is None else RepositoryPreflightCause(code=cause,
                next_owner='accountable_manager', action='resolve_cause_before_editing_no_automatic_retry'))
        raw = _bytes(receipt.model_dump())
        read_repository_preflight(raw)
        return raw
