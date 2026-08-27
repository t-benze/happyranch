"""Unit tests for docker CLI wrappers (dockerctl).

The cell volume-size measurement must go through the docker daemon (a
read-only `du` sidecar on the pinned tailscale image) — never a host-side
walk of the docker volume mountpoint, which sits under the root-only
`/var/lib/docker` tree on the runner and fails with PermissionError
(observed in lab run 33038215874 at head e7e35cb8).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from dockerctl import Docker, Transcript
from cellspec import TAILSCALE_IMAGE

RUN = "cap-20260826T120000Z-ab12"


def _docker(scripted: list[subprocess.CompletedProcess]) -> tuple[Docker, list[list[str]]]:
    d = Docker(Transcript(Path("/tmp/cap-test-transcript.jsonl")), RUN)
    calls: list[list[str]] = []

    def fake_run(cmd, *, timeout=120, check=True, stdin_data=None):  # noqa: ANN001
        calls.append(list(cmd))
        return scripted.pop(0)

    d.run = fake_run  # type: ignore[method-assign]
    return d, calls


def test_volume_size_bytes_uses_readonly_du_sidecar():
    """Green: volume bytes come from a docker-daemon `du` sidecar on the
    pinned tailscale image — no host-path access under /var/lib/docker."""
    d, calls = _docker([subprocess.CompletedProcess([], 0, "123456\t/data\n", "")])
    assert d.volume_size_bytes(1) == 123456
    cmd = calls[0]
    assert cmd[0:2] == ["docker", "run"]
    assert "--entrypoint" in cmd and "du" in cmd
    assert f"vol-{RUN}-c1:/data:ro" in cmd
    assert cmd[-1] == "/data"
    assert TAILSCALE_IMAGE in cmd
    # No host path (os.walk on /var/lib/docker/...) may appear in the command.
    assert not any("/var/lib/docker" in part or "os.walk" in part for part in cmd)


def test_volume_size_bytes_negative_on_garbage():
    d, _ = _docker([subprocess.CompletedProcess([], 0, "not a number\n", "")])
    assert d.volume_size_bytes(1) == -1


def test_volume_size_bytes_negative_on_failed_sidecar():
    d, _ = _docker([subprocess.CompletedProcess([], 1, "", "no such volume")])
    assert d.volume_size_bytes(1) == -1
