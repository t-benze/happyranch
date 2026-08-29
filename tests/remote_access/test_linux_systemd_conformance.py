"""Real Linux/systemd conformance for the supervised connector (THR-097
phase unit 3).

Hermetic user-scope systemd conformance on a capable host that launches the
ACTUAL rendered connector: install the unit rendered for the shipping
``ConnectorSupervisor`` (``python -m runtime.remote_access.cli run`` with a
lab provider, LoadCredential=, and the managed-dir config), start it against
the real ``Type=notify`` READY contract, verify it reaches ``active`` with a
live PID (READY is emitted ONLY after every readiness gate passes AND the
listener started), prove the lab listener is actually bound, exercise
status/restart/stop, and verify the listener is gone after stop. A real HTTP
request through the lab listener proves the shipping pipeline forwards to the
literal-loopback daemon. Upgrade/rollback round-trip, enable/disable, and
daemon-reload run against the real user manager too. Every unit is torn down;
no live daemon is touched.

Gated on an operational probe with an explicit skip reason — an unavailable
real capability is NOT silently counted as PASS (skip, not pass), mirroring
``tests/platform/test_task_producer_linux_integration.py``. Runs under
``-m integration`` (unit runs exclude it). Where the runner cannot host user
systemd, the deterministic unit-level fail-closed battery
(``test_supervisor.py``/``test_service_manager.py``/``test_state_store.py``)
remains the coverage.
"""
from __future__ import annotations

import json
import shutil
import socket
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib import request as url_request

import pytest

from runtime.remote_access.lab_provider import LabProviderConfig
from runtime.remote_access.service_manager import SystemdServiceManager
from runtime.remote_access.supervisor import ConnectorConfig, ConnectorSupervisor
from runtime.remote_access.systemd_unit import ConnectorUnitSpec, render_connector_unit

from .conftest import load_fixture

pytestmark = pytest.mark.integration

UNIT_NAME = "happyranch-connector-conformance.service"

_HELPER = """\
import os, socket, time
path = os.environ.get("NOTIFY_SOCKET")
sock = None
if path:
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    sock.connect(path)

def notify(payload):
    if sock is not None:
        sock.sendall(payload)

notify(b"READY=1\\n")
# ping the watchdog while we hold the service up, exactly like the
# connector supervisor's foreground loop
deadline = time.monotonic() + 90
while time.monotonic() < deadline:
    notify(b"WATCHDOG=1\\n")
    time.sleep(2)
"""


