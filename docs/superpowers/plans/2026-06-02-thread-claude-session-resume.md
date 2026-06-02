# Thread `claude -p --resume` Session Reuse Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Claude-backed thread participants reuse their Claude session across turns via `claude -p --resume <session_id>`, shipping only the new-message delta each turn instead of re-tokenizing the full transcript and workspace `CLAUDE.md`.

**Architecture:** Persist a per-`(thread, agent)` Claude session id on `thread_participants`. Turn 1 (no stored id) runs the full prompt and captures Claude's `.session_id` from the JSON output. Turn 2+ invokes with `--resume <stored_id>` and a delta prompt containing only messages newer than the stored watermark. If `--resume` fails with a session-not-found signature, fall back transparently to a full-context fresh session. The SQLite transcript remains the canonical record; the session id is a pure optimization.

**Tech Stack:** Python 3.11+, SQLite (WAL), Pydantic v2, `claude -p --output-format json`. Scope is **threads only** — talks have no daemon-side per-turn executor loop (they are interactive founder↔agent sessions), so the issue's talk column is intentionally dropped. Resume is **Claude-only**; codex/opencode/pi participants are unchanged.

---

## Deviations from issue #53 (read before starting)

1. **Talks dropped.** Confirmed in code: `happyranch talk start` only creates a talk row (`src/cli.py:1751`); there is no daemon executor loop re-invoking `claude -p` per talk turn. The only per-turn executor loops are tasks (`orchestrator.py:521`, out of scope) and threads (`thread_runner.py:241`). The `talks.claude_session_id` column from the issue has no consumer and is omitted.

2. **Second column added: `last_resumed_seq`.** The issue lists only `claude_session_id`. Correct delta computation needs a watermark of "highest message seq this stored session has already seen," updated only on a successful turn. Without it, a turn that fails after a partial read would permanently orphan the messages it skipped from the resumed session. `last_resumed_seq` makes the delta self-healing: a failed turn doesn't advance the watermark, so the next successful resume re-includes the missed messages.

3. **The existing `session_id` plumbing is NOT Claude's session id.** `ExecutorResult.session_id` / `_run_command`'s `sid` is a HappyRanch-generated `sess-<uuid>` used only for SessionTracker/`/cancel` binding (`executors.py:179`). `claude -p` is never invoked with `--resume`/`--session-id` today, and Claude's own `.session_id` in the JSON output is currently discarded. We add a **new** field `ExecutorResult.claude_session_id` and never touch `session_id`.

4. **Delta prompt keeps the small per-turn doctrine + purpose note; it drops only the full transcript.** The 45 KB re-tokenization the issue targets is the workspace `CLAUDE.md` / system prompt, which `--resume` avoids at the session layer (it was loaded at session creation and is restored, not re-injected). The ~1 KB decline-by-default doctrine + invocation-token block stay in the delta because they are behaviorally load-bearing and negligible in cost. We do not strip them.

---

## File Structure

- **Modify `src/infrastructure/database.py`** — add two columns to the `thread_participants` `CREATE TABLE` (fresh DBs) AND to the idempotent `ALTER TABLE` migration block (existing DBs); add `get_thread_session` / `update_thread_session` accessors.
- **Modify `src/orchestrator/executors.py`** — add `ExecutorResult.claude_session_id`; add `_parse_claude_session_id`; add `resume_session_id` param to `ClaudeExecutor.run`; thread a `session_id_parser` through `_run_command`.
- **Modify `src/infrastructure/audit_logger.py`** — add `log_claude_session_reused` and `log_claude_session_evicted_fallback`.
- **Modify `src/daemon/thread_runner.py`** — add `build_thread_delta_prompt`; add `_is_session_not_found`; wire resume + capture + fallback into `run_invocation`.
- **Modify `CLAUDE.md`** — add a "Thread Claude-session resume" section with load-bearing invariants.
- **Tests:** `tests/test_thread_db.py`, `tests/test_executor.py`, `tests/test_audit_logger.py`, `tests/test_thread_runner.py`.

---

## Task 1: Schema columns + DB accessors

**Files:**
- Modify: `src/infrastructure/database.py:317-326` (CREATE TABLE thread_participants)
- Modify: `src/infrastructure/database.py:451-491` (idempotent ALTER block)
- Modify: `src/infrastructure/database.py:1902` (near `list_thread_participants` — add accessors)
- Test: `tests/test_thread_db.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_thread_db.py`:

```python
def test_thread_session_defaults_and_roundtrip(tmp_path):
    from src.infrastructure.database import Database
    from src.models import ThreadRecord

    db = Database(tmp_path / "happyranch.db")
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))
    db.add_thread_participant("THR-001", "alice", added_by="founder")

    # Default state: no stored session, watermark 0.
    assert db.get_thread_session("THR-001", "alice") == (None, 0)

    # Unknown participant also returns the safe default (no row).
    assert db.get_thread_session("THR-001", "ghost") == (None, 0)

    db.update_thread_session(
        "THR-001", "alice", claude_session_id="claude-sess-123", last_resumed_seq=7
    )
    assert db.get_thread_session("THR-001", "alice") == ("claude-sess-123", 7)

    # Eviction clears the id but the accessor still returns a safe tuple.
    db.update_thread_session(
        "THR-001", "alice", claude_session_id=None, last_resumed_seq=0
    )
    assert db.get_thread_session("THR-001", "alice") == (None, 0)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_thread_db.py::test_thread_session_defaults_and_roundtrip -v`
Expected: FAIL — `AttributeError: 'Database' object has no attribute 'get_thread_session'`.

- [ ] **Step 3: Add columns to the fresh-DB CREATE TABLE**

In `src/infrastructure/database.py`, change the `thread_participants` CREATE TABLE (currently lines 317-324) to:

```python
            CREATE TABLE IF NOT EXISTS thread_participants (
                thread_id TEXT NOT NULL,
                agent_name TEXT NOT NULL,
                added_at TEXT NOT NULL,
                added_by TEXT NOT NULL,
                claude_session_id TEXT,
                last_resumed_seq INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (thread_id, agent_name),
                FOREIGN KEY (thread_id) REFERENCES threads(id)
            );
```

- [ ] **Step 4: Add columns to the idempotent migration block**

In the idempotent ALTER loop (the `for ddl in (...)` block ending at line 491), append two entries before the closing `):`:

```python
            # Thread Claude-session resume (issue #53). claude_session_id holds
            # the resumable `claude -p --resume` session for this (thread, agent);
            # NULL means "no resumable session yet / evicted". last_resumed_seq is
            # the highest thread message seq this stored session has been shown —
            # the delta watermark, advanced only on a successful turn.
            "ALTER TABLE thread_participants ADD COLUMN claude_session_id TEXT",
            "ALTER TABLE thread_participants ADD COLUMN last_resumed_seq INTEGER NOT NULL DEFAULT 0",
```

- [ ] **Step 5: Add the accessors**

Insert after `list_thread_participants` (after line ~1916, before the next method) in `src/infrastructure/database.py`:

```python
    @_synchronized
    def get_thread_session(
        self, thread_id: str, agent_name: str
    ) -> tuple[str | None, int]:
        """Return (claude_session_id, last_resumed_seq) for a (thread, agent).

        Returns (None, 0) when the participant row is absent — the safe
        turn-1 default that drives a full-context first invocation.
        """
        cursor = self._conn.execute(
            "SELECT claude_session_id, last_resumed_seq FROM thread_participants "
            "WHERE thread_id = ? AND agent_name = ?",
            (thread_id, agent_name),
        )
        row = cursor.fetchone()
        if row is None:
            return (None, 0)
        return (row["claude_session_id"], row["last_resumed_seq"] or 0)

    @_synchronized
    def update_thread_session(
        self,
        thread_id: str,
        agent_name: str,
        *,
        claude_session_id: str | None,
        last_resumed_seq: int,
    ) -> None:
        """Persist the resumable session id + delta watermark for a participant."""
        self._conn.execute(
            "UPDATE thread_participants SET claude_session_id = ?, last_resumed_seq = ? "
            "WHERE thread_id = ? AND agent_name = ?",
            (claude_session_id, last_resumed_seq, thread_id, agent_name),
        )
        self._conn.commit()
```

- [ ] **Step 6: Run test to verify it passes**

Run: `uv run pytest tests/test_thread_db.py::test_thread_session_defaults_and_roundtrip -v`
Expected: PASS.

- [ ] **Step 7: Verify the migration is idempotent against a pre-existing DB**

Run: `uv run pytest tests/test_thread_db.py tests/test_database.py -v`
Expected: PASS (no `duplicate column name` errors; the `except sqlite3.OperationalError: pass` swallows re-adds).

- [ ] **Step 8: Commit**

```bash
git add src/infrastructure/database.py tests/test_thread_db.py
git commit -m "feat(threads): add claude_session_id + last_resumed_seq to thread_participants"
```

---

## Task 2: ExecutorResult.claude_session_id + ClaudeExecutor `--resume`

**Files:**
- Modify: `src/orchestrator/executors.py:19-39` (ExecutorResult), `:170-244` (_run_command), `:247-292` (ClaudeExecutor)
- Test: `tests/test_executor.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_executor.py`:

```python
@patch("src.orchestrator.executors.subprocess")
def test_claude_executor_captures_session_id_from_json(mock_subprocess, tmp_path, runtime):
    workspace = tmp_path / "dev_agent"
    workspace.mkdir()
    mock_subprocess.Popen.return_value = _popen_mock(
        stdout='{"type":"result","result":"ok","session_id":"claude-abc-123",'
               '"usage":{"input_tokens":10,"output_tokens":5},"model":"claude"}',
    )
    executor = ClaudeExecutor(claude_cli_path="claude", permission_mode="auto", settings=Settings(), paths=runtime)
    result = executor.run(workspace=workspace, prompt="x", timeout_seconds=30)

    assert result.success is True
    assert result.claude_session_id == "claude-abc-123"
    # The HappyRanch session id is unchanged and distinct.
    assert result.session_id != "claude-abc-123"


@patch("src.orchestrator.executors.subprocess")
def test_claude_executor_appends_resume_flag_when_requested(mock_subprocess, tmp_path, runtime):
    workspace = tmp_path / "dev_agent"
    workspace.mkdir()
    mock_subprocess.Popen.return_value = _popen_mock(
        stdout='{"type":"result","session_id":"claude-new-999"}',
    )
    executor = ClaudeExecutor(claude_cli_path="claude", permission_mode="auto", settings=Settings(), paths=runtime)
    result = executor.run(
        workspace=workspace, prompt="delta only", timeout_seconds=30,
        resume_session_id="claude-prior-555",
    )

    cmd = mock_subprocess.Popen.call_args[0][0]
    assert "--resume" in cmd
    assert cmd[cmd.index("--resume") + 1] == "claude-prior-555"
    # Resume may fork a new id; we capture whatever the JSON reports.
    assert result.claude_session_id == "claude-new-999"


@patch("src.orchestrator.executors.subprocess")
def test_claude_executor_omits_resume_flag_by_default(mock_subprocess, tmp_path, runtime):
    workspace = tmp_path / "dev_agent"
    workspace.mkdir()
    mock_subprocess.Popen.return_value = _popen_mock(stdout='{"session_id":"s"}')
    executor = ClaudeExecutor(claude_cli_path="claude", permission_mode="auto", settings=Settings(), paths=runtime)
    executor.run(workspace=workspace, prompt="x", timeout_seconds=30)
    assert "--resume" not in mock_subprocess.Popen.call_args[0][0]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_executor.py -k "session_id or resume" -v`
Expected: FAIL — `AttributeError: 'ExecutorResult' object has no attribute 'claude_session_id'` / `TypeError: run() got an unexpected keyword argument 'resume_session_id'`.

- [ ] **Step 3: Add the `claude_session_id` field to ExecutorResult**

In `src/orchestrator/executors.py`, add to the `ExecutorResult` dataclass (after `token_usage`, line 39):

```python
    # Claude's own session id parsed from `--output-format json` output. Distinct
    # from `session_id` (the HappyRanch sess-<uuid> used for SessionTracker). Used
    # to resume thread sessions via `claude -p --resume` (issue #53). None for
    # non-Claude executors and on parse failure.
    claude_session_id: str | None = None
```

- [ ] **Step 4: Add the session-id parser**

Add near `_parse_claude_usage` in `src/orchestrator/executors.py`:

```python
def _parse_claude_session_id(stdout: str) -> str | None:
    """Extract `.session_id` from Claude Code's `--output-format json` stdout.

    Best-effort: returns None on empty/invalid/missing-field output. The
    session id is an optimization (resume), never a correctness dependency,
    so a parse miss simply forces a fresh session next turn.
    """
    if not stdout or not stdout.strip():
        return None
    try:
        obj = json.loads(stdout.strip())
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None
    sid = obj.get("session_id")
    return sid if isinstance(sid, str) and sid else None
```

- [ ] **Step 5: Thread a `session_id_parser` through `_run_command`**

Change the `_run_command` signature (line 170) to add a parameter after `usage_parser`:

```python
    usage_parser: Callable[[str], "TokenUsage | None"] | None = None,
    session_id_parser: Callable[[str], "str | None"] | None = None,
```

Then in the success branch (the `return ExecutorResult(success=True, ...)` near line 236), populate the new field. Replace that final `return ExecutorResult(...)` with:

```python
    claude_session_id: str | None = None
    if session_id_parser is not None:
        try:
            claude_session_id = session_id_parser(full_stdout)
        except Exception as exc:  # parser must never break the task
            logger.warning("session-id parser raised: %s", exc)
            claude_session_id = None
    return ExecutorResult(
        success=True,
        duration_seconds=int(time.monotonic() - start_time),
        session_id=sid,
        returncode=proc.returncode,
        stdout_tail=stdout_tail,
        stderr_tail=stderr_tail,
        token_usage=token_usage,
        claude_session_id=claude_session_id,
    )
```

- [ ] **Step 6: Add `resume_session_id` to ClaudeExecutor.run and pass the parser**

Replace `ClaudeExecutor.run` (lines 254-292) with:

```python
    def run(
        self,
        workspace: Path,
        prompt: str,
        session_id: str | None = None,
        timeout_seconds: int = 1800,
        on_started: Callable[[int], None] | None = None,
        resume_session_id: str | None = None,
    ) -> ExecutorResult:
        # The workspace's .claude/settings.json `permissions.allow` list is not
        # honoured in headless `-p` mode (observed empirically: Claude Code
        # 2.1.105 records `command_permissions.allowedTools: []` regardless of
        # what's in settings.json). Pass --allowedTools on the CLI instead so
        # agents can reliably call `happyranch ...` callbacks. Per-agent extras come
        # from the optional ``allow_rules:`` list in the agent's frontmatter
        # at ``<runtime>/org/agents/<name>.md``.
        from src.orchestrator.workspace_adapters import allow_rules_for_agent

        # Workspace layout is `<runtime>/workspaces/<agent_name>`, so the
        # directory name is the canonical agent identifier.
        allowed = " ".join(allow_rules_for_agent(self._paths, workspace.name, cli=True))
        cmd = [
            self._cli_path,
            "-p",
            prompt,
            "--permission-mode",
            self._permission_mode,
            "--allowedTools",
            allowed,
            "--output-format",
            "json",
        ]
        # Resume an existing Claude session (issue #53). Used by thread turn 2+
        # so the system prompt + transcript stay in session memory and only the
        # delta is shipped. Resume may fork a new session id; the caller reads
        # ExecutorResult.claude_session_id and persists whatever comes back.
        if resume_session_id:
            cmd += ["--resume", resume_session_id]
        return _run_command(
            cmd,
            workspace,
            session_id,
            timeout_seconds,
            on_started=on_started,
            usage_parser=_parse_claude_usage,
            session_id_parser=_parse_claude_session_id,
        )
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `uv run pytest tests/test_executor.py -v`
Expected: PASS (new tests + all existing executor tests, including `test_claude_executor_launches_with_current_semantics`).

- [ ] **Step 8: Commit**

```bash
git add src/orchestrator/executors.py tests/test_executor.py
git commit -m "feat(executor): capture claude session_id + support --resume"
```

---

## Task 3: Audit-logger kinds

**Files:**
- Modify: `src/infrastructure/audit_logger.py` (near the thread methods, after `log_thread_dispatch`)
- Test: `tests/test_audit_logger.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_audit_logger.py`:

```python
def test_log_claude_session_reused_and_evicted(tmp_path):
    from src.infrastructure.database import Database
    from src.infrastructure.audit_logger import AuditLogger

    db = Database(tmp_path / "happyranch.db")
    audit = AuditLogger(db)

    audit.log_claude_session_reused(
        "THR-001", agent_name="alice",
        claude_session_id="claude-abc", triggering_seq=4,
    )
    audit.log_claude_session_evicted_fallback(
        "THR-001", agent_name="alice",
        stale_session_id="claude-old", error="No conversation found",
    )

    rows = db.get_audit_logs("THR-001")
    actions = {r.action for r in rows}
    assert "claude_session_reused" in actions
    assert "claude_session_evicted_fallback" in actions
    reused = next(r for r in rows if r.action == "claude_session_reused")
    assert reused.payload["claude_session_id"] == "claude-abc"
    assert reused.payload["triggering_seq"] == 4
```

> Note: confirm the audit-row accessor name. `get_audit_logs(task_id)` and `row.action` / `row.payload` are the documented shapes (see CLAUDE.md "Audit `task_id` overload"). If `test_audit_logger.py` uses a different read helper (e.g. raw SQL), mirror that file's existing convention instead.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_audit_logger.py::test_log_claude_session_reused_and_evicted -v`
Expected: FAIL — `AttributeError: 'AuditLogger' object has no attribute 'log_claude_session_reused'`.

- [ ] **Step 3: Add the two methods**

Insert in `src/infrastructure/audit_logger.py` after `log_thread_dispatch` (around line 750):

```python
    def log_claude_session_reused(
        self,
        thread_id: str,
        *,
        agent_name: str,
        claude_session_id: str,
        triggering_seq: int,
    ) -> None:
        """Informational: a thread turn successfully resumed a Claude session."""
        self._db.insert_audit_log(
            task_id=thread_id,
            agent=agent_name,
            action="claude_session_reused",
            payload={
                "claude_session_id": claude_session_id,
                "triggering_seq": triggering_seq,
            },
        )

    def log_claude_session_evicted_fallback(
        self,
        thread_id: str,
        *,
        agent_name: str,
        stale_session_id: str,
        error: str,
    ) -> None:
        """Fires when `--resume` reported session-not-found and we rebuilt a
        fresh full-context session. Watch frequency: high rates mean Claude's
        local session TTL is shorter than our typical inter-turn gap."""
        self._db.insert_audit_log(
            task_id=thread_id,
            agent=agent_name,
            action="claude_session_evicted_fallback",
            payload={
                "stale_session_id": stale_session_id,
                "error": error[:500],
            },
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_audit_logger.py::test_log_claude_session_reused_and_evicted -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/infrastructure/audit_logger.py tests/test_audit_logger.py
git commit -m "feat(audit): add claude_session_reused + claude_session_evicted_fallback"
```

