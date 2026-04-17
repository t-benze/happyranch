from unittest.mock import MagicMock, patch

import pytest

from src.cli import build_parser


def test_run_subcommand():
    parser = build_parser()
    args = parser.parse_args([
        "run",
        "--task", "implement_feature",
        "--brief", "Add Alipay support",
    ])
    assert args.command == "run"
    assert args.task == "implement_feature"
    assert args.brief == "Add Alipay support"


def test_status_subcommand():
    parser = build_parser()
    args = parser.parse_args(["status", "TASK-001"])
    assert args.command == "status"
    assert args.task_id == "TASK-001"


def test_tasks_subcommand():
    parser = build_parser()
    args = parser.parse_args(["tasks"])
    assert args.command == "tasks"
    assert args.limit == 20


def test_tasks_with_limit():
    parser = build_parser()
    args = parser.parse_args(["tasks", "--limit", "5"])
    assert args.limit == 5


def test_agents_subcommand():
    parser = build_parser()
    args = parser.parse_args(["agents"])
    assert args.command == "agents"
    assert args.detail is False


def test_agents_detail():
    parser = build_parser()
    args = parser.parse_args(["agents", "--detail"])
    assert args.detail is True


def test_init_agent_subcommand():
    parser = build_parser()
    args = parser.parse_args(["init-agent"])
    assert args.command == "init-agent"
    assert args.agent is None


def test_init_agent_specific():
    parser = build_parser()
    args = parser.parse_args(["init-agent", "dev_agent"])
    assert args.command == "init-agent"
    assert args.agent == "dev_agent"


def test_init_subcommand():
    parser = build_parser()
    args = parser.parse_args(["init", "/tmp/my-runtime"])
    assert args.command == "init"
    assert args.path == "/tmp/my-runtime"


def test_no_command_prints_help(capsys):
    parser = build_parser()
    args = parser.parse_args([])
    assert args.command is None




def test_run_without_task_flag():
    parser = build_parser()
    args = parser.parse_args(["run", "--brief", "Explore the codebase"])
    assert args.command == "run"
    assert args.task == "general"
    assert args.brief == "Explore the codebase"


def test_run_with_task_flag():
    parser = build_parser()
    args = parser.parse_args(["run", "--task", "bug_fix", "--brief", "Fix it"])
    assert args.task == "bug_fix"


def test_cmd_init_calls_register_endpoint(tmp_path, capsys):
    from src.cli import cmd_init

    fake_client = MagicMock()
    fake_client.post.return_value.status_code = 200
    fake_client.post.return_value.json.return_value = {
        "active": str(tmp_path / "rt"),
        "registered": [str(tmp_path / "rt")],
    }

    with patch("src.cli.OpcClient.from_env", return_value=fake_client):
        args = MagicMock(path=str(tmp_path / "rt"))
        cmd_init(args)

    fake_client.post.assert_called_once_with(
        "/api/v1/runtimes/register", json={"path": str(tmp_path / "rt")},
    )
    out = capsys.readouterr().out
    assert "active runtime" in out.lower()


def test_cmd_use_calls_activate_endpoint(tmp_path, capsys):
    from src.cli import cmd_use

    fake_client = MagicMock()
    fake_client.post.return_value.status_code = 200
    fake_client.post.return_value.json.return_value = {
        "active": str(tmp_path / "rt"),
        "registered": [str(tmp_path / "rt")],
    }

    with patch("src.cli.OpcClient.from_env", return_value=fake_client):
        args = MagicMock(path=str(tmp_path / "rt"))
        cmd_use(args)

    fake_client.post.assert_called_once_with(
        "/api/v1/runtimes/activate", json={"path": str(tmp_path / "rt")},
    )


