#!/usr/bin/env bash
# Finite selection for the twelve accepted business scenarios, not a case-count gate.
set -euo pipefail
cd /source
: "${NAMING_EVIDENCE:=/evidence}"
mkdir -p "$NAMING_EVIDENCE"
# This non-test operator obligation and served HTTP comparison are distinct.
uv run python scripts/generate_openapi_snapshot.py --check
uv run python scripts/run_bounded_output.py --output "$NAMING_EVIDENCE/naming.log" --max-bytes 1048576 -- \
  uv run python tests/helpers/integration_parent.py -- pytest \
  tests/integration/test_identity_names_core.py::test_scenario1_existing_org_upgrade_and_underscore \
  tests/integration/test_identity_names_e2e.py::test_scenario2_browser_agent_rename_and_stale_readback \
  tests/integration/test_identity_names_e2e.py::test_scenario3_browser_founder_name_and_durable_human_inbox \
  tests/integration/test_identity_names_core.py::test_scenario4_folded_collision_and_second_org \
  tests/integration/test_identity_names_integrated.py::test_scenario5_former_before_callback_and_multipart_effects \
  tests/integration/test_identity_names_integrated.py::test_scenario6_current_names_and_unknown_fallback \
  tests/integration/test_identity_names_e2e.py::test_scenario6_live_current_name_callback_and_unknown_fallback \
  tests/integration/test_identity_names_integrated.py::test_scenario7_founder_only_preserves_open_and_stale_catchup \
  tests/integration/test_identity_names_integrated.py::test_scenario8_cli_targets_and_fresh_prompt_context \
  tests/integration/test_identity_names_e2e.py::test_scenario8_live_cli_picker_and_persisted_ids \
  tests/integration/test_identity_names_integrated.py::test_scenarios9_12_queued_ids_survive_rename_and_reopen \
  tests/integration/test_identity_names_e2e.py::test_scenario9_live_history_authority_reopen \
  tests/integration/test_identity_names_core.py::test_a10_bound_callback_continuity \
  tests/integration/test_identity_names_integrated.py::test_scenario10_bound_and_unauthorized_rename \
  tests/integration/test_identity_names_core.py::test_scenario11_one_daemon_async_contention \
  tests/integration/test_identity_names_core.py::test_scenario12_interrupted_lifecycle_and_reservations \
  tests/integration/test_identity_names_e2e.py::test_served_openapi_matches_supported_snapshot \
  -m integration -v --tb=short --basetemp /scratch/naming-tests \
  -o tmp_path_retention_policy=all \
  --junitxml "$NAMING_EVIDENCE/naming.xml"
