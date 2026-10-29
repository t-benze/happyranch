"""Canonical K3a agent-read records, independent of document/custody encodings."""
from __future__ import annotations

from runtime.infrastructure._workflow_reader import (
    _append as _reader_append,
    _update as _reader_update,
)

from runtime.infrastructure._workflow_reader import _reserve as _reader_reserve

from runtime.infrastructure._workflow_reader import (
    _dict_pairs as _reader_dict_pairs,
    _list as _reader_list,
    _set as _reader_set,
    _sorted as _reader_sorted,
)

from runtime.infrastructure._workflow_reader import (
    _b64decode as _reader_b64decode,
    _b64encode as _reader_b64encode,
    _decode as _reader_decode,
)

from runtime.infrastructure._workflow_reader import _work as _reader_work

import base64

from runtime.workflows.coding_saved_records import _canonical, _check, _hash


def identity(kind: str, *parts: object, budget=None) -> str:
    _reader_work((_reader_reserve(budget, 96) or (kind, parts,)), budget)
    return 'coding-' + kind + ':' + _hash(_canonical(parts, reader_budget=budget), reader_budget=budget)


def encode_row(row: dict, *, budget=None) -> dict:
    # SQL BLOB and TEXT remain distinct, including empty values.
    _reader_work((_reader_reserve(budget, 80) or (row,)), budget)
    return _reader_dict_pairs(((_reader_reserve(budget, 96) or (k,  (_reader_reserve(budget, 192) or {'sql_blob_base64': _reader_decode(_reader_b64encode(v, budget=budget), 'ascii', budget=budget)})
            if type(v) is bytes else v )) for k, v in row.items()), budget=budget)


def decode_row(value: dict, *, budget=None) -> dict:
    _reader_work((_reader_reserve(budget, 80) or (value,)), budget)
    return _reader_dict_pairs(((_reader_reserve(budget, 96) or (k,  _reader_b64decode(v['sql_blob_base64'], validate=True, budget=budget)
            if type(v) is dict and _reader_set(v, budget=budget) == _reader_set((_reader_reserve(budget, 80) or ('sql_blob_base64',)), budget=budget) else v )) for k, v in value.items()), budget=budget)


def task_brief(work: dict, *, budget=None) -> str:
    _reader_work((_reader_reserve(budget, 80) or (work,)), budget)
    return ('Coding development is queued. Downstream coding execution is unavailable.\n'
            'The pinned repository, object format and comparison base are immutable; '
            'no developer head or approval exists.\n' + work['brief'])


def review_brief(work: dict, *, budget=None) -> str:
    _reader_work((_reader_reserve(budget, 80) or (work,)), budget)
    return ('Independently review the exact coding subject in the bound supplement.\n'
            'Use existing authorized repository tools; unavailable access requires BLOCK.\n'
            'Return genuine APPROVE, REQUEST_CHANGES or BLOCK with findings.\n' + work['brief'])


def review_scope(*, org: str, instance: str, command: dict, roles: dict,
                 developer: dict, subject: dict, budget=None) -> dict:
    """Same-team first-edge scope; unrelated cross-team @3 pins stay reserved."""
    _reader_work((_reader_reserve(budget, 160) or (org, instance, command, roles, developer, subject,)), budget)
    return (_reader_reserve(budget, 2368) or dict(format='workflow-coding-review-scope@1', org_slug=org, instance_id=instance,
        attempt_id=identity('attempt', instance, 2, budget=budget), stage='code_review', role='code_reviewer',
        principal=roles['code_reviewer']['principal'], team=roles['code_reviewer']['team'],
        revision=1, attempt_sequence=1, assignment_generation=1, release=command['release'],
        mapping_digest=command['mapping']['digest'], context_id=identity('context', instance, budget=budget),
        authorization=identity('authorization', instance, budget=budget), work_scope=command['work'],
        subject=subject, developer=(_reader_reserve(budget, 704) or dict(attempt_id=developer['id'], task_id=developer['task_id'],
            session_id=developer['session_id'], result_id=developer['result_id'], principal=developer['principal']))))


