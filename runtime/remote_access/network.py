"""Customer-owned-network address discovery and validation (THR-097 Unit 3A).

The Supported-DIY adapter binds ONLY to an address on the customer's OWN
network — never a wildcard, never a public/wildcard interface. Two sources:

- **tailscale mode (default)** — ride-installed system Tailscale (THR-034
  pattern: the customer runs their own Tailscale/headscale and is logged
  in). The resolver invokes the ``tailscale`` CLI (``tailscale ip -4``)
  and returns the node's tailnet IPv4 (the ``100.x`` address). No embedded
  tsnet, no new dependency, no Network Extension.
- **explicit mode** — an operator-configured concrete customer-network
  address (used for the acceptance and for LAN-style customer networks).
  The value is validated strictly: wildcard, loopback, multicast,
  broadcast, and obviously-invalid addresses are refused (fail closed).

Resolution failure (CLI missing, not logged in, no IPv4, malformed output,
ambiguous results) fails closed — no listener. The resolved address is
never logged beyond its role as a bind target; it is not a secret, but it
is also never echoed into diagnostics as if it were the network boundary.
"""
from __future__ import annotations

import ipaddress
import shutil
import subprocess
from dataclasses import dataclass
from typing import Callable, Protocol


class NetworkAddressError(Exception):
    """Customer-network address could not be resolved/validated (fail
    closed: no listener)."""


@dataclass(frozen=True)
class NetworkConfig:
    """Customer-owned-network configuration (secret-free).

    ``mode`` is ``tailscale`` (ride-installed CLI; default) or ``explicit``
    (concrete ``address``). ``tailscale_cli`` overrides the CLI path for the
    tailscale mode (default: ``tailscale`` on PATH).
    """

    mode: str = "tailscale"
    tailscale_cli: str = "tailscale"
    address: str | None = None

    def validate(self) -> None:
        if self.mode not in {"tailscale", "explicit"}:
            raise NetworkAddressError("network mode must be 'tailscale' or 'explicit'")
        if self.mode == "explicit":
            if not self.address or not self.address.strip():
                raise NetworkAddressError("explicit network mode requires an address")
            validate_customer_network_address(self.address)
        elif self.tailscale_cli and not self.tailscale_cli.strip():
            raise NetworkAddressError("tailscale_cli must be non-empty")


class CustomerNetworkResolver(Protocol):
    def resolve(self) -> str: ...


class ExplicitAddressResolver:
    """A fixed, strictly-validated customer-network address."""

    def __init__(self, address: str) -> None:
        validate_customer_network_address(address)
        self._address = address

    def resolve(self) -> str:
        return self._address


class TailscaleCliResolver:
    """Ride-installed Tailscale: resolve the local node's tailnet IPv4 via
    the system ``tailscale ip -4`` command (the customer's OWN Tailscale/
    headscale — no embedded tsnet, no new dependency).

    Fail-closed: CLI missing, non-zero exit, empty output, multiple
    addresses, or an address that fails validation => ``NetworkAddressError``
    (never a listener on a guessed address).
    """

    def __init__(self, cli: str = "tailscale", *, runner: Callable[[list[str]], str] | None = None) -> None:
        self._cli = cli
        # ``runner`` is a test seam; production (None) uses subprocess. When a
        # runner is injected the CLI-existence probe is bypassed (the seam
        # owns command execution).
        self._runner = runner or self._default_run

    @staticmethod
    def _default_run(argv: list[str]) -> str:
        return subprocess.run(
            argv,
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        ).stdout.strip()

    def resolve(self) -> str:
        if not self._cli:
            raise NetworkAddressError("tailscale CLI not configured")
        if self._runner is None and shutil.which(self._cli) is None:
            raise NetworkAddressError(
                "tailscale CLI not found — the customer-owned network "
                "requires the customer's own Tailscale/headscale client "
                "installed and logged in"
            )
        try:
            output = self._runner([self._cli, "ip", "-4"])
        except (OSError, subprocess.SubprocessError) as exc:
            raise NetworkAddressError("tailscale ip failed") from exc
        addresses = [line.strip() for line in output.splitlines() if line.strip()]
        if len(addresses) != 1:
            raise NetworkAddressError(
                "tailscale did not resolve exactly one IPv4 address (is the "
                "customer network logged in?)"
            )
        address = addresses[0]
        validate_customer_network_address(address)
        return address


def resolve_customer_network_address(config: NetworkConfig) -> str:
    """Resolve the bind address for a customer-owned network config. Any
    failure raises ``NetworkAddressError`` (fail closed, no listener)."""
    config.validate()
    if config.mode == "explicit":
        return ExplicitAddressResolver(config.address).resolve()
    return TailscaleCliResolver(config.tailscale_cli).resolve()


def validate_customer_network_address(address: str) -> None:
    """Strict validation of a customer-network bind address. Refuses
    wildcards, loopback, multicast, broadcast, and non-IPv4 strings —
    a connector must listen only on a concrete address on the customer's
    own network."""
    try:
        parsed = ipaddress.ip_address(address)
    except ValueError as exc:
        raise NetworkAddressError("invalid network address") from exc
    if parsed.version != 4:
        raise NetworkAddressError("customer network bind address must be IPv4")
    if parsed.is_unspecified:  # 0.0.0.0
        raise NetworkAddressError("wildcard bind address forbidden")
    if parsed.is_loopback:  # 127.0.0.0/8
        raise NetworkAddressError("loopback bind address forbidden for the customer network")
    if parsed.is_multicast:  # 224.0.0.0/4
        raise NetworkAddressError("multicast bind address forbidden")
    if parsed.is_link_local:  # 169.254.0.0/16 (APIPA)
        raise NetworkAddressError("link-local bind address forbidden")
    if parsed.is_reserved or str(parsed) == "255.255.255.255":
        raise NetworkAddressError("reserved/broadcast bind address forbidden")