def _user_systemd_available() -> str | None:
    """Probe the user systemd manager. Returns a skip reason or None."""
    if shutil.which("systemctl") is None:
        return "systemctl not installed"
    try:
        proc = subprocess.run(
            ["systemctl", "--user", "is-system-running"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"systemctl --user unreachable: {exc}"
    if proc.returncode not in (0, 1):
        return f"systemctl --user is-system-running failed: {proc.stderr.strip()[:200]}"
    if "System has not been booted" in proc.stderr or "Failed to connect" in proc.stderr:
        return "no user systemd manager on this runner"
    return None


def _skip_if_unavailable() -> None:
    reason = _user_systemd_available()
    if reason is not None:
        pytest.skip(f"Linux user systemd conformance not runnable: {reason}")


@pytest.fixture(scope="module")
def manager():
    _skip_if_unavailable()
    return SystemdServiceManager(system=False)


@pytest.fixture(scope="module", autouse=True)
def _clean_state_directory_residue():
    """systemd creates the declared StateDirectory at unit start; remove the
    empty residue so conformance leaves no trace in the real user state home."""
    yield
    shutil.rmtree(
        Path.home() / ".local" / "state" / "happyranch-connector", ignore_errors=True
    )


@pytest.fixture(scope="module")
def notify_helper(tmp_path_factory) -> str:
    helper = tmp_path_factory.mktemp("connector-conformance") / "notify_sleep.py"
    helper.write_text(_HELPER, encoding="utf-8")
    return str(helper)


def _unit_text(helper: str) -> str:
    spec = ConnectorUnitSpec(
        unit_name=UNIT_NAME,
        exec_start=(sys.executable, helper),
        system=False,
        daemon_token_path="",
        watchdog_sec=30,
        restart_sec=1,
    )
    return render_connector_unit(spec)


# ── real-supervisor conformance (finding 3) ────────────────────────────────


class _FakeDaemonHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args) -> None:  # noqa: A002
        return

    def do_GET(self) -> None:
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()


class _FakeDaemon:
    """A literal-127.0.0.1 stand-in for the daemon the readiness probe and the
    loopback forwarder talk to."""

    def __init__(self) -> None:
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeDaemonHandler)
        self.port = int(self.server.server_address[1])
        self.thread = threading.Thread(
            target=self.server.serve_forever, name="hr-fake-daemon", daemon=True
        )
        self.thread.start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _port_open(port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


def _wait_for(predicate, timeout: float = 30.0, interval: float = 0.2) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def _write_policy_envelope(path: Path) -> None:
    """A current, structurally-valid route-policy envelope for the service."""
    artifact = load_fixture("route-policy")
    envelope = {
        "schema_version": 1,
        "artifact_version": int(artifact.get("version", 1)),
        "issued_at": (datetime.now(timezone.utc) - timedelta(seconds=60)).isoformat(),
        "max_age_seconds": 3600,
        "revision": 1,
        "state": "active",
        "artifact": artifact,
    }
    path.write_text(json.dumps(envelope, indent=2), encoding="utf-8")


def test_real_systemd_supervisor_readiness_listener_lifecycle(
    tmp_path_factory,
) -> None:
    """The shipping ConnectorSupervisor under the REAL user systemd manager:
    install the actual rendered unit, start it through the Type=notify READY
    contract (READY only after all gates + listener start), verify active +
    live PID + bound listener + end-to-end request to the literal-loopback
    daemon, then restart and stop with the listener gone."""
    _skip_if_unavailable()
    manager = SystemdServiceManager(system=False)
    base = tmp_path_factory.mktemp("hr-conn-supervisor")
    daemon = _FakeDaemon()
    try:
        token = base / "daemon.token"
        token.write_text("conformance-token", encoding="utf-8")
        token.chmod(0o600)
        policy = base / "policy.json"
        _write_policy_envelope(policy)
        lab_port = _free_port()
        managed_config = str(base / "managed" / "happyranch-connector" / "config.json")
        config = ConnectorConfig(
            tenant_id="tenant-conformance",
            home_id="home-conformance",
            connector_id="connector-conformance",
            daemon_port=daemon.port,
            daemon_token_path=str(token),
            policy_path=str(policy),
            state_path=str(base / "state.json"),
            unit_name=UNIT_NAME,
            system=False,
            managed_dir_root=str(base / "managed"),
            lab=LabProviderConfig(
                bind_host="127.0.0.1", bind_port=lab_port, lab_only=True
            ),
            exec_start=(
                sys.executable,
                "-m",
                "runtime.remote_access.cli",
                "run",
                "--config",
                managed_config,
                "--lab-only",
            ),
        )
        supervisor = ConnectorSupervisor(config=config, manager=manager)
        manager.uninstall(UNIT_NAME)  # hermetic baseline
        try:
            unit_path = supervisor.install(enable=False)
            assert unit_path.is_file()
            # The unit points --config at the MANAGED config (never a
            # daemon-home/state_path-derived path) and renders LoadCredential=.
            unit_text = unit_path.read_text(encoding="utf-8")
            assert f"--config {managed_config}" in unit_text
            assert "~/.happyranch" not in unit_text
            assert f"LoadCredential=daemon.token:{token}" in unit_text

            managed_root = base / "managed" / "happyranch-connector"
            assert (managed_root / "config.json").is_file()
            assert (managed_root / "policy.json").is_file()
            staged = ConnectorConfig.from_file(managed_root / "config.json")
            assert staged.state_path == str(managed_root / "trust-state.json")
            assert staged.policy_path == str(managed_root / "policy.json")

            manager.start(UNIT_NAME)
            assert manager.is_active(UNIT_NAME) is True
            status = manager.status(UNIT_NAME)
            assert status.load_state == "loaded"
            assert status.pid > 0
            assert status.running is True
            # Type=notify: active means READY was emitted — and READY is only
            # emitted after ALL gates passed AND the lab listener started.
            assert _wait_for(lambda: _port_open(lab_port), timeout=30), (
                "connector lab listener never came up under systemd"
            )

            # End-to-end: a real request through the lab listener runs the
            # shipping gateway pipeline and reaches the literal-loopback daemon.
            resp = url_request.urlopen(
                f"http://127.0.0.1:{lab_port}/api/v1/health", timeout=10
            )
            assert resp.status == 200
            resp.close()

            manager.restart(UNIT_NAME)
            assert manager.is_active(UNIT_NAME) is True
            assert _wait_for(lambda: _port_open(lab_port), timeout=30)

            manager.stop(UNIT_NAME)
            assert manager.is_active(UNIT_NAME) is False
            assert _wait_for(lambda: not _port_open(lab_port), timeout=15), (
                "connector listener still bound after stop"
            )
        finally:
            manager.uninstall(UNIT_NAME)
        assert not (manager.unit_dir / UNIT_NAME).exists()
    finally:
        daemon.close()


def test_real_systemd_occupied_port_retries_without_crash(
    tmp_path_factory,
) -> None:
    """QA TASK-6014 under the REAL user systemd manager: a lab port that is
    already occupied must NOT crash the connector process. Before the fix the
    bare OSError killed ``run()`` and systemd ``Restart=on-failure`` produced
    5 restarts then "Start request repeated too quickly" (unit permanently
    failed). With the adapter-boundary normalization the supervisor keeps
    retrying with STATUS (never READY), the unit stays alive (no restart
    throttle), and it recovers + binds + READY once the port frees."""
    _skip_if_unavailable()
    manager = SystemdServiceManager(system=False)
    base = tmp_path_factory.mktemp("hr-conn-occupied")
    daemon = _FakeDaemon()
    blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    blocker.bind(("127.0.0.1", 0))
    blocker.listen(1)
    lab_port = int(blocker.getsockname()[1])
    try:
        token = base / "daemon.token"
        token.write_text("conformance-token", encoding="utf-8")
        token.chmod(0o600)
        policy = base / "policy.json"
        _write_policy_envelope(policy)
        managed_config = str(base / "managed" / "happyranch-connector" / "config.json")
        config = ConnectorConfig(
            tenant_id="tenant-conformance",
            home_id="home-conformance",
            connector_id="connector-conformance",
            daemon_port=daemon.port,
            daemon_token_path=str(token),
            policy_path=str(policy),
            state_path=str(base / "state.json"),
            unit_name=UNIT_NAME,
            system=False,
            managed_dir_root=str(base / "managed"),
            lab=LabProviderConfig(
                bind_host="127.0.0.1", bind_port=lab_port, lab_only=True
            ),
            poll_seconds=1.0,
            exec_start=(
                sys.executable,
                "-m",
                "runtime.remote_access.cli",
                "run",
                "--config",
                managed_config,
                "--lab-only",
            ),
        )
        supervisor = ConnectorSupervisor(config=config, manager=manager)
        manager.uninstall(UNIT_NAME)  # hermetic baseline
        try:
            supervisor.install(enable=False)
            # ``systemctl start`` on Type=notify blocks until READY (90s
            # TimeoutStartSec) — with the port occupied there is no READY, so
            # start WITHOUT blocking and poll the unit state instead.
            subprocess.run(
                ["systemctl", "--user", "start", "--no-block", UNIT_NAME],
                check=True,
                capture_output=True,
                text=True,
                timeout=15,
            )
            # Allow several retry cycles: the process must stay alive and the
            # unit must never reach "failed" (no crash -> no restart-throttle).
            assert _wait_for(
                lambda: manager.status(UNIT_NAME).pid > 0, timeout=15
            ), "connector process never came up"
            time.sleep(5)  # a few poll_seconds retries while the port is busy
            first = manager.status(UNIT_NAME)
            assert first.active_state != "failed", (
                "connector unit FAILED on an occupied port (crash/restart-throttle)"
            )
            assert first.pid > 0
            time.sleep(3)
            second = manager.status(UNIT_NAME)
            assert second.pid == first.pid, (
                "connector process restarted (bare OSError still escaping)"
            )
            assert second.active_state != "failed"

            # Release the conflict: the supervised loop binds on the next poll,
            # emits READY, and the unit reaches active with a live listener.
            blocker.close()
            assert _wait_for(lambda: _port_open(lab_port), timeout=30), (
                "connector never recovered the freed lab port"
            )
            status = manager.status(UNIT_NAME)
            assert status.running is True
            assert status.active_state == "active"
        finally:
            manager.uninstall(UNIT_NAME)
        assert not (manager.unit_dir / UNIT_NAME).exists()
    finally:
        blocker.close()
        daemon.close()


def test_real_upgrade_rollback_roundtrip(manager, notify_helper) -> None:
    """Upgrade replaces the unit over a backup; rollback restores it — both
    against the real user manager with a start-verification loop."""
    v1 = (
        "# conformance v1\n"
        "[Service]\n"
        "Type=simple\n"
        "ExecStart=/usr/bin/sleep 300\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )
    v2 = (
        "# conformance v2\n"
        "[Service]\n"
        "Type=simple\n"
        "ExecStart=/usr/bin/sleep 400\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )
    manager.uninstall(UNIT_NAME)
    try:
        manager.install(v1, UNIT_NAME)
        outcome = manager.upgrade(v2, UNIT_NAME, verify_start=True)
        assert outcome.ok is True
        assert outcome.backup_path is not None
        assert (manager.unit_dir / UNIT_NAME).read_text(encoding="utf-8") == v2

        rollback = manager.rollback(UNIT_NAME)
        assert rollback.ok is True
        assert (manager.unit_dir / UNIT_NAME).read_text(encoding="utf-8") == v1
    finally:
        manager.uninstall(UNIT_NAME)


def test_real_status_of_missing_unit_is_not_running(manager) -> None:
    status = manager.status("happyranch-connector-definitely-missing.service")
    assert status.load_state == "not-found"
    assert status.running is False


def test_real_daemon_reload_and_enable_disable(manager, notify_helper) -> None:
    """enable/disable and daemon-reload against the real user manager."""
    text = _unit_text(notify_helper)
    manager.uninstall(UNIT_NAME)
    try:
        manager.install(text, UNIT_NAME)
        manager.disable(UNIT_NAME)
        manager.enable(UNIT_NAME)
        status = manager.status(UNIT_NAME)
        assert status.unit_file_state in {"enabled", "enabled-runtime"}
    finally:
        manager.uninstall(UNIT_NAME)
