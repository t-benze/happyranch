"""Closed K3a commands and agent-read pins; no repository or execution grant."""
from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Annotated, Literal
from pydantic import Field, RootModel, StrictBool, StrictInt, field_validator, model_validator

from runtime.workflows._coding_role_records import AgentBinding, Closed, MappingPin, Release, RepositoryReference, Roles
from runtime.workflows.artifacts import CodeChangeDescriptor, Token
from runtime.workflows.coding_saved_records import (
    _check, _constant, _json, _model, _oid, _operation, _pairs, _paths, _repository,
)

START_LIMIT = 2097152


class CodingWork(Closed):
    brief: str
    repository_id: str
    object_format: Literal['sha1', 'sha256']
    base: str
    paths: list[str] = Field(min_length=1, max_length=64)

    @model_validator(mode='after')
    def bounded(self) -> CodingWork:
        _check(0 < len(self.brief.encode('utf8')) <= 16384 and '\x00' not in self.brief)
        _repository(self.repository_id)
        _oid(self.base, self.object_format)
        _paths(self.paths)
        return self


class CodingStart(Closed):
    format: Literal['workflow-coding-start@1']
    operation_key: Token
    release: Release
    mapping: MappingPin
    work: CodingWork
    implementer_override: AgentBinding | None

    @model_validator(mode='after')
    def public_key(self) -> CodingStart:
        _operation(self.operation_key)
        return self


class CodingStartDecision(Closed):
    action: Literal['workflow_start']
    workflow: CodingStart


class CodingStageOutput(Closed):
    format: Literal['workflow-coding-stage-output@1']
    instance_id: Token
    attempt_id: Token
    revision: Annotated[StrictInt, Field(ge=1, le=1)]
    stage: Literal['development', 'code_review', 'qa']
    subject: CodeChangeDescriptor
    findings: str
    outcome: Literal['produced', 'APPROVE', 'REQUEST_CHANGES', 'BLOCK', 'PASS', 'REVISE']

    @model_validator(mode='after')
    def closed_stage(self) -> CodingStageOutput:
        _check(self.subject.comparison is not None)
        _check(self.findings.strip() != '' and '\x00' not in self.findings
               and 0 < len(self.findings.encode('utf8')) <= 16384)
        _check(self.outcome in {'development': ('produced',),
            'code_review': ('APPROVE', 'REQUEST_CHANGES', 'BLOCK'), 'qa': ('PASS', 'REVISE', 'BLOCK')}[self.stage])
        return self


def parse_stage_output(result: dict, *, instance: str, attempt: dict,
                       work: dict, subject: dict | None = None) -> dict:
    """Validate content against an already authenticated native result row.

    Envelope IDs and commit syntax do not prove assignment or object existence.
    Failed/blocked and unusable callbacks remain their original native evidence.
    """
    _check(type(result['id']) is int and result['id'] > 0
           and result['id'] == attempt['result_id']
           and (result['task_id'], result['session_id'], result['agent']) ==
               (attempt['task_id'], attempt['session_id'], attempt['principal'])
           and result['status'] == 'completed' and result['decision_json'] is None
           and result['waiting_on_job_ids'] is None and type(result['output_summary']) is str)
    output = _model(CodingStageOutput, _json(result['output_summary'].encode('utf8'), 65536)).model_dump()
    _check((output['instance_id'], output['attempt_id'], output['revision'], output['stage']) ==
           (instance, attempt['id'], attempt['revision'], attempt['stage']))
    descriptor = output['subject']
    _check((descriptor['repository']['id'], descriptor['object_format'], descriptor['comparison']['base']) ==
           (work['repository_id'], work['object_format'], work['base']))
    _check(result['verdict'] == (None if output['stage'] == 'development' else output['outcome']))
    if subject is not None:
        _check(descriptor == subject)
    return output


class CodingReadiness(Closed):
    enabled: Literal[False]
    reason: Literal['coding_downstream_unavailable']

    @field_validator('enabled', mode='before')
    @classmethod
    def disabled_only(cls, value: object) -> bool:
        _check(type(value) is bool and value is False)
        return value


class QueuedAttempt(Closed):
    id: Token
    task_id: Token
    stage: Literal['development']
    state: Literal['queued', 'claimed', 'running', 'uncertain', 'blocked', 'failed', 'completed']
    principal: Token


class QueuedEvent(Closed):
    id: Token
    kind: Literal['run_started', 'session_bound', 'launch_reserved', 'launch_observed', 'callback_recorded', 'terminal_recorded', 'cleanup_settled', 'held', 'host_reconciled']
    event_seq: Annotated[StrictInt, Field(ge=1, le=4096)]


