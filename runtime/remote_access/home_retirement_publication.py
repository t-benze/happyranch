"""Explicit minimized final publication after complete independent facts.

The active/inert owners supply their existing full account/native/cleanup
readback. This writer does not revoke, stop, delete or reconstruct provenance.
A canonical-prefix staged record may be completed under fresh full proof;
unknown staged bytes and any malformed final record refuse.
"""
from __future__ import annotations

import os
from typing import TYPE_CHECKING, Callable

from runtime.remote_access.home_retirement import (
    ACCOUNT, HomeRetirement, RECEIPT, RetirementUncertain, STATE_DIRECTORY, UNIT,
    _ABSENT_UNIT, _FILE_FLAGS, _FINAL_BYTES, _FINAL_PENDING, _LEGACY_PATHS,
    _RETAINED, _RETAINED_BYTES, _signature,
)
from runtime.remote_access.home_retirement_fence import RetirementFence

if TYPE_CHECKING:
    from runtime.remote_access.installer import ManagedInstaller


def publish_final(io: ManagedInstaller, fence: RetirementFence, *, require: Callable[[], str]) -> bytes:
    """Publish only a currently proven final account disposition, never intent.

    The directory flock and permanent old-payload fence span the operation.
    After a successful rename, recovery never rewrites the final record.
    """
    reader = HomeRetirement(io)
    final_bytes = b''

    def guard() -> None:
        nonlocal final_bytes
        fence.require(io)
        current = reader._receipt()
        disposition = require()
        data = {'absent': _FINAL_BYTES, _RETAINED: _RETAINED_BYTES}.get(disposition)
        if (data is None or (final_bytes and data != final_bytes)
                or (current is not None and current != data)):
            raise RetirementUncertain()
        final_bytes = data
        consumers = reader._retained_consumers() if disposition == _RETAINED else frozenset()
        if (any(reader._present(path) for path in _LEGACY_PATHS)
                or reader._state_evidence(completed=True, publishing=True)
                or reader._config_evidence() or reader._unit_evidence(consumers)):
            raise RetirementUncertain()
        if disposition == 'absent':
            for lookup in (io._lookup_user, io._lookup_group):
                try:
                    lookup(ACCOUNT)
                except KeyError:
                    continue
                raise RetirementUncertain()
        fence.require(io)

    def read(parent: int, name: str) -> tuple[bytes, tuple[int, ...]] | None:
        try:
            info = os.stat(name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            return None
        data = reader._read(parent, name, info, private=True)
        valid = final_bytes.startswith(data) if name == _FINAL_PENDING else data == final_bytes
        if not valid:
            raise RetirementUncertain()
        return data, _signature(info)

    try:
        guard()
        if io.manager.show_properties(UNIT, tuple(_ABSENT_UNIT)) != _ABSENT_UNIT:
            # The caller has just proved exact original ownership, complete
            # stopped/disabled native state and absence of all old fragments.
            guard()
            io.manager.daemon_reload()
            guard()
        if io.manager.show_properties(UNIT, tuple(_ABSENT_UNIT)) != _ABSENT_UNIT:
            raise RetirementUncertain()
        with io._publication_parent(STATE_DIRECTORY) as (parent, check):
            final, staged = read(parent, RECEIPT), read(parent, _FINAL_PENDING)
            if final is not None and staged is not None:
                raise RetirementUncertain()
            name = RECEIPT if final is not None else _FINAL_PENDING
            if final is None and staged is None:
                guard()
                descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                    | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=parent)
                try:
                    os.fchown(descriptor, *io.root_owner)
                    os.fchmod(descriptor, 0o600)
                    if os.write(descriptor, final_bytes) != len(final_bytes):
                        raise RetirementUncertain()
                finally:
                    os.close(descriptor)
            expected = read(parent, name)
            partial = expected is not None and expected[0] != final_bytes
            descriptor = os.open(name, _FILE_FLAGS | (os.O_RDWR if partial else 0), dir_fd=parent)
            try:
                if expected is None or _signature(os.fstat(descriptor)) != expected[1]:
                    raise RetirementUncertain()
                guard()
                if partial:
                    # Only the fixed canonical prefix is repairable. Retain the
                    # original open inode and recheck its named identity after
                    # the potentially blocking complete proof, before writing.
                    if (read(parent, name) != expected
                            or _signature(os.fstat(descriptor)) != expected[1]):
                        raise RetirementUncertain()
                    os.lseek(descriptor, len(expected[0]), os.SEEK_SET)
                    suffix = final_bytes[len(expected[0]):]
                    if os.write(descriptor, suffix) != len(suffix):
                        raise RetirementUncertain()
                    expected = final_bytes, _signature(os.fstat(descriptor))
                    if read(parent, name) != expected:
                        raise RetirementUncertain()
                os.fsync(descriptor)
                os.fsync(parent)
                check()
                if read(parent, name) != expected:
                    raise RetirementUncertain()
                guard()
                if final is None:
                    if read(parent, RECEIPT) is not None:
                        raise RetirementUncertain()
                    io._rename_provision_entry(parent, _FINAL_PENDING, RECEIPT)
                    os.fsync(parent)
                    # Rename may change ctime. Keep the original open inode as
                    # the authority for the new name, never recapture its path.
                    expected = final_bytes, _signature(os.fstat(descriptor))
                check()
                if read(parent, RECEIPT) != expected:
                    raise RetirementUncertain()
                guard()
                if reader.detect().state != 'retired':
                    raise RetirementUncertain()
                return final_bytes
            finally:
                os.close(descriptor)
    except (OSError, ValueError, RuntimeError, TypeError, KeyError, AttributeError):
        raise RetirementUncertain() from None
