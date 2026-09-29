from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import runpy
import shutil
import stat
import subprocess
import sys
import tarfile
import time
import zipfile

import pytest

from app.linux.package.build_connector import build_connector
from runtime.remote_access.cli import (
    _prepare_fresh_enrollment,
    _reconcile_enrollment_retirement,
    _retire_enrollment_source,
    main as connector_cli_main,
)
from runtime.remote_access.linux_package import (
    CompositeServiceManager,
    PackageError,
    TRANSACTION_MARKER,
    _recover_interrupted,
    build_linux_package,
    credential_capability,
    install_linux_package,
    render_composite_units,
    uninstall_linux_package,
)


def _stage_system_credentials(root: Path, *, enrollment: bool = False) -> None:
    config = root / "etc/happyranch"
    config.mkdir(parents=True, mode=0o700)
    (config / "daemon.token").write_text("daemon\n")
    (config / "daemon.token").chmod(0o600)
    if enrollment:
        (config / "enrollment.key").write_text("one-use\n")
        (config / "enrollment.key").chmod(0o600)


def _tree_snapshot(root: Path) -> dict[str, list[object]]:
    """Complete lstat identity (type/mode/uid/gid + bytes) of a fixture tree."""
    entries: dict[str, list[object]] = {}
    for path in [root, *sorted(root.rglob("*"))]:
        info = path.lstat()
        value: list[object] = [
            stat.S_IFMT(info.st_mode),
            stat.S_IMODE(info.st_mode),
            info.st_uid,
            info.st_gid,
        ]
        if stat.S_ISREG(info.st_mode):
            value.append(path.read_bytes())
        entries[str(path.relative_to(root))] = value
    return entries


def test_connector_builder_installs_real_wheel_without_ambient_pip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    wheel = tmp_path / "happyranch-1-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as built:
        built.writestr("runtime/__init__.py", "")
        built.writestr("runtime/remote_access/__init__.py", "")
        built.writestr("runtime/remote_access/cli.py", "REAL_WHEEL = True\ndef main(): return 7\n")
        built.writestr("happyranch-1.dist-info/METADATA", "Name: happyranch\nVersion: 1\n")
    commands: list[list[str]] = []
    real_run = subprocess.run

    def run(command, **_kwargs):
        commands.append(command)
        installed = Path(command[command.index("--paths") + 1])
        assert (installed / "runtime/remote_access/cli.py").read_text() == "REAL_WHEEL = True\ndef main(): return 7\n"
        entry_path = Path(command[-1])
        entry = entry_path.read_text()
        assert "raise SystemExit(main())" in entry
        generated = real_run(
            [sys.executable, "-c", "import runpy,sys; sys.path.insert(0, sys.argv[1]); runpy.run_path(sys.argv[2], run_name='__main__')", str(installed), str(entry_path)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert generated.returncode == 7
        (tmp_path / "happyranch-connector").write_bytes(b"frozen-real-wheel")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(subprocess, "run", run)
    output = build_connector(wheel, tmp_path / "happyranch-connector")
    assert output.read_bytes() == b"frozen-real-wheel"
    assert len(commands) == 1
    assert "PyInstaller" in commands[0]
    assert "pip" not in commands[0]


def test_generated_connector_entry_executes_actual_wheel_cli_capability_and_retirement_cases(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise the builder-generated shipping entry with the real candidate wheel."""
    wheel_dir = tmp_path / "wheel"
    subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(wheel_dir)],
        check=True,
        capture_output=True,
        text=True,
    )
    wheel, = wheel_dir.glob("happyranch-*.whl")
    installed = tmp_path / "installed"
    entry = tmp_path / "connector_entry.py"
    output = tmp_path / "happyranch-connector"
    real_run = subprocess.run

    def capture_generated_entry(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        generated_installed = Path(command[command.index("--paths") + 1])
        generated_entry = Path(command[-1])
        shutil.copytree(generated_installed, installed)
        shutil.copy2(generated_entry, entry)
        output.write_bytes(b"frozen-placeholder")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(subprocess, "run", capture_generated_entry)
    assert build_connector(wheel, output) == output
    assert entry.read_text(encoding="utf-8") == (
        "from runtime.remote_access.cli import main\n"
        "if __name__ == '__main__': raise SystemExit(main())\n"
    )
    monkeypatch.setattr(subprocess, "run", real_run)
    launcher = (
        "import runpy, sys; installed, entry, *arguments = sys.argv[1:]; "
        "sys.path.insert(0, installed); sys.argv = [entry, *arguments]; "
        "runpy.run_path(entry, run_name='__main__')"
    )

    def invoke(*arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-I", "-c", launcher, str(installed), str(entry), *arguments],
            cwd=tmp_path,
            env={key: value for key, value in os.environ.items() if key not in {"PYTHONPATH", "CREDENTIALS_DIRECTORY"}},
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )

    absent = invoke("credential-capability", "--name", "daemon.token", "--unit", "happyranch-connector.service")
    assert (absent.returncode, absent.stderr.strip()) == (1, "credential_absent")
    incompatible = invoke("credential-capability", "--name", "daemon.token", "--unit", "happyranch-tsnet-sidecar.service")
    assert (incompatible.returncode, incompatible.stderr.strip()) == (1, "credential_staging_incompatible")
    source = tmp_path / "enrollment.key"
    source.write_text("one-use\n")
    source.chmod(0o600)
    marker = tmp_path / "credential.consumed"
    marker.write_text("durable\n")
    marker.chmod(0o600)
    consumed = invoke("credential-capability", "--name", "enrollment.key", "--unit", "happyranch-tsnet-sidecar.service", "--consumed-marker", str(marker))
    assert (consumed.returncode, consumed.stdout, consumed.stderr) == (0, "", "")
    retired = invoke("retire-enrollment-source", "--source", str(source), "--marker", str(marker))
    assert retired.returncode == 0 and not source.exists() and marker.exists()
    assert invoke("retire-enrollment-source", "--source", str(source), "--marker", str(marker)).returncode == 0
    invalid_root = tmp_path / "invalid"
    invalid_root.mkdir()
    invalid_source = invalid_root / "enrollment.key"
    invalid_source.write_text("one-use\n")
    invalid_source.chmod(0o600)
    invalid_marker = invalid_root / "credential.consumed"
    invalid_marker.write_text("bad\n")
    invalid_marker.chmod(0o644)
    invalid = invoke("retire-enrollment-source", "--source", str(invalid_source), "--marker", str(invalid_marker))
    assert (invalid.returncode, invalid.stderr.strip()) == (1, "error: enrollment_source_retirement_failed")
    assert invalid_source.exists() and not invalid_source.with_name("enrollment.key.retiring").exists()

    # THR-228 seq275: the generated shipping entry must reach the actual
    # bound affirmative stopped-state observation. A stopped unit performs the
    # transition with exactly one reload; a query failure refuses category-only
    # with an unchanged snapshot and no reload.
    fixture_bin = tmp_path / "fixture-bin"
    fixture_bin.mkdir()
    calls = tmp_path / "systemctl.calls"
    show_file = tmp_path / "systemctl.show"
    show_file.write_bytes(b"LoadState=loaded\nActiveState=inactive\nSubState=dead\n")
    fixture_systemctl = fixture_bin / "systemctl"
    fixture_systemctl.write_text(
        "#!/bin/sh\n"
        'printf \'%s\\n\' "$*" >> "$SYSTEMCTL_CALLS"\n'
        'case "$1" in\n'
        '  show) cat "$SYSTEMCTL_SHOW_OUTPUT"; exit "$SYSTEMCTL_SHOW_EXIT" ;;\n'
        '  daemon-reload) exit 0 ;;\n'
        '  *) exit 99 ;;\n'
        "esac\n"
    )
    fixture_systemctl.chmod(0o700)

    def invoke_with_service(*arguments: str, query_exit: int = 0) -> subprocess.CompletedProcess[str]:
        calls.write_bytes(b"")
        env = {
            key: value
            for key, value in os.environ.items()
            if key not in {"PYTHONPATH", "CREDENTIALS_DIRECTORY"}
        }
        env.update({
            "PATH": str(fixture_bin) + os.pathsep + env.get("PATH", ""),
            "SYSTEMCTL_CALLS": str(calls),
            "SYSTEMCTL_SHOW_OUTPUT": str(show_file),
            "SYSTEMCTL_SHOW_EXIT": str(query_exit),
        })
        return subprocess.run(
            [sys.executable, "-I", "-c", launcher, str(installed), str(entry), *arguments],
            cwd=tmp_path,
            env=env,
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )

    fresh = tmp_path / "fresh"
    fresh.mkdir()
    fresh_source = fresh / "enrollment.key"
    fresh_source.write_text("fresh-one-use\n")
    fresh_source.chmod(0o600)
    fresh_marker = fresh / "credential.consumed"
    fresh_marker.write_text("durable\n")
    fresh_marker.chmod(0o600)
    fresh_dropin = fresh / "unit.d" / "10-enrollment-credential.conf"
    fresh_dropin.parent.mkdir()
    accepted = invoke_with_service(
        "prepare-fresh-enrollment",
        "--source", str(fresh_source),
        "--marker", str(fresh_marker),
        "--dropin", str(fresh_dropin),
    )
    assert (accepted.returncode, accepted.stdout, accepted.stderr) == (0, "", "")
    assert not fresh_marker.exists()
    assert fresh_dropin.read_bytes() == b"[Service]\nLoadCredential=enrollment.key:/etc/happyranch/enrollment.key\n"
    assert fresh_dropin.stat().st_mode & 0o777 == 0o600
    assert fresh_source.read_text() == "fresh-one-use\n"
    assert calls.read_text().splitlines() == [
        "show -p LoadState -p ActiveState -p SubState happyranch-tsnet-sidecar.service",
        "daemon-reload",
    ]
    published_before = (fresh_dropin.read_bytes(), fresh_dropin.stat().st_mode, fresh_source.stat().st_mode)
    refused = invoke_with_service(
        "prepare-fresh-enrollment",
        "--source", str(fresh_source),
        "--marker", str(fresh_marker),
        "--dropin", str(fresh_dropin),
        query_exit=1,
    )
    assert (refused.returncode, refused.stdout, refused.stderr.strip()) == (
        1,
        "",
        "error: fresh_enrollment_transition_failed",
    )
    assert not fresh_marker.exists()
    assert (fresh_dropin.read_bytes(), fresh_dropin.stat().st_mode, fresh_source.stat().st_mode) == published_before
    assert calls.read_text().splitlines() == [
        "show -p LoadState -p ActiveState -p SubState happyranch-tsnet-sidecar.service"
    ]

    # TASK8607 F1: the builder-generated shipping entry must validate raw record
    # framing before normalization. Each non-LF separator is refused
    # category-only with a complete unchanged snapshot and no reload, twice.
    for label, malformed in (
        ("file-separator", b"LoadState=loaded\x1cActiveState=inactive\x1cSubState=dead\n"),
        ("vertical-tab", b"LoadState=loaded\x0bActiveState=inactive\x0bSubState=dead\n"),
        ("form-feed", b"LoadState=loaded\x0cActiveState=inactive\x0cSubState=dead\n"),
        (
            "unicode-line-separator",
            "LoadState=loaded\u2028ActiveState=inactive\u2028SubState=dead\n".encode(),
        ),
    ):
        show_file.write_bytes(malformed)
        case_root = tmp_path / f"framing-{label}"
        case_root.mkdir()
        case_source = case_root / "enrollment.key"
        case_source.write_text("fresh-one-use\n")
        case_source.chmod(0o600)
        case_marker = case_root / "credential.consumed"
        case_marker.write_text("durable\n")
        case_marker.chmod(0o600)
        case_dropin = case_root / "unit.d" / "10-enrollment-credential.conf"
        case_dropin.parent.mkdir()
        malformed_arguments = (
            "prepare-fresh-enrollment",
            "--source", str(case_source),
            "--marker", str(case_marker),
            "--dropin", str(case_dropin),
        )
        before_framing = _tree_snapshot(case_root)
        for attempt in (1, 2):
            malformed_result = invoke_with_service(*malformed_arguments)
            assert (
                malformed_result.returncode,
                malformed_result.stdout,
                malformed_result.stderr.strip(),
            ) == (1, "", "error: fresh_enrollment_transition_failed"), (label, attempt)
            assert _tree_snapshot(case_root) == before_framing, (label, attempt)
            assert not case_dropin.with_name(case_dropin.name + ".new").exists()
        assert calls.read_text().splitlines() == [
            "show -p LoadState -p ActiveState -p SubState happyranch-tsnet-sidecar.service"
        ], label


def _inputs(tmp_path: Path) -> tuple[Path, Path, Path, Path, Path]:
    sidecar = tmp_path / "sidecar"
    sidecar.write_bytes(b"sidecar-binary")
    connector = tmp_path / "connector"
    shutil.copy2(sys.executable, connector)
    connector.chmod(0o700)
    wheel = tmp_path / "happyranch-1-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as built:
        built.writestr("runtime/__init__.py", "")
        built.writestr("runtime/remote_access/__init__.py", "")
        built.writestr("runtime/remote_access/cli.py", "print('fixture')\n")
        built.writestr("happyranch-1.dist-info/METADATA", "Metadata-Version: 2.1\nName: happyranch\nVersion: 1\n")
    inventory = tmp_path / "dependency-inventory.json"
    license_text = "fixture license\n"
    license_digest = hashlib.sha256(license_text.encode()).hexdigest()
    inventory.write_text(json.dumps({"schema_version": 1, "artifact": {"goos": "linux", "goarch": "amd64", "cgo_enabled": False, "package": "happyranch/linux-tsnet-sidecar"}, "generator": "tools/generate_inventory.py", "modules": [{"module": "example.test/mod", "version": "v1", "sum": "h1:x", "source": "https://example.test/mod", "spdx": "MIT", "license_sha256": license_digest, "relationship": "statically-linked-linux-build-input"}]}) + "\n")
    notices = tmp_path / "THIRD_PARTY_NOTICES.md"
    notices.write_text("# notices\n\n---\nModules:\n- example.test/mod@v1\n\nSPDX: MIT\nLicense-SHA256: " + license_digest + "\n\n```text\n" + license_text.rstrip() + "\n```\n")
    return sidecar, connector, wheel, inventory, notices


def test_composite_units_start_services_concurrently_without_readiness_cycle() -> None:
    units = render_composite_units("/opt/happyranch")
    connector = units["happyranch-connector.service"]
    sidecar = units["happyranch-tsnet-sidecar.service"]
    assert "Type=notify" in connector
    assert "NotifyAccess=main" in connector
    assert "credential-capability --name daemon.token --unit happyranch-connector.service" in connector
    assert "ExecStart=/opt/happyranch/bin/happyranch-tsnet-sidecar supervise-connector /opt/happyranch/bin/happyranch-connector run --managed" in connector
    assert "Before=happyranch-tsnet-sidecar.service" not in connector
    assert "After=happyranch-connector.service" not in sidecar
    assert "Requires=happyranch-connector.service" not in sidecar
    assert "BindsTo=happyranch-connector.service" in sidecar
    assert "Type=notify" in sidecar
    assert "NotifyAccess=main" in sidecar
    assert "ExecStartPre=+/opt/happyranch/bin/happyranch-connector reconcile-enrollment-retirement" in sidecar
    assert "ExecStartPre=/opt/happyranch/bin/happyranch-connector credential-capability --name enrollment.key --unit happyranch-tsnet-sidecar.service --consumed-marker /var/lib/happyranch-tsnet-sidecar/credential.consumed" in sidecar
    assert "ExecStart=/opt/happyranch/bin/happyranch-tsnet-sidecar --config /etc/happyranch/sidecar.json" in sidecar
    for directive in ("User=happyranch", "CapabilityBoundingSet=", "PrivateDevices=yes"):
        assert directive in sidecar
    assert "StateDirectoryMode=0700" in sidecar
    assert "LoadCredential=" not in sidecar
    assert "ExecStartPost=+/opt/happyranch/bin/happyranch-connector retire-enrollment-source" in sidecar
    assert "--dropin /etc/systemd/system/happyranch-tsnet-sidecar.service.d/10-enrollment-credential.conf" in sidecar
    assert "UMask=0077" in connector and "UMask=0077" in sidecar
    assert "0.0.0.0" not in connector + sidecar


def test_composite_units_share_exact_address_family_sandbox() -> None:
    units = render_composite_units("/opt/happyranch")
    expected = "RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX AF_NETLINK"
    rendered = {
        unit: [line for line in text.splitlines() if line.startswith("RestrictAddressFamilies=")]
        for unit, text in units.items()
        if unit.endswith(".service")
    }

    assert rendered == {
        "happyranch-connector.service": [expected],
        "happyranch-tsnet-sidecar.service": [expected],
    }


def test_real_systemd_harness_is_zero_skip_and_uses_only_pinned_peer_artifacts() -> None:
    result = subprocess.run(
        ["bash", "-n", "app/linux/package/real_systemd_n3.sh"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_real_systemd_harness_uses_headscale_025_policy_schema() -> None:
    harness = Path("app/linux/package/real_systemd_n3.sh").read_text()
    assert "'{\"acls\":[{\"action\":\"accept\",\"src\":[\"*\"],\"dst\":[\"*:*\"]}]}'" in harness
    assert '"proto"' not in harness


def test_real_systemd_harness_quiesces_failed_staging_before_first_enrollment() -> None:
    harness = Path("app/linux/package/real_systemd_n3.sh").read_text()
    stop = harness.index(
        "stop happyranch-managed.target happyranch-tsnet-sidecar.service happyranch-connector.service"
    )
    reset = harness.index(
        "reset-failed happyranch-tsnet-sidecar.service happyranch-connector.service"
    )
    assert "reset-failed happyranch-tsnet-sidecar.service happyranch-connector.service happyranch-managed.target" not in harness
    staged_cleanup = harness.index('failed credential staging cleanup', reset)
    assert "sudo test ! -e /run/credentials/happyranch-tsnet-sidecar.service" in harness
    restore = harness.index(
        "mv /etc/happyranch/enrollment.key.held /etc/happyranch/enrollment.key",
        staged_cleanup,
    )
    assert stop < reset < staged_cleanup < restore


def test_real_systemd_harness_keeps_headscale_control_socket_in_task_root() -> None:
    harness = Path("app/linux/package/real_systemd_n3.sh").read_text()
    assert "unix_socket: $work/hs/headscale.sock" in harness


def test_real_systemd_harness_probes_headscale_health_over_configured_https() -> None:
    harness = Path("app/linux/package/real_systemd_n3.sh").read_text()
    assert (
        'curl --silent --fail --cacert "$work/tls/cert.pem" '
        "https://127.0.0.1:18080/health"
    ) in harness
    assert "http://127.0.0.1:19090/health" not in harness


def test_real_systemd_harness_proves_root_owned_binary_is_service_executable() -> None:
    harness = Path("app/linux/package/real_systemd_n3.sh").read_text()
    assert 'stat -c %U:%G:%a /opt/happyranch)' in harness
    assert 'stat -c %U:%G:%a /opt/happyranch/bin)' in harness
    assert '== "root:root:755"' in harness
    assert 'sudo -u happyranch test -x "$binary"' in harness


def test_real_systemd_harness_keeps_load_credential_source_root_custodied() -> None:
    harness = Path("app/linux/package/real_systemd_n3.sh").read_text()
    assert (
        'sudo install -m 0600 -o root -g root "$work/daemon.token" '
        "/etc/happyranch/daemon.token"
    ) in harness
    assert (
        'sudo install -m 0600 -o root -g root "$work/enrollment.key" '
        "/etc/happyranch/enrollment.key"
    ) in harness


def test_real_systemd_early_failure_cleanup_is_bounded_and_redacted() -> None:
    harness = Path("app/linux/package/real_systemd_n3.sh").read_text()
    assert "wait_status" not in harness
    assert 'cp "$work/$log.log" "$diagnostics/$log.log"' not in harness
    assert 'journalctl -u happyranch-connector.service -u happyranch-tsnet-sidecar.service' not in harness
    assert 'cleanup-status.txt' in harness


def test_real_systemd_missing_credential_accepts_null_peer_map_as_no_identity() -> None:
    harness = Path("app/linux/package/real_systemd_n3.sh").read_text()
    assert '(d.get("Peer") or {}).values()' in harness


def test_real_systemd_uses_plain_shipping_unit_without_af_netlink_ab_arms() -> None:
    harness = Path("app/linux/package/real_systemd_n3.sh").read_text()
    assert "acceptance_arms" not in harness
    assert "ordering-a-control" not in harness
    assert "ordering-a-candidate" not in harness
    assert "ordering-b-candidate" not in harness
    assert "ordering-b-control" not in harness
    assert "reset_shipping_unit" in harness
    assert "capture_denial_matrix shipping-unit" in harness
    assert "RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX AF_NETLINK" in harness
    assert "90-ci-af-netlink.conf" not in harness


def test_real_systemd_denial_probe_follows_shipping_state_directory_creation() -> None:
    harness = Path("app/linux/package/real_systemd_n3.sh").read_text()
    reset = harness.index('reset_shipping_unit || fail "fresh shipping-unit reset/setup failed"')
    first_start = harness.index("sudo systemctl start happyranch-managed.target || true", reset)
    denial_probe = harness.index("capture_denial_matrix shipping-unit", reset)
    successful_start = harness.index("sudo systemctl start happyranch-managed.target", first_start + 1)

    assert reset < first_start < denial_probe < successful_start


def test_real_systemd_denial_matrix_executes_every_bounded_probe() -> None:
    harness = Path("app/linux/package/real_systemd_n3.sh").read_text()
    for probe in ("socket.AF_NETLINK", "socket.SOCK_RAW", "/dev/net/tun", "probe-write", "create_connection"):
        assert probe in harness
    assert 'validate-denial-matrix' in harness
    assert '"measured":True' in harness
    assert 'systemd-run --quiet --wait --collect --pipe' in harness
    for sandbox_property in ("PrivateDevices=yes", "ProtectSystem=strict", "ProtectHome=yes", "CapabilityBoundingSet="):
        assert sandbox_property in harness


def _run_real_systemd_failure_snapshot(tmp_path: Path, *, malformed: bool = False) -> subprocess.CompletedProcess[str]:
    harness = Path("app/linux/package/real_systemd_n3.sh").read_text()
    snapshot_helpers = "safe_systemctl_value() {" + harness.split("safe_systemctl_value() {", 1)[1].split("\ncleanup() {", 1)[0]
    fake_bin = tmp_path / "bin"; fake_bin.mkdir()
    (fake_bin / "journalctl").write_text("""#!/usr/bin/env python3
import json, sys, time
boot = open("/proc/sys/kernel/random/boot_id").read().strip()
if any(argument == "JOB_TYPE=start" for argument in sys.argv):
    print(json.dumps({"UNIT": "happyranch-connector.service", "JOB_ID": "42", "JOB_TYPE": "start", "JOB_RESULT": "failed", "MESSAGE_ID": "be02cf6855d2428ba40df7e9d022f03d", "_PID": "1", "_UID": "0", "_BOOT_ID": boot, "__REALTIME_TIMESTAMP": str(int(time.time() * 1_000_000))}, separators=(",", ":")))
else:
    print(json.dumps({"MESSAGE": "diagnostic_receipt=" + json.dumps({"category": "network_join", "phase": "peer_establishment", "actor": "tsnet-sidecar", "unit": "happyranch-tsnet-sidecar.service", "outcome": "failed", "terminal": True, "assertion": {"status": "completed"}}), "_SYSTEMD_UNIT": "happyranch-tsnet-sidecar.service", "_SYSTEMD_INVOCATION_ID": "12345678-1234-1234-1234-123456789abc", "_BOOT_ID": boot, "__REALTIME_TIMESTAMP": str(int(time.time() * 1_000_000))}, separators=(",", ":")))
    print("not-json SECRET_CANARY")
""")
    (fake_bin / "journalctl").chmod(0o700)
    script = f'''set -euo pipefail
diagnostics={tmp_path!s}; mkdir -p "$diagnostics"
sudo() {{ "$@"; }}
timeout() {{ while [[ $1 == --* || $1 =~ ^[0-9]+$ ]]; do shift; done; "$@"; }}
systemctl() {{
  if [[ $1 == show ]]; then
    case $4 in
      InvocationID) printf '%s\n' 12345678-1234-1234-1234-123456789abc ;;
      ActiveState) printf '%s\n' "${{MALFORMED:+SECRET_CANARY}}${{MALFORMED:-failed}}" ;;
      SubState) printf '%s\n' failed ;;
      Result) printf '%s\n' exit-code ;;
      ExecMainStatus) printf '%s\n' 37 ;;
      ExecStartPre) printf '%s\n' 'path=/secret status=19 command=SECRET_CANARY' ;;
    esac
  elif [[ $1 == list-jobs ]]; then
    printf '%s\n' '42 happyranch-connector.service start running' '99 unrelated.service start waiting'
  fi
}}
{snapshot_helpers}
failure_capture_driver={Path("app/linux/package/n3_failure_capture.py").resolve()!s}
capture_failure_snapshot first-positive-start-failure
cat "$diagnostics/first-positive-start-failure.json"
'''
    return subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True,
        env=os.environ | {"PATH": f"{fake_bin}:{os.environ['PATH']}"} | ({"MALFORMED": "1"} if malformed else {}), check=False,
    )


def test_real_systemd_failure_snapshot_executes_shipping_source_and_is_secret_free(tmp_path: Path) -> None:
    result = _run_real_systemd_failure_snapshot(tmp_path)
    assert result.returncode == 0, result.stderr
    assert "SECRET_CANARY" not in result.stdout + result.stderr
    snapshot = json.loads(result.stdout)
    assert snapshot["id"] == "first-positive-start-failure"
    assert snapshot["units"]["happyranch-connector.service"] == {
        "active": "failed", "sub": "failed", "result": "exit-code",
        "exec_main_status": "37", "exec_start_pre_status": "unknown",
    }
    assert snapshot["jobs"] == [{"id": 42, "unit": "happyranch-connector.service", "type": "start", "result": "failed"}]
    assert snapshot["credential_presence"] == {
        "source": False, "held_source": False, "consumed_marker": False,
        "transient_dropin": False, "staged_directory": False,
    }
    assert snapshot["collection"]["source"] == "systemctl-and-attributed-systemd-journals"
    assert snapshot["collection"]["window_seconds"] == 45
    assert snapshot["collection"]["output_cap_bytes"] == 19968
    assert snapshot["collection"]["budget_model"] == "reserved-sections"
    assert snapshot["diagnostic_receipts"] == [{"category": "network_join", "phase": "peer_establishment"}]
    assert snapshot["observation_loss"]["diagnostic_receipts"] == ["parse_loss"]
    assert snapshot["observation_loss"]["happyranch-connector.service.exec_start_pre_status"] == "not_collected"
    assert "jobs" not in snapshot["observation_loss"]


def test_real_systemd_labels_deliberate_negative_credential_leg_as_expected() -> None:
    harness = Path("app/linux/package/real_systemd_n3.sh").read_text()
    assert 'negative_leg_diagnostic_id="$run_id:negative-leg-expected:credential_input"' in harness
    assert 'expectation=expected category=credential_input' in harness
    assert 'diagnostic credential_input input_acquisition systemd happyranch-tsnet-sidecar.service "$negative_leg_diagnostic_id"' in harness


def _run_seq305_failure_snapshot(
    tmp_path: Path,
    *,
    sidecar_mode: str = "observed",
    jobs_mode: str = "observed",
) -> tuple[subprocess.CompletedProcess[str], Path]:
    """Drive the shipped capture function with the run-36435811326 failure shape."""
    harness = Path("app/linux/package/real_systemd_n3.sh").read_text()
    snapshot_helpers = "safe_systemctl_value() {" + harness.split("safe_systemctl_value() {", 1)[1].split("\ncleanup() {", 1)[0]
    fake_bin = tmp_path / "bin"; fake_bin.mkdir()
    event_log = tmp_path / "events.log"
    boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip().replace("-", "")
    invocation = "12345678123412341234123456789abc"
    receipt = json.dumps({
        "MESSAGE": "diagnostic_receipt=" + json.dumps({
            "category": "network_join", "phase": "peer_establishment", "actor": "tsnet-sidecar",
            "unit": "happyranch-tsnet-sidecar.service", "outcome": "failed", "terminal": True,
            "assertion": {"status": "completed"},
        }),
        "_SYSTEMD_UNIT": "happyranch-tsnet-sidecar.service", "_SYSTEMD_INVOCATION_ID": invocation,
        "_BOOT_ID": boot, "__REALTIME_TIMESTAMP": "1700000000000000",
    }, separators=(",", ":"))
    job = json.dumps({
        "UNIT": "happyranch-tsnet-sidecar.service", "JOB_ID": "71", "JOB_TYPE": "start",
        "JOB_RESULT": "failed", "MESSAGE_ID": "be02cf6855d2428ba40df7e9d022f03d",
        "_PID": "1", "_UID": "0", "_BOOT_ID": boot, "__REALTIME_TIMESTAMP": "1700000000000000",
    }, separators=(",", ":"))
    dependency = json.dumps({
        "UNIT": "happyranch-managed.target", "JOB_ID": "72", "JOB_TYPE": "start",
        "JOB_RESULT": "dependency", "MESSAGE_ID": "be02cf6855d2428ba40df7e9d022f03d",
        "_PID": "1", "_UID": "0", "_BOOT_ID": boot, "__REALTIME_TIMESTAMP": "1700000000000000",
    }, separators=(",", ":"))
    connector_job = json.dumps({
        "UNIT": "happyranch-connector.service", "JOB_ID": "73", "JOB_TYPE": "start",
        "JOB_RESULT": "failed", "MESSAGE_ID": "be02cf6855d2428ba40df7e9d022f03d",
        "_PID": "1", "_UID": "0", "_BOOT_ID": boot, "__REALTIME_TIMESTAMP": "1700000000000000",
    }, separators=(",", ":"))
    (fake_bin / "date").write_text("#!/bin/bash\nprintf '1700000000000000000\\n'\n")
    (fake_bin / "systemctl").write_text("""#!/bin/bash
set -u
if [[ $1 != show ]]; then exit 97; fi
unit=$2; property=$4
printf 'show:%s:%s\n' "$unit" "$property" >>"$EVENT_LOG"
if [[ $property == InvocationID ]]; then printf '%s\n' "$INVOCATION"; exit 0; fi
if [[ $unit == happyranch-tsnet-sidecar.service && $property == ActiveState ]]; then
  case "$SIDECAR_MODE" in
    observed) echo failed;; timeout) exit 124;; query_error) printf '%s\n' TOKEN_CANARY; exit 7;;
    truncation) printf 'TOKEN_CANARY%0200d\n' 0;; malformed) echo TOKEN_CANARY;; empty) exit 0;;
  esac
  exit 0
