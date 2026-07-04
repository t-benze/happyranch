"""Commit the fan-out permission-boundary invariant.

A fanned-out child's Bash allow-rules MUST be byte-identical to the SAME agent's
single-delegation baseline on BOTH surfaces (settings.json permissions.allow AND
the ``--allowedTools`` CLI flag list), AND a child MUST NOT be widened to its
fan-out parent manager's rules (no permission inheritance from the parent).

SEAM: ``allow_rules_for_agent(paths, agent_name, *, cli)`` takes ONLY
``(paths, agent_name, cli)`` — NO delegation-context parameter; the fan-out
spawn path sets ``assigned_agent`` to the CHILD's own name, so every call-path
derives rules solely from the child's ``agents/<name>.md`` frontmatter.
A future regression that widened a child's rules (e.g. unioning parent+child,
or adding a delegation-context knob) will turn one of these assertions RED.
"""
from __future__ import annotations

from pathlib import Path

from runtime.orchestrator._paths import OrgPaths
from runtime.orchestrator.workspace_adapters import (
    allow_rules_for_agent,
    bash_allow_prefixes_for_agent,
    build_settings_json,
)
from runtime.runtime import RuntimeDir


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _make_paths(tmp_path: Path) -> OrgPaths:
    rt = RuntimeDir.init(tmp_path / "rt")
    return OrgPaths(root=rt.orgs_dir / "test")


def _write_agent(paths: OrgPaths, name: str, allow_rules: list[str]) -> None:
    paths.agents_dir.mkdir(parents=True, exist_ok=True)
    rules_block = (
        "allow_rules: []\n" if not allow_rules
        else "allow_rules:\n" + "\n".join(f"  - {r!r}" for r in allow_rules) + "\n"
    )
    (paths.agents_dir / f"{name}.md").write_text(
        "---\n"
        f"name: {name}\nteam: engineering\nrole: worker\nexecutor: claude\n"
        f"{rules_block}"
        "repos: {}\nenrolled_by: null\nenrolled_at_task: null\nenrolled_at: null\n"
        "---\n\nbody\n"
    )


# ---------------------------------------------------------------------------
# invariant (a): wrk's surfaces equal baseline + wrk's own frontmatter rules,
#                regardless of whether dispatched as a single delegation or
#                as a fan-out child of mgr (the two paths are structurally
#                identical — neither accepts a parent/context param).
# ---------------------------------------------------------------------------

def test_child_rules_are_baseline_plus_own_rules_settings_form(
    tmp_path: Path,
) -> None:
    """settings.json surface: wrk gets ONLY baseline + its own rules."""
    paths = _make_paths(tmp_path)
    _write_agent(paths, "mgr", ["gh pr merge --squash", "docker"])
    _write_agent(paths, "wrk", ["pytest"])

    rules = allow_rules_for_agent(paths, "wrk", cli=False)

    assert rules == ["Bash(happyranch:*)", "Bash(pytest:*)"]


def test_child_rules_are_baseline_plus_own_rules_cli_form(
    tmp_path: Path,
) -> None:
    """--allowedTools surface: wrk gets ONLY baseline + its own rules."""
    paths = _make_paths(tmp_path)
    _write_agent(paths, "mgr", ["gh pr merge --squash", "docker"])
    _write_agent(paths, "wrk", ["pytest"])

    rules = allow_rules_for_agent(paths, "wrk", cli=True)

    assert rules == ["Bash(happyranch *)", "Bash(pytest *)"]


def test_child_bash_prefixes_are_baseline_plus_own_rules(
    tmp_path: Path,
) -> None:
    """opencode.json surface: wrk gets ONLY baseline + its own raw prefixes."""
    paths = _make_paths(tmp_path)
    _write_agent(paths, "mgr", ["gh pr merge --squash", "docker"])
    _write_agent(paths, "wrk", ["pytest"])

    prefixes = bash_allow_prefixes_for_agent(paths, "wrk")

    assert prefixes == ["happyranch", "pytest"]


# ---------------------------------------------------------------------------
# invariant (b): wrk's surfaces do NOT contain any of mgr's broad rules
#                (no widening / permission inheritance from the fan-out parent).
# ---------------------------------------------------------------------------


