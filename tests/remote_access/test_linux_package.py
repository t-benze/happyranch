from __future__ import annotations

from collections.abc import Iterator
import ctypes
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
        '  daemon-reload) exit "$SYSTEMCTL_RELOAD_EXIT" ;;\n'
        '  *) exit 99 ;;\n'
        "esac\n"
    )
    fixture_systemctl.chmod(0o700)

    def invoke_with_service(*arguments: str, query_exit: int = 0, reload_exit: int = 0) -> subprocess.CompletedProcess[str]:
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
            "SYSTEMCTL_RELOAD_EXIT": str(reload_exit),
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

    retirement = tmp_path / "retirement"
    retirement.mkdir()
    retirement_source = retirement / "enrollment.key"
    retirement_marker = retirement / "credential.consumed"
    retirement_dropin = retirement / "10-enrollment-credential.conf"
    for path, data in [(retirement_source, b"one-use\n"), (retirement_marker, b"durable\n"),
                       (retirement_dropin, b"[Service]\nLoadCredential=enrollment.key:/source\n")]:
        path.write_bytes(data)
        path.chmod(0o600)
    retained = {path: (path.read_bytes(), path.stat().st_mode) for path in [retirement_source, retirement_marker]}
    for command in ["retire-enrollment-source", "reconcile-enrollment-retirement"]:
        failed = invoke_with_service(
            command, "--source", str(retirement_source), "--marker", str(retirement_marker),
            "--dropin", str(retirement_dropin), reload_exit=7,
        )
        assert (failed.returncode, failed.stdout, failed.stderr) == (
            1, "", "error: enrollment_source_retirement_failed\n",
        )
        assert calls.read_text().splitlines() == ["daemon-reload"]
        assert not retirement_dropin.exists()
        assert {path: (path.read_bytes(), path.stat().st_mode) for path in retained} == retained
    recovered = invoke_with_service(
        "reconcile-enrollment-retirement", "--source", str(retirement_source),
        "--marker", str(retirement_marker), "--dropin", str(retirement_dropin),
    )
    assert (recovered.returncode, recovered.stdout, recovered.stderr) == (0, "", "")
    assert calls.read_text().splitlines() == ["daemon-reload"]
    assert not retirement_source.exists() and not retirement_source.with_suffix(".key.retiring").exists()
    assert (retirement_marker.read_bytes(), retirement_marker.stat().st_mode) == retained[retirement_marker]

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


