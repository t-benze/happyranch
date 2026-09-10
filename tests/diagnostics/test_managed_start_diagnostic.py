from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import json
import hashlib
import shutil

import yaml

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
    literal = diagnostic.extract_literal_startup(source)
    rendered = diagnostic.extract_startup(source)
    start = source.index(diagnostic.START)
    probe = source.index(diagnostic.PROBE, start)
    positive = source.index(diagnostic.FIRST_POSITIVE, probe)
    literal_startup = source[:start] + source[start : positive + len(diagnostic.FIRST_POSITIVE)]
    assert literal == literal_startup
    assert 'python "$evidence_driver" finalize' not in rendered
    assert 'python "$evidence_driver" validate "$evidence_artifact"' not in rendered
    assert 'diagnostic_capture negative || true' in rendered
    assert 'diagnostic_capture prepositive || true' in rendered
    assert 'diagnostic_capture positive_success || true' in rendered
    assert 'diagnostic_capture positive_failure || true' in rendered
    assert 'trap diagnostic_exit EXIT' in rendered
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


def test_emitted_shell_uses_absolute_observer_and_preserves_first_start_status(tmp_path: Path) -> None:
    rendered = diagnostic.extract_startup(SHIPPING.read_text())
    assert str(SCRIPT.resolve()) in rendered
    assert 'set +e\nsudo systemctl start happyranch-managed.target\ndiagnostic_first_positive_status=$?' in rendered
    assert 'exit "$diagnostic_first_positive_status"' in rendered
    assert 'diagnostic_cleanup_done=0' in rendered
    assert 'diagnostic_cleanup "$status"' in rendered
    assert 'diagnostic_capture exceptional_exit || true' in rendered
    assert 'status != 0' in rendered
    assert 'diagnostic-cleanup.json' in rendered
    assert 'diagnostics="${N3_DIAGNOSTICS_DIR:-$(mktemp -d)}"' in rendered
    assert 'DIAGNOSTIC_PYTHON=' in rendered
    assert '"$DIAGNOSTIC_PYTHON" "$DIAGNOSTIC_OBSERVER" --capture' in rendered
    assert 'window_start=$((now - 60))' in rendered
    assert 'window_end=$((now + 1))' in rendered
    assert '--window-start "$window_start" --window-end "$window_end"' in rendered
    assert 'diagnostic_signal_term() { trap - INT TERM; exit 143; }' in rendered


def test_actual_emitted_shell_cleans_partial_setup_without_host_effects(tmp_path: Path) -> None:
    """The emitted bytes, not a shell surrogate, own early failure cleanup."""
    generated = tmp_path / "generated.sh"
    write = subprocess.run(
        [sys.executable, str(SCRIPT), "--extract", "--shipping", str(SHIPPING), "--output", str(generated)],
        check=False, capture_output=True, text=True,
    )
    assert write.returncode == 0, write.stderr
    fake_bin = tmp_path / "fake-bin"; fake_bin.mkdir()
    (fake_bin / "ps").write_text("#!/bin/sh\necho systemd\n")
    (fake_bin / "systemctl").write_text("#!/bin/sh\nexit 0\n")
    # Never forward a privileged-looking command: this is a finite fake-effect
    # seam and records no host service, package, or filesystem mutation.
    (fake_bin / "sudo").write_text("#!/bin/sh\nexit 0\n")
    (fake_bin / "curl").write_text("#!/bin/sh\nexit 7\n")
    (fake_bin / "sha256sum").write_text("#!/bin/sh\nexit 0\n")
    for executable in fake_bin.iterdir(): executable.chmod(0o755)
    package = tmp_path / "package.tar"; package.write_bytes(b"package")
    diagnostics = tmp_path / "diagnostics"
    env = dict(os.environ, PATH=f"{fake_bin}:{os.environ['PATH']}", PACKAGE_TAR=str(package),
               PROOF_SUBJECT_SHA="2147c5c6edb5d847e4c0ca855a044fa850fd7e11", N3_DIAGNOSTICS_DIR=str(diagnostics))
    result = subprocess.run(["bash", str(generated)], cwd=ROOT, env=env, check=False, capture_output=True, text=True, timeout=10)
    receipt = diagnostics / "diagnostic-cleanup.json"
    assert result.returncode == 7
    assert receipt.exists()
    assert receipt.read_text() == '{"cleanup":"complete"}\n'
    assert "synthetic-daemon-token" not in result.stdout + result.stderr + receipt.read_text()


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
    assert ' >"$package_tmp/build.raw" 2>&1' in workflow
    assert ' >"$package_tmp/harness.raw" 2>&1' in workflow
    assert "path: ${{ env.PUBLISH || format('{0}/managed-start-publish', runner.temp) }}" in workflow
    assert 'path: ${{ env.DIAGNOSTICS }}' not in workflow
    assert '--publish --diagnostics "$diagnostics" --output "$publish"' in workflow


def test_publication_consumer_rejects_opaque_json_and_sanitizes_metadata(tmp_path: Path) -> None:
    """Exercise the workflow consumer boundary without forwarding shell effects."""
    diagnostics, published = tmp_path / "diagnostics", tmp_path / "published"
    diagnostics.mkdir()
    canary = "OPAQUE_NESTED_CANARY"
    (diagnostics / "positive_failure-observation.json").write_text(json.dumps({"phase": "positive_failure", "units": {}, "paths": {}, "jobs": {}, "journal": [], "extra": {"secret": canary}}))
    (diagnostics / "diagnostic-cleanup.json").write_text(json.dumps({"cleanup": "complete", "extra": canary}))
    (diagnostics / "receipt.txt").write_text(f"schema=managed-start-diagnostic-receipt-v1\\nrun_id=12\\nextra={canary}\\n")
    assert diagnostic.publish(diagnostics, published, identities={"run_id": "12", "run_attempt": "1", "runner_image": "ubuntu-24.04", "systemd": "255", "shipping": "a" * 40, "diagnostic": "b" * 40, "workflow": "c" * 64, "script": "d" * 64, "tests": "e" * 64, "package": "f" * 64})
    output = "".join(path.read_text() for path in published.iterdir())
    assert canary not in output
    fallback = json.loads((published / "positive_failure-observation.json").read_text())
    assert fallback["phase"] == "positive_failure"
    assert all(item == {"availability": "unavailable"} for item in fallback["units"].values())
    assert not (published / "diagnostic-cleanup.json").exists()
    assert not (published / "receipt.txt").exists()
    provenance = json.loads((published / "provenance.json").read_text())
    assert provenance["runner_image"] == "ubuntu-24.04"


