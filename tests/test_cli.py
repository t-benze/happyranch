from unittest.mock import MagicMock, patch

import pytest

from cli.main import build_parser, resolve_org_slug


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


def test_runtime_subcommand():
    parser = build_parser()
    args = parser.parse_args(["runtime"])
    assert args.command == "runtime"


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


def test_cmd_init_calls_runtime_endpoint(tmp_path, capsys):
    from cli.main import cmd_init

    fake_client = MagicMock()
    fake_client.post.return_value.status_code = 200
    fake_client.post.return_value.json.return_value = {
        "runtime": str(tmp_path / "rt"),
    }

    with patch("cli.main.OpcClient.from_env", return_value=fake_client):
        args = MagicMock(path=str(tmp_path / "rt"))
        cmd_init(args)

    fake_client.post.assert_called_once_with(
        "/api/v1/runtime",
        json={"path": str(tmp_path / "rt")},
    )
    out = capsys.readouterr().out
    assert f"runtime: {tmp_path / 'rt'}" in out


def test_cmd_use_calls_runtime_use_endpoint(tmp_path, capsys):
    from cli.main import cmd_use

    fake_client = MagicMock()
    fake_client.post.return_value.status_code = 200
    fake_client.post.return_value.json.return_value = {
        "runtime": str(tmp_path / "rt"),
    }

    with patch("cli.main.OpcClient.from_env", return_value=fake_client):
        args = MagicMock(path=str(tmp_path / "rt"))
        cmd_use(args)

    fake_client.post.assert_called_once_with(
        "/api/v1/runtime/use", json={"path": str(tmp_path / "rt")},
    )
    out = capsys.readouterr().out
    assert f"runtime: {tmp_path / 'rt'}" in out


def test_cmd_runtime_active(capsys):
    from cli.main import cmd_runtime

    fake_client = MagicMock()
    fake_client.get.return_value.status_code = 200
    fake_client.get.return_value.json.return_value = {"runtime": "/tmp/rt"}

    with patch("cli.main.OpcClient.from_env", return_value=fake_client):
        args = MagicMock()
        cmd_runtime(args)

    fake_client.get.assert_called_once_with("/api/v1/runtime")
    assert "runtime: /tmp/rt" in capsys.readouterr().out


def test_cmd_runtime_idle(capsys):
    from cli.main import cmd_runtime

    fake_client = MagicMock()
    fake_client.get.return_value.status_code = 200
    fake_client.get.return_value.json.return_value = {"runtime": None}

    with patch("cli.main.OpcClient.from_env", return_value=fake_client):
        args = MagicMock()
        cmd_runtime(args)

    fake_client.get.assert_called_once_with("/api/v1/runtime")
    assert "(no active runtime)" in capsys.readouterr().out


def test_cmd_tasks_calls_list_endpoint(capsys):
    from cli.main import cmd_tasks

    fake = MagicMock()
    fake.get.return_value.status_code = 200
    fake.get.return_value.json.return_value = {"tasks": [
        {"task_id": "TASK-001", "team": "engineering", "status": "approved", "brief": "x"},
    ]}
    with patch("cli.main.OpcClient.from_env", return_value=fake), \
         patch("cli.main._fetch_available_orgs", return_value=["alpha"]):
        args = MagicMock(org=None, limit=20)
        cmd_tasks(args)
    fake.get.assert_called_once_with("/api/v1/orgs/alpha/tasks", params={"limit": 20})
    assert "TASK-001" in capsys.readouterr().out


def test_cmd_tasks_shows_assigned_agent_column(capsys):
    """The table must surface which agent owns each task so the founder can
    see at a glance whether a root task is being handled by EH or a worker
    (and, for child tasks, which worker). Without this, distinguishing EH
    orchestrations from actual worker runs requires drilling into `happyranch details`.
    """
    from cli.main import cmd_tasks

    fake = MagicMock()
    fake.get.return_value.status_code = 200
    fake.get.return_value.json.return_value = {"tasks": [
        {
            "task_id": "TASK-020", "team": "engineering", "status": "in_progress",
            "brief": "Fix the save button",
            "assigned_agent": "dev_agent",
        },
        {
            "task_id": "TASK-018", "team": "engineering", "status": "in_progress",
            "brief": "Re-dispatch of TASK-017",
            "assigned_agent": "engineering_head",
        },
        {
            "task_id": "TASK-021", "team": "engineering", "status": "pending",
            "brief": "Not yet assigned",
            "assigned_agent": None,
        },
    ]}
    with patch("cli.main.OpcClient.from_env", return_value=fake), \
         patch("cli.main._fetch_available_orgs", return_value=["alpha"]):
        args = MagicMock(org=None, limit=20)
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
    from cli.main import cmd_tasks

    fake = MagicMock()
    fake.get.return_value.status_code = 409
    fake.get.return_value.json.return_value = {"detail": {"code": "no_active_runtime"}}
    with patch("cli.main.OpcClient.from_env", return_value=fake), \
         patch("cli.main._fetch_available_orgs", return_value=["alpha"]):
        args = MagicMock(org=None, limit=20)
        with pytest.raises(SystemExit):
            cmd_tasks(args)
    out = capsys.readouterr().out
    assert "No active runtime" in out
    assert "happyranch use" in out


def test_cmd_details_handles_404(capsys):
    from cli.main import cmd_details

    fake = MagicMock()
    fake.get.return_value.status_code = 404
    with patch("cli.main.OpcClient.from_env", return_value=fake), \
         patch("cli.main._fetch_available_orgs", return_value=["alpha"]):
        args = MagicMock(org=None, task_id="TASK-X")
        with pytest.raises(SystemExit):
            cmd_details(args)
    assert "not found" in capsys.readouterr().out


def test_cmd_run_submits_and_returns_without_streaming(capsys):
    """Submission is fire-and-forget; the CLI must NOT call client.stream(),
    and must surface the tail hint so the user knows how to attach later."""
    from cli.main import cmd_run

    fake = MagicMock()
    fake.post.return_value.status_code = 200
    fake.post.return_value.json.return_value = {"task_id": "TASK-001"}

    with patch("cli.main.OpcClient.from_env", return_value=fake), \
         patch("cli.main._fetch_available_orgs", return_value=["alpha"]):
        args = MagicMock(org=None, team=None, brief="x", brief_file=None)
        cmd_run(args)

    fake.post.assert_called_once_with("/api/v1/orgs/alpha/tasks", json={"brief": "x"})
    fake.stream.assert_not_called()
    out = capsys.readouterr().out
    assert "TASK-001" in out
    assert "happyranch tail TASK-001" in out


def test_cmd_run_reads_brief_from_file(tmp_path, capsys):
    """--brief-file reads the brief from disk and submits its contents."""
    from cli.main import cmd_run

    brief_path = tmp_path / "brief.md"
    brief_path.write_text("multi-line\nbrief content\n", encoding="utf-8")

    fake = MagicMock()
    fake.post.return_value.status_code = 200
    fake.post.return_value.json.return_value = {"task_id": "TASK-002"}
    with patch("cli.main.OpcClient.from_env", return_value=fake), \
         patch("cli.main._fetch_available_orgs", return_value=["alpha"]):
        args = MagicMock(org=None, team=None, brief=None, brief_file=str(brief_path))
        cmd_run(args)

    fake.post.assert_called_once_with(
        "/api/v1/orgs/alpha/tasks", json={"brief": "multi-line\nbrief content\n"}
    )


def test_cmd_run_brief_file_missing(tmp_path, capsys):
    """--brief-file with a nonexistent path exits with a friendly error."""
    from cli.main import cmd_run

    args = MagicMock(org=None, team=None, brief=None, brief_file=str(tmp_path / "nope.md"))
    fake = MagicMock()
    with patch("cli.main.OpcClient.from_env", return_value=fake), \
         patch("cli.main._fetch_available_orgs", return_value=["alpha"]):
        with pytest.raises(SystemExit):
            cmd_run(args)
    out = capsys.readouterr().out
    assert "Error reading brief file" in out
    fake.post.assert_not_called()


def test_cmd_run_brief_file_empty_rejected(tmp_path, capsys):
    """An empty brief file is rejected before hitting the daemon."""
    from cli.main import cmd_run

    brief_path = tmp_path / "empty.md"
    brief_path.write_text("   \n\n", encoding="utf-8")
    fake = MagicMock()
    with patch("cli.main.OpcClient.from_env", return_value=fake), \
         patch("cli.main._fetch_available_orgs", return_value=["alpha"]):
        args = MagicMock(org=None, team=None, brief=None, brief_file=str(brief_path))
        with pytest.raises(SystemExit):
            cmd_run(args)
    out = capsys.readouterr().out
    assert "brief is empty" in out
    fake.post.assert_not_called()


