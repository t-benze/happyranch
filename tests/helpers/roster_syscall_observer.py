"""Own-child Linux x86_64 C8/C9 syscall witness, not a production launcher.

Authoring only: review/capability/venue checks must precede execution. Unknown
descriptor transfers, shared mappings or incomplete tracking invalidate proof.
Pair with roster_python_observer; syscall evidence is not SQL commit proof.
"""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import platform
import signal
import stat
import subprocess
import sys


class Registers(ctypes.Structure):
    _fields_ = [(name, ctypes.c_ulonglong) for name in (
        'r15', 'r14', 'r13', 'r12', 'rbp', 'rbx', 'r11', 'r10', 'r9', 'r8',
        'rax', 'rcx', 'rdx', 'rsi', 'rdi', 'orig_rax', 'rip', 'cs', 'eflags',
        'rsp', 'ss', 'fs_base', 'gs_base', 'ds', 'es', 'fs', 'gs')]


LIBC = ctypes.CDLL(None, use_errno=True)
LIBC.ptrace.restype = ctypes.c_long
LIBC.ptrace.argtypes = [ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p]
TRACEME, PEEKDATA, SYSCALL, GETREGS, SETOPTIONS, GETEVENTMSG = 0, 2, 24, 12, 0x4200, 0x4201
GET_SYSCALL_INFO = 0x420e
OPTIONS = 1 | 2 | 4 | 8 | 16 | (1 << 20)  # SYSGOOD/FORK/VFORK/CLONE/EXEC/EXITKILL
WALL = 0x40000000


def ptrace(request: int, pid: int, address=0, data=0) -> int:
    ctypes.set_errno(0)
    value = LIBC.ptrace(request, pid, address, data)
    error = ctypes.get_errno()
    if value == -1 and error:
        raise OSError(error, 'owned-child ptrace observation failed')
    return value


def signed(value: int) -> int:
    return ctypes.c_longlong(value).value


def string(pid: int, address: int) -> str:
    if not address:
        raise ValueError('null_syscall_path')
    data = bytearray()
    for offset in range(0, 4096, ctypes.sizeof(ctypes.c_long)):
        word = ptrace(PEEKDATA, pid, address + offset)
        chunk = ctypes.c_ulong(word).value.to_bytes(8, sys.byteorder)
        end = chunk.find(b'\0')
        if end >= 0:
            return os.fsdecode(bytes(data) + chunk[:end])
        data.extend(chunk)
    raise ValueError('syscall_path_unresolved_or_overlong')


def descriptor(pid: int, fd: int) -> Path:
    raw = os.readlink(f'/proc/{pid}/fd/{signed(fd)}')
    if not raw.startswith('/') or raw.endswith(' (deleted)'):
        raise ValueError('unresolved_writer_descriptor')
    return Path(raw).resolve(strict=True)


def path_at(pid: int, address: int, dirfd: int = -100) -> Path:
    value = Path(string(pid, address))
    if not value.is_absolute():
        base = Path(f'/proc/{pid}/cwd').resolve(strict=True) if signed(dirfd) == -100 else descriptor(pid, dirfd)
        value = base / value
    # rename/unlink/link operate on the final directory entry. Resolving that
    # entry would hide CLAUDE.md -> AGENTS.md replacements. Follow only the
    # containing path; descriptor observations separately identify opened files.
    if value.name in ('.', '..') or str(value) == '/':
        return value.resolve(strict=True)
    return value.parent.resolve(strict=True) / value.name


def private_mapping(pid: int, start: int, length: int) -> bool:
    """Require complete current private coverage; unknown/shared refuses."""
    end = start + length
    cursor = start
    for line in Path(f'/proc/{pid}/maps').read_text().splitlines():
        columns = line.split(maxsplit=5)
        left, right = (int(value, 16) for value in columns[0].split('-'))
        if right <= cursor or left >= end:
            continue
        if left > cursor or len(columns[1]) != 4 or columns[1][3] != 'p':
            return False
        cursor = min(end, right)
        if cursor == end:
            return True
    return False


