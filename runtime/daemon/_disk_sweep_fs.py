"""Private Linux filesystem observations for THR307; no legacy cleanup policy.

Paths are walked from / with O_NOFOLLOW, then all tree operations are dir-fd
relative. No chmod follows symlinks. Mountpoints (including same-device bind
mounts) refuse. POSIX pathname syscalls cannot fence a hostile same-UID rename
between a final identity check and unlink; neither this nor /proc is a writer
lock. The daemon supervisor bounds the owning process group, not a thread.
"""
from __future__ import annotations

import os
import re
import stat
import time
from collections import Counter
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path


class Retain(RuntimeError):
    """A failed observation is retention, never negative evidence."""


class Expired(RuntimeError):
    pass


def check(deadline: float) -> None:
    if time.monotonic() >= deadline:
        raise Expired("total sweep deadline")


def identity(s: os.stat_result) -> tuple[int, int, int]:
    return s.st_dev, s.st_ino, stat.S_IFMT(s.st_mode)


def signature(s: os.stat_result) -> tuple:
    return identity(s) + (s.st_mode, s.st_size, s.st_mtime_ns, s.st_ctime_ns, s.st_nlink)


def names(fd: int, deadline: float) -> list[str]:
    result = []
    with os.scandir(fd) as entries:
        while True:
            check(deadline)
            try:
                result.append(next(entries).name)
            except StopIteration:
                break
    return sorted(result)


@contextmanager
def directory(path: Path, deadline: float):
    """Open every absolute component without following symlinks."""
    path = Path(os.path.abspath(path))
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            check(deadline)
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        yield fd
    finally:
        os.close(fd)


def mounts(deadline: float) -> set[str]:
    check(deadline)
    result = set()
    with open('/proc/self/mountinfo', encoding='utf-8', errors='strict') as stream:
        for line in stream:
            check(deadline)
            fields = line.split()
            if len(fields) < 7:
                raise Retain('unknown_mount_evidence')
            result.add(re.sub(r'\\([0-7]{3})', lambda m: chr(int(m[1], 8)), fields[4]))
    return result


def snapshot(path: Path, deadline: float) -> dict[str, os.stat_result]:
    """Complete no-follow tree stat, including root; ctime AND mtime age.

    ctime detects recent writes/chmods even when mtime was backdated; atime is
    deliberately excluded because this observation itself may update atime.
    Any unreadable descendant or special object refuses the whole candidate.
    """
    mountpoints = mounts(deadline)
    result = {}
    with directory(path, deadline) as root:
        device = os.fstat(root).st_dev

        def walk(fd, relative):
            check(deadline)
            s = os.fstat(fd)
            absolute = str(path / relative) if relative else str(path)
            if s.st_dev != device or absolute in mountpoints:
                raise Retain('filesystem_boundary')
            result[relative] = s
            for name in names(fd, deadline):
                check(deadline)
                rel = f'{relative}/{name}' if relative else name
                s = os.stat(name, dir_fd=fd, follow_symlinks=False)
                if s.st_dev != device or str(path / rel) in mountpoints:
                    raise Retain('filesystem_boundary')
                if stat.S_ISDIR(s.st_mode):
                    child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                    try:
                        if identity(os.fstat(child)) != identity(s):
                            raise Retain('identity_changed')
                        walk(child, rel)
                    finally:
                        os.close(child)
                elif stat.S_ISREG(s.st_mode) or stat.S_ISLNK(s.st_mode):
                    result[rel] = s
                else:
                    raise Retain('unknown_special_entry')
        walk(root, '')
    return result


def allocated(tree: dict[str, os.stat_result], seen: set[tuple]) -> tuple[int, int]:
    """Observed allocation and exclusive-link attribution ceiling, inode deduped."""
    occurrences = Counter(identity(s) for s in tree.values())
    total = exclusive = 0
    for s in tree.values():
        key = identity(s)
        if key in seen:
            continue
        seen.add(key)
        amount = s.st_blocks * 512
        total += amount
        if stat.S_ISDIR(s.st_mode) or s.st_nlink == occurrences[key]:
            exclusive += amount
    return total, exclusive


