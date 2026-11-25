"""Standalone MAC-1 evidence-verdict checks, outside retired Python units."""
from __future__ import annotations

import base64
import gzip
import hashlib
import http.client
import json
import os
import subprocess
import struct
import sys
import tarfile
import tempfile
import time
import unittest
import zlib
from pathlib import Path
from contextlib import ExitStack
from unittest.mock import patch

from scripts.mac_native.suite_app_scheme import PNG_CAP, ROOT, UPLOAD_DIGEST, assess, qualify_cold_client, qualify_dialogs, qualify_steps, read_events, run, screenshot_archive, synthetic_daemon
from scripts.mac_native.app_scheme_diagnostics import sample_stalled_processes
from scripts.mac_native.app_scheme_test_evidence import archive_events, assess_native, source_inventory, terminal_results
from scripts.mac_native.suite_supervisor import CommandTimeout, _build_environment, _command, _prepare_keychain


def native_evidence(parameterized: bool = True) -> tuple[bytes, list[dict], dict]:
    inventory = {'xctest': ['HappyRanchAppTests.HomeCandidateOwnershipTests/testKeepsActive'],
                 'swift_testing': ['HappyRanchAppTests/HomeInviteModelTests.swift/retainsSave']}
    raw = ("Test Case '-[HappyRanchAppTests.HomeCandidateOwnershipTests testKeepsActive]' started.\n"
           "Test Case '-[HappyRanchAppTests.HomeCandidateOwnershipTests testKeepsActive]' passed (0.1 seconds).\n").encode()
    signature = 'retainsSave(_:)' if parameterized else 'retainsSave()'
    key = f'HappyRanchAppTests.HomeInviteModelTests/{signature}/HappyRanchAppTests/HomeInviteModelTests.swift:12:3'
    records = [dict(version=0, kind='test', payload=dict(kind='function', id=key, name=signature,
               sourceLocation=dict(fileID='HappyRanchAppTests/HomeInviteModelTests.swift'), isParameterized=parameterized))]
    kinds = (['runStarted', 'testStarted'] + (['testCaseStarted', 'testCaseEnded'] * 2 if parameterized else [])
             + ['testEnded', 'runEnded'])
    for kind in kinds:
        records.append(dict(version=0, kind='event', payload=dict(kind=kind, **(
            {} if kind in ('runStarted', 'runEnded') else {'testID': key}))))
    return raw, records, inventory


def native_stream(records: list[dict]) -> bytes:
    return ('\n'.join(json.dumps(row) for row in records) + '\n').encode()


def screenshot_png(width: int = 760, height: int = 560) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress((b'\0' + b'\xff' * (width * 3)) * height)) + chunk(b'IEND', b''))


def screenshot_files(folder: Path, locale: str, with_scroll: bool = False) -> list[dict]:
    records = []
    for theme in ('light', 'dark'):
        for scale in (100, 200):
            for dialog in ('settings', 'connection', 'main'):
                name = f'{locale}-{theme}-{scale}-{dialog}.png'
                (folder / name).write_bytes(screenshot_png())
                records.append(dict(event='screenshot', name=name, theme=theme, webTheme=theme))
                if scale == 200 and with_scroll:
                    for number in (1, 2):
                        name = f'{locale}-{theme}-{scale}-{dialog}-scroll-{number}.png'
                        (folder / name).write_bytes(screenshot_png())
                        records.append(dict(event='screenshot', name=name, theme=theme, webTheme=theme))
                    records.append(dict(event='capture_complete', locale=locale, theme=theme, textPercent=scale,
                        dialog=dialog, scrollCaptures=2, scrollers=[dict(index=0, pages=2, step=300, maximum=600)]))
    return records


