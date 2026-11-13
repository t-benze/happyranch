#!/usr/bin/env python3
"""THR307 disposable acceptance driver, explicitly outside retired Python units.

Run from the repository: .venv/bin/python scripts/verify_disk_sweeper.py
Real files/Git/child processes, synthetic SQLite records, no daemon actors. The
wall-clock argument is advanced two hours for old-ctime fixtures (ctime cannot
be backdated on Linux). c12's comparison table advances an observed monotonic
clock; its snapshots/signatures and all process/Git effects remain real.
No pytest or tests/ imports. Never run against existing orgs or cleanup data.
"""
from __future__ import annotations

import argparse
import asyncio
import builtins
import fcntl
import io
import json
import logging
import os
import shutil
import signal
import socket
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
from contextlib import ExitStack, contextmanager
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from runtime.daemon import disk_sweeper as ds
from runtime.daemon import _disk_sweep_fs as fs
from runtime.orchestrator.task_scratch import prepare_task_scratch
from runtime.orchestrator.org_config import OrgConfig, OrgConfigError

RESULTS = []
NOW = time.time() + 7200


class Fixture:
    def __init__(self, base):
        self.base = base
        self.org = base / 'runtime/orgs/demo'
        (self.org / 'org').mkdir(parents=True)
        (self.org / 'org/config.yaml').write_text('disk_sweep: {mode: "on"}\n')
        (base / 'runtime/happyranch.yaml').write_text('schema_version: 2\ntype: multi-org-runtime\n')
        self.workspace = self.org / 'workspaces/maker'
        self.workspace.mkdir(parents=True)
        self.db = sqlite3.connect(self.org / 'happyranch.db')
        self.db.executescript('''
        CREATE TABLE tasks(id TEXT PRIMARY KEY,parent_task_id TEXT,status TEXT,assigned_agent TEXT);
        CREATE TABLE jobs(task_id TEXT,status TEXT);
        CREATE TABLE audit_log(id INTEGER PRIMARY KEY,task_id TEXT,agent TEXT,action TEXT,payload TEXT,timestamp TEXT);
        ''')

    def task(self, name, status='completed', parent=None):
        self.db.execute('INSERT INTO tasks VALUES(?,?,?,?)', (name, parent, status, 'maker'))
        self.db.execute('INSERT INTO audit_log(task_id,action,payload) VALUES(?,?,?)', (name,'session_start',json.dumps({'workspace':str(self.workspace)})))
        self.db.commit()

    def scratch(self, name, status='completed', parent=None):
        self.task(name,status,parent)
        s = prepare_task_scratch(workspace=self.workspace, task_id=name, producer_kind='qa_fixture', producer_id='THR307')
        (s.root / 'nested').mkdir()
        (s.root / 'nested/payload').write_bytes(b'payload' * 2048)
        return s.root

    def worktree(self, name, project="project"):
        self.task(name)
        repo = self.workspace / 'repos' / project
        if not repo.exists():
            repo.mkdir(parents=True)
            self.git(repo,'init','-q')
            self.git(repo,'config','user.name','Disposable fixture')
            self.git(repo,'config','user.email','fixture@example.invalid')
            (repo / 'source').write_text('committed\n')
            (repo / '.gitignore').write_text('ignored/\n.worktree-guard.json\n')
            self.git(repo,'add','source','.gitignore')
            self.git(repo,'commit','-qm','fixture')
        path = repo / '.claude/worktrees' / name
        path.parent.mkdir(parents=True, exist_ok=True)
        self.git(repo,'worktree','add','-qb',f'task/{name}',str(path))
        (path / '.worktree-guard.json').write_text(json.dumps(dict(task_id=name,worktree_root=str(path),primary_root=str(repo))))
        return repo,path

    @staticmethod
    def git(repo,*args):
        return subprocess.run(['git','-C',str(repo),*args],check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE).stdout.decode()

    def sweep(self, mode='on', seconds=12):
        return ds.sweep_org(self.org,time.monotonic()+seconds,mode=mode,now=NOW)


@contextmanager
def fixture():
    # Explicit task-owned temp root; never production / historical roots.
    base = Path(tempfile.mkdtemp(prefix='disk-sweep-disposable-'))
    f = Fixture(base)
    try:
        with patch.dict(os.environ, HAPPYRANCH_DAEMON_HOME=str(base / 'daemon')):
            (base / 'daemon/disk-sweep').mkdir(parents=True)
            yield f
    finally:
        f.db.close()
        # Only this newly created fixture is cleaned by the harness.
        def writable(path):
            if path.is_symlink(): return
            if path.is_dir():
                path.chmod(0o700)
                for child in path.iterdir(): writable(child)
        writable(base)
        shutil.rmtree(base)


def row(report,path):
    return next(c for c in report['candidates'] if c['path']==str(path))


def retained(report,path,reason):
    r=row(report,path)
    assert r['state']=='retained',r
    assert reason in r['reason'],r
    assert path.exists() or path.is_symlink(),path


def case(number,purpose,fn):
    start=time.monotonic()
    try:
        detail=fn()
        disposition='PARTIAL' if isinstance(detail,dict) and any(isinstance(v,dict) and v.get('result')=='UNAVAILABLE' for v in detail.values()) else 'PASS'
        RESULTS.append(dict(case=number,purpose=purpose,result=disposition,seconds=round(time.monotonic()-start,3),observations=detail))
    except Exception as exc:
        RESULTS.append(dict(case=number,purpose=purpose,result='FAIL',error=f'{type(exc).__name__}: {exc}'))
        raise


def disposable_contents(f, root, kind='all'):
    """Real disposable entries; short fd path avoids AF_UNIX pathname limits."""
    if kind in ('git_directory', 'all'):
        repo = root / 'nested/repository'
        repo.mkdir()
        f.git(repo, 'init', '-q')
        (repo / 'payload').write_text('nested repository content')
    if kind in ('git_file', 'all'):
        holder = root / 'nested/git-file'
        holder.mkdir()
        (holder / '.git').write_text('gitdir: /not-a-real-fixture-target\n')
    if kind in ('socket', 'all'):
        with fs.directory(root / 'nested', time.monotonic() + 5) as fd:
            with socket.socket(socket.AF_UNIX) as endpoint:
                endpoint.bind(f'/proc/self/fd/{fd}/socket')
    if kind in ('fifo', 'all'):
        os.mkfifo(root / 'nested/fifo')


@contextmanager
def no_endpoint_io():
    """Fail before a blocking endpoint open; allow metadata-only O_PATH."""
    attempts = []
    def guarded(original, descriptor=False):
        def call(path, *args, **kwargs):
            if not isinstance(path, int):
                try:
                    observed = os.stat(path, dir_fd=kwargs.get('dir_fd'), follow_symlinks=False)
                except (FileNotFoundError, NotADirectoryError):
                    observed = None
                if observed and (stat.S_ISSOCK(observed.st_mode) or stat.S_ISFIFO(observed.st_mode)):
                    if not descriptor or not args[0] & os.O_PATH:
                        attempts.append(os.fsdecode(path))
                        raise AssertionError('endpoint I/O open')
            return original(path, *args, **kwargs)
        return call
    with patch.object(os, 'open', guarded(os.open, True)), \
            patch.object(builtins, 'open', guarded(builtins.open)), \
            patch.object(io, 'open', guarded(io.open)):
        try:
            yield
        finally:
            assert attempts == [], attempts


def c1():
    with fixture() as f:
        p=f.scratch('TASK-1')
        manifest=f.workspace / '.happyranch/task-scratch-manifests'
        before={x.name:x.read_bytes() for x in manifest.iterdir()}
        out=f.workspace / 'output/TASK-1';out.mkdir(parents=True);(out/'keep').write_text('durable')
        os.link(p/'nested/payload',p/'nested/duplicate')
        (out/'external-link').write_text('durable hardlink')
        external=f.scratch('TASK-2')
        os.link(out/'external-link',external/'link-to-output')
        before={x.name:x.read_bytes() for x in manifest.iterdir()}
        roots = [p]
        for number, kind in enumerate(('git_directory', 'git_file', 'socket', 'fifo', 'all'), 3):
            target = f.scratch(f'TASK-{number}')
            disposable_contents(f, target, kind)
            (target / 'outside').symlink_to(out / 'keep')
            roots.append(target)
        before={x.name:x.read_bytes() for x in manifest.iterdir()}
        # Independent allocation oracle: actual lstat blocks once per inode,
        # with all links internal in these successful candidates.
        expected = {}
        for target in roots:
            inodes = {(s.st_dev, s.st_ino): s.st_blocks * 512
                      for x in (target, *target.rglob('*')) for s in (x.lstat(),)}
            expected[str(target)] = sum(inodes.values())
        with no_endpoint_io(): r=f.sweep()
        retained(r,external,'unknown_external_hardlinks')
        assert all(row(r,target)['state']=='removed' for target in roots), [row(r,target) for target in roots]
        for target in roots:
            assert row(r,target)['state']=='removed',row(r,target)
            assert row(r,target)['allocated_bytes']==expected[str(target)],row(r,target)
            assert row(r,target)['removed_attributable_bytes']==expected[str(target)],row(r,target)
            assert not target.exists(),target
        assert (out/'external-link').read_text()=='durable hardlink'
        assert row(r,p)['state']=='removed',r
        assert not p.exists()
        assert before=={x.name:x.read_bytes() for x in manifest.iterdir()}
        assert (out/'keep').read_text()=='durable'
        assert r['phases']['scratch']['removed_attributable_bytes']==sum(expected.values())>0
        assert r['physical_freed_bytes'] is None
        return dict(disposable_kinds=['ordinary/internal hardlinks', 'git_directory', 'git_file', 'socket', 'fifo', 'all'], allocated_by_path=expected,
                    preserved='manifest/lock/output/outside sentinel/external hardlink; metadata-only endpoints')


def c2():
    with fixture() as f:
        f.task('TASK-1','in_progress')
        p=f.scratch('TASK-2',parent='TASK-1')
        disposable_contents(f,p)
        rejected=[p]
        for n,state in enumerate(('superseded','escalated','unknown','pending'),3): rejected.append(f.scratch(f'TASK-{n}',state))
        rejected.append(f.scratch('TASK-7',parent='TASK-999'))
        a=f.scratch('TASK-8',parent='TASK-9');rejected.append(a)
        b=f.scratch('TASK-9',parent='TASK-8');rejected.append(b)
        f.db.execute("UPDATE audit_log SET payload=? WHERE task_id='TASK-3'",(json.dumps({'workspace':'/conflicting'}),));f.db.commit()
        conflict=f.scratch('TASK-10');rejected.append(conflict)
        f.db.execute("UPDATE audit_log SET payload=? WHERE task_id='TASK-10'",(json.dumps({'workspace':'/conflicting'}),));f.db.commit()
        f.task('TASK-12')
        active=f.scratch('TASK-11',status='in_progress',parent='TASK-12');rejected.append(active)
        disposable_contents(f,active)
        before=candidate_contents(p,active)
        with no_endpoint_io(),refuse_mutations(): r=f.sweep()
        retained(r,p,'task_or_root_not_terminal');retained(r,active,'task_or_root_not_terminal')
        assert before==candidate_contents(p,active)
        assert 'conflicting_workspace' in row(r,conflict)['reason']
        assert all(row(r,p)['state']=='retained' and p.exists() for p in rejected),r
        assert not r['phases']['scratch']['removed_attributable_bytes']
        return {p.name:row(r,p)['reason'] for p in rejected}


def c3():
    # Historical seq85 process-retention fixture; superseded by seq117.
    with fixture() as f:
        p=f.scratch('TASK-1')
        reads=[];original=os.readlink
        def traced(path,*a,**kw):
            reads.append(str(path));return original(path,*a,**kw)
        proc=subprocess.Popen(['sleep','20'],cwd=p)
        try:
            with patch.object(os,'readlink',traced): r=f.sweep()
            retained(r,p,'process_use')
        finally: proc.terminate();proc.wait()
        # A real open fd and executable inside a second candidate are separate
        # observations, so cwd refusal alone cannot satisfy these boundaries.
        for kind in ('fd','exe'):
            q=f.scratch('TASK-'+('2' if kind=='fd' else '3'))
            if kind=='fd':
                proc=subprocess.Popen([sys.executable,'-c','import sys,time; f=open(sys.argv[1]); print("ready",flush=True); time.sleep(20)',str(q/'nested/payload')],stdout=subprocess.PIPE,text=True)
                assert proc.stdout.readline().strip()=='ready'
            else:
                shutil.copy2(shutil.which('sleep'),q/'sleep')
                proc=subprocess.Popen([str(q/'sleep'),'20'])
            try:
                with patch.object(os,'readlink',traced): r=f.sweep()
                retained(r,q,'process_use')
            finally: proc.terminate();proc.wait()
        assert all(any(x.endswith('/'+kind) for x in reads) for kind in ('cwd','root','exe'))
        assert any('/fd/' in x for x in reads)
        return dict(ordinary_processes='Real cwd, fd and executable users retained; actual proc read trace inspected.',root_case=isolated_boundary('root'))


def c4():
    # Historical process-environment retention fixture; superseded by seq117.
    with fixture() as f:
        for n,key in enumerate(('TMPDIR','TMP','TEMP'),1):
            p=f.scratch(f'TASK-{n}')
            proc=subprocess.Popen(['sleep','20'],env={**os.environ,key:str(p)})
            try: retained(f.sweep(),p,'process_use')
            finally: proc.terminate();proc.wait()
        p=f.scratch('TASK-4');alias=f.base/'temp-alias';alias.symlink_to(p,target_is_directory=True)
        proc=subprocess.Popen(['sleep','20'],env={**os.environ,'TMPDIR':str(alias)})
        try:retained(f.sweep(),p,'process_use')
        finally:proc.terminate();proc.wait()
        return 'Three actual child environment variants and a symlinked TMPDIR alias retained.'


def c5():
    with fixture() as f:
        p=f.scratch('TASK-1');(p/'nested').chmod(0o555)
        assert row(f.sweep(),p)['state']=='removed'
        repo,q=f.worktree('TASK-2');q.chmod(0o555)
        r=f.sweep()
        # This Git version removes registration on permission failure. That
        # partial outcome correctly refuses fresh retry; never recreate it.
        assert row(r,q)['state']=='partial_or_unknown',r
        repo,z=f.worktree('TASK-3','retry-project');z.chmod(0o555)
        real=ds.git;fail_once=[True]
        def fault(repo,deadline,*args):
            if args[:2]==('worktree','remove') and args[-1]==str(z) and fail_once[0]:
                fail_once[0]=False
                # Controlled subprocess failure before mutation, using actual
                # Git (exit129); subsequent command is the real remove.
                return real(repo,deadline,'--THR307-controlled-invalid-option')
            return real(repo,deadline,*args)
        with patch.object(ds,'git',fault):r=f.sweep()
        assert row(r,z)['state']=='removed' and row(r,z).get('retry_count')==1,r
        assert 'task/TASK-3' in f.git(repo,'branch','--list')
        return 'Readonly scratch removed. Real Git permission failure/unregistration retained partial. Separate controlled real-Git exit129 before mutation exercised one full-gated repair/retry, successful removal and branch preservation.'



def c6():
    with fixture() as f:
        p=f.scratch('TASK-1');sentinel=f.base/'sentinel';sentinel.write_text('external');sentinel.chmod(0o400)
        (p/'outside').symlink_to(sentinel)
        before=sentinel.stat();r=f.sweep();assert row(r,p)['state']=='removed',r
        assert sentinel.read_text()=='external' and sentinel.stat().st_mode==before.st_mode
        q=f.scratch('TASK-2');shutil.rmtree(q);q.symlink_to(sentinel.parent,target_is_directory=True)
        r=f.sweep();retained(r,q,'Not a directory')
        # Actual ancestor symlink refuses before mutation.
        q.unlink();p=f.scratch('TASK-3');parent=p.parent;relocated=parent.with_name('held-tmp');parent.rename(relocated);parent.symlink_to(relocated,target_is_directory=True)
        r=f.sweep();assert r['outcome']=='incomplete' and (relocated/'TASK-3').exists(),r
        parent.unlink();relocated.rename(parent)
        return dict(external_sentinel='bytes/mode unchanged; symlink candidate and ancestor retained',mount_case=isolated_boundary('mount'))


def c7():
    with fixture() as f:
        repo,p=f.worktree('TASK-1');(p/'source').write_text('dirty')
        _,q=f.worktree('TASK-2');(q/'untracked').write_text('keep')
        _,z=f.worktree('TASK-3');(z/'ignored').mkdir();(z/'ignored/cache').write_text('disposable')
        special=[]
        for number,kind in enumerate(('socket','fifo'),4):
            _,target=f.worktree(f'TASK-{number}')
            (target/'ignored/nested').mkdir(parents=True)
            disposable_contents(f,target/'ignored',kind)
            special.append(target)
        before=candidate_contents(*special)
        with no_endpoint_io(): r=f.sweep()
        retained(r,p,'dirty');retained(r,q,'dirty')
        for target in special: retained(r,target,'unknown_special_entry')
        assert before==candidate_contents(*special)
        assert row(r,p)['dirty_paths'] and row(r,q)['dirty_paths']
        assert row(r,z)['state']=='removed',r
        assert 'task/TASK-3' in f.git(repo,'branch','--list')
    metadata=[]
    for kind in ('socket','fifo'):
        with fixture() as f:
            repo,target=f.worktree('TASK-1')
            admin=repo/'.git/worktrees'/target.name
            (admin/'nested').mkdir()
            disposable_contents(f,admin,kind)
            before=candidate_contents(target,admin)
            with no_endpoint_io(),refuse_mutations(): r=f.sweep()
            assert r['discovery']=='partial' and r['outcome']=='incomplete',r
            assert any('unknown_special_entry' in s['reason'] for s in r['discovery_scopes']),r
            assert not r['candidates'],r
            assert before==candidate_contents(target,admin)
            metadata.append(dict(kind=kind,whole_repository='retained without mutation'))
    # Safe synthetic kernel metadata on owned regular fixtures: never mknod.
    unknown=[]
    for mode in (stat.S_IFCHR,stat.S_IFBLK,0):
        with fixture() as f:
            target=f.scratch('TASK-1');leaf=target/'nested/payload'
            real=os.stat
            def observed(path,*args,**kwargs):
                value=real(path,*args,**kwargs)
                if (value.st_dev,value.st_ino)==(leaf_identity.st_dev,leaf_identity.st_ino):
                    fields={name:getattr(value,name) for name in dir(value) if name.startswith('st_')}
                    fields['st_mode']=mode | 0o600
                    return SimpleNamespace(**fields)
                return value
            leaf_identity=leaf.lstat()
            with patch.object(os,'stat',observed),refuse_mutations(): r=f.sweep()
            retained(r,target,'unknown_special_entry')
            assert leaf.read_bytes()==b'payload'*2048
            unknown.append(dict(synthetic_type=mode,retained=True))
    return dict(worktree_ignored_endpoints='retained; regular ignored cache removed; dirty/untracked retained',strict_metadata=metadata,unknown_types=unknown)


def c8():
    with fixture() as f:
        p=f.scratch('TASK-1')
        disposable_contents(f,p)
        sentinel=f.base/'outside-sentinel';sentinel.write_text('outside');sentinel.chmod(0o400)
        (p/'outside').symlink_to(sentinel)
        (p/'nested').chmod(0o555)
        repo,q=f.worktree('TASK-2')
        def signature():
            return {str(x.relative_to(f.base)):(x.lstat().st_mode,x.read_bytes() if x.is_file() else None) for x in f.base.rglob('*') if x.name!='happyranch.db'}
        before=signature();registered=f.git(repo,'worktree','list','--porcelain')
        refs=f.git(repo,'show-ref')
        calls=[];realgit=ds.git
        def trace(*a):calls.append(a[2:]);return realgit(*a)
        with no_endpoint_io(),patch.object(ds,'git',trace),patch.object(os,'fchmod',side_effect=AssertionError('dry chmod')),patch.object(os,'unlink',side_effect=AssertionError('dry unlink')),patch.object(os,'rmdir',side_effect=AssertionError('dry rmdir')):
            r=f.sweep('dry_run')
        assert all(c['state']=='eligible' for c in r['candidates']),r
        assert before==signature() and registered==f.git(repo,'worktree','list','--porcelain')
        assert refs==f.git(repo,'show-ref')
        assert not any('remove' in c or 'prune' in c for c in calls)
        controls=f.workspace/'.happyranch/task-scratch-manifests'
        before_cli=candidate_contents(p,q,sentinel,controls,repo/'.git')
        # Public manual CLI uses real worker, actual current clock, JSON plus
        # one permitted audit row; it never activates on-mode deletion.
        result=subprocess.run([sys.executable,'-m','runtime.daemon.disk_sweeper','--dry-run','--runtime-root',str(f.base/'runtime')],env={**os.environ,'HAPPYRANCH_DAEMON_HOME':str(f.base/'daemon')},capture_output=True,text=True,timeout=8)
        assert result.returncode==0,result.stderr
        assert f.db.execute("SELECT COUNT(*) FROM audit_log WHERE action='disk_sweep_summary'").fetchone()[0]==1
        assert p.exists() and q.exists() and registered==f.git(repo,'worktree','list','--porcelain')
        assert before_cli==candidate_contents(p,q,sentinel,controls,repo/'.git')
        assert refs==f.git(repo,'show-ref')
        return 'Function dry-run tree bytes/modes and Git registrations unchanged; invocation trace has no mutation. Public manual CLI real worker exit0, one audit summary and JSON; candidate roots remain.'


# Unsupported aliases are literal unknown values, never guessed terminal states.
UNKNOWN_JOB_STATES = ('cancelled', 'succeeded', 'interrupted', 'timed_out', 'unknown', ' completed ', '', None, b'completed')


def candidate_contents(*roots):
    return {
        str(path): (path.lstat().st_mode, path.read_bytes() if path.is_file() else None)
        for root in roots for path in (root, *root.rglob('*'))
    }


@contextmanager
def refuse_mutations():
    calls=[]
    realgit=ds.git
    def forbidden(name):
        def fail(*a,**kw):
            calls.append(name)
            raise AssertionError('unexpected mutation: '+name)
        return fail
    def trace(repo,deadline,*args):
        if args[:2] in (('worktree','remove'), ('worktree','prune')):
            return forbidden('git '+args[1])()
        return realgit(repo,deadline,*args)
    with patch.object(ds,'git',trace), patch.object(os,'chmod',forbidden('chmod')), patch.object(os,'fchmod',forbidden('fchmod')), patch.object(os,'unlink',forbidden('unlink')), patch.object(os,'rmdir',forbidden('rmdir')):
        yield calls
    assert calls==[],calls


_DEFAULT_JOB_OWNER = object()


def job_refusal(f,status,reason,job_owner=_DEFAULT_JOB_OWNER):
    p=f.scratch('TASK-1');repo,q=f.worktree('TASK-2')
    (p/'nested').chmod(0o555);q.chmod(0o555)
    f.db.executemany('INSERT INTO jobs VALUES(?,?)',[(job_owner,status)] if job_owner is not _DEFAULT_JOB_OWNER else [('TASK-1',status),('TASK-2',status)]);f.db.commit()
    before=candidate_contents(p,q);registered=f.git(repo,'worktree','list','--porcelain')
    with refuse_mutations():r=f.sweep()
    retained(r,p,reason);retained(r,q,reason)
    assert before==candidate_contents(p,q)
    assert registered==f.git(repo,'worktree','list','--porcelain')
    assert all(phase['removed_attributable_bytes']==0 for phase in r['phases'].values()),r
    return dict(status=repr(status),scratch=row(r,p)['reason'],worktree=row(r,q)['reason'],unchanged=True,mutation_calls=0)


LINEAGE_KINDS = ('missing', 'cycle', 'empty_parent', 'missing_parent', 'blob_parent', 'empty_owner', 'blob_owner', 'null_owner', 'conflicting')


def broken_job_owner(f,kind):
    if kind=='missing': return 'TASK-90'
    if kind=='empty_owner': return ''
    if kind=='blob_owner': return b'TASK-90'
    if kind=='null_owner': return None  # Fixture also tests fail-closed malformed projection.
    if kind=='conflicting':
        # Native PRIMARY KEY excludes duplicates; model a conflicting reader
        # projection explicitly, without claiming native duplicate storage.
        f.db.execute('ALTER TABLE tasks RENAME TO saved_tasks')
        f.db.execute('CREATE TABLE tasks AS SELECT * FROM saved_tasks')
        f.db.execute("INSERT INTO tasks VALUES('TASK-90',NULL,'completed','maker')")
        f.db.execute("INSERT INTO tasks VALUES('TASK-90','TASK-91','completed','maker')")
        f.db.commit()
        return 'TASK-90'
    parent={'cycle':'TASK-90','empty_parent':'','missing_parent':'TASK-999','blob_parent':b'TASK-91'}[kind]
    f.task('TASK-90',parent=parent)
    return 'TASK-90'


def lineage_refusals():
    observations=[]
    for kind in LINEAGE_KINDS:
        for status in ('running',):
            with fixture() as f:
                owner=broken_job_owner(f,kind)
                item=job_refusal(f,status,'lineage',job_owner=owner)
                item['lineage']=kind;observations.append(item)
    return observations


def c9():
    with fixture() as f:
        p=f.scratch('TASK-1')
        manifest=f.workspace/'.happyranch/task-scratch-manifests/TASK-1.json';manifest.unlink()
        r=f.sweep();assert row(r,p)['state']=='retained'
        q=f.scratch('TASK-2');(q/'nested').chmod(0)
        r=f.sweep();assert row(r,q)['state']=='retained'
        # Process-denial retention was superseded by seq117.
        z=f.scratch('TASK-3')
        f.db.execute('DROP TABLE jobs');f.db.commit()
        assert row(f.sweep(),z)['state']=='retained'
    unknown=[]
    for status in UNKNOWN_JOB_STATES:
        with fixture() as f:unknown.append(job_refusal(f,status,'unknown_job_status'))
    return dict(evidence='Missing manifest/jobs, actual unreadable child directory, all retain. Process-denial retention superseded by seq117.',unknown_jobs=unknown,unknown_lineage=lineage_refusals())


def c10():
    active=[]
    for status in ('pending','queued','running'):
        with fixture() as f:active.append(job_refusal(f,status,'active_job'))
    terminal=[]
    for status in ('completed','failed','rejected'):
        with fixture() as f:
            p=f.scratch('TASK-1');repo,q=f.worktree('TASK-2')
            f.db.executemany('INSERT INTO jobs VALUES(?,?)',[('TASK-1',status),('TASK-2',status)]);f.db.commit()
            r=f.sweep()
            assert row(r,p)['state']==row(r,q)['state']=='removed',r
            assert not p.exists() and not q.exists()
            assert str(q) not in f.git(repo,'worktree','list','--porcelain')
            assert 'task/TASK-2' in f.git(repo,'branch','--list')
            assert all(phase['removed_attributable_bytes']>0 for phase in r['phases'].values()),r
            terminal.append(dict(status=status,scratch='removed',worktree='removed',branch_retained=True))
    unrelated=[]
    for status in ('pending','queued','running','completed','failed','rejected'):
        with fixture() as f:
            p=f.scratch('TASK-1');repo,q=f.worktree('TASK-2')
            f.task('TASK-90');f.task('TASK-91',parent='TASK-90')
            f.db.execute('INSERT INTO jobs VALUES(?,?)',('TASK-91',status));f.db.commit()
            r=f.sweep()
            assert row(r,p)['state']==row(r,q)['state']=='removed',r
            assert not p.exists() and not q.exists()
            assert str(q) not in f.git(repo,'worktree','list','--porcelain')
            assert 'task/TASK-2' in f.git(repo,'branch','--list')
            unrelated.append(dict(status=status,both_removed=True,branch_retained=True))
    with fixture() as f:
        p=f.scratch('TASK-1');repo,q=f.worktree('TASK-2')
        for status in ('pending','queued','running'):
            f.db.execute('DELETE FROM jobs');f.db.executemany('INSERT INTO jobs VALUES(?,?)',[('TASK-1',status),('TASK-2',status)]);f.db.commit()
            r=f.sweep();retained(r,p,'active_job');retained(r,q,'active_job')
        f.db.execute('DELETE FROM jobs');f.task('TASK-3')
        f.db.execute("UPDATE tasks SET parent_task_id='TASK-3' WHERE id IN ('TASK-1','TASK-2')")
        f.db.execute("INSERT INTO jobs VALUES('TASK-3','running')");f.db.commit()
        r=f.sweep();retained(r,p,'active_job');retained(r,q,'active_job')
        f.db.execute('DELETE FROM jobs');f.db.commit();q.chmod(0o555)
        real=ds.git
        def change(repo,deadline,*args):
            try:return real(repo,deadline,*args)
            except fs.Retain:
                if args[:2]==('worktree','remove'):
                    f.db.execute("INSERT INTO jobs VALUES('TASK-2','queued')");f.db.commit()
                raise
        with patch.object(ds,'git',change):r=f.sweep()
        assert row(r,q)['state']=='partial_or_unknown' and 'active_job' in row(r,q)['reason'],r
        assert q.stat().st_mode & 0o777==0o555 and not row(r,q).get('retry_count')
    retries=[]
    for status in UNKNOWN_JOB_STATES[:4]:
        with fixture() as f:
            repo,q=f.worktree('TASK-1');q.chmod(0o555)
            f.db.execute("INSERT INTO jobs VALUES('TASK-1','completed')");f.db.commit()
            before=candidate_contents(q);registered=f.git(repo,'worktree','list','--porcelain')
            real=ds.git;removes=[];mutations=[]
            def change_to_unknown(repo,deadline,*args):
                if args[:2]==('worktree','remove'):
                    removes.append(args)
                    assert len(removes)==1,'second removal attempted'
                    try:return real(repo,deadline,'--THR307-controlled-invalid-option')
                    except fs.Retain:
                        f.db.execute('UPDATE jobs SET status=?',(status,));f.db.commit()
                        raise
                if args[:2]==('worktree','prune'):
                    mutations.append('prune');raise AssertionError('unexpected prune')
                return real(repo,deadline,*args)
            def forbidden(*a,**kw):
                mutations.append('permission/removal');raise AssertionError('mutation after first failure')
            with patch.object(ds,'git',change_to_unknown), patch.object(os,'chmod',forbidden), patch.object(os,'fchmod',forbidden), patch.object(os,'unlink',forbidden), patch.object(os,'rmdir',forbidden):r=f.sweep()
            item=row(r,q)
            assert item['state']=='partial_or_unknown' and 'unknown_job_status' in item['reason'],r
            assert item['first_remove_error'].startswith('git_failed:') and not item.get('retry_count'),r
            assert len(removes)==1 and mutations==[]
            assert before==candidate_contents(q) and registered==f.git(repo,'worktree','list','--porcelain')
            assert r['outcome']=='error' and r['phases']['worktree']['attribution_unknown'],r
            assert r['phases']['worktree']['removed_attributable_bytes']==0,r
            retries.append(dict(status=status,state=item['state'],reason=item['reason'],remove_calls=1,repair_calls=0,unchanged=True))
    return dict(active_jobs=active,native_terminal_controls=terminal,active_retry='Original newly queued job after real Git failure still refuses chmod/retry.',unknown_retries=retries,unrelated_valid_jobs=unrelated,lineage_retries=lineage_retry_refusals())


def lineage_retry_refusals():
    observations=[]
    for kind in ('missing','cycle','empty_parent'):
        for status in ('running',):
            with fixture() as f:
                repo,q=f.worktree('TASK-1')
                before=candidate_contents(q);registered=f.git(repo,'worktree','list','--porcelain')
                real=ds.git;removes=[];mutations=[]
                def change(repo,deadline,*args):
                    if args[:2]==('worktree','remove'):
                        removes.append(args);assert len(removes)==1,'second remove'
                        # Real first unlink remains historical partial deletion;
                        # an actual failing Git command defines the retry seam.
                        (q/'source').unlink()
                        try:return real(repo,deadline,'--THR307-controlled-invalid-option')
                        except fs.Retain:
                            f.db.execute('INSERT INTO jobs VALUES(?,?)',(broken_job_owner(f,kind),status));f.db.commit()
                            raise
                    if args[:2]==('worktree','prune'):
                        mutations.append('prune');raise AssertionError('prune')
                    return real(repo,deadline,*args)
                def forbidden(*a,**kw):
                    mutations.append('repair');raise AssertionError('repair after lineage change')
                with patch.object(ds,'git',change),patch.object(os,'chmod',forbidden),patch.object(os,'fchmod',forbidden):r=f.sweep()
                item=row(r,q)
                assert item['state']=='partial_or_unknown' and 'lineage' in item['reason'],r
                assert item['first_remove_error'].startswith('git_failed:') and not item.get('retry_count')
                assert len(removes)==1 and mutations==[]
                assert not (q/'source').exists()
                after=candidate_contents(q)
                # Only the already observed unlink and directory timestamps
                # changed; all surviving content/modes remain exact.
                assert after=={k:v for k,v in before.items() if k!=str(q/'source')}
                assert registered==f.git(repo,'worktree','list','--porcelain')
                assert r['outcome']=='error' and r['phases']['worktree']['attribution_unknown']
                assert r['phases']['worktree']['removed_attributable_bytes']==0
                observations.append(dict(lineage=kind,status=status,partial_unlink=True,remove_calls=1,repair_calls=0,reason=item['reason'],survivors_unchanged=True,registration_unchanged=True))
    return observations


def c11():
    with fixture() as f:
        p=f.scratch('TASK-1');repo,q=f.worktree('TASK-2')
        for root,file in ((p,p/'nested/payload'),(q,q/'source')):
            os.utime(root,(time.time()-20000,)*2)
            os.utime(file,(NOW,)*2)
        r=f.sweep();retained(r,p,'changed_within_hour');retained(r,q,'changed_within_hour')
        return 'Backdated root mtime cannot admit nested recent writes in either phase.'


def c12():
    def comparison(caller: str, change: str, boundary: str | None = None, beyond: bool = False) -> dict:
        """Observe the actual two consumers, including their old signature alias."""
        label = f'{caller}/{change}/{boundary or "unexpired"}/{"beyond" if beyond else "exact"}'
        with fixture() as f:
            target = f.scratch('TASK-1')
            early, tail = target / 'a-early', target / 'z-late'
            early.write_bytes(b'early'); tail.write_bytes(b'late')
            os.link(target / 'nested/payload', target / 'nested/duplicate')
            control = f.scratch('TASK-2', status='in_progress')
            output = f.workspace / 'output/TASK-1'
            output.mkdir(parents=True); (output / 'keep').write_bytes(b'durable')
            outside = f.base / 'outside'; outside.write_bytes(b'outside')
            (target / 'outside').symlink_to(outside)
            sidecars = f.workspace / '.happyranch/task-scratch-manifests'
            # Ensure this candidate precedes the retained control regardless of
            # filesystem timestamp resolution; all descendants pass NOW's age.
            os.utime(target, (NOW - 10800, NOW - 10800))
            os.utime(control, (NOW - 7200, NOW - 7200))
            protected = candidate_contents(control, output, outside, sidecars)
            expected_contents = candidate_contents(target)
            inodes = {(s.st_dev, s.st_ino): s.st_blocks * 512
                      for p in (target, *target.rglob('*')) for s in (p.lstat(),)}
            amount = sum(inodes.values())  # c1's independent allocation oracle
            report, captures, admissions, returns, entries, checks = {}, [], [], [], [], []
            selected, captured, begun, pairs, differences = [], {}, set(), {}, []
            real_snapshot, real_admit = ds.snapshot, ds.admit
            real_signature, real_check, real_clock = fs.signature, fs.check, time.monotonic
            real_chmod = os.chmod
            real_compare = getattr(ds, 'trees_equal', None)
            deadline = real_clock() + 12
            clock = dict(value=deadline - 1, armed=False, observing=False, inside=False, flipped=False)
            changed = False
            summary = f.base / 'comparison.json'

            def drift() -> None:
                nonlocal changed, expected_contents
                if changed or change == 'reordered_equal' or change == 'deadline':
                    return
                if change == 'early_mismatch':
                    early.write_bytes(b'EARLY')  # real non-root, same-length drift
                elif change == 'tail_write':
                    tail.write_bytes(b'LATE')
                elif change == 'key_rename':
                    tail.rename(tail.with_name('zz-renamed'))
                elif change == 'replacement':
                    replacement = f.base / 'replacement'
                    replacement.write_bytes(b'NEW!'); replacement.replace(tail)
                elif change == 'chmod':
                    real_chmod(tail, 0o400)  # fixture action, not a sweeper repair
                else:
                    raise AssertionError(change)
                changed = True
                expected_contents = candidate_contents(target)

            def arm(left: dict, right: dict) -> None:
                assert not selected and left and right and '' in left and '' in right
                selected.extend((left, right))
                for side, tree in enumerate(selected):
                    for key, observed in tree.items():
                        captured[id(observed)] = (key, side)
                if change == 'reordered_equal':
                    assert list(left) != list(right), label
                elif change != 'deadline':
                    assert changed, label
                    assert len(left) == len(right), label
                    for key in sorted(left.keys() | right.keys()):
                        before = real_signature(right[key]) if key in right else None
                        after = real_signature(left[key]) if key in left else None
                        if before != after:
                            differences.append(dict(key=key, left=after, right=before))
                    assert differences, label
                if change == 'early_mismatch':
                    assert real_signature(left['']) == real_signature(right['']), label
                    assert real_signature(left['a-early']) != real_signature(right['a-early']), label
                    assert list(left).index('a-early') < list(left).index('z-late'), label
                clock['observing'] = True
                if boundary:
                    clock['armed'] = True
                    if boundary == 'entry':
                        clock['value'] = deadline + int(beyond)
                        clock['flipped'] = True

            def snapshot(path: Path, original_deadline: float, **kwargs) -> dict:
                observed = real_snapshot(path, original_deadline, **kwargs)
                if path == target:
                    number = len(captures) + 1
                    if change == 'reordered_equal' and (
                        caller == 'admit' and number == 2 or caller == 'sweep_org' and number in (3, 4)
                    ):
                        observed = dict(reversed(tuple(observed.items())))
                    captures.append(observed)
                    if caller == 'admit' and number == 1:
                        drift()
                    if caller == 'admit' and number == 2:
                        arm(observed, captures[0])
                return observed

            def admit(candidate: dict, evidence: ds.Evidence, original_deadline: float, **kwargs) -> dict:
                admissions.append(candidate['path'])
                observed = real_admit(candidate, evidence, original_deadline, **kwargs)
                if caller == 'sweep_org' and candidate['path'] == str(target) and len(captures) == 4:
                    arm(captures[0], observed)
                return observed

            def checkpoint() -> None:
                ds.atomic_json(summary, report)  # persisted existing boundary
                if caller == 'sweep_org' and any(c['state'] == 'mutation_in_flight' for c in report['candidates']):
                    drift()

            def signature(observed: os.stat_result) -> tuple:
                match = captured.get(id(observed)) if clock['observing'] else None
                if match:
                    key, side = match
                    if key not in begun:
                        begun.add(key)
                        entries.append(dict(key=key, started=clock['value'], expired=clock['armed'] and clock['value'] >= deadline))
                    pairs.setdefault(key, set()).add(side)
                value = real_signature(observed)
                if match and boundary and not clock['flipped'] and len(pairs[key]) == 2:
                    trigger = list(selected[0])[0] if boundary == 'first' else list(selected[0])[-1]
                    if key == trigger:
                        # A pair already in progress may finish; production must
                        # guard the NEXT entry or its own normal return.
                        clock.update(value=deadline + int(beyond), flipped=True)
                return value

            def check(original_deadline: float) -> None:
                if clock['inside']:
                    checks.append(dict(deadline=original_deadline, at=clock['value']))
                return real_check(original_deadline)  # observation adds no guard

            def compare(left: dict, right: dict, original_deadline: float) -> bool:
                if not selected or left is not selected[0] or right is not selected[1]:
                    return real_compare(left, right, original_deadline)
                assert original_deadline == deadline, label
                clock['inside'] = True
                try:
                    value = real_compare(left, right, original_deadline)
                except fs.Expired:
                    returns.append(dict(kind='Expired', at=clock['value'], deadline=original_deadline))
                    raise
                else:
                    returns.append(dict(kind='return', at=clock['value'], deadline=original_deadline, value=value))
                    return value
                finally:
                    clock['inside'] = False
                    clock['observing'] = False

            mutations = []
            real_mutate = ds.mutate
            def mutate(*args, **kwargs) -> dict:
                mutations.append(str(args[0]))
                return real_mutate(*args, **kwargs)

            with ExitStack() as stack:
                stack.enter_context(patch.object(ds, 'snapshot', snapshot))
                stack.enter_context(patch.object(ds, 'admit', admit))
                stack.enter_context(patch.object(ds, 'signature', signature))
                stack.enter_context(patch.object(fs, 'signature', signature))
                stack.enter_context(patch.object(fs, 'check', check))
                stack.enter_context(patch.object(time, 'monotonic', lambda: clock['value'] if clock['armed'] else real_clock()))
                stack.enter_context(patch.object(ds, 'mutate', mutate))
                if real_compare is not None:
                    stack.enter_context(patch.object(ds, 'trees_equal', compare))
                if change != 'reordered_equal':
                    stack.enter_context(refuse_mutations())
                sweep_expired = False
                try:
                    result = ds.sweep_org(f.org, deadline, mode='on', now=NOW, result=report, checkpoint=checkpoint)
                except fs.Expired:
                    if not boundary:
                        raise
                    sweep_expired = True
                    result = report  # shipping sweep_org checkpoints then propagates

            evidence = dict(row=label, entries=entries, checks=checks, comparator=returns,
                            differences=differences, original_deadline=deadline, admissions=admissions)
            assert protected == candidate_contents(control, output, outside, sidecars), evidence
            candidate, phase = row(result, target), result['phases']['scratch']
            if change == 'reordered_equal':
                assert candidate['state'] == 'removed' and not target.exists(), evidence
                assert candidate['allocated_bytes'] == candidate['removed_attributable_bytes'] == amount, candidate
                retained(result, control, 'task_or_root_not_terminal:in_progress')
            else:
                assert expected_contents == candidate_contents(target) and not mutations, evidence
                assert phase['removed_attributable_bytes'] == 0, result
                assert phase['measured_allocated_bytes'] == (amount if caller == 'sweep_org' else 0), result
                assert candidate.get('allocated_bytes') == (amount if caller == 'sweep_org' else None), candidate
                if boundary:
                    assert sweep_expired, evidence
                    assert result['outcome'] == 'incomplete' and result['error'] == 'total sweep deadline', evidence
                    assert candidate['state'] == ('inspecting' if caller == 'admit' else 'mutation_in_flight'), candidate
                    assert str(control) not in admissions, evidence
                    assert clock['flipped'], evidence
                    assert not any(e['expired'] for e in entries), ('comparison entry began after expiry', evidence)
                    assert not any(r['kind'] == 'return' and r['at'] >= deadline for r in returns), ('comparator returned after expiry', evidence)
                    if real_compare is not None:
                        assert returns and returns[-1]['kind'] == 'Expired', evidence
                        assert checks and all(c['deadline'] == deadline for c in checks), evidence
                    if change == 'early_mismatch':
                        assert 'a-early' in begun and 'z-late' in begun, evidence
                else:
                    retained(result, target, 'tree_changed_during_admission' if caller == 'admit' else 'tree_changed_before_mutation')
            assert json.loads(summary.read_text()) == result, label
            evidence.update(state=candidate['state'], measured=phase['measured_allocated_bytes'],
                            removed=phase['removed_attributable_bytes'], preserved=change != 'reordered_equal')
            return evidence

    comparisons = []
    for caller in ('admit', 'sweep_org'):
        for change in ('reordered_equal', 'tail_write', 'key_rename', 'replacement', 'chmod'):
            comparisons.append(comparison(caller, change))
        for boundary in ('entry', 'first', 'last', 'early_mismatch_later_expiry'):
            for beyond in (False, True):
                comparisons.append(comparison(caller, 'early_mismatch' if boundary == 'early_mismatch_later_expiry' else 'deadline', boundary, beyond))
    with fixture() as f:
        p=f.scratch('TASK-1');(p/'nested/a').write_text('a')
        real=os.unlink
        def partial(path,*a,**kw):
            if path=='payload':raise PermissionError('controlled partial failure')
            return real(path,*a,**kw)
        with patch.object(os,'unlink',partial):r=f.sweep()
        assert row(r,p)['state']=='partial_or_unknown' and not (p/'nested/a').exists(),r
        assert r['phases']['scratch']['attribution_unknown'] and r['outcome']=='error'
        # Real blocking Git subprocess in isolated worker: a disposable Git
        # shim records its sleep child's PID; supervisor must kill both.
        repo,q=f.worktree('TASK-2');binpath=f.base/'bin';binpath.mkdir();pidfile=f.base/'sleep.pid'
        shim=binpath/'git';shim.write_text('#!/bin/sh\nsleep 20 &\nprintf "%s" "$!" > "$SWEEP_FIXTURE_PID"\nwait\n');shim.chmod(0o700)
        second=f.base/'zz-unreached-org';(second/'org').mkdir(parents=True);(second/'org/config.yaml').write_text('disk_sweep: {mode: "off"}')
        async def exercise(cancel: bool) -> dict:
            summary=f.base/('cancel.json' if cancel else 'deadline.json')
            ticks=0;budget=2;spawned={}
            real_spawn=asyncio.create_subprocess_exec
            async def observe_spawn(*args: str, **kw: object) -> asyncio.subprocess.Process:
                # Observe the ORIGINAL production window; readiness never resets it.
                spawned['deadline']=float(args[args.index('--deadline')+1])
                spawned['started']=float(args[args.index('--started-monotonic')+1])
                process=await real_spawn(*args, **kw)
                spawned['process']=process
                return process
            async def heartbeat() -> None:
                nonlocal ticks
                while True:ticks+=1;await asyncio.sleep(.01)
            tick=asyncio.create_task(heartbeat())
            with patch.object(asyncio,'create_subprocess_exec',observe_spawn):
                worker=asyncio.create_task(ds.run_worker([str(f.org),str(second)],summary,dry_run=True,budget=budget))
                ready_until=time.monotonic()+1
                try:
                    while not pidfile.exists() and not worker.done() and time.monotonic()<ready_until:
                        await asyncio.sleep(.01)
                    ready=time.monotonic()
                    assert pidfile.exists(),'blocked Git readiness missing before ORIGINAL fixture deadline'
                    deadline=spawned['deadline'];process=spawned['process']
                    assert ready<min(ready_until,deadline),(ready,ready_until,deadline)
                    assert deadline==spawned['started']+budget,spawned
                    pid=int(pidfile.read_text());proc=Path(f'/proc/{pid}/stat')
                    assert proc.exists() and not proc.read_text().split(') ')[1].startswith('Z '),'Git sleep was not runnable at readiness'
                    assert os.getpgid(pid)==process.pid,'Git sleep outside owned worker group'
                    checkpoint=json.loads(summary.read_text())
                    assert checkpoint['deadline_monotonic']==deadline,checkpoint
                    assert [item['org'] for item in checkpoint['orgs']]==[f.org.name],checkpoint
                    assert not summary.with_name(summary.name+'.pending').exists(),'blocked Git phase still publishing'
                    if cancel:worker.cancel()
                    started=time.monotonic();cancelled=False
                    try:await worker
                    except asyncio.CancelledError:cancelled=True
                    assert cancelled==cancel
                finally:
                    # An assertion failure still stops/reaps this fixture's child.
                    if not worker.done():
                        worker.cancel()
                        try:await worker
                        except asyncio.CancelledError:pass
                    tick.cancel()
                    try:await tick
                    except asyncio.CancelledError:pass
            assert time.monotonic()-started<3 and ticks>=2
            report=json.loads(summary.read_text());assert report['outcome']=='incomplete' and report['interruption']==('shutdown' if cancel else 'deadline'),report
            assert report['stop_cause'] == ('shutdown_signal' if cancel else 'budget'), report
            assert str(second) in report['unreached_orgs'],report
            assert report['budget_seconds']==budget and report['deadline_monotonic']==deadline,report
            assert cancel or time.monotonic()>=deadline,'deadline supervisor returned before ORIGINAL deadline'
            assert report['partial_mutation']=='unknown' and report['worker_pid']==process.pid,report
            assert process.returncode is not None and not Path(f'/proc/{process.pid}').exists(),'worker leader not reaped'
            assert not proc.exists() or proc.read_text().split(') ')[1].startswith('Z '),'deleting child survived'
            assert all(phase['removed_attributable_bytes']==0 for phase in report['orgs'][0]['phases'].values()),report
            assert not (p/'nested/a').exists() and (p/'nested/payload').exists() and q.exists(),'preview changed original partial effects'
            pidfile.unlink()
            return dict(interruption=report['interruption'],budget=budget,original_deadline=deadline,
                        ready_after=ready-spawned['started'],remaining_at_readiness=deadline-ready,
                        ticks=ticks,worker_reaped=True,git_child_nonrunnable=True)
        with patch.dict(os.environ,PATH=str(binpath)+':'+os.environ['PATH'],SWEEP_FIXTURE_PID=str(pidfile)):
            a=asyncio.run(exercise(False));b=asyncio.run(exercise(True))
        # Interrupt an ACTUAL admitted removal in a supervised process, after
        # one real unlink. The child imports shipping code; only the unlink
        # timing is slowed. No product test flag or fabricated helper result.
        with fixture() as g:
            target=g.scratch('TASK-1');(target/'nested/a').write_text('a')
            marker=g.base/'unlink-started';summary=g.base/'mutating.json'
            code = f"""
import json,os,time
from pathlib import Path
from runtime.daemon import disk_sweeper as ds
real=os.unlink
first=True
def slow(name,*a,**kw):
 global first
 real(name,*a,**kw)
 if first:
  first=False
  Path({str(marker)!r}).write_text(str(os.getpid()))
  time.sleep(20)
os.unlink=slow
report={{'outcome':'incomplete','orgs':[]}}
item={{}};report['orgs'].append(item)
ds.sweep_org(Path({str(g.org)!r}),time.monotonic()+10,mode='on',now={NOW!r},result=item,checkpoint=lambda:ds.atomic_json(Path({str(summary)!r}),report))
"""
            real_spawn=asyncio.create_subprocess_exec
            async def mutated_spawn(*args,**kw):
                return await real_spawn(sys.executable,'-c',code,**kw)
            async def stop_mutation():
                with patch.object(asyncio,'create_subprocess_exec',mutated_spawn):
                    task=asyncio.create_task(ds.run_worker([str(g.org)],summary,dry_run=False,budget=3))
                    for _ in range(150):
                        if marker.exists():break
                        await asyncio.sleep(.01)
                    assert marker.exists(),'no actual unlink was observed'
                    task.cancel()
                    try:await task
                    except asyncio.CancelledError:pass
                report=json.loads(summary.read_text())
                assert report['interruption']=='shutdown' and report['outcome']=='incomplete'
                assert report['stop_cause'] == 'shutdown_signal', report
                assert report['orgs'][0]['candidates'][0]['state']=='mutation_in_flight',report
                detail = report['orgs'][0]['candidates'][0]['measurement']
                assert detail['final_reason'] == 'shutdown_signal' and detail['effects_after_checkpoint'] == 'unknown', detail
                assert detail['duration_seconds'] > 0 and not detail['confirmed_effect'], detail
                assert not (target/'nested/a').exists() and (target/'nested/payload').exists()
                pid=int(marker.read_text());assert not Path(f'/proc/{pid}').exists(),'mutating worker survived'
            asyncio.run(stop_mutation())
        return dict(comparisons=comparisons, partial='real earlier unlink observed, no false reclaimed bytes',supervisor=[a,b,'shutdown after actual unlink; surviving file preserved, mutating worker reaped'],event_loop='heartbeat remained responsive; Git/sleep child absent or zombie, no runnable worker')


def c13():
    with fixture() as f:
        repo,p=f.worktree('TASK-1');f.git(p,'checkout','--detach','-q');(p/'source').write_text('unique');f.git(p,'add','source');f.git(p,'commit','-qm','detached')
        retained(f.sweep(),p,'unique_head_commit')
        f.git(repo,'branch','retained',f.git(p,'rev-parse','HEAD').strip())
        r=f.sweep();assert row(r,p)['state']=='removed',r
        assert f.git(repo,'rev-parse','retained').strip()
        return 'Unique detached commit retained; same detached HEAD removable after an actual retained local ref is created.'


def c14():
    with fixture() as f:
        for n in range(1,51):
            p=f.scratch(f'TASK-{n}','superseded');os.utime(p,(time.time()-20000-n,)*2)
        p=f.scratch('TASK-100')
        r=f.sweep(seconds=12);assert row(r,p)['state']=='removed' and len(r['candidates'])==51,r
        q=f.scratch('TASK-101');r={}
        try:ds.sweep_org(f.org,time.monotonic()+.000001,mode='on',now=NOW,result=r)
        except fs.Expired:pass
        assert r['outcome']=='incomplete' and r['discovery']=='unknown' and r['unreached'] is None and q.exists(),r
        return '50 older refused roots precede actual removal within 12 seconds; separately exhausted discovery reports unknown/unreached, without cursor or refill.'


def c15():
    """First-night reproducer: known scratch/repos survive non-Git siblings."""
    observations=[]
    for mode in ('dry_run', 'on'):
        with fixture() as f:
            p=f.scratch('TASK-1')
            a,x=f.worktree('TASK-2','a-project')
            z,y=f.worktree('TASK-3','z-project')
            protected=[]
            for name in ('.happyranch','output'):
                root=f.workspace/'repos'/name
                (root/'TASK-8928').mkdir(parents=True)
                (root/'TASK-8928/keep').write_text('protected noncandidate')
                protected.append(root)
            # Detect accidental Git reads in protected noncandidate children.
            before=candidate_contents(p,x,y,*protected)
            controls=candidate_contents(*protected)
            registrations={str(repo):f.git(repo,'worktree','list','--porcelain') for repo in (a,z)}
            calls=[]
            realgit=ds.git
            def trace(repo,deadline,*args):
                calls.append((str(repo),args))
                value=realgit(repo,deadline,*args)
                assert repo not in protected, ('protected Git traversal',repo)
                return value
            with patch.object(ds,'git',trace):
                if mode=='dry_run':
                    with refuse_mutations(): r=f.sweep(mode)
                else: r=f.sweep(mode)
            assert all(repo not in {str(v) for v in protected} for repo,args in calls),calls
            assert not any(args[:2]==('worktree','prune') for repo,args in calls),calls
            assert r['outcome']=='incomplete' and r['discovery']=='partial',r
            assert {c['path'] for c in r['candidates']}=={str(p),str(x),str(y)},r
            assert [c['path'] for c in r['candidates']]==[str(p),str(x),str(y)],r
            scopes=r['unknown_scope']['discovery_scopes']
            assert {s['path'] for s in scopes}=={str(v) for v in protected},r
            assert all(s['candidate_count'] is None and s['reason'] for s in scopes),r
            assert r['known_candidate_count']==3 and r['known_unreached_count']==0,r
            assert r['unreached'] is None,r
            assert candidate_contents(*protected)==controls
            if mode=='dry_run':
                assert candidate_contents(p,x,y,*protected)==before
                assert all(row(r,v)['state']=='eligible' for v in (p,x,y)),r
                assert all(f.git(repo,'worktree','list','--porcelain')==registrations[str(repo)] for repo in (a,z))
                other=f.base/'zz-independent';(other/'workspaces').mkdir(parents=True)
                summary=f.base/'summary.json'
                with refuse_mutations():
                    aggregate=ds.sweep([str(f.org),str(other)],time.monotonic()+5,summary,dry_run=True,scheduled_date=None,revision='fixture')
                assert aggregate['outcome']=='incomplete' and aggregate['unreached_orgs']==[],aggregate
                assert aggregate['orgs'][1]['outcome']=='complete',aggregate
                assert json.loads(summary.read_text())==aggregate
                assert ds._audit_payload(aggregate,aggregate['orgs'][0],summary)['org']['discovery_scopes']==scopes
            else:
                assert all(row(r,v)['state']=='removed' and not v.exists() for v in (p,x,y)),r
                assert all(f.git(repo,'show-ref','--verify',f'refs/heads/task/{v.name}') for repo,v in ((a,x),(z,y)))
            observations.append(dict(mode=mode,known=3,unknown_scopes=scopes,states=[c['state'] for c in r['candidates']],git_calls=len(calls)))
    return observations


def c16():
    """Real inaccessible/broken Git and workspace scopes; aliases stay literal."""
    observed={}
    with fixture() as f:
        target=f.scratch('TASK-1')
        outside=f.base/'outside';outside.mkdir();(outside/'keep').write_text('durable')
        shutil.rmtree(target);target.symlink_to(outside,target_is_directory=True)
        before=candidate_contents(outside)
        with refuse_mutations(): r=f.sweep()
        retained(r,target,'Not a directory')
        assert before==candidate_contents(outside)
        observed['scratch_root_alias']='literal root refused; outside target unchanged'
    with fixture() as f:
        p=f.scratch('TASK-1')
        repo,q=f.worktree('TASK-2','z-project')
        repos=f.workspace/'repos'
        broken=repos/'b-broken';(broken/'.git').mkdir(parents=True)
        # A broken .git may cause real Git to find a valid ancestor instead.
        f.git(f.base,'init','-q')
        unreadable=repos/'c-unreadable';(unreadable/'.git').mkdir(parents=True);(unreadable/'.git').chmod(0)
        alias=repos/'a-alias';alias.symlink_to(repo,target_is_directory=True)
        file=repos/'d-file';file.write_text('not a repository')
        git_alias=repos/'e-git-alias';git_alias.mkdir();(git_alias/'.git').symlink_to(repo/'.git',target_is_directory=True)
        owner_file=f.org/'workspaces/aa-file';owner_file.write_text('not a workspace')
        owner_alias=f.org/'workspaces/ab-alias';owner_alias.symlink_to(f.workspace,target_is_directory=True)
        # A failed scratch subtree must not hide this workspace's Git roots.
        tmp=p.parent;tmp.chmod(0)
        before=candidate_contents(q,broken,file,owner_file)
        realgit=ds.git;calls=[]
        def trace(repo,deadline,*args):
            calls.append(str(repo))
            assert repo not in (alias,owner_alias), ('followed alias',repo)
            return realgit(repo,deadline,*args)
        with patch.object(ds,'git',trace),refuse_mutations(): r=f.sweep('dry_run')
        assert r['outcome']=='incomplete' and row(r,q)['state']=='eligible',r
        expected={tmp,broken,unreadable,alias,file,git_alias,owner_file,owner_alias}
        scopes=r['unknown_scope']['discovery_scopes']
        assert {s['path'] for s in scopes}=={str(v) for v in expected},r
        assert all(s['candidate_count'] is None and s['reason'] for s in scopes),r
        assert before==candidate_contents(q,broken,file,owner_file)
        assert str(broken) in calls,calls
        assert not {str(v) for v in (alias,unreadable,git_alias,owner_alias)} & set(calls),calls
        assert all(c['path']!=str(p) for c in r['candidates']),r
        observed.update(known=1,unknown_scopes=scopes,alias_git_calls=0)
    with fixture() as f:
        repo,q=f.worktree('TASK-1')
        external=f.base/'external';(external/'task-tmp/TASK-999').mkdir(parents=True)
        (external/'task-tmp/TASK-999/keep').write_text('durable alias target')
        (f.workspace/'.happyranch').symlink_to(external,target_is_directory=True)
        before=candidate_contents(external,q)
        with refuse_mutations(): r=f.sweep('dry_run')
        assert r['outcome']=='incomplete' and row(r,q)['state']=='eligible',r
        assert len(r['candidates'])==1 and len(r['discovery_scopes'])==1,r
        assert r['discovery_scopes'][0]['path']==str(f.workspace/'.happyranch/task-tmp'),r
        assert candidate_contents(external,q)==before
        observed['scratch_ancestor_alias']='retained; external target unchanged; independent worktree eligible'
    with fixture() as f:
        p=f.scratch('TASK-1')
        repo,q=f.worktree('TASK-2')
        external=f.base/'protected-worktrees'
        q.parent.rename(external)
        q.parent.symlink_to(external,target_is_directory=True)
        before=candidate_contents(external,p)
        with refuse_mutations(): r=f.sweep('dry_run')
        assert r['outcome']=='incomplete' and row(r,p)['state']=='eligible',r
        assert len(r['candidates'])==1 and r['discovery_scopes'][0]['path']==str(repo),r
        assert candidate_contents(external,p)==before
        observed['worktree_parent_alias']='retained unknown; no candidate stat through alias; independent scratch eligible'
    return observed


def c17():
    """Actual Git timeout midway through discovery is a global stop."""
    with fixture() as f:
        p=f.scratch('TASK-1')
        repo,q=f.worktree('TASK-2','a-project')
        late,z=f.worktree('TASK-3','z-project')
        blocker=f.workspace/'repos/m-blocker';(blocker/'.git').mkdir(parents=True)
        binpath=f.base/'bin';binpath.mkdir()
        realgit=shutil.which('git')
        shim=binpath/'git'
        shim.write_text('#!'+sys.executable+'\nimport os,sys,time\nif '+repr(str(blocker))+' in sys.argv: time.sleep(2)\nos.execv('+repr(realgit)+',["git",*sys.argv[1:]])\n')
        shim.chmod(0o700)
        later=f.base/'zz-org';(later/'org').mkdir(parents=True);(later/'org/config.yaml').write_text('disk_sweep: {mode: "off"}')
        summary=f.base/'report.json'
        start=time.monotonic();deadline=start+.3
        with patch.dict(os.environ,PATH=str(binpath)+os.pathsep+os.environ['PATH']),refuse_mutations():
            r=ds.sweep([str(f.org),str(later)],deadline,summary,dry_run=True,scheduled_date=None,revision='fixture',started_monotonic=start)
        item=r['orgs'][0]
        assert r['outcome']=='incomplete' and r['unreached_orgs']==[str(later)],r
        assert r['deadline_monotonic']==deadline and r['budget_seconds']==deadline-start,r
        assert item['discovery']=='unknown' and item['unreached'] is None,item
        assert item['unknown_scope']['unfinished_discovery_root']==str(f.org/'workspaces'),item
        assert item['known_candidate_count']==item['known_unreached_count']==2,item
        assert {c['path'] for c in item['candidates']}=={str(p),str(q)},item
        assert all(c['state']=='unreached' for c in item['candidates']),item
        assert any(s['path']==str(blocker) and 'deadline' in s['reason'] for s in item['unknown_scope']['discovery_scopes']),item
        assert all(v.exists() for v in (p,q,z))
        assert time.monotonic()-start<2
        assert ds._audit_payload(r,item,summary)['org']==item
        return dict(seconds=time.monotonic()-start,known_unreached=2,unknown_total=None,unreached_orgs=r['unreached_orgs'],discovery_scopes=item['unknown_scope']['discovery_scopes'])


def c18():
    """No count cap: many real uncertain repos and refused roots precede work."""
    with fixture() as f:
        for n in range(1,31):
            p=f.scratch(f'TASK-{n}','superseded');os.utime(p,(time.time()-20000-n,)*2)
        eligible=f.scratch('TASK-100')
        repo,q=f.worktree('TASK-101','z-project')
        for n in range(35):
            root=f.workspace/'repos'/f'a-unknown-{n:02d}'
            root.mkdir();(root/'keep').write_text('protected')
        start=time.monotonic();r=f.sweep(seconds=12)
        assert r['outcome']=='incomplete' and r['discovery']=='partial',r
        assert len(r['unknown_scope']['discovery_scopes'])==35,r
        assert r['retained_count']==30 and r['known_candidate_count']==32,r
        assert all(row(r,v)['state']=='removed' and not v.exists() for v in (eligible,q)),r
        ordered=sorted(r['candidates'],key=lambda c:(c['phase']!='scratch',c['oldest'],c['path']))
        assert ordered==r['candidates'],r
        assert all((f.workspace/'repos'/f'a-unknown-{n:02d}/keep').read_text()=='protected' for n in range(35))
        return dict(seconds=time.monotonic()-start,unknown_scopes=35,retained=30,removed=2,unknown_total=None)


@contextmanager
def damaged_registration(f, repo, path, kind):
    """Task-owned real metadata damage; restore only our exact fixture changes."""
    admin=repo/'.git/worktrees';entry=admin/path.name
    restore=[]
    external=f.base/('external-'+kind);external.mkdir()
    sentinel=external/'sentinel';sentinel.write_bytes(b'external control');sentinel.chmod(0o400)
    if kind in ('missing_admin','missing_entry'):
        target=admin if kind=='missing_admin' else entry
        held=external/'held';target.rename(held)
        restore.append(lambda: held.rename(target))
    elif kind=='admin_alias':
        # QA's valid escape reproducer, with working absolute commondir.
        for e in admin.iterdir():
            pointer=e/'commondir';original=pointer.read_bytes()
            restore.append(lambda pointer=pointer,original=original: pointer.write_bytes(original))
            pointer.write_text(str(repo/'.git')+'\n')
        held=external/'held';admin.rename(held);admin.symlink_to(held,target_is_directory=True)
        restore.append(lambda: (admin.unlink(),held.rename(admin)))
    elif kind in ('entry_alias','gitdir_alias','commondir_alias','backlink_alias'):
        target={'entry_alias':entry,'gitdir_alias':entry/'gitdir','commondir_alias':entry/'commondir','backlink_alias':path/'.git'}[kind]
        held=external/'held';target.rename(held);target.symlink_to(held,target_is_directory=kind=='entry_alias')
        restore.append(lambda: (target.unlink(),held.rename(target)))
    elif kind.startswith('unreadable_'):
        target={'unreadable_admin':admin,'unreadable_entry':entry,'unreadable_gitdir':entry/'gitdir','unreadable_index':entry/'index'}[kind]
        old=target.stat().st_mode & 0o777;target.chmod(0)
        restore.append(lambda: target.chmod(old))
    elif kind in ('pointer_hardlink', 'index_hardlink'):
        target = entry / ('gitdir' if kind == 'pointer_hardlink' else 'index')
        alias = external / 'hardlink'; os.link(target, alias)
        restore.append(alias.unlink)
    elif kind == 'duplicate_target':
        target = admin / 'duplicate'; shutil.copytree(entry, target)
        restore.append(lambda: shutil.rmtree(target))
    elif kind=='extra_entry':
        target=admin/'unexpected';target.mkdir();(target/'keep').write_text('not a registration')
        restore.append(lambda: ((target/'keep').unlink(),target.rmdir()))
    else:
        target=path/'.git' if kind=='conflicting_backlink' else entry/('HEAD' if kind in ('missing_head','malformed_head') else 'commondir' if kind=='missing_commondir' else 'gitdir')
        old=target.read_bytes()
        if kind.startswith('missing_'): target.unlink()
        else: target.write_text('gitdir: /conflicting\n' if kind=='conflicting_backlink' else 'a'*41+'\n' if kind=='malformed_head' else 'relative malformed\n')
        restore.append(lambda: target.write_bytes(old))
    try:
        yield dict(sentinel=sentinel,external=external)
    finally:
        for action in reversed(restore):action()


def descendant_setup(f, *, nested=True):
    """Exact F1 topology; local excludes belong only to this disposable primary."""
    repo, outer = f.worktree('TASK-1')
    base_head = f.git(repo, 'rev-parse', 'HEAD').strip()
    f.git(repo, 'branch', 'fixture-base', base_head)
    (outer / 'source').write_text('distinct outer commit\n')
    f.git(outer, 'commit', '-qam', 'outer fixture role')
    outer_head = f.git(outer, 'rev-parse', 'HEAD').strip()
    assert outer_head != base_head
    exclude = repo / '.git/info/exclude'
    exclude.write_bytes(exclude.read_bytes() + b'\n/.claude/worktrees/\n')
    (outer / 'ignored').mkdir()
    (outer / 'ignored/payload').write_bytes(b'outer ignored payload' * 1024)
    candidate = dict(path=str(outer), repo=str(repo), workspace=str(f.workspace), phase='worktree')
    deadline = time.monotonic() + 12
    # The original ordinary gates admit before adding a different registration.
    ds.admit(candidate, ds.Evidence(f.org, deadline, writing=True), deadline, now=NOW)
    baseline = outer / '.claude/worktrees/TASK-1-baseline'
    if nested:
        descendant_add(f, repo, baseline, base_head)
        descendant_ready(f, repo, outer, baseline, outer_head, base_head)
    return repo, outer, baseline, outer_head, base_head


def descendant_add(f, repo, baseline, base_head):
    baseline.parent.mkdir(parents=True, exist_ok=True)
    f.git(repo, 'worktree', 'add', '--detach', str(baseline), base_head)
    (baseline / 'ignored').mkdir()
    (baseline / 'ignored/payload').write_bytes(b'detached ignored payload' * 1024)


def descendant_ready(f, repo, outer, baseline, outer_head, base_head):
    deadline = time.monotonic() + 12
    assert f.git(outer, 'check-ignore', str(baseline)).strip() == str(baseline)
    for tree, head in ((outer, outer_head), (baseline, base_head)):
        assert f.git(tree, 'status', '--porcelain', '--untracked-files=all') == ''
        assert f.git(tree, 'rev-parse', 'HEAD').strip() == head
        assert f.git(tree, 'rev-list', '-n1', 'HEAD', '--not', '--branches', '--remotes') == ''
        observed = fs.snapshot(tree, deadline)
        assert all(max(v.st_ctime, v.st_mtime) <= NOW - 3600 for v in observed.values())
        assert all(stat.S_ISDIR(v.st_mode) or v.st_nlink == 1 for v in observed.values())
    assert f.git(repo, 'rev-parse', 'refs/heads/task/TASK-1').strip() == outer_head
    assert f.git(repo, 'rev-parse', 'refs/heads/fixture-base').strip() == base_head
    rows = ds.registrations(repo, deadline)
    assert set(rows) == {str(repo), str(outer), str(baseline)}, rows
    assert 'detached' in rows[str(baseline)] and 'locked' not in rows[str(baseline)]
    assert all('prunable' not in value for value in rows.values())
    ds.Evidence(f.org, deadline, writing=True).ownership(outer, f.workspace, 'TASK-1')


def descendant_snapshot(f, repo, *trees):
    # Types/modes/bytes include roots, both backlinks, complete admin and local
    # excludes. Times intentionally excluded: exact fixture moves change ctime.
    return dict(contents=candidate_contents(*trees, repo / '.git/worktrees', repo / '.git/info/exclude'),
                heads=[f.git(tree, 'rev-parse', 'HEAD') for tree in trees],
                refs=f.git(repo, 'show-ref'),
                porcelain=f.git(repo, 'worktree', 'list', '--porcelain', '-z'))


def registration_lock_peer():
    """C19-R: settle a real Git writer only at its observed lock read."""
    with fixture() as f:
        repo, a = f.worktree('TASK-1'); _, b = f.worktree('TASK-2')
        entry = repo / '.git/worktrees' / a.name
        b_entry = repo / '.git/worktrees' / b.name
        lock = entry / 'index.lock'
        deadline = time.monotonic() + 20
        assert set(ds.registrations(repo, deadline)) == {str(repo), str(a), str(b)}
        for path in (a, b):
            ds.admit(dict(path=str(path), repo=str(repo), workspace=str(f.workspace), phase='worktree'),
                     ds.Evidence(f.org, deadline, writing=True), deadline, now=NOW)
        refs = f.git(repo, 'show-ref'); a_before = candidate_contents(a)
        index_before = (entry / 'index').read_bytes()
        record = f.git(a, 'ls-tree', 'HEAD', '--', 'source').encode()
        assert record.startswith(b'100644 blob ') and record.endswith(b'\tsource\n')
        writer = None
        events = []; witnessed = []; delegated = []
        real_snapshot, real_metadata = ds.snapshot, ds._metadata_file
        previous_alarm = signal.getsignal(signal.SIGALRM)
        def timeout(signum, frame):
            raise TimeoutError('C19-R original witness budget; missing witness is not RED/PASS')
        signal.signal(signal.SIGALRM, timeout)
        signal.setitimer(signal.ITIMER_REAL, max(.001, deadline - time.monotonic() - 3))
        try:
            env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
            writer = subprocess.Popen(['git', '-C', str(a), 'update-index', '--index-info'],
                                      stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
            capacity = fcntl.fcntl(writer.stdin.fileno(), fcntl.F_GETPIPE_SZ)
            payload = record * (capacity // len(record) + 2)
            assert len(payload) > capacity
            writer.stdin.write(payload); writer.stdin.flush()
            assert writer.poll() is None
            stamp = fs.signature(lock.lstat())
            events.append('actual writer owns lock')
            def snapshot(path, limit, **kwargs):
                captured = real_snapshot(path, limit, **kwargs)
                if not delegated and Path(path) in (repo / '.git/worktrees', entry):
                    relative = a.name + '/index.lock' if Path(path) != entry else 'index.lock'
                    assert relative in captured, 'actual census did not observe the pending lock'
                    assert fs.signature(captured[relative]) == stamp
                    witnessed.append(True); events.append('real census observed exact lock')
                return captured
            def metadata(path, limit, **kwargs):
                if Path(path) == lock and not delegated:
                    assert witnessed and fs.signature(lock.lstat()) == stamp and writer.poll() is None
                    events.append('original reader reached exact lock')
                    writer.stdin.close(); writer.stdin = None
                    _, stderr = writer.communicate(timeout=max(.001, min(5, deadline - time.monotonic())))
                    assert writer.returncode == 0, stderr
                    events.append('exact writer exit0 and reaped')
                    try: lock.lstat()
                    except FileNotFoundError as exc: assert exc.errno == 2
                    else: raise AssertionError('Git did not release its lock')
                    delegated.append(True); events.append('literal lock disappeared')
                    try: return real_metadata(path, limit, **kwargs)
                    except ds.Retain as exc:
                        assert isinstance(exc.__cause__, FileNotFoundError) and exc.__cause__.errno == 2, exc
                        events.append('real reader native ENOENT'); raise
                return real_metadata(path, limit, **kwargs)
            with patch.object(ds, 'snapshot', snapshot), patch.object(ds, '_metadata_file', metadata):
                report = f.sweep(seconds=max(.001, deadline - time.monotonic()))
            assert witnessed and delegated and events[-1] == 'real reader native ENOENT', (events, report)
            # This is the intended baseline failure, after the actual witness.
            assert not os.path.lexists(b) and not os.path.lexists(b_entry), ('C19-R eligible B was not physically removed/unregistered AFTER real lock witness', events, report)
            assert str(b) not in f.git(repo, 'worktree', 'list', '--porcelain', '-z')
            assert row(report, b)['state'] == 'removed', report
            retained(report, a, str(lock))
            assert 'No such file' in row(report, a)['reason'], report
            assert a_before == candidate_contents(a) and refs == f.git(repo, 'show-ref')
            assert not os.path.lexists(lock) and writer.returncode == 0
            assert report['discovery'] == 'partial' and report['unreached'] is None, report
            return dict(case='C19-R', events=events, peer='physically absent/admin absent/unregistered/ref retained',
                        writer_exit=writer.returncode, index_rewritten_by_git=index_before != (entry / 'index').read_bytes())
        finally:
            # Always inside fixture lifecycle: reap ONLY this exact owned child.
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, previous_alarm)
            if writer is not None:
                close_error = None
                if writer.stdin is not None:
                    try: writer.stdin.close()
                    except OSError as exc: close_error = exc
                    finally: writer.stdin = None
                try: writer.communicate(timeout=1)
                except subprocess.TimeoutExpired:
                    try: writer.terminate()
                    except ProcessLookupError: pass
                    try: writer.communicate(timeout=1)
                    except subprocess.TimeoutExpired:
                        try: writer.kill()
                        except ProcessLookupError: pass
                        writer.communicate(timeout=1)
                if close_error is not None: raise close_error


def registration_signature_diagnostic(kind: str) -> dict:
    """C19-L/P: real reader deltas, with an independent no-follow stat oracle."""
    assert kind in ('snapshot', 'pre_git', 'post_git', 'appeared', 'disappeared', 'equal', 'first_cause', 'report')
    with fixture() as f:
        started = time.monotonic()
        deadline = started + 20
        commands, events, pairs, reads = [], [], [], []

        def fixture_git(root: Path, *args: str) -> str:
            ds.check(deadline)
            child = subprocess.run(['git', '-C', str(root), *args], capture_output=True, text=True,
                                   timeout=max(.001, min(8, deadline - time.monotonic())))
            commands.append(dict(args=args, exit=child.returncode))
            child.check_returncode()
            ds.check(deadline)
            return child.stdout

        f.git = fixture_git
        repo, a = f.worktree('TASK-1'); _, b = f.worktree('TASK-2')
        entry = repo / '.git/worktrees' / a.name
        note, index = entry / 'fixture-note', entry / 'index'
        if kind != 'appeared': note.write_text('before\n')
        refs = f.git(repo, 'show-ref')
        original_chmod = os.chmod
        original_mode = index.lstat().st_mode
        if kind == 'first_cause': index.chmod(0)
        native_error = []

        def raw(path: Path) -> dict | None:
            try: value = os.stat(path, follow_symlinks=False)
            except FileNotFoundError: return None
            return dict(device=value.st_dev, inode=value.st_ino, type=stat.S_IFMT(value.st_mode),
                        mode=value.st_mode, size=value.st_size, mtime_ns=value.st_mtime_ns,
                        ctime_ns=value.st_ctime_ns, nlink=value.st_nlink)

        def change(seam: str) -> None:
            ds.check(deadline)
            old = raw(note)
            if kind == 'appeared': note.write_text('new ordinary auxiliary\n')
            elif kind == 'disappeared': note.unlink()
            else:
                with note.open('a') as stream: stream.write('actual append\n')
                # Intentionally test-owned mode/mtime writes, never sweeper repair.
                original_chmod(note, 0o640)
                os.utime(note, ns=(note.lstat().st_atime_ns, old['mtime_ns'] + 1_000_000))
            new = raw(note)
            assert old != new, (kind, old, new)
            pairs.append(dict(before=old, after=new))
            events.append(seam)

        real_snapshot, real_metadata, real_git, real_registrations = ds.snapshot, ds._registration_metadata, ds.git, ds.registrations
        census_count = 0

        def snapshot(path: Path, limit: float, **kwargs) -> dict:
            assert limit == deadline
            tree = real_snapshot(path, limit, **kwargs)
            if kind in ('snapshot', 'report') and Path(path) == repo / '.git/worktrees' and not pairs:
                # Check the independent observation agrees with the captured census.
                value = tree[a.name + '/fixture-note']
                assert raw(note) == dict(device=value.st_dev, inode=value.st_ino, type=stat.S_IFMT(value.st_mode),
                                         mode=value.st_mode, size=value.st_size, mtime_ns=value.st_mtime_ns,
                                         ctime_ns=value.st_ctime_ns, nlink=value.st_nlink)
                change('complete snapshot -> ordinary read')
            return tree

        def metadata(root: Path, limit: float, **kwargs) -> ds._Registrations:
            nonlocal census_count
            assert limit == deadline
            observed = real_metadata(root, limit, **kwargs)
            census_count += 1
            if census_count == 1 and kind in ('pre_git', 'appeared', 'disappeared', 'first_cause'):
                before = raw(note)
                assert observed.metadata[str(a)].get(str(note)) == (tuple(before.values()) if before else None)
                if kind == 'first_cause':
                    native_error.append(observed.errors[str(a)])
                    assert str(index) in native_error[0] and 'Permission denied' in native_error[0]
                    original_chmod(index, stat.S_IMODE(original_mode))
                change('first complete census -> second census')
            return observed

        def git(root: Path, limit: float, *args: str) -> str:
            assert limit == deadline
            value = real_git(root, limit, *args)
            commands.append(dict(args=args, exit=0, shipping=True))
            if args[:2] == ('worktree', 'list'):
                events.append('genuine Git list returned')
                if kind == 'post_git' and not pairs: change('successful Git list -> final census')
            return value

        def registrations(root: Path, limit: float, **kwargs) -> ds._Registrations:
            observed = real_registrations(root, limit, **kwargs)
            reads.append(dict(observed.errors))
            return observed

        observed = dict(kind=kind, events=events, pairs=pairs, commands=commands, reader_errors=reads,
                        original_deadline=deadline, fixture=str(f.base))
        latched = {}
        try:
            with patch.object(ds, 'snapshot', snapshot), patch.object(ds, '_registration_metadata', metadata), \
                    patch.object(ds, 'git', git), patch.object(ds, 'registrations', registrations):
                if kind == 'report':
                    summary = f.base / 'policy-summary.json'
                    with refuse_mutations(), patch.object(time, 'time', return_value=NOW):
                        projected = ds.sweep([str(f.org)], deadline, summary, dry_run=True,
                                             scheduled_date=None, revision='signature-fixture', started_monotonic=started)
                        ds._manual_audit(projected, [str(f.org)], summary)
                    report = projected['orgs'][0]
                    payload = ds._audit_payload(projected, report, summary)
                    saved = f.db.execute("SELECT task_id,agent,action,payload FROM audit_log WHERE action='disk_sweep_summary'").fetchall()
                    observed.update(org=report, audit=payload)
                    assert payload['org'] is report
                    assert len(saved) == 1 and saved[0][:3] == ('', 'daemon', 'disk_sweep_summary'), observed
                    assert json.loads(saved[0][3]) == payload and json.loads(summary.read_text()) == projected, observed
                    reason = row(report, a)['reason']
                    assert reason == reads[0][str(a)], observed
                    assert report['discovery'] == 'partial' and report['outcome'] == 'incomplete', observed
                    assert report['known_candidate_count'] == 2 and report['known_unreached_count'] == 0, observed
                    assert report['unreached'] is None and report['physical_freed_bytes'] is None, observed
                    assert report['retained_count'] == 1 and report['retained_reasons'] == {'metadata_changed': 1}, observed
                    assert row(report, a).get('allocated_bytes') is None and row(report, b)['state'] == 'eligible', observed
                    assert all(p['removed_attributable_bytes'] == 0 and not p['attribution_unknown'] for p in report['phases'].values()), observed
                    scope = next(s for s in report['discovery_scopes'] if s['path'] == str(a))
                    assert scope['reason'] == reason and scope['candidate_count'] is None, observed
                    assert report['unknown_scope']['discovery_scopes'] == report['discovery_scopes'], observed
                else:
                    rows = ds.registrations(repo, deadline, retained=latched)
                    assert set(rows) == {str(repo), str(a), str(b)} and str(b) not in rows.errors, observed
                    reason = rows.errors.get(str(a))
                    if kind == 'equal':
                        assert reason is None and not rows.errors and not latched[str(repo)], observed
                    elif kind == 'first_cause':
                        assert pairs and reason == native_error[0] and ';signature_change=' not in reason, observed
                        again = ds.registrations(repo, deadline, retained=latched)
                        assert again.errors[str(a)] == reason and latched[str(repo)][str(a)][0] == reason, observed
                        observed['native_first_cause'] = reason
            observed.update(censuses=census_count, cause=reason)
            assert refs == f.git(repo, 'show-ref') and a.exists() and b.exists(), observed
            if kind not in ('equal', 'first_cause'):
                before, after = pairs[0]['before'], pairs[0]['after']
                fields = before if before is not None else after
                expected = {field: [before[field] if before is not None else None, after[field] if after is not None else None]
                            for field in fields if (before[field] if before is not None else None) != (after[field] if after is not None else None)}
                label = 'snapshot_to_read' if kind in ('snapshot', 'report') else 'post_git_census' if kind == 'post_git' else 'pre_git_censuses'
                expected_reason = 'metadata_changed:' + str(note) + ';signature_change=' + json.dumps(
                    dict(comparison=label, changes=expected), separators=(',', ':'))
                assert reason == expected_reason, observed
                observed['expected_changes'] = expected
                if kind == 'post_git': assert events.index('genuine Git list returned') < events.index('successful Git list -> final census'), observed
                if kind in ('appeared', 'disappeared'): assert len(expected) == 8, observed
            ds.check(deadline)
            observed['seconds'] = time.monotonic() - started
            return observed
        finally:
            original_chmod(index, stat.S_IMODE(original_mode))


def registration_local_peers():
    """C19-L/E/P: a proven local unknown never grants or vetoes peer eligibility."""
    observations = [registration_signature_diagnostic(kind) for kind in
                    ('snapshot', 'pre_git', 'post_git', 'appeared', 'disappeared', 'equal', 'first_cause', 'report')]
    scenarios = [('unreadable_index', None), ('missing_head', None), ('changed_note', None)]
    scenarios += [('unreadable_index', protection) for protection in
                  ('active_owner', 'active_root', 'pending', 'queued', 'running', 'dirty', 'untracked')]
    for kind, protection in scenarios:
        for preview in (False, True):
            with fixture() as f, ExitStack() as changes:
                repo, a = f.worktree('TASK-1'); _, b = f.worktree('TASK-2')
                entry = repo / '.git/worktrees' / a.name
                note = entry / 'fixture-note'
                if kind == 'changed_note': note.write_text('before\n')
                c = None
                if protection:
                    _, c = f.worktree('TASK-3')
                    if protection == 'active_owner':
                        f.db.execute("UPDATE tasks SET status='in_progress' WHERE id='TASK-3'")
                    elif protection == 'active_root':
                        f.task('TASK-90', status='in_progress')
                        f.db.execute("UPDATE tasks SET parent_task_id='TASK-90' WHERE id='TASK-3'")
                    elif protection in ('pending', 'queued', 'running'):
                        f.db.execute('INSERT INTO jobs VALUES(?,?)', ('TASK-3', protection))
                    elif protection == 'dirty': (c / 'source').write_text('changed tracked bytes\n')
                    else: (c / 'untracked').write_text('keep nonignored\n')
                    f.db.commit()
                refs = f.git(repo, 'show-ref')
                before = candidate_contents(a, entry, *([c] if c else []))
                if kind != 'changed_note':
                    damage = changes.enter_context(damaged_registration(f, repo, a, kind))
                    sentinel = candidate_contents(damage['external'])
                    if kind == 'unreadable_index':
                        try: (entry / 'index').read_bytes()
                        except PermissionError as exc: assert exc.errno == 13
                        else: raise AssertionError('ordinary UID did not observe actual index EACCES')
                changed = []; calls = []; real_snapshot, realgit = ds.snapshot, ds.git
                def snapshot(path, deadline, **kwargs):
                    tree = real_snapshot(path, deadline, **kwargs)
                    if kind == 'changed_note' and Path(path) == repo / '.git/worktrees' and not changed:
                        with note.open('a') as stream: stream.write('actual append\n')
                        changed.append(True)
                    return tree
                def git(root, deadline, *args):
                    calls.append((str(root), args)); return realgit(root, deadline, *args)
                with patch.object(ds, 'snapshot', snapshot), patch.object(ds, 'git', git):
                    if preview:
                        with refuse_mutations():
                            if kind == 'unreadable_index' and protection is None:
                                projected, summary = policy_sweep(f, dry_run=True)
                                ds._manual_audit(projected, [str(f.org)], summary)
                                report = projected['orgs'][0]
                                saved = f.db.execute("SELECT task_id,agent,payload FROM audit_log WHERE action='disk_sweep_summary'").fetchall()
                                assert len(saved) == 1 and saved[0][:2] == ('', 'daemon')
                                assert json.loads(saved[0][2])['org'] == report
                                assert json.loads(summary.read_text()) == projected
                            else: report = f.sweep('dry_run')
                    else: report = f.sweep()
                target = entry / ('HEAD' if kind == 'missing_head' else 'fixture-note' if kind == 'changed_note' else 'index')
                retained(report, a, str(target))
                reason = row(report, a)['reason']
                assert ('Permission denied' if kind == 'unreadable_index' else 'No such file' if kind == 'missing_head' else 'metadata_changed:') in reason, reason
                assert report['discovery'] == 'partial' and report['unreached'] is None, report
                assert report['known_unreached_count'] == 0 and report['known_candidate_count'] == (3 if c else 2), report
                assert row(report, a).get('allocated_bytes') is None and report['physical_freed_bytes'] is None, report
                assert any(scope['path'] == str(a) and str(target) in scope['reason'] and scope['candidate_count'] is None for scope in report['discovery_scopes']), report
                if preview:
                    assert row(report, b)['state'] == 'eligible' and b.exists()
                else:
                    assert row(report, b)['state'] == 'removed' and not os.path.lexists(b)
                    assert not os.path.lexists(repo / '.git/worktrees' / b.name)
                    assert str(b) not in f.git(repo, 'worktree', 'list', '--porcelain', '-z')
                    assert row(report, b)['removed_attributable_bytes'] > 0
                if c:
                    expected = 'task_or_root_not_terminal' if protection in ('active_owner', 'active_root') else 'active_job:' + protection if protection in ('pending', 'queued', 'running') else 'dirty_or_untracked'
                    retained(report, c, expected)
                    if protection in ('dirty', 'untracked'): assert row(report, c)['dirty_paths']
                assert not any(args[:2] == ('worktree', 'prune') for _, args in calls), calls
                assert not any(args[:2] == ('worktree', 'remove') and str(a) in args for _, args in calls), calls
                if kind != 'changed_note':
                    assert sentinel == candidate_contents(damage['external'])
                    changes.close()
                else:
                    assert changed and note.read_text() == 'before\nactual append\n'
                    before[str(note)] = (note.lstat().st_mode, b'before\nactual append\n')
                assert before == candidate_contents(a, entry, *([c] if c else []))
                assert refs == f.git(repo, 'show-ref')
                observations.append(dict(case='C19-E' if protection else 'C19-L', kind=kind, protection=protection,
                                         preview=preview, cause=reason, peer='eligible/preserved' if preview else 'root/admin/registration absent; refs retained'))
    return observations


def residue_input(f, repo, path, *, empty=False, name=None, keep=False):
    """Fixture input only; no historical location is passed to the sweeper."""
    entry = repo / '.git/worktrees' / path.name
    if not keep:
        shutil.rmtree(path)
    if empty:
        for child in entry.iterdir():
            if child.is_dir(): shutil.rmtree(child)
            else: child.unlink()
    else:
        (entry / 'gitdir').unlink()
    if name:
        entry.rename(entry.with_name(name)); entry = entry.with_name(name)
    return entry


def residue_scope(report, entry):
    scopes = [s for s in report['discovery_scopes'] if s['path'] == str(entry)]
    import errno
    cause = str(FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), 'gitdir'))
    assert scopes == [dict(path=str(entry), candidate_count=0,
                           reason=f'unknown_git_metadata:{entry / "gitdir"}: {cause}')], report
    assert scopes[0] in report['unknown_scope']['discovery_scopes']
    assert all(c['path'] != str(entry) for c in report['candidates']), report


def counted(report, n, r=0, u=0, m=0, *, partial=True):
    expected = dict(known_candidate_count=n, known_unreached_count=0, retained_count=r,
                    partial_unknown_count=u, retained_allocation_unknown_count=m,
                    physical_freed_bytes=None, discovery='partial' if partial else 'complete',
                    outcome='error' if u else 'incomplete' if partial else 'complete',
                    unreached=None if partial else 0)
    assert {k: report[k] for k in expected} == expected, (expected, report)
    if partial: assert report.get('prune_retained', []) == [], report


def residue_cases(*, unsafe=False):
    """C19 residue table: accepted cases1-15/17/18; real filesystem/Git only."""
    observations, failures = [], []
    positive = ('empty', 'valid', 'renamed', 'multiple', 'local_index', 'local_head', 'outside')
    negative = ('present_dir', 'present_file', 'present_live', 'present_dangling',
                'other_dir', 'other_file', 'other_live', 'other_dangling',
                'gitdir_denied', 'gitdir_live', 'gitdir_dangling', 'gitdir_hardlink',
                'gitdir_dir', 'gitdir_fifo', 'gitdir_socket',
                'commondir_missing', 'commondir_denied', 'commondir_bad',
                'commondir_live', 'commondir_hardlink', 'commondir_fifo',
                'aux_denied', 'aux_live', 'aux_hardlink', 'aux_fifo', 'head_dir', 'index_dir',
                'prefix_alias', 'prefix_denied', 'root_denied', 'entry_race', 'names_race',
                'ancestor_loss', 'leaf_replaced', 'git_omit', 'git_add', 'git_duplicate',
                'git_duplicate_field', 'git_alter', 'head_zero', 'head_bad')
    unsafe_rows = tuple(k for k in negative if k.startswith(('gitdir_live','gitdir_dangling','gitdir_hardlink','gitdir_dir','gitdir_fifo','gitdir_socket','commondir_live','commondir_hardlink','commondir_fifo','aux_live','aux_hardlink','aux_fifo','head_dir','index_dir')))
    selected = unsafe_rows if unsafe else (*positive, *(k for k in negative if k not in unsafe_rows))
    for kind in selected:
        for preview in (False, True):
            label = f'C19-residue:{kind}:{preview}'
            try:
                with fixture() as f, ExitStack() as stack:
                    repo, w = f.worktree('TASK-12087', 'a-project')
                    _, b = f.worktree('TASK-12088', 'a-project')
                    # Reach healthy admission before applying any fixture damage.
                    d = time.monotonic() + 12
                    ds.admit(dict(path=str(b), repo=str(repo), workspace=str(f.workspace), phase='worktree'),
                             ds.Evidence(f.org, d), d, now=NOW)
                    second = f.worktree('TASK-12086', 'a-project')[1] if kind == 'multiple' else None
                    e = residue_input(f, repo, w, empty=kind in ('empty', 'multiple'),
                                      name='safe-non-task' if kind == 'renamed' else None,
                                      keep=kind == 'outside' or kind.startswith('present_'))
                    if kind == 'outside':
                        outside = f.base / 'outside-every-candidate'; w.rename(outside)
                    entries = [e]
                    if kind == 'multiple':
                        entries.append(residue_input(f, repo, second, empty=True))
                    if kind in ('local_index', 'local_head', 'head_zero', 'head_bad'):
                        _, q = f.worktree('TASK-12089', 'a-project')
                        qe = repo / '.git/worktrees' / q.name
                        if kind.startswith('local_'):
                            stack.enter_context(damaged_registration(f, repo, q,
                                                'unreadable_index' if kind == 'local_index' else 'missing_head'))
                        else: (qe / 'HEAD').write_text('0' * 40 + '\n' if kind == 'head_zero' else 'bad\n')
                    x = z = None
                    if kind in negative:
                        x = f.scratch('TASK-1'); safe, z = f.worktree('TASK-2', 'z-safe')
                    sentinel = f.base / 'outside-sentinel'; sentinel.write_bytes(b'keep')
                    if kind.startswith(('present_', 'other_')):
                        target = w if kind.startswith('present_') else w.with_name('TASK-12090')
                        form = kind.split('_')[1]
                        if os.path.lexists(target): shutil.rmtree(target)
                        if form == 'dir': target.mkdir()
                        elif form == 'file': target.write_text('unregistered')
                        else: target.symlink_to(sentinel if form == 'live' else sentinel.with_name('absent'))
                    elif kind.startswith(('gitdir_', 'commondir_', 'aux_')):
                        category, form = kind.split('_')
                        target = e / ('gitdir' if category == 'gitdir' else 'commondir' if category == 'commondir' else 'note')
                        if os.path.lexists(target): target.unlink()
                        if form in ('denied', 'bad', 'hardlink'):
                            target.write_text('bad\n' if form == 'bad' else '../..\n')
                            if form == 'denied':
                                target.chmod(0)
                                try: target.read_bytes()
                                except PermissionError as exc: assert exc.errno == 13
                                else: raise AssertionError('ordinary UID EACCES not reached')
                            if form == 'hardlink': os.link(target, f.base / 'alias')
                        elif form in ('live', 'dangling'): target.symlink_to(sentinel if form == 'live' else sentinel.with_name('absent'))
                        elif form == 'dir': target.mkdir()
                        elif form == 'fifo': os.mkfifo(target)
                        elif form == 'socket':
                            endpoint = socket.socket(socket.AF_UNIX)
                            fd = os.open(e, os.O_RDONLY | os.O_DIRECTORY)
                            try: endpoint.bind(f'/proc/self/fd/{fd}/' + target.name)
                            finally: os.close(fd)
                            stack.callback(endpoint.close)
                    elif kind in ('head_dir', 'index_dir'):
                        target = e / kind.split('_')[0].upper() if kind == 'head_dir' else e / 'index'
                        target.unlink(); target.mkdir()
                    elif kind == 'prefix_alias':
                        prefix = repo / '.claude'; held = f.base / 'held-prefix'; prefix.rename(held); prefix.symlink_to(held)
                    elif kind in ('prefix_denied', 'root_denied'):
                        target = repo / ('.claude' if kind == 'prefix_denied' else '.claude/worktrees')
                        target.chmod(0)
                    # Independent preservation oracle; no-follow snapshot also
                    # records exact times/nlink/inodes, but never reads endpoints.
                    def residue_bytes():
                        return (fs.snapshot(e, time.monotonic() + 12), candidate_contents(e))
                    before = residue_bytes() if kind in positive else None
                    refs = f.git(repo, 'show-ref'); calls = []; events = []
                    realgit, realmeta, realsnap, realfile = ds.git, ds._registration_metadata, ds.snapshot, ds._metadata_file
                    def metadata(root, deadline, **kwargs):
                        value = realmeta(root, deadline, **kwargs)
                        if root == repo:
                            events.append('census')
                            if kind == 'entry_race' and events.count('census') == 1:
                                e.rename(e.with_name('renamed-entry'))
                        return value
                    def snap(path, deadline, **kw):
                        value = realsnap(path, deadline, **kw)
                        if Path(path) == repo / '.git/worktrees' and not events and kind == 'names_race':
                            (repo / '.git/worktrees/new-entry').mkdir(); events.append('names changed')
                        return value
                    def read(path, deadline, **kw):
                        if Path(path) == e / 'gitdir' and not events:
                            if kind == 'ancestor_loss': e.rename(e.with_name('lost')); events.append('parent lost')
                            if kind == 'leaf_replaced': (e / 'gitdir').symlink_to(sentinel); events.append('leaf replaced')
                        return realfile(path, deadline, **kw)
                    def git(root, deadline, *args):
                        calls.append((str(root), args)); raw = realgit(root, deadline, *args)
                        if root == repo and args[:2] == ('worktree', 'list') and kind.startswith('git_'):
                            blocks = raw.strip('\0').split('\0\0')
                            peer = next(v for v in blocks if v.startswith('worktree ' + str(b) + '\0'))
                            if kind == 'git_omit': blocks.remove(peer)
                            elif kind == 'git_duplicate': blocks.append(peer)
                            elif kind == 'git_duplicate_field': blocks[blocks.index(peer)] = peer + '\0HEAD ' + 'a' * 40
                            elif kind == 'git_add': blocks.append(peer.replace(str(b), str(w)))
                            elif kind == 'git_alter': blocks[blocks.index(peer)] = peer.replace(str(b), str(w))
                            raw = '\0\0'.join(blocks) + '\0\0'; events.append('captured-wire fault')
                        return raw
                    with patch.object(ds, 'git', git), patch.object(ds, '_registration_metadata', metadata), \
                            patch.object(ds, 'snapshot', snap), patch.object(ds, '_metadata_file', read):
                        if preview:
                            with refuse_mutations(), no_endpoint_io(): report = f.sweep('dry_run', seconds=20)
                        else:
                            with no_endpoint_io(): report = f.sweep(seconds=20)
                    if kind in ('prefix_denied','root_denied'):
                        assert stat.S_IMODE(target.stat().st_mode) == 0
                        target.chmod(0o755)  # fixture-only restoration for physical postconditions
                        assert any('Permission denied' in v['reason'] for v in report['discovery_scopes']), report
                    if kind in ('present_dir', 'other_dir'):
                        # A literal TASK directory has a location even when
                        # the old admin entry is an independent unmapped residue.
                        unknown = w if kind == 'present_dir' else w.with_name('TASK-12090')
                        counted(report, 4, r=1, m=1)
                        residue_scope(report, e)
                        retained(report, unknown, 'unknown_unregistered_task_worktree:' + str(unknown))
                        assert not any(s['path'] == str(repo) for s in report['discovery_scopes']), report
                        assert row(report, b)['state'] == ('eligible' if preview else 'removed'), report
                        assert row(report, z)['state'] == ('eligible' if preview else 'removed'), report
                        assert row(report, x)['state'] == ('eligible' if preview else 'removed'), report
                    elif kind in positive:
                        local = kind in ('local_index', 'local_head')
                        counted(report, 2 if local else 1, int(local), m=int(local))
                        for entry in entries: residue_scope(report, entry)
                        assert row(report, b)['state'] == ('eligible' if preview else 'removed'), report
                        if not preview:
                            assert not os.path.lexists(b) and row(report, b)['removed_attributable_bytes'] > 0
                        if local:
                            retained(report, q, str(qe / ('index' if kind == 'local_index' else 'HEAD')))
                        after = residue_bytes()
                        assert before[1] == after[1] and all(fs.signature(before[0][k]) == fs.signature(after[0][k]) for k in before[0])
                        current = ds.registrations(repo, time.monotonic() + 12)
                        assert all(str(entry) not in current.linked for entry in entries)
                        expected = {str(repo)} | ({str(b)} if preview else set()) | ({str(q)} if local else set())
                        assert set(current) == expected and set(current.linked) == expected - {str(repo)}
                        if kind == 'outside': assert outside.exists() and (outside / '.git').is_file()
                    else:
                        counted(report, 2, partial=True)
                        assert not any(c.get('repo') == str(repo) for c in report['candidates']), report
                        assert b.exists() and row(report, z)['state'] == ('eligible' if preview else 'removed')
                        assert row(report, x)['state'] == ('eligible' if preview else 'removed')
                        scope = next(v for v in report['discovery_scopes'] if v['path'] == str(repo))
                        assert scope['candidate_count'] is None, scope
                        if kind.startswith(('present_', 'other_')): assert 'unknown_unregistered_task_worktree:' in scope['reason'], scope
                        if kind.endswith(('_fifo','_socket')):
                            # The existing complete snapshot reports this native
                            # category before the file census can emit a leaf path.
                            assert scope['reason'] == f'unknown_git_registrations:{e.parent}: unknown_special_entry', scope
                        elif kind.startswith(('gitdir_', 'commondir_', 'aux_')):
                            assert str(target) in scope['reason'], scope
                            if kind.endswith('_denied'): assert 'Permission denied' in scope['reason'], scope
                        if kind in ('head_dir','index_dir'): assert 'nonregular_registration_metadata:' + str(target) in scope['reason'], scope
                        if kind == 'head_bad': assert 'malformed_head:' + str(qe / 'HEAD') in scope['reason'], scope
                        if kind.startswith('git_'):
                            category = 'duplicate_git_registration_field:' if kind == 'git_duplicate_field' else 'unknown_git_registration:' if kind == 'git_duplicate' else 'unknown_git_registration_inventory:'
                            assert category in scope['reason'] and 'captured-wire fault' in events, scope
                        elif kind == 'head_zero': assert 'unknown_git_registration_head:' in scope['reason'], scope
                        else: assert not any(root == str(repo) for root, _ in calls), calls
                    assert not any(args[:2] == ('worktree', 'prune') for _, args in calls), calls
                    assert sentinel.read_bytes() == b'keep' and refs == f.git(repo, 'show-ref')
                    observations.append(dict(row=label, result='PASS', events=events, report_counts={k:report[k] for k in ('known_candidate_count','retained_count','partial_unknown_count')}))
            except Exception as exc:
                failures.append(dict(row=label, error=str(exc)))
    for failure in failures: print(json.dumps(dict(regression=failure)), flush=True)
    assert not failures, failures
    return observations


def registration_arrival(seam: str, preview: bool) -> dict:
    """Drive the real census/list seams using only disposable real Git inputs."""
    with fixture() as f:
        repo, peer = f.worktree('TASK-1')
        new = peer.with_name('TASK-2')
        calls, events = [], []
        realgit, realmeta = ds.git, ds._registration_metadata
        def introduce():
            if events: return
            if seam.startswith('plain'):
                new.mkdir(); (new / 'keep').write_text('unregistered payload')
            elif seam == 'non_task':
                (repo / '.git/worktrees/non-task').mkdir()
            elif seam == 'existing':
                entry = repo / '.git/worktrees' / peer.name
                entry.rename(entry.with_name('changed-entry'))
                (peer / '.git').write_text('gitdir: ' + str(entry.with_name('changed-entry')) + '\n')
            else:
                _, made = f.worktree(new.name)
                assert made == new
            events.append(seam)
        if seam == 'plain': introduce()
        before = candidate_contents(peer, repo / '.git', *([new] if new.exists() else []))
        census = []
        def metadata(root, deadline, **kwargs):
            value = realmeta(root, deadline, **kwargs)
            if root == repo:
                census.append(True)
                if len(census) == 1 and seam in ('pre_git', 'plain_pre_git', 'non_task', 'existing'): introduce()
            return value
        def git(root, deadline, *args):
            calls.append(args)
            if root == repo and args[:2] == ('worktree', 'list') and seam == 'list_before': introduce()
            value = realgit(root, deadline, *args)
            if root == repo and args[:2] == ('worktree', 'list') and seam in ('list_after', 'plain_list_after'): introduce()
            return value
        with patch.object(ds, 'git', git), patch.object(ds, '_registration_metadata', metadata):
            if preview:
                with refuse_mutations(): report = f.sweep('dry_run')
            else: report = f.sweep()
        assert events == [seam], events
        if seam in ('non_task', 'existing'):
            assert peer.exists() and not any(c.get('repo') == str(repo) for c in report['candidates']), report
            assert any(s['path'] == str(repo) and 'git_mapping_changed_during_inventory' in s['reason'] for s in report['discovery_scopes']), report
        else:
            reason = 'unknown_unregistered_task_worktree:' + str(new)
            assert new.exists(), report
            assert any(s['path'] == str(new) and s['reason'] == reason for s in report['discovery_scopes']), report
            assert not any(s['path'] == str(repo) for s in report['discovery_scopes']), report
            assert row(report, peer)['state'] == ('eligible' if preview else 'removed'), report
            if any(c['path'] == str(new) for c in report['candidates']): retained(report, new, reason)
            assert report['discovery'] == 'partial' and report['outcome'] == ('incomplete' if seam.startswith('plain') else 'complete'), report
            if not seam.startswith('plain'): assert str(new) in report['deferred_arrivals']
            if preview:
                after = candidate_contents(peer, repo / '.git', *([new] if seam == 'plain' else []))
                # The injected real add owns new admin bytes; all old files stay byte/mode exact.
                assert all(after.get(k) == v for k, v in before.items()), (seam, before, after)
                if seam == 'plain':
                    command = [sys.executable, '-m', 'runtime.daemon.disk_sweeper', '--dry-run',
                               '--runtime-root', str(f.base / 'runtime')]
                    result = subprocess.run(command, capture_output=True, text=True, timeout=8, check=False)
                    assert result.returncode == 1, (result.stdout, result.stderr)
                    manual = json.loads(result.stdout)['report']['orgs'][0]
                    retained(manual, new, reason)
                    retained(manual, peer, 'changed_within_hour')
                    assert before == candidate_contents(peer, repo / '.git', new)
        assert not any(a[:2] == ('worktree', 'prune') for a in calls), calls
        if preview: assert not any(a[:2] == ('worktree', 'remove') for a in calls), calls
        return {'seam': seam, 'preview': preview, 'result': 'PASS'}


def c23():
    """Additive TASK arrivals are local; changes to existing mappings stay shared."""
    seams = ('list_after', 'plain', 'plain_pre_git', 'plain_list_after',
             'pre_git', 'list_before', 'non_task', 'existing')
    return [registration_arrival(seam, preview) for seam in seams for preview in (False, True)]


def c19():
    """Git exit0 omissions must not create empty/complete inventories."""
    observed=[registration_lock_peer(), *registration_local_peers(), *residue_cases()]
    for kind in ('unreadable_admin','unreadable_entry','unreadable_gitdir','missing_admin','missing_entry','missing_gitdir','missing_commondir','malformed_head','malformed_gitdir','extra_entry'):
        with fixture() as f:
            p=f.scratch('TASK-1');repo,q=f.worktree('TASK-2','a-uncertain')
            safe,z=f.worktree('TASK-3','z-safe')
            peer = f.worktree('TASK-4', 'a-uncertain')[1] if kind == 'missing_gitdir' else None
            before=candidate_contents(q,repo/'.git/worktrees')
            registered=f.git(repo,'worktree','list','--porcelain')
            calls=[];real=ds.git
            def trace(repo,deadline,*args):
                calls.append((str(repo),args));return real(repo,deadline,*args)
            with damaged_registration(f,repo,q,kind) as damaged:
                # Real Git's omission is recorded, never used as authority.
                git_output=f.git(repo,'worktree','list','--porcelain')
                with patch.object(ds,'git',trace),refuse_mutations():dry=f.sweep('dry_run')
                assert dry['discovery']=='partial' and dry['outcome']=='incomplete',dry
                assert dry['unreached'] is None and dry['known_candidate_count']==2,dry
                assert any(s['path']==str(repo) and s['candidate_count'] is None and str(repo/'.git/worktrees') in s['reason'] for s in dry['discovery_scopes']),dry
                assert all(c['path']!=str(q) for c in dry['candidates'])
                assert p.exists() and z.exists() and q.exists()
                if peer: assert peer.exists()
                with patch.object(ds,'git',trace):r=f.sweep()
                assert r['discovery']=='partial' and r['outcome']=='incomplete' and r['unreached'] is None,r
                assert all(row(r,x)['state']=='removed' and not x.exists() for x in (p,z)),r
                assert q.exists() and damaged['sentinel'].read_bytes()==b'external control'
                if peer: assert peer.exists()
                assert not any(root==str(repo) for root,args in calls),calls
                assert not any(args[:2]==('worktree','prune') for root,args in calls),calls
                if kind.startswith('unreadable_'): assert target_mode(repo,q,kind)==0
                observed.append(dict(case='C19-U',kind=kind,git_omitted=str(q) not in git_output,dry='preserved/no mutation',on='independent scratch/repo removed',bad_repo_git_calls=0,unknown_total=None))
            assert before==candidate_contents(q,repo/'.git/worktrees')
            assert registered==f.git(repo,'worktree','list','--porcelain')
            assert f.git(repo,'show-ref','--verify','refs/heads/task/TASK-2')
    # C4: authentic nested registration moved without updating its backlink.
    with fixture() as f:
        repo, outer, baseline, _, _ = descendant_setup(f)
        p = f.scratch('TASK-2'); safe, z = f.worktree('TASK-3', 'z-safe')
        valid = descendant_snapshot(f, repo, outer, baseline)
        held = f.base / 'held-baseline'
        baseline.rename(held)
        corrupted = candidate_contents(outer, held, repo / '.git/worktrees', repo / '.git/info/exclude')
        calls = []; real = ds.git
        def trace(root, deadline, *args):
            calls.append((str(root), args)); return real(root, deadline, *args)
        try:
            diagnostic = f.git(repo, 'worktree', 'list', '--porcelain')
            assert 'prunable' in diagnostic, diagnostic
            with patch.object(ds, 'git', trace), refuse_mutations(): dry = f.sweep('dry_run')
            with patch.object(ds, 'git', trace): report = f.sweep()
            for result in (dry, report):
                assert result['discovery'] == 'partial' and result['outcome'] == 'incomplete', result
                assert result['unreached'] is None and result['known_candidate_count'] == 2, result
                assert any(scope['path'] == str(repo) and scope['candidate_count'] is None for scope in result['discovery_scopes']), result
                assert all(c['path'] != str(outer) for c in result['candidates']), result
            assert all(row(report, x)['state'] == 'removed' and not x.exists() for x in (p, z)), report
            assert not any(root == str(repo) for root, args in calls), calls
            assert not any(args[:2] == ('worktree', 'prune') for root, args in calls), calls
            assert corrupted == candidate_contents(outer, held, repo / '.git/worktrees', repo / '.git/info/exclude')
        finally:
            held.rename(baseline)
        assert valid == descendant_snapshot(f, repo, outer, baseline)
        observed.append(dict(case='C4_nested_missing_path', unknown_candidate_count=None,
                             safe_scopes_removed=2, bad_repo_git_calls=0, exact_move_restored=True,
                             restoration='bytes/types/modes/backlinks/HEAD/refs/porcelain; ctime excluded'))
    return observed


def target_mode(repo,path,kind):
    target={'unreadable_admin':repo/'.git/worktrees','unreadable_entry':repo/'.git/worktrees'/path.name,'unreadable_gitdir':repo/'.git/worktrees'/path.name/'gitdir'}[kind]
    return target.lstat().st_mode & 0o777


def c20():
    """Valid alias escape and pointer conflicts stop BEFORE Git consumes them."""
    observed=residue_cases(unsafe=True)
    for kind in ('admin_alias','entry_alias','gitdir_alias','commondir_alias','backlink_alias','conflicting_backlink','pointer_hardlink','index_hardlink','duplicate_target'):
        with fixture() as f:
            p=f.scratch('TASK-1');repo,q=f.worktree('TASK-2')
            peer = f.worktree('TASK-3')[1] if kind in ('pointer_hardlink','index_hardlink','duplicate_target') else None
            refs = f.git(repo, 'show-ref')
            # Preserve valid Git meaning even after the admin-directory move.
            (repo/'.git/worktrees'/q.name/'commondir').write_text(str(repo/'.git')+'\n')
            before=candidate_contents(q,repo/'.git/worktrees')
            registered=f.git(repo,'worktree','list','--porcelain')
            calls=[];real=ds.git
            def trace(root,deadline,*args):calls.append((str(root),args));return real(root,deadline,*args)
            with damaged_registration(f,repo,q,kind) as damaged:
                protected=candidate_contents(damaged['external'])
                # This is the actual valid nonzero-HEAD QA reproducer.
                if kind=='admin_alias':
                    actual=f.git(repo,'worktree','list','--porcelain')
                    assert str(q) in actual and 'HEAD 0000000000000000000000000000000000000000' not in actual
                with patch.object(ds,'git',trace),refuse_mutations():dry=f.sweep('dry_run')
                assert not any(root==str(repo) for root,args in calls), ('C20-U unsafe census reached Git', kind, 'preview', calls)
                with patch.object(ds,'git',trace):r=f.sweep()
                assert not any(root==str(repo) for root,args in calls), ('C20-U unsafe census reached Git', kind, calls)
                assert dry['discovery']==r['discovery']=='partial' and r['outcome']=='incomplete',r
                assert r['unreached'] is None and row(r,p)['state']=='removed' and q.exists(),r
                assert r['discovery_scopes'][0]['path']==str(repo) and r['discovery_scopes'][0]['candidate_count'] is None,r
                assert not any(args[:2] in (('worktree','remove'),('worktree','prune')) for root,args in calls),calls
                if peer: assert peer.exists()
                assert refs == f.git(repo, 'show-ref')
                assert protected==candidate_contents(damaged['external'])
                observed.append(dict(case='C20-U',kind=kind,git_calls=0,external_bytes_modes='unchanged',registration_worktree_branch='preserved'))
            assert before==candidate_contents(q,repo/'.git/worktrees')
            assert registered==f.git(repo,'worktree','list','--porcelain')
            assert f.git(repo,'show-ref','--verify','refs/heads/task/TASK-2')
    return observed


def descendant_cases():
    observations = []
    # C1/C5 share the exact F1 setup; preview uses the real JSON/audit path.
    for preview in (False, True):
        with fixture() as f:
            repo, outer, baseline, _, _ = descendant_setup(f)
            before = descendant_snapshot(f, repo, outer, baseline)
            with refuse_mutations():
                if preview:
                    projected, summary = policy_sweep(f, dry_run=True)
                    ds._manual_audit(projected, [str(f.org)], summary)
                    report = projected['orgs'][0]
                else:
                    report = f.sweep()
            retained(report, outer, 'linked_descendant:' + str(baseline))
            assert len(report['candidates']) == 1, report
            assert report['phases']['worktree']['removed_attributable_bytes'] == 0, report
            assert row(report, outer).get('allocated_bytes') is None, report
            assert before == descendant_snapshot(f, repo, outer, baseline)
            if preview:
                saved = f.db.execute("SELECT task_id,agent,payload FROM audit_log WHERE action='disk_sweep_summary'").fetchall()
                assert len(saved) == 1 and saved[0][:2] == ('', 'daemon'), saved
                payload = json.loads(saved[0][2])
                assert payload['org'] == report and payload['summary_path'] == str(summary), payload
                assert payload['outcome'] == projected['outcome'] == 'complete'
            observations.append(dict(case='C5' if preview else 'C1', retained_descendant=str(baseline),
                                     mutation_attempts=0, removed_attribution=0, preservation='exact snapshot'))
    # C2: a locked sibling TASK-10 must not veto TASK-1 by string prefix.
    with fixture() as f:
        repo, outer = f.worktree('TASK-1'); _, sibling = f.worktree('TASK-10')
        f.git(repo, 'worktree', 'lock', str(sibling))
        (outer / 'ignored').mkdir(); (outer / 'ignored/payload').write_bytes(b'cache' * 2048)
        before = descendant_snapshot(f, repo, sibling)
        sibling_row = ds.registrations(repo, time.monotonic() + 12)[str(sibling)]
        calls = []; real = ds.git
        def trace(root, deadline, *args):
            calls.append(args); return real(root, deadline, *args)
        with patch.object(ds, 'git', trace): report = f.sweep()
        assert row(report, outer)['state'] == 'removed' and not outer.exists(), report
        assert row(report, outer)['removed_attributable_bytes'] > 0, report
        retained(report, sibling, 'locked')
        after = descendant_snapshot(f, repo, sibling)
        # TASK-1 admin removal is expected; compare the sibling's exact metadata
        # separately, and full refs/HEADs without its removed registration row.
        assert candidate_contents(sibling, repo / '.git/worktrees' / sibling.name) == {
            k: v for k, v in before['contents'].items()
            if k == str(sibling) or k.startswith(str(sibling) + '/')
            or k == str(repo / '.git/worktrees' / sibling.name)
            or k.startswith(str(repo / '.git/worktrees' / sibling.name) + '/')}
        assert before['heads'] == after['heads'] and before['refs'] == after['refs']
        assert str(outer) not in ds.registrations(repo, time.monotonic() + 12)
        assert sibling_row == ds.registrations(repo, time.monotonic() + 12)[str(sibling)]
        assert not any(args[:2] == ('worktree', 'prune') for args in calls), calls
        observations.append(dict(case='C2', state='removed', positive_attribution=True, sibling='locked TASK-10 preserved', prune=0))
    # C3: deterministic introductions AFTER each named successful admission.
    transitions = ('initial_to_final', 'final_to_remove', 'failure_to_admission',
                   'retry_to_repair', 'repaired_to_retry')
    for boundary, uncertain in ([(b, False) for b in transitions] +
                                [(b, True) for b in ('final_to_remove', 'retry_to_repair', 'repaired_to_retry')]):
        with fixture() as f, ExitStack() as changes:
            repo, outer, baseline, outer_head, base_head = descendant_setup(f, nested=False)
            readonly = outer / 'ignored/readonly'
            if boundary == 'repaired_to_retry': readonly.mkdir(); readonly.chmod(0o555)
            calls = []; repairs = []; repair_attempts = []; introductions = []; censuses = []; admits = []; mode_changes = []
            realgit, realadmit, realmutate, realrows = ds.git, ds.admit, ds.mutate, ds.registrations
            def introduce():
                assert not introductions
                descendant_add(f, repo, baseline, base_head)
                descendant_ready(f, repo, outer, baseline, outer_head, base_head)
                introductions.append(descendant_snapshot(f, repo, outer, baseline))
                if uncertain:
                    changes.enter_context(damaged_registration(f, repo, baseline, 'unreadable_index'))
                    try: (repo / '.git/worktrees' / baseline.name / 'index').read_bytes()
                    except PermissionError as exc: assert exc.errno == 13
                    else: raise AssertionError('uncertain descendant index did not deny the real read')
            def admit(candidate, evidence, deadline, **kwargs):
                result = realadmit(candidate, evidence, deadline, **kwargs)
                admits.append(len(admits) + 1)
                target = {'initial_to_final': 1, 'final_to_remove': 2,
                          'retry_to_repair': 3, 'repaired_to_retry': 4}.get(boundary)
                if len(admits) == target: introduce()
                return result
            def registrations(root, deadline, **kwargs):
                result = realrows(root, deadline, **kwargs)
                if introductions and str(baseline) in result:
                    assert result.linked[str(baseline)] == repo / '.git/worktrees' / baseline.name
                    if uncertain:
                        assert str(repo / '.git/worktrees' / baseline.name / 'index') in result.errors[str(baseline)]
                    censuses.append(len(admits))
                return result
            def git(root, deadline, *args):
                if args[:2] in (('worktree', 'remove'), ('worktree', 'prune')): calls.append(args)
                if args[:2] == ('worktree', 'remove'):
                    assert len(calls) == 1, calls
                    try:
                        return realgit(root, deadline, '--THR307-controlled-invalid-option')
                    except ds.Retain as exc:
                        assert 'unknown option' in str(exc), exc
                        if boundary == 'failure_to_admission': introduce()
                        raise
                return realgit(root, deadline, *args)
            def mutate(path, tree, deadline, *, remove):
                assert not remove
                repair_attempts.append(str(path))
                old = readonly.stat() if boundary == 'repaired_to_retry' else None
                result = realmutate(path, tree, deadline, remove=False)
                repairs.append(result)
                if old:
                    assert stat.S_IMODE(old.st_mode) == 0o555
                    changed = readonly.stat()
                    assert stat.S_IMODE(changed.st_mode) == 0o755
                    assert set(result) == {'ignored/readonly'}, result
                    assert fs.identity(old) == fs.identity(changed)
                    assert changed.st_ctime_ns >= old.st_ctime_ns
                    mode_changes.append(dict(path=str(readonly), before_mode=oct(stat.S_IMODE(old.st_mode)),
                                             after_mode=oct(stat.S_IMODE(changed.st_mode)),
                                             before_ctime_ns=old.st_ctime_ns, after_ctime_ns=changed.st_ctime_ns))
                return result
            with patch.object(ds, 'admit', admit), patch.object(ds, 'git', git), \
                    patch.object(ds, 'mutate', mutate), patch.object(ds, 'registrations', registrations):
                report = f.sweep()
            assert len(introductions) == 1 and censuses, (boundary, admits, censuses, report)
            expected_admits = {'initial_to_final': 1, 'final_to_remove': 2, 'failure_to_admission': 2,
                               'retry_to_repair': 3, 'repaired_to_retry': 4}[boundary]
            assert len(admits) == expected_admits, (boundary, admits, report)
            # For b/d/e the first returned post-introduction census is precisely
            # the final pre-action call, not a veto inside another admission.
            assert censuses[0] == expected_admits, (boundary, censuses)
            removes = [args for args in calls if args[:2] == ('worktree', 'remove')]
            assert len(removes) == (0 if boundary in ('initial_to_final', 'final_to_remove') else 1), calls
            assert len(repair_attempts) == (1 if boundary == 'repaired_to_retry' else 0), ('C21-T unexpected repair attempt', boundary, uncertain, repair_attempts, report)
            assert len(repairs) == (1 if boundary == 'repaired_to_retry' else 0), repairs
            assert not any(args[:2] == ('worktree', 'prune') for args in calls), calls
            current = row(report, outer)
            assert current['state'] == ('retained' if not removes else 'partial_or_unknown'), report
            assert 'linked_descendant:' + str(baseline) in current['reason'], report
            assert current.get('removed_attributable_bytes') is None and current.get('retry_count', 0) == 0, current
            assert report['phases']['worktree']['removed_attributable_bytes'] == 0, report
            if removes:
                assert current['first_remove_error'].startswith('git_failed:')
                assert report['phases']['worktree']['attribution_unknown'] is True
            changes.close()
            assert introductions[0] == descendant_snapshot(f, repo, outer, baseline)
            observations.append(dict(case='C21-T' if uncertain else 'C3', uncertain_descendant=uncertain, canonical_descendant=str(baseline), boundary=boundary, remove_attempts=len(removes),
                                     repair_attempts=len(repair_attempts), completed_repairs=len(repairs), retry_attempts=0,
                                     completed_admissions=len(admits), last_census_reached=True,
                                     state=current['state'], earlier_owned_mode_changes=mode_changes))
    return observations


def uncertain_descendants():
    """C21-N/P: retain actual canonical nested paths even with local unknowns."""
    observations = []
    for kind in ('unreadable_index', 'missing_gitdir'):
        for preview in (False, True):
            with fixture() as f:
                repo, outer, baseline, _, _ = descendant_setup(f)
                _, sibling = f.worktree('TASK-10')
                entry = repo / '.git/worktrees' / baseline.name
                before = candidate_contents(outer, repo / '.git/worktrees' / outer.name, entry)
                refs = f.git(repo, 'show-ref'); calls = []; real = ds.git
                def trace(root, deadline, *args):
                    calls.append((str(root), args)); return real(root, deadline, *args)
                with damaged_registration(f, repo, baseline, kind):
                    with patch.object(ds, 'git', trace):
                        if preview:
                            with refuse_mutations(): report = f.sweep('dry_run')
                        else: report = f.sweep()
                    assert report['discovery'] == 'partial' and report['unreached'] is None, report
                    if kind == 'unreadable_index':
                        retained(report, outer, 'linked_descendant:' + str(baseline))
                        assert any(scope['path'] == str(baseline) and str(entry / 'index') in scope['reason']
                                   and 'Permission denied' in scope['reason'] for scope in report['discovery_scopes']), report
                        assert row(report, sibling)['state'] == ('eligible' if preview else 'removed'), report
                        if not preview:
                            assert not os.path.lexists(sibling) and not os.path.lexists(repo / '.git/worktrees' / sibling.name)
                            assert str(sibling) not in f.git(repo, 'worktree', 'list', '--porcelain', '-z')
                    else:
                        counted(report, 2, 1, m=1)
                        residue_scope(report, entry)
                        retained(report, outer, 'possible_nested_worktree:' + str(baseline / '.git'))
                        assert row(report, sibling)['state'] == ('eligible' if preview else 'removed'), report
                        assert sibling.exists() is preview and outer.exists()
                    assert not any(args[:2] == ('worktree', 'prune') for _, args in calls), calls
                    assert baseline.exists()
                assert before == candidate_contents(outer, repo / '.git/worktrees' / outer.name, entry)
                assert refs == f.git(repo, 'show-ref')
                observations.append(dict(case='C21-N', kind=kind, preview=preview, canonical_descendant=str(baseline),
                                         sibling='eligible' if preview else 'removed'))
    return observations


def local_registration_transitions():
    """C21-T/P: late local errors, sibling churn and independent removal credit."""
    observations = []
    for when, kind, preview in (('after_discovery', 'unreadable_index', False),
                                ('after_discovery', 'unreadable_index', True),
                                ('during_B_census', 'unreadable_index', True),
                                ('post_remove', 'unreadable_index', False),
                                ('post_remove', 'missing_gitdir', False)):
        with fixture() as f, ExitStack() as changes:
            repo, a = f.worktree('TASK-1'); _, b = f.worktree('TASK-2')
            entry = repo / '.git/worktrees' / a.name; b_entry = repo / '.git/worktrees' / b.name
            note = entry / 'fixture-note'; note.write_text('before\n')
            before = candidate_contents(a, entry); refs = f.git(repo, 'show-ref')
            if when == 'post_remove': os.utime(b, (time.time() - 7200, time.time() - 7200))
            events = []; damage = []; calls = []; churn = []; captured = []
            realgit, realdiscover, real_snapshot = ds.git, ds.discover, ds.snapshot
            fixture_chmod = os.chmod
            def change():
                # The input action alone may chmod; sweeper execution remains
                # under preview's normal mutation prohibitions.
                with patch.object(os, 'chmod', fixture_chmod):
                    damage.append(changes.enter_context(damaged_registration(f, repo, a, kind)))
                events.append('only A damaged')
            def discover(*args, **kwargs):
                result = realdiscover(*args, **kwargs)
                captured.append(kwargs['inventory'])
                order = [c['path'] for c in result]
                if when == 'post_remove':
                    assert order.index(str(b)) < order.index(str(a)), order
                    events.append('actual discovery B before A')
                elif when == 'after_discovery': change()
                return result
            def snapshot(path, deadline, **kwargs):
                tree = real_snapshot(path, deadline, **kwargs)
                if when == 'during_B_census' and Path(path) == b and not damage:
                    assert row(captured[0], a)['state'] == 'eligible'
                    events.append('A previously eligible in preview')
                    change()
                if when == 'after_discovery' and Path(path) == b:
                    with note.open('a') as stream: stream.write('ordinary sibling churn\n')
                    churn.append(True)
                return tree
            def git(root, deadline, *args):
                calls.append(args)
                result = realgit(root, deadline, *args)
                if when == 'post_remove' and args[:2] == ('worktree', 'remove') and args[-1] == str(b):
                    assert a.exists() and entry.is_dir()
                    current = ds.registrations(repo, deadline)
                    assert str(a) in current and str(a) in current.linked and not current.errors, current.errors
                    assert (a / '.git').read_text() == 'gitdir: ' + str(entry) + '\n'
                    assert (entry / 'gitdir').read_text() == str(a / '.git') + '\n'
                    assert (entry / 'commondir').read_text() == '../..\n'
                    events.append('actual B remove success; A still physically present/registered/reciprocal')
                    assert not os.path.lexists(b) and not os.path.lexists(b_entry)
                    assert str(b) not in current and refs == f.git(repo, 'show-ref')
                    events.append('independent B root/admin/registration absence and retained ref')
                    change()
                return result
            with patch.object(ds, 'discover', discover), patch.object(ds, 'snapshot', snapshot), patch.object(ds, 'git', git):
                if preview:
                    with refuse_mutations(): report = f.sweep('dry_run')
                else: report = f.sweep()
            assert len(damage) == 1 and report['discovery'] == 'partial' and report['unreached'] is None, report
            assert a.exists() and not any(args[:2] == ('worktree', 'prune') for args in calls), calls
            if when == 'during_B_census':
                assert events == ['A previously eligible in preview', 'only A damaged'], events
                assert row(report, a)['state'] == 'retained', ('C21-P late local census retained an eligible label', report)
                assert row(report, b)['state'] == 'eligible' and a.exists() and b.exists(), report
            elif when == 'after_discovery':
                assert churn, 'B admission never reached genuine sibling auxiliary churn'
                assert row(report, b)['state'] == ('eligible' if preview else 'removed'), report
                if not preview:
                    assert not os.path.lexists(b) and not os.path.lexists(b_entry)
                    assert str(b) not in f.git(repo, 'worktree', 'list', '--porcelain', '-z')
                before[str(note)] = (note.lstat().st_mode, b'before\n' + b'ordinary sibling churn\n' * len(churn))
            else:
                assert events[-1] == 'only A damaged' and len(events) == 4, events
                assert not os.path.lexists(b) and not os.path.lexists(b_entry)
                assert str(b) not in f.git(repo, 'worktree', 'list', '--porcelain', '-z')
                if kind == 'unreadable_index':
                    assert row(report, b)['state'] == 'removed' and row(report, b)['removed_attributable_bytes'] > 0, report
                else:
                    assert row(report, b)['state'] == 'partial_or_unknown' and row(report, b).get('removed_attributable_bytes') is None, report
                    assert report['phases']['worktree']['removed_attributable_bytes'] == 0, report
            if kind == 'unreadable_index': retained(report, a, str(entry / 'index'))
            changes.close()
            assert before == candidate_contents(a, entry) and refs == f.git(repo, 'show-ref')
            observations.append(dict(case='C21-T', when=when, kind=kind, preview=preview, events=events,
                                     sibling_auxiliary_changes=len(churn), B_state=row(report, b)['state']))
    # Same final reader must block its OWN newly-local error before first Git.
    with fixture() as f, ExitStack() as changes:
        repo, path = f.worktree('TASK-1'); entry = repo / '.git/worktrees' / path.name
        before = candidate_contents(path, entry); refs = f.git(repo, 'show-ref')
        admits = []; censuses = []; calls = []; repairs = []
        realadmit, realrows, realgit, realmutate = ds.admit, ds.registrations, ds.git, ds.mutate
        def admit(candidate, evidence, deadline, **kwargs):
            tree = realadmit(candidate, evidence, deadline, **kwargs)
            admits.append(True)
            if len(admits) == 2:
                changes.enter_context(damaged_registration(f, repo, path, 'unreadable_index'))
            return tree
        def registrations(root, deadline, **kwargs):
            result = realrows(root, deadline, **kwargs)
            if len(admits) == 2:
                assert str(entry / 'index') in result.errors[str(path)]
                censuses.append(True)
            return result
        def git(root, deadline, *args):
            if args[:2] in (('worktree', 'remove'), ('worktree', 'prune')): calls.append(args)
            return realgit(root, deadline, *args)
        def mutate(*args, **kwargs):
            repairs.append(True); return realmutate(*args, **kwargs)
        with patch.object(ds, 'admit', admit), patch.object(ds, 'registrations', registrations), \
                patch.object(ds, 'git', git), patch.object(ds, 'mutate', mutate):
            report = f.sweep()
        assert censuses and len(admits) == 2, report
        assert not calls and not repairs, ('C21-T self final local error reached Git/repair', calls, repairs, report)
        retained(report, path, str(entry / 'index'))
        assert report['discovery'] == 'partial' and row(report, path).get('removed_attributable_bytes') is None, report
        changes.close()
        assert before == candidate_contents(path, entry) and refs == f.git(repo, 'show-ref')
        observations.append(dict(case='C21-T', when='self_final_to_remove', final_census_reached=True, remove_or_repair=0))
    return observations


def nested_input(f, repo, outer, kind='file'):
    """Literal ignored names; no contents are needed by production's guard."""
    d = outer / 'ignored/a/deep/.git'; d.parent.mkdir(parents=True, exist_ok=True)
    if kind == 'file': d.write_text('untrusted git pointer\n')
    elif kind == 'dir': d.mkdir(); (d / 'keep').write_text('nested metadata')
    elif kind in ('live', 'dangling'):
        d.symlink_to(outer / 'source' if kind == 'live' else outer / 'absent')
    else: raise AssertionError(kind)
    return d


def nested_cases():
    """C21 extended snapshot/transition table, accepted16/18/21/22/23."""
    observations, failures = [], []
    table = [(scope, when, form) for scope in ('same', 'cross', 'none')
             for when in ('initial', 'shallow', 'fresh', 'repair', 'retry', 'second_snapshot', 'heal')
             for form in (('file', 'dir', 'live', 'dangling') if when in ('initial', 'shallow') else ('file','dir','dangling') if when in ('fresh','repair','retry') else ('file',))]
    for scope, when, form in table:
        label = f'C21-nested:{scope}:{when}:{form}'
        try:
            with fixture() as f:
                repo, c = f.worktree('TASK-1', 'a-container')
                _, q = f.worktree('TASK-2', 'a-container')
                e = None
                if scope != 'none':
                    p = repo if scope == 'same' else f.worktree('TASK-3', 'z-residue')[0]
                    if scope == 'same': _, w = f.worktree('TASK-3', 'a-container')
                    else: w = p / '.claude/worktrees/TASK-3'
                    e = residue_input(f, p, w)
                # Actual first admission is clean; make every later fixture
                # change at an existing production observation/action boundary.
                (c / 'ignored').mkdir()
                (c / 'ignored/readonly').mkdir(); (c / 'ignored/readonly').chmod(0o555)
                refs = f.git(repo, 'show-ref'); events = []; calls = []; repairs = []; seen = []; preserved = []
                d = c / ('ignored/.git' if when == 'shallow' else 'ignored/a/deep/.git')
                realadmit, realsnap, realgit, realmutate = ds.admit, ds.snapshot, ds.git, ds.mutate
                def add():
                    # Reverse creation order still selects the lexical first.
                    other = c / 'ignored/z/.git'; other.parent.mkdir(); other.write_text('other')
                    actual = nested_input(f, repo, c, form)
                    if when == 'shallow': actual.rename(d)
                    else: assert actual == d
                    preserved.append(candidate_contents(c, repo / '.git/worktrees' / c.name))
                    events.append('introduced literal nested .git')
                if when in ('initial', 'shallow', 'heal'): add()
                os.utime(c, (time.time() - 7200, time.time() - 7200))
                snapshots = []
                def snap(path, deadline, **kw):
                    value = realsnap(path, deadline, **kw)
                    if Path(path) == c:
                        snapshots.append(value)
                        if when == 'second_snapshot' and len(snapshots) == 1: add()
                        if any(k != '.git' and Path(k).name == '.git' for k in value): seen.append('completed match')
                    return value
                def admit(candidate, evidence, deadline, **kw):
                    if candidate['path'] == str(c) and when == 'fresh' and 'first admitted' in events:
                        add()
                    value = realadmit(candidate, evidence, deadline, **kw)
                    if candidate['path'] == str(c): events.append('first admitted')
                    if candidate['path'] == str(q) and when == 'heal' and 'fixture healed' not in events:
                        # The explicit fixture change occurs AFTER C's actual
                        # retained checkpoint; later Q admission cannot clear it.
                        assert seen and d.exists(); d.unlink(); events.append('fixture healed')
                    return value
                def git(root, deadline, *args):
                    calls.append((str(root), args))
                    if args[:2] == ('worktree', 'remove') and args[-1] == str(c) and when in ('repair', 'retry'):
                        # Genuine exit129 before mutation, not a permission failure.
                        try: return realgit(root, deadline, 'worktree', 'remove', '--invalid-fixture-option', str(c))
                        finally:
                            events.append('real Git exit129')
                            if when == 'repair': add()
                    return realgit(root, deadline, *args)
                def mutate(path, tree, deadline, **kw):
                    value = realmutate(path, tree, deadline, **kw)
                    if path == c:
                        assert kw.get('remove') is False and value, value
                        repairs.append(value)
                        if when == 'retry': add()
                    return value
                with patch.object(ds, 'admit', admit), patch.object(ds, 'snapshot', snap), \
                        patch.object(ds, 'git', git), patch.object(ds, 'mutate', mutate):
                    report = f.sweep(seconds=25)
                prior = when in ('repair', 'retry')
                counted(report, 2, 0 if prior else 1, int(prior), int(when in ('initial','shallow','heal','second_snapshot')), partial=scope != 'none')
                r = row(report, c)
                assert r['state'] == ('partial_or_unknown' if prior else 'retained'), r
                assert r['reason'] == 'possible_nested_worktree:' + str(d), r
                assert report['retained_reasons'] == {'possible_nested_worktree': 1}, report
                assert seen and c.exists() and row(report, q)['state'] == 'removed' and not q.exists()
                assert r.get('removed_attributable_bytes') is None and not r.get('retry_count'), r
                attempts = [args for _,args in calls if args[:2] == ('worktree','remove') and args[-1] == str(c)]
                assert len(attempts) == int(prior) and len(repairs) == int(when == 'retry'), (calls, repairs)
                assert bool(r.get('first_remove_error')) == prior
                if e: residue_scope(report, e)
                else:
                    assert report['discovery_scopes'] == [] and report.get('prune_retained') is None, report
                if when != 'heal': assert preserved[-1] == candidate_contents(c, repo / '.git/worktrees' / c.name)
                else: assert 'fixture healed' in events and not d.exists()
                assert refs == f.git(repo, 'show-ref')
                assert not any(args[:2] == ('worktree','prune') for _,args in calls), calls
                observations.append(dict(row=label, events=events, snapshots=len(snapshots), result='PASS', reason=r['reason']))
        except Exception as exc: failures.append(dict(row=label, error=str(exc)))
    for failure in failures: print(json.dumps(dict(regression=failure)), flush=True)
    assert not failures, failures
    # Real cross-repository P/W inside Z/C: P alone loses its pointer.
    for preview in (False, True):
        with fixture() as f:
            z, c = f.worktree('TASK-1', 'a-Z'); _, q = f.worktree('TASK-2', 'a-Z')
            p, original = f.worktree('TASK-3', 'z-P')
            f.git(p, 'worktree', 'remove', str(original))
            w = c / 'ignored/nested'; w.parent.mkdir()
            f.git(p, 'worktree', 'add', '--detach', str(w), 'HEAD')
            e = p / '.git/worktrees/nested'; (e / 'gitdir').unlink()
            before = candidate_contents(c, e); refs = f.git(p,'show-ref')
            with refuse_mutations() if preview else ExitStack(): report = f.sweep('dry_run' if preview else 'on')
            counted(report,2,1,m=1); residue_scope(report,e)
            retained(report,c,'possible_nested_worktree:'+str(w/'.git'))
            assert row(report,q)['state'] == ('eligible' if preview else 'removed')
            assert before == candidate_contents(c,e) and refs == f.git(p,'show-ref')
            observations.append(dict(row='C21-real-P-Z',preview=preview,result='PASS'))
    return observations


def residue_topology_transitions():
    """Existing pre/post-Git censuses must include unmapped entry identity."""
    observations=[]
    for when in ('pre_git','post_git'):
        for kind in ('add','remove','rename','restore_pointer','unregistered_root','replace_inode'):
            with fixture() as f:
                repo,w=f.worktree('TASK-1','a-unknown');_,b=f.worktree('TASK-2','a-unknown')
                e=residue_input(f,repo,w); x=f.scratch('TASK-3');_,z=f.worktree('TASK-4','z-safe')
                refs=f.git(repo,'show-ref');events=[];calls=[]
                realmeta,realgit=ds._registration_metadata,ds.git
                def change():
                    if kind=='add': (e.parent/'new-empty').mkdir()
                    elif kind=='remove': shutil.rmtree(e)
                    elif kind=='rename': e.rename(e.with_name('renamed'))
                    elif kind=='restore_pointer': (e/'gitdir').write_text(str(w/'.git')+'\n')
                    elif kind=='unregistered_root': w.mkdir()
                    else:
                        e.rename(e.with_name('held'));e.mkdir()
                    events.append(kind)
                def metadata(root,deadline,**kwargs):
                    value=realmeta(root,deadline,**kwargs)
                    if root==repo and not events and when=='pre_git': change()
                    return value
                def git(root,deadline,*args):
                    calls.append((str(root),args));value=realgit(root,deadline,*args)
                    if root==repo and args[:2]==('worktree','list') and not events and when=='post_git': change()
                    return value
                with patch.object(ds,'_registration_metadata',metadata),patch.object(ds,'git',git):report=f.sweep()
                if kind == 'unregistered_root':
                    counted(report, 4, r=1, m=1)
                    retained(report, w, 'unknown_unregistered_task_worktree:' + str(w))
                    assert row(report, b)['state'] == 'removed' and not b.exists(), report
                    assert not any(v['path'] == str(repo) for v in report['discovery_scopes']), report
                else:
                    counted(report,2)
                    assert b.exists(), report
                    assert not any(c.get('repo')==str(repo) for c in report['candidates']),report
                    assert any(v['path']==str(repo) and v['candidate_count'] is None for v in report['discovery_scopes']),report
                    assert not any(root==str(repo) and args[:2] in (('worktree','remove'),('worktree','prune')) for root,args in calls)
                assert events==[kind],(events,report)
                assert row(report,x)['state']==row(report,z)['state']=='removed'
                assert not any(args[:2] == ('worktree','prune') for _,args in calls), calls
                assert refs==f.git(repo,'show-ref')
                observations.append(dict(row='C21-residue-topology',when=when,kind=kind,result='PASS'))
    return observations


def residue_transitions():
    observations = []
    for kind in ('post_empty', 'post_valid', 'post_own', 'post_bad', 'post_root', 'healed', 'removed', 'replaced'):
        with fixture() as f:
            repo, b = f.worktree('TASK-1')
            entry = None
            if not kind.startswith('post_'):
                _, w = f.worktree('TASK-2'); entry = residue_input(f,repo,w)
            refs = f.git(repo,'show-ref'); calls = []; changed = []; preserved = []
            realdiscover, realgit = ds.discover, ds.git
            def discover(*args, **kw):
                value = realdiscover(*args, **kw)
                if not kind.startswith('post_'):
                    residue_scope(kw['inventory'], entry) if 'unknown_scope' in kw['inventory'] else None
                    if kind == 'healed': (entry/'gitdir').write_text(str(w/'.git')+'\n')
                    elif kind == 'removed': shutil.rmtree(entry)
                    else:
                        entry.rename(entry.with_name('held')); entry.mkdir()
                    changed.append(True)
                return value
            def git(root, deadline, *args):
                calls.append(args); result = realgit(root,deadline,*args)
                if kind.startswith('post_') and args[:2] == ('worktree','remove') and args[-1] == str(b):
                    assert not os.path.lexists(b)
                    e = repo/'.git/worktrees'/('TASK-1' if kind == 'post_own' else 'new-residue')
                    e.mkdir(parents=True)
                    if kind in ('post_valid','post_bad'):
                        (e/'commondir').write_text('../..\n' if kind == 'post_valid' else 'bad\n')
                        (e/'note').write_text('ordinary')
                    if kind == 'post_root': b.mkdir()  # actual post-success fixture input
                    preserved.append((e,candidate_contents(e))); changed.append(True)
                return result
            with patch.object(ds,'discover',discover), patch.object(ds,'git',git): report=f.sweep()
            assert changed and report['discovery']=='partial' and report['unreached'] is None,report
            assert not any(args[:2]==('worktree','prune') for args in calls)
            if kind.startswith('post_'):
                e,before=preserved[0]; assert before==candidate_contents(e)
                if kind not in ('post_bad','post_root'):
                    counted(report,1);residue_scope(report,e)
                    assert row(report,b)['state']=='removed' and row(report,b)['removed_attributable_bytes']>0,report
                else:
                    counted(report,1,u=1);assert row(report,b)['state']=='partial_or_unknown'
                    assert not row(report,b).get('removed_attributable_bytes')
            else:
                counted(report,1,1,m=1)
                assert b.exists() and row(report,b)['state']=='retained'
                assert any(v['path']==str(entry) and v['candidate_count']==0 for v in report['discovery_scopes'])
            assert refs==f.git(repo,'show-ref')
            observations.append(dict(row='C21-residue-transition',kind=kind,result='PASS'))
    return observations


def c21():
    """Transitions wrap actual shipping boundaries, with real Git failures."""
    from contextlib import ExitStack
    observations=[*descendant_cases(), *uncertain_descendants(), *local_registration_transitions(), *nested_cases(), *residue_topology_transitions(), *residue_transitions()]
    for when in ('after_discovery','after_failure','after_repair','partial_failure'):
        for kind in ('admin_alias','unreadable_admin','conflicting_backlink'):
            with fixture() as f, ExitStack() as changes:
                repo,q=f.worktree('TASK-1');_,sibling=f.worktree('TASK-2')
                safe,z=f.worktree('TASK-3','z-safe')
                if when=='partial_failure':q.chmod(0o555)
                calls=[];repairs=[];damage=[];expected=[];late_git=[];first=[True]
                realgit=ds.git;realdiscover=ds.discover;realmutate=ds.mutate
                def change():
                    expected.append(candidate_contents(q,sibling,repo/'.git/worktrees'))
                    damage.append(changes.enter_context(damaged_registration(f,repo,q if when!='partial_failure' else sibling,kind)))
                    expected.append(candidate_contents(damage[0]['external']))
                def discover(*args,**kw):
                    result=realdiscover(*args,**kw)
                    if when=='after_discovery':change()
                    return result
                def git(root,deadline,*args):
                    calls.append((str(root),args))
                    if root==repo and damage:late_git.append(args)
                    if root==repo and args[:2]==('worktree','remove') and first[0]:
                        first[0]=False
                        try:
                            if when=='partial_failure':return realgit(root,deadline,*args)
                            return realgit(root,deadline,'--THR307-controlled-invalid-option')
                        except ds.Retain:
                            if when in ('after_failure','partial_failure'):change()
                            raise
                    return realgit(root,deadline,*args)
                def mutate(path,tree,deadline,*,remove):
                    if not remove:repairs.append(str(path))
                    result=realmutate(path,tree,deadline,remove=remove)
                    if not remove and when=='after_repair':change()
                    return result
                with patch.object(ds,'discover',discover),patch.object(ds,'git',git),patch.object(ds,'mutate',mutate):r=f.sweep()
                assert len(damage)==1 and r['discovery']=='partial' and r['unreached'] is None,r
                assert any(s['path']==str(repo) and s['candidate_count'] is None for s in r['discovery_scopes']),r
                assert row(r,sibling)['state']=='retained' and sibling.exists(),r
                assert row(r,z)['state']=='removed' and not z.exists(),r
                removes=[args for root,args in calls if root==str(repo) and args[:2]==('worktree','remove')]
                assert len(removes)==(0 if when=='after_discovery' else 1),calls
                assert len(repairs)==(1 if when=='after_repair' else 0),repairs
                assert not any(args[:2]==('worktree','prune') for root,args in calls)
                current=row(r,q)
                assert current['state']==('retained' if when=='after_discovery' else 'partial_or_unknown'),r
                assert current.get('removed_attributable_bytes') is None and r['phases']['worktree']['attribution_unknown']==(when!='after_discovery'),r
                assert damage[0]['sentinel'].read_bytes()==b'external control'
                if when=='partial_failure':
                    assert q.exists() and q.stat().st_mode & 0o777==0o555
                    # Git has really unregistered this root on permission failure.
                    held=damage[0]['external']/'held' if kind=='admin_alias' else repo/'.git/worktrees'
                    assert not (held/q.name).exists(),r
                    assert 'first_remove_error' in current
                assert late_git==[],late_git
                assert expected[1]==candidate_contents(damage[0]['external'])
                changes.close()
                assert expected[0]==candidate_contents(q,sibling,repo/'.git/worktrees')
                assert f.git(repo,'show-ref','--verify','refs/heads/task/TASK-1')
                observations.append(dict(when=when,kind=kind,remove_calls=len(removes),repair_calls=len(repairs),candidate_state=current['state'],partial_bytes='unknown; no invented attribution'))
    # After the last removal there is no prune or further Git action. A new
    # alias then is not observed; the confirmed earlier effect stays recorded.
    with fixture() as f:
        repo,q=f.worktree('TASK-1');external=f.base/'late-external';external.mkdir();(external/'sentinel').write_text('keep')
        calls=[];real=ds.git;result={};changed=[False]
        def checkpoint():
            if any(c['state']=='removed' for c in result.get('candidates',[])) and not changed[0]:
                changed[0]=True
                admin=repo/'.git/worktrees'
                if admin.exists():admin.rename(external/'old-empty')
                admin.symlink_to(external,target_is_directory=True)
        def trace(root,deadline,*args):calls.append(args);return real(root,deadline,*args)
        with patch.object(ds,'git',trace):r=ds.sweep_org(f.org,time.monotonic()+12,mode='on',now=NOW,result=result,checkpoint=checkpoint)
        assert changed[0] and r['discovery']=='complete' and r['outcome']=='complete',r
        assert row(r,q)['state']=='removed' and row(r,q)['removed_attributable_bytes']>0 and not q.exists(),r
        assert not any(args[:2]==('worktree','prune') for args in calls)
        assert (external/'sentinel').read_text()=='keep'
        assert f.git(repo,'show-ref','--verify','refs/heads/task/TASK-1')
        observations.append(dict(when='after_final_removal_no_prune',prune_calls=0,earlier_removal='preserved with positive attribution',no_post_prune_census=True))
    return observations


def c22():
    """Primary-only absent-admin control and metadata enumeration deadline."""
    for empty in (False, True):
        with fixture() as f:
            repo,w=f.worktree('TASK-1');e=residue_input(f,repo,w,empty=empty)
            before=candidate_contents(e);calls=[];real=ds.git
            def git(root,deadline,*args):calls.append(args);return real(root,deadline,*args)
            with patch.object(ds,'git',git):report=f.sweep()
            counted(report,0);residue_scope(report,e)
            assert report['candidates']==[] and before==candidate_contents(e)
            assert all(v['measured_allocated_bytes']==v['removed_attributable_bytes']==0 for v in report['phases'].values())
            assert not any(args[:2]==('worktree','prune') for args in calls)
    with fixture() as f:
        repo,q=f.worktree('TASK-1');f.git(repo,'worktree','remove',str(q))
        admin=repo/'.git/worktrees'
        if admin.exists():admin.rmdir()
        p=f.scratch('TASK-2')
        r=f.sweep()
        assert r['discovery']=='complete' and r['outcome']=='complete' and row(r,p)['state']=='removed',r
        assert ds.registrations(repo,time.monotonic()+3).keys()=={str(repo)}
        # A locked registration is valid known inventory, retained by admission.
        _,z=f.worktree('TASK-3');f.git(repo,'worktree','lock',str(z))
        r=f.sweep();retained(r,z,'locked')
        assert f.git(repo,'show-ref','--verify','refs/heads/task/TASK-3')
    with fixture() as f:
        p=f.scratch('TASK-1');repo,q=f.worktree('TASK-2')
        later=f.base/'zz-org';(later/'org').mkdir(parents=True);(later/'org/config.yaml').write_text('disk_sweep: {mode: "off"}')
        real=ds.names;delayed=[False]
        def names(fd,deadline):
            values=real(fd,deadline)
            if os.readlink('/proc/self/fd/'+str(fd))==str(repo/'.git/worktrees'):
                delayed[0]=True;time.sleep(.09)
            return values
        start=time.monotonic();deadline=start+.06
        with patch.object(ds,'names',names),refuse_mutations():
            r=ds.sweep([str(f.org),str(later)],deadline,f.base/'summary.json',dry_run=True,scheduled_date=None,revision='fixture',started_monotonic=start)
        item=r['orgs'][0]
        assert delayed[0] and r['outcome']=='incomplete' and r['unreached_orgs']==[str(later)],r
        assert r['deadline_monotonic']==deadline and r['budget_seconds']==deadline-start
        assert item['discovery']=='unknown' and item['unreached'] is None and item['known_unreached_count']==1,item
        assert row(item,p)['state']=='unreached' and p.exists() and q.exists()
        assert any(s.get('interrupted') and s['path']==str(repo) for s in item['discovery_scopes']),item
        return dict(primary_only_absent_admin='complete; independent scratch removed',locked='retained',metadata_enumeration='original deadline; globally stopped; later org unreached',seconds=time.monotonic()-start)


POLICY_CONFIGS = (
    ('missing_file', None), ('absent_block', ''),
    ('legacy_off', 'disk_sweep: {mode: "off"}'),
    ('legacy_dry_run', 'disk_sweep: {mode: "dry_run"}'),
    ('legacy_on', 'disk_sweep: {mode: "on"}'),
    ('obsolete_null', 'disk_sweep: null'),
    ('obsolete_boolean', 'disk_sweep: {mode: true}'),
    ('obsolete_unknown', 'disk_sweep: {mode: bad}'),
    ('obsolete_extra', 'disk_sweep: {extra: 1}'),
    ('obsolete_scalar', 'disk_sweep: retired'),
)


def config_checks():
    for label, text in POLICY_CONFIGS:
        try:
            cfg = OrgConfig.load_from_text((text or '') + '\ntimezone: Asia/Shanghai\nworkspace_cleanup: {enabled: false}\n')
        except OrgConfigError as exc:
            raise AssertionError(dict(config=label, expected='obsolete setting ignored', observed=str(exc))) from exc
        assert cfg.timezone == 'Asia/Shanghai' and cfg.workspace_cleanup_enabled is False, label
    for text in ('session_timeout_seconds: true', 'workspace_cleanup: {enabled: bad}'):
        try:
            OrgConfig.load_from_text('disk_sweep: null\n' + text)
        except OrgConfigError as exc:
            assert text.split(':')[0] in str(exc), str(exc)
        else:
            raise AssertionError(dict(expected='unrelated validation refuses', observed=text))
    from datetime import datetime
    for hour in (2,3,23):
        now=datetime.now().astimezone().replace(hour=hour)
        assert ds.next_night(now)>now


def isolated_boundary(kind):
    """Run the actual bounded fixture, never a capability-only true probe.

    This one executor attempts unprivileged disposable isolation. EPERM is
    reported without sudo, another executor, a job or provisioning fallback.
    """
    if kind == 'root':
        return dict(result='SUPERSEDED', reason='THR307 seq117 removed process-root retention; historical source not executed')
    if not sys.platform.startswith('linux') or not shutil.which('unshare'):
        return dict(result='UNAVAILABLE',reason='Linux util-linux unshare required')
    namespaces={name:os.readlink('/proc/self/ns/'+name) for name in ('mnt','pid')}
    helper=Path(__file__).with_name('_verify_disk_sweeper_isolation.py')
    command=['unshare','--user','--map-root-user','--mount','--pid','--fork','--kill-child=KILL','--mount-proc','--propagation','private',sys.executable,str(helper),kind,namespaces['mnt'],namespaces['pid']]
    proc=subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,start_new_session=True)
    try:
        stdout,stderr=proc.communicate(timeout=40)
    except subprocess.TimeoutExpired:
        try: os.killpg(proc.pid,signal.SIGKILL)
        except ProcessLookupError: pass
        proc.communicate(timeout=5)
        raise AssertionError('isolated fixture timeout; namespace killed; fixture cleanup unknown')
    finally:
        assert all(os.readlink('/proc/self/ns/'+name)==ident for name,ident in namespaces.items()),'caller namespace changed'
    if proc.returncode:
        if proc.returncode==1 and 'unshare:' in stderr and 'Operation not permitted' in stderr:
            return dict(result='UNAVAILABLE',command=command,exit=proc.returncode,reason=stderr.strip())
        raise AssertionError(f'isolated {kind} fixture exit {proc.returncode}: {stderr} {stdout}')
    result=json.loads(stdout)
    assert result['result'] in ('PASS','UNAVAILABLE'),result
    return dict(result,command=command,exit=proc.returncode)


@contextmanager
def no_process_inspection():
    """Observe Python syscall boundaries; allow real self mountinfo containment.

    This is a sentinel, not host namespace/OS-denial proof. Count attempts even
    if shipping code swallows the sentinel exception and reports retention.
    """
    attempts, mount_reads = [], []
    def guarded(operation, original):
        def call(path, *args, **kwargs):
            value = os.fsdecode(path) if isinstance(path, (str, bytes, os.PathLike)) else ''
            if value == '/proc/self/mountinfo':
                mount_reads.append(operation)
            elif value == '/proc' or value.startswith('/proc/'):
                attempts.append(dict(operation=operation, path=value))
                raise AssertionError('unexpected process inspection: ' + value)
            return original(path, *args, **kwargs)
        return call
    with ExitStack() as stack:
        for owner, name in ((builtins, 'open'), (io, 'open'), (os, 'open'),
                            (os, 'listdir'), (os, 'scandir'), (os, 'readlink'),
                            (os, 'stat'), (os, 'lstat')):
            stack.enter_context(patch.object(owner, name, guarded(name, getattr(owner, name))))
        try:
            yield dict(attempts=attempts, mount_reads=mount_reads)
        finally:
            assert attempts == [], attempts


def correction_jobs():
    with fixture() as f:
        p = f.scratch('TASK-1')
        repo, q = f.worktree('TASK-2')
        f.task('TASK-3')
        f.db.execute("UPDATE tasks SET parent_task_id='TASK-3' WHERE id IN ('TASK-1','TASK-2')")
        finished = [('missing-owner', status) for status in ('completed', 'failed', 'rejected')]
        f.db.executemany('INSERT INTO jobs VALUES(?,?)', finished)
        f.db.commit()
        before = candidate_contents(p, q)
        registered = f.git(repo, 'worktree', 'list', '--porcelain')
        observations = []
        with refuse_mutations():
            r = f.sweep('dry_run')
            assert row(r, p)['state'] == row(r, q)['state'] == 'eligible', r
            assert f.db.execute('SELECT task_id,status FROM jobs').fetchall() == finished
            for owner, status, reason in [('missing-owner', 'running', 'lineage'), ('missing-owner', 'alien', 'unknown_job_status'), ('TASK-3', 'running', 'active_job')]:
                f.db.execute('INSERT INTO jobs VALUES(?,?)', (owner, status)); f.db.commit()
                r = f.sweep('dry_run')
                retained(r, p, reason); retained(r, q, reason)
                observations.append(dict(owner=owner, status=status, reason=row(r, p)['reason']))
                f.db.execute('DELETE FROM jobs WHERE status=?', (status,)); f.db.commit()
        assert candidate_contents(p, q) == before
        assert f.git(repo, 'worktree', 'list', '--porcelain') == registered
        assert f.db.execute('SELECT task_id,status FROM jobs').fetchall() == finished
        return dict(finished_orphans='both phases eligible; rows unchanged', paired_refusals=observations, filesystem_and_registration_unchanged=True)


def correction_superseded():
    with fixture() as f:
        p = f.scratch('TASK-1', status='superseded')
        repo, q = f.worktree('TASK-2')
        f.task('TASK-3', status='superseded')
        f.db.execute("UPDATE tasks SET status='superseded',parent_task_id='TASK-3' WHERE id IN ('TASK-1','TASK-2')"); f.db.commit()
        before = candidate_contents(p, q)
        registered = f.git(repo, 'worktree', 'list', '--porcelain')
        observations = []
        with refuse_mutations():
            r = f.sweep('dry_run')
            assert row(r, p)['state'] == row(r, q)['state'] == 'eligible', r
            assert all(phase['measured_allocated_bytes'] > 0 for phase in r['phases'].values()), r
            for target in ('owner', 'root'):
                ids = ('TASK-1', 'TASK-2') if target == 'owner' else ('TASK-3',)
                for status in ('in_progress', 'escalated', 'alien'):
                    f.db.executemany('UPDATE tasks SET status=? WHERE id=?', [(status, task) for task in ids]); f.db.commit()
                    r = f.sweep('dry_run')
                    retained(r, p, 'task_or_root_not_terminal'); retained(r, q, 'task_or_root_not_terminal')
                    observations.append(dict(target=target, status=status, both='retained'))
                f.db.executemany("UPDATE tasks SET status='superseded' WHERE id=?", [(task,) for task in ids]); f.db.commit()
        assert candidate_contents(p, q) == before
        assert f.git(repo, 'worktree', 'list', '--porcelain') == registered
        return dict(superseded_owner_and_root='both phases dry-run eligible; measured allocation positive', paired_refusals=observations, filesystem_and_registration_unchanged=True)


def correction_processes():
    """C3 seq117: ended trees eligible without any process-use inspection."""
    with fixture() as f:
        p = f.scratch('TASK-1')
        repo, q = f.worktree('TASK-2')
        f.task('TASK-3')
        f.db.execute("UPDATE tasks SET parent_task_id='TASK-3' WHERE id IN ('TASK-1','TASK-2')")
        f.db.commit()
        out = f.workspace / 'output/TASK-1'
        out.mkdir(parents=True); (out / 'keep').write_text('durable output')
        sidecars = f.workspace / '.happyranch/task-scratch-manifests'
        (q / 'ignored').mkdir(); (q / 'ignored/cache').write_bytes(b'cache' * 2048)
        (p / 'nested').chmod(0o555); (q / 'ignored').chmod(0o555)
        excluded = candidate_contents(out, sidecars)
        registered = f.git(repo, 'worktree', 'list', '--porcelain')
        branches = f.git(repo, 'show-ref', '--heads')
        summary = f.base / 'summary.json'
        expected_scope = 'not checked under THR307 seq117; ended task and structural root required; no process absence or authentication claim'
        projections = []
        def run(label):
            before = candidate_contents(p, q)
            # Only SQLite lineage/jobs and wall clock are synthetic. Exercise
            # actual config, filesystem/Git, wrapper, JSON and audit projection.
            with no_process_inspection() as calls, refuse_mutations(), patch.object(time, 'time', return_value=NOW):
                report = ds.sweep([str(f.org)], time.monotonic() + 12, summary, dry_run=True, scheduled_date=None, revision='fixture')
            persisted = json.loads(summary.read_text())
            assert persisted == report
            assert persisted['process_scope'] == expected_scope
            assert report['format'] == 'disk-sweep@1' and report['revision'] == 'fixture'
            assert report['outcome'] == 'complete' and report['unreached_orgs'] == []
            assert report['selected_org_roots'] == [str(f.org)] and len(report['orgs']) == 1
            result = report['orgs'][0]
            assert result['org'] == f.org.name and 'configured_mode' not in result and result['mode'] == 'dry_run', result
            assert result['process_scope'] == expected_scope and result['process_exemptions'] == []
            assert all(not c.get('process_exemptions') for c in result['candidates'])
            assert all(phase['removed_attributable_bytes'] == 0 for phase in result['phases'].values())
            audit = ds._audit_payload(report, result, summary)
            assert audit['org'] == result and audit['revision'] == report['revision']
            assert audit['org']['process_scope'] == expected_scope and audit['org']['process_exemptions'] == []
            assert candidate_contents(p, q) == before
            assert candidate_contents(out, sidecars) == excluded
            assert f.git(repo, 'worktree', 'list', '--porcelain') == registered
            assert f.git(repo, 'show-ref', '--heads') == branches
            assert calls['attempts'] == [] and calls['mount_reads'], calls
            projections.append(dict(control=label, report=persisted, audit=audit,
                                    process_attempts=calls['attempts'], mountinfo_reads=len(calls['mount_reads'])))
            return result
        r = run('ended_owner_and_root')
        assert row(r, p)['state'] == row(r, q)['state'] == 'eligible', r
        assert all(phase['measured_allocated_bytes'] > 0 for phase in r['phases'].values()), r
        source = (q / 'source').read_bytes()
        (q / 'source').write_text('dirty tracked source')
        r = run('dirty_worktree')
        retained(r, q, 'dirty_or_untracked')
        assert row(r, p)['state'] == 'eligible', r
        (q / 'source').write_bytes(source)
        for path in (p / 'nested/payload', q / 'ignored/cache'):
            os.utime(path, (NOW, NOW))
        r = run('recent_write_both_phases')
        retained(r, p, 'changed_within_hour'); retained(r, q, 'changed_within_hour')
        assert r['retained_allocation_unknown_count'] == 2
        return dict(actual_summary_and_audit_projection=True, top_level_wrapper_and_serialized_metadata=True,
                    process_scope=expected_scope, projections=projections,
                    filesystem_modes_registrations_branches_output_sidecars_unchanged_by_sweep=True,
                    limits='Python syscall sentinels only; no actual host namespace or OS-denial proof; no process started or inspected')


def policy_config(f, text):
    path = f.org / 'org/config.yaml'
    if text is None:
        path.unlink()
    else:
        path.write_text(text + '\n')


def policy_sweep(f, *, dry_run=False, discover=False):
    summary = f.base / 'policy-summary.json'
    with patch.object(time, 'time', return_value=NOW):
        report = ds.sweep([] if discover else [str(f.org)], time.monotonic() + 12, summary,
                          dry_run=dry_run, scheduled_date=None if dry_run else 'fixture-date',
                          revision='policy-fixture', runtime_root=f.base / 'runtime' if discover else None)
    assert json.loads(summary.read_text()) == report
    return report, summary


def correction_policy():
    observations = []
    for label, text in POLICY_CONFIGS:
        with fixture() as f:
            policy_config(f, text)
            p = f.scratch('TASK-1'); repo, q = f.worktree('TASK-2')
            output = f.workspace / 'output/TASK-1'; output.mkdir(parents=True)
            (output / 'keep').write_text('durable output')
            sidecars = f.workspace / '.happyranch/task-scratch-manifests'
            excluded = candidate_contents(output, sidecars)
            branches = f.git(repo, 'show-ref', '--heads')
            report, summary = policy_sweep(f, discover=label == 'missing_file')
            item = report['orgs'][0]
            assert item.get('mode') == 'on', dict(config=label, expected='on', observed=item)
            assert 'configured_mode' not in item, item
            assert report['outcome'] == item['outcome'] == 'complete', report
            assert report['selected_org_roots'] == [str(f.org)] and report['unreached_orgs'] == []
            assert row(item, p)['state'] == row(item, q)['state'] == 'removed', item
            assert not p.exists() and not q.exists()
            assert str(q) not in f.git(repo, 'worktree', 'list', '--porcelain')
            assert f.git(repo, 'show-ref', '--heads') == branches
            assert all(v['removed_attributable_bytes'] > 0 for v in item['phases'].values()), item
            assert candidate_contents(output, sidecars) == excluded
            audit = ds._audit_payload(report, item, summary)
            assert audit['org'] == item and audit['outcome'] == 'complete', audit
            observations.append(dict(config=label, report=report, audit=audit, retained_local_branches=True))
    config_checks()
    return dict(variants=observations, unrelated_config_validation='retained',
                discovery='missing-file org discovered through actual runtime wrapper; no OrgState admission claim')


def projection_input(f, kind, c=None):
    """Extend existing preview/scheduled fixtures, never production admission."""
    if c is None: repo, c = f.worktree('TASK-2')
    else: repo = c.parent.parent.parent
    e = d = q = None
    if kind != 'root' and kind != 'none':
        p, w = f.worktree('TASK-3', 'z-P' if kind == 'cross' else repo.name)
        if kind == 'cross':
            f.git(p,'worktree','remove',str(w))
            w = c/'ignored/foreign'; w.parent.mkdir()
            f.git(p,'worktree','add','--detach',str(w),'HEAD')
            e=p/'.git/worktrees/foreign'; (e/'gitdir').unlink(); d=w/'.git'
        else: e = residue_input(f,p,w)
    if kind in ('same','none'): d = nested_input(f,repo,c)
    if kind == 'root':
        (c/'ignored').mkdir(); (c/'ignored/.gitignore').write_text('ordinary name')
    if kind in ('same','cross','none','local'):
        _,q=f.worktree('TASK-4',repo.name)
    if kind=='local':
        (repo/'.git/worktrees'/c.name/'index').chmod(0)
    return repo,c,q,e,d


def projection_assert(report, repo, c, q, e, d, *, preview, scratch=0):
    held = q is not None
    counted(report, 1 + int(held) + scratch, int(held), m=int(held), partial=e is not None)
    if e: residue_scope(report,e)
    if held:
        retained(report,c, 'possible_nested_worktree:'+str(d) if d else str(repo/'.git/worktrees'/c.name/'index'))
    else: assert row(report,c)['state']==('eligible' if preview else 'removed'),report
    if q: assert row(report,q)['state']==('eligible' if preview else 'removed'),report
    if d:
        assert row(report,c)['reason']=='possible_nested_worktree:'+str(d),report
        assert report['retained_reasons']=={'possible_nested_worktree':1},report


def residue_preview():
    observations=[]
    for kind in ('residue','local','same','cross','none','root'):
        with fixture() as f:
            repo,c,q,e,d=projection_input(f,kind)
            # Real unreadable-index bytes cannot be read by the preservation
            # oracle. Capture its ordinary mode separately after the run.
            before=candidate_contents(c, *([e] if e else [])) if kind!='local' else None
            refs=f.git(repo,'show-ref'); calls=[];real=ds.git
            def git(root,deadline,*args): calls.append(args);return real(root,deadline,*args)
            with patch.object(ds,'git',git),refuse_mutations():
                batch,summary=policy_sweep(f,dry_run=True)
                ds._manual_audit(batch,[str(f.org)],summary)
            report=batch['orgs'][0]
            projection_assert(report,repo,c,q,e,d,preview=True)
            saved=f.db.execute("SELECT task_id,agent,payload FROM audit_log WHERE action='disk_sweep_summary'").fetchall()
            assert len(saved)==1 and saved[0][:2]==('', 'daemon')
            assert json.loads(saved[0][2])['org']==report and json.loads(summary.read_text())==batch
            assert not any(args[:2] in (('worktree','remove'),('worktree','prune')) for args in calls)
            if before: assert before==candidate_contents(c,*([e] if e else []))
            assert refs==f.git(repo,'show-ref')
            observations.append(dict(row='correction5-residue',kind=kind,result='PASS'))
    return observations


def correction_preview():
    observations = []
    for label, text in POLICY_CONFIGS:
        with fixture() as f:
            policy_config(f, text)
            p = f.scratch('TASK-1'); repo, q = f.worktree('TASK-2')
            (p / 'nested').chmod(0o555)
            output = f.workspace / 'output/TASK-1'; output.mkdir(parents=True)
            (output / 'keep').write_text('durable output')
            sidecars = f.workspace / '.happyranch/task-scratch-manifests'
            before = candidate_contents(p, q, output, sidecars)
            registrations = f.git(repo, 'worktree', 'list', '--porcelain')
            branches = f.git(repo, 'show-ref', '--heads')
            with refuse_mutations():
                report, summary = policy_sweep(f, dry_run=True)
                ds._manual_audit(report, [str(f.org)], summary)
            item = report['orgs'][0]
            assert item.get('mode') == 'dry_run', dict(config=label, expected='dry_run', observed=item)
            assert report['outcome'] == item['outcome'] == 'complete', report
            assert row(item, p)['state'] == row(item, q)['state'] == 'eligible', item
            assert all(v['removed_attributable_bytes'] == 0 and v['measured_allocated_bytes'] > 0 for v in item['phases'].values()), item
            saved = f.db.execute("SELECT task_id,agent,payload FROM audit_log WHERE action='disk_sweep_summary'").fetchall()
            assert len(saved) == 1 and saved[0][:2] == ('', 'daemon'), saved
            payload = json.loads(saved[0][2])
            assert payload['org'] == item and payload['org']['mode'] == 'dry_run'
            assert payload['outcome'] == 'complete' and payload['revision'] == 'policy-fixture'
            assert payload['scheduled_date'] is None and payload['summary_path'] == str(summary)
            assert payload['unreached_orgs'] == [] and payload['interruption'] is None
            if label == 'legacy_off':
                command = [sys.executable, '-m', 'runtime.daemon.disk_sweeper', '--dry-run', '--runtime-root', str(f.base / 'runtime')]
                cli = subprocess.run(command, env={**os.environ, 'HAPPYRANCH_DAEMON_HOME': str(f.base / 'daemon')}, capture_output=True, text=True, timeout=12)
                assert cli.returncode == 0, cli.stderr
                cli_report = json.loads(cli.stdout)['report']
                assert cli_report['orgs'][0]['mode'] == 'dry_run' and cli_report['orgs'][0]['discovery'] == 'complete', cli_report
                # Child clock is real: these newly created fixtures are recent.
                assert all(c['state'] == 'retained' for c in cli_report['orgs'][0]['candidates']), cli_report
                denied = subprocess.run(command[:3] + ['--runtime-root', str(f.base / 'runtime')], capture_output=True, text=True, timeout=5)
                assert denied.returncode == 2 and 'manual use requires --dry-run' in denied.stderr, denied.stderr
            assert candidate_contents(p, q, output, sidecars) == before
            assert f.git(repo, 'worktree', 'list', '--porcelain') == registrations
            assert f.git(repo, 'show-ref', '--heads') == branches
            observations.append(dict(config=label, report=report, audit=json.loads(saved[0][2]), mutations=0))
    return dict(variants=observations, residues=residue_preview(), cli='legacy off inventories; no dry-run flag refuses exit2',
                limits='child CLI uses real clock; in-process preview uses synthetic old-ctime clock')


def correction_fresh_policy():
    observations = []
    for transition in ('owner_active', 'root_active', 'active_job', 'unknown_job', 'legacy_off'):
        with fixture() as f:
            p = f.scratch('TASK-1'); repo, q = f.worktree('TASK-2'); f.task('TASK-3')
            f.db.execute("UPDATE tasks SET parent_task_id='TASK-3' WHERE id IN ('TASK-1','TASK-2')"); f.db.commit()
            before = candidate_contents(p, q)
            original = ds.atomic_json; changed = []
            def checkpoint(path, report):
                original(path, report)
                if changed or not report['orgs']:
                    return
                if not any(c['state'] == 'mutation_in_flight' for c in report['orgs'][0].get('candidates', [])):
                    return
                changed.append(transition)
                if transition == 'owner_active':
                    f.db.execute("UPDATE tasks SET status='in_progress' WHERE id IN ('TASK-1','TASK-2')")
                elif transition == 'root_active':
                    f.db.execute("UPDATE tasks SET status='in_progress' WHERE id='TASK-3'")
                elif transition in ('active_job', 'unknown_job'):
                    f.db.execute('INSERT INTO jobs VALUES(?,?)', ('TASK-3', 'running' if transition == 'active_job' else 'alien'))
                else:
                    policy_config(f, 'disk_sweep: {mode: "off"}')
                f.db.commit()
            with patch.object(ds, 'atomic_json', checkpoint):
                report, _ = policy_sweep(f)
            assert changed == [transition], changed
            item = report['orgs'][0]
            if transition == 'legacy_off':
                assert row(item, p)['state'] == row(item, q)['state'] == 'removed', dict(expected='legacy config cannot veto fresh admission', observed=item)
                assert not p.exists() and not q.exists()
            else:
                reason = 'task_or_root_not_terminal' if transition in ('owner_active', 'root_active') else 'active_job' if transition == 'active_job' else 'unknown_job_status'
                retained(item, p, reason); retained(item, q, reason)
                assert candidate_contents(p, q) == before
                assert all(v['removed_attributable_bytes'] == 0 for v in item['phases'].values()), item
            observations.append(dict(transition=transition, report=report))
    for transition in ('legacy_off', 'obsolete_null', 'active_job'):
        with fixture() as f:
            repo, q = f.worktree('TASK-1')
            q.chmod(0o555)
            before = candidate_contents(q)
            real = ds.git; removes = []; repairs = []
            real_mutate = ds.mutate
            def git(root, deadline, *args):
                if args[:2] == ('worktree', 'remove'):
                    removes.append(args)
                    if len(removes) == 1:
                        if transition == 'active_job':
                            f.db.execute("INSERT INTO jobs VALUES('TASK-1','running')"); f.db.commit()
                        else:
                            policy_config(f, 'disk_sweep: {mode: "off"}' if transition == 'legacy_off' else 'disk_sweep: null')
                        return real(root, deadline, '--THR307-controlled-invalid-option')
                return real(root, deadline, *args)
            def mutate(*args, **kwargs):
                repairs.append(kwargs['remove'])
                return real_mutate(*args, **kwargs)
            with patch.object(ds, 'git', git), patch.object(ds, 'mutate', mutate):
                report, _ = policy_sweep(f)
            item = row(report['orgs'][0], q)
            if transition == 'active_job':
                assert item['state'] == 'partial_or_unknown' and 'active_job' in item['reason'], item
                assert len(removes) == 1 and repairs == [] and candidate_contents(q) == before
                assert report['outcome'] == 'incomplete' and report['orgs'][0]['phases']['worktree']['attribution_unknown'], report
            else:
                assert item['state'] == 'removed' and item.get('retry_count') == 1, dict(expected='one policy-independent repair/retry', observed=item)
                assert len(removes) == 2 and repairs == [False] and not q.exists()
            assert 'task/TASK-1' in f.git(repo, 'branch', '--list')
            observations.append(dict(retry_transition=transition, report=report, remove_calls=len(removes), repair_calls=len(repairs)))
    return dict(transitions=observations, seam='actual persisted mutation checkpoint and actual first Git exit129; no gate mutation')


def correction_policy_reporting():
    from types import SimpleNamespace
    import threading
    from runtime.orchestrator import producer_move
    observations = []
    for partial in (True, False):
        with fixture() as f, fixture() as deferred:
            policy_config(f, 'disk_sweep: {mode: "dry_run"}')
            p = f.scratch('TASK-1')
            if partial:
                (f.workspace / 'repos/not-git').mkdir(parents=True)
            # Real owner_for refuses an installed-but-unowned optional layout.
            # No live schema installation: minimal disposable refusal fixture.
            deferred.db.execute('CREATE TABLE team_move_operations(active_slot INTEGER)'); deferred.db.commit()
            deferred_root = deferred.org.with_name('zz-deferred')
            deferred.org.rename(deferred_root)
            def org(root, conn):
                return SimpleNamespace(root=root, db=SimpleNamespace(_conn=conn, _lock=threading.RLock()))
            orgs = {f.org.name: org(f.org, f.db), 'zz-deferred': org(deferred_root, deferred.db)}
            state = SimpleNamespace(orgs=orgs, orgs_snapshot=lambda: dict(orgs))
            dispositions = []
            actual_disposition = producer_move.NativeWriterDisposition
            def disposition():
                value = actual_disposition(); dispositions.append(value); return value
            async def in_process(roots, summary, **kwargs):
                assert kwargs['dry_run'] is False and kwargs['scheduled_date'] == 'fixture-date', kwargs
                assert roots == [str(f.org)], roots
                with patch.object(time, 'time', return_value=NOW):
                    report = ds.sweep(roots, time.monotonic() + kwargs['budget'], summary,
                                      dry_run=False, scheduled_date=kwargs['scheduled_date'], revision='policy-fixture',
                                      native_writers=kwargs['native_writers'])
                report['worker_group_stop'] = 'SIMULATED supervision only; actual in-process sweep'
                report['worker_returncode'] = 0
                ds.atomic_json(summary, report)
                return report
            summary = f.base / 'scheduled-summary.json'
            with patch.object(producer_move, 'NativeWriterDisposition', disposition), patch.object(ds, 'run_worker', in_process):
                report = asyncio.run(ds._run_scheduled_worker(state, summary, 'fixture-date'))
            # Partial discovery with every candidate closed is a known effect.
            assert len(dispositions) == 2 and dispositions[0].uncertain is False, dict(expected_uncertain=False, partial=partial, observed=[v.uncertain for v in dispositions], report=report)
            assert report['outcome'] == 'incomplete' and report['move_deferred_orgs'] == {'zz-deferred': 'team_move_native_owner_unavailable'}, report
            assert report['selected_org_roots'] == [str(f.org)] and report['unreached_orgs'] == []
            assert json.loads(summary.read_text()) == report
            item = report['orgs'][0]
            assert item['mode'] == 'on' and 'configured_mode' not in item, item
            assert row(item, p)['state'] == 'removed' and not p.exists(), item
            assert item['discovery'] == ('partial' if partial else 'complete'), item
            if partial:
                assert item['unreached'] is None and item['discovery_scopes'], item
            audit = ds._audit_payload(report, item, summary)
            assert audit['move_deferred_orgs'] == report['move_deferred_orgs'] and audit['outcome'] == 'incomplete'
            assert ds._audit_payload(report, None, summary)['org'] is None
            observations.append(dict(partial=partial, report=report, audit=audit, native_uncertain=dispositions[0].uncertain))
    return dict(runs=observations, limits='actual native reservation and missing-owner refusal; no installed-layout success or worker process supervision proof')



# S1-S5 extend the existing corrections lane (8-12); C1-C7 remain owners of
# their original safety contracts. No production policy is implemented here.
@contextmanager
def scheduled_fixture():
    from types import SimpleNamespace
    import threading
    with fixture() as f:
        others = []
        for name in ('missing', 'new'):
            (f.base / name).mkdir()
            others.append(Fixture(f.base / name))
        for g in others:
                policy_config(g, None)
        orgs = [f, *others]
        policy_config(f, 'disk_sweep: {mode: "off"}')
        for name, g in zip(('off', 'missing', 'new'), orgs):
            g.db.close()
            target = g.org.with_name(name); g.org.rename(target)
            g.org = target; g.workspace = target / 'workspaces/maker'
            g.db = sqlite3.connect(target / 'happyranch.db', check_same_thread=False)
        paths, refs = [], []
        for g in orgs:
            paths.append(g.scratch('TASK-1'))
            repo, q = g.worktree('TASK-2')
            paths.append(q)
            out = g.workspace / 'output/TASK-1'; out.mkdir(parents=True)
            (out / 'keep').write_text('durable')
            paths.extend([out, g.workspace / '.happyranch/task-scratch-manifests'])
            refs.append((repo, g.git(repo, 'show-ref', '--heads'), g.git(repo, 'worktree', 'list', '--porcelain')))
        import runtime.infrastructure.database  # noqa: F401
        from runtime.infrastructure.db.audit import AuditMixin
        class Audit(AuditMixin):
            def __init__(self, g):
                self._conn = g.db
                self._lock = threading.RLock()
        loaded = {name: SimpleNamespace(root=g.org, db=Audit(g)) for name, g in zip(('off', 'missing', 'new'), orgs)}
        state = SimpleNamespace(runtime=True, broken_orgs={}, orgs=loaded, orgs_snapshot=lambda: dict(loaded))
        stop = f.base / 'daemon/disk-sweep/stop.json'
        summary = stop.with_name('scheduled.json')
        try:
            yield f, orgs, state, stop, summary, paths, refs
        finally:
            for g in others:
                g.db.close()


def scheduled_attempt(state, summary, *, forbidden=False):
    from runtime.orchestrator import producer_move
    calls = []
    actual_writer = producer_move.native_writer
    @contextmanager
    def writer(*args, **kwargs):
        calls.append('reservation')
        assert not forbidden, dict(expected='zero writer reservations under global suspension', observed=calls)
        with actual_writer(*args, **kwargs) as value:
            yield value
    async def worker(roots, summary, **kwargs):
        calls.append('worker')
        assert not forbidden, dict(expected='zero worker launches under global suspension', observed=calls)
        with patch.object(time, 'time', return_value=NOW):
            report = ds.sweep(roots, time.monotonic() + kwargs['budget'], summary,
                              dry_run=False, scheduled_date=kwargs['scheduled_date'], revision='stop-fixture',
                              native_writers=kwargs['native_writers'])
        report['worker_group_stop'] = 'SIMULATED supervision; actual disposable sweep'
        report['worker_returncode'] = 0
        ds.atomic_json(summary, report)
        return report
    with patch.object(producer_move, 'native_writer', writer), patch.object(ds, 'run_worker', worker):
        report = asyncio.run(ds._run_scheduled_worker(state, summary, 'fixture-date'))
    return report, calls


def assert_suspended(report, reason, summary):
    assert report.get('outcome') == 'suspended', dict(expected='suspended', observed=report)
    admission = report['scheduled_admission']
    assert admission['scope'] == 'daemon' and admission['state'] == 'suspended' and admission['reason'] == reason, admission
    assert report['orgs'] == [] and len(report['unreached_orgs']) == 3
    assert not any(key in report for key in ('worker_pid', 'worker_group_stop', 'removed_attributable_bytes'))
    assert json.loads(summary.read_text()) == report
    payload = ds._audit_payload(report, None, summary)
    assert payload['scheduled_admission'] == admission and payload['outcome'] == 'suspended' and payload['org'] is None, payload


def residue_scheduled():
    observations=[]
    for kind in ('residue','local','same','cross','none','root','earlier_org'):
        with scheduled_fixture() as (f,orgs,state,stop,summary,paths,refs):
            later=orgs[0]
            if kind=='earlier_org':
                early=orgs[1]; z=paths[5].parent.parent.parent; c=paths[5]
                p=refs[0][0]; _,w=later.worktree('TASK-3',p.name)
                later.git(p,'worktree','remove',str(w))
                w=c/'ignored/foreign';w.parent.mkdir()
                later.git(p,'worktree','add','--detach',str(w),'HEAD')
                e=p/'.git/worktrees/foreign';(e/'gitdir').unlink();d=w/'.git'
                q=None; repo=z
            else: repo,c,q,e,d=projection_input(later,kind,paths[1])
            events=[];calls=[]; realmeta,realgit=ds._registration_metadata,ds.git
            def metadata(root,deadline,**kwargs):
                value=realmeta(root,deadline,**kwargs)
                if kind=='earlier_org' and root==p and getattr(value, 'residues', {}):
                    events.append('later residue census')
                return value
            realatomic=ds.atomic_json
            def atomic(path,value):
                realatomic(path,value)
                if kind=='earlier_org':
                    for item in value.get('orgs',[]):
                        if item.get('org')==early.org.name and any(x['path']==str(c) and x.get('reason')=='possible_nested_worktree:'+str(d) for x in item.get('candidates',[])):
                            events.append('earlier retained checkpoint')
            def git(root,deadline,*args):
                calls.append((str(root),args));return realgit(root,deadline,*args)
            before=candidate_contents(c,*([e] if e else [])) if kind!='local' else None
            with patch.object(ds,'_registration_metadata',metadata),patch.object(ds,'atomic_json',atomic),patch.object(ds,'git',git):
                batch,supervision=scheduled_attempt(state,summary)
            if kind=='earlier_org':
                report=next(x for x in batch['orgs'] if x['org']==early.org.name)
                counted(report,2,1,m=1,partial=False)
                retained(report,c,'possible_nested_worktree:'+str(d))
                assert report['discovery_scopes']==[] and report.get('prune_retained') is None
                late=next(x for x in batch['orgs'] if x['org']==later.org.name)
                counted(late,2);residue_scope(late,e)
                assert row(late,paths[1])['state']=='removed'
                assert events.index('earlier retained checkpoint')<events.index('later residue census'),events
                assert not any(root in (str(p),str(z)) and args[:2]==('worktree','prune') for root,args in calls)
            else:
                report=next(x for x in batch['orgs'] if x['org']==later.org.name)
                projection_assert(report,repo,c,q,e,d,preview=False,scratch=1)
                assert not any(root==str(repo) and args[:2]==('worktree','prune') for root,args in calls) if q or e else True
            assert batch['outcome']==('incomplete' if e else 'complete'),batch
            assert batch.get('night', {}).get('complete') is (not bool(e)), batch
            assert json.loads(summary.read_text())==batch and 'SIMULATED' in batch['worker_group_stop']
            # Use the actual existing audit writer with the scheduled payload;
            # this does not claim a scheduler-loop or supervision witness.
            for g in orgs:
                item=next(x for x in batch['orgs'] if x['org']==g.org.name)
                state.orgs[g.org.name].db.insert_audit_log('', 'daemon','disk_sweep_summary',ds._audit_payload(batch,item,summary))
                saved=g.db.execute("SELECT task_id,agent,payload FROM audit_log WHERE action='disk_sweep_summary'").fetchall()
                assert len(saved)==1 and saved[0][:2]==('','daemon') and json.loads(saved[0][2])['org']==item
            if before: assert before==candidate_contents(c,*([e] if e else [])) if q or d else not c.exists()
            assert supervision==['reservation']*3+['worker']
            observations.append(dict(row='correction8-residue',kind=kind,result='PASS',events=events,supervision='SIMULATED'))
    return observations


def correction_stop_default():
    observations = []
    for control in (None, '{"stop":false}'):
        with scheduled_fixture() as (f, orgs, state, stop, summary, paths, refs):
            if control is not None: stop.write_text(control)
            report, calls = scheduled_attempt(state, summary)
            assert report['outcome'] == 'complete' and calls == ['reservation'] * 3 + ['worker'], (report, calls)
            assert all(not paths[n].exists() and not paths[n+1].exists() for n in range(0, len(paths), 4))
            assert all(g.git(repo, 'show-ref', '--heads') == branches for g, (repo, branches, _) in zip(orgs, refs))
            assert all(paths[n+2].joinpath('keep').read_text() == 'durable' and paths[n+3].exists() for n in range(0, len(paths), 4))
            observations.append(dict(control=control, outcome=report['outcome'], calls=calls, org_modes=[o['mode'] for o in report['orgs']], supervision='SIMULATED'))
    return dict(attempts=observations, residues=residue_scheduled())


def correction_stop_true():
    from runtime.orchestrator import producer_move
    with scheduled_fixture() as (f, orgs, state, stop, summary, paths, refs):
        stop.write_text('{"stop":true}')
        before = candidate_contents(*paths, *(repo / '.git' for repo, _, _ in refs))
        report, calls = scheduled_attempt(state, summary, forbidden=True)
        assert_suspended(report, 'stop_true', summary)
        assert calls == [] and candidate_contents(*paths, *(repo / '.git' for repo, _, _ in refs)) == before
        # Exercise shipping loop reservation/audit order without changing dates:
        # only the first sleep is passed; the next sleep terminates this fixture.
        sleeps = []
        async def sleep(_):
            sleeps.append(1)
            if len(sleeps) > 1: raise asyncio.CancelledError
        audit_order = []
        audit_type = type(state.orgs['off'].db)
        actual_audit = audit_type.insert_audit_log
        def audit(db, *args):
            if args[2] == 'disk_sweep_summary':
                audit_order.append(next(slug for slug, org in state.orgs.items() if org.db is db))
            return actual_audit(db, *args)
        with patch.object(audit_type, 'insert_audit_log', audit), patch.object(ds.asyncio, 'sleep', sleep), patch.object(producer_move, 'native_writer', side_effect=AssertionError('forbidden reservation')), patch.object(ds, 'run_worker', side_effect=AssertionError('forbidden worker')):
            try: asyncio.run(ds.disk_sweeper_loop(state))
            except asyncio.CancelledError: pass
        assert audit_order == sorted(state.orgs), audit_order
        marker = json.loads(stop.with_name('last-scheduled.json').read_text())
        actual_summary = stop.with_name(marker['scheduled_date'] + '.json')
        actual_report = json.loads(actual_summary.read_text())
        assert_suspended(actual_report, 'stop_true', actual_summary)
        audits = []
        for g in orgs:
            rows = g.db.execute("SELECT task_id,agent,payload FROM audit_log WHERE action='disk_sweep_summary'").fetchall()
            assert len(rows) == 1 and rows[0][:2] == ('', 'daemon'), rows
            payload = json.loads(rows[0][2]); audits.append(payload)
            assert payload == ds._audit_payload(actual_report, None, actual_summary)
        assert candidate_contents(*paths, *(repo / '.git' for repo, _, _ in refs)) == before
        for g, (repo, branches, registration) in zip(orgs, refs):
            assert g.git(repo, 'show-ref', '--heads') == branches and g.git(repo, 'worktree', 'list', '--porcelain') == registration
        # The marker suppresses a repeated occurrence even after control repair.
        stop.write_text('{"stop":false}')
        sleeps.clear()
        with patch.object(ds.asyncio, 'sleep', sleep), patch.object(ds, '_run_scheduled_worker', side_effect=AssertionError('same-night retry')):
            try: asyncio.run(ds.disk_sweeper_loop(state))
            except asyncio.CancelledError: pass
        assert all(g.db.execute("SELECT COUNT(*) FROM audit_log WHERE action='disk_sweep_summary'").fetchone()[0] == 1 for g in orgs)
        return dict(report=report, audit=audits[0], persisted_audits=len(audits), marker=marker, same_night_retry=False, candidate_changes=False)


def publication_refusal(entry, variant='parent_alias', control=None):
    """S3 shipping publication paths; clock/sentinels observe, never decide."""
    from runtime.orchestrator import producer_move
    with scheduled_fixture() as (f, orgs, state, stop, summary, paths, refs), ExitStack() as fixture_stack:
        date = ds.next_night(ds.datetime.now().astimezone()).date().isoformat()
        summary = stop.with_name(date + '.json')
        marker = stop.with_name('last-scheduled.json')
        target = paths[0]
        if control is not None:
            stop.write_text(control)
        if variant == 'parent_alias':
            stop.parent.rmdir()
            stop.parent.symlink_to(target, target_is_directory=True)
        elif variant == 'ancestor_alias':
            stop.parent.rmdir(); stop.parent.parent.rmdir()
            (target / 'disk-sweep').mkdir()
            stop.parent.parent.symlink_to(target, target_is_directory=True)
        elif variant == 'parent_missing':
            stop.parent.rmdir()
        elif variant in ('marker_alias', 'marker_dangling', 'marker_hardlink', 'pending_hardlink', 'pending_alias', 'pending_regular', 'pending_fifo',
                         'summary_alias', 'summary_dangling', 'summary_hardlink', 'summary_pending_alias', 'summary_pending_dangling',
                         'summary_pending_hardlink', 'summary_pending_regular', 'summary_pending_fifo'):
            payload = target / 'publication-target'
            payload.write_text('{"scheduled_date":"2000-01-01"}')
            slot = (summary.with_suffix('.json.pending') if variant.startswith('summary_pending_') else
                    summary if variant.startswith('summary_') else
                    marker.with_suffix('.json.pending') if variant.startswith('pending_') else marker)
            if variant.endswith('_hardlink'): os.link(payload, slot)
            elif variant.endswith('_regular'): slot.write_text('unowned pending evidence')
            elif variant.endswith('_fifo'):
                os.mkfifo(slot)
                # A reader lets the pre-fix writer return for causal inspection;
                # this does not implement publication policy or change the FIFO.
                fifo_reader = os.open(slot, os.O_RDONLY | os.O_NONBLOCK)
                fixture_stack.callback(os.close, fifo_reader)
            else: slot.symlink_to(target / 'missing' if variant.endswith('_dangling') else payload)
        else: raise AssertionError(variant)
        before = candidate_contents(*paths, *(repo / '.git' for repo, _, _ in refs))
        # Also preserve final/pending sidecars when literal directory is trusted.
        sidecars = {str(p): (p.lstat(), os.readlink(p) if p.is_symlink() else p.read_bytes() if p.is_file() else None)
                    for p in (marker, marker.with_suffix('.json.pending'), summary, summary.with_suffix('.json.pending')) if os.path.lexists(p)}
        calls, sleeps = [], []
        actual_writer, actual_worker = producer_move.native_writer, ds.run_worker
        actual_admission, admissions = ds._scheduled_admission, []
        def admission(*a, **kw):
            value = actual_admission(*a, **kw)
            admissions.append(value)
            return value
        async def sleep(_):
            sleeps.append(1)
            if len(sleeps) > 1: raise asyncio.CancelledError
        @contextmanager
        def writer(*a, **kw):
            calls.append('native_writer_enter')
            with actual_writer(*a, **kw) as disposition:
                yield disposition
        async def worker(*a, **kw):
            calls.append('worker_enter')
            return await actual_worker(*a, **kw)
        async def forbidden_spawn(*a, **kw):
            calls.append('worker_spawn')
            raise OSError('forbidden worker spawn sentinel')
        messages = io.StringIO()
        handler = logging.StreamHandler(messages)
        ds.LOG.addHandler(handler)
        error = None
        try:
            with patch.object(ds.asyncio, 'sleep', sleep), patch.object(ds, '_scheduled_admission', admission), patch.object(producer_move, 'native_writer', writer), patch.object(ds, 'run_worker', worker), patch.object(ds.asyncio, 'create_subprocess_exec', forbidden_spawn):
                try:
                    if entry == 'loop': asyncio.run(ds.disk_sweeper_loop(state))
                    elif entry == 'scheduled': asyncio.run(ds._run_scheduled_worker(state, summary, date))
                    elif entry == 'worker': asyncio.run(actual_worker([], summary, dry_run=False, scheduled_date=date))
                    else: raise AssertionError(entry)
                except asyncio.CancelledError:
                    assert entry == 'loop'
                except (OSError, fs.Retain) as exc:
                    error = type(exc).__name__ + ': ' + str(exc)
        finally:
            ds.LOG.removeHandler(handler)
        after = candidate_contents(*paths, *(repo / '.git' for repo, _, _ in refs))
        assert after == before, dict(expected='candidate/sidecar/permission/ref/registration nonmutation',
                                     entry=entry, variant=variant, changed=[p for p in before.keys() | after.keys() if before.get(p) != after.get(p)])
        if variant == 'parent_missing':
            assert not stop.parent.exists(), 'scheduled attempt created missing control parent'
        for path, (observed, content) in sidecars.items():
            p = Path(path)
            assert os.path.lexists(p), dict(expected='unchanged publication sidecar exists', observed='missing', path=path)
            assert fs.signature(p.lstat()) == fs.signature(observed), dict(expected='unchanged publication sidecar', path=path, before=fs.signature(observed), observed=fs.signature(p.lstat()))
            assert (os.readlink(p) if p.is_symlink() else p.read_bytes() if p.is_file() else None) == content
        assert calls == [], dict(expected='zero native reservations/worker effects before publication refusal',
                                 entry=entry, variant=variant, control=control, observed=calls)
        if variant.startswith('summary_') and entry != 'worker':
            assert len(admissions) == 1 and admissions[0]['state'] == 'allowed', admissions
            assert admissions[0]['reason'] == ('stop_false' if control is not None else 'absent'), admissions
            if entry == 'loop':
                assert ds._read_json(marker) == {'scheduled_date': date}
        if not any(path == str(summary) for path in sidecars):
            assert not os.path.lexists(summary), 'refused summary was nevertheless published'
        for g, (repo, branches, registration) in zip(orgs, refs):
            assert g.git(repo, 'show-ref', '--heads') == branches and g.git(repo, 'worktree', 'list', '--porcelain') == registration
            assert g.db.execute("SELECT COUNT(*) FROM audit_log WHERE action='disk_sweep_summary'").fetchone()[0] == 0
        visible = messages.getvalue()
        if entry == 'loop':
            assert 'Disk sweep incomplete; no same-night automatic retry' in visible, visible
        else: assert error, dict(expected='publication visibly refused', observed=visible)
        return dict(entry=entry, variant=variant, control=control, admissions=admissions, calls=calls, candidate_changes=False,
                    refusal=visible if entry == 'loop' else error, persisted_summary_or_audit=False)


def correction_stop_invalid():
    publication = [publication_refusal('loop', variant) for variant in ('parent_alias', 'ancestor_alias', 'parent_missing', 'marker_alias', 'marker_dangling', 'marker_hardlink', 'pending_hardlink', 'pending_alias', 'pending_regular', 'pending_fifo')]
    publication += [publication_refusal(entry) for entry in ('scheduled', 'worker')]
    publication.append(publication_refusal('worker', 'summary_alias'))
    publication += [publication_refusal(entry, variant, control)
                    for entry in ('loop', 'scheduled') for control in (None, '{"stop":false}')
                    for variant in ('summary_alias', 'summary_dangling', 'summary_hardlink', 'summary_pending_alias',
                                    'summary_pending_dangling', 'summary_pending_hardlink', 'summary_pending_regular', 'summary_pending_fifo')]
    variants = [('empty', b''), ('malformed', b'{'), ('oversized', b' ' * 65537), ('utf8', b'\xff'),
                *[(v, v.encode()) for v in ('null', '"x"', '1', '[]', 'true', '{}', '{"stop":null}', '{"stop":1}', '{"stop":"false"}', '{"stop":[]}', '{"stop":false,"extra":0}', '{"stop":false,"stop":false}')],
                *[(v, None) for v in ('unreadable', 'symlink', 'dangling', 'directory', 'fifo', 'hardlink', 'parent_alias', 'changed_read', 'replaced_read', 'read_error', 'short_read', 'parent_missing')]]
    observations = []
    for label, content in variants:
        with scheduled_fixture() as (f, orgs, state, stop, summary, paths, refs), ExitStack() as stack:
            if content is not None: stop.write_bytes(content)
            else: stop.write_text('{"stop":false}')
            if label == 'unreadable': stop.chmod(0)
            elif label in ('symlink', 'dangling'):
                stop.unlink(); stop.symlink_to(f.base / ('missing-target' if label == 'dangling' else 'target'))
                if label == 'symlink': stop.resolve().write_text('{"stop":false}')
            elif label == 'directory': stop.unlink(); stop.mkdir()
            elif label == 'fifo': stop.unlink(); os.mkfifo(stop)
            elif label == 'hardlink': os.link(stop, stop.with_name('alias'))
            elif label == 'parent_alias':
                parent = stop.parent; moved = parent.with_name('real-disk-sweep'); parent.rename(moved); parent.symlink_to(moved, target_is_directory=True)
            elif label == 'parent_missing':
                stop.unlink(); stop.parent.rmdir()
            elif label in ('changed_read', 'replaced_read', 'read_error', 'short_read'):
                real = os.read
                control_identity = fs.identity(stop.stat())
                def read(fd, n):
                    raw = real(fd, n)
                    if fs.identity(os.fstat(fd)) != control_identity: return raw
                    if label == 'changed_read': stop.write_text('{"stop":true }')
                    elif label == 'replaced_read':
                        replacement = stop.with_name('replacement'); replacement.write_text('{"stop":false}'); replacement.replace(stop)
                    elif label == 'read_error': raise OSError('controlled read I/O failure')
                    else: return raw[:-1]
                    return raw
                stack.enter_context(patch.object(ds.os, 'read', read))
            before = candidate_contents(*paths, *(repo / '.git' for repo, _, _ in refs))
            if label in ('parent_alias', 'parent_missing'):
                try: scheduled_attempt(state, summary, forbidden=True)
                except OSError as exc:
                    expected_type = NotADirectoryError if label == 'parent_alias' else FileNotFoundError
                    assert isinstance(exc, expected_type), exc
                else: raise AssertionError('untrusted publication parent accepted')
                assert candidate_contents(*paths, *(repo / '.git' for repo, _, _ in refs)) == before
                observations.append(dict(control=label, publication='refused without persisted summary; real loop covered above'))
                continue
            report, calls = scheduled_attempt(state, summary, forbidden=True)
            stack.close()
            assert_suspended(report, 'invalid_control', summary)
            error = report['scheduled_admission'].get('error', '')
            expected = ('PermissionError' if label == 'unreadable' else
                        'stop_not_regular_or_hardlinked' if label in ('symlink', 'dangling', 'directory', 'fifo', 'hardlink') else
                        'NotADirectoryError' if label == 'parent_alias' else
                        'FileNotFoundError' if label == 'parent_missing' else
                        'stop_changed_during_read' if label in ('changed_read', 'replaced_read') else
                        'controlled read I/O failure' if label == 'read_error' else
                        'stop_oversized_or_short_read' if label in ('short_read', 'oversized') else
                        'UnicodeDecodeError' if label == 'utf8' else
                        'JSONDecodeError' if label in ('empty', 'malformed') else 'stop_requires_only_boolean_stop')
            assert expected in error, dict(control=label, expected=expected, observed=error)
            assert calls == [] and candidate_contents(*paths, *(repo / '.git' for repo, _, _ in refs)) == before
            for g, (repo, branches, registration) in zip(orgs, refs):
                assert g.git(repo, 'show-ref', '--heads') == branches and g.git(repo, 'worktree', 'list', '--porcelain') == registration
            observations.append(dict(control=label, admission=report['scheduled_admission'], evidence='test-side controlled fault' if label in ('changed_read', 'replaced_read', 'read_error', 'short_read') else 'real filesystem/JSON'))
    # Publication failure does not enter reservations; the error is observable.
    with scheduled_fixture() as (f, orgs, state, stop, summary, paths, refs):
        stop.write_text('{"stop":true}'); before = candidate_contents(*paths, *(repo / '.git' for repo, _, _ in refs))
        with patch.object(ds, 'atomic_json', side_effect=OSError('controlled summary publication failure')):
            try: scheduled_attempt(state, summary, forbidden=True)
            except OSError as exc: assert 'summary publication failure' in str(exc)
            else: raise AssertionError('publication error swallowed')
        assert candidate_contents(*paths, *(repo / '.git' for repo, _, _ in refs)) == before
    return dict(variants=observations, publication=publication, publication_failure='visible; zero reservation/worker')


def correction_stop_recovery():
    observations = []
    for initial, repaired in (('{"stop":true}', '{"stop":false}'), ('{"stop":true}', None), ('{', '{"stop":false}')):
        with scheduled_fixture() as (f, orgs, state, stop, summary, paths, refs):
            stop.write_text(initial)
            first, calls = scheduled_attempt(state, summary, forbidden=True)
            assert_suspended(first, 'stop_true' if 'true' in initial else 'invalid_control', summary)
            first_bytes = summary.read_bytes()
            if repaired is None: stop.unlink()
            else: stop.write_text(repaired)
            second, calls = scheduled_attempt(state, summary.with_name('next-attempt.json'))
            assert second['outcome'] == 'complete' and calls == ['reservation'] * 3 + ['worker'], second
            assert all(not paths[n].exists() and not paths[n+1].exists() for n in range(0, len(paths), 4))
            assert summary.read_bytes() == first_bytes
            observations.append(dict(initial=initial, repaired=repaired, first=first['outcome'], second=second['outcome']))
    return dict(attempts=observations, boundary='separate attempted admissions, no schedule/date manipulation or in-flight cancellation')


def correction_stop_preview():
    observations = []
    for control in ('{"stop":true}', '{', None):
        for config in ('disk_sweep: {mode: "off"}', None):
            with fixture() as f:
                policy_config(f, config)
                stop = f.base / 'daemon/disk-sweep/stop.json'
                if control is not None: stop.write_text(control)
                p = f.scratch('TASK-1'); repo, q = f.worktree('TASK-2')
                sidecars = f.workspace / '.happyranch/task-scratch-manifests'
                before = candidate_contents(p, q, sidecars)
                refs = f.git(repo, 'show-ref', '--heads'); registrations = f.git(repo, 'worktree', 'list', '--porcelain')
                command = [sys.executable, '-m', 'runtime.daemon.disk_sweeper', '--dry-run', '--runtime-root', str(f.base / 'runtime')]
                result = subprocess.run(command, capture_output=True, text=True, timeout=12)
                assert result.returncode == 0, result.stderr
                report = json.loads(result.stdout)['report']
                assert report['outcome'] == 'complete' and report['orgs'][0]['mode'] == 'dry_run', report
                assert all(c['state'] == 'retained' for c in report['orgs'][0]['candidates']), report
                assert candidate_contents(p, q, sidecars) == before
                assert f.git(repo, 'show-ref', '--heads') == refs and f.git(repo, 'worktree', 'list', '--porcelain') == registrations
                observations.append(dict(control=control, config=config, exit=result.returncode, mode='dry_run', candidate_changes=False))
    return dict(variants=observations, clock='real subprocess clock retains fresh candidates; C5 owns eligible preview sentinels')


def correction_per_org_settlement():
    """One org's incomplete/failed/interrupted sweep never leaves a finished peer unknown."""
    def item(org, **kw):
        value = dict(org=org, mode='on', outcome='complete', candidates=[])
        value.update(kw)
        return value
    stop = 'owned group signalled SIGKILL when present'
    clean, partial = item('a'), item('b', outcome='error', candidates=[dict(state='partial_or_unknown')])
    table = [
        # (report, slug, expected)
        (dict(orgs=[clean, partial], worker_group_stop=stop), 'a', True),
        (dict(orgs=[clean, partial], worker_group_stop=stop), 'b', False),
        (dict(orgs=[clean, item('b', outcome='incomplete', discovery='partial')], worker_group_stop=stop), 'b', True),
        (dict(orgs=[clean, item('b', outcome='incomplete', error='total sweep deadline')], worker_group_stop=stop), 'b', False),
        (dict(orgs=[clean, item('b', outcome='incomplete', candidates=[dict(state='inspecting')])], worker_group_stop=stop), 'b', False),
        # A killed worker: earlier orgs provably returned, the last one did not.
        (dict(orgs=[clean, item('b')], worker_group_stop=stop, interruption='deadline'), 'a', True),
        (dict(orgs=[clean, item('b')], worker_group_stop=stop, interruption='deadline'), 'b', False),
        (dict(orgs=[clean], worker_group_stop=stop, interruption='worker_exit:1'), 'a', False),
        (dict(orgs=[item('a', mode='dry_run')], worker_group_stop=stop), 'a', False),
        (dict(orgs=[clean, dict(clean)], worker_group_stop=stop), 'a', False),
        (dict(orgs=[clean]), 'a', False),
        (dict(orgs=[clean], worker_group_stop=stop), 'missing', False),
    ]
    for report, slug, expected in table:
        observed = ds._org_effects_known(report, slug)
        assert observed is expected, dict(report=report, slug=slug, expected=expected, observed=observed)
    # Real worker process: an orderly incomplete sweep exits WORKER_INCOMPLETE_EXIT
    # and the supervisor records it without an interruption.
    with tempfile.TemporaryDirectory() as base:
        missing = Path(base) / 'missing-org'
        summary = Path(base) / 'summary.json'
        report = asyncio.run(ds.run_worker([str(missing)], summary, dry_run=False, budget=30))
        assert report['worker_returncode'] == ds.WORKER_INCOMPLETE_EXIT, report
        assert 'interruption' not in report and 'partial_mutation' not in report, report
        # Missing root: partial discovery, nothing touched, so its effect is known.
        assert report['outcome'] == 'incomplete' and report['orgs'][0]['discovery'] == 'partial', report
        assert ds._org_effects_known(report, 'missing-org') is True, report
    return dict(table=len(table), worker_exit=report['worker_returncode'], limits='uncaught worker exception path (exit 1) not exercised; C12 owns deadline/shutdown supervision')


def measurement_legacy(value):
    """Remove only the newly documented keys, preserving every legacy byte."""
    additions = {'measurement', 'measurements', 'measurement_history', 'stop_cause', 'source_revision', 'coverage', 'scope_classifications', 'exclusions', 'deferred_arrivals', 'inventory_window', 'interrupted_removals', 'previous_interrupted_removals', 'night', 'policy', 'elapsed_seconds', 'unloaded_orgs_unknown', 'worker_group_stop', 'worker_returncode'}
    if isinstance(value, dict):
        return {key: measurement_legacy(item) for key, item in value.items() if key not in additions}
    if isinstance(value, list):
        return [measurement_legacy(item) for item in value]
    return value


def c24():
    """Compare actual pinned pre-instrumentation code on the SAME fixture."""
    from datetime import datetime, timezone
    class FixedDate(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 11, 13, 0, 0, tzinfo=timezone.utc)

    baseline = '76fd6d6076370471a3634ca94ef15b3effc11232'
    def source(path):
        return subprocess.run(['git', 'show', f'{baseline}:{path}'], check=True, capture_output=True).stdout
    old_fs = ModuleType('baseline_disk_sweep_fs')
    exec(compile(source('runtime/daemon/_disk_sweep_fs.py'), '<baseline fs>', 'exec'), old_fs.__dict__)
    old = ModuleType('baseline_disk_sweeper')
    with patch.dict(sys.modules, {'runtime.daemon._disk_sweep_fs': old_fs}):
        exec(compile(source('runtime/daemon/disk_sweeper.py'), '<baseline sweeper>', 'exec'), old.__dict__)
    with fixture() as f:
        p = f.scratch('TASK-1')
        repo, q = f.worktree('TASK-2')
        (q / 'source').write_text('dirty\n')
        before = candidate_contents(p, q)
        refs = f.git(repo, 'show-ref', '--heads')
        summary = f.base / 'daemon/disk-sweep/2026-11-13.json'
        deadline = time.monotonic() + 30
        common = dict(dry_run=True, scheduled_date='2026-11-13', revision='synthetic-code-binding')
        # Fix presentation inputs only; Git, filesystem gates and DB are real.
        real_publication, publications = ds.atomic_json, []
        def publication(path, report):
            observer = fs._observer.get()
            publications.append(observer.clone if observer is not None else False)
            return real_publication(path, report)
        with patch.object(ds, 'datetime', FixedDate), patch.object(old, 'datetime', FixedDate), \
                patch.object(os, 'statvfs', return_value=SimpleNamespace(f_bavail=50, f_blocks=100)):
            legacy = old.sweep([str(f.org)], deadline, summary, **common)
            with patch.object(ds, 'atomic_json', publication):
                measured = ds.sweep([str(f.org)], deadline, summary, **common)
            failed = None
            with patch.object(ds._Measurements, 'record', side_effect=ValueError('controlled measurement failure')):
                failed = ds.sweep([str(f.org)], deadline, summary, **common)
        encode = lambda value: json.dumps(value, sort_keys=True, separators=(',', ':')).encode()
        # Step 3 explicitly puts a recent-root keep before dirty status. The
        # same real-clock root is recent AND dirty: assert the exact permitted
        # delta, then compare every remaining legacy report/audit byte.
        assert row(legacy['orgs'][0], q)['reason'] == 'dirty_or_untracked'
        assert row(legacy['orgs'][0], q)['dirty_paths'] == [' M source', '']
        assert legacy['orgs'][0]['retained_reasons'] == {'changed_within_hour': 1, 'dirty_or_untracked': 1}
        expected = json.loads(encode(legacy))
        row(expected['orgs'][0], q)['reason'] = 'changed_within_hour'
        del row(expected['orgs'][0], q)['dirty_paths']
        expected['orgs'][0]['retained_reasons'] = {'changed_within_hour': 2}
        assert encode(expected) == encode(measurement_legacy(measured)), (expected, measured)
        assert encode(expected) == encode(measurement_legacy(failed)), (expected, failed)
        for report in (measured, failed):
            assert encode(old._audit_payload(expected, expected['orgs'][0], summary)) == encode(measurement_legacy(ds._audit_payload(report, report['orgs'][0], summary)))
        assert failed['orgs'][0]['measurements']['errors']['count'] > 0, failed
        assert candidate_contents(p, q) == before and f.git(repo, 'show-ref', '--heads') == refs
        assert measured['revision'] == 'synthetic-code-binding'
        assert measured['source_revision'] == measured['revision']
        assert measured['stop_cause'] == 'finished'
        writes = measured['orgs'][0]['measurements']
        assert writes['counters']['report_writes'] == sum(value is not False for value in publications), (writes, publications)
        assert writes['clones'][str(repo)]['report_writes'] == publications.count(str(repo)), (writes, publications)
        assert writes['counters']['report_writes_confirmed'] == writes['counters']['report_writes'] - 1
        assert json.loads(summary.read_text()) == failed
        # Existing eligible-preview fixture, using its established advanced
        # wall clock, also preserves every legacy org byte (including size).
        legacy_preview = old.sweep_org(f.org, deadline, mode='dry_run', now=NOW)
        measured_preview = ds.sweep_org(f.org, deadline, mode='dry_run', now=NOW)
        assert row(legacy_preview, p)['state'] == 'eligible'
        assert encode(legacy_preview) == encode(measurement_legacy(measured_preview))
        # Production-shaped output: real measured dirty row and real org/counter
        # shape, 1,600 distinct paths of normal production length, 12 clones.
        prototype = json.loads(json.dumps(measured))
        org = prototype['orgs'][0]
        dirty = row(org, q)
        org['candidates'] = [dict(dirty, path=f'/home/fixture/.happyranch/runtime/orgs/happyranch/workspaces/dev_agent/repos/happyranch/.claude/worktrees/TASK-{n:06d}') for n in range(1600)]
        original = org['measurements']['clones'][str(repo)]
        org['measurements']['clones'] = {f'/home/fixture/workspaces/maker/repos/project-{n}': original for n in range(12)}
        ds._summarize_org(org)
        size = len(json.dumps(prototype, sort_keys=True, indent=2).encode())
        audit_size = len(json.dumps(ds._audit_payload(prototype, org, summary)).encode())
        assert size < 4 * 1024 * 1024 and audit_size < 2 * 1024 * 1024, (size, audit_size)
        # A caught configuration error ends the traversal without changing its
        # legacy outcome; the additive cause must still describe that error.
        (f.org / 'org/config.yaml').write_text('[')
        with patch.object(ds, 'datetime', FixedDate), patch.object(old, 'datetime', FixedDate), \
                patch.object(os, 'statvfs', return_value=SimpleNamespace(f_bavail=50, f_blocks=100)):
            legacy_error = old.sweep([str(f.org)], deadline, summary, **common)
            measured_error = ds.sweep([str(f.org)], deadline, summary, **common)
        assert measured_error['stop_cause'] == 'error', measured_error
        assert measured_error['orgs'][0]['stop_cause'] == 'error', measured_error
        assert encode(legacy_error) == encode(measurement_legacy(measured_error))
        return dict(base=baseline, legacy_report_and_audit='byte-identical after removing additive keys', failure_errors=failed['orgs'][0]['measurements']['errors']['count'], candidates=1600, clones=12, date_report_bytes=size, audit_payload_bytes=audit_size)


def c25():
    """Independent known counts, real effects, already-walked size and history."""
    observations = []
    for dirty in (True, False):
        with fixture() as f:
            repo, p = f.worktree('TASK-1')
            if dirty:
                (p / 'source').write_text('dirty\n')
            branch = f.git(repo, 'rev-parse', 'refs/heads/task/TASK-1')
            real = ds.registrations
            calls = []
            def topology(root, deadline, **kwargs):
                calls.append(str(root))
                return real(root, deadline, **kwargs)
            # Walk oracle consumes only the test-side returned snapshots; no
            # mock implements measurement or the sweep decision.
            snapshots = []
            real_snapshot = ds.snapshot
            def snapshot(path, deadline, **kwargs):
                tree = real_snapshot(path, deadline, **kwargs)
                snapshots.append(len(tree))
                return tree
            with patch.object(ds, 'registrations', topology), patch.object(ds, 'snapshot', snapshot):
                started = time.perf_counter()
                report = f.sweep()
                elapsed = time.perf_counter() - started
            candidate = row(report, p)
            assert 'measurement' in candidate, 'per-item measurement missing from actual sweep report'
            metrics = candidate['measurement']
            counters = report['measurements']['clones'][str(repo)]
            assert report['measurements']['errors']['count'] == 0, report
            assert len(calls) == counters['topology_reads'] == (1 if dirty else 9), (calls, counters)
            assert counters['censuses'] == (3 if dirty else 27), counters
            assert counters['ownership_reads'] == (0 if dirty else 7), counters
            assert counters['entries_walked'] == sum(snapshots), (counters, snapshots)
            assert counters['git']['worktree list']['calls'] == (1 if dirty else 9), counters
            assert counters['git']['status']['calls'] == (1 if dirty else 7), counters
            assert counters['git'].get('rev-parse', {}).get('calls', 0) == (0 if dirty else 13), counters
            assert ('rev-list' not in counters['git']) if dirty else counters['git']['rev-list']['calls'] == 6
            assert counters['gitdir_gone_count'] == 0, counters
            for value in counters['git'].values():
                assert 0 < value['duration_seconds'] <= elapsed and value['timeouts'] == 0, value
            assert 0 < metrics['duration_seconds'] <= elapsed, metrics
            assert 0 < report['measurements']['duration_seconds'] <= elapsed, report
            assert sum(value['duration_seconds'] for value in counters['git'].values()) <= elapsed, counters
            if dirty:
                assert metrics['stage_reached'] == 'git:status', metrics
                assert candidate['reason'] == metrics['final_reason'] == 'dirty_or_untracked'
                assert metrics['attempted_effect'] == metrics['confirmed_effect'] == {}
                assert metrics['kept_allocated_bytes'] == 'unknown' and metrics['kept_since'], metrics
            else:
                assert metrics['stage_reached'] == 'removal_confirmed', metrics
                assert 'worktree prune' not in counters['git'], counters
                assert candidate['state'] == metrics['final_reason'] == 'removed' and not p.exists(), candidate
                assert metrics['attempted_effect'] == metrics['confirmed_effect'] == {'remove': 1}, metrics
                assert isinstance(metrics['kept_allocated_bytes'], int), metrics
                assert f.git(repo, 'rev-parse', 'refs/heads/task/TASK-1') == branch
            observations.append(dict(dirty=dirty, counters=counters, item=metrics))
    with fixture() as f:
        p = f.scratch('TASK-1')
        # First existing snapshot completes before the recent-age gate refuses.
        os.utime(p / 'nested/payload', (NOW, NOW))
        oracle = sum({(s.st_dev, s.st_ino): s.st_blocks * 512 for x in (p, *p.rglob('*')) for s in (x.lstat(),)}.values())
        report = f.sweep()
        assert row(report, p)['measurement']['kept_allocated_bytes'] == oracle
    with fixture() as f:
        repo, p = f.worktree('TASK-1')
        entry = repo / '.git/worktrees/residue'
        entry.mkdir()  # A real, safe, empty unknown-location gitdir-gone entry.
        (p / 'source').write_text('dirty\n')
        report = f.sweep()
        assert report['measurements']['clones'][str(repo)]['gitdir_gone_count'] == 1, report
        assert report['discovery'] == 'partial' and report['outcome'] == 'incomplete'
    with fixture() as f:
        p = f.scratch('TASK-1')
        with patch.object(ds._Measurements, 'record', side_effect=ValueError('controlled measurement failure')):
            report = f.sweep()
        assert row(report, p)['state'] == 'removed' and not p.exists(), report
        assert report['measurements']['errors']['count'] > 0, report
    with fixture() as f:
        p = f.scratch('TASK-1', status='in_progress')
        directory = f.base / 'daemon/disk-sweep'
        history = dict(orgs=[dict(candidates=[dict(path=str(p), state='retained')])])
        ds.atomic_json(directory / '2026-11-10.json', history)
        ds.atomic_json(directory / '2026-11-11.json', history)
        real_history, calls = ds._kept_history, []
        def counted(*args):
            calls.append(args)
            return real_history(*args)
        summary = directory / '2026-11-13.json'
        with patch.object(ds, '_kept_history', counted):
            report = ds.sweep([str(f.org)], time.monotonic() + 12, summary, dry_run=True, scheduled_date='2026-11-13', revision='synthetic')
        assert len(calls) == 1, calls
        assert row(report['orgs'][0], p)['measurement']['kept_since'] == '2026-11-10', report
        # A report alias cannot be followed; the measurement failure is visible
        # and does not turn an active owner into deletion authority.
        (directory / '2026-11-11.json').unlink()
        (directory / '2026-11-11.json').symlink_to(directory / '2026-11-10.json')
        report = ds.sweep([str(f.org)], time.monotonic() + 12, summary, dry_run=True, scheduled_date='2026-11-13', revision='synthetic')
        assert report['measurement_history']['status'].startswith('unavailable:'), report
        retained(report['orgs'][0], p, 'task_or_root_not_terminal')
    return dict(counts=observations, kept_size='existing complete snapshot only', residue=1, history='one pass, earliest observed date, no-follow refusal', failure='real scratch removal still succeeds')


def c26():
    """Ordinary history byte exhaustion preserves validated retained dates."""
    from datetime import date, timedelta
    observations = []
    with fixture() as f:
        repo, p = f.worktree('TASK-1')
        (p / 'source').write_text('dirty\n')
        summary = f.base / 'daemon/disk-sweep/2026-11-14.json'
        common = dict(dry_run=True, scheduled_date='2026-11-14', revision='synthetic')
        prototype = ds.sweep([str(f.org)], time.monotonic() + 12, summary, **common)
        org = prototype['orgs'][0]
        dirty = row(org, p)
        dirty['measurement']['kept_since'] = '2026-10-13'
        org['candidates'] = [dict(dirty, path=str(p) if n == 0 else f'/home/fixture/.happyranch/runtime/orgs/happyranch/workspaces/dev_agent/repos/happyranch/.claude/worktrees/TASK-{n:06d}') for n in range(1600)]
        original = org['measurements']['clones'][str(repo)]
        org['measurements']['clones'] = {f'/home/fixture/workspaces/maker/repos/project-{n}': original for n in range(12)}
        ds._summarize_org(org)
        raw = json.dumps(prototype, sort_keys=True, indent=2).encode()
        # The review's ordinary 1.96 MB/report volume, not an individually
        # oversized report. JSON permits trailing whitespace for exact sizing.
        report_bytes = max(1_962_316, len(raw))
        assert report_bytes <= 8 * 1024 * 1024, report_bytes
        raw += b' ' * (report_bytes - len(raw))
        paths = [summary.parent / ((date(2026, 10, 13) + timedelta(days=n)).isoformat() + '.json') for n in range(32)]
        for path in paths:
            path.write_bytes(raw)
        assert 32 * report_bytes > 32 * 1024 * 1024
        report = ds.sweep([str(f.org)], time.monotonic() + 12, summary, **common)
        kept = row(report['orgs'][0], p)
        assert kept['measurement']['kept_since'] == '2026-10-13', (kept['measurement'], report['measurement_history'])
        history = report['measurement_history']
        assert history['status'] == 'bounded_window', history
        window_reports = (32 * 1024 * 1024) // report_bytes
        assert 1 < window_reports < 32
        assert history['reports'] == window_reports and history['bytes_read'] == window_reports * report_bytes, history
        assert json.loads(summary.read_text())['measurement_history'] == history
        assert ds._audit_payload(report, report['orgs'][0], summary)['measurement_history'] == history
        retained(report['orgs'][0], p, 'changed_within_hour')
        observations.append(dict(input='32 valid production-sized reports', bytes_per_report=report_bytes, total_bytes=32 * report_bytes, history=history, kept_since=kept['measurement']['kept_since']))
        # A malformed report inside the readable window still fails visibly,
        # even after a newer valid report has contributed a date.
        paths[-2].write_text('{')
        report = ds.sweep([str(f.org)], time.monotonic() + 12, summary, **common)
        history = report['measurement_history']
        assert history['status'] == 'unavailable:JSONDecodeError' and history['reports'] == 1, history
        assert row(report['orgs'][0], p)['measurement']['kept_since'] == '2026-11-14', history
        retained(report['orgs'][0], p, 'changed_within_hour')
        observations.append(dict(input='malformed second report', history=history))
        paths[-2].write_bytes(raw)
        # The first file outside the remaining aggregate allowance is also
        # checked against the independent single-report limit before stopping.
        with paths[-(window_reports + 1)].open('wb') as stream:
            stream.truncate(8 * 1024 * 1024 + 1)
        report = ds.sweep([str(f.org)], time.monotonic() + 12, summary, **common)
        history = report['measurement_history']
        assert history['status'] == 'unavailable:Retain' and history['reports'] == window_reports, history
        assert row(report['orgs'][0], p)['measurement']['kept_since'] == '2026-11-14', history
        retained(report['orgs'][0], p, 'changed_within_hour')
        observations.append(dict(input='oversized boundary report', history=history))
    return observations


def c27():
    """Coverage distinguishes located keeps/exclusions from unknown discovery."""
    observations = []
    failures = []
    for kind in ('candidate_missing', 'candidate_cycle', 'candidate_malformed', 'job_missing', 'job_cycle', 'job_malformed', 'dirty', 'metadata', 'unknown_residue', 'unregistered', 'non_clone', 'configured_missing_git', 'aliased_git', 'unreadable_git', 'unknown_owner', 'shared', 'unknown_job'):
        try:
            with fixture() as f:
                repo, p = f.worktree('TASK-1')
                _, peer = f.worktree('TASK-2')
                if kind.startswith(('candidate_', 'job_')):
                    owner = 'TASK-1' if kind.startswith('candidate_') else 'TASK-99'
                    if kind.startswith('job_'):
                        f.task(owner)
                        f.db.execute("INSERT INTO jobs VALUES(?,'running')", (owner,))
                    if kind.endswith('_missing'): f.db.execute('DELETE FROM tasks WHERE id=?', (owner,))
                    else: f.db.execute('UPDATE tasks SET parent_task_id=? WHERE id=?', (owner if kind.endswith('_cycle') else '', owner))
                    f.db.commit()
                elif kind == 'dirty': (p / 'source').write_text('dirty\n')
                elif kind == 'metadata': (repo / '.git/worktrees/TASK-1/index').chmod(0)
                elif kind == 'unknown_residue': residue_input(f, repo, p, empty=True)
                elif kind == 'unregistered':
                    p = p.with_name('TASK-3'); p.mkdir()
                elif kind in ('non_clone', 'configured_missing_git'):
                    agents = f.org / 'org/agents'; agents.mkdir()
                    (agents / 'maker.md').write_text('---\nname: maker\nteam: engineering\nrole: worker\nexecutor: codex\nrepos: {project: "https://example.invalid/project"}\n---\nFixture agent.\n')
                    extra = f.workspace / 'repos' / ('output' if kind == 'non_clone' else 'project')
                    if kind == 'non_clone': extra.mkdir()
                    else: (repo / '.git').rename(repo / 'held-git')
                elif kind == 'aliased_git':
                    held = f.base / 'held-git'; (repo / '.git').rename(held); (repo / '.git').symlink_to(held)
                elif kind == 'unreadable_git': (repo / '.git').chmod(0)
                elif kind == 'unknown_owner': (f.workspace / 'repos/output').mkdir()
                elif kind == 'shared': (repo / '.git/worktrees/TASK-1/gitdir').write_text(str(peer / '.git') + '\n')
                elif kind == 'unknown_job':
                    f.db.execute("INSERT INTO jobs VALUES('TASK-1','mystery')"); f.db.commit()
                calls = []; realgit = ds.git
                def git(root, deadline, *args):
                    calls.append(args); return realgit(root, deadline, *args)
                with patch.object(ds, 'git', git): report = f.sweep()
                if kind.startswith('candidate_'):
                    retained(report, p, 'unknown_conflicting_lineage' if kind.endswith('_malformed') else 'unknown_missing_or_cyclic_lineage')
                    assert not peer.exists() and row(report, peer)['state'] == 'removed', (kind, report)
                    assert not report.get('org_hold'), (kind, report)
                elif kind.startswith('job_'):
                    assert p.exists() and peer.exists() and report.get('org_hold'), (kind, report)
                    assert all(c['state'] == 'retained' for c in report['candidates']), (kind, report)
                complete = kind in ('dirty', 'metadata', 'non_clone')
                assert report.get('coverage', {}).get('complete') is complete, (kind, report)
                assert report['outcome'] == ('complete' if complete else 'incomplete'), (kind, report)
                assert not any(a[:2] == ('worktree', 'prune') for a in calls), calls
                if kind == 'non_clone':
                    assert report['exclusions'] == [dict(path=str(extra), reason='ordinary_non_clone')]
                if kind == 'unknown_residue':
                    assert report['unreached'] is None and report['coverage']['unknown_discovery']
                    assert row(report, peer)['state'] == 'removed'
                if kind in ('shared', 'unknown_job'): assert peer.exists()
                if complete: assert row(report, peer)['state'] == 'removed'
                observations.append(dict(kind=kind, coverage=report['coverage']))
        except AssertionError as exc:
            failures.append(dict(kind=kind, error=str(exc)))
    for failure in failures: print(json.dumps(dict(regression=failure)), flush=True)
    assert not failures, failures
    return observations


def c28():
    """A uniquely located absent folder is retained, never repaired; peers progress."""
    observations = []
    for kind in ('missing', 'alias_parent', 'denied_parent', 'conflict', 'reappeared', 'repointed', 'descendant'):
        with fixture() as f:
            repo, p = f.worktree('TASK-1')
            _, peer = f.worktree('TASK-2')
            if kind == 'descendant':
                p = peer / 'ignored/missing'; p.parent.mkdir(parents=True)
                f.git(repo, 'worktree', 'add', '--detach', str(p), 'HEAD')
                entry = repo / '.git/worktrees/missing'
            else: entry = repo / '.git/worktrees/TASK-1'
            shutil.rmtree(p)
            before = candidate_contents(entry); refs = f.git(repo, 'show-ref')
            if kind == 'alias_parent':
                original = p.parent; held = f.base / 'held-prefix'
                original.rename(held); original.symlink_to(held)
            if kind == 'denied_parent': p.parent.chmod(0)
            if kind == 'conflict': (repo / '.git/worktrees/TASK-2/gitdir').write_text(str(p / '.git') + '\n')
            realmeta = ds._registration_metadata; events = []
            def metadata(root, deadline, **kwargs):
                value = realmeta(root, deadline, **kwargs)
                if kind in ('reappeared', 'repointed') and root == repo and not events:
                    if kind == 'reappeared': p.mkdir()
                    else: (entry / 'gitdir').write_text(str(peer / '.git') + '\n')
                    events.append(kind)
                return value
            with patch.object(ds, '_registration_metadata', metadata): report = f.sweep()
            if kind != 'repointed': assert before == candidate_contents(entry)
            assert refs == f.git(repo, 'show-ref')
            if kind == 'missing':
                assert any(c['path'] == str(peer) and c['state'] == 'removed' for c in report['candidates']), report
                missing = row(report, p)
                assert missing['state'] == 'retained' and missing['reason'].startswith('missing_worktree_folder:'), report
                assert not p.exists() and report['coverage']['complete'], report
            elif kind == 'descendant':
                retained(report, peer, 'linked_descendant:' + str(p))
                assert not p.exists()
            else:
                assert not any(c['state'] == 'removed' for c in report['candidates']), report
                assert report['outcome'] == 'incomplete'
            observations.append(dict(kind=kind, report=report))
    return observations


def scheduled_closure(kind):
    """Interrupt the shipping scheduler between actual worker/parent publications."""
    from datetime import datetime, timedelta, timezone
    class Before(datetime):
        @classmethod
        def now(cls, tz=None): return cls(2026, 11, 13, 12, tzinfo=timezone.utc).astimezone(tz)
    class After(datetime):
        @classmethod
        def now(cls, tz=None): return (Before.now(tz) + timedelta(days=1))
    class SupervisorLost(BaseException): pass
    with scheduled_fixture() as (f, orgs, state, stop, _, paths, refs):
        for g in orgs: g.db.row_factory = sqlite3.Row
        date = ds.next_night(Before.now()).date().isoformat()
        summary = stop.with_name(date + '.json')
        sleeps, boundary = [], []
        async def sleep_once(_):
            sleeps.append(1)
            if len(sleeps) > 1: raise asyncio.CancelledError
        realread = ds._read_json
        def read(path):
            report = realread(path)
            if kind == 'worker_only' and path == summary and report.get('finished_at'):
                assert 'worker_group_stop' not in report and 'worker_returncode' not in report
                boundary.append('worker final published; parent final not published')
                raise SupervisorLost
            return report
        audit_type = type(state.orgs['off'].db)
        realaudit = audit_type.insert_audit_log
        def audit(db, *args):
            if kind == 'parent_only' and args[2] == 'disk_sweep_summary':
                boundary.append('parent final published; summary audits not published')
                raise SupervisorLost
            return realaudit(db, *args)
        with patch.object(ds, 'datetime', Before), patch.object(ds.asyncio, 'sleep', sleep_once), patch.object(ds, '_read_json', read), patch.object(audit_type, 'insert_audit_log', audit):
            try: asyncio.run(ds.disk_sweeper_loop(state))
            except (SupervisorLost, asyncio.CancelledError): pass
        saved = json.loads(summary.read_text())
        assert saved['night']['complete'] is (kind != 'worker_only'), (kind, saved)
        if kind == 'worker_only':
            assert boundary == ['worker final published; parent final not published'], boundary
            assert 'supervisor_closure_unproven' in saved['night']['reasons'], saved
            assert 'worker_group_stop' not in saved and 'worker_returncode' not in saved
        else:
            assert saved['worker_group_stop'] and saved['worker_returncode'] == 0, saved
        for g in orgs:
            rows = g.db.execute("SELECT payload FROM audit_log WHERE action='disk_sweep_summary'").fetchall()
            assert len(rows) == (1 if kind == 'complete' else 0), (kind, rows)
            if rows:
                item = next(o for o in saved['orgs'] if o['org'] == g.org.name)
                assert json.loads(rows[0][0]) == ds._audit_payload(saved, item, summary)
        async def stop_at_sleep(_): raise asyncio.CancelledError
        for _ in range(2):
            with patch.object(ds, 'datetime', After), patch.object(ds.asyncio, 'sleep', stop_at_sleep), patch.object(ds, '_run_scheduled_worker', side_effect=AssertionError('no retry/catch-up')):
                try: asyncio.run(ds.disk_sweeper_loop(state))
                except asyncio.CancelledError: pass
        notices = []
        for g in orgs:
            rows = g.db.execute("SELECT task_id,agent,payload FROM audit_log WHERE action='disk_sweep_night_notice'").fetchall()
            selected = [r for r in rows if json.loads(r[2])['scheduled_date'] == date]
            assert len(selected) == (0 if kind == 'complete' else 1), (kind, selected)
            if selected:
                assert selected[0][:2] == ('', 'daemon')
                payload = json.loads(selected[0][2]); notices.append(payload)
                assert payload['status'] == 'incomplete' and payload['summary_path'] == str(summary)
                reason = 'supervisor_closure_unproven' if kind == 'worker_only' else 'summary_audit_missing_or_mismatched:'
                assert any(r.startswith(reason) for r in payload['reasons']), payload
        assert json.loads(stop.with_name('last-scheduled.json').read_text())['scheduled_date'] == date
        return dict(kind=kind, boundary=boundary, night=saved['night'], notices=notices, supervision='actual child, stop/reap and scheduled JSON/audit path')


def c29():
    """Actual scheduler startup and pre-night checks write one durable org record."""
    from datetime import datetime, timezone
    observations, failures = [], []
    for kind in ('worker_only', 'parent_only', 'complete'):
        try: observations.append(scheduled_closure(kind))
        except AssertionError as exc: failures.append(dict(kind=kind, error=str(exc)))
    for failure in failures: print(json.dumps(dict(regression=failure)), flush=True)
    assert not failures, failures
    class FixedDate(datetime):
        @classmethod
        def now(cls, tz=None): return cls(2026, 11, 14, 12, tzinfo=timezone.utc).astimezone(tz)
    for kind in ('missing', 'incomplete', 'complete', 'suspended', 'malformed'):
        with scheduled_fixture() as (f, orgs, state, stop, summary, paths, refs):
            for g in orgs: g.db.row_factory = sqlite3.Row
            # At startup the last expected night is the current local date.
            expected = FixedDate.now().astimezone().date().isoformat()
            source = stop.with_name(expected + '.json')
            if kind == 'malformed':
                source.write_text('{')
            elif kind != 'missing':
                if kind == 'suspended':
                    stop.write_text('{"stop":true}')
                    report, _ = scheduled_attempt(state, source, forbidden=True)
                else: report, _ = scheduled_attempt(state, source)
                report['scheduled_date'] = expected
                if kind == 'complete': assert report['night']['complete'] is True
                if kind == 'incomplete': report['unreached_orgs'] = ['/fixture/unreached']
                ds.atomic_json(source, report)
                if kind == 'complete':
                    for slug, org in state.orgs_snapshot().items():
                        item = next(o for o in report['orgs'] if o['org'] == slug)
                        org.db.insert_audit_log('', 'daemon', 'disk_sweep_summary', ds._audit_payload(report, item, source))
            preserved = [path for path in paths if path.exists()]
            snapshot = candidate_contents(*preserved)
            for attempt in range(2):
                async def sleep(_): raise asyncio.CancelledError
                with patch.object(ds, 'datetime', FixedDate), patch.object(ds.asyncio, 'sleep', sleep), patch.object(ds, '_run_scheduled_worker', side_effect=AssertionError('no catch-up worker')):
                    try: asyncio.run(ds.disk_sweeper_loop(state))
                    except asyncio.CancelledError: pass
            for g in orgs:
                saved = g.db.execute("SELECT task_id,agent,payload FROM audit_log WHERE action='disk_sweep_night_notice'").fetchall()
                assert len(saved) == (0 if kind == 'complete' else 1), (kind, saved)
                if saved:
                    assert saved[0][:2] == ('', 'daemon')
                    payload = json.loads(saved[0][2])
                    assert payload['scheduled_date'] == expected and payload['status'] == ('missing' if kind == 'missing' else 'incomplete'), payload
                    assert payload['reasons'] and payload['summary_path'] == (None if kind == 'missing' else str(source))
                    if kind == 'suspended': assert 'scheduled_suspension' in payload['reasons']
            assert not stop.with_name('last-scheduled.json').exists() and snapshot == candidate_contents(*preserved)
            # Drive the pre-night hook too, with the same affected night and no retry.
            sleeps = []
            async def sleep_once(_):
                sleeps.append(1)
                if len(sleeps) > 1: raise asyncio.CancelledError
            stop.write_text('{"stop":true}')
            with patch.object(ds, 'datetime', FixedDate), patch.object(ds.asyncio, 'sleep', sleep_once):
                try: asyncio.run(ds.disk_sweeper_loop(state))
                except asyncio.CancelledError: pass
            for g in orgs:
                assert g.db.execute("SELECT COUNT(*) FROM audit_log WHERE action='disk_sweep_night_notice'").fetchone()[0] == (0 if kind == 'complete' else 1)
            observations.append(dict(kind=kind, checks='startup/restart/pre-night', records_per_org=0 if kind == 'complete' else 1))
    return observations


def c30():
    """Interrupted-removal provenance survives in JSON/history without recovery."""
    with fixture() as f:
        p = f.scratch('TASK-1')
        summary = f.base / 'daemon/disk-sweep/2026-11-13.json'
        def interrupted(path, tree, deadline, **kwargs):
            (p / 'nested/payload').unlink()
            raise ds.Expired('total sweep deadline')
        with patch.object(ds, 'mutate', interrupted), patch.object(time, 'time', return_value=NOW):
            report = ds.sweep([str(f.org)], time.monotonic() + 12, summary, dry_run=False, scheduled_date='2026-11-13', revision='interruption-fixture')
        saved = json.loads(summary.read_text())
        records = saved['orgs'][0].get('interrupted_removals', [])
        assert len(records) == 1 and records[0]['path'] == str(p), saved
        assert records[0]['effect'] == 'unknown' and records[0]['automatic_recovery'] is False
        assert not saved.get('night', {}).get('complete', False) and p.exists()
        before = candidate_contents(p)
        later = summary.with_name('2026-11-14.json')
        with patch.object(ds, 'mutate', side_effect=AssertionError('record cannot authorize recovery')):
            preview = ds.sweep([str(f.org)], time.monotonic() + 12, later, dry_run=True, scheduled_date='2026-11-14', revision='later-fixture')
        prior = preview.get('previous_interrupted_removals', [])
        assert len(prior) == 1 and prior[0]['summary_path'] == str(summary) and prior[0]['source_revision'] == 'interruption-fixture', preview
        assert prior[0]['path'] == str(p) and before == candidate_contents(p)
        assert ds._audit_payload(report, report['orgs'][0], summary)['org']['interrupted_removals'] == records
        return dict(current=records, previous=prior, automatic_recovery=False)


OWNERSHIP_BASE = '2b61f5c480974b494ccdbabd591c878a48b7d8f5'


def ownership_base():
    """C24 pattern: execute pinned production source, not a policy oracle mock."""
    def source(path):
        return subprocess.run(['git', 'show', f'{OWNERSHIP_BASE}:{path}'], check=True, capture_output=True).stdout
    old_fs = ModuleType('ownership_base_fs')
    exec(compile(source('runtime/daemon/_disk_sweep_fs.py'), '<ownership base fs>', 'exec'), old_fs.__dict__)
    old = ModuleType('ownership_base_sweeper')
    with patch.dict(sys.modules, {'runtime.daemon._disk_sweep_fs': old_fs}):
        exec(compile(source('runtime/daemon/disk_sweeper.py'), '<ownership base sweeper>', 'exec'), old.__dict__)
    return old


def ownership_indexes(f):
    """Copies of EXISTING production indexes, only on this disposable database."""
    f.db.executescript('''
    CREATE INDEX jobs_status_idx ON jobs(status);
    CREATE INDEX jobs_task_id_idx ON jobs(task_id);
    CREATE INDEX idx_audit_log_task_id ON audit_log(task_id);
    ''')


def ownership_decisions(report, base):
    """Compare policy outputs, excluding clocks, inode allocation and counters."""
    return dict(outcome=report['outcome'], coverage=report['coverage'],
                candidates=[{key: str(c[key]).replace(str(base), '<fixture>') for key in
                             ('phase', 'path', 'state', 'reason') if key in c}
                            for c in report['candidates']])


def c31():
    """Identical-input base/current ownership and whole-org decision parity."""
    old = ownership_base()
    # Literal expected reasons are independent of either implementation.
    table = [
        ('ended', '', None),
        ('open_intermediate', "INSERT INTO tasks VALUES('TASK-5','TASK-1','in_progress','maker'); UPDATE tasks SET parent_task_id='TASK-5' WHERE id='TASK-2';", None),
        ('terminal_orphans', "INSERT INTO jobs VALUES('gone','completed'),('gone','failed'),('gone','rejected');", None),
        ('unrelated_active', "INSERT INTO jobs VALUES('TASK-4','running');", None),
        ('pending_sibling', "INSERT INTO jobs VALUES('TASK-3','pending');", 'active_job:pending'),
        ('queued_sibling', "INSERT INTO jobs VALUES('TASK-3','queued');", 'active_job:queued'),
        ('running_sibling', "INSERT INTO jobs VALUES('TASK-3','running');", 'active_job:running'),
        ('unknown_status', "INSERT INTO jobs VALUES('TASK-4','unrecognised');", 'unknown_job_status'),
        ('null_status', "INSERT INTO jobs VALUES('TASK-4',NULL);", 'unknown_job_status'),
        ('missing_job_owner', "INSERT INTO jobs VALUES('missing','running');", 'unknown_missing_or_cyclic_lineage'),
        ('malformed_job_owner', "INSERT INTO jobs VALUES('','running');", 'unknown_malformed_job_or_task_lineage'),
        ('null_job_owner', "INSERT INTO jobs VALUES(NULL,'running');", 'unknown_malformed_job_or_task_lineage'),
        ('job_cycle', "UPDATE tasks SET parent_task_id='TASK-4' WHERE id='TASK-4'; INSERT INTO jobs VALUES('TASK-4','running');", 'unknown_missing_or_cyclic_lineage'),
        ('missing_task', "DELETE FROM tasks WHERE id='TASK-2';", 'unknown_missing_or_cyclic_lineage'),
        ('candidate_cycle', "UPDATE tasks SET parent_task_id='TASK-2' WHERE id='TASK-1';", 'unknown_missing_or_cyclic_lineage'),
        ('malformed_parent', "UPDATE tasks SET parent_task_id='' WHERE id='TASK-1';", 'unknown_conflicting_lineage'),
        ('open_root', "UPDATE tasks SET status='in_progress' WHERE id='TASK-1';", 'task_or_root_not_terminal:in_progress'),
        ('open_task', "UPDATE tasks SET status='pending' WHERE id='TASK-2';", 'task_or_root_not_terminal:pending'),
        ('wrong_workspace', "UPDATE tasks SET assigned_agent='other' WHERE id='TASK-2';", 'unknown_owner_workspace'),
        ('missing_workspace', "DELETE FROM audit_log WHERE task_id='TASK-2';", 'unknown_missing_or_conflicting_workspace_evidence'),
        ('malformed_workspace', "UPDATE audit_log SET payload='[]' WHERE task_id='TASK-2';", 'unknown_workspace_evidence'),
        ('conflicting_workspace', "INSERT INTO audit_log(task_id,action,payload) VALUES('TASK-2','session_start','{\"workspace\":\"/other\"}');", 'unknown_missing_or_conflicting_workspace_evidence'),
        ('single_null_task', "INSERT INTO tasks VALUES(NULL,NULL,'completed','maker');", None),
        ('duplicate_null_tasks', "INSERT INTO tasks VALUES(NULL,NULL,'completed','maker'),(NULL,NULL,'completed','maker');", 'unknown_conflicting_lineage'),
        ('active_before_unknown', "INSERT INTO jobs VALUES('TASK-3','running'),('TASK-4','unrecognised');", 'active_job:running'),
        ('unknown_before_active', "INSERT INTO jobs VALUES('TASK-4','unrecognised'),('TASK-3','running');", 'unknown_job_status'),
    ]
    observations = []
    for label, sql, expected in table:
        with fixture() as f:
            ownership_indexes(f)
            f.task('TASK-1')
            path = f.scratch('TASK-2', parent='TASK-1')
            f.task('TASK-3', parent='TASK-1'); f.task('TASK-4')
            f.db.executescript(sql)
            before = candidate_contents(path)
            answers = []
            for module in (old, ds):
                try:
                    module.Evidence(f.org, time.monotonic() + 10).ownership(path, f.workspace, path.name)
                    answers.append((None, None))
                except Exception as exc:
                    answers.append((str(exc), getattr(exc, 'finding', None)))
            assert answers[0] == answers[1] and answers[1][0] == expected, (label, expected, answers)
            reports = [module.sweep_org(f.org, time.monotonic() + 15, mode='dry_run', now=NOW) for module in (old, ds)]
            decisions = [ownership_decisions(report, f.base) for report in reports]
            assert decisions[0] == decisions[1], (label, decisions)
            actual = row(reports[1], path)
            assert actual['state'] == ('retained' if expected else 'eligible'), (label, actual)
            assert actual.get('reason') == expected, (label, actual)
            assert candidate_contents(path) == before, label
            observations.append(dict(variant=label, reason=expected, finding=answers[1][1], result='PASS'))
    return dict(base=OWNERSHIP_BASE, variants=observations, equivalence_differences=[],
                limits='same-input real SQLite/scratch preview; large actual removal equivalence is C33')


def c32():
    """Ownership changes after eligible inventory must refuse final removal."""
    variants = {
        'task_reopened': ("UPDATE tasks SET status='in_progress' WHERE id='TASK-2'", "UPDATE tasks SET status='completed' WHERE id='TASK-2'", 'task_or_root_not_terminal:in_progress'),
        'root_reopened': ("UPDATE tasks SET status='in_progress' WHERE id='TASK-1'", "UPDATE tasks SET status='completed' WHERE id='TASK-1'", 'task_or_root_not_terminal:in_progress'),
        'root_reparented': ("UPDATE tasks SET parent_task_id='TASK-4' WHERE id='TASK-1'", "UPDATE tasks SET parent_task_id=NULL WHERE id='TASK-1'", 'task_or_root_not_terminal:in_progress'),
        'new_sibling_job': ("INSERT INTO jobs VALUES('TASK-3','pending')", "DELETE FROM jobs WHERE status='pending'", 'active_job:pending'),
        'job_status_unknown': ("UPDATE jobs SET status='unrecognised'", "UPDATE jobs SET status='completed'", 'unknown_job_status'),
        'job_ancestry_unknown': ("INSERT INTO jobs VALUES('missing','running')", "DELETE FROM jobs WHERE status='running'", 'unknown_missing_or_cyclic_lineage'),
    }
    observations, failures = [], []
    old = ownership_base()
    for phase in ('scratch', 'worktree'):
        for label, (change, heal, expected) in variants.items():
            for module in (old, ds):
                try:
                    with fixture() as f:
                        ownership_indexes(f)
                        f.task('TASK-1'); f.task('TASK-3', parent='TASK-1'); f.task('TASK-4', status='in_progress')
                        if phase == 'scratch':
                            path = f.scratch('TASK-2', parent='TASK-1')
                            peer = f.scratch('TASK-9')
                            repo = None
                        else:
                            repo, path = f.worktree('TASK-2')
                            _, peer = f.worktree('TASK-9')
                            f.db.execute("UPDATE tasks SET parent_task_id='TASK-1' WHERE id='TASK-2'")
                        f.db.execute("INSERT INTO jobs VALUES('TASK-3','completed')"); f.db.commit()
                        before = candidate_contents(path)
                        refs = f.git(repo, 'show-ref') if repo else None
                        result, events, actions = {}, [], []
                        real_git = module.git
                        def git(root, deadline, *args):
                            if args[:2] == ('worktree', 'remove'): actions.append(args[2])
                            return real_git(root, deadline, *args)
                        def checkpoint():
                            target = next((c for c in result.get('candidates', []) if c['path'] == str(path)), None)
                            if target and target['state'] == 'eligible' and not events:
                                f.db.execute(change); f.db.commit(); events.append('changed_after_inventory')
                            elif target and target['state'] == 'retained' and events == ['changed_after_inventory']:
                                assert target.get('reason') == expected, (phase, label, target)
                                f.db.execute(heal); f.db.commit()
                                # A fresh read really passes now; the night's failed
                                # observation must still retain the original item.
                                module.Evidence(f.org, time.monotonic() + 10).ownership(path, f.workspace, path.name)
                                events.append('healed_after_refusal')
                        with patch.object(module, 'git', git):
                            report = module.sweep_org(f.org, time.monotonic() + 30, mode='on', now=NOW, result=result, checkpoint=checkpoint)
                        assert events == ['changed_after_inventory', 'healed_after_refusal'], (phase, label, events, row(report, path))
                        retained(report, path, expected)
                        assert candidate_contents(path) == before and str(path) not in actions, (phase, label, actions)
                        if repo: assert f.git(repo, 'show-ref') == refs
                        org_held = label in ('job_status_unknown', 'job_ancestry_unknown')
                        assert row(report, peer)['state'] == ('retained' if org_held else 'removed'), (phase, label, row(report, peer))
                        if org_held and module is ds: assert report['org_hold'] == expected
                        observations.append(dict(module='base' if module is old else 'new', phase=phase, race=label, result='PASS', reason=expected, events=events))
                except AssertionError as exc:
                    failures.append(dict(phase=phase, race=label, result='FAIL', error=str(exc)[:2000]))
    # Keep all rows attributable in the intended-fail mutation run.
    assert not failures, dict(passed=observations, failures=failures)
    return dict(variants=observations, limits='ownership races only; complete non-ownership Step3 race table remains required')



def c33():
    """Full-size synthetic fixture, actual base/new sweeps, no live data reads."""
    import hashlib
    scratch_root = Path(os.environ['TMPDIR']).resolve()
    assert 'task-tmp' in scratch_root.parts, 'C33 requires TMPDIR in runtime task-tmp, never output/'
    old = ownership_base()
    measurements, decisions = {}, {}
    for label, module in (('base', old), ('new', ds)):
        with patch.object(tempfile, 'tempdir', str(scratch_root)), fixture() as first:
            orgs = [first]
            other_base = first.base / 'second'; other_base.mkdir()
            second = Fixture(other_base); orgs.append(second)
            # Same runtime, distinct orgs, two clones per org. All data/content
            # is generated here; no live DB rows or worktree contents are read.
            second.db.close()
            old_org = second.org
            second.org = first.org.with_name('second')
            old_org.rename(second.org)
            second.workspace = second.org / 'workspaces/maker'
            second.db = sqlite3.connect(second.org / 'happyranch.db')
            try:
                for f in orgs:
                    ownership_indexes(f)
                    f.task('TASK-9000')
                    for index in range(370):
                        f.scratch(f'TASK-{index + 1}', status='completed' if index < 4 else 'in_progress', parent='TASK-9000')
                    for clone in range(2):
                        for index in range(215):
                            name = f'TASK-{1000 + clone * 215 + index}'
                            _, path = f.worktree(name, project=f'project-{clone}')
                            f.db.execute("UPDATE tasks SET parent_task_id='TASK-9000' WHERE id=?", (name,))
                            if index >= 2:
                                if index % 2:
                                    (path / 'source').write_text('uncommitted synthetic fixture\n')
                                else:
                                    f.db.execute('UPDATE tasks SET status=? WHERE id=?', ('in_progress', name))
                    remaining = 7950 - f.db.execute('SELECT count(*) FROM tasks').fetchone()[0]
                    f.db.executemany('INSERT INTO tasks VALUES(?,NULL,?,?)',
                                     ((f'TASK-{10000 + n}', 'completed', 'maker') for n in range(remaining)))
                    f.db.executemany('INSERT INTO jobs VALUES(?,?)', (('historical-orphan', ('completed', 'failed', 'rejected')[n % 3]) for n in range(3349)))
                    f.db.execute("INSERT INTO jobs VALUES('TASK-10000','running')")
                    f.db.commit()
                summary = first.base / 'daemon/disk-sweep' / (label + '.json')
                started = time.monotonic()
                with patch.object(time, 'time', return_value=NOW):
                    report = module.sweep([str(f.org) for f in orgs], started + 1800, summary,
                                          dry_run=False, scheduled_date='fixture-date', revision='synthetic-step3-fixture',
                                          runtime_root=first.base / 'runtime')
                elapsed = time.monotonic() - started
                combined = dict(wall_seconds=elapsed, git={}, censuses=0, topology_reads=0, ownership_reads=0, report_writes=0,
                                candidates=0, removed=0, retained=0, outcome=report['outcome'], orgs=[])
                projected = []
                for item in report['orgs']:
                    counters = item['measurements']['counters']
                    for key in ('censuses', 'topology_reads', 'ownership_reads', 'report_writes'):
                        combined[key] += counters[key]
                    for command, counts in counters['git'].items():
                        combined['git'][command] = combined['git'].get(command, 0) + counts['calls']
                    combined['candidates'] += len(item['candidates'])
                    combined['removed'] += sum(c['state'] == 'removed' for c in item['candidates'])
                    combined['retained'] += sum(c['state'] == 'retained' for c in item['candidates'])
                    combined['orgs'].append(dict(org=item['org'], outcome=item['outcome'], coverage=item['coverage']))
                    projected.append(ownership_decisions(item, first.base))
                raw = json.dumps(projected, sort_keys=True, separators=(',', ':')).encode()
                combined['decision_sha256'] = hashlib.sha256(raw).hexdigest()
                measurements[label], decisions[label] = combined, projected
                # Flush bounded metrics as each side finishes, even if later
                # measurement/equivalence fails. Full trees stay only task-tmp.
                print(json.dumps(dict(production_fixture_side=label, measurement=combined)), flush=True)
            finally:
                second.db.close()
    assert measurements['base']['candidates'] == measurements['new']['candidates'] == 1600, measurements
    assert measurements['base']['removed'] == measurements['new']['removed'] == 16, measurements
    assert decisions['base'] == decisions['new'], measurements
    return dict(base=OWNERSHIP_BASE, shape=dict(orgs=2, clones=4, linked_registrations=860, primary_registrations=4,
                candidates=1600, tasks=15900, jobs=6700), measurements=measurements, equivalence_differences=[],
                limits='disposable Linux fixture only; no real-night timing, under-ten-minute target, Darwin or absence-of-loss proof')


def c34():
    """Batched real publication retains crash intent and refuses unsafe paths."""
    observations, failures = [], []
    old = ownership_base()
    with fixture() as f:
        for index in range(40): f.scratch(f'TASK-{index + 1}', status='in_progress')
        before = candidate_contents(*(f.workspace / '.happyranch/task-tmp').iterdir())
        reports, writes = [], []
        for label, module in (('base', old), ('new', ds)):
            summary = f.base / 'daemon/disk-sweep' / (label + '.json')
            real_publication, published = module.atomic_json, []
            def publication(path, value):
                real_publication(path, value)
                published.append(json.loads(path.read_text()))
            with patch.object(module, 'atomic_json', publication):
                reports.append(module.sweep([str(f.org)], time.monotonic() + 20, summary,
                    dry_run=False, scheduled_date='2026-11-13', revision='checkpoint-fixture'))
            writes.append(len(published))
            assert all(not p.get('night', {}).get('complete', False) for p in published), label
            for item in reports[-1]['orgs'][0]['candidates']:
                assert item['state'] == 'retained' and item['reason'] == 'task_or_root_not_terminal:in_progress', item
        assert ownership_decisions(reports[0]['orgs'][0], f.base) == ownership_decisions(reports[1]['orgs'][0], f.base)
        assert writes[1] < writes[0] / 4, writes
        assert candidate_contents(*(f.workspace / '.happyranch/task-tmp').iterdir()) == before
        assert reports[1]['night']['reasons'] == ['supervisor_closure_unproven'], reports[1]['night']
        observations.append(dict(variant='forty_located_keeps', base_writes=writes[0], new_writes=writes[1], decision_differences=[]))
        # Abrupt process death while unpublished keep progress is in memory.
        original, calls = ds._summarize_org, []
        def interrupted(value):
            original(value)
            finished = [c['path'] for c in value.get('candidates', []) if c['state'] == 'retained']
            calls[:] = finished
            if len(finished) == 3: raise KeyboardInterrupt('synthetic abrupt stop between batches')
        summary = f.base / 'daemon/disk-sweep/crash-keeps.json'
        try:
            with patch.object(ds, '_summarize_org', interrupted):
                ds.sweep([str(f.org)], time.monotonic() + 20, summary,
                    dry_run=False, scheduled_date='2026-11-13', revision='checkpoint-fixture')
        except KeyboardInterrupt:
            pass
        else:
            raise AssertionError('crash was not reached')
        saved = ds._read_json(summary)
        assert saved['outcome'] == 'incomplete' and 'finished_at' not in saved, saved
        assert not saved.get('night', {}).get('complete', False)
        assert not any(c['state'] in ('removed', 'eligible') for o in saved['orgs'] for c in o.get('candidates', []))
        observations.append(dict(variant='crash_between_keep_batches', calls=len(calls), persisted_outcome=saved['outcome']))

    for phase in ('scratch', 'worktree'):
        try:
            with fixture() as f:
                if phase == 'scratch': path = f.scratch('TASK-1')
                else: _, path = f.worktree('TASK-1')
                before = candidate_contents(path)
                summary = f.base / 'daemon/disk-sweep/crash-intent.json'
                reached = []
                def crash():
                    saved = ds._read_json(summary)
                    target = row(saved['orgs'][0], path)
                    assert target['state'] == 'mutation_in_flight', (phase, target)
                    records = saved['orgs'][0]['interrupted_removals']
                    assert len(records) == 1 and records[0]['path'] == str(path) and records[0]['effect'] == 'unknown', records
                    assert not saved.get('night', {}).get('complete', False) and saved['outcome'] == 'incomplete'
                    reached.append('persisted_before_effect')
                    raise KeyboardInterrupt('synthetic abrupt stop before removal')
                original_git = ds.git
                def git(repo, deadline, *args):
                    if args[:2] == ('worktree', 'remove'): crash()
                    return original_git(repo, deadline, *args)
                def mutate(*args, **kwargs): crash()
                try:
                    with patch.object(time, 'time', return_value=NOW), patch.object(ds, 'git', git), patch.object(ds, 'mutate', mutate):
                        ds.sweep([str(f.org)], time.monotonic() + 20, summary,
                            dry_run=False, scheduled_date='2026-11-13', revision='checkpoint-fixture')
                except KeyboardInterrupt:
                    pass
                assert reached == ['persisted_before_effect'] and candidate_contents(path) == before, (phase, reached)
                observations.append(dict(variant='crash_before_' + phase + '_remove', persisted_intent=True, effects=False))
        except AssertionError as exc:
            failures.append(dict(variant=phase, error=str(exc)[:2000]))

    for variant in ('pending_file', 'pending_symlink', 'pending_hardlink', 'final_hardlink', 'parent_symlink'):
        try:
            with fixture() as f:
                path = f.scratch('TASK-1')
                before = candidate_contents(path)
                summary = f.base / 'daemon/disk-sweep/refused.json'
                sentinel = f.base / 'sentinel'; sentinel.write_bytes(b'protected publication sentinel\n')
                original, changes = ds.admit, []
                saved_path = summary
                pending = summary.with_name(summary.name + '.pending')
                def admit(*args, **kwargs):
                    nonlocal saved_path
                    tree = original(*args, **kwargs)
                    if not changes:
                        changes.append('after_inventory_before_delete')
                        if variant == 'pending_file': pending.write_bytes(b'unowned pending\n')
                        elif variant == 'pending_symlink': pending.symlink_to(sentinel)
                        elif variant == 'pending_hardlink': os.link(sentinel, pending)
                        elif variant == 'final_hardlink': os.link(summary, f.base / 'other-report-link')
                        else:
                            held = f.base / 'held-reports'
                            summary.parent.rename(held); summary.parent.symlink_to(held, target_is_directory=True)
                            saved_path = held / summary.name
                    return tree
                refused = None
                try:
                    with patch.object(ds, 'admit', admit), patch.object(time, 'time', return_value=NOW):
                        ds.sweep([str(f.org)], time.monotonic() + 20, summary,
                            dry_run=False, scheduled_date='2026-11-13', revision='checkpoint-fixture')
                except (OSError, ds.Retain) as exc:
                    refused = str(exc)
                assert changes == ['after_inventory_before_delete'] and refused, (variant, changes, refused)
                assert path.exists(), (variant, 'candidate removed before safe intent publication')
                assert candidate_contents(path) == before and sentinel.read_bytes() == b'protected publication sentinel\n', variant
                # The hardlinked-target variant intentionally makes the
                # production reader refuse too; inspect our fixture bytes.
                saved = json.loads(saved_path.read_text())
                assert saved['outcome'] == 'incomplete' and not saved.get('night', {}).get('complete', False), (variant, saved)
                if variant == 'pending_file': assert pending.read_bytes() == b'unowned pending\n'
                observations.append(dict(variant=variant, refusal=refused, effects=False, sentinel='unchanged'))
        except AssertionError as exc:
            failures.append(dict(variant=variant, error=str(exc)[:2000]))
    assert not failures, dict(passed=observations, failures=failures)
    return dict(variants=observations, limits='sampled disposable publication/crash boundaries; no physical power-loss durability or real-night performance claim')


def c35():
    """Org refusal inventory is final; successful observations never authorize."""
    observations, failures = [], []
    old = ownership_base()
    positive = {
        'task_reopened': ("UPDATE tasks SET status='in_progress' WHERE id='TASK-2'", "UPDATE tasks SET status='completed' WHERE id='TASK-2'", 'task_or_root_not_terminal:in_progress'),
        'root_reopened': ("UPDATE tasks SET status='in_progress' WHERE id='TASK-1'", "UPDATE tasks SET status='completed' WHERE id='TASK-1'", 'task_or_root_not_terminal:in_progress'),
        'root_reparented': ("UPDATE tasks SET parent_task_id='TASK-4' WHERE id='TASK-1'", "UPDATE tasks SET parent_task_id=NULL WHERE id='TASK-1'", 'task_or_root_not_terminal:in_progress'),
        'new_sibling_job': ("INSERT INTO jobs VALUES('TASK-3','pending')", "DELETE FROM jobs WHERE status='pending'", 'active_job:pending'),
        'job_status_unknown': ("UPDATE jobs SET status='unrecognised'", "UPDATE jobs SET status='completed'", 'unknown_job_status'),
        'job_ancestry_unknown': ("INSERT INTO jobs VALUES('missing','running')", "DELETE FROM jobs WHERE status='running'", 'unknown_missing_or_cyclic_lineage'),
    }
    negative = {key: positive[key] for key in ('task_reopened', 'root_reopened', 'new_sibling_job', 'job_status_unknown')}
    for phase in ('scratch', 'worktree'):
        for direction, variants in (('negative_heals', negative), ('positive_changes', positive)):
            for label, (change, heal, expected) in variants.items():
                for module in (old, ds):
                    try:
                        with fixture() as f:
                            ownership_indexes(f)
                            f.task('TASK-1'); f.task('TASK-3', parent='TASK-1'); f.task('TASK-4', status='in_progress')
                            if phase == 'scratch': path = f.scratch('TASK-2', parent='TASK-1'); repo = None
                            else:
                                repo, path = f.worktree('TASK-2')
                                f.db.execute("UPDATE tasks SET parent_task_id='TASK-1' WHERE id='TASK-2'")
                            f.db.execute("INSERT INTO jobs VALUES('TASK-3','completed')"); f.db.commit()
                            if direction == 'negative_heals': f.db.execute(change); f.db.commit()
                            before = candidate_contents(path)
                            result, events, current_reads = {}, [], []
                            original_inventory, original_read = getattr(module.Evidence, 'inventory', None), module.Evidence.ownership
                            def inventory(self, candidates):
                                refused = original_inventory(self, candidates)
                                if direction == 'negative_heals':
                                    assert str(path) in refused and str(refused[str(path)]) == expected, (label, refused)
                                    f.db.execute(heal)
                                else:
                                    assert str(path) not in refused, (label, refused)
                                    f.db.execute(change)
                                f.db.commit(); events.append('changed_after_org_inventory')
                                return refused
                            def ownership(self, *args):
                                current_reads.append(args[0])
                                if module is old and not events:
                                    if direction == 'positive_changes':
                                        f.db.execute(change); f.db.commit(); events.append('changed_after_org_inventory')
                                    else:
                                        try: original_read(self, *args)
                                        except module.Retain as exc:
                                            assert str(exc) == expected, (label, exc)
                                            f.db.execute(heal); f.db.commit(); events.append('changed_after_org_inventory')
                                            raise
                                        raise AssertionError('base negative did not refuse')
                                return original_read(self, *args)
                            def checkpoint():
                                target = next((c for c in result.get('candidates', []) if c['path'] == str(path)), None)
                                if direction == 'positive_changes' and target and target['state'] == 'retained' and len(events) == 1:
                                    assert target.get('reason') == expected, (label, target)
                                    f.db.execute(heal); f.db.commit(); events.append('healed_after_refusal')
                            with patch.object(module.Evidence, 'inventory', inventory, create=True), patch.object(module.Evidence, 'ownership', ownership):
                                report = module.sweep_org(f.org, time.monotonic() + 20, mode='on', now=NOW, result=result, checkpoint=checkpoint)
                            retained(report, path, expected)
                            assert candidate_contents(path) == before, (direction, phase, label)
                            assert events == (['changed_after_org_inventory'] if direction == 'negative_heals' else
                                              ['changed_after_org_inventory', 'healed_after_refusal']), events
                            assert bool(current_reads) == (module is old or direction == 'positive_changes'), (direction, phase, label, current_reads)
                            if repo: assert f.git(repo, 'rev-parse', f'refs/heads/task/{path.name}').strip()
                            module.Evidence(f.org, time.monotonic() + 10).ownership(path, f.workspace, path.name)
                            if module is ds and label in ('job_status_unknown', 'job_ancestry_unknown'): assert report['org_hold'] == expected
                            observations.append(dict(module='base' if module is old else 'new', phase=phase, direction=direction, variant=label, result='PASS', reason=expected,
                                                     current_reads=len(current_reads), events=events))
                    except AssertionError as exc:
                        failures.append(dict(phase=phase, direction=direction, variant=label, error=str(exc)[:2000]))
    # Production SQL is observed, never substituted. Forty job-held candidates
    # need one task/job inventory, no current ownership reads or Git/tree work.
    old = ownership_base()
    with fixture() as f:
        ownership_indexes(f); f.task('TASK-900'); f.task('TASK-901', parent='TASK-900')
        for index in range(40): f.scratch(f'TASK-{index + 1}', parent='TASK-900')
        f.db.execute("INSERT INTO jobs VALUES('TASK-901','pending')"); f.db.commit()
        reports = [old.sweep_org(f.org, time.monotonic() + 15, mode='on', now=NOW)]
        original_connect, original_read = sqlite3.connect, ds.Evidence.ownership
        queries, reads = [], []
        def connect(*args, **kwargs):
            conn = original_connect(*args, **kwargs); conn.set_trace_callback(queries.append); return conn
        def ownership(self, *args):
            reads.append(args[0]); return original_read(self, *args)
        with patch.object(sqlite3, 'connect', connect), patch.object(ds.Evidence, 'ownership', ownership):
            reports.append(ds.sweep_org(f.org, time.monotonic() + 15, mode='on', now=NOW))
        assert ownership_decisions(reports[0], f.base) == ownership_decisions(reports[1], f.base), reports
        counts = dict(current_reads=len(reads), task_inventories=queries.count('SELECT id,parent_task_id,status,assigned_agent FROM tasks'),
                      job_inventories=queries.count('SELECT DISTINCT status FROM jobs'))
        if counts != dict(current_reads=0, task_inventories=1, job_inventories=1):
            failures.append(dict(variant='forty_job_keeps_one_inventory', counts=counts))
        for item in reports[1]['candidates']:
            assert item['state'] == 'retained' and item['reason'] == 'active_job:pending', item
        observations.append(dict(variant='forty_job_keeps_one_inventory', task_inventories=1, job_inventories=1,
                                 fresh_reads=len(reads), decision_differences=[]))
    assert not failures, dict(passed=observations, failures=failures)
    return dict(variants=observations, limits='ownership inventory only; topology/candidate race proof remains required')


def c36():
    """Same-input keep decisions, negative healing latch and one clone census."""
    observations, failures = [], []
    old = ownership_base()
    expected = dict(tracked='dirty_or_untracked', untracked='dirty_or_untracked',
                    guard='unknown_worktree_owner', locked='git_unregistered_locked_or_prunable',
                    branch='unknown_worktree_branch_owner', active='task_or_root_not_terminal:in_progress')
    for label in (*expected, 'clean'):
        with fixture() as f:
            ownership_indexes(f); repo, path = f.worktree('TASK-1')
            if label in ('tracked', 'active'): (path / 'source').write_text('changed\n')
            if label == 'untracked': (path / 'new-file').write_text('new\n')
            if label == 'guard': (path / '.worktree-guard.json').write_text('{}')
            if label == 'locked': f.git(repo, 'worktree', 'lock', str(path))
            if label == 'branch':
                f.git(repo, 'branch', 'other', 'HEAD'); f.git(path, 'symbolic-ref', 'HEAD', 'refs/heads/other')
            if label == 'active':
                f.db.execute("UPDATE tasks SET status='in_progress'"); f.db.commit()
            reports = [module.sweep_org(f.org, time.monotonic() + 20, mode='dry_run', now=NOW) for module in (old, ds)]
            assert ownership_decisions(reports[0], f.base) == ownership_decisions(reports[1], f.base), (label, reports)
            target = reports[1]['candidates'][0]
            assert target['state'] == ('eligible' if label == 'clean' else 'retained'), (label, target)
            if label in expected: assert target['reason'] == expected[label], (label, target)
            observations.append(dict(variant='base_equivalence/' + label, decision=target['state'], reason=target.get('reason')))
    for direction in ('negative_heals', 'positive_changes'):
        for label in ('tracked', 'untracked', 'guard', 'locked'):
            for module in (old, ds):
                try:
                    with fixture() as f:
                        ownership_indexes(f); repo, path = f.worktree('TASK-1')
                        guard = (path / '.worktree-guard.json').read_bytes()
                        original_inventory, original_admit = getattr(module, 'worktree_inventory', None), module.admit
                        events, admissions, result = [], [], {}
                        def change():
                            if label == 'tracked': (path / 'source').write_text('changed\n')
                            if label == 'untracked': (path / 'new-file').write_text('new\n')
                            if label == 'guard': (path / '.worktree-guard.json').write_text('{}')
                            if label == 'locked': f.git(repo, 'worktree', 'lock', str(path))
                        def heal():
                            if label == 'tracked': (path / 'source').write_text('committed\n')
                            if label == 'untracked': (path / 'new-file').unlink()
                            if label == 'guard': (path / '.worktree-guard.json').write_bytes(guard)
                            if label == 'locked': f.git(repo, 'worktree', 'unlock', str(path))
                        if direction == 'negative_heals': change()
                        def inventory(*args, **kwargs):
                            refused = original_inventory(*args, **kwargs)
                            if direction == 'negative_heals':
                                assert str(refused) == expected[label], (label, refused)
                                heal()
                            else:
                                assert refused is None, (label, refused)
                                change()
                            events.append('changed_after_worktree_inventory')
                            return refused
                        def admit(*args, **kwargs):
                            admissions.append(args[0]['path'])
                            if module is old and not events:
                                if direction == 'positive_changes':
                                    change(); events.append('changed_after_worktree_inventory')
                                else:
                                    try: original_admit(*args, **kwargs)
                                    except module.Retain as exc:
                                        assert str(exc) == expected[label], (label, exc)
                                        heal(); events.append('changed_after_worktree_inventory')
                                        raise
                                    raise AssertionError('base negative did not refuse')
                            return original_admit(*args, **kwargs)
                        def checkpoint():
                            target = next((c for c in result.get('candidates', []) if c['path'] == str(path)), None)
                            if direction == 'positive_changes' and target and target['state'] == 'retained' and len(events) == 1:
                                assert target['reason'] == expected[label], (label, target)
                                heal(); events.append('healed_after_refusal')
                        with patch.object(module, 'worktree_inventory', inventory, create=True), patch.object(module, 'admit', admit):
                            report = module.sweep_org(f.org, time.monotonic() + 20, mode='on', now=NOW, result=result, checkpoint=checkpoint)
                        retained(report, path, expected[label])
                        assert (path / 'source').read_text() == 'committed\n' and not (path / 'new-file').exists()
                        assert (path / '.worktree-guard.json').read_bytes() == guard
                        assert f.git(repo, 'rev-parse', 'refs/heads/task/TASK-1').strip()
                        assert bool(admissions) == (module is old or direction == 'positive_changes'), (direction, label, admissions)
                        assert events == (['changed_after_worktree_inventory'] if direction == 'negative_heals' else
                                          ['changed_after_worktree_inventory', 'healed_after_refusal']), events
                        # Healing is real; it cannot grant this night a second try.
                        module.admit(dict(phase='worktree', path=str(path), workspace=str(f.workspace), repo=str(repo)),
                                 module.Evidence(f.org, time.monotonic() + 10), time.monotonic() + 10, now=NOW)
                        observations.append(dict(variant=('base/' if module is old else 'new/') + direction + '/' + label, result='PASS', reason=expected[label], admissions=len(admissions)))
                except AssertionError as exc:
                    failures.append(dict(variant=('base/' if module is old else 'new/') + direction + '/' + label, error=str(exc)[:2000]))
    with fixture() as f:
        ownership_indexes(f)
        for index in range(40):
            repo, path = f.worktree(f'TASK-{index + 1}'); (path / 'source').write_text('changed\n')
        reports = [module.sweep_org(f.org, time.monotonic() + 30, mode='on', now=NOW) for module in (old, ds)]
        assert ownership_decisions(reports[0], f.base) == ownership_decisions(reports[1], f.base), reports
        before, after = [r['measurements']['counters'] for r in reports]
        if after['topology_reads'] != 1 or after['censuses'] != 3 or not after['censuses'] < before['censuses']:
            failures.append(dict(variant='forty_dirty_keeps_one_clone_inventory', base=before, new=after))
        assert all(c['state'] == 'retained' and c['reason'] == 'dirty_or_untracked' for c in reports[1]['candidates'])
        observations.append(dict(variant='forty_dirty_keeps_one_clone_inventory', base_censuses=before['censuses'],
                                 new_censuses=after['censuses'], new_topology_reads=after['topology_reads'], decision_differences=[]))
    assert not failures, dict(passed=observations, failures=failures)
    return dict(variants=observations, limits='Keep-only topology/guard/status inventory; destructive topology path remains unchanged')


def c37():
    """Post-remove named refs are freshly resolved before confirmed success."""
    old = ownership_base()
    observations = []
    for label in ('named', 'detached', 'branch_lost_after_remove'):
        decisions = []
        for module in (old, ds):
            with fixture() as f:
                ownership_indexes(f); repo, path = f.worktree('TASK-1')
                branch = 'refs/heads/task/TASK-1'
                head = f.git(repo, 'rev-parse', branch).strip()
                if label == 'detached': f.git(path, 'checkout', '--detach', '-q', head)
                real_git = module.git
                removed = []
                def git(root, deadline, *args):
                    answer = real_git(root, deadline, *args)
                    if args[:2] == ('worktree', 'remove'):
                        removed.append(str(path))
                        if label == 'branch_lost_after_remove':
                            f.git(repo, 'update-ref', '-d', branch)
                    return answer
                with patch.object(module, 'git', git):
                    report = module.sweep_org(f.org, time.monotonic() + 20, mode='on', now=NOW)
                target = row(report, path)
                assert removed == [str(path)] and not path.exists(), (label, removed, target)
                assert str(path) not in module.registrations(repo, time.monotonic() + 5)
                if label == 'branch_lost_after_remove' and module is ds:
                    assert target['state'] == 'partial_or_unknown', target
                    assert target['reason'].startswith('git_failed:'), target
                    assert report['phases']['worktree']['attribution_unknown'], report
                    assert not report['coverage']['complete'] and report['outcome'] == 'error', report
                    assert 'removed_attributable_bytes' not in target, target
                else:
                    assert target['state'] == 'removed' and report['coverage']['complete'], report
                    if label != 'branch_lost_after_remove':
                        assert f.git(repo, 'rev-parse', '--verify', branch + '^{commit}').strip() == head
                decisions.append(ownership_decisions(report, f.base))
        if label != 'branch_lost_after_remove':
            assert decisions[0] == decisions[1], (label, decisions)
        else:
            assert decisions[0]['candidates'][0]['state'] == 'removed', decisions
        observations.append(dict(variant=label, base=decisions[0], new=decisions[1]))
    return dict(variants=observations, equivalence_differences=[
        'branch_lost_after_remove: base records removed; new records partial_or_unknown and incomplete, as required by section5.2 post-removal branch proof'])


def c38():
    """Git identities and current preserving refs cannot reuse inventory clearance."""
    observations, failures = [], []
    old = ownership_base()
    expected = dict(head_moved='git_identity_changed_since_inventory',
                    branch_moved='git_identity_changed_since_inventory',
                    new_unpreserved_commit='unique_head_commit',
                    last_preserving_ref_lost='unique_head_commit',
                    entry_identity='unknown_git_registrations:',
                    pointer_identity='unknown_git_registrations:')
    for boundary in ('keep_inventory', 'eligible_checkpoint'):
        for label, reason in expected.items():
            for module in (old, ds):
                name = ('base/' if module is old else 'new/') + boundary + '/' + label
                try:
                    with fixture() as f:
                        ownership_indexes(f); repo, path = f.worktree('TASK-1')
                        branch = 'refs/heads/task/TASK-1'
                        head = f.git(repo, 'rev-parse', branch).strip()
                        tree = f.git(repo, 'rev-parse', head + '^{tree}').strip()
                        changed_head = f.git(repo, 'commit-tree', tree, '-p', head, '-m', 'synthetic racing commit').strip()
                        if label == 'last_preserving_ref_lost':
                            f.git(repo, 'update-ref', 'refs/heads/fixture-preserve', changed_head)
                            f.git(path, 'checkout', '--detach', '-q', changed_head)
                        entry = repo / '.git/worktrees/TASK-1'
                        held = f.base / 'held-original-metadata'
                        events, removes, result = [], [], {}
                        def change():
                            if label == 'head_moved': f.git(repo, 'update-ref', branch, changed_head)
                            elif label == 'branch_moved': f.git(path, 'checkout', '--detach', '-q', head)
                            elif label == 'new_unpreserved_commit': f.git(path, 'checkout', '--detach', '-q', changed_head)
                            elif label == 'last_preserving_ref_lost': f.git(repo, 'update-ref', '-d', 'refs/heads/fixture-preserve')
                            elif label == 'entry_identity':
                                entry.rename(held); shutil.copytree(held, entry)
                            elif label == 'pointer_identity':
                                pointer = entry / 'gitdir'; pointer.rename(held); pointer.write_bytes(held.read_bytes())
                            events.append('changed_after_' + boundary)
                        def heal():
                            if label == 'head_moved': f.git(repo, 'update-ref', branch, head)
                            elif label in ('branch_moved', 'new_unpreserved_commit'):
                                f.git(path, 'symbolic-ref', 'HEAD', branch)
                            elif label == 'last_preserving_ref_lost':
                                f.git(repo, 'update-ref', 'refs/heads/fixture-preserve', changed_head)
                            elif label == 'entry_identity':
                                shutil.rmtree(entry); held.rename(entry)
                            elif label == 'pointer_identity':
                                (entry / 'gitdir').unlink(); held.rename(entry / 'gitdir')
                            events.append('healed_after_refusal')
                        original_inventory, original_git = getattr(module, 'worktree_inventory', None), module.git
                        original_admit = module.admit
                        def inventory(*args, **kwargs):
                            refusal = original_inventory(*args, **kwargs)
                            assert refusal is None, (label, refusal)
                            if boundary == 'keep_inventory': change()
                            return refusal
                        def admit(*args, **kwargs):
                            if module is old and boundary == 'keep_inventory' and not events: change()
                            return original_admit(*args, **kwargs)
                        def checkpoint():
                            target = next((c for c in result.get('candidates', []) if c['path'] == str(path)), None)
                            if not target: return
                            if boundary == 'eligible_checkpoint' and target['state'] == 'eligible' and not events: change()
                            if target['state'] in ('retained', 'partial_or_unknown') and len(events) == 1:
                                if module is ds: assert reason in target['reason'], (label, target)
                                heal()
                        def git(root, deadline, *args):
                            if args[:2] == ('worktree', 'remove'):
                                removes.append(args)
                                raise module.Retain('fixture_observed_prohibited_mutation')
                            return original_git(root, deadline, *args)
                        with patch.object(module, 'worktree_inventory', inventory, create=True), patch.object(module, 'git', git), patch.object(module, 'admit', admit):
                            report = module.sweep_org(f.org, time.monotonic() + 20, mode='on', now=NOW,
                                                  result=result, checkpoint=checkpoint)
                        target = row(report, path)
                        if module is ds:
                            retained(report, path, reason)
                            assert not removes, removes
                        assert events == ['changed_after_' + boundary, 'healed_after_refusal'], events
                        assert (path / 'source').read_text() == 'committed\n'
                        assert f.git(repo, 'rev-parse', '--verify', branch).strip() == head
                        # Real healed current state passes; the completed decision
                        # remains retained and no same-night retry/removal occurred.
                        deadline = time.monotonic() + 10
                        module.admit(dict(phase='worktree', path=str(path), workspace=str(f.workspace), repo=str(repo)),
                                 module.Evidence(f.org, deadline), deadline, now=NOW)
                        assert row(report, path)['state'] == target['state']
                        observations.append(dict(variant=name, result='PASS', state=target['state'], reason=target['reason'].replace(str(f.base), '<fixture>'), actions=removes, events=events, coverage=report['coverage'], outcome=report['outcome']))
                except Exception as exc:
                    failures.append(dict(variant=name, error=str(exc)[:2000]))
    assert not failures, dict(passed=observations, failures=failures)
    return dict(base=OWNERSHIP_BASE, variants=observations, limits='Pinned base keep boundary is after discovery, new boundary after its keep inventory; eligible boundary identical. Prohibited base removal attempts intercepted; no policy mock.')



def c39():
    """Literal primary proof saves Git calls without changing removal policy."""
    old = ownership_base()
    observations = []
    for detached in (False, True):
        decisions, counts = [], []
        for module in (old, ds):
            with fixture() as f:
                ownership_indexes(f); repo, path = f.worktree('TASK-1')
                branch = 'refs/heads/task/TASK-1'
                head = f.git(repo, 'rev-parse', branch).strip()
                if detached: f.git(path, 'checkout', '--detach', '-q', head)
                calls = []
                original_git = module.git
                def git(root, deadline, *args):
                    calls.append((str(root), args))
                    return original_git(root, deadline, *args)
                with patch.object(module, 'git', git):
                    report = module.sweep_org(f.org, time.monotonic() + 20, mode='on', now=NOW)
                assert row(report, path)['state'] == 'removed' and report['coverage']['complete'], report
                assert not path.exists() and str(path) not in f.git(repo, 'worktree', 'list', '--porcelain')
                assert f.git(repo, 'rev-parse', '--verify', branch + '^{commit}').strip() == head
                common_args = ('rev-parse', '--path-format=absolute', '--git-common-dir')
                primary = sum(root == str(repo) and args == common_args for root, args in calls)
                candidate = sum(root == str(path) and args == common_args for root, args in calls)
                assert primary == (6 if module is old else 0) and candidate == 6, (primary, candidate, calls)
                decisions.append(ownership_decisions(report, f.base))
                counts.append(dict(primary_common_dir=primary, candidate_common_dir=candidate))
        assert decisions[0] == decisions[1], decisions
        observations.append(dict(detached=detached, decisions=decisions, calls=counts))
    return dict(variants=observations, equivalence_differences=[])


def c40():
    """Recursive safety is fresh after inventory; a healed refusal stays kept."""
    old = ownership_base()
    observations, failures = [], []
    for phase in ('scratch', 'worktree'):
        variants = ('outside_hardlink', 'recent_descendant', 'recent_root')
        if phase == 'worktree': variants += ('nested_file', 'nested_dir', 'nested_live', 'nested_dangling')
        for boundary in ('base_equivalence', 'keep_inventory', 'eligible_checkpoint'):
            for label in variants:
                for module in ((ds,) if boundary == 'base_equivalence' else (old, ds)):
                    name = ('base/' if module is old else 'new/') + phase + '/' + boundary + '/' + label
                    try:
                        with fixture() as f:
                            ownership_indexes(f)
                            if phase == 'scratch':
                                path = f.scratch('TASK-1'); repo = None
                                payload = path / 'nested/payload'
                            else:
                                repo, path = f.worktree('TASK-1')
                                payload = path / 'ignored/a/deep/payload'
                                payload.parent.mkdir(parents=True); payload.write_bytes(b'preserved payload')
                            link = f.base / 'outside-link'
                            nested = path / 'ignored/a/deep/.git'
                            timed = path if label == 'recent_root' else payload
                            original_times = timed.stat()
                            before = candidate_contents(path)
                            refs = f.git(repo, 'show-ref') if repo else None
                            expected = ('possible_nested_worktree:' + str(nested) if label.startswith('nested_') else
                                        'unknown_external_hardlinks' if label == 'outside_hardlink' else 'changed_within_hour')
                            events, actions, result = [], [], {}
                            def change():
                                if label.startswith('nested_'): nested_input(f, repo, path, label.removeprefix('nested_'))
                                elif label == 'outside_hardlink': os.link(payload, link)
                                else: os.utime(timed, (NOW, NOW))
                                events.append('changed_after_' + boundary)
                            def heal():
                                if label == 'nested_dir': shutil.rmtree(nested)
                                elif label.startswith('nested_'): nested.unlink()
                                elif label == 'outside_hardlink': link.unlink()
                                else: os.utime(timed, ns=(original_times.st_atime_ns, original_times.st_mtime_ns))
                                events.append('healed_after_refusal')
                            if boundary == 'base_equivalence':
                                change()
                                reports = [module.sweep_org(f.org, time.monotonic() + 20, mode='dry_run', now=NOW)
                                           for module in (old, ds)]
                                decisions = [ownership_decisions(report, f.base) for report in reports]
                                assert decisions[0] == decisions[1], (name, decisions)
                                for report in reports: retained(report, path, expected)
                                heal()
                            else:
                                original_inventory = getattr(module.Evidence, 'inventory', None)
                                original_worktree = getattr(module, 'worktree_inventory', None)
                                original_admit = module.admit
                                original_git, original_mutate = module.git, module.mutate
                                def inventory(self, candidates):
                                    refused = original_inventory(self, candidates)
                                    if phase == 'scratch' and boundary == 'keep_inventory':
                                        assert str(path) not in refused, refused
                                        change()
                                    return refused
                                def worktree_inventory(*args, **kwargs):
                                    refusal = original_worktree(*args, **kwargs)
                                    assert refusal is None, refusal
                                    if boundary == 'keep_inventory': change()
                                    return refusal
                                def admit(*args, **kwargs):
                                    if module is old and boundary == 'keep_inventory' and not events: change()
                                    return original_admit(*args, **kwargs)
                                def checkpoint():
                                    target = next((c for c in result.get('candidates', []) if c['path'] == str(path)), None)
                                    if target is None: return
                                    if boundary == 'eligible_checkpoint' and target['state'] == 'eligible' and not events: change()
                                    if target['state'] == 'retained' and len(events) == 1:
                                        assert target.get('reason') == expected, (name, target)
                                        heal()
                                def git(root, deadline, *args):
                                    if args[:2] == ('worktree', 'remove'): actions.append('git remove')
                                    return original_git(root, deadline, *args)
                                def mutate(*args, **kwargs):
                                    actions.append('filesystem mutation')
                                    return original_mutate(*args, **kwargs)
                                with patch.object(module.Evidence, 'inventory', inventory, create=True), patch.object(module, 'worktree_inventory', worktree_inventory, create=True), \
                                        patch.object(module, 'git', git), patch.object(module, 'mutate', mutate), patch.object(module, 'admit', admit):
                                    report = module.sweep_org(f.org, time.monotonic() + 20, mode='on', now=NOW,
                                                          result=result, checkpoint=checkpoint)
                                retained(report, path, expected)
                                assert events == ['changed_after_' + boundary, 'healed_after_refusal'], (name, events)
                                assert actions == [], (name, actions)
                                deadline = time.monotonic() + 10
                                candidate = dict(phase=phase, path=str(path), workspace=str(f.workspace))
                                if repo: candidate['repo'] = str(repo)
                                module.admit(candidate, module.Evidence(f.org, deadline), deadline, now=NOW)
                                assert row(report, path)['state'] == 'retained'
                            assert candidate_contents(path) == before, name
                            if repo: assert f.git(repo, 'show-ref') == refs
                        observations.append(dict(variant=name, result='PASS', reason=expected, events=events))
                    except Exception as exc:
                        failures.append(dict(variant=name, error=type(exc).__name__ + ': ' + str(exc)[:2000]))
    assert not failures, dict(passed=observations, failures=failures)
    return dict(variants=observations, equivalence_differences=[],
                limits='Real disposable files/links and synthetic age clock; no mount, Darwin or full cheaper-census proof')



def c41():
    """Candidate census saves sibling reads, never topology or own-file proof."""
    old = ownership_base()
    observations, failures = [], []
    for label in ('clean', 'unreadable_index', 'missing_head', 'malformed_head',
                  'index_hardlink', 'admin_alias', 'conflicting_backlink', 'duplicate_target'):
        with fixture() as f, ExitStack() as damage:
            ownership_indexes(f)
            repo, path = f.worktree('TASK-1'); _, peer = f.worktree('TASK-2')
            if label != 'clean': damage.enter_context(damaged_registration(f, repo, peer, label))
            before = candidate_contents(path, peer)
            refs = f.git(repo, 'show-ref')
            reports = [module.sweep_org(f.org, time.monotonic() + 20, mode='dry_run', now=NOW)
                       for module in (old, ds)]
            decisions = [ownership_decisions(report, f.base) for report in reports]
            assert decisions[0] == decisions[1], (label, decisions)
            if label in ('clean', 'unreadable_index', 'missing_head'):
                assert row(reports[1], path)['state'] == 'eligible', (label, reports[1])
            if label == 'clean': assert row(reports[1], peer)['state'] == 'eligible'
            assert candidate_contents(path, peer) == before and f.git(repo, 'show-ref') == refs
            observations.append(dict(variant='same_input/' + label, decisions=decisions, result='PASS'))

    decisions, counts = [], []
    for module in (old, ds):
        with fixture() as f:
            ownership_indexes(f)
            repo, path = f.worktree('TASK-1'); _, peer = f.worktree('TASK-2')
            f.db.execute("UPDATE tasks SET status='in_progress' WHERE id='TASK-2'"); f.db.commit()
            admin = repo / '.git/worktrees'
            for target in (path, peer):
                (admin / target.name / 'fixture-note').write_text('ordinary auxiliary metadata\n')
            before, refs = candidate_contents(peer), f.git(repo, 'show-ref')
            reads = []
            original_file = module._metadata_file
            def metadata_file(target, deadline, **kwargs):
                reads.append(str(target.relative_to(admin)) if admin in target.parents else str(target))
                return original_file(target, deadline, **kwargs)
            with patch.object(module, '_metadata_file', metadata_file):
                report = module.sweep_org(f.org, time.monotonic() + 20, mode='on', now=NOW)
            assert row(report, path)['state'] == 'removed' and not path.exists(), report
            retained(report, peer, 'task_or_root_not_terminal:in_progress')
            assert candidate_contents(peer) == before and f.git(repo, 'show-ref') == refs
            actual = {name: reads.count(name) for name in
                      ('TASK-1/index', 'TASK-1/fixture-note', 'TASK-2/index', 'TASK-2/fixture-note',
                       'TASK-2/gitdir', 'TASK-2/commondir', 'TASK-2/HEAD')}
            # Independent literal cost oracles. Own files still read at every
            # live-candidate census; all peer pointers and HEAD stay current.
            assert actual['TASK-1/index'] == actual['TASK-1/fixture-note'] == 24, actual
            assert actual['TASK-2/index'] == actual['TASK-2/fixture-note'] == (27 if module is old else 3), actual
            assert actual['TASK-2/gitdir'] == actual['TASK-2/commondir'] == 54, actual
            assert actual['TASK-2/HEAD'] == 27, actual
            decisions.append(ownership_decisions(report, f.base)); counts.append(actual)
    assert decisions[0] == decisions[1], decisions
    observations.append(dict(variant='on_mode_sibling_read_cost', decisions=decisions, reads=counts, result='PASS'))

    for label in ('own_auxiliary_change', 'sibling_auxiliary_change', 'sibling_unsafe_auxiliary'):
        for module in (old, ds):
            try:
                with fixture() as f:
                    ownership_indexes(f)
                    repo, path = f.worktree('TASK-1'); _, peer = f.worktree('TASK-2')
                    f.db.execute("UPDATE tasks SET status='in_progress' WHERE id='TASK-2'"); f.db.commit()
                    admin = repo / '.git/worktrees'
                    note = admin / (path.name if label == 'own_auxiliary_change' else peer.name) / 'fixture-note'
                    note.write_text('original ordinary metadata\n'); original = note.read_bytes()
                    before = candidate_contents(path, peer); refs = f.git(repo, 'show-ref')
                    events, removes, result = [], [], {}
                    original_snapshot, original_metadata, original_git = module.snapshot, module._registration_metadata, module.git
                    selected, ready = False, False
                    original_discover = module.discover
                    def discover(*args, **kwargs):
                        nonlocal ready
                        value = original_discover(*args, **kwargs); ready = True
                        return value
                    def metadata(root, deadline, **kwargs):
                        nonlocal selected
                        previous, selected = selected, ready and (module is old or kwargs.get('metadata_path') == path)
                        try: return original_metadata(root, deadline, **kwargs)
                        finally: selected = previous
                    def snapshot(root, deadline, **kwargs):
                        value = original_snapshot(root, deadline, **kwargs)
                        if Path(root) == admin and selected and not events:
                            if label == 'sibling_unsafe_auxiliary':
                                note.unlink(); note.symlink_to(f.base / 'unread-target')
                            else: note.write_bytes(original + b'racing write\n')
                            events.append('changed_after_fresh_stat')
                        return value
                    def checkpoint():
                        target = next((c for c in result.get('candidates', []) if c['path'] == str(path)), None)
                        if target and target['state'] == 'retained' and len(events) == 1:
                            if note.is_symlink(): note.unlink()
                            note.write_bytes(original); events.append('healed_after_refusal')
                    def git(root, deadline, *args):
                        if args[:2] == ('worktree', 'remove'): removes.append(args[2])
                        return original_git(root, deadline, *args)
                    with patch.object(module, '_registration_metadata', metadata), patch.object(module, 'snapshot', snapshot), \
                            patch.object(module, 'git', git), patch.object(module, 'discover', discover):
                        report = module.sweep_org(f.org, time.monotonic() + 20, mode='on', now=NOW,
                                              result=result, checkpoint=checkpoint)
                    if label == 'sibling_auxiliary_change':
                        assert events == ['changed_after_fresh_stat'], events
                        assert row(report, path)['state'] == 'removed' and removes == [str(path)], report
                        assert any(scope['path'] == str(peer) and 'metadata_changed:' + str(note) in scope['reason']
                                   for scope in report['discovery_scopes']), report
                        # Successful unrelated removal never repairs a sibling.
                        assert note.read_bytes() == original + b'racing write\n'
                        note.write_bytes(original)
                    else:
                        retained(report, path, ('metadata_changed:' if label == 'own_auxiliary_change' else
                                                'unknown_git_metadata:' if module is old else 'aliased_or_special_metadata:') + str(note))
                        assert events == ['changed_after_fresh_stat', 'healed_after_refusal'] and not removes, (events, removes)
                        if label == 'own_auxiliary_change':
                            assert 'snapshot_to_read' in row(report, path)['reason'], report
                        else:
                            assert report['discovery'] == 'partial' and not report['coverage']['complete'], report
                        assert candidate_contents(path, peer) == before
                        deadline = time.monotonic() + 10
                        module.admit(dict(phase='worktree', path=str(path), workspace=str(f.workspace), repo=str(repo)),
                                 module.Evidence(f.org, deadline), deadline, now=NOW)
                        assert row(report, path)['state'] == 'retained'
                    assert candidate_contents(peer) == {k: v for k, v in before.items() if k.startswith(str(peer))}
                    assert f.git(repo, 'show-ref') == refs
                    observations.append(dict(variant=('base/' if module is old else 'new/') + label, events=events, result='PASS', target=row(report, path)['state'], reason=row(report, path).get('reason', '').replace(str(f.base), '<fixture>')))
            except AssertionError as exc:
                failures.append(dict(variant=('base/' if module is old else 'new/') + label, error=str(exc)[:2000]))
    assert not failures, dict(passed=observations, failures=failures)
    return dict(variants=observations, equivalence_differences=[],
                limits='Pointers, HEAD and all stat censuses retained; full Step3 topology/native/mount race proof unfinished')


def c42(*, native: bool = False) -> dict:
    """Current ownership after the last scan/comparison/remove/repair census."""
    variants = {
        'task_reopened': ("UPDATE tasks SET status='in_progress' WHERE id='TASK-2'", "UPDATE tasks SET status='completed' WHERE id='TASK-2'", 'task_or_root_not_terminal:in_progress'),
        'root_reopened': ("UPDATE tasks SET status='in_progress' WHERE id='TASK-1'", "UPDATE tasks SET status='completed' WHERE id='TASK-1'", 'task_or_root_not_terminal:in_progress'),
        'root_reparented': ("UPDATE tasks SET parent_task_id='TASK-4' WHERE id='TASK-1'", "UPDATE tasks SET parent_task_id=NULL WHERE id='TASK-1'", 'task_or_root_not_terminal:in_progress'),
        'new_sibling_job': ("INSERT INTO jobs VALUES('TASK-3','pending')", "DELETE FROM jobs WHERE status='pending'", 'active_job:pending'),
        'job_status_unknown': ("UPDATE jobs SET status='unrecognised'", "UPDATE jobs SET status='completed'", 'unknown_job_status'),
        'job_ancestry_unknown': ("INSERT INTO jobs VALUES('missing','running')", "DELETE FROM jobs WHERE status='running'", 'unknown_missing_or_cyclic_lineage'),
    }
    if native:
        variants = {label: (None, None, reason) for label, reason in (
            ('writer_settled', 'team_move_cleanup_owner_unavailable'),
            ('writer_unknown', 'team_move_cleanup_owner_unavailable'),
            ('writer_replaced', 'team_move_cleanup_owner_unavailable'),
            ('native_hold', 'team_move_native_held'))}

    def native_state(f: Fixture, label: str) -> tuple:
        # Canonical shipping schema and actual persistent native readers/writers.
        # All identities/admission below are synthetic validator data; no route,
        # runtime owner, service or live org is invoked or authorized.
        import hashlib
        import threading
        import runtime.infrastructure.database  # noqa: F401
        from runtime.infrastructure.db.audit import AuditMixin
        from runtime.infrastructure.producer_admission import NativeWriterLifecycle, NativeWriterStore
        from runtime.infrastructure.team_move_store import (TEAM_MOVE_SCHEMA_SQL, Diagnostic, FounderAdmission,
                                                            Phase, TeamMoveStore, validate_team_move_schema)
        f.db.row_factory = sqlite3.Row
        f.db.execute(TEAM_MOVE_SCHEMA_SQL); f.db.commit()
        class Audit(AuditMixin):
            def __init__(self) -> None:
                self._conn, self._lock = f.db, threading.RLock()
        db = Audit()
        store = NativeWriterStore(db, f.org.name)
        initial = NativeWriterLifecycle(org_slug=f.org.name, writer_id='synthetic-disk-sweep', publisher='disk_sweep')
        store.save_uncommitted(initial); f.db.commit()
        saved_audit = [tuple(row) for row in f.db.execute("SELECT * FROM audit_log WHERE action='team_move_native_writer' ORDER BY id")]
        hold = {}
        def change() -> None:
            if label == 'native_hold':
                z = '0' * 64
                expected = {n + '_digest': z for n in ('canonical', 'roster', 'naming', 'profile', 'dependency', 'policy', 'skill_selection', 'permission')}
                expected.update(authority_namespace='synthetic', authority_generation=1, authority_digest=z, agent_revisions=[z, z])
                identity = dict(device=0, inode=1, mode=448)
                empty_digest = hashlib.sha256(b'').hexdigest()
                request = dict(version=1, org_slug=f.org.name, source_team='consultant', target_team='default',
                    target_principal='founder', affected_agents=['consultant_codex', 'consultant_head'], demote='consultant_head',
                    retire_source=True, drain_threads=False, expected=expected,
                    files=[dict(path=p, identity=identity, before_base64='', before_digest=empty_digest,
                                after_base64='', after_digest=empty_digest) for p in
                           ('org/agents/consultant_codex.md', 'org/agents/consultant_head.md', 'org/teams.yaml')],
                    workspaces=[dict(agent_id=a, path=str(f.base / 'synthetic-workspaces' / a), identity=identity)
                                for a in ('consultant_codex', 'consultant_head')])
                hold.update(TeamMoveStore(db, f.org.name).reserve(operation_key='synthetic-only',
                    request_bytes=json.dumps(request, sort_keys=True, separators=(',', ':')).encode(),
                    admission=FounderAdmission(principal='founder', boundary='_require_human', org_slug=f.org.name,
                                               admission_id='synthetic-validator-data', admitted_at='2026-11-13T00:00:00+00:00')))
            elif label == 'writer_replaced':
                f.db.execute("DELETE FROM audit_log WHERE action='team_move_native_writer'")
                store.save_uncommitted(initial.model_copy(update={'writer_id': 'synthetic-replacement'})); f.db.commit()
            else:
                store.save_uncommitted(initial.model_copy(update={'state': 'settled' if label == 'writer_settled' else 'unknown'}))
                f.db.commit()
            assert validate_team_move_schema(f.db, org_slug=f.org.name)
            current = store.history_uncommitted()
            if label == 'writer_replaced': assert initial.writer_id not in current and 'synthetic-replacement' in current
            elif label != 'native_hold': assert current[initial.writer_id].state == ('settled' if label == 'writer_settled' else 'unknown')
        def heal() -> None:
            if label == 'native_hold':
                TeamMoveStore(db, f.org.name).transition(hold['operation_id'], expected_revision=1,
                    phase=Phase.ABANDONED, diagnostic=Diagnostic(code='synthetic-release'))
            else:
                # Fixture-only snapshot restoration. An ended native writer
                # cannot reopen; this grants no production settlement/recovery.
                f.db.execute("DELETE FROM audit_log WHERE action='team_move_native_writer'")
                f.db.executemany('INSERT INTO audit_log(id,task_id,agent,action,payload,timestamp) VALUES(?,?,?,?,?,?)', saved_audit)
                f.db.commit()
                assert [tuple(row) for row in f.db.execute("SELECT * FROM audit_log WHERE action='team_move_native_writer' ORDER BY id")] == saved_audit
            assert validate_team_move_schema(f.db, org_slug=f.org.name)
            assert store.history_uncommitted()[initial.writer_id] == initial
        return initial.writer_id, change, heal

    old = ownership_base()
    observations, failures = [], []
    for phase in ('scratch', 'worktree'):
        boundaries = ('last_snapshot', 'final_comparison')
        if phase == 'worktree': boundaries += ('remove_census', 'repair_census', 'retry_census')
        for boundary in boundaries:
            for label, (sql, heal, expected) in variants.items():
                name = phase + '/' + boundary + '/' + label
                try:
                    decisions = []
                    for module in (old, ds):
                        with fixture() as f:
                            ownership_indexes(f)
                            f.task('TASK-1'); f.task('TASK-3', parent='TASK-1'); f.task('TASK-4', status='in_progress')
                            if phase == 'scratch':
                                path = f.scratch('TASK-2', parent='TASK-1'); repo = None
                            else:
                                repo, path = f.worktree('TASK-2')
                                f.db.execute("UPDATE tasks SET parent_task_id='TASK-1' WHERE id='TASK-2'")
                            f.db.execute("INSERT INTO jobs VALUES('TASK-3','completed')"); f.db.commit()
                            writer_id, change_native, heal_native = native_state(f, label) if native else (None, None, None)
                            before = candidate_contents(path)
                            refs = f.git(repo, 'show-ref') if repo else None
                            events, actions, result = [], [], {}
                            counts = dict(snapshots=0, comparisons=0, admissions=0, removes=0)
                            original_snapshot, original_equal = module.snapshot, module.trees_equal
                            original_admit, original_rows = module.admit, module.registrations
                            original_git, original_mutate = module.git, module.mutate
                            def change():
                                if native: change_native()
                                else: f.db.execute(sql); f.db.commit()
                                events.append('changed_after_' + boundary)
                            def snapshot(root, deadline, **kwargs):
                                tree = original_snapshot(root, deadline, **kwargs)
                                if Path(root) == path:
                                    counts['snapshots'] += 1
                                    if boundary == 'last_snapshot' and counts['snapshots'] == 4: change()
                                return tree
                            def equal(*args):
                                same = original_equal(*args)
                                counts['comparisons'] += 1
                                if boundary == 'final_comparison' and counts['comparisons'] == 3: change()
                                return same
                            def admit(*args, **kwargs):
                                tree = original_admit(*args, **kwargs)
                                counts['admissions'] += 1
                                return tree
                            def registrations(*args, **kwargs):
                                rows = original_rows(*args, **kwargs)
                                trigger = dict(remove_census=2, repair_census=3, retry_census=4).get(boundary)
                                if trigger is not None and counts['admissions'] == trigger and not events: change()
                                return rows
                            def git(root, deadline, *args):
                                if args[:2] == ('worktree', 'remove'):
                                    counts['removes'] += 1
                                    actions.append('git_remove')
                                    if boundary in ('repair_census', 'retry_census') and counts['removes'] == 1:
                                        # Existing error path, no policy substitute: the
                                        # first command has no filesystem effect.
                                        raise module.Retain('synthetic no-effect first Git refusal')
                                return original_git(root, deadline, *args)
                            def mutate(*args, **kwargs):
                                actions.append('scratch_remove' if kwargs.get('remove') else 'permission_repair')
                                return original_mutate(*args, **kwargs)
                            def checkpoint():
                                target = next((c for c in result.get('candidates', []) if c['path'] == str(path)), None)
                                if (module is ds and target and target['state'] in ('retained', 'partial_or_unknown')
                                        and len(events) == 1):
                                    assert target.get('reason') == expected, (name, target)
                                    # Heal the real synthetic row. A new current read
                                    # passes, but the night's decision cannot retry.
                                    if native: heal_native()
                                    else: f.db.execute(heal); f.db.commit()
                                    module.Evidence(f.org, time.monotonic() + 5, writer_id=writer_id, writing=native).ownership(path, f.workspace, path.name)
                                    events.append('healed_after_refusal')
                            with patch.object(module, 'snapshot', snapshot), patch.object(module, 'trees_equal', equal), \
                                    patch.object(module, 'admit', admit), patch.object(module, 'registrations', registrations), \
                                    patch.object(module, 'git', git), patch.object(module, 'mutate', mutate):
                                report = module.sweep_org(f.org, time.monotonic() + 20, mode='on', now=NOW,
                                                          result=result, checkpoint=checkpoint, writer_id=writer_id)
                            target = row(report, path)
                            if module is old:
                                if boundary == 'repair_census':
                                    assert target['state'] == 'partial_or_unknown' and path.exists(), (name, target)
                                    assert actions == ['git_remove', 'permission_repair'], (name, actions)
                                else:
                                    assert target['state'] == 'removed' and not path.exists(), (name, target, actions, events)
                            else:
                                prior_effect = boundary in ('repair_census', 'retry_census')
                                assert target['state'] == ('partial_or_unknown' if prior_effect else 'retained'), (name, target)
                                assert target['reason'] == expected, (name, target)
                                assert events == ['changed_after_' + boundary, 'healed_after_refusal'], (name, events)
                                expected_actions = (['git_remove', 'permission_repair'] if boundary == 'retry_census' else
                                                    ['git_remove'] if boundary == 'repair_census' else [])
                                assert actions == expected_actions, (name, actions)
                                assert candidate_contents(path) == before, name
                                if repo: assert f.git(repo, 'show-ref') == refs
                                assert 'removed_attributable_bytes' not in target, target
                                if prior_effect:
                                    assert report['outcome'] == 'error' and not report['coverage']['complete'], report
                                deadline = time.monotonic() + 10
                                candidate = dict(phase=phase, path=str(path), workspace=str(f.workspace))
                                if repo: candidate['repo'] = str(repo)
                                module.admit(candidate, module.Evidence(f.org, deadline, writer_id=writer_id, writing=native), deadline, now=NOW)
                                assert row(report, path)['state'] == target['state']
                            decisions.append(dict(state=target['state'], reason=target.get('reason'), actions=actions, events=events))
                    observations.append(dict(variant=name, result='PASS', base=decisions[0], new=decisions[1]))
                except AssertionError as exc:
                    failures.append(dict(variant=name, error=str(exc)[:2000]))
    assert not failures, dict(passed=observations, failures=failures)
    return dict(variants=observations, equivalence_differences=[
        ('Late native writer/hold races: ' if native else 'Late task/job ownership races: ') +
        'base removes, or attempts permission repair at repair_census; current refuses after last observations. '
        'Before any remove it is retained; after first attempted Git remove it remains partial_or_unknown under existing classification.'],
        limits='Actual disposable SQLite/Git/filesystem/native records; synthetic no-effect first Git refusal only for repair/retry entry. Native audit snapshot restoration is fixture-only, never reopening authority.')


def c43():
    """Cheap recent roots keep before guard/dirt/full admission; age never clears."""
    old = ownership_base()
    observations, differences, failures = [], [], []
    for phase in ('scratch', 'worktree'):
        for variant in ('recent', 'active_recent', 'healed_recent', 'recent_dirty', 'recent_guard'):
            if phase == 'scratch' and variant in ('recent_dirty', 'recent_guard'): continue
            name = phase + '/' + variant
            try:
                decisions = []
                for module in (old, ds):
                    with fixture() as f:
                        ownership_indexes(f)
                        if phase == 'scratch': path, repo = f.scratch('TASK-1'), None
                        else: repo, path = f.worktree('TASK-1')
                        if variant == 'active_recent':
                            f.db.execute("UPDATE tasks SET status='in_progress'"); f.db.commit()
                        if variant == 'recent_dirty': (path / 'source').write_text('dirty content remains\n')
                        if variant == 'recent_guard':
                            guard = path / '.worktree-guard.json'
                            value = json.loads(guard.read_text()); value['task_id'] = 'TASK-other'
                            guard.write_text(json.dumps(value))
                        stamp = path.stat()
                        os.utime(path, (NOW, NOW))
                        before = candidate_contents(path)
                        refs = f.git(repo, 'show-ref') if repo else None
                        events, actions, counts, result = [], [], dict(admit=0, snapshot=0, git_status=0), {}
                        ready = False
                        original_directory, original_discover = module.directory, module.discover
                        original_admit, original_snapshot = module.admit, module.snapshot
                        original_git, original_mutate = module.git, module.mutate
                        def discover(*args, **kwargs):
                            nonlocal ready
                            value = original_discover(*args, **kwargs); ready = True
                            return value
                        @contextmanager
                        def directory(root, deadline):
                            with original_directory(root, deadline) as fd:
                                yield fd
                                if ready and Path(root) == path and variant == 'healed_recent' and not events:
                                    # Heal a real mtime AFTER the root observation.
                                    # The earlier failed age answer remains final.
                                    os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
                                    events.append('healed_after_root_observation')
                        def admit(*args, **kwargs):
                            counts['admit'] += 1; return original_admit(*args, **kwargs)
                        def snapshot(*args, **kwargs):
                            if ready: counts['snapshot'] += 1
                            return original_snapshot(*args, **kwargs)
                        def git(root, deadline, *args):
                            if args[0] == 'status': counts['git_status'] += 1
                            if args[:2] == ('worktree', 'remove'): actions.append('git_remove')
                            return original_git(root, deadline, *args)
                        def mutate(*args, **kwargs):
                            actions.append('tree_remove' if kwargs.get('remove') else 'repair')
                            return original_mutate(*args, **kwargs)
                        with patch.object(module, 'directory', directory), patch.object(module, 'discover', discover), \
                                patch.object(module, 'admit', admit), patch.object(module, 'snapshot', snapshot), \
                                patch.object(module, 'git', git), patch.object(module, 'mutate', mutate), \
                                patch.dict(original_snapshot.__globals__, directory=directory):
                            report = module.sweep_org(f.org, time.monotonic() + 20, mode='on', now=NOW, result=result)
                        target = row(report, path)
                        expected = ('task_or_root_not_terminal:in_progress' if variant == 'active_recent' else
                                    'dirty_or_untracked' if module is old and variant == 'recent_dirty' else
                                    'unknown_worktree_owner' if module is old and variant == 'recent_guard' else
                                    'changed_within_hour')
                        if variant == 'healed_recent': assert events == ['healed_after_root_observation'], (name, events)
                        if module is ds or variant != 'healed_recent' or phase == 'scratch':
                            retained(report, path, expected)
                            assert not actions and candidate_contents(path) == before, (name, actions)
                            if repo: assert f.git(repo, 'show-ref') == refs
                            complete = not (module is old and variant == 'recent_guard')
                            assert report['outcome'] == ('complete' if complete else 'incomplete') and report['coverage']['complete'] is complete, report
                        if module is ds:
                            assert counts == dict(admit=0, snapshot=0, git_status=0), (name, counts)
                            if variant == 'healed_recent':
                                assert events == ['healed_after_root_observation'], events
                                deadline = time.monotonic() + 5
                                candidate = dict(phase=phase, path=str(path), workspace=str(f.workspace))
                                if repo: candidate['repo'] = str(repo)
                                module.admit(candidate, module.Evidence(f.org, deadline), deadline, now=NOW)
                                assert row(report, path)['state'] == 'retained' and not actions
                        decisions.append(dict(state=target['state'], reason=target.get('reason'),
                                              outcome=report['outcome'], counts=counts, events=events, actions=actions))
                if decisions[0]['reason'] != decisions[1]['reason'] or decisions[0]['state'] != decisions[1]['state']:
                    differences.append(dict(variant=name, base=decisions[0], new=decisions[1],
                        justification='Approved earliest recent-root KEEP and latch after a real observed failure; no positive age reuse.'))
                observations.append(dict(variant=name, result='PASS', decisions=decisions))
            except AssertionError as exc:
                failures.append(dict(variant=name, error=str(exc)[:2000]))
    assert not failures, dict(passed=observations, failures=failures)
    return dict(variants=observations, equivalence_differences=differences,
                limits='Scratch healing runs after its actual snapshot-root observation on base and cheap-root observation on new; both retain the observed recent age. Cheap root age only; an old root still needs every current recursive admission. C40 owns later age races.')


def c44() -> dict:
    """Installed native writer and hold changes after slow work stay latched."""
    return c42(native=True)


def c45():
    """Topology changes after an eligible checkpoint never reuse that proof."""
    variants = ('new_inside', 'moved_inside', 'repointed_inside', 'non_task_inside',
                'uncertain_inside', 'duplicate_pointer', 'conflicting_pointer',
                'common_identity', 'admin_identity', 'entry_identity', 'prefix_identity',
                'pointer_ancestor_identity', 'pointer_depth2_identity', 'pointer_depth3_identity', 'unsafe_unknown', 'unenumerable_unknown',
                'unreadable_gitdir', 'git_list_omission', 'literal_root_omission')
    observations, failures = [], []
    old = ownership_base()
    for boundary in ('eligible_checkpoint', 'remove_census', 'repair_census', 'retry_census'):
        for label in variants:
            for module in (old, ds):
                name = ('base/' if module is old else 'new/') + boundary + '/' + label
                try:
                    with fixture() as f, ExitStack() as changes:
                        ownership_indexes(f)
                        repo, path = f.worktree('TASK-1')
                        _, peer = f.worktree('TASK-2')
                        admin = repo / '.git/worktrees'
                        entry = admin / path.name
                        peer_entry = admin / peer.name
                        # Make the independently retained peer run after TASK-1.
                        os.utime(path, (time.time() - 60, time.time() - 60))
                        os.utime(peer, (time.time(), time.time()))
                        f.db.execute("UPDATE tasks SET status='in_progress' WHERE id='TASK-2'")
                        f.db.commit()
                        if label in ('pointer_ancestor_identity', 'pointer_depth2_identity', 'pointer_depth3_identity'):
                            ancestor = f.base / 'pointer-chain/a/b'
                            ancestor.mkdir(parents=True)
                            remote = ancestor / 'non-task'
                            f.git(repo, 'worktree', 'move', str(peer), str(remote))
                            peer = remote
                        before = candidate_contents(path, peer, repo / '.git')
                        refs = f.git(repo, 'show-ref')
                        events, actions, result = [], [], {}
                        real_git, real_admit, real_rows, real_mutate = module.git, module.admit, module.registrations, module.mutate
                        counts = dict(admissions=0, removes=0)

                        def replace_directory(target):
                            held = f.base / ('held-' + label)
                            target.rename(held)
                            if label.startswith('pointer_depth'):
                                # Replace only this ancestor; retain its descendant and
                                # immediate pointer-parent inode, bytes and reciprocal map.
                                target.mkdir()
                                child = next(held.iterdir()).name
                                (held / child).rename(target / child)
                                changes.callback(lambda: ((target / child).rename(held / child), target.rmdir(), held.rename(target)))
                            else:
                                shutil.copytree(held, target)
                                changes.callback(lambda: (shutil.rmtree(target), held.rename(target)))

                        def change():
                            if label in ('new_inside', 'non_task_inside'):
                                nested = path / 'ignored' / ('TASK-3' if label == 'new_inside' else 'non-task')
                                nested.parent.mkdir(exist_ok=True)
                                f.git(repo, 'worktree', 'add', '--detach', str(nested), 'HEAD')
                                nested_entry = Path((nested / '.git').read_text().strip().removeprefix('gitdir: '))
                                changes.callback(lambda: (shutil.rmtree(nested), shutil.rmtree(nested_entry)))
                            elif label in ('moved_inside', 'repointed_inside', 'uncertain_inside'):
                                nested = path / 'ignored/non-task'
                                nested.parent.mkdir(exist_ok=True)
                                if label == 'repointed_inside':
                                    pointer = peer_entry / 'gitdir'
                                    original = pointer.read_bytes()
                                    peer.rename(nested)
                                    pointer.write_text(str(nested / '.git') + '\n')
                                    changes.callback(lambda: (pointer.write_bytes(original), nested.rename(peer)))
                                else:
                                    f.git(repo, 'worktree', 'move', str(peer), str(nested))
                                    changes.callback(lambda: f.git(repo, 'worktree', 'move', str(nested), str(peer)))
                                if label == 'uncertain_inside':
                                    index = peer_entry / 'index'
                                    mode = stat.S_IMODE(index.stat().st_mode)
                                    index.chmod(0)
                                    changes.callback(lambda: index.chmod(mode))
                            elif label == 'duplicate_pointer':
                                duplicate = admin / 'duplicate'
                                shutil.copytree(peer_entry, duplicate)
                                changes.callback(lambda: shutil.rmtree(duplicate))
                            elif label == 'conflicting_pointer':
                                pointer = peer_entry / 'gitdir'
                                original = pointer.read_bytes()
                                pointer.write_text(str(path / '.git') + '\n')
                                changes.callback(lambda: pointer.write_bytes(original))
                            elif label.endswith('_identity'):
                                target = dict(common_identity=repo / '.git', admin_identity=admin,
                                              entry_identity=entry, prefix_identity=path.parent,
                                              pointer_ancestor_identity=peer.parent, pointer_depth2_identity=peer.parent.parent,
                                              pointer_depth3_identity=peer.parent.parent.parent)[label]
                                replace_directory(target)
                            elif label in ('unsafe_unknown', 'unenumerable_unknown', 'unreadable_gitdir'):
                                kind = dict(unsafe_unknown='gitdir_alias', unenumerable_unknown='unreadable_entry',
                                            unreadable_gitdir='unreadable_gitdir')[label]
                                changes.enter_context(damaged_registration(f, repo, peer, kind))
                            # Omission rows alter only the external observation below.
                            events.append('changed_at_' + boundary)

                        def checkpoint():
                            target = next((c for c in result.get('candidates', []) if c['path'] == str(path)), None)
                            if boundary == 'eligible_checkpoint' and target and target['state'] == 'eligible' and not events:
                                change()
                            if target and target['state'] in ('retained', 'partial_or_unknown') and len(events) == 1:
                                changes.close()
                                events.append('healed_after_refusal')

                        def git(root, deadline, *args):
                            if args[:2] in (('worktree', 'remove'), ('worktree', 'prune')):
                                if boundary in ('repair_census', 'retry_census') and not events and not counts['removes']:
                                    counts['removes'] += 1
                                    actions.append('initial_remove')
                                    raise module.Retain('synthetic no-effect first Git refusal')
                                actions.append(args[:2])
                                # A regression must be attributable without letting the
                                # test remove its deliberately damaged fixture.
                                raise module.Retain('fixture_observed_prohibited_mutation')
                            value = real_git(root, deadline, *args)
                            if label == 'git_list_omission' and events == ['changed_at_' + boundary] and args[:2] == ('worktree', 'list'):
                                value = '\0\0'.join(block for block in value.split('\0\0')
                                                     if not block.startswith('worktree ' + str(peer) + '\0'))
                            return value

                        def mutate(*args, **kwargs):
                            if not events and boundary == 'retry_census':
                                actions.append('initial_repair')
                                return real_mutate(*args, **kwargs)
                            actions.append(('filesystem', 'mutation'))
                            raise module.Retain('fixture_observed_prohibited_mutation')

                        real_names = module.names
                        def names(fd, deadline):
                            value = real_names(fd, deadline)
                            if label == 'literal_root_omission' and events == ['changed_at_' + boundary]:
                                if fs.identity(os.fstat(fd)) == fs.identity(path.parent.stat()):
                                    return [name for name in value if name != peer.name]
                            return value

                        def admit(*args, **kwargs):
                            tree = real_admit(*args, **kwargs)
                            counts['admissions'] += 1
                            return tree

                        def registrations(*args, **kwargs):
                            trigger = dict(remove_census=2, repair_census=3, retry_census=4).get(boundary)
                            if trigger is not None and counts['admissions'] == trigger and not events:
                                change()
                            return real_rows(*args, **kwargs)

                        with patch.object(module, 'git', git), patch.object(module, 'mutate', mutate), patch.object(module, 'names', names), \
                                patch.object(module, 'admit', admit), patch.object(module, 'registrations', registrations):
                            report = module.sweep_org(f.org, time.monotonic() + 20, mode='on', now=NOW,
                                                  result=result, checkpoint=checkpoint)
                        target = row(report, path)
                        prior = (['initial_remove', 'initial_repair'] if boundary == 'retry_census' else
                                 ['initial_remove'] if boundary == 'repair_census' else [])
                        if module is ds:
                            assert actions == prior, ('C45 stale inventory reached mutation', name, actions, target)
                            assert target['state'] == ('partial_or_unknown' if prior else 'retained'), (name, target)
                            reason = ('linked_descendant:' if label.endswith('inside') else
                                      'git_mapping_changed_during_inventory:' if label in
                                      ('pointer_ancestor_identity', 'pointer_depth2_identity', 'pointer_depth3_identity', 'literal_root_omission')
                                      else 'git_mapping_changed_during_inventory:' if boundary != 'eligible_checkpoint' and label in
                                      ('common_identity', 'admin_identity', 'entry_identity', 'prefix_identity')
                                      else 'unknown_git_registration')
                            assert reason in target['reason'], (name, target)
                            if prior: assert not report['coverage']['complete'] and report['outcome'] == 'error', report
                        assert events == ['changed_at_' + boundary, 'healed_after_refusal'], (label, events)
                        assert target.get('removed_attributable_bytes') is None
                        assert report['phases']['worktree']['removed_attributable_bytes'] == 0
                        changes.close()
                        # The parent ignored directory is fixture-created, not an effect.
                        ignored = path / 'ignored'
                        if ignored.exists() and not any(ignored.iterdir()): ignored.rmdir()
                        assert candidate_contents(path, peer, repo / '.git') == before, label
                        assert f.git(repo, 'show-ref') == refs
                        deadline = time.monotonic() + 10
                        module.admit(dict(phase='worktree', path=str(path), workspace=str(f.workspace), repo=str(repo)),
                                 module.Evidence(f.org, deadline), deadline, now=NOW)
                        assert row(report, path)['state'] == target['state']
                        observations.append(dict(variant=name, result='PASS', state=target['state'], reason=target['reason'].replace(str(f.base), '<fixture>'),
                                                 coverage=report['coverage'], outcome=report['outcome'], actions=actions,
                                                 events=events, preservation='bytes/modes/refs'))
                except Exception as exc:
                    failures.append(dict(variant=name, error=type(exc).__name__ + ': ' + str(exc)[:3000]))
    assert not failures, dict(passed=observations, failures=failures)
    return dict(base=OWNERSHIP_BASE, variants=observations, limits='Actual pinned base and new production; prohibited next effects intercepted. Initial no-effect Git refusal enters repair/retry. Real ancestor-preserving renames; omission rows inject incomplete external observations.')



def c46():
    """A mount observed after eligibility refuses deletion and stays latched."""
    observations, failures = [], []
    old = ownership_base()
    for phase in ('scratch', 'worktree'):
        boundaries = ('eligible_checkpoint', 'final_snapshot')
        if phase == 'worktree': boundaries += ('remove_census', 'repair_census', 'retry_census')
        for boundary in boundaries:
            for module in (old, ds):
                name = ('base/' if module is old else 'new/') + phase + '/' + boundary
                try:
                    with fixture() as f:
                        ownership_indexes(f)
                        repo = None
                        if phase == 'scratch':
                            path = f.scratch('TASK-1')
                            mountpoint = path / 'nested'
                        else:
                            repo, path = f.worktree('TASK-1')
                            mountpoint = path / 'ignored/mountpoint'
                            mountpoint.mkdir(parents=True)
                            (mountpoint / 'payload').write_bytes(b'preserved same-device bytes')
                        before = candidate_contents(path)
                        refs = f.git(repo, 'show-ref') if repo else None
                        events, actions, result = [], [], {}
                        active = False
                        real_snapshot = module.snapshot
                        real_mounts = real_snapshot.__globals__['mounts']
                        real_git, real_admit, real_rows, real_mutate = module.git, module.admit, module.registrations, module.mutate
                        counts = dict(admissions=0, removes=0)
                        snapshots = 0
                        def mounts(deadline):
                            value = real_mounts(deadline)
                            return value | {str(mountpoint)} if active else value
                        def snapshot(root, deadline, **kwargs):
                            nonlocal snapshots, active
                            if Path(root) == path:
                                snapshots += 1
                                if boundary == 'final_snapshot' and snapshots == 3:
                                    active = True
                                    events.append('mount_arrived_after_inventory')
                            return real_snapshot(root, deadline, **kwargs)
                        def checkpoint():
                            nonlocal active
                            target = next((c for c in result.get('candidates', []) if c['path'] == str(path)), None)
                            if not target: return
                            if boundary == 'eligible_checkpoint' and target['state'] == 'eligible' and not events:
                                active = True
                                events.append('mount_arrived_after_inventory')
                            if target['state'] in ('retained', 'partial_or_unknown') and active:
                                if module is ds: assert target['reason'] == 'filesystem_boundary', target
                                active = False
                                events.append('mount_healed_after_refusal')
                        def git(root, deadline, *args):
                            if args[:2] in (('worktree', 'remove'), ('worktree', 'prune')):
                                if boundary in ('repair_census', 'retry_census') and not active and not counts['removes']:
                                    counts['removes'] += 1
                                    actions.append('initial_remove')
                                    raise module.Retain('synthetic no-effect first Git refusal')
                                actions.append(args[:2])
                                raise module.Retain('fixture_observed_prohibited_mutation')
                            return real_git(root, deadline, *args)
                        def mutate(*args, **kwargs):
                            if not active and boundary == 'retry_census':
                                actions.append('initial_repair')
                                return real_mutate(*args, **kwargs)
                            actions.append(('filesystem', 'mutation'))
                            raise module.Retain('fixture_observed_prohibited_mutation')
                        def admit(*args, **kwargs):
                            tree = real_admit(*args, **kwargs)
                            counts['admissions'] += 1
                            return tree
                        def registrations(*args, **kwargs):
                            nonlocal active
                            trigger = dict(remove_census=2, repair_census=3, retry_census=4).get(boundary)
                            if trigger is not None and counts['admissions'] == trigger and not events:
                                active = True
                                events.append('mount_arrived_after_inventory')
                            return real_rows(*args, **kwargs)
                        with patch.dict(real_snapshot.__globals__, mounts=mounts), patch.object(module, 'snapshot', snapshot), \
                                patch.object(module, 'git', git), patch.object(module, 'mutate', mutate), \
                                patch.object(module, 'admit', admit), patch.object(module, 'registrations', registrations):
                            report = module.sweep_org(f.org, time.monotonic() + 15, mode='on', now=NOW,
                                                  result=result, checkpoint=checkpoint)
                        target = row(report, path)
                        prior = (['initial_remove', 'initial_repair'] if boundary == 'retry_census' else
                                 ['initial_remove'] if boundary == 'repair_census' else [])
                        if module is ds:
                            assert actions == prior, ('C46 stale inventory reached mutation', name, actions)
                            assert target['state'] == ('partial_or_unknown' if prior else 'retained'), (name, target)
                            assert target['reason'] == 'filesystem_boundary', (name, target)
                        if prior: assert not report['coverage']['complete'] and report['outcome'] == 'error', report
                        assert events == ['mount_arrived_after_inventory', 'mount_healed_after_refusal'], (name, events)
                        assert candidate_contents(path) == before
                        assert row(report, path).get('removed_attributable_bytes') is None
                        assert report['phases'][phase]['removed_attributable_bytes'] == 0
                        if repo: assert f.git(repo, 'show-ref') == refs
                        candidate = dict(phase=phase, path=str(path), workspace=str(f.workspace))
                        if repo: candidate['repo'] = str(repo)
                        deadline = time.monotonic() + 10
                        module.admit(candidate, module.Evidence(f.org, deadline), deadline, now=NOW)
                        assert row(report, path)['state'] == target['state']
                        observations.append(dict(variant=name, result='PASS', reason=target['reason'], state=target['state'],
                                                 outcome=report['outcome'], coverage=report['coverage'], events=events, actions=actions))
                except Exception as exc:
                    failures.append(dict(variant=name, error=type(exc).__name__ + ': ' + str(exc)[:3000]))
    assert not failures, dict(passed=observations, failures=failures)
    return dict(base=OWNERSHIP_BASE, variants=observations, limits='Existing production mounts() seam supplies one same-device mountpoint; actual native mount creation/enumeration and Darwin NOT VERIFIED. No mount/namespace/config operation.')



def c47():
    """Arrivals/churn in final censuses preserve local keeps and peer progress."""
    old = ownership_base()
    observations, failures = [], []
    for label in ('new_task_pair', 'new_task_root', 'candidate_note', 'sibling_note',
                  'candidate_index_lock', 'sibling_index_lock'):
        decisions = []
        for module in (old, ds):
            name = ('base/' if module is old else 'new/') + label
            try:
                with fixture() as f:
                    ownership_indexes(f)
                    repo, path = f.worktree('TASK-1'); _, peer = f.worktree('TASK-2')
                    f.db.execute("UPDATE tasks SET status='in_progress' WHERE id='TASK-2'"); f.db.commit()
                    os.utime(path, (time.time() - 60, time.time() - 60))
                    entry = repo / '.git/worktrees' / (peer.name if label.startswith('sibling') else path.name)
                    note = entry / ('index.lock' if label.endswith('index_lock') else 'fixture-note')
                    if label.endswith('note'): note.write_bytes(b'initial ordinary bytes\n')
                    initial_peer = candidate_contents(peer, repo / '.git/worktrees' / peer.name)
                    initial_path = candidate_contents(path, repo / '.git/worktrees' / path.name)
                    refs = set(f.git(repo, 'show-ref').splitlines())
                    events, actions, result = [], [], {}
                    armed = False
                    arrival = path.with_name('TASK-3')
                    real_meta, real_git = module._registration_metadata, module.git

                    def change():
                        if label == 'new_task_pair':
                            _, created = f.worktree('TASK-3'); assert created == arrival
                        elif label == 'new_task_root':
                            arrival.mkdir(); (arrival / 'payload').write_bytes(b'unknown preserved bytes')
                        else: note.write_bytes(b'new ordinary bytes; no writer identity inferred\n')
                        events.append('changed_between_final_censuses')

                    def heal():
                        if label.endswith('note'): note.write_bytes(b'initial ordinary bytes\n')
                        else: note.unlink()  # Only the synthetic lock created above.
                        events.append('healed_after_latched_observation')

                    def metadata(root, deadline, **kwargs):
                        value = real_meta(root, deadline, **kwargs)
                        if armed and not events: change()
                        return value

                    def checkpoint():
                        nonlocal armed
                        target = next((c for c in result.get('candidates', []) if c['path'] == str(path)), None)
                        if target and target['state'] == 'eligible': armed = True
                        if len(events) == 1 and not label.startswith('new_task'):
                            owner = peer if label.startswith('sibling') else path
                            scopes = result.get('discovery_scopes', [])
                            if any(scope['path'] == str(owner) and 'metadata_changed:' in scope['reason'] for scope in scopes):
                                heal()

                    def git(root, deadline, *args):
                        if args[:2] in (('worktree', 'remove'), ('worktree', 'prune')):
                            actions.append(args)
                            if label.startswith('candidate') or args[-1] == str(peer) or args[:2] == ('worktree', 'prune'):
                                raise module.Retain('fixture_observed_prohibited_mutation')
                        return real_git(root, deadline, *args)

                    with patch.object(module, '_registration_metadata', metadata), patch.object(module, 'git', git):
                        report = module.sweep_org(f.org, time.monotonic() + 20, mode='on', now=NOW,
                                                  result=result, checkpoint=checkpoint)
                    assert events, (name, report)
                    target = row(report, path)
                    if label.startswith('candidate'):
                        assert not actions, ('C47 stale census missed local keep', name, actions)
                        retained(report, path, 'metadata_changed:' + str(note))
                        assert candidate_contents(path, repo / '.git/worktrees' / path.name) == initial_path
                    else:
                        assert target['state'] == 'removed' and not path.exists(), (name, target)
                        assert len(actions) == 1 and actions[0][:2] == ('worktree', 'remove'), (name, actions)
                        assert target['removed_attributable_bytes'] > 0
                    if label.startswith('sibling'):
                        retained(report, peer, 'metadata_changed:' + str(note))
                    if label.startswith('new_task'):
                        assert arrival.exists() and any(scope['path'] == str(arrival) for scope in report['discovery_scopes']), (name, report)
                        if label == 'new_task_pair':
                            assert str(arrival) in report['deferred_arrivals'] and report['coverage']['complete'], report
                        else: assert not report['coverage']['complete'], report
                    else:
                        assert events == ['changed_between_final_censuses', 'healed_after_latched_observation'], (name, events)
                        # Healed ordinary metadata currently passes; the run's
                        # retained reason cannot be cleared by that later fact.
                        module.registrations(repo, time.monotonic() + 5).require(peer if label.startswith('sibling') else path, time.monotonic() + 5)
                    assert candidate_contents(peer, repo / '.git/worktrees' / peer.name) == initial_peer
                    assert refs <= set(f.git(repo, 'show-ref').splitlines())
                    decision = ownership_decisions(report, f.base)
                    decision = json.loads(json.dumps(decision).replace(str(f.base), '<fixture>'))
                    for candidate in decision['candidates']:
                        reason = candidate.get('reason', '')
                        if ';signature_change=' in reason:
                            native, diagnostic = reason.split(';signature_change=', 1)
                            assert json.loads(diagnostic)['comparison'] == 'pre_git_censuses', reason
                            # Separate fixtures have different inode/time pairs;
                            # policy equivalence retains the exact native cause.
                            candidate['reason'] = native
                    decisions.append(decision)
                    observations.append(dict(variant=name, result='PASS', events=events, decision=decision))
            except Exception as exc:
                failures.append(dict(variant=name, error=type(exc).__name__ + ': ' + str(exc)[:3000]))
        if len(decisions) == 2 and decisions[0] != decisions[1]:
            failures.append(dict(variant=label + '/equivalence', error=str(decisions)))
    assert not failures, dict(passed=observations, failures=failures)
    return dict(base=OWNERSHIP_BASE, variants=observations, equivalence_differences=[],
                limits='Real disposable Git/files and existing census seam; synthetic index.lock created/healed only by this fixture, never a host lock or writer-liveness inference')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    selection=parser.add_mutually_exclusive_group()
    selection.add_argument('--corrections-only', action='store_true', help='THR307seq117/134: C1-C7, S1-S5 (8-12) and per-org settlement (13); no historical/isolation cases')
    selection.add_argument('--correction-cases', type=int, nargs='+', choices=range(1, 14), help='select finite cases within the corrections lane')
    selection.add_argument('--cases',type=int,nargs='+',choices=range(1,48))
    selection.add_argument('--isolation-case',choices=('mount','root'),help='only the private executable namespace fixture; exit77 if unavailable')
    args=parser.parse_args()
    # The private helper alone is mapped root; the driver retains its ordinary
    # permission assumptions. Never run the whole driver as host/mapped root.
    if os.geteuid()==0: parser.error('run this driver as an unprivileged user')
    status=0
    try:
        if args.corrections_only or args.correction_cases:
            corrections = ((1, 'Finished jobs before lineage', correction_jobs), (2, 'Superseded owner and structural root', correction_superseded), (3, 'Ended trees without process inspection and truthful reporting', correction_processes), (4, 'All-org policy and obsolete config compatibility', correction_policy), (5, 'Explicit preview for every legacy policy', correction_preview), (6, 'Fresh admission and bounded retry independent of policy', correction_fresh_policy), (7, 'Actual reporting and native deferred/partial disposition', correction_policy_reporting), (8, 'S1 default-on scheduled control', correction_stop_default), (9, 'S2 global scheduled stop and audit', correction_stop_true), (10, 'S3 strict invalid control suspension', correction_stop_invalid), (11, 'S4 next-attempt recovery', correction_stop_recovery), (12, 'S5 manual preview with stop controls', correction_stop_preview), (13, 'Per-org native writer settlement and orderly incomplete exit', correction_per_org_settlement))
            for number, purpose, fn in corrections:
                if args.correction_cases is None or number in args.correction_cases:
                    case(number, purpose, fn)
        elif args.isolation_case:
            detail=isolated_boundary(args.isolation_case)
            RESULTS.append(dict(case=args.isolation_case,result=detail['result'],observations=detail))
            status=77 if detail['result']=='UNAVAILABLE' else 0
        else:
            config_checks()
            purposes=('Terminal owned root and attribution','Nonterminal and invalid lineage','Real process cwd/root/exe/fd','Real TMPDIR/TMP/TEMP and alias','Readonly handling and bounded Git retry','Link/ancestor and actual mounted boundaries','Dirty/untracked and ignored Git contents','Nonmutating function and public CLI dry run','Missing/denied evidence and all job lineage','Jobs both phases and fresh retry change','Nested recent writes','Partial removal, deadline, shutdown and event loop','Detached commit retained refs','Same-night progress after refusals and exhaustion','Partial discovery with protected non-Git siblings','Narrow unreadable/broken/alias discovery scopes','Global deadline during partial discovery','Progress after many uncertain subtrees','Complete unreadable/missing registration census','No metadata alias or conflicting backlink traversal','Fresh registration gates and truthful partial effects','Primary-only control and metadata enumeration deadline')
            purposes += ('Additive TASK registration arrivals and shared mapping changes',)
            purposes += ('Measurement compatibility with pinned pre-instrumentation source', 'Known measurement counts, effects, history and failure isolation')
            purposes += ('Production-sized bounded history and invalid-report visibility',)
            purposes += ('Coverage and uncertainty classification', 'Located missing-folder residue', 'Durable previous-night records', 'Reporting-only interrupted removals')
            purposes += ('Indexed ownership equivalence with pinned base', 'Fresh ownership after inventory and latched refusals', 'Production-sized base/new disposable measurement')
            purposes += ('Batched checkpoint equivalence, crash honesty and safe publication',)
            purposes += ('Org ownership inventory, refusal latch and fresh positive invalidation',)
            purposes += ('Clone keep inventory, dirty cost and healed-refusal latch',)
            purposes += ('Post-removal branch resolution and incomplete lost refs', 'Fresh HEAD branch refs and mapping identities after inventory',)
            purposes += ('Literal primary common-dir proof and base removal parity', 'Fresh recursive safety after inventory and healed refusal latch',)
            purposes += ('Candidate-scoped auxiliary reads with exhaustive fresh topology',)
            purposes += ('Current ownership at final scan remove repair retry boundaries',)
            purposes += ('Cheap recent-root keep ordering and healed refusal latch',)
            purposes += ('Fresh native writer and hold at final effect boundaries',)
            purposes += ('Fresh topology after eligible inventory and healed-refusal latch', 'Mount arrival through production detection seam after inventory',)
            purposes += ('Dynamic base equivalence for final-census TASK arrivals and local churn',)
            for number,(fn,purpose) in enumerate(zip((c1,c2,c3,c4,c5,c6,c7,c8,c9,c10,c11,c12,c13,c14,c15,c16,c17,c18,c19,c20,c21,c22,c23,c24,c25,c26,c27,c28,c29,c30,c31,c32,c33,c34,c35,c36,c37,c38,c39,c40,c41,c42,c43,c44,c45,c46,c47),purposes),1):
                if args.cases is None or number in args.cases:
                    if number in (3, 4):
                        RESULTS.append(dict(case=number, purpose=purpose, result='SUPERSEDED', reason='THR307 seq117 removed OS-process eligibility; historical source not executed'))
                    else:
                        case(number,purpose,fn)
            if any(item['result']=='PARTIAL' for item in RESULTS): status=77
    except Exception:
        import traceback
        traceback.print_exc();status=1
    finally:
        print(json.dumps({'environment':dict(python=sys.version,platform=sys.platform,uid=os.geteuid(),wall_clock='synthetic now +7200s for old ctime; actual monotonic budget'), 'cases':RESULTS,'limits':'UNAVAILABLE/PARTIAL are missing executable evidence, not PASS. No protected mutation or historical proof executed.'},indent=2))
    raise SystemExit(status)
