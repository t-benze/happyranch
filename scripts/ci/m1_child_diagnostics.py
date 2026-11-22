"""Disposable M1 observer: never export trace text or stderr payloads."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import selectors
import signal
import subprocess
import sys
import time

PYTHON = '/opt/happyranch-desktop/python/bin/python3'
CHILD = [PYTHON, '-I', '-m', 'runtime.remote_access.home_activation_backend']
HELPER = b'/usr/bin/python3\0-I\0/opt/happyranch-desktop/bin/happyranch-remote-access-helper\0'
MAX_TRACE = 4 * 1024 * 1024
MAX_CHILDREN = 32
MAX_STDERR = 4096
TRACE_ARGS = ['-f', '-q', '-s', '192', '-e', 'trace=execve,write,writev',
              '-e', 'raw=write,writev', '-o', '/dev/stdout']


class Projection:
    def __init__(self) -> None:
        self.children: dict[int, dict] = {}
        self.pending: set[int] = set()
        self.truncated = False

    def line(self, raw: bytes) -> None:
        # Only exact fixed argv identifies the activation child. No raw trace
        # string, exception, command arguments or pointer escapes this method.
        text = raw.decode('ascii', errors='replace')
        match = re.fullmatch(r'\s*(\d+)\s+(.*)', text)
        if match is None:
            return
        pid, event = int(match[1]), match[2]
        fixed = 'execve(' + json.dumps(PYTHON) + ', [' + ', '.join(map(json.dumps, CHILD)) + '], '
        outcome = r'(?:0|-1 E[A-Z0-9]+ \([^)]*\))'
        complete = event.startswith(fixed) and re.fullmatch(
            r'0x[0-9a-f]+ /\* 0 vars \*/\) += ' + outcome, event[len(fixed):])
        if event.startswith(fixed) and re.fullmatch(r'0x[0-9a-f]+ /\* 0 vars \*/\)? <unfinished \.\.\.>', event[len(fixed):]):
            if len(self.pending) < MAX_CHILDREN:
                self.pending.add(pid)
            else:
                self.truncated = True
        if pid in self.pending and event.startswith('<... execve resumed>'):
            self.pending.remove(pid)
            complete = re.fullmatch(r'<\.\.\. execve resumed>\)?\s+= ' + outcome, event)
        if complete:
            if len(self.children) >= MAX_CHILDREN:
                self.truncated = True
            else:
                self.children[pid] = dict(child_pid=pid, exit_status=None, signal=None,
                    stderr_bytes=0, stderr_truncated=False, stderr_incomplete=False,
                    stderr='payload-omitted')
        if pid not in self.children:
            return
        record = self.children[pid]
        write = re.fullmatch(r'write(?:v)?\(0x2, 0x[0-9a-f]+, 0x[0-9a-f]+\)\s+= (0x[0-9a-f]+|[0-9]+)', event)
        if write:
            count = record['stderr_bytes'] + int(write[1], 16 if write[1].startswith('0x') else 10)
            record['stderr_bytes'] = min(count, MAX_STDERR)
            record['stderr_truncated'] |= count > MAX_STDERR
        elif re.match(r'write(?:v)?\(0x2,', event):
            record['stderr_incomplete'] = True
        exited = re.fullmatch(r'\+\+\+ exited with ([0-9]+) \+\+\+', event)
        killed = re.fullmatch(r'\+\+\+ killed by (SIG[A-Z0-9]+)(?: \(core dumped\))? \+\+\+', event)
        if exited:
            record['exit_status'] = int(exited[1])
        if killed and killed[1] in signal.Signals.__members__:
            record['signal'] = killed[1]

    def result(self, status: str) -> dict:
        return dict(child_diagnostics=status, truncated=self.truncated,
                    children=list(self.children.values()))


def observe(pid: int, start: str) -> None:
    assert Path(f'/proc/{pid}/cmdline').read_bytes() == HELPER
    assert Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[19] == start
    projection = Projection()
    with subprocess.Popen(['/usr/bin/strace', *TRACE_ARGS, '-p', str(pid)],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL) as tracer:
        try:
            end = time.monotonic() + 2
            while time.monotonic() < end:
                status = Path(f'/proc/{pid}/status').read_text()
                if re.search(r'^TracerPid:\s+' + str(tracer.pid) + r'$', status, re.MULTILINE):
                    assert Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[19] == start
                    break
                if tracer.poll() is not None:
                    raise RuntimeError()
                time.sleep(.01)
            else:
                raise RuntimeError()
            print('ARMED', flush=True)
            pending = b''
            total = 0
            status = 'complete'
            detach_at = None
            with selectors.DefaultSelector() as selector:
                selector.register(tracer.stdout, selectors.EVENT_READ)
                selector.register(sys.stdin.buffer, selectors.EVENT_READ)
                running = True
                while running:
                    if detach_at is not None and time.monotonic() >= detach_at:
                        break
                    wait = 1 if detach_at is None else max(0, detach_at - time.monotonic())
                    for key, _ in selector.select(wait):
                        if key.fileobj is sys.stdin.buffer:
                            # EOF from the owning caller ends only the observer.
                            selector.unregister(sys.stdin.buffer)
                            detach_at = time.monotonic() + .25
                            status = 'detached'
                            continue
                        chunk = os.read(tracer.stdout.fileno(), 65536)
                        total += len(chunk)
                        if total > MAX_TRACE:
                            projection.truncated = True
                            status = 'trace-limit'
                            running = False
                            break
                        if not chunk:
                            running = False
                            break
                        pending += chunk
                        while b'\n' in pending:
                            line, pending = pending.split(b'\n', 1)
                            if len(line) <= 4096:
                                projection.line(line)
                            else:
                                projection.truncated = True
                        if len(pending) > 4096:
                            projection.truncated = True
                            status = 'line-limit'
                            running = False
                            break
            result = projection.result(status)
            result.update(helper_pid=pid, helper_start=start)
            print(json.dumps(result, sort_keys=True), flush=True)
        finally:
            if tracer.poll() is None:
                tracer.terminate()  # strace detaches; never signal the tracee.
            tracer.wait(timeout=2)


def main() -> None:
    assert os.geteuid() == 0
    assert os.environ.get('GITHUB_ACTIONS') == 'true'
    assert os.environ.get('RUNNER_ENVIRONMENT') == 'github-hosted'
    assert len(sys.argv) == 3 and all(value.isascii() and value.isdigit() for value in sys.argv[1:])
    def stop(signum: int, frame: object) -> None:
        raise InterruptedError()
    signal.signal(signal.SIGTERM, stop)
    observe(int(sys.argv[1]), sys.argv[2])


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print(json.dumps(dict(child_diagnostics='unavailable')), flush=True)
        raise SystemExit(1) from None
