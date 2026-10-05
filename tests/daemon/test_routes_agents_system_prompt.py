"""THR-280 founder prompt-only HTTP contracts, accepted cases C01/C04/C07/C20.

Function/parameter inventory and four test-authoring answers are recorded in
TASK-9801's implementation checkpoint. These use real OrgState and pair writers.
"""
from __future__ import annotations

import hashlib
import asyncio
import os
from contextlib import asynccontextmanager
from dataclasses import asdict, replace

import pytest
from fastapi.testclient import TestClient

from runtime.orchestrator import prompt_loader
from runtime.orchestrator.agent_def import render_agent_text
from runtime.orchestrator.context_builder import ContextBuilder
from tests.daemon.test_routes_agents import (
    _authority_generation,
    _paths,
    _seed_active_agent,
)

URL = "/api/v1/orgs/alpha/agents/dev_agent/system-prompt"
MARKDOWN = "# 指令 🐎\n\n正文  \n\n    code\n\n```text\n例子\n```\n\n## Details\n保留内容\n"


@pytest.fixture(params=["claude", "codex", "pi", "opencode", "prompt_codex"])
def prompt_profile(request, monkeypatch):
    """An isolated registered alias uses the existing Codex adapters."""
    from runtime.orchestrator.executor_registry import get_registry

    registry = get_registry()
    if request.param == "prompt_codex":
        monkeypatch.setattr(registry, "_profiles", dict(registry._profiles))
        registry.register_custom_profile(replace(registry.get_profile("codex"), name=request.param))
    return request.param


def _record_next_task_delivery(org_state, monkeypatch, target, expected_prompt):
    """Observe the production task launch; the child only reads workspace files."""
    import json
    import subprocess
    import sys

    from runtime.orchestrator.executors import ExecutorResult
    from runtime.orchestrator.executor_registry import get_registry
    from runtime.platform.session_backend import LaunchSpec

    final, revision, raw = prompt_loader.load_agent_snapshot(_paths(org_state), target)
    assert revision == hashlib.sha256(raw).hexdigest()
    profile = get_registry().get_profile(final.executor)
    assert profile is not None
    assert profile.workspace_adapter_id == ("codex" if final.executor == "prompt_codex" else final.executor)
    assert profile.command_adapter_id == profile.workspace_adapter_id
    selected, launches = [], []

    class Recorder:
        def build_launch_spec(self, **kwargs):
            return LaunchSpec(argv=(sys.executable, "-c", (
                "import json,os,stat,sys; from pathlib import Path; sys.stdin.read(); "
                "print(json.dumps({'cwd':str(Path.cwd()),'body':Path('AGENTS.md').read_text(),"
                "'mode':os.lstat('AGENTS.md').st_mode,'link':os.readlink('CLAUDE.md')}))"
            )), cwd=str(kwargs["workspace"]))

        def run(self, **kwargs):
            if kwargs.get("pre_launch_validator") is not None:
                kwargs["pre_launch_validator"]()
            running = kwargs.get("running")
            if running is not None:
                out, err = running.process.communicate(input=kwargs["prompt"], timeout=10)
                assert running.process.returncode == 0, err
            else:
                spec = self.build_launch_spec(**kwargs)
                out = subprocess.run(spec.argv, cwd=spec.cwd, input=kwargs["prompt"],
                                     capture_output=True, text=True, timeout=10, check=True).stdout
            launches.append({**json.loads(out), "model": kwargs["model"], "session": kwargs["session_id"]})
            return ExecutorResult(success=True, duration_seconds=0, session_id=kwargs["session_id"])

    def select(provider):
        selected.append(provider)
        return Recorder()

    orchestrator = org_state.orchestrator
    monkeypatch.setattr(orchestrator, "_build_executor", select)
    task_id = orchestrator.create_task("Observe prompt delivery after the competing writer")
    result, report = orchestrator._run_agent(task_id, target, "")
    assert result.success, result.error
    assert report is None
    assert selected == [final.executor]
    assert len(launches) == 1
    launch = launches[0]
    import stat
    assert stat.S_ISREG(launch["mode"])
    assert launch["cwd"] == str(_paths(org_state).workspaces_dir / target)
    assert launch["link"] == "AGENTS.md"
    assert "## System Prompt" in launch["body"]
    assert expected_prompt.strip() in launch["body"]
    assert "OLD" not in launch["body"]
    assert launch["model"] == final.model
    starts = [r for r in org_state.db.get_audit_logs(task_id) if r["action"] == "session_start"]
    assert len(starts) == 1
    assert starts[0]["payload"]["executor"] == final.executor
    assert starts[0]["payload"]["session_id"] == launch["session"]


def _seed(org_state, *, workspace: bool = True) -> tuple:
    _seed_active_agent(org_state, "dev_agent", system_prompt="OLD\n", model="kept-model")
    paths = _paths(org_state)
    loaded = prompt_loader.load_agent(paths, "dev_agent")
    assert loaded is not None
    agent = replace(loaded, description="保留 description", allow_rules=("git",),
                    repos={"source": "https://example.test/source.git"})
    canonical = paths.agents_dir / "dev_agent.md"
    canonical.write_text(render_agent_text(agent), encoding="utf-8")
    ws = paths.workspaces_dir / "dev_agent"
    if workspace:
        ws.mkdir(parents=True, exist_ok=True)
        ContextBuilder(org_state.settings, paths, slug=org_state.slug).write_claude_md(
            ws, "dev_agent", "OLD\n",
        )
    _authority_generation(org_state)
    snapshot = prompt_loader.load_agent_snapshot(paths, "dev_agent")
    assert snapshot is not None
    return snapshot


def _state(org_state) -> tuple:
    paths = _paths(org_state)
    ws = paths.workspaces_dir / "dev_agent"
    pair = tuple(
        (name, os.readlink(ws / name) if (ws / name).is_symlink()
         else (ws / name).read_bytes(), (ws / name).lstat().st_mode)
        for name in ("AGENTS.md", "CLAUDE.md")
    ) if ws.exists() else None
    audits = org_state.db.get_audit_logs("founder")
    return (paths.agents_dir / "dev_agent.md").read_bytes(), pair, audits


@pytest.mark.parametrize("authored,normalized", [
    pytest.param(MARKDOWN, MARKDOWN, id="lf"),
    pytest.param(MARKDOWN.replace("\n", "\r\n"), MARKDOWN, id="crlf"),
    pytest.param(MARKDOWN.replace("\n", "\r"), MARKDOWN, id="cr"),
    pytest.param("\n\n" + MARKDOWN, MARKDOWN, id="leading-lf"),
    pytest.param(MARKDOWN[:-1], MARKDOWN, id="no-final-lf"),
    pytest.param(MARKDOWN + "  \n\n", MARKDOWN + "  \n\n", id="trailing-space"),
])
def test_prompt_save_normalizes_and_preserves_fields(
    app, org_state, auth_headers, authored: str, normalized: str,
) -> None:
    before, revision, _ = _seed(org_state)
    client = TestClient(app)
    response = client.put(URL, headers=auth_headers, json={
        "system_prompt": authored, "expected_revision": revision,
    })
    assert response.status_code == 200, response.text
    snapshot = prompt_loader.load_agent_snapshot(_paths(org_state), "dev_agent")
    assert snapshot is not None
    after, new_revision, contents = snapshot
    assert after.system_prompt == normalized
    assert asdict(replace(after, system_prompt=before.system_prompt)) == asdict(before)
    assert new_revision == hashlib.sha256(contents).hexdigest()
    assert response.json() == {
        "agent": "dev_agent", "system_prompt": normalized, "revision": new_revision,
    }
    roster = client.get("/api/v1/orgs/alpha/agents", headers=auth_headers).json()["agents"]
    observed = next(a for a in roster if a["name"] == "dev_agent")
    assert (observed["system_prompt"], observed["revision"]) == (normalized, new_revision)
    ws = _paths(org_state).workspaces_dir / "dev_agent"
    assert not (ws / "AGENTS.md").is_symlink()
    assert os.readlink(ws / "CLAUDE.md") == "AGENTS.md"
    text = (ws / "AGENTS.md").read_text()
    assert normalized.strip() in text
    assert "OLD" not in text
    audits = [a for a in org_state.db.get_audit_logs("founder") if a["action"] == "agent_managed"]
    assert len(audits) == 1
    assert audits[0]["agent"] == "founder"
    assert audits[0]["task_id"] == "founder"
    assert audits[0]["payload"] == {"action": "update", "name": "dev_agent", "source": "founder"}


