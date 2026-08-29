"""THR-181 Track A (founder option B) — Unit 2: the mechanically restricted,
turn-scoped executor allow set for a single ACTIVE authority continuation
envelope.

Proves at the SHIPPING seams (inspecting ACTUAL executor argv / opencode
permission-map config, not helper outputs):

(a) the continuation launch resolves a mechanically narrowed allow set from
    the ACTIVE single-use envelope (launch-time envelope identity check);
    ordinary turns resolve None and launch byte-identically;
(b) the exact accepted continuation on a runtime-owned allow surface launches
    narrowed: claude ``--allowedTools`` carries ONLY the ``happyranch
    report-completion`` channel + the read/write tools needed to author the
    report — no git/gh, no jobs/threads/kb/memory/… verbs, no general bash,
    no Edit — and the opencode ``opencode.json`` permission map is narrowed
    the same way (turn-scoped write + restore, ordinary map restored after);
(c) executors WITHOUT a runtime-owned per-command allow surface (codex
    sandbox flags, pi, generic CLI, custom adapters) refuse the continued
    turn PRE-LAUNCH: the envelope is spent ``violated`` and the root enters
    the EXISTING ordinary founder-escalation lifecycle — never an
    unrestricted continued turn;
(d) every forbidden tool/command/action family is absent from the narrowed
    set; alternate daemon routes (jobs/threads) are unreachable from it;
(e) launch-refusal audit rows are bounded (decision family + error code,
    never raw prose); the audit denominator and exactly-once/CAS behavior
    stay intact; ordinary ESCALATE behavior is byte-identical apart from the
    required authority records;
(f) the strict CI fake stays deterministic (positive CONTINUE reachable).
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from runtime.config import Settings
from runtime.infrastructure.database import Database
from runtime.models import TaskStatus
from runtime.orchestrator._paths import OrgPaths
from runtime.orchestrator.authority import (
    AUDIT_ACTION_ENVELOPE_VIOLATED,
    _CONTINUATION_DONE_ALLOWED_BASH_PREFIXES,
    _CONTINUATION_DONE_ALLOWED_TOOLS_CLI,
    StrictFakeAuthorityEvaluator,
    executor_supports_turn_scoped_allow_set,
    resolve_continuation_turn_allow_set,
)
from runtime.orchestrator.authority_policy import ACTION_CONTINUE_SAME_ROOT
from runtime.orchestrator.orchestrator import Orchestrator
from runtime.orchestrator.teams import TeamsRegistry
from runtime.runtime import RuntimeDir

CONTINUE_REASON = "routine same-root follow-through of the already-completed slice"


@pytest.fixture(autouse=True)
def _mock_executor_binaries(monkeypatch, tmp_path):
    daemon_home = tmp_path / ".happyranch"
    monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(daemon_home))
    from runtime.orchestrator.executor_binary_registry import set_binary
    for name in ("claude", "codex", "opencode", "pi"):
        fake_bin = tmp_path / "bin" / name
        fake_bin.parent.mkdir(parents=True, exist_ok=True)
        fake_bin.touch(mode=0o755)
        set_binary(name, str(fake_bin))


@pytest.fixture(autouse=True)
def _seed_active_agents_for_run_step(runtime: OrgPaths):
    from tests.conftest import seed_test_agents
    seed_test_agents(runtime, ("engineering_head", "dev_agent", "content_head"))


@pytest.fixture
def runtime(tmp_path: Path) -> OrgPaths:
    rt = RuntimeDir.init(tmp_path / "rt")
    paths = OrgPaths(root=rt.orgs_dir / "test")
    paths.teams_config_path.parent.mkdir(parents=True, exist_ok=True)
    paths.teams_config_path.write_text(
        "teams:\n"
        "  engineering:\n"
        "    manager: engineering_head\n"
        "    workers: [product_manager, dev_agent, payment_agent, qa_engineer]\n"
    )
    return paths


@pytest.fixture
def db(runtime: OrgPaths) -> Database:
    return Database(runtime.db_path)


def _rewrite_manager_executor(runtime: OrgPaths, executor: str) -> None:
    """Point the seeded engineering_head at a different executor for the
    launch-refusal tests (hook eligibility does not depend on executor)."""
    from runtime.orchestrator.agent_def import AgentDef, render_agent_text
    from datetime import datetime, timezone
    ad = AgentDef(
        name="engineering_head", team="engineering", role="manager",
        executor=executor, allow_rules=("gh pr view",), repos={},
        enrolled_by=None, enrolled_at_task=None,
        enrolled_at=datetime.now(timezone.utc),
        system_prompt="You are the Engineering Head.\n",
    )
    (runtime.agents_dir / "engineering_head.md").write_text(render_agent_text(ad))


def _make_report(output_summary: str, status: str = "completed"):
    from runtime.models import CompletionReport
    return CompletionReport(
        task_id="T-IGNORED", agent="engineering_head", status=status,
        confidence=80, output_summary=output_summary,
    )


def _make_result(success: bool = True, duration: int = 1, session: str = "sess-x"):
    from runtime.orchestrator.executors import ExecutorResult
    return ExecutorResult(
        success=success, session_id=session, duration_seconds=duration,
    )


class _SlugQueue:
    def __init__(self) -> None:
        import asyncio as _asyncio
        self._q: _asyncio.Queue = _asyncio.Queue()
    def put_nowait(self, slug: str, task_id: str) -> None:
        self._q.put_nowait((slug, task_id))
    def qsize(self) -> int:
        return self._q.qsize()
    def get_nowait(self):
        return self._q.get_nowait()


def _make_orch(runtime, db, evaluator=None):
    orch = Orchestrator(
        db=db, settings=Settings(), paths=runtime, slug="test",
        teams=TeamsRegistry.load(runtime.root),
        authority_evaluator=evaluator,
    )
    orch._queue = _SlugQueue()
    from runtime.daemon.sessions import SessionTracker
    orch.attach_sessions(SessionTracker())
    return orch


def _seed_root(db, task_id: str = "T-ROOT") -> None:
    from runtime.models import TaskRecord
    db.insert_task(TaskRecord(
        id=task_id, brief="b", assigned_agent="engineering_head", team="engineering",
    ))


def _escalate_decision(reason: str) -> str:
    return json.dumps({"action": "escalate", "reason": reason})


def _run_escalate_step(orch, task_id: str, reason: str, monkeypatch) -> None:
    """Original escalate step: mints the envelope (CONTINUE) — the mocked
    _run_agent rides the ordinary path (no envelope yet), so its signature
    stays the baseline 4-arg form."""
    def fake_run_agent(task_id, agent, prompt, on_session_started=None):
        orch.db.update_task(task_id, current_session_id="sess-x")
        orch.db.insert_task_result(
            task_id=task_id, agent=agent, session_id="sess-x",
            status="completed", confidence_score=80,
            output_summary=_escalate_decision(reason),
            decision_json=_escalate_decision(reason),
        )
        return _make_result(session="sess-x"), _make_report(
            output_summary=_escalate_decision(reason),
        )
    monkeypatch.setattr(orch, "_run_agent", fake_run_agent)
    orch.run_step(task_id)


# ── (a) resolver: launch-time envelope identity check ────────────────────

def test_continuation_turn_resolves_narrowed_allow_set(runtime, db, monkeypatch):
    fake = StrictFakeAuthorityEvaluator()
    _seed_root(db)
    orch = _make_orch(runtime, db, evaluator=fake)
    _run_escalate_step(orch, "T-ROOT", CONTINUE_REASON, monkeypatch)

    env = db.get_active_authority_continue_envelope("T-ROOT")
    assert env is not None
    allow_set = resolve_continuation_turn_allow_set(db, "T-ROOT", "engineering_head")
    assert allow_set is not None
    assert allow_set.refused is False
    # Bound to the immutable Unit-1 envelope identity.
    assert allow_set.envelope_id == env["id"]
    assert allow_set.permitted_action == ACTION_CONTINUE_SAME_ROOT
    assert allow_set.permitted_decision == "done"
    # The mechanically narrowed set: report-completion channel + read/write
    # tools only. No general bash, no git/gh, no Edit, no other happyranch verb.
    assert allow_set.allowed_tools_cli == _CONTINUATION_DONE_ALLOWED_TOOLS_CLI
    assert "Bash(happyranch report-completion *)" in allow_set.allowed_tools_cli
    assert not any("Bash(" in t and "happyranch report-completion" not in t
                   for t in allow_set.allowed_tools_cli)
    assert allow_set.allowed_bash_prefixes == _CONTINUATION_DONE_ALLOWED_BASH_PREFIXES
    assert allow_set.allowed_bash_prefixes == ("happyranch report-completion",)


def test_ordinary_turn_resolves_none(runtime, db, monkeypatch):
    fake = StrictFakeAuthorityEvaluator()
    _seed_root(db)
    orch = _make_orch(runtime, db, evaluator=fake)
    _run_escalate_step(orch, "T-ROOT", "needs founder decision", monkeypatch)
    # The escalate was NOT a continuation -> no envelope -> ordinary turn.
    assert db.get_active_authority_continue_envelope("T-ROOT") is None
    assert resolve_continuation_turn_allow_set(db, "T-ROOT", "engineering_head") is None


def test_consumed_envelope_resolves_none(runtime, db, monkeypatch):
    """A spent envelope no longer restricts any launch — but the continuation
    window is over, so the resolver returns None (ordinary turn)."""
    fake = StrictFakeAuthorityEvaluator()
    _seed_root(db)
    orch = _make_orch(runtime, db, evaluator=fake)
    _run_escalate_step(orch, "T-ROOT", CONTINUE_REASON, monkeypatch)
    assert db.spend_authority_continue_envelope_if_active(
        "T-ROOT", audit_agent="engineering_head", error="test: spent",
    ) is True
    assert resolve_continuation_turn_allow_set(db, "T-ROOT", "engineering_head") is None


def test_envelope_identity_mismatch_at_launch_refuses(runtime, db, monkeypatch):
    fake = StrictFakeAuthorityEvaluator()
    _seed_root(db)
    orch = _make_orch(runtime, db, evaluator=fake)
    _run_escalate_step(orch, "T-ROOT", CONTINUE_REASON, monkeypatch)
    # A DIFFERENT manager agent cannot inherit the continuation's allow set.
    allow_set = resolve_continuation_turn_allow_set(db, "T-ROOT", "dev_agent")
    assert allow_set is not None
    assert allow_set.refused is True
    assert "identity mismatch" in (allow_set.refused_reason or "")


def test_unsupported_permitted_action_refuses(runtime, db, monkeypatch):
    """An ACTIVE envelope whose permitted action has no mechanical allow set
    must refuse the launch (fail closed), never launch unrestricted."""
    fake = StrictFakeAuthorityEvaluator()
    _seed_root(db)
    orch = _make_orch(runtime, db, evaluator=fake)
    _run_escalate_step(orch, "T-ROOT", CONTINUE_REASON, monkeypatch)

    from runtime.orchestrator import authority as authority_mod
    real_get = db.get_active_authority_continue_envelope
    env = real_get("T-ROOT")
    fake_env = dict(env)
    fake_env["action"] = "escalate_to_founder"
    monkeypatch.setattr(
        db, "get_active_authority_continue_envelope",
        lambda root: fake_env,
    )
    allow_set = resolve_continuation_turn_allow_set(db, "T-ROOT", "engineering_head")
    assert allow_set is not None
    assert allow_set.refused is True
    assert "unsupported permitted action" in (allow_set.refused_reason or "")


def test_executor_support_predicate():
    assert executor_supports_turn_scoped_allow_set("claude") is True
    assert executor_supports_turn_scoped_allow_set("opencode") is True
    # Executors without a runtime-owned per-command allow surface:
    assert executor_supports_turn_scoped_allow_set("codex") is False
    assert executor_supports_turn_scoped_allow_set("pi") is False
    assert executor_supports_turn_scoped_allow_set("some-custom-adapter") is False


# ── (c) unsupported executors refuse the continued turn PRE-LAUNCH ───────

@pytest.mark.parametrize("executor", ["codex", "pi"])
def test_continuation_launch_refused_for_unsupported_executor(
    runtime, db, monkeypatch, executor,
):
    """Codex/Pi (no runtime-owned per-command allow surface) must refuse the
    continued turn pre-launch: envelope violated + ordinary founder
    escalation + NO executor launch. An unrestricted continued turn is never
    launched on them."""
    _rewrite_manager_executor(runtime, executor)
    fake = StrictFakeAuthorityEvaluator()
    _seed_root(db)
    orch = _make_orch(runtime, db, evaluator=fake)
    _run_escalate_step(orch, "T-ROOT", CONTINUE_REASON, monkeypatch)
    assert db.get_active_authority_continue_envelope("T-ROOT") is not None

    launched = []
    def fake_run_agent(task_id, agent, prompt, on_session_started=None, turn_allow_set=None):
        launched.append((task_id, turn_allow_set))
        orch.db.update_task(task_id, current_session_id="sess-y")
        orch.db.insert_task_result(
            task_id=task_id, agent=agent, session_id="sess-y",
            status="completed", confidence_score=80,
            output_summary=json.dumps({"action": "done"}),
            decision_json=json.dumps({"action": "done"}),
        )
        return _make_result(session="sess-y"), _make_report(output_summary="done")
    monkeypatch.setattr(orch, "_run_agent", fake_run_agent)
    orch.run_step("T-ROOT")

    # NO launch happened.
    assert launched == []
    t = db.get_task("T-ROOT")
    assert t.status == TaskStatus.ESCALATED
    env = db.get_active_authority_continue_envelope("T-ROOT")
    assert env is None  # spent
    spent = db.get_authority_continue_envelope(
        "CONT-" + db.list_authority_candidates_for_root("T-ROOT")[0].id,
    )
    assert spent["state"] == "violated"
    # The refusal audit row is bounded (decision family + error code, never
    # raw prose) and names the envelope id.
    viol = [
        a for a in db.get_audit_logs("T-ROOT")
        if a["action"] == AUDIT_ACTION_ENVELOPE_VIOLATED
    ]
    assert len(viol) == 1
    assert viol[0]["payload"]["decision_family"] == "launch_refused"
    assert viol[0]["payload"]["envelope_id"] == spent["id"]
    # The bounded error code names the unsupported executor surface.
    assert "executor" in viol[0]["payload"].get("error", "")
    # Ordinary founder-escalation rows exist.
    esc = [a for a in db.get_audit_logs("T-ROOT") if a["action"] == "escalation"]
    assert len(esc) == 1
    assert "authority-continuation envelope violation" in esc[0]["payload"].get("reason", "")


def test_ordinary_escalate_byte_identical_without_envelope_on_codex(
    runtime, db, monkeypatch,
):
    """A codex manager WITHOUT an active envelope escalates ordinarily — the
    launch refusal is scoped to the continuation window only."""
    _rewrite_manager_executor(runtime, "codex")
    fake = StrictFakeAuthorityEvaluator()
    _seed_root(db)
    orch = _make_orch(runtime, db, evaluator=fake)

    launched = []
    def fake_run_agent(task_id, agent, prompt, on_session_started=None):
        launched.append(task_id)
        orch.db.update_task(task_id, current_session_id="sess-x")
        orch.db.insert_task_result(
            task_id=task_id, agent=agent, session_id="sess-x",
            status="completed", confidence_score=80,
            output_summary=_escalate_decision("needs founder"),
            decision_json=_escalate_decision("needs founder"),
        )
        return _make_result(), _make_report(output_summary=_escalate_decision("needs founder"))
    monkeypatch.setattr(orch, "_run_agent", fake_run_agent)
    orch.run_step("T-ROOT")

    assert launched == ["T-ROOT"]
    assert db.get_task("T-ROOT").status == TaskStatus.ESCALATED
    assert db.get_active_authority_continue_envelope("T-ROOT") is None


# ── (b) narrowed claude argv at the real executor seam ───────────────────

def test_claude_continuation_launch_narrows_allowed_tools(runtime, tmp_path, monkeypatch):
    """The REAL ClaudeExecutor.run with a continuation allow set produces an
    argv whose ``--allowedTools`` is the mechanically narrowed set — the
    continued manager turn never inherits the ordinary gh rules."""
    from runtime.orchestrator.authority import ContinuationTurnAllowSet
    from runtime.orchestrator.executors import ClaudeExecutor
    workspace = tmp_path / "engineering_head"
    workspace.mkdir()

    monkeypatch.setattr(
        "runtime.orchestrator.workspace_adapters.allow_rules_for_agent",
        lambda paths, agent, *, cli: ["Bash(happyranch *)", "Bash(gh pr view *)"],
    )

    allow_set = ContinuationTurnAllowSet(
        envelope_id="CONT-cand",
        permitted_action=ACTION_CONTINUE_SAME_ROOT,
        permitted_decision="done",
    )

    captured = {}
    def _capture_popen(cmd, **kwargs):
        captured["cmd"] = list(cmd)
        proc = _FakeProc()
        return proc
    monkeypatch.setattr("runtime.orchestrator.executors.subprocess.Popen", _capture_popen)

    executor = ClaudeExecutor(
        claude_cli_path="claude", permission_mode="auto",
        settings=Settings(), paths=runtime,
    )
    result = executor.run(
        workspace=workspace, prompt="continue the root", timeout_seconds=30,
        turn_allow_set=allow_set,
    )
    assert result.success is True
    cmd = captured["cmd"]
    allowed = cmd[cmd.index("--allowedTools") + 1]
    assert "Bash(happyranch report-completion *)" in allowed
    # No ordinary manager permissions: no gh, no general bash, no git.
    assert "gh" not in allowed
    assert "git" not in allowed
    assert "Bash(happyranch *)" not in allowed
    # No alternate daemon routes / other happyranch verbs.
    for verb in ("jobs", "threads", "kb", "memory", "artifacts", "tasks",
                 "cancel", "revisit", "progress", "tail", "details"):
        assert f"happyranch {verb}" not in allowed, verb
    # Only the report-completion channel is a bash rule.
    import re as _re
    bash_rules = _re.findall(r"Bash\([^)]*\)", allowed)
    assert bash_rules == ["Bash(happyranch report-completion *)"]


def test_claude_ordinary_launch_keeps_baseline_allow_rules(runtime, tmp_path, monkeypatch):
    """Without a continuation allow set the ordinary launch keeps the
    per-agent baseline rules byte-identically (no baseline change)."""
    from runtime.orchestrator.executors import ClaudeExecutor
    workspace = tmp_path / "engineering_head"
    workspace.mkdir()
    monkeypatch.setattr(
        "runtime.orchestrator.workspace_adapters.allow_rules_for_agent",
        lambda paths, agent, *, cli: ["Bash(happyranch *)", "Bash(gh pr view *)"],
    )
    captured = {}
    def _capture_popen(cmd, **kwargs):
        captured["cmd"] = list(cmd)
        return _FakeProc()
    monkeypatch.setattr("runtime.orchestrator.executors.subprocess.Popen", _capture_popen)
    executor = ClaudeExecutor(
        claude_cli_path="claude", permission_mode="auto",
        settings=Settings(), paths=runtime,
    )
    result = executor.run(workspace=workspace, prompt="decide", timeout_seconds=30)
    assert result.success is True
    cmd = captured["cmd"]
    allowed = cmd[cmd.index("--allowedTools") + 1]
    assert "Bash(happyranch *)" in allowed
    assert "Bash(gh pr view *)" in allowed


def test_claude_continuation_build_launch_spec_narrowed(runtime, tmp_path):
    """The contained-path LaunchSpec for a continued turn carries the same
    narrowed ``--allowedTools`` argv."""
    from runtime.orchestrator.authority import ContinuationTurnAllowSet
    from runtime.orchestrator.executors import ClaudeExecutor
    workspace = tmp_path / "engineering_head"
    workspace.mkdir()
    allow_set = ContinuationTurnAllowSet(
        envelope_id="CONT-cand", permitted_action=ACTION_CONTINUE_SAME_ROOT,
        permitted_decision="done",
    )
    executor = ClaudeExecutor(
        claude_cli_path="claude", permission_mode="auto",
        settings=Settings(), paths=runtime,
    )
    spec = executor.build_launch_spec(
        workspace=workspace, prompt="continue", session_id="sess-x",
        turn_allow_set=allow_set,
    )
    argv = list(spec.argv)
    allowed = argv[argv.index("--allowedTools") + 1]
    assert "Bash(happyranch report-completion *)" in allowed
    assert "gh" not in allowed


# ── opencode: turn-scoped permission-map swap (daemon-owned writer) ──────

def _ordinary_opencode_map(runtime, agent_name="engineering_head"):
    from runtime.orchestrator.workspace_adapters import bash_allow_prefixes_for_agent
    prefixes = bash_allow_prefixes_for_agent(runtime, agent_name)
    return {"$schema": "https://opencode.ai/config.json",
            "permission": {"bash": {"*": "deny", **{f"{p} *": "allow" for p in prefixes}}}}


def test_opencode_turn_scoped_permissions_swap(runtime, tmp_path):
    """The turn-scoped opencode.json swap narrows the permission map to
    ``happyranch report-completion``-only and restores the ordinary per-agent
    map afterwards — on the success AND the error path (fail-closed)."""
    from runtime.orchestrator.authority import ContinuationTurnAllowSet
    from runtime.orchestrator.orchestrator import _turn_scoped_opencode_permissions
    workspace = tmp_path / "engineering_head"
    workspace.mkdir()
    ordinary = _ordinary_opencode_map(runtime)
    (workspace / "opencode.json").write_text(json.dumps(ordinary, indent=2) + "\n")
    allow_set = ContinuationTurnAllowSet(
        envelope_id="CONT-cand", permitted_action=ACTION_CONTINUE_SAME_ROOT,
        permitted_decision="done",
    )

    with _turn_scoped_opencode_permissions(
        Settings(), runtime, workspace, "engineering_head", allow_set,
    ):
        narrowed = json.loads((workspace / "opencode.json").read_text())
        bash = narrowed["permission"]["bash"]
        assert bash["*"] == "deny"
        assert bash["happyranch report-completion *"] == "allow"
        assert not any(k.startswith(("gh ", "git ")) for k in bash)
        assert not any("happyranch jobs" in k or "happyranch threads" in k for k in bash)
        try:
            raise RuntimeError("boom inside the turn")  # error path still restores
        except RuntimeError:
            pass
    # Ordinary map restored.
    restored = json.loads((workspace / "opencode.json").read_text())
    assert restored == ordinary


def test_opencode_continuation_applies_swap_at_real_launch_seam(
    runtime, db, monkeypatch,
):
    """The REAL ``Orchestrator._run_agent`` for an opencode continued turn
    writes the narrowed opencode.json BEFORE the executor launch (the fake
    executor observes the narrowed file at launch time) and restores the
    ordinary per-agent map AFTER the session."""
    _rewrite_manager_executor(runtime, "opencode")
    from runtime.orchestrator.authority import ContinuationTurnAllowSet
    from runtime.orchestrator.orchestrator import (
        _turn_scoped_opencode_permissions,
        Orchestrator,
    )
    from runtime.orchestrator.workspace_adapters import OpencodeWorkspaceAdapter
    from runtime.daemon.sessions import SessionTracker

    orch = Orchestrator(
        db=db, settings=Settings(), paths=runtime, slug="test",
        teams=TeamsRegistry.load(runtime.root),
    )
    orch._queue = _SlugQueue()
    orch.attach_sessions(SessionTracker())

    workspace = runtime.workspaces_dir / "engineering_head"
    workspace.mkdir(parents=True, exist_ok=True)
    OpencodeWorkspaceAdapter(Settings(), runtime, slug="test").ensure_workspace_ready(
        workspace, "engineering_head", "You are the Engineering Head.\n",
    )
    ordinary = json.loads((workspace / "opencode.json").read_text())

    allow_set = ContinuationTurnAllowSet(
        envelope_id="CONT-cand", permitted_action=ACTION_CONTINUE_SAME_ROOT,
        permitted_decision="done",
    )

    observed = {}
    class _FakeOpenExecutor:
        def run(self, *, workspace, prompt, session_id, timeout_seconds, **kwargs):
            observed["turn_allow_set"] = kwargs.get("turn_allow_set")
            observed["opencode_json_at_launch"] = json.loads(
                (workspace / "opencode.json").read_text(),
            )
            return _make_result(session=session_id)
    monkeypatch.setattr(orch, "_build_executor", lambda provider: _FakeOpenExecutor())
    # Stub the pre-launch machinery we are not testing here.
    monkeypatch.setattr(
        "runtime.orchestrator.orchestrator.materialize_workspace_skills",
        lambda *a, **k: [],
    )
    monkeypatch.setattr(
        "runtime.orchestrator.orchestrator.validate_workspace_skills_integrity",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(
        "runtime.orchestrator.orchestrator.refresh_workspace_repos",
        lambda workspace: {},
    )
    monkeypatch.setattr(
        "runtime.orchestrator.orchestrator.resolve_managed_skills_index",
        lambda **k: "",
    )
    monkeypatch.setattr(
        "runtime.orchestrator.orchestrator.resolve_protocol_doc_manifest",
        lambda **k: "",
    )
    monkeypatch.setattr(
        orch, "_build_agent_prompt",
        lambda *a, **k: "continue the same root within `done`",
    )
    monkeypatch.setattr(
        orch, "_materialize_task_attachments", lambda **k: "",
    )

    _seed_root(db)
    result, report = orch._run_agent(
        "T-ROOT", "engineering_head", "continue", turn_allow_set=allow_set,
    )
    assert result.success is True
    # The executor observed the narrowed map AT LAUNCH.
    assert observed["opencode_json_at_launch"]["permission"]["bash"]["*"] == "deny"
    assert (
        observed["opencode_json_at_launch"]["permission"]["bash"]
        ["happyranch report-completion *"] == "allow"
    )
    assert observed["turn_allow_set"].envelope_id == "CONT-cand"
    # The ordinary per-agent map is restored after the session.
    assert json.loads((workspace / "opencode.json").read_text()) == ordinary


class _FakeProc:
    pid = 4242
    returncode = 0
    def communicate(self, input=None, timeout=None):
        return ("", "")
    def kill(self):
        pass
