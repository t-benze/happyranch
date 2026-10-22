"""Canonical K3a agent-read records, independent of document/custody encodings."""
from __future__ import annotations

import base64

from runtime.workflows.coding_saved_records import _canonical, _check, _hash


def identity(kind: str, *parts: object) -> str:
    return 'coding-' + kind + ':' + _hash(_canonical(parts))


def encode_row(row: dict) -> dict:
    # SQL BLOB and TEXT remain distinct, including empty values.
    return {k: {'sql_blob_base64': base64.b64encode(v).decode('ascii')}
            if type(v) is bytes else v for k, v in row.items()}


def decode_row(value: dict) -> dict:
    return {k: base64.b64decode(v['sql_blob_base64'], validate=True)
            if type(v) is dict and set(v) == {'sql_blob_base64'} else v for k, v in value.items()}


def task_brief(work: dict) -> str:
    return ('Coding development is queued. Downstream coding execution is unavailable.\n'
            'The pinned repository, object format and comparison base are immutable; '
            'no developer head or approval exists.\n' + work['brief'])


def review_brief(work: dict) -> str:
    return ('Independently review the exact coding subject in the bound supplement.\n'
            'Use existing authorized repository tools; unavailable access requires BLOCK.\n'
            'Return genuine APPROVE, REQUEST_CHANGES or BLOCK with findings.\n' + work['brief'])


def review_scope(*, org: str, instance: str, command: dict, roles: dict,
                 developer: dict, subject: dict) -> dict:
    """Same-team first-edge scope; unrelated cross-team @3 pins stay reserved."""
    return dict(format='workflow-coding-review-scope@1', org_slug=org, instance_id=instance,
        attempt_id=identity('attempt', instance, 2), stage='code_review', role='code_reviewer',
        principal=roles['code_reviewer']['principal'], team=roles['code_reviewer']['team'],
        revision=1, attempt_sequence=1, assignment_generation=1, release=command['release'],
        mapping_digest=command['mapping']['digest'], context_id=identity('context', instance),
        authorization=identity('authorization', instance), work_scope=command['work'],
        subject=subject, developer=dict(attempt_id=developer['id'], task_id=developer['task_id'],
            session_id=developer['session_id'], result_id=developer['result_id'], principal=developer['principal']))


def qa_brief(work: dict) -> str:
    return ('Independently QA the exact produced and approved coding subject in the bound supplement.\n'
            'Use existing authorized repository tools; unavailable access or proof requires BLOCK.\n'
            'Return genuine PASS, REVISE or BLOCK with your own findings.\n' + work['brief'])


def qa_scope(*, org: str, instance: str, command: dict, roles: dict, developer: dict,
             reviewer: dict, subject: dict, task_id: str) -> dict:
    return dict(format='workflow-coding-qa-scope@1', org_slug=org, instance_id=instance,
        attempt_id=identity('attempt', instance, 3), task_id=task_id, stage='qa', role='qa_engineer',
        principal=roles['qa_engineer']['principal'], team=roles['qa_engineer']['team'],
        revision=1, attempt_sequence=1, assignment_generation=1, release=command['release'],
        mapping=command['mapping'], mapping_digest=command['mapping']['digest'], context_id=identity('context', instance),
        authorization=identity('authorization', instance), work_scope=command['work'], subject=subject,
        developer=dict(attempt_id=developer['id'], task_id=developer['task_id'],
            session_id=developer['session_id'], result_id=developer['result_id'],
            principal=developer['principal'], outcome='produced', subject=subject),
        reviewer=dict(attempt_id=reviewer['id'], task_id=reviewer['task_id'],
            session_id=reviewer['session_id'], result_id=reviewer['result_id'],
            principal=reviewer['principal'], outcome='APPROVE', subject=subject))


