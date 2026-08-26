"""THR-204 issue 3 — daemon-mediated route isolation (TASK-5579 continuation).

PR #710 already fails closed for IN-PROCESS registry writes from a pytest
process (``set_binary``/``save_registry``/``remove_*`` targeting the DEFAULT
production registry).  This module covers the DAEMON-MEDIATED route — the
surface the 2026-08-23 incident could also have used, and the one the
process-local guard does not cover:

- ``happyranch executor-binaries register`` / ``remove`` (CLI) — the CLI is a
  subprocess of the caller; when the caller is a test/repro context, the CLI
  process inherits pytest's ``PYTEST_CURRENT_TEST`` marker and must refuse
  production-registry writes BEFORE any HTTP request reaches the daemon.
- ``POST /api/v1/executor-binaries/register`` (and the DELETE route) handled
  by a daemon that was itself launched by a test harness — the daemon
  inherits the test marker and must fail closed cleanly (403) before any
  mkdir/.tmp/write surface, with registry bytes unchanged.

The acceptance boundary is exercised against a SEPARATELY RUNNING daemon
process (real ``python -m runtime.daemon`` subprocess) whose registry is a
disposable sandbox standing in for the production registry: the daemon runs
with ``HOME`` redirected into a per-test sandbox and ``HAPPYRANCH_DAEMON_HOME``
unset, so the sandbox's ``~/.happyranch/executors.json`` IS its default
registry — exactly the production shape — while the REAL
``/home/benze/.happyranch/executors.json`` is never a write target.  A
module-scoped autouse fixture additionally checksums the REAL live registry
before and after and asserts byte + semantic identity.

Two daemon shapes are exercised:

- **test-launched** — env carries pytest's ``PYTEST_CURRENT_TEST`` marker
  (inherited by any daemon a test harness starts).  POSTs of production
  names must be refused with 403 and leave registry bytes unchanged.
- **live-shaped** — env is scrubbed of the marker (a fresh interpreter never
  imports pytest, so this is exactly the production daemon shape).  A CLI
  invoked from a TEST context must still refuse pre-flight; a CLI invoked
  from an OPERATOR context must still register (operator preservation).

Residual (documented, not silently hidden): a bare HTTP POST crafted by a
pytest process DIRECTLY against a live-shaped daemon carries no in-band
test marker the daemon can see, so it is indistinguishable from web-UI
operator traffic.  ``test_post_to_live_shaped_daemon_is_the_documented_residual``
pins that boundary explicitly.  Closing it requires a product-visible
API/CLI change (operator confirmation capability or surface markers) —
founder decision, out of scope here.
"""
from __future__ import annotations

import argparse
import json
import os
import stat
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

from cli.commands.executor_binaries import (
    cmd_executor_binaries_register,
    cmd_executor_binaries_remove,
)

# Real live production registry — the thing the fix must never let a test write.
_LIVE_REGISTRY = Path.home() / ".happyranch" / "executors.json"


@pytest.fixture(scope="module", autouse=True)
def _live_registry_proof():
    """Proof the live production registry bytes and valid entries remain
    unchanged across this entire daemon-mediated adversarial module."""
    before_bytes = _LIVE_REGISTRY.read_bytes() if _LIVE_REGISTRY.exists() else None
    before_entries = json.loads(before_bytes) if before_bytes is not None else {}
    yield
    after_bytes = _LIVE_REGISTRY.read_bytes() if _LIVE_REGISTRY.exists() else None
    assert after_bytes == before_bytes, (
        "THR-204 issue 3: the live production executors.json BYTES changed "
        "while running the daemon-mediated isolation regression suite!"
    )
    after_entries = json.loads(after_bytes) if after_bytes is not None else {}
    assert after_entries == before_entries, (
        "THR-204 issue 3: the live production registry ENTRIES changed "
        "while running the daemon-mediated isolation regression suite!"
    )


