"""Write a new local appcast from a final release ZIP; never publish or use keys."""
from __future__ import annotations

import argparse
import base64
import binascii
from datetime import datetime, timezone
from email.utils import format_datetime
import os
from pathlib import Path
import plistlib
import re
import stat
import sys
import tempfile
from urllib.parse import unquote, urlsplit
from xml.etree import ElementTree as ET
from xml.parsers import expat
import zipfile

from sparkle_bundle import FEED, VERSION_PATTERN, public_key

SPARKLE = 'http://www.andymatuschak.org/xml-namespaces/sparkle'
NS = '{' + SPARKLE + '}'


def version(value: str, diagnostic: str) -> tuple[int, ...]:
    if not re.fullmatch(VERSION_PATTERN, value):
        raise ValueError(diagnostic + ': expected three numeric components without leading zeros')
    return tuple(map(int, value.split('.')))


def signature(value: str) -> None:
    try:
        raw = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error):
        raise ValueError('SIGNATURE_SHAPE_INVALID: expected canonical base64 of exactly 64 bytes') from None
    if len(raw) != 64 or base64.b64encode(raw).decode() != value:
        raise ValueError('SIGNATURE_SHAPE_INVALID: expected canonical base64 of exactly 64 bytes')


def download_url(value: str) -> None:
    parsed = urlsplit(value)
    if (parsed.scheme != 'https' or parsed.netloc != 'github.com' or parsed.query or parsed.fragment
            or not re.fullmatch(r'https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/releases/download/[^/?#\s]+/[^/?#\s]+', value)
            or any(part in ('.', '..') or re.search(r'[/\\\s\x00-\x1f\x7f]', part)
                   for part in map(unquote, parsed.path.split('/')[1:]))):
        raise ValueError('DOWNLOAD_URL_INVALID: expected https://github.com/OWNER/REPO/releases/download/TAG/ASSET')


def existing_feed(raw: bytes, build: tuple[int, ...]) -> int:
    """Validate without serializing, returning a byte insertion offset."""
    parser = expat.ParserCreate(namespace_separator='}')
    stack: list[str] = []
    insertion: list[int] = []

    def refuse_dtd(*_args: object) -> None:
        raise ValueError('FEED_INVALID: DTDs and entities are unsupported')

    def declaration(_xml_version: str, encoding: str | None, _standalone: int) -> None:
        if encoding and encoding.lower() not in ('utf-8', 'utf8'):
            raise ValueError('FEED_INVALID: existing feed must be UTF-8')

    def start(name: str, _attributes: dict) -> None:
        if name == 'item' and stack == ['rss', 'channel'] and not insertion:
            insertion.append(parser.CurrentByteIndex)
        stack.append(name)

    def end(_name: str) -> None:
        stack.pop()

    parser.StartDoctypeDeclHandler = refuse_dtd
    parser.EntityDeclHandler = refuse_dtd
    parser.XmlDeclHandler = declaration
    parser.StartElementHandler = start
    parser.EndElementHandler = end
    try:
        raw.decode('utf-8-sig')
        parser.Parse(raw, True)
        root = ET.fromstring(raw)
    except (UnicodeError, expat.ExpatError, ET.ParseError):
        raise ValueError('FEED_INVALID: malformed UTF-8 XML') from None
    channels = root.findall('channel')
    if root.tag != 'rss' or root.get('version') != '2.0' or len(channels) != 1:
        raise ValueError('FEED_INVALID: expected RSS 2.0 with one channel')
    items = channels[0].findall('item')
    if not items or len(list(root.iter('item'))) != len(items) or not insertion:
        raise ValueError('FEED_INVALID: expected channel items; use --bootstrap only for the first feed')
    builds = set()
    for item in items:
        enclosures = item.findall('enclosure')
        if len(enclosures) != 1:
            raise ValueError('FEED_INVALID: each item needs one enclosure')
        values = {}
        # Sparkle accepts item elements and legacy enclosure attributes.
        for field in ('version', 'shortVersionString'):
            nodes = item.findall(NS + field)
            candidates = [node.text or '' for node in nodes]
            if NS + field in enclosures[0].attrib:
                candidates.append(enclosures[0].attrib[NS + field])
            if len(candidates) != 1 or any(len(node) for node in nodes):
                raise ValueError('FEED_VERSION_INVALID: missing or ambiguous ' + field)
            values[field] = version(candidates[0], 'FEED_VERSION_INVALID')
        if values['version'] in builds:
            raise ValueError('FEED_DUPLICATE_VERSION: existing build reused')
        builds.add(values['version'])
    if build in builds:
        raise ValueError('BUILD_REUSED: build already exists in the feed')
    if any(build <= old for old in builds):
        raise ValueError('BUILD_NOT_MONOTONIC: build must exceed every existing item, including internal')
    return insertion[0]