def test_real_systemd_harness_has_valid_bash_syntax() -> None:
    result = subprocess.run(
        ["bash", "-n", "app/linux/package/real_systemd_n3.sh"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr




def _shipping_declarations(harness: str) -> str:
    '''Extract actual top-level shell declarations, including their heredocs.'''
    declarations = []
    for declaration in re.finditer(r"(?m)^\w+\(\) \{", harness):
        line_end = harness.index("\n", declaration.start())
        if harness[declaration.start():line_end].endswith("}"):
            end = line_end
        else:
            closing = re.search(r"(?m)^\}$", harness[line_end:])
            assert closing is not None
            end = line_end + closing.end()
        declarations.append(harness[declaration.start():end])
    return "\n".join(declarations)


def _shipping_task_variable(harness: str) -> str:
    assignment = re.search(r'(?m)^(\w+)="\$\(mktemp -d\)"$', harness)
    assert assignment is not None, "shipping task-root allocation missing"
    return assignment[1]


def _run_shipping_fragment(
    tmp_path: Path, fragment: str, *, peer: object = None,
) -> tuple[subprocess.CompletedProcess[str], list[list[str]]]:
    '''Execute selected shipping statements; intercept external commands before forwarding.'''
    harness = Path("app/linux/package/real_systemd_n3.sh").read_text()
    task_variable = _shipping_task_variable(harness)
    (tmp_path / "hs").mkdir()
    (tmp_path / "tls").mkdir()
    for name in ("daemon.token", "enrollment.key"):
        (tmp_path / name).write_text("synthetic-fixture-only\n")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fixture = fake_bin / "fixture"
    fixture.write_text(f"#!{sys.executable}\n" + r'''
import json, os, pathlib, sys
root = pathlib.Path(os.environ["FRAGMENT_ROOT"])
argv = [pathlib.Path(sys.argv[0]).name, *sys.argv[1:]]
with (root / "argv.jsonl").open("a") as stream: stream.write(json.dumps(argv) + "\n")
if argv[0] == "stat":
    if argv[1:3] != ["-c", "%U:%G:%a"]: raise SystemExit(64)
    print("root:root:755")
elif argv[0] == "curl":
    pass  # Observation only: no curl request is forwarded.
elif argv[0] == "sudo":
    args = argv[1:]
    barrier = "/var/lib/happyranch-tsnet-sidecar/.n3-barrier-fixture"
    executables = ["/opt/happyranch/bin/happyranch-connector", "/opt/happyranch/bin/happyranch-tsnet-sidecar"]
    if args[:2] == ["-u", "happyranch"] and args[2:4] == ["test", "-x"] and args[4:] in [[x] for x in executables]: pass
    elif args[:7] == ["install", "-m", "0600", "-o", "root", "-g", "root"] and args[7:] in [[str(root / name), "/etc/happyranch/" + name] for name in ["daemon.token", "enrollment.key"]]: pass
    elif args[:2] == ["test", "-e"]: pass  # Observe only; never forward path probes.
    elif args[:1] == ["tee"] and args[1:] in [[barrier + "/" + name] for name in ["start-release", "stop-release"]]: sys.stdin.read()
    elif args == ["install", "-d", "-m", "0700", "-o", "happyranch", "-g", "happyranch", barrier]: pass
    elif args == ["rm", "-f", *[barrier + "/" + name for name in ["start-entered", "start-release", "stop-entered", "stop-release"]]]: pass
    elif args == ["rmdir", barrier]: pass
    elif args == [str(root / "tailscale"), "--socket=" + str(root / "peer.sock"), "status", "--json"]: print(os.environ["PEER_JSON"])
    else: raise SystemExit(64)
else: raise SystemExit(64)
''')
    fixture.chmod(0o700)
    for command in ("sudo", "stat", "curl"):
        (fake_bin / command).symlink_to(fixture)
    # Retain the actual external barrier assignment; command fixtures record
    # its selected paths without touching them.
    barrier_assignment = re.search(r'(?m)^(\w+)="[^\n]*\.n3-barrier-\$run_id"$', harness)
    assert barrier_assignment is not None
    script = f'''set -euo pipefail
{task_variable}={str(tmp_path)!r}; diagnostics={str(tmp_path)!r}; ts_dir={str(tmp_path)!r}; run_id=fixture
{barrier_assignment[0]}
{_shipping_declarations(harness)}
{fragment}
'''
    result = subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, check=False, timeout=10,
        env=os.environ | {"PATH": f"{fake_bin}:{Path(sys.executable).parent}:/usr/bin:/bin",
                          "FRAGMENT_ROOT": str(tmp_path), "PEER_JSON": json.dumps({"Peer": peer})},
    )
    log = tmp_path / "argv.jsonl"
    return result, [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


def test_real_systemd_harness_uses_headscale_025_policy_schema(tmp_path: Path) -> None:
    harness = Path("app/linux/package/real_systemd_n3.sh").read_text()
    fragment = harness[harness.index('cat >'):harness.index('sudo install -m 0644')]
    result, _ = _run_shipping_fragment(tmp_path, fragment)
    policy = tmp_path / "hs/policy.json"
    observed = json.loads(policy.read_text()) if policy.exists() else None
    assert observed == {"acls": [{"action": "accept", "src": ["*"], "dst": ["*:*"]}]}, observed
    assert result.returncode == 0, result.stderr


def test_real_systemd_harness_quiesces_failed_staging_before_first_enrollment(tmp_path: Path) -> None:
    result, events, _ = _run_positive_start_cleanup_scenario(tmp_path, outer_startup=True)
    observations = [json.loads(event) for event in events]
    restores = [event for event in observations if event["event"] == "restore"]
    assert len(restores) == 1, observations
    assert restores[0] == {
        "event": "restore", "active": False, "main_pid": 0,
        "restart_pending": False, "staging_present": False, "staging_checks": 2,
        "credential": "fixture-enrollment",
    }, observations
    resets = [event["argv"] for event in observations if event["event"] == "negative-reset"]
    assert resets == [["reset-failed", "happyranch-tsnet-sidecar.service", "happyranch-connector.service"]], observations
    checks = [event["path"] for event in observations if event["event"] == "staging-check"]
    assert checks == ["/run/credentials/happyranch-tsnet-sidecar.service"] * 2, observations
    assert result.returncode == 0, result.stderr



def test_real_systemd_harness_keeps_headscale_control_socket_in_task_root(tmp_path: Path) -> None:
    import yaml

    harness = Path("app/linux/package/real_systemd_n3.sh").read_text()
    fragment = harness[harness.index('cat >'):harness.index('sudo install -m 0644')]
    result, _ = _run_shipping_fragment(tmp_path, fragment)
    config = tmp_path / "hs/config.yaml"
    observed = yaml.safe_load(config.read_text()) if config.exists() else {}
    assert observed.get("unix_socket") == str(tmp_path / "hs/headscale.sock"), observed
    assert result.returncode == 0, result.stderr



def test_real_systemd_harness_probes_headscale_health_over_configured_https(tmp_path: Path) -> None:
    harness = Path("app/linux/package/real_systemd_n3.sh").read_text()
    fragment = "\n".join(line for line in harness.splitlines() if re.match(r'^\w+ "Headscale health" ', line))
    result, argv = _run_shipping_fragment(tmp_path, fragment)
    assert argv == [["curl", "--silent", "--fail", "--cacert", str(tmp_path / "tls/cert.pem"), "https://127.0.0.1:18080/health"]], argv
    assert result.returncode == 0, result.stderr



def test_real_systemd_harness_proves_root_owned_binary_is_service_executable(tmp_path: Path) -> None:
    harness = Path("app/linux/package/real_systemd_n3.sh").read_text()
    start = next(line for line in harness.splitlines() if '|| fail "system-service payload root custody mismatch"' in line)
    fragment = harness[harness.index(start):harness.index('sudo systemctl daemon-reload', harness.index(start))]
    result, argv = _run_shipping_fragment(tmp_path, fragment)
    expected = [["stat", "-c", "%U:%G:%a", path] for path in ["/opt/happyranch", "/opt/happyranch/bin"]]
    for binary in ["/opt/happyranch/bin/happyranch-connector", "/opt/happyranch/bin/happyranch-tsnet-sidecar"]:
        expected.extend([["stat", "-c", "%U:%G:%a", binary], ["sudo", "-u", "happyranch", "test", "-x", binary]])
    assert argv == expected, argv
    assert result.returncode == 0, result.stderr



def test_real_systemd_harness_keeps_load_credential_source_root_custodied(tmp_path: Path) -> None:
    harness = Path("app/linux/package/real_systemd_n3.sh").read_text()
    initial = harness[:harness.index('|| fail "system-service payload root custody mismatch"')]
    fragment = "\n".join(line for line in initial.splitlines() if line.startswith('sudo install ') and any(name in line for name in ['daemon.token', 'enrollment.key']))
    result, argv = _run_shipping_fragment(tmp_path, fragment)
    assert argv == [["sudo", "install", "-m", "0600", "-o", "root", "-g", "root", str(tmp_path / name), "/etc/happyranch/" + name] for name in ["daemon.token", "enrollment.key"]], argv
    assert result.returncode == 0, result.stderr



def test_real_systemd_early_failure_cleanup_is_bounded_and_redacted(tmp_path: Path) -> None:
    harness = Path("app/linux/package/real_systemd_n3.sh").read_text()
    assert 'cp "$work/$log.log" "$diagnostics/$log.log"' not in harness
    assert 'journalctl -u happyranch-connector.service -u happyranch-tsnet-sidecar.service' not in harness
    started = time.monotonic()
    result, events, work = _run_positive_start_cleanup_scenario(tmp_path, headscale_capture=True, early_failure=True)
    assert time.monotonic() - started < 40
    assert result.returncode == 37, (result.returncode, result.stderr)
    assert "systemctl:start" not in events, events
    assert "headscale:queried" in events and events.index("headscale:queried") < events.index("systemctl:stop"), events
    _assert_secret_free_diagnostics(result, tmp_path / "diagnostics")
    assert (tmp_path / "diagnostics/cleanup-status.txt").read_text() == "fixtures_reaped=1\n"
    assert not work.exists()
    bounds = [json.loads(line) for line in (tmp_path / "timeout-argv.jsonl").read_text().splitlines()]
    assert len(bounds) == 22, bounds
    assert all(argv[0] == "--kill-after=1" and argv[1] in {"1", "3", "16"} for argv in bounds), bounds



def test_real_systemd_missing_credential_accepts_null_peer_map_as_no_identity(tmp_path: Path) -> None:
    harness = Path("app/linux/package/real_systemd_n3.sh").read_text()
    fragment = "\n".join(line for line in harness.splitlines() if '|| fail "failed-start TSNet identity remained visible"' in line)
    cases = [None, {}, {"other": {"HostName": "unrelated"}}, {"sidecar": {"HostName": "home-sidecar-ci"}}]
    for index, peer in enumerate(cases):
        root = tmp_path / str(index); root.mkdir()
        result, argv = _run_shipping_fragment(root, fragment, peer=peer)
        assert argv == [["sudo", str(root / "tailscale"), "--socket=" + str(root / "peer.sock"), "status", "--json"]], argv
        assert result.returncode == (1 if index == 3 else 0), (peer, result.returncode, result.stderr)


def test_real_systemd_uses_plain_shipping_unit_without_af_netlink_ab_arms(tmp_path: Path) -> None:
    harness = Path("app/linux/package/real_systemd_n3.sh").read_text()
    assert "acceptance_arms" not in harness
    assert "ordering-a-control" not in harness
    assert "ordering-a-candidate" not in harness
    assert "ordering-b-candidate" not in harness
    assert "ordering-b-control" not in harness
    assert "RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX AF_NETLINK" in harness
    assert "90-ci-af-netlink.conf" not in harness
    result, events, _ = _run_positive_start_cleanup_scenario(tmp_path, outer_startup=True)
    observations = [json.loads(event) for event in events]
    resets = [event for event in observations if event["event"] == "shipping-reset-boundary"]
    assert resets == [{
        "event": "shipping-reset-boundary", "installed": True,
        "reset_log": "shipping_unit_reset=complete cleanup_complete=true\n",
    }], observations
    probes = [event for event in observations if event["event"] == "denial-argv"]
    assert probes == [{"event": "denial-argv", "argv": [
        "15", "systemd-run", "--quiet", "--wait", "--collect", "--pipe",
        "--unit=happyranch-n3-denial-shipping-unit", "--property=User=happyranch",
        "--property=Group=happyranch", "--property=NoNewPrivileges=yes",
        "--property=PrivateDevices=yes", "--property=ProtectSystem=strict",
        "--property=ProtectHome=yes", "--property=ReadWritePaths=/var/lib/happyranch-tsnet-sidecar",
        "--property=RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX AF_NETLINK",
        "--property=CapabilityBoundingSet=", "/usr/bin/python3", "-", "shipping-unit",
    ]}], observations
    installs = [event for event in observations if event["event"] == "reset-install"]
    cleanup_resets = [event for event in observations if event["event"] == "reset-cleanup"]
    reloads = [event for event in observations if event["event"] == "reset-reload"]
    starts = [event for event in observations if event["event"] == "start"]
    assert len(installs) == len(reloads) == 1 and len(starts) == 2, observations
    assert cleanup_resets == [{"event": "reset-cleanup", "argv": [
        "reset-failed", "happyranch-managed.target", "happyranch-tsnet-sidecar.service",
        "happyranch-connector.service",
    ]}], observations
    assert observations.index(cleanup_resets[0]) < observations.index(installs[0]), observations
    assert observations.index(installs[0]) < observations.index(reloads[0]) < observations.index(resets[0]), observations
    assert observations.index(resets[0]) < observations.index(starts[0]) < observations.index(probes[0]) < observations.index(starts[1]), observations
    assert result.returncode == 0, result.stderr


def test_real_systemd_denial_probe_follows_shipping_state_directory_creation(tmp_path: Path) -> None:
    result, events, _ = _run_positive_start_cleanup_scenario(tmp_path, outer_startup=True)
    observations = [json.loads(event) for event in events]
    starts = [event for event in observations if event["event"] == "start"]
    probes = [event for event in observations if event["event"] == "denial-probe"]
    assert starts == [
        {"event": "start", "credential_present": False, "state_directory_created": True, "exit": 1},
        {"event": "start", "credential_present": True, "state_directory_created": False, "exit": 0},
    ], observations
    assert probes == [{"event": "denial-probe", "state_directory_present": True}], observations
    assert observations.index(starts[0]) < observations.index(probes[0]) < observations.index(starts[1]), observations
    assert result.returncode == 0, result.stderr


def _run_shipping_denial_probes(tmp_path: Path) -> tuple[subprocess.CompletedProcess[str], list[list[str]], dict[str, object]]:
    """Run the real measurement body and validator with non-forwarding external fixtures."""
    harness = Path("app/linux/package/real_systemd_n3.sh").read_text()
    fake_bin = tmp_path / "bin"; fake_bin.mkdir()
    fixture = fake_bin / "fixture"
    fixture.write_text(f"#!{sys.executable}\n" + r'''
import builtins, errno, json, os, pathlib, socket, subprocess, sys
root = pathlib.Path(os.environ["DENIAL_ROOT"])
argv = [pathlib.Path(sys.argv[0]).name, *sys.argv[1:]]
def record(value):
    with (root / "argv.jsonl").open("a") as stream: stream.write(json.dumps(value) + "\n")
record(argv)
if argv[0] == "sudo":
    expected = ["timeout", "15", "systemd-run", "--quiet", "--wait", "--collect", "--pipe",
        "--unit=happyranch-n3-denial-shipping-unit", "--property=User=happyranch", "--property=Group=happyranch",
        "--property=NoNewPrivileges=yes", "--property=PrivateDevices=yes", "--property=ProtectSystem=strict",
        "--property=ProtectHome=yes", "--property=ReadWritePaths=/var/lib/happyranch-tsnet-sidecar",
        "--property=RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX AF_NETLINK", "--property=CapabilityBoundingSet=",
        "/usr/bin/python3", "-", "shipping-unit"]
    if argv[1:] != expected: raise SystemExit(64)
    body = sys.stdin.read()
    class ClosedFixture:
        def close(self): pass
    def socket_result(family, kind, protocol=0):
        record(["socket", family, kind, protocol])
        if (family, kind, protocol) == (socket.AF_NETLINK, socket.SOCK_RAW, 0): return ClosedFixture()
        if (family, kind, protocol) == (socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_ICMP): raise PermissionError(errno.EPERM, "synthetic")
        raise SystemExit(64)
    def device_result(path, mode, buffering=-1):
        record(["device", path, mode, buffering])
        if (path, mode, buffering) != ("/dev/net/tun", "rb", 0): raise SystemExit(64)
        raise PermissionError(errno.EACCES, "synthetic")
    def write_result(path, flags, mode):
        record(["write", path, flags, mode])
        if (path, flags, mode) != ("/var/lib/happyranch-tsnet-sidecar/probe-write", os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600): raise SystemExit(64)
        return os.dup(sys.stdout.fileno())
    def unlink_result(path):
        record(["unlink", path])
        if path != "/var/lib/happyranch-tsnet-sidecar/probe-write": raise SystemExit(64)
    def control_result(address, timeout):
        record(["control", list(address), timeout])
        if (address, timeout) != (("127.0.0.1", 18080), 2): raise SystemExit(64)
        return ClosedFixture()
    socket.socket = socket_result; socket.create_connection = control_result
    builtins.open = device_result; os.open = write_result; os.unlink = unlink_result
    sys.argv = ["-", "shipping-unit"]
    exec(compile(body, "<shipping-denial-probes>", "exec"))
elif argv[0] == "python":
    expected = [os.environ["EVIDENCE_DRIVER"], "validate-denial-matrix", str(root / "shipping-unit-denial-matrix.json"), "--expected-arm", "shipping-unit"]
    if argv[1:] != expected: raise SystemExit(64)
    raise SystemExit(subprocess.run([sys.executable, *argv[1:]], check=False).returncode)
else: raise SystemExit(64)
''')
    fixture.chmod(0o700)
    for command in ("sudo", "python"):
        (fake_bin / command).symlink_to(fixture)
    driver = Path("app/linux/package/n3_evidence.py").resolve()
    # Select the real main consumer by its external shipping arm, independently
    # of the declaration's private identifier; an omitted consumer executes none.
    consumer = "\n".join(re.findall(r"(?m)^\w+ shipping-unit$", harness))
    script = f'''set -euo pipefail
diagnostics={str(tmp_path)!r}; evidence_driver={str(driver)!r}
{_shipping_declarations(harness)}
{consumer}
'''
    result = subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, check=False, timeout=10,
        env=os.environ | {"PATH": f"{fake_bin}:{Path(sys.executable).parent}:/usr/bin:/bin", "DENIAL_ROOT": str(tmp_path), "EVIDENCE_DRIVER": str(driver)},
    )
    log = tmp_path / "argv.jsonl"; matrix = tmp_path / "shipping-unit-denial-matrix.json"
    return result, [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else [], json.loads(matrix.read_text()) if matrix.exists() and matrix.read_text().strip() else {}

def test_real_systemd_denial_matrix_executes_every_bounded_probe(tmp_path: Path) -> None:
    import socket

    result, argv, matrix = _run_shipping_denial_probes(tmp_path)
    external = [row[0] for row in argv if row[0] in {"socket", "device", "write", "unlink", "control"}]
    assert external == ["socket", "socket", "device", "write", "unlink", "control"], argv
    assert [row for row in argv if row[0] == "socket"] == [["socket", socket.AF_NETLINK, socket.SOCK_RAW, 0], ["socket", socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_ICMP]], argv
    operations = matrix.get("operations", [])
    assert operations == [
        {"id": name, "measured": True, "result": result, "category": category, "errno": code}
        for name, result, category, code in [
            ("address_family_netlink", "allow", "none", None), ("linux_capabilities", "deny", "permission_denied", "EPERM"),
            ("device_access", "deny", "permission_denied", "EACCES"), ("writable_paths", "allow", "none", None),
            ("control_plane_operations", "allow", "none", None),
        ]
    ], matrix
    launches = [row for row in argv if row[0] == "sudo"]
    assert len(launches) == 1 and launches[0][1:8] == ["timeout", "15", "systemd-run", "--quiet", "--wait", "--collect", "--pipe"], argv
    for property in ["User=happyranch", "Group=happyranch", "NoNewPrivileges=yes", "PrivateDevices=yes", "ProtectSystem=strict", "ProtectHome=yes", "ReadWritePaths=/var/lib/happyranch-tsnet-sidecar", "RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX AF_NETLINK", "CapabilityBoundingSet="]:
        assert "--property=" + property in launches[0], launches
    validators = [row for row in argv if row[0] == "python"]
    assert validators == [["python", str(Path("app/linux/package/n3_evidence.py").resolve()), "validate-denial-matrix", str(tmp_path / "shipping-unit-denial-matrix.json"), "--expected-arm", "shipping-unit"]], argv
    assert result.returncode == 0, result.stderr


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
    assert "headscale" in snapshot, "failure-only Headscale observation missing"
    assert snapshot["headscale"]["process_state"] == "unknown"
    assert snapshot["headscale"]["nodes"]["peer"] == {"count": None, "state": "unknown"}
    assert snapshot["headscale"]["nodes"]["losses"] == ["unavailable"]
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
    assert snapshot["collection"]["source"] == "systemctl-filtered-sidecar-text-and-attributed-systemd-journals"
    assert snapshot["collection"]["window_seconds"] == 45
    assert snapshot["collection"]["output_cap_bytes"] == 24064
    assert snapshot["collection"]["budget_model"] == "reserved-sections"
    assert snapshot["diagnostic_receipts"] == [{"category": "network_join", "phase": "peer_establishment"}]
    assert snapshot["observation_loss"]["diagnostic_receipts"] == ["parse_loss"]
    assert snapshot["observation_loss"]["happyranch-connector.service.exec_start_pre_status"] == "not_collected"
    assert "jobs" not in snapshot["observation_loss"]



def test_real_systemd_labels_deliberate_negative_credential_leg_as_expected(tmp_path: Path) -> None:
    result, events, _ = _run_positive_start_cleanup_scenario(tmp_path, outer_startup=True)
    argv = [json.loads(event)["argv"] for event in events if json.loads(event)["event"] == "diagnostic-argv"]
    assert argv == [["diagnose", str(tmp_path / "diagnostics/execution-evidence.json"), "--id", "fixture:negative-leg-expected:credential_input", "--category", "credential_input", "--phase", "input_acquisition", "--actor", "systemd", "--unit", "happyranch-tsnet-sidecar.service"]], argv
    expectations = (tmp_path / "diagnostics/diagnostic-expectations.log").read_text()
    assert expectations == "diagnostic_id=fixture:negative-leg-expected:credential_input expectation=expected category=credential_input\n", expectations
    assert result.returncode == 0, result.stderr


def _assert_secret_free_diagnostics(result: subprocess.CompletedProcess[str], diagnostics: Path) -> None:
    sentinels = ("KEY_CANARY", "TOKEN_CANARY", "CREDENTIAL_CANARY", "ADDRESS_CANARY", "URL_CANARY", "CONFIG_CANARY", "BACKEND_CANARY", "100.64.2.3")

    def safe(value: object) -> bool:
        if isinstance(value, dict):
            return all(safe(key) and safe(item) for key, item in value.items())
        if isinstance(value, list):
            return all(safe(item) for item in value)
        if isinstance(value, str):
            if any(sentinel in value for sentinel in sentinels):
                return False
            nested = value.removeprefix("diagnostic_receipt=")
            if nested.startswith(("{", "[")):
                try:
                    return safe(json.loads(nested))
                except ValueError:
                    pass
        return True

    assert safe(result.stdout) and safe(result.stderr), "private sentinel in subprocess output"
    for artifact in diagnostics.iterdir():
        raw = artifact.read_bytes()
        assert not any(sentinel.encode() in raw for sentinel in sentinels), "private sentinel in diagnostic artifact bytes"
        try:
            value = json.loads(raw)
        except ValueError:
            value = raw.decode("utf-8")
        assert safe(value), "private sentinel in decoded diagnostic artifact"


def _run_seq305_failure_snapshot(
    tmp_path: Path,
    *,
    sidecar_mode: str = "observed",
    jobs_mode: str = "observed",
    headscale_mode: str = "unavailable",
    headscale_log_mode: str = "observed",
    watch_private_buffers: bool = False,
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
  if [[ " $* " == *" -o cat "* ]]; then
    printf 'journal:plain\n' >>"$EVENT_LOG"
    printf '%s\n' 'diagnostic_receipt={"actor":"tsnet-sidecar","assertion":{"status":"completed"},"category":"network_join","outcome":"failed","phase":"peer_establishment","terminal":true,"unit":"happyranch-tsnet-sidecar.service"}'
    exit 0
  fi
  printf 'journal:receipt\n' >>"$EVENT_LOG"
  printf '%s\n' "$RECEIPT"
fi
""")
    for executable in fake_bin.iterdir():
        executable.chmod(0o700)
    fixture = tmp_path / "fixture"
    fixture.mkdir(mode=0o700)
    if headscale_mode != "unavailable":
        (fixture / "hs").mkdir()
        (fixture / "hs/config.yaml").write_text("CONFIG_CANARY\n")
        (fixture / "headscale.log").write_text(
            '{"level":"error","caller":"hscontrol/noise.go:160","message":"unsupported client connected","node_key":"KEY_CANARY"}\n'
        )
        (fixture / "headscale").write_text("""#!/usr/bin/env python3
import json,os,pathlib,signal,subprocess,sys,time
root=pathlib.Path(__file__).parent
assert sys.argv[1:]==['nodes','list','--config',str(root/'hs/config.yaml'),'--output','json']
with open(os.environ['EVENT_LOG'],'a') as stream: stream.write('headscale:nodes\\n')
mode=os.environ['HEADSCALE_MODE']
nodes=[{'name':'synthetic-peer-ci','online':True,'ip_addresses':['ADDRESS_CANARY']},{'name':'home-sidecar-ci','token':'TOKEN_CANARY'}]
if mode=='query_error': print(json.dumps(nodes)); print('CREDENTIAL_CANARY',file=sys.stderr); sys.exit(7)
if mode=='timeout': time.sleep(60)
if mode=='grandchild':
 child=subprocess.Popen([sys.executable,'-c','import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); time.sleep(60)'])
 (root/'capture-pids').write_text(str(os.getpid())+' '+str(child.pid))
 time.sleep(60)
if mode=='oversized': print('URL_CANARY'+'x'*65536)
elif mode=='malformed': print('not-json BACKEND_CANARY')
elif mode=='empty': print('[]')
elif mode=='empty_null': print('null')
elif mode=='empty_stdout': pass
elif mode=='exact_bytes':
 raw=json.dumps(nodes); sys.stdout.write(raw+' '*(65536-len(raw)))
elif mode=='node_limit': print(json.dumps([{'name':'synthetic-peer-ci'}]*64))
elif mode=='node_overflow': print(json.dumps([{'name':'synthetic-peer-ci'}]*65))
elif mode=='partial': print(json.dumps(nodes+[{'given_name':'home-sidecar-ci'}]))
else: print(json.dumps(nodes))
""")
        (fixture / "headscale").chmod(0o700)
        for private in (fixture / "hs/config.yaml", fixture / "headscale.log"):
            private.chmod(0o600)
        log_path = fixture / "headscale.log"
        if headscale_log_mode in {"missing", "fifo", "symlink"}:
            log_path.unlink()
            if headscale_log_mode == "fifo":
                os.mkfifo(log_path, mode=0o600)
            elif headscale_log_mode == "symlink":
                log_path.symlink_to(fixture / "hs/config.yaml")
        elif headscale_log_mode == "unreadable":
            log_path.chmod(0)
        elif headscale_log_mode == "empty":
            log_path.write_bytes(b"")
        elif headscale_log_mode == "malformed":
            log_path.write_bytes(b"invalid TOKEN_CANARY\n")
        elif headscale_log_mode == "invalid_utf8":
            log_path.write_bytes(b"\xff\n")
        elif headscale_log_mode == "partial":
            with log_path.open("ab") as stream:
                stream.write(b"invalid TOKEN_CANARY\n")
        elif headscale_log_mode in {"exact_bytes", "oversized"}:
            prefix = b'{"level":"info","message":"history","padding":"'
            suffix = b'"}\n'
            size = 65536 if headscale_log_mode == "exact_bytes" else 65537
            log_path.write_bytes(prefix + b"x" * (size - len(prefix) - len(suffix)) + suffix)
        elif headscale_log_mode == "line_overflow":
            log_path.write_bytes(b'{"level":"info","message":"history"}\n' * 257)
    if watch_private_buffers:
        (fake_bin / "mktemp").write_text("""#!/usr/bin/env python3
import json,os,pathlib,stat,subprocess,sys
result=subprocess.run(['/usr/bin/mktemp',*sys.argv[1:]],capture_output=True,text=True)
if result.returncode==0:
 path=pathlib.Path(result.stdout.strip())
 if path.name.startswith(('.n3-observe.','.n3-jobs.','.n3-invocation.','.n3-receipts.')):
  observation={'private_parent':path.parent==pathlib.Path(os.environ['PRIVATE_FIXTURE']), 'mode':stat.S_IMODE(path.stat().st_mode)}
  with open(os.environ['PRIVATE_BUFFER_EVIDENCE'],'a') as stream: stream.write(json.dumps(observation)+'\\n')
sys.stdout.write(result.stdout)
sys.exit(result.returncode)
""")
        (fake_bin / "mktemp").chmod(0o700)
    script = f'''set -euo pipefail
diagnostics={tmp_path / 'diagnostics'!s}; mkdir -p "$diagnostics"
work={fixture!s}; headscale_pid=$$
capture_window_since_us=1699999999999999
sudo() {{ printf 'presence:%s\n' "$*" >>"$EVENT_LOG"; return 1; }}
timeout() {{ while [[ $1 == --* || $1 =~ ^[0-9]+$ ]]; do shift; done; "$@"; }}
failure_capture_driver={Path("app/linux/package/n3_failure_capture.py").resolve()!s}
run_id=seq305-red-green
{snapshot_helpers}
capture_failure_snapshot first-positive-start-failure
cat "$diagnostics/first-positive-start-failure.json"
'''
    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=False, timeout=30, env=os.environ | {
        "PATH": f"{fake_bin}:{os.environ['PATH']}", "EVENT_LOG": str(event_log), "INVOCATION": invocation,
        "BOOT": boot, "RECEIPT": receipt, "JOB": job, "DEPENDENCY": dependency, "CONNECTOR_JOB": connector_job,
        "SIDECAR_MODE": sidecar_mode, "JOBS_MODE": jobs_mode, "N3_UNIT_ROOT": str(tmp_path),
        "HEADSCALE_MODE": headscale_mode, "PRIVATE_FIXTURE": str(fixture),
        "PRIVATE_BUFFER_EVIDENCE": str(tmp_path / "private-buffer-evidence"),
    })
    return result, event_log


@pytest.mark.parametrize("mode,losses,count", [
    ("empty", ["empty"], 0), ("missing", ["unavailable"], 0), ("fifo", ["unavailable"], 0),
    ("symlink", ["unavailable"], 0), ("unreadable", ["unavailable"], 0), ("malformed", ["parse_loss"], 0), ("invalid_utf8", ["parse_loss"], 0),
    ("partial", ["parse_loss"], 1), ("exact_bytes", ["observed"], 1),
    ("oversized", ["parse_loss", "truncated"], 0), ("line_overflow", ["truncated"], 256),
])
def test_seq322_shipping_log_bounds_preserve_nodes_and_sidecar(tmp_path: Path, mode: str, losses: list[str], count: int) -> None:
    result, _ = _run_seq305_failure_snapshot(tmp_path, headscale_mode="observed", headscale_log_mode=mode)
    assert result.returncode == 0, result.stderr
    snapshot = json.loads(result.stdout)
    assert snapshot["headscale"]["log"]["losses"] == losses
    assert sum(event["count"] for event in snapshot["headscale"]["log"]["events"]) == count
    assert snapshot["headscale"]["nodes"]["peer"] == {"count": 1, "state": "online"}
    assert snapshot["diagnostic_receipts"] == [{"category": "network_join", "phase": "peer_establishment"}]
    for artifact in (tmp_path / "diagnostics").iterdir():
        assert b"CANARY" not in artifact.read_bytes()
    _assert_secret_free_diagnostics(result, tmp_path / "diagnostics")


def test_seq322_shipping_raw_buffers_are_private_and_removed(tmp_path: Path) -> None:
    result, _ = _run_seq305_failure_snapshot(tmp_path, headscale_mode="observed", watch_private_buffers=True)
    assert result.returncode == 0, result.stderr
    observations = [json.loads(line) for line in (tmp_path / "private-buffer-evidence").read_text().splitlines()]
    assert observations and all(item == {"private_parent": True, "mode": 0o600} for item in observations)
    assert not list((tmp_path / "fixture").glob(".n3-*"))
    assert not list((tmp_path / "diagnostics").glob(".n3-*"))
    _assert_secret_free_diagnostics(result, tmp_path / "diagnostics")


def test_seq322_shipping_sidecar_and_job_exhaustion_preserves_reserved_headscale(tmp_path: Path) -> None:
    result, _ = _run_seq305_failure_snapshot(tmp_path, sidecar_mode="truncation", jobs_mode="truncation", headscale_mode="observed")
    assert result.returncode == 0, result.stderr
    snapshot = json.loads(result.stdout)
    assert snapshot["observation_loss"]["happyranch-tsnet-sidecar.service.active"] == "truncated"
    assert snapshot["observation_loss"]["jobs"] == "truncated"
    assert snapshot["headscale"]["nodes"]["peer"] == {"count": 1, "state": "online"}
    assert snapshot["headscale"]["log"]["losses"] == ["observed"]
    assert snapshot["diagnostic_receipts"] == [{"category": "network_join", "phase": "peer_establishment"}]


@pytest.fixture
def capture_descendant_watchdog(tmp_path: Path) -> Iterator[None]:
    """Own failed-mutation descendants too; never leave a fixture to PID1."""
    libc = ctypes.CDLL(None, use_errno=True) if sys.platform.startswith("linux") else None
    previous = ctypes.c_int()
    if libc is not None:
        assert libc.prctl(37, ctypes.byref(previous), 0, 0, 0) == 0
        assert libc.prctl(36, 1, 0, 0, 0) == 0
    try:
        yield
    finally:
        pid_file = tmp_path / "fixture/capture-pids"
        if pid_file.exists():
            for text in pid_file.read_text().split():
                pid = int(text)
                try:
                    os.kill(pid, 9)
                except ProcessLookupError:
                    pass
                deadline = time.monotonic() + 2
                while time.monotonic() < deadline:
                    try:
                        observed, _ = os.waitpid(pid, os.WNOHANG)
                    except ChildProcessError:
                        break
                    if observed:
                        break
                    time.sleep(0.01)
                assert not Path(f"/proc/{pid}").exists(), "test watchdog could not reap owned descendant"
        if libc is not None:
            assert libc.prctl(36, previous.value, 0, 0, 0) == 0


@pytest.mark.parametrize("mode,loss", [
    ("observed", "observed"), ("empty", "empty"), ("unavailable", "unavailable"),
    ("query_error", "query_error"), ("timeout", "timeout"), ("oversized", "truncated"),
    ("malformed", "parse_loss"), ("partial", "parse_loss"),
    ("grandchild", "timeout"), ("empty_null", "empty"), ("empty_stdout", "parse_loss"),
    ("exact_bytes", "observed"), ("node_limit", "observed"), ("node_overflow", "truncated"),
])
def test_seq322_shipping_headscale_reserved_capture(tmp_path: Path, capture_descendant_watchdog: object, mode: str, loss: str) -> None:
    result, events = _run_seq305_failure_snapshot(tmp_path, headscale_mode=mode)
    assert result.returncode == 0, result.stderr
    snapshot = json.loads(result.stdout)
    assert "headscale" in snapshot, "failure-only Headscale observation missing"
    headscale = snapshot["headscale"]
    assert headscale["process_state"] == "running"
    assert headscale["nodes"]["losses"] == [loss]
    if mode in {"observed", "partial", "exact_bytes"}:
        assert headscale["nodes"]["peer"] == {"count": 1, "state": "unknown" if mode == "partial" else "online"}
        assert headscale["nodes"]["sidecar"] == {"count": 1, "state": "unknown" if mode == "partial" else "offline"}
    elif mode == "node_limit":
        assert headscale["nodes"]["peer"] == {"count": 64, "state": "offline"}
    elif mode in {"empty", "empty_null"}:
        assert headscale["nodes"]["peer"] == {"count": 0, "state": "absent"}
    else:
        assert headscale["nodes"]["peer"] == {"count": None, "state": "unknown"}
    assert snapshot["diagnostic_receipts"] == [{"category": "network_join", "phase": "peer_establishment"}]
    if mode != "unavailable":
        assert headscale["log"] == {"events": [{"event": "unsupported_client", "count": 1}], "losses": ["observed"]}
        ordering = events.read_text().splitlines()
        assert ordering.index("journal:plain") < ordering.index("headscale:nodes")
        assert ordering.index("headscale:nodes") < next(i for i, value in enumerate(ordering) if value.startswith("show:happyranch-connector.service:"))
    for sentinel in ("KEY_CANARY", "TOKEN_CANARY", "CREDENTIAL_CANARY", "ADDRESS_CANARY", "URL_CANARY", "CONFIG_CANARY", "BACKEND_CANARY"):
        assert sentinel not in result.stdout + result.stderr
        for artifact in (tmp_path / "diagnostics").iterdir():
            assert sentinel.encode() not in artifact.read_bytes()
    if mode == "grandchild":
        for pid in (tmp_path / "fixture/capture-pids").read_text().split():
            assert not Path(f"/proc/{pid}").exists(), "observation descendant was not reaped"
    _assert_secret_free_diagnostics(result, tmp_path / "diagnostics")


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
    assert snapshot["sidecar_failure_lines"] == [
        'diagnostic_receipt={"actor":"tsnet-sidecar","assertion":{"status":"completed"},"category":"network_join","outcome":"failed","phase":"peer_establishment","terminal":true,"unit":"happyranch-tsnet-sidecar.service"}',
    ]
    events = event_log.read_text().splitlines()
    sidecar_last = max(
        index for index, event in enumerate(events)
        if event.startswith("show:happyranch-tsnet-sidecar.service:") and not event.endswith(":InvocationID")
    )
    jobs_index = events.index("journal:jobs")
    receipt_index = events.index("journal:receipt")
    later_first = min(index for index, event in enumerate(events) if event.startswith(("show:happyranch-connector.service:", "show:happyranch-managed.target:", "presence:")))
    plain_index = events.index("journal:plain")
    assert plain_index < sidecar_last < jobs_index < receipt_index < later_first
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


def _observe_exhausted_presence(tmp_path: Path) -> tuple[object, str, int]:
    """Execute shipping presence/admission declarations with zero remaining budget."""
    harness = Path("app/linux/package/real_systemd_n3.sh").read_text()
    call = re.search(r'(\w+) /etc/happyranch/enrollment\.key -e; source="\$(\w+)"; source_loss="\$(\w+)"', harness)
    assert call is not None
    declarations = _shipping_declarations(harness)
    # Discover the deadline/cap variables from the real admission predicate;
    # private identifier spelling is not the expected-result oracle.
    predicate = re.search(r'SECONDS < (\w+) && (\w+) > 0', declarations)
    assert predicate is not None
    admission = re.findall(r'^(\w+)\(\) \{', declarations[:predicate.start()], re.MULTILINE)[-1]
    script = f'''set -euo pipefail
{declarations}
{predicate[1]}=$((SECONDS + 5)); {predicate[2]}=128
{admission}
{predicate[1]}=0; {predicate[2]}=128
attempts=0
timeout() {{ attempts=$((attempts + 1)); return 1; }}
sudo() {{ attempts=$((attempts + 1)); return 1; }}
{call[1]} "{tmp_path / 'synthetic-absent'}" -e
printf '%s|%s|%s\\n' "${{{call[2]}}}" "${{{call[3]}}}" "$attempts"
'''
    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=False, timeout=3)
    assert result.returncode == 0, result.stderr
    value, loss, attempts = result.stdout.strip().split("|")
    return json.loads(value), loss, int(attempts)


def test_real_systemd_failure_snapshot_uses_real_timeout_and_never_claims_unattempted_as_absent(tmp_path: Path) -> None:
    harness = Path("app/linux/package/real_systemd_n3.sh").read_text()
    snapshot_helpers = _shipping_declarations(harness)
    snapshot_call = re.search(r"(\w+) first-positive-start-failure \|\| true", harness)
    assert snapshot_call is not None
    fake_bin = tmp_path / "bin"; fake_bin.mkdir()
    (fake_bin / "systemctl").write_text("#!/bin/bash\nsleep 2\nprintf '%s\\n' failed\n")
    (fake_bin / "journalctl").write_text("#!/bin/bash\nsleep 2\n")
    for executable in fake_bin.iterdir():
        executable.chmod(0o700)
    script = f'''set -euo pipefail
diagnostics={tmp_path!s}; mkdir -p "$diagnostics"
sudo() {{ case "$1" in test) return 1 ;; systemctl|journalctl) "$@" ;; *) return 64 ;; esac; }}
failure_capture_driver={Path("app/linux/package/n3_failure_capture.py").resolve()!s}
{snapshot_helpers}
{snapshot_call[1]} real-timeout
cat "$diagnostics/real-timeout.json"
'''
    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=False, timeout=45, env=os.environ | {"PATH": f"{fake_bin}:{os.environ['PATH']}"})
    assert result.returncode == 0, result.stderr
    snapshot = json.loads(result.stdout)
    assert "timeout" in snapshot["observation_loss"].values()
    assert snapshot["credential_presence"]["source"] is False
    assert _observe_exhausted_presence(tmp_path) == ("unknown", "unattempted", 0)


def test_real_systemd_failure_snapshot_bounds_malformed_observations(tmp_path: Path) -> None:
    result = _run_real_systemd_failure_snapshot(tmp_path, malformed=True)
    assert result.returncode == 0, result.stderr
    assert "SECRET_CANARY" not in result.stdout + result.stderr
    snapshot = json.loads(result.stdout)
    assert {unit["active"] for unit in snapshot["units"].values()} == {"unknown"}
    assert {
        value
        for key, value in snapshot["observation_loss"].items()
        if key not in {"diagnostic_receipts", "sidecar_failure_lines"}
    } == {"parse_loss", "not_collected"}
    assert snapshot["observation_loss"]["sidecar_failure_lines"] == "empty"



def test_real_systemd_barriers_use_restrictive_service_state_directory_and_controller_sudo(tmp_path: Path) -> None:
    harness = Path("app/linux/package/real_systemd_n3.sh").read_text()
    region = harness[harness.index('# semantic evidence: concurrency_reentry'):harness.index('# semantic evidence: readiness_loss')]
    statements = []
    for line in region.splitlines():
        if (line.startswith('sudo install -d ') and '/etc/systemd' not in line) or re.match(r'^\w+ "(?:start|stop) barrier entered" ', line) or line.startswith(': | sudo tee ') or line.startswith('sudo rmdir ') or (line.startswith('sudo rm -f ') and '90-ci-barrier.conf' not in line):
            statements.append(line.split('; wait', 1)[0])
    result, argv = _run_shipping_fragment(tmp_path, "\n".join(statements))
    barrier = "/var/lib/happyranch-tsnet-sidecar/.n3-barrier-fixture"
    assert argv == [
        ["sudo", "install", "-d", "-m", "0700", "-o", "happyranch", "-g", "happyranch", barrier],
        ["sudo", "test", "-e", barrier + "/start-entered"],
        ["sudo", "tee", barrier + "/start-release"],
        ["sudo", "test", "-e", barrier + "/stop-entered"],
        ["sudo", "tee", barrier + "/stop-release"],
        ["sudo", "rm", "-f", *[barrier + "/" + name for name in ["start-entered", "start-release", "stop-entered", "stop-release"]]],
        ["sudo", "rmdir", barrier],
    ], argv
    assert result.returncode == 0, result.stderr


def test_real_systemd_cleanup_releases_both_held_barriers_before_teardown(tmp_path: Path) -> None:
    harness = Path("app/linux/package/real_systemd_n3.sh").read_text()
    unit_helpers = "systemctl_absent_value() {" + harness.split("systemctl_absent_value() {", 1)[1].split("\ndiagnostics=", 1)[0]
    cleanup = harness.split("cleanup() {", 1)[1].split("\n}\ntrap cleanup EXIT", 1)[0]
    fake_bin = tmp_path / "bin"; fake_bin.mkdir()
    (fake_bin / "systemctl").write_text("""#!/bin/bash
printf 'systemctl:%s\n' "$1" >>"$EVENT_LOG"
case "$1" in show) echo 0;; list-unit-files) [[ "$*" == 'list-unit-files --full --no-legend --no-pager' ]] || exit 1; echo 'unrelated.service enabled enabled';; stop|disable|reset-failed|daemon-reload) exit 0;; *) exit 96;; esac
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
    assert "sudo:tee:/visible/service-owned/barrier/start-release" in events, events
    assert "sudo:tee:/visible/service-owned/barrier/stop-release" in events, events
    start_release = next(index for index, event in enumerate(events) if event.endswith(":/visible/service-owned/barrier/start-release"))
    stop_release = next(index for index, event in enumerate(events) if event.endswith(":/visible/service-owned/barrier/stop-release"))
    teardown = events.index("systemctl:stop")
    assert start_release < teardown and stop_release < teardown


