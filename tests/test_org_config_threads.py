from __future__ import annotations

import textwrap

import pytest

from src.orchestrator.org_config import OrgConfig, OrgConfigError


def _load(text: str) -> OrgConfig:
    # Adapt this helper if the loader API differs in the actual module.
    if hasattr(OrgConfig, "load_from_text"):
        return OrgConfig.load_from_text(text)
    if hasattr(OrgConfig, "from_yaml"):
        return OrgConfig.from_yaml(text)
    raise RuntimeError("No known loader on OrgConfig")


def test_threads_defaults_when_missing():
    cfg = _load("")
    assert cfg.threads_enabled is True
    assert cfg.threads_default_turn_cap == 500
    assert cfg.threads_close_out_wait_seconds == 300
    assert cfg.threads_invocation_timeout_seconds is None


def test_threads_loaded_from_yaml():
    text = textwrap.dedent("""
    threads:
      enabled: false
      default_turn_cap: 200
      close_out_wait_seconds: 120
      invocation_timeout_seconds: 900
    """)
    cfg = _load(text)
    assert cfg.threads_enabled is False
    assert cfg.threads_default_turn_cap == 200
    assert cfg.threads_close_out_wait_seconds == 120
    assert cfg.threads_invocation_timeout_seconds == 900


def test_threads_invalid_cap_raises():
    text = "threads:\n  default_turn_cap: -1\n"
    with pytest.raises(OrgConfigError):
        _load(text)
