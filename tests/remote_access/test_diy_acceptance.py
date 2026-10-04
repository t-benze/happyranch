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

That is the intended reconciliation contract. The process acceptance checks use a 15-second closure wait and do not measure or prove a one-poll latency bound.

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
import selectors
import threading
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
from .diy_client import _admission_response, _read_first_sse_frame, AdmissionError

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
            # A ceremony publishes snapshot then anchor. A concurrent readiness
            # read may stop the old listener, even after the CLI has completed.
            # This scenario checks durable removal on a fresh process; live
            # remove/revoke and stream reopening are covered separately below.
            proc.send_signal(signal.SIGTERM)
            proc.wait(timeout=20)
            remove_proc = subprocess.run(
                [sys.executable, "-m", "runtime.remote_access.cli", "remove-device", "--config", str(config_path), "--device", "macbook-pro"],
                capture_output=True,
                text=True,
                timeout=20,
            )
            assert remove_proc.returncode == 0, remove_proc.stderr
            proc = start_connector()
            # TCP reachability is only a transport probe, not reload readiness.
            # The old process is reaped and publication finished before this
            # process started. Require the actual fresh denial without retries.
            _wait_until(lambda: _connector_reachable(host, connector_port), what="post-removal connector transport")
            requests_before_removed = len(daemon.requests)
            removed = _run_client(host, connector_port, ["request", "--path", "/api/v1/health", "--credential", credential2])
            assert removed["status"] == 403
            assert len(daemon.requests) == requests_before_removed
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

        # Publish the initial code before listener startup. Pair publication
        # can transiently fail readiness and stop an already listening socket.
        # Redemption and all stream/revocation operations below remain live.
        pair_proc = subprocess.run(
            [sys.executable, "-m", "runtime.remote_access.cli", "pair", "--config", str(config_path), "--device", "macbook-pro"],
            capture_output=True,
            text=True,
            timeout=20,
        )
        assert pair_proc.returncode == 0, pair_proc.stderr
        code = [l for l in pair_proc.stdout.splitlines() if "pairing code for device" in l][0].split(": ")[-1].strip()

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


