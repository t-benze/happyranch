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
- TASK-5989 review: strict no-message-omission — a resumed delta is used
  only when the ENTIRE required post-watermark range is proven present and
  contiguous at the production seam (>10k transcripts, internal holes,
  equal/ahead and null/zero/negative (<= 0) watermarks, truncated loads);
  and exact executor/rc/stream/signature eviction classification bound to
  the attempted session id (positive + negative matrix)
- an explicit regression that TASK execution never passes a resume id
"""
from __future__ import annotations

import re
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
    def __init__(self, success: bool, error: str = "", agent_session_id=None,
                 stderr_tail: str = "", stdout_tail: str = "",
                 returncode: int | None = None):
        self.success = success
        self.error = error
        self.returncode = 0 if success else (1 if returncode is None else returncode)
        self.session_id = "sess-x"
        self.duration_seconds = 1
        self.agent_session_id = agent_session_id
        self.stdout_tail = stdout_tail
        self.stderr_tail = stderr_tail
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


def _bulk_append_messages(db, thread_id, start, end, prefix="msg-"):
    """Bulk-insert messages ``start..end`` (inclusive) with distinct bodies
    in ONE transaction — mirrors append_thread_message's row shape."""
    rows = [
        (thread_id, seq, "founder", ThreadMessageKind.MESSAGE.value,
         f"{prefix}{seq:05d}", None, None, None, None,
         datetime.now(timezone.utc).isoformat())
        for seq in range(start, end + 1)
    ]
    db._conn.executemany(
        "INSERT INTO thread_messages (thread_id, seq, speaker, kind, "
        "body_markdown, decline_reason, system_payload_json, "
        "sent_from_task_id, mentions_json, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    db._conn.commit()


def _bodies_in(prompt: str) -> set[str]:
    """Distinct ``msg-<seq>`` body tokens present in a rendered prompt."""
    return set(re.findall(r"msg-\d{5}", prompt))


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
    assert _parse_pi_session_id('{"type":"session","version":3,"id":42}') is None


def test_parse_opencode_session_id_from_step_start():
    """opencode 1.18.25 `--format json` NDJSON: EVERY event carries the
    top-level ``sessionID``; the parser returns the first one seen (the
    ``step_start`` event, matching the observed fresh-run output)."""
    from runtime.orchestrator.executors import _parse_opencode_session_id
    stream = (
        '{"type":"step_start","timestamp":1787978082641,'
        '"sessionID":"ses_fb433ade8ffeh55zGEozUE3mey",'
        '"part":{"id":"prt_01","sessionID":"ses_fb433ade8ffeh55zGEozUE3mey",'
        '"type":"step-start"}}\n'
        '{"type":"text","timestamp":1787978082916,'
        '"sessionID":"ses_fb433ade8ffeh55zGEozUE3mey",'
        '"part":{"type":"text","text":"OK"}}\n'
        '{"type":"step_finish","timestamp":1787978082916,'
        '"sessionID":"ses_fb433ade8ffeh55zGEozUE3mey",'
        '"part":{"type":"step-finish","reason":"stop","tokens":{}}}\n'
    )
    assert _parse_opencode_session_id(stream) == "ses_fb433ade8ffeh55zGEozUE3mey"


def test_parse_opencode_session_id_resume_emits_same_id():
    """After continuation opencode re-emits the SAME sessionID — the parser
    must return it (replacement ids are also fine: the runner persists
    whatever the provider emits)."""
    from runtime.orchestrator.executors import _parse_opencode_session_id
    stream = (
        '{"type":"step_start","timestamp":1,'
        '"sessionID":"ses_fb433ade8ffeh55zGEozUE3mey","part":{}}\n'
    )
    assert _parse_opencode_session_id(stream) == "ses_fb433ade8ffeh55zGEozUE3mey"


def test_parse_opencode_session_id_malformed_or_missing_returns_none():
    from runtime.orchestrator.executors import _parse_opencode_session_id
    assert _parse_opencode_session_id("") is None
    assert _parse_opencode_session_id("not json") is None
    # Non-dict JSON lines are skipped without error.
    assert _parse_opencode_session_id('["step_start"]\n') is None
    # Events without a sessionID field, non-string sessionID, or an
    # empty-string sessionID never yield an id.
    assert _parse_opencode_session_id('{"type":"step_start"}') is None
    assert _parse_opencode_session_id('{"type":"step_start","sessionID":42}') is None
    assert _parse_opencode_session_id('{"type":"step_start","sessionID":""}') is None
    # A JSON error event (auth/transport class) carries a minted sessionID —
    # still captured best-effort; resume eligibility gates on stored state.
    assert _parse_opencode_session_id(
        '{"type":"error","sessionID":"ses_fb43169fbffez2iDYBQ26mOdMg",'
        '"error":{"name":"UnknownError"}}'
    ) == "ses_fb43169fbffez2iDYBQ26mOdMg"


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
                                          ("pi", "01a0-pi-prior"),
                                          ("opencode", "ses_opencode-prior")])
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
        # Exact stderr signatures recorded by the 2026-08-28 local probes
        # (bounded, no API traffic) against the INSTALLED CLIs — each echoes
        # the ATTEMPTED id verbatim. For codex the OBSERVED complete line
        # includes the CLI envelope `Error: thread/resume: thread/resume
        # failed: `; for pi the line is exactly `No session found matching
        # '<id>'` (quoted id).
        ("codex", "Error: thread/resume: thread/resume failed: no rollout "
                  "found for thread id 01a0-dead (code -32600)"),
        ("pi", "No session found matching '01a0-dead'"),
        ("claude", "No conversation found with session ID: 01a0-dead"),
        # The observed complete signature line still classifies when wrapped
        # in unrelated text on OTHER physical lines (never on the signature
        # line itself — arbitrary same-line prefix/suffix is a negative,
        # below; auth/quota/transport output is a negative too).
        ("codex", "Warning: stale config detected; continuing\n"
                  "Error: thread/resume: thread/resume failed: no rollout "
                  "found for thread id 01a0-dead (code -32600)\n"
                  "Process finished with exit code 1"),
        ("pi", "some unrelated first line\n"
                "No session found matching '01a0-dead'\n"
                "process finished with exit code 1"),
        ("claude", "No conversation found with session ID: 01a0-dead\n"
                    "[debug] exiting"),
        # opencode 1.18.25 (TASK-6080 audit): the attempted id is NOT echoed;
        # the contract is rc=1 + empty stdout + one complete physical stderr
        # line exactly equal to `Error: Session not found` after ANSI-SGR
        # stripping. Unrelated text on OTHER lines stays positive; only text
        # on the signature line is a negative. Unlike claude/codex/pi no
        # global auth/quota/transport token veto applies — a token on an
        # unrelated line must not veto a genuine eviction line.
        ("opencode", "Error: Session not found"),
        ("opencode", "\x1b[91m\x1b[1mError: \x1b[0mSession not found"),
        ("opencode", "Ignoring 1 permissions.allow entry...\n"
                      "Error: Session not found\n"
                      "process finished with exit code 1"),
        ("opencode", "Error: 401 unauthorized\nError: Session not found"),
        ("opencode", "Error: Session not found\r\n"),
        # Horizontal whitespace is permitted only for the three id-bound
        # provider contracts. OpenCode's signature-only physical line is a
        # literal contract and therefore has no padding allowance.
        ("codex", "  Error: thread/resume: thread/resume failed: no rollout "
                  "found for thread id  01a0-dead  (code -32600)  "),
        ("pi", "  No session found matching  '01a0-dead'  "),
        ("claude", "\tNo conversation found with session ID: 01a0-dead\t"),
        # Complete-line termination (TASK-6019): the observed provider stderr
        # LINE must be exactly `No conversation found with session ID:
        # <attempted-id>`; allowed whitespace around the signature/id and
        # unrelated text on OTHER lines stay positive — only text ON the
        # signature line after the id (hyphen/punctuation/words) is a
        # suffix and must fail.
        ("claude", "  No conversation found with session ID: 01a0-dead  "),
        ("claude", "No conversation found with session ID: 01a0-dead \t\n"
                    "[debug] exiting"),
    ],
)
async def test_eviction_invalidates_then_single_full_retry(
    tmp_path, monkeypatch, executor, eviction_error,
):
    """Provider-declared session-not-found → transactional invalidation
    BEFORE exactly one fresh full-transcript retry; a failed fallback leaves
    the id NULL and the watermark unadvanced."""
    first = _FakeResult(False, error=f"Command exited with code 1: {eviction_error}",
                        agent_session_id=None, stderr_tail=eviction_error,
                        returncode=1)
    fallback = _FakeResult(False, error="Command exited with code 1: boom",
                           agent_session_id=None, returncode=1)
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
@pytest.mark.parametrize(
    "corpus_stderr,corpus_sid",
    [
        # Positive corpus from the IMMUTABLE audit-log evidence: the live
        # daemon recorded REAL Claude evictions as `agent_session_evicted_fallback`
        # audit rows in the org runtime DB
        # `/home/benze/.happyranch/runtime/orgs/happyranch/happyranch.db`
        # (table `audit_log`; 25 rows, executor=claude, agent=consultant_head,
        # 2026-08-24T10:02:18Z id 67364 .. 2026-08-28T01:32:47Z id 74845;
        # THR-198/THR-195/THR-187/THR-190/THR-175/THR-165/THR-097). The
        # observed payload error shape is `Command exited with code 1: <stderr>`
        # where <stderr> is one COMPLETE LF-terminated line
        # `No conversation found with session ID: <attempted-id>` — for row
        # 67364 the eviction line is the FIRST (only) stderr line; for the
        # THR-187/THR-195 rows it FOLLOWS a `.claude/settings.json`
        # trust-warning first line (unrelated text on an EARLIER line is
        # tolerated; the eviction LINE itself is complete). The session id
        # is substituted with a synthetic UUID of the same shape — no
        # secrets; the classifier reads stderr_tail (never the envelope).
        (
            "No conversation found with session ID: "
            "efdad7c5-f542-413b-8733-69961917801d",
            "efdad7c5-f542-413b-8733-69961917801d",
        ),
        (
            "Ignoring 1 permissions.allow entry from .claude/settings.json: "
            "this workspace has not been trusted. Run Claude Code "
            "interactively here once and accept the trust dialog.\n"
            "No conversation found with session ID: "
            "e65b6466-bed4-4aa2-b704-57e270babe66",
            "e65b6466-bed4-4aa2-b704-57e270babe66",
        ),
    ],
)
async def test_eviction_observed_audit_corpus_line_classifies(
    tmp_path, monkeypatch, corpus_stderr, corpus_sid,
):
    """The corpus line recorded in the immutable audit log for the observed
    Claude eviction classifies through the SHIPPING `_classify_session_evicted`
    call path (run_invocation eviction fallback) with the transactional
    invalidation-before-exactly-one-fresh-full-retry behavior: one resume
    attempt, the eviction audit + `agent_session_id = NULL` invalidation
    committed in one transaction, then exactly one fresh full-transcript
    retry whose failure leaves the id NULL and the watermark unadvanced."""
    first = _FakeResult(False, error=f"Command exited with code 1: {corpus_stderr}",
                        agent_session_id=None, stderr_tail=corpus_stderr,
                        returncode=1)
    fallback = _FakeResult(False, error="Command exited with code 1: boom",
                           agent_session_id=None, returncode=1)
    db, fake = await _run_reply(
        tmp_path, monkeypatch, "claude",
        stored_sid=corpus_sid, last_seq=1,
        fake=_RecordingExec([first, fallback]),
    )
    # Exactly one resume attempt + exactly one fresh full retry.
    assert len(fake.calls) == 2
    assert fake.calls[0].get("resume_session_id") == corpus_sid
    assert "resume_session_id" not in fake.calls[1]
    # The fallback ran the FULL canonical transcript, not a delta.
    assert "m1" in fake.calls[1]["prompt"] and "m2 newest" in fake.calls[1]["prompt"]
    # Transactional invalidation committed: id NULL, watermark preserved.
    stored, seq = db.get_thread_session("THR-001", "alice")
    assert stored is None
    assert seq == 1  # failed fallback must not advance delivery state
    evicted = [r for r in db.get_audit_logs("THR-001")
               if r["action"] == "agent_session_evicted_fallback"]
    assert len(evicted) == 1
    assert evicted[0]["payload"]["executor"] == "claude"
    assert evicted[0]["payload"]["stale_session_id"] == corpus_sid


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "executor,stderr,stdout,rc",
    [
        # Cross-provider text on the wrong executor must never classify.
        ("codex", "No session found matching '01a0-live'", "", 1),
        ("codex", "no conversation found with session id: 01a0-live", "", 1),
        ("pi", "no rollout found for thread id 01a0-live (code -32600)", "", 1),
        ("claude", "no rollout found for thread id 01a0-live (code -32600)", "", 1),
        # Pi's exact declared signature must not be accepted as Claude's even
        # though it shares the "no session found" substring.
        ("claude", "No session found matching '01a0-live'", "", 1),
        # Claude's THR-200-era generic legacy markers carry no immutable
        # producer/CLI evidence and were REMOVED — each stays a negative.
        ("claude", "session not found", "", 1),
        ("claude", "no session found", "", 1),
        ("claude", "could not find session", "", 1),
        ("claude", "no such session", "", 1),
        ("claude", "no conversation found", "", 1),  # bare marker, no id
        ("claude", "no conversation was found", "", 1),  # near-miss marker
        # Wrong attempted id: the signature names a DIFFERENT session.
        ("claude", "No conversation found with session ID: 01a0-OTHER", "", 1),
        ("codex", "no rollout found for thread id 01a0-OTHER (code -32600)", "", 1),
        ("pi", "No session found matching '01a0-OTHER'", "", 1),
        # Missing id: signature present without any id binding.
        ("claude", "No conversation found with session ID:", "", 1),
        ("codex", "no rollout found for thread id (code -32600)", "", 1),
        ("pi", "No session found matching ''", "", 1),
        # Prefix / suffix near-matches on the attempted id.
        ("claude", "No conversation found with session ID: x01a0-live", "", 1),
        ("claude", "No conversation found with session ID: 01a0-liveX", "", 1),
        ("codex", "no rollout found for thread id x01a0-live (code -32600)", "", 1),
        ("codex", "no rollout found for thread id 01a0-liveX (code -32600)", "", 1),
        ("pi", "No session found matching 'x01a0-live'", "", 1),
        ("pi", "No session found matching '01a0-liveX'", "", 1),
        # Complete-line termination (TASK-6019 [HIGH]): the signature plus the
        # attempted id must form the COMPLETE observed provider stderr line
        # (`No conversation found with session ID: <attempted-id>`). The old \b
        # word-boundary terminator accepted punctuation-led suffixes (e.g.
        # attempted `01a0-live` vs stderr `... session ID: 01a0-live-suffix`);
        # hyphen + multiple punctuation suffixes, and unrelated prefix/suffix
        # text on the signature line, must never classify.
        ("claude", "No conversation found with session ID: 01a0-live-suffix", "", 1),
        ("claude", "No conversation found with session ID: 01a0-live-", "", 1),
        ("claude", "No conversation found with session ID: 01a0-live.extra", "", 1),
        ("claude", "No conversation found with session ID: 01a0-live:extra", "", 1),
        ("claude", "No conversation found with session ID: 01a0-live!", "", 1),
        ("claude", "No conversation found with session ID: 01a0-live,next", "", 1),
        ("claude", "No conversation found with session ID: 01a0-live_under", "", 1),
        ("claude", "No conversation found with session ID: 01a0-live-extra.suffix", "", 1),
        ("claude", "No conversation found with session ID: 01a0-live trailing words", "", 1),
        ("claude", "Error: No conversation found with session ID: 01a0-live", "", 1),
        ("claude", "some prefix No conversation found with session ID: 01a0-live", "", 1),
        ("claude", "prefix No conversation found with session ID: 01a0-live suffix", "", 1),
        ("claude", "No conversation found with session ID: 01a0-live-suffix\n"
                    "[debug] exiting", "", 1),
        # Same-physical-line constraint (TASK-6024/THR-200 continuation):
        # the observed signature and the attempted id must be on the SAME
        # stderr LINE — the old `\s*` gap between them also consumed LF/CRLF,
        # accepting split-line forms where the signature terminates line N
        # and the id starts line N+1. LF, CRLF, and whitespace-indented
        # split-line signature/ID forms must never classify.
        ("claude", "No conversation found with session ID:\n01a0-live", "", 1),
        ("claude", "No conversation found with session ID:\r\n01a0-live", "", 1),
        ("claude", "No conversation found with session ID:\n  01a0-live", "", 1),
        ("claude", "  No conversation found with session ID:\n01a0-live", "", 1),
        ("claude", "  No conversation found with session ID:\r\n\t01a0-live", "", 1),
        # TASK-6028 [HIGH] six probes + symmetric broadening: codex/pi must
        # ALSO anchor the COMPLETE observed physical stderr line with
        # horizontal whitespace only — `\s` must never consume LF/CRLF and
        # arbitrary same-line prefix/suffix must never classify (the old
        # unanchored `re.search` + `\s*` accepted all of these as eviction,
        # wrongly invalidating durable state and fresh-retrying).
        #
        # Split signature/ID forms: LF, CRLF, and whitespace-indented splits
        # (both the bare signature and the observed codex envelope form).
        ("codex", "no rollout found for thread id\n01a0-live (code -32600)", "", 1),
        ("codex", "Error: thread/resume: thread/resume failed: no rollout "
                  "found for thread id\n01a0-live (code -32600)", "", 1),
        ("codex", "Error: thread/resume: thread/resume failed: no rollout "
                  "found for thread id\r\n01a0-live (code -32600)", "", 1),
        ("codex", "Error: thread/resume: thread/resume failed: no rollout "
                  "found for thread id\n  01a0-live (code -32600)", "", 1),
        ("pi", "No session found matching\n'01a0-live'", "", 1),
        ("pi", "No session found matching\r\n'01a0-live'", "", 1),
        ("pi", "No session found matching\n  '01a0-live'", "", 1),
        # Arbitrary unrelated same-line prefix / suffix (bare + enveloped).
        ("codex", "boom no rollout found for thread id 01a0-live (code -32600)", "", 1),
        ("codex", "boom: Error: thread/resume: thread/resume failed: no "
                  "rollout found for thread id 01a0-live (code -32600)", "", 1),
        ("codex", "no rollout found for thread id 01a0-live (code -32600) boom", "", 1),
        ("codex", "Error: thread/resume: thread/resume failed: no rollout "
                  "found for thread id 01a0-live (code -32600) boom", "", 1),
        ("pi", "boom No session found matching '01a0-live'", "", 1),
        ("pi", "No session found matching '01a0-live' boom", "", 1),
        # The observed codex contract is the COMPLETE line including the CLI
        # envelope `Error: thread/resume: thread/resume failed: ` — a bare
        # signature without the envelope, and invented same-line wrapper
        # text on the observed pi line, never classify (fail closed).
        ("codex", "no rollout found for thread id 01a0-live (code -32600)", "", 1),
        ("pi", "Error: no session found matching '01a0-live' "
               "(the session file may have expired)", "", 1),
        # Wrong / missing / prefix / suffix attempted id on the OBSERVED
        # enveloped codex line (id binding, not envelope presence, decides).
        ("codex", "Error: thread/resume: thread/resume failed: no rollout "
                  "found for thread id 01a0-OTHER (code -32600)", "", 1),
        ("codex", "Error: thread/resume: thread/resume failed: no rollout "
                  "found for thread id (code -32600)", "", 1),
        ("codex", "Error: thread/resume: thread/resume failed: no rollout "
                  "found for thread id x01a0-live (code -32600)", "", 1),
        ("codex", "Error: thread/resume: thread/resume failed: no rollout "
                  "found for thread id 01a0-liveX (code -32600)", "", 1),
        ("pi", "No session found matching '01a0-live-suffix'", "", 1),
        ("pi", "No session found matching 'x01a0-live-suffix'", "", 1),
        # An otherwise matching marker EMBEDDED in auth/quota/transport
        # output on the signature line is not the provider declaring the
        # session missing.
        ("claude", "API Error: 401 Unauthorized: no conversation found with "
                    "session id 01a0-live", "", 1),
        ("codex", "API Error: 429 rate limit exceeded: no rollout found for "
                   "thread id 01a0-live", "", 1),
        ("codex", "Error: authentication failed for thread id 01a0-live "
                   "(code -32600)", "", 1),
        ("pi", "Connection timed out: no session found matching '01a0-live' "
                "is unavailable", "", 1),
        # An otherwise complete eviction line accompanied by auth/quota/
        # transport text anywhere in stderr is not eviction either.
        ("codex", "Error: thread/resume: thread/resume failed: no rollout "
                  "found for thread id 01a0-live (code -32600)\n"
                  "Error: 429 rate limit exceeded", "", 1),
        ("pi", "Error: 401 unauthorized: no session found matching '01a0-live'", "", 1),
        # Wrong return code with the exact signature.
        ("codex", "no rollout found for thread id 01a0-live (code -32600)", "", 2),
        ("pi", "No session found matching '01a0-live'", "", 3),
        ("claude", "No conversation found with session ID: 01a0-live", "", 2),
        # stdout-only text (signature on the wrong stream) never classifies;
        # the failure envelope is stderr-derived so it stays clean too.
        ("codex", "", "no rollout found for thread id 01a0-live (code -32600)", 1),
        ("pi", "", "No session found matching '01a0-live'", 1),
        ("claude", "", "No conversation found with session ID: 01a0-live", 1),
        # Malformed / near-match signatures.
        ("codex", "no rollout found for thread id 01a0-live", "", 1),  # no code
        ("pi", "No session found", "", 1),  # missing "matching"
        # Auth / quota / transport / generic failures.
        ("codex", "API Error: 401 Unauthorized", "", 1),
        ("pi", "API Error: 429 Overloaded", "", 1),
        ("claude", "Connection reset by peer", "", 1),
        ("codex", "panic: runtime error", "", 1),
        # opencode 1.18.25 negatives (TASK-6080 audit): wrong rc, stdout-only
        # text, same-line prefix/suffix, split LF/CRLF forms, unrelated
        # stderr, and the empty-id silent-fresh case never classify. The
        # attempted id is NOT echoed by opencode, so these negatives carry
        # the same 01a0-live placeholder the other executors use.
        ("opencode", "Error: Session not found", "", 0),   # wrong rc (fresh path)
        ("opencode", "Error: Session not found", "", 2),   # wrong rc
        ("opencode", "", "Error: Session not found", 1),   # stdout-only
        ("opencode", "Error: Session not found.", "", 1),  # punctuation suffix
        ("opencode", "Error: Session not found - retry later", "", 1),
        ("opencode", "xError: Session not found", "", 1),  # same-line prefix
        ("opencode", "prefix Error: Session not found", "", 1),
        ("opencode", "Error: Session not foundx", "", 1),  # same-line suffix
        ("opencode", " Error: Session not found", "", 1),  # whitespace prefix
        ("opencode", "Error: Session not found\t", "", 1),  # whitespace suffix
        ("opencode", "Error: Session\nnot found", "", 1),  # LF split
        ("opencode", "Error: Session\r\nnot found", "", 1),  # CRLF split
        ("opencode", "401 unauthorized: Error: Session not found", "", 1),
        ("opencode", "Error: Session not found (quota exceeded)", "", 1),
        ("opencode", "transport error: Error: Session not found", "", 1),
        ("claude", "Error: Session not found", "", 1),  # wrong executor
        ("opencode", "Error: Failed to change directory to /nonexistent", "", 1),
        ("opencode", "agent \"nosuch\" not found. Falling back to default", "", 0),
        ("opencode", "sessionID", "", 1),  # unrelated word on the line
    ],
)
async def test_eviction_negative_matrix_never_invalidates_or_retries(
    tmp_path, monkeypatch, executor, stderr, stdout, rc,
):
    """Only the exact proven executor/rc/stream/signature classifies as
    eviction. Everything else — wrong executor, wrong rc, stdout-only text,
    near-matches, auth/quota/transport/generic — leaves the resume state
    untouched and never fires the fresh retry."""
    sid = "01a0-live"
    # Production-shaped failure envelope: _run_command derives error from
    # ``full_stderr or full_stdout``, so a stdout-only signature WOULD reach
    # the ``error`` field — the classifier must still reject it (stderr-only).
    stream_text = stderr or stdout
    envelope = f"Command exited with code {rc}"
    if stream_text:
        envelope += f": {stream_text}"
    failure = _FakeResult(False, error=envelope, agent_session_id=None,
                          stderr_tail=stderr, stdout_tail=stdout, returncode=rc)
    db, fake = await _run_reply(
        tmp_path, monkeypatch, executor,
        stored_sid=sid, last_seq=1,
        fake=_RecordingExec([failure]),
    )
    # Exactly ONE attempt — the resume ran but no eviction retry fired.
    assert len(fake.calls) == 1
    assert fake.calls[0].get("resume_session_id") == sid
    # Resume state untouched: id preserved, watermark unadvanced.
    stored, seq = db.get_thread_session("THR-001", "alice")
    assert stored == sid and seq == 1
    actions = {r["action"] for r in db.get_audit_logs("THR-001")}
    assert "agent_session_evicted_fallback" not in actions


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