def mutate(path: Path, tree: dict, deadline: float, *, remove: bool) -> dict:
    """One admitted operation: contained permission repair and optional removal.

    The caller refreshes ALL common gates immediately before entry. Each
    syscall rechecks the recorded inode and device. No external-link chmod,
    recursion into links, generic rm or unbounded chmod subprocess is used.
    """
    repairs = {}
    own_inode_updates = {}
    with directory(path.parent, deadline) as parent:
        check(deadline)
        original = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
        if signature(original) != signature(tree['']):
            raise Retain('identity_changed_before_mutation')
        mountpoints = mounts(deadline)

        def visit(parent_fd, name, relative):
            check(deadline)
            expected = tree[relative]
            current = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            if signature(current) != own_inode_updates.get(identity(expected), signature(expected)):
                raise Retain('tree_changed_before_mutation')
            if str(path / relative) in mountpoints or current.st_dev != original.st_dev:
                raise Retain('filesystem_boundary')
            if stat.S_ISDIR(current.st_mode):
                fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
                try:
                    if signature(os.fstat(fd)) != signature(expected):
                        raise Retain('identity_changed')
                    check(deadline)
                    # Repair directories only, through the verified open fd.
                    # Files need no mode changes for unlink.
                    if not current.st_mode & stat.S_IWUSR:
                        os.fchmod(fd, stat.S_IMODE(current.st_mode) | stat.S_IWUSR)
                        repairs[relative] = (signature(os.fstat(fd)), current)
                    for child in names(fd, deadline):
                        rel = f'{relative}/{child}' if relative else child
                        if rel not in tree:
                            raise Retain('new_entry_during_mutation')
                        visit(fd, child, rel)
                    if remove:
                        check(deadline)
                        if identity(os.stat(name, dir_fd=parent_fd, follow_symlinks=False)) != identity(expected):
                            raise Retain('identity_changed')
                        os.rmdir(name, dir_fd=parent_fd)
                finally:
                    os.close(fd)
            elif remove:
                # O_PATH observes the link inode itself, including symlinks;
                # track nlink/ctime changes caused by our own prior unlink so
                # internal hardlinks need neither double counts nor a bypass.
                leaf = os.open(name, os.O_PATH | os.O_NOFOLLOW, dir_fd=parent_fd)
                try:
                    if signature(os.fstat(leaf)) != signature(current):
                        raise Retain('identity_changed')
                    check(deadline)
                    os.unlink(name, dir_fd=parent_fd)
                    own_inode_updates[identity(expected)] = signature(os.fstat(leaf))
                finally:
                    os.close(leaf)
        visit(parent, path.name, '')
    return repairs


UNREADABLE_PROCESS_NAMES = frozenset({'systemd', '(sd-pam)', 'sshd-session', 'ssh-agent'})