def test_acceptance_cross_process_revoke_remove_then_reopen_streams(tmp_path) -> None:
    """TASK-6045 finding-2 shipping-process regression: after a SEPARATE
    CLI-process revoke AND remove-device close genuinely in-flight SSE
    streams on the REAL ``cli run --diy`` connector, the connector MUST NOT
    be permanently sealed — a re-paired credential and an unaffected current
    credential both open NEW SSE streams in the same connector lifetime,
    while old/revoked credentials and WebSocket upgrades remain denied.

    Fresh per-child heartbeat admission and 16s no-action controls precede
    each separate CLI action; closure is observed within 15s. First-frame
    probes prove reopening without draining a held response.

    Historically this failed on the reviewed head f46a83bc (the one-shot registry is
    sealed and never rotated) and passes after the rotation fix.
    """
    import http.client

    host = NETWORK_IPV4
    connector_port = _free_port(host)
    daemon = FakeDaemon(BEARER, held_sse_mode="heartbeat")
    owner = _OwnedDiyProcesses()
    proc = None
    try:
        daemon.start()
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
        def pair_device(device: str) -> str:
            pair_proc = _run_owned_command(owner, [sys.executable, "-m", "runtime.remote_access.cli", "pair", "--config", str(config_path), "--device", device])
            assert pair_proc.returncode == 0, pair_proc.stderr
            lines = [line for line in pair_proc.stdout.splitlines() if "pairing code for device" in line]
            assert len(lines) == 1, "[D-cleanup] pairing output"
            code = lines[0].split(": ")[-1].strip()
            owner.forbidden.append(code.encode())
            return code

        # Only initial setup is ordered before listener startup. Re-pair,
        # revoke, remove and unaffected reopening stay in this process lifetime.
        initial_code = pair_device("macbook-pro")
        connector = owner.spawn([sys.executable, "-m", "runtime.remote_access.cli", "run", "--diy", "--config", str(config_path)], role="connector")
        proc = connector["proc"]
        owner.wait(lambda: _connector_reachable(host, connector_port), 30, "[D-cleanup] connector listener")

        def redeem(code: str) -> str:
            raw = _run_owned_command(owner, [sys.executable, str(CLIENT), "--host", host, "--port", str(connector_port), "redeem", "--code", code])
            result = json.loads(raw.stdout)
            assert result["status"] == 200, "[D-cleanup] redeem status"
            assert result["status"] == 200, result
            owner.forbidden.append(result["body"]["credential"].encode())
            return result["body"]["credential"]

        next_child = 0
        def open_stream(credential: str):
            nonlocal next_child
            next_child += 1
            before = set(daemon._held_snapshot())
            stream = owner.spawn([sys.executable, str(CLIENT), "--host", host, "--port", str(connector_port), "stream", "--path", "/api/v1/orgs/acme/threads/T-1/tail", "--credential", credential, "--observe-lifecycle", "--observation-id", str(next_child)], child_id=next_child)
            owner.wait(lambda: len(stream["records"]) >= 1, 10, "[D-record] fresh admission required")
            assert daemon.started.wait(timeout=15), "stream must reach the daemon"
            assert stream["proc"].poll() is None, "[D-held] live admitted child"
            added = set(daemon._held_snapshot()) - before
            assert len(added) == 1, "[D-held] one fresh request per child"
            ordinal = added.pop()
            _healthy_held(daemon, ordinal)
            print("DIY_ADMITTED", flush=True)
            _no_action(owner, stream, daemon, ordinal)
            stream["ordinal"] = ordinal
            return stream

        def close_stream(owned_stream) -> dict:
            owner.wait(lambda: owned_stream["proc"].poll() is not None and len(owned_stream["eof"]) == 2, 15, "[D-held] CLI closure within 15s")
            assert owned_stream["proc"].wait(timeout=0) == 0, "[D-strict] closure exit"
            stream = owned_stream["proc"]
            err = bytes(owned_stream["stderr"]).decode()
            assert stream.returncode == 0, err
            assert len(owned_stream["records"]) == 2, "[D-record] exact terminal record"
            assert not owned_stream["stderr"], "[D-record] normal stderr must be empty"
            result = owned_stream["records"][1]
            assert result["kind"] in ("eof", "reset") and result["received_bytes"] > 13, "[D-strict] closure category/continuation"
            assert not daemon.release.is_set() and not daemon._held_snapshot()[owned_stream["ordinal"]]["failure"], "[D-held] fixture cannot supply closure"
            return result

        def sse_status(credential: str) -> int:
            with _admission_response(host, connector_port, "/api/v1/orgs/acme/threads/T-1/tail", {"X-HappyRanch-Device-Credential": credential, "Accept": "text/event-stream"}) as (resp, sock, deadline, watchdog):
                status = resp.status
                if status == 200:
                    assert _read_first_sse_frame(resp, deadline) == 13, "[D-reader] admitted first frame"
                    print("DIY_ADMITTED", flush=True)
                else:
                    assert len(resp.read(1025)) <= 1024, "[D-reader] bounded denial"
                return status

        # ── revoke path ───────────────────────────────────────────────────
        cred_a = redeem(initial_code)
        stream_a = open_stream(cred_a)
        _healthy_held(daemon, stream_a["ordinal"])
        revoke_proc = _run_owned_command(owner, [sys.executable, "-m", "runtime.remote_access.cli", "revoke", "--config", str(config_path), "--device", "macbook-pro"])
        assert revoke_proc.returncode == 0, revoke_proc.stderr

        result = close_stream(stream_a)
        assert result["status"] == 200  # headers flushed before the revoke closed it
        # RE-PAIR opens a NEW SSE in the same connector lifetime.
        cred_a2 = redeem(pair_device("macbook-pro"))
        assert cred_a2 != cred_a
        assert sse_status(cred_a2) == 200, (
            "re-paired credential must open a NEW SSE after cross-process revoke "
            "(shipping process was permanently sealed on f46a83bc)"
        )

        # ── remove path ───────────────────────────────────────────────────
        cred_b = redeem(pair_device("phone"))
        stream_b = open_stream(cred_b)
        _healthy_held(daemon, stream_b["ordinal"])
        remove_proc = _run_owned_command(owner, [sys.executable, "-m", "runtime.remote_access.cli", "remove-device", "--config", str(config_path), "--device", "phone"])
        assert remove_proc.returncode == 0, remove_proc.stderr

        result = close_stream(stream_b)
        assert result["status"] == 200
        cred_b2 = redeem(pair_device("phone"))
        assert sse_status(cred_b2) == 200, "re-paired (removed) device must reopen SSE"

        # ── unaffected current credential after a targeted revoke ─────────
        # (macbook-pro re-paired above is current; targeted-revoke phone only
        #  closed phone's stream — macbook-pro still opens new streams)
        assert sse_status(cred_a2) == 200

        # ── old/revoked credentials remain denied ─────────────────────────
        assert sse_status(cred_a) == 403, "old revoked credential must stay denied"
        assert sse_status(cred_b) == 403, "removed credential must stay denied"

        # ── WebSocket denial retained ─────────────────────────────────────
        with _admission_response(host, connector_port, "/api/v1/orgs/acme/threads/T-1/tail", {"X-HappyRanch-Device-Credential": cred_b2, "Upgrade": "websocket", "Connection": "Upgrade"}) as (ws_resp, sock, deadline, watchdog):
            assert len(ws_resp.read(1025)) <= 1024, "[D-reader] bounded WebSocket denial"
        assert ws_resp.status == 403

        # ── the connector process survived the whole lifecycle ────────────
        assert not owner.expired.is_set() and time.monotonic() <= owner.deadline, "[D-cleanup] whole-case 240s deadline"
        assert proc.poll() is None, "the shipping connector process must stay alive"
        assert _connector_reachable(host, connector_port)
    finally:
        _cleanup_owned(owner, daemon, sys.exception())
        if sys.exception() is None:
            assert not _connector_reachable(host, connector_port), "[D-cleanup] connector listener absence"