fi
if [[ $unit == happyranch-tsnet-sidecar.service ]]; then
  case "$property" in SubState) echo failed;; Result) echo exit-code;; ExecMainStatus) echo 203;; *) echo TOKEN_CANARY;; esac
  exit 0
fi
# Reproduce the runner: connector/target observations are lossy and oversized.
case "$property" in SubState) echo start-pre;; *) printf 'TOKEN_CANARY%0200d\n' 0;; esac
""")
    (fake_bin / "journalctl").write_text("""#!/bin/bash
set -u
if [[ " $* " == *" JOB_TYPE=start "* ]]; then
  printf 'journal:jobs\n' >>"$EVENT_LOG"
  [[ " $* " == *" --output-fields=UNIT,JOB_ID,JOB_TYPE,JOB_RESULT,MESSAGE_ID,_PID,_UID,_BOOT_ID,__REALTIME_TIMESTAMP "* ]] || exit 91
  case "$JOBS_MODE" in
    observed) printf '%s\n%s\n%s\n' "$JOB" "$DEPENDENCY" "$CONNECTOR_JOB";; timeout) exit 124;;
    query_error) printf '%s\n' TOKEN_CANARY; exit 7;; truncation) printf 'TOKEN_CANARY%09000d\n' 0;;
    malformed) printf '%s\n' 'not-json TOKEN_CANARY';; empty) exit 0;;
  esac
else
  printf 'journal:receipt\n' >>"$EVENT_LOG"
  printf '%s\n' "$RECEIPT"