@pytest.mark.parametrize("revision", [
    pytest.param(..., id="omitted"), pytest.param(None, id="null"),
    pytest.param("", id="empty"), pytest.param("0" * 63, id="short"),
    pytest.param("0" * 65, id="long"), pytest.param("g" * 64, id="nonhex"),
    pytest.param("A" * 64, id="uppercase"),
])
def test_prompt_revision_validation_has_no_write(app, org_state, auth_headers, revision) -> None:
    _seed(org_state)
    before = _state(org_state)
    body = {"system_prompt": "NEW\n"}
    if revision is not ...:
        body["expected_revision"] = revision
    response = TestClient(app).put(URL, headers=auth_headers, json=body)
    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "expected_revision_required"
    assert _state(org_state) == before


@pytest.mark.parametrize("delta", [
    pytest.param({"system_prompt": None}, id="null"),
    pytest.param({"system_prompt": ""}, id="empty"),
    pytest.param({"system_prompt": " \n\t "}, id="whitespace"),
    pytest.param({"system_prompt": 7}, id="number"),
    pytest.param({"system_prompt": []}, id="list"),
    pytest.param({"system_prompt": {}}, id="object"),
    pytest.param({"expected_revision": 7}, id="revision-number"),
    *[pytest.param({key: "unapproved"}, id=f"extra-{key}") for key in (
        "description", "role", "team", "executor", "model", "repos",
        "allow_rules", "enrolled_by", "enrolled_at_task", "enrolled_at",
    )],
    pytest.param({"system_prompt": ...}, id="omitted"),
])
def test_prompt_request_validation_has_no_write(app, org_state, auth_headers, delta: dict) -> None:
    _, revision, _ = _seed(org_state)
    before = _state(org_state)
    body = {"system_prompt": "NEW\n", "expected_revision": revision, **delta}
    if body["system_prompt"] is ...:
        del body["system_prompt"]
    response = TestClient(app).put(URL, headers=auth_headers, json=body)
    assert response.status_code == 422, response.text
    assert _state(org_state) == before


def test_prompt_stale_loser_preserves_winner(app, org_state, auth_headers) -> None:
    _, revision, _ = _seed(org_state)
    client = TestClient(app)
    winner = client.put(URL, headers=auth_headers, json={
        "system_prompt": "WINNER\n", "expected_revision": revision,
    })
    assert winner.status_code == 200, winner.text
    before = _state(org_state)
    loser = client.put(URL, headers=auth_headers, json={
        "system_prompt": "LOSER\n", "expected_revision": revision,
    })
    assert loser.status_code == 409, loser.text
    assert loser.json()["detail"] == {
        "code": "stale_agent_revision", "current_revision": winner.json()["revision"],
    }
    assert _state(org_state) == before


def test_prompt_absent_workspace_commits_canonical_only(app, org_state, auth_headers) -> None:
    _, revision, _ = _seed(org_state, workspace=False)
    ws = _paths(org_state).workspaces_dir / "dev_agent"
    assert not ws.exists()
    response = TestClient(app).put(URL, headers=auth_headers, json={
        "system_prompt": "NEW\n", "expected_revision": revision,
    })
    assert response.status_code == 200, response.text
    assert not ws.exists()
    snapshot = prompt_loader.load_agent_snapshot(_paths(org_state), "dev_agent")
    assert snapshot is not None
    assert snapshot[0].system_prompt == response.json()["system_prompt"] == "NEW\n"
    assert snapshot[1] == response.json()["revision"] == hashlib.sha256(snapshot[2]).hexdigest()


@pytest.mark.parametrize("route,delta", [
    pytest.param("model", "new-model", id="model-set"),
    pytest.param("model", None, id="model-clear"),
    pytest.param("repo", "add", id="repo-add"),
    pytest.param("repo", "update", id="repo-update"),
    pytest.param("repo", "remove", id="repo-remove"),
])
def test_prompt_winner_before_sibling_acquisition_survives(
    org_state, monkeypatch, prompt_profile, route: str, delta: str | None,
) -> None:
    """C14: accepted prompt winner precedes actual sibling gate acquisition."""
    from runtime.daemon.routes import agents as routes

    existing, _, _ = _seed(org_state)
    paths = _paths(org_state)
    (paths.agents_dir / "dev_agent.md").write_text(render_agent_text(replace(existing, executor=prompt_profile)))
    _authority_generation(org_state)
    revision = prompt_loader.agent_revision(paths, "dev_agent")
    real_interval = routes._consumer_writer_interval
    arrived, release = asyncio.Event(), asyncio.Event()

    @asynccontextmanager
    async def delayed_interval(org, **kwargs):
        if kwargs["publisher"] == ("set_agent_model" if route == "model" else "manage_repo"):
            arrived.set()
            await asyncio.wait_for(release.wait(), 10)
        async with real_interval(org, **kwargs) as interval:
            yield interval

    monkeypatch.setattr(routes, "_consumer_writer_interval", delayed_interval)
    # Network clone is an external boundary; instruction generation stays real.
    monkeypatch.setattr(ContextBuilder, "clone_repo", lambda *args: True)

    async def exercise():
        if route == "model":
            operation = routes.set_agent_model("alpha", "dev_agent", routes.SetModelBody(model=delta), org_state)
        else:
            operation = routes.manage_repo("alpha", "dev_agent", routes.ManageRepoBody(
                action=delta, repo_name="extra" if delta == "add" else "source",
                url="https://example.test/new.git" if delta != "remove" else None,
            ), org_state)
        sibling = asyncio.create_task(operation)
        try:
            await asyncio.wait_for(arrived.wait(), 10)
            await routes.set_agent_system_prompt("alpha", "dev_agent", routes.SystemPromptBody(
                system_prompt="WINNER\n", expected_revision=revision,
            ), org_state)
        finally:
            release.set()
            await asyncio.wait_for(sibling, 10)
        final, digest, contents = prompt_loader.load_agent_snapshot(_paths(org_state), "dev_agent")
        assert final.system_prompt == "WINNER\n"
        assert digest == hashlib.sha256(contents).hexdigest()
        if route == "model":
            assert final.model == delta
        elif delta == "remove":
            assert "source" not in final.repos
        else:
            assert final.repos["extra" if delta == "add" else "source"] == "https://example.test/new.git"
        ws = _paths(org_state).workspaces_dir / "dev_agent"
        assert "WINNER" in (ws / "AGENTS.md").read_text()
        assert "OLD" not in (ws / "AGENTS.md").read_text()
        assert os.readlink(ws / "CLAUDE.md") == "AGENTS.md"

    asyncio.run(exercise())
    _record_next_task_delivery(org_state, monkeypatch, "dev_agent", "WINNER\n")


