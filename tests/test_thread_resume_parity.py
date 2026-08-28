"""TASK-5977: thread REPLY provider-session resume parity beyond Claude.

Contract audit (THR-200 seq 31) proved, against the INSTALLED binaries,
that Codex (codex-cli 0.148.0) and Pi (pi 0.84.2) expose a stable
conversation identifier, non-interactive continuation by that identifier
with a stdin (large-prompt-safe) transport, and re-emit the same
identifier after continuation. OpenCode is NOT installed on this machine —
its resume contract is an unproven gap and it must stay fresh.

These tests pin the seams:
- codex/pi session-id parsers (stable/replacement capture, malformed -> None)
- codex/pi executor resume argv construction + default-off (task-style) runs
- thread-runner resume flows for codex/pi (delta vs full fallback, eviction
  classification, transactional invalidation, lifecycle, mixed executors,
  unsupported executors staying fresh)
- an explicit regression that TASK execution never passes a resume id
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from runtime.config import Settings
from runtime.infrastructure.database import Database
from runtime.models import (
    ThreadInvocationPurpose,
    ThreadInvocationStatus,
    ThreadMessage,
    ThreadMessageKind,
    ThreadParticipant,
    ThreadRecord,
    TokenUsage,
)


# ── shared fakes (mirror tests/test_thread_runner.py helpers) ──────────────


class _FakeResult:
    def __init__(self, success: bool, error: str = "", agent_session_id=None):
        self.success = success
        self.error = error
        self.returncode = 0 if success else 1
        self.session_id = "sess-x"
        self.duration_seconds = 1
        self.agent_session_id = agent_session_id
        self.stdout_tail = ""
        self.stderr_tail = ""
        self.token_usage = None


class _RecordingExec:
    """Fake executor: records run() kwargs, returns scripted results in order."""

    def __init__(self, scripted):
        self._scripted = list(scripted)
        self.calls: list[dict] = []

    def run(self, **kwargs):
        self.calls.append(kwargs)
        return self._scripted.pop(0)


class _Org:
    def __init__(self, db: Database, root: Path):
        self.db = db
        self.root = root
        self.slug = "test"


def _seed_queued_reply(db, thread_id, agent_name, triggering_seq):
    inv = db.mint_thread_invocation(
        thread_id=thread_id, agent_name=agent_name,
        triggering_seq=triggering_seq, purpose=ThreadInvocationPurpose.REPLY,
    )
    db._conn.execute(
        "INSERT INTO thread_reply_delivery_state "
        "(thread_id, agent_name, acknowledged_through_seq, required_through_seq, "
        "queued_invocation_token, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
        (thread_id, agent_name, triggering_seq - 1, triggering_seq,
         inv.invocation_token, datetime.now(timezone.utc).isoformat()),
    )
    db._conn.commit()
    return inv


def _write_agent(tmp_path: Path, executor: str, model: str | None = None) -> Path:
    agent_dir = tmp_path / "org" / "agents"
    agent_dir.mkdir(parents=True, exist_ok=True)
    model_line = f"model: {model}\n" if model else ""
    (agent_dir / "alice.md").write_text(
        "---\nname: alice\nteam: engineering\nrole: worker\n"
        f"executor: {executor}\n{model_line}---\n\nYou are a test agent.\n"
    )
    ws = tmp_path / "workspaces" / "alice"
    ws.mkdir(parents=True, exist_ok=True)
    return ws


async def _run_reply(tmp_path, monkeypatch, executor, stored_sid, last_seq, fake):
    from runtime.daemon.thread_runner import run_invocation
    db = Database(tmp_path / "happyranch.db")
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))
    db.add_thread_participant("THR-001", "alice", added_by="founder")
    db.append_thread_message(thread_id="THR-001", speaker="founder",
                             kind=ThreadMessageKind.MESSAGE, body_markdown="m1")
    db.append_thread_message(thread_id="THR-001", speaker="bob",
                             kind=ThreadMessageKind.MESSAGE, body_markdown="m2 newest")
    if stored_sid is not None:
        db.update_thread_session("THR-001", "alice",
                                 agent_session_id=stored_sid,
                                 last_resumed_seq=last_seq)
    inv = _seed_queued_reply(db, "THR-001", "alice", triggering_seq=2)
    _write_agent(tmp_path, executor)
    import runtime.daemon.thread_runner as runner_mod
    monkeypatch.setattr(runner_mod, "_build_executor_for_provider",
                        lambda provider, settings, paths: fake)
    org = _Org(db=db, root=tmp_path)
    await run_invocation(org_state=org, invocation_token=inv.invocation_token,
                         settings=Settings())
    return db, fake


# ═══════════════════════════════════════════════════════════════════════
# 1. Session-id parsers (stable / replacement capture)
# ═══════════════════════════════════════════════════════════════════════


def test_parse_codex_session_id_from_thread_started():
    from runtime.orchestrator.executors import _parse_codex_session_id
    stream = (
        '{"type":"thread.started","thread_id":"01a04748-75f8-7481-97e8-279332502c71",'
        '"timestamp":"2026-08-28T07:32:06Z"}\n'
        '{"type":"turn.started"}\n'
    )
    assert _parse_codex_session_id(stream) == "01a04748-75f8-7481-97e8-279332502c71"


def test_parse_codex_session_id_resume_emits_same_id():
    """After continuation codex re-emits the SAME thread_id — the parser must
    return it (replacement ids are also fine: the runner persists whatever the
    provider emits)."""
    from runtime.orchestrator.executors import _parse_codex_session_id
    stream = (
        '{"type":"thread.started","thread_id":"01a04748-75f8-7481-97e8-279332502c71"}\n'
    )
    assert _parse_codex_session_id(stream) == "01a04748-75f8-7481-97e8-279332502c71"


def test_parse_codex_session_id_malformed_or_missing_returns_none():
    from runtime.orchestrator.executors import _parse_codex_session_id
    assert _parse_codex_session_id("") is None
    assert _parse_codex_session_id("not json") is None
    assert _parse_codex_session_id('{"type":"turn.started"}') is None
    assert _parse_codex_session_id('{"type":"thread.started"}') is None  # no id
    assert _parse_codex_session_id('{"type":"thread.started","thread_id":42}') is None


def test_parse_pi_session_id_from_session_header():
    from runtime.orchestrator.executors import _parse_pi_session_id
    stream = (
        '{"type":"session","version":3,"id":"01a04749-2675-7bf2-924b-dd9e26f14092",'
        '"timestamp":"2026-08-28T07:32:51Z","cwd":"/tmp"}\n'
        '{"type":"agent_start"}\n'
    )
    assert _parse_pi_session_id(stream) == "01a04749-2675-7bf2-924b-dd9e26f14092"


def test_parse_pi_session_id_resume_emits_same_id():
    from runtime.orchestrator.executors import _parse_pi_session_id
    stream = '{"type":"session","version":3,"id":"01a04749-2675-7bf2-924b-dd9e26f14092"}\n'
    assert _parse_pi_session_id(stream) == "01a04749-2675-7bf2-924b-dd9e26f14092"


def test_parse_pi_session_id_malformed_or_missing_returns_none():
    from runtime.orchestrator.executors import _parse_pi_session_id
    assert _parse_pi_session_id("") is None
    assert _parse_pi_session_id("not json") is None
    assert _parse_pi_session_id('{"type":"agent_start"}') is None
    assert _parse_pi_session_id('{"type":"session","version":3}') is None  # no id
    assert _parse_pi_session_id('{"type":"session","id":123}') is None


# ═══════════════════════════════════════════════════════════════════════
# 2. Executor wiring: resume argv on request, default-off for task-style
# ═══════════════════════════════════════════════════════════════════════


def _popen_mock(stdout: str):
    from unittest.mock import MagicMock
    proc = MagicMock()
    proc.pid = 4242
    proc.returncode = 0
    proc.communicate.return_value = (stdout, "")
    return proc


def _patch_resolve_binary(monkeypatch, path: str = "/usr/local/bin/codex"):
    monkeypatch.setattr(
        "runtime.orchestrator.executors._resolve_binary",
        lambda name: path,
    )


def _capture_popen(monkeypatch, captured_cmd: list, stdout: str):
    """Patch Popen to record argv and return a success mock with `stdout`."""
    from runtime.orchestrator.executors import subprocess as _subprocess_mod

    def _capture(cmd, **kw):
        captured_cmd.extend(cmd)
        return _popen_mock(stdout)

    monkeypatch.setattr(_subprocess_mod, "Popen", _capture)


def test_codex_executor_resume_argv_and_session_capture(tmp_path, monkeypatch):
    from runtime.orchestrator.executors import CodexExecutor
    _patch_resolve_binary(monkeypatch)
    captured: list[str] = []
    _capture_popen(
        monkeypatch, captured,
        stdout='{"type":"thread.started","thread_id":"01a0-new"}\n'
               '{"type":"turn.completed","usage":{"input_tokens":1,"output_tokens":1}}\n',
    )
    ws = tmp_path / "ws"; ws.mkdir()
    ex = CodexExecutor(codex_cli_path="codex", sandbox_mode="workspace-write")
    result = ex.run(workspace=ws, prompt="delta", session_id="sess-X",
                    resume_session_id="01a0-prior", timeout_seconds=30)

    cmd = captured
    assert cmd[1:4] == ["exec", "resume", "01a0-prior"]
    assert "-c" in cmd and 'sandbox_mode="workspace-write"' in cmd
    assert "sandbox_workspace_write.network_access=true" in cmd
    assert cmd[-2:] == ["--json", "-"]
    # Prompt never an argv element (stdin transport).
    assert "delta" not in cmd
    assert result.success is True
    assert result.agent_session_id == "01a0-new"


def test_codex_executor_task_style_run_never_resumes(tmp_path, monkeypatch):
    """Regression: a task-style run (no resume_session_id) must not emit a
    resume subcommand — task execution never resumes provider sessions."""
    from runtime.orchestrator.executors import CodexExecutor
    _patch_resolve_binary(monkeypatch)
    captured: list[str] = []
    _capture_popen(monkeypatch, captured,
                   stdout='{"type":"thread.started","thread_id":"01a0-x"}\n')
    ws = tmp_path / "ws"; ws.mkdir()
    ex = CodexExecutor(codex_cli_path="codex", sandbox_mode="workspace-write")
    ex.run(workspace=ws, prompt="task prompt", session_id="sess-X", timeout_seconds=30)
    cmd = captured
    assert cmd[1] == "exec"
    assert "resume" not in cmd
    assert cmd[2] == "--sandbox"  # fresh exec keeps the --sandbox flag


def test_pi_executor_resume_argv_and_session_capture(tmp_path, monkeypatch):
    from runtime.orchestrator.executors import PiExecutor
    _patch_resolve_binary(monkeypatch, "/usr/local/bin/pi")
    captured: list[str] = []
    _capture_popen(
        monkeypatch, captured,
        stdout='{"type":"session","version":3,"id":"01a0-new","cwd":"/tmp"}\n'
               '{"type":"agent_start"}\n',
    )
    ws = tmp_path / "ws"; ws.mkdir()
    ex = PiExecutor(pi_cli_path="pi")
    result = ex.run(workspace=ws, prompt="delta", session_id="sess-X",
                    resume_session_id="01a0-prior", timeout_seconds=30)

    cmd = captured
    # --session (fail-if-missing) — NOT --session-id (create-if-missing):
    # an evicted pi session must fail closed so the runner can detect it.
    assert cmd[-2:] == ["--session", "01a0-prior"]
    assert "--session-id" not in cmd
    assert "delta" not in cmd
    assert result.success is True
    assert result.agent_session_id == "01a0-new"


def test_pi_executor_task_style_run_never_resumes(tmp_path, monkeypatch):
    from runtime.orchestrator.executors import PiExecutor
    _patch_resolve_binary(monkeypatch, "/usr/local/bin/pi")
    captured: list[str] = []
    _capture_popen(monkeypatch, captured,
                   stdout='{"type":"session","version":3,"id":"01a0-x"}\n')
    ws = tmp_path / "ws"; ws.mkdir()
    ex = PiExecutor(pi_cli_path="pi")
    ex.run(workspace=ws, prompt="task prompt", session_id="sess-X", timeout_seconds=30)
    cmd = captured
    assert "--session" not in cmd
    assert "-p" in cmd and "--mode" in cmd and "json" in cmd


# ═══════════════════════════════════════════════════════════════════════
# 3. Thread-runner flows
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
@pytest.mark.parametrize("executor,sid", [("codex", "01a0-codex-prior"),
                                          ("pi", "01a0-pi-prior")])
async def test_turn2_resumes_with_delta_for_codex_and_pi(
    tmp_path, monkeypatch, executor, sid,
):
    db, fake = await _run_reply(
        tmp_path, monkeypatch, executor,
        stored_sid=sid, last_seq=1,
        fake=_RecordingExec([_FakeResult(True, agent_session_id=sid)]),
    )
    assert fake.calls[0].get("resume_session_id") == sid
    delta = fake.calls[0]["prompt"]
    assert "m2 newest" in delta
    assert "m1" not in delta
    # Watermark advanced to the highest shown seq.
    stored, seq = db.get_thread_session("THR-001", "alice")
    assert stored == sid and seq == 2
    actions = {r["action"] for r in db.get_audit_logs("THR-001")}
    assert "agent_session_reused" in actions
    reused = next(r for r in db.get_audit_logs("THR-001")
                  if r["action"] == "agent_session_reused")
    assert reused["payload"]["executor"] == executor


@pytest.mark.asyncio
@pytest.mark.parametrize("executor", ["codex", "pi"])
async def test_turn1_full_prompt_never_resumes_for_codex_and_pi(
    tmp_path, monkeypatch, executor,
):
    db, fake = await _run_reply(
        tmp_path, monkeypatch, executor,
        stored_sid=None, last_seq=0,
        fake=_RecordingExec([_FakeResult(True, agent_session_id="01a0-fresh")]),
    )
    assert "resume_session_id" not in fake.calls[0]
    full = fake.calls[0]["prompt"]
    assert "m1" in full and "m2 newest" in full
    stored, seq = db.get_thread_session("THR-001", "alice")
    assert stored == "01a0-fresh" and seq == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "executor,eviction_error",
    [
        ("codex", "Command exited with code 1: Error: thread/resume: thread/resume "
                  "failed: no rollout found for thread id 01a0-dead (code -32600)"),
        ("pi", "Command exited with code 1: No session found matching '01a0-dead'"),
    ],
)
async def test_eviction_invalidates_then_single_full_retry(
    tmp_path, monkeypatch, executor, eviction_error,
):
    """Provider-declared session-not-found → transactional invalidation
    BEFORE exactly one fresh full-transcript retry; a failed fallback leaves
    the id NULL and the watermark unadvanced."""
    first = _FakeResult(False, error=eviction_error, agent_session_id=None)
    first.returncode = 1
    fallback = _FakeResult(False, error="Command exited with code 1: boom",
                           agent_session_id=None)
    fallback.returncode = 1
    db, fake = await _run_reply(
        tmp_path, monkeypatch, executor,
        stored_sid="01a0-dead", last_seq=1,
        fake=_RecordingExec([first, fallback]),
    )
    # Exactly one resume attempt + one fresh retry — no more.
    assert len(fake.calls) == 2
    assert fake.calls[0].get("resume_session_id") == "01a0-dead"
    assert "resume_session_id" not in fake.calls[1]
    # Fallback used the FULL transcript, not a delta.
    assert "m1" in fake.calls[1]["prompt"] and "m2 newest" in fake.calls[1]["prompt"]
    # Transactional invalidation committed: id NULL, watermark preserved.
    stored, seq = db.get_thread_session("THR-001", "alice")
    assert stored is None
    assert seq == 1  # failed fallback must not advance delivery state
    evicted = [r for r in db.get_audit_logs("THR-001")
               if r["action"] == "agent_session_evicted_fallback"]
    assert len(evicted) == 1
    assert evicted[0]["payload"]["executor"] == executor
    assert evicted[0]["payload"]["stale_session_id"] == "01a0-dead"


@pytest.mark.asyncio
@pytest.mark.parametrize("executor", ["codex", "pi"])
async def test_generic_resume_failure_never_retries_fresh(
    tmp_path, monkeypatch, executor,
):
    """A resume failure that is NOT the provider-declared eviction signature
    (auth/quota/transport/generic exit/ambiguous) must never trigger a fresh
    full-prompt retry — only eviction does."""
    generic = _FakeResult(False, error="Command exited with code 1: API Error: 529 Overloaded")
    generic.returncode = 1
    db, fake = await _run_reply(
        tmp_path, monkeypatch, executor,
        stored_sid="01a0-live", last_seq=1,
        fake=_RecordingExec([generic]),
    )
    assert len(fake.calls) == 1
    assert fake.calls[0].get("resume_session_id") == "01a0-live"
    stored, seq = db.get_thread_session("THR-001", "alice")
    assert stored == "01a0-live" and seq == 1  # untouched, no retry
    actions = {r["action"] for r in db.get_audit_logs("THR-001")}
    assert "agent_session_evicted_fallback" not in actions


@pytest.mark.asyncio
async def test_equality_watermark_uses_full_prompt_for_codex(tmp_path, monkeypatch):
    """GH-688 claim gate stays strict for codex too: watermark ==
    running_from_seq → full prompt, never a delta that could omit the
    required sequence."""
    db, fake = await _run_reply(
        tmp_path, monkeypatch, "codex",
        stored_sid="01a0-eq", last_seq=2,
        fake=_RecordingExec([_FakeResult(True, agent_session_id="01a0-eq")]),
    )
    assert "resume_session_id" not in fake.calls[0]
    assert "m1" in fake.calls[0]["prompt"] and "m2 newest" in fake.calls[0]["prompt"]


@pytest.mark.asyncio
async def test_opencode_stays_fresh_even_with_stale_stored_session(
    tmp_path, monkeypatch,
):
    """OpenCode's resume contract is an unproven gap (not installed) — a
    participant must stay fresh even if a stale session row exists."""
    db, fake = await _run_reply(
        tmp_path, monkeypatch, "opencode",
        stored_sid="01a0-stale", last_seq=1,
        fake=_RecordingExec([_FakeResult(True, agent_session_id=None)]),
    )
    assert "resume_session_id" not in fake.calls[0]
    assert "m1" in fake.calls[0]["prompt"] and "m2 newest" in fake.calls[0]["prompt"]
    # opencode emits no session id → the runner never touches the row; a
    # stale row is left for lifecycle invalidation (archive/switch/termination),
    # never resumed and never read.
    stored, seq = db.get_thread_session("THR-001", "alice")
    assert stored == "01a0-stale" and seq == 1  # untouched


@pytest.mark.asyncio
async def test_mixed_executors_resume_their_own_sessions(tmp_path, monkeypatch):
    """claude + codex participants in one thread each resume their own
    stored provider session."""
    from runtime.daemon.thread_runner import run_invocation

    db = Database(tmp_path / "happyranch.db")
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))
    db.add_thread_participant("THR-001", "alice", added_by="founder")
    db.add_thread_participant("THR-001", "bob", added_by="founder")
    db.append_thread_message(thread_id="THR-001", speaker="founder",
                             kind=ThreadMessageKind.MESSAGE, body_markdown="m1")
    db.append_thread_message(thread_id="THR-001", speaker="founder",
                             kind=ThreadMessageKind.MESSAGE, body_markdown="m2")
    db.update_thread_session("THR-001", "alice", agent_session_id="claude-s",
                             last_resumed_seq=1)
    db.update_thread_session("THR-001", "bob", agent_session_id="01a0-codex-s",
                             last_resumed_seq=1)

    # Seed alice as claude, bob as codex.
    agent_dir = tmp_path / "org" / "agents"
    agent_dir.mkdir(parents=True, exist_ok=True)
    (agent_dir / "alice.md").write_text(
        "---\nname: alice\nteam: engineering\nrole: worker\nexecutor: claude\n"
        "---\n\nYou are alice.\n")
    (agent_dir / "bob.md").write_text(
        "---\nname: bob\nteam: engineering\nrole: worker\nexecutor: codex\n"
        "---\n\nYou are bob.\n")
    (tmp_path / "workspaces" / "alice").mkdir(parents=True, exist_ok=True)
    (tmp_path / "workspaces" / "bob").mkdir(parents=True, exist_ok=True)

    calls: list[dict] = []

    class _Fake:
        def __init__(self):
            self._tokens: list[str] = []

        def set_tokens(self, tokens):
            self._tokens = tokens

        def run(self, **kwargs):
            calls.append(kwargs)
            # Simulate the agent posting its reply mid-session so the
            # invocation settles without a nudge re-invoke.
            db.consume_invocation(self._tokens.pop(0))
            r = _FakeResult(True)
            r.agent_session_id = kwargs.get("resume_session_id") or "fresh"
            return r

    import runtime.daemon.thread_runner as runner_mod
    fake = _Fake()
    monkeypatch.setattr(runner_mod, "_build_executor_for_provider",
                        lambda provider, settings, paths: fake)
    org = _Org(db=db, root=tmp_path)

    inv_a = _seed_queued_reply(db, "THR-001", "alice", triggering_seq=2)
    inv_b = _seed_queued_reply(db, "THR-001", "bob", triggering_seq=2)
    fake.set_tokens([inv_a.invocation_token, inv_b.invocation_token])
    await run_invocation(org_state=org, invocation_token=inv_a.invocation_token,
                         settings=Settings())
    await run_invocation(org_state=org, invocation_token=inv_b.invocation_token,
                         settings=Settings())

    assert len(calls) == 2
    assert calls[0].get("resume_session_id") == "claude-s"
    assert calls[1].get("resume_session_id") == "01a0-codex-s"


# ═══════════════════════════════════════════════════════════════════════
# 4. Task execution never passes a resume id
# ═══════════════════════════════════════════════════════════════════════


def test_orchestrator_task_path_source_never_references_resume():
    """Regression (TASK-5977): the orchestrator's task-execution surfaces
    must never pass a resume id. thread_runner.run_invocation is the ONLY
    production caller that wires resume_session_id; a task run that starts
    referencing it must update this pin + the protocol docs together."""
    import inspect
    from runtime.orchestrator.orchestrator import Orchestrator

    for member in ("_run_agent", "_run_agent_launch_contained"):
        src = inspect.getsource(getattr(Orchestrator, member))
        assert "resume_session_id" not in src, (
            f"Orchestrator.{member} must not reference resume_session_id — "
            f"task execution never resumes provider sessions."
        )


def test_executor_run_default_never_resumes_across_providers():
    """The resume parameter defaults to None on every resume-capable
    executor, so any caller that omits it (tasks, wakes, dreams) gets a
    fresh invocation. The resume-incapable executors (opencode, generic-CLI)
    do not even accept the parameter."""
    import inspect
    from runtime.orchestrator import executors as ex_mod

    for cls_name in ("ClaudeExecutor", "CodexExecutor", "PiExecutor"):
        cls = getattr(ex_mod, cls_name)
        sig = inspect.signature(cls.run)
        param = sig.parameters["resume_session_id"]
        assert param.default is None, (
            f"{cls_name}.run resume_session_id default must be None"
        )
    for cls_name in ("OpencodeExecutor", "GenericCliExecutor"):
        cls = getattr(ex_mod, cls_name)
        sig = inspect.signature(cls.run)
        assert "resume_session_id" not in sig.parameters, (
            f"{cls_name}.run must not accept resume_session_id — its resume "
            f"contract is unproven and it stays fresh"
        )
