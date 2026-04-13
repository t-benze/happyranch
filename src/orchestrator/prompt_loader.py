"""Parse agent system prompts from protocol markdown files."""
from __future__ import annotations

import re
from pathlib import Path


# Maps agent names to (file, heading) so we know where to find each prompt.
_AGENT_SOURCES: dict[str, tuple[str, str]] = {
    "engineering_head": ("02-system-prompts-managers.md", "Engineering Head"),
    "product_manager": ("03-system-prompts-workers.md", "Product Manager"),
    "dev_agent": ("03-system-prompts-workers.md", "Dev Agent"),
    "payment_agent": ("03-system-prompts-workers.md", "Payment Agent"),
}


def _extract_prompt(text: str, heading: str) -> str:
    """Extract the fenced code block under a ## heading."""
    # Find the heading
    pattern = rf"^## {re.escape(heading)}\s*$"
    match = re.search(pattern, text, re.MULTILINE)
    if not match:
        return ""

    # Find the first ``` ... ``` block after the heading
    rest = text[match.end():]
    fence_match = re.search(r"```\n(.*?)```", rest, re.DOTALL)
    if not fence_match:
        return ""

    return fence_match.group(1).strip()


def load_system_prompt(protocol_dir: Path, agent_name: str) -> str:
    """Load an agent's system prompt from protocol markdown files.

    Returns empty string if agent not found or file missing.
    """
    source = _AGENT_SOURCES.get(agent_name)
    if source is None:
        return ""

    filename, heading = source
    filepath = protocol_dir / filename
    if not filepath.exists():
        return ""

    text = filepath.read_text()
    return _extract_prompt(text, heading)


def load_all_prompts(protocol_dir: Path) -> dict[str, str]:
    """Load system prompts for all known agents."""
    return {
        agent: load_system_prompt(protocol_dir, agent)
        for agent in _AGENT_SOURCES
    }