def process_use(path: Path, deadline: float, *, on_exemption: Callable[[dict], None] = lambda observation: None) -> None:
    """Visible same-effective-UID scan with four exact unreadability exceptions.

    Includes every thread's cwd/root/exe/fd and TMPDIR/TMP/TEMP. Other UIDs
    are outside the stated scope, NOT claimed unused. Missing procfs, denied
    credentials/identity, unstable census or PID reuse is unknown. Only denied
    references for exact approved comm names can be exempted and reported;
    readable candidate use always refuses. Names do not authenticate executables.
    Confirmed disappearance differs from denial. Stable zombies have no live
    references. A later opener remains a snapshot limitation.
    """
    if not Path('/proc/self/task').is_dir():
        raise Retain('unknown_process_platform')
    prefix = str(path) + '/'

    def used(value):
        value = value.removesuffix(' (deleted)')
        if value == str(path) or value.startswith(prefix):
            raise Retain('process_use')

    def token(base):
        raw = (base / 'stat').read_text()
        start, end = raw.find(' ('), raw.rfind(') ')
        fields = raw[end + 2:].split()
        if (start < 1 or end <= start + 2 or raw[:start] != base.name
                or len(fields) < 20 or not fields[19].isdecimal()):
            raise Retain('unknown_process_identity')
        return fields[19], fields[0], raw[start + 2:end]  # starttime, state, exact comm

    check(deadline)
    leaders = sorted(p for p in os.listdir('/proc') if p.isdecimal())
    for pid in leaders:
        check(deadline)
        leader = Path('/proc') / pid
        try:
            status_lines = (leader / 'status').read_text().splitlines()
            uid_rows = [line.split()[1:] for line in status_lines if line.startswith('Uid:')]
            if len(uid_rows) != 1 or len(uid_rows[0]) != 4 or not all(v.isdecimal() for v in uid_rows[0]):
                raise Retain('unknown_process_credentials')
            if int(uid_rows[0][1]) != os.geteuid():
                continue
            before = token(leader)
            taskdir = leader / 'task'
            tids = sorted(os.listdir(taskdir))
            for tid in tids:
                check(deadline)
                base = taskdir / tid
                try:
                    ident = token(base)
                    if base.stat().st_uid != os.geteuid():
                        raise Retain('unknown_process_credentials_changed')
                    denied = False

                    def reference(read):
                        nonlocal denied
                        try:
                            return read()
                        except PermissionError:
                            if ident[2] not in UNREADABLE_PROCESS_NAMES:
                                raise
                            observed = token(base)
                            if (ident[0] != observed[0] or ident[2] != observed[2]
                                    or (ident[1] == 'Z') != (observed[1] == 'Z')):
                                raise Retain('unknown_process_identity_changed')
                            if base.stat().st_uid != os.geteuid():
                                raise Retain('unknown_process_credentials_changed')
                            if not denied:
                                on_exemption(dict(pid=int(pid), tid=int(tid), starttime=ident[0],
                                                  name=ident[2], uid=os.geteuid(), reason='unreadable'))
                            denied = True
                            return None

                    if ident[1] != 'Z':
                        # Zombies may lack ns/mnt as well as live-use references.
                        # The identity/Z-state bracket below still covers every
                        # thread, independently of its leader's state.
                        namespace = reference(lambda: os.readlink(base / 'ns/mnt'))
                        if namespace is not None and namespace != os.readlink('/proc/self/ns/mnt'):
                            raise Retain('unknown_process_mount_namespace')
                        for ref in ('cwd', 'root', 'exe'):
                            check(deadline)
                            value = reference(lambda: os.readlink(base / ref))
                            if value is not None:
                                used(value)
                        fd_dir = base / 'fd'
                        for fd in sorted(reference(lambda: os.listdir(fd_dir)) or []):
                            check(deadline)
                            try:
                                value = reference(lambda: os.readlink(fd_dir / fd))
                                if value is not None:
                                    used(value)
                            except FileNotFoundError:
                                # An individual closed fd is distinguishable
                                # from denial; the task identity is bracketed.
                                continue
                        check(deadline)
                        environment = reference(lambda: (base / 'environ').read_bytes())
                        for item in (environment or b'').split(b'\0'):
                            key, _, value = item.partition(b'=')
                            if key in (b'TMPDIR', b'TMP', b'TEMP'):
                                text = os.fsdecode(value)
                                if not text.startswith('/'):
                                    raise Retain('unknown_relative_process_temp')
                                used(os.path.normpath(text))
                                # Resolve aliases through the inspected task's
                                # root, never through an assumed host root.
                                try:
                                    resolved = reference(lambda: (base / 'root' / text.lstrip('/')).resolve(strict=True))
                                except OSError as exc:
                                    raise Retain('unknown_process_temp_resolution') from exc
                                if resolved is not None:
                                    used(str(resolved))
                    after = token(base)
                    if ident[0] != after[0] or ident[2] != after[2] or (ident[1] == 'Z') != (after[1] == 'Z'):
                        raise Retain('unknown_process_identity_changed')
                    if denied:
                        if base.stat().st_uid != os.geteuid():
                            raise Retain('unknown_process_credentials_changed')
                except FileNotFoundError:
                    if base.exists():
                        raise Retain('unknown_missing_process_reference')
                    # Observed absent task, not a permission-denied task.
            if ([line.split()[1:] for line in (leader / 'status').read_text().splitlines() if line.startswith('Uid:')] != uid_rows
                    or token(leader)[0] != before[0] or sorted(os.listdir(taskdir)) != tids):
                raise Retain('unknown_process_census_changed')
        except FileNotFoundError:
            if leader.exists():
                raise Retain('unknown_missing_process_reference')
        except PermissionError as exc:
            raise Retain('unknown_process_denied') from exc
    check(deadline)
    # New process arrivals are incomplete coverage. Departures were bracketed.
    if set(p for p in os.listdir('/proc') if p.isdecimal()) - set(leaders):
        raise Retain('unknown_process_census_changed')
