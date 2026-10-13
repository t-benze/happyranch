"""Closed THR304 source/release admission and Linux owned-process supervisor.

This manual QA lane is separate from automated lint and daily Product E2E.
EXECUTION_OWNER is manager-allocated to TASK-11079; final-head review and release remain pending.
"""
from __future__ import annotations

import argparse
import ast
import ctypes
from contextlib import contextmanager
import fcntl
import hashlib
import json
import marshal
import os
from pathlib import Path
import re
import selectors
import shutil
import signal
import socket
import stat
import subprocess
import sys
import time

ROOT_TASK = "TASK-11079"
# Recovery publication retains original TASK-11112 maker commits and provenance.
IMPLEMENTATION_TASK = "TASK-11122"
IMPLEMENTATION_REF = "refs/heads/task/TASK-11122"
EXECUTION_OWNER = "TASK-11079"  # Allocated owner; independent final-head review + manager release pending.
SELECTION = "attachment-c1-c8-v1"
TEST_PATH = "tests/integration/test_attachment_feedback_shipping.py"
PARAMETERS = {'test_c01_delegate': ['direct', 'then-only'], 'test_c02_initial_fanout': ['plain-first', 'plain-last', 'carrier-first', 'carrier-last', 'nested-first', 'nested-last', 'nested-only-first', 'nested-only-last'], 'test_c03_uniqueness_and_count': ['direct-direct', 'direct-then', 'then-then', 'sibling-sibling', 'carrier-nested', 'nested-same-carrier', 'nested-cross-siblings', 'plain-nested', 'delegate-five', 'fanout-five', 'delegate-six', 'fanout-six', 'delegate-total-over-five', 'fanout-total-over-five'], 'test_c04_error_classes': ['too-many', 'duplicate', 'missing', 'claimed-same-tree', 'unsafe-name', 'unsupported-suffix', 'directory', 'no-suffix', 'name-201', 'invalid-key-subclass', 'malformed-ref'], 'test_c05_claim_loss': ['before-A-cancel', 'before-A-rival-feedback', 'before-A-new-step', 'after-A-cancel', 'after-A-new-step', 'B-wins-then-cancel', 'B-not-configured'], 'test_c06_receipts': ['ordinary-delegate-duplicate', 'ordinary-fanout-duplicate', 'v2-applied', 'v2-claimed-before-body', 'v2-claimed-after-A', 'v2-refused-repeat'], 'test_c07_two_opportunities': ['root-DDD', 'root-DFD', 'root-FDF', 'nonroot-DDD', 'nonroot-DFD', 'nonroot-FDF', 'valid-after-two', 'same-claim-contenders'], 'test_c08_transaction_boundary': ['missing-before-prepare', 'competing-key-claim', 'allocated-child-collision', 'writer-lock-conflict', 'missing-after-prepare']}
SUBOBSERVATIONS = ('C4.errors.fixed.D.validation.non-JSON', 'C4.errors.fixed.D.validation.JSON-scalar', 'C4.errors.fixed.D.validation.JSON-list', 'C4.errors.fixed.D.validation.unknown-code', 'C4.errors.fixed.D.validation.missing-field', 'C4.errors.fixed.D.validation.wrong-key-type', 'C4.errors.fixed.D.validation.bool-max', 'C4.errors.fixed.D.validation.wrong-max', 'C4.errors.fixed.D.validation.extra-member', 'C4.errors.fixed.D.validation.duplicate-member', 'C4.errors.fixed.D.validation.ValueError-subclass', 'C4.errors.fixed.D.validation.nonstring-argument', 'C4.errors.fixed.D.validation.multiple-args', 'C4.errors.fixed.D.prepare.non-JSON', 'C4.errors.fixed.D.prepare.JSON-scalar', 'C4.errors.fixed.D.prepare.JSON-list', 'C4.errors.fixed.D.prepare.unknown-code', 'C4.errors.fixed.D.prepare.missing-field', 'C4.errors.fixed.D.prepare.wrong-key-type', 'C4.errors.fixed.D.prepare.bool-max', 'C4.errors.fixed.D.prepare.wrong-max', 'C4.errors.fixed.D.prepare.extra-member', 'C4.errors.fixed.D.prepare.duplicate-member', 'C4.errors.fixed.D.prepare.ValueError-subclass', 'C4.errors.fixed.D.prepare.nonstring-argument', 'C4.errors.fixed.D.prepare.multiple-args', 'C4.errors.fixed.F.validation.non-JSON', 'C4.errors.fixed.F.validation.JSON-scalar', 'C4.errors.fixed.F.validation.JSON-list', 'C4.errors.fixed.F.validation.unknown-code', 'C4.errors.fixed.F.validation.missing-field', 'C4.errors.fixed.F.validation.wrong-key-type', 'C4.errors.fixed.F.validation.bool-max', 'C4.errors.fixed.F.validation.wrong-max', 'C4.errors.fixed.F.validation.extra-member', 'C4.errors.fixed.F.validation.duplicate-member', 'C4.errors.fixed.F.validation.ValueError-subclass', 'C4.errors.fixed.F.validation.nonstring-argument', 'C4.errors.fixed.F.validation.multiple-args', 'C4.errors.fixed.F.prepare.non-JSON', 'C4.errors.fixed.F.prepare.JSON-scalar', 'C4.errors.fixed.F.prepare.JSON-list', 'C4.errors.fixed.F.prepare.unknown-code', 'C4.errors.fixed.F.prepare.missing-field', 'C4.errors.fixed.F.prepare.wrong-key-type', 'C4.errors.fixed.F.prepare.bool-max', 'C4.errors.fixed.F.prepare.wrong-max', 'C4.errors.fixed.F.prepare.extra-member', 'C4.errors.fixed.F.prepare.duplicate-member', 'C4.errors.fixed.F.prepare.ValueError-subclass', 'C4.errors.fixed.F.prepare.nonstring-argument', 'C4.errors.fixed.F.prepare.multiple-args', 'C5.fingerprint.task_id.A', 'C5.fingerprint.task_id.B', 'C5.fingerprint.assigned_agent.A', 'C5.fingerprint.assigned_agent.B', 'C5.fingerprint.team.A', 'C5.fingerprint.team.B', 'C5.fingerprint.current_session_id.A', 'C5.fingerprint.current_session_id.B', 'C5.fingerprint.step.A', 'C5.fingerprint.step.B', 'C5.fingerprint.active_chain.A', 'C5.fingerprint.active_chain.B', 'C5.fingerprint.active_fanout.A', 'C5.fingerprint.active_fanout.B', 'C5.fingerprint.revision.A', 'C5.fingerprint.revision.B', 'C5.fingerprint.note.A', 'C5.fingerprint.note.B', 'C5.fingerprint.status.A', 'C5.fingerprint.status.B', 'C5.fingerprint.block_kind.A', 'C5.fingerprint.block_kind.B', 'C5.fingerprint.cancelled_at.A', 'C5.fingerprint.cancelled_at.B', 'C5.fingerprint.result_row_id.A', 'C5.fingerprint.result_row_id.B', 'C5.enqueue-raises', 'C6.identity-controls.ordinary-duplicate', 'C6.identity-controls.v2-duplicate', 'C6.identity-controls.changed-callback', 'C6.identity-controls.foreign-result', 'C6.identity-controls.applied-repeat', 'C6.identity-controls.refused-repeat', 'C6.identity-controls.new-correction', 'C6.v2-applied.public-after-ACK-interruption', 'C6.v2-claimed-after-A.after-A-before-B', 'C6.v2-claimed-after-A.before-ACK', 'C7.ledger-integrity.duplicate-key', 'C7.ledger-integrity.nonstandard-constant', 'C7.ledger-integrity.ordinal-gap', 'C7.ledger-integrity.duplicate-accepted', 'C7.ledger-integrity.duplicate-feedback', 'C7.ledger-integrity.orphan-result', 'C7.ledger-integrity.status-mismatch', 'C7.ledger-integrity.owner-mismatch', 'C7.ledger-integrity.reason-mismatch', 'C7.ledger-integrity.ambiguous-step', 'C7.ledger-integrity.untagged-unrelated', 'C7.ledger-integrity.duplicate-claim', 'C7.ledger-integrity.lost-B', 'C7.stop-owner-loss.root', 'C7.stop-owner-loss.nonroot', 'C7.positive-revise-cap.root', 'C7.positive-revise-cap.nonroot', 'C8.exists-stat.D', 'C8.exists-stat.F', 'C8.A-audit-deny.D', 'C8.A-audit-deny.F', 'C8.spawn-audit-deny.D', 'C8.spawn-audit-deny.F', 'C8.real-races.D', 'C8.real-races.F', 'C8.COMMIT-deny.A', 'C8.COMMIT-deny.spawn-D', 'C8.COMMIT-deny.spawn-F', 'C8.COMMIT-deny.stop-root', 'C8.COMMIT-deny.stop-nonroot', 'C8.stop-second-denominator', 'C8.postcommit.fanout_spawned', 'C8.postcommit.history', 'C8.postcommit.queue')
NODES = tuple(f"{TEST_PATH}::{name}[{parameter}]" for name, values in PARAMETERS.items() for parameter in values)
MANIFEST = {"selection": SELECTION, "nodes": NODES, "subobservations": SUBOBSERVATIONS}
MANIFEST_SHA256 = hashlib.sha256(json.dumps(MANIFEST, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
SOURCE_FILES = (
    TEST_PATH, "tests/helpers/attachment_feedback_fixture.py",
    "tests/helpers/attachment_feedback_prelaunch/sitecustomize.py",
    "scripts/attachment_scoped_qa.py", "tests/helpers/integration_parent.py",
    "tests/helpers/integration_stub_guard/guard.py", "tests/helpers/integration_stub_guard/sitecustomize.py",
    "tests/helpers/deterministic_plan.py", "tests/conftest.py", "tests/integration/conftest.py",
    "tests/integration/fake_claude.sh", "tests/integration/fake_codex.sh", "tests/integration/fake_opencode.sh",
    "runtime/orchestrator/run_step.py", "runtime/infrastructure/db/tasks.py", "runtime/infrastructure/database.py",
    "runtime/daemon/__main__.py", "runtime/daemon/queue.py", "runtime/daemon/dispatcher.py",
    "runtime/daemon/routes/tasks.py", "runtime/daemon/org_state.py", "cli/main.py", "scripts/daemon.sh",
    "scripts/run_bounded_output.py", "uv.lock",
)
PHASE_LIMITS = {"setup": 15, "call": 20, "teardown": 5}
CAPS = {"command_log": 1048576, "junit": 1048576, "phase": 4096,
        "phases": 2097152, "observer": 1048576, "metadata": 1048576,
        "snapshot":65536, "snapshots":8388608, "logs":16777216, "daemon_log":1048576,
        "processes": 256, "process_rows": 4096, "aggregate": 33554432}

# Missing immutable terminal files retain headroom while other writers run.
# The empty lock inode is permanent, counted, and never unlinked/recreated.
FINDING_ROWS = ('R14.admission','R14.Popen','R14.selector','R15.raw','R14/R15.capture')

FINAL_CAPS = {'processes.json':2097152, 'phases.json':2097152,
              'result.json':1048576, 'observations.json':1048576, 'cases.xml':1048576}
EVIDENCE_LOCK = '.allocation.lock'
ERROR_LIMIT = 128
ERROR_TEXT_LIMIT = 1024
ERROR_LABEL_LIMIT = 96
ERROR_OVERFLOW_LIMIT = 128
ERROR_OVERFLOW_MEANING = 'unretained observations, not unique causes; count saturates at 128 (lower bound)'


def error_text(exc, limit=ERROR_TEXT_LIMIT):
    return (type(exc).__name__+':'+str(exc)[:limit])[:limit]


def error_note(exc, note):
    # Even repeated evidence cleanup of the SAME propagated exception is bounded.
    notes=getattr(exc,'__notes__',())
    if len(notes)<ERROR_LIMIT-1:exc.add_note(note[:ERROR_TEXT_LIMIT])
    elif len(notes)==ERROR_LIMIT-1:exc.add_note('additional evidence cleanup notes truncated')


def evidence_cleanup(actions):
    original=sys.exception(); failure=None
    for action in actions:
        try:action()
        except BaseException as exc:
            if original is not None:error_note(original,'evidence cleanup unavailable: '+type(exc).__name__)
            elif failure is None:failure=exc
    if failure is not None:raise failure


@contextmanager
def evidence_allocation(root: Path, identity=None):
    info=root.lstat()
    if (root.resolve(strict=True)!=root or not stat.S_ISDIR(info.st_mode)
            or info.st_uid!=os.getuid() or stat.S_IMODE(info.st_mode)!=0o700
            or identity is not None and (info.st_dev,info.st_ino)!=identity):
        raise ValueError('evidence directory changed/unowned')
    parent=os.open(root,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    lock=None
    try:
        opened=os.fstat(parent)
        if (opened.st_dev,opened.st_ino)!=(info.st_dev,info.st_ino):
            raise ValueError('evidence directory changed')
        lock=os.open(EVIDENCE_LOCK,os.O_RDWR|os.O_NOFOLLOW,dir_fd=parent)
        held=os.fstat(lock)
        if (not stat.S_ISREG(held.st_mode) or held.st_uid!=os.getuid()
                or stat.S_IMODE(held.st_mode)!=0o600 or held.st_nlink!=1 or held.st_size!=0):
            raise ValueError('evidence lock unowned')
        deadline=time.monotonic()+.25
        while True:
            try:
                fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic()>=deadline:raise TimeoutError('bounded evidence allocation expired')
                time.sleep(.005)
        current=os.stat(EVIDENCE_LOCK,dir_fd=parent,follow_symlinks=False)
        if (current.st_dev,current.st_ino)!=(held.st_dev,held.st_ino):
            raise ValueError('evidence lock changed')
        yield
    finally:
        # Closing releases flock even on interruption; no stale PID lock/retry.
        evidence_cleanup([lambda fd=fd:os.close(fd) for fd in (lock,parent) if fd is not None])


def evidence_usage(root: Path):
    total=0; entries=0; files={}
    for path in root.rglob('*'):
        entries+=1
        if entries>4096:raise ValueError('external evidence file inventory cap')
        info=path.lstat()
        directory=stat.S_ISDIR(info.st_mode)
        if (info.st_uid!=os.getuid() or stat.S_IMODE(info.st_mode)!=(0o700 if directory else 0o600)
                or not (directory or stat.S_ISREG(info.st_mode))
                or not directory and info.st_nlink!=1):
            raise ValueError('external evidence entry unowned/unsafe')
        if not directory:
            total+=info.st_size
            files[path.relative_to(root).as_posix()]=info.st_size
        if total>CAPS['aggregate']:raise ValueError('aggregate evidence cap')
    return total,entries,files


def evidence_capacity(root: Path, path: Path, size: int, entries: int):
    total,count,files=evidence_usage(root)
    # Reserve only absent final targets, excluding the one being published now.
    missing={name:cap for name,cap in FINAL_CAPS.items() if name not in files and root/name!=path}
    if total+size+sum(missing.values())>CAPS['aggregate']:
        raise ValueError('aggregate evidence allocation refused')
    if count+entries+len(missing)>4096:
        raise ValueError('external evidence entry allocation refused')


def evidence_publish(path: Path, data: bytes, cap: int, root: Path, *, replace=False, identity=None):
    if len(data)>cap or path.name==EVIDENCE_LOCK or path.name.endswith('.tmp'):
        raise ValueError('evidence publication cap/name')
    if path.parent!=root and (path.parent.parent!=root or not re.fullmatch(r'[0-9a-f]{20}',path.parent.name)):
        raise ValueError('evidence publication domain')
    if path.parent==root and path.name in FINAL_CAPS and (replace or cap>FINAL_CAPS[path.name]):
        raise ValueError('immutable final evidence cap')
    with evidence_allocation(root,identity):
        new_directory=path.parent!=root and not path.parent.exists()
        # Count the old target plus the full temporary, and a new directory.
        evidence_capacity(root,path,len(data),1+int(new_directory))
        if new_directory:path.parent.mkdir(mode=0o700)
        parent_info=path.parent.lstat()
        parent=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        owned=None;fd=None;temporary=path.name+'.tmp'
        try:
            opened=os.fstat(parent)
            if (opened.st_dev,opened.st_ino)!=(parent_info.st_dev,parent_info.st_ino):
                raise ValueError('evidence parent changed')
            if path.parent.resolve(strict=True)!=path.parent:
                raise ValueError('evidence parent changed')
            try:previous=os.stat(path.name,dir_fd=parent,follow_symlinks=False)
            except FileNotFoundError:previous=None
            if previous is not None and not replace:raise FileExistsError(path)
            fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=parent)
            owned=os.fstat(fd)
            if os.write(fd,data)!=len(data):raise ValueError('short evidence publication')
            os.fsync(fd)
            os.replace(temporary,path.name,src_dir_fd=parent,dst_dir_fd=parent)
            owned=None
        finally:
            def remove_temporary():
                if owned is None:return
                current=os.stat(temporary,dir_fd=parent,follow_symlinks=False)
                if (current.st_dev,current.st_ino)!=(owned.st_dev,owned.st_ino):
                    raise ValueError('evidence temporary changed')
                os.unlink(temporary,dir_fd=parent)
            evidence_cleanup(([lambda:os.close(fd)] if fd is not None else [])
                             +[remove_temporary,lambda:os.close(parent)])


def evidence_append(path: Path, data: bytes, cap: int, root: Path, *, exclusive=False, identity=None):
    if path.parent!=root or path.name==EVIDENCE_LOCK or path.name in FINAL_CAPS or path.name.endswith('.tmp'):
        raise ValueError('evidence append domain')
    with evidence_allocation(root):
        try:before=path.lstat()
        except FileNotFoundError:before=None
        if exclusive and before is not None:raise FileExistsError(path)
        if identity is not None and (before is None or (before.st_dev,before.st_ino)!=identity):
            raise ValueError('evidence stream identity changed')
        size=0 if before is None else before.st_size
        if size+len(data)>cap:raise ValueError('evidence stream cap')
        evidence_capacity(root,path,len(data),int(before is None))
        flags=os.O_WRONLY|os.O_APPEND|os.O_NOFOLLOW
        fd=os.open(path,flags|(os.O_CREAT|os.O_EXCL if before is None else 0),0o600)
        try:
            opened=os.fstat(fd)
            if before is not None and (opened.st_dev,opened.st_ino)!=(before.st_dev,before.st_ino):
                raise ValueError('evidence stream changed')
            if os.write(fd,data)!=len(data):raise ValueError('short evidence stream write')
            return opened.st_dev,opened.st_ino
        finally:
            evidence_cleanup([lambda:os.close(fd)])


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def closed_read(path: Path, cap: int) -> dict:
    info = path.lstat()
    if (path.resolve(strict=True) != path or not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size > cap):
        raise ValueError("unowned, noncanonical or oversized descriptor")
    return json.loads(path.read_text())


def admit(task_id: str, expected_sha: str, *, interpreter: bool = True) -> dict:
    if EXECUTION_OWNER is None:
        raise ValueError("THR304 execution owner unallocated; manager must bind EXECUTION_OWNER after source review")
    if task_id != EXECUTION_OWNER or not re.fullmatch(r"TASK-[1-9][0-9]*", task_id):
        raise ValueError("wrong released execution task")
    source = Path(__file__).resolve().parents[1]
    git = lambda *args: subprocess.check_output(["git", *args], cwd=source, text=True).strip()
    actual = git("rev-parse", "HEAD")
    if (not re.fullmatch(r"[0-9a-f]{40}", expected_sha) or expected_sha != actual
            or actual != os.environ.get("GITHUB_SHA") or os.environ.get("GITHUB_REF") != IMPLEMENTATION_REF
            or os.environ.get("GITHUB_REPOSITORY") != "t-benze/happyranch"
            or os.environ.get("GITHUB_EVENT_NAME") != "workflow_dispatch"
            or os.environ.get("QA_RUNNER_ENVIRONMENT") != "github-hosted"
            or os.environ.get("RUNNER_OS") != "Linux" or os.environ.get("ImageOS") != "ubuntu24"
            or git("status", "--porcelain")):
        raise ValueError("THR304 immutable source/ref/event/host/clean admission refused")
    if interpreter and (sys.version_info[:2] != (3, 14) or sys.platform != "linux"):
        raise ValueError("THR304 requires effective Python3.14 on Linux")
    if len(NODES) != 61 or len(set(NODES)) != 61 or len(set(SUBOBSERVATIONS)) != len(SUBOBSERVATIONS):
        raise ValueError("closed manifest invalid")
    return {"source": str(source), "head": actual, "tree": git("rev-parse", "HEAD^{tree}"),
            "ref": IMPLEMENTATION_REF, "task": task_id, "root": ROOT_TASK,
            "manifest_sha256": MANIFEST_SHA256,
            "source_hashes": {name: sha(source / name) for name in SOURCE_FILES},
            "python": os.path.abspath(sys.executable), "python_binary": str(Path(sys.executable).resolve()),
            "python_version": sys.version,
            "python_prefix": sys.prefix, "python_sha256": sha(Path(sys.executable).resolve()),
            "run": os.environ.get("GITHUB_RUN_ID"), "attempt": os.environ.get("GITHUB_RUN_ATTEMPT")}


def write_receipt(path: Path, value, cap: int = 1048576, *, evidence: Path | None = None):
    encoded = (json.dumps(value, sort_keys=True) + "\n").encode()
    if len(encoded) > cap:
        raise ValueError("receipt cap exceeded")
    if evidence is not None:
        evidence_publish(path,encoded,cap,evidence)
        return
    with path.open("xb") as handle:
        os.chmod(path, 0o600)
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())


class OwnedTree:
    """Discover only positively owned child edges; signals use bound pidfds."""
    def __init__(self, evidence: Path, record=None):
        libc = ctypes.CDLL(None, use_errno=True)
        if libc.prctl(36, 1, 0, 0, 0):  # PR_SET_CHILD_SUBREAPER
            raise OSError(ctypes.get_errno(), "subreaper refused")
        value = ctypes.c_int()
        if libc.prctl(37, ctypes.byref(value), 0, 0, 0) or value.value != 1:
            raise ValueError("subreaper readback refused")
        self.identities, self.errors, self.events = {}, [], []
        self.record = record
        self.reported = []
        self.error_overflow = 0
        self.evidence = evidence
        self.self_pid = os.getpid()
        self.self_identity = self.identity(self.self_pid)[:2]
        self.ports = {}
        self.logs = {}
        self.required_roles=set()
        self.head=None
        self.retired_cases={}
        self.direct_wait=None

    def failure(self, label, exc):
        # Report the actual exception when it occurs, before later obligations.
        if any(prior is exc for prior in self.reported):return
        if len(self.reported)<ERROR_LIMIT:
            self.reported.append(exc)
            self.errors.append(label[:ERROR_LABEL_LIMIT]+':'+error_text(exc,256))
        else:
            # Unknown identities are deliberately NOT retained after capacity.
            # Repeated propagation can count again, but adds no rows or notes.
            self.error_overflow=min(ERROR_OVERFLOW_LIMIT,self.error_overflow+1)
        if self.record is not None:self.record(label,exc)

    def event(self, **value):
        value.update(time_ns=time.time_ns())
        if len(self.events) >= CAPS["process_rows"] or len(json.dumps(value)) > 2048:
            raise ValueError("process evidence cap")
        self.events.append(value)

    @staticmethod
    def identity(pid: int):
        path = Path(f"/proc/{pid}")
        uid = path.stat().st_uid
        raw = (path / "stat").read_text()
        fields = raw[raw.rindex(")")+2:].split()
        return uid, int(fields[19]), int(fields[1])

    def children(self, pid: int) -> set[int]:
        threads = list(Path(f"/proc/{pid}/task").iterdir())
        if len(threads) > 128:
            raise ValueError("owned thread cap")
        children = set()
        for thread in threads:
            children.update(int(x) for x in (thread / "children").read_text().split())
        return children

    def proved_parent(self, parent: int) -> None:
        expected = self.self_identity if parent == self.self_pid else None
        if parent in self.identities and not self.identities[parent]['exited']:
            entry = self.identities[parent]
            expected = entry['uid'], entry['start']
        if expected is None:
            raise ValueError('unproved binding parent')
        if self.identity(parent)[:2] != expected:
            raise ValueError('binding parent UID/start changed')

    @staticmethod
    def binding_exit(fd: int) -> bool:
        # Readiness is positive exit evidence, never a consuming wait.
        poll = selectors.DefaultSelector()
        try:
            poll.register(fd, selectors.EVENT_READ)
            return bool(poll.select(0))
        finally:
            evidence_cleanup([poll.close])

    def bind_child(self, pid: int, parent: int) -> None:
        fd = None
        facts = {'pid': pid, 'enumerated_parent': parent, 'before': None,
                 'after': None, 'member': None, 'exit_indicated': None,
                 'terminal_identity': None, 'edge_identity': None,
                 'current_member': None, 'stage': 'initial identity'}
        try:
            before = self.identity(pid)
            facts['before'] = before
            if before[0] != os.getuid():
                raise ValueError('binding child UID mismatch')
            self.proved_parent(parent)
            # A stale children listing may precede an actual owned reparent.
            self.proved_parent(before[2])
            if before[2] != parent and pid not in self.children(before[2]):
                raise ValueError('binding initial owner edge unavailable')
            facts['stage'] = 'pidfd open'
            fd = os.pidfd_open(pid)
            facts['stage'] = 'post-pidfd identity'
            after = self.identity(pid)
            facts['after'] = after
            if before[:2] != after[:2]:
                raise ValueError('binding child UID/start changed')
            facts['stage'] = 'post-pidfd owner'
            self.proved_parent(after[2])
            facts['stage'] = 'parent membership'
            member = pid in self.children(after[2])
            facts['member'] = member
            exited = self.binding_exit(fd)
            facts['exit_indicated'] = exited
            if exited:
                # Both identity reads already proved this pidfd's child. A
                # later absence can explain a lost edge, but supplies no wait.
                facts['stage'] = 'terminal identity'
                try: terminal = self.identity(pid)
                except FileNotFoundError: terminal = None
                facts['terminal_identity'] = terminal
                if terminal is not None:
                    if terminal[:2] != before[:2]:
                        raise ValueError('binding terminal child UID/start changed')
                    self.proved_parent(terminal[2])
                    if terminal[2] != after[2]:
                        if pid not in self.children(terminal[2]):
                            raise ValueError('binding terminal reparent edge unavailable')
                        after = terminal
            elif not member:
                # A live child can reparent after the post-pidfd identity read.
                # Require the same identity and a fresh proved-owner edge.
                facts['stage'] = 'live edge reread'
                current = self.identity(pid)
                facts['edge_identity'] = current
                if current[:2] != before[:2]:
                    raise ValueError('binding live child UID/start changed')
                self.proved_parent(current[2])
                current_member = pid in self.children(current[2])
                facts['current_member'] = current_member
                if current[2] == after[2] or not current_member:
                    raise ValueError('binding live owner edge unavailable')
                after = current
            facts['stage'] = 'accepted exit' if exited else 'accepted edge'
            self.event(kind='binding_observation', **facts)
            self.identities[pid] = {'uid': before[0], 'start': before[1], 'parent': after[2],
                'fd': fd, 'exited': False, 'reaped': False, 'birth_parent': before[2],
                'parent_wait': None, 'removed_after_exit': False}
            fd = None  # transfer only after identity/ownership proof
            self.event(kind='bound', pid=pid, parent=after[2], start=before[1])
            if before[2] != after[2]:
                self.event(kind='reparent', pid=pid, before=before[2], after=after[2])
        except BaseException as exc:
            # Record the native earliest failure before diagnostics or fd close.
            self.failure('pidfd binding', exc)
            try:error_note(exc, 'binding context '+json.dumps(facts, sort_keys=True))
            except BaseException as later:self.failure('binding context', later)
            try:self.event(kind='binding_refused', **facts)
            except BaseException as later:self.failure('binding observation', later)
            raise
        finally:
            if fd is not None:
                try:os.close(fd)
                except BaseException as exc:self.failure('unbound pidfd close', exc)

    def discover(self):
        frontier, visited = [self.self_pid], set()
        # Retain known nonadopted ancestors to cover teardown/reparenting.
        frontier.extend(pid for pid, entry in self.identities.items() if not entry['exited'])
        while frontier:
            parent = frontier.pop()
            if parent in visited:
                continue
            visited.add(parent)
            try:
                children = self.children(parent)
            except FileNotFoundError as exc:
                entry = self.identities.get(parent)
                # Only an already bound, positively terminal parent may vanish.
                if entry is not None and self.binding_exit(entry['fd']):
                    self.event(kind='discovery_parent_absent', pid=parent,
                               start=entry['start'], exit_indicated=True, consuming_wait=False)
                    continue
                self.failure('discover parent', exc)
                continue
            except (OSError, ValueError) as exc:
                self.failure('discover parent', exc)
                continue
            for pid in children:
                try:
                    if pid not in self.identities:
                        if len(self.identities) >= CAPS['processes']:
                            raise ValueError('owned process cap')
                        self.bind_child(pid, parent)
                    else:
                        entry = self.identities[pid]
                        try:current = self.identity(pid)
                        except FileNotFoundError:
                            if not self.binding_exit(entry['fd']):raise
                            self.event(kind='discovery_child_absent', pid=pid,
                                       start=entry['start'], exit_indicated=True, consuming_wait=False)
                            continue
                        if current[:2] != (entry['uid'], entry['start']):
                            raise ValueError('owned PID identity changed')
                        if current[2] != entry['parent']:
                            self.proved_parent(current[2])
                            if pid not in self.children(current[2]):
                                raise ValueError('observed reparent edge unavailable')
                            self.event(kind='reparent', pid=pid, before=entry['parent'], after=current[2])
                            entry['parent'] = current[2]
                    frontier.append(pid)
                except (OSError, ValueError) as exc:
                    self.failure('discover', exc)

    def observe_exits(self, direct_pid: int):
        for pid, entry in self.identities.items():
            if entry["exited"]:
                continue
            poll = selectors.DefaultSelector()
            try:
                poll.register(entry["fd"], selectors.EVENT_READ)
                if not poll.select(0):
                    continue
                entry["exited"] = True
                self.event(kind="exit_indication", pid=pid, start=entry["start"])
                if pid != direct_pid:
                    try:
                        result = os.waitid(os.P_PIDFD, entry["fd"], os.WEXITED | os.WNOHANG | os.WNOWAIT)
                        if result is not None:
                            self.event(kind="waitid", pid=pid, status=result.si_status, code=result.si_code)
                            consumed=os.waitid(os.P_PIDFD, entry["fd"], os.WEXITED)
                            if (consumed.si_pid,consumed.si_status,consumed.si_code)!=(pid,result.si_status,result.si_code):
                                raise ValueError('owned wait result changed')
                            entry['supervisor_wait']={'owner':self.self_pid,'child':pid,'start':entry['start'],
                                'status':consumed.si_status,'code':consumed.si_code}
                            entry["reaped"] = True
                            self.event(kind="reaped", pid=pid,owner=self.self_pid,start=entry['start'],
                                       status=consumed.si_status,code=consumed.si_code)
                    except ChildProcessError:
                        # Its actual parent owns reap; this is only exit indication.
                        try:
                            current=self.identity(pid)
                            removed=current[1]!=entry['start']
                        except FileNotFoundError:
                            removed=True
                        if removed:
                            entry['removed_after_exit']=True
                            entry['reaped']=entry['parent_wait'] is not None
                            self.event(kind='owning_parent_exit',pid=pid,parent=entry['parent'],
                                evidence='bound pidfd exit and original proc identity absent; parent wait separately required')
                        else:
                            entry['exited']=False
                            self.event(kind='nonadopted_zombie',pid=pid,parent=entry['parent'])
            except BaseException as exc:
                self.failure('exit observation',exc)
                raise
            finally:
                try:poll.close()
                except BaseException as exc:self.failure('exit selector close',exc)

    def signal_bound(self, sig: int):
        self.discover()
        for pid, entry in self.identities.items():
            if not entry["exited"]:
                try:
                    signal.pidfd_send_signal(entry["fd"], sig)
                    self.event(kind="signal", pid=pid, signal=sig)
                except ProcessLookupError:
                    pass
                except OSError as exc:
                    self.failure("pidfd signal",exc)

    def closed_identity(self,pid):
        entry=self.identities[pid]
        return entry['exited'] and (entry['reaped'] or
            (pid not in self.required_roles and entry['removed_after_exit']))

    def role_receipts(self,path,node):
        if path.parent!=self.evidence:raise ValueError('required role path outside evidence')
        receipt=closed_read(path,262144)
        if receipt['node']!=node or receipt['head']!=self.head:raise ValueError('role source/node mismatch')
        for row in receipt['rows']:
            launch=row['launch']
            if launch['head']!=self.head:raise ValueError('foreign provider launch source')
            provider=row.get('provider')
            if provider is None or provider['sid']!=launch['session_id'] or provider['task']!=launch['task_id']:
                raise ValueError('actual executor PID/SID source receipt missing')
            for pid in (provider['pid'],launch['driver_pid']):
                self.required_roles.add(pid)
                if pid not in self.identities:
                    self.failure('role identity',ValueError('required provider role identity missing'))
            waits=[]
            if 'provider-wait' in row:waits.append(row['provider-wait'])
            if 'callback' in row:
                callback=row['callback']
                waits.append({'owner':callback['parent_pid'],'child':callback['child_pid'],
                              'returncode':callback['parent_wait_returncode']})
            for wait in waits:
                pid=wait['child']
                if pid in self.identities:
                    entry=self.identities[pid]
                    if entry['birth_parent']!=wait['owner'] or type(wait['returncode']) is not int:
                        raise ValueError('required source parent wait edge mismatch')
                    entry['parent_wait']=wait['returncode']
                    if entry['removed_after_exit']:entry['reaped']=True
                    self.event(kind='required_role_parent_wait',pid=pid,owner=wait['owner'],status=wait['returncode'])
                else:
                    # The authenticated deterministic callback wait is actual
                    # parent evidence; it grants no pidfd signalling right.
                    self.event(kind='callback_parent_wait',pid=pid,owner=wait['owner'],status=wait['returncode'],independently_bound=False)
                    self.failure('callback identity',ValueError('required callback identity not observed'))

    def register_log(self,node,log):
        # Capture registration is not process ownership or endpoint admission.
        path=Path(log)
        parent=path.parent
        if not parent.exists():
            # tmp_home deliberately leaves exactly this directory absent so
            # public startup still performs its real mkdir.
            if parent.name not in ('.happyranch','daemon'):raise ValueError('unowned absent log parent')
            parent=parent.parent
        if (path.name!='daemon.log' or path.resolve()!=path or parent.resolve(strict=True)!=parent
                or parent.stat().st_uid!=os.getuid()
                or not path.is_relative_to(self.evidence.parent/'scratch')):
            raise ValueError('owned prelaunch log path refused')
        if str(path) not in self.logs:
            if len(self.logs)>=8:raise ValueError('bounded log inventory refused')
            self.logs[str(path)]={'node':node,'log':str(path),'log_identity':None}
        elif self.logs[str(path)]['node']!=node:
            raise ValueError('log node ownership changed')

    def bind_endpoint(self,pid,port,node,log,wait_log):
        self.discover()
        if pid not in self.identities or self.identities[pid]['exited']:
            raise ValueError('case daemon unavailable before endpoint binding')
        if type(port) is not int or not 1<=port<=65535 or len(self.ports)>=8:
            raise ValueError('bounded owned port inventory refused')
        inodes=set()
        for path in Path(f'/proc/{pid}/fd').iterdir():
            link=os.readlink(path)
            if link.startswith('socket:['):inodes.add(link[8:-1])
        listeners=[]
        for name in ('tcp','tcp6'):
            rows=Path(f'/proc/{pid}/net/{name}').read_text().splitlines()[1:]
            if len(rows)>4096:raise ValueError('endpoint table cap')
            for row in rows:
                values=row.split();address,encoded=values[1].split(':')
                if values[3]=='0A' and int(encoded,16)==port and values[9] in inodes:
                    listeners.append({'inode':values[9],'address':address,'port':port})
        if len(listeners)!=1 or listeners[0]['address'] not in ('0100007F','00000000000000000000000001000000'):
            raise ValueError('exact owned loopback socket unavailable')
        # scripts/daemon.sh backgrounds real uv, which normally waits in Rust.
        # Bind that exact owned launcher before it can consume the daemon wait.
        launcher=self.identities[pid]['birth_parent']
        if launcher not in self.identities:
            raise ValueError('original owned uv launcher missing')
        launcher_identity=self.identity(launcher)
        with Path(f'/proc/{launcher}/cmdline').open('rb') as handle:raw_argv=handle.read(4097)
        if len(raw_argv)>4096:raise ValueError('launcher argv cap')
        argv=raw_argv.split(b'\0')
        executable=os.readlink(f'/proc/{launcher}/exe')
        if (launcher_identity[:2]!=(self.identities[launcher]['uid'],self.identities[launcher]['start'])
                or launcher_identity[2]!=self.self_pid
                or argv[1:]!=[b'run',b'python',b'-m',b'runtime.daemon',b'']
                or Path(executable).name!='uv'):
            raise ValueError('public uv launcher identity/argv/adoption unavailable')
        log_path=Path(log)
        if (log_path.resolve(strict=True)!=log_path or log_path.stat().st_uid!=os.getuid()
                or not log_path.is_relative_to(self.evidence.parent/'scratch')
                or not stat.S_ISREG(log_path.lstat().st_mode)):
            raise ValueError('owned daemon log unavailable')
        if self.identity(pid)!=(self.identities[pid]['uid'],self.identities[pid]['start'],launcher):
            raise ValueError('daemon original launcher edge changed during endpoint binding')
        self.register_log(node,log)
        log_identity=log_path.lstat()
        logged=self.logs[str(log_path)]
        if logged['log_identity'] not in (None,(log_identity.st_dev,log_identity.st_ino)):
            raise ValueError('prelaunch log inode changed')
        logged['log_identity']=(log_identity.st_dev,log_identity.st_ino)
        self.required_roles.update((pid,launcher))
        self.ports[pid]={'node':node,'pid':pid,'port':port,'listener':listeners[0],'log':str(log_path),
                          'start':self.identities[pid]['start'],'wait_log':wait_log,'wait_rows':0,
                          'launcher':launcher,'launcher_start':launcher_identity[1],
                          'launcher_executable':executable,'launcher_argv':[x.decode() for x in argv[:-1]],
                          'log_identity':(log_identity.st_dev,log_identity.st_ino)}
        self.event(kind='endpoint_bound',pid=pid,node=node,listener=listeners[0])

    def close_case(self,pid,port,node,direct_pid,deadline,*,interrupt=False):
        endpoint=self.ports.get(pid)
        if endpoint is None or endpoint['pid']!=pid or endpoint['port']!=port or endpoint['node']!=node:
            raise ValueError('unbound case endpoint closure')
        self.capture_logs()
        launcher=endpoint['launcher']
        selected={pid,launcher}
        # Kill ONLY the bound adopted uv parent first; it cannot then reap in an
        # unobserved Rust wait. The live daemon must become our actual child.
        # Original PID/start/birth-parent remain unchanged in the evidence.
        entry=self.identities[launcher]
        if not entry['exited']:
            current=self.identity(launcher)
            if current!=(entry['uid'],endpoint['launcher_start'],self.self_pid):
                raise ValueError('uv launcher ownership changed before adoption')
            signal.pidfd_send_signal(entry['fd'],signal.SIGKILL)
            self.event(kind='launcher_adoption_signal',pid=launcher,node=node,signal=signal.SIGKILL)
        prior_wait=self.identities[pid].get('supervisor_wait')
        adopted=(self.closed_identity(pid) and prior_wait is not None
            and (prior_wait['owner'],prior_wait['child'],prior_wait['start'])==
                (self.self_pid,pid,endpoint['start']))
        # Fixture teardown may revisit its already interrupted endpoint. Reuse
        # only the actual SAME-identity wait, never PID absence or port closure.
        while not adopted and time.monotonic()<deadline:
            self.discover()
            try:
                current=self.identity(pid)
            except FileNotFoundError:
                break
            if current[:2]!=(self.identities[pid]['uid'],endpoint['start']):
                raise ValueError('daemon identity changed before adoption')
            if current[2]==self.self_pid:
                adopted=True
                self.event(kind='daemon_adopted',pid=pid,node=node,owner=self.self_pid,
                           birth_parent=self.identities[pid]['birth_parent'],start=current[1])
                break
            time.sleep(.02)
        if not adopted:
            self.failure('daemon adoption',ValueError('required daemon adoption unavailable; no observed wait'))
        for _ in range(CAPS['processes']):
            added={p for p,v in self.identities.items() if (v['parent'] in selected or v['birth_parent'] in selected)}
            if added<=selected:break
            selected.update(added)
        for p in selected:
            entry=self.identities[p]
            if not entry['exited']:
                try:
                    signal.pidfd_send_signal(entry['fd'],signal.SIGKILL if interrupt else signal.SIGTERM)
                    self.event(kind='public_interruption' if interrupt else 'case_cleanup',pid=p,node=node)
                except ProcessLookupError:pass
        while time.monotonic()<deadline:
            self.discover();self.observe_exits(direct_pid);self.wait_receipts()
            # Adoption after termination may reveal new genuine child edges.
            expanded={p for p,v in self.identities.items() if (v['parent'] in selected or v['birth_parent'] in selected)}
            for p in expanded-selected:
                selected.add(p)
                if not self.identities[p]['exited']:
                    signal.pidfd_send_signal(self.identities[p]['fd'],signal.SIGKILL)
            if all(self.closed_identity(p) for p in selected):break
            time.sleep(.02)
        probe=socket.socket();probe.settimeout(.2)
        try:port_closed=probe.connect_ex(('127.0.0.1',port))!=0
        except BaseException as exc:
            self.failure('port probe',exc)
            raise
        finally:
            try:probe.close()
            except BaseException as exc:self.failure('port probe close',exc)
        # Public upload/launcher helpers are pytest children rather than daemon
        # descendants. Their actual exit/reap proof also belongs in the closed
        # receipt; never retain them merely because the live parent is pytest.
        # Another live endpoint may still owe roles, so keep its helpers.
        if not any(other!=pid and not self.closed_identity(other) for other in self.ports):
            selected.update(p for p in self.identities if p not in self.required_roles
                and self.closed_identity(p))
        clean=not self.errors and all(self.closed_identity(p) for p in selected)
        return {'pid':pid,'node':node,'closed':clean,'port_closed':port_closed,'port':port,
            'owned_listener':endpoint['listener'],
            'launcher':{key:endpoint[key] for key in ('launcher','launcher_start','launcher_executable','launcher_argv')},
            'identities':{p:{k:v for k,v in self.identities[p].items() if k!='fd'} for p in selected},
            'events':[row for row in self.events if row.get('pid') in selected]}

    def retire_case(self, closure: dict, receipt_path: Path) -> None:
        """Retire only durably exported, completely settled owned identities."""
        if not closure['closed'] or not closure['port_closed']:
            raise ValueError('unclosed case cannot retire')
        if closed_read(receipt_path,262144)!=json.loads(json.dumps(closure)):
            raise ValueError('immutable closure export unavailable')
        selected=set(closure['identities'])
        if closure['node'] not in self.retired_cases and len(self.retired_cases)>=len(NODES):
            raise ValueError('case closure inventory cap')
        # An unresolved descendant still needs every observed ancestor link.
        protected=set()
        frontier=[pid for pid in self.identities if not self.closed_identity(pid)]
        while frontier:
            pid=frontier.pop()
            if pid in protected:continue
            protected.add(pid)
            entry=self.identities.get(pid)
            if entry is not None:
                frontier.extend(p for p in (entry['parent'],entry['birth_parent']) if p in self.identities)
        group=self.retired_cases.setdefault(closure['node'],[])
        if len(group)>=3:raise ValueError('per-case batch receipt cap')
        retired=[]
        for pid in sorted(selected-protected):
            if not self.closed_identity(pid):raise ValueError('unresolved identity retirement refused')
            os.close(self.identities[pid]['fd'])
            del self.identities[pid]
            self.required_roles.discard(pid)
            retired.append(pid)
        group.append({'receipt':str(receipt_path),'sha256':sha(receipt_path),
            'retired':retired,'retained':sorted(selected&protected)})
        retired_set=set(retired)
        self.events=[row for row in self.events if row.get('pid') not in retired_set]

    def wait_receipts(self):
        for endpoint in self.ports.values():
            path=Path(endpoint['wait_log'])
            if path.parent!=self.evidence or not path.is_file():
                raise ValueError('source-bound wait receipt missing')
            info=path.stat()
            if info.st_uid!=os.getuid() or info.st_size>CAPS['observer']:
                raise ValueError('wait journal owner/cap')
            raw=path.read_bytes()
            lines=raw.splitlines()
            if raw and not raw.endswith(b'\n'):lines=lines[:-1]
            for line in lines[endpoint['wait_rows']:]:
                row=json.loads(line)
                if (row['owner']!=endpoint['pid'] or type(row['returncode']) is not int
                        or row['head']!=self.head
                        or row['code_sha256']!=hashlib.sha256(marshal.dumps(subprocess.Popen.wait.__code__,2)).hexdigest()):
                    raise ValueError('unbound parent wait')
                child=row['child']
                if child not in self.identities:
                    # Tiny-lived unbound children never acquire signalling rights.
                    self.event(kind='tiny_lived_wait',pid=child,owner=row['owner'],status=row['returncode'],
                        independently_bound=False)
                else:
                    entry=self.identities[child]
                    if entry['birth_parent']!=row['owner']:
                        raise ValueError('wait parent edge mismatch')
                    entry['parent_wait']=row['returncode']
                    if entry.get('removed_after_exit'):entry['reaped']=True
                    self.event(kind='source_parent_wait',pid=child,owner=row['owner'],status=row['returncode'])
            endpoint['wait_rows']=len(lines)

    def capture_logs(self):
        """Checkpoint bounded owned log bytes before any closure/abort decision."""
        for endpoint in self.logs.values():
            path=Path(endpoint['log'])
            try:before=path.lstat()
            except FileNotFoundError:
                if endpoint['log_identity'] is None:continue
                raise ValueError('previously observed daemon log missing')
            if endpoint['log_identity'] is None:
                endpoint['log_identity']=(before.st_dev,before.st_ino)
            if (path.resolve(strict=True)!=path or not stat.S_ISREG(before.st_mode)
                    or before.st_uid!=os.getuid()
                    or (before.st_dev,before.st_ino)!=endpoint['log_identity']):
                raise ValueError('daemon log identity unavailable')
            fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
            try:
                opened=os.fstat(fd)
                if (opened.st_dev,opened.st_ino)!=(before.st_dev,before.st_ino):
                    raise ValueError('daemon log changed during capture')
                with os.fdopen(fd,'rb',closefd=False) as handle:
                    data=handle.read(CAPS['daemon_log']+1)
            except BaseException as exc:
                self.failure('log capture',exc)
                raise
            finally:
                try:os.close(fd)
                except BaseException as exc:self.failure('log fd close',exc)
            overflow=len(data)>CAPS['daemon_log']
            dest=self.evidence/('daemon-'+hashlib.sha256(endpoint['node'].encode()).hexdigest()[:20]
                +'-'+hashlib.sha256(str(path).encode()).hexdigest()[:12]+'.log')
            # Our fixed owned checkpoint can be replaced; closure/history cannot.
            evidence_publish(dest,data[:CAPS['daemon_log']],CAPS['daemon_log'],self.evidence,replace=True)
            if overflow:raise ValueError('daemon capture overflow; bounded prefix retained')

    def monitor(self):
        self.wait_receipts()
        # All mutation and this snapshot share allocation; never inspect an
        # active temporary as if it were an interrupted publication.
        with evidence_allocation(self.evidence):
            total,entries,files=evidence_usage(self.evidence)
            if any(name.endswith('.tmp') for name in files):
                raise ValueError('interrupted evidence publication remains')
            for name,size in files.items():
                cap=FINAL_CAPS.get(name,CAPS['command_log'] if name=='pytest.log' else CAPS['metadata'])
                if size>cap:raise ValueError('external evidence cap exceeded: '+name)
            if sum(size for name,size in files.items() if name.endswith('.log'))>CAPS['logs']:
                raise ValueError('aggregate required log cap')
        for endpoint in self.ports.values():
            path=Path(endpoint['log'])
            if path.exists() and path.stat().st_size>CAPS['daemon_log']:
                raise ValueError('required daemon log overflow')

    def cleanup(self, child: subprocess.Popen | None, deadline: float, *, finalize=True) -> bool:
        # Every obligation is independent. A receipt failure never strands fds
        # or prevents the actual Popen owner from attempting its consuming wait.
        self.direct_wait = None
        def attempt(label, action):
            try:return action()
            except BaseException as exc:
                self.failure(label,exc)
                return None
        attempt('log capture', self.capture_logs)
        attempt('TERM bound', lambda:self.signal_bound(signal.SIGTERM))
        if child is not None:
            attempt('TERM direct', lambda:child.terminate() if child.poll() is None else None)
        # Reserve part of the remaining budget for KILL and the consuming wait.
        grace=min(deadline,time.monotonic()+min(10,max(0,deadline-time.monotonic())/2))
        def settle(stop):
            while time.monotonic()<stop:
                attempt('discover',self.discover)
                if child is not None:attempt('observe',lambda:self.observe_exits(child.pid))
                if all(row['exited'] for row in self.identities.values()):break
                time.sleep(.02)
        attempt('TERM grace',lambda:settle(grace))
        attempt('KILL bound',lambda:self.signal_bound(signal.SIGKILL))
        if child is not None:
            attempt('KILL direct',lambda:child.kill() if child.poll() is None else None)
        # The direct owner can consume its wait even if pidfd observation failed.
        # Do this before descendant settlement rather than after the deadline.
        if child is not None:
            def consuming_wait():
                self.direct_wait=child.wait(timeout=max(0,deadline-time.monotonic()))
                if child.pid in self.identities:
                    self.identities[child.pid]['exited']=True
                    self.identities[child.pid]['reaped']=True
                self.event(kind='direct_reaped',pid=child.pid,returncode=self.direct_wait)
            attempt('direct wait',consuming_wait)
        attempt('KILL settlement',lambda:settle(deadline))
        attempt('final discover',self.discover)
        if child is not None:attempt('final observe',lambda:self.observe_exits(child.pid))
        attempt('final log capture',self.capture_logs)
        clean=(child is not None and self.direct_wait is not None and not self.errors
               and all(self.closed_identity(pid) for pid in self.identities))
        return self.finalize_cleanup(clean) if finalize else clean

    def finalize_cleanup(self, clean):
        # Drains can still need the exact bound pidfds for queued case-close
        # events. Release them afterwards, before fallible receipt publication.
        for row in self.identities.values():
            if row.get('fd') is not None:
                fd=row['fd'];row['fd']=None
                try:os.close(fd)
                except BaseException as exc:self.failure('pidfd close',exc)
        clean=clean and not self.errors
        try:write_receipt(self.evidence/'processes.json',
            {'identities':{pid:{k:v for k,v in row.items() if k!='fd'} for pid,row in self.identities.items()},
             'events':self.events,'errors':self.errors,'closure':clean,'retired_cases':self.retired_cases,
             'error_overflow':{'observations':self.error_overflow,'meaning':ERROR_OVERFLOW_MEANING},
             'direct_wait_returncode':self.direct_wait},2097152,evidence=self.evidence)
        except BaseException as exc:self.failure('process publication',exc)
        return clean and not self.errors


class CaptureChannels:
    """One parser for live reads and bounded residue; pipe state is not wait proof."""
    def __init__(self, evidence, on_phase, record=None):
        self.evidence=evidence;self.on_phase=on_phase;self.log_identity=None
        self.record=record
        self.counts={'output':0,'phase':0,'junit':0};self.buffer=b'';self.junit=bytearray()
        self.drains={};self.failed=set();self.processing_deadline=None

    def consume(self, channel, chunk):
        first=None
        def failure(label,exc):
            nonlocal first
            if first is None:first=exc
            self.failed.add(channel)
            if self.record is not None:self.record(label,exc)
        cap={'output':CAPS['command_log'],'phase':CAPS['phases'],'junit':CAPS['junit']}[channel]
        remaining=max(0,cap-self.counts[channel])
        # Counts are observed prefix lengths, saturating one byte above cap.
        self.counts[channel]=min(cap+1,self.counts[channel]+len(chunk))
        kept=chunk[:remaining]
        if self.counts[channel]>cap:
            failure(channel+' capture cap',ValueError(channel+' capture overflow; bounded prefix retained'))
        try:
            if channel=='output' and kept:
                if self.log_identity is None:
                    self.log_identity=evidence_append(self.evidence/'pytest.log',b'',cap,self.evidence,exclusive=True)
                evidence_append(self.evidence/'pytest.log',kept,cap,self.evidence,identity=self.log_identity)
            elif channel=='junit':self.junit.extend(kept)
            elif channel=='phase':
                available=12288-len(self.buffer)
                self.buffer+=kept[:available]
                if len(kept)>available:failure('phase framing',ValueError('phase framing cap; bounded prefix retained'))
                while b'\n' in self.buffer:
                    if self.processing_deadline is not None and time.monotonic()>self.processing_deadline:
                        failure('phase parsing deadline',TimeoutError('terminal phase parsing deadline'))
                        break  # Leave the unprocessed residue; no action after expiry.
                    raw,self.buffer=self.buffer.split(b'\n',1)
                    if len(raw)>CAPS['phase']:
                        failure('phase row',ValueError('phase row cap'))
                        continue
                    try:row=json.loads(raw)
                    except BaseException as exc:
                        failure('phase parse',exc)
                        continue
                    try:self.on_phase(row)
                    except BaseException as exc:failure('phase action',exc)
        except BaseException as exc:failure(channel+' capture',exc)
        if first is not None:raise first

    def drain(self, channel, fd, producer_closed, attempt, *, deadline=None):
        # O_RDWR FIFOs report EAGAIN rather than EOF even after the actual owned
        # producer closes. That fact is recorded separately from closure/waits.
        status={'producer_closed':producer_closed,'read_state':None,'complete':False}
        self.drains[channel]=status
        deadline=min(time.monotonic()+1,deadline) if deadline is not None else time.monotonic()+1
        if channel=='phase':
            self.processing_deadline=min(deadline,self.processing_deadline) if self.processing_deadline is not None else deadline
        cap={'output':CAPS['command_log'],'phase':CAPS['phases'],'junit':CAPS['junit']}[channel]
        budget=cap+8192
        try:
            os.set_blocking(fd,False)
            while budget>0 and time.monotonic()<deadline:
                try:chunk=os.read(fd,min(8192,budget))
                except BlockingIOError:
                    status['read_state']='EAGAIN';break
                if not chunk:
                    status['read_state']='EOF';break
                budget-=len(chunk)
                def consume_chunk():
                    try:self.consume(channel,chunk)
                    except BaseException:
                        self.failed.add(channel);raise
                attempt('drain '+channel,consume_chunk)
            status['frame_complete']=channel!='phase' or not self.buffer
            status['complete']=(producer_closed and status['read_state'] in ('EOF','EAGAIN')
                and status['frame_complete'] and channel not in self.failed and self.counts[channel]<=cap)
            if not status['complete']:raise ValueError(channel+' terminal drain incomplete')
        except BaseException as exc:
            self.failed.add(channel)
            if self.record is not None:self.record('terminal '+channel,exc)
            raise


def supervise(binding: dict, scratch: Path, *, deadline: float | None = None) -> int:
    runner_temp=Path(os.environ['RUNNER_TEMP']).resolve(strict=True)
    expected=runner_temp/'attachment-qa'/binding['task']/f"{binding['run']}-{binding['attempt']}"/'scratch'
    if scratch!=expected or scratch.exists() or scratch.is_symlink():raise ValueError('scratch binding/refusal')
    evidence=scratch.parent/'evidence'
    child=None;tree=None;selector=None;phase_fd=None;junit_fd=None;lock_fd=None;evidence_owned=False
    start=time.monotonic();phase_deadline=start+60;events=[];clean=False;total=None
    # A nested synthetic trial inherits the original call budget. The actual
    # top-level owner retains its unchanged aggregate and cleanup guarantees.
    cleanup_deadline=min(start+1260,deadline) if deadline is not None else start+1260
    run_deadline=min(start+1200,deadline) if deadline is not None else start+1200
    errors=[];first=None;reported=[];prior_handlers={}
    overflow={'observations':0,'meaning':ERROR_OVERFLOW_MEANING}
    def record(label,exc):
        nonlocal first
        if any(prior is exc for prior in reported):return first
        if first is None:first=exc
        if len(reported)<ERROR_LIMIT:
            reported.append(exc)
            errors.append({'operation':label[:ERROR_LABEL_LIMIT],'error':error_text(exc)})
        else:overflow['observations']=min(ERROR_OVERFLOW_LIMIT,overflow['observations']+1)
        return first
    channels=CaptureChannels(evidence,lambda row:on_phase(row),record=record)
    def attempt(label,action):
        try:return action()
        except BaseException as exc:
            if label.startswith('drain '):channels.failed.add(label.split(' ',1)[1])
            record(label,exc)
            return None
    def on_phase(row):
        nonlocal phase_deadline
        if row.get("nonce") != descriptor["nonce"] or row.get("node") not in NODES:
            raise ValueError("phase source/node/nonce mismatch")
        if len(events)>=4096:raise ValueError("phase row count cap")
        events.append(row)
        if row.get("event") == "start":
            phase_deadline = time.monotonic()+PHASE_LIMITS[row["phase"]]
        elif row.get("event") == "finish":
            phase_deadline = time.monotonic()+5
        elif row.get('event')=='daemon-log':
            tree.register_log(row['node'],row['daemon_log'])
            tree.capture_logs()
        elif row.get('event')=='endpoint':
            tree.bind_endpoint(row['daemon_pid'],row['port'],row['node'],row['daemon_log'],row['wait_log'])
        elif row.get('event') in ('interrupt','case-close'):
            pid=row['daemon_pid'];port=row['port'];node=row['node']
            interruption=row['event']=='interrupt'
            tree.capture_logs()
            tree.role_receipts(Path(row['roles']),node)
            closure_deadline=min(time.monotonic()+4,channels.processing_deadline) if channels.processing_deadline is not None else time.monotonic()+4
            closure=tree.close_case(pid,port,node,child.pid,closure_deadline,interrupt=interruption)
            closure_path=Path(row['closure'])
            stem='case-close-'+hashlib.sha256(node.encode()).hexdigest()[:20]
            expected_names={f'closure-{pid}.json'} if interruption else {stem+'.json',stem+'-batch-1.json',stem+'-batch-2.json'}
            if closure_path.parent!=evidence or closure_path.name not in expected_names:
                raise ValueError('closure receipt binding mismatch')
            write_receipt(closure_path,closure,262144,evidence=evidence)
            tree.capture_logs()
            if not closure['closed'] or not closure['port_closed']:
                raise ValueError('owned case tree/port closure incomplete')
            if not interruption:
                endpoint=tree.ports.pop(pid)
                tree.logs.pop(endpoint['log'])
                tree.retire_case(closure,closure_path)
        else:
            raise ValueError("unknown phase event")
    def interrupted(signum,frame):raise InterruptedError(f'owned supervisor received signal {signum}')
    try:
        for sig in (signal.SIGINT,signal.SIGTERM):
            prior_handlers[sig]=signal.getsignal(sig);signal.signal(sig,interrupted)
        scratch.parent.mkdir(parents=True,mode=0o700,exist_ok=False)
        scratch.mkdir(mode=0o700);evidence.mkdir(mode=0o700);evidence_owned=True
        lock_fd=os.open(evidence/EVIDENCE_LOCK,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        os.close(lock_fd);lock_fd=None
        junit_pipe=scratch.parent/'junit.fifo';os.mkfifo(junit_pipe,0o600)
        junit_fd=os.open(junit_pipe,os.O_RDWR|os.O_NONBLOCK)
        phase_pipe=scratch.parent/'phases.fifo';os.mkfifo(phase_pipe,0o600)
        phase_fd=os.open(phase_pipe,os.O_RDWR|os.O_NONBLOCK)
        descriptor={'version':1,**binding,'selection':SELECTION,'nodes':NODES,
            'nonce':os.urandom(24).hex(),'evidence':str(evidence),'scratch':str(scratch),
            'phase_pipe':str(phase_pipe),'start':start,'deadline':start+1200,'phase_limits':PHASE_LIMITS,'caps':CAPS}
        descriptor_path=scratch.parent/'descriptor.json'
        write_receipt(descriptor_path,descriptor,16384)
        write_receipt(evidence/'source.json',binding,262144,evidence=evidence)
        argv=[binding['python'],'tests/helpers/integration_parent.py','--','pytest','-p',
            'tests.helpers.attachment_feedback_fixture','-m','integration',*NODES,
            f'--attachment-qa-descriptor={descriptor_path}','-v','--tb=short','--capture=no','--show-capture=no',
            f"--basetemp={scratch/'pytest'}",f'--junitxml={junit_pipe}']
        tree=OwnedTree(evidence,record=record);tree.head=binding['head']
        env=dict(os.environ,TMPDIR=str(scratch),TMP=str(scratch),TEMP=str(scratch),
                 HAPPYRANCH_TEST_REAL_PLATFORM='1',PYTHONDONTWRITEBYTECODE='1')
        child=subprocess.Popen(argv,cwd=Path(binding['source']),env=env,stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT,start_new_session=True)
        tree.discover()
        os.set_blocking(child.stdout.fileno(),False)
        selector=selectors.DefaultSelector()
        for fd,name in ((child.stdout,'output'),(phase_fd,'phase'),(junit_fd,'junit')):
            selector.register(fd,selectors.EVENT_READ,name)
        channels.log_identity=evidence_append(evidence/'pytest.log',b'',CAPS['command_log'],evidence,exclusive=True)
        next_capture=start
        while child.poll() is None:
            tree.discover();tree.observe_exits(child.pid)
            if time.monotonic()>=next_capture:
                tree.capture_logs();next_capture=time.monotonic()+.2
            tree.monitor()
            if time.monotonic()>min(run_deadline,phase_deadline):raise TimeoutError('aggregate or independent phase watchdog expired')
            for key,_ in selector.select(.02):
                fd=key.fileobj if type(key.fileobj) is int else key.fileobj.fileno()
                try:chunk=os.read(fd,8192)
                except BlockingIOError:continue
                if not chunk:selector.unregister(key.fileobj);continue
                try:channels.consume(key.data,chunk)
                except BaseException:
                    channels.failed.add(key.data);raise
        child.wait(timeout=min(1,max(0,run_deadline-time.monotonic())))
    except BaseException as exc:record('acquisition/capture',exc)
    finally:
        # The tree's consuming wait precedes drains. An error anywhere leaves
        # the other acquired resources and channels with their own attempts.
        if tree is not None:
            clean=bool(attempt('owned cleanup',lambda:tree.cleanup(child,min(cleanup_deadline,time.monotonic()+60),finalize=False)))
        producer_closed=bool(attempt('producer closure',lambda:(child is not None and tree is not None and tree.direct_wait is not None
                         and all(tree.closed_identity(pid) for pid in tree.identities))))
        output_fd=None
        if child is not None and child.stdout is not None:output_fd=attempt('stdout descriptor',child.stdout.fileno)
        for name,fd in (('output',output_fd),
                        ('phase',phase_fd),('junit',junit_fd)):
            if fd is not None:attempt('terminal '+name,lambda name=name,fd=fd:channels.drain(name,fd,producer_closed,attempt,deadline=cleanup_deadline))
        if tree is not None:
            clean=bool(attempt('process finalization',lambda:tree.finalize_cleanup(clean)))
        if child is not None and child.stdout is not None:attempt('stdout close',child.stdout.close)
        if selector is not None:attempt('selector close',selector.close)
        for name,fd in (('phase',phase_fd),('junit',junit_fd),('lock',lock_fd)):
            if fd is not None:attempt(name+' fd close',lambda fd=fd:os.close(fd))
        for sig,handler in prior_handlers.items():attempt('signal restore',lambda sig=sig,handler=handler:signal.signal(sig,handler))
    if not evidence_owned:
        print(json.dumps({'complete':False,'storage':'unavailable','errors':errors,'error_overflow':overflow}),file=sys.stderr)
        return 1
    if channels.junit:attempt('JUnit publication',lambda:evidence_publish(evidence/'cases.xml',bytes(channels.junit),CAPS['junit'],evidence))
    attempt('phase publication',lambda:write_receipt(evidence/'phases.json',
        {'events':events,'capture':channels.counts,'drains':channels.drains,'errors':errors,'error_overflow':overflow},2097152,evidence=evidence))
    import xml.etree.ElementTree as ET
    complete=False
    def acceptance():
        xml=evidence/'cases.xml'
        if not xml.is_file() or xml.stat().st_size>CAPS['junit']:raise ValueError('JUnit absent/oversized')
        cases=list(ET.parse(xml).iter('testcase'))
        observations=closed_read(evidence/'observations.json',1048576)
        findings=closed_read(evidence/'finding-assertions.json',262144)
        actual_nodes=tuple(TEST_PATH+'::'+case.attrib['name'] for case in cases)
        phase_pairs={(row['node'],row['phase'],row['event']) for row in events if row['event'] in ('start','finish')}
        expected_pairs={(node,phase,event) for node in NODES for phase in PHASE_LIMITS for event in ('start','finish')}
        return (actual_nodes==NODES and expected_pairs<=phase_pairs
            and all(not any(c.find(tag) is not None for tag in ('failure','error','skipped')) for c in cases)
            and set(observations)==set(SUBOBSERVATIONS) and all(v.get('outcome')=='passed' for v in observations.values())
            and set(findings)==set(FINDING_ROWS) and all(v.get('outcome')=='passed' and v.get('head')==binding['head']
                and v.get('node')==NODES[0] and v.get('source_hashes')==binding['source_hashes'] for v in findings.values()))
    complete=bool(attempt('acceptance',acceptance))
    def inventory():
        with evidence_allocation(evidence):
            total,entries,files=evidence_usage(evidence)
            if any(name.endswith('.tmp') for name in files):raise ValueError('interrupted evidence publication remains')
            return total
    total=attempt('final inventory',inventory)
    complete=(complete and clean and first is None and total is not None
              and set(channels.drains)=={'output','phase','junit'} and all(row['complete'] for row in channels.drains.values()))
    command_exit=None if child is None else child.returncode
    exit_code=command_exit if command_exit else (0 if complete else 1)
    result_stored=False
    def publish_result():
        nonlocal result_stored
        write_receipt(evidence/'result.json',
            {'exit':exit_code,'command_exit':command_exit,'complete':complete,'closure':clean,
             'error':None if first is None else error_text(first),'errors':errors,'error_overflow':overflow,
             'manifest':MANIFEST_SHA256,'bytes':total},evidence=evidence)
        result_stored=True
    attempt('result publication',publish_result)
    if not result_stored:
        print(json.dumps({'complete':False,'storage':'unavailable','errors':errors,'error_overflow':overflow}),file=sys.stderr)
    if first is not None:return exit_code or 1
    if complete:
        attempt('scratch removal',lambda:shutil.rmtree(scratch))
    return exit_code if first is None else (exit_code or 1)


def main() -> int:
    parser=argparse.ArgumentParser()
    parser.add_argument("--task-id",required=True)
    parser.add_argument("--expected-sha",required=True)
    parser.add_argument("--scratch",type=Path)
    parser.add_argument("--selection",choices=(SELECTION,),default=SELECTION)
    parser.add_argument("--admit-only",action="store_true")
    args=parser.parse_args()
    binding=admit(args.task_id,args.expected_sha,interpreter=not args.admit_only)
    if args.admit_only:
        print(json.dumps(binding,sort_keys=True));return 0
    if args.scratch is None:
        parser.error("--scratch required")
    return supervise(binding,args.scratch)


if __name__=="__main__":
    try:
        raise SystemExit(main())
    except (OSError,ValueError,subprocess.SubprocessError) as exc:
        print("THR304 refused/incomplete: "+str(exc),file=sys.stderr)
        raise SystemExit(2)