# ─────────────────────────────────────────────────────────────────
# Shared helpers
# ─────────────────────────────────────────────────────────────────


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _make_exit0_fake(tmp_path: Path, name: str = "fake_claude.sh") -> Path:
    """An executable fake binary that exits 0 — passes ``validate_binary``,
    so a naive registration would succeed and silently clobber production."""
    fake = tmp_path / name
    fake.write_text("#!/bin/sh\nexit 0\n")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    return fake


def _prod_sandbox_env(sandbox_home: Path, *, test_context: bool) -> dict[str, str]:
    """Env for a daemon/CLI subprocess whose DEFAULT registry is the sandbox.

    ``HOME`` is redirected into the sandbox and ``HAPPYRANCH_DAEMON_HOME`` is
    unset, so the sandbox's ``~/.happyranch/executors.json`` plays the role of
    the production registry.  ``HAPPYRANCH_DAEMON_PORT=0`` (ephemeral) so a
    sandbox daemon never collides with the real daemon on 8765.
    """
    env = dict(os.environ)
    env.pop("HAPPYRANCH_DAEMON_HOME", None)
    env.pop("HAPPYRANCH_ORG_SLUG", None)
    env["HOME"] = str(sandbox_home)
    env["HAPPYRANCH_DAEMON_PORT"] = "0"
    if test_context:
        env["PYTEST_CURRENT_TEST"] = "tests::daemon-mediated-acceptance"
    else:
        env.pop("PYTEST_CURRENT_TEST", None)
    return env


class _Daemon:
    def __init__(self, proc: subprocess.Popen, home: Path, port: str, token: str):
        self.proc = proc
        self.home = home
        self.port = port
        self.token = token

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def registry(self) -> Path:
        return self.home / ".happyranch" / "executors.json"

    def post(self, path: str, json_body: dict):
        return httpx.post(
            f"{self.base_url}{path}",
            json=json_body,
            headers={"Authorization": f"Bearer {self.token}"},
            timeout=10.0,
        )

    def request(self, method: str, path: str, json_body: dict | None = None):
        return httpx.request(
            method,
            f"{self.base_url}{path}",
            json=json_body,
            headers={"Authorization": f"Bearer {self.token}"},
            timeout=10.0,
        )

    def stop(self) -> None:
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=10)