def test_publication_consumer_retains_only_canonical_observation_and_cleanup(tmp_path: Path) -> None:
    diagnostics, published = tmp_path / "diagnostics", tmp_path / "published"
    diagnostics.mkdir()
    document = diagnostic.collect("positive_failure", diagnostics / "positive_failure-observation.json", 9999999999, _collector_runner(), window=(1, 2), now=lambda: 0)
    (diagnostics / "diagnostic-cleanup.json").write_text('{"cleanup":"failed"}')
    assert diagnostic.publish(diagnostics, published, identities={})
    assert json.loads((published / "positive_failure-observation.json").read_text()) == document
    assert json.loads((published / "diagnostic-cleanup.json").read_text()) == {"cleanup": "failed"}


def test_publication_strictly_types_nested_fields_and_retains_four_query_journal(tmp_path: Path) -> None:
    diagnostics, published = tmp_path / "diagnostics", tmp_path / "published"
    diagnostics.mkdir()
    document = diagnostic.collect("positive_failure", diagnostics / "positive_failure-observation.json", 9999999999, _collector_runner(), window=(1, 2), now=lambda: 0)
    document["units"][diagnostic.UNITS[0]]["MainPID"] = True
    (diagnostics / "positive_failure-observation.json").write_text(json.dumps(document))
    assert diagnostic.publish(diagnostics, published, identities={})
    assert json.loads((published / "positive_failure-observation.json").read_text())["units"][diagnostic.UNITS[0]] == {"availability": "unavailable"}
    document = diagnostic.collect("positive_failure", diagnostics / "positive_failure-observation.json", 9999999999, _collector_runner(), window=(1, 2), now=lambda: 0)
    document["journal"] = [{"unit": diagnostic.UNITS[0], "cause": "credential_missing", "timestamp": item} for item in range(64)]
    (diagnostics / "positive_failure-observation.json").write_text(json.dumps(document))
    assert diagnostic.publish(diagnostics, published, identities={})
    assert len(json.loads((published / "positive_failure-observation.json").read_text())["journal"]) == 64


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
    properties = b"Result=exit-code\nActiveState=failed\nSubState=failed\nMainPID=7\nNRestarts=2\nInvocationID=0123456789abcdef0123456789abcdef\nExecStartPre={ path=/usr/bin/test ; argv[]=/usr/bin/test q7M9zCANARY ; code=exited ; status=126 }\nExecMainCode=1\nExecMainStatus=1\n"
    journal = b'{"_SYSTEMD_UNIT":"happyranch-tsnet-sidecar.service","__MONOTONIC_TIMESTAMP":"12","MESSAGE":"credential_missing q7M9zCANARY"}\n'
    def runner(command: list[str] | tuple[str, ...], deadline: float) -> diagnostic.RunResult:
        assert deadline == 9999999999
        if command[0] == "systemctl":
            return diagnostic.RunResult(0, properties + canary)
        if command[0] == "journalctl":
            return diagnostic.RunResult(0, journal + canary)
        return diagnostic.RunResult(0, b"PRESENT:81a4:0:0\n")
    return runner


