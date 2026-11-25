"""Roots read preparation, bounded export encoding and served wire schemas."""
from __future__ import annotations

import csv
import io
import json
import re
from datetime import datetime, timezone
from itertools import islice
from typing import Annotated, Any, Literal

from fastapi import HTTPException, Query
from pydantic import BaseModel, BeforeValidator, ConfigDict, Field

from runtime.daemon.content_disposition import attachment_content_disposition
from runtime.daemon.routes.task_attribute_models import AttributeErrorResponse, Presence
from runtime.task_attribute_filters import AttributeFilter, AttributeFilterError, parse_attribute_filters, resolve_attribute_filters

MAX_EXPORT_BYTES = 8 * 1024 * 1024
FIXED_COLUMNS = ["task_id", "brief_excerpt", "status", "assigned_agent", "created_at"]


class RootListRow(BaseModel):
    model_config = ConfigDict(extra="allow")
    task_id: str
    attribute_values: dict[str, Presence]


class RootListRead(BaseModel):
    tasks: list[RootListRow]
    next_cursor: str | None


class ExportDefinition(BaseModel):
    key: str
    definition_id: str
    version: int
    type: Literal["numeric", "string", "boolean"]
    name: str
    archived: bool


class ExportTask(BaseModel):
    task_id: str
    brief_excerpt: str
    status: str
    assigned_agent: str | None
    created_at: str
    attribute_values: dict[str, Presence]


class RootsExportRead(BaseModel):
    format: Literal["happyranch.task-roots-export.v1"]
    columns: list[str]
    attribute_definitions: list[ExportDefinition]
    tasks: list[ExportTask]
    limit: int = Field(20, ge=1, le=1000)
    page_full: bool
    next_cursor: str | None


READ_ERRORS = {code: {"model": AttributeErrorResponse, "description": description}
               for code, description in {
                   401: "Existing bearer read authority", 404: "Unknown org", 409: "Org unavailable",
                   422: "Invalid attribute filters or export controls", 503: "Read unavailable",
               }.items()}
LIST_RESPONSES = {200: {"model": RootListRead}, **READ_ERRORS}
EXPORT_RESPONSES = {
    **READ_ERRORS, 413: {"model": AttributeErrorResponse, "description": "Export column or encoded byte cap"},
    200: {"model": RootsExportRead, "content": {"text/csv": {"schema": {"type": "string"}}},
          "headers": {name: {"schema": {"type": "string"}, "description": description}
                      for name, description in {
                          "Content-Disposition": "UTF-8 attachment filename",
                          "X-HappyRanch-Page-Full": "true iff returned rows equal the limit",
                          "X-HappyRanch-Export-Limit": "Requested page size",
                          "X-HappyRanch-Next-Cursor": "Last ID of a full page; absent otherwise",
                      }.items()}},
}


def root_attribute_filters(
    attr_min: Annotated[list[str] | None, Query(description="Repeat KEY=NUMBER; inclusive finite JSON numeric lower bound.")] = None,
    attr_max: Annotated[list[str] | None, Query(description="Repeat KEY=NUMBER; inclusive finite JSON numeric upper bound.")] = None,
    attr_set: Annotated[list[str] | None, Query(description="Repeat current KEY; present, including zero/false/empty.")] = None,
    attr_not_set: Annotated[list[str] | None, Query(description="Repeat current KEY; absent or cleared.")] = None,
) -> tuple[AttributeFilter, ...]:
    try:
        return parse_attribute_filters([(operator, raw) for operator, values in (
            ("attr_min", attr_min), ("attr_max", attr_max), ("attr_set", attr_set), ("attr_not_set", attr_not_set))
            for raw in values or []])
    except AttributeFilterError as exc:
        raise HTTPException(422, detail=exc.detail) from exc


def prepare_root_filters(db: Any, filters: tuple[AttributeFilter, ...]) -> tuple[tuple[AttributeFilter, ...], list[dict]]:
    definitions = db.root_attribute_definitions([item.key for item in filters])
    return resolve_attribute_filters(filters, definitions), definitions