def _launch_daemon(sandbox_home: Path, *, test_context: bool) -> _Daemon:
    """Launch a real daemon subprocess owning the sandbox registry."""
    env = _prod_sandbox_env(sandbox_home, test_context=test_context)
    proc = subprocess.Popen(
        [sys.executable, "-m", "runtime.daemon"],
        cwd=_repo_root(),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    dh = sandbox_home / ".happyranch"
    port_file = dh / "daemon.port"
    token_file = dh / "daemon.token"
    deadline = time.time() + 25
    while time.time() < deadline:
        if proc.poll() is not None:
            out, err = proc.communicate(timeout=5)
            raise RuntimeError(
                f"sandbox daemon exited early (rc={proc.returncode}): {err[-2000:]}"
            )
        if port_file.exists() and token_file.exists():
            port = port_file.read_text().strip()
            token = token_file.read_text().strip()
            try:
                r = httpx.get(
                    f"http://127.0.0.1:{port}/api/v1/health", timeout=1.0
                )
                if r.status_code == 200:
                    return _Daemon(proc, sandbox_home, port, token)
            except httpx.HTTPError:
                pass
        time.sleep(0.25)
    proc.terminate()
    raise RuntimeError("sandbox daemon failed to become healthy within 25s")


@pytest.fixture
def test_launched_daemon(tmp_path):
    """A daemon launched BY a test harness: inherits pytest's test marker.

    Its default registry is the disposable sandbox registry (HOME redirect +
    HAPPYRANCH_DAEMON_HOME unset) — the production shape, but disposable."""
    daemon = _launch_daemon(tmp_path / "prod-home", test_context=True)
    yield daemon
    daemon.stop()


@pytest.fixture
def live_shaped_daemon(tmp_path):
    """A daemon shaped EXACTLY like the production daemon: fresh interpreter,
    no pytest marker in its environment, default registry in the sandbox.

    The real production daemon never runs under pytest, so this fixture is
    the honest stand-in for the live daemon's write surface."""
    daemon = _launch_daemon(tmp_path / "prod-home", test_context=False)
    yield daemon
    daemon.stop()


# ─────────────────────────────────────────────────────────────────
# CLI-layer guard — unit tests (in-process, no daemon needed)
# ─────────────────────────────────────────────────────────────────


def _unsolated_test_context(monkeypatch, tmp_path):
    """HOME -> sandbox, HAPPYRANCH_DAEMON_HOME unset (the unisolated state
    that caused THR-204 issue 3).  Under pytest, this process is a test
    process, so the CLI guard must refuse before contacting any daemon."""
    sandbox = tmp_path / "prod-home"
    monkeypatch.setenv("HOME", str(sandbox))
    monkeypatch.delenv("HAPPYRANCH_DAEMON_HOME", raising=False)
    return sandbox


def test_cli_register_refuses_unsolated_test_context_before_http(
    monkeypatch, tmp_path, capsys
):
    """`happyranch executor-binaries register claude --path <fake>` from a test
    context with NO daemon-home isolation must exit 1 BEFORE constructing an
    HTTP client — the daemon-mediated route must not even be reached."""
    sandbox = _unsolated_test_context(monkeypatch, tmp_path)
    fake = _make_exit0_fake(tmp_path)

    from_env_called: list[bool] = []

    def _must_not_reach_daemon(*_a, **_k):
        from_env_called.append(True)
        raise AssertionError("CLI must refuse before contacting the daemon")

    with patch(
        "cli.commands.executor_binaries.OpcClient.from_env",
        side_effect=_must_not_reach_daemon,
    ):
        with pytest.raises(SystemExit) as exc_info:
            cmd_executor_binaries_register(
                argparse.Namespace(kind="claude", path=str(fake))
            )

    assert exc_info.value.code == 1
    assert not from_env_called, "no HTTP client may be constructed pre-flight"
    err = capsys.readouterr().err
    assert "HAPPYRANCH_DAEMON_HOME" in err
    assert "executors.json" in err
    # Rejection must happen before any mkdir/.tmp/write surface.
    assert not (sandbox / ".happyranch").exists()


def test_cli_register_refuses_all_production_names(monkeypatch, tmp_path, capsys):
    """At minimum claude and codex — plus the other built-in names and even
    custom kinds — must all be refused in the unisolated test context."""
    sandbox = _unsolated_test_context(monkeypatch, tmp_path)
    fake = _make_exit0_fake(tmp_path)

    for kind in ("claude", "codex", "opencode", "pi", "my-custom-cli"):
        with patch(
            "cli.commands.executor_binaries.OpcClient.from_env",
            side_effect=AssertionError("must not reach the daemon"),
        ):
            with pytest.raises(SystemExit) as exc_info:
                cmd_executor_binaries_register(
                    argparse.Namespace(kind=kind, path=str(fake))
                )
        assert exc_info.value.code == 1
        capsys.readouterr()  # drain

    assert not (sandbox / ".happyranch").exists()


def test_cli_remove_refuses_unsolated_test_context(monkeypatch, tmp_path, capsys):
    """remove must refuse too — the DELETE route can delete production
    registry entries and must not be reachable from a test context."""
    sandbox = _unsolated_test_context(monkeypatch, tmp_path)

    with patch(
        "cli.commands.executor_binaries.OpcClient.from_env",
        side_effect=AssertionError("must not reach the daemon"),
    ):
        with pytest.raises(SystemExit) as exc_info:
            cmd_executor_binaries_remove(
                argparse.Namespace(
                    kind="my-custom-cli", expected_path="/opt/bin/my-cli"
                )
            )

    assert exc_info.value.code == 1
    err = capsys.readouterr().err
    assert "HAPPYRANCH_DAEMON_HOME" in err
    assert not (sandbox / ".happyranch").exists()


def test_cli_register_allows_isolated_daemon_home(monkeypatch, tmp_path):
    """Proper isolation (HAPPYRANCH_DAEMON_HOME -> temp daemon home) keeps the
    CLI working under pytest — normal test registration behavior is preserved."""
    isolated = tmp_path / "iso-home"
    monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(isolated))
    fake = _make_exit0_fake(tmp_path)

    fake_client = MagicMock()
    fake_client.post.return_value = MagicMock(
        status_code=200,
        json=lambda: {"kind": "claude", "path": str(fake), "valid": True},
    )

    with patch(
        "cli.commands.executor_binaries.OpcClient.from_env",
        return_value=fake_client,
    ):
        cmd_executor_binaries_register(
            argparse.Namespace(kind="claude", path=str(fake))
        )

    fake_client.post.assert_called_once_with(
        "/api/v1/executor-binaries/register",
        json={"kind": "claude", "path": str(fake)},
    )


