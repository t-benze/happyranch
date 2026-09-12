from __future__ import annotations

import subprocess
import os
import json
import sys
import stat
from pathlib import Path

import pytest

from tests import thr211_containment as containment

from tests.thr211_containment import (
    build_review_required_job_script,
    plan_environment,
    prepare_private_test_paths,
)


def test_private_plan_paths_are_fresh_private_and_explicit(tmp_path: Path) -> None:
    paths = prepare_private_test_paths(tmp_path / "candidate")

    assert paths.plans.is_dir()
    assert paths.artifacts.is_dir()
    assert plan_environment(paths) == {"HAPPYRANCH_TEST_PLAN_DIR": str(paths.plans)}
    assert paths.plans.stat().st_mode & 0o077 == 0


def test_private_plan_paths_refuse_existing_root(tmp_path: Path) -> None:
    root = tmp_path / "candidate"
    root.mkdir()

    with pytest.raises(FileExistsError):
        prepare_private_test_paths(root)


def test_private_plan_paths_refuse_symlink_ancestor_without_touching_target(tmp_path: Path) -> None:
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(foreign, target_is_directory=True)

    with pytest.raises(OSError):
        prepare_private_test_paths(alias / "candidate")

    assert list(foreign.iterdir()) == []


def test_private_plan_paths_refuse_nested_symlink_ancestor_without_touching_target(tmp_path: Path) -> None:
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "alias").symlink_to(foreign, target_is_directory=True)

    with pytest.raises(OSError):
        prepare_private_test_paths(nested / "alias" / "candidate")

    assert list(foreign.iterdir()) == []


def test_private_plan_paths_refuse_non_directory_ancestor(tmp_path: Path) -> None:
    ancestor = tmp_path / "not-a-directory"
    ancestor.write_text("foreign")

    with pytest.raises(NotADirectoryError):
        prepare_private_test_paths(ancestor / "candidate")


def test_review_required_job_script_is_exclusive_and_private(tmp_path: Path) -> None:
    paths = prepare_private_test_paths(tmp_path / "candidate with spaces")
    sentinel = paths.plans / "job's sentinel"
    script = build_review_required_job_script(sentinel)
    assert subprocess.run(["bash", "-c", script], capture_output=True).returncode == 0
    assert sentinel.stat().st_mode & 0o077 == 0
    inode = sentinel.stat().st_ino
    assert subprocess.run(["bash", "-c", script], capture_output=True).returncode != 0
    assert sentinel.stat().st_ino == inode


def test_review_required_job_script_refuses_foreign_symlink(tmp_path: Path) -> None:
    paths = prepare_private_test_paths(tmp_path / "candidate")
    foreign = tmp_path / "foreign"
    foreign.write_text("do not alter")
    sentinel = paths.plans / "sentinel"
    sentinel.symlink_to(foreign)
    assert subprocess.run(
        ["bash", "-c", build_review_required_job_script(sentinel)], capture_output=True
    ).returncode != 0
    assert foreign.read_text() == "do not alter"


@pytest.mark.parametrize("family", ["threads", "thread_reply", "review_required", "persistent", "blocked_multi", "blocked_autonomous", "content"])
def test_shipping_plan_builder_exists(family: str) -> None:
    assert callable(getattr(containment, f"build_{family}_plan", None))


