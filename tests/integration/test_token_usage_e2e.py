"""End-to-end coverage for token-usage tracking.

The fakes (`fake_claude.sh`, `fake_codex.sh`) emit fixture-shaped JSON usage
payloads when invoked with the JSON output flags the real executors pass.
These tests confirm the full chain — subprocess stdout -> usage parser ->
ExecutorResult.token_usage -> run_step write -> session_token_usage row +
session_end audit payload + opc tokens surfacing — actually runs end-to-end.

The Claude fixture: input=1000, output=500, cache_creation=300, cache_read=200,
no reasoning. So total token_count (input + output + reasoning) = 1500.

The Codex fixture: input=2000, output=800, cached=150 (mapped to
cache_read_tokens; Codex doesn't separate cache_creation), reasoning=100,
model="gpt-5".
"""
from __future__ import annotations

import time
from pathlib import Path

import httpx
import pytest

from src.infrastructure.database import Database
from tests.integration.conftest import seed_workspace


pytestmark = pytest.mark.integration


def _auth_headers() -> dict:
    from src.daemon import paths

    return {"Authorization": f"Bearer {paths.read_token()}"}


def _wait_for_terminal_status(
    base: str,
    task_id: str,
    timeout: float = 30.0,
) -> str:
    headers = _auth_headers()
    deadline = time.monotonic() + timeout
    body: dict = {}
    while time.monotonic() < deadline:
        r = httpx.get(f"{base}/tasks/{task_id}", headers=headers, timeout=5.0)
        body = r.json()
        status = body["task"]["status"]
        if status in ("completed", "failed"):
            return status
        if status == "blocked" and body["task"].get("block_kind") == "escalated":
            return status
        time.sleep(0.2)
    raise AssertionError(f"task {task_id} did not reach a terminal state (last={body})")


def _submit_task(base: str, brief: str = "token usage e2e") -> str:
    r = httpx.post(
        f"{base}/tasks",
        json={"type": "general", "brief": brief},
        headers=_auth_headers(),
        timeout=5.0,
    )
    assert r.status_code == 200, r.text
    return r.json()["task_id"]


def _write_done_plan(plan_path: Path, agent: str = "engineering_head") -> None:
    plan_path.write_text(
        '#!/usr/bin/env bash\n'
        'set -e\n'
        'task_id=$1; session_id=$2; agent=$3; org_slug=$4\n'
        'opc report-completion --org "$org_slug" \\\n'
        '  --task-id "$task_id" --session-id "$session_id" \\\n'
        f'  --agent {agent} --status completed --confidence 90 \\\n'
        '  --summary \'{"action":"done","summary":"ok"}\'\n'
    )
    plan_path.chmod(0o755)


def _write_codex_done_plan(plan_path: Path, agent: str = "engineering_head") -> None:
    """Codex plan signature is (task_id, session_id, org_slug) — no agent arg."""
    plan_path.write_text(
        '#!/usr/bin/env bash\n'
        'set -e\n'
        'task_id=$1; session_id=$2; org_slug=$3\n'
        'opc report-completion --org "$org_slug" \\\n'
        '  --task-id "$task_id" --session-id "$session_id" \\\n'
        f'  --agent {agent} --status completed --confidence 90 \\\n'
        '  --summary \'{"action":"done","summary":"ok"}\'\n'
    )
    plan_path.chmod(0o755)


def _seed_codex_workspace(org_root: Path, agent: str) -> None:
    """Create the minimum Codex workspace: AGENTS.md readiness marker +
    agent.yaml declaring the codex executor.

    The orchestrator's WorkspaceNotInitialized guard checks for AGENTS.md
    when provider == "codex". Past that, fake_codex.sh parses task_id /
    session_id from the prompt directly and runs the plan — no real Codex
    bootstrap is needed."""
    workspace = org_root / "workspaces" / agent
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "AGENTS.md").write_text("# AGENTS (test stub)\n")
    (workspace / "agent.yaml").write_text("repos: {}\nexecutor: codex\n")