class CodingInitialRunReceipt(Closed):
    format: Literal['workflow-coding-run@1']
    instance_id: Token
    release: Release
    mapping: MappingPin
    effective_roles: Roles
    cas_version: Annotated[StrictInt, Field(ge=1, le=4096)]
    current_revision: Annotated[StrictInt, Field(ge=0, le=0)]
    target_revision: Annotated[StrictInt, Field(ge=1, le=1)]
    state: Literal['development', 'held']
    current_attempt: QueuedAttempt
    history: list[QueuedEvent] = Field(min_length=1, max_length=4096)
    closeout: None
    readiness: CodingReadiness


class CodingOrderedAttempt(QueuedAttempt):
    stage: Literal['development', 'code_review']


class CodingOrderedEvent(QueuedEvent):
    kind: Literal['run_started', 'session_bound', 'launch_reserved', 'launch_observed',
        'callback_recorded', 'terminal_recorded', 'cleanup_settled', 'held', 'host_reconciled',
        'artifact_staged', 'contribution_recorded', 'stage_queued', 'advance', 'review_staged']


class CodingOrderedRunReceipt(CodingInitialRunReceipt):
    format: Literal['workflow-coding-run@2']
    current_revision: Annotated[StrictInt, Field(ge=1, le=1)]
    state: Literal['code_review', 'held']
    current_attempt: CodingOrderedAttempt
    history: list[CodingOrderedEvent] = Field(min_length=1, max_length=4096)


class CodingQAAttempt(CodingOrderedAttempt):
    stage: Literal['qa']


class CodingQARunReceipt(CodingOrderedRunReceipt):
    format: Literal['workflow-coding-run@3']
    state: Literal['qa', 'held']
    current_attempt: CodingQAAttempt


class CodingFinalAttempt(CodingOrderedAttempt):
    stage: Literal['agent_manager']


class CodingFinalEvent(CodingOrderedEvent):
    kind: Literal['run_started', 'session_bound', 'launch_reserved', 'launch_observed',
        'callback_recorded', 'terminal_recorded', 'cleanup_settled', 'held', 'host_reconciled',
        'artifact_staged', 'contribution_recorded', 'stage_queued', 'advance', 'review_staged',
        'manager_completed']


class CodingFinalCloseout(Closed):
    result_id: Annotated[StrictInt, Field(gt=0)]
    task_id: Token
    subject: CodeChangeDescriptor
    outcome: Literal['merged', 'held', 'uncertain']


class CodingFinalRunReceipt(CodingOrderedRunReceipt):
    format: Literal['workflow-coding-run@4']
    state: Literal['agent_manager', 'held', 'complete']
    current_attempt: CodingFinalAttempt
    closeout: CodingFinalCloseout | None
    history: list[CodingFinalEvent] = Field(min_length=1, max_length=4096)


class CodingRunReceipt(RootModel[Annotated[
        CodingInitialRunReceipt | CodingOrderedRunReceipt | CodingQARunReceipt | CodingFinalRunReceipt,
        Field(discriminator='format')]]):
    pass


class CodingUpstream(Closed):
    attempt_id: Token
    task_id: Token
    session_id: Token
    principal: Token
    result_id: Annotated[StrictInt, Field(gt=0)]
    outcome: Literal['produced', 'APPROVE', 'PASS']
    output_digest: str = Field(pattern=r'^[0-9a-f]{64}$')


class CodingUpstreams(Closed):
    development: CodingUpstream
    code_review: CodingUpstream
    qa: CodingUpstream

    @model_validator(mode='after')
    def ordered(self) -> CodingUpstreams:
        values = [self.development, self.code_review, self.qa]
        _check([v.outcome for v in values] == ['produced', 'APPROVE', 'PASS'])
        for key in ('task_id', 'session_id', 'principal', 'result_id', 'attempt_id'):
            _check(len({getattr(v, key) for v in values}) == 3)
        return self


class CodingFinalizeCommand(Closed):
    format: Literal['workflow-coding-finalize-command@1']
    operation_key: Token
    repository_path: str = Field(min_length=1, max_length=4096)
    instance_id: Token
    attempt_id: Token
    task_id: Token
    session_id: Token
    principal: Token
    revision: Annotated[StrictInt, Field(ge=1, le=1)]
    subject: CodeChangeDescriptor
    upstream_results: CodingUpstreams

    @model_validator(mode='after')
    def own_command(self) -> CodingFinalizeCommand:
        from pathlib import Path
        _operation(self.operation_key)
        _check(Path(self.repository_path).is_absolute() and '\x00' not in self.repository_path)
        _check(self.subject.object_format == 'sha1' and self.subject.comparison is not None
               and self.principal not in {v.principal for v in
                   (self.upstream_results.development, self.upstream_results.code_review, self.upstream_results.qa)})
        return self


