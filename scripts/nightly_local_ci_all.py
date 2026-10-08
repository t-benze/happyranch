import concurrent.futures, gzip, hashlib, io, json, os, pathlib, shutil, signal, subprocess, sys, tempfile, threading


MAX_MEMBER_BYTES = 128 * 1024 * 1024
MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
SEGMENT_BYTES = 8 * 1024 * 1024
artifact_lock = threading.Lock()
artifact_reserved = 0


def capture_stream(reader, *, directory, stem):
    # Ordered gzip members keep complete raw evidence below acquisition bounds.
    # A bound refusal retains partial files and is never a complete capture.
    global artifact_reserved
    metadata = {'complete': False, 'raw_bytes': 0, 'raw_sha256': None,
                'stored_bytes': 0, 'stored_sha256': None, 'segments': []}
    raw_digest, stored_digest = hashlib.sha256(), hashlib.sha256()
    pending = b''
    try:
        while True:
            pending = reader.read(65536)
            if not pending:
                break
            reservation = SEGMENT_BYTES + 65536
            with artifact_lock:
                existing = sum(f.stat().st_size for f in evidence.rglob('*')
                               if f.is_file() and 'scratch' not in f.relative_to(evidence).parts)
                # Reserve overhead for tails and compact receipts before admission.
                if existing + artifact_reserved + reservation + 2 * 1048576 > MAX_ARCHIVE_BYTES:
                    raise RuntimeError('artifact archive bound: incomplete capture retained')
                artifact_reserved += reservation
            number = len(metadata['segments']) + 1
            name = stem + ('' if number == 1 else '-' + str(number).zfill(6)) + '.gz'
            path = directory / name
            segment = {'order': number, 'path': str(path), 'complete': False,
                       'raw_bytes': 0, 'raw_sha256': None, 'stored_bytes': None, 'stored_sha256': None}
            metadata['segments'].append(segment)
            segment_digest = hashlib.sha256()
            try:
                with gzip.open(path, 'wb') as stream:
                    while pending and segment['raw_bytes'] < SEGMENT_BYTES:
                        stream.write(pending)
                        segment_digest.update(pending)
                        raw_digest.update(pending)
                        segment['raw_bytes'] += len(pending)
                        metadata['raw_bytes'] += len(pending)
                        pending = b''
                        if segment['raw_bytes'] < SEGMENT_BYTES:
                            pending = reader.read(min(65536, SEGMENT_BYTES - segment['raw_bytes']))
                segment['complete'] = True
            finally:
                segment['raw_sha256'] = segment_digest.hexdigest()
                if path.exists():
                    digest = hashlib.sha256()
                    with path.open('rb') as stored:
                        for chunk in iter(lambda: stored.read(65536), b''):
                            digest.update(chunk)
                            stored_digest.update(chunk)
                    segment.update(stored_bytes=path.stat().st_size, stored_sha256=digest.hexdigest())
                    metadata['stored_bytes'] += segment['stored_bytes']
                with artifact_lock:
                    artifact_reserved -= reservation
            if segment['stored_bytes'] > MAX_MEMBER_BYTES:
                raise RuntimeError('artifact member bound: incomplete capture retained')
        metadata['complete'] = True
    finally:
        metadata.update(raw_sha256=raw_digest.hexdigest(), stored_sha256=stored_digest.hexdigest())
        (directory / (stem + '.json')).write_text(json.dumps(metadata, indent=2) + '\n')
    return metadata


def bounded_run(argv, *, cwd, env, directory, tail):
    metadata = {'argv': argv, 'cwd': str(cwd), 'complete': False, 'exit_code': None,
                'stdout_contract': 'runner stdout includes merged child stdout/stderr',
                'stderr_contract': 'runner stderr, separately captured', 'streams': {}}
    path = directory / 'full-log.json'
    path.write_text(json.dumps(metadata, indent=2) + '\n')
    process = subprocess.Popen([sys.executable, str(source / 'scripts/run_bounded_output.py'),
        '--output', str(tail), '--max-bytes', '1048576', '--', *argv],
        cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as readers:
            futures = {'stdout': readers.submit(capture_stream, process.stdout, directory=directory, stem='full.log'),
                       'stderr': readers.submit(capture_stream, process.stderr, directory=directory, stem='runner-stderr.log')}
            try:
                for name, future in futures.items():
                    metadata['streams'][name] = future.result()
            except BaseException:
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGTERM)
                    try: process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid, signal.SIGKILL)
                        process.wait(timeout=10)
                raise
        metadata.update(exit_code=process.wait(), complete=True)
        metadata.update({key: metadata['streams']['stdout'][key]
                         for key in ('raw_bytes', 'raw_sha256', 'stored_bytes', 'stored_sha256', 'segments')})
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            try: process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=10)
        process.stdout.close()
        process.stderr.close()
        # Read partial stream manifests too; preserve cleanup and error evidence.
        for name, stem in (('stdout', 'full.log'), ('stderr', 'runner-stderr.log')):
            manifest_path = directory / (stem + '.json')
            if manifest_path.exists():
                metadata['streams'][name] = json.loads(manifest_path.read_text())
        metadata['reaped_exit'] = process.returncode
        path.write_text(json.dumps(metadata, indent=2) + '\n')
    return metadata['exit_code'], metadata


def compact_error(exc):
    # Failure texts already live in authenticated command/JUnit evidence.
    raw = str(exc).encode('utf-8')
    directory = pathlib.Path(tempfile.mkdtemp(prefix='error-', dir=evidence))
    try:
        capture = capture_stream(io.BytesIO(raw), directory=directory, stem='error.txt')
    except Exception:
        capture = json.loads((directory / 'error.txt.json').read_text())
    return {'type': type(exc).__name__, 'message': raw[:1024].decode('utf-8', errors='replace'),
            'message_bytes': len(raw), 'message_sha256': hashlib.sha256(raw).hexdigest(),
            'message_manifest': str(directory / 'error.txt.json'), 'message_complete': capture['complete'],
            'evidence_root': str(evidence)}


