from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

import pytest

from scripts import jenkins_mac_integration as job


ROOT = Path(__file__).resolve().parents[2]
JENKINSFILE = ROOT / "ci" / "jenkins" / "mac-integration" / "Jenkinsfile"


def _run_emitted_uv_check(
    tmp_path: Path, reported_version: str
) -> subprocess.CompletedProcess[str]:
    lines = job._INNER_SCRIPT.splitlines()
    observed_index = lines.index('  observed_uv="$(uv --version)"')
    start_index = observed_index - 1
    assert lines[start_index] == 'if [ "$workload_status" -eq 0 ]; then'
    end_index = lines.index("fi", observed_index)
    fragment = "\n".join(lines[start_index : end_index + 1])

    identity = tmp_path / "identity.txt"
    assert fragment.count("/workspace/artifacts/identity.txt") == 1
    fragment = fragment.replace(
        "/workspace/artifacts/identity.txt", str(identity)
    )

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_uv = fake_bin / "uv"
    fake_uv.write_text(
        "#!/bin/sh\nprintf '%s\\n' \"$FAKE_UV_VERSION\"\n",
        encoding="utf-8",
    )
    fake_uv.chmod(0o755)
    shell = shutil.which("sh")
    assert shell is not None
    return subprocess.run(
        [shell, "-c", f'workload_status=0\n{fragment}\nexit "$workload_status"\n'],
        check=False,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "FAKE_UV_VERSION": reported_version,
            "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
        },
    )


@pytest.mark.parametrize(
    "reported_version",
    [
        "uv 0.12.21",
        "uv 0.12.21 (aarch64-unknown-linux-gnu)",
        "uv 0.12.21 (x86_64-unknown-linux-gnu)",
    ],
)
def test_emitted_uv_check_accepts_exact_version_with_optional_target_triple(
    tmp_path: Path, reported_version: str
) -> None:
    result = _run_emitted_uv_check(tmp_path, reported_version)

    assert result.returncode == 0
    assert result.stderr == ""
    assert (tmp_path / "identity.txt").read_text(encoding="utf-8") == (
        f"uv_version={reported_version}\n"
    )


@pytest.mark.parametrize(
    "reported_version",
    [
        "uv 0.12.210",
        "uv 0.12.2",
        "uv 0.12.210 (aarch64-unknown-linux-gnu)",
        "uv 0.12.2 (aarch64-unknown-linux-gnu)",
        "uv 0.12.21 ()",
        "uv 0.12.21 (a b)",
        "uv 0.12.21 (triple) extra",
        "uv 0.12.21-foo",
        "",
    ],
)
def test_emitted_uv_check_rejects_wrong_or_malformed_version(
    tmp_path: Path, reported_version: str
) -> None:
    result = _run_emitted_uv_check(tmp_path, reported_version)

    assert result.returncode == 82
    assert result.stderr == f"unexpected uv version: {reported_version}\n"
    assert not (tmp_path / "identity.txt").exists()


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
    started = pathlib.Path(os.environ["FAKE_STARTED_MARKER"]).exists()
    status = "running" if started else os.environ.get("FAKE_CONTAINER_STATUS", "running")
    if status == "running":
        print("FIELD VALUE")
        print("status running")
        raise SystemExit(0)
    if status == "stopped":
        print(os.environ.get("FAKE_CONTAINER_STOPPED_OUTPUT", "apiserver is not running"))
        raise SystemExit(1)
    print(os.environ.get("FAKE_CONTAINER_STATUS_OUTPUT", "unexpected status output"))
    raise SystemExit(int(os.environ.get("FAKE_CONTAINER_STATUS_EXIT", "1")))
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
    assert "host_backed == expected" in payload
    assert "expected.issubset(host_backed)" not in payload


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
    "stopped_output",
    [
        "apiserver is not running",
        "apiserver is not running and not registered with launchd",
    ],
)
def test_runtime_ready_stopped_exit_one_starts_then_reports_running(
    tmp_path: Path, stopped_output: str
) -> None:
    executable, log = _fake_container(tmp_path)
    kernel_link = tmp_path / "default.kernel-arm64"
    kernel_link.touch()
    runner = job.CommandRunner(
        executable=str(executable),
        env=_fake_env(
            log,
            kernel_link,
            FAKE_CONTAINER_STATUS="stopped",
            FAKE_CONTAINER_STOPPED_OUTPUT=stopped_output,
        ),
    )

    job.ensure_runtime_ready(runner, kernel_link=kernel_link)

    calls = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert calls == [
        ["system", "status"],
        ["system", "start", "--disable-kernel-install", "--timeout", "120"],
        ["system", "status"],
    ]


