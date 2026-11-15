"""Portable appcast CLI contracts with synthetic ZIPs and signature placeholders."""
from __future__ import annotations

import base64
from email.utils import parsedate_to_datetime
import os
from pathlib import Path
import plistlib
import stat
import subprocess
import sys
import tempfile
import unittest
import warnings
from xml.etree import ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / 'app/mac/scripts/appcast_entry.py'
NS = '{http://www.andymatuschak.org/xml-namespaces/sparkle}'
FEED = 'https://app.happyranch.ai/mac/appcast.xml'
KEY = base64.b64encode(bytes(range(32))).decode()
URL = 'https://github.com/t-benze/happyranch/releases/download/v1.0.10/HappyRanch.zip'


class AppcastEntryTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix='appcast-entry-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.archive = self.root / 'final.zip'
        self.source = self.root / 'appcast.xml'
        self.output = self.root / 'new.xml'
        # Random bytes are signature-shaped placeholders, never signing keys.
        self.signature = base64.b64encode(os.urandom(64)).decode()
        self.info = dict(CFBundleVersion='1.0.10', CFBundleShortVersionString='1.0.10',
                         SUFeedURL=FEED, SUPublicEDKey=KEY)
        self.write_zip()
        self.feed('1.0.9')

    def write_zip(self, *, payload: bytes | None = None, member: str = 'HappyRanch.app/Contents/Info.plist') -> None:
        with zipfile.ZipFile(self.archive, 'w') as zipped:
            zipped.writestr(member, plistlib.dumps(self.info) if payload is None else payload)
            zipped.writestr('HappyRanch.app/Contents/MacOS/HappyRanch', b'synthetic sample, no native code')
        self.length = str(self.archive.stat().st_size)

    def feed(self, *builds: str, legacy: bool = False) -> bytes:
        items = []
        for number, build in enumerate(builds):
            fields = f'<s:version>{build}</s:version><s:shortVersionString>1.0.0</s:shortVersionString>'
            attrs = ''
            if legacy:
                attrs = f' s:version="{build}" s:shortVersionString="1.0.0"'
                fields = ''
            items.append(f'<item data-order="{number}"><title><![CDATA[kept & unchanged]]></title>{fields}'
                         f'<s:channel>internal</s:channel><enclosure url="{URL}" length="123" '
                         f'type="application/octet-stream" s:edSignature="{self.signature}"{attrs}/></item>')
        raw = ('<?xml version="1.0" encoding="UTF-8"?>\r\n'
               '<rss xmlns:s="http://www.andymatuschak.org/xml-namespaces/sparkle" version="2.0">\r\n'
               '<channel><title>HappyRanch — retained</title><!--preserve this too-->\r\n'
               + '\r\n'.join(items) + '\r\n</channel></rss>\r\n').encode()
        self.source.write_bytes(raw)
        return raw

    def run_cli(self, *, bootstrap: bool = False, **overrides: str | Path | None) -> subprocess.CompletedProcess:
        args = dict(output=self.output, archive=self.archive, version='1.0.10', build='1.0.10',
                    public_key=KEY, signature=self.signature, length=self.length, url=URL)
        args.update(overrides)
        argv = [sys.executable, '-B', str(TOOL)]
        argv += ['--bootstrap'] if bootstrap else ['--input', str(self.source)]
        for name, value in args.items():
            if value is not None:
                argv += ['--' + name.replace('_', '-'), str(value)]
        return subprocess.run(argv, text=True, capture_output=True, timeout=10)

    def refused(self, diagnostic: str, **overrides: str | Path | None) -> None:
        before = self.source.read_bytes()
        result = self.run_cli(**overrides)
        try:
            self.assertNotEqual(result.returncode, 0, result.stdout)
            self.assertIn(diagnostic, result.stderr)
            self.assertFalse(self.output.exists(), result.stdout)
            self.assertEqual(self.source.read_bytes(), before)
            self.assertEqual(list(self.root.glob('.new.xml.*')), [])
        finally:
            # Keep table cases independent even when a regression writes output.
            if self.output.is_file():
                self.output.unlink()

    def test_bootstrap_and_channel(self) -> None:
        self.source.unlink()  # Bootstrap really needs no existing feed.
        for channel in (None, 'internal'):
            with self.subTest(channel=channel):
                self.output = self.root / f'bootstrap-{channel}.xml'
                result = self.run_cli(bootstrap=True, channel=channel)
                self.assertEqual(result.returncode, 0, result.stderr)
                item = ET.parse(self.output).getroot().find('channel/item')
                self.assertIsNotNone(item)
                for field, expected in (('version', '1.0.10'), ('shortVersionString', '1.0.10'),
                                        ('minimumSystemVersion', '14.0.0'), ('hardwareRequirements', 'arm64'),
                                        ('channel', channel)):
                    self.assertEqual(item.findtext(NS + field), expected)
                self.assertIsNotNone(parsedate_to_datetime(item.findtext('pubDate')).tzinfo)
                self.assertEqual(item.find('enclosure').attrib,
                                 dict(url=URL, length=self.length, type='application/octet-stream',
                                      **{NS + 'edSignature': self.signature}))
                self.output.unlink()
        help_result = subprocess.run([sys.executable, '-B', str(TOOL), '--help'], capture_output=True, text=True)
        self.assertEqual(help_result.returncode, 0)
        self.assertIn('no Ed25519 verification', help_result.stdout)

    def test_existing_items_preserved(self) -> None:
        for legacy in (False, True):
            with self.subTest(legacy=legacy):
                self.output = self.root / f'preserved-{legacy}.xml'
                original = self.feed('1.0.8', '1.0.9', '0.9.99', legacy=legacy)
                result = self.run_cli()
                self.assertEqual(result.returncode, 0, result.stderr)
                actual = self.output.read_bytes()
                offset = original.index(b'<item data-order=')
                self.assertTrue(actual.startswith(original[:offset]))
                self.assertTrue(actual.endswith(original[offset:]))
                self.assertEqual(self.source.read_bytes(), original)
                items = ET.fromstring(actual).findall('channel/item')
                self.assertEqual(len(items), 4)
                self.assertEqual([item.get('data-order') for item in items[1:]], ['0', '1', '2'])
                self.assertEqual(items[0].findtext(NS + 'version'), '1.0.10')
                self.output.unlink()

    def test_monotonic_refusals(self) -> None:
        cases = [(('1.0.10',), 'BUILD_REUSED'), (('1.0.11',), 'BUILD_NOT_MONOTONIC'),
                 (('1.0.8', '1.0.10', '1.0.9'), 'BUILD_REUSED'),
                 (('1.0.9', '1.1.0'), 'BUILD_NOT_MONOTONIC'), (('2.0.0',), 'BUILD_NOT_MONOTONIC')]
        for builds, diagnostic in cases:
            with self.subTest(builds=builds):
                self.feed(*builds)
                self.refused(diagnostic)

    def test_version_grammar_refusals(self) -> None:
        for field in ('build', 'version'):
            for value in ('1', '1.2', '01.0.10', '1.00.10', '1.0.010', '1.0.10-beta', '1.0.10 ', '-1.0.10'):
                with self.subTest(field=field, value=value):
                    self.refused(field.upper() + '_INVALID', **{field: value})

    def test_length_and_signature_refusals(self) -> None:
        for length in ('0', '-1', '01', 'not-length', str(int(self.length) + 1)):
            with self.subTest(length=length):
                self.refused('LENGTH_INVALID' if not length.isdecimal() or length.startswith('0') else 'ZIP_LENGTH_MISMATCH', length=length)
        for value in ('', '!', self.signature.rstrip('='), self.signature + '\n',
                      base64.b64encode(os.urandom(63)).decode(), base64.b64encode(os.urandom(65)).decode(),
                      'A' * 85 + 'B=='):  # Valid decoded size, noncanonical pad bits.
            with self.subTest(signature=value):
                self.refused('SIGNATURE_SHAPE_INVALID', signature=value)
        with self.archive.open('ab') as stream:
            stream.write(b'changed after length was recorded')
        self.refused('ZIP_LENGTH_MISMATCH')

    def test_plist_and_key_refusals(self) -> None:
        original = dict(self.info)
        cases = [('CFBundleVersion', '1.0.9', 'ZIP_PLIST_MISMATCH'),
                 ('CFBundleShortVersionString', '1.0.9', 'ZIP_PLIST_MISMATCH'),
                 ('SUFeedURL', 'https://example.invalid/feed', 'ZIP_PLIST_MISMATCH'),
                 ('SUPublicEDKey', base64.b64encode(b'x' * 32).decode(), 'ZIP_PUBLIC_KEY_MISMATCH'),
                 ('SUPublicEDKey', 'invalid', 'SPARKLE_PUBLIC_KEY_INVALID'),
                 ('SUPublicEDKey', '', 'ZIP_PUBLIC_KEY_MISMATCH'),
                 ('SUPublicEDKey', None, 'ZIP_PUBLIC_KEY_MISMATCH')]
        for field, value, diagnostic in cases:
            with self.subTest(field=field, value=value):
                self.info = {**original, field: value}
                if value is None:
                    del self.info[field]
                self.write_zip()
                self.refused(diagnostic)
        self.info = original
        self.write_zip()
        for key in ('', 'bad', base64.b64encode(os.urandom(31)).decode()):
            with self.subTest(public_pin=key):
                self.refused('SPARKLE_PUBLIC_KEY_INVALID', public_key=key)
        for payload in (b'invalid plist', b'<plist><dict>', plistlib.dumps(['not a dictionary'])):
            with self.subTest(payload=payload):
                self.write_zip(payload=payload)
                self.refused('ZIP_PLIST_INVALID')
        for kind in ('duplicate', 'symlink'):
            with self.subTest(plist_member=kind):
                self.write_zip()
                if kind == 'duplicate':
                    with zipfile.ZipFile(self.archive, 'a') as zipped:
                        with warnings.catch_warnings():
                            warnings.simplefilter('ignore', UserWarning)
                            zipped.writestr('HappyRanch.app/Contents/Info.plist', plistlib.dumps(self.info))
                else:
                    with zipfile.ZipFile(self.archive, 'w') as zipped:
                        member = zipfile.ZipInfo('HappyRanch.app/Contents/Info.plist')
                        member.create_system = 3
                        member.external_attr = (stat.S_IFLNK | 0o777) << 16
                        zipped.writestr(member, plistlib.dumps(self.info))
                self.length = str(self.archive.stat().st_size)
                self.refused('ZIP_PLIST_INVALID')
        self.write_zip(member='Other.app/Contents/Info.plist')
        self.refused('ZIP_PLIST_INVALID')
        self.archive.write_bytes(b'not a ZIP')
        self.length = str(self.archive.stat().st_size)
        self.refused('ZIP_PLIST_INVALID')

    def test_url_refusals(self) -> None:
        for url in ('http://github.com/o/r/releases/download/v/a.zip', 'https://example.invalid/o/r/releases/download/v/a.zip',
                    'https://github.com.evil.invalid/o/r/releases/download/v/a.zip',
                    'https://user@github.com/o/r/releases/download/v/a.zip',
                    'https://github.com/o/r/archive/v.zip', 'https://github.com/o/r/releases/download/',
                    URL + '?token=placeholder', URL + '#fragment',
                    'https://github.com/o/r/releases/download/../a.zip',
                    'https://github.com/o/r/releases/download/%2e%2e/a.zip',
                    'https://github.com/o/r/releases/download/v/a.zip\n'):
            with self.subTest(url=url):
                self.refused('DOWNLOAD_URL_INVALID', url=url)

    def test_feed_refusals(self) -> None:
        original = self.source.read_bytes()
        cases = [(b'<rss>', 'FEED_INVALID'), (original.replace(b'version="2.0"', b'version="1.0"'), 'FEED_INVALID'),
                 (original.replace(b'1.0.9', b'1.0'), 'FEED_VERSION_INVALID'),
                 (original.replace(b'<s:version>1.0.9</s:version>', b''), 'FEED_VERSION_INVALID'),
                 (original.replace(b'<s:shortVersionString>1.0.0', b'<s:shortVersionString>bad'), 'FEED_VERSION_INVALID'),
                 (original.replace(b'<s:version>1.0.9</s:version>', b'<s:version>1.0.9</s:version>' * 2), 'FEED_VERSION_INVALID'),
                 (b'<!DOCTYPE rss [<!ENTITY x "1.0.9">]>' + original[original.index(b'<rss'):], 'FEED_INVALID'),
                 (b'<rss version="2.0"><channel/></rss>', 'FEED_INVALID')]
        for raw, diagnostic in cases:
            with self.subTest(raw=raw):
                self.source.write_bytes(raw)
                self.refused(diagnostic)
        self.feed('1.0.9', '1.0.9')
        self.refused('FEED_DUPLICATE_VERSION')

    def test_output_refusals(self) -> None:
        original = self.source.read_bytes()
        result = self.run_cli(output=self.source)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('OUTPUT_EXISTS', result.stderr)
        self.assertEqual(self.source.read_bytes(), original)
        self.output.write_bytes(b'keep existing output')
        result = self.run_cli()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('OUTPUT_EXISTS', result.stderr)
        self.assertEqual(self.output.read_bytes(), b'keep existing output')
        self.output.unlink()
        self.output.symlink_to(self.root / 'absent')
        result = self.run_cli()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('OUTPUT_EXISTS', result.stderr)
        self.assertTrue(self.output.is_symlink())


if __name__ == '__main__':
    unittest.main()