def test_collector_persists_typed_causal_evidence_without_canary(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    artifact = tmp_path / "observation.json"
    document = diagnostic.collect("positive_failure", artifact, 9999999999, _collector_runner(), window=(1, 2), now=lambda: 0)
    persisted = artifact.read_text()
    assert "q7M9zCANARY" not in persisted + capsys.readouterr().out + capsys.readouterr().err
    assert document["units"]["happyranch-tsnet-sidecar.service"]["ExecStartPre"] == [{"code": "exited", "status": 126}]
    assert document["journal"] == [{"unit": "happyranch-tsnet-sidecar.service", "cause": "credential_missing", "timestamp": 12}]
    assert document["jobs"] == {unit: {"availability": "unavailable", "reason": "no_record"} for unit in diagnostic.UNITS}


@pytest.mark.parametrize("malformed", [b"Result=exit-code\nResult=success\n", b"Result=q7M9zCANARY\n", b"MainPID=bad\n"])
def test_collector_fails_closed_for_malformed_properties(tmp_path: Path, malformed: bytes) -> None:
    artifact = tmp_path / "observation.json"
    diagnostic.collect("negative", artifact, 9999999999, _collector_runner(malformed), window=(1, 2), now=lambda: 0)
    document = json.loads(artifact.read_text())
    assert all(value == {"availability": "unavailable"} for value in document["units"].values())
    assert "q7M9zCANARY" not in artifact.read_text()


def test_collector_marks_query_errors_unavailable_not_absent(tmp_path: Path) -> None:
    def runner(command: list[str] | tuple[str, ...], _remaining: float) -> diagnostic.RunResult:
        return diagnostic.RunResult(3) if command[0] == "sudo" else diagnostic.RunResult(1)
    artifact = tmp_path / "observation.json"
    diagnostic.collect("prepositive", artifact, 9999999999, runner, window=(1, 2), now=lambda: 0)
    assert all(value == {"availability": "unavailable"} for value in json.loads(artifact.read_text())["paths"].values())


def test_bounded_runner_caps_dual_streams_and_terminates_immediately() -> None:
    code = "import sys; sys.stdout.write('x'*1000000); sys.stderr.write('y'*1000000)"
    result = diagnostic.run_bounded([sys.executable, "-c", code], __import__("time").monotonic() + 3)
    assert result.truncated
    assert result.timed_out
    assert len(result.stdout) <= diagnostic.MAX_BYTES
    assert len(result.stderr) <= diagnostic.MAX_BYTES


def test_bounded_runner_reaps_timeout_and_pipe_holding_descendant() -> None:
    code = "import subprocess,sys,time; subprocess.Popen([sys.executable,'-c','import time; time.sleep(10)']); time.sleep(10)"
    start = __import__("time").monotonic()
    result = diagnostic.run_bounded([sys.executable, "-c", code], start + 0.15)
    assert result.timed_out
    assert __import__("time").monotonic() - start < 2


def test_actual_fake_command_adapter_persists_real_systemd255_shapes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    systemctl = bin_dir / "systemctl"
    systemctl.write_text("""#!/usr/bin/env python3
import sys
assert sys.argv[1] == 'show' and '--property=ExecStartPre' in sys.argv
print('Result=exit-code\\nActiveState=failed\\nSubState=failed\\nMainPID=7\\nNRestarts=2\\nInvocationID=0123456789abcdef0123456789abcdef\\nActiveEnterTimestampMonotonic=99\\nExecStartPre={ path=/usr/bin/test ; argv[]=/usr/bin/test ADAPTER_CANARY ; code=exited ; status=126 }\\nExecMainCode=1\\nExecMainStatus=1')
""")
    journalctl = bin_dir / "journalctl"
    journalctl.write_text("""#!/usr/bin/env python3
import json, sys
assert '--output=json' in sys.argv and any(arg.startswith('--unit=') for arg in sys.argv) and '--since=@10' in sys.argv and '--until=@20' in sys.argv
print(json.dumps({'_SYSTEMD_UNIT':'happyranch-tsnet-sidecar.service','__MONOTONIC_TIMESTAMP':'12','MESSAGE':'credential_missing ADAPTER_CANARY'}))
""")
    sudo = bin_dir / "sudo"
    sudo.write_text("""#!/usr/bin/env python3
import sys
assert sys.argv[1:4] == ['-n', 'python3', '-c']
print('PRESENT:81a4:0:0')
""")
    for command in bin_dir.iterdir(): command.chmod(0o755)
    artifact = tmp_path / "artifact.json"
    old_path = os.environ["PATH"]
    os.environ["PATH"] = f"{bin_dir}:{old_path}"
    try:
        document = diagnostic.collect("positive_failure", artifact, __import__("time").monotonic() + 2, window=(10, 20))
    finally:
        os.environ["PATH"] = old_path
    text = artifact.read_text() + capsys.readouterr().out + capsys.readouterr().err
    assert document["units"]["happyranch-tsnet-sidecar.service"]["ExecMainStatus"] == 1
    assert document["journal"] and document["journal"][0]["cause"] == "credential_missing"
    assert "ADAPTER_CANARY" not in text


def test_emitted_shell_encloses_fractional_same_second_journal_event() -> None:
    rendered = diagnostic.extract_startup(SHIPPING.read_text())
    # For now=100, @40..@101 encloses an event stamped 100.999 without wait.
    assert 'window_start=$((now - 60))' in rendered
    assert 'window_end=$((now + 1))' in rendered
    assert '--window-start "$window_start" --window-end "$window_end"' in rendered


def test_emitted_shell_partial_initialization_guards_unbound_diagnostics() -> None:
    rendered = diagnostic.extract_startup(SHIPPING.read_text())
    assert '[[ -n "${diagnostics:-}" && -d "$diagnostics" ]]' in rendered
    for boundary in ('diagnostics="${N3_DIAGNOSTICS_DIR:-$(mktemp -d)}"', 'mkdir -p "$diagnostics"', 'work="$(mktemp -d)"'):
        assert rendered.index('trap diagnostic_exit EXIT') < rendered.index(boundary)


def test_expired_deadline_starts_no_process_and_calls_cannot_renew(tmp_path: Path) -> None:
    calls: list[Sequence[str]] = []
    def runner(command: Sequence[str], deadline: float) -> diagnostic.RunResult:
        calls.append(command)
        return diagnostic.RunResult(0)
    diagnostic.collect("negative", tmp_path / "expired.json", 1, runner, window=(1, 2), now=lambda: 1)
    assert not calls


@pytest.mark.parametrize("bad", [b"Result=123\n", b"Result=exit-code\nResult=success\n", b"ExecStartPre={ argv[]=CANARY ; code=7 ; status=1 }\n"])
def test_property_enums_are_never_numeric_fallbacks(bad: bytes) -> None:
    assert diagnostic._properties(bad) is None


def test_systemd255_numeric_main_and_repeated_precommands_are_typed() -> None:
    parsed = diagnostic._properties(
        b"Result=exit-code\nActiveState=inactive\nExecMainCode=1\nExecMainStatus=2\n"
        b"ExecStartPre={ path=/bin/a ; code=killed ; status=15/TERM }\n"
        b"ExecStartPre={ path=/bin/b ; code=exited ; status=0 }\n"
    )
    assert parsed == {"Result": "exit-code", "ActiveState": "inactive", "ExecMainCode": 1, "ExecMainStatus": 2, "ExecStartPre": [{"code": "killed", "status": 15}, {"code": "exited", "status": 0}]}


def test_empty_optional_fields_preserve_independent_unit_state() -> None:
    parsed = diagnostic._properties(b"Result=success\nActiveState=inactive\nSubState=dead\nMainPID=\nExecStartPre=\nExecMainStatus=\n")
    assert parsed == {"Result": "success", "ActiveState": "inactive", "SubState": "dead", "MainPID": {"availability": "not_applicable"}, "ExecStartPre": {"availability": "not_applicable"}, "ExecMainStatus": {"availability": "not_applicable"}}


def test_structured_job_fields_are_retained_per_unit_and_not_inferred_from_result(tmp_path: Path) -> None:
    def runner(command: Sequence[str], _deadline: float) -> diagnostic.RunResult:
        if command[0] == "systemctl":
            return diagnostic.RunResult(0, b"Result=exit-code\nActiveState=failed\n")
        if command[-1] == "--unit=init.scope":
            return diagnostic.RunResult(0, b'{"_SYSTEMD_UNIT":"init.scope","JOB_UNIT":"happyranch-connector.service","JOB_ID":"42","JOB_RESULT":"failed","__MONOTONIC_TIMESTAMP":"9","MESSAGE":"main exited"}\n{"JOB_UNIT":"happyranch-managed.target","JOB_ID":"43","JOB_RESULT":"done"}\n')
        if command[0] == "journalctl":
            return diagnostic.RunResult(0, b"")
        return diagnostic.RunResult(0, b"ENOENT\n")
    document = diagnostic.collect("positive_failure", tmp_path / "observation.json", 99, runner, window=(1, 2), now=lambda: 0)
    assert document["jobs"]["happyranch-connector.service"] == {"availability": "available", "records": [{"availability": "available", "unit": "happyranch-connector.service", "id": 42, "result": "failed"}]}
    assert document["jobs"]["happyranch-managed.target"] == {"availability": "available", "records": [{"availability": "available", "unit": "happyranch-managed.target", "id": 43, "result": "done"}]}
    assert document["jobs"]["happyranch-tsnet-sidecar.service"] == {"availability": "unavailable", "reason": "no_record"}
    assert document["journal"] == [{"unit": "happyranch-connector.service", "cause": "main_exited", "timestamp": 9}]


def test_metadata_enoent_only_proves_absence_and_window_is_required(tmp_path: Path) -> None:
    assert diagnostic._path_metadata(diagnostic.RunResult(0, b"ENOENT\n")) == {"present": False}
    assert diagnostic._path_metadata(diagnostic.RunResult(0, b"UNAVAILABLE\n")) == {"availability": "unavailable"}
    artifact = tmp_path / "observation.json"
    diagnostic.collect("negative", artifact, 99, _collector_runner(), window=(20, 10), now=lambda: 0)
    assert json.loads(artifact.read_text())["journal"] == {"availability": "unavailable", "reason": "window_unavailable"}


def _observer_commands(tmp_path: Path) -> Path:
    bin_dir = tmp_path / "observer-bin"
    bin_dir.mkdir()
    (bin_dir / "systemctl").write_text("""#!/usr/bin/env python3
import sys
print('Result=success\\nActiveState=inactive\\nSubState=dead\\nMainPID=0')
""")
    (bin_dir / "journalctl").write_text("""#!/usr/bin/env python3
import json, sys
unit = next(item[7:] for item in sys.argv if item.startswith('--unit='))
if unit == 'happyranch-managed.target':
    for job_id in ('41', '42', '42', '43', '44', '45', '46', '47', '48', '49', '50', '51', '52', '53', '54', '55', '56', '57', '58'):
        print(json.dumps({'JOB_UNIT':unit,'JOB_ID':job_id,'JOB_RESULT':'done'}))
elif unit != 'init.scope':
    print(json.dumps({'JOB_UNIT':unit,'JOB_ID':str(len(unit)),'JOB_RESULT':'done'}))
print(json.dumps({'_SYSTEMD_UNIT':unit if unit != 'init.scope' else 'happyranch-managed.target','__MONOTONIC_TIMESTAMP':'9','MESSAGE':'credential missing SECRET_CANARY'}))
""")
    (bin_dir / "sudo").write_text("""#!/usr/bin/env python3
print('ENOENT')
""")
    for command in bin_dir.iterdir(): command.chmod(0o755)
    return bin_dir


def test_capture_cli_positive_success_persists_bounded_distinct_jobs_without_canaries(tmp_path: Path) -> None:
    artifact = tmp_path / "artifact.json"
    bin_dir = _observer_commands(tmp_path)
    env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}")
    command = [sys.executable, str(SCRIPT), "--capture", "--phase", "positive_success", "--window-start", "1", "--window-end", "3", "--budget-seconds", "5", "--output", str(artifact)]
    result = subprocess.run(command, env=env, check=False, capture_output=True, text=True)
    assert result.returncode == 0
    document = json.loads(artifact.read_text())
    assert set(document["jobs"]) == set(diagnostic.UNITS)
    assert all(item["availability"] == "available" for item in document["jobs"].values())
    target = document["jobs"]["happyranch-managed.target"]["records"]
    assert [record["id"] for record in target] == list(range(41, 57))
    assert len({record["id"] for record in target}) == len(target) == diagnostic.MAX_RECORDS
    assert "SECRET_CANARY" not in artifact.read_text() + result.stdout + result.stderr


