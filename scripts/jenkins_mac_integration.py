#!/usr/bin/env python3
"""Run HappyRanch integration tests in an isolated Apple container VM."""

from __future__ import annotations

import argparse
import os
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence


SOURCE_REPOSITORY = "https://github.com/t-benze/happyranch"
CONTAINER_CLI = "/usr/local/bin/container"
CONTAINER_VERSION = "1.5.0"
IMAGE_MANIFEST_DIGEST = (
    "sha256:950206c37262dd86c55659797f6ee418fee30535072f65a82ed470d985f5cda5"
)
IMAGE_REFERENCE = f"docker.io/library/python:3.12-slim@{IMAGE_MANIFEST_DIGEST}"
UV_VERSION = "0.12.21"
CHECKOUT_COMMAND_COUNT = 4
CHECKOUT_COMMAND_TIMEOUT_SECONDS = 300
CHECKOUT_VERIFY_TIMEOUT_SECONDS = 30
CONTAINER_VERSION_TIMEOUT_SECONDS = 15
KERNEL_TIMEOUT_SECONDS = 480
SYSTEM_STATUS_TIMEOUT_SECONDS = 30
SYSTEM_START_TIMEOUT_SECONDS = 150
CONTAINER_RUN_TIMEOUT_SECONDS = 2520
IMAGE_INSPECT_TIMEOUT_SECONDS = 30
CLEANUP_REMOVE_TIMEOUT_SECONDS = 60
CLEANUP_LIST_TIMEOUT_SECONDS = 30
POST_CLEANUP_BUDGET_SECONDS = (
    CLEANUP_REMOVE_TIMEOUT_SECONDS + CLEANUP_LIST_TIMEOUT_SECONDS
)
MAX_RUNNER_WAIT_SECONDS = (
    CHECKOUT_COMMAND_COUNT * CHECKOUT_COMMAND_TIMEOUT_SECONDS
    + CHECKOUT_VERIFY_TIMEOUT_SECONDS
    + CONTAINER_VERSION_TIMEOUT_SECONDS
    + KERNEL_TIMEOUT_SECONDS
    + 2 * SYSTEM_STATUS_TIMEOUT_SECONDS
    + SYSTEM_START_TIMEOUT_SECONDS
    + CONTAINER_RUN_TIMEOUT_SECONDS
    + IMAGE_INSPECT_TIMEOUT_SECONDS
    + CLEANUP_REMOVE_TIMEOUT_SECONDS
    + CLEANUP_LIST_TIMEOUT_SECONDS
)
CLEANUP_FAILURE_EXIT = 90
EVIDENCE_FAILURE_EXIT = 91
JOB_FAILURE_EXIT = 2

_SOURCE_SHA = re.compile(r"[0-9a-fA-F]{40}\Z")
_BUILD_NUMBER = re.compile(r"[0-9]+\Z")
_VERSION = re.compile(r"\Acontainer CLI version ([0-9]+\.[0-9]+\.[0-9]+)\b")
_STOPPED_SYSTEM_STATUS_OUTPUTS = frozenset(
    {
        "apiserver is not running",
        "apiserver is not running and not registered with launchd",
    }
)


class JobError(RuntimeError):
    """A fail-closed Jenkins workload validation or execution error."""


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


class CommandRunner:
    """Bounded adapter for the Apple container CLI."""

    def __init__(
        self,
        *,
        executable: str = CONTAINER_CLI,
        env: Mapping[str, str] | None = None,
    ) -> None:
        self.executable = executable
        self.env = dict(env) if env is not None else None

    def run(
        self,
        arguments: Sequence[str],
        *,
        timeout_seconds: int,
        capture: bool = True,
    ) -> CommandResult:
        completed = subprocess.run(
            [self.executable, *arguments],
            check=False,
            env=self.env,
            timeout=timeout_seconds,
            stdout=subprocess.PIPE if capture else None,
            stderr=subprocess.PIPE if capture else None,
            text=True,
        )
        return CommandResult(
            completed.returncode,
            completed.stdout or "",
            completed.stderr or "",
        )


def validate_source_sha(value: str) -> str:
    if _SOURCE_SHA.fullmatch(value) is None:
        raise JobError("SOURCE_SHA must be exactly 40 hexadecimal characters")
    return value.lower()


