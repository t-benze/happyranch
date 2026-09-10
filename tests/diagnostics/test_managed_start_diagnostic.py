from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import json
import hashlib

import pytest

ROOT = Path(__file__).resolve().parents[2]
SHIPPING = ROOT / "app/linux/package/real_systemd_n3.sh"
SCRIPT = ROOT / "scripts/diagnostics/managed_start_diagnostic.py"
spec = importlib.util.spec_from_file_location("diagnostic", SCRIPT)
diagnostic = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(diagnostic)


def test_literal_extraction_retains_pinned_setup_helpers_and_startup() -> None:
    source = SHIPPING.read_text()
    assert hashlib.sha256(source.encode()).hexdigest() == diagnostic.FROZEN_SHIPPING_SHA256
    rendered = diagnostic.extract_startup(source)
    start = source.index(diagnostic.START)
    probe = source.index(diagnostic.PROBE, start)
    positive = source.index(diagnostic.FIRST_POSITIVE, probe)
    assert rendered == source[:start] + source[start : positive + len(diagnostic.FIRST_POSITIVE)]
    for required in ("capture_denial_matrix() {", "reset_shipping_unit() {", "shipping_cleanup() {", "validate-denial-matrix", "socket.AF_NETLINK", "socket.SOCK_RAW", "/dev/net/tun", "create_connection"):
        assert required in rendered
    assert '[[ "$(systemctl show happyranch-tsnet-sidecar.service -p MainPID --value)" == 0 ]]' in rendered
    assert "credential.consumed" in rendered
    assert 'status --json | python -c' in rendered
    assert 'wait_for "failed credential staging cleanup"' in rendered
    assert 'wait_for "connector READY"' not in rendered


@pytest.mark.parametrize("replacement", [
    "# semantic evidence: startup\n# semantic evidence: startup\n",
    "capture_denial_matrix other-unit\n",
    "capture_denial_matrix shipping-unit\ncapture_denial_matrix shipping-unit\n",
    "sudo systemctl start happyranch-managed.target\n# semantic evidence: startup\n",
])
def test_extraction_fails_closed_on_digest_and_boundary_drift(replacement: str) -> None:
    source = SHIPPING.read_text()
    source = source.replace(diagnostic.START if replacement.startswith("#") else diagnostic.PROBE, replacement, 1)
    with pytest.raises(diagnostic.ExtractionError):
        diagnostic.extract_startup(source)
    # A trusted-byte caller still rejects duplicate, missing, and out-of-order
    # delimiters rather than silently selecting a different startup path.
    with pytest.raises(diagnostic.ExtractionError):
        diagnostic.extract_startup(source, expected_digest=hashlib.sha256(source.encode()).hexdigest())


@pytest.mark.parametrize("needle", [
    'wait_for() {',
    'capture_denial_matrix() {',
    'sudo systemctl start happyranch-managed.target || true',
])
def test_extractor_rejects_changed_helper_probe_or_startup_with_unchanged_markers(needle: str) -> None:
    source = SHIPPING.read_text().replace(needle, needle + " # changed", 1)
    with pytest.raises(diagnostic.ExtractionError, match="digest"):
        diagnostic.extract_startup(source)


