"""Inert B14 unit rendering and fixed shipped-policy renewal decisions.

No reader, writer, lock acquisition, installer or executable is wired here.
The eventual root entry must hold the installation lock while supplying fresh
protected inputs and atomically publishing a successful candidate.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta

from runtime.remote_access.policy import (
    PolicyEnvelope,
    PolicyError,
    RoutePolicyConsumer,
    canonical_json,
)

SERVICE_NAME = "happyranch-remote-access-policy-renew.service"
TIMER_NAME = "happyranch-remote-access-policy-renew.timer"
# Reserved using the packaged helper's bin convention; entry packaging deferred.
RENEW_EXECUTABLE = "/opt/happyranch-desktop/bin/happyranch-remote-access-policy-renew"
POLICY_MAX_AGE_SECONDS = 3600


def render_service() -> str:
    """Render the fixed root oneshot; no inputs or selectable command arguments."""
    return "\n".join([
        "[Unit]",
        "Description=HappyRanch fixed route-policy renewal",
        "PartOf=happyranch-managed.target",
        "",
        "[Service]",
        "Type=oneshot",
        "User=root",
        "Group=happyranch",
        f"ExecStart={RENEW_EXECUTABLE}",
        "NoNewPrivileges=yes",
        "ProtectSystem=strict",
        "ProtectHome=yes",
        "PrivateTmp=yes",
        "PrivateDevices=yes",
        "ProtectKernelTunables=yes",
        "ProtectKernelModules=yes",
        "ProtectKernelLogs=yes",
        "ProtectControlGroups=yes",
        "RestrictSUIDSGID=yes",
        "RestrictRealtime=yes",
        "LockPersonality=yes",
        "MemoryDenyWriteExecute=yes",
        "SystemCallArchitectures=native",
        "RestrictAddressFamilies=AF_UNIX",
        "IPAddressDeny=any",
        "CapabilityBoundingSet=",
        "AmbientCapabilities=",
        "UMask=0077",
        "ReadWritePaths=/etc/happyranch /var/lib/happyranch-remote-access",
        "TasksMax=16",
        "MemoryMax=96M",
        "LimitNOFILE=128",
        "TimeoutStartSec=30",
        "TimeoutStopSec=5",
        "KillMode=control-group",
        "StandardOutput=null",
        "StandardError=null",
        "",
    ])


def render_timer() -> str:
    """Render the settled cadence, without installing or enabling anything."""
    return "\n".join([
        "[Unit]",
        "Description=HappyRanch fixed route-policy renewal timer",
        "PartOf=happyranch-managed.target",
        "",
        "[Timer]",
        "OnUnitActiveSec=1800s",
        "OnBootSec=60s",
        "Persistent=false",
        f"Unit={SERVICE_NAME}",
        "",
        "[Install]",
        "WantedBy=happyranch-managed.target",
        "",
    ])


def renewal_candidate(
    installed: PolicyEnvelope | None,
    shipped_artifact: dict | None,
    *,
    now: datetime,
) -> PolicyEnvelope | None:
    """Return a new 3600s envelope only for the current identical shipped policy.

    Inputs are decoded protected evidence, never caller-selected policy. The
    digest uses policy.py's canonical artifact bytes, excluding the operational
    envelope. None means absent/withdrawn evidence or refusal: leave the installed
    expiry untouched and wait for the next tick. Expired policy is not revived
    by this automatic action. The supplied envelope is never mutated.
    """
    if installed is None or shipped_artifact is None:
        return None
    try:
        if (now.utcoffset() is None or installed.issued_at.utcoffset() is None
                or installed.max_age_seconds != POLICY_MAX_AGE_SECONDS
                or installed.revision < 1
                or now < installed.issued_at
                or now >= installed.issued_at + timedelta(seconds=POLICY_MAX_AGE_SECONDS)):
            return None
        digest = hashlib.sha256(canonical_json(shipped_artifact)).hexdigest()
        consumer = RoutePolicyConsumer.from_envelope(installed, pinned_digest=digest)
        consumer.require_current(now)
    except (PolicyError, KeyError, TypeError, ValueError, OverflowError):
        return None
    return installed.model_copy(deep=True, update={
        "issued_at": now,
        "revision": installed.revision + 1,
        "max_age_seconds": POLICY_MAX_AGE_SECONDS,
    })
