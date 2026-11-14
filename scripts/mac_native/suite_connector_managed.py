"""Combined source CLI acceptance, synthetic inputs, fixed UID-scoped agents.

No tailnet enrollment or packaged-app claim. Never touches preexisting targets.
"""
from __future__ import annotations

import json
import os
import plistlib
import signal
import shutil
import socket
import sys
import tempfile
import time
import venv
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scripts.mac_native.suite_supervisor import _command
from scripts.mac_native.toolchain import PINS, parse_pins, provision

ROOT = Path(__file__).resolve().parents[2]
LABELS = ("ai.happyranch.connector", "ai.happyranch.tsnet-sidecar")


def service_absent(code: int, output: str, label: str) -> bool:
    return code != 0 and f'Could not find service "{label}" in domain' in output


def domain_unavailable(code: int, output: str) -> bool:
    return code != 0 and ("Could not find domain for" in output or
                         (code == 125 and "Domain does not support specified action" in output))


def process_running(status: dict[str, Any]) -> bool:
    # CLI JSON is dataclasses.asdict: the ServiceStatus.running property is
    # intentionally not serialized. Observe its native fields instead.
    return (status.get("active_state") == "active" and status.get("sub_state") == "running"
            and status.get("load_state") == "loaded" and status.get("pid", 0) > 0)


def honest_not_ready(status: dict[str, Any]) -> bool:
    """Require a loaded agent and its own negative record, including failed startup."""
    side = status.get("sidecar", {})
    return (status.get("ready") is False and side.get("ready") is False
            and side.get("load_state") == "loaded"
            and side.get("readiness_state") in {"running-not-ready", "starting", "pid-mismatch"}
            and side.get("readiness_record_state") == "valid"
            and side.get("readiness_reason") in {"starting", "waiting-for-readiness"})


def private(path: Path, value: bytes) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with path.open("xb") as stream:
        os.fchmod(stream.fileno(), 0o600)
        stream.write(value)