def _shipping_barrier_cleanup(harness: str) -> tuple[str, str]:
    """Select the shipping phase block independently of its removal command."""
    phase = harness.split("# semantic evidence: concurrency_reentry.", 1)[1].split(
        "# semantic evidence: readiness_loss.", 1,
    )[0]
    cleanup = phase.rsplit('wait "$stop_job"; wait "$start_job"\n', 1)[1]
    # The marker names are the external service/controller filesystem contract;
    # the shell variable spelling is private and may change independently.
    marker = re.search(r'\$([A-Za-z_]\w*)/start-entered', cleanup)
    assert marker is not None
    return cleanup, marker.group(1)


def test_real_systemd_barrier_bodies_remove_only_owned_markers_before_rmdir(tmp_path: Path) -> None:
    """Execute the source-defined barrier bodies and its controller cleanup."""
    harness = Path("app/linux/package/real_systemd_n3.sh").read_text()
    start_body = re.search(r"ExecStartPre=/bin/sh -c '([^']+)'", harness)
    stop_body = re.search(r"ExecStopPost=/bin/sh -c '([^']+)'", harness)
    cleanup_body, cleanup_variable = _shipping_barrier_cleanup(harness)
    assert start_body and stop_body
    barrier_dir = tmp_path / "state" / ".n3-barrier-test"
    barrier_dir.parent.mkdir(mode=0o700)
    durable = barrier_dir.parent / "credential.consumed"; durable.write_text("durable")
    durable.chmod(0o600)
    durable_before = (durable.read_bytes(), durable.stat().st_mode)
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
sudo() {{
  [[ $1 == systemctl ]] && return 0
  [[ $1 == rm && $3 == /etc/systemd/system/happyranch-tsnet-sidecar.service.d/90-ci-barrier.conf ]] && return 0
  "$@"
}}
fail() {{ return 1; }}
barrier_dir="{barrier_dir}"
{cleanup_variable}="{barrier_dir}"
{cleanup_body}
'''], capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert not barrier_dir.exists()
    assert durable.read_text() == "durable"
    assert (durable.read_bytes(), durable.stat().st_mode) == durable_before
    assert barrier_dir.parent.stat().st_mode & 0o777 == 0o700


def test_real_systemd_barrier_cleanup_refuses_unknown_child_residue(tmp_path: Path) -> None:
    harness = Path("app/linux/package/real_systemd_n3.sh").read_text()
    cleanup_body, cleanup_variable = _shipping_barrier_cleanup(harness)
    barrier_dir = tmp_path / ".n3-barrier-test"; barrier_dir.mkdir(mode=0o700)
    for marker in ("start-entered", "start-release", "stop-entered", "stop-release"):
        (barrier_dir / marker).touch()
    unexpected = barrier_dir / "unexpected"
    unexpected.write_text("must-refuse")
    unexpected.chmod(0o640)
    before = (unexpected.read_bytes(), unexpected.stat().st_mode, barrier_dir.stat().st_mode)
    result = subprocess.run(
        ["bash", "-c", f'''set -euo pipefail