@pytest.mark.parametrize("workspace", [True, False], ids=["workspace", "absent"])
@pytest.mark.parametrize("position", ["plain", "embedded", "fenced"])
@pytest.mark.parametrize("marker", [
    "## [RESERVED] Active Team Escalation Policy",
    "## [reserved] aCtIvE tEaM eScAlAtIoN pOlIcY",
    "<!-- BEGIN HAPPYRANCH ACTIVE TEAM POLICY -->",
    "<!-- begin happyranch active team policy -->",
    "<!-- END HAPPYRANCH ACTIVE TEAM POLICY -->",
    "<!-- end happyranch active team policy -->",
    "## Workflow",
])
def test_prompt_protected_input_refuses_before_persistence(
    app, org_state, auth_headers, workspace: bool, position: str, marker: str,
) -> None:
    """C19: existing scanners, no policy reinterpretation or workspace dependency."""
    _, revision, _ = _seed(org_state, workspace=workspace)
    before = _state(org_state)
    if position == "embedded":
        # Ordinary H2 semantics are line-start/exact-case, unlike policy markers.
        prompt = f"prefix {marker} suffix\n" if "Workflow" not in marker else f"prefix\n{marker}\nsuffix\n"
    elif position == "fenced":
        prompt = f"```text\n{marker}\n```\n"
    else:
        prompt = marker + "\n"
    response = TestClient(app).put(URL, headers=auth_headers, json={
        "system_prompt": prompt, "expected_revision": revision,
    })
    assert response.status_code == 422, response.text
    assert _state(org_state) == before


@pytest.mark.parametrize("prompt", ["## workflow\nAllowed\n", "prefix ## Workflow suffix\n", "```text\n## Details\n```\n"])
def test_prompt_scanner_valid_controls(app, org_state, auth_headers, prompt: str) -> None:
    _, revision, _ = _seed(org_state, workspace=False)
    response = TestClient(app).put(URL, headers=auth_headers, json={
        "system_prompt": prompt, "expected_revision": revision,
    })
    assert response.status_code == 200, response.text
    assert response.json()["system_prompt"] == prompt


@pytest.mark.parametrize("fault", ["write", "replace"])
def test_prompt_canonical_failure_preserves_exact_state(app, org_state, auth_headers, monkeypatch, fault: str) -> None:
    """C11: actual file failure, no pair/audit or temporary canonical residue."""
    from runtime.daemon.routes import agents as routes
    _, revision, _ = _seed(org_state)
    before = _state(org_state)
    directory = set(_paths(org_state).agents_dir.iterdir())
    if fault == "replace":
        real_replace = routes.os.replace
        def fail_replace(source, destination):
            if destination == _paths(org_state).agents_dir / "dev_agent.md":
                raise OSError("injected canonical replace")
            return real_replace(source, destination)
        monkeypatch.setattr(routes.os, "replace", fail_replace)
    else:
        def fail_open(fd, *args, **kwargs):
            os.close(fd)
            raise OSError("injected canonical open")
        monkeypatch.setattr(routes.os, "fdopen", fail_open)
    response = TestClient(app, raise_server_exceptions=False).put(URL, headers=auth_headers, json={
        "system_prompt": "NEW\n", "expected_revision": revision,
    })
    assert response.status_code == 500
    assert _state(org_state) == before
    assert set(_paths(org_state).agents_dir.iterdir()) == directory


@pytest.mark.parametrize("ownership", ["owned", "winner", "disappeared", "restore-failure"])
def test_prompt_pair_failure_compensates_only_owned_original_bytes(
    app, org_state, auth_headers, monkeypatch, ownership: str,
) -> None:
    """C12: full original CRLF bytes, detected winners and honest restore failure."""
    from runtime.daemon.routes import agents as routes
    from runtime.orchestrator import workspace_adapters as adapters
    _seed(org_state)
    canonical = _paths(org_state).agents_dir / "dev_agent.md"
    canonical.write_bytes(canonical.read_bytes().replace(b"\n", b"\r\n"))
    before = _state(org_state)
    _, revision, original = prompt_loader.load_agent_snapshot(_paths(org_state), "dev_agent")
    winner_bytes = b""
    real_write = adapters._atomic_write_regular
    def fail_pair(path, data, mode=0o644):
        nonlocal winner_bytes
        if path.name == "AGENTS.md":
            real_write(path, data, mode)
            if ownership == "winner":
                winner_bytes = original.replace(b"OLD", b"EXTERNAL_WINNER")
                canonical.write_bytes(winner_bytes)
            elif ownership == "disappeared":
                canonical.unlink()
            raise OSError("injected pair write after canonical text")
        return real_write(path, data, mode)
    monkeypatch.setattr(adapters, "_atomic_write_regular", fail_pair)
    if ownership == "restore-failure":
        real_restore = routes._prompt_replace_bytes
        calls = 0
        def fail_restore(path, contents):
            nonlocal calls
            calls += 1
            if calls > 1:
                raise OSError("injected original-byte restore")
            return real_restore(path, contents)
        monkeypatch.setattr(routes, "_prompt_replace_bytes", fail_restore)
    response = TestClient(app).put(URL, headers=auth_headers, json={
        "system_prompt": "NEW\n", "expected_revision": revision,
    })
    assert response.status_code == 400, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "system_prompt_reconciliation_failed"
    expected = "restored" if ownership == "owned" else "failed" if ownership == "restore-failure" else "not_owned"
    assert detail["compensation"]["canonical"] == expected
    if ownership == "owned":
        assert _state(org_state) == before
        assert detail["compensation"]["workspace"] == "restored"
    elif ownership == "winner":
        assert canonical.read_bytes() == winner_bytes
    elif ownership == "disappeared":
        assert not canonical.exists()
    else:
        assert prompt_loader.load_agent(_paths(org_state), "dev_agent").system_prompt == "NEW\n"
    assert org_state.db.get_audit_logs("founder") == before[2]


def test_prompt_audit_failure_reports_possible_commit(app, org_state, auth_headers, monkeypatch) -> None:
    """C12: failed audit does not pretend to roll back the committed body/pair."""
    from runtime.infrastructure.audit_logger import AuditLogger
    _, revision, _ = _seed(org_state)
    def fail_audit(*args, **kwargs):
        raise RuntimeError("injected audit insert")
    monkeypatch.setattr(AuditLogger, "log_agent_managed", fail_audit)
    response = TestClient(app).put(URL, headers=auth_headers, json={
        "system_prompt": "NEW\n", "expected_revision": revision,
    })
    assert response.status_code == 500
    assert response.json()["detail"] == {
        "code": "system_prompt_audit_failed", "commit_state": "possibly_committed",
    }
    assert prompt_loader.load_agent(_paths(org_state), "dev_agent").system_prompt == "NEW\n"
    assert "NEW" in (_paths(org_state).workspaces_dir / "dev_agent" / "AGENTS.md").read_text()
    assert not org_state.db.get_audit_logs("founder")


