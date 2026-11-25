"""Strict wire contracts for org-local task attributes (no UI or producers)."""
from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictFloat, StrictInt, StrictStr, model_validator


class Closed(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


Scalar = StrictInt | StrictFloat | StrictStr | StrictBool
Positive = Annotated[StrictInt, Field(ge=1)]
Revision = Annotated[StrictInt, Field(ge=0)]


class CreateDefinition(Closed):
    expected_version: Literal[0]
    key: StrictStr
    type: Literal["numeric", "string", "boolean"]
    name: StrictStr
    description: StrictStr | None = None
    constraints: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def native_zero(cls, value):
        if isinstance(value, dict) and type(value.get("expected_version")) is not int:
            raise ValueError("expected_version")
        return value


class UpdateDefinition(Closed):
    expected_version: Positive
    key: StrictStr | None = None
    type: Literal["numeric", "string", "boolean"] | None = None
    name: StrictStr | None = None
    description: StrictStr | None = None
    constraints: dict[str, Any] | None = None

    @model_validator(mode="before")
    @classmethod
    def changes_required(cls, value):
        if isinstance(value, dict):
            if not value.keys() - {"expected_version"}:
                raise ValueError("changes_required")
            if any(value.get(key) is None for key in ("key", "type", "name", "constraints") if key in value):
                raise ValueError("null_metadata")
        return value


class ArchiveDefinition(Closed):
    expected_version: Positive


class Change(Closed):
    definition_id: StrictStr
    expected_version: Positive
    expected_revision: Revision
    note: StrictStr | None = None


class SetValue(Change):
    op: Literal["set"]
    value: Scalar


class ClearValue(Change):
    op: Literal["clear"]


class SaveAttributes(Closed):
    changes: Annotated[list[Annotated[SetValue | ClearValue, Field(discriminator="op")]], Field(min_length=1)]


class Reason(Closed):
    code: str


class Editability(Closed):
    can_edit: bool
    refusal_reason: Reason | None


class DefinitionEditability(Closed):
    can_create: bool
    can_edit_metadata: bool
    can_edit_structure: bool
    can_archive: bool
    refusal_reason: Reason | None


class Definition(Closed):
    definition_id: str
    version: int
    key: str
    type: Literal["numeric", "string", "boolean"]
    name: str
    description: str | None
    constraints: dict[str, Any]
    created_at: str
    recorded_at: str
    first_used_at: str | None
    archived_at: str | None
    archived: bool


class DefinitionRead(Closed):
    definition: Definition
    editability: DefinitionEditability


class DefinitionsRead(Closed):
    definitions: list[Definition]
    editability: DefinitionEditability


class Absent(Closed):
    present: Literal[False]


class Present(Closed):
    present: Literal[True]
    value: Scalar


Presence = Annotated[Absent | Present, Field(discriminator="present")]


class CurrentAbsent(Absent):
    revision: int
    definition_version: int | None = None
    updated_at: str | None = None


class CurrentPresent(Present):
    revision: int
    definition_version: int
    updated_at: str


class InitialSet(Closed):
    state: Literal["set"]
    value: Scalar
    definition_version: int
    definition: Definition


class InitialUnset(Closed):
    state: Literal["unset"]


class InitialUnknown(Closed):
    state: Literal["unknown"]


class AdmissionRecorded(Closed):
    state: Literal["recorded"]
    actor_id: str
    actor_kind: str
    at: str


class AdmissionUnavailable(Closed):
    state: Literal["unavailable"]


class Actor(Closed):
    canonical_id: str
    kind: str
    addressable_name: str | None


class Event(Closed):
    event_id: int
    definition_id: str
    definition_version: int
    definition: Definition
    revision: int
    actor_id: str
    actor_kind: str
    actor: Actor
    at: str
    source: Literal["admission", "edit", "clear"]
    old: Presence
    new: Presence
    note: str | None = None


class History(Closed):
    history_state: Literal["complete", "incomplete", "unavailable"]
    events: list[Event]


class AttributeRead(Closed):
    definition: Definition
    current: Annotated[CurrentAbsent | CurrentPresent, Field(discriminator="present")]
    initial: Annotated[InitialSet | InitialUnset | InitialUnknown, Field(discriminator="state")]
    admission: Annotated[AdmissionRecorded | AdmissionUnavailable, Field(discriminator="state")]
    provenance: Literal["supplied_at_admission", "not_supplied_before_dispatch", "definition_added_later", "timing_unknown"]
    history: History
    editability: Editability


class TaskAttributesRead(Closed):
    task_id: str
    editability: Editability
    attributes: list[AttributeRead]


class TaskHistoryRead(History):
    task_id: str


class RevisionConflict(Closed):
    definition_id: str
    expected_revision: int
    current_revision: int


class ErrorDetail(Closed):
    code: str
    errors: dict[str, list[str]] | None = None
    current: DefinitionRead | TaskAttributesRead | None = None
    conflicts: list[RevisionConflict] | None = None


class AttributeErrorResponse(Closed):
    detail: ErrorDetail


def body_schema(model: type[BaseModel]) -> dict:
    schema = model.model_json_schema()
    definitions = schema.pop("$defs", {})

    def inline(value):
        if isinstance(value, dict):
            if "propertyName" in value and "mapping" in value:
                return {"propertyName": value["propertyName"]}
            if "$ref" in value:
                return inline(definitions[value["$ref"].rsplit("/", 1)[-1]])
            return {key: inline(item) for key, item in value.items()}
        if isinstance(value, list):
            return [inline(item) for item in value]
        return value
    return {"requestBody": {"required": True, "content": {"application/json": {"schema": inline(schema)}}}}