sudo() {{
  [[ $1 == systemctl ]] && return 0
  [[ $1 == rm && $3 == /etc/systemd/system/happyranch-tsnet-sidecar.service.d/90-ci-barrier.conf ]] && return 0
  "$@"
}}
fail() {{ return 1; }}
barrier_dir="{barrier_dir}"
{cleanup_variable}="{barrier_dir}"
{cleanup_body}
'''], capture_output=True, text=True, check=False,
    )
    assert result.returncode != 0
    assert (barrier_dir / "unexpected").read_text() == "must-refuse"
    assert (unexpected.read_bytes(), unexpected.stat().st_mode, barrier_dir.stat().st_mode) == before


def _seed_n3_evidence(artifact: Path, *, run_id: str, include_cleanup: bool) -> Path:
    """Use the real shipping evidence driver to make a valid isolated ledger."""
    driver = Path("app/linux/package/n3_evidence.py").resolve()
    # Fixed accepted v4 lifecycle inputs; never follow a shrunken candidate.
    phases = {
        "startup": ("process_absent", "tsnet_admission_absent", "connector_staged_credential_service_readable_non_writable", "sidecar_staged_credential_service_readable_non_writable", "credential_source_retired", "credential_dropin_retired", "composite_ready_after_sidecar", "missing_consumed_state_failed_closed"),
        "admission": ("tsnet_admission_reachable",),
        "active_flow": ("production_process_active", "watchdog_composite_current", "watchdog_ceased_on_sidecar_loss"),
        "readiness_loss": ("tsnet_admission_removed_before_connector",),
        "revocation": ("stop_before_connector_cleanup", "tsnet_admission_absent"),
        "shutdown": ("same_instance_stop_twice", "no_double_close", "no_residue"),
        "partial_failure": ("fresh_pid", "fresh_composite_gates"),
        "concurrency_reentry": ("start_then_stop_barrier", "stop_then_start_barrier", "stop_wins"),
        "recovery": ("fresh_install_rollback_reentry_each_checkpoint", "upgrade_rollback", "retained_payload_units", "fresh_composite_gates", "no_transaction_residue", "credential_free_stopped_restart", "interrupted_retirement_reentry", "explicit_fresh_reenrollment"),
        "cleanup": ("virtual_admission_removed_while_peer_alive", "all_residue_absent", "task_work_removed"),
    }
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
    listener_residue: bool = False, observation: dict[str, str] | None = None,
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    """Run source cleanup against isolated strict dependencies and real evidence code."""
    harness = Path("app/linux/package/real_systemd_n3.sh").read_text()
    evidence = harness.split("evidence() {", 1)[1].split("\n}\ndiagnostic()", 1)[0]
    cleanup = harness.split("cleanup() {", 1)[1].split("\n}\ntrap cleanup EXIT", 1)[0]
    unit_helpers = "systemctl_absent_value() {" + harness.split("systemctl_absent_value() {", 1)[1].split("\ndiagnostics=", 1)[0]
    artifact = tmp_path / "execution-evidence.json"
    driver = _seed_n3_evidence(artifact, run_id=artifact_run, include_cleanup=include_cleanup)
    fake_bin = tmp_path / "bin"; fake_bin.mkdir()
    events = tmp_path / "events.log"
    (fake_bin / "python").write_text("#!/bin/bash\nprintf 'evidence:%s\\n' \"$2\" >>\"$EVENT_LOG\"\nexec \"$REAL_PYTHON\" \"$@\"\n")
    (fake_bin / "systemctl").write_text('''#!/bin/bash
case "$1" in
  show)
    [[ $4 == MainPID ]] || exit 91
    printf '%s:%s\\n' "$2" "$4" >>"$SHOW_LOG"
    [[ $2 != happyranch-managed.target ]] || exit 0
    if [[ -z ${FAULT_UNIT:-} || $2 == "$FAULT_UNIT" ]]; then
      [[ ${PID_MISSING:-0} != 1 ]] || exit "${PID_RC:-0}"
      printf '%s\\n' "${MAIN_PID-0}"; exit "${PID_RC:-0}"
    fi
    echo 0; exit 0;;
  list-unit-files)
    if [[ "$*" != 'list-unit-files --full --no-legend --no-pager' ]]; then exit "${NAMED_UNIT_LIST_RC:-1}"; fi
    printf '%s' "${UNIT_LIST_OUTPUT-unrelated.service enabled enabled}"; exit "${UNIT_LIST_RC:-0}";;
  stop|disable|reset-failed|daemon-reload) exit 0;;
  *) exit 91;;
esac
''')
    (fake_bin / "sudo").write_text('''#!/bin/bash
printf 'sudo:%s\\n' "$1" >>"$EVENT_LOG"
case "$1" in
  systemctl) shift; exec systemctl "$@";;
  find) printf '%s' "${FIND_OUTPUT-}"; exit "${FIND_RC:-0}";;
  test|rm|kill|update-ca-certificates) exit 0;;
  *) exit 92;;
esac
''')
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
{unit_helpers}
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
                                              "N3_UNIT_ROOT": str(tmp_path),
                                              "SHOW_LOG": str(tmp_path / "systemctl-show.log")} | (observation or {}))
    return result, events.read_text().splitlines() if events.exists() else []


def test_real_systemd_cleanup_finalizes_and_validates_actual_evidence_once(tmp_path: Path) -> None:
    result, events = _run_source_cleanup_acceptance(
        tmp_path, artifact_run="run", cleanup_run="run", include_cleanup=True,
    )
    assert result.returncode == 0, result.stderr
    assert events.count("evidence:finalize") == 1
    assert events.count("evidence:validate") == 1
    assert (tmp_path / "systemctl-show.log").read_text().splitlines() == [
        "happyranch-connector.service:MainPID", "happyranch-tsnet-sidecar.service:MainPID",
    ]


@pytest.mark.parametrize("observation", [
    None,
    {"UNIT_LIST_RC": "1", "UNIT_LIST_OUTPUT": ""},
    {"UNIT_LIST_RC": "1", "UNIT_LIST_OUTPUT": "unrelated.service enabled enabled"},
    {"UNIT_LIST_RC": "2"}, {"UNIT_LIST_RC": "2", "UNIT_LIST_OUTPUT": "loaded"},
    {"UNIT_LIST_OUTPUT": "loaded"},
    {"FIND_RC": "1"}, {"FIND_RC": "1", "FIND_OUTPUT": "/.happyranch-stage-leftover"},
    {"FIND_OUTPUT": "/.happyranch-stage-leftover"},
    {"MAIN_PID": "", "PID_RC": "0"}, {"MAIN_PID": "", "PID_RC": "4"},
    {"MAIN_PID": "unknown"}, {"MAIN_PID": "00"}, {"MAIN_PID": "42"},
    {"MAIN_PID": "0", "PID_RC": "2"},
])
def test_real_systemd_cleanup_residue_never_finalizes_actual_evidence(
    tmp_path: Path, observation: dict[str, str] | None,
) -> None:
    result, events = _run_source_cleanup_acceptance(
        tmp_path, artifact_run="run", cleanup_run="run", include_cleanup=True,
        listener_residue=observation is None, observation=observation,
    )
    assert result.returncode != 0
    assert "evidence:finalize" not in events and "evidence:validate" not in events
    ledger = json.loads((tmp_path / "execution-evidence.json").read_text())
    assert not any(event["observation"] == "all_residue_absent" for event in ledger["records"])
    assert "sudo:rm" in events
    assert not (tmp_path / "work").exists()


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


def test_real_systemd_failure_capture_precedes_teardown_and_preserves_exit_37(tmp_path: Path) -> None:
    result, events, work = _run_positive_start_cleanup_scenario(
        tmp_path, fault="outer-failure", outer_startup=True,
    )
    observations = [json.loads(event) for event in events]
    positives = [event for event in observations if event["event"] == "start" and event["credential_present"]]
    assert len(positives) == 1, observations
    positive = positives[0]
    assert positive["exit"] == 37, observations
    after_start = observations[observations.index(positive) + 1:]
    captures = [event for event in after_start if event["event"] == "capture-attempt"]
    assert captures == [
        {"event": "capture-attempt", "cleanup_entered": False, "exit": 9},
        {"event": "capture-attempt", "cleanup_entered": True, "exit": 9},
    ], observations
    teardown = next(event for event in after_start if event["event"] == "teardown-stop")
    assert all(after_start.index(event) < after_start.index(teardown) for event in captures), observations
    assert teardown["exit"] == 55, observations
    assert (tmp_path / "diagnostics/cleanup-events.log").read_text().splitlines() == ["cleanup"]
    assert not work.exists()
    assert result.returncode == 37, result.stderr


@pytest.mark.parametrize(
    ("capture_mode", "expected_lines", "expected_outcome"),
    [
        (
            "receipt",
            ['diagnostic_receipt={"actor":"tsnet-sidecar","assertion":{"status":"completed"},"category":"network_join","outcome":"failed","phase":"peer_establishment","terminal":true,"unit":"happyranch-tsnet-sidecar.service"}'],
            "observed",
        ),
        ("empty", [], "empty"),
        ("query_error", [], "query_error"),
        ("timeout", [], "timeout"),
        (
            "oversized_unrelated",
            ['diagnostic_receipt={"actor":"tsnet-sidecar","assertion":{"status":"completed"},"category":"network_join","outcome":"failed","phase":"peer_establishment","terminal":true,"unit":"happyranch-tsnet-sidecar.service"}'],
            "observed",
        ),
        ("known_exit_messages", ["readiness_unavailable", "watchdog_unavailable"], "observed"),
        ("malformed_receipt_canary", [], "empty"),
        ("matched_overflow", [], "truncated"),
        *[("reason:" + reason, ['diagnostic_receipt={"actor":"tsnet-sidecar","assertion":{"status":"completed"},"category":"network_join","outcome":"failed","phase":"peer_establishment","sub_reason":"' + reason + '","terminal":true,"unit":"happyranch-tsnet-sidecar.service"}'], "observed") for reason in (
            "unclassified", "context_cancelled", "deadline_exceeded", "up_backend_error", "up_no_ip",
            "up_error_unclassified", "up_status_unavailable", "up_not_running", "peer_status_error",
            "peer_status_unavailable", "peer_not_running", "peer_wait_deadline", "expected_peer_missing",
        )],
        ("reserialize", ['diagnostic_receipt={"actor":"tsnet-sidecar","assertion":{"status":"completed"},"category":"network_join","outcome":"failed","phase":"peer_establishment","terminal":true,"unit":"happyranch-tsnet-sidecar.service"}'], "observed"),
        ("unknown_fallback", ['diagnostic_receipt={"actor":"tsnet-sidecar","assertion":{"status":"completed"},"category":"unknown","outcome":"failed","phase":"unknown","terminal":true,"unit":"happyranch-tsnet-sidecar.service"}'], "observed"),
        ("deep_then_valid", ['diagnostic_receipt={"actor":"tsnet-sidecar","assertion":{"status":"completed"},"category":"network_join","outcome":"failed","phase":"peer_establishment","terminal":true,"unit":"happyranch-tsnet-sidecar.service"}'], "observed"),
        *[("invalid:" + kind, [], "empty") for kind in ("extra", "nested_extra", "duplicate", "nested_duplicate", "null", "list", "number", "bool", "unknown", "control", "nonfinite", "other_category")],


    ],
)
def test_real_systemd_filter_first_sidecar_capture_is_bounded_secret_free_and_preserves_exit(
    tmp_path: Path,
    capture_mode: str,
    expected_lines: list[str],
    expected_outcome: str,
) -> None:
    """Drive the actual shipping capture/start/trap path through PATH doubles."""
    harness = Path("app/linux/package/real_systemd_n3.sh").read_text()
    snapshot = "safe_systemctl_value() {" + harness.split("safe_systemctl_value() {", 1)[1].split("\ncleanup() {", 1)[0]
    unit_helpers = "systemctl_absent_value() {" + harness.split("systemctl_absent_value() {", 1)[1].split("\ndiagnostics=", 1)[0]
    cleanup = harness.split("cleanup() {", 1)[1].split("\n}\ntrap cleanup EXIT", 1)[0]
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    (fake_bin / "date").write_text("#!/bin/bash\nprintf '1700000000123456000\\n'\n")
    (fake_bin / "systemctl").write_text("""#!/bin/bash
printf 'systemctl:%s\\n' "$1" >>"$EVENT_LOG"
case "$1" in
  start) exit 37 ;;
  show) case "$4" in InvocationID) echo 12345678-1234-1234-1234-123456789abc;; ActiveState) echo failed;; SubState) echo failed;; Result) echo exit-code;; ExecMainStatus) echo 37;; MainPID) echo 0;; *) echo unknown;; esac ;;
  list-unit-files) [[ "$*" == 'list-unit-files --full --no-legend --no-pager' ]] || exit 1; echo 'unrelated.service enabled enabled' ;;
  stop|disable|reset-failed|daemon-reload|list-jobs) exit 0 ;;
  *) exit 97 ;;
esac
""")
    (fake_bin / "sudo").write_text("""#!/bin/bash
if [[ $1 == systemctl ]]; then shift; exec systemctl "$@"; fi
if [[ $1 == test ]]; then exit 1; fi
exit 0
""")
    (fake_bin / "timeout").write_text("""#!/bin/bash
printf 'timeout:%s\\n' "$*" >>"$EVENT_LOG"
while [[ $1 == --* ]]; do shift; done
shift
if [[ ${CAPTURE_MODE:?} == timeout && $1 == journalctl && " $* " == *" -o cat "* ]]; then exit 124; fi
exec "$@"
""")
    (fake_bin / "journalctl").write_text("""#!/bin/bash
printf 'journal:%s\\n' "$*" >>"$EVENT_LOG"
if [[ " $* " == *" JOB_TYPE=start "* ]]; then exit 0; fi
if [[ " $* " != *" -o cat "* ]]; then exit 0; fi
[[ " $* " == *" -u happyranch-tsnet-sidecar.service "* ]] || exit 91
[[ " $* " == *" -b $BOOT "* ]] || exit 92
[[ " $* " == *" --since @1700000000.123456 "* ]] || exit 93
[[ " $* " == *" --until @1700000000.123456 "* ]] || exit 94
case "${CAPTURE_MODE:?}" in
  reason:*|reserialize|deep_then_valid|unknown_fallback|invalid:*) printf '%s\\n' "$PLAIN_NEW" ;;
  receipt) printf '%s\\n' 'diagnostic_receipt={"actor":"tsnet-sidecar","assertion":{"status":"completed"},"category":"network_join","outcome":"failed","phase":"peer_establishment","terminal":true,"unit":"happyranch-tsnet-sidecar.service"}' ;;
  empty) printf '%s\\n' 'TOKEN_CANARY unrelated' ;;
  query_error) exit 17 ;;
  oversized_unrelated)
    printf 'TOKEN_CANARY'; printf '%10000s\\n' unrelated
    printf '%s\\n' 'diagnostic_receipt={"actor":"tsnet-sidecar","assertion":{"status":"completed"},"category":"network_join","outcome":"failed","phase":"peer_establishment","terminal":true,"unit":"happyranch-tsnet-sidecar.service"}' ;;
  known_exit_messages) printf '%s\\n' readiness_unavailable watchdog_unavailable ;;
  malformed_receipt_canary) printf '%s\\n' 'diagnostic_receipt={TOKEN_CANARY=never-retain}' ;;
  matched_overflow) i=0; while (( i < 80 )); do echo readiness_unavailable; i=$((i + 1)); done ;;
  timeout) exit 95 ;;
esac
""")
    (fake_bin / "pgrep").write_text("#!/bin/bash\nexit 1\n")
    for executable in fake_bin.iterdir():
        executable.chmod(0o700)
    work = tmp_path / "work"
    (work / "hs").mkdir(parents=True)
    (work / "headscale").write_text("#!/bin/bash\nprintf '[]'\n")
    (work / "headscale").chmod(0o700)
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
    new_receipt = {"category": "network_join", "phase": "peer_establishment", "actor": "tsnet-sidecar", "unit": "happyranch-tsnet-sidecar.service", "outcome": "failed", "terminal": True, "assertion": {"status": "completed"}}
    if capture_mode.startswith("reason:"):
        new_receipt["sub_reason"] = capture_mode.split(":", 1)[1]
    plain_new = "diagnostic_receipt=" + json.dumps(new_receipt).replace("tsnet-sidecar", "tsnet\\u002dsidecar", 1)
    if capture_mode == "unknown_fallback":
        plain_new = "diagnostic_receipt=" + json.dumps(new_receipt | {"category": "unknown", "phase": "unknown"})
    if capture_mode == "deep_then_valid":
        plain_new = "diagnostic_receipt=" + json.dumps(new_receipt)[:-1] + ',"raw":' + '[' * 1100 + '"TOKEN_CANARY"' + ']' * 1100 + '}\n' + plain_new
    if capture_mode.startswith("invalid:"):
        kind = capture_mode.split(":", 1)[1]
        if kind == "extra": new_receipt["raw"] = "TOKEN_CANARY"
        elif kind == "nested_extra": new_receipt["assertion"]["raw"] = "TOKEN_CANARY"
        elif kind == "other_category": new_receipt.update(category="engine_start", phase="engine_initialization", sub_reason="unclassified")
        elif kind not in {"duplicate", "nested_duplicate"}: new_receipt["sub_reason"] = {"null": None, "list": [], "number": 1, "bool": True, "unknown": "TOKEN_CANARY", "control": "unclassified\x00", "nonfinite": float("nan")}[kind]
        plain_new = "diagnostic_receipt=" + json.dumps(new_receipt)
        if kind == "duplicate": plain_new = plain_new[:-1] + ',"category":"network_join"}'
        if kind == "nested_duplicate": plain_new = plain_new.replace('"status": "completed"', '"status":"completed","status":"completed"')

    event_log = tmp_path / "events.log"
    boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip().replace("-", "")
    result = subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        check=False,
        env=os.environ
        | {
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "EVENT_LOG": str(event_log),
            "CAPTURE_MODE": capture_mode,
            "PLAIN_NEW": plain_new,
            "BOOT": boot,
            "N3_RESIDUE_ROOT": str(tmp_path),
            "N3_UNIT_ROOT": str(tmp_path),
        },
    )
    assert result.returncode == 37, result.stderr
    document_text = (tmp_path / "first-positive-start-failure.json").read_text()
    document = json.loads(document_text)
    assert document["sidecar_failure_lines"] == expected_lines
    assert document["observation_loss"]["sidecar_failure_lines"] == expected_outcome
    assert "TOKEN_CANARY" not in document_text + result.stdout + result.stderr
    events = event_log.read_text().splitlines()
    plain_capture = next(index for index, event in enumerate(events) if " -o cat " in f" {event} " and event.startswith(("journal:", "timeout:")))
    assert plain_capture < events.index("systemctl:stop")
    assert (tmp_path / "cleanup-events.log").read_text().splitlines() == ["cleanup"]


def test_real_systemd_positive_start_exit_trap_preserves_exit_37_despite_capture_and_cleanup_failures(tmp_path: Path) -> None:
    harness = Path("app/linux/package/real_systemd_n3.sh").read_text()
    snapshot = "safe_systemctl_value() {" + harness.split("safe_systemctl_value() {", 1)[1].split("\ncleanup() {", 1)[0]
    unit_helpers = "systemctl_absent_value() {" + harness.split("systemctl_absent_value() {", 1)[1].split("\ndiagnostics=", 1)[0]
    cleanup = harness.split("cleanup() {", 1)[1].split("\n}\ntrap cleanup EXIT", 1)[0]
    fake_bin = tmp_path / "bin"; fake_bin.mkdir()
    (fake_bin / "systemctl").write_text("""#!/bin/bash
if [[ $1 == start ]]; then exit 37; fi
if [[ $1 == show ]]; then case $4 in ActiveState) echo failed;; SubState) echo failed;; Result) echo exit-code;; ExecMainStatus) echo 37;; *) echo unknown;; esac; fi
if [[ $1 == list-unit-files ]]; then [[ "$*" == 'list-unit-files --full --no-legend --no-pager' ]] || exit 1; echo 'unrelated.service enabled enabled'; fi
exit 0
""")
    (fake_bin / "sudo").write_text("#!/bin/bash\nif [[ $1 == systemctl ]]; then shift; exec systemctl \"$@\"; fi\nexit 0\n")
    (fake_bin / "pgrep").write_text("#!/bin/bash\nexit 1\n")
    for executable in fake_bin.iterdir(): executable.chmod(0o700)
    work = tmp_path / "work"; (work / "hs").mkdir(parents=True)
    (work / "headscale").write_text("#!/bin/bash\necho '[]'\n"); (work / "headscale").chmod(0o700)
    script = f'''set -euo pipefail