# ═══════════════════════════════════════════════════════════════════════
# Strict no-message-omission (TASK-5989): delta resume ONLY when the ENTIRE
# required post-watermark range is proven present and contiguous at the
# production seam (real Database + run_invocation, uncapped load +
# independent authoritative max-seq proof).
# ═══════════════════════════════════════════════════════════════════════


def _run_reply_at_seq(tmp_path, monkeypatch, executor, stored_sid, last_seq,
                      triggering_seq, fake, db=None):
    """Production-seam harness: real Database + real run_invocation with a
    caller-chosen triggering seq (the caller seeds the transcript and the
    queued REPLY delivery state first, optionally passing its own ``db``)."""
    from runtime.daemon.thread_runner import run_invocation
    if db is None:
        db = Database(tmp_path / "happyranch.db")
        db.insert_thread(ThreadRecord(id="THR-001", subject="x"))
        db.add_thread_participant("THR-001", "alice", added_by="founder")
    if stored_sid is not None:
        db.update_thread_session("THR-001", "alice",
                                 agent_session_id=stored_sid,
                                 last_resumed_seq=last_seq)
    inv = _seed_queued_reply(db, "THR-001", "alice",
                             triggering_seq=triggering_seq)
    _write_agent(tmp_path, executor)
    import runtime.daemon.thread_runner as runner_mod
    monkeypatch.setattr(runner_mod, "_build_executor_for_provider",
                        lambda provider, settings, paths: fake)
    org = _Org(db=db, root=tmp_path)
    # asyncio coroutine wrapper so the helper reads like the others.
    return run_invocation(org_state=org,
                          invocation_token=inv.invocation_token,
                          settings=Settings()), db, fake


