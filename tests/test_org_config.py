from __future__ import annotations

from pathlib import Path

import pytest

from runtime.orchestrator._paths import OrgPaths
from runtime.orchestrator.org_config import OrgConfigError, load_org_config
from runtime.runtime import RuntimeDir


def _runtime(tmp_path: Path) -> OrgPaths:
    rt = RuntimeDir.init(tmp_path / "rt")
    paths = OrgPaths(root=rt.orgs_dir / "test")
    paths.org_dir.mkdir(parents=True, exist_ok=True)
    return paths


def test_missing_file_returns_empty_config(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    cfg = load_org_config(runtime)
    assert cfg.session_timeout_seconds is None


def test_loads_session_timeout(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    runtime.org_config_path.write_text("session_timeout_seconds: 3600\n")
    cfg = load_org_config(runtime)
    assert cfg.session_timeout_seconds == 3600


def test_explicit_null_inherits(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    runtime.org_config_path.write_text("session_timeout_seconds: null\n")
    cfg = load_org_config(runtime)
    assert cfg.session_timeout_seconds is None


def test_unknown_keys_ignored(tmp_path: Path) -> None:
    """Forward compatibility: future keys should not break older callers."""
    runtime = _runtime(tmp_path)
    runtime.org_config_path.write_text("future_setting: 42\nsession_timeout_seconds: 1200\n")
    cfg = load_org_config(runtime)
    assert cfg.session_timeout_seconds == 1200


@pytest.mark.parametrize(
    "bad_yaml,match",
    [
        ("- just\n- a\n- list\n", "must be a mapping"),
        ("session_timeout_seconds: 0\n", "positive integer"),
        ("session_timeout_seconds: -100\n", "positive integer"),
        ("session_timeout_seconds: '3600'\n", "positive integer"),
        ("session_timeout_seconds: true\n", "positive integer"),
        ("session_timeout_seconds: 1.5\n", "positive integer"),
    ],
)
def test_rejects_invalid(tmp_path: Path, bad_yaml: str, match: str) -> None:
    runtime = _runtime(tmp_path)
    runtime.org_config_path.write_text(bad_yaml)
    with pytest.raises(OrgConfigError, match=match):
        load_org_config(runtime)


def test_rejects_malformed_yaml(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    runtime.org_config_path.write_text("session_timeout_seconds: [oops\n")
    with pytest.raises(OrgConfigError, match="malformed YAML"):
        load_org_config(runtime)


def _write_config(paths: OrgPaths, body: str) -> None:
    paths.org_config_path.parent.mkdir(parents=True, exist_ok=True)
    paths.org_config_path.write_text(body)


def test_feishu_notifications_missing_block_returns_none(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    _write_config(runtime, "session_timeout_seconds: 1800\n")
    from runtime.orchestrator.org_config import load_org_config
    cfg = load_org_config(runtime)
    assert cfg.feishu_notifications is None


def test_feishu_notifications_disabled_returns_none(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    _write_config(runtime, """
feishu_notifications:
  enabled: false
  provider: feishu
  region: feishu
  chat_id: oc_xxx
""")
    from runtime.orchestrator.org_config import load_org_config
    cfg = load_org_config(runtime)
    assert cfg.feishu_notifications is None


def test_feishu_notifications_full_block_parses(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    _write_config(runtime, """
feishu_notifications:
  enabled: true
  provider: feishu
  region: feishu
  chat_id: oc_aaa111
  app_id: cli_x
  app_secret: secret_x
  reply_ttl_hours: 48
""")
    from runtime.orchestrator.org_config import load_org_config, FeishuNotificationsConfig
    cfg = load_org_config(runtime)
    f = cfg.feishu_notifications
    assert f is not None
    assert f.provider == "feishu"
    assert f.region == "feishu"
    assert f.chat_id == "oc_aaa111"
    assert f.app_id == "cli_x"
    assert f.app_secret == "secret_x"
    assert f.reply_ttl_hours == 48


def test_feishu_notifications_default_ttl(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    _write_config(runtime, """
feishu_notifications:
  enabled: true
  provider: feishu
  region: feishu
  chat_id: oc_aaa
  app_id: cli_test
  app_secret: secret_test
""")
    from runtime.orchestrator.org_config import load_org_config
    cfg = load_org_config(runtime)
    assert cfg.feishu_notifications.reply_ttl_hours == 72


def test_feishu_notifications_invalid_provider_raises(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    _write_config(runtime, """
feishu_notifications:
  enabled: true
  provider: slack
  region: feishu
  chat_id: oc_xxx
  app_id: cli_test
  app_secret: secret_test
""")
    from runtime.orchestrator.org_config import load_org_config
    try:
        load_org_config(runtime)
    except OrgConfigError as exc:
        assert "provider" in str(exc)
    else:
        raise AssertionError("expected OrgConfigError")


def test_feishu_notifications_invalid_region_raises(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    _write_config(runtime, """
feishu_notifications:
  enabled: true
  provider: feishu
  region: us
  chat_id: oc_xxx
  app_id: cli_test
  app_secret: secret_test
""")
    from runtime.orchestrator.org_config import load_org_config
    try:
        load_org_config(runtime)
    except OrgConfigError as exc:
        assert "region" in str(exc)
    else:
        raise AssertionError("expected OrgConfigError")


def test_feishu_notifications_missing_chat_id_raises(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    _write_config(runtime, """
feishu_notifications:
  enabled: true
  provider: feishu
  region: feishu
  app_id: cli_test
  app_secret: secret_test
""")
    from runtime.orchestrator.org_config import load_org_config
    try:
        load_org_config(runtime)
    except OrgConfigError as exc:
        assert "chat_id" in str(exc)
    else:
        raise AssertionError("expected OrgConfigError")


def test_feishu_notifications_ttl_out_of_range_raises(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    _write_config(runtime, """
feishu_notifications:
  enabled: true
  provider: feishu
  region: feishu
  chat_id: oc_x
  app_id: cli_test
  app_secret: secret_test
  reply_ttl_hours: 9999
""")
    from runtime.orchestrator.org_config import load_org_config
    try:
        load_org_config(runtime)
    except OrgConfigError as exc:
        assert "reply_ttl_hours" in str(exc)
    else:
        raise AssertionError("expected OrgConfigError")


def test_feishu_notifications_missing_app_id_raises(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    _write_config(runtime, """
feishu_notifications:
  enabled: true
  provider: feishu
  region: feishu
  chat_id: oc_xxx
  app_secret: s
""")
    from runtime.orchestrator.org_config import load_org_config
    try:
        load_org_config(runtime)
    except OrgConfigError as exc:
        assert "app_id" in str(exc)
    else:
        raise AssertionError("expected OrgConfigError")


def test_feishu_notifications_missing_app_secret_raises(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    _write_config(runtime, """
feishu_notifications:
  enabled: true
  provider: feishu
  region: feishu
  chat_id: oc_xxx
  app_id: cli_x
""")
    from runtime.orchestrator.org_config import load_org_config
    try:
        load_org_config(runtime)
    except OrgConfigError as exc:
        assert "app_secret" in str(exc)
    else:
        raise AssertionError("expected OrgConfigError")
