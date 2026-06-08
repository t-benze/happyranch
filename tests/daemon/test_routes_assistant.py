from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from runtime.config import Settings
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


def test_assistant_configure_writes_workspace_and_status(
    client: TestClient,
    runtime,
) -> None:
    probe_result = _passed_probe_result("codex")

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
    assert body["latest_probe_results"] == [probe_result]
    assert (paths.workspace / "agent.yaml").is_file()
    assert (paths.workspace / "AGENTS.md").is_file()
    assert (paths.learnings_dir / "_index.md").is_file()

    config = load_assistant_config(runtime.root)
    assert config is not None
    assert config.selected_executor == "codex"
    assert config.selected_command == "/usr/local/bin/codex"
    assert config.workspace_path == str(paths.workspace)
    assert config.latest_probe_results == [probe_result]


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
