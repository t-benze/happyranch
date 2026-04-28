from unittest.mock import MagicMock, patch

import pytest

from src.cli import build_parser, resolve_org_slug


def test_run_subcommand():
    parser = build_parser()
    args = parser.parse_args([
        "run",
        "--team", "engineering",
        "--brief", "Add Alipay support",
    ])
    assert args.command == "run"
    assert args.team == "engineering"
    assert args.brief == "Add Alipay support"


def test_details_subcommand():
    parser = build_parser()
    args = parser.parse_args(["details", "TASK-001"])
    assert args.command == "details"
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
    args = parser.parse_args(["init", "/tmp/my-runtime", "--slug", "hk-tourism"])
    assert args.command == "init"
    assert args.path == "/tmp/my-runtime"
    assert args.slug == "hk-tourism"


def test_init_subcommand_requires_slug():
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["init", "/tmp/my-runtime"])


def test_no_command_prints_help(capsys):
    parser = build_parser()
    args = parser.parse_args([])
    assert args.command is None




def test_run_without_team_flag():
    parser = build_parser()
    args = parser.parse_args(["run", "--brief", "Explore the codebase"])
    assert args.command == "run"
    assert args.team is None
    assert args.brief == "Explore the codebase"


def test_run_with_team_flag():
    parser = build_parser()
    args = parser.parse_args(["run", "--team", "content", "--brief", "Write guide"])
    assert args.team == "content"


def test_cmd_init_calls_register_endpoint(tmp_path, capsys):
    from src.cli import cmd_init

    fake_client = MagicMock()
    fake_client.post.return_value.status_code = 200
    fake_client.post.return_value.json.return_value = {
        "active": str(tmp_path / "rt"),
        "registered": [str(tmp_path / "rt")],
    }

    with patch("src.cli.OpcClient.from_env", return_value=fake_client):
        args = MagicMock(path=str(tmp_path / "rt"), slug="hk-tourism")
        cmd_init(args)

    fake_client.post.assert_called_once_with(
        "/api/v1/runtimes/register",
        json={"path": str(tmp_path / "rt"), "slug": "hk-tourism"},
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
        {"id": "TASK-001", "team": "engineering", "status": "approved", "brief": "x"},
    ]}
    with patch("src.cli.OpcClient.from_env", return_value=fake):
        args = MagicMock(limit=20)
        cmd_tasks(args)
    fake.get.assert_called_once_with("/api/v1/tasks", params={"limit": 20})
    assert "TASK-001" in capsys.readouterr().out


def test_cmd_tasks_shows_assigned_agent_column(capsys):
    """The table must surface which agent owns each task so the founder can
    see at a glance whether a root task is being handled by EH or a worker
    (and, for child tasks, which worker). Without this, distinguishing EH
    orchestrations from actual worker runs requires drilling into `opc details`.
    """
    from src.cli import cmd_tasks

    fake = MagicMock()
    fake.get.return_value.status_code = 200
    fake.get.return_value.json.return_value = {"tasks": [
        {
            "id": "TASK-020", "team": "engineering", "status": "in_progress",
            "brief": "Fix the save button",
            "assigned_agent": "dev_agent",
        },
        {
            "id": "TASK-018", "team": "engineering", "status": "in_progress",
            "brief": "Re-dispatch of TASK-017",
            "assigned_agent": "engineering_head",
        },
        {
            "id": "TASK-021", "team": "engineering", "status": "pending",
            "brief": "Not yet assigned",
            "assigned_agent": None,
        },
    ]}
    with patch("src.cli.OpcClient.from_env", return_value=fake):
        args = MagicMock(limit=20)
        cmd_tasks(args)

    out = capsys.readouterr().out
    # Header
    assert "Agent" in out
    # Worker task shows the worker
    assert "dev_agent" in out
    # EH-owned root task shows EH
    assert "engineering_head" in out
    # Pending / unassigned renders a placeholder, not an empty slot
    # (makes eyeballing which tasks haven't started yet easy)
    lines = out.splitlines()
    pending_line = next(line for line in lines if "TASK-021" in line)
    # The pending task must have an Agent cell of some kind, not be silently
    # shortened. Either '-' or 'None' — test accepts either, but one must exist
    # between the status column and the brief.
    assert " - " in pending_line or " none " in pending_line.lower()


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


def test_cmd_details_handles_404(capsys):
    from src.cli import cmd_details

    fake = MagicMock()
    fake.get.return_value.status_code = 404
    with patch("src.cli.OpcClient.from_env", return_value=fake):
        args = MagicMock(task_id="TASK-X")
        with pytest.raises(SystemExit):
            cmd_details(args)
    assert "not found" in capsys.readouterr().out


def test_cmd_run_submits_and_returns_without_streaming(capsys):
    """Submission is fire-and-forget; the CLI must NOT call client.stream(),
    and must surface the tail hint so the user knows how to attach later."""
    from src.cli import cmd_run

    fake = MagicMock()
    fake.post.return_value.status_code = 200
    fake.post.return_value.json.return_value = {"task_id": "TASK-001"}

    with patch("src.cli.OpcClient.from_env", return_value=fake):
        args = MagicMock(team=None, brief="x")
        cmd_run(args)

    fake.post.assert_called_once_with("/api/v1/tasks", json={"brief": "x"})
    fake.stream.assert_not_called()
    out = capsys.readouterr().out
    assert "TASK-001" in out
    assert "opc tail TASK-001" in out


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