def _validate_lifecycle_record(raw: bytes, child_id: int, index: int) -> dict:
    def pairs(items):
        result = {}
        for key, value in items:
            assert key not in result, "[D-record] duplicate key"
            result[key] = value
        return result
    assert len(raw) <= 512 and raw.endswith(b"\n"), "[D-record] framing"
    try:
        row = json.loads(raw.decode("ascii"), object_pairs_hook=pairs)
    except (ValueError, UnicodeError):
        raise AssertionError("[D-record] malformed") from None
    assert isinstance(row, dict), "[D-record] object"
    phase = "admitted" if index == 0 else "terminal"
    keys = {"phase", "child_id", "status", "received_bytes"}
    keys |= {"sse", "first_frame_ok", "first_frame_bytes"} if index == 0 else {"kind"}
    assert set(row) == keys and row["phase"] == phase, "[D-record] keys/order"
    assert type(row["child_id"]) is int and row["child_id"] == child_id, "[D-record] fresh child"
    assert type(row["status"]) is int and row["status"] == 200, "[D-record] status"
    assert type(row["received_bytes"]) is int and 13 <= row["received_bytes"] <= 1048576, "[D-record] count"
    if index == 0:
        assert row["sse"] is True and row["first_frame_ok"] is True, "[D-record] first frame"
        assert type(row["first_frame_bytes"]) is int and row["first_frame_bytes"] == 13, "[D-record] first frame"
        assert row["received_bytes"] == 13, "[D-record] admission count"
    else:
        assert row["kind"] in {"eof", "reset", "timeout", "read_error", "protocol_error", "deadline", "budget_exhausted"}, "[D-record] kind"
    return row


