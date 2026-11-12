"""Closed transport declaration; repository service owns observation and replay."""
from __future__ import annotations

from typing import Literal

from runtime.workflows.sequential_definition import _json
from runtime.workflows.sequential_history import _bytes
from runtime.workflows.sequential_repository_records import RepositoryPreflightRequest


class RepositoryCommand(RepositoryPreflightRequest):
    format: Literal['workflow-sequential-repository-command@1']


def read_repository_command(raw: bytes) -> RepositoryPreflightRequest:
    _json(raw, 49152)
    command = RepositoryCommand.model_validate_json(raw, strict=True)
    if _bytes(command.model_dump(mode='json')) != raw:
        raise ValueError('sequential_repository_command_noncanonical')
    return RepositoryPreflightRequest(repo_key=command.repo_key, base=command.base, scope=command.scope)
