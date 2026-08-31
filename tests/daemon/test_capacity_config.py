from __future__ import annotations

import threading

import pytest
import yaml

from runtime.config import Settings
from runtime.daemon.capacity_config import CapacityConfigError, save, snapshot


def test_no_file_and_empty_mapping_are_safe(monkeypatch, tmp_path):
    monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path))
    running = Settings(queue_workers=6, host_global_session_cap=13)
    missing = snapshot(tmp_path / "config.yaml", running, capability_reason="healthy")
    assert missing["persisted_yaml"] == {"queue_workers": None, "host_global_session_cap": None}
    (tmp_path / "config.yaml").write_text("{}\n")
    empty = snapshot(tmp_path / "config.yaml", running, capability_reason="healthy")
    assert empty["persisted_yaml"] == missing["persisted_yaml"]
    assert empty["revision"] != missing["revision"]


@pytest.mark.parametrize("text,code", [("[1, 2]\n", "config_not_mapping"), ("{bad", "config_parse_failed")])
def test_malformed_or_non_mapping_fail_closed(monkeypatch, tmp_path, text, code):
    monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path))
    running = Settings()
    (tmp_path / "config.yaml").write_text(text)
    with pytest.raises(CapacityConfigError, match="configuration") as raised:
        snapshot(tmp_path / "config.yaml", running, capability_reason="healthy")
    assert raised.value.code == code


def test_save_preserves_unrelated_keys_and_running_snapshot(monkeypatch, tmp_path):
    monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path))
    path = tmp_path / "config.yaml"
    path.write_text("permission_mode: auto\nqueue_workers: 4\nhost_global_session_cap: 11\n")
    running = Settings(queue_workers=4, host_global_session_cap=11)
    before = snapshot(path, running, capability_reason="healthy")
    events = []
    result = save(path, running, expected_revision=before["revision"], queue_workers=6,
                  host_global_session_cap=13, rationale="measured receipts",
                  confirm_environment_shadow=False, audit=events.append,
                  capability_reason="healthy")
    assert yaml.safe_load(path.read_text())["permission_mode"] == "auto"
    assert result["running_at_daemon_start"] == {"queue_workers": 4, "host_global_session_cap": 11}
    assert result["restart_pending"] is True
    assert events[0]["prior"] == {"queue_workers": 4, "host_global_session_cap": 11}


def test_audit_failure_restores_authoritative_bytes(monkeypatch, tmp_path):
    monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path))
    path = tmp_path / "config.yaml"
    original = b"other: retained\nqueue_workers: 4\nhost_global_session_cap: 11\n"
    path.write_bytes(original)
    before = snapshot(path, Settings(), capability_reason="healthy")
    with pytest.raises(CapacityConfigError) as raised:
        save(path, Settings(), expected_revision=before["revision"], queue_workers=6,
             host_global_session_cap=13, rationale="test", confirm_environment_shadow=False,
             audit=lambda _: (_ for _ in ()).throw(OSError("db unavailable")),
             capability_reason="healthy")
    assert raised.value.code == "audit_failed"
    assert path.read_bytes() == original
    assert not list(tmp_path.glob(".*.tmp"))


def test_stale_and_environment_shadow_reject_without_mutation(monkeypatch, tmp_path):
    monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path))
    path = tmp_path / "config.yaml"
    path.write_text("queue_workers: 4\nhost_global_session_cap: 11\n")
    original = path.read_bytes()
    with pytest.raises(CapacityConfigError) as stale:
        save(path, Settings(), expected_revision="sha256:stale", queue_workers=6,
             host_global_session_cap=13, rationale="test", confirm_environment_shadow=False,
             audit=lambda _: None, capability_reason="healthy")
    assert stale.value.code == "stale_revision"
    monkeypatch.setenv("HAPPYRANCH_QUEUE_WORKERS", "8")
    current = snapshot(path, Settings(), capability_reason="healthy")
    with pytest.raises(CapacityConfigError) as shadow:
        save(path, Settings(), expected_revision=current["revision"], queue_workers=6,
             host_global_session_cap=13, rationale="test", confirm_environment_shadow=False,
             audit=lambda _: None, capability_reason="healthy")
    assert shadow.value.code == "environment_confirmation_required"
    assert path.read_bytes() == original


def test_process_wide_lock_prevents_lost_update(monkeypatch, tmp_path):
    monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path))
    path = tmp_path / "config.yaml"
    path.write_text("queue_workers: 4\nhost_global_session_cap: 11\n")
    revision = snapshot(path, Settings(), capability_reason="healthy")["revision"]
    outcomes = []
    barrier = threading.Barrier(3)
    def writer(value):
        barrier.wait()
        try:
            save(path, Settings(), expected_revision=revision, queue_workers=value,
                 host_global_session_cap=13, rationale="race", confirm_environment_shadow=False,
                 audit=lambda _: None, capability_reason="healthy")
            outcomes.append("saved")
        except CapacityConfigError as exc:
            outcomes.append(exc.code)
    threads = [threading.Thread(target=writer, args=(value,)) for value in (6, 7)]
    for thread in threads: thread.start()
    barrier.wait()
    for thread in threads: thread.join()
    assert sorted(outcomes) == ["saved", "stale_revision"]