def test_cmd_tasks_calls_list_endpoint(capsys):
    from src.cli import cmd_tasks

    fake = MagicMock()
    fake.get.return_value.status_code = 200
    fake.get.return_value.json.return_value = {"tasks": [
        {"id": "TASK-001", "type": "general", "status": "approved", "brief": "x"},
    ]}
    with patch("src.cli.OpcClient.from_env", return_value=fake):
        args = MagicMock(limit=20)
        cmd_tasks(args)
    fake.get.assert_called_once_with("/api/v1/tasks", params={"limit": 20})
    assert "TASK-001" in capsys.readouterr().out


def test_cmd_tasks_idle_daemon_prints_friendly_message(capsys):
    """409 no_active_runtime should produce a sentence, not raw JSON."""
    from src.cli import cmd_tasks

    fake = MagicMock()
    fake.get.return_value.status_code = 409
    fake.get.return_value.json.return_value = {"detail": {"code": "no_active_runtime"}}
    with patch("src.cli.OpcClient.from_env", return_value=fake):
        args = MagicMock(limit=20)
        with pytest.raises(SystemExit):
            cmd_tasks(args)
    out = capsys.readouterr().out
    assert "No active runtime" in out
    assert "opc use" in out


def test_cmd_status_handles_404(capsys):
    from src.cli import cmd_status

    fake = MagicMock()
    fake.get.return_value.status_code = 404
    with patch("src.cli.OpcClient.from_env", return_value=fake):
        args = MagicMock(task_id="TASK-X")
        with pytest.raises(SystemExit):
            cmd_status(args)
    assert "not found" in capsys.readouterr().out


def test_cmd_run_submits_then_streams(capsys):
    from src.cli import cmd_run

    fake = MagicMock()
    fake.post.return_value.status_code = 200
    fake.post.return_value.json.return_value = {"task_id": "TASK-001"}
    fake.stream.return_value = iter([
        '{"type": "audit", "n": 1}',
        '{"type": "task_complete", "outcome": "approved"}',
    ])

    with patch("src.cli.OpcClient.from_env", return_value=fake):
        args = MagicMock(task="general", brief="x")
        cmd_run(args)

    fake.post.assert_called_once_with("/api/v1/tasks", json={"type": "general", "brief": "x"})
    out = capsys.readouterr().out
    assert "TASK-001" in out
    assert "task_complete" in out


def test_cmd_tail_streams_existing_task(capsys):
    from src.cli import cmd_tail

    fake = MagicMock()
    fake.stream.return_value = iter(['{"type": "task_complete"}'])
    with patch("src.cli.OpcClient.from_env", return_value=fake):
        args = MagicMock(task_id="TASK-001")
        cmd_tail(args)
    assert "task_complete" in capsys.readouterr().out


def test_cmd_run_idle_daemon_prints_friendly_message(capsys):
    """409 no_active_runtime from POST should produce the same friendly sentence
    as the read-side commands — no raw JSON, no KeyError on malformed bodies."""
    from src.cli import cmd_run

    fake = MagicMock()
    fake.post.return_value.status_code = 409
    fake.post.return_value.json.return_value = {"detail": {"code": "no_active_runtime"}}
    with patch("src.cli.OpcClient.from_env", return_value=fake):
        args = MagicMock(task="general", brief="x")
        with pytest.raises(SystemExit):
            cmd_run(args)
    out = capsys.readouterr().out
    assert "No active runtime" in out
    assert "opc use" in out


def test_cmd_tail_handles_stream_error(capsys):
    """OpcClient.stream calls raise_for_status — a 404 for an unknown task id
    must surface as a clean message, not an httpx traceback."""
    import httpx

    from src.cli import cmd_tail

    response = MagicMock(status_code=404)
    fake = MagicMock()
    fake.stream.side_effect = httpx.HTTPStatusError(
        "not found", request=MagicMock(), response=response,
    )
    with patch("src.cli.OpcClient.from_env", return_value=fake):
        args = MagicMock(task_id="TASK-X")
        with pytest.raises(SystemExit):
            cmd_tail(args)
    out = capsys.readouterr().out
    assert "TASK-X" in out
    assert "404" in out


