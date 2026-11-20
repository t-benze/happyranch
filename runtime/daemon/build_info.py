"""Load public release identity once, before the daemon serves requests."""
from __future__ import annotations

import json
import os
import re
import stat
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

_MAX_BUILD_INFO_BYTES = 65536
_UNRELEASED = "unreleased"


def _load_release() -> str:
    # Linux build-payload.sh writes <payload>/build-info.json beside python/.
    # Frozen builds currently have no such file; never guess from Git or cwd.
    if getattr(sys, "frozen", False):
        return _UNRELEASED
    path = Path(sys.prefix).parent / "build-info.json"
    try:
        # O_NONBLOCK avoids waiting on a FIFO; reject links and non-regular
        # files before reading, and bound both the file size and actual read.
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > _MAX_BUILD_INFO_BYTES:
                return _UNRELEASED
            raw = stream.read(_MAX_BUILD_INFO_BYTES + 1)
        if len(raw) > _MAX_BUILD_INFO_BYTES:
            return _UNRELEASED
        metadata = json.loads(raw)
        sha = metadata.get("source_sha") if isinstance(metadata, dict) else None
        if not isinstance(sha, str) or re.fullmatch(r"[0-9a-f]{40}", sha) is None or sha == "0" * 40:
            return _UNRELEASED
        package_version = version("happyranch")
        if re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", package_version) is None:
            return _UNRELEASED
        return f"{package_version}+{sha}"
    except (OSError, ValueError, UnicodeError, RecursionError, PackageNotFoundError):
        # Missing/malformed identity is public "unreleased", never diagnostics.
        return _UNRELEASED


RELEASE = _load_release()
