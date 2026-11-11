"""Internal U5b2a local records. Parsing establishes neither authority nor effect.

These are not public commands, callback outputs or historical Coding families.
Only a fresh owned live selection can supply local Final work; saved bytes never
authorize replay. Remote observation and original command disposition are separate.
"""
from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import PurePosixPath
from typing import Annotated, Literal, Self, TypeVar

from pydantic import Field, StrictBool, StrictInt, model_validator

from runtime.workflows.repository_artifacts import Component
from runtime.workflows.sequential_coding_output import CodeChangeDeclaration
from runtime.workflows.sequential_coding_policy import REQUIRED_LINTS
from runtime.workflows.sequential_contracts import Closed, Digest, OutputRef, Positive, Token
from runtime.workflows.sequential_definition import _json
from runtime.workflows.sequential_history import _bytes

RECORD_LIMIT = 65536
OID = Annotated[str, Field(pattern=r'^[0-9a-f]{40}$')]
Stamp = Annotated[str, Field(min_length=1, max_length=64)]
LocalPath = Annotated[str, Field(min_length=1, max_length=4096)]


def _stamp(value: str) -> datetime:
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError('sequential_final_time_unverified')
    return result


def record_digest(value: Closed) -> str:
    return hashlib.sha256(_bytes(value.model_dump(mode='json'))).hexdigest()


class FinalResultPin(Closed):
    ref: OutputRef
    principal: Component
    carrier_digest: Digest
    terminal_digest: Digest
    outer_digest: Digest


class FinalNativePin(Closed):
    root_task_id: Token
    invocation_id: Token
    request_id: Token
    handle_id: Token
    backend: Literal['linux-systemd-cgroup-v2', 'macos-process-group']
    pause_generation: Annotated[StrictInt, Field(ge=0)]
    org_root: LocalPath
    workspace_root: LocalPath

    @model_validator(mode='after')
    def literal_paths(self) -> Self:
        for value in (self.org_root, self.workspace_root):
            path = PurePosixPath(value)
            if not path.is_absolute() or '..' in path.parts or str(path) != value:
                raise ValueError('sequential_final_workspace_unverified')
        return self


class CodingFinalSelection(Closed):
    format: Literal['workflow-sequential-coding-final-selection@1']
    org_slug: Annotated[str, Field(pattern=r'^[a-z][a-z0-9-]{0,62}$')]
    run_id: Token
    visit_id: Token
    task_id: Annotated[str, Field(pattern=r'^[A-Za-z0-9_-]{1,128}$')]
    principal: Component
    session_id: Token
    definition_digest: Digest
    start_digest: Digest
    cas: Annotated[StrictInt, Field(ge=0)]
    last_event_digest: Digest
    input_selection_digest: Digest
    authority_generation: Positive
    authority_digest: Digest
    change: CodeChangeDeclaration
    contributors: Annotated[tuple[Component, ...], Field(min_length=1, max_length=64)]
    results: Annotated[tuple[FinalResultPin, ...], Field(min_length=3, max_length=3)]
    native: FinalNativePin

    @model_validator(mode='after')
    def consistent(self) -> Self:
        if (tuple(pin.ref.contract for pin in self.results) !=
                ('code-change@1', 'independent-code-review@1', 'code-qa@1')
                or any(pin.ref.epoch != self.change.epoch for pin in self.results)
                or tuple(sorted(set(self.contributors))) != self.contributors
                or self.results[0].principal not in self.contributors
                or len({self.principal, *(pin.principal for pin in self.results)}) != 4
                or set(self.contributors).intersection((self.principal, self.results[1].principal, self.results[2].principal))
                or self.native.workspace_root != str(PurePosixPath(self.native.org_root) / 'workspaces' / self.principal)):
            raise ValueError('sequential_final_selection_conflict')
        return self


class FinalLintObservation(Closed):
    name: Literal['Source Boundary Lint', 'Web Lint (Node 24)']
    repository: Annotated[str, Field(min_length=3, max_length=256)]
    head: OID
    check_id: Positive
    run_id: Positive
    status: Literal['completed']
    conclusion: Literal['success']
    started_at: Stamp
    completed_at: Stamp
    observed_at: Stamp

    @model_validator(mode='after')
    def ordered_times(self) -> Self:
        if not _stamp(self.started_at) <= _stamp(self.completed_at) <= _stamp(self.observed_at):
            raise ValueError('sequential_final_lint_stale')
        return self


