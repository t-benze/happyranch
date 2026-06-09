"""Settings sourcing: env vars > <daemon-home>/config.yaml > code defaults.

The daemon-home location is resolved per-instantiation (honoring
HAPPYRANCH_DAEMON_HOME), so these tests redirect it to a tmp dir.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from runtime.config import Settings


def test_missing_config_yaml_uses_defaults(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path))
    monkeypatch.delenv("HAPPYRANCH_QUEUE_WORKERS", raising=False)
    # No config.yaml present — must not raise, falls through to code defaults.
    assert Settings().queue_workers == 3
    assert Settings().assistant_probe_timeout_seconds == 15.0


def test_config_yaml_overrides_defaults(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path))
    monkeypatch.delenv("HAPPYRANCH_QUEUE_WORKERS", raising=False)
    (tmp_path / "config.yaml").write_text(
        "queue_workers: 6\n"
        "session_timeout_seconds: 900\n"
        "assistant_probe_timeout_seconds: 45\n"
    )
    s = Settings()
    assert s.queue_workers == 6
    assert s.session_timeout_seconds == 900
    assert s.assistant_probe_timeout_seconds == 45


def test_env_var_overrides_config_yaml(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path))
    (tmp_path / "config.yaml").write_text("queue_workers: 6\n")
    monkeypatch.setenv("HAPPYRANCH_QUEUE_WORKERS", "11")
    assert Settings().queue_workers == 11


def test_queue_workers_must_be_positive(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path))
    monkeypatch.delenv("HAPPYRANCH_QUEUE_WORKERS", raising=False)
    (tmp_path / "config.yaml").write_text("queue_workers: 0\n")
    with pytest.raises(ValueError, match="greater than 0"):
        Settings()
