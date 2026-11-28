"""Shared new-process delivery; standalone jobs intentionally do not call it."""
from __future__ import annotations

import os
from pathlib import Path

from runtime.org_secrets.names import SecretError, validate_name
from runtime.org_secrets.store import store_for
from runtime.platform.session_backend import PrelaunchRefused


class EnvironmentBudgetRefused(SecretError, PrelaunchRefused):
    """Closed final environment admission, before any process launch."""


def session_owner():
    from runtime.orchestrator.thread_move import current_thread_preparation
    from runtime.orchestrator.producer_move import current_producer_preparation
    return current_thread_preparation() or current_producer_preparation()


def session_values(org_slug: str | None, workspace: Path | None, *, fresh: bool = False) -> dict[str, str]:
    if org_slug is None:
        return {}
    # OrgPaths.workspaces_dir is the common built-in/custom workspace contract.
    # Refuse an ambiguous association rather than use a machine-global slug map.
    if workspace is None or workspace.parent.name != 'workspaces' or workspace.parent.parent.name != org_slug:
        raise SecretError('secret_org_context_unavailable', 503)
    owner = session_owner()
    snapshot = getattr(owner, 'secret_snapshot', None)
    if not fresh and snapshot is not None and snapshot[:2] == (org_slug, workspace):
        return snapshot[2]
    values = store_for(workspace.parent.parent).current_values()
    # Independent delivery guard: also reject legacy/adapter-provided records
    # if disk validation is bypassed or a name becomes reserved after storage.
    for name in values:
        validate_name(name)
    return values


def names_note(org_slug: str | None, workspace: Path | None, *, values: dict[str, str] | None = None) -> str:
    names = sorted(session_values(org_slug, workspace) if values is None else values)
    return ('Org secrets available as environment variables (names only): '
            + ', '.join(names) + '. Never print, echo, log or commit their values.\n\n') if names else ''


def check_environment_budget(env: dict[str, str]) -> None:
    # Reserve half ARG_MAX for argv, pointers and platform wrapper overhead.
    # Store acceptance has an independent 128 KiB aggregate bound.
    limit = min(int(os.sysconf('SC_ARG_MAX')) // 2, 1024 * 1024)
    size = sum(len(os.fsencode(k)) + len(os.fsencode(v)) + 2 + 8 for k, v in env.items())
    if size > limit:
        raise EnvironmentBudgetRefused('environment_budget_exceeded', 413)


def admit_session_values(org_slug: str | None, workspace: Path | None, owner=None, *, selflaunch: bool = False) -> dict[str, str]:
    """Select once after capacity waits, before native launch commitment."""
    owner = owner if owner is not None else session_owner()
    try:
        values = session_values(org_slug, workspace, fresh=True)
    except SecretError:
        if owner is None:
            raise
        if selflaunch:
            owner.defer_selflaunch()  # Exact caller has not invoked Popen.
        from runtime.orchestrator.team_move import DeferredTeamMove
        raise DeferredTeamMove('org_secrets_locked') from None
    if owner is not None:
        owner.secret_snapshot = (org_slug, workspace, values)
    return values


def refresh_session_spec(spec, owner):
    """Refresh retained contained specs without changing native admission gates."""
    from dataclasses import replace
    from runtime.orchestrator.executors import _callee_env
    if getattr(owner.state, 'kind', None) == 'job':
        return spec
    if spec is None or spec.env is None or not spec.env.get('HAPPYRANCH_ORG_SLUG'):
        return spec  # Jobs and no-org legacy specs have no org-store injection.
    org_slug, workspace = spec.env['HAPPYRANCH_ORG_SLUG'], Path(spec.cwd)
    values = admit_session_values(org_slug, workspace, owner)
    return replace(spec, env=_callee_env(org_slug=org_slug, workspace=workspace,
        session_id=spec.env.get('HAPPYRANCH_RUNTIME_SESSION_ID'), secret_values=values))
