"""Persistent old-payload refusal under the original installer directory flock.

This is only the first step of explicit retirement. It neither revokes existing
devices/transports nor authorizes stop, cleanup, package replacement or a final
receipt. The historical activation reader already refuses this pending name,
so interruption cannot leave the old helper free to resume home activation.
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from typing import TYPE_CHECKING, Iterator

from runtime.remote_access.home_retirement import (
    HomeRetirement, RetirementUncertain, STATE_DIRECTORY, _FILE_FLAGS, _signature,
)
from runtime.remote_access.installer_lock import installer_state_lock

if TYPE_CHECKING:
    from runtime.remote_access.installer import ManagedInstaller

_NAME = ".home-activation.json.pending"
_BYTES = b'{"format":"home-retirement-fence@1"}\n'


class RetirementFence:
    """Live capability bound to the held directory and pinned fence inode.

    Constructed by ``retirement_fence`` only. Even a completed fence is not
    revocation success. There is deliberately no remove/reset/unfence operation.
    """

    def __init__(self, io: ManagedInstaller, directory: int) -> None:
        self._io, self._directory = io, directory
        self._identity: tuple[int, ...] | None = None

    def _observe(self) -> tuple[int, ...]:
        if self._directory < 0:
            raise RetirementUncertain()
        reader = HomeRetirement(self._io)
        with reader._entry(STATE_DIRECTORY + "/" + _NAME) as (parent, name, info):
            expected = os.fstat(self._directory)
            current = os.fstat(parent)
            if (name != _NAME or info is None
                    or (expected.st_dev, expected.st_ino) != (current.st_dev, current.st_ino)
                    or reader._read(parent, name, info, private=True) != _BYTES):
                raise RetirementUncertain()
            return _signature(info)

    def require(self, io: ManagedInstaller | None = None) -> None:
        if io is not None and (io.root, io.root_owner) != (self._io.root, self._io.root_owner):
            raise RetirementUncertain()
        if self._identity is None or self._observe() != self._identity:
            raise RetirementUncertain()

    def _establish(self) -> None:
        try:
            os.stat(_NAME, dir_fd=self._directory, follow_symlinks=False)
        except FileNotFoundError:
            descriptor = os.open(_NAME, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=self._directory)
            try:
                os.fchown(descriptor, *self._io.root_owner)
                os.fchmod(descriptor, 0o600)
                if os.write(descriptor, _BYTES) != len(_BYTES):
                    raise RetirementUncertain()
                os.fsync(descriptor)
                created = _signature(os.fstat(descriptor))
                observed = self._observe()
                if created != observed or _signature(os.fstat(descriptor)) != observed:
                    raise RetirementUncertain()
            finally:
                # Retain even partial/torn publication: the old reader still
                # refuses; unknown evidence is never deleted or adopted.
                os.close(descriptor)
        else:
            observed = self._observe()
            descriptor = os.open(_NAME, _FILE_FLAGS, dir_fd=self._directory)
            try:
                if _signature(os.fstat(descriptor)) != observed:
                    raise RetirementUncertain()
                os.fsync(descriptor)
                if _signature(os.fstat(descriptor)) != self._observe():
                    raise RetirementUncertain()
            finally:
                os.close(descriptor)
        # A previous complete write is not assumed durable. Explicit recovery
        # first revalidates exact custody/content, then synchronizes the parent.
        os.fsync(self._directory)
        self._identity = observed
        self.require()


@contextmanager
def retirement_fence(io: ManagedInstaller) -> Iterator[RetirementFence]:
    """Explicit coordinator use only; never called by detection or startup.

    No directory is initialized. The whole retirement action must keep this
    context, not release exclusion after creating an intent. A fresh explicit
    action can re-observe a complete protected fence, never an arbitrary pending
    file. State-directory contention has no filesystem/service/account effects.
    """
    fence: RetirementFence | None = None
    try:
        with installer_state_lock(io) as parent:
            fence = RetirementFence(io, parent)
            fence._establish()
            yield fence
            fence.require()
    finally:
        if fence is not None:
            fence._directory = -1