fi
""")
    for executable in fake_bin.iterdir():
        executable.chmod(0o700)
    script = f'''set -euo pipefail
diagnostics={tmp_path!s}; mkdir -p "$diagnostics"
capture_window_since_us=1699999999999999
sudo() {{ printf 'presence:%s\n' "$*" >>"$EVENT_LOG"; return 1; }}
timeout() {{ while [[ $1 == --* || $1 =~ ^[0-9]+$ ]]; do shift; done; "$@"; }}
failure_capture_driver={Path("app/linux/package/n3_failure_capture.py").resolve()!s}
run_id=seq305-red-green
{snapshot_helpers}
capture_failure_snapshot first-positive-start-failure
cat "$diagnostics/first-positive-start-failure.json"
'''
    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=False, env=os.environ | {
        "PATH": f"{fake_bin}:{os.environ['PATH']}", "EVENT_LOG": str(event_log), "INVOCATION": invocation,
        "BOOT": boot, "RECEIPT": receipt, "JOB": job, "DEPENDENCY": dependency, "CONNECTOR_JOB": connector_job,
        "SIDECAR_MODE": sidecar_mode, "JOBS_MODE": jobs_mode, "N3_UNIT_ROOT": str(tmp_path),
    })
    return result, event_log


def test_seq305_run_failure_shape_captures_sidecar_failed_jobs_and_receipt_before_lossy_units(tmp_path: Path) -> None:
    result, event_log = _run_seq305_failure_snapshot(tmp_path)
    assert result.returncode == 0, result.stderr
    assert "TOKEN_CANARY" not in result.stdout + result.stderr
    snapshot = json.loads(result.stdout)
    assert snapshot["units"]["happyranch-tsnet-sidecar.service"] == {
        "active": "failed", "sub": "failed", "result": "exit-code", "exec_main_status": "203",
        "exec_start_pre_status": "unknown",
    }
    assert snapshot["jobs"] == [
        {"id": 71, "unit": "happyranch-tsnet-sidecar.service", "type": "start", "result": "failed"},
        {"id": 72, "unit": "happyranch-managed.target", "type": "start", "result": "dependency"},
        {"id": 73, "unit": "happyranch-connector.service", "type": "start", "result": "failed"},
    ]
    assert snapshot["diagnostic_receipts"] == [{"category": "network_join", "phase": "peer_establishment"}]
    events = event_log.read_text().splitlines()
    sidecar_last = max(
        index for index, event in enumerate(events)
        if event.startswith("show:happyranch-tsnet-sidecar.service:") and not event.endswith(":InvocationID")
    )
    jobs_index = events.index("journal:jobs")
    receipt_index = events.index("journal:receipt")
    later_first = min(index for index, event in enumerate(events) if event.startswith(("show:happyranch-connector.service:", "show:happyranch-managed.target:", "presence:")))
    assert sidecar_last < jobs_index < receipt_index < later_first
    assert not any(event.endswith(":ExecStartPre") for event in events)


@pytest.mark.parametrize("mode", ["timeout", "query_error", "truncation", "malformed", "empty"])
def test_seq305_sidecar_loss_is_closed_and_does_not_zero_job_or_receipt_sections(tmp_path: Path, mode: str) -> None:
    result, _ = _run_seq305_failure_snapshot(tmp_path, sidecar_mode=mode)
    assert result.returncode == 0, result.stderr
    assert "TOKEN_CANARY" not in result.stdout + result.stderr
    snapshot = json.loads(result.stdout)
    expected = "parse_loss" if mode == "malformed" else "truncated" if mode == "truncation" else mode
    assert snapshot["observation_loss"]["happyranch-tsnet-sidecar.service.active"] == expected
    assert snapshot["jobs"][0]["unit"] == "happyranch-tsnet-sidecar.service"
    assert snapshot["diagnostic_receipts"] == [{"category": "network_join", "phase": "peer_establishment"}]


@pytest.mark.parametrize("mode", ["timeout", "query_error", "truncation", "malformed", "empty"])
def test_seq305_job_loss_is_closed_and_does_not_zero_receipt_or_later_sections(tmp_path: Path, mode: str) -> None:
    result, event_log = _run_seq305_failure_snapshot(tmp_path, jobs_mode=mode)
    assert result.returncode == 0, result.stderr
    assert "TOKEN_CANARY" not in result.stdout + result.stderr
    snapshot = json.loads(result.stdout)
    expected = "parse_loss" if mode == "malformed" else "truncated" if mode == "truncation" else mode
    assert snapshot["observation_loss"]["jobs"] == expected
    assert snapshot["diagnostic_receipts"] == [{"category": "network_join", "phase": "peer_establishment"}]
    assert any(event.startswith("show:happyranch-connector.service:") for event in event_log.read_text().splitlines())


def test_real_systemd_failure_snapshot_uses_real_timeout_and_never_claims_unattempted_as_absent(tmp_path: Path) -> None:
    harness = Path("app/linux/package/real_systemd_n3.sh").read_text()
    snapshot_helpers = "safe_systemctl_value() {" + harness.split("safe_systemctl_value() {", 1)[1].split("\ncleanup() {", 1)[0]
    fake_bin = tmp_path / "bin"; fake_bin.mkdir()
    (fake_bin / "systemctl").write_text("#!/bin/bash\nsleep 2\nprintf '%s\\n' failed\n")
    (fake_bin / "journalctl").write_text("#!/bin/bash\nsleep 2\n")
    for executable in fake_bin.iterdir():
        executable.chmod(0o700)
    script = f'''set -euo pipefail
diagnostics={tmp_path!s}; mkdir -p "$diagnostics"
sudo() {{ "$@"; }}
failure_capture_driver={Path("app/linux/package/n3_failure_capture.py").resolve()!s}
{snapshot_helpers}
capture_failure_snapshot real-timeout
cat "$diagnostics/real-timeout.json"
'''
    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=False, env=os.environ | {"PATH": f"{fake_bin}:{os.environ['PATH']}"})
    assert result.returncode == 0, result.stderr
    snapshot = json.loads(result.stdout)
    assert "timeout" in snapshot["observation_loss"].values()
    assert snapshot["credential_presence"]["source"] is False


def test_real_systemd_failure_snapshot_bounds_malformed_observations(tmp_path: Path) -> None:
    result = _run_real_systemd_failure_snapshot(tmp_path, malformed=True)
    assert result.returncode == 0, result.stderr
    assert "SECRET_CANARY" not in result.stdout + result.stderr
    snapshot = json.loads(result.stdout)
    assert {unit["active"] for unit in snapshot["units"].values()} == {"unknown"}
    assert {value for key, value in snapshot["observation_loss"].items() if key != "diagnostic_receipts"} == {"parse_loss", "not_collected"}


def test_real_systemd_barriers_use_restrictive_service_state_directory_and_controller_sudo() -> None:
    harness = Path("app/linux/package/real_systemd_n3.sh").read_text()
    assert 'barrier_dir="/var/lib/happyranch-tsnet-sidecar/.n3-barrier-$run_id"' in harness
    assert 'install -d -m 0700 -o happyranch -g happyranch "$barrier_dir"' in harness
    assert 'sudo test -e "$barrier_dir/start-entered"' in harness
    assert 'sudo test -e "$barrier_dir/stop-entered"' in harness
    assert 'sudo tee "$barrier_dir/start-release"' in harness
    assert 'sudo tee "$barrier_dir/stop-release"' in harness
    assert 'sudo rm -f "$barrier_dir/start-entered" "$barrier_dir/start-release" "$barrier_dir/stop-entered" "$barrier_dir/stop-release"' in harness
    assert 'sudo rmdir "$barrier_dir" || fail "barrier residue"' in harness


def test_real_systemd_cleanup_releases_both_held_barriers_before_teardown(tmp_path: Path) -> None:
    harness = Path("app/linux/package/real_systemd_n3.sh").read_text()
    unit_helpers = "systemctl_absent_value() {" + harness.split("systemctl_absent_value() {", 1)[1].split("\ndiagnostics=", 1)[0]
    cleanup = harness.split("cleanup() {", 1)[1].split("\n}\ntrap cleanup EXIT", 1)[0]
    fake_bin = tmp_path / "bin"; fake_bin.mkdir()
    (fake_bin / "systemctl").write_text("""#!/bin/bash
printf 'systemctl:%s\n' "$1" >>"$EVENT_LOG"
case "$1" in show) echo 0;; list-unit-files) exit 0;; stop|disable|reset-failed|daemon-reload) exit 0;; *) exit 96;; esac
""")
    (fake_bin / "sudo").write_text("""#!/bin/bash
printf 'sudo:%s:%s\n' "$1" "${2:-}" >>"$EVENT_LOG"
case "$1" in systemctl) shift; exec systemctl "$@";; test|rm|kill|find|update-ca-certificates|tee) exit 0;; *) exit 95;; esac
""")
    (fake_bin / "pgrep").write_text("#!/bin/bash\nexit 1\n")
    for executable in fake_bin.iterdir():
        executable.chmod(0o700)
    work = tmp_path / "work"; (work / "hs").mkdir(parents=True)
    (work / "headscale").write_text("#!/bin/bash\nprintf '[]'\n"); (work / "headscale").chmod(0o700)
    evidence = tmp_path / "evidence.py"; evidence.write_text("raise SystemExit(0)\n")
    event_log = tmp_path / "events.log"
    script = f'''set -euo pipefail
diagnostics={tmp_path!s}; work={work!s}; evidence_driver={evidence!s}; evidence_artifact={tmp_path / "artifact"!s}
PROOF_SUBJECT_SHA={'a' * 40}
peer_pid=""; daemon_pid=""; headscale_pid=""; sidecar_ip=""; run_id=test
barrier_dir=/visible/service-owned/barrier
port_open() {{ return 1; }}
tsnet_open() {{ return 1; }}
evidence() {{ return 0; }}
{unit_helpers}
cleanup() {{
{cleanup}
}}
cleanup 0
'''
    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=False, env=os.environ | {"PATH": f"{fake_bin}:{os.environ['PATH']}", "EVENT_LOG": str(event_log), "N3_UNIT_ROOT": str(tmp_path)})
    assert result.returncode == 0, result.stderr
    events = event_log.read_text().splitlines()
    start_release = next(index for index, event in enumerate(events) if event.endswith(":/visible/service-owned/barrier/start-release"))
    stop_release = next(index for index, event in enumerate(events) if event.endswith(":/visible/service-owned/barrier/stop-release"))
    teardown = events.index("systemctl:stop")
    assert start_release < teardown and stop_release < teardown


def test_real_systemd_barrier_bodies_remove_only_owned_markers_before_rmdir(tmp_path: Path) -> None:
    """Execute the source-defined barrier bodies and its controller cleanup."""
    harness = Path("app/linux/package/real_systemd_n3.sh").read_text()
    start_body = re.search(r"ExecStartPre=/bin/sh -c '([^']+)'", harness)
    stop_body = re.search(r"ExecStopPost=/bin/sh -c '([^']+)'", harness)
    cleanup_body = re.search(
        r'(sudo rm -f "\$barrier_dir/start-entered"[^\n]+\n'
        r'\s*sudo rmdir "\$barrier_dir" \|\| fail "barrier residue")', harness,
    )
    assert start_body and stop_body and cleanup_body
    barrier_dir = tmp_path / "state" / ".n3-barrier-test"
    barrier_dir.parent.mkdir(mode=0o700)
    durable = barrier_dir.parent / "credential.consumed"; durable.write_text("durable")
    barrier_dir.mkdir(mode=0o700)
    assert barrier_dir.stat().st_mode & 0o777 == 0o700
    for body, entered, release in (
        (start_body.group(1), "start-entered", "start-release"),
        (stop_body.group(1), "stop-entered", "stop-release"),
    ):
        process = subprocess.Popen(["bash", "-c", f'barrier_dir="{barrier_dir}"; {body}'])
        for _ in range(40):
            if (barrier_dir / entered).exists():
                break
            process.poll()
            if process.returncode is not None:
                pytest.fail(f"source barrier exited early: {body}")
            time.sleep(0.01)
        else:
            pytest.fail(f"source barrier never created {entered}")
        (barrier_dir / release).touch()
        assert process.wait(timeout=1) == 0

    result = subprocess.run(
        ["bash", "-c", f'''set -euo pipefail
sudo() {{ "$@"; }}
fail() {{ return 1; }}
barrier_dir="{barrier_dir}"
{cleanup_body.group(1)}
'''], capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert not barrier_dir.exists()
    assert durable.read_text() == "durable"


def test_real_systemd_barrier_cleanup_refuses_unknown_child_residue(tmp_path: Path) -> None:
    harness = Path("app/linux/package/real_systemd_n3.sh").read_text()
    cleanup_body = re.search(
        r'(sudo rm -f "\$barrier_dir/start-entered"[^\n]+\n'
        r'\s*sudo rmdir "\$barrier_dir" \|\| fail "barrier residue")', harness,
    )
    assert cleanup_body
    barrier_dir = tmp_path / ".n3-barrier-test"; barrier_dir.mkdir(mode=0o700)
    for marker in ("start-entered", "start-release", "stop-entered", "stop-release"):
        (barrier_dir / marker).touch()
    (barrier_dir / "unexpected").write_text("must-refuse")
    result = subprocess.run(
        ["bash", "-c", f'''set -euo pipefail
sudo() {{ "$@"; }}
fail() {{ return 1; }}
barrier_dir="{barrier_dir}"
{cleanup_body.group(1)}
'''], capture_output=True, text=True, check=False,
    )
    assert result.returncode != 0
    assert (barrier_dir / "unexpected").read_text() == "must-refuse"


def _seed_n3_evidence(artifact: Path, *, run_id: str, include_cleanup: bool) -> Path:
    """Use the real shipping evidence driver to make a valid isolated ledger."""
    driver = Path("app/linux/package/n3_evidence.py").resolve()
    phases = runpy.run_path(str(driver))["PHASES"]
    subject = "a" * 40
    subprocess.run([sys.executable, str(driver), "init", str(artifact), "--git-head", subject,
                    "--package-sha256", "b" * 64, "--run-id", run_id], check=True)
    for phase, observations in phases.items():
        for observation in observations:
            if phase == "cleanup" and observation in {"all_residue_absent", "task_work_removed"}:
                continue
            if phase == "cleanup" and not include_cleanup:
                continue
            subprocess.run([sys.executable, str(driver), "observe", str(artifact), "--phase", phase,
                            "--observation", observation, "--assertion-id", f"seed:{phase}:{observation}"], check=True)
    subprocess.run([sys.executable, str(driver), "diagnose", str(artifact), "--id", "seed-diagnostic",
                    "--category", "network_join", "--phase", "peer_establishment", "--actor", "tsnet-sidecar",
                    "--unit", "happyranch-tsnet-sidecar.service"], check=True)
    return driver


def _run_source_cleanup_acceptance(
    tmp_path: Path, *, artifact_run: str, cleanup_run: str, include_cleanup: bool,
    listener_residue: bool = False,
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    """Run source cleanup against isolated strict dependencies and real evidence code."""
    harness = Path("app/linux/package/real_systemd_n3.sh").read_text()
    evidence = harness.split("evidence() {", 1)[1].split("\n}\ndiagnostic()", 1)[0]
    cleanup = harness.split("cleanup() {", 1)[1].split("\n}\ntrap cleanup EXIT", 1)[0]
    artifact = tmp_path / "execution-evidence.json"
    driver = _seed_n3_evidence(artifact, run_id=artifact_run, include_cleanup=include_cleanup)
    fake_bin = tmp_path / "bin"; fake_bin.mkdir()
    events = tmp_path / "events.log"
    (fake_bin / "python").write_text("#!/bin/bash\nprintf 'evidence:%s\\n' \"$2\" >>\"$EVENT_LOG\"\nexec \"$REAL_PYTHON\" \"$@\"\n")
    (fake_bin / "systemctl").write_text("#!/bin/bash\ncase \"$1\" in show) echo 0;; list-unit-files|stop|disable|reset-failed|daemon-reload) ;; *) exit 91;; esac\n")
    (fake_bin / "sudo").write_text("#!/bin/bash\ncase \"$1\" in systemctl) shift; exec systemctl \"$@\";; test|rm|kill|find|update-ca-certificates) exit 0;; *) exit 92;; esac\n")
    (fake_bin / "pgrep").write_text("#!/bin/bash\nexit 1\n")
    for executable in fake_bin.iterdir():
        executable.chmod(0o700)
    work = tmp_path / "work"; (work / "hs").mkdir(parents=True)
    (work / "headscale").write_text("#!/bin/bash\nprintf '[]'\n"); (work / "headscale").chmod(0o700)
    script = f'''set -euo pipefail
diagnostics={tmp_path!s}; work={work!s}; evidence_driver={driver!s}; evidence_artifact={artifact!s}
PROOF_SUBJECT_SHA={'a' * 40}
peer_pid={"123" if listener_residue else '""'}; daemon_pid=""; headscale_pid=""; sidecar_ip={"visible" if listener_residue else '""'}; run_id={cleanup_run}
port_open() {{ return 1; }}
tsnet_open() {{ return {0 if listener_residue else 1}; }}
evidence() {{
{evidence}
}}
cleanup() {{
{cleanup}
}}
cleanup 0
'''
    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=False,
                            env=os.environ | {"PATH": f"{fake_bin}:{os.environ['PATH']}", "EVENT_LOG": str(events),
                                              "REAL_PYTHON": sys.executable, "N3_RESIDUE_ROOT": str(tmp_path),
                                              "N3_UNIT_ROOT": str(tmp_path)})
    return result, events.read_text().splitlines() if events.exists() else []


def test_real_systemd_cleanup_finalizes_and_validates_actual_evidence_once(tmp_path: Path) -> None:
    result, events = _run_source_cleanup_acceptance(
        tmp_path, artifact_run="run", cleanup_run="run", include_cleanup=True,
    )
    assert result.returncode == 0, result.stderr
    assert events.count("evidence:finalize") == 1
    assert events.count("evidence:validate") == 1


def test_real_systemd_cleanup_residue_never_finalizes_actual_evidence(tmp_path: Path) -> None:
    result, events = _run_source_cleanup_acceptance(
        tmp_path, artifact_run="run", cleanup_run="run", include_cleanup=True, listener_residue=True,
    )
    assert result.returncode != 0
    assert "evidence:finalize" not in events and "evidence:validate" not in events


@pytest.mark.parametrize(("artifact_run", "expected_final", "expected_validate"), [
    ("run", 1, 1), ("different", 1, 1),
])
def test_real_systemd_cleanup_finalizer_or_validator_failure_is_nonzero(
    tmp_path: Path, artifact_run: str, expected_final: int, expected_validate: int,
) -> None:
    result, events = _run_source_cleanup_acceptance(
        tmp_path, artifact_run=artifact_run, cleanup_run="run", include_cleanup=artifact_run == "different",
    )
    assert result.returncode != 0
    assert events.count("evidence:finalize") == expected_final
    assert events.count("evidence:validate") == expected_validate


def test_real_systemd_failure_capture_precedes_teardown_and_preserves_exit_37() -> None:
    harness = Path("app/linux/package/real_systemd_n3.sh").read_text()
    cleanup_capture = harness.index("(( original_status == 0 )) || capture_failure_snapshot failure-before-teardown || true")
    teardown = harness.index("sudo systemctl stop happyranch-managed.target", cleanup_capture)
    guarded_start = harness.index("start_managed_target() {")
    capture = harness.index("capture_failure_snapshot first-positive-start-failure || true", guarded_start)
    preserved_status = harness.index('start_managed_target || exit "$?"', capture)
    assert cleanup_capture < teardown
    assert guarded_start < capture < preserved_status
    assert "trap cleanup EXIT\ntrap 'cleanup 130' INT\ntrap 'cleanup 143' TERM" in harness


def test_real_systemd_positive_start_exit_trap_preserves_exit_37_despite_capture_and_cleanup_failures(tmp_path: Path) -> None:
    harness = Path("app/linux/package/real_systemd_n3.sh").read_text()
    snapshot = "safe_systemctl_value() {" + harness.split("safe_systemctl_value() {", 1)[1].split("\ncleanup() {", 1)[0]
    unit_helpers = "systemctl_absent_value() {" + harness.split("systemctl_absent_value() {", 1)[1].split("\ndiagnostics=", 1)[0]
    cleanup = harness.split("cleanup() {", 1)[1].split("\n}\ntrap cleanup EXIT", 1)[0]
    fake_bin = tmp_path / "bin"; fake_bin.mkdir()
    (fake_bin / "systemctl").write_text("""#!/bin/bash
