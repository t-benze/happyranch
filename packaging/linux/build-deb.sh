#!/usr/bin/env bash
# Repackage a PR1/PR2 payload; no root, downloads, payload execution or services.
set -euo pipefail
exec /usr/bin/python3 -I - "$@" <<'PY'
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from email.utils import format_datetime
import gzip
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import posixpath
import re
import shutil
import subprocess
import tarfile
import tempfile

parser = argparse.ArgumentParser(description='Build a desktop .deb from a Linux payload tarball.')
parser.add_argument('payload', type=Path)
parser.add_argument('--dist-dir', type=Path, help='literal absolute output directory (default: beside payload)')
args = parser.parse_args()
if not shutil.which('dpkg-deb'):
    parser.error('dpkg-deb is required')
dist = args.dist_dir if args.dist_dir is not None else args.payload.absolute().parent
if not dist.is_absolute() or dist.resolve() != dist:
    parser.error('--dist-dir must be a literal absolute path')

with tempfile.TemporaryDirectory(prefix='happyranch-deb-', dir='/tmp') as temporary:
    work = Path(temporary)
    with tarfile.open(args.payload, 'r:gz') as archive:
        members = archive.getmembers()
        paths = {}
        roots = set()
        for member in members:
            path = PurePosixPath(member.name)
            if (path.is_absolute() or '..' in path.parts or not path.parts
                    or str(path) in paths or not (member.isdir() or member.isfile() or member.issym())):
                parser.error('payload contains an unsafe or duplicate archive member')
            paths[str(path)] = member
            roots.add(path.parts[0])
        if len(roots) != 1:
            parser.error('payload must contain one top-level directory')
        root = roots.pop()
        for name, member in paths.items():
            if any(str(parent) in paths and not paths[str(parent)].isdir()
                   for parent in PurePosixPath(name).parents if str(parent) != '.'):
                parser.error('payload has a non-directory ancestor')
            if member.issym():
                target = posixpath.normpath(str(PurePosixPath(name).parent / member.linkname))
                if member.linkname.startswith('/') or not target.startswith(root + '/'):
                    parser.error('payload symlink escapes its prefix')
        metadata = paths.get(root + '/build-info.json')
        if metadata is None or not metadata.isfile() or metadata.size > 1024 * 1024:
            parser.error('payload build-info.json is missing or invalid')
        info = json.load(archive.extractfile(metadata))
        architectures = {'x86_64': 'amd64', 'aarch64': 'arm64'}
        if info.get('arch') not in architectures:
            parser.error('unsupported payload architecture')
        arch = architectures[info['arch']]
        versions = [version for name, version in info['dependencies']
                    if name.lower().replace('_', '-') == 'happyranch']
        if len(versions) != 1:
            parser.error('build-info must identify exactly one HappyRanch distribution')
        version = versions[0]
        # Explicit supported PEP 440 subset; do not silently reorder unknown forms.
        match = re.fullmatch(r'([0-9]+(?:\.[0-9]+)*)(?:(a|b|rc)([0-9]+))?'
                             r'(?:\.post([0-9]+))?(?:\.dev([0-9]+))?', version)
        if match is None:
            parser.error('unsupported version; review Debian mapping before release')
        release, pre, pre_number, post, dev = match.groups()
        debversion = release + (f'~{pre}{pre_number}' if pre else '')
        debversion += (f'+post{post}' if post else '') + (f'~dev{dev}' if dev else '') + '-1'
        if root != f'happyranch-{version}-linux-{info["arch"]}':
            parser.error('payload directory disagrees with build-info')
        subprocess.run(['dpkg', '--validate-version', debversion], check=True)
        filename = f'happyranch_{debversion}_{arch}.deb'
        dist.mkdir(parents=True, exist_ok=True)
        if os.path.lexists(dist / filename):
            parser.error('output already exists; choose a fresh destination')
        extracted = work / 'input'
        extracted.mkdir()
        # Never use extractall on supplied paths. Create directories, then regular
        # files, then contained relative links, ignoring archive owners/mode extras.
        for name, member in paths.items():
            if member.isdir():
                (extracted / name).mkdir(parents=True, exist_ok=True)
        for name, member in paths.items():
            destination = extracted / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            if member.isfile():
                with archive.extractfile(member) as source, destination.open('xb') as target:
                    shutil.copyfileobj(source, target)
                destination.chmod(0o755 if member.mode & 0o111 else 0o644)
            elif member.issym():
                destination.symlink_to(member.linkname)

    payload = extracted / root
    for path in payload.rglob('*'):
        if path.is_symlink() and not path.resolve(strict=True).is_relative_to(payload):
            parser.error('payload symlink escapes its prefix')
    for required in ('bin/happyranch', 'bin/happyranch-daemon', 'bin/happyranch-app',
                     'python/bin/python3', 'web/dist/index.html',
                     'share/happyranch/desktop/happyranch_window.py',
                     'share/applications/happyranch.desktop.in', 'licenses/HappyRanch-LICENSE'):
        if not (payload / required).is_file():
            parser.error('incomplete desktop payload: ' + required)

    stage = work / 'package'
    prefix = stage / 'opt/happyranch-desktop'
    prefix.parent.mkdir(parents=True)
    shutil.move(payload, prefix)
    # Regenerable build-time bytecode is not part of the installed application.
    for cache in prefix.rglob('__pycache__'):
        if cache.is_dir() and not cache.is_symlink():
            shutil.rmtree(cache)
    for library in (prefix / 'python/lib').rglob('*'):
        if library.is_file() and not library.is_symlink() and (
                library.name.endswith('.so') or '.so.' in library.name):
            library.chmod(0o644)
    bindir = stage / 'usr/bin'
    bindir.mkdir(parents=True)
    for launcher in ('happyranch', 'happyranch-app'):
        wrapper = bindir / launcher
        # Forward using the real absolute name: payload launchers derive PREFIX
        # from $0 and cannot be invoked through a /usr/bin symlink.
        wrapper.write_text(f'#!/bin/sh\nexec /opt/happyranch-desktop/bin/{launcher} "$@"\n')
        wrapper.chmod(0o755)
    applications = stage / 'usr/share/applications'
    applications.mkdir(parents=True)
    template = (prefix / 'share/applications/happyranch.desktop.in').read_text()
    desktop = template.replace('@APP_EXEC@', '/usr/bin/happyranch-app')
    if '@' in desktop or 'Icon=happyranch\n' not in desktop:
        parser.error('unexpected desktop template; review packaging substitutions')
    (applications / 'happyranch.desktop').write_text(desktop)
    icons = prefix / 'share/icons/hicolor'
    for size in ('16', '32', '64', '128', '256', '512', '1024'):
        if not (icons / f'{size}x{size}/apps/happyranch.png').is_file():
            parser.error('missing hicolor icon: ' + size)
    if not (icons / 'scalable/apps/happyranch.svg').is_file():
        parser.error('missing scalable hicolor icon')
    shutil.copytree(icons, stage / 'usr/share/icons/hicolor', symlinks=True)
    doc = stage / 'usr/share/doc/happyranch'
    doc.mkdir(parents=True)
    shutil.copytree(prefix / 'licenses', doc / 'licenses', symlinks=True)
    (doc / 'copyright').write_text(
        'HappyRanch\nSource: https://github.com/t-benze/happyranch\n\n'
        'Copyright: see the original upstream and dependency notices in licenses/.\n'
        'License: Apache-2.0\n'
        ' HappyRanch is distributed under the Apache License, Version 2.0.\n'
        ' On Debian systems the full text is in /usr/share/common-licenses/Apache-2.0.\n'
        ' The original upstream license is also preserved in licenses/HappyRanch-LICENSE.\n\n'
        'Bundled dependencies retain their respective copyrights and licenses;\n'
        'see licenses/ for the unchanged PR1 notices and inventories.\n'
    )
    changelog = (f'happyranch ({debversion}) unstable; urgency=medium\n\n'
                 '  * Package the relocatable Linux desktop payload.\n\n'
                 ' -- HappyRanch maintainers <noreply@github.com>  '
                 + format_datetime(datetime.now(timezone.utc)) + '\n')
    (doc / 'changelog.Debian.gz').write_bytes(gzip.compress(changelog.encode(), mtime=0))
    shutil.copyfile(prefix / 'build-info.json', doc / 'build-info.json')
    for path in doc.rglob('*'):
        if path.is_file() and not path.is_symlink():
            path.chmod(0o644)
    control = stage / 'DEBIAN'
    control.mkdir()
    # GNU du reports allocated KiB, as required for Installed-Size, before the
    # control archive is added. Ownership is supplied by dpkg-deb, never chown.
    installed_size = subprocess.check_output(['du', '-sk', str(stage)], text=True).split()[0]
    (control / 'control').write_text(
        f'Package: happyranch\nVersion: {debversion}\nArchitecture: {arch}\n'
        'Maintainer: HappyRanch maintainers <noreply@github.com>\n'
        'Section: devel\nPriority: optional\n'
        'Homepage: https://github.com/t-benze/happyranch\n'
        f'Installed-Size: {installed_size}\n'
        'Depends: python3 (>= 3.10), python3-gi, gir1.2-gtk-3.0, gir1.2-webkit2-4.1, '
        'git, ca-certificates, libc6 (>= 2.35), libgcc-s1\n'
        'Description: desktop and command-line workspace\n'
        ' Native GTK and WebKit window for the local HappyRanch web interface,\n'
        ' with a private Python runtime and an application-owned local daemon.\n'
    )
    # Package readability must not depend on the caller's umask (e.g. 077).
    stage.chmod(0o755)
    for path in stage.rglob('*'):
        if not path.is_symlink():
            path.chmod(0o755 if path.is_dir() or path.stat().st_mode & 0o111 else 0o644)
    files = sorted(path for path in stage.rglob('*')
                   if path.is_file() and not path.is_symlink() and not path.is_relative_to(control))
    (control / 'md5sums').write_text(''.join(
        hashlib.md5(path.read_bytes()).hexdigest() + '  ' + str(path.relative_to(stage)) + '\n'
        for path in files))
    (control / 'md5sums').chmod(0o644)
    # No maintainer scripts: desktop-file-utils/hicolor-icon-theme own triggers.
    built = work / filename
    subprocess.run(['dpkg-deb', '--root-owner-group', '--build', str(stage), str(built)], check=True)
    with built.open('rb') as source, (dist / filename).open('xb') as target:
        shutil.copyfileobj(source, target)
    print(dist / filename)
PY
