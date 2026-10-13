"""Closed THR304 source/release admission and Linux owned-process supervisor.

This manual QA lane is separate from automated lint and daily Product E2E.
EXECUTION_OWNER is manager-allocated to TASK-11079; final-head review and release remain pending.
"""
from __future__ import annotations

import argparse
import ast
import ctypes
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


def write_receipt(path: Path, value, cap: int = 1048576):
    encoded = (json.dumps(value, sort_keys=True) + "\n").encode()
    if len(encoded) > cap:
        raise ValueError("receipt cap exceeded")
    with path.open("xb") as handle:
        os.chmod(path, 0o600)
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())


class OwnedTree:
    """Discover only positively owned child edges; signals use bound pidfds."""
    def __init__(self, evidence: Path):
        libc = ctypes.CDLL(None, use_errno=True)
        if libc.prctl(36, 1, 0, 0, 0):  # PR_SET_CHILD_SUBREAPER
            raise OSError(ctypes.get_errno(), "subreaper refused")
        value = ctypes.c_int()
        if libc.prctl(37, ctypes.byref(value), 0, 0, 0) or value.value != 1:
            raise ValueError("subreaper readback refused")
        self.identities, self.errors, self.events = {}, [], []
        self.evidence = evidence
        self.self_pid = os.getpid()
        self.ports = {}
        self.required_roles=set()
        self.head=None
        self.retired_cases={}

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

    def discover(self):
        frontier, visited = [self.self_pid], set()
        # Retain known nonadopted ancestors to cover teardown/reparenting.
        frontier.extend(pid for pid, entry in self.identities.items() if not entry["exited"])
        while frontier:
            parent = frontier.pop()
            if parent in visited:
                continue
            visited.add(parent)
            try:
                for pid in self.children(parent):
                    if pid not in self.identities:
                        if len(self.identities) >= CAPS["processes"]:
                            raise ValueError("owned process cap")
                        before = self.identity(pid)
                        if before[0] != os.getuid() or before[2] != parent:
                            raise ValueError("owned edge/UID mismatch")
                        fd = os.pidfd_open(pid)
                        after = self.identity(pid)
                        if before != after or pid not in self.children(parent):
                            os.close(fd)
                            raise ValueError("owned edge changed during pidfd binding")
                        self.identities[pid] = {"uid": before[0], "start": before[1], "parent": parent,
                                                "fd": fd, "exited": False, "reaped": False,"birth_parent":parent,"parent_wait":None,"removed_after_exit":False}
                        self.event(kind="bound", pid=pid, parent=parent, start=before[1])
                    else:
                        entry=self.identities[pid]
                        current=self.identity(pid)
                        if current[0]!=entry['uid'] or current[1]!=entry['start']:
                            raise ValueError('owned PID identity changed')
                        if current[2]!=entry['parent']:
                            if current[2] not in self.identities and current[2]!=self.self_pid:
                                raise ValueError('unobserved reparent edge')
                            self.event(kind='reparent',pid=pid,before=entry['parent'],after=current[2])
                            entry['parent']=current[2]
                    frontier.append(pid)
            except FileNotFoundError:
                if parent == self.self_pid:
                    raise
            except (OSError, ValueError) as exc:
                self.errors.append(type(exc).__name__ + ":" + str(exc)[:256])

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
                            os.waitid(os.P_PIDFD, entry["fd"], os.WEXITED)
                            entry["reaped"] = True
                            self.event(kind="reaped", pid=pid)
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
            finally:
                poll.close()

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
                    self.errors.append("pidfd signal:" + str(exc)[:128])

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
                    self.errors.append('required provider role identity missing')
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
                    self.errors.append('required callback identity not observed')

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
        log_path=Path(log)
        if log_path.resolve(strict=True)!=log_path or log_path.stat().st_uid!=os.getuid():
            raise ValueError('owned daemon log unavailable')
        self.required_roles.add(pid)
        self.ports[pid]={'node':node,'pid':pid,'port':port,'listener':listeners[0],'log':str(log_path),
                          'start':self.identities[pid]['start'],'wait_log':wait_log,'wait_rows':0}
        self.event(kind='endpoint_bound',pid=pid,node=node,listener=listeners[0])

    def close_case(self,pid,port,node,direct_pid,deadline,*,interrupt=False):
        endpoint=self.ports.get(pid)
        if endpoint is None or endpoint['pid']!=pid or endpoint['port']!=port or endpoint['node']!=node:
            raise ValueError('unbound case endpoint closure')
        selected={pid}
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
        finally:probe.close()
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

    def monitor(self):
        self.wait_receipts()
        # Refuse oversized external evidence while writers still run.
        files=list(self.evidence.rglob('*'))
        if len(files)>4096:raise ValueError('external evidence file inventory cap')
        total=0
        for path in files:
            info=path.lstat()
            if stat.S_ISLNK(info.st_mode):raise ValueError('external evidence symlink refused')
            if stat.S_ISREG(info.st_mode):
                total+=info.st_size
                cap=CAPS['junit'] if path.name=='cases.xml' else CAPS['command_log'] if path.name=='pytest.log' else CAPS['metadata']
                if info.st_size>cap:raise ValueError('external evidence cap exceeded: '+path.name)
        if total>CAPS['aggregate']:raise ValueError('aggregate evidence cap')
        log_total=sum(path.stat().st_size for path in files if path.is_file() and path.suffix=='.log')
        if log_total>CAPS['logs']:raise ValueError('aggregate required log cap')
        for endpoint in self.ports.values():
            path=Path(endpoint['log'])
            if path.exists() and path.stat().st_size>CAPS['daemon_log']:
                raise ValueError('required daemon log overflow')

    def cleanup(self, child: subprocess.Popen, deadline: float) -> bool:
        self.signal_bound(signal.SIGTERM)
        grace = min(deadline, time.monotonic()+10)
        while time.monotonic() < grace:
            self.discover()
            self.observe_exits(child.pid)
            if all(row["exited"] for row in self.identities.values()):
                break
            time.sleep(0.02)
        self.signal_bound(signal.SIGKILL)
        while time.monotonic() < deadline:
            self.discover()
            self.observe_exits(child.pid)
            if all(row["exited"] for row in self.identities.values()):
                break
            time.sleep(0.02)
        try:
            child.wait(timeout=max(0.01, deadline-time.monotonic()))
            if child.pid in self.identities:
                self.identities[child.pid]["exited"] = True
                self.identities[child.pid]["reaped"] = True
            self.event(kind="direct_reaped", pid=child.pid, returncode=child.returncode)
        except subprocess.TimeoutExpired:
            self.errors.append("direct child still live")
        self.discover()
        self.observe_exits(child.pid)
        clean = not self.errors and all(self.closed_identity(pid) for pid in self.identities)
        write_receipt(self.evidence / "processes.json", {"identities": {pid: {k:v for k,v in row.items() if k != "fd"}
            for pid,row in self.identities.items()}, "events": self.events, "errors": self.errors, "closure": clean, "retired_cases": self.retired_cases}, 2097152)
        for row in self.identities.values():
            os.close(row["fd"])
        return clean


