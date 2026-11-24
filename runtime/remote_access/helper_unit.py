"""Trusted shipped helper policy, consumed by package and ownership readers."""
from __future__ import annotations

import os
import stat

PACKAGED_HELPER_UNIT = "usr/lib/systemd/system/happyranch-remote-access-helper.service"


PACKAGED_HELPER_UNIT_BYTES = b"""[Unit]
Description=HappyRanch remote access helper

[Service]
Type=dbus
BusName=ai.happyranch.RemoteAccess1
User=root
ExecStart=/usr/bin/python3 -I /opt/happyranch-desktop/bin/happyranch-remote-access-helper
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=read-only
PrivateTmp=yes
ReadWritePaths=/etc
ReadWritePaths=/var/lib/happyranch-remote-access
ReadWritePaths=-/var/lib/happyranch-headscale
ReadWritePaths=-/var/lib/happyranch-connector
ReadWritePaths=-/var/lib/happyranch-tsnet-sidecar
ReadWritePaths=-/run/happyranch-headscale
ReadWritePaths=-/run/happyranch-connector
ReadWritePaths=-/run/happyranch-tsnet-sidecar
ReadWritePaths=-/opt/happyranch
"""


def _owned_helper_unit(path: str, info: os.stat_result, data: bytes,
                       owner: tuple[int, int]) -> bool:
    """K1/K2 exact package policy; owner maps root only in the existing temp-root venue."""
    return (path == PACKAGED_HELPER_UNIT and stat.S_ISREG(info.st_mode)
            and info.st_nlink == 1 and (info.st_uid, info.st_gid) == owner
            and stat.S_IMODE(info.st_mode) == 0o644 and data == PACKAGED_HELPER_UNIT_BYTES)
