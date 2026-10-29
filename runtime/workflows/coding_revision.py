"""Closed revision scope encodings; no allocation, authority or native effects.

Historical scope/output families retain their literal r1 contract. These pure
encoders receive only independently reconstructed native history at each call.
"""
from __future__ import annotations

from runtime.infrastructure._workflow_reader import (
    _append as _reader_append,
    _put as _reader_put,
    _update as _reader_update,
)

from runtime.infrastructure._workflow_reader import (
    _list as _reader_list,
    _set as _reader_set,
    _sorted as _reader_sorted,
)

from runtime.infrastructure._workflow_reader import (
    _ReaderBudgetExhausted, _encode as _reader_encode, _work as _reader_work,
    _reserve as _reader_reserve,
)
from runtime.workflows.coding_bootstrap import final_scope, identity
from runtime.workflows.coding_saved_records import _check, _hash
from runtime.workflows.coding_authority_history import _decoded
from runtime.workflows.coding_start_records import parse_stage_output

ROLES = dict(development='implementer', code_review='code_reviewer', qa='qa_engineer', manager_closeout='agent_manager')


def current_stage(attempts: dict, revision: int, stage: str, *, budget=None) -> dict | None:
    _reader_reserve(budget, 64 + 16 * len(attempts))
    if budget is not None:
        for _ in attempts:
            budget.step()
    selected = _reader_list((a for a in attempts.values() if a['revision'] == revision and a['stage'] == stage), budget=budget)
    _check(len(selected) <= 1)
    return selected[0] if selected else None


def contributors(attempts: dict, *, budget=None) -> list[dict]:
    # Allocation is retained contribution provenance, even before a result exists.
    _reader_reserve(budget, 64 + 32 * len(attempts))
    if budget is not None:
        for a in attempts.values():
            budget.step()
            _reader_work((_reader_reserve(budget, 112) or (a['id'], a['task_id'], a['principal'])), budget)
    return _reader_list(((_reader_reserve(budget, 448) or dict(attempt_id=a['id'], task_id=a['task_id'], principal=a['principal']))
            for a in _reader_sorted(attempts.values(), key=lambda a: a['id'], budget=budget) if a['stage'] == 'development'), budget=budget)


def independent(attempts: dict, roles: dict, *, budget=None) -> None:
    _reader_reserve(budget, 64 * (len(attempts) + len(roles) + 2))
    if budget is not None:
        for a in attempts.values():
            budget.step()
            _reader_work(a['principal'], budget)
        for role in roles.values():
            budget.step()
            _reader_work(role['principal'], budget)
    authors = _reader_set((a['principal'] for a in attempts.values() if a['stage'] == 'development'), budget=budget)
    _check(len(_reader_set((b['principal'] for b in roles.values()), budget=budget)) == 4
           and not authors.intersection(roles[k]['principal'] for k in ('code_reviewer', 'qa_engineer', 'agent_manager')))


def returned_findings(attempts: dict, stages: dict, instance: str, work: dict, budget: object) -> list[dict]:
    _reader_reserve(budget, 64 + 64 * len(attempts))
    values = (_reader_reserve(budget, 64) or [])
    for a in _reader_sorted(attempts.values(), key=lambda a: (_reader_reserve(budget, 96) or (a['revision'], a['stage'])), budget=budget):
        budget.step()
        if a['stage'] not in ('development', 'code_review', 'qa'):
            continue
        native = stages.get(a['id'])
        if native is None or native.get('callback') is None:
            continue
        r = native['callback']
        _check(type(r['output_summary']) is str and len(r['output_summary']) <= 65536)
        _reader_reserve(budget, 12 * len(r['output_summary']))
        output = parse_stage_output(r, instance=instance, attempt=a, work=work, budget=budget)
        _decoded(output, budget)
        _check(a['state'] == 'completed' and native['source_settled'] and native['cleanup_recorded']
               and native['cleanup'] and native['terminal'] == 'success' and native['claim'] is not None
               and not native.get('ownership_unknown'))
        _reader_append(values, _reader_reserve(budget, 1344) or dict(attempt_id=a['id'], task_id=a['task_id'], session_id=a['session_id'],
            principal=a['principal'], result_id=r['id'], revision=a['revision'], outcome=output['outcome'],
            output_digest=_hash(_reader_encode(r['output_summary'], 'utf8', budget=budget), reader_budget=budget), summary=r['output_summary'], verdict=r['verdict']), budget=budget)
    return values


def attempt_identity(instance: str, revision: int, stage: str, sequence: int, generation: int, *, budget=None) -> str:
    return identity('attempt', instance, 'revision', revision, stage, sequence, generation, budget=budget)


