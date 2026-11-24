"""Shared managed configuration validators, independent of installation effects."""
from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from urllib.parse import urlsplit

from runtime.remote_access.installer_io import InstallerError

UNITS = (
    "happyranch-connector.service", "happyranch-tsnet-sidecar.service",
    "happyranch-managed.target", "happyranch-release-verifier.socket",
    "happyranch-release-verifier.service",
)


class InstallerValidation:
    """Keep shipping configuration and historical provenance checks identical."""

    def _validate_configs(self, files: dict[str, bytes], *, staged: bool = False, partial: bool = False,
                          require_current: bool = True) -> None:
        # Reuse shipping connector/policy validators; never start a supervisor.
        # Only inert inventory/replacement recovery omits currentness. Schema,
        # artifact and cross-config checks still apply; admission defaults closed.
        from runtime.remote_access.managed_provider import ManagedProviderConfig
        from runtime.remote_access.policy import PolicyEnvelope, RoutePolicyConsumer
        from runtime.remote_access.supervisor import ConnectorConfig

        try:
            cfg = None
            if not partial or "connector.json" in files:
                raw = self._json(files["connector.json"])
                managed = raw.pop("managed")
                if not isinstance(managed, dict) or raw.get("lab") is not None or raw.get("diy") is not None:
                    raise ValueError
                cfg = ConnectorConfig(**raw, managed=ManagedProviderConfig(**managed))
                cfg.validate()
                if (any(not isinstance(value, str) or not value.strip() for value in (cfg.tenant_id, cfg.home_id, cfg.connector_id))
                        or cfg.system is not True or cfg.service_user != "happyranch" or cfg.service_group != "happyranch"
                        or cfg.daemon_port is not None
                        or not 1 <= cfg.managed.bind_port <= 65535
                        or cfg.daemon_token_path != "/etc/happyranch/daemon.token"
                        or cfg.policy_path != "/etc/happyranch/policy.json"
                        or cfg.state_path != "/var/lib/happyranch-connector/trust-state.json"
                        or cfg.credentials_directory is not None or cfg.exec_start is not None
                        or cfg.managed_dir_root is not None or cfg.unit_name != UNITS[0]
                        or any(value != "happyranch-connector" for value in (cfg.state_dir, cfg.run_dir, cfg.logs_dir))
                        or any(type(value) not in (int, float) or not math.isfinite(value) or value <= 0
                               for value in (cfg.watchdog_sec, cfg.poll_seconds, cfg.restart_sec))):
                    raise ValueError
                if (cfg.managed.profile == "linux-managed-identity-v1"
                        and (cfg.excluded_daemon_ports != [8765] or not cfg.managed.control_plane_id)):
                    raise ValueError
            if not partial or "policy.json" in files:
                policy = RoutePolicyConsumer.from_envelope(PolicyEnvelope(**self._json(files["policy.json"])))
                if require_current:
                    policy.require_current(datetime.now(timezone.utc))
            if partial and "sidecar.json" not in files:
                return
            sidecar = self._json(files["sidecar.json"])
            profile = cfg.managed.profile if cfg is not None else sidecar.get("Profile")
            if profile == "linux-managed-identity-v1":
                if not staged and "home_binding" in sidecar:
                    from runtime.remote_access.home_admission import HomeProfileAdmission
                    admitted = HomeProfileAdmission(self).require()
                    if any(admitted.files.get(name) != data for name, data in files.items()):
                        raise ValueError
                    return
                # An inert staged bundle, not a runnable home transport. In
                # particular, never publish a URL without its protected CA.
                if (sidecar.keys() != {"Profile", "StateDir", "RoleIdentity", "DERPPolicy",
                                      "ListenAddr", "ExcludedDaemonPorts"}
                        or sidecar["Profile"] != profile
                        or sidecar["StateDir"] != "/var/lib/happyranch-tsnet-sidecar"
                        or not isinstance(sidecar["RoleIdentity"], str)
                        or not sidecar["RoleIdentity"].startswith("home-sidecar-")
                        or len(sidecar["RoleIdentity"]) <= len("home-sidecar-")
                        or (cfg is not None and sidecar["RoleIdentity"] != f"home-sidecar-{cfg.home_id}")
                        or sidecar["DERPPolicy"] != "private-only" or sidecar["ListenAddr"] != ":443"
                        or sidecar["ExcludedDaemonPorts"] != [8765]):
                    raise ValueError
                return
            required = {"StateDir", "ControlURL", "RoleIdentity", "ExpectedPeer", "DERPPolicy", "ListenAddr", "ConnectorAddr"}
            if (not required <= sidecar.keys() <= required | {"ExcludedDaemonPorts"}
                    or any(not isinstance(sidecar[key], str) for key in required)):
                raise ValueError
            sidecar_excluded = sidecar.get("ExcludedDaemonPorts", [])
            if (not isinstance(sidecar_excluded, list)
                    or any(type(port) is not int or not 1 <= port <= 65535 for port in sidecar_excluded)):
                raise ValueError
            excluded = sorted({8765, *(cfg.excluded_daemon_ports if cfg is not None else []), *sidecar_excluded})
            if cfg is not None and (cfg.managed.bind_port in excluded
                    or sidecar["ConnectorAddr"] != f"127.0.0.1:{cfg.managed.bind_port}"):
                raise ValueError
            if (cfg is not None and staged and self.layout == "deb"
                    and (cfg.excluded_daemon_ports != excluded or sidecar_excluded != excluded)):
                raise ValueError
            control = urlsplit(sidecar["ControlURL"])
            listen = re.fullmatch(r"(?:\[[^\]]+\]|[^:]*):([0-9]+)", sidecar["ListenAddr"])
            ingress = re.fullmatch(r"127\.0\.0\.1:([0-9]+)", sidecar["ConnectorAddr"])
            if (control.scheme != "https" or not control.hostname or control.username is not None
                    or control.password is not None or control.hostname.lower() == "controlplane.tailscale.com"
                    or not sidecar["RoleIdentity"].startswith("home-sidecar-")
                    or len(sidecar["RoleIdentity"]) <= len("home-sidecar-")
                    or not sidecar["ExpectedPeer"].strip() or sidecar["DERPPolicy"] != "private-only"
                    or sidecar["StateDir"] != "/var/lib/happyranch-tsnet-sidecar"
                    or listen is None or not 1 <= int(listen[1]) <= 65535
                    or ingress is None or not 1 <= int(ingress[1]) <= 65535 or int(ingress[1]) in excluded):
                raise ValueError
        except Exception:
            # Validators can include input values. Keep all diagnostics secret-free.
            raise InstallerError("invalid connector, sidecar or current policy provisioning") from None


    def _validate_provision_record(self, data: bytes) -> None:
        record = self._json(data)
        if "version" not in record:
            if record.keys() != {"layout", "token_source", "token_sha256", "configs"} or record["layout"] != self.layout:
                raise InstallerError("invalid legacy provisioning record")
            return
        digests = record.get("digests")
        keys = {"connector.json", "sidecar.json", "policy.json", "release-daemon.json", "daemon.token"}
        if (record.keys() != {"version", "layout", "digests", "phase"}
                or type(record["version"]) is not int or record["version"] != 1
                or record["layout"] not in ("source", "deb") or record["layout"] != self.layout
                or not isinstance(record["phase"], str) or record["phase"] != "provisioned"
                or not isinstance(digests, dict) or digests.keys() != keys
                or any(not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None
                       for value in digests.values())):
            raise InstallerError("invalid versioned installation record")
