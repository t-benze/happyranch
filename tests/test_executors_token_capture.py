from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from runtime.config import Settings
from runtime.orchestrator.executors import ClaudeExecutor, CodexExecutor, OpencodeExecutor, PiExecutor, ExecutorResult

_EXECUTOR_NAMES = frozenset({"claude", "codex", "opencode", "pi"})


@pytest.fixture(autouse=True)
def _mock_shutil_which(monkeypatch):
    """Patch shutil.which inside executors so the executor constructors'
    _resolve_binary calls resolve deterministically regardless of host PATH."""
    import runtime.orchestrator.executors as _ex_mod

    _real_which = shutil.which

    def _patched_which(name, path=None):
        real = _real_which(name, path=path)
        if real is not None:
            return real
        if name in _EXECUTOR_NAMES:
            return f"/usr/local/bin/{os.path.basename(name)}"
        return None

    monkeypatch.setattr(_ex_mod.shutil, "which", _patched_which)


def _make_completed_proc(stdout: str, returncode: int = 0):
    p = MagicMock()
    p.communicate.return_value = (stdout, "")
    p.returncode = returncode
    p.pid = 12345
    return p


def test_executor_result_has_token_usage_field_default_none():
    r = ExecutorResult(success=True, duration_seconds=1, session_id="s")
    assert r.token_usage is None


def test_claude_executor_attaches_token_usage_on_success(tmp_path: Path, monkeypatch):
    workspace = tmp_path / "workspaces" / "dev_agent"
    workspace.mkdir(parents=True)

    fixture = (Path(__file__).parent / "fixtures" / "usage_claude.json").read_text()
    fake_proc = _make_completed_proc(stdout=fixture)
    with patch("runtime.orchestrator.executors.subprocess.Popen", return_value=fake_proc):
        # allow_rules_for_agent reads from <runtime>/org/agents/<name>.md;
        # short-circuit it for this isolated unit test.
        with patch("runtime.orchestrator.workspace_adapters.allow_rules_for_agent", return_value=["Bash(happyranch *)"]):
            ex = ClaudeExecutor(
                claude_cli_path="claude",
                permission_mode="auto",
                settings=Settings(),
                paths=None,
            )
            result = ex.run(workspace, prompt="hi", session_id="sess-x")
    assert result.success
    assert result.token_usage is not None
    assert result.token_usage.input_tokens == 12345


def test_claude_executor_passes_output_format_json_flag(tmp_path: Path):
    workspace = tmp_path / "workspaces" / "dev_agent"
    workspace.mkdir(parents=True)
    fake_proc = _make_completed_proc(stdout="{}")

    captured_cmd = []

    def _capture_popen(cmd, **kw):
        captured_cmd.extend(cmd)
        return fake_proc

    with patch("runtime.orchestrator.executors.subprocess.Popen", side_effect=_capture_popen):
        with patch("runtime.orchestrator.workspace_adapters.allow_rules_for_agent", return_value=[]):
            ex = ClaudeExecutor("claude", "auto", Settings(), paths=None)
            ex.run(workspace, prompt="hi", session_id="sess-x")
    assert "--output-format" in captured_cmd
    json_idx = captured_cmd.index("--output-format")
    assert captured_cmd[json_idx + 1] == "json"


def test_claude_executor_token_usage_is_none_on_subprocess_failure(tmp_path: Path):
    workspace = tmp_path / "workspaces" / "dev_agent"
    workspace.mkdir(parents=True)
    fake_proc = _make_completed_proc(stdout="{}", returncode=1)
    with patch("runtime.orchestrator.executors.subprocess.Popen", return_value=fake_proc):
        with patch("runtime.orchestrator.workspace_adapters.allow_rules_for_agent", return_value=[]):
            ex = ClaudeExecutor("claude", "auto", Settings(), paths=None)
            r = ex.run(workspace, prompt="hi", session_id="sess-x")
    assert not r.success
    assert r.token_usage is None  # subprocess failed → no row should be written


def test_codex_executor_attaches_token_usage(tmp_path: Path):
    workspace = tmp_path / "workspaces" / "dev_agent"
    workspace.mkdir(parents=True)
    fixture = (Path(__file__).parent / "fixtures" / "usage_codex.jsonl").read_text()
    fake_proc = _make_completed_proc(stdout=fixture)
    with patch("runtime.orchestrator.executors.subprocess.Popen", return_value=fake_proc):
        ex = CodexExecutor("codex", sandbox_mode="workspace-write")
        r = ex.run(workspace, prompt="hi", session_id="sess-x")
    assert r.success
    assert r.token_usage is not None
    assert r.token_usage.input_tokens == 34887


def test_opencode_executor_attaches_token_usage(tmp_path: Path):
    workspace = tmp_path / "workspaces" / "dev_agent"
    workspace.mkdir(parents=True)
    fixture = (Path(__file__).parent / "fixtures" / "usage_opencode.json").read_text()
    fake_proc = _make_completed_proc(stdout=fixture)
    with patch("runtime.orchestrator.executors.subprocess.Popen", return_value=fake_proc):
        ex = OpencodeExecutor("opencode")
        r = ex.run(workspace, prompt="hi", session_id="sess-x")
    assert r.success
    assert r.token_usage is not None
    assert r.token_usage.input_tokens == 300


def test_pi_executor_preserves_raw_json_usage_when_schema_is_unrecognized(tmp_path: Path):
    workspace = tmp_path / "workspaces" / "dev_agent"
    workspace.mkdir(parents=True)
    fake_proc = _make_completed_proc(stdout='{"type":"result","model":"pi-model"}\n')
    with patch("runtime.orchestrator.executors.subprocess.Popen", return_value=fake_proc):
        ex = PiExecutor("pi")
        r = ex.run(workspace, prompt="hi", session_id="sess-x")
    assert r.success
    assert r.token_usage is not None
    assert r.token_usage.input_tokens is None
    assert "pi-model" in (r.token_usage.usage_raw_json or "")