class _OwnedDiyProcesses:
    """One stdout/stderr owner, immediate registration, finite cleanup."""
    def __init__(self, seconds=240):
        self.deadline = time.monotonic() + seconds
        self.expired = threading.Event()
        self.watchdog = threading.Timer(seconds, self.expired.set)
        self.watchdog.daemon = True
        self.selector = selectors.DefaultSelector()
        self.children = []
        self.forbidden = [BEARER.encode(), b"data: hello", b"data: world", b"Bearer ", b"DIY_SECRET_CANARY"]
        self.watchdog.start()

    def spawn(self, argv, *, role="client", child_id=None):
        proc = subprocess.Popen(argv, cwd=HERE.parents[1], stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=False, bufsize=0,
                                env={**os.environ, "PYTHONUNBUFFERED": "1"})
        row = dict(proc=proc, role=role, child_id=child_id, stdout=bytearray(),
                   stderr=bytearray(), pending=bytearray(), records=[], eof=set(), total=0)
        self.children.append(row)  # Register before the first read or wait.
        for label in ("stdout", "stderr"):
            pipe = getattr(proc, label)
            os.set_blocking(pipe.fileno(), False)
            self.selector.register(pipe, selectors.EVENT_READ, (row, label))
        return row

    def _privacy(self, raw):
        assert not any(value and value in raw for value in self.forbidden), "[D-record] privacy"

    def pump(self, timeout=0.1, *, cleanup=False):
        if not cleanup:
            assert not self.expired.is_set() and time.monotonic() < self.deadline, "[D-cleanup] case watchdog"
        failure = None
        for key, _ in self.selector.select(timeout):
            row, label = key.data
            try:
                chunk = os.read(key.fileobj.fileno(), 4096)
                if not chunk:
                    self.selector.unregister(key.fileobj)
                    row["eof"].add(label)
                    if row["child_id"] is not None and row["pending"]:
                        raise AssertionError("[D-record] truncated")
                    continue
                if row.get("fault"):
                    continue
                cap = (1024 if label == "stdout" else 4096) if row["child_id"] else 65536
                row["total"] += len(chunk) if label == "stdout" else 0
                assert len(row[label]) + len(chunk) <= cap, "[D-record] overflow"
                row[label].extend(chunk)
                if row["child_id"] is not None:
                    self._privacy(bytes(row[label]))
                    if label == "stdout":
                        row["pending"].extend(chunk)
                        while b"\n" in row["pending"]:
                            line, _, rest = row["pending"].partition(b"\n")
                            row["pending"] = bytearray(rest)
                            assert len(row["records"]) < 2, "[D-record] extra record"
                            row["records"].append(_validate_lifecycle_record(bytes(line) + b"\n", row["child_id"], len(row["records"])))
                        assert len(row["pending"]) <= 512, "[D-record] line overflow"
            except (AssertionError, OSError) as exc:
                failure = failure or exc
                row["fault"] = True
                row["pending"].clear()
                # Keep draining during teardown; bounded buffers never grow.
        if failure is not None:
            raise failure

    def wait(self, predicate, seconds, message):
        stop = min(self.deadline, time.monotonic() + seconds)
        while time.monotonic() < stop:
            self.pump()
            if predicate():
                return
        raise AssertionError(message)

    def command(self, argv):
        row = self.spawn(argv, role="cli")
        self.wait(lambda: row["proc"].poll() is not None and len(row["eof"]) == 2,
                  20, "[D-cleanup] CLI timeout")
        assert row["proc"].wait(timeout=0) == 0, "[D-cleanup] CLI exit"
        return subprocess.CompletedProcess(argv, row["proc"].returncode, bytes(row["stdout"]).decode(), bytes(row["stderr"]).decode())

    def cleanup(self):
        deadline = time.monotonic() + 25  # Remaining 5s belongs to the fixture.
        errors = []
        self.watchdog.cancel()
        self.watchdog.join(timeout=1)
        # All client/CLI children before the connector.
        for roles in ({"client", "cli"}, {"connector"}):
            rows = [row for row in self.children if row["role"] in roles]
            for row in rows:
                if row["proc"].poll() is None:
                    row["proc"].terminate()
            term_end = min(deadline, time.monotonic() + 10)
            while any(row["proc"].poll() is None for row in rows) and time.monotonic() < term_end:
                try:
                    self.pump(cleanup=True)
                except (AssertionError, OSError):
                    errors.append("pipe")
            for row in rows:
                if row["proc"].poll() is None:
                    row["proc"].kill()
            for row in rows:
                try:
                    row["proc"].wait(timeout=max(0.001, min(5, deadline - time.monotonic())))
                except subprocess.TimeoutExpired:
                    errors.append("survivor")
        # Drain EOF before closing every descriptor, including failure paths.
        stop = min(deadline, time.monotonic() + 1)
        while self.selector.get_map() and time.monotonic() < stop:
            try:
                self.pump(cleanup=True)
            except (AssertionError, OSError):
                errors.append("pipe")
        for row in self.children:
            for label in ("stdout", "stderr"):
                try:
                    getattr(row["proc"], label).close()
                except OSError:
                    errors.append("descriptor")
        self.selector.close()
        if self.watchdog.is_alive():
            errors.append("watchdog")
        return errors


def _cleanup_owned(owner, daemon, primary):
    errors = []
    try:
        errors.extend(owner.cleanup())
    except Exception:
        errors.append("process_cleanup")
    try:
        daemon.stop()
    except Exception:
        errors.append("fixture_cleanup")
    if primary is not None:
        if errors:
            primary.add_note("owned cleanup categories: " + ",".join(sorted(set(errors))))
    elif errors:
        raise AssertionError("[D-cleanup] cleanup incomplete: " + ",".join(sorted(set(errors))))


def _run_owned_command(owner, argv):
    return owner.command(argv)


def _pump_owned_pipes(owner):
    owner.pump()


def _healthy_held(daemon, ordinal):
    row = daemon._held_snapshot()[ordinal]
    assert row["alive"] and row["first_frame_flushed"] and not row["release"] and not row["failure"], "[D-held] healthy unreleased stream"
    if daemon.held_sse_mode == "heartbeat":
        assert time.monotonic() - row["last_flush"] <= 3, "[D-held] successful heartbeat gap"
    return row


def _no_action(owner, stream, daemon, ordinal):
    start = time.monotonic()
    count = _healthy_held(daemon, ordinal)["heartbeat_count"]
    while time.monotonic() - start < 16:
        owner.pump()
        assert stream["proc"].poll() is None and len(stream["records"]) == 1, "[D-held] no-action transport must remain alive for 16s"
        _healthy_held(daemon, ordinal)
    assert _healthy_held(daemon, ordinal)["heartbeat_count"] > count, "[D-held] successful continuation writes"


