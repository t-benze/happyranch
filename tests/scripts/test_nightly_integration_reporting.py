from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "nightly_integration_summary.py"
RUNNER = ROOT / "scripts" / "run_bounded_output.py"
WORKFLOW = ROOT / ".github" / "workflows" / "nightly-integration.yml"
ALL_RUNNER = ROOT / "scripts" / "nightly_local_ci_all.py"


def test_manual_local_ci_preserves_schedule_only_integration() -> None:
    # GitHub consumes these exact YAML keys and expression bytes. BaseLoader
    # preserves the workflow's `on` key instead of YAML 1.1 boolean coercion.
    workflow = yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    dispatch = workflow["on"]["workflow_dispatch"]
    # The parametrized all-only keeper below owns type/default and all four
    # schedule/manual cases; the manual receipt lane admits no integration toggle.
    assert set(dispatch["inputs"]) == {"all_only"}
    assert "run_integration" not in dispatch["inputs"]
    jobs = workflow["jobs"]
    assert set(jobs) == {"local-ci-all", "integration", "report-scheduled-failure"}
    manual = jobs["local-ci-all"]
    # Observed Python units took 99 minutes before the full Web lane. Keep
    # headroom for the complete selection, within the approved finite cap.
    manual_cap = int(manual["timeout-minutes"])
    assert manual_cap == 150, f"manual cap {manual_cap} must be the approved 150 minutes"
    assert jobs["integration"]["timeout-minutes"] == "30"
    assert workflow["on"]["schedule"] == [{"cron": "0 7 * * *"}]
    assert manual["runs-on"] == "ubuntu-latest"
    assert "strategy" not in manual
    manual_steps = {step["name"]: step for step in manual["steps"]}
    assert manual_steps["Install uv"]["with"]["python-version"] == "3.14"
    assert manual_steps["Set up Node"]["with"]["node-version"] == "24"
    assert manual_steps["Sync dependencies (frozen)"]["run"] == "uv sync --frozen"
    local_all = manual_steps["Run exact local CI all in a clean test environment"]
    assert local_all["run"] == (
        'if [ "$GITHUB_REPOSITORY" = "t-benze/happyranch" ] && '
        '[ "$GITHUB_EVENT_NAME" = "workflow_dispatch" ] && '
        '[ "$GITHUB_REF" = "refs/heads/task/TASK-10034" ]; then\n'
        '  uv run --frozen --no-sync python scripts/nightly_local_ci_all.py\n'
        'else\n'
        '  uv run python scripts/nightly_local_ci_all.py\n'
        'fi\n'
    )
    assert local_all["env"]["ALL_ONLY"] == "${{ inputs.all_only }}"
    # Receipt production moved out of the workflow scalar. Inspect its actual
    # owner without importing/executing it; Python keeper proof stays suspended.
    runner_source = ALL_RUNNER.read_text(encoding="utf-8")
    for required in (
        "assert head == os.environ['GITHUB_SHA']",
        "assert not subprocess.check_output(['git', 'status', '--porcelain'])",
        "'command': 'scripts/local_ci.sh all'",
        "'source_sha256':",
        "'tests/helpers/integration_parent.py'",
        "'tests/helpers/integration_stub_guard/guard.py'",
        "'cli/main.py'",
        "'--max-bytes', '1048576', '--', *argv",
        "all_exit, receipt['full_log'] = bounded_run(['scripts/local_ci.sh', 'all'],",
        "cwd=source, env=env, directory=evidence, tail=evidence / 'local-ci-all.log')",
        "metadata.update(exit_code=process.wait(), complete=True)",
        "return metadata['exit_code']",
        "assert sys.version_info[:2] == (3, 14)",
        "assert receipt['tools']['node']['version'].split('.')[0] == 'v24'",
        "with tempfile.TemporaryDirectory(prefix='local-ci-parent-') as directory:",
        "for name in ('home', 'config', 'cache', 'tmp', 'daemon', 'bin'):",
        "'HOME': str(root / 'home'), 'XDG_CONFIG_HOME': str(root / 'config')",
        "'HAPPYRANCH_DAEMON_HOME': str(root / 'daemon'), 'HAPPYRANCH_DAEMON_PORT': '0'",
        "'UV_PYTHON': sys.executable, 'UV_PYTHON_DOWNLOADS': 'never'",
        "receipt['exit_code'] = result.returncode",
        "raise SystemExit(result.returncode)",
    ):
        assert required in runner_source
    upload = manual_steps["Upload manual local CI evidence"]
    assert upload["if"] == "${{ always() }}"
    assert upload["with"]["path"] == "${{ runner.temp }}/local-ci-all/"
    assert upload["with"]["name"] == "local-ci-all-${{ github.run_id }}-${{ github.run_attempt }}"

    ci = yaml.load((ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    ci_jobs = ci["jobs"]
    assert set(ci_jobs) == {"python-unit", "web", "linux-canonical-validation", "macos-canonical-validation"}
    assert ci_jobs["python-unit"]["strategy"]["matrix"]["python-version"] == (
        "${{ github.event_name == 'pull_request' && fromJSON('[\"3.14\"]') "
        "|| fromJSON('[\"3.12\", \"3.13\", \"3.14\"]') }}"
    )
    python_steps = {step["name"]: step for step in ci_jobs["python-unit"]["steps"]}
    assert python_steps["Run unit tests"]["run"] == "uv run pytest tests/ -v -n 4"
    assert ci_jobs["linux-canonical-validation"]["runs-on"] == "ubuntu-latest"
    assert ci_jobs["macos-canonical-validation"]["runs-on"] == "macos-15"
    linux_steps = {step["name"]: step for step in ci_jobs["linux-canonical-validation"]["steps"]}
    assert linux_steps["Run real daemon + executor smoke test"]["run"] == (
        "uv run python tests/helpers/integration_parent.py -- pytest -m integration "
        "tests/integration/test_end_to_end.py::test_register_and_run_completes_via_codex_callback -v --tb=short"
    )
    assert [step["run"] for step in ci_jobs["web"]["steps"] if "run" in step] == [
        "npm ci", "bash scripts/verify-design-system-colour-gate.sh", "npm run lint",
        "npm run typecheck", "npm run build", "npm run build-storybook", "npx vitest run",
    ]
    predicate = workflow["jobs"]["integration"]["if"]
    assert predicate == "${{ github.event_name == 'schedule' }}"
    assert workflow["jobs"]["local-ci-all"]["if"] == "${{ github.event_name == 'workflow_dispatch' }}"
    for event, expected in [
        ("schedule", True), ("workflow_dispatch", False),
    ]:
        expression = predicate[3:-2].strip()
        expression = expression.replace("github.event_name", repr(event))
        assert eval(expression, {"__builtins__": {}}, {}) is expected


def test_summary_reports_counts_and_failed_test_ids(tmp_path: Path) -> None:
    junit = tmp_path / "integration.xml"
    junit.write_text(
        """<?xml version="1.0" encoding="utf-8"?>
<testsuites name="pytest tests">
  <testsuite name="pytest" tests="5" failures="1" errors="1" skipped="1">
    <testcase classname="tests.integration.test_ok" name="test_pass" file="tests/integration/test_ok.py" />
    <testcase classname="tests.integration.test_bad" name="test_failure[x]" file="tests/integration/test_bad.py"><failure>no</failure></testcase>
    <testcase classname="tests.integration.test_bad" name="test_error" file="tests/integration/test_bad.py"><error>boom</error></testcase>
    <testcase classname="tests.integration.test_skip" name="test_skip" file="tests/integration/test_skip.py"><skipped /></testcase>
    <testcase classname="tests.integration.test_ok" name="test_pass_two" file="tests/integration/test_ok.py" />
  </testsuite>
</testsuites>
""",
        encoding="utf-8",
    )
    output = tmp_path / "summary.md"

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            str(junit),
            "--output",
            str(output),
            "--head-sha",
            "abcdef1234567890",
            "--run-url",
            "https://github.example/runs/42",
            "--artifact-name",
            "nightly-integration-42",
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    summary = output.read_text(encoding="utf-8")
    assert "| 5 | 2 | 2 | 1 |" in summary
    assert "`tests/integration/test_bad.py::test_failure[x]`" in summary
    assert "`tests/integration/test_bad.py::test_error`" in summary
    assert "abcdef1234567890" in summary
    assert "[Open hosted run](https://github.example/runs/42)" in summary
    assert "`nightly-integration-42`" in summary


def test_summary_surfaces_missing_junit_without_fabricating_counts(tmp_path: Path) -> None:
    output = tmp_path / "summary.md"

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            str(tmp_path / "missing.xml"),
            "--output",
            str(output),
            "--head-sha",
            "deadbeef",
            "--run-url",
            "https://github.example/runs/43",
            "--artifact-name",
            "nightly-integration-43",
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    summary = output.read_text(encoding="utf-8")
    assert "| unavailable | unavailable | unavailable | unavailable |" in summary
    assert "JUnit XML was not produced" in summary


def test_nightly_workflow_preserves_selection_and_scopes_issue_permission() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "uv run python tests/helpers/integration_parent.py -- pytest tests/ -v -m integration" in workflow
    assert "--junitxml=artifacts/nightly-integration.xml" in workflow
    assert "python3 scripts/run_bounded_output.py" in workflow
    assert "--output artifacts/nightly-integration.log" in workflow
    assert "--max-bytes 1048576" in workflow
    assert "| tee artifacts/nightly-integration.log" not in workflow
    assert "${{ always() && github.event_name == 'schedule'" in workflow
    assert "needs.integration.result == 'failure'" in workflow
    assert "nightly-integration-failure" in workflow
    assert "github.paginate(github.rest.issues.listForRepo" in workflow
    assert "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02" in workflow
    assert "actions/download-artifact@d3f86a106a0bac45b974a628896c90dbdf5c8093" in workflow
    assert "actions/github-script@f28e40c7f34bde8b3046d885e986cb6290c5673b" in workflow

    integration_job, issue_job = workflow.split("  report-scheduled-failure:\n", maxsplit=1)
    assert "issues: write" not in integration_job
    assert "issues: write" in issue_job


def _run_bounded_output(
    tmp_path: Path,
    *,
    command: list[str],
    max_bytes: int = 128,
) -> tuple[subprocess.CompletedProcess[str], Path]:
    output = tmp_path / "nightly-integration.log"
    result = subprocess.run(
        [
            sys.executable,
            str(RUNNER),
            "--output",
            str(output),
            "--max-bytes",
            str(max_bytes),
            "--",
            *command,
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    return result, output


def test_bounded_output_caps_artifact_and_retains_tail(tmp_path: Path) -> None:
    cap = 128
    tail = "TAIL-SENTINEL\n"
    command = [
        sys.executable,
        "-c",
        f"import sys; sys.stdout.write({'x' * 400 + tail!r})",
    ]

    result, output = _run_bounded_output(
        tmp_path,
        command=command,
        max_bytes=cap,
    )

    assert result.returncode == 0, result.stderr
    artifact = output.read_bytes()
    assert len(artifact) <= cap, f"observed {len(artifact)} bytes for {cap}-byte cap"
    assert b"nightly log truncated" in artifact
    assert artifact.endswith(tail.encode())


def test_bounded_output_returns_wrapped_nonzero_status(tmp_path: Path) -> None:
    result, output = _run_bounded_output(
        tmp_path,
        command=[sys.executable, "-c", "import sys; print('failed'); sys.exit(7)"],
    )

    assert result.returncode == 7
    assert output.read_bytes().endswith(b"failed\n")


def test_bounded_output_returns_zero_for_success(tmp_path: Path) -> None:
    result, output = _run_bounded_output(
        tmp_path,
        command=[sys.executable, "-c", "print('passed')"],
    )

    assert result.returncode == 0, result.stderr
    assert output.read_bytes().endswith(b"passed\n")


@pytest.mark.parametrize('event,all_only,expected_integration', [
    ('schedule', None, True), ('workflow_dispatch', None, False),
    ('workflow_dispatch', False, False), ('workflow_dispatch', True, False),
], ids=['schedule', 'manual-default', 'manual-false', 'manual-true'])
def test_nightly_workflow_all_only_selection(event, all_only, expected_integration, tmp_path: Path) -> None:
    import ast
    import yaml
    workflow = WORKFLOW.read_text(encoding='utf-8')
    document = yaml.load(workflow, Loader=yaml.BaseLoader)
    inputs = document['on']['workflow_dispatch']['inputs']
    assert set(inputs) == {'all_only'}
    assert inputs['all_only']['type'] == 'boolean' and inputs['all_only']['default'] == 'false'
    job = document['jobs']['integration']
    assert job['if'] == "${{ github.event_name == 'schedule' }}"
    import re
    expression = job['if'].removeprefix('${{').removesuffix('}}').strip()
    expression = expression.replace('github.event_name', 'event').replace('inputs.all_only', 'all_only')
    expression = expression.replace('||', ' or ').replace('&&', ' and ')
    expression = re.sub(r'!(?!=)', 'not ', expression)
    parsed_condition = ast.parse(expression.strip(), mode='eval')

    def observed_condition(node):
        if isinstance(node, ast.Name) and node.id in {'event', 'all_only'}:
            return {'event': event, 'all_only': all_only}[node.id]
        if isinstance(node, ast.Constant) and (type(node.value) in (str, bool) or node.value is None):
            return node.value
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            return not observed_condition(node.operand)
        if isinstance(node, ast.BoolOp) and isinstance(node.op, (ast.And, ast.Or)):
            values = [bool(observed_condition(value)) for value in node.values]
            return all(values) if isinstance(node.op, ast.And) else any(values)
        if (isinstance(node, ast.Compare) and len(node.ops) == len(node.comparators) == 1
                and isinstance(node.ops[0], (ast.Eq, ast.NotEq))):
            equal = observed_condition(node.left) == observed_condition(node.comparators[0])
            return equal if isinstance(node.ops[0], ast.Eq) else not equal
        raise AssertionError('unknown externally consumed workflow predicate syntax')

    assert observed_condition(parsed_condition.body) == expected_integration
    assert document['jobs']['local-ci-all']['timeout-minutes'] == '150'
    step = next(step for step in document['jobs']['local-ci-all']['steps'] if step.get('name') == 'Run exact local CI all in a clean test environment')
    assert step['env']['ALL_ONLY'] == '${{ inputs.all_only }}'
    assert document['jobs']['local-ci-all']['if'] == "${{ github.event_name == 'workflow_dispatch' }}"
    assert step['run'] == (
        'if [ "$GITHUB_REPOSITORY" = "t-benze/happyranch" ] && '
        '[ "$GITHUB_EVENT_NAME" = "workflow_dispatch" ] && '
        '[ "$GITHUB_REF" = "refs/heads/task/TASK-10034" ]; then\n'
        '  uv run --frozen --no-sync python scripts/nightly_local_ci_all.py\n'
        'else\n'
        '  uv run python scripts/nightly_local_ci_all.py\n'
        'fi\n'
    )
    assert all(len(actual_step['run']) < 21000
               for actual_job in document['jobs'].values()
               for actual_step in actual_job['steps'] if 'run' in actual_step)
    # ROOT belongs to this keeper's source copy, including archived control checkouts.
    python = ALL_RUNNER.read_text(encoding='utf-8')
    tree = ast.parse(python)
    literals = {node.targets[0].id: ast.literal_eval(node.value) for node in ast.walk(tree)
                if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id in ('FIXED_ISOLATED', 'FIXED_SIBLINGS')}
    expected_isolated = (
        'tests/workflows/test_submission_schema.py::test_g_reviewed_definitions_and_independent_complete_layouts',
        'tests/workflows/test_submission_schema.py::test_g_partial_mixed_metadata_and_unknown_objects_refuse_without_writes',
        'tests/workflows/test_submission_schema.py::test_g_rebuild_preserves_populated_legacy_relations_and_exact_values',
        'tests/workflows/test_submission_schema.py::test_g_legacy_event_mapping_refuses_ambiguity_without_history_repair',
        'tests/workflows/test_submission_schema.py::test_g_revision_uniqueness_and_unsalted_raw_byte_reuse',
        'tests/test_workflow_submission_migration_script.py::test_g_script_target_identity_and_missing_database_refuse',
        'tests/test_workflow_submission_migration_script.py::test_g_script_check_and_populated_noop_are_read_only',
        'tests/test_workflow_submission_migration_script.py::test_g_script_offline_quiescence_and_ownership_refuse_unsafe_work',
        'tests/test_workflow_submission_migration_script.py::test_g_script_old_or_new_visibility_and_atomic_target',
        'tests/test_workflow_submission_migration_script.py::test_g_script_every_interruption_recovers_complete_old_or_new',
        'tests/test_workflow_submission_migration_script.py::test_g_script_two_writers_and_contention_are_bounded',
        'tests/test_workflow_submission_migration_script.py::test_g_script_wal_preserves_committed_source_and_reader_snapshots',
        'tests/workflows/test_submission_schema.py::test_g_pending_operation_and_legacy_result_meanings',
        'tests/workflows/test_submission_schema.py::test_g_exact_result_links_preserve_failed_and_shared_callback_evidence',
        'tests/workflows/test_submission_schema.py::test_g_operation_result_cross_row_corruption_refuses_without_writes',
        'tests/daemon/test_routes_orgs.py::test_deliberate_creation_is_complete_e_before_attachment_and_reopens_twice',
        'tests/daemon/test_org_state.py::test_g_existing_f_e_and_g_cold_load_preserve_workflow_histories',
        'tests/daemon/test_routes_orgs.py::test_g_creation_failure_preserves_existing_org_and_cleans_only_owned_skeleton',
        'tests/test_workflow_release_schema.py::test_shipping_oracle_selects_independent_complete_layout',
        'tests/test_workflow_release_schema.py::test_shipping_oracle_never_relearns_candidate_or_ignores_extra_objects',
        'tests/test_workflow_release_schema.py::test_actual_v2_capture_includes_e_and_remains_observed_only',
        'tests/test_authority_v2_schema_observation.py::test_complete_e_real_v2_claim_continues_and_keeps_observation_diagnostic',
        'tests/workflows/test_cutover.py::test_g_metadata_and_pending_work_are_distinct_and_never_auto_drained',
        'tests/workflows/test_cutover.py::test_g_existing_s2_f_e_g_readiness_and_downgrade_are_truthful',
        'tests/test_workflow_draft_migration_script.py::test_draft_script_populated_g_check_and_noop_label_actual_layout',
        'tests/workflows/test_draft_dispatch.py::test_real_dispatch_result_and_quiescence_complete_without_manager_decision',
        'tests/workflows/test_draft_dispatch.py::test_possible_launch_or_missing_callback_stays_uncertain_and_never_relaunches',
        'tests/workflows/test_draft_dispatch.py::test_completion_retry_compares_every_supplied_result_field',
        'tests/workflows/test_draft_dispatch.py::test_callback_cancel_writer_contenders_commit_one_owned_order',
        'tests/workflows/test_draft_dispatch.py::test_lost_notification_cold_discovery_and_duplicate_enqueue_keep_original_attempt',
        'tests/workflows/test_activation_store.py::test_historical_semantic_corruption_refuses_all_receipt_seams_without_repair',
        'tests/workflows/test_workflow_recovery.py::test_semantic_corruption_fences_every_owned_consumer_before_effects',
        'tests/workflows/test_draft_schema.py::test_source_pinned_preceding_reader_reopens_f_and_refuses_e',
        'tests/workflows/test_draft_schema.py::test_g_source_pinned_faf_and_s2_readers_refuse_g_without_writes',
        'tests/workflows/test_draft_schema.py::test_g_s2_reader_archive_identity_bytes_modes_and_safe_extraction',
        'tests/test_workflow_submission_migration_script.py::test_g_script_source_pinned_v0_v1_initializer_preserves_legacy_rows',
        'tests/daemon/test_workflow_activation_routes.py::test_initial_activation_commits_authentic_root_and_admitted_lane',
        'tests/daemon/test_workflow_activation_routes.py::test_historical_activation_replay_survives_disable_and_authority_change',
        'tests/daemon/test_workflow_activation_routes.py::test_operation_conflict_and_instance_cas_create_no_losing_rows',
        'tests/daemon/test_workflow_activation_routes.py::test_closed_activation_wire_refuses_without_rows_or_private_echo',
        'tests/daemon/test_workflow_activation_routes.py::test_activation_author_binding_requires_current_distinct_same_org_principals',
        'tests/daemon/test_workflow_activation_routes.py::test_failed_event_write_rolls_back_real_task_and_every_domain_row',
        'tests/daemon/test_workflow_activation_routes.py::test_corrupt_historical_request_refuses_with_safe_code_and_no_repair',
        'tests/daemon/test_workflow_activation_routes.py::test_cold_org_reopen_and_new_template_preserve_original_activation',
        'tests/daemon/test_workflow_activation_routes.py::test_activation_reads_session_capacity_before_sqlite_writer',
        'tests/daemon/test_workflow_activation_routes.py::test_nested_activation_records_are_closed_before_discovery',
        'tests/daemon/test_workflow_activation_routes.py::test_nested_activation_values_refuse_aliases_and_coercions_without_effects',
        'tests/daemon/test_workflow_activation_routes.py::test_activation_http_human_boundary_precedes_corrupt_receipt_reads',
        'tests/daemon/test_workflow_activation_routes.py::test_foreign_org_receipt_read_ignores_original_corruption_and_own_readiness',
        'tests/test_cli_workflows.py::test_activation_cli_real_http_create_replay_list_show_and_pending_exit',
        'tests/test_cli_workflows.py::test_activation_cli_transport_errors_are_safe_and_nonzero',
        'tests/workflows/test_activation_store.py::test_process_loss_at_actual_admission_boundary_retains_atomic_graph_and_original_replay',
        'tests/workflows/test_activation_store.py::test_admitted_context_freezes_server_identities_and_full_binding',
        'tests/workflows/test_activation_store.py::test_total_canonical_context_budget_includes_actual_server_envelope',
        'tests/workflows/test_activation_store.py::test_extracted_insert_preserves_all_twenty_fields_and_transaction_owner',
        'tests/workflows/test_activation_store.py::test_activation_allocates_with_existing_max_glob_semantics_and_gaps',
        'tests/workflows/test_activation_store.py::test_independent_activation_writers_contend_on_explicit_instance_and_atomic_root',
        'tests/workflows/test_activation_store.py::test_thread_private_source_requires_existing_recipient_visibility',
        'tests/workflows/test_activation_store.py::test_task_input_refusal_never_creates_domain_rows_or_echoes_private_bytes',
        'tests/workflows/test_activation_store.py::test_activation_commit_failure_rolls_back_before_releasing_publication_lease',
        'tests/workflows/test_activation_store.py::test_supported_target_change_after_capture_refuses_stale_admission_without_new_lease',
        'tests/workflows/test_activation_store.py::test_service_receipt_principal_is_checked_before_any_stored_or_current_read',
        'tests/workflows/test_activation_store.py::test_original_input_snapshot_replays_after_mutable_source_disappears',
        'tests/workflows/test_activation_store.py::test_real_profile_operation_and_activation_serialize_both_orders',
        'tests/workflows/test_activation_store.py::test_activation_discovery_and_notification_are_outside_durable_ownership',
        'tests/workflows/test_activation_store.py::test_actual_disable_and_admission_writer_serialize_without_partial_graph',
        'tests/workflows/test_activation_store.py::test_real_canonical_org_writer_and_activation_serialize_both_orders',
        'tests/workflows/test_activation_store.py::test_admission_and_claim_revalidate_complete_captured_authority_profile_closure',
        'tests/workflows/test_activation_store.py::test_running_work_hour_occupies_author_without_task_tracker_binding',
        'tests/workflows/test_activation_store.py::test_unrelated_or_inactive_work_hour_does_not_substitute_author',
        'tests/workflows/test_activation_store.py::test_replacement_candidates_cannot_bypass_role_or_independence',
        'tests/workflows/test_activation_store.py::test_eligible_replacements_are_frozen_inspectable_and_never_dispatched',
        'tests/workflows/test_activation_store.py::test_canonical_nonactive_agent_cannot_be_bound_or_selected_as_replacement',
        'tests/workflows/test_draft_dispatch.py::test_activation_notifies_only_after_commit_and_never_on_replay',
        'tests/workflows/test_draft_dispatch.py::test_queued_cancel_fences_intent_before_task_terminal_and_replays_read_only',
        'tests/workflows/test_draft_dispatch.py::test_registered_session_refuses_before_reservation_and_early_callback_waits_for_handle',
        'tests/workflows/test_draft_dispatch.py::test_real_callback_cancel_orders_keep_owned_result_and_wait_for_finalized_containment',
        'tests/workflows/test_draft_dispatch.py::test_lost_actual_launch_acknowledgment_survives_cold_reopen_without_second_launch',
        'tests/workflows/test_draft_dispatch.py::test_completion_retry_requires_original_full_result_and_intent_closure',
        'tests/workflows/test_draft_dispatch.py::test_delayed_real_containment_keeps_cancel_and_drain_pending_until_terminal_evidence',
        'tests/workflows/test_draft_dispatch.py::test_real_draft_callback_cannot_create_author_retry_or_manager_authority',
        'tests/workflows/test_draft_dispatch.py::test_real_callback_write_failure_rolls_back_result_pointer_and_event_before_retry',
        'tests/workflows/test_draft_dispatch.py::test_claimed_no_launch_recovers_same_task_before_one_real_attempt',
        'tests/workflows/test_draft_dispatch.py::test_workflow_rate_limit_cannot_consume_ordinary_supervisor_retry_budget',
        'tests/workflows/test_draft_dispatch.py::test_owned_pid_ttl_consumers_cannot_terminalize_uncertain_draft',
        'tests/workflows/test_draft_dispatch.py::test_real_active_author_keeps_other_admitted_draft_pending',
        'tests/workflows/test_draft_dispatch.py::test_finalized_outcome_requires_exact_original_host_request',
        'tests/workflows/test_draft_dispatch.py::test_observed_handle_requires_exact_durable_host_effect',
        'tests/workflows/test_draft_dispatch.py::test_duplicate_genuine_finalizer_is_read_only_before_tracker_release',
        'tests/workflows/test_draft_dispatch.py::test_actual_prelaunch_work_hour_defers_same_intent_before_external_launch',
        'tests/workflows/test_draft_dispatch.py::test_independent_dispatch_claim_observes_busy_then_same_original_cas',
        'tests/workflows/test_draft_dispatch.py::test_live_periodic_discovery_recovers_same_eligible_intent_after_notification_loss',
        'tests/workflows/test_draft_dispatch.py::test_actual_profile_or_disable_writer_and_prelaunch_reservation_both_winners',
        'tests/workflows/test_draft_dispatch.py::test_actual_prelaunch_revalidates_entire_captured_profile_authority_closure',
        'tests/workflows/test_draft_dispatch.py::test_actual_prelaunch_refuses_supported_selected_target_change_after_capture',
        'tests/workflows/test_draft_dispatch.py::test_live_periodic_discovery_never_replays_a_live_or_possible_launch',
        'tests/workflows/test_workflow_recovery.py::test_startup_never_pid_fails_a_draft_root_with_missing_launch_closure',
        'tests/workflows/test_workflow_recovery.py::test_draft_root_never_enters_ordinary_manager_run_step',
        'tests/workflows/test_workflow_recovery.py::test_incomplete_ownership_fences_all_status_branches_and_consumers',
        'tests/workflows/test_workflow_recovery.py::test_task_name_type_and_corrupt_unrelated_workflow_do_not_claim_ordinary_task',
        'tests/scripts/test_nightly_integration_reporting.py::test_nightly_workflow_all_only_selection',
    )
    expected_siblings = (
        'tests/workflows/test_submission_schema.py',
        'tests/test_workflow_submission_migration_script.py',
        'tests/daemon/test_routes_orgs.py',
        'tests/daemon/test_org_state.py',
        'tests/test_workflow_release_schema.py',
        'tests/test_authority_v2_schema_observation.py',
        'tests/workflows/test_cutover.py',
        'tests/test_workflow_draft_migration_script.py',
        'tests/workflows/test_draft_dispatch.py',
        'tests/workflows/test_activation_store.py',
        'tests/workflows/test_workflow_recovery.py',
        'tests/workflows/test_draft_schema.py',
        'tests/daemon/test_workflow_activation_routes.py',
        'tests/test_cli_workflows.py',
        'tests/scripts/test_nightly_integration_reporting.py',
    )
    assert literals['FIXED_ISOLATED'] == expected_isolated
    assert literals['FIXED_SIBLINGS'] == expected_siblings
    assert len(set(expected_isolated)) == 101 and len(set(expected_siblings)) == 15
    assert {node.split('::')[0] for node in expected_isolated} == set(expected_siblings)
    assert "if not PYTHON_UNIT_SUSPENDED and os.environ.get('ALL_ONLY') == 'true' and result.returncode == 0:" in python
    assert "for round_number in (1, 2, 3, 4, 5):" in python
    assert "'--basetemp', str(basetemp)" in python and "'--junitxml'" in python
    assert python.index("receipt['exit_code'] = result.returncode") < python.index('FIXED_ISOLATED')
    controls = next(ast.literal_eval(node.value) for node in ast.walk(tree)
                    if isinstance(node, ast.Assign) and len(node.targets) == 1
                    and isinstance(node.targets[0], ast.Name) and node.targets[0].id == 'SOURCE_CONTROLS')
    # The real source-copy controls retain their IDs and original case closure.
    assert len(controls) == 40
    control_by_id = {control['id']: control for control in controls}
    assert control_by_id['closed-ci-selection']['patches'] == [{
        'path': 'scripts/nightly_local_ci_all.py', 'function': None,
        'old': "'tests/workflows/test_draft_dispatch.py::test_delayed_real_containment_keeps_cancel_and_drain_pending_until_terminal_evidence',",
        'new': "'tests/workflows/test_draft_dispatch.py::test_omitted_delayed_real_containment_keeps_cancel_and_drain_pending_until_terminal_evidence',"}]
    assert control_by_id['preservation-control-plan']['patches'] == [{
        'path': 'scripts/nightly_local_ci_all.py', 'function': None,
        'old': "'id': 'g-validator-physical-no-write'",
        'new': "'id': 'missing-g-validator-physical-no-write'"}]
    assert control_by_id['typed-ci-flag']['patches'] == [{
        'path': '.github/workflows/nightly-integration.yml', 'function': None,
        'old': '        type: boolean', 'new': '        type: string'}]
    assert control_by_id['safe-ci-default']['patches'] == [{
        'path': '.github/workflows/nightly-integration.yml', 'function': None,
        'old': '        default: false', 'new': '        default: true'}]
    assert all(control_by_id[identity]['isolated_ids'] == [101] for identity in (
        'closed-ci-selection', 'preservation-control-plan', 'typed-ci-flag', 'safe-ci-default'))
    manifest = next(node.value for node in ast.walk(tree)
                    if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)
                    and node.targets[0].id == 'manifest')
    assert isinstance(manifest, ast.DictComp)
    assert ast.literal_eval(ast.Tuple(elts=manifest.generators[0].iter.elts[:2], ctx=ast.Load())) == (
        '.github/workflows/nightly-integration.yml', 'scripts/nightly_local_ci_all.py')
    cold = control_by_id['lost-notification-discovery']
    assert cold['isolated_ids'] == [30] and cold['red_kind'] == 'business'
    assert expected_isolated[29].endswith('::test_lost_notification_cold_discovery_and_duplicate_enqueue_keep_original_attempt')
    assert cold['patches'] == [{'path': 'runtime/workflows/recovery.py', 'function': 'recover_owned_task',
        'old': '        queue.enqueue_if_absent(slug, task_id)',
        'new': '        return True  # source-copy regression: lose initial queued-draft discovery'}]
    assert cold['required_assertion'] == 'durable lost notification was not rediscovered exactly once'
    assert cold['required_source'] == 'assert state.queue._queue.qsize() == 1'
    assert cold['required_observed'] == 'assert 0 == 1'
    revision = control_by_id['original-event-revision']
    assert revision['isolated_ids'] == [3]
    assert revision['required_assertion'] == 'assert {9} == {4}'
    assert revision['required_source'] == "assert {r[0] for r in observer.execute('SELECT revision FROM workflow_events')} == {4}"
    predicate = control_by_id['manual-integration-predicate']
    assert predicate['isolated_ids'] == [101]
    assert predicate['patches'] == [{'path': '.github/workflows/nightly-integration.yml', 'function': None,
        'old': "\n    if: ${{ github.event_name == 'schedule' }}\n", 'new': "\n    if: ${{ github.event_name != 'schedule' }}\n"}]
    assert "body.replace(patch['old'], patch['new'], 1)" in python
    suspension = next(node for node in tree.body if isinstance(node, ast.Assign)
                      and isinstance(node.targets[0], ast.Name) and node.targets[0].id == 'PYTHON_UNIT_SUSPENDED')
    assert isinstance(suspension.value, ast.Constant) and suspension.value.value is True
    follow_on = next(node for node in tree.body if isinstance(node, ast.If)
                     and any(isinstance(child, ast.Name) and child.id == 'PYTHON_UNIT_SUSPENDED'
                             for child in ast.walk(node.test)))
    assert isinstance(follow_on.test, ast.BoolOp) and isinstance(follow_on.test.op, ast.And)
    assert isinstance(follow_on.test.values[0], ast.UnaryOp) and isinstance(follow_on.test.values[0].op, ast.Not)
    assert follow_on.test.values[0].operand.id == 'PYTHON_UNIT_SUSPENDED'
    # All unit/collection/control/repetition execution remains inside that guard.
    assert not any(isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                   and node.func.id in {'fixed_command', 'source_controls', 'run_phase'}
                   for statement in tree.body if statement is not follow_on for node in ast.walk(statement))
    assert "'status': 'SUSPENDED / SKIPPED'" in python and "'children_launched': 0" in python
    assert python.index("print(json.dumps(receipt['g_follow_on']") < python.index('FIXED_ISOLATED')
    assert len({control['id'] for control in controls}) == len(controls)
    assert {i for control in controls for i in control['isolated_ids']} == set(range(1, 102))
    origin = next(control for control in controls if control['red_kind'] == 'mixed-origin')
    assert origin['isolated_ids'] == [1, 16, *range(37, 101)]
    for control in controls:
        assert control['patches'] and control['red_kind'] in {'business', 'mixed-origin'}
        for patch in control['patches']:
            assert patch['old'] != patch['new']
            assert not (patch['function'] or '').startswith('test_')
    # The uploaded fixed control plan must own the corrected successful-reopen
    # identity contract AND both physical validator boundaries. These are
    # separate from old-reader refusal, whose immutable archive is never mutated.
    preservation = {control['id']: control for control in controls
                    if control['id'] in {'g-reopen-durable-identity',
                                         'g-validator-physical-no-write',
                                         'g-refusal-physical-no-write'}}
    assert set(preservation) == {'g-reopen-durable-identity',
                                 'g-validator-physical-no-write',
                                 'g-refusal-physical-no-write'}
    assert all(control['isolated_ids'] == [34] and control['red_kind'] == 'business'
               for control in preservation.values())
    assert all(control['required_assertion'] for control in preservation.values())
    # These fields are the externally retained attribution/restoration contract,
    # independently inspected by the receiving reviewer/QA on the real run.
    assert "receipt['restoration'] == receipt['originals']" in python
    assert "'source-controls-complete.json'" in python
    assert "'red_attribution_failed_after_restored_green'" in python
    assert python.index('source_controls()\n') < python.index('for round_number in (1, 2, 3, 4, 5):')

    # The shipping scheduler must reap started children and retain failures before
    # admitting another phase. Extract only its actual definition, never all CI.
    import concurrent.futures
    import threading
    import time
    nested = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    assert 'run_batch' in nested, 'missing bounded child scheduler'
    namespace = {'concurrent': concurrent, 'json': __import__('json'),
                 'owned': tmp_path, 'head': 'test-source', 'compact_error': lambda exc: {'message': str(exc)}}
    exec(compile(ast.Module(body=[nested['run_batch']], type_ignores=[]), '<workflow scheduler>', 'exec'), namespace)
    lock = threading.Lock()
    active = peak = 0
    finished = []

    def child(item):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        try:
            time.sleep(0.01)
            if item == 2:
                raise RuntimeError('actual child failure')
        finally:
            with lock:
                active -= 1
                finished.append(item)

    with pytest.raises(RuntimeError, match='actual child failure'):
        namespace['run_batch'](tuple(range(9)), child)
    assert 1 < peak <= 4, ('observed child maximum', peak, 'expected 2..4')
    assert active == 0 and sorted(finished) == list(range(9)), finished
    finished.clear()
    namespace['run_batch']((10, 11), lambda item: finished.append(item))
    assert sorted(finished) == [10, 11]
    rounds = next(n for n in ast.walk(tree) if isinstance(n, ast.For)
                  and isinstance(n.target, ast.Name) and n.target.id == 'round_number')
    phases = [n.value.args[0].value for n in rounds.body
              if isinstance(n, ast.Expr) and isinstance(n.value, ast.Call)
              and isinstance(n.value.func, ast.Name) and n.value.func.id == 'run_phase']
    assert phases == ['isolated', 'sibling'], phases
    assert 'run_batch(SOURCE_CONTROLS, source_control)' in python
    assert "'UV_NO_SYNC': '1'" in python
    assert "'PYTHONDONTWRITEBYTECODE': '1'" in python
    assert "'cache_dir=' + str(root / 'cache/pytest')" in python
    assert "record['import_proof']" in python
    assert "'full-log.json'" in python and "'raw_sha256'" in python
    assert "'stored_sha256'" in python and "'complete'" in python
    assert "log_manifest['streams']['stdout']['segments']" in python
    assert "record['junit'] = capture_stream" in python
    assert "'junit_manifest': str(command_dir / 'junit.xml.json')" in python
    assert "'failure': n.find('failure').text" not in python
    assert "error=str(exc)" not in python and "'errors': str(exc)" not in python

    # Observe the actual shipping stream capture, including a truncated tail and
    # a nonzero child. Full byte identity is independent of the runner's tail.
    import gzip
    import hashlib
    import json
    import os
    import signal
    namespace.update(gzip=gzip, hashlib=hashlib, os=os, pathlib=__import__('pathlib'),
                     signal=signal, subprocess=subprocess, sys=sys, source=ROOT, evidence=tmp_path,
                     concurrent=concurrent, threading=threading, artifact_lock=threading.Lock(), artifact_reserved=0,
                     SEGMENT_BYTES=8 * 1024 * 1024, MAX_MEMBER_BYTES=128 * 1024 * 1024,
                     MAX_ARCHIVE_BYTES=512 * 1024 * 1024)
    exec(compile(ast.Module(body=[nested['capture_stream'], nested['bounded_run']], type_ignores=[]), '<workflow capture>', 'exec'), namespace)
    log_root = tmp_path / 'lossless'
    log_root.mkdir()
    payload = b'contract-stream\n' * 80000
    argv = [sys.executable, '-c', "import sys; sys.stdout.buffer.write(b'contract-stream\\n'*80000); sys.exit(7)"]
    status, metadata = namespace['bounded_run'](argv, cwd=ROOT, env=os.environ.copy(),
        directory=log_root, tail=log_root / 'tail.log')
    assert status == 7 and metadata['exit_code'] == 7
    assert metadata['complete'] is True
    assert gzip.decompress((log_root / 'full.log.gz').read_bytes()) == payload
    assert metadata['raw_bytes'] == len(payload)
    assert metadata['raw_sha256'] == hashlib.sha256(payload).hexdigest()
    stored = (log_root / 'full.log.gz').read_bytes()
    assert metadata['stored_bytes'] == len(stored)
    assert metadata['stored_sha256'] == hashlib.sha256(stored).hexdigest()
    assert json.loads((log_root / 'full-log.json').read_text()) == metadata
    assert (log_root / 'tail.log').read_bytes().startswith(b'[nightly log truncated;')
    # Keep original tail/complete/nonzero assertions; independently consume every
    # ordered segment and the separate runner-stderr manifest as retained files.
    assert metadata['streams']['stderr']['complete'] is True
    assert metadata['streams']['stderr']['raw_bytes'] == 0
    namespace['SEGMENT_BYTES'] = 128 * 1024  # Test-local capture input, no production seam.
    segmented = tmp_path / 'segmented'
    segmented.mkdir()
    status, split = namespace['bounded_run'](argv, cwd=ROOT, env=os.environ.copy(),
        directory=segmented, tail=segmented / 'tail.log')
    assert status == 7 and split['complete'] is True
    assert len(split['segments']) > 1
    chunks = []
    for ordinal, segment in enumerate(split['segments'], 1):
        stored = Path(segment['path']).read_bytes()
        raw = gzip.decompress(stored)
        assert segment['order'] == ordinal and segment['complete'] is True
        assert len(raw) == segment['raw_bytes'] <= namespace['SEGMENT_BYTES']
        assert len(stored) == segment['stored_bytes'] <= namespace['MAX_MEMBER_BYTES']
        assert hashlib.sha256(raw).hexdigest() == segment['raw_sha256']
        assert hashlib.sha256(stored).hexdigest() == segment['stored_sha256']
        chunks.append(raw)
    assert b''.join(chunks) == payload
    assert split['raw_sha256'] == hashlib.sha256(payload).hexdigest()
    assert json.loads((segmented / 'full-log.json').read_text()) == split
    # Capture bound refusal and compact error references through the same source.
    import io
    namespace.update(io=io, tempfile=__import__('tempfile'))
    exec(compile(ast.Module(body=[nested['compact_error']], type_ignores=[]), '<workflow errors>', 'exec'), namespace)
    message = 'attributable failure evidence\n' * 80000
    error = namespace['compact_error'](RuntimeError(message))
    raw = message.encode()
    assert len(error['message'].encode()) <= 1024
    assert error['message_bytes'] == len(raw) and error['message_sha256'] == hashlib.sha256(raw).hexdigest()
    manifest = json.loads(Path(error['message_manifest']).read_text())
    assert error['message_complete'] is True and manifest['complete'] is True
    assert b''.join(gzip.decompress(Path(segment['path']).read_bytes()) for segment in manifest['segments']) == raw
    namespace['MAX_ARCHIVE_BYTES'] = 1  # Test-local bound, not a production override.
    refused = tmp_path / 'refused'
    refused.mkdir()
    with pytest.raises(RuntimeError, match='archive bound'):
        namespace['capture_stream'](io.BytesIO(b'not silently discarded'), directory=refused, stem='bound.log')
    incomplete = json.loads((refused / 'bound.log.json').read_text())
    assert incomplete['complete'] is False and incomplete['raw_bytes'] == 0

    required_assignment = next(n for n in ast.walk(nested['fixed_command'])
        if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name)
        and n.targets[0].id == 'required')
    coverage = {'FIXED_ISOLATED': expected_isolated, 'phase': 'sibling',
                'selectors': [expected_siblings[0]]}
    exec(compile(ast.Module(body=[required_assignment], type_ignores=[]), '<workflow coverage>', 'exec'), coverage)
    assert coverage['required'] == {node for node in expected_isolated
                                   if node.split('::')[0] == expected_siblings[0]}
    assert "receipt['cleanup_exit'] = 0 if directory is not None and not pathlib.Path(directory).exists() else None" in python
