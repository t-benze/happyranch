"""Public Distill contracts. Internal captures are never response fields."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from runtime.knowledge.curation import CuratedItem, Disposition
from runtime.knowledge.sources import Coverage


class DistillSubmission(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    submission_id: str = Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$')
    source_id: str = Field(pattern=r'^[a-z0-9_]{1,64}$')
    confirmed: Literal[True]
    processor_id: str | None = Field(default=None, pattern=r'^[a-z0-9_]{1,64}$')

    @field_validator('confirmed', mode='before')
    @classmethod
    def require_confirmation(cls, value: object) -> object:
        if value is not True:
            raise ValueError('explicit_confirmation_required')
        return value


class DistillOperation(BaseModel):
    operation_id: str
    submission_id: str
    source_id: str
    state: Literal['Queued', 'Processing', 'Ready', 'Failed']
    phase: str
    cutoff: str
    coverage: list[Coverage]
    requested_processor_id: str | None
    processor_id: str | None
    task_id: str | None
    session_id: str | None
    result_id: int | None
    created_at: str
    updated_at: str
    failure: dict[str, str | int] | None
    archive_id: str | None
    archive_version: int | None


class ArchiveInventoryEntry(BaseModel):
    reference_id: str
    category: str
    sha256: str
    relative_path: str | None = None
    metadata: dict | None = None


class ArchiveManifest(BaseModel):
    format: Literal['knowledge-archive@1']
    org_slug: str
    archive_id: str
    version: int
    operation_id: str
    source_id: str
    source_name: str
    source_team: str
    source_provider: str
    cutoff: str
    processed_at: str
    processor_id: str
    task_id: str
    session_id: str
    result_id: int
    capture_digest: str
    explicitly_empty: bool
    coverage: list[Coverage]
    errors: list[str]
    dispositions: list[Disposition]
    category_counts: dict[str, int]
    disposition_counts: dict[str, int]
    eligible_count: int
    inventory: list[ArchiveInventoryEntry]
    item_inventory: list[str]


class KnowledgeArchive(BaseModel):
    archive_id: str
    org_slug: str
    source_id: str
    version: int
    operation_id: str
    created_at: str
    manifest: ArchiveManifest
    items: list[CuratedItem]


class ArchiveSelection(BaseModel):
    archive_id: str
    version: int
    operation_id: str
    source_id: str
    source_name: str
    source_team: str
    source_provider: str
    created_at: str
    cutoff: str
    processor_id: str
    item_count: int
    eligible_count: int
    coverage: list[Coverage]


class ArchiveList(BaseModel):
    archives: list[ArchiveSelection]


class InstillConfirmation(BaseModel):
    """Original key and exact selection; replay never expands the selection."""
    model_config = ConfigDict(extra='forbid', strict=True)
    submission_id: str = Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$')
    archive_id: str = Field(min_length=1, max_length=128)
    archive_version: int = Field(gt=0)
    item_ids: list[str] = Field(min_length=1)
    confirmed: Literal[True]

    @field_validator('confirmed', mode='before')
    @classmethod
    def require_confirmation(cls, value: object) -> object:
        if value is not True:
            raise ValueError('explicit_confirmation_required')
        return value

    @field_validator('item_ids')
    @classmethod
    def unique_selection(cls, value: list[str]) -> list[str]:
        if any(not v for v in value) or len(set(value)) != len(value):
            raise ValueError('unique_nonempty_item_ids_required')
        return value


class ImportItemResult(BaseModel):
    item_id: str
    outcome: Literal['Pending', 'Imported', 'Skipped', 'Failed']
    memory_id: str | None = None
    reason: str | None = None
    held: bool = False
    retryable: bool = False


class ImportOperation(BaseModel):
    operation_id: str
    submission_id: str
    archive_id: str
    archive_version: int
    source_id: str
    recipient_id: str
    item_ids: list[str]
    state: Literal['Queued', 'Processing', 'Succeeded', 'Partially failed', 'Failed']
    phase: str
    created_at: str
    updated_at: str
    started_at: str | None
    finished_at: str | None
    selected: int
    imported: int
    skipped: int
    failed: int
    pending: int
    results: list[ImportItemResult]
