"""Standalone B14 renderer/decision contracts; no host services are contacted.

Run: PYTHONPATH=$PWD uv run --frozen pytest scripts/test_policy_renew_units.py -v --tb=short
"""
from __future__ import annotations

import copy
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from runtime.remote_access import policy_renew_units as renew
from runtime.remote_access.policy import PolicyEnvelope, PolicyError, RoutePolicyConsumer

NOW = datetime(2026, 11, 18, 12, tzinfo=timezone.utc)
SERVICE_GOLDEN = b"""[Unit]
Description=HappyRanch fixed route-policy renewal
PartOf=happyranch-managed.target

[Service]
Type=oneshot
User=root
Group=happyranch
ExecStart=/opt/happyranch-desktop/bin/happyranch-remote-access-policy-renew
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
PrivateDevices=yes
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectKernelLogs=yes
ProtectControlGroups=yes
RestrictSUIDSGID=yes
RestrictRealtime=yes
LockPersonality=yes
MemoryDenyWriteExecute=yes
SystemCallArchitectures=native
RestrictAddressFamilies=AF_UNIX
IPAddressDeny=any
CapabilityBoundingSet=
AmbientCapabilities=
UMask=0077
ReadWritePaths=/etc/happyranch /var/lib/happyranch-remote-access
TasksMax=16
MemoryMax=96M
LimitNOFILE=128
TimeoutStartSec=30
TimeoutStopSec=5
KillMode=control-group
StandardOutput=null
StandardError=null
"""
TIMER_GOLDEN = b"""[Unit]
Description=HappyRanch fixed route-policy renewal timer
PartOf=happyranch-managed.target

[Timer]
OnUnitActiveSec=1800s
OnBootSec=60s
Persistent=false
Unit=happyranch-remote-access-policy-renew.service

[Install]
WantedBy=happyranch-managed.target
"""


@pytest.fixture
def artifact() -> dict:
    return json.loads((Path(__file__).resolve().parents[1]
                       / "runtime/remote_access/route_policy.json").read_bytes())


@pytest.fixture
def installed(artifact: dict) -> PolicyEnvelope:
    return PolicyEnvelope(schema_version=1, artifact_version=1, artifact=copy.deepcopy(artifact),
                          issued_at=NOW - timedelta(seconds=1800), max_age_seconds=3600,
                          revision=5, state="active")


def test_service_golden_and_fixed_surface() -> None:
    assert renew.SERVICE_NAME == "happyranch-remote-access-policy-renew.service"
    assert renew.RENEW_EXECUTABLE == "/opt/happyranch-desktop/bin/happyranch-remote-access-policy-renew"
    rendered = renew.render_service()
    assert rendered.encode() == SERVICE_GOLDEN
    assert renew.render_service() == rendered
    assert "Environment" not in rendered
    assert "LoadCredential" not in rendered
    assert "SetCredential" not in rendered
    assert "://" not in rendered
    assert "%" not in rendered and "$" not in rendered
    assert [line for line in rendered.splitlines() if line.startswith("Exec")] == [
        "ExecStart=/opt/happyranch-desktop/bin/happyranch-remote-access-policy-renew"]


def test_timer_golden_and_fixed_cadence() -> None:
    assert renew.TIMER_NAME == "happyranch-remote-access-policy-renew.timer"
    rendered = renew.render_timer()
    assert rendered.encode() == TIMER_GOLDEN
    assert renew.render_timer() == rendered
    assert "Environment" not in rendered
    assert "OnUnitActiveSec=1800s\nOnBootSec=60s\nPersistent=false\n" in rendered


