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


# ── AdapterEntryResponse eligibility semantic test (TASK-3836 fix-forward) ─


def test_adapter_entry_eligibility_includes_recovery_ready() -> None:
    """AdapterEntryResponse.eligibility description MUST include 'recovery_ready'.

    The _compute_eligibility function returns 'recovery_ready' for approved
    no-intended adapters with valid hash/integrity (TASK-3832).  If the
    OpenAPI description omits this value, the published contract is
    semantically wrong — consumers reading the schema won't know this
    state exists.  This test is a fail-closed semantic guard beyond the
    path-level snapshot.
    """
    app = create_app(DaemonState.idle(Settings()))
    full = app.openapi()

    schemas = full.get("components", {}).get("schemas", {})
    adapter_schema = schemas.get("AdapterEntryResponse", {})
    assert adapter_schema, "AdapterEntryResponse schema missing from OpenAPI components"

    eligibility_prop = adapter_schema.get("properties", {}).get("eligibility", {})
    assert eligibility_prop, (
        "eligibility field missing from AdapterEntryResponse schema"
    )

    description = eligibility_prop.get("description", "")
    assert "recovery_ready" in description, (
        f"AdapterEntryResponse.eligibility description must include 'recovery_ready'.\n"
        f"Current description:\n{description}\n\n"
        f"_compute_eligibility returns 'recovery_ready' for approved no-intended "
        f"adapters. If this test fails, update the description in "
        f"runtime/daemon/routes/adapters.py AdapterEntryResponse.eligibility field "
        f"to include 'recovery_ready'."
    )

    # Also verify that 'not_intended' is NOT present (it was removed in TASK-3832).
    assert "not_intended" not in description, (
        f"AdapterEntryResponse.eligibility description must NOT include 'not_intended' "
        f"(replaced by 'recovery_ready' in TASK-3832).\n"
        f"Current description:\n{description}"
    )


# ── BindProfileRequest + bind-profile operation semantic test (TASK-3841 fix-forward) ─

def test_bind_profile_contract_describes_both_paths() -> None:
    """BindProfileRequest and bind-profile operation MUST describe both binding paths.

    TASK-3839 found the published contract still claimed the request must
    unconditionally match intended_profile_name, while the runtime
    deliberately accepts a caller-selected name for approved no-intended
    (recovery_ready) adapters.  This test is a fail-closed semantic guard:
    it inspects the generated schema and operation descriptions.
    """
    app = create_app(DaemonState.idle(Settings()))
    full = app.openapi()

    # ── BindProfileRequest schema ──
    schemas = full.get("components", {}).get("schemas", {})
    bp_schema = schemas.get("BindProfileRequest", {})
    assert bp_schema, "BindProfileRequest schema missing from OpenAPI components"

    schema_desc = bp_schema.get("description", "")
    assert "recovery_ready" in schema_desc, (
        f"BindProfileRequest schema description must include 'recovery_ready'.\n"
        f"Current description:\n{schema_desc}"
    )
    assert "intended_profile_name" in schema_desc, (
        f"BindProfileRequest schema description must mention 'intended_profile_name'.\n"
        f"Current description:\n{schema_desc}"
    )

    profile_prop = bp_schema.get("properties", {}).get("profile_name", {})
    assert profile_prop, "BindProfileRequest.profile_name property missing from schema"
    prop_desc = profile_prop.get("description", "")
    assert "recovery_ready" in prop_desc, (
        f"profile_name field description must include 'recovery_ready'.\n"
        f"Current description:\n{prop_desc}"
    )
    assert "intended" in prop_desc, (
        f"profile_name field description must describe intended-profile matching.\n"
        f"Current description:\n{prop_desc}"
    )

    # ── Reject unconditional intended-name-only claim ──
    assert "caller-selected" in prop_desc.lower() or "caller selected" in prop_desc.lower(), (
        f"profile_name field description must mention caller-selected name for recovery.\n"
        f"Current description:\n{prop_desc}"
    )

    # ── bind-profile POST operation ──
    paths = full.get("paths", {})
    bind_path = None
    for path_key, path_val in paths.items():
        if path_key.endswith("bind-profile"):
            bind_path = path_val
            break
    assert bind_path is not None, "bind-profile path missing from OpenAPI"

    post_op = bind_path.get("post", {})
    assert post_op, "bind-profile POST operation missing"

    op_desc = post_op.get("description", "")
    assert "recovery_ready" in op_desc, (
        f"bind-profile operation description must include 'recovery_ready'.\n"
        f"Current description:\n{op_desc}"
    )
    assert "intended_profile_name" in op_desc, (
        f"bind-profile operation description must mention intended_profile_name.\n"
        f"Current description:\n{op_desc}"
    )


# ── ScheduleEditBody null-type regression ─────────────────────────────

_NON_NULLABLE_EDIT_FIELDS = ["fire_at", "recurrence", "timezone"]

_RECURRING_VALIDATION_CODES = {
    "invalid_freq_fields", "invalid_byday", "monthly_selector_missing",
    "monthly_selector_conflict", "invalid_interval", "anchor_date_not_settable",
    "invalid_until", "invalid_count", "end_condition_conflict", "invalid_time",
    "invalid_timezone",
}


def test_schedule_create_contract_documents_recurring_kind_and_validation_codes() -> None:
    app = create_app(DaemonState.idle(Settings()))
    full = app.openapi()
    schemas = full.get("components", {}).get("schemas", {})

    create_schema = schemas["ScheduleCreateBody"]
    assert "recurring" in create_schema["properties"]["kind"]["description"]

    error_schema = schemas["RecurringValidationErrorResponse"]
    code_schema = error_schema["properties"]["detail"]["$ref"]
    detail_name = code_schema.rsplit("/", 1)[-1]
    assert set(schemas[detail_name]["properties"]["code"]["enum"]) == _RECURRING_VALIDATION_CODES


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
