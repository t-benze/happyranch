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


# ============================================================================
# Layer 1: SPAWN-IDENTITY LOCK (HIGH #1)
#
# Drives the REAL production spawn function _spawn_fanout_children and
# asserts the load-bearing wiring: assigned_agent = child_info['agent'],
# never the manager_agent. This exercises the code path at
# run_step.py:2386 that the invariant depends on.
# ============================================================================


def test_spawn_identity_child_assigned_own_agent_not_parent(
    tmp_path: Path,
) -> None:
    """_spawn_fanout_children sets assigned_agent=child's OWN agent_name
    (never the manager_agent), and the child's allow_rules are the child's
    own narrow rules — not widened to the parent's broad rules.

    This exercises the REAL production path at run_step.py:2386 — the
    load-bearing wiring the permission-boundary invariant depends on.
    A regression that assigned the parent/manager name would go RED here.
    """
    from runtime.config import Settings
    from runtime.infrastructure.database import Database
    from runtime.models import TaskRecord
    from runtime.orchestrator.orchestrator import Orchestrator
    from runtime.orchestrator.run_step import _spawn_fanout_children
    from runtime.orchestrator.teams import TeamsRegistry

    # ---- setup: tmp org with mgr (broad rules) + wrk (narrow rule) ----
    paths = _make_paths(tmp_path)
    _write_agent(paths, "mgr", ["gh pr merge --squash", "docker"])
    _write_agent(paths, "wrk", ["pytest"])

    # Team config: mgr is manager, wrk is worker on engineering team
    paths.teams_config_path.parent.mkdir(parents=True, exist_ok=True)
    paths.teams_config_path.write_text(
        "teams:\n"
        "  engineering:\n"
        "    manager: mgr\n"
        "    workers: [wrk]\n"
    )

    # Workspace dirs (Orchestrator's cross-team validation needs them)
    (paths.workspaces_dir / "mgr").mkdir(parents=True)
    (paths.workspaces_dir / "wrk").mkdir(parents=True)

    # ---- spawn fan-out children via the REAL production function ----
    db = Database(paths.db_path)
    db.insert_task(TaskRecord(
        id="T-FANOUT-PERM", brief="permission-inheritance probe",
        assigned_agent="mgr",
    ))
    orch = Orchestrator(
        db=db, settings=Settings(),
        paths=paths, slug="test",
        teams=TeamsRegistry.load(paths.root),
    )
    # Wire a minimal queue so _spawn_fanout_children can enqueue the child.
    # (The spawn checks `if orch._queue is not None` before putting.)
    from collections import deque
    class _SlugQueue:
        def __init__(self) -> None:
            self._items: deque = deque()
        def put_nowait(self, slug: str, task_id: str) -> None:
            self._items.append((slug, task_id))
        def get_nowait(self) -> tuple[str, str]:
            return self._items.popleft()
    orch._queue = _SlugQueue()  # type: ignore[attr-defined]

    children_payload: list[dict] = [
        {"agent": "wrk", "prompt": "run tests"},
    ]
    _spawn_fanout_children(
        orch, db.get_task("T-FANOUT-PERM"), "T-FANOUT-PERM", 1,
        children=children_payload, width=1,
        manager_agent="mgr", step_audit_id=1,
    )

    # ---- verify spawned child identity ----
    child_ids = db.get_children("T-FANOUT-PERM")
    assert len(child_ids) == 1, "expected exactly one fan-out child"
    child = db.get_task(child_ids[0])
    assert child is not None

    # LOAD-BEARING: assigned_agent is the CHILD's own name, never the manager
    assert child.assigned_agent == "wrk", (
        f"assigned_agent must be 'wrk' (the child's own name), "
        f"got {child.assigned_agent!r}"
    )
    assert child.assigned_agent != "mgr", (
        "assigned_agent must NOT be the parent/manager 'mgr' — "
        "a regression assigning the parent name would go RED here"
    )

    # ---- from the spawned child identity, verify rules are child's own ----
    # settings.json surface (cli=False)
    wrk_settings = allow_rules_for_agent(paths, child.assigned_agent, cli=False)
    assert wrk_settings == ["Bash(happyranch:*)", "Bash(pytest:*)"]

    # --allowedTools surface (cli=True)
    wrk_cli = allow_rules_for_agent(paths, child.assigned_agent, cli=True)
    assert wrk_cli == ["Bash(happyranch *)", "Bash(pytest *)"]

    # Child rules do NOT contain the manager's broad rules
    for surface in (wrk_settings, wrk_cli):
        assert not any("gh pr merge" in r for r in surface), (
            "child must not inherit manager's 'gh pr merge' rule"
        )
        assert not any("docker" in r for r in surface), (
            "child must not inherit manager's 'docker' rule"
        )

    # Sanity: manager DOES have its own broad rules
    mgr_settings = allow_rules_for_agent(paths, "mgr", cli=False)
    assert "Bash(gh pr merge --squash:*)" in mgr_settings
    assert "Bash(docker:*)" in mgr_settings


