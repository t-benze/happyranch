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


def test_executor_profiles_multi_org_collision_prevented(tmp_path: Path) -> None:
    """Multi-org regression: two orgs with the same custom profile name
    but different command/argv semantics. The second org load must fail
    loudly (ExecutorProfileCollisionError) and the first org's profile
    must remain unchanged. One org must not be able to silently alter
    another org's profile semantics."""
    from runtime.orchestrator.executor_registry import (
        ExecutorProfileCollisionError,
        get_registry,
        reset_registry,
    )
    from runtime.orchestrator.org_config import OrgConfig
    reset_registry()

    # Org alpha defines executor_profiles.shared with echo
    cfg_alpha = OrgConfig.load_from_text("""
executor_profiles:
  shared:
    command: echo
    adapter: pi
    argv_template:
      - echo
      - "{prompt}"
""")
    assert cfg_alpha.executor_profiles
    get_registry().register_custom_from_config(cfg_alpha.executor_profiles)
    assert get_registry().is_registered("shared")

    # Org beta defines executor_profiles.shared with printf — different command
    cfg_beta = OrgConfig.load_from_text("""
executor_profiles:
  shared:
    command: printf
    adapter: pi
    argv_template:
      - printf
      - "{prompt}"
""")
    assert cfg_beta.executor_profiles

    # The second registration must fail because 'shared' already means echo
    with pytest.raises(ExecutorProfileCollisionError, match="shared"):
        get_registry().register_custom_from_config(cfg_beta.executor_profiles)

    # Alpha's profile is unchanged — NOT overwritten by beta
    p = get_registry().get_profile("shared")
    assert p is not None
    assert p.argv_template == ["echo", "{prompt}"]
    assert p.command == "echo"


def test_executor_profiles_non_conflicting_custom_registers(tmp_path: Path) -> None:
    """A config-declared fake custom executor registers/validates through
    the production path without conflicting with built-ins."""
    from runtime.orchestrator.executor_registry import get_registry, reset_registry
    from runtime.orchestrator.org_config import OrgConfig
    reset_registry()

    cfg = OrgConfig.load_from_text("""
executor_profiles:
  testcli:
    command: echo
    adapter: pi
    argv_template:
      - echo
      - "--input"
      - "{prompt}"
""")
    assert cfg.executor_profiles
    get_registry().register_custom_from_config(cfg.executor_profiles)

    assert get_registry().is_registered("testcli")
    p = get_registry().get_profile("testcli")
    assert p is not None
    assert p.kind == "custom"
    assert p.adapter_id == "pi"
    assert p.command == "echo"


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


# ══════════════════════════════════════════════════════════════════════════════
# write_skill_eligibility_entry unit tests (THR-092 Phase 3a)
# ══════════════════════════════════════════════════════════════════════════════

import yaml

from runtime.orchestrator.org_config import (
    write_skill_eligibility_entry,
)


def _write_config_yaml(paths: OrgPaths, content: str) -> None:
    paths.org_config_path.parent.mkdir(parents=True, exist_ok=True)
    paths.org_config_path.write_text(content.rstrip() + "\n")


def test_skills_not_in_org_writable_keys() -> None:
    """'skills' is NOT in _ORG_WRITABLE_KEYS — scoped writer is the sole path."""
    from runtime.orchestrator.org_config import _ORG_WRITABLE_KEYS
    assert "skills" not in _ORG_WRITABLE_KEYS
    assert "executor_profiles" not in _ORG_WRITABLE_KEYS  # sanity: mirror pattern


def test_write_eligibility_allow_roundtrips(tmp_path: Path) -> None:
    """Allow writes the rule, and it survives round-trip through _build_org_config."""
    paths = _runtime(tmp_path)
    _write_config_yaml(paths, "timezone: Asia/Shanghai\n")

    write_skill_eligibility_entry(
        paths, agent="dev_agent", skill_id="hr:my-skill", action="allow",
    )

    raw = yaml.safe_load(paths.org_config_path.read_text())
    allow_list = raw["skills"]["agents"]["dev_agent"]["allow"]
    assert "hr:my-skill" in allow_list
    assert "timezone" in raw  # other keys survive


def test_write_eligibility_remove(tmp_path: Path) -> None:
    """Remove retracts the allow rule."""
    paths = _runtime(tmp_path)
    _write_config_yaml(paths, "timezone: Asia/Shanghai\n")

    write_skill_eligibility_entry(
        paths, agent="dev_agent", skill_id="hr:my-skill", action="allow",
    )
    write_skill_eligibility_entry(
        paths, agent="dev_agent", skill_id="hr:my-skill", action="remove",
    )

    raw = yaml.safe_load(paths.org_config_path.read_text())
    agents = raw.get("skills", {}).get("agents", {})
    dev_rules = agents.get("dev_agent", {})
    # After remove, allow list is empty → key should be absent
    assert "hr:my-skill" not in dev_rules.get("allow", [])


