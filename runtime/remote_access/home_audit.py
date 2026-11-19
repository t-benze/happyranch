"""B16 retained audit; callers must await intent persistence before effects.

The helper brackets existing device effects; lifecycle effects remain deferred.
Identifiers are local audit correlation labels, never node identities, keys, digests, paths, or arbitrary diagnostics.
An I/O failure may follow a partial append/fsync: it never proves no effect.
"""
from __future__ import annotations

import fcntl
import json
import os
import re
import stat
import time
from dataclasses import asdict, dataclass, replace
from enum import Enum
from pathlib import Path

AUDIT_PATH = Path('/var/lib/happyranch-remote-access/home-audit.jsonl')
MAX_RECORD_BYTES = 1024
MAX_FILE_BYTES = 1024 * 1024
_DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_FILE_FLAGS = os.O_RDWR | os.O_APPEND | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK


class _StringEnum(str, Enum):
    """Value text on every supported Python, including system Python 3.10."""

    def __str__(self) -> str:
        return self.value

    def __format__(self, spec: str) -> str:
        return format(self.value, spec)


class Event(_StringEnum):
    ACTIVATION = 'activation'
    INVITE_ISSUED = 'invite_issued'
    INVITE_CANCELLED = 'invite_cancelled'
    INVITE_RECOVERED = 'invite_recovered'
    INVITE_EXPIRED = 'invite_expired'
    DEVICE_APPROVED = 'device_approved'
    DEVICE_REJECTED = 'device_rejected'
    DEVICE_REVOKED = 'device_revoked'
    REFRESH = 'refresh'
    POLICY_RENEW = 'policy_renew'
    POLICY_FAIL = 'policy_fail'
    TURN_OFF = 'turn_off'
    UNCERTAIN = 'uncertain'


class Phase(_StringEnum):
    PREPARED = 'prepared'
    ACTIVATING = 'activating'
    JOINED = 'joined'
    READY = 'ready'
    FAILED_UNCERTAIN = 'failed_uncertain'


class Component(_StringEnum):
    SIDECAR = 'sidecar'
    CONNECTOR = 'connector'
    HEADSCALE = 'headscale'
    UNITS = 'units'
    LIVE_STATE = 'live_state'
    CREDENTIALS = 'credentials'
    ACCOUNTS = 'accounts'


class Outcome(_StringEnum):
    SUCCEEDED = 'succeeded'
    FAILED = 'failed'
    REFUSED = 'refused'
    ABSENT = 'absent'
    RETAINED = 'retained'
    PARTIAL_CLEANUP = 'partial_cleanup'
    UNCERTAIN = 'uncertain'


class Stage(_StringEnum):
    INTENT = 'intent'
    OUTCOME = 'outcome'
    UNCERTAIN = 'uncertain'


class AuditRefused(ValueError):
    """Input-free errors; unavailable may mean a partially persisted append."""


def _identifier(value: str, prefix: str) -> None:
    if (type(value) is not str or not 1 <= len(value) <= 320
            or re.fullmatch(prefix + r'-[a-z0-9]{1,12}(?:-[a-z0-9]{1,12})*', value) is None
            or re.search(r'nodekey|tskey|token|secret|bearer|digest', value)
            or re.search(r'(?:^|-)[0-9a-f]{8,}(?:-|$)', value)):
        raise AuditRefused('record')


def _enum(value: object, kind: type[_StringEnum]) -> None:
    if type(value) is not str and type(value) is not kind:
        raise AuditRefused('record')
    try:
        kind(value)
    except ValueError:
        raise AuditRefused('record') from None


@dataclass(frozen=True, repr=False)
class Record:
    event: Event
    stage: Stage
    intent_id: str
    operation_id: str
    timestamp_ns: int
    subject_id: str | None = None
    phase: Phase | None = None
    component: Component | None = None
    outcome: Outcome | None = None

    def __post_init__(self) -> None:
        _enum(self.event, Event)
        _enum(self.stage, Stage)
        _identifier(self.intent_id, 'intent')
        _identifier(self.operation_id, 'op')
        if self.subject_id is not None:
            _identifier(self.subject_id, 'subject')
        if type(self.timestamp_ns) is not int or not 0 <= self.timestamp_ns < 2**63:
            raise AuditRefused('record')
        if self.phase is not None:
            _enum(self.phase, Phase)
        if self.component is not None:
            _enum(self.component, Component)
        if self.outcome is not None:
            _enum(self.outcome, Outcome)
        if self.event == Event.UNCERTAIN:
            if self.stage != Stage.UNCERTAIN or self.outcome != Outcome.UNCERTAIN:
                raise AuditRefused('record')
        else:
            if ((self.event == Event.ACTIVATION) != (self.phase is not None)
                    or (self.event == Event.TURN_OFF) != (self.component is not None)
                    or self.stage == Stage.UNCERTAIN
                    or (self.stage == Stage.INTENT) != (self.outcome is None)
                    or self.outcome == Outcome.UNCERTAIN
                    or (self.event != Event.TURN_OFF and self.outcome in
                        (Outcome.ABSENT, Outcome.RETAINED, Outcome.PARTIAL_CLEANUP))):
                raise AuditRefused('record')


