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


def test_org_state_enabled_with_secrets_attaches_notifier(
    org_root, test_settings,
):
    _write_cfg(org_root, textwrap.dedent("""
        feishu_notifications:
          enabled: true
          provider: feishu
          region: feishu
          chat_id: oc_x
          app_id: cli_x
          app_secret: secret_x
    """))
    state = OrgState.load(slug="test", root=org_root, settings=test_settings)
    assert state.notifier is not None
    # Phase 2 listener will read these from OrgState.
    assert state.feishu_app_id == "cli_x"
    assert state.feishu_app_secret == "secret_x"
    assert state.feishu_chat_id == "oc_x"
    assert state.feishu_domain is not None  # CN domain


def test_lifespan_creates_listener_when_feishu_configured(
    org_root, test_settings, monkeypatch,
):
    """Smoke: the lifespan helper builds a FeishuEventListener for an org
    that has full Feishu config."""
    import asyncio

    from src.daemon.app import _start_feishu_listeners
    from src.daemon.feishu_listener import FeishuEventListener
    from src.daemon.state import DaemonState

    _write_cfg(org_root, textwrap.dedent("""
        feishu_notifications:
          enabled: true
          provider: feishu
          region: feishu
          chat_id: oc_x
          app_id: cli_x
          app_secret: secret_x
    """))

    org = OrgState.load(slug="test", root=org_root, settings=test_settings)
    state = DaemonState(runtime=None, settings=test_settings, orgs={"test": org})
    # Don't actually start the WS thread in the test; stub `start` so we just
    # verify construction.
    monkeypatch.setattr(FeishuEventListener, "start", lambda self: None)

    loop = asyncio.new_event_loop()
    try:
        _start_feishu_listeners(state, loop)
    finally:
        loop.close()
    assert isinstance(org.feishu_listener, FeishuEventListener)


@pytest.mark.asyncio
async def test_add_org_starts_feishu_listener_when_configured(
    tmp_path, test_settings, monkeypatch,
):
    """DaemonState.add_org must start a FeishuEventListener for orgs created
    after daemon startup when their config is complete."""
    from src.daemon.feishu_listener import FeishuEventListener
    from src.daemon.state import DaemonState
    from src.runtime import RuntimeDir

    rt = RuntimeDir.init(tmp_path / "rt")
    org_root = rt.orgs_dir / "newbie"
    org_root.mkdir(parents=True)
    (org_root / "org").mkdir()
    (org_root / "org" / "config.yaml").write_text(textwrap.dedent("""
        feishu_notifications:
          enabled: true
          provider: feishu
          region: feishu
          chat_id: oc_x
          app_id: cli_x
          app_secret: secret_x
    """))

    # Don't actually start the WS thread.
    started: list[FeishuEventListener] = []
    monkeypatch.setattr(
        FeishuEventListener, "start",
        lambda self: started.append(self),
    )

    state = DaemonState.from_runtime(rt, test_settings)
    org = await state.add_org("newbie")

    assert isinstance(org.feishu_listener, FeishuEventListener)
    assert org.feishu_listener in started
