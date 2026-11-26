"""Least-privilege systemd unit rendering (THR-097 phase unit 3).

Renders connector/sidecar units and their target. systemd owns recovery:

- a dedicated unprivileged service user (system mode) or the calling user
  (user mode — no ``User=``/``Group=`` allowed in user units);
- zero capabilities (``CapabilityBoundingSet=``/``AmbientCapabilities=``
  empty), ``NoNewPrivileges=yes``, ``ProtectSystem=strict`` with explicit
  state/log/runtime directories, ``ProtectHome=yes``, private tmp/dev,
  restricted address families, SUID/SGID + realtime + personality +
  write-execute-memory restrictions;
- the daemon bearer is injected into the unit ONLY via ``LoadCredential=``
  (the daemon token file path, never the token value) so the service user
  never needs direct access to the daemon home;
- ``Type=notify`` + ``WatchdogSec=`` (the supervisor reports READY/WATCHDOG
  through sd_notify) and ``Restart=on-failure`` for crash recovery.

Rendering is pure and deterministic. The managed Linux verifier is a separate
root evidence reader; its capabilities never extend the connector account.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# Canonical least-privilege directive set. Every directive is load-bearing
# for the unit's posture; the checked-in tests guard each line exactly.
_SYSTEM_HARDENING: tuple[str, ...] = (
    "Type=notify",
    "Restart=on-failure",
    "NoNewPrivileges=yes",
    "PrivateTmp=yes",
    "ProtectSystem=strict",
    "ProtectHome=yes",
    "ProtectKernelTunables=yes",
    "ProtectControlGroups=yes",
    "RestrictSUIDSGID=yes",
    "RestrictRealtime=yes",
    "LockPersonality=yes",
    "MemoryDenyWriteExecute=yes",
    "SystemCallArchitectures=native",
    "UMask=0077",
)

# System-mode-only directives. The USER manager cannot apply these: an empty
# capability bounding set means dropping capabilities the user manager does
# not itself hold (EPERM), and ``PrivateDevices=``/``ProtectKernelModules=``
# require namespace mounts with elevated privileges the user manager lacks —
# a user unit carrying them fails to start on real systemd (verified on
# Ubuntu 26.04 systemd, THR-097 unit 3 conformance). The system service
# (with a dedicated unprivileged service user) gets the full set; user-mode
# units omit all four.
_SYSTEM_ONLY_HARDENING: tuple[str, ...] = (
    "PrivateDevices=yes",
    "ProtectKernelModules=yes",
    "CapabilityBoundingSet=",
    "AmbientCapabilities=",
)

_DEFAULT_WATCHDOG_SEC = 30
_DEFAULT_RESTART_SEC = 1
_MANAGED_TARGET = "happyranch-managed.target"
_START_LIMIT = ("StartLimitIntervalSec=300", "StartLimitBurst=5")

# Keep the rendered verifier namespace and installer admission in one place.
# Paths are hidden/inaccessible per systemd.exec; read-only mounts still allow
# evidence reads. New filesystem directives must record their hidden paths here.
VERIFIER_FILESYSTEM_HARDENING: tuple[tuple[str, tuple[str, ...]], ...] = (
    ('ProtectSystem=strict', ()),
    ('ProtectHome=read-only', ()),
    ('PrivateTmp=yes', ('/tmp', '/var/tmp')),
    ('PrivateDevices=yes', ('/dev',)),
    ('ProtectKernelTunables=yes', ('/proc/kallsyms', '/proc/kcore')),
    ('ProtectKernelModules=yes', ('/usr/lib/modules',)),
    ('ProtectKernelLogs=yes', ('/proc/kmsg', '/dev/kmsg')),
    ('ProtectControlGroups=yes', ()),
)


def _hardening(*, system: bool, sidecar: bool = False) -> list[str]:
    # tsnet's Linux netmon subscribes to NETLINK_ROUTE for interface changes.
    families = "AF_INET AF_INET6 AF_UNIX" + (" AF_NETLINK" if sidecar else "")
    return [
        *_SYSTEM_HARDENING,
        f"RestrictAddressFamilies={families}",
        *(_SYSTEM_ONLY_HARDENING if system else ()),
    ]


@dataclass(frozen=True)
class ConnectorUnitSpec:
    """Everything needed to render the connector service unit."""

    unit_name: str = "happyranch-connector.service"
    exec_start: tuple[str, ...] = (
        "/opt/happyranch/venv/bin/python",
        "-m",
        "runtime.remote_access.cli",
        "run",
    )
    system: bool = True
    release_verifier: bool = False
    ingress_profile: str = ""
    user: str | None = None
    group: str | None = None
    daemon_token_path: str = ""
    state_dir: str = "happyranch-connector"
    run_dir: str = "happyranch-connector"
    logs_dir: str = "happyranch-connector"
    watchdog_sec: int = _DEFAULT_WATCHDOG_SEC
    restart_sec: int = _DEFAULT_RESTART_SEC
    extra_environment: tuple[str, ...] = field(default_factory=tuple)


def _quote_systemd_arg(arg: str) -> str:
    """Quote one ExecStart argument per systemd's simple quoting rules.

    systemd single-quotes words containing spaces or quotes; an embedded
    single quote is escaped as ``''`` inside a quoted word.
    """
    if any(c in arg for c in " \t'\"\\\n"):
        return "'" + arg.replace("'", "''") + "'"
    return arg


def render_connector_unit(spec: ConnectorUnitSpec) -> str:
    """Render the connector systemd unit file deterministically."""
    if spec.ingress_profile not in ("", "linux-managed-identity-v1"):
        raise ValueError("unknown ingress profile")
    if spec.ingress_profile and (not spec.system or spec.run_dir != "happyranch-connector"
                                 or spec.user != "happyranch" or spec.group != "happyranch"):
        raise ValueError("identity ingress requires the fixed system service")
    lines: list[str] = []
    lines.append("[Unit]")
    lines.append("Description=HappyRanch remote-access connector (supervised Linux home connector)")
    lines.append("After=network-online.target")
    lines.append("Wants=network-online.target")
    # A missing desktop daemon is an ordinary, potentially long startup wait.
    # BindsTo/Upholds dependency starts also consume the burst; NRestarts alone
    # understates it. Retain the readiness gate and bounded start attempt, but
    # never strand this fixed managed profile after its start timeouts.
    lines.extend(("StartLimitIntervalSec=0", "StartLimitBurst=5")
                 if spec.system and spec.release_verifier and spec.ingress_profile
                 else _START_LIMIT)
    lines.append(f"PartOf={_MANAGED_TARGET}")
    if spec.system:
        if spec.release_verifier:
            lines.append("Requires=happyranch-release-verifier.socket")
            lines.append("After=happyranch-release-verifier.socket")
        # Re-pull the BindsTo sidecar when the connector recovers (systemd >= 249).
        lines.append("Upholds=happyranch-tsnet-sidecar.service")
    lines.append("")
    lines.append("[Service]")
    exec_line = " ".join(_quote_systemd_arg(arg) for arg in spec.exec_start)
    lines.append(f"ExecStart={exec_line}")
    if spec.system:
        if not spec.user or not spec.group:
            raise ValueError("system-mode connector unit requires user and group")
        lines.append(f"User={spec.user}")
        lines.append(f"Group={spec.group}")
    lines.append(f"RestartSec={spec.restart_sec}")
    lines.append(f"WatchdogSec={spec.watchdog_sec}")
    lines.append("TimeoutStopSec=10")
    lines.append("TimeoutStartSec=120")
    lines.append("NotifyAccess=main")
    lines.extend(_hardening(system=spec.system))
    lines.append(f"StateDirectory={spec.state_dir}")
    lines.append(f"RuntimeDirectory={spec.run_dir}")
    if spec.ingress_profile:
        lines.append("RuntimeDirectoryMode=0700")
    lines.append(f"LogsDirectory={spec.logs_dir}")
    if spec.daemon_token_path:
        # The token VALUE is never rendered — only the file path, which
        # systemd copies into $CREDENTIALS_DIRECTORY at unit start.
        lines.append(f"LoadCredential=daemon.token:{spec.daemon_token_path}")
    for env in spec.extra_environment:
        lines.append(f"Environment={env}")
    lines.append("")
    lines.append("[Install]")
    lines.append("WantedBy=multi-user.target" if spec.system else "WantedBy=default.target")
    return "\n".join(lines) + "\n"


@dataclass(frozen=True)
class SidecarUnitSpec:
    """Fixed managed Linux sidecar service with file-only enrollment input."""

    restart_sec: int = _DEFAULT_RESTART_SEC
    watchdog_sec: int = _DEFAULT_WATCHDOG_SEC
    binary_path: str = "/opt/happyranch/bin/happyranch-tsnet-sidecar"
    home_transport: bool = False


def render_sidecar_unit(spec: SidecarUnitSpec) -> str:
    """Render a systemd-owned sidecar ordered after its loopback connector."""
    return "\n".join([
        "[Unit]",
        "Description=HappyRanch embedded tsnet sidecar",
        "After=network-online.target happyranch-connector.service",
        "Wants=network-online.target",
        *_START_LIMIT,
        "BindsTo=happyranch-connector.service",
        f"PartOf={_MANAGED_TARGET}",
        "",
        "[Service]",
        f"ExecStart={_quote_systemd_arg(spec.binary_path)} --config /etc/happyranch/sidecar.json "
        "--credential-file ${CREDENTIALS_DIRECTORY}/enrollment-key",
        # Credential-name lookup tolerates absence on restart (systemd >= 247).
        # The installer links this reserved name to /etc/happyranch/enrollment.key.
        "LoadCredential=enrollment-key:happyranch-tsnet-enrollment",
        "User=happyranch",
        "Group=happyranch",
        *([
            "Environment=SSL_CERT_FILE=/etc/happyranch/control-ca.pem",
            "Environment=SSL_CERT_DIR=/etc/happyranch/empty-ca-directory",
        ] if spec.home_transport else []),
        "NotifyAccess=main",
        f"RestartSec={spec.restart_sec}",
        f"WatchdogSec={spec.watchdog_sec}",
        "TimeoutStartSec=120",
        "TimeoutStopSec=35",
        *_hardening(system=True, sidecar=True),
        "StateDirectory=happyranch-tsnet-sidecar",
        "StateDirectoryMode=0700",
        "RuntimeDirectory=happyranch-tsnet-sidecar",
        "RuntimeDirectoryPreserve=yes",
        "LogsDirectory=happyranch-tsnet-sidecar",
        "",
        "[Install]",
        f"WantedBy={_MANAGED_TARGET}",
        "",
    ])


def render_managed_target() -> str:
    """Group both independently supervised services; does not install them."""
    return "\n".join([
        "[Unit]",
        "Description=HappyRanch managed remote access",
        "Wants=happyranch-release-verifier.socket happyranch-connector.service happyranch-tsnet-sidecar.service",
        "",
        "[Install]",
        "WantedBy=multi-user.target",
        "",
    ])


def render_verifier_socket() -> str:
    """Root-owned listener; group access does not grant path replacement."""
    return '\n'.join([
        '[Unit]', 'Description=HappyRanch release verifier socket',
        f'PartOf={_MANAGED_TARGET}', '', '[Socket]',
        'ListenStream=/run/happyranch-release-verifier/control.sock',
        'SocketUser=root', 'SocketGroup=happyranch', 'SocketMode=0660',
        'DirectoryMode=0755', 'RemoveOnStop=yes', '', '[Install]',
        f'WantedBy={_MANAGED_TARGET}', '',
    ])


def render_verifier_unit(python: tuple[str, ...]) -> str:
    """Privileged evidence reader, never the network-facing connector."""
    return '\n'.join([
        '[Unit]', 'Description=HappyRanch release socket ownership verifier',
        f'PartOf={_MANAGED_TARGET}', 'Requires=happyranch-release-verifier.socket',
        'After=happyranch-release-verifier.socket', '', '[Service]',
        'Type=simple', 'User=root', 'Group=happyranch',
        'ExecStart=' + ' '.join(_quote_systemd_arg(arg) for arg in
                              (*python, '-m', 'runtime.remote_access.release_verifier')),
        'Restart=on-failure', 'RestartSec=1', 'NoNewPrivileges=yes',
        *(directive for directive, _ in VERIFIER_FILESYSTEM_HARDENING),
        'RestrictSUIDSGID=yes',
        'RestrictRealtime=yes', 'LockPersonality=yes',
        'MemoryDenyWriteExecute=yes', 'SystemCallArchitectures=native',
        'RestrictAddressFamilies=AF_UNIX', 'UMask=0077',
        'CapabilityBoundingSet=CAP_DAC_READ_SEARCH CAP_SYS_PTRACE CAP_SETUID CAP_SETGID',
        'AmbientCapabilities=', 'TasksMax=16', 'MemoryMax=96M',
        'LimitNOFILE=128', 'TimeoutStopSec=5', '',
    ])


def render_headscale_unit(*, home_transport: bool = False) -> str:
    """Separate system-only unit; the unselected home variant adds B5 ordering.

    v0.25.1 serve has no sd_notify. ExecStartPost supplies a bounded local
    startup probe; no watchdog or notify claim is made for this binary.
    """
    lines = [
        "[Unit]", "Description=HappyRanch home Headscale", "After=network.target",
        *(["Before=happyranch-tsnet-sidecar.service"] if home_transport else []),
        *_START_LIMIT, "", "[Service]", "User=happyranch-headscale", "Group=happyranch-headscale",
        "ExecStart=/opt/happyranch-desktop/libexec/headscale/headscale --config /etc/happyranch/headscale/config.yaml serve",
        "ExecStartPost=/opt/happyranch-desktop/python/bin/python3 -I -m runtime.remote_access.headscale_service wait-ready",
        *["Type=exec" if line == "Type=notify" else line for line in _hardening(system=True)],
        "RestartSec=1", "TimeoutStartSec=40", "TimeoutStopSec=30", "KillMode=control-group",
        "RuntimeDirectory=happyranch-headscale", "RuntimeDirectoryMode=0700",
        "RuntimeDirectoryPreserve=yes", "StateDirectory=happyranch-headscale", "StateDirectoryMode=0700",
        "ReadWritePaths=/var/lib/happyranch-headscale /run/happyranch-headscale",
        "StandardOutput=null", "StandardError=null", "",
    ]
    # Default remains inert; no production caller selects the home variant.
    if home_transport:
        lines.extend(["[Install]", f"WantedBy={_MANAGED_TARGET}", ""])
    return "\n".join(lines)