def test_cmd_report_completion_posts_with_session_id():
    from src.cli import cmd_report_completion

    fake = MagicMock()
    fake.post.return_value.status_code = 200
    args = MagicMock(
        from_file=None,
        task_id="TASK-001", session_id="sess-1", agent="dev_agent",
        status="completed", confidence=90, summary="ok",
        risks=[], dependencies=[], reviewer_focus=[],
    )
    with patch("src.cli.OpcClient.from_env", return_value=fake):
        cmd_report_completion(args)
    args_pos, kwargs = fake.post.call_args
    assert args_pos[0] == "/api/v1/tasks/TASK-001/completion"
    assert kwargs["json"]["session_id"] == "sess-1"


def test_cmd_report_completion_from_file_posts_loaded_body(tmp_path):
    """--from-file lets agents submit a completion as a single-line command.
    Multi-line bash (backslash continuations) breaks Claude Code's
    Bash(opc:*) allow rule because newlines separate subcommands."""
    import json
    from src.cli import cmd_report_completion

    payload = {
        "task_id": "TASK-042",
        "session_id": "sess-x",
        "agent": "dev_agent",
        "status": "completed",
        "confidence": 85,
        "summary": "Wired Alipay happy path",
        "risks": ["refund flow untested"],
        "dependencies": ["ALIPAY_APP_ID env var"],
        "reviewer_focus": ["signature canonicalization"],
    }
    completion_file = tmp_path / "completion.json"
    completion_file.write_text(json.dumps(payload))

    fake = MagicMock()
    fake.post.return_value.status_code = 200
    args = MagicMock(
        from_file=str(completion_file),
        task_id=None, session_id=None, agent=None,
        status=None, confidence=80, summary=None,
        risks=[], dependencies=[], reviewer_focus=[],
    )
    with patch("src.cli.OpcClient.from_env", return_value=fake):
        cmd_report_completion(args)

    args_pos, kwargs = fake.post.call_args
    assert args_pos[0] == "/api/v1/tasks/TASK-042/completion"
    body = kwargs["json"]
    assert body["session_id"] == "sess-x"
    assert body["agent"] == "dev_agent"
    assert body["status"] == "completed"
    assert body["confidence"] == 85
    assert body["output_summary"] == "Wired Alipay happy path"
    assert body["risks_flagged"] == ["refund flow untested"]
    assert body["dependencies"] == ["ALIPAY_APP_ID env var"]
    assert body["suggested_reviewer_focus"] == ["signature canonicalization"]


def test_report_completion_parser_accepts_from_file_alone():
    """With --from-file, none of --task-id/--session-id/... are required."""
    parser = build_parser()
    args = parser.parse_args([
        "report-completion", "--from-file", "/tmp/x.json",
    ])
    assert args.from_file == "/tmp/x.json"
    assert args.task_id is None
    assert args.summary is None


def test_cmd_learning_posts_with_session_id():
    from src.cli import cmd_learning

    fake = MagicMock()
    fake.post.return_value.status_code = 200
    args = MagicMock(
        task_id="TASK-001", session_id="sess-1",
        agent="dev_agent", text="x",
    )
    with patch("src.cli.OpcClient.from_env", return_value=fake):
        cmd_learning(args)
    args_pos, kwargs = fake.post.call_args
    assert args_pos[0] == "/api/v1/agents/dev_agent/learnings"
    assert kwargs["json"]["session_id"] == "sess-1"


def test_cmd_report_completion_session_mismatch_friendly_message(capsys):
    """409 session_mismatch must print a clean sentence, not raw JSON."""
    from src.cli import cmd_report_completion

    fake = MagicMock()
    fake.post.return_value.status_code = 409
    fake.post.return_value.json.return_value = {
        "detail": {"code": "session_mismatch", "active": "sess-real", "got": "sess-stale"},
    }
    args = MagicMock(
        from_file=None,
        task_id="TASK-001", session_id="sess-stale", agent="dev_agent",
        status="completed", confidence=80, summary="x",
        risks=[], dependencies=[], reviewer_focus=[],
    )
    with patch("src.cli.OpcClient.from_env", return_value=fake):
        with pytest.raises(SystemExit):
            cmd_report_completion(args)
    out = capsys.readouterr().out
    assert "Session id mismatch" in out
    assert "sess-real" in out
    assert "sess-stale" in out