diagnostics={tmp_path!s}; work={work!s}; evidence_driver=/missing; evidence_artifact=/missing
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
  list-unit-files) [[ "$*" == 'list-unit-files --full --no-legend --no-pager' ]] || exit 1; echo 'unrelated.service enabled enabled' ;;
  list-jobs|stop|disable|reset-failed|daemon-reload) exit 0 ;;
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
    (fake_bin / "journalctl").write_text("#!/bin/bash\n[[ \" $* \" == *\" JOB_TYPE=start \"* ]] && exit 0\nprintf 'journal:%s\\n' \"$*\" >>\"$EVENT_LOG\"\nexit 0\n")
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
    assert not event_log.exists(), "journal must not be queried without an attributable invocation"


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
  list-unit-files) [[ "$*" == 'list-unit-files --full --no-legend --no-pager' ]] || exit 1; echo 'unrelated.service enabled enabled' ;;
  stop|disable|reset-failed|daemon-reload|list-jobs) exit 0 ;;
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
    (fake_bin / "systemctl").write_text("#!/bin/bash\nif [[ $1 == show ]]; then echo 0; fi\nif [[ $1 == list-unit-files ]]; then [[ \"$*\" == 'list-unit-files --full --no-legend --no-pager' ]] || exit 1; echo 'unrelated.service enabled enabled'; fi\nexit 0\n")
    (fake_bin / "sudo").write_text("#!/bin/bash\nif [[ $1 == systemctl ]]; then shift; exec systemctl \"$@\"; fi\nexit 0\n")
    (fake_bin / "pgrep").write_text("#!/bin/bash\nexit 1\n")
    for executable in fake_bin.iterdir(): executable.chmod(0o700)
    work = tmp_path / "work"; (work / "hs").mkdir(parents=True)
    (work / "headscale").write_text("#!/bin/bash\necho '[]'\n"); (work / "headscale").chmod(0o700)
    script = f'''set -euo pipefail
diagnostics={tmp_path!s}; work={work!s}; evidence_driver=/missing; evidence_artifact=/missing
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
    headscale_capture: bool = False, capture_reentry: bool = False,
    late_headscale: bool = False, outer_startup: bool = False, early_failure: bool = False,
) -> tuple[subprocess.CompletedProcess[str], list[str], Path]:
    """Run the actual positive-start EXIT/trap seam with a failing teardown command."""
    harness = Path("app/linux/package/real_systemd_n3.sh").read_text()
    if outer_startup:
        # Select declarations by shell grammar, and the outer startup region by
        # its acceptance labels. Private function identifiers are not oracles.
        declarations = []
        for declaration in re.finditer(r"(?m)^\w+\(\) \{", harness):
            line_end = harness.index("\n", declaration.start())
            if harness[declaration.start():line_end].endswith("}"):
                end = line_end
            else:
                closing = re.search(r"(?m)^\}$", harness[line_end:])
                assert closing is not None
                end = line_end + closing.end()
            declarations.append(harness[declaration.start():end])
        entry = re.search(r'(?m)^\w+ \|\| fail "fresh shipping-unit reset/setup failed"$', harness)
        assert entry is not None
        outer = harness[entry.start():harness.index('wait_for "connector READY"', entry.end())]
        traps = "\n".join(re.findall(r"(?m)^trap .+ (?:EXIT|INT|TERM)$", harness))
        fake_bin = tmp_path / "bin"
        fake_bin.mkdir()
        work = tmp_path / "work"
        (work / "hs").mkdir(parents=True)
        diagnostics = tmp_path / "diagnostics"
        diagnostics.mkdir()
        for name in ("daemon.token", "connector.json", "policy.json", "sidecar.json"):
            (work / name).write_text("fixture-only")
        event_log = tmp_path / "events.log"
        # These commands model external systemd/installer results, never the
        # shipping control flow. Every accepted argv is bounded and local;
        # unexpected commands fail without forwarding to any host manager.
        fixture = fake_bin / "fixture"
        fixture.write_text(f"#!{sys.executable}\n" + r'''
import json, os, pathlib, shutil, subprocess, sys, tempfile
root = pathlib.Path(os.environ["FIXTURE_ROOT"])
work = root / "work"
tool = pathlib.Path(sys.argv[0]).name
argv = sys.argv[1:]
units = ["happyranch-managed.target", "happyranch-tsnet-sidecar.service", "happyranch-connector.service"]
state_file = root / "external-state.json"
state = json.loads(state_file.read_text()) if state_file.exists() else {
    "installed": False, "active": False, "main_pid": 0, "restart_pending": False,
    "staging_checks": 0, "positive": False,
}
def record(event, **fields):
    with (root / "events.log").open("a") as stream:
        stream.write(json.dumps({"event": event, **fields}) + "\n")
def finish(status=0):
    state_file.write_text(json.dumps(state))
    raise SystemExit(status)
def local(path):
    path = pathlib.Path(path)
    if path.is_relative_to(root): return path
    assert path.is_absolute() and ".." not in path.parts, path
    assert path.parts[1] in {"etc", "opt", "var", "run", "usr", ".happyranch-install-transaction.json", ".happyranch-backup", ".happyranch-units-backup"}, path
    return root / str(path).lstrip("/")
if tool == "sudo":
    tool, *argv = argv
    if tool.startswith("/"):
        assert tool == str(work / "tailscale"), tool
        tool = "tailscale"
if tool == "systemctl":
    verb, *args = argv
    if verb == "start":
        assert args == [units[0]], argv
        credential = local("/etc/happyranch/enrollment.key").exists()
        if not credential:
            reset_log = root / "diagnostics/shipping-unit.log"
            record("shipping-reset-boundary", installed=state["installed"],
                   reset_log=reset_log.read_text() if reset_log.exists() else None)
        directory = local("/var/lib/happyranch-tsnet-sidecar")
        created = not directory.exists()
        directory.mkdir(parents=True, exist_ok=True)
        staging = local("/run/credentials/happyranch-tsnet-sidecar.service")
        staging.mkdir(parents=True, exist_ok=True)
        state.update(active=True, main_pid=42, restart_pending=not credential, positive=credential)
        status = (37 if os.environ["OUTER_FAULT"] == "outer-failure" else 0) if credential else 1
        record("start", credential_present=credential, state_directory_created=created, exit=status)
        finish(status)
    if verb in {"stop", "disable", "reset-failed"}:
        assert args and len(set(args)) == len(args) and set(args) <= set(units), argv
        status = 55 if state["positive"] and os.environ["OUTER_FAULT"] == "outer-failure" else 0
        if verb == "stop":
            assert args == [units[0]] or args == units, argv
            record("teardown-stop" if state["positive"] else "stop", exit=status)
            state.update(active=False, main_pid=0)
        elif verb == "disable":
            assert args == [units[0]], argv
        else:
            if not state["installed"]:
                record("reset-cleanup", argv=argv)
            if state["installed"] and not state["positive"]:
                record("negative-reset", argv=argv)
            state["restart_pending"] = False
        finish(status)
    if verb == "daemon-reload":
        assert not args, argv
        if state["installed"] and not state["positive"]:
            record("reset-reload")
        finish()
    if verb == "list-unit-files":
        if args == ["--full", "--no-legend", "--no-pager"]:
            print("unrelated.service enabled enabled")
            if state["installed"]:
                for unit in units: print(unit + " enabled enabled")
            finish()
        assert len(args) == 4 and set(args[:-1]) == set(units) and args[-1] == "--no-legend", argv
        finish(1)
    if verb == "show":
        assert len(args) == 4 and args[0] in units and args[1] == "-p" and args[3] == "--value", argv
        values = {"LoadState": "loaded" if state["installed"] else "not-found",
                  "ActiveState": "active" if state["active"] else "inactive",
                  "SubState": "running" if state["active"] else "dead", "MainPID": str(state["main_pid"])}
        assert args[2] in values, argv
        if args[0] != units[0] or args[2] != "MainPID":
            print(values[args[2]])
        finish()
    if verb == "is-active":
        assert args == ["--quiet", units[1]], argv
        finish(0 if state["active"] else 3)
    raise AssertionError(argv)
if tool == "mv":
    assert argv in [["/etc/happyranch/enrollment.key", "/etc/happyranch/enrollment.key.held"],
                    ["/etc/happyranch/enrollment.key.held", "/etc/happyranch/enrollment.key"]], argv
    source, destination = map(local, argv)
    if argv[0].endswith(".held"):
        record("restore", active=state["active"], main_pid=state["main_pid"],
               restart_pending=state["restart_pending"],
               staging_present=local("/run/credentials/happyranch-tsnet-sidecar.service").exists(),
               staging_checks=state["staging_checks"], credential=source.read_text().strip())
    source.rename(destination)
    finish()
if tool == "test":
    invert = argv[0] == "!"
    args = argv[1:] if invert else argv
    assert len(args) == 2 and args[0] in {"-e", "-d"}, argv
    path = local(args[1])
    if invert and args[1] == "/run/credentials/happyranch-tsnet-sidecar.service":
        state["staging_checks"] += 1
        record("staging-check", path=args[1])
        if state["staging_checks"] == 2 and not state["active"] and not state["restart_pending"]:
            shutil.rmtree(path)
    present = path.is_dir() if args[0] == "-d" else path.exists()
    finish(0 if present != invert else 1)
if tool == "install":
    assert argv[:6] == ["-d", "-m", "0700", "-o", "happyranch", "-g"] or argv[:2] == ["-m", "0600"], argv
    if argv[0] == "-d":
        assert argv == ["-d", "-m", "0700", "-o", "happyranch", "-g", "happyranch", "/etc/happyranch"], argv
        local(argv[-1]).mkdir(parents=True, exist_ok=True)
    else:
        assert len(argv) == 8 and argv[2] == "-o" and argv[4] == "-g", argv
        name = pathlib.Path(argv[-1]).name
        assert name in {"daemon.token", "connector.json", "policy.json", "sidecar.json", "enrollment.key"}, argv
        owner = "root" if name in {"daemon.token", "enrollment.key"} else "happyranch"
        assert argv[3] == argv[5] == owner and argv[-2] == str(work / name) and argv[-1] == "/etc/happyranch/" + name, argv
        shutil.copyfile(argv[-2], local(argv[-1]))
    finish()
if tool == "env":
    assert argv == ["PATH=" + os.environ["PATH"], "uv", "run", "python", "-", "fixture-package"], argv
    assert sys.stdin.read() == "import sys\nfrom pathlib import Path\nfrom runtime.remote_access.linux_package import install_linux_package\ninstall_linux_package(Path(sys.argv[1]), Path('/'), system_service=True)\n"
    state["installed"] = True
    record("reset-install")
    finish()