def test_claude_session_writes_token_usage_row(
    live_daemon,
    runtime,
    fake_plan_env,
):
    """A successful Claude session writes one session_token_usage row with
    the parser-extracted fields, and `opc tokens` (via the daemon route)
    surfaces it."""
    port = live_daemon
    base = f"http://127.0.0.1:{port}/api/v1/orgs/test"

    _write_done_plan(fake_plan_env)
    seed_workspace(runtime, "engineering_head")

    task_id = _submit_task(base)
    assert _wait_for_terminal_status(base, task_id, timeout=20.0) == "completed"

    db = Database(runtime / "opc.db")
    rows = db.list_session_token_usage(task_id=task_id)
    assert rows, f"expected token-usage row for {task_id}, found none"
    # The fake fixture maps to exactly one Claude session for the manager.
    claude_rows = [r for r in rows if r["executor"] == "claude"]
    assert claude_rows, f"no claude rows: {rows}"
    row = claude_rows[0]
    assert row["agent"] == "engineering_head"
    assert row["input_tokens"] == 1000
    assert row["output_tokens"] == 500
    assert row["cache_creation_tokens"] == 300
    assert row["cache_read_tokens"] == 200
    assert row["reasoning_tokens"] is None
    assert row["model"] == "claude-sonnet-4-6"

    # Daemon route surfaces the same row.
    r = httpx.get(
        f"{base}/tokens",
        params={"task_id": task_id},
        headers=_auth_headers(),
        timeout=5.0,
    )
    assert r.status_code == 200, r.text
    api_rows = r.json()["rows"]
    assert any(
        ar["task_id"] == task_id
        and ar["executor"] == "claude"
        and ar["input_tokens"] == 1000
        and ar["output_tokens"] == 500
        for ar in api_rows
    ), api_rows


def test_audit_log_carries_token_usage_payload(
    live_daemon,
    runtime,
    fake_plan_env,
):
    """The session_end audit row carries token_usage dict + token_count int.

    token_count is input + output + reasoning (cache reads are reported
    separately, never folded in). For the Claude fixture: 1000+500+0 = 1500.
    """
    port = live_daemon
    base = f"http://127.0.0.1:{port}/api/v1/orgs/test"

    _write_done_plan(fake_plan_env)
    seed_workspace(runtime, "engineering_head")

    task_id = _submit_task(base)
    assert _wait_for_terminal_status(base, task_id, timeout=20.0) == "completed"

    db = Database(runtime / "opc.db")
    audit = db.get_audit_logs(task_id)
    session_ends = [a for a in audit if a["action"] == "session_end"]
    assert session_ends, f"no session_end entries in {audit}"
    payload = session_ends[0]["payload"]
    assert isinstance(payload, dict), payload
    assert payload["token_count"] == 1500
    tu = payload["token_usage"]
    assert isinstance(tu, dict), payload
    assert tu["input_tokens"] == 1000
    assert tu["output_tokens"] == 500
    assert tu["cache_creation_tokens"] == 300
    assert tu["cache_read_tokens"] == 200
    assert tu["reasoning_tokens"] is None
    assert tu["model"] == "claude-sonnet-4-6"


def test_codex_session_writes_token_usage_row(
    live_daemon,
    runtime,
    fake_codex_plan_env,
):
    """A successful Codex session writes one session_token_usage row whose
    fields match `_parse_codex_usage`'s mapping of the fixture NDJSON
    (input=2000, output=800, cached=150, reasoning=100, model="gpt-5").

    Without this mirror, a regression in `_parse_codex_usage` (e.g. the
    `cached_tokens` → `cache_read_tokens` rename, or dropping the reasoning
    field) would not be caught by the e2e suite."""
    port = live_daemon
    base = f"http://127.0.0.1:{port}/api/v1/orgs/test"

    _write_codex_done_plan(fake_codex_plan_env)
    _seed_codex_workspace(runtime, "engineering_head")

    task_id = _submit_task(base)
    assert _wait_for_terminal_status(base, task_id, timeout=20.0) == "completed"

    db = Database(runtime / "opc.db")
    rows = db.list_session_token_usage(task_id=task_id)
    assert rows, f"expected token-usage row for {task_id}, found none"
    codex_rows = [r for r in rows if r["executor"] == "codex"]
    assert codex_rows, f"no codex rows: {rows}"
    row = codex_rows[0]
    assert row["agent"] == "engineering_head"
    assert row["input_tokens"] == 2000
    assert row["output_tokens"] == 800
    # Codex emits a single `cached_tokens` field, mapped onto cache_read; it
    # does not separate cache-creation, so that column stays NULL.
    assert row["cache_read_tokens"] == 150
    assert row["cache_creation_tokens"] is None
    assert row["reasoning_tokens"] == 100
    assert row["model"] == "gpt-5"