@pytest.mark.asyncio
async def test_over_10k_complete_transcript_resumes_with_delta(tmp_path, monkeypatch):
    """>10,000-message transcript: the old first-10,000 load cap would have
    truncated the canonical range required after the watermark (10,001..
    10,500). With the uncapped load + independent max-seq proof, EVERY
    required claimed sequence is present and contiguous, so the delta is
    authorized and ships exactly the required range — no omission."""
    db = Database(tmp_path / "happyranch.db")
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))
    db.add_thread_participant("THR-001", "alice", added_by="founder")
    _bulk_append_messages(db, "THR-001", 1, 10500)
    fake = _RecordingExec([_FakeResult(True, agent_session_id="01a0-big")])
    coro, db, fake = _run_reply_at_seq(
        tmp_path, monkeypatch, "codex",
        stored_sid="01a0-big", last_seq=10000, triggering_seq=10500, fake=fake,
        db=db,
    )
    await coro
    # Delta resume used (proof passed).
    assert fake.calls[0].get("resume_session_id") == "01a0-big"
    delta = fake.calls[0]["prompt"]
    assert "New activity since your last turn follows" in delta
    bodies = _bodies_in(delta)
    assert len(bodies) == 500
    assert "msg-10001" in bodies and "msg-10500" in bodies
    assert max(int(b[4:]) for b in bodies) == 10500
    assert min(int(b[4:]) for b in bodies) == 10001  # nothing below the watermark
    # Watermark advanced to the proven transcript max after a successful turn.
    stored, seq = db.get_thread_session("THR-001", "alice")
    assert stored == "01a0-big" and seq == 10500