def test_completion_payload_from_file_accepts_artifact_dir(tmp_path):
    import json as _json
    from src.cli import _completion_payload_from_file

    path = tmp_path / "c.json"
    path.write_text(_json.dumps({
        "task_id": "TASK-001",
        "session_id": "s",
        "agent": "dev_agent",
        "status": "completed",
        "summary": "done",
        "artifact_dir": "artifacts/TASK-001",
    }))
    task_id, body = _completion_payload_from_file(str(path))
    assert task_id == "TASK-001"
    assert body["artifact_dir"] == "artifacts/TASK-001"


def test_completion_payload_from_file_artifact_optional(tmp_path):
    import json as _json
    from src.cli import _completion_payload_from_file

    path = tmp_path / "c.json"
    path.write_text(_json.dumps({
        "task_id": "T", "session_id": "s", "agent": "a",
        "status": "completed", "summary": "done",
    }))
    _, body = _completion_payload_from_file(str(path))
    assert body.get("artifact_dir") is None


def test_completion_payload_from_file_passes_decision_through(tmp_path):
    """EH's completion JSON carries an optional `decision` field that the
    orchestrator acts on (delegate/done/escalate). The CLI must pass it
    through to the daemon body verbatim."""
    import json as _json
    from src.cli import _completion_payload_from_file

    path = tmp_path / "eh.json"
    path.write_text(_json.dumps({
        "task_id": "TASK-001",
        "session_id": "sess-eh",
        "agent": "engineering_head",
        "status": "completed",
        "summary": "Triaged and delegated.",
        "decision": {
            "action": "delegate",
            "agent": "dev_agent",
            "prompt": "Implement X",
        },
    }))
    _, body = _completion_payload_from_file(str(path))
    assert body["decision"] == {
        "action": "delegate",
        "agent": "dev_agent",
        "prompt": "Implement X",
    }
    # Prose summary stays in output_summary — the two fields are orthogonal.
    assert body["output_summary"] == "Triaged and delegated."


def test_completion_payload_from_file_omits_decision_when_absent(tmp_path):
    """Workers (and EH on legacy skills) don't include `decision`. The CLI
    must NOT synthesize a key — absence is the signal to the parser that the
    legacy prose-JSON path applies."""
    import json as _json
    from src.cli import _completion_payload_from_file

    path = tmp_path / "w.json"
    path.write_text(_json.dumps({
        "task_id": "T", "session_id": "s", "agent": "dev_agent",
        "status": "completed", "summary": "done",
    }))
    _, body = _completion_payload_from_file(str(path))
    assert "decision" not in body


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


def test_manage_agent_parser_enroll():
    parser = build_parser()
    args = parser.parse_args([
        "manage-agent", "enroll",
        "--from-file", "/tmp/enroll.json",
    ])
    assert args.command == "manage-agent"
    assert args.action == "enroll"
    assert args.from_file == "/tmp/enroll.json"


def test_manage_agent_parser_terminate():
    parser = build_parser()
    args = parser.parse_args([
        "manage-agent", "terminate",
        "--name", "content_writer",
        "--task-id", "TASK-001",
        "--session-id", "sess-123",
    ])
    assert args.action == "terminate"
    assert args.name == "content_writer"
    assert args.task_id == "TASK-001"
    assert args.session_id == "sess-123"


def test_cmd_manage_agent_posts_to_daemon():
    import argparse

    from src.cli import cmd_manage_agent

    fake = MagicMock()
    fake.post.return_value.status_code = 200
    fake.post.return_value.json.return_value = {"ok": True, "status": "pending"}
    args = argparse.Namespace(
        from_file=None,
        action="enroll", name="content_writer",
        task_id="TASK-001", session_id="sess-123",
        description="Writes guides", system_prompt="You are...",
        repos=None,
    )
    with patch("src.cli.OpcClient.from_env", return_value=fake):
        cmd_manage_agent(args)
    args_pos, kwargs = fake.post.call_args
    assert args_pos[0] == "/api/v1/agents/manage"
    assert kwargs["json"]["action"] == "enroll"
    assert kwargs["json"]["name"] == "content_writer"


def test_cmd_manage_agent_from_file(tmp_path):
    import json

    from src.cli import cmd_manage_agent

    payload = {
        "action": "enroll",
        "name": "content_writer",
        "task_id": "TASK-001",
        "session_id": "sess-123",
        "description": "Writes guides",
        "system_prompt": "You are the Content Writer...",
    }
    f = tmp_path / "enroll.json"
    f.write_text(json.dumps(payload))

    fake = MagicMock()
    fake.post.return_value.status_code = 200
    fake.post.return_value.json.return_value = {"ok": True, "status": "pending"}
    args = MagicMock(
        from_file=str(f),
        action=None, name=None, description=None,
        system_prompt=None, repos=None,
    )
    with patch("src.cli.OpcClient.from_env", return_value=fake):
        cmd_manage_agent(args)
    args_pos, kwargs = fake.post.call_args
    assert kwargs["json"]["action"] == "enroll"
    assert kwargs["json"]["name"] == "content_writer"


