from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from runtime.config import Settings
from runtime.daemon import paths as paths_mod
from runtime.daemon.assistant_pty import ProbeResult
from runtime.daemon.app import create_app
from runtime.daemon.state import DaemonState
from runtime.system_assistant import (
    AssistantConfig,
    AssistantState,
    load_assistant_config,
    save_assistant_config,
    system_assistant_paths,
)


@pytest.fixture
def auth(auth_headers: dict[str, str]) -> dict[str, str]:
    return auth_headers


def _idle_client(auth: dict[str, str]) -> TestClient:
    client = TestClient(create_app(DaemonState.idle(Settings())))
    client.headers.update(auth)
    return client


def _passed_probe_result(executor: str = "codex") -> dict[str, Any]:
    return {
        "passed": True,
        "executor": executor,
        "command": f"/usr/local/bin/{executor}",
        "argv": [f"/usr/local/bin/{executor}"],
        "name": executor,
        "prompt_surface": "CLAUDE.md" if executor == "claude" else "AGENTS.md",
        "output_excerpt": "ready",
        "detail": "ready marker observed",
        "elapsed_seconds": 0.01,
        "timed_out": False,
        "error": None,
        "returncode": 0,
    }


def test_assistant_status_no_active_runtime(tmp_home: Path, auth: dict[str, str]) -> None:
    client = _idle_client(auth)

    response = client.get("/api/v1/assistant/status")

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "no_active_runtime"


def test_assistant_status_uninitialized(client: TestClient) -> None:
    response = client.get("/api/v1/assistant/status")

    assert response.status_code == 200, response.text
    assert response.json() == {
        "state": AssistantState.UNINITIALIZED,
        "selected_executor": None,
        "workspace_path": None,
        "detail": None,
        "latest_probe_results": [],
    }


