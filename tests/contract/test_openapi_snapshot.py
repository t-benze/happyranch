"""Snapshot test: pins the daemon's OpenAPI schema.

When a daemon route changes (added/removed/renamed/method change), this test
fails. To accept the new schema, regenerate the snapshot:

    HAPPYRANCH_REGEN_OPENAPI=1 uv run pytest tests/contract/test_openapi_snapshot.py

The snapshot is the single source of truth that the TS contract coverage test
(``web/src/test/openapi-coverage.test.ts``) reads.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from runtime.config import Settings
from runtime.daemon.app import create_app
from runtime.daemon.state import DaemonState

SNAPSHOT_PATH = Path(__file__).parent / "openapi.json"


def _summarize(schema: dict) -> dict:
    """Reduce the schema to only the surface area we want to pin.

    Full schemas include FastAPI-generated component refs that churn on every
    Pydantic upgrade — too noisy. We pin paths + methods + parameter names +
    response codes. That's the contract the TS client cares about.
    """
    paths: dict = {}
    for path, methods in sorted(schema.get("paths", {}).items()):
        path_summary: dict = {}
        for method, op in sorted(methods.items()):
            if method.upper() not in {"GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"}:
                continue
            params = sorted(
                [p["name"], p.get("in")]
                for p in op.get("parameters", [])
            )
            responses = sorted(op.get("responses", {}).keys())
            path_summary[method.upper()] = {
                "params": params,
                "responses": responses,
            }
        if path_summary:
            paths[path] = path_summary
    return {"paths": paths}


def test_openapi_snapshot_matches() -> None:
    app = create_app(DaemonState.idle(Settings()))
    current = _summarize(app.openapi())

    if os.environ.get("HAPPYRANCH_REGEN_OPENAPI"):
        SNAPSHOT_PATH.write_text(json.dumps(current, indent=2, sort_keys=True) + "\n")
        return

    if not SNAPSHOT_PATH.exists():
        raise AssertionError(
            f"Snapshot file missing: {SNAPSHOT_PATH}. "
            f"Run: HAPPYRANCH_REGEN_OPENAPI=1 uv run pytest {__file__}"
        )

    stored = json.loads(SNAPSHOT_PATH.read_text())
    if current != stored:
        # Render a path-only diff so the failure message is digestible.
        cur_keys = set(current["paths"].keys())
        stored_keys = set(stored["paths"].keys())
        added = sorted(cur_keys - stored_keys)
        removed = sorted(stored_keys - cur_keys)
        msg_lines = ["OpenAPI schema drift:"]
        if added:
            msg_lines.append(f"  + added paths:   {added}")
        if removed:
            msg_lines.append(f"  - removed paths: {removed}")
        msg_lines.append(
            "Regenerate after reviewing: "
            f"HAPPYRANCH_REGEN_OPENAPI=1 uv run pytest {__file__}"
        )
        raise AssertionError("\n".join(msg_lines))


# ── ScheduleEditBody null-type regression ─────────────────────────────

_NON_NULLABLE_EDIT_FIELDS = ["fire_at", "recurrence", "timezone"]


def test_schedule_edit_body_schema_no_null_type() -> None:
    """ScheduleEditBody must not advertise ``type: null`` for mutable fields.

    The route rejects explicit-null payloads at runtime (422 ``explicit_null``),
    so the OpenAPI schema must not tell callers that null is a valid value.
    """
    app = create_app(DaemonState.idle(Settings()))
    full = app.openapi()

    # Navigate to the PATCH /orgs/{slug}/schedules/{schedule_id} request body.
    schedule_edit_path = "/api/v1/orgs/{slug}/schedules/{schedule_id}"

    # Try both the component-schema and path-embedded paths.
    edit_body_schema = None

    # 1) Check openapi.json shape (which pins the path/param view; the full
    #    Pydantic-generated schema lives in components.schemas).
    schemas = full.get("components", {}).get("schemas", {})
    for name, schema in schemas.items():
        if "ScheduleEditBody" in name:
            edit_body_schema = schema
            break

    assert edit_body_schema is not None, (
        "ScheduleEditBody schema not found in components.schemas"
    )

    properties = edit_body_schema.get("properties", {})
    for field_name in _NON_NULLABLE_EDIT_FIELDS:
        prop = properties.get(field_name)
        assert prop is not None, f"{field_name} missing from ScheduleEditBody schema"

        # Must NOT expose type: null or anyOf with a null branch.
        prop_json = json.dumps(prop)
        has_null_type = prop.get("type") == "null"
        has_null_anyof = any(
            branch.get("type") == "null"
            for branch in prop.get("anyOf", [])
        ) if "anyOf" in prop else False

        assert not has_null_type, (
            f"ScheduleEditBody.{field_name} exposes type=null: {prop_json}"
        )
        assert not has_null_anyof, (
            f"ScheduleEditBody.{field_name} exposes anyOf null branch: {prop_json}"
        )
