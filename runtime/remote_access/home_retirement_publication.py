"""Explicit minimized final publication after complete independent facts.

The active/inert owners supply their existing full account/native/cleanup
readback. This writer does not revoke, stop, delete or reconstruct provenance.
A complete staged record may be resynchronized; unknown/torn records refuse.
"""
from __future__ import annotations

import os
from typing import TYPE_CHECKING, Callable

from runtime.remote_access.home_retirement import (
    ACCOUNT, HomeRetirement, RECEIPT, RetirementUncertain, STATE_DIRECTORY, UNIT,
    _ABSENT_UNIT, _FILE_FLAGS, _FINAL_BYTES, _FINAL_PENDING, _LEGACY_PATHS, _signature,
)
from runtime.remote_access.home_retirement_fence import RetirementFence

if TYPE_CHECKING:
    from runtime.remote_access.installer import ManagedInstaller


def publish_final(io: ManagedInstaller, fence: RetirementFence, *, require: Callable[[], str]) -> bytes:
    """Publish only the exclusive-account-absent result, never by intent.

    Retained-account completion is not represented by the absent schema. The
    directory flock and permanent old-payload fence span the entire operation.
    After a successful rename, recovery never rewrites the final record.
    """
    reader = HomeRetirement(io)

    def guard() -> None:
        fence.require(io)
        reader._receipt()
        if require() != 'absent':
            raise RetirementUncertain()
        if (any(reader._present(path) for path in _LEGACY_PATHS)
                or reader._state_evidence(completed=True, publishing=True)
                or reader._config_evidence() or reader._unit_evidence()):
            raise RetirementUncertain()
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
        if data != _FINAL_BYTES:
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
                    if os.write(descriptor, _FINAL_BYTES) != len(_FINAL_BYTES):
                        raise RetirementUncertain()
                finally:
                    os.close(descriptor)
            expected = read(parent, name)
            descriptor = os.open(name, _FILE_FLAGS, dir_fd=parent)
            try:
                if expected is None or _signature(os.fstat(descriptor)) != expected[1]:
                    raise RetirementUncertain()
                guard()
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
                    expected = _FINAL_BYTES, _signature(os.fstat(descriptor))
                check()
                if read(parent, RECEIPT) != expected:
                    raise RetirementUncertain()
                guard()
                if reader.detect().state != 'retired':
                    raise RetirementUncertain()
                return _FINAL_BYTES
            finally:
                os.close(descriptor)
    except (OSError, ValueError, RuntimeError, TypeError, KeyError, AttributeError):
        raise RetirementUncertain() from None
