#!/usr/bin/env python3
"""Inert original-wheel repack; stdlib + Git only, no build backend/imports."""
import argparse
import contextlib
import base64
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import zipfile

ORIGINAL = 'pyinstaller-6.21.0-py3-none-macosx_10_13_universal2.whl'
REPLACEMENT = 'pyinstaller-6.21.0-1happyranchcustody1-py3-none-macosx_10_13_universal2.whl'
ORIGINAL_SHA = '327d132389f37912609e01be62810cf96b5aa95b613903e4b8692e0d12fb0eda'
NOTICE_SHA = 'dcf75fdb959db1e3b41c0f8505069d2ece781b5ec6b3d0a4d30975cfc6580245'
SOURCES = ('PyInstaller/building/api.py','PyInstaller/building/utils.py')
INFO = 'pyinstaller-6.21.0.dist-info/'
CHANGED = set(SOURCES) | {INFO + k for k in ('METADATA','WHEEL','RECORD')}
CAP = 64 * 1024**2


def need(ok, code):
    if not ok:
        raise ValueError('PYINSTALLER_CUSTODY_' + code)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def safe_bytes(path, cap=CAP):
    path = Path(path).absolute()
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
    held = []
    try:
        fd = os.open('/',flags | os.O_DIRECTORY); held.append(fd)
        edges = []
        for i,part in enumerate(path.parts[1:]):
            last = i == len(path.parts)-2
            child = os.open(part, flags | (0 if last else os.O_DIRECTORY), dir_fd=fd)
            held.append(child)
            s = os.fstat(child)
            need(s.st_uid in (0,os.getuid()) and not s.st_mode & 0o022, 'INPUT_OWNER_MODE')
            need((stat.S_ISREG(s.st_mode) and s.st_nlink == 1 and s.st_size <= cap) if last else stat.S_ISDIR(s.st_mode), 'INPUT_KIND_OR_CAP')
            identity = (s.st_dev,s.st_ino,s.st_mode,s.st_uid) if not last else (s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
            edges.append((fd,part,child,identity,last)); fd=child
        raw=bytearray()
        while True:
            chunk=os.read(fd,min(65536,cap-len(raw)+1))
            if not chunk: break
            raw.extend(chunk); need(len(raw)<=cap,'INPUT_CAP')
        need(len(raw)==os.fstat(fd).st_size,'INPUT_SIZE')
        for parent,part,child,old,last in edges:
            for s in (os.fstat(child),os.stat(part,dir_fd=parent,follow_symlinks=False)):
                now=(s.st_dev,s.st_ino,s.st_mode,s.st_uid) if not last else (s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
                need(old==now,'INPUT_UNSTABLE')
        return bytes(raw)
    finally:
        for fd in reversed(held): os.close(fd)



@contextlib.contextmanager
def directory(path):
    path=Path(path).absolute()
    fds,edges=[],[]
    try:
        fd=os.open('/',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC); fds.append(fd)
        for part in path.parts[1:]:
            need(part not in ('','.','..'),'OUTPUT_PATH')
            child=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=fd)
            fds.append(child); info=os.fstat(child)
            need(info.st_uid in (0,os.getuid()) and not info.st_mode & 0o022,'OUTPUT_OWNER_MODE')
            edges.append((fd,part,child,(info.st_dev,info.st_ino,info.st_mode,info.st_uid))); fd=child
        yield fd
        for parent,part,child,old in edges:
            for info in (os.fstat(child),os.stat(part,dir_fd=parent,follow_symlinks=False)):
                need(old==(info.st_dev,info.st_ino,info.st_mode,info.st_uid),'OUTPUT_ANCESTRY_CHANGED')
    finally:
        for fd in reversed(fds): os.close(fd)


def archive(raw):
    result, metadata, total = {}, {}, 0
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        need(len(z.infolist()) == 566, 'INVENTORY_COUNT')
        for entry in z.infolist():
            p = entry.filename
            need(p not in result and not p.startswith('/') and all(x not in ('','.','..') for x in p.split('/')),
                 'MEMBER_NAME')
            mode = entry.external_attr >> 16
            need(stat.S_IFMT(mode) in (0,stat.S_IFREG) and not entry.flag_bits & 1 and entry.file_size <= 16*1024**2, 'MEMBER_KIND')
            total += entry.file_size; need(total <= CAP,'EXPANDED_CAP')
            with z.open(entry) as stream:
                value=stream.read(entry.file_size+1)
                need(len(value)==entry.file_size,'MEMBER_SIZE')
            result[p] = value
            metadata[p] = entry.external_attr
    rows = list(csv.reader(io.StringIO(result[INFO+'RECORD'].decode('utf-8'))))
    need(len(rows)==len(result),'RECORD_COUNT')
    seen=set()
    for row in rows:
        need(len(row)==3,'RECORD_ROW')
        p,h,n=row
        need(p not in seen and p in result,'RECORD_MEMBER'); seen.add(p)
        if p == INFO+'RECORD': need(h==n=='','RECORD_SELF'); continue
        expected='sha256='+base64.urlsafe_b64encode(hashlib.sha256(result[p]).digest()).rstrip(b'=').decode()
        need(h==expected and n==str(len(result[p])),'RECORD_DIGEST')
    need(seen==set(result),'RECORD_INVENTORY')
    return result,metadata


def inventory(members, modes):
    return {p:{'size':len(v),'sha256':sha(v),'external_attr':modes[p]} for p,v in sorted(members.items())}


def apply(base, patch, output):
    with directory(output.parent) as parent:
        os.mkdir(output.name,0o700,dir_fd=parent)
    # Exactly two existing source paths; no mode/rename/delete/new-file metadata.
    text=patch.decode('utf-8')
    names=[line.removeprefix('+++ b/') for line in text.splitlines() if line.startswith('+++ b/')]
    need(set(names)==set(SOURCES) and len(names)==2 and sum(line.startswith('--- a/') for line in text.splitlines())==2,
         'PATCH_PATHS')
    need(not any(line.startswith(('new file mode','deleted file mode','old mode','new mode','rename ','copy ','GIT binary patch'))
                 for line in text.splitlines()),'PATCH_OPERATION')
    for part in ('PyInstaller','PyInstaller/building'):
        (output/part).mkdir(mode=0o700)
    for member in SOURCES:
        path=output/member
        fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'wb') as stream: stream.write(base[member])
    patchfile=output/'custody.patch'
    fd=os.open(patchfile,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'wb') as stream: stream.write(patch)
    env={k:v for k,v in os.environ.items() if not k.startswith('GIT_')}
    env.update(GIT_CONFIG_NOSYSTEM='1',GIT_CONFIG_GLOBAL=os.devnull,GIT_CEILING_DIRECTORIES=str(output.parent))
    previous_umask=os.umask(0o077)
    try:
        for opts in (['--check'],[]):
            subprocess.run(['git','apply',*opts,'--whitespace=error-all',str(patchfile)],cwd=output,env=env,check=True)
    finally:
        os.umask(previous_umask)
    return {member:safe_bytes(output/member) for member in SOURCES}


