from __future__ import annotations

from pathlib import Path

from src.orchestrator.prompt_loader import allow_rules_for


def _write(path: Path, contents: str) -> None:
    path.write_text(contents)


def test_parses_allow_rules_bullets(tmp_path: Path) -> None:
    md = tmp_path / "02-system-prompts-managers.md"
    _write(md, """## Engineering Head

```
role text
```

### Allow Rules

Beyond the baseline `opc *` grant, this agent may run:

- `gh pr close`
- `gh pr comment`
- `gh issue close`
- `gh issue comment`

---

## Content Manager

```
role text
```

### Allow Rules

No additional grants.

---
""")
    assert allow_rules_for(tmp_path, "engineering_head") == (
        "gh pr close", "gh pr comment", "gh issue close", "gh issue comment",
    )
    assert allow_rules_for(tmp_path, "content_manager") == ()


def test_missing_subsection_returns_empty(tmp_path: Path) -> None:
    md = tmp_path / "02-system-prompts-managers.md"
    _write(md, """## Engineering Head

```
role text
```

---
""")
    assert allow_rules_for(tmp_path, "engineering_head") == ()


def test_unknown_agent_returns_empty(tmp_path: Path) -> None:
    md = tmp_path / "02-system-prompts-managers.md"
    _write(md, "## Engineering Head\n\n```\nx\n```\n")
    assert allow_rules_for(tmp_path, "nobody") == ()


def test_allow_rules_for_real_protocol_engineering_head() -> None:
    """Test against the actual protocol file to ensure parser pins the EH allow rules."""
    protocol_dir = Path(__file__).parent.parent / "protocol"
    rules = allow_rules_for(protocol_dir, "engineering_head")
    assert rules == ("gh pr close", "gh pr comment", "gh issue close", "gh issue comment")


def test_allow_rules_for_real_protocol_content_manager() -> None:
    """Test against the actual protocol file to ensure CM has no additional grants."""
    protocol_dir = Path(__file__).parent.parent / "protocol"
    rules = allow_rules_for(protocol_dir, "content_manager")
    assert rules == ()


def test_allow_rules_for_real_protocol_operations_manager() -> None:
    """Test against the actual protocol file: OM has no Allow Rules subsection."""
    protocol_dir = Path(__file__).parent.parent / "protocol"
    rules = allow_rules_for(protocol_dir, "operations_manager")
    assert rules == ()


def test_allow_rules_for_eof_section_no_trailing_divider(tmp_path: Path) -> None:
    """Test that parser handles a section ending at EOF with no trailing --- divider."""
    md = tmp_path / "02-system-prompts-managers.md"
    _write(md, """## Engineering Head

```
role text
```

### Allow Rules

- `gh foo`
""")
    assert allow_rules_for(tmp_path, "engineering_head") == ("gh foo",)
