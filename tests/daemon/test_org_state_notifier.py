from __future__ import annotations

import textwrap

import pytest

from src.daemon.org_state import OrgState


def _write_cfg(root, body: str) -> None:
    cfg = root / "org" / "config.yaml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(body)


@pytest.fixture
def org_root(tmp_path):
    root = tmp_path / "orgs" / "test"
    root.mkdir(parents=True)
    (root / "org").mkdir()
    return root


def test_org_state_no_feishu_block_means_no_notifier(org_root, test_settings):
    state = OrgState.load(slug="test", root=org_root, settings=test_settings)
    assert state.notifier is None


def test_org_state_disabled_means_no_notifier(org_root, test_settings):
    _write_cfg(org_root, textwrap.dedent("""
        feishu_notifications:
          enabled: false
          provider: feishu
          region: feishu
          chat_id: oc_x
    """))
    state = OrgState.load(slug="test", root=org_root, settings=test_settings)
    assert state.notifier is None


def test_org_state_enabled_no_secrets_skips(
    org_root, test_settings, monkeypatch,
):
    monkeypatch.delenv("OPC_FEISHU_APP_ID", raising=False)
    monkeypatch.delenv("OPC_FEISHU_APP_SECRET", raising=False)
    monkeypatch.delenv("OPC_FEISHU_APP_ID__TEST", raising=False)
    monkeypatch.delenv("OPC_FEISHU_APP_SECRET__TEST", raising=False)
    _write_cfg(org_root, textwrap.dedent("""
        feishu_notifications:
          enabled: true
          provider: feishu
          region: feishu
          chat_id: oc_x
    """))
    state = OrgState.load(slug="test", root=org_root, settings=test_settings)
    assert state.notifier is None


def test_org_state_enabled_with_secrets_attaches_notifier(
    org_root, test_settings, monkeypatch,
):
    monkeypatch.setenv("OPC_FEISHU_APP_ID", "cli_x")
    monkeypatch.setenv("OPC_FEISHU_APP_SECRET", "secret_x")
    _write_cfg(org_root, textwrap.dedent("""
        feishu_notifications:
          enabled: true
          provider: feishu
          region: feishu
          chat_id: oc_x
    """))
    state = OrgState.load(slug="test", root=org_root, settings=test_settings)
    assert state.notifier is not None
    # Phase 2 listener will read these from OrgState.
    assert state.feishu_app_id == "cli_x"
    assert state.feishu_app_secret == "secret_x"
    assert state.feishu_chat_id == "oc_x"
    assert state.feishu_domain is not None  # CN domain
