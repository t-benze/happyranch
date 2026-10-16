#!/usr/bin/env python3
"""THR307 disposable acceptance driver, explicitly outside retired Python units.

Run from the repository: .venv/bin/python scripts/verify_disk_sweeper.py
Real files/Git/child processes, synthetic SQLite records, no daemon actors. The
wall-clock argument is advanced two hours for old-ctime fixtures (ctime cannot
be backdated on Linux). Monotonic deadlines and process/Git effects stay real.
No pytest or tests/ imports. Never run against existing orgs or cleanup data.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
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
        tree=fs.snapshot(p,time.monotonic()+5)
        expected=fs.allocated(tree,set())
        r=f.sweep()
        retained(r,external,'unknown_external_hardlinks')
        assert row(r,p)['allocated_bytes']==expected[0]
        assert row(r,p)['removed_attributable_bytes']==expected[1]
        assert (out/'external-link').read_text()=='durable hardlink'
        assert row(r,p)['state']=='removed',r
        assert not p.exists()
        assert before=={x.name:x.read_bytes() for x in manifest.iterdir()}
        assert (out/'keep').read_text()=='durable'
        assert r['phases']['scratch']['removed_attributable_bytes']==row(r,p)['removed_attributable_bytes']>0
        return 'Exact root absent; manifest/lock/output unchanged; one inode-deduped attribution.'


def c2():
    with fixture() as f:
        f.task('TASK-1','in_progress')
        p=f.scratch('TASK-2',parent='TASK-1')
        rejected=[p]
        for n,state in enumerate(('superseded','escalated','unknown','pending'),3): rejected.append(f.scratch(f'TASK-{n}',state))
        rejected.append(f.scratch('TASK-7',parent='TASK-999'))
        a=f.scratch('TASK-8',parent='TASK-9');rejected.append(a)
        b=f.scratch('TASK-9',parent='TASK-8');rejected.append(b)
        f.db.execute("UPDATE audit_log SET payload=? WHERE task_id='TASK-3'",(json.dumps({'workspace':'/conflicting'}),));f.db.commit()
        conflict=f.scratch('TASK-10');rejected.append(conflict)
        f.db.execute("UPDATE audit_log SET payload=? WHERE task_id='TASK-10'",(json.dumps({'workspace':'/conflicting'}),));f.db.commit()
        r=f.sweep()
        assert 'conflicting_workspace' in row(r,conflict)['reason']
        assert all(row(r,p)['state']=='retained' and p.exists() for p in rejected),r
        assert not r['phases']['scratch']['removed_attributable_bytes']
        return {p.name:row(r,p)['reason'] for p in rejected}


def c3():
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
        r=f.sweep();retained(r,p,'dirty');retained(r,q,'dirty')
        assert row(r,p)['dirty_paths'] and row(r,q)['dirty_paths']
        assert row(r,z)['state']=='removed',r
        assert 'task/TASK-3' in f.git(repo,'branch','--list')
        return 'Dirty/nonignored untracked paths listed; ignored cache discarded with clean locally retained branch.'


def c8():
    with fixture() as f:
        p=f.scratch('TASK-1');(p/'nested').chmod(0o555)
        repo,q=f.worktree('TASK-2')
        def signature():
            return {str(x.relative_to(f.base)):(x.lstat().st_mode,x.read_bytes() if x.is_file() else None) for x in f.base.rglob('*') if x.name!='happyranch.db'}
        before=signature();registered=f.git(repo,'worktree','list','--porcelain')
        calls=[];realgit=ds.git
        def trace(*a):calls.append(a[2:]);return realgit(*a)
        with patch.object(ds,'git',trace),patch.object(os,'fchmod',side_effect=AssertionError('dry chmod')),patch.object(os,'unlink',side_effect=AssertionError('dry unlink')),patch.object(os,'rmdir',side_effect=AssertionError('dry rmdir')):
            r=f.sweep('dry_run')
        assert all(c['state']=='eligible' for c in r['candidates']),r
        assert before==signature() and registered==f.git(repo,'worktree','list','--porcelain')
        assert not any('remove' in c or 'prune' in c for c in calls)
        # Public manual CLI uses real worker, actual current clock, JSON plus
        # one permitted audit row; it never activates on-mode deletion.
        result=subprocess.run([sys.executable,'-m','runtime.daemon.disk_sweeper','--dry-run','--runtime-root',str(f.base/'runtime')],env={**os.environ,'HAPPYRANCH_DAEMON_HOME':str(f.base/'daemon')},capture_output=True,text=True,timeout=8)
        assert result.returncode==0,result.stderr
        assert f.db.execute("SELECT COUNT(*) FROM audit_log WHERE action='disk_sweep_summary'").fetchone()[0]==1
        assert p.exists() and q.exists() and registered==f.git(repo,'worktree','list','--porcelain')
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
        for status in ('completed','failed','rejected','running'):
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
        # Real denied proc read: a test-owned restriction at the syscall seam
        # models kernel denial, not a permissive fabricated helper outcome.
        z=f.scratch('TASK-3');real=os.readlink
        def deny(path,*a,**kw):
            if str(path).startswith('/proc/') and str(path).endswith('/root'):raise PermissionError('fixture denial')
            return real(path,*a,**kw)
        with patch.object(os,'readlink',deny): retained(f.sweep(),z,'unknown_process_denied')
        f.db.execute('DROP TABLE jobs');f.db.commit()
        assert row(f.sweep(),z)['state']=='retained'
    unknown=[]
    for status in UNKNOWN_JOB_STATES:
        with fixture() as f:unknown.append(job_refusal(f,status,'unknown_job_status'))
    return dict(evidence='Missing manifest/jobs, actual unreadable child directory, controlled proc syscall denial all retain. Proc denial injection is not actual OS-denial proof.',unknown_jobs=unknown,unknown_lineage=lineage_refusals())


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
        for status in ('completed','failed','rejected'):
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
        async def exercise(cancel):
            summary=f.base/('cancel.json' if cancel else 'deadline.json')
            ticks=0
            async def heartbeat():
                nonlocal ticks
                while True:ticks+=1;await asyncio.sleep(.01)
            tick=asyncio.create_task(heartbeat())
            worker=asyncio.create_task(ds.run_worker([str(f.org),str(second)],summary,dry_run=True,budget=2 if cancel else .25))
            if cancel:
                for _ in range(100):
                    if pidfile.exists():break
                    await asyncio.sleep(.01)
                assert pidfile.exists(),'child did not start'
                worker.cancel()
            started=time.monotonic()
            try:await worker
            except asyncio.CancelledError:pass
            tick.cancel()
            assert time.monotonic()-started<3 and ticks>=2
            report=json.loads(summary.read_text());assert report['outcome']=='incomplete' and report['interruption']==('shutdown' if cancel else 'deadline'),report
            assert str(second) in report['unreached_orgs'],report
            assert report['budget_seconds']==(2 if cancel else .25),report
            if pidfile.exists():
                pid=int(pidfile.read_text());proc=Path(f'/proc/{pid}/stat')
                assert not proc.exists() or proc.read_text().split(') ')[1].startswith('Z '),'deleting child survived'
                pidfile.unlink()
            return report['interruption']
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
                assert report['orgs'][0]['candidates'][0]['state']=='mutation_in_flight',report
                assert not (target/'nested/a').exists() and (target/'nested/payload').exists()
                pid=int(marker.read_text());assert not Path(f'/proc/{pid}').exists(),'mutating worker survived'
            asyncio.run(stop_mutation())
        return dict(partial='real earlier unlink observed, no false reclaimed bytes',supervisor=[a,b,'shutdown after actual unlink; surviving file preserved, mutating worker reaped'],event_loop='heartbeat remained responsive; Git/sleep child absent or zombie, no runnable worker')


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
        observed=dict(known=1,unknown_scopes=scopes,alias_git_calls=0)
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
        target={'unreadable_admin':admin,'unreadable_entry':entry,'unreadable_gitdir':entry/'gitdir'}[kind]
        old=target.stat().st_mode & 0o777;target.chmod(0)
        restore.append(lambda: target.chmod(old))
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


def c19():
    """Git exit0 omissions must not create empty/complete inventories."""
    observed=[]
    for kind in ('unreadable_admin','unreadable_entry','unreadable_gitdir','missing_admin','missing_entry','missing_gitdir','missing_commondir','missing_head','malformed_head','malformed_gitdir','extra_entry'):
        with fixture() as f:
            p=f.scratch('TASK-1');repo,q=f.worktree('TASK-2','a-uncertain')
            safe,z=f.worktree('TASK-3','z-safe')
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
                with patch.object(ds,'git',trace):r=f.sweep()
                assert r['discovery']=='partial' and r['outcome']=='incomplete' and r['unreached'] is None,r
                assert all(row(r,x)['state']=='removed' and not x.exists() for x in (p,z)),r
                assert q.exists() and damaged['sentinel'].read_bytes()==b'external control'
                assert not any(root==str(repo) for root,args in calls),calls
                assert not any(args[:2]==('worktree','prune') for root,args in calls),calls
                if kind.startswith('unreadable_'): assert target_mode(repo,q,kind)==0
                observed.append(dict(kind=kind,git_omitted=str(q) not in git_output,dry='preserved/no mutation',on='independent scratch/repo removed',bad_repo_git_calls=0,unknown_total=None))
            assert before==candidate_contents(q,repo/'.git/worktrees')
            assert registered==f.git(repo,'worktree','list','--porcelain')
            assert f.git(repo,'show-ref','--verify','refs/heads/task/TASK-2')
    return observed


def target_mode(repo,path,kind):
    target={'unreadable_admin':repo/'.git/worktrees','unreadable_entry':repo/'.git/worktrees'/path.name,'unreadable_gitdir':repo/'.git/worktrees'/path.name/'gitdir'}[kind]
    return target.lstat().st_mode & 0o777


def c20():
    """Valid alias escape and pointer conflicts stop BEFORE Git consumes them."""
    observed=[]
    for kind in ('admin_alias','entry_alias','gitdir_alias','commondir_alias','backlink_alias','conflicting_backlink'):
        with fixture() as f:
            p=f.scratch('TASK-1');repo,q=f.worktree('TASK-2')
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
                with patch.object(ds,'git',trace):r=f.sweep()
                assert dry['discovery']==r['discovery']=='partial' and r['outcome']=='incomplete',r
                assert r['unreached'] is None and row(r,p)['state']=='removed' and q.exists(),r
                assert r['discovery_scopes'][0]['path']==str(repo) and r['discovery_scopes'][0]['candidate_count'] is None,r
                assert not any(root==str(repo) for root,args in calls),calls
                assert not any(args[:2] in (('worktree','remove'),('worktree','prune')) for root,args in calls),calls
                assert protected==candidate_contents(damaged['external'])
                observed.append(dict(kind=kind,git_calls=0,external_bytes_modes='unchanged',registration_worktree_branch='preserved'))
            assert before==candidate_contents(q,repo/'.git/worktrees')
            assert registered==f.git(repo,'worktree','list','--porcelain')
            assert f.git(repo,'show-ref','--verify','refs/heads/task/TASK-2')
    return observed


def c21():
    """Transitions wrap actual shipping boundaries, with real Git failures."""
    from contextlib import ExitStack
    observations=[]
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
    # Metadata changes AFTER a completed removal must prohibit prune without
    # erasing the real earlier effect or its measured attribution.
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
        assert changed[0] and r['discovery']=='partial' and r['outcome']=='incomplete',r
        assert row(r,q)['state']=='removed' and row(r,q)['removed_attributable_bytes']>0 and not q.exists(),r
        assert not any(args[:2]==('worktree','prune') for args in calls)
        assert (external/'sentinel').read_text()=='keep'
        assert f.git(repo,'show-ref','--verify','refs/heads/task/TASK-1')
        observations.append(dict(when='before_prune',prune_calls=0,earlier_removal='preserved with positive attribution',unknown_total=None))
    return observations


def c22():
    """Primary-only absent-admin control and metadata enumeration deadline."""
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


def config_checks():
    assert OrgConfig.load_from_text('').disk_sweep_mode=='dry_run'
    for mode in ('off','dry_run','on'):assert OrgConfig.load_from_text('disk_sweep: {mode: "'+mode+'"}').disk_sweep_mode==mode
    for text in ('disk_sweep: null','disk_sweep: {mode: true}','disk_sweep: {mode: bad}','disk_sweep: {extra: 1}'):
        try:OrgConfig.load_from_text(text)
        except OrgConfigError:continue
        raise AssertionError(text)
    with fixture() as f:
        p=f.scratch('TASK-1')
        with patch.object(ds,'discover',side_effect=AssertionError('off inventory')):assert f.sweep('off')['outcome']=='off'
        assert p.exists()
    from datetime import datetime
    for hour in (2,3,23):
        now=datetime.now().astimezone().replace(hour=hour)
        assert ds.next_night(now)>now


def isolated_boundary(kind):
    """Run the actual bounded fixture, never a capability-only true probe.

    This one executor attempts unprivileged disposable isolation. EPERM is
    reported without sudo, another executor, a job or provisioning fallback.
    """
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


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    selection=parser.add_mutually_exclusive_group()
    selection.add_argument('--cases',type=int,nargs='+',choices=range(1,23))
    selection.add_argument('--isolation-case',choices=('mount','root'),help='only the private executable namespace fixture; exit77 if unavailable')
    args=parser.parse_args()
    # The private helper alone is mapped root; the driver retains its ordinary
    # permission assumptions. Never run the whole driver as host/mapped root.
    if os.geteuid()==0: parser.error('run this driver as an unprivileged user')
    status=0
    try:
        if args.isolation_case:
            detail=isolated_boundary(args.isolation_case)
            RESULTS.append(dict(case=args.isolation_case,result=detail['result'],observations=detail))
            status=77 if detail['result']=='UNAVAILABLE' else 0
        else:
            config_checks()
            purposes=('Terminal owned root and attribution','Nonterminal and invalid lineage','Real process cwd/root/exe/fd','Real TMPDIR/TMP/TEMP and alias','Readonly handling and bounded Git retry','Link/ancestor and actual mounted boundaries','Dirty/untracked and ignored Git contents','Nonmutating function and public CLI dry run','Missing/denied evidence and all job lineage','Jobs both phases and fresh retry change','Nested recent writes','Partial removal, deadline, shutdown and event loop','Detached commit retained refs','Same-night progress after refusals and exhaustion','Partial discovery with protected non-Git siblings','Narrow unreadable/broken/alias discovery scopes','Global deadline during partial discovery','Progress after many uncertain subtrees','Complete unreadable/missing registration census','No metadata alias or conflicting backlink traversal','Fresh registration gates and truthful partial effects','Primary-only control and metadata enumeration deadline')
            for number,(fn,purpose) in enumerate(zip((c1,c2,c3,c4,c5,c6,c7,c8,c9,c10,c11,c12,c13,c14,c15,c16,c17,c18,c19,c20,c21,c22),purposes),1):
                if args.cases is None or number in args.cases:case(number,purpose,fn)
            if any(item['result']=='PARTIAL' for item in RESULTS): status=77
    except Exception:
        import traceback
        traceback.print_exc();status=1
    finally:
        print(json.dumps({'environment':dict(python=sys.version,platform=sys.platform,uid=os.geteuid(),wall_clock='synthetic now +7200s for old ctime; actual monotonic budget'), 'cases':RESULTS,'limits':'UNAVAILABLE/PARTIAL are missing executable evidence, not PASS. No protected mutation or historical proof executed.'},indent=2))
    raise SystemExit(status)
