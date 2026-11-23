"""Exact reviewed legacy manifests; no filename inference or byte mutation."""
from __future__ import annotations

import hashlib
import json
import os
import stat
from datetime import timedelta
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from runtime.infrastructure.shared_artifact_references import reference_outcomes
from runtime.infrastructure.shared_artifact_service import (
    SharedArtifactError, _date, _iso,
)

MAX_DOCUMENT_BYTES = 32 * 1024 * 1024
MAX_ITEMS = 100_000
MANAGER_ID = 'engineering_manager'
ACCEPTANCE_PREFIX = 'SHARED_ARTIFACT_REVIEW_ACCEPTED '
Digest = Annotated[str, Field(pattern=r'^[0-9a-f]{64}$')]
Classification = Literal['default', 'reference', 'retire', 'undecided']


class Document(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class InventoryItem(Document):
    name: str
    sha256: Digest
    size_bytes: Annotated[int, Field(ge=0)]
    dependency_evidence: Annotated[list[str], Field(min_length=1)]
    owner_agent_id: str | None
    manager_agent_id: str | None
    origin_task_id: str | None
    origin_thread_id: str | None
    purpose: str | None
    classification: Classification
    reviewed_expires_at: str | None
    reference_reason: str | None

    @field_validator('name')
    @classmethod
    def exact_name(cls, value: str) -> str:
        # ArtifactStore owns native name validation at the runtime boundary.
        if not value or any(c in value for c in '*?[]'):
            raise ValueError('literal artifact name required')
        return value

    @field_validator('dependency_evidence')
    @classmethod
    def nonblank_evidence(cls, value: list[str]) -> list[str]:
        if any(not item.strip() for item in value):
            raise ValueError('nonblank dependency evidence required')
        return value

    @field_validator('reviewed_expires_at')
    @classmethod
    def utc_expiry(cls, value: str | None) -> str | None:
        if value is not None:
            _date(value)
        return value


class Inventory(Document):
    version: Literal[1]
    org: Annotated[str, Field(pattern=r'^[a-z0-9-]{1,40}$')]
    source_task_id: Literal['TASK-18706']
    items: Annotated[list[InventoryItem], Field(max_length=MAX_ITEMS)]

    @model_validator(mode='after')
    def unique_names(self) -> Inventory:
        if len({item.name for item in self.items}) != len(self.items):
            raise ValueError('duplicate inventory name')
        return self


class Decision(Document):
    name: str
    classification: Classification


class Acceptance(Document):
    task_id: Annotated[str, Field(pattern=r'^TASK-[0-9]+$')]
    result_id: Annotated[int, Field(gt=0)]


class Review(Document):
    version: Literal[1]
    org: Annotated[str, Field(pattern=r'^[a-z0-9-]{1,40}$')]
    inventory_digest: Digest
    decisions: Annotated[list[Decision], Field(max_length=MAX_ITEMS)]
    acceptance: Acceptance


def canonical(value: object) -> str:
    """Canonical UTF-8 JSON: sorted keys, compact separators, ordered arrays."""
    if isinstance(value, BaseModel):
        value = value.model_dump(mode='json')
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def digest(value: object) -> str:
    return hashlib.sha256(canonical(value).encode('utf-8')).hexdigest()


def approval_digest(review: Review) -> str:
    # Excluding the later result citation avoids a circular acceptance digest.
    return digest(review.model_dump(mode='json', exclude={'acceptance'}))


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('duplicate JSON key')
        value[key] = item
    return value


def read_document(path: Path, model: type[Document]) -> Document:
    """Bounded, no-follow final-file read; reject ambiguous JSON and aliases."""
    if not path.is_absolute():
        raise ValueError('absolute document path required')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > MAX_DOCUMENT_BYTES:
            raise ValueError('document must be a bounded regular file')
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            raw = stream.read(MAX_DOCUMENT_BYTES + 1)
        after = os.fstat(fd)
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns) or len(raw) > MAX_DOCUMENT_BYTES:
            raise ValueError('document changed or exceeded bound')
    finally:
        os.close(fd)
    return model.model_validate(json.loads(raw, object_pairs_hook=_unique_object))


def validate_pair(inventory: Inventory, review: Review, slug: str) -> None:
    expected = [(item.name, item.classification) for item in inventory.items]
    actual = [(item.name, item.classification) for item in review.decisions]
    if inventory.org != slug or review.org != slug or review.inventory_digest != digest(inventory) or actual != expected:
        raise ValueError('inventory_review_mismatch')


def authenticated_review(conn: object, inventory: Inventory, review: Review, *, now: object) -> dict:
    """Read a session-bound manager result from the existing completion store.

    Reviewer labels in JSON are never credentials. Task completion intake owns
    agent/session authentication; this reader cannot create acceptance records.
    """
    validate_pair(inventory, review, inventory.org)
    row = conn.execute(
        'SELECT r.*,t.assigned_agent,t.current_session_id,t.status AS task_status '
        'FROM task_results r JOIN tasks t ON t.id=r.task_id WHERE r.id=? AND r.task_id=?',
        (review.acceptance.result_id, review.acceptance.task_id),
    ).fetchone()
    marker = ACCEPTANCE_PREFIX + approval_digest(review)
    if (not row or row['assigned_agent'] != MANAGER_ID or row['agent'] != MANAGER_ID
        or row['status'] != 'completed' or row['task_status'] != 'completed'
        or not row['session_id'] or row['current_session_id'] != row['session_id']
        or marker not in (row['output_summary'] or '').splitlines()):
        raise ValueError('authenticated_engineering_manager_acceptance_required')
    reviewed_at = _date(row['created_at'])
    if reviewed_at > now:
        raise ValueError('manager_review_time_invalid')
    return dict(review_id=digest(review), inventory_digest=digest(inventory),
                reviewer_agent_id=MANAGER_ID, reviewer_task_id=row['task_id'],
                ruling_citation=f"task:{row['task_id']}/result:{row['id']}",
                decisions_json=canonical(review), reviewed_at=_iso(reviewed_at))


