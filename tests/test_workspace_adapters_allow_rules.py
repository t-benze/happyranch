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


def test_dynamic_agent_uses_db_allow_rules(tmp_path) -> None:
    from src.runtime import RuntimeDir
    from src.infrastructure.database import Database

    rt = RuntimeDir.init(tmp_path / "rt")
    db = Database(rt.db_path)
    db.insert_enrollment(
        name="seo_bot",
        description="d",
        system_prompt="s",
        repos={},
        executor="claude",
        allow_rules=["curl https://api.example.com"],
    )
    db.update_enrollment_status("seo_bot", "approved")

    s = Settings()
    rules = allow_rules_for_agent(s, "seo_bot", cli=False, db=db)
    assert "Bash(opc:*)" in rules
    assert "Bash(curl https://api.example.com:*)" in rules