def test_matching_policy_restamps_without_mutation(installed: PolicyEnvelope, artifact: dict) -> None:
    before = installed.model_dump(mode="json")
    shipped_before = copy.deepcopy(artifact)
    candidate = renew.renewal_candidate(installed, artifact, now=NOW)
    assert candidate is not None and candidate is not installed
    assert candidate.model_dump(mode="json") == {
        **before, "issued_at": "2026-11-18T12:00:00Z", "revision": 6, "max_age_seconds": 3600}
    assert installed.model_dump(mode="json") == before
    assert artifact == shipped_before
    consumer = RoutePolicyConsumer.from_envelope(candidate)
    consumer.require_current(NOW + timedelta(seconds=3599))
    with pytest.raises(PolicyError) as error:
        consumer.require_current(NOW + timedelta(seconds=3601))
    assert error.value.outcome.reason == "stale"


@pytest.mark.parametrize("side", ["installed", "shipped"])
@pytest.mark.parametrize("change", ["description", "route"])
def test_changed_artifact_refused(installed: PolicyEnvelope, artifact: dict,
                                  side: str, change: str) -> None:
    changed = installed.artifact if side == "installed" else artifact
    if change == "description":
        changed["description"] += " changed during upgrade"
    else:
        changed["allow"].append({"method": "GET", "path_template": "/synthetic-new-route"})
    # Both variants are structurally valid/current: digest mismatch must own denial.
    RoutePolicyConsumer.from_envelope(installed).require_current(NOW)
    before = installed.model_dump(mode="json")
    assert renew.renewal_candidate(installed, artifact, now=NOW) is None
    assert installed.model_dump(mode="json") == before


@pytest.mark.parametrize("elapsed,accepted", [(-1, False), (0, True), (3599, True),
                                             (3600, False), (3601, False)])
def test_automatic_expiry_boundary(installed: PolicyEnvelope, artifact: dict,
                                   elapsed: int, accepted: bool) -> None:
    installed.issued_at = NOW - timedelta(seconds=elapsed)
    before = installed.model_dump(mode="json")
    candidate = renew.renewal_candidate(installed, artifact, now=NOW)
    assert (candidate is not None) is accepted
    assert installed.model_dump(mode="json") == before


@pytest.mark.parametrize("missing", ["installed", "shipped", "both"])
def test_missing_or_withdrawn_refused(installed: PolicyEnvelope, artifact: dict, missing: str) -> None:
    before = installed.model_dump(mode="json")
    assert renew.renewal_candidate(None if missing in {"installed", "both"} else installed,
        None if missing in {"shipped", "both"} else artifact, now=NOW) is None
    assert installed.model_dump(mode="json") == before


@pytest.mark.parametrize("state", ["empty", "malformed", "compiler_failed", "apply_failed", "withdrawn"])
def test_inactive_policy_refused(installed: PolicyEnvelope, artifact: dict, state: str) -> None:
    installed.state = state
    before = installed.model_dump(mode="json")
    assert renew.renewal_candidate(installed, artifact, now=NOW) is None
    assert installed.model_dump(mode="json") == before


@pytest.mark.parametrize("invalid", ["schema", "artifact_version", "artifact_shape", "empty_routes",
                                     "short_envelope", "long_envelope", "revision", "naive_issued", "naive_now"])
def test_invalid_evidence_refused(installed: PolicyEnvelope, artifact: dict, invalid: str) -> None:
    now = NOW
    if invalid == "schema":
        installed.schema_version = 2
    elif invalid == "artifact_version":
        installed.artifact_version = 2
    elif invalid == "artifact_shape":
        installed.artifact["normalization"] = None
        artifact["normalization"] = None
    elif invalid == "empty_routes":
        installed.artifact["allow"] = []
        artifact["allow"] = []
    elif invalid in {"short_envelope", "long_envelope"}:
        installed.max_age_seconds = 3599 if invalid == "short_envelope" else 3601
    elif invalid == "revision":
        installed.revision = 0
    elif invalid == "naive_issued":
        installed.issued_at = installed.issued_at.replace(tzinfo=None)
    else:
        now = NOW.replace(tzinfo=None)
    before = installed.model_dump(mode="json")
    assert renew.renewal_candidate(installed, artifact, now=now) is None
    assert installed.model_dump(mode="json") == before


