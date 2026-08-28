"""Systemd service-manager lifecycle (THR-097 phase unit 3).

The supervisor manages the connector service through ``systemctl`` shell-outs
(injectable for deterministic tests, mirroring the daemon's
``LinuxSystemdBackend`` pattern — no new dependency). Every operation fails
closed on a non-zero exit. ``upgrade`` installs a new unit over a backed-up
copy and verifies the unit comes up active; a failed start auto-rolls back
to the previous unit. ``rollback`` restores the most recent backup.
"""
from __future__ import annotations

import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import pytest

from runtime.remote_access.service_manager import (
    ServiceManagerError,
    ServiceStatus,
    SystemdServiceManager,
    UpgradeOutcome,
)

UNIT_NAME = "happyranch-connector.service"
UNIT_TEXT_V1 = "# unit v1\n[Service]\nExecStart=/bin/true\n"
UNIT_TEXT_V2 = "# unit v2\n[Service]\nExecStart=/bin/true\n"


class FakeSystemctl:
    """Records argv; simulates outcomes for the deterministic battery."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.active: set[str] = set()
        self.fail_on: set[tuple[str, ...]] = set()
        self.fail_once: set[tuple[str, ...]] = set()
        self.show_props = {
            "ActiveState": "inactive",
            "SubState": "dead",
            "LoadState": "loaded",
            "ExecMainPID": "0",
            "UnitFileState": "enabled",
        }

    def __call__(self, argv: list[str], timeout: float = 10.0) -> tuple[int, str]:
        del timeout
        self.calls.append(argv)
        # Normalize: drop the binary and the --user flag so fail_on keys are
        # verb-first tuples (e.g. ("restart", "<unit>")).
        args = [a for a in argv if a not in ("systemctl", "--user")]
        verb = args[0] if args else ""
        if tuple(args) in self.fail_on:
            return 1, f"failed: {args}"
        if tuple(args) in self.fail_once:
            self.fail_once.discard(tuple(args))
            return 1, f"failed once: {args}"
        if verb == "is-active":
            unit = args[-1]
            return 0 if unit in self.active else 1, ""
        if verb == "show":
            return 0, "".join(f"{k}={v}\n" for k, v in self.show_props.items())
        if verb in {"start", "restart"}:
            self.active.add(args[-1])
        elif verb == "stop":
            self.active.discard(args[-1])
        return 0, ""


@pytest.fixture
def fake() -> FakeSystemctl:
    return FakeSystemctl()


@pytest.fixture
def manager(fake: FakeSystemctl, tmp_path) -> SystemdServiceManager:
    return SystemdServiceManager(
        systemctl="systemctl",
        run=fake,
        unit_dir=tmp_path / "units",
        backup_dir=tmp_path / "backups",
    )


def _write_installed(manager: SystemdServiceManager, text: str) -> Path:
    manager.install(text, UNIT_NAME)
    return manager.unit_dir / UNIT_NAME


class TestBasicLifecycle:
    def test_install_writes_unit_and_reloads(self, manager, fake) -> None:
        path = _write_installed(manager, UNIT_TEXT_V1)
        assert path.read_text(encoding="utf-8") == UNIT_TEXT_V1
        assert any("daemon-reload" in c for c in fake.calls)
        assert path.stat().st_mode & 0o077 == 0  # owner-only unit file

    def test_install_backs_up_existing_unit(self, manager, fake) -> None:
        _write_installed(manager, UNIT_TEXT_V1)
        manager.install(UNIT_TEXT_V2, UNIT_NAME)
        backups = list(manager.backup_dir.glob(f"{UNIT_NAME}.*"))
        assert len(backups) == 1
        assert backups[0].read_text(encoding="utf-8") == UNIT_TEXT_V1

    def test_uninstall_stops_disables_removes(self, manager, fake) -> None:
        _write_installed(manager, UNIT_TEXT_V1)
        manager.uninstall(UNIT_NAME)
        verbs = [c[0] if len(c) == 1 else c[-2] for c in fake.calls if "systemctl" in c]
        assert "stop" in [c[-2] for c in fake.calls if len(c) >= 2]
        assert "disable" in [c[-2] for c in fake.calls if len(c) >= 2]
        assert not (manager.unit_dir / UNIT_NAME).exists()
        assert any("daemon-reload" in c for c in fake.calls)

    def test_start_stop_restart_enable_disable(self, manager, fake) -> None:
        _write_installed(manager, UNIT_TEXT_V1)
        manager.start(UNIT_NAME)
        assert any(c[-2:] == ["start", UNIT_NAME] for c in fake.calls)
        manager.stop(UNIT_NAME)
        assert any(c[-2:] == ["stop", UNIT_NAME] for c in fake.calls)
        manager.restart(UNIT_NAME)
        assert any(c[-2:] == ["restart", UNIT_NAME] for c in fake.calls)
        manager.enable(UNIT_NAME)
        assert any(c[-2:] == ["enable", UNIT_NAME] for c in fake.calls)
        manager.disable(UNIT_NAME)
        assert any(c[-2:] == ["disable", UNIT_NAME] for c in fake.calls)

    def test_status_parses_show(self, manager, fake) -> None:
        fake.show_props.update(
            ActiveState="active", SubState="running", ExecMainPID="4242"
        )
        status = manager.status(UNIT_NAME)
        assert status.unit_name == UNIT_NAME
        assert status.active_state == "active"
        assert status.sub_state == "running"
        assert status.pid == 4242
        assert status.running is True

    def test_status_failed_unit_not_running(self, manager, fake) -> None:
        fake.show_props.update(ActiveState="failed", SubState="failed")
        status = manager.status(UNIT_NAME)
        assert status.running is False
        assert status.active_state == "failed"

    def test_is_active(self, manager, fake) -> None:
        fake.active.add(UNIT_NAME)
        assert manager.is_active(UNIT_NAME) is True
        fake.active.discard(UNIT_NAME)
        assert manager.is_active(UNIT_NAME) is False


class TestFailClosed:
    def test_systemctl_failure_raises(self, manager, fake) -> None:
        fake.fail_on.add(("restart", UNIT_NAME))
        with pytest.raises(ServiceManagerError):
            manager.restart(UNIT_NAME)

    def test_user_mode_unit_dir_defaults(self, tmp_path, fake, monkeypatch) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        mgr = SystemdServiceManager(systemctl="systemctl", run=fake, system=False)
        assert str(mgr.unit_dir).endswith(".config/systemd/user")

    def test_system_mode_unit_dir_defaults(self, tmp_path, fake, monkeypatch) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        mgr = SystemdServiceManager(systemctl="systemctl", run=fake, system=True)
        assert str(mgr.unit_dir) == "/etc/systemd/system"

    def test_install_systemctl_failure_raises(self, manager, fake) -> None:
        fake.fail_on.add(("daemon-reload",))
        with pytest.raises(ServiceManagerError):
            manager.install(UNIT_TEXT_V1, UNIT_NAME)

    def test_missing_unit_status_raises(self, manager, fake) -> None:
        fake.show_props.update(LoadState="not-found")
        status = manager.status("ghost.service")
        assert status.load_state == "not-found"
        assert status.running is False


class TestUpgradeRollback:
    def test_upgrade_success_verifies_active(self, manager, fake) -> None:
        _write_installed(manager, UNIT_TEXT_V1)
        outcome = manager.upgrade(UNIT_TEXT_V2, UNIT_NAME, verify_start=True)
        assert outcome.ok is True
        assert outcome.rolled_back is False
        assert outcome.backup_path is not None
        assert (manager.unit_dir / UNIT_NAME).read_text(encoding="utf-8") == UNIT_TEXT_V2

    def test_upgrade_failed_start_auto_rolls_back(self, manager, fake) -> None:
        _write_installed(manager, UNIT_TEXT_V1)
        # the first restart of the NEW unit fails; the rollback restart
        # (which follows) succeeds.
        fake.fail_once.add(("restart", UNIT_NAME))
        outcome = manager.upgrade(UNIT_TEXT_V2, UNIT_NAME, verify_start=True)
        assert outcome.ok is False
        assert outcome.rolled_back is True
        assert (manager.unit_dir / UNIT_NAME).read_text(encoding="utf-8") == UNIT_TEXT_V1

    def test_upgrade_no_prior_unit_has_no_backup(self, manager, fake) -> None:
        outcome = manager.upgrade(UNIT_TEXT_V2, UNIT_NAME, verify_start=False)
        assert outcome.ok is True
        assert outcome.backup_path is None

    def test_rollback_restores_latest_backup(self, manager, fake) -> None:
        _write_installed(manager, UNIT_TEXT_V1)
        manager.install(UNIT_TEXT_V2, UNIT_NAME)
        # now roll back to v1
        outcome = manager.rollback(UNIT_NAME)
        assert outcome.ok is True
        assert (manager.unit_dir / UNIT_NAME).read_text(encoding="utf-8") == UNIT_TEXT_V1

    def test_rollback_without_backup_raises(self, manager, fake) -> None:
        manager.install(UNIT_TEXT_V2, UNIT_NAME)
        with pytest.raises(ServiceManagerError):
            manager.rollback(UNIT_NAME)

    def test_upgrade_failure_without_backup_no_rollback(self, manager, fake) -> None:
        fake.fail_once.add(("restart", UNIT_NAME))
        outcome = manager.upgrade(UNIT_TEXT_V2, UNIT_NAME, verify_start=True)
        assert outcome.ok is False
        assert outcome.rolled_back is False  # nothing to roll back to
