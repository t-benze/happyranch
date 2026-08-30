"""Deterministic launch-linearization coverage for logical custom-skill purge."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from runtime.skills.custom.fence import (
    acquire_custom_skill_publication_read,
    custom_skill_publication_fence,
)


def _start_purge(org: str):
    entered = threading.Event()
    committed = threading.Event()

    def purge() -> None:
        entered.set()
        with custom_skill_publication_fence(org, write=True):
            committed.set()

    thread = threading.Thread(target=purge)
    thread.start()
    assert entered.wait(1)
    return thread, committed


def test_read_token_linearizes_spawn_before_purge_commit():
    token = acquire_custom_skill_publication_read("acme")
    thread, committed = _start_purge("acme")
    assert not committed.wait(0.05)

    # This is the production launch linearization: the token is closed only
    # after backend.launch/Popen returns, so purge cannot commit first.
    token.close()
    assert committed.wait(1)
    thread.join(timeout=1)


def test_read_token_is_org_scoped_and_close_is_idempotent():
    token = acquire_custom_skill_publication_read("acme")
    thread, committed = _start_purge("other")
    assert committed.wait(1)
    token.close()
    token.close()
    thread.join(timeout=1)


def test_launch_preparation_exception_releases_token(monkeypatch, tmp_path: Path):
    import runtime.orchestrator.workspace_adapters as adapters

    monkeypatch.setattr(
        adapters,
        "materialize_workspace_skills",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    with pytest.raises(RuntimeError, match="boom"):
        adapters.prepare_workspace_skills_launch(
            tmp_path, object(), slug="acme", context="task", provider="codex",
            agent_name="dev", team="engineering", skills_root=tmp_path,
        )

    thread, committed = _start_purge("acme")
    assert committed.wait(1)
    thread.join(timeout=1)