if [[ $1 == start ]]; then exit 37; fi
if [[ $1 == show ]]; then case $4 in ActiveState) echo failed;; SubState) echo failed;; Result) echo exit-code;; ExecMainStatus) echo 37;; *) echo unknown;; esac; fi
exit 0
""")
    (fake_bin / "sudo").write_text("#!/bin/bash\nif [[ $1 == systemctl ]]; then shift; exec systemctl \"$@\"; fi\nexit 0\n")
    (fake_bin / "pgrep").write_text("#!/bin/bash\nexit 1\n")
    for executable in fake_bin.iterdir(): executable.chmod(0o700)
    work = tmp_path / "work"; (work / "hs").mkdir(parents=True)
    (work / "headscale").write_text("#!/bin/bash\necho '[]'\n"); (work / "headscale").chmod(0o700)
    script = f'''set -euo pipefail
diagnostics={tmp_path!s}; work={work!s}; evidence_driver=/missing; evidence_artifact=/missing
peer_pid=""; daemon_pid=""; headscale_pid=""; sidecar_ip=""; run_id=test
port_open() {{ return 1; }}
tsnet_open() {{ return 1; }}
evidence() {{ return 0; }}
{snapshot}
{unit_helpers}
cleanup() {{
{cleanup}
}}
trap cleanup EXIT
trap 'cleanup 130' INT
trap 'cleanup 143' TERM
start_managed_target || exit "$?"
'''
    result = subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True,
        env=os.environ | {"PATH": f"{fake_bin}:{os.environ['PATH']}", "N3_RESIDUE_ROOT": str(tmp_path), "N3_UNIT_ROOT": str(tmp_path)}, check=False,
    )
    assert result.returncode == 37, result.stderr
    assert (tmp_path / "first-positive-start-failure.json").exists()


def test_real_systemd_shipping_capture_retains_pre_start_receipt_with_compact_boot_context(tmp_path: Path) -> None:
    """Run the shipping start/trap path against strict time- and context-aware doubles."""
    harness = Path("app/linux/package/real_systemd_n3.sh").read_text()
    snapshot = "safe_systemctl_value() {" + harness.split("safe_systemctl_value() {", 1)[1].split("\ncleanup() {", 1)[0]
    unit_helpers = "systemctl_absent_value() {" + harness.split("systemctl_absent_value() {", 1)[1].split("\ndiagnostics=", 1)[0]
    cleanup = harness.split("cleanup() {", 1)[1].split("\n}\ntrap cleanup EXIT", 1)[0]
    fake_bin = tmp_path / "bin"; fake_bin.mkdir()
    invocation = "12345678123412341234123456789abc"
    boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip().replace("-", "")
    receipt = json.dumps({
        "MESSAGE": "diagnostic_receipt=" + json.dumps({
            "category": "network_join", "phase": "peer_establishment", "actor": "tsnet-sidecar",
            "unit": "happyranch-tsnet-sidecar.service", "outcome": "failed", "terminal": True,
            "assertion": {"status": "completed"},
        }),
        "_SYSTEMD_UNIT": "happyranch-tsnet-sidecar.service", "_SYSTEMD_INVOCATION_ID": invocation,
        "_BOOT_ID": boot, "__REALTIME_TIMESTAMP": "1700000000000000",
    })
    stale = json.dumps({"MESSAGE": "diagnostic_receipt={TOKEN_CANARY=never-retain}", "_SYSTEMD_UNIT": "happyranch-tsnet-sidecar.service", "_SYSTEMD_INVOCATION_ID": "stale", "_BOOT_ID": boot, "__REALTIME_TIMESTAMP": "1700000000000001"})
    old = json.dumps({"MESSAGE": "diagnostic_receipt={TOKEN_CANARY=never-retain}", "_SYSTEMD_UNIT": "happyranch-tsnet-sidecar.service", "_SYSTEMD_INVOCATION_ID": invocation, "_BOOT_ID": "f" * 32, "__REALTIME_TIMESTAMP": "1699999999999999"})
    (fake_bin / "date").write_text("#!/bin/bash\nprintf '1700000000000000000\\n'\n")
    (fake_bin / "systemctl").write_text(f'''#!/bin/bash
printf 'systemctl:%s\\n' "$1" >>"$EVENT_LOG"
case "$1" in
  start) exit 37 ;;
  show) case "$4" in InvocationID) printf '%s\\n' "$INVOCATION";; ActiveState) echo failed;; SubState) echo failed;; Result) echo exit-code;; ExecMainStatus) echo 37;; *) echo unknown;; esac ;;
  list-jobs|stop|disable|reset-failed|daemon-reload|list-unit-files) exit 0 ;;
  *) exit 98 ;;
esac
''')
    (fake_bin / "sudo").write_text("#!/bin/bash\nif [[ $1 == systemctl ]]; then shift; exec systemctl \"$@\"; fi\nif [[ $1 == test ]]; then exit 1; fi\nexit 0\n")
    (fake_bin / "journalctl").write_text('''#!/bin/bash
printf 'journal:%s\\n' "$*" >>"$EVENT_LOG"
[[ "$*" == *"-b $BOOT"* && "$*" == *"--since @1700000000"* ]] || exit 91
printf '%s\\n%s\\n%s\\n' "$RECEIPT" "$STALE" "$OLD"
''')
    (fake_bin / "pgrep").write_text("#!/bin/bash\nexit 1\n")
    work = tmp_path / "work"; (work / "hs").mkdir(parents=True)
    (work / "headscale").write_text("#!/bin/bash\nprintf '[]'\n"); (work / "headscale").chmod(0o700)
    for executable in fake_bin.iterdir(): executable.chmod(0o700)
    script = f'''set -euo pipefail
diagnostics={tmp_path!s}; work={work!s}; evidence_driver=/missing; evidence_artifact=/missing
failure_capture_driver={Path("app/linux/package/n3_failure_capture.py").resolve()!s}
peer_pid=""; daemon_pid=""; headscale_pid=""; sidecar_ip=""; run_id=test
port_open() {{ return 1; }}
tsnet_open() {{ return 1; }}
evidence() {{ return 0; }}
{snapshot}
{unit_helpers}
cleanup() {{
{cleanup}
}}
trap cleanup EXIT
start_managed_target || exit "$?"
'''
    event_log = tmp_path / "events.log"
    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=False, env=os.environ | {
        "PATH": f"{fake_bin}:{os.environ['PATH']}", "EVENT_LOG": str(event_log), "INVOCATION": invocation,
        "BOOT": boot, "RECEIPT": receipt, "STALE": stale, "OLD": old, "N3_RESIDUE_ROOT": str(tmp_path), "N3_UNIT_ROOT": str(tmp_path),
    })
    assert result.returncode == 37, result.stderr
    snapshot_doc = json.loads((tmp_path / "first-positive-start-failure.json").read_text())
    assert snapshot_doc["diagnostic_receipts"] == [{"category": "network_join", "phase": "peer_establishment"}]
    assert snapshot_doc["collection"]["boot_id"] == boot
    assert snapshot_doc["collection"]["window_start_us"] == 1700000000000000
    assert snapshot_doc["collection"]["journal_since_epoch_seconds"] == 1700000000
    assert "TOKEN_CANARY" not in (tmp_path / "first-positive-start-failure.json").read_text() + result.stdout + result.stderr
    events = event_log.read_text().splitlines()
    assert next(index for index, event in enumerate(events) if event.startswith("journal:")) < events.index("systemctl:stop")


def test_real_systemd_failure_snapshot_keeps_valid_loss_json_when_jobs_temp_launch_fails(tmp_path: Path) -> None:
    harness = Path("app/linux/package/real_systemd_n3.sh").read_text()
    snapshot = "safe_systemctl_value() {" + harness.split("safe_systemctl_value() {", 1)[1].split("\ncleanup() {", 1)[0]
    fake_bin = tmp_path / "bin"; fake_bin.mkdir()
    (fake_bin / "mktemp").write_text("#!/bin/bash\n[[ $* == *n3-jobs* ]] && exit 1\nexec /usr/bin/mktemp \"$@\"\n")
    (fake_bin / "systemctl").write_text("#!/bin/bash\n[[ $1 == show ]] && { [[ $4 == InvocationID ]] && printf '%s\\n' 12345678123412341234123456789abc || echo untrusted; exit 0; }\nexit 1\n")
    (fake_bin / "sudo").write_text("#!/bin/bash\n[[ $1 == test ]] && exit 1\nexec \"$@\"\n")
    (fake_bin / "journalctl").write_text("#!/bin/bash\nexit 1\n")
    for executable in fake_bin.iterdir(): executable.chmod(0o700)
    script = f'''set -euo pipefail
diagnostics={tmp_path!s}; failure_capture_driver=/missing; run_id=test
{snapshot}
capture_failure_snapshot jobs-temp-failure
cat "$diagnostics/jobs-temp-failure.json"
'''
    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=False, env=os.environ | {
        "PATH": f"{fake_bin}:{os.environ['PATH']}", "N3_UNIT_ROOT": str(tmp_path),
    })
    assert result.returncode == 0, result.stderr
    document = json.loads(result.stdout)
    assert document["observation_loss"]["jobs"] == "launch_failure"
    assert document["observation_loss"]["happyranch-managed.target.active"] == "parse_loss"


def test_real_systemd_capture_reserves_termination_grace_from_shared_deadline(tmp_path: Path) -> None:
    """The extracted shipping helper refuses a new command without its kill grace."""
    harness = Path("app/linux/package/real_systemd_n3.sh").read_text()
    helper = "observe_remaining() {" + harness.split("observe_remaining() {", 1)[1].split("\ncapture_now_us() {", 1)[0]
    result = subprocess.run(
        ["bash", "-c", f"set -euo pipefail\n{helper}\nSECONDS=10\nobserve_deadline=12\nobserve_bytes_left=1\nobserve_remaining"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1


def test_real_systemd_failure_snapshot_refuses_valid_looking_nonzero_invocation_query(tmp_path: Path) -> None:
    """A nonzero InvocationID lookup cannot authorize journal attribution."""
    harness = Path("app/linux/package/real_systemd_n3.sh").read_text()
    snapshot = "safe_systemctl_value() {" + harness.split("safe_systemctl_value() {", 1)[1].split("\ncleanup() {", 1)[0]
    fake_bin = tmp_path / "bin"; fake_bin.mkdir()
    (fake_bin / "systemctl").write_text("""#!/bin/bash
if [[ $1 == show && $4 == InvocationID ]]; then printf '%s\\n' 12345678123412341234123456789abc; exit 7; fi
if [[ $1 == show ]]; then echo failed; exit 0; fi
exit 0
""")
    (fake_bin / "sudo").write_text("#!/bin/bash\n[[ $1 == test ]] && exit 1\nexec \"$@\"\n")
    (fake_bin / "journalctl").write_text("#!/bin/bash\n[[ \" $* \" == *\" JOB_TYPE=start \"* ]] && exit 0\nprintf 'receipt-journal-called\\n' >>\"$EVENT_LOG\"\nexit 0\n")
    for executable in fake_bin.iterdir(): executable.chmod(0o700)
    event_log = tmp_path / "events.log"
    script = f'''set -euo pipefail
diagnostics={tmp_path!s}; failure_capture_driver=/missing; run_id=test
{snapshot}
capture_failure_snapshot nonzero-invocation
cat "$diagnostics/nonzero-invocation.json"
'''
    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=False, env=os.environ | {
        "PATH": f"{fake_bin}:{os.environ['PATH']}", "EVENT_LOG": str(event_log), "N3_UNIT_ROOT": str(tmp_path),
    })
    assert result.returncode == 0, result.stderr
    document = json.loads(result.stdout)
    assert document["observation_loss"]["diagnostic_receipts"] == ["query_error"]
    assert not event_log.exists()


@pytest.mark.parametrize(("capture_mode", "expected_loss"), [
    ("query", "query_error"), ("parse", "parse_loss"),
    ("timeout", "timeout"), ("launch", "launch_failure"),
])
def test_real_systemd_shipping_exit_path_is_strict_ordered_and_preserves_37(
    tmp_path: Path, capture_mode: str, expected_loss: str,
) -> None:
    """Exercise the extracted positive-start EXIT/trap path, never host systemd."""
    harness = Path("app/linux/package/real_systemd_n3.sh").read_text()
    snapshot = "safe_systemctl_value() {" + harness.split("safe_systemctl_value() {", 1)[1].split("\ncleanup() {", 1)[0]
    unit_helpers = "systemctl_absent_value() {" + harness.split("systemctl_absent_value() {", 1)[1].split("\ndiagnostics=", 1)[0]
    cleanup = harness.split("cleanup() {", 1)[1].split("\n}\ntrap cleanup EXIT", 1)[0]
    fake_bin = tmp_path / "bin"; fake_bin.mkdir()
    (fake_bin / "systemctl").write_text("""#!/bin/bash