@pytest.mark.asyncio
async def test_over_10k_missing_internal_seq_falls_back_to_full_transcript(
    tmp_path, monkeypatch,
):
    """A hole in the required post-watermark range (seq 10300 absent from the
    canonical transcript) must fail closed: NO delta resume — the runner ships
    the genuinely complete canonical full transcript fresh, so the missing
    sequence can never be silently omitted by a delta."""
    db = Database(tmp_path / "happyranch.db")
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))
    db.add_thread_participant("THR-001", "alice", added_by="founder")
    _bulk_append_messages(db, "THR-001", 1, 10500)
    db._conn.execute(
        "DELETE FROM thread_messages WHERE thread_id = ? AND seq = 10300",
        ("THR-001",),
    )
    db._conn.commit()
    fake = _RecordingExec([_FakeResult(True, agent_session_id="01a0-big")])
    coro, db, fake = _run_reply_at_seq(
        tmp_path, monkeypatch, "codex",
        stored_sid="01a0-big", last_seq=10000, triggering_seq=10500, fake=fake,
        db=db,
    )
    await coro
    # No delta resume — the completeness proof failed closed.
    assert "resume_session_id" not in fake.calls[0]
    full = fake.calls[0]["prompt"]
    assert "Full message history follows" in full
    bodies = _bodies_in(full)
    assert "msg-00001" in bodies and "msg-10001" in bodies and "msg-10500" in bodies
    assert "msg-10300" not in bodies  # genuinely absent from the canonical transcript
    assert len(bodies) == 10499  # every other canonical sequence rendered


