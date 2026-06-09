from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
import shutil
import sys
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


def _patch_probe_runner(
    monkeypatch: pytest.MonkeyPatch,
    *,
    passed: bool = True,
    detail: str = "ready marker observed",
    error: str | None = None,
    returncode: int | None = None,
) -> None:
    from runtime.daemon.routes import assistant as assistant_route

    class FakeProbeRunner:
        def probe_executor(self, spec: Any, *, timeout_seconds: float = 3) -> ProbeResult:
            return ProbeResult(
                passed=passed,
                executor=spec.name,
                output_excerpt=f"{spec.name} daemon probe",
                detail=detail,
                elapsed_seconds=0.02,
                timed_out=False,
                error=error,
                returncode=returncode,
            )

    monkeypatch.setattr(assistant_route, "ProbeRunner", FakeProbeRunner)


def _daemon_probe_result(executor: str, argv: list[str]) -> dict[str, Any]:
    return {
        **_passed_probe_result(executor),
        "command": argv[0],
        "argv": argv,
        "output_excerpt": f"{executor} daemon probe",
        "elapsed_seconds": 0.02,
        "returncode": None,
    }


def _expected_resolved_argv(argv: list[str]) -> list[str]:
    if not argv:
        return []
    executable = shutil.which(argv[0])
    return [executable, *argv[1:]] if executable is not None else argv


class _CloseTrackingSessions:
    def __init__(self, *, lock: asyncio.Lock) -> None:
        self.lock = lock
        self.close_calls = 0

    async def close_all(self) -> None:
        assert self.lock.locked()
        self.close_calls += 1


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


def test_assistant_status_requires_http_auth(client: TestClient) -> None:
    no_auth = TestClient(client.app)

    response = no_auth.get("/api/v1/assistant/status")

    assert response.status_code == 401


def test_parse_resize_control_frame() -> None:
    from runtime.daemon.routes.assistant import _parse_resize_control

    assert _parse_resize_control("__HAPPYRANCH_ASSISTANT_RESIZE__ 43 132") == (
        43,
        132,
    )
    assert _parse_resize_control("__HAPPYRANCH_ASSISTANT_RESIZE__ 0 132") is None
    assert _parse_resize_control("hello") is None