def test_cmd_tail_streams_existing_task(capsys):
    from cli.main import cmd_tail

    fake = MagicMock()
    fake.stream.return_value = iter(['{"type": "task_complete"}'])
    with patch("cli.main.OpcClient.from_env", return_value=fake), \
         patch("cli.main._fetch_available_orgs", return_value=["alpha"]):
        args = MagicMock(org=None, task_id="TASK-001")
        cmd_tail(args)
    assert "task_complete" in capsys.readouterr().out


def test_cmd_run_idle_daemon_prints_friendly_message(capsys):
    """409 no_active_runtime from POST should produce the same friendly sentence
    as the read-side commands — no raw JSON, no KeyError on malformed bodies."""
    from cli.main import cmd_run

    fake = MagicMock()
    fake.post.return_value.status_code = 409
    fake.post.return_value.json.return_value = {"detail": {"code": "no_active_runtime"}}
    with patch("cli.main.OpcClient.from_env", return_value=fake), \
         patch("cli.main._fetch_available_orgs", return_value=["alpha"]):
        args = MagicMock(org=None, team=None, brief="x", brief_file=None)
        with pytest.raises(SystemExit):
            cmd_run(args)
    out = capsys.readouterr().out
    assert "No active runtime" in out
    assert "happyranch use" in out


def test_cmd_tail_handles_stream_error(capsys):
    """OpcClient.stream calls raise_for_status — a 404 for an unknown task id
    must surface as a clean message, not an httpx traceback."""
    import httpx

    from cli.main import cmd_tail

    response = MagicMock(status_code=404)
    fake = MagicMock()
    fake.stream.side_effect = httpx.HTTPStatusError(
        "not found", request=MagicMock(), response=response,
    )
    with patch("cli.main.OpcClient.from_env", return_value=fake), \
         patch("cli.main._fetch_available_orgs", return_value=["alpha"]):
        args = MagicMock(org=None, task_id="TASK-X")
        with pytest.raises(SystemExit):
            cmd_tail(args)
    out = capsys.readouterr().out
    assert "TASK-X" in out
    assert "404" in out


def test_cmd_report_completion_posts_with_session_id():
    from cli.main import cmd_report_completion

    fake = MagicMock()
    fake.post.return_value.status_code = 200
    args = MagicMock(
        org="alpha",
        from_file=None,
        task_id="TASK-001", session_id="sess-1", agent="dev_agent",
        status="completed", confidence=90, summary="ok",
        risks=[], dependencies=[], reviewer_focus=[],
    )
    with patch("cli.main.OpcClient.from_env", return_value=fake):
        cmd_report_completion(args)
    args_pos, kwargs = fake.post.call_args
    assert args_pos[0] == "/api/v1/orgs/alpha/tasks/TASK-001/completion"
    assert kwargs["json"]["session_id"] == "sess-1"


def test_cmd_report_completion_from_file_posts_loaded_body(tmp_path):
    """--from-file lets agents submit a completion as a single-line command.
    Multi-line bash (backslash continuations) breaks Claude Code's
    Bash(happyranch:*) allow rule because newlines separate subcommands."""
    import json
    from cli.main import cmd_report_completion

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
        org="alpha",
        from_file=str(completion_file),
        task_id=None, session_id=None, agent=None,
        status=None, confidence=80, summary=None,
        risks=[], dependencies=[], reviewer_focus=[],
    )
    with patch("cli.main.OpcClient.from_env", return_value=fake):
        cmd_report_completion(args)

    args_pos, kwargs = fake.post.call_args
    assert args_pos[0] == "/api/v1/orgs/alpha/tasks/TASK-042/completion"
    body = kwargs["json"]
    assert body["session_id"] == "sess-x"
    assert body["agent"] == "dev_agent"
    assert body["status"] == "completed"
    assert body["confidence"] == 85
    assert body["output_summary"] == "Wired Alipay happy path"
    assert body["risks_flagged"] == ["refund flow untested"]
    assert body["dependencies"] == ["ALIPAY_APP_ID env var"]
    assert body["suggested_reviewer_focus"] == ["signature canonicalization"]


def test_completion_payload_from_file_accepts_output_dir(tmp_path):
    import json as _json
    from cli.main import _completion_payload_from_file

    path = tmp_path / "c.json"
    path.write_text(_json.dumps({
        "task_id": "TASK-001",
        "session_id": "s",
        "agent": "dev_agent",
        "status": "completed",
        "summary": "done",
        "output_dir": "output/TASK-001",
    }))
    task_id, body = _completion_payload_from_file(str(path))
    assert task_id == "TASK-001"
    assert body["output_dir"] == "output/TASK-001"


def test_completion_payload_from_file_output_dir_optional(tmp_path):
    import json as _json
    from cli.main import _completion_payload_from_file

    path = tmp_path / "c.json"
    path.write_text(_json.dumps({
        "task_id": "T", "session_id": "s", "agent": "a",
        "status": "completed", "summary": "done",
    }))
    _, body = _completion_payload_from_file(str(path))
    assert body.get("output_dir") is None


def test_completion_payload_from_file_passes_decision_through(tmp_path):
    """EH's completion JSON carries an optional `decision` field that the
    orchestrator acts on (delegate/done/escalate). The CLI must pass it
    through to the daemon body verbatim."""
    import json as _json
    from cli.main import _completion_payload_from_file

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
    from cli.main import _completion_payload_from_file

    path = tmp_path / "w.json"
    path.write_text(_json.dumps({
        "task_id": "T", "session_id": "s", "agent": "dev_agent",
        "status": "completed", "summary": "done",
    }))
    _, body = _completion_payload_from_file(str(path))
    assert "decision" not in body


def test_completion_payload_from_file_passes_waiting_on_job_ids_through(tmp_path):
    """Agents self-blocking on jobs include `waiting_on_job_ids` in the JSON
    payload. The CLI must forward the list verbatim to the daemon body — without
    it, the daemon-side block-on-jobs branch never sees the list and the task
    falls through to the legacy self-escalate path."""
    import json as _json
    from cli.main import _completion_payload_from_file

    path = tmp_path / "blocked.json"
    path.write_text(_json.dumps({
        "task_id": "TASK-001",
        "session_id": "sess-1",
        "agent": "dev_agent",
        "status": "blocked",
        "confidence": 0,
        "summary": "Waiting on JOB-12 and JOB-13",
        "waiting_on_job_ids": ["JOB-12", "JOB-13"],
    }))
    _, body = _completion_payload_from_file(str(path))
    assert body["waiting_on_job_ids"] == ["JOB-12", "JOB-13"]


def test_completion_payload_from_file_omits_waiting_on_job_ids_when_absent(tmp_path):
    """When the agent doesn't include `waiting_on_job_ids`, the CLI must NOT
    inject an empty list — absence vs. empty list is a meaningful distinction
    on the daemon side (absent = legacy escalate path; explicit [] = 400
    empty_waiting_on_job_ids)."""
    import json as _json
    from cli.main import _completion_payload_from_file

    path = tmp_path / "w.json"
    path.write_text(_json.dumps({
        "task_id": "T", "session_id": "s", "agent": "dev_agent",
        "status": "completed", "summary": "done",
    }))
    _, body = _completion_payload_from_file(str(path))
    assert "waiting_on_job_ids" not in body


def test_completion_payload_from_file_forwards_explicit_empty_waiting_on_job_ids(tmp_path):
    """When the agent EXPLICITLY sends `waiting_on_job_ids: []`, the CLI must
    forward the empty list to the daemon (membership check, not truthiness) so
    the daemon can return 400 empty_waiting_on_job_ids. Silently dropping it
    would mask a malformed agent payload and route it to the legacy escalate
    path instead — the exact silent-fallback bug Codex flagged."""
    import json as _json
    from cli.main import _completion_payload_from_file

    path = tmp_path / "explicit-empty.json"
    path.write_text(_json.dumps({
        "task_id": "TASK-001",
        "session_id": "s",
        "agent": "dev_agent",
        "status": "blocked",
        "confidence": 0,
        "summary": "agent thought it had jobs to wait on but populated nothing",
        "waiting_on_job_ids": [],
    }))
    _, body = _completion_payload_from_file(str(path))
    assert "waiting_on_job_ids" in body
    assert body["waiting_on_job_ids"] == []