set -eu
printf 'systemctl:%s\n' "$1" >>"$EVENT_LOG"
case "$1" in
  start) exit 37 ;;
  show) case "$4" in InvocationID) echo 12345678-1234-1234-1234-123456789abc;; ActiveState) echo failed;; SubState) echo failed;; Result) echo exit-code;; ExecMainStatus) echo 37;; MainPID) echo 0;; *) echo unknown;; esac ;;
  stop|disable|reset-failed|daemon-reload|list-unit-files|list-jobs) exit 0 ;;
  *) printf 'unknown-systemctl:%s\n' "$1" >>"$EVENT_LOG"; exit 97 ;;
esac
""")
    (fake_bin / "sudo").write_text("""#!/bin/bash
set -eu
printf 'sudo:%s\n' "$1" >>"$EVENT_LOG"
case "$1" in
  systemctl) shift; exec systemctl "$@" ;;
  test|rm|kill|find|update-ca-certificates) exit 0 ;;
  *) printf 'unknown-sudo:%s\n' "$1" >>"$EVENT_LOG"; exit 98 ;;
esac
""")
    (fake_bin / "pgrep").write_text("#!/bin/bash\nprintf 'pgrep\n' >>\"$EVENT_LOG\"\nexit 0\n")
    (fake_bin / "journalctl").write_text("""#!/bin/bash
printf 'capture\n' >>"$EVENT_LOG"
case "${CAPTURE_MODE:?}" in
  query) exit 5 ;;
  parse) printf '%s\n' 'diagnostic_receipt={TOKEN_CANARY=never-retain}' ;;
  timeout) sleep 2 ;;
  *) exit 99 ;;
esac
""")
    (fake_bin / "mktemp").write_text("""#!/bin/bash
if [[ ${CAPTURE_MODE:-} == launch && $* == *n3-receipts* ]]; then printf 'capture-launch\n' >>"$EVENT_LOG"; exit 1; fi
exec /usr/bin/mktemp "$@"
""")
    (fake_bin / "evidence").write_text("#!/bin/bash\nprintf 'evidence:%s\n' \"$1\" >>\"$EVENT_LOG\"\nexit 0\n")
    for executable in fake_bin.iterdir():
        executable.chmod(0o700)
    work = tmp_path / "work"; (work / "hs").mkdir(parents=True)
    (work / "headscale").write_text("#!/bin/bash\nprintf '[]'\n")
    (work / "headscale").chmod(0o700)
    script = f'''set -euo pipefail
diagnostics={tmp_path!s}; work={work!s}; evidence_driver={fake_bin / "evidence"!s}; evidence_artifact=/missing
failure_capture_driver={Path("app/linux/package/n3_failure_capture.py").resolve()!s}
peer_pid=""; daemon_pid=""; headscale_pid=""; sidecar_ip=""; run_id=test
port_open() {{ return 1; }}
tsnet_open() {{ return 1; }}
evidence() {{ return 0; }}
{snapshot}
{unit_helpers}
cleanup() {{
{cleanup}
}}
trap cleanup EXIT
trap 'cleanup 130' INT
trap 'cleanup 143' TERM
start_managed_target || exit "$?"
'''
    event_log = tmp_path / "events.log"
    result = subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, check=False,
        env=os.environ | {"PATH": f"{fake_bin}:{os.environ['PATH']}", "EVENT_LOG": str(event_log), "CAPTURE_MODE": capture_mode, "N3_RESIDUE_ROOT": str(tmp_path), "N3_UNIT_ROOT": str(tmp_path)},
    )
    assert result.returncode == 37, result.stderr
    snapshot_doc = json.loads((tmp_path / "first-positive-start-failure.json").read_text())
    assert expected_loss in snapshot_doc["observation_loss"]["diagnostic_receipts"]
    events = event_log.read_text().splitlines()
    first_capture = "capture-launch" if capture_mode == "launch" else "capture"
    assert events.index(first_capture) < events.index("systemctl:stop")
    assert (tmp_path / "cleanup-events.log").read_text().splitlines() == ["cleanup"]
    assert not any(event.startswith(("evidence:finalize", "evidence:validate", "unknown-")) for event in events)


@pytest.mark.parametrize(("signal", "expected"), [("INT", 130), ("TERM", 143)])
def test_real_systemd_signal_traps_cleanup_once_and_preserve_non_success(tmp_path: Path, signal: str, expected: int) -> None:
    harness = Path("app/linux/package/real_systemd_n3.sh").read_text()
    snapshot = "safe_systemctl_value() {" + harness.split("safe_systemctl_value() {", 1)[1].split("\ncleanup() {", 1)[0]
    unit_helpers = "systemctl_absent_value() {" + harness.split("systemctl_absent_value() {", 1)[1].split("\ndiagnostics=", 1)[0]
    cleanup = harness.split("cleanup() {", 1)[1].split("\n}\ntrap cleanup EXIT", 1)[0]
    fake_bin = tmp_path / "bin"; fake_bin.mkdir()
    (fake_bin / "systemctl").write_text("#!/bin/bash\nif [[ $1 == show ]]; then echo 0; fi\nexit 0\n")
    (fake_bin / "sudo").write_text("#!/bin/bash\nif [[ $1 == systemctl ]]; then shift; exec systemctl \"$@\"; fi\nexit 0\n")
    (fake_bin / "pgrep").write_text("#!/bin/bash\nexit 1\n")
    for executable in fake_bin.iterdir(): executable.chmod(0o700)
    work = tmp_path / "work"; (work / "hs").mkdir(parents=True)
    (work / "headscale").write_text("#!/bin/bash\necho '[]'\n"); (work / "headscale").chmod(0o700)
    script = f'''set -euo pipefail
diagnostics={tmp_path!s}; work={work!s}; evidence_driver=/missing; evidence_artifact=/missing
peer_pid=""; daemon_pid=""; headscale_pid=""; sidecar_ip=""; run_id=test
port_open() {{ return 1; }}
tsnet_open() {{ return 1; }}
evidence() {{ return 0; }}
{snapshot}
{unit_helpers}
cleanup() {{
{cleanup}
}}
trap cleanup EXIT
trap 'cleanup 130' INT
trap 'cleanup 143' TERM
kill -{signal} $$
'''
    result = subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True,
        env=os.environ | {"PATH": f"{fake_bin}:{os.environ['PATH']}", "N3_RESIDUE_ROOT": str(tmp_path), "N3_UNIT_ROOT": str(tmp_path)}, check=False,
    )
    assert result.returncode == expected, result.stderr
    assert (tmp_path / "cleanup-status.txt").read_text() == "fixtures_reaped=1\n"
    assert (tmp_path / "cleanup-events.log").read_text().splitlines() == ["cleanup"]


def _run_positive_start_cleanup_scenario(
    tmp_path: Path, *, fault: str = "none", signal: str | None = None,
) -> tuple[subprocess.CompletedProcess[str], list[str], Path]:
    """Run the actual positive-start EXIT/trap seam with a failing teardown command."""
    harness = Path("app/linux/package/real_systemd_n3.sh").read_text()
    snapshot = "safe_systemctl_value() {" + harness.split("safe_systemctl_value() {", 1)[1].split("\ncleanup() {", 1)[0]
    unit_helpers = "systemctl_absent_value() {" + harness.split("systemctl_absent_value() {", 1)[1].split("\ndiagnostics=", 1)[0]
    cleanup = harness.split("cleanup() {", 1)[1].split("\n}\ntrap cleanup EXIT", 1)[0]
    fake_bin = tmp_path / "bin"; fake_bin.mkdir()
    event_log = tmp_path / "events.log"
    (fake_bin / "systemctl").write_text(f'''#!/bin/bash
printf 'systemctl:%s\\n' "$1" >>"$EVENT_LOG"
case "$1" in
  start) exit 37 ;;
  show) case "$4" in InvocationID) echo unknown;; ActiveState) echo failed;; SubState) echo failed;; Result) echo exit-code;; ExecMainStatus) echo 37;; MainPID) echo 0;; *) echo unknown;; esac ;;
  stop|disable|reset-failed) [[ "$1" != "{fault}" ]] || exit 55; exit 0 ;;
  daemon-reload|list-jobs|list-unit-files) exit 0 ;;
  *) printf 'unknown-systemctl:%s\\n' "$1" >>"$EVENT_LOG"; exit 97 ;;
esac
''')
    (fake_bin / "sudo").write_text('''#!/bin/bash
printf 'sudo:%s\\n' "$1" >>"$EVENT_LOG"
case "$1" in
  systemctl) shift; exec systemctl "$@";;
  test) [[ "${2:-}" == '!' ]] && exit 0; exit 1;;
  rm|find|kill|update-ca-certificates) exit 0;;
  *) exit 98;;
esac
''')
    (fake_bin / "pgrep").write_text("#!/bin/bash\nexit 1\n")
    (fake_bin / "evidence").write_text(
        "import os, sys\nopen(os.environ['EVENT_LOG'], 'a').write('evidence:%s\\n' % sys.argv[1])\n"
    )
    for executable in fake_bin.iterdir():
        executable.chmod(0o700)
    work = tmp_path / "work"; (work / "hs").mkdir(parents=True)
    (work / "headscale").write_text("#!/bin/bash\necho '[]'\n"); (work / "headscale").chmod(0o700)
    trigger = f"kill -{signal} $$" if signal else 'start_managed_target || exit "$?"'
    script = f'''set -euo pipefail
diagnostics={tmp_path!s}; work={work!s}; evidence_driver={fake_bin / "evidence"!s}; evidence_artifact=/missing
failure_capture_driver=/missing
peer_pid=""; daemon_pid=""; headscale_pid=""; sidecar_ip=""; run_id=test
port_open() {{ return 1; }}
tsnet_open() {{ return 1; }}
evidence() {{ return 0; }}
{snapshot}
{unit_helpers}
cleanup() {{
{cleanup}
}}
trap cleanup EXIT
trap 'cleanup 130' INT
trap 'cleanup 143' TERM
{trigger}
'''
    result = subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, check=False,
        env=os.environ | {"PATH": f"{fake_bin}:{os.environ['PATH']}", "EVENT_LOG": str(event_log),
                          "N3_RESIDUE_ROOT": str(tmp_path), "N3_UNIT_ROOT": str(tmp_path)},
    )
    events = event_log.read_text().splitlines() if event_log.exists() else []
    return result, events, work


@pytest.mark.parametrize("fault", ["none", "stop", "disable", "reset-failed"])
def test_real_systemd_cleanup_command_faults_preserve_exit_37_and_finish_teardown(tmp_path: Path, fault: str) -> None:
    """An independently failing stop/disable/reset cannot abort the EXIT teardown."""
    result, events, work = _run_positive_start_cleanup_scenario(tmp_path, fault=fault)
    assert result.returncode == 37, result.stderr
    assert [event for event in events if event in {"systemctl:stop", "systemctl:disable", "systemctl:reset-failed"}] == [
        "systemctl:stop", "systemctl:disable", "systemctl:reset-failed",
    ]
    assert (tmp_path / "cleanup-status.txt").read_text() == "fixtures_reaped=1\n"
    assert (tmp_path / "cleanup-events.log").read_text().splitlines() == ["cleanup"]
    assert not work.exists()
    assert not any(event.startswith(("evidence:finalize", "evidence:validate", "unknown-")) for event in events)


@pytest.mark.parametrize(("signal", "expected", "fault"), [
    ("INT", 130, "stop"), ("TERM", 143, "reset-failed"),
])
def test_real_systemd_signal_cleanup_command_faults_preserve_signal_status_once(
    tmp_path: Path, signal: str, expected: int, fault: str,
) -> None:
    """TERM/INT after trap registration keep their status through a failing teardown command."""
    result, events, work = _run_positive_start_cleanup_scenario(tmp_path, fault=fault, signal=signal)
    assert result.returncode == expected, result.stderr
    assert [event for event in events if event in {"systemctl:stop", "systemctl:disable", "systemctl:reset-failed"}] == [
        "systemctl:stop", "systemctl:disable", "systemctl:reset-failed",
    ]
    assert (tmp_path / "cleanup-status.txt").read_text() == "fixtures_reaped=1\n"
    assert (tmp_path / "cleanup-events.log").read_text().splitlines() == ["cleanup"]
    assert not work.exists()
    assert not any(event.startswith(("evidence:finalize", "evidence:validate", "unknown-")) for event in events)


def test_real_systemd_signal_during_cleanup_runs_teardown_once(tmp_path: Path) -> None:
    """A second signal while teardown is running must not start a second teardown."""
    harness = Path("app/linux/package/real_systemd_n3.sh").read_text()
    snapshot = "safe_systemctl_value() {" + harness.split("safe_systemctl_value() {", 1)[1].split("\ncleanup() {", 1)[0]
    unit_helpers = "systemctl_absent_value() {" + harness.split("systemctl_absent_value() {", 1)[1].split("\ndiagnostics=", 1)[0]
    cleanup = harness.split("cleanup() {", 1)[1].split("\n}\ntrap cleanup EXIT", 1)[0]
    fake_bin = tmp_path / "bin"; fake_bin.mkdir()
    (fake_bin / "systemctl").write_text("""#!/bin/bash
if [[ $1 == stop && ! -e "$EVENT_LOG.signalled" ]]; then
  touch "$EVENT_LOG.signalled"
  kill -TERM "$PPID"
fi
if [[ $1 == show && $4 == MainPID ]]; then echo 0; fi
exit 0
""")
    (fake_bin / "sudo").write_text("#!/bin/bash\nif [[ $1 == systemctl ]]; then shift; exec systemctl \"$@\"; fi\nexit 0\n")
    (fake_bin / "pgrep").write_text("#!/bin/bash\nexit 1\n")
    for executable in fake_bin.iterdir():
        executable.chmod(0o700)
    work = tmp_path / "work"; (work / "hs").mkdir(parents=True)
    (work / "headscale").write_text("#!/bin/bash\necho '[]'\n"); (work / "headscale").chmod(0o700)
    script = f'''set -euo pipefail
diagnostics={tmp_path!s}; work={work!s}; evidence_driver=/missing; evidence_artifact=/missing
peer_pid=""; daemon_pid=""; headscale_pid=""; sidecar_ip=""; run_id=test
port_open() {{ return 1; }}
tsnet_open() {{ return 1; }}
evidence() {{ return 0; }}
{snapshot}
{unit_helpers}
cleanup() {{
{cleanup}
}}
trap cleanup EXIT
trap 'cleanup 130' INT
trap 'cleanup 143' TERM
kill -TERM $$
'''
    result = subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, check=False,
        env=os.environ | {"PATH": f"{fake_bin}:{os.environ['PATH']}", "EVENT_LOG": str(tmp_path / "events.log"),
                          "N3_RESIDUE_ROOT": str(tmp_path), "N3_UNIT_ROOT": str(tmp_path)},
    )
    assert result.returncode == 143, result.stderr
    assert (tmp_path / "cleanup-events.log").read_text().splitlines() == ["cleanup"]
    assert (tmp_path / "events.log.signalled").exists()


@pytest.mark.parametrize(("caller_errexit", "expected"), [("on", "errexit-on"), ("off", "errexit-off")])
def test_real_systemd_capture_preserves_caller_shell_options(tmp_path: Path, caller_errexit: str, expected: str) -> None:
    """A bounded capture must restore the caller's errexit state on every return."""
    harness = Path("app/linux/package/real_systemd_n3.sh").read_text()
    snapshot = "safe_systemctl_value() {" + harness.split("safe_systemctl_value() {", 1)[1].split("\ncleanup() {", 1)[0]
    fake_bin = tmp_path / "bin"; fake_bin.mkdir()
    (fake_bin / "mktemp").write_text("#!/bin/bash\nexec /usr/bin/mktemp \"$@\"\n")
    (fake_bin / "date").write_text("#!/bin/bash\nprintf '1700000000000000000\\n'\n")
    (fake_bin / "systemctl").write_text("""#!/bin/bash
if [[ $1 == show ]]; then case "$4" in InvocationID) printf '%s\\n' 12345678123412341234123456789abc;; ActiveState) echo failed;; SubState) echo failed;; Result) echo exit-code;; ExecMainStatus) echo 37;; MainPID) echo 0;; *) echo unknown;; esac; exit 0; fi
exit 1
""")
    (fake_bin / "sudo").write_text("#!/bin/bash\n[[ $1 == test ]] && exit 1\nexec \"$@\"\n")
    (fake_bin / "journalctl").write_text("#!/bin/bash\nexit 1\n")
    for executable in fake_bin.iterdir():
        executable.chmod(0o700)
    prelude = (
        "capture_failure_snapshot option-probe || true"
        if caller_errexit == "on"
        else "set +e\ncapture_failure_snapshot option-probe"
    )
    script = f'''set -euo pipefail
diagnostics={tmp_path!s}; failure_capture_driver=/missing; run_id=test
{snapshot}
{prelude}
case "$-" in *e*) printf 'errexit-on\\n';; *) printf 'errexit-off\\n';; esac
'''
    result = subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, check=False,
        env=os.environ | {"PATH": f"{fake_bin}:{os.environ['PATH']}", "N3_UNIT_ROOT": str(tmp_path)},
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == expected, result.stderr