---

## Task 4: Delta prompt builder

**Files:**
- Modify: `src/daemon/thread_runner.py` (add `build_thread_delta_prompt` after `build_thread_prompt`, ~line 138)
- Test: `tests/test_thread_runner.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_thread_runner.py`:

```python
def test_build_delta_prompt_excludes_old_history_includes_new():
    from datetime import datetime, timezone
    from src.daemon.thread_runner import build_thread_delta_prompt
    from src.models import ThreadRecord, ThreadMessage, ThreadMessageKind

    thread = ThreadRecord(
        id="THR-001", subject="Refund policy",
        started_at=datetime(2026, 5, 13, tzinfo=timezone.utc),
    )
    new_msgs = [
        ThreadMessage(
            thread_id="THR-001", seq=12, speaker="bob",
            kind=ThreadMessageKind.MESSAGE, body_markdown="brand new point",
        ),
    ]
    triggering = new_msgs[0]
    prompt = build_thread_delta_prompt(
        thread=thread, new_messages=new_msgs,
        invocation_token="TOK-XYZ", invoked_agent="alice",
        purpose="reply", triggering_seq=12, triggering_message=triggering,
    )
    # Delta carries the new message + token + decline doctrine.
    assert "brand new point" in prompt
    assert "TOK-XYZ" in prompt
    assert "Decline-by-Default" in prompt
    # It must NOT re-ship the full transcript header / participant roster.
    assert "Full message history follows" not in prompt
    assert "Participants:" not in prompt
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_thread_runner.py::test_build_delta_prompt_excludes_old_history_includes_new -v`
Expected: FAIL — `ImportError: cannot import name 'build_thread_delta_prompt'`.

- [ ] **Step 3: Implement the delta builder**

Add to `src/daemon/thread_runner.py` immediately after `build_thread_prompt` (after line 138):

```python
def build_thread_delta_prompt(
    *,
    thread: ThreadRecord,
    new_messages: list[ThreadMessage],
    invocation_token: str,
    invoked_agent: str,
    purpose: str,
    triggering_seq: int,
    triggering_message: "ThreadMessage | None",
) -> str:
    """Turn 2+ prompt for a resumed Claude session (issue #53).

    The full transcript, participant roster, and workspace CLAUDE.md are
    already in the resumed session's memory — we ship only the messages newer
    than the stored watermark plus the per-turn doctrine, purpose note, and
    single-use invocation token. `new_messages` is the delta the caller
    computed (seq > last_resumed_seq).
    """
    note = _purpose_note(
        purpose, triggering_seq, invoked_agent,
        triggering_message=triggering_message,
    )
    doctrine = _decline_by_default_doctrine() if purpose == "reply" else ""
    delta = "\n".join(_render_message(m) for m in new_messages)
    return (
        f"{doctrine}"
        f"Continuing thread {thread.id}: \"{thread.subject}\". "
        f"New activity since your last turn follows.\n\n"
        f"---\n{delta}\n\n"
        f"You have been invoked because:\n  {note}\n\n"
        f"Your invocation_token for this turn is: {invocation_token}\n"
        f"Include this token in every callback payload (reply, decline,\n"
        f"dispatch). It authorizes this single turn and is single-use for the\n"
        f"terminal callback (reply/decline).\n\n"
        f"Consult `protocol/skills/thread/SKILL.md` and respond.\n"
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_thread_runner.py::test_build_delta_prompt_excludes_old_history_includes_new -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/daemon/thread_runner.py tests/test_thread_runner.py
git commit -m "feat(threads): add delta prompt builder for resumed sessions"
```

---

## Task 5: Wire resume + capture + fallback into `run_invocation`

**Files:**
- Modify: `src/daemon/thread_runner.py` (add `_is_session_not_found`; rework the run section of `run_invocation`, lines ~192-261)
- Test: `tests/test_thread_runner.py`

This is the integration task. The control flow inside `run_invocation`, after the executor is built and timeout resolved, becomes:

1. Determine `is_claude = executor_name == "claude"`.
2. Read `(stored_sid, last_seq) = db.get_thread_session(...)` (claude only; else `(None, 0)`).
3. If `is_claude and stored_sid`: compute `new_messages = [m for m in messages if m.seq > last_seq]`, build delta prompt, set `resume = stored_sid`. Else: build full prompt, `resume = None`.
4. Run the executor (passing `resume_session_id` only when set).
5. If `is_claude and resume and not result.success and _is_session_not_found(result)`: audit `claude_session_evicted_fallback`, rebuild full prompt, run again with no resume — this becomes the final `result`.
6. If `is_claude and result.success and result.claude_session_id`: persist `update_thread_session(..., last_resumed_seq=max_shown_seq)`; if `resume` was used, audit `claude_session_reused`.
7. Existing post-run token-state inspection / auto-decline runs unchanged on the final `result`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_thread_runner.py`. These reuse the existing `FakeOrgState`; add a configurable fake executor.

```python
class _ResumeRecordingExec:
    """Fake executor that records run() calls and returns scripted results."""
    def __init__(self, scripted):
        self._scripted = list(scripted)
        self.calls = []

    def run(self, **kwargs):
        self.calls.append(kwargs)
        return self._scripted.pop(0)


def _ok_result(claude_session_id="claude-new"):
    r = FakeExecutorResult(success=True)
    r.claude_session_id = claude_session_id
    return r