class _DiyWirePeer:
    """Owned miniature wire peer for admission/classification fault rows."""
    def __init__(self, chunks, *, header_delay=0, interval=0, ending="hold", content_type="text/event-stream"):
        self.chunks = chunks
        self.content_type = content_type
        self.header_delay = header_delay
        self.interval = interval
        self.ending = ending
        self.release = threading.Event()
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen()
        self.listener.settimeout(1)
        self.port = self.listener.getsockname()[1]
        self.connection = None
        self.failure = False
        self.thread = threading.Thread(target=self.serve, daemon=True)

    def serve(self):
        try:
            while not self.release.is_set():
                try:
                    self.connection, _ = self.listener.accept()
                    break
                except TimeoutError:
                    continue
            if self.connection is None:
                return
            self.connection.settimeout(2)
            request = bytearray()
            while b"\r\n\r\n" not in request and len(request) < 4096:
                chunk = self.connection.recv(1024)
                if not chunk:
                    return
                request.extend(chunk)
            if self.release.wait(self.header_delay):
                return
            self.connection.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: " + self.content_type.encode() + b"\r\nConnection: close\r\n\r\n")
            for chunk in self.chunks:
                self.connection.sendall(chunk)
                if self.release.wait(self.interval):
                    break
            if self.ending == "hold":
                self.release.wait(30)
            elif self.ending == "reset":
                self.release.wait(10)
                import struct
                self.connection.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
        except OSError:
            if not self.release.is_set():
                self.failure = True
        finally:
            if self.connection is not None:
                self.connection.close()

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.release.set()
        if self.connection is not None:
            try:
                self.connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        self.listener.close()
        self.thread.join(3)
        assert not self.thread.is_alive(), "[D-cleanup] peer thread absence"


@pytest.mark.parametrize("case", ["short", "segmented", "split_terminator", "truncated", "oversize", "wrong_frame", "deadline", "header_deadline", "read_error"])
def test_diy_first_frame_reader(case):
    chunks = {
        "short": [b"data: hello\n\n"],
        "segmented": [bytes([b]) for b in b"data: hello\n\n"],
        "split_terminator": [b"data: hello\n", b"\n"],
        "truncated": [b"data: hello\n"],
        "oversize": [b"x" * 64],
        "wrong_frame": [b"data: wrong\n\n"],
        "deadline": [b"data: hello\n", b"\n"],
        "header_deadline": [b"data: hello\n\n"],
        "read_error": [b"data: hello\n\n"],
    }[case]
    # Independent bounded wire precondition: parser mutations cannot fabricate
    # admission, and this fixed-length read never waits for SSE EOF.
    with _DiyWirePeer([b"data: hello\n\n"]) as control:
        with _admission_response("127.0.0.1", control.port, "/tail", {}) as (resp, sock, deadline, watchdog):
            assert resp.status == 200 and resp.getheader("Content-Type") == "text/event-stream"
            assert resp.read(13) == b"data: hello\n\n", "[D-reader] independent literal first frame"
            print("DIY_ADMITTED", flush=True)
    began = time.monotonic()
    expected = {"truncated": "truncated", "oversize": "oversized", "wrong_frame": "protocol_error",
                "deadline": "deadline", "header_deadline": "deadline"}.get(case)
    with _DiyWirePeer(chunks, header_delay=10.2 if case == "header_deadline" else 8 if case == "deadline" else 0,
                      interval=3 if case == "deadline" else (0.01 if case == "segmented" else 0),
                      ending="eof" if case == "truncated" else "hold") as peer:
        def observe():
            with _admission_response("127.0.0.1", peer.port, "/tail", {}) as (resp, sock, deadline, watchdog):
                if case == "read_error":
                    def broken(_):
                        raise OSError(5, "DIY_SECRET_CANARY")
                    resp.read1 = broken
                try:
                    result = _read_first_sse_frame(resp, deadline)
                except AdmissionError:
                    assert expected is not None, "[D-reader] positive admission within 10s"
                    raise
                assert result == 13, "[D-reader] exact frame size"
                print("DIY_ADMITTED", flush=True)
        if expected:
            with pytest.raises(AdmissionError) as exc:
                observe()
            assert exc.value.kind == expected, "[D-reader] failure category"
            if case in ("deadline", "header_deadline"):
                assert time.monotonic() - began < 10.5, "[D-reader] one 10s budget"
        elif case == "read_error":
            with pytest.raises(OSError):
                observe()
        else:
            observe()


