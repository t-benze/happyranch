"""Root-only, socket-activated release-daemon ownership verifier.

The wire grammar is closed: resolve\n or confirm <local-port> <daemon-port>\n.
No request can select a file, user, unit or command. Success is never cached.
"""
from __future__ import annotations

import json
import logging
import os
import pwd
import re
import socket
import stat
import subprocess
import time
from pathlib import Path

SOCKET_PATH = '/run/happyranch-release-verifier/control.sock'
RECORD_PATH = Path('/etc/happyranch/release-daemon.json')
CONFIG_PATH = Path('/etc/happyranch/connector.json')
UNIT = 'happyranch-daemon.service'
FRAGMENT = '/usr/lib/systemd/user/happyranch-daemon.service'
_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
_REASON = re.compile(r'[a-z_]{1,48}')


class VerificationRefused(OSError):
    """Only a closed machine reason may cross the privileged boundary."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


def _refuse(reason: str) -> None:
    raise VerificationRefused(reason)


def _read(path: Path, *, uid: int, limit: int, trusted: bool = False) -> bytes:
    """Descriptor-relative, no-follow traversal, including every parent."""
    if not path.is_absolute() or '..' in path.parts:
        _refuse('unsafe_path')
    parent = os.open('/', _FLAGS | os.O_DIRECTORY)
    try:
        for part in path.parts[1:-1]:
            child = os.open(part, _FLAGS | os.O_DIRECTORY, dir_fd=parent)
            os.close(parent)
            parent = child
            info = os.fstat(parent)
            if trusted and (info.st_uid != 0 or info.st_mode & 0o022):
                _refuse('unsafe_parent')
        fd = os.open(path.name, _FLAGS, dir_fd=parent)
        try:
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != uid
                    or info.st_nlink != 1 or info.st_size > limit
                    or (trusted and info.st_mode & 0o022)):
                _refuse('unsafe_file')
            data = os.read(fd, limit + 1)
            after = os.fstat(fd)
            def signature(st):
                return (st.st_dev, st.st_ino, st.st_mode, st.st_uid, st.st_nlink,
                        st.st_size, st.st_mtime_ns, st.st_ctime_ns)
            if len(data) > limit or len(data) != info.st_size or signature(after) != signature(info):
                _refuse('changing_file')
            return data
        finally:
            os.close(fd)
    finally:
        os.close(parent)


def _object(data: bytes) -> dict:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                _refuse('invalid_record')
            result[key] = value
        return result
    value = json.loads(data, object_pairs_hook=pairs)
    if not isinstance(value, dict):
        _refuse('invalid_record')
    return value


def validate_record(record: dict) -> None:
    if (record.keys() != {'user', 'uid', 'data_home', 'unit', 'fragment_path'}
            or not isinstance(record['user'], str)
            or re.fullmatch(r'[a-z_][a-z0-9_-]{0,31}', record['user']) is None
            or type(record['uid']) is not int or not 0 < record['uid'] < 2**32 - 1
            or record['unit'] != UNIT or record['fragment_path'] != FRAGMENT
            or not isinstance(record['data_home'], str)):
        _refuse('invalid_record')
    home = Path(record['data_home'])
    if (not home.is_absolute() or '..' in home.parts or home == Path('/')
            or home.parts[1] in ('etc', 'var')
            or home.is_relative_to(Path(pwd.getpwuid(record['uid']).pw_dir) / '.happyranch')
            or any(ord(c) < 32 for c in str(home))):
        _refuse('unsafe_data_home')


class ReleaseVerifier:
    """All configuration is selected by root, never by socket clients."""

    def _configuration(self) -> tuple[dict, set[int]]:
        record = _object(_read(RECORD_PATH, uid=0, limit=4096, trusted=True))
        validate_record(record)
        config = _object(_read(CONFIG_PATH, uid=0, limit=65536, trusted=True))
        excluded = config.get('excluded_daemon_ports', [])
        if (not isinstance(excluded, list)
                or any(type(p) is not int or not 1 <= p <= 65535 for p in excluded)):
            _refuse('invalid_exclusions')
        return record, {8765, *excluded}

    def _unit(self, record: dict) -> tuple[int, str]:
        result = subprocess.run([
            '/usr/bin/systemctl', '--user', '-M', record['user'] + '@.host', 'show', UNIT,
            '-p', 'MainPID,ActiveState,FragmentPath,DropInPaths',
        ], capture_output=True, timeout=2, check=False, env={'PATH': '/usr/bin:/bin', 'LC_ALL': 'C'})
        if result.returncode or len(result.stdout) > 8192:
            _refuse('unit_unavailable')
        properties = {}
        for line in result.stdout.decode('utf-8').splitlines():
            key, separator, value = line.partition('=')
            if not separator or key in properties:
                _refuse('unit_invalid')
            properties[key] = value
        if (properties.keys() != {'MainPID', 'ActiveState', 'FragmentPath', 'DropInPaths'}
                or properties['ActiveState'] != 'active'
                or properties['FragmentPath'] != FRAGMENT or properties['DropInPaths']):
            _refuse('unit_identity')
        if not re.fullmatch('[1-9][0-9]{0,9}', properties['MainPID']):
            _refuse('unit_pid')
        pid = int(properties['MainPID'])
        # Process start time plus uid distinguishes PID reuse across observations.
        proc = Path(f'/proc/{pid}')
        # Non-dumpable processes can have root-owned proc directories; read the
        # kernel's UID fields rather than interpreting that ownership as uid 0.
        with (proc / 'status').open(encoding='ascii') as status:
            metadata = status.read(8193)
        uid = re.search(r'^Uid:\s+([0-9]+)\s+([0-9]+)\s+([0-9]+)\s+([0-9]+)$', metadata, re.MULTILINE)
        if len(metadata) > 8192 or uid is None or any(int(value) != record['uid'] for value in uid.groups()):
            _refuse('process_owner')
        start = (proc / 'stat').read_text().rsplit(')', 1)[1].split()[19]
        return pid, start

    def _inodes(self, pid: int) -> set[str]:
        entries = Path(f'/proc/{pid}/fd')
        result = set()
        for index, entry in enumerate(entries.iterdir()):
            if index >= 4096:
                _refuse('fd_limit')
            try:
                target = os.readlink(entry)
            except FileNotFoundError:
                continue  # unrelated fds can close; absence never supplies proof
            match = re.fullmatch(r'socket:\[([0-9]+)\]', target)
            if match:
                result.add(match[1])
        return result

    def _socket(self, port: int, peer: int | None, inodes: set[str]) -> bool:
        with open('/proc/net/tcp', encoding='ascii') as table:
            if len(table.readline(512)) >= 512:
                _refuse('tcp_invalid')
            for index, line in enumerate(table):
                if index >= 16384 or len(line) > 512:
                    _refuse('tcp_limit')
                columns = line.split()
                if len(columns) < 10:
                    _refuse('tcp_invalid')
                local, remote, state, inode = columns[1], columns[2], columns[3], columns[9]
                expected_peer = '00000000:0000' if peer is None else f'0100007F:{peer:04X}'
                if (local == f'0100007F:{port:04X}' and remote == expected_peer
                        and state == ('0A' if peer is None else '01') and inode in inodes):
                    return True
        return False

    def verify(self, daemon_port: int | None = None, local_port: int | None = None) -> int:
        record, excluded = self._configuration()
        data = _read(Path(record['data_home']) / 'daemon.port', uid=record['uid'], limit=16)
        if re.fullmatch(rb'[0-9]{1,5}\n?', data) is None:
            _refuse('invalid_port')
        port = int(data)
        if not 1 <= port <= 65535:
            _refuse('invalid_port')
        if port in excluded:
            _refuse('excluded_port')
        if daemon_port is not None and port != daemon_port:
            _refuse('port_changed')
        identity = self._unit(record)
        deadline = time.monotonic() + (0.25 if local_port is not None else 0)
        while True:
            if self._socket(port, local_port, self._inodes(identity[0])):
                break
            if time.monotonic() >= deadline:
                _refuse('socket_not_owned')
            # TCP handshake can complete before MainPID accepts its server fd.
            time.sleep(0.01)
        if self._unit(record) != identity:
            _refuse('process_changed')
        return port

    def request(self, request: bytes) -> bytes:
        try:
            if request == b'resolve\n':
                return f'port {self.verify()}\n'.encode('ascii')
            match = re.fullmatch(rb'confirm ([1-9][0-9]{0,4}) ([1-9][0-9]{0,4})\n', request)
            if match is None or any(int(p) > 65535 for p in match.groups()):
                _refuse('invalid_request')
            self.verify(daemon_port=int(match[2]), local_port=int(match[1]))
            return b'ok\n'
        except VerificationRefused as exc:
            logging.getLogger(__name__).warning('release verification refused: %s', exc.reason)
            return f'refused {exc.reason}\n'.encode('ascii')
        except (OSError, ValueError, UnicodeError, subprocess.SubprocessError, KeyError, IndexError):
            logging.getLogger(__name__).warning('release verification refused: evidence_unavailable')
            return b'refused evidence_unavailable\n'


class VerifierClient:
    """Fixed root-owned socket endpoint; fresh exchange for every operation."""

    def _exchange(self, request: bytes) -> bytes:
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as channel:
                channel.settimeout(6)
                channel.connect(SOCKET_PATH)
                channel.sendall(request)
                response = bytearray()
                while len(response) < 80 and not response.endswith(b'\n'):
                    chunk = channel.recv(80 - len(response))
                    if not chunk:
                        break
                    response.extend(chunk)
        except OSError:
            raise VerificationRefused('verifier_unavailable') from None
        value = bytes(response)
        if value.startswith(b'refused '):
            reason = value[8:-1].decode('ascii', errors='replace')
            raise VerificationRefused(reason if _REASON.fullmatch(reason) else 'verifier_protocol')
        return value

    def resolve(self) -> int:
        reply = self._exchange(b'resolve\n')
        match = re.fullmatch(rb'port ([1-9][0-9]{0,4})\n', reply)
        if match is None or int(match[1]) > 65535:
            raise VerificationRefused('verifier_protocol')
        return int(match[1])

    def confirm(self, local_port: int, daemon_port: int) -> None:
        if self._exchange(f'confirm {local_port} {daemon_port}\n'.encode('ascii')) != b'ok\n':
            raise VerificationRefused('verifier_protocol')


def main() -> None:
    if (os.getuid() != 0 or os.environ.get('LISTEN_PID') != str(os.getpid())
            or os.environ.get('LISTEN_FDS') != '1'):
        raise SystemExit('requires root systemd socket activation')
    verifier = ReleaseVerifier()
    with socket.socket(fileno=3) as listener:
        while True:
            channel, _ = listener.accept()
            with channel:
                channel.settimeout(1)
                try:
                    request = bytearray()
                    while len(request) < 64 and not request.endswith(b'\n'):
                        chunk = channel.recv(64 - len(request))
                        if not chunk:
                            break
                        request.extend(chunk)
                    channel.sendall(verifier.request(bytes(request)))
                except OSError:
                    continue


if __name__ == '__main__':
    main()
