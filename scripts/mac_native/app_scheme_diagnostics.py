"""Bounded native test diagnostics in the existing evidence/*.json collection."""
from __future__ import annotations

import base64
import gzip
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

from scripts.mac_native.app_scheme_test_evidence import TEST_OUTPUT_CAP
from scripts.mac_native.suite_supervisor import CommandTimeout, _command

ARCHIVE_CAP = 8 * 1024 * 1024  # Below the existing 10 MB artifact transfer cap.
SAMPLE_CAP = 1024 * 1024


def archive_stream(raw: bytes) -> dict[str, Any]:
    if len(raw) > TEST_OUTPUT_CAP:
        raise ValueError('native diagnostic stream exceeds 16 MiB')
    compressed = gzip.compress(raw, mtime=0)
    return dict(encoding='gzip+base64', bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest(),
                gzip_bytes=len(compressed), gzip_sha256=hashlib.sha256(compressed).hexdigest(),
                data=base64.b64encode(compressed).decode('ascii'))


def write_diagnostics(directory: Path, library: str, capture: dict[str, Any]) -> dict[str, Any]:
    """Fixed job-owned files; initialize before tests so old builds cannot pass."""
    if library not in ('xctest', 'swift_testing'):
        raise ValueError('unsupported native library')
    path = directory / f'mac-native-{library}.json'
    encoded = (json.dumps(capture, sort_keys=True) + '\n').encode()
    if len(encoded) > ARCHIVE_CAP:
        raise ValueError('native diagnostic artifact exceeds 8 MiB')
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'wb') as target:
        target.write(encoded)
        target.flush()
        os.fsync(target.fileno())
    return dict(path=path.name, bytes=len(encoded), sha256=hashlib.sha256(encoded).hexdigest())


def sample_stalled_processes(pid: int, env: dict[str, str], scratch: Path,
                             deadline: float, capture: dict[str, Any]) -> None:
    """Read only the command's group/descendants, never ambient argv or env.

    XCTest/Swift Testing children precede the SwiftPM driver. All observations
    share 30 seconds inside the suite deadline; errors/omissions stay explicit.
    The caller still kills/reaps its owned process group after this returns.
    """
    end = min(deadline, time.monotonic() + 30)
    capture.update(driver_pid=pid, processes=[], samples=[], complete=False)
    try:
        code, raw = _command(['/bin/ps', '-axo', 'pid=,ppid=,pgid=,comm='], env, scratch,
                             min(end, time.monotonic() + 3), output_cap=1024 * 1024)
        capture['census_exit_code'] = code
        if code:
            return
        processes = []
        for line in raw.decode(errors='replace').splitlines():
            fields = line.split(None, 3)
            if len(fields) != 4:
                raise ValueError('unsupported ps row')
            processes.append(dict(pid=int(fields[0]), ppid=int(fields[1]), pgid=int(fields[2]), executable=fields[3]))
        owned = {pid} | {row['pid'] for row in processes if row['pgid'] == pid}
        while True:
            descendants = {row['pid'] for row in processes if row['ppid'] in owned}
            if descendants <= owned:
                break
            owned |= descendants
        rows = [row for row in processes if row['pid'] in owned]
        rows.sort(key=lambda row: ('xctest' not in row['executable'].lower()
                                  and 'packagetests' not in row['executable'].lower(), row['pid'] == pid, row['pid']))
        capture['processes'] = rows[:32]
        capture['omitted_processes'] = max(0, len(rows) - 32)
        for row in rows[:32]:
            if time.monotonic() >= end:
                break
            sample: dict[str, Any] = dict(pid=row['pid'], exit_code=None)
            capture['samples'].append(sample)
            path = scratch / f'native-sample-{row["pid"]}.txt'
            stderr = bytearray()
            try:
                code, output = _command(['/usr/bin/sample', str(row['pid']), '1', '1', '-file', str(path)],
                                        env, scratch, min(end, time.monotonic() + 5),
                                        output_cap=65536, stderr_data=stderr)
                sample['exit_code'] = code
            except CommandTimeout as error:
                output = error.stdout
                sample['error'] = type(error).__name__
            except (OSError, RuntimeError) as error:
                output = b''
                sample['error'] = type(error).__name__
            sample['stdout'] = archive_stream(output)
            sample['stderr'] = archive_stream(bytes(stderr))
            if path.is_file() and path.stat().st_size <= SAMPLE_CAP:
                sample['stack'] = archive_stream(path.read_bytes())
            else:
                sample['stack_error'] = 'missing or exceeds 1 MiB'
        capture['complete'] = (bool(rows) and len(capture['samples']) == len(rows)
                               and all(row['exit_code'] == 0 and 'stack' in row for row in capture['samples']))
    except (OSError, RuntimeError, ValueError, TimeoutError) as error:
        capture['error'] = type(error).__name__