def test_private_paths_compensate_partial_acquisition(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    real_mkdir = os.mkdir

    def fail_artifacts(path: str, *args: object, **kwargs: object) -> None:
        if path == "artifacts":
            raise OSError("primary artifacts acquisition")
        real_mkdir(path, *args, **kwargs)

    monkeypatch.setattr(os, "mkdir", fail_artifacts)
    root = tmp_path / "partial"
    with pytest.raises(OSError, match="primary artifacts acquisition"):
        prepare_private_test_paths(root)
    assert not root.exists()


# These stubs record the real generated shell's callback files, never talk to a
# daemon, and leave the submitted job bodies for independent execution below.
_TOOL_STUB = r'''
import json, os, pathlib, signal, subprocess, sys
name = pathlib.Path(sys.argv[0]).name
args = sys.argv[1:]
root = pathlib.Path(os.environ['CONTROL_ROOT'])
receipt = root / 'calls.jsonl'
calls = [json.loads(line) for line in receipt.read_text().splitlines()] if receipt.exists() else []
if name in ('happyranch', 'curl'):
    source = pathlib.Path(args[args.index('--from-file') + 1] if name == 'happyranch' else args[args.index('-d') + 1][1:])
    info = source.lstat()
    payload = json.loads(source.read_text())
    entry = dict(tool=name, args=args, path=str(source), payload=payload,
                 mode=info.st_mode, uid=info.st_uid, inode=info.st_ino,
                 parent_mode=source.parent.stat().st_mode)
    with receipt.open('a') as stream:
        stream.write(json.dumps(entry) + '\n')
    number = len(calls) + 1
    if str(number) == os.environ.get('ABORT_CALLBACK'):
        os.kill(os.getppid(), signal.SIGTERM)
    if str(number) == os.environ.get('FAIL_CALLBACK'):
        print('controlled primary callback error', file=sys.stderr)
        sys.exit(37)
    if args[:2] == ['jobs', 'submit']:
        count = sum(c['args'][:2] == ['jobs', 'submit'] for c in calls) + 1
        print(f'ok: submitted JOB-{count:03d} (status=pending).')
    sys.exit(0)
if name == 'sleep':
    with (root / 'sleeps.jsonl').open('a') as stream:
        stream.write(json.dumps(args) + '\n')
    if args == ['0.1']:
        stop = pathlib.Path(os.environ['CONTROL_PLANS']) / 'founder-acted.txt'
        with stop.open('x'):
            stop.chmod(0o600)
    sys.exit(0)
if name in ('rm', 'rmdir'):
    with (root / 'cleanup.jsonl').open('a') as stream:
        stream.write(json.dumps(dict(tool=name, args=args)) + '\n')
    if os.environ.get('FAIL_CLEANUP') == '1':
        print('controlled secondary ' + name, file=sys.stderr)
        sys.exit(51 if name == 'rm' else 52)
os.execv('/bin/' + name, [name, *args])
'''


class PlanRun:
    def __init__(self, root: Path, family: str) -> None:
        root.mkdir(mode=0o700)
        self.root = root
        self.family = family
        self.paths = prepare_private_test_paths(root / "owned root's space")
        self.bin = root / 'bin'
        self.bin.mkdir(mode=0o700)
        for tool in ('happyranch', 'curl', 'sleep', 'rm', 'rmdir'):
            executable = self.bin / tool
            executable.write_text(f'#!{sys.executable}\n' + _TOOL_STUB)
            executable.chmod(0o700)
        self.env = {
            'PATH': f'{self.bin}:{Path(sys.executable).parent}:/usr/bin:/bin',
            'CONTROL_ROOT': str(root), 'CONTROL_PLANS': str(self.paths.plans),
            'LANG': 'C.UTF-8',
        }
        for variable in ('HOME', 'XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME',
                         'XDG_STATE_HOME', 'XDG_RUNTIME_DIR', 'TMPDIR', 'HAPPYRANCH_DAEMON_HOME'):
            directory = root / variable
            directory.mkdir(mode=0o700)
            self.env[variable] = str(directory)
        daemon = Path(self.env['HAPPYRANCH_DAEMON_HOME'])
        (daemon / 'daemon.port').write_text('1')
        (daemon / 'daemon.token').write_text('synthetic-not-a-credential')
        self.script = getattr(containment, f'build_{family}_plan')(self.paths.plans)
        self.unrelated = self.paths.plans / 'unrelated-sentinel'
        self.unrelated.write_text('unrelated')
        self.unrelated_inode = self.unrelated.stat().st_ino

    def run(self, agent: str = 'engineering_head', **controls: str) -> subprocess.CompletedProcess:
        result = subprocess.run(
            ['bash', '-c', self.script, 'plan', 'TASK-042', "session's space", agent, 'test', 'reply'],
            cwd=self.root, env=self.env | controls, capture_output=True, text=True, timeout=15,
        )
        assert self.unrelated.read_text() == 'unrelated'
        assert self.unrelated.stat().st_ino == self.unrelated_inode
        return result

    def calls(self) -> list[dict]:
        receipt = self.root / 'calls.jsonl'
        return [json.loads(line) for line in receipt.read_text().splitlines()] if receipt.exists() else []

    def assert_paths(self) -> None:
        for call in self.calls():
            assert Path(call['path']).is_relative_to(self.paths.plans)
            assert stat.S_ISREG(call['mode'])
            assert call['mode'] & 0o777 == 0o600
            assert call['parent_mode'] & 0o777 == 0o700
            assert call['uid'] == os.getuid()
        assert not list(self.paths.plans.glob('invocation-*'))


@pytest.mark.parametrize('family,stages,jobs', [
    ('threads', ['engineering_head'], 0),
    ('thread_reply', ['payment_agent'], 0),
    ('review_required', ['engineering_head'], 1),
    ('persistent', ['engineering_head'], 1),
    ('blocked_multi', ['engineering_head', 'engineering_head'], 2),
    ('blocked_autonomous', ['engineering_head', 'engineering_head'], 1),
    ('content', ['content_manager', 'content_writer', 'content_manager', 'content_qa', 'content_manager'], 0),
])
def test_actual_plans_duplicate_ids_private_roots_and_submitted_jobs(
    tmp_path: Path, family: str, stages: list[str], jobs: int,
) -> None:
    roots = [PlanRun(tmp_path / f"run {n}'s", family) for n in range(2)]
    for plan in roots:
        for agent in stages:
            result = plan.run(agent)
            assert result.returncode == 0, result.stderr
        calls = plan.calls()
        submits = [c for c in calls if c['args'][:2] == ['jobs', 'submit']]
        assert len(submits) == jobs
        for submitted in submits:
            payload = submitted['payload']
            assert payload['task_id'] == 'TASK-042'
            assert payload['session_id'] == "session's space"
            assert payload['interpreter'] == 'bash'
            assert payload['review_required'] is (family in ('review_required', 'blocked_multi'))
            if family != 'review_required':
                assert payload['persistent'] is (family == 'persistent')
            job = subprocess.run(['bash', '-c', payload['script']], cwd=plan.root,
                                 env=plan.env, capture_output=True, text=True, timeout=5)
            assert job.returncode == 0, job.stderr
            if family == 'review_required':
                sentinel = plan.paths.plans / 'happyranch-job-e2e-sentinel'
                info = sentinel.stat()
                assert sentinel.read_bytes() == b''
                assert info.st_uid == os.getuid() and info.st_mode & 0o777 == 0o600
            else:
                expected = {'persistent': 'starting', 'blocked_autonomous': 'autonomous-job-ran'}
                assert job.stdout.strip() == expected.get(family, 'job-a-ran' if submitted == submits[0] else 'job-b-ran')
        completions = [c['payload'] for c in calls if c['tool'] == 'curl' or c['args'][0] == 'report-completion']
        if family.startswith('blocked_'):
            assert completions[0] == {
                'session_id': "session's space", 'agent': 'engineering_head', 'status': 'blocked',
                'confidence': 0, 'risks_flagged': [], 'dependencies': [], 'suggested_reviewer_focus': [],
                'output_summary': 'Waiting for JOB-001 and JOB-002 before proceeding.' if jobs == 2 else 'Waiting for JOB-001 to finish before proceeding.',
                'waiting_on_job_ids': ['JOB-001', 'JOB-002'] if jobs == 2 else ['JOB-001'],
            }
            assert completions[1]['status'] == 'completed'
            assert json.loads(completions[1]['summary'])['action'] == 'done'
            assert (plan.paths.plans / 'invocation_counter').read_text().strip() == '2'
            if jobs == 2:
                assert [(plan.paths.plans / f'job_{suffix}.id').read_text().strip() for suffix in ('a', 'b')] == ['JOB-001', 'JOB-002']
                assert submits[0]['path'] != submits[1]['path']
        elif family == 'content':
            assert [p.get('decision', {}).get('action') for p in completions] == ['delegate', None, 'delegate', None, 'done']
            assert completions[0]['decision']['agent'] == 'content_writer'
            assert completions[2]['decision']['agent'] == 'content_qa'
            assert completions[3]['summary'] == 'VERDICT: PASS - content is accurate'
            assert (plan.paths.plans / 'cm_step.txt').read_text().strip() == '3'
        elif family == 'thread_reply':
            assert calls[0]['payload'] == {'thread_id': 'TASK-042', 'invocation_token': "session's space", 'speaker': 'payment_agent', 'body_markdown': 'got it', 'in_response_to_seq': 1}
        elif family == 'threads':
            assert calls[0]['payload'] == {'composer': 'engineering_head', 'subject': 'int test loop in', 'recipients': ['payment_agent'], 'body_markdown': 'looping payment_agent in'}
            assert completions[0]['summary'] == 'composed thread'
        elif family == 'review_required':
            assert completions[0] == {'task_id': 'TASK-042', 'session_id': "session's space", 'agent': 'engineering_head', 'status': 'blocked', 'summary': 'Awaiting JOB-001', 'confidence': 50, 'risks_flagged': [], 'dependencies': [], 'suggested_reviewer_focus': []}
        else:
            assert json.loads(completions[0]['summary']) == {'action': 'done', 'summary': 'loop launched, founder stopped'}
            sleeps = [json.loads(line) for line in (plan.root / 'sleeps.jsonl').read_text().splitlines()]
            assert ['0.1'] in sleeps and ['60'] in sleeps
        plan.assert_paths()
    assert {c['path'] for c in roots[0].calls()}.isdisjoint(c['path'] for c in roots[1].calls())


@pytest.mark.parametrize('family,agent,callback', [
    ('threads', 'engineering_head', 1), ('threads', 'engineering_head', 2),
    ('thread_reply', 'payment_agent', 1),
    ('review_required', 'engineering_head', 1), ('review_required', 'engineering_head', 2),
    ('persistent', 'engineering_head', 1), ('persistent', 'engineering_head', 2),
    ('blocked_multi', 'engineering_head', 1), ('blocked_multi', 'engineering_head', 2), ('blocked_multi', 'engineering_head', 3),
    ('blocked_autonomous', 'engineering_head', 1), ('blocked_autonomous', 'engineering_head', 2),
    ('content', 'content_manager', 1), ('content', 'content_writer', 1), ('content', 'content_qa', 1),
])
@pytest.mark.parametrize('control,exit_code', [('FAIL_CALLBACK', 37), ('ABORT_CALLBACK', 143)])
def test_actual_plan_callback_failure_and_abort_cleanup(
    tmp_path: Path, family: str, agent: str, callback: int, control: str, exit_code: int,
) -> None:
    plan = PlanRun(tmp_path / 'run', family)
    result = plan.run(agent, **{control: str(callback)})
    assert result.returncode == exit_code, result.stderr
    assert len(plan.calls()) == callback
    plan.assert_paths()


@pytest.mark.parametrize('control,primary', [({}, 0), ({'FAIL_CALLBACK': '2'}, 37), ({'ABORT_CALLBACK': '2'}, 143)])
def test_plan_preserves_primary_and_every_cleanup_error(tmp_path: Path, control: dict, primary: int) -> None:
    plan = PlanRun(tmp_path / 'run', 'review_required')
    result = plan.run(FAIL_CLEANUP='1', **control)
    assert result.returncode == (primary or 1)
    assert result.stderr.count(f'primary={primary}') == 4  # submit, log, completion, directory
    assert result.stderr.count('exit=51') == 3
    assert result.stderr.count('exit=52') == 1
    cleanup = [json.loads(line) for line in (plan.root / 'cleanup.jsonl').read_text().splitlines()]
    assert [c['tool'] for c in cleanup] == ['rm', 'rm', 'rm', 'rmdir']
    assert all(Path(c['args'][-1]).is_relative_to(plan.paths.plans) for c in cleanup)


@pytest.mark.parametrize('kind', ['file', 'symlink', 'old'])
def test_submitted_review_job_rejects_foreign_or_old_sentinel(tmp_path: Path, kind: str) -> None:
    plan = PlanRun(tmp_path / 'run', 'review_required')
    assert plan.run().returncode == 0
    script = plan.calls()[0]['payload']['script']
    sentinel = plan.paths.plans / 'happyranch-job-e2e-sentinel'
    foreign = plan.root / 'foreign'
    foreign.write_text('foreign contents')
    if kind == 'symlink':
        sentinel.symlink_to(foreign)
    elif kind == 'file':
        sentinel.write_text('foreign file')
    else:
        assert subprocess.run(['bash', '-c', script], env=plan.env).returncode == 0
    before = (sentinel.lstat(), sentinel.read_bytes(), foreign.stat(), foreign.read_bytes())
    result = subprocess.run(['bash', '-c', script], env=plan.env, capture_output=True)
    assert result.returncode != 0
    assert before == (sentinel.lstat(), sentinel.read_bytes(), foreign.stat(), foreign.read_bytes())


@pytest.mark.parametrize('name', ['job-submitted.txt', 'founder-acted.txt'])
@pytest.mark.parametrize('kind', ['file', 'symlink'])
def test_persistent_plan_rejects_old_readiness_before_submission(tmp_path: Path, name: str, kind: str) -> None:
    plan = PlanRun(tmp_path / 'run', 'persistent')
    foreign = plan.root / 'foreign'
    foreign.write_text('foreign')
    sentinel = plan.paths.plans / name
    if kind == 'symlink':
        sentinel.symlink_to(foreign)
    else:
        sentinel.write_text('old')
    before = sentinel.lstat().st_ino, sentinel.read_bytes(), foreign.stat().st_ino
    assert plan.run().returncode != 0
    assert not plan.calls()
    assert before == (sentinel.lstat().st_ino, sentinel.read_bytes(), foreign.stat().st_ino)
    assert foreign.read_text() == 'foreign'
    plan.assert_paths()


@pytest.mark.parametrize('kind', ['root-symlink', 'root-dangling', 'ancestor-dangling', 'deep-ancestor', 'root-file'])
def test_private_paths_negative_ancestry_preserves_foreign(tmp_path: Path, kind: str) -> None:
    foreign = tmp_path / 'foreign'
    foreign.mkdir()
    sentinel = foreign / 'sentinel'
    sentinel.write_text('foreign')
    root = tmp_path / 'candidate'
    if kind == 'root-file':
        root.write_text('foreign root')
    elif kind.startswith('root-'):
        root.symlink_to(foreign if kind == 'root-symlink' else tmp_path / 'absent')
    elif kind == 'ancestor-dangling':
        alias = tmp_path / 'alias'
        alias.symlink_to(tmp_path / 'absent')
        root = alias / 'candidate'
    else:
        deep = tmp_path / 'one' / 'two' / 'three'
        deep.mkdir(parents=True)
        alias = deep / 'alias'
        alias.symlink_to(foreign)
        root = alias / 'candidate'
    before = sentinel.stat().st_ino, sentinel.read_text()
    with pytest.raises(OSError):
        prepare_private_test_paths(root)
    assert before == (sentinel.stat().st_ino, sentinel.read_text())
    assert list(foreign.iterdir()) == [sentinel]


@pytest.mark.parametrize('root', [Path('relative'), Path('/'), Path('/one/../candidate')])
def test_private_paths_reject_invalid_root_before_open(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail('invalid root reached filesystem open')
    monkeypatch.setattr(os, 'open', forbidden)
    with pytest.raises(ValueError):
        prepare_private_test_paths(root)


@pytest.mark.parametrize('seam', ['root-open', 'plans-open', 'artifacts-open', 'fstat'])
def test_private_paths_cleanup_each_acquisition_gap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, seam: str) -> None:
    real_open, real_fstat = os.open, os.fstat
    opened = []
    def controlled_open(path: str, *args: object, **kwargs: object) -> int:
        if path == {'root-open': 'candidate', 'plans-open': 'plans', 'artifacts-open': 'artifacts'}.get(seam):
            raise OSError('primary acquisition gap')
        fd = real_open(path, *args, **kwargs)
        opened.append(fd)
        return fd
    def controlled_stat(fd: int) -> os.stat_result:
        if seam == 'fstat':
            raise OSError('primary acquisition gap')
        return real_fstat(fd)
    monkeypatch.setattr(os, 'open', controlled_open)
    monkeypatch.setattr(os, 'fstat', controlled_stat)
    with pytest.raises(OSError, match='primary acquisition gap'):
        prepare_private_test_paths(tmp_path / 'candidate')
    assert not (tmp_path / 'candidate').exists()
    for fd in opened:
        with pytest.raises(OSError):
            real_fstat(fd)


def test_private_paths_preserve_primary_all_removal_and_close_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    real_mkdir, real_close = os.mkdir, os.close
    closed = []
    removed = []
    def mkdir(path: str, *args: object, **kwargs: object) -> None:
        if path == 'artifacts':
            raise OSError('primary artifacts failure')
        real_mkdir(path, *args, **kwargs)
    def rmdir(path: str, *args: object, **kwargs: object) -> None:
        removed.append(path)
        raise OSError(f'secondary remove {path}')
    def close(fd: int) -> None:
        real_close(fd)
        closed.append(fd)
        raise OSError(f'secondary close {fd}')
    monkeypatch.setattr(os, 'mkdir', mkdir)
    monkeypatch.setattr(os, 'rmdir', rmdir)
    monkeypatch.setattr(os, 'close', close)
    with pytest.raises(ExceptionGroup) as caught:
        prepare_private_test_paths(tmp_path / 'candidate')
    errors = caught.value.exceptions
    assert str(errors[0]) == 'primary artifacts failure'
    assert [str(e) for e in errors[1:3]] == ['secondary remove plans', 'secondary remove candidate']
    assert len(errors) == 3 + len(closed)
    assert removed == ['plans', 'candidate']
    assert [str(e) for e in errors[3:]] == [f'secondary close {fd}' for fd in closed]
    assert (tmp_path / 'candidate' / 'plans').is_dir()  # residue stays visible
    assert not (tmp_path / 'candidate' / 'artifacts').exists()


@pytest.mark.parametrize('family,agent', [('blocked_multi', 'engineering_head'), ('blocked_autonomous', 'engineering_head'), ('content', 'content_manager')])
@pytest.mark.parametrize('control,exit_code', [('FAIL_CALLBACK', 37), ('ABORT_CALLBACK', 143)])
def test_resumed_plan_cleanup_paths(tmp_path: Path, family: str, agent: str, control: str, exit_code: int) -> None:
    plan = PlanRun(tmp_path / 'run', family)
    assert plan.run(agent).returncode == 0
    prior = len(plan.calls())
    result = plan.run(agent, **{control: str(prior + 1)})
    assert result.returncode == exit_code
    assert len(plan.calls()) == prior + 1
    plan.assert_paths()
