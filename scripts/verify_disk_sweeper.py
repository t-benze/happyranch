#!/usr/bin/env python3
"""THR307 disposable acceptance driver, explicitly outside retired Python units.

Run from the repository: .venv/bin/python scripts/verify_disk_sweeper.py
Real files/Git/child processes, synthetic SQLite records, no daemon actors. The
wall-clock argument is advanced two hours for old-ctime fixtures (ctime cannot
be backdated on Linux). Monotonic deadlines and process/Git effects stay real.
No pytest or tests/ imports. Never run against existing orgs or cleanup data.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
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

    def worktree(self, name):
        self.task(name)
        repo = self.workspace / 'repos/project'
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
        disposition='PARTIAL' if number==6 and isinstance(detail,dict) and detail.get('mount_case','').startswith('UNAVAILABLE') else 'PASS'
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
        return 'Real cwd, fd and executable users retained; actual proc root/cwd/exe/fd read trace inspected. No positive chroot fixture.'


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
        _,z=f.worktree('TASK-3');z.chmod(0o555)
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
        # Mount namespace availability is observed, never faked via helper stubs.
        probe=subprocess.run(['unshare','-Urnm','true'],capture_output=True,text=True)
        return dict(external_sentinel='bytes/mode unchanged; symlink candidate and ancestor retained',mount_case='AVAILABLE but not executed' if probe.returncode==0 else 'UNAVAILABLE: '+probe.stderr.strip())


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


def job_refusal(f,status,reason):
    p=f.scratch('TASK-1');repo,q=f.worktree('TASK-2')
    (p/'nested').chmod(0o555);q.chmod(0o555)
    f.db.executemany('INSERT INTO jobs VALUES(?,?)',[('TASK-1',status),('TASK-2',status)]);f.db.commit()
    before=candidate_contents(p,q);registered=f.git(repo,'worktree','list','--porcelain')
    with refuse_mutations():r=f.sweep()
    retained(r,p,reason);retained(r,q,reason)
    assert before==candidate_contents(p,q)
    assert registered==f.git(repo,'worktree','list','--porcelain')
    assert all(phase['removed_attributable_bytes']==0 for phase in r['phases'].values()),r
    return dict(status=repr(status),scratch=row(r,p)['reason'],worktree=row(r,q)['reason'],unchanged=True,mutation_calls=0)


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
    return dict(evidence='Missing manifest/jobs, actual unreadable child directory, controlled proc syscall denial all retain. Proc denial injection is not actual OS-denial proof.',unknown_jobs=unknown)


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
    return dict(active_jobs=active,native_terminal_controls=terminal,active_retry='Original newly queued job after real Git failure still refuses chmod/retry.',unknown_retries=retries)


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


if __name__=='__main__':
    status=0
    try:
        config_checks()
        purposes=('Terminal owned root and attribution','Nonterminal and invalid lineage','Real process cwd/root/exe/fd','Real TMPDIR/TMP/TEMP and alias','Readonly handling and bounded Git retry','Link/ancestor containment and mount availability','Dirty/untracked and ignored Git contents','Nonmutating function and public CLI dry run','Missing/denied evidence','Jobs both phases and fresh retry change','Nested recent writes','Partial removal, deadline, shutdown and event loop','Detached commit retained refs','Same-night progress after refusals and exhaustion')
        for number,(fn,purpose) in enumerate(zip((c1,c2,c3,c4,c5,c6,c7,c8,c9,c10,c11,c12,c13,c14),purposes),1):case(number,purpose,fn)
    except Exception:
        import traceback
        traceback.print_exc();status=1
    finally:
        print(json.dumps({'environment':dict(python=sys.version,platform=sys.platform,uid=os.geteuid(),wall_clock='synthetic now +7200s for old ctime; actual monotonic budget'), 'cases':RESULTS,'missing':'Actual mounted filesystem-boundary and positive chroot-root fixtures require suitable isolation; no protected mutation or historical proof executed.'},indent=2))
    raise SystemExit(status)
