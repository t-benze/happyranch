"""Snapshot test: pins the daemon's OpenAPI schema.

When a daemon route changes (added/removed/renamed/method change), this test
fails. To accept the new schema, regenerate the snapshot:

    uv run python scripts/generate_openapi_snapshot.py --write

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


from scripts.generate_openapi_snapshot import _summarize


def test_thread_list_full_operation_contract() -> None:
    """Consume the full published GET contract, independently of the summary."""
    schema = create_app(DaemonState.idle(Settings())).openapi()
    operation = schema["paths"]["/api/v1/orgs/{slug}/threads"]["get"]

    def resolve(value):
        if "$ref" not in value:
            return value
        target = schema
        assert value["$ref"].startswith("#/")
        for segment in value["$ref"][2:].split("/"):
            target = target[segment.replace("~1", "/").replace("~0", "~")]
        return resolve(target)

    def types(value):
        value = resolve(value)
        if "anyOf" in value or "oneOf" in value:
            return set().union(*(types(branch) for branch in value.get("anyOf", value.get("oneOf"))))
        return {value["type"]}

    assert {"200", "400", "422"} <= operation["responses"].keys()
    assert "invalid_thread_cursor" in operation["responses"]["400"]["description"]
    success = resolve(operation["responses"]["200"]["content"]["application/json"]["schema"])
    branches = [resolve(branch) for branch in success.get("anyOf", success.get("oneOf", []))]
    assert len(branches) == 2
    legacy = next(branch for branch in branches if set(branch["properties"]) == {"threads"})
    page = next(branch for branch in branches if "totals" in branch["properties"])
    assert set(legacy["required"]) == {"threads"}
    assert legacy.get("additionalProperties") is not False
    required = {"threads", "totals", "has_more", "next_cursor", "sampled_at"}
    assert set(page["properties"]) == set(page["required"]) == required
    assert types(page["properties"]["has_more"]) == {"boolean"}
    assert types(page["properties"]["next_cursor"]) == {"string", "null"}
    assert types(page["properties"]["sampled_at"]) == {"string"}
    totals = resolve(page["properties"]["totals"])
    assert set(totals["properties"]) == set(totals["required"]) == {"open", "archived", "all", "dream_origin"}
    for value in totals["properties"].values():
        assert types(value) == {"integer"}

    row_types = {
        **{key: {"string"} for key in ("thread_id", "subject", "status", "started_at")},
        **{key: {"string", "null"} for key in (
            "archived_at", "forwarded_from_id", "forwarded_from_kind", "summary", "transcript_path",
            "composed_by", "composed_from_task_id", "composed_from_dream_id", "last_speaker", "pinned_at", "last_activity_at",
        )},
        "turn_cap": {"integer"}, "turns_used": {"integer"}, "pinned": {"boolean"}, "participants": {"array"},
    }
    for branch in (legacy, page):
        array = resolve(branch["properties"]["threads"])
        assert types(array) == {"array"}
        row = resolve(array["items"])
        assert set(row["properties"]) == set(row["required"]) == set(row_types)
        assert row["additionalProperties"] is False
        for key, expected in row_types.items():
            assert types(row["properties"][key]) == expected, key
        assert types(row["properties"]["participants"]["items"]) == {"string"}

    params = {param["name"]: param for param in operation["parameters"] if param["in"] in ("path", "query")}
    assert set(params) == {"slug", "status", "limit", "page_size", "cursor"}
    assert params["slug"]["in"] == "path" and params["slug"]["required"] is True
    assert types(params["slug"]["schema"]) == {"string"}
    for name in ("status", "cursor", "page_size", "limit"):
        assert params[name]["in"] == "query" and params[name]["required"] is False
    assert types(params["page_size"]["schema"]) == {"integer", "null"}
    size = next(branch for branch in params["page_size"]["schema"]["anyOf"] if branch.get("type") == "integer")
    assert (size.get("minimum"), size.get("maximum")) == (1, 100)
    assert params["page_size"]["schema"].get("default") is None
    assert "limit" in params["page_size"]["description"]
    for name in ("status", "cursor"):
        assert types(params[name]["schema"]) == {"string", "null"}
        assert params[name]["schema"].get("default") is None
        assert not any("enum" in branch or "minLength" in branch or "maxLength" in branch for branch in params[name]["schema"]["anyOf"])
    assert "page_size" in params["cursor"]["description"]
    limit = resolve(params["limit"]["schema"])
    assert types(limit) == {"integer"} and limit["default"] == 50
    assert "minimum" not in limit and "maximum" not in limit


def test_openapi_snapshot_matches() -> None:
    app = create_app(DaemonState.idle(Settings()))
    current = _summarize(app.openapi())

    if os.environ.get("HAPPYRANCH_REGEN_OPENAPI"):
        SNAPSHOT_PATH.write_text(json.dumps(current, indent=2, sort_keys=True) + "\n")
        return

    if not SNAPSHOT_PATH.exists():
        raise AssertionError(
            f"Snapshot file missing: {SNAPSHOT_PATH}. "
            f"Run: uv run python scripts/generate_openapi_snapshot.py --write"
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
            f"uv run python scripts/generate_openapi_snapshot.py --write"
        )
        raise AssertionError("\n".join(msg_lines))


def test_system_prompt_exact_request_receipt_and_failure_contract() -> None:
    """THR280 C18: externally consumed request/receipt/compensation schema."""
    schema = create_app(DaemonState.idle(Settings())).openapi()
    models = schema["components"]["schemas"]
    request = models["SystemPromptBody"]
    assert request["additionalProperties"] is False
    assert set(request["properties"]) == {"system_prompt", "expected_revision"}
    assert set(request["required"]) == {"system_prompt", "expected_revision"}
    assert request["properties"]["system_prompt"]["type"] == "string"
    assert request["properties"]["expected_revision"] == {"type": "string", "pattern": "^[0-9a-f]{64}$"}
    assert set(models["SystemPromptReceipt"]["required"]) == {"agent", "system_prompt", "revision"}
    assert set(models["SystemPromptReceipt"]["properties"]) == {"agent", "system_prompt", "revision"}
    operation = schema["paths"]["/api/v1/orgs/{slug}/agents/{agent_name}/system-prompt"]["put"]
    assert set(operation["responses"]) == {"200", "400", "404", "409", "422", "500"}
    for field in ("canonical", "workspace"):
        assert models["SystemPromptCompensation"]["properties"][field]["enum"] == [
            "restored", "not_owned", "failed", "not_required",
        ]
    assert models["SystemPromptAuditDetail"]["properties"]["commit_state"]["const"] == "possibly_committed"


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

_NON_NULLABLE_EDIT_FIELDS = ["fire_at", "recurrence", "timezone", "start_date"]

_RECURRING_VALIDATION_CODES = {
    "invalid_freq_fields", "invalid_byday", "monthly_selector_missing",
    "monthly_selector_conflict", "invalid_interval", "anchor_date_not_settable",
    "invalid_until", "invalid_count", "end_condition_conflict", "invalid_time",
    "invalid_timezone", "invalid_start_date",
}


def test_schedule_create_contract_documents_recurring_kind_and_validation_codes() -> None:
    app = create_app(DaemonState.idle(Settings()))
    full = app.openapi()
    schemas = full.get("components", {}).get("schemas", {})

    create_schema = schemas["ScheduleCreateBody"]
    assert "recurring" in create_schema["properties"]["kind"]["description"]
    assert "start_date" in create_schema["properties"]
    assert "server derives" in create_schema["properties"]["fire_at"]["description"].lower()

    error_schema = schemas["RecurringValidationErrorResponse"]
    code_schema = error_schema["properties"]["detail"]["$ref"]
    detail_name = code_schema.rsplit("/", 1)[-1]
    assert set(schemas[detail_name]["properties"]["code"]["enum"]) == _RECURRING_VALIDATION_CODES


def test_schedule_renew_contract_documents_state_conflict() -> None:
    app = create_app(DaemonState.idle(Settings()))
    responses = app.openapi()["paths"]["/api/v1/orgs/{slug}/schedules/{schedule_id}/renew"]["post"][
        "responses"
    ]

    assert set(responses) == {"200", "409", "422"}


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


def test_schedule_edit_contract_documents_server_derived_recurring_fire_at() -> None:
    app = create_app(DaemonState.idle(Settings()))
    operation = app.openapi()["paths"]["/api/v1/orgs/{slug}/schedules/{schedule_id}"]["patch"]
    description = operation["description"]
    assert "omit" in description.lower()
    assert "server" in description.lower()
    assert "strict" in description.lower()
    assert "start_date" in description


def test_schedule_edit_contract_documents_recurring_selector_clears() -> None:
    app = create_app(DaemonState.idle(Settings()))
    schema = app.openapi()["components"]["schemas"]["ScheduleEditBody"]
    description = schema["properties"]["recurrence"]["description"].lower()
    assert "byday" in description
    assert "bymonthday" in description
    assert "ordinal" in description
    assert "null" in description
    assert "after merge" in description


def test_thread_reply_delivery_operations_document_four_state_precedence() -> None:
    app = create_app(DaemonState.idle(Settings()))
    paths = app.openapi()["paths"]
    for suffix in ("", "/messages"):
        operation = paths[
            f"/api/v1/orgs/{{slug}}/threads/{{thread_id}}{suffix}"
        ]["get"]
        description = operation["description"]
        assert "running > queued > held > retry_required > settled" in description
        assert "held" in description.lower()


def test_cutover_openapi_has_closed_request_and_truthful_projection() -> None:
    schema = create_app(DaemonState.idle(Settings())).openapi()
    request = schema["paths"]["/api/v1/orgs/{slug}/workflows/cutover/requests"]["post"]
    body = request["requestBody"]["content"]["application/json"]["schema"]
    assert body["additionalProperties"] is False
    assert set(body["required"]) == {"action", "operation_key", "expected_generation"}
    assert body["properties"]["action"]["enum"] == ["enable", "disable"]
    assert body["properties"]["expected_generation"]["type"] == "integer"
    assert body["properties"]["expected_generation"]["exclusiveMinimum"] == 0
    projection = schema["components"]["schemas"]["CutoverProjection"]["properties"]
    assert {"events", "blockers", "reconciliation_required", "allowed_actions", "verification"} <= projection.keys()
    assert "execution_started" not in projection


def test_activation_openapi_pins_closed_request_and_complete_receipt():
    full = create_app(DaemonState.idle(Settings())).openapi()
    base = '/api/v1/orgs/{slug}/workflows/activations'
    post = full['paths'][base]['post']
    request_union = post['requestBody']['content']['application/json']['schema']
    assert len(request_union['anyOf']) == 2
    request, document_request = request_union['anyOf']
    assert document_request['additionalProperties'] is False
    assert document_request['properties']['format']['const'] == 'workflow-activation-request@2'
    assert set(document_request['required']) == set(request['required']) | {'format'}
    for name in ('bindings', 'eligible_replacements'):
        role_map = document_request['properties'][name]
        assert role_map['propertyNames'] == {
            'minLength': 1, 'maxLength': 63, 'pattern': '^[a-z][a-z0-9-]*$',
        }
        assert role_map['minProperties'] == 2 and role_map['maxProperties'] == 4
        assert 'patternProperties' not in role_map
    assert document_request['properties']['bindings']['additionalProperties']['additionalProperties'] is False
    replacements = document_request['properties']['eligible_replacements']['additionalProperties']
    assert replacements['type'] == 'array' and replacements['maxItems'] == 16
    assert replacements['items']['additionalProperties'] is False
    assert request['additionalProperties'] is False
    assert set(request['required']) == {
        'operation_key', 'instance_id', 'expected_activation_revision', 'template',
        'authority', 'scope', 'bindings', 'eligible_replacements', 'allowed_actions', 'inputs',
    }
    assert request['properties']['expected_activation_revision']['type'] == 'integer'
    assert request['properties']['expected_activation_revision']['maximum'] == 0
    assert request['properties']['bindings']['additionalProperties'] is False
    schemas = full['components']['schemas']
    response = post['responses']['201']['content']['application/json']['schema']
    assert response['anyOf'] == [{'$ref': '#/components/schemas/ActivationReceipt'}, {'$ref': '#/components/schemas/DocumentActivationReceipt'}]
    receipt = schemas['ActivationReceipt']
    assert receipt['additionalProperties'] is False
    assert set(receipt['required']) == {
        'activation_id', 'instance_id', 'instance_reference', 'activation_revision',
        'root_task_id', 'intent_id', 'template', 'authority', 'bindings',
        'eligible_replacements', 'allowed_actions', 'scope_digest', 'context_digest',
        'activated_by', 'created_at', 'original_request_digest', 'replayed', 'state',
        'execution_started', 'pending', 'reconciliation_required', 'cancellation_requested',
        'current_eligibility', 'responsible_owner',
    }
    document_receipt = schemas['DocumentActivationReceipt']
    assert document_receipt['additionalProperties'] is False
    assert document_receipt['properties']['format']['const'] == 'workflow-activation-receipt@2'
    assert set(document_receipt['required']) == set(receipt['required']) | {'format'}
    assert 'format' not in receipt['properties']
    assert set(post['responses']) == {'200', '201', '403', '409', '422', '500'}
    assert post['responses']['200']['content']['application/json']['schema']['anyOf'] == response['anyOf']
    assert full['paths'][base]['get']['responses']['200']['content']['application/json']['schema']['items']['anyOf'] == response['anyOf']
    assert full['paths'][base+'/{activation_id}']['get']['responses']['200']['content']['application/json']['schema']['anyOf'] == response['anyOf']


def test_activation_served_openapi_resolves_all_internal_pointers_and_input_variants() -> None:
    from fastapi.testclient import TestClient
    app = create_app(DaemonState.idle(Settings()))
    served = TestClient(app).get('/openapi.json')
    assert served.status_code == 200
    document = served.json()
    assert document == app.openapi()

    def resolve(pointer):
        value = document
        for part in pointer[2:].split('/'):
            key = part.replace('~1', '/').replace('~0', '~')
            try:
                value = value[int(key)] if isinstance(value, list) else value[key]
            except (KeyError, IndexError, ValueError):
                raise AssertionError(f'dangling served OpenAPI pointer: {pointer}') from None
        return value

    def check(value):
        if isinstance(value, dict):
            for item in value.values():
                check(item)
        elif isinstance(value, list):
            for item in value:
                check(item)
        elif isinstance(value, str) and value.startswith('#/'):
            resolve(value)

    check(document)
    schema = document['paths']['/api/v1/orgs/{slug}/workflows/activations']['post']['requestBody']['content']['application/json']['schema']
    assert len(schema['anyOf']) == 2
    for branch in schema['anyOf']:
        inputs = branch['properties']['inputs']['items']
        assert inputs['discriminator']['propertyName'] == 'kind'
        assert set(inputs['discriminator']['mapping']) == {'task-attachment', 'thread-attachment'}
        for kind, pointer in inputs['discriminator']['mapping'].items():
            variant = resolve(pointer)
            assert variant in inputs['oneOf']
            assert variant['properties']['kind']['const'] == kind
            assert variant['additionalProperties'] is False
            assert ('task_id' in variant['required']) is (kind == 'task-attachment')
            assert ('thread_id' in variant['required']) is (kind == 'thread-attachment')