def run(report: dict[str, Any]) -> None:
    checks: list[dict[str, Any]] = []
    section: dict[str, Any] = {"checks": checks, "observations": {}, "cleanup": {},
                               "labels": LABELS, "uid": os.getuid(), "owned": False}
    report["connector_managed"] = section
    # 240s provisioning + <=2700s cold Go build + <=240s lifecycle + cleanup,
    # within the existing 5400s job (including its <=300s bootstrap).
    deadline = time.monotonic() + 3300
    step = "absence"
    job_home = Path(os.environ.get("HOME", ""))
    job_tmp = Path(os.environ.get("TMPDIR", ""))
    if (not job_home.is_absolute() or not job_tmp.is_absolute()
            or not job_home.is_dir() or not job_tmp.is_dir()
            or job_home.resolve().parent != job_tmp.resolve().parent
            or not Path(sys.prefix).resolve().is_relative_to(job_home.resolve().parent)):
        raise RuntimeError("connector-managed requires the job-private HOME/TMPDIR/interpreter")

    def check(name: str, passed: bool) -> None:
        checks.append({"name": name, "status": "PASS" if passed else "FAIL"})
        if not passed:
            raise RuntimeError(f"connector-managed check failed: {name}")

    scratch = Path(tempfile.mkdtemp(prefix="connector-managed-", dir=job_tmp)).resolve()
    retain_scratch = False
    try:
        home, tmp = scratch / "home", scratch / "tmp"
        home.mkdir(mode=0o700)
        tmp.mkdir(mode=0o700)
        env = dict(os.environ, HOME=str(home), TMPDIR=str(tmp))
        for key in ("PYTHONPATH", "PYTHONHOME"):
            env.pop(key, None)
        agents = home / "Library/LaunchAgents"
        plists = [agents / f"{label}.plist" for label in LABELS]
        state = home / "Library/Application Support/HappyRanch/connector"
        domains: list[str] = []
        owned = False
        python = scratch / "venv/bin/python3"
        cleanup_deadline: float | None = None

        def command(argv: list[str], *, seconds: float = 10, cwd: Path = scratch,
                    cleanup: bool = False) -> tuple[int, str]:
            until = time.monotonic() + seconds
            if not cleanup:
                until = min(deadline, until)
            elif cleanup_deadline is not None:
                until = min(cleanup_deadline, until)
            if until <= time.monotonic():
                raise TimeoutError("connector-managed command deadline")
            code, raw = _command(argv, env, cwd, until, output_cap=1024 * 1024)
            output = raw.decode("utf-8", errors="replace")
            if argv[:1] == ["launchctl"]:
                key = " ".join(argv[1:])
                section["observations"].setdefault(step, {})[key] = {
                    "exit_code": code, "excerpt": output[:2048]}
            return code, output

        def snapshots() -> None:
            for domain in domains:
                command(["launchctl", "print-disabled", domain])
                for label in LABELS:
                    command(["launchctl", "print", f"{domain}/{label}"])

        def verify_absent() -> None:
            for domain in domains:
                for label in LABELS:
                    code, output = command(["launchctl", "print", f"{domain}/{label}"])
                    check(f"{step}_{domain}/{label}_absent", service_absent(code, output, label))

        def cli(*args: str, expected: int = 0, cleanup: bool = False) -> dict:
            code, output = command([str(python), "-B", "-m", "runtime.remote_access.installer", *args],
                                   seconds=120 if args[0] in {"stop", "restart", "uninstall"} else 35,
                                   cleanup=cleanup)
            # Inputs are synthetic; still retain only bounded CLI diagnostics.
            section.setdefault("cli", {})[step] = {"exit_code": code, "excerpt": output[:8192]}
            check(f"{step}_exit", code == expected)
            return json.loads(output) if code == 0 and output.strip() else {}

        try:
            # ALL fixed targets and private plists, not just the selected domain.
            for domain in (f"user/{os.getuid()}", f"gui/{os.getuid()}"):
                code, output = command(["launchctl", "print", domain])
                if code == 0:
                    domains.append(domain)
                    for label in LABELS:
                        code, output = command(["launchctl", "print", f"{domain}/{label}"])
                        check(f"{domain}/{label}_absent", service_absent(code, output, label))
                else:
                    check(f"{domain}_unavailable", domain.startswith("gui/") and domain_unavailable(code, output))
            check("both_plists_absent", all(not os.path.lexists(path) for path in plists))
            check("user_domain_observed", f"user/{os.getuid()}" in domains)
            owned = section["owned"] = True
            snapshots()

            # Scratch venv .pth references only the already-bootstrapped private
            # packages and this checkout. No pip, downloads or product env hook.
            step = "importability"
            venv.EnvBuilder(with_pip=False).create(scratch / "venv")
            sites = list((scratch / "venv/lib").glob("python*/site-packages"))
            check("one_private_site", len(sites) == 1)
            dependencies = [str(Path(path).resolve()) for path in sys.path if path
                            and Path(path).name == "site-packages"
                            and Path(path).resolve().is_relative_to(job_home.resolve().parent)]
            check("offline_dependencies_present", bool(dependencies))
            (sites[0] / "source.pth").write_text("\n".join([str(ROOT), *dependencies]) + "\n")
            code, output = command([str(python), "-I", "-B", "-c",
                "import runtime.remote_access.cli, runtime.remote_access.installer; print('offline imports available')"])
            section["imports"] = {"exit_code": code, "excerpt": output[:2048]}
            check("offline_source_imports", code == 0)

            step = "build"
            section["provisioning"] = {"attempts": []}
            tools = provision(scratch / "tools", parse_pins(PINS.read_text()),
                              min(deadline, time.monotonic() + 240), section["provisioning"]["attempts"])
            env.update({key: value for key, value in tools.items() if key in {
                "PATH", "GOROOT", "GOPATH", "GOTOOLCHAIN", "GOCACHE", "GOMODCACHE"}})
            env.update(CGO_ENABLED="0", GOOS="darwin", GOARCH="arm64", GOFLAGS="-mod=readonly", GOWORK="off")
            go = str(scratch / "tools/go/bin/go")
            code, version = command([go, "version"])
            section["go"] = {"path": go, "version": version.strip(), "exit_code": code}
            check("pinned_go", code == 0 and version.strip() == "go version go1.23.12 darwin/arm64")
            binary = scratch / "happyranch-tsnet-sidecar"
            started = time.monotonic()
            code, output = command([go, "build", "-o", str(binary), "./cmd/happyranch-tsnet-sidecar"],
                                   seconds=2700, cwd=ROOT / "app/linux/tsnet-sidecar")
            section["build"] = {"exit_code": code, "excerpt": output[:2048],
                                "elapsed_seconds": time.monotonic() - started}
            check("sidecar_built", code == 0 and binary.is_file())
            binary.chmod(0o700)
            deadline = min(deadline, time.monotonic() + 240)

            # No real daemon or control server; all credentials are synthetic.
            policy = json.dumps({"schema_version": 1, "artifact_version": 1, "revision": 1,
                "issued_at": datetime.now(timezone.utc).isoformat(), "max_age_seconds": 3600,
                "state": "active", "artifact": json.loads((ROOT / "runtime/remote_access/route_policy.json").read_text())}).encode()
            private(scratch / "policy.json", policy)
            private(scratch / "daemon.token", b"synthetic-native-probe-daemon-token")
            private(scratch / "enrollment.key", b"synthetic-native-probe-enrollment-key")
            private(scratch / "config.json", json.dumps({"tenant_id": "synthetic-native", "home_id": "synthetic-native",
                "connector_id": "synthetic-native", "daemon_port": 18765,
                "daemon_token_path": str(scratch / "daemon.token"), "policy_path": str(scratch / "policy.json"),
                "poll_seconds": .2, "watchdog_sec": 30, "managed": {"bind_port": 18443}}).encode())
            private(scratch / "sidecar.json", json.dumps({"ControlURL": "https://127.0.0.1:1",
                "RoleIdentity": "home-sidecar-synthetic-native", "ExpectedPeer": "away-synthetic-native",
                "DERPPolicy": "private-only", "ListenAddr": ":18444", "ConnectorAddr": "127.0.0.1:18443"}).encode())
            step = "install"
            cli("install", "--config", str(scratch / "config.json"), "--enrollment-key-file", str(scratch / "enrollment.key"),
                "--sidecar-config", str(scratch / "sidecar.json"), "--sidecar-binary", str(binary))
            check("both_agents_pinned_home", all(plistlib.loads(p.read_bytes())["EnvironmentVariables"] == {
                "HOME": str(home), "TMPDIR": str(tmp)} for p in plists))
            snapshots()
            step = "start"
            cli("start")
            snapshots()

            def wait_status(predicate: Any) -> dict:
                until = min(deadline, time.monotonic() + 40)
                while time.monotonic() < until:
                    status = cli("status")
                    if predicate(status):
                        return status
                    time.sleep(.2)
                raise TimeoutError("managed source CLI status did not reach required substate")

            step = "status"
            status = wait_status(lambda value: honest_not_ready(value) and process_running(value["connector"]))
            section["composite_status"] = status
            check("honest_sidecar_not_ready", honest_not_ready(status))
            snapshots()
            first = status["connector"]["pid"]
            os.kill(first, signal.SIGKILL)  # exact PID from the owned fixed target
            step = "recovery"
            recovered = wait_status(lambda value: process_running(value["connector"])
                                    and value["connector"]["pid"] > 0 and value["connector"]["pid"] != first)
            section["recovery"] = {"old_pid": first, "new_pid": recovered["connector"]["pid"]}
            snapshots()
            step = "stop"
            cli("stop")
            snapshots()
            step = "check-drift"
            stopped = cli("check-drift")
            check("stopped_no_drift", stopped["layout"] == "ok" and not stopped["ready"]
                  and all(not process_running(stopped[name]) for name in ("connector", "sidecar")))
            snapshots()
            # Bind wildcard to produce a real non-loopback lsof observation.
            # No SO_REUSE*: any occupied 8443 refuses; never steal a listener.
            step = "coexistence"
            code, occupied = command(["/usr/sbin/lsof", "-nP", "-iTCP:8443", "-sTCP:LISTEN", "-Fpn"])
            section["port_8443_inventory"] = {"exit_code": code, "empty": not occupied}
            if code != 1 or occupied:
                section["port_8443"] = "occupied-or-unavailable; refusal fixture not created"
                raise RuntimeError("port 8443 not proven free; coexistence fixture refused")
            with socket.socket() as listener:
                try:
                    listener.bind(("0.0.0.0", 8443))
                except OSError:
                    section["port_8443"] = "occupied-or-unavailable; refusal fixture not created"
                    raise RuntimeError("port 8443 unavailable; coexistence fixture refused") from None
                listener.listen(1)
                section["port_8443"] = "owned temporary non-loopback listener"
                for operation in ("install", "start", "restart", "enable"):
                    step = f"coexistence-{operation}"
                    cli(operation, expected=1)
                    check(f"{step}_message", "Swift HomeConnector port 8443" in section["cli"][step]["excerpt"])
                    snapshots()
                step = "coexistence-status"
                check("status_reports_conflict", cli("status")["coexistence"]["state"] == "conflict")
                step = "coexistence-check-drift"
                check("drift_reports_conflict", cli("check-drift")["coexistence"]["state"] == "conflict")
            section["coexistence_listener_closed"] = True
            snapshots()
            step = "uninstall"
            cli("uninstall")
            check("uninstall_plists_absent_state_retained", all(not os.path.lexists(p) for p in plists)
                  and state.is_dir() and not (state / "happyranch-connector/config.json").exists())
            snapshots()
            verify_absent()
            step = "purge"
            cli("uninstall", "--purge")
            check("purged_state_absent", all(not (state / name).exists() for name in (
                "happyranch-connector", "happyranch-tsnet-sidecar")))
            snapshots()
            verify_absent()

        finally:
            step = "cleanup"
            cleanup = section["cleanup"]
            if owned:
                # Reserve independent bounded cleanup even after the main deadline.
                # Boot out ONLY targets proven absent before this run's install.
                from runtime.remote_access.launchd_agent import parse_launchctl_print

                cleanup_deadline = time.monotonic() + 100
                complete = True
                try:
                    for domain in domains:
                        for label in reversed(LABELS):
                            target = f"{domain}/{label}"
                            command(["launchctl", "bootout", target], cleanup=True)
                            started = time.monotonic()
                            until = min(cleanup_deadline, started + 30)
                            receipt: dict[str, Any] = {"last_print": None, "elapsed_seconds": 0}
                            cleanup.setdefault("settle", {})[target] = receipt
                            absent = False
                            try:
                                while time.monotonic() < until:
                                    code, output = command(["launchctl", "print", target], cleanup=True,
                                                           seconds=min(10, until - time.monotonic()))
                                    receipt["last_print"] = {"exit_code": code, "excerpt": output[:2048]}
                                    if service_absent(code, output, label):
                                        domain_code, _ = command(["launchctl", "print", domain], cleanup=True,
                                                                seconds=min(10, until - time.monotonic()))
                                        absent = domain_code == 0 and time.monotonic() < until
                                        break
                                    if code:
                                        break
                                    parse_launchctl_print(label, output, enabled=False)
                                    remaining = until - time.monotonic()
                                    if remaining <= 0:
                                        break
                                    time.sleep(min(.5, remaining))
                            finally:
                                receipt["elapsed_seconds"] = time.monotonic() - started
                                cleanup[f"{target}_absent"] = absent
                            complete &= absent
                        command(["launchctl", "print-disabled", domain], cleanup=True)
                except Exception as exc:
                    cleanup["error"] = type(exc).__name__
                    complete = False
                # Never erase a live service's executable/home after failed stop.
                if not complete:
                    retain_scratch = True
                    cleanup["scratch_retained"] = str(scratch)
                    raise RuntimeError("managed cleanup incomplete; owned scratch retained")
                for path in plists:
                    path.unlink(missing_ok=True)
                cleanup["plists_absent"] = all(not os.path.lexists(p) for p in plists)
                check("cleanup_services_and_plists_absent", complete and cleanup["plists_absent"])
            else:
                cleanup["not_owned_no_service_or_plist_cleanup"] = True
    finally:
        if not retain_scratch:
            shutil.rmtree(scratch)
        section["scratch_removed"] = not scratch.exists()
        if not retain_scratch:
            check("scratch_removed", section["scratch_removed"])
