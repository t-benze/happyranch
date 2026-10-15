#!/usr/bin/env python3
"""Private THR307 fixtures; invoked ONLY by verify_disk_sweeper's unshare wrapper.

Fresh mapped-user/mount/PID namespace, private propagation and private procfs.
No host mounts, sudo, capabilities/provisioning changes or whole-driver root run.
"""
from __future__ import annotations

import ctypes
import errno
import json
import os
from pathlib import Path
import select
import shutil
import subprocess
import sys
import tempfile
from unittest.mock import patch

from verify_disk_sweeper import (
    Fixture, candidate_contents, ds, refuse_mutations, retained, row,
)


LIBC = ctypes.CDLL(None, use_errno=True)
LIBC.mount.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_ulong, ctypes.c_char_p]
LIBC.umount2.argtypes = [ctypes.c_char_p, ctypes.c_int]


def syscall(result):
    if result != 0:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code))


def mount_id(path):
    # Fixture mkdtemp paths contain no mountinfo escapes/spaces.
    rows = [line.split()[0] for line in Path('/proc/self/mountinfo').read_text().splitlines() if line.split()[4] == str(path)]
    assert len(rows) <= 1, rows
    return rows[0] if rows else None


class Disposable:
    def __enter__(self):
        self.base = Path(tempfile.mkdtemp(prefix='disk-sweep-isolated-'))
        self.f = Fixture(self.base)
        self.mounts = []
        return self

    def mount(self, source, target, *, tmpfs=False):
        assert target.is_relative_to(self.base) and not target.is_symlink()
        assert mount_id(target) is None, 'preexisting mount'
        if tmpfs:
            syscall(LIBC.mount(b'tmpfs', os.fsencode(target), b'tmpfs', 6, b'size=1m,nr_inodes=128'))
        else:
            assert source.is_relative_to(self.base) and not source.is_symlink()
            syscall(LIBC.mount(os.fsencode(source), os.fsencode(target), None, 4096, None))  # MS_BIND
        ident = mount_id(target)
        # Record ownership before any assertion; cleanup never guesses a path.
        self.mounts.append((target, ident))
        assert ident is not None

    def __exit__(self, *exc):
        try:
            for target, ident in reversed(self.mounts):
                assert ident is not None and mount_id(target) == ident, 'mount ownership changed'
                syscall(LIBC.umount2(os.fsencode(target), 0))  # No force/lazy unmount.
                assert mount_id(target) is None
            self.mounts.clear()
        finally:
            self.f.db.close()
        # Reached only after all exact owned mounts were unmounted. A cleanup
        # failure leaves the tree for namespace teardown, never follows a bind.
        assert not self.mounts
        def writable(path):
            if path.is_symlink(): return
            if path.is_dir():
                path.chmod(0o700)
                for child in path.iterdir(): writable(child)
        writable(self.base)
        shutil.rmtree(self.base)


def mount_cases():
    observations = []
    for kind in ('different_device', 'same_device_bind'):
        with Disposable() as d:
            f = d.f
            p = f.scratch('TASK-1'); repo, q = f.worktree('TASK-2')
            source = d.base / 'external'; source.mkdir()
            if kind == 'different_device': d.mount(None, source, tmpfs=True)
            sentinel = source / 'sentinel'; sentinel.write_bytes(b'external keep'); sentinel.chmod(0o400)
            source.chmod(0o555)
            targets = [p/'nested/boundary', q/'ignored/boundary']
            for target in targets:
                target.mkdir(parents=True); d.mount(source, target)
                assert (target.stat().st_dev != target.parent.stat().st_dev) == (kind == 'different_device')
                assert mount_id(target) is not None
            before = candidate_contents(p, q, source)
            registered = f.git(repo, 'worktree', 'list', '--porcelain')
            with refuse_mutations(): report = f.sweep()
            for path in (p, q): retained(report, path, 'filesystem_boundary')
            assert candidate_contents(p, q, source) == before
            assert registered == f.git(repo, 'worktree', 'list', '--porcelain')
            assert all(v['removed_attributable_bytes'] == 0 for v in report['phases'].values())
            observations.append(dict(kind=kind, phases=['scratch','worktree'], seam='shipping on-sweep admission', reasons=[row(report,x)['reason'] for x in (p,q)], external_bytes_modes_unchanged=True, registrations_unchanged=True, mutations=0))
    # Install a real nested self-bind after the shipping final admission and
    # immediately before its actual contained mutator. Same inode/signature
    # forces the mountinfo check, rather than an earlier identity refusal.
    for phase in ('scratch', 'worktree'):
        with Disposable() as d:
            f = d.f
            if phase == 'scratch':
                path = f.scratch('TASK-1'); target = path/'nested'; repo = None
            else:
                repo, path = f.worktree('TASK-1'); target = path/'ignored'; target.mkdir()
            sentinel = target/'sentinel'; sentinel.write_text('keep'); sentinel.chmod(0o400)
            before = candidate_contents(path)
            registered = f.git(repo,'worktree','list','--porcelain') if repo else None
            real_mutate = ds.mutate; real_git = ds.git; removes = []; entries = []
            def mounted_mutate(root, tree, deadline, **kw):
                assert root == path and not entries
                entries.append(kw['remove']); d.mount(target, target)
                return real_mutate(root, tree, deadline, **kw)
            def first_failure(repo, deadline, *args):
                if args[:2] == ('worktree','remove'):
                    removes.append(args); assert len(removes) == 1
                    return real_git(repo,deadline,'--THR307-controlled-invalid-option')
                return real_git(repo,deadline,*args)
            with patch.object(ds,'mutate',mounted_mutate), patch.object(ds,'git',first_failure), patch.object(os,'fchmod',side_effect=AssertionError('boundary chmod')), patch.object(os,'unlink',side_effect=AssertionError('boundary unlink')), patch.object(os,'rmdir',side_effect=AssertionError('boundary rmdir')):
                report = f.sweep()
            item = row(report,path)
            assert item['state'] == 'partial_or_unknown' and item['reason'] == 'filesystem_boundary', report
            assert entries == [phase == 'scratch'] and not item.get('retry_count')
            assert candidate_contents(path) == before
            assert report['phases'][phase]['attribution_unknown'] and report['phases'][phase]['removed_attributable_bytes'] == 0
            if repo: assert registered == f.git(repo,'worktree','list','--porcelain') and len(removes) == 1
            observations.append(dict(kind='late_same_inode_bind',phase=phase,seam='shipping contained mutation' if phase=='scratch' else 'shipping Git failure/refreshed repair',reason=item['reason'],bytes_modes_unchanged=True,mutations=0,attribution='unknown'))
    return observations


