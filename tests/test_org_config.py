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


def test_feishu_notifications_block_is_stripped_on_load(tmp_path: Path) -> None:
    """Guardrail #3: an org config with a legacy feishu_notifications block
    must load without error — the key is tolerated and stripped."""
    runtime = _runtime(tmp_path)
    _write_config(runtime, """
feishu_notifications:
  enabled: true
  provider: feishu
  region: feishu
  chat_id: oc_xxx
  app_id: cli_x
  app_secret: secret_x
""")
    cfg = load_org_config(runtime)
    # Config loaded without error; feishu_notifications block is ignored.
    assert cfg.session_timeout_seconds is None


# ── THR-032 Phase 2: memory_digest_budget config parsing ──

def test_memory_digest_budget_default_1500(tmp_path: Path) -> None:
    """Default is 1500 when not present in config."""
    runtime = _runtime(tmp_path)
    cfg = load_org_config(runtime)
    assert cfg.memory_digest_budget == 1500


def test_memory_digest_budget_explicit_0_disables(tmp_path: Path) -> None:
    """Explicit 0 disables the digest."""
    runtime = _runtime(tmp_path)
    _write_config(runtime, "memory_digest_budget: 0\n")
    cfg = load_org_config(runtime)
    assert cfg.memory_digest_budget == 0


def test_memory_digest_budget_positive_value(tmp_path: Path) -> None:
    """Explicit positive values are accepted."""
    runtime = _runtime(tmp_path)
    _write_config(runtime, "memory_digest_budget: 2000\n")
    cfg = load_org_config(runtime)
    assert cfg.memory_digest_budget == 2000


# ── THR-052: executor_profiles config parsing ──

def test_executor_profiles_default_empty(tmp_path: Path) -> None:
    """executor_profiles is an empty dict by default."""
    runtime = _runtime(tmp_path)
    cfg = load_org_config(runtime)
    assert cfg.executor_profiles == {}


def test_executor_profiles_parsed_from_config(tmp_path: Path) -> None:
    """An executor_profiles block is parsed and retained in OrgConfig."""
    runtime = _runtime(tmp_path)
    _write_config(runtime, """
executor_profiles:
  openclaw:
    command: openclaw
    adapter: pi
    argv_template:
      - openclaw
      - agent
      - "--local"
      - "--json"
      - "--message"
      - "{prompt}"
      - "--timeout"
      - "{timeout_seconds}"
  customcli:
    command: mycli
    adapter: claude
    argv_template:
      - mycli
      - "--prompt"
      - "{prompt}"
""")
    cfg = load_org_config(runtime)
    assert "openclaw" in cfg.executor_profiles
    assert "customcli" in cfg.executor_profiles
    assert cfg.executor_profiles["openclaw"]["command"] == "openclaw"
    assert cfg.executor_profiles["openclaw"]["adapter"] == "pi"
    assert len(cfg.executor_profiles["openclaw"]["argv_template"]) == 8


def test_executor_profiles_rejects_non_mapping(tmp_path: Path) -> None:
    """executor_profiles must be a mapping, not a list or scalar."""
    runtime = _runtime(tmp_path)
    _write_config(runtime, "executor_profiles: [1, 2, 3]\n")
    with pytest.raises(OrgConfigError, match="executor_profiles must be a mapping"):
        load_org_config(runtime)


def test_executor_profiles_rejects_empty_key(tmp_path: Path) -> None:
    """executor_profiles keys must be non-empty strings."""
    runtime = _runtime(tmp_path)
    _write_config(runtime, """
executor_profiles:
  '':
    command: foo
    argv_template: [foo, --prompt, '{prompt}']
""")
    with pytest.raises(OrgConfigError, match="executor_profiles keys must be non-empty"):
        load_org_config(runtime)


def test_executor_profiles_rejects_non_dict_value(tmp_path: Path) -> None:
    """Each executor_profiles entry must be a mapping."""
    runtime = _runtime(tmp_path)
    _write_config(runtime, """
executor_profiles:
  foo: bar
""")
    with pytest.raises(OrgConfigError, match="executor_profiles.foo must be a mapping"):
        load_org_config(runtime)


def test_executor_profiles_production_shape_probe(tmp_path: Path) -> None:
    """Production-shape probe: load config, register profiles, verify
    registry accepts the custom profile."""
    from runtime.orchestrator.org_config import OrgConfig
    from runtime.orchestrator.executor_registry import get_registry, reset_registry
    reset_registry()
    cfg = OrgConfig.load_from_text("""
executor_profiles:
  openclaw:
    command: echo
    adapter: pi
    argv_template:
      - echo
      - "{prompt}"
""")
    assert cfg.executor_profiles
    # Simulate the production registration path (what OrgState.load does).
    get_registry().register_custom_from_config(cfg.executor_profiles)
    assert get_registry().is_registered("openclaw")
    assert get_registry().is_registered("OPENCLAW")  # case-insensitive
    profile = get_registry().get_profile("openclaw")
    assert profile is not None
    assert profile.kind == "custom"
    assert profile.adapter_id == "pi"


@pytest.mark.parametrize(
    "bad_value,err_fragment",
    [
        ("memory_digest_budget: -1\n", ">= 0"),
        ("memory_digest_budget: true\n", "must be an integer"),
        ("memory_digest_budget: false\n", "must be an integer"),
        ("memory_digest_budget: '1500'\n", "must be an integer"),
        ("memory_digest_budget: 1.5\n", "must be an integer"),
    ],
)
def test_memory_digest_budget_rejects_invalid(
    tmp_path: Path, bad_value: str, err_fragment: str,
) -> None:
    """Negative values, bools, strings, and floats are rejected."""
    runtime = _runtime(tmp_path)
    _write_config(runtime, bad_value)
    with pytest.raises(OrgConfigError, match=err_fragment):
        load_org_config(runtime)