@pytest.mark.asyncio
async def test_claim_end_beyond_transcript_max_fails_closed(tmp_path, monkeypatch):
    """A claimed REPLY whose inclusive end (seq 2) is NOT present in the
    canonical transcript (deleted after the wake was queued) must never
    resume with a delta — the claim references a sequence the proof cannot
    supply, so the runner fails closed to the full canonical prompt."""
    db = Database(tmp_path / "happyranch.db")
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))
    db.add_thread_participant("THR-001", "alice", added_by="founder")
    db.append_thread_message(thread_id="THR-001", speaker="founder",
                             kind=ThreadMessageKind.MESSAGE, body_markdown="m1")
    db.append_thread_message(thread_id="THR-001", speaker="founder",
                             kind=ThreadMessageKind.MESSAGE, body_markdown="m2 newest")
    db.update_thread_session("THR-001", "alice", agent_session_id="01a0-claim",
                             last_resumed_seq=1)
    inv = _seed_queued_reply(db, "THR-001", "alice", triggering_seq=2)
    db._conn.execute(
        "DELETE FROM thread_messages WHERE thread_id = ? AND seq = 2",
        ("THR-001",),
    )
    db._conn.commit()
    _write_agent(tmp_path, "codex")
    from runtime.daemon.thread_runner import run_invocation
    import runtime.daemon.thread_runner as runner_mod
    fake = _RecordingExec([_FakeResult(True, agent_session_id="01a0-claim")])
    monkeypatch.setattr(runner_mod, "_build_executor_for_provider",
                        lambda provider, settings, paths: fake)
    org = _Org(db=db, root=tmp_path)
    await run_invocation(org_state=org, invocation_token=inv.invocation_token,
                         settings=Settings())
    assert "resume_session_id" not in fake.calls[0]
    full = fake.calls[0]["prompt"]
    assert "Full message history follows" in full
    assert "m1" in full and "m2 newest" not in full  # canonical truth