def build(original, patch, stage):
    need(len(original)==1052350 and sha(original)==ORIGINAL_SHA,'ORIGINAL_DIGEST')
    base,modes=archive(original)
    need(sha(base[INFO+'licenses/COPYING.txt'])==NOTICE_SHA,'ORIGINAL_NOTICE')
    new=dict(base); new.update(apply(base,patch,stage))
    meta=base[INFO+'METADATA']
    need(b'X-HappyRanch-Custody-Revision:' not in meta,'ORIGINAL_METADATA')
    new[INFO+'METADATA']=meta.replace(b'\n\n',b'\nX-HappyRanch-Custody-Revision: 1\n\n',1)
    new[INFO+'WHEEL']=base[INFO+'WHEEL']+b'Build: 1happyranchcustody1\n'
    record=io.StringIO(newline=''); writer=csv.writer(record,lineterminator='\n')
    for member in sorted(new):
        raw=new[member]
        writer.writerow([member,'',''] if member==INFO+'RECORD' else
                        [member,'sha256='+base64.urlsafe_b64encode(hashlib.sha256(raw).digest()).rstrip(b'=').decode(),str(len(raw))])
    new[INFO+'RECORD']=record.getvalue().encode()
    need({p for p in base if base[p]!=new[p]}==CHANGED,'CHANGED_ALLOWLIST')
    result=io.BytesIO()
    with zipfile.ZipFile(result,'w',compression=zipfile.ZIP_STORED,allowZip64=False) as z:
        for member in sorted(new):
            info=zipfile.ZipInfo(member,(1980,1,1,0,0,0)); info.create_system=3
            info.external_attr=modes[member]; info.compress_type=zipfile.ZIP_STORED
            info.extra=b''; info.comment=b''
            z.writestr(info,new[member])
    raw=result.getvalue()
    actual,attrs=archive(raw)
    need(actual==new and attrs==modes,'REOPEN_IDENTITY')
    return raw,inventory(new,modes),inventory(base,modes)


