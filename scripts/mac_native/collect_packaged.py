"""Join archived packaged evidence with the existing outer EXIT receipt.

Run after exact-build terminal observation. Preserve both original artifacts;
the separately written collected report carries the final step-8 observation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
from typing import Any


def join_exit_receipt(probe: dict[str, Any], identity: str, source_sha: str) -> dict[str, Any]:
    if (not re.fullmatch(r'[a-f0-9]{40}', source_sha)
            or probe.get('platform') != 'darwin' or probe.get('suite') != 'packaged'
            or probe.get('packaged', {}).get('source_sha') != source_sha
            or set(probe.get('steps', {})) != {str(i) for i in range(1, 9)}):
        raise ValueError('packaged probe identity mismatch')
    fields = [line.split('=', 1) for line in identity.splitlines() if '=' in line]
    def values(key: str) -> list[str]:
        return [value for name, value in fields if name == key]
    if values('source_sha') != [source_sha] or values('suite') != ['packaged']:
        raise ValueError('outer identity source/suite mismatch')
    receipts = values('scratch_removed')
    if receipts not in ([], ['true'], ['false']):
        raise ValueError('ambiguous outer EXIT scratch receipt')
    step = {'name': probe['steps']['8']['name'], 'status': 'NOT_RUN',
            'reason': 'Outer EXIT scratch receipt absent; cleanup is unproven.'}
    if receipts:
        step = {'name': step['name'], 'status': 'PASS' if receipts == ['true'] else 'FAIL',
                'scratch_removed': receipts == ['true'], 'receipt': 'identity.txt',
                'identity_sha256': hashlib.sha256(identity.encode()).hexdigest()}
    return {**probe, 'steps': {**probe['steps'], '8': step}}


def bounded_read(path: Path) -> bytes:
    with path.open('rb') as stream:
        raw = stream.read(65537)
    if len(raw) > 65536:
        raise ValueError('packaged artifact exceeds 64 KiB')
    return raw


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--probe', type=Path, required=True)
    parser.add_argument('--identity', type=Path, required=True)
    parser.add_argument('--source-sha', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        result = join_exit_receipt(json.loads(bounded_read(args.probe)),
                                   bounded_read(args.identity).decode('utf-8'), args.source_sha)
        raw = json.dumps(result, separators=(',', ':')) + '\n'
        if len(raw.encode()) > 65536:
            raise ValueError('collected report exceeds 64 KiB')
        with args.output.open('x') as stream:
            stream.write(raw)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(2, str(exc) + '\n')


if __name__ == '__main__':
    main()