def test_cmd_manage_agent_from_file_talk_path(tmp_path):
    import json

    from src.cli import cmd_manage_agent

    payload = {
        "action": "enroll",
        "name": "content_writer",
        "talk_id": "TALK-002",
        "description": "Writes guides",
        "system_prompt": "You are the Content Writer...",
    }
    f = tmp_path / "enroll.json"
    f.write_text(json.dumps(payload))

    fake = MagicMock()
    fake.post.return_value.status_code = 200
    fake.post.return_value.json.return_value = {"ok": True, "status": "pending"}
    args = MagicMock(
        from_file=str(f),
        action=None, name=None, description=None,
        system_prompt=None, repos=None,
    )
    with patch("src.cli.OpcClient.from_env", return_value=fake):
        cmd_manage_agent(args)
    _args_pos, kwargs = fake.post.call_args
    assert kwargs["json"]["talk_id"] == "TALK-002"
    assert "task_id" not in kwargs["json"]
    assert "session_id" not in kwargs["json"]


def test_manage_agent_payload_from_file_rejects_mixed_auth(tmp_path):
    import json

    from src.cli import _manage_agent_payload_from_file

    f = tmp_path / "mixed.json"
    f.write_text(json.dumps({
        "action": "enroll",
        "name": "content_writer",
        "task_id": "TASK-001",
        "session_id": "sess-1",
        "talk_id": "TALK-002",
    }))
    with pytest.raises(ValueError, match="not both"):
        _manage_agent_payload_from_file(str(f))


def test_manage_agent_payload_from_file_rejects_no_auth(tmp_path):
    import json

    from src.cli import _manage_agent_payload_from_file

    f = tmp_path / "noauth.json"
    f.write_text(json.dumps({"action": "enroll", "name": "content_writer"}))
    with pytest.raises(ValueError, match="task_id \\+ session_id"):
        _manage_agent_payload_from_file(str(f))


def test_manage_agent_parser_accepts_talk_id():
    parser = build_parser()
    args = parser.parse_args([
        "manage-agent", "enroll",
        "--name", "content_writer",
        "--talk-id", "TALK-002",
    ])
    assert args.talk_id == "TALK-002"
    assert args.task_id is None


def test_enrollments_parser():
    parser = build_parser()
    args = parser.parse_args(["enrollments", "--status", "pending"])
    assert args.command == "enrollments"
    assert args.status == "pending"


def test_cmd_enrollments_lists(capsys):
    from src.cli import cmd_enrollments

    fake = MagicMock()
    fake.get.return_value.status_code = 200
    fake.get.return_value.json.return_value = {
        "enrollments": [
            {"name": "content_writer", "description": "Writes", "status": "pending",
             "created_at": "2026-04-17T00:00:00"},
        ],
    }
    with patch("src.cli.OpcClient.from_env", return_value=fake):
        cmd_enrollments(MagicMock(status="pending"))
    out = capsys.readouterr().out
    assert "content_writer" in out
    assert "pending" in out


def test_approve_agent_parser():
    parser = build_parser()
    args = parser.parse_args(["approve-agent", "content_writer"])
    assert args.command == "approve-agent"
    assert args.name == "content_writer"


def test_cmd_approve_agent_posts(capsys):
    import argparse

    from src.cli import cmd_approve_agent

    fake = MagicMock()
    fake.post.return_value.status_code = 200
    fake.post.return_value.json.return_value = {"ok": True}
    with patch("src.cli.OpcClient.from_env", return_value=fake):
        cmd_approve_agent(argparse.Namespace(name="content_writer"))
    fake.post.assert_called_once_with("/api/v1/agents/content_writer/approve", json={})
    assert "approved" in capsys.readouterr().out.lower()


def test_reject_agent_parser():
    parser = build_parser()
    args = parser.parse_args(["reject-agent", "content_writer"])
    assert args.command == "reject-agent"
    assert args.name == "content_writer"


def test_cli_recall_parses_flags():
    parser = build_parser()
    args = parser.parse_args(["recall", "TASK-001", "--tree", "--fetch-artifact"])
    assert args.command == "recall"
    assert args.task_id == "TASK-001"
    assert args.tree is True
    assert args.fetch_artifact is True


def test_cli_recall_defaults():
    parser = build_parser()
    args = parser.parse_args(["recall", "TASK-001"])
    assert args.task_id == "TASK-001"
    assert args.tree is False
    assert args.fetch_artifact is False


def test_cmd_recall_prints_payload(capsys):
    import argparse
    import json as _json
    from src.cli import cmd_recall

    fake = MagicMock()
    fake.get.return_value.status_code = 200
    fake.get.return_value.json.return_value = {"task_id": "TASK-001", "brief": "hi"}
    with patch("src.cli.OpcClient.from_env", return_value=fake):
        cmd_recall(argparse.Namespace(task_id="TASK-001", tree=False, fetch_artifact=False))
    fake.get.assert_called_once_with("/api/v1/tasks/TASK-001/recall", params={})
    out = capsys.readouterr().out
    assert _json.loads(out)["task_id"] == "TASK-001"


def test_cmd_recall_forwards_tree_and_artifact_params():
    import argparse
    from src.cli import cmd_recall

    fake = MagicMock()
    fake.get.return_value.status_code = 200
    fake.get.return_value.json.return_value = {}
    with patch("src.cli.OpcClient.from_env", return_value=fake):
        cmd_recall(argparse.Namespace(task_id="TASK-001", tree=True, fetch_artifact=True))
    fake.get.assert_called_once_with(
        "/api/v1/tasks/TASK-001/recall",
        params={"tree": "true", "include_artifact": "true"},
    )


def test_cmd_recall_404_exits(capsys):
    import argparse
    from src.cli import cmd_recall

    fake = MagicMock()
    fake.get.return_value.status_code = 404
    with patch("src.cli.OpcClient.from_env", return_value=fake):
        with pytest.raises(SystemExit):
            cmd_recall(argparse.Namespace(task_id="TASK-404", tree=False, fetch_artifact=False))
    assert "not found" in capsys.readouterr().out.lower()