@pytest.mark.asyncio
async def test_turn1_full_prompt_captures_session_id(tmp_path, monkeypatch):
    db = Database(tmp_path / "happyranch.db")
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))
    db.add_thread_participant("THR-001", "alice", added_by="founder")
    db.append_thread_message(
        thread_id="THR-001", speaker="founder",
        kind=ThreadMessageKind.MESSAGE, body_markdown="hello",
    )
    inv = db.mint_thread_invocation(
        thread_id="THR-001", agent_name="alice",
        triggering_seq=1, purpose=ThreadInvocationPurpose.REPLY,
    )
    ws = tmp_path / "workspaces" / "alice"
    ws.mkdir(parents=True)
    (ws / "agent.yaml").write_text("executor: claude\n")

    import src.daemon.thread_runner as runner_mod
    fake = _ResumeRecordingExec([_ok_result("claude-sess-001")])
    monkeypatch.setattr(
        runner_mod, "_build_executor_for_provider",
        lambda provider, settings, paths: fake,
    )
    org = FakeOrgState(db=db, root=tmp_path)
    await run_invocation(org_state=org, invocation_token=inv.invocation_token, settings=Settings())

    # Turn 1: full prompt, no --resume, session id captured.
    assert "resume_session_id" not in fake.calls[0]
    sid, seq = db.get_thread_session("THR-001", "alice")
    assert sid == "claude-sess-001"
    assert seq == 1


@pytest.mark.asyncio
async def test_turn2_resumes_with_delta(tmp_path, monkeypatch):
    db = Database(tmp_path / "happyranch.db")
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))
    db.add_thread_participant("THR-001", "alice", added_by="founder")
    db.append_thread_message(thread_id="THR-001", speaker="founder",
                             kind=ThreadMessageKind.MESSAGE, body_markdown="m1")
    db.append_thread_message(thread_id="THR-001", speaker="bob",
                             kind=ThreadMessageKind.MESSAGE, body_markdown="m2 newest")
    # Stored session from a prior turn that saw seq 1.
    db.update_thread_session("THR-001", "alice", claude_session_id="claude-prior", last_resumed_seq=1)
    inv = db.mint_thread_invocation(
        thread_id="THR-001", agent_name="alice",
        triggering_seq=2, purpose=ThreadInvocationPurpose.REPLY,
    )
    ws = tmp_path / "workspaces" / "alice"
    ws.mkdir(parents=True)
    (ws / "agent.yaml").write_text("executor: claude\n")

    import src.daemon.thread_runner as runner_mod
    fake = _ResumeRecordingExec([_ok_result("claude-prior")])
    monkeypatch.setattr(runner_mod, "_build_executor_for_provider",
                        lambda provider, settings, paths: fake)
    org = FakeOrgState(db=db, root=tmp_path)
    await run_invocation(org_state=org, invocation_token=inv.invocation_token, settings=Settings())

    assert fake.calls[0].get("resume_session_id") == "claude-prior"
    # Delta prompt contains only the new message, not the old one.
    delta_prompt = fake.calls[0]["prompt"]
    assert "m2 newest" in delta_prompt
    assert "m1" not in delta_prompt
    # Watermark advanced to seq 2.
    _, seq = db.get_thread_session("THR-001", "alice")
    assert seq == 2
    # Reuse was audited.
    actions = {r.action for r in db.get_audit_logs("THR-001")}
    assert "claude_session_reused" in actions


@pytest.mark.asyncio
async def test_resume_not_found_falls_back_to_full(tmp_path, monkeypatch):
    db = Database(tmp_path / "happyranch.db")
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))
    db.add_thread_participant("THR-001", "alice", added_by="founder")
    db.append_thread_message(thread_id="THR-001", speaker="founder",
                             kind=ThreadMessageKind.MESSAGE, body_markdown="m1")
    db.update_thread_session("THR-001", "alice", claude_session_id="claude-evicted", last_resumed_seq=0)
    inv = db.mint_thread_invocation(
        thread_id="THR-001", agent_name="alice",
        triggering_seq=1, purpose=ThreadInvocationPurpose.REPLY,
    )
    ws = tmp_path / "workspaces" / "alice"
    ws.mkdir(parents=True)
    (ws / "agent.yaml").write_text("executor: claude\n")

    evicted = FakeExecutorResult(success=False, error="No conversation found for session claude-evicted")
    evicted.returncode = 1
    evicted.stderr_tail = "No conversation found"
    evicted.claude_session_id = None

    import src.daemon.thread_runner as runner_mod
    fake = _ResumeRecordingExec([evicted, _ok_result("claude-fresh")])
    monkeypatch.setattr(runner_mod, "_build_executor_for_provider",
                        lambda provider, settings, paths: fake)
    org = FakeOrgState(db=db, root=tmp_path)
    await run_invocation(org_state=org, invocation_token=inv.invocation_token, settings=Settings())

    # Two runs: first with --resume, second (fallback) full, no resume.
    assert len(fake.calls) == 2
    assert fake.calls[0].get("resume_session_id") == "claude-evicted"
    assert "resume_session_id" not in fake.calls[1]
    assert "Full message history follows" in fake.calls[1]["prompt"]
    # New session persisted; eviction audited.
    sid, _ = db.get_thread_session("THR-001", "alice")
    assert sid == "claude-fresh"
    actions = {r.action for r in db.get_audit_logs("THR-001")}
    assert "claude_session_evicted_fallback" in actions
```

> The existing `FakeExecutorResult` (test file line 47) already sets `returncode`/`session_id`/`duration_seconds`; add `claude_session_id = None` to its `__init__` so success results default cleanly. Add `self.stdout_tail = ""` / `self.stderr_tail = ""` too if `_is_session_not_found` reads them.

- [ ] **Step 2: Update `FakeExecutorResult`**

In `tests/test_thread_runner.py`, extend the existing `FakeExecutorResult.__init__` (line 48) to:

```python
    def __init__(self, success: bool, error: str = ""):
        self.success = success
        self.error = error
        self.returncode = 0
        self.session_id = "sess-x"
        self.duration_seconds = 1
        self.claude_session_id = None
        self.stdout_tail = ""
        self.stderr_tail = ""
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_thread_runner.py -k "turn1 or turn2 or not_found" -v`
Expected: FAIL — resume not wired; `resume_session_id` never passed, no session persisted, no audit rows.

- [ ] **Step 4: Add the session-not-found predicate**

Add to `src/daemon/thread_runner.py` (after `_decline_by_default_doctrine`, ~line 100):

```python
# Best-effort markers for "the resume target no longer exists in Claude's local
# session store" (TTL eviction / workspace move). Verify against the running
# Claude CLI during integration — same caveat as the Codex event-name note in
# executors._parse_codex_usage. A miss is safe: it degrades to a normal failure,
# never a wrong answer.
_SESSION_NOT_FOUND_MARKERS = (
    "no conversation found",
    "session not found",
    "no session found",
    "could not find session",
    "no such session",
)