def root_cases():
    observations = []
    for phase in ('scratch', 'worktree'):
        with Disposable() as d:
            f = d.f
            if phase == 'scratch': path = f.scratch('TASK-1'); repo = None
            else: repo, path = f.worktree('TASK-1')
            # chroot deliberately leaves cwd outside. Python's executable,
            # inherited pipes/devnull and temp environment are also outside.
            code = 'import os,sys,time,ctypes; os.chroot(sys.argv[1]); ctypes.CDLL(None).prctl(4,1,0,0,0); print("ready",flush=True); time.sleep(20)'
            proc = subprocess.Popen([sys.executable,'-c',code,str(path)],cwd=d.base,env={k:v for k,v in os.environ.items() if k not in ('TMPDIR','TMP','TEMP')},stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
            try:
                ready, _, _ = select.select([proc.stdout],[],[],5)
                assert ready, 'root child readiness timeout'
                if proc.stdout.readline().strip() != 'ready':
                    _, error = proc.communicate(timeout=2)
                    if 'Operation not permitted' in error: raise PermissionError(errno.EPERM, error)
                    raise AssertionError('root child failed: '+error)
                base = Path('/proc')/str(proc.pid)
                refs = {key:os.readlink(base/key) for key in ('cwd','root','exe')}
                refs['fd'] = [os.readlink(fd) for fd in (base/'fd').iterdir()]
                assert refs['root'] == str(path), refs
                def inside(value): return value == str(path) or value.startswith(str(path)+'/')
                assert not any(inside(v) for v in [refs['cwd'],refs['exe'],*refs['fd']]), refs
                assert all(not item.startswith((b'TMPDIR=',b'TMP=',b'TEMP=')) for item in (base/'environ').read_bytes().split(b'\0'))
                assert (base/'status').read_text().split('Uid:')[1].splitlines()[0].split()[1] == str(os.geteuid())
                before = candidate_contents(path)
                registered = f.git(repo,'worktree','list','--porcelain') if repo else None
                reads=[]; real=os.readlink
                def trace(link,*a,**kw):
                    value=real(link,*a,**kw)
                    if str(link)==f'/proc/{proc.pid}/task/{proc.pid}/root': reads.append(value)
                    return value
                with refuse_mutations(), patch.object(os,'readlink',trace): report=f.sweep()
                retained(report,path,'process_use')
                assert row(report,path)['reason'] == 'process_use' and reads == [str(path)], report
                assert before == candidate_contents(path)
                if repo: assert registered == f.git(repo,'worktree','list','--porcelain')
                assert all(v['removed_attributable_bytes']==0 for v in report['phases'].values())
                observations.append(dict(phase=phase,root_only=True,refs=refs,root_read=reads,reason='process_use',bytes_modes_registration_unchanged=True,mutations=0))
            finally:
                if proc.poll() is None: proc.kill()
                proc.communicate(timeout=3)
                assert proc.poll() is not None  # Reaped before fixture cleanup.
    return observations


def main():
    case, outer_mount, outer_pid = sys.argv[1:]
    assert os.geteuid() == 0 and os.getpid() == 1, 'fresh mapped user/PID namespace required'
    assert os.readlink('/proc/self/ns/mnt') != outer_mount
    assert os.readlink('/proc/self/ns/pid') != outer_pid
    mapping = Path('/proc/self/uid_map').read_text().split()
    assert len(mapping) == 3 and mapping[0] == '0' and mapping[2] == '1', mapping
    assert all(not any(x.startswith(('shared:','master:')) for x in line.split()[6:line.split().index('-')]) for line in Path('/proc/self/mountinfo').read_text().splitlines()), 'private propagation required'
    try:
        observations = {'mount':mount_cases,'root':root_cases}[case]()
        result = dict(result='PASS',case=case,observations=observations,cleanup='owned mounts unmounted; children reaped; disposable roots removed')
    except OSError as exc:
        if exc.errno not in (errno.EPERM, errno.EACCES, errno.ENOSYS): raise
        result = dict(result='UNAVAILABLE',case=case,reason=str(exc))
    print(json.dumps(result))


if __name__ == '__main__': main()
