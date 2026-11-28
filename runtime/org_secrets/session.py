"""Shared new-process delivery; standalone jobs intentionally do not call it."""
from __future__ import annotations

import os
from pathlib import Path

from runtime.org_secrets.names import SecretError
from runtime.org_secrets.store import store_for


def session_values(org_slug: str | None, workspace: Path | None) -> dict[str, str]:
    if org_slug is None:
        return {}
    # OrgPaths.workspaces_dir is the common built-in/custom workspace contract.
    # Refuse an ambiguous association rather than use a machine-global slug map.
    if workspace is None or workspace.parent.name != 'workspaces' or workspace.parent.parent.name != org_slug:
        raise SecretError('secret_org_context_unavailable', 503)
    return store_for(workspace.parent.parent).current_values()


def names_note(org_slug: str | None, workspace: Path | None) -> str:
    names = sorted(session_values(org_slug, workspace))
    return ('Org secrets available as environment variables (names only): '
            + ', '.join(names) + '. Never print, echo, log or commit their values.\n\n') if names else ''


def check_environment_budget(env: dict[str, str]) -> None:
    # Reserve half ARG_MAX for argv, pointers and platform wrapper overhead.
    # Store acceptance has an independent 128 KiB aggregate bound.
    limit = min(int(os.sysconf('SC_ARG_MAX')) // 2, 1024 * 1024)
    size = sum(len(os.fsencode(k)) + len(os.fsencode(v)) + 2 + 8 for k, v in env.items())
    if size > limit:
        raise SecretError('environment_budget_exceeded', 413)