def decode(pid: int, regs: Registers) -> dict | None:
    """Decode actual x86_64 arguments; unsupported writers refuse proof."""
    number = regs.orig_rax
    a, b, c, d, e, f = regs.rdi, regs.rsi, regs.rdx, regs.r10, regs.r8, regs.r9
    # Socket/pipe descriptors cannot reference protected regular files, but a
    # transferred descriptor or kernel-assisted file write needs full decoding.
    if number in (40, 46, 47, 133, 188, 189, 190, 191, 192, 193, 197, 198, 199,
                  259, 275, 276, 278, 285, 299, 307, 327, 437):
        raise ValueError(f'unsupported_descriptor_or_writer_syscall:{number}')
    if number == 16:  # ioctl may mutate regular-file metadata/content
        raw = os.readlink(f'/proc/{pid}/fd/{signed(a)}')
        if raw.startswith('/') and stat.S_ISREG(os.stat(f'/proc/{pid}/fd/{signed(a)}').st_mode):
            raise ValueError('regular_file_ioctl_requires_independent_decoder')
    if number in (1, 18, 20, 74, 75, 77, 91, 93, 296, 328):
        name = {1: 'write', 18: 'pwrite', 20: 'writev', 74: 'fsync', 75: 'fdatasync',
                77: 'ftruncate', 91: 'fchmod', 93: 'fchown', 296: 'pwritev', 328: 'pwritev2'}[number]
        raw = os.readlink(f'/proc/{pid}/fd/{signed(a)}')
        if raw.startswith(('pipe:[', 'socket:[')):
            return None  # no descriptor transfer is admitted above
        path = descriptor(pid, a)
        info = os.stat(f'/proc/{pid}/fd/{signed(a)}')
        return dict(syscall=name, paths=[str(path)], identity=[info.st_dev, info.st_ino], descriptor=signed(a))
    if number in (2, 257):
        path = path_at(pid, a) if number == 2 else path_at(pid, b, a)
        flags = b if number == 2 else c
        if flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND):
            return dict(syscall='open', paths=[str(path)], flags=flags)
        return None
    if number in (30, 76, 83, 84, 87, 90, 92, 94, 235):
        name = {76: 'truncate', 83: 'mkdir', 84: 'rmdir', 87: 'unlink',
                90: 'chmod', 92: 'chown', 94: 'lchown', 30: 'utime', 235: 'utimes'}[number]
        return dict(syscall=name, paths=[str(path_at(pid, a))])
    if number in (82, 86):
        return dict(syscall='rename' if number == 82 else 'link',
                    paths=[str(path_at(pid, a)), str(path_at(pid, b))])
    if number == 88:
        return dict(syscall='symlink', paths=[str(path_at(pid, b))])
    if number in (258, 260, 263, 268, 280):
        name = {258: 'mkdir', 260: 'chown', 263: 'unlink', 268: 'chmod', 280: 'utimensat'}[number]
        return dict(syscall=name, paths=[str(path_at(pid, b, a))])
    if number in (264, 265, 316):
        return dict(syscall='link' if number == 265 else 'rename',
                    paths=[str(path_at(pid, b, a)), str(path_at(pid, d, c))])
    if number == 266:
        return dict(syscall='symlink', paths=[str(path_at(pid, c, b))])
    if number == 9:  # mmap: do not infer write absence from final file bytes
        if d & 1 and not d & 32:
            raise ValueError('shared_file_mapping_requires_independent_localization')
    if number in (25, 26):  # mremap/msync can otherwise hide a mapped writer
        raise ValueError('unknown_shared_mapping_operation')
    if number == 10:
        # A complete private/anonymous mapping provenance decoder is still a
        # capability prerequisite; no blind no-write claim for mprotect.
        if c & 2 and not private_mapping(pid, a, b):
            raise ValueError('writable_mprotect_requires_mapping_provenance')
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--source-sha', required=True)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--expected-digest', required=True)
    parser.add_argument('--boundary', required=True)
    parser.add_argument('--receipt', type=Path, required=True)
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if (platform.system() != 'Linux' or platform.machine() != 'x86_64'
            or ctypes.sizeof(ctypes.c_long) != 8 or sys.version_info[:2] != (3, 14)):
        raise ValueError('compiled_x86_64_linux_decoder_required')
    source = args.source.resolve(strict=True)
    if not args.source.is_absolute() or args.source.is_symlink():
        raise ValueError('absolute_pinned_source_required')
    if subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=source, text=True).strip() != args.source_sha:
        raise ValueError('observer_source_sha_mismatch')
    if subprocess.check_output(['git', 'status', '--porcelain'], cwd=source).strip():
        raise ValueError('clean_committed_observer_source_required')
    raw = args.manifest.read_bytes()
    if hashlib.sha256(raw).hexdigest() != args.expected_digest:
        raise ValueError('exact_observed_manifest_digest_required')
    manifest = json.loads(raw)
    if manifest['kind'] != 'THR296-checked-manifest-v1' or manifest['source_sha'] != args.source_sha:
        raise ValueError('real_checked_source_bound_manifest_required')
    org = Path(manifest['runtime_root']) / 'orgs' / manifest['org']
    roots = [org.resolve(strict=True), Path(manifest['canonical_store_root']).resolve(strict=True)]
    registered_paths = {Path(manifest['runtime_root']) / 'happyranch.yaml',
                        *(Path(value) for value in manifest['registry_images'])}
    command = args.command[1:] if args.command[:1] == ['--'] else args.command
    if (len(command) < 2 or Path(command[0]).resolve(strict=True) != Path(sys.executable).resolve(strict=True)
            or Path(command[1]).resolve(strict=True) != source / 'tests/helpers/roster_python_observer.py'):
        raise ValueError('pinned_python_and_paired_frame_observer_required')
    if '--script' not in command or command[command.index('--script') + 1] != str(source / 'scripts/migrate_human_team_roster.py'):
        raise ValueError('only_bounded_roster_utility_may_be_observed')
    for flag, expected in (('--source', str(source)), ('--source-sha', args.source_sha)):
        if command.count(flag) != 1 or command[command.index(flag) + 1] != expected:
            raise ValueError('paired_frame_source_binding_mismatch')
    directory = args.receipt.parent.resolve(strict=True)
    if (not args.receipt.is_absolute() or args.receipt.is_symlink()
            or directory.stat().st_uid != os.getuid() or stat.S_IMODE(directory.stat().st_mode) != 0o700
            or any(directory.is_relative_to(root) for root in roots) or directory.is_relative_to(source)):
        raise ValueError('external_private_observer_receipt_directory_required')
    fd = os.open(args.receipt, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    sequence = 0

    def emit(row: dict) -> None:
        nonlocal sequence
        sequence += 1
        data = (json.dumps({'sequence': sequence, **row}, sort_keys=True) + '\n').encode()
        if os.write(fd, data) != len(data):
            raise OSError('observer_receipt_short_write')
        os.fsync(fd)

    protected = {org / rel: ('head_replace' if rel.endswith('consultant_head.md') else
                            'codex_replace' if rel.endswith('consultant_codex.md') else 'teams_replace')
                 for rel in ('org/agents/consultant_head.md', 'org/agents/consultant_codex.md', 'org/teams.yaml')}
    for index, rel in enumerate(sorted(set(manifest['after']) - {'org/agents/consultant_head.md', 'org/agents/consultant_codex.md', 'org/teams.yaml'})):
        protected[org / rel] = f'g{index}'
    last_rename = {}

    def boundary(pid: int, row: dict) -> str | None:
        paths = [Path(value) for value in row['paths']]
        if row['syscall'] == 'rename' and paths[-1] in protected:
            return protected[paths[-1]] + '.rename'
        for path, family in protected.items():
            if row['syscall'] in ('write', 'pwrite', 'writev', 'pwritev', 'pwritev2') and any(
                    target.parent == path.parent and
                    (target == path or target.name.startswith('.' + path.name + '.roster-')
                     or target.name.startswith(path.name + '.happyranch-')) for target in paths):
                return family + '.stage_write'
            if row['syscall'] in ('fsync', 'fdatasync'):
                if any(target == path or target.parent == path.parent and target.name.startswith('.' + path.name + '.roster-') for target in paths):
                    return family + '.file_fsync'
        if row['syscall'] in ('fsync', 'fdatasync') and pid in last_rename:
            target = last_rename[pid]
            if paths == [target.parent]:
                return protected[target] + '.dir_fsync'
        return None

    allowed = {'observe-only'} | {family + '.' + effect + '.' + side
        for family in protected.values() for effect in ('stage_write', 'file_fsync', 'rename', 'dir_fsync')
        for side in ('before', 'after')}
    if args.boundary not in allowed:
        os.close(fd)
        raise ValueError('boundary_not_in_finite_manifest_path_selection')
    emit(dict(kind='begin', source_sha=args.source_sha, manifest_sha256=args.expected_digest,
              boundary=args.boundary, effective_python=sys.executable, decoder='linux-x86_64',
              protected_path_map={str(path): family for path, family in protected.items()}))
    child = os.fork()
    if child == 0:
        try:
            os.close(fd)
            ptrace(TRACEME, 0)
            os.kill(os.getpid(), signal.SIGSTOP)
            os.execv(command[0], command)
        except BaseException:
            os._exit(86)
    owned = {child}
    pending = {}
    loss = []
    fault = False
    exits = {}

    def kill_owned() -> None:
        for pid in tuple(owned):
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    try:
        pid, status = os.waitpid(child, WALL)
        if pid != child or not os.WIFSTOPPED(status):
            raise ValueError('owned_child_initial_trace_stop_missing')
        ptrace(SETOPTIONS, child, 0, OPTIONS)
        ptrace(SYSCALL, child)
        while owned:
            pid, status = os.waitpid(-1, WALL)
            if pid not in owned:
                raise ValueError('untracked_descendant_or_thread')
            if os.WIFEXITED(status) or os.WIFSIGNALED(status):
                exits[pid] = {'exit': os.WEXITSTATUS(status)} if os.WIFEXITED(status) else {'signal': os.WTERMSIG(status)}
                emit(dict(kind='process-terminal', pid=pid, **exits[pid]))
                owned.remove(pid)
                continue
            if not os.WIFSTOPPED(status):
                raise ValueError('unknown_child_wait_state')
            stop = os.WSTOPSIG(status)
            event = status >> 16
            if event in (1, 2, 3):
                new_pid = ctypes.c_ulonglong()
                ptrace(GETEVENTMSG, pid, 0, ctypes.byref(new_pid))
                if not new_pid.value or new_pid.value in owned:
                    raise ValueError('ambiguous_descendant_tracking')
                owned.add(new_pid.value)
                emit(dict(kind='descendant', parent=pid, pid=new_pid.value, event=event))
            elif event == 4:
                old_tid = ctypes.c_ulonglong()
                ptrace(GETEVENTMSG, pid, 0, ctypes.byref(old_tid))
                if old_tid.value != pid:
                    if old_tid.value not in owned:
                        raise ValueError('untracked_exec_identity_change')
                    owned.remove(old_tid.value)
                emit(dict(kind='exec', pid=pid, executable=os.readlink(f'/proc/{pid}/exe')))
            elif event:
                raise ValueError('unsupported_ptrace_event')
            elif stop == (signal.SIGTRAP | 0x80):
                info = ctypes.create_string_buffer(128)
                size = ptrace(GET_SYSCALL_INFO, pid, ctypes.sizeof(info), ctypes.byref(info))
                operation = info.raw[0]
                if size < 24 or int.from_bytes(info.raw[4:8], 'little') != 0xc000003e or operation not in (1, 2):
                    raise ValueError('complete_x86_64_syscall_phase_information_required')
                regs = Registers()
                ptrace(GETREGS, pid, 0, ctypes.byref(regs))
                if operation == 1:
                    row = decode(pid, regs)
                    pending[pid] = row
                    if row is not None:
                        selected = boundary(pid, row)
                        relevant = (any(Path(path).is_relative_to(root) for path in row['paths'] for root in roots)
                                    or any(Path(path) in registered_paths for path in row['paths']))
                        emit(dict(kind='syscall-entry', pid=pid, protected=relevant, boundary=selected, **row))
                        if selected is not None and args.boundary == selected + '.before':
                            fault = True
                            emit(dict(kind='selected-fault', pid=pid, boundary=args.boundary))
                            kill_owned()
                            continue
                else:
                    row = pending.pop(pid, None)
                    if row is not None:
                        result = signed(regs.rax)
                        if result >= 0 and row['syscall'] == 'open':
                            opened = os.stat(f'/proc/{pid}/fd/{result}')
                            row = {**row, 'opened_path': str(descriptor(pid, result)),
                                   'identity': [opened.st_dev, opened.st_ino]}
                        selected = boundary(pid, row)
                        emit(dict(kind='syscall-exit', pid=pid, result=result, successful=result >= 0, boundary=selected, **row))
                        if result >= 0 and row['syscall'] == 'rename' and Path(row['paths'][-1]) in protected:
                            last_rename[pid] = Path(row['paths'][-1])
                        if result >= 0 and selected is not None and args.boundary == selected + '.after':
                            fault = True
                            emit(dict(kind='selected-fault', pid=pid, boundary=args.boundary, result=result))
                            kill_owned()
                            continue
            # New descendants stop with SIGSTOP; other genuine signals retain
            # their original delivery. Synthetic trace events are not delivered.
            delivered = 0 if event or stop in (signal.SIGSTOP, signal.SIGTRAP, signal.SIGTRAP | 0x80) else stop
            ptrace(SYSCALL, pid, 0, delivered)
    except BaseException as exc:
        loss.append(type(exc).__name__ + ':' + str(exc))
        kill_owned()
        while owned:
            try:
                pid, _ = os.waitpid(-1, WALL)
            except ChildProcessError:
                loss.append('owned_child_reap_unavailable')
                break
            owned.discard(pid)
    finally:
        if args.boundary != 'observe-only' and not fault:
            loss.append('selected_boundary_not_reached')
        emit(dict(kind='terminal', complete=not loss, loss=loss, selected_fault=fault,
                  target=exits.get(child), processes=exits,
                  sql_commit_proof=False, frame_proof='paired receipt required'))
        os.close(fd)
    if loss:
        return 86
    if fault:
        return 0  # observer completed its selected fault; utility did not pass
    return exits.get(child, {}).get('exit', 86)


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        print(f'syscall observation unavailable: {exc}', file=sys.stderr)
        raise SystemExit(86)
