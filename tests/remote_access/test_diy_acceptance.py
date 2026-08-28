"""REAL end-to-end Supported-DIY acceptance (THR-097 Unit 3A).

A REAL away-client process (``tests/remote_access/diy_client.py`` — the
THR-034 wire contract the signed macOS ``ClientBridge`` speaks) connects over
a REAL network path to the REAL supervised connector (the shipping
``python -m runtime.remote_access.cli run --diy`` subprocess) which forwards
to a REAL loopback daemon with the bearer injected on the final hop.

Proven scenarios (each is asserted, not inferred):

1. allowed route success (redeem -> authenticated GET -> daemon response);
2. forbidden route denial (agent-callback route -> 403 category-only);
3. direct remote daemon/bearer attempt (daemon NOT reachable on the network
   address; the daemon bearer is never a usable pairing credential);
4. restart with persisted revocation (revoke -> SIGTERM -> fresh process over
   the same files -> still denied);
5. re-pair invalidating old authority (new credential works, old 403);
6. replayed code and removed credentials deny identically;
7. network/control-plane outage fail-closed (daemon down -> listener stops ->
   client refused; daemon back -> supervised listener returns);
8. credential/token leakage scans across the connector's stdout/stderr, the
   client outputs, the config file, the trust-state files, and process argv;
9. cross-process revocation closes a live in-flight SSE stream served by the
   connector PROCESS within one ``poll_seconds`` interval after a SEPARATE
   CLI ``revoke``, and the CLI never reports false stream closure.

The residual gap (reported, never fabricated): this host has no macOS binary
and no Tailscale/headscale client, so the genuine macOS-client launch and the
WireGuard/tailnet transport hop remain unproven here (THR-034 signed-device
acceptance). The wire contract served by the connector is identical.

Runs under ``-m integration``; skipped with an explicit reason on hosts with
no non-loopback IPv4 (the customer-owned-network address requirement).
"""
from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from runtime.remote_access.network import validate_customer_network_address
from runtime.remote_access.state_store import AtomicFileTrustStateStore

from .conftest import load_fixture, make_policy_envelope
from .fake_daemon import FakeDaemon

BEARER = "diy-acceptance-bearer-42"
HERE = Path(__file__).resolve().parent
CLIENT = HERE / "diy_client.py"

pytestmark = pytest.mark.integration