class CodingSelectedResult(Closed):
    identity: CodingUpstream
    # Original callback summary, selected by INTEGER result, never task prose.
    summary: str = Field(min_length=1, max_length=65536)
    verdict: Literal['APPROVE', 'PASS'] | None


class CodingFinalSelection(Closed):
    format: Literal['workflow-coding-final-selection@1']
    instance_id: Token
    attempt_id: Token
    task_id: Token
    session_id: Token
    principal: Token
    revision: Annotated[StrictInt, Field(ge=1, le=1)]
    subject: CodeChangeDescriptor
    upstream_results: CodingUpstreams
    results: list[CodingSelectedResult] = Field(min_length=3, max_length=3)
    release: Release
    mapping: MappingPin
    scope_digest: str = Field(pattern=r'^[0-9a-f]{64}$')


class CodingFinalWorkspace(Closed):
    """Original native OrgPaths observation, never a caller-selected directory."""
    format: Literal['workflow-coding-final-workspace@1']
    org_slug: Token
    org_root: str = Field(min_length=1, max_length=4096)
    workspace_root: str = Field(min_length=1, max_length=4096)
    principal: Token
    task_id: Token
    attempt_id: Token
    invocation_id: Token
    session_id: Token

    @model_validator(mode='after')
    def original_path(self) -> CodingFinalWorkspace:
        from pathlib import Path
        for value in (self.org_root, self.workspace_root):
            path = Path(value)
            _check(path.is_absolute() and str(path) == value and '\x00' not in value
                   and '..' not in path.parts)
        _check(self.principal not in ('.', '..') and '/' not in self.principal
               and self.workspace_root == str(Path(self.org_root) / 'workspaces' / self.principal))
        return self


class CodingBoundFinalSelection(CodingFinalSelection):
    format: Literal['workflow-coding-final-selection@2']
    repository_reference: RepositoryReference
    workspace: CodingFinalWorkspace

    @model_validator(mode='after')
    def bound(self) -> CodingBoundFinalSelection:
        validate_final_identity(self.model_dump(), org=self.workspace.org_slug)
        return self


def selected_repository(mapping: dict, work: dict) -> dict:
    references = [r for r in mapping['repository_references']
        if (r['repository_id'], r['object_format']) == (work['repository_id'], work['object_format'])]
    _check(len(references) == 1)
    return RepositoryReference.model_validate(references[0]).model_dump()


def validate_final_identity(selected: dict, *, org: str) -> None:
    """Pure full-path/reference reconstruction; filesystem checks belong to the local tool."""
    reference, workspace = selected['repository_reference'], selected['workspace']
    _check(workspace['org_slug'] == org and
           (workspace['principal'], workspace['task_id'], workspace['attempt_id'], workspace['session_id']) ==
           (selected['principal'], selected['task_id'], selected['attempt_id'], selected['session_id']))
    from runtime.workflows.coding_saved_records import _canonical, _hash
    _check(reference['repository_id'] == 'workflow-repository:' + _hash(_canonical(
        [org, reference['repo_key'], reference['origin_digest']]))
        and (reference['repository_id'], reference['object_format']) ==
            (selected['subject']['repository']['id'], selected['subject']['object_format']))


def aware_time(value: str) -> str:
    _check(type(value) is str and 0 < len(value) <= 128
           and datetime.fromisoformat(value.replace('Z', '+00:00')).utcoffset() is not None)
    return value


class CodingCheckEvidence(Closed):
    name: Literal['Source Boundary Lint', 'Web Lint (Node 24)']
    check_id: Annotated[StrictInt, Field(gt=0)]
    run_id: Annotated[StrictInt, Field(gt=0)]
    head: str = Field(pattern=r'^[0-9a-f]{40}$')
    status: Literal['completed']
    conclusion: Literal['success']
    started_at: str
    completed_at: str
    observed_at: str

    @model_validator(mode='after')
    def times(self) -> CodingCheckEvidence:
        times = [datetime.fromisoformat(aware_time(v).replace('Z', '+00:00'))
                 for v in (self.started_at, self.completed_at, self.observed_at)]
        _check(times[0] <= times[1] <= times[2])
        return self