def test_collector_preserves_earlier_job_when_later_unit_query_fails(tmp_path: Path) -> None:
    calls = 0
    def runner(command: Sequence[str], _deadline: float) -> diagnostic.RunResult:
        nonlocal calls
        if command[0] == "systemctl":
            return diagnostic.RunResult(0, b"Result=exit-code\nActiveState=failed\n")
        if command[0] == "sudo":
            return diagnostic.RunResult(0, b"ENOENT\n")
        calls += 1
        if calls == 1:
            return diagnostic.RunResult(0, b'{"JOB_UNIT":"happyranch-connector.service","JOB_ID":"7","JOB_RESULT":"failed"}\n')
        if calls == 2:
            return diagnostic.RunResult(1, b"LATER_QUERY_CANARY")
        return diagnostic.RunResult(0, b"")
    artifact = tmp_path / "observation.json"
    document = diagnostic.collect("positive_failure", artifact, 99, runner, window=(1, 2), now=lambda: 0)
    persisted = artifact.read_text()
    assert document["jobs"]["happyranch-connector.service"] == {"availability": "available", "records": [{"availability": "available", "unit": "happyranch-connector.service", "id": 7, "result": "failed"}]}
    assert document["jobs"]["happyranch-tsnet-sidecar.service"] == {"availability": "unavailable", "reason": "query_failed"}
    assert "LATER_QUERY_CANARY" not in persisted