def revision_scope(*, org: str, instance: str, command: dict, roles: dict, attempt: dict,
                   attempts: dict, subject: dict, returns: list, upstream: dict | None,
                   repository_reference: dict, budget=None) -> dict:
    # Nested scope members remain original references here; the actual
    # canonical/dump owner separately charges their traversal and copying.
    _reader_reserve(budget, 2048)
    stage, revision = (_reader_reserve(budget, 96) or (attempt['stage'], attempt['revision']))
    role = ROLES[stage]
    if stage == 'manager_closeout':
        value = final_scope(org=org, instance=instance, command=command, roles=roles,
            qa=current_stage(attempts, revision, 'qa', budget=budget), subject=subject,
            task_id=attempt['task_id'], upstream=upstream, repository_reference=repository_reference, budget=budget)
        _reader_put('workflow-coding-final-scope@3', value, 'format', budget=budget)
    else:
        value = (_reader_reserve(budget, 2368) or dict(format='workflow-coding-revision-scope@1', org_slug=org, instance_id=instance,
            task_id=attempt['task_id'], stage=stage, role=role, principal=roles[role]['principal'], team=roles[role]['team'],
            release=command['release'], mapping=command['mapping'], mapping_digest=command['mapping']['digest'],
            context_id=identity('context', instance, budget=budget), authorization=identity('authorization', instance, budget=budget),
            work_scope=command['work'], subject=None if stage == 'development' else subject,
            previous_subject=subject if stage == 'development' else None,
            returned_findings=returns if stage == 'development' else (_reader_reserve(budget, 64) or []),
            upstream_results=upstream))
    _reader_update(value, (_reader_reserve(budget, 704) or {'attempt_id': attempt['id'], 'revision': revision, 'attempt_sequence': attempt['attempt_sequence'], 'assignment_generation': attempt['assignment_generation'], 'contributors': contributors(attempts, budget=budget)}), budget=budget)
    if command['format'] == 'workflow-coding-start@2':
        _reader_put(command['settings'], value, 'settings', budget=budget)
    return value


def manager_no_action(output: dict, *, budget=None) -> bool:
    """Only a complete bound callback's affirmative pre-action refusal.

    Called after full result/intent/workspace/native validation. Absence of a
    callback, partial intent, command receipt or owner is never proof here.
    """
    _reader_work(output, budget)
    evidence = output['evidence']
    return (output['format'] in ('workflow-coding-final-output@2', 'workflow-coding-final-output@3')
            and output['outcome'] == 'held' and evidence['command'] is None and evidence['remote'] is None
            and evidence['revalidated_at'] is None
            and evidence['guard_verdict'] in ('ci_failed', 'checks_missing', 'timeout', 'merge_guard_mergeable',
                'merge_guard_review', 'merge_guard_qa'))


def manager_successor_scope(*, org: str, instance: str, command: dict, roles: dict, attempt: dict,
                            attempts: dict, subject: dict, upstream: dict, repository_reference: dict, budget: object) -> dict:
    previous = attempts[attempt['predecessor_id']]
    _check(previous['stage'] == attempt['stage'] == 'manager_closeout'
           and previous['revision'] == attempt['revision'] and previous['principal'] == attempt['principal'])
    _copy_allowance(previous, budget)
    value = revision_scope(org=org, instance=instance, command=command, roles=roles, attempt=attempt,
        attempts=attempts, subject=subject, returns=(_reader_reserve(budget, 64) or []), upstream=upstream, repository_reference=repository_reference, budget=budget)
    _reader_update(value, (_reader_reserve(budget, 448) or {'format': 'workflow-coding-final-scope@4', 'predecessor_id': previous['id'], 'predecessor': _reader_reserve(budget, 832) or dict(attempt_id=previous['id'],
        task_id=previous['task_id'], session_id=previous['session_id'], result_id=previous['result_id'],
        principal=previous['principal'], scope_digest=previous['scope_digest'])}), budget=budget)
    return value


def revision_brief(work: dict, stage: str, *, budget=None) -> str:
    _reader_work(work, budget)
    from runtime.workflows.coding_bootstrap import final_brief, qa_brief, review_brief
    if stage != 'development':
        return (_reader_reserve(budget, 448) or {'code_review': review_brief, 'qa': qa_brief, 'manager_closeout': final_brief})[stage](work, budget=budget)
    return ('Revise the fixed-base coding work using every original returned finding in your bound supplement.\n'
            'Produce a genuinely changed commit with existing authorized tools; no previous approval applies.\n' + work['brief'])


def fulfilled_revision(attempt: dict, callback: dict | None, work: dict, budget: object) -> bool:
    """A new-family positive outer closure may fulfill content before allocation.

    Unusable output is retained for the native held disposition. Budget and
    scope-read refusal propagate; they are never converted into missing output.
    """
    from runtime.workflows._coding_settings_execution import _read_json
    if attempt['stage'] != 'development' or attempt['revision'] <= 1 or callback is None:
        return False
    _check(type(callback['output_summary']) is str and len(callback['output_summary']) <= 65536)
    _reader_reserve(budget, 12 * len(callback['output_summary']))
    scope = _read_json(attempt['scope_bytes'], budget, 1048576)
    try:
        _reader_reserve(budget, 112)
        output = parse_stage_output(callback, instance=attempt['instance_id'], attempt=attempt, work=work, budget=budget)
    except _ReaderBudgetExhausted:
        raise
    except ((ValueError, TypeError, UnicodeError)):
        return False
    _decoded(output, budget)
    return output['subject']['head'] != scope['previous_subject']['head']


def scope_allowance(attempts: dict, command: dict, subject: dict, returns: list,
                    upstream: dict | None, budget: object) -> None:
    """Charge original objects before producing repeated scope/canonical copies."""
    for _ in attempts:
        budget.step()
    for value in (_reader_reserve(budget, 128) or (command, subject, returns, upstream)):
        _copy_allowance(value, budget)
    for a in attempts.values():
        # Contributor and attempt inventory projection copies, not all prior scopes.
        _copy_allowance((_reader_reserve(budget, 448) or dict(id=a['id'], task_id=a['task_id'], principal=a['principal'])), budget)


def _copy_allowance(value: object, budget: object) -> None:
    _reader_work(value, budget, copies=2)
