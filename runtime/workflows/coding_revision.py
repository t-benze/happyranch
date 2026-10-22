"""Closed revision scope encodings; no allocation, authority or native effects.

Historical scope/output families retain their literal r1 contract. These pure
encoders receive only independently reconstructed native history at each call.
"""
from __future__ import annotations

from runtime.workflows.coding_bootstrap import final_scope, identity
from runtime.workflows.coding_saved_records import _check, _hash
from runtime.workflows.coding_authority_history import _decoded
from runtime.workflows.coding_start_records import parse_stage_output

ROLES = dict(development='implementer', code_review='code_reviewer', qa='qa_engineer', manager_closeout='agent_manager')


def current_stage(attempts: dict, revision: int, stage: str) -> dict | None:
    selected = [a for a in attempts.values() if a['revision'] == revision and a['stage'] == stage]
    _check(len(selected) <= 1)
    return selected[0] if selected else None


def contributors(attempts: dict) -> list[dict]:
    # Allocation is retained contribution provenance, even before a result exists.
    return [dict(attempt_id=a['id'], task_id=a['task_id'], principal=a['principal'])
            for a in sorted(attempts.values(), key=lambda a: a['id']) if a['stage'] == 'development']


def independent(attempts: dict, roles: dict) -> None:
    authors = {a['principal'] for a in attempts.values() if a['stage'] == 'development'}
    _check(len({b['principal'] for b in roles.values()}) == 4
           and not authors.intersection(roles[k]['principal'] for k in ('code_reviewer', 'qa_engineer', 'agent_manager')))


def returned_findings(attempts: dict, stages: dict, instance: str, work: dict, budget: object) -> list[dict]:
    values = []
    for a in sorted(attempts.values(), key=lambda a: (a['revision'], a['stage'])):
        budget.step()
        if a['stage'] not in ('development', 'code_review', 'qa'):
            continue
        native = stages.get(a['id'])
        if native is None or native.get('callback') is None:
            continue
        r = native['callback']
        _check(type(r['output_summary']) is str and len(r['output_summary']) <= 65536)
        budget.consumed += 12 * len(r['output_summary'])
        _check(budget.remaining_bytes >= 0)
        output = parse_stage_output(r, instance=instance, attempt=a, work=work)
        _decoded(output, budget)
        _check(a['state'] == 'completed' and native['source_settled'] and native['cleanup_recorded']
               and native['cleanup'] and native['terminal'] == 'success' and native['claim'] is not None
               and not native.get('ownership_unknown'))
        values.append(dict(attempt_id=a['id'], task_id=a['task_id'], session_id=a['session_id'],
            principal=a['principal'], result_id=r['id'], revision=a['revision'], outcome=output['outcome'],
            output_digest=_hash(r['output_summary'].encode('utf8')), summary=r['output_summary'], verdict=r['verdict']))
    return values


def attempt_identity(instance: str, revision: int, stage: str, sequence: int, generation: int) -> str:
    return identity('attempt', instance, 'revision', revision, stage, sequence, generation)


def revision_scope(*, org: str, instance: str, command: dict, roles: dict, attempt: dict,
                   attempts: dict, subject: dict, returns: list, upstream: dict | None,
                   repository_reference: dict) -> dict:
    stage, revision = attempt['stage'], attempt['revision']
    role = ROLES[stage]
    if stage == 'manager_closeout':
        value = final_scope(org=org, instance=instance, command=command, roles=roles,
            qa=current_stage(attempts, revision, 'qa'), subject=subject,
            task_id=attempt['task_id'], upstream=upstream, repository_reference=repository_reference)
        value['format'] = 'workflow-coding-final-scope@3'
    else:
        value = dict(format='workflow-coding-revision-scope@1', org_slug=org, instance_id=instance,
            task_id=attempt['task_id'], stage=stage, role=role, principal=roles[role]['principal'], team=roles[role]['team'],
            release=command['release'], mapping=command['mapping'], mapping_digest=command['mapping']['digest'],
            context_id=identity('context', instance), authorization=identity('authorization', instance),
            work_scope=command['work'], subject=None if stage == 'development' else subject,
            previous_subject=subject if stage == 'development' else None,
            returned_findings=returns if stage == 'development' else [],
            upstream_results=upstream)
    value.update(attempt_id=attempt['id'], revision=revision, attempt_sequence=attempt['attempt_sequence'],
        assignment_generation=attempt['assignment_generation'], contributors=contributors(attempts))
    if command['format'] == 'workflow-coding-start@2':
        value['settings'] = command['settings']
    return value


def manager_no_action(output: dict) -> bool:
    """Only a complete bound callback's affirmative pre-action refusal.

    Called after full result/intent/workspace/native validation. Absence of a
    callback, partial intent, command receipt or owner is never proof here.
    """
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
        attempts=attempts, subject=subject, returns=[], upstream=upstream, repository_reference=repository_reference)
    value.update(format='workflow-coding-final-scope@4', predecessor=dict(attempt_id=previous['id'],
        task_id=previous['task_id'], session_id=previous['session_id'], result_id=previous['result_id'],
        principal=previous['principal'], scope_digest=previous['scope_digest']))
    return value


def revision_brief(work: dict, stage: str) -> str:
    from runtime.workflows.coding_bootstrap import final_brief, qa_brief, review_brief
    if stage != 'development':
        return {'code_review': review_brief, 'qa': qa_brief, 'manager_closeout': final_brief}[stage](work)
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
    budget.consumed += 12 * len(callback['output_summary'])
    _check(budget.remaining_bytes >= 0)
    scope = _read_json(attempt['scope_bytes'], budget, 1048576)
    try:
        output = parse_stage_output(callback, instance=attempt['instance_id'], attempt=attempt, work=work)
    except (ValueError, TypeError, UnicodeError):
        return False
    _decoded(output, budget)
    return output['subject']['head'] != scope['previous_subject']['head']


def scope_allowance(attempts: dict, command: dict, subject: dict, returns: list,
                    upstream: dict | None, budget: object) -> None:
    """Charge original objects before producing repeated scope/canonical copies."""
    for _ in attempts:
        budget.step()
    for value in (command, subject, returns, upstream):
        _copy_allowance(value, budget)
    for a in attempts.values():
        # Contributor and attempt inventory projection copies, not all prior scopes.
        _copy_allowance(dict(id=a['id'], task_id=a['task_id'], principal=a['principal']), budget)


def _copy_allowance(value: object, budget: object) -> None:
    pending = [value]
    while pending:
        item = pending.pop()
        budget.step()
        if type(item) is dict:
            pending.extend(item.keys())
            pending.extend(item.values())
        elif type(item) is list:
            pending.extend(item)
        elif type(item) is str:
            budget.consumed += 12 * len(item)
        elif type(item) is bytes:
            budget.consumed += 4 * len(item)
        _check(budget.remaining_bytes >= 0)