def test_collector_marks_all_failed_queries_and_missing_records_explicitly(tmp_path: Path) -> None:
    def runner(command: Sequence[str], _deadline: float) -> diagnostic.RunResult:
        if command[0] == "systemctl": return diagnostic.RunResult(0, b"Result=success\n")
        if command[0] == "sudo": return diagnostic.RunResult(0, b"ENOENT\n")
        return diagnostic.RunResult(1)
    document = diagnostic.collect("positive_success", tmp_path / "observation.json", 99, runner, window=(1, 2), now=lambda: 0)
    assert document["jobs"] == {unit: {"availability": "unavailable", "reason": "query_failed"} for unit in diagnostic.UNITS}


@pytest.mark.parametrize("arguments", [
    ["--capture", "--phase", "nope", "--window-start", "1", "--window-end", "2", "--budget-seconds", "1"],
    ["--capture", "--phase", "negative", "--window-start", "4", "--window-end", "2", "--budget-seconds", "1"],
    ["--capture", "--phase", "negative", "--window-start", "1", "--window-end", "2", "--budget-seconds", "0"],
])
def test_capture_cli_invalid_arguments_have_zero_effects_and_no_echo(tmp_path: Path, arguments: list[str]) -> None:
    artifact = tmp_path / "artifact.json"
    canary = "ARGUMENT_CANARY"
    result = subprocess.run([sys.executable, str(SCRIPT), *arguments, "--output", str(artifact), "--unexpected", canary], check=False, capture_output=True, text=True)
    assert result.returncode == 2
    assert not artifact.exists()
    assert canary not in result.stdout + result.stderr


def test_capture_cli_output_failure_is_fixed_evidence_failure(tmp_path: Path) -> None:
    destination = tmp_path / "occupied"
    destination.mkdir()
    result = subprocess.run([sys.executable, str(SCRIPT), "--capture", "--phase", "negative", "--window-start", "1", "--window-end", "2", "--budget-seconds", "1", "--output", str(destination)], check=False, capture_output=True, text=True)
    assert result.returncode == 3
    assert not list(tmp_path.glob(".*diagnostic-tmp"))


def test_capture_cli_accepts_truthful_exceptional_exit_phase(tmp_path: Path) -> None:
    """The EXIT trap has a distinct final-state label, never a stale negative one."""
    artifact = tmp_path / "exceptional-exit.json"
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--capture", "--phase", "exceptional_exit", "--window-start", "1", "--window-end", "2", "--budget-seconds", "1", "--output", str(artifact)],
        check=False,
        capture_output=True,
        text=True,
    )
    # The host observer may not be available, but argument validation must
    # admit the final-state phase rather than reject it as malformed.
    assert result.returncode != 2


def test_run_bounded_reaps_when_selector_setup_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    class BrokenSelector:
        def register(self, *_args: object) -> None: raise OSError("SELECTOR_CANARY")
        def close(self) -> None: pass
    monkeypatch.setattr(diagnostic.selectors, "DefaultSelector", BrokenSelector)
    result = diagnostic.run_bounded([sys.executable, "-c", "import time; time.sleep(10)"], __import__("time").monotonic() + 2)
    assert result.failed and result.timed_out


# These are deliberately actual-workflow tests.  The adapter admits a finite
# command grammar and never forwards a service, root, package, or network call.
def _diagnostic_workflow_blocks() -> dict[str, str]:
    workflow = yaml.load((ROOT / ".github/workflows/managed-start-diagnostic.yml").read_text(), Loader=yaml.BaseLoader)
    return {step["name"]: step["run"] for step in workflow["jobs"]["diagnose"]["steps"] if "run" in step}


def _write_workflow_adapter(root: Path) -> Path:
    fake = root / "fake.py"
    fake.write_text("""#!/usr/bin/python3
import os, pathlib, sys
n = pathlib.Path(sys.argv[0]).name; a = sys.argv[1:]
pathlib.Path(os.environ['EFFECT_LOG']).open('a').write(n + ' ' + ' '.join(a) + '\\n')
if n == 'systemctl' and a == ['--version']: print('systemd 255 (255.4-1ubuntu8)'); raise SystemExit(0)
if n == 'git' and len(a) == 4 and a[0] == '-C' and a[2:] == ['rev-parse', 'HEAD']:
    if not pathlib.Path(a[1]).exists(): raise SystemExit(1)
    print(os.environ['SHIPPING_SHA'] if a[1] == 'shipping' else os.environ['CANDIDATE_SHA']); raise SystemExit(0)
if n == 'uv' and a[:3] == ['run', 'python', 'app/linux/package/build_package.py']: raise SystemExit(int(os.environ.get('FINAL_BUILD_EXIT', '0')))
if n == 'uv' and a == ['sync', '--frozen', '--group', 'build']: raise SystemExit(int(os.environ.get('SYNC_EXIT', '0')))
if n == 'uv' and (a[:2] == ['build', '--wheel'] or a[:3] == ['run', 'python', 'app/linux/package/build_connector.py']): raise SystemExit(0)
if n == 'python' and a == [os.environ['GITHUB_WORKSPACE'] + '/diagnostic/scripts/diagnostics/managed_start_diagnostic.py', '--extract', '--shipping', 'app/linux/package/real_systemd_n3.sh', '--output', os.environ['PACKAGE_TMP'] + '/diagnostic-harness.sh']:
    pathlib.Path(a[-1]).write_text('# finite fixture harness\\n')
    raise SystemExit(int(os.environ.get('EXTRACTOR_EXIT', '0')))
if n == 'go' and (a == ['test', './...'] or a[:3] == ['build', '-trimpath', '-buildvcs=false']): raise SystemExit(0)
if n == 'bash' and a == [os.environ['PACKAGE_TMP'] + '/diagnostic-harness.sh']: raise SystemExit(int(os.environ.get('HARNESS_EXIT', '0')))
# A refusal must remain observable even when the workflow redirects both
# streams.  Never forward an unrecognised command merely to make a fixture
# progress.
pathlib.Path(os.environ['EFFECT_LOG']).open('a').write('REFUSED:' + n + '\\n')
print('REFUSED:' + n, file=sys.stderr); raise SystemExit(91)
""")
    fake.chmod(0o755)
    return fake