def expectations(root):
    raw=safe_bytes(root/'provenance.json',256*1024)
    def pairs(items):
        result={}
        for k,v in items:
            need(k not in result,'PROVENANCE_DUPLICATE'); result[k]=v
        return result
    value=json.loads(raw,object_pairs_hook=pairs)
    need(set(value)=={'encoding','original','notice','patch','preparer','sources','replacement','members','original_members','format'},'PROVENANCE_SCHEMA')
    for key,file in (('patch','custody.patch'),('preparer','prepare.py')):
        data=safe_bytes(root/file)
        need(value[key]=={'size':len(data),'sha256':sha(data)},'EXPECTED_'+key.upper())
    notice=safe_bytes(root/'original/COPYING.txt',32768)
    need(len(notice)==32138 and sha(notice)==NOTICE_SHA,'NOTICE_DIGEST')
    return value


def main():
    parser=argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument('command',choices=('prepare','verify'))
    parser.add_argument('--output-dir',type=Path)
    args=parser.parse_args()
    root=Path(__file__).absolute().parent
    expected=expectations(root)
    original=safe_bytes(root/'original'/ORIGINAL)
    patch=safe_bytes(root/'custody.patch')
    if args.command=='prepare':
        need(args.output_dir is not None and args.output_dir.is_absolute(),'ABSENT_OUTPUT_REQUIRED')
        output=args.output_dir
        with directory(output.parent) as parent:
            os.mkdir(output.name,0o700,dir_fd=parent)
        raw,members,original_members=build(original,patch,output/'sources')
        need(members==expected['members'] and original_members==expected['original_members'],'EXPECTED_MEMBER_IDENTITIES')
        need({'size':len(raw),'sha256':sha(raw)}==expected['replacement'],'EXPECTED_REPLACEMENT')
        with (output/REPLACEMENT).open('xb') as stream: stream.write(raw)
        with (output/'inventory.json').open('xb') as stream:
            stream.write((json.dumps(members,sort_keys=True,separators=(',',':'))+'\n').encode())
    else:
        raw=safe_bytes(root/'dist'/REPLACEMENT)
        members,modes=archive(raw); base,original_modes=archive(original)
        need(set(members)==set(base) and modes==original_modes and {p for p in base if base[p]!=members[p]}==CHANGED,'WHEEL_ALLOWLIST')
        need(inventory(members,modes)==expected['members'] and inventory(base,original_modes)==expected['original_members'],'EXPECTED_MEMBER_IDENTITIES')
        need({'size':len(raw),'sha256':sha(raw)}==expected['replacement'],'EXPECTED_REPLACEMENT')
    print(json.dumps({'size':len(raw),'sha256':sha(raw),'members':len(members),'scope':'source_repack_only'}))


if __name__=='__main__': main()