def classification_gate(conn: object, now: object) -> str | None:
    state = conn.execute('SELECT * FROM shared_artifact_org_state WHERE id=1').fetchone()
    if not state or not state['rollout_started_at']:
        return 'first_complete_preview_required'
    if now < _date(state['rollout_started_at']) + timedelta(days=7):
        return 'preview_seven_days_required'
    if not state['preview_acceptance']:
        return 'preview_acceptance_required'
    return None


def item_preview(org: object, conn: object, item: InventoryItem, *, deadline: float,
                 reference: tuple[str, list[str]] | None = None) -> tuple[dict, dict | None]:
    """Revalidate an exact item inside its retained writer transaction."""
    svc = org.db._shared_artifact_service
    result = dict(name=item.name, classification=item.classification, outcome='unclassified',
                  reason=None, manager_agent_id=item.manager_agent_id, metadata=None)
    metadata = None
    try:
        svc.store.validate_name(item.name)
        row = org.db._shared_artifact_uncommitted(conn, item.name)
        if not row or row['state'] != 'legacy_unclassified':
            result['reason'] = 'legacy_metadata_required'
            return result, None
        # Use literal ancestry: path_for resolves aliases and could hide a link.
        observed = svc.identity(svc.store.root / item.name, deadline=deadline)
        if (observed['sha256'], observed['size_bytes']) != (item.sha256, item.size_bytes) or (
            row['sha256'], row['size_bytes']) != (item.sha256, item.size_bytes):
            result['reason'] = 'inventory_bytes_changed'
            return result, None
        if conn.execute("SELECT 1 FROM shared_artifact_operations WHERE name=? AND phase NOT IN ('committed','aborted')", (item.name,)).fetchone():
            result['reason'] = 'artifact_operation_pending'
            return result, None
        outcome, refs = reference if reference is not None else reference_outcomes(
            org, conn, [row], deadline=deadline, origin=org.reference_origin)[row['name'], row['generation']]
        result.update(reference_outcome=outcome, references=refs)
        if outcome == 'indeterminate':
            result['reason'] = 'dependency_check_unavailable'
        elif item.classification == 'undecided':
            result['reason'] = 'review_undecided'
        elif not item.owner_agent_id or item.owner_agent_id not in svc.known_agents:
            result['reason'] = 'unknown_owner'
        elif (not item.manager_agent_id or item.manager_agent_id not in org.manager_ids
              or item.manager_agent_id not in svc.known_agents):
            result['reason'] = 'unknown_manager'
        elif item.classification == 'retire' and outcome != 'absent':
            result['reason'] = 'live_dependency'
        else:
            retention = 'reference' if item.classification == 'reference' else 'default'
            if item.classification != 'retire' and item.reviewed_expires_at is None:
                result['reason'] = 'explicit_reviewed_expiry_required'
                return result, None
            if item.classification == 'retire' and item.reviewed_expires_at is not None:
                result['reason'] = 'retire_has_no_retention_grant'
                return result, None
            # Metadata validates same-org origins and bounded UTC expiry. Retire
            # supplies no grant: it enters A's recovery transition in the same TX.
            if item.classification == 'retire':
                if (not item.purpose or not item.purpose.strip()
                    or bool(item.origin_task_id) == bool(item.origin_thread_id)
                    or (item.origin_task_id and org.db.get_task(item.origin_task_id) is None)
                    or (item.origin_thread_id and org.db.get_thread(item.origin_thread_id) is None)):
                    raise ValueError('complete_attribution_required')
                metadata = dict(owner_kind='agent', owner=item.owner_agent_id,
                    owner_agent_id=item.owner_agent_id, origin_task_id=item.origin_task_id,
                    origin_thread_id=item.origin_thread_id, purpose=item.purpose.strip(),
                    retention_class=retention, reference_reason=None, created_at=_iso(svc.clock()),
                    expires_at=None, requested_expires_at=None, state='active', attribution_status='attributed')
            else:
                metadata = svc.metadata(agent=item.owner_agent_id, task_id=item.origin_task_id,
                    thread_id=item.origin_thread_id, purpose=item.purpose, retention=retention,
                    reason=item.reference_reason, expires_at=item.reviewed_expires_at)
                # A's classification seam intentionally accepts only attribution
                # columns, not the canonical-source writer's system-derived flag.
                metadata.pop('system_derived')
            result.update(outcome='eligible', metadata=metadata)
    except FileNotFoundError:
        result['reason'] = 'inventory_bytes_missing'
    except SharedArtifactError as exc:
        result['reason'] = exc.detail['code']
    except (OSError, ValueError):
        result['reason'] = 'item_validation_unavailable'
    return result, metadata