def _actual_yaml_case(tmp_path: Path, case: str) -> tuple[dict[str, int], Path, str, dict[str, str]]:
    """Run selected real YAML blocks with a copied local subject and finite effects."""
    blocks = _diagnostic_workflow_blocks(); bin_dir = tmp_path / "bin"; bin_dir.mkdir()
    fake = _write_workflow_adapter(tmp_path)
    for name in ("git", "systemctl", "uv", "bash", "go"):
        (bin_dir / name).symlink_to(fake)
    for name in ("mkdir", "sha256sum", "awk", "grep", "sed", "cp", "find"):
        (bin_dir / name).symlink_to(Path("/usr/bin") / name)
    (bin_dir / "python").symlink_to("/usr/bin/python3")
    diagnostic_checkout = tmp_path / "diagnostic"; shipping_checkout = tmp_path / "shipping"
    for checkout in (diagnostic_checkout, shipping_checkout): checkout.mkdir()
    for rel in (".github/workflows/managed-start-diagnostic.yml", "scripts/diagnostics/managed_start_diagnostic.py"):
        target = diagnostic_checkout / rel; target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes((ROOT / rel).read_bytes())
    target = shipping_checkout / "app/linux/package/real_systemd_n3.sh"; target.parent.mkdir(parents=True); target.write_bytes(SHIPPING.read_bytes())
    # This directory is a prerequisite of the final builder.  Leaving it out
    # makes `cd` fail before builder37 and produces false evidence.
    (shipping_checkout / "app/linux/tsnet-sidecar").mkdir(parents=True)
    effect_log = tmp_path / "effects.log"
    env = dict(os.environ, PATH=str(bin_dir), RUNNER_TEMP=str(tmp_path), GITHUB_WORKSPACE=str(tmp_path), GITHUB_ENV=str(tmp_path / "github-env"), GITHUB_RUN_ID="1234", GITHUB_RUN_ATTEMPT="1", ImageOS="ubuntu24", ImageVersion="20260907.1.0", SHIPPING_SHA="2147c5c6edb5d847e4c0ca855a044fa850fd7e11", CANDIDATE_SHA="672b584b891e1375802f0e907b3276569badcff3", PYTHONDONTWRITEBYTECODE="1", EFFECT_LOG=str(effect_log))
    traces: dict[str, str] = {}
    def run(name: str, cwd: Path = tmp_path) -> int:
        result = subprocess.run(["/bin/bash", "--noprofile", "--norc", "-e", "-o", "pipefail", "-c", blocks[name]], cwd=cwd, env=env, check=False, capture_output=True, text=True, timeout=15)
        effects = effect_log.read_text() if effect_log.exists() else ""
        traces[name] = "stdout:\n" + result.stdout + "\nstderr:\n" + result.stderr + "\neffects:\n" + effects
        assert result.returncode != 91 and "REFUSED:" not in result.stderr and "REFUSED:" not in effects, result.stderr + effects
        return result.returncode
    exits = {"init": run("Initialize failure-safe diagnostic receipt")}
    for line in (tmp_path / "github-env").read_text().splitlines():
        key, value = line.split("=", 1); env[key] = value
    diagnostics_dir = Path(env["DIAGNOSTICS"]); package_dir = Path(env["PACKAGE_TMP"]); publish_dir = Path(env["PUBLISH"])
    (package_dir / "happyranch-linux-amd64.tar").write_text("fixture package")
    (diagnostics_dir / "diagnostic-cleanup.json").write_text('{"cleanup":"complete"}\n')
    if case == "f1":
        doc = diagnostic.collect("positive_failure", diagnostics_dir / "positive_failure-observation.json", 99, runner=lambda *_: diagnostic.RunResult(0, b""), window=(1, 2), now=lambda: 0)
        doc["paths"][diagnostic.PATHS[0]] = "PATH_SCALAR_CANARY"
        (diagnostics_dir / "positive_failure-observation.json").write_text(json.dumps(doc))
    if case in {"f2", "f2-diagnostic"}: shutil.rmtree(diagnostic_checkout)
    if case in {"f2", "f2-shipping"}: shutil.rmtree(shipping_checkout)
    if case == "f3":
        env["FINAL_BUILD_EXIT"] = "37"; exits["build"] = run("Build pinned shipping package", shipping_checkout)
    if case in {"f3-extractor3", "f3-harness7", "f3-harness0"}:
        # The exact reuse block is run only through the finite adapter.  The
        # external extractor and extracted harness are the two admitted
        # effects; its later provenance block runs with the real local
        # publisher, after this step has recorded its original status.
        (bin_dir / "python").unlink(); (bin_dir / "python").symlink_to(fake)
        env["EXTRACTOR_EXIT"] = "3" if case == "f3-extractor3" else "0"
        env["HARNESS_EXIT"] = "7" if case == "f3-harness7" else "0"
        exits["reuse"] = run("Reuse pinned setup through first start", shipping_checkout)
        (bin_dir / "python").unlink(); (bin_dir / "python").symlink_to("/usr/bin/python3")
    if case == "f4": (diagnostics_dir / "diagnostic-cleanup.json").write_text(" " * 1_200_000 + '{"cleanup":"complete"}')
    if case == "f6":
        # Do not copy the corrupt result into the later producer document.
        units = {unit: {"Result": "exit-code", "ActiveState": "failed", "SubState": "failed", "MainPID": 7, "NRestarts": 0, "InvocationID": "0" * 32, "ExecStartPre": [{"code": "exited", "status": 126}], "ExecMainCode": 1, "ExecMainStatus": 7} for unit in diagnostic.UNITS}
        valid = {"phase": "positive_success", "units": units, "paths": {path: {"present": False} for path in diagnostic.PATHS}, "jobs": {unit: {"availability": "available", "records": [{"availability": "available", "unit": unit, "id": 42, "result": "failed"}]} for unit in diagnostic.UNITS}, "journal": [{"unit": diagnostic.UNITS[0], "cause": "main_exited", "timestamp": 100}]}
        assert diagnostic._canonical_observation(valid)
        malformed = json.loads(json.dumps(valid)); malformed["phase"] = "positive_failure"
        malformed["units"][diagnostic.UNITS[0]]["Result"] = {"wrong": "type"}
        (diagnostics_dir / "positive_failure-observation.json").write_text(json.dumps(malformed))
        (diagnostics_dir / "positive_success-observation.json").write_text(json.dumps(valid))
    exits["provenance"] = run("Record provenance")
    return exits, publish_dir, (tmp_path / "github-env").read_text() + "\nEFFECTS:\n" + (effect_log.read_text() if effect_log.exists() else ""), traces