source = pathlib.Path.cwd()
head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
assert head == os.environ['GITHUB_SHA']
assert not subprocess.check_output(['git', 'status', '--porcelain'])
assert sys.version_info[:2] == (3, 14)
evidence = pathlib.Path(os.environ['RUNNER_TEMP']) / 'local-ci-all'
evidence.mkdir()
receipt = {'event': os.environ['GITHUB_EVENT_NAME'], 'ref': os.environ['GITHUB_REF'],
           'github_sha': os.environ['GITHUB_SHA'], 'checkout': head,
           'command': 'scripts/local_ci.sh all', 'all_only': os.environ.get('ALL_ONLY') == 'true',
           'python': sys.executable, 'cwd': str(source),
           'tree': subprocess.check_output(['git', 'rev-parse', 'HEAD^{tree}'], text=True).strip(),
           'python_version': sys.version, 'source_sha256': {
               name: hashlib.sha256((source / name).read_bytes()).hexdigest()
               for name in ('scripts/local_ci.sh', 'tests/helpers/integration_parent.py',
                            'tests/helpers/integration_stub_guard/guard.py', 'cli/main.py')}}
with tempfile.TemporaryDirectory(prefix='local-ci-parent-') as directory:
    root = pathlib.Path(directory)
    for name in ('home', 'config', 'cache', 'tmp', 'daemon', 'bin'):
        (root / name).mkdir(mode=0o700)
    (root / 'daemon/executors.json').write_text('{}')
    for tool in ('uv', 'node', 'npm', 'npx'):
        resolved = shutil.which(tool)
        assert resolved is not None, tool
        (root / 'bin' / tool).symlink_to(pathlib.Path(resolved).resolve())
    env = {'HOME': str(root / 'home'), 'XDG_CONFIG_HOME': str(root / 'config'),
           'XDG_CACHE_HOME': str(root / 'cache'), 'TMPDIR': str(root / 'tmp'),
           'TMP': str(root / 'tmp'), 'TEMP': str(root / 'tmp'),
           'HAPPYRANCH_DAEMON_HOME': str(root / 'daemon'), 'HAPPYRANCH_DAEMON_PORT': '0',
           'PATH': os.pathsep.join((str(root / 'bin'), str(source / '.venv/bin'), '/usr/local/bin', '/usr/bin', '/bin')),
           'UV_PYTHON': sys.executable, 'UV_PYTHON_DOWNLOADS': 'never',
           'UV_CACHE_DIR': str(root / 'cache/uv'), 'PYTHONNOUSERSITE': '1',
           'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8', 'CI': 'true'}
    receipt['tools'] = {tool: {'path': str((root / 'bin' / tool).resolve()),
                              'version': subprocess.check_output([tool, '--version'], env=env, text=True).strip()}
                        for tool in ('uv', 'node', 'npm')}
    assert receipt['tools']['node']['version'].split('.')[0] == 'v24'
    receipt_path = evidence / 'provenance.json'
    receipt_path.write_text(json.dumps(receipt, indent=2) + '\n')
    print(json.dumps(receipt, sort_keys=True), flush=True)
    all_exit, receipt['full_log'] = bounded_run(['scripts/local_ci.sh', 'all'],
        cwd=source, env=env, directory=evidence, tail=evidence / 'local-ci-all.log')
    result = subprocess.CompletedProcess(['scripts/local_ci.sh', 'all'], all_exit)
    receipt['exit_code'] = result.returncode
    receipt['environment'] = env
    receipt_path.write_text(json.dumps(receipt, indent=2) + '\n')
    print('scripts/local_ci.sh all exit_code=' + str(result.returncode), flush=True)
receipt['cleanup_exit'] = 0 if not root.exists() else None
receipt_path.write_text(json.dumps(receipt, indent=2) + '\n')
PYTHON_UNIT_SUSPENDED = True  # THR291 seq5/16; release requires ordinary reviewed source change.
receipt['g_follow_on'] = {'status': 'SUSPENDED / SKIPPED' if PYTHON_UNIT_SUSPENDED else 'eligible',
                          'authority': 'THR291 seq5/16; THR139 seq429',
                          'execution': 'NOT RUN', 'selectors': 101, 'siblings': 15, 'controls': 40,
                          'repetitions': 580, 'children_launched': 0}