# ============================================================================
# Layer 2: EXECUTOR --allowedTools + settings.json SURFACE LOCK
#          (HIGH #2 + MEDIUM)
#
# Asserts the REAL ClaudeExecutor command construction derives
# --allowedTools from workspace.name (the child's agent name) and is
# byte-identical to the single-delegation baseline. Also asserts the
# build_settings_json surface matches identically.
# ============================================================================


def test_claude_executor_allowedtools_uses_child_workspace_name_only(
    tmp_path: Path,
) -> None:
    """ClaudeExecutor --allowedTools is derived from workspace.name
    (the child's agent name), NOT the parent/manager's name.

    This exercises the REAL production code path at executors.py:591:
        allowed = " ".join(allow_rules_for_agent(self._paths, workspace.name, cli=True))

    A regression that widened --allowedTools (e.g. unioning parent+child
    rules, or adding a delegation-context knob) would go RED here.
    """
    from unittest.mock import MagicMock, patch
    from runtime.config import Settings
    from runtime.orchestrator.executors import ClaudeExecutor

    paths = _make_paths(tmp_path)
    _write_agent(paths, "mgr", ["gh pr merge --squash", "docker"])
    _write_agent(paths, "wrk", ["pytest"])

    # workspace named after the child/worker agent
    workspace = tmp_path / "wrk"
    workspace.mkdir()

    with patch("runtime.orchestrator.executors.subprocess") as mock_subprocess:
        mock_subprocess.Popen.return_value = MagicMock()
        mock_subprocess.Popen.return_value.returncode = 0
        mock_subprocess.Popen.return_value.pid = 9999
        mock_subprocess.Popen.return_value.communicate.return_value = (
            "{}", ""
        )

        executor = ClaudeExecutor(
            claude_cli_path="claude", permission_mode="auto",
            settings=Settings(), paths=paths,
        )
        executor.run(
            workspace=workspace, prompt="test prompt", timeout_seconds=30,
        )

        # Capture the command that was constructed
        cmd = mock_subprocess.Popen.call_args[0][0]
        allowed = cmd[cmd.index("--allowedTools") + 1]

    # Baseline for wrk (single-delegation): happyranch + pytest
    expected = "Bash(happyranch *) Bash(pytest *)"
    assert allowed == expected, (
        f"--allowedTools must be byte-identical to single-delegation baseline\n"
        f"  expected: {expected!r}\n"
        f"  got:      {allowed!r}"
    )

    # Must NOT contain the manager's broad rules
    assert "gh pr merge" not in allowed, (
        "--allowedTools must not contain manager's 'gh pr merge' rule"
    )
    assert "docker" not in allowed, (
        "--allowedTools must not contain manager's 'docker' rule"
    )

    # Sanity: if the same executor targets a workspace named after the
    # manager, it DOES get the manager's broad rules.
    mgr_workspace = tmp_path / "mgr"
    mgr_workspace.mkdir()

    with patch("runtime.orchestrator.executors.subprocess") as mgr_mock:
        mgr_mock.Popen.return_value = MagicMock()
        mgr_mock.Popen.return_value.returncode = 0
        mgr_mock.Popen.return_value.pid = 9999
        mgr_mock.Popen.return_value.communicate.return_value = ("{}", "")

        mgr_executor = ClaudeExecutor(
            claude_cli_path="claude", permission_mode="auto",
            settings=Settings(), paths=paths,
        )
        mgr_executor.run(
            workspace=mgr_workspace, prompt="test", timeout_seconds=30,
        )

        mgr_cmd = mgr_mock.Popen.call_args[0][0]
        mgr_allowed = mgr_cmd[mgr_cmd.index("--allowedTools") + 1]

    assert "gh pr merge" in mgr_allowed, (
        "sanity: manager workspace must get manager's gh pr merge rule"
    )
    assert "docker" in mgr_allowed, (
        "sanity: manager workspace must get manager's docker rule"
    )


