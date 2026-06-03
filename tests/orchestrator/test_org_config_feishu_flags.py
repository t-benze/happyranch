from __future__ import annotations

from pathlib import Path

import pytest

from runtime.orchestrator._paths import OrgPaths
from runtime.orchestrator.org_config import OrgConfigError, load_org_config
from runtime.runtime import RuntimeDir


def _make_paths(tmp_path: Path) -> OrgPaths:
    rt = RuntimeDir.init(tmp_path / "rt")
    paths = OrgPaths(root=rt.orgs_dir / "test")
    paths.org_dir.mkdir(parents=True, exist_ok=True)
    return paths


def _write_config(paths: OrgPaths, body: str) -> None:
    paths.org_config_path.parent.mkdir(parents=True, exist_ok=True)
    paths.org_config_path.write_text(body)


def _minimal_feishu_block(extra: str = "") -> str:
    return f"""feishu_notifications:
  enabled: true
  provider: feishu
  region: feishu
  chat_id: oc_xyz
  app_id: cli_abc
  app_secret: shhh
{extra}"""


def test_defaults_when_flags_absent(tmp_path: Path) -> None:
    paths = _make_paths(tmp_path)
    _write_config(paths, _minimal_feishu_block())
    cfg = load_org_config(paths)
    assert cfg.feishu_notifications is not None
    assert cfg.feishu_notifications.notify_on_failure is False
    assert cfg.feishu_notifications.allow_dispatch is False


def test_flags_set_true(tmp_path: Path) -> None:
    paths = _make_paths(tmp_path)
    _write_config(
        paths,
        _minimal_feishu_block("  notify_on_failure: true\n  allow_dispatch: true\n"),
    )
    cfg = load_org_config(paths)
    assert cfg.feishu_notifications.notify_on_failure is True
    assert cfg.feishu_notifications.allow_dispatch is True


def test_notify_on_failure_must_be_bool(tmp_path: Path) -> None:
    paths = _make_paths(tmp_path)
    _write_config(paths, _minimal_feishu_block('  notify_on_failure: "yes"\n'))
    with pytest.raises(OrgConfigError, match="notify_on_failure"):
        load_org_config(paths)


def test_allow_dispatch_must_be_bool(tmp_path: Path) -> None:
    paths = _make_paths(tmp_path)
    _write_config(paths, _minimal_feishu_block("  allow_dispatch: 1\n"))
    with pytest.raises(OrgConfigError, match="allow_dispatch"):
        load_org_config(paths)