def test_cli_has_kb_subcommands():
    from src.cli import build_parser
    parser = build_parser()
    sub = next(a for a in parser._actions if a.__class__.__name__ == "_SubParsersAction")
    assert "kb" in sub.choices
    kb = sub.choices["kb"]
    kb_sub = next(a for a in kb._actions if a.__class__.__name__ == "_SubParsersAction")
    for name in ("list", "get", "search", "add", "update", "delete", "reindex", "precedent"):
        assert name in kb_sub.choices, f"missing kb subcommand: {name}"


def test_cli_has_resolve_escalation():
    from src.cli import build_parser
    parser = build_parser()
    sub = next(a for a in parser._actions if a.__class__.__name__ == "_SubParsersAction")
    assert "resolve-escalation" in sub.choices


def test_kb_add_requires_from_file():
    from src.cli import build_parser
    parser = build_parser()
    # parse_args raises SystemExit(2) on missing required args
    import pytest as _pytest
    with _pytest.raises(SystemExit):
        parser.parse_args(["kb", "add", "--agent", "dev_agent"])


def test_kb_delete_parses_confirm_and_as_founder():
    from src.cli import build_parser
    parser = build_parser()
    ns = parser.parse_args([
        "kb", "delete", "alipay-refund", "--agent", "engineering_head",
        "--confirm", "--as-founder",
    ])
    assert ns.confirm is True
    assert ns.as_founder is True


def test_cmd_tasks_shows_block_kind_when_present(capsys):
    """A blocked task should show its block_kind alongside status."""
    from src.cli import cmd_tasks
    from argparse import Namespace
    from unittest.mock import MagicMock, patch

    client = MagicMock()
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {"tasks": [
        {"id": "T-1", "team": "engineering", "status": "blocked",
         "assigned_agent": "engineering_head", "brief": "waiting",
         "block_kind": "delegated"},
        {"id": "T-2", "team": "engineering", "status": "completed",
         "assigned_agent": "engineering_head", "brief": "done",
         "block_kind": None},
    ]}
    client.get.return_value = response
    with patch("src.cli.OpcClient.from_env", return_value=client):
        cmd_tasks(Namespace(limit=10))
    out = capsys.readouterr().out
    assert "blocked(delegated)" in out or "blocked (delegated)" in out
    assert "completed" in out


def test_cmd_tasks_renders_team_column(capsys):
    """Regression: the task-list table must read the `team` column, not the
    retired `type` column. Rendering a payload that matches the real API
    response (no `type` key) used to raise KeyError and crash `opc tasks`.
    """
    from src.cli import cmd_tasks
    from argparse import Namespace

    fake = MagicMock()
    fake.get.return_value.status_code = 200
    fake.get.return_value.json.return_value = {"tasks": [
        {"id": "TASK-030", "team": "content", "status": "in_progress",
         "assigned_agent": "content_manager", "brief": "Draft Macau visa guide"},
        {"id": "TASK-031", "team": "engineering", "status": "completed",
         "assigned_agent": "engineering_head", "brief": "Add Alipay"},
    ]}
    with patch("src.cli.OpcClient.from_env", return_value=fake):
        cmd_tasks(Namespace(limit=20))
    out = capsys.readouterr().out
    assert "Team" in out
    assert "Type" not in out.splitlines()[0]
    assert "content" in out
    assert "engineering" in out


def test_talk_start_parses():
    parser = build_parser()
    args = parser.parse_args(["talk", "start", "--agent", "dev_agent"])
    assert args.command == "talk"
    assert args.talk_command == "start"
    assert args.agent == "dev_agent"


def test_talk_resume_parses():
    parser = build_parser()
    args = parser.parse_args(["talk", "resume", "--talk-id", "TALK-001"])
    assert args.command == "talk"
    assert args.talk_command == "resume"
    assert args.talk_id == "TALK-001"


def test_talk_abandon_parses():
    parser = build_parser()
    args = parser.parse_args([
        "talk", "abandon", "--talk-id", "TALK-001", "--reason", "orphan",
    ])
    assert args.command == "talk"
    assert args.talk_command == "abandon"
    assert args.talk_id == "TALK-001"
    assert args.reason == "orphan"


def test_talk_abandon_parses_default_reason():
    parser = build_parser()
    args = parser.parse_args(["talk", "abandon", "--talk-id", "TALK-001"])
    assert args.reason == "manual"


def test_cmd_talk_start_prints_id(capsys):
    from src.cli import cmd_talk_start

    fake = MagicMock()
    fake.post.return_value.status_code = 200
    fake.post.return_value.json.return_value = {
        "talk_id": "TALK-007",
        "started_at": "2026-04-21T10:00:00+00:00",
    }
    with patch("src.cli.OpcClient.from_env", return_value=fake):
        args = MagicMock(agent="dev_agent")
        cmd_talk_start(args)
    fake.post.assert_called_once_with("/api/v1/talks", json={"agent_name": "dev_agent"})
    out = capsys.readouterr().out
    assert "TALK-007" in out


