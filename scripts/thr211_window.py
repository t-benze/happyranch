"""One THR211 foreground window; operator supplies independently reviewed native facts.

No default native commands, UID, source controls, or evidence locations exist.
This helper never configures Jenkins or inhibits/restores launch sources itself.
The operator performs those exact reviewed actions and confirms their receipts.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import platform
import re
import select
import stat
import subprocess
import sys
import time


class Refused(RuntimeError):
    pass


def private_control(path: Path, *, readable: bool = False) -> None:
    """Every control ancestor is root owned and not group/other writable."""
    if not path.is_absolute() or str(path) != os.path.normpath(path):
        raise Refused("noncanonical control path")
    for item in (path, *path.parents):
        info = item.lstat()
        if stat.S_ISLNK(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise Refused("control path ownership/mode")
    if not readable and path.stat().st_mode & 0o077:
        raise Refused("evidence must be private")


class Window:
    def __init__(self, facts: dict, evidence: Path, *, clock=time.monotonic,
                 wall=time.time, sleep=time.sleep, run=subprocess.run):
        self.facts, self.evidence = facts, evidence
        self.clock, self.wall, self.sleep, self.run = clock, wall, sleep, run
        self.deadline = self.clock() + min(4200, facts['expiry'] - self.wall())
        self.cleanup_deadline = float('inf')
        self.receipt = dict(cleanup='NOT_STARTED', restoration='NOT_STARTED', export='UNKNOWN')

    def command_plan(self) -> dict[str, list[str]]:
        """Validate every required vector without executing any native command."""
        uid = str(self.facts['uid'])
        if type(self.facts['uid']) is not int or not re.fullmatch(r'[1-9][0-9]*', uid):
            raise Refused('nonzero numeric UID required')
        allowed = {
            'id': ['/usr/bin/id', '-u', 'jenkins'],
            'dscl': ['/usr/bin/dscl', '.', '-read', '/Users/jenkins', 'UniqueID'],
            'census': ['/bin/ps', '-axo', 'pid=,ruid=,uid='],
            'term': ['/usr/bin/pkill', '-TERM', '-U', uid, '-u', uid],
            'kill': ['/usr/bin/pkill', '-KILL', '-U', uid, '-u', uid],
        }
        commands = self.facts.get('commands')
        if not isinstance(commands, dict):
            raise Refused('unbound native command plan')
        for name, expected in allowed.items():
            argv = commands.get(name)
            if not isinstance(argv, list) or argv != expected:
                raise Refused('native command outside reviewed UID scope: ' + name)
        return allowed

    def command(self, name: str, remaining: float = 3) -> str:
        argv = self.command_plan()[name]
        timeout = min(3, remaining, self.deadline - self.clock(), self.cleanup_deadline - self.clock())
        if timeout <= 0:
            raise Refused('deadline expired')
        result = self.run(argv, capture_output=True, text=True, timeout=timeout, env={'PATH': '/usr/bin:/bin'})
        if result.returncode != 0:
            raise Refused('native command failed: ' + name)
        return result.stdout.strip()

    def identity(self, remaining: float = 3) -> None:
        uid = str(self.facts['uid'])
        if platform.system() != 'Darwin' or platform.node() != self.facts['host'] or os.geteuid() != 0:
            raise Refused('native identity changed')
        if not re.fullmatch(r'[1-9][0-9]*', uid):
            raise Refused('nonzero numeric UID required')
        if self.command('id', remaining) != uid or self.command('dscl', remaining) != 'UniqueID: ' + uid:
            raise Refused('independent UID disagreement')

    def census(self, remaining: float = 3) -> list[int]:
        rows = []
        uid = self.facts['uid']
        for line in self.command('census', remaining).splitlines():
            fields = line.split()
            if len(fields) != 3 or any(not value.isdigit() for value in fields):
                raise Refused('unreadable census')
            pid, real, effective = map(int, fields)
            if uid in (real, effective):
                if real != uid or effective != uid or pid <= 1:
                    raise Refused('mixed UID target row')
                rows.append(pid)
        return rows

    def operator_receipt(self, key: str) -> dict:
        path = Path(self.facts[key])
        private_control(path)
        text = path.read_text()
        if not text:
            raise Refused('missing operator evidence: ' + key)
        data = json.loads(text)
        expected = dict(request=self.facts['binding'][0], source=self.facts['binding'][1],
                        uid=self.facts['uid'], host=self.facts['host'])
        if any(data.get(name) != value for name, value in expected.items()):
            raise Refused('operator receipt run binding: ' + key)
        return data

    def control(self) -> None:
        """Recheck the independent operating terminal and required evidence access."""
        if platform.system() != 'Darwin' or platform.node() != self.facts['host'] or os.geteuid() != 0:
            raise Refused('native outside-UID root terminal required')
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            raise Refused('independent terminal unavailable')
        private_control(self.evidence)
        ready_parent = Path(self.facts['ready']).parent
        private_control(ready_parent, readable=True)
        for path in (self.evidence, ready_parent):
            if not path.is_dir() or not os.access(path, os.R_OK | os.W_OK | os.X_OK):
                raise Refused('independent control/evidence inaccessible')
        for key in ('native_semantics_review', 'source_inhibition_receipt', 'drain_receipt', 'original_state_receipt'):
            path = Path(self.facts[key])
            private_control(path)
            if not path.is_file() or not path.read_bytes():
                raise Refused('missing operator evidence: ' + key)

    def stopped_hold(self) -> None:
        self.control()
        hold = Path(self.facts['stopped_sources_receipt'])
        if self.operator_receipt('stopped_sources_receipt').get('state') != 'STOPPED_NO_PENDING_LAUNCH':
            raise Refused('source hold changed')
        if not 0 <= self.wall() - hold.stat().st_mtime <= 30:
            raise Refused('source hold confirmation stale')
        self.operator_receipt('export_receipt')

    def ready(self) -> None:
        self.command_plan()
        self.control()
        self.identity()
        for key in ('native_semantics_review', 'source_inhibition_receipt', 'drain_receipt'):
            self.operator_receipt(key)
        if self.operator_receipt('source_inhibition_receipt').get('state') != 'HELD' or self.operator_receipt('drain_receipt').get('state') != 'DRAINED':
            raise Refused('source hold/drain not confirmed')
        import hashlib
        if hashlib.sha256(Path(self.facts['original_state_receipt']).read_bytes()).hexdigest() != self.facts['binding'][5]:
            raise Refused('original state binding mismatch')
        expected = self.facts['binding']
        if len(expected) != 6 or not re.fullmatch(r'[A-Za-z0-9._-]{1,128}', expected[0]):
            raise Refused('missing run binding')
        for value, size in zip(expected[1:], (40, 64, 64, 64, 64)):
            if not re.fullmatch('[0-9a-f]{' + str(size) + '}', value):
                raise Refused('invalid source/Pipeline/evidence binding')
        if self.facts['expiry'] - self.wall() < 3900 or self.facts['expiry'] - self.wall() > 4200:
            raise Refused('finite 70 minute window required')
        # Before admitting setup, only the independently identified agent may exist.
        if not self.facts['agent_pids'] or set(self.census()) != set(self.facts['agent_pids']):
            raise Refused('account not drained')

    def lease(self) -> None:
        # root-controlled readable directory is outside target-writable ancestry.
        self.control()
        self.identity()
        if self.clock() >= self.deadline:
            raise Refused('observation deadline expired')
        request, source, pipeline, config, evaluated, _ = self.facts['binding']
        line = f'{request} {source} {pipeline} {config} {self.facts["uid"]} {self.facts["host"]} {min(int(self.wall()) + 8, self.facts["expiry"])} {evaluated} ready\n'
        path = Path(self.facts['ready'])
        temp = path.with_name(path.name + '.next')
        with temp.open('x') as stream:
            stream.write(line)
        temp.chmod(0o644)
        os.replace(temp, path)

    def capture(self) -> None:
        """Copy only intended partial evidence before the target agent is swept.

        The operator records the actual assigned build/root in an independent
        root-owned receipt. No environment, account HOME, or credentials copied.
        """
        import hashlib
        run_receipt = Path(self.facts['run_receipt'])
        private_control(run_receipt)
        run = json.loads(run_receipt.read_text())
        if run['request'] != self.facts['binding'][0] or run['source'] != self.facts['binding'][1]:
            raise Refused('partial export run binding mismatch')
        root = Path(run['root'])
        if not root.is_absolute() or str(root) != os.path.normpath(root) or root.name != 'thr211-' + str(run['build']):
            raise Refused('unbound private run root')
        files = [(('artifacts', name), name) for name in
                 ('setup.log', 'preparation.json', 'shell-result.json', 'pipeline.json',
                  'workload.log', 'workload-exit.json', 'workload-result.json', 'integration.xml')]
        files.append((('source', 'artifacts', 'integration.xml'), 'source-integration.xml'))
        copied = []
        descriptors = []
        try:
            fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            descriptors.append(fd)
            for component in root.parts[1:]:
                fd = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                descriptors.append(fd)
            for parts, name in files:
                relative_fds = []
                try:
                    parent = fd
                    for component in parts[:-1]:
                        parent = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
                        relative_fds.append(parent)
                    incoming = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
                    with os.fdopen(incoming, 'rb') as stream:
                        info = os.fstat(stream.fileno())
                        if not stat.S_ISREG(info.st_mode) or info.st_uid != self.facts['uid']:
                            raise Refused('foreign partial artifact')
                        data = stream.read(64 * 1024 * 1024 + 1)
                    if len(data) > 64 * 1024 * 1024:
                        raise Refused('partial artifact exceeds bounded export')
                    with (self.evidence / name).open('xb') as output:
                        output.write(data)
                    copied.append(dict(name=name, result='COPIED', bytes=len(data), sha256=hashlib.sha256(data).hexdigest()))
                except FileNotFoundError:
                    copied.append(dict(name=name, result='UNAVAILABLE'))
                finally:
                    for relative_fd in reversed(relative_fds):
                        os.close(relative_fd)
        finally:
            for fd in reversed(descriptors):
                os.close(fd)
            self.receipt['artifacts'] = copied
        if not any(row['result'] == 'COPIED' for row in copied):
            raise Refused('no partial evidence available')
        self.receipt['export'] = 'PARTIAL_OR_COMPLETE_FILES_COPIED'

    def cleanup(self) -> None:
        # Operator has exported partial/complete intended evidence and stopped
        # the agent and every source. No readiness lease remains during cleanup.
        self.receipt['cleanup'] = 'INCOMPLETE'
        end = min(self.deadline, self.clock() + 30)
        self.cleanup_deadline = end
        self.command_plan()
        self.stopped_hold()
        if self.receipt['export'] == 'UNKNOWN':
            self.receipt['export'] = 'OPERATOR_RECEIPTED'
        for phase in ('term', 'kill'):
            self.stopped_hold()
            self.identity(end - self.clock())
            if self.census(end - self.clock()):
                # Exact native UID-intersection command vectors come from the
                # reviewed native receipt; never construct PID/name/PGID targets.
                self.stopped_hold()
                self.command(phase, end - self.clock())
            if phase == 'term':
                self.sleep(min(3, max(0, end - self.clock())))
        quiet = None
        while True:
            if self.clock() >= end:
                raise Refused('cleanup deadline expired')
            self.stopped_hold()
            self.identity(end - self.clock())
            if self.census(end - self.clock()):
                raise Refused('survivor or rearrival')
            self.stopped_hold()
            observed = self.clock()
            if observed >= end:
                raise Refused('cleanup deadline expired')
            if quiet is None:
                quiet = observed
            elif observed - quiet >= 5:
                break
            self.sleep(min(.5, max(0, end - self.clock())))
        self.stopped_hold()
        if self.clock() >= end:
            raise Refused('cleanup deadline expired')
        self.receipt['cleanup'] = 'OBSERVED_EMPTY_5S'

    def restored(self) -> None:
        if self.receipt['cleanup'] != 'OBSERVED_EMPTY_5S':
            raise Refused('keep admission held: cleanup incomplete')
        self.control()
        import hashlib
        original_bytes = Path(self.facts['original_state_receipt']).read_bytes()
        if hashlib.sha256(original_bytes).hexdigest() != self.facts['binding'][5]:
            raise Refused('original restoration evidence changed')
        original = json.loads(original_bytes)
        restored_path = Path(self.facts['restored_state_receipt'])
        private_control(restored_path)
        if original != json.loads(restored_path.read_text()):
            raise Refused('restoration differs from exact original state')
        self.control()
        if self.clock() >= self.deadline:
            raise Refused('observation deadline expired')
        self.receipt['restoration'] = 'READBACK_MATCH'


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('facts', type=Path)
    parser.add_argument('evidence', type=Path)
    args = parser.parse_args()
    private_control(args.facts)
    facts = json.loads(args.facts.read_text())
    window = Window(facts, args.evidence)
    window.ready()
    # The foreground operator keeps this terminal open through preparation/run.
    print('Window active. After independent export and source shutdown, type finalize.', flush=True)
    ready_path = Path(facts['ready'])
    try:
        while True:
            window.lease()
            if select.select([sys.stdin], [], [], 2)[0]:
                answer = sys.stdin.readline()
                if not answer:
                    raise Refused('operator terminal lost')
                if answer.strip() == 'finalize':
                    break
    finally:
        ready_path.unlink(missing_ok=True)
        with (args.evidence / 'window-observation.json').open('x') as stream:
            json.dump(window.receipt, stream)
    try:
        window.capture()
        window.cleanup()
        print('Restore only recorded changes; supply exact readback receipt, then type restored.', flush=True)
        if not select.select([sys.stdin], [], [], max(0, window.deadline - window.clock()))[0] or sys.stdin.readline().strip() != 'restored':
            raise Refused('restoration unconfirmed')
        window.restored()
    finally:
        with (args.evidence / 'window-result.json').open('x') as stream:
            json.dump(window.receipt, stream)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