@pytest.mark.asyncio
@pytest.mark.parametrize("last_seq", [2, 3])
async def test_equal_and_ahead_watermarks_never_resume_delta(
    tmp_path, monkeypatch, last_seq,
):
    """Equal (watermark == transcript max) and ahead (watermark > max)
    watermarks leave an empty required post-watermark range — the proof fails
    closed and the runner ships the full prompt."""
    db = Database(tmp_path / "happyranch.db")
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))
    db.add_thread_participant("THR-001", "alice", added_by="founder")
    db.append_thread_message(thread_id="THR-001", speaker="founder",
                             kind=ThreadMessageKind.MESSAGE, body_markdown="m1")
    db.append_thread_message(thread_id="THR-001", speaker="founder",
                             kind=ThreadMessageKind.MESSAGE, body_markdown="m2 newest")
    fake = _RecordingExec([_FakeResult(True, agent_session_id="01a0-eq")])
    coro, db, fake = _run_reply_at_seq(
        tmp_path, monkeypatch, "codex",
        stored_sid="01a0-eq", last_seq=last_seq, triggering_seq=2, fake=fake,
        db=db,
    )
    await coro
    assert "resume_session_id" not in fake.calls[0]
    full = fake.calls[0]["prompt"]
    assert "Full message history follows" in full
    assert "m1" in full and "m2 newest" in full


