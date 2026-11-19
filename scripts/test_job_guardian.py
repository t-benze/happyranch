"""Focused real-process job checks; outside the retired Python unit suite.

Run: uv run --frozen python -B -m unittest scripts.test_job_guardian -v
"""
from __future__ import annotations

import asyncio
from contextlib import ExitStack, asynccontextmanager
from collections.abc import AsyncIterator
import fcntl
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from runtime.daemon.job_fence_observation import observe_job_fences
from runtime.daemon.job_guardian import helper_argv, launch_observed, read_record, timeout_observed, write_record
from runtime.daemon.jobs_runner import run_job
from runtime.infrastructure.database import Database
from runtime.infrastructure.org_access_fence import OrgAccessFence, org_access_key
from runtime.models import JobInterpreter, JobRecord, JobStatus, TaskRecord, TaskStatus


ROOT = Path(__file__).resolve().parents[1]


class JobGuardianTests(unittest.TestCase):
    def setUp(self) -> None:
        scratch = tempfile.TemporaryDirectory(prefix='job-budget-')
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name)
        self.home = self.root / 'daemon'
        self.org = self.root / 'runtime/orgs/scratch'
        (self.org / 'jobs').mkdir(parents=True)
        (self.home / 'org-access-fences').mkdir(parents=True, mode=0o700)

    def run_script(self, script: str, budget: int | None) -> object:
        return asyncio.run(run_job(script_text=script, interpreter='bash', cwd=str(self.org),
            stdout_path=str(self.org / 'jobs/JOB-1.out'), stderr_path=str(self.org / 'jobs/JOB-1.err'),
            max_runtime_seconds=budget, publish=lambda _event: None))

    def test_unbounded_direct_launch_and_normal_exit(self) -> None:
        result = self.run_script('echo $$; echo unbounded; exit 7', None)
        self.assertEqual((result.status, result.exit_code, result.reason), ('completed', 7, None))
        self.assertIn('unbounded', result.stdout_head)
        self.assertFalse((self.org / 'jobs/JOB-1.err.timeout.json').exists())

    def test_bounded_normal_exit_without_exhaustion(self) -> None:
        result = self.run_script('echo normal; exit 0', 20)
        self.assertEqual((result.status, result.exit_code, result.reason), ('completed', 0, None))
        self.assertFalse(timeout_observed(str(self.org / 'jobs/JOB-1.err')))

    def test_daemon_and_guardian_report_same_timeout_even_zero_exit(self) -> None:
        result = self.run_script("trap 'exit 0' TERM; while :; do sleep .1; done", 1)
        self.assertEqual((result.status, result.reason), ('failed', 'timeout'))
        self.assertTrue(timeout_observed(str(self.org / 'jobs/JOB-1.err')))

    @unittest.skipUnless(sys.platform == 'linux', 'Linux process-state observation')
    def test_background_group_remains_bounded_after_script_exit(self) -> None:
        result = self.run_script(
            "python3 -c 'import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); time.sleep(60)' & echo $!; exit 0", 1)
        self.assertEqual((result.status, result.reason), ('failed', 'timeout'))
        self.assertTrue(timeout_observed(str(self.org / 'jobs/JOB-1.err')))
        self.assertLess(result.duration_ms, 8000, 'guardian waited for the daemon fallback')
        pid = int(result.stdout_head.strip())
        try:
            state = Path(f'/proc/{pid}/stat').read_text().rpartition(')')[2].split()[0]
        except FileNotFoundError:
            state = 'absent'
        self.assertIn(state, ('absent', 'Z'))

    @unittest.skipUnless(sys.platform == 'linux', 'Linux /proc demonstration')
    def test_parent_loss_group_kill_marker_and_boot_diagnostics(self) -> None:
        # The harness owns only this scratch flock. It launches the shipping
        # guardian exactly as run_job does, then is killed without cleanup.
        fence = self.home / 'org-access-fences' / (org_access_key(self.org) + '.lock')
        err = self.org / 'jobs/JOB-1.err'
        ready = self.root / 'pids.json'
        payload = """import json,os,signal,subprocess,sys,time
signal.signal(signal.SIGTERM, signal.SIG_IGN)
child=subprocess.Popen([sys.executable,'-c','import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)'],pass_fds=(int(sys.argv[2]),))
with open(sys.argv[1],'w') as stream: json.dump([os.getpid(),child.pid],stream)
time.sleep(60)
"""
        harness = """import fcntl,json,os,subprocess,sys,time
fd=os.open(sys.argv[1],os.O_CREAT|os.O_RDWR,0o600)
fcntl.flock(fd,fcntl.LOCK_SH)
argv=[str(fd) if value=='FENCE_FD' else value for value in json.loads(sys.argv[2])]
child=subprocess.Popen(argv,pass_fds=(fd,),start_new_session=True,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
print(child.pid,flush=True)
time.sleep(60)
"""
        argv = [*helper_argv(), '--seconds', '3', '--stderr-path', str(err), '--job-id', 'JOB-1',
                '--fence-fd', 'FENCE_FD', sys.executable, '-c', payload, str(ready), 'FENCE_FD']
        parent = subprocess.Popen([sys.executable, '-c', harness, str(fence), json.dumps(argv)],
                                  stdout=subprocess.PIPE, text=True)
        self.addCleanup(parent.stdout.close)
        pgid = int(parent.stdout.readline())

        def cleanup() -> None:
            if parent.poll() is None:
                parent.kill()
                parent.wait()
            try:
                os.killpg(pgid, signal.SIGKILL)
            except ProcessLookupError:
                pass

        self.addCleanup(cleanup)
        end = time.monotonic() + 2
        while not ready.exists() and time.monotonic() < end:
            time.sleep(.02)
        self.assertTrue(ready.exists(), 'job script did not start')
        pids = json.loads(ready.read_text())
        inode = fence.stat().st_ino
        global_before = [line for line in Path('/proc/locks').read_text().splitlines() if f':{inode} ' in line]
        self.assertTrue(global_before, 'scratch flock not present before parent loss')
        # The script, not the guardian, must own the inherited fence.
        guardian_fd_locks = [p.read_text() for p in Path(f'/proc/{pgid}/fdinfo').iterdir()]
        self.assertFalse(any(f':{inode} ' in text for text in guardian_fd_locks))
        # An earlier external shutdown request must not cancel the independent
        # budget when the supervising parent then disappears.
        os.killpg(pgid, signal.SIGTERM)
        parent.kill()
        self.assertEqual(parent.wait(timeout=2), -signal.SIGKILL)
        self.assertFalse(timeout_observed(str(err)), 'parent loss consumed budget')
        holder = observe_job_fences(self.home, self.org, ['JOB-1'])
        self.assertEqual(holder.status, 'observed', holder)
        self.assertTrue({h.pid for h in holder.holders}.issuperset(pids), holder)
        self.assertTrue(all(h.pgid == pgid and h.age_seconds >= 0 for h in holder.holders))
        mixed = observe_job_fences(self.home, self.org, ['JOB-legacy', 'JOB-1'])
        self.assertEqual(mixed.status, 'unavailable')
        identity_path = Path(str(err) + '.process.json')
        identity_bytes = identity_path.read_bytes()
        identity = json.loads(identity_bytes)
        identity['start_ticks'] += 1
        identity_path.write_text(json.dumps(identity))
        reused = observe_job_fences(self.home, self.org, ['JOB-1'])
        self.assertEqual((reused.status, reused.holders), ('unavailable', []))
        identity_path.write_bytes(identity_bytes)
        mixed = observe_job_fences(self.home, self.org, ['JOB-2', 'JOB-1'])
        self.assertEqual(mixed.status, 'unavailable')
        self.assertTrue(mixed.holders, 'legacy identity hid a known blocker')

        # A separate boot observation uses real SQLite retained running rows,
        # then the actual jobs-show route and workflow hold evidence reader.
        db = Database(self.org / 'happyranch.db')
        self.addCleanup(db.close)
        db.insert_job(JobRecord(id='JOB-1', task_id='TASK-scratch', agent_name='builder',
            title='scratch budget', rationale='synthetic', script_text='sleep 60', interpreter='bash',
            status=JobStatus.RUNNING, max_runtime_seconds=3, created_at=datetime.now(timezone.utc).isoformat()))
        before = db.get_job('JOB-1').model_dump()
        self.assertEqual(db.recover_orphaned_running_jobs(now_iso='2026-01-01T00:00:00Z'), ['JOB-1'])
        from runtime.daemon.routes.jobs import get_job_route
        from runtime.daemon.org_integrity_monitor import runtime_key
        from runtime.daemon.workflow_setup_status import read_setup_status
        from runtime.daemon.routes.workflow_setup import _running_job_ids
        from runtime.daemon.job_guardian import write_record
        namespace = self.home / 'backups/workflow-sequential' / runtime_key(self.org.parent.parent) / 'scratch'
        namespace.mkdir(parents=True, mode=0o700)
        write_record(namespace / 'hold.json', dict(format='workflow-setup-hold@1',
            runtime_key=runtime_key(self.org.parent.parent), org_slug='scratch',
            reason='writer_exclusion_unproven', state='not_installed',
            attempt_time=datetime.now(timezone.utc).isoformat()))
        with patch.dict(os.environ, HAPPYRANCH_DAEMON_HOME=str(self.home)):
            self.assertIsNone(_running_job_ids(None))
            self.assertIsNone(_running_job_ids(SimpleNamespace(db=db)))
            access = OrgAccessFence.acquire(self.org, deadline=time.monotonic()+.1)
            db._org_access_fence = access
            try:
                job_ids = _running_job_ids(SimpleNamespace(db=db))
                self.assertEqual(job_ids, ['JOB-1'])
                status = asyncio.run(get_job_route('scratch', 'JOB-1', SimpleNamespace(db=db, root=self.org), has_bearer=True))
                setup = read_setup_status(self.home, self.org.parent.parent, 'scratch', job_ids=job_ids)
            finally:
                del db._org_access_fence
                access.close()
        self.assertEqual(status['liveness'], 'job_liveness_unknown')
        self.assertTrue(status['fence_observation']['holders'])
        self.assertEqual(setup.reason, 'writer_exclusion_unproven')
        self.assertIn('JOB-1 PID=', setup.writer_blockers.detail)
        self.assertIn('PGID=', setup.writer_blockers.detail)
        lsof = subprocess.run(['lsof', '-p', ','.join(map(str, pids))], capture_output=True, text=True, timeout=2)
        self.assertEqual(lsof.returncode, 0, lsof.stderr)
        held_lines = [line for line in lsof.stdout.splitlines() if str(fence) in line]
        self.assertTrue(held_lines, 'lsof did not show inherited fence after parent loss')
        print(json.dumps(dict(stage='before_budget_after_parent_SIGKILL', parent_exit=-9,
            proc_locks_before_parent_loss=global_before, lsof_exit=lsof.returncode, lsof_fence=held_lines,
            job_status=status['fence_observation'], hold_reason=setup.writer_blockers.model_dump()), sort_keys=True))
        end = time.monotonic() + 9
        while time.monotonic() < end:
            live = []
            for pid in [pgid, *pids]:
                try:
                    if Path(f'/proc/{pid}/stat').read_text().rpartition(')')[2].split()[0] != 'Z':
                        live.append(pid)
                except FileNotFoundError:
                    pass
            if not live:
                break
            time.sleep(.05)
        self.assertFalse(live, f'whole original process group survived budget: {live}')
        self.assertTrue(timeout_observed(str(err)), 'external TERM disabled the independent timeout marker')
        marker = read_record(Path(str(err) + '.timeout.json'))
        self.assertEqual((marker['reason'], marker['max_runtime_seconds']), ('timeout', 3))
        # A real nonblocking EX attempt is stronger than global lock absence.
        fd = os.open(fence, os.O_RDWR)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(fd)
        global_after = [line for line in Path('/proc/locks').read_text().splitlines() if f':{inode} ' in line]
        self.assertFalse(global_after)
        after = observe_job_fences(self.home, self.org, ['JOB-1'])
        self.assertEqual(after.exhausted_jobs, ['JOB-1'])
        self.assertEqual(db.get_job('JOB-1').model_dump(), before, 'observation rewrote retained job')
        print(json.dumps(dict(stage='after_budget', proc_locks=global_after, exclusive_probe='acquired and closed',
            marker=marker, process_states='absent or zombie; no live group members', retained_row='unchanged'), sort_keys=True))

    def test_observation_unavailable_without_proc_or_identity(self) -> None:
        write_record(self.org / 'jobs/JOB-1.err.timeout.json', {'reason': 'timeout'})
        with patch('runtime.daemon.job_fence_observation.sys.platform', 'darwin'):
            result = observe_job_fences(self.home, self.org, ['JOB-1'])
        self.assertEqual((result.status, result.holders), ('unavailable', []))
        self.assertEqual(result.exhausted_jobs, ['JOB-1'])
        self.assertIn('never proof', result.detail)
        (self.home / 'org-access-fences' / (org_access_key(self.org) + '.lock')).touch()
        missing = observe_job_fences(self.home, self.org, ['JOB-1'])
        self.assertEqual(missing.status, 'unavailable')
        self.assertIn('identity unavailable: JOB-1', missing.detail)
        exhausted = observe_job_fences(self.home, self.org, ['JOB-1'] * 1025)
        self.assertEqual(exhausted.status, 'unavailable')

    def test_frozen_helper_uses_hidden_dispatch(self) -> None:
        with patch.object(sys, 'frozen', True, create=True):
            self.assertEqual(helper_argv(), [sys.executable, '--job-guardian'])


class GuardianPersistenceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        from runtime.daemon import jobs_runner
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch.object(jobs_runner, '_JOBS_SHUTTING_DOWN', False))
        stack.enter_context(patch.object(jobs_runner, '_ORCH_RESOLVER', None))
        stack.enter_context(patch.object(jobs_runner, '_RESUME_MAIN_LOOP', None))
        for name in ('_INFLIGHT', '_RUNNER_TASKS', '_KILL_REASON_OVERRIDE'):
            stack.enter_context(patch.dict(getattr(jobs_runner, name), clear=True))
        self.scratch = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix='guardian-result-')))

    async def launch(self, org: object, job_id: str, script: str, budget: int | None) -> object:
        from runtime.daemon import jobs_runner
        from runtime.daemon.routes import jobs
        task_id = job_id.replace('JOB-', 'TASK-', 1)
        org.db.insert_task(TaskRecord(id=task_id, brief='Synthetic guardian result',
            assigned_agent='worker', team='retiring', status=TaskStatus.IN_PROGRESS,
            current_session_id='sess-synthetic-' + job_id))
        org.db.insert_job(JobRecord(id=job_id, task_id=task_id, agent_name='worker',
            title='Synthetic guardian result', rationale='Focused regression', script_text=script,
            interpreter=JobInterpreter.PYTHON3, max_runtime_seconds=budget,
            created_at='2026-11-19T00:00:00+00:00'))
        admitted = await jobs._run_job_core(org, job_id=job_id, cwd_override=None, timeout_override=None)
        self.assertEqual(admitted['status'], 'running', admitted)
        registration = jobs_runner._RUNNER_TASKS[(org.db, job_id)]
        self.addAsyncCleanup(self.reap, registration)
        return registration

    async def reap(self, registration: object) -> None:
        proc = registration.process
        if proc is not None and proc.returncode is None:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await proc.wait()
        if not registration.task.done():
            registration.task.cancel()
        await asyncio.gather(registration.task, return_exceptions=True)

    async def test_bounded_shutdown_preserves_cleanup_and_single_term(self) -> None:
        from runtime.daemon import jobs_runner
        from scripts.test_agent_retirement import fixture
        async with fixture(self.scratch / 'shutdown') as (org, _client):
            runs = []
            for job_id, budget in (('JOB-901', None), ('JOB-902', 60)):
                prefix = self.scratch / job_id
                script = f"""import signal,time
from pathlib import Path
prefix = {str(prefix)!r}
cleaning = False
def stop(sig, frame):
    global cleaning
    with open(prefix + '.terms', 'a') as stream: stream.write('TERM\\n')
    if cleaning: return
    cleaning = True
    time.sleep(8)
    Path(prefix + '.complete').touch()
    raise SystemExit(0)
signal.signal(signal.SIGTERM, stop)
Path(prefix + '.ready').touch()
while True: time.sleep(.1)
"""
                runs.append(await self.launch(org, job_id, script, budget))
            async def ready() -> None:
                while not all((self.scratch / (r.job_id + '.ready')).exists() for r in runs):
                    for r in runs:
                        if r.task.done():
                            self.fail(f'job exited before ready: {org.db.get_job(r.job_id)}')
                    await asyncio.sleep(.02)
            await asyncio.wait_for(ready(), 5)
            jobs_runner.begin_job_shutdown()
            await asyncio.wait_for(asyncio.gather(*(r.task for r in runs)), 14)
            for r in runs:
                with self.subTest(job=r.job_id):
                    row = org.db.get_job(r.job_id)
                    self.assertEqual((row.status.value, row.reason, row.exit_code),
                                     ('failed', 'daemon_shutdown', 0))
                    self.assertEqual((self.scratch / (r.job_id + '.terms')).read_text(), 'TERM\n')
                    self.assertTrue((self.scratch / (r.job_id + '.complete')).exists())
                    self.assertFalse(timeout_observed(row.stderr_path))

    async def test_helper_launch_failures_persist_failed_and_script_exits_complete(self) -> None:
        from scripts.test_agent_retirement import fixture
        # Faults exist only in the real helper's child interpreter. Production
        # argv, launch, output pumps, terminal CAS and audit writer still run.
        wrapper = """import errno,os,runpy,stat,subprocess,sys
from unittest.mock import patch
fault = sys.argv.pop(1)
helper = sys.argv.pop(1)
original = os.open
original_fsync = os.fsync
launch_fd = parent_fd = None
def admission_open(path, *args, **kwargs):
    global launch_fd, parent_fd
    if str(path).endswith('.' + fault + '.json'):
        raise OSError(errno.ENOSPC, 'synthetic guardian ' + fault)
    if launch_fd is not None and args[0] & os.O_DIRECTORY:
        if fault == 'launch_dir_open':
            raise OSError(errno.EIO, 'synthetic guardian ' + fault)
        parent_fd = original(path, *args, **kwargs)
        return parent_fd
    fd = original(path, *args, **kwargs)
    if str(path).endswith('.launch.json'):
        launch_fd = fd
    return fd
def admission_fsync(fd):
    if (fault == 'launch_file_fsync' and fd == launch_fd
            and stat.S_ISREG(os.fstat(fd).st_mode)) or (fault == 'launch_dir_fsync' and fd == parent_fd):
        raise OSError(errno.EIO, 'synthetic guardian ' + fault)
    return original_fsync(fd)
with patch('os.open', side_effect=admission_open), patch('os.fsync', side_effect=admission_fsync):
    if fault == 'popen':
        with patch('subprocess.Popen', side_effect=OSError(errno.EAGAIN, 'synthetic guardian popen')):
            runpy.run_path(helper, run_name='__main__')
    else:
        runpy.run_path(helper, run_name='__main__')
"""
        publication_faults = ('launch_file_fsync', 'launch_dir_open', 'launch_dir_fsync')
        for fault in ('timeout', 'process', 'popen', *publication_faults, None):
            with self.subTest(fault=fault):
                async with fixture(self.scratch / str(fault)) as (org, _client):
                    sentinel = self.scratch / (str(fault) + '.ran')
                    script = f'from pathlib import Path\nPath({str(sentinel)!r}).touch()\nraise SystemExit(1)\n'
                    if fault in publication_faults:
                        # Keep the real script alive until helper admission
                        # fails, without relying on process scheduling order.
                        script = ('import time\nfrom pathlib import Path\n'
                                  f'while not Path({str(sentinel) + ".release"!r}).exists(): time.sleep(.01)\n'
                                  + script)
                    with ExitStack() as stack:
                        if fault is not None:
                            stack.enter_context(patch('runtime.daemon.jobs_runner.helper_argv', return_value=[
                                sys.executable, '-c', wrapper, fault, str(ROOT / 'runtime/daemon/job_guardian.py')]))
                        r = await self.launch(org, 'JOB-1', script, 60)
                        await asyncio.wait_for(asyncio.shield(r.task), 8)
                    row = org.db.get_job('JOB-1')
                    audits = org.db.execute("SELECT action,payload FROM audit_log WHERE action IN "
                        "('job_run_failed','job_run_completed') ORDER BY id").fetchall()
                    print(json.dumps(dict(fault=fault, status=row.status.value, reason=row.reason,
                        exit_code=row.exit_code, launch_record=launch_observed(row.stderr_path),
                        script_completed=sentinel.exists(), audit_actions=[a['action'] for a in audits])))
                    self.assertEqual(len(audits), 1)
                    if fault is None:
                        self.assertEqual((row.status.value, row.reason, row.exit_code), ('completed', None, 1))
                        self.assertTrue(sentinel.exists())
                        self.assertEqual(audits[0]['action'], 'job_run_completed')
                    else:
                        if fault in publication_faults:
                            self.assertTrue(launch_observed(row.stderr_path), 'fault did not follow JSON flush')
                        self.assertFalse(sentinel.exists(), 'script completed despite helper admission failure')
                        self.assertIn('synthetic guardian ' + fault, row.stderr_head)
                        self.assertIn('synthetic guardian ' + fault, Path(row.stderr_path).read_text())
                        self.assertEqual((row.status.value, row.reason), ('failed', 'spawn_failed'))
                        self.assertEqual(audits[0]['action'], 'job_run_failed')
                        self.assertEqual(json.loads(audits[0]['payload'])['reason'], 'spawn_failed')