def test_report_completion_parser_accepts_from_file_alone():
    """With --from-file, none of --task-id/--session-id/... are required.
    --org IS required for agent callbacks (see test_report_completion_parser_requires_org)."""
    parser = build_parser()
    args = parser.parse_args([
        "report-completion", "--org", "alpha", "--from-file", "/tmp/x.json",
    ])
    assert args.from_file == "/tmp/x.json"
    assert args.task_id is None
    assert args.summary is None
    assert args.org == "alpha"


def test_report_completion_parser_requires_org():
    """The agent callback parser must REQUIRE --org. The slug is baked into
    the agent's skill files literally — a missing --org is a programming
    error, not a user typo, so it must fail at the parser layer."""
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["report-completion", "--from-file", "/tmp/x.json"])


def test_cmd_learning_posts_with_session_id():
    from cli.main import cmd_learning

    fake = MagicMock()
    fake.post.return_value.status_code = 200
    args = MagicMock(
        org="alpha",
        task_id="TASK-001", session_id="sess-1",
        agent="dev_agent", text="x",
    )
    with patch("cli.main.OpcClient.from_env", return_value=fake):
        cmd_learning(args)
    args_pos, kwargs = fake.post.call_args
    assert args_pos[0] == "/api/v1/orgs/alpha/agents/dev_agent/learnings"
    assert kwargs["json"]["session_id"] == "sess-1"


def test_cmd_report_completion_session_mismatch_friendly_message(capsys):
    """409 session_mismatch must print a clean sentence, not raw JSON."""
    from cli.main import cmd_report_completion

    fake = MagicMock()
    fake.post.return_value.status_code = 409
    fake.post.return_value.json.return_value = {
        "detail": {"code": "session_mismatch", "active": "sess-real", "got": "sess-stale"},
    }
    args = MagicMock(
        org="alpha",
        from_file=None,
        task_id="TASK-001", session_id="sess-stale", agent="dev_agent",
        status="completed", confidence=80, summary="x",
        risks=[], dependencies=[], reviewer_focus=[],
    )
    with patch("cli.main.OpcClient.from_env", return_value=fake):
        with pytest.raises(SystemExit):
            cmd_report_completion(args)
    out = capsys.readouterr().out
    assert "Session id mismatch" in out
    assert "sess-real" in out
    assert "sess-stale" in out


def test_cmd_learning_unknown_session_friendly_message(capsys):
    """409 unknown_session must print a clean sentence, not raw JSON."""
    from cli.main import cmd_learning

    fake = MagicMock()
    fake.post.return_value.status_code = 409
    fake.post.return_value.json.return_value = {
        "detail": {"code": "unknown_session", "task_id": "TASK-001", "agent": "dev_agent"},
    }
    args = MagicMock(
        org="alpha",
        task_id="TASK-001", session_id="ghost", agent="dev_agent", text="x",
    )
    with patch("cli.main.OpcClient.from_env", return_value=fake):
        with pytest.raises(SystemExit):
            cmd_learning(args)
    out = capsys.readouterr().out
    assert "Session not recognised" in out
    assert "TASK-001" in out
    assert "dev_agent" in out


def test_cmd_init_agent_surfaces_error_detail(capsys):
    """Daemon-emitted error frames must show the `detail` so users see what broke."""
    from cli.main import cmd_init_agent

    fake = MagicMock()
    fake.stream.return_value = iter([
        '{"agent": "dev_agent", "phase": "starting"}',
        '{"agent": "dev_agent", "phase": "error", "detail": "repo clone failed: fatal"}',
    ])
    with patch("cli.main.OpcClient.from_env", return_value=fake), \
         patch("cli.main._fetch_available_orgs", return_value=["alpha"]):
        args = MagicMock(org=None, agent="dev_agent")
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
    from cli.main import cmd_audit

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
    with patch("cli.main.OpcClient.from_env", return_value=fake), \
         patch("cli.main._fetch_available_orgs", return_value=["alpha"]):
        cmd_audit(MagicMock(
            org=None, task_id="TASK-001", agent=None, action=None,
            since=None, limit=3, json=False,
        ))
    # Verify URL and params were forwarded correctly (only non-None filters).
    args_pos, call_kwargs = fake.get.call_args
    assert args_pos[0] == "/api/v1/orgs/alpha/audit"
    assert call_kwargs["params"] == {"task_id": "TASK-001", "limit": 3}
    out = capsys.readouterr().out
    assert "TASK-001" in out
    assert "session_start" in out
    assert "dev_agent" in out


def test_cmd_audit_empty_result_message(capsys):
    from cli.main import cmd_audit

    fake = MagicMock()
    fake.get.return_value.status_code = 200
    fake.get.return_value.json.return_value = {"entries": []}
    with patch("cli.main.OpcClient.from_env", return_value=fake), \
         patch("cli.main._fetch_available_orgs", return_value=["alpha"]):
        cmd_audit(MagicMock(
            org=None, task_id=None, agent=None, action=None,
            since=None, limit=None, json=False,
        ))
    assert "No audit entries" in capsys.readouterr().out


def test_cmd_audit_json_flag_dumps_raw(capsys):
    import json as _json

    from cli.main import cmd_audit

    fake = MagicMock()
    fake.get.return_value.status_code = 200
    entries = [{"id": 9, "task_id": "T", "agent": "a", "action": "x", "payload": None,
                "timestamp": "2026-01-01T00:00:00+00:00"}]
    fake.get.return_value.json.return_value = {"entries": entries}
    with patch("cli.main.OpcClient.from_env", return_value=fake), \
         patch("cli.main._fetch_available_orgs", return_value=["alpha"]):
        cmd_audit(MagicMock(
            org=None, task_id=None, agent=None, action=None,
            since=None, limit=None, json=True,
        ))
    parsed = _json.loads(capsys.readouterr().out)
    assert parsed == entries


def test_cmd_init_agent_handles_stream_http_error(capsys):
    """OpcClient.stream calls raise_for_status — a 409 from /agents/init must
    surface as a clean message, not an httpx traceback."""
    import httpx

    from cli.main import cmd_init_agent

    response = MagicMock(status_code=409)
    fake = MagicMock()
    fake.stream.side_effect = httpx.HTTPStatusError(
        "conflict", request=MagicMock(), response=response,
    )
    with patch("cli.main.OpcClient.from_env", return_value=fake), \
         patch("cli.main._fetch_available_orgs", return_value=["alpha"]):
        args = MagicMock(org=None, agent=None)
        with pytest.raises(SystemExit):
            cmd_init_agent(args)
    out = capsys.readouterr().out
    assert "init stream failed" in out


def test_manage_repo_parser_add():
    parser = build_parser()
    args = parser.parse_args([
        "manage-repo", "--org", "alpha", "add",
        "--agent", "dev_agent",
        "--repo-name", "docs",
        "--url", "https://github.com/t-benze/docs.git",
    ])
    assert args.command == "manage-repo"
    assert args.action == "add"
    assert args.agent == "dev_agent"
    assert args.repo_name == "docs"
    assert args.url == "https://github.com/t-benze/docs.git"
    assert args.org == "alpha"


def test_manage_repo_parser_remove():
    parser = build_parser()
    args = parser.parse_args([
        "manage-repo", "--org", "alpha", "remove",
        "--agent", "dev_agent",
        "--repo-name", "docs",
    ])
    assert args.action == "remove"
    assert args.url is None


def test_manage_repo_parser_from_file():
    parser = build_parser()
    args = parser.parse_args([
        "manage-repo", "--org", "alpha", "--from-file", "/tmp/repo.json",
    ])
    assert args.from_file == "/tmp/repo.json"


def test_manage_repo_parser_requires_org():
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args([
            "manage-repo", "--from-file", "/tmp/repo.json",
        ])


def test_cmd_manage_repo_posts_to_daemon():
    from cli.main import cmd_manage_repo

    fake = MagicMock()
    fake.post.return_value.status_code = 200
    fake.post.return_value.json.return_value = {"ok": True}
    args = MagicMock(
        org="alpha",
        from_file=None,
        action="add", agent="dev_agent",
        repo_name="docs", url="https://github.com/t-benze/docs.git",
    )
    with patch("cli.main.OpcClient.from_env", return_value=fake):
        cmd_manage_repo(args)
    args_pos, kwargs = fake.post.call_args
    assert args_pos[0] == "/api/v1/orgs/alpha/agents/dev_agent/repos"
    assert kwargs["json"]["action"] == "add"
    assert kwargs["json"]["repo_name"] == "docs"
    assert kwargs["json"]["url"] == "https://github.com/t-benze/docs.git"