def encode(record: Record) -> bytes:
    """Encode exactly the closed fields, including the newline in the byte cap."""
    if type(record) is not Record:
        raise AuditRefused('record')
    record.__post_init__()
    data = (json.dumps({'version': 1, **asdict(record)}, sort_keys=True,
                       separators=(',', ':'), ensure_ascii=True) + '\n').encode('ascii')
    if len(data) > MAX_RECORD_BYTES:
        raise AuditRefused('record-too-large')
    return data


class HomeAudit:
    """Existing protected parent only; default custody is root:root.

    The path and expected IDs can bind an isolated filesystem, as for the B8
    helper. Cooperating writers lock the same parent directory through fsync,
    so append size checks and rotation cannot race each other. This is not a
    fence against another root process replacing entries outside this API.
    """

    def __init__(self, path: Path = AUDIT_PATH, *, uid: int = 0, gid: int = 0) -> None:
        self.path = Path(path)
        if (not self.path.is_absolute() or '..' in self.path.parts
                or not self.path.name or self.path == Path('/')
                or any(type(value) is not int or not 0 <= value < 2**32 - 1
                       for value in (uid, gid))):
            raise AuditRefused('custody')
        self.uid, self.gid = uid, gid

    def intent(self, event: Event, *, intent_id: str, operation_id: str,
               subject_id: str | None = None, phase: Phase | None = None,
               component: Component | None = None) -> Record:
        record = Record(event, Stage.INTENT, intent_id, operation_id, time.time_ns(),
                        subject_id, phase, component)
        self.append(record)
        return record

    def outcome(self, intent: Record, outcome: Outcome) -> None:
        self._intent(intent)
        self.append(replace(intent, stage=Stage.OUTCOME, outcome=outcome,
                            timestamp_ns=time.time_ns()))

    def uncertain(self, intent: Record) -> None:
        self._intent(intent)
        self.append(replace(intent, event=Event.UNCERTAIN, stage=Stage.UNCERTAIN,
                            outcome=Outcome.UNCERTAIN, timestamp_ns=time.time_ns()))

    @staticmethod
    def _intent(record: Record) -> None:
        if type(record) is not Record or record.stage != Stage.INTENT:
            raise AuditRefused('record')
        encode(record)

    def _directory(self) -> int:
        descriptor = os.open('/', _DIRECTORY_FLAGS)
        try:
            for part in self.path.parent.parts[1:]:
                child = os.open(part, _DIRECTORY_FLAGS, dir_fd=descriptor)
                os.close(descriptor)
                descriptor = child
            info = os.fstat(descriptor)
            if (not stat.S_ISDIR(info.st_mode) or info.st_uid != self.uid
                    or info.st_gid != self.gid or info.st_mode & 0o022):
                raise AuditRefused('custody')
            return descriptor
        except BaseException:
            os.close(descriptor)
            raise

    def _file_info(self, info: os.stat_result) -> None:
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or info.st_uid != self.uid or info.st_gid != self.gid
                or stat.S_IMODE(info.st_mode) != 0o600
                or info.st_size > MAX_FILE_BYTES):
            raise AuditRefused('custody')

    def _open(self, directory: int) -> int:
        try:
            descriptor = os.open(self.path.name, _FILE_FLAGS, dir_fd=directory)
        except FileNotFoundError:
            descriptor = os.open(self.path.name, _FILE_FLAGS | os.O_CREAT | os.O_EXCL,
                                 0o600, dir_fd=directory)
        try:
            self._file_info(os.fstat(descriptor))
        except BaseException:
            os.close(descriptor)
            raise
        return descriptor

    def append(self, record: Record) -> None:
        """Persist one complete line; never rewrite existing bytes or auto-clean.

        A Record/intent reference is not an authorization or replay token. The
        future lifecycle owner must keep its own effect/settlement discipline.
        """
        data = encode(record)  # refuse invalid/oversize input before any I/O
        directory = descriptor = None
        try:
            directory = self._directory()
            fcntl.flock(directory, fcntl.LOCK_EX)
            descriptor = self._open(directory)
            size = os.fstat(descriptor).st_size
            # Preserve a failed append's torn bytes as a separate evidence line.
            separator = b'\n' if size and os.pread(descriptor, 1, size - 1) != b'\n' else b''
            if size + len(separator) + len(data) > MAX_FILE_BYTES:
                backup = self.path.name + '.1'
                try:
                    info = os.stat(backup, dir_fd=directory, follow_symlinks=False)
                except FileNotFoundError:
                    pass
                else:
                    self._file_info(info)
                os.rename(self.path.name, backup, src_dir_fd=directory, dst_dir_fd=directory)
                os.fsync(directory)
                os.close(descriptor)
                descriptor = None
                descriptor = self._open(directory)
                separator = b''  # the new current file has no torn tail
            remaining = memoryview(separator + data)
            while remaining:
                written = os.write(descriptor, remaining)
                if written <= 0:
                    raise AuditRefused('unavailable')
                remaining = remaining[written:]
            os.fsync(descriptor)
            # An existing entry may be left by an interrupted create/rotation.
            # Establish its durability before every successful acknowledgement.
            os.fsync(directory)
        except OSError:
            raise AuditRefused('unavailable') from None
        finally:
            if descriptor is not None:
                os.close(descriptor)
            if directory is not None:
                os.close(directory)
