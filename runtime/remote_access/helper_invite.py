"""Bounded stdlib pipe transport for the fixed helper's private B11 worker.

The existing ready/commit exchange carries the live-caller hook to the Gio
parent. It creates no listener and adds no connector IPC verb or authority.
"""
from __future__ import annotations

import json
import os
import select
import subprocess
import sys
import time
from typing import Callable

METHODS = {'CancelInvite': 'cancel-invite', 'RecoverInvite': 'recover-invite'}
OUTCOMES = frozenset({'complete', 'refused', 'uncertain', 'partial_cleanup'})


def _pairs(items: list[tuple[str, object]]) -> dict:
    result: dict = {}
    for key, value in items:
        if key in result:
            raise ValueError('unavailable')
        result[key] = value
    return result


def read_frame(fd: int, deadline: float) -> dict:
    data = bytearray()
    while len(data) <= 4096:
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not select.select([fd], [], [], remaining)[0]:
            raise TimeoutError('expired')
        value = os.read(fd, 1)
        if not value:
            raise ValueError('unavailable')
        data.extend(value)
        if value == b'\n':
            result = json.loads(data, object_pairs_hook=_pairs)
            if type(result) is not dict or time.monotonic() >= deadline:
                raise ValueError('unavailable')
            return result
    raise ValueError('unavailable')


def confirm(deadline: float) -> None:
    """Child-side callback; no journal lock may span this ready/commit wait."""
    if time.monotonic() >= deadline:
        raise TimeoutError('expired')
    print('{"result":"ready"}', flush=True)
    value = read_frame(sys.stdin.fileno(), deadline)
    if set(value) != {'commit'} or value['commit'] is not True:
        raise ValueError('refused')


def effect(command: tuple[str, ...], request: dict,
           before_commit: Callable[[], None]) -> dict:
    deadline = request['deadline']
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.DEVNULL, env={}, cwd='/')
    try:
        process.stdin.write(json.dumps(request).encode() + b'\n')
        process.stdin.flush()
        for _ in range(16):  # B11's finite revalidation boundaries, never a retry.
            value = read_frame(process.stdout.fileno(), deadline)
            if value == {'result': 'ready'}:
                before_commit()
                if time.monotonic() >= deadline:
                    raise TimeoutError('expired')
                process.stdin.write(b'{"commit":true}\n')
                process.stdin.flush()
                continue
            process.stdin.close()
            if process.wait(timeout=max(.001, deadline - time.monotonic())) != 0:
                raise ValueError('unavailable')
            if time.monotonic() >= deadline:
                raise TimeoutError('expired')
            return value
        raise ValueError('unavailable')
    finally:
        if process.poll() is None:
            process.kill()
        process.wait()
        process.stdout.close()
        if not process.stdin.closed:
            process.stdin.close()
