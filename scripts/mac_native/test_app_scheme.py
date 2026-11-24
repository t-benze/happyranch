"""Standalone MAC-1 evidence-verdict checks, outside retired Python units."""
from __future__ import annotations

import hashlib
import http.client
import json
import subprocess
import sys
import tarfile
import tempfile
import time
import unittest
from pathlib import Path
from contextlib import ExitStack
from unittest.mock import patch

from scripts.mac_native.suite_app_scheme import ROOT, UPLOAD_DIGEST, assess, qualify_steps, read_events, run, synthetic_daemon
from scripts.mac_native.app_scheme_test_evidence import assess_native, source_inventory, terminal_results
from scripts.mac_native.suite_supervisor import _build_environment, _prepare_keychain


def native_evidence() -> tuple[bytes, list[dict], dict]:
    inventory = {'xctest': ['HappyRanchAppTests.HomeCandidateOwnershipTests/testKeepsActive'],
                 'swift_testing': ['HappyRanchAppTests/HomeInviteModelTests.swift/retainsSave']}
    raw = ("Test Case '-[HappyRanchAppTests.HomeCandidateOwnershipTests testKeepsActive]' started.\n"
           "Test Case '-[HappyRanchAppTests.HomeCandidateOwnershipTests testKeepsActive]' passed (0.1 seconds).\n").encode()
    key = 'HappyRanchAppTests.HomeInviteModelTests/retainsSave(_:)/HappyRanchAppTests/HomeInviteModelTests.swift:12:3'
    records = [dict(version=0, kind='test', payload=dict(kind='function', id=key, name='retainsSave(_:)',
               sourceLocation=dict(fileID='HappyRanchAppTests/HomeInviteModelTests.swift'), isParameterized=True))]
    for kind in ('runStarted', 'testStarted', 'testCaseStarted', 'testCaseEnded',
                 'testCaseStarted', 'testCaseEnded', 'testEnded', 'runEnded'):
        records.append(dict(version=0, kind='event', payload=dict(kind=kind, **(
            {} if kind in ('runStarted', 'runEnded') else {'testID': key}))))
    return raw, records, inventory


def native_stream(records: list[dict]) -> bytes:
    return ('\n'.join(json.dumps(row) for row in records) + '\n').encode()