def test_cmd_talk_start_conflict_exits_with_message(capsys):
    from src.cli import cmd_talk_start

    fake = MagicMock()
    fake.post.return_value.status_code = 409
    fake.post.return_value.json.return_value = {
        "detail": {
            "code": "talk_already_open",
            "prior_open_talk_id": "TALK-003",
            "prior_started_at": "2026-04-20T09:00:00+00:00",
        },
    }
    with patch("src.cli.OpcClient.from_env", return_value=fake):
        args = MagicMock(agent="dev_agent")
        with pytest.raises(SystemExit):
            cmd_talk_start(args)
    out = capsys.readouterr().out
    assert "TALK-003" in out
    # Friendly message should mention "already" or "open talk"
    assert "already" in out.lower() or "open talk" in out.lower()


def test_talk_end_parses():
    parser = build_parser()
    args = parser.parse_args([
        "talk", "end", "--talk-id", "TALK-001", "--from-file", "/tmp/x.json",
    ])
    assert args.command == "talk"
    assert args.talk_command == "end"
    assert args.talk_id == "TALK-001"
    assert args.from_file == "/tmp/x.json"


def test_cmd_talk_end_success(tmp_path, capsys):
    import json
    from argparse import Namespace

    from src.cli import cmd_talk_end

    payload = {
        "summary": "ok",
        "topic_list": [],
        "transcript_markdown": "t",
        "learnings": [{"text": "x"}, {"text": "y"}, {"text": "z"}],
        "kb_slugs": [],
    }
    payload_path = tmp_path / "end.json"
    payload_path.write_text(json.dumps(payload))

    fake = MagicMock()
    fake.post.return_value.status_code = 200
    fake.post.return_value.json.return_value = {
        "talk_id": "TALK-007",
        "status": "closed",
        "new_learnings_count": 3,
        "transcript_path": "/r/talks/TALK-007.md",
    }
    with patch("src.cli.OpcClient.from_env", return_value=fake):
        args = Namespace(talk_id="TALK-007", from_file=str(payload_path))
        cmd_talk_end(args)
    fake.post.assert_called_once_with(
        "/api/v1/talks/TALK-007/end", json=payload
    )
    out = capsys.readouterr().out
    assert "TALK-007" in out
    assert "closed" in out.lower() or "ok" in out.lower()


def test_cmd_talk_end_missing_file(tmp_path, capsys):
    from argparse import Namespace

    from src.cli import cmd_talk_end

    missing = tmp_path / "does-not-exist.json"
    fake = MagicMock()
    with patch("src.cli.OpcClient.from_env", return_value=fake):
        args = Namespace(talk_id="TALK-007", from_file=str(missing))
        with pytest.raises(SystemExit):
            cmd_talk_end(args)
    out = capsys.readouterr().out
    assert "Error reading" in out
    fake.post.assert_not_called()


def test_talk_status_parses():
    parser = build_parser()
    args = parser.parse_args(["talk", "status", "--agent", "dev_agent"])
    assert args.command == "talk"
    assert args.talk_command == "status"
    assert args.agent == "dev_agent"


def test_talk_list_parses_defaults():
    parser = build_parser()
    args = parser.parse_args(["talk", "list"])
    assert args.command == "talk"
    assert args.talk_command == "list"
    assert args.agent is None
    assert args.limit == 20


def test_talk_list_parses_with_limit():
    parser = build_parser()
    args = parser.parse_args(["talk", "list", "--limit", "5"])
    assert args.limit == 5


def test_talk_show_parses():
    parser = build_parser()
    args = parser.parse_args(["talk", "show", "TALK-007"])
    assert args.command == "talk"
    assert args.talk_command == "show"
    assert args.talk_id == "TALK-007"
    assert args.json is False


def test_talk_show_json_flag():
    parser = build_parser()
    args = parser.parse_args(["talk", "show", "TALK-007", "--json"])
    assert args.json is True


def test_cmd_talk_status_prints_open_talks(capsys):
    from argparse import Namespace

    from src.cli import cmd_talk_status

    fake = MagicMock()
    fake.get.return_value.status_code = 200
    fake.get.return_value.json.return_value = {
        "talks": [
            {
                "talk_id": "TALK-001",
                "agent_name": "dev_agent",
                "started_at": "2026-04-21T10:00:00+00:00",
            }
        ]
    }
    with patch("src.cli.OpcClient.from_env", return_value=fake):
        cmd_talk_status(Namespace(agent="dev_agent"))
    fake.get.assert_called_once_with(
        "/api/v1/talks", params={"status": "open", "agent": "dev_agent"}
    )
    out = capsys.readouterr().out
    assert "TALK-001" in out
    assert "dev_agent" in out


def test_cmd_talk_status_empty(capsys):
    from argparse import Namespace

    from src.cli import cmd_talk_status

    fake = MagicMock()
    fake.get.return_value.status_code = 200
    fake.get.return_value.json.return_value = {"talks": []}
    with patch("src.cli.OpcClient.from_env", return_value=fake):
        cmd_talk_status(Namespace(agent=None))
    out = capsys.readouterr().out
    assert "no open talks" in out


def test_cmd_talk_list_uses_limit(capsys):
    from argparse import Namespace

    from src.cli import cmd_talk_list

    fake = MagicMock()
    fake.get.return_value.status_code = 200
    fake.get.return_value.json.return_value = {
        "talks": [
            {
                "talk_id": "TALK-042",
                "status": "closed",
                "agent_name": "dev_agent",
                "started_at": "2026-04-20T10:00:00+00:00",
                "ended_at": "2026-04-20T11:00:00+00:00",
                "new_learnings_count": 2,
            }
        ]
    }
    with patch("src.cli.OpcClient.from_env", return_value=fake):
        cmd_talk_list(Namespace(agent="dev_agent", limit=5))
    fake.get.assert_called_once_with(
        "/api/v1/talks", params={"limit": 5, "agent": "dev_agent"}
    )
    out = capsys.readouterr().out
    assert "TALK-042" in out


