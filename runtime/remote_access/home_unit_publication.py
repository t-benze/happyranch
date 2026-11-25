"""A5 exact-byte replacement shared only by the two activation unit owners."""
from __future__ import annotations

import os
from typing import Callable

from runtime.remote_access.home_activation import _signature
from runtime.remote_access.home_activation_coordinator import ActivationRefused
from runtime.remote_access.installer import ManagedInstaller


def publish_home_unit(io: ManagedInstaller, name: str, *, legacy: bytes, home: bytes,
                      require: Callable[[], None]) -> None:
    """Caller holds the activation lock and revalidates its phase/source authority.

    Retain pending evidence on every failure; never restore or remove final bytes.
    Neither this primitive nor daemon-reload enables, starts or links a service.
    """
    from runtime.remote_access.policy_renew_units import SERVICE_NAME, render_previous_service, render_service
    relative = "etc/systemd/system"
    if name == SERVICE_NAME:
        if legacy != render_previous_service().encode() or home != render_service().encode():
            raise ActivationRefused()
        relative = "usr/lib/systemd/system"
    elif name not in ("happyranch-headscale.service", "happyranch-tsnet-sidecar.service"):
        raise ActivationRefused()
    with io._publication_parent(relative) as (parent, check):
        require()
        io._headscale_no_pending(parent, name)
        before = _signature(os.stat(name, dir_fd=parent, follow_symlinks=False))
        current = io._read_unit(parent, name)
        if current not in (legacy, home):
            raise ActivationRefused()
        if current == home:
            check()
            return
        pending = "." + name + ".pending"
        descriptor = os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                             | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=parent)
        try:
            os.fchown(descriptor, *io.root_owner)
            os.fchmod(descriptor, 0o644)
            with os.fdopen(descriptor, "wb", closefd=False) as stream:
                if stream.write(home) != len(home):
                    raise ActivationRefused()
                stream.flush()
                os.fsync(descriptor)
            created = _signature(os.fstat(descriptor))
            require()
            check()
            if (io._read_unit(parent, name) != legacy
                    or _signature(os.stat(name, dir_fd=parent, follow_symlinks=False)) != before
                    or io._read_unit(parent, pending) != home
                    or _signature(os.stat(pending, dir_fd=parent, follow_symlinks=False)) != created
                    or _signature(os.fstat(descriptor)) != created):
                raise ActivationRefused()
            io._rename_provision_entry(parent, pending, name, updating=True)
            os.fsync(parent)
            check()
            if (io._read_unit(parent, name) != home
                    or _signature(os.stat(name, dir_fd=parent, follow_symlinks=False))
                    != _signature(os.fstat(descriptor))):
                raise ActivationRefused()
        finally:
            os.close(descriptor)
