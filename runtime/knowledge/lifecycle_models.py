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