@pytest.mark.parametrize("provider", ["claude", "codex", "pi", "opencode"])
def test_prompt_text_refresh_uses_selected_registered_adapter(app, org_state, auth_headers, provider: str) -> None:
    """C15 preparation: provider-selected real text, no full settings bootstrap."""
    existing, _, _ = _seed(org_state)
    canonical = _paths(org_state).agents_dir / "dev_agent.md"
    canonical.write_text(render_agent_text(replace(existing, executor=provider)))
    _authority_generation(org_state)
    revision = prompt_loader.agent_revision(_paths(org_state), "dev_agent")
    workspace = _paths(org_state).workspaces_dir / "dev_agent"
    sentinel = workspace / "opencode.json"
    sentinel.write_bytes(b"untouched-settings")
    response = TestClient(app).put(URL, headers=auth_headers, json={
        "system_prompt": MARKDOWN, "expected_revision": revision,
    })
    assert response.status_code == 200, response.text
    assert response.json()["system_prompt"] == MARKDOWN
    assert sentinel.read_bytes() == b"untouched-settings"
    assert MARKDOWN.strip() in (workspace / "AGENTS.md").read_text()
    assert "OLD" not in (workspace / "AGENTS.md").read_text()
    assert os.readlink(workspace / "CLAUDE.md") == "AGENTS.md"
    assert not (workspace / ".claude" / "settings.json").exists()


def test_prompt_absent_save_later_explicit_init_delivers_new(app, org_state, auth_headers, monkeypatch) -> None:
    """C20: canonical-only save precedes real explicit bootstrap/readiness."""
    _, revision, _ = _seed(org_state, workspace=False)
    workspace = _paths(org_state).workspaces_dir / "dev_agent"
    client = TestClient(app)
    saved = client.put(URL, headers=auth_headers, json={"system_prompt": "NEW\n", "expected_revision": revision})
    assert saved.status_code == 200, saved.text
    assert not workspace.exists()
    monkeypatch.setattr(ContextBuilder, "clone_repo", lambda *args: True)
    initialized = client.post("/api/v1/orgs/alpha/agents/init", headers=auth_headers, json={"agent": "dev_agent"})
    assert initialized.status_code == 200
    assert '"phase": "all_done"' in initialized.text, initialized.text
    assert "NEW" in (workspace / "AGENTS.md").read_text()
    assert "OLD" not in (workspace / "AGENTS.md").read_text()
    assert os.readlink(workspace / "CLAUDE.md") == "AGENTS.md"
    assert (workspace / ".claude" / "skills" / "start-task" / "SKILL.md").is_file()


@pytest.mark.parametrize("provider", ["claude", "codex", "pi", "opencode"])
def test_prompt_http_save_delivers_new_at_next_real_task_launch(
    app, org_state, auth_headers, monkeypatch, provider: str,
) -> None:
    """C15: external launch recorder reads delivery, never refreshes it.

    This proves preparation at the real task launch boundary, not adoption
    by a retained provider conversation (the separate finite C16 probes).
    """
    import json
    import subprocess
    import sys

    from runtime.orchestrator.executors import ExecutorResult
    from runtime.platform.session_backend import LaunchSpec

    existing, _, _ = _seed(org_state)
    paths = _paths(org_state)
    (paths.agents_dir / "dev_agent.md").write_text(
        render_agent_text(replace(existing, executor=provider)), encoding="utf-8",
    )
    _authority_generation(org_state)
    workspace = paths.workspaces_dir / "dev_agent"
    ContextBuilder(org_state.settings, paths, slug=org_state.slug).ensure_workspace_ready(
        workspace, "dev_agent", "OLD\n", provider=provider,
    )
    assert "OLD" in (workspace / "AGENTS.md").read_text()
    revision = prompt_loader.agent_revision(paths, "dev_agent")
    saved = TestClient(app).put(URL, headers=auth_headers, json={
        "system_prompt": MARKDOWN, "expected_revision": revision,
    })
    assert saved.status_code == 200, saved.text
    snapshot = prompt_loader.load_agent_snapshot(paths, "dev_agent")
    assert snapshot is not None
    assert snapshot[0].system_prompt == saved.json()["system_prompt"] == MARKDOWN
    assert snapshot[1] == saved.json()["revision"] == hashlib.sha256(snapshot[2]).hexdigest()

    launches = []
    selected = []

    class ReadOnlyExecutorRecorder:
        def build_launch_spec(self, **kwargs):
            return LaunchSpec(
                argv=(sys.executable, "-c", (
                    "import json,os,sys; from pathlib import Path; sys.stdin.read(); "
                    "print(json.dumps({'workspace':str(Path.cwd()),"
                    "'body':Path('AGENTS.md').read_text(),"
                    "'link':os.readlink('CLAUDE.md')}))"
                )), cwd=str(kwargs["workspace"]),
            )

        def run(self, **kwargs):
            validate = kwargs["pre_launch_validator"]
            if validate is not None:
                validate()
            running = kwargs.get("running")
            if running is not None:
                stdout, stderr = running.process.communicate(input=kwargs["prompt"], timeout=10)
                assert running.process.returncode == 0, stderr
            else:
                spec = self.build_launch_spec(**kwargs)
                child = subprocess.run(
                    spec.argv, cwd=spec.cwd, input=kwargs["prompt"],
                    capture_output=True, text=True, timeout=10, check=True,
                )
                stdout = child.stdout
            observed = json.loads(stdout)
            launches.append({
                **observed,
                "model": kwargs["model"], "session": kwargs["session_id"],
            })
            return ExecutorResult(
                success=True, duration_seconds=0, session_id=kwargs["session_id"],
            )

    def select_executor(actual_provider):
        selected.append(actual_provider)
        return ReadOnlyExecutorRecorder()

    orchestrator = org_state.orchestrator
    monkeypatch.setattr(orchestrator, "_build_executor", select_executor)
    task_id = orchestrator.create_task("Record the next task launch boundary")
    result, report = orchestrator._run_agent(task_id, "dev_agent", "")
    assert result.success, result.error
    assert report is None
    assert selected == [provider]
    assert len(launches) == 1
    launch = launches[0]
    assert launch["workspace"] == str(workspace)
    assert MARKDOWN.strip() in launch["body"]
    assert "OLD" not in launch["body"]
    assert launch["link"] == "AGENTS.md"
    assert launch["model"] == existing.model
    starts = [row for row in org_state.db.get_audit_logs(task_id) if row["action"] == "session_start"]
    assert len(starts) == 1
    assert starts[0]["payload"]["executor"] == provider
    assert starts[0]["payload"]["session_id"] == launch["session"]