def qa_brief(work: dict, *, budget=None) -> str:
    _reader_work((_reader_reserve(budget, 80) or (work,)), budget)
    return ('Independently QA the exact produced and approved coding subject in the bound supplement.\n'
            'Use existing authorized repository tools; unavailable access or proof requires BLOCK.\n'
            'Return genuine PASS, REVISE or BLOCK with your own findings.\n' + work['brief'])


def qa_scope(*, org: str, instance: str, command: dict, roles: dict, developer: dict,
             reviewer: dict, subject: dict, task_id: str, budget=None) -> dict:
    _reader_work((_reader_reserve(budget, 192) or (org, instance, command, roles, developer, reviewer, subject, task_id,)), budget)
    return (_reader_reserve(budget, 2752) or dict(format='workflow-coding-qa-scope@1', org_slug=org, instance_id=instance,
        attempt_id=identity('attempt', instance, 3, budget=budget), task_id=task_id, stage='qa', role='qa_engineer',
        principal=roles['qa_engineer']['principal'], team=roles['qa_engineer']['team'],
        revision=1, attempt_sequence=1, assignment_generation=1, release=command['release'],
        mapping=command['mapping'], mapping_digest=command['mapping']['digest'], context_id=identity('context', instance, budget=budget),
        authorization=identity('authorization', instance, budget=budget), work_scope=command['work'], subject=subject,
        developer=(_reader_reserve(budget, 960) or dict(attempt_id=developer['id'], task_id=developer['task_id'],
            session_id=developer['session_id'], result_id=developer['result_id'],
            principal=developer['principal'], outcome='produced', subject=subject)),
        reviewer=(_reader_reserve(budget, 960) or dict(attempt_id=reviewer['id'], task_id=reviewer['task_id'],
            session_id=reviewer['session_id'], result_id=reviewer['result_id'],
            principal=reviewer['principal'], outcome='APPROVE', subject=subject))))


def final_brief(work: dict, *, budget=None) -> str:
    _reader_work((_reader_reserve(budget, 80) or (work,)), budget)
    return ('Finalize only your own exact coding subject using the consumed final-manager supplement.\n'
            'Use the LOCAL workflows coding finalize command once, current original SID, no decisions/jobs.\n'
            'Preserve intent/action/remote receipts before your FINAL genuine callback; unavailable authority holds.\n'
            + work['brief'])


def final_scope(*, org: str, instance: str, command: dict, roles: dict, qa: dict,
                subject: dict, task_id: str, upstream: dict, repository_reference: dict | None = None, budget=None) -> dict:
    _reader_work((_reader_reserve(budget, 208) or (org, instance, command, roles, qa, subject, task_id, upstream, repository_reference,)), budget)
    scope = (_reader_reserve(budget, 2752) or dict(format='workflow-coding-final-scope@1', org_slug=org, instance_id=instance,
        attempt_id=identity('attempt', instance, 4, budget=budget), task_id=task_id, stage='agent_manager', role='agent_manager',
        principal=roles['agent_manager']['principal'], team=roles['agent_manager']['team'],
        revision=1, attempt_sequence=1, assignment_generation=1, release=command['release'],
        mapping=command['mapping'], mapping_digest=command['mapping']['digest'], context_id=identity('context', instance, budget=budget),
        authorization=identity('authorization', instance, budget=budget), work_scope=command['work'], subject=subject,
        predecessor_id=qa['id'], upstream_results=upstream))
    if repository_reference is not None:
        _reader_update(scope, (_reader_reserve(budget, 320) or {'format': 'workflow-coding-final-scope@2', 'repository_reference': repository_reference}), budget=budget)
    return scope