def test_assistant_probes_returns_fake_probe_results(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from runtime.daemon.routes import assistant as assistant_route

    @dataclass(frozen=True)
    class FakeSpec:
        name: str
        argv: list[str]
        prompt_surface: str

    class FakeProbeRunner:
        def probe_executor(self, spec: FakeSpec) -> ProbeResult:
            return ProbeResult(
                passed=spec.name == "codex",
                executor=spec.name,
                output_excerpt=f"{spec.name} output",
                detail="fake result",
                elapsed_seconds=0.25,
                timed_out=False,
                error=None,
                returncode=0,
            )

    monkeypatch.setattr(
        assistant_route,
        "build_executor_specs",
        lambda _settings: [
            FakeSpec(name="claude", argv=["/bin/claude"], prompt_surface="CLAUDE.md"),
            FakeSpec(name="codex", argv=["/bin/codex"], prompt_surface="AGENTS.md"),
        ],
    )
    monkeypatch.setattr(assistant_route, "ProbeRunner", FakeProbeRunner)

    response = client.post("/api/v1/assistant/probes")

    assert response.status_code == 200, response.text
    assert response.json() == {
        "probe_results": [
            {
                "passed": False,
                "executor": "claude",
                "command": "/bin/claude",
                "argv": ["/bin/claude"],
                "name": "claude",
                "prompt_surface": "CLAUDE.md",
                "output_excerpt": "claude output",
                "detail": "fake result",
                "elapsed_seconds": 0.25,
                "timed_out": False,
                "error": None,
                "returncode": 0,
            },
            {
                "passed": True,
                "executor": "codex",
                "command": "/bin/codex",
                "argv": ["/bin/codex"],
                "name": "codex",
                "prompt_surface": "AGENTS.md",
                "output_excerpt": "codex output",
                "detail": "fake result",
                "elapsed_seconds": 0.25,
                "timed_out": False,
                "error": None,
                "returncode": 0,
            },
        ]
    }


def test_assistant_configure_requires_passing_probe(client: TestClient) -> None:
    response = client.post(
        "/api/v1/assistant/configure",
        json={
            "selected_executor": "codex",
            "probe_results": [
                {
                    **_passed_probe_result("codex"),
                    "passed": False,
                    "detail": "not ready",
                }
            ],
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "selected_executor_not_probe_passed"


def test_assistant_configure_rejects_unsupported_executor_without_workspace(
    client: TestClient,
    runtime,
) -> None:
    paths = system_assistant_paths(runtime.root)

    response = client.post(
        "/api/v1/assistant/configure",
        json={
            "selected_executor": "not-real",
            "probe_results": [_passed_probe_result("not-real")],
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "unsupported_assistant_executor"
    assert not paths.root.exists()


def test_assistant_configure_rejects_extra_probe_fields(
    client: TestClient,
    runtime,
) -> None:
    paths = system_assistant_paths(runtime.root)
    probe_result = {
        **_passed_probe_result("codex"),
        "unexpected": "must not persist",
    }

    response = client.post(
        "/api/v1/assistant/configure",
        json={"selected_executor": "codex", "probe_results": [probe_result]},
    )

    assert response.status_code == 422
    assert not paths.config_path.exists()


def test_assistant_configure_rejects_unknown_probe_executor(
    client: TestClient,
    runtime,
) -> None:
    paths = system_assistant_paths(runtime.root)

    response = client.post(
        "/api/v1/assistant/configure",
        json={
            "selected_executor": "codex",
            "probe_results": [
                _passed_probe_result("codex"),
                _passed_probe_result("not-real"),
            ],
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "unknown_probe_executor"
    assert not paths.config_path.exists()


def test_assistant_configure_rejects_contradictory_passed_probe(
    client: TestClient,
    runtime,
) -> None:
    paths = system_assistant_paths(runtime.root)

    response = client.post(
        "/api/v1/assistant/configure",
        json={
            "selected_executor": "codex",
            "probe_results": [
                {
                    **_passed_probe_result("codex"),
                    "timed_out": True,
                }
            ],
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "invalid_probe_result"
    assert not paths.config_path.exists()


def test_assistant_configure_writes_workspace_and_status(
    client: TestClient,
    runtime,
) -> None:
    probe_result = _passed_probe_result("codex")
    expected_probe_result = {
        **probe_result,
        "command": "codex",
        "argv": ["codex"],
    }

    response = client.post(
        "/api/v1/assistant/configure",
        json={"selected_executor": "codex", "probe_results": [probe_result]},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    paths = system_assistant_paths(runtime.root)
    assert body["state"] == AssistantState.CONFIGURED
    assert body["selected_executor"] == "codex"
    assert body["workspace_path"] == str(paths.workspace)
    assert body["latest_probe_results"] == [expected_probe_result]
    assert (paths.workspace / "agent.yaml").is_file()
    assert (paths.workspace / "AGENTS.md").is_file()
    assert (paths.learnings_dir / "_index.md").is_file()

    config = load_assistant_config(runtime.root)
    assert config is not None
    assert config.selected_executor == "codex"
    assert config.selected_command == "codex"
    assert config.workspace_path == str(paths.workspace)
    assert config.latest_probe_results == [expected_probe_result]


def test_assistant_configure_derives_command_from_server_specs(
    runtime,
    auth: dict[str, str],
) -> None:
    state = DaemonState.from_runtime(
        runtime,
        Settings(codex_cli_path="/server/bin/codex"),
    )
    client = TestClient(create_app(state))
    client.headers.update(auth)
    probe_result = {
        **_passed_probe_result("codex"),
        "command": "/tmp/not-probed",
        "argv": ["/tmp/not-probed"],
        "name": "forged-name",
        "prompt_surface": "CLAUDE.md",
    }
    expected_probe_result = {
        **probe_result,
        "command": "/server/bin/codex",
        "argv": ["/server/bin/codex"],
        "name": "codex",
        "prompt_surface": "AGENTS.md",
    }

    response = client.post(
        "/api/v1/assistant/configure",
        json={"selected_executor": "codex", "probe_results": [probe_result]},
    )

    assert response.status_code == 200, response.text
    config = load_assistant_config(runtime.root)
    assert config is not None
    assert config.selected_command == "/server/bin/codex"
    assert config.latest_probe_results == [expected_probe_result]


def test_assistant_websocket_streams_to_selected_cli(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from runtime.daemon.routes import assistant as assistant_route

    fake_cli = tmp_path / "fake-assistant"
    fake_cli.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "print('assistant ready', flush=True)\n"
        "for line in sys.stdin:\n"
        "    print('echo: ' + line.strip(), flush=True)\n"
    )
    fake_cli.chmod(0o755)

    @dataclass(frozen=True)
    class FakeSpec:
        name: str
        argv: list[str]
        prompt_surface: str

    monkeypatch.setattr(
        assistant_route,
        "build_executor_specs",
        lambda _settings: [
            FakeSpec(name="codex", argv=[str(fake_cli)], prompt_surface="AGENTS.md"),
        ],
    )
    response = client.post(
        "/api/v1/assistant/configure",
        json={
            "selected_executor": "codex",
            "probe_results": [_passed_probe_result("codex")],
        },
    )
    assert response.status_code == 200, response.text

    try:
        token = paths_mod.read_token()
        with client.websocket_connect(
            f"/api/v1/assistant/session?token={token}"
        ) as websocket:
            assert websocket.receive_text().strip() == "assistant ready"

            websocket.send_text("hello from websocket\n")

            assert "echo: hello from websocket" in websocket.receive_text()
    finally:
        asyncio.run(client.app.state.daemon.assistant_sessions.close_all())


def test_assistant_websocket_rejects_bad_token_without_starting_session(
    client: TestClient,
    runtime,
) -> None:
    paths = system_assistant_paths(runtime.root)
    paths.root.mkdir(parents=True)
    paths.workspace.mkdir(parents=True)
    (paths.workspace / "agent.yaml").write_text("name: system_assistant\n")
    (paths.workspace / "AGENTS.md").write_text("# Assistant\n")
    (paths.learnings_dir).mkdir(parents=True)
    (paths.learnings_dir / "_index.md").write_text("# Learnings\n")
    save_assistant_config(
        runtime.root,
        AssistantConfig(
            selected_executor="codex",
            selected_command="/should/not/start",
            workspace_path=str(paths.workspace),
            latest_probe_results=[],
        ),
    )

    class NoStartSessions:
        async def get_or_start(self, **_kwargs: Any) -> None:
            raise AssertionError("unauthorized websocket started assistant session")

    client.app.state.daemon.assistant_sessions = NoStartSessions()

    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect("/api/v1/assistant/session?token=bad-token"):
            pass

    assert exc_info.value.code == 1008


def test_assistant_websocket_uninitialized_sends_hint_without_starting_session(
    client: TestClient,
) -> None:
    class NoStartSessions:
        async def get_or_start(self, **_kwargs: Any) -> None:
            raise AssertionError("uninitialized assistant started session")

    client.app.state.daemon.assistant_sessions = NoStartSessions()
    token = paths_mod.read_token()

    with client.websocket_connect(f"/api/v1/assistant/session?token={token}") as websocket:
        assert websocket.receive_text() == (
            "assistant_init_required: configure the system assistant before attaching"
        )
        with pytest.raises(WebSocketDisconnect) as exc_info:
            websocket.receive_text()

    assert exc_info.value.code == 1000


def test_assistant_repair_refreshes_workspace(client: TestClient, runtime) -> None:
    paths = system_assistant_paths(runtime.root)
    probe_result = _passed_probe_result("claude")
    paths.root.mkdir(parents=True)
    save_assistant_config(
        runtime.root,
        AssistantConfig(
            selected_executor="claude",
            selected_command="/usr/local/bin/claude",
            workspace_path=str(paths.workspace),
            latest_probe_results=[probe_result],
        ),
    )

    response = client.post("/api/v1/assistant/repair")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["state"] == AssistantState.CONFIGURED
    assert body["selected_executor"] == "claude"
    assert body["workspace_path"] == str(paths.workspace)
    assert body["latest_probe_results"] == [probe_result]
    assert (paths.workspace / "agent.yaml").is_file()
    assert (paths.workspace / "CLAUDE.md").is_file()
    assert (paths.learnings_dir / "_index.md").is_file()


def test_assistant_repair_invalid_config_returns_conflict(
    client: TestClient,
    runtime,
) -> None:
    paths = system_assistant_paths(runtime.root)
    paths.root.mkdir(parents=True)
    paths.config_path.write_text("{invalid json")

    response = client.post("/api/v1/assistant/repair")

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "assistant_config_invalid"


def test_assistant_repair_invalid_config_schema_returns_conflict(
    client: TestClient,
    runtime,
) -> None:
    paths = system_assistant_paths(runtime.root)
    paths.root.mkdir(parents=True)
    paths.config_path.write_text('{"selected_executor": "not-real"}\n')
    no_raise_client = TestClient(client.app, raise_server_exceptions=False)
    no_raise_client.headers.update(client.headers)

    response = no_raise_client.post("/api/v1/assistant/repair")

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "assistant_config_invalid"


def test_assistant_repair_requires_config(client: TestClient) -> None:
    response = client.post("/api/v1/assistant/repair")

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "assistant_not_configured"


@pytest.mark.parametrize(
    ("method", "path", "json"),
    [
        ("post", "/api/v1/assistant/probes", None),
        (
            "post",
            "/api/v1/assistant/configure",
            {"selected_executor": "codex", "probe_results": [_passed_probe_result()]},
        ),
        ("post", "/api/v1/assistant/repair", None),
    ],
)
def test_assistant_mutations_require_active_runtime(
    tmp_home: Path,
    auth: dict[str, str],
    method: str,
    path: str,
    json: dict[str, Any] | None,
) -> None:
    client = _idle_client(auth)

    response = client.request(method, path, json=json)

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "no_active_runtime"