@pytest.mark.parametrize("provider", ["claude", "codex", "pi", "opencode"])
@pytest.mark.parametrize("context", ["reply", "bootstrap", "task_followup", "wake", "dream"])
def test_prompt_http_save_delivers_new_at_next_auxiliary_launch(
    app, org_state, auth_headers, monkeypatch, provider: str, context: str,
) -> None:
    """C15: real runner/admission with an external, read-only file recorder.

    Q1-Q4 per parameter are in TASK-9801's case inventory. The child cannot
    manufacture delivery or prove provider cognition/retained adoption.
    """
    import json
    import subprocess
    import sys
    from datetime import datetime, timezone

    from runtime.daemon import thread_runner
    from runtime.daemon.dream_runner import run_dream
    from runtime.daemon.wake_runner import run_wake
    from runtime.models import (
        DreamRecord, ThreadInvocationPurpose, ThreadMessageKind, ThreadRecord,
        WorkHourMode, WorkHourRecord, WorkHourStatus,
    )
    from runtime.orchestrator.executors import ExecutorResult
    from tests.test_thread_runner import _seed_queued_reply

    existing, _, _ = _seed(org_state)
    paths = _paths(org_state)
    (paths.agents_dir / "dev_agent.md").write_text(
        render_agent_text(replace(existing, executor=provider)), encoding="utf-8",
    )
    _authority_generation(org_state)
    workspace = paths.workspaces_dir / "dev_agent"
    ContextBuilder(org_state.settings, paths, slug=org_state.slug).ensure_workspace_ready(
        workspace, "dev_agent", "OLD\n", provider=provider,
    )
    prompt = MARKDOWN + "\n## Routine Tasks\n\n- Record this wake.\n"
    saved = TestClient(app).put(URL, headers=auth_headers, json={
        "system_prompt": prompt,
        "expected_revision": prompt_loader.agent_revision(paths, "dev_agent"),
    })
    assert saved.status_code == 200, saved.text
    snapshot = prompt_loader.load_agent_snapshot(paths, "dev_agent")
    assert snapshot is not None
    assert snapshot[0].system_prompt == saved.json()["system_prompt"] == prompt
    assert snapshot[1] == saved.json()["revision"] == hashlib.sha256(snapshot[2]).hexdigest()
    launches, selected = [], []

    class Recorder:
        def run(self, **kwargs):
            if kwargs.get("pre_launch_validator") is not None:
                kwargs["pre_launch_validator"]()
            child = subprocess.run(
                (sys.executable, "-c", (
                    "import json,os; from pathlib import Path; "
                    "print(json.dumps({'cwd':str(Path.cwd()),"
                    "'body':Path('AGENTS.md').read_text(),"
                    "'regular':Path('AGENTS.md').is_file() and not Path('AGENTS.md').is_symlink(),"
                    "'link':os.readlink('CLAUDE.md')}))"
                )), cwd=kwargs["workspace"], capture_output=True, text=True,
                timeout=10, check=True,
            )
            launches.append({**json.loads(child.stdout),
                             "model": kwargs["model"], "session": kwargs["session_id"]})
            return ExecutorResult(success=True, duration_seconds=0,
                                  session_id=kwargs["session_id"])

    def factory(actual_provider, settings, actual_paths):
        selected.append(actual_provider)
        assert actual_paths.root == org_state.root
        return Recorder()

    async def launch():
        if context in {"reply", "bootstrap", "task_followup"}:
            org_state.db.insert_thread(ThreadRecord(id="THR-PROMPT", subject="Record launch"))
            org_state.db.add_thread_participant("THR-PROMPT", "dev_agent", added_by="founder")
            org_state.db.append_thread_message(
                thread_id="THR-PROMPT", speaker="founder",
                kind=ThreadMessageKind.MESSAGE, body_markdown="Record the launch boundary.",
            )
            if context == "reply":
                invocation = _seed_queued_reply(org_state.db, "THR-PROMPT", "dev_agent", 1)
            elif context == "bootstrap":
                invocation = org_state.db.mint_thread_invocation(
                    thread_id="THR-PROMPT", agent_name="dev_agent", triggering_seq=1,
                    purpose=ThreadInvocationPurpose.BOOTSTRAP,
                )
            else:
                invocation, _ = org_state.db.mint_followup_invocation_with_cap_extend(
                    "THR-PROMPT", agent_name="dev_agent", triggering_seq=1,
                )
            monkeypatch.setattr(thread_runner, "_build_executor_for_provider", factory)
            await thread_runner.run_invocation(
                org_state=org_state, invocation_token=invocation.invocation_token,
                settings=org_state.settings,
            )
            row = org_state.db.get_invocation_any_status(invocation.invocation_token)
            assert row.executor == provider and row.model == existing.model
        elif context == "wake":
            org_state.db.work_hours.insert(WorkHourRecord(
                id="WORKHOUR-PROMPT", agent_name="dev_agent", local_date="2026-10-06",
                slot="09:00", mode=WorkHourMode.WINDOWED,
                scheduled_for=datetime(2026, 10, 6, tzinfo=timezone.utc),
                status=WorkHourStatus.PENDING, routine_count=1,
            ))
            await run_wake(org_state=org_state, work_hour_id="WORKHOUR-PROMPT",
                           settings=org_state.settings, executor_factory=factory)
        else:
            org_state.db.insert_dream(DreamRecord(
                id="DREAM-PROMPT", agent_name="dev_agent", local_date="2026-10-06",
                scheduled_for=datetime(2026, 10, 6, tzinfo=timezone.utc),
                window_end=datetime(2026, 10, 6, tzinfo=timezone.utc),
            ))
            await run_dream(org_state=org_state, dream_id="DREAM-PROMPT",
                            executor_factory=factory)

    asyncio.run(launch())
    assert selected == [provider]
    # The recorder intentionally submits no callback. Thread runners therefore
    # perform their ordinary single no-callback correction; inspect both real
    # launches rather than faking a settled provider reply to suppress it.
    assert len(launches) == (2 if context in {"reply", "bootstrap", "task_followup"} else 1)
    for observed in launches:
        assert observed["cwd"] == str(workspace)
        assert observed["regular"] and observed["link"] == "AGENTS.md"
        assert prompt.strip() in observed["body"]
        assert "OLD" not in observed["body"]
        assert "System Prompt" in observed["body"]
        assert observed["model"] == existing.model
        assert observed["session"]


def test_prompt_repairs_pair_non_destructively_without_following_external_links(app, org_state, auth_headers, tmp_path) -> None:
    """C20: repairable raw link and independent legacy regular file retain bytes."""
    _, revision, _ = _seed(org_state)
    workspace = _paths(org_state).workspaces_dir / "dev_agent"
    external = tmp_path / "external.md"
    external.write_bytes(b"EXTERNAL SENTINEL")
    (workspace / "AGENTS.md").unlink()
    (workspace / "AGENTS.md").symlink_to(external)
    (workspace / "CLAUDE.md").unlink()
    (workspace / "CLAUDE.md").write_bytes(b"LEGACY CLAUDE")
    (workspace / "CLAUDE.md").chmod(0o640)
    response = TestClient(app).put(URL, headers=auth_headers, json={"system_prompt": "NEW\n", "expected_revision": revision})
    assert response.status_code == 200, response.text
    assert external.read_bytes() == b"EXTERNAL SENTINEL"
    assert not (workspace / "AGENTS.md").is_symlink()
    assert "NEW" in (workspace / "AGENTS.md").read_text()
    assert os.readlink(workspace / "CLAUDE.md") == "AGENTS.md"
    backups = list(workspace.glob("CLAUDE.md.happyranch-*.bak"))
    assert len(backups) == 1 and backups[0].read_bytes() == b"LEGACY CLAUDE"
    assert backups[0].stat().st_mode & 0o7777 == 0o640