def test_cmd_talk_show_human(capsys):
    from argparse import Namespace

    from src.cli import cmd_talk_show

    fake = MagicMock()
    fake.get.return_value.status_code = 200
    fake.get.return_value.json.return_value = {
        "talk_id": "TALK-007",
        "agent_name": "dev_agent",
        "status": "closed",
        "started_at": "2026-04-21T10:00:00+00:00",
        "ended_at": "2026-04-21T11:00:00+00:00",
        "topic_list": ["testing", "cli"],
        "summary": "We discussed testing.",
        "transcript": "founder: hi\nagent: hello",
        "new_learnings_count": 3,
        "new_kb_slugs": ["abc-123"],
    }
    with patch("src.cli.OpcClient.from_env", return_value=fake):
        cmd_talk_show(Namespace(talk_id="TALK-007", json=False))
    out = capsys.readouterr().out
    assert "TALK-007" in out
    assert "## Summary" in out
    assert "## Transcript" in out
    assert "testing" in out


def test_cmd_talk_show_json_mode(capsys):
    import json as _json
    from argparse import Namespace

    from src.cli import cmd_talk_show

    payload = {
        "talk_id": "TALK-007",
        "agent_name": "dev_agent",
        "status": "closed",
        "started_at": "2026-04-21T10:00:00+00:00",
        "ended_at": "2026-04-21T11:00:00+00:00",
        "topic_list": ["testing"],
        "summary": "We discussed testing.",
        "transcript": "founder: hi\nagent: hello",
        "new_learnings_count": 3,
        "new_kb_slugs": ["abc-123"],
    }
    fake = MagicMock()
    fake.get.return_value.status_code = 200
    fake.get.return_value.json.return_value = payload
    with patch("src.cli.OpcClient.from_env", return_value=fake):
        cmd_talk_show(Namespace(talk_id="TALK-007", json=True))
    out = capsys.readouterr().out
    data = _json.loads(out)
    assert data["talk_id"] == "TALK-007"
    assert data["agent_name"] == "dev_agent"


def test_cmd_details_shows_note(capsys):
    from src.cli import cmd_details
    from argparse import Namespace
    from unittest.mock import MagicMock, patch

    client = MagicMock()
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {
        "task": {
            "id": "T-1", "type": "general", "status": "completed",
            "assigned_agent": "engineering_head", "brief": "b",
            "created_at": "2026-04-19T00:00:00", "updated_at": "2026-04-19T00:00:00",
            "note": "Feature landed",
        },
        "results": [],
        "audit_log": [],
    }
    client.get.return_value = response
    with patch("src.cli.OpcClient.from_env", return_value=client):
        cmd_details(Namespace(task_id="T-1"))
    out = capsys.readouterr().out
    assert "Feature landed" in out


def test_cmd_revisit_rejects_non_tty(capsys, monkeypatch):
    """No TTY => abort before any HTTP call."""
    from src.cli import cmd_revisit

    fake = MagicMock()
    monkeypatch.setattr("src.cli.sys.stdin.isatty", lambda: False)
    monkeypatch.setattr("src.cli.sys.stdout.isatty", lambda: True)
    with patch("src.cli.OpcClient.from_env", return_value=fake):
        args = MagicMock(task_id="TASK-052", note=None)
        with pytest.raises(SystemExit):
            cmd_revisit(args)
    # Never touched the client.
    fake.post.assert_not_called()
    assert "interactive terminal" in capsys.readouterr().out


