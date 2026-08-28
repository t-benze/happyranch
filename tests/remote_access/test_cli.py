"""Linux connector CLI tests (THR-097 phase unit 3)."""
from __future__ import annotations

import json

import pytest

from runtime.remote_access import cli
from runtime.remote_access.lab_provider import LAB_ONLY_BANNER, LabProviderConfig
from runtime.remote_access.supervisor import ConnectorConfigError

from .test_supervisor import _config


class StubSupervisor:
    """Records commands; returns canned results for CLI assertions."""

    def __init__(self, config) -> None:
        self.config = config
        self.calls: list[str] = []
        self.ready = True
        self.upgrade_ok = True

    def readiness_report(self):
        from runtime.remote_access.readiness import GateResult, ReadinessReport

        self.calls.append("readiness_report")
        gates = {
            name: GateResult(self.ready, f"{name}_ok", f"{name} ok")
            for name in ("daemon_loopback", "credential_permissions", "current_policy", "bind_identity", "trust_state")
        }
        return ReadinessReport(ready=self.ready, gates=gates)

    def diagnose(self):
        self.calls.append("diagnose")
        return {"role": "happyranch-connector", "secrets": "redacted"}

    def status(self):
        self.calls.append("status")
        return type("S", (), {"__dict__": {"active_state": "active"}})()

    def install(self, enable=True):
        self.calls.append(f"install:{enable}")

    def uninstall(self):
        self.calls.append("uninstall")

    def start(self):
        self.calls.append("start")

    def stop(self):
        self.calls.append("stop")

    def restart(self):
        self.calls.append("restart")

    def enable(self):
        self.calls.append("enable")

    def disable(self):
        self.calls.append("disable")

    def upgrade(self, verify_start=True):
        self.calls.append(f"upgrade:{verify_start}")
        from runtime.remote_access.service_manager import UpgradeOutcome

        return UpgradeOutcome(ok=self.upgrade_ok)

    def rollback(self):
        self.calls.append("rollback")
        from runtime.remote_access.service_manager import UpgradeOutcome

        return UpgradeOutcome(ok=True)


@pytest.fixture
def config_file(tmp_path) -> str:
    config = _config(tmp_path)
    path = tmp_path / "config.json"
    config.to_file(path)
    return str(path)


@pytest.fixture(autouse=True)
def stub_supervisor(monkeypatch):
    instances: list[StubSupervisor] = []
    current: list[StubSupervisor | None] = [StubSupervisor(config=None)]
    instances.append(current[0])

    def factory(*args, **kwargs):
        return current[0]

    monkeypatch.setattr(cli, "ConnectorSupervisor", factory)
    return instances


def test_parser_exposes_all_commands() -> None:
    parser = cli.build_parser()
    names = {action.dest for action in parser._actions if hasattr(action, "choices")}
    sub_actions = [a for a in parser._actions if a.dest == "command"][0]
    assert set(sub_actions.choices) == {
        "run",
        "install",
        "uninstall",
        "start",
        "stop",
        "restart",
        "enable",
        "disable",
        "status",
        "readiness",
        "diagnose",
        "upgrade",
        "rollback",
    }


def test_missing_config_returns_1(tmp_path, capsys) -> None:
    code = cli.main(["status", "--config", str(tmp_path / "nope.json")])
    assert code == 1
    assert "config file not found" in capsys.readouterr().err


def test_invalid_config_returns_1(tmp_path, capsys) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text('{"tenant_id": "x"}')
    code = cli.main(["status", "--config", str(bad)])
    assert code == 1
    assert "error" in capsys.readouterr().err.lower()


def test_install_delegates(config_file, stub_supervisor) -> None:
    code = cli.main(["install", "--config", config_file])
    assert code == 0
    assert stub_supervisor[0].calls == ["install:True"]


def test_install_no_enable(config_file, stub_supervisor) -> None:
    code = cli.main(["install", "--no-enable", "--config", config_file])
    assert code == 0
    assert stub_supervisor[0].calls == ["install:False"]


def test_lifecycle_verbs(config_file, stub_supervisor) -> None:
    for verb in ("start", "stop", "restart", "enable", "disable", "uninstall"):
        code = cli.main([verb, "--config", config_file])
        assert code == 0
    assert stub_supervisor[0].calls == [
        "start",
        "stop",
        "restart",
        "enable",
        "disable",
        "uninstall",
    ]


def test_readiness_exit_0_when_ready(config_file, stub_supervisor, capsys) -> None:
    code = cli.main(["readiness", "--config", config_file])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ready"] is True


def test_readiness_exit_1_when_not_ready(config_file, stub_supervisor, capsys) -> None:
    stub_supervisor[0].ready = False
    code = cli.main(["readiness", "--config", config_file])
    assert code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ready"] is False


def test_diagnose_redacted(config_file, stub_supervisor, capsys) -> None:
    code = cli.main(["diagnose", "--config", config_file])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["secrets"] == "redacted"


def test_status_delegates(config_file, stub_supervisor, capsys) -> None:
    code = cli.main(["status", "--config", config_file])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["active_state"] == "active"


def test_upgrade_and_rollback(config_file, stub_supervisor) -> None:
    assert cli.main(["upgrade", "--config", config_file]) == 0
    assert stub_supervisor[0].calls == ["upgrade:True"]
    assert cli.main(["upgrade", "--no-verify", "--config", config_file]) == 0
    assert stub_supervisor[0].calls == ["upgrade:True", "upgrade:False"]
    assert cli.main(["rollback", "--config", config_file]) == 0
    assert stub_supervisor[0].calls[-1] == "rollback"


def test_upgrade_failure_exit_1(config_file, stub_supervisor) -> None:
    stub_supervisor[0].upgrade_ok = False
    assert cli.main(["upgrade", "--config", config_file]) == 1


def test_run_requires_lab_only_when_lab_configured(tmp_path, capsys) -> None:
    config = _config(tmp_path)  # lab configured
    path = tmp_path / "config.json"
    config.to_file(path)
    code = cli.main(["run", "--config", str(path)])
    assert code == 1
    assert "lab-only" in capsys.readouterr().err


def test_run_with_lab_only_flag_banner_printed(tmp_path, monkeypatch, capsys) -> None:
    config = _config(tmp_path)
    path = tmp_path / "config.json"
    config.to_file(path)
    seen: dict = {}

    class RunStub(StubSupervisor):
        def __init__(self, config):
            super().__init__(config)
            self.config.lab = LabProviderConfig(bind_host="127.0.0.1", lab_only=True)

        def run(self, *args, **kwargs):
            seen["ran"] = True
            return 0

    monkeypatch.setattr(cli, "ConnectorSupervisor", RunStub)
    code = cli.main(["run", "--lab-only", "--config", str(path)])
    assert code == 0
    assert seen.get("ran") is True
    assert LAB_ONLY_BANNER in capsys.readouterr().err


def test_run_without_lab_config_ok(tmp_path, monkeypatch, capsys) -> None:
    config = _config(tmp_path, lab=False)
    path = tmp_path / "config.json"
    config.to_file(path)
    seen: dict = {}

    class RunStub(StubSupervisor):
        def run(self, *args, **kwargs):
            seen["ran"] = True
            return 0

    monkeypatch.setattr(cli, "ConnectorSupervisor", RunStub)
    code = cli.main(["run", "--config", str(path)])
    assert code == 0
    assert seen.get("ran") is True
    assert "error" not in capsys.readouterr().err.lower()
