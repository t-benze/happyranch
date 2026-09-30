from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from scripts import jenkins_mac_integration as job


ROOT = Path(__file__).resolve().parents[2]
JENKINSFILE = ROOT / "ci" / "jenkins" / "mac-integration" / "Jenkinsfile"


def _fake_container(tmp_path: Path) -> tuple[Path, Path]:
    executable = tmp_path / "container"
    log = tmp_path / "container-argv.jsonl"
    executable.write_text(
        """#!/usr/bin/env python3
import json
import os
import pathlib
import sys

args = sys.argv[1:]
with open(os.environ["FAKE_CONTAINER_LOG"], "a", encoding="utf-8") as stream:
    stream.write(json.dumps(args) + "\\n")
if args == ["--version"]:
    print(os.environ.get("FAKE_CONTAINER_VERSION", "container CLI version 1.5.0 (build: release, commit: d265d66)"))
    raise SystemExit(0)
if args[:2] == ["system", "status"]:
    print("FIELD VALUE")
    started = pathlib.Path(os.environ["FAKE_STARTED_MARKER"]).exists()
    print("status " + ("running" if started else os.environ.get("FAKE_CONTAINER_STATUS", "running")))
    raise SystemExit(0)
if args[:4] == ["system", "kernel", "set", "--recommended"]:
    pathlib.Path(os.environ["FAKE_KERNEL_LINK"]).touch()
    raise SystemExit(0)
if args[:2] == ["system", "start"]:
    pathlib.Path(os.environ["FAKE_STARTED_MARKER"]).touch()
    raise SystemExit(0)
if args[:2] == ["image", "inspect"]:
    print('{"reference":"pinned"}')
    raise SystemExit(0)
if args[:2] == ["rm", "-f"]:
    raise SystemExit(int(os.environ.get("FAKE_RM_STATUS", "0")))
if args == ["ls", "-a"]:
    print(os.environ.get("FAKE_CONTAINER_LIST", "ID  IMAGE  OS  ARCH  STATE  IP  CPUS  MEMORY  STARTED"))
    raise SystemExit(int(os.environ.get("FAKE_LS_STATUS", "0")))
if args[:1] == ["run"]:
    raise SystemExit(int(os.environ.get("FAKE_RUN_STATUS", "0")))
raise SystemExit(64)
""",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    return executable, log


def _fake_env(log: Path, kernel_link: Path, **overrides: str) -> dict[str, str]:
    env = {
        **os.environ,
        "FAKE_CONTAINER_LOG": str(log),
        "FAKE_KERNEL_LINK": str(kernel_link),
        "FAKE_STARTED_MARKER": str(log.with_suffix(".started")),
    }
    env.update(overrides)
    return env


@pytest.mark.parametrize(
    "value",
    ["", "abc", "g" * 40, "a" * 39, "a" * 41, "a" * 40 + "; touch nope"],
)
def test_source_sha_rejects_every_non_exact_hex_value(value: str) -> None:
    with pytest.raises(job.JobError, match="SOURCE_SHA"):
        job.validate_source_sha(value)


def test_source_sha_accepts_and_normalizes_exact_hex() -> None:
    assert job.validate_source_sha("A1" * 20) == "a1" * 20


def test_checkout_commands_fetch_exact_public_commit_detached(tmp_path: Path) -> None:
    source = tmp_path / "source"
    sha = "ab" * 20

    commands = job.build_checkout_commands(source, sha)

    assert commands == [
        ["git", "init", str(source)],
        ["git", "-C", str(source), "remote", "add", "origin", job.SOURCE_REPOSITORY],
        [
            "git",
            "-C",
            str(source),
            "fetch",
            "--no-tags",
            "--depth=1",
            "origin",
            sha,
        ],
        ["git", "-C", str(source), "checkout", "--detach", "FETCH_HEAD"],
    ]


def test_container_argv_has_only_two_host_mounts_and_no_forbidden_mode(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    artifacts = tmp_path / "artifacts"
    source.mkdir()
    artifacts.mkdir()

    argv = job.build_container_argv(
        source=source,
        artifacts=artifacts,
        container_name="happyranch-integration-42",
        source_sha="12" * 20,
        node_name="mac-mini",
        build_url="http://jenkins.example/job/42/",
    )

    mounts = [argv[index + 1] for index, value in enumerate(argv) if value == "--mount"]
    assert mounts == [
        f"type=bind,source={source.resolve()},target=/workspace/src,readonly",
        f"type=bind,source={artifacts.resolve()},target=/workspace/artifacts",
    ]
    assert argv[:3] == [job.CONTAINER_CLI, "run", "--rm"]
    assert ["--arch", "arm64"] == argv[argv.index("--arch") : argv.index("--arch") + 2]
    assert job.IMAGE_REFERENCE in argv
    assert "--privileged" not in argv
    assert "--cap-add" not in argv
    assert "--pid" not in argv
    assert "--network" not in argv
    assert "host" not in argv
    inherited_env = [
        argv[index + 1] for index, value in enumerate(argv) if value == "--env"
    ]
    assert "HOME=/tmp/happyranch-home" in inherited_env
    assert not any(value == "HOME" or value == f"HOME={Path.home()}" for value in inherited_env)
    payload = argv[-1]
    assert f"uv=={job.UV_VERSION}" in payload
    assert "apt-get install -y --no-install-recommends bash curl" in payload
    assert "uv sync --frozen" in payload
    assert "scripts/run_bounded_output.py" in payload
    assert "uv run pytest tests/ -v -m integration" in payload
    assert "scripts/nightly_integration_summary.py" in payload
    assert "/proc/self/mountinfo" in payload
    assert 'replace("\\\\040", " ")' in payload


def test_fake_container_proves_version_kernel_and_start_argv(tmp_path: Path) -> None:
    executable, log = _fake_container(tmp_path)
    kernel_link = tmp_path / "kernels" / "default.kernel-arm64"
    kernel_link.parent.mkdir()
    runner = job.CommandRunner(
        executable=str(executable),
        env=_fake_env(
            log,
            kernel_link,
            FAKE_CONTAINER_STATUS="stopped",
        ),
    )

    version = job.validate_container_version(runner)
    job.ensure_runtime_ready(runner, kernel_link=kernel_link)

    assert version.startswith("container CLI version 1.5.0 ")
    calls = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert ["system", "kernel", "set", "--recommended"] in calls
    assert [
        "system",
        "start",
        "--disable-kernel-install",
        "--timeout",
        "120",
    ] in calls
    assert not any("--debug" in call for call in calls)
    assert kernel_link.exists()


def test_fake_container_rejects_wrong_cli_version_before_readiness(tmp_path: Path) -> None:
    executable, log = _fake_container(tmp_path)
    kernel_link = tmp_path / "kernel"
    runner = job.CommandRunner(
        executable=str(executable),
        env=_fake_env(
            log,
            kernel_link,
            FAKE_CONTAINER_VERSION="container CLI version 1.4.1 (build: release)",
        ),
    )

    with pytest.raises(job.JobError, match="1.5.0 is required"):
        job.validate_container_version(runner)

    calls = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert calls == [["--version"]]


def test_runtime_ready_skips_installed_kernel_and_running_system(tmp_path: Path) -> None:
    executable, log = _fake_container(tmp_path)
    kernel_link = tmp_path / "default.kernel-arm64"
    kernel_link.touch()
    runner = job.CommandRunner(
        executable=str(executable),
        env=_fake_env(log, kernel_link),
    )

    job.ensure_runtime_ready(runner, kernel_link=kernel_link)

    calls = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert calls == [["system", "status"], ["system", "status"]]


@pytest.mark.parametrize(
    ("listing", "expected"),
    [
        ("ID  IMAGE  OS  ARCH  STATE  IP  CPUS  MEMORY  STARTED\n", set()),
        (
            "ID  IMAGE  OS  ARCH  STATE  IP  CPUS  MEMORY  STARTED\n"
            "other python linux arm64 stopped - 4 1G now\n",
            {"other"},
        ),
    ],
)
def test_parse_container_ls_all_table(listing: str, expected: set[str]) -> None:
    assert job.parse_container_ids(listing) == expected


def test_parse_container_ls_all_rejects_missing_header() -> None:
    with pytest.raises(job.JobError, match="malformed table"):
        job.parse_container_ids("job-42 python linux arm64 running\n")


def test_cleanup_fake_container_records_absence(tmp_path: Path) -> None:
    executable, log = _fake_container(tmp_path)
    kernel_link = tmp_path / "kernel"
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    runner = job.CommandRunner(
        executable=str(executable),
        env=_fake_env(log, kernel_link),
    )

    assert job.cleanup_container(runner, "job-42", artifacts=artifacts) is True
    evidence = (artifacts / "cleanup.txt").read_text(encoding="utf-8")
    assert "cleanup_verified_absent=true" in evidence
    assert "rm_status=0" in evidence


def test_cleanup_fake_container_rejects_residual_named_row(tmp_path: Path) -> None:
    executable, log = _fake_container(tmp_path)
    kernel_link = tmp_path / "kernel"
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    listing = (
        "ID  IMAGE  OS  ARCH  STATE  IP  CPUS  MEMORY  STARTED\n"
        "job-42 python linux arm64 running 1.2.3.4 4 1G now"
    )
    runner = job.CommandRunner(
        executable=str(executable),
        env=_fake_env(log, kernel_link, FAKE_CONTAINER_LIST=listing),
    )

    assert job.cleanup_container(runner, "job-42", artifacts=artifacts) is False
    assert "cleanup_verified_absent=false" in (
        artifacts / "cleanup.txt"
    ).read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("workload_status", "cleanup_ok", "expected"),
    [(0, True, 0), (0, False, 90), (7, True, 7), (7, False, 7)],
)
def test_final_exit_preserves_workload_and_distinguishes_cleanup_failure(
    workload_status: int,
    cleanup_ok: bool,
    expected: int,
) -> None:
    assert job.final_exit_code(workload_status, cleanup_ok=cleanup_ok) == expected


def test_fake_container_run_returns_real_workload_status(tmp_path: Path) -> None:
    executable, log = _fake_container(tmp_path)
    kernel_link = tmp_path / "kernel"
    runner = job.CommandRunner(
        executable=str(executable),
        env=_fake_env(log, kernel_link, FAKE_RUN_STATUS="23"),
    )

    result = runner.run(["run", "--rm", "pinned-image"], timeout_seconds=5)

    assert result.returncode == 23


def test_jenkinsfile_is_bounded_parameterized_and_archives_evidence() -> None:
    pipeline = JENKINSFILE.read_text(encoding="utf-8")

    assert "agent { label 'mac-mini' }" in pipeline
    assert "string(name: 'SOURCE_SHA'" in pipeline
    assert pipeline.count("string(name:") == 1
    assert "timeout(time: 55, unit: 'MINUTES')" in pipeline
    assert "disableConcurrentBuilds()" in pipeline
    assert "skipDefaultCheckout(true)" in pipeline
    assert "junit allowEmptyResults: true, testResults: 'artifacts/integration.xml'" in pipeline
    assert "archiveArtifacts artifacts: 'artifacts/**'" in pipeline
    assert "scripts/jenkins_mac_integration.py" in pipeline
    assert "cron(" not in pipeline