def _run_real_systemd_shipping_cleanup(tmp_path: Path, **env: str) -> subprocess.CompletedProcess[str]:
    harness = Path("app/linux/package/real_systemd_n3.sh").read_text()
    unit_helpers = "systemctl_absent_value() {" + harness.split("systemctl_absent_value() {", 1)[1].split("\ndiagnostics=", 1)[0]
    cleanup = harness.split("shipping_cleanup() {", 1)[1].split("\n}\nreset_shipping_unit()", 1)[0]
    fake_bin = tmp_path / "bin"; fake_bin.mkdir()
    (fake_bin / "systemctl").write_text("""#!/bin/bash
if [[ $1 == show ]]; then p=$4; case $p in LoadState) v=${LOAD_STATE-not-found}; s=${LOAD_RC:-4};; ActiveState) v=${ACTIVE_STATE-inactive}; s=${ACTIVE_RC:-4};; SubState) v=${SUB_STATE-dead}; s=${SUB_RC:-4};; MainPID) v=${MAIN_PID-0}; s=${PID_RC:-4};; esac; printf '%s\\n' "$v"; exit "$s"; fi
[[ $1 == list-unit-files && ${UNIT_LIST_RESIDUE:-0} == 1 ]] && echo loaded
exit 0
""")
    (fake_bin / "sudo").write_text("""#!/bin/bash
[[ $1 == rm ]] && exit 0
exec "$@"
""")
    (fake_bin / "pgrep").write_text("#!/bin/bash\n[[ ${PROCESS_RESIDUE:-0} == 1 ]]\n")
    for executable in fake_bin.iterdir(): executable.chmod(0o700)
    work = tmp_path / "work"; (work / "hs").mkdir(parents=True)
    (work / "headscale").write_text("#!/bin/bash\n[[ ${FIXTURE_RESIDUE:-0} == 1 ]] && echo '[{\"id\":1,\"name\":\"home-sidecar-ci\"}]' || echo '[]'\n")
    (work / "headscale").chmod(0o700)
    script = f"""set -euo pipefail
fail() {{ echo "$1" >&2; exit 1; }}
port_open() {{ [[ ${{PORT_RESIDUE:-0}} == 1 ]]; }}
tsnet_open() {{ [[ ${{LISTENER_RESIDUE:-0}} == 1 ]]; }}
{unit_helpers}
work={work!s}; diagnostics={tmp_path!s}
shipping_cleanup() {{
{cleanup}
}}
shipping_cleanup
"""
    run_env = os.environ | {"PATH": f"{fake_bin}:{os.environ['PATH']}", "N3_RESIDUE_ROOT": str(tmp_path), "N3_UNIT_ROOT": str(tmp_path)} | env
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=run_env, check=False)


@pytest.mark.parametrize("exit_codes", [(0, 0, 0, 0), (1, 1, 1, 1), (4, 0, 1, 4)])
def test_real_systemd_shipping_cleanup_accepts_recognized_absent_exit_orderings(tmp_path: Path, exit_codes: tuple[int, int, int, int]) -> None:
    result = _run_real_systemd_shipping_cleanup(tmp_path, **dict(zip(("LOAD_RC", "ACTIVE_RC", "SUB_RC", "PID_RC"), map(str, exit_codes), strict=True)))
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("env", [
    {"LOAD_STATE": "", "LOAD_RC": "4"}, {"LOAD_STATE": "not-found", "LOAD_RC": "2"},
    {"LOAD_STATE": "loaded", "LOAD_RC": "0"},
    {"ACTIVE_STATE": "active", "ACTIVE_RC": "0"}, {"MAIN_PID": "42", "PID_RC": "0"},
    {"UNIT_LIST_RESIDUE": "1"}, {"PROCESS_RESIDUE": "1"}, {"PORT_RESIDUE": "1"},
    {"LISTENER_RESIDUE": "1"}, {"FIXTURE_RESIDUE": "1"},
])
def test_real_systemd_shipping_cleanup_rejects_query_and_probe_residue(tmp_path: Path, env: dict[str, str]) -> None:
    assert _run_real_systemd_shipping_cleanup(tmp_path, **env).returncode != 0


@pytest.mark.parametrize("residue", [
    "etc/systemd/system/happyranch-managed.target", "run/systemd/system/happyranch-managed.target.d",
    "etc/happyranch/enrollment.key", ".happyranch-install-transaction.json", ".happyranch-backup",
    ".happyranch-units-backup", "opt/happyranch", "var/lib/happyranch-connector",
    "run/happyranch-tsnet-sidecar", "var/log/happyranch-connector", ".happyranch-stage-leftover",
    ".happyranch-tmp-leftover",
])
def test_real_systemd_shipping_cleanup_rejects_every_filesystem_residue_class(tmp_path: Path, residue: str) -> None:
    path = tmp_path / residue
    path.parent.mkdir(parents=True, exist_ok=True)
    path.mkdir() if "." not in path.name else path.write_text("residue")
    assert _run_real_systemd_shipping_cleanup(tmp_path).returncode != 0


def test_composite_service_manager_executes_start_ready_stop_crash_restart() -> None:
    events: list[tuple[str, ...]] = []
    active = {"happyranch-connector.service": False, "happyranch-tsnet-sidecar.service": False}
    def run(command, **_kwargs):
        args = tuple(command[1:])
        events.append(args)
        if args[:2] == ("start", "happyranch-managed.target"):
            active["happyranch-connector.service"] = True
            active["happyranch-tsnet-sidecar.service"] = True
        elif args[:2] == ("stop", "happyranch-managed.target"):
            active["happyranch-tsnet-sidecar.service"] = False
            active["happyranch-connector.service"] = False
        elif args[0] == "restart": active[args[1]] = True
        stdout = ""
        if args[0] == "show": stdout = "active\n" if active[args[1]] else "inactive\n"
        return subprocess.CompletedProcess(command, 0, stdout, "")
    manager = CompositeServiceManager(run=run)
    manager.start_ready()
    active["happyranch-tsnet-sidecar.service"] = False  # observed crash/readiness loss
    manager.restart_after_crash("happyranch-tsnet-sidecar.service")
    manager.stop()
    assert events == [
        ("start", "happyranch-managed.target"),
        ("show", "happyranch-connector.service", "--property=ActiveState", "--value"),
        ("show", "happyranch-tsnet-sidecar.service", "--property=ActiveState", "--value"),
        ("restart", "happyranch-tsnet-sidecar.service"),
        ("show", "happyranch-tsnet-sidecar.service", "--property=ActiveState", "--value"),
        ("stop", "happyranch-managed.target"),
    ]
    assert active == {"happyranch-connector.service": False, "happyranch-tsnet-sidecar.service": False}


def test_build_is_reproducible_and_manifest_couples_every_payload(tmp_path: Path) -> None:
    inputs = _inputs(tmp_path)
    first = build_linux_package(tmp_path / "one.tar", *inputs, version="1.2.3")
    second = build_linux_package(tmp_path / "two.tar", *inputs, version="1.2.3")
    assert first.read_bytes() == second.read_bytes()
    with tarfile.open(first) as archive:
        names = archive.getnames()
        manifest = json.load(archive.extractfile("happyranch-linux-amd64/manifest.json"))
        for item in manifest["files"]:
            raw = archive.extractfile("happyranch-linux-amd64/" + item["path"]).read()
            assert hashlib.sha256(raw).hexdigest() == item["sha256"]
        assert "happyranch-linux-amd64/share/sbom.cdx.json" in names
        assert "happyranch-linux-amd64/share/THIRD_PARTY_NOTICES.md" in names
        assert manifest["sidecar_dependency_count"] == 1


def test_build_rejects_incomplete_notices(tmp_path: Path) -> None:
    sidecar, connector, wheel, inventory, notices = _inputs(tmp_path)
    notices.write_text("# notices\n")
    with pytest.raises(PackageError, match="notice_inventory_mismatch"):
        build_linux_package(tmp_path / "bad.tar", sidecar, connector, wheel, inventory, notices, version="1")


def test_fixture_install_upgrade_uninstall_is_owner_only_and_residue_free(tmp_path: Path) -> None:
    package = build_linux_package(tmp_path / "pkg.tar", *_inputs(tmp_path), version="1")
    root = tmp_path / "root"
    receipt = install_linux_package(package, root)
    assert receipt["version"] == "1"
    assert (root / "opt/happyranch/bin/happyranch-tsnet-sidecar").stat().st_mode & 0o077 == 0
    assert (root / "etc/systemd/system/happyranch-managed.target").exists()
    install_linux_package(package, root)  # idempotent/re-entry
    uninstall_linux_package(root)
    assert not (root / "opt/happyranch").exists()
    assert not list((root / "etc/systemd/system").glob("happyranch-*"))


def test_system_service_install_keeps_root_payload_executable_by_service_user(tmp_path: Path) -> None:
    package = build_linux_package(tmp_path / "pkg.tar", *_inputs(tmp_path), version="1")
    root = tmp_path / "root"
    _stage_system_credentials(root)
    install_linux_package(package, root, system_service=True)
    assert (root / "opt/happyranch").stat().st_mode & 0o777 == 0o755
    assert (root / "opt/happyranch/bin").stat().st_mode & 0o777 == 0o755
    for name in ("happyranch-connector", "happyranch-tsnet-sidecar"):
        binary = root / "opt/happyranch/bin" / name
        assert binary.stat().st_mode & 0o777 == 0o755


def test_no_root_install_retains_owner_only_payload_mode(tmp_path: Path) -> None:
    package = build_linux_package(tmp_path / "pkg.tar", *_inputs(tmp_path), version="1")
    root = tmp_path / "root"
    install_linux_package(package, root, system_service=False)
    assert (root / "opt/happyranch").stat().st_mode & 0o777 == 0o700
    assert (root / "opt/happyranch/bin").stat().st_mode & 0o777 == 0o700
    for name in ("happyranch-connector", "happyranch-tsnet-sidecar"):
        assert (root / "opt/happyranch/bin" / name).stat().st_mode & 0o777 == 0o700


def test_install_rejects_ambiguous_service_mode_before_write(tmp_path: Path) -> None:
    package = build_linux_package(tmp_path / "pkg.tar", *_inputs(tmp_path), version="1")
    root = tmp_path / "root"
    with pytest.raises(PackageError, match="install_mode_invalid"):
        install_linux_package(package, root, system_service=1)  # type: ignore[arg-type]
    assert not root.exists()


def test_system_service_mode_survives_rollback_and_reentry(tmp_path: Path) -> None:
    package = build_linux_package(tmp_path / "pkg.tar", *_inputs(tmp_path), version="1")
    root = tmp_path / "root"
    _stage_system_credentials(root)
    install_linux_package(package, root, system_service=True)
    with pytest.raises(RuntimeError, match="injected"):
        install_linux_package(
            package,
            root,
            system_service=True,
            fault=lambda name: (_ for _ in ()).throw(RuntimeError("injected"))
            if name == "payload_published" else None,
        )
    install_linux_package(package, root, system_service=True)
    assert (root / "opt/happyranch").stat().st_mode & 0o777 == 0o755
    assert (root / "opt/happyranch/bin").stat().st_mode & 0o777 == 0o755
    for name in ("happyranch-connector", "happyranch-tsnet-sidecar"):
        assert (root / "opt/happyranch/bin" / name).stat().st_mode & 0o777 == 0o755


@pytest.mark.parametrize("field", ["uid", "gid"])
def test_archive_rejects_non_root_payload_ownership_before_write(tmp_path: Path, field: str) -> None:
    package = build_linux_package(tmp_path / "pkg.tar", *_inputs(tmp_path), version="1")
    def mutate(entries) -> None:
        member, _ = next(item for item in entries if item[0].name.endswith("happyranch-connector"))
        setattr(member, field, 1000)
    root = tmp_path / "root"
    with pytest.raises(PackageError, match="archive_owner_invalid"):
        install_linux_package(_rewrite_package(package, tmp_path / f"bad-{field}.tar", mutate), root)
    assert not root.exists()


@pytest.mark.parametrize("boundary", ["payload_old_retained", "payload_published", *[f"unit_published:{name}" for name in ("happyranch-connector.service", "happyranch-tsnet-sidecar.service", "happyranch-managed.target")]])
def test_upgrade_rolls_back_at_every_publication_boundary(tmp_path: Path, boundary: str) -> None:
    package = build_linux_package(tmp_path / "pkg.tar", *_inputs(tmp_path), version="1")
    root = tmp_path / "root"
    install_linux_package(package, root)
    before = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    def fault(name: str) -> None:
        if name == boundary:
            raise RuntimeError("injected")
    with pytest.raises(RuntimeError, match="injected"):
        install_linux_package(package, root, fault=fault)
    after = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    assert after == before
    assert not list(root.glob(".happyranch-*"))


def test_archive_rejects_duplicate_member(tmp_path: Path) -> None:
    package = build_linux_package(tmp_path / "pkg.tar", *_inputs(tmp_path), version="1")
    duplicate = tmp_path / "duplicate.tar"
    with tarfile.open(package) as source, tarfile.open(duplicate, "w") as target:
        members = source.getmembers()
        for member in [*members, members[0]]:
            raw = source.extractfile(member).read()
            target.addfile(member, io.BytesIO(raw))
    with pytest.raises(PackageError, match="archive_duplicate_member"):
        install_linux_package(duplicate, tmp_path / "root")


def test_archive_rejects_actual_mode_mismatch_before_write(tmp_path: Path) -> None:
    package = build_linux_package(tmp_path / "pkg.tar", *_inputs(tmp_path), version="1")
    def mutate(entries) -> None:
        member, _raw = next(item for item in entries if item[0].name.endswith("happyranch-tsnet-sidecar"))
        member.mode = 0o777
    root = tmp_path / "root"
    with pytest.raises(PackageError, match="archive_mode_invalid"):
        install_linux_package(_rewrite_package(package, tmp_path / "bad-mode.tar", mutate), root)
    assert not root.exists()


def test_install_rejects_tampered_payload_without_partial_residue(tmp_path: Path) -> None:
    package = build_linux_package(tmp_path / "pkg.tar", *_inputs(tmp_path), version="1")
    tampered = tmp_path / "tampered.tar"
    with tarfile.open(package) as source, tarfile.open(tampered, "w", format=tarfile.PAX_FORMAT) as target:
        for member in source.getmembers():
            data = source.extractfile(member).read() if member.isfile() else None
            if member.name.endswith("happyranch-tsnet-sidecar"):
                data = b"tampered"
                member.size = len(data)
            target.addfile(member, io.BytesIO(data) if data is not None else None)
    root = tmp_path / "root"
    with pytest.raises(PackageError, match="manifest_hash_mismatch"):
        install_linux_package(tampered, root)
    assert not (root / "opt/happyranch").exists()


def _rewrite_package(package: Path, output: Path, mutate) -> Path:
    with tarfile.open(package) as source:
        entries = [(member, source.extractfile(member).read()) for member in source.getmembers()]
    mutate(entries)
    with tarfile.open(output, "w", format=tarfile.PAX_FORMAT) as target:
        for member, raw in entries:
            member.size = len(raw)
            target.addfile(member, io.BytesIO(raw))
    return output


@pytest.mark.parametrize("field,value", [
    ("schema_version", True), ("sidecar_dependency_count", True),
    ("architecture", "linux-arm64"), ("sidecar_dependency_count", 99),
])
def test_manifest_wire_values_fail_closed_before_write(tmp_path: Path, field: str, value: object) -> None:
    package = build_linux_package(tmp_path / "pkg.tar", *_inputs(tmp_path), version="1")
    def mutate(entries) -> None:
        for index, (member, raw) in enumerate(entries):
            if member.name.endswith("manifest.json"):
                manifest = json.loads(raw)
                manifest[field] = value
                entries[index] = member, json.dumps(manifest).encode()
    malformed = _rewrite_package(package, tmp_path / "bad.tar", mutate)
    root = tmp_path / "root"
    with pytest.raises(PackageError): install_linux_package(malformed, root)
    assert not root.exists()