class NativeWorkloadEvidenceTests(unittest.TestCase):
    def test_full_package_workload_enables_both_libraries_and_records_each_case(self) -> None:
        raw, records, inventory = native_evidence()
        with tempfile.TemporaryDirectory(dir=ROOT) as folder:
            report, calls = {}, []
            def transfer(url: str, archive: Path, *args: object) -> dict:
                with tarfile.open(archive, 'w:gz'):
                    pass
                return {'curl_exit_code': 0}
            def command(argv: list[str], *args: object, **kwargs: object) -> tuple[int, bytes]:
                calls.append((argv, kwargs))
                if 'test' in argv:
                    if '--event-stream-output-path' in argv:
                        Path(argv[argv.index('--event-stream-output-path') + 1]).write_bytes(native_stream(records))
                    return 0, raw
                return (1, b'') if '--show-bin-path' in argv else (0, b'')
            with patch.dict('os.environ', {'TMPDIR': folder}), patch(
                'scripts.mac_native.suite_app_scheme._command', side_effect=command
            ), patch('scripts.mac_native.suite_app_scheme.curl_attempt', side_effect=transfer), patch(
                'scripts.mac_native.suite_app_scheme.verify_checksum'
            ), patch('scripts.mac_native.suite_app_scheme.source_inventory', return_value=inventory, create=True), patch(
                'scripts.mac_native.suite_app_scheme._prepare_keychain', create=True
            ):
                with self.assertRaisesRegex(RuntimeError, 'bin_path failed'):
                    run(report)
            test_calls = [(argv, kwargs) for argv, kwargs in calls if 'test' in argv]
            self.assertEqual(len(test_calls), 1, 'one full-package build/test invocation')
            argv, kwargs = test_calls[0]
            self.assertNotIn('--filter', argv)
            self.assertNotIn('--skip', argv)
            for option in ('--enable-testable-imports', '--enable-xctest', '--enable-swift-testing', '--no-parallel'):
                self.assertIn(option, argv)
            self.assertEqual(argv[argv.index('-c') + 1], 'release')
            self.assertEqual(argv[argv.index('--event-stream-version') + 1], '0')
            self.assertEqual(kwargs['output_cap'], 16 * 1024 * 1024)
            result = report['app_scheme']['native_tests']
            self.assertEqual(result['status'], 'PASS')
            self.assertEqual([row['status'] for row in result['cases']], ['PASS'] * 3)
            self.assertEqual([row['case'] for row in result['cases'][1:]], [1, 2])
            row = next(row for row in report['app_scheme']['commands'] if row['name'] == 'product_build_and_all_tests')
            self.assertEqual(row['timeout_seconds'], 1200)

    def test_disposable_fixture_cleans_exact_scratch_keychain_and_checks_preferences(self) -> None:
        for fails in (False, True):
            with self.subTest(create_fails=fails), tempfile.TemporaryDirectory() as folder:
                scratch = Path(folder); env = _build_environment(scratch)
                keychain = scratch / 'home/Library/Keychains/login.keychain-db'
                checks, operations = [], []
                def command(argv: list[str], *args: object, **kwargs: object) -> tuple[int, bytes]:
                    if argv[1] == '-i':
                        operation = kwargs['input_data'].split(b' ', 1)[0].decode()
                        operations.append(operation)
                        if operation == 'create-keychain':
                            keychain.write_bytes(b'synthetic')
                            return (1 if fails else 0), b''
                    elif argv[1] == 'delete-keychain':
                        self.assertEqual(argv[2], str(keychain))
                        operations.append('delete-keychain'); keychain.unlink()
                    else:
                        self.assertEqual(argv[2:], ['-d', 'user'], 'preference commands are read-only')
                        operations.append(argv[1])
                    return 0, b''
                def row(name: str, status: str, expected: object, actual: object) -> None:
                    checks.append(dict(name=name, status=status, expected=expected, actual=actual))
                with patch('scripts.mac_native.suite_supervisor._command', side_effect=command):
                    if fails:
                        with self.assertRaises(RuntimeError), ExitStack() as cleanup:
                            _prepare_keychain(cleanup, env, scratch, time.monotonic() + 30, row, checks)
                    else:
                        with ExitStack() as cleanup:
                            _prepare_keychain(cleanup, env, scratch, time.monotonic() + 30, row, checks)
                            self.assertTrue(keychain.is_file())
                self.assertFalse(keychain.exists())
                self.assertEqual(operations.count('delete-keychain'), 1)
                self.assertEqual(operations.count('list-keychains'), 2)
                self.assertEqual(operations.count('default-keychain'), 2)
                self.assertNotIn('input_data', json.dumps(checks))

    def test_source_census_includes_xctest_and_multiline_swift_testing(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            tests = Path(folder) / 'HappyRanchAppTests'; tests.mkdir()
            (tests / 'Ownership.swift').write_text('''final class Ownership: XCTestCase {
                func testCandidate() async throws {}
            }
            @Suite struct Invite {
                @Test("save", arguments: [false, true])
                func save(_ failed: Bool) async {}
            }''')
            self.assertEqual(source_inventory(Path(folder)), {
                'xctest': ['HappyRanchAppTests.Ownership/testCandidate'],
                'swift_testing': ['HappyRanchAppTests/Ownership.swift/save']})

    def test_missing_failed_skipped_or_incomplete_case_never_passes(self) -> None:
        for failure in ('missing_xctest', 'failed_xctest', 'skipped_xctest', 'duplicate_xctest',
                        'missing_swift_function', 'missing_swift_case', 'missing_swift_end', 'swift_issue',
                        'swift_skip', 'partial_stream', 'bad_schema', 'missing_run_end', 'nonzero_exit'):
            with self.subTest(failure=failure):
                raw, records, inventory = native_evidence()
                if failure == 'missing_xctest':
                    raw = b''
                elif failure in ('failed_xctest', 'skipped_xctest'):
                    raw = raw.replace(b'passed', b'failed' if failure == 'failed_xctest' else b'skipped')
                elif failure == 'duplicate_xctest':
                    raw += raw
                elif failure == 'missing_swift_function':
                    inventory['swift_testing'].append('HappyRanchAppTests/HomeInviteModelTests.swift/cancel')
                elif failure == 'missing_swift_case':
                    records = [row for row in records if row['payload']['kind'] not in ('testCaseStarted', 'testCaseEnded')]
                elif failure == 'missing_swift_end':
                    records.pop(6)
                elif failure in ('swift_issue', 'swift_skip'):
                    records.insert(4, dict(version=0, kind='event', payload=dict(
                        kind='issueRecorded' if failure == 'swift_issue' else 'testSkipped', testID=records[0]['payload']['id'])))
                elif failure == 'bad_schema':
                    records[0]['version'] = 99
                elif failure == 'missing_run_end':
                    records.pop()
                stream = native_stream(records)
                if failure == 'partial_stream':
                    stream += b'{'
                result = assess_native(raw, stream, inventory, 1 if failure == 'nonzero_exit' else 0)
                self.assertEqual(result['status'], 'FAIL')

    def test_results_beyond_old_16k_bound_are_retained_without_truncation(self) -> None:
        raw, records, inventory = native_evidence()
        for index in range(400):
            key = f'HappyRanchSupervisorTests.ParserTests/testAdditionalCase{index}'
            inventory['xctest'].append(key)
            raw += (f"Test Case '-[HappyRanchSupervisorTests.ParserTests testAdditionalCase{index}]' started.\n"
                    f"Test Case '-[HappyRanchSupervisorTests.ParserTests testAdditionalCase{index}]' passed (0.1 seconds).\n").encode()
        retained = terminal_results(raw.decode())
        self.assertGreater(retained['json_bytes'], 16384)
        self.assertFalse(retained['truncated'])
        self.assertEqual(len(retained['lines']), 401)
        result = assess_native(raw, native_stream(records), inventory, 0)
        self.assertEqual(result['status'], 'PASS')
        self.assertEqual(len(result['cases']), 403)


def evidence() -> tuple[list[dict], list[dict]]:
    events = [dict(event='root', path='/onboarding', root=1),
              dict(event='deep_reload', path='/onboarding', origin='happyranch-app://app', root=1)]
    for method in ('POST', 'PUT'):
        events.append(dict(event='json', method=method, bytes=20, intact=True,
                           sha256=hashlib.sha256(b'{"synthetic":"mac1"}').hexdigest()))
    events.append(dict(event='upload', fileBytes=10485760, intact=True, sha256=UPLOAD_DIGEST))
    sends = []
    for mode, count, base in (('stream', 5, 1000), ('abort', 2, 10000)):
        events.append(dict(event='native_stream_open', requestID='stream-owner' if mode == 'stream' else 'abort-owner'))
        for index in range(count):
            sent = base + index * 1000
            sends.append(dict(event='send', mode=mode, index=index, sentWallMs=sent))
            events.append(dict(event='receipt', mode=mode, index=index, sentWallMs=sent, receiptWallMs=sent + 25))
    events.extend([dict(event='abort', pageWallMs=11030),
                   dict(event='native_stop', nativeWallMs=11040, requestID='abort-owner'), dict(event='download_finish')])
    for step in ('stream', 'abort', 'download'):
        events.extend([dict(event='done', processStep=step), dict(event='process_exit', step=step, exit_code=0)])
    events.append(dict(event='stream_end', mode='stream', count=5))
    events.extend(dict(event='step', step=step, status='PASS') for step in ('stream', 'abort', 'download'))
    return events, sends


class QualificationVerdictTests(unittest.TestCase):
    def test_native_process_failure_cannot_suppress_abort_or_download(self) -> None:
        for failure in ('watchdog', 'timeout', 'output_cap', 'malformed_events', 'partial_events'):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as folder:
                scratch = Path(folder)
                calls = []
                def command(argv: list[str], *args: object, **kwargs: object) -> tuple[int, bytes]:
                    calls.append(argv)
                    path = Path(argv[-3])
                    path.write_bytes(b'{"event":"startup"}\n')
                    if argv[-1] == 'stream':
                        if failure == 'timeout':
                            raise TimeoutError('synthetic-private-value')
                        if failure == 'output_cap':
                            raise RuntimeError('synthetic-private-value')
                        if failure == 'malformed_events':
                            path.write_bytes(b'invalid\n')
                        elif failure == 'partial_events':
                            path.write_bytes(b'{"event":"startup"}\n{"event":')
                        return 124, b''
                    path.write_text('{"event":"done"}\n')
                    return 0, b''
                section = {'events': []}
                with patch('scripts.mac_native.suite_app_scheme._command', side_effect=command):
                    code = qualify_steps(['product', '--qualify-app-scheme', 'assets', 'policy', '1234'], {},
                                         scratch, time.monotonic() + 150, section, scratch / 'downloads')
                self.assertEqual(code, 1)
                self.assertEqual([argv[-1] for argv in calls], ['stream', 'abort', 'download'])
                self.assertEqual(len({argv[-3] for argv in calls}), 3, 'separate JSONL files')
                self.assertTrue(all(argv[0] == 'product' for argv in calls), 'fresh product invocation per step')
                self.assertEqual([row['step'] for row in section['native_processes']], ['stream', 'abort', 'download'])
                self.assertEqual([row['processStep'] for row in section['events'] if row['event'] == 'done'], ['abort', 'download'])
                self.assertNotIn('synthetic-private-value', json.dumps(section))
        # Exercise the real subprocess boundary too: a terminated child has no
        # actor, realm or message handler that can starve either later child.
        with tempfile.TemporaryDirectory() as folder:
            scratch = Path(folder)
            script = ('import json,os,sys; from pathlib import Path; '
                      'step=sys.argv[-1]; '
                      'Path(sys.argv[-3]).write_text(json.dumps({"event":"startup","pid":os.getpid()})+"\\n"'
                      '+(json.dumps({"event":"done"})+"\\n" if step!="stream" else "")); '
                      'sys.exit(124 if step=="stream" else 0)')
            section = {'events': []}
            code = qualify_steps([sys.executable, '-c', script], {}, scratch, time.monotonic() + 10,
                                 section, scratch / 'downloads')
            self.assertEqual(code, 1)
            self.assertEqual([row['exit_code'] for row in section['native_processes']], [124, 0, 0])
            starts = [row for row in section['events'] if row['event'] == 'startup']
            self.assertEqual([row['processStep'] for row in starts], ['stream', 'abort', 'download'])
            self.assertEqual(len({row['pid'] for row in starts}), 3)

    def test_each_process_exit_and_done_are_required(self) -> None:
        for step in ('stream', 'abort', 'download'):
            for failure in ('missing_exit', 'duplicate_exit', 'nonzero_exit', 'missing_done', 'termination', 'navigation'):
                with self.subTest(step=step, failure=failure):
                    events, sends = evidence()
                    receipt = next(row for row in events if row.get('event') == 'process_exit' and row.get('step') == step)
                    if failure == 'missing_exit':
                        events.remove(receipt)
                    elif failure == 'duplicate_exit':
                        events.append(receipt.copy())
                    elif failure == 'nonzero_exit':
                        receipt['exit_code'] = 124
                    elif failure == 'missing_done':
                        events = [row for row in events if not (row.get('event') == 'done' and row.get('processStep') == step)]
                    else:
                        events.append(dict(event='content_process_terminated' if failure == 'termination' else 'navigation_failure'))
                    self.check_status(events, sends, 'FAIL')

    def test_command_retains_xctest_outcomes_and_closed_loader_diagnostics(self) -> None:
        outcomes = [f"Test Case '-[HappyRanchAppTests.AppSchemeLoadTests testCase{index}]' passed (0.001 seconds)"
                    for index in range(12)]
        page_facts = dict(bootstrap='complete', initialOrigin='app', initialTop='true', origin='app', protocol='app',
                          host='app', top='true', secure='false', readyState='complete', webkit='object',
                          handlers='object', appTransport='object', postMessage='function', crypto='object', random='function')
        diagnostic = dict(label='ready', checkpoint='initial', contextRead='valid', bind='true', readiness='ready',
                          contextResult='string', bindResult='bool', diagnosticsResult='dictionary',
                          pageFacts=', '.join(f'{key}={value}' for key, value in page_facts.items()), healthInvocations=1,
                          loaded=1, failed=0, callbacks=3)
        forged = {**diagnostic, 'label': 'synthetic-private-value', 'contextRead': 'synthetic-private-value',
                  'pageFacts': 'origin=synthetic-private-value, secret=synthetic-private-value',
                  'context': 'synthetic-private-value', 'callbacks': 'synthetic-private-value'}
        middle = '\n'.join(outcomes + ['MAC1_LOADER ' + json.dumps(row) for row in (diagnostic, forged)])
        raw = (b'warning\n' * 2000 + middle.encode() + b'\n' + b'warning\n' * 2000)
        for code in (0, 1):
            with self.subTest(code=code), tempfile.TemporaryDirectory(dir=ROOT) as folder:
                report = {}
                with patch.dict('os.environ', {'TMPDIR': folder}), patch(
                    'scripts.mac_native.suite_app_scheme._command', side_effect=[(code, raw), (1, b'')],
                ):
                    with self.assertRaisesRegex(RuntimeError, ('macos' if code == 0 else 'swift_version') + ' failed'):
                        run(report)
                row = json.loads(json.dumps(report))['app_scheme']['commands'][0]
                self.assertNotIn('Test Case', row['head'] + row['tail'])
                self.assertEqual(row.get('test_results', {}).get('lines'), outcomes)
                self.assertFalse(row['test_results']['truncated'])
                retained = row.get('loader_diagnostics', {})
                self.assertEqual(retained.get('matching_lines'), 2)
                self.assertFalse(retained['truncated'])
                self.assertEqual(retained['rows'][0], {**diagnostic, 'pageFacts': page_facts})
                self.assertNotIn('synthetic-private-value', json.dumps(retained))

    def test_loader_diagnostics_report_explicit_bounded_truncation(self) -> None:
        raw = (b'MAC1_LOADER {"label":"ready","checkpoint":"initial"}\n' * 2000)
        with tempfile.TemporaryDirectory(dir=ROOT) as folder:
            report = {}
            with patch.dict('os.environ', {'TMPDIR': folder}), patch(
                'scripts.mac_native.suite_app_scheme._command', return_value=(1, raw),
            ):
                with self.assertRaisesRegex(RuntimeError, 'swift_version failed'):
                    run(report)
            retained = report['app_scheme']['commands'][0].get('loader_diagnostics', {})
            self.assertEqual(retained.get('matching_lines'), 2000)
            self.assertTrue(retained['truncated'])
            self.assertGreater(len(retained['rows']), 0)
            self.assertLessEqual(len(json.dumps(retained).encode()), 65536)

    def test_failed_command_retains_bounded_error_context_in_report(self) -> None:
        # Drive the suite's actual failure/report boundary without running Mac tools.
        middle = (b'preceding context\nExample.swift:42:7: error: missing argument\n'
                  b' 42 | changedCall()\n    | ^ missing owner\n'
                  b'error: second diagnostic\nfollowing context\n')
        for label, raw, truncated in (
            ('middle', b'build head\n' + b'unrelated warning\n' * 4000 + middle
             + b'unrelated warning\n' * 4000 + b'error: fatalError\n', False),
            ('overflow', (b'error: ' + b'x' * 1000 + b'\n') * 100, True),
            ('unicode', ('error: ' + '\u5931\u8d25' * 40000).encode(), True),
            ('no_errors', b'warning: ordinary warning\n', False),
        ):
            with self.subTest(label=label), tempfile.TemporaryDirectory(dir=ROOT) as folder:
                report = {}
                with patch.dict('os.environ', {'TMPDIR': folder}), patch(
                    'scripts.mac_native.suite_app_scheme._command', return_value=(1, raw),
                ) as command:
                    with self.assertRaisesRegex(RuntimeError, 'swift_version failed'):
                        run(report)
                command.assert_called_once()
                # Round-trip exactly the JSON record probe.py retains after failure.
                row = json.loads(json.dumps(report))['app_scheme']['commands'][0]
                self.assertEqual(row['exit_code'], 1)
                self.assertEqual(row['bytes'], len(raw))
                self.assertEqual(row['sha256'], hashlib.sha256(raw).hexdigest())
                self.assertEqual(row['head'], raw[:2048].decode(errors='replace'))
                self.assertEqual(row['tail'], raw[-2048:].decode(errors='replace'))
                errors = row.get('error_context')
                self.assertIsInstance(errors, dict, 'command report must retain compiler diagnostics')
                self.assertEqual(errors['truncated'], truncated)
                self.assertLessEqual(len(json.dumps(errors).encode()), 65536)
                if label == 'middle':
                    self.assertNotIn('missing argument', row['head'] + row['tail'])
                    for line in middle.decode().splitlines():
                        self.assertIn(line, errors['text'])
                    self.assertIn('error: fatalError', errors['text'])
                    self.assertEqual(errors['matching_lines'], 3)
                    self.assertLess(errors['text'].count('unrelated warning'), 10)
                elif label in ('overflow', 'unicode'):
                    self.assertIn('error: ', errors['text'])
                    self.assertEqual(errors['matching_lines'], 100 if label == 'overflow' else 1)
                else:
                    self.assertEqual(errors['text'], '')
                    self.assertEqual(errors['matching_lines'], 0)
                self.assertEqual(report['app_scheme']['qualification']['status'], 'NOT RUN')

    def test_shipping_owner_receipt_save_admission(self) -> None:
        result = subprocess.run(
            ['node', '--experimental-transform-types', 'scripts/mac_native/app_scheme_page_probe.mjs'],
            cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True, timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def check_status(self, events: list[dict], sends: list[dict], status: str, code: int = 0) -> None:
        self.assertEqual(assess(events, sends, code, b'mac1 download\n')['status'], status)

    def test_prompt_events_and_bounded_abort_pass(self) -> None:
        self.check_status(*evidence(), 'PASS')

    def test_eof_and_each_independent_step_are_required(self) -> None:
        for failure in ('missing_eof', 'wrong_eof_count', 'duplicate_eof', 'failure_event',
                        'missing_step', 'failed_step', 'duplicate_step'):
            with self.subTest(failure=failure):
                events, sends = evidence()
                eof = next(row for row in events if row['event'] == 'stream_end')
                step = next(row for row in events if row.get('event') == 'step' and row.get('step') == 'stream')
                if failure == 'missing_eof':
                    events.remove(eof)
                elif failure == 'wrong_eof_count':
                    eof['count'] = 4
                elif failure == 'duplicate_eof':
                    events.append(eof.copy())
                elif failure == 'failure_event':
                    events.append(dict(event='failure', reason='qualification'))
                elif failure == 'missing_step':
                    events.remove(step)
                elif failure == 'failed_step':
                    step['status'] = 'FAIL'
                else:
                    events.append(step.copy())
                self.check_status(events, sends, 'FAIL')
                if failure != 'failure_event':
                    self.assertEqual(assess(events, sends, 0, b'mac1 download\n')['step_results'],
                                     {'stream': 'FAIL', 'abort': 'PASS', 'download': 'PASS'})

    def test_buffered_or_missing_receipts_never_pass(self) -> None:
        for failure in ('buffered', 'missing', 'duplicate', 'foreign_send', 'negative_latency', 'too_fast_send'):
            with self.subTest(failure=failure):
                events, sends = evidence()
                receipt = next(row for row in events if row['event'] == 'receipt')
                if failure == 'buffered':
                    receipt['receiptWallMs'] += 1000
                elif failure == 'missing':
                    events.remove(receipt)
                elif failure == 'duplicate':
                    events.append(receipt.copy())
                elif failure == 'foreign_send':
                    sends[0]['sentWallMs'] += 1
                elif failure == 'negative_latency':
                    receipt['receiptWallMs'] = receipt['sentWallMs'] - 1
                else:
                    sends[1]['sentWallMs'] = 1005
                    second = [row for row in events if row['event'] == 'receipt'][1]
                    second['sentWallMs'] = 1005; second['receiptWallMs'] = 1030
                self.check_status(events, sends, 'FAIL')

    def test_abort_missing_late_or_post_stop_bytes_fail(self) -> None:
        for failure in ('missing', 'late', 'late_bytes', 'foreign_stop'):
            with self.subTest(failure=failure):
                events, sends = evidence()
                stop = next(row for row in events if row['event'] == 'native_stop')
                if failure == 'missing':
                    events.remove(stop)
                elif failure == 'late':
                    stop['nativeWallMs'] += 1000
                elif failure == 'late_bytes':
                    events.append(dict(event='native_write', requestID='abort-owner', nativeWallMs=11050))
                else:
                    stop['requestID'] = 'unrelated-request'
                self.check_status(events, sends, 'FAIL')
                self.assertFalse(assess(events, sends, 0, b'mac1 download\n')['abort_pass'])

    def test_root_json_upload_download_and_timeout_are_required(self) -> None:
        for event in ('root', 'deep_reload', 'json', 'upload', 'download_finish', 'done'):
            with self.subTest(event=event):
                events, sends = evidence()
                events = [row for row in events if row['event'] != event]
                self.check_status(events, sends, 'FAIL')
        events, sends = evidence()
        result = assess(events, sends, 124, b'mac1 download\n')
        self.assertEqual(result['status'], 'FAIL')
        self.assertEqual(len(result['measurements']), 7)
        self.assertTrue(result['partial'])
        self.assertEqual(assess(events, sends, 0, b'wrong')['status'], 'FAIL')
        for path in ('/', '/settings'):
            with self.subTest(path=path):
                events, sends = evidence()
                next(row for row in events if row['event'] == 'deep_reload')['path'] = path
                self.assertFalse(assess(events, sends, 0, b'mac1 download\n')['deep_reload_pass'])

    def test_partial_event_file_keeps_prior_receipts(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'events.jsonl'
            path.write_bytes(b'{"event":"receipt","index":0}\n{"event":')
            self.assertEqual(read_events(path), [{'event': 'receipt', 'index': 0}])
            path.write_bytes(b'invalid\n')
            with self.assertRaises(json.JSONDecodeError):
                read_events(path)

    def test_synthetic_fixture_requires_native_credential_and_preserves_json(self) -> None:
        with synthetic_daemon() as (port, _):
            connection = http.client.HTTPConnection('127.0.0.1', port, timeout=2)
            connection.request('GET', '/api/v1/health')
            response = connection.getresponse()
            self.assertEqual(response.status, 401)
            response.read(); connection.close()
            connection = http.client.HTTPConnection('127.0.0.1', port, timeout=2)
            connection.request('PUT', '/api/v1/mac-qualification/echo', b'{"synthetic":"mac1"}',
                               {'Authorization': 'Bearer synthetic-mac1'})
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            result = json.loads(response.read())
            self.assertEqual(result['bytes'], 20)
            self.assertTrue(result['intact'])
            connection.close()


if __name__ == '__main__':
    unittest.main()