def test_websocket_token_is_valid_parses_bearer_and_compares(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    from starlette.datastructures import Headers

    from runtime.daemon.routes import assistant as assistant_route

    expected = "s3cret-bearer-token"

    def _fake_ws(authorization: str | None) -> Any:
        raw = {} if authorization is None else {"authorization": authorization}
        return SimpleNamespace(headers=Headers(raw))

    monkeypatch.setattr(assistant_route.daemon_paths, "read_token", lambda: expected)

    # Valid 'Bearer <token>' header is accepted.
    assert assistant_route._websocket_token_is_valid(_fake_ws(f"Bearer {expected}")) is True
    # A non-matching token is rejected.
    assert assistant_route._websocket_token_is_valid(_fake_ws("Bearer wrong-token")) is False
    # Fail-closed: missing Authorization header.
    assert assistant_route._websocket_token_is_valid(_fake_ws(None)) is False
    # Fail-closed: header carries the token but lacks the 'Bearer ' prefix.
    assert assistant_route._websocket_token_is_valid(_fake_ws(expected)) is False

    # Fail-closed: no expected token on disk, even with a well-formed header.
    monkeypatch.setattr(assistant_route.daemon_paths, "read_token", lambda: None)
    assert assistant_route._websocket_token_is_valid(_fake_ws(f"Bearer {expected}")) is False


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
        def probe_executor(
            self,
            spec: FakeSpec,
            *,
            timeout_seconds: float = 3,
        ) -> ProbeResult:
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
                "command": _expected_resolved_argv(["/bin/codex"])[0],
                "argv": _expected_resolved_argv(["/bin/codex"]),
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


def test_assistant_probes_use_configured_timeout(
    runtime,
    auth: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from runtime.daemon.routes import assistant as assistant_route

    seen_timeouts: list[float] = []

    class FakeProbeRunner:
        def probe_executor(self, spec: Any, *, timeout_seconds: float = 3) -> ProbeResult:
            seen_timeouts.append(timeout_seconds)
            return ProbeResult(
                passed=False,
                executor=spec.name,
                output_excerpt="",
                detail="fake",
                elapsed_seconds=0.01,
                timed_out=True,
                error="timeout",
                returncode=None,
            )

    monkeypatch.setattr(assistant_route, "ProbeRunner", FakeProbeRunner)
    state = DaemonState.from_runtime(
        runtime,
        Settings(assistant_probe_timeout_seconds=42),
    )
    client = TestClient(create_app(state))
    client.headers.update(auth)

    response = client.post("/api/v1/assistant/probes")

    assert response.status_code == 200, response.text
    assert seen_timeouts == [42, 42, 42, 42]


def test_assistant_configure_requires_daemon_passing_probe(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_probe_runner(
        monkeypatch,
        passed=False,
        detail="not ready",
        error="timeout",
    )
    response = client.post(
        "/api/v1/assistant/configure",
        json={
            "selected_executor": "codex",
            "probe_results": [_passed_probe_result("codex")],
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "selected_executor_probe_failed"
    assert response.json()["detail"]["probe_result"]["passed"] is False


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
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_probe_runner(monkeypatch)
    probe_result = _passed_probe_result("codex")
    expected_argv = _expected_resolved_argv(["codex"])
    expected_probe_result = _daemon_probe_result("codex", expected_argv)

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
    assert config.selected_command == expected_argv[0]
    assert config.selected_argv == expected_argv
    assert config.workspace_path == str(paths.workspace)
    assert config.latest_probe_results == [expected_probe_result]


def test_assistant_configure_derives_command_from_server_specs(
    runtime,
    auth: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_probe_runner(monkeypatch)
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
    expected_probe_result = _daemon_probe_result("codex", ["/server/bin/codex"])

    response = client.post(
        "/api/v1/assistant/configure",
        json={"selected_executor": "codex", "probe_results": [probe_result]},
    )

    assert response.status_code == 200, response.text
    config = load_assistant_config(runtime.root)
    assert config is not None
    assert config.selected_command == "/server/bin/codex"
    assert config.selected_argv == ["/server/bin/codex"]
    assert config.latest_probe_results == [expected_probe_result]


def test_assistant_configure_closes_active_session(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_probe_runner(monkeypatch)
    sessions = _CloseTrackingSessions(
        lock=client.app.state.daemon.assistant_lifecycle_lock,
    )
    client.app.state.daemon.assistant_sessions = sessions

    response = client.post(
        "/api/v1/assistant/configure",
        json={
            "selected_executor": "codex",
            "probe_results": [_passed_probe_result("codex")],
        },
    )

    assert response.status_code == 200, response.text
    assert sessions.close_calls == 1


def test_assistant_configure_rejects_runtime_changed_during_probe(
    client: TestClient,
    runtime,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from runtime.daemon.routes import assistant as assistant_route
    from runtime.runtime import RuntimeDir

    state = client.app.state.daemon
    next_runtime = RuntimeDir.init(tmp_path / "next-runtime")

    def fake_probe_selected_executor(
        selected_executor: str,
        specs: list[Any],
        *,
        timeout_seconds: float,
    ) -> tuple[Any, dict[str, Any]]:
        del timeout_seconds
        spec = next(spec for spec in specs if spec.name == selected_executor)
        state.runtime = next_runtime
        return spec, _daemon_probe_result(selected_executor, list(spec.argv))

    monkeypatch.setattr(
        assistant_route,
        "_probe_selected_executor",
        fake_probe_selected_executor,
    )

    response = client.post(
        "/api/v1/assistant/configure",
        json={
            "selected_executor": "codex",
            "probe_results": [_passed_probe_result("codex")],
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "assistant_runtime_changed"
    assert not system_assistant_paths(runtime.root).config_path.exists()
    assert not system_assistant_paths(next_runtime.root).config_path.exists()


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
    _patch_probe_runner(monkeypatch)
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
            "/api/v1/assistant/session",
            headers={"Authorization": f"Bearer {token}"},
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
                selected_argv=["/should/not/start"],
                workspace_path=str(paths.workspace),
                latest_probe_results=[],
            ),
    )

    class NoStartSessions:
        async def get_or_start(self, **_kwargs: Any) -> None:
            raise AssertionError("unauthorized websocket started assistant session")

    client.app.state.daemon.assistant_sessions = NoStartSessions()

    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect(
            "/api/v1/assistant/session",
            headers={"Authorization": "Bearer bad-token"},
        ):
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

    with client.websocket_connect(
        "/api/v1/assistant/session",
        headers={"Authorization": f"Bearer {token}"},
    ) as websocket:
        assert websocket.receive_text() == (
            "assistant_init_required: configure the system assistant before attaching"
        )
        with pytest.raises(WebSocketDisconnect) as exc_info:
            websocket.receive_text()

    assert exc_info.value.code == 1000


def test_assistant_websocket_starts_session_under_lifecycle_lock(
    client: TestClient,
    runtime,
) -> None:
    paths = system_assistant_paths(runtime.root)
    paths.workspace.mkdir(parents=True)
    (paths.workspace / "agent.yaml").write_text("name: system_assistant\n")
    (paths.workspace / "AGENTS.md").write_text("# Assistant\n")
    paths.learnings_dir.mkdir(parents=True)
    (paths.learnings_dir / "_index.md").write_text("# Learnings\n")
    paths.knowledge_dir.mkdir(parents=True)
    (paths.knowledge_dir / "README.md").write_text("# Knowledge\n")
    paths.logs_dir.mkdir(parents=True)
    save_assistant_config(
        runtime.root,
        AssistantConfig(
            selected_executor="codex",
            selected_command=sys.executable,
            selected_argv=[sys.executable],
            workspace_path=str(paths.workspace),
            latest_probe_results=[],
        ),
    )

    class FakeSession:
        def subscribe(self) -> asyncio.Queue[str | None]:
            queue: asyncio.Queue[str | None] = asyncio.Queue()
            queue.put_nowait("fake ready")
            return queue

        def unsubscribe(self, _queue: asyncio.Queue[str | None]) -> None:
            pass

        async def write_text(self, _text: str) -> None:
            pass

        async def resize(self, *, rows: int, cols: int) -> None:
            pass

    class LockCheckingSessions:
        def __init__(self) -> None:
            self.started_under_lock = False

        async def get_or_start(self, **_kwargs: Any) -> FakeSession:
            self.started_under_lock = (
                client.app.state.daemon.assistant_lifecycle_lock.locked()
            )
            return FakeSession()

    sessions = LockCheckingSessions()
    client.app.state.daemon.assistant_sessions = sessions
    token = paths_mod.read_token()

    with client.websocket_connect(
        "/api/v1/assistant/session",
        headers={"Authorization": f"Bearer {token}"},
    ) as websocket:
        assert websocket.receive_text() == "fake ready"

    assert sessions.started_under_lock is True


def test_assistant_repair_refreshes_workspace(client: TestClient, runtime) -> None:
    paths = system_assistant_paths(runtime.root)
    probe_result = _passed_probe_result("claude")
    paths.root.mkdir(parents=True)
    save_assistant_config(
        runtime.root,
            AssistantConfig(
                selected_executor="claude",
                selected_command=sys.executable,
                selected_argv=[sys.executable],
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


def test_assistant_repair_closes_active_session(client: TestClient, runtime) -> None:
    paths = system_assistant_paths(runtime.root)
    paths.root.mkdir(parents=True)
    save_assistant_config(
        runtime.root,
            AssistantConfig(
                selected_executor="claude",
                selected_command=sys.executable,
                selected_argv=[sys.executable],
                workspace_path=str(paths.workspace),
                latest_probe_results=[_passed_probe_result("claude")],
            ),
    )
    sessions = _CloseTrackingSessions(
        lock=client.app.state.daemon.assistant_lifecycle_lock,
    )
    client.app.state.daemon.assistant_sessions = sessions

    response = client.post("/api/v1/assistant/repair")

    assert response.status_code == 200, response.text
    assert sessions.close_calls == 1


def test_assistant_repair_loads_config_under_lifecycle_lock(
    client: TestClient,
    runtime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from runtime.daemon.routes import assistant as assistant_route

    paths = system_assistant_paths(runtime.root)
    config = AssistantConfig(
        selected_executor="claude",
        selected_command=sys.executable,
        selected_argv=[sys.executable],
        workspace_path=str(paths.workspace),
        latest_probe_results=[_passed_probe_result("claude")],
    )
    lock_states: list[bool] = []

    def fake_load_assistant_config(_root: Path) -> AssistantConfig:
        lock_states.append(client.app.state.daemon.assistant_lifecycle_lock.locked())
        return config

    monkeypatch.setattr(
        assistant_route,
        "load_assistant_config",
        fake_load_assistant_config,
    )

    response = client.post("/api/v1/assistant/repair")

    assert response.status_code == 200, response.text
    assert lock_states == [True]


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