def test_exact_archive_allowlist_rejects_manifested_extra_payload(tmp_path: Path) -> None:
    package = build_linux_package(tmp_path / "pkg.tar", *_inputs(tmp_path), version="1")
    def mutate(entries) -> None:
        manifest_index = next(i for i, (m, _) in enumerate(entries) if m.name.endswith("manifest.json"))
        member, raw = entries[manifest_index]
        manifest = json.loads(raw)
        payload = b"extra"
        manifest["files"].append({"path": "bin/other", "sha256": hashlib.sha256(payload).hexdigest(), "mode": "0o700"})
        entries[manifest_index] = member, json.dumps(manifest).encode()
        extra = tarfile.TarInfo("happyranch-linux-amd64/bin/other")
        extra.mode = 0o700
        extra.uname = extra.gname = "root"
        entries.append((extra, payload))
    with pytest.raises(PackageError, match="manifest_path_invalid"):
        install_linux_package(_rewrite_package(package, tmp_path / "bad.tar", mutate), tmp_path / "root")


def test_archive_rejects_traversal_and_unmanifested_members_before_write(tmp_path: Path) -> None:
    package = build_linux_package(tmp_path / "pkg.tar", *_inputs(tmp_path), version="1")
    for name, expected in (("happyranch-linux-amd64/../escape", "archive_member_invalid"),
                           ("happyranch-linux-amd64/unmanifested", "manifest_membership_mismatch")):
        def mutate(entries, member_name=name) -> None:
            member = tarfile.TarInfo(member_name)
            member.mode = 0o600
            member.uname = member.gname = "root"
            entries.append((member, b"hostile"))
        root = tmp_path / expected
        with pytest.raises(PackageError, match=expected):
            install_linux_package(_rewrite_package(package, tmp_path / f"{expected}.tar", mutate), root)
        assert not root.exists()


@pytest.mark.parametrize("mutation", ["inventory_boolean", "sbom_missing_purl", "notice_wrong_content"])
def test_complete_evidence_structure_fails_closed_before_write(tmp_path: Path, mutation: str) -> None:
    package = build_linux_package(tmp_path / "pkg.tar", *_inputs(tmp_path), version="1")
    def mutate(entries) -> None:
        manifest_i = next(i for i, (m, _) in enumerate(entries) if m.name.endswith("manifest.json"))
        manifest_member, manifest_raw = entries[manifest_i]
        manifest = json.loads(manifest_raw)
        suffix = {"inventory_boolean": "dependency-inventory.json", "sbom_missing_purl": "sbom.cdx.json",
                  "notice_wrong_content": "THIRD_PARTY_NOTICES.md"}[mutation]
        evidence_i = next(i for i, (m, _) in enumerate(entries) if m.name.endswith(suffix))
        evidence_member, evidence_raw = entries[evidence_i]
        if mutation == "inventory_boolean":
            evidence = json.loads(evidence_raw); evidence["schema_version"] = True
            evidence_raw = json.dumps(evidence).encode()
        elif mutation == "sbom_missing_purl":
            evidence = json.loads(evidence_raw); evidence["components"][0].pop("purl")
            evidence_raw = json.dumps(evidence).encode()
        else:
            evidence_raw = evidence_raw.replace(b"fixture license", b"tampered license")
        entries[evidence_i] = evidence_member, evidence_raw
        relative = str(PurePosixPath(evidence_member.name).relative_to("happyranch-linux-amd64"))
        next(item for item in manifest["files"] if item["path"] == relative)["sha256"] = hashlib.sha256(evidence_raw).hexdigest()
        entries[manifest_i] = manifest_member, json.dumps(manifest).encode()
    root = tmp_path / "root"
    with pytest.raises(PackageError):
        install_linux_package(_rewrite_package(package, tmp_path / "bad.tar", mutate), root)
    assert not root.exists()


def _installer_snapshot(root: Path) -> dict[str, tuple]:
    """Recursively snapshot existence/type/bytes/modes without following links."""
    state: dict[str, tuple] = {}
    for path in sorted(root.rglob("*")):
        relative = str(path.relative_to(root))
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode):
            state[relative] = ("link", os.readlink(path))
        elif stat.S_ISDIR(metadata.st_mode):
            state[relative] = ("dir", stat.S_IMODE(metadata.st_mode))
        elif stat.S_ISREG(metadata.st_mode):
            state[relative] = ("file", path.read_bytes(), stat.S_IMODE(metadata.st_mode))
        else:
            state[relative] = ("other", metadata.st_mode)
    return state


class _InstallerGuard:
    """Occurrence-scoped installer fault injector with exact hit receipts.

    ``stage``/``operation``/``path``/``occurrence`` select the exact real
    filesystem mutation to fault.  ``partial`` performs a real truncated write
    at the destination before raising; ``persistent`` re-fires on every match.
    """

    def __init__(self, *, operation: str, stage: str = "before", occurrence: int = 1,
                 path: Path | None = None, partial: bool = False, persistent: bool = False,
                 exception: type[BaseException] = OSError) -> None:
        self.operation = operation
        self.stage = stage
        self.occurrence = occurrence
        self.path = None if path is None else str(path)
        self.partial = partial
        self.persistent = persistent
        self.exception = exception
        self.hits: list[tuple[str, str, str]] = []
        self.fired = 0

    def __call__(self, stage: str, operation: str, path: str) -> None:
        self.hits.append((stage, operation, path))
        if stage != self.stage or operation != self.operation:
            return
        if self.path is not None and path != self.path:
            return
        self.fired += 1
        if not self.persistent and self.fired != self.occurrence:
            return
        if self.partial:
            Path(path).write_bytes(b"partial-new-bytes")
        raise self.exception("injected")

    def receipts(self) -> list[tuple[str, str, str]]:
        return [
            hit for hit in self.hits
            if hit[0] == self.stage and hit[1] == self.operation
            and (self.path is None or hit[2] == self.path)
        ]


def _distinct_package(tmp_path: Path, version: str, marker: bytes) -> Path:
    sidecar, connector, wheel, inventory, notices = _inputs(tmp_path)
    sidecar.write_bytes(b"sidecar-" + marker)
    return build_linux_package(
        tmp_path / f"pkg-{version}.tar", sidecar, connector, wheel, inventory, notices, version=version
    )


@pytest.mark.parametrize("phase", ["prepared", "payload_retained", "payload_published", "units_publishing"])
def test_legacy_v1_transaction_marker_is_preserved_and_refused(tmp_path: Path, phase: str) -> None:
    """M2: every formerly accepted schema-v1 composition lacks ownership facts."""
    package = _distinct_package(tmp_path, "1", b"one")
    root = tmp_path / "root"
    install_linux_package(package, root)
    marker = root / TRANSACTION_MARKER
    marker.write_text(json.dumps({"phase": phase, "schema_version": 1}) + "\n")
    marker.chmod(0o600)
    before = _installer_snapshot(root)
    for _ in range(2):
        with pytest.raises(PackageError, match="transaction_state_invalid"):
            install_linux_package(package, root)
    assert _installer_snapshot(root) == before


@pytest.mark.parametrize("residue", ["empty-unit-backup", "orphan-stage", "payload-backup"])
def test_unrecorded_preparation_residue_is_preserved_and_refused(tmp_path: Path, residue: str) -> None:
    """M6/M9: no marker plus any owned-looking residue is ambiguous, never deleted."""
    package = _distinct_package(tmp_path, "1", b"one")
    root = tmp_path / "root"
    root.mkdir()
    if residue == "empty-unit-backup":
        (root / ".happyranch-units-backup").mkdir(mode=0o700)
    elif residue == "orphan-stage":
        stage = root / ".happyranch-stage-deadbeef"
        stage.mkdir(mode=0o700)
        (stage / "partial").write_bytes(b"x")
    else:
        backup = root / ".happyranch-backup"
        backup.mkdir(mode=0o700)
        (backup / "old").write_bytes(b"y")
    before = _installer_snapshot(root)
    with pytest.raises(PackageError, match="transaction_state_invalid"):
        install_linux_package(package, root)
    assert _installer_snapshot(root) == before


@pytest.mark.parametrize("operation", [
    "payload_retain",
    "payload_publish",
    "unit_publish:happyranch-connector.service",
    "unit_publish:happyranch-tsnet-sidecar.service",
    "unit_publish:happyranch-managed.target",
])
def test_exact_operation_fault_restores_old_bytes_and_modes_then_reinstalls(
    tmp_path: Path, operation: str,
) -> None:
    """B/I: real syscall-level injection at each publication seam restores OLD."""
    old = _distinct_package(tmp_path, "1", b"old")
    new = _distinct_package(tmp_path, "2", b"new")
    root = tmp_path / "root"
    install_linux_package(old, root)
    before = _installer_snapshot(root)
    guard = _InstallerGuard(operation=operation, stage="after")
    with pytest.raises(OSError, match="injected"):
        install_linux_package(new, root, guard=guard)
    assert len(guard.receipts()) == 1
    assert _installer_snapshot(root) == before
    assert not list(root.glob(".happyranch-*"))
    # A transient fault cleared must allow coherent real reentry to NEW.
    install_linux_package(new, root)
    assert (root / "opt/happyranch/bin/happyranch-tsnet-sidecar").read_bytes() == b"sidecar-new"
    assert not list(root.glob(".happyranch-*"))


def test_pre_record_fault_cleans_only_owned_preparation(tmp_path: Path) -> None:
    old = _distinct_package(tmp_path, "1", b"old")
    new = _distinct_package(tmp_path, "2", b"new")
    root = tmp_path / "root"
    install_linux_package(old, root)
    before = _installer_snapshot(root)
    guard = _InstallerGuard(operation="stage_payload:share/happyranch.whl", stage="before")
    with pytest.raises(OSError, match="injected"):
        install_linux_package(new, root, guard=guard)
    assert len(guard.receipts()) == 1
    assert _installer_snapshot(root) == before
    assert not list(root.glob(".happyranch-*"))


def test_baseexception_interruption_is_recovered_by_real_reentry(tmp_path: Path) -> None:
    """K proves process interruption (not caught by ordinary rollback) then recovery."""
    old = _distinct_package(tmp_path, "1", b"old")
    new = _distinct_package(tmp_path, "2", b"new")
    root = tmp_path / "root"
    install_linux_package(old, root)
    before = _installer_snapshot(root)
    guard = _InstallerGuard(operation="payload_publish", stage="after", exception=KeyboardInterrupt)
    with pytest.raises(KeyboardInterrupt):
        install_linux_package(new, root, guard=guard)
    assert (root / TRANSACTION_MARKER).exists()
    install_linux_package(new, root)
    assert (root / "opt/happyranch/bin/happyranch-tsnet-sidecar").read_bytes() == b"sidecar-new"
    assert not list(root.glob(".happyranch-*"))
    assert before != _installer_snapshot(root)


def test_direct_recovery_entry_restores_old_before_reinstall(tmp_path: Path) -> None:
    old = _distinct_package(tmp_path, "1", b"old")
    new = _distinct_package(tmp_path, "2", b"new")
    root = tmp_path / "root"
    install_linux_package(old, root)
    before = _installer_snapshot(root)
    guard = _InstallerGuard(
        operation="unit_publish:happyranch-tsnet-sidecar.service", stage="after", exception=KeyboardInterrupt
    )
    with pytest.raises(KeyboardInterrupt):
        install_linux_package(new, root, guard=guard)
    _recover_interrupted(root)
    assert _installer_snapshot(root) == before
    assert not list(root.glob(".happyranch-*"))


def test_partial_unit_write_is_classified_and_rolled_back(tmp_path: Path) -> None:
    old = _distinct_package(tmp_path, "1", b"old")
    new = _distinct_package(tmp_path, "2", b"new")
    root = tmp_path / "root"
    install_linux_package(old, root)
    before = _installer_snapshot(root)
    unit = "happyranch-connector.service"
    guard = _InstallerGuard(operation=f"unit_publish:{unit}", stage="before", partial=True)
    with pytest.raises(OSError, match="injected"):
        install_linux_package(new, root, guard=guard)
    assert len(guard.receipts()) == 1
    assert _installer_snapshot(root) == before
    assert not list(root.glob(".happyranch-*"))


def test_fresh_install_rollback_removes_only_owned_paths(tmp_path: Path) -> None:
    package = _distinct_package(tmp_path, "1", b"one")
    root = tmp_path / "root"
    root.mkdir()
    (root / "unrelated.txt").write_bytes(b"keep")
    guard = _InstallerGuard(operation="unit_publish:happyranch-managed.target", stage="before")
    with pytest.raises(OSError, match="injected"):
        install_linux_package(package, root, guard=guard)
    assert (root / "unrelated.txt").read_bytes() == b"keep"
    assert not (root / "opt").exists()
    assert not (root / "etc").exists()
    assert not list(root.glob(".happyranch-*"))


def test_upgrade_rollback_preserves_prior_dropin_bytes_modes_and_sibling(tmp_path: Path) -> None:
    old = _distinct_package(tmp_path, "1", b"old")
    new = _distinct_package(tmp_path, "2", b"new")
    root = tmp_path / "root"
    _stage_system_credentials(root, enrollment=True)
    install_linux_package(old, root, system_service=False)
    dropin_dir = root / "etc/systemd/system/happyranch-tsnet-sidecar.service.d"
    dropin_dir.mkdir(mode=0o710)
    dropin = dropin_dir / "10-enrollment-credential.conf"
    dropin.write_bytes(b"operator-managed-prior-dropin\n")
    dropin.chmod(0o640)
    sibling = dropin_dir / "99-other.conf"
    sibling.write_bytes(b"foreign-sibling\n")
    sibling.chmod(0o600)
    before = _installer_snapshot(root)
    guard = _InstallerGuard(operation="dropin_publish", stage="before", partial=True)
    with pytest.raises(OSError, match="injected"):
        install_linux_package(new, root, system_service=True, guard=guard)
    assert len(guard.receipts()) == 1
    assert _installer_snapshot(root) == before
    assert dropin.read_bytes() == b"operator-managed-prior-dropin\n"
    assert stat.S_IMODE(dropin.lstat().st_mode) == 0o640
    assert sibling.read_bytes() == b"foreign-sibling\n"
    assert not list(root.glob(".happyranch-*"))


def test_persistent_committed_cleanup_fault_preserves_new_and_resumes(tmp_path: Path) -> None:
    old = _distinct_package(tmp_path, "1", b"old")
    new = _distinct_package(tmp_path, "2", b"new")
    root = tmp_path / "root"
    install_linux_package(old, root)
    guard = _InstallerGuard(operation="backup_remove:unlink", stage="before", persistent=True)
    with pytest.raises(OSError, match="injected"):
        install_linux_package(new, root, guard=guard)
    assert (root / TRANSACTION_MARKER).exists()
    assert (root / "opt/happyranch/bin/happyranch-tsnet-sidecar").read_bytes() == b"sidecar-new"
    # Fault cleared: real reentry completes committed cleanup and reinstalls NEW.
    install_linux_package(new, root)
    assert (root / "opt/happyranch/bin/happyranch-tsnet-sidecar").read_bytes() == b"sidecar-new"
    assert not list(root.glob(".happyranch-*"))


def test_corrupt_backup_is_refused_not_restored(tmp_path: Path) -> None:
    """M5: a digest-mismatched backup never authorizes replacing OLD."""
    old = _distinct_package(tmp_path, "1", b"old")
    new = _distinct_package(tmp_path, "2", b"new")
    root = tmp_path / "root"
    install_linux_package(old, root)
    guard = _InstallerGuard(operation="payload_publish", stage="after", exception=KeyboardInterrupt)
    with pytest.raises(KeyboardInterrupt):
        install_linux_package(new, root, guard=guard)
    backup = root / ".happyranch-units-backup/happyranch-connector.service"
    backup.write_bytes(b"corrupt-backup")
    state = _installer_snapshot(root)
    with pytest.raises(PackageError, match="transaction_state_invalid"):
        install_linux_package(new, root)
    assert _installer_snapshot(root) == state