def test_child_does_not_inherit_parent_rules_settings_form(
    tmp_path: Path,
) -> None:
    paths = _make_paths(tmp_path)
    _write_agent(paths, "mgr", ["gh pr merge --squash", "docker"])
    _write_agent(paths, "wrk", ["pytest"])

    rules = allow_rules_for_agent(paths, "wrk", cli=False)

    # mgr's broad rules must NOT leak into wrk's permission list
    assert "Bash(gh pr merge --squash:*)" not in rules
    assert "Bash(docker:*)" not in rules
    # manager itself should still have its own rules (sanity check: the
    # generator works correctly for the parent too — no under-generation)
    mgr_rules = allow_rules_for_agent(paths, "mgr", cli=False)
    assert "Bash(gh pr merge --squash:*)" in mgr_rules
    assert "Bash(docker:*)" in mgr_rules


def test_child_does_not_inherit_parent_rules_cli_form(
    tmp_path: Path,
) -> None:
    paths = _make_paths(tmp_path)
    _write_agent(paths, "mgr", ["gh pr merge --squash", "docker"])
    _write_agent(paths, "wrk", ["pytest"])

    rules = allow_rules_for_agent(paths, "wrk", cli=True)

    # mgr's broad rules must NOT leak into wrk's --allowedTools list
    assert "Bash(gh pr merge --squash *)" not in rules
    assert "Bash(docker *)" not in rules
    # sanity: mgr still has its own
    mgr_rules = allow_rules_for_agent(paths, "mgr", cli=True)
    assert "Bash(gh pr merge --squash *)" in mgr_rules
    assert "Bash(docker *)" in mgr_rules


def test_child_does_not_inherit_parent_prefixes(
    tmp_path: Path,
) -> None:
    paths = _make_paths(tmp_path)
    _write_agent(paths, "mgr", ["gh pr merge --squash", "docker"])
    _write_agent(paths, "wrk", ["pytest"])

    prefixes = bash_allow_prefixes_for_agent(paths, "wrk")

    assert "gh pr merge --squash" not in prefixes
    assert "docker" not in prefixes


# ---------------------------------------------------------------------------
# invariant (c): byte-identical assertions — the build_settings_json surface
#                must match the direct allow_rules_for_agent surface exactly.
# ---------------------------------------------------------------------------


def test_build_settings_json_uses_child_agent_name_only(
    tmp_path: Path,
) -> None:
    """build_settings_json for wrk produces the exact same permissions.allow
    as allow_rules_for_agent(paths, 'wrk', cli=False) — proving the spawn seam
    (agent_name=str, no delegation context) is the sole input."""
    paths = _make_paths(tmp_path)
    _write_agent(paths, "mgr", ["gh pr merge --squash", "docker"])
    _write_agent(paths, "wrk", ["pytest"])

    settings = build_settings_json(paths, [], agent_name="wrk")

    expected = ["Bash(happyranch:*)", "Bash(pytest:*)"]
    assert settings["permissions"]["allow"] == expected


def test_build_settings_json_does_not_leak_parent_rules(
    tmp_path: Path,
) -> None:
    """build_settings_json for wrk must not include mgr's rules."""
    paths = _make_paths(tmp_path)
    _write_agent(paths, "mgr", ["gh pr merge --squash", "docker"])
    _write_agent(paths, "wrk", ["pytest"])

    settings = build_settings_json(paths, [], agent_name="wrk")

    rules = settings["permissions"]["allow"]
    assert "Bash(gh pr merge --squash:*)" not in rules
    assert "Bash(docker:*)" not in rules


# ---------------------------------------------------------------------------
# Structural invariant: allow_rules_for_agent signature has NO delegation-
# context parameter — this is the architectural guarantee.
# ---------------------------------------------------------------------------

def test_allow_rules_for_agent_accepts_only_agent_name_and_cli_flag(
    tmp_path: Path,
) -> None:
    """The function takes ONLY (paths, agent_name, *, cli) — inspect the
    parameter names to confirm there is no parent_agent, delegation_context,
    or any other knob that could widen a child's permissions."""
    import inspect

    sig = inspect.signature(allow_rules_for_agent)
    param_names = set(sig.parameters.keys())

    # Must accept a paths, agent_name, and cli flag — nothing else.
    assert param_names == {"paths", "agent_name", "cli"}

    # The agent_name parameter has NO default — it must always be provided.
    agent_param = sig.parameters["agent_name"]
    assert agent_param.default is inspect.Parameter.empty
