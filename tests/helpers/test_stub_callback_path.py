"""Safe shell wiring units: explicit plans never call a daemon or provider."""
from __future__ import annotations

import json
from pathlib import Path
import shlex
import subprocess
import sys

import pytest

from tests.helpers.deterministic_plan import DeterministicPlan
from tests.helpers.integration_parent import build_environment


@pytest.mark.parametrize("provider", ["claude", "codex", "opencode"])
def test_registered_stub_restores_bound_callback_after_uv_path_prefix(tmp_path: Path, provider: str) -> None:
    source = Path(__file__).resolve().parents[2]
    parent = tmp_path / "parent"
    parent.mkdir()
    env = build_environment(parent, source, Path(sys.executable), Path("/usr/bin/true"), "a" * 40)
    # uv run prepends this directory on actual daemon startup. It contains
    # the project-installed CLI, which must not shadow our bound callback.
    assert (source / ".venv/bin/happyranch").is_file()
    env["PATH"] = str(source / ".venv/bin") + ":" + env["PATH"]
    witness = tmp_path / "witness"
    witness.mkdir(mode=0o700)
    env["HAPPYRANCH_TEST_WITNESS_DIR"] = str(witness)
    marker = tmp_path / "resolved-callback"
    plan = DeterministicPlan(tmp_path / "plan.sh")
    # This plan observes command resolution only. No task/session/callback,
    # socket, daemon, package tool, container or provider is invoked.
    plan.write_text("#!/bin/sh\ncommand -v happyranch > " + shlex.quote(str(marker)) + "\n")
    env[f"FAKE_{provider.upper()}_PLAN"] = str(plan)
    result = subprocess.run(
        [str(parent / "bin" / provider)], input="opaque no-session input",
        env=env, cwd=tmp_path, text=True, capture_output=True, timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert marker.read_text().strip() == str(parent / "bin/happyranch")
    rows = [json.loads(line) for line in (witness / "identities.jsonl").read_text().splitlines()]
    assert len(rows) == 1
    assert rows[0]["kind"] == "stub" and rows[0]["provider"] == provider
    assert rows[0]["source_sha"] == "a" * 40
    assert rows[0]["argc"] == 0 and rows[0]["flags"] == []