def _is_session_not_found(result) -> bool:
    blob = " ".join(
        filter(
            None,
            [
                getattr(result, "error", "") or "",
                getattr(result, "stderr_tail", "") or "",
                getattr(result, "stdout_tail", "") or "",
            ],
        )
    ).lower()
    return any(marker in blob for marker in _SESSION_NOT_FOUND_MARKERS)
```

- [ ] **Step 5: Rework the run section of `run_invocation`**

Replace the block from the prompt build through the executor run (current lines 192-261, i.e. from `prompt = build_thread_prompt(...)` down to the end of the `except Exception as exc:` handler) with the following. The participant lookup, executor build, and timeout resolution above it stay as-is; note this moves the `prompt =` assignment to AFTER the executor name is known.

```python
    workspace = org_state.root / "workspaces" / inv.agent_name

    # Read agent.yaml to pick the executor.
    try:
        from src.daemon.agent_config import load_agent_config
        agent_yaml = load_agent_config(Path(workspace)) or {}
    except Exception:
        agent_yaml = {}
    executor_name = (agent_yaml.get("executor") or "claude").lower()
    if executor_name not in _EXECUTOR_MAP:
        executor_name = "claude"

    # Build OrgPaths so ClaudeExecutor can resolve allow rules.
    try:
        from src.orchestrator._paths import OrgPaths
        paths = OrgPaths(root=org_state.root)
    except Exception:
        paths = None

    executor = _build_executor_for_provider(executor_name, settings, paths)

    # Resolve timeout (org override → code default).
    timeout: int = settings.session_timeout_seconds
    try:
        from src.orchestrator.org_config import load_org_config
        from src.orchestrator._paths import OrgPaths as _OrgPaths
        cfg = load_org_config(_OrgPaths(root=org_state.root))
        if cfg.threads_invocation_timeout_seconds is not None:
            timeout = cfg.threads_invocation_timeout_seconds
    except Exception:
        pass

    # --- Claude session resume (issue #53) ---
    # Only Claude supports `--resume`; other executors always run full-context.
    is_claude = executor_name == "claude"
    stored_sid, last_seq = (
        org_state.db.get_thread_session(inv.thread_id, inv.agent_name)
        if is_claude else (None, 0)
    )
    resume_sid: str | None = None
    if is_claude and stored_sid:
        new_messages = [m for m in messages if m.seq > last_seq]
        triggering = next((m for m in messages if m.seq == inv.triggering_seq), None)
        prompt = build_thread_delta_prompt(
            thread=thread,
            new_messages=new_messages,
            invocation_token=invocation_token,
            invoked_agent=inv.agent_name,
            purpose=inv.purpose.value,
            triggering_seq=inv.triggering_seq,
            triggering_message=triggering,
        )
        resume_sid = stored_sid
        shown_seqs = [m.seq for m in new_messages]
    else:
        prompt = build_thread_prompt(
            thread=thread,
            participants=participants,
            messages=messages,
            invocation_token=invocation_token,
            invoked_agent=inv.agent_name,
            purpose=inv.purpose.value,
            triggering_seq=inv.triggering_seq,
        )
        shown_seqs = [m.seq for m in messages]

    org_state.db.stamp_invocation_started(invocation_token, session_id=None)
    audit = AuditLogger(org_state.db)

    def _invoke(run_prompt: str, resume: str | None):
        run_kwargs = dict(
            workspace=Path(workspace),
            prompt=run_prompt,
            session_id=None,
            timeout_seconds=timeout,
        )
        if resume:
            run_kwargs["resume_session_id"] = resume
        return executor.run(**run_kwargs)

    # Spawn subprocess in a thread pool (executors are synchronous).
    try:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, lambda: _invoke(prompt, resume_sid))

        # Fallback: resume target evicted → rebuild full context, fresh session.
        if (
            is_claude
            and resume_sid
            and not result.success
            and _is_session_not_found(result)
        ):
            audit.log_claude_session_evicted_fallback(
                inv.thread_id,
                agent_name=inv.agent_name,
                stale_session_id=resume_sid,
                error=str(getattr(result, "error", "") or ""),
            )
            full_prompt = build_thread_prompt(
                thread=thread,
                participants=participants,
                messages=messages,
                invocation_token=invocation_token,
                invoked_agent=inv.agent_name,
                purpose=inv.purpose.value,
                triggering_seq=inv.triggering_seq,
            )
            shown_seqs = [m.seq for m in messages]
            resume_sid = None  # the second run is a fresh session
            result = await loop.run_in_executor(
                None, lambda: _invoke(full_prompt, None)
            )
    except Exception as exc:
        org_state.db.fail_invocation(
            invocation_token,
            status=ThreadInvocationStatus.FAILED,
            decline_reason=f"runner_crash: {exc}",
        )
        audit.log_thread_invocation_failed(
            inv.thread_id,
            agent=inv.agent_name,
            token=invocation_token,
            purpose=inv.purpose.value,
            reason=str(exc),
        )
        return

    # Persist the (possibly forked / freshly-minted) Claude session id + the
    # delta watermark. Advanced only on a successful subprocess — a failed turn
    # leaves the watermark so the next resume re-includes the skipped messages.
    if is_claude and result.success and getattr(result, "claude_session_id", None):
        new_watermark = max(shown_seqs) if shown_seqs else last_seq
        new_watermark = max(new_watermark, last_seq)
        org_state.db.update_thread_session(
            inv.thread_id,
            inv.agent_name,
            claude_session_id=result.claude_session_id,
            last_resumed_seq=new_watermark,
        )
        # `claude_session_reused` fires only when an actual --resume happened
        # (resume_sid is cleared on the fallback path, so an evicted→fresh turn
        # records only the eviction, not a reuse).
        if resume_sid:
            audit.log_claude_session_reused(
                inv.thread_id,
                agent_name=inv.agent_name,
                claude_session_id=result.claude_session_id,
                triggering_seq=inv.triggering_seq,
            )
```

The remaining tail of `run_invocation` (the `after = org_state.db.get_invocation_any_status(...)` block through the final auto-decline `log_thread_invocation_failed`, current lines 263-292) stays unchanged and operates on the final `result`. Note the existing code constructs `AuditLogger(org_state.db)` inline in that tail twice — leave those as-is, or reuse the `audit` local; either is fine.

> **Import note:** `AuditLogger` is already imported at the top of `thread_runner.py` (line 13). `build_thread_delta_prompt` is module-local. No new imports needed.

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_thread_runner.py -v`
Expected: PASS — all new tests plus the pre-existing `test_run_invocation_no_callback_silent_decline` (which uses a non-resume path: `get_thread_session` returns `(None, 0)`, so it takes the full-prompt branch exactly as before).