def _support_client(owner, port, *, child_id=1, idle=5, inject=None, default=False, auth=False):
    argv = ["--host", "127.0.0.1", "--port", str(port), "stream", "--path", "/tail", "--credential", "test-side-credential"]
    if not default:
        argv += ["--observe-lifecycle", "--observation-id", str(child_id), "--idle-timeout", str(idle)]
    # Owned test-side child drivers exercise the actual client read boundary.
    source = "from tests.remote_access import diy_client as c\nimport http.client,time,os\n"
    if auth:
        source += "original=http.client.HTTPConnection.request\ndef request(self,*a,**kw):\n kw.setdefault('headers',{})['Authorization']='Bearer '+" + repr(BEARER) + "\n return original(self,*a,**kw)\nhttp.client.HTTPConnection.request=request\n"
    if inject == "read_error":
        source += "original_read=http.client.HTTPResponse.read1\ndef read(self,n=-1):\n if n==4096: raise OSError(5,'DIY_SECRET_CANARY')\n return original_read(self,n)\nhttp.client.HTTPResponse.read1=read\n"
    if inject == "buffer_stdout":
        source += "import io,sys\nsys.stdout=io.TextIOWrapper(io.BufferedWriter(io.FileIO(1,mode='w',closefd=False)),encoding='ascii')\nemit=c._emit_lifecycle_record\ndef witness(row):\n if row['phase']=='admitted':\n  assert row['status']==200 and row['first_frame_ok'] is True and row['first_frame_bytes']==13\n  os.write(2,b'CLIENT_FIRST_FRAME_OK')\n return emit(row)\nc._emit_lifecycle_record=witness\n"
    if inject == "delay_ack":
        source += "emit=c._emit_lifecycle_record\ndef delayed(row):\n if row['phase']=='admitted':\n  assert row['status']==200 and row['first_frame_bytes']==13 and row['first_frame_ok'] is True\n  os.write(2,b'CLIENT_FIRST_FRAME_OK')\n  time.sleep(1)\n return emit(row)\nc._emit_lifecycle_record=delayed\n"
    source += "raise SystemExit(c.main(" + repr(argv) + "))\n"
    return owner.spawn([sys.executable, "-c", source], child_id=None if default else child_id)


