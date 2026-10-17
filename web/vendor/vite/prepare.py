#!/usr/bin/env python3
"""Offline, deterministic preparation from the exact original Vite archive."""
import argparse
import difflib
import gzip
import hashlib
import io
import json
from pathlib import Path
import re
import tarfile

ORIGINAL = '1974803337cb6a1a86a6fdcf45a05ff0555220140d5ca70a58306f9f9b895af6'
CHUNK = 'package/dist/node/chunks/dep-BK3b2jBa.js'
VERSION = '5.4.21+hr.css-relation.1'
BASE = Path(__file__).parent

def digest(raw):
    return hashlib.sha256(raw).hexdigest()

def prepare(original):
    raw = original.read_bytes()
    if digest(raw) != ORIGINAL:
        raise ValueError('VITE_ORIGINAL_IDENTITY_MISMATCH')
    members = {}
    with tarfile.open(fileobj=io.BytesIO(raw), mode='r:gz') as archive:
        for row in archive:
            if not row.isfile() or row.name in members or not row.name.startswith('package/') or '..' in row.name.split('/') or row.size > 32*1024**2:
                raise ValueError('VITE_ORIGINAL_MEMBER_INVALID')
            members[row.name] = (archive.extractfile(row).read(), row.mode)
    original_members = dict(members)
    text = members[CHUNK][0].decode()
    lines = text.splitlines(True)
    patch = (BASE / 'css-relation.patch').read_text().splitlines(True)
    output, cursor, index = [], 0, 2
    while index < len(patch):
        match = re.fullmatch(r'@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@.*\n', patch[index])
        if not match:
            raise ValueError('VITE_PATCH_HUNK_INVALID')
        start = int(match[1])-1
        if start < cursor:
            raise ValueError('VITE_PATCH_OVERLAP')
        output.extend(lines[cursor:start]);cursor = start; index += 1
        while index < len(patch) and not patch[index].startswith('@@'):
            line = patch[index];index += 1
            if line[0] in ' -':
                if cursor >= len(lines) or lines[cursor] != line[1:]:
                    raise ValueError('VITE_PATCH_PREIMAGE_MISMATCH')
                cursor += 1
            if line[0] in ' +':
                output.append(line[1:])
    output.extend(lines[cursor:])
    members[CHUNK] = (''.join(output).encode(), members[CHUNK][1])
    metadata = json.loads(members['package/package.json'][0]);metadata['version'] = VERSION
    members['package/package.json'] = ((json.dumps(metadata, indent=2)+'\n').encode(), members['package/package.json'][1])
    tar = io.BytesIO()
    with tarfile.open(fileobj=tar, mode='w', format=tarfile.USTAR_FORMAT) as archive:
        for name, (body, mode) in sorted(members.items()):
            row = tarfile.TarInfo(name);row.size=len(body);row.mode=mode;row.mtime=0;row.uid=row.gid=0;row.uname=row.gname=''
            archive.addfile(row, io.BytesIO(body))
    result = io.BytesIO()
    with gzip.GzipFile(fileobj=result, mode='wb', filename='', mtime=0, compresslevel=9) as stream:
        stream.write(tar.getvalue())
    return result.getvalue(), members, original_members

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--original',type=Path,required=True);parser.add_argument('--check',action='store_true');args=parser.parse_args()
    raw,members,old=prepare(args.original)
    delta=[{'path':name,'original_sha256':digest(old[name][0]),'sha256':digest(body),'size':len(body),'mode':mode} for name,(body,mode) in sorted(members.items()) if old[name] != (body,mode)]
    if {r['path'] for r in delta} != {CHUNK,'package/package.json'}:
        raise ValueError('VITE_MEMBER_DELTA_INVALID')
    manifest={'original_sha256':ORIGINAL,'version':VERSION,'patch_sha256':digest((BASE/'css-relation.patch').read_bytes()),'archive_sha256':digest(raw),'archive_size':len(raw),'integrity':'sha512-'+__import__('base64').b64encode(hashlib.sha512(raw).digest()).decode(),'member_delta':delta,'members':[{'path':n,'size':len(b),'sha256':digest(b),'mode':m} for n,(b,m) in sorted(members.items())]}
    targets={BASE/f'vite-{VERSION}.tgz':raw,BASE/'LICENSE.md':old['package/LICENSE.md'][0],BASE/'UPSTREAM.json':(json.dumps(manifest,indent=2)+'\n').encode()}
    for path,body in targets.items():
        if path.exists():
            if path.read_bytes()!=body:
                raise ValueError('VITE_EXISTING_OUTPUT_CONFLICT: '+str(path))
        elif args.check:
            raise ValueError('VITE_PREPARED_OUTPUT_MISSING: '+str(path))
        else:
            with path.open('xb') as stream:stream.write(body)
    print(json.dumps({k:manifest[k] for k in ['archive_sha256','archive_size','integrity','member_delta']}))

if __name__=='__main__':main()