def test_cmd_revisit_aborts_on_negative_confirmation(capsys, monkeypatch):
    """TTY present but founder types 'n' => no POST."""
    from src.cli import cmd_revisit

    fake = MagicMock()
    monkeypatch.setattr("src.cli.sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("src.cli.sys.stdout.isatty", lambda: True)
    with patch("src.cli.OpcClient.from_env", return_value=fake), \
         patch("builtins.input", return_value="n"):
        args = MagicMock(task_id="TASK-052", note=None)
        with pytest.raises(SystemExit):
            cmd_revisit(args)
    fake.post.assert_not_called()


def test_cmd_revisit_submits_without_streaming_on_yes(capsys, monkeypatch):
    """'y' confirmation => POST, then return; no streaming. The tail hint must
    point at the new root id, not the predecessor."""
    from src.cli import cmd_revisit

    fake = MagicMock()
    fake.post.return_value.status_code = 200
    fake.post.return_value.json.return_value = {
        "new_root_task_id": "TASK-072",
        "predecessor_root_task_id": "TASK-052",
        "flagged_task_id": "TASK-052",
        "cascade": ["TASK-052"],
        "predecessor_status": "failed",
    }

    monkeypatch.setattr("src.cli.sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("src.cli.sys.stdout.isatty", lambda: True)
    with patch("src.cli.OpcClient.from_env", return_value=fake), \
         patch("builtins.input", return_value="y"):
        args = MagicMock(task_id="TASK-052", note="PR merged")
        cmd_revisit(args)

    fake.post.assert_called_once_with(
        "/api/v1/tasks/TASK-052/revisit",
        json={"founder_note": "PR merged"},
    )
    fake.stream.assert_not_called()
    out = capsys.readouterr().out
    assert "TASK-072" in out
    assert "opc tail TASK-072" in out


def test_cmd_details_shows_revisit_header_chain_and_footer(capsys):
    """When the task is a revisit AND has later revisits, details must show:
    - a `Revisit of:` header line with the predecessor id and prior_status
    - a `Chain:` line with the full chain, oldest leftmost, (this) marker
    - a `Revisited as:` footer line listing direct revisits
    """
    from src.cli import cmd_details
    fake = MagicMock()
    fake.get.return_value.status_code = 200
    fake.get.return_value.json.return_value = {
        "task": {
            "id": "TASK-072",
            "type": "implement_feature",
            "status": "pending",
            "assigned_agent": None,
            "brief": "Add Alipay support",
            "created_at": "2026-04-23T10:00:00+00:00",
            "updated_at": "2026-04-23T10:00:00+00:00",
            "revisit_of_task_id": "TASK-068",
        },
        "results": [],
        "audit_log": [],
        "revisit_chain": ["TASK-072", "TASK-068", "TASK-052"],
        "direct_revisits": ["TASK-091", "TASK-103"],
        "predecessor_prior_status": "failed-cancelled",
    }
    with patch("src.cli.OpcClient.from_env", return_value=fake):
        args = MagicMock(task_id="TASK-072")
        cmd_details(args)
    out = capsys.readouterr().out
    # Header
    assert "Revisit of: TASK-068" in out
    assert "failed-cancelled" in out
    # Chain: oldest-first, with (this) marker on the current task
    assert "TASK-052" in out
    assert "TASK-068" in out
    assert "TASK-072" in out
    assert "(this)" in out
    # Arrow direction — ← reads "created from"
    assert "←" in out
    # Footer
    assert "Revisited as: TASK-091, TASK-103" in out


def test_cmd_details_omits_revisit_blocks_when_plain_task(capsys):
    """Non-revisit task with no descendants must render cleanly — no empty
    'Revisit of:' / 'Chain:' / 'Revisited as:' lines."""
    from src.cli import cmd_details
    fake = MagicMock()
    fake.get.return_value.status_code = 200
    fake.get.return_value.json.return_value = {
        "task": {
            "id": "TASK-001",
            "type": "general",
            "status": "pending",
            "assigned_agent": None,
            "brief": "plain task",
            "created_at": "2026-04-23T10:00:00+00:00",
            "updated_at": "2026-04-23T10:00:00+00:00",
            "revisit_of_task_id": None,
        },
        "results": [],
        "audit_log": [],
        "revisit_chain": ["TASK-001"],
        "direct_revisits": [],
        "predecessor_prior_status": None,
    }
    with patch("src.cli.OpcClient.from_env", return_value=fake):
        args = MagicMock(task_id="TASK-001")
        cmd_details(args)
    out = capsys.readouterr().out
    assert "Revisit of:" not in out
    assert "Chain:" not in out
    assert "Revisited as:" not in out


def test_cmd_details_shows_footer_only_when_predecessor_has_revisits(capsys):
    """Predecessor-side view: task is NOT a revisit (no header/chain) but
    HAS been revisited (footer present)."""
    from src.cli import cmd_details
    fake = MagicMock()
    fake.get.return_value.status_code = 200
    fake.get.return_value.json.return_value = {
        "task": {
            "id": "TASK-052",
            "type": "general",
            "status": "failed",
            "assigned_agent": None,
            "brief": "the original",
            "created_at": "2026-04-21T10:00:00+00:00",
            "updated_at": "2026-04-21T10:00:00+00:00",
            "revisit_of_task_id": None,
        },
        "results": [],
        "audit_log": [],
        "revisit_chain": ["TASK-052"],
        "direct_revisits": ["TASK-072"],
        "predecessor_prior_status": None,
    }
    with patch("src.cli.OpcClient.from_env", return_value=fake):
        args = MagicMock(task_id="TASK-052")
        cmd_details(args)
    out = capsys.readouterr().out
    assert "Revisit of:" not in out
    assert "Chain:" not in out
    assert "Revisited as: TASK-072" in out


def test_cmd_details_renders_dispatched_from(capsys):
    """When a task was dispatched from a talk, `opc details` must show:
    - a `Dispatched from:` header line with the source talk id
    - the dispatcher agent + role pulled from the task_dispatched audit row
    The line appears after the (optional) revisit header and before the
    main task summary block.
    """
    from argparse import Namespace

    from src.cli import cmd_details

    fake = MagicMock()
    fake.get.return_value.status_code = 200
    fake.get.return_value.json.return_value = {
        "task": {
            "id": "TASK-042",
            "type": "general",
            "status": "pending",
            "assigned_agent": None,
            "brief": "Investigate daemon crash",
            "created_at": "2026-04-26T10:00:00+00:00",
            "updated_at": "2026-04-26T10:00:00+00:00",
            "dispatched_from_talk_id": "TALK-007",
        },
        "results": [],
        "audit_log": [
            {
                "timestamp": "2026-04-26T10:00:00+00:00",
                "agent": "dev_agent",
                "action": "task_dispatched",
                "payload": {
                    "talk_id": "TALK-007",
                    "dispatcher_agent": "dev_agent",
                    "dispatcher_role": "worker",
                },
            },
        ],
    }
    with patch("src.cli.OpcClient.from_env", return_value=fake):
        cmd_details(Namespace(task_id="TASK-042"))
    out = capsys.readouterr().out
    assert "Dispatched from: TALK-007" in out
    assert "dev_agent / worker" in out


def test_cmd_dispatch_happy_path(tmp_path):
    """`opc dispatch --from-file ...` POSTs to /talks/{talk_id}/dispatch with
    body shaped {brief, target_agent?, team?} — talk_id stays in the URL path
    and is NOT echoed in the request body."""
    import json
    from argparse import Namespace

    from src.cli import cmd_dispatch

    payload = {
        "talk_id": "TALK-001",
        "brief": "Investigate the daemon crash",
        "target_agent": "dev_agent",
        "team": "engineering",
    }
    payload_path = tmp_path / "dispatch.json"
    payload_path.write_text(json.dumps(payload))

    fake = MagicMock()
    fake.post.return_value.status_code = 200
    fake.post.return_value.json.return_value = {
        "task_id": "TASK-042",
        "team": "engineering",
        "assigned_agent": "dev_agent",
        "dispatched_from_talk_id": "TALK-001",
    }
    with patch("src.cli.OpcClient.from_env", return_value=fake):
        cmd_dispatch(Namespace(from_file=str(payload_path)))

    args_pos, kwargs = fake.post.call_args
    assert args_pos[0] == "/api/v1/talks/TALK-001/dispatch"
    body = kwargs["json"]
    assert body == {
        "brief": "Investigate the daemon crash",
        "target_agent": "dev_agent",
        "team": "engineering",
    }
    assert "talk_id" not in body


def test_cmd_dispatch_missing_talk_id_raises(tmp_path, capsys):
    """A from-file payload without `talk_id` should fail before the HTTP call."""
    import json
    from argparse import Namespace

    from src.cli import cmd_dispatch

    payload = {"brief": "Do the thing"}  # no talk_id
    payload_path = tmp_path / "bad.json"
    payload_path.write_text(json.dumps(payload))

    fake = MagicMock()
    with patch("src.cli.OpcClient.from_env", return_value=fake):
        with pytest.raises(SystemExit):
            cmd_dispatch(Namespace(from_file=str(payload_path)))
    fake.post.assert_not_called()


def test_cmd_dispatch_whitespace_talk_id_raises(tmp_path, capsys):
    """A from-file payload with a whitespace-only `talk_id` should fail before
    the HTTP call — symmetric with the `brief` strip-validation."""
    import json
    from argparse import Namespace

    from src.cli import cmd_dispatch

    payload = {"talk_id": "   ", "brief": "x"}  # whitespace-only talk_id
    payload_path = tmp_path / "bad.json"
    payload_path.write_text(json.dumps(payload))

    fake = MagicMock()
    with patch("src.cli.OpcClient.from_env", return_value=fake):
        with pytest.raises(SystemExit):
            cmd_dispatch(Namespace(from_file=str(payload_path)))
    fake.post.assert_not_called()


def test_cmd_tasks_suffixes_revisit_rows(capsys):
    """Tasks that have a predecessor root show `↩ TASK-XXX` as a trailing
    marker; plain tasks render unchanged."""
    from src.cli import cmd_tasks
    fake = MagicMock()
    fake.get.return_value.status_code = 200
    fake.get.return_value.json.return_value = {"tasks": [
        {
            "id": "TASK-072", "team": "engineering", "status": "pending",
            "brief": "Add Alipay support",
            "assigned_agent": None,
            "revisit_of_task_id": "TASK-052",
        },
        {
            "id": "TASK-001", "team": "engineering", "status": "completed",
            "brief": "plain task",
            "assigned_agent": "dev_agent",
            "revisit_of_task_id": None,
        },
    ]}
    with patch("src.cli.OpcClient.from_env", return_value=fake):
        args = MagicMock(limit=20)
        cmd_tasks(args)
    out = capsys.readouterr().out
    lines = out.splitlines()
    revisit_line = next(line for line in lines if "TASK-072" in line)
    plain_line = next(line for line in lines if "TASK-001" in line)
    assert "↩ TASK-052" in revisit_line
    assert "↩" not in plain_line


# ── resolve_org_slug ──────────────────────────────────────────


def test_resolve_org_explicit_flag_wins(monkeypatch) -> None:
    monkeypatch.setenv("OPC_ORG_SLUG", "from-env")
    available = ["alpha", "beta"]
    slug = resolve_org_slug(args_org="from-flag", available=available)
    assert slug == "from-flag"


def test_resolve_org_env_var(monkeypatch) -> None:
    monkeypatch.setenv("OPC_ORG_SLUG", "from-env")
    slug = resolve_org_slug(args_org=None, available=["alpha", "from-env"])
    assert slug == "from-env"


def test_resolve_org_auto_infer_single(monkeypatch) -> None:
    monkeypatch.delenv("OPC_ORG_SLUG", raising=False)
    slug = resolve_org_slug(args_org=None, available=["alpha"])
    assert slug == "alpha"


def test_resolve_org_zero_orgs_errors(monkeypatch, capsys) -> None:
    monkeypatch.delenv("OPC_ORG_SLUG", raising=False)
    with pytest.raises(SystemExit):
        resolve_org_slug(args_org=None, available=[])
    err = capsys.readouterr().err
    assert "no orgs registered" in err


def test_resolve_org_multi_errors(monkeypatch, capsys) -> None:
    monkeypatch.delenv("OPC_ORG_SLUG", raising=False)
    with pytest.raises(SystemExit):
        resolve_org_slug(args_org=None, available=["alpha", "beta"])
    err = capsys.readouterr().err
    assert "alpha" in err
    assert "beta" in err