def _export_limit(value: Any) -> int:
    text = str(value)
    if not re.fullmatch(r"[0-9]{1,4}", text) or not 1 <= int(text) <= 1000:
        raise HTTPException(422, detail={"code": "task_export_invalid", "errors": {"limit": ["integer_1_1000"]}})
    return int(text)


ExportLimit = Annotated[int, Query(ge=1, le=1000, description="Export page size, integer 1..1000."),
                        BeforeValidator(_export_limit)]


def export_controls(query: Any, limit: int, format: str) -> tuple[int, str]:
    for field in ("limit", "format"):
        if len(query.getlist(field)) > 1:
            raise HTTPException(422, detail={"code": "task_export_invalid", "errors": {field: ["duplicate_parameter"]}})
    if format not in ("json", "csv"):
        raise HTTPException(422, detail={"code": "task_export_invalid", "errors": {"format": ["expected_json_or_csv"]}})
    return limit, format


def _too_large(reason: str) -> None:
    raise HTTPException(413, detail={"code": "task_export_too_large", "errors": {"export": [reason]}})


def _json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")


def _csv_cell(value: Any) -> str:
    if value is None:
        text = ""
    elif type(value) is bool:
        text = "true" if value else "false"
    else:
        text = str(value)
    return "'" + text if text and text[0] in "=+-@\t\r" else text


def encode_roots_export(db: Any, *, filters: tuple[AttributeFilter, ...], limit: int,
                        format: str, selection: dict) -> tuple[bytes, str | None, bool]:
    """Called inside ONE request view; no bytes escape before all caps pass."""
    definitions = db.root_attribute_definitions([item.key for item in filters], for_export=True)
    if len(definitions) > 256:
        _too_large("too_many_attribute_columns")
    keys = [d["key"] for d in definitions]
    columns = FIXED_COLUMNS + ["attribute:" + key for key in keys]
    body = bytearray()

    def append(data: bytes) -> None:
        if len(body) + len(data) > MAX_EXPORT_BYTES:
            _too_large("encoded_body_too_large")
        body.extend(data)

    def csv_row(cells: list) -> None:
        row = io.StringIO(newline="")
        csv.writer(row, lineterminator="\r\n").writerow([_csv_cell(value) for value in cells])
        append(row.getvalue().encode("utf-8"))

    if format == "json":
        prefix = {"format": "happyranch.task-roots-export.v1", "columns": columns,
                  "attribute_definitions": definitions}
        append(_json(prefix)[:-1] + b',"tasks":[')
    else:
        csv_row(columns)
    count, last_id = 0, None
    rows = db.iter_roots_export(limit=limit, attribute_filters=filters, **selection)
    try:
        while batch := list(islice(rows, 8)):
            values = db.root_attribute_values([row["task_id"] for row in batch], definitions)
            for row in batch:
                row["attribute_values"] = values[row["task_id"]]
                if format == "json":
                    append((b"," if count else b"") + _json(row))
                else:
                    csv_row([row[column] for column in FIXED_COLUMNS] +
                            [values[row["task_id"]][key].get("value") for key in keys])
                count += 1
                last_id = row["task_id"]
    finally:
        rows.close()
    page_full = count == limit
    next_cursor = last_id if page_full else None
    if format == "json":
        append(b"]," + _json({"limit": limit, "page_full": page_full, "next_cursor": next_cursor})[1:])
    return bytes(body), next_cursor, page_full


def export_headers(slug: str, format: str, limit: int, cursor: str | None,
                   page_full: bool, started: datetime) -> dict[str, str]:
    safe_slug = re.sub(r"[^A-Za-z0-9_-]", "_", slug)[:64] or "org"
    stamp = started.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    headers = {"Content-Disposition": attachment_content_disposition(f"task-roots-{safe_slug}-{stamp}.{format}"),
               "Cache-Control": "no-store", "X-HappyRanch-Export-Limit": str(limit),
               "X-HappyRanch-Page-Full": str(page_full).lower()}
    if cursor is not None:
        headers["X-HappyRanch-Next-Cursor"] = cursor
    return headers