@pytest.mark.parametrize("case", ["f1", "f2", "f3", "f4", "f5", "f6"], ids=["F1-typed-path-scalar", "F2-missing-both-checkouts", "F3-final-builder37", "F4-bounded-cleanup-read", "F5-actual-yaml-shell", "F6-wrong-result-retains-later"])
def test_actual_yaml_regression_requirements_are_not_helper_only(tmp_path: Path, case: str) -> None:
    exits, published, env_bytes, traces = _actual_yaml_case(tmp_path, case)
    # F5 is the control proving these assertions use the exact YAML bytes and
    # inherited bash flags; the other rows encode required (currently red) behavior.
    assert "DIAGNOSTICS=" in env_bytes
    if case == "f1": assert "PATH_SCALAR_CANARY" not in "".join(p.read_text() for p in published.glob("*"))
    if case == "f2":
        provenance = json.loads((published / "provenance.json").read_text())
        assert provenance["shipping"] == "unavailable" and provenance["diagnostic"] == "unavailable"
    if case == "f3":
        # The exact terminal builder, rather than the earlier sync command,
        # must have been reached under the inherited strict Bash flags.
        assert traces["Build pinned shipping package"].split("effects:\n", 1)[1].splitlines() == [
            "uv sync --frozen --group build",
            "go test ./...",
            "go build -trimpath -buildvcs=false -o " + str(tmp_path / "managed-start-package/happyranch-tsnet-sidecar") + " ./cmd/happyranch-tsnet-sidecar",
            "uv build --wheel --out-dir " + str(tmp_path / "managed-start-package"),
            "uv run python app/linux/package/build_connector.py --wheel  --output " + str(tmp_path / "managed-start-package/happyranch-connector"),
            "uv run python app/linux/package/build_package.py --sidecar " + str(tmp_path / "managed-start-package/happyranch-tsnet-sidecar") + " --connector " + str(tmp_path / "managed-start-package/happyranch-connector") + " --wheel  --version ci --output " + str(tmp_path / "managed-start-package/happyranch-linux-amd64.tar"),
        ]
        assert exits["build"] == 37
        assert "uv run python app/linux/package/build_package.py" in env_bytes
        assert "build_status=37\n" in (published / "receipt.txt").read_text()
    if case == "f4":
        # The current publisher reads the whole cleanup document despite its
        # bounded collector contract; no unrelated provenance may disappear.
        assert exits["provenance"] == 3
        assert json.loads((published / "provenance.json").read_text())["shipping"].startswith("2147")
    if case == "f5": assert exits["provenance"] == 0
    if case == "f6":
        retained = json.loads((published / "positive_success-observation.json").read_text())
        assert retained["phase"] == "positive_success" and retained["units"][diagnostic.UNITS[0]]["Result"] == "exit-code"


@pytest.mark.parametrize("absence", ["baseline", "diagnostic", "shipping", "both"], ids=["F2-baseline", "F2-missing-diagnostic", "F2-missing-shipping", "F2-missing-both"])
def test_actual_yaml_f2_checkout_absence_preserves_independent_provenance(tmp_path: Path, absence: str) -> None:
    """The always provenance block has independent fields, not all-or-nothing fallback."""
    case = {"baseline": "f5", "diagnostic": "f2-diagnostic", "shipping": "f2-shipping", "both": "f2"}[absence]
    exits, published, _env_bytes, _traces = _actual_yaml_case(tmp_path, case)
    assert exits["provenance"] == 0
    raw = (published / "provenance.json").read_bytes()
    document = json.loads(raw)
    # The baseline fixture establishes every identity; absence only makes its
    # corresponding checkout-derived fields unavailable.
    if absence in {"diagnostic", "both"}:
        assert document["diagnostic"] == "unavailable"
    else:
        assert document["diagnostic"].startswith("672b")
    if absence in {"shipping", "both"}:
        assert document["shipping"] == "unavailable"
    else:
        assert document["shipping"].startswith("2147")
    assert document["package"] != "unavailable"
    assert document["run_id"] == "1234" and document["run_attempt"] == "1"
    assert document["systemd"] == "255"
    assert (published / "receipt.txt").is_file()
    assert (published / "diagnostic-cleanup.json").is_file()
    assert all("fixture package" not in path.read_text(errors="ignore") for path in published.iterdir())