- [ ] **Step 7: Run the full thread + executor + audit suite**

Run: `uv run pytest tests/test_thread_runner.py tests/test_thread_prompt_doctrine.py tests/test_executor.py tests/test_audit_logger.py tests/test_thread_db.py -v`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add src/daemon/thread_runner.py tests/test_thread_runner.py
git commit -m "feat(threads): resume Claude sessions on turn 2+ with delta + eviction fallback"
```

---

## Task 6: Document load-bearing invariants in CLAUDE.md

**Files:**
- Modify: `CLAUDE.md` (add a new section near the other thread sections, e.g. after "Thread broadcast routing")

- [ ] **Step 1: Add the section**

Insert into `CLAUDE.md`:

```markdown
## Thread Claude-session resume (turn 2+ via `--resume`)

Claude-backed thread participants reuse their Claude session across turns instead
of re-shipping the full transcript + workspace CLAUDE.md every invocation. Per-
`(thread, agent)` state lives in two columns on `thread_participants`:
`claude_session_id` (the resumable session) and `last_resumed_seq` (the delta
watermark). Issue #53. Implementation: `src/daemon/thread_runner.py`
(`build_thread_delta_prompt`, `_is_session_not_found`, resume wiring in
`run_invocation`), `src/orchestrator/executors.py` (`ClaudeExecutor.run`
`resume_session_id` param + `ExecutorResult.claude_session_id` +
`_parse_claude_session_id`), `src/infrastructure/database.py`
(`get_thread_session` / `update_thread_session`).

**Load-bearing invariants:**

- **Claude-only.** Resume is gated on `executor_name == "claude"`. codex/opencode/
  pi participants always run full-context and never read/write the session
  columns. Don't generalize without a per-provider resume design.
- **Session id is an optimization, never a correctness dependency.** The SQLite
  transcript is canonical. A parse miss, an eviction, or any non-Claude executor
  silently falls back to a full-context fresh session.
- **`last_resumed_seq` advances ONLY on a successful subprocess.** A failed/timed-
  out turn leaves the watermark untouched so the next successful resume re-
  includes the messages the broken turn skipped. Reverting to "advance on every
  invocation" orphans messages from the session on any failure.
- **Eviction fallback runs the executor a second time within one `run_invocation`,
  then clears `resume_sid`.** Because the failed resume never consumed the single-
  use invocation token, the full-context retry can still consume it. The fallback
  path audits `claude_session_evicted_fallback` and (because `resume_sid` is now
  None) does NOT also audit `claude_session_reused`.
- **`--resume` may fork a new session id.** Always persist `result.claude_session_id`
  from each successful turn, not the id you passed in.
- **Concurrency safety comes from per-`(thread, agent)` serialization** (the thread
  queue), giving at most one in-flight invocation per session. `--resume` on the
  same session concurrently is undefined; don't add parallelism per participant.
- **System-prompt drift is accepted.** A mid-thread CLAUDE.md / org-prompt change
  is not visible to an already-resumed session (it was locked in at session
  creation). To force a fresh session, clear `claude_session_id` for that
  `(thread, agent)` row. Most prompt changes happen between conversations.
- **Two-place schema add** — the columns are in BOTH the `thread_participants`
  CREATE TABLE (fresh DBs) and the idempotent ALTER block (existing DBs). Both
  paths are required, same as the assets-dir pattern.
```

- [ ] **Step 2: Verify the doc renders / no broken references**

Run: `grep -n "Thread Claude-session resume" CLAUDE.md`
Expected: one match.

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: document thread Claude-session resume invariants"
```

---

## Final verification

- [ ] **Run the full unit suite**

Run: `uv run pytest tests/ -v`
Expected: PASS (unit tests; integration excluded by default).

- [ ] **Run the integration suite** (this change touches the thread runner — the surface CLAUDE.md flags as historically regression-prone)

Run: `uv run pytest tests/ -v -m integration`
Expected: PASS. If the fake-claude thread plan needs to emit a `session_id` in its JSON for a new resume-path integration test, that is an OPTIONAL follow-up — the unit tests in Task 5 fully cover turn-1 capture, turn-2 resume, and eviction fallback. The existing integration thread flow exercises the full-context (turn-1) path unchanged.

---

## Self-Review notes (already reconciled)

- **Spec coverage:** schema (Task 1), executor `--resume` + capture (Task 2), audit kinds (Task 3), preamble-skip via delta builder (Task 4), fallback + wiring + tests (Task 5), CLAUDE.md invariants (Task 6). Talks intentionally dropped (see Deviations). Concurrency covered by structural serialization (documented, not new code).
- **Type consistency:** `claude_session_id` (str|None), `last_resumed_seq` (int), `resume_session_id` (param), `build_thread_delta_prompt` signature, `get_thread_session`→`(str|None, int)`, `update_thread_session(*, claude_session_id, last_resumed_seq)` — names identical across all tasks.
- **No placeholders:** every code step contains full code; every run step has an exact command + expected outcome.

---

## Appendix: generalizing to other executors (codex / opencode / pi)

This appendix is **not** part of the Claude implementation above. It documents how
to extend session-resume to the other three executors as additive follow-up work,
and the cheap renames to apply *inside the Claude tasks* so that follow-up is
purely additive (no rename migration later). Land Claude first.

### A.1 Two session-ownership models

Resume splits the executors into two models. The single `agent_session_id` column
+ `if stored_id → delta else → full` logic serves both; they differ only in **who
mints the id** and **how eviction is detected**.

| Model | Executors | Turn 1 | Resume invocation | Eviction detection |
|---|---|---|---|---|
| **Callee-assigned** | Claude, Codex, opencode | run full → parse the session id from CLI output → store it | pass the stored id back to the CLI | CLI **errors** with a not-found string → fallback |
| **Caller-assigned** | **Pi** | **we** mint a uuid, store it, pass it (full prompt) | pass the same id every turn | CLI **silently re-creates** → must check **session-file existence** ourselves |