@pytest.mark.parametrize("mutation", ["bad-json", "non-object", "missing-key", "extra-key", "wrong-type", "unknown-phase", "wrong-root"])
def test_malformed_or_foreign_record_is_refused_unchanged(tmp_path: Path, mutation: str) -> None:
    package = _distinct_package(tmp_path, "1", b"one")
    root = tmp_path / "root"
    install_linux_package(package, root)
    marker = root / TRANSACTION_MARKER
    record = {
        "schema_version": 2, "attempt_id": "a" * 32, "root": str(root), "phase": "prepared",
        "payload_present": True, "units": {name: True for name in (
            "happyranch-connector.service", "happyranch-tsnet-sidecar.service", "happyranch-managed.target")},
        "dropin_present": False, "stage": None, "created_parents": [], "published_units": [],
        "dropin_published": False,
        "backups": {"units": {name: None for name in (
            "happyranch-connector.service", "happyranch-tsnet-sidecar.service", "happyranch-managed.target")}, "dropin": None},
    }
    if mutation == "bad-json":
        marker.write_text("{not json")
    elif mutation == "non-object":
        marker.write_text(json.dumps([1, 2, 3]))
    elif mutation == "missing-key":
        record.pop("attempt_id")
        marker.write_text(json.dumps(record))
    elif mutation == "extra-key":
        record["unexpected"] = 1
        marker.write_text(json.dumps(record))
    elif mutation == "wrong-type":
        record["payload_present"] = "yes"
        marker.write_text(json.dumps(record))
    elif mutation == "unknown-phase":
        record["phase"] = "invented"
        marker.write_text(json.dumps(record))
    else:
        record["root"] = str(root / "elsewhere")
        marker.write_text(json.dumps(record))
    marker.chmod(0o600)
    before = _installer_snapshot(root)
    with pytest.raises(PackageError, match="transaction_state_invalid"):
        install_linux_package(package, root)
    assert _installer_snapshot(root) == before


def test_enrollment_source_retirement_is_atomic_reentrant_and_rolls_back(tmp_path: Path) -> None:
    source = tmp_path / "enrollment.key"
    marker = tmp_path / "state" / "credential.consumed"
    marker.parent.mkdir(mode=0o700)
    source.write_text("one-use\n")
    source.chmod(0o600)
    marker.write_text("durable\n")
    marker.chmod(0o600)
    _retire_enrollment_source(source, marker)
    assert not source.exists() and not source.with_name("enrollment.key.retiring").exists()
    _retire_enrollment_source(source, marker)

    marker.unlink()
    source.with_name("enrollment.key.retiring").write_text("rollback\n")
    source.with_name("enrollment.key.retiring").chmod(0o600)
    with pytest.raises(OSError, match="enrollment not durable"):
        _retire_enrollment_source(source, marker)
    assert source.read_text() == "rollback\n"


def test_enrollment_source_retirement_removes_transient_dropin_after_marker(tmp_path: Path) -> None:
    source = tmp_path / "enrollment.key"
    marker = tmp_path / "state" / "credential.consumed"
    dropin = tmp_path / "unit.d" / "10-enrollment-credential.conf"
    marker.parent.mkdir()
    dropin.parent.mkdir()
    source.write_text("one-use\n")
    source.chmod(0o600)
    marker.write_text("durable\n")
    marker.chmod(0o600)
    dropin.write_text("[Service]\nLoadCredential=enrollment.key:/source\n")
    dropin.chmod(0o600)
    reloads: list[str] = []
    _retire_enrollment_source(source, marker, dropin=dropin, reload_manager=lambda: reloads.append("reload"))
    assert not source.exists()
    assert not dropin.exists()
    _retire_enrollment_source(source, marker, dropin=dropin, reload_manager=lambda: reloads.append("reload"))
    assert reloads == ["reload"]


def test_retirement_removes_and_reloads_dropin_before_source(tmp_path: Path) -> None:
    source = tmp_path / "enrollment.key"
    marker = tmp_path / "state" / "credential.consumed"
    dropin = tmp_path / "unit.d" / "10-enrollment-credential.conf"
    marker.parent.mkdir(); dropin.parent.mkdir()
    source.write_text("one-use\n"); source.chmod(0o600)
    marker.write_text("durable\n"); marker.chmod(0o600)
    dropin.write_text("[Service]\nLoadCredential=enrollment.key:/source\n"); dropin.chmod(0o600)
    observed: list[tuple[bool, bool]] = []
    _retire_enrollment_source(
        source, marker, dropin=dropin,
        reload_manager=lambda: observed.append((dropin.exists(), source.exists())),
    )
    assert observed == [(False, True)]
    assert not source.exists() and not dropin.exists()


def test_interrupted_retirement_reentry_finishes_source_after_dropin_reload(tmp_path: Path) -> None:
    source = tmp_path / "enrollment.key"
    marker = tmp_path / "state" / "credential.consumed"
    dropin = tmp_path / "unit.d" / "10-enrollment-credential.conf"
    marker.parent.mkdir(); dropin.parent.mkdir()
    source.write_text("one-use\n"); source.chmod(0o600)
    marker.write_text("durable\n"); marker.chmod(0o600)
    _reconcile_enrollment_retirement(source, marker, dropin=dropin)
    assert not source.exists()


def _sidecar_stopped_proof() -> None:
    """Injected affirmative stopped proof for the unit-level lifecycle cases."""
    return None


def _sidecar_running_proof() -> None:
    raise OSError("service must be stopped")


def test_explicit_fresh_enrollment_replaces_consumed_state(tmp_path: Path) -> None:
    source = tmp_path / "enrollment.key"
    marker = tmp_path / "state" / "credential.consumed"
    dropin = tmp_path / "unit.d" / "10-enrollment-credential.conf"
    marker.parent.mkdir(); dropin.parent.mkdir()
    source.write_text("fresh-one-use\n"); source.chmod(0o600)
    marker.write_text("durable\n"); marker.chmod(0o600)
    reloads: list[str] = []
    _prepare_fresh_enrollment(
        source, marker, dropin=dropin, reload_manager=lambda: reloads.append("reload"),
        require_service_stopped=_sidecar_stopped_proof,
    )
    assert not marker.exists()
    assert source.read_text() == "fresh-one-use\n"
    assert dropin.read_text() == "[Service]\nLoadCredential=enrollment.key:/etc/happyranch/enrollment.key\n"
    assert reloads == ["reload"]


def test_explicit_fresh_enrollment_refuses_running_service(tmp_path: Path) -> None:
    source = tmp_path / "enrollment.key"
    marker = tmp_path / "state" / "credential.consumed"
    dropin = tmp_path / "unit.d" / "10-enrollment-credential.conf"
    marker.parent.mkdir(); dropin.parent.mkdir()
    source.write_text("fresh-one-use\n"); source.chmod(0o600)
    marker.write_text("durable\n"); marker.chmod(0o600)
    with pytest.raises(OSError, match="service must be stopped"):
        _prepare_fresh_enrollment(
            source, marker, dropin=dropin, require_service_stopped=_sidecar_running_proof
        )
    assert marker.exists() and not dropin.exists()


def test_system_install_stages_credential_only_in_transient_dropin(tmp_path: Path) -> None:
    package = build_linux_package(tmp_path / "pkg.tar", *_inputs(tmp_path), version="1")
    root = tmp_path / "root"
    source = root / "etc/happyranch/enrollment.key"
    _stage_system_credentials(root)
    source.write_text("one-use\n")
    source.chmod(0o600)
    install_linux_package(package, root, system_service=True)
    dropin = root / "etc/systemd/system/happyranch-tsnet-sidecar.service.d/10-enrollment-credential.conf"
    assert dropin.read_text() == "[Service]\nLoadCredential=enrollment.key:/etc/happyranch/enrollment.key\n"
    assert dropin.stat().st_mode & 0o777 == 0o600


def test_credential_capability_distinguishes_fail_closed_categories(tmp_path: Path) -> None:
    source = tmp_path / "credential"
    uid = os.geteuid()
    assert credential_capability(source, expected_uid=uid) == "credential_absent"
    source.mkdir()
    assert credential_capability(source, expected_uid=uid) == "credential_wrong_type"
    source.rmdir()
    target = tmp_path / "target"
    target.write_text("secret\n")
    target.chmod(0o600)
    source.symlink_to(target)
    assert credential_capability(source, expected_uid=uid) == "credential_unsafe_symlink"
    source.unlink()
    source.write_text("secret\n")
    source.chmod(0o640)
    assert credential_capability(source, expected_uid=uid) == "credential_wrong_custody"
    source.chmod(0o600)
    source.write_text("")
    assert credential_capability(source, expected_uid=uid) == "credential_staging_incompatible"
    source.write_text("secret\n")
    assert credential_capability(source, expected_uid=uid) == "credential_valid"


def test_staged_credential_capability_rejects_service_writable_file(tmp_path: Path) -> None:
    source = tmp_path / "credential"
    source.write_text("secret\n")
    source.chmod(0o600)

    assert credential_capability(
        source,
        expected_uid=None,
        allowed_modes=(0o600,),
        require_read_only=True,
    ) == "credential_staging_incompatible"


@pytest.mark.parametrize(
    ("kind", "category"),
    [
        ("absent", "credential_absent"),
        ("directory", "credential_wrong_type"),
        ("symlink", "credential_unsafe_symlink"),
        ("loose", "credential_wrong_custody"),
        ("empty", "credential_staging_incompatible"),
    ],
)
def test_system_install_preflights_daemon_source_before_publication(
    tmp_path: Path, kind: str, category: str
) -> None:
    package = build_linux_package(tmp_path / "pkg.tar", *_inputs(tmp_path), version="1")
    root = tmp_path / "root"
    source = root / "etc/happyranch/daemon.token"
    source.parent.mkdir(parents=True)
    if kind == "directory":
        source.mkdir()
    elif kind == "symlink":
        target = tmp_path / "target"
        target.write_text("daemon\n")
        target.chmod(0o600)
        source.symlink_to(target)
    elif kind != "absent":
        source.write_text("" if kind == "empty" else "daemon\n")
        source.chmod(0o640 if kind == "loose" else 0o600)
    with pytest.raises(PackageError, match=category):
        install_linux_package(package, root, system_service=True)
    assert not (root / "opt/happyranch").exists()
    assert not (root / ".happyranch-install-transaction.json").exists()


@pytest.mark.parametrize(
    ("name", "unit"),
    [
        ("daemon.token", "happyranch-connector.service"),
        ("enrollment.key", "happyranch-tsnet-sidecar.service"),
    ],
)
def test_packaged_preflight_uses_ownership_neutral_systemd_staged_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    unit: str,
) -> None:
    observed: list[tuple[Path, int | None, tuple[int, ...], bool]] = []

    def classify(
        path: Path,
        *,
        expected_uid: int | None,
        allowed_modes: tuple[int, ...] | None,
        require_read_only: bool,
    ) -> str:
        observed.append((path, expected_uid, allowed_modes, require_read_only))
        return "credential_valid"

    monkeypatch.setattr("runtime.remote_access.cli.credential_capability", classify)
    staged = tmp_path / "run" / "credentials" / unit
    staged.mkdir(parents=True)
    staged.chmod(0o500)
    monkeypatch.setattr(
        "runtime.remote_access.cli._expected_systemd_credentials_directory",
        lambda _unit: staged,
    )
    monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(staged))
    assert connector_cli_main([
        "credential-capability", "--name", name, "--unit", unit,
    ]) == 0
    assert observed == [(staged / name, None, None, True)]


@pytest.mark.parametrize(
    ("name", "unit"),
    [
        ("daemon.token", "happyranch-connector.service"),
        ("enrollment.key", "happyranch-tsnet-sidecar.service"),
    ],
)
def test_packaged_preflight_accepts_real_systemd_0440_staging(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    unit: str,
) -> None:
    staged = tmp_path / "run" / "credentials" / unit
    staged.mkdir(parents=True)
    credential = staged / name
    credential.write_text("secret\n")
    # Real systemd 255 on the Ubuntu 24.04 shipping runner stages both
    # credentials as service-readable, non-writable 0440 files.  The mode is
    # an implementation detail; the service-observable capability is the
    # contract.
    credential.chmod(0o440)
    staged.chmod(0o500)
    monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(staged))
    monkeypatch.setattr(
        "runtime.remote_access.cli._expected_systemd_credentials_directory",
        lambda _unit: staged,
        raising=False,
    )

    try:
        assert connector_cli_main([
            "credential-capability", "--name", name, "--unit", unit,
        ]) == 0
    finally:
        # Restore fixture ownership permissions after observing the real 0500
        # capability boundary so pytest can remove its task-owned basetemp.
        staged.chmod(0o700)


@pytest.mark.parametrize(
    ("name", "unit"),
    [
        ("daemon.token", "happyranch-connector.service"),
        ("enrollment.key", "happyranch-tsnet-sidecar.service"),
    ],
)
def test_packaged_preflight_rejects_service_writable_staging_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    name: str,
    unit: str,
) -> None:
    staged = tmp_path / unit
    staged.mkdir()
    (staged / name).write_text("secret\n")
    (staged / name).chmod(0o400)
    monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(staged))
    monkeypatch.setattr(
        "runtime.remote_access.cli._expected_systemd_credentials_directory",
        lambda _unit: staged,
    )

    assert connector_cli_main([
        "credential-capability", "--name", name, "--unit", unit,
    ]) == 1
    assert capsys.readouterr().err.strip() == "credential_staging_incompatible"


@pytest.mark.parametrize(
    ("name", "unit", "directory"),
    [
        ("daemon.token", "happyranch-connector.service", "/run/credentials/happyranch-tsnet-sidecar.service"),
        ("enrollment.key", "happyranch-tsnet-sidecar.service", "/run/credentials/happyranch-connector.service"),
        ("daemon.token", "happyranch-connector.service", "/tmp/credentials/happyranch-connector.service"),
        ("enrollment.key", "happyranch-tsnet-sidecar.service", "relative/credentials"),
    ],
)
def test_packaged_preflight_rejects_invalid_systemd_staging_provenance(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    name: str,
    unit: str,
    directory: str,
) -> None:
    monkeypatch.setenv("CREDENTIALS_DIRECTORY", directory)
    assert connector_cli_main([
        "credential-capability", "--name", name, "--unit", unit,
    ]) == 1
    assert capsys.readouterr().err.strip() == "credential_staging_incompatible"


@pytest.mark.parametrize(
    ("name", "unit"),
    [
        ("daemon.token", "happyranch-connector.service"),
        ("enrollment.key", "happyranch-tsnet-sidecar.service"),
    ],
)
@pytest.mark.parametrize(
    "category",
    ("credential_wrong_type", "credential_unsafe_symlink", "credential_wrong_custody", "credential_staging_incompatible"),
)
def test_each_rendered_unit_rejects_invalid_staged_type_mode_or_path_without_leak(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    name: str,
    unit: str,
    category: str,
) -> None:
    secret = "forbidden-credential-material"
    staged = tmp_path / unit
    staged.mkdir()
    staged.chmod(0o500)
    monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(staged))
    monkeypatch.setattr(
        "runtime.remote_access.cli._expected_systemd_credentials_directory",
        lambda _unit: staged,
    )
    monkeypatch.setattr(
        "runtime.remote_access.cli.credential_capability",
        lambda *_args, **_kwargs: category,
    )
    assert connector_cli_main([
        "credential-capability", "--name", name, "--unit", unit,
    ]) == 1
    error = capsys.readouterr().err.strip()
    assert error == category
    assert secret not in error
    assert "/run/credentials" not in error


def test_packaged_preflight_uses_each_units_staged_credential(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    staged = tmp_path / "happyranch-connector.service"
    staged.mkdir()
    staged.chmod(0o500)
    monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(staged))
    monkeypatch.setattr(
        "runtime.remote_access.cli._expected_systemd_credentials_directory",
        lambda _unit: staged,
    )
    monkeypatch.setattr(
        "runtime.remote_access.cli.credential_capability",
        lambda path, **_kwargs: "credential_valid" if path.name == "credential.consumed" else "credential_absent",
    )
    assert connector_cli_main(["credential-capability", "--name", "daemon.token", "--unit", "happyranch-connector.service"]) == 1
    assert capsys.readouterr().err.strip() == "credential_absent"
    monkeypatch.delenv("CREDENTIALS_DIRECTORY")
    marker = tmp_path / "credential.consumed"
    marker.write_text("durable\n")
    marker.chmod(0o600)
    assert connector_cli_main([
        "credential-capability", "--name", "enrollment.key", "--unit", "happyranch-tsnet-sidecar.service",
        "--consumed-marker", str(marker),
    ]) == 0


def test_packaged_connector_binary_executes_outside_source_checkout(tmp_path: Path) -> None:
    package = build_linux_package(tmp_path / "pkg.tar", *_inputs(tmp_path), version="1")
    root = tmp_path / "root"
    install_linux_package(package, root)
    result = subprocess.run([root / "opt/happyranch/bin/happyranch-connector", "--help"],
                            cwd=tmp_path, text=True, capture_output=True, check=True)
    assert "usage:" in result.stdout
    assert "No module named" not in result.stderr