def test_cmd_learning_unknown_session_friendly_message(capsys):
    """409 unknown_session must print a clean sentence, not raw JSON."""
    from src.cli import cmd_learning

    fake = MagicMock()
    fake.post.return_value.status_code = 409
    fake.post.return_value.json.return_value = {
        "detail": {"code": "unknown_session", "task_id": "TASK-001", "agent": "dev_agent"},
    }
    args = MagicMock(
        task_id="TASK-001", session_id="ghost", agent="dev_agent", text="x",
    )
    with patch("src.cli.OpcClient.from_env", return_value=fake):
        with pytest.raises(SystemExit):
            cmd_learning(args)
    out = capsys.readouterr().out
    assert "Session not recognised" in out
    assert "TASK-001" in out
    assert "dev_agent" in out


def test_cmd_init_agent_surfaces_error_detail(capsys):
    """Daemon-emitted error frames must show the `detail` so users see what broke."""
    from src.cli import cmd_init_agent

    fake = MagicMock()
    fake.stream.return_value = iter([
        '{"agent": "dev_agent", "phase": "starting"}',
        '{"agent": "dev_agent", "phase": "error", "detail": "repo clone failed: fatal"}',
    ])
    with patch("src.cli.OpcClient.from_env", return_value=fake):
        args = MagicMock(agent="dev_agent")
        cmd_init_agent(args)
    out = capsys.readouterr().out
    assert "[dev_agent] starting" in out
    assert "[dev_agent] error: repo clone failed: fatal" in out


def test_audit_subcommand_defaults():
    parser = build_parser()
    args = parser.parse_args(["audit"])
    assert args.command == "audit"
    assert args.task_id is None
    assert args.agent is None
    assert args.action is None
    assert args.since is None
    assert args.limit is None
    assert args.json is False


def test_audit_subcommand_with_filters():
    parser = build_parser()
    args = parser.parse_args([
        "audit", "TASK-007",
        "--agent", "engineering_head",
        "--action", "session_end",
        "--limit", "5",
        "--json",
    ])
    assert args.task_id == "TASK-007"
    assert args.agent == "engineering_head"
    assert args.action == "session_end"
    assert args.limit == 5
    assert args.json is True


def test_cmd_audit_sends_filters_and_prints_table(capsys):
    from src.cli import cmd_audit

    fake = MagicMock()
    fake.get.return_value.status_code = 200
    fake.get.return_value.json.return_value = {
        "entries": [
            {
                "id": 1,
                "task_id": "TASK-001",
                "agent": "dev_agent",
                "action": "session_start",
                "payload": {"workspace": "/tmp/a"},
                "timestamp": "2026-04-16T12:00:00+00:00",
            },
        ],
    }
    with patch("src.cli.OpcClient.from_env", return_value=fake):
        cmd_audit(MagicMock(
            task_id="TASK-001", agent=None, action=None,
            since=None, limit=3, json=False,
        ))
    # Verify params were forwarded correctly (only non-None filters).
    call_kwargs = fake.get.call_args.kwargs
    assert call_kwargs["params"] == {"task_id": "TASK-001", "limit": 3}
    out = capsys.readouterr().out
    assert "TASK-001" in out
    assert "session_start" in out
    assert "dev_agent" in out


def test_cmd_audit_empty_result_message(capsys):
    from src.cli import cmd_audit

    fake = MagicMock()
    fake.get.return_value.status_code = 200
    fake.get.return_value.json.return_value = {"entries": []}
    with patch("src.cli.OpcClient.from_env", return_value=fake):
        cmd_audit(MagicMock(
            task_id=None, agent=None, action=None, since=None, limit=None, json=False,
        ))
    assert "No audit entries" in capsys.readouterr().out