def supervise(binding: dict, scratch: Path) -> int:
    # The exact new invocation directory is unique, canonical and owner-only.
    runner_temp = Path(os.environ["RUNNER_TEMP"]).resolve(strict=True)
    expected = runner_temp / "attachment-qa" / binding["task"] / f"{binding['run']}-{binding['attempt']}" / "scratch"
    if scratch != expected or scratch.exists() or scratch.is_symlink():
        raise ValueError("scratch binding/refusal")
    scratch.parent.mkdir(parents=True, mode=0o700, exist_ok=False)
    scratch.mkdir(mode=0o700)
    evidence = scratch.parent / "evidence"
    evidence.mkdir(mode=0o700)
    phase_pipe = scratch.parent / "phases.fifo"
    os.mkfifo(phase_pipe, 0o600)
    phase_fd = os.open(phase_pipe, os.O_RDWR | os.O_NONBLOCK)
    start = time.monotonic()
    descriptor = {"version": 1, **binding, "selection": SELECTION, "nodes": NODES,
                  "nonce": os.urandom(24).hex(), "evidence": str(evidence), "scratch": str(scratch),
                  "phase_pipe": str(phase_pipe), "start": start, "deadline": start+1200,
                  "phase_limits": PHASE_LIMITS, "caps": CAPS}
    descriptor_path = scratch.parent / "descriptor.json"
    write_receipt(descriptor_path, descriptor, 16384)
    write_receipt(evidence / "source.json", binding, 262144)
    source, python = Path(binding["source"]), binding["python"]
    argv = [python, "tests/helpers/integration_parent.py", "--", "pytest", "-p", "tests.helpers.attachment_feedback_fixture",
            "-m", "integration", *NODES, f"--attachment-qa-descriptor={descriptor_path}",
            "-v", "--tb=short", "--capture=no", "--show-capture=no", f"--basetemp={scratch/'pytest'}",
            f"--junitxml={evidence/'cases.xml'}"]
    tree = OwnedTree(evidence)
    tree.head=binding["head"]
    env = dict(os.environ, TMPDIR=str(scratch), TMP=str(scratch), TEMP=str(scratch),
               HAPPYRANCH_TEST_REAL_PLATFORM="1", PYTHONDONTWRITEBYTECODE="1")
    child = subprocess.Popen(argv, cwd=source, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             start_new_session=True)
    tree.discover()
    selector = selectors.DefaultSelector()
    selector.register(child.stdout, selectors.EVENT_READ, "output")
    selector.register(phase_fd, selectors.EVENT_READ, "phase")
    phase_deadline, buffer, events, counts = start+60, b"", [], {"output":0, "phase":0}
    error = None
    prior_handlers={sig:signal.getsignal(sig) for sig in (signal.SIGINT,signal.SIGTERM)}
    def interrupted(signum,frame):
        raise InterruptedError(f'owned supervisor received signal {signum}')
    for sig in prior_handlers:
        signal.signal(sig,interrupted)
    try:
        with (evidence / "pytest.log").open("xb") as log:
            while child.poll() is None:
                tree.discover()
                tree.observe_exits(child.pid)
                tree.monitor()
                if time.monotonic() > min(start+1200, phase_deadline):
                    raise TimeoutError("aggregate or independent phase watchdog expired")
                for key, _ in selector.select(0.02):
                    chunk = os.read(key.fileobj if type(key.fileobj) is int else key.fileobj.fileno(), 8192)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    counts[key.data] += len(chunk)
                    cap = CAPS["command_log"] if key.data == "output" else CAPS["phases"]
                    if counts[key.data] > cap:
                        raise ValueError("external output/phase capture cap")
                    if key.data == "output":
                        log.write(chunk); log.flush()
                    else:
                        buffer += chunk
                        if len(buffer) > 12288:
                            raise ValueError("phase framing cap")
                        while b"\n" in buffer:
                            raw, buffer = buffer.split(b"\n",1)
                            if len(raw) > 4096:
                                raise ValueError("phase row cap")
                            row=json.loads(raw)
                            if row.get("nonce") != descriptor["nonce"] or row.get("node") not in NODES:
                                raise ValueError("phase source/node/nonce mismatch")
                            if len(events)>=4096:raise ValueError("phase row count cap")
                            events.append(row)
                            if row.get("event") == "start":
                                phase_deadline = time.monotonic()+PHASE_LIMITS[row["phase"]]
                            elif row.get("event") == "finish":
                                phase_deadline = time.monotonic()+5
                            elif row.get('event')=='endpoint':
                                tree.bind_endpoint(row['daemon_pid'],row['port'],row['node'],row['daemon_log'],row['wait_log'])
                            elif row.get('event') in ('interrupt','case-close'):
                                pid=row['daemon_pid'];port=row['port'];node=row['node']
                                interruption=row['event']=='interrupt'
                                tree.role_receipts(Path(row['roles']),node)
                                closure=tree.close_case(pid,port,node,child.pid,time.monotonic()+4,interrupt=interruption)
                                closure_path=Path(row['closure'])
                                stem='case-close-'+hashlib.sha256(node.encode()).hexdigest()[:20]
                                expected_names={f'closure-{pid}.json'} if interruption else {stem+'.json',stem+'-batch-1.json',stem+'-batch-2.json'}
                                if closure_path.parent!=evidence or closure_path.name not in expected_names:
                                    raise ValueError('closure receipt binding mismatch')
                                write_receipt(closure_path,closure,262144)
                                if not closure['closed'] or not closure['port_closed']:
                                    raise ValueError('owned case tree/port closure incomplete')
                                if not interruption:
                                    endpoint=tree.ports.pop(pid)
                                    log_path=Path(endpoint['log'])
                                    if log_path.exists():
                                        data=log_path.read_bytes()
                                        if len(data)>CAPS['daemon_log']:raise ValueError('daemon capture overflow')
                                        dest=evidence/('daemon-'+hashlib.sha256(node.encode()).hexdigest()[:20]+'-'+str(pid)+'.log')
                                        with dest.open('xb') as handle:os.chmod(dest,0o600);handle.write(data)
                                    tree.retire_case(closure,closure_path)
                            else:
                                raise ValueError("unknown phase event")
            child.wait(timeout=1)
    except (OSError, ValueError, TimeoutError, KeyError, subprocess.SubprocessError) as exc:
        error = type(exc).__name__+":"+str(exc)[:1024]
    finally:
        selector.close(); os.close(phase_fd)
        for sig in prior_handlers:
            signal.signal(sig,prior_handlers[sig])
        clean = tree.cleanup(child, min(start+1260, time.monotonic()+60))
    write_receipt(evidence / "phases.json", {"events": events, "capture":counts, "error":error}, 2097152)
    # Results alone cannot satisfy missing subobservations, closure or caps.
    import xml.etree.ElementTree as ET
    complete=False
    try:
        xml=evidence/"cases.xml"
        if not xml.is_file() or xml.stat().st_size > CAPS["junit"]:
            raise ValueError("JUnit absent/oversized")
        cases=list(ET.parse(xml).iter("testcase"))
        observations=closed_read(evidence/"observations.json",1048576)
        actual_nodes=tuple(TEST_PATH+"::"+case.attrib["name"] for case in cases)
        phase_pairs={(row["node"],row["phase"],row["event"]) for row in events if row["event"] in ("start","finish")}
        expected_pairs={(node,phase,event) for node in NODES for phase in PHASE_LIMITS for event in ("start","finish")}
        complete=(actual_nodes==NODES and expected_pairs<=phase_pairs and all(not any(c.find(tag) is not None for tag in ("failure","error","skipped")) for c in cases)
                  and set(observations)==set(SUBOBSERVATIONS)
                  and all(v.get("outcome")=="passed" for v in observations.values()))
    except (OSError, ValueError, ET.ParseError):
        complete=False
    total=sum(p.stat().st_size for p in evidence.rglob("*") if p.is_file())
    exit_code=child.returncode if child.returncode else (0 if complete and clean and error is None and total<=CAPS["aggregate"] else 1)
    write_receipt(evidence/"result.json", {"exit":exit_code,"command_exit":child.returncode,"complete":complete,
        "closure":clean,"error":error,"manifest":MANIFEST_SHA256,"bytes":total})
    if clean:
        # Exact scratch only; source/external evidence are retained.
        shutil.rmtree(scratch)
    return exit_code


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