if tool == "headscale":
    if argv == ["preauthkeys", "create", "--user", "ci", "--reusable=false", "--expiration", "10m", "--config", str(work / "hs/config.yaml")]:
        print("fixture-enrollment")
    else:
        assert argv == ["nodes", "list", "--output", "json", "--config", str(work / "hs/config.yaml")], argv
        print("[]")
    finish()
if tool == "tailscale":
    assert argv == ["--socket=" + str(work / "peer.sock"), "status", "--json"], argv
    print('{"Peer":null}')
    finish()
if tool == "timeout":
    if argv[:2] == ["15", "systemd-run"]:
        record("denial-argv", argv=argv)
        expected = ["15", "systemd-run", "--quiet", "--wait", "--collect", "--pipe",
            "--unit=happyranch-n3-denial-shipping-unit", "--property=User=happyranch", "--property=Group=happyranch",
            "--property=NoNewPrivileges=yes", "--property=PrivateDevices=yes", "--property=ProtectSystem=strict",
            "--property=ProtectHome=yes", "--property=ReadWritePaths=/var/lib/happyranch-tsnet-sidecar",
            "--property=RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX AF_NETLINK", "--property=CapabilityBoundingSet=",
            "/usr/bin/python3", "-", "shipping-unit"]
        if argv != expected:
            finish(64)  # Record the attempted boundary, then refuse without forwarding.
        record("denial-probe", state_directory_present=local("/var/lib/happyranch-tsnet-sidecar").is_dir())
        sys.stdin.read()  # Never execute privileged denial-probe input locally.
        print("{}")
        finish()
    assert argv in [["1", "bash", "-c", "</dev/tcp/127.0.0.1/" + port] for port in ("18443", "18765", "18080", "19090", "15043", "13478")], argv
    finish(1)  # No host or network probe is forwarded.
if tool == "mktemp":
    if argv == [str(work / ".n3-unit-inventory.XXXXXX")]:
        fd, path = tempfile.mkstemp(prefix=".n3-unit-inventory.", dir=work)
        os.close(fd)
        state["inventory_path"] = path
        print(path)
        finish()
    assert argv == [str(root / "diagnostics/.n3-losses.XXXXXX")], argv
    record("capture-attempt", cleanup_entered=(root / "diagnostics/cleanup-events.log").exists(), exit=9)
    finish(9)  # Real capture entry observes a failed external temp-file launch.
if tool == "sleep":
    assert argv in [["1"], ["2"]], argv
    finish()
if tool == "pgrep":
    assert argv == ["-f", "(^|/)(happyranch-connector|happyranch-tsnet-sidecar)( |$)"], argv
    finish(1)
if tool == "find":
    assert argv == ["/", "-maxdepth", "1", "(", "-name", ".happyranch-stage-*", "-o", "-name", ".happyranch-tmp-*", ")", "-print", "-quit"] or argv == [str(root), *["-maxdepth", "1", "(", "-name", ".happyranch-stage-*", "-o", "-name", ".happyranch-tmp-*", ")", "-print", "-quit"]], argv
    finish()
if tool == "update-ca-certificates":
    assert not argv, argv
    finish()
if tool == "rm":
    if argv == ["-f", "--", state.get("inventory_path")]:
        path = pathlib.Path(argv[-1])
        assert path.parent == work and path.name.startswith(".n3-unit-inventory."), argv
        path.unlink()
        del state["inventory_path"]
        finish()
    assert argv[0] in {"-f", "-rf"}, argv
    if set("/etc/systemd/system/" + unit for unit in units) <= set(argv[1:]):
        state["installed"] = False
    allowed = {"/etc/systemd/system/happyranch-tsnet-sidecar.service.d", "/usr/local/share/ca-certificates/happyranch-n3-ci.crt",
        *["/etc/systemd/system/" + unit for unit in units],
        "/opt/happyranch", "/etc/happyranch", *["/" + base + "/" + name for base in ("var/lib", "run", "var/log") for name in ("happyranch-connector", "happyranch-tsnet-sidecar")], str(work)}
    assert argv[1:] and set(argv[1:]) <= allowed, argv
    for value in argv[1:]:
        path = local(value)
        if path.is_dir():
            assert argv[0] == "-rf", argv
            shutil.rmtree(path)
        elif path.exists(): path.unlink()
    finish()
raise AssertionError((tool, argv))
''')
        fixture.chmod(0o700)
        for command in ("sudo", "systemctl", "sleep", "timeout", "mktemp", "pgrep", "rm"):
            (fake_bin / command).symlink_to(fixture)
        (work / "headscale").symlink_to(fixture)
        (work / "tailscale").symlink_to(fixture)
        driver = tmp_path / "evidence.py"
        driver.write_text('''import json, sys
from pathlib import Path
args = sys.argv[1:]
directory = Path(__file__).parent / "diagnostics"
artifact = str(directory / "execution-evidence.json")
if args[0] == "observe":
    phase, observation = args[3], args[5]
    assert (phase, observation) in {("startup", "process_absent"), ("startup", "tsnet_admission_absent"),
        ("cleanup", "all_residue_absent"), ("cleanup", "task_work_removed")}, args
    assert args == ["observe", artifact, "--phase", phase, "--observation", observation,
        "--assertion-id", "fixture:" + phase + ":" + observation], args
elif args[0] == "diagnose":
    with (Path(__file__).parent / "events.log").open("a") as stream:
        stream.write(json.dumps({"event": "diagnostic-argv", "argv": args}) + "\\n")
    if args != ["diagnose", artifact, "--id", "fixture:negative-leg-expected:credential_input",
        "--category", "credential_input", "--phase", "input_acquisition", "--actor", "systemd",
        "--unit", "happyranch-tsnet-sidecar.service"]: raise SystemExit(64)
elif args[0] == "validate-denial-matrix":
    assert args == ["validate-denial-matrix", str(directory / "shipping-unit-denial-matrix.json"),
        "--expected-arm", "shipping-unit"], args
elif args[0] == "finalize":
    assert args == ["finalize", artifact], args
else:
    assert args == ["validate", artifact, "--expected-subject", "a" * 40, "--expected-run", "fixture"], args
''')
        script = f'''set -euo pipefail
work={str(work)!r}; diagnostics={str(diagnostics)!r}; ts_dir="$work"
run_id=fixture; PACKAGE_TAR=fixture-package; evidence_driver={str(driver)!r}; PROOF_SUBJECT_SHA={'a' * 40}
evidence_artifact="$diagnostics/execution-evidence.json"; capture_raw_files=()
headscale_pid=""; peer_pid=""; daemon_pid=""; sidecar_ip=""; barrier_dir=""
{chr(10).join(declarations)}
{traps}
{outer}
'''
        result = subprocess.run(
            ["bash", "-c", script], capture_output=True, text=True, check=False, timeout=20,
            env=os.environ | {"PATH": f"{fake_bin}:{Path(sys.executable).parent}:/usr/bin:/bin",
                              "FIXTURE_ROOT": str(tmp_path), "OUTER_FAULT": fault,
                              "N3_RESIDUE_ROOT": str(tmp_path), "N3_UNIT_ROOT": str(tmp_path)},
        )
        assert "Traceback" not in result.stderr and "unbound variable" not in result.stderr, result.stderr
        events = event_log.read_text().splitlines() if event_log.exists() else []
        return result, events, work
    snapshot = "safe_systemctl_value() {" + harness.split("safe_systemctl_value() {", 1)[1].split("\ncleanup() {", 1)[0]
    unit_helpers = "systemctl_absent_value() {" + harness.split("systemctl_absent_value() {", 1)[1].split("\ndiagnostics=", 1)[0]
    cleanup = harness.split("cleanup() {", 1)[1].split("\n}\ntrap cleanup EXIT", 1)[0]
    fake_bin = tmp_path / "bin"; fake_bin.mkdir()
    event_log = tmp_path / "events.log"
    (fake_bin / "systemctl").write_text(f'''#!/bin/bash
printf 'systemctl:%s\\n' "$1" >>"$EVENT_LOG"
case "$1" in
  start) exit 37 ;;
  show) case "$4" in InvocationID) echo unknown;; ActiveState) echo failed;; SubState) echo failed;; Result) echo exit-code;; ExecMainStatus) echo 37;; MainPID) [[ "{fault}" != observation-pid ]] || exit 2; echo 0;; *) echo unknown;; esac ;;
  stop|disable|reset-failed) [[ "$1" != "{fault}" ]] || exit 55; exit 0 ;;
  list-unit-files) [[ "$*" == 'list-unit-files --full --no-legend --no-pager' ]] || exit 1; [[ "{fault}" != observation-list ]] || exit 2; echo 'unrelated.service enabled enabled' ;;
  daemon-reload|list-jobs) exit 0 ;;
  *) printf 'unknown-systemctl:%s\\n' "$1" >>"$EVENT_LOG"; exit 97 ;;
esac
''')
    (fake_bin / "sudo").write_text(f'''#!/bin/bash
printf 'sudo:%s\\n' "$1" >>"$EVENT_LOG"
case "$1" in
  systemctl) shift; exec systemctl "$@";;
  test) [[ "${{2:-}}" == '!' ]] && exit 0; exit 1;;
  kill) if [[ ${{REAL_FIXTURE_KILL:-0}} == 1 ]]; then shift; exec /bin/kill "$@"; fi; exit 0;;
  find) [[ "{fault}" != observation-find ]] || exit 1; exit 0;;
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
    fixture_setup = ""
    if headscale_capture:
        (work / "hs/config.yaml").write_text("CONFIG_CANARY\n")
        (work / "headscale.log").write_text('{"level":"info","message":"history","credential":"CREDENTIAL_CANARY"}\n')
        (work / "headscale").write_text('''#!/usr/bin/env python3
import os,pathlib,signal,time
root=pathlib.Path(__file__).parent
os.kill(int(os.environ['HEADSCALE_PID']),0)
events=pathlib.Path(os.environ['EVENT_LOG'])
with events.open('a') as stream: stream.write('headscale:queried\\n')
if os.environ.get('CAPTURE_REENTRY')=='1':
 count=events.read_text().splitlines().count('headscale:queried')
 if count==int(os.environ['REENTRY_QUERY']): os.kill(int(os.environ['SHIPPING_PID']),signal.SIGTERM)
print('[{"name":"synthetic-peer-ci","online":true},{"name":"home-sidecar-ci"}]')
''')
        (work / "headscale").chmod(0o700)
        for private in (work / "hs/config.yaml", work / "headscale.log"):
            private.chmod(0o600)
        fixture_setup = 'sleep 60 & headscale_pid=$!\nexport HEADSCALE_PID="$headscale_pid" SHIPPING_PID="$$"\nprintf "%s\\n" "$headscale_pid" >"$FIXTURE_PID_FILE"\n'
    capture_driver = Path("app/linux/package/n3_failure_capture.py").resolve() if headscale_capture else Path("/missing")
    if late_headscale:
        # Execute the real shipped CLI; delay only delivery of a real successful
        # query wait. Its worker/parser source and group cleanup remain intact.
        wrapper = tmp_path / "late-capture-driver.py"
        wrapper.write_text(f'''import importlib.util,subprocess,time
spec=importlib.util.spec_from_file_location("shipping_capture",{str(capture_driver)!r})
capture=importlib.util.module_from_spec(spec)
spec.loader.exec_module(capture)
actual_popen=subprocess.Popen
def delayed_popen(argv,**kwargs):
 child=actual_popen(argv,**kwargs)
 if argv[0]=={str(work / "headscale")!r}:
  actual_wait=child.wait
  expires=time.monotonic()+3
  first=True
  def wait(*args,**kwargs):
   nonlocal first
   status=actual_wait(*args,**kwargs)
   if first:
    first=False
    time.sleep(max(0,expires+0.05-time.monotonic()))
   return status
  child.wait=wait
 return child
capture.subprocess.Popen=delayed_popen
raise SystemExit(capture.main())
''')
        capture_driver = wrapper
    if early_failure:
        (fake_bin / "timeout").write_text(f"#!{sys.executable}\n" + "import json,os,sys\nfrom pathlib import Path\nwith (Path(os.environ['EVENT_LOG']).parent/'timeout-argv.jsonl').open('a') as stream: stream.write(json.dumps(sys.argv[1:])+'\\n')\nos.execv('/usr/bin/timeout',['timeout',*sys.argv[1:]])\n")
        (fake_bin / "timeout").chmod(0o700)
    trigger = "exit 37" if early_failure else f"kill -{signal} $$" if signal else 'start_managed_target || exit "$?"'
    script = f'''set -euo pipefail
diagnostics={tmp_path / 'diagnostics' if headscale_capture else tmp_path!s}; mkdir -p "$diagnostics"
work={work!s}; evidence_driver={fake_bin / "evidence"!s}; evidence_artifact=/missing
failure_capture_driver={capture_driver}
peer_pid=""; daemon_pid=""; headscale_pid=""; sidecar_ip=""; run_id=test
port_open() {{ return 1; }}
tsnet_open() {{ return 1; }}
evidence() {{ printf 'evidence:%s:%s\\n' "$1" "$2" >>"$EVENT_LOG"; }}
{snapshot}
{unit_helpers}
cleanup() {{
{cleanup}
}}
trap cleanup EXIT
trap 'cleanup 130' INT
trap 'cleanup 143' TERM
{fixture_setup}
{trigger}
'''
    result = subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, check=False,
        env=os.environ | {"PATH": f"{fake_bin}:{os.environ['PATH']}", "EVENT_LOG": str(event_log),
                          "N3_RESIDUE_ROOT": str(tmp_path), "N3_UNIT_ROOT": str(tmp_path),
                          "REAL_FIXTURE_KILL": "1" if headscale_capture else "0", "FIXTURE_PID_FILE": str(tmp_path / "private-fixture-pid"),
                          "CAPTURE_REENTRY": "1" if capture_reentry else "0", "REENTRY_QUERY": "1" if signal else "2"},
        timeout=40,
    )
    events = event_log.read_text().splitlines() if event_log.exists() else []
    return result, events, work


@pytest.mark.parametrize("signal,expected", [(None, 37), ("INT", 130), ("TERM", 143)])
@pytest.mark.parametrize("reentry", [False, True])
@pytest.mark.parametrize("late", [False, True])
def test_real_systemd_headscale_capture_precedes_teardown_and_reaps_fixture_once(tmp_path: Path, signal: str | None, expected: int, reentry: bool, late: bool) -> None:
    result, events, work = _run_positive_start_cleanup_scenario(
        tmp_path, signal=signal, headscale_capture=True, capture_reentry=reentry, late_headscale=late,
    )
    assert result.returncode == expected, result.stderr
    assert not work.exists()
    diagnostics = tmp_path / "diagnostics"
    assert (diagnostics / "cleanup-events.log").read_text().splitlines() == ["cleanup"]
    assert (diagnostics / "cleanup-status.txt").read_text() == "fixtures_reaped=1\n"
    queries = [i for i, event in enumerate(events) if event == "headscale:queried"]
    assert len(queries) == (1 if signal else 2)
    assert max(queries) < events.index("systemctl:stop") < events.index("systemctl:disable") < events.index("systemctl:reset-failed")
    pid = (tmp_path / "private-fixture-pid").read_text().strip()
    assert not Path(f"/proc/{pid}").exists(), "shipping cleanup did not reap its fixture"
    names = ["failure-before-teardown"] if signal else ["first-positive-start-failure", "failure-before-teardown"]
    for name in names:
        raw = (diagnostics / f"{name}.json").read_text()
        snapshot = json.loads(raw)
        assert snapshot["headscale"]["process_state"] == "running"
        assert snapshot["headscale"]["nodes"]["peer"] == ({"count": None, "state": "unknown"} if late else {"count": 1, "state": "online"})
        if late:
            assert snapshot["headscale"]["nodes"]["losses"] == ["timeout"]
            assert snapshot["headscale"]["nodes"]["sidecar"] == {"count": None, "state": "unknown"}
            assert snapshot["headscale"]["log"]["losses"] == ["observed"]
        assert "CANARY" not in raw + result.stdout + result.stderr
    _assert_secret_free_diagnostics(result, diagnostics)


@pytest.mark.parametrize("fault", ["none", "stop", "disable", "reset-failed", "observation-list", "observation-find", "observation-pid"])
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
    if fault.startswith("observation-"):
        assert "evidence:cleanup:all_residue_absent" not in events
        identifier = {"observation-list": "inventory_query_failed", "observation-find": "stage_query_failed", "observation-pid": "mainpid_unconfirmed"}[fault]
        assert result.stderr.count(f"n3-cleanup:final:{identifier}\n") == 1
        assert "TOKEN_CANARY" not in result.stderr


