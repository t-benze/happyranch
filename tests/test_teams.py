from __future__ import annotations

from pathlib import Path

import pytest

from src.orchestrator.teams import TeamManager, TeamsRegistry
from src.runtime import RuntimeDir


def _runtime(tmp_path: Path) -> RuntimeDir:
    return RuntimeDir.init(tmp_path / "rt", slug="test")


def _runtime_with_teams(tmp_path: Path) -> RuntimeDir:
    """Return a runtime pre-seeded with engineering + content teams."""
    rt = _runtime(tmp_path)
    rt.teams_config_path.write_text(
        "teams:\n"
        "  engineering:\n"
        "    manager: engineering_head\n"
        "    workers: [product_manager, dev_agent, payment_agent, qa_engineer]\n"
        "  content:\n"
        "    manager: content_manager\n"
        "    workers: [content_writer, content_qa]\n"
    )
    return rt


def test_load_missing_file_returns_empty_registry(tmp_path: Path) -> None:
    rt = _runtime(tmp_path)
    # Remove the seeded teams.yaml to simulate a runtime with no teams file.
    rt.teams_config_path.unlink()
    reg = TeamsRegistry.load(rt)
    assert reg.teams() == []
    assert reg.team_for_agent("dev_agent") is None


def test_save_then_load_roundtrips(tmp_path: Path) -> None:
    rt = _runtime_with_teams(tmp_path)
    reg = TeamsRegistry.load(rt)
    reg.save(rt)
    reloaded = TeamsRegistry.load(rt)
    assert reloaded.teams() == reg.teams()
    assert reloaded.manager_for_team("content").workers == reg.manager_for_team("content").workers


def test_lookup_helpers(tmp_path: Path) -> None:
    rt = _runtime_with_teams(tmp_path)
    reg = TeamsRegistry.load(rt)
    assert reg.team_for_agent("dev_agent") == "engineering"
    assert reg.team_for_agent("content_writer") == "content"
    assert reg.team_for_agent("unknown_agent") is None
    assert reg.team_for_manager("engineering_head") == "engineering"
    assert reg.team_for_manager("content_manager") == "content"
    assert reg.team_for_manager("dev_agent") is None
    assert reg.is_team_manager("engineering_head")
    assert reg.is_team_manager("content_manager")
    assert not reg.is_team_manager("dev_agent")


def test_add_and_remove_worker_persists(tmp_path: Path) -> None:
    rt = _runtime_with_teams(tmp_path)
    reg = TeamsRegistry.load(rt)
    reg.add_worker("content", "seo_agent")
    reloaded = TeamsRegistry.load(rt)
    assert "seo_agent" in reloaded.manager_for_team("content").workers
    reloaded.remove_worker("content", "seo_agent")
    again = TeamsRegistry.load(rt)
    assert "seo_agent" not in again.manager_for_team("content").workers


def test_add_worker_to_unknown_team_raises(tmp_path: Path) -> None:
    rt = _runtime(tmp_path)
    reg = TeamsRegistry.load(rt)
    with pytest.raises(KeyError):
        reg.add_worker("ops", "partner_liaison")


def test_manager_for_unknown_team_raises(tmp_path: Path) -> None:
    rt = _runtime(tmp_path)
    reg = TeamsRegistry.load(rt)
    with pytest.raises(KeyError):
        reg.manager_for_team("ops")


def test_all_agents_returns_managers_and_workers(tmp_path: Path) -> None:
    rt = _runtime_with_teams(tmp_path)
    reg = TeamsRegistry.load(rt)
    agents = set(reg.all_agents())
    assert {"engineering_head", "content_manager", "dev_agent", "content_writer", "content_qa"} <= agents


def test_seed_empty_writes_empty_teams_block(tmp_path):
    rt = RuntimeDir.init(tmp_path / "rt", slug="test")
    # init already calls seed_empty; calling again is idempotent.
    TeamsRegistry.seed_empty(rt)
    import yaml
    data = yaml.safe_load(rt.teams_config_path.read_text())
    assert data == {"teams": {}}


def test_load_returns_empty_registry_when_no_teams(tmp_path):
    rt = RuntimeDir.init(tmp_path / "rt", slug="test")
    reg = TeamsRegistry.load(rt)
    assert reg.teams() == []
    assert reg.team_for_agent("anybody") is None


def test_load_reads_runtime_team_file(tmp_path):
    rt = RuntimeDir.init(tmp_path / "rt", slug="test")
    rt.teams_config_path.write_text(
        "teams:\n"
        "  eng:\n"
        "    manager: alice\n"
        "    workers: [bob, carol]\n"
    )
    reg = TeamsRegistry.load(rt)
    assert reg.teams() == ["eng"]
    m = reg.manager_for_team("eng")
    assert m.name == "alice"
    assert m.workers == ("bob", "carol")