class CodingFinalIntent(Closed):
    command: CodingFinalizeCommand
    created_at: str
    path: str = Field(min_length=1, max_length=4096)
    digest: str = Field(pattern=r'^[0-9a-f]{64}$')
    selected_digest: str = Field(pattern=r'^[0-9a-f]{64}$')


class CodingBoundFinalIntent(CodingFinalIntent):
    repository_reference: RepositoryReference
    workspace: CodingFinalWorkspace
    origin: str = Field(min_length=1, max_length=4096)


class CodingCommandEvidence(Closed):
    # The read-only adapter exposes success or its original failure diagnostic;
    # it does not expose successful stdout/stderr or a raw transport receipt.
    argv: list[str] = Field(min_length=9, max_length=9)
    started_at: str
    finished_at: str
    exit_code: StrictInt | None
    succeeded: StrictBool
    diagnostic: str = Field(max_length=16384)


class CodingRemoteEvidence(Closed):
    repository: str = Field(min_length=3, max_length=256)
    number: Annotated[StrictInt, Field(gt=0)]
    url: str = Field(min_length=1, max_length=2048)
    head: str = Field(pattern=r'^[0-9a-f]{40}$')
    state: Literal['MERGED']
    merge_commit: str = Field(pattern=r'^[0-9a-f]{40}$')
    merged_at: str
    observed_at: str
    source: Literal['action_adapter', 'separate_read']

    @model_validator(mode='after')
    def remote_time(self) -> CodingRemoteEvidence:
        aware_time(self.merged_at)
        aware_time(self.observed_at)
        _check(self.url == f'https://github.com/{self.repository}/pull/{self.number}')
        return self


class CodingObservationFile(Closed):
    name: str = Field(pattern=r'^(check-runs|statuses|run-[1-9][0-9]*)$')
    path: str = Field(min_length=1, max_length=4096)
    digest: str = Field(pattern=r'^[0-9a-f]{64}$')


class CodingFinalEvidence(Closed):
    intent: CodingFinalIntent
    checks: list[CodingCheckEvidence] = Field(max_length=2)
    observations: list[CodingObservationFile] = Field(max_length=4)
    revalidated_at: str | None
    guard_verdict: Literal['merged', 'merge_guard_review', 'merge_guard_qa', 'merge_guard_mergeable',
        'merge_failed', 'ci_failed', 'stale_head', 'checks_missing', 'timeout', 'pr_closed', 'pr_draft', 'github_error']
    command: CodingCommandEvidence | None
    remote: CodingRemoteEvidence | None
    observation_diagnostic: str = Field(max_length=16384)


class CodingBoundFinalEvidence(CodingFinalEvidence):
    intent: CodingBoundFinalIntent