def test_prompt_unsafe_pair_refuses_before_canonical_write(app, org_state, auth_headers) -> None:
    """C20: unsafe directory remains byte-exact, no success audit or definition change."""
    _, revision, original = _seed(org_state)
    workspace = _paths(org_state).workspaces_dir / "dev_agent"
    (workspace / "AGENTS.md").unlink()
    (workspace / "AGENTS.md").mkdir()
    sentinel = workspace / "AGENTS.md" / "sentinel"
    sentinel.write_bytes(b"preserve")
    response = TestClient(app).put(URL, headers=auth_headers, json={"system_prompt": "NEW\n", "expected_revision": revision})
    assert response.status_code == 400, response.text
    assert response.json()["detail"]["compensation"] == {"canonical": "not_required", "workspace": "not_required"}
    assert (_paths(org_state).agents_dir / "dev_agent.md").read_bytes() == original
    assert sentinel.read_bytes() == b"preserve"
    assert os.readlink(workspace / "CLAUDE.md") == "AGENTS.md"
    assert not org_state.db.get_audit_logs("founder")


@pytest.mark.parametrize("refusal", ["missing", "pending", "terminated", "unknown-org", "no-token", "bad-token"])
def test_prompt_identity_and_auth_refusals_have_no_alpha_effect(app, org_state, auth_headers, refusal: str) -> None:
    """C08: real existing OrgDep/token boundaries and active-only prompt lookup."""
    existing, revision, _ = _seed(org_state)
    before = _state(org_state)
    url, headers, expected = URL, auth_headers, 404
    if refusal in {"missing", "pending", "terminated"}:
        url = URL.replace("dev_agent", "unavailable")
        if refusal == "pending":
            prompt_loader.write_pending_agent(_paths(org_state), replace(existing, name="unavailable"))
        elif refusal == "terminated":
            archive = _paths(org_state).agents_dir / "_terminated"
            archive.mkdir()
            (archive / "unavailable.md").write_text(render_agent_text(replace(existing, name="unavailable")))
    elif refusal == "unknown-org":
        url = URL.replace("/alpha/", "/unknown/")
    else:
        headers = {} if refusal == "no-token" else {"Authorization": "Bearer deliberately-invalid"}
        expected = 401
    response = TestClient(app).put(url, headers=headers, json={"system_prompt": "NEW\n", "expected_revision": revision})
    assert response.status_code == expected, response.text
    if refusal in {"missing", "pending", "terminated"}:
        assert response.json()["detail"]["code"] == "agent_not_found"
    assert _state(org_state) == before


@pytest.mark.parametrize("route,phase", [
    ("init", "bootstrap"), ("init", "clone"), ("init", "readiness"),
    ("repo", "bootstrap"), ("repo", "clone"), ("executor", "materialize"),
    ("create", "bootstrap"), ("approve", "bootstrap"),
    ("model-set", "acquisition"), ("model-clear", "acquisition"),
])
def test_prompt_save_queues_through_sibling_bootstrap(org_state, monkeypatch, prompt_profile, route: str, phase: str) -> None:
    """C13/C14/C20: release bootstrap/gate before awaiting the queued save."""
    from runtime.daemon.routes import agents as routes

    existing, _, _ = _seed(org_state)
    existing = replace(existing, executor=prompt_profile)
    destination = "codex" if prompt_profile == "pi" else "pi"
    (_paths(org_state).agents_dir / "dev_agent.md").write_text(render_agent_text(existing))
    _authority_generation(org_state)
    target = "dev_agent"
    if route in {"create", "approve"}:
        target = f"prompt_{route}"
    if route == "approve":
        prompt_loader.write_pending_agent(_paths(org_state), replace(existing, name=target, repos={}))
        org_state.teams.add_worker("engineering", target)
    real_to_thread = asyncio.to_thread
    entered, release, attempted = asyncio.Event(), asyncio.Event(), asyncio.Event()
    real_interval = routes._consumer_writer_interval

    @asynccontextmanager
    async def observe_interval(org, **kwargs):
        if kwargs["publisher"] == "set_agent_system_prompt":
            attempted.set()
        async with real_interval(org, **kwargs) as interval:
            if route.startswith("model-") and kwargs["publisher"] == "set_agent_model":
                entered.set()
                await asyncio.wait_for(release.wait(), 10)
            yield interval

    async def hold_bootstrap(func, *args, **kwargs):
        if ((route == "executor" and func is routes._executor_switch_materialize)
            or (phase == "bootstrap" and getattr(func, "__name__", "") == "ensure_workspace_ready")
            or (phase == "clone" and getattr(func, "__name__", "") == "clone_repo")
            or (phase == "readiness" and func is routes._bootstrap_readiness_marker)):
            entered.set()
            await asyncio.wait_for(release.wait(), 10)
        return await real_to_thread(func, *args, **kwargs)

    monkeypatch.setattr(routes, "_consumer_writer_interval", observe_interval)
    monkeypatch.setattr(routes.asyncio, "to_thread", hold_bootstrap)
    def clone_repo(*args):
        return True
    monkeypatch.setattr(ContextBuilder, "clone_repo", clone_repo)

    async def exercise():
        async def initialize():
            response = await routes.init_agents("alpha", routes.InitBody(agent=target), org_state)
            events = [event async for event in response.body_iterator]
            assert '"phase": "all_done"' in events[-1]["data"], events

        if route == "init":
            operation = initialize()
        elif route == "executor":
            operation = routes.set_agent_executor("alpha", target, routes.SetExecutorBody(executor=destination), org_state)
        elif route == "repo":
            operation = routes.manage_repo("alpha", target, routes.ManageRepoBody(
                action="add", repo_name="extra", url="https://example.test/extra.git",
            ), org_state)
        elif route == "approve":
            operation = routes.approve_agent("alpha", target, org_state)
        elif route.startswith("model-"):
            operation = routes.set_agent_model("alpha", target, routes.SetModelBody(
                model="changed-model" if route == "model-set" else None,
            ), org_state)
        else:
            operation = routes.founder_create_agent("alpha", routes.FounderCreateAgentBody(
                name=target, role="worker", team="engineering", executor=prompt_profile,
                description="created", system_prompt="OLD\n",
            ), org_state)
        sibling = asyncio.create_task(operation)
        save = None
        try:
            await asyncio.wait_for(entered.wait(), 10)
            loaded = prompt_loader.load_agent_snapshot(_paths(org_state), target)
            assert loaded is not None  # active publication precedes save
            save = asyncio.create_task(routes.set_agent_system_prompt("alpha", target, routes.SystemPromptBody(
                system_prompt="WINNER\n", expected_revision=loaded[1],
            ), org_state))
            await asyncio.wait_for(attempted.wait(), 10)
            assert org_state.workflow_authority._async_writer_lock.locked()
            assert not save.done()
            assert prompt_loader.load_agent_snapshot(_paths(org_state), target)[2] == loaded[2]
            assert not org_state.teams_lock.locked()
        finally:
            release.set()
            await asyncio.wait_for(sibling, 10)
            if save is not None:
                if route == "executor" or route.startswith("model-"):
                    from fastapi import HTTPException
                    with pytest.raises(HTTPException) as conflict:
                        await asyncio.wait_for(save, 10)
                    assert conflict.value.status_code == 409
                    assert conflict.value.detail["code"] == "stale_agent_revision"
                    fresh = prompt_loader.load_agent_snapshot(_paths(org_state), target)
                    assert fresh is not None
                    # Explicit reapply after inspecting the unrelated executor
                    # winner. This is a separate save, never a blind PUT retry.
                    await routes.set_agent_system_prompt("alpha", target, routes.SystemPromptBody(
                        system_prompt="WINNER\n", expected_revision=fresh[1],
                    ), org_state)
                else:
                    await asyncio.wait_for(save, 10)
        final, digest, contents = prompt_loader.load_agent_snapshot(_paths(org_state), target)
        assert final.system_prompt == "WINNER\n"
        assert digest == hashlib.sha256(contents).hexdigest()
        workspace = _paths(org_state).workspaces_dir / target
        assert "WINNER" in (workspace / "AGENTS.md").read_text()
        assert "OLD" not in (workspace / "AGENTS.md").read_text()
        assert os.readlink(workspace / "CLAUDE.md") == "AGENTS.md"
        if route == "executor":
            assert final.executor == destination and final.model is None
        elif route.startswith("model-"):
            assert final.model == ("changed-model" if route == "model-set" else None)

    asyncio.run(exercise())
    _record_next_task_delivery(org_state, monkeypatch, target, "WINNER\n")