@pytest.mark.parametrize('elapsed,accepted', [(-1, False), (0, True), (3600, True), (7200, True)])
def test_fresh_auth_can_revive_only_identical_valid_policy(installed: PolicyEnvelope, artifact: dict,
                                                         elapsed: int, accepted: bool) -> None:
    now = installed.issued_at + timedelta(seconds=elapsed)
    candidate = renew.fresh_auth_renewal_candidate(installed, artifact, now=now)
    assert (candidate is not None) == accepted
    if candidate is not None:
        assert (candidate.revision, candidate.issued_at, candidate.max_age_seconds) == (6, now, 3600)
        assert candidate.artifact == artifact
    changed = copy.deepcopy(artifact)
    changed['version'] = 999
    assert renew.fresh_auth_renewal_candidate(installed, changed, now=now) is None
    assert renew.fresh_auth_renewal_candidate(installed.model_copy(update={'state': 'withdrawn'}), artifact, now=now) is None


def test_fixed_renewal_entry_and_launcher(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    import os
    import runpy
    import sys
    from runtime.remote_access import policy_renew
    calls = []
    monkeypatch.setattr(sys, 'argv', ['fixed-entry'])
    monkeypatch.setattr(os, 'geteuid', lambda: 0)
    class Installer:
        def __init__(self, **kwargs: object) -> None:
            assert kwargs == {'layout': 'deb'}
        def renew_policy(self, **kwargs: object) -> None:
            calls.append(kwargs)
    monkeypatch.setattr(policy_renew, 'ManagedInstaller', Installer)
    assert policy_renew.main() == 0
    assert calls == [{}], 'timer must use automatic mode only'
    def refuse(self: object, **kwargs: object) -> None:
        raise ValueError('synthetic-private-path-and-input')
    monkeypatch.setattr(Installer, 'renew_policy', refuse)
    assert policy_renew.main() == 1
    assert capsys.readouterr().err == 'renewal-refused\n'
    monkeypatch.setattr(os, 'geteuid', lambda: 1000)
    assert policy_renew.main() == 1
    assert capsys.readouterr().err == 'root-required\n'
    launcher = Path(__file__).resolve().parents[1] / 'packaging/linux/bin/happyranch-remote-access-policy-renew'
    for selector in ('--help', '/tmp/policy', 'https://untrusted.invalid'):
        monkeypatch.setattr(sys, 'argv', ['fixed-entry', selector])
        assert policy_renew.main() == 2
        assert capsys.readouterr().err == 'invalid-request\n'
        with pytest.raises(SystemExit, match='invalid-request'):
            runpy.run_path(str(launcher), run_name='__main__')
    monkeypatch.setattr(sys, 'argv', ['fixed-entry'])
    monkeypatch.setenv('PYTHONPATH', '/untrusted')
    monkeypatch.setenv('POLICY_PATH', '/untrusted')
    class Executed(BaseException):
        pass
    def execute(path: str, argv: tuple[str, ...], env: dict[str, str]) -> None:
        assert path == '/opt/happyranch-desktop/python/bin/python3'
        assert argv == (path, '-I', '-m', 'runtime.remote_access.policy_renew')
        assert env == {}
        raise Executed()
    monkeypatch.setattr(os, 'execve', execute)
    with pytest.raises(Executed):
        runpy.run_path(str(launcher), run_name='__main__')



def test_packaged_renewal_units_are_exact_renderer_bytes() -> None:
    folder = Path(__file__).resolve().parents[1] / 'packaging/linux/systemd'
    assert (folder / renew.SERVICE_NAME).read_bytes() == SERVICE_GOLDEN == renew.render_service().encode()
    assert (folder / renew.TIMER_NAME).read_bytes() == TIMER_GOLDEN == renew.render_timer().encode()