def test_build_settings_json_byte_identical_to_child_baseline(
    tmp_path: Path,
) -> None:
    """build_settings_json permissions.allow for wrk is byte-identical
    to the single-delegation baseline — no widening from parent.

    This exercises the REAL production code path at:
        workspace_adapters.py:413 — allow_rules_for_agent(paths, agent_name, cli=False)

    A regression that widened settings.json permissions (e.g. unioning
    parent+child rules) would go RED here.
    """
    paths = _make_paths(tmp_path)
    _write_agent(paths, "mgr", ["gh pr merge --squash", "docker"])
    _write_agent(paths, "wrk", ["pytest"])

    settings = build_settings_json(paths, [], agent_name="wrk")
    rules = settings["permissions"]["allow"]

    # Byte-identical to single-delegation baseline
    expected = ["Bash(happyranch:*)", "Bash(pytest:*)"]
    assert rules == expected, (
        f"permissions.allow must be byte-identical to single-delegation baseline\n"
        f"  expected: {expected!r}\n"
        f"  got:      {rules!r}"
    )

    # Must NOT contain manager's broad rules
    assert "Bash(gh pr merge --squash:*)" not in rules, (
        "permissions.allow must not contain manager's 'gh pr merge' rule"
    )
    assert "Bash(docker:*)" not in rules, (
        "permissions.allow must not contain manager's 'docker' rule"
    )

    # Sanity: mgr's settings.json DOES include its own broad rules
    mgr_settings = build_settings_json(paths, [], agent_name="mgr")
    mgr_rules = mgr_settings["permissions"]["allow"]
    assert "Bash(gh pr merge --squash:*)" in mgr_rules
    assert "Bash(docker:*)" in mgr_rules


# ============================================================================
# Layer 3: FAN-OUT CHILD LAUNCH PATH LOCK (reviewer round-2 HIGH)
#
# Proves that the fan-out child LAUNCH PATH (run_step -> _run_agent ->
# executor.run) passes the CHILD's own workspace, not the parent/manager's.
# The untested link from round 1: assigned_agent -> workspace.name passed
# to executor.
#
# Seam: run_step.py:218 agent=task.assigned_agent ->
#       run_step.py:224 orch._run_agent(task_id, agent, prompt) ->
#       orchestrator.py:493 workspace = self._paths.workspaces_dir / agent_name ->
#       executors.py:589 allow_rules_for_agent(self._paths, workspace.name, cli=True)
# ============================================================================