def test_actual_yaml_f2_initializer_env_failure_skips_normal_steps_but_runs_always_provenance(tmp_path: Path) -> None:
    blocks = _diagnostic_workflow_blocks()
    (tmp_path / "github-env").mkdir()
    # Use the same finite adapter and exact inherited shell invocation.  A
    # directory GITHUB_ENV makes the initializer fail after its receipt setup.
    bin_dir = tmp_path / "bin"; bin_dir.mkdir(); fake = _write_workflow_adapter(tmp_path)
    for name in ("git", "systemctl", "uv", "bash", "go"): (bin_dir / name).symlink_to(fake)
    for name in ("mkdir", "sha256sum", "awk", "grep", "sed", "cp", "find"): (bin_dir / name).symlink_to(Path("/usr/bin") / name)
    (bin_dir / "python").symlink_to("/usr/bin/python3")
    effect_log = tmp_path / "effects.log"
    occupied_publish = tmp_path / "occupied-publish"; occupied_publish.write_text("not a directory")
    env = dict(os.environ, PATH=str(bin_dir), RUNNER_TEMP=str(tmp_path), GITHUB_WORKSPACE=str(tmp_path), GITHUB_ENV=str(tmp_path / "github-env"), PUBLISH=str(occupied_publish), GITHUB_RUN_ID="1234", GITHUB_RUN_ATTEMPT="1", ImageOS="ubuntu24", ImageVersion="20260907.1.0", EFFECT_LOG=str(effect_log), PYTHONDONTWRITEBYTECODE="1")
    init = subprocess.run(["/bin/bash", "--noprofile", "--norc", "-e", "-o", "pipefail", "-c", blocks["Initialize failure-safe diagnostic receipt"]], cwd=tmp_path, env=env, check=False, capture_output=True, text=True)
    assert init.returncode != 0
    # A workflow would skip ordinary build/harness steps after this failure;
    # invoke only the real always() block and require a truthful safe result.
    always = subprocess.run(["/bin/bash", "--noprofile", "--norc", "-e", "-o", "pipefail", "-c", blocks["Record provenance"]], cwd=tmp_path, env=env, check=False, capture_output=True, text=True)
    assert always.returncode == 3
    # `always()` may probe systemd for provenance, but no ordinary build,
    # extractor, or harness effect is allowed after initialization failed.
    assert effect_log.read_text().splitlines() == ["systemctl --version"]
    assert "diagnostic_publication_failed" in always.stderr + always.stdout
    assert not (occupied_publish / "provenance.json").exists()


def test_actual_yaml_f3_nonfinal_sync37_records_original_status(tmp_path: Path) -> None:
    exits, published, _env_bytes, traces = _actual_yaml_case(tmp_path, "f5")
    # Re-run the exact build block with only its first admitted effect failing;
    # this control distinguishes the terminal-builder regression from normal
    # AND-list status capture.
    blocks = _diagnostic_workflow_blocks(); env_path = tmp_path / "github-env"
    env = dict(os.environ, PATH=str(tmp_path / "bin"), RUNNER_TEMP=str(tmp_path), GITHUB_WORKSPACE=str(tmp_path), GITHUB_ENV=str(env_path), EFFECT_LOG=str(tmp_path / "effects.log"), SYNC_EXIT="37", PACKAGE_TMP=str(tmp_path / "managed-start-package"), DIAGNOSTICS=str(tmp_path / "managed-start-diagnostic"))
    result = subprocess.run(["/bin/bash", "--noprofile", "--norc", "-e", "-o", "pipefail", "-c", blocks["Build pinned shipping package"]], cwd=tmp_path / "shipping", env=env, check=False, capture_output=True, text=True)
    assert result.returncode == 37
    assert "build_status=37\n" in (tmp_path / "managed-start-diagnostic/receipt.txt").read_text()
    assert exits["provenance"] == 0 and (published / "diagnostic-cleanup.json").is_file()


@pytest.mark.parametrize(
    ("case", "expected_step_exit", "expected_effects"),
    [
        ("f3-extractor3", 3, ["python"]),
        ("f3-harness7", 7, ["python", "bash"]),
        ("f3-harness0", 0, ["python", "bash"]),
    ],
    ids=["F3-extractor-exit3", "F3-harness-exit7", "F3-harness-exit0"],
)
def test_actual_yaml_f3_reuse_records_extractor_and_harness_statuses(
    tmp_path: Path, case: str, expected_step_exit: int, expected_effects: list[str]
) -> None:
    """The frozen reuse YAML records its own result before actual always provenance."""
    exits, published, _env_bytes, traces = _actual_yaml_case(tmp_path, case)
    assert exits["reuse"] == expected_step_exit
    receipt = (tmp_path / "managed-start-diagnostic" / "receipt.txt").read_text()
    assert f"harness_status={expected_step_exit}\n" in receipt
    # The trace is captured immediately after the reuse block, before its
    # subsequent always() provenance consumer.  Its finite adapter admits no
    # command beyond the extractor and (only after successful extraction) the
    # harness, so the order and count are part of the evidence.
    effects = traces["Reuse pinned setup through first start"].split("effects:\n", 1)[1].splitlines()
    assert [effect.split(" ", 1)[0] for effect in effects] == expected_effects
    assert effects[0] == "python " + str(tmp_path / "diagnostic/scripts/diagnostics/managed_start_diagnostic.py") + " --extract --shipping app/linux/package/real_systemd_n3.sh --output " + str(tmp_path / "managed-start-package/diagnostic-harness.sh")
    if expected_step_exit == 3:
        assert len(effects) == 1
    else:
        assert effects[1] == "bash " + str(tmp_path / "managed-start-package/diagnostic-harness.sh")
        assert len(effects) == 2
    # This is fixture-supplied availability, not a claim that the emitted
    # harness proved cleanup.  It survives the following real always() block.
    assert exits["provenance"] == 0
    assert json.loads((published / "diagnostic-cleanup.json").read_text()) == {"cleanup": "complete"}


def test_actual_yaml_adapter_refusal_is_persisted_when_streams_and_status_are_swallowed(tmp_path: Path) -> None:
    fake = _write_workflow_adapter(tmp_path); effect_log = tmp_path / "effects.log"
    env = dict(os.environ, EFFECT_LOG=str(effect_log))
    result = subprocess.run([str(fake)], executable=str(fake), env=env, check=False, capture_output=True, text=True)
    # Deliberately discard the returned status and stderr as a workflow
    # redirect could; the marker still exposes the finite refusal.
    _ = result.returncode, result.stderr
    assert effect_log.read_text().splitlines()[-1] == "REFUSED:fake.py"
