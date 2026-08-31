from __future__ import annotations

import threading
from pathlib import Path

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


def test_durable_audit_precedes_every_authoritative_replace(monkeypatch, tmp_path):
    monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path))
    path = tmp_path / "config.yaml"
    original = b"other: retained\nqueue_workers: 4\nhost_global_session_cap: 11\n"
    path.write_bytes(original)
    before = snapshot(path, Settings(), capability_reason="healthy")
    events = []
    real_replace = __import__("os").replace

    def checked_replace(source, target):
        assert events and events[0]["outcome"] == "validated_write_authorized"
        real_replace(source, target)

    monkeypatch.setattr("runtime.daemon.capacity_config.os.replace", checked_replace)
    save(path, Settings(), expected_revision=before["revision"], queue_workers=6,
         host_global_session_cap=13, rationale="ordering", confirm_environment_shadow=False,
         audit=events.append, capability_reason="healthy")
    assert yaml.safe_load(path.read_text())["queue_workers"] == 6


def test_replace_fault_after_durable_audit_leaves_original_authoritative(monkeypatch, tmp_path):
    monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path))
    path = tmp_path / "config.yaml"
    original = b"other: retained\nqueue_workers: 4\nhost_global_session_cap: 11\n"
    path.write_bytes(original)
    before = snapshot(path, Settings(), capability_reason="healthy")
    events = []

    def fail_replace(_source, _target):
        assert events and events[0]["outcome"] == "validated_write_authorized"
        raise OSError("replace fault")

    monkeypatch.setattr("runtime.daemon.capacity_config.os.replace", fail_replace)
    with pytest.raises(CapacityConfigError) as raised:
        save(path, Settings(), expected_revision=before["revision"], queue_workers=6,
             host_global_session_cap=13, rationale="fault", confirm_environment_shadow=False,
             audit=events.append, capability_reason="healthy")
    assert raised.value.code == "config_write_failed"
    assert path.read_bytes() == original
    assert not list(tmp_path.glob(".*.tmp"))


@pytest.mark.parametrize(
    "fault",
    ["directory_open", "directory_fsync", "directory_close", "read_back", "yaml_parse", "revision", "values"],
)
def test_post_replace_fault_reports_publication_uncertain_and_forces_reconciliation(
    monkeypatch, tmp_path, fault,
):
    monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path))
    path = tmp_path / "config.yaml"
    path.write_text("other: retained\nqueue_workers: 4\nhost_global_session_cap: 11\n")
    running = Settings(queue_workers=4, host_global_session_cap=11)
    before = snapshot(path, running, capability_reason="healthy")
    events = []
    published = False
    real_replace = __import__("os").replace

    def tracked_replace(source, target):
        nonlocal published
        real_replace(source, target)
        published = True

    monkeypatch.setattr("runtime.daemon.capacity_config.os.replace", tracked_replace)
    if fault == "directory_open":
        real_open = __import__("os").open
        monkeypatch.setattr("runtime.daemon.capacity_config.os.open", lambda *a, **k: (_ for _ in ()).throw(OSError("open fault")) if published else real_open(*a, **k))
    elif fault == "directory_fsync":
        real_fsync = __import__("os").fsync
        monkeypatch.setattr("runtime.daemon.capacity_config.os.fsync", lambda fd: (_ for _ in ()).throw(OSError("fsync fault")) if published else real_fsync(fd))
    elif fault == "directory_close":
        monkeypatch.setattr("runtime.daemon.capacity_config.os.close", lambda _fd: (_ for _ in ()).throw(OSError("close fault")))
    elif fault == "read_back":
        real_read = Path.read_bytes
        failed = False
        def fail_one_read(self):
            nonlocal failed
            if published and self == path and not failed:
                failed = True
                raise OSError("read fault")
            return real_read(self)
        monkeypatch.setattr(Path, "read_bytes", fail_one_read)
    elif fault == "yaml_parse":
        real_load = yaml.safe_load
        failed = False
        def fail_one_parse(value):
            nonlocal failed
            if published and not failed:
                failed = True
                raise yaml.YAMLError("parse fault")
            return real_load(value)
        monkeypatch.setattr(yaml, "safe_load", fail_one_parse)
    elif fault == "revision":
        monkeypatch.setattr("runtime.daemon.capacity_config._verify_published_revision", lambda *_: (_ for _ in ()).throw(OSError("revision fault")))
    else:
        monkeypatch.setattr("runtime.daemon.capacity_config._verify_published_values", lambda *_: (_ for _ in ()).throw(OSError("value fault")))

    with pytest.raises(CapacityConfigError) as raised:
        save(path, running, expected_revision=before["revision"], queue_workers=6,
             host_global_session_cap=13, rationale="fault", confirm_environment_shadow=False,
             audit=events.append, capability_reason="healthy")

    assert raised.value.code == "config_publication_uncertain"
    assert "reload" in str(raised.value).lower()
    assert yaml.safe_load(path.read_bytes()) == {
        "other": "retained", "queue_workers": 6, "host_global_session_cap": 13,
    }
    assert events == [{
        "prior": {"queue_workers": 4, "host_global_session_cap": 11},
        "new": {"queue_workers": 6, "host_global_session_cap": 13},
        "revision_before": before["revision"],
        "revision_after": snapshot(path, running, capability_reason="healthy")["revision"],
        "rationale": "fault", "outcome": "validated_write_authorized",
        "provenance": "server-observed config.yaml and startup snapshot",
        "environment_shadowed": [],
    }]
    assert not list(tmp_path.glob(".*.tmp"))
    latest = snapshot(path, running, capability_reason="healthy")
    assert latest["persisted_yaml"] == {"queue_workers": 6, "host_global_session_cap": 13}
    with pytest.raises(CapacityConfigError) as stale:
        save(path, running, expected_revision=before["revision"], queue_workers=7,
             host_global_session_cap=14, rationale="blind retry", confirm_environment_shadow=False,
             audit=events.append, capability_reason="healthy")
    assert stale.value.code == "stale_revision"


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
