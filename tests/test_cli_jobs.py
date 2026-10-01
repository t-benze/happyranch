"""CLI smoke tests for happyranch jobs subcommands."""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from unittest.mock import Mock, patch

from cli.commands.jobs import cmd_jobs_output, cmd_jobs_show, cmd_jobs_submit


def _run(*args) -> subprocess.CompletedProcess:
    """Invoke `uv run happyranch ...` from the worktree root."""
    return subprocess.run(
        ["uv", "run", "happyranch", *args],
        capture_output=True, text=True,
        cwd=str(Path(__file__).resolve().parents[1]),
    )


def test_jobs_submit_help():
    result = _run("jobs", "submit", "--help")
    assert result.returncode == 0
    assert "--from-file" in result.stdout
    assert "--org" in result.stdout
    assert "--json" in result.stdout


def test_jobs_submit_missing_from_file():
    """argparse should fail-fast when --from-file is missing."""
    result = _run("jobs", "submit", "--org", "alpha")
    assert result.returncode != 0
    assert "from-file" in (result.stderr + result.stdout)


def test_jobs_list_help():
    r = _run("jobs", "list", "--help")
    assert r.returncode == 0
    assert "--status" in r.stdout


def test_jobs_show_help():
    r = _run("jobs", "show", "--help")
    assert r.returncode == 0
    assert "--json" in r.stdout
    assert "--task-id" in r.stdout
    assert "--session-id" in r.stdout


def test_jobs_reject_help():
    r = _run("jobs", "reject", "--help")
    assert r.returncode == 0
    assert "--reason" in r.stdout


def test_jobs_output_help():
    r = _run("jobs", "output", "--help")
    assert r.returncode == 0
    assert "--stream" in r.stdout
    assert "--json" in r.stdout
    assert "--task-id" in r.stdout
    assert "--session-id" in r.stdout


def test_jobs_run_help():
    r = _run("jobs", "run", "--help")
    assert r.returncode == 0
    assert "--cwd" in r.stdout
    assert "--timeout-seconds" in r.stdout


def test_jobs_run_requires_tty():
    """Non-TTY invocation fails-fast with canonical message."""
    r = _run("jobs", "run", "JOB-001", "--org", "alpha")
    assert r.returncode != 0
    assert "TTY" in (r.stderr + r.stdout)


def test_scripts_shim_prints_deprecation_warning():
    """`happyranch scripts <verb>` reaches the handler with a deprecation banner."""
    # `run` fails the TTY gate before any network call — same pattern as
    # test_jobs_run_requires_tty above, just on the deprecated alias path.
    r = _run("scripts", "run", "JOB-001", "--org", "alpha")
    assert r.returncode != 0
    assert "deprecated" in r.stderr.lower()


def _response(payload):
    response = Mock()
    response.status_code = 200
    response.json.return_value = payload
    return response


def test_submit_json_emits_unmodified_structured_submission(tmp_path, capsys):
    payload_file = tmp_path / "job.json"
    payload_file.write_text(json.dumps({
        "task_id": "TASK-1", "session_id": "sess-1",
        "title": "scan", "rationale": "receipt",
        "script": "true\n", "interpreter": "bash",
    }))
    receipt = {
        "id": "JOB-7", "status": "running",
        "authentication": {"task_id": "TASK-1", "session_id": "sess-1"},
    }
    client = Mock()
    client.post.return_value = _response(receipt)
    args = argparse.Namespace(org="alpha", from_file=str(payload_file), json=True)
    with patch("cli.commands.jobs.OpcClient.from_env", return_value=client), patch(
        "cli.commands.jobs._shared._fetch_available_orgs", return_value=["alpha"]
    ):
        cmd_jobs_submit(args)
    assert json.loads(capsys.readouterr().out) == receipt


def test_show_and_output_json_use_exact_receipt_route(capsys):
    receipt = {
        "authentication": {"task_id": "TASK-1", "session_id": "sess-1"},
        "job": {"id": "JOB-7"}, "output": {"stdout": "{}\n"},
    }
    for command, extra in (
        (cmd_jobs_show, {}),
        (cmd_jobs_output, {"stream": "both", "max_bytes": 123}),
    ):
        client = Mock()
        client.get.return_value = _response(receipt)
        args = argparse.Namespace(
            org="alpha", job_id="JOB-7", json=True,
            task_id="TASK-1", session_id="sess-1", **extra,
        )
        with patch("cli.commands.jobs.OpcClient.from_env", return_value=client), patch(
            "cli.commands.jobs._shared._fetch_available_orgs", return_value=["alpha"]
        ):
            command(args)
        assert json.loads(capsys.readouterr().out) == receipt
        path = client.get.call_args.args[0]
        assert path.endswith("/jobs/JOB-7/receipt")
        assert client.get.call_args.kwargs["params"]["task_id"] == "TASK-1"
        assert client.get.call_args.kwargs["params"]["session_id"] == "sess-1"