def test_write_eligibility_preserves_sibling_agents(tmp_path: Path) -> None:
    """Deep-merge preserves sibling agents' existing eligibility."""
    paths = _runtime(tmp_path)
    _write_config_yaml(paths, """
timezone: Asia/Shanghai
skills:
  agents:
    qa_engineer:
      allow:
      - hr:existing-skill
""")

    write_skill_eligibility_entry(
        paths, agent="dev_agent", skill_id="hr:my-skill", action="allow",
    )

    raw = yaml.safe_load(paths.org_config_path.read_text())
    agents = raw["skills"]["agents"]
    assert "hr:existing-skill" in agents["qa_engineer"]["allow"]
    assert "hr:my-skill" in agents["dev_agent"]["allow"]
    assert "timezone" in raw


def test_write_eligibility_preserves_sibling_skills(tmp_path: Path) -> None:
    """Deep-merge preserves sibling skills in the same agent's allow list."""
    paths = _runtime(tmp_path)
    _write_config_yaml(paths, """
timezone: Asia/Shanghai
skills:
  agents:
    dev_agent:
      allow:
      - hr:skill-a
""")

    write_skill_eligibility_entry(
        paths, agent="dev_agent", skill_id="hr:skill-b", action="allow",
    )

    raw = yaml.safe_load(paths.org_config_path.read_text())
    dev_allow = raw["skills"]["agents"]["dev_agent"]["allow"]
    assert "hr:skill-a" in dev_allow
    assert "hr:skill-b" in dev_allow


def test_write_eligibility_idempotent(tmp_path: Path) -> None:
    """Allowing the same skill twice does not create duplicates."""
    paths = _runtime(tmp_path)
    _write_config_yaml(paths, "timezone: Asia/Shanghai\n")

    write_skill_eligibility_entry(
        paths, agent="dev_agent", skill_id="hr:my-skill", action="allow",
    )
    write_skill_eligibility_entry(
        paths, agent="dev_agent", skill_id="hr:my-skill", action="allow",
    )

    raw = yaml.safe_load(paths.org_config_path.read_text())
    dev_allow = raw["skills"]["agents"]["dev_agent"]["allow"]
    assert dev_allow.count("hr:my-skill") == 1


def test_write_eligibility_missing_config_creates(tmp_path: Path) -> None:
    """Writer creates config from scratch when config.yaml doesn't exist."""
    paths = _runtime(tmp_path)
    # Ensure no existing config
    if paths.org_config_path.exists():
        paths.org_config_path.unlink()

    write_skill_eligibility_entry(
        paths, agent="dev_agent", skill_id="hr:my-skill", action="allow",
    )

    raw = yaml.safe_load(paths.org_config_path.read_text())
    assert "hr:my-skill" in raw["skills"]["agents"]["dev_agent"]["allow"]


def test_write_eligibility_invalid_action_raises(tmp_path: Path) -> None:
    """Invalid action raises ValueError."""
    paths = _runtime(tmp_path)
    _write_config_yaml(paths, "timezone: Asia/Shanghai\n")

    with pytest.raises(ValueError, match="action must be"):
        write_skill_eligibility_entry(
            paths, agent="dev_agent", skill_id="hr:my-skill", action="invalid",
        )


def test_write_eligibility_atomic_no_partial_write(tmp_path: Path) -> None:
    """Writer is atomic — invalid config does not persist."""
    paths = _runtime(tmp_path)
    _write_config_yaml(paths, "timezone: Asia/Shanghai\n")
    original = paths.org_config_path.read_text()

    # Write invalid config (session_timeout_seconds: -1 via manipulating raw)
    # Since _build_org_config validates, an invalid raw would raise.
    # Actually, our writer validates via _build_org_config which validates
    # known fields; the skills block is passthrough. So invalid data in
    # known fields would block. Let's test by manipulating timezone.
    # The writer validates — if _build_org_config raises, the original
    # file should be unchanged.
    # We simulate by using a bad value for a validated field after write
    # Actually we need to craft a case where _build_org_config would reject.
    # The easiest: write a config with a bad session_timeout_seconds.
    _write_config_yaml(paths, "session_timeout_seconds: -1\n")
    bad = paths.org_config_path.read_text()

    with pytest.raises(OrgConfigError):
        write_skill_eligibility_entry(
            paths, agent="dev_agent", skill_id="hr:my-skill", action="allow",
        )

    # Original (bad) content should be unchanged
    assert paths.org_config_path.read_text() == bad


def test_write_eligibility_other_top_level_keys_survive(tmp_path: Path) -> None:
    """Non-skills top-level keys survive the write."""
    paths = _runtime(tmp_path)
    _write_config_yaml(paths, "timezone: Asia/Shanghai\n" + "dreaming:\n  enabled: true\n")

    write_skill_eligibility_entry(
        paths, agent="dev_agent", skill_id="hr:my-skill", action="allow",
    )

    raw = yaml.safe_load(paths.org_config_path.read_text())
    assert raw["timezone"] == "Asia/Shanghai"
    assert raw["dreaming"]["enabled"] is True
    assert "hr:my-skill" in raw["skills"]["agents"]["dev_agent"]["allow"]
