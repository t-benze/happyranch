"""Environment facts recording for the lab transcript.

The measured report must record the exact host/runtime path, OS/kernel,
container-runtime version, and immutable image digests. Docker output is
parsed here into a plain dict for the raw results.
"""

from __future__ import annotations

import json
import platform
import re


def host_facts() -> dict:
    """Python-visible host facts (OS, kernel, arch)."""
    return {
        "platform": platform.platform(),
        "system": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "python_version": platform.python_version(),
    }


def parse_uname(text: str) -> str:
    return text.strip()


def parse_os_release(text: str) -> dict:
    out: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        out[k.strip()] = v.strip().strip('"')
    return out


def parse_docker_version(text: str) -> dict:
    """Parse ``docker version --format '{{json .}}'`` client/server versions."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {}
    out: dict[str, str] = {}
    for key in ("Client", "Server"):
        if key not in data:
            continue
        ver = data[key].get("Version", "")
        api = data[key].get("ApiVersion", "")
        os_name = data[key].get("Os", "")
        arch = data[key].get("Arch", "")
        out[f"{key.lower()}_version"] = ver
        out[f"{key.lower()}_api_version"] = api
        out[f"{key.lower()}_os"] = os_name
        out[f"{key.lower()}_arch"] = arch
    return out


def parse_repo_digest(text: str) -> str | None:
    """Parse ``docker inspect --format '{{index .RepoDigests 0}}'`` output."""
    m = re.search(r"sha256:[0-9a-f]{64}", text)
    return m.group(0) if m else None
