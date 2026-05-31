"""The `happyranch scripts <verb>` shim warns + dispatches to jobs."""
from __future__ import annotations

import subprocess

import pytest

from tests.integration.conftest import DEFAULT_TEST_SLUG


pytestmark = pytest.mark.integration


def test_scripts_alias_warns_and_dispatches(live_daemon, runtime):
    """`happyranch scripts list` prints the deprecation banner and reaches the daemon."""
    r = subprocess.run(
        ["uv", "run", "happyranch", "scripts", "list", "--org", DEFAULT_TEST_SLUG],
        capture_output=True,
        text=True,
        check=False,
    )
    assert r.returncode == 0, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    assert "[deprecated]" in r.stderr, r.stderr
    assert "renamed to `happyranch jobs`" in r.stderr, r.stderr
    # The shim's output should mention either the empty-result placeholder or
    # the table header. Either confirms we reached `cmd_jobs_list` and got a
    # successful round-trip with the daemon.
    out = r.stdout
    assert (
        "(no script requests match)" in out
        or "(no jobs match)" in out
        or "JOB-" in out
        or "ID" in out
    ), f"unexpected stdout: {out!r}"
