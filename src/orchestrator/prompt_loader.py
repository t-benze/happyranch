"""Parse agent system prompts from protocol markdown files."""
from __future__ import annotations

import re
from pathlib import Path


# Maps agent names to (file, heading) so we know where to find each prompt.
_AGENT_SOURCES: dict[str, tuple[str, str]] = {
    "engineering_head": ("02-system-prompts-managers.md", "Engineering Head"),
    "content_manager": ("02-system-prompts-managers.md", "Content Manager"),
    "product_manager": ("03-system-prompts-workers.md", "Product Manager"),
    "dev_agent": ("03-system-prompts-workers.md", "Dev Agent"),
    "payment_agent": ("03-system-prompts-workers.md", "Payment Agent"),
    "qa_engineer": ("03-system-prompts-workers.md", "QA Engineer"),
    "content_qa": ("03-system-prompts-workers.md", "Content QA"),
    "content_writer": ("03-system-prompts-workers.md", "Content Writer"),
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


_BULLET_RE = re.compile(r"^-\s+`?([^`\n]+?)`?\s*$")


def allow_rules_for(protocol_dir: Path, agent_name: str) -> tuple[str, ...]:
    """Extract the bullet list under the ``### Allow Rules`` subsection
    inside an agent's role section. Returns ``()`` when the subsection
    is absent, or when the agent is not in ``_AGENT_SOURCES``.

    The agent's role section starts at ``## <Heading>`` and ends at the
    next ``---`` divider (the top-level separator between role sections).
    ``## `` headings inside the fenced code block are intentionally ignored
    because the section boundary is the ``---`` divider, not a heading.
    Inside that range we find ``### Allow Rules``, then collect
    ``- <prefix>`` bullets until the next heading or divider.
    """
    source = _AGENT_SOURCES.get(agent_name)
    if source is None:
        return ()
    filename, heading = source
    filepath = protocol_dir / filename
    if not filepath.exists():
        return ()

    text = filepath.read_text()
    head_re = re.compile(rf"^## {re.escape(heading)}\s*$", re.MULTILINE)
    head = head_re.search(text)
    if head is None:
        return ()

    # Section ends at the next top-level ``---`` divider (on its own line).
    # We don't use ``## `` as an end marker because those appear inside the
    # fenced code block that holds the agent's system prompt.
    end_re = re.compile(r"^---\s*$", re.MULTILINE)
    end = end_re.search(text, head.end())
    section = text[head.end(): end.start() if end else len(text)]

    sub_re = re.compile(r"^### Allow Rules\s*$", re.MULTILINE)
    sub = sub_re.search(section)
    if sub is None:
        return ()

    tail_re = re.compile(r"^(## |### |---\s*$)", re.MULTILINE)
    tail = tail_re.search(section, sub.end())
    body = section[sub.end(): tail.start() if tail else len(section)]

    rules: list[str] = []
    for line in body.splitlines():
        m = _BULLET_RE.match(line)
        if m:
            rules.append(m.group(1).strip())
    return tuple(rules)