@pytest.mark.asyncio
async def test_null_watermark_with_stored_session_never_resumes(
    tmp_path, monkeypatch,
):
    """A stored provider session with a NULL/zero watermark (last_resumed_seq
    = 0) is INELIGIBLE for resume (TASK-6007 HIGH 3): the runner must make a
    fresh invocation with the complete canonical transcript, never a delta
    against the stored provider id."""
    db = Database(tmp_path / "happyranch.db")
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))
    db.add_thread_participant("THR-001", "alice", added_by="founder")
    db.append_thread_message(thread_id="THR-001", speaker="founder",
                             kind=ThreadMessageKind.MESSAGE, body_markdown="m1")
    db.append_thread_message(thread_id="THR-001", speaker="founder",
                             kind=ThreadMessageKind.MESSAGE, body_markdown="m2 newest")
    fake = _RecordingExec([_FakeResult(True, agent_session_id="01a0-null")])
    coro, db, fake = _run_reply_at_seq(
        tmp_path, monkeypatch, "codex",
        stored_sid="01a0-null", last_seq=0, triggering_seq=2, fake=fake,
        db=db,
    )
    await coro
    assert "resume_session_id" not in fake.calls[0]
    full = fake.calls[0]["prompt"]
    assert "Full message history follows" in full
    assert "m1" in full and "m2 newest" in full