def test_cli_register_allows_isolated_home_via_symlink(monkeypatch, tmp_path):
    """An isolated HAPPYRANCH_DAEMON_HOME spelled through a symlink to a REAL
    temp daemon home must keep working (canonical comparison, positive case)."""
    real_iso = tmp_path / "real-iso"
    real_iso.mkdir()
    alias = tmp_path / "iso-alias"
    alias.symlink_to(real_iso, target_is_directory=True)
    monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(alias))
    fake = _make_exit0_fake(tmp_path)

    fake_client = MagicMock()
    fake_client.post.return_value = MagicMock(
        status_code=200,
        json=lambda: {"kind": "pi", "path": str(fake), "valid": True},
    )

    with patch(
        "cli.commands.executor_binaries.OpcClient.from_env",
        return_value=fake_client,
    ):
        cmd_executor_binaries_register(
            argparse.Namespace(kind="pi", path=str(fake))
        )

    fake_client.post.assert_called_once()


def test_cli_register_refuses_symlink_alias_to_default(monkeypatch, tmp_path, capsys):
    """HAPPYRANCH_DAEMON_HOME aliased (via symlink) to the sandbox's default
    .happyranch must still be refused — the guard compares CANONICAL targets."""
    sandbox = tmp_path / "prod-home"
    monkeypatch.setenv("HOME", str(sandbox))
    dh_alias = tmp_path / "dh-alias"
    dh_alias.symlink_to(sandbox / ".happyranch", target_is_directory=True)
    monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(dh_alias))
    fake = _make_exit0_fake(tmp_path)

    with patch(
        "cli.commands.executor_binaries.OpcClient.from_env",
        side_effect=AssertionError("must not reach the daemon"),
    ):
        with pytest.raises(SystemExit) as exc_info:
            cmd_executor_binaries_register(
                argparse.Namespace(kind="claude", path=str(fake))
            )

    assert exc_info.value.code == 1
    assert "HAPPYRANCH_DAEMON_HOME" in capsys.readouterr().err
    assert not (sandbox / ".happyranch").exists()


def test_cli_guard_repeated_attempts_no_residue(monkeypatch, tmp_path, capsys):
    """Repeated attacks fail closed every time, leaving zero write residue."""
    sandbox = _unsolated_test_context(monkeypatch, tmp_path)
    fake = _make_exit0_fake(tmp_path)

    for _ in range(3):
        with patch(
            "cli.commands.executor_binaries.OpcClient.from_env",
            side_effect=AssertionError("must not reach the daemon"),
        ):
            with pytest.raises(SystemExit):
                cmd_executor_binaries_register(
                    argparse.Namespace(kind="claude", path=str(fake))
                )
        capsys.readouterr()

    assert not (sandbox / ".happyranch").exists()
    assert not (sandbox / ".happyranch" / "executors.json.tmp").exists()


