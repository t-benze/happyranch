"""Independent job budget, using the original job process group (POSIX)."""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import signal
import stat
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


GRACE_SECONDS = 5


def helper_argv() -> list[str]:
    if getattr(sys, 'frozen', False):
        return [sys.executable, '--job-guardian']
    return [sys.executable, str(Path(__file__).resolve())]


def process_identity(pid: int) -> dict:
    value = dict(pid=pid, pgid=os.getpgid(pid), sid=os.getsid(pid),
                 started_at=datetime.now(timezone.utc).isoformat(),
                 start_ticks=None, boot_id=None)
    if sys.platform == 'linux':
        value['start_ticks'] = int(Path(f'/proc/{pid}/stat').read_text().rpartition(')')[2].split()[19])
        value['boot_id'] = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    return value


def write_record(path: Path, value: dict) -> None:
    """Fresh per-execution sidecars; never replace old execution evidence."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(value, stream, sort_keys=True)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(parent)
    finally:
        os.close(parent)


def read_record(path: Path) -> dict:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > 4096:
            raise ValueError('job_record_unavailable')
        raw = stream.read(4097)
    if len(raw) > 4096:
        raise ValueError('job_record_unavailable')
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError('job_record_unavailable')
    return value


def timeout_observed(stderr_path: str) -> bool:
    try:
        return read_record(Path(stderr_path + '.timeout.json')).get('reason') == 'timeout'
    except (OSError, ValueError, RecursionError):
        return False


def launch_observed(stderr_path: str) -> bool:
    """Reserved helper channel: script exit codes never confirm launch."""
    try:
        value = read_record(Path(stderr_path + '.launch.json'))
        return value.get('format') == 'job-guardian-launch@1' and value.get('status') == 'started'
    except (OSError, ValueError, RecursionError):
        return False


def _others_alive(group: int) -> bool:
    """The guardian pins group identity; failed observation retains supervision."""
    if sys.platform == 'linux':
        deadline = time.monotonic() + .2
        for count, entry in enumerate(Path('/proc').iterdir()):
            if count > 65536 or time.monotonic() >= deadline:
                return True
            if not entry.name.isdecimal() or int(entry.name) == os.getpid():
                continue
            try:
                fields = (entry / 'stat').read_text().rpartition(')')[2].split()
                if int(fields[2]) == group and fields[0] != 'Z':
                    return True
            except (FileNotFoundError, ProcessLookupError):
                continue
            except (OSError, ValueError, IndexError):
                return True
        return False
    if sys.platform == 'darwin':
        libproc = ctypes.CDLL('/usr/lib/libproc.dylib', use_errno=True)
        call = libproc.proc_listpids
        call.argtypes = [ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_int]
        call.restype = ctypes.c_int
        pids = (ctypes.c_int * 65536)()
        size = call(1, 0, pids, ctypes.sizeof(pids))
        if size <= 0 or size >= ctypes.sizeof(pids) or size % ctypes.sizeof(ctypes.c_int):
            return True
        for pid in pids[:size // ctypes.sizeof(ctypes.c_int)]:
            if pid in (0, os.getpid()):
                continue
            try:
                if os.getpgid(pid) == group:
                    return True
            except ProcessLookupError:
                continue
        return False
    return True  # Unsupported observation never shortens the budget guardian.


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--seconds', type=int, required=True)
    parser.add_argument('--stderr-path', type=Path, required=True)
    parser.add_argument('--job-id', required=True)
    parser.add_argument('--fence-fd', type=int)
    parser.add_argument('--admission-fd', type=int)
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    if args.seconds <= 0 or not args.command or os.getpgrp() != os.getpid():
        parser.error('positive budget, command and original group leader required')
    deadline = time.monotonic() + args.seconds
    group = os.getpgrp()

    def requested(_sig: int, _frame: object) -> None:
        # The daemon already signals the group and owns Stop/shutdown grace.
        # Keep watching the independent budget, including after parent loss.
        # A handler (rather than SIG_IGN) leaves the child's exec default intact.
        pass

    signal.signal(signal.SIGTERM, requested)
    # Marker storage is reserved before launch. Failure cannot leave an
    # unsupervised script; timeout writing later cannot prevent group kill.
    marker_fd = None
    child = None
    launched = False
    try:
        marker_fd = os.open(str(args.stderr_path) + '.timeout.json',
                            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        identity = process_identity(os.getpid()) | {'job_id': args.job_id}
        write_record(Path(str(args.stderr_path) + '.process.json'), identity)
        inherited = () if args.fence_fd is None else (args.fence_fd,)
        child = subprocess.Popen(args.command, pass_fds=inherited)
        if args.fence_fd is not None:
            os.close(args.fence_fd)  # Child keeps the SAME open-file description.
        write_record(Path(str(args.stderr_path) + '.launch.json'),
                     dict(format='job-guardian-launch@1', status='started', job_id=args.job_id))
        if args.admission_fd is not None:
            # This helper-only pipe is not passed to the script. A readable
            # record alone cannot confirm publication: flush may precede EIO.
            try:
                if os.write(args.admission_fd, b'1') != 1:
                    raise OSError('job_guardian_admission_short_write')
            except BrokenPipeError:
                pass  # Parent loss must not cancel the independent budget.
            finally:
                os.close(args.admission_fd)
        launched = True
        end_grace = None
        while True:
            code = child.poll()
            if code is not None and not _others_alive(group):
                if code < 0:
                    signal.signal(-code, signal.SIG_DFL)
                    os.kill(os.getpid(), -code)
                return code
            now = time.monotonic()
            if end_grace is None and now >= deadline:
                marker = dict(reason='timeout', job_id=args.job_id, pid=group,
                              pgid=group, max_runtime_seconds=args.seconds,
                              exhausted_at=datetime.now(timezone.utc).isoformat())
                try:
                    raw = (json.dumps(marker, sort_keys=True) + '\n').encode()
                    if os.write(marker_fd, raw) != len(raw):
                        raise OSError('job_guardian_timeout_short_write')
                    os.fsync(marker_fd)
                except OSError:
                    # Fail safe even if the filesystem fails after admission.
                    try:
                        os.write(2, b'job_guardian: timeout; exhaustion marker unavailable\n')
                    except OSError:
                        pass
                end_grace = now + GRACE_SECONDS
                os.killpg(group, signal.SIGTERM)
            if end_grace is not None and now >= end_grace:
                os.killpg(group, signal.SIGKILL)  # Includes guardian; no reused PGID.
            time.sleep(.05)
    except BaseException as exc:
        if not launched:
            # This diagnostic precedes self/group kill if Popen succeeded but
            # confirmation failed. No pipe acknowledgement is sent even if
            # a readable started record survives a failed sync/publication.
            # This also covers ENOSPC before any sidecar can be written.
            try:
                os.write(2, ('job_guardian: spawn_failed: ' + str(exc)[:2048] + '\n').encode())
            except OSError:
                pass
        if child is not None:
            os.killpg(group, signal.SIGKILL)
        raise
    finally:
        if marker_fd is not None:
            os.close(marker_fd)


if __name__ == '__main__':
    raise SystemExit(main())
