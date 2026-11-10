"""Inert U4 repository observations; saved syntax never establishes access.

U4b owns durable run binding and consumption. No reader probes Git, authenticates
an issuer, grants editing or changes sequential state.
"""
from __future__ import annotations

import hashlib
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from runtime.workflows.coding_saved_records import _paths
from runtime.workflows.repository_artifacts import Component, RepositoryId, _oid
from runtime.workflows.sequential_contracts import Closed, Digest, Positive, Token
from runtime.workflows.sequential_definition import _json
from runtime.workflows.sequential_history import _bytes

RECEIPT_LIMIT = 16384
Scope = Annotated[tuple[Annotated[str, Field(max_length=4096)], ...], Field(min_length=1, max_length=64)]


class RepositoryPreflightRequest(Closed):
    """The live task's declaration, not machine proof of a permission grant."""

    repo_key: Component
    base: Annotated[str, Field(min_length=1, max_length=128)]
    scope: Scope


class RepositoryChangeSubject(Closed):
    repository_id: RepositoryId
    repo_key: Component
    origin_digest: Digest
    object_format: Literal['sha1']
    base: str
    scope: Scope

    @model_validator(mode='after')
    def supported(self) -> Self:
        _oid(self.base, self.object_format)
        _paths(list(self.scope))
        if (tuple(sorted(set(self.scope))) != self.scope
                or sum(len(path.encode('utf8')) for path in self.scope) > 8192
                or len(_bytes(self.model_dump())) > 12000):
            raise ValueError('sequential_repository_scope_invalid')
        return self


class RepositoryPreflightCause(Closed):
    code: Literal['unsupported_repository_format', 'unsupported_repository_scope',
                  'repository_unavailable', 'repository_changed']
    next_owner: Literal['accountable_manager']
    action: Literal['resolve_cause_before_editing_no_automatic_retry']
    supported_object_format: Literal['sha1'] = 'sha1'


class RepositoryPreflightReceipt(Closed):
    format: Literal['workflow-sequential-repository-preflight@1']
    org_slug: Annotated[str, Field(pattern=r'^[a-z][a-z0-9-]{0,62}$')]
    run_id: Token
    visit_id: Token
    task_id: Token
    principal: Component
    session_id: Token
    definition_digest: Digest
    start_digest: Digest
    authority_generation: Positive
    authority_digest: Digest
    request_digest: Digest
    disposition: Literal['resolved', 'Held']
    subject: RepositoryChangeSubject | None
    cause: RepositoryPreflightCause | None

    @model_validator(mode='after')
    def closed_disposition(self) -> Self:
        if ((self.disposition == 'resolved') != (self.subject is not None)
                or (self.disposition == 'Held') != (self.cause is not None)):
            raise ValueError('sequential_repository_disposition_invalid')
        if self.subject is not None:
            subject = self.subject
            expected = 'workflow-repository:' + hashlib.sha256(_bytes(
                [self.org_slug, subject.repo_key, subject.origin_digest])).hexdigest()
            if subject.repository_id != expected:
                raise ValueError('sequential_repository_identity_invalid')
        return self


def read_repository_preflight(raw: bytes) -> RepositoryPreflightReceipt:
    """Bounded canonical, duplicate-free historical reader, with no I/O."""
    # JSON-mode is needed for deeply immutable tuple fields. _json first checks
    # duplicate keys, finite JSON, UTF8 and the original byte-size bound.
    _json(raw, RECEIPT_LIMIT)
    value = RepositoryPreflightReceipt.model_validate_json(raw, strict=True)
    if _bytes(value.model_dump()) != raw:
        raise ValueError('sequential_repository_receipt_not_canonical')
    return value