def test_runtime_ready_rejects_unrecognized_exit_one_status(tmp_path: Path) -> None:
    executable, log = _fake_container(tmp_path)
    kernel_link = tmp_path / "default.kernel-arm64"
    kernel_link.touch()
    runner = job.CommandRunner(
        executable=str(executable),
        env=_fake_env(log, kernel_link, FAKE_CONTAINER_STATUS="unexpected"),
    )

    with pytest.raises(job.JobError, match="unexpected exit-1 output"):
        job.ensure_runtime_ready(runner, kernel_link=kernel_link)

    calls = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert calls == [["system", "status"]]


def test_runtime_ready_rejects_unparseable_success_status(tmp_path: Path) -> None:
    executable, log = _fake_container(tmp_path)
    kernel_link = tmp_path / "default.kernel-arm64"
    kernel_link.touch()
    runner = job.CommandRunner(
        executable=str(executable),
        env=_fake_env(
            log,
            kernel_link,
            FAKE_CONTAINER_STATUS="unexpected",
            FAKE_CONTAINER_STATUS_EXIT="0",
        ),
    )

    with pytest.raises(job.JobError, match="exact running status"):
        job.ensure_runtime_ready(runner, kernel_link=kernel_link)

    calls = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert calls == [["system", "status"]]


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


def test_container_name_is_deterministic_and_build_scoped() -> None:
    assert job.build_container_name("ab" * 20, "123") == (
        "happyranch-integration-abababababab-123"
    )

    with pytest.raises(job.JobError, match="BUILD_NUMBER"):
        job.build_container_name("ab" * 20, "123; container rm -f anything")


def test_cleanup_only_returns_distinct_90_and_records_failed_absence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable, log = _fake_container(tmp_path)
    kernel_link = tmp_path / "kernel"
    runner = job.CommandRunner(
        executable=str(executable),
        env=_fake_env(log, kernel_link, FAKE_LS_STATUS="1"),
    )
    monkeypatch.setattr(job, "CommandRunner", lambda: runner)
    args = argparse.Namespace(
        workspace=tmp_path,
        source_sha="ab" * 20,
        build_number="123",
    )

    assert job._run_cleanup_only(args) == 90
    evidence = (tmp_path / "artifacts" / "post-cleanup.txt").read_text(
        encoding="utf-8"
    )
    assert "container_name=happyranch-integration-abababababab-123" in evidence
    assert "cleanup_verified_absent=false" in evidence


def test_jenkinsfile_is_bounded_parameterized_and_archives_evidence() -> None:
    pipeline = JENKINSFILE.read_text(encoding="utf-8")

    assert "agent { label 'mac-mini' }" in pipeline
    assert "string(name: 'SOURCE_SHA'" in pipeline
    assert pipeline.count("string(name:") == 1
    timeout_match = re.search(
        r"timeout\(time: (\d+), unit: 'MINUTES'\)", pipeline
    )
    assert timeout_match is not None
    outer_timeout_seconds = int(timeout_match.group(1)) * 60
    assert outer_timeout_seconds > (
        job.MAX_RUNNER_WAIT_SECONDS + job.POST_CLEANUP_BUDGET_SECONDS
    )
    assert "disableConcurrentBuilds()" in pipeline
    assert "skipDefaultCheckout(true)" in pipeline
    assert "junit allowEmptyResults: true, testResults: 'artifacts/integration.xml'" in pipeline
    assert "archiveArtifacts artifacts: 'artifacts/**'" in pipeline
    assert pipeline.count(
        "exec python3 definition/scripts/jenkins_mac_integration.py"
    ) == 2
    assert '--build-number "$BUILD_NUMBER"' in pipeline
    assert "--cleanup-only" in pipeline
    assert "post-cleanup.txt" in pipeline
    assert "returnStatus: true" in pipeline
    assert "cron(" not in pipeline