@pytest.mark.parametrize(("signal", "expected", "fault"), [
    ("INT", 130, "stop"), ("TERM", 143, "reset-failed"),
    *[(signal, expected, fault) for signal, expected in (("INT", 130), ("TERM", 143))
      for fault in ("observation-list", "observation-find", "observation-pid")],
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
    if fault.startswith("observation-"):
        assert "evidence:cleanup:all_residue_absent" not in events
        identifier = {"observation-list": "inventory_query_failed", "observation-find": "stage_query_failed", "observation-pid": "mainpid_unconfirmed"}[fault]
        assert result.stderr.count(f"n3-cleanup:final:{identifier}\n") == 1
        assert "TOKEN_CANARY" not in result.stderr


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
if [[ $1 == list-unit-files ]]; then [[ "$*" == 'list-unit-files --full --no-legend --no-pager' ]] || exit 1; echo 'unrelated.service enabled enabled'; fi
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
if [[ $1 == show ]]; then
  printf '%s:%s\\n' "$2" "$4" >>"$SHOW_LOG"
  # Target has common Unit properties, but no Service MainPID property.
  [[ $2 != happyranch-managed.target || $4 != MainPID ]] || exit 0
  selected=0
  [[ -z ${FAULT_UNIT:-} || $2 == "$FAULT_UNIT" ]] && selected=1
  case "$4" in
    LoadState) v=not-found; s=4; if (( selected )); then v=${LOAD_STATE-not-found}; s=${LOAD_RC:-4}; fi;;
    ActiveState) v=inactive; s=4; if (( selected )); then v=${ACTIVE_STATE-inactive}; s=${ACTIVE_RC:-4}; fi;;
    SubState) v=dead; s=4; if (( selected )); then v=${SUB_STATE-dead}; s=${SUB_RC:-4}; fi;;
    MainPID) v=0; s=4; if (( selected )); then v=${MAIN_PID-0}; s=${PID_RC:-4}; [[ ${PID_MISSING:-0} != 1 ]] || exit "$s"; fi;;
    *) exit 91;;
  esac
  printf '%s\\n' "$v"; exit "$s"
fi
if [[ $1 == list-unit-files ]]; then
  if [[ "$*" != 'list-unit-files --full --no-legend --no-pager' ]]; then exit "${NAMED_UNIT_LIST_RC:-1}"; fi
  if [[ ${UNIT_LIST_RESIDUE:-0} == 1 ]]; then echo 'happyranch-managed.target enabled enabled';
  else printf '%s' "${UNIT_LIST_OUTPUT-unrelated.service enabled enabled}"; fi
  exit "${UNIT_LIST_RC:-0}"
fi
exit 0
""")
    (fake_bin / "sudo").write_text("""#!/bin/bash
[[ $1 == rm ]] && exit 0
if [[ $1 == find && -n ${FIND_RC:-} ]]; then printf '%s' "${FIND_OUTPUT-}"; exit "$FIND_RC"; fi
exec "$@"
""")
    (fake_bin / "pgrep").write_text("#!/bin/bash\n[[ -z ${PGREP_RC:-} ]] || exit \"$PGREP_RC\"\n[[ ${PROCESS_RESIDUE:-0} == 1 ]]\n")
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
shipping_cleanup || exit 1
printf 'reset-continuation\\n'
"""
    run_env = os.environ | {"PATH": f"{fake_bin}:{os.environ['PATH']}", "N3_RESIDUE_ROOT": str(tmp_path), "N3_UNIT_ROOT": str(tmp_path),
                            "SHOW_LOG": str(tmp_path / "systemctl-show.log")} | env
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=run_env, check=False)


@pytest.mark.parametrize("exit_codes", [(0, 0, 0, 0), (1, 1, 1, 1), (4, 0, 1, 4)])
def test_real_systemd_shipping_cleanup_accepts_recognized_absent_exit_orderings(tmp_path: Path, exit_codes: tuple[int, int, int, int]) -> None:
    result = _run_real_systemd_shipping_cleanup(tmp_path, **dict(zip(("LOAD_RC", "ACTIVE_RC", "SUB_RC", "PID_RC"), map(str, exit_codes), strict=True)))
    assert result.returncode == 0, result.stderr
    assert result.stdout == "reset-continuation\n", result.stderr
    queries = (tmp_path / "systemctl-show.log").read_text().splitlines()
    assert queries == [
        "happyranch-managed.target:LoadState", "happyranch-managed.target:ActiveState",
        "happyranch-managed.target:SubState",
        *[f"{unit}:{prop}" for unit in ("happyranch-tsnet-sidecar.service", "happyranch-connector.service")
          for prop in ("LoadState", "ActiveState", "SubState", "MainPID")],
    ]


@pytest.mark.parametrize("env", [
    {"LOAD_STATE": "", "LOAD_RC": "4"}, {"LOAD_STATE": "not-found", "LOAD_RC": "2"},
    {"LOAD_STATE": "loaded", "LOAD_RC": "0"},
    {"ACTIVE_STATE": "active", "ACTIVE_RC": "0"}, {"MAIN_PID": "42", "PID_RC": "0"},
    {"UNIT_LIST_RESIDUE": "1"}, {"PROCESS_RESIDUE": "1"}, {"PORT_RESIDUE": "1"},
    {"LISTENER_RESIDUE": "1"}, {"FIXTURE_RESIDUE": "1"},
    {"UNIT_LIST_RC": "1", "UNIT_LIST_OUTPUT": ""},
    {"UNIT_LIST_RC": "1", "UNIT_LIST_OUTPUT": "unrelated.service enabled enabled"},
    {"UNIT_LIST_RC": "2"}, {"UNIT_LIST_RC": "2", "UNIT_LIST_RESIDUE": "1"},
    {"FIND_RC": "1"}, {"FIND_RC": "1", "FIND_OUTPUT": "/.happyranch-stage-leftover"},
    {"PGREP_RC": "0"}, {"PGREP_RC": "2"}, {"PGREP_RC": "3"}, {"PGREP_RC": "7"},
    {"MAIN_PID": "", "PID_RC": "0"}, {"MAIN_PID": "", "PID_RC": "4"},
    {"MAIN_PID": "prose"}, {"MAIN_PID": "00"}, {"MAIN_PID": "0", "PID_RC": "2"},
])
def test_real_systemd_shipping_cleanup_rejects_query_and_probe_residue(tmp_path: Path, env: dict[str, str]) -> None:
    result = _run_real_systemd_shipping_cleanup(tmp_path, **env)
    assert result.returncode != 0
    assert "reset-continuation" not in result.stdout
    identifiers = [line for line in result.stderr.splitlines() if line.startswith("n3-cleanup:")]
    assert identifiers and len(identifiers) == len(set(identifiers))
    assert len(identifiers) <= 46 and sum(len(line) + 1 for line in identifiers) < 4096
    assert all(re.fullmatch(r"n3-cleanup:shipping:[a-z_]+", line) for line in identifiers)


@pytest.mark.parametrize("phase", ["shipping", "final"])
@pytest.mark.parametrize("unit", ["happyranch-connector.service", "happyranch-tsnet-sidecar.service"])
@pytest.mark.parametrize("observation", [
    {"PID_MISSING": "1", "PID_RC": "0"}, {"PID_MISSING": "1", "PID_RC": "4"},
    {"MAIN_PID": "", "PID_RC": "0"}, {"MAIN_PID": "", "PID_RC": "4"},
    {"MAIN_PID": "prose"}, {"MAIN_PID": "00"}, {"MAIN_PID": "42", "PID_RC": "0"},
    {"MAIN_PID": "0", "PID_RC": "2"}, {"MAIN_PID": "0", "PID_RC": "7"},
])
def test_real_systemd_cleanup_each_service_requires_affirmative_pid_absence(
    tmp_path: Path, phase: str, unit: str, observation: dict[str, str],
) -> None:
    """Only the selected service fails; absent target and sibling stay valid."""
    observation = observation | {"FAULT_UNIT": unit}
    if phase == "shipping":
        result = _run_real_systemd_shipping_cleanup(tmp_path, **observation)
        assert "reset-continuation" not in result.stdout
        assert result.stderr == "n3-cleanup:shipping:unit_absence_unconfirmed\n"
    else:
        result, events = _run_source_cleanup_acceptance(
            tmp_path, artifact_run="run", cleanup_run="run", include_cleanup=True, observation=observation,
        )
        assert "evidence:finalize" not in events and "evidence:validate" not in events
        assert "evidence:cleanup:all_residue_absent" not in events
        assert result.stderr.count("n3-cleanup:final:mainpid_unconfirmed\n") == 1
        assert "sudo:rm" in events and not (tmp_path / "work").exists()
    assert result.returncode != 0
    queries = (tmp_path / "systemctl-show.log").read_text().splitlines()
    assert queries.count(f"{unit}:MainPID") == 1
    sibling = "happyranch-tsnet-sidecar.service" if unit == "happyranch-connector.service" else "happyranch-connector.service"
    assert queries.count(f"{sibling}:MainPID") == 1
    assert "happyranch-managed.target:MainPID" not in queries


@pytest.mark.parametrize(("property", "observation"), [
    ("LoadState", {"LOAD_STATE": "loaded", "LOAD_RC": "0"}),
    ("ActiveState", {"ACTIVE_STATE": "active", "ACTIVE_RC": "0"}),
    ("SubState", {"SUB_STATE": "running", "SUB_RC": "0"}),
    ("LoadState", {"LOAD_STATE": "", "LOAD_RC": "0"}),
    ("ActiveState", {"ACTIVE_STATE": "", "ACTIVE_RC": "0"}),
    ("SubState", {"SUB_STATE": "", "SUB_RC": "0"}),
    ("LoadState", {"LOAD_STATE": "not-found", "LOAD_RC": "2"}),
    ("ActiveState", {"ACTIVE_STATE": "inactive", "ACTIVE_RC": "2"}),
    ("SubState", {"SUB_STATE": "dead", "SUB_RC": "2"}),
])
def test_real_systemd_shipping_cleanup_target_requires_common_absence_observations(
    tmp_path: Path, property: str, observation: dict[str, str],
) -> None:
    result = _run_real_systemd_shipping_cleanup(
        tmp_path, **(observation | {"FAULT_UNIT": "happyranch-managed.target"}),
    )
    assert result.returncode != 0
    assert "reset-continuation" not in result.stdout
    assert result.stderr == "n3-cleanup:shipping:unit_absence_unconfirmed\n"
    queries = (tmp_path / "systemctl-show.log").read_text().splitlines()
    assert queries.count(f"happyranch-managed.target:{property}") == 1
    assert "happyranch-managed.target:MainPID" not in queries
    assert queries.count("happyranch-connector.service:MainPID") == 1
    assert queries.count("happyranch-tsnet-sidecar.service:MainPID") == 1


@pytest.mark.parametrize("residue", [
    "etc/systemd/system/happyranch-managed.target", "run/systemd/system/happyranch-managed.target",
    "etc/systemd/system/happyranch-managed.target.d", "run/systemd/system/happyranch-managed.target.d",
    "etc/happyranch/enrollment.key", ".happyranch-install-transaction.json", ".happyranch-backup",
    ".happyranch-units-backup", "opt/happyranch", "var/lib/happyranch-connector",
    "run/happyranch-tsnet-sidecar", "var/log/happyranch-connector", ".happyranch-stage-leftover",
    ".happyranch-tmp-leftover",
])
def test_real_systemd_shipping_cleanup_rejects_every_filesystem_residue_class(tmp_path: Path, residue: str) -> None:
    path = tmp_path / residue
    path.parent.mkdir(parents=True, exist_ok=True)
    path.mkdir() if "." not in path.name else path.write_text("residue")
    result = _run_real_systemd_shipping_cleanup(tmp_path)
    assert result.returncode != 0
    assert "reset-continuation" not in result.stdout
    if "happyranch-managed.target" in residue:
        assert result.stderr == "n3-cleanup:shipping:unit_absence_unconfirmed\n"


@pytest.mark.parametrize("phase", ["shipping", "final"])
@pytest.mark.parametrize(("inventory", "status", "expected_identifier"), [
    ("unrelated.service enabled enabled\nhappyranch-managed.target-extra.service static -\nprefix-happyranch-connector.service disabled disabled\nhappyranch-tsnet-sidecar.service-extra.service alias -\n", "0", None),
    *[(f"unrelated.service enabled enabled\n{unit} disabled disabled\n", "0", "unit_file_residue")
      for unit in ("happyranch-managed.target", "happyranch-connector.service", "happyranch-tsnet-sidecar.service")],
    ("", "1", "inventory_query_failed"),
    ("unrelated.service enabled enabled\n", "1", "inventory_query_failed"),
    ("", "7", "inventory_query_failed"),
    ("unrelated.service enabled enabled\n", "7", "inventory_query_failed"),
    ("", "0", "inventory_malformed"),
    ("TOKEN_CANARY arbitrary upstream text\n", "0", "inventory_malformed"),
    ("unrelated.service enabled enabled EXTRA\n", "0", "inventory_malformed"),
    ("unrelated.service enabled\n", "0", "inventory_malformed"),
    ("unrelated.service enabled enabled\r\n", "0", "inventory_malformed"),
    ("unrelated.service enabled enabled\nunrelated.service enabled enabled\n", "0", "inventory_malformed"),
    ("happyranch-managed.target disabled disabled\nMALFORMED\n", "0", "inventory_malformed"),
])
def test_real_systemd_cleanup_inventory_requires_success_and_exact_owned_names(
    tmp_path: Path, phase: str, inventory: str, status: str, expected_identifier: str | None,
) -> None:
    """Actual source helpers in existing cleanup seams; named no-match is rc1."""
    observation = {"NAMED_UNIT_LIST_RC": "1", "UNIT_LIST_RC": status, "UNIT_LIST_OUTPUT": inventory}
    if phase == "shipping":
        result = _run_real_systemd_shipping_cleanup(tmp_path, **observation)
        continued = "reset-continuation" in result.stdout
    else:
        result, events = _run_source_cleanup_acceptance(
            tmp_path, artifact_run="run", cleanup_run="run", include_cleanup=True, observation=observation,
        )
        continued = "evidence:finalize" in events and "evidence:validate" in events
        assert "sudo:rm" in events
        assert not (tmp_path / "work").exists()
    assert (result.returncode == 0) == (expected_identifier is None), result.stderr
    assert continued == (expected_identifier is None), result.stderr
    if expected_identifier is not None:
        assert f"n3-cleanup:{phase}:{expected_identifier}\n" in result.stderr
    assert "TOKEN_CANARY" not in result.stderr + result.stdout
    assert not list(tmp_path.rglob(".n3-unit-inventory.*"))


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
@pytest.mark.parametrize("upgrade_kind", ["same", "distinct"])
def test_upgrade_rolls_back_at_every_publication_boundary(
    tmp_path: Path, boundary: str, upgrade_kind: str,
) -> None:
    package = build_linux_package(tmp_path / "pkg.tar", *_inputs(tmp_path), version="1")
    root = tmp_path / "root"
    install_linux_package(package, root)
    before = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    before_snapshot = _installer_snapshot(root)
    upgrade = package
    if upgrade_kind == "distinct":
        from tests.remote_access.test_linux_package_transaction import _distinct_unit_package

        upgrade = _distinct_unit_package(tmp_path, "2", b"new")
    def fault(name: str) -> None:
        if name == boundary:
            raise RuntimeError("injected")
    with pytest.raises(RuntimeError, match="injected"):
        install_linux_package(upgrade, root, fault=fault)
    after = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    assert after == before
    assert not list(root.glob(".happyranch-*"))
    assert _installer_snapshot(root) == before_snapshot


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


@pytest.mark.parametrize("name,expected", [
    pytest.param("happyranch-linux-amd64/../escape", "archive_member_invalid", id="traversal"),
    pytest.param("happyranch-linux-amd64/unmanifested", "manifest_membership_mismatch", id="unmanifested"),
    pytest.param(".", "archive_member_invalid", id="dot"),
    pytest.param("./", "archive_member_invalid", id="dot-slash"),
])
def test_archive_rejects_traversal_and_unmanifested_members_before_write(
    tmp_path: Path, name: str, expected: str,
) -> None:
    package = build_linux_package(tmp_path / "pkg.tar", *_inputs(tmp_path), version="1")
    def mutate(entries) -> None:
        member = tarfile.TarInfo(name)
        member.mode = 0o600
        member.uname = member.gname = "root"
        entries.append((member, b"hostile"))
    malformed = _rewrite_package(package, tmp_path / "bad.tar", mutate)
    with tarfile.open(malformed) as archive:
        member = archive.getmembers()[-1]
        assert member.isfile() and member.name == name
        if name in {".", "./"}:
            assert not PurePosixPath(member.name).parts
    root = tmp_path / expected
    before = _tree_snapshot(tmp_path)
    try:
        with pytest.raises(PackageError, match=f"^{expected}$"):
            install_linux_package(malformed, root)
    finally:
        assert not root.exists()
        assert _tree_snapshot(tmp_path) == before