@unittest.skipUnless(sys.platform == 'linux', 'Linux retained-job death proof')
class RetainedJobStopTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        import ctypes
        from runtime.daemon import jobs_runner
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(jobs_runner, '_JOBS_SHUTTING_DOWN', False))
        self.stack.enter_context(patch.object(jobs_runner, '_ORCH_RESOLVER', None))
        self.stack.enter_context(patch.object(jobs_runner, '_RESUME_MAIN_LOOP', None))
        for name in ('_INFLIGHT', '_RUNNER_TASKS', '_KILL_REASON_OVERRIDE'):
            self.stack.enter_context(patch.dict(getattr(jobs_runner, name), clear=True))
        self.scratch = Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='retained-stop-')))
        self.reaped: set[int] = set()
        # Adopt/reap only this test's orphaned children, so zombies cannot make
        # a positive process-group absence assertion pass accidentally.
        libc = ctypes.CDLL(None, use_errno=True)
        old = ctypes.c_int()
        self.assertEqual(libc.prctl(37, ctypes.byref(old), 0, 0, 0), 0)
        self.assertEqual(libc.prctl(36, 1, 0, 0, 0), 0)
        self.addCleanup(lambda: libc.prctl(36, old.value, 0, 0, 0))

    async def wait_file(self, path: Path) -> None:
        async def wait() -> None:
            while not path.exists():
                await asyncio.sleep(.01)
        await asyncio.wait_for(wait(), 10)

    async def reap_pid(self, pid: int) -> None:
        if pid in self.reaped:
            return
        async def wait() -> None:
            while True:
                try:
                    if os.waitpid(pid, os.WNOHANG)[0] == pid:
                        return
                except ChildProcessError:
                    if not Path(f'/proc/{pid}').exists():
                        return
                await asyncio.sleep(.01)
        await asyncio.wait_for(wait(), 5)
        self.reaped.add(pid)

    @asynccontextmanager
    async def retained(self, *, member: bool = False, separate_group: bool = False) -> AsyncIterator[tuple[object, object, dict, Path]]:
        from runtime.config import Settings
        from runtime.daemon.app import create_app
        from runtime.daemon.state import DaemonState
        from runtime.runtime import RuntimeDir
        import httpx
        root = self.scratch / str(time.monotonic_ns())
        ready = root.with_suffix('.ready')
        script = f"""import json,os,signal,time
from pathlib import Path
signal.signal(signal.SIGTERM, lambda *_: Path({str(ready) + '.signalled'!r}).touch())
child = os.fork() if {member!r} else 0
if {member!r} and not child and {separate_group!r}: os.setpgid(0, 0)
if not {member!r} or child:
    Path({str(ready)!r}).write_text(json.dumps(dict(pid=os.getpid(), member=child)))
while True: time.sleep(.1)
"""
        harness = r"""import asyncio,sys
from pathlib import Path
from scripts.test_agent_retirement import fixture
from runtime.daemon.routes.jobs import _run_job_core
from runtime.models import BlockKind,JobRecord,TaskRecord,TaskStatus
async def main():
    async with fixture(Path(sys.argv[1])) as (org, client):
        org.db.insert_task(TaskRecord(id='TASK-901', brief='Synthetic retained job waiter',
            assigned_agent='worker', team='retiring', status=TaskStatus.IN_PROGRESS,
            current_session_id='sess-synthetic-retained'))
        org.db.insert_job(JobRecord(id='JOB-901', task_id='TASK-901', agent_name='worker',
            title='Synthetic retained job', rationale='Focused restart regression', script_text=sys.argv[2], interpreter='python3',
            persistent=True, created_at='2026-11-19T00:00:00+00:00'))
        await _run_job_core(org, job_id='JOB-901', cwd_override=None, timeout_override=None)
        org.db.update_task('TASK-901', block_kind=BlockKind.BLOCKED_ON_JOB,
            blocked_on_job_ids='["JOB-901"]')
        await asyncio.Event().wait()
asyncio.run(main())
"""
        log = self.stack.enter_context((self.scratch / (root.name + '.log')).open('w+'))
        daemon = subprocess.Popen([sys.executable, '-B', '-c', harness, str(root), script],
            cwd=ROOT, stdout=log, stderr=log)
        pids = None
        state = None
        try:
            try:
                await self.wait_file(ready)
            except TimeoutError:
                log.flush()
                log.seek(0)
                self.fail('synthetic launcher failed: ' + log.read())
            pids = json.loads(ready.read_text())
            daemon.kill()  # Actual supervising process/registration loss.
            self.assertEqual(daemon.wait(timeout=5), -signal.SIGKILL)
            with patch.dict(os.environ, HAPPYRANCH_DAEMON_HOME=str(root / 'home')):
                state = DaemonState.from_runtime(RuntimeDir(root / 'runtime'), Settings())
                org = state.orgs['alpha']
                self.assertEqual(org.db.recover_orphaned_running_jobs(now_iso='2026-11-19T01:00:00Z'), ['JOB-901'])
                org.orchestrator._queue = state.queue
                org.orchestrator._main_loop = asyncio.get_running_loop()
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(state)),
                        base_url='http://synthetic.test', headers={'Authorization': 'Bearer synthetic-founder-token'}) as client:
                    shown = await client.get('/api/v1/orgs/alpha/jobs/JOB-901')
                    self.assertEqual(shown.json()['liveness'], 'job_liveness_unknown', shown.text)
                    yield org, client, pids, ready
        finally:
            if daemon.poll() is None:
                daemon.kill()
                daemon.wait(timeout=5)
            if pids is not None:
                for pid in (pids['pid'], pids['member']):
                    if not pid or pid in self.reaped:
                        continue
                    # waitpid proves this exact child is still ours before any
                    # cleanup signal; never target a reaped/reused numeric ID.
                    try:
                        if os.waitpid(pid, os.WNOHANG)[0] == 0:
                            os.kill(pid, signal.SIGKILL)
                            await self.reap_pid(pid)
                    except ChildProcessError:
                        pass
            if state is not None:
                await state.close_all()

    async def stop(self, org: object, client: object, *, accepted: bool) -> object:
        before = org.db.get_job('JOB-901').model_dump()
        journals = [tuple(r) for r in org.db.execute('SELECT * FROM task_pause_controls').fetchall()]
        with patch('os.killpg', wraps=os.killpg) as group_signal, patch('os.kill', wraps=os.kill) as pid_signal:
            result = await client.post('/api/v1/orgs/alpha/jobs/JOB-901/stop')
            group_signal.assert_not_called()
            pid_signal.assert_not_called()
        row = org.db.get_job('JOB-901')
        print(json.dumps(dict(route='POST /api/v1/orgs/alpha/jobs/JOB-901/stop', http=result.status_code,
            response=result.json(), job_status=row.status.value, reason=row.reason, exit_code=row.exit_code,
            owner_queue_size=org.orchestrator._queue._queue.qsize(), signalled=False), sort_keys=True))
        self.assertEqual([tuple(r) for r in org.db.execute('SELECT * FROM task_pause_controls').fetchall()], journals)
        if accepted:
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual((row.status.value, row.reason, row.exit_code), ('failed', 'orphaned_process_dead', None))
            self.assertIsNotNone(row.finished_at)
            queue = org.orchestrator._queue._queue
            self.assertEqual(queue.qsize(), 1, 'existing terminal path did not wake owner')
            audits = org.db.execute("SELECT task_id,agent,payload FROM audit_log WHERE action='job_run_failed'").fetchall()
            self.assertEqual(len(audits), 1)
            self.assertEqual(tuple(audits[0][:2]), ('TASK-901', 'founder'))
            self.assertEqual(json.loads(audits[0]['payload']), dict(script_request_id='JOB-901',
                exit_code=None, duration_ms=None, reason='orphaned_process_dead'))
            again = await client.post('/api/v1/orgs/alpha/jobs/JOB-901/stop')
            self.assertEqual(again.status_code, 409)
            self.assertEqual(again.json()['detail']['code'], 'not_running')
            self.assertEqual(queue.qsize(), 1)
        else:
            self.assertEqual(result.status_code, 409, result.text)
            self.assertEqual(result.json()['detail']['code'], 'job_liveness_unknown')
            self.assertEqual(row.model_dump(), before)
            self.assertEqual(org.orchestrator._queue._queue.qsize(), 0)
            self.assertFalse(org.db.execute("SELECT 1 FROM audit_log WHERE action='job_run_failed'").fetchall())
        return result

    async def test_dead_group_stop_terminalizes_and_wakes_owner(self) -> None:
        async with self.retained() as (org, client, pids, _ready):
            os.killpg(pids['pid'], signal.SIGKILL)
            await self.reap_pid(pids['pid'])
            await self.stop(org, client, accepted=True)

    async def test_alive_group_stop_refuses_without_signalling(self) -> None:
        async with self.retained() as (org, client, pids, ready):
            await self.stop(org, client, accepted=False)
            self.assertTrue(Path(f"/proc/{pids['pid']}").exists())
            self.assertFalse(Path(str(ready) + '.signalled').exists())

    async def test_dead_leader_surviving_member_refuses(self) -> None:
        for separate_group in (False, True):
            with self.subTest(separate_group=separate_group):
                async with self.retained(member=True, separate_group=separate_group) as (org, client, pids, _ready):
                    if separate_group:
                        async def changed_group() -> None:
                            while os.getpgid(pids['member']) != pids['member']:
                                await asyncio.sleep(.01)
                        await asyncio.wait_for(changed_group(), 2)
                    os.kill(pids['pid'], signal.SIGKILL)
                    await self.reap_pid(pids['pid'])
                    self.assertTrue(Path(f"/proc/{pids['member']}").exists())
                    await self.stop(org, client, accepted=False)
                    # A member that moved groups still belongs to the old SID.
                    if separate_group:
                        os.kill(pids['member'], signal.SIGKILL)
                        await self.reap_pid(pids['member'])

    async def test_missing_invalid_identity_and_unavailable_fence_refuse(self) -> None:
        async with self.retained() as (org, client, pids, _ready):
            os.killpg(pids['pid'], signal.SIGKILL)
            await self.reap_pid(pids['pid'])
            path = org.root / 'jobs/JOB-901.err.process.json'
            original = path.read_bytes()
            path.unlink()  # Identity sidecar only; never a fence or hold.
            await self.stop(org, client, accepted=False)
            for changed in ({'job_id': 'JOB-902'}, {'start_ticks': None}, {'pid': True},
                            {'boot_id': 'invalid'}, {'started_at': 'invalid'}):
                with self.subTest(identity=changed):
                    path.write_text(json.dumps(json.loads(original) | changed))
                    await self.stop(org, client, accepted=False)
            path.write_bytes(original)
            with patch('runtime.daemon.job_death.sys.platform', 'darwin'):
                await self.stop(org, client, accepted=False)
            with patch('runtime.daemon.job_death.Path.iterdir', side_effect=OSError('synthetic census I/O')):
                await self.stop(org, client, accepted=False)
            with patch('runtime.daemon.job_death.Path.iterdir', side_effect=lambda *_:
                       iter(Path('/proc') / f'entry-{i}' for i in range(65537))):
                await self.stop(org, client, accepted=False)
            with patch('runtime.daemon.job_fence_observation._read', side_effect=PermissionError('synthetic proc denial')):
                await self.stop(org, client, accepted=False)

    async def test_reused_pid_needs_empty_group_and_observed_fence(self) -> None:
        from runtime.daemon.job_guardian import process_identity
        async with self.retained() as (org, client, pids, _ready):
            os.killpg(pids['pid'], signal.SIGKILL)
            await self.reap_pid(pids['pid'])
            # Real unrelated process with a different PGID/SID, synthetic old
            # start pin: deterministic PID-reuse evidence without forcing reuse.
            unrelated = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
            try:
                identity = process_identity(unrelated.pid)
                identity.update(job_id='JOB-901', pgid=unrelated.pid, sid=unrelated.pid,
                                start_ticks=identity['start_ticks'] - 1)
                path = org.root / 'jobs/JOB-901.err.process.json'
                path.write_text(json.dumps(identity))
                with patch('runtime.daemon.job_fence_observation._read', side_effect=OSError('synthetic unavailable')):
                    await self.stop(org, client, accepted=False)
                await self.stop(org, client, accepted=True)
                self.assertIsNone(unrelated.poll(), 'reused unrelated PID was signalled')
            finally:
                unrelated.kill()
                unrelated.wait(timeout=5)

    async def test_final_proof_recheck_and_audit_rollback(self) -> None:
        from runtime.daemon.job_death import prove_job_dead
        async with self.retained() as (org, client, pids, _ready):
            os.killpg(pids['pid'], signal.SIGKILL)
            await self.reap_pid(pids['pid'])
            path = org.root / 'jobs/JOB-901.err.process.json'
            original = path.read_bytes()
            calls = 0

            def change_identity(*args: object) -> dict | None:
                nonlocal calls
                calls += 1
                if calls == 2:
                    identity = json.loads(original)
                    identity['start_ticks'] += 1
                    path.write_text(json.dumps(identity))
                return prove_job_dead(*args)

            with patch('runtime.daemon.job_death.prove_job_dead', side_effect=change_identity):
                await self.stop(org, client, accepted=False)
            self.assertEqual(calls, 2, 'final proof was not recollected')
            path.write_bytes(original)
            before = org.db.get_job('JOB-901').model_dump()
            with patch.object(org.db, 'insert_audit_log_uncommitted', side_effect=OSError('synthetic audit failure')):
                with self.assertRaisesRegex(OSError, 'synthetic audit failure'):
                    await client.post('/api/v1/orgs/alpha/jobs/JOB-901/stop')
            self.assertEqual(org.db.get_job('JOB-901').model_dump(), before)
            self.assertEqual(org.orchestrator._queue._queue.qsize(), 0)
            await self.stop(org, client, accepted=True)

    async def test_prior_boot_and_concurrent_stop_single_terminal(self) -> None:
        import uuid
        async with self.retained() as (org, client, pids, _ready):
            os.killpg(pids['pid'], signal.SIGKILL)
            await self.reap_pid(pids['pid'])
            path = org.root / 'jobs/JOB-901.err.process.json'
            path.write_text(json.dumps(json.loads(path.read_text()) | {'boot_id': str(uuid.uuid4())}))
            responses = await asyncio.gather(*(client.post('/api/v1/orgs/alpha/jobs/JOB-901/stop') for _ in range(2)))
            self.assertEqual(sorted(r.status_code for r in responses), [200, 409])
            self.assertEqual(org.db.get_job('JOB-901').reason, 'orphaned_process_dead')
            self.assertEqual(org.orchestrator._queue._queue.qsize(), 1)
            self.assertEqual(len(org.db.execute("SELECT 1 FROM audit_log WHERE action='job_run_failed'").fetchall()), 1)


if __name__ == '__main__':
    unittest.main()