def test_cmd_manage_repo_from_file(tmp_path):
    import json

    from cli.main import cmd_manage_repo

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
        org="alpha",
        from_file=str(f),
        action=None, agent=None, repo_name=None, url=None,
    )
    with patch("cli.main.OpcClient.from_env", return_value=fake):
        cmd_manage_repo(args)
    args_pos, kwargs = fake.post.call_args
    assert args_pos[0] == "/api/v1/orgs/alpha/agents/dev_agent/repos"
    assert kwargs["json"]["action"] == "remove"
    assert kwargs["json"]["repo_name"] == "docs"


def test_manage_agent_parser_enroll():
    parser = build_parser()
    args = parser.parse_args([
        "manage-agent", "--org", "alpha", "enroll",
        "--from-file", "/tmp/enroll.json",
    ])
    assert args.command == "manage-agent"
    assert args.action == "enroll"
    assert args.from_file == "/tmp/enroll.json"
    assert args.org == "alpha"


def test_manage_agent_parser_terminate():
    parser = build_parser()
    args = parser.parse_args([
        "manage-agent", "--org", "alpha", "terminate",
        "--name", "content_writer",
        "--task-id", "TASK-001",
        "--session-id", "sess-123",
    ])
    assert args.action == "terminate"
    assert args.name == "content_writer"
    assert args.task_id == "TASK-001"
    assert args.session_id == "sess-123"


def test_manage_agent_parser_requires_org():
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args([
            "manage-agent", "enroll",
            "--from-file", "/tmp/enroll.json",
        ])


def test_cmd_manage_agent_posts_to_daemon():
    import argparse

    from cli.main import cmd_manage_agent

    fake = MagicMock()
    fake.post.return_value.status_code = 200
    fake.post.return_value.json.return_value = {"ok": True, "status": "pending"}
    args = argparse.Namespace(
        org="alpha",
        from_file=None,
        action="enroll", name="content_writer",
        task_id="TASK-001", session_id="sess-123",
        description="Writes guides", system_prompt="You are...",
        repos=None,
    )
    with patch("cli.main.OpcClient.from_env", return_value=fake):
        cmd_manage_agent(args)
    args_pos, kwargs = fake.post.call_args
    assert args_pos[0] == "/api/v1/orgs/alpha/agents/manage"
    assert kwargs["json"]["action"] == "enroll"
    assert kwargs["json"]["name"] == "content_writer"


def test_cmd_manage_agent_from_file(tmp_path):
    import json

    from cli.main import cmd_manage_agent

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
        org="alpha",
        from_file=str(f),
        action=None, name=None, description=None,
        system_prompt=None, repos=None,
    )
    with patch("cli.main.OpcClient.from_env", return_value=fake):
        cmd_manage_agent(args)
    args_pos, kwargs = fake.post.call_args
    assert args_pos[0] == "/api/v1/orgs/alpha/agents/manage"
    assert kwargs["json"]["action"] == "enroll"
    assert kwargs["json"]["name"] == "content_writer"


def test_cmd_manage_agent_from_file_talk_path(tmp_path):
    import json

    from cli.main import cmd_manage_agent

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
        org="alpha",
        from_file=str(f),
        action=None, name=None, description=None,
        system_prompt=None, repos=None,
    )
    with patch("cli.main.OpcClient.from_env", return_value=fake):
        cmd_manage_agent(args)
    _args_pos, kwargs = fake.post.call_args
    assert kwargs["json"]["talk_id"] == "TALK-002"
    assert "task_id" not in kwargs["json"]
    assert "session_id" not in kwargs["json"]


def test_manage_agent_payload_from_file_rejects_mixed_auth(tmp_path):
    import json

    from cli.main import _manage_agent_payload_from_file

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

    from cli.main import _manage_agent_payload_from_file

    f = tmp_path / "noauth.json"
    f.write_text(json.dumps({"action": "enroll", "name": "content_writer"}))
    with pytest.raises(ValueError, match="task_id \\+ session_id"):
        _manage_agent_payload_from_file(str(f))