@pytest.mark.parametrize("mutation", [
    "inventory_boolean", "sbom_missing_purl", "notice_wrong_content",
    "sbom_array", "sbom_null", "sbom_boolean", "sbom_string", "sbom_integer",
])
def test_complete_evidence_structure_fails_closed_before_write(tmp_path: Path, mutation: str) -> None:
    package = build_linux_package(tmp_path / "pkg.tar", *_inputs(tmp_path), version="1")
    def mutate(entries) -> None:
        manifest_i = next(i for i, (m, _) in enumerate(entries) if m.name.endswith("manifest.json"))
        manifest_member, manifest_raw = entries[manifest_i]
        manifest = json.loads(manifest_raw)
        suffix = ("sbom.cdx.json" if mutation.startswith("sbom_") else
                  {"inventory_boolean": "dependency-inventory.json",
                   "notice_wrong_content": "THIRD_PARTY_NOTICES.md"}[mutation])
        evidence_i = next(i for i, (m, _) in enumerate(entries) if m.name.endswith(suffix))
        evidence_member, evidence_raw = entries[evidence_i]
        if mutation == "inventory_boolean":
            evidence = json.loads(evidence_raw); evidence["schema_version"] = True
            evidence_raw = json.dumps(evidence).encode()
        elif mutation == "sbom_missing_purl":
            evidence = json.loads(evidence_raw); evidence["components"][0].pop("purl")
            evidence_raw = json.dumps(evidence).encode()
        elif mutation.startswith("sbom_"):
            evidence_raw = json.dumps({
                "sbom_array": [], "sbom_null": None, "sbom_boolean": True,
                "sbom_string": "non-object", "sbom_integer": 7,
            }[mutation]).encode()
        else:
            evidence_raw = evidence_raw.replace(b"fixture license", b"tampered license")
        entries[evidence_i] = evidence_member, evidence_raw
        relative = str(PurePosixPath(evidence_member.name).relative_to("happyranch-linux-amd64"))
        next(item for item in manifest["files"] if item["path"] == relative)["sha256"] = hashlib.sha256(evidence_raw).hexdigest()
        entries[manifest_i] = manifest_member, json.dumps(manifest).encode()
    root = tmp_path / "root"
    malformed = _rewrite_package(package, tmp_path / "bad.tar", mutate)
    before = _tree_snapshot(tmp_path)
    expected = "^sbom_invalid$" if mutation in {
        "sbom_array", "sbom_null", "sbom_boolean", "sbom_string", "sbom_integer",
    } else None
    try:
        with pytest.raises(PackageError, match=expected):
            install_linux_package(malformed, root)
    finally:
        assert not root.exists()
        assert _tree_snapshot(tmp_path) == before


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

    # Retain the original combined-invalid shape above. These records come
    # from the real durable writer so unrelated missing fields cannot mask
    # the independently supplied root, phase, or value-type fault.
    values = ["yes", 1] if mutation == "wrong-type" else [None]
    for index, value in enumerate(values):
        isolated = tmp_path / f"isolated-{index}"
        install_linux_package(package, isolated)
        guard = _InstallerGuard(
            operation="payload_publish", stage="after", exception=KeyboardInterrupt,
        )
        with pytest.raises(KeyboardInterrupt):
            install_linux_package(package, isolated, guard=guard)
        assert len(guard.receipts()) == 1
        isolated_marker = isolated / TRANSACTION_MARKER
        isolated_record = json.loads(isolated_marker.read_text())
        assert isolated_record["schema_version"] == 2
        assert isolated_record["phase"] == "payload_retained"
        if mutation == "bad-json":
            isolated_marker.write_text("{not json")
        elif mutation == "non-object":
            isolated_marker.write_text(json.dumps([1, 2, 3]))
        else:
            if mutation == "missing-key":
                isolated_record.pop("attempt_id")
            elif mutation == "extra-key":
                isolated_record["unexpected"] = 1
            elif mutation == "wrong-type":
                isolated_record["payload_present"] = value
            elif mutation == "unknown-phase":
                isolated_record["phase"] = "invented"
            else:
                isolated_record["root"] = str(isolated / "elsewhere")
            isolated_marker.write_text(json.dumps(isolated_record))
        isolated_marker.chmod(0o600)
        isolated_before = _installer_snapshot(isolated)
        for _ in range(2):
            with pytest.raises(PackageError, match="transaction_state_invalid"):
                install_linux_package(package, isolated)
            assert _installer_snapshot(isolated) == isolated_before


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


def test_interrupted_retirement_reentry_finishes_source_after_dropin_reload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "enrollment.key"
    marker = tmp_path / "state" / "credential.consumed"
    dropin = tmp_path / "unit.d" / "10-enrollment-credential.conf"
    marker.parent.mkdir(); dropin.parent.mkdir()
    source.write_text("one-use\n"); source.chmod(0o600)
    marker.write_text("durable\n"); marker.chmod(0o600)
    observed: list[tuple[bool, bool]] = []
    monkeypatch.setattr("runtime.remote_access.cli._reload_systemd", lambda: observed.append((dropin.exists(), source.exists())))
    _reconcile_enrollment_retirement(source, marker, dropin=dropin)
    assert observed == [(False, True)]
    assert not source.exists()


@pytest.mark.parametrize("interruption", ["reload", "unlink-fsync"])
@pytest.mark.parametrize("reentry", ["direct", "reconcile"])
def test_retirement_retry_preserves_source_until_successful_reload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, interruption: str, reentry: str,
) -> None:
    from runtime.remote_access import cli

    source = tmp_path / "enrollment.key"
    marker = tmp_path / "credential.consumed"
    dropin = tmp_path / "unit.d" / "10-enrollment-credential.conf"
    dropin.parent.mkdir()
    for path, data in [(source, b"one-use\n"), (marker, b"durable\n"), (dropin, b"[Service]\n")]:
        path.write_bytes(data); path.chmod(0o600)
    before = {path: (path.read_bytes(), path.stat().st_mode) for path in [source, marker]}
    failed_observations: list[tuple[bool, bool]] = []

    def failed_reload() -> None:
        failed_observations.append((dropin.exists(), source.exists()))
        raise subprocess.CalledProcessError(7, ["systemctl", "daemon-reload"])

    real_fsync = cli._fsync_dir

    def interrupted_unlink(path: Path) -> None:
        if path == dropin.parent:
            raise OSError("interrupted after unlink")
        real_fsync(path)

    monkeypatch.setattr(cli, "_reload_systemd", failed_reload)
    if interruption == "unlink-fsync":
        monkeypatch.setattr(cli, "_fsync_dir", interrupted_unlink)
    with pytest.raises(subprocess.CalledProcessError if interruption == "reload" else OSError):
        _retire_enrollment_source(source, marker, dropin=dropin)
    monkeypatch.setattr(cli, "_fsync_dir", real_fsync)
    assert not dropin.exists()
    assert {path: (path.read_bytes(), path.stat().st_mode) for path in before} == before
    retry = _retire_enrollment_source if reentry == "direct" else _reconcile_enrollment_retirement
    # Observe the retained files even when the regression returns without raising.
    failed = False
    try:
        retry(source, marker, dropin=dropin)
    except subprocess.CalledProcessError:
        failed = True
    assert source.exists(), "retry deleted source without successful reload"
    assert {path: (path.read_bytes(), path.stat().st_mode) for path in before} == before
    assert failed
    assert failed_observations == [(False, True)] * (2 if interruption == "reload" else 1)
    observed: list[tuple[bool, bool]] = []
    monkeypatch.setattr(cli, "_reload_systemd", lambda: observed.append((dropin.exists(), source.exists())))
    retry(source, marker, dropin=dropin)
    assert observed == [(False, True)]
    assert not source.exists() and not source.with_name("enrollment.key.retiring").exists()
    assert (marker.read_bytes(), marker.stat().st_mode) == before[marker]
    retry(source, marker, dropin=dropin)
    assert observed == [(False, True)]


@pytest.mark.parametrize("reentry", ["direct", "reconcile"])
def test_interrupted_retirement_residue_requires_reload_before_unlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reentry: str,
) -> None:
    from runtime.remote_access import cli

    source = tmp_path / "enrollment.key"
    marker = tmp_path / "credential.consumed"
    dropin = tmp_path / "unit.d" / "10-enrollment-credential.conf"
    dropin.parent.mkdir()
    for path, data in [(source, b"one-use\n"), (marker, b"durable\n"), (dropin, b"[Service]\n")]:
        path.write_bytes(data); path.chmod(0o600)
    retiring = source.with_name("enrollment.key.retiring")
    before = (source.read_bytes(), source.stat().st_mode)
    marker_before = (marker.read_bytes(), marker.stat().st_mode)
    real_fsync = cli._fsync_dir
    observed: list[tuple[bool, bool, bool]] = []
    monkeypatch.setattr(cli, "_reload_systemd", lambda: observed.append((dropin.exists(), source.exists(), retiring.exists())))

    def interrupt_after_rename(path: Path) -> None:
        if path == source.parent and retiring.exists():
            raise OSError("interrupted after rename")
        real_fsync(path)

    monkeypatch.setattr(cli, "_fsync_dir", interrupt_after_rename)
    with pytest.raises(OSError, match="interrupted after rename"):
        _retire_enrollment_source(source, marker, dropin=dropin)
    monkeypatch.setattr(cli, "_fsync_dir", real_fsync)
    assert observed == [(False, True, False)]
    assert not source.exists() and not dropin.exists()
    assert (retiring.read_bytes(), retiring.stat().st_mode) == before

    def failed_reload() -> None:
        raise subprocess.CalledProcessError(7, ["systemctl", "daemon-reload"])

    monkeypatch.setattr(cli, "_reload_systemd", failed_reload)
    retry = _retire_enrollment_source if reentry == "direct" else _reconcile_enrollment_retirement
    failed = False
    try:
        retry(source, marker, dropin=dropin)
    except subprocess.CalledProcessError:
        failed = True
    assert retiring.exists(), "retry unlinked residue without successful reload"
    assert (retiring.read_bytes(), retiring.stat().st_mode) == before
    assert (marker.read_bytes(), marker.stat().st_mode) == marker_before
    assert failed
    monkeypatch.setattr(cli, "_reload_systemd", lambda: observed.append((dropin.exists(), source.exists(), retiring.exists())))
    retry(source, marker, dropin=dropin)
    assert observed == [(False, True, False), (False, False, True)]
    assert not source.exists() and not retiring.exists()
    assert (marker.read_bytes(), marker.stat().st_mode) == marker_before


@pytest.mark.parametrize("command", ["retire-enrollment-source", "reconcile-enrollment-retirement"])
def test_retirement_cli_reload_failure_is_category_only(tmp_path: Path, command: str) -> None:
    source = tmp_path / "enrollment.key"
    marker = tmp_path / "credential.consumed"
    dropin = tmp_path / "10-enrollment-credential.conf"
    for path, data in [(source, b"one-use\n"), (marker, b"durable\n"), (dropin, b"[Service]\n")]:
        path.write_bytes(data); path.chmod(0o600)
    if command == "reconcile-enrollment-retirement":
        dropin.unlink()  # interrupted unlink is not successful-reload evidence
    retained = {path: (path.read_bytes(), path.stat().st_mode) for path in [source, marker]}
    fixture_bin = tmp_path / "bin"
    fixture_bin.mkdir()
    calls = tmp_path / "systemctl.calls"
    fixture = fixture_bin / "systemctl"
    fixture.write_text('#!/bin/sh\n[ "$*" = daemon-reload ] || exit 99\nprintf "%s\\n" "$*" >> "$SYSTEMCTL_CALLS"\necho RAW_UPSTREAM_SECRET >&2\nexit "$SYSTEMCTL_RELOAD_EXIT"\n')
    fixture.chmod(0o700)

    def invoke(command: str, reload_exit: int) -> subprocess.CompletedProcess[str]:
        env = os.environ | {"PATH": str(fixture_bin) + os.pathsep + os.environ["PATH"],
                           "SYSTEMCTL_CALLS": str(calls), "SYSTEMCTL_RELOAD_EXIT": str(reload_exit)}
        return subprocess.run(
            [sys.executable, "-m", "runtime.remote_access.cli", command,
             "--source", str(source), "--marker", str(marker), "--dropin", str(dropin)],
            env=env, check=False, capture_output=True, text=True, timeout=10,
        )

    for attempt_command in [command, "reconcile-enrollment-retirement"]:
        result = invoke(attempt_command, 7)
        assert (result.returncode, result.stdout, result.stderr) == (1, "", "error: enrollment_source_retirement_failed\n")
        assert {path: (path.read_bytes(), path.stat().st_mode) for path in retained} == retained
        assert not dropin.exists()
    result = invoke("reconcile-enrollment-retirement", 0)
    assert (result.returncode, result.stdout, result.stderr) == (0, "", "")
    assert calls.read_text().splitlines() == ["daemon-reload"] * 3
    assert not source.exists() and not source.with_name("enrollment.key.retiring").exists()
    assert (marker.read_bytes(), marker.stat().st_mode) == retained[marker]


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
    ("credential_wrong_type", "credential_unsafe_symlink", "credential_wrong_custody", "credential_staging_incompatible", "credential_absent"),
)
def test_each_rendered_unit_rejects_invalid_staged_type_mode_or_path_without_leak(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    name: str,
    unit: str,
    category: str,
) -> None:
    """Observe real staged object refusals; custody row retains dispatch only."""
    secret = "forbidden-credential-material"
    staged = tmp_path / unit
    staged.mkdir()
    credential = staged / name
    monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(staged))
    monkeypatch.setattr(
        "runtime.remote_access.cli._expected_systemd_credentials_directory",
        lambda _unit: staged,
    )
    # Staged custody is deliberately ownership-neutral. Preserve this original
    # category propagation row without pretending to impose direct-source policy.
    states = {
        "credential_wrong_type": ("directory",),
        "credential_unsafe_symlink": ("symlink",),
        "credential_wrong_custody": ("category_dispatch",),
        "credential_staging_incompatible": ("writable", "empty"),
        "credential_absent": ("absent",),
    }[category]
    for state in states:
        if state == "directory":
            credential.mkdir()
            (credential / "synthetic").write_text(secret)
        elif state == "symlink":
            target = tmp_path / "synthetic-target"
            target.write_text(secret)
            credential.symlink_to(target)
        elif state in {"writable", "empty"}:
            credential.write_text(secret if state == "writable" else "")
            credential.chmod(0o600 if state == "writable" else 0o400)
        staged.chmod(0o500)
        before = _tree_snapshot(tmp_path)
        link = os.readlink(credential) if credential.is_symlink() else None
        try:
            with monkeypatch.context() as dispatch:
                if state == "category_dispatch":
                    dispatch.setattr("runtime.remote_access.cli.credential_capability", lambda *_args, **_kwargs: category)
                assert connector_cli_main([
                    "credential-capability", "--name", name, "--unit", unit,
                ]) == 1
            captured = capsys.readouterr()
            error = captured.err.strip()
            assert error == category
            assert captured.out == ""
            assert secret not in error
            assert "/run/credentials" not in error
            assert str(tmp_path) not in error
            assert _tree_snapshot(tmp_path) == before
            assert (os.readlink(credential) if credential.is_symlink() else None) == link
        finally:
            staged.chmod(0o700)
        if credential.is_file() and not credential.is_symlink():
            credential.unlink()


def test_packaged_preflight_refuses_missing_staging_and_accepts_consumed_marker(
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


def test_installed_fixture_payload_executes_outside_source_checkout(tmp_path: Path) -> None:
    """The copied interpreter payload survives installer relocation; no frozen CLI claim."""
    package = build_linux_package(tmp_path / "pkg.tar", *_inputs(tmp_path), version="1")
    root = tmp_path / "root"
    install_linux_package(package, root)
    result = subprocess.run([root / "opt/happyranch/bin/happyranch-connector", "--help"],
                            cwd=tmp_path, text=True, capture_output=True, check=True)
    assert "usage:" in result.stdout
    assert "No module named" not in result.stderr
