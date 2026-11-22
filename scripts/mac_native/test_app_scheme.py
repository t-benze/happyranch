"""Standalone MAC-1 evidence-verdict checks, outside retired Python units."""
from __future__ import annotations

import hashlib
import http.client
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts.mac_native.suite_app_scheme import UPLOAD_DIGEST, assess, read_events, synthetic_daemon


def evidence() -> tuple[list[dict], list[dict]]:
    events = [dict(event='root', path='/onboarding', root=1),
              dict(event='deep_reload', path='/settings', origin='happyranch-app://app', root=1)]
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
                   dict(event='native_stop', nativeWallMs=11040, requestID='abort-owner'), dict(event='download_finish'), dict(event='done')])
    return events, sends


class QualificationVerdictTests(unittest.TestCase):
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