def test_run_agent_launches_child_with_own_workspace_not_parent(
    tmp_path: Path,
) -> None:
    """The fan-out child LAUNCH PATH runs the child under its OWN workspace.

    Monkeypatches orch._build_executor to return a spy that records the
    ``workspace`` kwarg handed to ``executor.run()``, then drives the
    spawned fan-out child through ``orch._run_agent(child_id, child_agent, prompt)``.

    The assertion goes RED if run_step:218/224 or orchestrator:493 ever
    selects a non-child workspace (e.g. widening to the parent/manager's
    workspace), even if _spawn_fanout_children still assigns the correct
    ``assigned_agent``.
    """
    from unittest.mock import patch
    from runtime.config import Settings
    from runtime.infrastructure.database import Database
    from runtime.models import TaskRecord
    from runtime.orchestrator.executors import ExecutorResult
    from runtime.orchestrator.orchestrator import Orchestrator
    from runtime.orchestrator.run_step import _spawn_fanout_children
    from runtime.orchestrator.teams import TeamsRegistry

    # ---- setup: tmp org with mgr (broad rules) + wrk (narrow rule) ----
    paths = _make_paths(tmp_path)
    _write_agent(paths, "mgr", ["gh pr merge --squash", "docker"])
    _write_agent(paths, "wrk", ["pytest"])

    # Team config
    paths.teams_config_path.parent.mkdir(parents=True, exist_ok=True)
    paths.teams_config_path.write_text(
        "teams:\n"
        "  engineering:\n"
        "    manager: mgr\n"
        "    workers: [wrk]\n"
    )

    # Workspace dirs WITH readiness markers for _run_agent.
    # Claude's readiness marker is .claude/skills/start-task/SKILL.md
    for agent_name in ("mgr", "wrk"):
        ws = paths.workspaces_dir / agent_name
        ws.mkdir(parents=True, exist_ok=True)
        skill = ws / ".claude" / "skills" / "start-task"
        skill.mkdir(parents=True, exist_ok=True)
        (skill / "SKILL.md").write_text(f"# start-task: {agent_name}\n")

    # ---- spawn fan-out children ----
    db = Database(paths.db_path)
    db.insert_task(TaskRecord(
        id="T-LAUNCH-PATH-PROBE", brief="launch-path permission probe",
        assigned_agent="mgr",
    ))
    orch = Orchestrator(
        db=db, settings=Settings(),
        paths=paths, slug="test",
        teams=TeamsRegistry.load(paths.root),
    )

    # Wire a minimal queue so _spawn_fanout_children can enqueue the child
    from collections import deque
    class _SlugQueue:
        def __init__(self) -> None:
            self._items: deque = deque()
        def put_nowait(self, slug: str, task_id: str) -> None:
            self._items.append((slug, task_id))
        def get_nowait(self) -> tuple[str, str]:
            return self._items.popleft()
    orch._queue = _SlugQueue()  # type: ignore[attr-defined]

    children_payload: list[dict] = [
        {"agent": "wrk", "prompt": "run tests"},
    ]
    _spawn_fanout_children(
        orch, db.get_task("T-LAUNCH-PATH-PROBE"), "T-LAUNCH-PATH-PROBE", 1,
        children=children_payload, width=1,
        manager_agent="mgr", step_audit_id=1,
    )

    # Get the spawned child
    child_ids = db.get_children("T-LAUNCH-PATH-PROBE")
    assert len(child_ids) == 1, "expected exactly one fan-out child"
    child = db.get_task(child_ids[0])
    assert child is not None
    assert child.assigned_agent == "wrk", (
        f"assigned_agent must be 'wrk', got {child.assigned_agent!r}"
    )

    # ---- spy: record the workspace handed to executor.run() ----
    captured_workspace: list[Path] = []

    class SpyExecutor:
        def run(self, **kwargs: object) -> ExecutorResult:
            ws: Path = kwargs["workspace"]  # type: ignore[assignment]
            captured_workspace.append(ws)
            return ExecutorResult(
                success=True,
                duration_seconds=1,
                session_id="sess-spy",
            )

    spy = SpyExecutor()

    # Fix session_id for determinism
    orch._build_session_id = lambda: "sess-spy"  # type: ignore[method-assign]

    with patch.object(orch, "_build_executor", return_value=spy):
        orch._run_agent(child.id, child.assigned_agent, "test prompt")

    # ---- assertions ----
    assert len(captured_workspace) == 1, (
        "executor.run should have been called exactly once"
    )
    captured = captured_workspace[0]

    # ASSERTION 1: workspace.name == 'wrk' (the child's own name).
    # RED-FIRST PROOF performed: temporarily asserted == 'mgr' and confirmed
    # the test went RED, proving it genuinely catches a widening regression.
    assert captured.name == "wrk", (
        f"executor.run workspace.name must be 'wrk' (child's own name), "
        f"got {captured.name!r}. A regression that runs the child under "
        f"the parent/manager workspace would go RED here."
    )

    # ASSERTION 2: allow_rules from the captured workspace are byte-identical
    # to the single-delegation 'wrk' baseline
    rules_cli = allow_rules_for_agent(paths, captured.name, cli=True)
    expected_cli = ["Bash(happyranch *)", "Bash(pytest *)"]
    assert rules_cli == expected_cli, (
        f"--allowedTools must be byte-identical to single-delegation baseline\n"
        f"  expected: {expected_cli!r}\n"
        f"  got:      {rules_cli!r}"
    )

    # ASSERTION 3: does NOT contain mgr's broad rules
    assert not any("gh pr merge" in r for r in rules_cli), (
        "child --allowedTools must not inherit manager's 'gh pr merge' rule"
    )
    assert not any("docker" in r for r in rules_cli), (
        "child --allowedTools must not inherit manager's 'docker' rule"
    )

    # Sanity: mgr workspace would produce different rules (broad)
    mgr_rules_cli = allow_rules_for_agent(paths, "mgr", cli=True)
    assert "Bash(gh pr merge --squash *)" in mgr_rules_cli, (
        "sanity: manager should have gh pr merge rule"
    )
    assert "Bash(docker *)" in mgr_rules_cli, (
        "sanity: manager should have docker rule"
    )
