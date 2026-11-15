"""Pinned Sparkle bundle layout and key-gated updater build inputs."""
from __future__ import annotations

import argparse
import base64
import binascii
import os
from pathlib import Path
import plistlib
import sys

FRAMEWORK = 'Contents/Frameworks/Sparkle.framework'
# Enumerated from the official 2.10.1 SwiftPM ZIP, after removing XPCServices.
LINKS = {'Versions/Current': 'B', **{
    name: 'Versions/Current/' + name for name in (
        'Sparkle', 'Resources', 'Headers', 'PrivateHeaders', 'Modules', 'Autoupdate', 'Updater.app')
}}
FEED = 'https://app.happyranch.ai/mac/appcast.xml'


def public_key(value: str) -> str:
    if value:
        try:
            raw = base64.b64decode(value, validate=True)
        except (ValueError, binascii.Error):
            raise ValueError('SPARKLE_PUBLIC_KEY_INVALID: expected base64 of exactly 32 bytes') from None
        if len(raw) != 32 or base64.b64encode(raw).decode() != value:
            raise ValueError('SPARKLE_PUBLIC_KEY_INVALID: expected canonical base64 of exactly 32 bytes')
    return value


def check_link(relative: str, target: str) -> None:
    prefix = FRAMEWORK + '/'
    if not relative.startswith(prefix) or LINKS.get(relative[len(prefix):]) != target:
        raise ValueError('SPARKLE_SYMLINK_REFUSED: ' + relative)


def validate_framework(app: Path) -> None:
    root = app / FRAMEWORK
    if root.is_symlink() or not root.is_dir():
        raise ValueError('SPARKLE_FRAMEWORK_MISSING')
    observed = set()
    for path in app.rglob('*'):
        if path.is_symlink():
            rel = path.relative_to(app).as_posix()
            check_link(rel, os.readlink(path))
            if not path.resolve(strict=True).is_relative_to(root.resolve()):
                raise ValueError('SPARKLE_SYMLINK_REFUSED: ' + rel)
            observed.add(path.relative_to(root).as_posix())
    if observed != set(LINKS):
        raise ValueError('SPARKLE_LINK_SET_MISMATCH')
    if any(p.name == 'XPCServices' or p.suffix == '.xpc' for p in root.rglob('*')):
        raise ValueError('SPARKLE_XPC_SERVICES_PRESENT')


def configure_plist(data: dict, key: str) -> dict:
    enabled = bool(public_key(key))
    data.update(SUFeedURL=FEED, SUEnableAutomaticChecks=enabled, SUAutomaticallyUpdate=enabled)
    data.pop('SUPublicEDKey', None)
    if enabled:
        data['SUPublicEDKey'] = key
    return data


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='mode', required=True)
    key = sub.add_parser('validate-public-key')
    key.add_argument('key')
    plist = sub.add_parser('plist')
    plist.add_argument('path', type=Path)
    plist.add_argument('key')
    args = parser.parse_args()
    try:
        if args.mode == 'validate-public-key':
            public_key(args.key)
        else:
            data = configure_plist(plistlib.loads(args.path.read_bytes()), args.key)
            args.path.write_bytes(plistlib.dumps(data))
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