receipt_path.write_text(json.dumps(receipt, indent=2) + '\n')
print(json.dumps(receipt['g_follow_on'], sort_keys=True), flush=True)
if not PYTHON_UNIT_SUSPENDED and os.environ.get('ALL_ONLY') == 'true' and result.returncode == 0:
    FIXED_ISOLATED = (
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
    FIXED_SIBLINGS = (
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
    import datetime, xml.etree.ElementTree as ET
    assert len(FIXED_ISOLATED) == len(set(FIXED_ISOLATED)) == 101
    assert len(FIXED_SIBLINGS) == len(set(FIXED_SIBLINGS)) == 15
    assert {node.split('::')[0] for node in FIXED_ISOLATED} == set(FIXED_SIBLINGS)
    tree = subprocess.check_output(['git', 'rev-parse', 'HEAD^{tree}'], text=True).strip()
    owned = evidence / (os.environ['GITHUB_RUN_ID'] + '-' + os.environ['GITHUB_RUN_ATTEMPT'])
    (owned / 'scratch').mkdir(parents=True)
    manifest = {name: {'sha256': hashlib.sha256((source / name).read_bytes()).hexdigest(),
                       'mode': (source / name).stat().st_mode & 0o777}
                for name in ('.github/workflows/nightly-integration.yml', 'scripts/nightly_local_ci_all.py', 'docs/local-ci.md',
                             'pyproject.toml', 'uv.lock', 'runtime/infrastructure/workflow_schema.py',
                             'scripts/migrate_workflow_submission_schema.py', *FIXED_SIBLINGS)}
    (owned / 'source-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')


    def run_batch(items, invoke):
        errors = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            futures = [(item, executor.submit(invoke, item)) for item in items]
            for item, future in futures:
                try: future.result()
                except Exception as exc: errors.append({'item': str(item)[:256], 'error': compact_error(exc)})
        if errors:
            (owned / 'batch-errors.json').write_text(json.dumps({'head': head, 'errors': errors}, indent=2) + '\n')
            raise RuntimeError('actual child failure; compact errors in ' + str(owned / 'batch-errors.json'))

    def fixed_command(selectors, phase, identity, round_number, *, collect=False, execution_source=None, red_kind=None, required_assertion=None, required_source=None, required_observed=None):
        execution_source = source if execution_source is None else execution_source
        command_dir = owned / 'commands' / phase / identity / ('round-' + str(round_number))
        command_dir.mkdir(parents=True)
        scratch = owned / 'scratch' / phase / identity / ('round-' + str(round_number))
        scratch.mkdir(parents=True)
        record = {'head': head, 'tree': tree, 'cwd': str(execution_source), 'phase': phase, 'red_kind': red_kind,
                  'identity': identity, 'round': round_number, 'selectors': selectors,
                  'full_log_manifest': str(command_dir / 'full-log.json'), 'junit_manifest': str(command_dir / 'junit.xml.json'),
                  'required_assertion': required_assertion, 'required_source': required_source, 'required_observed': required_observed,
                  'python': sys.executable, 'python_version': sys.version,
                  'run_id': os.environ['GITHUB_RUN_ID'], 'run_attempt': os.environ['GITHUB_RUN_ATTEMPT'],
                  'source_manifest': str(owned / 'source-manifest.json'),
                  'expected_dimension_manifest': str(owned / 'collection-nodes.json'), 'state': 'not_started',
                  'pytest_exit': None, 'wrapper_exit': None, 'cleanup_exit': None}
        record_path = command_dir / 'receipt.json'
        def persist(): record_path.write_text(json.dumps(record, indent=2) + '\n')
        persist()
        root = None
        try:
            with tempfile.TemporaryDirectory(prefix='owned-', dir=scratch) as directory:
                root = pathlib.Path(directory).resolve()
                for name in ('home', 'config', 'cache', 'tmp', 'daemon', 'bin'):
                    (root / name).mkdir(mode=0o700)
                (root / 'daemon/executors.json').write_text('{}')
                for tool in ('uv', 'node', 'npm', 'npx'):
                    resolved = shutil.which(tool)
                    assert resolved is not None, tool
                    (root / 'bin' / tool).symlink_to(pathlib.Path(resolved).resolve())
                env = {'HOME': str(root / 'home'), 'XDG_CONFIG_HOME': str(root / 'config'),
                       'XDG_CACHE_HOME': str(root / 'cache'), 'TMPDIR': str(root / 'tmp'),
                       'TMP': str(root / 'tmp'), 'TEMP': str(root / 'tmp'),
                       'HAPPYRANCH_DAEMON_HOME': str(root / 'daemon'), 'HAPPYRANCH_DAEMON_PORT': '0',
                       'PATH': os.pathsep.join((str(root / 'bin'), str(source / '.venv/bin'), '/usr/local/bin', '/usr/bin', '/bin')),
                       'UV_PYTHON': sys.executable, 'UV_PYTHON_DOWNLOADS': 'never',
                       'UV_NO_SYNC': '1', 'PYTHONDONTWRITEBYTECODE': '1',
                       'PYTHONPATH': str(execution_source),
                       'UV_CACHE_DIR': str(root / 'cache/uv'), 'PYTHONNOUSERSITE': '1',
                       'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8', 'CI': 'true'}
                basetemp = root / 'pytest'
                assert basetemp.is_absolute() and not basetemp.exists() and not root.is_symlink()
                assert not subprocess.check_output(['git', 'status', '--porcelain'])
                assert subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip() == head
                for name, pinned in manifest.items():
                    assert hashlib.sha256((source / name).read_bytes()).hexdigest() == pinned['sha256'], name
                    assert (source / name).stat().st_mode & 0o777 == pinned['mode'], name
                argv = ['uv', 'run', 'python', '-m', 'pytest', *selectors, '-v', '--tb=short', '-o', 'verbosity_assertions=0', '-m', 'not integration',
                        '--basetemp', str(basetemp), '-o', 'cache_dir=' + str(root / 'cache/pytest')]
                if collect: argv.append('--collect-only')
                else: argv += ['--junitxml', str(root / 'junit.xml')]
                record.update(state='running', command=argv, basetemp=str(basetemp),
                              started_at=datetime.datetime.now(datetime.timezone.utc).isoformat())
                persist()
                print('BEGIN ' + phase + '/' + identity + '/round-' + str(round_number), flush=True)
                proof_code = ('import json,pathlib,sys,runtime.infrastructure.workflow_schema as m; '
                    'print(json.dumps({"module":str(pathlib.Path(m.__file__).resolve()),'
                    '"python":sys.executable,"version":sys.version}))')
                record['import_proof'] = json.loads(subprocess.check_output(
                    [sys.executable, '-c', proof_code], cwd=execution_source, env=env, text=True))
                assert pathlib.Path(record['import_proof']['module']) == (execution_source / 'runtime/infrastructure/workflow_schema.py').resolve()
                record['environment'] = env
                record['tools'] = {tool: {'path': str((root / 'bin' / tool).resolve()),
                    'version': subprocess.check_output([tool, '--version'], env=env, text=True).strip()}
                    for tool in ('uv', 'node', 'npm')}
                assert record['tools']['node']['version'].split('.')[0] == 'v24'
                persist()
                try:
                    status, record['full_log'] = bounded_run(argv, cwd=execution_source,
                        env=env, directory=command_dir, tail=command_dir / 'output.log')
                finally:
                    # Preserve even partial JUnit before TemporaryDirectory cleanup.
                    junit_path = root / 'junit.xml'
                    if junit_path.exists():
                        with junit_path.open('rb') as raw_junit:
                            record['junit'] = capture_stream(raw_junit, directory=command_dir, stem='junit.xml')
                        persist()
                run = subprocess.CompletedProcess(argv, status)
                record.update(wrapper_exit=run.returncode, finished_at=datetime.datetime.now(datetime.timezone.utc).isoformat())
                print('END ' + phase + '/' + identity + '/round-' + str(round_number)
                      + ' exit=' + str(run.returncode), flush=True)
                # A normal bounded-wrapper return propagates pytest's status.
                if run.returncode in (0, 1, 2, 3, 4, 5): record['pytest_exit'] = run.returncode
                persist()
                if run.returncode != (1 if red_kind else 0):
                    raise RuntimeError('unexpected fixed verification exit: ' + identity)
                if not collect:
                    assert record['junit']['complete']
                    required = {node for node in FIXED_ISOLATED if node.split('::')[0] in selectors} if phase == 'sibling' else set(selectors)
                    record['cases'] = []
                    attributable = set()
                    for _, element in ET.iterparse(junit_path, events=('end',)):
                        if element.tag != 'testcase':
                            continue
                        case = {'classname': element.get('classname'), 'name': element.get('name'),
                                'skipped': element.find('skipped') is not None, 'failure': None, 'error': None}
                        node = case['classname'].replace('.', '/') + '.py::' + case['name'].split('[')[0]
                        for kind in ('failure', 'error'):
                            diagnostic = element.find(kind)
                            if diagnostic is None:
                                continue
                            text = diagnostic.text or ''
                            raw = text.encode('utf-8')
                            case[kind] = {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest(),
                                          'junit_manifest': str(command_dir / 'junit.xml.json'),
                                          'case_ordinal': len(record['cases']), 'element': kind}
                            if not red_kind:
                                raise RuntimeError('unexpected JUnit ' + kind + ' at ' + node + '; see authenticated JUnit')
                            if node not in selectors or kind != ('error' if red_kind == 'origin' else 'failure'):
                                raise RuntimeError('unrelated/setup/import failure at ' + node + '; see authenticated JUnit')
                            if red_kind == 'origin':
                                assert '_assert_activation_org_layout' in text and 'AssertionError' in text
                            else:
                                # Pytest comparison diffs are actual assertions even without the
                                # literal exception class. Setup/import errors are rejected above.
                                assert 'AssertionError' in text or 'DID NOT RAISE' in text or any(
                                    line.lstrip().startswith('E') and 'assert ' in line for line in text.splitlines()), 'not business assertion at ' + node
                                if required_assertion is not None:
                                    assert required_assertion in text, 'RED missed intended assertion at ' + node
                                if required_source is not None:
                                    assert required_source in text, 'RED missed intended source at ' + node
                                if required_observed is not None:
                                    assert required_observed in text, 'RED missed observed/expected at ' + node
                            attributable.add(node)
                        record['cases'].append(case)
                        assert not case['skipped'] or node not in required, node
                        element.clear()
                    assert record['cases']
                    expected_nodes = json.loads((owned / 'collection-nodes.json').read_text())
                    expected_cases = {n for n in expected_nodes if n.split('[')[0] in required}
                    actual_cases = {n['classname'].replace('.', '/') + '.py::' + n['name'] for n in record['cases']}
                    assert expected_cases and expected_cases <= actual_cases
                    record['required_case_coverage'] = sorted(expected_cases)
                    if red_kind:
                        assert set(selectors) <= attributable, 'no attributable ' + red_kind + ' RED at named contract'
                        record['attribution'] = {'kind': red_kind, 'nodes': sorted(attributable),
                                                 'required_assertion': required_assertion, 'required_source': required_source,
                                                 'required_observed': required_observed, 'junit': str(command_dir / 'junit.xml.json')}
                    persist()
                    # Remove only the duplicate raw XML after complete authenticated capture.
                    assert record['junit']['complete'] and record['junit']['raw_bytes'] == junit_path.stat().st_size
                    junit_path.unlink()
            record.update(state='expected_red' if red_kind else 'passed', cleanup_exit=0)
            persist()
        except BaseException as exc:
            record['error'] = compact_error(exc)
            record['state'] = 'failed_or_interrupted'
            # Observe actual scratch removal even on an assertion failure.
            record['cleanup_exit'] = 0 if root is not None and not root.exists() else None
            persist()
            raise
        return command_dir

    import ast, io, tarfile
    SOURCE_CONTROLS = ({'id': 'fresh-origin', 'isolated_ids': [1, 16, 37, 38, 39, 40, 41, 42, 43, 44, 45, 46, 47, 48, 49, 50, 51, 52, 53, 54, 55, 56, 57, 58, 59, 60, 61, 62, 63, 64, 65, 66, 67, 68, 69, 70, 71, 72, 73, 74, 75, 76, 77, 78, 79, 80, 81, 82, 83, 84, 85, 86, 87, 88, 89, 90, 91, 92, 93, 94, 95, 96, 97, 98, 99, 100], 'red_kind': 'mixed-origin', 'patches': [{'path': 'runtime/infrastructure/workflow_schema.py', 'function': 'initialize_complete_org_schema', 'old': '# Proven empty creation:', 'new': 'return\n        # Proven empty creation:'}]}, {'id': 'complete-object-reference', 'isolated_ids': [2], 'red_kind': 'business', 'patches': [{'path': 'runtime/infrastructure/workflow_schema.py', 'function': 'validate_workflow_schema', 'old': '    layout = _validate_installed(conn, expected_org_slug=expected_org_slug)', 'new': '    observed = _layout(conn)\n    names = {row[1] for row in observed[0]}\n    layout = "G" if "workflow_submission_schema_versions" in names else ("E" if "workflow_draft_adapter_versions" in names else "F")\n    if observed != _canonical_layout(layout):\n        return layout\n    layout = _validate_installed(conn, expected_org_slug=expected_org_slug)'}], 'required_assertion': 'DID NOT RAISE'}, {'id': 'original-event-revision', 'isolated_ids': [3], 'red_kind': 'business', 'patches': [{'path': 'runtime/infrastructure/workflow_schema.py', 'function': '_event_revision', 'old': 'return candidates[0][2]', 'new': 'return 9'}], 'required_assertion': 'assert {9} == {4}', 'required_source': "assert {r[0] for r in observer.execute('SELECT revision FROM workflow_events')} == {4}"}, {'id': 'authenticated-event-kind', 'isolated_ids': [4], 'red_kind': 'business', 'patches': [{'path': 'runtime/infrastructure/workflow_schema.py', 'function': '_event_revision', 'old': "if subject is not None and 'event_kind' in subject and subject['event_kind'] != kind:", 'new': 'if False:'}]}, {'id': 'revision-event-uniqueness', 'isolated_ids': [5], 'red_kind': 'business', 'patches': [{'path': 'runtime/infrastructure/workflow_schema.py', 'function': None, 'old': 'UNIQUE(instance_id,revision,event_kind)', 'new': 'UNIQUE(instance_id,revision,event_kind,event_digest)'}]}, {'id': 'operator-refusal-status', 'isolated_ids': [6], 'red_kind': 'business', 'patches': [{'path': 'scripts/migrate_workflow_submission_schema.py', 'function': 'main', 'old': 'return 1\n    finally:', 'new': 'return 0\n    finally:'}]}, {'id': 'operator-layout-label', 'isolated_ids': [7], 'red_kind': 'business', 'patches': [{'path': 'scripts/migrate_workflow_submission_schema.py', 'function': 'main', 'old': 'layout G; compatible reader required', 'new': 'layout E; compatible reader required'}]}, {'id': 'offline-observation', 'isolated_ids': [8], 'red_kind': 'business', 'patches': [{'path': 'scripts/migrate_workflow_submission_schema.py', 'function': '_lifecycle_snapshot', 'old': 'home = daemon_home()', 'new': 'return ()\n    home = daemon_home()'}]}, {'id': 'atomic-committed-target', 'isolated_ids': [9, 12], 'red_kind': 'business', 'patches': [{'path': 'runtime/infrastructure/workflow_schema.py', 'function': 'migrate_submission_schema', 'old': "conn.commit()\n        return 'migrated'", 'new': "conn.rollback()\n        return 'migrated'"}]}, {'id': 'interrupted-uncommitted-source', 'isolated_ids': [10], 'red_kind': 'business', 'patches': [{'path': 'runtime/infrastructure/workflow_schema.py', 'function': 'migrate_submission_schema', 'old': "conn.execute('DROP TABLE workflow_events')", 'new': "conn.execute('DROP TABLE workflow_events')\n        conn.commit()"}]}, {'id': 'writer-reservation', 'isolated_ids': [11], 'red_kind': 'business', 'patches': [{'path': 'scripts/migrate_workflow_submission_schema.py', 'function': 'execute', 'old': "if sql == 'BEGIN IMMEDIATE':", 'new': "if sql == 'BEGIN IMMEDIATE':\n            self.rollback()"}]}, {'id': 'permanent-null-history', 'isolated_ids': [13], 'red_kind': 'business', 'patches': [{'path': 'runtime/infrastructure/workflow_schema.py', 'function': '_validate_submission_data', 'old': "    for link in _records(conn, 'SELECT * FROM workflow_submission_result_links'):", 'new': '    conn.execute("UPDATE workflow_submissions SET source_result_id=\'inferred\' WHERE source_result_id IS NULL")\n    for link in _records(conn, \'SELECT * FROM workflow_submission_result_links\'):'}]}, {'id': 'shared-genuine-result', 'isolated_ids': [14], 'red_kind': 'business', 'patches': [{'path': 'runtime/infrastructure/workflow_schema.py', 'function': '_validate_submission_data', 'old': "    for link in _records(conn, 'SELECT * FROM workflow_submission_result_links'):", 'new': "    if conn.execute('SELECT 1 FROM workflow_submission_result_links GROUP BY task_result_id HAVING COUNT(*)>1').fetchone():\n        raise ValueError('unapproved_global_result_uniqueness')\n    for link in _records(conn, 'SELECT * FROM workflow_submission_result_links'):"}]}, {'id': 'operation-result-closure', 'isolated_ids': [15], 'red_kind': 'business', 'patches': [{'path': 'runtime/infrastructure/workflow_schema.py', 'function': '_validate_submission_data', 'old': "    code = 'workflow_submission_data_corrupt'", 'new': "    return\n    code = 'workflow_submission_data_corrupt'"}]}, {'id': 'existing-load-no-upgrade', 'isolated_ids': [17, 33], 'red_kind': 'business', 'patches': [{'path': 'runtime/daemon/org_state.py', 'function': 'load', 'old': '            if layout == "F":', 'new': '            if layout in (\'F\', \'E\') and db._conn.execute(\'SELECT 1 FROM org_settings LIMIT 1\').fetchone():\n                from runtime.infrastructure.workflow_schema import migrate_submission_schema\n                migrate_submission_schema(db._conn, expected_org_slug=slug)\n                layout = \'G\'\n            if layout == "F":'}]}, {'id': 'creation-rollback-ownership', 'isolated_ids': [18], 'red_kind': 'business', 'patches': [{'path': 'runtime/daemon/routes/orgs.py', 'function': 'init_org', 'old': '        # add_org failed AFTER we seeded the skeleton.', 'new': "        shutil.rmtree(org_root.parent / 'beta')\n        # add_org failed AFTER we seeded the skeleton."}]}, {'id': 'selected-full-release', 'isolated_ids': [19], 'red_kind': 'business', 'patches': [{'path': 'runtime/orchestrator/authority.py', 'function': '_server_evidence', 'old': 'schema_drift = reference == "unavailable" or live_schema_digest == "unavailable" or live_schema_digest != reference', 'new': 'schema_drift = True'}]}, {'id': 'whole-database-drift', 'isolated_ids': [20], 'red_kind': 'business', 'patches': [{'path': 'runtime/orchestrator/authority.py', 'function': '_server_evidence', 'old': 'schema_drift = reference == "unavailable" or live_schema_digest == "unavailable" or live_schema_digest != reference', 'new': 'schema_drift = False'}]}, {'id': 'v2-observed-only', 'isolated_ids': [21, 22], 'red_kind': 'business', 'patches': [{'path': 'runtime/orchestrator/authority.py', 'function': 'capture_authority_policy_v2_schema_observation', 'old': '            inventory = _v2_capture_inventory(conn)', 'new': '            inventory = _v2_capture_inventory(conn)\n            if conn.execute("SELECT 1 FROM sqlite_schema WHERE name=\'workflow_draft_adapter_versions\'").fetchone():\n                _release_schema_digest(\'E\')'}]}, {'id': 'pending-work-not-metadata', 'isolated_ids': [23], 'red_kind': 'business', 'patches': [{'path': 'runtime/workflows/cutover.py', 'function': '_drain_blockers', 'old': '        if _validate_installed(conn, expected_org_slug=self._org_slug, validate_data=False) == "G":', 'new': '        if False:'}]}, {'id': 'g-compatible-reader', 'isolated_ids': [24], 'red_kind': 'business', 'patches': [{'path': 'runtime/workflows/cutover.py', 'function': 'downgrade_preflight', 'old': '                    blockers.append(self._blocker("submission_schema_requires_compatible_reader" if layout == "G" else "draft_schema_requires_compatible_reader", owner="operator",', 'new': '                    if layout != "G": blockers.append(self._blocker("draft_schema_requires_compatible_reader", owner="operator",'}]}, {'id': 'old-draft-g-label', 'isolated_ids': [25], 'red_kind': 'business', 'patches': [{'path': 'scripts/migrate_workflow_draft_schema.py', 'function': 'main', 'old': 'layout {layout}; compatible reader required', 'new': 'layout E; compatible reader required'}]}, {'id': 'literal-quiescence', 'isolated_ids': [26], 'red_kind': 'business', 'patches': [{'path': 'runtime/workflows/draft_dispatch.py', 'function': 'terminal', 'old': 'and receipt.backend == running.backend and receipt.quiescent is True', 'new': 'and receipt.backend == running.backend and False'}]}, {'id': 'missing-quiescence', 'isolated_ids': [27], 'red_kind': 'business', 'patches': [{'path': 'runtime/workflows/draft_dispatch.py', 'function': 'terminal', 'old': 'and receipt.backend == running.backend and receipt.quiescent is True', 'new': 'and receipt.backend == running.backend'}]}, {'id': 'full-callback-replay', 'isolated_ids': [28], 'red_kind': 'business', 'patches': [{'path': 'runtime/workflows/draft_dispatch.py', 'function': None, 'old': 'and completion_result_payload_matches(existing[0], **payload)', 'new': 'and True'}, {'path': 'runtime/workflows/draft_dispatch.py', 'function': None, 'old': 'and completion_result_payload_matches(rows[0], **payload)', 'new': 'and True'}]}, {'id': 'cancel-writer-order', 'isolated_ids': [29], 'red_kind': 'business', 'patches': [{'path': 'runtime/workflows/draft_dispatch.py', 'function': 'callback_uncommitted', 'old': 'or intent["cancellation_requested"] or not intent["is_current"]', 'new': 'or not intent["is_current"]'}, {'path': 'runtime/infrastructure/workflow_schema.py', 'function': '_validate_draft_data', 'old': "or intent['id'] in accepted_results or after['cancellation_requested']", 'new': "or intent['id'] in accepted_results"}]}, {'id': 'lost-notification-discovery', 'isolated_ids': [30], 'red_kind': 'business', 'patches': [{'path': 'runtime/workflows/recovery.py', 'function': 'recover_owned_task', 'old': '        queue.enqueue_if_absent(slug, task_id)', 'new': '        return True  # source-copy regression: lose initial queued-draft discovery'}], 'required_assertion': 'durable lost notification was not rediscovered exactly once', 'required_source': 'assert state.queue._queue.qsize() == 1', 'required_observed': 'assert 0 == 1'}, {'id': 'historical-semantic-closure', 'isolated_ids': [31], 'red_kind': 'business', 'patches': [{'path': 'runtime/workflows/activation.py', 'function': '_closure', 'old': '            if (canonical_bytes(expected_grant) != authorization["authority_bytes"]\n                    or canonical_bytes(bound) != binding["binding_bytes"]\n                    or canonical_bytes(expected_context) != context["context_bytes"]\n                    or len(context["context_bytes"]) > CONTEXT_LIMIT\n                    or canonical_bytes(task_scope) != intent["task_scope_bytes"]\n                    or binding["id"] != _id("workflow-binding", instance["id"], digest(binding["binding_bytes"]))\n                    or context["id"] != _id("workflow-context", instance["id"], digest(context["context_bytes"]))\n                    or authorization["id"] != _id("workflow-authorization", instance["id"], 1, digest(authorization["authority_bytes"]))):', 'new': '            if False:'}, {'path': 'runtime/infrastructure/workflow_schema.py', 'function': '_validate_draft_data', 'old': '    def refuse() -> None:', 'new': '    return\n    def refuse() -> None:'}]}, {'id': 'owned-consumer-fence', 'isolated_ids': [32], 'red_kind': 'business', 'patches': [{'path': 'runtime/workflows/recovery.py', 'function': 'classify_task', 'old': '            validate_workflow_schema(conn, expected_org_slug=org_slug)', 'new': '            # Omitted semantic validation control'}, {'path': 'runtime/workflows/recovery.py', 'function': 'classify_task', 'old': '                dispatcher.org.workflow_activations._closure(\n                    conn, intent["activation_id"], actor=intent["admission_principal"],\n                )', 'new': '                # Omitted authenticated canonical closure control'}, {'path': 'runtime/infrastructure/workflow_schema.py', 'function': '_validate_draft_data', 'old': '    def refuse() -> None:', 'new': '    return\n    def refuse() -> None:'}]}, {'id': 'g-old-reader-refusal', 'isolated_ids': [34], 'red_kind': 'business', 'patches': [{'path': 'runtime/infrastructure/workflow_schema.py', 'function': 'migrate_submission_schema', 'old': '    _validate_submission_source_ownership(conn, layout)\n    for event in', 'new': "    return 'migrated'\n    _validate_submission_source_ownership(conn, layout)\n    for event in"}]}, {'id': 'archive-destination-ownership', 'isolated_ids': [35], 'red_kind': 'business', 'patches': [{'path': 'tests/workflows/test_draft_schema.py', 'function': '_extract_s2_source', 'old': '        if source.exists():', 'new': '        if False:'}, {'path': 'tests/workflows/test_draft_schema.py', 'function': '_extract_s2_source', 'old': '        source.mkdir()', 'new': '        source.mkdir(exist_ok=True)'}]}, {'id': 'historical-durable-rows', 'isolated_ids': [36], 'red_kind': 'business', 'patches': [{'path': 'runtime/infrastructure/workflow_schema.py', 'function': 'migrate_submission_schema', 'old': "        conn.execute('DROP TABLE temp.event_stage')", 'new': "        conn.execute('DROP TABLE temp.event_stage')\n        conn.execute('DELETE FROM tasks')"}]}, {'id': 'closed-ci-selection', 'isolated_ids': [101], 'red_kind': 'business', 'patches': [{'path': 'scripts/nightly_local_ci_all.py', 'function': None, 'old': "'tests/workflows/test_draft_dispatch.py::test_delayed_real_containment_keeps_cancel_and_drain_pending_until_terminal_evidence',", 'new': "'tests/workflows/test_draft_dispatch.py::test_omitted_delayed_real_containment_keeps_cancel_and_drain_pending_until_terminal_evidence',"}]}, {'id': 'typed-ci-flag', 'isolated_ids': [101], 'red_kind': 'business', 'patches': [{'path': '.github/workflows/nightly-integration.yml', 'function': None, 'old': '        type: boolean', 'new': '        type: string'}]}, {'id': 'safe-ci-default', 'isolated_ids': [101], 'red_kind': 'business', 'patches': [{'path': '.github/workflows/nightly-integration.yml', 'function': None, 'old': '        default: false', 'new': '        default: true'}]}, {'id': 'manual-integration-predicate', 'isolated_ids': [101], 'red_kind': 'business', 'patches': [{'path': '.github/workflows/nightly-integration.yml', 'function': None, 'old': "\n    if: ${{ github.event_name == 'schedule' }}\n", 'new': "\n    if: ${{ github.event_name != 'schedule' }}\n"}]}, {'id': 'g-reopen-durable-identity', 'isolated_ids': [34], 'red_kind': 'business', 'patches': [{'path': 'runtime/daemon/org_state.py', 'function': 'load', 'old': '            return state', 'new': '            if layout == "G":\n                db._conn.execute("UPDATE tasks SET brief=brief || \' altered-by-reopen\'")\n                db._conn.execute(f\'PRAGMA user_version={db._conn.execute("PRAGMA user_version").fetchone()[0] + 1}\')\n                db._conn.commit()\n            return state'}], 'required_assertion': 'assert _g_closed_database_identity(root) == g_durable_before'}, {'id': 'g-validator-physical-no-write', 'isolated_ids': [34], 'red_kind': 'business', 'patches': [{'path': 'runtime/infrastructure/workflow_schema.py', 'function': 'validate_workflow_schema', 'old': '    _validate_source_data(conn)', 'new': '    conn.execute(f\'PRAGMA user_version={conn.execute("PRAGMA user_version").fetchone()[0] + 1}\')\n    _validate_source_data(conn)'}], 'required_assertion': 'assert snapshot() == before'}, {'id': 'g-refusal-physical-no-write', 'isolated_ids': [34], 'red_kind': 'business', 'patches': [{'path': 'runtime/infrastructure/workflow_schema.py', 'function': '_validate_installed', 'old': '    if _object_keys(actual) != _object_keys(expected):', 'new': '    if _object_keys(actual) != _object_keys(expected):\n        conn.execute(f\'PRAGMA user_version={conn.execute("PRAGMA user_version").fetchone()[0] + 1}\')'}], 'required_assertion': 'assert snapshot() == before'}, {'id': 'preservation-control-plan', 'isolated_ids': [101], 'red_kind': 'business', 'patches': [{'path': 'scripts/nightly_local_ci_all.py', 'function': None, 'old': "'id': 'g-validator-physical-no-write'", 'new': "'id': 'missing-g-validator-physical-no-write'"}], 'required_assertion': 'assert set(preservation) =='})
    assert {i for control in SOURCE_CONTROLS for i in control['isolated_ids']} == set(range(1, 102))
    assert len(SOURCE_CONTROLS) == 40
    (owned / 'source-controls-plan.json').write_text(json.dumps(SOURCE_CONTROLS, indent=2) + '\n')

    def source_control(control):
        identity = control['id']
        receipt_path = owned / ('control-' + identity + '.json')
        receipt = {'head': head, 'tree': tree, 'control': control, 'state': 'not_started',
                   'restoration': None, 'red': None, 'green': None}
        def persist_control():
            receipt_path.write_text(json.dumps(receipt, indent=2) + '\n')
        persist_control()
        directory = None
        try:
            with tempfile.TemporaryDirectory(prefix='control-' + identity + '-', dir=owned / 'scratch') as directory:
                checkout = pathlib.Path(directory) / 'source'
                checkout.mkdir()
                archive_bytes = subprocess.check_output(['git', 'archive', '--format=tar', head], cwd=source)
                with tarfile.open(fileobj=io.BytesIO(archive_bytes)) as archive:
                    archive.extractall(checkout, filter='data')
                (checkout / '.venv').symlink_to(source / '.venv', target_is_directory=True)
                originals = {patch['path']: {'bytes': (checkout / patch['path']).read_bytes(),
                                             'mode': (checkout / patch['path']).stat().st_mode & 0o777}
                             for patch in control['patches']}
                receipt['originals'] = {name: {'sha256': hashlib.sha256(v['bytes']).hexdigest(), 'mode': v['mode']}
                                         for name, v in originals.items()}
                receipt['copy'] = str(checkout)
                receipt['state'] = 'running_red'
                persist_control()
                selectors = [FIXED_ISOLATED[i - 1] for i in control['isolated_ids']]
                red_error = None
                try:
                    for patch in control['patches']:
                        path = checkout / patch['path']
                        content = path.read_text()
                        if patch['function']:
                            parsed = ast.parse(content)
                            nodes = [n for n in ast.walk(parsed) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                                     and n.name == patch['function']]
                            assert len(nodes) == 1
                            node = nodes[0]
                            lines = content.splitlines(keepends=True)
                            before = ''.join(lines[:node.lineno - 1])
                            body = ''.join(lines[node.lineno - 1:node.end_lineno])
                            after = ''.join(lines[node.end_lineno:])
                        else:
                            before, body, after = '', content, ''
                        assert patch['old'] != patch['new'] and patch['old'] in body, identity
                        body = body.replace(patch['old'], patch['new'], 1)
                        changed = before + body + after
                        if path.suffix == '.py':
                            ast.parse(changed)
                        path.write_text(changed)
                    receipt['mutated'] = {name: {'sha256': hashlib.sha256((checkout / name).read_bytes()).hexdigest(),
                                                 'mode': (checkout / name).stat().st_mode & 0o777} for name in originals}
                    persist_control()
                    if control['red_kind'] == 'mixed-origin':
                        # These setup errors are explicitly origin-regression RED;
                        # only the independent fresh/POST assertions are business RED.
                        receipt['red'] = str(fixed_command(selectors[:2], 'control-red', identity + '-business', 0,
                            execution_source=checkout, red_kind='business'))
                        receipt['origin_red'] = str(fixed_command(selectors[2:], 'control-red', identity + '-origin', 0,
                            execution_source=checkout, red_kind='origin'))
                    else:
                        receipt['red'] = str(fixed_command(selectors, 'control-red', identity, 0,
                            execution_source=checkout, red_kind=control['red_kind'],
                            required_assertion=control.get('required_assertion'), required_source=control.get('required_source'),
                            required_observed=control.get('required_observed')))
                except BaseException as exc:
                    red_error = exc
                    receipt['red_error'] = compact_error(exc)
                finally:
                    for name, original in originals.items():
                        path = checkout / name
                        path.write_bytes(original['bytes'])
                        path.chmod(original['mode'])
                    receipt['restoration'] = {name: {'sha256': hashlib.sha256((checkout / name).read_bytes()).hexdigest(),
                                                     'mode': (checkout / name).stat().st_mode & 0o777} for name in originals}
                    assert receipt['restoration'] == receipt['originals'], identity
                    for cache in checkout.rglob('__pycache__'):
                        shutil.rmtree(cache)
                    receipt['state'] = 'restored'
                    persist_control()
                if control['red_kind'] == 'mixed-origin':
                    receipt['green'] = str(fixed_command(selectors[:2], 'control-green', identity + '-business', 0,
                        execution_source=checkout))
                    receipt['origin_green'] = str(fixed_command(selectors[2:], 'control-green', identity + '-origin', 0,
                        execution_source=checkout))
                else:
                    receipt['green'] = str(fixed_command(selectors, 'control-green', identity, 0, execution_source=checkout))
                if red_error is not None:
                    receipt['state'] = 'red_attribution_failed_after_restored_green'
                    persist_control()
                    raise RuntimeError('red_attribution_failed_after_restored_green: ' + identity + '; see ' + str(receipt_path))
                receipt['state'] = 'passed_red_restored_green'
                persist_control()
        except BaseException as exc:
            receipt.update(state='failed_or_interrupted', error=compact_error(exc))
            raise
        finally:
            receipt['cleanup_exit'] = 0 if directory is not None and not pathlib.Path(directory).exists() else None
            persist_control()

    def source_controls():
        try:
            run_batch(SOURCE_CONTROLS, source_control)
        except Exception as exc:
            (owned / 'source-controls-failed.json').write_text(json.dumps({'head': head,
                'error': compact_error(exc), 'batch_errors': str(owned / 'batch-errors.json')}, indent=2) + '\n')
            raise
        (owned / 'source-controls-complete.json').write_text(json.dumps({'head': head,
            'controls': len(SOURCE_CONTROLS), 'affected_functions': 101}, indent=2) + '\n')

    collected = fixed_command(list(FIXED_ISOLATED), 'collection', 'all101', 0, collect=True)
    log_manifest = json.loads((collected / 'full-log.json').read_text())
    assert log_manifest['complete']
    chunks = []
    for segment in log_manifest['streams']['stdout']['segments']:
        with gzip.open(segment['path'], 'rb') as stream:
            chunks.append(stream.read())
    text = b''.join(chunks).decode('utf-8')
    import re
    observed, ancestry = [], []
    for line in text.splitlines():
        match = re.fullmatch(r'( *)(?:<(Dir|Package|Module|Function) (.+)>)', line)
        if match is None: continue
        depth, kind, name = len(match[1]), match[2], match[3]
        while ancestry and ancestry[-1][0] >= depth: ancestry.pop()
        if kind == 'Function':
            parts = [entry[2] for entry in ancestry]
            if 'tests' not in parts: raise RuntimeError('unowned collected function')
            parts = parts[parts.index('tests'):]
            observed.append('/'.join(parts) + '::' + name)
        else: ancestry.append((depth, kind, name))
    assert observed and len(observed) == len(set(observed))
    assert {node.split('[')[0] for node in observed} == set(FIXED_ISOLATED)
    # Parameters are independently visible in the full collection transcript.
    (owned / 'collection-nodes.json').write_text(json.dumps(observed, indent=2) + '\n')
    invocation_plan = [{'phase': phase, 'identity': prefix + str(ordinal).zfill(width),
                        'round': r, 'selector': selector, 'state': 'not_started'}
                       for r in (1, 2, 3, 4, 5)
                       for phase, prefix, width, selection in (
                           ('isolated', 'I', 3, FIXED_ISOLATED), ('sibling', 'S', 2, FIXED_SIBLINGS))
                       for ordinal, selector in enumerate(selection, 1)]
    assert len(invocation_plan) == 580
    (owned / 'invocation-plan.json').write_text(json.dumps(invocation_plan, indent=2) + '\n')
    source_controls()
    def run_phase(phase, selection, prefix, width, round_number):
        def invoke(item):
            ordinal, selector = item
            fixed_command([selector], phase, prefix + str(ordinal).zfill(width), round_number)
        run_batch(tuple(enumerate(selection, 1)), invoke)

    for round_number in (1, 2, 3, 4, 5):
        run_phase('isolated', FIXED_ISOLATED, 'I', 3, round_number)
        run_phase('sibling', FIXED_SIBLINGS, 'S', 2, round_number)
    (owned / 'repetitions-complete.json').write_text(json.dumps({'head': head, 'processes': 580,
        'rounds': 5, 'isolated': 505, 'siblings': 75}, indent=2) + '\n')
raise SystemExit(result.returncode)