def build_container_name(source_sha: str, build_number: str) -> str:
    normalized_sha = validate_source_sha(source_sha)
    if _BUILD_NUMBER.fullmatch(build_number) is None:
        raise JobError("BUILD_NUMBER must contain only decimal digits")
    return f"happyranch-integration-{normalized_sha[:12]}-{build_number}"


def build_checkout_commands(source: Path, source_sha: str) -> list[list[str]]:
    return [
        ["git", "init", str(source)],
        ["git", "-C", str(source), "remote", "add", "origin", SOURCE_REPOSITORY],
        [
            "git",
            "-C",
            str(source),
            "fetch",
            "--no-tags",
            "--depth=1",
            "origin",
            source_sha,
        ],
        ["git", "-C", str(source), "checkout", "--detach", "FETCH_HEAD"],
    ]


def _run_checked(command: Sequence[str], *, timeout_seconds: int) -> str:
    try:
        completed = subprocess.run(
            command,
            check=False,
            timeout=timeout_seconds,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except subprocess.TimeoutExpired as exc:
        raise JobError(f"command timed out: {shlex.join(command)}") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()[-500:]
        raise JobError(
            f"command failed ({completed.returncode}): {shlex.join(command)}: {detail}"
        )
    return completed.stdout


def prepare_source(source: Path, source_sha: str) -> None:
    if source.exists():
        raise JobError(f"refusing pre-existing source directory: {source}")
    for command in build_checkout_commands(source, source_sha):
        _run_checked(command, timeout_seconds=CHECKOUT_COMMAND_TIMEOUT_SECONDS)
    actual = _run_checked(
        ["git", "-C", str(source), "rev-parse", "HEAD"],
        timeout_seconds=CHECKOUT_VERIFY_TIMEOUT_SECONDS,
    ).strip()
    if actual != source_sha:
        raise JobError(f"detached checkout mismatch: expected {source_sha}, observed {actual}")


def validate_container_version(runner: CommandRunner) -> str:
    result = runner.run(["--version"], timeout_seconds=CONTAINER_VERSION_TIMEOUT_SECONDS)
    if result.returncode != 0:
        raise JobError(f"container --version failed with status {result.returncode}")
    output = result.stdout.strip()
    match = _VERSION.match(output)
    if match is None or match.group(1) != CONTAINER_VERSION:
        raise JobError(
            f"Apple container {CONTAINER_VERSION} is required; observed {output!r}"
        )
    return output


def _system_is_running(runner: CommandRunner) -> bool:
    result = runner.run(
        ["system", "status"], timeout_seconds=SYSTEM_STATUS_TIMEOUT_SECONDS
    )
    output = result.stdout.strip()
    if result.returncode == 1:
        if output in _STOPPED_SYSTEM_STATUS_OUTPUTS:
            return False
        raise JobError(
            "container system status returned unexpected exit-1 output: "
            f"{output!r}"
        )
    if result.returncode != 0:
        raise JobError(f"container system status failed with status {result.returncode}")
    status: str | None = None
    for line in result.stdout.splitlines():
        fields = line.split()
        if fields and fields[0].lower() == "status" and len(fields) == 2:
            if status is not None:
                raise JobError("container system status contained duplicate status fields")
            status = fields[1].lower()
    if status != "running":
        raise JobError("container system status did not contain exact running status")
    return True


def ensure_runtime_ready(runner: CommandRunner, *, kernel_link: Path) -> None:
    if not kernel_link.exists():
        try:
            result = runner.run(
                ["system", "kernel", "set", "--recommended"],
                timeout_seconds=KERNEL_TIMEOUT_SECONDS,
                capture=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise JobError("recommended kernel installation exceeded 480 seconds") from exc
        if result.returncode != 0:
            raise JobError(
                "container system kernel set --recommended failed with status "
                f"{result.returncode}"
            )
        if not kernel_link.exists():
            raise JobError("recommended kernel command succeeded without installing the link")

    if not _system_is_running(runner):
        try:
            result = runner.run(
                [
                    "system",
                    "start",
                    "--disable-kernel-install",
                    "--timeout",
                    "120",
                ],
                timeout_seconds=SYSTEM_START_TIMEOUT_SECONDS,
                capture=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise JobError("container system start exceeded 150 seconds") from exc
        if result.returncode != 0:
            raise JobError(f"container system start failed with status {result.returncode}")
    if not _system_is_running(runner):
        raise JobError("container system is not running after bounded readiness")


_INNER_SCRIPT = f"""\
set -u
umask 077
mkdir -p "$HOME" "$UV_PROJECT_ENVIRONMENT" "$UV_CACHE_DIR" "$XDG_CACHE_HOME"
python - <<'PY'
from pathlib import Path
import sys

raw = Path("/proc/self/mountinfo").read_text(encoding="utf-8")
expected = {{"/workspace/src", "/workspace/artifacts"}}
host_backed = set()
for line in raw.splitlines():
    fields = line.split()
    try:
        separator = fields.index("-")
    except ValueError:
        continue
    mount_point = fields[4].replace("\\\\040", " ")
    fs_type = fields[separator + 1]
    if fs_type == "virtiofs":
        host_backed.add(mount_point)

verdict = host_backed == expected and not any(
    path == "/Users" or path.startswith("/Users/") for path in host_backed
)
evidence = [
    f"expected_host_mounts={{sorted(expected)!r}}",
    f"observed_host_mounts={{sorted(host_backed)!r}}",
    f"host_mount_isolation={{str(verdict).lower()}}",
    "",
    raw,
]
Path("/workspace/artifacts/mount-evidence.txt").write_text(
    "\\n".join(evidence), encoding="utf-8"
)
if not verdict:
    sys.exit(81)
PY
workload_status=$?
if [ "$workload_status" -eq 0 ]; then
  apt-get update && apt-get install -y --no-install-recommends bash curl
  workload_status=$?
fi
if [ "$workload_status" -eq 0 ]; then
  dpkg-query -W -f='os_tool=${{Package}} ${{Version}}\\n' bash curl \\
    >> /workspace/artifacts/identity.txt
  python --version 2>&1 | sed 's/^/python_version=/' \\
    >> /workspace/artifacts/identity.txt
  python -m pip install --disable-pip-version-check --no-cache-dir "uv=={UV_VERSION}"
  workload_status=$?
fi
if [ "$workload_status" -eq 0 ]; then
  observed_uv="$(uv --version)"
  uv_version_ok=false
  case "$observed_uv" in
    "uv {UV_VERSION}")
      uv_version_ok=true
      ;;
    "uv {UV_VERSION} ("*")")
      uv_target_triple="${{observed_uv#* (}}"
      uv_target_triple="${{uv_target_triple%)}}"
      case "$uv_target_triple" in
        ""|*[\\ \\(\\)]*) ;;
        *) uv_version_ok=true ;;
      esac
      ;;
  esac
  if [ "$uv_version_ok" != true ]; then
    printf 'unexpected uv version: %s\\n' "$observed_uv" >&2
    workload_status=82
  else
    printf 'uv_version=%s\\n' "$observed_uv" >> /workspace/artifacts/identity.txt
  fi
fi
if [ "$workload_status" -eq 0 ]; then
  cd /workspace/src
  uv sync --frozen
  workload_status=$?
fi
if [ "$workload_status" -eq 0 ]; then
  python scripts/run_bounded_output.py \\
    --output /workspace/artifacts/integration.log \\
    --max-bytes 1048576 \\
    -- uv run pytest tests/ -v -m integration \\
      --basetemp=/tmp/happyranch-pytest \\
      -p no:cacheprovider \\
      --junitxml=/workspace/artifacts/integration.xml
  workload_status=$?
fi
python /workspace/src/scripts/nightly_integration_summary.py \\
  /workspace/artifacts/integration.xml \\
  --output /workspace/artifacts/integration-summary.md \\
  --head-sha "$SOURCE_SHA" \\
  --run-url "$JOB_BUILD_URL" \\
  --artifact-name "jenkins-mac-integration-$SOURCE_SHA"
summary_status=$?
if [ "$workload_status" -eq 0 ] && [ "$summary_status" -ne 0 ]; then
  exit "$summary_status"
fi
exit "$workload_status"
"""


def build_container_argv(
    *,
    source: Path,
    artifacts: Path,
    container_name: str,
    source_sha: str,
    node_name: str,
    build_url: str,
) -> list[str]:
    return [
        CONTAINER_CLI,
        "run",
        "--rm",
        "--arch",
        "arm64",
        "--name",
        container_name,
        "--workdir",
        "/workspace/src",
        "--env",
        "HOME=/tmp/happyranch-home",
        "--env",
        "UV_PROJECT_ENVIRONMENT=/tmp/happyranch-venv",
        "--env",
        "UV_CACHE_DIR=/tmp/uv-cache",
        "--env",
        "XDG_CACHE_HOME=/tmp/xdg-cache",
        "--env",
        "TMPDIR=/tmp",
        "--env",
        f"SOURCE_SHA={source_sha}",
        "--env",
        f"JOB_NODE_NAME={node_name}",
        "--env",
        f"JOB_BUILD_URL={build_url}",
        "--mount",
        f"type=bind,source={source.resolve()},target=/workspace/src,readonly",
        "--mount",
        f"type=bind,source={artifacts.resolve()},target=/workspace/artifacts",
        "--entrypoint",
        "/bin/sh",
        IMAGE_REFERENCE,
        "-c",
        _INNER_SCRIPT,
    ]


def parse_container_ids(output: str) -> set[str]:
    lines = [line for line in output.splitlines() if line.strip()]
    if not lines or lines[0].split()[:1] != ["ID"]:
        raise JobError("container ls -a returned a malformed table")
    identifiers: set[str] = set()
    for line in lines[1:]:
        fields = line.split()
        if not fields:
            raise JobError("container ls -a returned a malformed row")
        identifiers.add(fields[0])
    return identifiers


def cleanup_container(
    runner: CommandRunner,
    container_name: str,
    *,
    artifacts: Path,
    evidence_name: str = "cleanup.txt",
) -> bool:
    rm_status = -1
    ls_status = -1
    listing = ""
    error = ""
    try:
        rm_result = runner.run(
            ["rm", "-f", container_name],
            timeout_seconds=CLEANUP_REMOVE_TIMEOUT_SECONDS,
        )
        rm_status = rm_result.returncode
    except (OSError, subprocess.TimeoutExpired) as exc:
        error = f"remove_error={type(exc).__name__}: {exc}"
    try:
        ls_result = runner.run(
            ["ls", "-a"], timeout_seconds=CLEANUP_LIST_TIMEOUT_SECONDS
        )
        ls_status = ls_result.returncode
        listing = ls_result.stdout
        if ls_status != 0:
            verified = False
        else:
            verified = container_name not in parse_container_ids(listing)
    except (JobError, OSError, subprocess.TimeoutExpired) as exc:
        verified = False
        error = f"{error}\nlist_error={type(exc).__name__}: {exc}".strip()
    evidence = (
        f"container_name={container_name}\n"
        f"rm_status={rm_status}\n"
        f"ls_status={ls_status}\n"
        f"cleanup_verified_absent={str(verified).lower()}\n"
        f"{error}\n"
        "container_ls_all_begin\n"
        f"{listing}"
        "container_ls_all_end\n"
    )
    (artifacts / evidence_name).write_text(evidence, encoding="utf-8")
    return verified


def final_exit_code(workload_status: int, *, cleanup_ok: bool) -> int:
    if workload_status != 0:
        return workload_status
    if not cleanup_ok:
        return CLEANUP_FAILURE_EXIT
    return 0


def _identity_text(
    *,
    source_sha: str,
    definition_sha: str,
    container_version: str,
    node_name: str,
) -> str:
    return (
        f"source_repository={SOURCE_REPOSITORY}\n"
        f"source_sha={source_sha}\n"
        f"job_definition_sha={definition_sha}\n"
        f"image_reference={IMAGE_REFERENCE}\n"
        f"image_linux_arm64_manifest_digest={IMAGE_MANIFEST_DIGEST}\n"
        f"apple_container_version={container_version}\n"
        f"uv_pin={UV_VERSION}\n"
        f"node={node_name}\n"
    )


def _run_job(args: argparse.Namespace) -> int:
    workspace = args.workspace.resolve(strict=True)
    source = workspace / "source"
    artifacts = workspace / "artifacts"
    if artifacts.exists():
        raise JobError(f"refusing pre-existing artifact directory: {artifacts}")
    artifacts.mkdir(mode=0o700)

    try:
        source_sha = validate_source_sha(args.source_sha)
        definition_sha = validate_source_sha(args.definition_sha)
        prepare_source(source, source_sha)
        runner = CommandRunner()
        version = validate_container_version(runner)
        (artifacts / "identity.txt").write_text(
            _identity_text(
                source_sha=source_sha,
                definition_sha=definition_sha,
                container_version=version,
                node_name=args.node_name,
            ),
            encoding="utf-8",
        )
        kernel_link = (
            Path.home()
            / "Library"
            / "Application Support"
            / "com.apple.container"
            / "kernels"
            / "default.kernel-arm64"
        )
        ensure_runtime_ready(runner, kernel_link=kernel_link)
        container_name = build_container_name(source_sha, args.build_number)
        container_argv = build_container_argv(
            source=source,
            artifacts=artifacts,
            container_name=container_name,
            source_sha=source_sha,
            node_name=args.node_name,
            build_url=args.build_url,
        )
        workload_status = JOB_FAILURE_EXIT
        evidence_ok = True
        try:
            try:
                result = runner.run(
                    container_argv[1:],
                    timeout_seconds=CONTAINER_RUN_TIMEOUT_SECONDS,
                    capture=False,
                )
                workload_status = result.returncode
            except subprocess.TimeoutExpired:
                workload_status = 124
                (artifacts / "timeout.txt").write_text(
                    "container workload exceeded 2520 seconds\n", encoding="utf-8"
                )
            try:
                inspect = runner.run(
                    ["image", "inspect", IMAGE_REFERENCE],
                    timeout_seconds=IMAGE_INSPECT_TIMEOUT_SECONDS,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                inspect = CommandResult(
                    EVIDENCE_FAILURE_EXIT,
                    "",
                    f"{type(exc).__name__}: {exc}",
                )
            with (artifacts / "identity.txt").open("a", encoding="utf-8") as stream:
                stream.write(f"image_inspect_status={inspect.returncode}\n")
                stream.write("image_inspect_begin\n")
                stream.write(inspect.stdout[:65536])
                if not inspect.stdout.endswith("\n"):
                    stream.write("\n")
                stream.write("image_inspect_end\n")
                if inspect.stderr:
                    stream.write(
                        f"image_inspect_error={inspect.stderr[:1000].strip()}\n"
                    )
            evidence_ok = inspect.returncode == 0
        finally:
            cleanup_ok = cleanup_container(
                runner, container_name, artifacts=artifacts
            )
        status = final_exit_code(workload_status, cleanup_ok=cleanup_ok)
        if status == 0 and not evidence_ok:
            return EVIDENCE_FAILURE_EXIT
        return status
    except Exception as exc:
        (artifacts / "job-error.txt").write_text(
            f"{type(exc).__name__}: {exc}\n", encoding="utf-8"
        )
        raise


def _run_cleanup_only(args: argparse.Namespace) -> int:
    workspace = args.workspace.resolve(strict=True)
    artifacts = workspace / "artifacts"
    artifacts.mkdir(mode=0o700, exist_ok=True)
    container_name = build_container_name(args.source_sha, args.build_number)
    cleanup_ok = cleanup_container(
        CommandRunner(),
        container_name,
        artifacts=artifacts,
        evidence_name="post-cleanup.txt",
    )
    return 0 if cleanup_ok else CLEANUP_FAILURE_EXIT


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cleanup-only", action="store_true")
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--build-number", required=True)
    parser.add_argument("--definition-sha")
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--node-name")
    parser.add_argument("--build-url")
    args = parser.parse_args()
    if not args.cleanup_only:
        for field in ("definition_sha", "node_name", "build_url"):
            if getattr(args, field) is None:
                parser.error(f"--{field.replace('_', '-')} is required")
    try:
        if args.cleanup_only:
            return _run_cleanup_only(args)
        return _run_job(args)
    except (JobError, OSError, subprocess.SubprocessError) as exc:
        print(f"jenkins-mac-integration: {exc}", file=sys.stderr)
        return JOB_FAILURE_EXIT


if __name__ == "__main__":
    raise SystemExit(main())