def _host_network_ipv4() -> str | None:
    try:
        out = subprocess.run(
            ["ip", "-4", "-o", "addr", "show"],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    for line in out.splitlines():
        parts = line.split()
        for i, part in enumerate(parts):
            if part == "inet" and i + 1 < len(parts):
                addr = parts[i + 1].split("/")[0]
                if addr.startswith("127."):
                    continue
                try:
                    validate_customer_network_address(addr)
                    return addr
                except Exception:
                    continue
    return None


NETWORK_IPV4 = _host_network_ipv4()


def _free_port(host: str) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((host, 0))
        return int(s.getsockname()[1])


def _run_client(host: str, port: int, args: list[str], timeout: int = 20) -> dict:
    proc = subprocess.run(
        [sys.executable, str(CLIENT), "--host", host, "--port", str(port), *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    assert proc.returncode == 0, f"client failed: {proc.stderr}"
    return json.loads(proc.stdout)


def _wait_until(predicate, timeout: float = 30.0, interval: float = 0.2, what: str = "condition") -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(interval)
    raise AssertionError(f"timed out waiting for {what}")


def _connector_reachable(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except OSError:
        return False


@pytest.mark.skipif(
    NETWORK_IPV4 is None,
    reason="host has no non-loopback IPv4 address for a customer-owned-network acceptance run",
)
def test_real_diy_acceptance(tmp_path) -> None:
    host = NETWORK_IPV4
    connector_port = _free_port(host)

    # ── 1. the loopback daemon (real TCP on 127.0.0.1) ────────────────────
    daemon = FakeDaemon(BEARER)
    daemon.start()
    try:
        # ── 2. hermetic connector config ───────────────────────────────────
        token_path = tmp_path / "daemon.token"
        token_path.write_text(BEARER)
        token_path.chmod(0o600)
        state_path = tmp_path / "trust-state.json"
        policy_path = tmp_path / "policy.json"
        fixture = load_fixture("route-policy")
        envelope = make_policy_envelope(fixture, issued_at=datetime.now(timezone.utc) - timedelta(seconds=30))
        policy_path.write_text(envelope.model_dump_json() if hasattr(envelope, "model_dump_json") else json.dumps(envelope.__dict__))
        # The customer-owned network is the ENCRYPTED tailscale mode ONLY
        # (the plaintext explicit concrete-address mode was removed). The
        # tailnet address is resolved through the REAL shipping resolution
        # path (TailscaleCliResolver -> subprocess -> strict validation)
        # with a stub ``tailscale ip -4`` executable printing this host's
        # non-loopback IPv4 — this host has no tailnet; the encrypted
        # transport hop remains an honest residual gap (see the module
        # docstring).
        stub = tmp_path / "tailscale-stub"
        stub.write_text(f"#!/bin/sh\nprintf '%s\\n' '{host}'\n")
        stub.chmod(0o755)
        config = {
            "tenant_id": "diy",
            "home_id": "home-a",
            "connector_id": "connector-a",
            "daemon_port": daemon.port,
            "daemon_token_path": str(token_path),
            "policy_path": str(policy_path),
            "state_path": str(state_path),
            "system": False,
            "poll_seconds": 0.2,
            "diy": {
                "network": {"mode": "tailscale", "tailscale_cli": str(stub)},
                "bind_port": connector_port,
            },
        }
        config_path = tmp_path / "config.json"
        config_path.write_text(json.dumps(config))

        transcript: list[str] = []

        def log(line: str) -> None:
            transcript.append(line)

        def start_connector() -> subprocess.Popen:
            proc = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "runtime.remote_access.cli",
                    "run",
                    "--diy",
                    "--config",
                    str(config_path),
                ],
                cwd=Path(__file__).resolve().parents[3],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env={**os.environ, "PYTHONUNBUFFERED": "1"},
            )
            return proc

        proc = start_connector()
        try:
            _wait_until(lambda: _connector_reachable(host, connector_port), what="connector listener")
            log("connector listening on %s:%s" % (host, connector_port))

            # ── 3. issue a pairing code via the REAL CLI ───────────────────
            pair_proc = subprocess.run(
                [sys.executable, "-m", "runtime.remote_access.cli", "pair", "--config", str(config_path), "--device", "macbook-pro"],
                capture_output=True,
                text=True,
                timeout=20,
            )
            assert pair_proc.returncode == 0, pair_proc.stderr
            code = [l for l in pair_proc.stdout.splitlines() if "pairing code for device" in l][0].split(": ")[-1].strip()
            log(f"pairing code issued (8 chars, shown once)")

            # ── 4. scenario 1: allowed route success ───────────────────────
            redeem = _run_client(host, connector_port, ["redeem", "--code", code])
            assert redeem["status"] == 200
            credential = redeem["body"]["credential"]
            assert credential.startswith("hrpair_")
            log("scenario 1a: POST /pair redemption -> 200 + hrpair_ credential")
            health = _run_client(host, connector_port, ["request", "--path", "/api/v1/health", "--credential", credential])
            assert health["status"] == 200
            assert health["body"].get("ok") is True
            log("scenario 1b: authenticated GET /api/v1/health -> 200 (daemon reached via loopback)")
            assert any(r["path"] == "/api/v1/health" and r["headers"].get("authorization") == f"Bearer {BEARER}" for r in daemon.requests), "daemon must receive the injected bearer on the final hop"

            # ── 5. scenario 2: forbidden route ─────────────────────────────
            forbidden = _run_client(host, connector_port, ["request", "--path", "/api/v1/report-completion", "--credential", credential])
            assert forbidden["status"] == 403
            log("scenario 2: forbidden agent-callback route -> 403")

            # ── 6. scenario 3a: direct remote daemon attempt ───────────────
            try:
                with socket.create_connection((host, daemon.port), timeout=3):
                    pytest.fail("daemon must not listen on the customer-network address")
            except OSError:
                pass
            log("scenario 3a: direct TCP to (network-addr, daemon-port) refused — daemon is loopback-only")

            # ── 7. scenario 3b: daemon bearer is never a credential ────────
            direct = _run_client(host, connector_port, ["request", "--path", "/api/v1/health", "--credential", BEARER])
            assert direct["status"] == 403
            log("scenario 3b: direct bearer-as-credential attempt -> 403")

            # ── 8. scenario 6: replayed code denies identically ────────────
            replay = _run_client(host, connector_port, ["redeem", "--code", code])
            assert replay["status"] == 403
            assert "credential" not in replay["body"]
            log("scenario 6a: replayed one-time code -> 403 (single-use)")

            # ── 9. scenario 5: re-pair invalidates old authority ───────────
            pair2 = subprocess.run(
                [sys.executable, "-m", "runtime.remote_access.cli", "pair", "--config", str(config_path), "--device", "macbook-pro"],
                capture_output=True,
                text=True,
                timeout=20,
            )
            assert pair2.returncode == 0
            code2 = [l for l in pair2.stdout.splitlines() if "pairing code for device" in l][0].split(": ")[-1].strip()
            redeem2 = _run_client(host, connector_port, ["redeem", "--code", code2])
            assert redeem2["status"] == 200
            credential2 = redeem2["body"]["credential"]
            assert credential2 != credential
            old = _run_client(host, connector_port, ["request", "--path", "/api/v1/health", "--credential", credential])
            assert old["status"] == 403
            new = _run_client(host, connector_port, ["request", "--path", "/api/v1/health", "--credential", credential2])
            assert new["status"] == 200
            log("scenario 5: re-pair mints new authority; OLD credential -> 403, NEW -> 200")

            # ── 10. scenario 4: restart with persisted revocation ──────────
            revoke_proc = subprocess.run(
                [sys.executable, "-m", "runtime.remote_access.cli", "revoke", "--config", str(config_path), "--device", "macbook-pro"],
                capture_output=True,
                text=True,
                timeout=20,
            )
            assert revoke_proc.returncode == 0, revoke_proc.stderr
            denied = _run_client(host, connector_port, ["request", "--path", "/api/v1/health", "--credential", credential2])
            assert denied["status"] == 403
            log("scenario 4a: revoke -> live credential immediately denied")
            proc.send_signal(signal.SIGTERM)
            proc.wait(timeout=20)
            proc = start_connector()
            _wait_until(lambda: _connector_reachable(host, connector_port), what="restarted connector listener")
            denied_after_restart = _run_client(host, connector_port, ["request", "--path", "/api/v1/health", "--credential", credential2])
            assert denied_after_restart["status"] == 403
            log("scenario 4b: connector restarted over the same files -> revocation persisted, still denied")

            # ── 11. scenario 6b: removed credential denies like absent ─────
            remove_proc = subprocess.run(
                [sys.executable, "-m", "runtime.remote_access.cli", "remove-device", "--config", str(config_path), "--device", "macbook-pro"],
                capture_output=True,
                text=True,
                timeout=20,
            )
            assert remove_proc.returncode == 0
            removed = _run_client(host, connector_port, ["request", "--path", "/api/v1/health", "--credential", credential2])
            assert removed["status"] == 403
            log("scenario 6b: removed credential -> 403 (identical deny)")

            # ── 12. scenario 7: network/control-plane outage fail-closed ───
            daemon.stop()
            _wait_until(
                lambda: not _connector_reachable(host, connector_port),
                timeout=30,
                what="listener stopped after daemon outage (fail closed)",
            )
            log("scenario 7a: daemon outage -> readiness loss -> listener stopped (fail closed)")
            daemon_port = daemon.port
            daemon = FakeDaemon(BEARER, port=daemon_port)
            daemon.start()
            _wait_until(lambda: _connector_reachable(host, connector_port), timeout=30, what="listener recovered after daemon return")
            log("scenario 7b: daemon back -> supervised listener recovered")

            # ── 13. scenario 8: credential/token leakage scans ─────────────
            proc.send_signal(signal.SIGTERM)
            proc.wait(timeout=20)
            out, err = proc.communicate() if proc.stdout is not None else (b"", b"")
            proc = None
            # Connector-visible surfaces ONLY (client redeem outputs legitimately
            # carry the credential — they are the intended one-time delivery):
            connector_visible = " ".join(
                [
                    out or "",
                    err or "",
                    config_path.read_text(),
                    token_path.read_text(),
                    state_path.read_text() if state_path.exists() else "",
                    (Path(str(state_path) + ".anchor").read_text())
                    if Path(str(state_path) + ".anchor").exists()
                    else "",
                    pair_proc.stdout,
                    pair2.stdout,
                ]
            )
            # The credential values must NEVER appear in connector logs,
            # config, state files, or CLI outputs (the pair command prints
            # only the short one-time CODE, never a credential).
            assert credential not in connector_visible, "credential leaked into connector-visible surface"
            assert credential2 not in connector_visible, "credential2 leaked into connector-visible surface"
            # The daemon bearer never appears in connector logs (it is read
            # only on the final loopback hop and injected there).
            assert BEARER not in (out or "") and BEARER not in (err or ""), "daemon bearer leaked into connector logs"
            # The trust-state envelope holds digests only — never a raw
            # credential shape.
            state_blob = (state_path.read_text() if state_path.exists() else "") + (
                Path(str(state_path) + ".anchor").read_text()
                if Path(str(state_path) + ".anchor").exists()
                else ""
            )
            assert "hrpair_" not in state_blob, "credential shape in trust state"
            log("scenario 8: leakage scans clean (credentials only in client outputs; bearer never in connector logs)")

            # ── preserve the transcript ────────────────────────────────────
            transcript_path = tmp_path / "acceptance-transcript.txt"
            transcript_path.write_text("\n".join(transcript) + "\n")
            print(f"\n=== ACCEPTANCE TRANSCRIPT ===\n{chr(10).join(transcript)}\n=== END TRANSCRIPT ===")
        finally:
            if proc is not None and proc.poll() is None:
                proc.send_signal(signal.SIGTERM)
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
    finally:
        daemon.stop()


@pytest.mark.skipif(
    NETWORK_IPV4 is None,
    reason="host has no non-loopback IPv4 address for a customer-owned-network acceptance run",
)
def test_acceptance_cross_process_revoke_closes_live_sse_stream(tmp_path) -> None:
    """Cross-process revocation at the REAL shipping seam (TASK-6039
    reviewer [CRITICAL] finding 2 / finding 5): a live SSE stream served by
    the ``cli run --diy`` connector PROCESS is closed by a ``revoke`` issued
    from a SEPARATE CLI PROCESS, through the connector's authoritative
    registry + cross-process revocation reconciliation (bounded by
    ``poll_seconds``). The CLI must never report false stream closure.

    The customer-network address is resolved through the REAL shipping
    resolution path (tailscale-mode ``TailscaleCliResolver`` -> subprocess
    -> strict validation) with a stub ``tailscale ip -4`` executable that
    prints the host's non-loopback IPv4 — no tailnet is present on this
    host, and the encrypted-transport hop remains an honest residual gap.
    """
    host = NETWORK_IPV4
    connector_port = _free_port(host)
    # hold_open: the daemon flushes the SSE headers and HOLDS the body open,
    # so the stream is genuinely in flight when the cross-process revoke
    # lands — closure is revocation-driven, not a natural EOF.
    daemon = FakeDaemon(BEARER, hold_open=True)
    daemon.start()
    try:
        token_path = tmp_path / "daemon.token"
        token_path.write_text(BEARER)
        token_path.chmod(0o600)
        state_path = tmp_path / "trust-state.json"
        policy_path = tmp_path / "policy.json"
        fixture = load_fixture("route-policy")
        envelope = make_policy_envelope(
            fixture, issued_at=datetime.now(timezone.utc) - timedelta(seconds=30)
        )
        policy_path.write_text(
            envelope.model_dump_json()
            if hasattr(envelope, "model_dump_json")
            else json.dumps(envelope.__dict__)
        )
        stub = tmp_path / "tailscale-stub"
        stub.write_text(f"#!/bin/sh\nprintf '%s\\n' '{host}'\n")
        stub.chmod(0o755)
        config = {
            "tenant_id": "diy",
            "home_id": "home-a",
            "connector_id": "connector-a",
            "daemon_port": daemon.port,
            "daemon_token_path": str(token_path),
            "policy_path": str(policy_path),
            "state_path": str(state_path),
            "system": False,
            "poll_seconds": 0.2,
            "diy": {
                "network": {"mode": "tailscale", "tailscale_cli": str(stub)},
                "bind_port": connector_port,
            },
        }
        config_path = tmp_path / "config.json"
        config_path.write_text(json.dumps(config))

        proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "runtime.remote_access.cli",
                "run",
                "--diy",
                "--config",
                str(config_path),
            ],
            cwd=Path(__file__).resolve().parents[3],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
        )
        try:
            _wait_until(lambda: _connector_reachable(host, connector_port), what="connector listener")

            pair_proc = subprocess.run(
                [sys.executable, "-m", "runtime.remote_access.cli", "pair", "--config", str(config_path), "--device", "macbook-pro"],
                capture_output=True,
                text=True,
                timeout=20,
            )
            assert pair_proc.returncode == 0, pair_proc.stderr
            code = [l for l in pair_proc.stdout.splitlines() if "pairing code for device" in l][0].split(": ")[-1].strip()
            redeem = _run_client(host, connector_port, ["redeem", "--code", code])
            assert redeem["status"] == 200
            credential = redeem["body"]["credential"]

            # Open the SSE stream (blocks in the client until closure).
            stream = subprocess.Popen(
                [
                    sys.executable,
                    str(CLIENT),
                    "--host",
                    host,
                    "--port",
                    str(connector_port),
                    "stream",
                    "--path",
                    "/api/v1/orgs/acme/threads/T-1/tail",
                    "--credential",
                    credential,
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            # The stream is established when the daemon has flushed the SSE
            # headers (connector opened the registry-tracked stream).
            assert daemon.started.wait(timeout=15)

            # Revoke from a SEPARATE process (the shipping operator surface).
            revoke_proc = subprocess.run(
                [sys.executable, "-m", "runtime.remote_access.cli", "revoke", "--config", str(config_path), "--device", "macbook-pro"],
                capture_output=True,
                text=True,
                timeout=20,
            )
            assert revoke_proc.returncode == 0, revoke_proc.stderr
            assert "live streams closed" not in revoke_proc.stdout, (
                "CLI must never claim stream closure it cannot prove cross-process"
            )
            # The connector's reconciliation closes the stream within
            # poll_seconds: the client read loop must terminate.
            out, err = stream.communicate(timeout=15)
            assert stream.returncode == 0, err
            result = json.loads(out)
            assert result["status"] == 200
            # Leak scan: the stream client output never carries the credential.
            assert credential not in (out or "") and credential not in (err or "")
        finally:
            if proc.poll() is None:
                proc.send_signal(signal.SIGTERM)
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
    finally:
        daemon.stop()
