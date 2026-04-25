from pathlib import Path

from src.config import Settings
from src.orchestrator.prompt_loader import load_all_prompts, load_system_prompt


def test_load_engineering_head_prompt(tmp_path: Path):
    protocol = tmp_path / "protocol"
    protocol.mkdir()
    (protocol / "02-system-prompts-managers.md").write_text(
        "# Managers\n\n## Engineering Head\n\n```\nYou are the Engineering Head.\n\n## Your Role\nLead the team.\n```\n\n---\n"
    )
    prompt = load_system_prompt(protocol, "engineering_head")
    assert "Engineering Head" in prompt
    assert "Your Role" in prompt


def test_load_dev_agent_prompt(tmp_path: Path):
    protocol = tmp_path / "protocol"
    protocol.mkdir()
    (protocol / "03-system-prompts-workers.md").write_text(
        "# Workers\n\n## Product Manager\n\n```\nYou are the PM.\n```\n\n---\n\n## Dev Agent\n\n```\nYou are the Dev Agent.\n\n## Standards\nWrite tests.\n```\n\n---\n"
    )
    prompt = load_system_prompt(protocol, "dev_agent")
    assert "Dev Agent" in prompt
    assert "Standards" in prompt


def test_load_missing_agent(tmp_path: Path):
    protocol = tmp_path / "protocol"
    protocol.mkdir()
    (protocol / "02-system-prompts-managers.md").write_text("# Empty\n")
    prompt = load_system_prompt(protocol, "engineering_head")
    assert prompt == ""


def test_load_unknown_agent(tmp_path: Path):
    prompt = load_system_prompt(tmp_path, "unknown_agent")
    assert prompt == ""


def test_load_missing_file(tmp_path: Path):
    prompt = load_system_prompt(tmp_path, "engineering_head")
    assert prompt == ""


def test_load_all_prompts(tmp_path: Path):
    protocol = tmp_path / "protocol"
    protocol.mkdir()
    (protocol / "02-system-prompts-managers.md").write_text(
        "## Engineering Head\n\n```\nYou are the Engineering Head.\n```\n\n"
        "## Content Manager\n\n```\nYou are the Content Manager.\n```\n"
    )
    (protocol / "03-system-prompts-workers.md").write_text(
        "## Product Manager\n\n```\nYou are the PM.\n```\n\n"
        "## Dev Agent\n\n```\nYou are Dev.\n```\n\n"
        "## Payment Agent\n\n```\nYou are Payment.\n```\n\n"
        "## QA Engineer\n\n```\nYou are QA Engineer.\n```\n\n"
        "## Content QA\n\n```\nYou are Content QA.\n```\n\n"
        "## Content Writer\n\n```\nYou are the Content Writer.\n```\n"
    )
    prompts = load_all_prompts(protocol)
    assert len(prompts) == 8
    assert "Engineering Head" in prompts["engineering_head"]
    assert "Content Manager" in prompts["content_manager"]
    assert "PM" in prompts["product_manager"]
    assert "Dev" in prompts["dev_agent"]
    assert "Payment" in prompts["payment_agent"]
    assert "QA Engineer" in prompts["qa_engineer"]
    assert "Content QA" in prompts["content_qa"]
    assert "Content Writer" in prompts["content_writer"]


def test_load_from_real_protocol():
    """Verify prompts load from the actual protocol directory."""
    protocol = Path(__file__).resolve().parent.parent / "protocol"
    if not protocol.exists():
        return  # skip in CI if protocol not present
    prompts = load_all_prompts(protocol)
    for agent, prompt in prompts.items():
        assert len(prompt) > 100, f"{agent} prompt too short: {len(prompt)} chars"


def test_content_manager_prompt_loads() -> None:
    s = Settings()
    prompt = load_system_prompt(s.get_protocol_dir(), "content_manager")
    assert prompt.startswith("You are the Content Manager")


def test_content_writer_prompt_loads() -> None:
    s = Settings()
    prompt = load_system_prompt(s.get_protocol_dir(), "content_writer")
    assert "Content Writer" in prompt or "You are the Content Writer" in prompt