def pinned_rows(*, org: str, command: dict, source: dict, native_source: dict,
                authority: dict, mapping: dict, roles: dict, template: dict,
                carrier_id: str, instance_id: str, created_at: str) -> tuple[dict, dict, dict]:
    """One explicit @2 branch; historical @1 custody bytes keep their readers."""
    root = identity('root', org, source['task_id'], source['result']['id'], _hash(_canonical(command)))
    _check(root == instance_id)
    activation_id = identity('activation', root)
    authorization_id = identity('authorization', root)
    binding_id = identity('binding', root)
    context_id = identity('context', root)
    namespace = f'org/{org}/workflow-instance/{root}'
    reference = next(r for r in mapping['repository_references']
                     if r['repository_id'] == command['work']['repository_id'])
    authorization = dict(format='workflow-coding-authorization@2', instance_id=root,
        owner_principal='agent:' + source['principal'], source=source, template=template,
        authority=authority, work_scope=command['work'], repository_reference=reference,
        budgets=source['budgets'], created_at=created_at)
    binding = dict(format='workflow-coding-binding@2', instance_id=root,
        template_version_id=template['version_id'], authorization_revision_id=authorization_id,
        mapping_id=command['mapping']['id'], mapping_digest=command['mapping']['digest'],
        bindings=roles, eligible_replacements=mapping['eligible_replacements'], repository_reference=reference)
    context = dict(format='workflow-coding-context@2', org_slug=org, release=command['release'],
        mapping=command['mapping'], bindings=roles, eligible_replacements=mapping['eligible_replacements'],
        repository_reference=reference, work_scope=command['work'], authority=authority,
        source=source, native_source=native_source, authorization_id=authorization_id,
        binding_snapshot_id=binding_id, activation_id=activation_id, activation_revision=1)
    grant, bound, ctx = map(_canonical, (authorization, binding, context))
    pin = authority['pin']
    digest = _hash(_canonical(command))
    actor = 'agent:' + source['principal']
    rows = {
        'workflow_authorization_revisions': [dict(id=authorization_id, namespace=namespace,
            revision=1, authority_bytes=grant, authority_digest=_hash(grant),
            source_pin='workflow-coding-agent-read-start@1', created_at=created_at)],
        'workflow_active_authorizations': [dict(namespace=namespace, authorization_revision_id=authorization_id)],
        'workflow_binding_snapshots': [dict(id=binding_id, template_version_id=template['version_id'],
            authorization_revision_id=authorization_id, binding_bytes=bound,
            binding_digest=_hash(bound), created_at=created_at)],
        'workflow_contexts': [dict(id=context_id, binding_snapshot_id=binding_id, context_bytes=ctx,
            context_digest=_hash(ctx), source_kind='coding-manager-result', source_id=str(source['result']['id']))],
        'workflow_instances': [dict(id=root, binding_snapshot_id=binding_id, context_id=context_id,
            root_task_id=carrier_id, owner_principal=actor, status='draft')],
        'workflow_activations': [dict(id=activation_id, instance_id=root, activation_revision=1,
            template_identity_id=template['identity_id'], template_version_id=template['version_id'],
            authority_namespace='release/happyranch', authority_generation=pin['generation'],
            authority_digest=pin['snapshot_digest'], request_digest=digest, activated_by=actor,
            state='active', created_at=created_at)],
        'workflow_active_activations': [dict(instance_id=root, activation_id=activation_id, activation_revision=1)],
        'workflow_activation_operations': [dict(org_slug=org, principal=actor,
            operation_key=command['operation_key'], request_digest=digest, activation_id=activation_id)],
        'workflow_admission_records': [dict(id=authority['admission_record_id'], namespace=pin['namespace'],
            generation=pin['generation'], request_digest=digest, admitted_by=actor)],
    }
    return rows, context, binding


def initial_scope(*, org: str, instance: str, attempt: str, command: dict,
                  roles: dict, context: dict) -> dict:
    return dict(format='workflow-coding-scope@2', org_slug=org, instance_id=instance,
        attempt_id=attempt, stage='development', role='implementer',
        principal=roles['implementer']['principal'], team=roles['implementer']['team'],
        revision=1, attempt_sequence=1, assignment_generation=1, release=command['release'],
        mapping_digest=command['mapping']['digest'], context_id=identity('context', instance),
        authorization=context['authorization_id'], work_scope=command['work'],
        inputs=[], artifact=None, predecessor=None)


def journal_rows(pins: dict) -> list:
    records = []
    for table, values in sorted(pins.items()):
        for row in values:
            if table == 'workflow_activation_operations':
                key = [row[name] for name in ('org_slug', 'principal', 'operation_key')]
            else:
                key = row.get('id', row.get('namespace', row.get('instance_id')))
            records.append(dict(table=table, id=key, record=encode_row(row)))
    return records


def effective_bindings(instance: str, roles: dict) -> bytes:
    return _canonical(dict(format='workflow-coding-effective-bindings@1', instance_id=instance, bindings=roles))
