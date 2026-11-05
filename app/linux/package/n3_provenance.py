#!/usr/bin/env python3
"""Runner-only bounded encoding of exact runtime observations.

Product diagnostic JSON retains its existing control-character prohibition.
Runtime command output is retained as attributed UTF8 bytes in base64 instead.
"""
from __future__ import annotations

import base64
import binascii
import hashlib

LABELS = ('python', 'go', 'uv', 'systemd')


def encode_runtime(value: str | None) -> dict | None:
    if value is None:
        return None
    if type(value) is not str:
        raise ValueError('runtime_provenance_type')
    raw = value.encode('utf-8', 'strict')
    if not 0 < len(raw) <= 4096:
        raise ValueError('runtime_provenance_size')
    return {'encoding': 'utf8-base64', 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest(),
            'value': base64.b64encode(raw).decode('ascii')}


def validate_runtime(runtime: object) -> dict:
    if type(runtime) is not dict or set(runtime) != set(LABELS):
        raise ValueError('runtime_provenance_shape')
    result = {}
    for label in LABELS:
        entry = runtime[label]
        if entry is None:
            result[label] = None
            continue
        if (type(entry) is not dict or set(entry) != {'encoding', 'bytes', 'sha256', 'value'}
                or entry['encoding'] != 'utf8-base64' or type(entry['bytes']) is not int
                or not 0 < entry['bytes'] <= 4096 or type(entry['value']) is not str
                or len(entry['value']) > 5464 or type(entry['sha256']) is not str):
            raise ValueError('runtime_provenance_shape')
        try:
            raw = base64.b64decode(entry['value'], validate=True)
            text = raw.decode('utf-8', 'strict')
        except (ValueError, UnicodeError, binascii.Error):
            raise ValueError('runtime_provenance_encoding') from None
        if encode_runtime(text) != entry:
            raise ValueError('runtime_provenance_identity')
        result[label] = entry
    return result
