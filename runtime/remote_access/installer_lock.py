"""Shared installer-state exclusion, retained independently of home activation."""
from __future__ import annotations

import fcntl
from contextlib import contextmanager
from typing import TYPE_CHECKING, Iterator

if TYPE_CHECKING:
    from runtime.remote_access.installer import ManagedInstaller

INSTALLER_STATE = "var/lib/happyranch-remote-access"


@contextmanager
def installer_state_lock(io: ManagedInstaller) -> Iterator[int]:
    """Hold the existing protected directory inode, never a replacement lock file.

    No directory is created. The no-follow parent walk and exact private-directory
    custody check apply before admission and after the protected operation.
    Activation and policy renewal must serialize on this same inode while the
    legacy activation callers remain installed.
    """
    from runtime.remote_access.installer import InstallerError

    try:
        with io._publication_parent(INSTALLER_STATE) as (parent, revalidate):
            io._check_directory(parent, INSTALLER_STATE, (0o700, io.root_owner))
            fcntl.flock(parent, fcntl.LOCK_EX | fcntl.LOCK_NB)
            revalidate()
            yield parent
            revalidate()
    except OSError:
        raise InstallerError("installer state unavailable") from None