def test_cmd_audit_json_flag_dumps_raw(capsys):
    import json as _json

    from src.cli import cmd_audit

    fake = MagicMock()
    fake.get.return_value.status_code = 200
    entries = [{"id": 9, "task_id": "T", "agent": "a", "action": "x", "payload": None,
                "timestamp": "2026-01-01T00:00:00+00:00"}]
    fake.get.return_value.json.return_value = {"entries": entries}
    with patch("src.cli.OpcClient.from_env", return_value=fake):
        cmd_audit(MagicMock(
            task_id=None, agent=None, action=None, since=None, limit=None, json=True,
        ))
    parsed = _json.loads(capsys.readouterr().out)
    assert parsed == entries


def test_cmd_init_agent_handles_stream_http_error(capsys):
    """OpcClient.stream calls raise_for_status — a 409 from /agents/init must
    surface as a clean message, not an httpx traceback."""
    import httpx

    from src.cli import cmd_init_agent

    response = MagicMock(status_code=409)
    fake = MagicMock()
    fake.stream.side_effect = httpx.HTTPStatusError(
        "conflict", request=MagicMock(), response=response,
    )
    with patch("src.cli.OpcClient.from_env", return_value=fake):
        args = MagicMock(agent=None)
        with pytest.raises(SystemExit):
            cmd_init_agent(args)
    out = capsys.readouterr().out
    assert "init stream failed" in out


def test_manage_repo_parser_add():
    parser = build_parser()
    args = parser.parse_args([
        "manage-repo", "add",
        "--agent", "dev_agent",
        "--repo-name", "docs",
        "--url", "https://github.com/t-benze/docs.git",
    ])
    assert args.command == "manage-repo"
    assert args.action == "add"
    assert args.agent == "dev_agent"
    assert args.repo_name == "docs"
    assert args.url == "https://github.com/t-benze/docs.git"


def test_manage_repo_parser_remove():
    parser = build_parser()
    args = parser.parse_args([
        "manage-repo", "remove",
        "--agent", "dev_agent",
        "--repo-name", "docs",
    ])
    assert args.action == "remove"
    assert args.url is None


def test_manage_repo_parser_from_file():
    parser = build_parser()
    args = parser.parse_args([
        "manage-repo", "--from-file", "/tmp/repo.json",
    ])
    assert args.from_file == "/tmp/repo.json"


def test_cmd_manage_repo_posts_to_daemon():
    from src.cli import cmd_manage_repo

    fake = MagicMock()
    fake.post.return_value.status_code = 200
    fake.post.return_value.json.return_value = {"ok": True}
    args = MagicMock(
        from_file=None,
        action="add", agent="dev_agent",
        repo_name="docs", url="https://github.com/t-benze/docs.git",
    )
    with patch("src.cli.OpcClient.from_env", return_value=fake):
        cmd_manage_repo(args)
    args_pos, kwargs = fake.post.call_args
    assert args_pos[0] == "/api/v1/agents/dev_agent/repos"
    assert kwargs["json"]["action"] == "add"
    assert kwargs["json"]["repo_name"] == "docs"
    assert kwargs["json"]["url"] == "https://github.com/t-benze/docs.git"


def test_cmd_manage_repo_from_file(tmp_path):
    import json

    from src.cli import cmd_manage_repo

    payload = {
        "action": "remove",
        "agent": "dev_agent",
        "repo_name": "docs",
    }
    f = tmp_path / "repo.json"
    f.write_text(json.dumps(payload))

    fake = MagicMock()
    fake.post.return_value.status_code = 200
    fake.post.return_value.json.return_value = {"ok": True}
    args = MagicMock(
        from_file=str(f),
        action=None, agent=None, repo_name=None, url=None,
    )
    with patch("src.cli.OpcClient.from_env", return_value=fake):
        cmd_manage_repo(args)
    args_pos, kwargs = fake.post.call_args
    assert args_pos[0] == "/api/v1/agents/dev_agent/repos"
    assert kwargs["json"]["action"] == "remove"
    assert kwargs["json"]["repo_name"] == "docs"