def test_cli_guard_environment_restored_after_refusal(monkeypatch, tmp_path):
    """After an unisolated refusal, restoring isolation (the environment
    cleanup a test fixture performs) makes the CLI usable again."""
    _unsolated_test_context(monkeypatch, tmp_path)
    fake = _make_exit0_fake(tmp_path)

    with patch(
        "cli.commands.executor_binaries.OpcClient.from_env",
        side_effect=AssertionError("must not reach the daemon"),
    ):
        with pytest.raises(SystemExit):
            cmd_executor_binaries_register(
                argparse.Namespace(kind="claude", path=str(fake))
            )

    # Cleanup/restoration: point at an isolated daemon home -> works again.
    isolated = tmp_path / "iso-home"
    monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(isolated))
    fake_client = MagicMock()
    fake_client.post.return_value = MagicMock(
        status_code=200,
        json=lambda: {"kind": "claude", "path": str(fake), "valid": True},
    )
    with patch(
        "cli.commands.executor_binaries.OpcClient.from_env",
        return_value=fake_client,
    ):
        cmd_executor_binaries_register(
            argparse.Namespace(kind="claude", path=str(fake))
        )
    fake_client.post.assert_called_once()


# ─────────────────────────────────────────────────────────────────
# Daemon-mediated acceptance — real daemon subprocess (CLI surface)
# ─────────────────────────────────────────────────────────────────