def archive_plist(archive: Path, length: str, short: str, build: str, key: str) -> None:
    if not re.fullmatch(r'[1-9][0-9]*', length):
        raise ValueError('LENGTH_INVALID: expected positive decimal bytes from sign_update')
    with archive.open('rb') as stream:
        if os.fstat(stream.fileno()).st_size != int(length):
            raise ValueError('ZIP_LENGTH_MISMATCH: final ZIP differs from sign_update length')
        try:
            with zipfile.ZipFile(stream) as zipped:
                members = [m for m in zipped.infolist() if m.filename == 'HappyRanch.app/Contents/Info.plist']
                if (len(members) != 1 or members[0].file_size > 1024 * 1024
                        or stat.S_IFMT(members[0].external_attr >> 16) not in (0, stat.S_IFREG)):
                    raise ValueError('ZIP_PLIST_INVALID: expected one regular HappyRanch.app/Contents/Info.plist <=1 MiB')
                info = plistlib.loads(zipped.read(members[0]))
        except (zipfile.BadZipFile, plistlib.InvalidFileException, expat.ExpatError, RuntimeError, NotImplementedError):
            raise ValueError('ZIP_PLIST_INVALID: unreadable ZIP or Info.plist') from None
        if not isinstance(info, dict):
            raise ValueError('ZIP_PLIST_INVALID: expected plist dictionary')
        for field, expected in (('CFBundleVersion', build), ('CFBundleShortVersionString', short), ('SUFeedURL', FEED)):
            if info.get(field) != expected:
                raise ValueError('ZIP_PLIST_MISMATCH: ' + field)
        embedded = info.get('SUPublicEDKey')
        if not isinstance(embedded, str) or not embedded or public_key(embedded) != key:
            raise ValueError('ZIP_PUBLIC_KEY_MISMATCH: SUPublicEDKey differs from the public pin')


def item_xml(args: argparse.Namespace) -> bytes:
    # Local namespace declaration works with any existing feed's prefix choice.
    item = ET.Element('item', {'xmlns:sparkle': SPARKLE})
    for field, value in (('title', 'HappyRanch ' + args.version), ('sparkle:version', args.build),
                         ('sparkle:shortVersionString', args.version), ('sparkle:minimumSystemVersion', '14.0.0'),
                         ('sparkle:hardwareRequirements', 'arm64'),
                         ('pubDate', format_datetime(datetime.now(timezone.utc), usegmt=True))):
        ET.SubElement(item, field).text = value
    if args.channel:
        ET.SubElement(item, 'sparkle:channel').text = args.channel
    ET.SubElement(item, 'enclosure', {'url': args.url, 'length': args.length,
                                    'type': 'application/octet-stream', 'sparkle:edSignature': args.signature})
    ET.indent(item, space='  ')
    return ET.tostring(item, encoding='utf-8') + b'\n'


def write_new(path: Path, data: bytes) -> None:
    if path.exists() or path.is_symlink():
        raise ValueError('OUTPUT_EXISTS: choose a new output path, never the input')
    pending = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.' + path.name + '.', delete=False) as stream:
            pending = Path(stream.name)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(pending, path)
    finally:
        if pending is not None and pending.exists():
            pending.unlink()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False,
        epilog='Signature SHAPE only: no Ed25519 verification. Before publishing, the founder must run '
               "Sparkle 2.10.1 sign_update --verify FINAL_ZIP SIGNATURE with the pinned signing Keychain account. "
               'No network, upload, signing or notarization occurs here. Internal entries are invisible to shipped builds.')
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--input', type=Path, help='Existing complete UTF-8 RSS 2.0 appcast (all channels)')
    source.add_argument('--bootstrap', action='store_true', help='First feed only; never use to reset build history')
    parser.add_argument('--output', type=Path, required=True, help='New local output path; must not exist')
    parser.add_argument('--archive', type=Path, required=True, help='FINAL signed/notarized/stapled ZIP')
    parser.add_argument('--version', required=True, help='CFBundleShortVersionString')
    parser.add_argument('--build', required=True, help='CFBundleVersion, strictly greater than every feed item')
    parser.add_argument('--public-key', required=True, help='Founder-pinned public SUPublicEDKey (never a private key)')
    parser.add_argument('--signature', required=True, help='sparkle:edSignature printed by sign_update; shape checked only')
    parser.add_argument('--length', required=True, help='ZIP byte length printed by sign_update')
    parser.add_argument('--url', required=True, help='GitHub Releases HTTPS asset download URL')
    parser.add_argument('--channel', choices=['internal'])
    args = parser.parse_args()
    try:
        version(args.version, 'VERSION_INVALID')
        build = version(args.build, 'BUILD_INVALID')
        if not args.public_key:
            raise ValueError('SPARKLE_PUBLIC_KEY_INVALID: empty public pin')
        public_key(args.public_key)
        signature(args.signature)
        download_url(args.url)
        archive_plist(args.archive, args.length, args.version, args.build, args.public_key)
        if args.bootstrap:
            raw = (f'<?xml version="1.0" encoding="utf-8"?>\n<rss version="2.0"><channel>\n'
                   f'<title>HappyRanch</title><link>{FEED}</link><description>HappyRanch Mac updates</description>\n'
                   '</channel></rss>\n').encode()
            offset = raw.index(b'</channel>')
        else:
            raw = args.input.read_bytes()
            offset = existing_feed(raw, build)
        write_new(args.output, raw[:offset] + item_xml(args) + raw[offset:])
    except (OSError, ValueError, OverflowError) as exc:
        print('APPCAST_REFUSED: ' + str(exc), file=sys.stderr)
        return 2
    print('Wrote local appcast: ' + str(args.output) + ' (signature shape only; founder verification required)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