def test_prompt_refresh_serializes_manager_without_durable_leases(org_state, monkeypatch):
    """C13: actual prompt refresh owns only the process gate during await."""
    from fastapi import HTTPException
    from runtime.daemon.routes import agents as routes
    from tests.daemon.test_routes_agents import _activate_eh_session, _EH_TASK, _EH_SESSION

    _activate_eh_session(org_state)
    _, revision, _ = _seed(org_state)
    generation = _authority_generation(org_state)
    entered, release, attempted = asyncio.Event(), asyncio.Event(), asyncio.Event()
    real_thread, real_interval = asyncio.to_thread, routes._consumer_writer_interval

    async def held_writer(func, *args, **kwargs):
        if getattr(func, "__name__", "") == "write_claude_md":
            entered.set()
            await asyncio.wait_for(release.wait(), 10)
        return await real_thread(func, *args, **kwargs)

    @asynccontextmanager
    async def observed_interval(org, **kwargs):
        if kwargs["publisher"] == "manage_agent_update":
            attempted.set()
        async with real_interval(org, **kwargs) as interval:
            yield interval

    monkeypatch.setattr(routes.asyncio, "to_thread", held_writer)
    monkeypatch.setattr(routes, "_consumer_writer_interval", observed_interval)

    async def exercise():
        writer = asyncio.create_task(routes.set_agent_system_prompt("alpha", "dev_agent", routes.SystemPromptBody(
            system_prompt=MARKDOWN, expected_revision=revision,
        ), org_state))
        manager = None
        try:
            await asyncio.wait_for(entered.wait(), 10)
            assert not org_state.teams_lock.locked()
            async with org_state.teams_lock:
                assert prompt_loader.load_agent(_paths(org_state), "dev_agent").system_prompt == MARKDOWN
            assert not org_state.db._conn.in_transaction
            assert org_state.db.execute("SELECT COUNT(*) FROM workflow_publication_leases").fetchone()[0] == 0
            assert org_state.db.execute("SELECT COUNT(*) FROM workflow_profile_leases").fetchone()[0] == 0
            coordinator = org_state._profile_coordinator
            with coordinator.profile_read("claude"):
                assert org_state.db.execute("SELECT COUNT(*) FROM workflow_publication_leases").fetchone()[0] == 0
            owner = "prompt-test-independent-lease"
            org_state.workflow_authority._acquire_lease(owner)
            org_state.workflow_authority._release_lease(owner)
            pointer = org_state.db.execute(
                "SELECT state FROM workflow_authority_pointers WHERE namespace=?",
                (org_state.workflow_authority.namespace,),
            ).fetchone()
            assert pointer["state"] == "fenced"
            manager = asyncio.create_task(routes.manage_agent("alpha", routes.ManageAgentBody(
                action="update", name="dev_agent", task_id=_EH_TASK, session_id=_EH_SESSION,
                system_prompt="MANAGER LOSER\n", expected_revision=revision,
            ), org_state))
            await asyncio.wait_for(attempted.wait(), 10)
            assert not manager.done()
            assert prompt_loader.load_agent(_paths(org_state), "dev_agent").system_prompt == MARKDOWN
        finally:
            release.set()
            receipt = await asyncio.wait_for(writer, 10)
            if manager is not None:
                with pytest.raises(HTTPException) as stale:
                    await asyncio.wait_for(manager, 10)
                assert stale.value.status_code == 409
        final = prompt_loader.load_agent_snapshot(_paths(org_state), "dev_agent")
        assert final[0].system_prompt == MARKDOWN
        assert final[1] == receipt.revision == hashlib.sha256(final[2]).hexdigest()

    asyncio.run(exercise())
    assert _authority_generation(org_state) == generation + 1
    assert [r for r in org_state.db.get_audit_logs(_EH_TASK) if r["action"] == "agent_managed"] == []
    _record_next_task_delivery(org_state, monkeypatch, "dev_agent", MARKDOWN)


def test_prompt_publication_failure_keeps_receipt_and_fences_readiness(app, org_state, auth_headers, monkeypatch):
    """C13: canonical success does not assert workflow admission readiness."""
    from runtime.workflows.authority import WorkflowAuthorityError

    _, revision, _ = _seed(org_state)
    def failed_publication(**kwargs):
        raise RuntimeError("injected prompt publication failure")
    monkeypatch.setattr(org_state.workflow_authority, "publish_current", failed_publication)
    response = TestClient(app).put(URL, headers=auth_headers, json={
        "system_prompt": MARKDOWN, "expected_revision": revision,
    })
    assert response.status_code == 200, response.text
    final = prompt_loader.load_agent_snapshot(_paths(org_state), "dev_agent")
    assert final[0].system_prompt == response.json()["system_prompt"] == MARKDOWN
    assert final[1] == response.json()["revision"] == hashlib.sha256(final[2]).hexdigest()
    pair = _state(org_state)[1]
    assert MARKDOWN.strip().encode() in pair[0][1]
    assert pair[1][1] == "AGENTS.md"
    with pytest.raises(WorkflowAuthorityError, match="authority_pointer_not_ready"):
        org_state.workflow_authority.verify_admission_ready()


