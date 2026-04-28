"""Tests for the file-based prompt_loader API."""
from __future__ import annotations

from pathlib import Path

import pytest

from src.orchestrator import prompt_loader
from src.orchestrator.agent_def import AgentDef
from src.runtime import RuntimeDir


def _write_agent(runtime: RuntimeDir, name: str, **fm) -> Path:
    """Helper: write a minimal valid agent file."""
    parts = [
        "---",
        f"name: {name}",
        f"team: {fm.get('team', 'engineering')}",
        f"role: {fm.get('role', 'worker')}",
        f"executor: {fm.get('executor', 'claude')}",
    ]
    if "allow_rules" in fm:
        parts.append("allow_rules:")
        for r in fm["allow_rules"]:
            parts.append(f"  - {r!r}")
    else:
        parts.append("allow_rules: []")
    parts.append("repos: {}")
    parts.append("enrolled_by: null")
    parts.append("enrolled_at_task: null")
    parts.append("enrolled_at: null")
    parts.append("---")
    parts.append("")
    parts.append(fm.get("body", f"You are {name}.\n"))
    pending = fm.get("pending", False)
    target_dir = runtime.pending_agents_dir if pending else runtime.agents_dir
    path = target_dir / f"{name}.md"
    path.write_text("\n".join(parts))
    return path


def test_load_agent_returns_agentdef(tmp_path: Path) -> None:
    rt = RuntimeDir.init(tmp_path / "rt", slug="x")
    _write_agent(rt, "dev_agent", role="worker", team="engineering")
    agent = prompt_loader.load_agent(rt, "dev_agent")
    assert agent is not None
    assert agent.name == "dev_agent"
    assert agent.role == "worker"


def test_load_agent_returns_none_when_missing(tmp_path: Path) -> None:
    rt = RuntimeDir.init(tmp_path / "rt", slug="x")
    assert prompt_loader.load_agent(rt, "nope") is None


def test_load_agent_does_not_return_pending(tmp_path: Path) -> None:
    rt = RuntimeDir.init(tmp_path / "rt", slug="x")
    _write_agent(rt, "draft", pending=True)
    assert prompt_loader.load_agent(rt, "draft") is None


def test_list_agents_excludes_pending(tmp_path: Path) -> None:
    rt = RuntimeDir.init(tmp_path / "rt", slug="x")
    _write_agent(rt, "active1")
    _write_agent(rt, "active2")
    _write_agent(rt, "draft", pending=True)
    names = sorted(a.name for a in prompt_loader.list_agents(rt))
    assert names == ["active1", "active2"]


def test_list_pending_only_pending(tmp_path: Path) -> None:
    rt = RuntimeDir.init(tmp_path / "rt", slug="x")
    _write_agent(rt, "active1")
    _write_agent(rt, "draft", pending=True)
    names = sorted(a.name for a in prompt_loader.list_pending(rt))
    assert names == ["draft"]


def test_write_pending_agent_creates_file(tmp_path: Path) -> None:
    rt = RuntimeDir.init(tmp_path / "rt", slug="x")
    agent = AgentDef(
        name="newbie",
        team="engineering",
        role="worker",
        executor="claude",
        allow_rules=(),
        repos={},
        enrolled_by="engineering_head",
        enrolled_at_task="TASK-9",
        enrolled_at=None,
        system_prompt="You are newbie.\n",
    )
    path = prompt_loader.write_pending_agent(rt, agent)
    assert path == rt.pending_agents_dir / "newbie.md"
    assert path.exists()
    reloaded = prompt_loader.list_pending(rt)
    assert len(reloaded) == 1 and reloaded[0].name == "newbie"


def test_write_pending_agent_atomic_overwrite(tmp_path: Path) -> None:
    rt = RuntimeDir.init(tmp_path / "rt", slug="x")
    agent = AgentDef(
        name="newbie", team="engineering", role="worker", executor="claude",
        allow_rules=(), repos={}, enrolled_by=None, enrolled_at_task=None,
        enrolled_at=None, system_prompt="v1\n",
    )
    prompt_loader.write_pending_agent(rt, agent)
    agent2 = AgentDef(**{**agent.__dict__, "system_prompt": "v2\n"})
    prompt_loader.write_pending_agent(rt, agent2)
    out = (rt.pending_agents_dir / "newbie.md").read_text()
    assert "v2" in out and "v1" not in out


def test_approve_agent_moves_pending_to_active(tmp_path: Path) -> None:
    rt = RuntimeDir.init(tmp_path / "rt", slug="x")
    _write_agent(rt, "newbie", pending=True)
    agent = prompt_loader.approve_agent(rt, "newbie")
    assert agent.name == "newbie"
    assert (rt.agents_dir / "newbie.md").exists()
    assert not (rt.pending_agents_dir / "newbie.md").exists()


def test_approve_agent_404_when_no_pending(tmp_path: Path) -> None:
    rt = RuntimeDir.init(tmp_path / "rt", slug="x")
    with pytest.raises(FileNotFoundError):
        prompt_loader.approve_agent(rt, "nope")


def test_approve_agent_409_when_active_already_exists(tmp_path: Path) -> None:
    rt = RuntimeDir.init(tmp_path / "rt", slug="x")
    _write_agent(rt, "dup")  # already active
    _write_agent(rt, "dup", pending=True)  # somehow also pending
    with pytest.raises(FileExistsError):
        prompt_loader.approve_agent(rt, "dup")


def test_reject_agent_unlinks_pending(tmp_path: Path) -> None:
    rt = RuntimeDir.init(tmp_path / "rt", slug="x")
    _write_agent(rt, "drop", pending=True)
    prompt_loader.reject_agent(rt, "drop")
    assert not (rt.pending_agents_dir / "drop.md").exists()


def test_reject_agent_404_when_missing(tmp_path: Path) -> None:
    rt = RuntimeDir.init(tmp_path / "rt", slug="x")
    with pytest.raises(FileNotFoundError):
        prompt_loader.reject_agent(rt, "nope")