def pinned_rows(*, org: str, command: dict, source: dict, native_source: dict,
                authority: dict, mapping: dict, roles: dict, template: dict,
                carrier_id: str, instance_id: str, created_at: str, budget=None) -> tuple[dict, dict, dict]:
    """Literal agent-read families; historical custody bytes keep their readers."""
    _reader_work((_reader_reserve(budget, 240) or (org, command, source, native_source, authority, mapping, roles, template, carrier_id, instance_id, created_at,)), budget)
    from runtime.workflows._coding_settings_execution import _settings_family
    settings = _settings_family(command)
    root = identity('root', org, source['task_id'], source['result']['id'], _hash(_canonical(command, reader_budget=budget), reader_budget=budget), budget=budget)
    _check(root == instance_id)
    activation_id = identity('activation', root, budget=budget)
    authorization_id = identity('authorization', root, budget=budget)
    binding_id = identity('binding', root, budget=budget)
    context_id = identity('context', root, budget=budget)
    namespace = f'org/{org}/workflow-instance/{root}'
    reference = next(r for r in mapping['repository_references']
                     if r['repository_id'] == command['work']['repository_id'])
    authorization = (_reader_reserve(budget, 1344) or dict(format='workflow-coding-authorization@2', instance_id=root,
        owner_principal='agent:' + source['principal'], source=source, template=template,
        authority=authority, work_scope=command['work'], repository_reference=reference,
        budgets=source['budgets'], created_at=created_at))
    binding = (_reader_reserve(budget, 1216) or dict(format='workflow-coding-binding@2', instance_id=root,
        template_version_id=template['version_id'], authorization_revision_id=authorization_id,
        mapping_id=command['mapping']['id'], mapping_digest=command['mapping']['digest'],
        bindings=roles, eligible_replacements=mapping['eligible_replacements'], repository_reference=reference))
    context = (_reader_reserve(budget, 1984) or dict(format='workflow-coding-context@2', org_slug=org, release=command['release'],
        mapping=command['mapping'], bindings=roles, eligible_replacements=mapping['eligible_replacements'],
        repository_reference=reference, work_scope=command['work'], authority=authority,
        source=source, native_source=native_source, authorization_id=authorization_id,
        binding_snapshot_id=binding_id, activation_id=activation_id, activation_revision=1))
    if settings:
        _reader_update(authorization, (_reader_reserve(budget, 448) or {'format': 'workflow-coding-authorization@3', 'settings': command['settings'], 'mapping': command['mapping']}), budget=budget)
        _reader_update(binding, (_reader_reserve(budget, 448) or {'format': 'workflow-coding-binding@3', 'settings': command['settings'], 'mapping': command['mapping']}), budget=budget)
        _reader_update(context, (_reader_reserve(budget, 320) or {'format': 'workflow-coding-context@3', 'settings': command['settings']}), budget=budget)
    grant, bound, ctx = (_canonical(value, reader_budget=budget)
                         for value in (_reader_reserve(budget, 112) or (authorization, binding, context)))
    pin = authority['pin']
    digest = _hash(_canonical(command, reader_budget=budget), reader_budget=budget)
    actor = 'agent:' + source['principal']
    rows = (_reader_reserve(budget, 1216) or {
        'workflow_authorization_revisions': (_reader_reserve(budget, 192) or [(_reader_reserve(budget, 960) or dict(id=authorization_id, namespace=namespace,
            revision=1, authority_bytes=grant, authority_digest=_hash(grant, reader_budget=budget),
            source_pin='workflow-coding-agent-read-start@2' if settings else 'workflow-coding-agent-read-start@1', created_at=created_at))]),
        'workflow_active_authorizations': (_reader_reserve(budget, 192) or [(_reader_reserve(budget, 320) or dict(namespace=namespace, authorization_revision_id=authorization_id))]),
        'workflow_binding_snapshots': (_reader_reserve(budget, 192) or [(_reader_reserve(budget, 832) or dict(id=binding_id, template_version_id=template['version_id'],
            authorization_revision_id=authorization_id, binding_bytes=bound,
            binding_digest=_hash(bound, reader_budget=budget), created_at=created_at))]),
        'workflow_contexts': (_reader_reserve(budget, 192) or [(_reader_reserve(budget, 832) or dict(id=context_id, binding_snapshot_id=binding_id, context_bytes=ctx,
            context_digest=_hash(ctx, reader_budget=budget), source_kind='coding-manager-result', source_id=(_reader_reserve(budget, 128) or str(source['result']['id']))))]),
        'workflow_instances': (_reader_reserve(budget, 192) or [(_reader_reserve(budget, 832) or dict(id=root, binding_snapshot_id=binding_id, context_id=context_id,
            root_task_id=carrier_id, owner_principal=actor, status='draft'))]),
        'workflow_activations': (_reader_reserve(budget, 192) or [(_reader_reserve(budget, 1600) or dict(id=activation_id, instance_id=root, activation_revision=1,
            template_identity_id=template['identity_id'], template_version_id=template['version_id'],
            authority_namespace='release/happyranch', authority_generation=pin['generation'],
            authority_digest=pin['snapshot_digest'], request_digest=digest, activated_by=actor,
            state='active', created_at=created_at))]),
        'workflow_active_activations': (_reader_reserve(budget, 192) or [(_reader_reserve(budget, 448) or dict(instance_id=root, activation_id=activation_id, activation_revision=1))]),
        'workflow_activation_operations': (_reader_reserve(budget, 192) or [(_reader_reserve(budget, 704) or dict(org_slug=org, principal=actor,
            operation_key=command['operation_key'], request_digest=digest, activation_id=activation_id))]),
        'workflow_admission_records': (_reader_reserve(budget, 192) or [(_reader_reserve(budget, 704) or dict(id=authority['admission_record_id'], namespace=pin['namespace'],
            generation=pin['generation'], request_digest=digest, admitted_by=actor))]),
    })
    return (_reader_reserve(budget, 112) or (rows, context, binding))


