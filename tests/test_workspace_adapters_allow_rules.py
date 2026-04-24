from __future__ import annotations

from src.config import Settings
from src.orchestrator.workspace_adapters import allow_rules_for_agent


def test_baseline_only_for_unknown_agent() -> None:
    s = Settings()
    settings_rules = allow_rules_for_agent(s, "nobody", cli=False)
    cli_rules = allow_rules_for_agent(s, "nobody", cli=True)
    assert settings_rules == ["Bash(opc:*)"]
    assert cli_rules == ["Bash(opc *)"]


def test_engineering_head_gets_gh_extras_from_protocol() -> None:
    s = Settings()
    settings_rules = allow_rules_for_agent(s, "engineering_head", cli=False)
    assert "Bash(opc:*)" in settings_rules
    assert "Bash(gh pr close:*)" in settings_rules
    assert "Bash(gh issue close:*)" in settings_rules


def test_content_manager_has_no_extras() -> None:
    s = Settings()
    assert allow_rules_for_agent(s, "content_manager", cli=False) == ["Bash(opc:*)"]
