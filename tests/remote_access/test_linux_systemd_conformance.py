"""Real Linux/systemd conformance for the supervised connector (THR-097
phase unit 3).

Hermetic user-scope systemd conformance on a capable host: install a REAL
user unit rendered by ``render_connector_unit`` into the user unit dir,
``daemon-reload``, start it against the real ``Type=notify`` READY contract
(the helper sends ``READY=1`` over sd_notify, exactly like the connector
supervisor), verify it reaches ``active`` with a live PID, restart it,
exercise upgrade/rollback against the real user manager, stop it, uninstall
it, and prove the unit is gone. Every unit is torn down; no live daemon is
touched.

Gated on an operational probe with an explicit skip reason — an unavailable
real capability is NOT silently counted as PASS (skip, not pass), mirroring
``tests/platform/test_task_producer_linux_integration.py``. Runs under
``-m integration`` (unit runs exclude it).
"""
from __future__ import annotations

import shutil
import subprocess
import sys

import pytest

from runtime.remote_access.service_manager import SystemdServiceManager
from runtime.remote_access.systemd_unit import ConnectorUnitSpec, render_connector_unit

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


def test_real_user_systemd_install_start_status_stop_uninstall(
    manager, notify_helper
) -> None:
    """The full hermetic lifecycle against the REAL user systemd manager:
    install a rendered least-privilege user unit, reload, start (the helper
    satisfies the Type=notify READY contract), verify active + live PID,
    restart, stop, uninstall, prove removal."""
    text = _unit_text(notify_helper)
    manager.uninstall(UNIT_NAME)  # hermetic baseline: nothing left behind
    try:
        path = manager.install(text, UNIT_NAME)
        assert path.is_file()
        assert "NoNewPrivileges=yes" in text
        # user-mode render must be startable by the user manager
        assert "CapabilityBoundingSet=" not in text
        assert "PrivateDevices=yes" not in text

        manager.start(UNIT_NAME)
        assert manager.is_active(UNIT_NAME) is True

        status = manager.status(UNIT_NAME)
        assert status.load_state == "loaded"
        assert status.pid > 0
        assert status.running is True

        manager.restart(UNIT_NAME)
        assert manager.is_active(UNIT_NAME) is True

        manager.stop(UNIT_NAME)
        assert manager.is_active(UNIT_NAME) is False
    finally:
        manager.uninstall(UNIT_NAME)
    assert not (manager.unit_dir / UNIT_NAME).exists()


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