def initial_scope(*, org: str, instance: str, attempt: str, command: dict,
                  roles: dict, context: dict, budget=None) -> dict:
    _reader_work((_reader_reserve(budget, 160) or (org, instance, attempt, command, roles, context,)), budget)
    scope = (_reader_reserve(budget, 2496) or dict(format='workflow-coding-scope@2', org_slug=org, instance_id=instance,
        attempt_id=attempt, stage='development', role='implementer',
        principal=roles['implementer']['principal'], team=roles['implementer']['team'],
        revision=1, attempt_sequence=1, assignment_generation=1, release=command['release'],
        mapping_digest=command['mapping']['digest'], context_id=identity('context', instance, budget=budget),
        authorization=context['authorization_id'], work_scope=command['work'],
        inputs=(_reader_reserve(budget, 64) or []), artifact=None, predecessor=None))
    from runtime.workflows._coding_settings_execution import _settings_family
    if _settings_family(command):
        _reader_update(scope, (_reader_reserve(budget, 448) or {'format': 'workflow-coding-scope@3', 'settings': command['settings'], 'mapping': command['mapping']}), budget=budget)
    return scope


def journal_rows(pins: dict, *, budget=None) -> list:
    _reader_work((_reader_reserve(budget, 80) or (pins,)), budget)
    records = (_reader_reserve(budget, 64) or [])
    for table, values in _reader_sorted(pins.items(), budget=budget):
        for row in values:
            if table == 'workflow_activation_operations':
                key = _reader_list((row[name] for name in ('org_slug', 'principal', 'operation_key')), budget=budget)
            else:
                key = row.get('id', row.get('namespace', row.get('instance_id')))
            _reader_append(records, _reader_reserve(budget, 448) or dict(table=table, id=key, record=encode_row(row, budget=budget)), budget=budget)
    return records


def effective_bindings(instance: str, roles: dict, *, budget=None) -> bytes:
    _reader_work((_reader_reserve(budget, 96) or (instance, roles,)), budget)
    return _canonical((_reader_reserve(budget, 448) or dict(format='workflow-coding-effective-bindings@1', instance_id=instance, bindings=roles)), reader_budget=budget)
