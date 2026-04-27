"""Sanity: the in-repo example tree is a loadable runtime org/ folder."""
from __future__ import annotations

import shutil
from pathlib import Path

from src.orchestrator import prompt_loader
from src.orchestrator.teams import TeamsRegistry
from src.runtime import RuntimeDir


EXAMPLE_ROOT = Path(__file__).resolve().parents[1] / "examples" / "orgs" / "hk-macau-tourism"


def test_example_tree_parses_cleanly(tmp_path: Path) -> None:
    rt_root = tmp_path / "rt"
    rt = RuntimeDir.init(rt_root, slug="hk-tourism")
    # Replace seed contents with the example tree.
    shutil.rmtree(rt.org_dir)
    shutil.copytree(EXAMPLE_ROOT / "org", rt.org_dir)
    # Loader should now see all 8 agents.
    agents = sorted(a.name for a in prompt_loader.list_agents(rt))
    assert agents == [
        "content_manager", "content_qa", "content_writer",
        "dev_agent", "engineering_head", "payment_agent",
        "product_manager", "qa_engineer",
    ]
    # Teams registry loads without error.
    teams = TeamsRegistry.load(rt)
    assert sorted(teams.teams()) == ["content", "engineering"]
    eh = prompt_loader.load_agent(rt, "engineering_head")
    assert eh is not None
    assert eh.role == "manager"
    assert eh.team == "engineering"
    assert "gh pr close" in eh.allow_rules
