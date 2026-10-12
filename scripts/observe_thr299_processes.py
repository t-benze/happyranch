"""Read-only, bounded Linux process evidence for the disposable THR299 lane.

Only identity and classification leave this reader; environments and command
lines never do. It neither signals processes nor modifies scratch or permissions.
"""
import argparse
import json
import os
from pathlib import Path
import select
import time


LIMIT = 128
READ_LIMIT = 1048576
PROCESS_LIMIT = 4096


def read_complete(path):
    with path.open('rb') as stream:
        data = stream.read(READ_LIMIT + 1)
    if len(data) > READ_LIMIT:
        raise ValueError('read_truncated')
    return data


def identity(proc):
    info = proc.stat()
    raw = read_complete(proc / 'stat')
    # comm may contain spaces and parentheses; field22 follows the final ')'.
    tail = raw[raw.rindex(b')') + 2:].split()
    pid = int(raw.split(b' ', 1)[0])
    start = int(tail[19])
    rows = [line.split()[1:] for line in read_complete(proc / 'status').splitlines()
            if line.startswith(b'Uid:')]
    if pid != int(proc.name) or start <= 0 or len(rows) != 1 or len(rows[0]) != 4:
        raise ValueError('identity_incomplete')
    uids = [int(value) for value in rows[0]]
    return dict(pid=pid, start_ticks=start, uids=uids, device=info.st_dev, inode=info.st_ino)


def observe_process(proc, uid, scratch):
    before, pidfd, bound, stage = None, None, False, 'identity_before'
    try:
        before = identity(proc)
        stage = 'pidfd_open'
        pidfd = os.pidfd_open(before['pid'])
        stage = 'identity_bind'
        if identity(proc) != before:
            raise ValueError('identity_changed')
        bound = True
        stage = 'environment'
        relevant = uid in before['uids']
        matches = False
        if relevant:
            data = read_complete(proc / 'environ')
            if data and not data.endswith(b'\0'):
                raise ValueError('environment_incomplete')
            prefix = b'TMPDIR=' + os.fsencode(scratch)
            matches = any(value == prefix or value.startswith(prefix + b'/')
                          for value in data.split(b'\0'))
        stage = 'identity_after'
        if identity(proc) != before:
            raise ValueError('identity_changed')
        return dict(classification='bound' if matches else 'unrelated', identity=before,
                    basis='complete_environment' if relevant else 'different_credentials')
    except (OSError, ValueError, IndexError) as exc:
        # Only a pidfd bound to the observed identity establishes termination.
        # ENOENT/ESRCH or a missing numeric path alone never establishes it.
        terminal = False
        if bound and pidfd is not None and isinstance(exc, OSError):
            poller = select.poll()
            poller.register(pidfd, select.POLLIN)
            terminal = any(event & select.POLLIN for _, event in poller.poll(0))
            if terminal:
                try:
                    # A reused numeric path must remain unknown, even though
                    # the original pidfd is terminal.
                    proc.stat()
                    terminal = False
                except FileNotFoundError:
                    pass
                except OSError:
                    terminal = False
        return dict(classification='disappeared' if terminal else 'unknown',
                    pid=int(proc.name), identity=before, stage=stage,
                    error_class=type(exc).__name__, errno=getattr(exc, 'errno', None),
                    reason=str(exc) if isinstance(exc, ValueError) else None,
                    basis='bound_pidfd_terminal' if terminal else 'insufficient_identity_or_visibility')
    finally:
        if pidfd is not None:
            os.close(pidfd)


def observe(uid, scratch):
    receipt = dict(uid=uid, scratch=str(scratch), observer_euid=os.geteuid(),
                   bound_processes=[], observation_errors=[], disappeared=[],
                   same_uid_unrelated=[],
                   same_uid_observed=0, different_uid_observed=0,
                   observations_truncated=False, result='unknown')
    deadline = time.monotonic() + 10
    count = 0
    try:
        with os.scandir('/proc') as entries:
            for entry in entries:
                if not entry.name.isdecimal():
                    continue
                count += 1
                if count > PROCESS_LIMIT or time.monotonic() >= deadline:
                    receipt['observations_truncated'] = True
                    break
                row = observe_process(Path(entry.path), uid, scratch)
                kind = row['classification']
                if kind == 'unrelated':
                    key = 'same_uid_observed' if uid in row['identity']['uids'] else 'different_uid_observed'
                    receipt[key] += 1
                    if key == 'same_uid_observed':
                        if len(receipt['same_uid_unrelated']) == LIMIT:
                            receipt['observations_truncated'] = True
                            break
                        receipt['same_uid_unrelated'].append(row)
                else:
                    key = {'bound': 'bound_processes', 'unknown': 'observation_errors',
                           'disappeared': 'disappeared'}[kind]
                    if len(receipt[key]) == LIMIT:
                        receipt['observations_truncated'] = True
                        break
                    receipt[key].append(row)
    except OSError as exc:
        receipt['observation_errors'].append(dict(stage='proc_enumeration',
            error_class=type(exc).__name__, errno=exc.errno, identity=None))
    receipt['process_entries_observed'] = count
    if not receipt['observations_truncated'] and not receipt['observation_errors']:
        receipt['result'] = 'bound_process_observed' if receipt['bound_processes'] else 'no_bound_process_observed'
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--uid', type=int, required=True)
    parser.add_argument('--scratch', type=Path, required=True)
    args = parser.parse_args()
    # The workflow separately validates the exact runner.temp path and owner.
    if args.uid <= 0 or not args.scratch.is_absolute() or args.scratch.resolve() != args.scratch:
        parser.error('positive runner UID and canonical absolute scratch required')
    print(json.dumps(observe(args.uid, args.scratch), sort_keys=True))


if __name__ == '__main__':
    main()