@pytest.mark.asyncio
@pytest.mark.parametrize("last_seq", [0, -1])
async def test_zero_and_negative_watermark_with_stored_session_never_resume(
    tmp_path, monkeypatch, last_seq,
):
    """Zero AND negative stored watermarks with a stored id are ineligible:
    fresh full canonical invocation, no resume, at the production seam."""
    db = Database(tmp_path / "happyranch.db")
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))
    db.add_thread_participant("THR-001", "alice", added_by="founder")
    db.append_thread_message(thread_id="THR-001", speaker="founder",
                             kind=ThreadMessageKind.MESSAGE, body_markdown="m1")
    db.append_thread_message(thread_id="THR-001", speaker="founder",
                             kind=ThreadMessageKind.MESSAGE, body_markdown="m2 newest")
    fake = _RecordingExec([_FakeResult(True, agent_session_id="01a0-zz")])
    coro, db, fake = _run_reply_at_seq(
        tmp_path, monkeypatch, "codex",
        stored_sid="01a0-zz", last_seq=last_seq, triggering_seq=2, fake=fake,
        db=db,
    )
    await coro
    assert "resume_session_id" not in fake.calls[0]
    full = fake.calls[0]["prompt"]
    assert "Full message history follows" in full
    assert "m1" in full and "m2 newest" in full


@pytest.mark.parametrize(
    "messages,last_seq,max_seq,expected",
    [
        # Complete required range → authorized.
        ([1, 2, 3, 4, 5], 2, 5, True),
        ([1, 2, 3], 1, 3, True),
        # Null/zero/negative watermark → fail closed (never a delta).
        ([1, 2, 3], 0, 3, False),
        ([1, 2, 3], -1, 3, False),
        ([1, 2, 3], 0, 0, False),
        ([], 0, 0, False),
        # Truncated load (does not reach the authoritative max) → fail closed.
        ([1, 2, 3, 4], 2, 5, False),
        # Internal hole in the required range → fail closed.
        ([1, 2, 3, 5], 2, 5, False),
        ([1, 3, 4, 5], 1, 5, False),          # hole right after the watermark
        # Equal / ahead watermark → empty required range → fail closed.
        ([1, 2, 3], 3, 3, False),
        ([1, 2, 3], 4, 3, False),
    ],
)
def test_delta_range_completeness_proof(messages, last_seq, max_seq, expected):
    """Unit pin for the completeness proof itself (the production-seam tests
    above prove the seam; this pins every branch of the proof incl. the
    truncation guard that the uncapped seam cannot reach)."""
    from runtime.daemon.thread_runner import _delta_range_is_complete
    msgs = [
        ThreadMessage(
            thread_id="THR-001", seq=s, speaker="founder",
            kind=ThreadMessageKind.MESSAGE, body_markdown=f"m{s}",
            created_at=datetime.now(timezone.utc),
        )
        for s in messages
    ]
    assert _delta_range_is_complete(msgs, last_seq=last_seq, max_seq=max_seq) is expected


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
async def test_opencode_fresh_first_wake_full_then_persists_new_id(
    tmp_path, monkeypatch,
):
    """A stored opencode session row + eligible positive watermark resumes
    with the delta; a fresh first wake (no stored id) ships the FULL
    canonical transcript and persists the minted session id for the next
    turn (TASK-6080)."""
    db, fake = await _run_reply(
        tmp_path, monkeypatch, "opencode",
        stored_sid=None, last_seq=0,
        fake=_RecordingExec([_FakeResult(True, agent_session_id="ses_fb433ade8ffeh55zGEozUE3mey")]),
    )
    assert "resume_session_id" not in fake.calls[0]
    full = fake.calls[0]["prompt"]
    assert "m1" in full and "m2 newest" in full  # complete canonical transcript
    stored, seq = db.get_thread_session("THR-001", "alice")
    assert stored == "ses_fb433ade8ffeh55zGEozUE3mey" and seq == 2


@pytest.mark.asyncio
async def test_opencode_stored_id_zero_watermark_stays_fresh(
    tmp_path, monkeypatch,
):
    """TASK-6007 HIGH 3 preserved for opencode: a stored provider id whose
    durable delivery watermark is <= 0 is INELIGIBLE — the wake ships the
    complete canonical full transcript, never a delta against the stored
    id (opencode ``-s ""`` would silently start fresh, so an empty/zero
    frontier must never wire resume)."""
    db, fake = await _run_reply(
        tmp_path, monkeypatch, "opencode",
        stored_sid="ses_stale-zero", last_seq=0,
        fake=_RecordingExec([_FakeResult(True, agent_session_id="ses_fb433ade8ffeh55zGEozUE3mey")]),
    )
    assert "resume_session_id" not in fake.calls[0]
    full = fake.calls[0]["prompt"]
    assert "m1" in full and "m2 newest" in full
    # The fresh minted id replaces the ineligible stored id.
    stored, seq = db.get_thread_session("THR-001", "alice")
    assert stored == "ses_fb433ade8ffeh55zGEozUE3mey" and seq == 2


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

    for cls_name in ("ClaudeExecutor", "CodexExecutor", "PiExecutor",
                      "OpencodeExecutor"):
        cls = getattr(ex_mod, cls_name)
        sig = inspect.signature(cls.run)
        param = sig.parameters["resume_session_id"]
        assert param.default is None, (
            f"{cls_name}.run resume_session_id default must be None"
        )
    for cls_name in ("GenericCliExecutor", "CustomAdapterExecutor"):
        cls = getattr(ex_mod, cls_name)
        sig = inspect.signature(cls.run)
        assert "resume_session_id" not in sig.parameters, (
            f"{cls_name}.run must not accept resume_session_id — its resume "
            f"contract is unproven and it stays fresh"
        )
