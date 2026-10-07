"""Observe the repository's default retention through real, isolated pytest runs."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


CONFIG = Path(__file__).resolve().parents[2] / "pyproject.toml"

# Receipts live outside both the child suite and its allocated pytest base.
CHILD_SUITE = '''
import json
import os
from pathlib import Path
import tempfile
import pytest

def record(kind, path):
    with open(os.environ["RETENTION_RECORD"], "a") as receipt:
        receipt.write(json.dumps({"kind": kind, "path": str(path)}) + "\\n")

def test_config(pytestconfig, tmp_path_factory):
    Path(os.environ["RETENTION_CONFIG"]).write_text(json.dumps({
        "config": str(pytestconfig.inipath),
        "policy": pytestconfig.getini("tmp_path_retention_policy"),
        "count": pytestconfig.getini("tmp_path_retention_count"),
        "base": str(tmp_path_factory.getbasetemp()),
    }))

@pytest.mark.parametrize("fixture", ["tmp_path", "tmpdir"])
def test_pass(request, fixture):
    path = Path(str(request.getfixturevalue(fixture)))
    (path / "diagnostic.txt").write_text("passing diagnostic")
    record("pass", path)

def test_factories(tmp_path_factory, tmpdir_factory):
    for factory in (tmp_path_factory, tmpdir_factory):
        path = Path(str(factory.mktemp("factory")))
        (path / "diagnostic.txt").write_text("factory diagnostic")
        record("factory", path)
    with tempfile.NamedTemporaryFile(delete=False) as scratch:
        scratch.write(b"arbitrary tempfile")
        record("outside", scratch.name)

@pytest.mark.parametrize("fixture", ["tmp_path", "tmpdir"])
def test_fail(request, fixture):
    path = Path(str(request.getfixturevalue(fixture)))
    (path / "diagnostic.txt").write_text("failing diagnostic")
    record("fail", path)
    assert False, "ordinary failed call"
'''


def _run_child(root: Path, *, failing: bool, explicit_base: bool):
    for name in ("suite", "home", "config", "tmp", "pytest-root", "daemon"):
        (root / name).mkdir()
    suite = root / "suite" / "test_child.py"
    suite.write_text(CHILD_SUITE)
    env = {
        "HOME": str(root / "home"),
        "XDG_CONFIG_HOME": str(root / "config"),
        "TMPDIR": str(root / "tmp"),
        "TMP": str(root / "tmp"),
        "TEMP": str(root / "tmp"),
        "PYTEST_DEBUG_TEMPROOT": str(root / "pytest-root"),
        "HAPPYRANCH_DAEMON_HOME": str(root / "daemon"),
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        "PYTHONNOUSERSITE": "1",
        "PATH": os.pathsep.join((str(Path(sys.executable).parent), "/usr/bin", "/bin")),
        "RETENTION_RECORD": str(root / "paths.jsonl"),
        "RETENTION_CONFIG": str(root / "config.json"),
    }
    command = [
        sys.executable, "-m", "pytest", "-p", "pytest_asyncio.plugin",
        "-c", str(CONFIG), "--confcutdir", str(root / "suite"),
        str(suite), "-q",
    ]
    if not failing:
        command += ["-k", "not test_fail"]
    if explicit_base:
        base = root / "explicit-base"
        base.mkdir()
        (base / "pre-existing.txt").write_text("dedicated disposable base")
        command += ["--basetemp", str(base)]
    result = subprocess.run(
        command, cwd=root / "suite", env=env, capture_output=True, text=True,
        timeout=30,
    )
    assert result.returncode == (1 if failing else 0), result.stdout + result.stderr
    config = json.loads((root / "config.json").read_text())
    paths = [json.loads(line) for line in (root / "paths.jsonl").read_text().splitlines()]
    return config, paths


def test_repository_pytest_resolves_failed_retention(tmp_path: Path):
    config, _ = _run_child(tmp_path, failing=False, explicit_base=False)
    assert Path(config["config"]).resolve() == CONFIG
    assert config["policy"] == "failed"
    assert int(config["count"]) == 3


@pytest.mark.parametrize("explicit_base", [False, True], ids=["default-base", "explicit-base"])
@pytest.mark.parametrize("failing", [False, True], ids=["all-pass", "mixed-failure"])
def test_direct_pytest_fixture_and_session_retention(
    tmp_path: Path, failing: bool, explicit_base: bool,
):
    config, paths = _run_child(tmp_path, failing=failing, explicit_base=explicit_base)
    base = Path(config["base"])
    assert len([row for row in paths if row["kind"] == "pass"]) == 2
    assert len([row for row in paths if row["kind"] == "fail"]) == (2 if failing else 0)
    assert len([row for row in paths if row["kind"] == "factory"]) == 2
    for row in paths:
        path = Path(row["path"])
        if row["kind"] == "pass":
            assert not path.exists(), f"passing fixture scratch retained: {path}"
        elif row["kind"] == "fail":
            assert (path / "diagnostic.txt").read_text() == "failing diagnostic"
        elif row["kind"] == "factory":
            assert path.is_relative_to(base)
            if failing or explicit_base:
                assert (path / "diagnostic.txt").read_text() == "factory diagnostic"
            else:
                assert not path.exists(), f"successful session factory scratch retained: {path}"
        else:
            assert not path.is_relative_to(base)
            assert path.read_bytes() == b"arbitrary tempfile"
    assert base.exists() == (failing or explicit_base)
    if explicit_base:
        assert base == tmp_path / "explicit-base"
        assert not (base / "pre-existing.txt").exists()