@pytest.mark.parametrize("ownership", ["owned", "newer-pair", "pair-restore-failure"])
def test_prompt_route_pair_replay_requires_owned_fingerprint(app, org_state, auth_headers, monkeypatch, ownership):
    """C12: receipt failure after pair write independently exercises route replay."""
    from runtime.orchestrator import workspace_adapters as adapters

    _, revision, _ = _seed(org_state)
    before = _state(org_state)
    workspace = _paths(org_state).workspaces_dir / "dev_agent"
    real_snapshot = prompt_loader.load_agent_snapshot
    real_restore = adapters._restore_instruction_path
    written, refused, newer = False, False, None
    real_writer = ContextBuilder.write_claude_md

    def observe_write(*args, **kwargs):
        nonlocal written
        result = real_writer(*args, **kwargs)
        written = True
        return result

    def failed_receipt(paths, name):
        nonlocal refused, newer
        if written and not refused:
            refused = True
            assert "NEW" in (workspace / "AGENTS.md").read_text()
            if ownership == "newer-pair":
                (workspace / "AGENTS.md").write_bytes(b"DETECTED NEWER PAIR\n")
                (workspace / "AGENTS.md").chmod(0o600)
                (workspace / "CLAUDE.md").unlink()
                (workspace / "CLAUDE.md").symlink_to("newer-target.md")
                (workspace / "newer-target.md").write_bytes(b"UNTOUCHED LINK TARGET\n")
                newer = _state(org_state)[1]
            raise RuntimeError("injected same-read receipt failure")
        return real_snapshot(paths, name)

    def fail_restore(path, state):
        if ownership == "pair-restore-failure" and path.name == "AGENTS.md":
            raise OSError("injected pair path restore failure")
        return real_restore(path, state)

    monkeypatch.setattr(prompt_loader, "load_agent_snapshot", failed_receipt)
    monkeypatch.setattr(ContextBuilder, "write_claude_md", observe_write)
    monkeypatch.setattr(adapters, "_restore_instruction_path", fail_restore)
    response = TestClient(app).put(URL, headers=auth_headers, json={
        "system_prompt": "NEW\n", "expected_revision": revision,
    })
    assert response.status_code == 400, response.text
    compensation = response.json()["detail"]["compensation"]
    assert compensation["canonical"] == "restored"
    assert _state(org_state)[0] == before[0]
    assert _state(org_state)[2] == before[2]
    if ownership == "owned":
        assert compensation["workspace"] == "restored"
        assert _state(org_state) == before
    elif ownership == "newer-pair":
        assert compensation["workspace"] == "not_owned"
        assert _state(org_state)[1] == newer
        assert (workspace / "newer-target.md").read_bytes() == b"UNTOUCHED LINK TARGET\n"
    else:
        assert compensation["workspace"] == "failed"
        assert "NEW" in (workspace / "AGENTS.md").read_text()
        assert prompt_loader.load_agent(_paths(org_state), "dev_agent").system_prompt == "OLD\n"


def test_prompt_external_mid_pair_failure_reports_actual_inner_restore(app, org_state, auth_headers, monkeypatch):
    """C12: captured inner rollback is not a global external-writer fence."""
    from runtime.orchestrator import workspace_adapters as adapters

    _, revision, _ = _seed(org_state)
    workspace = _paths(org_state).workspaces_dir / "dev_agent"
    (workspace / "CLAUDE.md").unlink()
    (workspace / "CLAUDE.md").write_bytes(b"LEGACY RAW CLAUDE\n")
    (workspace / "CLAUDE.md").chmod(0o600)
    before = _state(org_state)
    observed = []
    def fail_link(path):
        assert "NEW" in (workspace / "AGENTS.md").read_text()
        (workspace / "AGENTS.md").write_bytes(b"EXTERNAL MID PAIR\n")
        observed.append((workspace / "AGENTS.md").read_bytes())
        raise OSError("injected canonical CLAUDE link failure")
    monkeypatch.setattr(adapters, "_replace_with_canonical_claude_link", fail_link)
    response = TestClient(app).put(URL, headers=auth_headers, json={
        "system_prompt": "NEW\n", "expected_revision": revision,
    })
    assert response.status_code == 400, response.text
    assert observed == [b"EXTERNAL MID PAIR\n"]
    assert response.json()["detail"]["compensation"] == {"canonical": "restored", "workspace": "restored"}
    assert "link" in response.json()["detail"]["error"].lower()
    assert _state(org_state) == before


def test_prompt_same_name_org_cas_and_legitimate_beta_write_are_isolated(app, daemon_state, org_state, auth_headers):
    """C08: real OrgDep resolves beta; an alpha hash cannot authorize beta."""
    _seed(org_state)
    paths = _paths(org_state)
    beta_root = daemon_state.runtime.orgs_dir / "beta"
    (beta_root / "org" / "agents").mkdir(parents=True)
    (beta_root / "org" / "teams.yaml").write_bytes(paths.teams_config_path.read_bytes())
    for file in paths.agents_dir.glob("*.md"):
        (beta_root / "org" / "agents" / file.name).write_bytes(file.read_bytes())
    beta = asyncio.run(daemon_state.add_org("beta"))
    beta_agent, _, _ = _seed(beta)
    (_paths(beta).agents_dir / "dev_agent.md").write_text(
        render_agent_text(replace(beta_agent, system_prompt="BETA OLD\n")),
    )
    _authority_generation(beta)
    alpha_before, beta_before = _state(org_state), _state(beta)
    alpha_hash = prompt_loader.agent_revision(paths, "dev_agent")
    beta_hash = prompt_loader.agent_revision(_paths(beta), "dev_agent")
    assert beta_hash != alpha_hash
    client = TestClient(app)
    url = URL.replace("/alpha/", "/beta/")
    rejected = client.put(url, headers=auth_headers, json={
        "system_prompt": "CROSS ORG LOSER\n", "expected_revision": alpha_hash,
    })
    assert rejected.status_code == 409
    assert rejected.json()["detail"] == {"code": "stale_agent_revision", "current_revision": beta_hash}
    assert _state(org_state) == alpha_before and _state(beta) == beta_before
    saved = client.put(url, headers=auth_headers, json={
        "system_prompt": "BETA NEW\n", "expected_revision": beta_hash,
    })
    assert saved.status_code == 200, saved.text
    beta_final = prompt_loader.load_agent_snapshot(_paths(beta), "dev_agent")
    assert beta_final[0].system_prompt == saved.json()["system_prompt"] == "BETA NEW\n"
    assert beta_final[1] == saved.json()["revision"] == hashlib.sha256(beta_final[2]).hexdigest()
    assert _state(org_state) == alpha_before
    assert b"BETA NEW" in _state(beta)[1][0][1]
    assert len(_state(beta)[2]) == len(beta_before[2]) + 1
    (_paths(beta).agents_dir / "dev_agent.md").unlink()
    deleted = client.put(url, headers=auth_headers, json={
        "system_prompt": "DO NOT RESURRECT\n", "expected_revision": beta_final[1],
    })
    assert deleted.status_code == 404 and deleted.json()["detail"]["code"] == "agent_not_found"
    assert not (_paths(beta).agents_dir / "dev_agent.md").exists()
    assert _state(org_state) == alpha_before


@pytest.mark.parametrize("name", ["bad%20name", "%2e%2e%2fdev_agent", "%2e%2e%2f%2e%2e%2fdev_agent"])
def test_prompt_invalid_encoded_name_cannot_escape_org(app, org_state, auth_headers, name):
    """C08: measure the normal router/loader refusal, without new policy."""
    _, revision, _ = _seed(org_state)
    before = _state(org_state)
    response = TestClient(app).put(URL.replace("dev_agent", name), headers=auth_headers, json={
        "system_prompt": "INVALID TARGET\n", "expected_revision": revision,
    })
    assert response.status_code == 404, response.text
    assert _state(org_state) == before


def test_prompt_registration_token_and_idle_runtime_refuse_without_write(app, app_idle, daemon_state, org_state, auth_headers):
    """C08: genuine fixture registration token cannot edit founder content."""
    _, revision, _ = _seed(org_state)
    before = _state(org_state)
    token, _ = daemon_state.registration_token_store.mint("alpha", "prompt-fixture")
    rejected = TestClient(app).put(URL, headers={"Authorization": f"Bearer {token}"}, json={
        "system_prompt": "REGISTRATION LOSER\n", "expected_revision": revision,
    })
    assert rejected.status_code == 401
    idle = TestClient(app_idle).put(URL, headers=auth_headers, json={
        "system_prompt": "IDLE LOSER\n", "expected_revision": revision,
    })
    assert idle.status_code == 409 and idle.json()["detail"]["code"] == "no_active_runtime"
    assert _state(org_state) == before
