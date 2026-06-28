"""current_time injection into the shared agent-prompt Parameters block.

TASK-976 (THR-039): every provider's prompt carries the local wall-clock + zone,
fresh on every spawn/wake, with an injectable clock for deterministic tests.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

import pytest

from runtime.infrastructure.database import Database
from runtime.orchestrator.orchestrator import Orchestrator
from runtime.orchestrator.teams import TeamsRegistry

_FROZEN = datetime(2026, 6, 27, 4, 47, tzinfo=timezone.utc)  # 12:47 in +08:00


@pytest.fixture
def orch(test_settings, test_runtime):
    test_runtime.root.mkdir(parents=True, exist_ok=True)
    db = Database(test_runtime.db_path)
    teams = TeamsRegistry.load(test_runtime.root)
    return Orchestrator(
        db=db, settings=test_settings, paths=test_runtime, slug="test", teams=teams,
    )


def _write_org_tz(test_runtime, tz: str) -> None:
    path = test_runtime.org_config_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"timezone: {tz}\n")


def test_current_time_line_exact_format_with_configured_tz(orch, test_runtime) -> None:
    _write_org_tz(test_runtime, "Asia/Shanghai")
    prompt = orch._build_agent_prompt(
        "claude", "dev_agent", "TASK-1", "sess-1", "do a thing", "",
        now=lambda: _FROZEN,
    )
    assert "  current_time: 2026-06-27T12:47+08:00 (Asia/Shanghai)\n" in prompt


def test_current_time_line_machine_local_fallback_is_valid(orch) -> None:
    # No org config -> machine-local resolution; the line must still be valid.
    prompt = orch._build_agent_prompt(
        "claude", "dev_agent", "TASK-1", "sess-1", "do a thing", "",
        now=lambda: _FROZEN,
    )
    m = re.search(
        r"^  current_time: 2026-06-27T\d{2}:\d{2}[+-]\d{2}:\d{2} \(.+\)$",
        prompt,
        re.MULTILINE,
    )
    assert m is not None, prompt


@pytest.mark.parametrize("provider", ["claude", "codex", "opencode", "pi"])
def test_current_time_in_all_provider_prompts(orch, test_runtime, provider) -> None:
    _write_org_tz(test_runtime, "Asia/Shanghai")
    prompt = orch._build_agent_prompt(
        provider, "dev_agent", "TASK-1", "sess-1", "brief text", "",
        now=lambda: _FROZEN,
    )
    assert "  current_time: 2026-06-27T12:47+08:00 (Asia/Shanghai)\n" in prompt


def test_current_time_default_clock_emits_line(orch, test_runtime) -> None:
    # Omitting `now` falls back to the real UTC clock; the line must still emit.
    _write_org_tz(test_runtime, "Asia/Shanghai")
    prompt = orch._build_agent_prompt(
        "claude", "dev_agent", "TASK-1", "sess-1", "brief", "",
    )
    assert re.search(r"^  current_time: .+ \(Asia/Shanghai\)$", prompt, re.MULTILINE)


# ═══════════════════════════════════════════════════════════════════
# THR-032 Phase 2 — PUSH memory digest prompt injection
# ═══════════════════════════════════════════════════════════════════


class TestMemoryDigestPromptInjection:
    """Verify the MEMORY-DIGEST block is injected into _build_agent_prompt
    when non-empty and omitted when empty/None."""

    @pytest.fixture
    def orch(self, test_settings, test_runtime):
        test_runtime.root.mkdir(parents=True, exist_ok=True)
        db = Database(test_runtime.db_path)
        teams = TeamsRegistry.load(test_runtime.root)
        return Orchestrator(
            db=db, settings=test_settings, paths=test_runtime, slug="test",
            teams=teams,
        )

    _SAMPLE_DIGEST = (
        "=== MEMORY-DIGEST (system) ===\n"
        "Relevant memory (pointers only — "
        "fetch bodies with `happyranch memory get <id>`):\n"
        "\n"
        "- `MEM-001` — Test Memory  (experiential, salience 90)\n"
    )

    def test_digest_injected_when_non_empty(self, orch):
        """When a non-empty digest is passed, it appears after brief/role_guidance."""
        prompt = orch._build_agent_prompt(
            "claude", "dev_agent", "TASK-1", "sess-1", "do a thing", "",
            memory_digest=self._SAMPLE_DIGEST,
        )
        assert "=== MEMORY-DIGEST (system) ===" in prompt
        assert "MEM-001" in prompt
        # Digest should appear after the brief line
        brief_idx = prompt.index("brief: do a thing")
        digest_idx = prompt.index("=== MEMORY-DIGEST")
        assert digest_idx > brief_idx

    def test_digest_omitted_when_none(self, orch):
        """When memory_digest is None, no MEMORY-DIGEST block appears."""
        prompt = orch._build_agent_prompt(
            "claude", "dev_agent", "TASK-1", "sess-1", "do a thing", "",
            memory_digest=None,
        )
        assert "MEMORY-DIGEST" not in prompt

    def test_digest_omitted_when_empty_string(self, orch):
        """When memory_digest is an empty string, no MEMORY-DIGEST block appears."""
        prompt = orch._build_agent_prompt(
            "claude", "dev_agent", "TASK-1", "sess-1", "do a thing", "",
            memory_digest="",
        )
        assert "MEMORY-DIGEST" not in prompt

    def test_digest_injected_for_all_providers(self, orch):
        """The digest must appear for every provider (harness-agnostic)."""
        for provider in ("claude", "codex", "opencode", "pi"):
            prompt = orch._build_agent_prompt(
                provider, "dev_agent", "TASK-1", "sess-1", "brief", "",
                memory_digest=self._SAMPLE_DIGEST,
            )
            assert "=== MEMORY-DIGEST (system) ===" in prompt, f"missing for {provider}"

    def test_digest_does_not_interfere_with_role_guidance(self, orch):
        """When prompt (role_guidance) is non-empty, the digest still appears
        after it without interfering."""
        prompt = orch._build_agent_prompt(
            "claude", "engineering_head", "TASK-1", "sess-1",
            "manager brief", "Manager capabilities here",
            memory_digest=self._SAMPLE_DIGEST,
        )
        assert "role_guidance:" in prompt
        assert "Manager capabilities here" in prompt
        assert "=== MEMORY-DIGEST (system) ===" in prompt
        # role_guidance block comes before digest
        rg_idx = prompt.index("role_guidance:")
        digest_idx = prompt.index("=== MEMORY-DIGEST")
        assert digest_idx > rg_idx