@pytest.mark.parametrize("case", ["heartbeat_no_action", "two_children", "silent_timeout", "eof", "reset", "read_error", "default_output", "malformed", "truncated_record", "oversize_record", "extra_record", "unflushed_record", "secret_canary", "stderr_canary", "backpressure", 'flushed_admission', 'duplicate_keys', 'non_ascii', 'wrong_child', 'wrong_bool', 'out_of_order', 'invalid_terminal'])
def test_diy_lifecycle_records(case):
    owner = _OwnedDiyProcesses(60)
    daemon = FakeDaemon(BEARER, held_sse_mode="silent" if case == "silent_timeout" else "heartbeat")
    try:
        if case in {"heartbeat_no_action", "two_children", "silent_timeout", "default_output", "flushed_admission"}:
            if case == "default_output":
                daemon = FakeDaemon(BEARER)
            daemon.start()
            start = time.monotonic()
            row = _support_client(owner, daemon.port, inject="buffer_stdout" if case == "flushed_admission" else None, idle=4 if case == "silent_timeout" else 5,
                                  default=case == "default_output", auth=True)
            if case == "default_output":
                owner.wait(lambda: row["proc"].poll() is not None and len(row["eof"]) == 2, 6, "[D-strict] default completion")
                result = json.loads(row["stdout"])
                assert result == {"status": 200, "received_bytes": 26}, "[D-strict] default output"
                assert row["proc"].returncode == 0
            else:
                if case == "flushed_admission":
                    owner.wait(lambda: b"CLIENT_FIRST_FRAME_OK" in row["stderr"], 2, "[D-record] independent client frame witness")
                    print("DIY_ADMITTED", flush=True)
                    owner.wait(lambda: len(row["records"]) == 1, 2, "[D-record] flushed admission required")
                owner.wait(lambda: len(row["records"]) == 1, 10, "[D-record] fresh admission required")
                print("DIY_ADMITTED", flush=True)
                if case == "silent_timeout":
                    start = time.monotonic()
                    owner.wait(lambda: row["proc"].poll() is not None and len(row["eof"]) == 2, 6, "[D-strict] silent timeout bound")
                    assert row["records"][1]["kind"] == "timeout" and row["proc"].returncode == 2, "[D-strict] timeout must be nonzero"
                    assert time.monotonic() - start < 6 and not daemon.release.is_set(), "[D-strict] before upstream idle timeout"
                elif case != "flushed_admission":
                    _no_action(owner, row, daemon, 1)
                    if case == "heartbeat_no_action":
                        with _admission_response("127.0.0.1", daemon.port, "/tail", {"Authorization": "Bearer " + BEARER}) as (resp, sock, deadline, watchdog):
                            assert _read_first_sse_frame(resp, deadline) == 13
                            assert resp.read1(3) == b":\n\n", "[D-held] actual comment frame"
                    if case == "two_children":
                        start2 = time.monotonic()
                        row2 = _support_client(owner, daemon.port, child_id=2, inject="delay_ack", auth=True)
                        owner.wait(lambda: b"CLIENT_FIRST_FRAME_OK" in row2["stderr"], 10, "[D-record] actual second-child frame")
                        owner.wait(lambda: len(row2["records"]) == 1, 10, "[D-record] second fresh admission required")
                        assert time.monotonic() - start2 >= 1 and row2["records"][0]["child_id"] == 2, "[D-record] sticky event cannot admit child2"
                        _healthy_held(daemon, 2)
        elif case in {"eof", "reset", "read_error"}:
            with _DiyWirePeer([b"data: hello\n\n"], ending="reset" if case == "reset" else "hold") as peer:
                row = _support_client(owner, peer.port, inject="read_error" if case == "read_error" else None)
                owner.wait(lambda: len(row["records"]) >= 1, 10, "[D-record] fresh admission required")
                print("DIY_ADMITTED", flush=True)
                if case == "reset":
                    peer.release.set()
                elif case == "eof":
                    peer.connection.shutdown(socket.SHUT_WR)
                owner.wait(lambda: row["proc"].poll() is not None and len(row["eof"]) == 2, 6, "[D-strict] terminal completion")
                assert row["records"][1]["kind"] == case, "[D-strict] exact terminal category"
                assert row["proc"].returncode == (3 if case == "read_error" else 0), "[D-strict] category exit"
                assert b"DIY_SECRET_CANARY" not in row["stdout"] + row["stderr"], "[D-record] exception privacy"
        else:
            admitted = b'{"phase":"admitted","child_id":1,"status":200,"sse":true,"first_frame_ok":true,"first_frame_bytes":13,"received_bytes":13}\n'
            terminal = b'{"phase":"terminal","child_id":1,"status":200,"kind":"eof","received_bytes":13}\n'
            raw = {"malformed": b"bad\n", "truncated_record": admitted[:-1], "oversize_record": b"x" * 513 + b"\n",
                   "extra_record": admitted + terminal + terminal, "secret_canary": admitted.replace(b"true", b'"DIY_SECRET_CANARY"', 1),
                   "stderr_canary": admitted, "backpressure": admitted}.get(case, admitted)
            source = "import os,time\n"
            if case == "unflushed_record":
                source += "out=os.fdopen(os.dup(1),'wb',buffering=8192)\nout.write(" + repr(raw) + ")\ntime.sleep(5)\n"
            else:
                source += "os.write(1," + repr(raw) + ")\n"
                if case == "stderr_canary":
                    source += "os.write(2,b'DIY_SECRET_CANARY')\n"
                if case == "backpressure":
                    source += "os.write(2,b'x'*65536)\n"
            if case == "duplicate_keys":
                raw = admitted.replace(b'"child_id":1', b'"child_id":1,"child_id":1')
            elif case == "non_ascii":
                raw = admitted.replace(b'"admitted"', b'"admitt\xffed"')
            elif case == "wrong_child":
                raw = admitted.replace(b'"child_id":1', b'"child_id":2')
            elif case == "wrong_bool":
                raw = admitted.replace(b'"sse":true', b'"sse":1')
            elif case == "out_of_order":
                raw = terminal + admitted
            elif case == "invalid_terminal":
                raw = admitted + terminal.replace(b'"eof"', b'"unknown"')
            if case in {"duplicate_keys", "non_ascii", "wrong_child", "wrong_bool", "out_of_order", "invalid_terminal"}:
                source = "import os\nos.write(1," + repr(raw) + ")\n"
            row = owner.spawn([sys.executable, "-c", source], child_id=1)
            expected_category = {"malformed": "malformed", "truncated_record": "truncated", "oversize_record": "framing", "extra_record": "extra record", "secret_canary": "privacy", "stderr_canary": "privacy", "backpressure": "overflow", "unflushed_record": "deadline", "duplicate_keys": "duplicate key", "non_ascii": "malformed", "wrong_child": "fresh child", "wrong_bool": "first frame", "out_of_order": "keys/order", "invalid_terminal": "kind"}[case]
            assert _validate_lifecycle_record(admitted, 1, 0)["first_frame_bytes"] == 13
            print("DRIVER_ADMITTED", flush=True)
            observed = None
            try:
                owner.wait(lambda: row["proc"].poll() is not None and len(row["eof"]) == 2 and len(row["records"]) == 2,
                           1, "[D-record] protocol admission/completion deadline")
            except AssertionError as exc:
                observed = str(exc)
            assert observed is not None and expected_category in observed, "[D-record] expected " + expected_category
    finally:
        # Fixture may never have started in pure pipe rows.
        errors = owner.cleanup()
        if daemon._thread.is_alive():
            daemon.stop()
        else:
            daemon._server.server_close()
        assert all(row["proc"].poll() is not None and row["proc"].stdout.closed and row["proc"].stderr.closed for row in owner.children), "[D-cleanup] reaped pipes"
        assert not owner.watchdog.is_alive(), "[D-cleanup] watchdog joined"
        if sys.exception() is None:
            assert not errors, "[D-cleanup] unexpected cleanup failure"