class CodingFinalOutput(Closed):
    format: Literal['workflow-coding-final-output@1']
    instance_id: Token
    attempt_id: Token
    revision: Annotated[StrictInt, Field(ge=1, le=1)]
    stage: Literal['agent_manager']
    subject: CodeChangeDescriptor
    upstream_results: CodingUpstreams
    outcome: Literal['merged', 'held', 'uncertain']
    findings: str = Field(min_length=1, max_length=16384)
    evidence: CodingFinalEvidence

    @model_validator(mode='after')
    def performed_only(self) -> CodingFinalOutput:
        from runtime.workflows.coding_saved_records import _canonical, _hash
        e, subject = self.evidence, self.subject.model_dump()
        c = e.intent.command
        _check(self.findings.strip() and '\x00' not in self.findings
               and len(self.findings.encode('utf8')) <= 16384)
        _check((c.instance_id, c.attempt_id, c.revision, c.subject.model_dump(), c.upstream_results) ==
               (self.instance_id, self.attempt_id, self.revision, subject, self.upstream_results))
        aware_time(e.intent.created_at)
        from pathlib import Path
        path = Path(e.intent.path)
        _check(path.is_absolute() and tuple(path.parts[-4:]) == ('output', c.task_id,
            'coding-finalize-' + _hash(c.attempt_id.encode('utf8')), 'intent.json'))
        intent_body = dict(command=c.model_dump(), created_at=e.intent.created_at,
                           selected_digest=e.intent.selected_digest)
        if self.format == 'workflow-coding-final-output@2':
            intent_body.update(repository_reference=e.intent.repository_reference.model_dump(),
                               workspace=e.intent.workspace.model_dump(), origin=e.intent.origin)
        _check(e.intent.digest == _hash(_canonical(intent_body)))
        repo, number = github_subject(subject)
        head = subject['head']
        _check(len({v.name for v in e.observations}) == len(e.observations))
        for observation in e.observations:
            _check(Path(observation.path) == path.parent / (observation.name + '.json'))
        if e.revalidated_at is not None:
            aware_time(e.revalidated_at)
        if e.command is not None:
            a = e.command
            _check(a.argv == ['gh', 'pr', 'merge', str(number), '--repo', repo, '--merge', '--match-head-commit', head])
            aware_time(a.started_at)
            aware_time(a.finished_at)
            _check(not a.succeeded or (a.exit_code == 0 and a.diagnostic == ''))
        if e.remote is not None:
            _check((e.remote.repository, e.remote.number, e.remote.head) == (repo, number, head))
        if self.outcome == 'merged':
            _check(e.guard_verdict == 'merged' and e.command is not None and e.command.succeeded
                   and e.command.exit_code == 0 and e.remote is not None
                   and e.remote.source == 'action_adapter' and e.revalidated_at is not None
                   and len(e.checks) == 2 and {v.name for v in e.checks} ==
                       {'Source Boundary Lint', 'Web Lint (Node 24)'}
                   and len({v.check_id for v in e.checks}) == 2
                   and all(v.head == head for v in e.checks)
                   and {v.name for v in e.observations} ==
                       {'check-runs', 'statuses', *('run-' + str(v.run_id) for v in e.checks)})
            times = [datetime.fromisoformat(v.replace('Z', '+00:00')) for v in
                (e.intent.created_at, e.revalidated_at, e.command.started_at,
                 e.remote.observed_at, e.command.finished_at)]
            _check(times == sorted(times) and all(datetime.fromisoformat(v.observed_at.replace('Z', '+00:00')) <= times[0] for v in e.checks))
        elif self.outcome == 'held':
            _check(e.command is None and e.remote is None)
        else:
            _check(e.command is not None)
        return self


class CodingBoundFinalOutput(CodingFinalOutput):
    format: Literal['workflow-coding-final-output@2']
    evidence: CodingBoundFinalEvidence

    @model_validator(mode='after')
    def bound_performed_only(self) -> CodingBoundFinalOutput:
        from pathlib import Path
        from runtime.workflows.coding_saved_records import _hash
        intent = self.evidence.intent
        command, workspace = intent.command.model_dump(), intent.workspace.model_dump()
        validate_final_identity(dict(**command, workspace=workspace,
            repository_reference=intent.repository_reference.model_dump()), org=workspace['org_slug'])
        _check(intent.path == str(Path(workspace['workspace_root']) / 'output' / command['task_id'] /
            ('coding-finalize-' + _hash(command['attempt_id'].encode('utf8'))) / 'intent.json'))
        for observation in self.evidence.observations:
            _check(observation.path == str(Path(intent.path).parent / (observation.name + '.json')))
        _check(_hash(intent.origin.encode('utf8')) == intent.repository_reference.origin_digest)
        repo, _ = github_subject(self.subject.model_dump())
        _check(github_origin(intent.origin) == repo)
        # Exact lexical checkout containment; the tool additionally checks no aliases and Git common-dir.
        checkout = Path(workspace['workspace_root']) / 'repos' / intent.repository_reference.repo_key
        local = Path(command['repository_path'])
        _check(str(local) == command['repository_path'] and '..' not in local.parts
               and (local == checkout or local.is_relative_to(checkout / '.claude' / 'worktrees')))
        return self


def github_origin(origin: str) -> str:
    # No normalization: the configured raw UTF8 origin digest is checked separately.
    match = re.fullmatch(r'(?:https://github\.com/|git@github\.com:)([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?)(?:\.git)?', origin)
    _check(match is not None)
    return match[1]


def github_subject(subject: dict) -> tuple[str, int]:
    _check(subject['object_format'] == 'sha1' and subject['comparison'] is not None)
    navigation = subject['navigation']
    _check(type(navigation['pr_url']) is str and type(navigation['branch']) is str
           and navigation['branch'] and not navigation['branch'].startswith('-'))
    match = re.fullmatch(r'https://github\.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)/pull/([1-9][0-9]*)', navigation['pr_url'])
    _check(match is not None and int(match[2]) <= 2147483647)
    return match[1], int(match[2])


def parse_finalize(raw: bytes) -> dict:
    return _model(CodingFinalizeCommand, _json(raw, 65536)).model_dump()