def test_real_cli_from_test_context_refuses_against_live_shaped_daemon(
    live_shaped_daemon,
):
    """The REAL CLI registration command invoked from a test context must be
    refused pre-flight even against a live-shaped (no-marker) daemon — this is
    the incident's suspected route: shelling out to
    ``happyranch executor-binaries register`` from a repro."""
    daemon = live_shaped_daemon
    registry = daemon.registry
    registry.write_text(json.dumps({
        "claude": "/usr/local/bin/claude-real",
        "codex": "/usr/local/bin/codex-real",
    }))
    before = registry.read_bytes()
    fake = _make_exit0_fake(daemon.home)

    env = _prod_sandbox_env(daemon.home, test_context=True)
    r = subprocess.run(
        [
            sys.executable, "-m", "cli.main", "executor-binaries", "register",
            "claude", "--path", str(fake),
        ],
        cwd=_repo_root(),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert r.returncode == 1, f"CLI must refuse; stdout={r.stdout!r} stderr={r.stderr!r}"
    assert "HAPPYRANCH_DAEMON_HOME" in r.stderr
    # Registry bytes and valid entries unchanged — no request reached the daemon.
    assert registry.read_bytes() == before
    assert json.loads(registry.read_text()) == {
        "claude": "/usr/local/bin/claude-real",
        "codex": "/usr/local/bin/codex-real",
    }


def test_real_cli_operator_context_registers_against_live_shaped_daemon(
    live_shaped_daemon,
):
    """An OPERATOR context (no test marker anywhere in the CLI subprocess env)
    must still register production names through the real CLI + real daemon —
    legitimate operator registration is preserved end to end."""
    daemon = live_shaped_daemon
    registry = daemon.registry
    fake = _make_exit0_fake(daemon.home)

    env = _prod_sandbox_env(daemon.home, test_context=False)
    r = subprocess.run(
        [
            sys.executable, "-m", "cli.main", "executor-binaries", "register",
            "claude", "--path", str(fake),
        ],
        cwd=_repo_root(),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert r.returncode == 0, f"operator registration must succeed: {r.stderr!r}"
    assert "registered" in r.stdout
    assert json.loads(registry.read_text())["claude"] == str(fake)


def _launch_isolated_daemon(isolated_home: Path) -> _Daemon:
    """Launch a daemon whose registry is an EXPLICIT isolated daemon home
    (HAPPYRANCH_DAEMON_HOME set to the isolated home) — the legitimate test
    isolation pattern.  HOME points ELSEWHERE, so the isolated home is NOT
    the daemon's "default production" registry and the write guard never
    fires for it."""
    env = _prod_sandbox_env(isolated_home, test_context=True)
    env["HAPPYRANCH_DAEMON_HOME"] = str(isolated_home / ".happyranch")
    env["HOME"] = str(isolated_home / "operator-home")
    proc = subprocess.Popen(
        [sys.executable, "-m", "runtime.daemon"],
        cwd=_repo_root(),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    dh = isolated_home / ".happyranch"
    deadline = time.time() + 25
    while time.time() < deadline:
        if proc.poll() is not None:
            _, err = proc.communicate(timeout=5)
            raise RuntimeError(f"isolated daemon exited early: {err[-1500:]}")
        if (dh / "daemon.port").exists() and (dh / "daemon.token").exists():
            port = (dh / "daemon.port").read_text().strip()
            token = (dh / "daemon.token").read_text().strip()
            try:
                if httpx.get(
                    f"http://127.0.0.1:{port}/api/v1/health", timeout=1.0
                ).status_code == 200:
                    return _Daemon(proc, isolated_home, port, token)
            except httpx.HTTPError:
                pass
        time.sleep(0.25)
    proc.terminate()
    raise RuntimeError("isolated daemon failed to become healthy")


def test_real_cli_test_context_with_isolated_daemon_home_still_registers(tmp_path):
    """A test-context CLI pointed at an ISOLATED daemon home (the legitimate
    test isolation pattern) must keep registering — the guard only protects
    the default production registry."""
    isolated_home = tmp_path / "iso-daemon-home"
    daemon = _launch_isolated_daemon(isolated_home)
    try:
        fake = _make_exit0_fake(isolated_home)
        env = _prod_sandbox_env(isolated_home, test_context=True)
        env["HAPPYRANCH_DAEMON_HOME"] = str(isolated_home / ".happyranch")
        env["HOME"] = str(tmp_path / "operator-home")
        r = subprocess.run(
            [
                sys.executable, "-m", "cli.main", "executor-binaries", "register",
                "codex", "--path", str(fake),
            ],
            cwd=_repo_root(),
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert r.returncode == 0, f"isolated registration must succeed: {r.stderr!r}"
        reg = isolated_home / ".happyranch" / "executors.json"
        assert json.loads(reg.read_text())["codex"] == str(fake)
    finally:
        daemon.stop()


# ─────────────────────────────────────────────────────────────────
# Daemon-mediated acceptance — real daemon subprocess (POST surface)
# ─────────────────────────────────────────────────────────────────


def test_post_to_test_launched_daemon_refuses_production_names_before_write(
    test_launched_daemon,
):
    """POST /executor-binaries/register against a daemon the test harness
    launched (marker inherited) must fail closed with 403, BEFORE any
    mkdir/.tmp/write — registry bytes and entries unchanged.

    Cases: (b) an executable fake that exits 0 deceptively (passes
    validate_binary, so only the guard can stop it); and (a) the
    initially-valid fixture that breaks LATER (removed/non-executable) is
    exactly the incident shape — the registration itself must never land.
    A non-executable path is rejected by validation (422) before any write."""
    daemon = test_launched_daemon
    registry = daemon.registry
    registry.write_text(json.dumps({
        "claude": "/usr/local/bin/claude-real",
        "codex": "/usr/local/bin/codex-real",
        "pi": "/opt/homebrew/bin/pi",
    }))
    before = registry.read_bytes()

    # (b) executable fake that exits 0 — passes validate_binary.
    exit0_fake = _make_exit0_fake(daemon.home)
    # (a) fixture removed AFTER a clean registration — register an exit-0
    # fake for codex, then unlink it (the incident's later-dangling shape).
    dangling_fake = _make_exit0_fake(daemon.home, name="fragile_codex.sh")

    # Exit-0 fakes (the deceptive case) must be refused by the guard (403).
    for kind, path in (("claude", str(exit0_fake)), ("codex", str(exit0_fake))):
        r = daemon.post(
            "/api/v1/executor-binaries/register", {"kind": kind, "path": path}
        )
        assert r.status_code == 403, (
            f"POST {kind} must be refused with 403; got {r.status_code}: {r.text}"
        )
        assert "HAPPYRANCH_DAEMON_HOME" in r.json()["detail"]

    # Later-removed fixture: registration of the initially-valid path is
    # refused up front (guard), and a now-non-executable path is rejected by
    # validation (422) — in NO case does the registry change.
    dangling_fake.unlink()
    r = daemon.post(
        "/api/v1/executor-binaries/register",
        {"kind": "codex", "path": str(dangling_fake)},
    )
    assert r.status_code == 403 or r.status_code == 422, (
        f"removed-path POST must be refused; got {r.status_code}: {r.text}"
    )

    assert registry.read_bytes() == before
    assert json.loads(registry.read_text()) == {
        "claude": "/usr/local/bin/claude-real",
        "codex": "/usr/local/bin/codex-real",
        "pi": "/opt/homebrew/bin/pi",
    }
    assert not (daemon.home / ".happyranch" / "executors.json.tmp").exists(), (
        "no .json.tmp scratch file may appear next to the protected registry"
    )


def test_post_delete_to_test_launched_daemon_refuses(test_launched_daemon):
    """DELETE /executor-binaries/{kind} against a test-launched daemon must
    also fail closed with 403 before any write (custom kind — built-in kinds
    are separately blocked by the route's 422 built-in guard)."""
    daemon = test_launched_daemon
    registry = daemon.registry
    registry.write_text(json.dumps({"my-custom-cli": "/opt/bin/my-custom-cli"}))
    before = registry.read_bytes()

    resp = daemon.request(
        "DELETE",
        "/api/v1/executor-binaries/my-custom-cli",
        json_body={
            "expected_name": "my-custom-cli",
            "expected_path": "/opt/bin/my-custom-cli",
        },
    )
    assert resp.status_code == 403, (
        f"DELETE my-custom-cli must be refused with 403; got {resp.status_code}: {resp.text}"
    )
    assert "HAPPYRANCH_DAEMON_HOME" in resp.json()["detail"]
    assert registry.read_bytes() == before


def test_operator_post_to_test_launched_daemon_with_isolated_home_still_works(tmp_path):
    """A test-launched daemon with an ISOLATED home (HAPPYRANCH_DAEMON_HOME set)
    must keep accepting registrations — the guard only protects the default
    production registry, so normal isolated test daemons are unaffected."""
    isolated_home = tmp_path / "iso-daemon"
    daemon = _launch_isolated_daemon(isolated_home)
    try:
        fake = _make_exit0_fake(isolated_home)
        r = daemon.post(
            "/api/v1/executor-binaries/register",
            {"kind": "claude", "path": str(fake)},
        )
        assert r.status_code == 200, f"isolated registration must succeed: {r.text}"
        reg = isolated_home / ".happyranch" / "executors.json"
        assert json.loads(reg.read_text())["claude"] == str(fake)
    finally:
        daemon.stop()


# ─────────────────────────────────────────────────────────────────
# Residual boundary — pinned explicitly, not silently hidden
# ─────────────────────────────────────────────────────────────────


def test_post_to_live_shaped_daemon_is_the_documented_residual(live_shaped_daemon):
    """The KNOWN RESIDUAL, pinned for the record (TASK-5579 escalation):

    A bare HTTP POST crafted directly against a live-shaped daemon (the exact
    production daemon shape — fresh interpreter, no pytest marker anywhere)
    is indistinguishable from web-UI operator traffic: there is NO in-band
    test marker the daemon can see, so the write succeeds.

    This uses the DISPOSABLE sandbox registry as the production stand-in —
    the real live registry is never a write target (module-scoped byte proof).

    Closing this residual requires a product-visible API/CLI change — e.g. an
    operator-confirmation capability, a web+CLI surface-marker requirement,
    or an interactive-TTY gate for production-name registration — which is a
    founder decision, NOT a change we make silently here."""
    daemon = live_shaped_daemon
    registry = daemon.registry
    registry.write_text(json.dumps({"claude": "/usr/local/bin/claude-real"}))
    exit0_fake = _make_exit0_fake(daemon.home)

    r = daemon.post(
        "/api/v1/executor-binaries/register",
        {"kind": "claude", "path": str(exit0_fake)},
    )
    assert r.status_code == 200, (
        "residual boundary: bare POST to a live-shaped daemon currently "
        f"succeeds (200); got {r.status_code}: {r.text}"
    )
    assert json.loads(registry.read_text())["claude"] == str(exit0_fake)
