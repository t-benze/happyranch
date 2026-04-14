import sys
from pathlib import Path
from unittest.mock import patch

from src.cli import build_parser
from src.infrastructure.database import Database


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


def test_run_verbose():
    parser = build_parser()
    args = parser.parse_args([
        "run",
        "--task", "bug_fix",
        "--brief", "Fix broken links",
        "--verbose",
    ])
    assert args.command == "run"
    assert args.task == "bug_fix"
    assert args.verbose is True


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


def test_runtime_flag():
    parser = build_parser()
    args = parser.parse_args(["--runtime", "/tmp/rt", "tasks"])
    assert args.runtime == "/tmp/rt"
    assert args.command == "tasks"


def test_no_command_prints_help(capsys):
    parser = build_parser()
    args = parser.parse_args([])
    assert args.command is None


def test_list_tasks_integration(tmp_dir):
    """Test that `opc tasks` works end-to-end with an empty database."""
    db = Database(tmp_dir / "test.db")
    tasks = db.list_tasks()
    assert tasks == []
    db.close()


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


def test_list_tasks_returns_records(tmp_dir):
    """Test list_tasks returns TaskRecords."""
    from src.models import TaskRecord, TaskType

    db = Database(tmp_dir / "test.db")
    db.insert_task(TaskRecord(id="TASK-001", type=TaskType.BUG_FIX, brief="Fix it"))
    db.insert_task(TaskRecord(id="TASK-002", type=TaskType.IMPLEMENT_FEATURE, brief="Build it"))
    tasks = db.list_tasks()
    assert len(tasks) == 2
    assert tasks[0].id == "TASK-002"  # most recent first
    db.close()


from unittest.mock import MagicMock, patch


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