def parse_final_output(result: dict, *, instance: str, attempt: dict, work: dict,
                       subject: dict, upstream: dict, selected: dict | None = None) -> dict:
    _check(type(result['id']) is int and result['id'] > 0 and result['id'] == attempt['result_id']
           and (result['task_id'], result['session_id'], result['agent']) ==
               (attempt['task_id'], attempt['session_id'], attempt['principal'])
           and attempt['stage'] == 'manager_closeout' and result['status'] == 'completed'
           and result['decision_json'] is None and result['waiting_on_job_ids'] is None
           and result['verdict'] is None and type(result['output_summary']) is str)
    raw = _json(result['output_summary'].encode('utf8'), 65536)
    repaired = selected is not None and selected['format'] == 'workflow-coding-final-selection@2'
    output = _model(CodingBoundFinalOutput if repaired else CodingFinalOutput, raw).model_dump()
    if repaired:
        intent = output['evidence']['intent']
        _check(intent['repository_reference'] == selected['repository_reference']
               and intent['workspace'] == selected['workspace'])
    _check((output['instance_id'], output['attempt_id'], output['revision'], output['subject'], output['upstream_results']) ==
           (instance, attempt['id'], attempt['revision'], subject, upstream))
    c = output['evidence']['intent']['command']
    _check((c['task_id'], c['session_id'], c['principal']) ==
           (attempt['task_id'], attempt['session_id'], attempt['principal']))
    _check((subject['repository']['id'], subject['object_format'], subject['comparison']['base']) ==
           (work['repository_id'], work['object_format'], work['base']))
    # Positive aware callback time is an upper bound on same-session observations.
    callback_at = datetime.fromisoformat(aware_time(result['created_at']).replace('Z', '+00:00'))
    evidence = output['evidence']
    for value in (evidence['intent']['created_at'], evidence['revalidated_at'],
                  None if evidence['command'] is None else evidence['command']['finished_at'],
                  None if evidence['remote'] is None else evidence['remote']['observed_at']):
        _check(value is None or datetime.fromisoformat(value.replace('Z', '+00:00')) <= callback_at)
    return output


def parse_start(raw: bytes) -> dict:
    return _model(CodingStart, _json(raw, START_LIMIT)).model_dump()


def parse_decision(raw: bytes) -> dict:
    # The native callback owner preserves json.dumps spacing and key order.
    # Retain those bytes separately; only the nested command is canonicalized.
    _check(type(raw) is bytes and 0 < len(raw) <= START_LIMIT)
    value = json.loads(raw.decode('utf8'), object_pairs_hook=_pairs, parse_constant=_constant)
    return _model(CodingStartDecision, value).model_dump()


def effective_mapping(mapping: dict, command: dict) -> dict:
    """Select initial work without inventing a developer head or subject."""
    import copy
    selected = copy.deepcopy(mapping)
    override = command['implementer_override']
    if override is not None:
        _check(override in mapping['eligible_replacements']['implementer'])
        selected['roles']['implementer'] = override
    selected['eligible_replacements'] = {key: [] for key in selected['roles']}
    references = [r for r in mapping['repository_references']
        if (r['repository_id'], r['object_format']) ==
           (command['work']['repository_id'], command['work']['object_format'])]
    _check(len(references) == 1 and command['release'] == mapping['release'])
    # RoleMapping performs distinct-principal and repository-reference checks.
    from runtime.workflows._coding_role_records import RoleMapping
    RoleMapping.model_validate(selected)
    return selected


def parse_native_decision(raw: bytes) -> dict:
    # Existing native callback route owns this reserved carrier. Preserve the
    # entire original result and fingerprint; it is never caller authority.
    _check(type(raw) is bytes and 0 < len(raw) <= 1048576)
    value = json.loads(raw.decode('utf8'), object_pairs_hook=_pairs, parse_constant=_constant)
    _check(type(value) is dict)
    value.pop('_manager_self_evaluation', None)
    return _model(CodingStartDecision, value).model_dump()


def start_decision_schema() -> dict:
    """Closed nested callback schema without dangling OpenAPI component refs."""
    schema = CodingStartDecision.model_json_schema()
    definitions = schema.pop('$defs', {})

    def inline(value: object) -> object:
        if isinstance(value, dict):
            if '$ref' in value:
                return inline(definitions[value['$ref'].rsplit('/', 1)[-1]])
            return {key: inline(item) for key, item in value.items()}
        if isinstance(value, list):
            return [inline(item) for item in value]
        return value
    return inline(schema)