The whole delta-prompt builder, watermark logic, and full-context-rebuild fallback
are model-agnostic. Only three things are per-executor: command shape, how the id
is obtained (parse-from-output vs. self-minted), and how eviction is detected.

### A.2 Rename to do NOW inside the Claude tasks (free; avoids a later migration)

Apply these substitutions to Tasks 1-5 before implementing so the storage layer is
executor-neutral from day one:

- Column `thread_participants.claude_session_id` → **`agent_session_id`** (Task 1,
  both the CREATE TABLE and the ALTER block; and in `get_thread_session` /
  `update_thread_session`).
- `ExecutorResult.claude_session_id` → **`agent_session_id`** (Task 2). Each
  executor's session-id parser populates it; Claude's is `_parse_claude_session_id`.
- Audit kinds: keep `claude_session_*` only if you want Claude-specific telemetry;
  otherwise rename to **`agent_session_reused`** / **`agent_session_evicted_fallback`**
  with an `executor` field in the payload (Task 3). Recommended: the generic names
  + `executor` field, so one query covers all four.

Everything else in Tasks 1-5 is already executor-neutral.

### A.3 The executor capability seam

Replace the `is_claude = executor_name == "claude"` gate in `run_invocation`
(Task 5) with a capability the executor declares. Add to each executor class:

```python
class ClaudeExecutor:
    supports_resume = True
    session_id_model = "callee"   # CLI mints the id; we parse it from output
    # ... existing run() with resume_session_id (Task 2) ...

class CodexExecutor:
    supports_resume = True
    session_id_model = "callee"

class OpencodeExecutor:
    supports_resume = True
    session_id_model = "callee"

class PiExecutor:
    supports_resume = True
    session_id_model = "caller"   # we mint the id; pass it every turn

# Pi's run() never gets a resume flag in the Claude sense — it always passes
# --session-id <id> --session-dir <path>; "resume" is implicit in the id existing.
```

Then `run_invocation` reads `getattr(executor, "supports_resume", False)` instead
of the string check, and branches the id-acquisition on `session_id_model`.

### A.4 Per-executor command construction

**opencode (callee, easiest):** plain flag added to the existing `run` argv.

```python
# OpencodeExecutor.run — when resume_session_id set:
cmd = [self._cli_path, "run", "--dir", str(workspace),
       "--format", "json", "--session", resume_session_id, "--prompt", prompt]
# session-id parser: locate opencode's session id in the --format json output.
```

**Codex (callee, hardest — different subcommand shape):** the non-resume path is
`codex exec ... --json -` (prompt via **stdin**). The resume path is a *different*
subcommand with the id as a **positional**:

```python
# CodexExecutor.run — when resume_session_id set:
cmd = [self._cli_path, "exec", "resume", resume_session_id,
       "-c", "sandbox_workspace_write.network_access=true",
       "--skip-git-repo-check", "--json", prompt]   # prompt positional? OR keep "-" + stdin?
```

> **MUST verify before implementing Codex:** does `codex exec resume <id>` accept
> the prompt via stdin (so we keep `input_text=prompt` and a trailing `-`), or does
> it require the prompt as a positional arg? The current `_run_command` pipes the
> prompt through stdin. Confirm against the installed Codex CLI:
> `codex exec resume --help`. Also confirm **which NDJSON event carries the session
> id** (likely a `session_configured` / thread-started event near the top of the
> stream) to write `_parse_codex_session_id`.

**Pi (caller — self-minted id, no parsing):** mint the id ourselves on turn 1, pin
a stable session dir, pass both every turn:

```python
# In run_invocation, for session_id_model == "caller":
stored_sid, last_seq = org_state.db.get_thread_session(inv.thread_id, inv.agent_name)
session_dir = Path(workspace) / ".pi-sessions"
if stored_sid is None:
    # Turn 1: mint our own id, full prompt. (uuid; must avoid Math.random-style
    # nondeterminism only matters in workflow scripts — here a uuid4 is fine.)
    import uuid
    stored_sid = f"thr-{inv.thread_id}-{inv.agent_name}-{uuid.uuid4().hex[:8]}"
    use_delta = False
else:
    # Eviction check: Pi --session-id silently re-creates, so detect a missing
    # session file ourselves and force a full prompt under the SAME id.
    use_delta = _pi_session_exists(session_dir, stored_sid)
    if not use_delta:
        audit.log_agent_session_evicted_fallback(
            inv.thread_id, agent_name=inv.agent_name,
            stale_session_id=stored_sid, error="pi session file absent",
        )

# PiExecutor.run always passes:  --session-id <stored_sid> --session-dir <session_dir>
# We persist stored_sid on turn 1 and leave it stable thereafter; the watermark
# advances exactly as for the callee model.
```

`_pi_session_exists(session_dir, sid)` stats the on-disk session file. **MUST
verify the filename pattern** before writing it: run
`pi -p --mode json --session-id test123 --session-dir /tmp/pis "hi"` once and
inspect `/tmp/pis` to learn how Pi names the file for a given id.

Notes on Pi:
- Use `--session-id` (explicit, create-if-missing), **not** `--continue/-c` —
  `--continue` resumes the *last* session, which is ambiguous across concurrent
  per-(thread, agent) sessions.
- Pi needs **no session-id parser** and never sets `ExecutorResult.agent_session_id`
  from output; `run_invocation` writes the self-minted id directly.
- Pinning `--session-dir` under the workspace gives us a session store we control,
  sidestepping any global Pi TTL.

### A.5 Eviction detection becomes per-model

`_is_session_not_found(result)` (Task 5) covers the **callee** model only; give it
per-executor marker lists (Claude/Codex/opencode error wording differs). The
**caller** model (Pi) never reaches it — its eviction check is the file-existence
test in A.4. Keep both paths funneling into the same
`agent_session_evicted_fallback` audit + full-context rebuild.

### A.6 Follow-up task checklist (per non-Claude executor)

- [ ] opencode: `supports_resume`/`session_id_model`; `--session` in `run`;
      `_parse_opencode_session_id`; opencode error markers; tests mirroring Task 5.
- [ ] Codex: **verify stdin-vs-positional + session-id event first**; branched
      `exec resume` command; `_parse_codex_session_id`; codex error markers; tests.
- [ ] Pi: **verify session-file naming first**; `--session-id`/`--session-dir` in
      `run`; self-minted id + `_pi_session_exists`; tests (turn 1 mints id, turn 2
      reuses, deleted-file forces full).
```