class DialogCaptureEvidenceTests(unittest.TestCase):
    def test_archive_roundtrips_full_locale_and_refuses_missing_alias_oversize_or_wrong_dimensions(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            expected = screenshot_files(root, 'en')
            rows = screenshot_archive(root, 'en')
            self.assertEqual({row['name'] for row in rows}, {row['name'] for row in expected})
            self.assertEqual(len(rows), 12)
            for row in rows:
                self.assertEqual(base64.b64decode(row['data']), screenshot_png())
                self.assertEqual(row['sha256'], hashlib.sha256(screenshot_png()).hexdigest())
                self.assertEqual((row['width'], row['height']), (760, 560))
            for number in (1, 2):
                (root / f'en-dark-200-connection-scroll-{number}.png').write_bytes(screenshot_png())
            scrolled = screenshot_archive(root, 'en', {('dark', 'connection'): 2})
            self.assertEqual(len(scrolled), 14)
            self.assertEqual([row['scroll_capture'] for row in scrolled if row['name'].startswith('en-dark-200-connection')], [0, 1, 2])
            self.assertEqual(base64.b64decode(scrolled[-2]['data']), screenshot_png())
            (root / 'en-dark-200-connection-scroll-1.png').unlink()
            with self.assertRaises(OSError):
                screenshot_archive(root, 'en', {('dark', 'connection'): 2})
            with self.assertRaises(ValueError):
                screenshot_archive(root, 'en', {('dark', 'connection'): 17})
            image = root / 'en-light-100-settings.png'
            for invalid in ('missing', 'alias', 'oversize', 'dimensions'):
                with self.subTest(invalid=invalid):
                    image.unlink()
                    if invalid == 'alias':
                        image.symlink_to(root / 'en-light-100-main.png')
                    elif invalid == 'oversize':
                        with image.open('wb') as output:
                            output.truncate(PNG_CAP + 1)
                    elif invalid == 'dimensions':
                        image.write_bytes(screenshot_png(759))
                    with self.assertRaises((OSError, ValueError)):
                        screenshot_archive(root, 'en')
                    if image.exists() or image.is_symlink():
                        image.unlink()
                    image.write_bytes(screenshot_png())

    def test_process_collection_requires_both_locales_complete_receipts_and_zero_exit(self) -> None:
        for failure in ('none', 'missing-file', 'missing-receipt', 'nonzero', 'missing-completion',
                        'duplicate-completion', 'missing-scroll', 'scroll-gap', 'duplicate-receipt', 'wrong-theme',
                        'partial-progress'):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as folder:
                root = Path(folder); downloads = root / 'downloads'; downloads.mkdir()
                calls = []
                def command(argv: list[str], *args: object, **kwargs: object) -> tuple[int, bytes]:
                    locale = argv[-1].removeprefix('dialogs-'); calls.append(locale)
                    rows = screenshot_files(downloads, locale, with_scroll=True)
                    if locale == 'zh' and failure == 'missing-file':
                        (downloads / f'{locale}-dark-100-main.png').unlink()
                    if locale == 'zh' and failure == 'missing-receipt':
                        rows = [row for row in rows if row.get('name') != f'{locale}-dark-100-main.png']
                    if locale == 'zh' and failure == 'missing-completion':
                        rows = [row for row in rows if row.get('event') != 'capture_complete']
                    if locale == 'zh' and failure == 'duplicate-completion':
                        rows.append(next(row for row in rows if row['event'] == 'capture_complete').copy())
                    if locale == 'zh' and failure == 'missing-scroll':
                        (downloads / f'{locale}-light-200-connection-scroll-1.png').unlink()
                    if locale == 'zh' and failure == 'scroll-gap':
                        rows = [row for row in rows if row.get('name') != f'{locale}-light-200-connection-scroll-1.png']
                    if locale == 'zh' and failure == 'duplicate-receipt':
                        rows.append(rows[0].copy())
                    if locale == 'zh' and failure == 'wrong-theme':
                        next(row for row in rows if row.get('theme') == 'dark' and row['event'] == 'screenshot')['webTheme'] = 'light'
                    if locale == 'zh' and failure == 'partial-progress':
                        rows = [dict(event='capture_progress', step='applied-theme', locale=locale, timeoutMs=2000),
                                dict(event='capture_step', step='applied-theme', locale=locale, result='timeout')]
                    Path(argv[-3]).write_text('\n'.join(json.dumps(row) for row in rows) + '\n')
                    if locale == 'zh' and failure == 'partial-progress':
                        with Path(argv[-3]).open('a') as output:
                            output.write('{"event":"capture')  # killed write is not a complete receipt
                        return 124, b''
                    return (1 if locale == 'zh' and failure == 'nonzero' else 0), b''
                section = {}
                with patch('scripts.mac_native.suite_app_scheme._command', side_effect=command):
                    qualify_dialogs(['product', '--qualify-app-scheme', 'assets', 'policy', '1234'], {},
                                    root, time.monotonic() + 10, section, downloads)
                self.assertEqual(calls, ['en', 'zh'])
                self.assertEqual(section['dialogs']['status'], 'PASS' if failure == 'none' else 'FAIL')
                incomplete = failure in ('missing-file', 'missing-scroll', 'missing-completion', 'duplicate-completion', 'partial-progress')
                self.assertEqual(len(section['dialogs']['screenshots']), 24 if incomplete else 48)
                if failure == 'partial-progress':
                    failed = section['dialogs']['processes'][1]
                    self.assertEqual(failed['exit_code'], 124)
                    self.assertEqual(failed.get('last_step', {}).get('step'), 'applied-theme')
                    self.assertEqual(failed['diagnostic_events'][-1]['result'], 'timeout')
                    self.assertEqual(failed['capture_error_reason'], 'missing scroll capture completion')
                    self.assertEqual(failed['events'], [])


class ColdClientCompositionEvidenceTests(unittest.TestCase):
    def test_requires_exact_composition_receipt_and_successful_process(self) -> None:
        receipt = dict(event='cold_client', status='PASS', entries=3, dialogs=True, no_target=True, unchanged_target=True)
        for failure in ('none', 'missing', 'duplicate', 'failed', 'entries', 'dialogs', 'no-target', 'target-changed', 'nonzero'):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                rows = [dict(receipt)]
                if failure == 'missing':
                    rows = []
                elif failure == 'duplicate':
                    rows *= 2
                elif failure == 'failed':
                    rows[0]['status'] = 'FAIL'
                elif failure == 'entries':
                    rows[0]['entries'] = 4
                elif failure == 'dialogs':
                    rows[0]['dialogs'] = False
                elif failure == 'no-target':
                    rows[0]['no_target'] = False
                elif failure == 'target-changed':
                    rows[0]['unchanged_target'] = False
                def command(argv: list[str], *args: object, **kwargs: object) -> tuple[int, bytes]:
                    self.assertEqual(argv[-1], 'cold-client')
                    Path(argv[-3]).write_text('\n'.join(json.dumps(row) for row in rows) + '\n')
                    return (1 if failure == 'nonzero' else 0), b''
                section = {}
                with patch('scripts.mac_native.suite_app_scheme._command', side_effect=command):
                    qualify_cold_client(['product', '--qualify-app-scheme', 'assets', 'policy', '0'], {},
                                        root, time.monotonic() + 10, section, root)
                self.assertEqual(section['cold_client']['status'], 'PASS' if failure == 'none' else 'FAIL')


class NativeWorkloadEvidenceTests(unittest.TestCase):
    def test_timeout_retains_partial_streams_xctest_and_swift_archive_without_passing(self) -> None:
        raw, records, inventory = native_evidence()
        stalled = 'HappyRanchAppTests.HomeCandidateOwnershipTests/testStalled'
        inventory['xctest'].append(stalled)
        raw += b"Test Case '-[HappyRanchAppTests.HomeCandidateOwnershipTests testStalled]' started.\n"
        stdout = b'warning\n' * 4000 + raw + b'warning\n' * 4000
        stderr = b'diagnostic\n' * 4000 + b'error: synthetic timeout context\n'
        for complete, partial_line in ((False, False), (True, False), (False, True)):
            stream = native_stream(records if complete else records[:4]) + (b'{' if partial_line else b'')
            with self.subTest(complete_events=complete, partial_line=partial_line), tempfile.TemporaryDirectory(dir=ROOT) as folder:
                report = {}
                def transfer(url: str, archive: Path, *args: object) -> dict:
                    with tarfile.open(archive, 'w:gz'):
                        pass
                    return {'curl_exit_code': 0}
                def command(argv: list[str], env: dict, cwd: Path, deadline: float, **kwargs: object) -> tuple[int, bytes]:
                    if 'test' not in argv:
                        return (1, b'') if '--show-bin-path' in argv else (0, b'')
                    event_write = ''
                    output = stdout if '--disable-swift-testing' in argv else b'Swift Testing partial\n'
                    if '--event-stream-output-path' in argv:
                        events = argv[argv.index('--event-stream-output-path') + 1]
                        event_write = f'Path({events!r}).write_bytes({stream!r}); '
                    script = ('from pathlib import Path; import os, time; ' + event_write
                              + f'os.write(1, {output!r}); os.write(2, {stderr!r}); time.sleep(60)')
                    return _command([sys.executable, '-c', script], env, cwd, time.monotonic() + 0.5, **kwargs)
                with patch.dict('os.environ', {'TMPDIR': folder}), patch(
                    'scripts.mac_native.suite_app_scheme._command', side_effect=command
                ), patch('scripts.mac_native.suite_app_scheme.curl_attempt', side_effect=transfer), patch(
                    'scripts.mac_native.suite_app_scheme.verify_checksum'
                ), patch('scripts.mac_native.suite_app_scheme.source_inventory', return_value=inventory), patch(
                    'scripts.mac_native.suite_app_scheme._prepare_keychain'
                ):
                    with self.assertRaisesRegex(RuntimeError, 'bin_path failed'):
                        run(report, Path(folder))
                section = json.loads(json.dumps(report))['app_scheme']
                for library, output in (('xctest', stdout), ('swift_testing', b'Swift Testing partial\n')):
                    row = next(row for row in section['commands'] if row['name'] == 'product_' + library)
                    self.assertIsNone(row['exit_code'], 'a killed command has no observed normal exit')
                    self.assertEqual(row['command_error'], 'CommandTimeout')
                    self.assertEqual(row['timeout_seconds'], 570)
                    for name, data in (('stdout', output), ('stderr', stderr)):
                        captured = row[name]
                        self.assertEqual(captured['bytes'], len(data))
                        self.assertEqual(captured['sha256'], hashlib.sha256(data).hexdigest())
                        self.assertEqual(captured['head'], data[:2048].decode())
                        self.assertEqual(captured['tail'], data[-2048:].decode())
                        self.assertEqual(captured['excerpts_truncated'], len(data) > 4096)
                    if library == 'xctest':
                        self.assertNotIn('Test Case', row['head'] + row['tail'])
                        self.assertEqual(row['test_results']['lines'], terminal_results(raw.decode())['lines'])
                    self.assertIn('synthetic timeout context', row['error_context']['text'])
                    artifact = section['native_tests']['diagnostic_artifacts'][library]
                    encoded = (Path(folder) / artifact['path']).read_bytes()
                    self.assertEqual(artifact['bytes'], len(encoded))
                    self.assertEqual(artifact['sha256'], hashlib.sha256(encoded).hexdigest())
                    saved = json.loads(encoded)
                    for name, data in (('stdout', output), ('stderr', stderr)):
                        self.assertEqual(gzip.decompress(base64.b64decode(saved[name]['data'])), data)
                    self.assertIn('processes', saved['timeout_diagnostics'])
                native = section['native_tests']
                self.assertEqual(native['status'], 'FAIL', 'complete events cannot excuse either timeout')
                self.assertIn('swift test exit unobserved', native['errors'])
                self.assertEqual(native['library_exits'], [None, None])
                self.assertIn(stalled, [row['id'] for row in native['unfinished_cases']])
                self.assertIn(dict(library='XCTest', id=stalled, event='started'), native['lifecycle'])
                xctest = next(case for case in native['cases'] if case['library'] == 'XCTest')
                self.assertEqual(xctest['status'], 'PASS', 'preserve observed partial outcomes')
                if not complete:
                    running = next(row for row in native['unfinished_cases'] if 'retainsSave' in row['id'])
                    self.assertEqual(running['status'], 'INCOMPLETE')
                archive = native['event_archive']
                self.assertEqual(gzip.decompress(base64.b64decode(archive['data'])), stream)
                self.assertEqual(archive['sha256'], hashlib.sha256(stream).hexdigest())

    def test_full_package_workload_enables_both_libraries_and_records_each_case(self) -> None:
        for parameterized, code in ((True, 0), (False, 0), (False, 1), (False, None)):
            raw, records, inventory = native_evidence(parameterized=parameterized)
            stream = native_stream(records) + (b'{' if code is None else b'')
            with tempfile.TemporaryDirectory(dir=ROOT) as folder:
                report, calls = {}, []
                def transfer(url: str, archive: Path, *args: object) -> dict:
                    with tarfile.open(archive, 'w:gz'):
                        pass
                    return {'curl_exit_code': 0}
                def command(argv: list[str], *args: object, **kwargs: object) -> tuple[int, bytes]:
                    calls.append((argv, kwargs))
                    if 'test' in argv:
                        output = raw if '--disable-swift-testing' in argv else b''
                        if '--event-stream-output-path' in argv:
                            Path(argv[argv.index('--event-stream-output-path') + 1]).write_bytes(stream)
                        if code is None:
                            raise CommandTimeout(output, b'')
                        return code, output
                    return (1, b'') if '--show-bin-path' in argv else (0, b'')
                with patch.dict('os.environ', {'TMPDIR': folder}), patch(
                    'scripts.mac_native.suite_app_scheme._command', side_effect=command
                ), patch('scripts.mac_native.suite_app_scheme.curl_attempt', side_effect=transfer), patch(
                    'scripts.mac_native.suite_app_scheme.verify_checksum'
                ), patch('scripts.mac_native.suite_app_scheme.source_inventory', return_value=inventory), patch(
                    'scripts.mac_native.suite_app_scheme._prepare_keychain'
                ):
                    with self.assertRaisesRegex(RuntimeError, 'bin_path failed'):
                        run(report, Path(folder))
                test_calls = [(argv, kwargs) for argv, kwargs in calls if 'test' in argv]
                self.assertEqual(len(test_calls), 2, 'separate full-package library invocations, even after timeout')
                for index, (argv, kwargs) in enumerate(test_calls):
                    self.assertNotIn('--filter', argv)
                    self.assertNotIn('--skip', argv)
                    for option in ('--enable-testable-imports', '--no-parallel'):
                        self.assertIn(option, argv)
                    self.assertIn('--enable-xctest' if index == 0 else '--disable-xctest', argv)
                    self.assertIn('--disable-swift-testing' if index == 0 else '--enable-swift-testing', argv)
                    self.assertEqual(argv[argv.index('-c') + 1], 'release')
                    self.assertEqual(kwargs['output_cap'], 16 * 1024 * 1024)
                    self.assertIn('timeout_observer', kwargs)
                argv = test_calls[1][0]
                self.assertEqual(argv[argv.index('--event-stream-version') + 1], '0')
                for row in report['app_scheme']['commands']:
                    if row['name'] in ('product_xctest', 'product_swift_testing'):
                        self.assertEqual(row['timeout_seconds'], 570)
                        self.assertEqual(row['diagnostic_timeout_seconds'], 30)
                result = report['app_scheme']['native_tests']
                self.assertEqual(result['status'], 'PASS' if code == 0 else 'FAIL')
                if code == 0:
                    self.assertEqual([row['status'] for row in result['cases']], ['PASS'] * (3 if parameterized else 2))
                    if parameterized:
                        self.assertEqual([row['case'] for row in result['cases'][1:]], [1, 2])
                    else:
                        self.assertEqual(result['cases'][1]['starts'], 1)
                        self.assertEqual(result['cases'][1]['ends'], 1)
                archive = result['event_archive']
                compressed = base64.b64decode(archive['data'], validate=True)
                self.assertEqual(gzip.decompress(compressed), stream)
                self.assertEqual(archive['bytes'], len(stream))
                self.assertEqual(archive['sha256'], hashlib.sha256(stream).hexdigest())
                self.assertEqual(archive['gzip_bytes'], len(compressed))
                self.assertEqual(archive['gzip_sha256'], hashlib.sha256(compressed).hexdigest())

    def test_timeout_samples_only_owned_processes_before_kill(self) -> None:
        capture, observed = {}, []
        stack = b'synthetic stalled test stack\n'
        def diagnostic(argv: list[str], env: dict, cwd: Path, deadline: float, **kwargs: object) -> tuple[int, bytes]:
            if argv[0] == '/bin/ps':
                pid = capture['driver_pid']
                return 0, f'{pid} 1 {pid} /test/PackageTests.xctest\n999999 1 1 /unrelated\n'.encode()
            pid = int(argv[1])
            os.kill(pid, 0)  # The actual stalled process must still be alive.
            observed.append(pid)
            self.assertEqual(argv[2:5], ['1', '1', '-file'])
            Path(argv[5]).write_bytes(stack)
            return 0, b'sampled\n'
        with tempfile.TemporaryDirectory() as folder:
            scratch = Path(folder)
            with patch('scripts.mac_native.app_scheme_diagnostics._command', side_effect=diagnostic):
                with self.assertRaises(CommandTimeout):
                    _command([sys.executable, '-c', 'import time; time.sleep(60)'], dict(os.environ), scratch,
                             time.monotonic() + 0.2, timeout_observer=lambda pid: sample_stalled_processes(
                                 pid, dict(os.environ), scratch, time.monotonic() + 5, capture))
            self.assertEqual(observed, [capture.get('driver_pid')])
            self.assertTrue(capture['complete'])
            self.assertEqual(gzip.decompress(base64.b64decode(capture['samples'][0]['stack']['data'])), stack)
            with self.assertRaises(ProcessLookupError):
                os.kill(capture['driver_pid'], 0)  # Reaped only after sampling.

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

    def test_ordinary_function_lifecycle_and_parameter_cases_are_independent(self) -> None:
        raw, records, inventory = native_evidence()
        _, ordinary, ordinary_inventory = native_evidence(parameterized=False)
        ordinary_key = ordinary[0]['payload']['id'].replace('retainsSave()', 'ordinarySave()')
        ordinary[0]['payload'].update(id=ordinary_key, name='ordinarySave()')
        for row in ordinary[1:]:
            if 'testID' in row['payload']:
                row['payload']['testID'] = ordinary_key
        inventory['swift_testing'].append(ordinary_inventory['swift_testing'][0].replace('retainsSave', 'ordinarySave'))
        records.insert(1, ordinary[0])
        records[-1:-1] = ordinary[2:-1]  # ordinary function start/end, same run
        result = assess_native(raw, native_stream(records), inventory, 0)
        self.assertEqual(result['status'], 'PASS', result['errors'])
        rows = [row for row in result['cases'] if row['id'] == ordinary_key]
        self.assertEqual(rows, [dict(library='Swift Testing', id=ordinary_key, lifecycle='function',
                                     status='PASS', starts=1, ends=1)])
        self.assertEqual([row['case'] for row in result['cases'] if 'case' in row], [1, 2])

    def test_ordinary_lifecycle_never_falls_back_to_terminal_text(self) -> None:
        for failure in ('missing_start', 'missing_end', 'duplicate_start', 'duplicate_end', 'reversed',
                        'issue', 'late_issue', 'skip', 'cancel', 'unexpected_case', 'unknown_owner',
                        'outside_run', 'bad_parameterization'):
            with self.subTest(failure=failure):
                raw, records, inventory = native_evidence(parameterized=False)
                key = records[0]['payload']['id']
                event = lambda kind: dict(version=0, kind='event', payload=dict(kind=kind, testID=key))
                if failure in ('missing_start', 'missing_end'):
                    records.pop(2 if failure == 'missing_start' else 3)
                elif failure in ('duplicate_start', 'duplicate_end'):
                    records.insert(3, event('testStarted' if failure == 'duplicate_start' else 'testEnded'))
                elif failure == 'reversed':
                    records[2], records[3] = records[3], records[2]
                elif failure in ('issue', 'late_issue', 'skip', 'cancel'):
                    kind = {'issue': 'issueRecorded', 'late_issue': 'issueRecorded',
                            'skip': 'testSkipped', 'cancel': 'testCancelled'}[failure]
                    records.insert(4 if failure == 'late_issue' else 3, event(kind))
                elif failure == 'unexpected_case':
                    records.insert(3, event('testCaseEnded'))
                elif failure == 'unknown_owner':
                    records[2]['payload']['testID'] = 'foreign'
                elif failure == 'outside_run':
                    records[1], records[2] = records[2], records[1]
                else:
                    records[0]['payload']['isParameterized'] = 'false'
                raw += b'\nTest "retainsSave" passed after 0.001 seconds.\n'
                result = assess_native(raw, native_stream(records), inventory, 0)
                self.assertEqual(result['status'], 'FAIL')
                if failure in ('issue', 'late_issue', 'skip', 'cancel'):
                    self.assertEqual(result['cases'][-1]['status'], 'FAIL')

    def test_raw_archive_preserves_failed_partial_bytes_and_refuses_overflow(self) -> None:
        raw = b'{"version":0}\n{"partial":'
        self.assertEqual(gzip.decompress(base64.b64decode(archive_events(raw)['data'])), raw)
        for value in (b'', b'x' * (16 * 1024 * 1024 + 1)):
            with self.assertRaises(ValueError):
                archive_events(value)
        with patch('scripts.mac_native.app_scheme_test_evidence.EVENT_ARCHIVE_CAP', 1):
            with self.assertRaises(ValueError):
                archive_events(raw)

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
                        run(report, Path(folder))
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
                    run(report, Path(folder))
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
                        run(report, Path(folder))
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
