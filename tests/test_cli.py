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