def test_manage_agent_parser_accepts_talk_id():
    parser = build_parser()
    args = parser.parse_args([
        "manage-agent", "--org", "alpha", "enroll",
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
    from cli.main import cmd_enrollments

    fake = MagicMock()
    fake.get.return_value.status_code = 200
    fake.get.return_value.json.return_value = {
        "enrollments": [
            {"name": "content_writer", "description": "Writes", "status": "pending",
             "created_at": "2026-04-17T00:00:00"},
        ],
    }
    with patch("cli.main.OpcClient.from_env", return_value=fake), \
         patch("cli.main._fetch_available_orgs", return_value=["alpha"]):
        cmd_enrollments(MagicMock(org=None, status="pending"))
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

    from cli.main import cmd_approve_agent

    fake = MagicMock()
    fake.post.return_value.status_code = 200
    fake.post.return_value.json.return_value = {"ok": True}
    with patch("cli.main.OpcClient.from_env", return_value=fake), \
         patch("cli.main._fetch_available_orgs", return_value=["alpha"]):
        cmd_approve_agent(argparse.Namespace(org=None, name="content_writer"))
    fake.post.assert_called_once_with(
        "/api/v1/orgs/alpha/agents/content_writer/approve", json={},
    )
    assert "approved" in capsys.readouterr().out.lower()


def test_reject_agent_parser():
    parser = build_parser()
    args = parser.parse_args(["reject-agent", "content_writer"])
    assert args.command == "reject-agent"
    assert args.name == "content_writer"


def test_cli_recall_parses_flags():
    parser = build_parser()
    args = parser.parse_args(["recall", "TASK-001", "--tree", "--fetch-output"])
    assert args.command == "recall"
    assert args.task_id == "TASK-001"
    assert args.tree is True
    assert args.fetch_output is True


def test_cli_recall_defaults():
    parser = build_parser()
    args = parser.parse_args(["recall", "TASK-001"])
    assert args.task_id == "TASK-001"
    assert args.tree is False
    assert args.fetch_output is False


def test_cmd_recall_prints_payload(capsys):
    import argparse
    import json as _json
    from cli.main import cmd_recall

    fake = MagicMock()
    fake.get.return_value.status_code = 200
    fake.get.return_value.json.return_value = {"task_id": "TASK-001", "brief": "hi"}
    with patch("cli.main.OpcClient.from_env", return_value=fake), \
         patch("cli.main._fetch_available_orgs", return_value=["alpha"]):
        cmd_recall(argparse.Namespace(
            org=None, task_id="TASK-001", tree=False, fetch_output=False,
        ))
    fake.get.assert_called_once_with(
        "/api/v1/orgs/alpha/tasks/TASK-001/recall", params={},
    )
    out = capsys.readouterr().out
    assert _json.loads(out)["task_id"] == "TASK-001"


def test_cmd_recall_forwards_tree_and_output_params():
    import argparse
    from cli.main import cmd_recall

    fake = MagicMock()
    fake.get.return_value.status_code = 200
    fake.get.return_value.json.return_value = {}
    with patch("cli.main.OpcClient.from_env", return_value=fake), \
         patch("cli.main._fetch_available_orgs", return_value=["alpha"]):
        cmd_recall(argparse.Namespace(
            org=None, task_id="TASK-001", tree=True, fetch_output=True,
        ))
    fake.get.assert_called_once_with(
        "/api/v1/orgs/alpha/tasks/TASK-001/recall",
        params={"tree": "true", "include_output": "true"},
    )


def test_cmd_recall_404_exits(capsys):
    import argparse
    from cli.main import cmd_recall

    fake = MagicMock()
    fake.get.return_value.status_code = 404
    with patch("cli.main.OpcClient.from_env", return_value=fake), \
         patch("cli.main._fetch_available_orgs", return_value=["alpha"]):
        with pytest.raises(SystemExit):
            cmd_recall(argparse.Namespace(
                org=None, task_id="TASK-404", tree=False, fetch_output=False,
            ))
    assert "not found" in capsys.readouterr().out.lower()


def test_cli_has_kb_subcommands():
    from cli.main import build_parser
    parser = build_parser()
    sub = next(a for a in parser._actions if a.__class__.__name__ == "_SubParsersAction")
    assert "kb" in sub.choices
    kb = sub.choices["kb"]
    kb_sub = next(a for a in kb._actions if a.__class__.__name__ == "_SubParsersAction")
    for name in ("list", "get", "search", "add", "update", "delete", "reindex"):
        assert name in kb_sub.choices, f"missing kb subcommand: {name}"
    assert "precedent" not in kb_sub.choices, "kb precedent removed; founder rulings now flow through plain `kb add`"


def test_cli_has_resolve_escalation():
    from cli.main import build_parser
    parser = build_parser()
    sub = next(a for a in parser._actions if a.__class__.__name__ == "_SubParsersAction")
    assert "resolve-escalation" in sub.choices


def test_kb_add_requires_from_file():
    from cli.main import build_parser
    parser = build_parser()
    # parse_args raises SystemExit(2) on missing required args
    import pytest as _pytest
    with _pytest.raises(SystemExit):
        parser.parse_args(["kb", "add", "--agent", "dev_agent"])


def test_kb_delete_parses_confirm_and_as_founder():
    from cli.main import build_parser
    parser = build_parser()
    ns = parser.parse_args([
        "kb", "delete", "--org", "alpha", "alipay-refund",
        "--agent", "engineering_head",
        "--confirm", "--as-founder",
    ])
    assert ns.confirm is True
    assert ns.as_founder is True
    assert ns.org == "alpha"


def test_kb_write_parsers_require_org():
    """KB write commands (add/update/delete) must require --org —
    they're agent callbacks from skill files where the slug is baked in
    literally."""
    from cli.main import build_parser
    parser = build_parser()
    for argv in (
        ["kb", "add", "--agent", "dev_agent", "--from-file", "/tmp/e.md"],
        ["kb", "update", "alipay", "--agent", "dev_agent", "--from-file", "/tmp/e.md"],
        ["kb", "delete", "alipay", "--agent", "engineering_head", "--confirm"],
    ):
        with pytest.raises(SystemExit):
            parser.parse_args(argv)


def test_cmd_tasks_shows_block_kind_when_present(capsys):
    """A blocked task should show its block_kind alongside status."""
    from cli.main import cmd_tasks
    from argparse import Namespace
    from unittest.mock import MagicMock, patch

    client = MagicMock()
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {"tasks": [
        {"task_id": "T-1", "team": "engineering", "status": "blocked",
         "assigned_agent": "engineering_head", "brief": "waiting",
         "block_kind": "delegated"},
        {"task_id": "T-2", "team": "engineering", "status": "completed",
         "assigned_agent": "engineering_head", "brief": "done",
         "block_kind": None},
    ]}
    client.get.return_value = response
    with patch("cli.main.OpcClient.from_env", return_value=client), \
         patch("cli.main._fetch_available_orgs", return_value=["alpha"]):
        cmd_tasks(Namespace(org=None, limit=10))
    out = capsys.readouterr().out
    assert "blocked(delegated)" in out or "blocked (delegated)" in out
    assert "completed" in out


def test_cmd_tasks_renders_team_column(capsys):
    """Regression: the task-list table must read the `team` column, not the
    retired `type` column. Rendering a payload that matches the real API
    response (no `type` key) used to raise KeyError and crash `happyranch tasks`.
    """
    from cli.main import cmd_tasks
    from argparse import Namespace

    fake = MagicMock()
    fake.get.return_value.status_code = 200
    fake.get.return_value.json.return_value = {"tasks": [
        {"task_id": "TASK-030", "team": "content", "status": "in_progress",
         "assigned_agent": "content_manager", "brief": "Draft Macau visa guide"},
        {"task_id": "TASK-031", "team": "engineering", "status": "completed",
         "assigned_agent": "engineering_head", "brief": "Add Alipay"},
    ]}
    with patch("cli.main.OpcClient.from_env", return_value=fake), \
         patch("cli.main._fetch_available_orgs", return_value=["alpha"]):
        cmd_tasks(Namespace(org=None, limit=20))
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
    from cli.main import cmd_talk_start

    fake = MagicMock()
    fake.post.return_value.status_code = 200
    fake.post.return_value.json.return_value = {
        "talk_id": "TALK-007",
        "started_at": "2026-04-21T10:00:00+00:00",
    }
    with patch("cli.main.OpcClient.from_env", return_value=fake), \
         patch("cli.main._fetch_available_orgs", return_value=["alpha"]):
        args = MagicMock(org=None, agent="dev_agent")
        cmd_talk_start(args)
    fake.post.assert_called_once_with(
        "/api/v1/orgs/alpha/talks", json={"agent_name": "dev_agent"},
    )
    out = capsys.readouterr().out
    assert "TALK-007" in out


def test_cmd_talk_start_conflict_exits_with_message(capsys):
    from cli.main import cmd_talk_start

    fake = MagicMock()
    fake.post.return_value.status_code = 409
    fake.post.return_value.json.return_value = {
        "detail": {
            "code": "talk_already_open",
            "prior_open_talk_id": "TALK-003",
            "prior_started_at": "2026-04-20T09:00:00+00:00",
        },
    }
    with patch("cli.main.OpcClient.from_env", return_value=fake), \
         patch("cli.main._fetch_available_orgs", return_value=["alpha"]):
        args = MagicMock(org=None, agent="dev_agent")
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

    from cli.main import cmd_talk_end

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
    with patch("cli.main.OpcClient.from_env", return_value=fake), \
         patch("cli.main._fetch_available_orgs", return_value=["alpha"]):
        args = Namespace(org=None, talk_id="TALK-007", from_file=str(payload_path))
        cmd_talk_end(args)
    fake.post.assert_called_once_with(
        "/api/v1/orgs/alpha/talks/TALK-007/end", json=payload
    )
    out = capsys.readouterr().out
    assert "TALK-007" in out
    assert "closed" in out.lower() or "ok" in out.lower()


def test_cmd_talk_end_missing_file(tmp_path, capsys):
    from argparse import Namespace

    from cli.main import cmd_talk_end

    missing = tmp_path / "does-not-exist.json"
    fake = MagicMock()
    with patch("cli.main.OpcClient.from_env", return_value=fake), \
         patch("cli.main._fetch_available_orgs", return_value=["alpha"]):
        args = Namespace(org=None, talk_id="TALK-007", from_file=str(missing))
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

    from cli.main import cmd_talk_status

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
    with patch("cli.main.OpcClient.from_env", return_value=fake), \
         patch("cli.main._fetch_available_orgs", return_value=["alpha"]):
        cmd_talk_status(Namespace(org=None, agent="dev_agent"))
    fake.get.assert_called_once_with(
        "/api/v1/orgs/alpha/talks", params={"status": "open", "agent": "dev_agent"}
    )
    out = capsys.readouterr().out
    assert "TALK-001" in out
    assert "dev_agent" in out


def test_cmd_talk_status_empty(capsys):
    from argparse import Namespace

    from cli.main import cmd_talk_status

    fake = MagicMock()
    fake.get.return_value.status_code = 200
    fake.get.return_value.json.return_value = {"talks": []}
    with patch("cli.main.OpcClient.from_env", return_value=fake), \
         patch("cli.main._fetch_available_orgs", return_value=["alpha"]):
        cmd_talk_status(Namespace(org=None, agent=None))
    out = capsys.readouterr().out
    assert "no open talks" in out


def test_cmd_talk_list_uses_limit(capsys):
    from argparse import Namespace

    from cli.main import cmd_talk_list

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
    with patch("cli.main.OpcClient.from_env", return_value=fake), \
         patch("cli.main._fetch_available_orgs", return_value=["alpha"]):
        cmd_talk_list(Namespace(org=None, agent="dev_agent", limit=5))
    fake.get.assert_called_once_with(
        "/api/v1/orgs/alpha/talks", params={"limit": 5, "agent": "dev_agent"}
    )
    out = capsys.readouterr().out
    assert "TALK-042" in out


def test_cmd_talk_show_human(capsys):
    from argparse import Namespace

    from cli.main import cmd_talk_show

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
    with patch("cli.main.OpcClient.from_env", return_value=fake), \
         patch("cli.main._fetch_available_orgs", return_value=["alpha"]):
        cmd_talk_show(Namespace(org=None, talk_id="TALK-007", json=False))
    out = capsys.readouterr().out
    assert "TALK-007" in out
    assert "## Summary" in out
    assert "## Transcript" in out
    assert "testing" in out


def test_cmd_talk_show_json_mode(capsys):
    import json as _json
    from argparse import Namespace

    from cli.main import cmd_talk_show

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
    with patch("cli.main.OpcClient.from_env", return_value=fake), \
         patch("cli.main._fetch_available_orgs", return_value=["alpha"]):
        cmd_talk_show(Namespace(org=None, talk_id="TALK-007", json=True))
    out = capsys.readouterr().out
    data = _json.loads(out)
    assert data["talk_id"] == "TALK-007"
    assert data["agent_name"] == "dev_agent"


def test_cmd_details_shows_note(capsys):
    from cli.main import cmd_details
    from argparse import Namespace
    from unittest.mock import MagicMock, patch

    client = MagicMock()
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {
        "task": {
            "task_id": "T-1", "type": "general", "status": "completed",
            "assigned_agent": "engineering_head", "brief": "b",
            "created_at": "2026-04-19T00:00:00", "updated_at": "2026-04-19T00:00:00",
            "note": "Feature landed",
        },
        "results": [],
        "audit_log": [],
    }
    client.get.return_value = response
    with patch("cli.main.OpcClient.from_env", return_value=client), \
         patch("cli.main._fetch_available_orgs", return_value=["alpha"]):
        cmd_details(Namespace(org=None, task_id="T-1"))
    out = capsys.readouterr().out
    assert "Feature landed" in out


def test_cmd_details_full_flag_shows_untruncated_summary(capsys):
    """--full prints the full output_summary; default truncates to 80 chars."""
    from cli.main import cmd_details
    from argparse import Namespace
    from unittest.mock import MagicMock, patch

    long_summary = "S" * 200  # well past the 80-char default cap
    client = MagicMock()
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {
        "task": {
            "task_id": "T-1", "type": "general", "status": "completed",
            "assigned_agent": "engineering_head", "brief": "b",
            "created_at": "2026-04-19T00:00:00", "updated_at": "2026-04-19T00:00:00",
        },
        "results": [
            {"agent": "dev_agent", "confidence_score": 0.9, "output_summary": long_summary}
        ],
        "audit_log": [],
    }
    client.get.return_value = response

    # Default: truncated.
    with patch("cli.main.OpcClient.from_env", return_value=client), \
         patch("cli.main._fetch_available_orgs", return_value=["alpha"]):
        cmd_details(Namespace(org=None, task_id="T-1", full=False))
    out_default = capsys.readouterr().out
    assert long_summary not in out_default
    assert ("S" * 80) in out_default

    # --full: full text present.
    with patch("cli.main.OpcClient.from_env", return_value=client), \
         patch("cli.main._fetch_available_orgs", return_value=["alpha"]):
        cmd_details(Namespace(org=None, task_id="T-1", full=True))
    out_full = capsys.readouterr().out
    assert long_summary in out_full


def test_cmd_revisit_rejects_non_tty(capsys, monkeypatch):
    """No TTY => abort before any HTTP call."""
    from cli.main import cmd_revisit

    fake = MagicMock()
    monkeypatch.setattr("cli.main.sys.stdin.isatty", lambda: False)
    monkeypatch.setattr("cli.main.sys.stdout.isatty", lambda: True)
    with patch("cli.main.OpcClient.from_env", return_value=fake):
        args = MagicMock(task_id="TASK-052", note=None)
        with pytest.raises(SystemExit):
            cmd_revisit(args)
    # Never touched the client.
    fake.post.assert_not_called()
    assert "interactive terminal" in capsys.readouterr().out


def test_cmd_revisit_aborts_on_negative_confirmation(capsys, monkeypatch):
    """TTY present but founder types 'n' => no POST."""
    from cli.main import cmd_revisit

    fake = MagicMock()
    monkeypatch.setattr("cli.main.sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("cli.main.sys.stdout.isatty", lambda: True)
    with patch("cli.main.OpcClient.from_env", return_value=fake), \
         patch("builtins.input", return_value="n"):
        args = MagicMock(task_id="TASK-052", note=None, note_file=None)
        with pytest.raises(SystemExit):
            cmd_revisit(args)
    fake.post.assert_not_called()


def test_cmd_revisit_submits_without_streaming_on_yes(capsys, monkeypatch):
    """'y' confirmation => POST, then return; no streaming. The tail hint must
    point at the new root id, not the predecessor."""
    from cli.main import cmd_revisit

    fake = MagicMock()
    fake.post.return_value.status_code = 200
    fake.post.return_value.json.return_value = {
        "new_root_task_id": "TASK-072",
        "predecessor_root_task_id": "TASK-052",
        "flagged_task_id": "TASK-052",
        "cascade": ["TASK-052"],
        "predecessor_status": "failed",
    }

    monkeypatch.setattr("cli.main.sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("cli.main.sys.stdout.isatty", lambda: True)
    with patch("cli.main.OpcClient.from_env", return_value=fake), \
         patch("cli.main._fetch_available_orgs", return_value=["alpha"]), \
         patch("builtins.input", return_value="y"):
        args = MagicMock(
            org=None,
            task_id="TASK-052",
            note="PR merged",
            note_file=None,
            session_timeout_seconds=None,
        )
        cmd_revisit(args)

    fake.post.assert_called_once_with(
        "/api/v1/orgs/alpha/tasks/TASK-052/revisit",
        json={"founder_note": "PR merged"},
    )
    fake.stream.assert_not_called()
    out = capsys.readouterr().out
    assert "TASK-072" in out
    assert "happyranch tail TASK-072" in out


def test_cmd_revisit_reads_note_from_file(tmp_path, capsys, monkeypatch):
    """--note-file reads the founder note from disk and forwards it as founder_note."""
    from cli.main import cmd_revisit

    note_path = tmp_path / "note.md"
    note_path.write_text("multi-line\nfounder hint\n", encoding="utf-8")

    fake = MagicMock()
    fake.post.return_value.status_code = 200
    fake.post.return_value.json.return_value = {
        "new_root_task_id": "TASK-072",
        "predecessor_root_task_id": "TASK-052",
        "flagged_task_id": "TASK-052",
        "cascade": ["TASK-052"],
        "predecessor_status": "failed",
    }
    monkeypatch.setattr("cli.main.sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("cli.main.sys.stdout.isatty", lambda: True)
    with patch("cli.main.OpcClient.from_env", return_value=fake), \
         patch("cli.main._fetch_available_orgs", return_value=["alpha"]), \
         patch("builtins.input", return_value="y"):
        args = MagicMock(
            org=None,
            task_id="TASK-052",
            note=None,
            note_file=str(note_path),
            session_timeout_seconds=None,
        )
        cmd_revisit(args)

    fake.post.assert_called_once_with(
        "/api/v1/orgs/alpha/tasks/TASK-052/revisit",
        json={"founder_note": "multi-line\nfounder hint\n"},
    )


def test_cmd_revisit_note_file_missing(tmp_path, capsys, monkeypatch):
    """--note-file with a nonexistent path exits with a friendly error and never POSTs."""
    from cli.main import cmd_revisit

    fake = MagicMock()
    monkeypatch.setattr("cli.main.sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("cli.main.sys.stdout.isatty", lambda: True)
    with patch("cli.main.OpcClient.from_env", return_value=fake):
        args = MagicMock(
            task_id="TASK-052", note=None, note_file=str(tmp_path / "nope.md")
        )
        with pytest.raises(SystemExit):
            cmd_revisit(args)
    out = capsys.readouterr().out
    assert "Error reading note file" in out
    fake.post.assert_not_called()


def test_cmd_revisit_note_file_empty_rejected(tmp_path, capsys, monkeypatch):
    """An empty --note-file is rejected before the confirm prompt — likely a bug."""
    from cli.main import cmd_revisit

    note_path = tmp_path / "empty.md"
    note_path.write_text("   \n\n", encoding="utf-8")
    fake = MagicMock()
    monkeypatch.setattr("cli.main.sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("cli.main.sys.stdout.isatty", lambda: True)
    with patch("cli.main.OpcClient.from_env", return_value=fake):
        args = MagicMock(task_id="TASK-052", note=None, note_file=str(note_path))
        with pytest.raises(SystemExit):
            cmd_revisit(args)
    out = capsys.readouterr().out
    assert "note is empty" in out
    fake.post.assert_not_called()


def test_cmd_details_shows_revisit_header_chain_and_footer(capsys):
    """When the task is a revisit AND has later revisits, details must show:
    - a `Revisit of:` header line with the predecessor id and prior_status
    - a `Chain:` line with the full chain, oldest leftmost, (this) marker
    - a `Revisited as:` footer line listing direct revisits
    """
    from cli.main import cmd_details
    fake = MagicMock()
    fake.get.return_value.status_code = 200
    fake.get.return_value.json.return_value = {
        "task": {
            "task_id": "TASK-072",
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
    with patch("cli.main.OpcClient.from_env", return_value=fake), \
         patch("cli.main._fetch_available_orgs", return_value=["alpha"]):
        args = MagicMock(org=None, task_id="TASK-072")
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
    from cli.main import cmd_details
    fake = MagicMock()
    fake.get.return_value.status_code = 200
    fake.get.return_value.json.return_value = {
        "task": {
            "task_id": "TASK-001",
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
    with patch("cli.main.OpcClient.from_env", return_value=fake), \
         patch("cli.main._fetch_available_orgs", return_value=["alpha"]):
        args = MagicMock(org=None, task_id="TASK-001")
        cmd_details(args)
    out = capsys.readouterr().out
    assert "Revisit of:" not in out
    assert "Chain:" not in out
    assert "Revisited as:" not in out


def test_cmd_details_shows_footer_only_when_predecessor_has_revisits(capsys):
    """Predecessor-side view: task is NOT a revisit (no header/chain) but
    HAS been revisited (footer present)."""
    from cli.main import cmd_details
    fake = MagicMock()
    fake.get.return_value.status_code = 200
    fake.get.return_value.json.return_value = {
        "task": {
            "task_id": "TASK-052",
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
    with patch("cli.main.OpcClient.from_env", return_value=fake), \
         patch("cli.main._fetch_available_orgs", return_value=["alpha"]):
        args = MagicMock(org=None, task_id="TASK-052")
        cmd_details(args)
    out = capsys.readouterr().out
    assert "Revisit of:" not in out
    assert "Chain:" not in out
    assert "Revisited as: TASK-072" in out


def test_cmd_details_renders_dispatched_from(capsys):
    """When a task was dispatched from a talk, `happyranch details` must show:
    - a `Dispatched from:` header line with the source talk id
    - the dispatcher agent + role pulled from the task_dispatched audit row
    The line appears after the (optional) revisit header and before the
    main task summary block.
    """
    from argparse import Namespace

    from cli.main import cmd_details

    fake = MagicMock()
    fake.get.return_value.status_code = 200
    fake.get.return_value.json.return_value = {
        "task": {
            "task_id": "TASK-042",
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
    with patch("cli.main.OpcClient.from_env", return_value=fake), \
         patch("cli.main._fetch_available_orgs", return_value=["alpha"]):
        cmd_details(Namespace(org=None, task_id="TASK-042"))
    out = capsys.readouterr().out
    assert "Dispatched from: TALK-007" in out
    assert "dev_agent / worker" in out


def test_cmd_details_renders_workflow_chain(capsys):
    """When body['active_chain'] is set, the chain block is rendered showing
    step N of M, leg markers (▶ in-flight, ✓ completed, ⋯ pending), and
    expect_verdict notes."""
    from argparse import Namespace

    from cli.main import cmd_details

    fake = MagicMock()
    fake.get.return_value.status_code = 200
    fake.get.return_value.json.return_value = {
        "task": {
            "task_id": "TASK-050",
            "type": "general",
            "status": "in_progress",
            "assigned_agent": "dev_agent",
            "brief": "Delegate multi-leg task",
            "created_at": "2026-04-26T10:00:00+00:00",
            "updated_at": "2026-04-26T10:00:00+00:00",
        },
        "active_chain": {
            "step_index": 1,
            "first_leg_expect_verdict": "approved",
            "legs": [
                {
                    "agent": "pm",
                    "prompt": "Review the design",
                    "expect_verdict": "approved",
                },
                {
                    "agent": "eng_lead",
                    "prompt": "Implement feature X",
                    "expect_verdict": "completed",
                },
            ],
        },
        "results": [],
        "audit_log": [],
    }
    with patch("cli.main.OpcClient.from_env", return_value=fake), \
         patch("cli.main._fetch_available_orgs", return_value=["alpha"]):
        cmd_details(Namespace(org=None, task_id="TASK-050"))
    out = capsys.readouterr().out
    assert "Current workflow chain" in out
    assert "step 2 of 3" in out
    assert "▶ Leg 2" in out
    assert "✓ Leg 1" in out
    assert "pm" in out
    assert "Review the design" in out
    assert "expecting: approved" in out


def test_cmd_dispatch_happy_path(tmp_path):
    """`happyranch dispatch --from-file ...` POSTs to /talks/{talk_id}/dispatch with
    body shaped {brief, target_agent?, team?} — talk_id stays in the URL path
    and is NOT echoed in the request body."""
    import json
    from argparse import Namespace

    from cli.main import cmd_dispatch

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
    with patch("cli.main.OpcClient.from_env", return_value=fake):
        cmd_dispatch(Namespace(org="alpha", from_file=str(payload_path)))

    args_pos, kwargs = fake.post.call_args
    assert args_pos[0] == "/api/v1/orgs/alpha/talks/TALK-001/dispatch"
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

    from cli.main import cmd_dispatch

    payload = {"brief": "Do the thing"}  # no talk_id
    payload_path = tmp_path / "bad.json"
    payload_path.write_text(json.dumps(payload))

    fake = MagicMock()
    with patch("cli.main.OpcClient.from_env", return_value=fake):
        with pytest.raises(SystemExit):
            cmd_dispatch(Namespace(org="alpha", from_file=str(payload_path)))
    fake.post.assert_not_called()


def test_cmd_dispatch_whitespace_talk_id_raises(tmp_path, capsys):
    """A from-file payload with a whitespace-only `talk_id` should fail before
    the HTTP call — symmetric with the `brief` strip-validation."""
    import json
    from argparse import Namespace

    from cli.main import cmd_dispatch

    payload = {"talk_id": "   ", "brief": "x"}  # whitespace-only talk_id
    payload_path = tmp_path / "bad.json"
    payload_path.write_text(json.dumps(payload))

    fake = MagicMock()
    with patch("cli.main.OpcClient.from_env", return_value=fake):
        with pytest.raises(SystemExit):
            cmd_dispatch(Namespace(org="alpha", from_file=str(payload_path)))
    fake.post.assert_not_called()


def test_cmd_dispatch_parser_requires_org(tmp_path):
    from cli.main import build_parser
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["dispatch", "--from-file", "/tmp/d.json"])


def test_cmd_learning_parser_requires_org():
    from cli.main import build_parser
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args([
            "learning",
            "--task-id", "TASK-1", "--session-id", "s",
            "--agent", "a", "--text", "x",
        ])


def test_cmd_tasks_suffixes_revisit_rows(capsys):
    """Tasks that have a predecessor root show `↩ TASK-XXX` as a trailing
    marker; plain tasks render unchanged."""
    from cli.main import cmd_tasks
    fake = MagicMock()
    fake.get.return_value.status_code = 200
    fake.get.return_value.json.return_value = {"tasks": [
        {
            "task_id": "TASK-072", "team": "engineering", "status": "pending",
            "brief": "Add Alipay support",
            "assigned_agent": None,
            "revisit_of_task_id": "TASK-052",
        },
        {
            "task_id": "TASK-001", "team": "engineering", "status": "completed",
            "brief": "plain task",
            "assigned_agent": "dev_agent",
            "revisit_of_task_id": None,
        },
    ]}
    with patch("cli.main.OpcClient.from_env", return_value=fake), \
         patch("cli.main._fetch_available_orgs", return_value=["alpha"]):
        args = MagicMock(org=None, limit=20)
        cmd_tasks(args)
    out = capsys.readouterr().out
    lines = out.splitlines()
    revisit_line = next(line for line in lines if "TASK-072" in line)
    plain_line = next(line for line in lines if "TASK-001" in line)
    assert "↩ TASK-052" in revisit_line
    assert "↩" not in plain_line


# ── resolve_org_slug ──────────────────────────────────────────


def test_resolve_org_explicit_flag_wins(monkeypatch) -> None:
    monkeypatch.setenv("HAPPYRANCH_ORG_SLUG", "from-env")
    available = ["alpha", "beta"]
    slug = resolve_org_slug(args_org="from-flag", available=available)
    assert slug == "from-flag"


def test_resolve_org_env_var(monkeypatch) -> None:
    monkeypatch.setenv("HAPPYRANCH_ORG_SLUG", "from-env")
    slug = resolve_org_slug(args_org=None, available=["alpha", "from-env"])
    assert slug == "from-env"


def test_resolve_org_auto_infer_single(monkeypatch) -> None:
    monkeypatch.delenv("HAPPYRANCH_ORG_SLUG", raising=False)
    slug = resolve_org_slug(args_org=None, available=["alpha"])
    assert slug == "alpha"


def test_resolve_org_zero_orgs_errors(monkeypatch, capsys) -> None:
    monkeypatch.delenv("HAPPYRANCH_ORG_SLUG", raising=False)
    with pytest.raises(SystemExit):
        resolve_org_slug(args_org=None, available=[])
    err = capsys.readouterr().err
    assert "no orgs registered" in err


def test_resolve_org_multi_errors(monkeypatch, capsys) -> None:
    monkeypatch.delenv("HAPPYRANCH_ORG_SLUG", raising=False)
    with pytest.raises(SystemExit):
        resolve_org_slug(args_org=None, available=["alpha", "beta"])
    err = capsys.readouterr().err
    assert "alpha" in err
    assert "beta" in err


# ── happyranch orgs family (Task 20) ────────────────────────────────


def test_orgs_list_subcommand():
    from cli.main import cmd_orgs

    parser = build_parser()
    args = parser.parse_args(["orgs", "list"])
    assert args.command == "orgs"
    assert args.func is cmd_orgs


def test_orgs_no_subcommand_lists():
    from cli.main import cmd_orgs

    parser = build_parser()
    args = parser.parse_args(["orgs"])
    assert args.command == "orgs"
    assert args.func is cmd_orgs


def test_orgs_init_subcommand():
    from cli.main import cmd_orgs_init

    parser = build_parser()
    args = parser.parse_args(["orgs", "init", "alpha"])
    assert args.command == "orgs"
    assert args.slug == "alpha"
    assert args.from_path is None
    assert args.func is cmd_orgs_init


def test_orgs_init_with_from():
    parser = build_parser()
    args = parser.parse_args(["orgs", "init", "alpha", "--from", "/tmp/example"])
    assert args.slug == "alpha"
    assert args.from_path == "/tmp/example"


def test_orgs_unload_subcommand():
    from cli.main import cmd_orgs_unload

    parser = build_parser()
    args = parser.parse_args(["orgs", "unload", "alpha"])
    assert args.command == "orgs"
    assert args.slug == "alpha"
    assert args.func is cmd_orgs_unload


def test_cmd_orgs_lists(capsys):
    from cli.main import cmd_orgs

    fake = MagicMock()
    fake.get.return_value.status_code = 200
    fake.get.return_value.json.return_value = {
        "orgs": [
            {"slug": "alpha", "root": "/tmp/rt/orgs/alpha"},
            {"slug": "beta", "root": "/tmp/rt/orgs/beta"},
        ],
    }
    with patch("cli.main.OpcClient.from_env", return_value=fake):
        cmd_orgs(MagicMock())

    fake.get.assert_called_once_with("/api/v1/orgs")
    out = capsys.readouterr().out
    assert "alpha" in out
    assert "beta" in out
    assert "/tmp/rt/orgs/alpha" in out


def test_cmd_orgs_init_basic(capsys):
    from cli.main import cmd_orgs_init

    fake = MagicMock()
    fake.post.return_value.status_code = 200
    fake.post.return_value.json.return_value = {
        "slug": "alpha", "root": "/tmp/rt/orgs/alpha",
    }
    with patch("cli.main.OpcClient.from_env", return_value=fake):
        args = MagicMock(slug="alpha", from_path=None)
        cmd_orgs_init(args)

    fake.post.assert_called_once_with("/api/v1/orgs", json={"slug": "alpha"})
    assert "created: alpha" in capsys.readouterr().out


def test_cmd_orgs_init_with_from(capsys):
    from cli.main import cmd_orgs_init

    fake = MagicMock()
    fake.post.return_value.status_code = 200
    fake.post.return_value.json.return_value = {
        "slug": "alpha", "root": "/tmp/rt/orgs/alpha",
    }
    with patch("cli.main.OpcClient.from_env", return_value=fake):
        args = MagicMock(slug="alpha", from_path="/tmp/ex")
        cmd_orgs_init(args)

    fake.post.assert_called_once_with(
        "/api/v1/orgs",
        json={"slug": "alpha", "from_example": "/tmp/ex"},
    )


def test_cmd_orgs_unload_basic(capsys):
    from cli.main import cmd_orgs_unload

    fake = MagicMock()
    fake.request.return_value.status_code = 200
    fake.request.return_value.json.return_value = {
        "slug": "alpha", "unloaded": True,
    }
    with patch("cli.main.OpcClient.from_env", return_value=fake):
        args = MagicMock(slug="alpha")
        cmd_orgs_unload(args)

    fake.request.assert_called_once_with("DELETE", "/api/v1/orgs/alpha")
    assert "unloaded: alpha" in capsys.readouterr().out


# ── per-command --org resolution (Task 21) ───────────────────


def test_cmd_run_resolves_org_explicit_flag(monkeypatch):
    """An explicit --org wins over HAPPYRANCH_ORG_SLUG and over the available list."""
    from cli.main import cmd_run

    monkeypatch.setenv("HAPPYRANCH_ORG_SLUG", "from-env")
    fake = MagicMock()
    # /api/v1/orgs reply for _fetch_available_orgs
    fake.get.return_value.status_code = 200
    fake.get.return_value.json.return_value = {
        "orgs": [{"slug": "alpha"}, {"slug": "beta"}],
    }
    fake.post.return_value.status_code = 200
    fake.post.return_value.json.return_value = {"task_id": "TASK-001"}

    with patch("cli.main.OpcClient.from_env", return_value=fake):
        args = MagicMock(org="from-flag", team=None, brief="x", brief_file=None)
        cmd_run(args)

    fake.post.assert_called_once_with(
        "/api/v1/orgs/from-flag/tasks", json={"brief": "x"},
    )


def test_cmd_run_resolves_org_via_env_var(monkeypatch):
    """When --org is unset, HAPPYRANCH_ORG_SLUG is used."""
    from cli.main import cmd_run

    monkeypatch.setenv("HAPPYRANCH_ORG_SLUG", "from-env")
    fake = MagicMock()
    fake.get.return_value.status_code = 200
    fake.get.return_value.json.return_value = {
        "orgs": [{"slug": "from-env"}, {"slug": "other"}],
    }
    fake.post.return_value.status_code = 200
    fake.post.return_value.json.return_value = {"task_id": "TASK-002"}

    with patch("cli.main.OpcClient.from_env", return_value=fake):
        args = MagicMock(org=None, team=None, brief="x", brief_file=None)
        cmd_run(args)

    fake.post.assert_called_once_with(
        "/api/v1/orgs/from-env/tasks", json={"brief": "x"},
    )


def test_cmd_run_resolves_org_auto_infer_single(monkeypatch):
    """No flag, no env, single registered org => auto-infer."""
    from cli.main import cmd_run

    monkeypatch.delenv("HAPPYRANCH_ORG_SLUG", raising=False)
    fake = MagicMock()
    fake.get.return_value.status_code = 200
    fake.get.return_value.json.return_value = {"orgs": [{"slug": "solo"}]}
    fake.post.return_value.status_code = 200
    fake.post.return_value.json.return_value = {"task_id": "TASK-003"}

    with patch("cli.main.OpcClient.from_env", return_value=fake):
        args = MagicMock(org=None, team=None, brief="x", brief_file=None)
        cmd_run(args)

    fake.post.assert_called_once_with(
        "/api/v1/orgs/solo/tasks", json={"brief": "x"},
    )


def test_cmd_run_multi_org_no_flag_no_env_errors(monkeypatch, capsys):
    """Multiple orgs and no flag/env => exit 1 with the available slug list."""
    from cli.main import cmd_run

    monkeypatch.delenv("HAPPYRANCH_ORG_SLUG", raising=False)
    fake = MagicMock()
    fake.get.return_value.status_code = 200
    fake.get.return_value.json.return_value = {
        "orgs": [{"slug": "alpha"}, {"slug": "beta"}],
    }

    with patch("cli.main.OpcClient.from_env", return_value=fake):
        args = MagicMock(org=None, team=None, brief="x")
        with pytest.raises(SystemExit):
            cmd_run(args)

    fake.post.assert_not_called()
    err = capsys.readouterr().err
    assert "--org <slug> is required" in err
    assert "alpha" in err
    assert "beta" in err
# ── progress callback ────────────────────────────────────────


def test_progress_parser_requires_all_args():
    parser = build_parser()
    args = parser.parse_args([
        "progress",
        "--org", "alpha",
        "--task-id", "TASK-001",
        "--session-id", "sess-1",
        "--agent", "dev_agent",
        "--message", "Phase 3 of 6",
    ])
    assert args.org == "alpha"
    assert args.task_id == "TASK-001"
    assert args.session_id == "sess-1"
    assert args.agent == "dev_agent"
    assert args.message == "Phase 3 of 6"


def test_cmd_progress_posts_to_progress_endpoint():
    from cli.main import cmd_progress

    fake = MagicMock()
    fake.post.return_value.status_code = 200
    args = MagicMock(
        org="alpha",
        task_id="TASK-001", session_id="sess-1",
        agent="dev_agent", message="Phase 3 of 6",
    )
    with patch("cli.main.OpcClient.from_env", return_value=fake):
        cmd_progress(args)
    pos, kwargs = fake.post.call_args
    assert pos[0] == "/api/v1/orgs/alpha/tasks/TASK-001/progress"
    assert kwargs["json"] == {
        "session_id": "sess-1",
        "agent": "dev_agent",
        "message": "Phase 3 of 6",
    }