def test_cli_writes_literal_shell_and_bash_parses_it(tmp_path: Path) -> None:
    output = tmp_path / "diagnostic-harness.sh"
    command = [sys.executable, str(SCRIPT), "--extract", "--shipping", str(SHIPPING), "--output", str(output)]
    assert subprocess.run(command, check=False).returncode == 0
    assert output.read_text() == diagnostic.extract_startup(SHIPPING.read_text())
    result = subprocess.run(["bash", "-n", str(output)], check=False, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def _shipping_function(source: str, name: str, successor: str) -> str:
    prefix = f"{name}() {{"
    if name in {"active", "absent"}:
        return prefix + source.split(prefix, 1)[1].split("\n", 1)[0]
    return prefix + source.split(prefix, 1)[1].split(f"\n}}\n{successor}", 1)[0] + "\n}"


def _write_fake_commands(tmp_path: Path) -> Path:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    (fake_bin / "sudo").write_text("""#!/usr/bin/env bash
set -e
printf 'sudo %s\\n' "$*" >> "$LOG"
if [[ $1 == timeout ]]; then
  shift; [[ $1 == 15 ]] && shift
fi
if [[ $1 == systemd-run ]]; then
  while [[ $1 != /usr/bin/python3 ]]; do shift; done
  exec "$@"
fi
if [[ $1 == systemctl ]]; then
  shift
  printf 'systemctl %s\\n' "$*" >> "$LOG"
  [[ $1 == is-active ]] && [[ ${ACTIVE:-0} != 1 ]] && exit 1
  exit 0
fi
if [[ $1 == test ]]; then
  if [[ $* == *credential.consumed* ]]; then [[ ${PRESENT_MARKER:-0} == 1 ]] && exit 0 || exit 1; fi
  if [[ $* == */run/credentials/happyranch-tsnet-sidecar.service* ]]; then
    count_file="$STAGING_COUNT"; count=$(cat "$count_file" 2>/dev/null || echo 0); echo $((count + 1)) > "$count_file"; [[ $count == 0 ]] && exit 1 || exit 0
  fi
fi
if [[ $1 == /fake/tailscale ]]; then
  [[ ${PEER_PRESENT:-0} == 1 ]] && printf '{"Peer":{"x":{"HostName":"home-sidecar-ci"}}}\\n' || printf '{"Peer":{}}\\n'
  exit 0
fi
exit 0
""")
    (fake_bin / "systemctl").write_text("""#!/usr/bin/env bash
printf 'systemctl %s\\n' "$*" >> "$LOG"
if [[ $1 == show && $* == *MainPID* ]]; then printf '%s\\n' "${MAIN_PID:-0}"; fi
exit 0
""")
    (fake_bin / "sleep").write_text("#!/usr/bin/env bash\nprintf 'sleep %s\\n' \"$*\" >> \"$LOG\"\n")
    (fake_bin / "seq").write_text("#!/usr/bin/env bash\nprintf '1\\n2\\n'\n")
    (fake_bin / "python").write_text("""#!/usr/bin/env bash
if [[ ${INVALID_MATRIX:-0} == 1 && $1 == *n3_evidence.py && $2 == validate-denial-matrix ]]; then
  /usr/bin/python3 - "$3" <<'PY'
import json, sys
path = sys.argv[1]
document = json.load(open(path))
document["operations"][0]["measured"] = False
open(path, "w").write(json.dumps(document))
PY
fi
exec /usr/bin/python3 "$@"
""")
    for command in fake_bin.iterdir():
        command.chmod(0o755)
    return fake_bin


def _literal_startup_result(tmp_path: Path, **extra_env: str) -> tuple[subprocess.CompletedProcess[str], str]:
    source = SHIPPING.read_text()
    rendered = diagnostic.extract_startup(source)
    startup = rendered[rendered.index(diagnostic.START) + len(diagnostic.START) :]
    wait_for = _shipping_function(source, "wait_for", "port_open")
    absent = _shipping_function(source, "absent", "evidence")
    active = _shipping_function(source, "active", "absent")
    capture = _shipping_function(source, "capture_denial_matrix", "shipping_cleanup")
    log = tmp_path / "calls"
    fake_bin = _write_fake_commands(tmp_path)
    harness = f'''set -euo pipefail
fail() {{ printf 'fail %s\\n' "$*" >> "$LOG"; exit 99; }}
{wait_for}
{active}
{absent}
evidence() {{ printf 'evidence %s\\n' "$*" >> "$LOG"; }}
diagnostic() {{ printf 'diagnostic %s\\n' "$*" >> "$LOG"; }}
{capture}
ts_dir=/fake
work={tmp_path}
diagnostics={tmp_path}
evidence_driver={ROOT / 'app/linux/package/n3_evidence.py'}
{startup}'''
    env = dict(os.environ, PATH=f"{fake_bin}:{os.environ['PATH']}", LOG=str(log), STAGING_COUNT=str(tmp_path / "staging-count"), **extra_env)
    result = subprocess.run(["bash", "-c", harness], env=env, check=False, capture_output=True, text=True)
    return result, log.read_text() if log.exists() else ""


def test_literal_startup_uses_actual_wait_and_capture_with_faked_external_effects(tmp_path: Path) -> None:
    result, calls = _literal_startup_result(tmp_path)
    assert result.returncode == 0, result.stderr
    assert "sleep 2" in calls  # literal negative-start delay
    assert "sleep 1" in calls  # actual shipping wait_for retried staged cleanup
    expected = [
        "sudo systemctl start happyranch-managed.target",
        "sudo systemctl stop happyranch-managed.target happyranch-tsnet-sidecar.service happyranch-connector.service",
        "systemctl show happyranch-tsnet-sidecar.service -p MainPID --value",
        "sudo /fake/tailscale --socket=",
        "sudo systemctl reset-failed happyranch-tsnet-sidecar.service happyranch-connector.service",
        "sudo mv /etc/happyranch/enrollment.key.held /etc/happyranch/enrollment.key",
        "sudo timeout 15 systemd-run",
        "sudo systemctl start happyranch-managed.target",
    ]
    positions: list[int] = []
    for item in expected:
        positions.append(calls.index(item, positions[-1] + 1 if positions else 0))
    assert positions == sorted(positions)
    assert (tmp_path / "shipping-unit-denial-matrix.json").exists()


@pytest.mark.parametrize("env, expected", [
    ({"ACTIVE": "1"}, "sidecar survived missing-credential startup"),
    ({"MAIN_PID": "17"}, "sidecar process survived failed startup"),
    ({"PRESENT_MARKER": "1"}, "residue at /var/lib/happyranch-tsnet-sidecar/credential.consumed"),
    ({"PEER_PRESENT": "1"}, "failed-start TSNet identity remained visible"),
])
def test_each_negative_start_guard_prevents_probe_and_positive_start(tmp_path: Path, env: dict[str, str], expected: str) -> None:
    result, calls = _literal_startup_result(tmp_path, **env)
    assert result.returncode == 99, result.stderr
    assert f"fail {expected}" in calls
    assert "systemd-run" not in calls
    assert calls.count("sudo systemctl start happyranch-managed.target") == 1


def test_invalid_actual_matrix_validator_prevents_positive_start(tmp_path: Path) -> None:
    result, calls = _literal_startup_result(tmp_path, INVALID_MATRIX="1")
    assert result.returncode == 1, result.stderr
    assert "sudo timeout 15 systemd-run" in calls
    assert calls.count("sudo systemctl start happyranch-managed.target") == 1
    assert "connector READY" not in calls


def test_workflow_uses_extractor_not_second_startup_program() -> None:
    workflow = (ROOT / ".github/workflows/managed-start-diagnostic.yml").read_text()
    assert "managed_start_diagnostic.py\" --extract" in workflow
    assert "app/linux/package/real_systemd_n3.sh" in workflow
    assert "DENIAL_PROGRAM" not in SCRIPT.read_text()
    assert "peer-visible" not in SCRIPT.read_text()


def test_actual_shipping_denial_validator_accepts_complete_matrix_and_rejects_invalid(tmp_path: Path) -> None:
    matrix = tmp_path / "matrix.json"
    operations = [
        {"id": name, "measured": True, "result": "deny", "category": "permission_denied", "errno": "EPERM"}
        for name in ("address_family_netlink", "linux_capabilities", "device_access", "writable_paths", "control_plane_operations")
    ]
    matrix.write_text(json.dumps({"schema": "happyranch.n3.sandbox-denial-matrix", "version": 1, "arm_id": "shipping-unit", "operations": operations}))
    validator = ROOT / "app/linux/package/n3_evidence.py"
    command = [sys.executable, str(validator), "validate-denial-matrix", str(matrix), "--expected-arm", "shipping-unit"]
    assert subprocess.run(command, check=False).returncode == 0
    operations[-1]["measured"] = False
    matrix.write_text(json.dumps({"schema": "happyranch.n3.sandbox-denial-matrix", "version": 1, "arm_id": "shipping-unit", "operations": operations}))
    assert subprocess.run(command, check=False).returncode != 0


def _collector_runner(canary: bytes = b""):
    properties = b"Result=exit-code\nActiveState=failed\nSubState=failed\nMainPID=7\nNRestarts=2\nInvocationID=0123456789abcdef0123456789abcdef\nExecStartPreCode=1\nExecStartPreStatus=126\nExecMainCode=1\nExecMainStatus=1\n"
    journal = b'{"unit":"happyranch-tsnet-sidecar.service","cause":"credential_missing","timestamp":12}\n'
    def runner(command: list[str] | tuple[str, ...], _remaining: float) -> diagnostic.RunResult:
        if command[0] == "systemctl":
            return diagnostic.RunResult(0, properties + canary)
        if command[0] == "journalctl":
            return diagnostic.RunResult(0, journal + canary)
        return diagnostic.RunResult(0 if command[-1].endswith("enrollment.key") else 1, canary)
    return runner


def test_collector_persists_typed_causal_evidence_without_canary(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    artifact = tmp_path / "observation.json"
    document = diagnostic.collect("positive_failure", artifact, 9999999999, _collector_runner(), now=lambda: 0)
    persisted = artifact.read_text()
    assert "q7M9zCANARY" not in persisted + capsys.readouterr().out + capsys.readouterr().err
    assert document["units"]["happyranch-tsnet-sidecar.service"]["ExecStartPreStatus"] == 126
    assert document["journal"] == [{"unit": "happyranch-tsnet-sidecar.service", "cause": "credential_missing", "timestamp": 12}]
    assert document["job"] == {"availability": "unavailable"}


@pytest.mark.parametrize("malformed", [b"Result=exit-code\nResult=success\n", b"Result=q7M9zCANARY\n", b"MainPID=bad\n"])
def test_collector_fails_closed_for_malformed_properties(tmp_path: Path, malformed: bytes) -> None:
    artifact = tmp_path / "observation.json"
    diagnostic.collect("negative", artifact, 9999999999, _collector_runner(malformed), now=lambda: 0)
    document = json.loads(artifact.read_text())
    assert all(value == {"availability": "unavailable"} for value in document["units"].values())
    assert "q7M9zCANARY" not in artifact.read_text()


def test_collector_marks_query_errors_unavailable_not_absent(tmp_path: Path) -> None:
    def runner(command: list[str] | tuple[str, ...], _remaining: float) -> diagnostic.RunResult:
        return diagnostic.RunResult(3) if command[0] == "test" else diagnostic.RunResult(1)
    artifact = tmp_path / "observation.json"
    diagnostic.collect("prepositive", artifact, 9999999999, runner, now=lambda: 0)
    assert all(value == {"availability": "unavailable"} for value in json.loads(artifact.read_text())["paths"].values())


def test_bounded_runner_drains_dual_streams_without_retaining_them() -> None:
    code = "import sys; sys.stdout.write('x'*1000000); sys.stderr.write('y'*1000000)"
    result = diagnostic.run_bounded([sys.executable, "-c", code], __import__("time").monotonic() + 3)
    assert result.returncode == 0
    assert result.truncated
    assert result.stdout == result.stderr == b""


def test_bounded_runner_reaps_timeout_and_pipe_holding_descendant() -> None:
    code = "import subprocess,sys,time; subprocess.Popen([sys.executable,'-c','import time; time.sleep(10)']); time.sleep(10)"
    start = __import__("time").monotonic()
    result = diagnostic.run_bounded([sys.executable, "-c", code], start + 0.15)
    assert result.timed_out
    assert __import__("time").monotonic() - start < 2
