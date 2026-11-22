"""Build-only ELF stack repair and bundled-Python MDWE verification."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys


def elf_kind(path: Path) -> int:
    # Classify before accepting any GNU_STACK: unknown types must fail closed
    # even if readelf happens to display an otherwise safe program header.
    ehdr = struct.Struct('<16sHHIQQQIHHHHHH')
    shdr = struct.Struct('<IIQQQQIIQQ')
    data = path.read_bytes()
    if len(data) < ehdr.size:
        raise RuntimeError('unsupported ELF layout: ' + str(path))
    header = ehdr.unpack_from(data)
    ident, kind, machine, version = header[:4]
    if (ident[:7] != b'\x7fELF\x02\x01\x01' or kind not in (1, 2, 3)
            or machine not in (62, 183) or version != 1 or header[8] != ehdr.size):
        raise RuntimeError('unsupported ELF layout: ' + str(path))
    if kind == 1:  # ET_REL: linker input, never a loadable runtime image.
        offset, size, count, names = header[6], *header[11:14]
        if (header[4:6] != (0, 0) or header[9:11] != (0, 0)
                or size != shdr.size or not 0 < count < 0xff00 or names >= count
                or offset < ehdr.size or offset + count * size > len(data)):
            raise RuntimeError('unsupported ELF layout: ' + str(path))
        sections = [shdr.unpack_from(data, offset + i * size) for i in range(count)]
        if any(sections[0]) or any(
                section[1] != 8 and section[4] + section[5] > len(data)  # SHT_NOBITS has no bytes
                for section in sections):
            raise RuntimeError('unsupported ELF layout: ' + str(path))
        if names and sections[names][1] != 3:  # e_shstrndx must identify SHT_STRTAB.
            raise RuntimeError('unsupported ELF layout: ' + str(path))
        # gABI section-header references: symbol/string tables, relocations,
        # hashes, dynamic data, groups and extended symbol section indexes.
        link_types = {2: (3,), 11: (3,), 4: (2, 11), 9: (2, 11),
                      5: (2, 11), 6: (3,), 17: (2, 11), 18: (2, 11)}
        for section in sections[1:]:
            type_, flags, link, info = section[1], section[2], section[6], section[7]
            if link >= count or (flags & 0x40 and info >= count):  # SHF_INFO_LINK
                raise RuntimeError('unsupported ELF layout: ' + str(path))
            targets = link_types.get(type_)
            if targets:
                if sections[link][1] not in targets:
                    raise RuntimeError('unsupported ELF layout: ' + str(path))
            elif link and not flags & 0x80:  # Only SHF_LINK_ORDER defines another link.
                raise RuntimeError('unsupported ELF layout: ' + str(path))
            if type_ in (2, 11):  # sh_info is a symbol count, not a section index.
                if section[9] != 24 or section[5] % 24 or not 0 < info <= section[5] // 24:
                    raise RuntimeError('unsupported ELF layout: ' + str(path))
            elif type_ in (4, 9):  # Relocations in ET_REL must name a target section.
                if not 0 < info < count or sections[info][1] == 0:
                    raise RuntimeError('unsupported ELF layout: ' + str(path))
            elif type_ == 17:  # SHT_GROUP sh_info indexes the linked symbol table.
                symbols = sections[link]
                if symbols[9] != 24 or symbols[5] % 24 or info >= symbols[5] // 24:
                    raise RuntimeError('unsupported ELF layout: ' + str(path))
            elif info and (type_ in (5, 6, 18) or not flags & 0x40):
                raise RuntimeError('unsupported ELF layout: ' + str(path))
    elif (header[9] != 56 or not 0 < header[10] < 0xfffc
          or header[5] < ehdr.size or header[5] + header[10] * 56 > len(data)):
        raise RuntimeError('unsupported ELF layout: ' + str(path))
    return kind


def clear_execstack(path: Path) -> None:
    # PBS's BOLT ET_EXEC layout crashes patchelf --clear-execstack (0.18/0.19).
    # Append a mapped program-header table instead of relocating code/data.
    # ELF64 LE is the entire supported Linux payload ABI (x86_64/aarch64).
    ehdr = struct.Struct('<16sHHIQQQIHHHHHH')
    phdr = struct.Struct('<IIQQQQQQ')
    data = bytearray(path.read_bytes())
    header = list(ehdr.unpack_from(data))
    ident, kind, machine = header[:3]
    offset, count = header[5], header[10]
    if (ident[:7] != b'\x7fELF\x02\x01\x01' or kind not in (2, 3)
            or machine not in (62, 183) or header[8:10] != [ehdr.size, phdr.size]
            or not 0 < count < 0xfffc or offset < ehdr.size
            or offset + count * phdr.size > len(data)):
        raise RuntimeError('unsupported ELF layout: ' + str(path))
    segments = [list(phdr.unpack_from(data, offset + i * phdr.size)) for i in range(count)]
    stacks = [i for i, segment in enumerate(segments) if segment[0] == 0x6474e551]
    if len(stacks) > 1:
        raise RuntimeError('duplicate GNU_STACK: ' + str(path))
    if stacks:
        index = stacks[0]
        segments[index][1] &= ~1  # PF_X
        phdr.pack_into(data, offset + index * phdr.size, *segments[index])
    else:
        loads = [segment for segment in segments if segment[0] == 1]
        if not loads or any(s[5] > s[6] or s[2] + s[5] > len(data) for s in loads):
            raise RuntimeError('invalid ELF LOAD segments: ' + str(path))
        # 64 KiB alignment supports both 4 KiB and 64 KiB native page sizes.
        alignment = 0x10000
        table_offset = (len(data) + alignment - 1) & -alignment
        address = (max(s[3] + s[6] for s in loads) + alignment - 1) & -alignment
        phdrs = [i for i, segment in enumerate(segments) if segment[0] == 6]
        if len(phdrs) > 1:
            raise RuntimeError('duplicate PT_PHDR: ' + str(path))
        if not phdrs:
            segments.insert(0, [0] * 8)
        table_size = (len(segments) + 2) * phdr.size
        if address + table_size >= 1 << 64:
            raise RuntimeError('ELF address overflow: ' + str(path))
        # PT_PHDR describes its new mapping; preserve every existing LOAD and
        # all code, dynamic tags, relocations, notes and section headers.
        segments[phdrs[0] if phdrs else 0] = [6, 4, table_offset, address, address,
                                            table_size, table_size, 8]
        segments.append([0x6474e551, 6, 0, 0, 0, 0, 0, 16])  # GNU_STACK RW
        segments.append([1, 4, table_offset, address, address,
                         table_size, table_size, alignment])  # LOAD R
        data.extend(b'\0' * (table_offset - len(data)))
        data.extend(b''.join(phdr.pack(*segment) for segment in segments))
        header[5], header[10] = table_offset, len(segments)
        ehdr.pack_into(data, 0, *header)
    with path.open('r+b') as stream:
        stream.write(data)


def stack_headers(path: Path) -> list[str]:
    output = subprocess.check_output(['readelf', '-lW', str(path)], text=True,
                                     env=dict(os.environ, LC_ALL='C'))
    return [''.join(line.split()[6:-1]) for line in output.splitlines()
            if line.split() and line.split()[0] == 'GNU_STACK']


def check_tree(root: Path, *, fix: bool = False) -> None:
    root = root.resolve(strict=True)
    count = 0
    relocatable = 0
    unsafe = []
    for path in sorted(root.rglob('*')):
        if path.is_symlink():
            if not path.resolve(strict=True).is_relative_to(root):
                raise RuntimeError('interpreter symlink escapes tree: ' + str(path))
            continue
        if not path.is_file():
            continue
        with path.open('rb') as stream:
            if stream.read(4) != b'\x7fELF':
                continue
        if elf_kind(path) == 1:
            relocatable += 1
            print(f'NON-LOADABLE ET_REL: {path.relative_to(root)}', flush=True)
            continue
        count += 1
        headers = stack_headers(path)
        if len(headers) == 1 and headers[0] and not set(headers[0]) & {'E', 'X'}:
            continue
        reason = 'missing' if not headers else 'executable or invalid'
        name = str(path.relative_to(root))
        if fix:
            clear_execstack(path)
            print(f'FIX GNU_STACK ({reason}): {name}', flush=True)
            headers = stack_headers(path)
            if len(headers) == 1 and headers[0] and not set(headers[0]) & {'E', 'X'}:
                continue
        unsafe.append(f'{name} ({reason})')
    if unsafe:
        raise RuntimeError('unsafe GNU_STACK: ' + ', '.join(unsafe))
    if not count:
        raise RuntimeError('no bundled loadable ELF files found: ' + str(root))
    print(f'PASS GNU_STACK: all {count} bundled loadable ELFs have non-executable stack headers; '
          f'{relocatable} non-loadable ET_REL files', flush=True)


THREAD = '''
import threading
completed = threading.Event()
thread = threading.Thread(target=completed.set)
thread.start()
thread.join()
assert completed.is_set()
print('PASS MDWE: bundled interpreter started and joined a thread', flush=True)
'''

# Set MDWE in a separate process BEFORE exec, so interpreter/loader startup and
# pthread stack allocation both run under the inherited kernel restriction.
# Linux UAPI: PR_SET_MDWE=65, PR_GET_MDWE=66, PR_MDWE_REFUSE_EXEC_GAIN=1.
PRCTL_EXEC = '''
import ctypes, errno, os, sys
libc = ctypes.CDLL(None, use_errno=True)
zero = ctypes.c_ulong(0)
if libc.prctl(65, ctypes.c_ulong(1), zero, zero, zero) != 0:
    error = ctypes.get_errno()
    if error in (errno.EINVAL, errno.ENOSYS):
        print('MDWE_UNSUPPORTED=' + str(error), flush=True)
        sys.exit(77)
    raise OSError(error, os.strerror(error))
if libc.prctl(66, zero, zero, zero, zero) != 1:
    raise RuntimeError('PR_GET_MDWE did not confirm REFUSE_EXEC_GAIN')
os.execv(sys.argv[1], sys.argv[1:])
'''


def check_mdwe(root: Path) -> None:
    executable = root.resolve(strict=True) / 'bin/python3'
    command = [str(executable), '-I', '-c', THREAD]
    manager_reason = 'systemctl/systemd-run unavailable'
    if shutil.which('systemctl') and shutil.which('systemd-run'):
        probe = subprocess.run(['systemctl', '--user', 'is-system-running'],
                               text=True, capture_output=True, timeout=15)
        state = probe.stdout.strip()
        if probe.returncode in (0, 1) and state in {
            'running', 'degraded', 'starting', 'initializing', 'maintenance'
        }:
            print('MDWE venue: systemd user manager', flush=True)
            subprocess.run(['systemd-run', '--user', '--wait', '--pipe', '--collect',
                            '-p', 'MemoryDenyWriteExecute=yes', '--', *command],
                           check=True, timeout=30)
            return
        manager_reason = f'user manager unavailable (probe exit {probe.returncode}, state {state!r})'
    print('MDWE venue: PR_SET_MDWE; ' + manager_reason, flush=True)
    result = subprocess.run([sys.executable, '-I', '-c', PRCTL_EXEC, *command],
                            text=True, capture_output=True, timeout=30)
    print(result.stdout, end='', flush=True)
    print(result.stderr, end='', file=sys.stderr, flush=True)
    if (result.returncode == 77 and not result.stderr
            and result.stdout in ('MDWE_UNSUPPORTED=22\n', 'MDWE_UNSUPPORTED=38\n')):
        print('SKIP MDWE: ' + manager_reason + '; kernel does not support PR_SET_MDWE', flush=True)
    else:
        result.check_returncode()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('python_tree', type=Path)
    parser.add_argument('--fix', action='store_true', help='add/clear non-executable GNU_STACK headers')
    parser.add_argument('--mdwe', action='store_true', help='also start a bundled thread under MDWE')
    args = parser.parse_args()
    check_tree(args.python_tree, fix=args.fix)
    if args.mdwe:
        check_mdwe(args.python_tree)


if __name__ == '__main__':
    main()