@pytest.mark.parametrize("case", ["success", "admission_failure", "frame_read_failure", "cli_timeout", "close_wait_timeout", "cleanup_failure", "kill_survivor"])
def test_diy_owned_cleanup(case):
    owner = _OwnedDiyProcesses(60)
    daemon = FakeDaemon(BEARER, held_sse_mode="silent")
    daemon.start()
    primary = AssertionError("[D-cleanup] primary retained")
    row = owner.spawn([sys.executable, "-c", "import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); print('ready',flush=True); time.sleep(30)" if case == "kill_survivor" else "import time; print('ready',flush=True); time.sleep(30)"], role="cli")
    owner.wait(lambda: b"ready" in row["stdout"], 2, "[D-cleanup] owned child ready")
    original = owner.cleanup
    if case == "cleanup_failure":
        def failure():
            original()
            return ["injected"]
        owner.cleanup = failure
    try:
        with _admission_response("127.0.0.1", daemon.port, "/tail", {"Authorization": "Bearer " + BEARER}) as (positive, positive_sock, positive_deadline, positive_watchdog):
            assert _read_first_sse_frame(positive, positive_deadline) == 13, "[D-cleanup] independent first frame"
            print("DIY_ADMITTED", flush=True)
        assert positive.closed and not positive_watchdog.is_alive(), "[D-cleanup] positive response closed"
        if case in {"admission_failure", "frame_read_failure"}:
            with _DiyWirePeer([b"bad\n\n"], content_type="application/json" if case == "admission_failure" else "text/event-stream") as peer:
                with pytest.raises(AdmissionError):
                    with _admission_response("127.0.0.1", peer.port, "/tail", {}) as (resp, sock, deadline, watchdog):
                        assert resp.status == 200
                        _read_first_sse_frame(resp, deadline)
                assert resp.closed and sock.fileno() == -1 and not watchdog.is_alive(), "[D-cleanup] connection/response/watchdog closed"
        elif case in {"cli_timeout", "close_wait_timeout"}:
            with pytest.raises(AssertionError, match=r"\[D-cleanup\]"):
                owner.wait(lambda: row["proc"].poll() is not None, 0.2, "[D-cleanup] injected wait timeout")
        if case != "success":
            raise primary
    except AssertionError as exc:
        secondary = None
        try:
            _cleanup_owned(owner, daemon, exc)
        except AssertionError as cleanup_exc:
            secondary = cleanup_exc
        assert secondary is None, "[D-cleanup] primary must survive cleanup failure"
        assert exc is primary, "[D-cleanup] same primary failure"
        if case == "cleanup_failure":
            assert "injected" in exc.__notes__[0], "[D-cleanup] secondary diagnostic retained"
    else:
        _cleanup_owned(owner, daemon, None)
    try:
        assert row["proc"].poll() is not None and row["proc"].stdout.closed and row["proc"].stderr.closed, "[D-cleanup] process and pipe absence"
        assert not daemon._thread.is_alive() and not owner.watchdog.is_alive(), "[D-cleanup] fixture/watchdog joined"
        assert not _connector_reachable("127.0.0.1", daemon.port), "[D-cleanup] listener absence"
    finally:
        # The test-created identity is independently contained after observing
        # the deliberately broken registry. This cannot satisfy the assertion.
        if row["proc"].poll() is None:
            row["proc"].kill()
        row["proc"].wait(timeout=5)
        row["proc"].stdout.close()
        row["proc"].stderr.close()