class CodingFinalIntent(Closed):
    format: Literal['workflow-sequential-coding-final-intent@1']
    operation_key: Token
    selected: CodingFinalSelection
    checks: Annotated[tuple[FinalLintObservation, ...], Field(min_length=2, max_length=2)]
    created_at: Stamp

    @model_validator(mode='after')
    def exact_checks(self) -> Self:
        if (tuple(check.name for check in self.checks) != REQUIRED_LINTS
                or len({check.check_id for check in self.checks}) != 2
                or any((check.repository, check.head) != (self.selected.change.pr.repository, self.selected.change.head)
                       or _stamp(check.observed_at) > _stamp(self.created_at) for check in self.checks)):
            raise ValueError('sequential_final_lints_unverified')
        return self


class CodingFinalCommandResult(Closed):
    format: Literal['workflow-sequential-coding-final-command-result@1']
    intent_digest: Digest
    selection_digest: Digest
    started_at: Stamp
    finished_at: Stamp
    exit_code: StrictInt | None
    succeeded: StrictBool
    diagnostic: Annotated[str, Field(max_length=4096)]

    @model_validator(mode='after')
    def original_disposition(self) -> Self:
        if (_stamp(self.started_at) > _stamp(self.finished_at)
                or (self.succeeded and self.exit_code != 0)):
            raise ValueError('sequential_final_command_unverified')
        return self


class CodingFinalRemoteObservation(Closed):
    format: Literal['workflow-sequential-coding-final-remote-observation@1']
    selection_digest: Digest
    command_digest: Digest
    repository: Annotated[str, Field(min_length=3, max_length=256)]
    number: Positive
    url: Annotated[str, Field(max_length=512)]
    head: OID
    state: Literal['MERGED']
    merge_commit: OID
    merged_at: Stamp
    observed_at: Stamp
    source: Literal['action_adapter', 'separate_read']

    @model_validator(mode='after')
    def remote_identity(self) -> Self:
        if (self.url != f'https://github.com/{self.repository}/pull/{self.number}'
                or _stamp(self.merged_at) > _stamp(self.observed_at)):
            raise ValueError('sequential_final_remote_unverified')
        return self


_Record = TypeVar('_Record', bound=Closed)


def _read(raw: bytes, model: type[_Record]) -> _Record:
    _json(raw, RECORD_LIMIT)
    result = model.model_validate_json(raw, strict=True)
    if _bytes(result.model_dump(mode='json')) != raw:
        raise ValueError('sequential_final_record_not_canonical')
    return result


def read_final_selection(raw: bytes) -> CodingFinalSelection:
    return _read(raw, CodingFinalSelection)


def read_final_intent(raw: bytes) -> CodingFinalIntent:
    return _read(raw, CodingFinalIntent)


def read_final_command(raw: bytes, intent: CodingFinalIntent) -> CodingFinalCommandResult:
    result = _read(raw, CodingFinalCommandResult)
    if (result.intent_digest != record_digest(intent)
            or result.selection_digest != record_digest(intent.selected)
            or _stamp(result.started_at) < _stamp(intent.created_at)):
        raise ValueError('sequential_final_command_conflict')
    return result


def read_final_observation(raw: bytes, selected: CodingFinalSelection,
                           command: CodingFinalCommandResult) -> CodingFinalRemoteObservation:
    result = _read(raw, CodingFinalRemoteObservation)
    pr = selected.change.pr
    if (result.selection_digest != record_digest(selected) or command.selection_digest != record_digest(selected)
            or result.command_digest != record_digest(command)
            or (result.repository, result.number, result.url, result.head) !=
               (pr.repository, pr.number, pr.url, selected.change.head)
            or _stamp(result.observed_at) < _stamp(command.finished_at)
            or (not command.succeeded and result.source != 'separate_read')):
        raise ValueError('sequential_final_observation_conflict')
    return result


def final_disposition(command: CodingFinalCommandResult,
                      observation: CodingFinalRemoteObservation | None) -> Literal['merged', 'uncertain']:
    """A later MERGED read never rewrites failed/lost original command evidence.

    This local reduction is not a sequential witness or native settlement proof.
    """
    if (observation is not None and command.succeeded
            and observation.command_digest == record_digest(command)
            and observation.selection_digest == command.selection_digest):
        return 'merged'
    return 'uncertain'
